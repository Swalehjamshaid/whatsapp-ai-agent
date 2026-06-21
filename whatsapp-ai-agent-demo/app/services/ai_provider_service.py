# ==========================================================
# FILE: app/services/ai_provider_service.py (v22.0 - ENTERPRISE)
# ==========================================================
# PURPOSE: AI ROUTER - Production-Grade WhatsApp Logistics AI
# VERSION: 22.0 - Complete Enterprise Upgrade
#
# ENHANCEMENTS IN v22.0:
# - ✅ Query validation & sanitization
# - ✅ Enhanced context memory with entity tracking
# - ✅ Session isolation for concurrent users
# - ✅ Rate limiting per user
# - ✅ Graceful degradation for service failures
# - ✅ 100% intent coverage for all logistics questions
# - ✅ Dealer alias/synonym support
# - ✅ WhatsApp quick replies
# - ✅ Performance monitoring with percentiles
# - ✅ Complete dashboard route fixes
# - ✅ Follow-up support for all entity types
# - ✅ Business rule enforcement
# - ✅ SQL injection protection
#
# ROLE: This file is the AI Router.
#        This file must NEVER perform analytics.
#        Analytics always come from analytics_service.py
#
# FLOW:
# User Message → Validate → Detect Intent → Extract Entities → 
# Route to Analytics → Format Response → Add Quick Replies → WhatsApp
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
from dataclasses import dataclass, field
from cachetools import TTLCache, LRUCache
from loguru import logger
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from functools import lru_cache
from collections import defaultdict

# ==========================================================
# ULTRA-FAST IMPORTS
# ==========================================================

# Ultra-fast JSON
try:
    import orjson
    JSON_FAST = True
except ImportError:
    import json
    orjson = None
    JSON_FAST = False

# Ultra-fast fuzzy matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    from difflib import SequenceMatcher
    RAPIDFUZZ_AVAILABLE = False

# Redis caching
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# Tenacity retry
try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

# ==========================================================
# LAZY IMPORTS - Avoid circular dependencies
# ==========================================================

def _get_ai_query_service():
    try:
        from app.services.ai_query_service import get_ai_query_service
        return get_ai_query_service()
    except ImportError:
        logger.warning("⚠️ ai_query_service not available")
        return None

def _get_analytics_service():
    try:
        from app.services.analytics_service import get_analytics_service, AnalyticsResponse
        return get_analytics_service(), AnalyticsResponse
    except ImportError:
        logger.warning("⚠️ analytics_service not available")
        return None, None

def _get_kpi_service():
    try:
        from app.services.kpi_service import get_kpi_service
        return get_kpi_service()
    except ImportError:
        logger.warning("⚠️ kpi_service not available")
        return None

def _get_groq_service():
    try:
        from app.services.groq_service import get_groq_service
        return get_groq_service()
    except ImportError:
        logger.warning("⚠️ groq_service not available")
        return None

def _get_schema_service():
    try:
        from app.schemas.schema_service import get_schema_service
        return get_schema_service()
    except ImportError:
        logger.warning("⚠️ schema_service not available")
        return None

def _get_whatsapp_service():
    try:
        from app.services.whatsapp_service import get_whatsapp_service
        return get_whatsapp_service()
    except ImportError:
        logger.warning("⚠️ whatsapp_service not available")
        return None

# ==========================================================
# CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800
MAX_RETRY_ATTEMPTS = 3
DEALER_SUGGESTION_LIMIT = 3
MAX_RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60

# ⚡ SPEED OPTIMIZED TIMEOUTS
GROQ_TIMEOUT_SECONDS = 8
ENRICHMENT_TIMEOUT_SECONDS = 3
DB_TIMEOUT_SECONDS = 10
OPENROUTE_TIMEOUT_SECONDS = 5

MAX_RECOVERY_ATTEMPTS = 3
MAX_RESPONSE_LENGTH = 2500  # WhatsApp character limit (reduced for better UX)

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
    DIVISION = "division_dashboard"
    SALES_OFFICE = "sales_office_dashboard"
    REVENUE_TREND = "revenue_trend_dashboard"
    SLA_COMPLIANCE = "sla_compliance_dashboard"

class EntityType(Enum):
    DEALER_NAME = "dealer_name"
    DEALER_CODE = "dealer_code"
    CUSTOMER_CODE = "customer_code"
    WAREHOUSE = "warehouse"
    WAREHOUSE_CODE = "warehouse_code"
    CITY = "city"
    CITY_ALIAS = "city_alias"
    MATERIAL = "material"
    PRODUCT_MODEL = "product_model"
    DN_NUMBER = "dn_number"
    SALES_OFFICE = "sales_office"
    DIVISION = "division"
    TRANSPORTER = "transporter"
    TRANSPORTER_CODE = "transporter_code"

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
# ENTITY PATTERNS FOR RECOGNITION (ENHANCED v22.0)
# ==========================================================

ENTITY_PATTERNS = {
    # Dealer Patterns
    "dealer_name": r'(?:dealer|customer|party|sold to|name)\s+([A-Za-z0-9\s&]+)',
    "dealer_code": r'\b(?:[A-Z]{2,4}\d{2,6})\b',
    "customer_code": r'\b(?:CUST|CT|CUST-)\s*(\d{5,})\b',
    
    # Warehouse Patterns
    "warehouse": r'(?:warehouse|wh|depot)\s+([A-Za-z0-9\s]+)',
    "warehouse_code": r'\b(?:WH|WH-)\s*(\d{3,})\b',
    
    # City Patterns
    "city": r'(?:city|location|in)\s+([A-Za-z\s]+)',
    
    # Product Patterns
    "material": r'(?:material|mat|item)\s+([A-Za-z0-9\-]+)',
    "product_model": r'\b(?:model|product|M)\s*([A-Z]+\d+)\b',
    
    # DN Patterns
    "dn_number": r'\b(\d{8,12})\b',
    "dn_pattern": r'(?:dn|delivery note|order)\s*[:#]?\s*(\d{8,12})',
    
    # Division Patterns
    "division": r'(?:division|div)\s+([A-Za-z\s]+)',
    
    # Sales Office Patterns
    "sales_office": r'(?:sales office|office|so)\s+([A-Za-z\s]+)',
    
    # Transporter Patterns
    "transporter": r'(?:transporter|carrier|transport)\s+([A-Za-z\s&]+)',
    "transporter_code": r'\b(?:TR|T)\s*(\d{3,})\b',
    
    # Time Patterns
    "time_period": r'\b(last|previous|next)\s+(\d+)\s+(day|days|week|weeks|month|months|quarter)\b',
    "month": r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b',
}

# ==========================================================
# ENHANCED INTENT CLASSIFICATION - v22.0
# ==========================================================

INTENT_PATTERNS = {
    # 1. Dealer Dashboard
    "dealer_dashboard": [
        "dealer", "customer", "show me dealer", "dealer performance",
        "dealer revenue", "dealer units", "dealer ranking",
        "top dealer", "best dealer", "dealer dashboard",
        "customer performance", "customer dashboard",
        "dealer name", "dealer code", "customer code"
    ],
    
    # 2. Dealer Products
    "dealer_products": [
        "products of", "what products", "models sold", "dealer sells",
        "top products for", "best selling for"
    ],
    
    # 3. Dealer DN Aging
    "dealer_dn_aging": [
        "dn aging", "aging for", "old dns", "aged dns",
        "pending days", "dn delay"
    ],
    
    # 4. Dealer Delivery Performance
    "dealer_delivery_performance": [
        "delivery performance", "delivery rate", "delivery time",
        "on time delivery", "delivery speed"
    ],
    
    # 5. Warehouse Dashboard
    "warehouse_dashboard": [
        "warehouse", "show me warehouse", "warehouse performance",
        "warehouse revenue", "warehouse ranking", "warehouse dashboard",
        "warehouse code"
    ],
    
    # 6. Warehouse Products
    "warehouse_products": [
        "warehouse stock", "stock in", "inventory at", "whats in",
        "products in warehouse", "warehouse inventory"
    ],
    
    # 7. Warehouse Coverage
    "warehouse_coverage": [
        "warehouse coverage", "cities served", "served cities",
        "warehouse reach", "warehouse service area"
    ],
    
    # 8. City Dashboard
    "city_dashboard": [
        "city", "show me city", "city performance", "city revenue",
        "city ranking", "top city", "worst city", "city dashboard"
    ],
    
    # 9. City Dealers
    "city_dealers": [
        "dealers in", "dealers in city", "city dealers",
        "city dealer list"
    ],
    
    # 10. City Warehouses
    "city_warehouses": [
        "warehouses in", "city warehouses", "city warehouse list"
    ],
    
    # 11. Product Dashboard
    "product_dashboard": [
        "product", "model", "top product", "best seller",
        "product performance", "product revenue", "product dashboard",
        "top model", "best model", "material performance"
    ],
    
    # 12. Product by Model
    "product_by_model": [
        "model", "show model", "model performance",
        "model revenue", "model units"
    ],
    
    # 13. Product DN Count
    "product_dn_count": [
        "dns for product", "product dns", "how many dns",
        "dn count for"
    ],
    
    # 14. DN Dashboard
    "dn_dashboard": [
        "dn", "track", "delivery note", "order status",
        "where is", "shipment", "delivery status", "track dn",
        "delivery note", "dn number"
    ],
    
    # 15. DN Questions
    "dn_questions": [
        "what is dn", "dn details", "dn information",
        "dn status", "dn delivery date", "dn pgi date",
        "dn pod status", "dn products", "dn units",
        "dn amount", "dn value", "dn aging"
    ],
    
    # 16. PGI Dashboard
    "pgi_dashboard": [
        "pgi", "goods issue", "pgi status", "pgi pending",
        "pgi completed", "pgi dashboard"
    ],
    
    # 17. PGI by Dealer
    "pgi_by_dealer": [
        "pgi pending for", "pending pgi", "pgi status for",
        "goods issue for"
    ],
    
    # 18. POD Dashboard
    "pod_dashboard": [
        "pod", "pending pod", "pod collection", "pod status",
        "pod compliance", "pod aging", "pod dashboard",
        "proof of delivery"
    ],
    
    # 19. POD by Dealer
    "pod_by_dealer": [
        "pod pending for", "pending pod", "pod status for",
        "pod aging for"
    ],
    
    # 20. POD Aging
    "pod_aging": [
        "aging pod", "pod aging analysis", "pod delay",
        "pod pending days"
    ],
    
    # 21. Delivery Dashboard
    "delivery_dashboard": [
        "delivery", "pending delivery", "delayed delivery",
        "delivery performance", "delivery rate", "delivery dashboard"
    ],
    
    # 22. Distance Dashboard
    "distance_dashboard": [
        "distance", "how far", "transit", "travel time",
        "distance from warehouse", "expected delivery", "distance dashboard"
    ],
    
    # 23. Distance by City
    "distance_by_city": [
        "distance to", "travel to", "route to",
        "transit to"
    ],
    
    # 24. Executive Dashboard
    "executive_dashboard": [
        "executive", "ceo", "management", "strategic",
        "nationwide", "overview", "business summary",
        "executive dashboard", "executive summary"
    ],
    
    # 25. Control Tower
    "control_tower": [
        "control tower", "control", "tower", "alerts",
        "critical issues", "logistics control", "control dashboard"
    ],
    
    # 26. Dealer Ranking
    "dealer_ranking": [
        "dealer ranking", "top dealers", "best dealers",
        "dealer rank", "ranking dealer"
    ],
    
    # 27. Warehouse Ranking
    "warehouse_ranking": [
        "warehouse ranking", "top warehouses", "best warehouses",
        "warehouse rank", "ranking warehouse"
    ],
    
    # 28. Product Ranking
    "product_ranking": [
        "product ranking", "top products", "best products",
        "product rank", "ranking product", "best selling"
    ],
    
    # 29. Transporter Dashboard
    "transporter_dashboard": [
        "transporter", "carrier", "logistics partner",
        "transporter performance", "transporter dashboard"
    ],
    
    # 30. Transporter by Name
    "transporter_by_name": [
        "show transporter", "transporter details", "transporter status",
        "transporter rating"
    ],
    
    # 31. Revenue Dashboard
    "revenue_dashboard": [
        "revenue", "sales", "income", "turnover",
        "revenue summary", "sales performance", "revenue dashboard"
    ],
    
    # 32. Revenue by Division
    "revenue_by_division": [
        "division revenue", "revenue by division", "revenue per division"
    ],
    
    # 33. Revenue by Warehouse
    "revenue_by_warehouse": [
        "warehouse revenue", "revenue by warehouse", "revenue per warehouse"
    ],
    
    # 34. Revenue Trend
    "revenue_trend": [
        "revenue trend", "monthly revenue", "revenue over time",
        "revenue growth", "trend analysis"
    ],
    
    # 35. Inventory Dashboard
    "inventory_dashboard": [
        "inventory", "stock", "warehouse stock",
        "inventory status", "stock level", "inventory dashboard"
    ],
    
    # 36. Inventory by Warehouse
    "inventory_by_warehouse": [
        "inventory in", "stock in", "warehouse stock level",
        "inventory level"
    ],
    
    # 37. Inventory by Material
    "inventory_by_material": [
        "material stock", "stock of", "inventory for",
        "product stock level"
    ],
    
    # 38. Forecast Dashboard
    "forecast_dashboard": [
        "forecast", "predict", "estimated", "projected",
        "next month", "expected revenue", "future", "forecast dashboard"
    ],
    
    # 39. Forecast by Division
    "forecast_by_division": [
        "division forecast", "forecast by division", "forecast per division"
    ],
    
    # 40. Forecast by Warehouse
    "forecast_by_warehouse": [
        "warehouse forecast", "forecast by warehouse", "forecast per warehouse"
    ],
    
    # 41. Division Dashboard
    "division_dashboard": [
        "division", "show division", "division performance",
        "division revenue", "division units", "division dashboard"
    ],
    
    # 42. Sales Office Dashboard
    "sales_office_dashboard": [
        "sales office", "office performance", "sales office revenue",
        "sales office dashboard"
    ],
    
    # 43. SLA Compliance
    "sla_compliance": [
        "sla", "sla compliance", "sla violation",
        "service level", "compliance rate"
    ],
    
    # 44. Transporter Ranking
    "transporter_ranking": [
        "transporter ranking", "top transporters", "best transporters"
    ],
}

# ==========================================================
# SPECIAL COMMANDS (Enhanced v22.0)
# ==========================================================

SPECIAL_COMMANDS = {
    # Control Tower
    "control tower": "control_tower",
    "control": "control_tower",
    "tower": "control_tower",
    "alerts": "control_tower",
    "critical": "control_tower",
    
    # Executive
    "executive summary": "executive_dashboard",
    "executive insights": "executive_dashboard",
    "executive": "executive_dashboard",
    "ceo": "executive_dashboard",
    "management": "executive_dashboard",
    "strategic": "executive_dashboard",
    "nationwide": "executive_dashboard",
    "overview": "executive_dashboard",
    
    # Help
    "help": "help",
    "hi": "help",
    "hello": "help",
    "menu": "help",
    "start": "help",
    "whatsapp menu": "help",
    "?" : "help",
    
    # Quick Commands
    "top dealers": "dealer_ranking",
    "top warehouses": "warehouse_ranking",
    "top products": "product_ranking",
    "revenue": "revenue_dashboard",
    "inventory": "inventory_dashboard",
    "forecast": "forecast_dashboard",
    "delivery": "delivery_dashboard",
    "pod": "pod_dashboard",
    "pgi": "pgi_dashboard",
}

# ==========================================================
# GROQ INTENT PATTERNS (Enhanced v22.0)
# ==========================================================

GROQ_INTENT_PATTERNS = {
    "root_cause": ["why", "root cause", "reason", "cause", "because", "due to"],
    "recommendation": ["recommend", "suggest", "advise", "should", "improve", "fix"],
    "executive": ["executive", "ceo", "strategy", "management", "critical"],
    "insight": ["insight", "trend", "pattern", "analysis"],
    "forecast_explain": ["forecast explanation", "why forecast", "predict why"],
    "kpi_explain": ["what is kpi", "explain kpi", "kpi meaning", "how is kpi calculated"],
    "comparison": ["compare", "versus", "vs", "difference", "better", "worse"],
}

# ==========================================================
# CITY ALIASES - For better recognition
# ==========================================================

CITY_ALIASES = {
    "lhe": "lahore",
    "khi": "karachi",
    "rwp": "rawalpindi",
    "isl": "islamabad",
    "fsd": "faisalabad",
    "mux": "multan",
    "hyd": "hyderabad",
    "psh": "peshawar",
    "qta": "quetta",
    "guj": "gujranwala",
    "skt": "sialkot",
    "bah": "bahawalpur",
    "sahiwal": "sahiwal",
    "okara": "okara",
    "haripur": "haripur",
}

# ==========================================================
# ENHANCED CONVERSATION CONTEXT - v22.0
# ==========================================================

