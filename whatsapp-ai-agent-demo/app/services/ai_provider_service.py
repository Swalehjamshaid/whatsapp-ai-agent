"""
File: app/services/intent_routing_service.py
Version: 3.0 - FIXED ROUTING
Purpose: Pure intent detection and routing engine with working DN detection
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
    """Final routing decision"""
    intent: str
    confidence: float
    service_key: str
    method: str
    entity: Dict[str, Any]
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    priority: int = 0

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
    """Intent detection and routing engine with priority system"""
    
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
        
        # Initialize semantic router
        self._init_router()
        
        logger.info("✅ SemanticRouterIntentEngine initialized")
    
    def _init_router(self):
        """Initialize semantic router with all routes"""
        try:
            encoder = HuggingFaceEncoder()
            
            routes = [
                # DN Routes
                Route(
                    name="dn_lookup",
                    utterances=[
                        "show dn", "dn dashboard", "delivery note", "track dn",
                        "dn number", "dn status", "check dn", "delivery note number",
                        "delivery note dashboard", "view dn", "get dn",
                        "delivery note status", "track delivery note",
                        "dn details", "dn information", "tell me about dn"
                    ]
                ),
                Route(
                    name="dn_status",
                    utterances=[
                        "dn status", "status of dn", "check dn status",
                        "what is the status of dn", "delivery note status",
                        "is dn delivered", "dn delivery status"
                    ]
                ),
                Route(
                    name="dn_history",
                    utterances=[
                        "dn history", "history of dn", "delivery note history",
                        "show dn history", "dn timeline"
                    ]
                ),
                Route(
                    name="dn_summary",
                    utterances=[
                        "dn summary", "summary of dns", "total dns",
                        "dn overview", "delivery note summary", "dn statistics"
                    ]
                ),
                Route(
                    name="pending_dns",
                    utterances=[
                        "pending dns", "pending deliveries", "show pending",
                        "list pending", "pending delivery notes", "open dns",
                        "undelivered dns", "outstanding deliveries"
                    ]
                ),
                Route(
                    name="pending_pgi",
                    utterances=[
                        "pending pgi", "pgi pending", "goods issue pending",
                        "pgi not done", "pending goods issue"
                    ]
                ),
                Route(
                    name="pending_pod",
                    utterances=[
                        "pending pod", "pod pending", "proof of delivery pending",
                        "pod not received", "pending proof of delivery"
                    ]
                ),
                Route(
                    name="recent_dns",
                    utterances=[
                        "recent dns", "latest dns", "newest dns",
                        "today's dns", "recent delivery notes", "this week dns"
                    ]
                ),
                Route(
                    name="delivery_timeline",
                    utterances=[
                        "delivery timeline", "dn timeline", "track delivery",
                        "delivery history", "delivery progress"
                    ]
                ),
                Route(
                    name="transit_analysis",
                    utterances=[
                        "transit analysis", "delivery transit", "shipping time",
                        "transit time", "delivery duration"
                    ]
                ),
                
                # Dealer Routes
                Route(
                    name="dealer_dashboard",
                    utterances=[
                        "show dealer", "dealer dashboard", "tell me about dealer",
                        "dealer details", "dealer profile", "dealer information",
                        "show me dealer", "dealer summary", "dealer overview",
                        "view dealer", "get dealer", "dealer performance"
                    ]
                ),
                Route(
                    name="dealer_revenue",
                    utterances=[
                        "dealer revenue", "dealer sales", "how much revenue",
                        "revenue of dealer", "dealer earnings", "sales of dealer",
                        "dealer income", "dealer revenue report"
                    ]
                ),
                Route(
                    name="dealer_pending",
                    utterances=[
                        "dealer pending", "pending dealer", "dealer overdue",
                        "dealer pending dns", "dealer deliveries pending"
                    ]
                ),
                Route(
                    name="top_dealers",
                    utterances=[
                        "top dealers", "best dealers", "leading dealers",
                        "dealer ranking", "top performing dealers", "dealer rank",
                        "highest revenue dealers", "best performing dealer"
                    ]
                ),
                Route(
                    name="dealer_comparison",
                    utterances=[
                        "compare dealers", "dealer vs dealer", "dealer comparison",
                        "compare two dealers", "dealer performance comparison"
                    ]
                ),
                
                # Warehouse Routes
                Route(
                    name="warehouse_dashboard",
                    utterances=[
                        "show warehouse", "warehouse dashboard", "warehouse details",
                        "warehouse information", "tell me about warehouse",
                        "view warehouse", "warehouse performance", "warehouse stats"
                    ]
                ),
                Route(
                    name="warehouse_revenue",
                    utterances=[
                        "warehouse revenue", "warehouse sales", "revenue of warehouse",
                        "warehouse performance", "how much warehouse sold"
                    ]
                ),
                Route(
                    name="warehouse_pending",
                    utterances=[
                        "warehouse pending", "pending warehouse", "warehouse overdue",
                        "warehouse pending dns"
                    ]
                ),
                Route(
                    name="top_warehouses",
                    utterances=[
                        "top warehouses", "best warehouses", "leading warehouses",
                        "warehouse ranking", "top performing warehouses"
                    ]
                ),
                
                # City Routes
                Route(
                    name="city_dashboard",
                    utterances=[
                        "show city", "city dashboard", "city details",
                        "city information", "tell me about city",
                        "view city", "city performance", "city stats",
                        "city overview", "city analytics"
                    ]
                ),
                Route(
                    name="city_revenue",
                    utterances=[
                        "city revenue", "city sales", "revenue of city",
                        "how much revenue in city", "city sales performance",
                        "city revenue report", "sales in city"
                    ]
                ),
                Route(
                    name="city_pending",
                    utterances=[
                        "city pending", "pending in city", "city overdue",
                        "pending dns in city", "city deliveries pending"
                    ]
                ),
                Route(
                    name="top_cities",
                    utterances=[
                        "top cities", "best cities", "leading cities",
                        "city ranking", "top performing cities",
                        "highest revenue city", "lowest revenue city"
                    ]
                ),
                Route(
                    name="city_comparison",
                    utterances=[
                        "compare cities", "city vs city", "city comparison",
                        "compare two cities", "which city is better"
                    ]
                ),
                
                # Product Routes
                Route(
                    name="product_dashboard",
                    utterances=[
                        "show product", "product dashboard", "product details",
                        "product information", "tell me about product",
                        "view product", "product performance"
                    ]
                ),
                Route(
                    name="top_products",
                    utterances=[
                        "top products", "best products", "leading products",
                        "product ranking", "top selling products",
                        "highest revenue product", "best selling product"
                    ]
                ),
                
                # KPI Routes
                Route(
                    name="national_kpi",
                    utterances=[
                        "national kpi", "kpi dashboard", "overall performance",
                        "national dashboard", "company kpi", "overall kpi",
                        "national metrics", "company performance",
                        "executive dashboard", "business overview"
                    ]
                ),
                Route(
                    name="national_revenue",
                    utterances=[
                        "total revenue", "national revenue", "overall revenue",
                        "total sales", "company revenue", "revenue total"
                    ]
                ),
                Route(
                    name="national_units",
                    utterances=[
                        "total units", "national units", "overall units",
                        "total quantity", "units sold total"
                    ]
                ),
                
                # General Routes
                Route(
                    name="greeting",
                    utterances=[
                        "hi", "hello", "hey", "good morning", "good afternoon",
                        "good evening", "salam", "namaste", "howdy",
                        "assalamualaikum", "welcome", "hey there"
                    ]
                ),
                Route(
                    name="help",
                    utterances=[
                        "help", "assist", "support", "how to", "what is",
                        "explain", "guide", "help me", "i need help",
                        "how do i", "what can you do", "commands", "instructions"
                    ]
                ),
                Route(
                    name="menu",
                    utterances=[
                        "menu", "options", "services", "what can you do",
                        "show menu", "main menu", "available options",
                        "what are my options", "show services"
                    ]
                ),
            ]
            
            self._router = Router(routes=routes, encoder=encoder)
            logger.info(f"✅ Semantic Router initialized with {len(routes)} routes")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize semantic router: {e}")
            raise
    
    # ============================================================
    # DN DETECTION (CRITICAL FIX)
    # ============================================================
    
    def _extract_dn(self, text: str) -> Optional[str]:
        """Extract DN number using multiple regex patterns"""
        if not text:
            return None
        
        # Clean the text
        text = text.strip()
        
        # Pattern 1: Exactly 10 digits
        match = re.search(r'(?<!\d)(\d{10})(?!\d)', text)
        if match:
            dn = match.group(1)
            logger.info(f"🔍 DN found (pattern 1 - 10 digits): {dn}")
            return dn
        
        # Pattern 2: 8-12 digits (flexible)
        match = re.search(r'(?<!\d)(\d{8,12})(?!\d)', text)
        if match:
            dn = match.group(1)
            logger.info(f"🔍 DN found (pattern 2 - 8-12 digits): {dn}")
            return dn
        
        # Pattern 3: With spaces (e.g., "6243 6987 49")
        match = re.search(r'(?<!\d)(\d{4}\s*\d{4}\s*\d{2,4})(?!\d)', text)
        if match:
            dn = re.sub(r'\s', '', match.group(1))
            logger.info(f"🔍 DN found (pattern 3 - with spaces): {dn}")
            return dn
        
        # Pattern 4: With dashes (e.g., "6243-6987-49")
        match = re.search(r'(?<!\d)(\d{4}-\d{4}-\d{2,4})(?!\d)', text)
        if match:
            dn = re.sub(r'-', '', match.group(1))
            logger.info(f"🔍 DN found (pattern 4 - with dashes): {dn}")
            return dn
        
        return None
    
    # ============================================================
    # ENTITY EXTRACTION
    # ============================================================
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract entities from message"""
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
            "sialkot", "gujranwala", "faisalabad", "bahawalpur", "sukkur"
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
    # INTENT TO SERVICE MAPPING
    # ============================================================
    
    def _get_service_mapping(self, intent: str) -> Tuple[str, str, bool, int]:
        """Map intent to service, method, AI requirement, and priority"""
        
        # DN Service (dn_analysis.py)
        if intent in [
            "dn_lookup", "dn_status", "dn_history", "dn_summary",
            "pending_dns", "pending_pgi", "pending_pod", "recent_dns",
            "delivery_timeline", "transit_analysis"
        ]:
            return ("dn_analysis", "get_dn_dashboard", False, 2)
        
        # Dealer Service (dealer_analytics_service.py)
        if intent in [
            "dealer_dashboard", "dealer_revenue", "dealer_pending",
            "top_dealers", "dealer_comparison"
        ]:
            return ("dealer_analytics", "get_dealer_dashboard", False, 3)
        
        # Warehouse Service (dn_analysis.py)
        if intent in [
            "warehouse_dashboard", "warehouse_revenue", "warehouse_pending",
            "top_warehouses"
        ]:
            return ("dn_analysis", "get_warehouse_dashboard", False, 3)
        
        # City Service (city_service.py)
        if intent in [
            "city_dashboard", "city_revenue", "city_pending",
            "top_cities", "city_comparison"
        ]:
            return ("city_service", "get_city_dashboard", False, 3)
        
        # Product Service (product_service.py)
        if intent in ["product_dashboard", "top_products"]:
            return ("product_service", "get_product_dashboard", False, 3)
        
        # National KPI Service (national_kpi_service.py)
        if intent in ["national_kpi", "national_revenue", "national_units"]:
            return ("national_kpi_service", "get_national_kpi_dashboard", False, 3)
        
        # General Intents (groq_service.py)
        if intent in ["greeting", "help", "menu"]:
            return ("groq_service", "process_query", True, 4)
        
        # Default fallback
        return ("groq_service", "process_query", True, 4)
    
    # ============================================================
    # MAIN DETECTION METHOD - THE CRITICAL FIX IS HERE
    # ============================================================
    
    def detect(self, message: str) -> RoutingDecision:
        """
        Detect intent and route using priority system.
        
        CRITICAL: DN numbers are detected FIRST and routed DIRECTLY to dn_analysis
        """
        message_clean = message.strip()
        
        # ============================================================
        # STAGE 1: DN NUMBER DETECTION - HIGHEST PRIORITY
        # This MUST happen before any semantic routing
        # ============================================================
        dn = self._extract_dn(message_clean)
        if dn:
            logger.info(f"🚨 DN DETECTED: {dn} - Routing DIRECTLY to dn_analysis")
            
            entities = {
                "dn": dn,
                "dn_number": dn,
                "id": dn
            }
            
            # Try to extract additional entities
            additional_entities = self._extract_entities(message_clean)
            entities.update(additional_entities)
            
            # DIRECT ROUTE - NO AI INVOLVED
            return RoutingDecision(
                intent="dn_lookup",
                confidence=1.0,
                service_key="dn_analysis",  # CRITICAL: Must match service file name
                method="get_dn_dashboard",
                entity=entities,
                requires_ai=False,  # CRITICAL: No AI
                reason=f"DN number detected: {dn} - Direct routing",
                original_message=message_clean,
                priority=1  # Highest priority
            )
        
        # ============================================================
        # STAGE 2: EXTRACT ENTITIES
        # ============================================================
        entities = self._extract_entities(message_clean)
        
        # ============================================================
        # STAGE 3: SEMANTIC ROUTER (for non-DN messages)
        # ============================================================
        try:
            result = self._router.route(message_clean)
            intent = result.name
            confidence = getattr(result, 'score', 0.85)
            
            logger.info(f"🧠 Semantic Router: intent={intent}, confidence={confidence:.2f}")
            
            # Get service mapping
            service_key, method, requires_ai, priority = self._get_service_mapping(intent)
            
            # Check confidence
            if confidence < 0.3:
                logger.info(f"⬇️ Low confidence ({confidence:.2f}) - AI fallback")
                return RoutingDecision(
                    intent="general_ai",
                    confidence=confidence,
                    service_key="groq_service",
                    method="process_query",
                    entity=entities or {"message": message_clean},
                    requires_ai=True,
                    reason=f"Low confidence ({confidence:.2f})",
                    original_message=message_clean,
                    priority=4
                )
            
            # Enrich entities based on intent
            if intent.startswith("dealer_") and "dealer" not in entities:
                dealer_match = re.search(r'([\w\s]{3,}(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation))', message_clean, re.IGNORECASE)
                if dealer_match:
                    entities["dealer_name"] = dealer_match.group(1).strip()
                    entities["dealer"] = dealer_match.group(1).strip()
            
            if intent.startswith("city_") and "city" not in entities:
                city_names = ["lahore", "karachi", "rawalpindi", "islamabad", "multan", "peshawar", "quetta", "abbottabad", "hyderabad", "sialkot", "gujranwala", "faisalabad"]
                for city in city_names:
                    if city in message_clean.lower():
                        entities["city"] = city.title()
                        entities["city_name"] = city.title()
                        break
            
            logger.info(f"✅ Routing: {intent} -> {service_key}.{method}")
            
            return RoutingDecision(
                intent=intent,
                confidence=confidence,
                service_key=service_key,
                method=method,
                entity=entities,
                requires_ai=requires_ai,
                reason=f"Semantic route: {intent}",
                original_message=message_clean,
                priority=priority
            )
            
        except Exception as e:
            logger.error(f"❌ Semantic router error: {e}")
        
        # ============================================================
        # STAGE 4: FALLBACK
        # ============================================================
        logger.info("🔄 Fallback - AI")
        return RoutingDecision(
            intent="general_ai",
            confidence=0.3,
            service_key="groq_service",
            method="process_query",
            entity=entities or {"message": message_clean},
            requires_ai=True,
            reason="Fallback",
            original_message=message_clean,
            priority=4
        )


