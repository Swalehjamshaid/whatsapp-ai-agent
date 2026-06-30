"""
File: app/services/ai_provider_service.py
Version: 10.0 - ENTERPRISE REFACTORED: Service-Oriented Architecture
Purpose: LIGHTWEIGHT ORCHESTRATOR - SINGLE ENTRY POINT for all WhatsApp requests.
         Delegates ALL business logic to dedicated services.
         NO SQL, NO business logic, NO formatting logic.
"""

import logging
import os
import threading
import time
import importlib
import inspect
import re
import sys
import asyncio
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from functools import wraps
import uuid

logger = logging.getLogger(__name__)

# ============================================================
# TENACITY FOR RETRY LOGIC
# ============================================================

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    logger.warning("⚠️ Tenacity not installed. Install with: pip install tenacity>=8.5.0")

# ============================================================
# IMPORTS WITH FALLBACK
# ============================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Core imports successful")
except ImportError as e:
    logger.error(f"❌ Core import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

# ============================================================
# IMPORT INTENT DETECTION ENGINE
# ============================================================

try:
    from .ai_provider_service_intents import IntentDetectionEngine, RoutingDecision
    INTENTS_AVAILABLE = True
    logger.info("✅ IntentDetectionEngine imported successfully")
except ImportError as e:
    INTENTS_AVAILABLE = False
    logger.warning(f"⚠️ IntentDetectionEngine import failed: {e}")
    
    # Fallback RoutingDecision
    @dataclass
    class RoutingDecision:
        intent: str
        service_key: str
        method: str
        entity: Optional[str] = None
        entity2: Optional[str] = None
        confidence: float = 0.0
        needs_groq: bool = False
        reason: str = ""
        original_message: str = ""
        suggestions: List[str] = field(default_factory=list)
        metadata: Dict[str, Any] = field(default_factory=dict)
        
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
                "suggestions": self.suggestions,
                "metadata": self.metadata
            }
    
    class IntentDetectionEngine:
        def detect_intent(self, message: str) -> RoutingDecision:
            import re
            cleaned = message.strip()
            # Check for DN number
            if re.sub(r'\D', '', cleaned) and 8 <= len(re.sub(r'\D', '', cleaned)) <= 12:
                dn_number = re.sub(r'\D', '', cleaned)
                return RoutingDecision(
                    intent="dn_lookup",
                    service_key="dn",
                    method="get_dn_dashboard",
                    entity=dn_number,
                    confidence=1.0,
                    needs_groq=False,
                    reason="DN number detected",
                    original_message=cleaned
                )
            # Check for dealer name
            if len(cleaned.split()) <= 4 and len(cleaned) > 2 and not re.match(r'^\d+$', cleaned):
                return RoutingDecision(
                    intent="dealer_dashboard",
                    service_key="dealer",
                    method="get_dealer_dashboard",
                    entity=cleaned,
                    confidence=0.80,
                    needs_groq=False,
                    reason="Dealer name detected",
                    original_message=cleaned
                )
            return RoutingDecision(
                intent="general_ai",
                service_key="groq",
                method="process_query",
                needs_groq=True,
                original_message=cleaned
            )

# ============================================================
# SERVICE REGISTRY - MATCHES YOUR FILE NAMES
# ============================================================

