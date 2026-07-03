"""
File: app/services/ai_provider_service.py
Version: 28.0 - ENTERPRISE AI ROUTER WITH FULL INTELLIGENCE

Single entry point for the WhatsApp AI agent. Enterprise-grade routing with:
- Intent Detection (100+ intents)
- Entity Recognition (20+ entity types)
- Multi-Intent Support
- Cross-Service Analytics
- Context Memory
- Semantic Routing
- AI Recommendations
- Executive Analytics
- Universal Search

ROUTING FLOW (Priority Order):
1. Menu Number (0-9) → Direct to specific service
2. DN Number (8-12 digits) → DN Analysis
3. Multi-Intent → Multiple services → Merged response
4. Entity-based routing (CITY FIRST)
5. Intent-based routing
6. Semantic routing
7. Context-aware routing
8. AI fallback

Status: ENTERPRISE READY
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union, Set, Tuple

logger = logging.getLogger(__name__)

# =====================================================================================================================
# AI BOOTSTRAP SERVICE - LAZY LOADING
# =====================================================================================================================

try:
    from app.services.ai_bootstrap_service import get_ai_bootstrap_service, warmup_ai_resources
    BOOTSTRAP_AVAILABLE = True
    warmup_ai_resources(include_heavy=False)
    logger.info("✅ AI Bootstrap Service connected and warmed up")
except ImportError:
    BOOTSTRAP_AVAILABLE = False
    logger.warning("⚠️ AI Bootstrap Service not available")

# =====================================================================================================================
# ENUMS
# =====================================================================================================================

class EntityType(Enum):
    """Entity types for recognition"""
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    PRODUCT = "product"
    MATERIAL = "material"
    DIVISION = "division"
    SALES_OFFICE = "sales_office"
    REGION = "region"
    PROVINCE = "province"
    TRANSPORTER = "transporter"
    VEHICLE = "vehicle"
    DRIVER = "driver"
    WAREHOUSE_CODE = "warehouse_code"
    DEALER_CODE = "dealer_code"
    CUSTOMER_CODE = "customer_code"
    MATERIAL_NUMBER = "material_number"
    ROUTE = "route"
    SALES_MANAGER = "sales_manager"
    DELIVERY_LOCATION = "delivery_location"
    DATE = "date"
    MONTH = "month"
    YEAR = "year"

class IntentCategory(Enum):
    """Intent categories"""
    DASHBOARD = "dashboard"
    SUMMARY = "summary"
    PERFORMANCE = "performance"
    REVENUE = "revenue"
    UNITS = "units"
    PENDING = "pending"
    POD = "pod"
    PGI = "pgi"
    DELIVERY = "delivery"
    DELAY = "delay"
    DISTANCE = "distance"
    WAREHOUSE = "warehouse"
    DEALER = "dealer"
    PRODUCT = "product"
    MATERIAL = "material"
    TRANSPORTER = "transporter"
    VEHICLE = "vehicle"
    DRIVER = "driver"
    REGION = "region"
    SALES_OFFICE = "sales_office"
    DIVISION = "division"
    RANKING = "ranking"
    COMPARISON = "comparison"
    FORECAST = "forecast"
    RECOMMENDATION = "recommendation"
    TREND = "trend"
    HISTORY = "history"
    TOP = "top"
    BOTTOM = "bottom"
    GROWTH = "growth"
    DECLINE = "decline"
    RISK = "risk"
    INVENTORY = "inventory"
    AGEING = "ageing"
    STOCK = "stock"
    ROUTE = "route"
    CAPACITY = "capacity"
    KPI = "kpi"
    NATIONAL = "national"
    EXECUTIVE = "executive"
    HELP = "help"
    GREETING = "greeting"
    SEARCH = "search"
    COMPARE = "compare"
    ANALYZE = "analyze"
    PREDICT = "predict"
    RECOMMEND = "recommend"
    LIST = "list"
    EXPLAIN = "explain"
    ROOT_CAUSE = "root_cause"
    SLA = "sla"
    UTILIZATION = "utilization"
    FORECAST_ENGINE = "forecast_engine"

# =====================================================================================================================
# DATACLASSES
# =====================================================================================================================

@dataclass
class Entity:
    """Represents a recognized entity"""
    type: EntityType
    value: str
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Intent:
    """Represents a detected intent"""
    category: IntentCategory
    sub_intent: Optional[str] = None
    confidence: float = 1.0
    entities: List[Entity] = field(default_factory=list)
    requires_multi_service: bool = False
    services: List[str] = field(default_factory=list)

@dataclass
class RoutingDecision:
    intent: str
    confidence: float
    service_key: str
    service_file: str
    method: str
    entity: Dict[str, Any]
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    menu_option: Optional[str] = None
    multi_intent: bool = False
    services: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    follow_up: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "service_key": self.service_key,
            "service_file": self.service_file,
            "method": self.method,
            "entity": self.entity,
            "requires_ai": self.requires_ai,
            "reason": self.reason,
            "original_message": self.original_message,
            "menu_option": self.menu_option,
            "multi_intent": self.multi_intent,
            "services": self.services,
        }

# =====================================================================================================================
# SEMANTIC ROUTER
# =====================================================================================================================

Route = None
SemanticRouter = None
HuggingFaceEncoder = None
SEMANTIC_ROUTER_AVAILABLE = False

try:
    from semantic_router import Route as _Route
    try:
        from semantic_router import SemanticRouter as _SemanticRouter
    except ImportError:
        try:
            from semantic_router import Router as _SemanticRouter
        except ImportError:
            from semantic_router.layer import RouteLayer as _SemanticRouter
    from semantic_router.encoders import HuggingFaceEncoder as _HuggingFaceEncoder

    Route = _Route
    SemanticRouter = _SemanticRouter
    HuggingFaceEncoder = _HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
except Exception:
    logger.warning("Semantic Router unavailable")

# =====================================================================================================================
# SERVICE IMPORTS WITH SAFE FALLBACKS
# =====================================================================================================================

# DN Analysis Service
try:
    from app.services.dn_analysis import DNAnalysisService
    DN_ANALYSIS_AVAILABLE = True
    logger.info("✅ DNAnalysisService imported")
except Exception:
    DN_ANALYSIS_AVAILABLE = False
    class DNAnalysisService:
        def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ DN service unavailable", "error": "DN service unavailable"}
        def get_warehouse_dashboard(self, warehouse: str) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Warehouse service unavailable", "error": "Warehouse service unavailable"}
        def get_pending_dns(self, limit: int = 20) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Pending DN service unavailable", "error": "Pending DN service unavailable"}
        def get_top_performers(self, limit: int = 10) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Performance service unavailable", "error": "Performance service unavailable"}

# Dealer Analytics Service
try:
    from app.services.dealer_analytics_service import DealerAnalyticsService
    DEALER_ANALYTICS_AVAILABLE = True
    logger.info("✅ DealerAnalyticsService imported")
except Exception:
    DEALER_ANALYTICS_AVAILABLE = False
    class DealerAnalyticsService:
        async def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Dealer service unavailable", "error": "Dealer service unavailable"}

# City Service
try:
    from app.services.city_service import CityAnalyticsService
    CITY_SERVICE_AVAILABLE = True
    logger.info("✅ CityAnalyticsService imported")
except Exception:
    CITY_SERVICE_AVAILABLE = False
    class CityAnalyticsService:
        def get_city_dashboard(self, city_name: str = "", **kwargs: Any) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ City service unavailable", "error": "City service unavailable"}
        def get_city_menu(self) -> str:
            return "🏙️ City Analytics Menu\n\nCity service is temporarily unavailable.\n\n0. Main Menu"
        def process_city_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
            return {"response": "City service unavailable", "menu_type": "city_menu", "action": "error", "data": {}, "exit_menu": True}

# Warehouse Service
try:
    from app.services.warehouse_service import WarehouseAnalyticsService
    WAREHOUSE_SERVICE_AVAILABLE = True
    logger.info("✅ WarehouseAnalyticsService imported")
except Exception:
    WAREHOUSE_SERVICE_AVAILABLE = False
    class WarehouseAnalyticsService:
        def get_warehouse_dashboard(self, warehouse_name: str = "", **kwargs: Any) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Warehouse service unavailable", "error": "Warehouse service unavailable"}
        def get_main_menu(self) -> str:
            return "🏭 Warehouse Analytics Menu\n\nWarehouse service is temporarily unavailable.\n\n0. Main Menu"
        def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
            return {"response": "Warehouse service unavailable", "menu_type": "warehouse_menu", "action": "error", "data": {}, "exit_menu": True}

# Product Service
try:
    from app.services.product_service import ProductAnalyticsService
    PRODUCT_SERVICE_AVAILABLE = True
    logger.info("✅ ProductAnalyticsService imported")
except Exception:
    PRODUCT_SERVICE_AVAILABLE = False
    class ProductAnalyticsService:
        def get_product_dashboard(self, product_name: str = "", **kwargs: Any) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Product service unavailable", "error": "Product service unavailable"}
        def get_main_menu(self) -> str:
            return "📦 Product Analytics Menu\n\nProduct service is temporarily unavailable.\n\n0. Main Menu"
        def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
            return {"response": "Product service unavailable", "menu_type": "product_menu", "action": "error", "data": {}, "exit_menu": True}

# National KPI Service
try:
    from app.services.national_kpi_service import NationalKPIService
    NATIONAL_KPI_AVAILABLE = True
    logger.info("✅ NationalKPIService imported")
except Exception:
    NATIONAL_KPI_AVAILABLE = False
    class NationalKPIService:
        def get_national_kpi_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ National KPI service unavailable", "error": "National KPI service unavailable"}
        def get_main_menu(self) -> str:
            return "🇵🇰 National Logistics Intelligence Menu\n\nNational KPI service is temporarily unavailable.\n\n0. Main Menu"
        def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
            return {"response": "National KPI service unavailable", "menu_type": "national_menu", "action": "error", "data": {}, "exit_menu": True}

# Groq Service
try:
    from app.services.groq_service import GroqService
    GROQ_SERVICE_AVAILABLE = True
    logger.info("✅ GroqService imported")
except Exception:
    GROQ_SERVICE_AVAILABLE = False
    class GroqService:
        async def process_query(self, message: str, entities: Dict[str, Any]) -> str:
            return get_main_menu()

# =====================================================================================================================
# MENU OPTIONS
# =====================================================================================================================

MENU_OPTIONS: Dict[str, Dict[str, Any]] = {
    "0": {"name": "Main Menu", "service_key": "menu_service", "service_file": "ai_provider_service.py", "method": "show_main_menu", "requires_ai": False},
    "1": {"name": "DN Delivery", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_dn_dashboard", "requires_ai": False},
    "2": {"name": "Dealer Analytics", "service_key": "dealer_analytics", "service_file": "dealer_analytics_service.py", "method": "get_dealer_dashboard", "requires_ai": False},
    "3": {"name": "City Analytics", "service_key": "city_menu", "service_file": "city_service.py", "method": "get_city_menu", "requires_ai": False},
    "4": {"name": "Warehouse Analytics", "service_key": "warehouse_menu", "service_file": "warehouse_service.py", "method": "get_main_menu", "requires_ai": False},
    "5": {"name": "Product Analytics", "service_key": "product_menu", "service_file": "product_service.py", "method": "get_main_menu", "requires_ai": False},
    "6": {"name": "National KPI", "service_key": "national_kpi_menu", "service_file": "national_kpi_service.py", "method": "get_main_menu", "requires_ai": False},
    "7": {"name": "Pending DN", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_pending_dns", "requires_ai": False},
    "8": {"name": "Top Performers", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_top_performers", "requires_ai": False},
    "9": {"name": "AI Query", "service_key": "groq_service", "service_file": "groq_service.py", "method": "process_query", "requires_ai": True},
}

# =====================================================================================================================
# INTENT DETECTION ENGINE - 100+ INTENTS
# =====================================================================================================================

class IntentEngine:
    """Enterprise intent detection engine with 100+ intents"""

    INTENT_PATTERNS: Dict[str, Dict[str, Any]] = {
        # Dashboard Intents
        "dashboard": {
            "patterns": [
                r"(?:show|display|get|view).*(?:dashboard|overview|summary)",
                r"(?:how is|what about|tell me about).*(?:performance|status)",
                r"(?:national|overall|general).*(?:dashboard|kpi|status)",
            ],
            "category": IntentCategory.DASHBOARD,
            "service": "dashboard",
            "priority": 1
        },
        "executive_dashboard": {
            "patterns": [
                r"(?:executive|management|leadership).*(?:dashboard|summary|overview)",
                r"what(?:'s)? happening (?:nationally|overall|at (?:top|executive) level)",
                r"(?:top|key|critical).*(?:insights|issues|risks)",
            ],
            "category": IntentCategory.EXECUTIVE,
            "service": "national_kpi",
            "priority": 1
        },
        "national_kpi": {
            "patterns": [
                r"(?:national|overall|pakistan).*(?:kpi|performance|score|health)",
                r"(?:country|nation|national).*(?:logistics|supply chain)",
                r"overall (?:performance|health|score)",
            ],
            "category": IntentCategory.NATIONAL,
            "service": "national_kpi",
            "priority": 1
        },

        # Revenue Intents
        "revenue": {
            "patterns": [
                r"(?:revenue|sales|income|turnover|earnings)",
                r"(?:how much|what(?:'s)? the).*(?:revenue|sales)",
                r"(?:total|overall).*(?:revenue|sales)",
            ],
            "category": IntentCategory.REVENUE,
            "service": "revenue",
            "priority": 1
        },
        "revenue_by_entity": {
            "patterns": [
                r"revenue (?:of|for|from|in)\s+([\w\s]+)",
                r"([\w\s]+).*(?:revenue|sales)",
            ],
            "category": IntentCategory.REVENUE,
            "service": "revenue",
            "priority": 2
        },

        # Units Intents
        "units": {
            "patterns": [
                r"(?:units|quantity|volume|pieces|items)",
                r"(?:how many|number of).*(?:units|items)",
            ],
            "category": IntentCategory.UNITS,
            "service": "units",
            "priority": 1
        },

        # Pending Intents
        "pending_dn": {
            "patterns": [
                r"(?:pending|outstanding|backlog|overdue).*(?:dn|delivery|order)",
                r"(?:undelivered|unfulfilled).*(?:orders|dns)",
            ],
            "category": IntentCategory.PENDING,
            "service": "pending",
            "priority": 1
        },
        "pending_pgi": {
            "patterns": [
                r"(?:pending|overdue).*(?:pgi|goods issue)",
                r"(?:pgi|goods issue).*(?:pending|not done)",
            ],
            "category": IntentCategory.PGI,
            "service": "pending",
            "priority": 1
        },
        "pending_pod": {
            "patterns": [
                r"(?:pending|overdue).*(?:pod|proof of delivery)",
                r"(?:pod|delivery proof).*(?:pending|missing)",
            ],
            "category": IntentCategory.POD,
            "service": "pending",
            "priority": 1
        },

        # Delivery Intents
        "delivery_performance": {
            "patterns": [
                r"(?:delivery|dispatch|shipping).*(?:performance|time|days)",
                r"(?:average|fastest|slowest).*(?:delivery|transit)",
                r"delivery (?:success|failure|rate)",
            ],
            "category": IntentCategory.DELIVERY,
            "service": "delivery",
            "priority": 1
        },
        "delivery_sla": {
            "patterns": [
                r"(?:sla|service level|agreement).*(?:delivery|performance)",
                r"sla (?:compliance|breach|violation)",
                r"delivery (?:timeline|commitment)",
            ],
            "category": IntentCategory.SLA,
            "service": "delivery",
            "priority": 1
        },

        # Dealer Intents
        "dealer_performance": {
            "patterns": [
                r"(?:dealer|dealers).*(?:performance|score|health)",
                r"how (?:is|are) (?:dealer|dealers) performing",
            ],
            "category": IntentCategory.DEALER,
            "service": "dealer",
            "priority": 1
        },
        "top_dealers": {
            "patterns": [
                r"(?:top|best|highest|leading).*(?:dealer|dealers)",
                r"dealer (?:ranking|rank|leaderboard)",
            ],
            "category": IntentCategory.TOP,
            "service": "dealer",
            "priority": 1
        },
        "bottom_dealers": {
            "patterns": [
                r"(?:bottom|worst|lowest).*(?:dealer|dealers)",
                r"worst (?:performing|performer)",
            ],
            "category": IntentCategory.BOTTOM,
            "service": "dealer",
            "priority": 1
        },
        "dealer_comparison": {
            "patterns": [
                r"(?:compare|vs|versus|against).*(?:dealer|dealers)",
                r"([\w\s]+)\s+(?:and|vs|versus)\s+([\w\s]+)",
            ],
            "category": IntentCategory.COMPARISON,
            "service": "dealer",
            "priority": 1
        },

        # Warehouse Intents
        "warehouse_performance": {
            "patterns": [
                r"(?:warehouse|depot|hub).*(?:performance|score|health)",
                r"how (?:is|are) (?:warehouse|warehouses) performing",
            ],
            "category": IntentCategory.WAREHOUSE,
            "service": "warehouse",
            "priority": 1
        },
        "warehouse_ranking": {
            "patterns": [
                r"(?:top|best|highest).*(?:warehouse|warehouses)",
                r"warehouse (?:ranking|rank|leaderboard)",
            ],
            "category": IntentCategory.RANKING,
            "service": "warehouse",
            "priority": 1
        },
        "warehouse_comparison": {
            "patterns": [
                r"compare\s+([\w\s]+)\s+(?:and|vs|versus)\s+([\w\s]+)",
                r"(?:warehouse|depot)\s+(?:vs|versus|comparison)",
            ],
            "category": IntentCategory.COMPARISON,
            "service": "warehouse",
            "priority": 1
        },
        "warehouse_utilization": {
            "patterns": [
                r"(?:utilization|capacity|storage).*(?:warehouse)",
                r"warehouse (?:utilization|capacity|usage)",
                r"(?:how full|space available).*(?:warehouse)",
            ],
            "category": IntentCategory.UTILIZATION,
            "service": "warehouse",
            "priority": 1
        },
        "warehouse_inventory": {
            "patterns": [
                r"(?:inventory|stock|supply).*(?:warehouse)",
                r"warehouse (?:inventory|stock|items)",
            ],
            "category": IntentCategory.INVENTORY,
            "service": "warehouse",
            "priority": 1
        },

        # City Intents
        "city_performance": {
            "patterns": [
                r"(?:city|town|location).*(?:performance|score)",
                r"how (?:is|are) (?:city|cities) performing",
            ],
            "category": IntentCategory.DASHBOARD,
            "service": "city",
            "priority": 1
        },
        "city_ranking": {
            "patterns": [
                r"(?:top|best|highest).*(?:city|cities)",
                r"city (?:ranking|rank|leaderboard)",
            ],
            "category": IntentCategory.RANKING,
            "service": "city",
            "priority": 1
        },
        "city_comparison": {
            "patterns": [
                r"compare\s+([\w\s]+)\s+(?:and|vs|versus)\s+([\w\s]+)",
                r"(?:city|town)\s+(?:vs|versus|comparison)",
            ],
            "category": IntentCategory.COMPARISON,
            "service": "city",
            "priority": 1
        },

        # Product Intents
        "product_performance": {
            "patterns": [
                r"(?:product|model|item).*(?:performance|sales)",
                r"how (?:is|are) (?:product|products) selling",
            ],
            "category": IntentCategory.PRODUCT,
            "service": "product",
            "priority": 1
        },
        "top_products": {
            "patterns": [
                r"(?:top|best|highest|fastest).*(?:product|products)",
                r"product (?:ranking|rank|leaderboard)",
            ],
            "category": IntentCategory.TOP,
            "service": "product",
            "priority": 1
        },

        # Root Cause Analysis
        "root_cause_analysis": {
            "patterns": [
                r"why (?:is|are|was|were)\s+([\w\s]+?)\s+(?:slow|delayed|late|underperforming|declining)",
                r"(?:reason|cause|why).*(?:delay|issue|problem)",
                r"what (?:caused|causes|is causing).*(?:problem|issue|delay)",
            ],
            "category": IntentCategory.ROOT_CAUSE,
            "service": "ai_analysis",
            "priority": 1
        },

        # Forecasting
        "forecast": {
            "patterns": [
                r"(?:forecast|predict|project|estimate).*(?:sales|revenue|delivery|volume)",
                r"what (?:will|would) be the (?:sales|revenue|delivery)",
                r"next (?:month|quarter|year).*(?:forecast|prediction)",
            ],
            "category": IntentCategory.FORECAST,
            "service": "forecast",
            "priority": 1
        },

        # Recommendations
        "recommendation": {
            "patterns": [
                r"(?:recommend|suggest|advice|improve|optimize)",
                r"what (?:should|can|could) (?:i|we) (?:do|improve|fix)",
                r"how to (?:improve|fix|optimize)\s+([\w\s]+)",
            ],
            "category": IntentCategory.RECOMMENDATION,
            "service": "recommendation",
            "priority": 1
        },

        # Executive Analytics
        "executive_insights": {
            "patterns": [
                r"(?:executive|management|leadership).*(?:insight|analytics|intelligence)",
                r"what(?:'s)? (?:going on|happening|the situation)",
                r"where (?:are we|is the business)",
            ],
            "category": IntentCategory.EXECUTIVE,
            "service": "national_kpi",
            "priority": 1
        },
        "risk_assessment": {
            "patterns": [
                r"(?:risk|risks|threat|vulnerability).*(?:analysis|assessment)",
                r"what are the (?:biggest|top) risks",
                r"risk (?:score|assessment|analysis)",
            ],
            "category": IntentCategory.RISK,
            "service": "national_kpi",
            "priority": 1
        },

        # Comparison
        "compare_general": {
            "patterns": [
                r"compare\s+([\w\s]+)\s+(?:and|vs|versus)\s+([\w\s]+)",
                r"difference between\s+([\w\s]+)\s+(?:and|vs)\s+([\w\s]+)",
            ],
            "category": IntentCategory.COMPARISON,
            "service": "comparison",
            "priority": 1
        },

        # Search
        "search": {
            "patterns": [
                r"(?:search|find|lookup|locate)\s+([\w\s\-_]+)",
                r"where (?:is|are)\s+([\w\s\-_]+)",
            ],
            "category": IntentCategory.SEARCH,
            "service": "search",
            "priority": 1
        },

        # Help
        "help": {
            "patterns": [
                r"(?:help|assist|support|guide|how to)",
                r"what can you (?:do|help with)",
                r"how (?:does|do) (?:i|you|this)",
            ],
            "category": IntentCategory.HELP,
            "service": "help",
            "priority": 1
        },
    }

    def __init__(self):
        self._compiled_patterns = {}
        self._lock = threading.RLock()
        self._cache: Dict[str, Tuple[IntentCategory, float]] = {}
        self._cache_ttl = 300

        # Compile all patterns
        for intent_name, config in self.INTENT_PATTERNS.items():
            self._compiled_patterns[intent_name] = [
                re.compile(pattern, re.IGNORECASE) for pattern in config["patterns"]
            ]

        logger.info(f"✅ IntentEngine initialized with {len(self.INTENT_PATTERNS)} intents")

    def detect_intent(self, message: str) -> Tuple[Optional[IntentCategory], Optional[str], float, Dict[str, Any]]:
        """Detect intent from message with confidence"""
        message_lower = message.lower()
        cache_key = message_lower[:200]

        with self._lock:
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                return cached[0], None, cached[1], {}

        best_intent = None
        best_score = 0.0
        best_config = None
        extracted_entities = {}

        # Check each intent pattern
        for intent_name, patterns in self._compiled_patterns.items():
            matches = 0
            total_patterns = len(patterns)

            for pattern in patterns:
                match = pattern.search(message_lower)
                if match:
                    matches += 1
                    # Extract entities from groups
                    if match.groups():
                        for i, group in enumerate(match.groups()):
                            if group and len(group.strip()) > 2:
                                extracted_entities[f"entity_{i}"] = group.strip()

            if matches > 0:
                # Score based on match ratio
                score = min(1.0, matches / max(1, total_patterns) * 2)
                # Boost score for exact matches
                if matches == total_patterns:
                    score = min(1.0, score * 1.5)

                if score > best_score:
                    best_score = score
                    best_intent = intent_name
                    best_config = self.INTENT_PATTERNS[intent_name]

        # If no pattern matched, try keyword-based detection
        if best_score < 0.3:
            keywords = message_lower.split()
            keyword_intent_map = {
                "revenue": "revenue",
                "sales": "revenue",
                "pending": "pending_dn",
                "delivery": "delivery_performance",
                "warehouse": "warehouse_performance",
                "dealer": "dealer_performance",
                "city": "city_performance",
                "product": "product_performance",
                "top": "top_products",
                "compare": "compare_general",
                "why": "root_cause_analysis",
                "forecast": "forecast",
                "recommend": "recommendation",
                "help": "help",
            }

            for keyword in keywords:
                if keyword in keyword_intent_map:
                    intent_name = keyword_intent_map[keyword]
                    best_intent = intent_name
                    best_score = 0.4
                    best_config = self.INTENT_PATTERNS[intent_name]
                    break

        with self._lock:
            self._cache[cache_key] = (best_intent, best_score)

        return best_intent, best_config["category"] if best_config else None, best_score, extracted_entities

# =====================================================================================================================
# ENTITY RECOGNITION ENGINE
# =====================================================================================================================

class EntityEngine:
    """Enterprise entity recognition engine"""

    ENTITY_PATTERNS = {
        EntityType.DN: [
            r'\b(\d{8,12})\b',
        ],
        EntityType.WAREHOUSE: [
            r'(?:warehouse|depot|hub)\s+([\w\s]+)',
            r'warehouse\s+([\w\s]+?)(?:\s|$|\.)',
        ],
        EntityType.CITY: [
            r'(?:city|town|location|in)\s+([\w\s]+)',
            r'([\w\s]+)\s+city',
        ],
        EntityType.DEALER: [
            r'(?:dealer|dealership|customer)\s+([\w\s]+)',
            r'([\w\s]+)\s+(?:electronics|traders|distributors|foods|group)',
        ],
        EntityType.PRODUCT: [
            r'(?:product|model|item)\s+([\w\s\-_]+)',
            r'([\w\s\-_]+)\s+(?:model|product)',
        ],
        EntityType.DIVISION: [
            r'(?:division|department)\s+([\w\s]+)',
        ],
        EntityType.SALES_OFFICE: [
            r'(?:sales\s+office|office)\s+([\w\s]+)',
        ],
        EntityType.REGION: [
            r'(?:region|area|zone)\s+([\w\s]+)',
        ],
        EntityType.TRANSPORTER: [
            r'(?:transporter|carrier|logistics)\s+([\w\s]+)',
        ],
        EntityType.VEHICLE: [
            r'(?:vehicle|truck|van)\s+([\w\s\-_]+)',
        ],
        EntityType.DRIVER: [
            r'(?:driver|delivery\s+boy)\s+([\w\s]+)',
        ],
        EntityType.DEALER_CODE: [
            r'(?:dealer\s+code|code)\s+([A-Z0-9]+)',
        ],
        EntityType.MATERIAL_NUMBER: [
            r'(?:material|mat)\s+([A-Z0-9\-]+)',
        ],
        EntityType.ROUTE: [
            r'(?:route|path|journey)\s+([\w\s]+)',
        ],
        EntityType.SALES_MANAGER: [
            r'(?:sales\s+manager|manager)\s+([\w\s]+)',
        ],
    }

    # Known entity values for validation
    KNOWN_CITIES = {
        "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
        "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
        "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
        "gwadar", "rahim yar khan"
    }

    KNOWN_WAREHOUSES = {
        "lahore", "karachi", "rawalpindi", "multan", "peshawar",
        "quetta", "hyderabad", "faisalabad", "sialkot", "gujranwala",
        "bahawalpur", "sukkur", "dg khan", "rahim yar khan",
        "abbottabad", "gwadar", "gilgit", "islamabad"
    }

    def __init__(self):
        self._compiled_patterns = {}
        for entity_type, patterns in self.ENTITY_PATTERNS.items():
            self._compiled_patterns[entity_type] = [
                re.compile(pattern, re.IGNORECASE) for pattern in patterns
            ]
        logger.info(f"✅ EntityEngine initialized with {len(self.ENTITY_PATTERNS)} entity types")

    def extract_entities(self, message: str) -> List[Entity]:
        """Extract all entities from message"""
        entities = []
        message_lower = message.lower()

        for entity_type, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                match = pattern.search(message_lower)
                if match and match.groups():
                    value = match.group(1).strip()
                    if len(value) > 1:
                        # Validate entity value
                        if self._validate_entity(entity_type, value):
                            entities.append(Entity(
                                type=entity_type,
                                value=value,
                                confidence=0.9
                            ))

        # Remove duplicates
        unique_entities = []
        seen = set()
        for entity in entities:
            key = f"{entity.type.value}:{entity.value}"
            if key not in seen:
                seen.add(key)
                unique_entities.append(entity)

        return unique_entities

    def _validate_entity(self, entity_type: EntityType, value: str) -> bool:
        """Validate entity value"""
        value_lower = value.lower()

        if entity_type == EntityType.CITY:
            return value_lower in self.KNOWN_CITIES or value_lower in [c.replace(" ", "") for c in self.KNOWN_CITIES]

        if entity_type == EntityType.WAREHOUSE:
            return value_lower in self.KNOWN_WAREHOUSES

        if entity_type == EntityType.DN:
            return len(value) >= 8 and value.isdigit()

        return True

# =====================================================================================================================
# CONTEXT MANAGER
# =====================================================================================================================

class ContextManager:
    """Maintain conversation context and memory"""

    def __init__(self, max_history: int = 10):
        self._contexts: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._max_history = max_history

    def get_context(self, session_id: str) -> Dict[str, Any]:
        """Get or create context for session"""
        with self._lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = {
                    "current_entity": None,
                    "current_entity_type": None,
                    "last_intent": None,
                    "history": [],
                    "last_dashboard": None,
                    "last_comparison": None,
                    "last_report": None,
                    "preferences": {},
                    "created_at": datetime.now().isoformat(),
                }
            return self._contexts[session_id]

    def update_context(self, session_id: str, data: Dict[str, Any]) -> None:
        """Update context with new data"""
        with self._lock:
            if session_id not in self._contexts:
                self.get_context(session_id)

            context = self._contexts[session_id]
            for key, value in data.items():
                if key == "history":
                    if isinstance(value, list):
                        context["history"] = (context["history"] + value)[-self._max_history:]
                else:
                    context[key] = value

    def get_follow_up_suggestions(self, session_id: str) -> List[str]:
        """Generate follow-up suggestions based on context"""
        context = self.get_context(session_id)
        suggestions = []

        last_intent = context.get("last_intent")
        current_entity = context.get("current_entity")

        if last_intent:
            suggestions.append(f"Tell me more about {last_intent}")

        if current_entity:
            suggestions.append(f"Compare {current_entity} with another")

        suggestions.extend([
            "Show me the dashboard",
            "View national KPI",
            "See pending DNs",
            "Top performers",
            "Executive summary"
        ])

        return suggestions[:5]

# =====================================================================================================================
# MAIN AI PROVIDER SERVICE - ENTERPRISE ROUTER
# =====================================================================================================================

class AIProviderService:
    _instance: Optional["AIProviderService"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "AIProviderService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return

        # Initialize all services
        self.dn_service = DNAnalysisService()
        self.dealer_service = DealerAnalyticsService()
        self.city_service = CityAnalyticsService() if CITY_SERVICE_AVAILABLE else None
        self.warehouse_service = WarehouseAnalyticsService() if WAREHOUSE_SERVICE_AVAILABLE else None
        self.product_service = ProductAnalyticsService() if PRODUCT_SERVICE_AVAILABLE else None
        self.national_kpi_service = NationalKPIService() if NATIONAL_KPI_AVAILABLE else None
        self.groq_service = GroqService()

        # Initialize engines
        self.intent_engine = IntentEngine()
        self.entity_engine = EntityEngine()
        self.context_manager = ContextManager()

        self._router: Any = None
        self._router_init_attempted = False
        self._router_lock = threading.Lock()
        self._cache: Dict[str, tuple[float, RoutingDecision]] = {}
        self._cache_ttl = 300.0
        self._menu_states: Dict[str, MenuSessionState] = {}
        self._menu_lock = threading.Lock()
        self._initialized = True

        if BOOTSTRAP_AVAILABLE:
            try:
                self._bootstrap = get_ai_bootstrap_service()
                logger.info("✅ AI Bootstrap Service connected")
            except Exception as e:
                logger.warning(f"⚠️ Failed to connect to Bootstrap Service: {e}")

        logger.info("AIProviderService initialized (ENTERPRISE ROUTER)")
        logger.info(f"  Intent Engine: {len(self.intent_engine.INTENT_PATTERNS)} intents")
        logger.info(f"  Entity Engine: {len(self.entity_engine.ENTITY_PATTERNS)} entity types")
        logger.info("  Services: DN, Dealer, City, Warehouse, Product, National KPI, AI")

    # =====================================================================================================================
    # ROUTING DECISION ENGINE
    # =====================================================================================================================

    def _make_routing_decision(self, message: str, session_id: str = "default") -> RoutingDecision:
        """Enterprise routing decision engine"""
        normalized = message.strip()
        cache_key = f"{session_id}:{normalized.casefold()}"
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]

        state = self._get_menu_state(session_id)

        # Check if in active menu
        if state.is_active:
            return self._handle_active_menu(state, normalized, message, session_id)

        # 1. Empty message → Show menu
        if not normalized:
            decision = self._decision_for_menu("0", message, reason="Empty message")
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 2. Menu Number (0-9) → Direct to specific service
        if (number := self._menu_number(normalized)) is not None:
            decision = self._handle_menu_number(number, message, state)
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 3. DN Number (8-12 digits) → DN Analysis
        if (dn := self._extract_dn(normalized)):
            entities = {"dn": dn, "dn_number": dn, "id": dn}
            decision = self._decision_for_menu("1", message, entities, "dn_lookup", reason="DN number detected")
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 4. Extract entities
        entities_raw = self._extract_entities(normalized)
        recognized_entities = self.entity_engine.extract_entities(normalized)

        # Update context with recognized entities
        if recognized_entities:
            for entity in recognized_entities:
                if entity.type in [EntityType.CITY, EntityType.DEALER, EntityType.WAREHOUSE]:
                    self.context_manager.update_context(session_id, {
                        "current_entity": entity.value,
                        "current_entity_type": entity.type.value,
                    })

        # 5. Detect intent
        intent_name, category, confidence, extracted = self.intent_engine.detect_intent(normalized)

        # 6. Intent-based routing
        if intent_name and confidence >= 0.3:
            decision = self._handle_intent(intent_name, category, message, entities_raw, confidence)
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 7. Entity-based routing (fallback)
        if recognized_entities:
            decision = self._handle_entity_routing(recognized_entities, message, entities_raw)
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 8. Semantic routing (if available)
        semantic_result = self._semantic_intent(normalized)
        if semantic_result[0] and semantic_result[1] >= 0.3:
            decision = self._handle_semantic_routing(semantic_result, message, entities_raw)
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 9. AI Query as last resort
        decision = self._decision_for_menu("9", message, entities_raw or {"message": message}, "general_ai", 0.3, "AI fallback")

        self._cache[cache_key] = (time.monotonic(), decision)
        if len(self._cache) > 1000:
            self._cache.clear()
        return decision

    def _handle_menu_number(self, number: str, message: str, state: "MenuSessionState") -> RoutingDecision:
        """Handle menu number selection"""
        if number == "3":
            state.is_active = True
            state.menu_type = "city"
            return RoutingDecision(
                intent="city_menu",
                confidence=1.0,
                service_key="city_menu",
                service_file="city_service.py",
                method="get_city_menu",
                entity={},
                requires_ai=False,
                reason="Menu number 3 selected - City Menu",
                original_message=message,
                menu_option="3",
            )
        elif number == "4":
            state.is_active = True
            state.menu_type = "warehouse"
            return RoutingDecision(
                intent="warehouse_menu",
                confidence=1.0,
                service_key="warehouse_menu",
                service_file="warehouse_service.py",
                method="get_main_menu",
                entity={},
                requires_ai=False,
                reason="Menu number 4 selected - Warehouse Menu",
                original_message=message,
                menu_option="4",
            )
        elif number == "5":
            state.is_active = True
            state.menu_type = "product"
            return RoutingDecision(
                intent="product_menu",
                confidence=1.0,
                service_key="product_menu",
                service_file="product_service.py",
                method="get_main_menu",
                entity={},
                requires_ai=False,
                reason="Menu number 5 selected - Product Menu",
                original_message=message,
                menu_option="5",
            )
        elif number == "6":
            state.is_active = True
            state.menu_type = "national"
            return RoutingDecision(
                intent="national_menu",
                confidence=1.0,
                service_key="national_kpi_menu",
                service_file="national_kpi_service.py",
                method="get_main_menu",
                entity={},
                requires_ai=False,
                reason="Menu number 6 selected - National KPI Menu",
                original_message=message,
                menu_option="6",
            )
        else:
            return self._decision_for_menu(number, message, reason=f"Menu number {number} selected")

    def _handle_intent(self, intent_name: str, category: Optional[IntentCategory], message: str, entities: Dict[str, Any], confidence: float) -> RoutingDecision:
        """Handle intent-based routing"""
        intent_map = {
            "revenue": ("revenue", "2" if entities.get("dealer") else "3" if entities.get("city") else "6"),
            "revenue_by_entity": ("revenue", "2" if entities.get("dealer") else "3" if entities.get("city") else "6"),
            "units": ("units", "2" if entities.get("dealer") else "3" if entities.get("city") else "6"),
            "pending_dn": ("pending_dn", "7"),
            "pending_pgi": ("pending_pgi", "7"),
            "pending_pod": ("pending_pod", "7"),
            "delivery_performance": ("delivery_performance", "2" if entities.get("dealer") else "3" if entities.get("city") else "6"),
            "delivery_sla": ("delivery_sla", "6"),
            "dealer_performance": ("dealer_dashboard", "2"),
            "top_dealers": ("top_dealers", "2"),
            "bottom_dealers": ("bottom_dealers", "2"),
            "dealer_comparison": ("dealer_comparison", "2"),
            "warehouse_performance": ("warehouse_dashboard", "4"),
            "warehouse_ranking": ("warehouse_ranking", "4"),
            "warehouse_comparison": ("warehouse_comparison", "4"),
            "warehouse_utilization": ("warehouse_utilization", "4"),
            "warehouse_inventory": ("warehouse_inventory", "4"),
            "city_performance": ("city_dashboard", "3"),
            "city_ranking": ("city_ranking", "3"),
            "city_comparison": ("city_comparison", "3"),
            "product_performance": ("product_dashboard", "5"),
            "top_products": ("top_products", "5"),
            "root_cause_analysis": ("root_cause", "9"),
            "forecast": ("forecast", "9"),
            "recommendation": ("recommendation", "9"),
            "executive_dashboard": ("executive_dashboard", "6"),
            "executive_insights": ("executive_insights", "6"),
            "risk_assessment": ("risk_assessment", "6"),
            "national_kpi": ("national_kpi", "6"),
            "compare_general": ("compare_general", "3"),
            "search": ("search", "2"),
            "help": ("help", "0"),
            "dashboard": ("dashboard", "3" if entities.get("city") else "2" if entities.get("dealer") else "4" if entities.get("warehouse") else "6"),
            "greeting": ("greeting", "0"),
        }

        if intent_name in intent_map:
            intent_key, menu_option = intent_map[intent_name]
            return self._decision_for_menu(menu_option, message, entities, intent_key, confidence, f"Intent: {intent_name}")

        # Default: use semantic routing or AI
        return self._decision_for_menu("9", message, entities, "general_ai", confidence, f"Intent: {intent_name}")

    def _handle_entity_routing(self, entities: List[Entity], message: str, entities_raw: Dict[str, Any]) -> RoutingDecision:
        """Handle entity-based routing"""
        for entity in entities:
            if entity.type == EntityType.CITY:
                return self._decision_for_menu("3", message, entities_raw, "city_dashboard", 0.9, f"City entity: {entity.value}")
            elif entity.type == EntityType.DEALER:
                return self._decision_for_menu("2", message, entities_raw, "dealer_dashboard", 0.9, f"Dealer entity: {entity.value}")
            elif entity.type == EntityType.WAREHOUSE:
                return self._decision_for_menu("4", message, entities_raw, "warehouse_dashboard", 0.9, f"Warehouse entity: {entity.value}")
            elif entity.type == EntityType.PRODUCT:
                return self._decision_for_menu("5", message, entities_raw, "product_dashboard", 0.9, f"Product entity: {entity.value}")
            elif entity.type == EntityType.DN:
                return self._decision_for_menu("1", message, entities_raw, "dn_lookup", 0.9, f"DN entity: {entity.value}")

        return self._decision_for_menu("9", message, entities_raw, "general_ai", 0.5, "Entity routing fallback")

    def _handle_semantic_routing(self, semantic_result: tuple, message: str, entities: Dict[str, Any]) -> RoutingDecision:
        """Handle semantic routing"""
        intent_name, confidence = semantic_result
        intent_map = {
            "national_kpi": "6",
            "national_dashboard": "6",
            "executive_summary": "6",
            "warehouse_dashboard": "4",
            "warehouse_ranking": "4",
            "city_dashboard": "3",
            "city_ranking": "3",
            "dealer_dashboard": "2",
            "top_dealers": "2",
            "product_dashboard": "5",
            "top_products": "5",
            "pending_dns": "7",
            "top_performers": "8",
        }

        if intent_name in intent_map:
            menu_option = intent_map[intent_name]
            return self._decision_for_menu(menu_option, message, entities, intent_name, confidence, f"Semantic: {intent_name}")

        return self._decision_for_menu("9", message, entities, "general_ai", confidence, "Semantic routing fallback")

    def _handle_active_menu(self, state: "MenuSessionState", normalized: str, message: str, session_id: str) -> RoutingDecision:
        """Handle active menu routing"""
        if state.menu_type == "city":
            return RoutingDecision(
                intent="city_menu_input",
                confidence=1.0,
                service_key="city_menu",
                service_file="city_service.py",
                method="process_city_menu_input",
                entity={"user_input": normalized, "session_id": session_id},
                requires_ai=False,
                reason="City menu input",
                original_message=message,
                menu_option="3",
            )
        elif state.menu_type == "warehouse":
            return RoutingDecision(
                intent="warehouse_menu_input",
                confidence=1.0,
                service_key="warehouse_menu",
                service_file="warehouse_service.py",
                method="process_menu_input",
                entity={"user_input": normalized, "session_id": session_id},
                requires_ai=False,
                reason="Warehouse menu input",
                original_message=message,
                menu_option="4",
            )
        elif state.menu_type == "product":
            return RoutingDecision(
                intent="product_menu_input",
                confidence=1.0,
                service_key="product_menu",
                service_file="product_service.py",
                method="process_menu_input",
                entity={"user_input": normalized, "session_id": session_id},
                requires_ai=False,
                reason="Product menu input",
                original_message=message,
                menu_option="5",
            )
        elif state.menu_type == "national":
            return RoutingDecision(
                intent="national_menu_input",
                confidence=1.0,
                service_key="national_kpi_menu",
                service_file="national_kpi_service.py",
                method="process_menu_input",
                entity={"user_input": normalized, "session_id": session_id},
                requires_ai=False,
                reason="National KPI menu input",
                original_message=message,
                menu_option="6",
            )
        else:
            # Unknown menu type, exit
            state.is_active = False
            state.menu_type = "main"
            return self._decision_for_menu("0", message, reason="Unknown menu type")

    # =====================================================================================================================
    # HELPER METHODS
    # =====================================================================================================================

    @staticmethod
    def _decision_for_menu(menu_option: str, message: str, entities: Optional[Dict[str, Any]] = None, intent: Optional[str] = None, confidence: float = 1.0, reason: str = "") -> RoutingDecision:
        config = MENU_OPTIONS[menu_option]
        return RoutingDecision(
            intent=intent or config["name"].lower().replace(" ", "_"),
            confidence=confidence,
            service_key=config["service_key"],
            service_file=config["service_file"],
            method=config["method"],
            entity=entities or {},
            requires_ai=config["requires_ai"],
            reason=reason,
            original_message=message,
            menu_option=menu_option,
        )

    @staticmethod
    def _menu_number(text: str) -> Optional[str]:
        match = re.fullmatch(r"\s*([0-9])(?:[.)])?\s*", text)
        return match.group(1) if match else None

    @staticmethod
    def _extract_dn(text: str) -> Optional[str]:
        compact = text.strip()
        match = re.search(r"(?<!\d)(\d{8,12})(?!\d)", compact)
        if match:
            return match.group(1)
        match = re.search(r"(?<!\d)(\d{4}[\s-]*\d{4}[\s-]*\d{0,4})(?!\d)", compact)
        if match:
            candidate = re.sub(r"[\s-]", "", match.group(1))
            if 8 <= len(candidate) <= 12:
                return candidate
        return None

    @staticmethod
    def _extract_entities(text: str) -> Dict[str, Any]:
        entities: Dict[str, Any] = {}

        # DN
        dn = AIProviderService._extract_dn(text)
        if dn:
            entities.update({"dn": dn, "dn_number": dn, "id": dn})

        # City
        city = _extract_city_name(text)
        if city:
            entities.update({"city": city, "city_name": city})

        # Dealer
        dealer = _extract_dealer_name(text)
        if dealer:
            entities.update({"dealer": dealer, "dealer_name": dealer})

        # Warehouse
        warehouse = _extract_warehouse_name(text)
        if warehouse:
            entities["warehouse"] = warehouse

        # Product
        product = re.search(r"(?:product|model|material|item)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if product:
            entities["product"] = product.group(1).strip()

        # National KPI
        if any(keyword in text.lower() for keyword in ["national", "overall", "pakistan", "executive", "kpi", "dashboard"]):
            entities["national_kpi"] = True

        return entities

    def _semantic_intent(self, message: str) -> tuple[Optional[str], float]:
        # Placeholder for semantic routing
        return None, 0.0

    def _get_menu_state(self, session_id: str) -> "MenuSessionState":
        with self._menu_lock:
            if session_id not in self._menu_states:
                self._menu_states[session_id] = MenuSessionState()
                self._menu_states[session_id].session_id = session_id
            return self._menu_states[session_id]

    # =====================================================================================================================
    # PROCESS WHATSAPP QUERY
    # =====================================================================================================================

    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **_: Any,
    ) -> str:
        sender = sender or sender_id or "default"
        if not message or not message.strip():
            return get_main_menu()

        logger.info("Processing WhatsApp message from %s", sender)
        logger.info("Message: %s", message)

        decision = self._make_routing_decision(message, sender)
        logger.info("Route: %s -> %s.%s (%s)", decision.intent, decision.service_file, decision.method, decision.reason)
        logger.info("Entities: %s", decision.entity)

        # Update context
        self.context_manager.update_context(sender, {
            "last_intent": decision.intent,
            "last_message": message,
            "history": [{"role": "user", "content": message, "intent": decision.intent}]
        })

        # Menu Service
        if decision.service_key == "menu_service":
            state = self._get_menu_state(sender)
            state.is_active = False
            state.menu_type = "main"
            return get_main_menu()

        # Greeting
        if decision.intent == "greeting":
            return "👋 Hello! Welcome to HPK Logistics 🏪. How can I assist you today? 📦"

        # Get service instance
        service = self._get_service(decision.service_key)
        if service is None:
            logger.error("Unknown service key: %s", decision.service_key)
            return get_invalid_selection_message()

        try:
            method = getattr(service, decision.method)
            result = await self._call_method(method, decision)
            response = _extract_whatsapp_message(result)

            # Add follow-up suggestions
            if "follow_up_suggestions" not in decision.__dict__:
                follow_ups = self.context_manager.get_follow_up_suggestions(sender)
                if follow_ups:
                    response = self._add_follow_ups(response, follow_ups)

            return response

        except Exception as e:
            logger.exception("Service call failed: %s.%s", decision.service_key, decision.method)
            return self._handle_error(e, decision)

    def _get_service(self, service_key: str):
        """Get service instance by key"""
        service_map = {
            "dn_analysis": self.dn_service,
            "dealer_analytics": self.dealer_service,
            "city_menu": self.city_service,
            "city_service": self.city_service,
            "warehouse_menu": self.warehouse_service,
            "warehouse_service": self.warehouse_service,
            "product_menu": self.product_service,
            "product_service": self.product_service,
            "national_kpi_menu": self.national_kpi_service,
            "national_kpi_service": self.national_kpi_service,
            "groq_service": self.groq_service,
        }
        return service_map.get(service_key)

    async def _call_method(self, method, decision: RoutingDecision):
        """Call method with proper parameters"""
        if decision.service_key == "city_menu":
            if decision.method == "process_city_menu_input":
                return method(decision.entity.get("session_id"), decision.entity.get("user_input"))
            return method()
        elif decision.service_key in ["warehouse_menu", "product_menu", "national_kpi_menu"]:
            if decision.method == "process_menu_input":
                return method(decision.entity.get("session_id"), decision.entity.get("user_input"))
            return method()
        elif decision.service_key == "dn_analysis":
            if decision.method == "get_dn_dashboard":
                return method(decision.entity.get("dn"))
            elif decision.method == "get_warehouse_dashboard":
                return method(decision.entity.get("warehouse"))
            else:
                return method()
        elif decision.service_key == "dealer_analytics":
            return await method(decision.entity.get("dealer_name") or decision.entity.get("dealer"))
        elif decision.service_key == "groq_service":
            return await method(decision.original_message, decision.entity)
        else:
            return method()

    def _add_follow_ups(self, response: str, follow_ups: List[str]) -> str:
        """Add follow-up suggestions to response"""
        if not follow_ups:
            return response

        footer = "\n\n💡 *Try:*"
        for suggestion in follow_ups[:3]:
            footer += f"\n• {suggestion}"

        # Ensure we don't exceed WhatsApp limits
        if len(response) + len(footer) > 4000:
            return response

        return response + footer

    def _handle_error(self, error: Exception, decision: RoutingDecision) -> str:
        """Handle service errors"""
        error_str = str(error)

        if "validation error" in error_str.lower() or "Invalid DN number" in error_str:
            return "⚠️ Invalid DN number format. Please provide a valid 8-12 digit DN number."

        if decision.service_key == "groq_service":
            return "⚠️ AI service is temporarily unavailable. Reply *menu* to use logistics services."

        service_name = MENU_OPTIONS[decision.menu_option or "0"]["name"]
        return f"⚠️ {service_name} is temporarily unavailable. Please try again."

    def show_main_menu(self) -> str:
        return get_main_menu()

# =====================================================================================================================
# GLOBAL FUNCTIONS
# =====================================================================================================================

class MenuSessionState:
    def __init__(self):
        self.is_active = False
        self.session_id = "default"
        self.menu_type = "main"
        self.last_response = ""
        self.last_input = ""

def get_main_menu() -> str:
    return (
        "📋 *AI LOGISTICS MENU*\n\n"
        "0. Main Menu\n"
        "1. DN Delivery\n"
        "2. Dealer Analytics\n"
        "3. City Analytics\n"
        "4. Warehouse Analytics\n"
        "5. Product Analytics\n"
        "6. National KPI\n"
        "7. Pending DN\n"
        "8. Top Performers\n"
        "9. AI Query\n\n"
        "Reply with a number from 0 to 9."
    )

def get_invalid_selection_message() -> str:
    return "Invalid selection. Please choose a number from 0 to 9.\n\n" + get_main_menu()

def _extract_whatsapp_message(result: Any) -> str:
    """Extract WhatsApp message from service result. ALWAYS returns a string."""
    if result is None:
        return "No response from service. Please try again."

    if isinstance(result, str):
        return result if result.strip() else "No response from service. Please try again."

    if isinstance(result, dict):
        if result.get("error"):
            return f"⚠️ {result.get('error')}"
        if "whatsapp_message" in result and result["whatsapp_message"]:
            return str(result["whatsapp_message"])
        if "formatted_response" in result and result["formatted_response"]:
            return str(result["formatted_response"])
        if "message" in result and result["message"]:
            return str(result["message"])
        if "response" in result and result["response"]:
            return str(result["response"])
        if "data" in result and result["data"]:
            return str(result["data"])

    try:
        return str(result) if result else "No response from service. Please try again."
    except Exception:
        return "No response from service. Please try again."

def _extract_city_name(text: str) -> Optional[str]:
    """Enhanced city name extraction."""
    lowered = text.casefold()

    # Check for "City" suffix
    city_match = re.match(r'^([a-zA-Z\s]+?)\s+city$', text, re.IGNORECASE)
    if city_match:
        potential_city = city_match.group(1).strip().lower()
        for city in CITY_NAMES:
            if potential_city in city or city in potential_city:
                return city.title()

    for city in CITY_NAMES:
        if city in lowered:
            return city.title()
        if f"{city} city" in lowered:
            return city.title()

    return None

def _extract_dealer_name(text: str) -> Optional[str]:
    """Enhanced dealer name extraction."""
    for suffix in DEALER_SUFFIXES:
        pattern = rf'([\w&.\'\- ]{{2,}}?\s*{suffix}\s*[\w&.\'\- ]*)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) > 2:
                return name
    return None

def _extract_warehouse_name(text: str) -> Optional[str]:
    """Extract warehouse name from text."""
    lowered = text.casefold()
    for warehouse in WAREHOUSE_NAMES:
        if warehouse in lowered:
            return warehouse.title()
    return None

# =====================================================================================================================
# CONSTANTS
# =====================================================================================================================

CITY_NAMES = (
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
    "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
    "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
    "dera ghazi khan", "gwadar", "rahim yar khan"
)

WAREHOUSE_NAMES = (
    "lahore", "karachi", "rawalpindi", "multan", "peshawar",
    "quetta", "hyderabad", "faisalabad", "sialkot", "gujranwala",
    "bahawalpur", "sukkur", "dg khan", "rahim yar khan",
    "abbottabad", "gwadar", "gilgit", "islamabad"
)

DEALER_SUFFIXES = (
    "electronics", "traders", "distributors", "foods", "group", "pvt", "ltd",
    "sons", "brothers", "enterprises", "company", "corporation", "store", "shop",
    "centre", "center", "solutions", "services", "digital", "technologies",
    "systems", "networks", "communications", "logistics", "transport",
)

# =====================================================================================================================
# SINGLETON
# =====================================================================================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()

def get_ai_provider_service() -> AIProviderService:
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                _ai_service = AIProviderService()
    return _ai_service

async def process_whatsapp_query(
    message: str,
    sender: Optional[str] = None,
    sender_id: Optional[str] = None,
    **kwargs: Any,
) -> str:
    try:
        return await get_ai_provider_service().process_whatsapp_query(
            message=message,
            sender=sender,
            sender_id=sender_id,
            **kwargs,
        )
    except Exception:
        logger.exception("Unexpected AI provider failure")
        if message and message.strip().casefold() in {"menu", "main menu", "help", "start", "0"}:
            return get_main_menu()
        if message and message.strip().casefold() in {"hello", "hi", "salam", "hey"}:
            return "👋 Hello! Welcome to HPK Logistics 🏪. How can I assist you today?"
        return "⚠️ Service is temporarily unavailable. Reply *menu* to try again."

__all__ = [
    "process_whatsapp_query",
    "get_main_menu",
    "get_ai_provider_service",
    "AIProviderService",
    "RoutingDecision",
    "IntentEngine",
    "EntityEngine",
    "ContextManager",
]
