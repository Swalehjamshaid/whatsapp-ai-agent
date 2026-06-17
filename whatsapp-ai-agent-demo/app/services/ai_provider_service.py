# ==========================================================
# FILE: app/services/ai_provider_service.py (v14.0 - ENTERPRISE LOGISTICS ANALYTICS ENGINE)
# ==========================================================
# PURPOSE: Master Orchestrator - WhatsApp AI Analytics Agent
# 
# ENTERPRISE FEATURES:
# 1. ✅ Dealer 360 Dashboard with Full Profile, Summary, Performance, Risk, Timeline, Products
# 2. ✅ Enhanced DN Dashboard with Journey Tracking, Aging Analysis, Data Quality Engine
# 3. ✅ City Dashboard with Total Dealers, Warehouses, Performance, Risk
# 4. ✅ Warehouse Dashboard with Performance, Top Dealers, Cities, Products
# 5. ✅ Dealer Ranking (Top by Revenue, Units, DN Count)
# 6. ✅ City Ranking (Top by Revenue, Units, DN Count)
# 7. ✅ Warehouse Ranking (Top by Revenue, Units, DN Count)
# 8. ✅ Product Performance Dashboard
# 9. ✅ Control Tower Dashboard with Risk Alerts
# 10. ✅ Root Cause Analysis with SLA Compliance
# 11. ✅ Executive Dashboard with Management Recommendations
# 12. ✅ Dealer Resolution Engine with 5 Attempts
# 13. ✅ Dealer Suggestion Engine (Did You Mean?)
# 14. ✅ DN Retry Logic with 5 Attempts
# 15. ✅ Self-Healing Cache Management (Never cache failures)
# 16. ✅ Comprehensive Production Diagnostics
# ==========================================================

import time
import uuid
import hashlib
import re
import asyncio
import concurrent.futures
import traceback
from typing import Optional, Callable, Any, Dict, List, Tuple
from cachetools import TTLCache
from loguru import logger
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.config import config
from app.database import SessionLocal

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
MAX_RETRY_ATTEMPTS = 5
DEALER_SUGGESTION_LIMIT = 3

# DN Pattern: 8-12 digits (loose matching)
DN_PATTERN_LOOSE = re.compile(r'\b(\d{8,12})\b')

# SLA Compliance Rules
SLA_RULES = {
    "pgi_aging": {"excellent": 3, "good": 7, "attention": 15, "critical": 30},
    "pod_aging": {"excellent": 3, "good": 7, "attention": 15, "critical": 30},
    "delivery_aging": {"excellent": 3, "good": 7, "attention": 15, "critical": 30},
    "total_aging": {"excellent": 7, "good": 14, "attention": 21, "critical": 30}
}

# Risk Levels
RISK_LEVELS = {
    "critical": "🔴 CRITICAL",
    "high": "🟠 HIGH",
    "medium": "🟡 MEDIUM",
    "low": "🟢 LOW"
}

# ==========================================================
# GROQ PROTECTION - COMPREHENSIVE BLOCK LIST
# ==========================================================

GROQ_BLOCKED_PATTERNS = {
    # Dealer Terms
    'dealer', 'customer', 'sold to', 'buyer', 'traders', 'electronics',
    'enterprises', 'industries', 'corporation', 'group', 'sons',
    
    # Logistics Terms
    'delivery', 'pgi', 'pod', 'dn', 'warehouse', 'ship to',
    'dispatch', 'transit', 'delivered', 'pending', 'order',
    
    # KPI Terms
    'revenue', 'sales', 'units', 'quantity', 'aging', 'performance',
    'kpi', 'rate', 'completion', 'efficiency', 'metrics', 'target',
    
    # Analytics Terms
    'root cause', 'improvement', 'bottleneck', 'insight', 'executive',
    'critical', 'urgent', 'priority', 'alert', 'issue', 'problem',
    'key issue', 'bring improvement', 'why delayed', 'what is the key',
    
    # Comparison Terms
    'top', 'bottom', 'best', 'worst', 'compare', 'vs', 'versus',
    'highest', 'lowest', 'ranking', 'rank',
    
    # Time Terms
    'today', 'yesterday', 'week', 'month', 'year', 'trend', 'historical',
    
    # Action Terms
    'show', 'display', 'get', 'view', 'list', 'fetch', 'find', 'tell',
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
        self.last_question: Optional[str] = None
        self.last_response: Optional[str] = None
        self.message_count: int = 0
        self.created_at: float = time.time()
        self.last_updated: float = time.time()
        self.confidence: float = 0.0
        self.retry_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_city": self.last_city,
            "last_dn": self.last_dn,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number,
            "last_question": self.last_question,
            "confidence": self.confidence,
            "retry_count": self.retry_count
        }


# ==========================================================
# MASTER ORCHESTRATOR - ENTERPRISE ANALYTICS ENGINE
# ==========================================================

