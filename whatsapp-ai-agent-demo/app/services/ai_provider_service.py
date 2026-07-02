"""
File: app/services/ai_provider_service.py
Version: 18.0 - PROPER DN ANALYSIS INTEGRATION

Single entry point for the WhatsApp AI agent. Deterministic requests (menu,
menu numbers, DN numbers and obvious entities) never depend on an AI provider.
Semantic Router and Groq are optional enhancements and cannot prevent startup.

FIXES:
- Proper integration with dn_analysis.py (passes dn_no parameter correctly)
- Enhanced DN validation (8-12 digits)
- Enhanced entity extraction for dealer names (Ruba Digital Wah)
- Fixed city service handling (Haripur)
- Proper DN validation and error messages
- Integrated with AI Bootstrap Service
- ALWAYS returns string responses
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
# AI BOOTSTRAP SERVICE - LAZY LOADING
# =====================================================================================================================

try:
    from app.services.ai_bootstrap_service import get_ai_bootstrap_service, warmup_ai_resources
    BOOTSTRAP_AVAILABLE = True
    # Warmup resources at startup (if not already done)
    warmup_ai_resources(include_heavy=False)
except ImportError:
    BOOTSTRAP_AVAILABLE = False
    logger.warning("AI Bootstrap Service not available")


# Semantic Router has changed its public class names across releases. Support
# current and older installations, but never make the whole application fail.
Route = None
SemanticRouter = None
HuggingFaceEncoder = None
SEMANTIC_ROUTER_AVAILABLE = False
SEMANTIC_ROUTER_IMPORT_ERROR: Optional[Exception] = None

try:
    from semantic_router import Route as _Route
    try:
        from semantic_router import SemanticRouter as _SemanticRouter
    except ImportError:  # compatibility with older semantic-router releases
        try:
            from semantic_router import Router as _SemanticRouter
        except ImportError:
            from semantic_router.layer import RouteLayer as _SemanticRouter
    from semantic_router.encoders import HuggingFaceEncoder as _HuggingFaceEncoder

    Route = _Route
    SemanticRouter = _SemanticRouter
    HuggingFaceEncoder = _HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
except Exception as exc:  # optional dependency
    SEMANTIC_ROUTER_IMPORT_ERROR = exc
    logger.warning("Semantic Router unavailable; rules and AI fallback remain active: %s", exc)


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
# SERVICE IMPORTS WITH SAFE FALLBACKS
# =====================================================================================================================

# DN Analysis Service
try:
    from app.services.dn_analysis import DNAnalysisService
    DN_ANALYSIS_AVAILABLE = True
    logger.info("✅ DNAnalysisService imported successfully")
except Exception as exc:
    logger.exception("Unable to import DNAnalysisService: %s", exc)
    DN_ANALYSIS_AVAILABLE = False

    class DNAnalysisService:  # type: ignore[no-redef]
        def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ DN service is temporarily unavailable.", "error": "DN service unavailable"}

        def get_warehouse_dashboard(self, warehouse: str) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Warehouse service is temporarily unavailable.", "error": "Warehouse service unavailable"}

        def get_pending_dns(self, limit: int = 20) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Pending DN service is temporarily unavailable.", "error": "Pending DN service unavailable"}

        def get_top_performers(self, limit: int = 10) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Performance service is temporarily unavailable.", "error": "Performance service unavailable"}


# Dealer Analytics Service
try:
    from app.services.dealer_analytics_service import DealerAnalyticsService
    DEALER_ANALYTICS_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import DealerAnalyticsService: %s", exc)
    DEALER_ANALYTICS_AVAILABLE = False

    class DealerAnalyticsService:  # type: ignore[no-redef]
        async def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Dealer service is temporarily unavailable.", "error": "Dealer service unavailable"}


# City Service
try:
    from app.services.city_service import CityService
    CITY_SERVICE_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import CityService: %s", exc)
    CITY_SERVICE_AVAILABLE = False

    class CityService:  # type: ignore[no-redef]
        async def get_city_dashboard(self, city_name: str) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ City service is temporarily unavailable.", "error": "City service unavailable"}


# Product Service
try:
    from app.services.product_service import ProductService
    PRODUCT_SERVICE_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import ProductService: %s", exc)
    PRODUCT_SERVICE_AVAILABLE = False

    class ProductService:  # type: ignore[no-redef]
        async def get_product_dashboard(self, product: str) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Product service is temporarily unavailable.", "error": "Product service unavailable"}


# National KPI Service
try:
    from app.services.national_kpi_service import NationalKPIService
    NATIONAL_KPI_AVAILABLE = True
except Exception as exc:
    logger.exception("Unable to import NationalKPIService: %s", exc)
    NATIONAL_KPI_AVAILABLE = False

    class NationalKPIService:  # type: ignore[no-redef]
        async def get_national_kpi(self) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ National KPI service is temporarily unavailable.", "error": "National KPI service unavailable"}


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
# ENHANCED DEALER SUFFIXES FOR BETTER EXTRACTION
# =====================================================================================================================

DEALER_SUFFIXES = (
    "electronics", "traders", "distributors", "foods", "group", "pvt", "ltd",
    "sons", "brothers", "enterprises", "company", "corporation", "store", "shop",
    "centre", "center", "solutions", "services", "digital", "technologies",
    "systems", "networks", "communications", "logistics", "transport",
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
# ENHANCED RESPONSE EXTRACTOR - ALWAYS RETURNS STRING
# =====================================================================================================================

def _extract_whatsapp_message(result: Any) -> str:
    """
    Extract WhatsApp message from service result.
    ALWAYS returns a string - never a dict.
    Enhanced to handle all possible return types.
    """
    if result is None:
        return "No response from service. Please try again."
    
    # If result is already a string, return it
    if isinstance(result, str):
        return result if result.strip() else "No response from service. Please try again."
    
    # If result is a dict, try multiple extraction methods
    if isinstance(result, dict):
        # Check for error message
        if result.get("error"):
            error_msg = result.get("error")
            if isinstance(error_msg, str):
                return f"⚠️ {error_msg}"
            elif isinstance(error_msg, dict):
                return f"⚠️ {error_msg.get('message', 'Service error')}"
        
        # Check for whatsapp_message
        if "whatsapp_message" in result and result["whatsapp_message"]:
            msg = result["whatsapp_message"]
            if isinstance(msg, str):
                return msg
            elif isinstance(msg, dict):
                return str(msg) if msg else "No response from service."
        
        # Check for formatted_response
        if "formatted_response" in result and result["formatted_response"]:
            return str(result["formatted_response"])
        
        # Check for message
        if "message" in result and result["message"]:
            return str(result["message"])
        
        # Check for response
        if "response" in result and result["response"]:
            return str(result["response"])
        
        # Check for data with to_whatsapp_message
        if "data" in result and result["data"]:
            data = result["data"]
            if hasattr(data, "to_whatsapp_message"):
                try:
                    msg = data.to_whatsapp_message()
                    if msg:
                        return str(msg)
                except Exception:
                    pass
            elif hasattr(data, "__str__"):
                return str(data)
        
        # Convert dict to readable format
        lines = []
        for key, value in result.items():
            if key not in ["whatsapp_message", "formatted_response", "message", "response", "data", "metadata", "success", "error"]:
                if value is not None and not key.startswith("_"):
                    try:
                        lines.append(f"{key}: {value}")
                    except Exception:
                        lines.append(f"{key}: [Unable to display]")
        if lines:
            return "\n".join(lines)
        
        # If we have data but no message, try to format it
        if "data" in result and result["data"]:
            return str(result["data"])
    
    # Last resort
    try:
        return str(result) if result else "No response from service. Please try again."
    except Exception:
        return "No response from service. Please try again."


# =====================================================================================================================
# ENHANCED ENTITY EXTRACTION
# =====================================================================================================================

def _extract_dealer_name(text: str) -> Optional[str]:
    """
    Enhanced dealer name extraction with multiple patterns.
    Handles names like "Ruba Digital Wah", "Super Trading Company", etc.
    """
    # First try: Dealer with suffix
    for suffix in DEALER_SUFFIXES:
        pattern = rf'([\w&.\'\- ]{{2,}}?\s*{suffix}\s*[\w&.\'\- ]*)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) > 2:
                return name
    
    # Second try: Company-like names
    company_patterns = [
        r'(?:dealer|show|get|view)\s+([\w&.\'\- ]{3,})',
        r'([\w&.\'\- ]{3,}?(?:digital|technologies|systems|solutions|services|logistics))',
        r'([\w&.\'\- ]{3,}?(?:trading|traders|distributors|dealers))',
        r'([\w&.\'\- ]{3,}?(?:company|corporation|enterprises))',
    ]
    
    for pattern in company_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) > 2:
                return name
    
    # Third try: Any capitalized phrase with 2+ words
    match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text)
    if match:
        return match.group(1).strip()
    
    return None


def _extract_city_name(text: str) -> Optional[str]:
    """
    Enhanced city name extraction.
    Handles single city names like "Haripur" and multi-word city names.
    """
    lowered = text.casefold()
    
    # Check exact city names first
    for city in CITY_NAMES:
        if city in lowered:
            return city.title()
    
    # Check for "city" keyword
    match = re.search(r'(?:city|town|location)\s+([\w&.\'\- ]{2,})', text, re.IGNORECASE)
    if match:
        return match.group(1).strip().title()
    
    return None


# =====================================================================================================================
# VALIDATE DN NUMBER
# =====================================================================================================================

def _is_valid_dn(dn: str) -> bool:
    """Validate DN number (8-12 digits)."""
    if not dn:
        return False
    cleaned = re.sub(r'[\s-]', '', dn)
    return cleaned.isdigit() and 8 <= len(cleaned) <= 12


def _format_dn_message(dn: str) -> str:
    """Format DN number for display."""
    if not dn:
        return "Unknown"
    cleaned = re.sub(r'[\s-]', '', dn)
    return cleaned


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

        # Only mark initialized after all mandatory state exists.
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
        
        # Try to get bootstrap resources
        self._bootstrap = None
        if BOOTSTRAP_AVAILABLE:
            try:
                self._bootstrap = get_ai_bootstrap_service()
                logger.info("✅ AI Bootstrap Service connected")
            except Exception as e:
                logger.warning(f"⚠️ Failed to connect to Bootstrap Service: {e}")
        
        logger.info("AIProviderService initialized; semantic router will load lazily")

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
            
            # Try to get from bootstrap first
            if BOOTSTRAP_AVAILABLE:
                try:
                    bootstrap = get_ai_bootstrap_service()
                    self._router = bootstrap.get_semantic_router()
                    if self._router:
                        logger.info("Semantic Router loaded from Bootstrap")
                        return
                except Exception:
                    pass
            
            # Fallback to manual initialization
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
        # Match 8-12 digit numbers
        match = re.search(r"(?<!\d)(\d{8,12})(?!\d)", compact)
        if match:
            return match.group(1)
        # Match spaced numbers (e.g., 6243 7011 97)
        match = re.search(r"(?<!\d)(\d{4}[\s-]*\d{4}[\s-]*\d{0,4})(?!\d)", compact)
        if match:
            candidate = re.sub(r"[\s-]", "", match.group(1))
            if 8 <= len(candidate) <= 12:
                return candidate
        return None

    @staticmethod
    def _menu_number(text: str) -> Optional[str]:
        match = re.fullmatch(r"\s*([0-9])(?:[.)])?\s*", text)
        return match.group(1) if match else None

    @staticmethod
    def _extract_entities(text: str) -> Dict[str, Any]:
        """Enhanced entity extraction with better dealer and city detection."""
        entities: Dict[str, Any] = {}
        
        # 1. Extract DN
        dn = AIProviderService._extract_dn(text)
        if dn:
            entities.update({"dn": dn, "dn_number": dn, "id": dn})

        # 2. Extract City (enhanced)
        city = _extract_city_name(text)
        if city:
            entities.update({"city": city, "city_name": city})

        # 3. Extract Dealer (enhanced)
        dealer = _extract_dealer_name(text)
        if dealer:
            entities.update({"dealer": dealer, "dealer_name": dealer})

        # 4. Extract Warehouse
        warehouse = re.search(r"(?:warehouse|depot|\bwh\b)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if warehouse:
            entities["warehouse"] = warehouse.group(1).strip()

        # 5. Extract Product
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
            
            # Check if it's a greeting
            if normalized.casefold() in {"hello", "hi", "salam", "hey", "good morning", "good evening"}:
                return self._decision_for_menu("0", message, entities, "greeting", reason="Greeting detected")
            
            # Check entity-based routing
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

    def show_main_menu(self) -> str:
        """Show main menu"""
        return get_main_menu()

    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **_: Any,
    ) -> str:
        """
        Process WhatsApp query and return formatted response.
        ALWAYS returns a string - never a dict.
        """
        sender = sender or sender_id
        if not message or not message.strip():
            return get_main_menu()

        logger.info("Processing WhatsApp message from %s", sender or "unknown")
        logger.info("Message: %s", message)
        
        decision = self._make_routing_decision(message)
        logger.info("Route: %s -> %s.%s (%s)", decision.intent, decision.service_file, decision.method, decision.reason)
        logger.info("Entities: %s", decision.entity)

        # Handle menu service
        if decision.service_key == "menu_service":
            return get_main_menu()

        # Handle greeting
        if decision.intent == "greeting":
            return "👋 Hello! Welcome to HPK Logistics 🏪. How can I assist you today? 📦 You can ask about *DN Tracking*, *Dealer Analytics*, or *City Analytics* 📊. What's on your mind? 🤔"

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
            method = getattr(service, decision.method)
            
            # =====================================================================================================================
            # CRITICAL FIX: Call methods with correct parameters for each service
            # =====================================================================================================================
            
            if decision.service_key == "dn_analysis":
                # DN Analysis methods expect specific parameters
                if decision.method == "get_dn_dashboard":
                    # Extract DN number from entities
                    dn_no = decision.entity.get("dn") or decision.entity.get("dn_number") or decision.entity.get("id")
                    if not dn_no:
                        return "⚠️ Please provide a valid DN number (8-12 digits)."
                    
                    # Validate DN number
                    if not _is_valid_dn(dn_no):
                        return f"⚠️ Invalid DN number '{dn_no}'. Please provide a valid 8-12 digit DN number."
                    
                    # Call with correct parameter
                    result = method(dn_no)
                
                elif decision.method == "get_warehouse_dashboard":
                    warehouse = decision.entity.get("warehouse")
                    if not warehouse:
                        return "⚠️ Please provide a warehouse name."
                    result = method(warehouse)
                
                elif decision.method == "get_pending_dns":
                    result = method()
                
                elif decision.method == "get_top_performers":
                    result = method()
                
                else:
                    # Fallback: try calling with entities dict
                    result = method(decision.entity)
            
            elif decision.service_key == "dealer_analytics":
                # Dealer Analytics expects dealer_name
                dealer_name = decision.entity.get("dealer_name") or decision.entity.get("dealer")
                if not dealer_name:
                    return "⚠️ Please provide a dealer name."
                result = await _resolve(method(dealer_name))
            
            elif decision.service_key == "city_service":
                # City Service expects city_name
                city_name = decision.entity.get("city_name") or decision.entity.get("city")
                if not city_name:
                    return "⚠️ Please provide a city name."
                result = await _resolve(method(city_name))
            
            elif decision.service_key == "product_service":
                # Product Service expects product
                product = decision.entity.get("product")
                if not product:
                    return "⚠️ Please provide a product name or code."
                result = await _resolve(method(product))
            
            elif decision.service_key == "national_kpi_service":
                # National KPI expects no parameters
                result = await _resolve(method())
            
            elif decision.service_key == "groq_service":
                # Groq Service expects message and entities
                result = await _resolve(method(message, decision.entity))
            
            else:
                # Generic fallback
                if decision.service_key == "groq_service":
                    result = await _resolve(method(message, decision.entity))
                else:
                    result = await _resolve(method(decision.entity))
            
            # Extract WhatsApp message - ALWAYS returns string
            response = _extract_whatsapp_message(result)
            
            # If response is empty or just error, provide fallback
            if not response or response.strip() == "":
                if decision.service_key == "dn_analysis":
                    dn_no = decision.entity.get("dn") or decision.entity.get("dn_number") or message
                    return f"⚠️ DN #{_format_dn_message(dn_no)} not found in PostgreSQL.\n\nPlease check the DN number and try again."
                elif decision.service_key == "dealer_analytics":
                    dealer_name = decision.entity.get("dealer_name") or decision.entity.get("dealer") or message
                    return f"⚠️ Dealer '{dealer_name}' not found.\n\nPlease check the dealer name and try again."
                elif decision.service_key == "city_service":
                    city_name = decision.entity.get("city_name") or decision.entity.get("city") or message
                    return f"⚠️ City '{city_name}' data not available.\n\nPlease try another city."
            
            return response
            
        except Exception as e:
            logger.exception("Service call failed: %s.%s", decision.service_key, decision.method)
            
            # Check if it's a Pydantic validation error (Invalid DN number)
            if "validation error" in str(e).lower() or "Invalid DN number" in str(e):
                return f"⚠️ Invalid DN number format. Please provide a valid 8-12 digit DN number."
            
            if decision.service_key == "groq_service":
                return "⚠️ AI service is temporarily unavailable. Reply *menu* to use logistics services."
            
            # Provide meaningful error based on service
            if decision.service_key == "dn_analysis":
                dn_no = decision.entity.get("dn") or decision.entity.get("dn_number") or message
                return f"⚠️ DN service error for #{_format_dn_message(dn_no)}: {str(e)[:100]}\n\nPlease check the DN number and try again."
            elif decision.service_key == "dealer_analytics":
                dealer_name = decision.entity.get("dealer_name") or decision.entity.get("dealer") or message
                return f"⚠️ Dealer service error for '{dealer_name}': {str(e)[:100]}\n\nPlease check the dealer name and try again."
            elif decision.service_key == "city_service":
                city_name = decision.entity.get("city_name") or decision.entity.get("city") or message
                return f"⚠️ City service error for '{city_name}': {str(e)[:100]}\n\nPlease try another city."
            
            return f"⚠️ {MENU_OPTIONS[decision.menu_option or '0']['name']} is temporarily unavailable. Please try again."


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
    """Backward-compatible factory used by webhook v28.2 and older code."""
    return get_ai_provider_service()


async def process_whatsapp_query(
    message: str,
    sender: Optional[str] = None,
    sender_id: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """
    Module-level function for backward compatibility.
    ALWAYS returns a string.
    """
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
        if message and message.strip().casefold() in {"hello", "hi", "salam", "hey"}:
            return "👋 Hello! Welcome to HPK Logistics 🏪. How can I assist you today?"
        return "⚠️ Service is temporarily unavailable. Reply *menu* to try again."


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