# ============================================================
# INTENT ROUTING SERVICE - FIXED VERSION
# ============================================================

class IntentRoutingService:
    """Enterprise Intent Routing Service"""
    
    def __init__(self):
        self._engine = SemanticRouterIntentEngine()
        self._cache: Dict[str, RoutingDecision] = {}
        self._cache_ttl = 300
    
    def detect_intent(self, message: str) -> Dict[str, Any]:
        """
        Detect intent and return routing decision.
        
        CRITICAL: Returns service_key that maps to service files:
        - "dn_analysis" -> dn_analysis.py
        - "dealer_analytics" -> dealer_analytics_service.py  
        - "city_service" -> city_service.py
        - "product_service" -> product_service.py
        - "national_kpi_service" -> national_kpi_service.py
        - "groq_service" -> groq_service.py
        """
        # Check cache
        cache_key = message.strip().lower()
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            return cached.to_dict()
        
        # Detect intent
        decision = self._engine.detect(message)
        
        # Log the routing decision clearly
        logger.info(f"📋 ROUTING DECISION: {decision.service_key}.{decision.method} (AI: {decision.requires_ai})")
        
        # Cache result
        self._cache[cache_key] = decision
        if len(self._cache) > 1000:
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
    
    def health_check(self) -> Dict[str, Any]:
        """Health check"""
        return {
            "service": "intent_routing_service",
            "version": "3.0",
            "available": SEMANTIC_ROUTER_AVAILABLE,
            "cache_size": len(self._cache),
            "status": "healthy"
        }


# ============================================================
# SINGLETON INSTANCE
# ============================================================

_intent_service: Optional[IntentRoutingService] = None
_service_lock = threading.Lock()


def get_intent_routing_service() -> IntentRoutingService:
    """Get singleton instance"""
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
    """Detect intent - MAIN ENTRY POINT"""
    service = get_intent_routing_service()
    return service.detect_intent(message)


def health_check() -> Dict[str, Any]:
    """Health check"""
    service = get_intent_routing_service()
    return service.health_check()


__all__ = [
    "IntentRoutingService",
    "SemanticRouterIntentEngine",
    "RoutingDecision",
    "get_intent_routing_service",
    "detect_intent",
    "health_check",
]
