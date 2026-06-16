# ==========================================================
# FILE: app/services/ai_provider_service.py (v9.0 - MASTER ORCHESTRATOR WITH GOVERNANCE)
# ==========================================================
# PURPOSE: Master Orchestrator - Final Governance & Routing Authority
# ARCHITECTURE RULES:
# 1. AIProviderService is the FINAL governance layer
# 2. Always revalidate AIQueryService decisions
# 3. Groq ONLY for general knowledge, creative tasks, casual conversation
# 4. Analytics NEVER goes to Groq alone
# 5. Entity resolution overrides intent detection
# 6. Comprehensive logging for debugging
# ==========================================================

import time
import uuid
import hashlib
import re
import asyncio
from typing import Optional, Callable, Any, Dict, List, Tuple
from cachetools import TTLCache
from loguru import logger
from sqlalchemy.orm import Session
from datetime import datetime

from app.config import config
from app.database import SessionLocal

from app.services.ai_query_service import AIQueryService, QueryPlan, get_ai_query_service
from app.services.analytics_service import AnalyticsService, get_analytics_service
from app.services.kpi_service import KPIService, get_kpi_service
from app.services.groq_service import GroqService, get_groq_service
from app.schemas.schema_service import get_schema_service, DN_PATTERN


# ==========================================================
# CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800

# ==========================================================
# GROQ PROTECTION - Keywords that MUST NOT go to Groq
# ==========================================================

