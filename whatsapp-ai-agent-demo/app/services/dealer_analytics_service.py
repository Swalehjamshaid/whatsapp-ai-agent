#!/usr/bin/env python3
# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 12.1 - ENTERPRISE DEALER INTELLIGENCE PLATFORM
# ============================================================

"""
================================================================================
DEALER LOGISTICS INTELLIGENCE PLATFORM - ENTERPRISE EDITION v12.1
================================================================================

SOURCE OF TRUTH: PostgreSQL ONLY

FEATURES:
- ✅ Complete Menu System (20+ options)
- ✅ Dealer Selection Prompts
- ✅ Comparison Flow (2 dealers)
- ✅ Ranking Display with Medals
- ✅ Quick Commands Support
- ✅ Context Memory
- ✅ Dynamic Menu Rendering
- ✅ WhatsApp-Optimized Formatting
- ✅ PostgreSQL Integration
- ✅ Full Analytics Suite
- ✅ Distance Calculation (ORS + Geopy + Haversine)
- ✅ AI Summary (Groq - Optional)
- ✅ Auto-Dealer Name Resolution (90% Confidence)
- ✅ Smart Suggestions
- ✅ Clean Dealer Name Display

================================================================================
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple

from cachetools import TTLCache
from sqlalchemy import and_, case, distinct, func, or_
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: OPTIONAL IMPORTS
# ============================================================

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    import openrouteservice
    ORS_AVAILABLE = True
except ImportError:
    ORS_AVAILABLE = False

try:
    from geopy.distance import geodesic
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

# ============================================================
# BLOCK 2: CONFIGURATION & CONSTANTS
# ============================================================

CACHE_TTL = max(60, int(os.getenv("DEALER_ANALYTICS_CACHE_TTL", "300")))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")
ORS_API_KEY = os.getenv("ORS_API_KEY", "")
ORS_PROFILE = os.getenv("ORS_PROFILE", "driving-car")

VERSION = "12.1"
MATCH_THRESHOLD = 90  # 90% confidence required

CITY_ABBREVIATIONS = {
    'khi': 'karachi', 'lhr': 'lahore', 'isb': 'islamabad', 'rwp': 'rawalpindi',
    'fsd': 'faisalabad', 'mul': 'multan', 'pes': 'peshawar', 'que': 'quetta',
    'hyd': 'hyderabad', 'guj': 'gujranwala', 'skt': 'sialkot'
}

DEALER_SUFFIXES = [
    'Electronics', 'Digital', 'Technologies', 'Traders', 'Enterprises',
    'Systems', 'Solutions', 'Incorporated', 'International', 'Corporation',
    'Limited', 'Ltd', 'Pvt', 'Private', 'Co', 'Company'
]

FALLBACK_COORDINATES = (30.3753, 69.3451)

WAREHOUSE_COORDINATES: Dict[str, Tuple[float, float]] = {
    "karachi": (24.8607, 67.0011), "lahore": (31.5204, 74.3587),
    "rawalpindi": (33.5651, 73.0169), "islamabad": (33.6844, 73.0479),
    "multan": (30.1575, 71.5249), "peshawar": (34.0151, 71.5249),
    "quetta": (30.1798, 66.9750), "hyderabad": (25.3960, 68.3578),
    "faisalabad": (31.4504, 73.1350), "sialkot": (32.4945, 74.5229),
    "gujranwala": (32.1617, 74.1883), "bahawalpur": (29.3956, 71.6836),
    "sukkur": (27.7060, 68.8530), "dg khan": (30.0430, 70.6402),
    "abbottabad": (34.1490, 73.2210), "gwadar": (25.1260, 62.3250),
    "gilgit": (35.9208, 74.3144)
}

CITY_COORDINATES = WAREHOUSE_COORDINATES.copy()

# ============================================================
# BLOCK 3: ENUMS & DATACLASSES
# ============================================================

class IntentType(Enum):
    DASHBOARD = "dashboard"; REVENUE = "revenue"; UNITS = "units"
    DEALERS = "dealers"; WAREHOUSES = "warehouses"; CITIES = "cities"
    PENDING_DN = "pending_dn"; PENDING_PGI = "pending_pgi"; PENDING_POD = "pending_pod"
    COMPARISON = "comparison"; RANKING = "ranking"; TREND = "trend"
    FORECAST = "forecast"; AI_SUMMARY = "ai_summary"; PERFORMANCE = "performance"
    RECOMMENDATIONS = "recommendations"; SEARCH = "search"; MENU = "menu"
    LOGISTICS = "logistics"; DISTANCE = "distance"; UNKNOWN = "unknown"

class MenuState(Enum):
    MAIN = "main"; DEALER_SELECTION = "dealer_selection"
    COMPARISON_SELECTION = "comparison_selection"; EXECUTING = "executing"

class ResponseFormat(Enum):
    COMPACT = "compact"; STANDARD = "standard"; EXECUTIVE = "executive"
    DETAILED = "detailed"; KPI_ONLY = "kpi_only"; JSON = "json"
    COMPARISON = "comparison"; RANKING = "ranking"; METRIC = "metric"
    LOGISTICS = "logistics"

@dataclass
class DealerContext:
    current_dealer: Optional[str] = None
    current_dealer_code: Optional[str] = None
    current_customer_code: Optional[str] = None
    last_question: Optional[str] = None
    last_intent: Optional[IntentType] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    session_start: datetime = field(default_factory=datetime.now)
    menu_state: MenuState = MenuState.MAIN
    selected_option: Optional[str] = None
    comparison_dealers: List[str] = field(default_factory=list)
    awaiting_dealer: bool = False
    awaiting_comparison: bool = False
    
    def clear(self) -> None:
        self.current_dealer = None
        self.current_dealer_code = None
        self.current_customer_code = None
        self.last_question = None
        self.last_intent = None
        self.conversation_history = []
        self.menu_state = MenuState.MAIN
        self.selected_option = None
        self.comparison_dealers = []
        self.awaiting_dealer = False
        self.awaiting_comparison = False

@dataclass
class QueryPlan:
    intent: IntentType
    dealer: Optional[str] = None
    dealers: List[str] = field(default_factory=list)
    dealer_code: Optional[str] = None
    customer_code: Optional[str] = None
    metrics: List[str] = field(default_factory=list)
    timeframe: Optional[str] = None
    limit: int = 10
    sort_by: Optional[str] = None
    order: str = "desc"
    format: str = "standard"
    confidence: float = 1.0
    requires_ai: bool = False

@dataclass
class DealerAnswer:
    question: str
    intent: IntentType
    plan: QueryPlan
    dashboard: Optional[Dict[str, Any]] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    recommendations: List[str] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    formatted_response: str = ""
    confidence: float = 1.0
    execution_time_ms: float = 0.0
    source: str = "PostgreSQL"
    ai_enhanced: bool = False
    context_used: bool = False

# ============================================================
# BLOCK 4: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except (TypeError, ValueError):
        return default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def _format_currency(amount: float) -> str:
    if amount >= 100_000_000:
        return f"PKR {amount/100_000_000:.2f}Cr"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount/1_000:.2f}K"
    return f"PKR {amount:,.0f}"

def _clean_dealer_name(name: str) -> str:
    if not name:
        return ""
    cleaned = name.lower().strip()
    for suffix in DEALER_SUFFIXES:
        cleaned = re.sub(r'\s*' + suffix.lower() + r'\s*$', '', cleaned)
    cleaned = re.sub(r'-[a-z]{3}$', '', cleaned)
    return cleaned.strip()

def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _get_coordinates(city: str) -> Tuple[float, float]:
    city_lower = city.lower()
    
    if ORS_AVAILABLE and ORS_API_KEY:
        try:
            client = openrouteservice.Client(key=ORS_API_KEY)
            result = client.pelias_search(text=city)
            if result and 'features' in result and len(result['features']) > 0:
                coords = result['features'][0]['geometry']['coordinates']
                return (coords[1], coords[0])
        except Exception:
            pass
    
    coords = CITY_COORDINATES.get(city_lower)
    if coords:
        return coords
    
    logger.warning(f"No coordinates for city: {city}, using fallback")
    return FALLBACK_COORDINATES

def _get_distance_info(warehouse: str, city: str) -> Dict[str, Any]:
    if not warehouse or not city:
        return {"distance_km": None, "estimated_delivery": "Unknown", "transportation_zone": "Unknown", "source": "Missing data"}
    
    warehouse_coord = _get_coordinates(warehouse)
    city_coord = _get_coordinates(city)
    
    if warehouse_coord and city_coord:
        if ORS_AVAILABLE and ORS_API_KEY:
            try:
                client = openrouteservice.Client(key=ORS_API_KEY)
                coords = [[warehouse_coord[1], warehouse_coord[0]], [city_coord[1], city_coord[0]]]
                routes = client.directions(coordinates=coords, profile=ORS_PROFILE, format='json')
                if routes and 'routes' in routes and len(routes['routes']) > 0:
                    route = routes['routes'][0]
                    summary = route.get('summary', {})
                    distance_km = summary.get('distance', 0) / 1000
                    duration_min = summary.get('duration', 0) / 60
                    
                    if distance_km <= 80: zone, est = "Local", "Same Day"
                    elif distance_km <= 200: zone, est = "Short Haul", "1 Day"
                    elif distance_km <= 400: zone, est = "Medium Haul", "2 Days"
                    elif distance_km <= 700: zone, est = "Long Haul", "3 Days"
                    else: zone, est = "Extended Haul", "4-5 Days"
                    
                    return {
                        "distance_km": round(distance_km, 1),
                        "duration_hours": round(duration_min / 60, 1),
                        "estimated_delivery": est,
                        "transportation_zone": zone,
                        "source": "OpenRouteService"
                    }
            except Exception:
                pass
        
        distance = _calculate_distance(warehouse_coord[0], warehouse_coord[1], city_coord[0], city_coord[1])
        if distance <= 80: zone, est = "Local", "Same Day"
        elif distance <= 200: zone, est = "Short Haul", "1 Day"
        elif distance <= 400: zone, est = "Medium Haul", "2 Days"
        elif distance <= 700: zone, est = "Long Haul", "3 Days"
        else: zone, est = "Extended Haul", "4-5 Days"
        
        return {
            "distance_km": round(distance, 1),
            "duration_hours": round(distance / 50, 1),
            "estimated_delivery": est,
            "transportation_zone": zone,
            "source": "Haversine (Fallback)"
        }
    
    return {"distance_km": None, "estimated_delivery": "Unknown", "transportation_zone": "Unknown", "source": "Unavailable"}

# ============================================================
# BLOCK 5: MENU RENDERER
# ============================================================

class DealerMenuRenderer:
    
    @staticmethod
    def _clean_dealer_name_for_display(name: str) -> str:
        if not name:
            return "Unknown Dealer"
        cleaned = name
        cleaned = re.sub(r'0[0-9]{2,4}[-.\s]?[0-9]{7,8}', '', cleaned)
        cleaned = re.sub(r'[0-9]{4}[-.\s]?[0-9]{7}', '', cleaned)
        cleaned = re.sub(r'\b[0-9]{10,12}\b', '', cleaned)
        cleaned = re.sub(r'^[0-9]{4,5}[-.\s]', '', cleaned)
        cleaned = re.sub(r'C/O\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*,', ',', cleaned)
        cleaned = re.sub(r',\s*', ', ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        cleaned = cleaned.rstrip(',')
        if len(cleaned) > 40:
            for sep in [',', '/', ' - ']:
                if sep in cleaned:
                    cleaned = cleaned.split(sep)[0].strip()
                    break
            if len(cleaned) > 35:
                cleaned = cleaned[:32] + "..."
        return cleaned if cleaned else "Unknown Dealer"
    
    @staticmethod
    def render_main_menu() -> str:
        return "\n".join([
            "🏢 *DEALER ANALYTICS MENU*", "",
            "0. Main Menu", "1. Dealer Dashboard", "2. Dealer Revenue",
            "3. Dealer Units", "4. Dealer Logistics", "5. Dealer Warehouses",
            "6. Dealer Cities", "7. Pending DN", "8. Pending PGI",
            "9. Pending POD", "10. Dealer Comparison", "11. Dealer Ranking",
            "12. Monthly Trend", "13. Executive Summary", "14. AI Insights",
            "15. Recommendations", "16. Business Performance",
            "17. Dealer Score", "18. Smart Search", "99. Back to Main", "",
            "📌 *Quick Commands:*",
            "• Type dealer name for dashboard",
            "• Compare [Dealer1] and [Dealer2]",
            "• Top dealers by revenue",
            "• Revenue of [Dealer]", "",
            "Reply with a number or dealer name:"
        ])
    
    @staticmethod
    def render_dealer_selection(prompt: str = "Enter dealer name:") -> str:
        return "\n".join([
            "🔍 *Dealer Selection*", "", prompt, "",
            "💡 *Examples:*", "Arshad Electronics-Khi", "Zoom Appliances",
            "RUBA Digital", "", "0. Main Menu", "99. Back"
        ])
    
    @staticmethod
    def render_comparison_selection() -> str:
        return "\n".join([
            "🔄 *Compare Dealers*", "",
            "Enter first dealer name:", "",
            "0. Main Menu", "99. Back"
        ])
    
    @staticmethod
    def render_dealer_dashboard(dealer_name: str, data: Dict[str, Any]) -> str:
        identity = data.get('identity', {})
        delivery = data.get('delivery', {})
        sales = data.get('sales', {})
        distance = data.get('distance', {})
        product = data.get('product', {})
        warehouse = data.get('warehouse', {})
        
        display_name = DealerMenuRenderer._clean_dealer_name_for_display(
            identity.get('customer_name', dealer_name)
        )
        
        today = datetime.utcnow().date()
        last_dn_date_str = data.get('dates', {}).get('last_delivery_date', '')
        oldest_pending_days = 0
        if last_dn_date_str:
            try:
                last_dn_date = datetime.strptime(last_dn_date_str, "%d-%b-%Y").date()
                oldest_pending_days = (today - last_dn_date).days
            except ValueError:
                pass
        
        primary_warehouse = identity.get('warehouse', 'N/A')
        warehouse_distance = distance.get('distance_km', 'N/A')
        estimated_transit = distance.get('estimated_delivery', 'N/A')
        warehouse_distribution = warehouse.get('warehouse_distribution', [])
        
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "        🏢 DEALER INTELLIGENCE CENTER",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
            f"Dealer Name        : {display_name}",
            f"Dealer Code        : {identity.get('dealer_code', 'N/A')}",
            f"City               : {identity.get('city', 'N/A')}",
            f"Sales Office       : {identity.get('sales_office', 'N/A')}",
            f"Sales Manager      : {identity.get('sales_manager', 'N/A')}", "",
            f"Primary Warehouse  : {primary_warehouse}",
            f"Distance           : {warehouse_distance} km",
            f"Estimated Transit  : {estimated_transit}", "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📦 DELIVERY SUMMARY",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
            f"Total DNs          : {delivery.get('total_dn', 0):,}",
            f"Total Units        : {sales.get('total_quantity', 0):,}",
            f"Total Revenue      : {_format_currency(sales.get('total_revenue', 0))}", "",
            f"Delivered DNs      : {delivery.get('delivered_dn', 0):,}",
            f"Pending DNs        : {delivery.get('pending_dn', 0):,}",
            f"Pending PGI        : {delivery.get('pgi_pending', 0):,}",
            f"Pending POD        : {delivery.get('pod_pending', 0):,}", "",
            f"Delivery Rate      : {delivery.get('delivery_rate', 0):.1f}%",
            f"POD Completion     : {delivery.get('pod_rate', 0):.1f}%", "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📅 AGING ANALYSIS",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
            f"Average Delivery Days : {delivery.get('avg_delivery_days', 0):.1f}",
            f"Average POD Days      : {delivery.get('avg_pod_days', 0):.1f}", "",
            f"Oldest Pending DN     : {oldest_pending_days} Days",
            f"Newest Pending DN     : 1 Day", "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏭 WAREHOUSE DISTRIBUTION",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        
        if warehouse_distribution:
            for wh in warehouse_distribution[:3]:
                lines.append(f"{wh.get('warehouse', 'Unknown')}: {wh.get('dn_count', 0)} DNs")
        else:
            lines.append(f"{primary_warehouse}: {delivery.get('total_dn', 0)} DNs")
        
        lines.extend([
            "", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📦 PRODUCT SUMMARY",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
            f"Top Model             : {product.get('top_model', 'N/A')}",
            f"Top Category          : {product.get('top_category', 'N/A')}",
            f"Total Models          : {product.get('total_models', 0)}", "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "⚠ ISSUES REQUIRING ACTION",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        
        issues = data.get('issues', [])
        if issues:
            for issue in issues[:4]:
                lines.append(f"• {issue}")
        else:
            lines.append("• No critical issues found")
        
        lines.extend([
            "", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📈 BUSINESS INSIGHTS",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        
        insights = data.get('insights', [])
        if insights:
            for insight in insights[:5]:
                lines.append(f"• {insight}")
        else:
            lines.append("• Performance is stable")
        
        if warehouse_distance != 'N/A':
            lines.append(f"• Distance to dealer: {warehouse_distance} km")
            lines.append(f"• Estimated transit: {estimated_transit}")
        
        lines.extend([
            "", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
            "0. Main Menu", "99. Back to Main"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "revenue", limit: int = 10) -> str:
        lines = [f"🏆 *Dealer Rankings by {metric.title()}*", ""]
        for i, item in enumerate(ranking[:limit], 1):
            dealer = item.get('dealer', 'Unknown')
            value = item.get('value', 'N/A')
            clean_dealer = DealerMenuRenderer._clean_dealer_name_for_display(dealer)
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            lines.append(f"{medal} {clean_dealer}: {value}")
        lines.extend(["", "━━━━━━━━━━━━━━━━━━━━", "", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison_result(dealer1: str, dealer2: str, metrics: Dict[str, Any]) -> str:
        clean_d1 = DealerMenuRenderer._clean_dealer_name_for_display(dealer1)
        clean_d2 = DealerMenuRenderer._clean_dealer_name_for_display(dealer2)
        lines = [f"🔄 *Comparison: {clean_d1} vs {clean_d2}*", "", "───────────────────", ""]
        
        metrics1 = metrics.get(f"{dealer1}_metrics", {})
        metrics2 = metrics.get(f"{dealer2}_metrics", {})
        
        for key in sorted(set(metrics1.keys()) | set(metrics2.keys())):
            v1 = metrics1.get(key, "N/A")
            v2 = metrics2.get(key, "N/A")
            lines.append(f"{key}: {v1} vs {v2}")
        
        lines.extend([
            "", "───────────────────", "",
            "💡 *Summary*", metrics.get('explanation', 'Comparison complete.'),
            "", "0. Main Menu", "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_pending_list(title: str, dealers: List[Dict[str, Any]]) -> str:
        if not dealers:
            return f"📋 *{title}*\n\nNo pending items found."
        lines = [f"📋 *{title}*", ""]
        for i, item in enumerate(dealers[:10], 1):
            dealer = item.get('dealer_name', 'N/A')
            pending = item.get('pending_count', 0)
            clean_dealer = DealerMenuRenderer._clean_dealer_name_for_display(dealer)
            lines.append(f"{i}. {clean_dealer}: {pending} pending")
        if len(dealers) > 10:
            lines.append(f"... and {len(dealers) - 10} more")
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_executive_summary(dealer_name: str, data: Dict[str, Any]) -> str:
        identity = data.get('identity', {})
        delivery = data.get('delivery', {})
        sales = data.get('sales', {})
        performance = data.get('performance', {})
        
        clean_dealer = DealerMenuRenderer._clean_dealer_name_for_display(
            identity.get('customer_name', dealer_name)
        )
        
        revenue = sales.get('total_revenue', 0)
        units = sales.get('total_quantity', 0)
        dn = delivery.get('total_dn', 0)
        pending = delivery.get('pending_dn', 0)
        score = performance.get('business_score', 0)
        tier = performance.get('performance_tier', 'Standard')
        recommendations = data.get('recommendations', [])[:3]
        
        lines = [
            f"📋 *Executive Summary - {clean_dealer}*", "",
            f"💰 Revenue: {_format_currency(revenue)}",
            f"📦 Units: {units:,}",
            f"📄 DN: {dn:,}",
            f"⏳ Pending: {pending:,}",
            f"⭐ Score: {score}/100",
            f"🏆 Tier: {tier}", "",
            "🎯 *Recommendations*",
        ]
        for rec in recommendations:
            lines.append(f"• {rec}")
        if not recommendations:
            lines.append("• Maintain current performance levels")
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)

# ============================================================
# BLOCK 6: DEALER REPOSITORY
# ============================================================

class DealerRepository:
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=2048, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def get_dealer_by_name(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        dealer_lower = dealer_identifier.lower()
        cache_key = f"dealer_{dealer_lower}"
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            query = self.session.query(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.delivery_location,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealer_count'),
                func.count(distinct(DeliveryReport.ship_to_city)).label('city_count'),
                func.count(distinct(DeliveryReport.warehouse)).label('warehouse_count'),
                func.min(DeliveryReport.dn_create_date).label('first_sale'),
                func.max(DeliveryReport.dn_create_date).label('last_sale'),
                func.avg(DeliveryReport.dn_amount).label('avg_dn_value'),
                func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label('pending_dn'),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)))).label('pgi_pending_dn'),
                func.count(distinct(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label('pod_pending_dn'),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label('pod_completed'),
                func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no)))).label('pgi_completed'),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label('avg_delivery_days'),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label('avg_pod_days'),
            ).filter(
                or_(
                    func.lower(DeliveryReport.customer_name) == dealer_lower,
                    func.lower(DeliveryReport.dealer_code) == dealer_lower,
                    func.lower(DeliveryReport.customer_code) == dealer_lower,
                    func.lower(DeliveryReport.customer_name).ilike(f"%{dealer_lower}%"),
                    func.lower(DeliveryReport.dealer_code).ilike(f"%{dealer_lower}%"),
                    func.lower(DeliveryReport.customer_code).ilike(f"%{dealer_lower}%"),
                )
            ).group_by(
                DeliveryReport.customer_name, DeliveryReport.dealer_code,
                DeliveryReport.customer_code, DeliveryReport.ship_to_city,
                DeliveryReport.warehouse, DeliveryReport.warehouse_code,
                DeliveryReport.delivery_location, DeliveryReport.sales_office,
                DeliveryReport.sales_manager, DeliveryReport.division
            ).first()
            
            if not query:
                return None
            
            dealer_data = {
                'customer_name': _text(query.customer_name),
                'dealer_code': _text(query.dealer_code),
                'customer_code': _text(query.customer_code),
                'city': _text(query.ship_to_city),
                'warehouse': _text(query.warehouse),
                'warehouse_code': _text(query.warehouse_code),
                'delivery_location': _text(query.delivery_location),
                'sales_office': _text(query.sales_office),
                'sales_manager': _text(query.sales_manager),
                'division': _text(query.division),
                'dn_count': int(query.dn_count or 0),
                'total_units': int(query.total_units or 0),
                'total_revenue': float(query.total_revenue or 0.0),
                'dealer_count': int(query.dealer_count or 0),
                'city_count': int(query.city_count or 0),
                'warehouse_count': int(query.warehouse_count or 0),
                'first_sale': _date_text(query.first_sale),
                'last_sale': _date_text(query.last_sale),
                'avg_dn_value': float(query.avg_dn_value or 0.0),
                'pending_dn': int(query.pending_dn or 0),
                'pgi_pending_dn': int(query.pgi_pending_dn or 0),
                'pod_pending_dn': int(query.pod_pending_dn or 0),
                'pod_completed': int(query.pod_completed or 0),
                'pgi_completed': int(query.pgi_completed or 0),
                'avg_delivery_days': float(query.avg_delivery_days or 0.0),
                'avg_pod_days': float(query.avg_pod_days or 0.0),
            }
            
            dealer_data['delivery_success_pct'] = _percent(dealer_data.get('pod_completed', 0), dealer_data.get('dn_count', 0))
            dealer_data['pgi_rate'] = _percent(dealer_data.get('pgi_completed', 0), dealer_data.get('dn_count', 0))
            dealer_data['pod_rate'] = _percent(dealer_data.get('pod_completed', 0), dealer_data.get('dn_count', 0))
            dealer_data['pending_pct'] = _percent(dealer_data.get('pending_dn', 0), dealer_data.get('dn_count', 0))
            dealer_data['avg_units_per_dn'] = dealer_data.get('total_units', 0) / dealer_data.get('dn_count', 1) if dealer_data.get('dn_count', 0) > 0 else 0
            dealer_data['distance'] = _get_distance_info(dealer_data.get('warehouse', ''), dealer_data.get('city', ''))
            
            score = (dealer_data.get('delivery_success_pct', 0) * 0.25 +
                    (100 - dealer_data.get('pending_pct', 0)) * 0.25 +
                    min(100, dealer_data.get('total_units', 0) / 100) * 0.20 +
                    min(100, dealer_data.get('avg_dn_value', 0) / 1000) * 0.15 +
                    min(100, dealer_data.get('dealer_count', 0) * 5) * 0.15)
            dealer_data['business_score'] = round(min(100, max(0, score)), 1)
            
            if dealer_data['business_score'] >= 85:
                dealer_data['performance_grade'], dealer_data['overall_status'], dealer_data['performance_tier'], dealer_data['dealer_rating'] = "A", "Excellent", "Platinum", 5.0
            elif dealer_data['business_score'] >= 70:
                dealer_data['performance_grade'], dealer_data['overall_status'], dealer_data['performance_tier'], dealer_data['dealer_rating'] = "B", "Good", "Gold", 4.0
            elif dealer_data['business_score'] >= 50:
                dealer_data['performance_grade'], dealer_data['overall_status'], dealer_data['performance_tier'], dealer_data['dealer_rating'] = "C", "Watch", "Silver", 3.0
            else:
                dealer_data['performance_grade'], dealer_data['overall_status'], dealer_data['performance_tier'], dealer_data['dealer_rating'] = "D", "Critical", "Bronze", 2.0
            
            dealer_data['risk_score'] = 100 - dealer_data['business_score']
            dealer_data['insights'] = self._generate_insights(dealer_data)
            dealer_data['recommendations'] = self._generate_recommendations(dealer_data)
            dealer_data['executive_summary'] = self._generate_executive_summary(dealer_data)
            
            with self._lock:
                self._cache[cache_key] = dealer_data.copy()
            return dealer_data
            
        except Exception as e:
            logger.error(f"Failed to get dealer data for {dealer_identifier}: {e}")
            return None
    
    def _generate_insights(self, data: Dict[str, Any]) -> List[str]:
        insights = []
        delivery = data.get('delivery_success_pct', 0)
        pod = data.get('pod_rate', 0)
        pgi = data.get('pgi_rate', 0)
        pending = data.get('pending_dn', 0)
        revenue = data.get('total_revenue', 0)
        score = data.get('business_score', 0)
        dealers = data.get('dealer_count', 0)
        
        if delivery >= 95: insights.append("✅ Excellent delivery performance")
        elif delivery >= 85: insights.append("✅ Good delivery performance")
        elif delivery < 75: insights.append("⚠️ Delivery rate needs improvement")
        
        if pod >= 95: insights.append("✅ Excellent POD completion")
        elif pod < 80: insights.append("⚠️ POD completion needs attention")
        
        if pgi >= 95: insights.append("✅ Excellent PGI completion")
        elif pgi < 80: insights.append("⚠️ PGI completion needs attention")
        
        if pending == 0: insights.append("✅ No pending orders - excellent efficiency")
        elif pending < 10: insights.append(f"📋 Low pending orders: {pending}")
        else: insights.append(f"⚠️ High pending orders: {pending}")
        
        if revenue > 10_000_000: insights.append("📈 Revenue is above dealer average")
        elif revenue > 5_000_000: insights.append("📈 Revenue is at dealer average")
        
        if score >= 85: insights.append(f"⭐ Excellent business score: {score:.1f}/100")
        elif score >= 70: insights.append(f"⭐ Good business score: {score:.1f}/100")
        elif score < 50: insights.append(f"⚠️ Critical business score: {score:.1f}/100")
        
        if dealers >= 50: insights.append(f"🏪 Strong dealer network with {dealers} dealers")
        elif dealers >= 20: insights.append(f"🏪 Good dealer network with {dealers} dealers")
        
        if not insights:
            insights.append("Performance is stable. Continue monitoring.")
        return insights
    
    def _generate_recommendations(self, data: Dict[str, Any]) -> List[str]:
        recommendations = []
        pending = data.get('pending_dn', 0)
        delivery = data.get('delivery_success_pct', 0)
        score = data.get('business_score', 0)
        dealers = data.get('dealer_count', 0)
        
        if pending > 20: recommendations.append(f"📋 Escalate {pending} pending DNs")
        elif pending > 10: recommendations.append("📋 Review pending orders")
        if delivery < 80: recommendations.append("📋 Improve delivery performance")
        if data.get('pod_rate', 0) < 85: recommendations.append("📋 Focus on POD completion")
        if score < 70: recommendations.append("📋 Develop performance improvement plan")
        if dealers < 10: recommendations.append("📋 Expand dealer network")
        
        if not recommendations:
            recommendations = ["📋 Maintain current performance", "📋 Monitor key metrics", "📋 Explore growth opportunities"]
        return recommendations
    
    def _generate_executive_summary(self, data: Dict[str, Any]) -> str:
        dealer = data.get('customer_name', 'Dealer')
        revenue = data.get('total_revenue', 0)
        pending = data.get('pending_dn', 0)
        score = data.get('business_score', 0)
        status = data.get('overall_status', 'Unknown')
        tier = data.get('performance_tier', 'Standard')
        action = "maintain current controls" if score >= 70 else "prioritize pending DN and POD closure"
        return f"{dealer} has {status.lower()} performance with a {score:.1f}/100 business score. Revenue is {_format_currency(revenue)} with {pending} pending deliveries. Performance tier: {tier}. Recommendation: {action}."
    
    def get_top_dealers_by_revenue(self, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(DeliveryReport.customer_name.isnot(None)).group_by(
                DeliveryReport.customer_name
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).limit(limit).all()
            return [{'dealer': _text(r.dealer), 'value': f"PKR {float(r.revenue or 0):,.2f}"} for r in results if r.dealer]
        except Exception as e:
            logger.error(f"Failed to get top dealers: {e}")
            return []
    
    def get_top_dealers_by_units(self, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                func.sum(DeliveryReport.dn_qty).label('units')
            ).filter(DeliveryReport.customer_name.isnot(None)).group_by(
                DeliveryReport.customer_name
            ).order_by(func.sum(DeliveryReport.dn_qty).desc()).limit(limit).all()
            return [{'dealer': _text(r.dealer), 'value': f"{int(r.units or 0):,} units"} for r in results if r.dealer]
        except Exception as e:
            logger.error(f"Failed to get top dealers by units: {e}")
            return []
    
    def search_dealers(self, query: str) -> List[Dict[str, Any]]:
        try:
            pattern = f"%{query}%"
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                DeliveryReport.dealer_code, DeliveryReport.customer_code,
                DeliveryReport.ship_to_city, DeliveryReport.warehouse
            ).filter(
                or_(
                    DeliveryReport.customer_name.ilike(pattern),
                    DeliveryReport.dealer_code.ilike(pattern),
                    DeliveryReport.customer_code.ilike(pattern),
                    DeliveryReport.ship_to_city.ilike(pattern),
                    DeliveryReport.warehouse.ilike(pattern)
                )
            ).distinct().limit(20).all()
            return [{
                'dealer': _text(r.dealer), 'dealer_code': _text(r.dealer_code),
                'customer_code': _text(r.customer_code), 'city': _text(r.ship_to_city),
                'warehouse': _text(r.warehouse)
            } for r in results if r.dealer]
        except Exception as e:
            logger.error(f"Failed to search dealers: {e}")
            return []
    
    def get_pending_dealers(self) -> List[Dict[str, Any]]:
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer_name'),
                func.count(distinct(DeliveryReport.dn_no)).label('pending_count')
            ).filter(or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None))).group_by(
                DeliveryReport.customer_name
            ).order_by(func.count(distinct(DeliveryReport.dn_no)).desc()).limit(10).all()
            return [{'dealer_name': _text(r.dealer_name), 'pending_count': int(r.pending_count or 0)} for r in results if r.dealer_name]
        except Exception as e:
            logger.error(f"Failed to get pending dealers: {e}")
            return []

# ============================================================
# BLOCK 7: DEALER DASHBOARD BUILDER
# ============================================================

class DealerDashboardBuilder:
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=CACHE_TTL)
        self._lock = threading.RLock()
        self.repository = DealerRepository(session)
    
    def build(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        cache_key = dealer_identifier.lower()
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        dealer_data = self.repository.get_dealer_by_name(dealer_identifier)
        if not dealer_data:
            return None
        
        top_models = self._get_top_models(dealer_identifier)
        
        dashboard = {
            'identity': {
                'customer_name': dealer_data.get('customer_name', ''),
                'dealer_code': dealer_data.get('dealer_code', ''),
                'customer_code': dealer_data.get('customer_code', ''),
                'city': dealer_data.get('city', ''),
                'warehouse': dealer_data.get('warehouse', ''),
                'warehouse_code': dealer_data.get('warehouse_code', ''),
                'delivery_location': dealer_data.get('delivery_location', ''),
                'sales_office': dealer_data.get('sales_office', ''),
                'sales_manager': dealer_data.get('sales_manager', ''),
                'division': dealer_data.get('division', ''),
            },
            'delivery': {
                'total_dn': dealer_data.get('dn_count', 0),
                'pending_dn': dealer_data.get('pending_dn', 0),
                'pgi_pending': dealer_data.get('pgi_pending_dn', 0),
                'pod_pending': dealer_data.get('pod_pending_dn', 0),
                'delivered_dn': dealer_data.get('pod_completed', 0),
                'pgi_completed': dealer_data.get('pgi_completed', 0),
                'pod_completed': dealer_data.get('pod_completed', 0),
                'delivery_rate': dealer_data.get('delivery_success_pct', 0),
                'pgi_rate': dealer_data.get('pgi_rate', 0),
                'pod_rate': dealer_data.get('pod_rate', 0),
                'avg_delivery_days': dealer_data.get('avg_delivery_days', 0),
                'avg_pod_days': dealer_data.get('avg_pod_days', 0),
            },
            'sales': {
                'total_revenue': dealer_data.get('total_revenue', 0),
                'total_quantity': dealer_data.get('total_units', 0),
                'avg_dn_value': dealer_data.get('avg_dn_value', 0),
                'avg_quantity_per_dn': dealer_data.get('avg_units_per_dn', 0),
            },
            'product': {
                'total_models': self._get_product_count(dealer_identifier),
                'top_models': top_models,
                'top_model': top_models[0].get('model', 'N/A') if top_models else 'N/A',
                'top_category': top_models[0].get('category', 'N/A') if top_models else 'N/A',
            },
            'warehouse': {
                'primary_warehouse': dealer_data.get('warehouse', ''),
                'warehouses_used': dealer_data.get('warehouse_count', 0),
                'warehouse_distribution': self._get_warehouse_distribution(dealer_identifier),
            },
            'city': {
                'cities_served': dealer_data.get('city_count', 0),
                'top_destination_cities': self._get_top_cities(dealer_identifier),
            },
            'performance': {
                'business_score': dealer_data.get('business_score', 0),
                'risk_score': dealer_data.get('risk_score', 0),
                'performance_tier': dealer_data.get('performance_tier', 'Standard'),
                'dealer_rating': dealer_data.get('dealer_rating', 0),
                'dealer_rank': 0,
            },
            'distance': dealer_data.get('distance', {}),
            'dates': {
                'last_delivery_date': dealer_data.get('last_sale', 'N/A'),
                'last_pgi_date': self._get_last_pgi_date(dealer_identifier),
                'last_pod_date': dealer_data.get('last_pod_date', 'N/A'),
            },
            'insights': dealer_data.get('insights', []),
            'issues': self._get_issues(dealer_data),
            'recommendations': dealer_data.get('recommendations', []),
            'executive_summary': dealer_data.get('executive_summary', ''),
        }
        
        dashboard['insights'].extend(self._get_warehouse_insights(dashboard))
        
        with self._lock:
            self._cache[cache_key] = dashboard.copy()
        return dashboard
    
    def _get_top_models(self, dealer_identifier: str, limit: int = 3) -> List[Dict[str, Any]]:
        try:
            with self.session as session:
                results = session.query(
                    DeliveryReport.material_no.label('model'),
                    func.count(DeliveryReport.dn_no).label('dn_count'),
                    func.sum(DeliveryReport.dn_qty).label('total_units')
                ).filter(DeliveryReport.customer_name == dealer_identifier).group_by(
                    DeliveryReport.material_no
                ).order_by(func.sum(DeliveryReport.dn_qty).desc()).limit(limit).all()
                
                def derive_category(material_no):
                    if not material_no:
                        return 'N/A'
                    m = material_no.upper()
                    if any(m.startswith(p) for p in ['AAC', 'AC-', 'INV-', 'AC']): return 'Air Conditioner'
                    if any(m.startswith(p) for p in ['REF', 'FRIDGE', 'RF-']): return 'Refrigerator'
                    if any(m.startswith(p) for p in ['WASH', 'WM-']): return 'Washing Machine'
                    if any(m.startswith(p) for p in ['TV-', 'LED-', 'LCD-']): return 'Television'
                    if any(m.startswith(p) for p in ['MIC', 'MW-']): return 'Microwave'
                    if any(m.startswith(p) for p in ['DW-', 'DISH']): return 'Dishwasher'
                    if any(m.startswith(p) for p in ['FREEZ', 'CHEST']): return 'Freezer'
                    if any(m.startswith(p) for p in ['FAN', 'CEILING']): return 'Fan'
                    return 'Electronics'
                
                return [{
                    'model': r.model,
                    'category': derive_category(r.model),
                    'dn_count': r.dn_count,
                    'total_units': r.total_units
                } for r in results]
        except Exception as e:
            logger.error(f"Error getting top models: {e}")
            return []
    
    def _get_product_count(self, dealer_identifier: str) -> int:
        try:
            with self.session as session:
                count = session.query(func.count(distinct(DeliveryReport.material_no))).filter(
                    DeliveryReport.customer_name == dealer_identifier
                ).scalar()
                return count or 0
        except Exception:
            return 0
    
    def _get_warehouse_distribution(self, dealer_identifier: str) -> List[Dict[str, Any]]:
        try:
            with self.session as session:
                results = session.query(
                    DeliveryReport.warehouse, func.count(DeliveryReport.dn_no).label('dn_count')
                ).filter(DeliveryReport.customer_name == dealer_identifier).group_by(
                    DeliveryReport.warehouse
                ).order_by(func.count(DeliveryReport.dn_no).desc()).all()
                return [{'warehouse': r.warehouse, 'dn_count': r.dn_count} for r in results]
        except Exception:
            return []
    
    def _get_last_pgi_date(self, dealer_identifier: str) -> str:
        try:
            with self.session as session:
                date_val = session.query(func.max(DeliveryReport.good_issue_date)).filter(
                    DeliveryReport.customer_name == dealer_identifier
                ).scalar()
                return _date_text(date_val) if date_val else 'N/A'
        except Exception:
            return 'N/A'
    
    def _get_top_cities(self, dealer_identifier: str, limit: int = 3) -> List[Dict[str, Any]]:
        try:
            with self.session as session:
                results = session.query(
                    DeliveryReport.ship_to_city.label('city'),
                    func.count(DeliveryReport.dn_no).label('dn_count')
                ).filter(DeliveryReport.customer_name == dealer_identifier).group_by(
                    DeliveryReport.ship_to_city
                ).order_by(func.count(DeliveryReport.dn_no).desc()).limit(limit).all()
                return [{'city': r.city, 'dn_count': r.dn_count} for r in results if r.city]
        except Exception:
            return []
    
    def _get_issues(self, dealer_data: Dict[str, Any]) -> List[str]:
        issues = []
        pending_dn = dealer_data.get('pending_dn', 0)
        pgi_pending = dealer_data.get('pgi_pending_dn', 0)
        pod_pending = dealer_data.get('pod_pending_dn', 0)
        avg_delivery_days = dealer_data.get('avg_delivery_days', 0)
        if pgi_pending > 0: issues.append(f"{pgi_pending} DNs pending PGI")
        if pod_pending > 0: issues.append(f"{pod_pending} DNs pending POD")
        if pending_dn > 10: issues.append(f"{pending_dn} DNs pending - above threshold")
        if avg_delivery_days > 5: issues.append(f"High average delivery days: {avg_delivery_days:.1f}")
        return issues
    
    def _get_warehouse_insights(self, dashboard: Dict[str, Any]) -> List[str]:
        insights = []
        identity = dashboard.get('identity', {})
        distance = dashboard.get('distance', {})
        warehouse = identity.get('warehouse', '')
        warehouse_distance = distance.get('distance_km', None)
        estimated_transit = distance.get('estimated_delivery', '')
        if warehouse_distance:
            insights.append(f"Distance to dealer: {warehouse_distance} km")
            insights.append(f"Estimated transit: {estimated_transit}")
        if warehouse:
            insights.append(f"Primary warehouse: {warehouse}")
        return insights

# ============================================================
# BLOCK 8: AI SUMMARY ENGINE
# ============================================================

class AISummaryEngine:
    def __init__(self):
        self._client = None
        self._available = False
        if GROQ_AVAILABLE and GROQ_API_KEY:
            try:
                self._client = Groq(api_key=GROQ_API_KEY)
                self._available = True
            except Exception as e:
                logger.warning(f"⚠️ Groq init failed: {e}")
    
    def generate_summary(self, dealer_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self._available or not self._client:
            return self._fallback_summary(dealer_data)
        
        try:
            identity = dealer_data.get('identity', {})
            delivery = dealer_data.get('delivery', {})
            sales = dealer_data.get('sales', {})
            performance = dealer_data.get('performance', {})
            
            prompt = f"""Analyze this dealer's performance:
            Dealer: {identity.get('customer_name', 'Unknown')}
            Revenue: PKR {sales.get('total_revenue', 0):,.2f}
            Total DN: {delivery.get('total_dn', 0)}
            Delivery Rate: {delivery.get('delivery_rate', 0):.1f}%
            Pending DN: {delivery.get('pending_dn', 0)}
            Business Score: {performance.get('business_score', 0)}/100
            
            Provide: 1) Business Health (1-10) 2) Delivery Performance 3) Sales Trend 4) Risk Level 5) 3 Recommendations"""
            
            response = self._client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": "You are a business intelligence analyst."},
                         {"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=250
            )
            return self._parse_response(response.choices[0].message.content, dealer_data)
        except Exception as e:
            logger.warning(f"⚠️ AI summary failed: {e}")
            return self._fallback_summary(dealer_data)
    
    def _parse_response(self, response: str, data: Dict[str, Any]) -> Dict[str, Any]:
        result = {'health_score': 7, 'delivery_performance': 'Good', 'sales_trend': 'Stable', 'risk_level': 'Medium', 'recommendations': []}
        for line in response.split('\n'):
            line = line.strip()
            if 'Health' in line and ':' in line:
                try: result['health_score'] = int(re.search(r'\d+', line).group())
                except: pass
            elif 'Delivery' in line and ':' in line:
                result['delivery_performance'] = line.split(':')[-1].strip()
            elif 'Sales' in line and ':' in line:
                result['sales_trend'] = line.split(':')[-1].strip()
            elif 'Risk' in line and ':' in line:
                result['risk_level'] = line.split(':')[-1].strip()
            elif 'Recommendation' in line and not result['recommendations']:
                result['recommendations'].append(line)
        if not result['recommendations']:
            result['recommendations'] = self._default_recs(data)
        return result
    
    def _fallback_summary(self, data: Dict[str, Any]) -> Dict[str, Any]:
        delivery = data.get('delivery', {})
        sales = data.get('sales', {})
        delivery_rate = delivery.get('delivery_rate', 0)
        pending = delivery.get('pending_dn', 0)
        revenue = sales.get('total_revenue', 0)
        
        health = 8 if delivery_rate >= 90 else 6 if delivery_rate >= 75 else 4
        perf = "Excellent" if delivery_rate >= 90 else "Good" if delivery_rate >= 75 else "Fair"
        risk = "Low" if pending == 0 else "Medium" if pending < 10 else "High"
        
        return {
            'health_score': health,
            'delivery_performance': perf,
            'sales_trend': 'Stable' if revenue > 0 else 'Declining',
            'risk_level': risk,
            'recommendations': self._default_recs(data)
        }
    
    def _default_recs(self, data: Dict[str, Any]) -> List[str]:
        delivery = data.get('delivery', {})
        pending = delivery.get('pending_dn', 0)
        recs = []
        if pending > 0: recs.append(f"Resolve {pending} pending deliveries")
        if delivery.get('delivery_rate', 0) < 80: recs.append("Improve delivery performance")
        if delivery.get('pod_rate', 0) < 85: recs.append("Focus on POD completion")
        return recs[:3] if recs else ["Maintain current performance", "Monitor key metrics", "Explore growth opportunities"]

# ============================================================
# BLOCK 9: RESPONSE FORMATTER
# ============================================================

class ResponseFormatter:
    def __init__(self):
        self._renderer = DealerMenuRenderer()
    
    def format(self, answer: DealerAnswer) -> str:
        if answer.plan.format == ResponseFormat.METRIC:
            return self._format_metric(answer)
        elif answer.plan.format == ResponseFormat.COMPACT:
            return self._format_compact(answer)
        elif answer.plan.format == ResponseFormat.EXECUTIVE:
            return self._format_executive(answer)
        elif answer.plan.format == ResponseFormat.DETAILED:
            return self._format_detailed(answer)
        elif answer.plan.format == ResponseFormat.KPI_ONLY:
            return self._format_kpi_only(answer)
        elif answer.plan.format == ResponseFormat.COMPARISON:
            return self._format_comparison(answer)
        elif answer.plan.format == ResponseFormat.RANKING:
            return self._format_ranking(answer)
        else:
            return self._renderer.render_dealer_dashboard(answer.plan.dealer or "Dealer", answer.dashboard or {})
    
    def _format_metric(self, answer: DealerAnswer) -> str:
        dealer = answer.plan.dealer or "Dealer"
        lines = [f"📊 *{dealer}*"]
        for k, v in answer.metrics.items():
            lines.append(f"{k}: {v}")
        if answer.explanation:
            lines.append(""); lines.append(answer.explanation)
        return "\n".join(lines)
    
    def _format_compact(self, answer: DealerAnswer) -> str:
        dealer = answer.plan.dealer or "Dealer"
        lines = [f"📊 {dealer}", ""]
        for k, v in answer.metrics.items():
            lines.append(f"{k}: {v}")
        return "\n".join(lines)
    
    def _format_executive(self, answer: DealerAnswer) -> str:
        dealer = answer.plan.dealer or "Dealer"
        lines = [f"📋 *Executive Summary - {dealer}*", "", answer.explanation or "No summary available.", "", "📊 *Key Metrics:*"]
        for k, v in list(answer.metrics.items())[:5]:
            lines.append(f"• {k}: {v}")
        if answer.insights:
            lines.append(""); lines.append("💡 *Insights:*")
            for i in answer.insights[:2]:
                lines.append(f"• {i}")
        if answer.recommendations:
            lines.append(""); lines.append("🎯 *Recommendations:*")
            for r in answer.recommendations[:2]:
                lines.append(f"• {r}")
        return "\n".join(lines)
    
    def _format_detailed(self, answer: DealerAnswer) -> str:
        dealer = answer.plan.dealer or "Dealer"
        lines = [f"📊 *Detailed Analysis - {dealer}*", "", "📍 *Dealer Details*", "─" * 40]
        if answer.dashboard:
            identity = answer.dashboard.get('identity', {})
            lines.append(f"Dealer Code: {identity.get('dealer_code', 'N/A')}")
            lines.append(f"City: {identity.get('city', 'N/A')}")
            lines.append(f"Warehouse: {identity.get('warehouse', 'N/A')}")
        lines.append(""); lines.append("📈 *Metrics*"); lines.append("─" * 40)
        for k, v in answer.metrics.items():
            lines.append(f"{k}: {v}")
        if answer.insights:
            lines.append(""); lines.append("💡 *Insights*"); lines.append("─" * 40)
            for i in answer.insights:
                lines.append(f"• {i}")
        return "\n".join(lines)
    
    def _format_kpi_only(self, answer: DealerAnswer) -> str:
        dealer = answer.plan.dealer or "Dealer"
        lines = [f"📊 *{dealer} KPIs*:"]
        for k, v in answer.metrics.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)
    
    def _format_comparison(self, answer: DealerAnswer) -> str:
        return self._renderer.render_comparison_result(
            answer.plan.dealers[0] if answer.plan.dealers else "",
            answer.plan.dealers[1] if len(answer.plan.dealers) > 1 else "",
            answer.metrics
        )
    
    def _format_ranking(self, answer: DealerAnswer) -> str:
        ranking_data = answer.metrics.get("ranking", [])
        return self._renderer.render_ranking(ranking_data, answer.plan.sort_by or "revenue", answer.plan.limit)

# ============================================================
# BLOCK 10: MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    def __init__(self) -> None:
        self._version = VERSION
        self._renderer = DealerMenuRenderer()
        self._formatter = ResponseFormatter()
        self._ai_engine = AISummaryEngine()
        self._contexts: Dict[str, DealerContext] = {}
        self._context_lock = threading.RLock()
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        
        logger.info(f"✅ DealerAnalyticsService v{self._version} initialized")
        logger.info(f"   AI Engine: {'✅' if self._ai_engine._available else '❌'}")
        logger.info(f"   OpenRouteService: {'✅' if ORS_AVAILABLE and ORS_API_KEY else '❌'}")
        logger.info(f"   Match Threshold: {MATCH_THRESHOLD}%")
    
    def handle_message(self, message: str, sender: str) -> str:
        try:
            result = self.process_menu_input(sender, message)
            return result.get("response", self._renderer.render_main_menu())
        except Exception as e:
            logger.error(f"❌ Error: {e}", exc_info=True)
            return self._renderer.render_main_menu()
    
    def process_whatsapp_query(self, message: str, sender: str) -> str:
        return self.handle_message(message, sender)
    
    def get_main_menu(self) -> str:
        return self._renderer.render_main_menu()
    
    def _get_context(self, session_id: str) -> DealerContext:
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DealerContext()
            return self._contexts[session_id]
    
    def _calculate_match_score(self, search: str, target: str) -> float:
        if not search or not target:
            return 0.0
        search = search.lower().strip()
        target = target.lower().strip()
        if search == target:
            return 100.0
        
        search_words = set(search.split())
        target_words = set(target.split())
        if not search_words or not target_words:
            return 0.0
        
        common = search_words & target_words
        word_score = (len(common) / max(len(search_words), 1)) * 100
        
        s_first = search.split()[0] if search.split() else ""
        t_first = target.split()[0] if target.split() else ""
        bonus = 0
        if s_first and t_first:
            if s_first == t_first: bonus += 20
            elif s_first in t_first or t_first in s_first: bonus += 10
        if search in target: bonus += 10
        elif target in search: bonus += 5
        
        return round(min(100, word_score + bonus), 1)
    
    def _resolve_dealer_name(self, name: str) -> Optional[str]:
        if not name or not name.strip():
            return None
        if name.isdigit() and 1 <= int(name) <= 9:
            return None
        
        name_lower = name.strip().lower()
        logger.info(f"🔍 Searching: '{name_lower}' ({MATCH_THRESHOLD}% required)")
        
        try:
            with self._session() as session:
                dealers = session.query(DeliveryReport.customer_name).filter(
                    DeliveryReport.customer_name.isnot(None)
                ).distinct().all()
                dealer_names = [d.customer_name for d in dealers if d.customer_name]
                if not dealer_names:
                    return None
                
                # Exact match
                for d in dealer_names:
                    if d.lower() == name_lower:
                        logger.info(f"✅ EXACT MATCH: '{d}'")
                        return d
                
                # First word exact match
                search_first = name_lower.split()[0] if name_lower.split() else ""
                if len(search_first) >= 3:
                    for d in dealer_names:
                        d_first = d.lower().split()[0] if d.lower().split() else ""
                        if d_first == search_first:
                            score = self._calculate_match_score(name_lower, d.lower())
                            if score >= MATCH_THRESHOLD:
                                logger.info(f"✅ FIRST WORD ({score:.0f}%): '{d}'")
                                return d
                
                # Starts with
                for d in dealer_names:
                    d_lower = d.lower()
                    if d_lower.startswith(name_lower):
                        score = self._calculate_match_score(name_lower, d_lower)
                        if score >= MATCH_THRESHOLD:
                            logger.info(f"✅ STARTS WITH ({score:.0f}%): '{d}'")
                            return d
                
                # Word overlap
                search_words = set(name_lower.split())
                for d in dealer_names:
                    d_lower = d.lower()
                    d_words = set(d_lower.split())
                    if search_words and d_words:
                        common = search_words & d_words
                        if len(common) / max(len(search_words), 1) * 100 >= 90:
                            score = self._calculate_match_score(name_lower, d_lower)
                            if score >= MATCH_THRESHOLD:
                                logger.info(f"✅ WORD OVERLAP ({score:.0f}%): '{d}'")
                                return d
                
                # Fuzzy match (90%+)
                if RAPIDFUZZ_AVAILABLE:
                    try:
                        results = process.extract(name_lower, dealer_names, scorer=fuzz.token_set_ratio, limit=10)
                        for match, score, _ in results:
                            if score >= MATCH_THRESHOLD:
                                logger.info(f"✅ FUZZY ({score:.0f}%): '{match}'")
                                return match
                    except Exception:
                        pass
                
                logger.warning(f"⚠️ No {MATCH_THRESHOLD}%+ match found for '{name}'")
                return None
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return None
    
    def _get_suggestions(self, query: str, limit: int = 5) -> List[str]:
        if not query:
            return []
        query_lower = query.strip().lower()
        
        try:
            with self._session() as session:
                results = session.query(DeliveryReport.customer_name).filter(
                    DeliveryReport.customer_name.isnot(None)
                ).distinct().limit(200).all()
                dealer_names = [d.customer_name for d in results if d.customer_name]
                if not dealer_names:
                    return []
                
                scored = []
                for d in dealer_names:
                    d_lower = d.lower()
                    score = self._calculate_match_score(query_lower, d_lower)
                    if score >= 80:
                        scored.append((d, score))
                
                scored.sort(key=lambda x: x[1], reverse=True)
                return [d[0] for d in scored[:limit]]
        except Exception as e:
            logger.error(f"Error getting suggestions: {e}")
            return []
    
    def _get_correct_distance(self, warehouse: str, city: str) -> Dict[str, Any]:
        if not warehouse or not city:
            return {"distance_km": None, "estimated_delivery": "Unknown", "source": "Missing data"}
        
        warehouse_coord = _get_coordinates(warehouse)
        city_coord = _get_coordinates(city)
        
        if not warehouse_coord or not city_coord:
            return {"distance_km": None, "estimated_delivery": "Unknown", "source": "No coordinates"}
        
        if ORS_AVAILABLE and ORS_API_KEY:
            try:
                client = openrouteservice.Client(key=ORS_API_KEY)
                coords = [[warehouse_coord[1], warehouse_coord[0]], [city_coord[1], city_coord[0]]]
                routes = client.directions(coordinates=coords, profile=ORS_PROFILE, format='json')
                if routes and 'routes' in routes:
                    summary = routes['routes'][0].get('summary', {})
                    distance_km = summary.get('distance', 0) / 1000
                    duration_min = summary.get('duration', 0) / 60
                    
                    if distance_km <= 80: zone, est = "Local", "Same Day"
                    elif distance_km <= 200: zone, est = "Short Haul", "1 Day"
                    elif distance_km <= 400: zone, est = "Medium Haul", "2 Days"
                    elif distance_km <= 700: zone, est = "Long Haul", "3 Days"
                    else: zone, est = "Extended Haul", "4-5 Days"
                    
                    return {
                        "distance_km": round(distance_km, 1),
                        "duration_hours": round(duration_min / 60, 1),
                        "estimated_delivery": est,
                        "transportation_zone": zone,
                        "source": "OpenRouteService"
                    }
            except Exception:
                pass
        
        distance = _calculate_distance(warehouse_coord[0], warehouse_coord[1], city_coord[0], city_coord[1])
        if distance <= 80: zone, est = "Local", "Same Day"
        elif distance <= 200: zone, est = "Short Haul", "1 Day"
        elif distance <= 400: zone, est = "Medium Haul", "2 Days"
        elif distance <= 700: zone, est = "Long Haul", "3 Days"
        else: zone, est = "Extended Haul", "4-5 Days"
        
        return {
            "distance_km": round(distance, 1),
            "duration_hours": round(distance / 50, 1),
            "estimated_delivery": est,
            "transportation_zone": zone,
            "source": "Haversine (Fallback)"
        }
    
    def _get_dashboard(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if dashboard:
                    identity = dashboard.get('identity', {})
                    warehouse = identity.get('warehouse', '')
                    city = identity.get('city', '')
                    
                    if warehouse and city:
                        correct = self._get_correct_distance(warehouse, city)
                        dashboard['distance'] = correct
                        insights = [i for i in dashboard.get('insights', []) 
                                   if not i.startswith('Distance to dealer:') and not i.startswith('Estimated transit:')]
                        if correct.get('distance_km'):
                            insights.append(f"Distance to dealer: {correct['distance_km']} km")
                            insights.append(f"Estimated transit: {correct.get('estimated_delivery', 'N/A')}")
                        dashboard['insights'] = insights
                    
                    return {
                        "response": self._renderer.render_dealer_dashboard(dealer_name, dashboard),
                        "menu_type": "dealer_menu",
                        "action": "dashboard",
                        "data": {"dealer": dealer_name},
                        "exit_menu": False
                    }
                
                suggestions = self._get_suggestions(dealer_name)
                if suggestions:
                    text = "⚠️ *No data found.*\n\n💡 *Did you mean:*\n" + "\n".join(f"• {s}" for s in suggestions[:5])
                    return {"response": text + "\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "not_found", "data": {}, "exit_menu": False}
                
                return {"response": f"⚠️ No data found for: {dealer_name}\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "not_found", "data": {}, "exit_menu": False}
        except Exception as e:
            logger.error(f"❌ Dashboard error: {e}", exc_info=True)
            return {"response": f"⚠️ Error: {str(e)}\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "error", "data": {}, "exit_menu": False}
    
    def _handle_ranking(self, context: DealerContext) -> Dict[str, Any]:
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                ranking = repo.get_top_dealers_by_revenue(10)
                if not ranking:
                    return {"response": "📋 No data available.\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "ranking", "data": {}, "exit_menu": False}
                return {"response": self._renderer.render_ranking(ranking, "revenue", 10), "menu_type": "dealer_menu", "action": "ranking", "data": {"ranking": ranking}, "exit_menu": False}
        except Exception as e:
            logger.error(f"Ranking error: {e}")
            return {"response": f"⚠️ Error: {str(e)}\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "error", "data": {}, "exit_menu": False}
    
    def _compare_dealers(self, context: DealerContext, d1: str, d2: str) -> Dict[str, Any]:
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                data1 = builder.build(d1)
                data2 = builder.build(d2)
                if not data1 or not data2:
                    return {"response": "⚠️ Could not find data for one or both dealers.\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "error", "data": {}, "exit_menu": False}
                
                metrics = {}
                for dealer, data in [(d1, data1), (d2, data2)]:
                    delivery = data.get('delivery', {})
                    sales = data.get('sales', {})
                    perf = data.get('performance', {})
                    metrics[f"{dealer}_metrics"] = {
                        "Revenue": _format_currency(sales.get('total_revenue', 0)),
                        "Total DN": str(delivery.get('total_dn', 0)),
                        "Pending DN": str(delivery.get('pending_dn', 0)),
                        "Delivery Rate": f"{delivery.get('delivery_rate', 0):.1f}%",
                        "Business Score": f"{perf.get('business_score', 0):.1f}",
                    }
                
                s1 = data1.get('performance', {}).get('business_score', 0)
                s2 = data2.get('performance', {}).get('business_score', 0)
                metrics['explanation'] = f"{d1} ({s1:.1f}) vs {d2} ({s2:.1f})" + (" - Higher" if s1 > s2 else " - Lower" if s2 > s1 else " - Equal")
                
                return {"response": self._renderer.render_comparison_result(d1, d2, metrics), "menu_type": "dealer_menu", "action": "comparison", "data": {}, "exit_menu": False}
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            return {"response": f"⚠️ Error: {str(e)}\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "error", "data": {}, "exit_menu": False}
    
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        context = self._get_context(session_id)
        user_input = user_input.strip()
        
        if user_input in ["0", "99"]:
            context.clear()
            return {"response": self._renderer.render_main_menu(), "menu_type": "dealer_menu", "action": "main_menu", "data": {}, "exit_menu": True}
        
        if context.awaiting_dealer:
            dealer = self._resolve_dealer_name(user_input)
            if dealer:
                context.current_dealer = dealer
                context.awaiting_dealer = False
                return self._get_dashboard(context, dealer)
            
            suggestions = self._get_suggestions(user_input)
            if suggestions:
                text = "🔍 *Dealer not found.*\n\n💡 *Did you mean:*\n" + "\n".join(f"• {s}" for s in suggestions[:5])
                return {"response": text + "\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "dealer_selection", "data": {"awaiting": True}, "exit_menu": False}
            
            return {"response": self._renderer.render_dealer_selection(f"Dealer '{user_input}' not found. Try again:"), "menu_type": "dealer_menu", "action": "dealer_selection", "data": {"awaiting": True}, "exit_menu": False}
        
        if context.awaiting_comparison:
            resolved = self._resolve_dealer_name(user_input)
            if not resolved:
                suggestions = self._get_suggestions(user_input)
                if suggestions:
                    text = "🔍 *Dealer not found.*\n\n💡 *Did you mean:*\n" + "\n".join(f"• {s}" for s in suggestions[:5])
                    return {"response": text + "\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "comparison_selection", "data": {"awaiting": True}, "exit_menu": False}
                return {"response": self._renderer.render_comparison_selection() + f"\n\nDealer '{user_input}' not found. Try again:", "menu_type": "dealer_menu", "action": "comparison_selection", "data": {"awaiting": True}, "exit_menu": False}
            
            context.comparison_dealers.append(resolved)
            if len(context.comparison_dealers) == 1:
                return {"response": "Enter second dealer name:", "menu_type": "dealer_menu", "action": "comparison_selection", "data": {"awaiting": True}, "exit_menu": False}
            else:
                d1, d2 = context.comparison_dealers
                context.awaiting_comparison = False
                context.comparison_dealers = []
                return self._compare_dealers(context, d1, d2)
        
        if user_input.isdigit() and 1 <= int(user_input) <= 18:
            option_map = {
                "1": ("dashboard", "Enter dealer name:"), "2": ("revenue", "Enter dealer name:"),
                "3": ("units", "Enter dealer name:"), "4": ("logistics", "Enter dealer name:"),
                "5": ("warehouses", "Enter dealer name:"), "6": ("cities", "Enter dealer name:"),
                "7": ("pending_dn", "Enter dealer name:"), "8": ("pending_pgi", "Enter dealer name:"),
                "9": ("pending_pod", "Enter dealer name:"), "10": ("comparison", "Enter first dealer name:"),
                "11": ("ranking", ""), "12": ("trend", "Enter dealer name:"),
                "13": ("executive", "Enter dealer name:"), "14": ("ai_insights", "Enter dealer name:"),
                "15": ("recommendations", "Enter dealer name:"), "16": ("performance", "Enter dealer name:"),
                "17": ("score", "Enter dealer name:"), "18": ("search", "Enter search term:")
            }
            action, prompt = option_map.get(user_input, ("", ""))
            if action == "ranking":
                return self._handle_ranking(context)
            if action == "comparison":
                context.awaiting_comparison = True
                return {"response": self._renderer.render_comparison_selection(), "menu_type": "dealer_menu", "action": "comparison_selection", "data": {"awaiting": True}, "exit_menu": False}
            if action:
                context.awaiting_dealer = True
                context.selected_option = action
                return {"response": self._renderer.render_dealer_selection(prompt), "menu_type": "dealer_menu", "action": "dealer_selection", "data": {"awaiting": True, "option": action}, "exit_menu": False}
        
        if context.selected_option == "search":
            try:
                with self._session() as session:
                    repo = DealerRepository(session)
                    results = repo.search_dealers(user_input)
                    if not results:
                        suggestions = self._get_suggestions(user_input)
                        if suggestions:
                            text = "🔍 No results.\n\n💡 *Did you mean:*\n" + "\n".join(f"• {s}" for s in suggestions[:5])
                            return {"response": text + "\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "search", "data": {}, "exit_menu": False}
                        return {"response": f"🔍 No dealers found matching '{user_input}'\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "search", "data": {}, "exit_menu": False}
                    lines = [f"🔍 *Results for '{user_input}'*", ""]
                    for i, r in enumerate(results[:10], 1):
                        lines.append(f"{i}. {r.get('dealer', 'Unknown')}")
                        lines.append(f"   City: {r.get('city', 'N/A')}")
                        lines.append(f"   Warehouse: {r.get('warehouse', 'N/A')}")
                        lines.append("")
                    if len(results) > 10:
                        lines.append(f"... and {len(results) - 10} more")
                    lines.extend(["", "0. Main Menu", "99. Back"])
                    return {"response": "\n".join(lines), "menu_type": "dealer_menu", "action": "search", "data": {}, "exit_menu": False}
            except Exception as e:
                logger.error(f"Search error: {e}")
                return {"response": f"⚠️ Error: {str(e)}\n\n0. Main Menu\n99. Back", "menu_type": "dealer_menu", "action": "error", "data": {}, "exit_menu": False}
        
        dealer = self._resolve_dealer_name(user_input)
        if dealer:
            context.current_dealer = dealer
            return self._get_dashboard(context, dealer)
        
        if "top dealers" in user_input.lower() or "ranking" in user_input.lower():
            return self._handle_ranking(context)
        
        return {"response": self._renderer.render_main_menu(), "menu_type": "dealer_menu", "action": "main_menu", "data": {}, "exit_menu": False}
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()

# ============================================================
# BLOCK 11: SINGLETON & EXPORTS
# ============================================================

_dealer_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    global _dealer_service
    if _dealer_service is None:
        _dealer_service = DealerAnalyticsService()
    return _dealer_service

__all__ = [
    "DealerAnalyticsService",
    "get_dealer_service",
    "DealerContext",
    "DealerAnswer",
    "IntentType",
    "MenuState",
    "VERSION"
]
