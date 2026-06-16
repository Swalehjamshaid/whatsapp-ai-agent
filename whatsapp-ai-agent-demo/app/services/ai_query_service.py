# ==========================================================
# FILE: app/services/ai_provider_service.py (v12.0 - ENTERPRISE ARCHITECTURE v4.0)
# ==========================================================
# ROLE: Master Orchestrator
# PURPOSE: Coordinate the complete request lifecycle
# 
# ARCHITECTURE RULES ENFORCED:
# 1. This file is the ORCHESTRATOR - NOT the decision engine
# 2. ai_query_service.py makes decisions, this file orchestrates
# 3. NEVER execute SQL queries directly
# 4. NEVER calculate KPIs directly
# 5. NEVER perform analytics directly
# 6. ALWAYS call appropriate service
# 7. ALWAYS handle service failures gracefully
# 8. ALWAYS track performance metrics
# 9. Groq ONLY for Executive/Root Cause/AI Insights
# 10. Database is ALWAYS the source of truth
# ==========================================================

import time
import uuid
import hashlib
import re
from typing import Optional, Callable, Any, Dict, List, Tuple
from cachetools import TTLCache
from loguru import logger
from sqlalchemy.orm import Session

from app.config import config
from app.database import SessionLocal

# ==========================================================
# LAZY IMPORTS - Break circular dependencies
# ==========================================================

def _get_ai_query_service():
    from app.services.ai_query_service import get_ai_query_service
    return get_ai_query_service()

def _get_analytics_service():
    from app.services.analytics_service import get_analytics_service
    return get_analytics_service()

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

# ==========================================================
# CONVERSATION CONTEXT
# ==========================================================

class ConversationContext:
    """Maintains conversation state for context-aware responses."""
    
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
# MASTER ORCHESTRATOR
# ==========================================================