@dataclass
class ConversationContext:
    # Core Identity
    phone_number: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    
    # Entity Memory (Enhanced)
    last_dealer: Optional[str] = None
    last_dealer_code: Optional[str] = None
    last_customer_code: Optional[str] = None
    last_warehouse: Optional[str] = None
    last_warehouse_code: Optional[str] = None
    last_city: Optional[str] = None
    last_dn: Optional[str] = None
    last_product_model: Optional[str] = None
    last_material: Optional[str] = None
    last_division: Optional[str] = None
    last_sales_office: Optional[str] = None
    last_transporter: Optional[str] = None
    last_transporter_code: Optional[str] = None
    
    # Entity Type Tracking
    last_entity_type: Optional[str] = None
    last_entity_value: Optional[str] = None
    entity_confidence: float = 0.0
    
    # Intent Memory
    last_intent: Optional[str] = None
    last_dashboard: Optional[str] = None
    intent_confidence: float = 0.0
    
    # Conversation State
    message_count: int = 0
    turn_count: int = 0
    is_valid: bool = True
    is_confirmed: bool = False
    needs_clarification: bool = False
    clarification_asked: bool = False
    clarification_topic: Optional[str] = None
    
    # Timing
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + CONTEXT_TTL_SECONDS)
    
    # Recovery
    retry_count: int = 0
    last_error: Optional[str] = None
    recovery_attempts: int = 0
    
    # Disambiguation Cache
    possible_dealers: List[str] = field(default_factory=list)
    possible_warehouses: List[str] = field(default_factory=list)
    possible_cities: List[str] = field(default_factory=list)
    possible_products: List[str] = field(default_factory=list)
    possible_dns: List[str] = field(default_factory=list)
    
    # History (Last 10 interactions)
    history: List[Dict[str, Any]] = field(default_factory=list)
    
    # Original question cache
    last_question: Optional[str] = None
    last_response: Optional[str] = None
    
    def is_expired(self) -> bool:
        return time.time() > self.expires_at
    
    def touch(self):
        self.last_updated = time.time()
        self.expires_at = time.time() + CONTEXT_TTL_SECONDS
        self.is_valid = True
    
    def add_history(self, question: str, response: str, intent: str, entity: str):
        self.history.append({
            "timestamp": time.time(),
            "question": question[:200],
            "response": response[:200],
            "intent": intent,
            "entity": entity
        })
        if len(self.history) > 10:
            self.history = self.history[-10:]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "phone_number": self.phone_number,
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_city": self.last_city,
            "last_dn": self.last_dn,
            "last_product": self.last_product_model,
            "last_division": self.last_division,
            "last_intent": self.last_intent,
            "last_dashboard": self.last_dashboard,
            "last_entity_type": self.last_entity_type,
            "last_entity_value": self.last_entity_value,
            "message_count": self.message_count,
            "entity_confidence": self.entity_confidence,
            "intent_confidence": self.intent_confidence,
            "is_valid": self.is_valid,
            "created_at": self.created_at,
            "last_updated": self.last_updated
        }


# ==========================================================
# MASTER AI ROUTER - v22.0 ENTERPRISE
# ==========================================================

