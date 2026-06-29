"""
File: app/services/ai_provider_service.py
Version: 10.0 - ENTERPRISE REFACTORED: Service-Oriented Architecture
Purpose: LIGHTWEIGHT ORCHESTRATOR - SINGLE ENTRY POINT for all WhatsApp requests.
         Delegates ALL business logic to dedicated services.
         NO SQL, NO business logic, NO formatting logic.
"""

import logging
import time
import uuid
import asyncio
import inspect
from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass

# ============================================================
# TENACITY FOR RETRY LOGIC
# ============================================================

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================================================
# IMPORT DEDICATED SERVICES
# ============================================================

try:
    from .dn_service import get_dn_service
    from .dealer_service import get_dealer_service
    from .warehouse_service import get_warehouse_service
    from .city_service import get_city_service
    from .product_service import get_product_service
    from .national_kpi_service import get_national_kpi_service
    from .groq_service import get_groq_service
    from .base_service import ServiceResponse
    SERVICES_AVAILABLE = True
except ImportError as e:
    SERVICES_AVAILABLE = False
    logger.error(f"❌ Service imports failed: {e}")

# ============================================================
# INTENT DETECTION ENGINE
# ============================================================

try:
    from .ai_provider_service_intents import IntentDetectionEngine, RoutingDecision
    INTENTS_AVAILABLE = True
except ImportError as e:
    INTENTS_AVAILABLE = False
    logger.error(f"❌ IntentDetectionEngine import failed: {e}")
    
    @dataclass
    class RoutingDecision:
        intent: str = "general_ai"
        service_key: str = "groq"
        method: str = "process_query"
        entity: Optional[str] = None
        entity2: Optional[str] = None
        confidence: float = 0.0
        needs_groq: bool = True
        reason: str = ""
        original_message: str = ""
        suggestions: list = None
        metadata: dict = None
        
        def to_dict(self) -> Dict[str, Any]:
            return {
                "intent": self.intent,
                "service_key": self.service_key,
                "method": self.method,
                "entity": self.entity,
                "entity2": self.entity2,
                "confidence": self.confidence,
                "needs_groq": self.needs_groq,
                "reason": self.reason,
                "original_message": self.original_message,
                "suggestions": self.suggestions or [],
                "metadata": self.metadata or {}
            }
    
    class IntentDetectionEngine:
        def detect_intent(self, message: str) -> RoutingDecision:
            # Simple fallback - check for DN
            import re
            if re.sub(r'\D', '', message) and 8 <= len(re.sub(r'\D', '', message)) <= 12:
                dn_number = re.sub(r'\D', '', message)
                return RoutingDecision(
                    intent="dn_lookup",
                    service_key="dn",
                    method="get_dn_dashboard",
                    entity=dn_number,
                    confidence=1.0,
                    needs_groq=False,
                    reason="DN number detected",
                    original_message=message
                )
            return RoutingDecision(
                intent="general_ai",
                service_key="groq",
                method="process_query",
                needs_groq=True,
                original_message=message
            )

# ============================================================
# WHATSAPP PROVIDER SERVICE - LIGHTWEIGHT ORCHESTRATOR
# ============================================================

