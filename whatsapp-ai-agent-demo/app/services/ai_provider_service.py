"""
File: app/services/ai_provider_service.py
Version: 16.0 - ENTERPRISE ORCHESTRATOR WITH PROPER SERVICE INTEGRATION
Single entry point for the WhatsApp AI agent with proper service routing
"""

from __future__ import annotations

import inspect
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)

# =====================================================================================================================
# SEMANTIC ROUTER - OPTIONAL DEPENDENCY
# =====================================================================================================================

Route = None
SemanticRouter = None
HuggingFaceEncoder = None
SEMANTIC_ROUTER_AVAILABLE = False
SEMANTIC_ROUTER_IMPORT_ERROR: Optional[Exception] = None

try:
    from semantic_router import Route as _Route
    try:
        from semantic_router import SemanticRouter as _SemanticRouter
    except ImportError:
        try:
            from semantic_router import Router as _SemanticRouter
        except ImportError:
            from semantic_router.layer import RouteLayer as _SemanticRouter
    from semantic_router.encoders import HuggingFaceEncoder as _HuggingFaceEncoder

    Route = _Route
    SemanticRouter = _SemanticRouter
    HuggingFaceEncoder = _HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
except Exception as exc:
    SEMANTIC_ROUTER_IMPORT_ERROR = exc
    logger.warning("Semantic Router unavailable; rules and AI fallback remain active: %s", exc)

# =====================================================================================================================
# SERVICE IMPORTS - WITH PROPER ERROR HANDLING
# =====================================================================================================================

# DN Analysis Service
try:
    from app.services.dn_analysis import DNAnalysisService, get_dn_dashboard, get_pending_dns, get_warehouse_dashboard, get_top_performers
    DN_ANALYSIS_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import DNAnalysisService: %s", exc)
    DN_ANALYSIS_AVAILABLE = False
    
    async def get_dn_dashboard(dn_no: str) -> Dict[str, Any]:
        return {"success": False, "error": "DN service unavailable", "whatsapp_message": "⚠️ DN service is temporarily unavailable."}
    
    async def get_pending_dns(limit: int = 20) -> Dict[str, Any]:
        return {"success": False, "error": "DN service unavailable", "whatsapp_message": "⚠️ Pending DN service is temporarily unavailable."}
    
    async def get_warehouse_dashboard(warehouse: str) -> Dict[str, Any]:
        return {"success": False, "error": "DN service unavailable", "whatsapp_message": "⚠️ Warehouse service is temporarily unavailable."}
    
    async def get_top_performers(limit: int = 10) -> Dict[str, Any]:
        return {"success": False, "error": "DN service unavailable", "whatsapp_message": "⚠️ Performance service is temporarily unavailable."}

# Dealer Analytics Service
try:
    from app.services.dealer_analytics_service import DealerAnalyticsService, get_dealer_analytics_service
    DEALER_ANALYTICS_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import DealerAnalyticsService: %s", exc)
    DEALER_ANALYTICS_AVAILABLE = False
    
    async def get_dealer_dashboard(dealer_name: str) -> Dict[str, Any]:
        return {"success": False, "error": "Dealer service unavailable", "whatsapp_message": "⚠️ Dealer service is temporarily unavailable."}

# City Service
try:
    from app.services.city_service import CityAnalyticsService, get_city_analytics_service
    CITY_SERVICE_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import CityService: %s", exc)
    CITY_SERVICE_AVAILABLE = False
    
    async def get_city_dashboard(city_name: str) -> Dict[str, Any]:
        return {"success": False, "error": "City service unavailable", "whatsapp_message": "⚠️ City service is temporarily unavailable."}

# Product Service
try:
    from app.services.product_service import ProductService
    PRODUCT_SERVICE_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import ProductService: %s", exc)
    PRODUCT_SERVICE_AVAILABLE = False
    
    async def get_product_dashboard(product: str) -> Dict[str, Any]:
        return {"success": False, "error": "Product service unavailable", "whatsapp_message": "⚠️ Product service is temporarily unavailable."}

# National KPI Service
try:
    from app.services.national_kpi_service import NationalKPIService
    NATIONAL_KPI_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import NationalKPIService: %s", exc)
    NATIONAL_KPI_AVAILABLE = False
    
    async def get_national_kpi() -> Dict[str, Any]:
        return {"success": False, "error": "National KPI service unavailable", "whatsapp_message": "⚠️ National KPI service is temporarily unavailable."}

