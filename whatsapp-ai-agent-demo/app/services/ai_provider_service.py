# ==========================================================
# FILE: app/services/ai_provider_service.py (v8.2 - PRODUCTION FIX)
# ==========================================================
# PURPOSE: Master Orchestrator - Coordinates Complete Request Lifecycle
# FIXES APPLIED:
# 1. Groq now ENRICHES analytics, never replaces it
# 2. DN formatting uses correct processing/delivery/cycle times
# 3. Data quality status displayed with proper validation
# 4. Context intelligence reuses last_dealer
# 5. City dashboard support added
# 6. Comparison intelligence for dealers, warehouses, cities
# 7. Cache invalidation after metadata refresh
# 8. Detailed logging with structured data
# 9. Root cause uses analytics + Groq summary
# 10. WhatsApp DN format with all metrics
# ==========================================================

import time
import uuid
import hashlib
import asyncio
from typing import Optional, Callable, Any, Dict, List
from cachetools import TTLCache
from loguru import logger
from sqlalchemy.orm import Session

from app.config import config
from app.database import SessionLocal

from app.services.ai_query_service import AIQueryService, QueryPlan, get_ai_query_service
from app.services.analytics_service import AnalyticsService, get_analytics_service
from app.services.kpi_service import KPIService, get_kpi_service
from app.services.groq_service import GroqService, get_groq_service
from app.schemas.schema_service import get_schema_service


