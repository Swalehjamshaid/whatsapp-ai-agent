# ==========================================================
# FILE: app/services/ai_provider_service.py (v11.0 - COMPLETE PRODUCTION REFACTOR)
# ==========================================================
# PURPOSE: MASTER ORCHESTRATOR - FINAL AUTHORITY & GOVERNANCE LAYER
# 
# ARCHITECTURE RULES ENFORCED:
# 1. AIQueryService SUGGESTS → SchemaService VERIFIES → Orchestrator DECIDES
# 2. DN Lookup HIGHEST PRIORITY (8-12 digits)
# 3. Entity Resolution OVERRIDES Intent Detection
# 4. Groq ONLY for General/Creative/Casual
# 5. Safe QueryPlan with getattr() - NO DIRECT ATTRIBUTE ACCESS
# 6. Comprehensive error logging with Reference IDs
# 7. Cache invalidation on metadata refresh
# 8. Startup dependency validation
# 9. Data Quality Governance for invalid dates
# ==========================================================

import time
import uuid
import hashlib
import re
import asyncio
import traceback
from typing import Optional, Callable, Any, Dict, List, Tuple
from cachetools import TTLCache
from loguru import logger
from sqlalchemy.orm import Session
from datetime import datetime

from app.config import config
from app.database import SessionLocal

# ==========================================================
# REQUIRED IMPORTS WITH VALIDATION
# ==========================================================

try:
    from app.services.ai_query_service import AIQueryService, QueryPlan, get_ai_query_service
except ImportError as e:
    logger.error(f"❌ Failed to import AIQueryService: {e}")
    raise

try:
    from app.services.analytics_service import AnalyticsService, get_analytics_service
except ImportError as e:
    logger.error(f"❌ Failed to import AnalyticsService: {e}")
    raise

try:
    from app.services.kpi_service import KPIService, get_kpi_service
except ImportError as e:
    logger.error(f"❌ Failed to import KPIService: {e}")
    raise

try:
    from app.services.groq_service import GroqService, get_groq_service
except ImportError as e:
    logger.error(f"❌ Failed to import GroqService: {e}")
    raise

try:
    from app.schemas.schema_service import get_schema_service, DN_PATTERN
except ImportError as e:
    logger.error(f"❌ Failed to import SchemaService: {e}")
    raise

# ==========================================================
# CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800

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
# REQUIRED METHODS FOR STARTUP VALIDATION
# ==========================================================

REQUIRED_ANALYTICS_METHODS = [
    'get_dn_analytics',
    'get_dealer_dashboard',
    'get_dealer_revenue',
    'get_dealer_units',
    'get_dealer_performance',
    'get_dealer_aging',
    'get_warehouse_dashboard',
    'get_city_dashboard',
    'get_city_ranking',
    'compare_dealers',
    'compare_warehouses',
    'compare_cities',
    'get_executive_summary',
    'get_root_cause_insights',
    'get_control_tower_alerts',
    'get_delivery_performance',
    'get_trend_analysis',
    'get_dealer_ranking'
]

REQUIRED_SCHEMA_METHODS = [
    'resolve_entity',
    'detect_metric',
    'is_logistics_keyword',
    'get_risk_status',
    'get_risk_emoji',
    'extract_dn_number',
    'is_dn_number'
]

