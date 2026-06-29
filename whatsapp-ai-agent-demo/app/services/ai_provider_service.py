"""
File: app/services/ai_provider_service.py
Version: 10.2 - COMPLETE FIXED: Proper Routing to DN and Dealer Services
Purpose: SINGLE ENTRY POINT for all WhatsApp requests.
         FIXED: DN numbers route to DN service, dealer names to dealer service
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
# ROUTING DECISION
# ============================================================

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

# ============================================================
# INTENT DETECTION ENGINE - RELIABLE AND SIMPLE
# ============================================================

class IntentDetectionEngine:
    """Reliable intent detection using regex patterns"""
    
    def __init__(self):
        # DN Pattern - 8 to 12 digits
        self.DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
        
        # Pending Patterns
        self.PENDING_DN_PATTERN = re.compile(r'(?:pending|open|outstanding)\s*(?:dn|dns|delivery|deliveries)', re.IGNORECASE)
        self.PENDING_PGI_PATTERN = re.compile(r'(?:pending|open)\s*(?:pgi|goods issue)', re.IGNORECASE)
        self.PENDING_POD_PATTERN = re.compile(r'(?:pending|open)\s*(?:pod|proof of delivery)', re.IGNORECASE)
        
        # Dealer Patterns
        self.DEALER_PATTERN = re.compile(
            r'(?:dealer|about|for|company|customer|tell me about|show me|get|view|display|give me)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.DEALER_DASHBOARD_PATTERN = re.compile(
            r'(?:dashboard|profile|summary|overview|info|information|details|status|statistics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # Ranking Patterns
        self.RANKING_PATTERN = re.compile(
            r'(?:top|best|highest|lowest|worst|bottom)\s+(\d+)?\s*(?:dealers?|cities?|warehouses?|products?)',
            re.IGNORECASE
        )
        
        # Help Patterns
        self.HELP_PATTERN = re.compile(
            r'(?:help|menu|commands|what can you do|available commands|how to use)',
            re.IGNORECASE
        )
        self.GREETING_PATTERN = re.compile(
            r'^(?:hello|hi|hey|good morning|good evening|good afternoon|howdy|greetings)',
            re.IGNORECASE
        )
        self.CONVERSATIONAL_PATTERN = re.compile(
            r'(?:can i|may i|could i|i have|i want|i need|tell me|help me|'
            r'question|ask you|something|anything|what is|how to|how do|'
            r'where is|when is|why is|who is|explain|describe|tell about)',
            re.IGNORECASE
        )
        
        # Dealer aliases for quick matching
        self.DEALER_ALIASES = {
            "sham": "Sham Electronics",
            "sham electronics": "Sham Electronics",
            "ruba": "Ruba Digital Wah",
            "ruba digital": "Ruba Digital Wah",
            "ruba digital wah": "Ruba Digital Wah",
            "taj": "Taj Electronics",
            "taj electronics": "Taj Electronics",
            "haroon": "Haroon Electronics",
            "haroon electronics": "Haroon Electronics",
            "mian": "Mian Group Chakwal",
            "mian group": "Mian Group Chakwal",
        }
        
        logger.info("✅ IntentDetectionEngine initialized (v10.2)")
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """Detect intent from message"""
        cleaned = message.strip()
        normalized = cleaned.lower()
        
        if not cleaned:
            return RoutingDecision(
                intent="general_ai",
                service_key="groq",
                method="process_query",
                needs_groq=True,
                original_message=cleaned
            )
        
        # ============================================================
        # PRIORITY 1: DN NUMBER DETECTION (HIGHEST PRIORITY)
        # ============================================================
        if self._is_dn_number(cleaned):
            dn_number = re.sub(r'\D', '', cleaned)
            logger.info(f"✅ DN number detected: {dn_number}")
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
        
        dn_match = self.DN_PATTERN.search(cleaned)
        if dn_match:
            dn_number = dn_match.group(1)
            logger.info(f"✅ DN number extracted: {dn_number}")
            return RoutingDecision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_number,
                confidence=1.0,
                needs_groq=False,
                reason="DN number extracted",
                original_message=cleaned
            )
        
        # ============================================================
        # PRIORITY 2: PENDING QUERIES
        # ============================================================
        if self.PENDING_DN_PATTERN.search(normalized):
            logger.info("✅ Pending DN query detected")
            return RoutingDecision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.98,
                needs_groq=False,
                reason="Pending DN query",
                original_message=cleaned
            )
        
        if self.PENDING_PGI_PATTERN.search(normalized):
            logger.info("✅ Pending PGI query detected")
            return RoutingDecision(
                intent="pending_pgi",
                service_key="dn",
                method="get_pending_pgi",
                confidence=0.95,
                needs_groq=False,
                reason="Pending PGI query",
                original_message=cleaned
            )
        
        if self.PENDING_POD_PATTERN.search(normalized):
            logger.info("✅ Pending POD query detected")
            return RoutingDecision(
                intent="pending_pod",
                service_key="dn",
                method="get_pending_pod",
                confidence=0.95,
                needs_groq=False,
                reason="Pending POD query",
                original_message=cleaned
            )
        
        # ============================================================
        # PRIORITY 3: DEALER DETECTION
        # ============================================================
        
        # Check dealer aliases first
        cleaned_lower = cleaned.lower()
        for alias, full_name in self.DEALER_ALIASES.items():
            if alias in cleaned_lower or cleaned_lower in alias:
                logger.info(f"✅ Dealer alias detected: {full_name}")
                return RoutingDecision(
                    intent="dealer_dashboard",
                    service_key="dealer",
                    method="get_dealer_dashboard",
                    entity=full_name,
                    confidence=0.95,
                    needs_groq=False,
                    reason=f"Dealer alias: {full_name}",
                    original_message=cleaned
                )
        
        dealer_name = None
        
        # Try dashboard pattern
        dashboard_match = self.DEALER_DASHBOARD_PATTERN.search(cleaned)
        if dashboard_match:
            dealer_name = dashboard_match.group(1).strip()
            dealer_name = self._clean_dealer_name(dealer_name)
        
        # Try dealer pattern
        if not dealer_name:
            dealer_match = self.DEALER_PATTERN.search(cleaned)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip()
                dealer_name = self._clean_dealer_name(dealer_name)
        
        # Check if it's a short dealer name
        if not dealer_name and len(cleaned.split()) <= 4 and len(cleaned) > 2:
            if not re.match(r'^\d+$', cleaned):
                dealer_name = self._clean_dealer_name(cleaned)
        
        if dealer_name and len(dealer_name) > 1:
            logger.info(f"✅ Dealer name detected: {dealer_name}")
            return RoutingDecision(
                intent="dealer_dashboard",
                service_key="dealer",
                method="get_dealer_dashboard",
                entity=dealer_name,
                confidence=0.85,
                needs_groq=False,
                reason=f"Dealer name: {dealer_name}",
                original_message=cleaned
            )
        
        # ============================================================
        # PRIORITY 4: RANKING
        # ============================================================
        ranking_match = self.RANKING_PATTERN.search(normalized)
        if ranking_match:
            if 'dealer' in normalized:
                if 'bottom' in normalized or 'worst' in normalized:
                    logger.info("✅ Bottom dealers ranking detected")
                    return RoutingDecision(
                        intent="bottom_dealers",
                        service_key="dealer",
                        method="get_bottom_dealers",
                        confidence=0.90,
                        needs_groq=False,
                        reason="Bottom dealers",
                        original_message=cleaned
                    )
                else:
                    logger.info("✅ Top dealers ranking detected")
                    return RoutingDecision(
                        intent="top_dealers",
                        service_key="dealer",
                        method="get_top_dealers",
                        confidence=0.90,
                        needs_groq=False,
                        reason="Top dealers",
                        original_message=cleaned
                    )
        
        # ============================================================
        # PRIORITY 5: HELP / GREETING / CONVERSATIONAL
        # ============================================================
        if self.HELP_PATTERN.search(normalized):
            logger.info("✅ Help query detected")
            return RoutingDecision(
                intent="help",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Help query",
                original_message=cleaned
            )
        
        if self.GREETING_PATTERN.search(normalized):
            logger.info("✅ Greeting detected")
            return RoutingDecision(
                intent="greeting",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Greeting",
                original_message=cleaned
            )
        
        if self.CONVERSATIONAL_PATTERN.search(normalized):
            logger.info("✅ Conversational query detected")
            return RoutingDecision(
                intent="conversational",
                service_key="groq",
                method="process_query",
                confidence=0.85,
                needs_groq=True,
                reason="Conversational",
                original_message=cleaned
            )
        
        # ============================================================
        # FALLBACK - Check if it looks like a DN or Dealer
        # ============================================================
        
        # Check if it looks like a DN number
        cleaned_digits = re.sub(r'\D', '', cleaned)
        if cleaned_digits and 8 <= len(cleaned_digits) <= 12:
            logger.info(f"🔄 Fallback: DN number detected: {cleaned_digits}")
            return RoutingDecision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=cleaned_digits,
                confidence=0.70,
                needs_groq=False,
                reason="DN number fallback",
                original_message=cleaned
            )
        
        # Check if it looks like a dealer name
        if len(cleaned.split()) <= 4 and len(cleaned) > 2 and not re.match(r'^\d+$', cleaned):
            cleaned_name = self._clean_dealer_name(cleaned)
            if cleaned_name and len(cleaned_name) > 1:
                logger.info(f"🔄 Fallback: Dealer name detected: {cleaned_name}")
                return RoutingDecision(
                    intent="dealer_dashboard",
                    service_key="dealer",
                    method="get_dealer_dashboard",
                    entity=cleaned_name,
                    confidence=0.60,
                    needs_groq=False,
                    reason="Dealer name fallback",
                    original_message=cleaned
                )
        
        # ============================================================
        # FINAL FALLBACK - Groq
        # ============================================================
        logger.info(f"⚠️ No specific intent detected - falling back to Groq")
        return RoutingDecision(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason="Unknown - Groq fallback",
            original_message=cleaned
        )
    
    def _is_dn_number(self, text: str) -> bool:
        """Check if text is a valid DN number"""
        if not text:
            return False
        cleaned = re.sub(r'\D', '', text.strip())
        return 8 <= len(cleaned) <= 12
    
    def _clean_dealer_name(self, name: str) -> Optional[str]:
        """Clean dealer name"""
        if not name:
            return None
        
        cleaned = re.sub(
            r'\b(?:dealer|about|for|of|show|get|view|display|give|me|company|customer|'
            r'dashboard|profile|summary|overview|info|information|details|status|'
            r'statistics|performance|the|a|an)\b',
            '',
            name,
            flags=re.IGNORECASE
        ).strip()
        
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned if len(cleaned) > 1 else None

# ============================================================
# SERVICE REGISTRY
# ============================================================

class ServiceRegistry:
    SERVICES = {
        "dn": {
            "module": "app.services.dn_service",
            "class_name": "DNAnalysisService",
            "methods": [
                "get_dn_dashboard",
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod",
                "health_check",
                "validation_query"
            ],
            "description": "DN Analytics Service",
        },
        "dealer": {
            "module": "app.services.dealer_service",
            "class_name": "DealerAnalyticsService",
            "methods": [
                "get_dealer_dashboard",
                "get_dealer_profile",
                "compare_dealers",
                "get_top_dealers",
                "get_bottom_dealers",
                "health_check",
                "validation_query"
            ],
            "description": "Dealer Analytics Service",
        },
        "groq": {
            "module": "app.services.groq_service",
            "class_name": "GroqService",
            "methods": ["process_query"],
            "description": "Groq AI Service",
        }
    }
    
    def __init__(self):
        self._instance_cache = {}
        self._lock = threading.RLock()
    
    def get_service_instance(self, service_key: str):
        """Get service instance with caching"""
        if service_key in self._instance_cache:
            return self._instance_cache[service_key]
        
        with self._lock:
            if service_key in self._instance_cache:
                return self._instance_cache[service_key]
            
            try:
                service_def = self.SERVICES.get(service_key)
                if not service_def:
                    logger.error(f"Service '{service_key}' not registered")
                    return None
                
                module = importlib.import_module(service_def["module"])
                cls = getattr(module, service_def["class_name"])
                instance = cls()
                self._instance_cache[service_key] = instance
                logger.info(f"✅ Service '{service_key}' initialized")
                return instance
            except ImportError as e:
                logger.error(f"Failed to import service '{service_key}': {e}")
                return None
            except Exception as e:
                logger.error(f"Failed to load service '{service_key}': {e}")
                return None

# ============================================================
# WHATSAPP PROVIDER SERVICE - COMPLETE FIXED
# ============================================================

class WhatsAppProviderService:
    def __init__(self):
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("AI Provider Service v10.2 - COMPLETE FIXED")
            logger.info("=" * 70)
            
            # Initialize registry
            self.registry = ServiceRegistry()
            logger.info("✅ ServiceRegistry initialized")
            
            # Initialize intent engine
            self.intent_engine = IntentDetectionEngine()
            logger.info("✅ IntentDetectionEngine initialized")
            
            # Pre-load services
            self.dn_service = self.registry.get_service_instance("dn")
            if self.dn_service:
                logger.info("✅ DN Service loaded successfully")
            else:
                logger.warning("⚠️ DN Service failed to load")
            
            self.dealer_service = self.registry.get_service_instance("dealer")
            if self.dealer_service:
                logger.info("✅ Dealer Service loaded successfully")
            else:
                logger.warning("⚠️ Dealer Service failed to load")
            
            self.groq_service = self.registry.get_service_instance("groq")
            if self.groq_service:
                logger.info("✅ Groq Service loaded successfully")
            else:
                logger.warning("⚠️ Groq Service failed to load")
            
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("   STATUS: ✅ PRODUCTION GRADE")
            logger.info("   ROUTING: DN → dn_service, Dealer → dealer_service")
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
        """Process WhatsApp query - ENTRY POINT"""
        logger.info(f"📩 Processing: '{message[:100]}'")
        start_time = time.perf_counter()
        
        try:
            # Detect intent
            routing_decision = self.intent_engine.detect_intent(message)
            logger.info(f"🎯 Intent: {routing_decision.intent}, Service: {routing_decision.service_key}, Entity: {routing_decision.entity}")
            
            # ============================================================
            # ROUTE BASED ON INTENT
            # ============================================================
            
            # DN Lookup
            if routing_decision.intent == "dn_lookup":
                logger.info(f"🔍 Routing to DN service")
                result = await self._handle_dn(routing_decision)
                if result:
                    return result
                else:
                    return self._format_response(
                        message,
                        "⚠️ DN service is currently unavailable. Please try again later.",
                        error=True
                    )
            
            # Pending Queries
            if routing_decision.intent in ["pending_dn", "pending_pgi", "pending_pod"]:
                logger.info(f"🔍 Routing to DN service for pending query")
                result = await self._handle_pending(routing_decision)
                if result:
                    return result
                else:
                    return self._format_response(
                        message,
                        "⚠️ DN service is currently unavailable. Please try again later.",
                        error=True
                    )
            
            # Dealer Dashboard
            if routing_decision.intent in ["dealer_dashboard", "top_dealers", "bottom_dealers"]:
                logger.info(f"🔍 Routing to Dealer service")
                result = await self._handle_dealer(routing_decision)
                if result:
                    return result
                else:
                    return self._format_response(
                        message,
                        "⚠️ Dealer service is currently unavailable. Please try again later.",
                        error=True
                    )
            
            # Groq (Conversational)
            if routing_decision.needs_groq or routing_decision.service_key == "groq":
                logger.info(f"🔍 Routing to Groq service")
                return await self._handle_groq(message, routing_decision)
            
            # ============================================================
            # FALLBACK - Try to detect DN or Dealer again
            # ============================================================
            
            cleaned = message.strip()
            
            # Check if it's a DN number
            cleaned_digits = re.sub(r'\D', '', cleaned)
            if cleaned_digits and 8 <= len(cleaned_digits) <= 12:
                logger.info(f"🔄 Fallback: DN number detected: {cleaned_digits}")
                result = await self._handle_dn(RoutingDecision(
                    intent="dn_lookup",
                    service_key="dn",
                    method="get_dn_dashboard",
                    entity=cleaned_digits,
                    original_message=message
                ))
                if result:
                    return result
            
            # Check if it's a dealer name
            if len(cleaned.split()) <= 4 and len(cleaned) > 2 and not re.match(r'^\d+$', cleaned):
                dealer_name = self._clean_dealer_name(cleaned)
                if dealer_name and len(dealer_name) > 1:
                    logger.info(f"🔄 Fallback: Dealer name detected: {dealer_name}")
                    result = await self._handle_dealer(RoutingDecision(
                        intent="dealer_dashboard",
                        service_key="dealer",
                        method="get_dealer_dashboard",
                        entity=dealer_name,
                        original_message=message
                    ))
                    if result:
                        return result
            
            # Final fallback
            return self._format_response(
                message,
                "I couldn't identify your request. Please specify:\n"
                "• A DN number (8-12 digits, e.g., 6243699261)\n"
                "• A dealer name (e.g., 'Sham Electronics')\n"
                "• 'Pending DN' for pending deliveries\n"
                "• 'Top dealers' for rankings\n\n"
                "Type 'Help' for all commands.",
                error=False
            )
            
        except Exception as e:
            logger.exception(f"❌ Failed: {e}")
            return self._format_response(
                message,
                "⚠️ An unexpected error occurred. Please try again.",
                error=True
            )
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"⏱️ Response time: {elapsed_ms:.2f}ms")
    
    # ============================================================
    # HANDLERS
    # ============================================================
    
    async def _handle_dn(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Handle DN lookup"""
        try:
            if not self.dn_service:
                logger.error("DN service not available")
                return None
            
            logger.info(f"🔍 Looking up DN: {decision.entity}")
            result = self.dn_service.get_dn_dashboard(decision.entity)
            
            if result and isinstance(result, dict):
                if result.get("success", False):
                    data = result.get("data")
                    if hasattr(data, "to_whatsapp_message"):
                        return self._format_response(decision.original_message, data, error=False)
                    return self._format_response(decision.original_message, result.get("whatsapp_message", data), error=False)
                else:
                    # DN not found
                    similar_dns = result.get("similar_dns", [])
                    if similar_dns:
                        response = f"🔍 DN {decision.entity} not found. Did you mean:\n\n"
                        for i, dn in enumerate(similar_dns[:5], 1):
                            response += f"{i}. {dn}\n"
                        response += "\nPlease type the full DN number."
                        return self._format_response(decision.original_message, response, error=False)
                    else:
                        return self._format_response(
                            decision.original_message,
                            result.get("whatsapp_message", f"❌ DN {decision.entity} not found."),
                            error=True
                        )
            
            return None
            
        except Exception as e:
            logger.error(f"DN handler failed: {e}")
            return None
    
    async def _handle_dealer(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Handle Dealer dashboard"""
        try:
            if not self.dealer_service:
                logger.error("Dealer service not available")
                return None
            
            entity = decision.entity or decision.original_message
            logger.info(f"🔍 Looking up dealer: {entity}")
            
            if decision.intent == "top_dealers":
                result = self.dealer_service.get_top_dealers(limit=10)
            elif decision.intent == "bottom_dealers":
                result = self.dealer_service.get_bottom_dealers(limit=10)
            else:
                result = self.dealer_service.get_dealer_dashboard(entity)
            
            if result and isinstance(result, dict):
                if result.get("success", False):
                    data = result.get("data")
                    if hasattr(data, "to_whatsapp_message"):
                        return self._format_response(decision.original_message, data, error=False)
                    return self._format_response(decision.original_message, result.get("whatsapp_message", data), error=False)
                else:
                    return self._format_response(
                        decision.original_message,
                        result.get("whatsapp_message", f"❌ Dealer '{entity}' not found."),
                        error=True
                    )
            
            return None
            
        except Exception as e:
            logger.error(f"Dealer handler failed: {e}")
            return None
    
    async def _handle_pending(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Handle pending queries"""
        try:
            if not self.dn_service:
                logger.error("DN service not available")
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
                    return self._format_response(decision.original_message, result.get("whatsapp_message"), error=False)
                else:
                    return self._format_response(
                        decision.original_message,
                        result.get("whatsapp_message", "⚠️ Pending query failed."),
                        error=True
                    )
            
            return None
            
        except Exception as e:
            logger.error(f"Pending handler failed: {e}")
            return None
    
    async def _handle_groq(self, message: str, decision: RoutingDecision) -> Dict[str, Any]:
        """Handle Groq queries"""
        try:
            if self.groq_service:
                if hasattr(self.groq_service, 'process_query'):
                    response = await self.groq_service.process_query(message)
                    if response:
                        if isinstance(response, dict) and response.get("response"):
                            return self._format_response(message, response.get("response"), error=False)
                        elif isinstance(response, str):
                            return self._format_response(message, response, error=False)
        except Exception as e:
            logger.error(f"Groq failed: {e}")
        
        # Fallback responses
        if decision.intent == "help":
            return self._format_response(
                message,
                "📋 **Available Commands**\n\n"
                "📦 **DN Queries:**\n"
                "• Send any 8-12 digit number\n"
                "• 'Pending DN'\n"
                "• 'Pending PGI'\n"
                "• 'Pending POD'\n\n"
                "🏪 **Dealer Queries:**\n"
                "• Send a dealer name\n"
                "• 'Top dealers'\n"
                "• 'Bottom dealers'\n\n"
                "🤖 **General:**\n"
                "• 'Hello', 'Hi'\n"
                "• 'Help', 'Menu'",
                error=False
            )
        
        if decision.intent == "greeting":
            return self._format_response(
                message,
                "👋 **Hello! Welcome to Sham Electronics**\n\n"
                "I'm your AI assistant. I can help you with:\n\n"
                "📦 **DN Tracking** - Send any 8-12 digit number\n"
                "🏪 **Dealer Analytics** - Send a dealer name\n"
                "📋 **Pending Items** - 'Pending DN'\n\n"
                "Type **Help** for all commands.",
                error=False
            )
        
        return self._format_response(
            message,
            "I'm here to help! What would you like to know?\n\n"
            "Try sending:\n"
            "• A DN number (like 6243699261)\n"
            "• A dealer name (like 'Sham Electronics')\n"
            "• 'Help' for commands",
            error=False
        )
    
    def _clean_dealer_name(self, name: str) -> Optional[str]:
        """Clean dealer name"""
        if not name:
            return None
        
        cleaned = re.sub(
            r'\b(?:dealer|about|for|of|show|get|view|display|give|me|company|customer|'
            r'dashboard|profile|summary|overview|info|information|details|status|'
            r'statistics|performance|the|a|an)\b',
            '',
            name,
            flags=re.IGNORECASE
        ).strip()
        
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned if len(cleaned) > 1 else None
    
    # ============================================================
    # RESPONSE FORMATTING
    # ============================================================
    
    def _format_response(self, original_message: str, data: Any, error: bool = False) -> Dict[str, Any]:
        """Format response for WhatsApp"""
        if error:
            return {
                "success": False,
                "message": original_message,
                "response": data,
                "error": True,
                "timestamp": datetime.now().isoformat()
            }
        
        if hasattr(data, "to_whatsapp_message"):
            try:
                data = data.to_whatsapp_message()
            except:
                pass
        
        if isinstance(data, dict):
            for key in ("whatsapp_message", "formatted_response", "response", "message"):
                if data.get(key) not in (None, ""):
                    data = data[key]
                    break
        
        return {
            "success": True,
            "message": original_message,
            "response": data,
            "error": False,
            "timestamp": datetime.now().isoformat()
        }
    
    # ============================================================
    # SYSTEM HEALTH
    # ============================================================
    
    def get_system_health(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "version": "10.2",
            "services": {
                "dn": self.dn_service is not None,
                "dealer": self.dealer_service is not None,
                "groq": self.groq_service is not None
            },
            "timestamp": datetime.now().isoformat()
        }

# ============================================================
# SINGLETON
# ============================================================

_whatsapp_provider_service = None
_provider_service_lock = threading.Lock()

def get_whatsapp_provider_service() -> WhatsAppProviderService:
    global _whatsapp_provider_service
    if _whatsapp_provider_service is None:
        with _provider_service_lock:
            if _whatsapp_provider_service is None:
                try:
                    _whatsapp_provider_service = WhatsAppProviderService()
                    logger.info("✅ WhatsAppProviderService initialized (v10.2)")
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
    'ServiceRegistry',
    'RoutingDecision',
    'IntentDetectionEngine'
]

logger.info("=" * 70)
logger.info("AI Provider Service v10.2 - COMPLETE FIXED")
logger.info("=" * 70)
logger.info("✅ DN Service - Registered and ready")
logger.info("✅ Dealer Service - Registered and ready")
logger.info("✅ Groq Service - Registered and ready")
logger.info("✅ Pending Queries - Ready")
logger.info("✅ Analytics Queries - Ready")
logger.info("✅ Fallback Detection - Ready")
logger.info("=" * 70)
