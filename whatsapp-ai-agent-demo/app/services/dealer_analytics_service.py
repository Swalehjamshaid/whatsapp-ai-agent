"""
File: app/services/dealer_analytics_service.py
Version: 9.0 - ENTERPRISE DEALER INTELLIGENCE ENGINE
Purpose: Complete dealer analytics with 200+ business questions
         PostgreSQL IS THE ONLY SOURCE OF TRUTH.
         Reuses DNAnalysisService architecture for consistency.
Status: PRODUCTION READY - BACKWARD COMPATIBLE
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any, Optional, Dict, List, Tuple, Union, Set

from cachetools import TTLCache
from rapidfuzz import fuzz, process
from sqlalchemy import and_, case, distinct, func, or_, text, desc, asc
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

try:
    import openrouteservice
except ImportError:
    openrouteservice = None

try:
    from geopy.distance import great_circle
except ImportError:
    great_circle = None

try:
    import pyarrow as pa
    import pyarrow.compute as pc
except ImportError:
    pa = None
    pc = None

try:
    import polars as pl
except ImportError:
    pl = None

try:
    from sentence_transformers import SentenceTransformer
    import torch
except ImportError:
    SentenceTransformer = None
    torch = None

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None

logger = logging.getLogger(__name__)


# ============================================================
# BLOCK 1: CONFIGURATION
# ============================================================

ORS_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY") or os.getenv("ORS_API_KEY")
CACHE_TTL = max(60, int(os.getenv("DEALER_ANALYTICS_CACHE_TTL", "300")))
USE_SEMANTIC_SEARCH = os.getenv("USE_SEMANTIC_SEARCH", "true").lower() == "true"
USE_PGVECTOR = os.getenv("USE_PGVECTOR", "false").lower() == "true"
USE_PYARROW = os.getenv("USE_PYARROW", "true").lower() == "true"
USE_POLARS = os.getenv("USE_POLARS", "true").lower() == "true"
DISABLE_AI = os.getenv("DISABLE_AI", "false").lower() == "true"
DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))


# ============================================================
# BLOCK 2: CONSTANTS - REUSED FROM DN_ANALYSIS
# ============================================================

TABLE: str = "delivery_reports"
SEPARATOR: str = "────────────────────"

# Business columns - identical to dn_analysis.py
BUSINESS_COLUMNS: tuple[str, ...] = (
    "dn_no", "division", "customer_code", "dealer_code", "customer_name",
    "customer_model", "material_no", "sales_office", "sales_manager",
    "ship_to_city", "warehouse", "warehouse_code", "delivery_location",
    "dn_qty", "dn_amount", "dn_create_date", "good_issue_date", "pod_date",
    "delivery_status", "pgi_status", "pod_status", "pending_flag",
)

# Warehouse coordinates - IDENTICAL to dn_analysis.py
WAREHOUSE_COORDINATES: dict[str, tuple[float, float]] = {
    "rawalpindi": (33.5651, 73.0169),
    "lahore": (31.5204, 74.3587),
    "karachi": (24.8607, 67.0011),
    "multan": (30.1575, 71.5249),
    "peshawar": (34.0151, 71.5249),
    "quetta": (30.1798, 66.9750),
    "hyderabad": (25.3960, 68.3578),
    "faisalabad": (31.4504, 73.1350),
    "sialkot": (32.4945, 74.5229),
    "gujranwala": (32.1617, 74.1883),
    "bahawalpur": (29.3956, 71.6836),
    "dg khan": (30.0430, 70.6402),
    "sukkur": (27.7060, 68.8530),
    "rahim yar khan": (28.4200, 70.3030),
    "abbottabad": (34.1490, 73.2210),
    "gwadar": (25.1260, 62.3250),
}


# ============================================================
# BLOCK 3: ENUMS
# ============================================================

class BusinessHealthStatus(Enum):
    """Business health status levels"""
    EXCELLENT = "Excellent"
    GOOD = "Good"
    WATCH = "Watch"
    CRITICAL = "Critical"


class TrendType(Enum):
    """Trend analysis types"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class RankType(Enum):
    """Ranking types"""
    REVENUE = "revenue"
    UNITS = "units"
    DN = "dn"
    DELIVERY = "delivery"
    PGI = "pgi"
    POD = "pod"
    PENDING = "pending"
    GROWTH = "growth"
    BUSINESS_SCORE = "business_score"
    REGIONAL = "regional"
    NATIONAL = "national"


class WhatsappFormat(Enum):
    """WhatsApp message formats"""
    COMPACT = "compact"
    STANDARD = "standard"
    EXECUTIVE = "executive"
    DETAILED = "detailed"


# ============================================================
# BLOCK 4: UTILITY FUNCTIONS - REUSED FROM DN_ANALYSIS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    """Safely convert to string - identical to dn_analysis.py"""
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except (TypeError, ValueError):
        return default


