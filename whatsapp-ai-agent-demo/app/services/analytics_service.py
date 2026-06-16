# ==========================================================
# FILE: app/services/ai_provider_service.py (v12.1 - CRITICAL FIXES)
# ==========================================================
# FIXES:
# 1. DN lookup - handle missing dates gracefully
# 2. Dealer resolution - better logging and fallback
# 3. Executive insights - show meaningful message when no data
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
    from app.schemas.schema_service import get_schema_service, DN_PATTERN
    return get_schema_service(), DN_PATTERN

def _get_whatsapp_service():
    from app.services.whatsapp_service import get_whatsapp_service
    return get_whatsapp_service()


# ==========================================================
# CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800
DN_PATTERN = re.compile(r'\b(\d{8,12})\b')


# ==========================================================
# GROQ PROTECTION - COMPREHENSIVE BLOCK LIST
# ==========================================================

GROQ_BLOCKED_PATTERNS = {
    'dealer', 'customer', 'sold to', 'buyer', 'traders', 'electronics',
    'enterprises', 'industries', 'corporation', 'group', 'sons',
    'delivery', 'pgi', 'pod', 'dn', 'warehouse', 'ship to',
    'dispatch', 'transit', 'delivered', 'pending', 'order',
    'revenue', 'sales', 'units', 'quantity', 'aging', 'performance',
    'kpi', 'rate', 'completion', 'efficiency', 'metrics', 'target',
    'root cause', 'improvement', 'bottleneck', 'insight', 'executive',
    'critical', 'urgent', 'priority', 'alert', 'issue', 'problem',
    'key issue', 'bring improvement', 'why delayed', 'what is the key',
    'top', 'bottom', 'best', 'worst', 'compare', 'vs', 'versus',
    'highest', 'lowest', 'ranking', 'rank',
    'today', 'yesterday', 'week', 'month', 'year', 'trend', 'historical',
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
# MASTER ORCHESTRATOR
# ==========================================================

class AIOrchestrator:
    """
    MASTER ORCHESTRATOR - FINAL AUTHORITY & GOVERNANCE LAYER
    """
    
    def __init__(self):
        self._query_service = None
        self._analytics = None
        self._kpi = None
        self._groq = None
        self._schema = None
        self._whatsapp = None
        self._dn_pattern = DN_PATTERN
        
        self.response_cache = TTLCache(maxsize=500, ttl=CACHE_TTL_SECONDS)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        
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
        
        logger.info("=" * 70)
        logger.info("AI Orchestrator v12.1 - Critical Fixes")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   FIXES APPLIED:")
        logger.info("   ✅ DN lookup - handles missing dates gracefully")
        logger.info("   ✅ Dealer resolution - better logging")
        logger.info("   ✅ Executive insights - meaningful messages")
        logger.info("   ✅ Data quality - proper validation")
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
            self._schema, _ = _get_schema_service()
        return self._schema
    
    @property
    def whatsapp(self):
        if self._whatsapp is None:
            self._whatsapp = _get_whatsapp_service()
        return self._whatsapp
    
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
    # DN LOOKUP - FIXED
    # ==========================================================
    
    def _is_dn_query(self, question: str) -> bool:
        """Check if query is a DN number (8-12 digits)."""
        cleaned = question.strip()
        return bool(self._dn_pattern.fullmatch(cleaned.replace(" ", "")))
    
    def _execute_dn_lookup(self, question: str) -> str:
        """Execute DN lookup with enhanced error handling."""
        dn_number = question.strip()
        
        try:
            result = self.analytics.get_dn_analytics(dn_number)
            self.metrics["analytics_success"] += 1
            
            if not result.get("found", False):
                return f"❌ DN {dn_number} not found in system. Please verify the number and try again."
            
            # Format the response with proper data quality handling
            return self._format_dn_details(result)
            
        except Exception as e:
            self.metrics["analytics_failure"] += 1
            logger.error(f"DN lookup failed for {dn_number}: {e}")
            return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again."
    
    def _format_dn_details(self, data: Dict) -> str:
        """Format DN details with proper data quality handling."""
        if not data or not data.get("found"):
            return "❌ DN not found."
        
        record = data.get("record", {})
        validation = data.get("validation", {})
        durations = validation.get("durations", {})
        status = data.get("status", "unknown")
        
        # Get values with proper None handling
        processing_days = durations.get('processing_time_days')
        delivery_days = durations.get('delivery_time_days')
        cycle_days = durations.get('total_cycle_days')
        
        # Data quality status
        is_valid = validation.get('is_valid', False)
        issues = validation.get('issues', [])
        
        # Determine quality status
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
    # ENTITY DASHBOARD EXECUTION
    # ==========================================================
    
    def _execute_entity_dashboard(self, entity_type: str, entity_name: str) -> str:
        """Execute dashboard for entity type with error handling."""
        try:
            if entity_type == "dealer":
                self.metrics["dealer_queries"] += 1
                result = self.analytics.get_dealer_dashboard(entity_name)
                
                # Check if dealer has data
                if not result or result.get("error"):
                    return f"🏪 *{entity_name} - No Data Found*\n\n" \
                           f"⚠️ No delivery data found for this dealer.\n\n" \
                           f"Please verify the dealer name or check if there are any deliveries."
                
                return self._format_dealer_dashboard(result, entity_name)
                
            elif entity_type == "city":
                self.metrics["city_queries"] += 1
                result = self.analytics.get_city_dashboard(entity_name)
                
                if not result or result.get("error"):
                    return f"🏙️ *{entity_name} - No Data Found*\n\n" \
                           f"⚠️ No delivery data found for this city.\n\n" \
                           f"Please verify the city name or check if there are any deliveries."
                
                return self._format_city_dashboard(result, entity_name)
                
            elif entity_type == "warehouse":
                self.metrics["warehouse_queries"] += 1
                result = self.analytics.get_warehouse_dashboard(entity_name)
                
                if not result or result.get("error"):
                    return f"🏭 *{entity_name} - No Data Found*\n\n" \
                           f"⚠️ No delivery data found for this warehouse.\n\n" \
                           f"Please verify the warehouse name or check if there are any deliveries."
                
                return self._format_warehouse_dashboard(result, entity_name)
                
            else:
                return f"❌ Unknown entity type: {entity_type}"
                
        except Exception as e:
            self.metrics["analytics_failure"] += 1
            logger.error(f"Dashboard failed for {entity_name}: {e}")
            return f"❌ Unable to retrieve dashboard for {entity_name}. Please try again."
    
    # ==========================================================
    # FORMATTERS - DEALER DASHBOARD
    # ==========================================================
    
    def _format_dealer_dashboard(self, data: Dict, dealer_name: str) -> str:
        """Format dealer dashboard response."""
        if not data or "error" in data:
            return f"❌ No data found for {dealer_name}"
        
        summary = data.get("summary", {})
        aging = data.get("aging", {})
        performance = data.get("performance", {})
        
        # Check if there's actual data
        if summary.get("total_dns", 0) == 0:
            return f"🏪 *{dealer_name} - No Deliveries Found*\n\n" \
                   f"⚠️ No delivery data found for this dealer.\n\n" \
                   f"Please verify the dealer name or check if there are any deliveries."
        
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
    
    # ==========================================================
    # FORMATTERS - CITY DASHBOARD
    # ==========================================================
    
    def _format_city_dashboard(self, data: Dict, city_name: str) -> str:
        """Format city dashboard response."""
        if not data or "error" in data:
            return f"❌ No data found for {city_name}"
        
        summary = data.get("summary", {})
        
        if summary.get("total_dns", 0) == 0:
            return f"🏙️ *{city_name} - No Deliveries Found*\n\n" \
                   f"⚠️ No delivery data found for this city.\n\n" \
                   f"Please verify the city name or check if there are any deliveries."
        
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
    
    # ==========================================================
    # FORMATTERS - WAREHOUSE DASHBOARD
    # ==========================================================
    
    def _format_warehouse_dashboard(self, data: Dict, warehouse_name: str) -> str:
        """Format warehouse dashboard response."""
        if not data or "error" in data:
            return f"❌ No data found for {warehouse_name}"
        
        summary = data.get("summary", {})
        
        if summary.get("total_dns", 0) == 0:
            return f"🏭 *{warehouse_name} - No Deliveries Found*\n\n" \
                   f"⚠️ No delivery data found for this warehouse.\n\n" \
                   f"Please verify the warehouse name or check if there are any deliveries."
        
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
    
    # ==========================================================
    # HELPER METHODS (Abbreviated for brevity)
    # ==========================================================
    
    def _is_entity_only_query(self, question: str, entity_name: str) -> bool:
        """Check if query is just an entity name."""
        question_clean = question.lower().strip()
        entity_clean = entity_name.lower().strip()
        
        if question_clean == entity_clean:
            return True
        
        prefixes = ["show ", "display ", "get ", "view ", "tell me about ", "what about "]
        for prefix in prefixes:
            if question_clean.startswith(prefix) and question_clean[len(prefix):].strip() == entity_clean:
                return True
        
        return False
    
    def _extract_query_plan(self, query_plan: Any) -> Dict[str, Any]:
        """Safely extract QueryPlan attributes."""
        return {
            "intent": getattr(query_plan, "intent", "help"),
            "entity": getattr(query_plan, "entity", None),
            "entity2": getattr(query_plan, "entity2", None),
            "entity_type": getattr(query_plan, "entity_type", None),
            "service": getattr(query_plan, "service", "help"),
            "confidence": getattr(query_plan, "confidence", 0.0),
            "original_message": getattr(query_plan, "original_message", ""),
        }
    
    def _get_query_plan(self, question: str, context: Dict) -> Any:
        """Get query plan from AIQueryService."""
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
            from types import SimpleNamespace
            return SimpleNamespace(
                original_message=question,
                intent="help",
                entity=None,
                entity2=None,
                entity_type=None,
                service="help",
                confidence=0.0
            )
    
    def _validate_and_override(self, query_plan: Any, question: str, entity_result: Dict) -> Any:
        """Validate and override query plan."""
        plan_data = self._extract_query_plan(query_plan)
        intent = plan_data["intent"]
        entity = plan_data["entity"]
        entity_type = plan_data["entity_type"]
        service = plan_data["service"]
        confidence = plan_data["confidence"]
        original = plan_data["original_message"]
        
        # Entity override
        if entity_result["type"] != "none":
            resolved_type = entity_result["type"]
            resolved_name = entity_result["name"]
            
            if self._is_entity_only_query(question, resolved_name) or confidence < 0.70:
                from types import SimpleNamespace
                return SimpleNamespace(
                    original_message=original,
                    intent=f"{resolved_type}_dashboard",
                    entity=resolved_name,
                    entity2=None,
                    entity_type=resolved_type,
                    service="analytics",
                    confidence=confidence
                )
        
        # Groq protection
        if service == "groq" and self._is_logistics_query(question):
            from types import SimpleNamespace
            return SimpleNamespace(
                original_message=original,
                intent="executive_insight",
                entity=entity,
                entity2=None,
                entity_type=entity_type,
                service="analytics",
                confidence=confidence
            )
        
        return query_plan
    
    def _is_logistics_query(self, question: str) -> bool:
        """Check if query contains logistics keywords."""
        question_lower = question.lower()
        for pattern in GROQ_BLOCKED_PATTERNS:
            if pattern in question_lower:
                return True
        return False
    
    def _execute_service(self, query_plan: Any, context: Dict, req_id: str) -> str:
        """Execute service with comprehensive error handling."""
        plan_data = self._extract_query_plan(query_plan)
        intent = plan_data["intent"]
        entity = plan_data["entity"]
        service = plan_data["service"]
        original = plan_data["original_message"]
        
        try:
            if service == "analytics":
                return self._execute_analytics(intent, entity, original)
            elif service == "kpi":
                return self._execute_kpi(intent, entity)
            elif service == "groq":
                return self._execute_groq(query_plan, context)
            else:
                return self._get_help_message()
        except Exception as e:
            self.metrics["analytics_failure"] += 1
            error_id = str(uuid.uuid4())[:8]
            logger.error(f"Service execution error [{error_id}]: {e}")
            return self._get_service_error_response(intent, entity, service, e, error_id)
    
    def _execute_analytics(self, intent: str, entity: Optional[str], original: str) -> str:
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
        
        # CITY ANALYTICS
        if intent == "city_dashboard" and entity:
            if not hasattr(self.analytics, 'get_city_dashboard'):
                return self._get_method_error("get_city_dashboard", "AnalyticsService")
            result = self.analytics.get_city_dashboard(entity)
            return self._format_city_dashboard(result, entity)
        
        # EXECUTIVE & ROOT CAUSE
        if intent == "executive_insight":
            if not hasattr(self.analytics, 'get_executive_summary'):
                return self._get_method_error("get_executive_summary", "AnalyticsService")
            result = self.analytics.get_executive_summary()
            return self._format_executive_insights(result)
        
        if intent == "root_cause":
            if not hasattr(self.analytics, 'get_root_cause_insights'):
                return self._get_method_error("get_root_cause_insights", "AnalyticsService")
            result = self.analytics.get_root_cause_insights()
            return self._format_root_cause(result)
        
        if intent == "control_tower":
            if not hasattr(self.analytics, 'get_control_tower_alerts'):
                return self._get_method_error("get_control_tower_alerts", "AnalyticsService")
            result = self.analytics.get_control_tower_alerts()
            return self._format_control_tower(result)
        
        if intent == "help":
            return self._get_help_message()
        
        return self._get_help_message()
    
    def _execute_kpi(self, intent: str, entity: Optional[str]) -> str:
        """Execute KPI queries."""
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
            logger.error(f"KPI execution failed: {e}")
            return f"⚠️ Unable to retrieve KPI data. Please try again."
    
    def _execute_groq(self, query_plan: Any, context: Dict) -> str:
        """Execute Groq ONLY for appropriate queries."""
        if self._is_logistics_query(getattr(query_plan, "original_message", "")):
            return self._get_groq_blocked_response()
        
        if hasattr(self.groq, 'is_available') and self.groq.is_available:
            try:
                response = self.groq.chat(getattr(query_plan, "original_message", ""), context)
                self.metrics["groq_uses"] += 1
                return response
            except Exception as e:
                logger.error(f"Groq execution failed: {e}")
                return "⚠️ AI service is temporarily unavailable. Please try again later."
        
        return "⚠️ AI service is not available. Please try again later."
    
    def _apply_groq_governance(self, response: str, query_plan: Any, question: str, context: Dict) -> str:
        """Enrich analytics with Groq insight."""
        if not hasattr(self.groq, 'is_available') or not self.groq.is_available:
            return response
        
        intent = getattr(query_plan, "intent", "")
        
        # Only enrich executive_insight and root_cause if they have meaningful data
        if intent in ["executive_insight", "root_cause"] and len(response) > 50:
            # Check if response has real data (not just "All metrics within acceptable range")
            if "0" in response and "No" in response:
                # Skip Groq enrichment for empty data
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
    
    def _format_executive_insights(self, data: Dict) -> str:
        """Format executive insights with better empty state handling."""
        if not data:
            return "📊 No executive insights available."
        
        if data.get("error"):
            return f"⚠️ {data['error']}"
        
        summary = data.get("summary", {})
        top_issues = data.get("top_issues", [])
        recommendations = data.get("recommendations", [])
        
        # Check if there's actual data
        if summary.get("total_dns", 0) == 0:
            return "📊 *Executive Insights*\n\n" \
                   "📈 *Overview:*\n" \
                   "   • No deliveries found in the system.\n\n" \
                   "⚠️ *Critical Issues:*\n" \
                   "   • No data available for analysis.\n\n" \
                   "💡 *Recommended Actions:*\n" \
                   "   • Please ensure data is imported into the system.\n" \
                   "   • Verify database connection.\n" \
                   "   • Check if there are any deliveries to analyze."
        
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
        """Format root cause with better empty state handling."""
        if not data:
            return "🔍 No root cause analysis available."
        
        if data.get("error"):
            return f"⚠️ {data['error']}"
        
        issues = data.get("key_issues", [])
        recommendations = data.get("recommendations", [])
        metrics = data.get("metrics", {})
        
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
    
    def _format_control_tower(self, data: Dict) -> str:
        """Format control tower response."""
        if not data:
            return "🚨 *Control Tower*\n\nNo data available."
        
        alerts = data.get("alerts", [])
        critical_count = data.get("critical_count", 0)
        high_count = data.get("high_count", 0)
        
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
        return (
            f"⚠️ *Unable to process your request*\n\n"
            f"• Error Reference: `{error_id}`\n"
            f"• Request ID: `{request_id}`\n"
            f"• Error: {str(error)[:100]}\n\n"
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
            "• 'Key issues' or 'Executive insights'\n\n"
            "Type 'Help' for all available commands."
        )
    
    def _get_help_message(self) -> str:
        return """📋 *AI Logistics Assistant - Help*

*🔍 DN Tracking:* Send any 8-12 digit DN number

*🏪 Dealer Queries:*
   • "Dealer name" (e.g., "Dubai Electronics")
   • "Dealer revenue" or "Dealer units"
   • "Dealer performance" or "Dealer aging"

*🏙️ City Queries:*
   • "City name" (e.g., "Haripur")

*🏭 Warehouse Queries:*
   • "Warehouse name" (e.g., "Rawalpindi")

*📊 Analytics:*
   • "Top dealers" or "Executive insights"
   • "Key issues" or "Critical alerts"

*🤖 General AI:* Any non-logistics question

*What would you like to know?* 🤖"""
    
    # ==========================================================
    # CONTEXT & CACHE
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
    
    def _update_context(self, phone_number: Optional[str], intent: str, entity_type: str, entity: str, req_id: str, response: str = ""):
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
        return {"status": "cleared", "version": "12.1"}
    
    # ==========================================================
    # METRICS
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        return {
            "total_requests": self.metrics["total_requests"],
            "cache_hits": self.metrics["cache_hits"],
            "cache_misses": self.metrics["cache_misses"],
            "cache_hit_rate": self.metrics["cache_hits"] / max(1, self.metrics["cache_hits"] + self.metrics["cache_misses"]),
            "dn_lookups": self.metrics["dn_lookups"],
            "dealer_queries": self.metrics["dealer_queries"],
            "city_queries": self.metrics["city_queries"],
            "warehouse_queries": self.metrics["warehouse_queries"],
            "kpi_queries": self.metrics["kpi_queries"],
            "executive_queries": self.metrics["executive_queries"],
            "groq_uses": self.metrics["groq_uses"],
            "analytics_success": self.metrics["analytics_success"],
            "analytics_failure": self.metrics["analytics_failure"],
            "errors": self.metrics["errors"],
            "conversation_count": len(self.conversation_cache),
            "cache_size": len(self.response_cache),
            "version": "12.1"
        }
    
    def get_routing_debug(self, question: str) -> Dict[str, Any]:
        context = {}
        query_plan = self._get_query_plan(question, context)
        plan_data = self._extract_query_plan(query_plan)
        return {
            "question": question,
            "routing_decision": plan_data,
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
logger.info("AI Provider Service v12.1 - Critical Fixes")
logger.info("=" * 70)
logger.info("")
logger.info("   FIXES:")
logger.info("   ✅ DN lookup handles missing dates")
logger.info("   ✅ Dealer resolution with better logging")
logger.info("   ✅ Executive insights with empty state handling")
logger.info("   ✅ Data quality validation")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
