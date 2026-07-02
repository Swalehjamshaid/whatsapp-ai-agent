"""
File: app/services/ai_provider_service.py
Version: 14.0 - FULL SEMANTIC-ROUTER INTEGRATION
Purpose: Single entry point for WhatsApp AI Agent with full semantic routing
         Uses semantic-router for ALL intent detection and routing decisions

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

Features:
- ✅ Semantic Router for ALL Intent Detection
- ✅ Automatic DN Number Detection
- ✅ Natural Language Understanding
- ✅ Menu-Based Routing (0-9)
- ✅ Entity Extraction
- ✅ Confidence Scoring
- ✅ AI Fallback (Groq)
- ✅ No Webhook Changes Required
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, Optional, List, Tuple
from dataclasses import dataclass, field

# ============================================================
# SEMANTIC-ROUTER - PRIMARY INTENT DETECTION LIBRARY
# ============================================================

try:
    from semantic_router import Route, Router
    from semantic_router.encoders import HuggingFaceEncoder
    from semantic_router.layer import RouteLayer
    SEMANTIC_ROUTER_AVAILABLE = True
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False
    raise ImportError(
        "semantic-router is required. Install with: pip install semantic-router>=0.0.70"
    )

logger = logging.getLogger(__name__)

# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class RoutingDecision:
    """Routing decision from semantic router"""
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
            "menu_option": self.menu_option
        }

# ============================================================
# IMPORT ALL SERVICE FILES WITH FALLBACK
# ============================================================

try:
    from app.services.dn_analysis import DNAnalysisService
except ImportError:
    logger.warning("⚠️ dn_analysis.py not found - using fallback")
    class DNAnalysisService:
        async def get_dn_dashboard(self, entities): 
            return f"📦 DN Dashboard\nDN: {entities.get('dn', 'Unknown')}"
        async def get_warehouse_dashboard(self, entities): 
            return "🏚️ Warehouse Dashboard"
        async def get_pending_dns(self, entities): 
            return "⏳ Pending DNS"
        async def get_top_performers(self, entities): 
            return "🏆 Top Performers"

try:
    from app.services.dealer_analytics_service import DealerAnalyticsService
except ImportError:
    logger.warning("⚠️ dealer_analytics_service.py not found - using fallback")
    class DealerAnalyticsService:
        async def get_dealer_dashboard(self, entities): 
            return f"🏢 Dealer Dashboard\nDealer: {entities.get('dealer', 'All Dealers')}"

try:
    from app.services.city_service import CityService
except ImportError:
    logger.warning("⚠️ city_service.py not found - using fallback")
    class CityService:
        async def get_city_dashboard(self, entities): 
            return f"🏙️ City Dashboard\nCity: {entities.get('city', 'All Cities')}"

try:
    from app.services.product_service import ProductService
except ImportError:
    logger.warning("⚠️ product_service.py not found - using fallback")
    class ProductService:
        async def get_product_dashboard(self, entities): 
            return "📦 Product Dashboard"

try:
    from app.services.national_kpi_service import NationalKPIService
except ImportError:
    logger.warning("⚠️ national_kpi_service.py not found - using fallback")
    class NationalKPIService:
        async def get_national_kpi(self, entities): 
            return "📊 National KPI Dashboard"

try:
    from app.services.groq_service import GroqService
except ImportError:
    logger.warning("⚠️ groq_service.py not found - using fallback")
    class GroqService:
        async def process_query(self, message, entities): 
            return f"🤖 Received: {message}"

# ============================================================
# MENU CONFIGURATION
# ============================================================

MENU_OPTIONS = {
    "0": {
        "name": "Main Menu",
        "service_key": "menu_service",
        "service_file": "ai_provider_service.py",
        "method": "show_main_menu",
        "requires_ai": False,
        "description": "📋 Show main menu",
        "category": "Menu"
    },
    "1": {
        "name": "DN Delivery Menu",
        "service_key": "dn_analysis",
        "service_file": "dn_analysis.py",
        "method": "get_dn_dashboard",
        "requires_ai": False,
        "description": "📦 DN Delivery dashboard",
        "category": "DN Operations"
    },
    "2": {
        "name": "Dealer Analytics Menu",
        "service_key": "dealer_analytics",
        "service_file": "dealer_analytics_service.py",
        "method": "get_dealer_dashboard",
        "requires_ai": False,
        "description": "🏢 Dealer performance analytics",
        "category": "Dealer Operations"
    },
    "3": {
        "name": "City Analytics Menu",
        "service_key": "city_service",
        "service_file": "city_service.py",
        "method": "get_city_dashboard",
        "requires_ai": False,
        "description": "🏙️ City-wise performance analytics",
        "category": "City Operations"
    },
    "4": {
        "name": "Warehouse Dashboard Menu",
        "service_key": "dn_analysis",
        "service_file": "dn_analysis.py",
        "method": "get_warehouse_dashboard",
        "requires_ai": False,
        "description": "🏚️ Warehouse performance dashboard",
        "category": "Warehouse Operations"
    },
    "5": {
        "name": "Product Analytics Menu",
        "service_key": "product_service",
        "service_file": "product_service.py",
        "method": "get_product_dashboard",
        "requires_ai": False,
        "description": "📦 Product performance analytics",
        "category": "Product Operations"
    },
    "6": {
        "name": "National KPI Menu",
        "service_key": "national_kpi_service",
        "service_file": "national_kpi_service.py",
        "method": "get_national_kpi",
        "requires_ai": False,
        "description": "📊 National KPI analytics",
        "category": "KPI Operations"
    },
    "7": {
        "name": "Pending DN Menu",
        "service_key": "dn_analysis",
        "service_file": "dn_analysis.py",
        "method": "get_pending_dns",
        "requires_ai": False,
        "description": "⏳ Pending delivery notes",
        "category": "DN Operations"
    },
    "8": {
        "name": "Top Performers Menu",
        "service_key": "dn_analysis",
        "service_file": "dn_analysis.py",
        "method": "get_top_performers",
        "requires_ai": False,
        "description": "🏆 Top performers dashboard",
        "category": "DN Operations"
    },
    "9": {
        "name": "AI Query Menu",
        "service_key": "groq_service",
        "service_file": "groq_service.py",
        "method": "process_query",
        "requires_ai": True,
        "description": "🤖 AI-powered query processing",
        "category": "AI Operations"
    }
}

# ============================================================
# SEMANTIC ROUTER - INTENT TO MENU MAPPING
# ============================================================

INTENT_TO_MENU = {
    "dn_lookup": "1",
    "dn_status": "1",
    "dn_history": "1",
    "dn_summary": "1",
    "dealer_dashboard": "2",
    "dealer_revenue": "2",
    "dealer_pending": "2",
    "top_dealers": "2",
    "dealer_comparison": "2",
    "city_dashboard": "3",
    "city_revenue": "3",
    "city_pending": "3",
    "top_cities": "3",
    "city_comparison": "3",
    "warehouse_dashboard": "4",
    "warehouse_revenue": "4",
    "warehouse_pending": "4",
    "top_warehouses": "4",
    "product_dashboard": "5",
    "top_products": "5",
    "national_kpi": "6",
    "national_revenue": "6",
    "national_units": "6",
    "pending_dns": "7",
    "pending_pgi": "7",
    "pending_pod": "7",
    "top_performers": "8",
    "help": "9",
    "menu": "0",
    "greeting": "0",
}

# ============================================================
# MAIN MENU GENERATOR
# ============================================================

def get_main_menu() -> str:
    """Generate the main menu"""
    return """
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

