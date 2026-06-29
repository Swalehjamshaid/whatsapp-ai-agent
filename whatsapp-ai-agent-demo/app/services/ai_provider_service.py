"""
File: app/services/ai_provider_service.py
Version: 8.4 - FIXED: Service Loading with Fallback
Purpose: SINGLE ENTRY POINT for all WhatsApp requests.
"""

import logging
import os
import threading
import time
import importlib
import inspect
import re
import sys
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

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
            "suggestions": self.suggestions
        }

# ============================================================
# SERVICE STATUS
# ============================================================

class ServiceStatus:
    READY = "READY"
    IN_DEVELOPMENT = "IN_DEVELOPMENT"
    NOT_STARTED = "NOT_STARTED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"

# ============================================================
# SERVICE LOADER WITH MULTIPLE FALLBACKS
# ============================================================

class ServiceLoader:
    """Load services with multiple fallback paths"""
    
    _dealer_service = None
    _dn_service = None
    _groq_service = None
    _loaded = False
    _lock = threading.RLock()
    
    @classmethod
    def load_all_services(cls):
        """Load all services with fallback paths"""
        if cls._loaded:
            return
        
        with cls._lock:
            if cls._loaded:
                return
            
            logger.info("=" * 70)
            logger.info("🔄 Loading Services...")
            logger.info("=" * 70)
            
            # Load Dealer Service
            cls._dealer_service = cls._load_dealer_service()
            
            # Load DN Service
            cls._dn_service = cls._load_dn_service()
            
            # Load Groq Service
            cls._groq_service = cls._load_groq_service()
            
            cls._loaded = True
            
            # Print status
            logger.info("")
            logger.info("   SERVICE STATUS:")
            logger.info(f"   Dealer Service: {'✅' if cls._dealer_service else '❌'} Loaded")
            logger.info(f"   DN Service: {'✅' if cls._dn_service else '❌'} Loaded")
            logger.info(f"   Groq Service: {'✅' if cls._groq_service else '⚠️'} Available")
            logger.info("")
    
    @classmethod
    def _load_dealer_service(cls):
        """Load dealer service with multiple fallbacks"""
        
        # Try path 1: Standard import
        try:
            from app.services.dealer_analytics_service import get_dealer_analytics_service
            service = get_dealer_analytics_service()
            if service:
                logger.info("✅ Dealer Service loaded (standard path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Standard import failed: {e}")
        
        # Try path 2: Relative import
        try:
            from .dealer_analytics_service import get_dealer_analytics_service
            service = get_dealer_analytics_service()
            if service:
                logger.info("✅ Dealer Service loaded (relative path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Relative import failed: {e}")
        
        # Try path 3: Direct file import
        try:
            import sys
            import os
            # Add current directory to path
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
            
            from dealer_analytics_service import get_dealer_analytics_service
            service = get_dealer_analytics_service()
            if service:
                logger.info("✅ Dealer Service loaded (direct path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Direct import failed: {e}")
        
        # Try path 4: Import from parent directory
        try:
            import sys
            import os
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            from services.dealer_analytics_service import get_dealer_analytics_service
            service = get_dealer_analytics_service()
            if service:
                logger.info("✅ Dealer Service loaded (parent path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Parent import failed: {e}")
        
        logger.error("❌ Failed to load Dealer Service")
        return None
    
    @classmethod
    def _load_dn_service(cls):
        """Load DN service with multiple fallbacks"""
        
        # Try path 1: Standard import
        try:
            from app.services.dn_analysis import get_dn_analytics_service
            service = get_dn_analytics_service()
            if service:
                logger.info("✅ DN Service loaded (standard path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Standard import failed: {e}")
        
        # Try path 2: Relative import
        try:
            from .dn_analysis import get_dn_analytics_service
            service = get_dn_analytics_service()
            if service:
                logger.info("✅ DN Service loaded (relative path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Relative import failed: {e}")
        
        # Try path 3: Direct file import
        try:
            import sys
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
            
            from dn_analysis import get_dn_analytics_service
            service = get_dn_analytics_service()
            if service:
                logger.info("✅ DN Service loaded (direct path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Direct import failed: {e}")
        
        # Try path 4: Import from parent directory
        try:
            import sys
            import os
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            from services.dn_analysis import get_dn_analytics_service
            service = get_dn_analytics_service()
            if service:
                logger.info("✅ DN Service loaded (parent path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Parent import failed: {e}")
        
        logger.error("❌ Failed to load DN Service")
        return None
    
    @classmethod
    def _load_groq_service(cls):
        """Load Groq service with multiple fallbacks"""
        
        # Try path 1: Standard import
        try:
            from app.services.groq_service import get_groq_service
            service = get_groq_service()
            if service:
                logger.info("✅ Groq Service loaded (standard path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Groq import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Groq init failed: {e}")
        
        return None
    
    @classmethod
    def get_dealer_service(cls):
        """Get dealer service instance"""
        if not cls._loaded:
            cls.load_all_services()
        return cls._dealer_service
    
    @classmethod
    def get_dn_service(cls):
        """Get DN service instance"""
        if not cls._loaded:
            cls.load_all_services()
        return cls._dn_service
    
    @classmethod
    def get_groq_service(cls):
        """Get Groq service instance"""
        if not cls._loaded:
            cls.load_all_services()
        return cls._groq_service