PROTECTED_GROQ_KEYWORDS = {
    # Dealer terms
    'dealer', 'customer', 'sold to', 'buyer', 'traders', 'electronics',
    'enterprises', 'industries', 'corporation', 'group', 'sons',
    
    # Logistics terms
    'delivery', 'pgi', 'pod', 'dn', 'warehouse', 'city', 'ship to',
    'dispatch', 'transit', 'delivered', 'pending',
    
    # KPI terms
    'revenue', 'sales', 'units', 'quantity', 'aging', 'performance',
    'kpi', 'rate', 'completion', 'efficiency', 'metrics',
    
    # Analytics terms
    'root cause', 'improvement', 'bottleneck', 'insight', 'executive',
    'critical', 'urgent', 'priority', 'alert', 'issue', 'problem',
    
    # Comparison terms
    'top', 'bottom', 'best', 'worst', 'compare', 'vs', 'versus',
    'highest', 'lowest', 'ranking',
    
    # Time terms
    'today', 'yesterday', 'week', 'month', 'year', 'trend',
    
    # Numbers (for DN detection)
    '6243', '6244', '6245', '6246'
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
# MASTER ORCHESTRATOR WITH GOVERNANCE
# ==========================================================

class AIOrchestrator:
    """
    MASTER ORCHESTRATOR - Final Governance & Routing Authority
    
    Architecture Rules Enforced:
    1. AIQueryService is trusted but ALWAYS revalidated
    2. Entity resolution OVERRIDES intent detection
    3. Groq ONLY for general knowledge, creative, casual
    4. Analytics NEVER goes to Groq alone
    5. Comprehensive logging for every decision
    """
    
    def __init__(self):
        self.query_service = get_ai_query_service()
        self.analytics = get_analytics_service()
        self.kpi = get_kpi_service()
        self.groq = get_groq_service()
        self.schema = get_schema_service()
        
        self.response_cache = TTLCache(maxsize=500, ttl=CACHE_TTL_SECONDS)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        
        self.metrics = {
            "total_requests": 0,
            "cache_hits": 0,
            "service_successes": 0,
            "service_failures": 0,
            "groq_uses": 0,
            "analytics_uses": 0,
            "overrides": 0,
            "rejections": 0
        }
        
        logger.info("=" * 60)
        logger.info("AI Orchestrator v9.0 - Master Governance Layer")
        logger.info("=" * 60)
        logger.info("")
        logger.info("   GOVERNANCE RULES:")
        logger.info("   ✅ Entity Resolution OVERRIDES Intent Detection")
        logger.info("   ✅ Groq ONLY for General/Creative/Casual")
        logger.info("   ✅ Analytics NEVER goes to Groq alone")
        logger.info("   ✅ DN Lookup IMMEDIATE (8-12 digits)")
        logger.info("   ✅ Comprehensive Logging")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 60)
    
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
        ).info(f"📥 Orchestrator processing: {question[:100]}")
        
        try:
            # Step 1: Load context
            context = self._load_context(phone_number)
            context_dict = context.to_dict() if context else {}
            
            # Step 2: Check for DN immediately (HIGHEST PRIORITY)
            if self._is_dn_query(question):
                logger.info(f"🔍 DN detected: {question}")
                response = self._execute_dn_lookup(question)
                self._update_context(phone_number, "dn_lookup", "dn", question, req_id)
                self._cache_response(question, phone_number, response)
                return response
            
            # Step 3: GOVERNANCE LAYER - Entity Resolution Override
            entity_result = self.schema.resolve_entity(question)
            
            if entity_result["type"] != "none":
                entity_type = entity_result["type"]
                entity_name = entity_result["name"]
                confidence = entity_result["confidence"]
                
                logger.info(
                    f"📍 GOVERNANCE: Entity resolved: {entity_type}='{entity_name}' "
                    f"(confidence: {confidence:.2f})"
                )
                
                # Override: Entity-only queries go to analytics
                if self._is_entity_only_query(question, entity_name):
                    logger.info(f"⚡ OVERRIDE: Entity-only query → {entity_type}_dashboard")
                    self.metrics["overrides"] += 1
                    response = self._execute_entity_dashboard(entity_type, entity_name)
                    self._update_context(phone_number, f"{entity_type}_dashboard", entity_type, entity_name, req_id)
                    self._cache_response(question, phone_number, response)
                    return response
            
            # Step 4: Get query plan from AIQueryService (trust but verify)
            query_plan = self._get_query_plan(question, context_dict)
            
            # Step 5: GOVERNANCE LAYER - Revalidate before execution
            validated_plan = self._validate_and_override(query_plan, question)
            
            # Log routing decision
            logger.info(
                f"🎯 ROUTING: intent={validated_plan.intent}, "
                f"entity={validated_plan.entity}, "
                f"entity_type={validated_plan.entity_type}, "
                f"service={validated_plan.service}"
            )
            
            # Step 6: Execute service
            response = self._execute_service(validated_plan, context_dict, req_id)
            
            # Step 7: GROQ GOVERNANCE - Enrich only, never replace
            response = self._apply_groq_governance(response, validated_plan, question, context_dict)
            
            # Step 8: Update context
            self._update_context(
                phone_number,
                validated_plan.intent,
                validated_plan.entity_type or "none",
                validated_plan.entity or question,
                req_id,
                response
            )
            
            # Step 9: Cache response
            self._cache_response(question, phone_number, response)
            
            duration_ms = int((time.time() - start_time) * 1000)
            logger.bind(request_id=req_id).info(
                f"✅ Orchestrator done: {duration_ms}ms | "
                f"Service: {validated_plan.service} | "
                f"Groq: {self.metrics['groq_uses'] > 0}"
            )
            
            return response
            
        except Exception as e:
            logger.exception(f"[{req_id}] Orchestrator fatal error: {e}")
            return self._get_fallback_response(question, str(e))
    
    # ==========================================================
    # DN DETECTION (HIGHEST PRIORITY)
    # ==========================================================
    
    def _is_dn_query(self, question: str) -> bool:
        """Check if query is a DN number (8-12 digits)."""
        return bool(DN_PATTERN.match(question.strip()))
    
    def _execute_dn_lookup(self, question: str) -> str:
        """Execute DN lookup immediately - no intent detection needed."""
        dn_number = question.strip()
        result = self.analytics.get_dn_analytics(dn_number)
        return self._format_dn_details(result)
    
    # ==========================================================
    # ENTITY-ONLY QUERY DETECTION
    # ==========================================================
    
    def _is_entity_only_query(self, question: str, entity_name: str) -> bool:
        """Check if query is just an entity name with minimal extra words."""
        question_clean = question.lower().strip()
        entity_clean = entity_name.lower().strip()
        
        # Exact match
        if question_clean == entity_clean:
            return True
        
        # Entity with common prefixes
        prefixes = ["show ", "display ", "get ", "view ", "tell me about "]
        for prefix in prefixes:
            if question_clean.startswith(prefix) and question_clean[len(prefix):].strip() == entity_clean:
                return True
        
        # Check if all meaningful words are from entity
        question_words = set(question_clean.split())
        entity_words = set(entity_clean.split())
        
        # Remove common words
        common_words = {"show", "display", "get", "view", "tell", "me", "about", "the", "a", "an"}
        meaningful_question_words = question_words - common_words
        meaningful_entity_words = entity_words - common_words
        
        # If all meaningful question words are in entity, it's entity-only
        if meaningful_question_words and meaningful_question_words.issubset(meaningful_entity_words):
            return True
        
        return False
    
    # ==========================================================
    # ENTITY DASHBOARD EXECUTION
    # ==========================================================
    
    def _execute_entity_dashboard(self, entity_type: str, entity_name: str) -> str:
        """Execute dashboard for entity type."""
        if entity_type == "dealer":
            result = self.analytics.get_dealer_dashboard(entity_name)
            return self._format_dealer_dashboard(result, entity_name)
        elif entity_type == "city":
            result = self.analytics.get_city_dashboard(entity_name)
            return self._format_city_dashboard(result, entity_name)
        elif entity_type == "warehouse":
            result = self.analytics.get_warehouse_dashboard(entity_name)
            return self._format_warehouse_dashboard(result, entity_name)
        else:
            return f"❌ Unknown entity type: {entity_type}"
    
    # ==========================================================
    # GOVERNANCE LAYER - Validate and Override
    # ==========================================================
    
    def _validate_and_override(self, query_plan: QueryPlan, question: str) -> QueryPlan:
        """
        GOVERNANCE LAYER: Revalidate and override AIQueryService decisions.
        
        Rules:
        1. If entity detected, override to appropriate dashboard
        2. If Groq selected but query is logistics, reject
        3. If city comparison, route to comparison analytics
        4. If dealer comparison, route to comparison analytics
        """
        
        # Rule 1: Entity Override
        entity_result = self.schema.resolve_entity(question)
        if entity_result["type"] != "none":
            entity_type = entity_result["type"]
            entity_name = entity_result["name"]
            
            # Check if this is a comparison query
            if " vs " in question.lower() or " compare " in question.lower() or " versus " in question.lower():
                # Parse comparison
                entities = self._parse_comparison(question)
                if entities and len(entities) == 2:
                    query_plan.intent = f"compare_{entity_type}s"
                    query_plan.entity = entities[0]
                    query_plan.entity2 = entities[1]
                    query_plan.entity_type = entity_type
                    query_plan.service = "analytics"
                    logger.info(f"⚡ OVERRIDE: Comparison detected → compare_{entity_type}s")
                    return query_plan
            
            # Override to dashboard for entity-only or low-confidence intents
            if query_plan.intent in ["general_ai", "help"] or query_plan.confidence < 0.70:
                query_plan.intent = f"{entity_type}_dashboard"
                query_plan.entity = entity_name
                query_plan.entity_type = entity_type
                query_plan.service = "analytics"
                logger.info(f"⚡ OVERRIDE: {query_plan.intent} (was: {query_plan.intent})")
                self.metrics["overrides"] += 1
        
        # Rule 2: Groq Protection
        if query_plan.service == "groq" and self._is_logistics_query(question):
            query_plan.service = "analytics"
            query_plan.intent = "executive_insight"
            logger.info(f"🚫 REJECTED: Groq blocked for logistics query")
            self.metrics["rejections"] += 1
        
        # Rule 3: City Ranking Detection
        if self._is_city_ranking_query(question):
            query_plan.intent = "city_ranking"
            query_plan.service = "analytics"
            logger.info(f"📊 OVERRIDE: City ranking detected")
        
        # Rule 4: Dealer Ranking Detection
        if self._is_dealer_ranking_query(question):
            query_plan.intent = "dealer_ranking"
            query_plan.service = "analytics"
            logger.info(f"📊 OVERRIDE: Dealer ranking detected")
        
        return query_plan
    
    # ==========================================================
    # COMPARISON PARSING
    # ==========================================================
    
    def _parse_comparison(self, question: str) -> List[str]:
        """Parse comparison query to extract entities."""
        question_lower = question.lower()
        
        # Check for comparison patterns
        patterns = [
            r"compare\s+(.+?)\s+(?:vs|versus|and)\s+(.+)",
            r"(.+?)\s+(?:vs|versus|and)\s+(.+)",
            r"compare\s+(.+?)\s+with\s+(.+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, question_lower, re.IGNORECASE)
            if match:
                entity1 = match.group(1).strip()
                entity2 = match.group(2).strip()
                
                # Resolve entities
                resolved1 = self.schema.resolve_entity(entity1)
                resolved2 = self.schema.resolve_entity(entity2)
                
                if resolved1["type"] != "none" and resolved2["type"] != "none":
                    return [resolved1["name"], resolved2["name"]]
        
        return []
    
    # ==========================================================
    # RANKING QUERY DETECTION
    # ==========================================================
    
    def _is_city_ranking_query(self, question: str) -> bool:
        """Check if query is asking for city ranking."""
        question_lower = question.lower()
        patterns = [
            "which city", "top city", "highest city", "best city",
            "city with highest", "city ranking", "cities by"
        ]
        return any(p in question_lower for p in patterns)
    
    def _is_dealer_ranking_query(self, question: str) -> bool:
        """Check if query is asking for dealer ranking."""
        question_lower = question.lower()
        patterns = [
            "top dealer", "best dealer", "highest dealer",
            "dealer ranking", "dealers by", "top 10 dealer"
        ]
        return any(p in question_lower for p in patterns)
    
    # ==========================================================
    # GROQ PROTECTION
    # ==========================================================
    
    def _is_logistics_query(self, question: str) -> bool:
        """Check if query contains logistics keywords (should not go to Groq)."""
        question_lower = question.lower()
        
        # Check protected keywords
        for keyword in PROTECTED_GROQ_KEYWORDS:
            if keyword in question_lower:
                return True
        
        # Check for metrics
        if self.schema.detect_metric(question):
            return True
        
        # Check for logistics keywords
        if self.schema.is_logistics_keyword(question):
            return True
        
        return False
    
    # ==========================================================
    # QUERY PLAN
    # ==========================================================
    
    def _get_query_plan(self, question: str, context: Dict) -> QueryPlan:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if asyncio.iscoroutinefunction(self.query_service.process_query):
            return loop.run_until_complete(
                self.query_service.process_query(question, context)
            )
        return self.query_service.process_query(question, context)
    
    # ==========================================================
    # SERVICE EXECUTION
    # ==========================================================
    
    def _execute_service(self, query_plan: QueryPlan, context: Dict, req_id: str) -> str:
        intent = query_plan.intent
        entity = query_plan.entity
        
        try:
            if query_plan.service == "analytics":
                self.metrics["analytics_uses"] += 1
                response = self._execute_analytics(intent, query_plan)
            elif query_plan.service == "kpi":
                response = self._execute_kpi(intent, query_plan)
            elif query_plan.service == "groq":
                response = self._execute_groq(query_plan, context)
            else:
                response = self._get_help_message()
            
            self.metrics["service_successes"] += 1
            return response
            
        except Exception as e:
            logger.exception(f"[{req_id}] Service execution error: {e}")
            self.metrics["service_failures"] += 1
            return self._get_error_response(query_plan, str(e))
    
    # ==========================================================
    # ANALYTICS EXECUTION
    # ==========================================================
    
    def _execute_analytics(self, intent: str, query_plan: QueryPlan) -> str:
        entity = query_plan.entity
        entity2 = query_plan.entity2
        
        # DEALER ANALYTICS
        if intent == "dealer_dashboard" and entity:
            result = self.analytics.get_dealer_dashboard(entity)
            return self._format_dealer_dashboard(result, entity)
        
        if intent == "dealer_revenue" and entity:
            result = self.analytics.get_dealer_revenue(entity)
            return self._format_dealer_revenue(result, entity)
        
        if intent == "dealer_units" and entity:
            result = self.analytics.get_dealer_units(entity)
            return self._format_dealer_units(result, entity)
        
        if intent == "dealer_performance" and entity:
            result = self.analytics.get_dealer_performance(entity)
            return self._format_dealer_performance(result, entity)
        
        if intent == "dealer_aging" and entity:
            result = self.analytics.get_dealer_aging(entity)
            return self._format_dealer_aging(result, entity)
        
        # WAREHOUSE ANALYTICS
        if intent == "warehouse_dashboard" and entity:
            result = self.analytics.get_warehouse_dashboard(entity)
            return self._format_warehouse_dashboard(result, entity)
        
        if intent == "warehouse_performance" and entity:
            result = self.analytics.get_warehouse_dashboard(entity)
            return self._format_warehouse_performance(result, entity)
        
        # CITY ANALYTICS
        if intent == "city_dashboard" and entity:
            result = self.analytics.get_city_dashboard(entity)
            return self._format_city_dashboard(result, entity)
        
        if intent == "city_performance" and entity:
            result = self.analytics.get_city_dashboard(entity)
            return self._format_city_performance(result, entity)
        
        if intent == "city_ranking":
            result = self.analytics.get_city_ranking()
            return self._format_city_ranking(result)
        
        # DEALER RANKING
        if intent == "dealer_ranking":
            top = "top" in query_plan.original_message.lower()
            limit = query_plan.limit or 10
            result = self.analytics.get_dealer_ranking(limit=limit, top=top)
            return self._format_dealer_ranking(result, top)
        
        # COMPARISON ANALYTICS
        if intent == "compare_dealers" and entity and entity2:
            result = self.analytics.compare_dealers(entity, entity2)
            return self._format_dealer_comparison(result, entity, entity2)
        
        if intent == "compare_warehouses" and entity and entity2:
            result = self.analytics.compare_warehouses(entity, entity2)
            return self._format_warehouse_comparison(result, entity, entity2)
        
        if intent == "compare_cities" and entity and entity2:
            result = self.analytics.compare_cities(entity, entity2)
            return self._format_city_comparison(result, entity, entity2)
        
        # DN ANALYTICS
        if intent == "dn_lookup" and entity:
            result = self.analytics.get_dn_analytics(entity)
            return self._format_dn_details(result)
        
        # EXECUTIVE & ROOT CAUSE
        if intent == "executive_insight":
            result = self.analytics.get_executive_summary()
            return self._format_executive_insights(result)
        
        if intent == "root_cause":
            result = self.analytics.get_root_cause_insights()
            return self._format_root_cause(result)
        
        if intent == "control_tower":
            result = self.analytics.get_control_tower_alerts()
            return self._format_control_tower(result)
        
        if intent == "delivery_performance":
            result = self.analytics.get_delivery_performance()
            return self._format_delivery_performance(result)
        
        if intent == "trend":
            result = self.analytics.get_trend_analysis()
            return self._format_trend_analysis(result)
        
        if intent == "help":
            return self._get_help_message()
        
        return self._get_help_message()
    
    # ==========================================================
    # KPI EXECUTION
    # ==========================================================
    
    def _execute_kpi(self, intent: str, query_plan: QueryPlan) -> str:
        entity = query_plan.entity
        
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
    
    # ==========================================================
    # GROQ EXECUTION (ONLY FOR APPROPRIATE QUERIES)
    # ==========================================================
    
    def _execute_groq(self, query_plan: QueryPlan, context: Dict) -> str:
        """Execute Groq ONLY for appropriate queries."""
        question = query_plan.original_message
        
        # Double-check: Is this really a Groq-appropriate query?
        if self._is_logistics_query(question):
            logger.warning(f"🚫 GROQ BLOCKED: Logistics query rejected at execution layer")
            self.metrics["rejections"] += 1
            return self._get_error_response(
                query_plan,
                "Logistics queries must use analytics, not Groq"
            )
        
        if self.groq.is_available:
            response = self.groq.chat(question, context)
            self.metrics["groq_uses"] += 1
            return response
        
        return self._get_fallback_response(question, "Groq service unavailable")
    
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
        if not self.groq.is_available:
            return response
        
        # Only enrich specific analytics intents
        if query_plan.intent in ["executive_insight", "root_cause"] and len(response) > 50:
            try:
                # Get concise Groq summary based on analytics
                enrichment_prompt = f"""
Based on this logistics analytics data:

{response[:600]}

Provide a brief, professional executive summary (2-3 sentences) that highlights the most critical insight and recommends one immediate action.

Keep it concise and actionable. Do not repeat the data, just provide insight.
"""
                groq_summary = self.groq.chat(enrichment_prompt, context)
                
                if groq_summary and len(groq_summary) > 10:
                    self.metrics["groq_uses"] += 1
                    # Combine analytics + Groq insight
                    return f"{response}\n\n💡 *AI Insight:*\n{groq_summary}"
            except Exception as e:
                logger.warning(f"Groq enrichment failed: {e}")
        
        # General AI queries go to Groq directly
        if query_plan.intent == "general_ai":
            groq_response = self.groq.chat(question, context)
            if groq_response and len(groq_response) > 10:
                self.metrics["groq_uses"] += 1
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
    
    def _cache_response(self, question: str, phone_number: Optional[str], response: str):
        """Cache response with TTL."""
        cache_key = self._generate_cache_key(question, phone_number)
        self.response_cache[cache_key] = response
    
    def _generate_cache_key(self, question: str, phone_number: Optional[str]) -> str:
        key = question.lower().strip()
        if phone_number:
            key = f"{phone_number}:{key}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def clear_caches(self):
        """Clear response cache (call after metadata refresh or Excel import)."""
        self.response_cache.clear()
        self.conversation_cache.clear()
        logger.info("🗑️ All caches cleared")
        return {"status": "cleared", "version": "9.0"}
    
    # ==========================================================
    # DIAGNOSTIC METHODS
    # ==========================================================
    
    def get_routing_debug(self, question: str) -> Dict[str, Any]:
        """Debug routing decision for a query."""
        # Get entity resolution
        entity_result = self.schema.resolve_entity(question)
        
        # Get query plan
        context = {}
        query_plan = self._get_query_plan(question, context)
        
        # Validate and override
        validated = self._validate_and_override(query_plan, question)
        
        return {
            "query": question,
            "ai_query_service": {
                "intent": query_plan.intent,
                "entity": query_plan.entity,
                "entity_type": query_plan.entity_type,
                "service": query_plan.service,
                "confidence": query_plan.confidence
            },
            "schema_override": {
                "entity_resolved": entity_result["type"] != "none",
                "entity_type": entity_result.get("type"),
                "entity_name": entity_result.get("name"),
                "confidence": entity_result.get("confidence", 0)
            },
            "final_decision": {
                "intent": validated.intent,
                "entity": validated.entity,
                "entity_type": validated.entity_type,
                "service": validated.service,
                "groq_protected": self._is_logistics_query(question)
            }
        }
    
    # ==========================================================
    # FORMATTERS - DEALER
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
        risk_emoji = self.schema.get_risk_emoji(risk_status)
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
            lines.append(
                f"{i}. {dealer.get('name', 'N/A')}\n"
                f"   Revenue: PKR {revenue:,.0f} | POD Rate: {pod_rate:.1f}%"
            )
        
        return "\n".join(lines)
    
    # ==========================================================
    # FORMATTERS - WAREHOUSE
    # ==========================================================
    
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
    
    # ==========================================================
    # FORMATTERS - CITY
    # ==========================================================
    
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
            lines.append(
                f"{i}. {city.get('name', 'N/A')}\n"
                f"   Revenue: PKR {revenue:,.0f} | POD Rate: {pod_rate:.1f}% | Dealers: {dealers}"
            )
        
        return "\n".join(lines)
    
    # ==========================================================
    # FORMATTERS - DN
    # ==========================================================
    
    def _format_dn_details(self, data: Dict) -> str:
        if not data or not data.get("found"):
            return "❌ DN not found."
        
        record = data.get("record", {})
        validation = data.get("validation", {})
        durations = validation.get("durations", {})
        status = data.get("status", "unknown")
        
        # Use correct field names
        processing_days = durations.get('processing_time_days')
        delivery_days = durations.get('delivery_time_days')
        cycle_days = durations.get('total_cycle_days')
        
        # Data quality status
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
        
        # Status display
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
    
    # ==========================================================
    # FORMATTERS - COMPARISON
    # ==========================================================
    
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
    
    # ==========================================================
    # FORMATTERS - EXECUTIVE & ROOT CAUSE
    # ==========================================================
    
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
    
    # ==========================================================
    # FORMATTERS - CONTROL TOWER
    # ==========================================================
    
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
            risk_emoji = self.schema.get_risk_emoji(alert.get('risk_status', 'low'))
            lines.append(
                f"{risk_emoji} {alert.get('type', 'Alert')}: "
                f"{alert.get('dealer', 'N/A')} - "
                f"{alert.get('description', '')} ({alert.get('days', 0)} days)"
            )
        
        return "\n".join(lines)
    
    # ==========================================================
    # FORMATTERS - PERFORMANCE & TREND
    # ==========================================================
    
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
            lines.append(
                f"   • {month.get('period', 'N/A')}: "
                f"{month.get('count', 0)} DNs, "
                f"Revenue: PKR {month.get('revenue', 0):,.0f}"
            )
        
        return "\n".join(lines)
    
    # ==========================================================
    # ERROR & FALLBACK RESPONSES
    # ==========================================================
    
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
    
    def _get_error_response(self, query_plan: QueryPlan, error: str) -> str:
        """Structured error response with logging."""
        logger.error(
            f"❌ Service error: intent={query_plan.intent}, "
            f"entity={query_plan.entity}, "
            f"service={query_plan.service}, "
            f"error={error}"
        )
        return (
            f"⚠️ Unable to retrieve analytics data right now.\n\n"
            f"• Intent: {query_plan.intent}\n"
            f"• Service: {query_plan.service}\n"
            f"• Error: {error[:100]}\n\n"
            f"Please try again or contact support if the issue persists."
        )
    
    def _get_fallback_response(self, question: str, error: str = "") -> str:
        """Generic fallback with logging."""
        logger.error(f"⚠️ Fallback triggered: question={question}, error={error}")
        return f"I understand you're asking: {question[:100]}\n\nType 'Help' for available commands."
    
    # ==========================================================
    # METRICS & ADMIN
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        return {
            "total_requests": self.metrics["total_requests"],
            "cache_hits": self.metrics["cache_hits"],
            "cache_hit_rate": self.metrics["cache_hits"] / max(1, self.metrics["total_requests"]),
            "service_successes": self.metrics["service_successes"],
            "service_failures": self.metrics["service_failures"],
            "service_success_rate": self.metrics["service_successes"] / max(1, self.metrics["service_successes"] + self.metrics["service_failures"]),
            "analytics_uses": self.metrics["analytics_uses"],
            "groq_uses": self.metrics["groq_uses"],
            "overrides": self.metrics["overrides"],
            "rejections": self.metrics["rejections"],
            "conversation_count": len(self.conversation_cache),
            "cache_size": len(self.response_cache),
            "version": "9.0"
        }
    
    def get_debug_info(self, question: str) -> Dict[str, Any]:
        """Get debug info for a specific question."""
        return self.get_routing_debug(question)


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
# WRAPPER FUNCTION (PRESERVED SIGNATURE - CRITICAL)
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
    """Clear AI response cache (call after metadata refresh or Excel import)."""
    orchestrator = get_orchestrator()
    return orchestrator.clear_caches()


def get_routing_debug(question: str) -> Dict[str, Any]:
    """Get routing debug information for a query."""
    orchestrator = get_orchestrator()
    return orchestrator.get_routing_debug(question)


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 60)
logger.info("AI Provider Service v9.0 - Master Governance Layer")
logger.info("=" * 60)
logger.info("")
logger.info("   GOVERNANCE RULES:")
logger.info("   ✅ Entity Resolution OVERRIDES Intent Detection")
logger.info("   ✅ Groq ONLY for General/Creative/Casual")
logger.info("   ✅ Analytics NEVER goes to Groq alone")
logger.info("   ✅ DN Lookup IMMEDIATE (8-12 digits)")
logger.info("   ✅ Context Intelligence (last_dealer, last_city, last_warehouse)")
logger.info("   ✅ City Ranking Support")
logger.info("   ✅ Dealer Comparison Support")
logger.info("   ✅ Cache Invalidation on Metadata Refresh")
logger.info("   ✅ Comprehensive Logging")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 60)
