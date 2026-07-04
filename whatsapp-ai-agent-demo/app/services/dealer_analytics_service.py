# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 7.0 - ENTERPRISE DEALER INTELLIGENCE PLATFORM
# ============================================================

"""
File: app/services/dealer_analytics_service.py
Version: 7.0 - ENTERPRISE DEALER INTELLIGENCE PLATFORM

================================================================================
PURPOSE
================================================================================

This is a complete Enterprise Dealer Intelligence Platform with:

1. Universal Dealer Detection Engine (Phase 1)
2. Dealer Master Profile (Phase 2)
3. Dealer Intelligence Dashboard - 360° View (Phase 3)
4. Dealer KPI Engine (Phase 4)
5. Dealer Ranking Engine (Phase 5)
6. Dealer Timeline (Phase 6)
7. Dealer Trend Engine (Phase 7)
8. Dealer Relationship Engine (Phase 8)
9. Dealer Recommendation Engine (Phase 9)
10. Dealer Question Library - 150+ Questions (Phase 10)
11. Smart Query Recognition (Phase 11)
12. Complete 360° Dealer Intelligence Report (Phase 12)

================================================================================
ARCHITECTURE
================================================================================

User Input
    │
    ▼
Universal Dealer Detection Engine
    │
    ├── Normalize Text
    ├── Dealer Dictionary (In-Memory Cache)
    ├── Exact Match
    ├── Alias Match
    ├── Fuzzy Match
    └── Dealer Confidence
    │
    ▼
Dealer Master Profile
    │
    ├── Identity
    ├── Financials
    ├── Operations
    ├── Delivery
    ├── Warehouses
    ├── Cities
    ├── Products
    ├── Distance
    └── Rankings
    │
    ▼
Dealer Intelligence Dashboard (360° View)
    │
    └── 70-100 Business Attributes
    │
    ▼
Response Formatter (WhatsApp)

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple, Union, Callable, Set
from functools import lru_cache

logger = logging.getLogger(__name__)

# ============================================================
# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 7.1 - ENTERPRISE DEALER INTELLIGENCE PLATFORM
# ============================================================

"""
File: app/services/dealer_analytics_service.py
Version: 7.1 - ENTERPRISE DEALER INTELLIGENCE PLATFORM

================================================================================
IMPROVEMENTS IN v7.1
================================================================================

BLOCK 1: Dealer Dictionary - _load_dealers() - Better normalization and aliases
BLOCK 2: Dealer Detection - detect_dealer() - Improved fuzzy and partial matching
BLOCK 3: Main Processing - process_whatsapp_query() - Multiple detection attempts
BLOCK 4: Search Dealers - search_dealers() - Better search with fuzzy matching
BLOCK 5: Profile Builder - build_profile() - Multiple detection strategies

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple, Union, Callable, Set
from functools import lru_cache

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: AI LIBRARIES
# ============================================================

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

try:
    from semantic_router import Route, SemanticRouter
    SEMANTIC_ROUTER_AVAILABLE = True
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False

try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
except ImportError:
    TEXTBLOB_AVAILABLE = False

try:
    import nltk
    from nltk.tokenize import word_tokenize
    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

# ============================================================
# BLOCK 2: DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, desc, asc, and_, case, text
    from sqlalchemy.orm import Session
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
    logger.info("✅ Dealer database imports successful")
except ImportError as e:
    DB_AVAILABLE = False
    logger.error(f"❌ Dealer database import error: {e}")

# ============================================================
# BLOCK 3: CONFIGURATION
# ============================================================

DEALER_CACHE_TTL = int(os.getenv("DEALER_CACHE_TTL", "600"))
DEALER_SESSION_TIMEOUT = int(os.getenv("DEALER_SESSION_TIMEOUT", "1800"))
DEALER_AI_ENABLED = os.getenv("DEALER_AI_ENABLED", "true").lower() == "true"
DEALER_SEMANTIC_ENABLED = os.getenv("DEALER_SEMANTIC_ENABLED", "true").lower() == "true"
DEALER_MENU_AUTO_SHOW = os.getenv("DEALER_MENU_AUTO_SHOW", "true").lower() == "true"
DEALER_DICTIONARY_REFRESH = int(os.getenv("DEALER_DICTIONARY_REFRESH", "3600"))

# AI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Distance Calculation
USE_ROUTE_DISTANCE = os.getenv("USE_ROUTE_DISTANCE", "false").lower() == "true"
USE_HAVERSINE_FALLBACK = os.getenv("USE_HAVERSINE_FALLBACK", "true").lower() == "true"

# Warehouse Coordinates (for distance calculation)
WAREHOUSE_COORDINATES: Dict[str, Tuple[float, float]] = {
    "lahore": (31.5204, 74.3587),
    "karachi": (24.8607, 67.0011),
    "rawalpindi": (33.5651, 73.0169),
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
    "gilgit": (35.9208, 74.3144),
    "islamabad": (33.6844, 73.0479),
}

# City Coordinates (for dealer location)
CITY_COORDINATES: Dict[str, Tuple[float, float]] = {
    "lahore": (31.5204, 74.3587),
    "karachi": (24.8607, 67.0011),
    "rawalpindi": (33.5651, 73.0169),
    "islamabad": (33.6844, 73.0479),
    "multan": (30.1575, 71.5249),
    "peshawar": (34.0151, 71.5249),
    "quetta": (30.1798, 66.9750),
    "hyderabad": (25.3960, 68.3578),
    "faisalabad": (31.4504, 73.1350),
    "sialkot": (32.4945, 74.5229),
    "gujranwala": (32.1617, 74.1883),
    "bahawalpur": (29.3956, 71.6836),
    "sukkur": (27.7060, 68.8530),
    "rahim yar khan": (28.4200, 70.3030),
    "abbottabad": (34.1490, 73.2210),
    "gwadar": (25.1260, 62.3250),
    "gilgit": (35.9208, 74.3144),
}

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

class DealerIntent(Enum):
    """Dealer intent types"""
    DASHBOARD = "dashboard"
    INTELLIGENCE = "intelligence"
    REVENUE = "revenue"
    UNITS = "units"
    DN = "dn"
    PENDING = "pending"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    DELIVERED = "delivered"
    DELIVERY = "delivery"
    PRODUCTS = "products"
    MODELS = "models"
    MATERIALS = "materials"
    DIVISIONS = "divisions"
    CATEGORIES = "categories"
    WAREHOUSES = "warehouses"
    CITIES = "cities"
    DISTANCE = "distance"
    ROUTE = "route"
    SALES_OFFICE = "sales_office"
    SALES_MANAGER = "sales_manager"
    PERFORMANCE = "performance"
    BUSINESS_SCORE = "business_score"
    HEALTH = "health"
    RISK = "risk"
    RANKING = "ranking"
    COMPARISON = "comparison"
    TREND = "trend"
    GROWTH = "growth"
    FORECAST = "forecast"
    TIMELINE = "timeline"
    HISTORY = "history"
    ACTIVITY = "activity"
    REVENUE_BREAKDOWN = "revenue_breakdown"
    UNIT_BREAKDOWN = "unit_breakdown"
    PRODUCT_MIX = "product_mix"
    COMPLETE_REPORT = "complete_report"
    INSIGHTS = "insights"
    RECOMMENDATIONS = "recommendations"
    KPI = "kpi"
    SEARCH = "search"
    MENU = "menu"
    HELP = "help"
    EXIT = "exit"
    UNKNOWN = "unknown"

class DealerMenuState(Enum):
    """Dealer menu states"""
    MAIN = "main"
    DASHBOARD = "dashboard"
    ANALYTICS = "analytics"
    INTELLIGENCE = "intelligence"
    AI_ASSISTANT = "ai_assistant"
    DEALER_SELECTED = "dealer_selected"
    COMPARISON = "comparison"
    SEARCH_RESULTS = "search_results"
    RANKING = "ranking"

# ============================================================
# BLOCK 5: DATA CLASSES
# ============================================================

@dataclass
class DealerProfile:
    """Complete Dealer Master Profile"""
    # Identity
    dealer_name: str = ""
    dealer_code: str = ""
    customer_code: str = ""
    sales_office: str = ""
    sales_manager: str = ""
    division: str = ""
    business_category: str = ""
    region: str = ""
    zone: str = ""
    
    # Location
    primary_warehouse: str = ""
    primary_city: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    city_coordinates: Tuple[float, float] = (0.0, 0.0)
    
    # Financial
    total_revenue: float = 0.0
    avg_revenue: float = 0.0
    monthly_revenue: float = 0.0
    yearly_revenue: float = 0.0
    revenue_growth: float = 0.0
    revenue_rank: int = 0
    revenue_contribution_pct: float = 0.0
    
    # Operations
    total_dn: int = 0
    total_units: int = 0
    avg_units_per_dn: float = 0.0
    avg_revenue_per_dn: float = 0.0
    product_count: int = 0
    model_count: int = 0
    material_count: int = 0
    
    # Delivery
    delivered_dn: int = 0
    pending_dn: int = 0
    pending_pgi: int = 0
    pending_pod: int = 0
    delivery_pct: float = 0.0
    pgi_pct: float = 0.0
    pod_pct: float = 0.0
    avg_delivery_days: float = 0.0
    avg_pod_days: float = 0.0
    oldest_pending: str = ""
    latest_delivery: str = ""
    latest_pod: str = ""
    
    # Warehouse Intelligence
    warehouses_used: List[str] = field(default_factory=list)
    primary_warehouse_name: str = ""
    warehouse_count: int = 0
    warehouse_revenue: float = 0.0
    warehouse_units: float = 0.0
    warehouse_rank: int = 0
    
    # City Intelligence
    cities_served: List[str] = field(default_factory=list)
    primary_city_name: str = ""
    city_count: int = 0
    city_revenue: float = 0.0
    city_rank: int = 0
    
    # Product Intelligence
    top_product: str = ""
    bottom_product: str = ""
    top_model: str = ""
    bottom_model: str = ""
    top_material: str = ""
    top_division: str = ""
    product_mix: Dict[str, float] = field(default_factory=dict)
    category_mix: Dict[str, float] = field(default_factory=dict)
    
    # Distance Intelligence
    dealer_lat: float = 0.0
    dealer_lon: float = 0.0
    warehouse_lat: float = 0.0
    warehouse_lon: float = 0.0
    actual_distance_km: float = 0.0
    estimated_distance_km: float = 0.0
    travel_time_minutes: int = 0
    avg_lead_distance: float = 0.0
    longest_route_km: float = 0.0
    shortest_route_km: float = 0.0
    transport_zone: str = ""
    
    # Rankings
    revenue_rank: int = 0
    unit_rank: int = 0
    dn_rank: int = 0
    delivery_rank: int = 0
    warehouse_rank: int = 0
    distance_rank: int = 0
    growth_rank: int = 0
    overall_rank: int = 0
    
    # Timeline
    first_order: str = ""
    last_order: str = ""
    first_dn: str = ""
    first_pgi: str = ""
    first_pod: str = ""
    latest_dn: str = ""
    latest_pgi: str = ""
    latest_pod_date: str = ""
    latest_activity: str = ""
    
    # KPI Scores
    revenue_score: float = 0.0
    delivery_score: float = 0.0
    pgi_score: float = 0.0
    pod_score: float = 0.0
    growth_score: float = 0.0
    warehouse_score: float = 0.0
    distance_score: float = 0.0
    product_mix_score: float = 0.0
    volume_score: float = 0.0
    customer_score: float = 0.0
    business_score: float = 0.0
    risk_score: float = 0.0
    
    # Recommendations
    recommendations: List[str] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for formatting"""
        return {
            'dealer_name': self.dealer_name,
            'dealer_code': self.dealer_code,
            'customer_code': self.customer_code,
            'sales_office': self.sales_office,
            'sales_manager': self.sales_manager,
            'division': self.division,
            'business_category': self.business_category,
            'region': self.region,
            'zone': self.zone,
            'primary_warehouse': self.primary_warehouse,
            'primary_city': self.primary_city,
            'total_revenue': self.total_revenue,
            'avg_revenue': self.avg_revenue,
            'monthly_revenue': self.monthly_revenue,
            'yearly_revenue': self.yearly_revenue,
            'revenue_growth': self.revenue_growth,
            'revenue_rank': self.revenue_rank,
            'revenue_contribution_pct': self.revenue_contribution_pct,
            'total_dn': self.total_dn,
            'total_units': self.total_units,
            'avg_units_per_dn': self.avg_units_per_dn,
            'avg_revenue_per_dn': self.avg_revenue_per_dn,
            'product_count': self.product_count,
            'model_count': self.model_count,
            'material_count': self.material_count,
            'delivered_dn': self.delivered_dn,
            'pending_dn': self.pending_dn,
            'pending_pgi': self.pending_pgi,
            'pending_pod': self.pending_pod,
            'delivery_pct': self.delivery_pct,
            'pgi_pct': self.pgi_pct,
            'pod_pct': self.pod_pct,
            'avg_delivery_days': self.avg_delivery_days,
            'avg_pod_days': self.avg_pod_days,
            'oldest_pending': self.oldest_pending,
            'latest_delivery': self.latest_delivery,
            'latest_pod': self.latest_pod,
            'warehouses_used': self.warehouses_used,
            'primary_warehouse_name': self.primary_warehouse_name,
            'warehouse_count': self.warehouse_count,
            'warehouse_revenue': self.warehouse_revenue,
            'warehouse_units': self.warehouse_units,
            'warehouse_rank': self.warehouse_rank,
            'cities_served': self.cities_served,
            'primary_city_name': self.primary_city_name,
            'city_count': self.city_count,
            'city_revenue': self.city_revenue,
            'city_rank': self.city_rank,
            'top_product': self.top_product,
            'bottom_product': self.bottom_product,
            'top_model': self.top_model,
            'bottom_model': self.bottom_model,
            'top_material': self.top_material,
            'top_division': self.top_division,
            'distance_km': self.actual_distance_km,
            'estimated_distance_km': self.estimated_distance_km,
            'travel_time_minutes': self.travel_time_minutes,
            'avg_lead_distance': self.avg_lead_distance,
            'longest_route_km': self.longest_route_km,
            'shortest_route_km': self.shortest_route_km,
            'transport_zone': self.transport_zone,
            'revenue_rank': self.revenue_rank,
            'unit_rank': self.unit_rank,
            'dn_rank': self.dn_rank,
            'delivery_rank': self.delivery_rank,
            'warehouse_rank': self.warehouse_rank,
            'distance_rank': self.distance_rank,
            'growth_rank': self.growth_rank,
            'overall_rank': self.overall_rank,
            'first_order': self.first_order,
            'last_order': self.last_order,
            'first_dn': self.first_dn,
            'first_pgi': self.first_pgi,
            'first_pod': self.first_pod,
            'latest_dn': self.latest_dn,
            'latest_pgi': self.latest_pgi,
            'latest_pod_date': self.latest_pod_date,
            'latest_activity': self.latest_activity,
            'revenue_score': self.revenue_score,
            'delivery_score': self.delivery_score,
            'pgi_score': self.pgi_score,
            'pod_score': self.pod_score,
            'growth_score': self.growth_score,
            'warehouse_score': self.warehouse_score,
            'distance_score': self.distance_score,
            'product_mix_score': self.product_mix_score,
            'volume_score': self.volume_score,
            'customer_score': self.customer_score,
            'business_score': self.business_score,
            'risk_score': self.risk_score,
        }

@dataclass
class DealerSession:
    """Dealer session state"""
    session_id: str
    locked: bool = True
    current_dealer: Optional[str] = None
    current_dealer_code: Optional[str] = None
    current_profile: Optional[DealerProfile] = None
    menu_state: DealerMenuState = DealerMenuState.MAIN
    selected_option: Optional[str] = None
    comparison_dealers: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    last_query: str = ""
    last_answer: str = ""
    last_intent: Optional[DealerIntent] = None
    last_sql: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    filters: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    menu_shown: bool = False
    
    def touch(self):
        self.updated_at = datetime.now()
    
    def is_expired(self, timeout: int = DEALER_SESSION_TIMEOUT) -> bool:
        elapsed = (datetime.now() - self.updated_at).total_seconds()
        return elapsed > timeout
    
    def add_history(self, query: str, answer: str):
        self.history.append({
            "query": query,
            "answer": answer[:200] if len(answer) > 200 else answer,
            "intent": self.last_intent.value if self.last_intent else None,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]
        self.last_query = query
        self.last_answer = answer
        self.touch()
    
    def set_dealer(self, name: str, code: Optional[str] = None, profile: Optional[DealerProfile] = None):
        self.current_dealer = name
        self.current_dealer_code = code
        self.current_profile = profile
        self.menu_state = DealerMenuState.DEALER_SELECTED
        self.touch()
    
    def clear(self):
        self.current_dealer = None
        self.current_dealer_code = None
        self.current_profile = None
        self.menu_state = DealerMenuState.MAIN
        self.comparison_dealers = []
        self.filters = {}
        self.context = {}
        self.menu_shown = False
        self.touch()

@dataclass
class DealerDetectionResult:
    """Result from Universal Dealer Detection Engine"""
    dealer_name: str
    dealer_code: str
    confidence: float
    match_type: str  # exact, alias, fuzzy, normalized, partial_word, phrase_match
    profile: Optional[DealerProfile] = None

# ============================================================
# BLOCK 6: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _format_currency(amount: float) -> str:
    if amount is None:
        return "PKR 0.00"
    if amount >= 1_000_000_000_000:
        return f"PKR {amount/1_000_000_000_000:,.2f} Trillion"
    elif amount >= 1_000_000_000:
        return f"PKR {amount/1_000_000_000:,.2f} Billion"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:,.2f} Million"
    else:
        return f"PKR {amount:,.2f}"

def _format_number(num: Union[int, float]) -> str:
    if num is None:
        return "0"
    return f"{num:,}"

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def _growth(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)

def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance using Haversine formula"""
    R = 6371  # Earth's radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _get_city_coordinates(city_name: str) -> Optional[Tuple[float, float]]:
    """Get coordinates for a city"""
    return CITY_COORDINATES.get(city_name.lower())