# ============================================================
# DEALER RESOLVER
# ============================================================

class DealerResolver:
    """Resolve dealer names from database"""
    
    _dealer_cache = {}
    _dealer_names = []
    _loaded = False
    _lock = threading.RLock()
    
    @classmethod
    def load_dealers(cls):
        """Load dealers from database"""
        if cls._loaded:
            return
        
        with cls._lock:
            if cls._loaded:
                return
            
            try:
                if not SessionLocal or not DeliveryReport:
                    logger.warning("⚠️ SessionLocal or DeliveryReport not available")
                    return
                
                session = SessionLocal()
                try:
                    dealers = session.query(
                        DeliveryReport.customer_name,
                        DeliveryReport.dealer_code,
                        DeliveryReport.customer_code
                    ).filter(
                        DeliveryReport.customer_name.isnot(None)
                    ).distinct().all()
                    
                    cls._dealer_names = [
                        {
                            "name": d.customer_name,
                            "code": d.dealer_code or "",
                            "customer_code": d.customer_code or "",
                            "normalized": cls._normalize(d.customer_name)
                        }
                        for d in dealers if d.customer_name
                    ]
                    
                    cls._loaded = True
                    logger.info(f"✅ Loaded {len(cls._dealer_names)} dealers")
                except Exception as e:
                    logger.warning(f"❌ Failed to load dealers: {e}")
                finally:
                    session.close()
            except Exception as e:
                logger.warning(f"❌ Failed to load dealers: {e}")
    
    @staticmethod
    def _normalize(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()
    
    @classmethod
    def find_dealer(cls, dealer_name: str) -> Optional[str]:
        """Find dealer by name"""
        if not dealer_name:
            return None
        
        cls.load_dealers()
        if not cls._dealer_names:
            return None
        
        normalized = cls._normalize(dealer_name)
        
        # Exact match
        for dealer in cls._dealer_names:
            if dealer["normalized"] == normalized:
                return dealer["name"]
        
        # Contains match
        for dealer in cls._dealer_names:
            if normalized in dealer["normalized"] or dealer["normalized"] in normalized:
                return dealer["name"]
        
        # Word match
        words = normalized.split()
        for word in words:
            if len(word) > 2:
                for dealer in cls._dealer_names:
                    if word in dealer["normalized"]:
                        return dealer["name"]
        
        return None
    
    @classmethod
    def find_similar(cls, dealer_name: str, limit: int = 5) -> List[str]:
        """Find similar dealers"""
        cls.load_dealers()
        if not cls._dealer_names:
            return []
        
        normalized = cls._normalize(dealer_name)
        results = []
        
        for dealer in cls._dealer_names:
            if normalized in dealer["normalized"] or dealer["normalized"] in normalized:
                results.append(dealer["name"])
            elif any(word in dealer["normalized"] for word in normalized.split() if len(word) > 2):
                results.append(dealer["name"])
            
            if len(results) >= limit:
                break
        
        return results

# ============================================================
# INTENT DETECTION ENGINE
# ============================================================

class IntentDetectionEngine:
    def __init__(self):
        self.DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
        
        self.DEALER_PATTERN = re.compile(
            r'(?:dealer|about|for|company|customer|tell me about|show me|get|view|display|give me)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.DEALER_DASHBOARD_PATTERN = re.compile(
            r'(?:dashboard|profile|summary|overview|info|information|details|status|statistics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        self.PENDING_PATTERN = re.compile(
            r'(?:pending|open|outstanding|waiting)\s*(?:dn|pgi|pod|delivery|deliveries)?',
            re.IGNORECASE
        )
        self.PENDING_DN_PATTERN = re.compile(
            r'(?:pending|open|outstanding)\s*(?:dn|dns|delivery|deliveries)',
            re.IGNORECASE
        )
        self.PENDING_PGI_PATTERN = re.compile(
            r'(?:pending|open)\s*(?:pgi|goods issue)',
            re.IGNORECASE
        )
        self.PENDING_POD_PATTERN = re.compile(
            r'(?:pending|open)\s*(?:pod|proof of delivery)',
            re.IGNORECASE
        )
        
        self.RANKING_PATTERN = re.compile(
            r'(?:top|best|highest|lowest|worst|bottom)\s+(\d+)?\s*(?:dealers?|cities?|warehouses?|products?)',
            re.IGNORECASE
        )
        self.COMPARISON_PATTERN = re.compile(
            r'(?:compare|vs|versus|and)\s+(.*?)(?:\s+and\s+|\s+vs\s+|\s+versus\s+)(.*?)(?:\?|$)',
            re.IGNORECASE
        )
        
        self.WAREHOUSE_PATTERN = re.compile(
            r'(?:warehouse|wh|depot|distribution)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.CITY_PATTERN = re.compile(
            r'(?:city|in|at|location)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.PRODUCT_PATTERN = re.compile(
            r'(?:product|model|material|item|sku)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        self.NATIONAL_KPI_PATTERN = re.compile(
            r'(?:national|pakistan|country|overall|executive|kpi dashboard|performance dashboard)',
            re.IGNORECASE
        )
        
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
        
        self.dealer_resolver = DealerResolver()
        threading.Thread(target=DealerResolver.load_dealers, daemon=True).start()
    
    def detect_intent(self, message: str) -> RoutingDecision:
        cleaned = message.strip()
        normalized = cleaned.lower()
        
        # DN Detection
        if self._is_dn_number(cleaned):
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
        
        dn_match = self.DN_PATTERN.search(cleaned)
        if dn_match:
            dn_number = dn_match.group(1)
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
        
        # Pending Detection
        if self.PENDING_DN_PATTERN.search(normalized):
            return RoutingDecision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.98,
                needs_groq=False,
                reason="Pending DN query detected",
                original_message=cleaned
            )
        
        if self.PENDING_PGI_PATTERN.search(normalized):
            return RoutingDecision(
                intent="pending_pgi",
                service_key="dn",
                method="get_pending_pgi",
                confidence=0.95,
                needs_groq=False,
                reason="Pending PGI query detected",
                original_message=cleaned
            )
        
        if self.PENDING_POD_PATTERN.search(normalized):
            return RoutingDecision(
                intent="pending_pod",
                service_key="dn",
                method="get_pending_pod",
                confidence=0.95,
                needs_groq=False,
                reason="Pending POD query detected",
                original_message=cleaned
            )
        
        if self.PENDING_PATTERN.search(normalized):
            return RoutingDecision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.90,
                needs_groq=False,
                reason="Pending query detected",
                original_message=cleaned
            )
        
        # National KPI
        if self.NATIONAL_KPI_PATTERN.search(normalized):
            return RoutingDecision(
                intent="national_kpi",
                service_key="national_kpi",
                method="get_national_kpi_dashboard",
                confidence=0.95,
                needs_groq=False,
                reason="National KPI query",
                original_message=cleaned
            )
        
        # Ranking
        ranking_result = self._detect_ranking(normalized)
        if ranking_result:
            intent, service_key, method = ranking_result
            return RoutingDecision(
                intent=intent,
                service_key=service_key,
                method=method,
                confidence=0.90,
                needs_groq=False,
                reason=f"Ranking: {intent}",
                original_message=cleaned
            )
        
        # Comparison
        comparison_match = self.COMPARISON_PATTERN.search(cleaned)
        if comparison_match:
            entity1 = comparison_match.group(1).strip()
            entity2 = comparison_match.group(2).strip()
            return RoutingDecision(
                intent="comparison",
                service_key="dealer",
                method="compare_dealers",
                entity=entity1,
                entity2=entity2,
                confidence=0.90,
                needs_groq=False,
                reason=f"Comparison: {entity1} vs {entity2}",
                original_message=cleaned
            )
        
        # Dealer Detection
        dealer_name = None
        
        dashboard_match = self.DEALER_DASHBOARD_PATTERN.search(cleaned)
        if dashboard_match:
            dealer_name = dashboard_match.group(1).strip()
        
        if not dealer_name:
            dealer_match = self.DEALER_PATTERN.search(cleaned)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip()
        
        if not dealer_name and len(cleaned.split()) <= 3 and len(cleaned) > 2:
            if not re.match(r'^\d+$', cleaned):
                dealer_name = cleaned
        
        if dealer_name:
            dealer_name = re.sub(r'\b(?:dealer|about|for|of|show|get|view|display|give|me|company|customer|dashboard|profile|summary|overview|info|information|details|status|statistics|performance|the|a|an)\b', '', dealer_name, flags=re.IGNORECASE).strip()
            
            if dealer_name and len(dealer_name) > 1:
                found_dealer = DealerResolver.find_dealer(dealer_name)
                if found_dealer:
                    return RoutingDecision(
                        intent="dealer_dashboard",
                        service_key="dealer",
                        method="get_dealer_dashboard",
                        entity=found_dealer,
                        confidence=0.95,
                        needs_groq=False,
                        reason=f"Dealer found: {found_dealer}",
                        original_message=cleaned
                    )
                else:
                    similar = DealerResolver.find_similar(dealer_name, limit=5)
                    if similar:
                        return RoutingDecision(
                            intent="dealer_suggestion",
                            service_key="dealer",
                            method="suggest_dealers",
                            entity=dealer_name,
                            suggestions=similar,
                            confidence=0.70,
                            needs_groq=False,
                            reason=f"Dealer not found, suggestions: {similar[:3]}",
                            original_message=cleaned
                        )
        
        # Warehouse/City/Product
        warehouse_match = self.WAREHOUSE_PATTERN.search(cleaned)
        if warehouse_match:
            warehouse_name = warehouse_match.group(1).strip()
            return RoutingDecision(
                intent="warehouse_dashboard",
                service_key="warehouse",
                method="get_warehouse_dashboard",
                entity=warehouse_name,
                confidence=0.90,
                needs_groq=False,
                reason=f"Warehouse: {warehouse_name}",
                original_message=cleaned
            )
        
        city_match = self.CITY_PATTERN.search(cleaned)
        if city_match:
            city_name = city_match.group(1).strip()
            return RoutingDecision(
                intent="city_dashboard",
                service_key="city",
                method="get_city_dashboard",
                entity=city_name,
                confidence=0.90,
                needs_groq=False,
                reason=f"City: {city_name}",
                original_message=cleaned
            )
        
        product_match = self.PRODUCT_PATTERN.search(cleaned)
        if product_match:
            product_name = product_match.group(1).strip()
            return RoutingDecision(
                intent="product_dashboard",
                service_key="product",
                method="get_product_dashboard",
                entity=product_name,
                confidence=0.90,
                needs_groq=False,
                reason=f"Product: {product_name}",
                original_message=cleaned
            )
        
        # Conversational
        if self.CONVERSATIONAL_PATTERN.search(normalized):
            return RoutingDecision(
                intent="conversational",
                service_key="groq",
                method="process_query",
                confidence=0.90,
                needs_groq=True,
                reason="Conversational question",
                original_message=cleaned
            )
        
        # Help / Greeting
        if self.HELP_PATTERN.search(normalized):
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
            return RoutingDecision(
                intent="greeting",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Greeting",
                original_message=cleaned
            )
        
        # Fallback
        return RoutingDecision(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason="Unknown - Groq fallback",
            original_message=cleaned
        )
    
    def _detect_ranking(self, normalized: str) -> Optional[Tuple[str, str, str]]:
        if 'top dealer' in normalized or 'best dealer' in normalized:
            if 'revenue' in normalized or 'sales' in normalized:
                return ("top_dealers_revenue", "dealer", "get_top_dealers")
            if 'unit' in normalized or 'quantity' in normalized:
                return ("top_dealers_units", "dealer", "get_top_dealers")
            return ("top_dealers", "dealer", "get_top_dealers")
        
        if 'bottom dealer' in normalized or 'worst dealer' in normalized:
            return ("bottom_dealers", "dealer", "get_bottom_dealers")
        
        return None
    
    def _is_dn_number(self, text: str) -> bool:
        if not text:
            return False
        cleaned = re.sub(r'\D', '', text.strip())
        return 8 <= len(cleaned) <= 12

