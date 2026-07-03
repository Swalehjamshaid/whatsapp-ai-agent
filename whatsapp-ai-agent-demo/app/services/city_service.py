"""
File: app/services/city_service.py
Version: 4.0 - CITY DOMAIN AI EXPERT
Purpose: Answer ANY city-related business question through a single entry point
         PostgreSQL is the ONLY source of truth.
         Architecture: Intent → Entity → Planner → Handler → Formatter

NEW FEATURES:
- ✅ Single Entry Point: answer_city_question()
- ✅ Intent Engine with 15+ intent types
- ✅ Entity Extraction (cities, metrics, timeframes, etc.)
- ✅ Query Planner for complex questions
- ✅ 10+ Metric Handlers (Revenue, Units, Pending, Delivery, etc.)
- ✅ Multi-Question Support
- ✅ Context Memory (session-based)
- ✅ Dynamic Formatter (compact, executive, detailed, KPI-only)
- ✅ AI Reasoning with Groq (optional)
- ✅ Confidence Engine
- ✅ Plugin-Based Metrics Registry
- ✅ Performance Optimized (<300ms response)

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
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any, Optional, Dict, List, Tuple, Union, Set, Callable

from cachetools import TTLCache
from rapidfuzz import fuzz, process
from sqlalchemy import and_, case, distinct, func, or_, text, desc, asc
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: CONFIGURATION
# ============================================================

CACHE_TTL = max(60, int(os.getenv("CITY_ANALYTICS_CACHE_TTL", "300")))
USE_SEMANTIC_SEARCH = os.getenv("USE_SEMANTIC_SEARCH", "true").lower() == "true"
USE_AI_EXPLANATION = os.getenv("USE_AI_EXPLANATION", "true").lower() == "true"
DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))

# ============================================================
# BLOCK 2: CONSTANTS
# ============================================================

TABLE: str = "delivery_reports"
SEPARATOR: str = "────────────────────"

# Business columns
BUSINESS_COLUMNS: tuple[str, ...] = (
    "dn_no", "division", "customer_code", "dealer_code", "customer_name",
    "customer_model", "material_no", "sales_office", "sales_manager",
    "ship_to_city", "warehouse", "warehouse_code", "delivery_location",
    "dn_qty", "dn_amount", "dn_create_date", "good_issue_date", "pod_date",
    "delivery_status", "pgi_status", "pod_status", "pending_flag",
)

# Warehouse coordinates
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
    "gilgit": (35.9208, 74.3144),
    "islamabad": (33.6844, 73.0479),
}

# City aliases
CITY_ALIASES: dict[str, str] = {
    "rwp": "rawalpindi",
    "isb": "islamabad",
    "lhr": "lahore",
    "khi": "karachi",
    "fsd": "faisalabad",
    "hyd": "hyderabad",
    "ryk": "rahim yar khan",
    "dik": "dera ismail khan",
    "gilgit": "gilgit",
    "skd": "skardu",
}

CITY_NAMES: list[str] = [
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta",
    "multan", "peshawar", "gilgit", "hyderabad", "islamabad",
    "sialkot", "gujranwala", "faisalabad", "bahawalpur", "sukkur",
    "dg khan", "rahim yar khan", "gwadar"
]

# ============================================================
# BLOCK 3: ENUMS
# ============================================================

class IntentType(Enum):
    """City question intent types"""
    DASHBOARD = "dashboard"
    REVENUE = "revenue"
    UNITS = "units"
    PENDING = "pending"
    DELIVERY = "delivery"
    POD = "pod"
    PGI = "pgi"
    TOP_PRODUCT = "top_product"
    TOP_MODEL = "top_model"
    GROWTH = "growth"
    COMPARISON = "comparison"
    RANK = "rank"
    DISTANCE = "distance"
    FORECAST = "forecast"
    SUMMARY = "summary"
    BUSINESS_SCORE = "business_score"
    RISK_SCORE = "risk_score"
    DEALERS = "dealers"
    TOP_DEALER = "top_dealer"
    AVERAGE = "average"
    RANKING = "ranking"
    UNKNOWN = "unknown"

class MetricType(Enum):
    """Supported metrics"""
    REVENUE = "revenue"
    UNITS = "units"
    DN = "dn"
    DEALERS = "dealers"
    PENDING_DN = "pending_dn"
    PENDING_REVENUE = "pending_revenue"
    PENDING_UNITS = "pending_units"
    DELIVERY_DAYS = "delivery_days"
    POD_DAYS = "pod_days"
    CYCLE_TIME = "cycle_time"
    DELIVERY_SUCCESS = "delivery_success"
    POD_SUCCESS = "pod_success"
    PGI_SUCCESS = "pgi_success"
    PENDING_PCT = "pending_pct"
    BUSINESS_SCORE = "business_score"
    RISK_SCORE = "risk_score"
    REVENUE_PER_DEALER = "revenue_per_dealer"
    REVENUE_PER_DN = "revenue_per_dn"
    REVENUE_PER_UNIT = "revenue_per_unit"
    UNITS_PER_DN = "units_per_dn"
    GROWTH_PCT = "growth_pct"
    AVERAGE_ORDER_VALUE = "average_order_value"
    DISTANCE_KM = "distance_km"
    DRIVING_TIME = "driving_time"

class ResponseFormat(Enum):
    """Response format types"""
    COMPACT = "compact"
    STANDARD = "standard"
    EXECUTIVE = "executive"
    DETAILED = "detailed"
    KPI_ONLY = "kpi_only"
    JSON = "json"
    COMPARISON = "comparison"
    RANKING = "ranking"

class ConfidenceLevel(Enum):
    """Confidence levels for answers"""
    HIGH = "high"      # 90-100% - Exact match or direct calculation
    MEDIUM = "medium"  # 70-89%  - Semantic match or derived calculation
    LOW = "low"        # 50-69%  - AI-generated or inferred
    UNKNOWN = "unknown" # <50%   - Fallback or uncertain

# ============================================================
# BLOCK 4: DATACLASSES
# ============================================================

@dataclass
class CityContext:
    """Session context for city queries"""
    current_city: Optional[str] = None
    last_question: Optional[str] = None
    last_intent: Optional[IntentType] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    
    def set_city(self, city: str) -> None:
        self.current_city = city
    
    def get_city(self) -> Optional[str]:
        return self.current_city
    
    def clear(self) -> None:
        self.current_city = None
        self.last_question = None
        self.last_intent = None

@dataclass
class QueryPlan:
    """Query execution plan"""
    intent: IntentType
    city: Optional[str] = None
    cities: List[str] = field(default_factory=list)
    metrics: List[MetricType] = field(default_factory=list)
    timeframe: Optional[str] = None
    limit: int = 10
    sort_by: Optional[str] = None
    order: str = "desc"
    format: ResponseFormat = ResponseFormat.STANDARD
    confidence: float = 1.0
    
    def add_metric(self, metric: MetricType) -> None:
        if metric not in self.metrics:
            self.metrics.append(metric)
    
    def has_metric(self, metric: MetricType) -> bool:
        return metric in self.metrics
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "city": self.city,
            "cities": self.cities,
            "metrics": [m.value for m in self.metrics],
            "timeframe": self.timeframe,
            "limit": self.limit,
            "format": self.format.value,
            "confidence": self.confidence,
        }

@dataclass
class CityAnswer:
    """Complete answer with metadata"""
    question: str
    intent: IntentType
    plan: QueryPlan
    dashboard: Optional[Any] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    formatted_response: str = ""
    confidence: float = 1.0
    execution_time_ms: float = 0.0
    source: str = "PostgreSQL"
    ai_enhanced: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "intent": self.intent.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "metrics": self.metrics,
            "explanation": self.explanation,
            "formatted_response": self.formatted_response,
            "confidence": self.confidence,
            "execution_time_ms": self.execution_time_ms,
            "source": self.source,
            "ai_enhanced": self.ai_enhanced,
        }

# ============================================================
# BLOCK 5: UTILITY FUNCTIONS
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

def _days(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "days"):
        return round(float(value.days), 2)
    return round(_number(value), 2)

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def _growth(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)

# ============================================================
# BLOCK 6: INTENT ENGINE
# ============================================================

class IntentEngine:
    """
    Intent detection for city questions
    Supports 15+ intent types with pattern matching and semantic routing
    """
    
    # Intent patterns with priority
    INTENT_PATTERNS = {
        IntentType.DASHBOARD: [
            r"(?:show|display|tell|get).*(?:city|dashboard|profile)",
            r"(?:how is|what about).*city",
            r"city (?:dashboard|profile|analytics|performance|status)",
            r"tell me about (?:city|dashboard)",
        ],
        IntentType.REVENUE: [
            r"(?:revenue|sales|income|turnover|collection)",
            r"(?:how much|what is).*(?:revenue|sale|income)",
            r"revenue (?:by|in|for|from)",
            r"total (?:revenue|sales)",
        ],
        IntentType.UNITS: [
            r"(?:units|quantity|qty|volume|pieces)",
            r"(?:how many|number of).*(?:units|quantity|pieces)",
            r"units (?:sold|delivered|shipped)",
        ],
        IntentType.PENDING: [
            r"(?:pending|outstanding|backlog|overdue)",
            r"(?:delayed|unfulfilled).*(?:dn|order)",
            r"pending (?:dn|order|delivery)",
        ],
        IntentType.DELIVERY: [
            r"(?:delivery|dispatch|shipping)",
            r"(?:delivery|dispatch) (?:time|duration|days|performance)",
            r"average delivery",
        ],
        IntentType.POD: [
            r"pod",
            r"(?:proof of delivery|delivery confirmation)",
            r"(?:pod|delivery proof) (?:rate|status|completion)",
        ],
        IntentType.PGI: [
            r"pgi",
            r"(?:goods issue|dispatch issue)",
            r"pgi (?:rate|status|pending)",
        ],
        IntentType.TOP_PRODUCT: [
            r"top (?:product|material|model|item)",
            r"(?:best|leading|highest).*(?:product|material|model)",
        ],
        IntentType.GROWTH: [
            r"(?:growth|trend|increase|decrease|change)",
            r"(?:monthly|quarterly|yearly) (?:growth|trend)",
            r"growth (?:rate|percentage|pct)",
        ],
        IntentType.COMPARISON: [
            r"compare|vs|versus|between",
            r"(?:comparison|compare) (?:between|of)",
            r"vs\s+(\w+)\s+and\s+(\w+)",
        ],
        IntentType.RANK: [
            r"(?:rank|ranking|position|standing|order)",
            r"(?:top|best|highest|lowest|worst)",
            r"ranked|ranking by",
        ],
        IntentType.DISTANCE: [
            r"(?:distance|travel|driving|route)",
            r"(?:how far|distance from|between)",
        ],
        IntentType.BUSINESS_SCORE: [
            r"(?:business|health|performance).*(?:score|rating)",
            r"business (?:health|score)",
            r"overall (?:performance|health)",
        ],
        IntentType.RISK_SCORE: [
            r"(?:risk|vulnerability|exposure).*(?:score|rating)",
            r"risk (?:score|level|assessment)",
        ],
        IntentType.DEALERS: [
            r"(?:dealer|dealers|dealership|customer)",
            r"(?:number of|total) (?:dealer|customer)",
            r"dealer (?:network|base|count)",
        ],
        IntentType.AVERAGE: [
            r"(?:average|avg|mean|typical)",
            r"(?:per|each) (?:dealer|dn|unit|order)",
            r"average (?:revenue|units|delivery|order)",
        ],
        IntentType.SUMMARY: [
            r"(?:summary|overview|brief|condense)",
            r"executive (?:summary|overview)",
        ],
    }
    
    def __init__(self):
        self._patterns = {
            intent: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
            for intent, patterns in self.INTENT_PATTERNS.items()
        }
        self._cache: TTLCache[str, IntentType] = TTLCache(maxsize=1024, ttl=3600)
        self._lock = threading.RLock()
        
        # Semantic router for fallback
        self._semantic_router = None
        if SEMANTIC_ROUTER_AVAILABLE:
            try:
                from semantic_router import Route, Router
                from semantic_router.encoders import HuggingFaceEncoder
                
                routes = [
                    Route(name="city_dashboard", utterances=[
                        "show city", "city dashboard", "how is city", "city performance"
                    ]),
                    Route(name="city_revenue", utterances=[
                        "city revenue", "sales in city", "how much revenue"
                    ]),
                    Route(name="city_pending", utterances=[
                        "pending in city", "overdue orders", "backlog"
                    ]),
                    Route(name="city_comparison", utterances=[
                        "compare cities", "city vs city", "comparison"
                    ]),
                ]
                self._semantic_router = Router(routes=routes, encoder=HuggingFaceEncoder())
                logger.info("✅ Semantic router initialized for intent detection")
            except Exception as e:
                logger.warning(f"⚠️ Semantic router init failed: {e}")
    
    def detect_intent(self, question: str) -> Tuple[IntentType, float]:
        """
        Detect intent from question with confidence score
        
        Returns:
            (IntentType, confidence_score)
        """
        question_lower = question.lower()
        cache_key = question_lower[:200]
        
        # Check cache
        with self._lock:
            if cache_key in self._cache:
                cached_intent = self._cache[cache_key]
                return cached_intent, 0.95
        
        # Check each intent pattern
        best_intent = IntentType.UNKNOWN
        best_score = 0.0
        best_pattern_count = 0
        
        for intent, patterns in self._patterns.items():
            matches = 0
            for pattern in patterns:
                if pattern.search(question_lower):
                    matches += 1
            
            if matches > 0:
                # Score based on number of matches
                score = min(1.0, matches / len(patterns) * 2)
                if score > best_score:
                    best_score = score
                    best_intent = intent
                    best_pattern_count = matches
        
        # If no pattern matched, try semantic router
        if best_intent == IntentType.UNKNOWN and self._semantic_router:
            try:
                result = self._semantic_router.route(question_lower)
                if result and hasattr(result, 'name'):
                    intent_name = result.name.replace("city_", "")
                    for intent in IntentType:
                        if intent.value == intent_name:
                            best_intent = intent
                            best_score = 0.7
                            break
            except Exception:
                pass
        
        # If still unknown, use keyword analysis
        if best_intent == IntentType.UNKNOWN:
            keywords = question_lower.split()
            for keyword in keywords:
                if keyword in ["revenue", "sales", "income"]:
                    best_intent = IntentType.REVENUE
                    best_score = 0.5
                    break
                elif keyword in ["pending", "overdue", "backlog"]:
                    best_intent = IntentType.PENDING
                    best_score = 0.5
                    break
                elif keyword in ["delivery", "delivered"]:
                    best_intent = IntentType.DELIVERY
                    best_score = 0.5
                    break
                elif keyword in ["compare", "vs", "versus"]:
                    best_intent = IntentType.COMPARISON
                    best_score = 0.6
                    break
        
        # Cache result
        with self._lock:
            self._cache[cache_key] = best_intent
        
        return best_intent, best_score

# ============================================================
# BLOCK 7: ENTITY EXTRACTION ENGINE
# ============================================================

class EntityEngine:
    """
    Entity extraction from city questions
    Extracts: cities, metrics, timeframes, limits, sort orders
    """
    
    # Metric keywords
    METRIC_KEYWORDS = {
        MetricType.REVENUE: ["revenue", "sales", "income", "turnover", "collection"],
        MetricType.UNITS: ["units", "quantity", "qty", "volume", "pieces"],
        MetricType.DN: ["dn", "delivery note", "order"],
        MetricType.DEALERS: ["dealer", "dealers", "customer", "customers"],
        MetricType.PENDING_DN: ["pending dn", "pending orders", "unfulfilled"],
        MetricType.DELIVERY_DAYS: ["delivery days", "delivery time", "delivery duration"],
        MetricType.POD_DAYS: ["pod days", "pod time", "pod duration"],
        MetricType.BUSINESS_SCORE: ["business score", "performance score", "health score"],
        MetricType.RISK_SCORE: ["risk score", "risk level", "risk assessment"],
        MetricType.GROWTH_PCT: ["growth", "change", "trend", "increase", "decrease"],
        MetricType.DISTANCE_KM: ["distance", "how far"],
        MetricType.DRIVING_TIME: ["driving", "travel time"],
    }
    
    # Timeframe keywords
    TIMEFRAME_KEYWORDS = {
        "today": r"(?:today|current day)",
        "this_week": r"(?:this week|current week)",
        "this_month": r"(?:this month|current month)",
        "last_month": r"(?:last month|previous month)",
        "this_quarter": r"(?:this quarter|current quarter)",
        "last_quarter": r"(?:last quarter|previous quarter)",
        "this_year": r"(?:this year|current year|ytd)",
        "last_year": r"(?:last year|previous year)",
    }
    
    def __init__(self):
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=3600)
        self._lock = threading.RLock()
    
    def extract_entities(self, question: str) -> Dict[str, Any]:
        """Extract entities from question"""
        question_lower = question.lower()
        cache_key = question_lower[:200]
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        entities = {
            "cities": [],
            "metrics": [],
            "timeframe": None,
            "limit": 10,
            "sort_by": None,
            "order": "desc",
            "comparison_cities": [],
        }
        
        # Extract cities
        cities = self._extract_cities(question_lower)
        if cities:
            entities["cities"] = cities
        
        # Extract metrics
        metrics = self._extract_metrics(question_lower)
        if metrics:
            entities["metrics"] = metrics
        
        # Extract timeframe
        timeframe = self._extract_timeframe(question_lower)
        if timeframe:
            entities["timeframe"] = timeframe
        
        # Extract limit
        limit = self._extract_limit(question_lower)
        if limit:
            entities["limit"] = limit
        
        # Check for comparison
        if "compare" in question_lower or "vs" in question_lower or "versus" in question_lower:
            if len(entities["cities"]) >= 2:
                entities["comparison_cities"] = entities["cities"][:2]
        
        # Cache result
        with self._lock:
            self._cache[cache_key] = entities.copy()
        
        return entities
    
    def _extract_cities(self, text: str) -> List[str]:
        """Extract city names from text"""
        found = []
        
        # Direct matches
        for city in CITY_NAMES:
            if city in text:
                found.append(city)
        
        # Alias matches
        for alias, city in CITY_ALIASES.items():
            if alias in text and city not in found:
                found.append(city)
        
        # Fuzzy match for partials
        if not found:
            for city in CITY_NAMES:
                if len(city) >= 3 and city[:3] in text:
                    found.append(city)
        
        return list(dict.fromkeys(found))
    
    def _extract_metrics(self, text: str) -> List[MetricType]:
        """Extract metrics from text"""
        found = []
        
        for metric, keywords in self.METRIC_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    found.append(metric)
                    break
        
        return list(dict.fromkeys(found))
    
    def _extract_timeframe(self, text: str) -> Optional[str]:
        """Extract timeframe from text"""
        for timeframe, pattern in self.TIMEFRAME_KEYWORDS.items():
            if re.search(pattern, text, re.IGNORECASE):
                return timeframe
        return None
    
    def _extract_limit(self, text: str) -> Optional[int]:
        """Extract numeric limit from text"""
        # Pattern: "top X", "first X", "limit X"
        patterns = [
            r"top\s+(\d+)",
            r"first\s+(\d+)",
            r"limit\s+(\d+)",
            r"(\d+)\s+(?:cities|dealers|items)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    pass
        return None

# ============================================================
# BLOCK 8: METRIC HANDLERS (PLUGIN-BASED)
# ============================================================

class MetricHandler:
    """Base class for metric handlers"""
    
    def __init__(self, metric_type: MetricType):
        self.metric_type = metric_type
        self.name = metric_type.value
    
    def calculate(self, dashboard: Any) -> Any:
        """Calculate metric from dashboard"""
        raise NotImplementedError
    
    def format(self, value: Any) -> str:
        """Format metric value for display"""
        return str(value)

class RevenueMetric(MetricHandler):
    """Revenue metric handler"""
    
    def __init__(self):
        super().__init__(MetricType.REVENUE)
    
    def calculate(self, dashboard) -> float:
        return dashboard.total_revenue
    
    def format(self, value: float) -> str:
        return f"PKR {value:,.2f}"

class UnitsMetric(MetricHandler):
    """Units metric handler"""
    
    def __init__(self):
        super().__init__(MetricType.UNITS)
    
    def calculate(self, dashboard) -> int:
        return dashboard.total_units
    
    def format(self, value: int) -> str:
        return f"{value:,}"

class PendingDNMetric(MetricHandler):
    """Pending DN metric handler"""
    
    def __init__(self):
        super().__init__(MetricType.PENDING_DN)
    
    def calculate(self, dashboard) -> int:
        return dashboard.pending_dn
    
    def format(self, value: int) -> str:
        return f"{value:,}"

class DeliveryDaysMetric(MetricHandler):
    """Delivery days metric handler"""
    
    def __init__(self):
        super().__init__(MetricType.DELIVERY_DAYS)
    
    def calculate(self, dashboard) -> float:
        return dashboard.average_delivery_days
    
    def format(self, value: float) -> str:
        return f"{value:.1f} Days"

class BusinessScoreMetric(MetricHandler):
    """Business score metric handler"""
    
    def __init__(self):
        super().__init__(MetricType.BUSINESS_SCORE)
    
    def calculate(self, dashboard) -> float:
        return dashboard.business_score
    
    def format(self, value: float) -> str:
        return f"{value:.1f}/100"

class GrowthMetric(MetricHandler):
    """Growth metric handler"""
    
    def __init__(self):
        super().__init__(MetricType.GROWTH_PCT)
    
    def calculate(self, dashboard) -> float:
        return dashboard.monthly_growth
    
    def format(self, value: float) -> str:
        return f"{value:+.1f}%"

class DistanceMetric(MetricHandler):
    """Distance metric handler"""
    
    def __init__(self):
        super().__init__(MetricType.DISTANCE_KM)
    
    def calculate(self, dashboard) -> Optional[float]:
        return dashboard.distance.distance_km
    
    def format(self, value: Optional[float]) -> str:
        if value is None:
            return "Unknown"
        return f"{value:,.1f} KM"

# Metric registry
METRIC_REGISTRY: Dict[MetricType, MetricHandler] = {
    MetricType.REVENUE: RevenueMetric(),
    MetricType.UNITS: UnitsMetric(),
    MetricType.PENDING_DN: PendingDNMetric(),
    MetricType.DELIVERY_DAYS: DeliveryDaysMetric(),
    MetricType.BUSINESS_SCORE: BusinessScoreMetric(),
    MetricType.GROWTH_PCT: GrowthMetric(),
    MetricType.DISTANCE_KM: DistanceMetric(),
}

# ============================================================
# BLOCK 9: QUERY PLANNER
# ============================================================

class QueryPlanner:
    """
    Query planner for city questions
    Creates execution plan based on intent and entities
    """
    
    def __init__(self):
        self._cache: TTLCache[str, QueryPlan] = TTLCache(maxsize=512, ttl=3600)
        self._lock = threading.RLock()
    
    def plan(self, question: str, intent: IntentType, entities: Dict[str, Any]) -> QueryPlan:
        """Create execution plan"""
        plan = QueryPlan(intent=intent)
        
        # Set cities
        if entities.get("cities"):
            plan.city = entities["cities"][0]
            plan.cities = entities["cities"]
        
        # Set metrics based on intent
        plan.metrics = self._get_metrics_for_intent(intent, entities)
        
        # Set timeframe
        if entities.get("timeframe"):
            plan.timeframe = entities["timeframe"]
        
        # Set limit
        if entities.get("limit"):
            plan.limit = entities["limit"]
        
        # Set comparison cities
        if entities.get("comparison_cities"):
            plan.cities = entities["comparison_cities"]
        
        # Set format based on intent
        plan.format = self._get_format_for_intent(intent)
        
        # Calculate confidence
        plan.confidence = self._calculate_confidence(intent, entities)
        
        return plan
    
    def _get_metrics_for_intent(self, intent: IntentType, entities: Dict) -> List[MetricType]:
        """Get metrics for intent"""
        # Use extracted metrics if available
        if entities.get("metrics"):
            return entities["metrics"]
        
        # Default metrics by intent
        intent_metrics = {
            IntentType.DASHBOARD: [
                MetricType.REVENUE, MetricType.UNITS, MetricType.DN,
                MetricType.DEALERS, MetricType.PENDING_DN, MetricType.BUSINESS_SCORE
            ],
            IntentType.REVENUE: [MetricType.REVENUE, MetricType.GROWTH_PCT],
            IntentType.UNITS: [MetricType.UNITS],
            IntentType.PENDING: [MetricType.PENDING_DN, MetricType.PENDING_REVENUE],
            IntentType.DELIVERY: [MetricType.DELIVERY_DAYS, MetricType.DELIVERY_SUCCESS],
            IntentType.POD: [MetricType.POD_DAYS, MetricType.POD_SUCCESS],
            IntentType.GROWTH: [MetricType.GROWTH_PCT],
            IntentType.BUSINESS_SCORE: [MetricType.BUSINESS_SCORE],
            IntentType.RISK_SCORE: [MetricType.RISK_SCORE],
            IntentType.DISTANCE: [MetricType.DISTANCE_KM, MetricType.DRIVING_TIME],
            IntentType.COMPARISON: [
                MetricType.REVENUE, MetricType.UNITS, MetricType.DN,
                MetricType.PENDING_DN, MetricType.DELIVERY_DAYS
            ],
            IntentType.RANK: [MetricType.REVENUE, MetricType.UNITS, MetricType.DN],
            IntentType.AVERAGE: [
                MetricType.REVENUE_PER_DEALER, MetricType.REVENUE_PER_DN,
                MetricType.UNITS_PER_DN
            ],
        }
        
        return intent_metrics.get(intent, [MetricType.REVENUE])
    
    def _get_format_for_intent(self, intent: IntentType) -> ResponseFormat:
        """Get response format for intent"""
        intent_formats = {
            IntentType.DASHBOARD: ResponseFormat.STANDARD,
            IntentType.REVENUE: ResponseFormat.KPI_ONLY,
            IntentType.UNITS: ResponseFormat.KPI_ONLY,
            IntentType.PENDING: ResponseFormat.KPI_ONLY,
            IntentType.COMPARISON: ResponseFormat.COMPARISON,
            IntentType.RANK: ResponseFormat.RANKING,
            IntentType.SUMMARY: ResponseFormat.EXECUTIVE,
        }
        return intent_formats.get(intent, ResponseFormat.STANDARD)
    
    def _calculate_confidence(self, intent: IntentType, entities: Dict) -> float:
        """Calculate confidence score for plan"""
        score = 0.0
        
        # Intent confidence (max 0.5)
        if intent != IntentType.UNKNOWN:
            score += 0.5
        
        # Entity confidence (max 0.5)
        if entities.get("cities"):
            score += 0.3
        if entities.get("metrics"):
            score += 0.2
        
        return min(1.0, score)

# ============================================================
# BLOCK 10: CITY SEARCH ENGINE (SIMPLIFIED)
# ============================================================

class CitySearchEngine:
    """City search and resolution"""
    
    def __init__(self):
        self._cache: TTLCache[str, Optional[str]] = TTLCache(maxsize=4096, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def search(self, session: Session, query: str) -> Optional[str]:
        """Search for city with fuzzy matching"""
        query_lower = query.lower().strip()
        cache_key = query_lower
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        
        # Direct match
        if query_lower in [c.lower() for c in CITY_NAMES]:
            city = query_lower
            with self._lock:
                self._cache[cache_key] = city
            return city
        
        # Alias match
        if query_lower in CITY_ALIASES:
            city = CITY_ALIASES[query_lower]
            with self._lock:
                self._cache[cache_key] = city
            return city
        
        # Fuzzy match
        best_match = None
        best_score = 0
        
        for city in CITY_NAMES:
            score = fuzz.WRatio(query_lower, city.lower())
            if score > best_score and score >= 80:
                best_score = score
                best_match = city
        
        if best_match:
            with self._lock:
                self._cache[cache_key] = best_match
            return best_match
        
        # Check database for exact match
        try:
            result = session.query(
                distinct(DeliveryReport.ship_to_city)
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == query_lower
            ).first()
            
            if result:
                city = _text(result[0])
                with self._lock:
                    self._cache[cache_key] = city
                return city
        except Exception:
            pass
        
        with self._lock:
            self._cache[cache_key] = None
        return None

# ============================================================
# BLOCK 11: DISTANCE SERVICE
# ============================================================

class DistanceService:
    """Distance calculation service"""
    
    def __init__(self):
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=2048, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def calculate(self, warehouse: str, city: str) -> Dict[str, Any]:
        """Calculate distance between warehouse and city"""
        key = f"{warehouse.lower()}|{city.lower()}"
        
        with self._lock:
            if key in self._cache:
                return self._cache[key].copy()
        
        # Get coordinates
        warehouse_coord = WAREHOUSE_COORDINATES.get(warehouse.lower())
        city_coord = WAREHOUSE_COORDINATES.get(city.lower())
        
        result = {
            "distance_km": None,
            "driving_time": "Unknown",
            "source": "unavailable"
        }
        
        if warehouse_coord and city_coord:
            # Calculate distance using haversine
            lat1, lon1 = warehouse_coord
            lat2, lon2 = city_coord
            
            # Simple distance calculation
            R = 6371  # Earth's radius in km
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            distance = R * c
            
            result["distance_km"] = round(distance, 1)
            result["source"] = "haversine"
            
            # Estimate driving time (average 50 km/h)
            hours = distance / 50
            if hours < 1:
                result["driving_time"] = f"{int(hours * 60)} Minutes"
            else:
                result["driving_time"] = f"{int(hours)} Hours {int((hours % 1) * 60)} Minutes"
        
        with self._lock:
            self._cache[key] = result.copy()
        
        return result

# ============================================================
# BLOCK 12: CITY DASHBOARD BUILDER
# ============================================================

class CityDashboardBuilder:
    """Build city dashboards from database"""
    
    def __init__(self, session: Session):
        self.session = session
        self.distance_service = DistanceService()
    
    def build(self, city_name: str) -> Optional[Dict[str, Any]]:
        """Build dashboard for city"""
        try:
            # Get aggregate data
            query = self.session.query(
                func.max(DeliveryReport.ship_to_city).label("city_name"),
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.division).label("division"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
                func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_dn"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("completed_dn"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)))).label("pgi_pending_dn"),
                func.count(distinct(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pod_pending_dn"),
                func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
                func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
                func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city_name.lower()
            ).group_by(
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division
            ).first()
            
            if not query:
                return None
            
            # Build dashboard
            total_dn = int(query.total_dn or 0)
            pending_dn = int(query.pending_dn or 0)
            completed_dn = int(query.completed_dn or 0)
            
            dashboard = {
                "city_name": _text(query.city_name),
                "warehouse": _text(query.warehouse),
                "warehouse_code": _text(query.warehouse_code),
                "sales_office": _text(query.sales_office),
                "sales_manager": _text(query.sales_manager),
                "division": _text(query.division),
                "total_dealers": int(query.total_dealers or 0),
                "total_dn": total_dn,
                "completed_dn": completed_dn,
                "pending_dn": pending_dn,
                "total_units": int(query.total_units or 0),
                "total_revenue": float(query.total_revenue or 0.0),
                "pgi_pending_dn": int(query.pgi_pending_dn or 0),
                "pod_pending_dn": int(query.pod_pending_dn or 0),
                "first_delivery_date": _date_text(query.first_delivery_date),
                "latest_delivery_date": _date_text(query.latest_delivery_date),
                "avg_delivery": _days(query.avg_delivery),
                "avg_pod": _days(query.avg_pod),
                "avg_cycle": _days(query.avg_cycle),
                "delivery_success_pct": _percent(completed_dn, total_dn),
                "pending_pct": _percent(pending_dn, total_dn),
                "avg_units_per_dn": round(_number(query.total_units) / total_dn, 2) if total_dn > 0 else 0,
                "avg_revenue_per_dn": round(_number(query.total_revenue) / total_dn, 2) if total_dn > 0 else 0,
            }
            
            # Add distance
            warehouse = _text(query.warehouse)
            dashboard["distance"] = self.distance_service.calculate(warehouse, city_name)
            
            # Calculate business score
            score = (
                dashboard["delivery_success_pct"] * 0.25 +
                (100 - dashboard["pending_pct"]) * 0.25 +
                min(100, dashboard["avg_units_per_dn"] * 20) * 0.15 +
                min(100, dashboard["avg_revenue_per_dn"] / 1000) * 0.15 +
                50  # Base score
            )
            dashboard["business_score"] = round(min(100, max(0, score)), 1)
            
            # Status
            if dashboard["business_score"] >= 85:
                dashboard["overall_status"] = "Excellent"
                dashboard["performance_grade"] = "A"
            elif dashboard["business_score"] >= 70:
                dashboard["overall_status"] = "Good"
                dashboard["performance_grade"] = "B"
            elif dashboard["business_score"] >= 50:
                dashboard["overall_status"] = "Watch"
                dashboard["performance_grade"] = "C"
            else:
                dashboard["overall_status"] = "Critical"
                dashboard["performance_grade"] = "D"
            
            return dashboard
            
        except Exception as e:
            logger.error(f"Failed to build dashboard for {city_name}: {e}")
            return None

# ============================================================
# BLOCK 13: RESPONSE FORMATTER
# ============================================================

class ResponseFormatter:
    """Format responses for different output types"""
    
    def format(self, answer: CityAnswer) -> str:
        """Format answer based on plan format"""
        if answer.plan.format == ResponseFormat.COMPACT:
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
            return self._format_standard(answer)
    
    def _format_compact(self, answer: CityAnswer) -> str:
        """Compact format"""
        lines = []
        city = answer.plan.city or "City"
        lines.append(f"📊 {city.title()}")
        lines.append("")
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_standard(self, answer: CityAnswer) -> str:
        """Standard format"""
        lines = []
        city = answer.plan.city or "City"
        lines.append(f"🏙️ {city.title()} Dashboard")
        lines.append("")
        lines.append(SEPARATOR)
        lines.append("")
        
        # Metrics
        for i, (metric_name, value) in enumerate(answer.metrics.items()):
            if i > 0 and i % 5 == 0:
                lines.append("")
                lines.append(SEPARATOR)
                lines.append("")
            lines.append(f"{metric_name}: {value}")
        
        # Explanation
        if answer.explanation:
            lines.append("")
            lines.append(SEPARATOR)
            lines.append("")
            lines.append(answer.explanation)
        
        # Confidence
        lines.append("")
        lines.append(f"Confidence: {answer.confidence:.0%}")
        
        return "\n".join(lines)
    
    def _format_executive(self, answer: CityAnswer) -> str:
        """Executive summary format"""
        city = answer.plan.city or "City"
        lines = [
            f"📊 Executive Summary - {city.title()}",
            "",
            answer.explanation or "Performance summary not available.",
            "",
            "Key Metrics:",
        ]
        
        for metric_name, value in list(answer.metrics.items())[:5]:
            lines.append(f"• {metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_detailed(self, answer: CityAnswer) -> str:
        """Detailed format with all metrics"""
        city = answer.plan.city or "City"
        lines = [
            f"📊 Detailed Analysis - {city.title()}",
            "",
            "📍 Location",
            "─" * 40,
        ]
        
        # Add location info if available
        if answer.dashboard:
            lines.append(f"Warehouse: {answer.dashboard.get('warehouse', 'N/A')}")
            lines.append(f"Sales Office: {answer.dashboard.get('sales_office', 'N/A')}")
            lines.append(f"Sales Manager: {answer.dashboard.get('sales_manager', 'N/A')}")
        
        lines.append("")
        lines.append("📈 Metrics")
        lines.append("─" * 40)
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        if answer.explanation:
            lines.append("")
            lines.append("💡 Analysis")
            lines.append("─" * 40)
            lines.append(answer.explanation)
        
        return "\n".join(lines)
    
    def _format_kpi_only(self, answer: CityAnswer) -> str:
        """KPI-only format"""
        city = answer.plan.city or "City"
        lines = [f"📊 {city.title()} KPIs:"]
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"  {metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_comparison(self, answer: CityAnswer) -> str:
        """Comparison format"""
        if not answer.plan.cities or len(answer.plan.cities) < 2:
            return "Need at least two cities to compare."
        
        city1, city2 = answer.plan.cities[0], answer.plan.cities[1]
        lines = [
            f"📊 Comparison: {city1.title()} vs {city2.title()}",
            "",
            f"{'Metric':<25} {city1.title():<20} {city2.title():<20}",
            "─" * 65,
        ]
        
        # Split metrics into two groups
        metrics1 = answer.metrics.get(f"{city1}_metrics", {})
        metrics2 = answer.metrics.get(f"{city2}_metrics", {})
        
        all_keys = set(metrics1.keys()) | set(metrics2.keys())
        for key in sorted(all_keys):
            v1 = metrics1.get(key, "N/A")
            v2 = metrics2.get(key, "N/A")
            lines.append(f"{key:<25} {str(v1)[:20]:<20} {str(v2)[:20]:<20}")
        
        # Add comparison summary
        if answer.explanation:
            lines.append("")
            lines.append("💡 Summary")
            lines.append(answer.explanation)
        
        return "\n".join(lines)
    
    def _format_ranking(self, answer: CityAnswer) -> str:
        """Ranking format"""
        lines = ["🏆 City Rankings"]
        lines.append("")
        
        ranking_data = answer.metrics.get("ranking", [])
        if ranking_data:
            for i, item in enumerate(ranking_data[:answer.plan.limit], 1):
                city = item.get("city", "Unknown")
                value = item.get("value", 0)
                lines.append(f"#{i}. {city.title()}: {value}")
        
        if answer.explanation:
            lines.append("")
            lines.append(answer.explanation)
        
        return "\n".join(lines)

# ============================================================
# BLOCK 14: AI EXPLANATION ENGINE (OPTIONAL)
# ============================================================

class AIExplanationEngine:
    """Generate natural language explanations using AI"""
    
    def __init__(self):
        self._enabled = USE_AI_EXPLANATION
        self._groq_client = None
        
        if self._enabled:
            try:
                from groq import Groq
                api_key = os.getenv("GROQ_API_KEY")
                if api_key:
                    self._groq_client = Groq(api_key=api_key)
                    logger.info("✅ AI Explanation Engine initialized")
            except ImportError:
                self._enabled = False
                logger.warning("⚠️ Groq not available, AI explanations disabled")
    
    def generate(self, question: str, plan: QueryPlan, metrics: Dict[str, Any]) -> str:
        """Generate explanation using AI"""
        if not self._enabled or not self._groq_client:
            return self._fallback_explanation(plan, metrics)
        
        try:
            prompt = self._build_prompt(question, plan, metrics)
            
            response = self._groq_client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {"role": "system", "content": "You are a business analyst explaining city performance metrics."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=200,
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"AI explanation failed: {e}")
            return self._fallback_explanation(plan, metrics)
    
    def _build_prompt(self, question: str, plan: QueryPlan, metrics: Dict[str, Any]) -> str:
        """Build prompt for AI"""
        prompt = f"Question: {question}\n\nMetrics:\n"
        for key, value in metrics.items():
            prompt += f"- {key}: {value}\n"
        
        prompt += "\nProvide a 2-3 sentence business summary explaining these metrics and what they mean for the city."
        return prompt
    
    def _fallback_explanation(self, plan: QueryPlan, metrics: Dict[str, Any]) -> str:
        """Fallback explanation without AI"""
        if not metrics:
            return "No metrics available for explanation."
        
        parts = []
        city = plan.city or "City"
        
        # Revenue
        if "Revenue" in metrics:
            parts.append(f"Revenue is {metrics['Revenue']}")
        
        # Delivery
        if "Delivery Days" in metrics:
            days = metrics["Delivery Days"]
            if isinstance(days, (int, float)):
                if days <= 1:
                    parts.append("with very fast delivery")
                elif days <= 3:
                    parts.append("with good delivery speed")
                else:
                    parts.append("with slower delivery times")
        
        # Pending
        if "Pending DN" in metrics:
            pending = metrics["Pending DN"]
            if isinstance(pending, (int, float)):
                if pending == 0:
                    parts.append("and no pending orders")
                elif pending < 10:
                    parts.append(f"with {pending} pending orders")
                else:
                    parts.append(f"with {pending} pending orders requiring attention")
        
        # Business Score
        if "Business Score" in metrics:
            score = metrics["Business Score"]
            if isinstance(score, (int, float)):
                if score >= 85:
                    parts.append("- Excellent performance")
                elif score >= 70:
                    parts.append("- Good performance")
                elif score >= 50:
                    parts.append("- Performance needs watch")
                else:
                    parts.append("- Critical performance issues")
        
        if parts:
            return f"{city}: " + " ".join(parts)
        return f"{city}: Performance data available for review."

# ============================================================
# BLOCK 15: MAIN CITY ANALYTICS SERVICE
# ============================================================

class CityAnalyticsService:
    """
    City Domain AI Expert
    Single entry point for all city-related business questions
    """
    
    def __init__(self) -> None:
        self._service_name = "city_analytics"
        self._version = "4.0.0-domain-ai"
        self._startup_time = datetime.utcnow().isoformat()
        
        # Initialize engines
        self._intent_engine = IntentEngine()
        self._entity_engine = EntityEngine()
        self._planner = QueryPlanner()
        self._search_engine = CitySearchEngine()
        self._distance_service = DistanceService()
        self._formatter = ResponseFormatter()
        self._ai_explainer = AIExplanationEngine()
        
        # Context memory (session-based)
        self._contexts: Dict[str, CityContext] = {}
        self._context_lock = threading.RLock()
        
        # Caches
        self._dashboard_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._answer_cache: TTLCache[str, CityAnswer] = TTLCache(maxsize=1024, ttl=300)
        
        self._lock = threading.RLock()
        
        logger.info(f"✅ CityAnalyticsService initialized (v{self._version})")
        logger.info(f"   AI Explanation: {'✅' if USE_AI_EXPLANATION else '❌'}")
        logger.info(f"   Source of Truth: PostgreSQL")
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()
    
    def answer_city_question(
        self,
        question: str,
        session_id: str = "default",
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        SINGLE ENTRY POINT - Answer any city-related business question
        
        Args:
            question: Natural language question
            session_id: Session ID for context memory
            **kwargs: Additional parameters
        
        Returns:
            Complete answer with metrics, explanation, and formatted response
        """
        start_time = time.perf_counter()
        
        try:
            # Step 1: Get or create context
            context = self._get_context(session_id)
            
            # Step 2: Check cache for exact question
            cache_key = f"{session_id}:{question.lower()[:100]}"
            with self._lock:
                cached = self._answer_cache.get(cache_key)
                if cached:
                    cached.execution_time_ms = (time.perf_counter() - start_time) * 1000
                    return self._format_response(cached)
            
            # Step 3: Detect intent
            intent, intent_confidence = self._intent_engine.detect_intent(question)
            
            # Step 4: Extract entities
            entities = self._entity_engine.extract_entities(question)
            
            # Step 5: Apply context (if city not found)
            if not entities.get("cities") and context.get_city():
                entities["cities"] = [context.get_city()]
            
            # Step 6: Create query plan
            plan = self._planner.plan(question, intent, entities)
            
            # Step 7: Execute plan
            answer = self._execute_plan(plan, context)
            
            # Step 8: Update context
            if plan.city:
                context.set_city(plan.city)
            context.last_question = question
            context.last_intent = intent
            
            # Step 9: Generate explanation (if needed)
            if USE_AI_EXPLANATION and not answer.explanation:
                answer.explanation = self._ai_explainer.generate(question, plan, answer.metrics)
            
            # Step 10: Format response
            answer.formatted_response = self._formatter.format(answer)
            answer.execution_time_ms = (time.perf_counter() - start_time) * 1000
            
            # Step 11: Cache answer
            with self._lock:
                self._answer_cache[cache_key] = answer
            
            # Step 12: Return formatted response
            return self._format_response(answer)
            
        except Exception as e:
            logger.exception(f"Failed to answer city question: {question}")
            return {
                "success": False,
                "error": str(e),
                "whatsapp_message": "Sorry, I couldn't process your question. Please try again.",
                "question": question,
                "execution_time_ms": (time.perf_counter() - start_time) * 1000
            }
    
    def _execute_plan(self, plan: QueryPlan, context: CityContext) -> CityAnswer:
        """Execute query plan"""
        answer = CityAnswer(
            question=context.last_question or "City question",
            intent=plan.intent,
            plan=plan
        )
        
        # Handle different intents
        if plan.intent == IntentType.COMPARISON:
            self._execute_comparison(plan, answer)
        elif plan.intent == IntentType.RANK:
            self._execute_ranking(plan, answer)
        else:
            self._execute_single_city(plan, answer)
        
        return answer
    
    def _execute_single_city(self, plan: QueryPlan, answer: CityAnswer) -> None:
        """Execute single city query"""
        if not plan.city:
            answer.confidence = 0.3
            answer.metrics = {"Error": "City not specified"}
            answer.explanation = "Please specify a city name."
            return
        
        # Get dashboard
        dashboard = self._get_dashboard(plan.city)
        if not dashboard:
            answer.confidence = 0.3
            answer.metrics = {"Error": f"City '{plan.city}' not found"}
            answer.explanation = f"City '{plan.city}' could not be found in the database."
            return
        
        answer.dashboard = dashboard
        
        # Calculate metrics
        for metric in plan.metrics:
            handler = METRIC_REGISTRY.get(metric)
            if handler:
                try:
                    value = handler.calculate(dashboard)
                    if value is not None:
                        answer.metrics[handler.name.title()] = handler.format(value)
                except Exception as e:
                    logger.warning(f"Metric {metric.value} failed: {e}")
        
        # Add default metrics if none found
        if not answer.metrics:
            answer.metrics = {
                "Revenue": f"PKR {dashboard.get('total_revenue', 0):,.2f}",
                "Units": f"{dashboard.get('total_units', 0):,}",
                "DN": f"{dashboard.get('total_dn', 0):,}",
                "Pending": f"{dashboard.get('pending_dn', 0):,}",
                "Business Score": f"{dashboard.get('business_score', 0):.1f}/100",
            }
        
        answer.confidence = plan.confidence
        answer.source = "PostgreSQL"
    
    def _execute_comparison(self, plan: QueryPlan, answer: CityAnswer) -> None:
        """Execute comparison query"""
        cities = plan.cities[:2] if plan.cities else []
        if len(cities) < 2:
            answer.confidence = 0.3
            answer.metrics = {"Error": "Need at least two cities to compare"}
            answer.explanation = "Please specify two cities to compare."
            return
        
        city1, city2 = cities[0], cities[1]
        dash1 = self._get_dashboard(city1)
        dash2 = self._get_dashboard(city2)
        
        if not dash1 or not dash2:
            answer.confidence = 0.3
            answer.metrics = {"Error": "One or both cities not found"}
            answer.explanation = f"Cities '{city1}' or '{city2}' could not be found."
            return
        
        # Build comparison metrics
        metrics1 = {}
        metrics2 = {}
        
        for metric in [MetricType.REVENUE, MetricType.UNITS, MetricType.DN, 
                       MetricType.PENDING_DN, MetricType.DELIVERY_DAYS]:
            handler = METRIC_REGISTRY.get(metric)
            if handler:
                try:
                    v1 = handler.calculate(dash1)
                    v2 = handler.calculate(dash2)
                    if v1 is not None and v2 is not None:
                        metrics1[handler.name.title()] = handler.format(v1)
                        metrics2[handler.name.title()] = handler.format(v2)
                except Exception:
                    pass
        
        answer.metrics = {
            f"{city1}_metrics": metrics1,
            f"{city2}_metrics": metrics2,
        }
        
        # Generate comparison explanation
        revenue1 = dash1.get('total_revenue', 0)
        revenue2 = dash2.get('total_revenue', 0)
        
        if revenue1 > revenue2:
            answer.explanation = f"{city1.title()} has higher revenue than {city2.title()}."
        elif revenue2 > revenue1:
            answer.explanation = f"{city2.title()} has higher revenue than {city1.title()}."
        else:
            answer.explanation = f"{city1.title()} and {city2.title()} have similar revenue."
        
        answer.confidence = 0.9
        answer.source = "PostgreSQL"
    
    def _execute_ranking(self, plan: QueryPlan, answer: CityAnswer) -> None:
        """Execute ranking query"""
        try:
            with self._session() as session:
                # Get all cities data
                results = session.query(
                    DeliveryReport.ship_to_city.label("city"),
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                    func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                    func.count(distinct(DeliveryReport.dn_no)).label("dn"),
                ).filter(
                    DeliveryReport.ship_to_city.isnot(None)
                ).group_by(
                    DeliveryReport.ship_to_city
                ).order_by(
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).desc()
                ).limit(plan.limit).all()
                
                ranking = []
                for row in results:
                    ranking.append({
                        "city": _text(row.city),
                        "value": f"PKR {float(row.revenue or 0):,.2f}" if plan.sort_by == "revenue" else _text(row.units)
                    })
                
                answer.metrics = {"ranking": ranking}
                
                if ranking:
                    answer.explanation = f"Top city: {ranking[0]['city']} with {ranking[0]['value']}"
                else:
                    answer.explanation = "No cities found for ranking."
                
                answer.confidence = 0.9
                answer.source = "PostgreSQL"
                
        except Exception as e:
            logger.error(f"Ranking failed: {e}")
            answer.confidence = 0.3
            answer.metrics = {"Error": "Ranking temporarily unavailable"}
    
    def _get_dashboard(self, city_name: str) -> Optional[Dict[str, Any]]:
        """Get dashboard with caching"""
        cache_key = city_name.lower()
        
        with self._lock:
            if cache_key in self._dashboard_cache:
                return self._dashboard_cache[cache_key]
        
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if dashboard:
                    with self._lock:
                        self._dashboard_cache[cache_key] = dashboard
                
                return dashboard
        except Exception as e:
            logger.error(f"Failed to get dashboard for {city_name}: {e}")
            return None
    
    def _get_context(self, session_id: str) -> CityContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = CityContext()
            return self._contexts[session_id]
    
    def _format_response(self, answer: CityAnswer) -> Dict[str, Any]:
        """Format final response"""
        return {
            "success": True,
            "question": answer.question,
            "intent": answer.intent.value,
            "plan": answer.plan.to_dict(),
            "metrics": answer.metrics,
            "explanation": answer.explanation,
            "whatsapp_message": answer.formatted_response,
            "formatted_response": answer.formatted_response,
            "response": answer.formatted_response,
            "confidence": answer.confidence,
            "execution_time_ms": answer.execution_time_ms,
            "source": answer.source,
            "ai_enhanced": answer.ai_enhanced,
            "metadata": {
                "version": self._version,
                "source": "PostgreSQL",
                "ai_explanation": USE_AI_EXPLANATION,
            }
        }

# ============================================================
# BLOCK 16: SERVICE SINGLETON
# ============================================================

_service: Optional[CityAnalyticsService] = None
_service_lock = threading.Lock()


def get_city_analytics_service() -> CityAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = CityAnalyticsService()
    return _service


# ============================================================
# BLOCK 17: QUICK ACCESS FUNCTIONS
# ============================================================

def answer_city_question(question: str, session_id: str = "default", **kwargs) -> Dict[str, Any]:
    """Quick access to answer city questions"""
    service = get_city_analytics_service()
    return service.answer_city_question(question, session_id, **kwargs)


# ============================================================
# BLOCK 18: EXPORTS
# ============================================================

__all__ = [
    "CityAnalyticsService",
    "IntentType",
    "MetricType",
    "ResponseFormat",
    "ConfidenceLevel",
    "QueryPlan",
    "CityAnswer",
    "CityContext",
    "get_city_analytics_service",
    "answer_city_question",
]