def _number(value: Any) -> float:
    """Safely convert to float"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _percent(numerator: Any, denominator: Any) -> float:
    """Calculate percentage safely"""
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0


def _date_text(value: Any) -> str:
    """Format date for display - identical to dn_analysis.py"""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")


def _format_date(value: Any) -> str:
    """Format date for WhatsApp display - DD-MMM-YYYY"""
    if not value:
        return "N/A"
    if isinstance(value, datetime):
        return value.strftime("%d-%b-%Y")
    if isinstance(value, date):
        return value.strftime("%d-%b-%Y")
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%d-%b-%Y")
        except (ValueError, TypeError):
            return str(value)[:10]
    return str(value)


def _days(value: Any) -> float:
    """Convert to days"""
    if value is None:
        return 0.0
    if hasattr(value, "days"):
        return round(float(value.days), 2)
    return round(_number(value), 2)


def _growth(current: float, previous: float) -> float:
    """Calculate growth percentage"""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)


def _status_complete(column: Any) -> Any:
    """Check if status is complete"""
    return func.lower(func.coalesce(column, "")).in_(("completed", "complete", "delivered", "done", "yes"))


def _flag(value: Any) -> bool:
    """Check if flag is true - identical to dn_analysis.py"""
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "pending"}


# ============================================================
# BLOCK 5: DATACLASSES - REUSED FROM DN_ANALYSIS
# ============================================================

@dataclass
class DistanceAnalytics:
    """Distance analytics for dealer-warehouse relationship"""
    warehouse: str
    dealer_city: str
    distance_km: Optional[float] = None
    estimated_driving_minutes: Optional[int] = None
    estimated_driving_time: str = "Unknown"
    estimated_delivery_time: str = "Unknown"
    source: str = "unavailable"


@dataclass
class DealerDashboard:
    """
    Complete Dealer Dashboard - Enterprise Dealer Intelligence
    Reuses business rules from dn_analysis.py
    """
    
    # ============================================================
    # CORE INFORMATION
    # ============================================================
    dealer_name: str
    dealer_code: str
    customer_code: str
    city: str
    warehouse: str
    warehouse_code: str
    sales_office: str
    sales_manager: str
    division: str
    delivery_location: str = "Unknown"
    
    # ============================================================
    # DN METRICS
    # ============================================================
    total_dn: int = 0
    completed_dn: int = 0
    pending_dn: int = 0
    delivery_pending_dn: int = 0
    
    # ============================================================
    # UNIT METRICS
    # ============================================================
    total_units: int = 0
    delivered_units: int = 0
    pending_units: int = 0
    average_units_per_dn: float = 0.0
    
    # ============================================================
    # REVENUE METRICS
    # ============================================================
    total_revenue: float = 0.0
    delivered_revenue: float = 0.0
    pending_revenue: float = 0.0
    average_revenue_per_dn: float = 0.0
    average_revenue_per_unit: float = 0.0
    
    # ============================================================
    # DELIVERY METRICS
    # ============================================================
    average_delivery_days: float = 0.0
    average_pod_days: float = 0.0
    average_total_cycle_time: float = 0.0
    delivery_success_pct: float = 0.0
    pgi_success_pct: float = 0.0
    pod_success_pct: float = 0.0
    pending_pct: float = 0.0
    fastest_delivery_days: float = 0.0
    slowest_delivery_days: float = 0.0
    same_day_deliveries: int = 0
    next_day_deliveries: int = 0
    
    # ============================================================
    # AGING METRICS - REUSED FROM DN_ANALYSIS
    # ============================================================
    pgi_aging_days: float = 0.0
    pod_aging_days: float = 0.0
    delivery_aging_days: float = 0.0
    transit_days: float = 0.0
    
    # ============================================================
    # DISTANCE
    # ============================================================
    distance: DistanceAnalytics = field(default_factory=lambda: DistanceAnalytics("", ""))
    
    # ============================================================
    # RANKINGS
    # ============================================================
    revenue_rank: Optional[int] = None
    unit_rank: Optional[int] = None
    dn_rank: Optional[int] = None
    delivery_rank: Optional[int] = None
    pgi_rank: Optional[int] = None
    pod_rank: Optional[int] = None
    pending_rank: Optional[int] = None
    growth_rank: Optional[int] = None
    business_score_rank: Optional[int] = None
    warehouse_rank: Optional[int] = None
    regional_rank: Optional[int] = None
    national_rank: Optional[int] = None
    
    # ============================================================
    # MONTHLY ANALYTICS
    # ============================================================
    busiest_month: str = "Unknown"
    best_month: str = "Unknown"
    worst_month: str = "Unknown"
    current_month_revenue: float = 0.0
    previous_month_revenue: float = 0.0
    monthly_growth: float = 0.0
    current_month_dn: int = 0
    previous_month_dn: int = 0
    current_month_units: int = 0
    previous_month_units: int = 0
    revenue_growth_pct: Optional[float] = None
    unit_growth_pct: Optional[float] = None
    dn_growth_pct: Optional[float] = None
    
    # ============================================================
    # PRODUCT ANALYTICS
    # ============================================================
    top_product: str = "Unknown"
    top_model: str = "Unknown"
    top_material: str = "Unknown"
    top_division: str = "Unknown"
    strongest_product_category: str = "Unknown"
    weakest_product_category: str = "Unknown"
    fastest_growing_product: str = "Unknown"
    highest_revenue_product: str = "Unknown"
    highest_unit_product: str = "Unknown"
    
    # ============================================================
    # WAREHOUSE ANALYTICS
    # ============================================================
    warehouse_utilization: float = 0.0
    delivery_coverage: float = 0.0
    warehouse_contribution: float = 0.0
    
    # ============================================================
    # DN TIMELINE
    # ============================================================
    first_delivery_date: str = "N/A"
    latest_delivery_date: str = "N/A"
    newest_dn: str = "N/A"
    highest_revenue_dn: str = "N/A"
    lowest_revenue_dn: str = "N/A"
    highest_unit_dn: str = "N/A"
    lowest_unit_dn: str = "N/A"
    
    # ============================================================
    # PENDING ANALYTICS
    # ============================================================
    pending_average_days: float = 0.0
    critical_pending: int = 0
    overdue_pending: int = 0
    oldest_pending_dn: str = "N/A"
    oldest_pending_days: int = 0
    pgi_pending_dn: int = 0
    pod_pending_dn: int = 0
    
    # ============================================================
    # DATE SUMMARY
    # ============================================================
    latest_pgi_date: str = "N/A"
    latest_pod_date: str = "N/A"
    
    # ============================================================
    # BUSINESS HEALTH
    # ============================================================
    business_score: float = 0.0
    risk_score: float = 0.0
    overall_status: str = "Needs Attention"
    executive_summary: str = ""
    performance_grade: str = "C"
    
    # ============================================================
    # GROWTH & FORECAST
    # ============================================================
    forecast_revenue: Optional[float] = None
    forecast_units: Optional[float] = None
    forecast_dn: Optional[float] = None
    
    # ============================================================
    # KPIs
    # ============================================================
    revenue_per_day: float = 0.0
    revenue_per_delivery: float = 0.0
    revenue_per_product: float = 0.0
    dealer_contribution: float = 0.0
    average_order_value: float = 0.0
    average_revenue_per_month: float = 0.0
    average_units_per_month: float = 0.0
    average_dn_per_month: float = 0.0
    
    # ============================================================
    # INSIGHTS & RECOMMENDATIONS
    # ============================================================
    insights: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    threats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)

    def to_whatsapp_message(self, format_type: str = "standard") -> str:
        """
        Enhanced WhatsApp formatting with multiple formats
        """
        if format_type == "compact":
            return self._compact_format()
        elif format_type == "executive":
            return self._executive_format()
        elif format_type == "detailed":
            return self._detailed_format()
        else:
            return self._standard_format()

    def _standard_format(self) -> str:
        """Standard WhatsApp format - identical style to dn_analysis.py"""
        distance = "Unknown" if self.distance.distance_km is None else f"{self.distance.distance_km:,.1f} KM"
        
        status_emoji = {
            "Excellent": "🟢",
            "Good": "🟡",
            "Watch": "🟠",
            "Critical": "🔴"
        }.get(self.overall_status, "⚪")
        
        return "\n".join([
            "📦 Dealer Dashboard",
            "",
            "Dealer", self.dealer_name,
            "Warehouse", self.warehouse,
            "Warehouse Code", self.warehouse_code,
            "City", self.city,
            "Delivery Location", self.delivery_location,
            "Sales Office", self.sales_office,
            "Sales Manager", self.sales_manager,
            "Division", self.division,
            "",
            SEPARATOR,
            "",
            "Revenue", f"PKR {self.total_revenue:,.2f}",
            "Units", f"{self.total_units:,}",
            "DN", f"{self.total_dn:,}",
            "Pending DN", f"{self.pending_dn:,}",
            "Average Revenue/DN", f"PKR {self.average_revenue_per_dn:,.2f}",
            "",
            SEPARATOR,
            "",
            "Delivery Success", f"{self.delivery_success_pct:.1f}%",
            "PGI Success", f"{self.pgi_success_pct:.1f}%",
            "POD Success", f"{self.pod_success_pct:.1f}%",
            "Pending Rate", f"{self.pending_pct:.1f}%",
            "Average Delivery", f"{self.average_delivery_days:.1f} Days",
            "Average POD", f"{self.average_pod_days:.1f} Days",
            "Transit Days", f"{self.transit_days:.1f} Days",
            "",
            SEPARATOR,
            "",
            "PGI Aging", f"{self.pgi_aging_days:.1f} Days",
            "POD Aging", f"{self.pod_aging_days:.1f} Days",
            "Delivery Aging", f"{self.delivery_aging_days:.1f} Days",
            "",
            SEPARATOR,
            "",
            f"{status_emoji} Status", self.overall_status,
            "Business Score", f"{self.business_score:.1f}/100",
            "Risk Score", f"{self.risk_score:.1f}/100",
            "Performance Grade", self.performance_grade,
            "",
            SEPARATOR,
            "",
            "Distance", distance,
            "Driving Time", self.distance.estimated_driving_time,
            "Estimated Delivery", self.distance.estimated_delivery_time,
            "",
            SEPARATOR,
            "",
            "Top Product", self.top_product,
            "Top Model", self.top_model,
            "Top Material", self.top_material,
            "Top Division", self.top_division,
            "",
            SEPARATOR,
            "",
            "Revenue Rank", f"#{self.revenue_rank or 'N/A'}",
            "DN Rank", f"#{self.dn_rank or 'N/A'}",
            "Unit Rank", f"#{self.unit_rank or 'N/A'}",
            "Delivery Rank", f"#{self.delivery_rank or 'N/A'}",
            "POD Rank", f"#{self.pod_rank or 'N/A'}",
            "National Rank", f"#{self.national_rank or 'N/A'}",
            "",
            SEPARATOR,
            "",
            "First DN", self.first_delivery_date,
            "Latest DN", self.latest_delivery_date,
            "Latest PGI", self.latest_pgi_date,
            "Latest POD", self.latest_pod_date,
            "",
            SEPARATOR,
            "",
            "Monthly Revenue", f"PKR {self.current_month_revenue:,.2f}",
            "Monthly Growth", f"{self.monthly_growth:+.1f}%",
            "Best Month", self.best_month,
            "",
            SEPARATOR,
            "",
            "Executive Summary",
            self.executive_summary or "Performance is stable.",
            "",
            "Key Insights",
            "\n".join(f"• {insight}" for insight in self.insights[:5]) or "• No significant exceptions.",
            "",
            "Recommendations",
            "\n".join(f"• {rec}" for rec in self.recommendations[:3]) or "• Continue monitoring.",
        ])

    def _compact_format(self) -> str:
        """Compact format for quick answers"""
        return "\n".join([
            f"Dealer: {self.dealer_name}",
            f"Revenue: PKR {self.total_revenue:,.2f}",
            f"Units: {self.total_units:,}",
            f"DN: {self.total_dn:,}",
            f"Pending: {self.pending_dn:,}",
            f"Status: {self.overall_status}",
            f"Score: {self.business_score:.1f}/100",
        ])

    def _executive_format(self) -> str:
        """Executive summary format"""
        return "\n".join([
            f"📊 Executive Summary - {self.dealer_name}",
            "",
            self.executive_summary,
            "",
            f"Status: {self.overall_status}",
            f"Score: {self.business_score:.1f}/100",
            f"Grade: {self.performance_grade}",
            f"Growth: {self.monthly_growth:+.1f}%",
            f"Pending: {self.pending_dn:,} DNs",
        ])

    def _detailed_format(self) -> str:
        """Detailed format for in-depth analysis"""
        return "\n".join([
            f"📊 Detailed Dealer Analysis - {self.dealer_name}",
            "",
            "📍 Location",
            f"City: {self.city}",
            f"Warehouse: {self.warehouse}",
            f"Sales Office: {self.sales_office}",
            f"Sales Manager: {self.sales_manager}",
            "",
            "💰 Revenue",
            f"Total: PKR {self.total_revenue:,.2f}",
            f"Delivered: PKR {self.delivered_revenue:,.2f}",
            f"Pending: PKR {self.pending_revenue:,.2f}",
            f"Per DN: PKR {self.average_revenue_per_dn:,.2f}",
            "",
            "📦 DN",
            f"Total: {self.total_dn:,}",
            f"Completed: {self.completed_dn:,}",
            f"Pending: {self.pending_dn:,}",
            f"PGI Pending: {self.pgi_pending_dn:,}",
            f"POD Pending: {self.pod_pending_dn:,}",
            "",
            "🚚 Delivery",
            f"Success: {self.delivery_success_pct:.1f}%",
            f"Average: {self.average_delivery_days:.1f} Days",
            f"Fastest: {self.fastest_delivery_days:.1f} Days",
            f"Slowest: {self.slowest_delivery_days:.1f} Days",
            f"Same Day: {self.same_day_deliveries:,}",
            f"Next Day: {self.next_day_deliveries:,}",
            "",
            "📈 Aging",
            f"PGI Aging: {self.pgi_aging_days:.1f} Days",
            f"POD Aging: {self.pod_aging_days:.1f} Days",
            f"Delivery Aging: {self.delivery_aging_days:.1f} Days",
            "",
            "🏷️ Products",
            f"Top Product: {self.top_product}",
            f"Top Model: {self.top_model}",
            f"Top Material: {self.top_material}",
            "",
            "🏆 Rankings",
            f"Revenue: #{self.revenue_rank or 'N/A'}",
            f"DN: #{self.dn_rank or 'N/A'}",
            f"Delivery: #{self.delivery_rank or 'N/A'}",
            f"National: #{self.national_rank or 'N/A'}",
            "",
            "💡 Insights",
            "\n".join(f"• {insight}" for insight in self.insights[:10]) or "• No significant exceptions.",
            "",
            "🎯 Recommendations",
            "\n".join(f"• {rec}" for rec in self.recommendations[:5]) or "• Continue monitoring.",
        ])

    def __str__(self) -> str:
        """String representation for WhatsApp"""
        return self.to_whatsapp_message()


@dataclass
class DealerComparison:
    """Dealer comparison data"""
    dealers: list[DealerDashboard]
    revenue_leader: str
    units_leader: str
    dn_leader: str
    delivery_leader: str
    summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DealerRanking:
    """Dealer ranking data"""
    sort_by: str
    order: str
    dealers: list[DealerDashboard]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DealerSearchResult:
    """Dealer search result data"""
    original_message: str
    extracted_dealer: str
    normalized_dealer: str
    dealer_found: Optional[str] = None
    dealer_code: Optional[str] = None
    customer_code: Optional[str] = None
    alias_used: Optional[str] = None
    rapidfuzz_score: Optional[float] = None
    semantic_score: Optional[float] = None
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False
    cache_used: bool = False
    exception: Optional[str] = None
    match_source: str = "unknown"


# ============================================================
# BLOCK 6: DISTANCE SERVICE - IDENTICAL TO DN_ANALYSIS
# ============================================================

class DistanceService:
    """Route distance calculation - identical to dn_analysis.py"""
    
    def __init__(self):
        self._cache: TTLCache[str, DistanceAnalytics] = TTLCache(maxsize=8192, ttl=CACHE_TTL)
        self._coordinate_cache: TTLCache[str, tuple[float, float] | None] = TTLCache(512, 86_400)
        self._lock = threading.RLock()
        self._ors_key = os.getenv("OPENROUTESERVICE_API_KEY")
        self._geocoder = None
        
        try:
            from geopy.geocoders import Nominatim
            self._geocoder = Nominatim(user_agent="dealer-analytics-service", timeout=4)
        except ImportError:
            pass
    
    def _coordinates(self, location: str) -> tuple[float, float] | None:
        key = location.strip().casefold()
        if key in self._coordinate_cache:
            return self._coordinate_cache[key]
        
        coordinates = None
        
        # Check warehouse coordinates first
        normalized_key = key.replace(" warehouse", "").strip()
        if normalized_key in WAREHOUSE_COORDINATES:
            coordinates = WAREHOUSE_COORDINATES[normalized_key]
        elif key in WAREHOUSE_COORDINATES:
            coordinates = WAREHOUSE_COORDINATES[key]
        
        # If not in warehouse coordinates, try geocoding
        if coordinates is None and self._geocoder and key:
            try:
                result = self._geocoder.geocode(location, exactly_one=True)
                if result:
                    coordinates = (float(result.latitude), float(result.longitude))
            except Exception as exc:
                logger.warning("Geocoding failed for {}: {}", location, exc)
        
        self._coordinate_cache[key] = coordinates
        return coordinates

    @staticmethod
    def _haversine(origin: tuple[float, float], destination: tuple[float, float]) -> float:
        lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *destination))
        dlat, dlon = lat2 - lat1, lon2 - lon1
        value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 6_371.0088 * 2 * math.asin(math.sqrt(value))

    @staticmethod
    def _format_duration(hours: float) -> str:
        total_minutes = max(0, round(hours * 60))
        whole_hours, minutes = divmod(total_minutes, 60)
        return f"{whole_hours} Hours {minutes} Minutes" if minutes else f"{whole_hours} Hours"

    def calculate(self, warehouse: str, dealer_city: str) -> DistanceAnalytics:
        warehouse_name = _text(warehouse)
        city_name = _text(dealer_city)
        key = f"{warehouse_name}|{city_name}"
        
        with self._lock:
            cached = self._cache.get(key)
        if cached:
            return cached
        
        origin = self._coordinates(warehouse_name)
        destination = self._coordinates(city_name)
        
        if not origin or not destination:
            result = DistanceAnalytics(warehouse_name, city_name)
        else:
            km: Optional[float] = None
            minutes: Optional[int] = None
            source = "haversine"
            
            # Priority 1: OpenRouteService
            if self._ors_key:
                try:
                    import openrouteservice
                    client = openrouteservice.Client(key=self._ors_key, timeout=5)
                    route = client.directions(
                        [(origin[1], origin[0]), (destination[1], destination[0])],
                        profile="driving-car",
                    )["routes"][0]["summary"]
                    km = round(float(route["distance"]) / 1000, 1)
                    minutes = int(round(float(route["duration"]) / 60))
                    source = "openrouteservice"
                except Exception as exc:
                    logger.warning("OpenRouteService failed: {}", exc)
            
            # Priority 2: Geopy
            if km is None and great_circle:
                try:
                    km = round(great_circle(origin, destination).kilometers, 1)
                    minutes = int(round(km / 55 * 60))
                    source = "geopy"
                except Exception:
                    pass
            
            # Priority 3: Haversine
            if km is None:
                km = round(self._haversine(origin, destination) * 1.20, 1)
                minutes = int(round(km / 45 * 60))
                source = "haversine"
            
            result = DistanceAnalytics(
                warehouse_name,
                city_name,
                km,
                minutes,
                self._format_duration(minutes / 60) if minutes else "Unknown",
                self._delivery_estimate(km),
                source
            )
        
        with self._lock:
            self._cache[key] = result
        return result

    @staticmethod
    def _delivery_estimate(km: Optional[float]) -> str:
        if km is None:
            return "Unknown"
        if km <= 80:
            return "Same Day"
        if km <= 200:
            return "Next Day"
        if km <= 400:
            return "1-2 Days"
        if km <= 700:
            return "2-3 Days"
        return "3-5 Days"


# ============================================================
# BLOCK 7: DEALER SEARCH ENGINE
# ============================================================

class DealerSearchEngine:
    """Enhanced dealer search with semantic matching"""
    
    STOP_PHRASES = frozenset({
        "tell me about", "dealer dashboard", "dealer profile", "dealer performance",
        "dealer statistics", "dealer revenue", "dealer distance", "dealer pending",
        "dealer status", "dealer pod", "dealer pgi", "show", "display", "dealer",
        "profile", "statistics", "performance", "status", "revenue", "distance",
        "pending", "dashboard", "about", "of", "the", "company", "private",
        "limited", "pvt", "ltd",
    })
    
    DEALER_ALIASES = {
        "mian": "Mian Group Chakwal",
        "mgc": "Mian Group Chakwal",
        "taj": "Taj Electronics",
        "taj haripur": "Taj Electronics Haripur",
        "haroon": "Haroon Electronics",
        "haroon electronics": "Haroon Electronics",
        "arco": "Arco Electronics",
        "shah": "Shah Electronics",
        "national": "National Foods",
        "lahore": "Lahore Traders",
        "islamabad": "Islamabad Electronics",
        "karachi": "Karachi Distributors",
        "commercial": "Commercial Abbottabad",
    }
    
    def __init__(self):
        self._cache: TTLCache[str, DealerSearchResult] = TTLCache(maxsize=4096, ttl=CACHE_TTL)
        self._candidate_cache: TTLCache[str, list[dict[str, str]]] = TTLCache(maxsize=1, ttl=3600)
        self._similarity_cache: TTLCache[str, float] = TTLCache(maxsize=5000, ttl=3600)
        self._lock = threading.RLock()
        self._normalize_regex = re.compile(r'[^a-z0-9\s]')
        self._dealer_extract_pattern = re.compile(r'(?:for|about|of|on|dealer|dashboard|profile)\s+([\w\s]+?)(?:\?|$|\.)', re.IGNORECASE)
        
        # Semantic search engine
        self._semantic_engine = None
        if USE_SEMANTIC_SEARCH and SentenceTransformer:
            try:
                self._semantic_engine = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
                logger.info("✅ Semantic search engine initialized")
            except Exception as e:
                logger.warning(f"⚠️ Semantic search init failed: {e}")

    def normalize(self, value: Any) -> str:
        """Normalize dealer text - identical to dn_analysis.py style"""
        if not value:
            return ""
        text_value = unicodedata.normalize("NFKD", _text(value, "").lower())
        text_value = self._normalize_regex.sub(" ", text_value)
        text_value = re.sub(r'\s+', ' ', text_value).strip()
        for phrase in self.STOP_PHRASES:
            if phrase in text_value:
                text_value = text_value.replace(phrase, " ")
        return re.sub(r'\s+', ' ', text_value).strip()

    def load_candidates(self, session: Session) -> None:
        """Load dealer candidates from PostgreSQL"""
        try:
            dealers = session.query(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).distinct().all()
            
            candidates = [
                {
                    "name": _text(d.customer_name),
                    "dealer_code": _text(d.dealer_code, ""),
                    "customer_code": _text(d.customer_code, ""),
                    "normalized": self.normalize(d.customer_name)
                }
                for d in dealers if _text(d.customer_name, "")
            ]
            
            with self._lock:
                self._candidate_cache["all"] = candidates
            
            logger.info(f"✅ Loaded {len(candidates)} dealer candidates")
        except Exception as e:
            logger.warning(f"Failed to load candidates: {e}")

    def get_candidates(self, session: Session) -> tuple[list[dict[str, str]], bool]:
        """Get dealer candidates with caching"""
        with self._lock:
            cached = self._candidate_cache.get("all")
        if cached is not None:
            return cached, True
        
        self.load_candidates(session)
        with self._lock:
            return self._candidate_cache.get("all", []), False

    def semantic_similarity(self, text1: str, text2: str) -> float:
        """Calculate semantic similarity"""
        if not self._semantic_engine:
            return 0.0
        
        cache_key = f"sim_{hash(text1)}_{hash(text2)}"
        if cache_key in self._similarity_cache:
            return self._similarity_cache[cache_key]
        
        try:
            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity
            
            vec1 = self._semantic_engine.encode(text1, convert_to_numpy=True)
            vec2 = self._semantic_engine.encode(text2, convert_to_numpy=True)
            score = float(cosine_similarity([vec1], [vec2])[0][0])
            self._similarity_cache[cache_key] = score
            return score
        except Exception:
            return 0.0

    def search(self, session: Session, message: str) -> DealerSearchResult:
        """
        Enhanced dealer resolution with 8-stage priority:
        1. Dealer Code
        2. Customer Code
        3. Exact Dealer Name
        4. Alias
        5. Semantic Similarity
        6. RapidFuzz
        7. Suggestions
        """
        started = time.perf_counter()
        original = _text(message, "")
        normalized = self.normalize(original)
        alias = self.DEALER_ALIASES.get(normalized)
        search_text = alias or normalized
        cache_key = search_text.lower()
        
        # Check cache
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached:
            result = DealerSearchResult(**asdict(cached))
            result.original_message = original
            result.cache_used = True
            return result
        
        result = DealerSearchResult(original, search_text, normalized, alias_used=alias)
        
        try:
            candidates, _ = self.get_candidates(session)
            candidates_list = candidates if candidates else []
            
            # Stage 1: Dealer Code
            token = original.strip()
            for item in candidates_list:
                if token == item["dealer_code"]:
                    result.dealer_found = item["name"]
                    result.dealer_code = item["dealer_code"]
                    result.customer_code = item["customer_code"]
                    result.match_source = "dealer_code"
                    self._cache_result(cache_key, result)
                    return result
            
            # Stage 2: Customer Code
            for item in candidates_list:
                if token == item["customer_code"]:
                    result.dealer_found = item["name"]
                    result.dealer_code = item["dealer_code"]
                    result.customer_code = item["customer_code"]
                    result.match_source = "customer_code"
                    self._cache_result(cache_key, result)
                    return result
            
            # Stage 3: Exact Dealer Name
            norm_search = self.normalize(search_text)
            for item in candidates_list:
                if item["normalized"] == norm_search:
                    result.dealer_found = item["name"]
                    result.dealer_code = item["dealer_code"]
                    result.customer_code = item["customer_code"]
                    result.rapidfuzz_score = 100.0
                    result.match_source = "exact_match"
                    self._cache_result(cache_key, result)
                    return result
            
            # Stage 4: Alias (already handled)
            
            # Stage 5: Semantic Similarity
            if self._semantic_engine and candidates_list:
                best_match = None
                best_score = 0.0
                for item in candidates_list[:100]:
                    score = self.semantic_similarity(search_text, item["normalized"])
                    if score > best_score:
                        best_score = score
                        best_match = item
                        if score > 0.7:
                            break
                
                if best_match and best_score > 0.7:
                    result.dealer_found = best_match["name"]
                    result.dealer_code = best_match["dealer_code"]
                    result.customer_code = best_match["customer_code"]
                    result.semantic_score = round(best_score, 3)
                    result.match_source = "semantic"
                    self._cache_result(cache_key, result)
                    return result
            
            # Stage 6: RapidFuzz
            choices = {i: item["normalized"] for i, item in enumerate(candidates_list)}
            matches = process.extract(search_text, choices, scorer=fuzz.WRatio, limit=5)
            scored = [(candidates_list[i], float(score)) for _, score, i in matches]
            best, score = scored[0] if scored else (None, 0)
            
            if best and score >= 85:
                result.dealer_found = best["name"]
                result.dealer_code = best["dealer_code"]
                result.customer_code = best["customer_code"]
                result.rapidfuzz_score = round(score, 2)
                result.match_source = "rapidfuzz"
                self._cache_result(cache_key, result)
                return result
            
            # Stage 7: Suggestions
            if scored and score >= 60:
                result.suggestions = [
                    {"dealer_name": item["name"], "similarity": round(s, 2), "dealer_code": item["dealer_code"]}
                    for item, s in scored[:5]
                ]
                result.rapidfuzz_score = round(scored[0][1], 2)
                result.ambiguous = True
                result.match_source = "suggestions"
            else:
                result.suggestions = [
                    {"dealer_name": item["name"], "similarity": round(score, 2), "dealer_code": item["dealer_code"]}
                    for item, score in scored[:5] if score > 40
                ]
                if result.suggestions:
                    result.ambiguous = True
            
            self._cache_result(cache_key, result)
            
        except Exception as error:
            result.exception = str(error)
            logger.exception(f"Dealer resolution failed for {original}")
        
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > 100:
            logger.warning(f"Slow dealer resolution: {elapsed_ms:.2f}ms for {original}")
        
        return result

    def _cache_result(self, key: str, result: DealerSearchResult) -> None:
        with self._lock:
            self._cache[key] = result


# ============================================================
# BLOCK 8: DEALER REPOSITORY - REUSES DN_ANALYSIS STYLE
# ============================================================

class DealerRepository:
    """
    Dealer repository - identical SQL style to dn_analysis.py
    PostgreSQL is the ONLY source of truth.
    """
    
    _GROUP_COLUMNS: tuple[str, ...] = (
        "customer_name", "dealer_code", "customer_code", "ship_to_city",
        "warehouse", "warehouse_code", "delivery_location", "sales_office",
        "sales_manager", "division",
    )

    @classmethod
    def _aggregate_sql(cls, where: str = "TRUE", order_by: str = "total_revenue DESC") -> str:
        columns = ", ".join(cls._GROUP_COLUMNS)
        return f"""
            SELECT {columns},
                   COUNT(DISTINCT dn_no) AS total_dn,
                   COALESCE(SUM(dn_qty), 0) AS total_units,
                   COALESCE(SUM(dn_amount), 0) AS total_revenue,
                   COUNT(DISTINCT material_no) AS material_count,
                   COUNT(DISTINCT customer_model) AS model_count,
                   COUNT(DISTINCT dn_no) FILTER (WHERE pod_date IS NULL OR pending_flag = true) AS pending_dn,
                   COUNT(DISTINCT dn_no) FILTER (WHERE good_issue_date IS NULL) AS pgi_pending_dn,
                   COUNT(DISTINCT dn_no) FILTER (WHERE good_issue_date IS NOT NULL AND pod_date IS NULL) AS pod_pending_dn,
                   MIN(dn_create_date) AS first_delivery_date,
                   MAX(dn_create_date) AS latest_delivery_date,
                   MAX(good_issue_date) AS latest_pgi_date,
                   MAX(pod_date) AS latest_pod_date,
                   AVG(EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400) AS avg_delivery,
                   AVG(EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400) AS avg_pod,
                   AVG(EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400) AS avg_cycle
              FROM {TABLE}
             WHERE {where}
             GROUP BY {columns}
             ORDER BY {order_by}
        """

    @staticmethod
    def dealer_filter(identifier: str) -> Any:
        token = identifier.strip()
        return or_(
            func.lower(func.trim(DeliveryReport.customer_name)) == token.lower(),
            DeliveryReport.dealer_code == token,
            DeliveryReport.customer_code == token,
        )


# ============================================================
# BLOCK 9: DEALER AGGREGATION ENGINE
# ============================================================

class DealerAggregationEngine:
    """Dealer aggregation engine - reuses dn_analysis.py logic"""
    
    def __init__(self, session: Session):
        self.session = session
        self._executor = ThreadPoolExecutor(max_workers=8)
    
    def get_dealer_data(self, dealer_identifier: str) -> Optional[dict[str, Any]]:
        """Get aggregated dealer data from PostgreSQL"""
        try:
            condition = DealerRepository.dealer_filter(dealer_identifier)
            
            query = self.session.query(
                func.coalesce(DeliveryReport.customer_name, "Unknown").label("dealer_name"),
                func.coalesce(DeliveryReport.dealer_code, "Unknown").label("dealer_code"),
                func.coalesce(DeliveryReport.customer_code, "Unknown").label("customer_code"),
                func.max(DeliveryReport.ship_to_city).label("city"),
                func.max(DeliveryReport.delivery_location).label("delivery_location"),
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.division).label("division"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
                func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_dn"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("completed_dn"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)))).label("pgi_pending_dn"),
                func.count(distinct(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pod_pending_dn"),
                func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
                func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
                func.max(DeliveryReport.good_issue_date).label("latest_pgi_date"),
                func.max(DeliveryReport.pod_date).label("latest_pod_date"),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
                func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
            ).filter(condition).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code
            ).first()
            
            if not query:
                return None
            
            return {
                "dealer_name": _text(query.dealer_name),
                "dealer_code": _text(query.dealer_code),
                "customer_code": _text(query.customer_code),
                "city": _text(query.city),
                "delivery_location": _text(query.delivery_location),
                "warehouse": _text(query.warehouse),
                "warehouse_code": _text(query.warehouse_code),
                "sales_office": _text(query.sales_office),
                "sales_manager": _text(query.sales_manager),
                "division": _text(query.division),
                "total_dn": int(query.total_dn or 0),
                "pending_dn": int(query.pending_dn or 0),
                "completed_dn": int(query.completed_dn or 0),
                "total_units": int(query.total_units or 0),
                "total_revenue": float(query.total_revenue or 0.0),
                "pgi_pending_dn": int(query.pgi_pending_dn or 0),
                "pod_pending_dn": int(query.pod_pending_dn or 0),
                "first_delivery_date": _date_text(query.first_delivery_date),
                "latest_delivery_date": _date_text(query.latest_delivery_date),
                "latest_pgi_date": _date_text(query.latest_pgi_date),
                "latest_pod_date": _date_text(query.latest_pod_date),
                "avg_delivery": _days(query.avg_delivery),
                "avg_pod": _days(query.avg_pod),
                "avg_cycle": _days(query.avg_cycle),
            }
        except Exception as e:
            logger.error(f"Dealer aggregation failed: {e}")
            return None


# ============================================================
# BLOCK 10: MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """
    Enterprise Dealer Intelligence Engine
    PostgreSQL is the ONLY source of truth.
    Reuses dn_analysis.py architecture.
    """
    
    SORT_ALIASES = {
        "revenue": "total_revenue",
        "units": "total_units",
        "dn": "total_dn",
        "delivery": "delivery_success_pct",
        "pgi": "pgi_success_pct",
        "pod": "pod_success_pct",
        "pending": "pending_pct",
        "growth": "revenue_growth_pct",
        "business_score": "business_score",
        "regional": "regional_rank",
        "national": "national_rank"
    }
    
    def __init__(self) -> None:
        self._service_name = "dealer_analytics"
        self._version = "9.0.0-enterprise"
        self._startup_time = datetime.utcnow().isoformat()
        self._initialization_errors: list[str] = []
        
        # Initialize services - identical to dn_analysis.py
        self._distance = DistanceService()
        self._search_engine = DealerSearchEngine()
        
        # Thread pool for parallel processing
        self._executor = ThreadPoolExecutor(max_workers=8)
        
        # Caches - identical to dn_analysis.py
        self._dashboard_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._ranking_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=128, ttl=600)
        self._aggregate_cache: TTLCache[str, list[Any]] = TTLCache(maxsize=1024, ttl=300)
        self._extended_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=4096, ttl=3600)
        
        self._lock = threading.RLock()
        self._last_diagnostic: dict[str, Any] = {}
        
        # Pre-load candidates
        try:
            with self._session() as session:
                self._search_engine.load_candidates(session)
        except Exception as e:
            logger.warning(f"⚠️ Failed to load candidates: {e}")
        
        logger.info(f"✅ DealerAnalyticsService initialized (v{self._version})")

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

    @staticmethod
    def _date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

    @staticmethod
    def _flag(value: Any) -> bool:
        return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "pending"}

    @staticmethod
    def _response(
        success: bool,
        data: Any = None,
        whatsapp_message: str = "",
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Response format - identical to dn_analysis.py"""
        return {
            "success": success,
            "data": {} if data is None else data,
            "whatsapp_message": whatsapp_message,
            "error": error,
            "metadata": dict(metadata or {}),
        }

    @staticmethod
    def _suggestion_response(search: DealerSearchResult) -> dict[str, Any]:
        """Format suggestion response"""
        suggestions = search.suggestions[:5]
        if search.ambiguous:
            lines = ["Multiple Dealers Found", ""]
            for index, item in enumerate(suggestions, 1):
                lines.extend((str(index), item["dealer_name"], f'{item["similarity"]:.0f}%', ""))
            lines.append("Reply with dealer number.")
            code = "MULTIPLE_DEALERS_FOUND"
        else:
            lines = ["Did you mean", ""]
            for item in suggestions:
                lines.extend((item["dealer_name"], f'{item["similarity"]:.0f}%', ""))
            code = "DEALER_SUGGESTIONS"
        
        return {
            "success": False,
            "error_code": code,
            "message": "\n".join(lines).strip(),
            "suggestions": suggestions,
            "search": search
        }

    # ============================================================
    # BLOCK 11: DASHBOARD BUILDING ENGINE
    # ============================================================

    def _build_dashboard(self, row: dict[str, Any]) -> DealerDashboard:
        """Build DealerDashboard from row - reuses dn_analysis.py logic"""
        total = int(row.get("total_dn", 0))
        pending = int(row.get("pending_dn", 0))
        
        # Calculate distance - identical to dn_analysis.py
        distance = self._distance.calculate(
            str(row.get("warehouse") or row.get("warehouse_code") or ""),
            str(row.get("delivery_location") or row.get("city") or ""),
        )
        
        # Calculate aging - identical to dn_analysis.py
        today = datetime.now().date()
        dn_date = self._date(row.get("first_delivery_date"))
        issue_date = self._date(row.get("latest_pgi_date"))
        pod_date = self._date(row.get("latest_pod_date"))
        pending_flag = pending > 0
        
        pgi_aging = _days((issue_date - dn_date) if issue_date and dn_date else 0)
        pod_aging = _days((pod_date - issue_date) if pod_date and issue_date else 0)
        delivery_aging = _days((pod_date or (today if pending_flag else None)) - dn_date) if dn_date else 0
        
        return DealerDashboard(
            dealer_name=row.get("dealer_name", "Unknown"),
            dealer_code=row.get("dealer_code", "Unknown"),
            customer_code=row.get("customer_code", "Unknown"),
            city=row.get("city", "Unknown"),
            warehouse=row.get("warehouse", "Unknown"),
            warehouse_code=row.get("warehouse_code", "Unknown"),
            sales_office=row.get("sales_office", "Unknown"),
            sales_manager=row.get("sales_manager", "Unknown"),
            division=row.get("division", "Unknown"),
            delivery_location=row.get("delivery_location", "Unknown"),
            total_dn=total,
            completed_dn=row.get("completed_dn", 0),
            pending_dn=pending,
            total_units=row.get("total_units", 0),
            total_revenue=row.get("total_revenue", 0.0),
            pending_revenue=0.0,  # Will be calculated separately
            average_revenue_per_dn=round(row.get("total_revenue", 0.0) / total, 2) if total else 0,
            average_units_per_dn=round(row.get("total_units", 0) / total, 2) if total else 0,
            average_delivery_days=row.get("avg_delivery", 0.0),
            average_pod_days=row.get("avg_pod", 0.0),
            average_total_cycle_time=row.get("avg_cycle", 0.0),
            first_delivery_date=row.get("first_delivery_date", "N/A"),
            latest_delivery_date=row.get("latest_delivery_date", "N/A"),
            latest_pgi_date=row.get("latest_pgi_date", "N/A"),
            latest_pod_date=row.get("latest_pod_date", "N/A"),
            pgi_aging_days=pgi_aging,
            pod_aging_days=pod_aging,
            delivery_aging_days=delivery_aging,
            transit_days=row.get("avg_delivery", 0.0),
            distance=distance,
            delivery_success_pct=_percent(row.get("completed_dn", 0), total),
            pending_pct=_percent(pending, total),
            pgi_pending_dn=row.get("pgi_pending_dn", 0),
            pod_pending_dn=row.get("pod_pending_dn", 0),
        )

    # ============================================================
    # BLOCK 12: EXTENDED ANALYTICS ENGINE
    # ============================================================

    def _apply_extended_analytics(self, session: Session, dashboard: DealerDashboard) -> None:
        """Apply extended analytics - reuses dn_analysis.py logic"""
        identity = dashboard.dealer_code if dashboard.dealer_code != "Unknown" else dashboard.customer_code
        identity = identity if identity != "Unknown" else dashboard.dealer_name
        cache_key = str(identity).lower()
        
        cached = self._extended_cache.get(cache_key)
        if cached:
            for key, value in cached.items():
                setattr(dashboard, key, value)
            self._apply_business_health(dashboard)
            dashboard.insights, dashboard.recommendations = self._business_insights(dashboard)
            return
        
        values: dict[str, Any] = {}
        
        # Parallel queries for speed
        futures = {
            'monthly': self._executor.submit(self._get_monthly_analytics, session, identity),
            'product': self._executor.submit(self._get_product_analytics, session, identity),
            'pending': self._executor.submit(self._get_pending_analytics, session, identity),
            'warehouse_util': self._executor.submit(self._get_warehouse_utilization, session, dashboard.warehouse, dashboard.total_units),
        }
        
        for key, future in futures.items():
            try:
                result = future.result(timeout=2.0)
                if result:
                    values.update(result)
            except Exception as e:
                logger.warning(f"Parallel query {key} failed: {e}")
        
        # Apply rankings
        self._apply_rankings(session, dashboard, values)
        
        # Apply all values
        for key, value in values.items():
            setattr(dashboard, key, value)
        
        # Calculate additional KPIs - identical to dn_analysis.py
        self._calculate_additional_kpis(dashboard)
        
        # Apply business health - identical to dn_analysis.py
        self._apply_business_health(dashboard)
        
        # Cache results
        self._extended_cache[cache_key] = values
        dashboard.insights, dashboard.recommendations = self._business_insights(dashboard)

    def _get_monthly_analytics(self, session: Session, dealer_identifier: str) -> dict[str, Any]:
        """Get monthly analytics"""
        try:
            condition = DealerRepository.dealer_filter(dealer_identifier)
            
            monthly = session.query(
                func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("month"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            ).filter(condition, DeliveryReport.dn_create_date.isnot(None)).group_by("month").all()
            
            if not monthly:
                return {}
            
            month_map = {r.month: r for r in monthly}
            current = date.today().strftime("%Y-%m")
            prev_date = date.today().replace(day=1) - timedelta(days=1)
            previous = prev_date.strftime("%Y-%m")
            
            current_row, previous_row = month_map.get(current), month_map.get(previous)
            current_revenue = _number(current_row.revenue) if current_row else 0.0
            previous_revenue = _number(previous_row.revenue) if previous_row else 0.0
            growth = _growth(current_revenue, previous_revenue)
            
            best = max(monthly, key=lambda r: _number(r.revenue))
            worst = min(monthly, key=lambda r: _number(r.revenue))
            
            return {
                "current_month_revenue": round(current_revenue, 2),
                "previous_month_revenue": round(previous_revenue, 2),
                "monthly_growth": round(growth, 2),
                "current_month_units": int(current_row.units or 0) if current_row else 0,
                "previous_month_units": int(previous_row.units or 0) if previous_row else 0,
                "current_month_dn": int(current_row.dns or 0) if current_row else 0,
                "previous_month_dn": int(previous_row.dns or 0) if previous_row else 0,
                "best_month": _text(best.month),
                "worst_month": _text(worst.month),
                "busiest_month": _text(best.month),
                "revenue_growth_pct": round(growth, 2),
            }
        except Exception:
            return {}

    def _get_product_analytics(self, session: Session, dealer_identifier: str) -> dict[str, Any]:
        """Get product analytics"""
        try:
            condition = DealerRepository.dealer_filter(dealer_identifier)
            
            top_model = session.query(
                DeliveryReport.customer_model.label("model"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(condition, DeliveryReport.customer_model.isnot(None)).group_by(
                DeliveryReport.customer_model
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
            
            top_material = session.query(
                DeliveryReport.material_no.label("material"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(condition, DeliveryReport.material_no.isnot(None)).group_by(
                DeliveryReport.material_no
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
            
            return {
                "top_product": _text(top_model.model) if top_model else "Unknown",
                "top_model": _text(top_model.model) if top_model else "Unknown",
                "top_material": _text(top_material.material) if top_material else "Unknown",
                "highest_revenue_product": _text(top_model.model) if top_model else "Unknown",
            }
        except Exception:
            return {}

    def _get_pending_analytics(self, session: Session, dealer_identifier: str) -> dict[str, Any]:
        """Get pending analytics"""
        try:
            condition = DealerRepository.dealer_filter(dealer_identifier)
            
            pending_rows = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_create_date,
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            ).filter(
                condition,
                or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None))
            ).group_by(DeliveryReport.dn_no, DeliveryReport.dn_create_date).all()
            
            if not pending_rows:
                return {}
            
            # Calculate pending metrics
            today = date.today()
            ages = []
            total_revenue = 0.0
            total_units = 0
            
            for row in pending_rows:
                dn_date = row.dn_create_date
                if dn_date:
                    age = (today - dn_date).days
                    ages.append(age)
                total_revenue += _number(row.revenue)
                total_units += _number(row.units)
            
            oldest = min(pending_rows, key=lambda r: r.dn_create_date or date.max)
            avg_age = sum(ages) / len(ages) if ages else 0
            
            return {
                "pending_revenue": round(total_revenue, 2),
                "pending_units": int(total_units),
                "pending_average_days": round(avg_age, 2),
                "critical_pending": sum(1 for age in ages if age > 7),
                "overdue_pending": sum(1 for age in ages if age > 14),
                "oldest_pending_dn": _text(oldest.dn_no),
                "oldest_pending_days": max(ages) if ages else 0,
            }
        except Exception:
            return {}

    def _get_warehouse_utilization(self, session: Session, warehouse: str, dealer_units: int) -> dict[str, Any]:
        """Get warehouse utilization"""
        try:
            warehouse_units = session.query(
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0)
            ).filter(DeliveryReport.warehouse == warehouse).scalar() or 0
            
            utilization = _percent(dealer_units, warehouse_units)
            
            return {
                "warehouse_utilization": utilization,
                "warehouse_contribution": utilization,
            }
        except Exception:
            return {"warehouse_utilization": 0.0, "warehouse_contribution": 0.0}

    def _apply_rankings(self, session: Session, dashboard: DealerDashboard, values: dict) -> None:
        """Apply comprehensive rankings"""
        cache_key = f"rankings_{dashboard.dealer_code}"
        cached_rankings = self._ranking_cache.get(cache_key)
        if cached_rankings:
            values.update(cached_rankings)
            return
        
        try:
            ranking_rows = session.query(
                DeliveryReport.customer_name.label("name"),
                DeliveryReport.dealer_code.label("code"),
                func.max(DeliveryReport.ship_to_city).label("city"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("delivery"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("pod"),
                func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending"),
            ).filter(DeliveryReport.customer_name.isnot(None)).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code
            ).all()
            
            target = next(
                (r for r in ranking_rows
                 if _text(r.code, "") == dashboard.dealer_code or _text(r.name, "") == dashboard.dealer_name),
                None
            )
            
            if not target:
                return
            
            def rank_for(rows: list, key_func, reverse: bool = True) -> int:
                sorted_rows = sorted(rows, key=key_func, reverse=reverse)
                for idx, row in enumerate(sorted_rows, 1):
                    if row is target:
                        return idx
                return len(rows)
            
            rankings = {
                "revenue_rank": rank_for(ranking_rows, lambda r: _number(r.revenue), True),
                "unit_rank": rank_for(ranking_rows, lambda r: _number(r.units), True),
                "dn_rank": rank_for(ranking_rows, lambda r: int(r.dns or 0), True),
                "delivery_rank": rank_for(
                    ranking_rows,
                    lambda r: _days(r.delivery) if r.delivery is not None else float("inf"),
                    False
                ),
                "pod_rank": rank_for(ranking_rows, lambda r: _percent(r.pod, r.dns), True),
                "pending_rank": rank_for(ranking_rows, lambda r: _percent(r.pending, r.dns), False),
                "pgi_rank": rank_for(ranking_rows, lambda r: _percent(r.pod, r.dns), True),
            }
            
            # National ranking
            composite = sorted(
                ranking_rows,
                key=lambda r: (_number(r.revenue), _percent(r.pod, r.dns)),
                reverse=True
            )
            rankings["national_rank"] = next(
                (idx for idx, row in enumerate(composite, 1) if row is target),
                len(composite)
            )
            
            # Regional ranking
            regional = [r for r in ranking_rows if _text(r.city, "").lower() == dashboard.city.lower()]
            regional.sort(key=lambda r: _number(r.revenue), reverse=True)
            rankings["regional_rank"] = next(
                (idx for idx, row in enumerate(regional, 1) if row is target),
                len(regional) or 1
            )
            
            values.update(rankings)
            self._ranking_cache[cache_key] = rankings
        except Exception as e:
            logger.warning(f"Rankings failed: {e}")

    def _calculate_additional_kpis(self, dashboard: DealerDashboard) -> None:
        """Calculate additional KPIs - identical to dn_analysis.py"""
        # Revenue per day
        if dashboard.first_delivery_date and dashboard.first_delivery_date != "N/A":
            try:
                first_date = datetime.strptime(dashboard.first_delivery_date, "%d-%b-%Y").date()
                days_active = max(1, (date.today() - first_date).days)
                dashboard.revenue_per_day = dashboard.total_revenue / days_active
            except:
                dashboard.revenue_per_day = 0.0
        
        # Revenue per delivery
        dashboard.revenue_per_delivery = dashboard.total_revenue / dashboard.total_dn if dashboard.total_dn > 0 else 0.0
        
        # Average order value
        dashboard.average_order_value = dashboard.total_revenue / dashboard.total_dn if dashboard.total_dn > 0 else 0.0
        
        # Monthly averages
        dashboard.average_revenue_per_month = dashboard.total_revenue / 12
        dashboard.average_units_per_month = dashboard.total_units / 12
        dashboard.average_dn_per_month = dashboard.total_dn / 12

    def _apply_business_health(self, dashboard: DealerDashboard) -> None:
        """Business health calculation - identical to dn_analysis.py"""
        # Weighted score with multiple factors
        score = (
            dashboard.delivery_success_pct * 0.25 +
            dashboard.pgi_success_pct * 0.15 +
            dashboard.pod_success_pct * 0.20 +
            max(0.0, 100.0 - dashboard.pending_pct) * 0.15 +
            min(100.0, max(0.0, 100.0 - dashboard.critical_pending * 2)) * 0.10 +
            min(100.0, max(0.0, 100.0 + dashboard.monthly_growth)) * 0.10 +
            dashboard.warehouse_utilization * 0.05
        )
        
        dashboard.business_score = round(max(0.0, min(100.0, score)), 1)
        
        # Determine status
        if dashboard.business_score >= 85:
            dashboard.overall_status = BusinessHealthStatus.EXCELLENT.value
            dashboard.performance_grade = "A"
        elif dashboard.business_score >= 70:
            dashboard.overall_status = BusinessHealthStatus.GOOD.value
            dashboard.performance_grade = "B"
        elif dashboard.business_score >= 50:
            dashboard.overall_status = BusinessHealthStatus.WATCH.value
            dashboard.performance_grade = "C"
        else:
            dashboard.overall_status = BusinessHealthStatus.CRITICAL.value
            dashboard.performance_grade = "D"
        
        # Risk score
        dashboard.risk_score = round(100 - dashboard.business_score, 1)
        
        # Executive summary
        trend = "growing" if dashboard.monthly_growth >= 0 else "declining"
        action = "maintain current controls" if dashboard.business_score >= 70 else "prioritize pending DN and POD closure"
        
        dashboard.executive_summary = (
            f"{dashboard.dealer_name} is {trend} with a {dashboard.business_score:.1f}/100 business score. "
            f"Delivery success is {dashboard.delivery_success_pct:.1f}% and {dashboard.pending_dn} DNs remain pending. "
            f"Revenue growth is {dashboard.monthly_growth:+.1f}% month over month. "
            f"Recommendation: {action}."
        )

    def _business_insights(self, dashboard: DealerDashboard) -> tuple[list[str], list[str]]:
        """Generate business insights - identical to dn_analysis.py"""
        trend = "increasing" if dashboard.monthly_growth >= 0 else "decreasing"
        
        insights = [
            f"Revenue is {trend} ({dashboard.monthly_growth:+.1f}% month over month).",
            f"Dealer has {dashboard.pending_dn:,} pending DNs (worth PKR {dashboard.pending_revenue:,.2f}).",
            f"Delivery success is {dashboard.delivery_success_pct:.1f}% with average delivery of {dashboard.average_delivery_days:.1f} days.",
            f"POD completion is {dashboard.pod_success_pct:.1f}%.",
            f"Top model: {dashboard.top_model}; Top material: {dashboard.top_material}.",
            f"National rank: #{dashboard.national_rank or 'N/A'}.",
        ]
        
        # Revenue insights
        if dashboard.monthly_growth > 10:
            insights.append(f"Revenue growth is strong at {dashboard.monthly_growth:+.1f}%.")
        elif dashboard.monthly_growth < -10:
            insights.append(f"Revenue is declining ({dashboard.monthly_growth:+.1f}%). Investigate causes.")
        
        # Pending insights
        if dashboard.oldest_pending_days > 14:
            insights.append(f"Oldest pending DN {dashboard.oldest_pending_dn} is {dashboard.oldest_pending_days} days old.")
        if dashboard.critical_pending > 5:
            insights.append(f"Critical pending (>7 days): {dashboard.critical_pending} DNs.")
        
        # Strengths
        strengths = []
        if dashboard.delivery_success_pct >= 90:
            strengths.append("Excellent delivery performance")
        if dashboard.pod_success_pct >= 90:
            strengths.append("Strong POD completion")
        if dashboard.monthly_growth >= 10:
            strengths.append("Strong revenue growth")
        if dashboard.pending_pct < 10:
            strengths.append("Low pending rate")
        
        # Weaknesses
        weaknesses = []
        if dashboard.pending_pct > 25:
            weaknesses.append("High pending rate")
        if dashboard.pod_success_pct < 80:
            weaknesses.append("Low POD completion")
        if dashboard.delivery_success_pct < 80:
            weaknesses.append("Low delivery success")
        if dashboard.monthly_growth < -10:
            weaknesses.append("Declining revenue")
        
        dashboard.strengths = strengths
        dashboard.weaknesses = weaknesses
        
        # Recommendations
        recommendations = []
        if dashboard.overdue_pending:
            recommendations.append(f"Escalate {dashboard.overdue_pending} DNs pending for more than 14 days.")
        if dashboard.pod_success_pct < 85:
            recommendations.append("Prioritize POD collection and closure.")
        if dashboard.pgi_pending_dn:
            recommendations.append(f"Review {dashboard.pgi_pending_dn} DNs awaiting PGI.")
        if dashboard.delivery_success_pct < 85:
            recommendations.append("Review delivery process for improvement.")
        if not recommendations:
            recommendations.append("Maintain current delivery and POD control process.")
            recommendations.append("Continue monitoring key performance indicators.")
        
        return insights, recommendations

    # ============================================================
    # BLOCK 13: PUBLIC API METHODS - BACKWARD COMPATIBLE
    # ============================================================

    def get_dealer_dashboard(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        """Get enhanced dealer dashboard - identical to dn_analysis.py style"""
        start_time = time.perf_counter()
        
        identifier = dealer_name or kwargs.get("dealer") or kwargs.get("dealer_code") or kwargs.get("customer_code") or ""
        if not identifier:
            return self._response(False, error="DEALER_REQUIRED", whatsapp_message="Please provide a dealer name or code.")
        
        try:
            with self._session() as session:
                search = self._search_engine.search(session, str(identifier))
                if search.exception:
                    return self._response(False, error="SEARCH_ERROR", whatsapp_message="Dealer search is temporarily unavailable.")
                if not search.dealer_found:
                    return self._suggestion_response(search)
                
                resolved_identity = search.dealer_code or search.customer_code or search.dealer_found
                dashboard_key = str(resolved_identity).lower()
                
                cached_dashboard = self._dashboard_cache.get(dashboard_key)
                if cached_dashboard:
                    return cached_dashboard
                
                # Get aggregated data
                agg_engine = DealerAggregationEngine(session)
                row = agg_engine.get_dealer_data(resolved_identity)
                
                if not row:
                    return self._suggestion_response(search)
                
                # Build dashboard
                dashboard = self._build_dashboard(row)
                
                # Apply extended analytics
                try:
                    self._apply_extended_analytics(session, dashboard)
                except Exception:
                    logger.exception("Extended analytics failed")
                    dashboard.insights, dashboard.recommendations = self._business_insights(dashboard)
                
                # Format WhatsApp message
                format_type = kwargs.get("format", "standard")
                formatted = dashboard.to_whatsapp_message(format_type)
                
                response = {
                    "success": True,
                    "data": dashboard,
                    "dashboard": dashboard,
                    "search": search,
                    "whatsapp_message": formatted,
                    "formatted_response": formatted,
                    "message": formatted,
                    "response": formatted,
                    "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2),
                    "metadata": {
                        "source": "PostgreSQL",
                        "dealer": dashboard.dealer_name,
                        "format": format_type,
                    }
                }
                
                self._dashboard_cache[dashboard_key] = response
                return response
                
        except Exception as error:
            logger.exception("Dealer dashboard query failed")
            return self._response(False, error="DATABASE_UNAVAILABLE", whatsapp_message="Dealer database is currently unavailable.")

    def get_dealer_profile(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        """Get enhanced dealer profile"""
        result = self.get_dealer_dashboard(dealer_name, **kwargs)
        if not result.get("success"):
            return result
        
        result["profile"] = result["data"]
        result["whatsapp_message"] = result["data"].to_whatsapp_message()
        result["message"] = result["whatsapp_message"]
        result["response"] = result["whatsapp_message"]
        return result

    def compare_dealers(self, dealer_names: Any = None, dealer_two: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
        """Compare two or more dealers"""
        try:
            values = dealer_names or kwargs.get("dealers") or kwargs.get("dealer1") or []
            if isinstance(values, str):
                values = [values]
            values = list(values)
            second = dealer_two or kwargs.get("dealer2")
            if second:
                values.append(second)
            values = list(dict.fromkeys(str(v) for v in values if v))
            
            if len(values) < 2:
                return self._response(False, error="TWO_DEALERS_REQUIRED", whatsapp_message="Please provide at least two dealers.")
            
            dashboards = []
            for value in values[:10]:
                result = self.get_dealer_dashboard(value)
                if result.get("success"):
                    dashboards.append(result["data"])
            
            if len(dashboards) < 2:
                return self._response(False, error="DEALERS_NOT_FOUND", whatsapp_message="At least two matching dealers are required.")
            
            comparison = DealerComparison(
                dashboards,
                max(dashboards, key=lambda x: x.total_revenue).dealer_name,
                max(dashboards, key=lambda x: x.total_units).dealer_name,
                max(dashboards, key=lambda x: x.total_dn).dealer_name,
                min(dashboards, key=lambda x: x.average_delivery_days or float("inf")).dealer_name,
                [
                    f"{max(dashboards, key=lambda x: x.total_revenue).dealer_name} leads revenue.",
                    f"{min(dashboards, key=lambda x: x.pending_pct).dealer_name} has the lowest pending rate."
                ],
            )
            return self._response(True, comparison, comparison.summary[0])
        except Exception as error:
            logger.exception("Dealer comparison failed")
            return self._response(False, error="COMPARISON_ERROR", whatsapp_message="Dealer comparison is temporarily unavailable.")

    def get_top_dealers(self, limit: int = 10, sort_by: str = "revenue", **kwargs: Any) -> dict[str, Any]:
        """Get top dealers by various metrics"""
        return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), False)

    def get_bottom_dealers(self, limit: int = 10, sort_by: str = "pending_pct", **kwargs: Any) -> dict[str, Any]:
        """Get bottom dealers by various metrics"""
        return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), True)

    def _rank(self, sort_by: str, limit: int, bottom: bool) -> dict[str, Any]:
        """Internal ranking method"""
        try:
            cache_key = f"{sort_by.lower()}|{int(limit)}|{int(bottom)}"
            cached = self._ranking_cache.get(cache_key)
            if cached:
                return cached
            
            with self._session() as session:
                agg_engine = DealerAggregationEngine(session)
                rows = []
                # Get all dealers
                all_dealers = session.query(
                    func.coalesce(DeliveryReport.customer_name, "Unknown").label("dealer_name"),
                    func.coalesce(DeliveryReport.dealer_code, "Unknown").label("dealer_code"),
                    func.coalesce(DeliveryReport.customer_code, "Unknown").label("customer_code"),
                    func.max(DeliveryReport.ship_to_city).label("city"),
                    func.max(DeliveryReport.delivery_location).label("delivery_location"),
                    func.max(DeliveryReport.warehouse).label("warehouse"),
                    func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                    func.max(DeliveryReport.sales_office).label("sales_office"),
                    func.max(DeliveryReport.sales_manager).label("sales_manager"),
                    func.max(DeliveryReport.division).label("division"),
                    func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
                    func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_dn"),
                    func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("completed_dn"),
                    func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
                    func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
                    func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("pod_success"),
                    func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending"),
                ).filter(DeliveryReport.customer_name.isnot(None)).group_by(
                    DeliveryReport.customer_name,
                    DeliveryReport.dealer_code,
                    DeliveryReport.customer_code
                ).all()
                
                for row in all_dealers:
                    rows.append({
                        "dealer_name": _text(row.dealer_name),
                        "dealer_code": _text(row.dealer_code),
                        "customer_code": _text(row.customer_code),
                        "city": _text(row.city),
                        "delivery_location": _text(row.delivery_location),
                        "warehouse": _text(row.warehouse),
                        "warehouse_code": _text(row.warehouse_code),
                        "sales_office": _text(row.sales_office),
                        "sales_manager": _text(row.sales_manager),
                        "division": _text(row.division),
                        "total_dn": int(row.total_dn or 0),
                        "pending_dn": int(row.pending_dn or 0),
                        "completed_dn": int(row.completed_dn or 0),
                        "total_units": int(row.total_units or 0),
                        "total_revenue": float(row.total_revenue or 0.0),
                        "avg_delivery": _days(row.avg_delivery),
                        "pod_success": int(row.pod_success or 0),
                        "pending": int(row.pending or 0),
                    })
            
            items = [self._build_dashboard(row) for row in rows]
            
            key_name = self.SORT_ALIASES.get(sort_by.lower().replace(" ", "_"), "total_revenue")
            reverse = (not bottom) or (bottom and key_name in {"pending_pct", "average_delivery_days"})
            
            items.sort(
                key=lambda v: getattr(v, key_name, 0) if getattr(v, key_name, None) is not None else 0,
                reverse=reverse
            )
            
            ranking = DealerRanking(sort_by, "bottom" if bottom else "top", items[:max(1, min(int(limit), 100))])
            response = self._response(True, ranking, f"Top {len(ranking.dealers)} dealers by {sort_by}")
            self._ranking_cache[cache_key] = response
            return response
        except (SQLAlchemyError, ValueError) as error:
            logger.exception("Dealer ranking failed")
            return self._response(False, error="RANKING_ERROR", whatsapp_message="Dealer ranking is currently unavailable.")

    def diagnose_dealer_search(self, message: str = "", **kwargs: Any) -> dict[str, Any]:
        """Diagnose dealer search"""
        started = time.perf_counter()
        try:
            with self._session() as session:
                result = self._search_engine.search(session, message or kwargs.get("dealer_name") or kwargs.get("dealer") or "")
                rows = len(self._aggregate_query(session, result.dealer_code or result.customer_code or result.dealer_found)) if result.dealer_found else 0
            
            output = asdict(result)
            output.update({
                "rows_returned": rows,
                "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)
            })
            return {"success": result.exception is None, "diagnostic": output}
        except Exception as error:
            logger.exception("Dealer diagnostics failed")
            return {"success": False, "diagnostic": {"original_message": message, "any_exception": str(error)}}

    def _aggregate_query(self, session: Session, dealer: Optional[str] = None) -> list[Any]:
        """Aggregate query - identical to dn_analysis.py"""
        cache_key = dealer or "all"
        cached = self._aggregate_cache.get(cache_key)
        if cached is not None:
            return cached
        
        try:
            query = session.query(
                func.coalesce(DeliveryReport.customer_name, "Unknown").label("dealer_name"),
                func.coalesce(DeliveryReport.dealer_code, "Unknown").label("dealer_code"),
                func.coalesce(DeliveryReport.customer_code, "Unknown").label("customer_code"),
                func.max(DeliveryReport.ship_to_city).label("city"),
                func.max(DeliveryReport.delivery_location).label("delivery_location"),
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.division).label("division"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
                func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_dn"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("completed_dn"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)))).label("pgi_pending_dn"),
                func.count(distinct(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pod_pending_dn"),
                func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
                func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
                func.max(DeliveryReport.good_issue_date).label("latest_pgi_date"),
                func.max(DeliveryReport.pod_date).label("latest_pod_date"),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
                func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
            ).filter(DeliveryReport.customer_name.isnot(None))
            
            if dealer:
                query = query.filter(DealerRepository.dealer_filter(dealer))
            
            result = query.group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code
            ).all()
            
            self._aggregate_cache[cache_key] = result
            return result
        except Exception:
            return []

    # ============================================================
    # BLOCK 14: SERVICE METADATA AND HEALTH CHECKS
    # ============================================================

    def health_check(self) -> dict[str, Any]:
        """Health check with detailed status - identical to dn_analysis.py"""
        started = time.perf_counter()
        try:
            with self._session() as session:
                rows = session.query(func.count(DeliveryReport.id)).scalar() or 0
            return {
                "healthy": True,
                "service": self._service_name,
                "version": self._version,
                "database": "connected",
                "records": int(rows),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "timestamp": datetime.utcnow().isoformat(),
                "source": "PostgreSQL"
            }
        except Exception as error:
            logger.exception("Dealer analytics health check failed")
            return {
                "healthy": False,
                "service": self._service_name,
                "version": self._version,
                "database": "disconnected",
                "error": str(error),
                "timestamp": datetime.utcnow().isoformat(),
                "source": "PostgreSQL"
            }

    def validation_query(self) -> dict[str, Any]:
        """Validate database connectivity - identical to dn_analysis.py"""
        try:
            with self._session() as session:
                records = session.query(
                    func.count(distinct(func.coalesce(DeliveryReport.dealer_code, DeliveryReport.customer_code, DeliveryReport.customer_name)))
                ).scalar() or 0
            return {"success": True, "records": int(records), "error": None, "source": "PostgreSQL"}
        except Exception as error:
            return {"success": False, "records": 0, "error": str(error), "source": "PostgreSQL"}

    def get_service_metadata(self) -> dict[str, Any]:
        """Get service metadata - identical to dn_analysis.py"""
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": "DEGRADED" if self._initialization_errors else "READY",
            "source": "PostgreSQL",
            "source_of_truth": "PostgreSQL",
            "table": TABLE,
            "business_columns": list(BUSINESS_COLUMNS),
            "distance_provider": "OpenRouteService/Geopy/Haversine",
            "semantic_search": USE_SEMANTIC_SEARCH,
            "startup_time": self._startup_time,
            "initialization_errors": self._initialization_errors
        }


# ============================================================
# BLOCK 15: SERVICE SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()


def get_dealer_analytics_service() -> DealerAnalyticsService:
    """Get singleton instance of DealerAnalyticsService"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                try:
                    _service = DealerAnalyticsService()
                    logger.info(f"DealerAnalyticsService initialized (v{_service._version})")
                except Exception as e:
                    logger.exception("DealerAnalyticsService initialization failed")
                    _service = DealerAnalyticsService.__new__(DealerAnalyticsService)
                    _service._service_name = "dealer_analytics"
                    _service._version = "9.0.0-degraded"
                    _service._startup_time = datetime.utcnow().isoformat()
                    _service._initialization_errors = [f"Emergency mode: {str(e)}"]
                    _service._distance = DistanceService()
                    _service._search_engine = DealerSearchEngine()
                    _service._executor = ThreadPoolExecutor(max_workers=4)
                    _service._dashboard_cache = TTLCache(maxsize=4096, ttl=600)
                    _service._ranking_cache = TTLCache(maxsize=128, ttl=600)
                    _service._aggregate_cache = TTLCache(maxsize=1024, ttl=300)
                    _service._extended_cache = TTLCache(maxsize=4096, ttl=3600)
                    _service._lock = threading.RLock()
                    _service._last_diagnostic = {}
    return _service


# ============================================================
# BLOCK 16: EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "DealerDashboard",
    "DealerComparison",
    "DealerRanking",
    "DealerSearchResult",
    "DistanceAnalytics",
    "BusinessHealthStatus",
    "TrendType",
    "RankType",
    "WhatsappFormat",
    "DealerSearchEngine",
    "DealerAggregationEngine",
    "DistanceService",
    "get_dealer_analytics_service"
]
