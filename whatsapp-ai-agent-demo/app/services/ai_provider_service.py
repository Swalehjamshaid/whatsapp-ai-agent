# ==========================================================
# FILE: app/services/ai_provider_service.py (v13.0 - FULL ALIGNMENT)
# ==========================================================
# PURPOSE: Master Orchestrator - FINAL AUTHORITY & GOVERNANCE LAYER
# 
# FULL ALIGNMENT FIXES:
# 1. ✅ AnalyticsResponse validation helper for ALL formatters
# 2. ✅ customer_name standardization (Never sold_to_party_name)
# 3. ✅ All formatters expect AnalyticsResponse with proper structure
# 4. ✅ DN normalization: re.sub(r"\D", "", question.strip())
# 5. ✅ Production diagnostics at every step
# 6. ✅ All analytics methods validated against analytics_service.py
# 7. ✅ Error handling: Never expose traceback to WhatsApp
# 8. ✅ KPI standardization: COUNT(DISTINCT dn_no), SUM(dn_qty), SUM(dn_amount)
# 9. ✅ Complete formatter audit and correction
# 10. ✅ 100% PostgreSQL compliance (no Excel/CSV)
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
from datetime import datetime

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

# DN Pattern: 8-12 digits (loose matching)
DN_PATTERN_LOOSE = re.compile(r'\b(\d{8,12})\b')

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
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_city": self.last_city,
            "last_dn": self.last_dn,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number,
            "last_question": self.last_question
        }


# ==========================================================
# MASTER ORCHESTRATOR - FINAL AUTHORITY
# ==========================================================

