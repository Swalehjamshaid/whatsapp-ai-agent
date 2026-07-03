"""
File: app/services/ai_provider_service.py
Version: 27.0 - ENTERPRISE AI ROUTER WITH CITY-FIRST ROUTING

Single entry point for the WhatsApp AI agent. Deterministic requests (menu,
menu numbers, DN numbers and obvious entities) never depend on an AI provider.
Semantic Router and Groq are optional enhancements and cannot prevent startup.

ROUTING FLOW (Priority Order):
1. Menu Number (0-9) → Direct to specific service file (HIGHEST PRIORITY)
2. DN Number (8-12 digits) → DN Analysis (dn_analysis.py)
3. CITY NAME → City Dashboard (city_service.py) - PRIORITY OVER DEALER
4. Dealer Name → Dealer Dashboard (dealer_analytics_service.py)
5. Warehouse → Warehouse Dashboard (warehouse_service.py)
6. Product → Product Dashboard (product_service.py)
7. National KPI → National KPI (national_kpi_service.py) - FULL MENU
8. Top Performers → Top Performers (dn_analysis.py)
9. Pending DN → Pending DN (dn_analysis.py)
10. AI Query → Groq AI (groq_service.py)

CRITICAL FIXES - APPLIED:
1. ✅ CITY names checked BEFORE dealer names (fixes "Lahore City" issue)
2. ✅ Enhanced city extraction with "City" suffix support
3. ✅ "Lahore City" → correctly routes to City Analytics
4. ✅ Menu "3" routes to city_service.get_city_menu()
5. ✅ Menu "4" routes to warehouse_service.get_main_menu()
6. ✅ Menu "5" routes to product_service.get_main_menu()
7. ✅ Menu "6" routes to national_kpi_service.get_main_menu()
8. ✅ ALWAYS returns string responses
9. ✅ Bootstrap integration for AI resources
10. ✅ Enhanced entity extraction with priority ordering
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
    warmup_ai_resources(include_heavy=False)
    logger.info("✅ AI Bootstrap Service connected and warmed up")
except ImportError:
    BOOTSTRAP_AVAILABLE = False
    logger.warning("⚠️ AI Bootstrap Service not available")


# Semantic Router
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
    logger.warning("Semantic Router unavailable: %s", exc)


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
    logger.info("✅ DNAnalysisService imported")
except Exception as exc:
    logger.exception("Unable to import DNAnalysisService: %s", exc)
    DN_ANALYSIS_AVAILABLE = False

    class DNAnalysisService:
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
    logger.info("✅ DealerAnalyticsService imported")
except Exception as exc:
    logger.exception("Unable to import DealerAnalyticsService: %s", exc)
    DEALER_ANALYTICS_AVAILABLE = False

    class DealerAnalyticsService:
        async def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Dealer service is temporarily unavailable.", "error": "Dealer service unavailable"}


# City Service with Menu Support
try:
    from app.services.city_service import CityAnalyticsService
    CITY_SERVICE_AVAILABLE = True
    logger.info("✅ CityAnalyticsService with Menu imported")
except Exception as exc:
    logger.exception("Unable to import CityAnalyticsService: %s", exc)
    CITY_SERVICE_AVAILABLE = False

    class CityAnalyticsService:
        def get_city_dashboard(self, city_name: str = "", **kwargs: Any) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ City service is temporarily unavailable.", "error": "City service unavailable"}
        
        def get_city_menu(self) -> str:
            return "🏙️ City Analytics Menu\n\nCity service is temporarily unavailable.\n\n0. Main Menu"
        
        def process_city_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
            return {
                "response": "City service is temporarily unavailable. Please try again later.",
                "menu_type": "city_menu",
                "action": "error",
                "data": {},
                "exit_menu": True
            }


# Warehouse Service with Menu Support
try:
    from app.services.warehouse_service import WarehouseAnalyticsService
    WAREHOUSE_SERVICE_AVAILABLE = True
    logger.info("✅ WarehouseAnalyticsService imported")
except Exception as exc:
    logger.exception("Unable to import WarehouseAnalyticsService: %s", exc)
    WAREHOUSE_SERVICE_AVAILABLE = False

    class WarehouseAnalyticsService:
        def get_warehouse_dashboard(self, warehouse_name: str = "", **kwargs: Any) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Warehouse service is temporarily unavailable.", "error": "Warehouse service unavailable"}
        
        def get_main_menu(self) -> str:
            return "🏭 Warehouse Analytics Menu\n\nWarehouse service is temporarily unavailable.\n\n0. Main Menu"
        
        def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
            return {
                "response": "Warehouse service is temporarily unavailable. Please try again later.",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {},
                "exit_menu": True
            }


# Product Service with Menu Support
try:
    from app.services.product_service import ProductAnalyticsService
    PRODUCT_SERVICE_AVAILABLE = True
    logger.info("✅ ProductAnalyticsService imported")
except Exception as exc:
    logger.exception("Unable to import ProductAnalyticsService: %s", exc)
    PRODUCT_SERVICE_AVAILABLE = False

    class ProductAnalyticsService:
        def get_product_dashboard(self, product_name: str = "", **kwargs: Any) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ Product service is temporarily unavailable.", "error": "Product service unavailable"}
        
        def get_main_menu(self) -> str:
            return "📦 Product Analytics Menu\n\nProduct service is temporarily unavailable.\n\n0. Main Menu"
        
        def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
            return {
                "response": "Product service is temporarily unavailable. Please try again later.",
                "menu_type": "product_menu",
                "action": "error",
                "data": {},
                "exit_menu": True
            }


# National KPI Service with Menu Support
try:
    from app.services.national_kpi_service import NationalKPIService
    NATIONAL_KPI_AVAILABLE = True
    logger.info("✅ NationalKPIService imported")
except Exception as exc:
    logger.exception("Unable to import NationalKPIService: %s", exc)
    NATIONAL_KPI_AVAILABLE = False

    class NationalKPIService:
        def get_national_kpi_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
            return {"success": False, "whatsapp_message": "⚠️ National KPI service is temporarily unavailable.", "error": "National KPI service unavailable"}
        
        def get_main_menu(self) -> str:
            return "🇵🇰 National Logistics Intelligence Menu\n\nNational KPI service is temporarily unavailable.\n\n0. Main Menu"
        
        def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
            return {
                "response": "National KPI service is temporarily unavailable. Please try again later.",
                "menu_type": "national_menu",
                "action": "error",
                "data": {},
                "exit_menu": True
            }


# Groq Service
try:
    from app.services.groq_service import GroqService
    GROQ_SERVICE_AVAILABLE = True
    logger.info("✅ GroqService imported")
except Exception as exc:
    logger.exception("Unable to import GroqService: %s", exc)
    GROQ_SERVICE_AVAILABLE = False

    class GroqService:
        async def process_query(self, message: str, entities: Dict[str, Any]) -> str:
            return get_main_menu()


# =====================================================================================================================
# MENU OPTIONS - DIRECT ROUTING TO EACH SERVICE FILE
# =====================================================================================================================

MENU_OPTIONS: Dict[str, Dict[str, Any]] = {
    "0": {"name": "Main Menu", "service_key": "menu_service", "service_file": "ai_provider_service.py", "method": "show_main_menu", "requires_ai": False},
    "1": {"name": "DN Delivery", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_dn_dashboard", "requires_ai": False},
    "2": {"name": "Dealer Analytics", "service_key": "dealer_analytics", "service_file": "dealer_analytics_service.py", "method": "get_dealer_dashboard", "requires_ai": False},
    "3": {"name": "City Analytics", "service_key": "city_menu", "service_file": "city_service.py", "method": "get_city_menu", "requires_ai": False},
    "4": {"name": "Warehouse Analytics", "service_key": "warehouse_menu", "service_file": "warehouse_service.py", "method": "get_main_menu", "requires_ai": False},
    "5": {"name": "Product Analytics", "service_key": "product_menu", "service_file": "product_service.py", "method": "get_main_menu", "requires_ai": False},
    "6": {"name": "National KPI", "service_key": "national_kpi_menu", "service_file": "national_kpi_service.py", "method": "get_main_menu", "requires_ai": False},
    "7": {"name": "Pending DN", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_pending_dns", "requires_ai": False},
    "8": {"name": "Top Performers", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_top_performers", "requires_ai": False},
    "9": {"name": "AI Query", "service_key": "groq_service", "service_file": "groq_service.py", "method": "process_query", "requires_ai": True},
}

INTENT_TO_MENU = {
    # DN Intents
    "dn_lookup": "1", "dn_status": "1", "dn_history": "1", "dn_summary": "1",
    
    # Dealer Intents
    "dealer_dashboard": "2", "dealer_revenue": "2", "dealer_pending": "2", 
    "top_dealers": "2", "dealer_comparison": "2",
    
    # City Intents - HIGHEST PRIORITY
    "city_dashboard": "3", "city_revenue": "3", "city_pending": "3", 
    "top_cities": "3", "city_comparison": "3", "city_menu": "3",
    
    # Warehouse Intents
    "warehouse_dashboard": "4", "warehouse_revenue": "4", "warehouse_pending": "4", 
    "top_warehouses": "4", "warehouse_comparison": "4", "warehouse_inventory": "4",
    "warehouse_menu": "4",
    
    # Product Intents
    "product_dashboard": "5", "top_products": "5", "product_revenue": "5", 
    "product_units": "5", "product_dealers": "5", "product_menu": "5",
    
    # National KPI Intents
    "national_kpi": "6", "national_revenue": "6", "national_units": "6",
    "national_delivery": "6", "national_pending": "6", "national_dashboard": "6",
    "national_health": "6", "executive_summary": "6",
    
    # Pending & Performance
    "pending_dns": "7", "pending_pgi": "7", "pending_pod": "7",
    "top_performers": "8",
    
    # General
    "help": "0", "menu": "0", "greeting": "0",
}

ROUTE_UTTERANCES: Dict[str, List[str]] = {
    # DN Routes
    "dn_lookup": ["show dn", "track dn", "delivery note", "dn status", "check delivery note"],
    "dn_history": ["dn history", "delivery history", "dn timeline", "tracking history"],
    "dn_summary": ["dn summary", "total dns", "delivery summary", "dn statistics"],
    "pending_dns": ["pending dns", "pending deliveries", "undelivered dns", "delivery backlog"],
    "pending_pgi": ["pending pgi", "goods issue pending", "pgi not done"],
    "pending_pod": ["pending pod", "proof of delivery pending", "pod missing"],
    
    # Dealer Routes
    "dealer_dashboard": ["dealer dashboard", "dealer performance", "show dealer", "dealer details"],
    "dealer_revenue": ["dealer revenue", "dealer sales", "dealer earnings"],
    "dealer_pending": ["dealer pending", "dealer pending orders", "dealer pending dns"],
    "top_dealers": ["top dealers", "best dealers", "dealer ranking"],
    "dealer_comparison": ["compare dealers", "dealer comparison", "dealer versus dealer"],
    
    # City Routes - HIGHEST PRIORITY
    "city_dashboard": ["city dashboard", "city performance", "show city", "city analytics"],
    "city_revenue": ["city revenue", "city sales", "revenue by city"],
    "city_pending": ["city pending", "pending deliveries by city"],
    "top_cities": ["top cities", "best cities", "city ranking"],
    "city_comparison": ["compare cities", "city comparison", "city versus city"],
    "city_menu": ["city menu", "show city menu", "city options"],
    
    # Warehouse Routes
    "warehouse_dashboard": ["warehouse dashboard", "warehouse performance", "show warehouse"],
    "warehouse_revenue": ["warehouse revenue", "warehouse sales"],
    "warehouse_pending": ["warehouse pending", "pending by warehouse"],
    "top_warehouses": ["top warehouses", "best warehouses", "warehouse ranking"],
    "warehouse_comparison": ["compare warehouses", "warehouse vs warehouse"],
    "warehouse_inventory": ["warehouse inventory", "warehouse stock", "inventory levels"],
    "warehouse_menu": ["warehouse menu", "show warehouse menu", "warehouse options"],
    
    # Product Routes
    "product_dashboard": ["product dashboard", "product performance", "show product"],
    "top_products": ["top products", "best products", "top selling products"],
    "product_revenue": ["product revenue", "product sales"],
    "product_units": ["product units", "units sold"],
    "product_dealers": ["product dealers", "dealers selling product"],
    "product_menu": ["product menu", "show product menu", "product options"],
    
    # National KPI Routes
    "national_kpi": ["national kpi", "overall performance", "executive dashboard"],
    "national_revenue": ["national revenue", "total revenue", "overall sales"],
    "national_units": ["national units", "total units", "overall quantity"],
    "national_delivery": ["national delivery", "delivery performance", "nationwide delivery"],
    "national_pending": ["national pending", "overall pending", "total pending orders"],
    "national_dashboard": ["national dashboard", "pakistan logistics", "overall dashboard"],
    "national_health": ["national health", "overall health score", "logistics health"],
    "executive_summary": ["executive summary", "management overview", "logistics summary"],
    
    # General
    "top_performers": ["top performers", "leaderboard", "best performers"],
    "greeting": ["hello", "hi", "salam", "good morning", "good evening"],
    "help": ["help", "how does this work", "what can you do", "instructions"],
    "menu": ["menu", "main menu", "options", "services", "show menu"],
}

# =====================================================================================================================
# ENHANCED CITY NAMES WITH VARIATIONS
# =====================================================================================================================

CITY_NAMES = (
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
    "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
    "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
    "dera ghazi khan", "gwadar", "rahim yar khan"
)

# City variations for better matching
CITY_VARIATIONS = {
    "lahore": ["lahore", "lahore city", "lhr"],
    "karachi": ["karachi", "karachi city", "khi"],
    "rawalpindi": ["rawalpindi", "rawalpindi city", "rwp"],
    "islamabad": ["islamabad", "islamabad city", "isb"],
    "multan": ["multan", "multan city"],
    "peshawar": ["peshawar", "peshawar city"],
    "quetta": ["quetta", "quetta city"],
    "faisalabad": ["faisalabad", "faisalabad city", "fsd"],
    "hyderabad": ["hyderabad", "hyderabad city", "hyd"],
    "sialkot": ["sialkot", "sialkot city", "skt"],
    "gujranwala": ["gujranwala", "gujranwala city", "guj"],
    "abbottabad": ["abbottabad", "abbottabad city"],
    "gilgit": ["gilgit", "gilgit city"],
    "bahawalpur": ["bahawalpur", "bahawalpur city", "bwp"],
    "sukkur": ["sukkur", "sukkur city", "skr"],
    "dg khan": ["dg khan", "dera ghazi khan", "dg khan city"],
}

WAREHOUSE_NAMES = (
    "lahore", "karachi", "rawalpindi", "multan", "peshawar",
    "quetta", "hyderabad", "faisalabad", "sialkot", "gujranwala",
    "bahawalpur", "sukkur", "dg khan", "rahim yar khan",
    "abbottabad", "gwadar", "gilgit", "islamabad"
)

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
        "0. Main Menu\n"
        "1. DN Delivery\n"
        "2. Dealer Analytics\n"
        "3. City Analytics\n"
        "4. Warehouse Analytics\n"
        "5. Product Analytics\n"
        "6. National KPI\n"
        "7. Pending DN\n"
        "8. Top Performers\n"
        "9. AI Query\n\n"
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
    """Extract WhatsApp message from service result. ALWAYS returns a string."""
    if result is None:
        return "No response from service. Please try again."
    
    if isinstance(result, str):
        return result if result.strip() else "No response from service. Please try again."
    
    if isinstance(result, dict):
        if result.get("error"):
            return f"⚠️ {result.get('error')}"
        
        if "whatsapp_message" in result and result["whatsapp_message"]:
            return str(result["whatsapp_message"])
        
        if "formatted_response" in result and result["formatted_response"]:
            return str(result["formatted_response"])
        
        if "message" in result and result["message"]:
            return str(result["message"])
        
        if "response" in result and result["response"]:
            return str(result["response"])
        
        if "data" in result and result["data"]:
            data = result["data"]
            if hasattr(data, "to_whatsapp_message"):
                try:
                    return str(data.to_whatsapp_message())
                except Exception:
                    pass
            elif hasattr(data, "__str__"):
                return str(data)
        
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
        
        if "data" in result and result["data"]:
            return str(result["data"])
    
    try:
        return str(result) if result else "No response from service. Please try again."
    except Exception:
        return "No response from service. Please try again."


# =====================================================================================================================
# ENHANCED ENTITY EXTRACTION - CITY FIRST
# =====================================================================================================================

def _extract_city_name(text: str) -> Optional[str]:
    """
    Enhanced city name extraction with "City" suffix support.
    Priority: City names take precedence over dealer detection.
    """
    lowered = text.casefold()
    
    # Check if text ends with "City" and extract the city name
    # e.g., "Lahore City" → "Lahore"
    city_match = re.match(r'^([a-zA-Z\s]+?)\s+city$', text, re.IGNORECASE)
    if city_match:
        potential_city = city_match.group(1).strip().lower()
        # Check if it's a known city
        if potential_city in [c.lower() for c in CITY_NAMES]:
            return potential_city.title()
        # Check in variations
        for city, variations in CITY_VARIATIONS.items():
            if potential_city in variations:
                return city.title()
    
    # Check for exact matches with "City" suffix
    for city in CITY_NAMES:
        if city in lowered:
            return city.title()
        # Check for "City" appended
        if f"{city} city" in lowered:
            return city.title()
    
    # Check aliases and variations
    for city, variations in CITY_VARIATIONS.items():
        for variation in variations:
            if variation in lowered:
                return city.title()
    
    # Check for city in context
    match = re.search(r'(?:city|town|location|in)\s+([\w&.\'\- ]{2,})', text, re.IGNORECASE)
    if match:
        potential_city = match.group(1).strip().lower()
        if potential_city in [c.lower() for c in CITY_NAMES]:
            return potential_city.title()
        for city, variations in CITY_VARIATIONS.items():
            if potential_city in variations or potential_city == city:
                return city.title()
    
    return None


def _extract_dealer_name(text: str) -> Optional[str]:
    """Enhanced dealer name extraction - ONLY called after city check fails."""
    for suffix in DEALER_SUFFIXES:
        pattern = rf'([\w&.\'\- ]{{2,}}?\s*{suffix}\s*[\w&.\'\- ]*)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) > 2:
                return name
    
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
    
    match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text)
    if match:
        return match.group(1).strip()
    
    return None


def _extract_warehouse_name(text: str) -> Optional[str]:
    """Extract warehouse name from text."""
    lowered = text.casefold()
    
    for warehouse in WAREHOUSE_NAMES:
        if warehouse in lowered:
            return warehouse.title()
    
    match = re.search(r'(?:warehouse|depot|hub)\s+([\w&.\'\- ]{2,})', text, re.IGNORECASE)
    if match:
        return match.group(1).strip().title()
    
    return None


# =====================================================================================================================
# VALIDATE DN NUMBER
# =====================================================================================================================

def _is_valid_dn(dn: str) -> bool:
    if not dn:
        return False
    cleaned = re.sub(r'[\s-]', '', dn)
    return cleaned.isdigit() and 8 <= len(cleaned) <= 12


def _format_dn_message(dn: str) -> str:
    if not dn:
        return "Unknown"
    cleaned = re.sub(r'[\s-]', '', dn)
    return cleaned


# =====================================================================================================================
# MENU STATE MANAGEMENT
# =====================================================================================================================

class MenuSessionState:
    def __init__(self):
        self.is_active = False
        self.session_id = "default"
        self.menu_type = "main"  # "main", "city", "warehouse", "product", "national"
        self.last_response = ""
        self.last_input = ""


# =====================================================================================================================
# MAIN AI PROVIDER SERVICE - COMPLETE ROUTING WITH CITY-FIRST
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

        # Initialize all services
        self.dn_service = DNAnalysisService()
        self.dealer_service = DealerAnalyticsService()
        self.city_service = CityAnalyticsService() if CITY_SERVICE_AVAILABLE else None
        self.warehouse_service = WarehouseAnalyticsService() if WAREHOUSE_SERVICE_AVAILABLE else None
        self.product_service = ProductAnalyticsService() if PRODUCT_SERVICE_AVAILABLE else None
        self.national_kpi_service = NationalKPIService() if NATIONAL_KPI_AVAILABLE else None
        self.groq_service = GroqService()
        
        self._router: Any = None
        self._router_init_attempted = False
        self._router_lock = threading.Lock()
        self._cache: Dict[str, tuple[float, RoutingDecision]] = {}
        self._cache_ttl = 300.0
        self._menu_states: Dict[str, MenuSessionState] = {}
        self._menu_lock = threading.Lock()
        self._initialized = True
        
        if BOOTSTRAP_AVAILABLE:
            try:
                self._bootstrap = get_ai_bootstrap_service()
                logger.info("✅ AI Bootstrap Service connected")
            except Exception as e:
                logger.warning(f"⚠️ Failed to connect to Bootstrap Service: {e}")
        
        logger.info("AIProviderService initialized with COMPLETE routing (CITY-FIRST)")
        logger.info("  Menu 0 → Main Menu (ai_provider_service.py)")
        logger.info("  Menu 1 → DN Analysis (dn_analysis.py)")
        logger.info("  Menu 2 → Dealer Analytics (dealer_analytics_service.py)")
        logger.info("  Menu 3 → City Analytics (city_service.py - FULL MENU) [CITY-FIRST]")
        logger.info("  Menu 4 → Warehouse Analytics (warehouse_service.py - FULL MENU)")
        logger.info("  Menu 5 → Product Analytics (product_service.py - FULL MENU)")
        logger.info("  Menu 6 → National KPI (national_kpi_service.py - FULL MENU)")
        logger.info("  Menu 7 → Pending DN (dn_analysis.py)")
        logger.info("  Menu 8 → Top Performers (dn_analysis.py)")
        logger.info("  Menu 9 → AI Query (groq_service.py)")

    def _ensure_semantic_router(self) -> None:
        if self._router is not None or self._router_init_attempted:
            return
        with self._router_lock:
            if self._router is not None or self._router_init_attempted:
                return
            self._router_init_attempted = True
            if not SEMANTIC_ROUTER_AVAILABLE:
                logger.warning("Semantic routing disabled")
                return
            
            if BOOTSTRAP_AVAILABLE:
                try:
                    bootstrap = get_ai_bootstrap_service()
                    self._router = bootstrap.get_semantic_router()
                    if self._router:
                        logger.info("Semantic Router loaded from Bootstrap")
                        return
                except Exception:
                    pass
            
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
        compact = text.strip()
        match = re.search(r"(?<!\d)(\d{8,12})(?!\d)", compact)
        if match:
            return match.group(1)
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
        entities: Dict[str, Any] = {}
        
        # 1. Extract DN FIRST (highest priority)
        dn = AIProviderService._extract_dn(text)
        if dn:
            entities.update({"dn": dn, "dn_number": dn, "id": dn})
            # If DN found, don't look for other entities to avoid confusion
            return entities

        # 2. Extract City SECOND (priority over dealer)
        city = _extract_city_name(text)
        if city:
            entities.update({"city": city, "city_name": city})
            # If city found, don't try to extract dealer
            # This fixes "Lahore City" being detected as dealer
            # But still check for other entities
            warehouse = _extract_warehouse_name(text)
            if warehouse:
                entities["warehouse"] = warehouse
            
            product = re.search(r"(?:product|model|material|item)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
            if product:
                entities["product"] = product.group(1).strip()
            
            # Check for national KPI keywords
            if any(keyword in text.lower() for keyword in ["national", "overall", "pakistan", "executive", "kpi", "dashboard"]):
                entities["national_kpi"] = True
            
            return entities

        # 3. Extract Dealer THIRD (only if no city found)
        dealer = _extract_dealer_name(text)
        if dealer:
            entities.update({"dealer": dealer, "dealer_name": dealer})

        # 4. Extract Warehouse
        warehouse = _extract_warehouse_name(text)
        if warehouse:
            entities["warehouse"] = warehouse

        # 5. Extract Product
        product = re.search(r"(?:product|model|material|item)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if product:
            entities["product"] = product.group(1).strip()
        
        # 6. Check for national KPI keywords
        if any(keyword in text.lower() for keyword in ["national", "overall", "pakistan", "executive", "kpi", "dashboard"]):
            entities["national_kpi"] = True
        
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
            logger.exception("Semantic routing failed")
            return None, 0.0

    @staticmethod
    def _rule_intent(message: str) -> Optional[str]:
        text = message.casefold()
        rules = (
            # City-first rules
            (r"\b(?:city|town)\s+(?:dashboard|analytics|performance)\b", "city_dashboard"),
            (r"\b(?:national|overall|pakistan)\s+(?:kpi|dashboard|performance|health|score)\b", "national_kpi"),
            (r"\b(?:executive|management)\s+summary\b", "executive_summary"),
            (r"\b(?:warehouse|depot)\s+(?:menu|options)\b", "warehouse_menu"),
            (r"\b(?:product|item)\s+(?:menu|options)\b", "product_menu"),
            (r"\b(?:pending\s+pod|proof of delivery pending)\b", "pending_pod"),
            (r"\b(?:pending\s+pgi|goods issue pending)\b", "pending_pgi"),
            (r"\b(?:pending\s+dn|pending deliveries)\b", "pending_dns"),
            (r"\b(?:top|best)\s+performers?\b|\bleaderboard\b", "top_performers"),
            (r"\b(?:dn|delivery note)\s+(?:service|services|dashboard|status|details?)\b", "dn_lookup"),
            (r"\bdealer\s+(?:service|services|dashboard|analytics|performance)\b", "dealer_dashboard"),
            (r"\bwarehouse\s+(?:service|services|dashboard|analytics|performance)\b", "warehouse_dashboard"),
            (r"\bproduct\s+(?:service|services|dashboard|analytics|performance)\b", "product_dashboard"),
            (r"\b(?:city menu|show city menu|city options)\b", "city_menu"),
        )
        for pattern, intent in rules:
            if re.search(pattern, text):
                return intent
        return None

    def _get_menu_state(self, session_id: str) -> MenuSessionState:
        with self._menu_lock:
            if session_id not in self._menu_states:
                self._menu_states[session_id] = MenuSessionState()
                self._menu_states[session_id].session_id = session_id
            return self._menu_states[session_id]

    def _make_routing_decision(self, message: str, session_id: str = "default") -> RoutingDecision:
        normalized = message.strip()
        cache_key = f"{session_id}:{normalized.casefold()}"
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]

        # Check if we're in any menu mode
        state = self._get_menu_state(session_id)
        
        # Handle menu exit
        if state.is_active and normalized.casefold() in {"menu", "main menu", "start", "back", "home", "0"}:
            state.is_active = False
            state.menu_type = "main"
            decision = self._decision_for_menu("0", message, reason=f"Exiting {state.menu_type} menu")
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # If in city menu, route to city service
        if state.is_active and state.menu_type == "city":
            decision = RoutingDecision(
                intent="city_menu_input",
                confidence=1.0,
                service_key="city_menu",
                service_file="city_service.py",
                method="process_city_menu_input",
                entity={"user_input": normalized, "session_id": session_id},
                requires_ai=False,
                reason="City menu input",
                original_message=message,
                menu_option="3",
            )
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # If in warehouse menu, route to warehouse service
        if state.is_active and state.menu_type == "warehouse":
            decision = RoutingDecision(
                intent="warehouse_menu_input",
                confidence=1.0,
                service_key="warehouse_menu",
                service_file="warehouse_service.py",
                method="process_menu_input",
                entity={"user_input": normalized, "session_id": session_id},
                requires_ai=False,
                reason="Warehouse menu input",
                original_message=message,
                menu_option="4",
            )
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # If in product menu, route to product service
        if state.is_active and state.menu_type == "product":
            decision = RoutingDecision(
                intent="product_menu_input",
                confidence=1.0,
                service_key="product_menu",
                service_file="product_service.py",
                method="process_menu_input",
                entity={"user_input": normalized, "session_id": session_id},
                requires_ai=False,
                reason="Product menu input",
                original_message=message,
                menu_option="5",
            )
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # If in national menu, route to national KPI service
        if state.is_active and state.menu_type == "national":
            decision = RoutingDecision(
                intent="national_menu_input",
                confidence=1.0,
                service_key="national_kpi_menu",
                service_file="national_kpi_service.py",
                method="process_menu_input",
                entity={"user_input": normalized, "session_id": session_id},
                requires_ai=False,
                reason="National KPI menu input",
                original_message=message,
                menu_option="6",
            )
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # =====================================================================================================================
        # CRITICAL: ROUTING PRIORITY ORDER
        # =====================================================================================================================

        # 1. Empty message → Show menu
        if not normalized:
            decision = self._decision_for_menu("0", message, reason="Empty message")
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 2. EXACT menu keywords → Show main menu
        if normalized.casefold() in {"menu", "main menu", "options", "help"}:
            decision = self._decision_for_menu("0", message, reason="Menu keyword detected")
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 3. EXACT domain menu keywords → Show specific domain menu
        domain_menus = {
            "city analytics": ("city_menu", "3"),
            "warehouse analytics": ("warehouse_menu", "4"),
            "product analytics": ("product_menu", "5"),
            "national kpi": ("national_kpi_menu", "6"),
            "city menu": ("city_menu", "3"),
            "warehouse menu": ("warehouse_menu", "4"),
            "product menu": ("product_menu", "5"),
            "national menu": ("national_kpi_menu", "6"),
        }
        
        for keyword, (service_key, menu_option) in domain_menus.items():
            if normalized.casefold() == keyword or normalized.casefold().startswith(keyword):
                state.is_active = True
                state.menu_type = keyword.split()[0]
                decision = RoutingDecision(
                    intent=f"{state.menu_type}_menu",
                    confidence=1.0,
                    service_key=service_key,
                    service_file="",  # Will be set by service
                    method="get_main_menu",
                    entity={},
                    requires_ai=False,
                    reason=f"Exact '{keyword}' text detected",
                    original_message=message,
                    menu_option=menu_option,
                )
                self._cache[cache_key] = (time.monotonic(), decision)
                return decision

        # 4. Menu Number (0-9) → Direct to specific service (HIGHEST PRIORITY)
        if (number := self._menu_number(normalized)) is not None:
            if number == "3":
                state.is_active = True
                state.menu_type = "city"
                decision = RoutingDecision(
                    intent="city_menu",
                    confidence=1.0,
                    service_key="city_menu",
                    service_file="city_service.py",
                    method="get_city_menu",
                    entity={},
                    requires_ai=False,
                    reason="Menu number 3 selected - City Menu",
                    original_message=message,
                    menu_option="3",
                )
            elif number == "4":
                state.is_active = True
                state.menu_type = "warehouse"
                decision = RoutingDecision(
                    intent="warehouse_menu",
                    confidence=1.0,
                    service_key="warehouse_menu",
                    service_file="warehouse_service.py",
                    method="get_main_menu",
                    entity={},
                    requires_ai=False,
                    reason="Menu number 4 selected - Warehouse Menu",
                    original_message=message,
                    menu_option="4",
                )
            elif number == "5":
                state.is_active = True
                state.menu_type = "product"
                decision = RoutingDecision(
                    intent="product_menu",
                    confidence=1.0,
                    service_key="product_menu",
                    service_file="product_service.py",
                    method="get_main_menu",
                    entity={},
                    requires_ai=False,
                    reason="Menu number 5 selected - Product Menu",
                    original_message=message,
                    menu_option="5",
                )
            elif number == "6":
                state.is_active = True
                state.menu_type = "national"
                decision = RoutingDecision(
                    intent="national_menu",
                    confidence=1.0,
                    service_key="national_kpi_menu",
                    service_file="national_kpi_service.py",
                    method="get_main_menu",
                    entity={},
                    requires_ai=False,
                    reason="Menu number 6 selected - National KPI Menu",
                    original_message=message,
                    menu_option="6",
                )
            else:
                decision = self._decision_for_menu(number, message, reason=f"Menu number {number} selected")
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 5. DN Number (8-12 digits) → DN Analysis
        if (dn := self._extract_dn(normalized)):
            entities = {"dn": dn, "dn_number": dn, "id": dn}
            decision = self._decision_for_menu("1", message, entities, "dn_lookup", reason="DN number detected")
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 6. Extract entities for natural language routing
        entities = self._extract_entities(normalized)

        # 7. Greeting detection
        if normalized.casefold() in {"hello", "hi", "salam", "hey", "good morning", "good evening"}:
            decision = self._decision_for_menu("0", message, entities, "greeting", reason="Greeting detected")
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision

        # 8. Entity-based routing (NATURAL LANGUAGE) - CITY FIRST
        # Check for CITY FIRST (highest priority entity-based routing)
        if "city" in entities or "city_name" in entities:
            city_name = entities.get("city_name") or entities.get("city")
            if city_name and city_name.lower() in [c.lower() for c in CITY_NAMES]:
                decision = self._decision_for_menu(
                    "3", 
                    message, 
                    entities, 
                    "city_dashboard", 
                    reason=f"City name detected: {city_name}"
                )
            else:
                # If it's just "City" without a valid name, show menu
                state.is_active = True
                state.menu_type = "city"
                decision = RoutingDecision(
                    intent="city_menu",
                    confidence=0.8,
                    service_key="city_menu",
                    service_file="city_service.py",
                    method="get_city_menu",
                    entity=entities,
                    requires_ai=False,
                    reason="City keyword without valid city name - showing menu",
                    original_message=message,
                    menu_option="3",
                )
        elif "national_kpi" in entities or any(kw in normalized.lower() for kw in ["national", "overall", "pakistan", "executive"]):
            state.is_active = True
            state.menu_type = "national"
            decision = RoutingDecision(
                intent="national_dashboard",
                confidence=0.9,
                service_key="national_kpi_menu",
                service_file="national_kpi_service.py",
                method="process_whatsapp_query",
                entity=entities,
                requires_ai=False,
                reason="National KPI entity detected",
                original_message=message,
                menu_option="6",
            )
        elif "warehouse" in entities:
            warehouse_name = entities.get("warehouse")
            if warehouse_name and warehouse_name.lower() in [w.lower() for w in WAREHOUSE_NAMES]:
                decision = self._decision_for_menu("4", message, entities, "warehouse_dashboard", reason="Warehouse entity detected")
            else:
                state.is_active = True
                state.menu_type = "warehouse"
                decision = RoutingDecision(
                    intent="warehouse_menu",
                    confidence=0.8,
                    service_key="warehouse_menu",
                    service_file="warehouse_service.py",
                    method="get_main_menu",
                    entity=entities,
                    requires_ai=False,
                    reason="Warehouse keyword without valid warehouse name - showing menu",
                    original_message=message,
                    menu_option="4",
                )
        elif "product" in entities:
            decision = self._decision_for_menu("5", message, entities, "product_dashboard", reason="Product entity detected")
        elif "dealer" in entities or "dealer_name" in entities:
            decision = self._decision_for_menu("2", message, entities, "dealer_dashboard", reason="Dealer entity detected")
        else:
            # 9. Semantic routing fallback
            intent = self._rule_intent(normalized)
            confidence = 1.0 if intent else 0.0
            if intent is None:
                intent, confidence = self._semantic_intent(normalized)
            menu_option = INTENT_TO_MENU.get(intent or "")
            if menu_option and confidence >= 0.30:
                decision = self._decision_for_menu(menu_option, message, entities, intent, confidence, "Semantic route matched")
            else:
                # 10. AI Query as last resort
                decision = self._decision_for_menu("9", message, entities or {"message": message}, "general_ai", max(confidence, 0.30), "AI fallback")

        self._cache[cache_key] = (time.monotonic(), decision)
        if len(self._cache) > 1000:
            self._cache.clear()
        return decision

    def show_main_menu(self) -> str:
        return get_main_menu()

    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **_: Any,
    ) -> str:
        sender = sender or sender_id or "default"
        if not message or not message.strip():
            return get_main_menu()

        logger.info("Processing WhatsApp message from %s", sender)
        logger.info("Message: %s", message)
        
        decision = self._make_routing_decision(message, sender)
        logger.info("Route: %s -> %s.%s (%s)", decision.intent, decision.service_file, decision.method, decision.reason)
        logger.info("Entities: %s", decision.entity)

        # Menu Service
        if decision.service_key == "menu_service":
            state = self._get_menu_state(sender)
            state.is_active = False
            state.menu_type = "main"
            return get_main_menu()

        # Greeting
        if decision.intent == "greeting":
            return "👋 Hello! Welcome to HPK Logistics 🏪. How can I assist you today? 📦"

        # Get service instance
        service = None
        if decision.service_key == "dn_analysis":
            service = self.dn_service
        elif decision.service_key == "dealer_analytics":
            service = self.dealer_service
        elif decision.service_key in ["city_menu", "city_service"]:
            service = self.city_service
        elif decision.service_key in ["warehouse_menu", "warehouse_service"]:
            service = self.warehouse_service
        elif decision.service_key in ["product_menu", "product_service"]:
            service = self.product_service
        elif decision.service_key in ["national_kpi_menu", "national_kpi_service"]:
            service = self.national_kpi_service
        elif decision.service_key == "groq_service":
            service = self.groq_service

        if service is None:
            logger.error("Unknown service key: %s", decision.service_key)
            return get_invalid_selection_message()

        try:
            # Get the method from service
            method = getattr(service, decision.method)
            
            # =====================================================================================================================
            # CITY MENU (city_service.py) - Press 3 or type "city menu"
            # =====================================================================================================================
            if decision.service_key == "city_menu":
                if decision.method == "get_city_menu":
                    # Show the full City Analytics Menu
                    if hasattr(service, "get_city_menu"):
                        menu = service.get_city_menu()
                        if not menu or menu.strip() == "":
                            return self._get_city_menu_default()
                        return menu
                    return self._get_city_menu_default()
                else:
                    # Process city menu input
                    user_input = decision.entity.get("user_input", message)
                    session_id = decision.entity.get("session_id", sender)
                    if hasattr(service, "process_city_menu_input"):
                        result = service.process_city_menu_input(session_id, user_input)
                        if result.get("exit_menu", False):
                            state = self._get_menu_state(sender)
                            state.is_active = False
                            state.menu_type = "main"
                        return result.get("response", get_main_menu())
                    return service.get_city_menu() if hasattr(service, "get_city_menu") else self._get_city_menu_default()

            # =====================================================================================================================
            # WAREHOUSE MENU (warehouse_service.py) - Press 4 or type "warehouse menu"
            # =====================================================================================================================
            elif decision.service_key == "warehouse_menu":
                if decision.method == "get_main_menu":
                    if hasattr(service, "get_main_menu"):
                        menu = service.get_main_menu()
                        if not menu or menu.strip() == "":
                            return self._get_warehouse_menu_default()
                        return menu
                    return self._get_warehouse_menu_default()
                else:
                    # Process warehouse menu input
                    user_input = decision.entity.get("user_input", message)
                    session_id = decision.entity.get("session_id", sender)
                    if hasattr(service, "process_menu_input"):
                        result = service.process_menu_input(session_id, user_input)
                        if result.get("exit_menu", False):
                            state = self._get_menu_state(sender)
                            state.is_active = False
                            state.menu_type = "main"
                        return result.get("response", get_main_menu())
                    return service.get_main_menu() if hasattr(service, "get_main_menu") else self._get_warehouse_menu_default()

            # =====================================================================================================================
            # PRODUCT MENU (product_service.py) - Press 5 or type "product menu"
            # =====================================================================================================================
            elif decision.service_key == "product_menu":
                if decision.method == "get_main_menu":
                    if hasattr(service, "get_main_menu"):
                        menu = service.get_main_menu()
                        if not menu or menu.strip() == "":
                            return self._get_product_menu_default()
                        return menu
                    return self._get_product_menu_default()
                else:
                    # Process product menu input
                    user_input = decision.entity.get("user_input", message)
                    session_id = decision.entity.get("session_id", sender)
                    if hasattr(service, "process_menu_input"):
                        result = service.process_menu_input(session_id, user_input)
                        if result.get("exit_menu", False):
                            state = self._get_menu_state(sender)
                            state.is_active = False
                            state.menu_type = "main"
                        return result.get("response", get_main_menu())
                    return service.get_main_menu() if hasattr(service, "get_main_menu") else self._get_product_menu_default()

            # =====================================================================================================================
            # NATIONAL KPI MENU (national_kpi_service.py) - Press 6 or type "national kpi"
            # =====================================================================================================================
            elif decision.service_key == "national_kpi_menu":
                if decision.method == "get_main_menu":
                    if hasattr(service, "get_main_menu"):
                        menu = service.get_main_menu()
                        if not menu or menu.strip() == "":
                            return self._get_national_menu_default()
                        return menu
                    return self._get_national_menu_default()
                else:
                    # Process national KPI menu input
                    user_input = decision.entity.get("user_input", message)
                    session_id = decision.entity.get("session_id", sender)
                    if hasattr(service, "process_menu_input"):
                        result = service.process_menu_input(session_id, user_input)
                        if result.get("exit_menu", False):
                            state = self._get_menu_state(sender)
                            state.is_active = False
                            state.menu_type = "main"
                        return result.get("response", get_main_menu())
                    return service.get_main_menu() if hasattr(service, "get_main_menu") else self._get_national_menu_default()

            # =====================================================================================================================
            # DN ANALYSIS (dn_analysis.py) - Menu 1, 7, 8
            # =====================================================================================================================
            elif decision.service_key == "dn_analysis":
                if decision.method == "get_dn_dashboard":
                    dn_no = decision.entity.get("dn") or decision.entity.get("dn_number")
                    if not dn_no:
                        return "⚠️ Please provide a valid DN number (8-12 digits)."
                    if not _is_valid_dn(dn_no):
                        return f"⚠️ Invalid DN number '{dn_no}'. Please provide a valid 8-12 digit DN number."
                    result = method(dn_no)
                elif decision.method == "get_warehouse_dashboard":
                    warehouse = decision.entity.get("warehouse")
                    if not warehouse:
                        return "⚠️ Please provide a warehouse name."
                    result = method(warehouse)
                elif decision.method in ["get_pending_dns", "get_top_performers"]:
                    result = method()
                else:
                    result = method(decision.entity)
                return _extract_whatsapp_message(result)

            # =====================================================================================================================
            # DEALER ANALYTICS (dealer_analytics_service.py) - Menu 2
            # =====================================================================================================================
            elif decision.service_key == "dealer_analytics":
                dealer_name = decision.entity.get("dealer_name") or decision.entity.get("dealer")
                if not dealer_name:
                    return "⚠️ Please provide a dealer name."
                result = await _resolve(method(dealer_name))
                return _extract_whatsapp_message(result)

            # =====================================================================================================================
            # CITY SERVICE (city_service.py) - Natural Language Queries
            # =====================================================================================================================
            elif decision.service_key == "city_service":
                if hasattr(service, "process_whatsapp_query"):
                    result = service.process_whatsapp_query(message, sender)
                    if isinstance(result, str):
                        return result
                    if isinstance(result, dict):
                        return _extract_whatsapp_message(result)
                else:
                    city_name = decision.entity.get("city_name") or decision.entity.get("city")
                    if not city_name:
                        return "⚠️ Please provide a city name.\n\nExample: Lahore, Karachi, Haripur"
                    result = service.get_city_dashboard(city_name)
                    return _extract_whatsapp_message(result)

            # =====================================================================================================================
            # WAREHOUSE SERVICE (warehouse_service.py) - Natural Language Queries
            # =====================================================================================================================
            elif decision.service_key == "warehouse_service":
                if hasattr(service, "process_whatsapp_query"):
                    result = service.process_whatsapp_query(message, sender)
                    if isinstance(result, str):
                        return result
                    if isinstance(result, dict):
                        return _extract_whatsapp_message(result)

            # =====================================================================================================================
            # PRODUCT SERVICE (product_service.py) - Natural Language Queries
            # =====================================================================================================================
            elif decision.service_key == "product_service":
                if hasattr(service, "process_whatsapp_query"):
                    result = service.process_whatsapp_query(message, sender)
                    if isinstance(result, str):
                        return result
                    if isinstance(result, dict):
                        return _extract_whatsapp_message(result)

            # =====================================================================================================================
            # NATIONAL KPI SERVICE (national_kpi_service.py) - Natural Language Queries
            # =====================================================================================================================
            elif decision.service_key == "national_kpi_service":
                if hasattr(service, "process_whatsapp_query"):
                    result = service.process_whatsapp_query(message, sender)
                    if isinstance(result, str):
                        return result
                    if isinstance(result, dict):
                        return _extract_whatsapp_message(result)

            # =====================================================================================================================
            # AI QUERY (groq_service.py) - Menu 9
            # =====================================================================================================================
            elif decision.service_key == "groq_service":
                result = await _resolve(method(message, decision.entity))
                return _extract_whatsapp_message(result)

            # =====================================================================================================================
            # GENERIC FALLBACK
            # =====================================================================================================================
            else:
                result = await _resolve(method(decision.entity))
                return _extract_whatsapp_message(result)

        except Exception as e:
            logger.exception("Service call failed: %s.%s", decision.service_key, decision.method)
            
            if "validation error" in str(e).lower() or "Invalid DN number" in str(e):
                return f"⚠️ Invalid DN number format. Please provide a valid 8-12 digit DN number."
            
            if decision.service_key == "groq_service":
                return "⚠️ AI service is temporarily unavailable. Reply *menu* to use logistics services."
            
            if decision.service_key == "dn_analysis":
                return f"⚠️ DN service error: {str(e)[:100]}\n\nPlease check the DN number and try again."
            elif decision.service_key == "dealer_analytics":
                return f"⚠️ Dealer service error: {str(e)[:100]}\n\nPlease check the dealer name and try again."
            elif decision.service_key in ["city_service", "city_menu"]:
                return f"⚠️ City service error: {str(e)[:100]}\n\nPlease try again or type 'city menu' for options."
            elif decision.service_key in ["warehouse_service", "warehouse_menu"]:
                return f"⚠️ Warehouse service error: {str(e)[:100]}\n\nPlease try again or type 'warehouse menu' for options."
            elif decision.service_key in ["product_service", "product_menu"]:
                return f"⚠️ Product service error: {str(e)[:100]}\n\nPlease try again or type 'product menu' for options."
            elif decision.service_key in ["national_kpi_service", "national_kpi_menu"]:
                return f"⚠️ National KPI service error: {str(e)[:100]}\n\nPlease try again or type 'national kpi' for options."
            
            return f"⚠️ {MENU_OPTIONS[decision.menu_option or '0']['name']} is temporarily unavailable. Please try again."

    # =====================================================================================================================
    # DEFAULT MENU HELPERS
    # =====================================================================================================================

    def _get_city_menu_default(self) -> str:
        return "\n".join([
            "🏙️ *CITY ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. City Dashboard",
            "2. City Revenue",
            "3. City Units",
            "4. City Pending",
            "5. City Delivery",
            "6. Compare Cities",
            "7. City Rankings",
            "8. Top Products",
            "9. Business Score",
            "10. Distance Info",
            "11. Growth Analytics",
            "12. Warehouse Distribution",
            "13. City Summary",
            "99. Back to Main",
            "",
            "Reply with a number or city name:"
        ])

    def _get_warehouse_menu_default(self) -> str:
        return "\n".join([
            "🏭 *WAREHOUSE ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Warehouse Dashboard",
            "2. Warehouse Inventory",
            "3. Warehouse Revenue",
            "4. Warehouse Units",
            "5. Pending DN",
            "6. Pending PGI",
            "7. Pending POD",
            "8. Delivery Performance",
            "9. Warehouse Ranking",
            "10. Warehouse Comparison",
            "11. Top Products",
            "12. Dealer Distribution",
            "13. City Distribution",
            "14. Storage Utilization",
            "15. Transit Analysis",
            "16. Delivery Aging",
            "17. Warehouse KPIs",
            "18. Warehouse AI Summary",
            "99. Back to Main",
            "",
            "Reply with a number or warehouse name:"
        ])

    def _get_product_menu_default(self) -> str:
        return "\n".join([
            "📦 *PRODUCT ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Product Dashboard",
            "2. Product Revenue",
            "3. Product Units",
            "4. Product Dealers",
            "5. Product Warehouses",
            "6. Product Cities",
            "7. Pending DN",
            "8. Pending PGI",
            "9. Pending POD",
            "10. Product Comparison",
            "11. Product Ranking",
            "12. Monthly Trend",
            "13. Executive Summary",
            "14. AI Insights",
            "15. Recommendations",
            "16. Product Life Cycle",
            "17. Product Performance",
            "18. Smart Search",
            "99. Back to Main",
            "",
            "Reply with a number or product name:"
        ])

    def _get_national_menu_default(self) -> str:
        return "\n".join([
            "🇵🇰 *NATIONAL LOGISTICS INTELLIGENCE MENU*",
            "",
            "0. Main Menu",
            "1. National Dashboard",
            "2. Warehouse Dashboard",
            "3. Warehouse Ranking",
            "4. Warehouse Comparison",
            "5. National Revenue",
            "6. National Units",
            "7. National Delivery",
            "8. Pending Dashboard",
            "9. POD Dashboard",
            "10. PGI Dashboard",
            "11. Dealer Coverage",
            "12. City Analytics",
            "13. Product Distribution",
            "14. SLA Compliance",
            "15. Executive Summary",
            "16. AI Insights",
            "17. Recommendations",
            "18. National Health Score",
            "19. Monthly Trend",
            "20. National Forecast",
            "99. Back to Main",
            "",
            "Reply with a number or command:"
        ])


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
    return get_ai_provider_service()


async def process_whatsapp_query(
    message: str,
    sender: Optional[str] = None,
    sender_id: Optional[str] = None,
    **kwargs: Any,
) -> str:
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