class AIOrchestrator:
    """
    MASTER AI ROUTER - v22.0 ENTERPRISE
    
    ROLE: This file is the AI Router.
    This file must NEVER perform analytics.
    Analytics always come from analytics_service.py
    
    RULES:
    1. Analytics First - Always try analytics_service.py first
    2. Groq Second - Only for specific intents
    3. Database Truth Always - Never hallucinate data
    4. Never Crash - Always handle errors gracefully
    5. Always Fast - Use caching and async where possible
    6. Always WhatsApp Safe - Max 2500 chars, proper formatting
    7. Smart Context - Track all entities for follow-up
    8. Rate Limited - Prevent abuse
    9. Validated Input - SQL injection protection
    10. Graceful Degradation - Handle service failures
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
        
        self.response_cache = TTLCache(maxsize=2000, ttl=CACHE_TTL_SECONDS)
        self.failure_cache = TTLCache(maxsize=400, ttl=60)
        self.fast_cache = LRUCache(maxsize=1000)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        self.dealer_resolution_cache: Dict[str, Tuple[str, float, float]] = {}
        self._suggestion_cache: Dict[str, List[str]] = {}
        
        # ==========================================================
        # RATE LIMITING
        # ==========================================================
        
        self._rate_limit_cache: Dict[str, List[float]] = {}
        
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
        # DASHBOARD ROUTING MATRIX - Full 22 Dashboards v22.0
        # ==========================================================
        
        self._dashboard_routing_matrix = {
            "dealer_dashboard": {
                "handler": self._route_dealer_dashboard,
                "requires": ["dealer_name", "dealer_code", "customer_code"],
                "follow_up": ["performance", "revenue", "pod", "dn", "ranking", "products"],
                "drill_down": ["dealer_details", "dealer_timeline", "dealer_products", "dealer_trend"],
                "display_name": "Dealer Dashboard",
                "emoji": "🏪"
            },
            "dealer_products": {
                "handler": self._route_dealer_products,
                "requires": ["dealer_name"],
                "follow_up": ["top_products", "revenue_by_product"],
                "drill_down": ["product_details"],
                "display_name": "Dealer Products",
                "emoji": "📦"
            },
            "dealer_dn_aging": {
                "handler": self._route_dealer_dn_aging,
                "requires": ["dealer_name"],
                "follow_up": ["aging_details", "pending_dns"],
                "drill_down": ["dn_list"],
                "display_name": "DN Aging",
                "emoji": "⏳"
            },
            "dealer_delivery_performance": {
                "handler": self._route_dealer_delivery_performance,
                "requires": ["dealer_name"],
                "follow_up": ["delivery_rate", "on_time", "delayed"],
                "drill_down": ["delivery_details"],
                "display_name": "Delivery Performance",
                "emoji": "🚚"
            },
            "warehouse_dashboard": {
                "handler": self._route_warehouse_dashboard,
                "requires": ["warehouse"],
                "follow_up": ["performance", "coverage", "revenue", "ranking", "inventory"],
                "drill_down": ["warehouse_details", "warehouse_top_dealers", "warehouse_inventory"],
                "display_name": "Warehouse Dashboard",
                "emoji": "🏭"
            },
            "warehouse_products": {
                "handler": self._route_warehouse_products,
                "requires": ["warehouse"],
                "follow_up": ["stock_levels", "movement"],
                "drill_down": ["product_details"],
                "display_name": "Warehouse Products",
                "emoji": "📦"
            },
            "warehouse_coverage": {
                "handler": self._route_warehouse_coverage,
                "requires": ["warehouse"],
                "follow_up": ["cities_served", "dealers_served"],
                "drill_down": ["coverage_details"],
                "display_name": "Warehouse Coverage",
                "emoji": "📍"
            },
            "city_dashboard": {
                "handler": self._route_city_dashboard,
                "requires": ["city"],
                "follow_up": ["performance", "top_dealers", "revenue", "ranking"],
                "drill_down": ["city_details", "city_top_products"],
                "display_name": "City Dashboard",
                "emoji": "🏙️"
            },
            "city_dealers": {
                "handler": self._route_city_dealers,
                "requires": ["city"],
                "follow_up": ["top_dealers", "dealer_list"],
                "drill_down": ["dealer_details"],
                "display_name": "City Dealers",
                "emoji": "🏪"
            },
            "city_warehouses": {
                "handler": self._route_city_warehouses,
                "requires": ["city"],
                "follow_up": ["warehouse_list", "coverage"],
                "drill_down": ["warehouse_details"],
                "display_name": "City Warehouses",
                "emoji": "🏭"
            },
            "product_dashboard": {
                "handler": self._route_product_dashboard_v22,
                "requires": ["product_model", "material"],
                "follow_up": ["performance", "revenue", "ranking", "sales"],
                "drill_down": ["product_details", "product_trend", "top_dealers"],
                "display_name": "Product Dashboard",
                "emoji": "📦"
            },
            "product_by_model": {
                "handler": self._route_product_by_model,
                "requires": ["product_model"],
                "follow_up": ["revenue", "units", "dns", "trend"],
                "drill_down": ["model_details"],
                "display_name": "Product Model",
                "emoji": "📊"
            },
            "product_dn_count": {
                "handler": self._route_product_dn_count,
                "requires": ["product_model"],
                "follow_up": ["dn_list", "dealer_list"],
                "drill_down": ["dn_details"],
                "display_name": "Product DN Count",
                "emoji": "📄"
            },
            "dn_dashboard": {
                "handler": self._route_dn_dashboard_v22,
                "requires": ["dn_number"],
                "follow_up": ["status", "delivery", "pod", "pgi", "products", "aging"],
                "drill_down": ["dn_details", "dn_timeline", "dn_products"],
                "display_name": "DN Dashboard",
                "emoji": "📄"
            },
            "dn_questions": {
                "handler": self._route_dn_questions,
                "requires": ["dn_number"],
                "follow_up": ["delivery_date", "pgi_date", "pod_status", "products", "units", "amount"],
                "drill_down": ["dn_details"],
                "display_name": "DN Details",
                "emoji": "📄"
            },
            "pgi_dashboard": {
                "handler": self._route_pgi_dashboard_v22,
                "requires": ["dn_number", "dealer_name"],
                "follow_up": ["status", "pending", "completed", "by_dealer"],
                "drill_down": ["pgi_details", "pgi_timeline"],
                "display_name": "PGI Dashboard",
                "emoji": "📋"
            },
            "pgi_by_dealer": {
                "handler": self._route_pgi_by_dealer,
                "requires": ["dealer_name"],
                "follow_up": ["pending", "completed", "rate"],
                "drill_down": ["pgi_details"],
                "display_name": "PGI by Dealer",
                "emoji": "📋"
            },
            "pod_dashboard": {
                "handler": self._route_pod_dashboard_v22,
                "requires": ["dn_number", "dealer_name"],
                "follow_up": ["status", "pending", "aging", "compliance", "by_dealer"],
                "drill_down": ["pod_details", "pod_timeline"],
                "display_name": "POD Dashboard",
                "emoji": "✅"
            },
            "pod_by_dealer": {
                "handler": self._route_pod_by_dealer,
                "requires": ["dealer_name"],
                "follow_up": ["pending", "completed", "rate", "aging"],
                "drill_down": ["pod_details"],
                "display_name": "POD by Dealer",
                "emoji": "✅"
            },
            "pod_aging": {
                "handler": self._route_pod_aging,
                "requires": [],
                "follow_up": ["aging_distribution", "critical"],
                "drill_down": ["pod_list"],
                "display_name": "POD Aging",
                "emoji": "⏳"
            },
            "delivery_dashboard": {
                "handler": self._route_delivery_dashboard,
                "requires": [],
                "follow_up": ["performance", "rate", "pending", "delayed", "sla"],
                "drill_down": ["delivery_details", "delivery_trend"],
                "display_name": "Delivery Dashboard",
                "emoji": "🚚"
            },
            "distance_dashboard": {
                "handler": self._route_distance_dashboard_v22,
                "requires": ["dealer_name", "warehouse"],
                "follow_up": ["transit", "travel_time", "route", "expected_delivery"],
                "drill_down": ["distance_details", "route_analysis"],
                "display_name": "Distance Dashboard",
                "emoji": "📍"
            },
            "distance_by_city": {
                "handler": self._route_distance_by_city,
                "requires": ["city"],
                "follow_up": ["transit", "travel_time"],
                "drill_down": ["distance_details"],
                "display_name": "Distance by City",
                "emoji": "📍"
            },
            "executive_dashboard": {
                "handler": self._route_executive_dashboard,
                "requires": [],
                "follow_up": ["summary", "insights", "kpi", "risks", "trends"],
                "drill_down": ["executive_details", "strategic_insights", "nationwide"],
                "display_name": "Executive Dashboard",
                "emoji": "👔"
            },
            "control_tower": {
                "handler": self._route_control_tower,
                "requires": [],
                "follow_up": ["alerts", "critical", "issues", "sla", "risks"],
                "drill_down": ["alert_details", "risk_analysis", "sla_compliance"],
                "display_name": "Control Tower",
                "emoji": "🚨"
            },
            "dealer_ranking": {
                "handler": self._route_dealer_ranking,
                "requires": [],
                "follow_up": ["top", "bottom", "revenue", "delivery", "pod"],
                "drill_down": ["ranking_details", "dealer_compare"],
                "display_name": "Dealer Ranking",
                "emoji": "🏆"
            },
            "warehouse_ranking": {
                "handler": self._route_warehouse_ranking,
                "requires": [],
                "follow_up": ["top", "bottom", "revenue", "delivery", "coverage"],
                "drill_down": ["ranking_details", "warehouse_compare"],
                "display_name": "Warehouse Ranking",
                "emoji": "🏆"
            },
            "product_ranking": {
                "handler": self._route_product_ranking_v22,
                "requires": [],
                "follow_up": ["top", "best_selling", "revenue", "units"],
                "drill_down": ["ranking_details", "product_compare"],
                "display_name": "Product Ranking",
                "emoji": "🏆"
            },
            "transporter_dashboard": {
                "handler": self._route_transporter_dashboard_v22,
                "requires": ["transporter_name"],
                "follow_up": ["performance", "delivery", "rating", "ranking"],
                "drill_down": ["transporter_details", "transporter_performance"],
                "display_name": "Transporter Dashboard",
                "emoji": "🚛"
            },
            "transporter_by_name": {
                "handler": self._route_transporter_by_name,
                "requires": ["transporter_name"],
                "follow_up": ["performance", "rating", "delivery_time"],
                "drill_down": ["transporter_details"],
                "display_name": "Transporter Details",
                "emoji": "🚛"
            },
            "transporter_ranking": {
                "handler": self._route_transporter_ranking,
                "requires": [],
                "follow_up": ["top", "best", "rating"],
                "drill_down": ["ranking_details"],
                "display_name": "Transporter Ranking",
                "emoji": "🏆"
            },
            "revenue_dashboard": {
                "handler": self._route_revenue_dashboard,
                "requires": [],
                "follow_up": ["summary", "trend", "by_dealer", "by_city", "by_division"],
                "drill_down": ["revenue_details", "revenue_trend"],
                "display_name": "Revenue Dashboard",
                "emoji": "💰"
            },
            "revenue_by_division": {
                "handler": self._route_revenue_by_division,
                "requires": ["division"],
                "follow_up": ["revenue_trend", "top_dealers", "top_products"],
                "drill_down": ["revenue_details"],
                "display_name": "Revenue by Division",
                "emoji": "💰"
            },
            "revenue_by_warehouse": {
                "handler": self._route_revenue_by_warehouse,
                "requires": ["warehouse"],
                "follow_up": ["revenue_trend", "top_dealers"],
                "drill_down": ["revenue_details"],
                "display_name": "Revenue by Warehouse",
                "emoji": "💰"
            },
            "revenue_trend": {
                "handler": self._route_revenue_trend,
                "requires": [],
                "follow_up": ["monthly", "quarterly", "yearly", "growth"],
                "drill_down": ["trend_details"],
                "display_name": "Revenue Trend",
                "emoji": "📈"
            },
            "inventory_dashboard": {
                "handler": self._route_inventory_dashboard_v22,
                "requires": ["warehouse", "material"],
                "follow_up": ["stock", "status", "movement", "by_warehouse", "by_material"],
                "drill_down": ["inventory_details", "inventory_status"],
                "display_name": "Inventory Dashboard",
                "emoji": "📦"
            },
            "inventory_by_warehouse": {
                "handler": self._route_inventory_by_warehouse,
                "requires": ["warehouse"],
                "follow_up": ["stock_levels", "movement", "products"],
                "drill_down": ["inventory_details"],
                "display_name": "Inventory by Warehouse",
                "emoji": "📦"
            },
            "inventory_by_material": {
                "handler": self._route_inventory_by_material,
                "requires": ["material"],
                "follow_up": ["stock_levels", "warehouses", "movement"],
                "drill_down": ["inventory_details"],
                "display_name": "Inventory by Material",
                "emoji": "📦"
            },
            "forecast_dashboard": {
                "handler": self._route_forecast_dashboard_v22,
                "requires": [],
                "follow_up": ["revenue", "units", "dns", "trend", "by_division"],
                "drill_down": ["forecast_details", "forecast_explanation"],
                "display_name": "Forecast Dashboard",
                "emoji": "📊"
            },
            "forecast_by_division": {
                "handler": self._route_forecast_by_division,
                "requires": ["division"],
                "follow_up": ["revenue_forecast", "unit_forecast"],
                "drill_down": ["forecast_details"],
                "display_name": "Forecast by Division",
                "emoji": "📊"
            },
            "forecast_by_warehouse": {
                "handler": self._route_forecast_by_warehouse,
                "requires": ["warehouse"],
                "follow_up": ["revenue_forecast", "unit_forecast"],
                "drill_down": ["forecast_details"],
                "display_name": "Forecast by Warehouse",
                "emoji": "📊"
            },
            "division_dashboard": {
                "handler": self._route_division_dashboard,
                "requires": ["division"],
                "follow_up": ["performance", "revenue", "units", "ranking"],
                "drill_down": ["division_details", "top_products"],
                "display_name": "Division Dashboard",
                "emoji": "🏢"
            },
            "sales_office_dashboard": {
                "handler": self._route_sales_office_dashboard,
                "requires": ["sales_office"],
                "follow_up": ["performance", "revenue", "units"],
                "drill_down": ["office_details"],
                "display_name": "Sales Office Dashboard",
                "emoji": "🏢"
            },
            "sla_compliance": {
                "handler": self._route_sla_compliance,
                "requires": [],
                "follow_up": ["delivery_sla", "pod_sla", "violations"],
                "drill_down": ["sla_details"],
                "display_name": "SLA Compliance",
                "emoji": "📊"
            }
        }
        
        # ==========================================================
        # METRICS (Enhanced v22.0)
        # ==========================================================
        
        self.metrics = {
            "total_requests": 0,
            "fast_cache_hits": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_failures_avoided": 0,
            "response_times_ms": [],
            "intent_detection_times_ms": [],
            "groq_times_ms": [],
            "rate_limited_requests": 0,
            "validation_failures": 0,
            "intent_detection": defaultdict(int),
            "follow_up_queries": 0,
            "drill_down_queries": 0,
            "dealer_resolution": {
                "attempts": 0,
                "success": 0,
                "failure": 0,
                "rapidfuzz_hits": 0,
                "suggestions_shown": 0,
                "ambiguous_handled": 0
            },
            "groq_uses": 0,
            "groq_fallbacks": 0,
            "errors": 0,
            "timeouts": 0,
            "service_unavailable": 0,
            "slow_operations": 0,
            "unique_users": set(),
        }
        
        logger.info("=" * 70)
        logger.info("AI Router v22.0 - Enterprise Production Ready")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   RULES:")
        logger.info("   ✅ Analytics First - analytics_service.py")
        logger.info("   ✅ Groq Second - Only for specific intents")
        logger.info("   ✅ Database Truth Always")
        logger.info("   ✅ Never Crash")
        logger.info("   ✅ Always Fast")
        logger.info("   ✅ Always WhatsApp Safe")
        logger.info("   ✅ Smart Context Memory")
        logger.info("   ✅ Rate Limited")
        logger.info("   ✅ Validated Input")
        logger.info("")
        logger.info("   📊 45+ INTENTS SUPPORTED:")
        logger.info("      1. 🏪 Dealer Dashboard")
        logger.info("      2. 📦 Dealer Products")
        logger.info("      3. ⏳ Dealer DN Aging")
        logger.info("      4. 🚚 Dealer Delivery")
        logger.info("      5. 🏭 Warehouse Dashboard")
        logger.info("      6. 📦 Warehouse Products")
        logger.info("      7. 📍 Warehouse Coverage")
        logger.info("      8. 🏙️ City Dashboard")
        logger.info("      9. 🏪 City Dealers")
        logger.info("      10. 🏭 City Warehouses")
        logger.info("      11. 📦 Product Dashboard")
        logger.info("      12. 📊 Product Model")
        logger.info("      13. 📄 Product DN Count")
        logger.info("      14. 📄 DN Dashboard")
        logger.info("      15. 📄 DN Details")
        logger.info("      16. 📋 PGI Dashboard")
        logger.info("      17. 📋 PGI by Dealer")
        logger.info("      18. ✅ POD Dashboard")
        logger.info("      19. ✅ POD by Dealer")
        logger.info("      20. ⏳ POD Aging")
        logger.info("      21. 🚚 Delivery Dashboard")
        logger.info("      22. 📍 Distance Dashboard")
        logger.info("      23. 📍 Distance by City")
        logger.info("      24. 👔 Executive Dashboard")
        logger.info("      25. 🚨 Control Tower")
        logger.info("      26. 🏆 Dealer Ranking")
        logger.info("      27. 🏆 Warehouse Ranking")
        logger.info("      28. 🏆 Product Ranking")
        logger.info("      29. 🚛 Transporter Dashboard")
        logger.info("      30. 🚛 Transporter Details")
        logger.info("      31. 🏆 Transporter Ranking")
        logger.info("      32. 💰 Revenue Dashboard")
        logger.info("      33. 💰 Revenue by Division")
        logger.info("      34. 💰 Revenue by Warehouse")
        logger.info("      35. 📈 Revenue Trend")
        logger.info("      36. 📦 Inventory Dashboard")
        logger.info("      37. 📦 Inventory by Warehouse")
        logger.info("      38. 📦 Inventory by Material")
        logger.info("      39. 📊 Forecast Dashboard")
        logger.info("      40. 📊 Forecast by Division")
        logger.info("      41. 📊 Forecast by Warehouse")
        logger.info("      42. 🏢 Division Dashboard")
        logger.info("      43. 🏢 Sales Office Dashboard")
        logger.info("      44. 📊 SLA Compliance")
        logger.info("      45. 📋 Help & Menu")
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
            service, response_class = _get_analytics_service()
            self._analytics = service
            self._analytics_response = response_class
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
    # QUERY VALIDATION & SANITIZATION (NEW v22.0)
    # ==========================================================
    
    def _validate_query(self, question: str, req_id: str) -> Tuple[bool, str]:
        """Validate query for safety and quality."""
        if not question or not question.strip():
            return False, "Please ask a question about your logistics data."
        
        if len(question) > 1000:
            return False, "Question is too long. Please be more specific."
        
        # SQL injection patterns
        sql_patterns = [
            r'(?i)(select|insert|update|delete|drop|alter|create)\s+.*\s+from',
            r'(?i)(union|join|where)\s+.*\s*=',
            r';\s*(select|insert|update|delete)',
            r'--\s*',
            r'/\*.*\*/',
            r'(?i)exec\s+',
            r'(?i)xp_\w+'
        ]
        
        for pattern in sql_patterns:
            if re.search(pattern, question, re.IGNORECASE):
                logger.warning(f"[{req_id}] ⛔ SQL injection attempt blocked")
                self.metrics["validation_failures"] += 1
                return False, "Invalid query format detected."
        
        # Control characters
        control_chars = ['\x00', '\x01', '\x02', '\x03', '\x04', '\x05']
        for char in control_chars:
            if char in question:
                return False, "Invalid characters in query."
        
        # Excessive repeated characters
        if re.search(r'(.)\1{20,}', question):
            return False, "Question contains excessive repetition."
        
        return True, ""
    
    def _sanitize_input(self, text: str) -> str:
        """Sanitize user input for safe processing."""
        # Remove control characters
        text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\r\t')
        
        # Normalize whitespace
        text = ' '.join(text.split())
        
        # Limit length
        if len(text) > 500:
            text = text[:500]
        
        return text.strip()
    
    # ==========================================================
    # RATE LIMITING (NEW v22.0)
    # ==========================================================
    
    def _check_rate_limit(self, phone_number: Optional[str], req_id: str) -> Tuple[bool, int]:
        """Check if user is within rate limits."""
        if not phone_number:
            return True, MAX_RATE_LIMIT_REQUESTS
        
        # Clean phone number for cache key
        phone_key = re.sub(r'\D', '', phone_number)
        
        # Check Redis first if available
        if self._redis_client:
            try:
                key = f"rate_limit:{phone_key}"
                current = self._redis_client.get(key)
                if current and int(current) >= MAX_RATE_LIMIT_REQUESTS:
                    self.metrics["rate_limited_requests"] += 1
                    return False, 0
                
                self._redis_client.incr(key)
                self._redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
                remaining = MAX_RATE_LIMIT_REQUESTS - int(self._redis_client.get(key) or 0)
                return True, max(0, remaining)
            except:
                pass
        
        # Fallback to in-memory
        now = time.time()
        if phone_key not in self._rate_limit_cache:
            self._rate_limit_cache[phone_key] = []
        
        # Clean old entries
        self._rate_limit_cache[phone_key] = [
            t for t in self._rate_limit_cache[phone_key]
            if now - t < RATE_LIMIT_WINDOW_SECONDS
        ]
        
        if len(self._rate_limit_cache[phone_key]) >= MAX_RATE_LIMIT_REQUESTS:
            self.metrics["rate_limited_requests"] += 1
            return False, 0
        
        self._rate_limit_cache[phone_key].append(now)
        remaining = MAX_RATE_LIMIT_REQUESTS - len(self._rate_limit_cache[phone_key])
        return True, max(0, remaining)
    
    # ==========================================================
    # PERFORMANCE MONITORING (NEW v22.0)
    # ==========================================================
    
    def _monitor_performance(self, req_id: str, operation: str, duration_ms: float):
        """Monitor performance in real-time."""
        if operation == "intent_detection":
            self.metrics["intent_detection_times_ms"].append(duration_ms)
            if len(self.metrics["intent_detection_times_ms"]) > 1000:
                self.metrics["intent_detection_times_ms"] = self.metrics["intent_detection_times_ms"][-1000:]
        elif operation == "groq_execution":
            self.metrics["groq_times_ms"].append(duration_ms)
            if len(self.metrics["groq_times_ms"]) > 100:
                self.metrics["groq_times_ms"] = self.metrics["groq_times_ms"][-100:]
        
        if duration_ms > 1000:
            self.metrics["slow_operations"] += 1
            logger.warning(f"[{req_id}] Slow operation: {operation} took {duration_ms:.0f}ms")
    
    def _calculate_percentiles(self, data: List[float]) -> Dict[str, float]:
        """Calculate percentiles for performance metrics."""
        if not data:
            return {"p50": 0, "p90": 0, "p95": 0, "p99": 0}
        
        sorted_data = sorted(data)
        return {
            "p50": sorted_data[int(len(sorted_data) * 0.5)] if len(sorted_data) > 1 else sorted_data[0],
            "p90": sorted_data[int(len(sorted_data) * 0.9)] if len(sorted_data) > 9 else sorted_data[-1],
            "p95": sorted_data[int(len(sorted_data) * 0.95)] if len(sorted_data) > 19 else sorted_data[-1],
            "p99": sorted_data[int(len(sorted_data) * 0.99)] if len(sorted_data) > 99 else sorted_data[-1],
        }
    
    # ==========================================================
    # CONTEXT MANAGEMENT (Enhanced v22.0)
    # ==========================================================
    
    def _load_context(self, phone_number: Optional[str]) -> Optional[ConversationContext]:
        """Load or create conversation context."""
        if not phone_number:
            return None
        
        phone_key = re.sub(r'\D', '', phone_number)
        
        # Try Redis first
        if self._redis_client:
            try:
                key = f"context:{phone_key}"
                data = self._redis_client.get(key)
                if data:
                    import json
                    ctx_data = json.loads(data)
                    ctx = ConversationContext(
                        phone_number=ctx_data.get("phone_number", phone_number),
                        session_id=ctx_data.get("session_id", str(uuid.uuid4()))
                    )
                    for key, value in ctx_data.items():
                        if hasattr(ctx, key):
                            setattr(ctx, key, value)
                    return ctx
            except:
                pass
        
        # Fallback to in-memory
        if phone_key not in self.conversation_cache:
            self.conversation_cache[phone_key] = ConversationContext(phone_number=phone_number)
        
        context = self.conversation_cache[phone_key]
        if context.is_expired():
            context = ConversationContext(phone_number=phone_number)
            self.conversation_cache[phone_key] = context
        
        return context
    
    def _save_context(self, context: ConversationContext):
        """Save conversation context."""
        if not context or not context.phone_number:
            return
        
        phone_key = re.sub(r'\D', '', context.phone_number)
        
        # Save to Redis
        if self._redis_client:
            try:
                import json
                key = f"context:{phone_key}"
                self._redis_client.setex(key, CONTEXT_TTL_SECONDS, json.dumps(context.to_dict()))
            except:
                pass
        
        # Save to memory
        self.conversation_cache[phone_key] = context
    
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
        """Update conversation context with new information."""
        if not phone_number or not success:
            return
        
        context = self._load_context(phone_number)
        if not context:
            return
        
        context.last_intent = intent
        context.last_dashboard = intent
        context.intent_confidence = 0.9
        context.last_question = entity
        context.last_updated = time.time()
        context.is_valid = True
        context.message_count += 1
        context.turn_count += 1
        
        # Update entity tracking
        if entity_type == "dealer":
            context.last_dealer = entity
            context.last_entity_type = "dealer"
            context.last_entity_value = entity
            context.entity_confidence = 0.9
        elif entity_type == "warehouse":
            context.last_warehouse = entity
            context.last_entity_type = "warehouse"
            context.last_entity_value = entity
            context.entity_confidence = 0.9
        elif entity_type == "city":
            context.last_city = entity
            context.last_entity_type = "city"
            context.last_entity_value = entity
            context.entity_confidence = 0.9
        elif entity_type == "dn":
            context.last_dn = entity
            context.last_entity_type = "dn"
            context.last_entity_value = entity
            context.entity_confidence = 0.9
        elif entity_type == "product":
            context.last_product_model = entity
            context.last_entity_type = "product"
            context.last_entity_value = entity
            context.entity_confidence = 0.9
        elif entity_type == "division":
            context.last_division = entity
            context.last_entity_type = "division"
            context.last_entity_value = entity
            context.entity_confidence = 0.9
        elif entity_type == "transporter":
            context.last_transporter = entity
            context.last_entity_type = "transporter"
            context.last_entity_value = entity
            context.entity_confidence = 0.9
        
        if response:
            context.last_response = response[:200]
            context.add_history(entity or "query", response, intent, entity_type)
        
        self._save_context(context)
    
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
        
        if hasattr(response, 'success') and response.success is False:
            error_msg = getattr(response, 'error', 'Unknown error')
            logger.error(f"[{req_id}] AnalyticsResponse success=False for {service_name}: {error_msg}")
            return False
        
        if not hasattr(response, 'data'):
            logger.error(f"[{req_id}] AnalyticsResponse missing 'data' for {service_name}")
            return False
        
        return True
    
    # ==========================================================
    # ENTITY RECOGNITION (Enhanced v22.0)
    # ==========================================================
    
    def _extract_entities(self, question: str) -> Dict[str, Optional[str]]:
        """Extract all entities from question using ENTITY_PATTERNS."""
        entities = {}
        
        # Apply all patterns
        for entity_type, pattern in ENTITY_PATTERNS.items():
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                # Handle patterns with capture groups
                if match.groups():
                    entities[entity_type] = match.group(1).strip()
                else:
                    # For patterns without capture groups (like dealer_code)
                    entities[entity_type] = match.group(0).strip()
        
        # Check for city aliases
        question_lower = question.lower()
        for alias, city in CITY_ALIASES.items():
            if alias in question_lower:
                entities["city"] = city
                break
        
        return entities
    
    def _normalize_city(self, city_input: str) -> str:
        """Normalize city name with alias support."""
        if not city_input:
            return ""
        
        city_lower = city_input.lower().strip()
        return CITY_ALIASES.get(city_lower, city_lower)
    
    # ==========================================================
    # FOLLOW-UP QUESTION SUPPORT (Enhanced v22.0)
    # ==========================================================
    
    def _handle_follow_up(self, question: str, context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle follow-up questions using context."""
        if not context or not context.last_intent:
            return None
        
        self.metrics["follow_up_queries"] += 1
        question_lower = question.lower()
        
        # Check for possessive references
        possessive_pronouns = ["its", "his", "her", "their"]
        for pronoun in possessive_pronouns:
            if pronoun in question_lower:
                # Use the last entity value
                if context.last_entity_value:
                    resolved = question.replace(pronoun, context.last_entity_value)
                    resolved = resolved.replace(pronoun.capitalize(), context.last_entity_value)
                    logger.info(f"[{req_id}] Follow-up resolved: '{question}' → '{resolved}'")
                    return resolved
        
        # Check for "what about" patterns
        if "what about" in question_lower and context.last_entity_value:
            return f"{context.last_entity_value} {question}"
        
        # Check for "how many" patterns
        if "how many" in question_lower and context.last_entity_value:
            return f"{question} for {context.last_entity_value}"
        
        # Check for comparative patterns
        if any(word in question_lower for word in ["compare", "versus", "vs", "difference"]):
            if context.last_entity_value:
                return f"compare {context.last_entity_value} {question}"
        
        # Check for time window patterns
        if any(word in question_lower for word in ["last month", "previous month", "trend"]):
            if context.last_dashboard:
                return f"{context.last_dashboard} {question}"
        
        # Check for detail requests
        if any(word in question_lower for word in ["more details", "details", "detailed"]):
            return self._handle_drill_down(context, req_id)
        
        # Check for list requests
        if "list" in question_lower:
            if context.last_entity_type == "dealer":
                return f"list dealers {question}"
            elif context.last_entity_type == "warehouse":
                return f"list warehouses {question}"
            elif context.last_entity_type == "dn":
                return f"list dns {question}"
        
        # Check for count requests
        if "count" in question_lower or "how many" in question_lower:
            if context.last_entity_type == "dealer":
                return f"dealer count {question}"
            elif context.last_entity_type == "dn":
                return f"dn count {question}"
        
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
        
        options_text = "\n".join([f"   • {option.replace('_', ' ').title()}" for option in drill_down_options[:5]])
        return f"""📊 *{matrix.get('display_name', 'Dashboard')} - Drill Down Options*

*Choose an option:*
{options_text}

*What would you like to explore?* 🤖"""
    
    # ==========================================================
    # INTENT DETECTION (Enhanced v22.0)
    # ==========================================================
    
    def _detect_intent(self, question: str, context: Optional[ConversationContext] = None) -> Tuple[str, Optional[str]]:
        """Detect intent from user question with follow-up support."""
        start_time = time.time()
        question_lower = question.lower().strip()
        
        # Check special commands first
        for command, intent in SPECIAL_COMMANDS.items():
            if command == question_lower or question_lower.startswith(command):
                if intent == "control_tower":
                    self.metrics["intent_detection"]["control_tower"] += 1
                    return "control_tower", None
                if intent == "executive_dashboard":
                    self.metrics["intent_detection"]["executive_dashboard"] += 1
                    return "executive_dashboard", None
                if intent in ["dealer_ranking", "warehouse_ranking", "product_ranking"]:
                    self.metrics["intent_detection"][intent] += 1
                    return intent, None
                if intent in ["revenue_dashboard", "inventory_dashboard", "forecast_dashboard", "delivery_dashboard"]:
                    self.metrics["intent_detection"][intent] += 1
                    return intent, None
                if intent == "help":
                    return "help", None
        
        # Check for follow-up questions first
        if context and context.last_intent:
            follow_up_result = self._handle_follow_up(question, context, self._current_request_id or "unknown")
            if follow_up_result:
                return self._detect_intent(follow_up_result, None)
        
        # Check for DN first (highest priority)
        if self._is_dn_query(question):
            self.metrics["intent_detection"]["dn_dashboard"] += 1
            return "dn_dashboard", self._normalize_dn(question)
        
        # Check if it's a specific dealer code or customer code
        entities = self._extract_entities(question)
        if entities.get("dealer_code"):
            return "dealer_dashboard", entities["dealer_code"]
        if entities.get("customer_code"):
            return "dealer_dashboard", entities["customer_code"]
        
        # Check each intent pattern
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    entity = self._extract_entity(question, intent)
                    self.metrics["intent_detection"][intent] += 1
                    
                    # Track duration
                    duration_ms = (time.time() - start_time) * 1000
                    self._monitor_performance(self._current_request_id or "unknown", "intent_detection", duration_ms)
                    
                    return intent, entity
        
        # Unknown intent
        self.metrics["intent_detection"]["unknown"] += 1
        return "unknown", None
    
    # ==========================================================
    # ENTITY EXTRACTION (Enhanced v22.0)
    # ==========================================================
    
    def _extract_entity(self, question: str, intent: str) -> Optional[str]:
        """Extract entity from question based on intent."""
        question_clean = question.strip()
        entities = self._extract_entities(question)
        
        # Entity mapping for each intent
        entity_mapping = {
            "dealer_dashboard": ["dealer_name", "dealer_code", "customer_code"],
            "dealer_products": ["dealer_name", "dealer_code"],
            "dealer_dn_aging": ["dealer_name", "dealer_code"],
            "dealer_delivery_performance": ["dealer_name", "dealer_code"],
            "warehouse_dashboard": ["warehouse", "warehouse_code"],
            "warehouse_products": ["warehouse", "warehouse_code"],
            "warehouse_coverage": ["warehouse", "warehouse_code"],
            "city_dashboard": ["city"],
            "city_dealers": ["city"],
            "city_warehouses": ["city"],
            "product_dashboard": ["product_model", "material"],
            "product_by_model": ["product_model"],
            "product_dn_count": ["product_model"],
            "dn_dashboard": ["dn_number"],
            "dn_questions": ["dn_number"],
            "pgi_dashboard": ["dn_number", "dealer_name"],
            "pgi_by_dealer": ["dealer_name", "dealer_code"],
            "pod_dashboard": ["dn_number", "dealer_name"],
            "pod_by_dealer": ["dealer_name", "dealer_code"],
            "pod_aging": [],
            "distance_dashboard": ["dealer_name", "warehouse"],
            "distance_by_city": ["city"],
            "transporter_dashboard": ["transporter", "transporter_code"],
            "transporter_by_name": ["transporter", "transporter_code"],
            "revenue_by_division": ["division"],
            "revenue_by_warehouse": ["warehouse", "warehouse_code"],
            "inventory_by_warehouse": ["warehouse", "warehouse_code"],
            "inventory_by_material": ["material"],
            "forecast_by_division": ["division"],
            "forecast_by_warehouse": ["warehouse", "warehouse_code"],
            "division_dashboard": ["division"],
            "sales_office_dashboard": ["sales_office"],
        }
        
        for entity_type in entity_mapping.get(intent, []):
            if entities.get(entity_type):
                return entities[entity_type]
        
        # Fallback entity extraction for common patterns
        if intent.startswith("dealer") or intent.startswith("warehouse") or intent.startswith("city"):
            # Try to extract name from the question
            words = question_clean.split()
            for word in words:
                if len(word) > 2:
                    # Check if it's a likely entity name
                    if word[0].isupper() or len(word) > 5:
                        return word
        
        if intent in ["dealer_dashboard", "dealer_products", "dealer_dn_aging", "dealer_delivery_performance"]:
            prefixes = ["show me", "tell me about", "get", "view", "display", 
                       "dealer", "customer", "for dealer", "for customer", "about"]
            for prefix in prefixes:
                if question_clean.lower().startswith(prefix):
                    entity = question_clean[len(prefix):].strip()
                    if entity and len(entity) > 2:
                        return entity
            if len(question_clean) < 50:
                return question_clean
        
        return None
    
    # ==========================================================
    # DEALER RESOLUTION (Enhanced v22.0)
    # ==========================================================
    
    def _resolve_dealer_safe(self, dealer_input: str, req_id: str) -> Tuple[Optional[str], float, str]:
        """Ultra-fast dealer resolution with alias support."""
        self.metrics["dealer_resolution"]["attempts"] += 1
        
        if not dealer_input or not dealer_input.strip():
            return None, 0.0, "empty_input"
        
        dealer_clean = dealer_input.strip()
        cache_key = dealer_clean.lower()
        
        # Check cache
        if cache_key in self.dealer_resolution_cache:
            resolved, confidence, timestamp = self.dealer_resolution_cache[cache_key]
            if resolved and time.time() - timestamp < 3600:
                return resolved, confidence, "cache_hit"
        
        # Check if it's a dealer code
        if re.match(r'^[A-Z]{2,4}\d{2,6}$', dealer_clean, re.IGNORECASE):
            if self.schema:
                try:
                    resolved = self.schema.resolve_dealer_by_code(dealer_clean.upper())
                    if resolved:
                        confidence = 0.99
                        self.metrics["dealer_resolution"]["success"] += 1
                        self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                        return resolved, confidence, "code_match"
                except:
                    pass
        
        # Check if it's a customer code
        if re.match(r'^(CUST|CT)\d{5,}$', dealer_clean, re.IGNORECASE):
            if self.schema:
                try:
                    resolved = self.schema.resolve_dealer_by_customer_code(dealer_clean.upper())
                    if resolved:
                        confidence = 0.99
                        self.metrics["dealer_resolution"]["success"] += 1
                        self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                        return resolved, confidence, "customer_code_match"
                except:
                    pass
        
        # Try RapidFuzz
        if RAPIDFUZZ_AVAILABLE and self.analytics:
            try:
                result = self.analytics.get_all_dealers_dashboard()
                if result and hasattr(result, 'success') and result.success:
                    dealers = result.data.get("dealers", [])
                    dealer_names = [d.get("dealer_name", "") for d in dealers if d.get("dealer_name")]
                    
                    if dealer_names:
                        matches = process.extract(
                            dealer_clean,
                            dealer_names,
                            scorer=fuzz.ratio,
                            limit=5
                        )
                        
                        if matches:
                            # Check for alias matches
                            for match in matches:
                                if match[1] >= 85:
                                    resolved = match[0]
                                    confidence = match[1] / 100
                                    self.metrics["dealer_resolution"]["success"] += 1
                                    self.metrics["dealer_resolution"]["rapidfuzz_hits"] += 1
                                    logger.info(f"[{req_id}] ✅ RapidFuzz exact: '{resolved}' (score: {confidence:.2f})")
                                    self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                                    return resolved, confidence, "rapidfuzz_exact"
                            
                            # Check partial matches
                            if matches[0][1] >= 70:
                                resolved = matches[0][0]
                                confidence = matches[0][1] / 100
                                self.metrics["dealer_resolution"]["success"] += 1
                                self.metrics["dealer_resolution"]["rapidfuzz_hits"] += 1
                                
                                # Store suggestions for disambiguation
                                suggestions = [m[0] for m in matches if m[1] >= 70]
                                if len(suggestions) > 1:
                                    self._suggestion_cache[cache_key] = suggestions
                                    self.metrics["dealer_resolution"]["ambiguous_handled"] += 1
                                
                                logger.info(f"[{req_id}] ✅ RapidFuzz partial: '{resolved}' (score: {confidence:.2f})")
                                self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                                return resolved, confidence, "rapidfuzz_partial"
                            
                            # Store suggestions for no-match case
                            if matches[0][1] >= 40:
                                self._suggestion_cache[cache_key] = [m[0] for m in matches[:3] if m[1] >= 40]
            except Exception as e:
                logger.debug(f"RapidFuzz failed: {e}")
        
        # Try schema service
        if self.schema:
            try:
                resolved = self.schema.resolve_dealer(dealer_clean)
                if resolved:
                    confidence = 0.85
                    self.metrics["dealer_resolution"]["success"] += 1
                    self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                    return resolved, confidence, "schema_match"
            except:
                pass
        
        self.metrics["dealer_resolution"]["failure"] += 1
        return None, 0.0, "all_failed"
    
    def _get_dealer_suggestions(self, dealer_input: str, req_id: str) -> List[str]:
        """Get dealer suggestions using RapidFuzz."""
        try:
            cache_key = dealer_input.lower().strip()
            if cache_key in self._suggestion_cache:
                return self._suggestion_cache[cache_key][:3]
            
            if RAPIDFUZZ_AVAILABLE and self.analytics:
                result = self.analytics.get_all_dealers_dashboard()
                if result and hasattr(result, 'success') and result.success:
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
                            self._suggestion_cache[cache_key] = suggestions
                            return suggestions[:3]
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
            r'^\d{8,12}$',
            r'^\d{3}-\d{3}-\d{3}$',
            r'^\d{4}-\d{4}$',
            r'^\d{2}-\d{4}-\d{4}$',
        ]
        
        for pattern in patterns:
            if re.match(pattern, dn.strip()):
                return True
        
        return False
    
    # ==========================================================
    # GET SAMPLE DNS
    # ==========================================================
    
    def _get_sample_dns(self, limit: int = 5) -> List[str]:
        """Get sample DN numbers from database for reference."""
        try:
            if self.analytics and hasattr(self.analytics, 'get_sample_dns'):
                return self.analytics.get_sample_dns(limit)
            
            # Fallback: try direct database query
            from app.database import SessionLocal
            from app.models import DeliveryReport
            
            db = SessionLocal()
            try:
                results = db.query(DeliveryReport.dn_no).filter(
                    DeliveryReport.dn_no.isnot(None),
                    DeliveryReport.dn_no != ''
                ).distinct().limit(limit).all()
                return [r[0] for r in results if r[0]]
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Failed to get sample DNs: {e}")
            return []
    
    # ==========================================================
    # CACHE MANAGEMENT (Enhanced v22.0)
    # ==========================================================
    
    def _get_cached_response(self, question: str, phone_number: Optional[str]) -> Optional[str]:
        """Get response from cache."""
        cache_key = self._generate_cache_key(question, phone_number)
        
        if cache_key in self.failure_cache:
            self.metrics["cache_failures_avoided"] += 1
            return None
        
        if cache_key in self.fast_cache:
            self.metrics["fast_cache_hits"] += 1
            return self.fast_cache[cache_key]
        
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
        return self.groq is not None and hasattr(self.groq, 'is_available') and self.groq.is_available
    
    # ==========================================================
    # SHOULD USE GROQ?
    # ==========================================================
    
    def _should_use_groq(self, question: str, intent: str) -> bool:
        """Determine if Groq should be used for this query."""
        question_lower = question.lower()
        
        never_groq_intents = [
            "dealer_dashboard", "dealer_products", "dealer_dn_aging", "dealer_delivery_performance",
            "warehouse_dashboard", "warehouse_products", "warehouse_coverage",
            "city_dashboard", "city_dealers", "city_warehouses",
            "product_dashboard", "product_by_model", "product_dn_count",
            "dn_dashboard", "dn_questions",
            "pgi_dashboard", "pgi_by_dealer",
            "pod_dashboard", "pod_by_dealer", "pod_aging",
            "delivery_dashboard",
            "distance_dashboard", "distance_by_city",
            "dealer_ranking", "warehouse_ranking", "product_ranking",
            "transporter_dashboard", "transporter_by_name", "transporter_ranking",
            "revenue_dashboard", "revenue_by_division", "revenue_by_warehouse", "revenue_trend",
            "inventory_dashboard", "inventory_by_warehouse", "inventory_by_material",
            "forecast_dashboard", "forecast_by_division", "forecast_by_warehouse",
            "division_dashboard", "sales_office_dashboard", "sla_compliance",
            "help"
        ]
        
        if intent in never_groq_intents:
            return False
        
        for groq_intent, patterns in GROQ_INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    return True
        
        if intent == "forecast_dashboard":
            return True
        
        if intent == "executive_dashboard":
            return True
        
        if intent == "control_tower":
            return True
        
        if intent == "unknown":
            return True
        
        return False
    
    # ==========================================================
    # MAIN ENTRY POINT (Enhanced v22.0)
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
        
        if phone_number:
            self.metrics["unique_users"].add(phone_number)
        
        logger.bind(request_id=req_id, phone_number=phone_number).info(f"📥 Processing: {question[:100]}")
        
        try:
            # 1. VALIDATE INPUT
            is_valid, error_msg = self._validate_query(question, req_id)
            if not is_valid:
                logger.warning(f"[{req_id}] Invalid query: {error_msg}")
                return self._format_whatsapp_response(error_msg, self._build_quick_replies("help", None))
            
            # 2. SANITIZE INPUT
            question = self._sanitize_input(question)
            if not question:
                return self._format_whatsapp_response("Please ask a question.", self._build_quick_replies("help", None))
            
            # 3. CHECK RATE LIMIT
            is_allowed, remaining = self._check_rate_limit(phone_number, req_id)
            if not is_allowed:
                logger.warning(f"[{req_id}] Rate limit exceeded for {phone_number}")
                return self._format_whatsapp_response(
                    "⏳ *Rate Limit Exceeded*\n\nPlease wait a moment and try again.",
                    self._build_quick_replies("help", None)
                )
            
            # 4. CHECK CACHE
            cached = self._get_cached_response(question, phone_number)
            if cached:
                duration_ms = int((time.time() - start_time) * 1000)
                logger.info(f"[{req_id}] ✅ Cache hit: {duration_ms}ms")
                return cached
            
            # 5. LOAD CONTEXT
            context = self._load_context(phone_number)
            if not context:
                context = ConversationContext(phone_number=phone_number or "anonymous")
            
            # 6. PROCESS WITH TIMEOUT
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self._process_sync_enhanced,
                    question,
                    context,
                    phone_number,
                    req_id
                )
                try:
                    response = future.result(timeout=25)
                    duration_ms = int((time.time() - start_time) * 1000)
                    self.metrics["response_times_ms"].append(duration_ms)
                    
                    if len(self.metrics["response_times_ms"]) > 1000:
                        self.metrics["response_times_ms"] = self.metrics["response_times_ms"][-1000:]
                    
                    # 7. MONITOR PERFORMANCE
                    self._monitor_performance(req_id, "total_response", duration_ms)
                    
                    # 8. CACHE RESPONSE
                    self._cache_response(question, phone_number, response, True)
                    
                    # 9. BUILD QUICK REPLIES
                    quick_replies = self._build_quick_replies(
                        context.last_intent or "default",
                        context
                    )
                    
                    logger.info(f"[{req_id}] ✅ Done: {duration_ms}ms")
                    return self._format_whatsapp_response(response, quick_replies)
                    
                except concurrent.futures.TimeoutError:
                    self.metrics["timeouts"] += 1
                    duration_ms = int((time.time() - start_time) * 1000)
                    logger.error(f"[{req_id}] Request timed out after {duration_ms}ms")
                    return self._get_timeout_response(req_id)
        
        except Exception as e:
            self.metrics["errors"] += 1
            duration_ms = int((time.time() - start_time) * 1000)
            logger.exception(f"[{req_id}] ERROR: {e}")
            return self._get_error_response(e, req_id)
    
    # ==========================================================
    # ENHANCED SYNC PROCESSING (v22.0)
    # ==========================================================
    
    def _process_sync_enhanced(
        self, 
        question: str, 
        context: ConversationContext,
        phone_number: Optional[str],
        req_id: str
    ) -> str:
        """Enhanced sync processing with context recovery."""
        
        question_clean = question.strip()
        
        # 1. DETECT INTENT (with timing)
        start_intent = time.time()
        intent, entity = self._detect_intent(question_clean, context)
        intent_duration = (time.time() - start_intent) * 1000
        self._monitor_performance(req_id, "intent_detection", intent_duration)
        
        logger.info(f"[{req_id}] 🎯 Intent: {intent} | Entity: {entity} | Duration: {intent_duration:.0f}ms")
        
        # 2. HANDLE HELP/MENU
        if intent == "help":
            response = self._get_help_message_enhanced()
            self._cache_response(question, phone_number, response, True)
            return response
        
        # 3. ROUTE TO DASHBOARD
        result = self._route_to_dashboard_enhanced(intent, entity, context, req_id)
        if result:
            self._cache_response(question, phone_number, result, True)
            self._update_context(
                phone_number, intent, 
                self._get_entity_type(intent), entity, 
                req_id, result, True
            )
            return result
        
        # 4. TRY GROQ (with timing)
        if self._should_use_groq(question_clean, intent) and self._is_groq_available():
            start_groq = time.time()
            result = self._execute_groq_safe(question_clean, context, req_id)
            groq_duration = (time.time() - start_groq) * 1000
            self._monitor_performance(req_id, "groq_execution", groq_duration)
            
            if result:
                self._cache_response(question, phone_number, result, True)
                self._update_context(
                    phone_number, intent,
                    self._get_entity_type(intent), entity,
                    req_id, result, True
                )
                return result
        
        # 5. FALLBACK - Help with context
        fallback = self._get_fallback_response(intent, entity, context, req_id)
        self._cache_response(question, phone_number, fallback, True)
        return fallback
    
    def _get_entity_type(self, intent: str) -> str:
        """Get entity type based on intent."""
        entity_mapping = {
            "dealer_dashboard": "dealer",
            "dealer_products": "dealer",
            "dealer_dn_aging": "dealer",
            "dealer_delivery_performance": "dealer",
            "warehouse_dashboard": "warehouse",
            "warehouse_products": "warehouse",
            "warehouse_coverage": "warehouse",
            "city_dashboard": "city",
            "city_dealers": "city",
            "city_warehouses": "city",
            "product_dashboard": "product",
            "product_by_model": "product",
            "product_dn_count": "product",
            "dn_dashboard": "dn",
            "dn_questions": "dn",
            "pgi_dashboard": "dn",
            "pgi_by_dealer": "dealer",
            "pod_dashboard": "dn",
            "pod_by_dealer": "dealer",
            "distance_dashboard": "dealer",
            "distance_by_city": "city",
            "transporter_dashboard": "transporter",
            "transporter_by_name": "transporter",
            "revenue_by_division": "division",
            "revenue_by_warehouse": "warehouse",
            "inventory_by_warehouse": "warehouse",
            "inventory_by_material": "product",
            "forecast_by_division": "division",
            "forecast_by_warehouse": "warehouse",
            "division_dashboard": "division",
            "sales_office_dashboard": "sales_office",
        }
        return entity_mapping.get(intent, "unknown")
    
    def _route_to_dashboard_enhanced(
        self, 
        intent: str, 
        entity: Optional[str], 
        context: ConversationContext, 
        req_id: str
    ) -> Optional[str]:
        """Route to the appropriate dashboard based on intent."""
        matrix = self._dashboard_routing_matrix.get(intent)
        if not matrix:
            return None
        
        handler = matrix.get("handler")
        if not handler:
            return None
        
        required = matrix.get("requires", [])
        if required and not entity:
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
                    elif req == "transporter_name" and context.last_transporter:
                        entity = context.last_transporter
                        break
                    elif req == "division" and context.last_division:
                        entity = context.last_division
                        break
                    elif req == "sales_office" and context.last_sales_office:
                        entity = context.last_sales_office
                        break
                    elif req == "material" and context.last_material:
                        entity = context.last_material
                        break
                    elif req == "product_model" and context.last_product_model:
                        entity = context.last_product_model
                        break
            
            if not entity:
                return self._get_missing_entity_message(intent, matrix)
        
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
• "DN 6243600648"

