"""
File: app/services/ai_provider_service.py
Version: 11.0 - COMPLETE MENU NAVIGATION
Purpose: Single entry point for WhatsApp AI Agent with menu navigation
         Routes all requests to appropriate service files

╔═══════════════════════════════════════════════════════════════════╗
║  Menu  │ Service File                    │ Method                  ║
╠═══════════════════════════════════════════════════════════════════╣
║  0     │ ai_provider_service.py          │ show_main_menu()        ║
║  1     │ dn_analysis.py                  │ get_dn_dashboard()      ║
║  2     │ dealer_analytics_service.py     │ get_dealer_dashboard()  ║
║  3     │ city_service.py                 │ get_city_dashboard()    ║
║  4     │ dn_analysis.py                  │ get_warehouse_dashboard()║
║  5     │ product_service.py              │ get_product_dashboard() ║
║  6     │ national_kpi_service.py         │ get_national_kpi()      ║
║  7     │ dn_analysis.py                  │ get_pending_dns()       ║
║  8     │ dn_analysis.py                  │ get_top_performers()    ║
║  9     │ groq_service.py                 │ process_query()         ║
╚═══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, Optional, List, Tuple

# ============================================================
# SEMANTIC ROUTER FOR NLP (Optional - for natural language)
# ============================================================

try:
    from semantic_router import Route, Router
    from semantic_router.encoders import HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False

# ============================================================
# IMPORT ALL SERVICE FILES FOR ROUTING
# ============================================================

try:
    from app.services.dn_analysis import DNAnalysisService
    from app.services.dealer_analytics_service import DealerAnalyticsService
    from app.services.city_service import CityService
    from app.services.product_service import ProductService
    from app.services.national_kpi_service import NationalKPIService
    from app.services.groq_service import GroqService
except ImportError as e:
    logging.error(f"❌ Failed to import services: {e}")
    # Create dummy services for testing/fallback
    class DNAnalysisService:
        async def get_dn_dashboard(self, entities): return "📦 DN Dashboard"
        async def get_warehouse_dashboard(self, entities): return "🏚️ Warehouse Dashboard"
        async def get_pending_dns(self, entities): return "⏳ Pending DNS"
        async def get_top_performers(self, entities): return "🏆 Top Performers"
    
    class DealerAnalyticsService:
        async def get_dealer_dashboard(self, entities): return "🏢 Dealer Dashboard"
    
    class CityService:
        async def get_city_dashboard(self, entities): return "🏙️ City Dashboard"
    
    class ProductService:
        async def get_product_dashboard(self, entities): return "📦 Product Dashboard"
    
    class NationalKPIService:
        async def get_national_kpi(self, entities): return "📊 National KPI"
    
    class GroqService:
        async def process_query(self, message, entities): return f"🤖 AI Response to: {message}"

logger = logging.getLogger(__name__)


# ============================================================
# MENU CONFIGURATION - COMPLETE ROUTING TABLE
# ============================================================

MENU_OPTIONS = {
    "0": {
        "name": "Main Menu",
        "service_key": "ai_provider_service",
        "service_file": "ai_provider_service.py",
        "method": "show_main_menu",
        "requires_ai": False,
        "description": "📋 Show main menu",
        "category": "Menu",
        "emoji": "📋"
    },
    "1": {
        "name": "DN Delivery Menu",
        "service_key": "dn_analysis",
        "service_file": "dn_analysis.py",
        "method": "get_dn_dashboard",
        "requires_ai": False,
        "description": "📦 DN Delivery dashboard",
        "category": "DN Operations",
        "emoji": "📦"
    },
    "2": {
        "name": "Dealer Analytics Menu",
        "service_key": "dealer_analytics",
        "service_file": "dealer_analytics_service.py",
        "method": "get_dealer_dashboard",
        "requires_ai": False,
        "description": "🏢 Dealer performance analytics",
        "category": "Dealer Operations",
        "emoji": "🏢"
    },
    "3": {
        "name": "City Analytics Menu",
        "service_key": "city_service",
        "service_file": "city_service.py",
        "method": "get_city_dashboard",
        "requires_ai": False,
        "description": "🏙️ City-wise performance analytics",
        "category": "City Operations",
        "emoji": "🏙️"
    },
    "4": {
        "name": "Warehouse Dashboard Menu",
        "service_key": "dn_analysis",
        "service_file": "dn_analysis.py",
        "method": "get_warehouse_dashboard",
        "requires_ai": False,
        "description": "🏚️ Warehouse performance dashboard",
        "category": "Warehouse Operations",
        "emoji": "🏚️"
    },
    "5": {
        "name": "Product Analytics Menu",
        "service_key": "product_service",
        "service_file": "product_service.py",
        "method": "get_product_dashboard",
        "requires_ai": False,
        "description": "📦 Product performance analytics",
        "category": "Product Operations",
        "emoji": "📦"
    },
    "6": {
        "name": "National KPI Menu",
        "service_key": "national_kpi_service",
        "service_file": "national_kpi_service.py",
        "method": "get_national_kpi",
        "requires_ai": False,
        "description": "📊 National KPI analytics",
        "category": "KPI Operations",
        "emoji": "📊"
    },
    "7": {
        "name": "Pending DN Menu",
        "service_key": "dn_analysis",
        "service_file": "dn_analysis.py",
        "method": "get_pending_dns",
        "requires_ai": False,
        "description": "⏳ Pending delivery notes",
        "category": "DN Operations",
        "emoji": "⏳"
    },
    "8": {
        "name": "Top Performers Menu",
        "service_key": "dn_analysis",
        "service_file": "dn_analysis.py",
        "method": "get_top_performers",
        "requires_ai": False,
        "description": "🏆 Top performers dashboard",
        "category": "DN Operations",
        "emoji": "🏆"
    },
    "9": {
        "name": "AI Query Menu",
        "service_key": "groq_service",
        "service_file": "groq_service.py",
        "method": "process_query",
        "requires_ai": True,
        "description": "🤖 AI-powered query processing",
        "category": "AI Operations",
        "emoji": "🤖"
    }
}

# Clean routing table for fast dictionary-based routing
ROUTING_TABLE = {
    "0": {"service": "ai_provider_service", "method": "show_main_menu", "file": "ai_provider_service.py"},
    "1": {"service": "dn_analysis", "method": "get_dn_dashboard", "file": "dn_analysis.py"},
    "2": {"service": "dealer_analytics", "method": "get_dealer_dashboard", "file": "dealer_analytics_service.py"},
    "3": {"service": "city_service", "method": "get_city_dashboard", "file": "city_service.py"},
    "4": {"service": "dn_analysis", "method": "get_warehouse_dashboard", "file": "dn_analysis.py"},
    "5": {"service": "product_service", "method": "get_product_dashboard", "file": "product_service.py"},
    "6": {"service": "national_kpi_service", "method": "get_national_kpi", "file": "national_kpi_service.py"},
    "7": {"service": "dn_analysis", "method": "get_pending_dns", "file": "dn_analysis.py"},
    "8": {"service": "dn_analysis", "method": "get_top_performers", "file": "dn_analysis.py"},
    "9": {"service": "groq_service", "method": "process_query", "file": "groq_service.py"}
}


# ============================================================
# MAIN MENU GENERATOR
# ============================================================

def get_main_menu() -> str:
    """Generate the main menu"""
    menu = """
