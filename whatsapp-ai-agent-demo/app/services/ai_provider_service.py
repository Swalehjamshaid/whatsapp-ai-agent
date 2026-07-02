"""
File: app/services/ai_provider_service.py
Version: 18.0 - ENTERPRISE AI ORCHESTRATOR WITH FIXED IMPORTS
Single entry point for the WhatsApp AI agent. Deterministic requests (menu,
menu numbers, DN numbers and obvious entities) never depend on an AI provider.
Semantic Router and Groq are optional enhancements and cannot prevent startup.

FIXES:
- Fixed all import issues causing webhook registration failure
- Added proper error handling for all optional dependencies
- Preserved ALL original attributes and methods
- Ensures webhook routes register properly
"""

from __future__ import annotations

import inspect
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# =====================================================================================================================
# SEMANTIC ROUTER - OPTIONAL DEPENDENCY (NEVER FAILS STARTUP)
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
# DATACLASSES
# =====================================================================================================================

@dataclass
class RoutingDecision:
    intent: str
    confidence: float
    service_key: str
    service_file: str
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
            "service_file": self.service_file,
            "method": self.method,
            "entity": self.entity,
            "requires_ai": self.requires_ai,
            "reason": self.reason,
            "original_message": self.original_message,
            "menu_option": self.menu_option,
        }


# =====================================================================================================================
# SERVICE IMPORTS WITH SAFE FALLBACKS (NEVER FAILS STARTUP)
# =====================================================================================================================