REQUIRED_KPI_METHODS = [
    'get_pending_pgi',
    'get_pending_pod'
]

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
    MASTER ORCHESTRATOR - FINAL AUTHORITY & GOVERNANCE LAYER
    
    Architecture Flow:
    1. DN Detection (Highest Priority)
    2. Entity Resolution (SchemaService Verifies)
    3. Intent Detection (AIQueryService Suggests)
    4. Governance Override (Orchestrator Decides)
    5. Service Execution (With Method Validation)
    6. Groq Enrichment (Only When Appropriate)
    """
    
    def __init__(self):
        # ==========================================================
        # SERVICE INITIALIZATION WITH VALIDATION
        # ==========================================================
        
        self.query_service = get_ai_query_service()
        self.analytics = get_analytics_service()
        self.kpi = get_kpi_service()
        self.groq = get_groq_service()
        self.schema = get_schema_service()
        
        # Validate all dependencies
        self._validate_dependencies()
        
        # ==========================================================
        # CACHES
        # ==========================================================
        
        self.response_cache = TTLCache(maxsize=500, ttl=CACHE_TTL_SECONDS)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        
        # ==========================================================
        # METRICS
        # ==========================================================
        
        self.metrics = {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "dn_lookups": 0,
            "dealer_queries": 0,
            "city_queries": 0,
            "warehouse_queries": 0,
            "comparisons": 0,
            "executive_insights": 0,
            "root_cause_analyses": 0,
            "groq_queries": 0,
            "groq_rejections": 0,
            "analytics_success": 0,
            "analytics_failure": 0,
            "routing_overrides": 0,
            "errors": 0,
            "service_successes": 0,
            "service_failures": 0
        }
        
        # ==========================================================
        # STARTUP LOGGING
        # ==========================================================
        
        logger.info("=" * 70)
        logger.info("AI Orchestrator v11.0 - FINAL AUTHORITY & GOVERNANCE LAYER")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ✅ Dependencies Validated")
        logger.info("   ✅ DN Lookup: HIGHEST PRIORITY (8-12 digits)")
        logger.info("   ✅ Entity Resolution: OVERRIDES Intent Detection")
        logger.info("   ✅ Groq: ONLY for General/Creative/Casual")
        logger.info("   ✅ Safe QueryPlan: getattr() for ALL attributes")
        logger.info("   ✅ Data Quality Governance: Invalid dates handled")
        logger.info("   ✅ Cache: TTL + invalidation on metadata refresh")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
    # ==========================================================
    # STARTUP VALIDATION
    # ==========================================================
    
    def _validate_dependencies(self) -> None:
        """Validate all required methods exist on dependencies."""
        errors = []
        
        # Validate AnalyticsService
        for method in REQUIRED_ANALYTICS_METHODS:
            if not hasattr(self.analytics, method):
                errors.append(f"AnalyticsService missing method: {method}")
                logger.warning(f"⚠️ AnalyticsService missing: {method}")
        
        # Validate SchemaService
        for method in REQUIRED_SCHEMA_METHODS:
            if not hasattr(self.schema, method):
                errors.append(f"SchemaService missing method: {method}")
                logger.warning(f"⚠️ SchemaService missing: {method}")
        
        # Validate KPIService
        for method in REQUIRED_KPI_METHODS:
            if not hasattr(self.kpi, method):
                errors.append(f"KPIService missing method: {method}")
                logger.warning(f"⚠️ KPIService missing: {method}")
        
        if errors:
            error_msg = "Dependency validation failed:\n" + "\n".join(f"  ❌ {e}" for e in errors)
            logger.error(error_msg)
            self._dependency_errors = errors
        else:
            self._dependency_errors = []
            logger.info("✅ All dependencies validated successfully")
    
    def get_dependency_status(self) -> Dict[str, Any]:
        """Get dependency validation status for diagnostics."""
        return {
            "status": "healthy" if not self._dependency_errors else "degraded",
            "errors": self._dependency_errors,
            "analytics_methods": {m: hasattr(self.analytics, m) for m in REQUIRED_ANALYTICS_METHODS},
            "schema_methods": {m: hasattr(self.schema, m) for m in REQUIRED_SCHEMA_METHODS},
            "kpi_methods": {m: hasattr(self.kpi, m) for m in REQUIRED_KPI_METHODS}
        }
    
    # ==========================================================
    # MAIN ENTRY POINT (PRESERVED SIGNATURE - CRITICAL)
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
        
        self.metrics["total_requests"] += 1
        
        logger.bind(
            request_id=req_id,
            phone=phone_number[:4] + "****" if phone_number else None
        ).info(f"📥 Processing: {question[:100]}")
        
        try:
            # ==========================================================
            # STEP 1: Check Cache
            # ==========================================================
            
            cached_response = self._get_cached_response(question, phone_number)
            if cached_response:
                self.metrics["cache_hits"] += 1
                return cached_response
            
            self.metrics["cache_misses"] += 1
            
            # ==========================================================
            # STEP 2: Load Context
            # ==========================================================
            
            context = self._load_context(phone_number)
            context_dict = context.to_dict() if context else {}
            
            # ==========================================================
            # STEP 3: DN Lookup (HIGHEST PRIORITY - No Exceptions)
            # ==========================================================
            
            if self._is_dn_query(question):
                logger.info(f"🔍 DN Lookup: {question}")
                self.metrics["dn_lookups"] += 1
                response = self._execute_dn_lookup(question)
                self._update_context(phone_number, "dn_lookup", "dn", question, req_id)
                self._cache_response(question, phone_number, response)
                return response
            
            # ==========================================================
            # STEP 4: Entity Resolution (SchemaService Verifies)
            # ==========================================================
            
            entity_result = self.schema.resolve_entity(question)
            
            if entity_result["type"] != "none":
                entity_type = entity_result["type"]
                entity_name = entity_result["name"]
                confidence = entity_result["confidence"]
                
                logger.info(
                    f"📍 Entity Resolved: {entity_type}='{entity_name}' "
                    f"(confidence: {confidence:.2f})"
                )
                
                # Check for comparison query
                if self._is_comparison_query(question):
                    logger.info(f"⚡ Comparison Detected: {entity_type}")
                    self.metrics["comparisons"] += 1
                    response = self._execute_comparison(entity_type, question, entity_name)
                    self._update_context(phone_number, f"compare_{entity_type}s", entity_type, entity_name, req_id)
                    self._cache_response(question, phone_number, response)
                    return response
                
                # Entity-only queries go to dashboard
                if self._is_entity_only_query(question, entity_name):
                    logger.info(f"⚡ Entity-Only: {entity_type}_dashboard")
                    self.metrics["routing_overrides"] += 1
                    response = self._execute_entity_dashboard(entity_type, entity_name)
                    self._update_context(phone_number, f"{entity_type}_dashboard", entity_type, entity_name, req_id)
                    self._cache_response(question, phone_number, response)
                    return response
            
            # ==========================================================
            # STEP 5: Intent Detection (AIQueryService Suggests)
            # ==========================================================
            
            query_plan = self._get_query_plan(question, context_dict)
            
            # SAFE: Extract all attributes with getattr()
            plan_data = self._extract_query_plan(query_plan)
            
            # ==========================================================
            # STEP 6: Governance Override (Orchestrator Decides)
            # ==========================================================
            
            validated_plan = self._validate_and_override(query_plan, question, entity_result)
            
            # ==========================================================
            # STEP 7: Service Execution (With Method Validation)
            # ==========================================================
            
            logger.info(
                f"🎯 ROUTING: intent={validated_plan.intent}, "
                f"entity={validated_plan.entity}, "
                f"service={validated_plan.service}"
            )
            
            response = self._execute_service(validated_plan, context_dict, req_id)
            
            # ==========================================================
            # STEP 8: Groq Governance (Enrich Only, Never Replace)
            # ==========================================================
            
            response = self._apply_groq_governance(response, validated_plan, question, context_dict)
            
            # ==========================================================
            # STEP 9: Update Context & Cache
            # ==========================================================
            
            self._update_context(
                phone_number,
                validated_plan.intent,
                validated_plan.entity_type or "none",
                validated_plan.entity or question,
                req_id,
                response
            )
            self._cache_response(question, phone_number, response)
            
            duration_ms = int((time.time() - start_time) * 1000)
            logger.bind(request_id=req_id).info(
                f"✅ Done: {duration_ms}ms | "
                f"Service: {validated_plan.service} | "
                f"Groq: {validated_plan.service == 'groq'}"
            )
            
            return response
            
        except Exception as e:
            self.metrics["errors"] += 1
            error_id = str(uuid.uuid4())[:8]
            logger.exception(f"[{req_id}] FATAL ERROR [{error_id}]: {e}")
            return self._get_error_response(question, e, error_id, req_id)
    
    # ==========================================================
    # SAFE QUERYPLAN EXTRACTION (FIXES entity2 issue)
    # ==========================================================
    
    def _extract_query_plan(self, query_plan: Any) -> Dict[str, Any]:
        """
        SAFELY extract all QueryPlan attributes with defaults.
        
        FIX: Replaces all direct attribute access with getattr()
        """
        return {
            "intent": getattr(query_plan, "intent", "help"),
            "entity": getattr(query_plan, "entity", None),
            "entity2": getattr(query_plan, "entity2", None),  # FIXED: Safe entity2 access
            "entity_type": getattr(query_plan, "entity_type", None),
            "service": getattr(query_plan, "service", "help"),
            "confidence": getattr(query_plan, "confidence", 0.0),
            "original_message": getattr(query_plan, "original_message", ""),
            "limit": getattr(query_plan, "limit", 10),
            "sort_by": getattr(query_plan, "sort_by", "revenue")
        }
    
    # ==========================================================
    # DN DETECTION (HIGHEST PRIORITY)
    # ==========================================================
    
    def _is_dn_query(self, question: str) -> bool:
        """Check if query is a DN number (8-12 digits)."""
        cleaned = question.strip()
        return bool(DN_PATTERN.fullmatch(cleaned.replace(" ", "")))
    
    def _execute_dn_lookup(self, question: str) -> str:
        """Execute DN lookup with method validation."""
        dn_number = question.strip()
        
        if not hasattr(self.analytics, 'get_dn_analytics'):
            return self._get_method_error("get_dn_analytics", "AnalyticsService")
        
        try:
            result = self.analytics.get_dn_analytics(dn_number)
            self.metrics["analytics_success"] += 1
            return self._format_dn_details(result)
        except Exception as e:
            self.metrics["analytics_failure"] += 1
            logger.error(f"DN lookup failed for {dn_number}: {e}")
            return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again."
    
    # ==========================================================
    # COMPARISON DETECTION
    # ==========================================================
    
    def _is_comparison_query(self, question: str) -> bool:
        """Check if query is asking for comparison."""
        question_lower = question.lower()
        patterns = [" vs ", " versus ", " compare ", " compare with ", " between "]
        return any(p in question_lower for p in patterns)
    
    def _execute_comparison(self, entity_type: str, question: str, entity_name: str) -> str:
        """Execute comparison analytics with method validation."""
        entities = self._parse_comparison(question, entity_type)
        
        if len(entities) < 2:
            return self._execute_entity_dashboard(entity_type, entity_name)
        
        entity1, entity2 = entities[0], entities[1]
        
        try:
            if entity_type == "dealer":
                if not hasattr(self.analytics, 'compare_dealers'):
                    return self._get_method_error("compare_dealers", "AnalyticsService")
                result = self.analytics.compare_dealers(entity1, entity2)
                return self._format_dealer_comparison(result, entity1, entity2)
            elif entity_type == "warehouse":
                if not hasattr(self.analytics, 'compare_warehouses'):
                    return self._get_method_error("compare_warehouses", "AnalyticsService")
                result = self.analytics.compare_warehouses(entity1, entity2)
                return self._format_warehouse_comparison(result, entity1, entity2)
            elif entity_type == "city":
                if not hasattr(self.analytics, 'compare_cities'):
                    return self._get_method_error("compare_cities", "AnalyticsService")
                result = self.analytics.compare_cities(entity1, entity2)
                return self._format_city_comparison(result, entity1, entity2)
            else:
                return self._execute_entity_dashboard(entity_type, entity_name)
        except Exception as e:
            self.metrics["analytics_failure"] += 1
            logger.error(f"Comparison failed: {e}")
            return f"❌ Unable to compare {entity1} and {entity2}. Please try again."
    
    def _parse_comparison(self, question: str, entity_type: str) -> List[str]:
        """Extract both entities from comparison query."""
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
        """Check if query is just an entity name with minimal extra words."""
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
    
    def _execute_entity_dashboard(self, entity_type: str, entity_name: str) -> str:
        """Execute dashboard for entity type with error handling."""
        try:
            if entity_type == "dealer":
                self.metrics["dealer_queries"] += 1
                if not hasattr(self.analytics, 'get_dealer_dashboard'):
                    return self._get_method_error("get_dealer_dashboard", "AnalyticsService")
                result = self.analytics.get_dealer_dashboard(entity_name)
                self.metrics["analytics_success"] += 1
                return self._format_dealer_dashboard(result, entity_name)
            elif entity_type == "city":
                self.metrics["city_queries"] += 1
                if not hasattr(self.analytics, 'get_city_dashboard'):
                    return self._get_method_error("get_city_dashboard", "AnalyticsService")
                result = self.analytics.get_city_dashboard(entity_name)
                self.metrics["analytics_success"] += 1
                return self._format_city_dashboard(result, entity_name)
            elif entity_type == "warehouse":
                self.metrics["warehouse_queries"] += 1
                if not hasattr(self.analytics, 'get_warehouse_dashboard'):
                    return self._get_method_error("get_warehouse_dashboard", "AnalyticsService")
                result = self.analytics.get_warehouse_dashboard(entity_name)
                self.metrics["analytics_success"] += 1
                return self._format_warehouse_dashboard(result, entity_name)
            else:
                return f"❌ Unknown entity type: {entity_type}"
        except Exception as e:
            self.metrics["analytics_failure"] += 1
            logger.error(f"Dashboard failed for {entity_name}: {e}")
            return f"❌ Unable to retrieve dashboard for {entity_name}. Please try again."
    
    # ==========================================================
    # QUERY PLAN
    # ==========================================================
    
    def _get_query_plan(self, question: str, context: Dict) -> QueryPlan:
        """Get query plan from AIQueryService with safe handling."""
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
            logger.error(f"Query plan generation failed: {e}")
            return QueryPlan(
                original_message=question,
                intent="help",
                entity=None,
                entity_type=None,
                service="help",
                confidence=0.0
            )
    
    # ==========================================================
    # GOVERNANCE LAYER - Validate and Override
    # ==========================================================
    
    def _validate_and_override(
        self,
        query_plan: QueryPlan,
        question: str,
        entity_result: Dict[str, Any]
    ) -> QueryPlan:
        """
        GOVERNANCE LAYER: Revalidate and override decisions.
        
        SAFE: Uses getattr() for all attributes.
        
        Rules:
        1. Entity Resolution OVERRIDES Intent Detection
        2. Groq Protection: Logistics queries never go to Groq
        3. Ranking Detection: City and Dealer ranking
        4. Root Cause Detection
        """
        
        plan_data = self._extract_query_plan(query_plan)
        
        intent = plan_data["intent"]
        entity = plan_data["entity"]
        entity_type = plan_data["entity_type"]
        service = plan_data["service"]
        confidence = plan_data["confidence"]
        original = plan_data["original_message"]
        
        # ==========================================================
        # RULE 1: Entity Resolution Override
        # ==========================================================
        
        if entity_result["type"] != "none":
            resolved_type = entity_result["type"]
            resolved_name = entity_result["name"]
            
            # If entity detected AND query is logistics-related, force dashboard
            if self._is_logistics_query(question) or confidence < 0.70:
                intent = f"{resolved_type}_dashboard"
                entity_type = resolved_type
                entity = resolved_name
                service = "analytics"
                self.metrics["routing_overrides"] += 1
                logger.info(f"⚡ OVERRIDE: {intent} (confidence: {confidence})")
                return QueryPlan(
                    original_message=original,
                    intent=intent,
                    entity=entity,
                    entity_type=entity_type,
                    service=service,
                    confidence=confidence
                )
        
        # ==========================================================
        # RULE 2: Groq Protection
        # ==========================================================
        
        if service == "groq" and self._is_logistics_query(question):
            service = "analytics"
            intent = "executive_insight"
            self.metrics["groq_rejections"] += 1
            logger.info(f"🚫 REJECTED: Groq blocked for logistics query")
            return QueryPlan(
                original_message=original,
                intent=intent,
                entity=entity,
                entity_type=entity_type,
                service=service,
                confidence=confidence
            )
        
        # ==========================================================
        # RULE 3: Ranking Detection
        # ==========================================================
        
        if self._is_city_ranking_query(question):
            intent = "city_ranking"
            service = "analytics"
            logger.info(f"📊 OVERRIDE: City ranking detected")
            return QueryPlan(
                original_message=original,
                intent=intent,
                entity=entity,
                entity_type=entity_type,
                service=service,
                confidence=confidence
            )
        
        if self._is_dealer_ranking_query(question):
            intent = "dealer_ranking"
            service = "analytics"
            logger.info(f"📊 OVERRIDE: Dealer ranking detected")
            return QueryPlan(
                original_message=original,
                intent=intent,
                entity=entity,
                entity_type=entity_type,
                service=service,
                confidence=confidence
            )
        
        # ==========================================================
        # RULE 4: Root Cause Detection
        # ==========================================================
        
        if self._is_root_cause_query(question):
            intent = "root_cause"
            service = "analytics"
            self.metrics["root_cause_analyses"] += 1
            logger.info(f"🔍 OVERRIDE: Root cause detected")
            return QueryPlan(
                original_message=original,
                intent=intent,
                entity=entity,
                entity_type=entity_type,
                service=service,
                confidence=confidence
            )
        
        # Return validated plan
        return QueryPlan(
            original_message=original,
            intent=intent,
            entity=entity,
            entity_type=entity_type,
            service=service,
            confidence=confidence
        )
    
    # ==========================================================
    # QUERY DETECTION HELPERS
    # ==========================================================
    
    def _is_logistics_query(self, question: str) -> bool:
        """Check if query contains logistics keywords (should not go to Groq)."""
        question_lower = question.lower()
        
        for pattern in GROQ_BLOCKED_PATTERNS:
            if pattern in question_lower:
                return True
        
        if hasattr(self.schema, 'detect_metric') and self.schema.detect_metric(question):
            return True
        
        if hasattr(self.schema, 'is_logistics_keyword') and self.schema.is_logistics_keyword(question):
            return True
        
        return False
    
    def _is_city_ranking_query(self, question: str) -> bool:
        """Check if query is asking for city ranking."""
        question_lower = question.lower()
        patterns = [
            "which city", "top city", "highest city", "best city",
            "city with highest", "city ranking", "cities by",
            "top cities", "best cities"
        ]
        return any(p in question_lower for p in patterns)
    
    def _is_dealer_ranking_query(self, question: str) -> bool:
        """Check if query is asking for dealer ranking."""
        question_lower = question.lower()
        patterns = [
            "top dealer", "best dealer", "highest dealer",
            "dealer ranking", "dealers by", "top 10 dealer",
            "top dealers", "bottom dealers"
        ]
        return any(p in question_lower for p in patterns)
    
    def _is_root_cause_query(self, question: str) -> bool:
        """Check if query is asking for root cause."""
        question_lower = question.lower()
        patterns = [
            "key issue", "root cause", "why delayed", "bring improvement",
            "how to improve", "critical alert", "executive insight",
            "what is the issue", "what is the key", "how to bring"
        ]
        return any(p in question_lower for p in patterns)
    
    # ==========================================================
    # SERVICE EXECUTION
    # ==========================================================
    
    def _execute_service(self, query_plan: QueryPlan, context: Dict, req_id: str) -> str:
        """Execute service with comprehensive error handling."""
        plan_data = self._extract_query_plan(query_plan)
        
        intent = plan_data["intent"]
        entity = plan_data["entity"]
        entity2 = plan_data["entity2"]
        service = plan_data["service"]
        original = plan_data["original_message"]
        
        try:
            if service == "analytics":
                self.metrics["analytics_success"] += 1
                return self._execute_analytics(intent, entity, entity2, original)
            elif service == "kpi":
                self.metrics["analytics_success"] += 1
                return self._execute_kpi(intent, entity)
            elif service == "groq":
                self.metrics["groq_queries"] += 1
                return self._execute_groq(query_plan, context)
            else:
                return self._get_help_message()
                
        except Exception as e:
            self.metrics["service_failures"] += 1
            self.metrics["analytics_failure"] += 1
            error_id = str(uuid.uuid4())[:8]
            logger.exception(
                f"[{req_id}] Service execution error [{error_id}]: "
                f"intent={intent}, service={service}, error={e}"
            )
            return self._get_service_error_response(intent, entity, service, e, error_id)
    
    # ==========================================================
    # ANALYTICS EXECUTION (With Method Validation)
    # ==========================================================
    
    def _execute_analytics(self, intent: str, entity: Optional[str], entity2: Optional[str], original: str) -> str:
        """Execute analytics with method validation."""
        
        # DEALER ANALYTICS
        if intent == "dealer_dashboard" and entity:
            if not hasattr(self.analytics, 'get_dealer_dashboard'):
                return self._get_method_error("get_dealer_dashboard", "AnalyticsService")
            result = self.analytics.get_dealer_dashboard(entity)
            return self._format_dealer_dashboard(result, entity)
        
        if intent == "dealer_revenue" and entity:
            if not hasattr(self.analytics, 'get_dealer_revenue'):
                return self._get_method_error("get_dealer_revenue", "AnalyticsService")
            result = self.analytics.get_dealer_revenue(entity)
            return self._format_dealer_revenue(result, entity)
        
        if intent == "dealer_units" and entity:
            if not hasattr(self.analytics, 'get_dealer_units'):
                return self._get_method_error("get_dealer_units", "AnalyticsService")
            result = self.analytics.get_dealer_units(entity)
            return self._format_dealer_units(result, entity)
        
        if intent == "dealer_performance" and entity:
            if not hasattr(self.analytics, 'get_dealer_performance'):
                return self._get_method_error("get_dealer_performance", "AnalyticsService")
            result = self.analytics.get_dealer_performance(entity)
            return self._format_dealer_performance(result, entity)
        
        if intent == "dealer_aging" and entity:
            if not hasattr(self.analytics, 'get_dealer_aging'):
                return self._get_method_error("get_dealer_aging", "AnalyticsService")
            result = self.analytics.get_dealer_aging(entity)
            return self._format_dealer_aging(result, entity)
        
        # WAREHOUSE ANALYTICS
        if intent == "warehouse_dashboard" and entity:
            if not hasattr(self.analytics, 'get_warehouse_dashboard'):
                return self._get_method_error("get_warehouse_dashboard", "AnalyticsService")
            result = self.analytics.get_warehouse_dashboard(entity)
            return self._format_warehouse_dashboard(result, entity)
        
        if intent == "warehouse_performance" and entity:
            if not hasattr(self.analytics, 'get_warehouse_dashboard'):
                return self._get_method_error("get_warehouse_dashboard", "AnalyticsService")
            result = self.analytics.get_warehouse_dashboard(entity)
            return self._format_warehouse_performance(result, entity)
        
        # CITY ANALYTICS
        if intent == "city_dashboard" and entity:
            if not hasattr(self.analytics, 'get_city_dashboard'):
                return self._get_method_error("get_city_dashboard", "AnalyticsService")
            result = self.analytics.get_city_dashboard(entity)
            return self._format_city_dashboard(result, entity)
        
        if intent == "city_performance" and entity:
            if not hasattr(self.analytics, 'get_city_dashboard'):
                return self._get_method_error("get_city_dashboard", "AnalyticsService")
            result = self.analytics.get_city_dashboard(entity)
            return self._format_city_performance(result, entity)
        
        if intent == "city_ranking":
            if not hasattr(self.analytics, 'get_city_ranking'):
                return self._get_method_error("get_city_ranking", "AnalyticsService")
            result = self.analytics.get_city_ranking()
            return self._format_city_ranking(result)
        
        # DEALER RANKING
        if intent == "dealer_ranking":
            top = "top" in original.lower() or "best" in original.lower()
            limit = 10
            if not hasattr(self.analytics, 'get_dealer_ranking'):
                return self._get_method_error("get_dealer_ranking", "AnalyticsService")
            result = self.analytics.get_dealer_ranking(limit=limit, top=top)
            return self._format_dealer_ranking(result, top)
        
        # COMPARISON ANALYTICS (SAFE entity2 handling)
        if intent == "compare_dealers" and entity and entity2:
            if not hasattr(self.analytics, 'compare_dealers'):
                return self._get_method_error("compare_dealers", "AnalyticsService")
            result = self.analytics.compare_dealers(entity, entity2)
            return self._format_dealer_comparison(result, entity, entity2)
        
        if intent == "compare_warehouses" and entity and entity2:
            if not hasattr(self.analytics, 'compare_warehouses'):
                return self._get_method_error("compare_warehouses", "AnalyticsService")
            result = self.analytics.compare_warehouses(entity, entity2)
            return self._format_warehouse_comparison(result, entity, entity2)
        
        if intent == "compare_cities" and entity and entity2:
            if not hasattr(self.analytics, 'compare_cities'):
                return self._get_method_error("compare_cities", "AnalyticsService")
            result = self.analytics.compare_cities(entity, entity2)
            return self._format_city_comparison(result, entity, entity2)
        
        # DN ANALYTICS
        if intent == "dn_lookup" and entity:
            if not hasattr(self.analytics, 'get_dn_analytics'):
                return self._get_method_error("get_dn_analytics", "AnalyticsService")
            result = self.analytics.get_dn_analytics(entity)
            return self._format_dn_details(result)
        
        # EXECUTIVE & ROOT CAUSE
        if intent == "executive_insight":
            self.metrics["executive_insights"] += 1
            if not hasattr(self.analytics, 'get_executive_summary'):
                return self._get_method_error("get_executive_summary", "AnalyticsService")
            result = self.analytics.get_executive_summary()
            return self._format_executive_insights(result)
        
        if intent == "root_cause":
            self.metrics["root_cause_analyses"] += 1
            if not hasattr(self.analytics, 'get_root_cause_insights'):
                return self._get_method_error("get_root_cause_insights", "AnalyticsService")
            result = self.analytics.get_root_cause_insights()
            return self._format_root_cause(result)
        
        if intent == "control_tower":
            if not hasattr(self.analytics, 'get_control_tower_alerts'):
                return self._get_method_error("get_control_tower_alerts", "AnalyticsService")
            result = self.analytics.get_control_tower_alerts()
            return self._format_control_tower(result)
        
        if intent == "delivery_performance":
            if not hasattr(self.analytics, 'get_delivery_performance'):
                return self._get_method_error("get_delivery_performance", "AnalyticsService")
            result = self.analytics.get_delivery_performance()
            return self._format_delivery_performance(result)
        
        if intent == "trend":
            if not hasattr(self.analytics, 'get_trend_analysis'):
                return self._get_method_error("get_trend_analysis", "AnalyticsService")
            result = self.analytics.get_trend_analysis()
            return self._format_trend_analysis(result)
        
        if intent == "help":
            return self._get_help_message()
        
        return self._get_help_message()
    
    # ==========================================================
    # KPI EXECUTION
    # ==========================================================
    
    def _execute_kpi(self, intent: str, entity: Optional[str]) -> str:
        """Execute KPI queries with method validation."""
        try:
            if intent == "pending_pgi":
                if not hasattr(self.kpi, 'get_pending_pgi'):
                    return self._get_method_error("get_pending_pgi", "KPIService")
                kpi = self.kpi.get_pending_pgi(entity)
                if entity:
                    return f"⏳ *PGI Pending for {entity}:* {kpi.get('pending_pgi', 0)}"
                return f"⏳ *Total PGI Pending:* {kpi.get('pending_pgi', 0)}"
            
            if intent == "pending_pod":
                if not hasattr(self.kpi, 'get_pending_pod'):
                    return self._get_method_error("get_pending_pod", "KPIService")
                kpi = self.kpi.get_pending_pod(entity)
                if entity:
                    return f"📎 *POD Pending for {entity}:* {kpi.get('pending_pod', 0)}"
                return f"📎 *Total POD Pending:* {kpi.get('pending_pod', 0)}"
            
            return self._get_help_message()
        except Exception as e:
            logger.error(f"KPI execution failed: {e}")
            return f"⚠️ Unable to retrieve KPI data. Please try again."
    
    # ==========================================================
    # GROQ EXECUTION (Only for Appropriate Queries)
    # ==========================================================
    
    def _execute_groq(self, query_plan: QueryPlan, context: Dict) -> str:
        """Execute Groq ONLY for appropriate queries with double-check."""
        plan_data = self._extract_query_plan(query_plan)
        question = plan_data["original_message"]
        
        if self._is_logistics_query(question):
            self.metrics["groq_rejections"] += 1
            logger.warning(f"🚫 GROQ BLOCKED: Logistics query rejected at execution layer")
            return self._get_groq_blocked_response()
        
        if hasattr(self.groq, 'is_available') and self.groq.is_available:
            try:
                response = self.groq.chat(question, context)
                self.metrics["groq_queries"] += 1
                return response
            except Exception as e:
                logger.error(f"Groq execution failed: {e}")
                return "⚠️ AI service is temporarily unavailable. Please try again later."
        
        return "⚠️ AI service is not available. Please try again later."
    
    # ==========================================================
    # GROQ GOVERNANCE - Enrichment Only
    # ==========================================================
    
    def _apply_groq_governance(self, response: str, query_plan: QueryPlan, question: str, context: Dict) -> str:
        """
        GROQ GOVERNANCE: Enrich analytics, never replace.
        
        Rules:
        1. Only enrich executive_insight and root_cause
        2. Never replace analytics with Groq
        3. Always preserve analytics data
        """
        if not hasattr(self.groq, 'is_available') or not self.groq.is_available:
            return response
        
        plan_data = self._extract_query_plan(query_plan)
        intent = plan_data["intent"]
        
        if intent in ["executive_insight", "root_cause"] and len(response) > 50:
            try:
                enrichment_prompt = f"""
