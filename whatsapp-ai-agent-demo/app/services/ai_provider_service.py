"""
File: app/services/intent_routing_service.py
Version: 2.0 - ENHANCED ROUTING ENGINE
Purpose: Pure intent detection and routing engine
         Routes requests to appropriate services based on intent detection

Key Features:
- ✅ Semantic Intent Detection using semantic-router
- ✅ Priority-Based Routing (Regex > Semantic > AI Fallback)
- ✅ Direct DN Number Detection with Validation
- ✅ Entity Extraction and Validation
- ✅ Routes to specific service files
- ✅ Confidence Scoring and Thresholds
- ✅ Cache Management with TTL
- ✅ No External API Keys Needed (HuggingFace Encoder)
- ✅ Singleton Pattern for Performance
- ✅ Health Monitoring and Metrics
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from collections import OrderedDict

# ============================================================
# SINGLE LIBRARY: semantic-router
# ============================================================

try:
    from semantic_router import Route, Router
    from semantic_router.encoders import HuggingFaceEncoder
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
    """Final routing decision with priority levels"""
    intent: str
    confidence: float
    service_key: str  # e.g., "dn_analysis", "dealer_analytics", "groq_service"
    method: str       # e.g., "get_dn_dashboard", "get_dealer_dashboard"
    entity: Dict[str, Any]
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    priority: int = 0  # 1=Highest (DN), 2=High (Service), 3=Medium, 4=Low (AI)

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
            "priority": self.priority
        }


# ============================================================
# SEMANTIC ROUTER INTENT ENGINE
# ============================================================

class SemanticRouterIntentEngine:
    """
    Pure intent detection and routing engine.
    
    Priority System:
    1. DN Number Detection (Regex) - HIGHEST PRIORITY
    2. Service Intent Detection (Semantic Router)
    3. AI Fallback (When confidence is low or no intent matches)
    """
    
    _instance: Optional["SemanticRouterIntentEngine"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "SemanticRouterIntentEngine":
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
        self._router: Optional[Router] = None
        self._cache: OrderedDict[str, RoutingDecision] = OrderedDict()
        self._cache_ttl = 300  # 5 minutes
        self._cache_max_size = 1000
        
        # Initialize semantic router
        self._init_router()
        
        logger.info("✅ SemanticRouterIntentEngine initialized")
    
    def _init_router(self):
        """Initialize semantic router with all routes"""
        try:
            # Use free HuggingFace encoder (no API key needed)
            encoder = HuggingFaceEncoder()
            
            # Define all routes
            routes = [
                # ============================================================
                # DN ROUTES - Delivery Note Management (dn_analysis.py)
                # ============================================================
                Route(
                    name="dn_lookup",
                    utterances=[
                        "show dn", "dn dashboard", "delivery note", "track dn",
                        "dn number", "dn status", "check dn", "delivery note number",
                        "delivery note dashboard", "view dn", "get dn",
                        "delivery note status", "track delivery note",
                        "dn details", "dn information", "tell me about dn",
                        "dn overview", "dn data"
                    ]
                ),
                Route(
                    name="dn_status",
                    utterances=[
                        "dn status", "status of dn", "check dn status",
                        "what is the status of dn", "delivery note status",
                        "is dn delivered", "dn delivery status",
                        "current status of dn", "where is my dn",
                        "delivery status", "shipment status"
                    ]
                ),
                Route(
                    name="dn_history",
                    utterances=[
                        "dn history", "history of dn", "delivery note history",
                        "show dn history", "dn timeline", "delivery note timeline",
                        "dn log", "dn tracking history", "dn audit",
                        "previous dn records"
                    ]
                ),
                Route(
                    name="dn_summary",
                    utterances=[
                        "dn summary", "summary of dns", "total dns",
                        "dn overview", "delivery note summary", "dn statistics",
                        "total delivery notes", "dn count", "number of dns",
                        "dn total", "dn stats"
                    ]
                ),
                Route(
                    name="pending_dns",
                    utterances=[
                        "pending dns", "pending deliveries", "show pending",
                        "list pending", "pending delivery notes", "open dns",
                        "undelivered dns", "outstanding deliveries",
                        "dns not delivered yet", "pending orders",
                        "dns pending delivery"
                    ]
                ),
                Route(
                    name="pending_pgi",
                    utterances=[
                        "pending pgi", "pgi pending", "goods issue pending",
                        "pgi not done", "pending goods issue",
                        "goods issue not completed", "pgi delay",
                        "pgi overdue"
                    ]
                ),
                Route(
                    name="pending_pod",
                    utterances=[
                        "pending pod", "pod pending", "proof of delivery pending",
                        "pod not received", "pending proof of delivery",
                        "pod missing", "no pod yet", "pod overdue",
                        "pod delay"
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
                        "delivery route", "shipment tracking"
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
                    name="dn_analytics",
                    utterances=[
                        "dn analytics", "dn analysis", "dn performance",
                        "delivery analytics", "dn metrics", "dn insights",
                        "dn kpi", "dn trends"
                    ]
                ),
                
                # ============================================================
                # DEALER ROUTES - Dealer Management (dealer_analytics_service.py)
                # ============================================================
                Route(
                    name="dealer_dashboard",
                    utterances=[
                        "show dealer", "dealer dashboard", "tell me about dealer",
                        "dealer details", "dealer profile", "dealer information",
                        "show me dealer", "dealer summary", "dealer overview",
                        "view dealer", "get dealer", "dealer performance",
                        "dealer stats", "dealer data", "dealer insights"
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
                        "dealer revenue analytics"
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
                        "dealer performance leaderboard"
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
                Route(
                    name="dealer_analytics",
                    utterances=[
                        "dealer analytics", "dealer analysis", "dealer insights",
                        "dealer performance metrics", "dealer kpi",
                        "dealer trends", "dealer data analysis"
                    ]
                ),
                
                # ============================================================
                # WAREHOUSE ROUTES - Warehouse Management (dn_analysis.py)
                # ============================================================
                Route(
                    name="warehouse_dashboard",
                    utterances=[
                        "show warehouse", "warehouse dashboard", "warehouse details",
                        "warehouse information", "tell me about warehouse",
                        "view warehouse", "warehouse performance", "warehouse stats",
                        "warehouse data", "warehouse summary", "warehouse insights"
                    ]
                ),
                Route(
                    name="warehouse_revenue",
                    utterances=[
                        "warehouse revenue", "warehouse sales", "revenue of warehouse",
                        "warehouse performance", "how much warehouse sold",
                        "warehouse earnings", "warehouse sales figures",
                        "warehouse revenue analytics"
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
                # CITY ROUTES - City Management (city_service.py)
                # ============================================================
                Route(
                    name="city_dashboard",
                    utterances=[
                        "show city", "city dashboard", "city details",
                        "city information", "tell me about city",
                        "view city", "city performance", "city stats",
                        "city overview", "city analytics", "city data",
                        "city insights", "city summary"
                    ]
                ),
                Route(
                    name="city_revenue",
                    utterances=[
                        "city revenue", "city sales", "revenue of city",
                        "how much revenue in city", "city sales performance",
                        "city revenue report", "sales in city",
                        "city total revenue", "city sales figures",
                        "city revenue analytics"
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
                Route(
                    name="city_analytics",
                    utterances=[
                        "city analytics", "city analysis", "city insights",
                        "city performance metrics", "city kpi", "city trends"
                    ]
                ),
                
                # ============================================================
                # PRODUCT ROUTES - Product Management (product_service.py)
                # ============================================================
                Route(
                    name="product_dashboard",
                    utterances=[
                        "show product", "product dashboard", "product details",
                        "product information", "tell me about product",
                        "view product", "product performance", "product stats",
                        "product data", "product summary", "product insights"
                    ]
                ),
                Route(
                    name="top_products",
                    utterances=[
                        "top products", "best products", "leading products",
                        "product ranking", "top selling products",
                        "highest revenue product", "best selling product",
                        "top 10 products", "best products list",
                        "product leaderboard", "product performance"
                    ]
                ),
                Route(
                    name="product_analytics",
                    utterances=[
                        "product analytics", "product analysis", "product insights",
                        "product performance metrics", "product kpi",
                        "product trends", "product sales analysis"
                    ]
                ),
                
                # ============================================================
                # KPI ROUTES - Performance Metrics (kpi_service.py & national_kpi_service.py)
                # ============================================================
                Route(
                    name="national_kpi",
                    utterances=[
                        "national kpi", "kpi dashboard", "overall performance",
                        "national dashboard", "company kpi", "overall kpi",
                        "national metrics", "company performance",
                        "executive dashboard", "business overview",
                        "company health", "overall business performance",
                        "national kpi dashboard"
                    ]
                ),
                Route(
                    name="national_revenue",
                    utterances=[
                        "total revenue", "national revenue", "overall revenue",
                        "total sales", "company revenue", "revenue total",
                        "company total sales", "overall earnings",
                        "national sales", "total earnings"
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
                Route(
                    name="kpi_analytics",
                    utterances=[
                        "kpi analytics", "kpi analysis", "kpi insights",
                        "business kpi", "performance metrics", "kpi trends",
                        "business analytics", "performance dashboard"
                    ]
                ),
                
                # ============================================================
                # GENERAL ROUTES - User Interaction (groq_service.py)
                # ============================================================
                Route(
                    name="greeting",
                    utterances=[
                        "hi", "hello", "hey", "good morning", "good afternoon",
                        "good evening", "salam", "namaste", "howdy",
                        "assalamualaikum", "welcome", "hey there",
                        "greetings", "good day", "what's up", "how are you",
                        "nice to meet you"
                    ]
                ),
                Route(
                    name="help",
                    utterances=[
                        "help", "assist", "support", "how to", "what is",
                        "explain", "guide", "help me", "i need help",
                        "how do i", "what can you do", "commands", "instructions",
                        "how does this work", "tutorial", "help desk",
                        "i need assistance", "please help"
                    ]
                ),
                Route(
                    name="menu",
                    utterances=[
                        "menu", "options", "services", "what can you do",
                        "show menu", "main menu", "available options",
                        "what are my options", "show services",
                        "list services", "capabilities", "features",
                        "show features", "available services"
                    ]
                ),
                Route(
                    name="general_ai",
                    utterances=[
                        "tell me", "what", "how", "why", "when", "where",
                        "who", "which", "explain", "describe", "summarize",
                        "analyze", "give me", "show me", "i want"
                    ]
                ),
            ]
            
            # Create router
            self._router = Router(routes=routes, encoder=encoder)
            
            logger.info(f"✅ Semantic Router initialized with {len(routes)} routes")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize semantic router: {e}")
            raise
    
    # ============================================================
    # DN DETECTION (Regex - Highest Priority)
    # ============================================================
    
    def _extract_dn(self, text: str) -> Optional[str]:
        """
        Extract DN number using regex patterns with validation.
        Priority 1: Direct 10-digit numbers
        Priority 2: Numbers with spaces
        Priority 3: Numbers with special characters
        """
        # Pattern 1: Direct 10-digit number
        match = re.search(r'(?<!\d)(\d{10})(?!\d)', text)
        if match:
            dn = match.group(1)
            if self._validate_dn(dn):
                return dn
        
        # Pattern 2: 8-12 digits
        match = re.search(r'(?<!\d)(\d{8,12})(?!\d)', text)
        if match:
            dn = match.group(1)
            if self._validate_dn(dn):
                return dn
        
        # Pattern 3: Numbers with spaces
        match = re.search(r'(?<!\d)(\d{4}\s*\d{4}\s*\d{2,4})(?!\d)', text)
        if match:
            dn = re.sub(r'\s', '', match.group(1))
            if self._validate_dn(dn):
                return dn
        
        # Pattern 4: Numbers with dashes
        match = re.search(r'(?<!\d)(\d{4}-\d{4}-\d{2,4})(?!\d)', text)
        if match:
            dn = re.sub(r'-', '', match.group(1))
            if self._validate_dn(dn):
                return dn
        
        return None
    
    def _validate_dn(self, dn: str) -> bool:
        """
        Validate DN number format.
        Returns True if DN is valid.
        """
        if not dn:
            return False
        
        # Must be between 8 and 12 digits
        if not (8 <= len(dn) <= 12):
            return False
        
        # Must be all digits
        if not dn.isdigit():
            return False
        
        return True
    
    # ============================================================
    # ENTITY EXTRACTION
    # ============================================================
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """
        Extract entities from message.
        Returns structured entity dictionary.
        """
        entities = {}
        
        # Extract DN
        dn = self._extract_dn(text)
        if dn:
            entities["dn"] = dn
            entities["dn_number"] = dn
            entities["id"] = dn
        
        # Extract dealer name
        dealer_patterns = [
            r'(?:dealer|about|for|company|customer|tell me about|show me|get|view)\s+([a-z0-9\s&\-\.]{3,})',
            r'([\w\s]{3,}(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation))',
            r'dealer\s*(?:name|:)?\s*([a-z0-9\s&\-\.]{3,})',
        ]
        for pattern in dealer_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dealer_name = match.group(1).strip()
                if 3 <= len(dealer_name) <= 100:
                    entities["dealer_name"] = dealer_name
                    entities["dealer"] = dealer_name
                    break
        
        # Extract city name
        city_names = [
            "abbottabad", "lahore", "karachi", "rawalpindi", "quetta",
            "multan", "peshawar", "gilgit", "hyderabad", "islamabad",
            "sialkot", "gujranwala", "faisalabad", "bahawalpur", "sukkur",
            "dg khan", "dera ghazi khan", "rahim yar khan", "gwadar",
            "attock", "jehlum", "sargodha", "mianwali", "bhakkar",
            "chakwal", "jhang", "kasur", "okara", "sahiwal"
        ]
        text_lower = text.lower()
        for city in city_names:
            if city in text_lower:
                entities["city"] = city.title()
                entities["city_name"] = city.title()
                break
        
        # Extract warehouse name
        warehouse_patterns = [
            r'(?:warehouse|wh|depot)\s+([a-z0-9\s&\-\.]{2,})',
            r'warehouse\s*(?:name|:)?\s*([a-z0-9\s&\-\.]{2,})',
        ]
        for pattern in warehouse_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                warehouse_name = match.group(1).strip()
                if 2 <= len(warehouse_name) <= 50:
                    entities["warehouse"] = warehouse_name
                    break
        
        # Extract product name
        product_patterns = [
            r'(?:product|model|material|item)\s+([a-z0-9\s&\-\.]{2,})',
            r'product\s*(?:name|:)?\s*([a-z0-9\s&\-\.]{2,})',
        ]
        for pattern in product_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                product_name = match.group(1).strip()
                if 2 <= len(product_name) <= 50:
                    entities["product"] = product_name
                    break
        
        # Extract date ranges
        date_match = re.search(r'(?:from|between)\s+(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', text, re.IGNORECASE)
        if date_match:
            entities["date_from"] = date_match.group(1)
        
        date_match = re.search(r'(?:to|until)\s+(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', text, re.IGNORECASE)
        if date_match:
            entities["date_to"] = date_match.group(1)
        
        return entities
    
    # ============================================================
    # INTENT TO SERVICE MAPPING
    # ============================================================
    
    def _get_service_mapping(self, intent: str, entities: Dict[str, Any]) -> Tuple[str, str, bool, int]:
        """
        Map intent to service, method, AI requirement, and priority.
        Returns: (service_key, method, requires_ai, priority)
        
        Service Files Mapping:
        1. dn_analysis.py - All DN related operations
        2. dealer_analytics_service.py - Dealer analytics and operations
        3. city_service.py - City related operations
        4. product_service.py - Product related operations
        5. kpi_service.py - KPI operations
        6. national_kpi_service.py - National level KPI operations
        7. groq_service.py - AI/General queries
        """
        
        # DN Service Intents (dn_analysis.py) - Priority 2
        dn_intents = {
            "dn_lookup": ("dn_analysis", "get_dn_dashboard", False, 2),
            "dn_status": ("dn_analysis", "get_dn_status", False, 2),
            "dn_history": ("dn_analysis", "get_dn_history", False, 2),
            "dn_summary": ("dn_analysis", "get_dn_summary", False, 2),
            "pending_dns": ("dn_analysis", "get_pending_dns", False, 2),
            "pending_pgi": ("dn_analysis", "get_pending_pgi", False, 2),
            "pending_pod": ("dn_analysis", "get_pending_pod", False, 2),
            "recent_dns": ("dn_analysis", "get_recent_dns", False, 2),
            "delivery_timeline": ("dn_analysis", "get_delivery_timeline", False, 2),
            "transit_analysis": ("dn_analysis", "get_transit_analysis", False, 2),
            "dn_analytics": ("dn_analysis", "get_dn_analytics", False, 2),
        }
        
        # Dealer Service Intents (dealer_analytics_service.py) - Priority 3
        dealer_intents = {
            "dealer_dashboard": ("dealer_analytics", "get_dealer_dashboard", False, 3),
            "dealer_revenue": ("dealer_analytics", "get_dealer_revenue", False, 3),
            "dealer_pending": ("dealer_analytics", "get_dealer_pending", False, 3),
            "top_dealers": ("dealer_analytics", "get_top_dealers", False, 3),
            "dealer_comparison": ("dealer_analytics", "compare_dealers", False, 3),
            "dealer_analytics": ("dealer_analytics", "get_dealer_analytics", False, 3),
        }
        
        # Warehouse Service Intents (dn_analysis.py) - Priority 3
        warehouse_intents = {
            "warehouse_dashboard": ("dn_analysis", "get_warehouse_dashboard", False, 3),
            "warehouse_revenue": ("dn_analysis", "get_warehouse_revenue", False, 3),
            "warehouse_pending": ("dn_analysis", "get_warehouse_pending", False, 3),
            "top_warehouses": ("dn_analysis", "get_top_warehouses", False, 3),
        }
        
        # City Service Intents (city_service.py) - Priority 3
        city_intents = {
            "city_dashboard": ("city_service", "get_city_dashboard", False, 3),
            "city_revenue": ("city_service", "get_city_revenue", False, 3),
            "city_pending": ("city_service", "get_city_pending", False, 3),
            "top_cities": ("city_service", "get_top_cities", False, 3),
            "city_comparison": ("city_service", "compare_cities", False, 3),
            "city_analytics": ("city_service", "get_city_analytics", False, 3),
        }
        
        # Product Service Intents (product_service.py) - Priority 3
        product_intents = {
            "product_dashboard": ("product_service", "get_product_dashboard", False, 3),
            "top_products": ("product_service", "get_top_products", False, 3),
            "product_analytics": ("product_service", "get_product_analytics", False, 3),
        }
        
        # KPI Service Intents (kpi_service.py) - Priority 3
        kpi_intents = {
            "kpi_analytics": ("kpi_service", "get_kpi_analytics", False, 3),
        }
        
        # National KPI Service Intents (national_kpi_service.py) - Priority 3
        national_kpi_intents = {
            "national_kpi": ("national_kpi_service", "get_national_kpi_dashboard", False, 3),
            "national_revenue": ("national_kpi_service", "get_national_revenue", False, 3),
            "national_units": ("national_kpi_service", "get_national_units", False, 3),
        }
        
        # General Intents (groq_service.py) - Priority 4 (AI Required)
        general_intents = {
            "greeting": ("groq_service", "process_query", True, 4),
            "help": ("groq_service", "process_query", True, 4),
            "menu": ("groq_service", "process_query", True, 4),
            "general_ai": ("groq_service", "process_query", True, 4),
        }
        
        # Combine all mappings
        all_mappings = {
            **dn_intents,
            **dealer_intents,
            **warehouse_intents,
            **city_intents,
            **product_intents,
            **kpi_intents,
            **national_kpi_intents,
            **general_intents
        }
        
        if intent in all_mappings:
            return all_mappings[intent]
        
        # Default to AI fallback
        return ("groq_service", "process_query", True, 4)
    
    # ============================================================
    # MAIN DETECTION METHOD
    # ============================================================
    
    def detect(self, message: str) -> RoutingDecision:
        """
        Detect intent and route using priority system.
        
        Priority Order:
        1. DN Number Detection (Regex) - HIGHEST
        2. Semantic Router (Intent Detection)
        3. AI Fallback (Low Confidence or No Intent)
        """
        message_clean = message.strip()
        
        # ============================================================
        # STAGE 1: DN NUMBER DETECTION (HIGHEST PRIORITY)
        # ============================================================
        dn = self._extract_dn(message_clean)
        if dn:
            entities = {"dn": dn, "dn_number": dn, "id": dn}
            
            # Try to extract additional entities
            additional_entities = self._extract_entities(message_clean)
            entities.update(additional_entities)
            
            logger.info(f"🔍 DN detected: {dn} - Direct routing to dn_analysis service")
            
            return RoutingDecision(
                intent="dn_lookup",
                confidence=1.0,
                service_key="dn_analysis",
                method="get_dn_dashboard",
                entity=entities,
                requires_ai=False,
                reason=f"DN number detected and validated: {dn}",
                original_message=message_clean,
                priority=1  # Highest priority
            )
        
        # ============================================================
        # STAGE 2: ENTITY EXTRACTION
        # ============================================================
        entities = self._extract_entities(message_clean)
        
        # ============================================================
        # STAGE 3: SEMANTIC ROUTER (Intent Detection)
        # ============================================================
        try:
            result = self._router.route(message_clean)
            
            intent = result.name
            confidence = getattr(result, 'score', 0.85)
            
            logger.info(f"🧠 Semantic Router: intent={intent}, confidence={confidence:.2f}")
            
            # ============================================================
            # STAGE 4: SERVICE MAPPING
            # ============================================================
            service_key, method, requires_ai, priority = self._get_service_mapping(intent, entities)
            
            # ============================================================
            # STAGE 5: ENTITY ENRICHMENT
            # ============================================================
            
            # Enrich DN-related intents with DN from message
            if intent.startswith("dn_") and "dn" not in entities:
                dn_in_message = self._extract_dn(message_clean)
                if dn_in_message:
                    entities["dn"] = dn_in_message
                    entities["dn_number"] = dn_in_message
            
            # Enrich dealer intents
            if intent.startswith("dealer_") and "dealer" not in entities:
                dealer_match = re.search(
                    r'([\w\s]{3,}(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation))',
                    message_clean, re.IGNORECASE
                )
                if dealer_match:
                    entities["dealer_name"] = dealer_match.group(1).strip()
                    entities["dealer"] = dealer_match.group(1).strip()
            
            # Enrich city intents
            if intent.startswith("city_") and "city" not in entities:
                city_names = [
                    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta",
                    "multan", "peshawar", "gilgit", "hyderabad", "islamabad",
                    "sialkot", "gujranwala", "faisalabad", "bahawalpur", "sukkur"
                ]
                message_lower = message_clean.lower()
                for city in city_names:
                    if city in message_lower:
                        entities["city"] = city.title()
                        entities["city_name"] = city.title()
                        break
            
            # Enrich warehouse intents
            if intent.startswith("warehouse_") and "warehouse" not in entities:
                warehouse_match = re.search(
                    r'(?:warehouse|wh|depot)\s+([a-z0-9\s&\-\.]{2,})',
                    message_clean, re.IGNORECASE
                )
                if warehouse_match:
                    entities["warehouse"] = warehouse_match.group(1).strip()
            
            # Enrich product intents
            if intent.startswith("product_") and "product" not in entities:
                product_match = re.search(
                    r'(?:product|model|material|item)\s+([a-z0-9\s&\-\.]{2,})',
                    message_clean, re.IGNORECASE
                )
                if product_match:
                    entities["product"] = product_match.group(1).strip()
            
            # ============================================================
            # STAGE 6: CONFIDENCE CHECK
            # ============================================================
            
            # If confidence is too low and intent is not DN-related
            if confidence < 0.3 and not intent.startswith("dn_"):
                logger.info(f"⬇️ Low confidence ({confidence:.2f}) - Falling back to AI")
                return RoutingDecision(
                    intent="general_ai",
                    confidence=confidence,
                    service_key="groq_service",
                    method="process_query",
                    entity=entities or {"message": message_clean},
                    requires_ai=True,
                    reason=f"Low confidence ({confidence:.2f}) - AI fallback",
                    original_message=message_clean,
                    priority=4
                )
            
            # ============================================================
            # STAGE 7: RETURN DECISION
            # ============================================================
            
            logger.info(f"✅ Routing: {intent} -> {service_key}.{method} (priority={priority})")
            
            return RoutingDecision(
                intent=intent,
                confidence=confidence,
                service_key=service_key,
                method=method,
                entity=entities,
                requires_ai=requires_ai,
                reason=f"Semantic route: {intent} (confidence: {confidence:.2f})",
                original_message=message_clean,
                priority=priority
            )
            
        except Exception as e:
            logger.error(f"❌ Semantic router error: {e}")
        
        # ============================================================
        # STAGE 8: FALLBACK (No Intent Detected)
        # ============================================================
        logger.info("🔄 No intent matched - Using AI fallback")
        return RoutingDecision(
            intent="general_ai",
            confidence=0.3,
            service_key="groq_service",
            method="process_query",
            entity=entities or {"message": message_clean},
            requires_ai=True,
            reason="Fallback - no intent matched",
            original_message=message_clean,
            priority=4
        )


# ============================================================
# INTENT ROUTING SERVICE
# ============================================================

class IntentRoutingService:
    """
    Enterprise Intent Routing Service.
    Wraps the SemanticRouterIntentEngine for easy integration.
    """
    
    def __init__(self):
        self._engine = SemanticRouterIntentEngine()
        self._cache: OrderedDict[str, RoutingDecision] = OrderedDict()
        self._cache_ttl = 300  # 5 minutes
        self._cache_max_size = 1000
        self._stats = {
            "total_requests": 0,
            "dn_detections": 0,
            "semantic_routes": 0,
            "ai_fallbacks": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }
    
    def detect_intent(self, message: str) -> Dict[str, Any]:
        """
        Detect intent and return routing decision.
        
        Returns:
            {
                "intent": str,
                "confidence": float,
                "service_key": str,  # e.g., "dn_analysis", "dealer_analytics", "city_service"
                "method": str,
                "entity": dict,
                "requires_ai": bool,
                "reason": str,
                "priority": int
            }
        """
        self._stats["total_requests"] += 1
        
        # Check cache
        cache_key = message.strip().lower()
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            # Check if cache is still valid
            age = time.time() - getattr(cached, '_timestamp', time.time())
            if age < self._cache_ttl:
                self._stats["cache_hits"] += 1
                logger.debug(f"✅ Cache hit for: {message[:50]}...")
                return cached.to_dict()
        
        self._stats["cache_misses"] += 1
        
        # Detect intent
        decision = self._engine.detect(message)
        
        # Update stats
        if decision.priority == 1:  # DN detection
            self._stats["dn_detections"] += 1
        elif decision.priority <= 3:  # Semantic route
            self._stats["semantic_routes"] += 1
        else:  # AI fallback
            self._stats["ai_fallbacks"] += 1
        
        # Cache result
        decision._timestamp = time.time()
        self._cache[cache_key] = decision
        if len(self._cache) > self._cache_max_size:
            self._clean_cache()
        
        return decision.to_dict()
    
    def _clean_cache(self):
        """Clean old cache entries"""
        current_time = time.time()
        keys_to_remove = []
        for key, value in self._cache.items():
            if current_time - getattr(value, '_timestamp', current_time) > self._cache_ttl:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self._cache[key]
    
    def clear_cache(self):
        """Clear the cache"""
        self._cache.clear()
        logger.info("🧹 Cache cleared")
    
    def get_supported_intents(self) -> List[str]:
        """Get list of supported intents"""
        return [
            # DN Intents (dn_analysis.py)
            "dn_lookup", "dn_status", "dn_history", "dn_summary",
            "pending_dns", "pending_pgi", "pending_pod", "recent_dns",
            "delivery_timeline", "transit_analysis", "dn_analytics",
            # Dealer Intents (dealer_analytics_service.py)
            "dealer_dashboard", "dealer_revenue", "dealer_pending",
            "top_dealers", "dealer_comparison", "dealer_analytics",
            # Warehouse Intents (dn_analysis.py)
            "warehouse_dashboard", "warehouse_revenue", "warehouse_pending",
            "top_warehouses",
            # City Intents (city_service.py)
            "city_dashboard", "city_revenue", "city_pending",
            "top_cities", "city_comparison", "city_analytics",
            # Product Intents (product_service.py)
            "product_dashboard", "top_products", "product_analytics",
            # KPI Intents (kpi_service.py & national_kpi_service.py)
            "national_kpi", "national_revenue", "national_units",
            "kpi_analytics",
            # General Intents (groq_service.py)
            "greeting", "help", "menu", "general_ai"
        ]
    
    def get_service_mapping(self) -> Dict[str, str]:
        """Get mapping of intents to service files"""
        return {
            "DN Operations": "dn_analysis.py",
            "Dealer Operations": "dealer_analytics_service.py",
            "City Operations": "city_service.py",
            "Product Operations": "product_service.py",
            "KPI Operations": "kpi_service.py",
            "National KPI": "national_kpi_service.py",
            "General/AI": "groq_service.py"
        }
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for the service"""
        return {
            "service": "intent_routing_service",
            "version": "2.0",
            "available": SEMANTIC_ROUTER_AVAILABLE,
            "cache_size": len(self._cache),
            "cache_max_size": self._cache_max_size,
            "cache_ttl": self._cache_ttl,
            "supported_intents": len(self.get_supported_intents()),
            "service_files": self.get_service_mapping(),
            "stats": self._stats,
            "status": "healthy" if SEMANTIC_ROUTER_AVAILABLE else "degraded"
        }