class WhatsAppProviderService:
    """
    LIGHTWEIGHT ORCHESTRATOR - Single entry point for all WhatsApp requests.
    
    Responsibilities:
        - Detect intent
        - Route to business services
        - Handle exceptions
        - Return responses
    
    DOES NOT CONTAIN:
        - SQL queries
        - Business logic
        - KPI calculations
        - Dashboard formatting
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._request_id = None
        start_time = time.time()
        
        try:
            self.logger.info("=" * 70)
            self.logger.info("🤖 WhatsApp AI Agent v10.0 - Service-Oriented Architecture")
            self.logger.info("=" * 70)
            
            # ============================================================
            # 1. INITIALIZE DEDICATED SERVICES
            # ============================================================
            
            if SERVICES_AVAILABLE:
                self.dn_service = get_dn_service()
                self.dealer_service = get_dealer_service()
                self.warehouse_service = get_warehouse_service()
                self.city_service = get_city_service()
                self.product_service = get_product_service()
                self.national_kpi_service = get_national_kpi_service()
                self.groq_service = get_groq_service()
                self.logger.info("✅ All business services initialized")
            else:
                self.dn_service = None
                self.dealer_service = None
                self.warehouse_service = None
                self.city_service = None
                self.product_service = None
                self.national_kpi_service = None
                self.groq_service = None
                self.logger.warning("⚠️ Services not available")
            
            # ============================================================
            # 2. INITIALIZE INTENT DETECTION
            # ============================================================
            
            if INTENTS_AVAILABLE:
                self.intent_engine = IntentDetectionEngine()
                self.logger.info("✅ IntentDetectionEngine initialized")
            else:
                self.intent_engine = IntentDetectionEngine()
                self.logger.info("⚠️ Using fallback IntentDetectionEngine")
            
            # ============================================================
            # 3. SERVICE REGISTRY FOR ROUTING
            # ============================================================
            
            self._service_registry = {
                'dn': self.dn_service,
                'dealer': self.dealer_service,
                'warehouse': self.warehouse_service,
                'city': self.city_service,
                'product': self.product_service,
                'national_kpi': self.national_kpi_service,
                'groq': self.groq_service,
                'default': self.groq_service
            }
            
            # ============================================================
            # 4. INTENT TO SERVICE MAPPING
            # ============================================================
            
            self._intent_service_map = {
                'dn_lookup': 'dn',
                'pending_dn': 'dn',
                'pending_pgi': 'dn',
                'pending_pod': 'dn',
                'dealer_dashboard': 'dealer',
                'dealer_suggestion': 'dealer',
                'top_dealers': 'dealer',
                'bottom_dealers': 'dealer',
                'top_dealers_revenue': 'dealer',
                'top_dealers_units': 'dealer',
                'comparison': 'dealer',
                'warehouse_dashboard': 'warehouse',
                'city_dashboard': 'city',
                'product_dashboard': 'product',
                'national_kpi': 'national_kpi',
                'help': 'groq',
                'greeting': 'groq',
                'conversational': 'groq',
                'general_ai': 'groq',
                'default': 'groq'
            }
            
            init_duration = (time.time() - start_time) * 1000
            self.logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            self.logger.info("   STATUS: ✅ PRODUCTION GRADE")
            self.logger.info("=" * 70)
            
        except Exception as e:
            self.logger.exception(f"❌ Failed to initialize: {str(e)}")
            raise
    
    # ============================================================
    # MAIN ENTRY POINT - DO NOT CHANGE SIGNATURE
    # ============================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Single entry point for all WhatsApp requests.
        
        IMPORTANT: DO NOT CHANGE THIS SIGNATURE - webhook.py depends on it.
        """
        # Generate request ID for tracking
        request_id = str(uuid.uuid4())[:8]
        self._request_id = request_id
        
        self.logger.info(f"📩 [REQ:{request_id}] Processing: '{message[:100]}'")
        start_time = time.perf_counter()
        
        try:
            # ============================================================
            # STEP 1: DETECT INTENT
            # ============================================================
            
            routing_decision = self.intent_engine.detect_intent(message)
            self.logger.info(
                f"🎯 [REQ:{request_id}] Intent: {routing_decision.intent} "
                f"(confidence: {routing_decision.confidence:.2f})"
            )
            
            # ============================================================
            # STEP 2: ROUTE TO APPROPRIATE SERVICE
            # ============================================================
            
            response = await self._route_request(routing_decision, request_id)
            
            # ============================================================
            # STEP 3: ENHANCE WITH GROQ (if needed)
            # ============================================================
            
            if routing_decision.needs_groq and self.groq_service:
                response = await self._enhance_with_groq(
                    message, routing_decision, response, request_id
                )
            
            # ============================================================
            # STEP 4: FORMAT AND RETURN
            # ============================================================
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self.logger.info(f"⏱️ [REQ:{request_id}] Response time: {elapsed_ms:.2f}ms")
            
            return self._format_orchestrated_response(
                message=message,
                response=response,
                routing_decision=routing_decision,
                request_id=request_id,
                elapsed_ms=elapsed_ms
            )
            
        except Exception as e:
            self.logger.exception(f"❌ [REQ:{request_id}] Failed: {str(e)}")
            return self._format_error_response(message, str(e), request_id)
    
    # ============================================================
    # REQUEST ROUTING
    # ============================================================
    
    async def _route_request(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """
        Route request to appropriate business service based on intent.
        """
        try:
            # Determine service key
            service_key = self._intent_service_map.get(
                decision.intent,
                self._intent_service_map.get('default', 'groq')
            )
            
            # Get service instance
            service = self._service_registry.get(service_key)
            if not service:
                self.logger.warning(f"⚠️ [REQ:{request_id}] Service '{service_key}' not found")
                return {
                    "success": False,
                    "error": f"Service '{service_key}' not available",
                    "whatsapp_message": "⚠️ Service temporarily unavailable."
                }
            
            # Get method
            method_name = decision.method
            method = getattr(service, method_name, None)
            
            if not method:
                self.logger.warning(
                    f"⚠️ [REQ:{request_id}] Method '{method_name}' not found in {service_key}"
                )
                return {
                    "success": False,
                    "error": f"Method '{method_name}' not found",
                    "whatsapp_message": "⚠️ Service method not available."
                }
            
            # Execute with appropriate arguments
            if decision.entity and decision.entity2:
                result = method(decision.entity, decision.entity2)
            elif decision.entity:
                result = method(decision.entity)
            else:
                result = method()
            
            # Handle async results
            if asyncio.iscoroutine(result):
                result = await result
            
            # Ensure result is a dict
            if not isinstance(result, dict):
                result = {
                    "success": True,
                    "data": result,
                    "whatsapp_message": str(result)
                }
            
            return result
            
        except Exception as e:
            self.logger.error(f"❌ [REQ:{request_id}] Routing failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "whatsapp_message": f"⚠️ Service error: {str(e)}"
            }
    
    # ============================================================
    # GROQ ENHANCEMENT
    # ============================================================
    
    async def _enhance_with_groq(
        self,
        message: str,
        decision: RoutingDecision,
        response: Dict[str, Any],
        request_id: str
    ) -> Dict[str, Any]:
        """
        Enhance response with Groq AI when needed.
        """
        try:
            if not self.groq_service:
                return response
            
            # Check if enhancement is needed
            if response.get("success") is False:
                return response
            
            if response.get("whatsapp_message"):
                # Already has message, but enhance for conversational intents
                if decision.intent in ['conversational', 'help', 'greeting']:
                    enhanced = await self.groq_service.enhance_response(
                        message=message,
                        decision=decision,
                        service_response=response,
                        request_id=request_id
                    )
                    if enhanced:
                        return enhanced
            
            return response
            
        except Exception as e:
            self.logger.error(f"❌ [REQ:{request_id}] Groq enhancement failed: {e}")
            return response
    
    # ============================================================
    # RESPONSE FORMATTING
    # ============================================================
    
    def _format_orchestrated_response(
        self,
        message: str,
        response: Dict[str, Any],
        routing_decision: RoutingDecision,
        request_id: str,
        elapsed_ms: float
    ) -> Dict[str, Any]:
        """Format final response from orchestration"""
        
        # If service returned a response already
        if response.get("whatsapp_message"):
            return {
                "success": response.get("success", True),
                "message": message,
                "response": response.get("whatsapp_message"),
                "error": response.get("error", False),
                "timestamp": datetime.now().isoformat(),
                "request_id": request_id,
                "elapsed_ms": round(elapsed_ms, 2),
                "metadata": {
                    "intent": routing_decision.intent,
                    "confidence": routing_decision.confidence,
                    "service": routing_decision.service_key
                }
            }
        
        # If service returned data
        if response.get("data"):
            data = response.get("data")
            if isinstance(data, str):
                return {
                    "success": True,
                    "message": message,
                    "response": data,
                    "error": False,
                    "timestamp": datetime.now().isoformat(),
                    "request_id": request_id,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "metadata": {
                        "intent": routing_decision.intent,
                        "confidence": routing_decision.confidence,
                        "service": routing_decision.service_key
                    }
                }
        
        # Fallback
        return {
            "success": response.get("success", True),
            "message": message,
            "response": response.get("response", response.get("whatsapp_message", "✅ Processed successfully")),
            "error": response.get("error", False),
            "timestamp": datetime.now().isoformat(),
            "request_id": request_id,
            "elapsed_ms": round(elapsed_ms, 2),
            "metadata": {
                "intent": routing_decision.intent,
                "confidence": routing_decision.confidence,
                "service": routing_decision.service_key
            }
        }
    
    def _format_error_response(self, message: str, error: str, request_id: str) -> Dict[str, Any]:
        """Format error response"""
        return {
            "success": False,
            "message": message,
            "response": f"⚠️ An unexpected error occurred. Please try again later.",
            "error": True,
            "timestamp": datetime.now().isoformat(),
            "request_id": request_id,
            "metadata": {
                "error": error
            }
        }
    
    # ============================================================
    # SYSTEM HEALTH
    # ============================================================
    
    def get_system_health(self) -> Dict[str, Any]:
        """Get comprehensive system health status."""
        services = {
            'dn': self.dn_service is not None,
            'dealer': self.dealer_service is not None,
            'warehouse': self.warehouse_service is not None,
            'city': self.city_service is not None,
            'product': self.product_service is not None,
            'national_kpi': self.national_kpi_service is not None,
            'groq': self.groq_service is not None
        }
        
        all_loaded = all(services.values())
        
        return {
            "status": "healthy" if all_loaded else "degraded",
            "version": "10.0",
            "services": services,
            "timestamp": datetime.now().isoformat(),
            "architecture": "Service-Oriented",
            "intent_engine": INTENTS_AVAILABLE,
            "tenacity_available": TENACITY_AVAILABLE
        }
    
    def clear_caches(self):
        """Clear all service caches"""
        try:
            for service in self._service_registry.values():
                if service and hasattr(service, 'clear_cache'):
                    service.clear_cache()
            self.logger.info("✅ All caches cleared")
        except Exception as e:
            self.logger.error(f"❌ Cache clear failed: {e}")

# ============================================================
# SINGLETON
# ============================================================

_whatsapp_provider_service = None
_provider_service_lock = asyncio.Lock()

async def get_whatsapp_provider_service() -> WhatsAppProviderService:
    """Get or create WhatsApp provider service singleton"""
    global _whatsapp_provider_service
    
    if _whatsapp_provider_service is None:
        async with _provider_service_lock:
            if _whatsapp_provider_service is None:
                try:
                    _whatsapp_provider_service = WhatsAppProviderService()
                    logger.info("✅ WhatsAppProviderService initialized (v10.0)")
                except Exception as e:
                    logger.exception(f"❌ Initialization failed: {e}")
                    raise
    
    return _whatsapp_provider_service

# ============================================================
# SYNC VERSION FOR BACKWARD COMPATIBILITY
# ============================================================

def get_whatsapp_provider_service_sync() -> WhatsAppProviderService:
    """Synchronous version for backward compatibility"""
    global _whatsapp_provider_service
    
    if _whatsapp_provider_service is None:
        try:
            _whatsapp_provider_service = WhatsAppProviderService()
            logger.info("✅ WhatsAppProviderService initialized (v10.0 sync)")
        except Exception as e:
            logger.exception(f"❌ Initialization failed: {e}")
            raise
    
    return _whatsapp_provider_service

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    'WhatsAppProviderService',
    'get_whatsapp_provider_service',
    'get_whatsapp_provider_service_sync',
    'RoutingDecision',
    'IntentDetectionEngine'
]

logger.info("=" * 70)
logger.info("AI Provider Service v10.0 - REFACTORED")
logger.info("=" * 70)
logger.info("✅ Lightweight Orchestrator")
logger.info("✅ Service-Oriented Architecture")
logger.info("✅ No Business Logic")
logger.info("✅ No SQL Queries")
logger.info("✅ Backward Compatible")
logger.info("=" * 70)