# Groq Service
try:
    from app.services.groq_service import GroqService
    GROQ_SERVICE_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import GroqService: %s", exc)
    GROQ_SERVICE_AVAILABLE = False
    
    async def process_ai_query(message: str) -> str:
        return get_main_menu()

# =====================================================================================================================
# MAIN MENU
# =====================================================================================================================

def get_main_menu() -> str:
    return """🤖 HPK Logistics AI Assistant

0️⃣ Main Menu
1️⃣ DN Delivery Menu
2️⃣ Dealer Analytics Menu
3️⃣ City Analytics Menu
4️⃣ Warehouse Dashboard Menu
5️⃣ Product Analytics Menu
6️⃣ National KPI Menu
7️⃣ Pending DN Menu
8️⃣ Top Performers Menu
9️⃣ AI Query Menu

Reply with menu number."""

# =====================================================================================================================
# MENU CONFIGURATION
# =====================================================================================================================

MENU_OPTIONS: Dict[str, Dict[str, Any]] = {
    "0": {"name": "Main Menu", "service_key": "menu_service", "method": "show_main_menu"},
    "1": {"name": "DN Delivery", "service_key": "dn_analysis", "method": "get_dn_dashboard"},
    "2": {"name": "Dealer Analytics", "service_key": "dealer_analytics", "method": "get_dealer_dashboard"},
    "3": {"name": "City Analytics", "service_key": "city_service", "method": "get_city_dashboard"},
    "4": {"name": "Warehouse Dashboard", "service_key": "dn_analysis", "method": "get_warehouse_dashboard"},
    "5": {"name": "Product Analytics", "service_key": "product_service", "method": "get_product_dashboard"},
    "6": {"name": "National KPI", "service_key": "national_kpi", "method": "get_national_kpi"},
    "7": {"name": "Pending DN", "service_key": "dn_analysis", "method": "get_pending_dns"},
    "8": {"name": "Top Performers", "service_key": "dn_analysis", "method": "get_top_performers"},
    "9": {"name": "AI Query", "service_key": "groq_service", "method": "process_ai_query"},
}

INTENT_TO_MENU = {
    "dn_lookup": "1", "dn_status": "1", "dn_history": "1", "dn_summary": "1",
    "dealer_dashboard": "2", "dealer_revenue": "2", "dealer_pending": "2",
    "city_dashboard": "3", "city_revenue": "3", "city_pending": "3",
    "warehouse_dashboard": "4", "warehouse_revenue": "4", "warehouse_pending": "4",
    "product_dashboard": "5", "top_products": "5",
    "national_kpi": "6", "national_revenue": "6", "national_units": "6",
    "pending_dns": "7", "pending_pgi": "7", "pending_pod": "7",
    "top_performers": "8",
    "help": "0", "menu": "0", "greeting": "0",
}

# =====================================================================================================================
# ROUTING UTTERANCES
# =====================================================================================================================

ROUTE_UTTERANCES: Dict[str, List[str]] = {
    "dn_lookup": ["show dn", "track dn", "delivery note", "dn status", "check delivery"],
    "dn_history": ["dn history", "delivery history", "dn timeline"],
    "pending_dns": ["pending dns", "pending deliveries", "undelivered dns"],
    "dealer_dashboard": ["dealer dashboard", "dealer performance", "show dealer"],
    "dealer_revenue": ["dealer revenue", "dealer sales"],
    "city_dashboard": ["city dashboard", "city performance", "show city"],
    "city_revenue": ["city revenue", "city sales"],
    "warehouse_dashboard": ["warehouse dashboard", "warehouse performance", "show warehouse"],
    "product_dashboard": ["product dashboard", "product performance", "show product"],
    "top_products": ["top products", "best products"],
    "national_kpi": ["national kpi", "overall performance"],
    "top_performers": ["top performers", "leaderboard"],
    "help": ["help", "what can you do", "instructions"],
    "menu": ["menu", "main menu", "options", "services"],
}

CITY_NAMES = (
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
    "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
    "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur",
)

# =====================================================================================================================
# ROUTING DECISION
# =====================================================================================================================