# ============================================================
# SINGLETON INSTANCE
# ============================================================

_intent_service: Optional[IntentRoutingService] = None
_service_lock = threading.Lock()


def get_intent_routing_service() -> IntentRoutingService:
    """Get singleton instance of IntentRoutingService"""
    global _intent_service
    if _intent_service is None:
        with _service_lock:
            if _intent_service is None:
                _intent_service = IntentRoutingService()
                logger.info("✅ IntentRoutingService singleton initialized")
    return _intent_service


# ============================================================
# MODULE-LEVEL FUNCTIONS
# ============================================================

def detect_intent(message: str) -> Dict[str, Any]:
    """Module-level function for intent detection"""
    service = get_intent_routing_service()
    return service.detect_intent(message)


def get_supported_intents() -> List[str]:
    """Get list of supported intents"""
    service = get_intent_routing_service()
    return service.get_supported_intents()


def health_check() -> Dict[str, Any]:
    """Health check"""
    service = get_intent_routing_service()
    return service.health_check()


def clear_cache() -> None:
    """Clear the cache"""
    service = get_intent_routing_service()
    service.clear_cache()


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "IntentRoutingService",
    "SemanticRouterIntentEngine",
    "RoutingDecision",
    "get_intent_routing_service",
    "detect_intent",
    "get_supported_intents",
    "health_check",
    "clear_cache",
]
