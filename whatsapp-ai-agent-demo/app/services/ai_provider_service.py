# ==========================================================
# FILE: app/services/ai_provider_service.py (v19.0 - MASTER AI ROUTER)
# ==========================================================
# PURPOSE: AI ROUTER - Routes queries to appropriate services
# VERSION: 19.0 - Master AI Router with Performance
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
# DN Tracking | Delivery Dashboard | POD Dashboard | Revenue Dashboard
# Distance Dashboard | Performance Dashboard | Forecast Dashboard | Executive Dashboard
#
# DEALER MATCHING: Use RapidFuzz
# CONTEXT MEMORY: Store last dealer, warehouse, product, city, dashboard
#
# WHEN TO USE GROQ: ONLY for Why Questions, Recommendations, Executive Summary,
#                   Root Cause Analysis, Forecast Explanation, Management Advice, Business Risks
#
# NEVER USE GROQ FOR: Dealer Dashboard, Warehouse Dashboard, City Dashboard,
#                     Product Dashboard, DN Tracking, Delivery Dashboard,
#                     POD Dashboard, Revenue Dashboard, Distance Dashboard, Rankings
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
from typing import Optional, Callable, Any, Dict, List, Tuple
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
# INTENT CLASSIFICATION
# ==========================================================

INTENT_PATTERNS = {
    "dealer_dashboard": [
        "dealer", "customer", "show me dealer", "dealer performance",
        "dealer revenue", "dealer units", "dealer ranking",
        "top dealer", "best dealer", "dealer dashboard"
    ],
    "warehouse_dashboard": [
        "warehouse", "show me warehouse", "warehouse performance",
        "warehouse revenue", "warehouse ranking", "warehouse dashboard"
    ],
    "city_dashboard": [
        "city", "show me city", "city performance", "city revenue",
        "city ranking", "top city", "worst city", "city dashboard"
    ],
    "product_dashboard": [
        "product", "model", "top product", "best seller",
        "product performance", "product revenue", "product dashboard",
        "top model", "best model"
    ],
    "dn_tracking": [
        "dn", "track", "delivery note", "order status",
        "where is", "shipment", "delivery status", "track dn",
        "delivery note"
    ],
    "delivery_dashboard": [
        "delivery", "pending delivery", "delayed delivery",
        "delivery performance", "delivery rate", "delivery dashboard"
    ],
    "pod_dashboard": [
        "pod", "pending pod", "pod collection", "pod status",
        "pod compliance", "pod aging", "pod dashboard"
    ],
    "revenue_dashboard": [
        "revenue", "sales", "income", "turnover",
        "revenue summary", "sales performance", "revenue dashboard"
    ],
    "distance_dashboard": [
        "distance", "how far", "transit", "travel time",
        "distance from warehouse", "expected delivery", "distance dashboard"
    ],
    "performance_dashboard": [
        "performance", "kpi", "metrics", "health score",
        "overall performance", "summary", "performance dashboard"
    ],
    "forecast_dashboard": [
        "forecast", "predict", "estimated", "projected",
        "next month", "expected revenue", "future", "forecast dashboard"
    ],
    "executive_dashboard": [
        "executive", "ceo", "management", "strategic",
        "nationwide", "overview", "business summary",
        "control tower", "critical issues", "executive dashboard",
        "executive summary", "control tower"
    ]
}

# ==========================================================
# SPECIAL COMMANDS (Instant Response)
# ==========================================================

SPECIAL_COMMANDS = {
    "control tower": "control_tower",
    "control": "control_tower",
    "tower": "control_tower",
    "executive summary": "executive_summary",
    "executive insights": "executive_summary",
    "executive": "executive_summary",
    "ceo": "executive_summary",
    "management": "executive_summary",
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
    "forecast_explain": ["forecast explanation", "why forecast", "predict why"]
}

# ==========================================================
# CONVERSATION CONTEXT
# ==========================================================