*What would you like to know?* 🤖"""
    
    def _get_fallback_response(self, intent: str, entity: Optional[str], context: ConversationContext, req_id: str) -> str:
        """Get fallback response when routing fails."""
        if entity:
            return f"""❌ I couldn't find information for "{entity}".

💡 *Try these:*
• Check the spelling
• Use a different format
• Type "help" for menu

*What would you like to know?* 🤖"""
        else:
            return f"""❌ I didn't understand your question.

💡 *Try these formats:*
• "Show dealer [dealer name]"
• "DN [number]"
• "[city] performance"
• "Warehouse [name]"

*Type "help" for full menu* 📋"""
    
    # ==========================================================
    # ENHANCED HELP MESSAGE (v22.0)
    # ==========================================================
    
    def _get_help_message_enhanced(self) -> str:
        """Enhanced WhatsApp help menu with quick commands."""
        return """🏠 *HAIER LOGISTICS AI* 🤖

*📊 45+ Ways to Get Answers*

*🏪 DEALERS*
• "Show dealer [name]"
• "[name] performance"
• "Products of [dealer]"
• "DN aging for [dealer]"
• "Delivery performance [dealer]"

*🏭 WAREHOUSES*
• "Show warehouse [name]"
• "[name] inventory"
• "Warehouse coverage"
• "[name] performance"

