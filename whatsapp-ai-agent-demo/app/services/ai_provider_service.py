# ==========================================================
# FILE: app/services/ai_provider_service.py (v21.0 - MASTER AI ROUTER WITH FULL DASHBOARDS)
# ==========================================================
# PURPOSE: AI ROUTER - Routes queries to appropriate services
# VERSION: 21.0 - Master AI Router with Full Dashboard Support
#
# ROLE: This file is the AI Router.
#        This file must NEVER perform analytics.
#        Analytics always come from analytics_service.py
#
# FLOW:
# User Message → Intent Detection → Analytics Service → Format Response → Optional Groq → WhatsApp
#
# INTENTS:
# Dealer Dashboard | Warehouse Dashboard | City Dashboard | Product Dashboard
# DN Dashboard | PGI Dashboard | POD Dashboard | Delivery Dashboard
# Distance Dashboard | Executive Dashboard | Control Tower Dashboard
# Dealer Ranking | Warehouse Ranking | Product Ranking | Transporter Dashboard
# Revenue Dashboard | Inventory Dashboard | Forecast Dashboard
#
# ENTITY RECOGNITION:
# Dealer Name | Dealer Code | Customer Code | Warehouse | City
# Material | Product Model | DN Number | Sales Office | Division
#
# CONTEXT MEMORY:
# Remember: last_dealer, last_warehouse, last_city, last_product, last_dashboard
#
# FOLLOW-UP SUPPORT:
# "What is its POD?" → Uses last_dealer context
# "How many pending DN?" → Uses last_dealer context
# "Show me its revenue" → Uses last_dealer context
#
# FINAL RULE: Analytics First | Groq Second | Database Truth Always
#             Never Hallucinate | Never Crash | Always Fast | Always WhatsApp Safe
# ==========================================================

import time
import uuid
import hashlib
import re
import asyncio
import concurrent.futures
import traceback
import math
from typing import Optional, Callable, Any, Dict, List, Tuple, Set
from enum import Enum
from dataclasses import dataclass
from cachetools import TTLCache, LRUCache
from loguru import logger
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from functools import lru_cache

# ==========================================================
# ULTRA-FAST IMPORTS
# ==========================================================

# Ultra-fast JSON
try:
    import orjson
    JSON_FAST = True
except:
    import json
    orjson = None
    JSON_FAST = False

# Ultra-fast fuzzy matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except:
    from difflib import SequenceMatcher
    RAPIDFUZZ_AVAILABLE = False

# Redis caching
try:
    import redis
    REDIS_AVAILABLE = True
except:
    REDIS_AVAILABLE = False

# Tenacity retry
try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except:
    TENACITY_AVAILABLE = False

# ==========================================================
# LAZY IMPORTS - Avoid circular dependencies
# ==========================================================

def _get_ai_query_service():
    from app.services.ai_query_service import get_ai_query_service
    return get_ai_query_service()

def _get_analytics_service():
    from app.services.analytics_service import get_analytics_service, AnalyticsResponse
    return get_analytics_service(), AnalyticsResponse

def _get_kpi_service():
    from app.services.kpi_service import get_kpi_service
    return get_kpi_service()

def _get_groq_service():
    from app.services.groq_service import get_groq_service
    return get_groq_service()

def _get_schema_service():
    from app.schemas.schema_service import get_schema_service
    return get_schema_service()

def _get_whatsapp_service():
    from app.services.whatsapp_service import get_whatsapp_service
    return get_whatsapp_service()


# ==========================================================
# CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800
MAX_RETRY_ATTEMPTS = 3
DEALER_SUGGESTION_LIMIT = 3

# ⚡ SPEED OPTIMIZED TIMEOUTS
GROQ_TIMEOUT_SECONDS = 8
ENRICHMENT_TIMEOUT_SECONDS = 3
DB_TIMEOUT_SECONDS = 10
OPENROUTE_TIMEOUT_SECONDS = 5

MAX_RECOVERY_ATTEMPTS = 3
MAX_RESPONSE_LENGTH = 3000  # WhatsApp character limit

DN_PATTERN_LOOSE = re.compile(r'\b(\d{8,12})\b')

# ==========================================================
# DASHBOARD TYPES - ENUM
# ==========================================================

class DashboardType(Enum):
    DEALER = "dealer_dashboard"
    WAREHOUSE = "warehouse_dashboard"
    CITY = "city_dashboard"
    PRODUCT = "product_dashboard"
    DN = "dn_dashboard"
    PGI = "pgi_dashboard"
    POD = "pod_dashboard"
    DELIVERY = "delivery_dashboard"
    DISTANCE = "distance_dashboard"
    EXECUTIVE = "executive_dashboard"
    CONTROL_TOWER = "control_tower_dashboard"
    DEALER_RANKING = "dealer_ranking_dashboard"
    WAREHOUSE_RANKING = "warehouse_ranking_dashboard"
    PRODUCT_RANKING = "product_ranking_dashboard"
    TRANSPORTER = "transporter_dashboard"
    REVENUE = "revenue_dashboard"
    INVENTORY = "inventory_dashboard"
    FORECAST = "forecast_dashboard"

class EntityType(Enum):
    DEALER_NAME = "dealer_name"
    DEALER_CODE = "dealer_code"
    CUSTOMER_CODE = "customer_code"
    WAREHOUSE = "warehouse"
    CITY = "city"
    MATERIAL = "material"
    PRODUCT_MODEL = "product_model"
    DN_NUMBER = "dn_number"
    SALES_OFFICE = "sales_office"
    DIVISION = "division"

# ==========================================================
# DISTANCE & TRANSIT CONFIGURATION
# ==========================================================

EARTH_RADIUS_KM = 6371.0

TRANSIT_DAYS_RULES = {
    "same_city": 1,
    "0-50": 1,
    "51-150": 2,
    "151-300": 3,
    "301-500": 4,
    "501-800": 5,
    "800+": 7
}

RISK_THRESHOLDS = {
    "low": 0.10,
    "medium": 0.30,
    "high": 0.30
}

# ==========================================================
# ENTITY PATTERNS FOR RECOGNITION
# ==========================================================

ENTITY_PATTERNS = {
    "dealer_name": r'(?:dealer|customer|party)\s+([A-Za-z0-9\s&]+)',
    "dealer_code": r'(?:code|dealer code)\s*[:#]?\s*([A-Za-z0-9\-]+)',
    "customer_code": r'(?:customer code|sold to)\s*[:#]?\s*([A-Za-z0-9\-]+)',
    "warehouse": r'(?:warehouse|wh)\s+([A-Za-z0-9\s]+)',
    "city": r'(?:city|location)\s+([A-Za-z\s]+)',
    "material": r'(?:material|mat)\s+([A-Za-z0-9\-]+)',
    "product_model": r'(?:model|product)\s+([A-Za-z0-9\-]+)',
    "dn_number": r'\b(\d{8,12})\b',
    "sales_office": r'(?:sales office|office)\s+([A-Za-z\s]+)',
    "division": r'(?:division|div)\s+([A-Za-z\s]+)',
}

# ==========================================================
# INTENT CLASSIFICATION - Full Dashboard Support
# ==========================================================

INTENT_PATTERNS = {
    # 1. Dealer Dashboard
    "dealer_dashboard": [
        "dealer", "customer", "show me dealer", "dealer performance",
        "dealer revenue", "dealer units", "dealer ranking",
        "top dealer", "best dealer", "dealer dashboard",
        "customer performance", "customer dashboard"
    ],
    # 2. Warehouse Dashboard
    "warehouse_dashboard": [
        "warehouse", "show me warehouse", "warehouse performance",
        "warehouse revenue", "warehouse ranking", "warehouse dashboard"
    ],
    # 3. City Dashboard
    "city_dashboard": [
        "city", "show me city", "city performance", "city revenue",
        "city ranking", "top city", "worst city", "city dashboard"
    ],
    # 4. Product Dashboard
    "product_dashboard": [
        "product", "model", "top product", "best seller",
        "product performance", "product revenue", "product dashboard",
        "top model", "best model", "material"
    ],
    # 5. DN Dashboard
    "dn_dashboard": [
        "dn", "track", "delivery note", "order status",
        "where is", "shipment", "delivery status", "track dn",
        "delivery note", "dn number"
    ],
    # 6. PGI Dashboard
    "pgi_dashboard": [
        "pgi", "goods issue", "pgi status", "pgi pending",
        "pgi completed", "pgi dashboard"
    ],
    # 7. POD Dashboard
    "pod_dashboard": [
        "pod", "pending pod", "pod collection", "pod status",
        "pod compliance", "pod aging", "pod dashboard",
        "proof of delivery"
    ],
    # 8. Delivery Dashboard
    "delivery_dashboard": [
        "delivery", "pending delivery", "delayed delivery",
        "delivery performance", "delivery rate", "delivery dashboard"
    ],
    # 9. Distance Dashboard
    "distance_dashboard": [
        "distance", "how far", "transit", "travel time",
        "distance from warehouse", "expected delivery", "distance dashboard"
    ],
    # 10. Executive Dashboard
    "executive_dashboard": [
        "executive", "ceo", "management", "strategic",
        "nationwide", "overview", "business summary",
        "executive dashboard", "executive summary"
    ],
    # 11. Control Tower Dashboard
    "control_tower": [
        "control tower", "control", "tower", "alerts",
        "critical issues", "logistics control", "control dashboard"
    ],
    # 12. Dealer Ranking
    "dealer_ranking": [
        "dealer ranking", "top dealers", "best dealers",
        "dealer rank", "ranking dealer"
    ],
    # 13. Warehouse Ranking
    "warehouse_ranking": [
        "warehouse ranking", "top warehouses", "best warehouses",
        "warehouse rank", "ranking warehouse"
    ],
    # 14. Product Ranking
    "product_ranking": [
        "product ranking", "top products", "best products",
        "product rank", "ranking product", "best selling"
    ],
    # 15. Transporter Dashboard
    "transporter_dashboard": [
        "transporter", "carrier", "logistics partner",
        "transporter performance", "transporter dashboard"
    ],
    # 16. Revenue Dashboard
    "revenue_dashboard": [
        "revenue", "sales", "income", "turnover",
        "revenue summary", "sales performance", "revenue dashboard"
    ],
    # 17. Inventory Dashboard
    "inventory_dashboard": [
        "inventory", "stock", "warehouse stock",
        "inventory status", "stock level", "inventory dashboard"
    ],
    # 18. Forecast Dashboard
    "forecast_dashboard": [
        "forecast", "predict", "estimated", "projected",
        "next month", "expected revenue", "future", "forecast dashboard"
    ]
}

# ==========================================================
# DASHBOARD ROUTING MATRIX
# ==========================================================