class AIOrchestrator:
    """
    MASTER ORCHESTRATOR - Coordinates Complete Request Lifecycle
    
    ARCHITECTURE RESPONSIBILITIES:
    1. Receive question from webhook
    2. Load conversation context
    3. Call ai_query_service.py for routing decision
    4. Execute correct service based on routing
    5. Call Groq when required (Executive/Root Cause)
    6. Handle service failures gracefully
    7. Standardize responses
    8. Track performance metrics
    
    ARCHITECTURE BOUNDARIES:
    - NEVER execute SQL queries directly
    - NEVER calculate KPIs directly
    - NEVER perform analytics directly
    - ALWAYS call appropriate service
    """
    
    def __init__(self):
        # ==========================================================
        # LAZY SERVICE INITIALIZATION
        # ==========================================================
        
        self._query_service = None
        self._analytics = None
        self._kpi = None
        self._groq = None
        self._schema = None
        self._whatsapp = None
        
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
            "kpi_queries": 0,
            "executive_queries": 0,
            "groq_uses": 0,
            "analytics_success": 0,
            "analytics_failure": 0,
            "service_timeouts": 0,
            "errors": 0
        }
        
        # ==========================================================
        # STARTUP LOGGING
        # ==========================================================
        
        logger.info("=" * 70)
        logger.info("AI Orchestrator v12.0 - Enterprise Architecture v4.0")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ROLE: Master Orchestrator")
        logger.info("   PURPOSE: Coordinate Complete Request Lifecycle")
        logger.info("")
        logger.info("   BOUNDARIES:")
        logger.info("   ✅ Orchestrates - Does NOT execute business logic")
        logger.info("   ✅ Routes - Does NOT make routing decisions")
        logger.info("   ✅ Coordinates - Does NOT access database")
        logger.info("   ✅ Calls Services - Does NOT calculate KPIs")
        logger.info("")
        logger.info("   STATUS: ✅ ENTERPRISE READY")
        logger.info("=" * 70)
    
    # ==========================================================
    # LAZY PROPERTIES - Services loaded on first access
    # ==========================================================
    
    @property
    def query_service(self):
        if self._query_service is None:
            self._query_service = _get_ai_query_service()
        return self._query_service
    
    @property
    def analytics(self):
        if self._analytics is None:
            self._analytics = _get_analytics_service()
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
        """
        Main entry point for WhatsApp queries.
        
        FLOW:
        1. Check cache
        2. Load context
        3. Get routing decision from ai_query_service
        4. Execute appropriate service
        5. Apply Groq enrichment if needed
        6. Update context and cache
        7. Return response
        
        Args:
            question: User's question
            session_factory: Optional session factory
            phone_number: User's phone number
            user_id: Optional user ID
            request_id: Optional request ID
            
        Returns:
            Formatted response string
        """
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
                logger.debug(f"⚡ Cache hit for: {question[:50]}")
                return cached_response
            
            self.metrics["cache_misses"] += 1
            
            # ==========================================================
            # STEP 2: Load Context
            # ==========================================================
            
            context = self._load_context(phone_number)
            context_dict = context.to_dict() if context else {}
            
            # ==========================================================
            # STEP 3: Get Routing Decision (ai_query_service)
            # ==========================================================
            
            routing_decision = self.query_service.process_query(question, context_dict)
            
            intent = routing_decision.get("intent", "help")
            entity = routing_decision.get("entity")
            entity_type = routing_decision.get("entity_type")
            service = routing_decision.get("service", "analytics")
            confidence = routing_decision.get("confidence", 0.0)
            needs_groq = routing_decision.get("needs_groq", False)
            
            logger.info(
                f"🎯 Routing Decision: intent={intent}, "
                f"entity={entity}, "
                f"service={service}, "
                f"confidence={confidence:.2f}"
            )
            
            # ==========================================================
            # STEP 4: Execute Service
            # ==========================================================
            
            if service == "analytics":
                response = self._execute_analytics_service(intent, entity, req_id)
                self.metrics["analytics_success"] += 1
                
            elif service == "kpi":
                response = self._execute_kpi_service(intent, entity, req_id)
                self.metrics["kpi_queries"] += 1
                
            elif service == "groq":
                response = self._execute_groq_service(question, context_dict, req_id)
                self.metrics["groq_uses"] += 1
                
            else:
                response = self._get_help_message()
            
            # ==========================================================
            # STEP 5: Groq Enrichment (Executive/Root Cause)
            # ==========================================================
            
            if needs_groq and not service == "groq":
                response = self._enrich_with_groq(response, intent, question, context_dict)
            
            # ==========================================================
            # STEP 6: Update Context & Cache
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
            
            # ==========================================================
            # STEP 7: Track Performance
            # ==========================================================
            
            duration_ms = int((time.time() - start_time) * 1000)
            logger.bind(request_id=req_id).info(
                f"✅ Done: {duration_ms}ms | "
                f"Intent: {intent} | "
                f"Service: {service} | "
                f"Groq: {needs_groq}"
            )
            
            return response
            
        except Exception as e:
            self.metrics["errors"] += 1
            error_id = str(uuid.uuid4())[:8]
            logger.exception(f"[{req_id}] FATAL ERROR [{error_id}]: {e}")
            return self._get_error_response(question, e, error_id, req_id)
    
    # ==========================================================
    # SERVICE EXECUTION
    # ==========================================================
    
    def _execute_analytics_service(self, intent: str, entity: Optional[str], req_id: str) -> str:
        """
        Execute Analytics Service.
        
        This service handles:
        - Dealer Dashboard
        - Dealer Revenue
        - Dealer Units
        - Dealer Performance
        - Dealer Aging
        - Dealer Ranking
        - Warehouse Dashboard
        - City Dashboard
        - City Ranking
        - Executive Insights
        - Root Cause Analysis
        - Control Tower
        - Trend Analysis
        """
        logger.debug(f"📊 Executing Analytics Service: intent={intent}, entity={entity}")
        
        try:
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
            
            if intent == "dealer_ranking":
                result = self.analytics.get_dealer_ranking(limit=10, top=True)
                return self._format_dealer_ranking(result)
            
            if intent == "warehouse_dashboard" and entity:
                result = self.analytics.get_warehouse_dashboard(entity)
                return self._format_warehouse_dashboard(result, entity)
            
            if intent == "city_dashboard" and entity:
                result = self.analytics.get_city_dashboard(entity)
                return self._format_city_dashboard(result, entity)
            
            if intent == "city_ranking":
                result = self.analytics.get_city_ranking()
                return self._format_city_ranking(result)
            
            if intent == "executive_insight":
                result = self.analytics.get_executive_summary()
                self.metrics["executive_queries"] += 1
                return self._format_executive_insights(result)
            
            if intent == "root_cause":
                result = self.analytics.get_root_cause_insights()
                self.metrics["executive_queries"] += 1
                return self._format_root_cause(result)
            
            if intent == "control_tower":
                result = self.analytics.get_control_tower_alerts()
                return self._format_control_tower(result)
            
            if intent == "trend":
                result = self.analytics.get_trend_analysis()
                return self._format_trend_analysis(result)
            
            if intent == "dn_lookup" and entity:
                result = self.analytics.get_dn_analytics(entity)
                self.metrics["dn_lookups"] += 1
                return self._format_dn_details(result)
            
            if intent == "help":
                return self._get_help_message()
            
            return self._get_help_message()
            
        except Exception as e:
            self.metrics["analytics_failure"] += 1
            logger.error(f"Analytics service failed for {intent}: {e}")
            return self._get_service_error_response(intent, entity, "analytics", e)
    
    def _execute_kpi_service(self, intent: str, entity: Optional[str], req_id: str) -> str:
        """
        Execute KPI Service.
        
        This service handles:
        - Pending PGI
        - Pending POD
        - PGI Aging
        - POD Aging
        - Delivery Aging
        - KPI Dashboard
        - SLA Compliance
        """
        logger.debug(f"📊 Executing KPI Service: intent={intent}, entity={entity}")
        
        try:
            if intent == "pending_pgi":
                if entity:
                    kpi = self.kpi.get_pending_pgi(entity)
                    return f"⏳ *PGI Pending for {entity}:* {kpi.get('pending_pgi', 0)}"
                kpi = self.kpi.get_pending_pgi()
                return f"⏳ *Total PGI Pending:* {kpi.get('pending_pgi', 0)}"
            
            if intent == "pending_pod":
                if entity:
                    kpi = self.kpi.get_pending_pod(entity)
                    return f"📎 *POD Pending for {entity}:* {kpi.get('pending_pod', 0)}"
                kpi = self.kpi.get_pending_pod()
                return f"📎 *Total POD Pending:* {kpi.get('pending_pod', 0)}"
            
            if intent == "pgi_aging":
                kpi = self.kpi.get_pgi_aging(entity)
                if entity:
                    return f"⏱️ *PGI Aging for {entity}:* {kpi.get('avg_aging', 0):.1f} days"
                return f"⏱️ *Average PGI Aging:* {kpi.get('avg_aging', 0):.1f} days"
            
            if intent == "pod_aging":
                kpi = self.kpi.get_pod_aging(entity)
                if entity:
                    return f"⏱️ *POD Aging for {entity}:* {kpi.get('avg_aging', 0):.1f} days"
                return f"⏱️ *Average POD Aging:* {kpi.get('avg_aging', 0):.1f} days"
            
            if intent == "kpi_dashboard":
                kpi = self.kpi.get_kpi_dashboard()
                return self._format_kpi_dashboard(kpi)
            
            if intent == "help":
                return self._get_help_message()
            
            return self._get_help_message()
            
        except Exception as e:
            logger.error(f"KPI service failed for {intent}: {e}")
            return self._get_service_error_response(intent, entity, "kpi", e)
    
    def _execute_groq_service(self, question: str, context: Dict, req_id: str) -> str:
        """Execute Groq Service for AI-generated responses."""
        logger.debug(f"🤖 Executing Groq Service: {question[:50]}")
        
        try:
            if hasattr(self.groq, 'is_available') and self.groq.is_available:
                response = self.groq.chat(question, context)
                self.metrics["groq_uses"] += 1
                return response
            return "⚠️ AI service is not available. Please try again later."
        except Exception as e:
            logger.error(f"Groq service failed: {e}")
            return "⚠️ AI service is temporarily unavailable. Please try again later."
    
    # ==========================================================
    # GROQ ENRICHMENT (Executive/Root Cause Only)
    # ==========================================================
    
    def _enrich_with_groq(self, response: str, intent: str, question: str, context: Dict) -> str:
        """
        Enrich analytics with Groq insights.
        
        ARCHITECTURE RULE:
        Groq ONLY for Executive Insights and Root Cause Analysis.
        Groq NEVER replaces analytics - only enriches.
        """
        # Only enrich executive and root cause
        if intent not in ["executive_insight", "root_cause"]:
            return response
        
        # Skip if no meaningful analytics
        if len(response) < 50:
            return response
        
        # Skip if Groq not available
        if not hasattr(self.groq, 'is_available') or not self.groq.is_available:
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
            logger.warning(f"Groq enrichment failed: {e}")
        
        return response
    
    # ==========================================================
    # CONTEXT MANAGEMENT
    # ==========================================================
    
    def _load_context(self, phone_number: Optional[str]) -> Optional[ConversationContext]:
        """Load or create conversation context."""
        if not phone_number:
            return None
        
        if phone_number not in self.conversation_cache:
            self.conversation_cache[phone_number] = ConversationContext(phone_number)
        
        context = self.conversation_cache[phone_number]
        
        # Reset context if expired
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
        """Update conversation context."""
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
        """Get cached response if available."""
        cache_key = self._generate_cache_key(question, phone_number)
        return self.response_cache.get(cache_key)
    
    def _cache_response(self, question: str, phone_number: Optional[str], response: str):
        """Cache response with TTL."""
        cache_key = self._generate_cache_key(question, phone_number)
        self.response_cache[cache_key] = response
    
    def _generate_cache_key(self, question: str, phone_number: Optional[str]) -> str:
        """Generate cache key from question and phone number."""
        key = question.lower().strip()
        if phone_number:
            key = f"{phone_number}:{key}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def clear_caches(self):
        """Clear all caches (call after metadata refresh)."""
        self.response_cache.clear()
        self.conversation_cache.clear()
        logger.info("🗑️ All caches cleared")
        return {"status": "cleared", "version": "12.0"}
    
    # ==========================================================
    # FORMATTERS - Response Formatting
    # ==========================================================
    
    def _format_dealer_dashboard(self, data: Dict, dealer_name: str) -> str:
        """Format dealer dashboard response."""
        if not data:
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
            f"   ⚠️ Risk Status: {performance.get('risk_status', 'low').upper()}",
        ]
        return "\n".join(lines)
    
    def _format_dealer_revenue(self, data: Dict, dealer_name: str) -> str:
        """Format dealer revenue response."""
        if not data:
            return f"❌ No revenue data for {dealer_name}"
        return (
            f"💰 *Revenue for {dealer_name}*\n\n"
            f"• Total Revenue: PKR {data.get('total_revenue', 0):,.0f}\n"
            f"• Number of DNs: {data.get('count', 0)}\n"
            f"• Average per DN: PKR {data.get('avg_revenue', 0):,.0f}"
        )
    
    def _format_dealer_units(self, data: Dict, dealer_name: str) -> str:
        """Format dealer units response."""
        if not data:
            return f"❌ No units data for {dealer_name}"
        return (
            f"📦 *Units for {dealer_name}*\n\n"
            f"• Total Units: {data.get('total_units', 0):,}\n"
            f"• Number of DNs: {data.get('count', 0)}\n"
            f"• Average per DN: {data.get('avg_units', 0):.1f}"
        )
    
    def _format_dealer_performance(self, data: Dict, dealer_name: str) -> str:
        """Format dealer performance response."""
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
        """Format dealer aging response."""
        if not data:
            return f"❌ No aging data for {dealer_name}"
        return (
            f"⏱️ *Aging for {dealer_name}*\n\n"
            f"• Average Aging: {data.get('avg_aging', 0):.1f} days\n"
            f"• Maximum Aging: {data.get('max_aging', 0)} days\n"
            f"• DNs with Aging: {data.get('count', 0)}"
        )
    
    def _format_dealer_ranking(self, data: Dict) -> str:
        """Format dealer ranking response."""
        dealers = data.get("dealers", [])
        if not dealers:
            return "📊 No dealers found."
        
        lines = ["🏆 *Top Dealers*", ""]
        for i, dealer in enumerate(dealers[:10], 1):
            revenue = dealer.get('revenue', 0)
            pod_rate = dealer.get('pod_rate', 0)
            lines.append(
                f"{i}. {dealer.get('name', 'N/A')}\n"
                f"   Revenue: PKR {revenue:,.0f} | POD Rate: {pod_rate:.1f}%"
            )
        return "\n".join(lines)
    
    def _format_warehouse_dashboard(self, data: Dict, warehouse_name: str) -> str:
        """Format warehouse dashboard response."""
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
    
    def _format_city_dashboard(self, data: Dict, city_name: str) -> str:
        """Format city dashboard response."""
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
    
    def _format_city_ranking(self, data: Dict) -> str:
        """Format city ranking response."""
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
    
    def _format_dn_details(self, data: Dict) -> str:
        """Format DN details response."""
        if not data or not data.get("found"):
            return "❌ DN not found."
        
        record = data.get("record", {})
        validation = data.get("validation", {})
        durations = validation.get("durations", {})
        status = data.get("status", "unknown")
        
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
        ]
        
        # Time Metrics
        processing = durations.get('processing_time_days')
        delivery = durations.get('delivery_time_days')
        cycle = durations.get('total_cycle_days')
        
        lines.append("")
        lines.append("⏱️ *Time Metrics:*")
        
        if processing is not None:
            lines.append(f"   • Processing Time: {processing} days")
        else:
            lines.append("   • Processing Time: N/A")
        
        if delivery is not None:
            lines.append(f"   • Delivery Time: {delivery} days")
        else:
            lines.append("   • Delivery Time: N/A")
        
        if cycle is not None:
            lines.append(f"   • Total Cycle Time: {cycle} days")
        else:
            lines.append("   • Total Cycle Time: N/A")
        
        # Data Quality
        is_valid = validation.get('is_valid', False)
        issues = validation.get('issues', [])
        
        if is_valid and not issues:
            lines.append("")
            lines.append("✅ *Data Quality: VALID*")
        elif issues:
            lines.append("")
            lines.append("⚠️ *Data Quality Issues Detected:*")
            for issue in issues:
                lines.append(f"   • {issue}")
        
        # Status
        status_map = {
            "pending_pgi": "⏳ Pending PGI",
            "pending_pod": "🚚 In Transit (POD Pending)",
            "delivered": "✅ Delivered",
            "unknown": "❓ Status Unknown"
        }
        lines.append("")
        lines.append(f"📊 *Status:* {status_map.get(status, '❓ Unknown')}")
        
        return "\n".join(lines)
    
    def _format_executive_insights(self, data: Dict) -> str:
        """Format executive insights response."""
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
        """Format root cause analysis response."""
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
        """Format control tower response."""
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
            lines.append(
                f"🔴 {alert.get('type', 'Alert')}: "
                f"{alert.get('dealer', 'N/A')} - "
                f"{alert.get('description', '')} ({alert.get('days', 0)} days)"
            )
        
        return "\n".join(lines)
    
    def _format_trend_analysis(self, data: Dict) -> str:
        """Format trend analysis response."""
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
    
    def _format_kpi_dashboard(self, data: Dict) -> str:
        """Format KPI dashboard response."""
        if not data:
            return "📊 No KPI data available."
        
        lines = [
            "📊 *KPI Dashboard*",
            "",
            f"📦 *Pending PGI:* {data.get('pending_pgi', 0)}",
            f"📎 *Pending POD:* {data.get('pending_pod', 0)}",
            f"⏱️ *Avg PGI Aging:* {data.get('avg_pgi_aging', 0):.1f} days",
            f"⏱️ *Avg POD Aging:* {data.get('avg_pod_aging', 0):.1f} days",
            f"📈 *PGI Rate:* {data.get('pgi_rate', 0):.1f}%",
            f"📈 *POD Rate:* {data.get('pod_rate', 0):.1f}%",
            f"✅ *SLA Compliance:* {data.get('sla_compliance', 0):.1f}%",
        ]
        return "\n".join(lines)
    
    def _get_help_message(self) -> str:
        """Get help message."""
        return """📋 *AI Logistics Assistant - Help*

*🔍 DN Tracking:* Send any 8-12 digit DN number

*🏪 Dealer Queries:*
   • "Dealer name" (e.g., "Dubai Electronics")
   • "Dealer revenue" or "Dealer units"
   • "Dealer performance" or "Dealer aging"

*🏙️ City Queries:*
   • "City name" (e.g., "Haripur")
   • "Which city has highest sales"

*🏭 Warehouse Queries:*
   • "Warehouse name" (e.g., "Rawalpindi")

*📊 Analytics:*
   • "Top dealers" or "Executive insights"
   • "Key issues" or "Critical alerts"

*🤖 General AI:* Any non-logistics question

*What would you like to know?* 🤖"""
    
    # ==========================================================
    # ERROR RESPONSES
    # ==========================================================
    
    def _get_error_response(self, question: str, error: Exception, error_id: str, request_id: str) -> str:
        """Structured error response."""
        return (
            f"⚠️ *Unable to process your request*\n\n"
            f"• Error Reference: `{error_id}`\n"
            f"• Request ID: `{request_id}`\n"
            f"• Error: {str(error)[:100]}\n\n"
            f"Please try again or contact support with the reference ID."
        )
    
    def _get_service_error_response(self, intent: str, entity: Optional[str], service: str, error: Exception) -> str:
        """Structured service error response."""
        error_id = str(uuid.uuid4())[:8]
        logger.error(f"Service error [{error_id}]: {service}.{intent} - {error}")
        
        return (
            f"⚠️ *Unable to retrieve data*\n\n"
            f"• Service: {service}\n"
            f"• Intent: {intent}\n"
            f"• Entity: {entity or 'N/A'}\n"
            f"• Error Reference: `{error_id}`\n\n"
            f"Please try again or contact support."
        )
    
    # ==========================================================
    # METRICS & ADMIN
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics."""
        total = self.metrics["total_requests"]
        cache_hits = self.metrics["cache_hits"]
        cache_misses = self.metrics["cache_misses"]
        
        return {
            "total_requests": total,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_hit_rate": cache_hits / max(1, cache_hits + cache_misses),
            "dn_lookups": self.metrics["dn_lookups"],
            "dealer_queries": self.metrics["dealer_queries"],
            "city_queries": self.metrics["city_queries"],
            "warehouse_queries": self.metrics["warehouse_queries"],
            "kpi_queries": self.metrics["kpi_queries"],
            "executive_queries": self.metrics["executive_queries"],
            "groq_uses": self.metrics["groq_uses"],
            "analytics_success": self.metrics["analytics_success"],
            "analytics_failure": self.metrics["analytics_failure"],
            "analytics_success_rate": self.metrics["analytics_success"] / max(1, self.metrics["analytics_success"] + self.metrics["analytics_failure"]),
            "errors": self.metrics["errors"],
            "conversation_count": len(self.conversation_cache),
            "cache_size": len(self.response_cache),
            "version": "12.0"
        }
    
    def get_routing_debug(self, question: str) -> Dict[str, Any]:
        """Get routing debug information."""
        context = {}
        routing = self.query_service.process_query(question, context)
        
        return {
            "question": question,
            "routing_decision": routing,
            "timestamp": time.time()
        }


# ==========================================================
# SINGLETON
# ==========================================================

_orchestrator = None

def get_orchestrator() -> AIOrchestrator:
    """Get singleton orchestrator instance."""
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
    """Main entry point for WhatsApp queries."""
    orchestrator = get_orchestrator()
    return orchestrator.process_whatsapp_query(
        question=question,
        session_factory=session_factory,
        phone_number=phone_number,
        user_id=user_id,
        request_id=request_id
    )


def get_ai_service_metrics() -> Dict[str, Any]:
    """Get service metrics."""
    orchestrator = get_orchestrator()
    return orchestrator.get_metrics()


def clear_ai_cache():
    """Clear AI response cache."""
    orchestrator = get_orchestrator()
    return orchestrator.clear_caches()


def get_routing_debug(question: str) -> Dict[str, Any]:
    """Get routing debug information."""
    orchestrator = get_orchestrator()
    return orchestrator.get_routing_debug(question)


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("AI Provider Service v12.0 - Enterprise Architecture v4.0")
logger.info("=" * 70)
logger.info("")
logger.info("   ROLE: Master Orchestrator")
logger.info("   STATUS: ✅ ENTERPRISE READY")
logger.info("")
logger.info("   ARCHITECTURE COMPLIANCE:")
logger.info("   ✅ Orchestrates - Does NOT execute business logic")
logger.info("   ✅ Routes - Does NOT make routing decisions")
logger.info("   ✅ Coordinates - Does NOT access database")
logger.info("   ✅ Calls Services - Does NOT calculate KPIs")
logger.info("")
logger.info("   INTEGRATION POINTS:")
logger.info("   ✅ ai_query_service.py - Routing Engine")
logger.info("   ✅ analytics_service.py - Business Intelligence")
logger.info("   ✅ kpi_service.py - KPI Calculations")
logger.info("   ✅ groq_service.py - AI Integration")
logger.info("   ✅ whatsapp_service.py - Meta Communication")
logger.info("=" * 70)