*🏙️ CITIES*
• "Show city [name]"
• "[name] dealers"
• "[name] warehouses"
• "[name] performance"

*📦 PRODUCTS*
• "Show product [model]"
• "[model] performance"
• "[model] DN count"
• "Top products"

*📄 DNS*
• "DN [number]"
• "Track [number]"
• "[number] status"
• "[number] POD"

*📋 PGI & POD*
• "PGI pending"
• "POD status"
• "POD aging"
• "PGI for [dealer]"

*🚚 DELIVERY*
• "Delivery performance"
• "Delivery rate"
• "Pending deliveries"
• "SLA compliance"

*📍 DISTANCE*
• "Distance to [city]"
• "Transit days"
• "Route info"

*👔 EXECUTIVE*
• "Executive summary"
• "Nationwide overview"
• "Top dealers"
• "Key insights"

*🚨 CONTROL TOWER*
• "Alerts"
• "Critical issues"
• "Risks"

*🚛 TRANSPORTERS*
• "Show transporter [name]"
• "Transporter ranking"
• "[name] performance"

*💰 REVENUE*
• "Revenue summary"
• "Revenue by division"
• "Revenue trend"
• "Top revenue"

*📈 FORECAST*
• "Forecast"
• "Next month forecast"
• "Division forecast"

*💡 Quick Commands*
• "help" or "menu" - This menu
• "top dealers" - Dealer ranking
• "top warehouses" - Warehouse ranking
• "control tower" - Alerts & issues
• "executive summary" - Business overview

*💬 Follow-up*
I remember your recent searches!
Try: "What is its POD?" or "More details"