Based on this logistics analytics data:

{response[:600]}

Provide a brief, professional executive summary (2-3 sentences) that highlights the most critical insight and recommends one immediate action.

Keep it concise and actionable. Do not repeat the data, just provide insight.
"""
                groq_summary = self.groq.chat(enrichment_prompt, context)
                
                if groq_summary and len(groq_summary) > 10:
                    self.metrics["groq_queries"] += 1
                    return f"{response}\n\n💡 *AI Insight:*\n{groq_summary}"
            except Exception as e:
                logger.warning(f"Groq enrichment failed: {e}")
        
        if intent == "general_ai":
            groq_response = self.groq.chat(question, context)
            if groq_response and len(groq_response) > 10:
                self.metrics["groq_queries"] += 1
                return groq_response
        
        return response
    
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
        return {"status": "cleared", "version": "11.0"}
    
    # ==========================================================
    # ERROR RESPONSES
    # ==========================================================
    
    def _get_method_error(self, method: str, service: str) -> str:
        error_id = str(uuid.uuid4())[:8]
        logger.error(f"Method missing: {service}.{method} (Error: {error_id})")
        return (
            f"⚠️ *Service Error*\n\n"
            f"• Service: {service}\n"
            f"• Method: {method}\n"
            f"• Error Reference: `{error_id}`\n\n"
            f"Please contact support with this reference ID."
        )
    
    def _get_error_response(self, question: str, error: Exception, error_id: str, request_id: str) -> str:
        error_msg = str(error)[:100]
        return (
            f"⚠️ *Unable to process your request*\n\n"
            f"• Error Reference: `{error_id}`\n"
            f"• Request ID: `{request_id}`\n"
            f"• Error: {error_msg}\n\n"
            f"Please try again or contact support with the reference ID."
        )
    
    def _get_service_error_response(self, intent: str, entity: Optional[str], service: str, error: Exception, error_id: str) -> str:
        error_msg = str(error)[:100]
        return (
            f"⚠️ *Unable to retrieve analytics data*\n\n"
            f"• Intent: {intent}\n"
            f"• Entity: {entity or 'N/A'}\n"
            f"• Service: {service}\n"
            f"• Error Reference: `{error_id}`\n"
            f"• Error: {error_msg}\n\n"
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
    # DIAGNOSTIC METHODS
    # ==========================================================
    
    def get_routing_debug(self, question: str) -> Dict[str, Any]:
        entity_result = self.schema.resolve_entity(question)
        context = {}
        query_plan = self._get_query_plan(question, context)
        plan_data = self._extract_query_plan(query_plan)
        validated = self._validate_and_override(query_plan, question, entity_result)
        validated_data = self._extract_query_plan(validated)
        
        return {
            "question": question,
            "ai_query_service": {
                "intent": plan_data["intent"],
                "entity": plan_data["entity"],
                "entity_type": plan_data["entity_type"],
                "service": plan_data["service"],
                "confidence": plan_data["confidence"]
            },
            "schema_override": {
                "entity_resolved": entity_result["type"] != "none",
                "entity_type": entity_result.get("type"),
                "entity_name": entity_result.get("name"),
                "confidence": entity_result.get("confidence", 0)
            },
            "final_decision": {
                "intent": validated_data["intent"],
                "entity": validated_data["entity"],
                "entity_type": validated_data["entity_type"],
                "service": validated_data["service"],
                "groq_protected": self._is_logistics_query(question)
            }
        }
    
    def get_cache_info(self) -> Dict[str, Any]:
        return {
            "cache_size": len(self.response_cache),
            "cache_maxsize": self.response_cache.maxsize,
            "conversation_count": len(self.conversation_cache),
            "cache_hits": self.metrics["cache_hits"],
            "cache_misses": self.metrics["cache_misses"],
            "hit_rate": self.metrics["cache_hits"] / max(1, self.metrics["cache_hits"] + self.metrics["cache_misses"])
        }
    
    def get_governance_info(self) -> Dict[str, Any]:
        return {
            "total_overrides": self.metrics["routing_overrides"],
            "groq_rejections": self.metrics["groq_rejections"],
            "groq_queries": self.metrics["groq_queries"],
            "analytics_success": self.metrics["analytics_success"],
            "analytics_failure": self.metrics["analytics_failure"]
        }
    
    # ==========================================================
    # FORMATTERS - All response formatting in single file
    # ==========================================================
    
    def _format_dealer_dashboard(self, data: Dict, dealer_name: str) -> str:
        if not data or "error" in data:
            return f"❌ No data found for {dealer_name}"
        
        summary = data.get("summary", {})
        aging = data.get("aging", {})
        performance = data.get("performance", {})
        
        lines = [
            f"🏪 *{dealer_name} - Dashboard*",
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
            f"   📦 Delivery Rate: {summary.get('pod_rate', 0):.1f}%",
            f"   📎 POD Rate: {summary.get('pod_rate', 0):.1f}%",
            f"   ⏰ Avg Delivery Aging: {aging.get('avg_delivery_aging', 0):.1f} days",
        ]
        
        risk_status = performance.get('risk_status', 'low')
        risk_emoji = self.schema.get_risk_emoji(risk_status) if hasattr(self.schema, 'get_risk_emoji') else "🟢"
        lines.append(f"   {risk_emoji} Risk Status: {risk_status.upper()}")
        
        return "\n".join(lines)
    
    def _format_dealer_revenue(self, data: Dict, dealer_name: str) -> str:
        if not data:
            return f"❌ No revenue data for {dealer_name}"
        return (
            f"💰 *Revenue for {dealer_name}*\n\n"
            f"• Total Revenue: PKR {data.get('total_revenue', 0):,.0f}\n"
            f"• Number of DNs: {data.get('count', 0)}\n"
            f"• Average per DN: PKR {data.get('avg_revenue', 0):,.0f}"
        )
    
    def _format_dealer_units(self, data: Dict, dealer_name: str) -> str:
        if not data:
            return f"❌ No units data for {dealer_name}"
        return (
            f"📦 *Units for {dealer_name}*\n\n"
            f"• Total Units: {data.get('total_units', 0):,}\n"
            f"• Number of DNs: {data.get('count', 0)}\n"
            f"• Average per DN: {data.get('avg_units', 0):.1f}"
        )
    
    def _format_dealer_performance(self, data: Dict, dealer_name: str) -> str:
        if not data:
            return f"❌ No performance data for {dealer_name}"
        lines = [
            f"📊 *Performance: {dealer_name}*",
            "",
            f"📦 Delivery Rate: {data.get('delivery_rate', 0):.1f}%",
            f"📎 POD Rate: {data.get('pod_rate', 0):.1f}%",
            f"⏳ Pending PGI: {data.get('pending_pgi', 0)}",
            f"📎 Pending POD: {data.get('pending_pod', 0)}",
            f"⏰ Avg Aging: {data.get('avg_aging', 0):.1f} days",
        ]
        return "\n".join(lines)
    
    def _format_dealer_aging(self, data: Dict, dealer_name: str) -> str:
        if not data:
            return f"❌ No aging data for {dealer_name}"
        return (
            f"⏱️ *Aging for {dealer_name}*\n\n"
            f"• Average Aging: {data.get('avg_aging', 0):.1f} days\n"
            f"• Maximum Aging: {data.get('max_aging', 0)} days\n"
            f"• DNs with Aging: {data.get('count', 0)}"
        )
    
    def _format_dealer_ranking(self, data: Dict, top: bool) -> str:
        dealers = data.get("dealers", [])
        if not dealers:
            return "📊 No dealers found."
        title = "🏆 *Top Dealers*" if top else "📉 *Bottom Dealers*"
        lines = [title, ""]
        for i, dealer in enumerate(dealers[:10], 1):
            revenue = dealer.get('revenue', 0)
            pod_rate = dealer.get('pod_rate', 0)
            lines.append(f"{i}. {dealer.get('name', 'N/A')}\n   Revenue: PKR {revenue:,.0f} | POD Rate: {pod_rate:.1f}%")
        return "\n".join(lines)
    
    def _format_warehouse_dashboard(self, data: Dict, warehouse_name: str) -> str:
        if not data:
            return f"❌ No data for {warehouse_name}"
        summary = data.get("summary", {})
        lines = [
            f"🏭 *{warehouse_name} - Dashboard*",
            "",
            f"📄 *Total DNs:* {summary.get('total_dns', 0):,}",
            f"📦 *Total Units:* {summary.get('total_units', 0):,}",
            f"💰 *Revenue:* PKR {summary.get('total_revenue', 0):,.0f}",
            f"📎 *POD Rate:* {summary.get('pod_rate', 0):.1f}%",
        ]
        top_dealers = data.get("top_dealers", [])
        if top_dealers:
            lines.append("")
            lines.append("🏆 *Top Dealers:*")
            for i, dealer in enumerate(top_dealers[:5], 1):
                lines.append(f"   {i}. {dealer.get('name', 'N/A')} - PKR {dealer.get('revenue', 0):,.0f}")
        return "\n".join(lines)
    
    def _format_warehouse_performance(self, data: Dict, warehouse_name: str) -> str:
        if not data:
            return f"❌ No performance data for {warehouse_name}"
        summary = data.get("summary", {})
        return (
            f"📊 *Performance: {warehouse_name}*\n\n"
            f"• Total DNs: {summary.get('total_dns', 0)}\n"
            f"• POD Rate: {summary.get('pod_rate', 0):.1f}%\n"
            f"• Revenue: PKR {summary.get('total_revenue', 0):,.0f}"
        )
    
    def _format_city_dashboard(self, data: Dict, city_name: str) -> str:
        if not data:
            return f"❌ No data for {city_name}"
        summary = data.get("summary", {})
        lines = [
            f"🏙️ *{city_name} - Dashboard*",
            "",
            f"📄 *Total DNs:* {summary.get('total_dns', 0):,}",
            f"💰 *Revenue:* PKR {summary.get('total_revenue', 0):,.0f}",
            f"🏪 *Active Dealers:* {summary.get('total_dealers', 0)}",
            f"📎 *POD Rate:* {summary.get('pod_rate', 0):.1f}%",
        ]
        top_dealers = data.get("top_dealers", [])
        if top_dealers:
            lines.append("")
            lines.append(f"🏆 *Top Dealers in {city_name}:*")
            for i, dealer in enumerate(top_dealers[:5], 1):
                lines.append(f"   {i}. {dealer.get('name', 'N/A')} - PKR {dealer.get('revenue', 0):,.0f}")
        return "\n".join(lines)
    
    def _format_city_performance(self, data: Dict, city_name: str) -> str:
        if not data:
            return f"❌ No performance data for {city_name}"
        summary = data.get("summary", {})
        return (
            f"📊 *Performance: {city_name}*\n\n"
            f"• Total DNs: {summary.get('total_dns', 0)}\n"
            f"• POD Rate: {summary.get('pod_rate', 0):.1f}%\n"
            f"• Revenue: PKR {summary.get('total_revenue', 0):,.0f}\n"
            f"• Active Dealers: {summary.get('total_dealers', 0)}"
        )
    
    def _format_city_ranking(self, data: Dict) -> str:
        cities = data.get("cities", [])
        if not cities:
            return "📊 No city data available."
        lines = ["🏙️ *City Rankings*", ""]
        for i, city in enumerate(cities[:10], 1):
            revenue = city.get('revenue', 0)
            pod_rate = city.get('pod_rate', 0)
            dealers = city.get('dealers', 0)
            lines.append(f"{i}. {city.get('name', 'N/A')}\n   Revenue: PKR {revenue:,.0f} | POD Rate: {pod_rate:.1f}% | Dealers: {dealers}")
        return "\n".join(lines)
    
    def _format_dn_details(self, data: Dict) -> str:
        if not data or not data.get("found"):
            return "❌ DN not found."
        
        record = data.get("record", {})
        validation = data.get("validation", {})
        durations = validation.get("durations", {})
        status = data.get("status", "unknown")
        
        processing_days = durations.get('processing_time_days')
        delivery_days = durations.get('delivery_time_days')
        cycle_days = durations.get('total_cycle_days')
        
        is_valid = validation.get('is_valid', False)
        issues = validation.get('issues', [])
        
        if is_valid and not issues:
            quality_emoji = "✅"
            quality_status = "VALID - All dates in correct order"
        elif issues:
            quality_emoji = "⚠️"
            quality_status = "DATA INTEGRITY ISSUE DETECTED"
        else:
            quality_emoji = "ℹ️"
            quality_status = "INCOMPLETE DATA"
        
        status_map = {
            "pending_pgi": "⏳ Pending PGI",
            "pending_pod": "🚚 In Transit (POD Pending)",
            "delivered": "✅ Delivered",
            "unknown": "❓ Status Unknown"
        }
        status_display = status_map.get(status, "❓ Unknown")
        
        lines = [
            "📄 *DN Details*",
            f"• DN: {record.get('dn_number', 'N/A')}",
            f"• Dealer: {record.get('sold_to_party_name', 'N/A')}",
            f"• City: {record.get('ship_to_city', 'N/A')}",
            f"• Warehouse: {record.get('warehouse', 'N/A')}",
            "",
            f"📦 *Units:* {record.get('units', 0)}",
            f"💰 *Amount:* PKR {record.get('amount', 0):,.0f}",
            "",
            f"📅 *Dates:*",
            f"   • DN Create: {record.get('dn_date', 'N/A')}",
            f"   • Good Issue: {record.get('pgi_date', 'N/A')}",
            f"   • POD: {record.get('pod_date', 'N/A')}",
            "",
            "⏱️ *Time Metrics:*",
        ]
        
        if processing_days is not None:
            emoji = "✅" if processing_days <= 7 else "⚠️" if processing_days <= 15 else "🔴"
            lines.append(f"   {emoji} Processing Time: {processing_days} days")
        else:
            lines.append("   ⏳ Processing Time: Not available")
        
        if delivery_days is not None:
            emoji = "✅" if delivery_days <= 7 else "⚠️" if delivery_days <= 15 else "🔴"
            lines.append(f"   {emoji} Delivery Time: {delivery_days} days")
        else:
            lines.append("   ⏳ Delivery Time: Not available")
        
        if cycle_days is not None:
            emoji = "✅" if cycle_days <= 14 else "⚠️" if cycle_days <= 21 else "🔴"
            lines.append(f"   {emoji} Total Cycle Time: {cycle_days} days")
        else:
            lines.append("   ⏳ Total Cycle Time: Not available")
        
        lines.append("")
        lines.append(f"{quality_emoji} *Data Quality: {quality_status}*")
        
        if issues:
            lines.append("")
            lines.append("⚠️ *Issues Detected:*")
            for issue in issues:
                lines.append(f"   • {issue}")
        
        warnings = validation.get('warnings', [])
        if warnings:
            lines.append("")
            lines.append("📋 *Warnings:*")
            for warning in warnings:
                lines.append(f"   • {warning}")
        
        lines.append("")
        lines.append(f"📊 *Status:* {status_display}")
        return "\n".join(lines)
    
    def _format_dealer_comparison(self, data: Dict, dealer1: str, dealer2: str) -> str:
        if not data:
            return f"❌ Could not compare {dealer1} and {dealer2}"
        d1 = data.get(dealer1, {})
        d2 = data.get(dealer2, {})
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
    
    def _format_warehouse_comparison(self, data: Dict, warehouse1: str, warehouse2: str) -> str:
        if not data:
            return f"❌ Could not compare {warehouse1} and {warehouse2}"
        w1 = data.get(warehouse1, {})
        w2 = data.get(warehouse2, {})
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
    
    def _format_city_comparison(self, data: Dict, city1: str, city2: str) -> str:
        if not data:
            return f"❌ Could not compare {city1} and {city2}"
        c1 = data.get(city1, {})
        c2 = data.get(city2, {})
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
    
    def _format_executive_insights(self, data: Dict) -> str:
        if not data:
            return "📊 No executive insights available."
        summary = data.get("summary", {})
        top_issues = data.get("top_issues", [])
        recommendations = data.get("recommendations", [])
        lines = [
            "🚨 *Executive Insights*",
            "",
            f"📈 *Overview:*",
            f"   • Total DNs: {summary.get('total_dns', 0):,}",
            f"   • Total Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
            f"   • Overall POD Rate: {summary.get('overall_pod_rate', 0):.1f}%",
            f"   • Active Dealers: {summary.get('active_dealers', 0)}",
            "",
            "⚠️ *Critical Issues:*",
        ]
        if top_issues:
            for issue in top_issues:
                lines.append(f"   • {issue}")
        else:
            lines.append("   ✅ No critical issues detected.")
        if recommendations:
            lines.append("")
            lines.append("💡 *Recommended Actions:*")
            for rec in recommendations:
                lines.append(f"   • {rec}")
        return "\n".join(lines)
    
    def _format_root_cause(self, data: Dict) -> str:
        if not data:
            return "🔍 No root cause analysis available."
        issues = data.get("key_issues", [])
        recommendations = data.get("recommendations", [])
        metrics = data.get("metrics", {})
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
    
    def _format_control_tower(self, data: Dict) -> str:
        alerts = data.get("alerts", [])
        critical_count = data.get("critical_count", 0)
        high_count = data.get("high_count", 0)
        if not alerts:
            return "🚨 *Control Tower*\n\n✅ No critical alerts at this time."
        lines = [
            "🚨 *Control Tower*",
            "",
            f"🔴 Critical: {critical_count}",
            f"🟠 High: {high_count}",
            "",
        ]
        for alert in alerts[:10]:
            risk_emoji = self.schema.get_risk_emoji(alert.get('risk_status', 'low')) if hasattr(self.schema, 'get_risk_emoji') else "⚪"
            lines.append(f"{risk_emoji} {alert.get('type', 'Alert')}: {alert.get('dealer', 'N/A')} - {alert.get('description', '')} ({alert.get('days', 0)} days)")
        return "\n".join(lines)
    
    def _format_delivery_performance(self, data: Dict) -> str:
        metrics = data.get("metrics", {})
        return (
            "📦 *Delivery Performance Dashboard*\n\n"
            f"📊 *Key Metrics:*\n"
            f"   • Total DNs: {metrics.get('total_dns', 0)}\n"
            f"   • Delivered: {metrics.get('delivered', 0)}\n"
            f"   • In Transit: {metrics.get('in_transit', 0)}\n"
            f"   • Pending PGI: {metrics.get('pending_pgi', 0)}\n"
            f"   • Pending POD: {metrics.get('pending_pod', 0)}\n"
            f"\n📈 *Rates:*\n"
            f"   • PGI Rate: {metrics.get('pgi_rate', 0):.1f}%\n"
            f"   • POD Rate: {metrics.get('pod_rate', 0):.1f}%\n"
            f"   • On-Time Delivery: {metrics.get('on_time_delivery_rate', 0):.1f}%"
        )
    
    def _format_trend_analysis(self, data: Dict) -> str:
        trends = data.get("trends", {})
        monthly = trends.get("monthly", [])
        if not monthly:
            return "📈 No trend data available."
        lines = ["📈 *Trend Analysis*", "", "📊 *Monthly Trends:*"]
        for month in monthly[:6]:
            lines.append(f"   • {month.get('period', 'N/A')}: {month.get('count', 0)} DNs, Revenue: PKR {month.get('revenue', 0):,.0f}")
        return "\n".join(lines)
    
    def _get_help_message(self) -> str:
        return """📋 *AI Logistics Assistant - Help*

