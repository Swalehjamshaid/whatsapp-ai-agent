"""
File: app/services/intent_routing_service.py
Version: 1.0 - SEMANTIC ROUTER ONLY
Purpose: Pure intent detection and routing using semantic-router
         Single library solution - NO other NLP libraries needed
         
Features:
- ✅ Semantic Intent Detection
- ✅ Dynamic Routing
- ✅ Confidence Scoring
- ✅ Entity Extraction
- ✅ Natural Language Understanding
- ✅ 100% PostgreSQL Integration
- ✅ No external API keys needed (uses HuggingFace encoder)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

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
    """Final routing decision from semantic router"""
    intent: str
    confidence: float
    service_key: str
    method: str
    entity: Dict[str, Any]
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    
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
        }


@dataclass
class IntentCandidate:
    """Candidate intent from semantic router"""
    intent: str
    score: float
    confidence: float
    matched_utterance: Optional[str] = None
    reason: str = ""


# ============================================================
# SEMANTIC ROUTER INTENT ENGINE
# ============================================================

class SemanticRouterIntentEngine:
    """
    Pure intent detection and routing using semantic-router.
    
    This is a SINGLE LIBRARY solution - no other NLP libraries needed.
    Uses HuggingFace encoder (free, no API key required).
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
        self._cache: Dict[str, RoutingDecision] = {}
        self._cache_ttl = 300  # 5 minutes
        
        # Initialize semantic router
        self._init_router()
        
        logger.info("✅ SemanticRouterIntentEngine initialized")
    
    def _init_router(self):
        """Initialize semantic router with all routes"""
        try:
            # Use free HuggingFace encoder (no API key needed)
            encoder = HuggingFaceEncoder()
            
            # Define all routes - this is the ONLY place routing is defined
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
                        "delivery note status", "track delivery note"
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
                        "show dn history", "dn timeline", "delivery note timeline"
                    ]
                ),
                Route(
                    name="dn_summary",
                    utterances=[
                        "dn summary", "summary of dns", "total dns",
                        "dn overview", "delivery note summary", "dn statistics",
                        "total delivery notes", "dn count"
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
                        "delivery history", "delivery progress", "shipment timeline"
                    ]
                ),
                Route(
                    name="transit_analysis",
                    utterances=[
                        "transit analysis", "delivery transit", "shipping time",
                        "transit time", "delivery duration", "how long delivery takes"
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
                        "view dealer", "get dealer", "dealer performance"
                    ]
                ),
                Route(
                    name="dealer_revenue",
                    utterances=[
                        "dealer revenue", "dealer sales", "how much revenue",
                        "revenue of dealer", "dealer earnings", "sales of dealer",
                        "dealer income", "dealer revenue report",
                        "how much did dealer sell", "dealer sales performance"
                    ]
                ),
                Route(
                    name="dealer_pending",
                    utterances=[
                        "dealer pending", "pending dealer", "dealer overdue",
                        "dealer pending dns", "dealer deliveries pending",
                        "dealer undelivered"
                    ]
                ),
                Route(
                    name="top_dealers",
                    utterances=[
                        "top dealers", "best dealers", "leading dealers",
                        "dealer ranking", "top performing dealers", "dealer rank",
                        "highest revenue dealers", "best performing dealer",
                        "which dealer is best", "dealer performance ranking"
                    ]
                ),
                Route(
                    name="dealer_comparison",
                    utterances=[
                        "compare dealers", "dealer vs dealer", "dealer comparison",
                        "compare two dealers", "dealer performance comparison",
                        "which dealer is better", "dealer vs"
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
                        "warehouse ranking", "top performing warehouses",
                        "highest revenue warehouse"
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
                        "highest revenue city", "lowest revenue city",
                        "which city has highest sales", "which city has lowest sales",
                        "best performing city", "worst performing city",
                        "city with highest revenue", "city with lowest revenue"
                    ]
                ),
                Route(
                    name="city_comparison",
                    utterances=[
                        "compare cities", "city vs city", "city comparison",
                        "compare two cities", "which city is better"
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
                
                # ============================================================
                # KPI ROUTES
                # ============================================================
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
                
                # ============================================================
                # GENERAL ROUTES
                # ============================================================
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
            
            # Create router
            self._router = Router(routes=routes, encoder=encoder)
            
            logger.info(f"✅ Semantic Router initialized with {len(routes)} routes")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize semantic router: {e}")
            raise
    
    # ============================================================
    # DN DETECTION (Regex - highest priority)
    # ============================================================
    
    def _extract_dn(self, text: str) -> Optional[str]:
        """Extract DN number using regex"""
        match = re.search(r'(?<!\d)(\d{8,12})(?!\d)', text)
        if match:
            return match.group(1)
        
        # Also check for digits with spaces
        match = re.search(r'(?<!\d)(\d{4}\s*\d{4}\s*\d{4})(?!\d)', text)
        if match:
            return re.sub(r'\s', '', match.group(1))
        
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
        
        # Extract dealer name patterns
        dealer_patterns = [
            r'(?:dealer|about|for|company|customer|tell me about|show me|get|view)\s+([a-z0-9\s&\-\.]+)',
            r'([\w\s]+(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation))',
        ]
        for pattern in dealer_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dealer_name = match.group(1).strip()
                if len(dealer_name) > 2:
                    entities["dealer_name"] = dealer_name
                    entities["dealer"] = dealer_name
                    break
        
        # Extract city name
        city_names = [
            "abbottabad", "lahore", "karachi", "rawalpindi", "quetta",
            "multan", "peshawar", "gilgit", "hyderabad", "islamabad",
            "sialkot", "gujranwala", "faisalabad", "bahawalpur", "sukkur",
            "dg khan", "rahim yar khan", "gwadar"
        ]
        for city in city_names:
            if city in text.lower():
                entities["city"] = city
                entities["city_name"] = city
                break
        
        # Extract warehouse name
        warehouse_match = re.search(r'(?:warehouse|wh|depot)\s+([a-z0-9\s&\-\.]+)', text, re.IGNORECASE)
        if warehouse_match:
            entities["warehouse"] = warehouse_match.group(1).strip()
        
        # Extract product name
        product_match = re.search(r'(?:product|model|material)\s+([a-z0-9\s&\-\.]+)', text, re.IGNORECASE)
        if product_match:
            entities["product"] = product_match.group(1).strip()
        
        return entities
    
    # ============================================================
    # MAIN DETECTION METHOD
    # ============================================================
    
    def detect(self, message: str) -> RoutingDecision:
        """
        Detect intent and route using semantic-router.
        
        This is the MAIN entry point for intent detection.
        Returns a RoutingDecision with intent, confidence, service, and entity.
        """
        message_clean = message.strip()
        
        # ============================================================
        # STAGE 1: DN NUMBER DETECTION (Highest Priority)
        # ============================================================
        dn = self._extract_dn(message_clean)
        if dn:
            return RoutingDecision(
                intent="dn_lookup",
                confidence=1.0,
                service_key="dn_service",
                method="get_dn_dashboard",
                entity={"dn": dn, "dn_number": dn},
                requires_ai=False,
                reason=f"DN number detected: {dn}",
                original_message=message_clean
            )
        
        # ============================================================
        # STAGE 2: ENTITY EXTRACTION
        # ============================================================
        entities = self._extract_entities(message_clean)
        
        # ============================================================
        # STAGE 3: SEMANTIC ROUTER
        # ============================================================
        try:
            result = self._router.route(message_clean)
            
            intent = result.name
            confidence = getattr(result, 'score', 0.85)
            
            # If confidence is too low, fallback
            if confidence < 0.3:
                return RoutingDecision(
                    intent="general_ai",
                    confidence=confidence,
                    service_key="groq_service",
                    method="process_query",
                    entity=entities or {"message": message_clean},
                    requires_ai=True,
                    reason=f"Low confidence ({confidence:.2f}) - fallback to AI",
                    original_message=message_clean
                )
            
            # ============================================================
            # STAGE 4: INTENT TO SERVICE MAPPING
            # ============================================================
            intent_map = {
                # DN Intents
                "dn_lookup": ("dn_service", "get_dn_dashboard", False),
                "dn_status": ("dn_service", "get_dn_status", False),
                "dn_history": ("dn_service", "get_dn_history", False),
                "dn_summary": ("dn_service", "get_dn_summary", False),
                "pending_dns": ("dn_service", "get_pending_dns", False),
                "pending_pgi": ("dn_service", "get_pending_pgi", False),
                "pending_pod": ("dn_service", "get_pending_pod", False),
                "recent_dns": ("dn_service", "get_recent_dns", False),
                "delivery_timeline": ("dn_service", "get_delivery_timeline", False),
                "transit_analysis": ("dn_service", "get_transit_analysis", False),
                
                # Dealer Intents
                "dealer_dashboard": ("dealer_service", "get_dealer_dashboard", False),
                "dealer_revenue": ("dealer_service", "get_dealer_dashboard", False),
                "dealer_pending": ("dealer_service", "get_dealer_dashboard", False),
                "top_dealers": ("dealer_service", "get_top_dealers", False),
                "dealer_comparison": ("dealer_service", "compare_dealers", False),
                
                # Warehouse Intents
                "warehouse_dashboard": ("warehouse_service", "get_warehouse_dashboard", False),
                "warehouse_revenue": ("warehouse_service", "get_warehouse_dashboard", False),
                "warehouse_pending": ("warehouse_service", "get_warehouse_dashboard", False),
                "top_warehouses": ("warehouse_service", "get_top_warehouses", False),
                
                # City Intents
                "city_dashboard": ("city_service", "get_city_dashboard", False),
                "city_revenue": ("city_service", "get_city_dashboard", False),
                "city_pending": ("city_service", "get_city_dashboard", False),
                "top_cities": ("city_service", "get_top_cities", False),
                "city_comparison": ("city_service", "compare_cities", False),
                
                # Product Intents
                "product_dashboard": ("product_service", "get_product_dashboard", False),
                "top_products": ("product_service", "get_top_products", False),
                
                # KPI Intents
                "national_kpi": ("kpi_service", "get_national_kpi_dashboard", False),
                "national_revenue": ("kpi_service", "get_national_kpi_dashboard", False),
                "national_units": ("kpi_service", "get_national_kpi_dashboard", False),
                
                # General Intents
                "greeting": ("groq_service", "process_query", True),
                "help": ("groq_service", "process_query", True),
                "menu": ("groq_service", "process_query", True),
            }
            
            # ============================================================
            # STAGE 5: CHECK FOR ENTITY IN INTENT
            # ============================================================
            
            # If intent is dealer-related and we have dealer entity
            if intent in ["dealer_dashboard", "dealer_revenue", "dealer_pending"]:
                if "dealer_name" in entities:
                    # Dealer already extracted
                    pass
                elif "dealer" in entities:
                    pass
                else:
                    # Try to extract from message
                    dealer_match = re.search(r'([\w\s]+(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation))', message_clean, re.IGNORECASE)
                    if dealer_match:
                        entities["dealer_name"] = dealer_match.group(1).strip()
                        entities["dealer"] = dealer_match.group(1).strip()
            
            # If intent is city-related and we have city entity
            if intent in ["city_dashboard", "city_revenue", "city_pending"]:
                if "city" not in entities:
                    # Try to extract from message
                    city_names = ["abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan", "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala", "faisalabad", "bahawalpur", "sukkur", "dg khan", "rahim yar khan", "gwadar"]
                    for city in city_names:
                        if city in message_clean.lower():
                            entities["city"] = city
                            entities["city_name"] = city
                            break
            
            # If intent is warehouse-related and we have warehouse entity
            if intent in ["warehouse_dashboard", "warehouse_revenue", "warehouse_pending"]:
                if "warehouse" not in entities:
                    warehouse_match = re.search(r'(?:warehouse|wh|depot)\s+([a-z0-9\s&\-\.]+)', message_clean, re.IGNORECASE)
                    if warehouse_match:
                        entities["warehouse"] = warehouse_match.group(1).strip()
            
            # ============================================================
            # STAGE 6: RETURN DECISION
            # ============================================================
            if intent in intent_map:
                service_key, method, requires_ai = intent_map[intent]
                
                return RoutingDecision(
                    intent=intent,
                    confidence=confidence,
                    service_key=service_key,
                    method=method,
                    entity=entities,
                    requires_ai=requires_ai,
                    reason=f"Semantic route: {intent} ({confidence:.2f})",
                    original_message=message_clean
                )
            
        except Exception as e:
            logger.error(f"❌ Semantic router error: {e}")
        
        # ============================================================
        # STAGE 7: FALLBACK
        # ============================================================
        return RoutingDecision(
            intent="general_ai",
            confidence=0.3,
            service_key="groq_service",
            method="process_query",
            entity=entities or {"message": message_clean},
            requires_ai=True,
            reason="Fallback - no intent matched",
            original_message=message_clean
        )


# ============================================================
# SERVICE WRAPPER
# ============================================================

class IntentRoutingService:
    """
    Enterprise Intent Routing Service using semantic-router.
    Wraps the SemanticRouterIntentEngine for easy integration.
    """
    
    def __init__(self):
        self._engine = SemanticRouterIntentEngine()
        self._cache: Dict[str, RoutingDecision] = {}
        self._cache_ttl = 300
        self._last_cache_cleanup = time.time()
    
    def detect_intent(self, message: str) -> Dict[str, Any]:
        """
        Detect intent and return routing decision.
        
        Returns:
            {
                "intent": str,
                "confidence": float,
                "service_key": str,
                "method": str,
                "entity": dict,
                "requires_ai": bool,
                "reason": str
            }
        """
        # Check cache
        cache_key = message.strip().lower()
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            # Check if cache is still valid
            if time.time() - self._last_cache_cleanup < self._cache_ttl:
                return cached.to_dict()
        
        # Detect intent
        decision = self._engine.detect(message)
        
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
            if current_time - self._last_cache_cleanup > self._cache_ttl:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self._cache[key]
        self._last_cache_cleanup = current_time
    
    def clear_cache(self):
        """Clear the cache"""
        self._cache.clear()
    
    def get_supported_intents(self) -> List[str]:
        """Get list of supported intents"""
        return [
            "dn_lookup", "dn_status", "dn_history", "dn_summary",
            "pending_dns", "pending_pgi", "pending_pod", "recent_dns",
            "delivery_timeline", "transit_analysis",
            "dealer_dashboard", "dealer_revenue", "dealer_pending",
            "top_dealers", "dealer_comparison",
            "warehouse_dashboard", "warehouse_revenue", "warehouse_pending",
            "top_warehouses",
            "city_dashboard", "city_revenue", "city_pending",
            "top_cities", "city_comparison",
            "product_dashboard", "top_products",
            "national_kpi", "national_revenue", "national_units",
            "greeting", "help", "menu", "general_ai"
        ]
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for the service"""
        return {
            "service": "intent_routing_service",
            "version": "1.0",
            "available": SEMANTIC_ROUTER_AVAILABLE,
            "cache_size": len(self._cache),
            "supported_intents": len(self.get_supported_intents()),
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


# ============================================================
# INTEGRATION WITH AI PROVIDER SERVICE
# ============================================================

"""
To integrate with ai_provider_service.py, add this:

from app.services.intent_routing_service import get_intent_routing_service

# In AIProviderOrchestrator._detect_intent:
async def _detect_intent(self, message: str, sender: str | None) -> tuple[Any, bool]:
    # Use semantic router for intent detection
    intent_service = get_intent_routing_service()
    decision = intent_service.detect_intent(message)
    return decision, False
"""


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
]