===============================
      AI LOGISTICS MENU
===============================

0. Main Menu
1. DN Delivery Menu
2. Dealer Analytics Menu
3. City Analytics Menu
4. Warehouse Dashboard Menu
5. Product Analytics Menu
6. National KPI Menu
7. Pending DN Menu
8. Top Performers Menu
9. AI Query Menu

Reply with a number to continue.
"""
    return menu


def get_invalid_selection_message() -> str:
    """Generate invalid selection message with menu"""
    return f"""
Invalid selection. Please choose a number from 0 to 9.

{get_main_menu()}
"""


# ============================================================
# AI PROVIDER SERVICE - SINGLE ENTRY POINT
# ============================================================

class AIProviderService:
    """
    Single entry point for WhatsApp AI Agent.
    Handles all incoming messages and routes them appropriately.
    
    NO WEBHOOK CHANGES REQUIRED - This class handles everything.
    All business logic stays in the destination service files.
    """
    
    _instance: Optional["AIProviderService"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "AIProviderService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        
        # Initialize all service instances
        self._init_services()
        
        # Initialize semantic router for natural language processing
        self._router = None
        self._init_semantic_router()
        
        logger.info("✅ AIProviderService initialized as single entry point")
        logger.info(f"📋 Menu routing table loaded with {len(MENU_OPTIONS)} options")
    
    def _init_services(self):
        """Initialize all service instances"""
        try:
            self.dn_service = DNAnalysisService()
            self.dealer_service = DealerAnalyticsService()
            self.city_service = CityService()
            self.product_service = ProductService()
            self.national_kpi_service = NationalKPIService()
            self.groq_service = GroqService()
            logger.info("✅ All services initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize services: {e}")
            raise
    
    def _init_semantic_router(self):
        """Initialize semantic router for natural language queries (optional)"""
        if not SEMANTIC_ROUTER_AVAILABLE:
            logger.info("ℹ️ Semantic Router not available - NLP features disabled")
            return
        
        try:
            encoder = HuggingFaceEncoder()
            
            routes = [
                Route(
                    name="dn_lookup",
                    utterances=[
                        "show dn", "dn dashboard", "delivery note", "track dn",
                        "dn number", "dn status", "check dn", "delivery note number",
                        "delivery note dashboard", "view dn", "get dn",
                        "dn details", "dn information"
                    ]
                ),
                Route(
                    name="dealer_analytics",
                    utterances=[
                        "show dealer", "dealer dashboard", "dealer analytics",
                        "dealer performance", "dealer revenue", "dealer stats",
                        "dealer information", "dealer details", "dealer data"
                    ]
                ),
                Route(
                    name="city_analytics",
                    utterances=[
                        "show city", "city dashboard", "city analytics",
                        "city performance", "city revenue", "city stats",
                        "city information", "city details", "city data"
                    ]
                ),
                Route(
                    name="warehouse_analytics",
                    utterances=[
                        "show warehouse", "warehouse dashboard", "warehouse analytics",
                        "warehouse performance", "warehouse revenue", "warehouse stats",
                        "warehouse details", "warehouse data"
                    ]
                ),
                Route(
                    name="product_analytics",
                    utterances=[
                        "show product", "product dashboard", "product analytics",
                        "product performance", "product revenue", "product stats",
                        "product details", "product data", "product information"
                    ]
                ),
                Route(
                    name="national_kpi",
                    utterances=[
                        "national kpi", "kpi dashboard", "overall performance",
                        "national dashboard", "company kpi", "company performance",
                        "overall kpi", "national metrics", "business overview"
                    ]
                ),
                Route(
                    name="pending_dns",
                    utterances=[
                        "pending dns", "pending deliveries", "show pending",
                        "list pending", "pending delivery notes", "open dns",
                        "undelivered dns", "outstanding deliveries", "pending orders"
                    ]
                ),
                Route(
                    name="top_performers",
                    utterances=[
                        "top performers", "best performers", "top performing",
                        "leaderboard", "top rankings", "top 10", "best performers"
                    ]
                ),
                Route(
                    name="help",
                    utterances=[
                        "help", "assist", "support", "how to", "what is",
                        "explain", "guide", "help me", "i need help", "tutorial"
                    ]
                ),
                Route(
                    name="menu",
                    utterances=[
                        "menu", "options", "services", "what can you do",
                        "show menu", "main menu", "available options",
                        "what are my options", "show services", "list services"
                    ]
                ),
            ]
            
            self._router = Router(routes=routes, encoder=encoder)
            logger.info(f"✅ Semantic Router initialized with {len(routes)} routes")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize semantic router: {e}")
            self._router = None
    
    # ============================================================
    # DN DETECTION
    # ============================================================
    
    def _extract_dn(self, text: str) -> Optional[str]:
        """Extract DN number using regex patterns"""
        if not text:
            return None
        
        text = text.strip()
        
        # Pattern 1: Exactly 10 digits
        match = re.search(r'(?<!\d)(\d{10})(?!\d)', text)
        if match:
            dn = match.group(1)
            logger.info(f"🔍 DN found (10 digits): {dn}")
            return dn
        
        # Pattern 2: 8-12 digits
        match = re.search(r'(?<!\d)(\d{8,12})(?!\d)', text)
        if match:
            dn = match.group(1)
            logger.info(f"🔍 DN found (8-12 digits): {dn}")
            return dn
        
        # Pattern 3: With spaces (e.g., "6243 6987 49")
        match = re.search(r'(?<!\d)(\d{4}\s*\d{4}\s*\d{2,4})(?!\d)', text)
        if match:
            dn = re.sub(r'\s', '', match.group(1))
            logger.info(f"🔍 DN found (with spaces): {dn}")
            return dn
        
        # Pattern 4: With dashes (e.g., "6243-6987-49")
        match = re.search(r'(?<!\d)(\d{4}-\d{4}-\d{2,4})(?!\d)', text)
        if match:
            dn = re.sub(r'-', '', match.group(1))
            logger.info(f"🔍 DN found (with dashes): {dn}")
            return dn
        
        return None
    
    # ============================================================
    # MENU DETECTION
    # ============================================================
    
    def _is_menu_selection(self, text: str) -> Optional[str]:
        """
        Check if message is a menu selection.
        Returns the menu number (0-9) or None.
        """
        if not text:
            return None
        
        text = text.strip().lower()
        
        # Check for menu keywords (always return "0" for menu)
        menu_keywords = ["menu", "main menu", "options", "help", "start", "back", "home", "0"]
        if text in menu_keywords:
            logger.info(f"📋 Menu keyword detected: '{text}' -> Showing main menu")
            return "0"
        
        # Check for single digit menu selection (0-9)
        if re.match(r'^[0-9]$', text):
            logger.info(f"📋 Menu selection: {text}")
            return text
        
        # Check for menu selection with dot or space (e.g., "1.", "1 ", "1)")
        if re.match(r'^[0-9][\.\s\)]', text):
            menu_num = text[0]
            logger.info(f"📋 Menu selection: {menu_num}")
            return menu_num
        
        return None
    
    # ============================================================
    # NATURAL LANGUAGE QUERY DETECTION (Optional)
    # ============================================================
    
    def _detect_natural_language_intent(self, message: str) -> Optional[str]:
        """
        Detect intent from natural language using semantic router.
        Returns the menu number (0-9) or None.
        """
        if not self._router:
            return None
        
        try:
            result = self._router.route(message)
            intent = result.name
            confidence = getattr(result, 'score', 0.0)
            
            logger.info(f"🧠 NLP Intent: {intent} (confidence: {confidence:.2f})")
            
            # Only use if confidence is reasonable
            if confidence < 0.3:
                logger.info(f"⬇️ Low confidence ({confidence:.2f}) - using AI fallback")
                return None
            
            # Map semantic intent to menu option
            intent_to_menu = {
                "dn_lookup": "1",
                "dealer_analytics": "2",
                "city_analytics": "3",
                "warehouse_analytics": "4",
                "product_analytics": "5",
                "national_kpi": "6",
                "pending_dns": "7",
                "top_performers": "8",
                "help": "0",
                "menu": "0"
            }
            
            menu_option = intent_to_menu.get(intent)
            if menu_option:
                logger.info(f"✅ NLP routed to menu: {menu_option}")
                return menu_option
            
        except Exception as e:
            logger.error(f"❌ Semantic router error: {e}")
        
        return None
    
    # ============================================================
    # MAIN ENTRY POINT - process_whatsapp_query
    # ============================================================
    
    async def process_whatsapp_query(self, message: str, sender: Optional[str] = None) -> str:
        """
        Single entry point for all WhatsApp messages.
        
        This is the ONLY function that should be called from webhook.
        Handles menu navigation, DN detection, and AI queries.
        
        Args:
            message: The user's message
            sender: Optional sender identifier
            
        Returns:
            Formatted response string
        """
        if not message or not message.strip():
            return get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📩 Processing message from {sender}: {message_clean[:50]}...")
        
        # ============================================================
        # STEP 1: CHECK FOR MENU KEYWORDS (HIGHEST PRIORITY)
        # "menu", "help", "start", "0" should ALWAYS show the menu
        # ============================================================
        menu_selection = self._is_menu_selection(message_clean)
        if menu_selection == "0":
            logger.info(f"📋 Menu requested via keyword: '{message_clean}'")
            return get_main_menu()
        
        # ============================================================
        # STEP 2: CHECK FOR DN NUMBER (HIGHEST PRIORITY AFTER MENU)
        # ============================================================
        dn = self._extract_dn(message_clean)
        if dn:
            logger.info(f"🔍 DN detected: {dn} -> Routing to DN dashboard")
            try:
                result = await self.dn_service.get_dn_dashboard({"dn": dn, "dn_number": dn})
                return result
            except Exception as e:
                logger.error(f"❌ DN service error: {e}")
                return f"⚠️ Error fetching DN {dn}: {str(e)}"
        
        # ============================================================
        # STEP 3: CHECK FOR MENU SELECTION (1-9)
        # ============================================================
        if menu_selection and menu_selection in ROUTING_TABLE:
            logger.info(f"📋 Menu selection: {menu_selection}")
            return await self._route_menu_selection(menu_selection, message_clean)
        
        # ============================================================
        # STEP 4: CHECK FOR NATURAL LANGUAGE INTENT
        # ============================================================
        menu_from_nlp = self._detect_natural_language_intent(message_clean)
        if menu_from_nlp:
            return await self._route_menu_selection(menu_from_nlp, message_clean)
        
        # ============================================================
        # STEP 5: DEFAULT - SEND TO AI QUERY
        # ============================================================
        logger.info(f"🤖 No specific intent detected - Sending to AI")
        try:
            result = await self.groq_service.process_query(message_clean, {"message": message_clean})
            return result
        except Exception as e:
            logger.error(f"❌ AI service error: {e}")
            return f"⚠️ AI service error: {str(e)}"
    
    # ============================================================
    # MENU ROUTING - Clean dictionary-based routing
    # ============================================================
    
    async def _route_menu_selection(self, menu_option: str, original_message: str) -> str:
        """
        Route menu selection to appropriate service.
        Uses clean dictionary-based routing instead of if/elif chains.
        """
        # Validate menu option
        if menu_option not in ROUTING_TABLE:
            logger.warning(f"⚠️ Invalid menu selection: {menu_option}")
            return get_invalid_selection_message()
        
        # Get routing info
        routing = ROUTING_TABLE[menu_option]
        service_key = routing["service"]
        method_name = routing["method"]
        service_file = routing["file"]
        
        # Handle Main Menu (Option 0)
        if menu_option == "0":
            logger.info("📋 Showing main menu")
            return get_main_menu()
        
        logger.info(f"📋 Routing menu {menu_option} -> {service_file}.{method_name}")
        
        # Service map for clean routing
        service_map = {
            "dn_analysis": self.dn_service,
            "dealer_analytics": self.dealer_service,
            "city_service": self.city_service,
            "product_service": self.product_service,
            "national_kpi_service": self.national_kpi_service,
            "groq_service": self.groq_service,
        }
        
        # Get the service instance
        service = service_map.get(service_key)
        if not service:
            logger.error(f"❌ Service not found: {service_key}")
            return f"⚠️ Service {service_key} not found. Please try again."
        
        # Call the appropriate method
        try:
            if hasattr(service, method_name):
                method = getattr(service, method_name)
                
                # Handle different method signatures
                if service_key == "groq_service":
                    # AI service needs the original message
                    result = await method(original_message, {"message": original_message})
                else:
                    # All other services use entities/empty dict
                    result = await method({})
                
                return result
            else:
                logger.error(f"❌ Method {method_name} not found in {service_key}")
                return f"⚠️ Service error: Method {method_name} not found."
                
        except Exception as e:
            logger.error(f"❌ Service error in {service_key}.{method_name}: {e}")
            return f"⚠️ Error: {str(e)}"
    
    # ============================================================
    # UTILITY METHODS
    # ============================================================
    
    def get_menu_info(self, menu_option: str) -> Optional[Dict[str, Any]]:
        """Get information about a menu option"""
        return MENU_OPTIONS.get(menu_option)
    
    def get_all_menu_options(self) -> List[Dict[str, Any]]:
        """Get all menu options"""
        return list(MENU_OPTIONS.values())
    
    def get_routing_table(self) -> Dict[str, Any]:
        """Get the complete routing table"""
        return ROUTING_TABLE
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for the service"""
        return {
            "service": "ai_provider_service",
            "version": "11.0",
            "status": "healthy",
            "menu_options": len(MENU_OPTIONS),
            "routing_table_size": len(ROUTING_TABLE),
            "services_available": {
                "dn_analysis": hasattr(self, "dn_service"),
                "dealer_analytics": hasattr(self, "dealer_service"),
                "city_service": hasattr(self, "city_service"),
                "product_service": hasattr(self, "product_service"),
                "national_kpi_service": hasattr(self, "national_kpi_service"),
                "groq_service": hasattr(self, "groq_service")
            },
            "semantic_router": self._router is not None,
            "semantic_router_available": SEMANTIC_ROUTER_AVAILABLE
        }


# ============================================================
# SINGLETON INSTANCE
# ============================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()


def get_ai_provider_service() -> AIProviderService:
    """Get singleton instance of AIProviderService"""
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                _ai_service = AIProviderService()
                logger.info("✅ AIProviderService singleton initialized")
    return _ai_service


# ============================================================
# MAIN ENTRY POINT FUNCTION (Backward Compatible)
# ============================================================

async def process_whatsapp_query(message: str, sender: Optional[str] = None) -> str:
    """
    Main entry point for WhatsApp messages.
    This is the function that should be called from webhook.
    
    NO WEBHOOK CHANGES REQUIRED - Just call this function.
    
    Args:
        message: User's message
        sender: Optional sender identifier
        
    Returns:
        Formatted response
    """
    service = get_ai_provider_service()
    return await service.process_whatsapp_query(message, sender)


# ============================================================
# MODULE EXPORTS
# ============================================================

__all__ = [
    # Main API
    "AIProviderService",
    "get_ai_provider_service",
    "process_whatsapp_query",
    
    # Menu
    "get_main_menu",
    "get_invalid_selection_message",
    
    # Config
    "MENU_OPTIONS",
    "ROUTING_TABLE"
]