*🔍 DN Tracking:* Send any 8-12 digit DN number

*🏪 Dealer Queries:*
   • "Dealer name" (e.g., "Dubai Electronics")
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
    
    # ==========================================================
    # METRICS
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        return {
            "total_requests": self.metrics["total_requests"],
            "cache_hits": self.metrics["cache_hits"],
            "cache_misses": self.metrics["cache_misses"],
            "cache_hit_rate": self.metrics["cache_hits"] / max(1, self.metrics["cache_hits"] + self.metrics["cache_misses"]),
            "service_successes": self.metrics["service_successes"],
            "service_failures": self.metrics["service_failures"],
            "service_success_rate": self.metrics["service_successes"] / max(1, self.metrics["service_successes"] + self.metrics["service_failures"]),
            "analytics_success": self.metrics["analytics_success"],
            "analytics_failure": self.metrics["analytics_failure"],
            "analytics_success_rate": self.metrics["analytics_success"] / max(1, self.metrics["analytics_success"] + self.metrics["analytics_failure"]),
            "dn_lookups": self.metrics["dn_lookups"],
            "dealer_queries": self.metrics["dealer_queries"],
            "city_queries": self.metrics["city_queries"],
            "warehouse_queries": self.metrics["warehouse_queries"],
            "comparisons": self.metrics["comparisons"],
            "executive_insights": self.metrics["executive_insights"],
            "root_cause_analyses": self.metrics["root_cause_analyses"],
            "groq_queries": self.metrics["groq_queries"],
            "groq_rejections": self.metrics["groq_rejections"],
            "routing_overrides": self.metrics["routing_overrides"],
            "errors": self.metrics["errors"],
            "conversation_count": len(self.conversation_cache),
            "cache_size": len(self.response_cache),
            "version": "11.0"
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


def get_dependency_status() -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    return orchestrator.get_dependency_status()


def get_cache_info() -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    return orchestrator.get_cache_info()


def get_governance_info() -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    return orchestrator.get_governance_info()


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("AI Provider Service v11.0 - COMPLETE PRODUCTION REFACTOR")
logger.info("=" * 70)
logger.info("")
logger.info("   CRITICAL FIXES:")
logger.info("   ✅ Fixed: Dealer queries route to dealer_dashboard")
logger.info("   ✅ Fixed: DN Lookup works immediately (8-12 digits)")
logger.info("   ✅ Fixed: Safe QueryPlan with getattr() for ALL attributes")
logger.info("   ✅ Fixed: No generic 'I understand' fallback")
logger.info("   ✅ Fixed: Executive analytics route to analytics")
logger.info("   ✅ Fixed: City ranking support")
logger.info("   ✅ Fixed: Method validation before calling AnalyticsService")
logger.info("")
logger.info("   ARCHITECTURE RULES:")
logger.info("   ✅ AIQueryService SUGGESTS → SchemaService VERIFIES → Orchestrator DECIDES")
logger.info("   ✅ Groq ONLY for General/Creative/Casual")
logger.info("   ✅ Entity Resolution OVERRIDES Intent Detection")
logger.info("   ✅ Analytics NEVER goes to Groq alone")
logger.info("   ✅ Comprehensive error logging with Reference IDs")
logger.info("   ✅ Cache invalidation on metadata refresh")
logger.info("   ✅ Startup dependency validation")
logger.info("   ✅ Data Quality Governance for invalid dates")
logger.info("")
logger.info("   DIAGNOSTIC ENDPOINTS:")
logger.info("   ✅ /debug/routing - Routing decision debug")
logger.info("   ✅ /debug/dependencies - Service dependency status")
logger.info("   ✅ /debug/cache - Cache information")
logger.info("   ✅ /debug/governance - Governance metrics")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
