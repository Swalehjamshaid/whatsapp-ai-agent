# ==========================================================
# FILE: app/services/ai_provider_service.py (v8.1 - MASTER ORCHESTRATOR)
# ==========================================================
# PURPOSE: Master Orchestrator - Coordinates Complete Request Lifecycle
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
        self.last_dn: Optional[str] = None
        self.message_count: int = 0
        self.created_at: float = time.time()
        self.last_updated: float = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_dn": self.last_dn,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number
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
            "groq_uses": 0
        }
        
        logger.info("AI Orchestrator v8.1 initialized")
    
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
        ).info(f"Orchestrator processing: {question[:100]}")
        
        try:
            context = self._load_context(phone_number)
            context_dict = context.to_dict() if context else {}
            
            cache_key = self._generate_cache_key(question, phone_number)
            cached_response = self.response_cache.get(cache_key)
            if cached_response:
                self.metrics["cache_hits"] += 1
                return cached_response
            
            query_plan = self._get_query_plan(question, context_dict)
            
            response = self._execute_service(query_plan, context_dict, req_id)
            response = self._apply_groq_governance(response, query_plan, question, context_dict)
            
            self._update_context(phone_number, query_plan, req_id)
            self.response_cache[cache_key] = response
            
            duration_ms = int((time.time() - start_time) * 1000)
            logger.bind(request_id=req_id).info(f"Orchestrator done: {duration_ms}ms")
            
            return response
            
        except Exception as e:
            logger.exception(f"[{req_id}] Orchestrator fatal error: {e}")
            return self._get_fallback_response(question)
    
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
        
        if intent == "dealer_dashboard" and entity:
            result = self.analytics.get_dealer_dashboard(entity)
            return self._format_dealer_dashboard(result)
        if intent == "dealer_revenue" and entity:
            result = self.analytics.get_dealer_performance(entity)
            return f"💰 *Revenue for {entity}:* PKR {result.get('revenue', 0):,.0f}"
        if intent == "dealer_units" and entity:
            result = self.analytics.get_dealer_performance(entity)
            return f"📦 *Units for {entity}:* {result.get('units', 0):,}"
        if intent == "dealer_performance" and entity:
            result = self.analytics.get_dealer_performance(entity)
            return self._format_dealer_performance(result)
        if intent == "warehouse_dashboard" and entity:
            result = self.analytics.get_warehouse_dashboard(entity)
            return self._format_warehouse_dashboard(result)
        if intent == "dn_lookup" and entity:
            result = self.analytics.logistics.get_dn_details(entity)
            return self._format_dn_details(result)
        if intent == "top_dealers":
            metric = query_plan.sort_by or "revenue"
            results = self.analytics.get_top_dealers(metric, query_plan.limit)
            return self._format_ranking(results, "Dealers", metric)
        if intent == "executive_insight":
            result = self.analytics.get_executive_dashboard()
            return self._format_executive_insights(result)
        if intent == "control_tower":
            result = self.analytics.get_control_tower()
            return self._format_control_tower(result)
        if intent == "help":
            return self._get_help_message()
        return self._get_help_message()
    
    def _execute_kpi(self, intent: str, query_plan: QueryPlan) -> str:
        entity = query_plan.entity
        if intent == "pending_pgi":
            kpi = self.kpi.get_pending_pgi(entity)
            return f"⏳ *PGI Pending for {entity}:* {kpi.get('pending_pgi', 0)}" if entity else f"⏳ *Total PGI Pending:* {kpi.get('pending_pgi', 0)}"
        if intent == "pending_pod":
            kpi = self.kpi.get_pending_pod(entity)
            return f"📎 *POD Pending for {entity}:* {kpi.get('pending_pod', 0)}" if entity else f"📎 *Total POD Pending:* {kpi.get('pending_pod', 0)}"
        return self._get_help_message()
    
    def _execute_groq(self, query_plan: QueryPlan, context: Dict) -> str:
        if self.groq.is_available:
            response = self.groq.chat(query_plan.original_message, context)
            self.metrics["groq_uses"] += 1
            return response
        return self._get_fallback_response(query_plan.original_message)
    
    # ==========================================================
    # GROQ GOVERNANCE
    # ==========================================================
    
    def _apply_groq_governance(self, response: str, query_plan: QueryPlan, question: str, context: Dict) -> str:
        if not self.groq.is_available:
            return response
        if query_plan.intent in ["executive_insight", "root_cause", "general_ai"]:
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
        if query_plan.entity_type == "dealer":
            context.last_dealer = query_plan.entity
        elif query_plan.entity_type == "warehouse":
            context.last_warehouse = query_plan.entity
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
    
    # ==========================================================
    # FORMATTERS
    # ==========================================================
    
    def _format_dealer_dashboard(self, data: Dict) -> str:
        if "error" in data:
            return f"❌ {data['error']}"
        lines = [f"🏪 *Dealer: {data.get('dealer_name', 'Unknown')}*", ""]
        lines.append(f"📄 *Total DNs:* {data.get('total_dns', 0):,}")
        lines.append(f"📦 *Total Units:* {data.get('total_units', 0):,}")
        lines.append(f"💰 *Revenue:* PKR {data.get('total_revenue', 0):,.0f}")
        lines.append("")
        lines.append("📊 *Delivery Status:*")
        lines.append(f"   ✅ Delivered: {data.get('delivered_units', 0)}")
        lines.append(f"   🚚 In Transit: {data.get('transit_units', 0)}")
        lines.append(f"   ⏳ Pending: {data.get('pending_delivery', 0)}")
        lines.append("")
        lines.append(f"📎 *POD Status:* {data.get('pod_completed', 0)} completed | {data.get('pending_pod', 0)} pending")
        lines.append("")
        lines.append(f"📈 *Performance:*")
        lines.append(f"   📦 Delivery Rate: {data.get('delivery_rate', 0)}%")
        lines.append(f"   📎 POD Rate: {data.get('pod_rate', 0)}%")
        lines.append(f"   ⏰ Avg Delivery: {data.get('avg_delivery_aging', 0)} days")
        lines.append(f"   📋 Avg POD: {data.get('avg_pod_aging', 0)} days")
        if data.get('oldest_pending_dn'):
            lines.append("")
            lines.append(f"⚠️ *Oldest Pending:* DN {data['oldest_pending_dn']} ({data['oldest_pending_days']} days)")
        return "\n".join(lines)
    
    def _format_dealer_performance(self, data: Dict) -> str:
        if "error" in data:
            return f"❌ {data['error']}"
        emoji = data.get('risk_emoji', '⚪')
        lines = [f"📊 *Performance Dashboard: {data.get('dealer_name', 'Unknown')}*", ""]
        lines.append(f"💰 *Revenue:* PKR {data.get('revenue', 0):,.0f}")
        lines.append(f"📦 *Units:* {data.get('units', 0):,}")
        lines.append("")
        lines.append(f"🚚 *Delivery Rate:* {data.get('delivery_rate', 0)}%")
        lines.append(f"📎 *POD Rate:* {data.get('pod_rate', 0)}%")
        lines.append(f"⏳ *Pending Delivery:* {data.get('pending_pgi', 0)}")
        lines.append(f"🚚 *Pending POD:* {data.get('pending_pod', 0)}")
        lines.append("")
        lines.append(f"{emoji} *Risk Status:* {data.get('risk_status', 'Unknown').title()}")
        return "\n".join(lines)
    
    def _format_warehouse_dashboard(self, data: Dict) -> str:
        if "error" in data:
            return f"❌ {data['error']}"
        lines = [f"🏭 *Warehouse: {data.get('warehouse_name', 'Unknown').title()}*", ""]
        lines.append(f"📄 *Total DNs:* {data.get('total_dns', 0):,}")
        lines.append(f"📦 *Total Units:* {data.get('total_units', 0):,}")
        lines.append(f"💰 *Revenue:* PKR {data.get('total_revenue', 0):,.0f}")
        lines.append("")
        lines.append(f"✅ *PGI Completed:* {data.get('pgi_completed', 0)}")
        lines.append(f"📎 *POD Completed:* {data.get('pod_completed', 0)}")
        lines.append(f"⏳ *Pending Delivery:* {data.get('pending_delivery', 0)}")
        lines.append(f"📎 *Pending POD:* {data.get('pending_pod', 0)}")
        return "\n".join(lines)
    
    def _format_dn_details(self, data: Optional[Dict]) -> str:
        if not data:
            return "❌ DN not found."
        lines = [f"📄 *DN: {data.get('dn_number', 'Unknown')}*", ""]
        lines.append(f"🏪 *Dealer:* {data.get('dealer', 'N/A')}")
        lines.append(f"🏭 *Warehouse:* {data.get('warehouse', 'N/A')}")
        lines.append(f"🌆 *City:* {data.get('city', 'N/A')}")
        lines.append("")
        lines.append(f"📦 *Units:* {data.get('units', 0):,}")
        lines.append(f"💰 *Amount:* PKR {data.get('amount', 0):,.0f}")
        lines.append("")
        if data.get('dn_date'):
            lines.append(f"📅 *DN Date:* {data['dn_date'].strftime('%Y-%m-%d')}")
        if data.get('pgi_date'):
            lines.append(f"🚚 *PGI Date:* {data['pgi_date'].strftime('%Y-%m-%d')}")
        if data.get('pod_date'):
            lines.append(f"📎 *POD Date:* {data['pod_date'].strftime('%Y-%m-%d')}")
        lines.append("")
        if data.get('delivery_aging') is not None:
            emoji = "✅" if data['delivery_aging'] <= 7 else "⚠️" if data['delivery_aging'] <= 15 else "🔴"
            lines.append(f"{emoji} *Delivery Time:* {data['delivery_aging']} days")
        if data.get('pod_aging') is not None:
            emoji = "✅" if data['pod_aging'] <= 7 else "⚠️" if data['pod_aging'] <= 15 else "🔴"
            lines.append(f"{emoji} *POD Time:* {data['pod_aging']} days")
        lines.append("")
        lines.append(f"📊 *Status:* {data.get('status_display', data.get('status', 'Unknown'))}")
        return "\n".join(lines)
    
    def _format_ranking(self, data: List[Dict], entity_type: str, metric: str) -> str:
        if not data:
            return f"📊 No {entity_type} found."
        metric_label = metric.title()
        lines = [f"🏆 *Top {len(data)} {entity_type} by {metric_label}*", ""]
        for i, item in enumerate(data, 1):
            value = item.get(metric, 0)
            lines.append(f"{i}. {item['name']}: {f'PKR {value:,.0f}' if metric == 'revenue' else f'{value:,} units'}")
        return "\n".join(lines)
    
    def _format_executive_insights(self, data: Dict) -> str:
        if not data:
            return "📊 No executive insights available."
        lines = ["🚨 *Executive Insights*", ""]
        lines.append(f"📊 *Pending PGI:* {data.get('pending_pgi', 0)}")
        lines.append(f"📎 *Pending POD:* {data.get('pending_pod', 0)}")
        lines.append(f"⏰ *Avg Delivery Aging:* {data.get('avg_delivery_aging', 0)} days")
        lines.append("")
        if data.get('worst_warehouse'):
            lines.append(f"🏭 *Critical Warehouse:* {data['worst_warehouse']}")
        if data.get('oldest_dn'):
            lines.append(f"🔴 *Oldest Pending:* DN {data['oldest_dn']} ({data['oldest_aging']} days)")
        recommendations = data.get('recommendations', [])
        if recommendations:
            lines.append("")
            lines.append("💡 *Recommendations:*")
            for rec in recommendations:
                lines.append(f"   • {rec}")
        return "\n".join(lines)
    
    def _format_control_tower(self, data: Dict) -> str:
        critical = data.get('critical_deliveries', [])
        if not critical:
            return "✅ No critical deliveries found."
        lines = ["🚨 *Control Tower - Critical Alerts*", ""]
        lines.append(f"🔴 *{len(critical)} critical deliveries*")
        for item in critical:
            lines.append(f"   • DN {item['dn']}: {item['dealer']} - {item['aging']} days ({item['warehouse']})")
        return "\n".join(lines)
    
    # ==========================================================
    # HELPERS
    # ==========================================================
    
    def _get_help_message(self) -> str:
        return """📋 *AI Logistics Assistant - Help*

*DN Tracking:* Send any 10+ digit DN number
*Dealer:* "Show dealer ABC Traders" or "ABC Traders revenue"
*Warehouse:* "Lahore warehouse summary"
*Pending:* "Pending deliveries" or "Pending POD"
*Performance:* "ABC Traders performance"
*Rankings:* "Top 10 dealers by revenue"
*Executive:* "Key issues" or "Critical alerts"

Need help? Just ask! 🤖"""
    
    def _get_fallback_response(self, question: str) -> str:
        return f"I understand you're asking: {question[:100]}\n\nType 'Help' for available commands."
    
    def get_metrics(self) -> Dict[str, Any]:
        return {
            "total_requests": self.metrics["total_requests"],
            "cache_hits": self.metrics["cache_hits"],
            "cache_hit_rate": self.metrics["cache_hits"] / max(1, self.metrics["total_requests"]),
            "service_successes": self.metrics["service_successes"],
            "service_failures": self.metrics["service_failures"],
            "service_success_rate": self.metrics["service_successes"] / max(1, self.metrics["service_successes"] + self.metrics["service_failures"]),
            "groq_uses": self.metrics["groq_uses"],
            "conversation_count": len(self.conversation_cache),
            "cache_size": len(self.response_cache),
            "version": "8.1"
        }
    
    def clear_caches(self):
        self.response_cache.clear()
        self.conversation_cache.clear()
        return {"status": "cleared", "version": "8.1"}


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
    orchestrator = get_orchestrator()
    return orchestrator.clear_caches()


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 60)
logger.info("AI Provider Service v8.1 - Master Orchestrator")
logger.info("=" * 60)
logger.info("")
logger.info("   SERVICES:")
logger.info("   ✅ AIQueryService - Decision Engine")
logger.info("   ✅ AnalyticsService - Business Intelligence")
logger.info("   ✅ KPIService - KPI Calculations")
logger.info("   ✅ GroqService - AI Integration")
logger.info("   ✅ SchemaService - Metadata Engine")
logger.info("")
logger.info("   STATUS: ✅ READY FOR PRODUCTION")
logger.info("=" * 60)