# ==========================================================
# CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800


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
    """MASTER ORCHESTRATOR - Coordinates Complete Request Lifecycle"""
    
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
            "analytics_uses": 0
        }
        
        logger.info("AI Orchestrator v8.2 initialized")
    
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
            context = self._load_context(phone_number)
            context_dict = context.to_dict() if context else {}
            
            # FIX: Check if this is a follow-up question using context
            if context and context.last_dealer and self._is_follow_up(question, context):
                enhanced_question = self._enhance_with_context(question, context)
                logger.info(f"🔗 Context-enhanced question: '{question}' → '{enhanced_question}'")
                question = enhanced_question
            
            cache_key = self._generate_cache_key(question, phone_number)
            cached_response = self.response_cache.get(cache_key)
            if cached_response:
                self.metrics["cache_hits"] += 1
                logger.info(f"⚡ Cache hit for: {question[:50]}")
                return cached_response
            
            query_plan = self._get_query_plan(question, context_dict)
            
            # Log query plan
            logger.info(
                f"🎯 Query Plan: intent={query_plan.intent}, "
                f"entity={query_plan.entity}, "
                f"entity_type={query_plan.entity_type}, "
                f"service={query_plan.service}"
            )
            
            response = self._execute_service(query_plan, context_dict, req_id)
            
            # FIX: Groq enriches, never replaces
            response = self._enrich_with_groq(response, query_plan, question, context_dict)
            
            self._update_context(phone_number, query_plan, req_id)
            self.response_cache[cache_key] = response
            
            duration_ms = int((time.time() - start_time) * 1000)
            logger.bind(request_id=req_id).info(f"✅ Orchestrator done: {duration_ms}ms")
            
            return response
            
        except Exception as e:
            logger.exception(f"[{req_id}] Orchestrator fatal error: {e}")
            return self._get_fallback_response(question)
    
    # ==========================================================
    # CONTEXT INTELLIGENCE
    # ==========================================================
    
    def _is_follow_up(self, question: str, context: ConversationContext) -> bool:
        """Check if question is a follow-up using context."""
        if not context.last_dealer:
            return False
        
        question_lower = question.lower().strip()
        
        # Short follow-up questions
        follow_up_patterns = [
            "revenue", "units", "performance", "aging", "pending",
            "pod", "pgi", "dashboard", "summary", "details",
            "show", "view", "get", "tell"
        ]
        
        # If question is short and contains a follow-up pattern
        if len(question_lower.split()) <= 3:
            for pattern in follow_up_patterns:
                if pattern in question_lower:
                    return True
        
        return False
    
    def _enhance_with_context(self, question: str, context: ConversationContext) -> str:
        """Enhance question with context."""
        if context.last_dealer:
            # Check if question already has a dealer name
            if context.last_dealer.lower() not in question.lower():
                return f"{question} {context.last_dealer}"
        
        if context.last_warehouse and "warehouse" in question.lower():
            if context.last_warehouse.lower() not in question.lower():
                return f"{question} {context.last_warehouse}"
        
        if context.last_city and "city" in question.lower():
            if context.last_city.lower() not in question.lower():
                return f"{question} {context.last_city}"
        
        return question
    
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
            return self._get_fallback_response(query_plan.original_message)
    
    def _execute_analytics(self, intent: str, query_plan: QueryPlan) -> str:
        entity = query_plan.entity
        entity2 = query_plan.entity2  # For comparisons
        
        # ==========================================================
        # DEALER ANALYTICS
        # ==========================================================
        
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
        
        # ==========================================================
        # WAREHOUSE ANALYTICS
        # ==========================================================
        
        if intent == "warehouse_dashboard" and entity:
            result = self.analytics.get_warehouse_dashboard(entity)
            return self._format_warehouse_dashboard(result, entity)
        
        if intent == "warehouse_performance" and entity:
            result = self.analytics.get_warehouse_dashboard(entity)
            return self._format_warehouse_performance(result, entity)
        
        # ==========================================================
        # CITY ANALYTICS (NEW)
        # ==========================================================
        
        if intent == "city_dashboard" and entity:
            result = self.analytics.get_city_dashboard(entity)
            return self._format_city_dashboard(result, entity)
        
        if intent == "city_performance" and entity:
            result = self.analytics.get_city_dashboard(entity)
            return self._format_city_performance(result, entity)
        
        # ==========================================================
        # DN ANALYTICS
        # ==========================================================
        
        if intent == "dn_lookup" and entity:
            result = self.analytics.get_dn_analytics(entity)
            return self._format_dn_details(result)
        
        # ==========================================================
        # COMPARISON ANALYTICS (NEW)
        # ==========================================================
        
        if intent == "compare_dealers" and entity and entity2:
            result = self.analytics.compare_dealers(entity, entity2)
            return self._format_dealer_comparison(result, entity, entity2)
        
        if intent == "compare_warehouses" and entity and entity2:
            result = self.analytics.compare_warehouses(entity, entity2)
            return self._format_warehouse_comparison(result, entity, entity2)
        
        if intent == "compare_cities" and entity and entity2:
            result = self.analytics.compare_cities(entity, entity2)
            return self._format_city_comparison(result, entity, entity2)
        
        # ==========================================================
        # RANKING ANALYTICS
        # ==========================================================
        
        if intent == "top_dealers":
            metric = query_plan.sort_by or "revenue"
            limit = query_plan.limit or 10
            results = self.analytics.get_dealer_ranking(limit=limit, top=True)
            return self._format_ranking(results, "Dealers", metric)
        
        if intent == "bottom_dealers":
            metric = query_plan.sort_by or "revenue"
            limit = query_plan.limit or 10
            results = self.analytics.get_dealer_ranking(limit=limit, top=False)
            return self._format_ranking(results, "Dealers", metric, top=False)
        
        # ==========================================================
        # EXECUTIVE & ROOT CAUSE ANALYTICS
        # ==========================================================
        
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
    
    def _execute_groq(self, query_plan: QueryPlan, context: Dict) -> str:
        if self.groq.is_available:
            response = self.groq.chat(query_plan.original_message, context)
            self.metrics["groq_uses"] += 1
            return response
        return self._get_fallback_response(query_plan.original_message)
    
    # ==========================================================
    # GROQ ENRICHMENT (FIXED: Never Replaces Analytics)
    # ==========================================================
    
    def _enrich_with_groq(self, response: str, query_plan: QueryPlan, question: str, context: Dict) -> str:
        """
        FIX: Groq enriches analytics, never replaces it.
        Only used for executive_insight and root_cause to add explanatory context.
        """
        if not self.groq.is_available:
            return response
        
        # Only enrich specific intents with analytics data
        if query_plan.intent in ["executive_insight", "root_cause"]:
            # Check if response already contains meaningful analytics
            if len(response) > 50:  # Has meaningful content
                try:
                    # Get a concise summary from Groq based on the analytics
                    enrichment_prompt = f"""
Based on this logistics analytics data:

{response[:500]}

Provide a brief, professional executive summary (2-3 sentences) that highlights the most critical insight and recommends one immediate action.

Keep it concise and actionable.
"""
                    groq_summary = self.groq.chat(enrichment_prompt, context)
                    
                    if groq_summary and len(groq_summary) > 10:
                        self.metrics["groq_uses"] += 1
                        # Combine analytics + Groq insight
                        return f"{response}\n\n💡 *AI Insight:*\n{groq_summary}"
                except Exception as e:
                    logger.warning(f"Groq enrichment failed: {e}")
        
        # For general AI, use Groq directly
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
    
    def _update_context(self, phone_number: Optional[str], query_plan: QueryPlan, req_id: str):
        if not phone_number:
            return
        context = self._load_context(phone_number)
        if not context:
            return
        
        context.last_intent = query_plan.intent
        context.last_question = query_plan.original_message
        
        if query_plan.entity_type == "dealer":
            context.last_dealer = query_plan.entity
        elif query_plan.entity_type == "warehouse":
            context.last_warehouse = query_plan.entity
        elif query_plan.entity_type == "city":
            context.last_city = query_plan.entity
        elif query_plan.entity_type == "dn":
            context.last_dn = query_plan.entity
        
        context.message_count += 1
        context.last_updated = time.time()
    
    # ==========================================================
    # CACHE MANAGEMENT
    # ==========================================================
    
    def _generate_cache_key(self, question: str, phone_number: Optional[str]) -> str:
        key = question.lower().strip()
        if phone_number:
            key = f"{phone_number}:{key}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def clear_caches(self):
        """Clear response cache (call after metadata refresh or Excel import)."""
        self.response_cache.clear()
        logger.info("🗑️ Response cache cleared")
        return {"status": "cleared", "version": "8.2"}
    
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
    # FORMATTERS - CITY (NEW)
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
            lines.append("🏆 *Top Dealers in {city_name}:*")
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
    
    # ==========================================================
    # FORMATTERS - DN (FIXED with correct metrics)
    # ==========================================================
    
    def _format_dn_details(self, data: Dict) -> str:
        if not data or not data.get("found"):
            return "❌ DN not found."
        
        record = data.get("record", {})
        validation = data.get("validation", {})
        durations = validation.get("durations", {})
        status = data.get("status", "unknown")
        
        # FIX: Use correct field names from SchemaService
        processing_days = durations.get('processing_time_days')
        delivery_days = durations.get('delivery_time_days')
        cycle_days = durations.get('total_cycle_days')
        
        # Determine data quality status
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
        
        # Determine DN status display
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
        ]
        
        # FIX: Add all three time metrics with proper validation
        lines.append("")
        lines.append("⏱️ *Time Metrics:*")
        
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
        
        # FIX: Show data quality status
        lines.append("")
        lines.append(f"{quality_emoji} *Data Quality: {quality_status}*")
        
        # Show issues if any
        if issues:
            lines.append("")
            lines.append("⚠️ *Issues Detected:*")
            for issue in issues:
                lines.append(f"   • {issue}")
        
        # Show warnings if any
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
    # FORMATTERS - COMPARISON (NEW)
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
        
        # Add winner
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
    # FORMATTERS - RANKING
    # ==========================================================
    
    def _format_ranking(self, data: Dict, entity_type: str, metric: str, top: bool = True) -> str:
        dealers = data.get("dealers", [])
        if not dealers:
            return f"📊 No {entity_type} found."
        
        title = "🏆 Top" if top else "📉 Bottom"
        metric_label = metric.title()
        
        lines = [f"{title} {len(dealers)} {entity_type} by {metric_label}", ""]
        for i, item in enumerate(dealers[:10], 1):
            value = item.get(metric, 0)
            pod_rate = item.get('pod_rate', 0)
            lines.append(
                f"{i}. {item['name']}: "
                f"{f'PKR {value:,.0f}' if metric == 'revenue' else f'{value:,} units'}"
                f" | POD: {pod_rate:.1f}%"
            )
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
            return "🚨 Control Tower\n\n✅ No critical alerts at this time."
        
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
    # FORMATTERS - DELIVERY PERFORMANCE
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
    
    # ==========================================================
    # FORMATTERS - TREND
    # ==========================================================
    
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
    # HELPERS
    # ==========================================================
    
    def _get_help_message(self) -> str:
        return """📋 *AI Logistics Assistant - Help*

*🔍 DN Tracking:* Send any 8-12 digit DN number

*🏪 Dealer Queries:*
   • "Dealer name" (e.g., "Dubai Electronics")
   • "Dealer revenue" or "Dealer units"
   • "Dealer performance" or "Dealer aging"

*🏙️ City Queries:* "City name" (e.g., "Haripur")

*🏭 Warehouse Queries:* "Warehouse name" (e.g., "Rawalpindi")

*📊 Analytics:*
   • "Top dealers" or "Bottom dealers"
   • "Executive insights" or "Key issues"
   • "Root cause" or "Critical alerts"

*📈 Performance:* "Delivery performance" or "Trend analysis"

*🤖 General AI:* Any non-logistics question

*What would you like to know?* 🤖"""
    
    def _get_fallback_response(self, question: str) -> str:
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
            "conversation_count": len(self.conversation_cache),
            "cache_size": len(self.response_cache),
            "version": "8.2"
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


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 60)
logger.info("AI Provider Service v8.2 - Production Fixed")
logger.info("=" * 60)
logger.info("")
logger.info("   SERVICES:")
logger.info("   ✅ AIQueryService - Decision Engine")
logger.info("   ✅ AnalyticsService - Business Intelligence")
logger.info("   ✅ KPIService - KPI Calculations")
logger.info("   ✅ GroqService - AI Integration (Enrichment Only)")
logger.info("   ✅ SchemaService - Metadata Engine")
logger.info("")
logger.info("   FIXES APPLIED:")
logger.info("   ✅ Groq enriches analytics, never replaces")
logger.info("   ✅ Correct DN metrics (processing/delivery/cycle)")
logger.info("   ✅ Data quality status displayed")
logger.info("   ✅ Context intelligence with last_dealer")
logger.info("   ✅ City dashboard support")
logger.info("   ✅ Comparison support (dealers/warehouses/cities)")
logger.info("   ✅ Cache invalidation on metadata refresh")
logger.info("   ✅ Root cause uses analytics + Groq")
logger.info("   ✅ WhatsApp DN format with all metrics")
logger.info("")
logger.info("   STATUS: ✅ READY FOR PRODUCTION")
logger.info("=" * 60)