# DN Analysis Service
try:
    from app.services.dn_analysis import DNAnalysisService
    DN_ANALYSIS_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import DNAnalysisService: %s", exc)
    DN_ANALYSIS_AVAILABLE = False

    class DNAnalysisService:  # type: ignore[no-redef]
        async def get_dn_dashboard(self, entities: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
            return {"whatsapp_message": "⚠️ DN service is temporarily unavailable.", "success": False}

        async def get_warehouse_dashboard(self, entities: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
            return {"whatsapp_message": "⚠️ Warehouse service is temporarily unavailable.", "success": False}

        async def get_pending_dns(self, entities: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
            return {"whatsapp_message": "⚠️ Pending DN service is temporarily unavailable.", "success": False}

        async def get_top_performers(self, entities: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
            return {"whatsapp_message": "⚠️ Performance service is temporarily unavailable.", "success": False}

# Dealer Analytics Service
try:
    from app.services.dealer_analytics_service import DealerAnalyticsService
    DEALER_ANALYTICS_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import DealerAnalyticsService: %s", exc)
    DEALER_ANALYTICS_AVAILABLE = False

    class DealerAnalyticsService:  # type: ignore[no-redef]
        async def get_dealer_dashboard(self, entities: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
            return {"whatsapp_message": "⚠️ Dealer service is temporarily unavailable.", "success": False}

# City Service
try:
    from app.services.city_service import CityService
    CITY_SERVICE_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import CityService: %s", exc)
    CITY_SERVICE_AVAILABLE = False

    class CityService:  # type: ignore[no-redef]
        async def get_city_dashboard(self, entities: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
            return {"whatsapp_message": "⚠️ City service is temporarily unavailable.", "success": False}

# Product Service
try:
    from app.services.product_service import ProductService
    PRODUCT_SERVICE_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import ProductService: %s", exc)
    PRODUCT_SERVICE_AVAILABLE = False

    class ProductService:  # type: ignore[no-redef]
        async def get_product_dashboard(self, entities: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
            return {"whatsapp_message": "⚠️ Product service is temporarily unavailable.", "success": False}

# National KPI Service
try:
    from app.services.national_kpi_service import NationalKPIService
    NATIONAL_KPI_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import NationalKPIService: %s", exc)
    NATIONAL_KPI_AVAILABLE = False

    class NationalKPIService:  # type: ignore[no-redef]
        async def get_national_kpi(self, entities: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
            return {"whatsapp_message": "⚠️ National KPI service is temporarily unavailable.", "success": False}

# Groq Service
try:
    from app.services.groq_service import GroqService
    GROQ_SERVICE_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import GroqService: %s", exc)
    GROQ_SERVICE_AVAILABLE = False

    class GroqService:  # type: ignore[no-redef]
        async def process_query(self, message: str, entities: Dict[str, Any]) -> str:
            return get_main_menu()


# =====================================================================================================================
# MENU OPTIONS
# =====================================================================================================================

MENU_OPTIONS: Dict[str, Dict[str, Any]] = {
    "0": {"name": "Main Menu", "service_key": "menu_service", "service_file": "ai_provider_service.py", "method": "show_main_menu", "requires_ai": False},
    "1": {"name": "DN Delivery", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_dn_dashboard", "requires_ai": False},
    "2": {"name": "Dealer Analytics", "service_key": "dealer_analytics", "service_file": "dealer_analytics_service.py", "method": "get_dealer_dashboard", "requires_ai": False},
    "3": {"name": "City Analytics", "service_key": "city_service", "service_file": "city_service.py", "method": "get_city_dashboard", "requires_ai": False},
    "4": {"name": "Warehouse Dashboard", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_warehouse_dashboard", "requires_ai": False},
    "5": {"name": "Product Analytics", "service_key": "product_service", "service_file": "product_service.py", "method": "get_product_dashboard", "requires_ai": False},
    "6": {"name": "National KPI", "service_key": "national_kpi_service", "service_file": "national_kpi_service.py", "method": "get_national_kpi", "requires_ai": False},
    "7": {"name": "Pending DN", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_pending_dns", "requires_ai": False},
    "8": {"name": "Top Performers", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_top_performers", "requires_ai": False},
    "9": {"name": "AI Query", "service_key": "groq_service", "service_file": "groq_service.py", "method": "process_query", "requires_ai": True},
}

INTENT_TO_MENU = {
    "dn_lookup": "1", "dn_status": "1", "dn_history": "1", "dn_summary": "1",
    "dealer_dashboard": "2", "dealer_revenue": "2", "dealer_pending": "2", "top_dealers": "2", "dealer_comparison": "2",
    "city_dashboard": "3", "city_revenue": "3", "city_pending": "3", "top_cities": "3", "city_comparison": "3",
    "warehouse_dashboard": "4", "warehouse_revenue": "4", "warehouse_pending": "4", "top_warehouses": "4",
    "product_dashboard": "5", "top_products": "5",
    "national_kpi": "6", "national_revenue": "6", "national_units": "6",
    "pending_dns": "7", "pending_pgi": "7", "pending_pod": "7",
    "top_performers": "8", "help": "0", "menu": "0", "greeting": "0",
}

ROUTE_UTTERANCES: Dict[str, List[str]] = {
    "dn_lookup": ["show dn", "track dn", "delivery note", "dn status", "check delivery note"],
    "dn_history": ["dn history", "delivery history", "dn timeline", "tracking history"],
    "dn_summary": ["dn summary", "total dns", "delivery summary", "dn statistics"],
    "pending_dns": ["pending dns", "pending deliveries", "undelivered dns", "delivery backlog"],
    "pending_pgi": ["pending pgi", "goods issue pending", "pgi not done"],
    "pending_pod": ["pending pod", "proof of delivery pending", "pod missing"],
    "dealer_dashboard": ["dealer dashboard", "dealer performance", "show dealer", "dealer details"],
    "dealer_revenue": ["dealer revenue", "dealer sales", "dealer earnings"],
    "dealer_pending": ["dealer pending", "dealer pending orders", "dealer pending dns"],
    "top_dealers": ["top dealers", "best dealers", "dealer ranking"],
    "dealer_comparison": ["compare dealers", "dealer comparison", "dealer versus dealer"],
    "city_dashboard": ["city dashboard", "city performance", "show city", "city analytics"],
    "city_revenue": ["city revenue", "city sales", "revenue by city"],
    "city_pending": ["city pending", "pending deliveries by city"],
    "top_cities": ["top cities", "best cities", "city ranking"],
    "city_comparison": ["compare cities", "city comparison", "city versus city"],
    "warehouse_dashboard": ["warehouse dashboard", "warehouse performance", "show warehouse"],
    "warehouse_revenue": ["warehouse revenue", "warehouse sales"],
    "warehouse_pending": ["warehouse pending", "pending by warehouse"],
    "top_warehouses": ["top warehouses", "best warehouses", "warehouse ranking"],
    "product_dashboard": ["product dashboard", "product performance", "show product"],
    "top_products": ["top products", "best products", "top selling products"],
    "national_kpi": ["national kpi", "overall performance", "executive dashboard"],
    "national_revenue": ["national revenue", "total revenue", "overall sales"],
    "national_units": ["national units", "total units", "overall quantity"],
    "top_performers": ["top performers", "leaderboard", "best performers"],
    "greeting": ["hello", "hi", "salam", "good morning", "good evening"],
    "help": ["help", "how does this work", "what can you do", "instructions"],
    "menu": ["menu", "main menu", "options", "services", "show menu"],
}

CITY_NAMES = (
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
    "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
    "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
    "dera ghazi khan",
)


# =====================================================================================================================
# MAIN MENU FUNCTIONS
# =====================================================================================================================

def get_main_menu() -> str:
    return (
        "📋 *AI LOGISTICS MENU*\n\n"
        "0. Main Menu\n1. DN Delivery\n2. Dealer Analytics\n"
        "3. City Analytics\n4. Warehouse Dashboard\n5. Product Analytics\n"
        "6. National KPI\n7. Pending DN\n8. Top Performers\n9. AI Query\n\n"
        "Reply with a number from 0 to 9."
    )


def get_invalid_selection_message() -> str:
    return "Invalid selection. Please choose a number from 0 to 9.\n\n" + get_main_menu()


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


# =====================================================================================================================
# RESPONSE EXTRACTOR - CRITICAL FIX FOR WHATSAPP RESPONSES
# =====================================================================================================================

def _extract_whatsapp_message(result: Any) -> str:
    """
    Extract WhatsApp message from service result with multiple fallback formats.
    This ensures we always return a valid WhatsApp message.
    
    Priority order:
    1. whatsapp_message field
    2. formatted_response field
    3. message field
    4. response field
    5. data.to_whatsapp_message()
    6. data.__str__()
    7. Convert dict to readable format
    8. str(result)
    9. Fallback message
    """
    if result is None:
        return "No response from service. Please try again."
    
    # If result is already a string, return it
    if isinstance(result, str):
        return result if result.strip() else "No response from service. Please try again."
    
    # If result is a dict, try multiple extraction methods
    if isinstance(result, dict):
        # Priority 1: whatsapp_message
        if "whatsapp_message" in result and result["whatsapp_message"]:
            msg = result["whatsapp_message"]
            if isinstance(msg, str):
                return msg
            elif isinstance(msg, dict):
                return str(msg) if msg else "No response from service."
        
        # Priority 2: formatted_response
        if "formatted_response" in result and result["formatted_response"]:
            return str(result["formatted_response"])
        
        # Priority 3: message
        if "message" in result and result["message"]:
            return str(result["message"])
        
        # Priority 4: response
        if "response" in result and result["response"]:
            return str(result["response"])
        
        # Priority 5: data with to_whatsapp_message method
        if "data" in result and result["data"]:
            data = result["data"]
            if hasattr(data, "to_whatsapp_message"):
                try:
                    msg = data.to_whatsapp_message()
                    if msg:
                        return str(msg)
                except Exception as e:
                    logger.warning(f"Failed to call to_whatsapp_message: {e}")
            elif hasattr(data, "__str__"):
                return str(data)
        
        # Priority 6: Convert dict to readable format (exclude meta fields)
        lines = []
        for key, value in result.items():
            if key not in ["whatsapp_message", "formatted_response", "message", "response", "data", "metadata"]:
                if value is not None and not key.startswith("_"):
                    try:
                        lines.append(f"{key}: {value}")
                    except Exception:
                        lines.append(f"{key}: [Unable to display]")
        if lines:
            return "\n".join(lines)
    
    # Last resort: convert to string with error handling
    try:
        return str(result) if result else "No response from service. Please try again."
    except Exception:
        return "No response from service. Please try again."


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

        # Initialize all services with safe fallbacks
        self.dn_service = DNAnalysisService()
        self.dealer_service = DealerAnalyticsService()
        self.city_service = CityService()
        self.product_service = ProductService()
        self.national_kpi_service = NationalKPIService()
        self.groq_service = GroqService()
        self._router: Any = None
        self._router_init_attempted = False
        self._router_lock = threading.Lock()
        self._cache: Dict[str, tuple[float, RoutingDecision]] = {}
        self._cache_ttl = 300.0
        self._initialized = True
        
        logger.info("=" * 60)
        logger.info("AIProviderService initialized successfully")
        logger.info(f"  DN Analysis: {'✅' if DN_ANALYSIS_AVAILABLE else '❌'}")
        logger.info(f"  Dealer Analytics: {'✅' if DEALER_ANALYTICS_AVAILABLE else '❌'}")
        logger.info(f"  City Service: {'✅' if CITY_SERVICE_AVAILABLE else '❌'}")
        logger.info(f"  Product Service: {'✅' if PRODUCT_SERVICE_AVAILABLE else '❌'}")
        logger.info(f"  National KPI: {'✅' if NATIONAL_KPI_AVAILABLE else '❌'}")
        logger.info(f"  Groq Service: {'✅' if GROQ_SERVICE_AVAILABLE else '❌'}")
        logger.info(f"  Semantic Router: {'✅' if SEMANTIC_ROUTER_AVAILABLE else '❌'}")
        logger.info("=" * 60)

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
                logger.exception("Semantic Router initialization failed; deterministic routing remains available")

    @staticmethod
    def _extract_dn(text: str) -> Optional[str]:
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
        match = re.fullmatch(r"\s*([0-9])(?:[.)])?\s*", text)
        return match.group(1) if match else None

    @staticmethod
    def _extract_entities(text: str) -> Dict[str, Any]:
        entities: Dict[str, Any] = {}
        dn = AIProviderService._extract_dn(text)
        if dn:
            entities.update({"dn": dn, "dn_number": dn, "id": dn})

        lowered = text.casefold()
        for city in CITY_NAMES:
            if re.search(rf"\b{re.escape(city)}\b", lowered):
                entities.update({"city": city.title(), "city_name": city.title()})
                break

        dealer = re.search(
            r"([\w&.'\- ]{2,}?(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation)(?:[\w&.'\- ]*)?)",
            text,
            re.IGNORECASE,
        )
        if dealer:
            name = dealer.group(1).strip()
            entities.update({"dealer": name, "dealer_name": name})

        warehouse = re.search(r"(?:warehouse|depot|\bwh\b)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if warehouse:
            entities["warehouse"] = warehouse.group(1).strip()

        product = re.search(r"(?:product|model|material|item)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if product:
            entities["product"] = product.group(1).strip()
        return entities

    @staticmethod
    def _decision_for_menu(menu_option: str, message: str, entities: Optional[Dict[str, Any]] = None, intent: Optional[str] = None, confidence: float = 1.0, reason: str = "") -> RoutingDecision:
        config = MENU_OPTIONS[menu_option]
        return RoutingDecision(
            intent=intent or config["name"].lower().replace(" ", "_"),
            confidence=confidence,
            service_key=config["service_key"],
            service_file=config["service_file"],
            method=config["method"],
            entity=entities or {},
            requires_ai=config["requires_ai"],
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
        """Cheap, dependable routing for common commands when embeddings are down."""
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
            entities = {"dn": dn, "dn_number": dn, "id": dn}
            decision = self._decision_for_menu("1", message, entities, "dn_lookup", reason="DN number detected")
        elif normalized.casefold() in {"menu", "main menu", "options", "start", "back", "home", "help"}:
            decision = self._decision_for_menu("0", message, reason="Menu keyword detected")
        elif (number := self._menu_number(normalized)) is not None:
            decision = self._decision_for_menu(number, message, reason="Menu number selected")
        else:
            entities = self._extract_entities(normalized)
            if "dealer" in entities:
                decision = self._decision_for_menu("2", message, entities, "dealer_dashboard", reason="Dealer entity detected")
            elif "city" in entities:
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

    # =====================================================================================================================
    # SHOW MAIN MENU - METHOD USED BY MENU OPTION 0
    # =====================================================================================================================

    def show_main_menu(self) -> str:
        """Show main menu - used by menu option 0"""
        return get_main_menu()

    # =====================================================================================================================
    # PROCESS WHATSAPP QUERY - MAIN ENTRY POINT
    # =====================================================================================================================

    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **_: Any,
    ) -> str:
        """
        Process WhatsApp query and return formatted response.
        Ensures a valid WhatsApp message is always returned.
        """
        sender = sender or sender_id
        
        if not message or not message.strip():
            logger.info("Empty message received, returning menu")
            return get_main_menu()

        logger.info("Processing WhatsApp message from %s", sender or "unknown")
        decision = self._make_routing_decision(message)
        logger.info("Route: %s -> %s.%s (%s)", decision.intent, decision.service_file, decision.method, decision.reason)

        # Handle menu service
        if decision.service_key == "menu_service":
            response = get_main_menu()
            logger.info(f"Returning menu response: {len(response)} chars")
            return response

        # Get service instance
        services = {
            "dn_analysis": self.dn_service,
            "dealer_analytics": self.dealer_service,
            "city_service": self.city_service,
            "product_service": self.product_service,
            "national_kpi_service": self.national_kpi_service,
            "groq_service": self.groq_service,
        }
        service = services.get(decision.service_key)
        if service is None:
            logger.error("Unknown service key: %s", decision.service_key)
            return get_invalid_selection_message()

        try:
            # Execute service method
            method = getattr(service, decision.method)
            
            # Handle different service types
            if decision.service_key == "groq_service":
                result = await _resolve(method(message, decision.entity))
            else:
                result = await _resolve(method(decision.entity))
            
            # Extract WhatsApp message from result using enterprise extractor
            whatsapp_message = _extract_whatsapp_message(result)
            
            # Validate message
            if not whatsapp_message or not whatsapp_message.strip():
                logger.warning("Service returned empty response, using fallback")
                whatsapp_message = "⚠️ No response was returned. Please try again."
            
            # Log response preview
            logger.info(f"Returning WhatsApp response: {len(whatsapp_message)} chars, preview: {whatsapp_message[:100]}...")
            
            return whatsapp_message
            
        except Exception as exc:
            logger.exception("Service call failed: %s.%s", decision.service_key, decision.method)
            
            # Return appropriate error message
            if decision.service_key == "groq_service":
                return "⚠️ AI service is temporarily unavailable. Reply *menu* to use logistics services."
            else:
                service_name = MENU_OPTIONS[decision.menu_option or '0']['name']
                return f"⚠️ {service_name} is temporarily unavailable. Please try again later."


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
                try:
                    _ai_service = AIProviderService()
                except Exception as exc:
                    logger.error(f"Failed to create AIProviderService: {exc}")
                    # Create minimal instance that can still show menu
                    _ai_service = AIProviderService.__new__(AIProviderService)
                    _ai_service._initialized = False
                    _ai_service.dn_service = DNAnalysisService()
                    _ai_service.dealer_service = DealerAnalyticsService()
                    _ai_service.city_service = CityService()
                    _ai_service.product_service = ProductService()
                    _ai_service.national_kpi_service = NationalKPIService()
                    _ai_service.groq_service = GroqService()
                    _ai_service._router = None
                    _ai_service._router_init_attempted = True
                    _ai_service._router_lock = threading.Lock()
                    _ai_service._cache = {}
                    _ai_service._cache_ttl = 300.0
                    _ai_service._initialized = True
                    logger.warning("AIProviderService running with fallback services")
    return _ai_service


def get_whatsapp_provider_service() -> AIProviderService:
    """Backward-compatible factory used by webhook v28.2 and older code."""
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
    """
    Module-level function for backward compatibility.
    Always returns a valid WhatsApp message string.
    """
    try:
        return await get_ai_provider_service().process_whatsapp_query(
            message=message,
            sender=sender,
            sender_id=sender_id,
            **kwargs,
        )
    except Exception as exc:
        logger.exception("Unexpected AI provider failure: %s", exc)
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
    "AIProviderService",
]
