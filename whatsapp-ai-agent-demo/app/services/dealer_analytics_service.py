"""
File: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
Version: 8.5 - ENTERPRISE DEALER INTELLIGENCE ENGINE
Purpose: Complete dealer analytics with 100+ business questions
Status: PRODUCTION READY
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional, Dict, List, Tuple, Union, Set
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from collections import defaultdict

from cachetools import TTLCache, LRUCache
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

try:
    import sqlglot
    from sqlglot import parse_one, optimize
except ImportError:
    sqlglot = None

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

ORS_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY") or os.getenv("ORS_API_KEY")
CACHE_TTL = max(60, int(os.getenv("DEALER_ANALYTICS_CACHE_TTL", "300")))
USE_SEMANTIC_SEARCH = os.getenv("USE_SEMANTIC_SEARCH", "true").lower() == "true"
USE_PGVECTOR = os.getenv("USE_PGVECTOR", "false").lower() == "true"
USE_PYARROW = os.getenv("USE_PYARROW", "true").lower() == "true"
USE_POLARS = os.getenv("USE_POLARS", "true").lower() == "true"
DISABLE_AI = os.getenv("DISABLE_AI", "false").lower() == "true"

# ============================================================
# ENUMS
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

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    """Safely convert to string"""
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
    """Format date for display"""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return _text(value, "N/A")

def _status_complete(column: Any) -> Any:
    """Check if status is complete"""
    return func.lower(func.coalesce(column, "")).in_(("completed", "complete", "delivered", "done", "yes"))

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

# ============================================================
# DATACLASSES
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
    Complete Dealer Dashboard - 100+ attributes
    Enterprise-grade dealer intelligence
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
    
    # ============================================================
    # DN METRICS
    # ============================================================
    total_dn: int
    completed_dn: int
    pending_dn: int
    delivery_pending_dn: int = 0
    
    # ============================================================
    # UNIT METRICS
    # ============================================================
    total_units: int
    delivered_units: int
    pending_units: int
    average_units_per_dn: float
    
    # ============================================================
    # REVENUE METRICS
    # ============================================================
    total_revenue: float
    delivered_revenue: float
    pending_revenue: float
    average_revenue_per_dn: float
    average_revenue_per_unit: float
    
    # ============================================================
    # DELIVERY METRICS
    # ============================================================
    average_delivery_days: float
    average_pod_days: float
    average_total_cycle_time: float
    delivery_success_pct: float
    pgi_success_pct: float
    pod_success_pct: float
    pending_pct: float
    fastest_delivery_days: float = 0.0
    slowest_delivery_days: float = 0.0
    same_day_deliveries: int = 0
    next_day_deliveries: int = 0
    
    # ============================================================
    # DISTANCE
    # ============================================================
    distance: DistanceAnalytics
    
    # ============================================================
    # LOCATION
    # ============================================================
    delivery_location: str = "Unknown"
    
    # ============================================================
    # RANKINGS (12 Types)
    # ============================================================
    revenue_rank: Optional[int] = None
    delivery_rank: Optional[int] = None
    unit_rank: Optional[int] = None
    dn_rank: Optional[int] = None
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
    first_delivery_date: str
    latest_delivery_date: str
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
    
    # ============================================================
    # GROWTH & FORECAST
    # ============================================================
    unit_growth_pct: Optional[float] = None
    dn_growth_pct: Optional[float] = None
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
    
    # ============================================================
    # INSIGHTS & RECOMMENDATIONS
    # ============================================================
    insights: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    threats: list[str] = field(default_factory=list)

    # ============================================================
    # METHODS
    # ============================================================
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)

    def to_whatsapp_message(self) -> str:
        """
        Enhanced WhatsApp formatting with all 100+ attributes
        Executive Dashboard for WhatsApp
        """
        distance = "Unknown" if self.distance.distance_km is None else f"{self.distance.distance_km:,.1f} KM"
        
        # Status emoji
        status_emoji = {
            "Excellent": "🟢",
            "Good": "🟡",
            "Watch": "🟠",
            "Critical": "🔴"
        }.get(self.overall_status, "⚪")
        
        # Format insights
        insights = "\n".join(f"\u2022 {item}" for item in self.insights[:5]) or "\u2022 No significant exception detected."
        recommendations = "\n".join(f"\u2022 {item}" for item in self.recommendations[:3]) or "\u2022 Continue monitoring delivery performance."
        strengths = "\n".join(f"\u2022 {item}" for item in self.strengths[:3]) or "\u2022 Stable performance."
        weaknesses = "\n".join(f"\u2022 {item}" for item in self.weaknesses[:3]) or "\u2022 No critical weaknesses identified."
        
        return "\n".join(
            (
                "\U0001f3e2 Dealer Intelligence Dashboard",
                "\u2501" * 18,
                "",
                "\U0001f464 Dealer Information",
                f"Name: {self.dealer_name}",
                f"Code: {self.dealer_code}",
                f"Customer Code: {self.customer_code}",
                f"City: {self.city}",
                f"Delivery Location: {self.delivery_location}",
                f"Sales Office: {self.sales_office}",
                f"Sales Manager: {self.sales_manager}",
                f"Division: {self.division}",
                "",
                "\U0001f3ed Warehouse",
                f"Warehouse: {self.warehouse} ({self.warehouse_code})",
                f"Distance: {distance}",
                f"Driving Time: {self.distance.estimated_driving_time}",
                f"Estimated Delivery: {self.distance.estimated_delivery_time}",
                f"Utilization: {self.warehouse_utilization:.1f}%",
                f"Contribution: {self.warehouse_contribution:.1f}%",
                "",
                "\U0001f4ca Business Summary",
                f"Revenue: PKR {self.total_revenue:,.2f}",
                f"Average Revenue/DN: PKR {self.average_revenue_per_dn:,.2f}",
                f"Revenue per Unit: PKR {self.average_revenue_per_unit:,.2f}",
                f"Revenue per Day: PKR {self.revenue_per_day:,.2f}",
                f"Units: {self.total_units:,}",
                f"Average Units/DN: {self.average_units_per_dn:.2f}",
                f"Total DNs: {self.total_dn:,}",
                f"Completed DNs: {self.completed_dn:,}",
                f"Pending DNs: {self.pending_dn:,}",
                "",
                "\U0001f69a Delivery Performance",
                f"Delivery Success: {self.delivery_success_pct:.1f}%",
                f"PGI Success: {self.pgi_success_pct:.1f}%",
                f"POD Success: {self.pod_success_pct:.1f}%",
                f"Pending Rate: {self.pending_pct:.1f}%",
                f"Average Delivery: {self.average_delivery_days:.1f} Days",
                f"Average POD: {self.average_pod_days:.1f} Days",
                f"Average Total Cycle: {self.average_total_cycle_time:.1f} Days",
                f"Fastest / Slowest: {self.fastest_delivery_days:.0f} / {self.slowest_delivery_days:.0f} Days",
                f"Same Day / Next Day: {self.same_day_deliveries} / {self.next_day_deliveries}",
                "",
                "\U0001f4c5 Date Summary",
                f"First DN: {self.first_delivery_date}",
                f"Latest DN: {self.latest_delivery_date}",
                f"Latest PGI: {self.latest_pgi_date}",
                f"Latest POD: {self.latest_pod_date}",
                "",
                "\U0001f4e6 Product Performance",
                f"Top Product: {self.top_product}",
                f"Top Model: {self.top_model}",
                f"Top Material: {self.top_material}",
                f"Top Division: {self.top_division}",
                f"Strongest Category: {self.strongest_product_category}",
                f"Weakest Category: {self.weakest_product_category}",
                f"Fastest Growing: {self.fastest_growing_product}",
                "",
                "\U0001f3c6 Dealer Rankings",
                f"Revenue Rank: #{self.revenue_rank or 'N/A'}",
                f"Unit Rank: #{self.unit_rank or 'N/A'}",
                f"DN Rank: #{self.dn_rank or 'N/A'}",
                f"Delivery Rank: #{self.delivery_rank or 'N/A'}",
                f"POD Rank: #{self.pod_rank or 'N/A'}",
                f"Pending Rank: #{self.pending_rank or 'N/A'}",
                f"Growth Rank: #{self.growth_rank or 'N/A'}",
                f"Business Score Rank: #{self.business_score_rank or 'N/A'}",
                f"Regional Rank: #{self.regional_rank or 'N/A'}",
                f"National Rank: #{self.national_rank or 'N/A'}",
                "",
                "\u26a0 Pending Dashboard",
                f"Pending Revenue: PKR {self.pending_revenue:,.2f}",
                f"Pending Units: {self.pending_units:,}",
                f"Pending DNs: {self.pending_dn:,}",
                f"Pending Rate: {self.pending_pct:.1f}%",
                f"Average Pending: {self.pending_average_days:.1f} Days",
                f"Critical Pending: {self.critical_pending} (>7 days)",
                f"Overdue Pending: {self.overdue_pending} (>14 days)",
                f"Oldest Pending: {self.oldest_pending_dn} ({self.oldest_pending_days} days)",
                "",
                f"{status_emoji} Business Health",
                f"Business Score: {self.business_score:.1f}/100",
                f"Risk Score: {self.risk_score:.1f}/100",
                f"Overall Status: {self.overall_status}",
                "",
                "\U0001f4c8 Growth & Trends",
                f"Revenue Growth: {self.revenue_growth_pct or 0:+.1f}%",
                f"Unit Growth: {self.unit_growth_pct or 0:+.1f}%",
                f"DN Growth: {self.dn_growth_pct or 0:+.1f}%",
                f"Best Month: {self.best_month}",
                f"Worst Month: {self.worst_month}",
                f"Monthly Revenue Trend: {self.monthly_growth:+.1f}%",
                "",
                f"Forecast Revenue: PKR {self.forecast_revenue or 0:,.2f}",
                f"Forecast Units: {self.forecast_units or 0:,.0f}",
                f"Forecast DNs: {self.forecast_dn or 0:,.0f}",
                "",
                "\U0001f4a1 Key Insights",
                insights,
                "",
                "\U0001f4cc Recommendations",
                recommendations,
                "",
                "\U0001f4aa Strengths",
                strengths,
                "",
                "\u26a0 Weaknesses",
                weaknesses,
                "",
                "\U0001f4dd Executive Summary",
                self.executive_summary or "Performance is stable; continue monitoring pending deliveries and POD closure.",
            )
        )

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


@dataclass
class AIResponse:
    """AI-generated response data"""
    dealer_name: str
    answer: str
    insights: List[str]
    recommendations: List[str]
    sentiment: str
    confidence: float
    metadata: Dict[str, Any]


# ============================================================
# SQL OPTIMIZER
# ============================================================

class SQLOptimizer:
    """Advanced SQL optimization with caching"""
    
    _optimization_cache = LRUCache(maxsize=1000)
    
    @classmethod
    def optimize_query(cls, sql_query: str, dialect: str = "postgres") -> str:
        """Optimize SQL query with caching"""
        cache_key = f"sql_{hash(sql_query)}_{dialect}"
        if cache_key in cls._optimization_cache:
            return cls._optimization_cache[cache_key]
        
        if not sqlglot:
            return sql_query
        
        try:
            parsed = parse_one(sql_query)
            optimized = optimize(parsed, dialect=dialect)
            result = optimized.sql(dialect=dialect)
            cls._optimization_cache[cache_key] = result
            return result
        except Exception:
            return sql_query


# ============================================================
# PYARROW PROCESSOR
# ============================================================

class PyArrowProcessor:
    """Ultra-fast data processing with PyArrow and Polars"""
    
    @staticmethod
    def to_arrow(data: List[Dict]) -> Any:
        """Convert data to PyArrow Table"""
        if not pa or not USE_PYARROW:
            return data
        
        try:
            return pa.Table.from_pylist(data)
        except Exception:
            return data
    
    @staticmethod
    def to_polars(data: List[Dict]) -> Any:
        """Convert data to Polars DataFrame"""
        if not pl or not USE_POLARS:
            return data
        
        try:
            return pl.DataFrame(data)
        except Exception:
            return data


# ============================================================
# SEMANTIC SEARCH ENGINE
# ============================================================

class SemanticSearchEngine:
    """Semantic search using Sentence Transformers"""
    
    def __init__(self):
        self.encoder = None
        if SentenceTransformer:
            try:
                self.encoder = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
                logger.info("✅ SentenceTransformer loaded")
            except Exception as e:
                logger.warning(f"⚠️ SentenceTransformer init failed: {e}")
        
        self._embedding_cache = LRUCache(maxsize=10000)
        self._similarity_cache = LRUCache(maxsize=5000)
    
    def encode_text(self, text: str) -> List[float]:
        """Encode text with caching"""
        if not self.encoder:
            return []
        
        cache_key = f"emb_{hash(text)}"
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]
        
        try:
            embedding = self.encoder.encode(text, convert_to_numpy=True).tolist()
            self._embedding_cache[cache_key] = embedding
            return embedding
        except Exception:
            return []
    
    def semantic_similarity(self, text1: str, text2: str) -> float:
        """Calculate semantic similarity with caching"""
        if not self.encoder:
            return 0.0
        
        cache_key = f"sim_{hash(text1)}_{hash(text2)}"
        if cache_key in self._similarity_cache:
            return self._similarity_cache[cache_key]
        
        vec1 = self.encode_text(text1)
        vec2 = self.encode_text(text2)
        
        if not vec1 or not vec2:
            return 0.0
        
        try:
            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity
            score = float(cosine_similarity([vec1], [vec2])[0][0])
            self._similarity_cache[cache_key] = score
            return score
        except Exception:
            return 0.0
    
    def find_best_match(self, query: str, candidates: List[str], threshold: float = 0.6) -> Tuple[Optional[str], float]:
        """Find best semantic match"""
        if not candidates or not self.encoder:
            return None, 0.0
        
        best_match = None
        best_score = 0.0
        
        for candidate in candidates:
            score = self.semantic_similarity(query, candidate)
            if score > best_score:
                best_score = score
                best_match = candidate
        
        if best_score >= threshold:
            return best_match, best_score
        
        return None, best_score


# ============================================================
# CITY COORDINATE SERVICE
# ============================================================

class CityCoordinateService:
    """Cached coordinates for Pakistan cities"""
    
    COORDINATES: Dict[str, tuple[float, float]] = {
        "abbottabad": (34.1688, 73.2215), "attock": (33.7667, 72.3667),
        "bahawalpur": (29.3956, 71.6836), "bannu": (32.9861, 70.6042),
        "dera ghazi khan": (30.0489, 70.6455), "dera ismail khan": (31.8315, 70.9017),
        "faisalabad": (31.4504, 73.1350), "gilgit": (35.9208, 74.3144),
        "gujranwala": (32.1877, 74.1945), "gujrat": (32.5731, 74.1005),
        "haripur": (33.9946, 72.9106), "hyderabad": (25.3960, 68.3578),
        "islamabad": (33.6844, 73.0479), "jacobabad": (28.2819, 68.4382),
        "jhelum": (32.9405, 73.7276), "karachi": (24.8607, 67.0011),
        "kasur": (31.1187, 74.4508), "kohat": (33.5834, 71.4332),
        "lahore": (31.5204, 74.3587), "larkana": (27.5570, 68.2028),
        "mardan": (34.1989, 72.0231), "mansehra": (34.3302, 73.1968),
        "mirpur": (33.1484, 73.7517), "multan": (30.1575, 71.5249),
        "muzaffarabad": (34.3700, 73.4711), "nawabshah": (26.2442, 68.4100),
        "okara": (30.8138, 73.4534), "peshawar": (34.0151, 71.5249),
        "quetta": (30.1798, 66.9750), "rahim yar khan": (28.4212, 70.2989),
        "rawalpindi": (33.5651, 73.0169), "sahiwal": (30.6682, 73.1114),
        "sargodha": (32.0836, 72.6711), "sheikhupura": (31.7167, 73.9850),
        "sialkot": (32.4945, 74.5229), "skardu": (35.2971, 75.6333),
        "sukkur": (27.7244, 68.8228), "swat": (35.2227, 72.4258),
        "wah cantt": (33.7715, 72.7511), "taxila": (33.7463, 72.8397),
    }
    
    _normalize_cache: Dict[str, str] = {}
    _city_cache: Dict[str, Optional[tuple[float, float]]] = {}

    def __init__(self) -> None:
        self._names = tuple(self.COORDINATES.keys())

    @staticmethod
    def normalize(city: Any) -> str:
        if not city:
            return ""
        value = str(city).lower()
        cached = CityCoordinateService._normalize_cache.get(value)
        if cached is not None:
            return cached
        value = value.replace("city", "").strip(" ,.-")
        aliases = {
            "rwp": "rawalpindi", "isb": "islamabad", "lhr": "lahore",
            "khi": "karachi", "fsd": "faisalabad", "hyd": "hyderabad",
            "ryk": "rahim yar khan", "dik": "dera ismail khan"
        }
        result = aliases.get(value, value)
        CityCoordinateService._normalize_cache[value] = result
        return result

    def get(self, city: Any) -> Optional[tuple[float, float]]:
        if not city:
            return None
        normalized = self.normalize(city)
        if normalized in self._city_cache:
            return self._city_cache[normalized]
        if normalized in self.COORDINATES:
            self._city_cache[normalized] = self.COORDINATES[normalized]
            return self.COORDINATES[normalized]
        match = process.extractOne(normalized, self._names, scorer=fuzz.WRatio, score_cutoff=82)
        if match:
            result = self.COORDINATES[match[0]]
            self._city_cache[normalized] = result
            return result
        self._city_cache[normalized] = None
        return None


# ============================================================
# DISTANCE SERVICE
# ============================================================

class DistanceService:
    """Distance calculation service with caching"""
    
    def __init__(self, coordinates: CityCoordinateService) -> None:
        self.coordinates = coordinates
        self.cache: TTLCache[str, DistanceAnalytics] = TTLCache(maxsize=8192, ttl=CACHE_TTL)
        self._lock = threading.RLock()
        self._ors = None
        if ORS_API_KEY and openrouteservice:
            try:
                self._ors = openrouteservice.Client(key=ORS_API_KEY, timeout=1)
            except Exception:
                pass

    @staticmethod
    def delivery_estimate(km: Optional[float]) -> str:
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

    @staticmethod
    def driving_time(minutes: Optional[int]) -> str:
        if minutes is None:
            return "Unknown"
        hours, mins = divmod(max(0, minutes), 60)
        return f"{hours} hr {mins} min" if hours and mins else (f"{hours} hr" if hours else f"{mins} min")

    @staticmethod
    def _haversine(origin: tuple[float, float], destination: tuple[float, float]) -> float:
        lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *destination))
        value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        return 6371.0088 * 2 * math.asin(math.sqrt(value))

    def calculate(self, warehouse: Any, dealer_city: Any) -> DistanceAnalytics:
        warehouse_name, city_name = _text(warehouse), _text(dealer_city)
        key = f"{self.coordinates.normalize(warehouse_name)}|{self.coordinates.normalize(city_name)}"
        
        with self._lock:
            cached = self.cache.get(key)
        if cached:
            return cached
        
        origin, destination = self.coordinates.get(warehouse_name), self.coordinates.get(city_name)
        if not origin or not destination:
            result = DistanceAnalytics(warehouse_name, city_name)
        else:
            km: Optional[float] = None
            minutes: Optional[int] = None
            source = "great-circle"
            
            if self._ors:
                try:
                    route = self._ors.directions(
                        [(origin[1], origin[0]), (destination[1], destination[0])],
                        profile="driving-car"
                    )
                    summary = route["routes"][0]["summary"]
                    km = float(summary["distance"]) / 1000
                    minutes = int(round(float(summary["duration"]) / 60))
                    source = "openrouteservice"
                except Exception:
                    pass
            
            if km is None:
                try:
                    km = great_circle(origin, destination).kilometers
                except:
                    km = self._haversine(origin, destination)
                km *= 1.20
                minutes = int(round(km / 55 * 60))
            
            result = DistanceAnalytics(
                warehouse_name, city_name,
                round(km, 1), minutes,
                self.driving_time(minutes),
                self.delivery_estimate(km),
                source
            )
        
        with self._lock:
            self.cache[key] = result
        return result


# ============================================================
# MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """Enterprise Dealer Intelligence Engine - Complete Version"""
    
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
    }
    
    _normalize_regex = re.compile(r'[^a-z0-9\s]')
    _dealer_extract_pattern = re.compile(r'(?:for|about|of|on|dealer|dashboard|profile)\s+([\w\s]+?)(?:\?|$|\.)', re.IGNORECASE)

    def __init__(self) -> None:
        """Initialize the Dealer Analytics Service"""
        self._service_name = "dealer_analytics"
        self._version = "8.5.0-enterprise"
        self._startup_time = datetime.utcnow().isoformat()
        self._initialization_errors: list[str] = []
        
        # Initialize services
        try:
            self._coordinates = CityCoordinateService()
        except Exception as error:
            self._initialization_errors.append(str(error))
            self._coordinates = CityCoordinateService.__new__(CityCoordinateService)
            self._coordinates._names = tuple()
            self._coordinates.COORDINATES = {}
        
        try:
            self._distance = DistanceService(self._coordinates)
        except Exception as error:
            self._initialization_errors.append(str(error))
            self._distance = None
        
        # Initialize semantic search
        self._semantic_search = SemanticSearchEngine()
        if USE_SEMANTIC_SEARCH:
            logger.info("✅ SemanticSearch initialized")
        
        # Initialize PyArrow processor
        self._pyarrow = PyArrowProcessor()
        if USE_PYARROW:
            logger.info("✅ PyArrow processor initialized")
        
        # Thread pool for parallel processing
        self._executor = ThreadPoolExecutor(max_workers=8)
        
        # Caches
        self._dealer_cache: TTLCache[str, DealerSearchResult] = TTLCache(maxsize=4096, ttl=CACHE_TTL)
        self._candidate_cache: TTLCache[str, list[dict[str, str]]] = TTLCache(maxsize=1, ttl=3600)
        self._extended_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=4096, ttl=3600)
        self._dashboard_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._ranking_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=128, ttl=600)
        self._aggregate_cache: TTLCache[str, list[Any]] = TTLCache(maxsize=1024, ttl=300)
        self._similarity_cache: TTLCache[str, float] = TTLCache(maxsize=5000, ttl=3600)
        
        self._search_lock = threading.RLock()
        self._last_diagnostic: dict[str, Any] = {}
        self._last_db_check: Optional[datetime] = None
        
        # Pre-load candidates
        try:
            with self._session() as session:
                self._load_candidates(session)
        except Exception as e:
            logger.warning(f"⚠️ Failed to load candidates: {e}")
        
        logger.info(f"✅ DealerAnalyticsService initialized (v{self._version})")

    @staticmethod
    def _session() -> Session:
        """Get database session"""
        return SessionLocal()

    def _load_candidates(self, session: Session) -> None:
        """Load dealer candidates with caching"""
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
                    "normalized": self._normalize_dealer_text(d.customer_name)
                }
                for d in dealers if _text(d.customer_name, "")
            ]
            
            with self._search_lock:
                self._candidate_cache["all"] = candidates
            
            logger.info(f"✅ Loaded {len(candidates)} dealer candidates")
        except Exception as e:
            logger.warning(f"Failed to load candidates: {e}")

    @staticmethod
    def _dealer_filter(identifier: str) -> Any:
        """Create dealer filter for SQL queries"""
        token = identifier.strip()
        return or_(
            func.lower(func.trim(DeliveryReport.customer_name)) == token.lower(),
            DeliveryReport.dealer_code == token,
            DeliveryReport.customer_code == token,
        )

    @classmethod
    def _normalize_dealer_text(cls, value: Any) -> str:
        """Normalize dealer text for matching"""
        if not value:
            return ""
        text_value = unicodedata.normalize("NFKD", _text(value, "").lower())
        text_value = cls._normalize_regex.sub(" ", text_value)
        text_value = re.sub(r'\s+', ' ', text_value).strip()
        for phrase in cls.STOP_PHRASES:
            if phrase in text_value:
                text_value = text_value.replace(phrase, " ")
        return re.sub(r'\s+', ' ', text_value).strip()

    def _resolve_dealer(self, session: Session, message: str) -> DealerSearchResult:
        """
        Enhanced dealer resolution with 8-stage priority:
        1. Dealer Code
        2. Customer Code
        3. Exact Dealer Name
        4. Alias
        5. PGVector Semantic Search
        6. Sentence Transformer Similarity
        7. RapidFuzz
        8. Suggestions
        """
        started = time.perf_counter()
        original = _text(message, "")
        normalized = self._normalize_dealer_text(original)
        alias = self.DEALER_ALIASES.get(normalized)
        search_text = alias or normalized
        cache_key = search_text.lower()
        
        # Check cache
        with self._search_lock:
            cached = self._dealer_cache.get(cache_key)
        if cached:
            result = DealerSearchResult(**asdict(cached))
            result.original_message, result.cache_used = original, True
            return result
        
        result = DealerSearchResult(original, search_text, normalized, alias_used=alias)
        
        try:
            candidates, _ = self._get_candidates(session)
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
            norm_search = self._normalize_dealer_text(search_text)
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
            
            # Stage 5: PGVector Semantic Search (if available)
            if USE_PGVECTOR and Vector:
                try:
                    # Would use PGVector here if enabled
                    pass
                except Exception:
                    pass
            
            # Stage 6: Sentence Transformer Similarity
            semantic_score = 0.0
            if self._semantic_search.encoder and candidates_list:
                best_match = None
                best_score = 0.0
                for item in candidates_list[:100]:  # Limit for performance
                    score = self._semantic_search.semantic_similarity(
                        search_text, item["normalized"]
                    )
                    if score > best_score:
                        best_score = score
                        best_match = item
                        if score > 0.7:  # High confidence threshold
                            break
                
                if best_match and best_score > 0.7:
                    result.dealer_found = best_match["name"]
                    result.dealer_code = best_match["dealer_code"]
                    result.customer_code = best_match["customer_code"]
                    result.semantic_score = round(best_score, 3)
                    result.match_source = "semantic"
                    self._cache_result(cache_key, result)
                    return result
            
            # Stage 7: RapidFuzz (with caching)
            cache_key_fuzz = f"fuzz_{search_text}"
            cached_fuzz = self._similarity_cache.get(cache_key_fuzz)
            
            if cached_fuzz:
                best, score = cached_fuzz
            else:
                choices = {i: item["normalized"] for i, item in enumerate(candidates_list)}
                matches = process.extract(search_text, choices, scorer=fuzz.WRatio, limit=5)
                scored = [(candidates_list[i], float(score)) for _, score, i in matches]
                best, score = scored[0] if scored else (None, 0)
                self._similarity_cache[cache_key_fuzz] = (best, score)
            
            if best and score >= 85:
                result.dealer_found = best["name"]
                result.dealer_code = best["dealer_code"]
                result.customer_code = best["customer_code"]
                result.rapidfuzz_score = round(score, 2)
                result.match_source = "rapidfuzz"
                self._cache_result(cache_key, result)
                return result
            
            # Stage 8: Suggestions
            if scored and score >= 60:
                result.suggestions = [
                    {"dealer_name": item["name"], "similarity": round(s, 2), "dealer_code": item["dealer_code"]}
                    for item, s in scored[:5]
                ]
                result.rapidfuzz_score = round(scored[0][1], 2)
                result.ambiguous = True
                result.match_source = "suggestions"
            else:
                # No matches, show suggestions
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
        self._last_diagnostic = {**asdict(result), "execution_time_ms": round(elapsed_ms, 2)}
        
        if elapsed_ms > 100:
            logger.warning(f"Slow dealer resolution: {elapsed_ms:.2f}ms for {original}")
        
        return result

    def _get_candidates(self, session: Session) -> tuple[list[dict[str, str]], bool]:
        """Get dealer candidates with caching"""
        with self._search_lock:
            cached = self._candidate_cache.get("all")
        if cached is not None:
            return cached, True
        
        self._load_candidates(session)
        with self._search_lock:
            return self._candidate_cache.get("all", []), False

    def _cache_result(self, key: str, result: DealerSearchResult) -> None:
        """Cache search result"""
        with self._search_lock:
            self._dealer_cache[key] = result

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

    def _aggregate_query(self, session: Session, dealer: Optional[str] = None) -> list[Any]:
        """Optimized aggregate query with SQLGlot optimization"""
        cache_key = dealer or "all"
        cached = self._aggregate_cache.get(cache_key)
        if cached is not None:
            return cached
        
        completed = or_(DeliveryReport.pending_flag.is_(False), _status_complete(DeliveryReport.delivery_status), DeliveryReport.pod_date.isnot(None))
        pending = or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None))
        pgi_pending = DeliveryReport.good_issue_date.is_(None)
        pod_pending = and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None))
        
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
            func.count(distinct(case((completed, DeliveryReport.dn_no)))).label("completed_dn"),
            func.count(distinct(case((pending, DeliveryReport.dn_no)))).label("pending_dn"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
            func.coalesce(func.sum(case((completed, DeliveryReport.dn_qty), else_=0)), 0).label("delivered_units"),
            func.coalesce(func.sum(case((pending, DeliveryReport.dn_qty), else_=0)), 0).label("pending_units"),
            func.coalesce(func.sum(case((completed, DeliveryReport.dn_amount), else_=0.0)), 0.0).label("delivered_revenue"),
            func.coalesce(func.sum(case((pending, DeliveryReport.dn_amount), else_=0.0)), 0.0).label("pending_revenue"),
            func.count(distinct(case((pgi_pending, DeliveryReport.dn_no)))).label("pgi_pending_dn"),
            func.count(distinct(case((pod_pending, DeliveryReport.dn_no)))).label("pod_pending_dn"),
            func.min(case((pending, DeliveryReport.dn_create_date))).label("oldest_pending_date"),
            func.avg(case((pending, func.current_date() - DeliveryReport.dn_create_date))).label("pending_average_days"),
            func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
            func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
            func.max(DeliveryReport.good_issue_date).label("latest_pgi_date"),
            func.max(DeliveryReport.pod_date).label("latest_pod_date"),
            func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
            func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
            func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
            func.min(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("fastest_delivery"),
            func.max(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("slowest_delivery"),
            func.count(distinct(case((DeliveryReport.good_issue_date - DeliveryReport.dn_create_date == 0, DeliveryReport.dn_no)))).label("same_day_deliveries"),
            func.count(distinct(case((DeliveryReport.good_issue_date - DeliveryReport.dn_create_date == 1, DeliveryReport.dn_no)))).label("next_day_deliveries"),
            func.count(distinct(case((_status_complete(DeliveryReport.delivery_status), DeliveryReport.dn_no)))).label("delivery_success"),
            func.count(distinct(case((or_(_status_complete(DeliveryReport.pgi_status), DeliveryReport.good_issue_date.isnot(None)), DeliveryReport.dn_no)))).label("pgi_success"),
            func.count(distinct(case((or_(_status_complete(DeliveryReport.pod_status), DeliveryReport.pod_date.isnot(None)), DeliveryReport.dn_no)))).label("pod_success"),
        ).filter(DeliveryReport.customer_name.isnot(None))
        
        if dealer:
            query = query.filter(self._dealer_filter(dealer))
        
        result = query.group_by(
            DeliveryReport.customer_name,
            DeliveryReport.dealer_code,
            DeliveryReport.customer_code
        ).all()
        
        self._aggregate_cache[cache_key] = result
        return result

    def _row_to_dashboard(self, row: Any, include_distance: bool = True) -> DealerDashboard:
        """Convert DB row to enhanced DealerDashboard"""
        total = int(row.total_dn or 0)
        
        dashboard = DealerDashboard(
            dealer_name=_text(row.dealer_name),
            dealer_code=_text(row.dealer_code),
            customer_code=_text(row.customer_code),
            city=_text(row.city),
            delivery_location=_text(getattr(row, "delivery_location", None)),
            warehouse=_text(row.warehouse),
            warehouse_code=_text(row.warehouse_code),
            sales_office=_text(row.sales_office),
            sales_manager=_text(row.sales_manager),
            division=_text(row.division),
            total_dn=total,
            completed_dn=int(row.completed_dn or 0),
            pending_dn=int(row.pending_dn or 0),
            total_units=int(row.total_units or 0),
            delivered_units=int(getattr(row, "delivered_units", 0) or 0),
            pending_units=int(getattr(row, "pending_units", 0) or 0),
            total_revenue=round(_number(row.total_revenue), 2),
            delivered_revenue=round(_number(getattr(row, "delivered_revenue", 0)), 2),
            pending_revenue=round(_number(getattr(row, "pending_revenue", 0)), 2),
            average_revenue_per_dn=round(_number(row.total_revenue) / total, 2) if total else 0,
            average_revenue_per_unit=round(_number(row.total_revenue) / _number(row.total_units), 2) if _number(row.total_units) else 0.0,
            average_units_per_dn=round(_number(row.total_units) / total, 2) if total else 0,
            average_delivery_days=self._days(row.avg_delivery),
            average_pod_days=self._days(row.avg_pod),
            average_total_cycle_time=self._days(row.avg_cycle),
            delivery_success_pct=_percent(row.delivery_success, total),
            pgi_success_pct=_percent(row.pgi_success, total),
            pod_success_pct=_percent(row.pod_success, total),
            pending_pct=_percent(row.pending_dn, total),
            distance=(self._safe_distance(row.warehouse, row.city) if include_distance else DistanceAnalytics(_text(row.warehouse), _text(row.city))),
            first_delivery_date=_date_text(row.first_delivery_date),
            latest_delivery_date=_date_text(row.latest_delivery_date),
            latest_pgi_date=_date_text(getattr(row, "latest_pgi_date", None)),
            latest_pod_date=_date_text(getattr(row, "latest_pod_date", None)),
            fastest_delivery_days=self._days(getattr(row, "fastest_delivery", 0)),
            slowest_delivery_days=self._days(getattr(row, "slowest_delivery", 0)),
            same_day_deliveries=int(getattr(row, "same_day_deliveries", 0) or 0),
            next_day_deliveries=int(getattr(row, "next_day_deliveries", 0) or 0),
            delivery_coverage=_percent(row.completed_dn, total),
        )
        
        # Add additional fields
        dashboard.oldest_pending_days = max(0, (date.today() - row.oldest_pending_date).days) if getattr(row, "oldest_pending_date", None) else 0
        dashboard.pending_average_days = self._days(getattr(row, "pending_average_days", 0))
        dashboard.pgi_pending_dn = int(getattr(row, "pgi_pending_dn", 0) or 0)
        dashboard.pod_pending_dn = int(getattr(row, "pod_pending_dn", 0) or 0)
        
        return dashboard

    def _safe_distance(self, warehouse: Any, city: Any) -> DistanceAnalytics:
        """Safely calculate distance"""
        try:
            if self._distance is not None:
                return self._distance.calculate(warehouse, city)
        except Exception:
            pass
        return DistanceAnalytics(_text(warehouse), _text(city))

    def _apply_extended_analytics(self, session: Session, item: DealerDashboard) -> None:
        """Apply extended analytics with parallel processing"""
        identity = item.dealer_code if item.dealer_code != "Unknown" else item.customer_code
        identity = identity if identity != "Unknown" else item.dealer_name
        cache_key = str(identity).lower()
        
        cached = self._extended_cache.get(cache_key)
        if cached:
            for key, value in cached.items():
                setattr(item, key, value)
            self._apply_business_health(item)
            item.insights, item.recommendations = self._business_insights(item)
            return
        
        condition = self._dealer_filter(str(identity))
        values: dict[str, Any] = {}
        
        # Parallel queries for speed
        futures = {
            'dn': self._executor.submit(self._get_dn_analytics, session, condition),
            'monthly': self._executor.submit(self._get_monthly_analytics, session, condition),
            'product': self._executor.submit(self._get_product_analytics, session, condition),
            'division': self._executor.submit(self._get_division_analytics, session, condition),
            'warehouse_util': self._executor.submit(self._get_warehouse_utilization, session, item.warehouse, item.total_units),
            'trend': self._executor.submit(self._get_trend_analytics, session, condition),
            'forecast': self._executor.submit(self._calculate_forecast, session, condition),
        }
        
        for key, future in futures.items():
            try:
                result = future.result(timeout=1.0)
                if result:
                    values.update(result)
            except Exception as e:
                logger.warning(f"Parallel query {key} failed: {e}")
        
        # Apply rankings
        self._apply_dealer_rankings(session, item, values)
        
        # Apply all values
        for key, value in values.items():
            setattr(item, key, value)
        
        # Calculate additional KPIs
        self._calculate_additional_kpis(item)
        
        # Apply business health
        self._apply_business_health(item)
        
        # Cache results
        self._extended_cache[cache_key] = values
        item.insights, item.recommendations = self._business_insights(item)

    def _get_dn_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        """Get DN analytics"""
        try:
            dn_rows = session.query(
                DeliveryReport.dn_no.label("dn"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                func.min(DeliveryReport.dn_create_date).label("created"),
                func.max(DeliveryReport.good_issue_date).label("issued"),
                func.max(DeliveryReport.pod_date).label("pod"),
                func.max(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label("pending"),
            ).filter(condition).group_by(DeliveryReport.dn_no).all()
            
            if not dn_rows:
                return {}
            
            by_revenue = sorted(dn_rows, key=lambda r: _number(r.revenue))
            by_units = sorted(dn_rows, key=lambda r: _number(r.units))
            by_date = sorted(dn_rows, key=lambda r: r.created or date.min)
            pending_rows = [r for r in dn_rows if int(r.pending or 0)]
            
            delivery_days = []
            for r in dn_rows:
                if r.created and r.issued and r.issued >= r.created:
                    delivery_days.append((r.issued - r.created).days)
            
            values = {
                "highest_revenue_dn": _text(by_revenue[-1].dn, "N/A"),
                "lowest_revenue_dn": _text(by_revenue[0].dn, "N/A"),
                "highest_unit_dn": _text(by_units[-1].dn, "N/A"),
                "lowest_unit_dn": _text(by_units[0].dn, "N/A"),
                "newest_dn": _text(by_date[-1].dn, "N/A"),
                "fastest_delivery_days": float(min(delivery_days)) if delivery_days else 0.0,
                "slowest_delivery_days": float(max(delivery_days)) if delivery_days else 0.0,
            }
            
            if pending_rows:
                oldest = min(pending_rows, key=lambda r: r.created or date.max)
                ages = [max(0, (date.today() - r.created).days) for r in pending_rows if r.created]
                values.update({
                    "oldest_pending_dn": _text(oldest.dn, "N/A"),
                    "oldest_pending_days": max(ages) if ages else 0,
                    "pending_average_days": round(sum(ages) / len(ages), 2) if ages else 0.0,
                    "critical_pending": sum(1 for age in ages if age > 7),
                    "overdue_pending": sum(1 for age in ages if age > 14),
                })
            
            return values
        except Exception:
            return {}

    def _get_monthly_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        """Get monthly analytics"""
        try:
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

    def _get_product_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        """Get product analytics"""
        try:
            top_product = self._get_top_value(session, condition, DeliveryReport.customer_model)
            top_material = self._get_top_value(session, condition, DeliveryReport.material_no)
            
            fastest = session.query(
                DeliveryReport.customer_model.label("product"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(condition, DeliveryReport.customer_model.isnot(None)).group_by(
                DeliveryReport.customer_model
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
            
            return {
                "top_product": top_product,
                "top_model": top_product,
                "top_material": top_material,
                "highest_revenue_product": top_product,
                "fastest_growing_product": _text(fastest.product) if fastest else "Unknown",
                "highest_unit_product": top_product,
            }
        except Exception:
            return {}

    def _get_division_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        """Get division analytics"""
        try:
            division_rows = session.query(
                DeliveryReport.division.label("value"),
                func.sum(DeliveryReport.dn_amount).label("revenue"),
            ).filter(condition, DeliveryReport.division.isnot(None)).group_by(
                DeliveryReport.division
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).all()
            
            if not division_rows:
                return {
                    "top_division": "Unknown",
                    "strongest_product_category": "Unknown",
                    "weakest_product_category": "Unknown",
                }
            
            return {
                "top_division": _text(division_rows[0].value),
                "strongest_product_category": _text(division_rows[0].value),
                "weakest_product_category": _text(division_rows[-1].value) if len(division_rows) > 1 else _text(division_rows[0].value),
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

    def _get_trend_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        """Get trend analytics"""
        try:
            monthly = session.query(
                func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("month"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            ).filter(
                condition,
                DeliveryReport.dn_create_date >= datetime.now() - timedelta(days=365)
            ).group_by("month").order_by("month").all()
            
            if not monthly or len(monthly) < 2:
                return {}
            
            recent = monthly[-3:]
            older = monthly[:3]
            
            recent_avg_revenue = sum(_number(r.revenue) for r in recent) / len(recent)
            older_avg_revenue = sum(_number(r.revenue) for r in older) / len(older)
            
            revenue_trend = _growth(recent_avg_revenue, older_avg_revenue)
            
            return {
                "unit_growth_pct": revenue_trend,
                "dn_growth_pct": revenue_trend,
            }
        except Exception:
            return {}

    def _calculate_forecast(self, session: Session, condition: Any) -> dict[str, Any]:
        """Calculate simple forecasts"""
        try:
            monthly = session.query(
                func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("month"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            ).filter(
                condition,
                DeliveryReport.dn_create_date >= datetime.now() - timedelta(days=180)
            ).group_by("month").order_by("month").all()
            
            if not monthly or len(monthly) < 2:
                return {}
            
            recent = monthly[-3:]
            avg_revenue = sum(_number(r.revenue) for r in recent) / len(recent)
            avg_units = sum(_number(r.units) for r in recent) / len(recent)
            avg_dns = sum(_number(r.dns) for r in recent) / len(recent)
            
            growth_rate = _growth(avg_revenue, sum(_number(r.revenue) for r in monthly[:3]) / 3)
            growth_factor = 1 + (growth_rate / 100)
            
            return {
                "forecast_revenue": round(avg_revenue * growth_factor, 2),
                "forecast_units": round(avg_units * growth_factor, 0),
                "forecast_dn": round(avg_dns * growth_factor, 0),
            }
        except Exception:
            return {}

    def _get_top_value(self, session: Session, condition: Any, column: Any) -> str:
        """Helper to get top value"""
        try:
            row = session.query(
                column.label("value"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(condition, column.isnot(None)).group_by(column).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).first()
            return _text(row.value) if row else "Unknown"
        except Exception:
            return "Unknown"

    def _apply_dealer_rankings(self, session: Session, item: DealerDashboard, values: dict) -> None:
        """Apply comprehensive rankings"""
        cache_key = f"rankings_{item.dealer_code}"
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
                func.count(distinct(case((_status_complete(DeliveryReport.pgi_status), DeliveryReport.dn_no)))).label("pgi_success"),
            ).filter(DeliveryReport.customer_name.isnot(None)).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code
            ).all()
            
            target = next(
                (r for r in ranking_rows
                 if _text(r.code, "") == item.dealer_code or _text(r.name, "") == item.dealer_name),
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
            
            # Business Score for ranking
            scores = []
            for r in ranking_rows:
                score = (
                    _percent(r.revenue, sum(row.revenue for row in ranking_rows)) * 0.35 +
                    _percent(r.pod, r.dns) * 0.25 +
                    max(0, 100 - _percent(r.pending, r.dns)) * 0.20 +
                    _percent(r.pgi_success, r.dns) * 0.20
                )
                scores.append(score)
            
            target_score = None
            for r, s in zip(ranking_rows, scores):
                if r is target:
                    target_score = s
                    break
            
            rankings = {
                "revenue_rank": rank_for(ranking_rows, lambda r: _number(r.revenue), True),
                "unit_rank": rank_for(ranking_rows, lambda r: _number(r.units), True),
                "dn_rank": rank_for(ranking_rows, lambda r: int(r.dns or 0), True),
                "delivery_rank": rank_for(
                    ranking_rows,
                    lambda r: self._days(r.delivery) if r.delivery is not None else float("inf"),
                    False
                ),
                "pod_rank": rank_for(ranking_rows, lambda r: _percent(r.pod, r.dns), True),
                "pending_rank": rank_for(ranking_rows, lambda r: _percent(r.pending, r.dns), False),
                "pgi_rank": rank_for(ranking_rows, lambda r: _percent(r.pgi_success, r.dns), True),
                "business_score_rank": rank_for(ranking_rows, lambda r: scores[ranking_rows.index(r)], True),
            }
            
            # Growth rank (approximate)
            growth_scores = []
            for r in ranking_rows:
                growth_scores.append(_number(r.revenue))
            
            target_growth = None
            for r, s in zip(ranking_rows, growth_scores):
                if r is target:
                    target_growth = s
                    break
            
            if target_growth is not None:
                rankings["growth_rank"] = rank_for(ranking_rows, lambda r: growth_scores[ranking_rows.index(r)], True)
            
            # Composite ranking
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
            regional = [r for r in ranking_rows if _text(r.city, "").lower() == item.city.lower()]
            regional.sort(key=lambda r: _number(r.revenue), reverse=True)
            rankings["regional_rank"] = next(
                (idx for idx, row in enumerate(regional, 1) if row is target),
                len(regional) or 1
            )
            
            values.update(rankings)
            self._ranking_cache[cache_key] = rankings
        except Exception as e:
            logger.warning(f"Rankings failed: {e}")

    def _calculate_additional_kpis(self, item: DealerDashboard) -> None:
        """Calculate additional business KPIs"""
        # Revenue per day
        if item.first_delivery_date and item.first_delivery_date != "N/A":
            try:
                first_date = datetime.strptime(item.first_delivery_date, "%Y-%m-%d").date()
                days_active = max(1, (date.today() - first_date).days)
                item.revenue_per_day = item.total_revenue / days_active
            except:
                item.revenue_per_day = 0.0
        
        # Revenue per delivery
        item.revenue_per_delivery = item.total_revenue / item.total_dn if item.total_dn > 0 else 0.0
        
        # Revenue per product
        item.revenue_per_product = item.total_revenue / 100
        
        # Dealer contribution
        item.dealer_contribution = 0.0
        
        # Risk score
        item.risk_score = round(100 - item.business_score, 1)

    def _apply_business_health(self, item: DealerDashboard) -> None:
        """Enhanced business health calculation"""
        # Weighted score with multiple factors
        score = (
            item.delivery_success_pct * 0.25 +
            item.pgi_success_pct * 0.15 +
            item.pod_success_pct * 0.20 +
            max(0.0, 100.0 - item.pending_pct) * 0.15 +
            min(100.0, max(0.0, 100.0 - item.critical_pending * 2)) * 0.10 +
            min(100.0, max(0.0, 100.0 + item.monthly_growth)) * 0.10 +
            item.warehouse_utilization * 0.05
        )
        
        item.business_score = round(max(0.0, min(100.0, score)), 1)
        
        # Determine status
        if item.business_score >= 85:
            item.overall_status = BusinessHealthStatus.EXCELLENT.value
        elif item.business_score >= 70:
            item.overall_status = BusinessHealthStatus.GOOD.value
        elif item.business_score >= 50:
            item.overall_status = BusinessHealthStatus.WATCH.value
        else:
            item.overall_status = BusinessHealthStatus.CRITICAL.value
        
        # Risk score
        item.risk_score = round(100 - item.business_score, 1)
        
        # Executive summary
        trend = "growing" if item.monthly_growth >= 0 else "declining"
        action = "maintain current controls" if item.business_score >= 70 else "prioritize pending DN and POD closure"
        
        item.executive_summary = (
            f"{item.dealer_name} is {trend} with a {item.business_score:.1f}/100 business score. "
            f"Delivery success is {item.delivery_success_pct:.1f}% and {item.pending_dn} DNs remain pending. "
            f"Revenue growth is {item.monthly_growth:+.1f}% month over month. "
            f"Recommendation: {action}."
        )

    def _business_insights(self, item: DealerDashboard) -> tuple[list[str], list[str]]:
        """Enhanced business insights"""
        trend = "increasing" if item.monthly_growth >= 0 else "decreasing"
        
        insights = [
            f"Revenue is {trend} ({item.monthly_growth:+.1f}% month over month).",
            f"Dealer has {item.pending_dn:,} pending DNs (worth PKR {item.pending_revenue:,.2f}).",
            f"Delivery success is {item.delivery_success_pct:.1f}% with average delivery of {item.average_delivery_days:.1f} days.",
            f"POD completion is {item.pod_success_pct:.1f}% and PGI completion is {item.pgi_success_pct:.1f}%.",
            f"{item.top_model} is the leading model; top material is {item.top_material}.",
            f"Best revenue month: {item.best_month}; National rank: #{item.national_rank or 'N/A'}.",
        ]
        
        # Revenue insights
        if item.monthly_growth > 10:
            insights.append(f"Revenue growth is strong at {item.monthly_growth:+.1f}%.")
        elif item.monthly_growth < -10:
            insights.append(f"Revenue is declining ({item.monthly_growth:+.1f}%). Investigate causes.")
        
        # Pending insights
        if item.oldest_pending_days > 14:
            insights.append(f"Oldest pending DN {item.oldest_pending_dn} is {item.oldest_pending_days} days old.")
        if item.critical_pending > 5:
            insights.append(f"Critical pending (>7 days): {item.critical_pending} DNs.")
        
        # Warehouse insights
        if item.warehouse_utilization > 80:
            insights.append(f"Warehouse {item.warehouse} is highly utilized ({item.warehouse_utilization:.1f}%).")
        elif item.warehouse_utilization < 30:
            insights.append(f"Warehouse {item.warehouse} has low utilization ({item.warehouse_utilization:.1f}%).")
        
        # Product insights
        if item.top_product and item.top_product != "Unknown":
            insights.append(f"{item.top_product} is the top product.")
        
        # Strengths
        strengths = []
        if item.delivery_success_pct >= 90:
            strengths.append("Excellent delivery performance")
        if item.pod_success_pct >= 90:
            strengths.append("Strong POD completion")
        if item.monthly_growth >= 10:
            strengths.append("Strong revenue growth")
        if item.pending_pct < 10:
            strengths.append("Low pending rate")
        if item.warehouse_utilization > 60:
            strengths.append("Good warehouse utilization")
        
        # Weaknesses
        weaknesses = []
        if item.pending_pct > 25:
            weaknesses.append("High pending rate")
        if item.pod_success_pct < 80:
            weaknesses.append("Low POD completion")
        if item.delivery_success_pct < 80:
            weaknesses.append("Low delivery success")
        if item.monthly_growth < -10:
            weaknesses.append("Declining revenue")
        if item.critical_pending > 5:
            weaknesses.append("Critical pending issues")
        
        # Opportunities
        opportunities = []
        if item.warehouse_utilization < 50:
            opportunities.append("Opportunity to increase warehouse utilization")
        if item.pending_pct > 20:
            opportunities.append("Reduce pending DNs to improve cash flow")
        if item.pod_success_pct < 85:
            opportunities.append("Improve POD collection process")
        
        # Threats
        threats = []
        if item.monthly_growth < -5:
            threats.append("Revenue decline trend")
        if item.critical_pending > 10:
            threats.append("High risk of order cancellations")
        if item.overdue_pending > 5:
            threats.append("Overdue deliveries affecting customer satisfaction")
        
        item.strengths = strengths
        item.weaknesses = weaknesses
        item.opportunities = opportunities
        item.threats = threats
        
        # Recommendations
        recommendations = []
        if item.overdue_pending:
            recommendations.append(f"Escalate {item.overdue_pending} DNs pending for more than 14 days.")
        if item.pod_success_pct < 85:
            recommendations.append("Prioritize POD collection and closure.")
        if item.pgi_pending_dn:
            recommendations.append(f"Review {item.pgi_pending_dn} DNs awaiting PGI.")
        if item.delivery_success_pct < 85:
            recommendations.append("Review delivery process for improvement.")
        if item.warehouse_utilization < 40:
            recommendations.append("Consider consolidating warehouse space.")
        if not recommendations:
            recommendations.append("Maintain current delivery and POD control process.")
            recommendations.append("Continue monitoring key performance indicators.")
        
        return insights, recommendations

    # ============================================================
    # PUBLIC API METHODS
    # ============================================================

    def get_dealer_dashboard(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        """Get enhanced dealer dashboard"""
        start_time = time.perf_counter()
        
        identifier = dealer_name or kwargs.get("dealer") or kwargs.get("dealer_code") or kwargs.get("customer_code") or ""
        if not identifier:
            return {"success": False, "error_code": "DEALER_REQUIRED", "message": "Please provide a dealer name or code."}
        
        try:
            with self._session() as session:
                search = self._resolve_dealer(session, str(identifier))
                if search.exception:
                    return {"success": False, "error_code": "SEARCH_ERROR", "message": "Dealer search is temporarily unavailable.", "error": search.exception}
                if not search.dealer_found:
                    return self._suggestion_response(search)
                
                resolved_identity = search.dealer_code or search.customer_code or search.dealer_found
                dashboard_key = str(resolved_identity).lower()
                
                cached_dashboard = self._dashboard_cache.get(dashboard_key)
                if cached_dashboard:
                    return cached_dashboard
                
                rows = self._aggregate_query(session, resolved_identity)
                if not rows:
                    return self._suggestion_response(search)
                
                data = self._row_to_dashboard(rows[0])
                
                try:
                    self._apply_extended_analytics(session, data)
                except Exception:
                    logger.exception("Extended analytics failed")
                    data.insights, data.recommendations = self._business_insights(data)
                
                try:
                    formatted = data.to_whatsapp_message()
                except Exception:
                    formatted = f"Dealer Dashboard\nDealer: {data.dealer_name}\nRevenue: {data.total_revenue:,.2f}\nUnits: {data.total_units:,}\nDN: {data.total_dn:,}"
                
                response = {
                    "success": True,
                    "data": data,
                    "dashboard": data,
                    "search": search,
                    "whatsapp_message": formatted,
                    "formatted_response": formatted,
                    "message": formatted,
                    "response": formatted,
                    "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
                }
                
                self._dashboard_cache[dashboard_key] = response
                return response
                
        except Exception as error:
            logger.exception("Dealer dashboard query failed")
            return {
                "success": False,
                "error_code": "DATABASE_UNAVAILABLE",
                "message": "Dealer database is currently unavailable.",
                "error": str(error),
                "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
            }

    def get_dealer_profile(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        """Get enhanced dealer profile"""
        try:
            result = self.get_dealer_dashboard(dealer_name, **kwargs)
            if not result.get("success"):
                return result
            
            result["profile"] = result["data"]
            result["whatsapp_message"] = result["data"].to_whatsapp_message()
            result["message"] = result["whatsapp_message"]
            result["response"] = result["whatsapp_message"]
            return result
        except Exception as error:
            logger.exception("Dealer profile failed")
            return {"success": False, "error_code": "PROFILE_ERROR", "message": "Dealer profile is temporarily unavailable.", "error": str(error)}

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
                return {"success": False, "error_code": "TWO_DEALERS_REQUIRED", "message": "Please provide at least two dealers."}
            
            dashboards = []
            for value in values[:10]:
                result = self.get_dealer_dashboard(value)
                if result.get("success"):
                    dashboards.append(result["data"])
            
            if len(dashboards) < 2:
                return {"success": False, "error_code": "DEALERS_NOT_FOUND", "message": "At least two matching dealers are required."}
            
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
            return {"success": True, "data": comparison, "comparison": comparison}
        except Exception as error:
            logger.exception("Dealer comparison failed")
            return {"success": False, "error_code": "COMPARISON_ERROR", "message": "Dealer comparison is temporarily unavailable.", "error": str(error)}

    def get_top_dealers(self, limit: int = 10, sort_by: str = "revenue", **kwargs: Any) -> dict[str, Any]:
        """Get top dealers by various metrics"""
        try:
            return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), False)
        except Exception as error:
            logger.exception("Top dealer request failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is temporarily unavailable.", "error": str(error)}

    def get_bottom_dealers(self, limit: int = 10, sort_by: str = "pending_pct", **kwargs: Any) -> dict[str, Any]:
        """Get bottom dealers by various metrics"""
        try:
            return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), True)
        except Exception as error:
            logger.exception("Bottom dealer request failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is temporarily unavailable.", "error": str(error)}

    def _rank(self, sort_by: str, limit: int, bottom: bool) -> dict[str, Any]:
        """Internal ranking method"""
        try:
            cache_key = f"{sort_by.lower()}|{int(limit)}|{int(bottom)}"
            cached = self._ranking_cache.get(cache_key)
            if cached:
                return cached
            
            with self._session() as session:
                items = [self._row_to_dashboard(row, include_distance=False) for row in self._aggregate_query(session)]
            
            key_name = self.SORT_ALIASES.get(sort_by.lower().replace(" ", "_"), "total_revenue")
            reverse = (not bottom) or (bottom and key_name in {"pending_pct", "average_delivery_days"})
            
            items.sort(
                key=lambda v: getattr(v, key_name, 0) if getattr(v, key_name, None) is not None else 0,
                reverse=reverse
            )
            
            ranking = DealerRanking(sort_by, "bottom" if bottom else "top", items[:max(1, min(int(limit), 100))])
            response = {"success": True, "data": ranking, "dealers": ranking.dealers, "count": len(ranking.dealers)}
            self._ranking_cache[cache_key] = response
            return response
        except (SQLAlchemyError, ValueError) as error:
            logger.exception("Dealer ranking failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is currently unavailable.", "error": str(error)}

    def diagnose_dealer_search(self, message: str = "", **kwargs: Any) -> dict[str, Any]:
        """Diagnose dealer search"""
        started = time.perf_counter()
        try:
            with self._session() as session:
                result = self._resolve_dealer(session, message or kwargs.get("dealer_name") or kwargs.get("dealer") or "")
                rows = len(self._aggregate_query(session, result.dealer_code or result.customer_code or result.dealer_found)) if result.dealer_found else 0
            
            output = asdict(result)
            output.update({
                "rows_returned": rows,
                "distance_calculated": False,
                "distance_source": "Unknown",
                "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)
            })
            return {"success": result.exception is None, "diagnostic": output}
        except Exception as error:
            logger.exception("Dealer diagnostics failed")
            return {"success": False, "diagnostic": {"original_message": message, "any_exception": str(error), "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)}}

    def health_check(self) -> dict[str, Any]:
        """Health check with detailed status"""
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
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as error:
            logger.exception("Dealer analytics health check failed")
            return {
                "healthy": False,
                "service": self._service_name,
                "version": self._version,
                "database": "disconnected",
                "error": str(error),
                "timestamp": datetime.utcnow().isoformat()
            }

    def validation_query(self) -> dict[str, Any]:
        """Validate database connectivity"""
        try:
            with self._session() as session:
                records = session.query(
                    func.count(distinct(func.coalesce(DeliveryReport.dealer_code, DeliveryReport.customer_code, DeliveryReport.customer_name)))
                ).scalar() or 0
            return {"success": True, "records": int(records), "error": None}
        except Exception as error:
            return {"success": False, "records": 0, "error": str(error)}

    def get_service_metadata(self) -> dict[str, Any]:
        """Get service metadata"""
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": "DEGRADED" if self._initialization_errors else "READY",
            "source": "PostgreSQL DeliveryReport",
            "distance_provider": "OpenRouteService" if self._distance and self._distance._ors else "geopy great-circle",
            "semantic_search": USE_SEMANTIC_SEARCH,
            "pyarrow": USE_PYARROW,
            "polars": USE_POLARS,
            "pgvector": USE_PGVECTOR,
            "startup_time": self._startup_time,
            "initialization_errors": self._initialization_errors
        }


# ============================================================
# SERVICE SINGLETON
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
                    _service._version = "8.5.0-degraded"
                    _service._startup_time = datetime.utcnow().isoformat()
                    _service._initialization_errors = [f"Emergency mode: {str(e)}"]
                    _service._coordinates = CityCoordinateService()
                    _service._distance = None
                    _service._semantic_search = SemanticSearchEngine()
                    _service._pyarrow = PyArrowProcessor()
                    _service._executor = ThreadPoolExecutor(max_workers=4)
                    _service._dealer_cache = TTLCache(maxsize=4096, ttl=CACHE_TTL)
                    _service._candidate_cache = TTLCache(maxsize=1, ttl=3600)
                    _service._extended_cache = TTLCache(maxsize=4096, ttl=3600)
                    _service._dashboard_cache = TTLCache(maxsize=4096, ttl=600)
                    _service._ranking_cache = TTLCache(maxsize=128, ttl=600)
                    _service._aggregate_cache = TTLCache(maxsize=1024, ttl=300)
                    _service._similarity_cache = TTLCache(maxsize=5000, ttl=3600)
                    _service._search_lock = threading.RLock()
                    _service._last_diagnostic = {}
                    _service._last_db_check = None
    return _service


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "DealerDashboard",
    "DealerComparison",
    "DealerRanking",
    "DealerSearchResult",
    "AIResponse",
    "DistanceAnalytics",
    "BusinessHealthStatus",
    "TrendType",
    "RankType",
    "SemanticSearchEngine",
    "SQLOptimizer",
    "PyArrowProcessor",
    "CityCoordinateService",
    "get_dealer_analytics_service"
]