DASHBOARD_ROUTING_MATRIX = {
    "dealer_dashboard": {
        "handler": "_route_dealer_dashboard",
        "requires": ["dealer_name", "dealer_code", "customer_code"],
        "follow_up": ["performance", "revenue", "pod", "dn", "ranking"],
        "drill_down": ["dealer_details", "dealer_timeline", "dealer_products"],
        "display_name": "Dealer Dashboard"
    },
    "warehouse_dashboard": {
        "handler": "_route_warehouse_dashboard",
        "requires": ["warehouse"],
        "follow_up": ["performance", "coverage", "revenue", "ranking"],
        "drill_down": ["warehouse_details", "warehouse_top_dealers"],
        "display_name": "Warehouse Dashboard"
    },
    "city_dashboard": {
        "handler": "_route_city_dashboard",
        "requires": ["city"],
        "follow_up": ["performance", "top_dealers", "revenue", "ranking"],
        "drill_down": ["city_details", "city_top_products"],
        "display_name": "City Dashboard"
    },
    "product_dashboard": {
        "handler": "_route_product_dashboard",
        "requires": ["product_model", "material"],
        "follow_up": ["performance", "revenue", "ranking"],
        "drill_down": ["product_details", "product_trend"],
        "display_name": "Product Dashboard"
    },
    "dn_dashboard": {
        "handler": "_route_dn_dashboard",
        "requires": ["dn_number"],
        "follow_up": ["status", "delivery", "pod", "pgi"],
        "drill_down": ["dn_details", "dn_timeline"],
        "display_name": "DN Dashboard"
    },
    "pgi_dashboard": {
        "handler": "_route_pgi_dashboard",
        "requires": ["dn_number", "dealer_name"],
        "follow_up": ["status", "pending", "completed"],
        "drill_down": ["pgi_details", "pgi_timeline"],
        "display_name": "PGI Dashboard"
    },
    "pod_dashboard": {
        "handler": "_route_pod_dashboard",
        "requires": ["dn_number", "dealer_name"],
        "follow_up": ["status", "pending", "aging", "compliance"],
        "drill_down": ["pod_details", "pod_timeline"],
        "display_name": "POD Dashboard"
    },
    "delivery_dashboard": {
        "handler": "_route_delivery_dashboard",
        "requires": [],
        "follow_up": ["performance", "rate", "pending", "delayed"],
        "drill_down": ["delivery_details", "delivery_trend"],
        "display_name": "Delivery Dashboard"
    },
    "distance_dashboard": {
        "handler": "_route_distance_dashboard",
        "requires": ["dealer_name", "warehouse"],
        "follow_up": ["transit", "travel_time", "route"],
        "drill_down": ["distance_details", "route_analysis"],
        "display_name": "Distance Dashboard"
    },
    "executive_dashboard": {
        "handler": "_route_executive_dashboard",
        "requires": [],
        "follow_up": ["summary", "insights", "kpi", "risks"],
        "drill_down": ["executive_details", "strategic_insights"],
        "display_name": "Executive Dashboard"
    },
    "control_tower": {
        "handler": "_route_control_tower",
        "requires": [],
        "follow_up": ["alerts", "critical", "issues", "sla"],
        "drill_down": ["alert_details", "risk_analysis"],
        "display_name": "Control Tower Dashboard"
    },
    "dealer_ranking": {
        "handler": "_route_dealer_ranking",
        "requires": [],
        "follow_up": ["top", "bottom", "revenue", "delivery"],
        "drill_down": ["ranking_details", "dealer_compare"],
        "display_name": "Dealer Ranking"
    },
    "warehouse_ranking": {
        "handler": "_route_warehouse_ranking",
        "requires": [],
        "follow_up": ["top", "bottom", "revenue", "delivery"],
        "drill_down": ["ranking_details", "warehouse_compare"],
        "display_name": "Warehouse Ranking"
    },
    "product_ranking": {
        "handler": "_route_product_ranking",
        "requires": [],
        "follow_up": ["top", "best_selling", "revenue"],
        "drill_down": ["ranking_details", "product_compare"],
        "display_name": "Product Ranking"
    },
    "transporter_dashboard": {
        "handler": "_route_transporter_dashboard",
        "requires": ["transporter_name"],
        "follow_up": ["performance", "delivery", "rating"],
        "drill_down": ["transporter_details", "transporter_performance"],
        "display_name": "Transporter Dashboard"
    },
    "revenue_dashboard": {
        "handler": "_route_revenue_dashboard",
        "requires": [],
        "follow_up": ["summary", "trend", "by_dealer", "by_city"],
        "drill_down": ["revenue_details", "revenue_trend"],
        "display_name": "Revenue Dashboard"
    },
    "inventory_dashboard": {
        "handler": "_route_inventory_dashboard",
        "requires": ["warehouse", "material"],
        "follow_up": ["stock", "status", "movement"],
        "drill_down": ["inventory_details", "inventory_status"],
        "display_name": "Inventory Dashboard"
    },
    "forecast_dashboard": {
        "handler": "_route_forecast_dashboard",
        "requires": [],
        "follow_up": ["revenue", "units", "dns", "trend"],
        "drill_down": ["forecast_details", "forecast_explanation"],
        "display_name": "Forecast Dashboard"
    }
}

# ==========================================================
# SPECIAL COMMANDS (Instant Response)
# ==========================================================

SPECIAL_COMMANDS = {
    "control tower": "control_tower",
    "control": "control_tower",
    "tower": "control_tower",
    "executive summary": "executive_dashboard",
    "executive insights": "executive_dashboard",
    "executive": "executive_dashboard",
    "ceo": "executive_dashboard",
    "management": "executive_dashboard",
    "help": "help",
    "hi": "help",
    "hello": "help",
    "menu": "help",
    "start": "help",
    "whatsapp menu": "help"
}

# ==========================================================
# GROQ INTENT PATTERNS (When to use Groq)
# ==========================================================

GROQ_INTENT_PATTERNS = {
    "root_cause": ["why", "root cause", "reason", "cause", "because", "due to"],
    "recommendation": ["recommend", "suggest", "advise", "should", "improve", "fix"],
    "executive": ["executive", "ceo", "strategy", "management", "critical"],
    "insight": ["insight", "trend", "pattern", "analysis"],
    "forecast_explain": ["forecast explanation", "why forecast", "predict why"],
    "kpi_explain": ["what is kpi", "explain kpi", "kpi meaning", "how is kpi calculated"],
}

# ==========================================================
# CONVERSATION CONTEXT
# ==========================================================

@dataclass
class ConversationContext:
    phone_number: str
    last_intent: Optional[str] = None
    last_entity: Optional[str] = None
    last_dealer: Optional[str] = None
    last_warehouse: Optional[str] = None
    last_city: Optional[str] = None
    last_dn: Optional[str] = None
    last_product: Optional[str] = None
    last_dashboard: Optional[str] = None
    last_question: Optional[str] = None
    last_response: Optional[str] = None
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    confidence: float = 0.0
    retry_count: int = 0
    is_valid: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_city": self.last_city,
            "last_dn": self.last_dn,
            "last_product": self.last_product,
            "last_dashboard": self.last_dashboard,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number,
            "last_question": self.last_question,
            "confidence": self.confidence,
            "retry_count": self.retry_count,
            "is_valid": self.is_valid
        }


# ==========================================================
# MASTER AI ROUTER - v21.0
# ==========================================================