class ServiceRegistry:
    SERVICES = {
        "dn": {
            "module": "app.services.dn_analysis",
            "class_name": "DNAnalysisService",
            "expected_methods": [
                "get_dn_dashboard",
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "DN Analytics Service",
        },
        "dealer": {
            "module": "app.services.dealer_analytics_service",
            "class_name": "DealerAnalyticsService",
            "expected_methods": [
                "get_dealer_dashboard",
                "get_dealer_profile",
                "compare_dealers",
                "get_top_dealers",
                "get_bottom_dealers",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "Dealer Analytics Service",
        },
        "warehouse": {
            "module": "app.services.warehouse_service",
            "class_name": "WarehouseAnalyticsService",
            "expected_methods": [
                "get_warehouse_dashboard",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "Warehouse Analytics Service",
        },
        "city": {
            "module": "app.services.city_service",
            "class_name": "CityAnalyticsService",
            "expected_methods": [
                "get_city_dashboard",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "City Analytics Service",
        },
        "product": {
            "module": "app.services.product_service",
            "class_name": "ProductAnalyticsService",
            "expected_methods": [
                "get_product_dashboard",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "Product Analytics Service",
        },
        "national_kpi": {
            "module": "app.services.national_kpi_service",
            "class_name": "NationalKPIService",
            "expected_methods": [
                "get_national_kpi_dashboard",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "National KPI Service",
        },
        "groq": {
            "module": "app.services.groq_service",
            "class_name": "GroqService",
            "expected_methods": ["process_query"],
            "description": "Groq AI Service",
        }
    }
    
    def __init__(self):
        self._services = self.SERVICES.copy()
        self._instance_cache = {}
        self._lock = threading.RLock()
        self._service_health = {}
    
    def get_service_instance(self, service_key: str):
        """Get service instance with caching"""
        if service_key in self._instance_cache:
            return self._instance_cache[service_key]
        
        with self._lock:
            if service_key in self._instance_cache:
                return self._instance_cache[service_key]
            
            try:
                service_def = self._services.get(service_key)
                if not service_def:
                    logger.error(f"Service '{service_key}' not registered")
                    return None
                
                module = importlib.import_module(service_def["module"])
                cls = getattr(module, service_def["class_name"])
                instance = cls()
                
                # Validate expected methods
                for method in service_def.get("expected_methods", []):
                    if not hasattr(instance, method):
                        logger.warning(f"⚠️ Service '{service_key}' missing method: {method}")
                
                self._instance_cache[service_key] = instance
                self._service_health[service_key] = {
                    "loaded": True,
                    "class": service_def["class_name"],
                    "module": service_def["module"]
                }
                logger.info(f"✅ Service '{service_key}' initialized from {service_def['module']}")
                return instance
            except ImportError as e:
                logger.error(f"❌ Failed to import service '{service_key}': {e}")
                return None
            except Exception as e:
                logger.error(f"❌ Failed to load service '{service_key}': {e}")
                return None
    
    def is_service_ready(self, service_key: str) -> bool:
        instance = self.get_service_instance(service_key)
        return instance is not None
    
    def get_service_health(self, service_key: str) -> Dict[str, Any]:
        return self._service_health.get(service_key, {"loaded": False})

# ============================================================
# WHATSAPP PROVIDER SERVICE - LIGHTWEIGHT ORCHESTRATOR
# ============================================================

class WhatsAppProviderService:
    def __init__(self):
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("🤖 WhatsApp AI Agent v10.0 - Service-Oriented Architecture")
            logger.info("=" * 70)
            
            # Initialize registry
            self.registry = ServiceRegistry()
            logger.info("✅ ServiceRegistry initialized")
            
            # Initialize intent engine
            if INTENTS_AVAILABLE:
                self.intent_engine = IntentDetectionEngine()
                logger.info("✅ IntentDetectionEngine initialized (v4.0)")
            else:
                self.intent_engine = IntentDetectionEngine()
                logger.info("⚠️ Using fallback IntentDetectionEngine")
            
            # Load all services with detailed logging
            logger.info("=" * 50)
            logger.info("🔧 LOADING SERVICES...")
            logger.info("=" * 50)
            
            self.dn_service = self.registry.get_service_instance("dn")
            if self.dn_service:
                logger.info("✅ DN Service loaded (dn_analysis.py)")
            else:
                logger.error("❌ DN Service FAILED to load (dn_analysis.py)")
            
            self.dealer_service = self.registry.get_service_instance("dealer")
            if self.dealer_service:
                logger.info("✅ Dealer Service loaded (dealer_analytics_service.py)")
            else:
                logger.error("❌ Dealer Service FAILED to load (dealer_analytics_service.py)")
            
            self.warehouse_service = self.registry.get_service_instance("warehouse")
            if self.warehouse_service:
                logger.info("✅ Warehouse Service loaded (warehouse_service.py)")
            else:
                logger.warning("⚠️ Warehouse Service FAILED to load (warehouse_service.py - optional)")
            
            self.city_service = self.registry.get_service_instance("city")
            if self.city_service:
                logger.info("✅ City Service loaded (city_service.py)")
            else:
                logger.warning("⚠️ City Service FAILED to load (city_service.py - optional)")
            
            self.product_service = self.registry.get_service_instance("product")
            if self.product_service:
                logger.info("✅ Product Service loaded (product_service.py)")
            else:
                logger.warning("⚠️ Product Service FAILED to load (product_service.py - optional)")
            
            self.national_kpi_service = self.registry.get_service_instance("national_kpi")
            if self.national_kpi_service:
                logger.info("✅ National KPI Service loaded (national_kpi_service.py)")
            else:
                logger.warning("⚠️ National KPI Service FAILED to load (national_kpi_service.py - optional)")
            
            self.groq_service = self.registry.get_service_instance("groq")
            if self.groq_service:
                logger.info("✅ Groq Service loaded (groq_service.py)")
            else:
                logger.warning("⚠️ Groq Service FAILED to load (groq_service.py - optional)")
            
            # Log summary
            logger.info("=" * 50)
            logger.info("📊 SERVICE STATUS SUMMARY:")
            logger.info(f"   DN: {'✅' if self.dn_service else '❌'} (dn_analysis.py)")
            logger.info(f"   Dealer: {'✅' if self.dealer_service else '❌'} (dealer_analytics_service.py)")
            logger.info(f"   Warehouse: {'✅' if self.warehouse_service else '❌'} (warehouse_service.py)")
            logger.info(f"   City: {'✅' if self.city_service else '❌'} (city_service.py)")
            logger.info(f"   Product: {'✅' if self.product_service else '❌'} (product_service.py)")
            logger.info(f"   National KPI: {'✅' if self.national_kpi_service else '❌'} (national_kpi_service.py)")
            logger.info(f"   Groq: {'✅' if self.groq_service else '❌'} (groq_service.py)")
            logger.info("=" * 50)
            
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("   STATUS: ✅ PRODUCTION GRADE")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"❌ Failed to initialize: {str(e)}")
            raise
    
    # ============================================================
    # MAIN ROUTING METHOD - ENTRY POINT
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
        request_id = str(uuid.uuid4())[:8]
        logger.info(f"📩 [REQ:{request_id}] Processing: '{message[:100]}'")
        start_time = time.perf_counter()
        
        try:
            # ============================================================
            # STEP 1: DETECT INTENT
            # ============================================================
            routing_decision = self.intent_engine.detect_intent(message)
            logger.info(
                f"🎯 [REQ:{request_id}] Intent: {routing_decision.intent} "
                f"(confidence: {routing_decision.confidence:.2f})"
            )
            
            # ============================================================
            # STEP 2: ROUTE TO APPROPRIATE SERVICE
            # ============================================================
            
            # DN Lookup
            if routing_decision.intent == "dn_lookup":
                logger.info(f"🔍 [REQ:{request_id}] Routing to DN service")
                result = await self._handle_dn(routing_decision, request_id)
                if result:
                    return result
                return self._format_response(
                    message,
                    "⚠️ DN service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            # Pending Queries
            elif routing_decision.intent in ["pending_dn", "pending_pgi", "pending_pod"]:
                logger.info(f"🔍 [REQ:{request_id}] Routing to DN service for pending query")
                result = await self._handle_pending(routing_decision, request_id)
                if result:
                    return result
                return self._format_response(
                    message,
                    "⚠️ DN service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            # Dealer Dashboard
            elif routing_decision.intent in ["dealer_dashboard", "dealer_suggestion"]:
                logger.info(f"🔍 [REQ:{request_id}] Routing to Dealer service")
                result = await self._handle_dealer(routing_decision, request_id)
                if result:
                    return result
                return self._format_response(
                    message,
                    "⚠️ Dealer service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            # Dealer Ranking
            elif routing_decision.intent in ["top_dealers", "bottom_dealers"]:
                logger.info(f"🔍 [REQ:{request_id}] Routing to Dealer service for ranking")
                result = await self._handle_dealer_ranking(routing_decision, request_id)
                if result:
                    return result
                return self._format_response(
                    message,
                    "⚠️ Dealer service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            # Warehouse Dashboard
            elif routing_decision.intent == "warehouse_dashboard":
                logger.info(f"🔍 [REQ:{request_id}] Routing to Warehouse service")
                result = await self._handle_warehouse(routing_decision, request_id)
                if result:
                    return result
                return self._format_response(
                    message,
                    "⚠️ Warehouse service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            # City Dashboard
            elif routing_decision.intent == "city_dashboard":
                logger.info(f"🔍 [REQ:{request_id}] Routing to City service")
                result = await self._handle_city(routing_decision, request_id)
                if result:
                    return result
                return self._format_response(
                    message,
                    "⚠️ City service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            # Product Dashboard
            elif routing_decision.intent == "product_dashboard":
                logger.info(f"🔍 [REQ:{request_id}] Routing to Product service")
                result = await self._handle_product(routing_decision, request_id)
                if result:
                    return result
                return self._format_response(
                    message,
                    "⚠️ Product service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            # National KPI
            elif routing_decision.intent == "national_kpi":
                logger.info(f"🔍 [REQ:{request_id}] Routing to National KPI service")
                result = await self._handle_national_kpi(routing_decision, request_id)
                if result:
                    return result
                return self._format_response(
                    message,
                    "⚠️ National KPI service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            # Groq (Conversational)
            elif routing_decision.needs_groq or routing_decision.service_key == "groq":
                logger.info(f"🔍 [REQ:{request_id}] Routing to Groq service")
                return await self._handle_groq(message, routing_decision, request_id)
            
            # Fallback
            else:
                return self._format_response(
                    message,
                    "I couldn't identify your request. Please specify:\n"
                    "• A DN number (8-12 digits, e.g., 6243699261)\n"
                    "• A dealer name (e.g., 'Sham Electronics')\n"
                    "• 'Pending DN' for pending deliveries\n"
                    "• 'Top dealers' for rankings\n\n"
                    "Type 'Help' for all commands.",
                    error=False,
                    request_id=request_id
                )
            
        except Exception as e:
            logger.exception(f"❌ [REQ:{request_id}] Failed: {e}")
            return self._format_response(
                message,
                f"⚠️ An unexpected error occurred. Reference: {request_id}",
                error=True,
                request_id=request_id
            )
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"⏱️ [REQ:{request_id}] Response time: {elapsed_ms:.2f}ms")
    
    # ============================================================
    # HANDLERS
    # ============================================================
    
    async def _handle_dn(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle DN lookup"""
        try:
            if not self.dn_service:
                logger.error(f"❌ [REQ:{request_id}] DN service not available")
                return None
            
            logger.info(f"🔍 [REQ:{request_id}] Looking up DN: {decision.entity}")
            result = self.dn_service.get_dn_dashboard(decision.entity)
            
            if result and isinstance(result, dict):
                if result.get("success", False):
                    data = result.get("data")
                    if hasattr(data, "to_whatsapp_message"):
                        return self._format_response(decision.original_message, data, error=False, request_id=request_id)
                    return self._format_response(decision.original_message, result.get("whatsapp_message", data), error=False, request_id=request_id)
                else:
                    return self._format_response(
                        decision.original_message,
                        result.get("whatsapp_message", f"❌ DN {decision.entity} not found."),
                        error=True,
                        request_id=request_id
                    )
            return None
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] DN handler failed: {e}")
            return None
    
    async def _handle_dealer(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle Dealer dashboard"""
        try:
            if not self.dealer_service:
                logger.error(f"❌ [REQ:{request_id}] Dealer service not available")
                return None
            
            entity = decision.entity or decision.original_message
            logger.info(f"🔍 [REQ:{request_id}] Looking up dealer: {entity}")
            result = self.dealer_service.get_dealer_dashboard(entity)
            
            if result and isinstance(result, dict):
                if result.get("success", False):
                    data = result.get("data")
                    if hasattr(data, "to_whatsapp_message"):
                        return self._format_response(decision.original_message, data, error=False, request_id=request_id)
                    return self._format_response(decision.original_message, result.get("whatsapp_message", data), error=False, request_id=request_id)
                else:
                    return self._format_response(
                        decision.original_message,
                        result.get("whatsapp_message", f"❌ Dealer '{entity}' not found."),
                        error=True,
                        request_id=request_id
                    )
            return None
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Dealer handler failed: {e}")
            return None
    
    async def _handle_dealer_ranking(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle Dealer ranking"""
        try:
            if not self.dealer_service:
                logger.error(f"❌ [REQ:{request_id}] Dealer service not available")
                return None
            
            if decision.intent == "top_dealers":
                result = self.dealer_service.get_top_dealers(limit=10)
            else:
                result = self.dealer_service.get_bottom_dealers(limit=10)
            
            if result and isinstance(result, dict):
                if result.get("success", False):
                    return self._format_response(decision.original_message, result.get("whatsapp_message"), error=False, request_id=request_id)
                else:
                    return self._format_response(
                        decision.original_message,
                        result.get("whatsapp_message", "⚠️ Ranking query failed."),
                        error=True,
                        request_id=request_id
                    )
            return None
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Dealer ranking handler failed: {e}")
            return None
    
    async def _handle_pending(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle pending queries"""
        try:
            if not self.dn_service:
                logger.error(f"❌ [REQ:{request_id}] DN service not available")
                return None
            
            if decision.intent == "pending_dn":
                result = self.dn_service.get_pending_dns()
            elif decision.intent == "pending_pgi":
                result = self.dn_service.get_pending_pgi()
            elif decision.intent == "pending_pod":
                result = self.dn_service.get_pending_pod()
            else:
                result = self.dn_service.get_pending_dns()
            
            if result and isinstance(result, dict):
                if result.get("success", False):
                    return self._format_response(decision.original_message, result.get("whatsapp_message"), error=False, request_id=request_id)
                else:
                    return self._format_response(
                        decision.original_message,
                        result.get("whatsapp_message", "⚠️ Pending query failed."),
                        error=True,
                        request_id=request_id
                    )
            return None
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Pending handler failed: {e}")
            return None
    
    async def _handle_warehouse(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle Warehouse dashboard"""
        try:
            if not self.warehouse_service:
                logger.error(f"❌ [REQ:{request_id}] Warehouse service not available")
                return None
            
            entity = decision.entity or decision.original_message
            logger.info(f"🔍 [REQ:{request_id}] Looking up warehouse: {entity}")
            result = self.warehouse_service.get_warehouse_dashboard(entity)
            
            if result and isinstance(result, dict):
                if result.get("success", False):
                    data = result.get("data")
                    if hasattr(data, "to_whatsapp_message"):
                        return self._format_response(decision.original_message, data, error=False, request_id=request_id)
                    return self._format_response(decision.original_message, result.get("whatsapp_message", data), error=False, request_id=request_id)
                else:
                    return self._format_response(
                        decision.original_message,
                        result.get("whatsapp_message", f"❌ Warehouse '{entity}' not found."),
                        error=True,
                        request_id=request_id
                    )
            return None
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Warehouse handler failed: {e}")
            return None
    
    async def _handle_city(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle City dashboard"""
        try:
            if not self.city_service:
                logger.error(f"❌ [REQ:{request_id}] City service not available")
                return None
            
            entity = decision.entity or decision.original_message
            logger.info(f"🔍 [REQ:{request_id}] Looking up city: {entity}")
            result = self.city_service.get_city_dashboard(entity)
            
            if result and isinstance(result, dict):
                if result.get("success", False):
                    data = result.get("data")
                    if hasattr(data, "to_whatsapp_message"):
                        return self._format_response(decision.original_message, data, error=False, request_id=request_id)
                    return self._format_response(decision.original_message, result.get("whatsapp_message", data), error=False, request_id=request_id)
                else:
                    return self._format_response(
                        decision.original_message,
                        result.get("whatsapp_message", f"❌ City '{entity}' not found."),
                        error=True,
                        request_id=request_id
                    )
            return None
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] City handler failed: {e}")
            return None

    async def _handle_product(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle Product dashboard"""
        try:
            if not self.product_service:
                logger.error(f"❌ [REQ:{request_id}] Product service not available")
                return None
            
            entity = decision.entity or decision.original_message
            result = self.product_service.get_product_dashboard(entity)
            if result and isinstance(result, dict):
                return self._format_response(decision.original_message, result.get("whatsapp_message", result.get("data")), error=not result.get("success", False), request_id=request_id)
            return None
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Product handler failed: {e}")
            return None

    async def _handle_national_kpi(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle National KPI queries"""
        try:
            if not self.national_kpi_service:
                logger.error(f"❌ [REQ:{request_id}] National KPI service not available")
                return None
            
            logger.info(f"🔍 [REQ:{request_id}] Gathering national KPIs")
            result = self.national_kpi_service.get_national_kpi_dashboard()
            
            if result and isinstance(result, dict):
                return self._format_response(
                    decision.original_message, 
                    result.get("whatsapp_message", result.get("data")), 
                    error=not result.get("success", False), 
                    request_id=request_id
                )
            return None
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] National KPI handler failed: {e}")
            return None

    async def _handle_groq(self, message: str, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle unstructured conversational logic via GroqService"""
        try:
            if not self.groq_service:
                logger.error(f"❌ [REQ:{request_id}] Groq service not available")
                return self._format_response(
                    message,
                    "⚠️ Conversational AI engine is currently offline. Please use strict pattern matching.",
                    error=True,
                    request_id=request_id
                )
            
            # Delegate raw prompt processing to your actual microservice
            ai_response = self.groq_service.process_query(message)
            if isinstance(ai_response, dict):
                return self._format_response(
                    message,
                    ai_response.get("whatsapp_message", ai_response.get("data", "Error processing text")),
                    error=not ai_response.get("success", True),
                    request_id=request_id
                )
            
            return self._format_response(message, str(ai_response), error=False, request_id=request_id)
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Groq interface handler failure: {e}")
            return self._format_response(message, "⚠️ Conversational component failed.", error=True, request_id=request_id)

    # ============================================================
    # UNIFIED OUTPUT SYSTEM - MATCHES WEBHOOK SCHEMAS
    # ============================================================

    def _format_response(self, query: str, content: Any, error: bool = False, request_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Builds uniform structural boundaries for responses.
        Ensures NO string building or data formatting happens inside this file.
        """
        # Unpack rich dataclasses if they have string conversions
        if hasattr(content, "to_whatsapp_message"):
            text_payload = content.to_whatsapp_message()
        else:
            text_payload = str(content)

        return {
            "query": query,
            "whatsapp_message": text_payload,
            "success": not error,
            "metadata": {
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat(),
                "orchestrator_version": "10.0"
            }
        }