def get_invalid_selection_message() -> str:
    """Generate invalid selection message with menu"""
    return f"""
Invalid selection. Please choose a number from 0 to 9.

{get_main_menu()}
"""

# ============================================================
# AI PROVIDER SERVICE WITH SEMANTIC ROUTER
# ============================================================

class AIProviderService:
    """
    Single entry point for WhatsApp AI Agent with FULL semantic-router integration.
    Uses semantic-router for ALL intent detection and routing decisions.
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
        
        # Initialize all services
        self.dn_service = DNAnalysisService()
        self.dealer_service = DealerAnalyticsService()
        self.city_service = CityService()
        self.product_service = ProductService()
        self.national_kpi_service = NationalKPIService()
        self.groq_service = GroqService()
        
        # Initialize semantic router
        self._router: Optional[Router] = None
        self._init_semantic_router()
        
        # Cache for routing decisions
        self._cache: Dict[str, RoutingDecision] = {}
        self._cache_ttl = 300
        
        logger.info("✅ AIProviderService initialized with semantic-router")
    
    # ============================================================
    # SEMANTIC ROUTER INITIALIZATION
    # ============================================================
    
    def _init_semantic_router(self):
        """Initialize semantic router with all routes"""
        try:
            # Use HuggingFace encoder (free, no API key)
            encoder = HuggingFaceEncoder()
            
            # Define all semantic routes
            routes = [
                # ============================================================
                # DN ROUTES
                # ============================================================
                Route(
                    name="dn_lookup",
                    utterances=[
                        "show dn", "dn dashboard", "delivery note", "track dn",
                        "dn number", "dn status", "check dn", "delivery note number",
                        "delivery note dashboard", "view dn", "get dn",
                        "delivery note status", "track delivery note",
                        "dn details", "dn information", "tell me about dn",
                        "what is the dn", "show me the dn", "find dn",
                        "search dn", "lookup dn", "delivery note lookup"
                    ]
                ),
                Route(
                    name="dn_status",
                    utterances=[
                        "dn status", "status of dn", "check dn status",
                        "what is the status of dn", "delivery note status",
                        "is dn delivered", "dn delivery status",
                        "current status of dn", "where is my dn",
                        "delivery status", "shipment status", "track delivery"
                    ]
                ),
                Route(
                    name="dn_history",
                    utterances=[
                        "dn history", "history of dn", "delivery note history",
                        "show dn history", "dn timeline", "delivery note timeline",
                        "dn log", "dn tracking history", "dn audit",
                        "previous dn records", "delivery history"
                    ]
                ),
                Route(
                    name="dn_summary",
                    utterances=[
                        "dn summary", "summary of dns", "total dns",
                        "dn overview", "delivery note summary", "dn statistics",
                        "total delivery notes", "dn count", "number of dns",
                        "dn total", "dn stats", "delivery summary"
                    ]
                ),
                Route(
                    name="pending_dns",
                    utterances=[
                        "pending dns", "pending deliveries", "show pending",
                        "list pending", "pending delivery notes", "open dns",
                        "undelivered dns", "outstanding deliveries",
                        "dns not delivered yet", "pending orders",
                        "dns pending delivery", "delivery backlog",
                        "waiting for delivery", "pending shipments"
                    ]
                ),
                Route(
                    name="pending_pgi",
                    utterances=[
                        "pending pgi", "pgi pending", "goods issue pending",
                        "pgi not done", "pending goods issue",
                        "goods issue not completed", "pgi delay",
                        "pgi overdue", "pgi status", "goods issue status"
                    ]
                ),
                Route(
                    name="pending_pod",
                    utterances=[
                        "pending pod", "pod pending", "proof of delivery pending",
                        "pod not received", "pending proof of delivery",
                        "pod missing", "no pod yet", "pod overdue",
                        "pod delay", "pod status", "proof of delivery status"
                    ]
                ),
                Route(
                    name="recent_dns",
                    utterances=[
                        "recent dns", "latest dns", "newest dns",
                        "today's dns", "recent delivery notes", "this week dns",
                        "dns from today", "recent deliveries",
                        "last 10 dns", "recent dns list"
                    ]
                ),
                Route(
                    name="delivery_timeline",
                    utterances=[
                        "delivery timeline", "dn timeline", "track delivery",
                        "delivery history", "delivery progress", "shipment timeline",
                        "delivery journey", "when was it delivered",
                        "delivery route", "shipment tracking", "delivery status timeline"
                    ]
                ),
                Route(
                    name="transit_analysis",
                    utterances=[
                        "transit analysis", "delivery transit", "shipping time",
                        "transit time", "delivery duration", "how long delivery takes",
                        "delivery speed", "transit days", "shipping duration",
                        "average transit time", "transit performance"
                    ]
                ),
                Route(
                    name="top_performers",
                    utterances=[
                        "top performers", "best performers", "top performing",
                        "leaderboard", "top rankings", "top 10",
                        "best performing", "highest performers",
                        "top performers list", "performance leaderboard",
                        "who is best", "top achievers"
                    ]
                ),
                
                # ============================================================
                # DEALER ROUTES
                # ============================================================
                Route(
                    name="dealer_dashboard",
                    utterances=[
                        "show dealer", "dealer dashboard", "tell me about dealer",
                        "dealer details", "dealer profile", "dealer information",
                        "show me dealer", "dealer summary", "dealer overview",
                        "view dealer", "get dealer", "dealer performance",
                        "dealer stats", "dealer data", "dealer insights",
                        "dealer analytics", "dealer report", "dealer information"
                    ]
                ),
                Route(
                    name="dealer_revenue",
                    utterances=[
                        "dealer revenue", "dealer sales", "how much revenue",
                        "revenue of dealer", "dealer earnings", "sales of dealer",
                        "dealer income", "dealer revenue report",
                        "how much did dealer sell", "dealer sales performance",
                        "dealer total revenue", "dealer sales figures",
                        "dealer revenue analytics", "dealer sales report"
                    ]
                ),
                Route(
                    name="dealer_pending",
                    utterances=[
                        "dealer pending", "pending dealer", "dealer overdue",
                        "dealer pending dns", "dealer deliveries pending",
                        "dealer undelivered", "dealer open orders",
                        "dealer pending deliveries", "dealer backlog"
                    ]
                ),
                Route(
                    name="top_dealers",
                    utterances=[
                        "top dealers", "best dealers", "leading dealers",
                        "dealer ranking", "top performing dealers", "dealer rank",
                        "highest revenue dealers", "best performing dealer",
                        "which dealer is best", "dealer performance ranking",
                        "top 10 dealers", "best dealer list",
                        "dealer performance leaderboard", "top dealer"
                    ]
                ),
                Route(
                    name="dealer_comparison",
                    utterances=[
                        "compare dealers", "dealer vs dealer", "dealer comparison",
                        "compare two dealers", "dealer performance comparison",
                        "which dealer is better", "dealer vs", "dealer comparison",
                        "dealer benchmark", "dealer analytics comparison"
                    ]
                ),
                
                # ============================================================
                # WAREHOUSE ROUTES
                # ============================================================
                Route(
                    name="warehouse_dashboard",
                    utterances=[
                        "show warehouse", "warehouse dashboard", "warehouse details",
                        "warehouse information", "tell me about warehouse",
                        "view warehouse", "warehouse performance", "warehouse stats",
                        "warehouse data", "warehouse summary", "warehouse insights",
                        "warehouse analytics", "warehouse report"
                    ]
                ),
                Route(
                    name="warehouse_revenue",
                    utterances=[
                        "warehouse revenue", "warehouse sales", "revenue of warehouse",
                        "warehouse performance", "how much warehouse sold",
                        "warehouse earnings", "warehouse sales figures",
                        "warehouse revenue analytics", "warehouse sales report"
                    ]
                ),
                Route(
                    name="warehouse_pending",
                    utterances=[
                        "warehouse pending", "pending warehouse", "warehouse overdue",
                        "warehouse pending dns", "warehouse undelivered",
                        "warehouse open orders", "warehouse backlog"
                    ]
                ),
                Route(
                    name="top_warehouses",
                    utterances=[
                        "top warehouses", "best warehouses", "leading warehouses",
                        "warehouse ranking", "top performing warehouses",
                        "highest revenue warehouse", "best warehouse list",
                        "warehouse leaderboard"
                    ]
                ),
                
                # ============================================================
                # CITY ROUTES
                # ============================================================
                Route(
                    name="city_dashboard",
                    utterances=[
                        "show city", "city dashboard", "city details",
                        "city information", "tell me about city",
                        "view city", "city performance", "city stats",
                        "city overview", "city analytics", "city data",
                        "city insights", "city summary", "city report"
                    ]
                ),
                Route(
                    name="city_revenue",
                    utterances=[
                        "city revenue", "city sales", "revenue of city",
                        "how much revenue in city", "city sales performance",
                        "city revenue report", "sales in city",
                        "city total revenue", "city sales figures",
                        "city revenue analytics", "city sales report"
                    ]
                ),
                Route(
                    name="city_pending",
                    utterances=[
                        "city pending", "pending in city", "city overdue",
                        "pending dns in city", "city deliveries pending",
                        "city open orders", "city undelivered", "city backlog"
                    ]
                ),
                Route(
                    name="top_cities",
                    utterances=[
                        "top cities", "best cities", "leading cities",
                        "city ranking", "top performing cities",
                        "highest revenue city", "lowest revenue city",
                        "which city has highest sales", "which city has lowest sales",
                        "best performing city", "worst performing city",
                        "city with highest revenue", "city with lowest revenue",
                        "top 10 cities", "best cities list",
                        "city performance leaderboard"
                    ]
                ),
                Route(
                    name="city_comparison",
                    utterances=[
                        "compare cities", "city vs city", "city comparison",
                        "compare two cities", "which city is better",
                        "city comparison", "city vs", "city benchmark"
                    ]
                ),
                
                # ============================================================
                # PRODUCT ROUTES
                # ============================================================
                Route(
                    name="product_dashboard",
                    utterances=[
                        "show product", "product dashboard", "product details",
                        "product information", "tell me about product",
                        "view product", "product performance", "product stats",
                        "product data", "product summary", "product insights",
                        "product analytics", "product report", "product overview"
                    ]
                ),
                Route(
                    name="top_products",
                    utterances=[
                        "top products", "best products", "leading products",
                        "product ranking", "top selling products",
                        "highest revenue product", "best selling product",
                        "top 10 products", "best products list",
                        "product leaderboard", "product performance",
                        "most popular products"
                    ]
                ),
                
                # ============================================================
                # KPI ROUTES
                # ============================================================
                Route(
                    name="national_kpi",
                    utterances=[
                        "national kpi", "kpi dashboard", "overall performance",
                        "national dashboard", "company kpi", "overall kpi",
                        "national metrics", "company performance",
                        "executive dashboard", "business overview",
                        "company health", "overall business performance",
                        "national kpi dashboard", "key performance indicators"
                    ]
                ),
                Route(
                    name="national_revenue",
                    utterances=[
                        "total revenue", "national revenue", "overall revenue",
                        "total sales", "company revenue", "revenue total",
                        "company total sales", "overall earnings",
                        "national sales", "total earnings", "total income"
                    ]
                ),
                Route(
                    name="national_units",
                    utterances=[
                        "total units", "national units", "overall units",
                        "total quantity", "units sold total",
                        "total items sold", "units summary",
                        "national unit sales", "total delivery units"
                    ]
                ),
                
                # ============================================================
                # GENERAL ROUTES
                # ============================================================
                Route(
                    name="greeting",
                    utterances=[
                        "hi", "hello", "hey", "good morning", "good afternoon",
                        "good evening", "salam", "namaste", "howdy",
                        "assalamualaikum", "welcome", "hey there",
                        "greetings", "good day", "what's up", "how are you",
                        "nice to meet you", "hello there"
                    ]
                ),
                Route(
                    name="help",
                    utterances=[
                        "help", "assist", "support", "how to", "what is",
                        "explain", "guide", "help me", "i need help",
                        "how do i", "what can you do", "commands", "instructions",
                        "how does this work", "tutorial", "help desk",
                        "i need assistance", "please help", "support needed"
                    ]
                ),
                Route(
                    name="menu",
                    utterances=[
                        "menu", "options", "services", "what can you do",
                        "show menu", "main menu", "available options",
                        "what are my options", "show services",
                        "list services", "capabilities", "features",
                        "show features", "available services", "menu options"
                    ]
                ),
            ]
            
            # Create router with all routes
            self._router = Router(routes=routes, encoder=encoder)
            
            logger.info(f"✅ Semantic Router initialized with {len(routes)} routes")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize semantic router: {e}")
            self._router = None
            raise
    
    # ============================================================
    # SEMANTIC ROUTING - MAIN DECISION ENGINE
    # ============================================================
    
    def _make_routing_decision(self, message: str) -> RoutingDecision:
        """
        Make routing decision using semantic router.
        This is the MAIN decision engine for ALL messages.
        """
        if not message or not message.strip():
            return RoutingDecision(
                intent="menu",
                confidence=1.0,
                service_key="menu_service",
                service_file="ai_provider_service.py",
                method="show_main_menu",
                entity={},
                requires_ai=False,
                reason="Empty message - showing menu",
                original_message=message,
                menu_option="0"
            )
        
        # ============================================================
        # STEP 1: CHECK FOR DN NUMBER USING REGEX (HIGHEST PRIORITY)
        # ============================================================
        dn = self._extract_dn(message)
        if dn:
            logger.info(f"🔍 DN detected via regex: {dn}")
            return RoutingDecision(
                intent="dn_lookup",
                confidence=1.0,
                service_key="dn_analysis",
                service_file="dn_analysis.py",
                method="get_dn_dashboard",
                entity={"dn": dn, "dn_number": dn},
                requires_ai=False,
                reason=f"DN number detected: {dn}",
                original_message=message,
                menu_option="1"
            )
        
        # ============================================================
        # STEP 2: CHECK FOR MENU KEYWORDS (HIGH PRIORITY)
        # ============================================================
        menu_keyword = self._check_menu_keywords(message)
        if menu_keyword:
            logger.info(f"📋 Menu keyword detected: {menu_keyword}")
            return RoutingDecision(
                intent="menu",
                confidence=1.0,
                service_key="menu_service",
                service_file="ai_provider_service.py",
                method="show_main_menu",
                entity={},
                requires_ai=False,
                reason=f"Menu keyword: {menu_keyword}",
                original_message=message,
                menu_option="0"
            )
        
        # ============================================================
        # STEP 3: CHECK FOR MENU NUMBER SELECTION
        # ============================================================
        menu_number = self._check_menu_number(message)
        if menu_number and menu_number in MENU_OPTIONS:
            menu_config = MENU_OPTIONS[menu_number]
            logger.info(f"📋 Menu selection: {menu_number} -> {menu_config['name']}")
            return RoutingDecision(
                intent=menu_config["name"].lower().replace(" ", "_"),
                confidence=1.0,
                service_key=menu_config["service_key"],
                service_file=menu_config["service_file"],
                method=menu_config["method"],
                entity={},
                requires_ai=menu_config["requires_ai"],
                reason=f"Menu selection: {menu_number}",
                original_message=message,
                menu_option=menu_number
            )
        
        # ============================================================
        # STEP 4: SEMANTIC ROUTER FOR NATURAL LANGUAGE (MAIN ENGINE)
        # ============================================================
        try:
            result = self._router.route(message)
            intent = result.name
            confidence = getattr(result, 'score', 0.0)
            
            logger.info(f"🧠 Semantic Router: intent={intent}, confidence={confidence:.2f}")
            
            # Check if confidence is high enough
            if confidence >= 0.3:
                # Map intent to menu option
                menu_option = INTENT_TO_MENU.get(intent)
                
                if menu_option and menu_option in MENU_OPTIONS:
                    menu_config = MENU_OPTIONS[menu_option]
                    
                    # Extract entities from message
                    entities = self._extract_entities(message)
                    
                    logger.info(f"✅ Semantic routing: {intent} -> menu {menu_option}")
                    
                    return RoutingDecision(
                        intent=intent,
                        confidence=confidence,
                        service_key=menu_config["service_key"],
                        service_file=menu_config["service_file"],
                        method=menu_config["method"],
                        entity=entities,
                        requires_ai=menu_config["requires_ai"],
                        reason=f"Semantic routing: {intent} (confidence: {confidence:.2f})",
                        original_message=message,
                        menu_option=menu_option
                    )
            
            # If confidence is low or no mapping found, fallback to AI
            logger.info(f"⬇️ Low confidence ({confidence:.2f}) or no mapping - AI fallback")
            
        except Exception as e:
            logger.error(f"❌ Semantic router error: {e}")
        
        # ============================================================
        # STEP 5: AI FALLBACK
        # ============================================================
        entities = self._extract_entities(message)
        
        return RoutingDecision(
            intent="general_ai",
            confidence=0.3,
            service_key="groq_service",
            service_file="groq_service.py",
            method="process_query",
            entity=entities or {"message": message},
            requires_ai=True,
            reason="AI fallback - no semantic intent matched",
            original_message=message,
            menu_option="9"
        )
    
    # ============================================================
    # HELPER METHODS
    # ============================================================
    
    def _extract_dn(self, text: str) -> Optional[str]:
        """Extract DN number using regex"""
        if not text:
            return None
        
        text = text.strip()
        
        # Pattern 1: Exactly 10 digits
        match = re.search(r'(?<!\d)(\d{10})(?!\d)', text)
        if match:
            return match.group(1)
        
        # Pattern 2: 8-12 digits
        match = re.search(r'(?<!\d)(\d{8,12})(?!\d)', text)
        if match:
            return match.group(1)
        
        # Pattern 3: With spaces
        match = re.search(r'(?<!\d)(\d{4}\s*\d{4}\s*\d{2,4})(?!\d)', text)
        if match:
            return re.sub(r'\s', '', match.group(1))
        
        # Pattern 4: With dashes
        match = re.search(r'(?<!\d)(\d{4}-\d{4}-\d{2,4})(?!\d)', text)
        if match:
            return re.sub(r'-', '', match.group(1))
        
        return None
    
    def _check_menu_keywords(self, text: str) -> Optional[str]:
        """Check if text contains menu keywords"""
        if not text:
            return None
        
        text_lower = text.strip().lower()
        keywords = ["menu", "main menu", "help", "start", "options", "back", "home"]
        
        for keyword in keywords:
            if keyword in text_lower:
                return keyword
        
        return None
    
    def _check_menu_number(self, text: str) -> Optional[str]:
        """Check if text is a menu number"""
        if not text:
            return None
        
        text = text.strip()
        
        # Single digit 0-9
        if re.match(r'^[0-9]$', text):
            return text
        
        # Number with dot or space
        if re.match(r'^[0-9][\.\s\)]', text):
            return text[0]
        
        return None
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract entities from message using regex"""
        entities = {}
        
        if not text:
            return entities
        
        # Extract DN
        dn = self._extract_dn(text)
        if dn:
            entities["dn"] = dn
            entities["dn_number"] = dn
            entities["id"] = dn
        
        # Extract dealer name
        dealer_match = re.search(
            r'([\w\s]{3,}(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation))',
            text, re.IGNORECASE
        )
        if dealer_match:
            entities["dealer_name"] = dealer_match.group(1).strip()
            entities["dealer"] = dealer_match.group(1).strip()
        
        # Extract city name
        city_names = [
            "abbottabad", "lahore", "karachi", "rawalpindi", "quetta",
            "multan", "peshawar", "gilgit", "hyderabad", "islamabad",
            "sialkot", "gujranwala", "faisalabad", "bahawalpur", "sukkur",
            "mansehra", "haripur", "dg khan", "dera ghazi khan"
        ]
        text_lower = text.lower()
        for city in city_names:
            if city in text_lower:
                entities["city"] = city.title()
                entities["city_name"] = city.title()
                break
        
        # Extract warehouse name
        warehouse_match = re.search(r'(?:warehouse|wh|depot)\s+([a-z0-9\s&\-\.]{2,})', text, re.IGNORECASE)
        if warehouse_match:
            entities["warehouse"] = warehouse_match.group(1).strip()
        
        # Extract product name
        product_match = re.search(r'(?:product|model|material|item)\s+([a-z0-9\s&\-\.]{2,})', text, re.IGNORECASE)
        if product_match:
            entities["product"] = product_match.group(1).strip()
        
        return entities
    
    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================
    
    async def process_whatsapp_query(self, message: str, sender: Optional[str] = None) -> str:
        """
        Single entry point for all WhatsApp messages.
        Uses semantic-router for ALL intent detection and routing decisions.
        """
        if not message or not message.strip():
            return get_main_menu()
        
        logger.info(f"📩 Processing: '{message[:50]}...' from {sender}")
        
        # Make routing decision using semantic router
        decision = self._make_routing_decision(message)
        
        logger.info(f"📋 Decision: {decision.intent} -> {decision.service_file}.{decision.method}")
        
        # ============================================================
        # HANDLE DIFFERENT ROUTING DECISIONS
        # ============================================================
        
        # Menu service
        if decision.service_key == "menu_service":
            return get_main_menu()
        
        # Route to appropriate service
        service_map = {
            "dn_analysis": self.dn_service,
            "dealer_analytics": self.dealer_service,
            "city_service": self.city_service,
            "product_service": self.product_service,
            "national_kpi_service": self.national_kpi_service,
            "groq_service": self.groq_service,
        }
        
        service = service_map.get(decision.service_key)
        if not service:
            logger.error(f"❌ Service not found: {decision.service_key}")
            return get_invalid_selection_message()
        
        # Call the appropriate method
        try:
            method = getattr(service, decision.method)
            
            if decision.service_key == "groq_service":
                # AI service needs the original message
                result = await method(message, decision.entity)
            else:
                # All other services use entities
                result = await method(decision.entity)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Service error: {e}")
            return f"⚠️ Error: {str(e)}"

# ============================================================
# SINGLETON INSTANCE
# ============================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()

def get_ai_provider_service() -> AIProviderService:
    """Get singleton instance"""
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                _ai_service = AIProviderService()
                logger.info("✅ AIProviderService initialized")
    return _ai_service

# ============================================================
# MAIN ENTRY POINT
# ============================================================

async def process_whatsapp_query(message: str, sender: Optional[str] = None) -> str:
    """Main entry point - Call this from webhook"""
    service = get_ai_provider_service()
    return await service.process_whatsapp_query(message, sender)

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "process_whatsapp_query",
    "get_main_menu",
    "get_ai_provider_service",
    "RoutingDecision",
    "MENU_OPTIONS",
    "INTENT_TO_MENU"
]