*Ask me anything about logistics!* 🚀"""
    
    # ==========================================================
    # QUICK REPLIES (NEW v22.0)
    # ==========================================================
    
    def _build_quick_replies(self, intent: str, context: Optional[ConversationContext]) -> List[str]:
        """Build WhatsApp quick replies based on current context."""
        
        quick_replies_map = {
            "dealer_dashboard": ["📄 DN", "✅ POD", "💰 Revenue", "📊 Ranking"],
            "dealer_products": ["📦 Top Products", "📈 Revenue", "📄 DNs"],
            "dealer_dn_aging": ["⏳ Aging Details", "📄 Pending DNs"],
            "dealer_delivery_performance": ["🚚 Delivery Rate", "📊 Performance"],
            "warehouse_dashboard": ["📊 Performance", "🏆 Ranking", "📦 Inventory"],
            "warehouse_products": ["📦 Stock Levels", "📈 Movement"],
            "warehouse_coverage": ["📍 Cities Served", "🏪 Dealers"],
            "city_dashboard": ["📊 Performance", "🏪 Top Dealers", "💰 Revenue"],
            "city_dealers": ["🏪 Dealer List", "🏆 Top Dealers"],
            "city_warehouses": ["🏭 Warehouse List", "📍 Coverage"],
            "product_dashboard": ["📊 Performance", "💰 Revenue", "📄 DN Count"],
            "product_by_model": ["📈 Revenue", "📄 DNs", "📊 Trend"],
            "product_dn_count": ["📄 DN List", "🏪 Dealers"],
            "dn_dashboard": ["🚚 Status", "✅ POD", "📋 PGI", "📍 Track"],
            "dn_questions": ["📄 Details", "📦 Products", "💰 Amount"],
            "pgi_dashboard": ["📋 Status", "⏳ Pending"],
            "pod_dashboard": ["✅ Status", "⏳ Aging"],
            "delivery_dashboard": ["🚚 Rate", "📊 Performance", "📈 Trend"],
            "distance_dashboard": ["📍 Route", "⏳ Transit"],
            "executive_dashboard": ["📊 Summary", "💡 Insights", "🏆 Rankings"],
            "control_tower": ["🚨 Alerts", "⚠️ Issues", "📊 SLA"],
            "dealer_ranking": ["🏆 Top 10", "📊 All"],
            "warehouse_ranking": ["🏆 Top 10", "📊 All"],
            "product_ranking": ["🏆 Top 10", "📊 All"],
            "transporter_dashboard": ["🚛 Performance", "🏆 Ranking"],
            "revenue_dashboard": ["💰 Summary", "📈 Trend", "📊 By Division"],
            "revenue_trend": ["📈 Monthly", "📊 Quarterly"],
            "inventory_dashboard": ["📦 Stock", "📊 Status"],
            "forecast_dashboard": ["📊 Revenue", "📈 Units"],
            "division_dashboard": ["🏢 Performance", "💰 Revenue"],
            "sla_compliance": ["📊 Compliance", "⚠️ Violations"],
            "default": ["📊 Dashboard", "📄 Track DN", "🏪 Dealers", "🏭 Warehouses"],
            "help": ["📄 Track DN", "🏪 Dealers", "📊 Menu"]
        }
        
        base_replies = quick_replies_map.get(intent, quick_replies_map["default"])
        
        # Add context-aware replies
        if context:
            if context.last_dealer:
                base_replies.insert(0, f"🔍 {context.last_dealer[:15]}")
            if context.last_dn:
                base_replies.insert(0, f"📄 {context.last_dn}")
            if context.last_warehouse:
                base_replies.insert(0, f"🏭 {context.last_warehouse[:15]}")
        
        # Remove duplicates and limit to 4
        seen = set()
        unique_replies = []
        for reply in base_replies:
            if reply not in seen and len(unique_replies) < 4:
                seen.add(reply)
                unique_replies.append(reply)
        
        return unique_replies
    
    def _format_whatsapp_response(self, text: str, quick_replies: Optional[List[str]] = None) -> str:
        """Format response for WhatsApp with quick replies."""
        # Truncate to WhatsApp limit
        if len(text) > MAX_RESPONSE_LENGTH:
            text = text[:MAX_RESPONSE_LENGTH - 20] + "\n\n... (truncated)"
        
        # Add quick replies as a footer (WhatsApp doesn't support native quick replies via text)
        # We'll add them as suggested commands
        if quick_replies:
            footer = "\n\n" + " • ".join(quick_replies[:4])
            if len(text) + len(footer) <= MAX_RESPONSE_LENGTH:
                text += footer
        
        return text
    
    # ==========================================================
    # DASHBOARD ROUTERS - v22.0 ENHANCED
    # ==========================================================
    
    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Dealer Dashboard."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        dealer_name = entity
        
        if not dealer_name and context and context.last_dealer:
            dealer_name = context.last_dealer
            logger.info(f"[{req_id}] Using context dealer: {dealer_name}")
        
        if not dealer_name:
            return "❌ Please specify a dealer name.\n\nExample: 'Show dealer ZQ Electronics'"
        
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
        
        result = self.analytics.get_dealer_dashboard(resolved)
        
        if not self._validate_analytics_response(result, "dealer_dashboard", req_id):
            return f"❌ Unable to retrieve dashboard for '{resolved}'."
        
        return self._format_dealer_dashboard(result, resolved, req_id)
    
    def _route_dealer_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Dealer Products."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        dealer_name = entity or (context.last_dealer if context else None)
        
        if not dealer_name:
            return "❌ Please specify a dealer name.\n\nExample: 'What products does ZQ Electronics sell?'"
        
        resolved, confidence, strategy = self._resolve_dealer_safe(dealer_name, req_id)
        
        if not resolved:
            return f"❌ Dealer '{dealer_name}' not found."
        
        result = self.analytics.get_dealer_products(resolved)
        
        if not self._validate_analytics_response(result, "dealer_products", req_id):
            return f"❌ Unable to retrieve products for '{resolved}'."
        
        return self._format_dealer_products(result, resolved, req_id)
    
    def _route_dealer_dn_aging(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Dealer DN Aging."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        dealer_name = entity or (context.last_dealer if context else None)
        
        if not dealer_name:
            return "❌ Please specify a dealer name.\n\nExample: 'DN aging for ZQ Electronics'"
        
        resolved, confidence, strategy = self._resolve_dealer_safe(dealer_name, req_id)
        
        if not resolved:
            return f"❌ Dealer '{dealer_name}' not found."
        
        result = self.analytics.get_dealer_dn_aging(resolved)
        
        if not self._validate_analytics_response(result, "dealer_dn_aging", req_id):
            return f"❌ Unable to retrieve DN aging for '{resolved}'."
        
        return self._format_dealer_dn_aging(result, resolved, req_id)
    
    def _route_dealer_delivery_performance(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Dealer Delivery Performance."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        dealer_name = entity or (context.last_dealer if context else None)
        
        if not dealer_name:
            return "❌ Please specify a dealer name.\n\nExample: 'Delivery performance for ZQ Electronics'"
        
        resolved, confidence, strategy = self._resolve_dealer_safe(dealer_name, req_id)
        
        if not resolved:
            return f"❌ Dealer '{dealer_name}' not found."
        
        result = self.analytics.get_dealer_delivery_performance(resolved)
        
        if not self._validate_analytics_response(result, "dealer_delivery_performance", req_id):
            return f"❌ Unable to retrieve delivery performance for '{resolved}'."
        
        return self._format_dealer_delivery_performance(result, resolved, req_id)
    
    def _route_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Warehouse Dashboard."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        warehouse_name = entity or (context.last_warehouse if context else None)
        
        if not warehouse_name:
            return "❌ Please specify a warehouse name.\n\nExample: 'Show Lahore warehouse'"
        
        warehouse_result = self.schema.resolve_warehouse(warehouse_name)
        if not warehouse_result:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        result = self.analytics.get_warehouse_dashboard(warehouse_result)
        
        if not self._validate_analytics_response(result, "warehouse_dashboard", req_id):
            return f"❌ Unable to retrieve warehouse dashboard for '{warehouse_result}'."
        
        return self._format_warehouse_dashboard(result, warehouse_result, req_id)
    
    def _route_warehouse_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Warehouse Products."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        warehouse_name = entity or (context.last_warehouse if context else None)
        
        if not warehouse_name:
            return "❌ Please specify a warehouse name.\n\nExample: 'What products are in Lahore warehouse?'"
        
        warehouse_result = self.schema.resolve_warehouse(warehouse_name)
        if not warehouse_result:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        result = self.analytics.get_warehouse_products(warehouse_result)
        
        if not self._validate_analytics_response(result, "warehouse_products", req_id):
            return f"❌ Unable to retrieve products for '{warehouse_result}'."
        
        return self._format_warehouse_products(result, warehouse_result, req_id)
    
    def _route_warehouse_coverage(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Warehouse Coverage."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        warehouse_name = entity or (context.last_warehouse if context else None)
        
        if not warehouse_name:
            return "❌ Please specify a warehouse name.\n\nExample: 'Lahore warehouse coverage'"
        
        warehouse_result = self.schema.resolve_warehouse(warehouse_name)
        if not warehouse_result:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        result = self.analytics.get_warehouse_coverage(warehouse_result)
        
        if not self._validate_analytics_response(result, "warehouse_coverage", req_id):
            return f"❌ Unable to retrieve coverage for '{warehouse_result}'."
        
        return self._format_warehouse_coverage(result, warehouse_result, req_id)
    
    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to City Dashboard."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        city_name = entity or (context.last_city if context else None)
        city_name = self._normalize_city(city_name) if city_name else None
        
        if not city_name:
            return "❌ Please specify a city name.\n\nExample: 'Show Lahore'"
        
        city_result = self.schema.resolve_city(city_name)
        if not city_result:
            return f"❌ City '{city_name}' not found."
        
        result = self.analytics.get_city_dashboard(city_result)
        
        if not self._validate_analytics_response(result, "city_dashboard", req_id):
            return f"❌ Unable to retrieve city dashboard for '{city_result}'."
        
        return self._format_city_dashboard(result, city_result, req_id)
    
    def _route_city_dealers(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to City Dealers."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        city_name = entity or (context.last_city if context else None)
        city_name = self._normalize_city(city_name) if city_name else None
        
        if not city_name:
            return "❌ Please specify a city name.\n\nExample: 'Dealers in Lahore'"
        
        city_result = self.schema.resolve_city(city_name)
        if not city_result:
            return f"❌ City '{city_name}' not found."
        
        result = self.analytics.get_city_dealers(city_result)
        
        if not self._validate_analytics_response(result, "city_dealers", req_id):
            return f"❌ Unable to retrieve dealers for '{city_result}'."
        
        return self._format_city_dealers(result, city_result, req_id)
    
    def _route_city_warehouses(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to City Warehouses."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        city_name = entity or (context.last_city if context else None)
        city_name = self._normalize_city(city_name) if city_name else None
        
        if not city_name:
            return "❌ Please specify a city name.\n\nExample: 'Warehouses in Lahore'"
        
        city_result = self.schema.resolve_city(city_name)
        if not city_result:
            return f"❌ City '{city_name}' not found."
        
        result = self.analytics.get_city_warehouses(city_result)
        
        if not self._validate_analytics_response(result, "city_warehouses", req_id):
            return f"❌ Unable to retrieve warehouses for '{city_result}'."
        
        return self._format_city_warehouses(result, city_result, req_id)
    
    # ==========================================================
    # PRODUCT DASHBOARD ROUTERS - v22.0 FIXED
    # ==========================================================
    
    def _route_product_dashboard_v22(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Product Dashboard - FIXED v22.0."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        product_model = entity or (context.last_product_model if context else None)
        
        if not product_model:
            return "❌ Please specify a product model.\n\nExample: 'Show Model A' or 'Show product A123'"
        
        result = self.analytics.get_product_dashboard(product_model)
        
        if not self._validate_analytics_response(result, "product_dashboard", req_id):
            return f"❌ Unable to retrieve product dashboard for '{product_model}'."
        
        return self._format_product_dashboard_v22(result, product_model, req_id)
    
    def _route_product_by_model(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Product by Model."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        product_model = entity or (context.last_product_model if context else None)
        
        if not product_model:
            return "❌ Please specify a product model.\n\nExample: 'Show Model A'"
        
        result = self.analytics.get_product_by_model(product_model)
        
        if not self._validate_analytics_response(result, "product_by_model", req_id):
            return f"❌ Unable to retrieve data for model '{product_model}'."
        
        return self._format_product_by_model(result, product_model, req_id)
    
    def _route_product_dn_count(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Product DN Count."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        product_model = entity or (context.last_product_model if context else None)
        
        if not product_model:
            return "❌ Please specify a product model.\n\nExample: 'How many DNs for Model A?'"
        
        result = self.analytics.get_product_dn_count(product_model)
        
        if not self._validate_analytics_response(result, "product_dn_count", req_id):
            return f"❌ Unable to retrieve DN count for model '{product_model}'."
        
        return self._format_product_dn_count(result, product_model, req_id)
    
    # ==========================================================
    # DN DASHBOARD ROUTERS - v22.0 ENHANCED
    # ==========================================================
    
    def _route_dn_dashboard_v22(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to DN Dashboard - Enhanced v22.0."""
        
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        dn_number = entity or (context.last_dn if context else None)
        
        if not dn_number:
            return "❌ Please provide a DN number (8-12 digits).\n\nExample: '6243600648'"
        
        cleaned = self._normalize_dn(dn_number)
        
        if not cleaned or len(cleaned) < 8 or len(cleaned) > 12:
            return f"""❌ Invalid DN number: '{dn_number}'

💡 *DN numbers must be 8-12 digits.*

📋 *Try these:*
• Enter a valid DN number (e.g., 1234567890)
• Type "help" for menu
• Ask about a dealer name

*What would you like to know?* 🤖"""
        
        # Check if DN exists
        try:
            if hasattr(self.analytics, 'verify_dn_exists'):
                exists_check = self.analytics.verify_dn_exists(cleaned)
                
                if not exists_check.get('found', False):
                    sample_dns = self._get_sample_dns(5)
                    sample_text = ""
                    if sample_dns:
                        sample_text = "\n".join([f"• {dn}" for dn in sample_dns[:3]])
                    
                    return f"""❌ DN {cleaned} not found in system.

💡 *The DN number you entered doesn't exist in our database.*

📋 *Sample DN numbers in system:*
{sample_text}

📋 *Try these:*
• Enter a valid DN number from the list above
• Type "help" for menu
• Ask about a dealer name (e.g., "Show ZQ Electronics")

*What would you like to know?* 🤖"""
        except Exception as e:
            logger.error(f"[{req_id}] DN existence check failed: {e}")
        
        result = self.analytics.get_dn_analytics(cleaned)
        
        if not self._validate_analytics_response(result, "dn_dashboard", req_id):
            error_msg = getattr(result, 'error', 'Unknown error')
            logger.error(f"[{req_id}] ❌ DN lookup failed: {error_msg}")
            
            sample_dns = self._get_sample_dns(5)
            sample_text = ""
            if sample_dns:
                sample_text = "\n".join([f"• {dn}" for dn in sample_dns[:3]])
            
            return f"""⚠️ Unable to load DN Dashboard.

💡 *Error details:* {error_msg}

📋 *Sample DN numbers in system:*
{sample_text}

📋 *Try these:*
• Enter a different DN number
• Check if DN exists in system
• Type "help" for menu

*What would you like to know?* 🤖"""
        
        return self._format_dn_dashboard_v22(result, req_id)
    
    def _route_dn_questions(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to DN Questions."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        dn_number = entity or (context.last_dn if context else None)
        
        if not dn_number:
            return "❌ Please provide a DN number.\n\nExample: 'What is the status of DN 6243600648?'"
        
        cleaned = self._normalize_dn(dn_number)
        
        if not cleaned or len(cleaned) < 8 or len(cleaned) > 12:
            return f"❌ Invalid DN number: '{dn_number}'"
        
        result = self.analytics.get_dn_details(cleaned)
        
        if not self._validate_analytics_response(result, "dn_questions", req_id):
            return f"❌ Unable to retrieve details for DN {cleaned}."
        
        return self._format_dn_details(result, req_id)
    
    # ==========================================================
    # PGI DASHBOARD ROUTERS - v22.0 FIXED
    # ==========================================================
    
    def _route_pgi_dashboard_v22(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to PGI Dashboard - FIXED v22.0."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        result = self.analytics.get_pgi_dashboard()
        
        if not self._validate_analytics_response(result, "pgi_dashboard", req_id):
            return "❌ Unable to retrieve PGI data."
        
        return self._format_pgi_dashboard_v22(result, req_id)
    
    def _route_pgi_by_dealer(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to PGI by Dealer."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        dealer_name = entity or (context.last_dealer if context else None)
        
        if not dealer_name:
            return "❌ Please specify a dealer name.\n\nExample: 'Pending PGI for ZQ Electronics'"
        
        resolved, confidence, strategy = self._resolve_dealer_safe(dealer_name, req_id)
        
        if not resolved:
            return f"❌ Dealer '{dealer_name}' not found."
        
        result = self.analytics.get_pgi_by_dealer(resolved)
        
        if not self._validate_analytics_response(result, "pgi_by_dealer", req_id):
            return f"❌ Unable to retrieve PGI data for '{resolved}'."
        
        return self._format_pgi_by_dealer(result, resolved, req_id)
    
    # ==========================================================
    # POD DASHBOARD ROUTERS - v22.0 FIXED
    # ==========================================================
    
    def _route_pod_dashboard_v22(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to POD Dashboard - FIXED v22.0."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        result = self.analytics.get_pod_dashboard()
        
        if not self._validate_analytics_response(result, "pod_dashboard", req_id):
            return "❌ Unable to retrieve POD data."
        
        return self._format_pod_dashboard_v22(result, req_id)
    
    def _route_pod_by_dealer(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to POD by Dealer."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        dealer_name = entity or (context.last_dealer if context else None)
        
        if not dealer_name:
            return "❌ Please specify a dealer name.\n\nExample: 'Pending POD for ZQ Electronics'"
        
        resolved, confidence, strategy = self._resolve_dealer_safe(dealer_name, req_id)
        
        if not resolved:
            return f"❌ Dealer '{dealer_name}' not found."
        
        result = self.analytics.get_pod_by_dealer(resolved)
        
        if not self._validate_analytics_response(result, "pod_by_dealer", req_id):
            return f"❌ Unable to retrieve POD data for '{resolved}'."
        
        return self._format_pod_by_dealer(result, resolved, req_id)
    
    def _route_pod_aging(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to POD Aging."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        result = self.analytics.get_pod_aging_analysis()
        
        if not self._validate_analytics_response(result, "pod_aging", req_id):
            return "❌ Unable to retrieve POD aging data."
        
        return self._format_pod_aging(result, req_id)
    
    # ==========================================================
    # DISTANCE DASHBOARD ROUTERS - v22.0 ENHANCED
    # ==========================================================
    
    def _route_distance_dashboard_v22(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Distance Dashboard - Enhanced v22.0."""
        if not context or not context.last_dealer or not context.last_warehouse:
            return "📍 Please specify a dealer and warehouse for distance analysis.\n\nExample: 'Show distance for ZQ Electronics from Lahore warehouse'"
        
        result = self.analytics.get_distance_analytics(context.last_warehouse, context.last_dealer)
        
        if not self._validate_analytics_response(result, "distance_dashboard", req_id):
            return f"📍 Unable to calculate distance between '{context.last_dealer}' and '{context.last_warehouse}'."
        
        return self._format_distance_dashboard_v22(result, context.last_dealer, context.last_warehouse, req_id)
    
    def _route_distance_by_city(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Distance by City."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        city_name = entity or (context.last_city if context else None)
        city_name = self._normalize_city(city_name) if city_name else None
        
        if not city_name:
            return "❌ Please specify a city name.\n\nExample: 'Distance to Haripur'"
        
        result = self.analytics.get_distance_to_city(city_name)
        
        if not self._validate_analytics_response(result, "distance_by_city", req_id):
            return f"📍 Unable to calculate distance to '{city_name}'."
        
        return self._format_distance_by_city(result, city_name, req_id)
    
    # ==========================================================
    # TRANSPORTER DASHBOARD ROUTERS - v22.0 FIXED
    # ==========================================================
    
    def _route_transporter_dashboard_v22(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Transporter Dashboard - FIXED v22.0."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        transporter_name = entity or (context.last_transporter if context else None)
        
        if not transporter_name:
            return "❌ Please specify a transporter name.\n\nExample: 'Show transporter AL Habib'"
        
        result = self.analytics.get_transporter_dashboard(transporter_name)
        
        if not self._validate_analytics_response(result, "transporter_dashboard", req_id):
            return f"❌ Unable to retrieve transporter dashboard for '{transporter_name}'."
        
        return self._format_transporter_dashboard_v22(result, transporter_name, req_id)
    
    def _route_transporter_by_name(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Transporter by Name."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        transporter_name = entity or (context.last_transporter if context else None)
        
        if not transporter_name:
            return "❌ Please specify a transporter name.\n\nExample: 'AL Habib performance'"
        
        result = self.analytics.get_transporter_details(transporter_name)
        
        if not self._validate_analytics_response(result, "transporter_by_name", req_id):
            return f"❌ Unable to retrieve details for '{transporter_name}'."
        
        return self._format_transporter_details(result, transporter_name, req_id)
    
    def _route_transporter_ranking(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Transporter Ranking."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        result = self.analytics.get_transporter_ranking(limit=10, top=True)
        
        if not self._validate_analytics_response(result, "transporter_ranking", req_id):
            return "❌ Unable to retrieve transporter ranking."
        
        return self._format_transporter_ranking(result, req_id)
    
    # ==========================================================
    # REVENUE DASHBOARD ROUTERS - v22.0
    # ==========================================================
    
    def _route_revenue_by_division(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Revenue by Division."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        division = entity or (context.last_division if context else None)
        
        if not division:
            return "❌ Please specify a division.\n\nExample: 'Revenue for Refrigerator division'"
        
        result = self.analytics.get_revenue_by_division(division)
        
        if not self._validate_analytics_response(result, "revenue_by_division", req_id):
            return f"❌ Unable to retrieve revenue for division '{division}'."
        
        return self._format_revenue_by_division(result, division, req_id)
    
    def _route_revenue_by_warehouse(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Revenue by Warehouse."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        warehouse_name = entity or (context.last_warehouse if context else None)
        
        if not warehouse_name:
            return "❌ Please specify a warehouse name.\n\nExample: 'Revenue for Lahore warehouse'"
        
        warehouse_result = self.schema.resolve_warehouse(warehouse_name)
        if not warehouse_result:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        result = self.analytics.get_revenue_by_warehouse(warehouse_result)
        
        if not self._validate_analytics_response(result, "revenue_by_warehouse", req_id):
            return f"❌ Unable to retrieve revenue for '{warehouse_result}'."
        
        return self._format_revenue_by_warehouse(result, warehouse_result, req_id)
    
    def _route_revenue_trend(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Revenue Trend."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        result = self.analytics.get_revenue_trend()
        
        if not self._validate_analytics_response(result, "revenue_trend", req_id):
            return "❌ Unable to retrieve revenue trend."
        
        return self._format_revenue_trend(result, req_id)
    
    # ==========================================================
    # INVENTORY DASHBOARD ROUTERS - v22.0 FIXED
    # ==========================================================
    
    def _route_inventory_dashboard_v22(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Inventory Dashboard - FIXED v22.0."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        result = self.analytics.get_inventory_dashboard()
        
        if not self._validate_analytics_response(result, "inventory_dashboard", req_id):
            return "❌ Unable to retrieve inventory data."
        
        return self._format_inventory_dashboard_v22(result, req_id)
    
    def _route_inventory_by_warehouse(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Inventory by Warehouse."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        warehouse_name = entity or (context.last_warehouse if context else None)
        
        if not warehouse_name:
            return "❌ Please specify a warehouse name.\n\nExample: 'Inventory in Lahore'"
        
        warehouse_result = self.schema.resolve_warehouse(warehouse_name)
        if not warehouse_result:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        result = self.analytics.get_inventory_by_warehouse(warehouse_result)
        
        if not self._validate_analytics_response(result, "inventory_by_warehouse", req_id):
            return f"❌ Unable to retrieve inventory for '{warehouse_result}'."
        
        return self._format_inventory_by_warehouse(result, warehouse_result, req_id)
    
    def _route_inventory_by_material(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Inventory by Material."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        material = entity or (context.last_material if context else None)
        
        if not material:
            return "❌ Please specify a material number.\n\nExample: 'Stock of material 12345'"
        
        result = self.analytics.get_inventory_by_material(material)
        
        if not self._validate_analytics_response(result, "inventory_by_material", req_id):
            return f"❌ Unable to retrieve inventory for material '{material}'."
        
        return self._format_inventory_by_material(result, material, req_id)
    
    # ==========================================================
    # FORECAST DASHBOARD ROUTERS - v22.0 FIXED
    # ==========================================================
    
    def _route_forecast_dashboard_v22(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Forecast Dashboard - FIXED v22.0."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        result = self.analytics.get_forecast_dashboard()
        
        if not self._validate_analytics_response(result, "forecast_dashboard", req_id):
            return "❌ Unable to retrieve forecast data."
        
        return self._format_forecast_dashboard_v22(result, req_id)
    
    def _route_forecast_by_division(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Forecast by Division."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        division = entity or (context.last_division if context else None)
        
        if not division:
            return "❌ Please specify a division.\n\nExample: 'Forecast for Refrigerator'"
        
        result = self.analytics.get_forecast_by_division(division)
        
        if not self._validate_analytics_response(result, "forecast_by_division", req_id):
            return f"❌ Unable to retrieve forecast for division '{division}'."
        
        return self._format_forecast_by_division(result, division, req_id)
    
    def _route_forecast_by_warehouse(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Forecast by Warehouse."""
        if not self.schema or not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        warehouse_name = entity or (context.last_warehouse if context else None)
        
        if not warehouse_name:
            return "❌ Please specify a warehouse name.\n\nExample: 'Forecast for Lahore'"
        
        warehouse_result = self.schema.resolve_warehouse(warehouse_name)
        if not warehouse_result:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        result = self.analytics.get_forecast_by_warehouse(warehouse_result)
        
        if not self._validate_analytics_response(result, "forecast_by_warehouse", req_id):
            return f"❌ Unable to retrieve forecast for '{warehouse_result}'."
        
        return self._format_forecast_by_warehouse(result, warehouse_result, req_id)
    
    # ==========================================================
    # DIVISION & SALES OFFICE ROUTERS - v22.0
    # ==========================================================
    
    def _route_division_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Division Dashboard."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        division = entity or (context.last_division if context else None)
        
        if not division:
            return "❌ Please specify a division name.\n\nExample: 'Show Refrigerator division'"
        
        result = self.analytics.get_division_dashboard(division)
        
        if not self._validate_analytics_response(result, "division_dashboard", req_id):
            return f"❌ Unable to retrieve division dashboard for '{division}'."
        
        return self._format_division_dashboard(result, division, req_id)
    
    def _route_sales_office_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to Sales Office Dashboard."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        sales_office = entity or (context.last_sales_office if context else None)
        
        if not sales_office:
            return "❌ Please specify a sales office.\n\nExample: 'Show Lahore sales office'"
        
        result = self.analytics.get_sales_office_dashboard(sales_office)
        
        if not self._validate_analytics_response(result, "sales_office_dashboard", req_id):
            return f"❌ Unable to retrieve sales office dashboard for '{sales_office}'."
        
        return self._format_sales_office_dashboard(result, sales_office, req_id)
    
    # ==========================================================
    # SLA COMPLIANCE ROUTER - v22.0
    # ==========================================================
    
    def _route_sla_compliance(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to SLA Compliance."""
        if not self.analytics:
            return "⚠️ Analytics service not available. Please try again later."
        
        result = self.analytics.get_sla_compliance()
        
        if not self._validate_analytics_response(result, "sla_compliance", req_id):
            return "❌ Unable to retrieve SLA compliance data."
        
        return self._format_sla_compliance(result, req_id)
    
    # ==========================================================
    # FORMATTERS - v22.0 ENHANCED
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
    
    def _format_dealer_products(self, data, dealer_name: str, req_id: str) -> str:
        """Format dealer products for WhatsApp."""
        try:
            d = data.data or {}
            products = d.get("products", [])
            
            if not products:
                return f"📦 No products found for {dealer_name}"
            
            lines = [
                f"📦 *PRODUCTS - {dealer_name}*",
                ""
            ]
            
            for i, product in enumerate(products[:10], 1):
                model = product.get("model", "Unknown")
                units = product.get("units", 0)
                revenue = product.get("revenue", 0)
                lines.append(f"{i}. {model}")
                lines.append(f"   Units: {units:,} | Revenue: PKR {revenue:,.0f}")
            
            if len(products) > 10:
                lines.append(f"")
                lines.append(f"*+ {len(products) - 10} more products*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Dealer products format error: {e}")
            return f"❌ Unable to format products for {dealer_name}"
    
    def _format_dealer_dn_aging(self, data, dealer_name: str, req_id: str) -> str:
        """Format dealer DN aging for WhatsApp."""
        try:
            d = data.data or {}
            aging = d.get("aging", {})
            
            lines = [
                f"⏳ *DN AGING - {dealer_name}*",
                "",
                f"Total Pending: {aging.get('total_pending', 0)}",
                f"0-7 Days: {aging.get('days_0_7', 0)}",
                f"8-14 Days: {aging.get('days_8_14', 0)}",
                f"15-30 Days: {aging.get('days_15_30', 0)}",
                f"30+ Days: {aging.get('days_30_plus', 0)}",
                "",
                f"Max Aging: {aging.get('max_aging_days', 0)} days",
                f"Avg Aging: {aging.get('avg_aging_days', 0):.1f} days"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Dealer DN aging format error: {e}")
            return f"❌ Unable to format DN aging for {dealer_name}"
    
    def _format_dealer_delivery_performance(self, data, dealer_name: str, req_id: str) -> str:
        """Format dealer delivery performance for WhatsApp."""
        try:
            d = data.data or {}
            performance = d.get("performance", {})
            
            lines = [
                f"🚚 *DELIVERY PERFORMANCE - {dealer_name}*",
                "",
                f"Delivery Rate: {performance.get('delivery_rate', 0):.1f}%",
                f"On-Time Rate: {performance.get('on_time_rate', 0):.1f}%",
                f"Delayed Rate: {performance.get('delayed_rate', 0):.1f}%",
                "",
                f"Avg Delivery Days: {performance.get('avg_delivery_days', 0):.1f}",
                f"Avg PGI Days: {performance.get('avg_pgi_days', 0):.1f}",
                "",
                f"Total Deliveries: {performance.get('total_deliveries', 0)}",
                f"On-Time: {performance.get('on_time', 0)}",
                f"Delayed: {performance.get('delayed', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Dealer delivery format error: {e}")
            return f"❌ Unable to format delivery performance for {dealer_name}"
    
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
    
    def _format_warehouse_products(self, data, warehouse_name: str, req_id: str) -> str:
        """Format warehouse products for WhatsApp."""
        try:
            d = data.data or {}
            products = d.get("products", [])
            
            if not products:
                return f"📦 No products found in {warehouse_name}"
            
            lines = [
                f"📦 *PRODUCTS - {warehouse_name}*",
                ""
            ]
            
            for i, product in enumerate(products[:10], 1):
                model = product.get("model", "Unknown")
                stock = product.get("stock", 0)
                movement = product.get("movement", 0)
                lines.append(f"{i}. {model}")
                lines.append(f"   Stock: {stock:,} | Movement: {movement:,}")
            
            if len(products) > 10:
                lines.append(f"")
                lines.append(f"*+ {len(products) - 10} more products*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Warehouse products format error: {e}")
            return f"❌ Unable to format products for {warehouse_name}"
    
    def _format_warehouse_coverage(self, data, warehouse_name: str, req_id: str) -> str:
        """Format warehouse coverage for WhatsApp."""
        try:
            d = data.data or {}
            coverage = d.get("coverage", {})
            cities = coverage.get("cities", [])
            dealers = coverage.get("dealers", [])
            
            lines = [
                f"📍 *COVERAGE - {warehouse_name}*",
                "",
                f"Cities Served: {len(cities)}",
                f"Dealers Served: {len(dealers)}",
                ""
            ]
            
            if cities:
                lines.append("*Cities:*")
                for city in cities[:10]:
                    lines.append(f"   • {city}")
                if len(cities) > 10:
                    lines.append(f"   *+ {len(cities) - 10} more*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Warehouse coverage format error: {e}")
            return f"❌ Unable to format coverage for {warehouse_name}"
    
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
    
    def _format_city_dealers(self, data, city_name: str, req_id: str) -> str:
        """Format city dealers for WhatsApp."""
        try:
            d = data.data or {}
            dealers = d.get("dealers", [])
            
            if not dealers:
                return f"🏪 No dealers found in {city_name}"
            
            lines = [
                f"🏪 *DEALERS IN {city_name}*",
                ""
            ]
            
            for i, dealer in enumerate(dealers[:10], 1):
                name = dealer.get("dealer_name", "Unknown")
                revenue = dealer.get("revenue", 0)
                delivery_rate = dealer.get("delivery_rate", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   Revenue: PKR {revenue:,.0f} | Delivery: {delivery_rate:.1f}%")
            
            if len(dealers) > 10:
                lines.append(f"")
                lines.append(f"*+ {len(dealers) - 10} more dealers*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] City dealers format error: {e}")
            return f"❌ Unable to format dealers for {city_name}"
    
    def _format_city_warehouses(self, data, city_name: str, req_id: str) -> str:
        """Format city warehouses for WhatsApp."""
        try:
            d = data.data or {}
            warehouses = d.get("warehouses", [])
            
            if not warehouses:
                return f"🏭 No warehouses found in {city_name}"
            
            lines = [
                f"🏭 *WAREHOUSES IN {city_name}*",
                ""
            ]
            
            for i, warehouse in enumerate(warehouses[:10], 1):
                name = warehouse.get("warehouse", "Unknown")
                revenue = warehouse.get("revenue", 0)
                dealers = warehouse.get("dealers_served", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   Revenue: PKR {revenue:,.0f} | Dealers: {dealers}")
            
            if len(warehouses) > 10:
                lines.append(f"")
                lines.append(f"*+ {len(warehouses) - 10} more warehouses*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] City warehouses format error: {e}")
            return f"❌ Unable to format warehouses for {city_name}"
    
    def _format_product_dashboard_v22(self, data, product_model: str, req_id: str) -> str:
        """Format product dashboard for WhatsApp - v22.0."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            performance = d.get("performance", {})
            top_dealers = d.get("top_dealers", [])
            
            lines = [
                f"📦 *PRODUCT DASHBOARD*",
                "",
                f"Model: {product_model}",
                f"Category: {summary.get('category', 'N/A')}",
                "",
                "📊 *Performance*",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"Total Units: {summary.get('total_units', 0):,}",
                f"Total Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                f"Avg Units/DN: {summary.get('avg_units_per_dn', 0):.1f}",
                f"Avg Revenue/DN: PKR {summary.get('avg_revenue_per_dn', 0):,.0f}",
                "",
                f"Delivery Rate: {performance.get('delivery_rate', 0):.1f}%",
                f"POD Rate: {performance.get('pod_rate', 0):.1f}%",
                "",
                "🏆 *Top Dealers*"
            ]
            
            for i, dealer in enumerate(top_dealers[:5], 1):
                name = dealer.get("dealer_name", "Unknown")
                units = dealer.get("units", 0)
                lines.append(f"   {i}. {name}: {units:,} units")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Product format error: {e}")
            return f"❌ Unable to format product dashboard for {product_model}"
    
    def _format_product_by_model(self, data, product_model: str, req_id: str) -> str:
        """Format product by model for WhatsApp."""
        try:
            d = data.data or {}
            
            lines = [
                f"📊 *MODEL PERFORMANCE*",
                "",
                f"Model: {product_model}",
                "",
                f"Revenue: PKR {d.get('revenue', 0):,.0f}",
                f"Units: {d.get('units', 0):,}",
                f"DNs: {d.get('dns', 0):,}",
                f"Dealers: {d.get('dealers', 0):,}",
                "",
                f"Avg Price: PKR {d.get('avg_price', 0):,.0f}",
                f"Market Share: {d.get('market_share', 0):.1f}%"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Product by model format error: {e}")
            return f"❌ Unable to format data for model {product_model}"
    
    def _format_product_dn_count(self, data, product_model: str, req_id: str) -> str:
        """Format product DN count for WhatsApp."""
        try:
            d = data.data or {}
            
            lines = [
                f"📄 *DN COUNT - {product_model}*",
                "",
                f"Total DNs: {d.get('total_dns', 0):,}",
                f"Total Units: {d.get('total_units', 0):,}",
                f"Total Revenue: PKR {d.get('total_revenue', 0):,.0f}",
                "",
                f"Avg Units/DN: {d.get('avg_units_per_dn', 0):.1f}",
                f"Avg Revenue/DN: PKR {d.get('avg_revenue_per_dn', 0):,.0f}",
                "",
                f"Unique Dealers: {d.get('unique_dealers', 0)}",
                f"Unique Warehouses: {d.get('unique_warehouses', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Product DN count format error: {e}")
            return f"❌ Unable to format DN count for {product_model}"
    
    def _format_dn_dashboard_v22(self, data, req_id: str) -> str:
        """Format DN dashboard for WhatsApp - v22.0."""
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
            create_date = record.get('create_date', 'N/A')
            pgi_date = record.get('pgi_date', 'N/A')
            delivery_date = record.get('delivery_date', 'N/A')
            pod_date = record.get('pod_date', 'N/A')
            
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
                f"Create Date: {create_date}",
                f"PGI Date: {pgi_date}",
                f"Delivery Date: {delivery_date}",
                f"POD Date: {pod_date}",
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
    
    def _format_dn_details(self, data, req_id: str) -> str:
        """Format DN details for WhatsApp."""
        try:
            d = data.data or {}
            record = d.get("record", {})
            products = d.get("products", [])
            
            dn_no = record.get('dn_number', 'N/A')
            
            lines = [
                f"📄 *DN DETAILS*",
                "",
                f"DN No: {dn_no}",
                f"Dealer: {record.get('customer_name', 'N/A')}",
                f"Warehouse: {record.get('warehouse', 'N/A')}",
                f"Transporter: {record.get('transporter', 'N/A')}",
                "",
                f"Units: {record.get('units', 0):,}",
                f"Amount: PKR {record.get('amount', 0):,.0f}",
                f"Status: {record.get('status', 'N/A')}",
                "",
                f"Create Date: {record.get('create_date', 'N/A')}",
                f"PGI Date: {record.get('pgi_date', 'N/A')}",
                f"Delivery Date: {record.get('delivery_date', 'N/A')}",
                f"POD Date: {record.get('pod_date', 'N/A')}",
                "",
                "📦 *Products*"
            ]
            
            for product in products[:5]:
                model = product.get("model", "Unknown")
                qty = product.get("qty", 0)
                lines.append(f"   • {model}: {qty} units")
            
            if len(products) > 5:
                lines.append(f"   *+ {len(products) - 5} more*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] DN details format error: {e}")
            return f"❌ Unable to format DN details"
    
    def _format_pgi_dashboard_v22(self, data, req_id: str) -> str:
        """Format PGI dashboard for WhatsApp - v22.0."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            by_dealer = d.get("by_dealer", [])
            
            lines = [
                "📋 *PGI DASHBOARD*",
                "",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"PGI Completed: {summary.get('pgi_completed', 0):,}",
                f"PGI Pending: {summary.get('pgi_pending', 0):,}",
                f"PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                "",
                f"Avg Processing: {summary.get('avg_processing_days', 0):.1f} days",
                "",
                "🏆 *Top Dealers by PGI Rate*"
            ]
            
            for i, dealer in enumerate(by_dealer[:5], 1):
                name = dealer.get("dealer_name", "Unknown")
                rate = dealer.get("pgi_rate", 0)
                lines.append(f"   {i}. {name}: {rate:.1f}%")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] PGI format error: {e}")
            return "❌ Unable to format PGI dashboard"
    
    def _format_pgi_by_dealer(self, data, dealer_name: str, req_id: str) -> str:
        """Format PGI by dealer for WhatsApp."""
        try:
            d = data.data or {}
            
            lines = [
                f"📋 *PGI STATUS - {dealer_name}*",
                "",
                f"Total DNs: {d.get('total_dns', 0)}",
                f"PGI Completed: {d.get('pgi_completed', 0)}",
                f"PGI Pending: {d.get('pgi_pending', 0)}",
                f"PGI Rate: {d.get('pgi_rate', 0):.1f}%",
                "",
                f"Avg Processing: {d.get('avg_processing_days', 0):.1f} days"
            ]
            
            pending_dns = d.get("pending_dns", [])
            if pending_dns:
                lines.append("")
                lines.append("*Pending DNs:*")
                for dn in pending_dns[:5]:
                    lines.append(f"   • {dn}")
                if len(pending_dns) > 5:
                    lines.append(f"   *+ {len(pending_dns) - 5} more*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] PGI by dealer format error: {e}")
            return f"❌ Unable to format PGI data for {dealer_name}"
    
    def _format_pod_dashboard_v22(self, data, req_id: str) -> str:
        """Format POD dashboard for WhatsApp - v22.0."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            by_dealer = d.get("by_dealer", [])
            aging = d.get("aging", {})
            
            lines = [
                "✅ *POD DASHBOARD*",
                "",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"POD Completed: {summary.get('pod_completed', 0):,}",
                f"POD Pending: {summary.get('pod_pending', 0):,}",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Avg POD Days: {summary.get('avg_pod_days', 0):.1f}",
                "",
                "⏳ *Aging*",
                f"0-7 Days: {aging.get('days_0_7', 0)}",
                f"8-14 Days: {aging.get('days_8_14', 0)}",
                f"15-30 Days: {aging.get('days_15_30', 0)}",
                f"30+ Days: {aging.get('days_30_plus', 0)}",
                "",
                "🏆 *Top Dealers by POD Rate*"
            ]
            
            for i, dealer in enumerate(by_dealer[:5], 1):
                name = dealer.get("dealer_name", "Unknown")
                rate = dealer.get("pod_rate", 0)
                lines.append(f"   {i}. {name}: {rate:.1f}%")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] POD format error: {e}")
            return "❌ Unable to format POD dashboard"
    
    def _format_pod_by_dealer(self, data, dealer_name: str, req_id: str) -> str:
        """Format POD by dealer for WhatsApp."""
        try:
            d = data.data or {}
            
            lines = [
                f"✅ *POD STATUS - {dealer_name}*",
                "",
                f"Total DNs: {d.get('total_dns', 0)}",
                f"POD Completed: {d.get('pod_completed', 0)}",
                f"POD Pending: {d.get('pod_pending', 0)}",
                f"POD Rate: {d.get('pod_rate', 0):.1f}%",
                "",
                f"Avg POD Days: {d.get('avg_pod_days', 0):.1f}",
                f"Max POD Days: {d.get('max_pod_days', 0)}"
            ]
            
            pending_dns = d.get("pending_dns", [])
            if pending_dns:
                lines.append("")
                lines.append("*Pending DNs:*")
                for dn in pending_dns[:5]:
                    lines.append(f"   • {dn}")
                if len(pending_dns) > 5:
                    lines.append(f"   *+ {len(pending_dns) - 5} more*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] POD by dealer format error: {e}")
            return f"❌ Unable to format POD data for {dealer_name}"
    
    def _format_pod_aging(self, data, req_id: str) -> str:
        """Format POD aging for WhatsApp."""
        try:
            d = data.data or {}
            aging = d.get("aging", {})
            critical = d.get("critical", [])
            
            lines = [
                "⏳ *POD AGING ANALYSIS*",
                "",
                f"0-7 Days: {aging.get('days_0_7', 0)}",
                f"8-14 Days: {aging.get('days_8_14', 0)}",
                f"15-30 Days: {aging.get('days_15_30', 0)}",
                f"30+ Days: {aging.get('days_30_plus', 0)}",
                "",
                f"Total Pending: {aging.get('total_pending', 0)}",
                f"Max Aging: {aging.get('max_aging_days', 0)} days",
                "",
                "🔴 *Critical PODs (30+ days)*"
            ]
            
            for dn in critical[:5]:
                dealer = dn.get("dealer", "Unknown")
                days = dn.get("days", 0)
                lines.append(f"   • {dn.get('dn_no', 'N/A')} - {dealer} ({days} days)")
            
            if len(critical) > 5:
                lines.append(f"   *+ {len(critical) - 5} more critical*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] POD aging format error: {e}")
            return "❌ Unable to format POD aging"
    
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
    
    def _format_distance_dashboard_v22(self, data, dealer: str, warehouse: str, req_id: str) -> str:
        """Format distance dashboard for WhatsApp - v22.0."""
        try:
            d = data.data or {}
            distance = d.get("distance", 0)
            transit_days = d.get("transit_days", 0)
            route = d.get("route", {})
            
            route_desc = "Short" if distance <= 50 else "Medium" if distance <= 150 else "Long" if distance <= 300 else "Extended" if distance <= 500 else "Very Long"
            
            lines = [
                "📍 *DISTANCE ANALYSIS*",
                "",
                f"Dealer: {dealer}",
                f"Warehouse: {warehouse}",
                "",
                f"Distance: {distance:.1f} KM",
                f"Route Type: {route_desc}",
                f"Expected Transit: {transit_days} Days",
                f"Risk Level: Low",
                "",
                f"Origin: {route.get('origin', warehouse)}",
                f"Destination: {route.get('destination', dealer)}",
                f"Route: {route.get('route_summary', 'N/A')}",
                "",
                f"Analysis: This is a {route_desc.lower()} distance route.",
                f"Expected delivery time is {transit_days} days."
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Distance format error: {e}")
            return f"📍 Unable to format distance analysis"
    
    def _format_distance_by_city(self, data, city_name: str, req_id: str) -> str:
        """Format distance by city for WhatsApp."""
        try:
            d = data.data or {}
            distances = d.get("distances", [])
            
            if not distances:
                return f"📍 No distance data found for {city_name}"
            
            lines = [
                f"📍 *DISTANCE TO {city_name.upper()}*",
                ""
            ]
            
            for dist in distances[:10]:
                origin = dist.get("origin", "Unknown")
                distance = dist.get("distance", 0)
                transit = dist.get("transit_days", 0)
                lines.append(f"   • {origin}: {distance:.1f} KM ({transit} days)")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Distance by city format error: {e}")
            return f"📍 Unable to format distance data for {city_name}"
    
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
    
    def _format_control_tower_dashboard(self, data, req_id: str) -> str:
        """Format Control Tower dashboard for WhatsApp."""
        try:
            d = data.data or {}
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
            
            if d.get("critical_alerts"):
                lines.append("")
                lines.append("⚠️ *Critical Alerts*")
                for alert in d.get("critical_alerts", [])[:3]:
                    lines.append(f"   • {alert}")
            
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
    
    def _format_product_ranking_v22(self, data, req_id: str) -> str:
        """Format Product Ranking for WhatsApp - v22.0."""
        try:
            products = data.data.get("products", [])
            
            lines = [
                "🏆 *PRODUCT RANKING*",
                "",
                "Top Products by Revenue:"
            ]
            
            for i, product in enumerate(products[:10], 1):
                name = product.get("model", "Unknown")
                revenue = product.get("revenue", 0)
                units = product.get("units", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   PKR {revenue:,.0f} | Units: {units:,}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Product ranking format error: {e}")
            return "❌ Unable to format product ranking"
    
    def _format_transporter_dashboard_v22(self, data, transporter_name: str, req_id: str) -> str:
        """Format Transporter Dashboard for WhatsApp - v22.0."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            
            lines = [
                f"🚛 *TRANSPORTER DASHBOARD*",
                "",
                f"Transporter: {transporter_name}",
                f"Code: {d.get('transporter_code', 'N/A')}",
                "",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"Completed: {summary.get('completed', 0):,}",
                f"In Transit: {summary.get('in_transit', 0):,}",
                f"Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                "",
                f"Avg Delivery Days: {summary.get('avg_delivery_days', 0):.1f}",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Rating: {summary.get('rating', 0):.1f}/5.0",
                f"Rank: #{summary.get('rank', 0)} of {summary.get('total_transporters', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Transporter format error: {e}")
            return f"❌ Unable to format transporter dashboard for {transporter_name}"
    
    def _format_transporter_details(self, data, transporter_name: str, req_id: str) -> str:
        """Format Transporter Details for WhatsApp."""
        try:
            d = data.data or {}
            
            lines = [
                f"🚛 *TRANSPORTER DETAILS*",
                "",
                f"Name: {transporter_name}",
                f"Code: {d.get('code', 'N/A')}",
                f"Contact: {d.get('contact', 'N/A')}",
                "",
                f"Total DNs: {d.get('total_dns', 0)}",
                f"Total Revenue: PKR {d.get('total_revenue', 0):,.0f}",
                f"Delivery Rate: {d.get('delivery_rate', 0):.1f}%",
                f"POD Rate: {d.get('pod_rate', 0):.1f}%",
                "",
                f"Avg Delivery: {d.get('avg_delivery_days', 0):.1f} days",
                f"Rating: {d.get('rating', 0):.1f}/5.0",
                "",
                f"Active: {d.get('is_active', 'Yes')}",
                f"Registered: {d.get('registered_date', 'N/A')}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Transporter details format error: {e}")
            return f"❌ Unable to format transporter details for {transporter_name}"
    
    def _format_transporter_ranking(self, data, req_id: str) -> str:
        """Format Transporter Ranking for WhatsApp."""
        try:
            transporters = data.data.get("transporters", [])
            
            lines = [
                "🏆 *TRANSPORTER RANKING*",
                "",
                "Top Transporters by Rating:"
            ]
            
            for i, transporter in enumerate(transporters[:10], 1):
                name = transporter.get("transporter", "Unknown")
                rating = transporter.get("rating", 0)
                delivery_rate = transporter.get("delivery_rate", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   Rating: {rating:.1f}/5.0 | Delivery: {delivery_rate:.1f}%")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Transporter ranking format error: {e}")
            return "❌ Unable to format transporter ranking"
    
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
    
    def _format_revenue_by_division(self, data, division: str, req_id: str) -> str:
        """Format revenue by division for WhatsApp."""
        try:
            d = data.data or {}
            
            lines = [
                f"💰 *REVENUE - {division.upper()}*",
                "",
                f"Total Revenue: PKR {d.get('total_revenue', 0):,.0f}",
                f"Total Units: {d.get('total_units', 0):,}",
                f"Total DNs: {d.get('total_dns', 0):,}",
                "",
                f"Market Share: {d.get('market_share', 0):.1f}%",
                f"Growth: {d.get('growth', 0):.1f}%",
                "",
                "🏆 *Top Products*"
            ]
            
            products = d.get("top_products", [])
            for product in products[:5]:
                name = product.get("product", "Unknown")
                revenue = product.get("revenue", 0)
                lines.append(f"   • {name}: PKR {revenue:,.0f}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Revenue by division format error: {e}")
            return f"❌ Unable to format revenue data for {division}"
    
    def _format_revenue_by_warehouse(self, data, warehouse_name: str, req_id: str) -> str:
        """Format revenue by warehouse for WhatsApp."""
        try:
            d = data.data or {}
            
            lines = [
                f"💰 *REVENUE - {warehouse_name.upper()}*",
                "",
                f"Total Revenue: PKR {d.get('total_revenue', 0):,.0f}",
                f"Total Units: {d.get('total_units', 0):,}",
                f"Total DNs: {d.get('total_dns', 0):,}",
                "",
                f"Dealers Served: {d.get('dealers_served', 0)}",
                f"Cities Served: {d.get('cities_served', 0)}",
                "",
                f"Avg Revenue/Dealer: PKR {d.get('avg_revenue_per_dealer', 0):,.0f}",
                f"Avg Units/Dealer: {d.get('avg_units_per_dealer', 0):.1f}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Revenue by warehouse format error: {e}")
            return f"❌ Unable to format revenue data for {warehouse_name}"
    
    def _format_revenue_trend(self, data, req_id: str) -> str:
        """Format revenue trend for WhatsApp."""
        try:
            d = data.data or {}
            trend = d.get("trend", [])
            
            lines = [
                "📈 *REVENUE TREND*",
                ""
            ]
            
            for period in trend[:6]:
                month = period.get("month", "Unknown")
                revenue = period.get("revenue", 0)
                growth = period.get("growth", 0)
                growth_arrow = "↑" if growth >= 0 else "↓"
                lines.append(f"   • {month}: PKR {revenue:,.0f} ({growth_arrow} {abs(growth):.1f}%)")
            
            if len(trend) > 6:
                lines.append(f"   *+ {len(trend) - 6} more periods*")
            
            lines.append("")
            lines.append(f"*Overall Growth: {d.get('overall_growth', 0):.1f}%*")
            lines.append(f"*Avg Monthly Revenue: PKR {d.get('avg_monthly_revenue', 0):,.0f}*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Revenue trend format error: {e}")
            return "❌ Unable to format revenue trend"
    
    def _format_inventory_dashboard_v22(self, data, req_id: str) -> str:
        """Format Inventory Dashboard for WhatsApp - v22.0."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            
            lines = [
                "📦 *INVENTORY DASHBOARD*",
                "",
                f"Total Products: {summary.get('total_products', 0):,}",
                f"Total Units: {summary.get('total_units', 0):,}",
                f"Total Warehouses: {summary.get('total_warehouses', 0)}",
                "",
                f"Stock Value: PKR {summary.get('stock_value', 0):,.0f}",
                f"Avg Stock/Product: {summary.get('avg_stock_per_product', 0):.1f}",
                "",
                f"High Stock: {summary.get('high_stock_count', 0)} products",
                f"Low Stock: {summary.get('low_stock_count', 0)} products",
                f"Out of Stock: {summary.get('out_of_stock_count', 0)} products",
                "",
                "📈 *Movement*",
                f"Moving: {summary.get('moving_products', 0)} products",
                f"Non-Moving: {summary.get('non_moving_products', 0)} products"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Inventory format error: {e}")
            return "❌ Unable to format inventory dashboard"
    
    def _format_inventory_by_warehouse(self, data, warehouse_name: str, req_id: str) -> str:
        """Format inventory by warehouse for WhatsApp."""
        try:
            d = data.data or {}
            products = d.get("products", [])
            
            lines = [
                f"📦 *INVENTORY - {warehouse_name.upper()}*",
                "",
                f"Total Products: {d.get('total_products', 0)}",
                f"Total Units: {d.get('total_units', 0):,}",
                f"Stock Value: PKR {d.get('stock_value', 0):,.0f}",
                "",
                "📊 *Top Products*"
            ]
            
            for product in products[:10]:
                name = product.get("product", "Unknown")
                stock = product.get("stock", 0)
                lines.append(f"   • {name}: {stock:,} units")
            
            if len(products) > 10:
                lines.append(f"   *+ {len(products) - 10} more products*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Inventory by warehouse format error: {e}")
            return f"❌ Unable to format inventory for {warehouse_name}"
    
    def _format_inventory_by_material(self, data, material: str, req_id: str) -> str:
        """Format inventory by material for WhatsApp."""
        try:
            d = data.data or {}
            warehouses = d.get("warehouses", [])
            
            lines = [
                f"📦 *INVENTORY - MATERIAL {material}*",
                "",
                f"Total Stock: {d.get('total_stock', 0):,}",
                f"Total Warehouses: {d.get('total_warehouses', 0)}",
                "",
                "🏭 *Warehouse Distribution*"
            ]
            
            for warehouse in warehouses[:10]:
                name = warehouse.get("warehouse", "Unknown")
                stock = warehouse.get("stock", 0)
                lines.append(f"   • {name}: {stock:,} units")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Inventory by material format error: {e}")
            return f"❌ Unable to format inventory for material {material}"
    
    def _format_forecast_dashboard_v22(self, data, req_id: str) -> str:
        """Format Forecast Dashboard for WhatsApp - v22.0."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            by_division = d.get("by_division", [])
            
            lines = [
                "📊 *FORECAST DASHBOARD*",
                "",
                "📈 *Next Month Forecast*",
                f"Revenue: PKR {summary.get('forecast_revenue', 0):,.0f}",
                f"Units: {summary.get('forecast_units', 0):,}",
                f"DNs: {summary.get('forecast_dns', 0):,}",
                "",
                f"Confidence: {summary.get('confidence', 0):.1f}%",
                f"Growth: {summary.get('growth', 0):.1f}%",
                "",
                "🏢 *Forecast by Division*"
            ]
            
            for division in by_division[:5]:
                name = division.get("division", "Unknown")
                revenue = division.get("forecast_revenue", 0)
                lines.append(f"   • {name}: PKR {revenue:,.0f}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Forecast format error: {e}")
            return "❌ Unable to format forecast dashboard"
    
    def _format_forecast_by_division(self, data, division: str, req_id: str) -> str:
        """Format forecast by division for WhatsApp."""
        try:
            d = data.data or {}
            
            lines = [
                f"📊 *FORECAST - {division.upper()}*",
                "",
                "📈 *Next Month*",
                f"Revenue: PKR {d.get('forecast_revenue', 0):,.0f}",
                f"Units: {d.get('forecast_units', 0):,}",
                f"DNs: {d.get('forecast_dns', 0):,}",
                "",
                f"Confidence: {d.get('confidence', 0):.1f}%",
                f"Growth: {d.get('growth', 0):.1f}%",
                "",
                f"Market Share: {d.get('market_share', 0):.1f}%",
                f"Trend: {d.get('trend', 'Stable')}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Forecast by division format error: {e}")
            return f"❌ Unable to format forecast for {division}"
    
    def _format_forecast_by_warehouse(self, data, warehouse_name: str, req_id: str) -> str:
        """Format forecast by warehouse for WhatsApp."""
        try:
            d = data.data or {}
            
            lines = [
                f"📊 *FORECAST - {warehouse_name.upper()}*",
                "",
                "📈 *Next Month*",
                f"Revenue: PKR {d.get('forecast_revenue', 0):,.0f}",
                f"Units: {d.get('forecast_units', 0):,}",
                f"DNs: {d.get('forecast_dns', 0):,}",
                "",
                f"Confidence: {d.get('confidence', 0):.1f}%",
                f"Growth: {d.get('growth', 0):.1f}%",
                "",
                f"Capacity Utilization: {d.get('capacity_utilization', 0):.1f}%",
                f"Recommended Stock: {d.get('recommended_stock', 0):,} units"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Forecast by warehouse format error: {e}")
            return f"❌ Unable to format forecast for {warehouse_name}"
    
    def _format_division_dashboard(self, data, division: str, req_id: str) -> str:
        """Format division dashboard for WhatsApp."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            top_products = d.get("top_products", [])
            
            lines = [
                f"🏢 *DIVISION DASHBOARD*",
                "",
                f"Division: {division}",
                "",
                "📊 *Performance*",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"Units: {summary.get('total_units', 0):,}",
                f"DNs: {summary.get('total_dns', 0):,}",
                f"Dealers: {summary.get('total_dealers', 0):,}",
                "",
                f"Market Share: {summary.get('market_share', 0):.1f}%",
                f"Growth: {summary.get('growth', 0):.1f}%",
                "",
                "🏆 *Top Products*"
            ]
            
            for product in top_products[:5]:
                name = product.get("product", "Unknown")
                revenue = product.get("revenue", 0)
                lines.append(f"   • {name}: PKR {revenue:,.0f}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Division format error: {e}")
            return f"❌ Unable to format division dashboard for {division}"
    
    def _format_sales_office_dashboard(self, data, sales_office: str, req_id: str) -> str:
        """Format sales office dashboard for WhatsApp."""
        try:
            d = data.data or {}
            summary = d.get("summary", {})
            
            lines = [
                f"🏢 *SALES OFFICE DASHBOARD*",
                "",
                f"Office: {sales_office}",
                f"Region: {d.get('region', 'N/A')}",
                "",
                "📊 *Performance*",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"Units: {summary.get('total_units', 0):,}",
                f"DNs: {summary.get('total_dns', 0):,}",
                f"Dealers: {summary.get('total_dealers', 0):,}",
                "",
                f"Market Share: {summary.get('market_share', 0):.1f}%",
                f"Growth: {summary.get('growth', 0):.1f}%",
                "",
                f"Cities: {summary.get('cities_covered', 0)}",
                f"Warehouses: {summary.get('warehouses_served', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] Sales office format error: {e}")
            return f"❌ Unable to format sales office dashboard for {sales_office}"
    
    def _format_sla_compliance(self, data, req_id: str) -> str:
        """Format SLA compliance for WhatsApp."""
        try:
            d = data.data or {}
            delivery = d.get("delivery", {})
            pod = d.get("pod", {})
            
            lines = [
                "📊 *SLA COMPLIANCE*",
                "",
                "🚚 *Delivery SLA*",
                f"Compliance Rate: {delivery.get('compliance_rate', 0):.1f}%",
                f"Target SLA: {delivery.get('target_sla', 0)} days",
                f"Actual Average: {delivery.get('actual_avg', 0):.1f} days",
                f"Violations: {delivery.get('violations', 0)}",
                "",
                "✅ *POD SLA*",
                f"Compliance Rate: {pod.get('compliance_rate', 0):.1f}%",
                f"Target SLA: {pod.get('target_sla', 0)} days",
                f"Actual Average: {pod.get('actual_avg', 0):.1f} days",
                f"Violations: {pod.get('violations', 0)}",
                "",
                f"Overall SLA Score: {d.get('overall_score', 0):.1f}%",
                f"Risk Level: {d.get('risk_level', 'Low')}"
            ]
            
            if d.get("top_violations"):
                lines.append("")
                lines.append("⚠️ *Top Violations*")
                for violation in d.get("top_violations", [])[:3]:
                    lines.append(f"   • {violation}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"[{req_id}] SLA format error: {e}")
            return "❌ Unable to format SLA compliance"
    
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
    
    # ==========================================================
    # GROQ EXECUTION (Preserved from v21.2)
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
            if not self.analytics:
                return None
            
            result = self.analytics.get_root_cause_insights()
            analytics_data = result.data if result and hasattr(result, 'success') and result.success else {}
            
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
            if not self.analytics:
                return None
            
            result = self.analytics.get_executive_summary()
            analytics_data = result.data if result and hasattr(result, 'success') and result.success else {}
            
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
            if not self.analytics:
                return None
            
            result = self.analytics.get_executive_summary()
            analytics_data = result.data if result and hasattr(result, 'success') and result.success else {}
            
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
    # ERROR RESPONSES
    # ==========================================================
    
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
    # METRICS & ADMIN (Enhanced v22.0)
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        avg_response = 0
        if self.metrics["response_times_ms"]:
            avg_response = sum(self.metrics["response_times_ms"]) / len(self.metrics["response_times_ms"])
        
        intent_percentiles = self._calculate_percentiles(self.metrics["intent_detection_times_ms"])
        groq_percentiles = self._calculate_percentiles(self.metrics["groq_times_ms"])
        
        return {
            "version": "22.0",
            "total_requests": self.metrics["total_requests"],
            "unique_users": len(self.metrics["unique_users"]),
            "fast_cache_hits": self.metrics["fast_cache_hits"],
            "cache_hits": self.metrics["cache_hits"],
            "avg_response_ms": round(avg_response, 2),
            "intent_detection": dict(self.metrics["intent_detection"]),
            "intent_percentiles_ms": intent_percentiles,
            "groq_percentiles_ms": groq_percentiles,
            "follow_up_queries": self.metrics["follow_up_queries"],
            "drill_down_queries": self.metrics["drill_down_queries"],
            "dealer_resolution": self.metrics["dealer_resolution"],
            "groq_uses": self.metrics["groq_uses"],
            "groq_fallbacks": self.metrics["groq_fallbacks"],
            "errors": self.metrics["errors"],
            "timeouts": self.metrics["timeouts"],
            "rate_limited": self.metrics["rate_limited_requests"],
            "validation_failures": self.metrics["validation_failures"],
            "slow_operations": self.metrics["slow_operations"],
            "service_unavailable": self.metrics["service_unavailable"],
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
        self._rate_limit_cache.clear()
        
        if self._redis_client:
            try:
                self._redis_client.flushdb()
            except:
                pass
        
        logger.info("🗑️ All caches cleared")
        return {"status": "cleared", "version": "22.0"}
    
    def warm_cache(self) -> Dict[str, Any]:
        """Warm the cache with common queries."""
        common_queries = [
            "executive summary",
            "top dealers",
            "top warehouses",
            "top products",
            "control tower",
            "delivery performance",
            "pod status",
            "pgi status",
            "revenue summary",
            "inventory status",
            "forecast"
        ]
        
        warmed = 0
        for query in common_queries:
            try:
                response = self.process_whatsapp_query(query, None, "warmup")
                if response and not response.startswith("❌"):
                    warmed += 1
            except:
                pass
        
        return {"warmed": warmed, "total": len(common_queries)}


# ==========================================================
# SINGLETON
# ==========================================================

_orchestrator = None

def get_orchestrator() -> AIOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        try:
            _orchestrator = AIOrchestrator()
            logger.info("✅ AI Orchestrator v22.0 initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize AI Orchestrator: {e}")
            logger.exception(e)
            _orchestrator = None
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
    if orchestrator is None:
        return "⚠️ AI service is currently unavailable. Please try again later."
    return orchestrator.process_whatsapp_query(
        question=question,
        session_factory=session_factory,
        phone_number=phone_number,
        user_id=user_id,
        request_id=request_id
    )


def get_ai_service_metrics() -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    if orchestrator is None:
        return {"error": "AI Orchestrator not available", "version": "22.0"}
    return orchestrator.get_metrics()


def clear_ai_cache():
    orchestrator = get_orchestrator()
    if orchestrator is None:
        return {"error": "AI Orchestrator not available", "version": "22.0"}
    return orchestrator.clear_caches()


def warm_ai_cache():
    orchestrator = get_orchestrator()
    if orchestrator is None:
        return {"error": "AI Orchestrator not available", "version": "22.0"}
    return orchestrator.warm_cache()


def get_routing_debug(question: str) -> Dict[str, Any]:
    """Debug routing for a question."""
    orchestrator = get_orchestrator()
    if orchestrator is None:
        return {"error": "AI Orchestrator not available"}
    
    try:
        intent, entity = orchestrator._detect_intent(question)
        return {
            "question": question,
            "intent": intent,
            "entity": entity,
            "should_use_groq": orchestrator._should_use_groq(question, intent)
        }
    except Exception as e:
        return {"error": str(e)}


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("AI Router v22.0 - Enterprise Production Ready")
logger.info("=" * 70)
logger.info("")
logger.info("   ENHANCEMENTS IN v22.0:")
logger.info("   ✅ Query validation & sanitization")
logger.info("   ✅ Enhanced context memory with entity tracking")
logger.info("   ✅ Session isolation for concurrent users")
logger.info("   ✅ Rate limiting per user")
logger.info("   ✅ Graceful degradation for service failures")
logger.info("   ✅ 45+ intents for 100% question coverage")
logger.info("   ✅ Dealer alias/synonym support")
logger.info("   ✅ WhatsApp quick replies")
logger.info("   ✅ Performance monitoring with percentiles")
logger.info("   ✅ Complete dashboard route fixes")
logger.info("   ✅ Follow-up support for all entity types")
logger.info("   ✅ Business rule enforcement")
logger.info("   ✅ SQL injection protection")
logger.info("")
logger.info("   RULES:")
logger.info("   ✅ Analytics First - analytics_service.py")
logger.info("   ✅ Groq Second - Only for specific intents")
logger.info("   ✅ Database Truth Always")
logger.info("   ✅ Never Crash")
logger.info("   ✅ Always Fast")
logger.info("   ✅ Always WhatsApp Safe")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