class AIOrchestrator:
    """
    MASTER ORCHESTRATOR - FINAL AUTHORITY & GOVERNANCE LAYER v13.0
    
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
        
        # Caches
        self.response_cache = TTLCache(maxsize=500, ttl=CACHE_TTL_SECONDS)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "dn_lookups": 0,
            "dn_lookups_success": 0,
            "dn_lookups_failure": 0,
            "dealer_queries": 0,
            "dealer_queries_success": 0,
            "dealer_queries_failure": 0,
            "city_queries": 0,
            "warehouse_queries": 0,
            "comparisons": 0,
            "executive_insights": 0,
            "root_cause_analyses": 0,
            "groq_uses": 0,
            "overrides": 0,
            "rejections": 0,
            "timeouts": 0,
            "errors": 0,
            "service_successes": 0,
            "service_failures": 0,
            "analytics_response_errors": 0
        }
        
        logger.info("=" * 70)
        logger.info("AI Orchestrator v13.0 - Full Alignment")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   FULL ALIGNMENT FIXES:")
        logger.info("   ✅ AnalyticsResponse validation helper")
        logger.info("   ✅ customer_name standardization")
        logger.info("   ✅ All formatters support AnalyticsResponse")
        logger.info("   ✅ DN normalization: re.sub(r'\\D', '', question)")
        logger.info("   ✅ Production diagnostics at every step")
        logger.info("   ✅ 100% PostgreSQL compliance")
        logger.info("   ✅ KPI standardization")
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
        """
        Validate AnalyticsResponse before formatting.
        
        Returns:
            True if valid, False otherwise
        """
        if response is None:
            logger.error(f"[{request_id}] AnalyticsResponse is None for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if not hasattr(response, 'success'):
            logger.error(f"[{request_id}] AnalyticsResponse missing 'success' attribute for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if not hasattr(response, 'data'):
            logger.error(f"[{request_id}] AnalyticsResponse missing 'data' attribute for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if not hasattr(response, 'error'):
            logger.error(f"[{request_id}] AnalyticsResponse missing 'error' attribute for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        return True
    
    def _is_analytics_response(self, obj) -> bool:
        """Check if object is AnalyticsResponse."""
        if obj is None:
            return False
        return hasattr(obj, 'success') and hasattr(obj, 'data') and hasattr(obj, 'error')
    
    # ==========================================================
    # DN NORMALIZATION (CRITICAL FIX)
    # ==========================================================
    
    def _normalize_dn(self, text: str) -> str:
        """
        Normalize DN number by removing all non-digit characters.
        
        Examples:
        - "6243611858." → "6243611858"
        - "DN 6243611858" → "6243611858"
        - "6243611858-0" → "6243611858"
        - "6243611858.0" → "6243611858"
        """
        return re.sub(r"\D", "", text.strip())
    
    # ==========================================================
    # DN DETECTION (CRITICAL FIX - Not fullmatch)
    # ==========================================================
    
    def _is_dn_query(self, question: str) -> bool:
        """
        Check if query contains a DN number (8-12 digits).
        
        FIXED: Uses loose detection, not fullmatch.
        """
        digits = self._normalize_dn(question)
        return 8 <= len(digits) <= 12
    
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
                
                response = self._execute_dn_lookup(dn_normalized, req_id)
                
                logger.info(f"[{req_id}] DN Lookup Result={'success' if response and '❌' not in response else 'failure'}")
                
                self._update_context(phone_number, "dn_lookup", "dn", question, req_id)
                self._cache_response(question, phone_number, response)
                return response
            
            # ==========================================================
            # STEP 2: Entity Resolution (SchemaService Verifies)
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
                
                if self._is_comparison_query(question):
                    logger.info(f"[{req_id}] ⚡ Comparison Detected: {entity_type}")
                    self.metrics["comparisons"] += 1
                    response = self._execute_comparison(entity_type, question, entity_name, req_id)
                    self._update_context(phone_number, f"compare_{entity_type}s", entity_type, entity_name, req_id)
                    self._cache_response(question, phone_number, response)
                    return response
                
                if self._is_entity_only_query(question, entity_name):
                    logger.info(f"[{req_id}] ⚡ Entity-Only: {entity_type}_dashboard")
                    self.metrics["overrides"] += 1
                    response = self._execute_entity_dashboard(entity_type, entity_name, req_id)
                    self._update_context(phone_number, f"{entity_type}_dashboard", entity_type, entity_name, req_id)
                    self._cache_response(question, phone_number, response)
                    return response
            
            # ==========================================================
            # STEP 3: Intent Detection (AIQueryService Suggests)
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
            # STEP 4: Governance Override (AIProviderService Decides)
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
            
            # ==========================================================
            # STEP 6: Groq Governance (Enrich Only, Never Replace)
            # ==========================================================
            
            if needs_groq and service != "groq":
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
            self._cache_response(question, phone_number, response)
            
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
    # DN EXECUTION (With Diagnostics & AnalyticsResponse)
    # ==========================================================
    
    def _execute_dn_lookup(self, dn_number: str, req_id: str) -> str:
        """Execute DN lookup - bypass all other logic."""
        try:
            logger.info(f"[{req_id}] 🔍 Executing DN Lookup: {dn_number}")
            
            result = self.analytics.get_dn_analytics(dn_number)
            
            logger.info(f"[{req_id}] Response Type={type(result).__name__}")
            
            # Validate AnalyticsResponse
            if not self._validate_analytics_response(result, "get_dn_analytics", req_id):
                self.metrics["dn_lookups_failure"] += 1
                return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again."
            
            logger.info(f"[{req_id}] AnalyticsResponse Success={result.success}")
            
            if not result.success:
                self.metrics["dn_lookups_failure"] += 1
                return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again."
            
            formatted = self._format_dn_details(result, req_id)
            self.metrics["dn_lookups_success"] += 1
            
            logger.info(f"[{req_id}] Formatter Success")
            return formatted
                
        except Exception as e:
            logger.exception(f"[{req_id}] DN lookup failed for {dn_number}: {e}")
            self.metrics["dn_lookups_failure"] += 1
            return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again."
    
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
    # ENTITY DASHBOARD EXECUTION (With AnalyticsResponse)
    # ==========================================================
    
    def _execute_entity_dashboard(self, entity_type: str, entity_name: str, req_id: str) -> str:
        try:
            logger.info(f"[{req_id}] 📊 Entity Dashboard: {entity_type}={entity_name}")
            
            if entity_type == "dealer":
                self.metrics["dealer_queries"] += 1
                result = self.analytics.get_dealer_dashboard(entity_name)
                
                if not self._validate_analytics_response(result, "get_dealer_dashboard", req_id):
                    self.metrics["dealer_queries_failure"] += 1
                    return f"❌ Unable to retrieve dashboard for {entity_name}."
                
                self.metrics["dealer_queries_success"] += 1
                return self._format_dealer_dashboard(result, entity_name, req_id)
                
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
            self.metrics["dealer_queries_failure"] += 1
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
    # ANALYTICS EXECUTION (With AnalyticsResponse)
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
        
        if intent == "city_ranking":
            result = self.analytics.get_city_ranking(limit=10, top=True)
            return self._format_city_ranking(result, req_id)
        
        # DEALER RANKING
        if intent == "dealer_ranking":
            result = self.analytics.get_dealer_ranking(limit=10, top=True)
            return self._format_dealer_ranking(result, True, req_id)
        
        # EXECUTIVE & ROOT CAUSE
        if intent == "executive_insight":
            self.metrics["executive_insights"] += 1
            result = self.analytics.get_executive_summary()
            return self._format_executive_insights(result, req_id)
        
        if intent == "root_cause":
            self.metrics["root_cause_analyses"] += 1
            result = self.analytics.get_root_cause_insights()
            return self._format_root_cause(result, req_id)
        
        if intent == "control_tower":
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
    
    # ==========================================================
    # CACHE MANAGEMENT
    # ==========================================================
    
    def _get_cached_response(self, question: str, phone_number: Optional[str]) -> Optional[str]:
        cache_key = self._generate_cache_key(question, phone_number)
        return self.response_cache.get(cache_key)
    
    def _cache_response(self, question: str, phone_number: Optional[str], response: str):
        cache_key = self._generate_cache_key(question, phone_number)
        self.response_cache[cache_key] = response
    
    def _generate_cache_key(self, question: str, phone_number: Optional[str]) -> str:
        key = question.lower().strip()
        if phone_number:
            key = f"{phone_number}:{key}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def clear_caches(self):
        self.response_cache.clear()
        self.conversation_cache.clear()
        logger.info("🗑️ All caches cleared")
        return {"status": "cleared", "version": "13.0"}
    
    # ==========================================================
    # FORMATTERS - DEALER (FULLY ALIGNED WITH AnalyticsResponse)
    # ==========================================================
    
    def _format_dealer_dashboard(self, data, dealer_name: str, req_id: str) -> str:
        """Format dealer dashboard with AnalyticsResponse support."""
        try:
            if not self._validate_analytics_response(data, "dealer_dashboard", req_id):
                return f"❌ Unable to retrieve dashboard for {dealer_name}."
            
            if not data.success:
                return f"❌ No data found for {dealer_name}"
            
            response_data = data.data or {}
            
            # Extract required sections with safe defaults
            profile = response_data.get("profile", {})
            summary = response_data.get("summary", {})
            aging = response_data.get("aging", {})
            performance = response_data.get("performance", {})
            
            total_dns = summary.get("total_dns", 0)
            
            if total_dns == 0:
                return f"🏪 *{dealer_name} - No Deliveries Found*\n\n" \
                       f"⚠️ No delivery data found for this dealer."
            
            dealer_code = profile.get("dealer_code", "N/A")
            customer_code = profile.get("customer_code", "N/A")
            city = profile.get("city", "N/A")
            warehouse = profile.get("warehouse", "N/A")
            
            lines = [
                f"🏪 *{dealer_name} - Dashboard*",
                "",
                f"📋 *Dealer Code:* {dealer_code}",
                f"📋 *Customer Code:* {customer_code}",
                f"🏙️ *City:* {city}",
                f"🏭 *Warehouse:* {warehouse}",
                "",
                f"📄 *Total DNs:* {summary.get('total_dns', 0):,}",
                f"📦 *Total Units:* {summary.get('total_units', 0):,}",
                f"💰 *Revenue:* PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                f"📊 *Delivery Status:*",
                f"   ✅ Delivered: {summary.get('delivered', 0)}",
                f"   🚚 In Transit: {summary.get('in_transit', 0)}",
                f"   ⏳ Pending PGI: {aging.get('pending_pgi', 0)}",
                f"   📎 Pending POD: {aging.get('pending_pod', 0)}",
                "",
                f"📈 *Performance:*",
                f"   📦 Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"   📎 POD Rate: {summary.get('pod_rate', 0):.1f}%",
                f"   ⏰ Avg Delivery Aging: {aging.get('avg_delivery_aging', 0):.1f} days",
            ]
            
            risk_status = performance.get('risk_status', 'low')
            risk_emoji = self.schema.get_risk_emoji(risk_status) if hasattr(self.schema, 'get_risk_emoji') else "🟢"
            lines.append(f"   {risk_emoji} Risk Status: {risk_status.upper()}")
            
            logger.info(f"[{req_id}] Dealer Records={total_dns}")
            return "\n".join(lines)
            
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer dashboard formatting failed: {e}")
            return f"❌ Unable to format dealer dashboard for {dealer_name}"
    
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
    
    def _format_dealer_ranking(self, data, top: bool, req_id: str) -> str:
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
                revenue = dealer.get('total_revenue', 0)
                delivery_rate = dealer.get('delivery_rate', 0)
                lines.append(f"{i}. {dealer.get('dealer_name', 'N/A')}\n   Revenue: PKR {revenue:,.0f} | Delivery Rate: {delivery_rate:.1f}%")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer ranking formatting failed: {e}")
            return "📊 Unable to format dealer ranking."
    
    # ==========================================================
    # FORMATTERS - WAREHOUSE & CITY (FULLY ALIGNED)
    # ==========================================================
    
    def _format_warehouse_dashboard(self, data, warehouse_name: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "warehouse_dashboard", req_id):
                return f"❌ No data found for {warehouse_name}"
            
            if not data.success:
                return f"❌ No data found for {warehouse_name}"
            
            d = data.data or {}
            summary = d.get("summary", {})
            
            if summary.get("total_dns", 0) == 0:
                return f"🏭 *{warehouse_name} - No Deliveries Found*\n\n" \
                       f"⚠️ No delivery data found for this warehouse."
            
            lines = [
                f"🏭 *{warehouse_name} - Dashboard*",
                "",
                f"📄 *Total DNs:* {summary.get('total_dns', 0):,}",
                f"📦 *Total Units:* {summary.get('total_units', 0):,}",
                f"💰 *Revenue:* PKR {summary.get('total_revenue', 0):,.0f}",
                f"📎 *POD Rate:* {summary.get('pod_rate', 0):.1f}%",
                f"🏪 *Active Dealers:* {summary.get('total_dealers', 0)}",
            ]
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Warehouse dashboard formatting failed: {e}")
            return f"❌ Unable to format warehouse dashboard for {warehouse_name}"
    
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
    
    def _format_city_dashboard(self, data, city_name: str, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "city_dashboard", req_id):
                return f"❌ No data found for {city_name}"
            
            if not data.success:
                return f"❌ No data found for {city_name}"
            
            d = data.data or {}
            summary = d.get("summary", {})
            
            if summary.get("total_dns", 0) == 0:
                return f"🏙️ *{city_name} - No Deliveries Found*\n\n" \
                       f"⚠️ No delivery data found for this city."
            
            lines = [
                f"🏙️ *{city_name} - Dashboard*",
                "",
                f"📄 *Total DNs:* {summary.get('total_dns', 0):,}",
                f"💰 *Revenue:* PKR {summary.get('total_revenue', 0):,.0f}",
                f"🏪 *Active Dealers:* {summary.get('total_dealers', 0)}",
                f"📎 *POD Rate:* {summary.get('pod_rate', 0):.1f}%",
            ]
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] City dashboard formatting failed: {e}")
            return f"❌ Unable to format city dashboard for {city_name}"
    
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
    
    def _format_city_ranking(self, data, req_id: str) -> str:
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
                revenue = city.get('total_revenue', 0)
                delivery_rate = city.get('delivery_rate', 0)
                dealers = city.get('total_dealers', 0)
                lines.append(f"{i}. {city.get('city', 'N/A')}\n   Revenue: PKR {revenue:,.0f} | Delivery Rate: {delivery_rate:.1f}% | Dealers: {dealers}")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] City ranking formatting failed: {e}")
            return "📊 Unable to format city ranking."
    
    # ==========================================================
    # FORMATTERS - COMPARISON (FULLY ALIGNED)
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
    # FORMATTERS - DN DETAILS (FULLY ALIGNED)
    # ==========================================================
    
    def _format_dn_details(self, data, req_id: str) -> str:
        """Format DN details with full information."""
        try:
            if not self._validate_analytics_response(data, "dn_details", req_id):
                return "❌ Unable to retrieve DN details."
            
            if not data.success:
                logger.warning(f"[{req_id}] DN not found")
                return "❌ DN not found."
            
            record = data.data.get("record", {})
            validation = data.data.get("validation", {})
            status = data.data.get("status", "unknown")
            
            # Extract fields using customer_name (NOT sold_to_party_name)
            dn_no = record.get('dn_number', record.get('dn_no', 'N/A'))
            dealer_name = record.get('customer_name', record.get('dealer', 'N/A'))
            dealer_code = record.get('dealer_code', 'N/A')
            customer_code = record.get('customer_code', 'N/A')
            warehouse = record.get('warehouse', 'N/A')
            city = record.get('ship_to_city', 'N/A')
            units = record.get('units', 0)
            amount = record.get('amount', record.get('dn_amount', 0))
            delivery_status = record.get('delivery_status', 'N/A')
            pgi_status = record.get('pgi_status', 'N/A')
            pod_status = record.get('pod_status', 'N/A')
            pending_flag = record.get('pending_flag', False)
            
            # Date fields
            dn_date = record.get('dn_create_date', 'N/A')
            pgi_date = record.get('good_issue_date', 'N/A')
            pod_date = record.get('pod_date', 'N/A')
            
            # Aging
            pgi_aging = record.get('pgi_aging_days', 'N/A')
            pod_aging = record.get('pod_aging_days', 'N/A')
            total_aging = record.get('total_aging_days', 'N/A')
            
            # Status display
            status_map = {
                "pending_pgi": "⏳ Pending PGI",
                "pending_pod": "🚚 In Transit (POD Pending)",
                "delivered": "✅ Delivered",
                "completed": "✅ Completed",
                "unknown": "❓ Unknown"
            }
            status_display = status_map.get(status.lower() if status else "unknown", "❓ Unknown")
            
            lines = [
                "📄 *DN Details*",
                "",
                f"📋 *DN No:* {dn_no}",
                f"🏪 *Dealer:* {dealer_name}",
                f"📋 *Dealer Code:* {dealer_code}",
                f"📋 *Customer Code:* {customer_code}",
                "",
                f"🏭 *Warehouse:* {warehouse}",
                f"🏙️ *City:* {city}",
                "",
                f"📦 *Units:* {units}",
                f"💰 *Revenue:* PKR {amount:,.0f}",
                "",
                f"📊 *Status:* {status_display}",
                f"   • PGI Status: {pgi_status}",
                f"   • POD Status: {pod_status}",
                f"   • Delivery Status: {delivery_status}",
                f"   • Pending Flag: {'✅ Yes' if pending_flag else '❌ No'}",
                "",
                f"📅 *Dates:*",
                f"   • DN Create: {dn_date}",
                f"   • PGI: {pgi_date}",
                f"   • POD: {pod_date}",
                "",
                f"⏱️ *Aging:*",
                f"   • PGI Aging: {pgi_aging} days" if pgi_aging != 'N/A' else "   • PGI Aging: Not available",
                f"   • POD Aging: {pod_aging} days" if pod_aging != 'N/A' else "   • POD Aging: Not available",
                f"   • Total Aging: {total_aging} days" if total_aging != 'N/A' else "   • Total Aging: Not available",
            ]
            
            is_valid = validation.get('is_valid', True)
            issues = validation.get('issues', [])
            
            if is_valid and not issues:
                lines.append("")
                lines.append("✅ *Data Quality: VALID*")
            elif issues:
                lines.append("")
                lines.append("⚠️ *Data Quality Issues:*")
                for issue in issues:
                    lines.append(f"   • {issue}")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.exception(f"[{req_id}] DN formatting failed: {e}")
            return f"❌ Unable to format DN details."
    
    # ==========================================================
    # FORMATTERS - EXECUTIVE, ROOT CAUSE, ETC.
    # ==========================================================
    
    def _format_executive_insights(self, data, req_id: str) -> str:
        try:
            if not self._validate_analytics_response(data, "executive_insights", req_id):
                return "📊 No executive insights available."
            
            if not data.success:
                return "📊 No executive insights available."
            
            d = data.data or {}
            summary = d.get("summary", {})
            insights_list = d.get("insights", [])
            top_dealers = d.get("top_dealers", [])
            
            if summary.get("total_dns", 0) == 0:
                return "📊 *Executive Insights*\n\n" \
                       "📈 *Overview:*\n" \
                       "   • No deliveries found in the system.\n\n" \
                       "⚠️ *Critical Issues:*\n" \
                       "   • No data available for analysis.\n\n" \
                       "💡 *Recommended Actions:*\n" \
                       "   • Please ensure data is imported into the system."
            
            lines = [
                "🚨 *Executive Insights*",
                "",
                f"📈 *Overview:*",
                f"   • Total DNs: {summary.get('total_dns', 0):,}",
                f"   • PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"   • POD Rate: {summary.get('pod_rate', 0):.1f}%",
                f"   • Avg Processing: {summary.get('avg_processing_days', 0):.1f} days",
                f"   • Avg Delivery: {summary.get('avg_delivery_days', 0):.1f} days",
                "",
                "💡 *Insights:*",
            ]
            if insights_list:
                for insight in insights_list:
                    lines.append(f"   • {insight}")
            else:
                lines.append("   ✅ No critical issues detected.")
            
            if top_dealers:
                lines.append("")
                lines.append("🏆 *Top Dealers:*")
                for dealer in top_dealers[:5]:
                    lines.append(f"   • {dealer.get('dealer_name', 'N/A')} - PKR {dealer.get('total_revenue', 0):,.0f}")
            
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Executive insights formatting failed: {e}")
            return "📊 Unable to format executive insights."
    
    def _format_root_cause(self, data, req_id: str) -> str:
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
                return "🔍 *Root Cause Analysis*\n\n" \
                       "📊 *Key Metrics:*\n" \
                       "   • No deliveries found in the system.\n\n" \
                       "⚠️ *Key Issues Identified:*\n" \
                       "   • No data available for analysis.\n\n" \
                       "💡 *Data-Driven Recommendations:*\n" \
                       "   • Please ensure data is imported into the system."
            
            lines = [
                "🔍 *Root Cause Analysis*",
                "",
                f"📊 *Key Metrics:*",
                f"   • Total DNs: {metrics.get('total_dns', 0)}",
                f"   • Avg Processing: {metrics.get('avg_processing_days', 0):.1f} days",
                f"   • Avg Delivery: {metrics.get('avg_delivery_days', 0):.1f} days",
                f"   • POD Rate: {metrics.get('pod_rate', 0):.1f}%",
                f"   • Pending POD: {metrics.get('pending_pod', 0)}",
                "",
                "⚠️ *Key Issues Identified:*",
            ]
            if issues:
                for issue in issues:
                    lines.append(f"   • {issue}")
            else:
                lines.append("   ✅ No critical issues identified.")
            if recommendations:
                lines.append("")
                lines.append("💡 *Data-Driven Recommendations:*")
                for rec in recommendations:
                    lines.append(f"   • {rec}")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Root cause formatting failed: {e}")
            return "🔍 Unable to format root cause analysis."
    
    def _format_control_tower(self, data, req_id: str) -> str:
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
                "🚨 *Control Tower*",
                "",
                f"🔴 Critical: {critical_count}",
                f"🟠 High: {high_count}",
                "",
            ]
            for alert in alerts[:10]:
                risk_emoji = "🔴" if alert.get('risk_status') == "critical" else "🟠"
                lines.append(f"{risk_emoji} {alert.get('type', 'Alert')}: {alert.get('dealer', 'N/A')} - {alert.get('description', '')}")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Control tower formatting failed: {e}")
            return "🚨 Unable to format control tower."
    
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
                f"📊 *Key Metrics:*\n"
                f"   • Total DNs: {metrics.get('total_dns', 0)}\n"
                f"   • Delivered: {metrics.get('delivered', 0)}\n"
                f"   • In Transit: {metrics.get('in_transit', 0)}\n"
                f"   • Pending PGI: {metrics.get('pending_pgi', 0)}\n"
                f"   • Pending POD: {metrics.get('pending_pod', 0)}\n"
                f"   • Pending Flag: {metrics.get('pending_flag_count', 0)}\n"
                f"\n📈 *Rates:*\n"
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
                lines.append(f"   • {month.get('period', 'N/A')}: {month.get('count', 0)} DNs, Revenue: PKR {month.get('revenue', 0):,.0f}")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"[{req_id}] Trend analysis formatting failed: {e}")
            return "📈 Unable to format trend analysis."
    
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
            "total_requests": self.metrics["total_requests"],
            "cache_hits": self.metrics["cache_hits"],
            "cache_misses": self.metrics["cache_misses"],
            "cache_hit_rate": round(self.metrics["cache_hits"] / max(1, self.metrics["cache_hits"] + self.metrics["cache_misses"]) * 100, 1),
            "dn_lookups": {
                "total": self.metrics["dn_lookups"],
                "success": self.metrics["dn_lookups_success"],
                "failure": self.metrics["dn_lookups_failure"],
                "success_rate": round(self.metrics["dn_lookups_success"] / max(total_dn, 1) * 100, 1)
            },
            "dealer_queries": {
                "total": self.metrics["dealer_queries"],
                "success": self.metrics["dealer_queries_success"],
                "failure": self.metrics["dealer_queries_failure"],
                "success_rate": round(self.metrics["dealer_queries_success"] / max(total_dealer, 1) * 100, 1)
            },
            "city_queries": self.metrics["city_queries"],
            "warehouse_queries": self.metrics["warehouse_queries"],
            "comparisons": self.metrics["comparisons"],
            "executive_insights": self.metrics["executive_insights"],
            "root_cause_analyses": self.metrics["root_cause_analyses"],
            "groq_uses": self.metrics["groq_uses"],
            "overrides": self.metrics["overrides"],
            "rejections": self.metrics["rejections"],
            "timeouts": self.metrics["timeouts"],
            "errors": self.metrics["errors"],
            "analytics_response_errors": self.metrics["analytics_response_errors"],
            "conversation_count": len(self.conversation_cache),
            "cache_size": len(self.response_cache),
            "version": "13.0"
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
logger.info("AI Provider Service v13.0 - Full Alignment")
logger.info("=" * 70)
logger.info("")
logger.info("   FULL ALIGNMENT FIXES:")
logger.info("   ✅ AnalyticsResponse validation helper")
logger.info("   ✅ customer_name standardization")
logger.info("   ✅ All formatters support AnalyticsResponse")
logger.info("   ✅ DN normalization: re.sub(r'\\D', '', question)")
logger.info("   ✅ Production diagnostics at every step")
logger.info("   ✅ 100% PostgreSQL compliance")
logger.info("   ✅ KPI standardization")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