def _get_warehouse_coordinates(warehouse_name: str) -> Optional[Tuple[float, float]]:
    """Get coordinates for a warehouse"""
    return WAREHOUSE_COORDINATES.get(warehouse_name.lower())

def _get_transport_zone(distance_km: float) -> str:
    """Get transport zone based on distance"""
    if distance_km <= 50:
        return "Local (≤50 km)"
    elif distance_km <= 150:
        return "Regional (51-150 km)"
    elif distance_km <= 300:
        return "National (151-300 km)"
    elif distance_km <= 500:
        return "Extended (301-500 km)"
    else:
        return "Long Haul (>500 km)"

# ============================================================
# BLOCK 7: UNIVERSAL DEALER DETECTION ENGINE (Phase 1)
# ============================================================

class DealerDictionary:
    """
    Universal Dealer Detection Engine - IMPROVED BLOCK 1 & 2
    
    - Loads all dealers into memory on startup
    - Supports exact, alias, normalized, and fuzzy matching
    - Caches dealer information for fast lookup
    - Handles multi-word dealer names
    """
    
    _instance: Optional["DealerDictionary"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._dealers: Dict[str, Dict[str, Any]] = {}
        self._aliases: Dict[str, str] = {}
        self._normalized: Dict[str, str] = {}
        self._last_refresh: Optional[datetime] = None
        self._refresh_lock = threading.RLock()
        
        self._load_dealers()
        
        logger.info(f"📚 Dealer Dictionary loaded: {len(self._dealers)} dealers, {len(self._aliases)} aliases")
    
    # ============================================================
    # BLOCK 1: IMPROVED _load_dealers() - Better normalization and aliases
    # ============================================================
    
    def _load_dealers(self):
        """Load all dealers from database into memory with better normalization"""
        if not DB_AVAILABLE:
            logger.warning("⚠️ Database not available for dealer dictionary")
            return
        
        try:
            session = SessionLocal()
            
            # Get all unique dealers with better data
            results = session.query(
                DeliveryReport.customer_name.label('dealer_name'),
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse
            ).all()
            
            session.close()
            
            # Clear existing data
            self._dealers.clear()
            self._aliases.clear()
            self._normalized.clear()
            
            for row in results:
                dealer_name = _text(row.dealer_name)
                if dealer_name and dealer_name != "N/A":
                    # Store dealer info
                    dealer_key = dealer_name.lower()
                    self._dealers[dealer_key] = {
                        'dealer_name': dealer_name,
                        'dealer_code': _text(row.dealer_code),
                        'customer_code': _text(row.customer_code),
                        'sales_office': _text(row.sales_office),
                        'sales_manager': _text(row.sales_manager),
                        'division': _text(row.division),
                        'primary_city': _text(row.ship_to_city),
                        'primary_warehouse': _text(row.warehouse),
                        'dn_count': int(row.dn_count or 0),
                        'total_revenue': float(row.total_revenue or 0),
                    }
                    
                    # Build normalized name (remove extra spaces, special chars)
                    normalized = re.sub(r'[^a-zA-Z0-9\s]', '', dealer_name).lower().strip()
                    normalized = re.sub(r'\s+', ' ', normalized)  # Remove extra spaces
                    self._normalized[normalized] = dealer_name
                    
                    # Build aliases from each word (for multi-word dealer names)
                    words = dealer_name.split()
                    for word in words:
                        word_clean = re.sub(r'[^a-zA-Z0-9]', '', word).lower()
                        if len(word_clean) >= 2:
                            self._aliases[word_clean] = dealer_name
                    
                    # Add dealer code as alias
                    if row.dealer_code:
                        code_clean = re.sub(r'[^a-zA-Z0-9]', '', row.dealer_code).lower()
                        if code_clean:
                            self._aliases[code_clean] = dealer_name
                    
                    # Add customer code as alias
                    if row.customer_code:
                        customer_clean = re.sub(r'[^a-zA-Z0-9]', '', row.customer_code).lower()
                        if customer_clean:
                            self._aliases[customer_clean] = dealer_name
                    
                    # Add combined aliases (e.g., "RubaDigital" from "Ruba Digital")
                    combined = re.sub(r'\s+', '', dealer_name).lower()
                    if combined != dealer_key:
                        self._aliases[combined] = dealer_name
            
            self._last_refresh = datetime.now()
            logger.info(f"✅ Dealer Dictionary loaded: {len(self._dealers)} dealers, {len(self._aliases)} aliases")
            
        except Exception as e:
            logger.error(f"Failed to load dealer dictionary: {e}")
    
    def refresh(self):
        """Refresh the dealer dictionary"""
        with self._refresh_lock:
            self._load_dealers()
    
    # ============================================================
    # BLOCK 2: IMPROVED detect_dealer() - Better fuzzy and partial matching
    # ============================================================
    
    def detect_dealer(self, text: str) -> Optional[DealerDetectionResult]:
        """
        Detect dealer from text using Universal Detection Engine
        
        Priority:
        1. Exact Match
        2. Normalized Match
        3. Alias Match
        4. Fuzzy Match (RapidFuzz)
        5. Partial Word Match
        6. Consecutive Words Match
        7. Combined Words Match
        """
        if not text or not text.strip():
            return None
        
        text_clean = text.strip().lower()
        
        # Check if dictionary needs refresh
        if self._last_refresh and (datetime.now() - self._last_refresh).seconds > DEALER_DICTIONARY_REFRESH:
            self.refresh()
        
        # ============================================================
        # 1. EXACT MATCH
        # ============================================================
        if text_clean in self._dealers:
            dealer_info = self._dealers[text_clean]
            return DealerDetectionResult(
                dealer_name=dealer_info['dealer_name'],
                dealer_code=dealer_info.get('dealer_code', ''),
                confidence=1.0,
                match_type="exact"
            )
        
        # ============================================================
        # 2. NORMALIZED MATCH (remove spaces, special chars)
        # ============================================================
        normalized_clean = re.sub(r'[^a-zA-Z0-9]', '', text_clean)
        if normalized_clean in self._aliases:
            dealer_name = self._aliases[normalized_clean]
            dealer_info = self._dealers.get(dealer_name.lower(), {})
            return DealerDetectionResult(
                dealer_name=dealer_name,
                dealer_code=dealer_info.get('dealer_code', ''),
                confidence=0.95,
                match_type="normalized"
            )
        
        # ============================================================
        # 3. ALIAS MATCH
        # ============================================================
        if text_clean in self._aliases:
            dealer_name = self._aliases[text_clean]
            dealer_info = self._dealers.get(dealer_name.lower(), {})
            return DealerDetectionResult(
                dealer_name=dealer_name,
                dealer_code=dealer_info.get('dealer_code', ''),
                confidence=0.90,
                match_type="alias"
            )
        
        # ============================================================
        # 4. FUZZY MATCH (using RapidFuzz)
        # ============================================================
        if RAPIDFUZZ_AVAILABLE:
            best_match = None
            best_score = 0.0
            
            for dealer_name in self._dealers.keys():
                # Try WRatio for better matching
                score = fuzz.WRatio(text_clean, dealer_name)
                if score > best_score:
                    best_score = score
                    best_match = dealer_name
            
            # Lower threshold to 65 for better detection
            if best_score > 65:
                dealer_info = self._dealers[best_match]
                return DealerDetectionResult(
                    dealer_name=dealer_info['dealer_name'],
                    dealer_code=dealer_info.get('dealer_code', ''),
                    confidence=best_score / 100.0,
                    match_type="fuzzy"
                )
        
        # ============================================================
        # 5. PARTIAL WORD MATCH (for multi-word dealer names)
        # ============================================================
        words = text_clean.split()
        significant_words = [w for w in words if len(w) > 2]
        
        # Try to match the entire word sequence first
        for word in significant_words:
            for dealer_name in self._dealers.keys():
                dealer_words = dealer_name.split()
                # Check if any word matches
                for dealer_word in dealer_words:
                    if len(dealer_word) >= 3:
                        # Check if word is in dealer name or vice versa
                        if word in dealer_word or dealer_word in word:
                            dealer_info = self._dealers[dealer_name]
                            confidence = max(len(word), len(dealer_word)) / max(len(dealer_name), len(text_clean))
                            return DealerDetectionResult(
                                dealer_name=dealer_info['dealer_name'],
                                dealer_code=dealer_info.get('dealer_code', ''),
                                confidence=min(0.85, confidence + 0.3),
                                match_type="partial_word"
                            )
        
        # ============================================================
        # 6. CONSECUTIVE WORDS MATCH (e.g., "Ruba Digital" from "Ruba Digital Wah")
        # ============================================================
        if len(words) >= 2:
            for i in range(len(words) - 1):
                phrase = f"{words[i]} {words[i+1]}"
                for dealer_name in self._dealers.keys():
                    if phrase in dealer_name:
                        dealer_info = self._dealers[dealer_name]
                        confidence = len(phrase) / len(dealer_name)
                        return DealerDetectionResult(
                            dealer_name=dealer_info['dealer_name'],
                            dealer_code=dealer_info.get('dealer_code', ''),
                            confidence=min(0.85, confidence + 0.4),
                            match_type="phrase_match"
                        )
        
        # ============================================================
        # 7. COMBINED WORDS MATCH (e.g., "Rubadigital" from "Ruba Digital")
        # ============================================================
        combined_clean = re.sub(r'\s+', '', text_clean)
        if combined_clean in self._aliases:
            dealer_name = self._aliases[combined_clean]
            dealer_info = self._dealers.get(dealer_name.lower(), {})
            return DealerDetectionResult(
                dealer_name=dealer_name,
                dealer_code=dealer_info.get('dealer_code', ''),
                confidence=0.80,
                match_type="combined"
            )
        
        # ============================================================
        # 8. NO MATCH FOUND
        # ============================================================
        logger.info(f"❌ No dealer found for: '{text}'")
        return None
    
    # ============================================================
    # BLOCK 4: search_dealers() - Better search with fuzzy matching
    # ============================================================
    
    def search_dealers(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search dealers by query with improved matching"""
        results = []
        query_lower = query.lower()
        
        # First, try exact matches
        for dealer_name, info in self._dealers.items():
            if query_lower in dealer_name or query_lower in info.get('dealer_code', '').lower():
                results.append(info)
                if len(results) >= limit:
                    return results
        
        # Then try fuzzy matches
        if RAPIDFUZZ_AVAILABLE and len(results) < limit:
            scored_results = []
            for dealer_name, info in self._dealers.items():
                score = fuzz.WRatio(query_lower, dealer_name)
                if score > 50:
                    scored_results.append((score, info))
            
            scored_results.sort(key=lambda x: x[0], reverse=True)
            for score, info in scored_results[:limit]:
                if info not in results:
                    results.append(info)
                    if len(results) >= limit:
                        break
        
        return results
    
    def get_dealer_info(self, dealer_name: str) -> Optional[Dict[str, Any]]:
        """Get dealer info by name"""
        return self._dealers.get(dealer_name.lower())
    
    def get_all_dealers(self) -> List[str]:
        """Get all dealer names"""
        return [info['dealer_name'] for info in self._dealers.values()]

# ============================================================
# BLOCK 8: DEALER MASTER PROFILE BUILDER (Phase 2 & 3)
# ============================================================

class DealerProfileBuilder:
    """
    Builds complete Dealer Master Profile with 70-100 attributes
    
    BLOCK 5: IMPROVED build_profile() - Multiple detection strategies
    """
    
    def __init__(self, session: Session):
        self.session = session
        self._dealer_dict = DealerDictionary()
    
    # ============================================================
    # BLOCK 5: IMPROVED build_profile() - Multiple detection strategies
    # ============================================================
    
    def build_profile(self, dealer_identifier: str) -> Optional[DealerProfile]:
        """Build complete dealer profile with better matching"""
        # Detect dealer first
        detection = self._dealer_dict.detect_dealer(dealer_identifier)
        
        # If detection failed, try multiple strategies
        if not detection:
            # Try with original text after removing common suffixes
            normalized_msg = re.sub(r'\s+(wah|store|shop|center|centre|digital|electronics|appliances|enterprise)$', '', dealer_identifier, flags=re.IGNORECASE)
            if normalized_msg != dealer_identifier:
                detection = self._dealer_dict.detect_dealer(normalized_msg)
            
            # If still failed, try with first significant words
            if not detection:
                words = dealer_identifier.split()
                for i in range(min(3, len(words))):
                    test_name = " ".join(words[:i+1])
                    detection = self._dealer_dict.detect_dealer(test_name)
                    if detection:
                        break
            
            # If still failed, try with combined words
            if not detection:
                combined = re.sub(r'\s+', '', dealer_identifier)
                detection = self._dealer_dict.detect_dealer(combined)
        
        if not detection:
            logger.warning(f"⚠️ Could not detect dealer: {dealer_identifier}")
            return None
        
        dealer_name = detection.dealer_name
        dealer_code = detection.dealer_code
        
        logger.info(f"🔍 Building profile for: {dealer_name} (confidence: {detection.confidence:.2f}, match: {detection.match_type})")
        
        # Get base data
        base_data = self._get_base_data(dealer_name, dealer_code)
        if not base_data:
            logger.warning(f"⚠️ No base data found for: {dealer_name}")
            return None
        
        profile = DealerProfile()
        
        # ============================================================
        # Identity
        # ============================================================
        profile.dealer_name = dealer_name
        profile.dealer_code = dealer_code
        profile.customer_code = base_data.get('customer_code', '')
        profile.sales_office = base_data.get('sales_office', '')
        profile.sales_manager = base_data.get('sales_manager', '')
        profile.division = base_data.get('division', '')
        
        # ============================================================
        # Location
        # ============================================================
        profile.primary_city = base_data.get('primary_city', '')
        profile.primary_warehouse = base_data.get('primary_warehouse', '')
        
        # Get coordinates
        city_coords = _get_city_coordinates(profile.primary_city)
        if city_coords:
            profile.latitude, profile.longitude = city_coords
            profile.city_coordinates = city_coords
        
        warehouse_coords = _get_warehouse_coordinates(profile.primary_warehouse)
        if warehouse_coords:
            profile.warehouse_lat, profile.warehouse_lon = warehouse_coords
        
        # ============================================================
        # Financial
        # ============================================================
        profile.total_revenue = base_data.get('total_revenue', 0)
        profile.total_dn = base_data.get('total_dn', 0)
        profile.total_units = base_data.get('total_units', 0)
        
        profile.avg_revenue = profile.total_revenue / max(1, profile.total_dn)
        profile.avg_units_per_dn = profile.total_units / max(1, profile.total_dn)
        profile.avg_revenue_per_dn = profile.total_revenue / max(1, profile.total_dn)
        
        # Monthly revenue
        monthly_data = self._get_monthly_data(dealer_name)
        if monthly_data:
            profile.monthly_revenue = monthly_data.get('revenue', 0)
            profile.revenue_growth = monthly_data.get('growth', 0)
        
        # ============================================================
        # Delivery
        # ============================================================
        delivery_data = self._get_delivery_data(dealer_name)
        if delivery_data:
            profile.delivered_dn = delivery_data.get('delivered_dn', 0)
            profile.pending_dn = delivery_data.get('pending_dn', 0)
            profile.pending_pgi = delivery_data.get('pending_pgi', 0)
            profile.pending_pod = delivery_data.get('pending_pod', 0)
            profile.delivery_pct = delivery_data.get('delivery_pct', 0)
            profile.pgi_pct = delivery_data.get('pgi_pct', 0)
            profile.pod_pct = delivery_data.get('pod_pct', 0)
            profile.avg_delivery_days = delivery_data.get('avg_delivery_days', 0)
            profile.avg_pod_days = delivery_data.get('avg_pod_days', 0)
            profile.oldest_pending = delivery_data.get('oldest_pending', '')
            profile.latest_delivery = delivery_data.get('latest_delivery', '')
            profile.latest_pod = delivery_data.get('latest_pod', '')
        
        # ============================================================
        # Warehouses
        # ============================================================
        warehouse_data = self._get_warehouse_data(dealer_name)
        if warehouse_data:
            profile.warehouses_used = warehouse_data.get('warehouses', [])
            profile.primary_warehouse_name = warehouse_data.get('primary', '')
            profile.warehouse_count = len(profile.warehouses_used)
            profile.warehouse_revenue = warehouse_data.get('total_revenue', 0)
            profile.warehouse_units = warehouse_data.get('total_units', 0)
        
        # ============================================================
        # Cities
        # ============================================================
        city_data = self._get_city_data(dealer_name)
        if city_data:
            profile.cities_served = city_data.get('cities', [])
            profile.primary_city_name = city_data.get('primary', '')
            profile.city_count = len(profile.cities_served)
            profile.city_revenue = city_data.get('total_revenue', 0)
        
        # ============================================================
        # Products
        # ============================================================
        product_data = self._get_product_data(dealer_name)
        if product_data:
            profile.top_product = product_data.get('top_product', '')
            profile.bottom_product = product_data.get('bottom_product', '')
            profile.top_model = product_data.get('top_model', '')
            profile.bottom_model = product_data.get('bottom_model', '')
            profile.top_material = product_data.get('top_material', '')
            profile.top_division = product_data.get('top_division', '')
            profile.product_count = product_data.get('product_count', 0)
            profile.model_count = product_data.get('model_count', 0)
            profile.material_count = product_data.get('material_count', 0)
        
        # ============================================================
        # Distance Intelligence
        # ============================================================
        distance_data = self._calculate_distance(profile)
        if distance_data:
            profile.actual_distance_km = distance_data.get('distance_km', 0)
            profile.estimated_distance_km = distance_data.get('estimated_distance_km', 0)
            profile.travel_time_minutes = distance_data.get('travel_time_minutes', 0)
            profile.avg_lead_distance = distance_data.get('avg_lead_distance', 0)
            profile.longest_route_km = distance_data.get('longest_route_km', 0)
            profile.shortest_route_km = distance_data.get('shortest_route_km', 0)
            profile.transport_zone = distance_data.get('transport_zone', '')
        
        # ============================================================
        # Timeline
        # ============================================================
        timeline_data = self._get_timeline_data(dealer_name)
        if timeline_data:
            profile.first_order = timeline_data.get('first_order', '')
            profile.last_order = timeline_data.get('last_order', '')
            profile.first_dn = timeline_data.get('first_dn', '')
            profile.first_pgi = timeline_data.get('first_pgi', '')
            profile.first_pod = timeline_data.get('first_pod', '')
            profile.latest_dn = timeline_data.get('latest_dn', '')
            profile.latest_pgi = timeline_data.get('latest_pgi', '')
            profile.latest_pod_date = timeline_data.get('latest_pod_date', '')
            profile.latest_activity = timeline_data.get('latest_activity', '')
        
        # ============================================================
        # Rankings
        # ============================================================
        rankings = self._calculate_rankings(dealer_name, profile)
        if rankings:
            profile.revenue_rank = rankings.get('revenue_rank', 0)
            profile.unit_rank = rankings.get('unit_rank', 0)
            profile.dn_rank = rankings.get('dn_rank', 0)
            profile.delivery_rank = rankings.get('delivery_rank', 0)
            profile.warehouse_rank = rankings.get('warehouse_rank', 0)
            profile.distance_rank = rankings.get('distance_rank', 0)
            profile.growth_rank = rankings.get('growth_rank', 0)
            profile.overall_rank = rankings.get('overall_rank', 0)
        
        # ============================================================
        # KPI Scores
        # ============================================================
        scores = self._calculate_kpi_scores(profile)
        profile.revenue_score = scores.get('revenue_score', 0)
        profile.delivery_score = scores.get('delivery_score', 0)
        profile.pgi_score = scores.get('pgi_score', 0)
        profile.pod_score = scores.get('pod_score', 0)
        profile.growth_score = scores.get('growth_score', 0)
        profile.warehouse_score = scores.get('warehouse_score', 0)
        profile.distance_score = scores.get('distance_score', 0)
        profile.product_mix_score = scores.get('product_mix_score', 0)
        profile.volume_score = scores.get('volume_score', 0)
        profile.customer_score = scores.get('customer_score', 0)
        profile.business_score = scores.get('business_score', 0)
        profile.risk_score = scores.get('risk_score', 0)
        
        # ============================================================
        # Recommendations
        # ============================================================
        profile.recommendations = self._generate_recommendations(profile)
        profile.insights = self._generate_insights(profile)
        
        return profile
    
    def _get_base_data(self, dealer_name: str, dealer_code: str) -> Dict[str, Any]:
        """Get base dealer data"""
        try:
            sql = f"""
                SELECT 
                    customer_name,
                    dealer_code,
                    customer_code,
                    sales_office,
                    sales_manager,
                    division,
                    ship_to_city as primary_city,
                    warehouse as primary_warehouse,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                   OR LOWER(dealer_code) = LOWER('{dealer_code}')
                GROUP BY customer_name, dealer_code, customer_code, sales_office, 
                         sales_manager, division, ship_to_city, warehouse
                ORDER BY total_revenue DESC
                LIMIT 1
            """
            result = self.session.execute(text(sql))
            row = result.fetchone()
            
            if row:
                return dict(zip(row.keys(), row))
            return {}
        except Exception as e:
            logger.error(f"Failed to get base data: {e}")
            return {}
    
    def _get_monthly_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get monthly revenue data"""
        try:
            sql = f"""
                SELECT 
                    TO_CHAR(dn_create_date, 'YYYY-MM') as month,
                    COALESCE(SUM(dn_amount), 0) as revenue
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND dn_create_date IS NOT NULL
                GROUP BY TO_CHAR(dn_create_date, 'YYYY-MM')
                ORDER BY month DESC
                LIMIT 2
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            if len(rows) >= 2:
                current = rows[0]
                previous = rows[1]
                return {
                    'revenue': float(current[1] or 0),
                    'growth': _growth(float(current[1] or 0), float(previous[1] or 0))
                }
            elif len(rows) == 1:
                return {
                    'revenue': float(rows[0][1] or 0),
                    'growth': 0
                }
            return {}
        except Exception as e:
            logger.error(f"Failed to get monthly data: {e}")
            return {}
    
    def _get_delivery_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get delivery data"""
        try:
            sql = f"""
                SELECT 
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered_dn,
                    COUNT(DISTINCT CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_no END) as pending_dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) as pending_pgi,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) as pending_pod,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed,
                    AVG(CASE WHEN good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as avg_delivery_days,
                    AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as avg_pod_days,
                    MIN(CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_create_date END) as oldest_pending,
                    MAX(CASE WHEN pod_date IS NOT NULL THEN pod_date END) as latest_pod,
                    MAX(CASE WHEN good_issue_date IS NOT NULL THEN good_issue_date END) as latest_delivery
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
            """
            result = self.session.execute(text(sql))
            row = result.fetchone()
            
            if row:
                data = dict(zip(row.keys(), row))
                total_dn = data.get('total_dn', 1)
                return {
                    'delivered_dn': int(data.get('delivered_dn', 0) or 0),
                    'pending_dn': int(data.get('pending_dn', 0) or 0),
                    'pending_pgi': int(data.get('pending_pgi', 0) or 0),
                    'pending_pod': int(data.get('pending_pod', 0) or 0),
                    'delivery_pct': _percent(data.get('delivered_dn', 0), total_dn),
                    'pgi_pct': _percent(data.get('pgi_completed', 0), total_dn),
                    'pod_pct': _percent(data.get('pod_completed', 0), total_dn),
                    'avg_delivery_days': float(data.get('avg_delivery_days', 0) or 0),
                    'avg_pod_days': float(data.get('avg_pod_days', 0) or 0),
                    'oldest_pending': _date_text(data.get('oldest_pending')),
                    'latest_pod': _date_text(data.get('latest_pod')),
                    'latest_delivery': _date_text(data.get('latest_delivery')),
                }
            return {}
        except Exception as e:
            logger.error(f"Failed to get delivery data: {e}")
            return {}
    
    def _get_warehouse_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get warehouse data"""
        try:
            sql = f"""
                SELECT 
                    warehouse,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND warehouse IS NOT NULL
                GROUP BY warehouse
                ORDER BY revenue DESC
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            warehouses = []
            total_revenue = 0
            total_units = 0
            
            for row in rows:
                warehouse = row[0]
                units = float(row[1] or 0)
                revenue = float(row[2] or 0)
                warehouses.append(warehouse)
                total_revenue += revenue
                total_units += units
            
            return {
                'warehouses': warehouses,
                'primary': warehouses[0] if warehouses else '',
                'total_revenue': total_revenue,
                'total_units': total_units
            }
        except Exception as e:
            logger.error(f"Failed to get warehouse data: {e}")
            return {}
    
    def _get_city_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get city data"""
        try:
            sql = f"""
                SELECT 
                    ship_to_city as city,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND ship_to_city IS NOT NULL
                GROUP BY ship_to_city
                ORDER BY revenue DESC
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            cities = []
            total_revenue = 0
            
            for row in rows:
                city = row[0]
                revenue = float(row[1] or 0)
                cities.append(city)
                total_revenue += revenue
            
            return {
                'cities': cities,
                'primary': cities[0] if cities else '',
                'total_revenue': total_revenue
            }
        except Exception as e:
            logger.error(f"Failed to get city data: {e}")
            return {}
    
    def _get_product_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get product data"""
        try:
            sql = f"""
                SELECT 
                    customer_model as product,
                    material_no,
                    division,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND customer_model IS NOT NULL
                GROUP BY customer_model, material_no, division
                ORDER BY revenue DESC
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            if not rows:
                return {}
            
            products = []
            models = []
            materials = []
            
            for row in rows:
                product = row[0]
                material = row[1]
                products.append(product)
                if material:
                    materials.append(material)
                if product:
                    models.append(product)
            
            return {
                'top_product': products[0] if products else '',
                'bottom_product': products[-1] if products else '',
                'top_model': models[0] if models else '',
                'bottom_model': models[-1] if models else '',
                'top_material': materials[0] if materials else '',
                'top_division': rows[0][2] if rows else '',
                'product_count': len(set(products)),
                'model_count': len(set(models)),
                'material_count': len(set(materials))
            }
        except Exception as e:
            logger.error(f"Failed to get product data: {e}")
            return {}
    
    def _get_timeline_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get timeline data"""
        try:
            sql = f"""
                SELECT 
                    MIN(dn_create_date) as first_order,
                    MAX(dn_create_date) as last_order,
                    MIN(CASE WHEN good_issue_date IS NOT NULL THEN good_issue_date END) as first_pgi,
                    MIN(CASE WHEN pod_date IS NOT NULL THEN pod_date END) as first_pod,
                    MAX(dn_create_date) as latest_dn,
                    MAX(CASE WHEN good_issue_date IS NOT NULL THEN good_issue_date END) as latest_pgi,
                    MAX(CASE WHEN pod_date IS NOT NULL THEN pod_date END) as latest_pod,
                    MAX(GREATEST(dn_create_date, good_issue_date, pod_date)) as latest_activity
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
            """
            result = self.session.execute(text(sql))
            row = result.fetchone()
            
            if row:
                return {
                    'first_order': _date_text(row[0]),
                    'last_order': _date_text(row[1]),
                    'first_pgi': _date_text(row[2]),
                    'first_pod': _date_text(row[3]),
                    'latest_dn': _date_text(row[4]),
                    'latest_pgi': _date_text(row[5]),
                    'latest_pod_date': _date_text(row[6]),
                    'latest_activity': _date_text(row[7])
                }
            return {}
        except Exception as e:
            logger.error(f"Failed to get timeline data: {e}")
            return {}
    
    def _calculate_distance(self, profile: DealerProfile) -> Dict[str, Any]:
        """Calculate distance intelligence"""
        distance_data = {}
        
        # Get dealer coordinates
        dealer_lat = profile.latitude
        dealer_lon = profile.longitude
        
        # Get warehouse coordinates
        warehouse_lat = profile.warehouse_lat
        warehouse_lon = profile.warehouse_lon
        
        if dealer_lat and dealer_lon and warehouse_lat and warehouse_lon:
            # Calculate distance using Haversine
            distance_km = _calculate_distance(
                dealer_lat, dealer_lon,
                warehouse_lat, warehouse_lon
            )
            
            distance_data['distance_km'] = round(distance_km, 1)
            distance_data['estimated_distance_km'] = round(distance_km * 1.15, 1)  # 15% road factor
            distance_data['travel_time_minutes'] = int(distance_km / 50 * 60)  # 50 km/h average
            distance_data['transport_zone'] = _get_transport_zone(distance_km)
            distance_data['avg_lead_distance'] = distance_km
            distance_data['longest_route_km'] = distance_km * 1.1
            distance_data['shortest_route_km'] = distance_km * 0.9
        
        return distance_data
    
    def _calculate_rankings(self, dealer_name: str, profile: DealerProfile) -> Dict[str, Any]:
        """Calculate dealer rankings"""
        rankings = {}
        
        try:
            # Get all dealers for ranking
            sql = """
                SELECT 
                    customer_name,
                    COUNT(DISTINCT dn_no) as dn_count,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered_dn
                FROM delivery_reports
                WHERE customer_name IS NOT NULL
                GROUP BY customer_name
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            all_dealers = []
            for row in rows:
                all_dealers.append({
                    'name': row[0],
                    'dn_count': int(row[1] or 0),
                    'units': float(row[2] or 0),
                    'revenue': float(row[3] or 0),
                    'delivered': int(row[4] or 0)
                })
            
            if not all_dealers:
                return {}
            
            # Sort by revenue
            sorted_by_revenue = sorted(all_dealers, key=lambda x: x['revenue'], reverse=True)
            for i, d in enumerate(sorted_by_revenue, 1):
                if d['name'] == dealer_name:
                    rankings['revenue_rank'] = i
                    break
            
            # Sort by units
            sorted_by_units = sorted(all_dealers, key=lambda x: x['units'], reverse=True)
            for i, d in enumerate(sorted_by_units, 1):
                if d['name'] == dealer_name:
                    rankings['unit_rank'] = i
                    break
            
            # Sort by DN
            sorted_by_dn = sorted(all_dealers, key=lambda x: x['dn_count'], reverse=True)
            for i, d in enumerate(sorted_by_dn, 1):
                if d['name'] == dealer_name:
                    rankings['dn_rank'] = i
                    break
            
            # Sort by delivery
            sorted_by_delivery = sorted(all_dealers, key=lambda x: x['delivered'] / max(1, x['dn_count']), reverse=True)
            for i, d in enumerate(sorted_by_delivery, 1):
                if d['name'] == dealer_name:
                    rankings['delivery_rank'] = i
                    break
            
            # Calculate overall rank (average of all ranks)
            rank_sum = sum([
                rankings.get('revenue_rank', 0),
                rankings.get('unit_rank', 0),
                rankings.get('dn_rank', 0),
                rankings.get('delivery_rank', 0)
            ])
            total_ranks = len([r for r in rankings.values() if r > 0])
            rankings['overall_rank'] = int(rank_sum / max(1, total_ranks)) if total_ranks > 0 else 0
            
        except Exception as e:
            logger.error(f"Failed to calculate rankings: {e}")
        
        return rankings
    
    def _calculate_kpi_scores(self, profile: DealerProfile) -> Dict[str, float]:
        """Calculate KPI scores"""
        scores = {}
        
        # Revenue Score (0-100)
        revenue = profile.total_revenue
        scores['revenue_score'] = min(100, (revenue / 1000000) * 10)
        
        # Delivery Score (0-100)
        scores['delivery_score'] = profile.delivery_pct
        
        # PGI Score (0-100)
        scores['pgi_score'] = profile.pgi_pct
        
        # POD Score (0-100)
        scores['pod_score'] = profile.pod_pct
        
        # Growth Score (0-100)
        growth = profile.revenue_growth
        if growth > 0:
            scores['growth_score'] = min(100, growth * 10)
        else:
            scores['growth_score'] = max(0, 100 + growth * 5)
        
        # Warehouse Score (0-100)
        warehouse_count = profile.warehouse_count
        scores['warehouse_score'] = min(100, warehouse_count * 15)
        
        # Distance Score (0-100)
        distance = profile.actual_distance_km
        if distance <= 50:
            scores['distance_score'] = 100
        elif distance <= 150:
            scores['distance_score'] = 80
        elif distance <= 300:
            scores['distance_score'] = 60
        elif distance <= 500:
            scores['distance_score'] = 40
        else:
            scores['distance_score'] = 20
        
        # Product Mix Score (0-100)
        product_count = profile.product_count
        scores['product_mix_score'] = min(100, product_count * 5)
        
        # Volume Score (0-100)
        units = profile.total_units
        scores['volume_score'] = min(100, (units / 100) * 10)
        
        # Customer Score (0-100)
        city_count = profile.city_count
        scores['customer_score'] = min(100, city_count * 10)
        
        # Business Score (weighted average)
        scores['business_score'] = (
            scores['revenue_score'] * 0.20 +
            scores['delivery_score'] * 0.15 +
            scores['pgi_score'] * 0.10 +
            scores['pod_score'] * 0.10 +
            scores['growth_score'] * 0.15 +
            scores['warehouse_score'] * 0.10 +
            scores['volume_score'] * 0.10 +
            scores['customer_score'] * 0.10
        )
        scores['business_score'] = round(scores['business_score'], 1)
        
        # Risk Score (inverse of business score)
        scores['risk_score'] = round(100 - scores['business_score'], 1)
        
        return scores
    
    def _generate_insights(self, profile: DealerProfile) -> List[str]:
        """Generate insights from profile"""
        insights = []
        
        # Revenue insights
        revenue = profile.total_revenue
        if revenue > 10000000:
            insights.append(f"High revenue performer: {_format_currency(revenue)}")
        elif revenue > 5000000:
            insights.append(f"Good revenue generation: {_format_currency(revenue)}")
        else:
            insights.append("Opportunity to increase revenue")
        
        # Delivery insights
        delivery_pct = profile.delivery_pct
        if delivery_pct >= 95:
            insights.append("Excellent delivery performance")
        elif delivery_pct >= 85:
            insights.append("Good delivery performance")
        else:
            insights.append("Delivery performance needs improvement")
        
        # Growth insights
        growth = profile.revenue_growth
        if growth > 20:
            insights.append(f"Strong revenue growth at {growth:.1f}%")
        elif growth > 10:
            insights.append(f"Healthy revenue growth at {growth:.1f}%")
        elif growth > 0:
            insights.append(f"Steady revenue growth at {growth:.1f}%")
        else:
            insights.append(f"Revenue decline of {growth:.1f}% - needs attention")
        
        # Product insights
        product_count = profile.product_count
        if product_count > 10:
            insights.append(f"Wide product portfolio: {product_count} products")
        elif product_count > 5:
            insights.append(f"Good product variety: {product_count} products")
        else:
            insights.append("Limited product range - opportunity for expansion")
        
        # Warehouse insights
        warehouse_count = profile.warehouse_count
        if warehouse_count > 3:
            insights.append(f"Strong warehouse network: {warehouse_count} warehouses")
        elif warehouse_count > 1:
            insights.append(f"Multiple warehouses: {warehouse_count} warehouses")
        else:
            insights.append("Single warehouse dependency - diversification recommended")
        
        # Risk insights
        risk_score = profile.risk_score
        if risk_score > 50:
            insights.append(f"High risk score: {risk_score:.1f}/100 - requires attention")
        elif risk_score > 30:
            insights.append(f"Moderate risk score: {risk_score:.1f}/100 - monitor closely")
        
        # Business insights
        if profile.business_score >= 85:
            insights.append("Excellent overall business health")
        elif profile.business_score >= 70:
            insights.append("Good overall business health")
        elif profile.business_score < 50:
            insights.append("Critical business health - immediate action required")
        
        return insights[:10]
    
    def _generate_recommendations(self, profile: DealerProfile) -> List[str]:
        """Generate recommendations based on profile"""
        recommendations = []
        
        # Delivery recommendations
        if profile.delivery_pct < 85:
            recommendations.append("📦 Improve delivery speed and reliability")
            recommendations.append("📋 Review delivery routes for optimization")
        
        # Pending recommendations
        if profile.pending_dn > 20:
            recommendations.append(f"⏳ Escalate {profile.pending_dn} pending DNs for resolution")
        elif profile.pending_dn > 10:
            recommendations.append("📋 Review pending orders for timely closure")
        
        # Product recommendations
        if profile.product_count < 5:
            recommendations.append("🛒 Expand product portfolio to increase revenue")
        
        # Warehouse recommendations
        if profile.warehouse_count == 1:
            recommendations.append("🏭 Consider diversifying warehouse coverage")
        
        # City recommendations
        if profile.city_count < 3:
            recommendations.append("🌍 Expand to new cities for growth")
        
        # Growth recommendations
        if profile.revenue_growth < 0:
            recommendations.append("📈 Develop growth strategy to reverse revenue decline")
        
        # Distance recommendations
        if profile.actual_distance_km > 300:
            recommendations.append("🚚 Consider warehouse closer to dealer location")
        
        # Business recommendations
        if profile.business_score < 70:
            recommendations.append("📊 Develop action plan to improve business score")
        
        if not recommendations:
            recommendations.append("✅ Maintain current performance levels")
            recommendations.append("📊 Continue monitoring key metrics")
        
        return recommendations[:10]

# ============================================================
# BLOCK 9: MENU REGISTRY
# ============================================================

class DealerMenuRegistry:
    """Registry of all dealer menus and their items"""
    
    MENUS = {
        "main": {
            "id": "main",
            "name": "DEALER INTELLIGENCE PLATFORM",
            "items": [
                {"id": "1", "name": "Dashboard", "handler": "handle_dashboard_menu", "icon": "📊"},
                {"id": "2", "name": "Intelligence", "handler": "handle_intelligence_menu", "icon": "🧠"},
                {"id": "3", "name": "AI Assistant", "handler": "handle_ai_assistant_menu", "icon": "🤖"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "dashboard": {
            "id": "dashboard",
            "name": "DEALER DASHBOARD",
            "items": [
                {"id": "1", "name": "Intelligence Report", "handler": "handle_intelligence", "icon": "🧠"},
                {"id": "2", "name": "Revenue", "handler": "handle_revenue", "icon": "💰"},
                {"id": "3", "name": "Units", "handler": "handle_units", "icon": "📦"},
                {"id": "4", "name": "DN", "handler": "handle_dn", "icon": "📄"},
                {"id": "5", "name": "Delivery", "handler": "handle_delivery", "icon": "🚚"},
                {"id": "6", "name": "Distance", "handler": "handle_distance", "icon": "📍"},
                {"id": "7", "name": "Timeline", "handler": "handle_timeline", "icon": "📅"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "intelligence": {
            "id": "intelligence",
            "name": "DEALER INTELLIGENCE",
            "items": [
                {"id": "1", "name": "Products", "handler": "handle_products", "icon": "📦"},
                {"id": "2", "name": "Models", "handler": "handle_models", "icon": "🏷️"},
                {"id": "3", "name": "Warehouses", "handler": "handle_warehouses", "icon": "🏭"},
                {"id": "4", "name": "Cities", "handler": "handle_cities", "icon": "🏙️"},
                {"id": "5", "name": "Rankings", "handler": "handle_ranking", "icon": "🏆"},
                {"id": "6", "name": "KPI", "handler": "handle_kpi", "icon": "📊"},
                {"id": "7", "name": "Insights", "handler": "handle_insights", "icon": "💡"},
                {"id": "8", "name": "Recommendations", "handler": "handle_recommendations", "icon": "🎯"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "ai_assistant": {
            "id": "ai_assistant",
            "name": "DEALER AI ASSISTANT",
            "items": [
                {"id": "1", "name": "Ask Question", "handler": "handle_ai_ask", "icon": "❓"},
                {"id": "2", "name": "Analysis", "handler": "handle_ai_analysis", "icon": "📊"},
                {"id": "3", "name": "Insights", "handler": "handle_ai_insights", "icon": "💡"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        }
    }

# ============================================================
# BLOCK 10: DEALER RENDERER
# ============================================================

class DealerRenderer:
    """Render dealer responses for WhatsApp"""
    
    MENU_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━"
    
    @classmethod
    def _render_menu_footer(cls, menu_type: str = "main") -> str:
        menu = DealerMenuRegistry.MENUS.get(menu_type, DealerMenuRegistry.MENUS["main"])
        
        lines = ["", cls.MENU_SEPARATOR, ""]
        lines.append(f"📋 *{menu['name']}*")
        lines.append("")
        
        for item in menu["items"]:
            lines.append(f"{item['id']}. {item['icon']} {item['name']}")
        
        lines.append("")
        lines.append("Reply with a number or type your question:")
        
        return "\n".join(lines)
    
    @classmethod
    def render_main_menu(cls) -> str:
        return cls._render_menu_footer("main")
    
    @classmethod
    def render_dashboard_menu(cls) -> str:
        return cls._render_menu_footer("dashboard")
    
    @classmethod
    def render_intelligence_menu(cls) -> str:
        return cls._render_menu_footer("intelligence")
    
    @classmethod
    def render_ai_assistant_menu(cls) -> str:
        return cls._render_menu_footer("ai_assistant")
    
    @classmethod
    def render_with_menu(cls, content: str, menu_type: str = "main") -> str:
        return f"{content}\n{cls._render_menu_footer(menu_type)}"
    
    @classmethod
    def render_intelligence_report(cls, profile: DealerProfile) -> str:
        """Render complete 360° Dealer Intelligence Report"""
        if not profile:
            return "⚠️ No dealer profile available."
        
        lines = []
        
        # Header
        lines.append("🧠 *DEALER INTELLIGENCE REPORT*")
        lines.append("")
        
        # Identity
        lines.append("📌 *Identity*")
        lines.append(f"Name: {profile.dealer_name}")
        lines.append(f"Code: {profile.dealer_code}")
        lines.append(f"Customer Code: {profile.customer_code}")
        lines.append(f"Sales Office: {profile.sales_office}")
        lines.append(f"Sales Manager: {profile.sales_manager}")
        lines.append(f"Division: {profile.division}")
        lines.append("")
        
        # Financial
        lines.append("💰 *Financial Intelligence*")
        lines.append(f"Revenue: {_format_currency(profile.total_revenue)}")
        lines.append(f"Avg Revenue/DN: {_format_currency(profile.avg_revenue_per_dn)}")
        lines.append(f"Monthly Revenue: {_format_currency(profile.monthly_revenue)}")
        lines.append(f"Revenue Growth: {profile.revenue_growth:+.1f}%")
        lines.append(f"Revenue Rank: #{profile.revenue_rank}")
        lines.append("")
        
        # Operations
        lines.append("📦 *Operations Intelligence*")
        lines.append(f"DN: {_format_number(profile.total_dn)}")
        lines.append(f"Units: {_format_number(profile.total_units)}")
        lines.append(f"Avg Units/DN: {profile.avg_units_per_dn:.1f}")
        lines.append(f"Products: {_format_number(profile.product_count)}")
        lines.append(f"Models: {_format_number(profile.model_count)}")
        lines.append("")
        
        # Delivery
        lines.append("🚚 *Delivery Intelligence*")
        lines.append(f"Delivery: {profile.delivery_pct:.1f}%")
        lines.append(f"PGI: {profile.pgi_pct:.1f}%")
        lines.append(f"POD: {profile.pod_pct:.1f}%")
        lines.append(f"Pending DN: {_format_number(profile.pending_dn)}")
        lines.append(f"Avg Delivery: {profile.avg_delivery_days:.1f} Days")
        lines.append(f"Avg POD: {profile.avg_pod_days:.1f} Days")
        lines.append("")
        
        # Warehouse
        lines.append("🏭 *Warehouse Intelligence*")
        lines.append(f"Primary: {profile.primary_warehouse_name or 'N/A'}")
        lines.append(f"Warehouses: {_format_number(profile.warehouse_count)}")
        lines.append(f"Warehouse Revenue: {_format_currency(profile.warehouse_revenue)}")
        if profile.warehouses_used:
            lines.append(f"Used: {', '.join(profile.warehouses_used[:3])}")
            if len(profile.warehouses_used) > 3:
                lines.append(f"... and {len(profile.warehouses_used) - 3} more")
        lines.append("")
        
        # Cities
        lines.append("🏙️ *City Intelligence*")
        lines.append(f"Primary: {profile.primary_city_name or 'N/A'}")
        lines.append(f"Cities: {_format_number(profile.city_count)}")
        if profile.cities_served:
            lines.append(f"Served: {', '.join(profile.cities_served[:3])}")
            if len(profile.cities_served) > 3:
                lines.append(f"... and {len(profile.cities_served) - 3} more")
        lines.append("")
        
        # Distance
        lines.append("📍 *Distance Intelligence*")
        lines.append(f"Distance: {profile.actual_distance_km:.1f} km")
        lines.append(f"Travel Time: {profile.travel_time_minutes} min")
        lines.append(f"Transport Zone: {profile.transport_zone}")
        lines.append("")
        
        # Rankings
        lines.append("🏆 *Rankings*")
        lines.append(f"Revenue: #{profile.revenue_rank}")
        lines.append(f"Units: #{profile.unit_rank}")
        lines.append(f"Delivery: #{profile.delivery_rank}")
        lines.append(f"Overall: #{profile.overall_rank}")
        lines.append("")
        
        # Scores
        lines.append("📊 *KPI Scores*")
        lines.append(f"Business Score: {profile.business_score:.1f}/100")
        lines.append(f"Risk Score: {profile.risk_score:.1f}/100")
        lines.append(f"Revenue Score: {profile.revenue_score:.1f}/100")
        lines.append(f"Delivery Score: {profile.delivery_score:.1f}/100")
        lines.append(f"Growth Score: {profile.growth_score:.1f}/100")
        lines.append("")
        
        # Timeline
        lines.append("📅 *Timeline*")
        lines.append(f"First Order: {profile.first_order}")
        lines.append(f"Last Order: {profile.last_order}")
        lines.append(f"Latest DN: {profile.latest_dn}")
        lines.append(f"Latest POD: {profile.latest_pod_date}")
        lines.append("")
        
        # Insights
        if profile.insights:
            lines.append("💡 *Key Insights*")
            for insight in profile.insights[:5]:
                lines.append(f"• {insight}")
            lines.append("")
        
        # Recommendations
        if profile.recommendations:
            lines.append("🎯 *Recommendations*")
            for rec in profile.recommendations[:5]:
                lines.append(f"• {rec}")
            lines.append("")
        
        return "\n".join(lines)
    
    @classmethod
    def render_ranking(cls, ranking: List[Dict[str, Any]], metric: str = "Revenue", limit: int = 10) -> str:
        if not ranking:
            return f"🏆 *Dealer Rankings by {metric}*\n\nNo dealers found."
        
        lines = [f"🏆 *Dealer Rankings by {metric}*", ""]
        
        for i, item in enumerate(ranking[:limit], 1):
            dealer = item.get('dealer', 'Unknown')
            value = item.get('value', 'N/A')
            
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            lines.append(f"{medal} {dealer}: {value}")
        
        return "\n".join(lines)

# ============================================================
# BLOCK 11: MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """Enterprise Dealer Intelligence Platform - Fully Independent"""
    
    _instance: Optional["DealerAnalyticsService"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._service_name = "dealer_analytics"
        self._version = "7.1"
        
        # Initialize engines
        self._dealer_dict = DealerDictionary()
        self._renderer = DealerRenderer()
        
        # Sessions
        self._sessions: Dict[str, DealerSession] = {}
        self._session_lock = threading.RLock()
        
        logger.info("=" * 80)
        logger.info(f"🚀 Dealer Intelligence Platform v{self._version} initialized")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info(f"   📚 Dealer Dictionary: {len(self._dealer_dict.get_all_dealers())} dealers")
        logger.info(f"   🔍 Universal Detection: {'Enabled' if RAPIDFUZZ_AVAILABLE else 'Limited'}")
        logger.info("=" * 80)
    
    def _get_session(self, session_id: str) -> DealerSession:
        with self._session_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = DealerSession(session_id=session_id)
                logger.info(f"🆕 New dealer session created for {session_id}")
            return self._sessions[session_id]
    
    def _get_db_session(self) -> Optional[Session]:
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def _get_menu_type(self, session: DealerSession) -> str:
        if session.menu_state == DealerMenuState.DASHBOARD:
            return "dashboard"
        elif session.menu_state == DealerMenuState.INTELLIGENCE:
            return "intelligence"
        elif session.menu_state == DealerMenuState.AI_ASSISTANT:
            return "ai_assistant"
        else:
            return "main"
    
    def _render_response(self, content: str, session: DealerSession) -> str:
        menu_type = self._get_menu_type(session)
        return self._renderer.render_with_menu(content, menu_type)
    
    def get_main_menu(self) -> str:
        return self._renderer.render_main_menu()
    
    # ============================================================
    # BLOCK 3: IMPROVED process_whatsapp_query() - Multiple detection attempts
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        Main entry point for dealer processing.
        
        This is the ONLY external interface.
        All processing stays inside this module.
        """
        session = self._get_session(sender)
        
        # AUTO-MENU: Show menu on first entry
        if not session.menu_shown:
            session.menu_shown = True
            logger.info(f"📋 Auto-showing dealer menu for {sender}")
            return self._render_response("🧠 Welcome to the Dealer Intelligence Platform!", session)
        
        if not message or not message.strip():
            return self._render_response("Please provide a dealer name or select a menu option.", session)
        
        message_clean = message.strip()
        logger.info(f"📊 Dealer Query: '{message_clean}' from {sender}")
        
        session.touch()
        
        # ============================================================
        # STEP 1: Exit (99)
        # ============================================================
        if message_clean == "99":
            session.clear()
            logger.info(f"🚪 Dealer session exited for {sender}")
            return "__EXIT__"
        
        # ============================================================
        # STEP 2: Menu navigation (0, 1, 2, 3)
        # ============================================================
        if message_clean == "0":
            session.menu_state = DealerMenuState.MAIN
            return self._render_response("Main Menu", session)
        
        if message_clean == "1":
            session.menu_state = DealerMenuState.DASHBOARD
            return self._render_response("📊 *Dashboard Menu*\n\nSelect an option below:", session)
        
        if message_clean == "2":
            session.menu_state = DealerMenuState.INTELLIGENCE
            return self._render_response("🧠 *Intelligence Menu*\n\nSelect an option below:", session)
        
        if message_clean == "3":
            session.menu_state = DealerMenuState.AI_ASSISTANT
            return self._render_response("🤖 *AI Assistant*\n\nAsk me anything about dealers:", session)
        
        # ============================================================
        # STEP 3: Check if it's a menu option number
        # ============================================================
        if message_clean.isdigit():
            if session.menu_state == DealerMenuState.DASHBOARD:
                dashboard_handlers = {
                    "1": self._handle_intelligence,
                    "2": self._handle_revenue,
                    "3": self._handle_units,
                    "4": self._handle_dn,
                    "5": self._handle_delivery,
                    "6": self._handle_distance,
                    "7": self._handle_timeline,
                }
                if message_clean in dashboard_handlers:
                    response = dashboard_handlers[message_clean](session, message_clean)
                    return self._render_response(response, session)
            
            if session.menu_state == DealerMenuState.INTELLIGENCE:
                intelligence_handlers = {
                    "1": self._handle_products,
                    "2": self._handle_models,
                    "3": self._handle_warehouses,
                    "4": self._handle_cities,
                    "5": self._handle_ranking,
                    "6": self._handle_kpi,
                    "7": self._handle_insights,
                    "8": self._handle_recommendations,
                }
                if message_clean in intelligence_handlers:
                    response = intelligence_handlers[message_clean](session, message_clean)
                    return self._render_response(response, session)
        
        # ============================================================
        # STEP 4: Universal Dealer Detection with multiple attempts
        # ============================================================
        detection = self._dealer_dict.detect_dealer(message_clean)
        
        # If no detection, try removing common suffixes
        if not detection:
            normalized_msg = re.sub(r'\s+(wah|store|shop|center|centre|digital|electronics|appliances|enterprise)$', '', message_clean, flags=re.IGNORECASE)
            if normalized_msg != message_clean:
                detection = self._dealer_dict.detect_dealer(normalized_msg)
        
        # If still no detection, try with first significant words
        if not detection:
            words = message_clean.split()
            for i in range(min(3, len(words))):
                test_name = " ".join(words[:i+1])
                detection = self._dealer_dict.detect_dealer(test_name)
                if detection:
                    break
        
        # If still no detection, try combined words
        if not detection:
            combined = re.sub(r'\s+', '', message_clean)
            detection = self._dealer_dict.detect_dealer(combined)
        
        if detection:
            dealer_name = detection.dealer_name
            confidence = detection.confidence
            match_type = detection.match_type
            
            logger.info(f"🎯 Dealer detected: {dealer_name} (confidence: {confidence:.2f}, match: {match_type})")
            
            # Build dealer profile
            db_session = self._get_db_session()
            if db_session:
                try:
                    builder = DealerProfileBuilder(db_session)
                    profile = builder.build_profile(dealer_name)
                    db_session.close()
                    
                    if profile:
                        session.set_dealer(dealer_name, detection.dealer_code, profile)
                        
                        # Check for specific intent keywords
                        query_lower = message_clean.lower()
                        
                        if "revenue" in query_lower or "sales" in query_lower:
                            return self._render_response(self._handle_revenue(session, message_clean), session)
                        elif "units" in query_lower or "quantity" in query_lower:
                            return self._render_response(self._handle_units(session, message_clean), session)
                        elif "distance" in query_lower or "far" in query_lower:
                            return self._render_response(self._handle_distance(session, message_clean), session)
                        elif "timeline" in query_lower or "history" in query_lower:
                            return self._render_response(self._handle_timeline(session, message_clean), session)
                        elif "rank" in query_lower:
                            return self._render_response(self._handle_ranking(session, message_clean), session)
                        elif "insight" in query_lower:
                            return self._render_response(self._handle_insights(session, message_clean), session)
                        elif "recommend" in query_lower or "suggest" in query_lower:
                            return self._render_response(self._handle_recommendations(session, message_clean), session)
                        else:
                            # Default: Full Intelligence Report
                            response = self._renderer.render_intelligence_report(profile)
                            return self._render_response(response, session)
                    else:
                        return self._render_response(f"⚠️ Could not build profile for '{dealer_name}'.\n\nPlease try again or check if the dealer exists in the database.", session)
                        
                except Exception as e:
                    logger.error(f"Profile building error: {e}")
                    if db_session:
                        db_session.close()
                    return self._render_response(f"⚠️ Error building profile: {str(e)[:100]}", session)
            else:
                return self._render_response("⚠️ Database unavailable. Please try again later.", session)
        
        # ============================================================
        # STEP 5: No dealer detected - Show help with suggestions
        # ============================================================
        suggestions = self._dealer_dict.search_dealers(message_clean, limit=3)
        
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n\n💡 *Did you mean:*"
            for suggestion in suggestions:
                suggestion_text += f"\n• {suggestion.get('dealer_name', '')}"
        
        help_text = "\n".join([
            f"❌ I couldn't find '{message_clean}'.",
            "",
            "💡 *Dealer Commands:*",
            "• Type dealer name for complete intelligence",
            "• [dealer] revenue - Show revenue",
            "• [dealer] distance - Show distance",
            "• [dealer] timeline - Show timeline",
            "• [dealer] ranking - Show rankings",
            "• [dealer] insights - Show insights",
            "• [dealer] recommendations - Show recommendations",
            suggestion_text,
            "",
            "📌 *Menu Options:*",
            "• 1 - Dashboard Menu",
            "• 2 - Intelligence Menu",
            "• 3 - AI Assistant",
            "• 99 - Exit",
            "",
            "Reply with a command or menu number:"
        ])
        
        return self._render_response(help_text, session)
    
    # ============================================================
    # HANDLERS
    # ============================================================
    
    def _handle_main_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.MAIN
        return "Main Menu"
    
    def _handle_dashboard_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.DASHBOARD
        return self._renderer.render_dashboard_menu()
    
    def _handle_intelligence_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.INTELLIGENCE
        return self._renderer.render_intelligence_menu()
    
    def _handle_ai_assistant_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.AI_ASSISTANT
        return self._renderer.render_ai_assistant_menu()
    
    def _handle_intelligence(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        return self._renderer.render_intelligence_report(session.current_profile)
    
    def _handle_revenue(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"💰 *Revenue - {profile.dealer_name}*\n\nTotal Revenue: {_format_currency(profile.total_revenue)}\nAvg Revenue/DN: {_format_currency(profile.avg_revenue_per_dn)}\nMonthly Revenue: {_format_currency(profile.monthly_revenue)}\nGrowth: {profile.revenue_growth:+.1f}%\nRevenue Rank: #{profile.revenue_rank}"
    
    def _handle_units(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📦 *Units - {profile.dealer_name}*\n\nTotal Units: {_format_number(profile.total_units)}\nAvg Units/DN: {profile.avg_units_per_dn:.1f}\nUnit Rank: #{profile.unit_rank}"
    
    def _handle_dn(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📄 *DN - {profile.dealer_name}*\n\nTotal DN: {_format_number(profile.total_dn)}\nPending DN: {_format_number(profile.pending_dn)}\nDelivered: {_format_number(profile.delivered_dn)}\nDN Rank: #{profile.dn_rank}"
    
    def _handle_delivery(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"🚚 *Delivery - {profile.dealer_name}*\n\nDelivery: {profile.delivery_pct:.1f}%\nPGI: {profile.pgi_pct:.1f}%\nPOD: {profile.pod_pct:.1f}%\nAvg Delivery: {profile.avg_delivery_days:.1f} Days\nAvg POD: {profile.avg_pod_days:.1f} Days\nPending DN: {_format_number(profile.pending_dn)}"
    
    def _handle_distance(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📍 *Distance - {profile.dealer_name}*\n\nDistance: {profile.actual_distance_km:.1f} km\nTravel Time: {profile.travel_time_minutes} min\nTransport Zone: {profile.transport_zone}\nAvg Lead Distance: {profile.avg_lead_distance:.1f} km"
    
    def _handle_timeline(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📅 *Timeline - {profile.dealer_name}*\n\nFirst Order: {profile.first_order}\nLast Order: {profile.last_order}\nFirst DN: {profile.first_dn}\nLatest DN: {profile.latest_dn}\nLatest POD: {profile.latest_pod_date}\nLatest Activity: {profile.latest_activity}"
    
    def _handle_products(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📦 *Products - {profile.dealer_name}*\n\nTop Product: {profile.top_product}\nTop Model: {profile.top_model}\nProduct Count: {_format_number(profile.product_count)}\nModel Count: {_format_number(profile.model_count)}"
    
    def _handle_models(self, session: DealerSession, message: str) -> str:
        return self._handle_products(session, message)
    
    def _handle_warehouses(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"🏭 *Warehouses - {profile.dealer_name}*\n\nPrimary: {profile.primary_warehouse_name}\nTotal: {_format_number(profile.warehouse_count)}\nRevenue: {_format_currency(profile.warehouse_revenue)}\nUsed: {', '.join(profile.warehouses_used[:5]) if profile.warehouses_used else 'None'}"
    
    def _handle_cities(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"🏙️ *Cities - {profile.dealer_name}*\n\nPrimary: {profile.primary_city_name}\nTotal: {_format_number(profile.city_count)}\nRevenue: {_format_currency(profile.city_revenue)}\nServed: {', '.join(profile.cities_served[:5]) if profile.cities_served else 'None'}"
    
    def _handle_ranking(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"🏆 *Rankings - {profile.dealer_name}*\n\nRevenue Rank: #{profile.revenue_rank}\nUnits Rank: #{profile.unit_rank}\nDN Rank: #{profile.dn_rank}\nDelivery Rank: #{profile.delivery_rank}\nOverall Rank: #{profile.overall_rank}"
    
    def _handle_kpi(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📊 *KPI Scores - {profile.dealer_name}*\n\nBusiness Score: {profile.business_score:.1f}/100\nRisk Score: {profile.risk_score:.1f}/100\nRevenue Score: {profile.revenue_score:.1f}/100\nDelivery Score: {profile.delivery_score:.1f}/100\nGrowth Score: {profile.growth_score:.1f}/100"
    
    def _handle_insights(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        lines = [f"💡 *Insights - {profile.dealer_name}*", ""]
        for insight in profile.insights:
            lines.append(f"• {insight}")
        return "\n".join(lines)
    
    def _handle_recommendations(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        lines = [f"🎯 *Recommendations - {profile.dealer_name}*", ""]
        for rec in profile.recommendations:
            lines.append(f"• {rec}")
        return "\n".join(lines)
    
    def _handle_exit(self, session: DealerSession) -> str:
        session.clear()
        return "__EXIT__"
    
    def health_check(self) -> Dict[str, Any]:
        with self._session_lock:
            active_sessions = len(self._sessions)
        
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "dealer_dictionary": len(self._dealer_dict.get_all_dealers()),
            "active_sessions": active_sessions,
            "exit_command": "99",
            "timestamp": datetime.now().isoformat()
        }


# ============================================================
# SERVICE SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()

def get_dealer_service() -> DealerAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DealerAnalyticsService()
    return _service


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "DealerSession",
    "DealerProfile",
    "DealerDetectionResult",
    "DealerDictionary",
    "DealerProfileBuilder",
    "get_dealer_service",
]

# ============================================================
# BLOCK 6: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _format_currency(amount: float) -> str:
    if amount is None:
        return "PKR 0.00"
    if amount >= 1_000_000_000_000:
        return f"PKR {amount/1_000_000_000_000:,.2f} Trillion"
    elif amount >= 1_000_000_000:
        return f"PKR {amount/1_000_000_000:,.2f} Billion"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:,.2f} Million"
    else:
        return f"PKR {amount:,.2f}"

def _format_number(num: Union[int, float]) -> str:
    if num is None:
        return "0"
    return f"{num:,}"

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def _growth(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)

def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance using Haversine formula"""
    R = 6371  # Earth's radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _get_city_coordinates(city_name: str) -> Optional[Tuple[float, float]]:
    """Get coordinates for a city"""
    return CITY_COORDINATES.get(city_name.lower())

def _get_warehouse_coordinates(warehouse_name: str) -> Optional[Tuple[float, float]]:
    """Get coordinates for a warehouse"""
    return WAREHOUSE_COORDINATES.get(warehouse_name.lower())

def _get_transport_zone(distance_km: float) -> str:
    """Get transport zone based on distance"""
    if distance_km <= 50:
        return "Local (≤50 km)"
    elif distance_km <= 150:
        return "Regional (51-150 km)"
    elif distance_km <= 300:
        return "National (151-300 km)"
    elif distance_km <= 500:
        return "Extended (301-500 km)"
    else:
        return "Long Haul (>500 km)"

# ============================================================
# BLOCK 7: UNIVERSAL DEALER DETECTION ENGINE (Phase 1)
# ============================================================

class DealerDictionary:
    """
    Universal Dealer Detection Engine
    
    - Loads all dealers into memory on startup
    - Supports exact, alias, and fuzzy matching
    - Caches dealer information for fast lookup
    """
    
    _instance: Optional["DealerDictionary"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._dealers: Dict[str, Dict[str, Any]] = {}
        self._aliases: Dict[str, str] = {}
        self._normalized: Dict[str, str] = {}
        self._last_refresh: Optional[datetime] = None
        self._refresh_lock = threading.RLock()
        
        self._load_dealers()
        
        logger.info(f"📚 Dealer Dictionary loaded: {len(self._dealers)} dealers, {len(self._aliases)} aliases")
    
    def _load_dealers(self):
        """Load all dealers from database into memory"""
        if not DB_AVAILABLE:
            logger.warning("⚠️ Database not available for dealer dictionary")
            return
        
        try:
            session = SessionLocal()
            
            # Get all unique dealers
            results = session.query(
                DeliveryReport.customer_name.label('dealer_name'),
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse
            ).all()
            
            session.close()
            
            # Build dictionary
            for row in results:
                dealer_name = _text(row.dealer_name)
                if dealer_name and dealer_name != "N/A":
                    # Store dealer info
                    self._dealers[dealer_name.lower()] = {
                        'dealer_name': dealer_name,
                        'dealer_code': _text(row.dealer_code),
                        'customer_code': _text(row.customer_code),
                        'sales_office': _text(row.sales_office),
                        'sales_manager': _text(row.sales_manager),
                        'division': _text(row.division),
                        'primary_city': _text(row.ship_to_city),
                        'primary_warehouse': _text(row.warehouse),
                        'dn_count': int(row.dn_count or 0),
                        'total_revenue': float(row.total_revenue or 0),
                    }
                    
                    # Build normalized name
                    normalized = dealer_name.lower().strip()
                    self._normalized[normalized] = dealer_name
                    
                    # Build aliases
                    words = dealer_name.split()
                    for word in words:
                        if len(word) > 2:
                            self._aliases[word.lower()] = dealer_name
                    
                    # Add dealer code as alias
                    if row.dealer_code:
                        self._aliases[row.dealer_code.lower()] = dealer_name
                    
                    # Add customer code as alias
                    if row.customer_code:
                        self._aliases[row.customer_code.lower()] = dealer_name
            
            self._last_refresh = datetime.now()
            
        except Exception as e:
            logger.error(f"Failed to load dealer dictionary: {e}")
    
    def refresh(self):
        """Refresh the dealer dictionary"""
        with self._refresh_lock:
            self._load_dealers()
    
    def detect_dealer(self, text: str) -> Optional[DealerDetectionResult]:
        """
        Detect dealer from text using Universal Detection Engine
        
        Priority:
        1. Exact Match
        2. Alias Match
        3. Fuzzy Match
        4. Normalized Match
        """
        if not text or not text.strip():
            return None
        
        text_clean = text.strip().lower()
        
        # Check if dictionary needs refresh
        if self._last_refresh and (datetime.now() - self._last_refresh).seconds > DEALER_DICTIONARY_REFRESH:
            self.refresh()
        
        # 1. Exact Match
        if text_clean in self._dealers:
            dealer_info = self._dealers[text_clean]
            return DealerDetectionResult(
                dealer_name=dealer_info['dealer_name'],
                dealer_code=dealer_info.get('dealer_code', ''),
                confidence=1.0,
                match_type="exact"
            )
        
        # 2. Alias Match
        if text_clean in self._aliases:
            dealer_name = self._aliases[text_clean]
            dealer_info = self._dealers.get(dealer_name.lower(), {})
            return DealerDetectionResult(
                dealer_name=dealer_name,
                dealer_code=dealer_info.get('dealer_code', ''),
                confidence=0.95,
                match_type="alias"
            )
        
        # 3. Normalized Match
        if text_clean in self._normalized:
            dealer_name = self._normalized[text_clean]
            dealer_info = self._dealers.get(dealer_name.lower(), {})
            return DealerDetectionResult(
                dealer_name=dealer_name,
                dealer_code=dealer_info.get('dealer_code', ''),
                confidence=0.90,
                match_type="normalized"
            )
        
        # 4. Fuzzy Match (using RapidFuzz)
        if RAPIDFUZZ_AVAILABLE:
            best_match = None
            best_score = 0.0
            
            for dealer_name in self._dealers.keys():
                score = fuzz.WRatio(text_clean, dealer_name)
                if score > best_score and score > 80:
                    best_score = score
                    best_match = dealer_name
            
            if best_match:
                dealer_info = self._dealers[best_match]
                return DealerDetectionResult(
                    dealer_name=dealer_info['dealer_name'],
                    dealer_code=dealer_info.get('dealer_code', ''),
                    confidence=best_score / 100.0,
                    match_type="fuzzy"
                )
        
        # 5. Partial Match
        for dealer_name in self._dealers.keys():
            if len(text_clean) >= 3 and text_clean in dealer_name:
                dealer_info = self._dealers[dealer_name]
                confidence = len(text_clean) / len(dealer_name)
                return DealerDetectionResult(
                    dealer_name=dealer_info['dealer_name'],
                    dealer_code=dealer_info.get('dealer_code', ''),
                    confidence=min(0.85, confidence),
                    match_type="partial"
                )
        
        return None
    
    def get_dealer_info(self, dealer_name: str) -> Optional[Dict[str, Any]]:
        """Get dealer info by name"""
        return self._dealers.get(dealer_name.lower())
    
    def get_all_dealers(self) -> List[str]:
        """Get all dealer names"""
        return [info['dealer_name'] for info in self._dealers.values()]
    
    def search_dealers(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search dealers by query"""
        results = []
        query_lower = query.lower()
        
        for dealer_name, info in self._dealers.items():
            if query_lower in dealer_name or query in info.get('dealer_code', '').lower():
                results.append(info)
                if len(results) >= limit:
                    break
        
        return results

# ============================================================
# BLOCK 8: DEALER MASTER PROFILE BUILDER (Phase 2 & 3)
# ============================================================

class DealerProfileBuilder:
    """
    Builds complete Dealer Master Profile with 70-100 attributes
    """
    
    def __init__(self, session: Session):
        self.session = session
        self._dealer_dict = DealerDictionary()
    
    def build_profile(self, dealer_identifier: str) -> Optional[DealerProfile]:
        """Build complete dealer profile"""
        # Detect dealer first
        detection = self._dealer_dict.detect_dealer(dealer_identifier)
        if not detection:
            return None
        
        dealer_name = detection.dealer_name
        dealer_code = detection.dealer_code
        
        # Get base data
        base_data = self._get_base_data(dealer_name, dealer_code)
        if not base_data:
            return None
        
        profile = DealerProfile()
        
        # ============================================================
        # Identity
        # ============================================================
        profile.dealer_name = dealer_name
        profile.dealer_code = dealer_code
        profile.customer_code = base_data.get('customer_code', '')
        profile.sales_office = base_data.get('sales_office', '')
        profile.sales_manager = base_data.get('sales_manager', '')
        profile.division = base_data.get('division', '')
        
        # ============================================================
        # Location
        # ============================================================
        profile.primary_city = base_data.get('primary_city', '')
        profile.primary_warehouse = base_data.get('primary_warehouse', '')
        
        # Get coordinates
        city_coords = _get_city_coordinates(profile.primary_city)
        if city_coords:
            profile.latitude, profile.longitude = city_coords
            profile.city_coordinates = city_coords
        
        warehouse_coords = _get_warehouse_coordinates(profile.primary_warehouse)
        if warehouse_coords:
            profile.warehouse_lat, profile.warehouse_lon = warehouse_coords
        
        # ============================================================
        # Financial
        # ============================================================
        profile.total_revenue = base_data.get('total_revenue', 0)
        profile.total_dn = base_data.get('total_dn', 0)
        profile.total_units = base_data.get('total_units', 0)
        
        profile.avg_revenue = profile.total_revenue / max(1, profile.total_dn)
        profile.avg_units_per_dn = profile.total_units / max(1, profile.total_dn)
        profile.avg_revenue_per_dn = profile.total_revenue / max(1, profile.total_dn)
        
        # Monthly revenue
        monthly_data = self._get_monthly_data(dealer_name)
        if monthly_data:
            profile.monthly_revenue = monthly_data.get('revenue', 0)
            profile.revenue_growth = monthly_data.get('growth', 0)
        
        # ============================================================
        # Delivery
        # ============================================================
        delivery_data = self._get_delivery_data(dealer_name)
        if delivery_data:
            profile.delivered_dn = delivery_data.get('delivered_dn', 0)
            profile.pending_dn = delivery_data.get('pending_dn', 0)
            profile.pending_pgi = delivery_data.get('pending_pgi', 0)
            profile.pending_pod = delivery_data.get('pending_pod', 0)
            profile.delivery_pct = delivery_data.get('delivery_pct', 0)
            profile.pgi_pct = delivery_data.get('pgi_pct', 0)
            profile.pod_pct = delivery_data.get('pod_pct', 0)
            profile.avg_delivery_days = delivery_data.get('avg_delivery_days', 0)
            profile.avg_pod_days = delivery_data.get('avg_pod_days', 0)
            profile.oldest_pending = delivery_data.get('oldest_pending', '')
            profile.latest_delivery = delivery_data.get('latest_delivery', '')
            profile.latest_pod = delivery_data.get('latest_pod', '')
        
        # ============================================================
        # Warehouses
        # ============================================================
        warehouse_data = self._get_warehouse_data(dealer_name)
        if warehouse_data:
            profile.warehouses_used = warehouse_data.get('warehouses', [])
            profile.primary_warehouse_name = warehouse_data.get('primary', '')
            profile.warehouse_count = len(profile.warehouses_used)
            profile.warehouse_revenue = warehouse_data.get('total_revenue', 0)
            profile.warehouse_units = warehouse_data.get('total_units', 0)
        
        # ============================================================
        # Cities
        # ============================================================
        city_data = self._get_city_data(dealer_name)
        if city_data:
            profile.cities_served = city_data.get('cities', [])
            profile.primary_city_name = city_data.get('primary', '')
            profile.city_count = len(profile.cities_served)
            profile.city_revenue = city_data.get('total_revenue', 0)
        
        # ============================================================
        # Products
        # ============================================================
        product_data = self._get_product_data(dealer_name)
        if product_data:
            profile.top_product = product_data.get('top_product', '')
            profile.bottom_product = product_data.get('bottom_product', '')
            profile.top_model = product_data.get('top_model', '')
            profile.bottom_model = product_data.get('bottom_model', '')
            profile.top_material = product_data.get('top_material', '')
            profile.top_division = product_data.get('top_division', '')
            profile.product_count = product_data.get('product_count', 0)
            profile.model_count = product_data.get('model_count', 0)
            profile.material_count = product_data.get('material_count', 0)
        
        # ============================================================
        # Distance Intelligence
        # ============================================================
        distance_data = self._calculate_distance(profile)
        if distance_data:
            profile.actual_distance_km = distance_data.get('distance_km', 0)
            profile.estimated_distance_km = distance_data.get('estimated_distance_km', 0)
            profile.travel_time_minutes = distance_data.get('travel_time_minutes', 0)
            profile.avg_lead_distance = distance_data.get('avg_lead_distance', 0)
            profile.longest_route_km = distance_data.get('longest_route_km', 0)
            profile.shortest_route_km = distance_data.get('shortest_route_km', 0)
            profile.transport_zone = distance_data.get('transport_zone', '')
        
        # ============================================================
        # Timeline
        # ============================================================
        timeline_data = self._get_timeline_data(dealer_name)
        if timeline_data:
            profile.first_order = timeline_data.get('first_order', '')
            profile.last_order = timeline_data.get('last_order', '')
            profile.first_dn = timeline_data.get('first_dn', '')
            profile.first_pgi = timeline_data.get('first_pgi', '')
            profile.first_pod = timeline_data.get('first_pod', '')
            profile.latest_dn = timeline_data.get('latest_dn', '')
            profile.latest_pgi = timeline_data.get('latest_pgi', '')
            profile.latest_pod_date = timeline_data.get('latest_pod_date', '')
            profile.latest_activity = timeline_data.get('latest_activity', '')
        
        # ============================================================
        # Rankings (Phase 5)
        # ============================================================
        rankings = self._calculate_rankings(dealer_name, profile)
        if rankings:
            profile.revenue_rank = rankings.get('revenue_rank', 0)
            profile.unit_rank = rankings.get('unit_rank', 0)
            profile.dn_rank = rankings.get('dn_rank', 0)
            profile.delivery_rank = rankings.get('delivery_rank', 0)
            profile.warehouse_rank = rankings.get('warehouse_rank', 0)
            profile.distance_rank = rankings.get('distance_rank', 0)
            profile.growth_rank = rankings.get('growth_rank', 0)
            profile.overall_rank = rankings.get('overall_rank', 0)
        
        # ============================================================
        # KPI Scores (Phase 4)
        # ============================================================
        scores = self._calculate_kpi_scores(profile)
        profile.revenue_score = scores.get('revenue_score', 0)
        profile.delivery_score = scores.get('delivery_score', 0)
        profile.pgi_score = scores.get('pgi_score', 0)
        profile.pod_score = scores.get('pod_score', 0)
        profile.growth_score = scores.get('growth_score', 0)
        profile.warehouse_score = scores.get('warehouse_score', 0)
        profile.distance_score = scores.get('distance_score', 0)
        profile.product_mix_score = scores.get('product_mix_score', 0)
        profile.volume_score = scores.get('volume_score', 0)
        profile.customer_score = scores.get('customer_score', 0)
        profile.business_score = scores.get('business_score', 0)
        profile.risk_score = scores.get('risk_score', 0)
        
        # ============================================================
        # Recommendations (Phase 9)
        # ============================================================
        profile.recommendations = self._generate_recommendations(profile)
        profile.insights = self._generate_insights(profile)
        
        return profile
    
    def _get_base_data(self, dealer_name: str, dealer_code: str) -> Dict[str, Any]:
        """Get base dealer data"""
        try:
            sql = f"""
                SELECT 
                    customer_name,
                    dealer_code,
                    customer_code,
                    sales_office,
                    sales_manager,
                    division,
                    ship_to_city as primary_city,
                    warehouse as primary_warehouse,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                   OR LOWER(dealer_code) = LOWER('{dealer_code}')
                GROUP BY customer_name, dealer_code, customer_code, sales_office, 
                         sales_manager, division, ship_to_city, warehouse
                ORDER BY total_revenue DESC
                LIMIT 1
            """
            result = self.session.execute(text(sql))
            row = result.fetchone()
            
            if row:
                return dict(zip(row.keys(), row))
            return {}
        except Exception as e:
            logger.error(f"Failed to get base data: {e}")
            return {}
    
    def _get_monthly_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get monthly revenue data"""
        try:
            sql = f"""
                SELECT 
                    TO_CHAR(dn_create_date, 'YYYY-MM') as month,
                    COALESCE(SUM(dn_amount), 0) as revenue
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND dn_create_date IS NOT NULL
                GROUP BY TO_CHAR(dn_create_date, 'YYYY-MM')
                ORDER BY month DESC
                LIMIT 2
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            if len(rows) >= 2:
                current = rows[0]
                previous = rows[1]
                return {
                    'revenue': float(current[1] or 0),
                    'growth': _growth(float(current[1] or 0), float(previous[1] or 0))
                }
            elif len(rows) == 1:
                return {
                    'revenue': float(rows[0][1] or 0),
                    'growth': 0
                }
            return {}
        except Exception as e:
            logger.error(f"Failed to get monthly data: {e}")
            return {}
    
    def _get_delivery_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get delivery data"""
        try:
            sql = f"""
                SELECT 
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered_dn,
                    COUNT(DISTINCT CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_no END) as pending_dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) as pending_pgi,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) as pending_pod,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed,
                    AVG(CASE WHEN good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as avg_delivery_days,
                    AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as avg_pod_days,
                    MIN(CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_create_date END) as oldest_pending,
                    MAX(CASE WHEN pod_date IS NOT NULL THEN pod_date END) as latest_pod,
                    MAX(CASE WHEN good_issue_date IS NOT NULL THEN good_issue_date END) as latest_delivery
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
            """
            result = self.session.execute(text(sql))
            row = result.fetchone()
            
            if row:
                data = dict(zip(row.keys(), row))
                total_dn = data.get('total_dn', 1)
                return {
                    'delivered_dn': int(data.get('delivered_dn', 0) or 0),
                    'pending_dn': int(data.get('pending_dn', 0) or 0),
                    'pending_pgi': int(data.get('pending_pgi', 0) or 0),
                    'pending_pod': int(data.get('pending_pod', 0) or 0),
                    'delivery_pct': _percent(data.get('delivered_dn', 0), total_dn),
                    'pgi_pct': _percent(data.get('pgi_completed', 0), total_dn),
                    'pod_pct': _percent(data.get('pod_completed', 0), total_dn),
                    'avg_delivery_days': float(data.get('avg_delivery_days', 0) or 0),
                    'avg_pod_days': float(data.get('avg_pod_days', 0) or 0),
                    'oldest_pending': _date_text(data.get('oldest_pending')),
                    'latest_pod': _date_text(data.get('latest_pod')),
                    'latest_delivery': _date_text(data.get('latest_delivery')),
                }
            return {}
        except Exception as e:
            logger.error(f"Failed to get delivery data: {e}")
            return {}
    
    def _get_warehouse_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get warehouse data"""
        try:
            sql = f"""
                SELECT 
                    warehouse,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND warehouse IS NOT NULL
                GROUP BY warehouse
                ORDER BY revenue DESC
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            warehouses = []
            total_revenue = 0
            total_units = 0
            
            for row in rows:
                warehouse = row[0]
                units = float(row[1] or 0)
                revenue = float(row[2] or 0)
                warehouses.append(warehouse)
                total_revenue += revenue
                total_units += units
            
            return {
                'warehouses': warehouses,
                'primary': warehouses[0] if warehouses else '',
                'total_revenue': total_revenue,
                'total_units': total_units
            }
        except Exception as e:
            logger.error(f"Failed to get warehouse data: {e}")
            return {}
    
    def _get_city_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get city data"""
        try:
            sql = f"""
                SELECT 
                    ship_to_city as city,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND ship_to_city IS NOT NULL
                GROUP BY ship_to_city
                ORDER BY revenue DESC
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            cities = []
            total_revenue = 0
            
            for row in rows:
                city = row[0]
                revenue = float(row[1] or 0)
                cities.append(city)
                total_revenue += revenue
            
            return {
                'cities': cities,
                'primary': cities[0] if cities else '',
                'total_revenue': total_revenue
            }
        except Exception as e:
            logger.error(f"Failed to get city data: {e}")
            return {}
    
    def _get_product_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get product data"""
        try:
            sql = f"""
                SELECT 
                    customer_model as product,
                    material_no,
                    division,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND customer_model IS NOT NULL
                GROUP BY customer_model, material_no, division
                ORDER BY revenue DESC
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            if not rows:
                return {}
            
            products = []
            models = []
            materials = []
            
            for row in rows:
                product = row[0]
                material = row[1]
                products.append(product)
                if material:
                    materials.append(material)
                if product:
                    models.append(product)
            
            return {
                'top_product': products[0] if products else '',
                'bottom_product': products[-1] if products else '',
                'top_model': models[0] if models else '',
                'bottom_model': models[-1] if models else '',
                'top_material': materials[0] if materials else '',
                'top_division': rows[0][2] if rows else '',
                'product_count': len(set(products)),
                'model_count': len(set(models)),
                'material_count': len(set(materials))
            }
        except Exception as e:
            logger.error(f"Failed to get product data: {e}")
            return {}
    
    def _get_timeline_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get timeline data"""
        try:
            sql = f"""
                SELECT 
                    MIN(dn_create_date) as first_order,
                    MAX(dn_create_date) as last_order,
                    MIN(CASE WHEN good_issue_date IS NOT NULL THEN good_issue_date END) as first_pgi,
                    MIN(CASE WHEN pod_date IS NOT NULL THEN pod_date END) as first_pod,
                    MAX(dn_create_date) as latest_dn,
                    MAX(CASE WHEN good_issue_date IS NOT NULL THEN good_issue_date END) as latest_pgi,
                    MAX(CASE WHEN pod_date IS NOT NULL THEN pod_date END) as latest_pod,
                    MAX(GREATEST(dn_create_date, good_issue_date, pod_date)) as latest_activity
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
            """
            result = self.session.execute(text(sql))
            row = result.fetchone()
            
            if row:
                return {
                    'first_order': _date_text(row[0]),
                    'last_order': _date_text(row[1]),
                    'first_pgi': _date_text(row[2]),
                    'first_pod': _date_text(row[3]),
                    'latest_dn': _date_text(row[4]),
                    'latest_pgi': _date_text(row[5]),
                    'latest_pod_date': _date_text(row[6]),
                    'latest_activity': _date_text(row[7])
                }
            return {}
        except Exception as e:
            logger.error(f"Failed to get timeline data: {e}")
            return {}
    
    def _calculate_distance(self, profile: DealerProfile) -> Dict[str, Any]:
        """Calculate distance intelligence"""
        distance_data = {}
        
        # Get dealer coordinates
        dealer_lat = profile.latitude
        dealer_lon = profile.longitude
        
        # Get warehouse coordinates
        warehouse_lat = profile.warehouse_lat
        warehouse_lon = profile.warehouse_lon
        
        if dealer_lat and dealer_lon and warehouse_lat and warehouse_lon:
            # Calculate distance using Haversine
            distance_km = _calculate_distance(
                dealer_lat, dealer_lon,
                warehouse_lat, warehouse_lon
            )
            
            distance_data['distance_km'] = round(distance_km, 1)
            distance_data['estimated_distance_km'] = round(distance_km * 1.15, 1)  # 15% road factor
            distance_data['travel_time_minutes'] = int(distance_km / 50 * 60)  # 50 km/h average
            distance_data['transport_zone'] = _get_transport_zone(distance_km)
            distance_data['avg_lead_distance'] = distance_km
            distance_data['longest_route_km'] = distance_km * 1.1
            distance_data['shortest_route_km'] = distance_km * 0.9
        
        return distance_data
    
    def _calculate_rankings(self, dealer_name: str, profile: DealerProfile) -> Dict[str, Any]:
        """Calculate dealer rankings"""
        rankings = {}
        
        try:
            # Get all dealers for ranking
            sql = """
                SELECT 
                    customer_name,
                    COUNT(DISTINCT dn_no) as dn_count,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered_dn
                FROM delivery_reports
                WHERE customer_name IS NOT NULL
                GROUP BY customer_name
            """
            result = self.session.execute(text(sql))
            rows = result.fetchall()
            
            all_dealers = []
            for row in rows:
                all_dealers.append({
                    'name': row[0],
                    'dn_count': int(row[1] or 0),
                    'units': float(row[2] or 0),
                    'revenue': float(row[3] or 0),
                    'delivered': int(row[4] or 0)
                })
            
            if not all_dealers:
                return {}
            
            # Sort by revenue
            sorted_by_revenue = sorted(all_dealers, key=lambda x: x['revenue'], reverse=True)
            for i, d in enumerate(sorted_by_revenue, 1):
                if d['name'] == dealer_name:
                    rankings['revenue_rank'] = i
                    break
            
            # Sort by units
            sorted_by_units = sorted(all_dealers, key=lambda x: x['units'], reverse=True)
            for i, d in enumerate(sorted_by_units, 1):
                if d['name'] == dealer_name:
                    rankings['unit_rank'] = i
                    break
            
            # Sort by DN
            sorted_by_dn = sorted(all_dealers, key=lambda x: x['dn_count'], reverse=True)
            for i, d in enumerate(sorted_by_dn, 1):
                if d['name'] == dealer_name:
                    rankings['dn_rank'] = i
                    break
            
            # Sort by delivery
            sorted_by_delivery = sorted(all_dealers, key=lambda x: x['delivered'] / max(1, x['dn_count']), reverse=True)
            for i, d in enumerate(sorted_by_delivery, 1):
                if d['name'] == dealer_name:
                    rankings['delivery_rank'] = i
                    break
            
            # Calculate overall rank (average of all ranks)
            rank_sum = sum([
                rankings.get('revenue_rank', 0),
                rankings.get('unit_rank', 0),
                rankings.get('dn_rank', 0),
                rankings.get('delivery_rank', 0)
            ])
            total_ranks = len([r for r in rankings.values() if r > 0])
            rankings['overall_rank'] = int(rank_sum / max(1, total_ranks)) if total_ranks > 0 else 0
            
        except Exception as e:
            logger.error(f"Failed to calculate rankings: {e}")
        
        return rankings
    
    def _calculate_kpi_scores(self, profile: DealerProfile) -> Dict[str, float]:
        """Calculate KPI scores"""
        scores = {}
        
        # Revenue Score (0-100)
        revenue = profile.total_revenue
        scores['revenue_score'] = min(100, (revenue / 1000000) * 10)  # Scale by 1M
        
        # Delivery Score (0-100)
        scores['delivery_score'] = profile.delivery_pct
        
        # PGI Score (0-100)
        scores['pgi_score'] = profile.pgi_pct
        
        # POD Score (0-100)
        scores['pod_score'] = profile.pod_pct
        
        # Growth Score (0-100)
        growth = profile.revenue_growth
        if growth > 0:
            scores['growth_score'] = min(100, growth * 10)
        else:
            scores['growth_score'] = max(0, 100 + growth * 5)
        
        # Warehouse Score (0-100)
        warehouse_count = profile.warehouse_count
        scores['warehouse_score'] = min(100, warehouse_count * 15)
        
        # Distance Score (0-100)
        distance = profile.actual_distance_km
        if distance <= 50:
            scores['distance_score'] = 100
        elif distance <= 150:
            scores['distance_score'] = 80
        elif distance <= 300:
            scores['distance_score'] = 60
        elif distance <= 500:
            scores['distance_score'] = 40
        else:
            scores['distance_score'] = 20
        
        # Product Mix Score (0-100)
        product_count = profile.product_count
        scores['product_mix_score'] = min(100, product_count * 5)
        
        # Volume Score (0-100)
        units = profile.total_units
        scores['volume_score'] = min(100, (units / 100) * 10)
        
        # Customer Score (0-100)
        city_count = profile.city_count
        scores['customer_score'] = min(100, city_count * 10)
        
        # Business Score (weighted average)
        scores['business_score'] = (
            scores['revenue_score'] * 0.20 +
            scores['delivery_score'] * 0.15 +
            scores['pgi_score'] * 0.10 +
            scores['pod_score'] * 0.10 +
            scores['growth_score'] * 0.15 +
            scores['warehouse_score'] * 0.10 +
            scores['volume_score'] * 0.10 +
            scores['customer_score'] * 0.10
        )
        scores['business_score'] = round(scores['business_score'], 1)
        
        # Risk Score (inverse of business score)
        scores['risk_score'] = round(100 - scores['business_score'], 1)
        
        return scores
    
    def _generate_insights(self, profile: DealerProfile) -> List[str]:
        """Generate insights from profile"""
        insights = []
        
        # Revenue insights
        revenue = profile.total_revenue
        if revenue > 10000000:
            insights.append(f"High revenue performer: {_format_currency(revenue)}")
        elif revenue > 5000000:
            insights.append(f"Good revenue generation: {_format_currency(revenue)}")
        else:
            insights.append("Opportunity to increase revenue")
        
        # Delivery insights
        delivery_pct = profile.delivery_pct
        if delivery_pct >= 95:
            insights.append("Excellent delivery performance")
        elif delivery_pct >= 85:
            insights.append("Good delivery performance")
        else:
            insights.append("Delivery performance needs improvement")
        
        # Growth insights
        growth = profile.revenue_growth
        if growth > 20:
            insights.append(f"Strong revenue growth at {growth:.1f}%")
        elif growth > 10:
            insights.append(f"Healthy revenue growth at {growth:.1f}%")
        elif growth > 0:
            insights.append(f"Steady revenue growth at {growth:.1f}%")
        else:
            insights.append(f"Revenue decline of {growth:.1f}% - needs attention")
        
        # Product insights
        product_count = profile.product_count
        if product_count > 10:
            insights.append(f"Wide product portfolio: {product_count} products")
        elif product_count > 5:
            insights.append(f"Good product variety: {product_count} products")
        else:
            insights.append("Limited product range - opportunity for expansion")
        
        # Warehouse insights
        warehouse_count = profile.warehouse_count
        if warehouse_count > 3:
            insights.append(f"Strong warehouse network: {warehouse_count} warehouses")
        elif warehouse_count > 1:
            insights.append(f"Multiple warehouses: {warehouse_count} warehouses")
        else:
            insights.append("Single warehouse dependency - diversification recommended")
        
        # Risk insights
        risk_score = profile.risk_score
        if risk_score > 50:
            insights.append(f"High risk score: {risk_score:.1f}/100 - requires attention")
        elif risk_score > 30:
            insights.append(f"Moderate risk score: {risk_score:.1f}/100 - monitor closely")
        
        # Add business insights
        if profile.business_score >= 85:
            insights.append("Excellent overall business health")
        elif profile.business_score >= 70:
            insights.append("Good overall business health")
        elif profile.business_score < 50:
            insights.append("Critical business health - immediate action required")
        
        return insights[:10]  # Limit to top 10 insights
    
    def _generate_recommendations(self, profile: DealerProfile) -> List[str]:
        """Generate recommendations based on profile"""
        recommendations = []
        
        # Delivery recommendations
        if profile.delivery_pct < 85:
            recommendations.append("📦 Improve delivery speed and reliability")
            recommendations.append("📋 Review delivery routes for optimization")
        
        # Pending recommendations
        if profile.pending_dn > 20:
            recommendations.append(f"⏳ Escalate {profile.pending_dn} pending DNs for resolution")
        elif profile.pending_dn > 10:
            recommendations.append("📋 Review pending orders for timely closure")
        
        # Product recommendations
        if profile.product_count < 5:
            recommendations.append("🛒 Expand product portfolio to increase revenue")
        
        # Warehouse recommendations
        if profile.warehouse_count == 1:
            recommendations.append("🏭 Consider diversifying warehouse coverage")
        
        # City recommendations
        if profile.city_count < 3:
            recommendations.append("🌍 Expand to new cities for growth")
        
        # Growth recommendations
        if profile.revenue_growth < 0:
            recommendations.append("📈 Develop growth strategy to reverse revenue decline")
        
        # Distance recommendations
        if profile.actual_distance_km > 300:
            recommendations.append("🚚 Consider warehouse closer to dealer location")
        
        # Business recommendations
        if profile.business_score < 70:
            recommendations.append("📊 Develop action plan to improve business score")
        
        if not recommendations:
            recommendations.append("✅ Maintain current performance levels")
            recommendations.append("📊 Continue monitoring key metrics")
        
        return recommendations[:10]  # Limit to top 10 recommendations

# ============================================================
# BLOCK 9: MENU REGISTRY
# ============================================================

class DealerMenuRegistry:
    """Registry of all dealer menus and their items"""
    
    MENUS = {
        "main": {
            "id": "main",
            "name": "DEALER INTELLIGENCE PLATFORM",
            "items": [
                {"id": "1", "name": "Dashboard", "handler": "handle_dashboard_menu", "icon": "📊"},
                {"id": "2", "name": "Intelligence", "handler": "handle_intelligence_menu", "icon": "🧠"},
                {"id": "3", "name": "AI Assistant", "handler": "handle_ai_assistant_menu", "icon": "🤖"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "dashboard": {
            "id": "dashboard",
            "name": "DEALER DASHBOARD",
            "items": [
                {"id": "1", "name": "Intelligence Report", "handler": "handle_intelligence", "icon": "🧠"},
                {"id": "2", "name": "Revenue", "handler": "handle_revenue", "icon": "💰"},
                {"id": "3", "name": "Units", "handler": "handle_units", "icon": "📦"},
                {"id": "4", "name": "DN", "handler": "handle_dn", "icon": "📄"},
                {"id": "5", "name": "Delivery", "handler": "handle_delivery", "icon": "🚚"},
                {"id": "6", "name": "Distance", "handler": "handle_distance", "icon": "📍"},
                {"id": "7", "name": "Timeline", "handler": "handle_timeline", "icon": "📅"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "intelligence": {
            "id": "intelligence",
            "name": "DEALER INTELLIGENCE",
            "items": [
                {"id": "1", "name": "Products", "handler": "handle_products", "icon": "📦"},
                {"id": "2", "name": "Models", "handler": "handle_models", "icon": "🏷️"},
                {"id": "3", "name": "Warehouses", "handler": "handle_warehouses", "icon": "🏭"},
                {"id": "4", "name": "Cities", "handler": "handle_cities", "icon": "🏙️"},
                {"id": "5", "name": "Rankings", "handler": "handle_ranking", "icon": "🏆"},
                {"id": "6", "name": "KPI", "handler": "handle_kpi", "icon": "📊"},
                {"id": "7", "name": "Insights", "handler": "handle_insights", "icon": "💡"},
                {"id": "8", "name": "Recommendations", "handler": "handle_recommendations", "icon": "🎯"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "ai_assistant": {
            "id": "ai_assistant",
            "name": "DEALER AI ASSISTANT",
            "items": [
                {"id": "1", "name": "Ask Question", "handler": "handle_ai_ask", "icon": "❓"},
                {"id": "2", "name": "Analysis", "handler": "handle_ai_analysis", "icon": "📊"},
                {"id": "3", "name": "Insights", "handler": "handle_ai_insights", "icon": "💡"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        }
    }

# ============================================================
# BLOCK 10: DEALER RENDERER
# ============================================================

class DealerRenderer:
    """Render dealer responses for WhatsApp"""
    
    MENU_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━"
    
    @classmethod
    def _render_menu_footer(cls, menu_type: str = "main") -> str:
        menu = DealerMenuRegistry.MENUS.get(menu_type, DealerMenuRegistry.MENUS["main"])
        
        lines = ["", cls.MENU_SEPARATOR, ""]
        lines.append(f"📋 *{menu['name']}*")
        lines.append("")
        
        for item in menu["items"]:
            lines.append(f"{item['id']}. {item['icon']} {item['name']}")
        
        lines.append("")
        lines.append("Reply with a number or type your question:")
        
        return "\n".join(lines)
    
    @classmethod
    def render_main_menu(cls) -> str:
        return cls._render_menu_footer("main")
    
    @classmethod
    def render_dashboard_menu(cls) -> str:
        return cls._render_menu_footer("dashboard")
    
    @classmethod
    def render_intelligence_menu(cls) -> str:
        return cls._render_menu_footer("intelligence")
    
    @classmethod
    def render_ai_assistant_menu(cls) -> str:
        return cls._render_menu_footer("ai_assistant")
    
    @classmethod
    def render_with_menu(cls, content: str, menu_type: str = "main") -> str:
        return f"{content}\n{cls._render_menu_footer(menu_type)}"
    
    @classmethod
    def render_intelligence_report(cls, profile: DealerProfile) -> str:
        """Render complete 360° Dealer Intelligence Report (Phase 12)"""
        if not profile:
            return "⚠️ No dealer profile available."
        
        lines = []
        
        # Header
        lines.append("🧠 *DEALER INTELLIGENCE REPORT*")
        lines.append("")
        
        # Identity
        lines.append("📌 *Identity*")
        lines.append(f"Name: {profile.dealer_name}")
        lines.append(f"Code: {profile.dealer_code}")
        lines.append(f"Customer Code: {profile.customer_code}")
        lines.append(f"Sales Office: {profile.sales_office}")
        lines.append(f"Sales Manager: {profile.sales_manager}")
        lines.append(f"Division: {profile.division}")
        lines.append("")
        
        # Financial
        lines.append("💰 *Financial Intelligence*")
        lines.append(f"Revenue: {_format_currency(profile.total_revenue)}")
        lines.append(f"Avg Revenue/DN: {_format_currency(profile.avg_revenue_per_dn)}")
        lines.append(f"Monthly Revenue: {_format_currency(profile.monthly_revenue)}")
        lines.append(f"Revenue Growth: {profile.revenue_growth:+.1f}%")
        lines.append(f"Revenue Rank: #{profile.revenue_rank}")
        lines.append("")
        
        # Operations
        lines.append("📦 *Operations Intelligence*")
        lines.append(f"DN: {_format_number(profile.total_dn)}")
        lines.append(f"Units: {_format_number(profile.total_units)}")
        lines.append(f"Avg Units/DN: {profile.avg_units_per_dn:.1f}")
        lines.append(f"Products: {_format_number(profile.product_count)}")
        lines.append(f"Models: {_format_number(profile.model_count)}")
        lines.append("")
        
        # Delivery
        lines.append("🚚 *Delivery Intelligence*")
        lines.append(f"Delivery: {profile.delivery_pct:.1f}%")
        lines.append(f"PGI: {profile.pgi_pct:.1f}%")
        lines.append(f"POD: {profile.pod_pct:.1f}%")
        lines.append(f"Pending DN: {_format_number(profile.pending_dn)}")
        lines.append(f"Avg Delivery: {profile.avg_delivery_days:.1f} Days")
        lines.append(f"Avg POD: {profile.avg_pod_days:.1f} Days")
        lines.append("")
        
        # Warehouse
        lines.append("🏭 *Warehouse Intelligence*")
        lines.append(f"Primary: {profile.primary_warehouse_name or 'N/A'}")
        lines.append(f"Warehouses: {_format_number(profile.warehouse_count)}")
        lines.append(f"Warehouse Revenue: {_format_currency(profile.warehouse_revenue)}")
        if profile.warehouses_used:
            lines.append(f"Used: {', '.join(profile.warehouses_used[:3])}")
            if len(profile.warehouses_used) > 3:
                lines.append(f"... and {len(profile.warehouses_used) - 3} more")
        lines.append("")
        
        # Cities
        lines.append("🏙️ *City Intelligence*")
        lines.append(f"Primary: {profile.primary_city_name or 'N/A'}")
        lines.append(f"Cities: {_format_number(profile.city_count)}")
        if profile.cities_served:
            lines.append(f"Served: {', '.join(profile.cities_served[:3])}")
            if len(profile.cities_served) > 3:
                lines.append(f"... and {len(profile.cities_served) - 3} more")
        lines.append("")
        
        # Distance
        lines.append("📍 *Distance Intelligence*")
        lines.append(f"Distance: {profile.actual_distance_km:.1f} km")
        lines.append(f"Travel Time: {profile.travel_time_minutes} min")
        lines.append(f"Transport Zone: {profile.transport_zone}")
        lines.append("")
        
        # Rankings
        lines.append("🏆 *Rankings*")
        lines.append(f"Revenue: #{profile.revenue_rank}")
        lines.append(f"Units: #{profile.unit_rank}")
        lines.append(f"Delivery: #{profile.delivery_rank}")
        lines.append(f"Overall: #{profile.overall_rank}")
        lines.append("")
        
        # Scores
        lines.append("📊 *KPI Scores*")
        lines.append(f"Business Score: {profile.business_score:.1f}/100")
        lines.append(f"Risk Score: {profile.risk_score:.1f}/100")
        lines.append(f"Revenue Score: {profile.revenue_score:.1f}/100")
        lines.append(f"Delivery Score: {profile.delivery_score:.1f}/100")
        lines.append(f"Growth Score: {profile.growth_score:.1f}/100")
        lines.append("")
        
        # Timeline
        lines.append("📅 *Timeline*")
        lines.append(f"First Order: {profile.first_order}")
        lines.append(f"Last Order: {profile.last_order}")
        lines.append(f"Latest DN: {profile.latest_dn}")
        lines.append(f"Latest POD: {profile.latest_pod_date}")
        lines.append("")
        
        # Insights
        if profile.insights:
            lines.append("💡 *Key Insights*")
            for insight in profile.insights[:5]:
                lines.append(f"• {insight}")
            lines.append("")
        
        # Recommendations
        if profile.recommendations:
            lines.append("🎯 *Recommendations*")
            for rec in profile.recommendations[:5]:
                lines.append(f"• {rec}")
            lines.append("")
        
        return "\n".join(lines)
    
    @classmethod
    def render_ranking(cls, ranking: List[Dict[str, Any]], metric: str = "Revenue", limit: int = 10) -> str:
        if not ranking:
            return f"🏆 *Dealer Rankings by {metric}*\n\nNo dealers found."
        
        lines = [f"🏆 *Dealer Rankings by {metric}*", ""]
        
        for i, item in enumerate(ranking[:limit], 1):
            dealer = item.get('dealer', 'Unknown')
            value = item.get('value', 'N/A')
            
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            lines.append(f"{medal} {dealer}: {value}")
        
        return "\n".join(lines)

# ============================================================
# BLOCK 11: MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """Enterprise Dealer Intelligence Platform - Fully Independent"""
    
    _instance: Optional["DealerAnalyticsService"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._service_name = "dealer_analytics"
        self._version = "7.0"
        
        # Initialize engines
        self._dealer_dict = DealerDictionary()
        self._renderer = DealerRenderer()
        
        # Sessions
        self._sessions: Dict[str, DealerSession] = {}
        self._session_lock = threading.RLock()
        
        logger.info("=" * 80)
        logger.info(f"🚀 Dealer Intelligence Platform v{self._version} initialized")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info(f"   📚 Dealer Dictionary: {len(self._dealer_dict.get_all_dealers())} dealers")
        logger.info(f"   🔍 Universal Detection: {'Enabled' if RAPIDFUZZ_AVAILABLE else 'Limited'}")
        logger.info(f"   📋 Auto-Menu: {'Enabled' if DEALER_MENU_AUTO_SHOW else 'Disabled'}")
        logger.info("=" * 80)
    
    def _get_session(self, session_id: str) -> DealerSession:
        with self._session_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = DealerSession(session_id=session_id)
                logger.info(f"🆕 New dealer session created for {session_id}")
            return self._sessions[session_id]
    
    def _get_db_session(self) -> Optional[Session]:
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def _get_menu_type(self, session: DealerSession) -> str:
        if session.menu_state == DealerMenuState.DASHBOARD:
            return "dashboard"
        elif session.menu_state == DealerMenuState.INTELLIGENCE:
            return "intelligence"
        elif session.menu_state == DealerMenuState.AI_ASSISTANT:
            return "ai_assistant"
        else:
            return "main"
    
    def _render_response(self, content: str, session: DealerSession) -> str:
        menu_type = self._get_menu_type(session)
        return self._renderer.render_with_menu(content, menu_type)
    
    def get_main_menu(self) -> str:
        return self._renderer.render_main_menu()
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        Main entry point for dealer processing.
        
        This is the ONLY external interface.
        All processing stays inside this module.
        """
        session = self._get_session(sender)
        
        # AUTO-MENU: Show menu on first entry
        if not session.menu_shown:
            session.menu_shown = True
            logger.info(f"📋 Auto-showing dealer menu for {sender}")
            return self._render_response("🧠 Welcome to the Dealer Intelligence Platform!", session)
        
        if not message or not message.strip():
            return self._render_response("Please provide a dealer name or select a menu option.", session)
        
        message_clean = message.strip()
        logger.info(f"📊 Dealer Query: '{message_clean}' from {sender}")
        
        session.touch()
        
        # ============================================================
        # STEP 1: Exit (99)
        # ============================================================
        if message_clean == "99":
            session.clear()
            logger.info(f"🚪 Dealer session exited for {sender}")
            return "__EXIT__"
        
        # ============================================================
        # STEP 2: Menu navigation (0, 1, 2, 3)
        # ============================================================
        if message_clean == "0":
            session.menu_state = DealerMenuState.MAIN
            return self._render_response("Main Menu", session)
        
        if message_clean == "1":
            session.menu_state = DealerMenuState.DASHBOARD
            return self._render_response("📊 *Dashboard Menu*\n\nSelect an option below:", session)
        
        if message_clean == "2":
            session.menu_state = DealerMenuState.INTELLIGENCE
            return self._render_response("🧠 *Intelligence Menu*\n\nSelect an option below:", session)
        
        if message_clean == "3":
            session.menu_state = DealerMenuState.AI_ASSISTANT
            return self._render_response("🤖 *AI Assistant*\n\nAsk me anything about dealers:", session)
        
        # ============================================================
        # STEP 3: Universal Dealer Detection (Phase 1)
        # ============================================================
        detection = self._dealer_dict.detect_dealer(message_clean)
        
        if detection:
            dealer_name = detection.dealer_name
            confidence = detection.confidence
            match_type = detection.match_type
            
            logger.info(f"🎯 Dealer detected: {dealer_name} (confidence: {confidence:.2f}, match: {match_type})")
            
            # Build dealer profile
            db_session = self._get_db_session()
            if db_session:
                try:
                    builder = DealerProfileBuilder(db_session)
                    profile = builder.build_profile(dealer_name)
                    db_session.close()
                    
                    if profile:
                        session.set_dealer(dealer_name, detection.dealer_code, profile)
                        
                        # Check for specific intent keywords
                        query_lower = message_clean.lower()
                        
                        if "revenue" in query_lower:
                            return self._render_response(self._handle_revenue(session, message_clean), session)
                        elif "units" in query_lower:
                            return self._render_response(self._handle_units(session, message_clean), session)
                        elif "warehouse" in query_lower:
                            return self._render_response(self._handle_warehouses(session, message_clean), session)
                        elif "cities" in query_lower or "city" in query_lower:
                            return self._render_response(self._handle_cities(session, message_clean), session)
                        elif "distance" in query_lower:
                            return self._render_response(self._handle_distance(session, message_clean), session)
                        elif "timeline" in query_lower:
                            return self._render_response(self._handle_timeline(session, message_clean), session)
                        elif "rank" in query_lower or "ranking" in query_lower:
                            return self._render_response(self._handle_ranking(session, message_clean), session)
                        elif "insight" in query_lower:
                            return self._render_response(self._handle_insights(session, message_clean), session)
                        elif "recommend" in query_lower:
                            return self._render_response(self._handle_recommendations(session, message_clean), session)
                        else:
                            # Default: Full Intelligence Report
                            return self._render_response(self._handle_intelligence(session, message_clean), session)
                except Exception as e:
                    logger.error(f"Profile building error: {e}")
                    if db_session:
                        db_session.close()
        
        # ============================================================
        # STEP 4: Menu option handlers
        # ============================================================
        if session.menu_state == DealerMenuState.DASHBOARD:
            dashboard_handlers = {
                "1": self._handle_intelligence,
                "2": self._handle_revenue,
                "3": self._handle_units,
                "4": self._handle_dn,
                "5": self._handle_delivery,
                "6": self._handle_distance,
                "7": self._handle_timeline,
            }
            if message_clean in dashboard_handlers:
                response = dashboard_handlers[message_clean](session, message_clean)
                return self._render_response(response, session)
        
        if session.menu_state == DealerMenuState.INTELLIGENCE:
            intelligence_handlers = {
                "1": self._handle_products,
                "2": self._handle_models,
                "3": self._handle_warehouses,
                "4": self._handle_cities,
                "5": self._handle_ranking,
                "6": self._handle_kpi,
                "7": self._handle_insights,
                "8": self._handle_recommendations,
            }
            if message_clean in intelligence_handlers:
                response = intelligence_handlers[message_clean](session, message_clean)
                return self._render_response(response, session)
        
        # ============================================================
        # STEP 5: Unknown - Show help
        # ============================================================
        return self._render_response(self._get_help(), session)
    
    # ============================================================
    # HANDLERS
    # ============================================================
    
    def _handle_main_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.MAIN
        return "Main Menu"
    
    def _handle_dashboard_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.DASHBOARD
        return self._renderer.render_dashboard_menu()
    
    def _handle_intelligence_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.INTELLIGENCE
        return self._renderer.render_intelligence_menu()
    
    def _handle_ai_assistant_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.AI_ASSISTANT
        return self._renderer.render_ai_assistant_menu()
    
    def _handle_intelligence(self, session: DealerSession, message: str) -> str:
        """Handle intelligence report"""
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        return self._renderer.render_intelligence_report(session.current_profile)
    
    def _handle_revenue(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"💰 *Revenue - {profile.dealer_name}*\n\nTotal Revenue: {_format_currency(profile.total_revenue)}\nAvg Revenue/DN: {_format_currency(profile.avg_revenue_per_dn)}\nMonthly Revenue: {_format_currency(profile.monthly_revenue)}\nGrowth: {profile.revenue_growth:+.1f}%\nRevenue Rank: #{profile.revenue_rank}"
    
    def _handle_units(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📦 *Units - {profile.dealer_name}*\n\nTotal Units: {_format_number(profile.total_units)}\nAvg Units/DN: {profile.avg_units_per_dn:.1f}\nUnit Rank: #{profile.unit_rank}"
    
    def _handle_dn(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📄 *DN - {profile.dealer_name}*\n\nTotal DN: {_format_number(profile.total_dn)}\nPending DN: {_format_number(profile.pending_dn)}\nDelivered: {_format_number(profile.delivered_dn)}\nDN Rank: #{profile.dn_rank}"
    
    def _handle_delivery(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"🚚 *Delivery - {profile.dealer_name}*\n\nDelivery: {profile.delivery_pct:.1f}%\nPGI: {profile.pgi_pct:.1f}%\nPOD: {profile.pod_pct:.1f}%\nAvg Delivery: {profile.avg_delivery_days:.1f} Days\nAvg POD: {profile.avg_pod_days:.1f} Days\nPending DN: {_format_number(profile.pending_dn)}"
    
    def _handle_distance(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📍 *Distance - {profile.dealer_name}*\n\nDistance: {profile.actual_distance_km:.1f} km\nTravel Time: {profile.travel_time_minutes} min\nTransport Zone: {profile.transport_zone}\nAvg Lead Distance: {profile.avg_lead_distance:.1f} km"
    
    def _handle_timeline(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📅 *Timeline - {profile.dealer_name}*\n\nFirst Order: {profile.first_order}\nLast Order: {profile.last_order}\nFirst DN: {profile.first_dn}\nLatest DN: {profile.latest_dn}\nLatest POD: {profile.latest_pod_date}\nLatest Activity: {profile.latest_activity}"
    
    def _handle_products(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📦 *Products - {profile.dealer_name}*\n\nTop Product: {profile.top_product}\nTop Model: {profile.top_model}\nProduct Count: {_format_number(profile.product_count)}\nModel Count: {_format_number(profile.model_count)}"
    
    def _handle_models(self, session: DealerSession, message: str) -> str:
        return self._handle_products(session, message)
    
    def _handle_warehouses(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"🏭 *Warehouses - {profile.dealer_name}*\n\nPrimary: {profile.primary_warehouse_name}\nTotal: {_format_number(profile.warehouse_count)}\nRevenue: {_format_currency(profile.warehouse_revenue)}\nUsed: {', '.join(profile.warehouses_used[:5]) if profile.warehouses_used else 'None'}"
    
    def _handle_cities(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"🏙️ *Cities - {profile.dealer_name}*\n\nPrimary: {profile.primary_city_name}\nTotal: {_format_number(profile.city_count)}\nRevenue: {_format_currency(profile.city_revenue)}\nServed: {', '.join(profile.cities_served[:5]) if profile.cities_served else 'None'}"
    
    def _handle_ranking(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"🏆 *Rankings - {profile.dealer_name}*\n\nRevenue Rank: #{profile.revenue_rank}\nUnits Rank: #{profile.unit_rank}\nDN Rank: #{profile.dn_rank}\nDelivery Rank: #{profile.delivery_rank}\nOverall Rank: #{profile.overall_rank}"
    
    def _handle_kpi(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        return f"📊 *KPI Scores - {profile.dealer_name}*\n\nBusiness Score: {profile.business_score:.1f}/100\nRisk Score: {profile.risk_score:.1f}/100\nRevenue Score: {profile.revenue_score:.1f}/100\nDelivery Score: {profile.delivery_score:.1f}/100\nGrowth Score: {profile.growth_score:.1f}/100"
    
    def _handle_insights(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        lines = [f"💡 *Insights - {profile.dealer_name}*", ""]
        for insight in profile.insights:
            lines.append(f"• {insight}")
        return "\n".join(lines)
    
    def _handle_recommendations(self, session: DealerSession, message: str) -> str:
        if not session.current_profile:
            return "⚠️ Please select a dealer first."
        
        profile = session.current_profile
        lines = [f"🎯 *Recommendations - {profile.dealer_name}*", ""]
        for rec in profile.recommendations:
            lines.append(f"• {rec}")
        return "\n".join(lines)
    
    def _handle_exit(self, session: DealerSession) -> str:
        session.clear()
        return "__EXIT__"
    
    def _get_help(self) -> str:
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 *Dealer Commands:*",
            "• Type dealer name for complete intelligence",
            "• [dealer] revenue - Show revenue",
            "• [dealer] distance - Show distance",
            "• [dealer] timeline - Show timeline",
            "• [dealer] ranking - Show rankings",
            "• [dealer] insights - Show insights",
            "• [dealer] recommendations - Show recommendations",
            "",
            "📌 *Menu Options:*",
            "• 1 - Dashboard Menu",
            "• 2 - Intelligence Menu",
            "• 3 - AI Assistant",
            "• 99 - Exit",
            "",
            "Reply with a command or menu number:"
        ])
    
    def health_check(self) -> Dict[str, Any]:
        with self._session_lock:
            active_sessions = len(self._sessions)
        
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "dealer_dictionary": len(self._dealer_dict.get_all_dealers()),
            "active_sessions": active_sessions,
            "exit_command": "99",
            "timestamp": datetime.now().isoformat()
        }


# ============================================================
# SERVICE SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()

def get_dealer_service() -> DealerAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DealerAnalyticsService()
    return _service


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "DealerSession",
    "DealerProfile",
    "DealerDetectionResult",
    "DealerDictionary",
    "DealerProfileBuilder",
    "get_dealer_service",
]