class AIOrchestrator:
    """
    MASTER AI ROUTER - v21.0
    
    ROLE: This file is the AI Router.
    This file must NEVER perform analytics.
    Analytics always come from analytics_service.py
    
    FLOW:
    User Message → Intent Detection → Analytics Service → Format Response → Optional Groq → WhatsApp
    
    RULES:
    1. Analytics First - Always try analytics_service.py first
    2. Groq Second - Only for specific intents (Why, Recommendations, Executive, etc.)
    3. Database Truth Always - Never hallucinate data
    4. Never Crash - Always handle errors gracefully
    5. Always Fast - Use caching and async where possible
    6. Always WhatsApp Safe - Max 3000 chars, proper formatting
    """
    
    def __init__(self):
        # Lazy loaded services
        self._query_service = None
        self._analytics = None
        self._analytics_response = None
        self._kpi = None
        self._groq = None
        self._schema = None
        self._whatsapp = None
        self._dn_pattern = DN_PATTERN_LOOSE
        
        # ==========================================================
        # CACHES
        # ==========================================================
        
        self.response_cache = TTLCache(maxsize=1000, ttl=CACHE_TTL_SECONDS)
        self.failure_cache = TTLCache(maxsize=200, ttl=60)
        self.fast_cache = LRUCache(maxsize=500)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        self.dealer_resolution_cache: Dict[str, Tuple[str, float, float]] = {}
        self._suggestion_cache: Dict[str, List[str]] = {}  # ← FIXED: Added missing cache
        
        # ==========================================================
        # REDIS CACHE (if available)
        # ==========================================================
        
        self._redis_client = None
        if REDIS_AVAILABLE:
            try:
                self._redis_client = redis.Redis(
                    host='localhost',
                    port=6379,
                    decode_responses=True,
                    socket_connect_timeout=1,
                    socket_timeout=1
                )
                self._redis_client.ping()
                logger.info("⚡ Redis cache connected")
            except:
                self._redis_client = None
                logger.warning("⚠️ Redis not available")
        
        # Circuit breaker for Groq
        self._groq_failures = 0
        self._groq_last_failure_time = 0
        self._groq_circuit_breaker_open = False
        
        # Request isolation state
        self._current_request_id: Optional[str] = None
        self._request_start_time: float = 0
        self._request_cache: Dict[str, Any] = {}
        self._recovery_attempts: int = 0
        self._groq_used: bool = False
        
        # ==========================================================
        # DASHBOARD ROUTING MATRIX - Full 18 Dashboards
        # ==========================================================
        
        self._dashboard_routing_matrix = {
            "dealer_dashboard": {
                "handler": self._route_dealer_dashboard,
                "requires": ["dealer_name", "dealer_code", "customer_code"],
                "follow_up": ["performance", "revenue", "pod", "dn", "ranking"],
                "drill_down": ["dealer_details", "dealer_timeline", "dealer_products"],
                "display_name": "Dealer Dashboard",
                "emoji": "🏪"
            },
            "warehouse_dashboard": {
                "handler": self._route_warehouse_dashboard,
                "requires": ["warehouse"],
                "follow_up": ["performance", "coverage", "revenue", "ranking"],
                "drill_down": ["warehouse_details", "warehouse_top_dealers"],
                "display_name": "Warehouse Dashboard",
                "emoji": "🏭"
            },
            "city_dashboard": {
                "handler": self._route_city_dashboard,
                "requires": ["city"],
                "follow_up": ["performance", "top_dealers", "revenue", "ranking"],
                "drill_down": ["city_details", "city_top_products"],
                "display_name": "City Dashboard",
                "emoji": "🏙️"
            },
            "product_dashboard": {
                "handler": self._route_product_dashboard,
                "requires": ["product_model", "material"],
                "follow_up": ["performance", "revenue", "ranking"],
                "drill_down": ["product_details", "product_trend"],
                "display_name": "Product Dashboard",
                "emoji": "📦"
            },
            "dn_dashboard": {
                "handler": self._route_dn_dashboard,
                "requires": ["dn_number"],
                "follow_up": ["status", "delivery", "pod", "pgi"],
                "drill_down": ["dn_details", "dn_timeline"],
                "display_name": "DN Dashboard",
                "emoji": "📄"
            },
            "pgi_dashboard": {
                "handler": self._route_pgi_dashboard,
                "requires": ["dn_number", "dealer_name"],
                "follow_up": ["status", "pending", "completed"],
                "drill_down": ["pgi_details", "pgi_timeline"],
                "display_name": "PGI Dashboard",
                "emoji": "📋"
            },
            "pod_dashboard": {
                "handler": self._route_pod_dashboard,
                "requires": ["dn_number", "dealer_name"],
                "follow_up": ["status", "pending", "aging", "compliance"],
                "drill_down": ["pod_details", "pod_timeline"],
                "display_name": "POD Dashboard",
                "emoji": "✅"
            },
            "delivery_dashboard": {
                "handler": self._route_delivery_dashboard,
                "requires": [],
                "follow_up": ["performance", "rate", "pending", "delayed"],
                "drill_down": ["delivery_details", "delivery_trend"],
                "display_name": "Delivery Dashboard",
                "emoji": "🚚"
            },
            "distance_dashboard": {
                "handler": self._route_distance_dashboard,
                "requires": ["dealer_name", "warehouse"],
                "follow_up": ["transit", "travel_time", "route"],
                "drill_down": ["distance_details", "route_analysis"],
                "display_name": "Distance Dashboard",
                "emoji": "📍"
            },
            "executive_dashboard": {
                "handler": self._route_executive_dashboard,
                "requires": [],
                "follow_up": ["summary", "insights", "kpi", "risks"],
                "drill_down": ["executive_details", "strategic_insights"],
                "display_name": "Executive Dashboard",
                "emoji": "👔"
            },
            "control_tower": {
                "handler": self._route_control_tower,
                "requires": [],
                "follow_up": ["alerts", "critical", "issues", "sla"],
                "drill_down": ["alert_details", "risk_analysis"],
                "display_name": "Control Tower Dashboard",
                "emoji": "🚨"
            },
            "dealer_ranking": {
                "handler": self._route_dealer_ranking,
                "requires": [],
                "follow_up": ["top", "bottom", "revenue", "delivery"],
                "drill_down": ["ranking_details", "dealer_compare"],
                "display_name": "Dealer Ranking",
                "emoji": "🏆"
            },
            "warehouse_ranking": {
                "handler": self._route_warehouse_ranking,
                "requires": [],
                "follow_up": ["top", "bottom", "revenue", "delivery"],
                "drill_down": ["ranking_details", "warehouse_compare"],
                "display_name": "Warehouse Ranking",
                "emoji": "🏆"
            },
            "product_ranking": {
                "handler": self._route_product_ranking,
                "requires": [],
                "follow_up": ["top", "best_selling", "revenue"],
                "drill_down": ["ranking_details", "product_compare"],
                "display_name": "Product Ranking",
                "emoji": "🏆"
            },
            "transporter_dashboard": {
                "handler": self._route_transporter_dashboard,
                "requires": ["transporter_name"],
                "follow_up": ["performance", "delivery", "rating"],
                "drill_down": ["transporter_details", "transporter_performance"],
                "display_name": "Transporter Dashboard",
                "emoji": "🚛"
            },
            "revenue_dashboard": {
                "handler": self._route_revenue_dashboard,
                "requires": [],
                "follow_up": ["summary", "trend", "by_dealer", "by_city"],
                "drill_down": ["revenue_details", "revenue_trend"],
                "display_name": "Revenue Dashboard",
                "emoji": "💰"
            },
            "inventory_dashboard": {
                "handler": self._route_inventory_dashboard,
                "requires": ["warehouse", "material"],
                "follow_up": ["stock", "status", "movement"],
                "drill_down": ["inventory_details", "inventory_status"],
                "display_name": "Inventory Dashboard",
                "emoji": "📦"
            },
            "forecast_dashboard": {
                "handler": self._route_forecast_dashboard,
                "requires": [],
                "follow_up": ["revenue", "units", "dns", "trend"],
                "drill_down": ["forecast_details", "forecast_explanation"],
                "display_name": "Forecast Dashboard",
                "emoji": "📊"
            }
        }
        
        # ==========================================================
        # METRICS
        # ==========================================================
        
        self.metrics = {
            "total_requests": 0,
            "fast_cache_hits": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_failures_avoided": 0,
            "response_times_ms": [],
            "intent_detection": {
                "dealer_dashboard": 0,
                "warehouse_dashboard": 0,
                "city_dashboard": 0,
                "product_dashboard": 0,
                "dn_dashboard": 0,
                "pgi_dashboard": 0,
                "pod_dashboard": 0,
                "delivery_dashboard": 0,
                "distance_dashboard": 0,
                "executive_dashboard": 0,
                "control_tower": 0,
                "dealer_ranking": 0,
                "warehouse_ranking": 0,
                "product_ranking": 0,
                "transporter_dashboard": 0,
                "revenue_dashboard": 0,
                "inventory_dashboard": 0,
                "forecast_dashboard": 0,
                "unknown": 0
            },
            "follow_up_queries": 0,
            "drill_down_queries": 0,
            "dealer_resolution": {
                "attempts": 0,
                "success": 0,
                "failure": 0,
                "rapidfuzz_hits": 0,
                "suggestions_shown": 0
            },
            "groq_uses": 0,
            "groq_fallbacks": 0,
            "errors": 0,
            "timeouts": 0
        }
        
        logger.info("=" * 70)
        logger.info("AI Router v21.0 - Master AI Router with Full Dashboards")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   RULES:")
        logger.info("   ✅ Analytics First - analytics_service.py")
        logger.info("   ✅ Groq Second - Only for specific intents")
        logger.info("   ✅ Database Truth Always")
        logger.info("   ✅ Never Crash")
        logger.info("   ✅ Always Fast")
        logger.info("   ✅ Always WhatsApp Safe")
        logger.info("")
        logger.info("   📊 18 DASHBOARDS SUPPORTED:")
        logger.info("      1. 🏪 Dealer Dashboard")
        logger.info("      2. 🏭 Warehouse Dashboard")
        logger.info("      3. 🏙️ City Dashboard")
        logger.info("      4. 📦 Product Dashboard")
        logger.info("      5. 📄 DN Dashboard")
        logger.info("      6. 📋 PGI Dashboard")
        logger.info("      7. ✅ POD Dashboard")
        logger.info("      8. 🚚 Delivery Dashboard")
        logger.info("      9. 📍 Distance Dashboard")
        logger.info("      10. 👔 Executive Dashboard")
        logger.info("      11. 🚨 Control Tower Dashboard")
        logger.info("      12. 🏆 Dealer Ranking")
        logger.info("      13. 🏆 Warehouse Ranking")
        logger.info("      14. 🏆 Product Ranking")
        logger.info("      15. 🚛 Transporter Dashboard")
        logger.info("      16. 💰 Revenue Dashboard")
        logger.info("      17. 📦 Inventory Dashboard")
        logger.info("      18. 📊 Forecast Dashboard")
        logger.info("")
        logger.info("   🔍 ENTITY RECOGNITION:")
        logger.info("      - Dealer Name | Dealer Code | Customer Code")
        logger.info("      - Warehouse | City | Material | Product Model")
        logger.info("      - DN Number | Sales Office | Division")
        logger.info("")
        logger.info("   💬 FOLLOW-UP SUPPORT:")
        logger.info("      - 'What is its POD?' → Uses last_dealer")
        logger.info("      - 'How many pending DN?' → Uses last_dealer")
        logger.info("      - 'Show me its revenue' → Uses last_dealer")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
    # ==========================================================
    # LAZY PROPERTIES
    # ==========================================================
    
    @property
    def query_service(self):
        if self._query_service is None:
            self._query_service = _get_ai_query_service()
        return self._query_service
    
    @property
    def analytics(self):
        if self._analytics is None:
            self._analytics, self._analytics_response = _get_analytics_service()
        return self._analytics
    
    @property
    def kpi(self):
        if self._kpi is None:
            self._kpi = _get_kpi_service()
        return self._kpi
    
    @property
    def groq(self):
        if self._groq is None:
            self._groq = _get_groq_service()
        return self._groq
    
    @property
    def schema(self):
        if self._schema is None:
            self._schema = _get_schema_service()
        return self._schema
    
    @property
    def whatsapp(self):
        if self._whatsapp is None:
            self._whatsapp = _get_whatsapp_service()
        return self._whatsapp
    
    # ==========================================================
    # ANALYTICS RESPONSE VALIDATION
    # ==========================================================
    
    def _validate_analytics_response(self, response: Any, service_name: str, req_id: str) -> bool:
        """Validate analytics response."""
        if response is None:
            logger.error(f"[{req_id}] AnalyticsResponse is None for {service_name}")
            return False
        
        if not hasattr(response, 'success'):
            logger.error(f"[{req_id}] AnalyticsResponse missing 'success' for {service_name}")
            return False
        
        if response.success is False:
            logger.error(f"[{req_id}] AnalyticsResponse success=False for {service_name}: {response.error}")
            return False
        
        if not hasattr(response, 'data'):
            logger.error(f"[{req_id}] AnalyticsResponse missing 'data' for {service_name}")
            return False
        
        return True
    
    # ==========================================================
    # ENTITY RECOGNITION
    # ==========================================================
    
    def _extract_entities(self, question: str) -> Dict[str, Optional[str]]:
        """Extract all entities from question using ENTITY_PATTERNS."""
        entities = {}
        for entity_type, pattern in ENTITY_PATTERNS.items():
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                entities[entity_type] = match.group(1).strip()
        return entities
    
    # ==========================================================
    # FOLLOW-UP QUESTION SUPPORT
    # ==========================================================
    
    def _handle_follow_up(self, question: str, context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle follow-up questions using context."""
        if not context or not context.last_intent:
            return None
        
        self.metrics["follow_up_queries"] += 1
        
        # Check for possessive references
        if "its" in question.lower() or "his" in question.lower() or "her" in question.lower():
            if context.last_dealer:
                # Replace "its" with dealer name
                resolved = question.replace("its", context.last_dealer).replace("Its", context.last_dealer)
                resolved = resolved.replace("his", context.last_dealer).replace("His", context.last_dealer)
                resolved = resolved.replace("her", context.last_dealer).replace("Her", context.last_dealer)
                logger.info(f"[{req_id}] Follow-up resolved: '{question}' → '{resolved}'")
                return resolved
        
        # Check for "what about" patterns
        if "what about" in question.lower() and context.last_entity:
            return f"{context.last_entity} {question}"
        
        # Check for drill-down patterns
        if "more details" in question.lower() or "drill down" in question.lower():
            return self._handle_drill_down(context, req_id)
        
        # Check for related dashboard navigation
        for dashboard_type, matrix in self._dashboard_routing_matrix.items():
            for follow_up in matrix.get("follow_up", []):
                if follow_up in question.lower():
                    return self._navigate_to_related_dashboard(context, dashboard_type, question, req_id)
        
        return None
    
    def _handle_drill_down(self, context: ConversationContext, req_id: str) -> Optional[str]:
        """Handle drill-down into current dashboard."""
        if not context or not context.last_intent:
            return None
        
        self.metrics["drill_down_queries"] += 1
        
        intent = context.last_intent
        matrix = self._dashboard_routing_matrix.get(intent)
        if not matrix:
            return None
        
        drill_down_options = matrix.get("drill_down", [])
        if not drill_down_options:
            return None
        
        # Return drill-down options
        options_text = "\n".join([f"   • {option.replace('_', ' ').title()}" for option in drill_down_options])
        return f"""📊 *{matrix.get('display_name', 'Dashboard')} - Drill Down Options*

*Choose an option:*
{options_text}

*What would you like to explore?* 🤖"""
    
    def _navigate_to_related_dashboard(self, context: ConversationContext, current_intent: str, question: str, req_id: str) -> Optional[str]:
        """Navigate to a related dashboard."""
        # Find the related dashboard
        for target_intent, target_matrix in self._dashboard_routing_matrix.items():
            if target_intent == current_intent:
                continue
            # Check if target dashboard is related
            for follow_up in target_matrix.get("follow_up", []):
                if follow_up in question.lower():
                    # Route to the target dashboard
                    entity = context.last_dealer or context.last_warehouse or context.last_city
                    if entity:
                        return f"{entity} {target_matrix.get('display_name', target_intent)}"
        
        return None
    
    # ==========================================================
    # INTENT DETECTION
    # ==========================================================
    
    def _detect_intent(self, question: str, context: Optional[ConversationContext] = None) -> Tuple[str, Optional[str]]:
        """Detect intent from user question with follow-up support."""
        question_lower = question.lower().strip()
        
        # Check special commands first
        if question_lower in SPECIAL_COMMANDS:
            command = SPECIAL_COMMANDS[question_lower]
            if command == "control_tower":
                self.metrics["intent_detection"]["control_tower"] += 1
                return "control_tower", None
            if command == "executive_dashboard":
                self.metrics["intent_detection"]["executive_dashboard"] += 1
                return "executive_dashboard", None
            if command == "help":
                return "help", None
        
        # Check for follow-up questions first
        if context and context.last_intent:
            follow_up_result = self._handle_follow_up(question, context, self._current_request_id or "unknown")
            if follow_up_result:
                # Process the resolved follow-up
                return self._detect_intent(follow_up_result, None)
        
        # Check for DN first (highest priority)
        if self._is_dn_query(question):
            self.metrics["intent_detection"]["dn_dashboard"] += 1
            return "dn_dashboard", self._normalize_dn(question)
        
        # Check each intent pattern
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    # Extract entity if present
                    entity = self._extract_entity(question, intent)
                    self.metrics["intent_detection"][intent] += 1
                    return intent, entity
        
        self.metrics["intent_detection"]["unknown"] += 1
        return "unknown", None
    
    # ==========================================================
    # ENTITY EXTRACTION
    # ==========================================================
    
    def _extract_entity(self, question: str, intent: str) -> Optional[str]:
        """Extract entity from question based on intent."""
        question_clean = question.strip()
        
        # Use entity patterns for extraction
        entities = self._extract_entities(question)
        
        # Map intent to entity type
        entity_mapping = {
            "dealer_dashboard": ["dealer_name", "dealer_code", "customer_code"],
            "warehouse_dashboard": ["warehouse"],
            "city_dashboard": ["city"],
            "product_dashboard": ["product_model", "material"],
            "dn_dashboard": ["dn_number"],
            "pgi_dashboard": ["dn_number", "dealer_name"],
            "pod_dashboard": ["dn_number", "dealer_name"],
            "transporter_dashboard": ["transporter_name"],
            "distance_dashboard": ["dealer_name", "warehouse"],
            "inventory_dashboard": ["warehouse", "material"]
        }
        
        for entity_type in entity_mapping.get(intent, []):
            if entities.get(entity_type):
                return entities[entity_type]
        
        # Fallback: extract from question
        if intent == "dealer_dashboard":
            # Remove common prefixes
            prefixes = ["show me", "tell me about", "get", "view", "display", 
                       "dealer", "customer", "for dealer", "for customer"]
            for prefix in prefixes:
                if question_clean.lower().startswith(prefix):
                    entity = question_clean[len(prefix):].strip()
                    if entity and len(entity) > 2:
                        return entity
            if len(question_clean) < 50:
                return question_clean
        
        # For warehouse queries
        elif intent == "warehouse_dashboard":
            prefixes = ["show me", "warehouse", "for warehouse"]
            for prefix in prefixes:
                if question_clean.lower().startswith(prefix):
                    entity = question_clean[len(prefix):].strip()
                    if entity and len(entity) > 2:
                        return entity
            if len(question_clean) < 50:
                return question_clean
        
        # For city queries
        elif intent == "city_dashboard":
            prefixes = ["show me", "city", "for city"]
            for prefix in prefixes:
                if question_clean.lower().startswith(prefix):
                    entity = question_clean[len(prefix):].strip()
                    if entity and len(entity) > 2:
                        return entity
            if len(question_clean) < 50:
                return question_clean
        
        return None
    
    # ==========================================================
    # SHOULD USE GROQ?
    # ==========================================================
    
    def _should_use_groq(self, question: str, intent: str) -> bool:
        """Determine if Groq should be used for this query."""
        question_lower = question.lower()
        
        # Never use Groq for these intents
        never_groq_intents = [
            "dealer_dashboard", "warehouse_dashboard", "city_dashboard",
            "product_dashboard", "dn_dashboard", "pgi_dashboard", "pod_dashboard",
            "delivery_dashboard", "distance_dashboard", "dealer_ranking",
            "warehouse_ranking", "product_ranking", "transporter_dashboard",
            "revenue_dashboard", "inventory_dashboard", "help"
        ]
        
        if intent in never_groq_intents:
            return False
        
        # Check Groq intent patterns
        for groq_intent, patterns in GROQ_INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    return True
        
        # If intent is forecast, use Groq for explanation
        if intent == "forecast_dashboard":
            return True
        
        # If intent is executive, use Groq for insights
        if intent == "executive_dashboard":
            return True
        
        # If intent is control tower, use Groq for insights
        if intent == "control_tower":
            return True
        
        # Default: use Groq for unknown intents
        if intent == "unknown":
            return True
        
        return False
    
    # ==========================================================
    # ULTRA-FAST DEALER RESOLUTION (RapidFuzz)
    # ==========================================================
    
    def _resolve_dealer_safe(self, dealer_input: str, req_id: str) -> Tuple[Optional[str], float, str]:
        """Ultra-fast dealer resolution using RapidFuzz (100x faster)."""
        self.metrics["dealer_resolution"]["attempts"] += 1
        
        if not dealer_input or not dealer_input.strip():
            return None, 0.0, "empty_input"
        
        # Check cache first
        cache_key = dealer_input.lower().strip()
        if cache_key in self.dealer_resolution_cache:
            resolved, confidence, timestamp = self.dealer_resolution_cache[cache_key]
            if resolved and time.time() - timestamp < 3600:
                return resolved, confidence, "cache_hit"
        
        dealer_clean = dealer_input.strip()
        
        # ==========================================================
        # RAPIDFUZZ STRATEGY (Ultra-fast - 100x faster)
        # ==========================================================
        
        if RAPIDFUZZ_AVAILABLE:
            try:
                # Get all dealers from analytics
                result = self.analytics.get_all_dealers_dashboard()
                if result and result.success:
                    dealers = result.data.get("dealers", [])
                    dealer_names = [d.get("dealer_name", "") for d in dealers if d.get("dealer_name")]
                    
                    if dealer_names:
                        # RapidFuzz - 100x faster than difflib
                        matches = process.extract(
                            dealer_clean,
                            dealer_names,
                            scorer=fuzz.ratio,
                            limit=3
                        )
                        
                        if matches:
                            # If exact match or very high score (>90)
                            if matches[0][1] >= 90:
                                resolved = matches[0][0]
                                confidence = matches[0][1] / 100
                                self.metrics["dealer_resolution"]["success"] += 1
                                self.metrics["dealer_resolution"]["rapidfuzz_hits"] += 1
                                logger.info(f"[{req_id}] ✅ RapidFuzz: '{resolved}' (score: {confidence:.2f})")
                                self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                                return resolved, confidence, "rapidfuzz_exact"
                            
                            # If good match (70-90), return with suggestions
                            elif matches[0][1] >= 70:
                                resolved = matches[0][0]
                                confidence = matches[0][1] / 100
                                self.metrics["dealer_resolution"]["success"] += 1
                                self.metrics["dealer_resolution"]["rapidfuzz_hits"] += 1
                                
                                # Store suggestions for later
                                suggestions = [m[0] for m in matches if m[1] >= 70]
                                if len(suggestions) > 1:
                                    self._suggestion_cache[cache_key] = suggestions
                                
                                logger.info(f"[{req_id}] ✅ RapidFuzz (partial): '{resolved}' (score: {confidence:.2f})")
                                self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                                return resolved, confidence, "rapidfuzz_partial"
            except Exception as e:
                logger.debug(f"RapidFuzz failed: {e}")
        
        # ==========================================================
        # FALLBACK: Schema Service Resolution
        # ==========================================================
        
        try:
            resolved = self.schema.resolve_dealer(dealer_clean)
            if resolved:
                confidence = 0.95
                self.metrics["dealer_resolution"]["success"] += 1
                self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                return resolved, confidence, "schema_match"
        except:
            pass
        
        # All strategies failed
        self.metrics["dealer_resolution"]["failure"] += 1
        return None, 0.0, "all_failed"
    
    def _get_dealer_suggestions(self, dealer_input: str, req_id: str) -> List[str]:
        """Get dealer suggestions using RapidFuzz."""
        try:
            if RAPIDFUZZ_AVAILABLE:
                result = self.analytics.get_all_dealers_dashboard()
                if result and result.success:
                    dealers = result.data.get("dealers", [])
                    dealer_names = [d.get("dealer_name", "") for d in dealers if d.get("dealer_name")]
                    
                    if dealer_names:
                        matches = process.extract(
                            dealer_input,
                            dealer_names,
                            scorer=fuzz.ratio,
                            limit=DEALER_SUGGESTION_LIMIT
                        )
                        
                        suggestions = [m[0] for m in matches if m[1] >= 40]
                        if suggestions:
                            self.metrics["dealer_resolution"]["suggestions_shown"] += 1
                            return suggestions
            return []
        except:
            return []
    
    # ==========================================================
    # DN NORMALIZATION & DETECTION
    # ==========================================================
    
    def _normalize_dn(self, text: str) -> str:
        """Normalize DN number by removing non-digits."""
        return re.sub(r"\D", "", text.strip())
    
    def _is_dn_query(self, question: str) -> bool:
        """Check if query is a valid DN number."""
        digits = self._normalize_dn(question)
        return 8 <= len(digits) <= 12
    
    def _is_valid_dn_format(self, dn: str) -> bool:
        """Check if DN matches valid formats."""
        cleaned = self._normalize_dn(dn)
        if not cleaned or len(cleaned) < 8 or len(cleaned) > 12:
            return False
        
        patterns = [
            r'^\d{8,12}$',  # 8-12 digits
            r'^\d{3}-\d{3}-\d{3}$',  # 123-456-789
            r'^\d{4}-\d{4}$',  # 1234-5678
            r'^\d{2}-\d{4}-\d{4}$',  # 12-3456-7890
        ]
        
        for pattern in patterns:
            if re.match(pattern, dn.strip()):
                return True
        
        return False
    
    # ==========================================================
    # CACHE MANAGEMENT
    # ==========================================================
    
    def _get_cached_response(self, question: str, phone_number: Optional[str]) -> Optional[str]:
        """Get response from cache."""
        cache_key = self._generate_cache_key(question, phone_number)
        
        # Check failure cache first
        if cache_key in self.failure_cache:
            self.metrics["cache_failures_avoided"] += 1
            return None
        
        # Check fast cache
        if cache_key in self.fast_cache:
            self.metrics["fast_cache_hits"] += 1
            return self.fast_cache[cache_key]
        
        # Check response cache
        if cache_key in self.response_cache:
            self.metrics["cache_hits"] += 1
            return self.response_cache[cache_key]
        
        return None
    
    def _cache_response(self, question: str, phone_number: Optional[str], response: str, success: bool = True):
        """Cache response."""
        cache_key = self._generate_cache_key(question, phone_number)
        
        if success and response and len(response) > 10 and not response.startswith("❌"):
            self.fast_cache[cache_key] = response
            self.response_cache[cache_key] = response
            
            # Cache in Redis if available
            if self._redis_client:
                try:
                    self._redis_client.setex(f"resp:{cache_key}", CACHE_TTL_SECONDS, response)
                except:
                    pass
        else:
            self.failure_cache[cache_key] = time.time()
    
    def _generate_cache_key(self, question: str, phone_number: Optional[str]) -> str:
        key = question.lower().strip()
        if phone_number:
            key = f"{phone_number}:{key}"
        return hashlib.md5(key.encode()).hexdigest()
    
    # ==========================================================
    # GROQ CIRCUIT BREAKER
    # ==========================================================
    
    def _is_groq_circuit_breaker_open(self) -> bool:
        if not self._groq_circuit_breaker_open:
            return False
        
        if time.time() - self._groq_last_failure_time > 60:
            self._groq_circuit_breaker_open = False
            self._groq_failures = 0
            logger.info("Groq circuit breaker: CLOSED")
            return False
        
        return True
    
    def _record_groq_success(self):
        self._groq_failures = 0
        self._groq_circuit_breaker_open = False
    
    def _record_groq_failure(self):
        self._groq_failures += 1
        self._groq_last_failure_time = time.time()
        if self._groq_failures >= 3:
            self._groq_circuit_breaker_open = True
            logger.error("Groq circuit breaker: OPEN (3 consecutive failures)")
    
    def _is_groq_available(self) -> bool:
        if self._is_groq_circuit_breaker_open():
            return False
        return hasattr(self.groq, 'is_available') and self.groq.is_available
    
    # ==========================================================
    # CONTEXT MANAGEMENT
    # ==========================================================
    
    def _load_context(self, phone_number: Optional[str]) -> Optional[ConversationContext]:
        if not phone_number:
            return None
        
        if phone_number not in self.conversation_cache:
            self.conversation_cache[phone_number] = ConversationContext(phone_number=phone_number)
        
        context = self.conversation_cache[phone_number]
        if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
            context = ConversationContext(phone_number=phone_number)
            self.conversation_cache[phone_number] = context
        
        return context
    
    def _update_context(
        self,
        phone_number: Optional[str],
        intent: str,
        entity_type: str,
        entity: str,
        req_id: str,
        response: str = "",
        success: bool = True
    ):
        if not phone_number or not success:
            return
        
        context = self._load_context(phone_number)
        if not context:
            return
        
        context.last_intent = intent
        context.last_question = entity
        context.last_dashboard = intent
        context.confidence = 0.9
        
        if entity_type == "dealer":
            context.last_dealer = entity
            context.last_entity = entity
        elif entity_type == "warehouse":
            context.last_warehouse = entity
            context.last_entity = entity
        elif entity_type == "city":
            context.last_city = entity
            context.last_entity = entity
        elif entity_type == "dn":
            context.last_dn = entity
            context.last_entity = entity
        elif entity_type == "product":
            context.last_product = entity
            context.last_entity = entity
        
        if response:
            context.last_response = response[:200]
        context.message_count += 1
        context.last_updated = time.time()
        context.is_valid = True
    
    # ==========================================================
    # MAIN ENTRY POINT
    # ==========================================================
    
    def process_whatsapp_query(
        self,
        question: str,
        session_factory: Optional[Callable[[], Session]] = None,
        phone_number: Optional[str] = None,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> str:
        start_time = time.time()
        req_id = request_id or str(uuid.uuid4())[:8]
        self._current_request_id = req_id
        self.metrics["total_requests"] += 1
        
        logger.bind(request_id=req_id).info(f"📥 Processing: {question[:100]}")
        
        try:
            # Check cache first
            cached = self._get_cached_response(question, phone_number)
            if cached:
                duration_ms = int((time.time() - start_time) * 1000)
                logger.info(f"[{req_id}] ✅ Cache hit: {duration_ms}ms")
                return cached
            
            # Process with timeout
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self._process_sync,
                    question,
                    phone_number,
                    req_id
                )
                try:
                    response = future.result(timeout=25)
                    duration_ms = int((time.time() - start_time) * 1000)
                    self.metrics["response_times_ms"].append(duration_ms)
                    
                    # Keep only last 1000
                    if len(self.metrics["response_times_ms"]) > 1000:
                        self.metrics["response_times_ms"] = self.metrics["response_times_ms"][-1000:]
                    
                    logger.info(f"[{req_id}] ✅ Done: {duration_ms}ms")
                    return response
                    
                except concurrent.futures.TimeoutError:
                    self.metrics["timeouts"] += 1
                    logger.error(f"[{req_id}] Request timed out")
                    return self._get_timeout_response(req_id)
                    
        except Exception as e:
            self.metrics["errors"] += 1
            logger.exception(f"[{req_id}] ERROR: {e}")
            return self._get_error_response(e, req_id)
    
    # ==========================================================
    # SYNC PROCESSING (MAIN ROUTER LOGIC)
    # ==========================================================
    
    def _process_sync(self, question: str, phone_number: Optional[str], req_id: str) -> str:
        """Main sync processing - THE AI ROUTER."""
        
        # Load context
        context = self._load_context(phone_number)
        question_clean = question.strip()
        
        # ==========================================================
        # STEP 1: DETECT INTENT (with follow-up support)
        # ==========================================================
        
        intent, entity = self._detect_intent(question_clean, context)
        logger.info(f"[{req_id}] 🎯 Intent: {intent} | Entity: {entity}")
        
        # ==========================================================
        # STEP 2: HANDLE SPECIAL COMMANDS
        # ==========================================================
        
        if intent == "help":
            response = self._get_help_message()
            self._cache_response(question, phone_number, response, True)
            return response
        
        # ==========================================================
        # STEP 3: ROUTE TO APPROPRIATE DASHBOARD
        # ==========================================================
        
        result = self._route_to_dashboard(intent, entity, context, req_id)
        if result:
            self._cache_response(question, phone_number, result, True)
            self._update_context(phone_number, intent, self._get_entity_type(intent), entity, req_id, result, True)
            return result
        
        # ==========================================================
        # STEP 4: UNKNOWN INTENT - Try Groq or Help
        # ==========================================================
        
        # Try Groq first (if applicable)
        if self._should_use_groq(question_clean, intent) and self._is_groq_available():
            result = self._execute_groq_safe(question_clean, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                return result
        
        # Fallback to help
        return self._get_help_message()
    
    def _get_entity_type(self, intent: str) -> str:
        """Get entity type based on intent."""
        entity_mapping = {
            "dealer_dashboard": "dealer",
            "warehouse_dashboard": "warehouse",
            "city_dashboard": "city",
            "product_dashboard": "product",
            "dn_dashboard": "dn",
            "pgi_dashboard": "dn",
            "pod_dashboard": "dn",
            "distance_dashboard": "dealer",
        }
        return entity_mapping.get(intent, "unknown")
    
    def _route_to_dashboard(self, intent: str, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to the appropriate dashboard based on intent."""
        matrix = self._dashboard_routing_matrix.get(intent)
        if not matrix:
            return None
        
        handler = matrix.get("handler")
        if not handler:
            return None
        
        # Check if entity is required
        required = matrix.get("requires", [])
        if required and not entity:
            # Try to use context
            if context:
                for req in required:
                    if req == "dealer_name" and context.last_dealer:
                        entity = context.last_dealer
                        break
                    elif req == "warehouse" and context.last_warehouse:
                        entity = context.last_warehouse
                        break
                    elif req == "city" and context.last_city:
                        entity = context.last_city
                        break
                    elif req == "dn_number" and context.last_dn:
                        entity = context.last_dn
                        break
            
            if not entity:
                return self._get_missing_entity_message(intent, matrix)
        
        # Call the handler
        try:
            return handler(entity, context, req_id)
        except Exception as e:
            logger.error(f"[{req_id}] Handler error for {intent}: {e}")
            return f"⚠️ Unable to load {matrix.get('display_name', intent)}. Please try again."
    
    def _get_missing_entity_message(self, intent: str, matrix: Dict) -> str:
        """Get message when entity is missing."""
        display_name = matrix.get("display_name", intent)
        required = matrix.get("requires", [])
        required_text = ", ".join([r.replace("_", " ").title() for r in required])
        
        return f"""❌ To view {display_name}, please specify: {required_text}

📋 *Examples:*
• "Show dealer ZQ Electronics"
• "ZQ Electronics"
• "Lahore warehouse"

*What would you like to know?* 🤖"""
    
    # ==========================================================
    # DASHBOARD ROUTERS - All 18 Dashboards
    # ==========================================================
    
    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Dealer Dashboard."""
        dealer_name = entity
        
        if not dealer_name and context and context.last_dealer:
            dealer_name = context.last_dealer
            logger.info(f"[{req_id}] Using context dealer: {dealer_name}")
        
        if not dealer_name:
            return "❌ Please specify a dealer name.\n\nExample: 'Show dealer ZQ Electronics'"
        
        # Resolve dealer with RapidFuzz
        resolved, confidence, strategy = self._resolve_dealer_safe(dealer_name, req_id)
        
        if not resolved:
            suggestions = self._get_dealer_suggestions(dealer_name, req_id)
            if suggestions:
                suggestion_text = "\n".join([f"   • {s}" for s in suggestions[:3]])
                return f"""❌ Dealer '{dealer_name}' not found.

💡 *Did You Mean?*
{suggestion_text}

📋 *Try these commands:*
• Enter 8-12 digit DN number
• Type "help" for full menu

*What would you like to know?* 🤖"""
            return f"❌ Dealer '{dealer_name}' not found. Please try again or type 'help'."
        
        # Get analytics
        result = self.analytics.get_dealer_dashboard(resolved)
        
        if not self._validate_analytics_response(result, "dealer_dashboard", req_id):
            return f"❌ Unable to retrieve dashboard for '{resolved}'."
        
        return self._format_dealer_dashboard(result, resolved, req_id)
    
    def _route_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Warehouse Dashboard."""
        warehouse_name = entity
        
        if not warehouse_name and context and context.last_warehouse:
            warehouse_name = context.last_warehouse
        
        if not warehouse_name:
            return "❌ Please specify a warehouse name.\n\nExample: 'Show Lahore warehouse'"
        
        warehouse_result = self.schema.resolve_warehouse(warehouse_name)
        if not warehouse_result:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        result = self.analytics.get_warehouse_dashboard(warehouse_result)
        
        if not self._validate_analytics_response(result, "warehouse_dashboard", req_id):
            return f"❌ Unable to retrieve warehouse dashboard for '{warehouse_result}'."
        
        return self._format_warehouse_dashboard(result, warehouse_result, req_id)
    
    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to City Dashboard."""
        city_name = entity
        
        if not city_name and context and context.last_city:
            city_name = context.last_city
        
        if not city_name:
            return "❌ Please specify a city name.\n\nExample: 'Show Lahore'"
        
        city_result = self.schema.resolve_city(city_name)
        if not city_result:
            return f"❌ City '{city_name}' not found."
        
        result = self.analytics.get_city_dashboard(city_result)
        
        if not self._validate_analytics_response(result, "city_dashboard", req_id):
            return f"❌ Unable to retrieve city dashboard for '{city_result}'."
        
        return self._format_city_dashboard(result, city_result, req_id)
    
    def _route_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Product Dashboard."""
        # Get top products from analytics
        result = self.analytics.get_all_dealers_dashboard()
        if not self._validate_analytics_response(result, "product_dashboard", req_id):
            return "❌ Unable to retrieve product data."
        
        return self._format_product_dashboard(result, req_id)
    
    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to DN Dashboard."""
        dn_number = entity
        
        if not dn_number and context and context.last_dn:
            dn_number = context.last_dn
        
        if not dn_number:
            return "❌ Please provide a DN number (8-12 digits)."
        
        cleaned = self._normalize_dn(dn_number)
        
        if not cleaned or len(cleaned) < 8 or len(cleaned) > 12:
            return f"""❌ Invalid DN number: '{dn_number}'

💡 *DN numbers must be 8-12 digits.*

📋 *Try these:*
• Enter a valid DN number (e.g., 1234567890)
• Type "help" for menu
• Ask about a dealer name

*What would you like to know?* 🤖"""
        
        result = self.analytics.get_dn_analytics(cleaned)
        
        if not self._validate_analytics_response(result, "dn_dashboard", req_id):
            return f"""❌ DN {cleaned} not found.

💡 *Please verify the number and try again.*

📋 *Try these:*
• Enter 8-12 digit DN number
• Type "help" for full menu
• Ask about a dealer name

*What would you like to know?* 🤖"""
        
        return self._format_dn_dashboard(result, req_id)
    
    def _route_pgi_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to PGI Dashboard."""
        # PGI dashboard shows PGI status, pending PGI, etc.
        result = self.analytics.get_delivery_performance()
        if not self._validate_analytics_response(result, "pgi_dashboard", req_id):
            return "❌ Unable to retrieve PGI data."
        
        return self._format_pgi_dashboard(result, req_id)
    
    def _route_pod_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to POD Dashboard."""
        result = self.analytics.get_root_cause_insights()
        if not self._validate_analytics_response(result, "pod_dashboard", req_id):
            return "❌ Unable to retrieve POD data."
        
        return self._format_pod_dashboard(result, req_id)
    
    def _route_delivery_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Delivery Dashboard."""
        result = self.analytics.get_delivery_performance()
        if not self._validate_analytics_response(result, "delivery_dashboard", req_id):
            return "❌ Unable to retrieve delivery data."
        
        return self._format_delivery_dashboard(result, req_id)
    
    def _route_distance_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Distance Dashboard."""
        if not context or not context.last_dealer or not context.last_warehouse:
            return "📍 Please specify a dealer and warehouse for distance analysis.\n\nExample: 'Show distance for ZQ Electronics from Lahore warehouse'"
        
        distance, transit_days, status = self._calculate_distance_and_transit(
            context.last_warehouse, context.last_dealer, req_id
        )
        
        if status == "unknown":
            return f"📍 Unable to calculate distance between '{context.last_dealer}' and '{context.last_warehouse}'."
        
        return self._format_distance_dashboard(context.last_dealer, context.last_warehouse, distance, transit_days, status, req_id)
    
    def _route_executive_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Executive Dashboard."""
        result = self.analytics.get_executive_summary()
        if not self._validate_analytics_response(result, "executive_dashboard", req_id):
            return "❌ Unable to retrieve executive data."
        
        return self._format_executive_dashboard(result, req_id)
    
    def _route_control_tower(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Control Tower Dashboard."""
        result = self.analytics.get_control_tower_alerts()
        if not self._validate_analytics_response(result, "control_tower", req_id):
            return "❌ Unable to retrieve control tower data."
        
        return self._format_control_tower_dashboard(result, req_id)
    
    def _route_dealer_ranking(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Dealer Ranking."""
        result = self.analytics.get_dealer_ranking(limit=10, top=True)
        if not self._validate_analytics_response(result, "dealer_ranking", req_id):
            return "❌ Unable to retrieve dealer ranking."
        
        return self._format_dealer_ranking(result, req_id)
    
    def _route_warehouse_ranking(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Warehouse Ranking."""
        result = self.analytics.get_warehouse_ranking(limit=10, top=True)
        if not self._validate_analytics_response(result, "warehouse_ranking", req_id):
            return "❌ Unable to retrieve warehouse ranking."
        
        return self._format_warehouse_ranking(result, req_id)
    
    def _route_product_ranking(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Product Ranking."""
        result = self.analytics.get_all_dealers_dashboard()
        if not self._validate_analytics_response(result, "product_ranking", req_id):
            return "❌ Unable to retrieve product ranking."
        
        return self._format_product_ranking(result, req_id)
    
    def _route_transporter_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Transporter Dashboard."""
        # Transporter dashboard - currently using delivery performance as proxy
        result = self.analytics.get_delivery_performance()
        if not self._validate_analytics_response(result, "transporter_dashboard", req_id):
            return "❌ Unable to retrieve transporter data."
        
        return self._format_transporter_dashboard(result, req_id)
    
    def _route_revenue_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Revenue Dashboard."""
        result = self.analytics.get_all_dealers_dashboard()
        if not self._validate_analytics_response(result, "revenue_dashboard", req_id):
            return "❌ Unable to retrieve revenue data."
        
        return self._format_revenue_dashboard(result, req_id)
    
    def _route_inventory_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Inventory Dashboard."""
        # Inventory dashboard - using delivery data as proxy
        result = self.analytics.get_delivery_performance()
        if not self._validate_analytics_response(result, "inventory_dashboard", req_id):
            return "❌ Unable to retrieve inventory data."
        
        return self._format_inventory_dashboard(result, req_id)
    
    def _route_forecast_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Forecast Dashboard."""
        result = self.analytics.get_executive_summary()
        if not self._validate_analytics_response(result, "forecast_dashboard", req_id):
            return "❌ Unable to retrieve forecast data."
        
        return self._format_forecast_dashboard(result, req_id)
    
    # ==========================================================
    # DISTANCE ENGINE
    # ==========================================================
    
    def _calculate_haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points using Haversine formula."""
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        
        a = math.sin(delta_phi / 2) ** 2 + \
            math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return EARTH_RADIUS_KM * c
    
    def _calculate_transit_days(self, distance_km: float) -> int:
        """Calculate expected transit days based on distance."""
        if distance_km <= 0:
            return 1
        elif distance_km <= 50:
            return 1
        elif distance_km <= 150:
            return 2
        elif distance_km <= 300:
            return 3
        elif distance_km <= 500:
            return 4
        elif distance_km <= 800:
            return 5
        else:
            return 7
    
    def _calculate_distance_and_transit(self, warehouse_name: str, dealer_name: str, req_id: str) -> Tuple[float, int, str]:
        """Calculate distance and transit days between warehouse and dealer."""
        try:
            dealer_result = self.analytics.get_dealer_dashboard(dealer_name)
            if dealer_result and dealer_result.success:
                dealer_data = dealer_result.data or {}
                profile = dealer_data.get("profile", {})
                dealer_city = profile.get("city", "").lower()
                warehouse_city = warehouse_name.lower().strip()
                
                if dealer_city and dealer_city == warehouse_city:
                    return 0.0, 1, "same_city"
        except:
            pass
        
        warehouse_coords = {
            "lahore": (31.5204, 74.3587),
            "karachi": (24.8607, 67.0011),
            "rawalpindi": (33.5651, 73.0169),
            "faisalabad": (31.4504, 73.1350),
            "multan": (30.1575, 71.5249),
            "hyderabad": (25.3960, 68.3578),
            "peshawar": (34.0151, 71.5249),
            "quetta": (30.1798, 66.9750),
            "islamabad": (33.6844, 73.0479),
            "gujranwala": (32.1877, 74.1945),
            "sialkot": (32.4945, 74.5227),
        }
        
        wh_coords = warehouse_coords.get(warehouse_name.lower())
        if not wh_coords:
            return 0.0, 0, "unknown"
        
        try:
            dealer_result = self.analytics.get_dealer_dashboard(dealer_name)
            if dealer_result and dealer_result.success:
                data = dealer_result.data or {}
                profile = data.get("profile", {})
                lat = profile.get("latitude")
                lon = profile.get("longitude")
                if lat is not None and lon is not None:
                    distance = self._calculate_haversine_distance(
                        wh_coords[0], wh_coords[1],
                        float(lat), float(lon)
                    )
                    transit_days = self._calculate_transit_days(distance)
                    return distance, transit_days, "calculated"
        except:
            pass
        
        return 0.0, 0, "unknown"
    
    # ==========================================================
    # GROQ EXECUTION
    # ==========================================================
    
    def _execute_groq_safe(self, question: str, context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Execute Groq for specific intents."""
        if not self._is_groq_available():
            return None
        
        try:
            logger.info(f"[{req_id}] 🤖 Using Groq for: {question[:50]}...")
            self.metrics["groq_uses"] += 1
            
            context_data = {}
            if context:
                context_data = context.to_dict()
            
            if any(kw in question.lower() for kw in GROQ_INTENT_PATTERNS["root_cause"]):
                return self._execute_root_cause_groq(question, context_data, req_id)
            
            if any(kw in question.lower() for kw in GROQ_INTENT_PATTERNS["recommendation"]):
                return self._execute_recommendation_groq(question, context_data, req_id)
            
            if any(kw in question.lower() for kw in GROQ_INTENT_PATTERNS["executive"]):
                return self._execute_executive_groq(question, context_data, req_id)
            
            if any(kw in question.lower() for kw in GROQ_INTENT_PATTERNS["kpi_explain"]):
                return self._execute_kpi_explanation_groq(question, context_data, req_id)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, question, context_data)
                try:
                    response = future.result(timeout=GROQ_TIMEOUT_SECONDS)
                    if response and len(response) > 10:
                        self._record_groq_success()
                        self.metrics["groq_fallbacks"] += 1
                        return f"💡 *AI Intelligence:*\n\n{response}"
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{req_id}] Groq timeout")
                    self._record_groq_failure()
            
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] Groq failed: {e}")
            self._record_groq_failure()
            return None
    
    def _execute_root_cause_groq(self, question: str, context: Dict, req_id: str) -> Optional[str]:
        """Execute Groq for root cause analysis."""
        try:
            result = self.analytics.get_root_cause_insights()
            analytics_data = result.data if result and result.success else {}
            
            prompt = f"""As Haier Pakistan's AI Logistics Control Tower, perform root cause analysis.

Question: {question}

Analytics Data: {analytics_data}

Provide:
1. Root Cause - What is the primary cause?
2. Impact - What is the business impact?
3. Risk - What is the risk level?
4. Recommendation - What should management do?

Keep it concise and actionable."""
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, prompt, context)
                try:
                    response = future.result(timeout=GROQ_TIMEOUT_SECONDS)
                    if response and len(response) > 20:
                        self._record_groq_success()
                        self.metrics["groq_fallbacks"] += 1
                        return f"🔍 *Root Cause Analysis*\n\n{response}"
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{req_id}] Root cause Groq timeout")
                    self._record_groq_failure()
            
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] Root cause Groq failed: {e}")
            return None
    
    def _execute_recommendation_groq(self, question: str, context: Dict, req_id: str) -> Optional[str]:
        """Execute Groq for recommendations."""
        try:
            result = self.analytics.get_executive_summary()
            analytics_data = result.data if result and result.success else {}
            
            prompt = f"""As Haier Pakistan's AI Logistics Control Tower, provide recommendations.

Question: {question}

Analytics Data: {analytics_data}

Provide:
1. Key Insights - What's most important?
2. Recommendations - What should be done?
3. Priority - What's most urgent?

Keep it concise and actionable."""
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, prompt, context)
                try:
                    response = future.result(timeout=GROQ_TIMEOUT_SECONDS)
                    if response and len(response) > 20:
                        self._record_groq_success()
                        self.metrics["groq_fallbacks"] += 1
                        return f"🎯 *Recommendations*\n\n{response}"
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{req_id}] Recommendation Groq timeout")
                    self._record_groq_failure()
            
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] Recommendation Groq failed: {e}")
            return None
    
    def _execute_executive_groq(self, question: str, context: Dict, req_id: str) -> Optional[str]:
        """Execute Groq for executive insights."""
        try:
            result = self.analytics.get_executive_summary()
            analytics_data = result.data if result and result.success else {}
            
            prompt = f"""As Haier Pakistan's Chief Logistics Officer, provide executive intelligence.

Question: {question}

Analytics Data: {analytics_data}

Provide:
1. Executive Summary - One paragraph overview
2. Critical Issues - Top 3 challenges
3. Strategic Recommendations - Actionable items
4. Risk Assessment - Key risks

Keep it concise but comprehensive."""
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, prompt, context)
                try:
                    response = future.result(timeout=GROQ_TIMEOUT_SECONDS)
                    if response and len(response) > 20:
                        self._record_groq_success()
                        self.metrics["groq_fallbacks"] += 1
                        return f"👔 *Executive Intelligence*\n\n{response}"
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{req_id}] Executive Groq timeout")
                    self._record_groq_failure()
            
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] Executive Groq failed: {e}")
            return None
    
    def _execute_kpi_explanation_groq(self, question: str, context: Dict, req_id: str) -> Optional[str]:
        """Execute Groq for KPI explanation."""
        try:
            prompt = f"""As Haier Pakistan's AI Logistics Control Tower, explain the following KPI.

Question: {question}

Provide:
1. What is this KPI?
2. How is it calculated?
3. Why is it important?
4. What is the target?
5. How to improve it?

Keep it simple and easy to understand."""
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, prompt, context)
                try:
                    response = future.result(timeout=GROQ_TIMEOUT_SECONDS)
                    if response and len(response) > 20:
                        self._record_groq_success()
                        self.metrics["groq_fallbacks"] += 1
                        return f"📊 *KPI Explanation*\n\n{response}"
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{req_id}] KPI explanation Groq timeout")
                    self._record_groq_failure()
            
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] KPI explanation Groq failed: {e}")
            return None
    
    # ==========================================================
    # NEW FORMATTERS - Full Dashboard Set
    # ==========================================================
    
    def _format_pgi_dashboard(self, data, req_id: str) -> str:
        """Format PGI dashboard for WhatsApp."""
        try:
            metrics = data.data.get("metrics", {})
            
            lines = [
                "📋 *PGI DASHBOARD*",
                "",
                f"Total DNs: {metrics.get('total_dns', 0):,}",
                f"PGI Completed: {metrics.get('delivered', 0):,}",
                f"PGI Pending: {metrics.get('pending_pgi', 0):,}",
                f"PGI Rate: {metrics.get('pgi_rate', 0):.1f}%",
                "",
                f"Avg Processing: {metrics.get('avg_processing_days', 0):.1f} days",
                "",
                "💡 *PGI Status:*",
                f"{'✅ Good' if metrics.get('pgi_rate', 0) >= 90 else '⚠️ Needs Attention'}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] PGI format error: {e}")
            return "❌ Unable to format PGI dashboard"
    
    def _format_control_tower_dashboard(self, data, req_id: str) -> str:
        """Format Control Tower dashboard for WhatsApp."""
        try:
            d = data.data or {}
            alerts = d.get("alerts", [])
            critical_count = d.get("critical_count", 0)
            high_count = d.get("high_count", 0)
            
            lines = [
                "🚨 *LOGISTICS CONTROL TOWER*",
                "",
                f"Critical Alerts: {critical_count}",
                f"High Priority: {high_count}",
                "",
                f"Pending PODs: {d.get('pending_pod', 0)}",
                f"Delayed Deliveries: {d.get('delayed_deliveries', 0)}",
                "",
                "📈 *SLA Compliance*",
                f"Delivery SLA: {d.get('delivery_sla', 0):.1f}%",
                f"POD SLA: {d.get('pod_sla', 0):.1f}%"
            ]
            
            if d.get("high_risk_areas"):
                lines.append("")
                lines.append("🔴 *High Risk Areas*")
                for area in d.get("high_risk_areas", [])[:3]:
                    lines.append(f"   • {area}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Control tower format error: {e}")
            return "🚨 Unable to format control tower"
    
    def _format_dealer_ranking(self, data, req_id: str) -> str:
        """Format Dealer Ranking for WhatsApp."""
        try:
            dealers = data.data.get("dealers", [])
            
            lines = [
                "🏆 *DEALER RANKING*",
                "",
                "Top Dealers by Revenue:"
            ]
            
            for i, dealer in enumerate(dealers[:10], 1):
                name = dealer.get("dealer_name", "Unknown")
                revenue = dealer.get("total_revenue", 0)
                delivery_rate = dealer.get("delivery_rate", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   PKR {revenue:,.0f} | Delivery: {delivery_rate:.1f}%")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Dealer ranking format error: {e}")
            return "❌ Unable to format dealer ranking"
    
    def _format_warehouse_ranking(self, data, req_id: str) -> str:
        """Format Warehouse Ranking for WhatsApp."""
        try:
            warehouses = data.data.get("warehouses", [])
            
            lines = [
                "🏆 *WAREHOUSE RANKING*",
                "",
                "Top Warehouses by Revenue:"
            ]
            
            for i, warehouse in enumerate(warehouses[:10], 1):
                name = warehouse.get("warehouse", "Unknown")
                revenue = warehouse.get("total_revenue", 0)
                dealers = warehouse.get("total_dealers", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   PKR {revenue:,.0f} | Dealers: {dealers}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Warehouse ranking format error: {e}")
            return "❌ Unable to format warehouse ranking"
    
    def _format_product_ranking(self, data, req_id: str) -> str:
        """Format Product Ranking for WhatsApp."""
        try:
            dealers = data.data.get("dealers", [])
            
            lines = [
                "🏆 *PRODUCT RANKING*",
                "",
                "Top Products by Revenue:"
            ]
            
            # Aggregate products from dealers
            products = {}
            for dealer in dealers[:20]:
                name = dealer.get("dealer_name", "Unknown")
                revenue = dealer.get("total_revenue", 0)
                if revenue > 0:
                    # Use dealer name as product for now
                    products[name] = revenue
            
            sorted_products = sorted(products.items(), key=lambda x: x[1], reverse=True)
            
            for i, (name, revenue) in enumerate(sorted_products[:10], 1):
                lines.append(f"{i}. {name}")
                lines.append(f"   PKR {revenue:,.0f}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Product ranking format error: {e}")
            return "❌ Unable to format product ranking"
    
    def _format_transporter_dashboard(self, data, req_id: str) -> str:
        """Format Transporter Dashboard for WhatsApp."""
        try:
            metrics = data.data.get("metrics", {})
            
            lines = [
                "🚛 *TRANSPORTER DASHBOARD*",
                "",
                f"Total Deliveries: {metrics.get('total_dns', 0):,}",
                f"Completed: {metrics.get('delivered', 0):,}",
                f"In Transit: {metrics.get('in_transit', 0):,}",
                f"Delivery Rate: {metrics.get('delivery_rate', 0):.1f}%",
                "",
                f"Avg Delivery Days: {metrics.get('avg_delivery_days', 0):.1f} days",
                "",
                "💡 *Performance:*",
                f"{'✅ Excellent' if metrics.get('delivery_rate', 0) >= 90 else '⚠️ Needs Improvement'}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Transporter format error: {e}")
            return "❌ Unable to format transporter dashboard"
    
    def _format_inventory_dashboard(self, data, req_id: str) -> str:
        """Format Inventory Dashboard for WhatsApp."""
        try:
            metrics = data.data.get("metrics", {})
            
            lines = [
                "📦 *INVENTORY DASHBOARD*",
                "",
                f"Total DNs: {metrics.get('total_dns', 0):,}",
                f"Units: {metrics.get('total_units', 0):,}",
                f"Pending PGI: {metrics.get('pending_pgi', 0):,}",
                "",
                f"Avg Processing: {metrics.get('avg_processing_days', 0):.1f} days",
                "",
                "💡 *Inventory Status:*",
                f"{'✅ Healthy' if metrics.get('pending_pgi', 0) < 100 else '⚠️ Backlog Detected'}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Inventory format error: {e}")
            return "❌ Unable to format inventory dashboard"
    
    # ==========================================================
    # RESPONSE FORMATTERS - Existing (Preserved)
    # ==========================================================
    
    def _truncate_response(self, response: str) -> str:
        """Truncate response to WhatsApp character limit."""
        if len(response) > MAX_RESPONSE_LENGTH:
            return response[:MAX_RESPONSE_LENGTH - 20] + "\n\n... (truncated)"
        return response
    
    def _format_dealer_dashboard(self, data, dealer_name: str, req_id: str) -> str:
        """Format dealer dashboard for WhatsApp."""
        try:
            d = data.data or {}
            profile = d.get("profile", {})
            summary = d.get("summary", {})
            performance = d.get("performance", {})
            distance_info = d.get("distance_info", {})
            
            total_dns = summary.get("total_dns", 0)
            if total_dns == 0:
                return f"❌ No data found for {dealer_name}"
            
            risk_level = performance.get("risk_level", "low").lower()
            risk_emoji = self._get_risk_emoji(risk_level)
            
            lines = [
                "🏪 *DEALER DASHBOARD*",
                "",
                "👤 *Dealer Profile*",
                f"Name: {dealer_name}",
                f"Code: {profile.get('dealer_code', 'N/A')}",
                f"City: {profile.get('city', 'N/A')}",
                f"Warehouse: {profile.get('warehouse', 'N/A')}",
                "",
                "📊 *Business Summary*",
                f"DNs: {total_dns:,}",
                f"Units: {summary.get('total_units', 0):,}",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Performance*",
                f"Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {summary.get('pending_pgi', 0)}",
                f"Pending PODs: {summary.get('pending_pod', 0)}",
                "",
                "⚠️ *Risk*",
                f"Risk Level: {risk_emoji} {risk_level.upper()}",
                f"Health Score: {performance.get('health_score', 0)}/100"
            ]
            
            if distance_info:
                distance_summary = distance_info.get("summary", "")
                if distance_summary:
                    lines.append("")
                    lines.append("📍 *Distance*")
                    lines.append(distance_summary)
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Dealer format error: {e}")
            return f"❌ Unable to format dealer dashboard for {dealer_name}"
    
    def _format_warehouse_dashboard(self, data, warehouse_name: str, req_id: str) -> str:
        """Format warehouse dashboard for WhatsApp."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            
            total_dns = summary.get("total_dns", 0)
            if total_dns == 0:
                return f"❌ No data found for {warehouse_name}"
            
            lines = [
                "🏭 *WAREHOUSE DASHBOARD*",
                "",
                f"Warehouse: {warehouse_name}",
                f"Code: {d.get('warehouse_code', 'N/A')}",
                "",
                "📍 *Coverage*",
                f"Dealers: {summary.get('total_dealers', 0):,}",
                f"Cities: {summary.get('cities_served', 0):,}",
                "",
                "📊 *Business*",
                f"DNs: {total_dns:,}",
                f"Units: {summary.get('total_units', 0):,}",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Performance*",
                f"Delivery: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI: {summary.get('pgi_rate', 0):.1f}%",
                f"POD: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {summary.get('pending_dns', 0):,}",
                f"Pending PODs: {summary.get('pending_pod_dns', 0):,}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Warehouse format error: {e}")
            return f"❌ Unable to format warehouse dashboard for {warehouse_name}"
    
    def _format_city_dashboard(self, data, city_name: str, req_id: str) -> str:
        """Format city dashboard for WhatsApp."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            
            total_dns = summary.get("total_dns", 0)
            if total_dns == 0:
                return f"❌ No data found for {city_name}"
            
            lines = [
                "🏙️ *CITY DASHBOARD*",
                "",
                f"City: {city_name}",
                "",
                "📊 *Business*",
                f"Dealers: {summary.get('total_dealers', 0):,}",
                f"Warehouses: {summary.get('total_warehouses', 0)}",
                f"DNs: {total_dns:,}",
                f"Units: {summary.get('total_units', 0):,}",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Performance*",
                f"Delivery: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI: {summary.get('pgi_rate', 0):.1f}%",
                f"POD: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {summary.get('pending_dns', 0)}",
                f"Pending PODs: {summary.get('pending_pod_dns', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] City format error: {e}")
            return f"❌ Unable to format city dashboard for {city_name}"
    
    def _format_dn_dashboard(self, data, req_id: str) -> str:
        """Format DN tracking for WhatsApp."""
        try:
            record = data.data.get("record", {})
            validation = data.data.get("validation", {})
            status = data.data.get("status", "unknown")
            distance_info = data.data.get("distance_info", {})
            risk_level = data.data.get("risk_level", "low")
            
            dn_no = record.get('dn_number', 'N/A')
            dealer_name = record.get('customer_name', 'N/A')
            warehouse = record.get('warehouse', 'N/A')
            units = record.get('units', 0)
            amount = record.get('amount', 0)
            
            status_emoji = "✅" if status == "delivered" else "🚚" if status == "pending_pod" else "⏳"
            risk_emoji = self._get_risk_emoji(risk_level)
            
            lines = [
                "📄 *DN TRACKING*",
                "",
                f"DN No: {dn_no}",
                f"Dealer: {dealer_name}",
                f"Warehouse: {warehouse}",
                "",
                f"Units: {units}",
                f"Revenue: PKR {amount:,.0f}",
                "",
                f"Status: {status_emoji} {status.upper()}",
                f"Risk: {risk_emoji} {risk_level.upper()}"
            ]
            
            distance_summary = distance_info.get("summary", "")
            if distance_summary:
                lines.append("")
                lines.append(distance_summary)
            
            issues = validation.get("issues", [])
            if issues:
                lines.append("")
                lines.append("⚠️ Issues:")
                for issue in issues[:2]:
                    lines.append(f"   • {issue}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] DN format error: {e}")
            return f"❌ Unable to format DN details"
    
    def _format_product_dashboard(self, data, req_id: str) -> str:
        """Format product dashboard for WhatsApp."""
        try:
            dealers = data.data.get("dealers", [])
            if not dealers:
                return "❌ No product data available"
            
            lines = [
                "📦 *PRODUCT DASHBOARD*",
                "",
                "🏆 *Top Models*"
            ]
            
            count = 0
            for dealer in dealers[:10]:
                if count >= 5:
                    break
                name = dealer.get("dealer_name", "Unknown")
                revenue = dealer.get("total_revenue", 0)
                if revenue > 0:
                    lines.append(f"   • {name}: PKR {revenue:,.0f}")
                    count += 1
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Product format error: {e}")
            return "❌ Unable to format product dashboard"
    
    def _format_delivery_dashboard(self, data, req_id: str) -> str:
        """Format delivery dashboard for WhatsApp."""
        try:
            metrics = data.data.get("metrics", {})
            
            lines = [
                "🚚 *DELIVERY DASHBOARD*",
                "",
                f"Total DNs: {metrics.get('total_dns', 0):,}",
                f"Delivered: {metrics.get('delivered', 0):,}",
                f"In Transit: {metrics.get('in_transit', 0):,}",
                f"Pending PGI: {metrics.get('pending_pgi', 0):,}",
                "",
                f"Delivery Rate: {metrics.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {metrics.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {metrics.get('pod_rate', 0):.1f}%",
                "",
                f"Avg Processing: {metrics.get('avg_processing_days', 0):.1f} days",
                f"Avg Delivery: {metrics.get('avg_delivery_days', 0):.1f} days"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Delivery format error: {e}")
            return "❌ Unable to format delivery dashboard"
    
    def _format_pod_dashboard(self, data, req_id: str) -> str:
        """Format POD dashboard for WhatsApp."""
        try:
            metrics = data.data.get("metrics", {})
            issues = data.data.get("key_issues", [])
            recommendations = data.data.get("recommendations", [])
            
            lines = [
                "📋 *POD DASHBOARD*",
                "",
                f"POD Rate: {metrics.get('pod_rate', 0):.1f}%",
                f"Pending POD: {metrics.get('pending_pod', 0):,}",
                f"POD Completed: {metrics.get('pod_completed', 0):,}",
            ]
            
            if issues:
                lines.append("")
                lines.append("⚠️ *Issues*")
                for issue in issues[:3]:
                    lines.append(f"   • {issue}")
            
            if recommendations:
                lines.append("")
                lines.append("🎯 *Recommendations*")
                for rec in recommendations[:2]:
                    lines.append(f"   • {rec}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] POD format error: {e}")
            return "❌ Unable to format POD dashboard"
    
    def _format_revenue_dashboard(self, data, req_id: str) -> str:
        """Format revenue dashboard for WhatsApp."""
        try:
            dealers = data.data.get("dealers", [])[:5]
            summary = data.data.get("summary", {})
            
            lines = [
                "💰 *REVENUE DASHBOARD*",
                "",
                f"Total Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"Total Dealers: {summary.get('total_dealers', 0):,}",
                "",
                "🏆 *Top Dealers*"
            ]
            
            for dealer in dealers:
                name = dealer.get("dealer_name", "Unknown")
                revenue = dealer.get("total_revenue", 0)
                lines.append(f"   • {name}: PKR {revenue:,.0f}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Revenue format error: {e}")
            return "❌ Unable to format revenue dashboard"
    
    def _format_distance_dashboard(self, dealer: str, warehouse: str, distance: float, transit_days: int, status: str, req_id: str) -> str:
        """Format distance dashboard for WhatsApp."""
        if status == "same_city":
            return f"""📍 *DISTANCE DASHBOARD*

📍 Same City Delivery
Warehouse and Dealer are located in the same city.

Expected Delivery: 1 Day
Risk: Low
Distance: Not Applicable

Dealer: {dealer}
Warehouse: {warehouse}"""
        
        route_desc = "Short" if distance <= 50 else "Medium" if distance <= 150 else "Long" if distance <= 300 else "Extended" if distance <= 500 else "Very Long"
        
        return f"""📍 *DISTANCE DASHBOARD*

Dealer: {dealer}
Warehouse: {warehouse}

Distance: {distance:.1f} KM
Route Type: {route_desc} distance route
Expected Transit: {transit_days} Days
Risk Level: Low

*Analysis:*
This is a {route_desc.lower()} distance route.
Expected delivery time is {transit_days} days."""
    
    def _format_performance_dashboard(self, data, req_id: str) -> str:
        """Format performance dashboard for WhatsApp."""
        try:
            metrics = data.data.get("metrics", {})
            
            lines = [
                "📊 *PERFORMANCE DASHBOARD*",
                "",
                f"Delivery Rate: {metrics.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {metrics.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {metrics.get('pod_rate', 0):.1f}%",
                "",
                f"Avg Processing: {metrics.get('avg_processing_days', 0):.1f} days",
                f"Avg Delivery: {metrics.get('avg_delivery_days', 0):.1f} days",
                "",
                f"Total DNs: {metrics.get('total_dns', 0):,}",
                f"Delivered: {metrics.get('delivered', 0):,}",
                f"In Transit: {metrics.get('in_transit', 0):,}",
                f"Pending: {metrics.get('pending_pgi', 0):,}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Performance format error: {e}")
            return "❌ Unable to format performance dashboard"
    
    def _format_forecast_dashboard(self, data, req_id: str) -> str:
        """Format forecast dashboard for WhatsApp."""
        try:
            summary = data.data.get("summary", {})
            insights = data.data.get("insights", [])
            
            lines = [
                "📊 *FORECAST DASHBOARD*",
                "",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"Units: {summary.get('total_units', 0):,}",
                f"DNs: {summary.get('total_dns', 0):,}",
                "",
                f"Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
            ]
            
            if insights:
                lines.append("")
                lines.append("💡 *Insights*")
                for insight in insights[:2]:
                    lines.append(f"   • {insight}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Forecast format error: {e}")
            return "❌ Unable to format forecast dashboard"
    
    def _format_executive_dashboard(self, data, req_id: str) -> str:
        """Format executive dashboard for WhatsApp."""
        try:
            summary = data.data.get("summary", {})
            insights = data.data.get("insights", [])
            health_score = data.data.get("health_score", 0)
            top_dealers = data.data.get("top_dealers", [])
            top_cities = data.data.get("top_cities", [])
            
            health_emoji = "✅" if health_score >= 80 else "⚠️" if health_score >= 60 else "🔴"
            health_status = "Healthy" if health_score >= 80 else "Needs Attention" if health_score >= 60 else "Critical"
            
            lines = [
                "👔 *EXECUTIVE DASHBOARD*",
                "",
                "💰 *Business*",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"Units: {summary.get('total_units', 0):,}",
                f"DNs: {summary.get('total_dns', 0):,}",
                "",
                "📈 *KPI*",
                f"Delivery: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI: {summary.get('pgi_rate', 0):.1f}%",
                f"POD: {summary.get('pod_rate', 0):.1f}%",
            ]
            
            if top_dealers:
                lines.append("")
                lines.append("🏆 *Top Dealer*")
                top = top_dealers[0]
                lines.append(f"   • {top.get('dealer_name', 'N/A')}: PKR {top.get('total_revenue', 0):,.0f}")
            
            if top_cities:
                lines.append("")
                lines.append("🏙️ *Top City*")
                top = top_cities[0]
                lines.append(f"   • {top.get('city', 'N/A')}: PKR {top.get('total_revenue', 0):,.0f}")
            
            lines.append("")
            lines.append("📊 *Health Score*")
            lines.append(f"{health_score}/100 - {health_emoji} {health_status}")
            
            if insights:
                lines.append("")
                lines.append("💡 *Insights*")
                for insight in insights[:2]:
                    lines.append(f"   • {insight}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Executive format error: {e}")
            return "👔 Unable to format executive dashboard"
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _get_risk_emoji(self, risk_level: str) -> str:
        risk_level = risk_level.lower()
        if risk_level == "critical":
            return "🔴"
        elif risk_level == "high":
            return "🟠"
        elif risk_level == "medium":
            return "🟡"
        else:
            return "🟢"
    
    def _get_help_message(self) -> str:
        return """🏠 *HAIER LOGISTICS AI*

*📋 18 Dashboards Available:*

1️⃣ 🏪 Dealer Dashboard
2️⃣ 🏭 Warehouse Dashboard
3️⃣ 🏙️ City Dashboard
4️⃣ 📦 Product Dashboard
5️⃣ 📄 DN Dashboard
6️⃣ 📋 PGI Dashboard
7️⃣ ✅ POD Dashboard
8️⃣ 🚚 Delivery Dashboard
9️⃣ 📍 Distance Dashboard
🔟 👔 Executive Dashboard
1️⃣1️⃣ 🚨 Control Tower
1️⃣2️⃣ 🏆 Dealer Ranking
1️⃣3️⃣ 🏆 Warehouse Ranking
1️⃣4️⃣ 🏆 Product Ranking
1️⃣5️⃣ 🚛 Transporter Dashboard
1️⃣6️⃣ 💰 Revenue Dashboard
1️⃣7️⃣ 📦 Inventory Dashboard
1️⃣8️⃣ 📊 Forecast Dashboard

*🔍 Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "ZQ Electronics")
• City name (e.g., "Haripur")
• Warehouse name
• "Executive summary"
• "Control tower"
• "Top dealers"
• "Help" for menu

*💡 Follow-up Support:*
• "What is its POD?" → Uses last dealer
• "How many pending DN?" → Uses last dealer
• "Show me its revenue" → Uses last dealer

*Ask me anything about logistics!* 🤖"""
    
    def _get_timeout_response(self, req_id: str) -> str:
        return f"""⏳ *Request Timed Out*

I'm still working on your request.
Please wait a moment and try again.

Reference: `{req_id}`"""
    
    def _get_error_response(self, error: Exception, req_id: str) -> str:
        error_id = str(uuid.uuid4())[:8]
        return f"""⚠️ *Unable to Process*

Please try again or type 'help' for assistance.

Reference: `{req_id}` | Error: `{error_id}`"""
    
    # ==========================================================
    # METRICS & ADMIN
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        avg_response = 0
        if self.metrics["response_times_ms"]:
            avg_response = sum(self.metrics["response_times_ms"]) / len(self.metrics["response_times_ms"])
        
        return {
            "version": "21.0",
            "total_requests": self.metrics["total_requests"],
            "fast_cache_hits": self.metrics["fast_cache_hits"],
            "cache_hits": self.metrics["cache_hits"],
            "avg_response_ms": round(avg_response, 2),
            "intent_detection": self.metrics["intent_detection"],
            "follow_up_queries": self.metrics["follow_up_queries"],
            "drill_down_queries": self.metrics["drill_down_queries"],
            "dealer_resolution": self.metrics["dealer_resolution"],
            "groq_uses": self.metrics["groq_uses"],
            "groq_fallbacks": self.metrics["groq_fallbacks"],
            "errors": self.metrics["errors"],
            "timeouts": self.metrics["timeouts"],
            "redis_available": REDIS_AVAILABLE and self._redis_client is not None,
            "rapidfuzz_available": RAPIDFUZZ_AVAILABLE
        }
    
    def clear_caches(self):
        self.response_cache.clear()
        self.failure_cache.clear()
        self.fast_cache.clear()
        self.conversation_cache.clear()
        self.dealer_resolution_cache.clear()
        self._suggestion_cache.clear()
        
        if self._redis_client:
            try:
                self._redis_client.flushdb()
            except:
                pass
        
        logger.info("🗑️ All caches cleared")
        return {"status": "cleared", "version": "21.0"}


# ==========================================================
# SINGLETON
# ==========================================================

_orchestrator = None

def get_orchestrator() -> AIOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AIOrchestrator()
    return _orchestrator


# ==========================================================
# WRAPPER FUNCTIONS
# ==========================================================

def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    orchestrator = get_orchestrator()
    return orchestrator.process_whatsapp_query(
        question=question,
        session_factory=session_factory,
        phone_number=phone_number,
        user_id=user_id,
        request_id=request_id
    )


def get_ai_service_metrics() -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    return orchestrator.get_metrics()


def clear_ai_cache():
    orchestrator = get_orchestrator()
    return orchestrator.clear_caches()


def get_routing_debug(question: str) -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    return orchestrator.get_routing_debug(question)


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("AI Router v21.0 - Master AI Router with Full Dashboards")
logger.info("=" * 70)
logger.info("")
logger.info("   RULES:")
logger.info("   ✅ Analytics First - analytics_service.py")
logger.info("   ✅ Groq Second - Only for specific intents")
logger.info("   ✅ Database Truth Always")
logger.info("   ✅ Never Crash")
logger.info("   ✅ Always Fast")
logger.info("   ✅ Always WhatsApp Safe")
logger.info("")
logger.info("   📊 18 DASHBOARDS SUPPORTED:")
logger.info("      1. 🏪 Dealer Dashboard")
logger.info("      2. 🏭 Warehouse Dashboard")
logger.info("      3. 🏙️ City Dashboard")
logger.info("      4. 📦 Product Dashboard")
logger.info("      5. 📄 DN Dashboard")
logger.info("      6. 📋 PGI Dashboard")
logger.info("      7. ✅ POD Dashboard")
logger.info("      8. 🚚 Delivery Dashboard")
logger.info("      9. 📍 Distance Dashboard")
logger.info("      10. 👔 Executive Dashboard")
logger.info("      11. 🚨 Control Tower Dashboard")
logger.info("      12. 🏆 Dealer Ranking")
logger.info("      13. 🏆 Warehouse Ranking")
logger.info("      14. 🏆 Product Ranking")
logger.info("      15. 🚛 Transporter Dashboard")
logger.info("      16. 💰 Revenue Dashboard")
logger.info("      17. 📦 Inventory Dashboard")
logger.info("      18. 📊 Forecast Dashboard")
logger.info("")
logger.info("   🔍 ENTITY RECOGNITION:")
logger.info("      - Dealer Name | Dealer Code | Customer Code")
logger.info("      - Warehouse | City | Material | Product Model")
logger.info("      - DN Number | Sales Office | Division")
logger.info("")
logger.info("   💬 FOLLOW-UP SUPPORT:")
logger.info("      - 'What is its POD?' → Uses last_dealer")
logger.info("      - 'How many pending DN?' → Uses last_dealer")
logger.info("      - 'Show me its revenue' → Uses last_dealer")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