@dataclass
class RoutingDecision:
    intent: str
    confidence: float
    service_key: str
    method: str
    entity: Dict[str, Any]
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    menu_option: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "service_key": self.service_key,
            "method": self.method,
            "entity": self.entity,
            "requires_ai": self.requires_ai,
            "reason": self.reason,
            "original_message": self.original_message,
            "menu_option": self.menu_option,
        }

# =====================================================================================================================
# HELPERS
# =====================================================================================================================

async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value

def _safe_get(data: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Safely get value from dict with fallback."""
    if data and key in data:
        return data[key]
    return default

def _extract_response_message(result: Dict[str, Any]) -> str:
    """Extract response message from service result."""
    if not result:
        return "No response from service."
    
    if isinstance(result, dict):
        # Check for whatsapp_message first
        if "whatsapp_message" in result:
            return result["whatsapp_message"]
        if "message" in result:
            return result["message"]
        if "formatted_response" in result:
            return result["formatted_response"]
        if "response" in result:
            return result["response"]
        if "error" in result and result["error"]:
            return f"⚠️ {result['error']}"
    
    return str(result)

# =====================================================================================================================
# MAIN AI PROVIDER SERVICE
# =====================================================================================================================

class AIProviderService:
    _instance: Optional["AIProviderService"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "AIProviderService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return

        self._router: Any = None
        self._router_init_attempted = False
        self._router_lock = threading.Lock()
        self._cache: Dict[str, tuple[float, RoutingDecision]] = {}
        self._cache_ttl = 300.0
        
        # Service instances
        self.dn_service = None
        self.dealer_service = None
        self.city_service = None
        self.product_service = None
        self.national_service = None
        self.groq_service = None
        
        # Initialize services if available
        if DN_ANALYSIS_AVAILABLE:
            try:
                self.dn_service = DNAnalysisService()
                logger.info("✅ DN Analysis Service initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize DN Analysis Service: {e}")
        
        if DEALER_ANALYTICS_AVAILABLE:
            try:
                self.dealer_service = get_dealer_analytics_service()
                logger.info("✅ Dealer Analytics Service initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Dealer Analytics Service: {e}")
        
        if CITY_SERVICE_AVAILABLE:
            try:
                self.city_service = get_city_analytics_service()
                logger.info("✅ City Analytics Service initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize City Analytics Service: {e}")
        
        if PRODUCT_SERVICE_AVAILABLE:
            try:
                self.product_service = ProductService()
                logger.info("✅ Product Service initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Product Service: {e}")
        
        if NATIONAL_KPI_AVAILABLE:
            try:
                self.national_service = NationalKPIService()
                logger.info("✅ National KPI Service initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize National KPI Service: {e}")
        
        if GROQ_SERVICE_AVAILABLE:
            try:
                self.groq_service = GroqService()
                logger.info("✅ Groq Service initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Groq Service: {e}")

        self._initialized = True
        logger.info("AIProviderService initialized successfully")
        logger.info(f"  DN Analysis: {'✅' if DN_ANALYSIS_AVAILABLE else '❌'}")
        logger.info(f"  Dealer Analytics: {'✅' if DEALER_ANALYTICS_AVAILABLE else '❌'}")
        logger.info(f"  City Analytics: {'✅' if CITY_SERVICE_AVAILABLE else '❌'}")
        logger.info(f"  Product Analytics: {'✅' if PRODUCT_SERVICE_AVAILABLE else '❌'}")
        logger.info(f"  National KPI: {'✅' if NATIONAL_KPI_AVAILABLE else '❌'}")
        logger.info(f"  Groq AI: {'✅' if GROQ_SERVICE_AVAILABLE else '❌'}")
        logger.info(f"  Semantic Router: {'✅' if SEMANTIC_ROUTER_AVAILABLE else '❌'}")

    def _ensure_semantic_router(self) -> None:
        if self._router is not None or self._router_init_attempted:
            return
        with self._router_lock:
            if self._router is not None or self._router_init_attempted:
                return
            self._router_init_attempted = True
            if not SEMANTIC_ROUTER_AVAILABLE:
                logger.warning("Semantic routing disabled: %s", SEMANTIC_ROUTER_IMPORT_ERROR)
                return
            try:
                encoder = HuggingFaceEncoder()
                routes = [Route(name=name, utterances=utterances) for name, utterances in ROUTE_UTTERANCES.items()]
                try:
                    self._router = SemanticRouter(encoder=encoder, routes=routes, auto_sync="local")
                except TypeError:
                    self._router = SemanticRouter(encoder=encoder, routes=routes)
                logger.info("Semantic Router initialized with %d routes", len(routes))
            except Exception:
                self._router = None
                logger.exception("Semantic Router initialization failed")

    @staticmethod
    def _extract_dn(text: str) -> Optional[str]:
        """Extract DN number from text."""
        compact = text.strip()
        match = re.search(r"(?<!\d)(\d{8,12})(?!\d)", compact)
        if match:
            return match.group(1)
        match = re.search(r"(?<!\d)(\d{4}[\s-]*\d{4}[\s-]*\d{0,4})(?!\d)", compact)
        if match:
            candidate = re.sub(r"[\s-]", "", match.group(1))
            return candidate if 8 <= len(candidate) <= 12 else None
        return None

    @staticmethod
    def _menu_number(text: str) -> Optional[str]:
        """Extract menu number from text."""
        match = re.fullmatch(r"\s*([0-9])(?:[.)])?\s*", text)
        return match.group(1) if match else None

    @staticmethod
    def _extract_entities(text: str) -> Dict[str, Any]:
        """Extract entities from text."""
        entities: Dict[str, Any] = {}
        
        # Extract DN
        dn = AIProviderService._extract_dn(text)
        if dn:
            entities["dn_no"] = dn
            entities["dn"] = dn
            entities["dn_number"] = dn

        # Extract City
        lowered = text.casefold()
        for city in CITY_NAMES:
            if re.search(rf"\b{re.escape(city)}\b", lowered):
                entities["city"] = city.title()
                entities["city_name"] = city.title()
                break

        # Extract Dealer
        dealer = re.search(
            r"([\w&.'\- ]{2,}?(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation)(?:[\w&.'\- ]*)?)",
            text,
            re.IGNORECASE,
        )
        if dealer:
            name = dealer.group(1).strip()
            entities["dealer_name"] = name
            entities["dealer"] = name

        # Extract Warehouse
        warehouse = re.search(r"(?:warehouse|depot|\bwh\b)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if warehouse:
            entities["warehouse"] = warehouse.group(1).strip()

        # Extract Product
        product = re.search(r"(?:product|model|material|item)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if product:
            entities["product"] = product.group(1).strip()
        
        return entities

    def _decision_for_menu(self, menu_option: str, message: str, entities: Optional[Dict[str, Any]] = None, 
                          intent: Optional[str] = None, confidence: float = 1.0, reason: str = "") -> RoutingDecision:
        config = MENU_OPTIONS.get(menu_option, MENU_OPTIONS["0"])
        return RoutingDecision(
            intent=intent or config["name"].lower().replace(" ", "_"),
            confidence=confidence,
            service_key=config["service_key"],
            method=config["method"],
            entity=entities or {},
            requires_ai=config.get("requires_ai", False),
            reason=reason,
            original_message=message,
            menu_option=menu_option,
        )

    def _semantic_intent(self, message: str) -> tuple[Optional[str], float]:
        self._ensure_semantic_router()
        if self._router is None:
            return None, 0.0
        try:
            result = self._router(message) if callable(self._router) else self._router.route(message)
            if result is None:
                return None, 0.0
            return getattr(result, "name", None), float(getattr(result, "score", 1.0) or 0.0)
        except Exception:
            logger.exception("Semantic routing failed for message")
            return None, 0.0

    @staticmethod
    def _rule_intent(message: str) -> Optional[str]:
        """Cheap, dependable routing for common commands."""
        text = message.casefold()
        rules = (
            (r"\b(?:pending\s+pod|proof of delivery pending)\b", "pending_pod"),
            (r"\b(?:pending\s+pgi|goods issue pending)\b", "pending_pgi"),
            (r"\b(?:pending\s+dn|pending deliveries)\b", "pending_dns"),
            (r"\b(?:top|best)\s+performers?\b|\bleaderboard\b", "top_performers"),
            (r"\b(?:dn|delivery note)\s+(?:service|services|dashboard|status|details?)\b", "dn_lookup"),
            (r"\bdealer\s+(?:service|services|dashboard|analytics|performance)\b", "dealer_dashboard"),
            (r"\bcit(?:y|ies)\s+(?:service|services|dashboard|analytics|performance)\b", "city_dashboard"),
            (r"\bwarehouse\s+(?:service|services|dashboard|analytics|performance)\b", "warehouse_dashboard"),
            (r"\bproduct\s+(?:service|services|dashboard|analytics|performance)\b", "product_dashboard"),
            (r"\b(?:national kpi|overall performance|executive dashboard)\b", "national_kpi"),
        )
        for pattern, intent in rules:
            if re.search(pattern, text):
                return intent
        return None

    def _make_routing_decision(self, message: str) -> RoutingDecision:
        normalized = message.strip()
        cache_key = normalized.casefold()
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]

        if not normalized:
            decision = self._decision_for_menu("0", message, reason="Empty message")
        elif (dn := self._extract_dn(normalized)):
            entities = {"dn_no": dn, "dn": dn, "dn_number": dn}
            decision = self._decision_for_menu("1", message, entities, "dn_lookup", reason="DN number detected")
        elif normalized.casefold() in {"menu", "main menu", "options", "start", "back", "home", "help"}:
            decision = self._decision_for_menu("0", message, reason="Menu keyword detected")
        elif (number := self._menu_number(normalized)) is not None:
            decision = self._decision_for_menu(number, message, reason="Menu number selected")
        else:
            entities = self._extract_entities(normalized)
            if "dealer" in entities or "dealer_name" in entities:
                decision = self._decision_for_menu("2", message, entities, "dealer_dashboard", reason="Dealer entity detected")
            elif "city" in entities or "city_name" in entities:
                decision = self._decision_for_menu("3", message, entities, "city_dashboard", reason="City entity detected")
            elif "warehouse" in entities:
                decision = self._decision_for_menu("4", message, entities, "warehouse_dashboard", reason="Warehouse entity detected")
            elif "product" in entities:
                decision = self._decision_for_menu("5", message, entities, "product_dashboard", reason="Product entity detected")
            else:
                intent = self._rule_intent(normalized)
                confidence = 1.0 if intent else 0.0
                if intent is None:
                    intent, confidence = self._semantic_intent(normalized)
                menu_option = INTENT_TO_MENU.get(intent or "")
                if menu_option and confidence >= 0.30:
                    decision = self._decision_for_menu(menu_option, message, entities, intent, confidence, "Semantic route matched")
                else:
                    decision = self._decision_for_menu("9", message, entities or {"message": message}, "general_ai", max(confidence, 0.30), "AI fallback")

        self._cache[cache_key] = (time.monotonic(), decision)
        if len(self._cache) > 1000:
            self._cache.clear()
        return decision

    async def _execute_service(self, decision: RoutingDecision) -> str:
        """Execute the service method based on routing decision."""
        service_key = decision.service_key
        method_name = decision.method
        entities = decision.entity
        
        logger.info(f"Executing: {service_key}.{method_name} with entities: {entities}")

        try:
            if service_key == "menu_service":
                return get_main_menu()
            
            elif service_key == "dn_analysis":
                if method_name == "get_dn_dashboard":
                    if self.dn_service:
                        result = await _resolve(self.dn_service.get_dn_dashboard(entities.get("dn_no", "")))
                    else:
                        result = await _resolve(get_dn_dashboard(entities.get("dn_no", "")))
                elif method_name == "get_pending_dns":
                    if self.dn_service:
                        result = await _resolve(self.dn_service.get_pending_dns())
                    else:
                        result = await _resolve(get_pending_dns())
                elif method_name == "get_warehouse_dashboard":
                    if self.dn_service:
                        result = await _resolve(self.dn_service.get_warehouse_dashboard(entities.get("warehouse", "")))
                    else:
                        result = await _resolve(get_warehouse_dashboard(entities.get("warehouse", "")))
                elif method_name == "get_top_performers":
                    if self.dn_service:
                        result = await _resolve(self.dn_service.get_top_performers())
                    else:
                        result = await _resolve(get_top_performers())
                else:
                    return f"⚠️ Unknown DN method: {method_name}"
            
            elif service_key == "dealer_analytics":
                if self.dealer_service:
                    result = await _resolve(self.dealer_service.get_dealer_dashboard(entities.get("dealer_name", "")))
                else:
                    result = await _resolve(get_dealer_dashboard(entities.get("dealer_name", "")))
            
            elif service_key == "city_service":
                if self.city_service:
                    result = await _resolve(self.city_service.get_city_dashboard(entities.get("city_name", entities.get("city", ""))))
                else:
                    result = await _resolve(get_city_dashboard(entities.get("city_name", entities.get("city", ""))))
            
            elif service_key == "product_service":
                if self.product_service:
                    result = await _resolve(self.product_service.get_product_dashboard(entities.get("product", "")))
                else:
                    result = await _resolve(get_product_dashboard(entities.get("product", "")))
            
            elif service_key == "national_kpi":
                if self.national_service:
                    result = await _resolve(self.national_service.get_national_kpi())
                else:
                    result = await _resolve(get_national_kpi())
            
            elif service_key == "groq_service":
                if self.groq_service:
                    result = await _resolve(self.groq_service.process_query(decision.original_message, entities))
                else:
                    result = await _resolve(process_ai_query(decision.original_message))
            
            else:
                return f"⚠️ Unknown service: {service_key}"
            
            # Extract response message
            if isinstance(result, dict):
                response_message = _extract_response_message(result)
                if not response_message or response_message == str(result):
                    # If no message field, format the result as a readable message
                    if "data" in result and result["data"]:
                        data = result["data"]
                        if hasattr(data, "to_whatsapp_message"):
                            response_message = data.to_whatsapp_message()
                        elif hasattr(data, "__str__"):
                            response_message = str(data)
                        else:
                            response_message = str(data)
                    elif "whatsapp_message" in result:
                        response_message = result["whatsapp_message"]
                    else:
                        response_message = str(result)
                return response_message
            else:
                return str(result) if result else "No response from service."
                
        except Exception as e:
            logger.exception(f"Service execution failed: {e}")
            return f"⚠️ Service error: {str(e)}\n\nPlease try again or type 'menu' for options."

    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **_: Any,
    ) -> str:
        """Process WhatsApp message and return response."""
        sender = sender or sender_id
        request_id = str(uuid.uuid4())[:8]
        
        if not message or not message.strip():
            return get_main_menu()

        logger.info(f"[{request_id}] Processing WhatsApp message from {sender or 'unknown'}: {message}")
        decision = self._make_routing_decision(message)
        logger.info(f"[{request_id}] Route: {decision.intent} -> {decision.service_key}.{decision.method} ({decision.reason})")

        try:
            response = await self._execute_service(decision)
            logger.info(f"[{request_id}] Response sent successfully")
            return response
        except Exception as e:
            logger.exception(f"[{request_id}] Unexpected error: {e}")
            if message and message.strip().casefold() in {"menu", "main menu", "help", "start", "0"}:
                return get_main_menu()
            return f"⚠️ Service is temporarily unavailable. Reply *menu* to try again."


# =====================================================================================================================
# SINGLETON INSTANCE
# =====================================================================================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()


def get_ai_provider_service() -> AIProviderService:
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                _ai_service = AIProviderService()
    return _ai_service


def get_whatsapp_provider_service() -> AIProviderService:
    """Backward-compatible factory used by webhook."""
    return get_ai_provider_service()


# =====================================================================================================================
# MODULE-LEVEL FUNCTION - BACKWARD COMPATIBLE
# =====================================================================================================================

async def process_whatsapp_query(
    message: str,
    sender: Optional[str] = None,
    sender_id: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Module-level function for backward compatibility."""
    try:
        return await get_ai_provider_service().process_whatsapp_query(
            message=message,
            sender=sender,
            sender_id=sender_id,
            **kwargs,
        )
    except Exception:
        logger.exception("Unexpected AI provider failure")
        if message and message.strip().casefold() in {"menu", "main menu", "help", "start", "0"}:
            return get_main_menu()
        return "⚠️ Service is temporarily unavailable. Reply *menu* to try again."


# =====================================================================================================================
# EXPORTS
# =====================================================================================================================

__all__ = [
    "process_whatsapp_query",
    "get_main_menu",
    "get_ai_provider_service",
    "get_whatsapp_provider_service",
    "RoutingDecision",
    "MENU_OPTIONS",
    "INTENT_TO_MENU",
]