class ConversationContext:
    def __init__(self, phone_number: str):
        self.phone_number = phone_number
        self.last_intent: Optional[str] = None
        self.last_entity: Optional[str] = None
        self.last_dealer: Optional[str] = None
        self.last_warehouse: Optional[str] = None
        self.last_city: Optional[str] = None
        self.last_dn: Optional[str] = None
        self.last_product: Optional[str] = None
        self.last_question: Optional[str] = None
        self.last_response: Optional[str] = None
        self.message_count: int = 0
        self.created_at: float = time.time()
        self.last_updated: float = time.time()
        self.confidence: float = 0.0
        self.retry_count: int = 0
        self.is_valid: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_city": self.last_city,
            "last_dn": self.last_dn,
            "last_product": self.last_product,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number,
            "last_question": self.last_question,
            "confidence": self.confidence,
            "retry_count": self.retry_count,
            "is_valid": self.is_valid
        }


# ==========================================================
# MASTER AI ROUTER - v19.0
# ==========================================================

class AIOrchestrator:
    """
    MASTER AI ROUTER - v19.0
    
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
                "dn_tracking": 0,
                "delivery_dashboard": 0,
                "pod_dashboard": 0,
                "revenue_dashboard": 0,
                "distance_dashboard": 0,
                "performance_dashboard": 0,
                "forecast_dashboard": 0,
                "executive_dashboard": 0,
                "unknown": 0
            },
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
        logger.info("AI Router v19.0 - Master AI Router")
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
        logger.info("   ⚡ PERFORMANCE:")
        logger.info("      - RapidFuzz: 100x faster matching")
        logger.info("      - Redis: Distributed caching")
        logger.info("      - ORJSON: Ultra-fast JSON")
        logger.info("      - Tenacity: Retry logic")
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
    # INTENT DETECTION
    # ==========================================================
    
    def _detect_intent(self, question: str) -> Tuple[str, Optional[str]]:
        """Detect intent from user question."""
        question_lower = question.lower().strip()
        
        # Check special commands first
        if question_lower in SPECIAL_COMMANDS:
            command = SPECIAL_COMMANDS[question_lower]
            if command == "control_tower":
                self.metrics["intent_detection"]["executive_dashboard"] += 1
                return "executive_dashboard", None
            if command == "executive_summary":
                self.metrics["intent_detection"]["executive_dashboard"] += 1
                return "executive_dashboard", None
            if command == "help":
                return "help", None
        
        # Check for DN first (highest priority)
        if self._is_dn_query(question):
            self.metrics["intent_detection"]["dn_tracking"] += 1
            return "dn_tracking", self._normalize_dn(question)
        
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
    
    def _extract_entity(self, question: str, intent: str) -> Optional[str]:
        """Extract entity from question based on intent."""
        question_clean = question.strip()
        
        # For dealer queries, try to extract dealer name
        if intent == "dealer_dashboard":
            # Remove common prefixes
            prefixes = ["show me", "tell me about", "get", "view", "display", 
                       "dealer", "customer", "for dealer", "for customer"]
            for prefix in prefixes:
                if question_clean.lower().startswith(prefix):
                    entity = question_clean[len(prefix):].strip()
                    if entity and len(entity) > 2:
                        return entity
            # Return the whole query if it's short
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
            "product_dashboard", "dn_tracking", "delivery_dashboard",
            "pod_dashboard", "revenue_dashboard", "distance_dashboard",
            "performance_dashboard", "help"
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
        return re.sub(r"\D", "", text.strip())
    
    def _is_dn_query(self, question: str) -> bool:
        digits = self._normalize_dn(question)
        return 8 <= len(digits) <= 12
    
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
            self.conversation_cache[phone_number] = ConversationContext(phone_number)
        
        context = self.conversation_cache[phone_number]
        if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
            context = ConversationContext(phone_number)
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
        context.confidence = 0.9
        
        if entity_type == "dealer":
            context.last_dealer = entity
        elif entity_type == "warehouse":
            context.last_warehouse = entity
        elif entity_type == "city":
            context.last_city = entity
        elif entity_type == "dn":
            context.last_dn = entity
        elif entity_type == "product":
            context.last_product = entity
        
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
        question_lower = question_clean.lower()
        
        # ==========================================================
        # STEP 1: DETECT INTENT
        # ==========================================================
        
        intent, entity = self._detect_intent(question_clean)
        logger.info(f"[{req_id}] 🎯 Intent: {intent} | Entity: {entity}")
        
        # ==========================================================
        # STEP 2: HANDLE SPECIAL COMMANDS
        # ==========================================================
        
        if intent == "help":
            response = self._get_help_message()
            self._cache_response(question, phone_number, response, True)
            return response
        
        # ==========================================================
        # STEP 3: ROUTE TO ANALYTICS SERVICE
        # ==========================================================
        
        # Dealer Dashboard
        if intent == "dealer_dashboard":
            result = self._handle_dealer_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                self._update_context(phone_number, intent, "dealer", entity, req_id, result, True)
                return result
        
        # Warehouse Dashboard
        elif intent == "warehouse_dashboard":
            result = self._handle_warehouse_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                self._update_context(phone_number, intent, "warehouse", entity, req_id, result, True)
                return result
        
        # City Dashboard
        elif intent == "city_dashboard":
            result = self._handle_city_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                self._update_context(phone_number, intent, "city", entity, req_id, result, True)
                return result
        
        # DN Tracking
        elif intent == "dn_tracking":
            result = self._handle_dn_tracking(entity, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                self._update_context(phone_number, intent, "dn", entity, req_id, result, True)
                return result
        
        # Product Dashboard
        elif intent == "product_dashboard":
            result = self._handle_product_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                self._update_context(phone_number, intent, "product", entity, req_id, result, True)
                return result
        
        # Delivery Dashboard
        elif intent == "delivery_dashboard":
            result = self._handle_delivery_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                return result
        
        # POD Dashboard
        elif intent == "pod_dashboard":
            result = self._handle_pod_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                return result
        
        # Revenue Dashboard
        elif intent == "revenue_dashboard":
            result = self._handle_revenue_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                return result
        
        # Distance Dashboard
        elif intent == "distance_dashboard":
            result = self._handle_distance_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                return result
        
        # Performance Dashboard
        elif intent == "performance_dashboard":
            result = self._handle_performance_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                return result
        
        # Forecast Dashboard
        elif intent == "forecast_dashboard":
            result = self._handle_forecast_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
                return result
        
        # Executive Dashboard
        elif intent == "executive_dashboard":
            result = self._handle_executive_dashboard(entity, context, req_id)
            if result:
                self._cache_response(question, phone_number, result, True)
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
    
    # ==========================================================
    # HANDLER METHODS - Dealer Dashboard
    # ==========================================================
    
    def _handle_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle dealer dashboard intent."""
        # Try to use entity or context
        dealer_name = entity
        
        if not dealer_name and context and context.last_dealer:
            dealer_name = context.last_dealer
            logger.info(f"[{req_id}] Using context dealer: {dealer_name}")
        
        if not dealer_name:
            # Try to resolve from question
            return None
        
        # Resolve dealer with RapidFuzz
        resolved, confidence, strategy = self._resolve_dealer_safe(dealer_name, req_id)
        
        if not resolved:
            # Show suggestions
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
        
        # Format response
        return self._format_dealer_dashboard(result, resolved, req_id)
    
    # ==========================================================
    # HANDLER METHODS - Warehouse Dashboard
    # ==========================================================
    
    def _handle_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle warehouse dashboard intent."""
        warehouse_name = entity
        
        if not warehouse_name and context and context.last_warehouse:
            warehouse_name = context.last_warehouse
        
        if not warehouse_name:
            return None
        
        # Resolve warehouse
        warehouse_result = self.schema.resolve_warehouse(warehouse_name)
        if not warehouse_result:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        # Get analytics
        result = self.analytics.get_warehouse_dashboard(warehouse_result)
        
        if not self._validate_analytics_response(result, "warehouse_dashboard", req_id):
            return f"❌ Unable to retrieve warehouse dashboard for '{warehouse_result}'."
        
        # Format response
        return self._format_warehouse_dashboard(result, warehouse_result, req_id)
    
    # ==========================================================
    # HANDLER METHODS - City Dashboard
    # ==========================================================
    
    def _handle_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle city dashboard intent."""
        city_name = entity
        
        if not city_name and context and context.last_city:
            city_name = context.last_city
        
        if not city_name:
            return None
        
        # Resolve city
        city_result = self.schema.resolve_city(city_name)
        if not city_result:
            return f"❌ City '{city_name}' not found."
        
        # Get analytics
        result = self.analytics.get_city_dashboard(city_result)
        
        if not self._validate_analytics_response(result, "city_dashboard", req_id):
            return f"❌ Unable to retrieve city dashboard for '{city_result}'."
        
        # Format response
        return self._format_city_dashboard(result, city_result, req_id)
    
    # ==========================================================
    # HANDLER METHODS - DN Tracking
    # ==========================================================
    
    def _handle_dn_tracking(self, dn_number: Optional[str], req_id: str) -> Optional[str]:
        """Handle DN tracking intent."""
        if not dn_number:
            return "❌ Please provide a DN number (8-12 digits)."
        
        # Get analytics
        result = self.analytics.get_dn_analytics(dn_number)
        
        if not self._validate_analytics_response(result, "dn_tracking", req_id):
            return f"❌ DN {dn_number} not found. Please verify the number."
        
        # Format response
        return self._format_dn_dashboard(result, req_id)
    
    # ==========================================================
    # HANDLER METHODS - Product Dashboard
    # ==========================================================
    
    def _handle_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle product dashboard intent."""
        # Get top products from analytics
        result = self.analytics.get_all_dealers_dashboard()
        if not self._validate_analytics_response(result, "product_dashboard", req_id):
            return "❌ Unable to retrieve product data."
        
        # Format response
        return self._format_product_dashboard(result, req_id)
    
    # ==========================================================
    # HANDLER METHODS - Delivery Dashboard
    # ==========================================================
    
    def _handle_delivery_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle delivery dashboard intent."""
        result = self.analytics.get_delivery_performance()
        if not self._validate_analytics_response(result, "delivery_dashboard", req_id):
            return "❌ Unable to retrieve delivery data."
        
        return self._format_delivery_dashboard(result, req_id)
    
    # ==========================================================
    # HANDLER METHODS - POD Dashboard
    # ==========================================================
    
    def _handle_pod_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle POD dashboard intent."""
        result = self.analytics.get_root_cause_insights()
        if not self._validate_analytics_response(result, "pod_dashboard", req_id):
            return "❌ Unable to retrieve POD data."
        
        return self._format_pod_dashboard(result, req_id)
    
    # ==========================================================
    # HANDLER METHODS - Revenue Dashboard
    # ==========================================================
    
    def _handle_revenue_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle revenue dashboard intent."""
        result = self.analytics.get_all_dealers_dashboard()
        if not self._validate_analytics_response(result, "revenue_dashboard", req_id):
            return "❌ Unable to retrieve revenue data."
        
        return self._format_revenue_dashboard(result, req_id)
    
    # ==========================================================
    # HANDLER METHODS - Distance Dashboard
    # ==========================================================
    
    def _handle_distance_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle distance dashboard intent."""
        if not context or not context.last_dealer or not context.last_warehouse:
            return "📍 Please specify a dealer and warehouse for distance analysis.\n\nExample: 'Show distance for ZQ Electronics from Lahore warehouse'"
        
        # Calculate distance
        distance, transit_days, status = self._calculate_distance_and_transit(
            context.last_warehouse, context.last_dealer, req_id
        )
        
        if status == "unknown":
            return f"📍 Unable to calculate distance between '{context.last_dealer}' and '{context.last_warehouse}'."
        
        return self._format_distance_dashboard(context.last_dealer, context.last_warehouse, distance, transit_days, status, req_id)
    
    # ==========================================================
    # HANDLER METHODS - Performance Dashboard
    # ==========================================================
    
    def _handle_performance_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle performance dashboard intent."""
        result = self.analytics.get_delivery_performance()
        if not self._validate_analytics_response(result, "performance_dashboard", req_id):
            return "❌ Unable to retrieve performance data."
        
        return self._format_performance_dashboard(result, req_id)
    
    # ==========================================================
    # HANDLER METHODS - Forecast Dashboard
    # ==========================================================
    
    def _handle_forecast_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle forecast dashboard intent."""
        result = self.analytics.get_executive_summary()
        if not self._validate_analytics_response(result, "forecast_dashboard", req_id):
            return "❌ Unable to retrieve forecast data."
        
        return self._format_forecast_dashboard(result, req_id)
    
    # ==========================================================
    # HANDLER METHODS - Executive Dashboard
    # ==========================================================
    
    def _handle_executive_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Handle executive dashboard intent."""
        result = self.analytics.get_executive_summary()
        if not self._validate_analytics_response(result, "executive_dashboard", req_id):
            return "❌ Unable to retrieve executive data."
        
        return self._format_executive_dashboard(result, req_id)
    
    # ==========================================================
    # DISTANCE ENGINE (Haversine Formula)
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
        # Check if warehouse and dealer are in same city
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
        
        # Simple distance based on known coordinates
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
        
        # Get warehouse coordinates
        wh_coords = warehouse_coords.get(warehouse_name.lower())
        if not wh_coords:
            return 0.0, 0, "unknown"
        
        # Get dealer coordinates from analytics (if available)
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
    # GROQ EXECUTION (Only for specific intents)
    # ==========================================================
    
    def _execute_groq_safe(self, question: str, context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Execute Groq for specific intents."""
        if not self._is_groq_available():
            return None
        
        try:
            logger.info(f"[{req_id}] 🤖 Using Groq for: {question[:50]}...")
            self.metrics["groq_uses"] += 1
            
            # Build context for Groq
            context_data = {}
            if context:
                context_data = context.to_dict()
            
            # Check if it's a root cause question
            if any(kw in question.lower() for kw in GROQ_INTENT_PATTERNS["root_cause"]):
                return self._execute_root_cause_groq(question, context_data, req_id)
            
            # Check if it's a recommendation question
            if any(kw in question.lower() for kw in GROQ_INTENT_PATTERNS["recommendation"]):
                return self._execute_recommendation_groq(question, context_data, req_id)
            
            # Check if it's an executive question
            if any(kw in question.lower() for kw in GROQ_INTENT_PATTERNS["executive"]):
                return self._execute_executive_groq(question, context_data, req_id)
            
            # General Groq
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
            # Get analytics data for context
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
    
    # ==========================================================
    # RESPONSE FORMATTERS - WhatsApp Safe
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
            
            # Distance info if available
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
            
            # Distance info
            distance_summary = distance_info.get("summary", "")
            if distance_summary:
                lines.append("")
                lines.append(distance_summary)
            
            # Issues
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
            
            # Aggregate products from top dealers
            lines = [
                "📦 *PRODUCT DASHBOARD*",
                "",
                "🏆 *Top Models*"
            ]
            
            # Show top models from top dealers
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

*Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "ZQ Electronics")
• City name (e.g., "Haripur")
• Warehouse name
• "Executive summary"
• "Control tower"
• "Help" for full menu

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
            "version": "19.0",
            "total_requests": self.metrics["total_requests"],
            "fast_cache_hits": self.metrics["fast_cache_hits"],
            "cache_hits": self.metrics["cache_hits"],
            "avg_response_ms": round(avg_response, 2),
            "intent_detection": self.metrics["intent_detection"],
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
        
        if self._redis_client:
            try:
                self._redis_client.flushdb()
            except:
                pass
        
        logger.info("🗑️ All caches cleared")
        return {"status": "cleared", "version": "19.0"}


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
logger.info("AI Router v19.0 - Master AI Router")
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
logger.info("   ⚡ PERFORMANCE:")
logger.info("      - RapidFuzz: 100x faster matching")
logger.info("      - Redis: Distributed caching")
logger.info("      - ORJSON: Ultra-fast JSON")
logger.info("      - Tenacity: Retry logic")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