# ============================================================
# WHATSAPP PROVIDER SERVICE
# ============================================================

class WhatsAppProviderService:
    def __init__(self):
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("AI Provider Service v8.4 - FIXED: Service Loading")
            logger.info("=" * 70)
            
            # Load all services
            ServiceLoader.load_all_services()
            
            # Initialize intent engine
            self.intent_engine = IntentDetectionEngine()
            logger.info("✅ IntentDetectionEngine initialized")
            
            # Get service references
            self.dealer_service = ServiceLoader.get_dealer_service()
            self.dn_service = ServiceLoader.get_dn_service()
            self.groq_service = ServiceLoader.get_groq_service()
            
            # Pre-load dealers
            threading.Thread(target=DealerResolver.load_dealers, daemon=True).start()
            
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("   STATUS: ✅ PRODUCTION GRADE")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"❌ Failed to initialize: {str(e)}")
            raise
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender_id: Optional[str] = None
    ) -> Dict[str, Any]:
        logger.info(f"📩 Processing: '{message[:100]}'")
        start_time = time.perf_counter()
        
        try:
            routing_decision = self.intent_engine.detect_intent(message)
            logger.info(f"🎯 Intent: {routing_decision.intent}, Service: {routing_decision.service_key}")
            
            # DN Lookup
            if routing_decision.intent == "dn_lookup":
                return await self._handle_dn(routing_decision)
            
            # Pending Queries
            if routing_decision.intent in ["pending_dn", "pending_pgi", "pending_pod"]:
                return await self._handle_pending(routing_decision)
            
            # Dealer Suggestions
            if routing_decision.intent == "dealer_suggestion":
                return self._format_dealer_suggestions(routing_decision)
            
            # Dealer Dashboard
            if routing_decision.intent in ["dealer_dashboard", "dealer_profile"]:
                return await self._handle_dealer(routing_decision)
            
            # Groq
            if routing_decision.needs_groq or routing_decision.service_key == "groq":
                return await self._handle_groq(message, routing_decision)
            
            # Try dealer fallback
            if routing_decision.service_key == "dealer":
                return await self._handle_dealer(routing_decision)
            
            # Try DN fallback
            if routing_decision.service_key == "dn":
                if routing_decision.intent == "dn_lookup":
                    return await self._handle_dn(routing_decision)
                else:
                    return await self._handle_pending(routing_decision)
            
            # Default
            return self._format_response(
                message,
                "I couldn't identify your request. Please specify:\n"
                "• A DN number (8-12 digits)\n"
                "• A dealer name (e.g., 'Taj Electronics')\n"
                "• A warehouse name\n"
                "• A city name\n"
                "• An analytics query (e.g., 'Top dealers')\n\n"
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
    
    async def _handle_dealer(self, decision: RoutingDecision) -> Dict[str, Any]:
        if not self.dealer_service:
            return self._format_response(
                decision.original_message,
                "⚠️ Dealer service is not available. Please try again later.",
                error=True
            )
        
        try:
            method = getattr(self.dealer_service, decision.method, None)
            if not method:
                return self._format_response(
                    decision.original_message,
                    f"⚠️ Method '{decision.method}' not found.",
                    error=True
                )
            
            if decision.entity:
                result = method(decision.entity)
            else:
                result = method()
            
            if inspect.iscoroutine(result):
                result = await result
            
            if result and isinstance(result, dict):
                if result.get("success", False):
                    return self._format_response(decision.original_message, result.get("data"), error=False)
                elif result.get("data"):
                    return self._format_response(decision.original_message, result.get("data"), error=False)
                elif result.get("whatsapp_message"):
                    return self._format_response(decision.original_message, result.get("whatsapp_message"), error=False)
            
            return self._format_response(decision.original_message, result, error=False)
        except Exception as e:
            logger.error(f"Dealer handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ Dealer lookup failed: {str(e)}",
                error=True
            )
    
    async def _handle_dn(self, decision: RoutingDecision) -> Dict[str, Any]:
        if not self.dn_service:
            return self._format_response(
                decision.original_message,
                "⚠️ DN service is not available. Please try again later.",
                error=True
            )
        
        try:
            result = self.dn_service.get_dn_dashboard(decision.entity)
            
            if result.get("success"):
                data = result.get("data")
                if hasattr(data, "to_whatsapp_message"):
                    return self._format_response(decision.original_message, data, error=False)
                return self._format_response(decision.original_message, result.get("whatsapp_message", data), error=False)
            else:
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
                        f"❌ DN {decision.entity} not found in database.",
                        error=True
                    )
        except Exception as e:
            logger.error(f"DN handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ DN lookup failed: {str(e)}",
                error=True
            )
    
    async def _handle_pending(self, decision: RoutingDecision) -> Dict[str, Any]:
        if not self.dn_service:
            return self._format_response(
                decision.original_message,
                "⚠️ DN service is not available. Please try again later.",
                error=True
            )
        
        try:
            if decision.intent == "pending_dn":
                result = self.dn_service.get_pending_dns()
            elif decision.intent == "pending_pgi":
                result = self.dn_service.get_pending_pgi()
            elif decision.intent == "pending_pod":
                result = self.dn_service.get_pending_pod()
            else:
                result = self.dn_service.get_pending_dns()
            
            if result.get("success"):
                records = result.get("records", [])
                if records:
                    response = self._format_pending_response(records, decision.intent)
                    return self._format_response(decision.original_message, response, error=False)
                else:
                    return self._format_response(
                        decision.original_message,
                        "✅ No pending items found.",
                        error=False
                    )
            else:
                return self._format_response(
                    decision.original_message,
                    f"⚠️ Pending query failed: {result.get('error', 'Unknown error')}",
                    error=True
                )
        except Exception as e:
            logger.error(f"Pending handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ Pending query failed: {str(e)}",
                error=True
            )
    
    def _format_dealer_suggestions(self, decision: RoutingDecision) -> Dict[str, Any]:
        suggestions = decision.suggestions
        
        if not suggestions:
            return self._format_response(
                decision.original_message,
                "🔍 No dealers found matching your search.\n\nPlease check the name and try again.",
                error=False
            )
        
        response = "🔍 I couldn't find exactly that dealer. Did you mean:\n\n"
        for i, name in enumerate(suggestions[:5], 1):
            response += f"{i}. {name}\n"
        
        response += "\nPlease type the full dealer name exactly as shown above."
        
        return self._format_response(decision.original_message, response, error=False)
    
    async def _handle_groq(self, message: str, decision: RoutingDecision) -> Dict[str, Any]:
        if self.groq_service:
            try:
                if hasattr(self.groq_service, 'process_query'):
                    response = await self.groq_service.process_query(message)
                    if response:
                        if isinstance(response, dict) and response.get("response"):
                            return self._format_response(message, response.get("response"), error=False)
                        elif isinstance(response, str):
                            return self._format_response(message, response, error=False)
            except Exception as e:
                logger.error(f"Groq failed: {e}")
        
        # Fallback
        if decision.intent == "conversational":
            return self._format_response(
                message,
                "👋 Of course! I'm here to help.\n\n"
                "I can help you with:\n"
                "📦 **DN Tracking** - Send any 8-12 digit number\n"
                "🏪 **Dealer Analytics** - Dealer performance\n"
                "🏭 **Warehouse Analytics** - Warehouse operations\n"
                "🏙️ **City Analytics** - City performance\n"
                "📊 **National KPIs** - Country-wide metrics\n"
                "📋 **Pending Items** - Pending DNs, PGI, POD\n\n"
                "What would you like to know?",
                error=False
            )
        
        if decision.intent == "help":
            return self._format_response(
                message,
                "📋 Available Commands\n\n"
                "📦 DN Queries:\n"
                "• Send a DN number (8-12 digits)\n"
                "• 'Pending DN', 'Pending PGI', 'Pending POD'\n\n"
                "🏪 Dealer Queries:\n"
                "• 'Dealer [name]'\n"
                "• '[Dealer name] dashboard'\n\n"
                "🏭 Warehouse Queries:\n"
                "• 'Warehouse [name]'\n\n"
                "🏙️ City Queries:\n"
                "• 'City [name]'\n\n"
                "📦 Product Queries:\n"
                "• 'Product [name]'\n\n"
                "📊 Analytics:\n"
                "• 'National KPI'\n"
                "• 'Revenue', 'Units', 'DNs'",
                error=False
            )
        
        return self._format_response(
            message,
            "I couldn't identify your request. Please specify:\n"
            "• A DN number (8-12 digits)\n"
            "• A dealer name (e.g., 'Taj Electronics')\n"
            "• A warehouse name\n"
            "• A city name\n"
            "• An analytics query (e.g., 'Top dealers')\n\n"
            "Type 'Help' for all commands.",
            error=False
        )
    
    def _format_pending_response(self, records: List, pending_type: str) -> str:
        if not records:
            return "✅ No pending items found."
        
        type_label = {
            "pending_dn": "Pending DNs",
            "pending_pgi": "Pending PGI",
            "pending_pod": "Pending POD"
        }.get(pending_type, "Pending Items")
        
        response = f"📋 {type_label}\n\n"
        for i, item in enumerate(records[:10], 1):
            response += f"{i}. DN: {item.get('dn_no')}\n"
            response += f"   Customer: {item.get('customer_name')}\n"
            if item.get('dn_create_date'):
                response += f"   Created: {item.get('dn_create_date')}\n"
            response += "\n"
        
        if len(records) > 10:
            response += f"... and {len(records) - 10} more items"
        
        return response
    
    def _format_response(self, original_message: str, data: Any, error: bool = False) -> Dict[str, Any]:
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
    
    def get_system_health(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "version": "8.4",
            "services": {
                "dealer": self.dealer_service is not None,
                "dn": self.dn_service is not None,
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
                    logger.info("✅ WhatsAppProviderService initialized (v8.4)")
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
    'ServiceStatus',
    'RoutingDecision',
    'IntentDetectionEngine',
    'DealerResolver',
    'ServiceLoader'
]

logger.info("=" * 70)
logger.info("AI Provider Service v8.4 - COMPLETE FIXED")
logger.info("=" * 70)
logger.info("✅ Service Loader with 4 fallback paths")
logger.info("✅ Dealer Service - Multiple import attempts")
logger.info("✅ DN Service - Multiple import attempts")
logger.info("✅ Detailed logging for debugging")
logger.info("=" * 70)