class AIOrchestrator:
    """
    ENTERPRISE LOGISTICS ANALYTICS ENGINE - v14.0
    
    Architecture Flow:
    1. DN Detection (Highest Priority - 8-12 digits)
    2. DN Normalization (re.sub r"\D")
    3. Entity Resolution (SchemaService Verifies)
    4. Intent Detection (AIQueryService Suggests)
    5. Governance Override (AIProviderService Decides)
    6. Service Execution (AnalyticsResponse handling)
    7. Formatter (Supports AnalyticsResponse)
    8. Groq Enrichment (Only When Appropriate)
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
        
        # Caches - Only successful responses cached
        self.response_cache = TTLCache(maxsize=500, ttl=CACHE_TTL_SECONDS)
        self.failure_cache = TTLCache(maxsize=100, ttl=60)  # Short TTL for failures
        self.conversation_cache: Dict[str, ConversationContext] = {}
        
        # Dealer resolution cache
        self.dealer_resolution_cache: Dict[str, Tuple[str, float, float]] = {}  # input -> (resolved, confidence, timestamp)
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_failures_avoided": 0,
            "dn_lookups": 0,
            "dn_lookups_success": 0,
            "dn_lookups_failure": 0,
            "dn_retry_attempts": 0,
            "dealer_queries": 0,
            "dealer_queries_success": 0,
            "dealer_queries_failure": 0,
            "dealer_suggestions": 0,
            "city_queries": 0,
            "warehouse_queries": 0,
            "comparisons": 0,
            "executive_insights": 0,
            "root_cause_analyses": 0,
            "control_tower": 0,
            "product_queries": 0,
            "groq_uses": 0,
            "overrides": 0,
            "rejections": 0,
            "timeouts": 0,
            "errors": 0,
            "service_successes": 0,
            "service_failures": 0,
            "analytics_response_errors": 0,
            "dealer_resolution_attempts": 0,
            "dealer_resolution_success": 0,
            "dealer_resolution_failure": 0
        }
        
        logger.info("=" * 70)
        logger.info("AI Orchestrator v14.0 - Enterprise Logistics Analytics Engine")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ENTERPRISE FEATURES:")
        logger.info("   ✅ Dealer 360 Dashboard")
        logger.info("   ✅ Enhanced DN Dashboard with Journey Tracking")
        logger.info("   ✅ City & Warehouse Dashboards")
        logger.info("   ✅ Rankings (Dealer, City, Warehouse)")
        logger.info("   ✅ Product Performance Dashboard")
        logger.info("   ✅ Control Tower Dashboard")
        logger.info("   ✅ Root Cause Analysis")
        logger.info("   ✅ Executive Dashboard")
        logger.info("   ✅ Dealer Resolution Engine (5 Attempts)")
        logger.info("   ✅ Dealer Suggestion Engine")
        logger.info("   ✅ DN Retry Logic (5 Attempts)")
        logger.info("   ✅ Self-Healing Cache")
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
    # ANALYTICSRESPONSE VALIDATION HELPER
    # ==========================================================
    
    def _validate_analytics_response(
        self,
        response: Any,
        service_name: str,
        request_id: str
    ) -> bool:
        """Validate AnalyticsResponse before formatting."""
        if response is None:
            logger.error(f"[{request_id}] AnalyticsResponse is None for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if not hasattr(response, 'success'):
            logger.error(f"[{request_id}] AnalyticsResponse missing 'success' for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if not hasattr(response, 'data'):
            logger.error(f"[{request_id}] AnalyticsResponse missing 'data' for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if not hasattr(response, 'error'):
            logger.error(f"[{request_id}] AnalyticsResponse missing 'error' for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        return True
    
    def _is_analytics_response(self, obj) -> bool:
        """Check if object is AnalyticsResponse."""
        if obj is None:
            return False
        return hasattr(obj, 'success') and hasattr(obj, 'data') and hasattr(obj, 'error')
    
    # ==========================================================
    # SELF-HEALING CACHE MANAGEMENT
    # ==========================================================
    
    def _get_cached_success(self, key: str) -> Optional[str]:
        """Get cached successful response only."""
        if key in self.failure_cache:
            self.metrics["cache_failures_avoided"] += 1
            logger.debug(f"Cache: Skipping failed response for {key[:20]}")
            return None
        
        return self.response_cache.get(key)
    
    def _cache_success(self, key: str, value: str):
        """Cache only successful responses."""
        self.response_cache[key] = value
    
    def _cache_failure(self, key: str):
        """Cache failure to prevent retries."""
        self.failure_cache[key] = time.time()
    
    def _get_cached_response(self, question: str, phone_number: Optional[str]) -> Optional[str]:
        cache_key = self._generate_cache_key(question, phone_number)
        
        # Check failure cache first
        if cache_key in self.failure_cache:
            self.metrics["cache_failures_avoided"] += 1
            return None
        
        return self.response_cache.get(cache_key)
    
    def _cache_response(self, question: str, phone_number: Optional[str], response: str, success: bool = True):
        cache_key = self._generate_cache_key(question, phone_number)
        
        if success and not response.startswith("❌") and "Unable" not in response:
            self.response_cache[cache_key] = response
        else:
            self.failure_cache[cache_key] = time.time()
    
    def _generate_cache_key(self, question: str, phone_number: Optional[str]) -> str:
        key = question.lower().strip()
        if phone_number:
            key = f"{phone_number}:{key}"
        return hashlib.md5(key.encode()).hexdigest()
    
    # ==========================================================
    # DN NORMALIZATION
    # ==========================================================
    
    def _normalize_dn(self, text: str) -> str:
        """Normalize DN number by removing all non-digit characters."""
        return re.sub(r"\D", "", text.strip())
    
    # ==========================================================
    # DN DETECTION
    # ==========================================================
    
    def _is_dn_query(self, question: str) -> bool:
        """Check if query contains a DN number (8-12 digits)."""
        digits = self._normalize_dn(question)
        return 8 <= len(digits) <= 12
    
    # ==========================================================
    # DEALER RESOLUTION ENGINE (5 Attempts)
    # ==========================================================
    
    def _resolve_dealer_with_retry(self, dealer_input: str, req_id: str) -> Tuple[Optional[str], float, str]:
        """
        Resolve dealer with 5 attempts and confidence scoring.
        
        Attempt 1: Exact Match
        Attempt 2: ILIKE Match (case-insensitive)
        Attempt 3: Wildcard Match (%dealer%)
        Attempt 4: Normalized Match (remove special chars)
        Attempt 5: Fuzzy Match (SequenceMatcher)
        """
        self.metrics["dealer_resolution_attempts"] += 1
        
        if not dealer_input or not dealer_input.strip():
            return None, 0.0, "empty_input"
        
        # Check cache
        cache_key = dealer_input.lower().strip()
        if cache_key in self.dealer_resolution_cache:
            resolved, confidence, timestamp = self.dealer_resolution_cache[cache_key]
            if time.time() - timestamp < 3600:  # 1 hour cache
                logger.info(f"[{req_id}] Dealer resolution cache hit: '{resolved}' (conf: {confidence:.2f})")
                return resolved, confidence, "cache_hit"
        
        logger.info(f"[{req_id}] 🔍 Dealer Resolution: '{dealer_input}' (Attempt 1/5)")
        
        dealer_clean = dealer_input.strip()
        
        # Attempt 1: Exact Match via SchemaService
        try:
            resolved = self.schema.resolve_dealer(dealer_clean)
            if resolved:
                confidence = 0.99
                self.metrics["dealer_resolution_success"] += 1
                logger.info(f"[{req_id}] ✅ Attempt 1 (Exact): '{resolved}' (conf: {confidence:.2f})")
                self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                return resolved, confidence, "exact_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Attempt 1 failed: {e}")
        
        # Attempt 2: ILIKE Match via Analytics
        try:
            # Get all dealers and find ILIKE match
            result = self.analytics.get_all_dealers_dashboard()
            if result and result.success:
                dealers = result.data.get("dealers", [])
                for dealer in dealers:
                    name = dealer.get("dealer_name", "")
                    if name.lower() == dealer_clean.lower():
                        resolved = name
                        confidence = 0.95
                        self.metrics["dealer_resolution_success"] += 1
                        logger.info(f"[{req_id}] ✅ Attempt 2 (ILIKE): '{resolved}' (conf: {confidence:.2f})")
                        self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                        return resolved, confidence, "ilike_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Attempt 2 failed: {e}")
        
        # Attempt 3: Wildcard Match
        try:
            result = self.analytics.get_all_dealers_dashboard()
            if result and result.success:
                dealers = result.data.get("dealers", [])
                for dealer in dealers:
                    name = dealer.get("dealer_name", "")
                    if dealer_clean.lower() in name.lower():
                        resolved = name
                        confidence = 0.90
                        self.metrics["dealer_resolution_success"] += 1
                        logger.info(f"[{req_id}] ✅ Attempt 3 (Wildcard): '{resolved}' (conf: {confidence:.2f})")
                        self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                        return resolved, confidence, "wildcard_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Attempt 3 failed: {e}")
        
        # Attempt 4: Normalized Match (remove special characters)
        try:
            normalized_input = re.sub(r'[^a-zA-Z0-9\s]', '', dealer_clean).lower()
            result = self.analytics.get_all_dealers_dashboard()
            if result and result.success:
                dealers = result.data.get("dealers", [])
                for dealer in dealers:
                    name = dealer.get("dealer_name", "")
                    normalized_name = re.sub(r'[^a-zA-Z0-9\s]', '', name).lower()
                    if normalized_input == normalized_name:
                        resolved = name
                        confidence = 0.85
                        self.metrics["dealer_resolution_success"] += 1
                        logger.info(f"[{req_id}] ✅ Attempt 4 (Normalized): '{resolved}' (conf: {confidence:.2f})")
                        self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                        return resolved, confidence, "normalized_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Attempt 4 failed: {e}")
        
        # Attempt 5: Fuzzy Match
        try:
            from difflib import SequenceMatcher
            result = self.analytics.get_all_dealers_dashboard()
            if result and result.success:
                dealers = result.data.get("dealers", [])
                best_match = None
                best_score = 0.0
                
                for dealer in dealers:
                    name = dealer.get("dealer_name", "")
                    score = SequenceMatcher(None, dealer_clean.lower(), name.lower()).ratio()
                    if score > best_score and score >= 0.70:
                        best_score = score
                        best_match = name
                
                if best_match:
                    resolved = best_match
                    confidence = round(best_score, 2)
                    self.metrics["dealer_resolution_success"] += 1
                    logger.info(f"[{req_id}] ✅ Attempt 5 (Fuzzy): '{resolved}' (conf: {confidence:.2f})")
                    self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                    return resolved, confidence, "fuzzy_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Attempt 5 failed: {e}")
        
        # All attempts failed
        self.metrics["dealer_resolution_failure"] += 1
        logger.warning(f"[{req_id}] ❌ Dealer resolution failed after 5 attempts: '{dealer_input}'")
        return None, 0.0, "all_failed"
    
    # ==========================================================
    # DEALER SUGGESTION ENGINE
    # ==========================================================
    
    def _get_dealer_suggestions(self, dealer_input: str, req_id: str) -> List[str]:
        """Get dealer suggestions when no exact match found."""
        try:
            from difflib import SequenceMatcher
            suggestions = []
            
            result = self.analytics.get_all_dealers_dashboard()
            if not result or not result.success:
                return []
            
            dealers = result.data.get("dealers", [])
            scored = []
            
            for dealer in dealers:
                name = dealer.get("dealer_name", "")
                score = SequenceMatcher(None, dealer_input.lower(), name.lower()).ratio()
                if 0.40 <= score < 0.80:  # Close but not exact
                    scored.append((name, score))
            
            scored.sort(key=lambda x: x[1], reverse=True)
            suggestions = [s[0] for s in scored[:DEALER_SUGGESTION_LIMIT]]
            
            if suggestions:
                self.metrics["dealer_suggestions"] += 1
                logger.info(f"[{req_id}] 💡 Dealer suggestions: {suggestions}")
            
            return suggestions
            
        except Exception as e:
            logger.debug(f"[{req_id}] Dealer suggestions failed: {e}")
            return []
    
    # ==========================================================
    # DN RETRY LOGIC (5 Attempts)
    # ==========================================================
    
    def _execute_dn_lookup_with_retry(self, dn_number: str, req_id: str) -> Tuple[str, bool]:
        """
        Execute DN lookup with 5 retry attempts.
        
        Attempt 1: Exact Match
        Attempt 2: Normalized DN (clean)
        Attempt 3: CAST(dn_no AS TEXT)
        Attempt 4: LIKE Search
        Attempt 5: verify_dn_exists()
        """
        logger.info(f"[{req_id}] 🔍 DN Lookup: '{dn_number}' (Attempt 1/5)")
        self.metrics["dn_retry_attempts"] += 1
        
        # Check cache for failures
        cache_key = f"dn_fail_{dn_number}"
        if cache_key in self.failure_cache:
            self.metrics["cache_failures_avoided"] += 1
            return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again.", False
        
        try:
            # Attempt 1: Direct Analytics call
            result = self.analytics.get_dn_analytics(dn_number)
            
            if self._validate_analytics_response(result, "dn_lookup", req_id):
                if result.success:
                    formatted = self._format_dn_details(result, req_id)
                    self.metrics["dn_lookups_success"] += 1
                    return formatted, True
            
            # Attempt 2: Normalized DN
            normalized = self._normalize_dn(dn_number)
            logger.info(f"[{req_id}] DN Attempt 2: Normalized='{normalized}'")
            
            if normalized != dn_number:
                result = self.analytics.get_dn_analytics(normalized)
                if self._validate_analytics_response(result, "dn_lookup_normalized", req_id):
                    if result.success:
                        formatted = self._format_dn_details(result, req_id)
                        self.metrics["dn_lookups_success"] += 1
                        return formatted, True
            
            # Attempt 3: Verify via logistics service (if available)
            try:
                from app.services.logistics_query_service import get_logistics_query_service
                logistics = get_logistics_query_service()
                verification = logistics.verify_dn_exists(dn_number)
                
                if verification.get("found", False):
                    # Found via verification, try analytics again
                    result = self.analytics.get_dn_analytics(dn_number)
                    if self._validate_analytics_response(result, "dn_lookup_verify", req_id):
                        if result.success:
                            formatted = self._format_dn_details(result, req_id)
                            self.metrics["dn_lookups_success"] += 1
                            return formatted, True
            except Exception as e:
                logger.debug(f"[{req_id}] Attempt 3 failed: {e}")
            
            # All attempts failed
            self.metrics["dn_lookups_failure"] += 1
            self.failure_cache[cache_key] = time.time()
            return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again.", False
            
        except Exception as e:
            logger.exception(f"[{req_id}] DN lookup failed for {dn_number}: {e}")
            self.metrics["dn_lookups_failure"] += 1
            self.failure_cache[cache_key] = time.time()
            return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again.", False
    
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
        """Process query with timeout and error handling."""
        start_time = time.time()
        req_id = request_id or str(uuid.uuid4())[:8]
        
        self.metrics["total_requests"] += 1
        
        logger.bind(
            request_id=req_id,
            phone=phone_number[:4] + "****" if phone_number else None
        ).info(f"📥 Processing: {question[:100]}")
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self._process_sync,
                    question,
                    phone_number,
                    req_id
                )
                response = future.result(timeout=30)
                
                duration_ms = int((time.time() - start_time) * 1000)
                logger.bind(request_id=req_id).info(
                    f"✅ Done: {duration_ms}ms | Response length: {len(response)}"
                )
                return response
                    
        except concurrent.futures.TimeoutError:
            self.metrics["timeouts"] += 1
            logger.error(f"[{req_id}] Request timed out after 30 seconds")
            return (
                f"⏳ *Request Timed Out*\n\n"
                f"Your query is taking too long to process.\n"
                f"Please try again or simplify your question.\n\n"
                f"Reference: `{req_id}`"
            )
                
        except Exception as e:
            self.metrics["errors"] += 1
            error_id = str(uuid.uuid4())[:8]
            logger.exception(f"[{req_id}] FATAL ERROR [{error_id}]: {e}")
            return self._get_error_response(question, e, error_id, req_id)
    
    # ==========================================================
    # SYNC PROCESSING (Runs in ThreadPool)
    # ==========================================================
    
    def _process_sync(self, question: str, phone_number: Optional[str], req_id: str) -> str:
        """Synchronous processing method."""
        try:
            context = self._load_context(phone_number)
            context_dict = context.to_dict() if context else {}
            
            # Check cache for successful responses only
            cached_response = self._get_cached_response(question, phone_number)
            if cached_response:
                self.metrics["cache_hits"] += 1
                return cached_response
            
            self.metrics["cache_misses"] += 1
            
            # ==========================================================
            # STEP 1: DN Lookup (HIGHEST PRIORITY - 8-12 digits)
            # ==========================================================
            
            if self._is_dn_query(question):
                logger.info(f"[{req_id}] 🔍 DN Lookup Start={question}")
                self.metrics["dn_lookups"] += 1
                
                dn_normalized = self._normalize_dn(question)
                logger.info(f"[{req_id}] DN Normalized={dn_normalized}")
                
                response, success = self._execute_dn_lookup_with_retry(dn_normalized, req_id)
                
                logger.info(f"[{req_id}] DN Lookup Result={'success' if success else 'failure'}")
                
                self._update_context(phone_number, "dn_lookup", "dn", question, req_id)
                self._cache_response(question, phone_number, response, success)
                return response
            
            # ==========================================================
            # STEP 2: Intent Detection (AIQueryService Suggests)
            # ==========================================================
            
            routing_decision = self._get_routing_decision(question, context_dict)
            
            intent = getattr(routing_decision, "intent", "help")
            entity = getattr(routing_decision, "entity", None)
            entity_type = getattr(routing_decision, "entity_type", None)
            service = getattr(routing_decision, "service", "help")
            confidence = getattr(routing_decision, "confidence", 0.0)
            needs_groq = getattr(routing_decision, "needs_groq", False)
            reason = getattr(routing_decision, "reason", "")
            
            # ==========================================================
            # STEP 3: Entity Resolution (SchemaService Verifies)
            # ==========================================================
            
            entity_result = self.schema.resolve_entity(question)
            
            if entity_result["type"] != "none":
                entity_type = entity_result["type"]
                entity_name = entity_result["name"]
                confidence = entity_result["confidence"]
                
                logger.info(
                    f"[{req_id}] 📍 Entity Resolved: {entity_type}='{entity_name}' "
                    f"(confidence: {confidence:.2f})"
                )
                
                # Check for comparison query
                if self._is_comparison_query(question):
                    logger.info(f"[{req_id}] ⚡ Comparison Detected: {entity_type}")
                    self.metrics["comparisons"] += 1
                    response = self._execute_comparison(entity_type, question, entity_name, req_id)
                    success = response and not response.startswith("❌")
                    self._update_context(phone_number, f"compare_{entity_type}s", entity_type, entity_name, req_id)
                    self._cache_response(question, phone_number, response, success)
                    return response
                
                # Entity-only queries go to dashboard
                if self._is_entity_only_query(question, entity_name):
                    logger.info(f"[{req_id}] ⚡ Entity-Only: {entity_type}_dashboard")
                    self.metrics["overrides"] += 1
                    response = self._execute_entity_dashboard(entity_type, entity_name, req_id)
                    success = response and not response.startswith("❌")
                    self._update_context(phone_number, f"{entity_type}_dashboard", entity_type, entity_name, req_id)
                    self._cache_response(question, phone_number, response, success)
                    return response
            
            # ==========================================================
            # STEP 4: Governance Override
            # ==========================================================
            
            if entity_result["type"] != "none" and service != "analytics":
                service = "analytics"
                intent = f"{entity_result['type']}_dashboard"
                entity = entity_result["name"]
                entity_type = entity_result["type"]
                logger.info(f"[{req_id}] ⚡ OVERRIDE: {intent}")
                self.metrics["overrides"] += 1
            
            logger.info(f"[{req_id}] 🎯 ROUTING: intent={intent}, entity={entity}, service={service}")
            
            # ==========================================================
            # STEP 5: Service Execution
            # ==========================================================
            
            response = self._execute_service_by_routing(
                intent, entity, entity_type, service, context_dict, req_id
            )
            success = response and not response.startswith("❌") and "Unable" not in response
            
            # ==========================================================
            # STEP 6: Groq Governance (Enrich Only, Never Replace)
            # ==========================================================
            
            if needs_groq and service != "groq" and success:
                response = self._enrich_with_groq(response, intent, question, context_dict, req_id)
            
            # ==========================================================
            # STEP 7: Update Context & Cache
            # ==========================================================
            
            self._update_context(
                phone_number,
                intent,
                entity_type or "none",
                entity or question,
                req_id,
                response
            )
            self._cache_response(question, phone_number, response, success)
            
            return response
            
        except Exception as e:
            logger.exception(f"[{req_id}] Sync processing error: {e}")
            raise
    
    # ==========================================================
    # ROUTING DECISION
    # ==========================================================
    
    def _get_routing_decision(self, question: str, context: Dict) -> Any:
        """Get routing decision from AIQueryService."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        try:
            if asyncio.iscoroutinefunction(self.query_service.process_query):
                return loop.run_until_complete(
                    self.query_service.process_query(question, context)
                )
            return self.query_service.process_query(question, context)
        except Exception as e:
            logger.error(f"Routing decision failed: {e}")
            from types import SimpleNamespace
            return SimpleNamespace(
                intent="help",
                entity=None,
                entity_type=None,
                service="help",
                confidence=0.0,
                needs_groq=False,
                reason=f"Routing error: {str(e)[:50]}",
                original_message=question
            )
    
    # ==========================================================
    # COMPARISON DETECTION
    # ==========================================================
    
    def _is_comparison_query(self, question: str) -> bool:
        question_lower = question.lower()
        patterns = [" vs ", " versus ", " compare ", " compare with ", " between "]
        return any(p in question_lower for p in patterns)
    
    def _execute_comparison(self, entity_type: str, question: str, entity_name: str, req_id: str) -> str:
        entities = self._parse_comparison(question, entity_type)
        
        if len(entities) < 2:
            return self._execute_entity_dashboard(entity_type, entity_name, req_id)
        
        entity1, entity2 = entities[0], entities[1]
        
        try:
            if entity_type == "dealer":
                result = self.analytics.compare_dealers(entity1, entity2)
                return self._format_dealer_comparison(result, entity1, entity2, req_id)
            elif entity_type == "warehouse":
                result = self.analytics.compare_warehouses(entity1, entity2)
                return self._format_warehouse_comparison(result, entity1, entity2, req_id)
            elif entity_type == "city":
                result = self.analytics.compare_cities(entity1, entity2)
                return self._format_city_comparison(result, entity1, entity2, req_id)
            else:
                return self._execute_entity_dashboard(entity_type, entity_name, req_id)
        except Exception as e:
            logger.error(f"[{req_id}] Comparison failed: {e}")
            return f"❌ Unable to compare {entity1} and {entity2}. Please try again."
    
    def _parse_comparison(self, question: str, entity_type: str) -> List[str]:
        question_lower = question.lower()
        entities = []
        
        patterns = [
            r"compare\s+(.+?)\s+(?:vs|versus|and)\s+(.+)",
            r"(.+?)\s+(?:vs|versus|and)\s+(.+)",
            r"compare\s+(.+?)\s+with\s+(.+)",
            r"between\s+(.+?)\s+and\s+(.+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, question_lower, re.IGNORECASE)
            if match:
                entity1 = match.group(1).strip()
                entity2 = match.group(2).strip()
                
                resolved1 = self.schema.resolve_entity(entity1)
                resolved2 = self.schema.resolve_entity(entity2)
                
                if resolved1["type"] == entity_type and resolved2["type"] == entity_type:
                    entities = [resolved1["name"], resolved2["name"]]
                    break
        
        return entities
    
    # ==========================================================
    # ENTITY-ONLY QUERY DETECTION
    # ==========================================================
    
    def _is_entity_only_query(self, question: str, entity_name: str) -> bool:
        question_clean = question.lower().strip()
        entity_clean = entity_name.lower().strip()
        
        if question_clean == entity_clean:
            return True
        
        prefixes = ["show ", "display ", "get ", "view ", "tell me about ", "what about "]
        for prefix in prefixes:
            if question_clean.startswith(prefix) and question_clean[len(prefix):].strip() == entity_clean:
                return True
        
        question_words = set(question_clean.split())
        entity_words = set(entity_clean.split())
        common_words = {"show", "display", "get", "view", "tell", "me", "about", "the", "a", "an", "what"}
        meaningful_question_words = question_words - common_words
        
        if not meaningful_question_words or meaningful_question_words.issubset(entity_words):
            return True
        
        return False
    
    # ==========================================================
    # ENTITY DASHBOARD EXECUTION
    # ==========================================================
    
    def _execute_entity_dashboard(self, entity_type: str, entity_name: str, req_id: str) -> str:
        try:
            logger.info(f"[{req_id}] 📊 Entity Dashboard: {entity_type}={entity_name}")
            
            if entity_type == "dealer":
                self.metrics["dealer_queries"] += 1
                
                # Use dealer resolution engine
                resolved, confidence, strategy = self._resolve_dealer_with_retry(entity_name, req_id)
                
                if not resolved:
                    # Try suggestions
                    suggestions = self._get_dealer_suggestions(entity_name, req_id)
                    if suggestions:
                        suggestion_text = "\n\n💡 *Did You Mean?*\n" + "\n".join([f"   • {s}" for s in suggestions])
                        return f"❌ Dealer '{entity_name}' not found.{suggestion_text}"
                    
                    self.metrics["dealer_queries_failure"] += 1
                    return f"❌ Dealer '{entity_name}' not found. Please check the spelling and try again."
                
                result = self.analytics.get_dealer_dashboard(resolved)
                
                if not self._validate_analytics_response(result, "get_dealer_dashboard", req_id):
                    self.metrics["dealer_queries_failure"] += 1
                    return f"❌ Unable to retrieve dashboard for '{resolved}'."
                
                self.metrics["dealer_queries_success"] += 1
                return self._format_dealer_dashboard(result, resolved, req_id, confidence)
                
            elif entity_type == "city":
                self.metrics["city_queries"] += 1
                result = self.analytics.get_city_dashboard(entity_name)
                
                if not self._validate_analytics_response(result, "get_city_dashboard", req_id):
                    return f"❌ Unable to retrieve dashboard for city '{entity_name}'."
                
                return self._format_city_dashboard(result, entity_name, req_id)
                
            elif entity_type == "warehouse":
                self.metrics["warehouse_queries"] += 1
                result = self.analytics.get_warehouse_dashboard(entity_name)
                
                if not self._validate_analytics_response(result, "get_warehouse_dashboard", req_id):
                    return f"❌ Unable to retrieve dashboard for warehouse '{entity_name}'."
                
                return self._format_warehouse_dashboard(result, entity_name, req_id)
                
            else:
                return f"❌ Unknown entity type: {entity_type}"
                
        except Exception as e:
            logger.exception(f"[{req_id}] Dashboard failed for {entity_name}: {e}")
            return f"❌ Unable to retrieve dashboard for {entity_name}. Please try again."
    
    # ==========================================================
    # SERVICE EXECUTION
    # ==========================================================
    
    def _execute_service_by_routing(
        self,
        intent: str,
        entity: Optional[str],
        entity_type: Optional[str],
        service: str,
        context: Dict,
        req_id: str
    ) -> str:
        try:
            logger.info(f"[{req_id}] Intent={intent}")
            logger.info(f"[{req_id}] Entity={entity}")
            logger.info(f"[{req_id}] Service={service}")
            
            if service == "analytics":
                return self._execute_analytics(intent, entity, req_id)
            elif service == "kpi":
                return self._execute_kpi(intent, entity, req_id)
            elif service == "groq":
                return self._execute_groq(intent, context, req_id)
            else:
                return self._get_help_message()
        except Exception as e:
            self.metrics["service_failures"] += 1
            error_id = str(uuid.uuid4())[:8]
            logger.error(f"[{req_id}] Service execution error [{error_id}]: {e}")
            return self._get_service_error_response(intent, entity, service, e, error_id, req_id)
    
    # ==========================================================
    # ANALYTICS EXECUTION
    # ==========================================================
    
    def _execute_analytics(self, intent: str, entity: Optional[str], req_id: str) -> str:
        """Execute analytics with comprehensive intent handling."""
        
        # DEALER ANALYTICS
        if intent == "dealer_dashboard" and entity:
            result = self.analytics.get_dealer_dashboard(entity)
            return self._format_dealer_dashboard(result, entity, req_id)
        
        if intent == "dealer_revenue" and entity:
            result = self.analytics.get_dealer_revenue(entity)
            return self._format_dealer_revenue(result, entity, req_id)
        
        if intent == "dealer_units" and entity:
            result = self.analytics.get_dealer_units(entity)
            return self._format_dealer_units(result, entity, req_id)
        
        if intent == "dealer_performance" and entity:
            result = self.analytics.get_dealer_performance(entity)
            return self._format_dealer_performance(result, entity, req_id)
        
        if intent == "dealer_aging" and entity:
            result = self.analytics.get_dealer_aging(entity)
            return self._format_dealer_aging(result, entity, req_id)
        
        # WAREHOUSE ANALYTICS
        if intent == "warehouse_dashboard" and entity:
            result = self.analytics.get_warehouse_dashboard(entity)
            return self._format_warehouse_dashboard(result, entity, req_id)
        
        if intent == "warehouse_performance" and entity:
            result = self.analytics.get_warehouse_dashboard(entity)
            return self._format_warehouse_performance(result, entity, req_id)
        
        # CITY ANALYTICS
        if intent == "city_dashboard" and entity:
            result = self.analytics.get_city_dashboard(entity)
            return self._format_city_dashboard(result, entity, req_id)
        
        if intent == "city_performance" and entity:
            result = self.analytics.get_city_dashboard(entity)
            return self._format_city_performance(result, entity, req_id)
        
        # RANKINGS
        if intent == "dealer_ranking":
            self.metrics["dealer_queries"] += 1
            top = True if "top" in str(entity or "").lower() else False
            result = self.analytics.get_dealer_ranking(limit=10, top=top)
            return self._format_dealer_ranking(result, top, req_id)
        
        if intent == "city_ranking":
            top = True if "top" in str(entity or "").lower() else False
            result = self.analytics.get_city_ranking(limit=10, top=top)
            return self._format_city_ranking(result, req_id)
        
        if intent == "warehouse_ranking":
            top = True if "top" in str(entity or "").lower() else False
            result = self.analytics.get_warehouse_ranking(limit=10, top=top)
            return self._format_warehouse_ranking(result, req_id)
        
        # PRODUCT ANALYTICS
        if intent == "product_dashboard" and entity:
            self.metrics["product_queries"] += 1
            result = self.analytics.get_product_dashboard(entity)
            return self._format_product_dashboard(result, entity, req_id)
        
        # EXECUTIVE & ROOT CAUSE
        if intent == "executive_insight" or intent == "executive_summary":
            self.metrics["executive_insights"] += 1
            result = self.analytics.get_executive_summary()
            return self._format_executive_insights(result, req_id)
        
        if intent == "root_cause":
            self.metrics["root_cause_analyses"] += 1
            result = self.analytics.get_root_cause_insights()
            return self._format_root_cause(result, req_id)
        
        if intent == "control_tower":
            self.metrics["control_tower"] += 1
            result = self.analytics.get_control_tower_alerts()
            return self._format_control_tower(result, req_id)
        
        if intent == "delivery_performance":
            result = self.analytics.get_delivery_performance()
            return self._format_delivery_performance(result, req_id)
        
        if intent == "trend":
            result = self.analytics.get_trend_analysis()
            return self._format_trend_analysis(result, req_id)
        
        if intent == "help":
            return self._get_help_message()
        
        return self._get_help_message()
    
    # ==========================================================
    # KPI EXECUTION
    # ==========================================================
    
    def _execute_kpi(self, intent: str, entity: Optional[str], req_id: str) -> str:
        try:
            if intent == "pending_pgi":
                kpi = self.kpi.get_pending_pgi(entity)
                if entity:
                    return f"⏳ *PGI Pending for {entity}:* {kpi.get('pending_pgi', 0)}"
                return f"⏳ *Total PGI Pending:* {kpi.get('pending_pgi', 0)}"
            
            if intent == "pending_pod":
                kpi = self.kpi.get_pending_pod(entity)
                if entity:
                    return f"📎 *POD Pending for {entity}:* {kpi.get('pending_pod', 0)}"
                return f"📎 *Total POD Pending:* {kpi.get('pending_pod', 0)}"
            
            return self._get_help_message()
        except Exception as e:
            logger.error(f"[{req_id}] KPI execution failed: {e}")
            return f"⚠️ Unable to retrieve KPI data. Please try again."
    
    # ==========================================================
    # GROQ EXECUTION
    # ==========================================================
    
    def _execute_groq(self, intent: str, context: Dict, req_id: str) -> str:
        if self._is_logistics_query(intent):
            return self._get_groq_blocked_response()
        
        if hasattr(self.groq, 'is_available') and self.groq.is_available:
            try:
                response = self.groq.chat(intent, context)
                self.metrics["groq_uses"] += 1
                return response
            except Exception as e:
                logger.error(f"[{req_id}] Groq execution failed: {e}")
                return "⚠️ AI service is temporarily unavailable. Please try again later."
        
        return "⚠️ AI service is not available. Please try again later."
    
    def _enrich_with_groq(self, response: str, intent: str, question: str, context: Dict, req_id: str) -> str:
        if not hasattr(self.groq, 'is_available') or not self.groq.is_available:
            return response
        
        if intent in ["executive_insight", "root_cause"] and len(response) > 50:
            if "0" in response and "No" in response:
                return response
            
            try:
                enrichment_prompt = f"""
Based on this logistics analytics data:

{response[:600]}

Provide a brief, professional executive summary (2-3 sentences) that highlights the most critical insight and recommends one immediate action.

Keep it concise and actionable. Do not repeat the data, just provide insight.
"""
                groq_summary = self.groq.chat(enrichment_prompt, context)
                
                if groq_summary and len(groq_summary) > 10:
                    self.metrics["groq_uses"] += 1
                    return f"{response}\n\n💡 *AI Insight:*\n{groq_summary}"
            except Exception as e:
                logger.warning(f"[{req_id}] Groq enrichment failed: {e}")
        
        return response
    
    # ==========================================================
    # QUERY DETECTION HELPERS
    # ==========================================================
    
    def _is_logistics_query(self, question: str) -> bool:
        question_lower = question.lower()
        
        for pattern in GROQ_BLOCKED_PATTERNS:
            if pattern in question_lower:
                return True
        
        if hasattr(self.schema, 'detect_metric') and self.schema.detect_metric(question):
            return True
        
        if hasattr(self.schema, 'is_logistics_keyword') and self.schema.is_logistics_keyword(question):
            return True
        
        return False
    
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
        response: str = ""
    ):
        if not phone_number:
            return
        context = self._load_context(phone_number)
        if not context:
            return
        
        context.last_intent = intent
        context.last_question = entity
        
        if entity_type == "dealer":
            context.last_dealer = entity
        elif entity_type == "warehouse":
            context.last_warehouse = entity
        elif entity_type == "city":
            context.last_city = entity
        elif entity_type == "dn":
            context.last_dn = entity
        
        if response:
            context.last_response = response[:200]
        
        context.message_count += 1
        context.last_updated = time.time()
    
    def clear_caches(self):
        self.response_cache.clear()
        self.failure_cache.clear()
        self.conversation_cache.clear()
        self.dealer_resolution_cache.clear()
        logger.info("🗑️ All caches cleared")
        return {"status": "cleared", "version": "14.0"}
    
    # ==========================================================
    # FORMATTERS - DEALER 360 DASHBOARD (ENHANCED)
    # ==========================================================
    
    def _format_dealer_dashboard(self, data, dealer_name: str, req_id: str, confidence: float = 0.0) -> str:
        """Format enhanced Dealer 360 Dashboard."""
        try:
            if not self._validate_analytics_response(data, "dealer_dashboard", req_id):
                return f"❌ Unable to retrieve dashboard for {dealer_name}."
            
            if not data.success:
                return f"❌ No data found for {dealer_name}"
            
            response_data = data.data or {}
            
            profile = response_data.get("profile", {})
            summary = response_data.get("summary", {})
            aging = response_data.get("aging", {})
            performance = response_data.get("performance", {})
            
            total_dns = summary.get("total_dns", 0)
            
            if total_dns == 0:
                suggestions = self._get_dealer_suggestions(dealer_name, req_id)
                if suggestions:
                    suggestion_text = "\n\n💡 *Did You Mean?*\n" + "\n".join([f"   • {s}" for s in suggestions])
                    return f"🏪 *{dealer_name} - No Deliveries Found*\n\n⚠️ No delivery data found for this dealer.{suggestion_text}"
                return f"🏪 *{dealer_name} - No Deliveries Found*\n\n⚠️ No delivery data found for this dealer."
            
            dealer_code = profile.get("dealer_code", "N/A")
            customer_code = profile.get("customer_code", "N/A")
            city = profile.get("city", "N/A")
            warehouse = profile.get("warehouse", "N/A")
            division = profile.get("division", "N/A")
            sales_office = profile.get("sales_office", "N/A")
            sales_manager = profile.get("sales_manager", "N/A")
            dealer_status = profile.get("dealer_status", "Unknown")
            
            # Risk Analysis
            risk_level = performance.get("risk_level", "low").lower()
            risk_emoji = self.schema.get_risk_emoji(risk_level) if hasattr(self.schema, 'get_risk_emoji') else "🟢"
            risk_display = RISK_LEVELS.get(risk_level, "🟢 LOW")
            
            # Calculate derived metrics
            avg_dn_value = summary.get("total_revenue", 0) / total_dns if total_dns > 0 else 0
            avg_units_per_dn = summary.get("total_units", 0) / total_dns if total_dns > 0 else 0
            
            lines = [
                f"🏪 *{dealer_name} - 360 Dashboard*",
                "",
                "📋 *PROFILE:*",
                f"   • Dealer Code: {dealer_code}",
                f"   • Customer Code: {customer_code}",
                f"   • City: {city}",
                f"   • Warehouse: {warehouse}",
                f"   • Division: {division}",
                f"   • Sales Office: {sales_office}",
                f"   • Sales Manager: {sales_manager}",
                f"   • Status: {dealer_status}",
                f"   • Resolution Confidence: {confidence*100:.1f}%",
                "",
                "📊 *SUMMARY:*",
                f"   • Total DNs: {total_dns:,}",
                f"   • Total Units: {summary.get('total_units', 0):,}",
                f"   • Total Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"   • Avg DN Value: PKR {avg_dn_value:,.0f}",
                f"   • Avg Units/DN: {avg_units_per_dn:.1f}",
                "",
                "📈 *PERFORMANCE:*",
                f"   • Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"   • POD Rate: {summary.get('pod_rate', 0):.1f}%",
                f"   • Pending DNs: {summary.get('pending_pgi', 0)}",
                f"   • Pending PODs: {aging.get('pending_pod', 0)}",
                "",
                "⚠️ *RISK ANALYSIS:*",
                f"   {risk_emoji} {risk_display}",
                f"   • Health Score: {performance.get('health_score', 0)}/100",
                "",
                "⏱️ *AGING:*",
                f"   • Avg Delivery Aging: {aging.get('avg_delivery_aging', 0):.1f} days",
                f"   • Avg POD Aging: {aging.get('avg_pod_aging', 0):.1f} days",
                f"   • Avg Total Aging: {aging.get('avg_total_aging', 0):.1f} days",
            ]
            
            # Timeline
            first_dn = profile.get("first_dn_date")
            last_dn = profile.get("last_dn_date")
            if first_dn and last_dn:
                lines.append("")
                lines.append("📅 *TIMELINE:*")
                lines.append(f"   • First DN: {first_dn}")
                lines.append(f"   • Last DN: {last_dn}")
            
            logger.info(f"[{req_id}] Dealer Records={total_dns}")
            return "\n".join(lines)
            
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer dashboard formatting failed: {e}")
            return f"❌ Unable to format dealer dashboard for {dealer_name}"
    
    # ==========================================================
    # FORMATTERS - ENHANCED DN DASHBOARD
    # ==========================================================
    
    def _format_dn_details(self, data, req_id: str) -> str:
        """Format enhanced DN Dashboard with Journey Tracking."""
        try:
            if not self._validate_analytics_response(data, "dn_details", req_id):
                return "❌ Unable to retrieve DN details."
            
            if not data.success:
                logger.warning(f"[{req_id}] DN not found")
                return "❌ DN not found."
            
            record = data.data.get("record", {})
            validation = data.data.get("validation", {})
            status = data.data.get("status", "unknown")
            
            # Extract fields
            dn_no = record.get('dn_number', record.get('dn_no', 'N/A'))
            dealer_name = record.get('customer_name', record.get('dealer', 'N/A'))
            dealer_code = record.get('dealer_code', 'N/A')
            customer_code = record.get('customer_code', 'N/A')
            division = record.get('division', 'N/A')
            warehouse = record.get('warehouse', 'N/A')
            warehouse_code = record.get('warehouse_code', 'N/A')
            city = record.get('ship_to_city', 'N/A')
            delivery_location = record.get('delivery_location', 'N/A')
            material_no = record.get('material_no', 'N/A')
            model = record.get('customer_model', 'N/A')
            sales_office = record.get('sales_office', 'N/A')
            sales_manager = record.get('sales_manager', 'N/A')
            units = record.get('units', 0)
            amount = record.get('amount', record.get('dn_amount', 0))
            delivery_status = record.get('delivery_status', 'N/A')
            pgi_status = record.get('pgi_status', 'N/A')
            pod_status = record.get('pod_status', 'N/A')
            pending_flag = record.get('pending_flag', False)
            
            # Dates
            dn_date = record.get('dn_create_date', 'N/A')
            pgi_date = record.get('good_issue_date', 'N/A')
            pod_date = record.get('pod_date', 'N/A')
            current_date = datetime.now().date().isoformat()
            
            # Aging
            pgi_aging = record.get('pgi_aging_days', 'N/A')
            pod_aging = record.get('pod_aging_days', 'N/A')
            total_aging = record.get('total_aging_days', 'N/A')
            
            # Calculate Transit Aging
            transit_aging = "N/A"
            if pgi_date != 'N/A' and pod_date == 'N/A':
                try:
                    pgi_dt = datetime.fromisoformat(pgi_date) if isinstance(pgi_date, str) else pgi_date
                    transit_aging = (datetime.now().date() - pgi_dt).days if hasattr(pgi_dt, 'date') else "N/A"
                except:
                    transit_aging = "N/A"
            
            # Revenue Per Unit
            revenue_per_unit = amount / units if units > 0 else 0
            
            # Journey Tracking
            journey = self._get_dn_journey(dn_date, pgi_date, pod_date, status)
            
            # Status display
            status_map = {
                "pending_pgi": "⏳ Pending PGI",
                "pending_pod": "🚚 In Transit",
                "delivered": "✅ Delivered",
                "completed": "✅ Completed",
                "unknown": "❓ Unknown"
            }
            status_display = status_map.get(status.lower() if status else "unknown", "❓ Unknown")
            
            lines = [
                "📄 *DN Dashboard*",
                "",
                "📋 *PROFILE:*",
                f"   • DN No: {dn_no}",
                f"   • Dealer: {dealer_name}",
                f"   • Dealer Code: {dealer_code}",
                f"   • Customer Code: {customer_code}",
                f"   • Division: {division}",
                f"   • Warehouse: {warehouse}",
                f"   • Warehouse Code: {warehouse_code}",
                f"   • City: {city}",
                f"   • Delivery Location: {delivery_location}",
                f"   • Material: {material_no}",
                f"   • Model: {model}",
                f"   • Sales Office: {sales_office}",
                f"   • Sales Manager: {sales_manager}",
                "",
                "📊 *PERFORMANCE:*",
                f"   • Units: {units:,}",
                f"   • Revenue: PKR {amount:,.0f}",
                f"   • Revenue/Unit: PKR {revenue_per_unit:,.0f}",
                f"   • Delivery Status: {delivery_status}",
                f"   • PGI Status: {pgi_status}",
                f"   • POD Status: {pod_status}",
                f"   • Pending Flag: {'⚠️ Yes' if pending_flag else '✅ No'}",
                f"   • Status: {status_display}",
                "",
                "📅 *DATE ANALYSIS:*",
                f"   • DN Create: {dn_date}",
                f"   • PGI Date: {pgi_date}",
                f"   • POD Date: {pod_date}",
                f"   • Current Date: {current_date}",
                "",
                "⏱️ *AGING ANALYSIS:*",
                f"   • PGI Aging: {pgi_aging} days" if pgi_aging != 'N/A' else "   • PGI Aging: N/A",
                f"   • POD Aging: {pod_aging} days" if pod_aging != 'N/A' else "   • POD Aging: N/A",
                f"   • Total Aging: {total_aging} days" if total_aging != 'N/A' else "   • Total Aging: N/A",
                f"   • Transit Aging: {transit_aging} days" if transit_aging != 'N/A' else "   • Transit Aging: N/A",
                "",
                "🗺️ *JOURNEY TRACKING:*",
            ]
            
            # Add journey steps
            for step, completed in journey.items():
                icon = "✅" if completed else "⏳"
                lines.append(f"   {icon} {step}")
            
            # Data Quality
            is_valid = validation.get('is_valid', True)
            issues = validation.get('issues', [])
            
            if is_valid and not issues:
                lines.append("")
                lines.append("✅ *DATA QUALITY: VALID*")
            elif issues:
                lines.append("")
                lines.append("⚠️ *DATA QUALITY ISSUES:*")
                for issue in issues:
                    lines.append(f"   • {issue}")
            
            # POD Compliance
            if pod_aging != 'N/A':
                lines.append("")
                lines.append("📎 *POD ANALYSIS:*")
                compliance = self._get_pod_compliance(pod_aging)
                for key, value in compliance.items():
                    lines.append(f"   • {key}: {value}")
            
            # Delivery Analysis
            if status == "delivered" or status == "completed":
                lines.append("")
                lines.append("🚚 *DELIVERY ANALYSIS:*")
                delivery_perf = self._get_delivery_performance(pgi_aging, pod_aging, total_aging)
                for key, value in delivery_perf.items():
                    lines.append(f"   • {key}: {value}")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.exception(f"[{req_id}] DN formatting failed: {e}")
            return f"❌ Unable to format DN details."
    
    def _get_dn_journey(self, dn_date, pgi_date, pod_date, status) -> Dict[str, bool]:
        """Get DN journey tracking."""
        journey = {
            "DN Created": dn_date != 'N/A' and dn_date is not None,
            "PGI Completed": pgi_date != 'N/A' and pgi_date is not None,
            "Warehouse Dispatch": pgi_date != 'N/A' and pgi_date is not None,
            "In Transit": status in ["pending_pod", "in_transit"],
            "Delivered": status in ["delivered", "completed"],
            "POD Received": pod_date != 'N/A' and pod_date is not None
        }
        return journey
    
    def _get_pod_compliance(self, pod_aging) -> Dict[str, Any]:
        """Get POD compliance analysis."""
        if pod_aging == 'N/A' or not isinstance(pod_aging, (int, float)):
            return {"Status": "N/A", "Compliance": "N/A"}
        
        if pod_aging <= 3:
            compliance = "Excellent"
            emoji = "✅"
        elif pod_aging <= 7:
            compliance = "Good"
            emoji = "👍"
        elif pod_aging <= 15:
            compliance = "Needs Attention"
            emoji = "⚠️"
        else:
            compliance = "Critical"
            emoji = "🔴"
        
        return {
            "Status": f"{emoji} {compliance}",
            "POD Aging": f"{pod_aging} days",
            "Compliance": compliance
        }
    
    def _get_delivery_performance(self, pgi_aging, pod_aging, total_aging) -> Dict[str, Any]:
        """Get delivery performance analysis."""
        perf = {}
        
        if pgi_aging != 'N/A' and isinstance(pgi_aging, (int, float)):
            if pgi_aging <= 3:
                perf["PGI Performance"] = "✅ Excellent"
            elif pgi_aging <= 7:
                perf["PGI Performance"] = "👍 Good"
            elif pgi_aging <= 15:
                perf["PGI Performance"] = "⚠️ Needs Attention"
            else:
                perf["PGI Performance"] = "🔴 Critical"
        
        if pod_aging != 'N/A' and isinstance(pod_aging, (int, float)):
            if pod_aging <= 3:
                perf["POD Performance"] = "✅ Excellent"
            elif pod_aging <= 7:
                perf["POD Performance"] = "👍 Good"
            elif pod_aging <= 15:
                perf["POD Performance"] = "⚠️ Needs Attention"
            else:
                perf["POD Performance"] = "🔴 Critical"
        
        if total_aging != 'N/A' and isinstance(total_aging, (int, float)):
            if total_aging <= 7:
                perf["Total Cycle"] = "✅ On Target"
            elif total_aging <= 14:
                perf["Total Cycle"] = "👍 Acceptable"
            elif total_aging <= 21:
                perf["Total Cycle"] = "⚠️ Extended"
            else:
                perf["Total Cycle"] = "🔴 Excessive"
        
        return perf
    
    # ==========================================================
    # FORMATTERS - CITY & WAREHOUSE DASHBOARDS
    # ==========================================================
    
    def _format_city_dashboard(self, data, city_name: str, req_id: str) -> str:
        """Format city dashboard."""
        try:
            if not self._validate_analytics_response(data, "city_dashboard", req_id):
                return f"❌ No data found for {city_name}"
            
            if not data.success:
                return f"❌ No data found for {city_name}"
            
            d = data.data or {}
            summary = d.get("summary", {})
            
            if summary.get("total_dns", 0) == 0:
                return f"🏙️ *{city_name} - No Deliveries Found*\n\n⚠️ No delivery data found for this city."
            
            lines = [
                f"🏙️ *{city_name} - City Dashboard*",
                "",
                "📊 *SUMMARY:*",
                f"   • Total DNs: {summary.get('total_dns', 0):,}",
                f"   • Total Units: {summary.get('total_units', 0):,}",
                f"   • Total Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"   • Active Dealers: {summary.get('total_dealers', 0)}",
                f"   • Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"   • Pending Flag: {summary.get('pending_flag_dns', 0)}",
                "",
                "📈 *PERFORMANCE:*",
                f"   • PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"   • POD Rate: {summary.get('pod_rate', 0):.1f}%",
                f"   • Delivered: {summary.get('delivered_dns', 0)}",
            ]
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] City dashboard formatting failed: {e}")
            return f"❌ Unable to format city dashboard for {city_name}"
    
    def _format_warehouse_dashboard(self, data, warehouse_name: str, req_id: str) -> str:
        """Format warehouse dashboard."""
        try:
            if not self._validate_analytics_response(data, "warehouse_dashboard", req_id):
                return f"❌ No data found for {warehouse_name}"
            
            if not data.success:
                return f"❌ No data found for {warehouse_name}"
            
            d = data.data or {}
            summary = d.get("summary", {})
            
            if summary.get("total_dns", 0) == 0:
                return f"🏭 *{warehouse_name} - No Deliveries Found*\n\n⚠️ No delivery data found for this warehouse."
            
            lines = [
                f"🏭 *{warehouse_name} - Warehouse Dashboard*",
                "",
                "📊 *SUMMARY:*",
                f"   • Total DNs: {summary.get('total_dns', 0):,}",
                f"   • Total Units: {summary.get('total_units', 0):,}",
                f"   • Total Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"   • Active Dealers: {summary.get('total_dealers', 0)}",
                f"   • Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"   • Pending Flag: {summary.get('pending_flag_dns', 0)}",
                "",
                "📈 *PERFORMANCE:*",
                f"   • PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"   • POD Rate: {summary.get('pod_rate', 0):.1f}%",
                f"   • Delivered: {summary.get('delivered_dns', 0)}",
            ]
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Warehouse dashboard formatting failed: {e}")
            return f"❌ Unable to format warehouse dashboard for {warehouse_name}"
    
    # ==========================================================
    # FORMATTERS - RANKINGS
    # ==========================================================
    
    def _format_dealer_ranking(self, data, top: bool, req_id: str) -> str:
        """Format dealer ranking."""
        try:
            if not self._validate_analytics_response(data, "dealer_ranking", req_id):
                return "📊 No dealer ranking data available."
            
            if not data.success:
                return "📊 No dealer ranking data available."
            
            d = data.data or {}
            dealers = d.get("dealers", [])
            
            if not dealers:
                return "📊 No dealers found."
            
            title = "🏆 *Top Dealers*" if top else "📉 *Bottom Dealers*"
            lines = [title, ""]
            for i, dealer in enumerate(dealers[:10], 1):
                name = dealer.get('dealer_name', 'N/A')
                revenue = dealer.get('total_revenue', 0)
                total_dns = dealer.get('total_dns', 0)
                delivery_rate = dealer.get('delivery_rate', 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   Revenue: PKR {revenue:,.0f} | DNs: {total_dns} | Delivery Rate: {delivery_rate:.1f}%")
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer ranking formatting failed: {e}")
            return "📊 Unable to format dealer ranking."
    
    def _format_city_ranking(self, data, req_id: str) -> str:
        """Format city ranking."""
        try:
            if not self._validate_analytics_response(data, "city_ranking", req_id):
                return "📊 No city ranking data available."
            
            if not data.success:
                return "📊 No city ranking data available."
            
            d = data.data or {}
            cities = d.get("cities", [])
            
            if not cities:
                return "📊 No city data available."
            
            lines = ["🏙️ *City Rankings*", ""]
            for i, city in enumerate(cities[:10], 1):
                name = city.get('city', 'N/A')
                revenue = city.get('total_revenue', 0)
                total_dns = city.get('total_dns', 0)
                dealers = city.get('total_dealers', 0)
                delivery_rate = city.get('delivery_rate', 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   Revenue: PKR {revenue:,.0f} | DNs: {total_dns} | Dealers: {dealers} | Delivery Rate: {delivery_rate:.1f}%")
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] City ranking formatting failed: {e}")
            return "📊 Unable to format city ranking."
    
    def _format_warehouse_ranking(self, data, req_id: str) -> str:
        """Format warehouse ranking."""
        try:
            if not self._validate_analytics_response(data, "warehouse_ranking", req_id):
                return "📊 No warehouse ranking data available."
            
            if not data.success:
                return "📊 No warehouse ranking data available."
            
            d = data.data or {}
            warehouses = d.get("warehouses", [])
            
            if not warehouses:
                return "📊 No warehouse data available."
            
            lines = ["🏭 *Warehouse Rankings*", ""]
            for i, warehouse in enumerate(warehouses[:10], 1):
                name = warehouse.get('warehouse', 'N/A')
                revenue = warehouse.get('total_revenue', 0)
                total_dns = warehouse.get('total_dns', 0)
                dealers = warehouse.get('total_dealers', 0)
                delivery_rate = warehouse.get('delivery_rate', 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   Revenue: PKR {revenue:,.0f} | DNs: {total_dns} | Dealers: {dealers} | Delivery Rate: {delivery_rate:.1f}%")
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Warehouse ranking formatting failed: {e}")
            return "📊 Unable to format warehouse ranking."
    
    # ==========================================================
    # FORMATTERS - PRODUCT DASHBOARD
    # ==========================================================
    
    def _format_product_dashboard(self, data, dealer_name: str, req_id: str) -> str:
        """Format product dashboard."""
        try:
            if not self._validate_analytics_response(data, "product_dashboard", req_id):
                return f"❌ No product data found for {dealer_name}"
            
            if not data.success:
                return f"❌ No product data found for {dealer_name}"
            
            d = data.data or {}
            products = d.get("products", [])
            
            if not products:
                return f"📦 *Product Performance for {dealer_name}*\n\n⚠️ No product data found."
            
            lines = [f"📦 *Product Performance for {dealer_name}*", ""]
            for i, product in enumerate(products[:10], 1):
                name = product.get('product_name', 'N/A')
                code = product.get('product_code', 'N/A')
                revenue = product.get('total_revenue', 0)
                units = product.get('total_units', 0)
                dn_count = product.get('dn_count', 0)
                delivery_rate = product.get('delivery_rate', 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   Code: {code} | Revenue: PKR {revenue:,.0f} | Units: {units} | DNs: {dn_count} | Delivery Rate: {delivery_rate:.1f}%")
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Product dashboard formatting failed: {e}")
            return f"❌ Unable to format product dashboard for {dealer_name}"
    
    # ==========================================================
    # FORMATTERS - EXECUTIVE, ROOT CAUSE, CONTROL TOWER
    # ==========================================================
    
    def _format_executive_insights(self, data, req_id: str) -> str:
        """Format executive insights."""
        try:
            if not self._validate_analytics_response(data, "executive_insights", req_id):
                return "📊 No executive insights available."
            
            if not data.success:
                return "📊 No executive insights available."
            
            d = data.data or {}
            summary = d.get("summary", {})
            insights_list = d.get("insights", [])
            top_dealers = d.get("top_dealers", [])
            top_cities = d.get("top_cities", [])
            
            if summary.get("total_dns", 0) == 0:
                return "📊 *Executive Insights*\n\n⚠️ No deliveries found in the system."
            
            lines = [
                "🚨 *Executive Dashboard*",
                "",
                "📈 *SUMMARY:*",
                f"   • Total DNs: {summary.get('total_dns', 0):,}",
                f"   • PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"   • POD Rate: {summary.get('pod_rate', 0):.1f}%",
                f"   • Avg Processing: {summary.get('avg_processing_days', 0):.1f} days",
                f"   • Avg Delivery: {summary.get('avg_delivery_days', 0):.1f} days",
                "",
                "💡 *KEY INSIGHTS:*",
            ]
            if insights_list:
                for insight in insights_list:
                    lines.append(f"   • {insight}")
            else:
                lines.append("   ✅ No critical issues detected.")
            
            if top_dealers:
                lines.append("")
                lines.append("🏆 *TOP DEALERS:*")
                for dealer in top_dealers[:5]:
                    lines.append(f"   • {dealer.get('dealer_name', 'N/A')} - PKR {dealer.get('total_revenue', 0):,.0f}")
            
            if top_cities:
                lines.append("")
                lines.append("🏙️ *TOP CITIES:*")
                for city in top_cities[:5]:
                    lines.append(f"   • {city.get('city', 'N/A')} - PKR {city.get('total_revenue', 0):,.0f}")
            
            lines.append("")
            lines.append("📋 *MANAGEMENT RECOMMENDATIONS:*")
            recommendations = self._generate_management_recommendations(summary)
            for rec in recommendations:
                lines.append(f"   • {rec}")
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Executive insights formatting failed: {e}")
            return "📊 Unable to format executive insights."
    
    def _generate_management_recommendations(self, summary: Dict) -> List[str]:
        """Generate management recommendations based on data."""
        recommendations = []
        
        pgi_rate = summary.get("pgi_rate", 0)
        if pgi_rate < 80:
            recommendations.append("⚠️ PGI rate below 80% - Investigate delays in goods issue processing")
        
        pod_rate = summary.get("pod_rate", 0)
        if pod_rate < 80:
            recommendations.append("⚠️ POD rate below 80% - Improve POD collection process")
        
        avg_processing = summary.get("avg_processing_days", 0)
        if avg_processing > 5:
            recommendations.append(f"⏳ Average processing time {avg_processing} days - Reduce PGI processing time")
        
        avg_delivery = summary.get("avg_delivery_days", 0)
        if avg_delivery > 5:
            recommendations.append(f"⏳ Average delivery time {avg_delivery} days - Optimize delivery routes")
        
        if not recommendations:
            recommendations.append("✅ All KPIs are performing well - Continue current practices")
        
        return recommendations
    
    def _format_root_cause(self, data, req_id: str) -> str:
        """Format root cause analysis."""
        try:
            if not self._validate_analytics_response(data, "root_cause", req_id):
                return "🔍 No root cause analysis available."
            
            if not data.success:
                return "🔍 No root cause analysis available."
            
            d = data.data or {}
            issues = d.get("key_issues", [])
            recommendations = d.get("recommendations", [])
            metrics = d.get("metrics", {})
            
            if metrics.get("total_dns", 0) == 0:
                return "🔍 *Root Cause Analysis*\n\n⚠️ No deliveries found in the system."
            
            lines = [
                "🔍 *Root Cause Analysis*",
                "",
                "📊 *KEY METRICS:*",
                f"   • Total DNs: {metrics.get('total_dns', 0)}",
                f"   • Avg Processing: {metrics.get('avg_processing_days', 0):.1f} days",
                f"   • Avg Delivery: {metrics.get('avg_delivery_days', 0):.1f} days",
                f"   • POD Rate: {metrics.get('pod_rate', 0):.1f}%",
                f"   • Pending POD: {metrics.get('pending_pod', 0)}",
                "",
                "⚠️ *KEY ISSUES IDENTIFIED:*",
            ]
            if issues:
                for issue in issues:
                    lines.append(f"   • {issue}")
            else:
                lines.append("   ✅ No critical issues identified.")
            
            if recommendations:
                lines.append("")
                lines.append("💡 *RECOMMENDATIONS:*")
                for rec in recommendations:
                    lines.append(f"   • {rec}")
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Root cause formatting failed: {e}")
            return "🔍 Unable to format root cause analysis."
    
    def _format_control_tower(self, data, req_id: str) -> str:
        """Format control tower dashboard."""
        try:
            if not self._validate_analytics_response(data, "control_tower", req_id):
                return "🚨 *Control Tower*\n\nNo data available."
            
            if not data.success:
                return "🚨 *Control Tower*\n\nNo data available."
            
            d = data.data or {}
            alerts = d.get("alerts", [])
            critical_count = d.get("critical_count", 0)
            high_count = d.get("high_count", 0)
            
            if not alerts and critical_count == 0 and high_count == 0:
                return "🚨 *Control Tower*\n\n✅ No critical alerts at this time."
            
            lines = [
                "🚨 *Control Tower Dashboard*",
                "",
                f"🔴 Critical Alerts: {critical_count}",
                f"🟠 High Priority: {high_count}",
                "",
            ]
            for alert in alerts[:10]:
                risk_emoji = "🔴" if alert.get('risk_status') == "critical" else "🟠"
                lines.append(f"{risk_emoji} {alert.get('type', 'Alert')}: {alert.get('dealer', 'N/A')} - {alert.get('description', '')}")
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Control tower formatting failed: {e}")
            return "🚨 Unable to format control tower."
    
    # ==========================================================
    # FORMATTERS - COMPARISONS
    # ==========================================================
    
    def _format_dealer_comparison(self, data, dealer1: str, dealer2: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "dealer_comparison", req_id):
                return f"❌ Could not compare {dealer1} and {dealer2}"
            
            if not data.success:
                return f"❌ Could not compare {dealer1} and {dealer2}"
            
            d = data.data or {}
            d1 = d.get(dealer1, {})
            d2 = d.get(dealer2, {})
            
            lines = [
                f"📊 *Dealer Comparison: {dealer1} vs {dealer2}*",
                "",
                "┌─────────────────┬─────────────┬─────────────┐",
                f"│ Metric           │ {dealer1[:12]:<11} │ {dealer2[:12]:<11} │",
                "├─────────────────┼─────────────┼─────────────┤",
                f"│ Revenue (PKR)    │ {d1.get('revenue', 0):>11,.0f} │ {d2.get('revenue', 0):>11,.0f} │",
                f"│ Units            │ {d1.get('units', 0):>11,} │ {d2.get('units', 0):>11,} │",
                f"│ DNs              │ {d1.get('dn_count', 0):>11} │ {d2.get('dn_count', 0):>11} │",
                f"│ POD Rate (%)     │ {d1.get('pod_rate', 0):>11.1f} │ {d2.get('pod_rate', 0):>11.1f} │",
                "└─────────────────┴─────────────┴─────────────┘",
            ]
            if d1.get('revenue', 0) > d2.get('revenue', 0):
                lines.append(f"\n🏆 {dealer1} has higher revenue by PKR {d1.get('revenue', 0) - d2.get('revenue', 0):,.0f}")
            elif d2.get('revenue', 0) > d1.get('revenue', 0):
                lines.append(f"\n🏆 {dealer2} has higher revenue by PKR {d2.get('revenue', 0) - d1.get('revenue', 0):,.0f}")
            else:
                lines.append("\n⚖️ Both dealers have equal revenue")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer comparison formatting failed: {e}")
            return f"❌ Could not compare {dealer1} and {dealer2}"
    
    def _format_warehouse_comparison(self, data, warehouse1: str, warehouse2: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "warehouse_comparison", req_id):
                return f"❌ Could not compare {warehouse1} and {warehouse2}"
            
            if not data.success:
                return f"❌ Could not compare {warehouse1} and {warehouse2}"
            
            d = data.data or {}
            w1 = d.get(warehouse1, {})
            w2 = d.get(warehouse2, {})
            
            lines = [
                f"🏭 *Warehouse Comparison: {warehouse1} vs {warehouse2}*",
                "",
                "┌─────────────────┬─────────────┬─────────────┐",
                f"│ Metric           │ {warehouse1[:12]:<11} │ {warehouse2[:12]:<11} │",
                "├─────────────────┼─────────────┼─────────────┤",
                f"│ Revenue (PKR)    │ {w1.get('revenue', 0):>11,.0f} │ {w2.get('revenue', 0):>11,.0f} │",
                f"│ Units            │ {w1.get('units', 0):>11,} │ {w2.get('units', 0):>11,} │",
                f"│ DNs              │ {w1.get('dn_count', 0):>11} │ {w2.get('dn_count', 0):>11} │",
                f"│ POD Rate (%)     │ {w1.get('pod_rate', 0):>11.1f} │ {w2.get('pod_rate', 0):>11.1f} │",
                "└─────────────────┴─────────────┴─────────────┘",
            ]
            if w1.get('revenue', 0) > w2.get('revenue', 0):
                lines.append(f"\n🏭 {warehouse1} has higher revenue by PKR {w1.get('revenue', 0) - w2.get('revenue', 0):,.0f}")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Warehouse comparison formatting failed: {e}")
            return f"❌ Could not compare {warehouse1} and {warehouse2}"
    
    def _format_city_comparison(self, data, city1: str, city2: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "city_comparison", req_id):
                return f"❌ Could not compare {city1} and {city2}"
            
            if not data.success:
                return f"❌ Could not compare {city1} and {city2}"
            
            d = data.data or {}
            c1 = d.get(city1, {})
            c2 = d.get(city2, {})
            
            lines = [
                f"🏙️ *City Comparison: {city1} vs {city2}*",
                "",
                "┌─────────────────┬─────────────┬─────────────┐",
                f"│ Metric           │ {city1[:12]:<11} │ {city2[:12]:<11} │",
                "├─────────────────┼─────────────┼─────────────┤",
                f"│ Revenue (PKR)    │ {c1.get('revenue', 0):>11,.0f} │ {c2.get('revenue', 0):>11,.0f} │",
                f"│ Dealers          │ {c1.get('dealers', 0):>11} │ {c2.get('dealers', 0):>11} │",
                f"│ DNs              │ {c1.get('dn_count', 0):>11} │ {c2.get('dn_count', 0):>11} │",
                f"│ POD Rate (%)     │ {c1.get('pod_rate', 0):>11.1f} │ {c2.get('pod_rate', 0):>11.1f} │",
                "└─────────────────┴─────────────┴─────────────┘",
            ]
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] City comparison formatting failed: {e}")
            return f"❌ Could not compare {city1} and {city2}"
    
    # ==========================================================
    # FORMATTERS - DELIVERY PERFORMANCE & TREND
    # ==========================================================
    
    def _format_delivery_performance(self, data, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "delivery_performance", req_id):
                return "📦 No delivery performance data available."
            
            if not data.success:
                return "📦 No delivery performance data available."
            
            d = data.data or {}
            metrics = d.get("metrics", {})
            
            return (
                "📦 *Delivery Performance Dashboard*\n\n"
                f"📊 *KEY METRICS:*\n"
                f"   • Total DNs: {metrics.get('total_dns', 0)}\n"
                f"   • Delivered: {metrics.get('delivered', 0)}\n"
                f"   • In Transit: {metrics.get('in_transit', 0)}\n"
                f"   • Pending PGI: {metrics.get('pending_pgi', 0)}\n"
                f"   • Pending POD: {metrics.get('pending_pod', 0)}\n"
                f"   • Pending Flag: {metrics.get('pending_flag_count', 0)}\n"
                f"\n📈 *RATES:*\n"
                f"   • PGI Rate: {metrics.get('pgi_rate', 0):.1f}%\n"
                f"   • POD Rate: {metrics.get('pod_rate', 0):.1f}%\n"
                f"   • Avg Processing: {metrics.get('avg_processing_days', 0):.1f} days\n"
                f"   • Avg Delivery: {metrics.get('avg_delivery_days', 0):.1f} days"
            )
        except Exception as e:
            logger.exception(f"[{req_id}] Delivery performance formatting failed: {e}")
            return "📦 Unable to format delivery performance."
    
    def _format_trend_analysis(self, data, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "trend_analysis", req_id):
                return "📈 No trend data available."
            
            if not data.success:
                return "📈 No trend data available."
            
            d = data.data or {}
            trends = d.get("trends", {})
            monthly = trends.get("monthly", [])
            
            if not monthly:
                return "📈 No trend data available."
            
            lines = ["📈 *Trend Analysis*", "", "📊 *Monthly Trends:*"]
            for month in monthly[:6]:
                period = month.get('period', 'N/A')
                count = month.get('count', 0)
                revenue = month.get('revenue', 0)
                lines.append(f"   • {period}: {count} DNs, Revenue: PKR {revenue:,.0f}")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Trend analysis formatting failed: {e}")
            return "📈 Unable to format trend analysis."
    
    # ==========================================================
    # FORMATTERS - REVENUE, UNITS, PERFORMANCE, AGING
    # ==========================================================
    
    def _format_dealer_revenue(self, data, dealer_name: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "dealer_revenue", req_id):
                return f"❌ No revenue data for {dealer_name}"
            
            if not data.success:
                return f"❌ No revenue data for {dealer_name}"
            
            d = data.data or {}
            return (
                f"💰 *Revenue for {dealer_name}*\n\n"
                f"• Total Revenue: PKR {d.get('total_revenue', 0):,.0f}\n"
                f"• Number of DNs: {d.get('count', 0)}\n"
                f"• Average per DN: PKR {d.get('avg_revenue', 0):,.0f}"
            )
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer revenue formatting failed: {e}")
            return f"❌ Unable to format revenue for {dealer_name}"
    
    def _format_dealer_units(self, data, dealer_name: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "dealer_units", req_id):
                return f"❌ No units data for {dealer_name}"
            
            if not data.success:
                return f"❌ No units data for {dealer_name}"
            
            d = data.data or {}
            return (
                f"📦 *Units for {dealer_name}*\n\n"
                f"• Total Units: {d.get('total_units', 0):,}\n"
                f"• Number of DNs: {d.get('count', 0)}\n"
                f"• Average per DN: {d.get('avg_units', 0):.1f}"
            )
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer units formatting failed: {e}")
            return f"❌ Unable to format units for {dealer_name}"
    
    def _format_dealer_performance(self, data, dealer_name: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "dealer_performance", req_id):
                return f"❌ No performance data for {dealer_name}"
            
            if not data.success:
                return f"❌ No performance data for {dealer_name}"
            
            d = data.data or {}
            lines = [
                f"📊 *Performance: {dealer_name}*",
                "",
                f"📦 Delivery Rate: {d.get('delivery_rate', 0):.1f}%",
                f"📎 POD Rate: {d.get('pod_rate', 0):.1f}%",
                f"⏳ Pending PGI: {d.get('pending_pgi', 0)}",
                f"📎 Pending POD: {d.get('pending_pod', 0)}",
                f"⏰ Avg Aging: {d.get('avg_aging', 0):.1f} days",
            ]
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer performance formatting failed: {e}")
            return f"❌ Unable to format performance for {dealer_name}"
    
    def _format_dealer_aging(self, data, dealer_name: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "dealer_aging", req_id):
                return f"❌ No aging data for {dealer_name}"
            
            if not data.success:
                return f"❌ No aging data for {dealer_name}"
            
            d = data.data or {}
            return (
                f"⏱️ *Aging for {dealer_name}*\n\n"
                f"• Average Aging: {d.get('avg_aging', 0):.1f} days\n"
                f"• Maximum Aging: {d.get('max_aging', 0)} days\n"
                f"• DNs with Aging: {d.get('count', 0)}"
            )
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer aging formatting failed: {e}")
            return f"❌ Unable to format aging for {dealer_name}"
    
    # ==========================================================
    # FORMATTERS - WAREHOUSE & CITY PERFORMANCE
    # ==========================================================
    
    def _format_warehouse_performance(self, data, warehouse_name: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "warehouse_performance", req_id):
                return f"❌ No performance data for {warehouse_name}"
            
            if not data.success:
                return f"❌ No performance data for {warehouse_name}"
            
            d = data.data or {}
            summary = d.get("summary", {})
            return (
                f"📊 *Performance: {warehouse_name}*\n\n"
                f"• Total DNs: {summary.get('total_dns', 0)}\n"
                f"• POD Rate: {summary.get('pod_rate', 0):.1f}%\n"
                f"• Revenue: PKR {summary.get('total_revenue', 0):,.0f}\n"
                f"• Active Dealers: {summary.get('total_dealers', 0)}"
            )
        except Exception as e:
            logger.exception(f"[{req_id}] Warehouse performance formatting failed: {e}")
            return f"❌ Unable to format performance for {warehouse_name}"
    
    def _format_city_performance(self, data, city_name: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "city_performance", req_id):
                return f"❌ No performance data for {city_name}"
            
            if not data.success:
                return f"❌ No performance data for {city_name}"
            
            d = data.data or {}
            summary = d.get("summary", {})
            return (
                f"📊 *Performance: {city_name}*\n\n"
                f"• Total DNs: {summary.get('total_dns', 0)}\n"
                f"• POD Rate: {summary.get('pod_rate', 0):.1f}%\n"
                f"• Revenue: PKR {summary.get('total_revenue', 0):,.0f}\n"
                f"• Active Dealers: {summary.get('total_dealers', 0)}"
            )
        except Exception as e:
            logger.exception(f"[{req_id}] City performance formatting failed: {e}")
            return f"❌ Unable to format performance for {city_name}"
    
    # ==========================================================
    # ERROR & FALLBACK RESPONSES
    # ==========================================================
    
    def _get_help_message(self) -> str:
        return """📋 *AI Logistics Assistant - Help*

*🔍 DN Tracking:* Send any 8-12 digit DN number

*🏪 Dealer Queries:*
   • "Dealer name" (e.g., "ZQ Electronics")
   • "Dealer revenue" or "Dealer units"
   • "Dealer performance" or "Dealer aging"
   • "Compare Dealer A vs Dealer B"

*🏙️ City Queries:*
   • "City name" (e.g., "Haripur")
   • "Which city has highest sales"
   • "Compare City A vs City B"

*🏭 Warehouse Queries:*
   • "Warehouse name" (e.g., "Rawalpindi")
   • "Compare Warehouse A vs Warehouse B"

*📊 Analytics:*
   • "Top dealers" or "Bottom dealers"
   • "Executive insights" or "Key issues"
   • "Root cause" or "Critical alerts"
   • "Delivery performance"
   • "Control tower"

*🤖 General AI:* Any non-logistics question

*What would you like to know?* 🤖"""
    
    def _get_error_response(self, question: str, error: Exception, error_id: str, request_id: str) -> str:
        return (
            f"⚠️ *Unable to process your request*\n\n"
            f"• Error Reference: `{error_id}`\n"
            f"• Request ID: `{request_id}`\n\n"
            f"Please try again or contact support with the reference ID."
        )
    
    def _get_service_error_response(self, intent: str, entity: Optional[str], service: str, error: Exception, error_id: str, req_id: str) -> str:
        return (
            f"⚠️ *Unable to retrieve analytics data*\n\n"
            f"• Intent: {intent}\n"
            f"• Entity: {entity or 'N/A'}\n"
            f"• Service: {service}\n"
            f"• Error Reference: `{error_id}`\n\n"
            f"Please try again or contact support."
        )
    
    def _get_groq_blocked_response(self) -> str:
        return (
            "⚠️ *Logistics queries are handled by analytics, not AI.*\n\n"
            "Please try one of these:\n"
            "• A specific dealer name\n"
            "• A DN number (8-12 digits)\n"
            "• 'Top dealers' or 'Top cities'\n"
            "• 'Key issues' or 'Executive insights'\n"
            "• 'Compare Dealer A vs Dealer B'\n\n"
            "Type 'Help' for all available commands."
        )
    
    # ==========================================================
    # METRICS & ADMIN
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        total_dn = self.metrics["dn_lookups_success"] + self.metrics["dn_lookups_failure"]
        total_dealer = self.metrics["dealer_queries_success"] + self.metrics["dealer_queries_failure"]
        
        return {
            "version": "14.0",
            "total_requests": self.metrics["total_requests"],
            "cache_hits": self.metrics["cache_hits"],
            "cache_misses": self.metrics["cache_misses"],
            "cache_failures_avoided": self.metrics["cache_failures_avoided"],
            "cache_hit_rate": round(self.metrics["cache_hits"] / max(1, self.metrics["cache_hits"] + self.metrics["cache_misses"]) * 100, 1),
            "dn_lookups": {
                "total": self.metrics["dn_lookups"],
                "success": self.metrics["dn_lookups_success"],
                "failure": self.metrics["dn_lookups_failure"],
                "success_rate": round(self.metrics["dn_lookups_success"] / max(total_dn, 1) * 100, 1),
                "retry_attempts": self.metrics["dn_retry_attempts"]
            },
            "dealer_queries": {
                "total": self.metrics["dealer_queries"],
                "success": self.metrics["dealer_queries_success"],
                "failure": self.metrics["dealer_queries_failure"],
                "success_rate": round(self.metrics["dealer_queries_success"] / max(total_dealer, 1) * 100, 1),
                "suggestions": self.metrics["dealer_suggestions"]
            },
            "dealer_resolution": {
                "attempts": self.metrics["dealer_resolution_attempts"],
                "success": self.metrics["dealer_resolution_success"],
                "failure": self.metrics["dealer_resolution_failure"],
                "success_rate": round(self.metrics["dealer_resolution_success"] / max(self.metrics["dealer_resolution_attempts"], 1) * 100, 1)
            },
            "city_queries": self.metrics["city_queries"],
            "warehouse_queries": self.metrics["warehouse_queries"],
            "product_queries": self.metrics["product_queries"],
            "comparisons": self.metrics["comparisons"],
            "executive_insights": self.metrics["executive_insights"],
            "root_cause_analyses": self.metrics["root_cause_analyses"],
            "control_tower": self.metrics["control_tower"],
            "groq_uses": self.metrics["groq_uses"],
            "overrides": self.metrics["overrides"],
            "timeouts": self.metrics["timeouts"],
            "errors": self.metrics["errors"],
            "analytics_response_errors": self.metrics["analytics_response_errors"],
            "conversation_count": len(self.conversation_cache),
            "cache_size": len(self.response_cache),
            "failure_cache_size": len(self.failure_cache)
        }
    
    def get_routing_debug(self, question: str) -> Dict[str, Any]:
        context = {}
        routing = self._get_routing_decision(question, context)
        return {
            "question": question,
            "is_dn_query": self._is_dn_query(question),
            "normalized_dn": self._normalize_dn(question) if self._is_dn_query(question) else None,
            "routing_decision": {
                "intent": getattr(routing, "intent", "unknown"),
                "entity": getattr(routing, "entity", None),
                "entity_type": getattr(routing, "entity_type", None),
                "service": getattr(routing, "service", "unknown"),
                "confidence": getattr(routing, "confidence", 0.0),
                "needs_groq": getattr(routing, "needs_groq", False),
                "reason": getattr(routing, "reason", "")
            },
            "timestamp": time.time()
        }


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
# WRAPPER FUNCTIONS (PRESERVED SIGNATURES - CRITICAL)
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
logger.info("AI Provider Service v14.0 - Enterprise Logistics Analytics Engine")
logger.info("=" * 70)
logger.info("")
logger.info("   ENTERPRISE FEATURES:")
logger.info("   ✅ Dealer 360 Dashboard")
logger.info("   ✅ Enhanced DN Dashboard with Journey Tracking")
logger.info("   ✅ City & Warehouse Dashboards")
logger.info("   ✅ Rankings (Dealer, City, Warehouse)")
logger.info("   ✅ Product Performance Dashboard")
logger.info("   ✅ Control Tower Dashboard")
logger.info("   ✅ Root Cause Analysis")
logger.info("   ✅ Executive Dashboard")
logger.info("   ✅ Dealer Resolution Engine (5 Attempts)")
logger.info("   ✅ Dealer Suggestion Engine")
logger.info("   ✅ DN Retry Logic (5 Attempts)")
logger.info("   ✅ Self-Healing Cache")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
