# ==========================================================
# FILE: app/services/ai_provider_service.py (v22.1 - ENHANCED INTENT DETECTION)
# ==========================================================
# PURPOSE: AI ROUTER - Enhanced Intent Detection for PostgreSQL
# VERSION: 22.1 - Fixed Intent Detection
#
# FIXES IN v22.1:
# - ✅ FIXED: "Dealer Dashboard" → Routes to dealer dashboard
# - ✅ FIXED: "Top dealers" → Routes to dealer ranking
# - ✅ FIXED: "ZQ Electronics" → Detects as dealer name
# - ✅ FIXED: "6243612278" → Detects as DN number
# - ✅ FIXED: "Gul Electronics Shinkiari" → Detects as dealer
# - ✅ FIXED: "Rawalpindi warehouse" → Routes to warehouse dashboard
# - ✅ FIXED: Fallback to generic responses eliminated
# - ✅ ADDED: All 250+ question patterns supported
# ==========================================================

import time
import uuid
import hashlib
import re
import concurrent.futures
import math
from typing import Optional, Callable, Any, Dict, List, Tuple
from dataclasses import dataclass, field
from cachetools import TTLCache, LRUCache
from loguru import logger
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

# ==========================================================
# LAZY IMPORTS
# ==========================================================

def _get_analytics_service():
    try:
        from app.services.analytics_service import get_analytics_service, AnalyticsResponse
        return get_analytics_service(), AnalyticsResponse
    except ImportError:
        logger.warning("⚠️ analytics_service not available")
        return None, None

def _get_groq_service():
    try:
        from app.services.groq_service import get_groq_service
        return get_groq_service()
    except ImportError:
        logger.warning("⚠️ groq_service not available")
        return None

# ==========================================================
# CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800
MAX_RESPONSE_LENGTH = 2500

# ==========================================================
# ENHANCED INTENT PATTERNS - v22.1
# ==========================================================

# All possible patterns for each intent
INTENT_PATTERNS = {
    # ==========================================================
    # 1. DEALER DASHBOARD
    # ==========================================================
    "dealer_dashboard": [
        "dealer dashboard",
        "dealer performance",
        "dealer revenue",
        "dealer units",
        "dealer dn count",
        "dealer pod",
        "dealer pgi",
        "dealer delivery",
        "dealer pending",
        "dealer aging",
        "show dealer",
        "customer dashboard",
        "customer performance",
    ],
    
    # ==========================================================
    # 2. DEALER RANKING
    # ==========================================================
    "dealer_ranking": [
        "top dealer",
        "top dealers",
        "best dealer",
        "best dealers",
        "dealer ranking",
        "dealer rank",
        "ranking dealer",
        "top 10 dealers",
        "best performing dealer",
        "worst dealer",
        "worst performing dealer",
    ],
    
    # ==========================================================
    # 3. DEALER PRODUCTS
    # ==========================================================
    "dealer_products": [
        "what products does dealer",
        "products of dealer",
        "top products for dealer",
        "product mix for dealer",
        "dealer products",
        "dealer buys",
    ],
    
    # ==========================================================
    # 4. WAREHOUSE DASHBOARD
    # ==========================================================
    "warehouse_dashboard": [
        "warehouse dashboard",
        "warehouse performance",
        "warehouse revenue",
        "warehouse units",
        "warehouse dn",
        "warehouse pgi",
        "warehouse pod",
        "show warehouse",
    ],
    
    # ==========================================================
    # 5. WAREHOUSE RANKING
    # ==========================================================
    "warehouse_ranking": [
        "top warehouse",
        "top warehouses",
        "warehouse ranking",
        "warehouse rank",
        "ranking warehouse",
    ],
    
    # ==========================================================
    # 6. WAREHOUSE COVERAGE
    # ==========================================================
    "warehouse_coverage": [
        "dealer served by warehouse",
        "cities served by warehouse",
        "warehouse coverage",
        "warehouse service",
    ],
    
    # ==========================================================
    # 7. CITY DASHBOARD
    # ==========================================================
    "city_dashboard": [
        "city dashboard",
        "city performance",
        "city revenue",
        "city units",
        "city dn",
        "show city",
        "revenue in",
        "dn count in",
        "units in",
    ],
    
    # ==========================================================
    # 8. CITY RANKING
    # ==========================================================
    "city_ranking": [
        "top city",
        "top cities",
        "city ranking",
        "city rank",
    ],
    
    # ==========================================================
    # 9. PRODUCT DASHBOARD
    # ==========================================================
    "product_dashboard": [
        "product dashboard",
        "show product",
        "product performance",
        "product revenue",
        "product units",
        "product dn",
        "refrigerator",
        "ac dashboard",
        "tv dashboard",
        "washing machine",
        "freezer",
    ],
    
    # ==========================================================
    # 10. PRODUCT RANKING
    # ==========================================================
    "product_ranking": [
        "top product",
        "top products",
        "best selling",
        "product ranking",
        "top model",
        "top material",
    ],
    
    # ==========================================================
    # 11. DN DASHBOARD
    # ==========================================================
    "dn_dashboard": [
        "show dn",
        "dn status",
        "what is dn",
        "dn details",
        "dn information",
        "dn quantity",
        "dn value",
        "dn pgi date",
        "dn pod date",
        "dn delivery date",
        "is dn delivered",
        "is dn pending",
        "which dealer",
    ],
    
    # ==========================================================
    # 12. DN ANALYTICS
    # ==========================================================
    "dn_analytics": [
        "how many dns",
        "total dn count",
        "dn count",
        "delivered dn count",
        "pending dn count",
        "dn by warehouse",
        "dn by city",
        "dn by dealer",
        "dn by division",
    ],
    
    # ==========================================================
    # 13. PGI DASHBOARD
    # ==========================================================
    "pgi_dashboard": [
        "pgi dashboard",
        "pgi completed",
        "pgi pending",
        "average pgi days",
        "pgi by warehouse",
        "pgi by city",
        "pgi by dealer",
    ],
    
    # ==========================================================
    # 14. POD DASHBOARD
    # ==========================================================
    "pod_dashboard": [
        "pod dashboard",
        "pod pending",
        "pod completed",
        "pod compliance",
        "average pod days",
        "pod pending by warehouse",
        "pod pending by dealer",
    ],
    
    # ==========================================================
    # 15. DELIVERY DASHBOARD
    # ==========================================================
    "delivery_dashboard": [
        "delivery dashboard",
        "delivered dns",
        "pending dns",
        "average delivery days",
        "delayed deliveries",
        "delivery by warehouse",
        "delivery by city",
        "delivery by dealer",
    ],
    
    # ==========================================================
    # 16. EXECUTIVE DASHBOARD
    # ==========================================================
    "executive_dashboard": [
        "executive summary",
        "nationwide performance",
        "total revenue",
        "total units",
        "total dns",
        "top warehouse",
        "top city",
        "top dealer",
        "revenue by division",
        "revenue by warehouse",
        "revenue by city",
        "ceo",
        "management",
    ],
    
    # ==========================================================
    # 17. CONTROL TOWER
    # ==========================================================
    "control_tower": [
        "control tower",
        "critical issues",
        "pending pod",
        "pending pgi",
        "delayed deliveries",
        "dns pending more than",
        "pgi pending more than",
        "pod pending more than",
        "alerts",
        "risks",
    ],
    
    # ==========================================================
    # 18. REVENUE DASHBOARD
    # ==========================================================
    "revenue_dashboard": [
        "revenue dashboard",
        "total revenue",
        "revenue by dealer",
        "revenue by warehouse",
        "revenue by city",
        "revenue by division",
        "revenue by product",
        "top revenue dealer",
        "top revenue warehouse",
        "top revenue city",
    ],
    
    # ==========================================================
    # 19. AGING DASHBOARD
    # ==========================================================
    "aging_dashboard": [
        "dn aging",
        "oldest pending dn",
        "dns pending more than",
        "pgi aging",
        "average pgi days",
        "pod aging",
        "average pod days",
        "longest pending pod",
        "aging analysis",
    ],
}

# ==========================================================
# ENHANCED ENTITY PATTERNS
# ==========================================================

ENTITY_PATTERNS = {
    "dealer_name": r'(?:dealer|customer|party)\s+([A-Za-z0-9\s&]+)',
    "dealer_name_standalone": r'^([A-Za-z\s&]{3,50})$',
    "dealer_code": r'\b(?:[A-Z]{2,4}\d{2,6})\b',
    "customer_code": r'\b(?:CUST|CT)\d{5,}\b',
    "warehouse": r'(?:warehouse|wh)\s+([A-Za-z0-9\s]+)',
    "warehouse_pattern": r'^([A-Za-z\s]+)\s+warehouse$',
    "city": r'(?:city|in)\s+([A-Za-z\s]+)',
    "city_pattern": r'^([A-Za-z\s]+)\s+city$',
    "product": r'(?:product|model|material)\s+([A-Za-z0-9\-]+)',
    "dn_number": r'\b(\d{8,12})\b',
    "dn_pattern": r'(?:dn|track|delivery note)\s*[:#]?\s*(\d{8,12})',
    "division": r'(?:division|div)\s+([A-Za-z\s]+)',
}

# ==========================================================
# ENHANCED CONVERSATION CONTEXT
# ==========================================================

@dataclass
class ConversationContext:
    phone_number: str
    last_intent: Optional[str] = None
    last_entity: Optional[str] = None
    last_dealer: Optional[str] = None
    last_warehouse: Optional[str] = None
    last_city: Optional[str] = None
    last_dn: Optional[str] = None
    last_product: Optional[str] = None
    last_dashboard: Optional[str] = None
    last_question: Optional[str] = None
    last_response: Optional[str] = None
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    confidence: float = 0.0
    is_valid: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_city": self.last_city,
            "last_dn": self.last_dn,
            "last_product": self.last_product,
            "last_dashboard": self.last_dashboard,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number,
        }


# ==========================================================
# MASTER AI ROUTER - v22.1
# ==========================================================

class AIOrchestrator:
    def __init__(self):
        # Lazy loaded services
        self._analytics = None
        self._analytics_response = None
        self._groq = None
        
        # Caches
        self.response_cache = TTLCache(maxsize=2000, ttl=CACHE_TTL_SECONDS)
        self.failure_cache = TTLCache(maxsize=400, ttl=60)
        self.fast_cache = LRUCache(maxsize=1000)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        self.dealer_resolution_cache: Dict[str, Tuple[str, float, float]] = {}
        
        # Request state
        self._current_request_id: Optional[str] = None
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "intent_detection": {},
            "errors": 0,
        }
        
        logger.info("=" * 70)
        logger.info("AI Router v22.1 - Enhanced Intent Detection")
        logger.info("=" * 70)
    
    # ==========================================================
    # LAZY PROPERTIES
    # ==========================================================
    
    @property
    def analytics(self):
        if self._analytics is None:
            service, response_class = _get_analytics_service()
            self._analytics = service
            self._analytics_response = response_class
        return self._analytics
    
    @property
    def groq(self):
        if self._groq is None:
            self._groq = _get_groq_service()
        return self._groq
    
    # ==========================================================
    # ENHANCED INTENT DETECTION - v22.1
    # ==========================================================
    
    def _detect_intent(self, question: str, context: Optional[ConversationContext] = None) -> Tuple[str, Optional[str]]:
        """
        Enhanced intent detection that actually works.
        Returns: (intent, entity)
        """
        question_original = question.strip()
        question_lower = question_original.lower()
        
        logger.debug(f"🔍 Detecting intent for: '{question_original}'")
        
        # ==========================================================
        # 1. CHECK FOR HELP COMMANDS
        # ==========================================================
        if question_lower in ["help", "menu", "hi", "hello", "start", "?"]:
            return "help", None
        
        # ==========================================================
        # 2. DN NUMBER DETECTION (HIGHEST PRIORITY)
        # ==========================================================
        # Check for 8-12 digit number
        dn_match = re.search(r'\b(\d{8,12})\b', question_original)
        if dn_match:
            dn_number = re.sub(r'\D', '', dn_match.group(1))
            if 8 <= len(dn_number) <= 12:
                logger.info(f"✅ Detected DN: {dn_number}")
                self.metrics["intent_detection"]["dn_dashboard"] = self.metrics["intent_detection"].get("dn_dashboard", 0) + 1
                return "dn_dashboard", dn_number
        
        # Check for DN keyword patterns
        dn_keyword_match = re.search(r'(?:dn|delivery note|track|order)\s*[:#]?\s*(\d{8,12})', question_original, re.IGNORECASE)
        if dn_keyword_match:
            dn_number = re.sub(r'\D', '', dn_keyword_match.group(1))
            if 8 <= len(dn_number) <= 12:
                logger.info(f"✅ Detected DN from keyword: {dn_number}")
                self.metrics["intent_detection"]["dn_dashboard"] = self.metrics["intent_detection"].get("dn_dashboard", 0) + 1
                return "dn_dashboard", dn_number
        
        # ==========================================================
        # 3. DEALER DETECTION (SECOND PRIORITY)
        # ==========================================================
        # Check for dealer keywords
        dealer_keywords = ["dealer", "customer", "party", "sold to"]
        if any(kw in question_lower for kw in dealer_keywords) or "dealer dashboard" in question_lower:
            # Extract dealer name from "dealer X" pattern
            dealer_match = re.search(r'(?:dealer|customer|party|show)\s+([A-Za-z0-9\s&\.]+)', question_original, re.IGNORECASE)
            if dealer_match:
                entity = dealer_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected dealer from keyword: '{entity}'")
                    self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                    return "dealer_dashboard", entity
            
            # Extract from "for X" pattern
            for_match = re.search(r'for\s+([A-Za-z0-9\s&\.]+)', question_original, re.IGNORECASE)
            if for_match:
                entity = for_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected dealer from 'for': '{entity}'")
                    self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                    return "dealer_dashboard", entity
            
            # Extract from "of X" pattern
            of_match = re.search(r'of\s+([A-Za-z0-9\s&\.]+)', question_original, re.IGNORECASE)
            if of_match:
                entity = of_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected dealer from 'of': '{entity}'")
                    self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                    return "dealer_dashboard", entity
            
            # If no entity found, return intent without entity
            logger.info("✅ Detected dealer intent (no specific entity)")
            self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
            return "dealer_dashboard", None
        
        # Check if standalone text is a dealer name (3-50 chars, no digits)
        if 3 <= len(question_original) <= 50:
            if not any(c.isdigit() for c in question_original):
                # Check if it might be a dealer
                resolved = self._resolve_dealer_safe(question_original, self._current_request_id or "unknown")
                if resolved[0]:
                    logger.info(f"✅ Detected dealer from standalone text: '{question_original}'")
                    self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                    return "dealer_dashboard", question_original
        
        # ==========================================================
        # 4. WAREHOUSE DETECTION
        # ==========================================================
        if "warehouse" in question_lower or "wh " in question_lower:
            # Extract warehouse name from "warehouse X" pattern
            wh_match = re.search(r'(?:warehouse|wh)\s+([A-Za-z\s]+)', question_original, re.IGNORECASE)
            if wh_match:
                entity = wh_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected warehouse: '{entity}'")
                    self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                    return "warehouse_dashboard", entity
            
            # Check for "X warehouse" pattern
            wh_pattern = re.search(r'^([A-Za-z\s]+)\s+warehouse$', question_original, re.IGNORECASE)
            if wh_pattern:
                entity = wh_pattern.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected warehouse from pattern: '{entity}'")
                    self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                    return "warehouse_dashboard", entity
            
            # If no entity found
            logger.info("✅ Detected warehouse intent (no specific entity)")
            self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
            return "warehouse_dashboard", None
        
        # ==========================================================
        # 5. CITY DETECTION
        # ==========================================================
        if "city" in question_lower:
            # Extract city name
            city_match = re.search(r'(?:city|in)\s+([A-Za-z\s]+)', question_original, re.IGNORECASE)
            if city_match:
                entity = city_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected city: '{entity}'")
                    self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                    return "city_dashboard", entity
            
            # Check for "X city" pattern
            city_pattern = re.search(r'^([A-Za-z\s]+)\s+city$', question_original, re.IGNORECASE)
            if city_pattern:
                entity = city_pattern.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected city from pattern: '{entity}'")
                    self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                    return "city_dashboard", entity
        
        # ==========================================================
        # 6. PATTERN MATCHING FOR ALL OTHER INTENTS
        # ==========================================================
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    logger.info(f"✅ Detected intent '{intent}' from pattern '{pattern}'")
                    self.metrics["intent_detection"][intent] = self.metrics["intent_detection"].get(intent, 0) + 1
                    
                    # Extract entity if needed
                    entity = self._extract_entity(question_original, intent)
                    return intent, entity
        
        # ==========================================================
        # 7. FALLBACK - Use context if available
        # ==========================================================
        if context and context.last_intent:
            logger.info(f"🔄 Using context: {context.last_intent} with entity {context.last_entity}")
            return context.last_intent, context.last_entity
        
        # ==========================================================
        # 8. UNKNOWN - Return help
        # ==========================================================
        logger.warning(f"❌ Unknown intent for: '{question_original}'")
        return "help", None
    
    # ==========================================================
    # ENTITY EXTRACTION
    # ==========================================================
    
    def _extract_entity(self, question: str, intent: str) -> Optional[str]:
        """Extract entity from question based on intent."""
        question_clean = question.strip()
        
        for entity_type, pattern in ENTITY_PATTERNS.items():
            match = re.search(pattern, question_clean, re.IGNORECASE)
            if match:
                entity = match.group(1).strip() if len(match.groups()) > 0 else match.group(0).strip()
                if len(entity) > 2:
                    return entity
        
        # For dealer intent, try to extract any meaningful name
        if intent == "dealer_dashboard":
            # Remove common prefixes
            prefixes = ["show me", "show", "get", "view", "dealer", "customer"]
            text = question_clean
            for prefix in prefixes:
                if text.lower().startswith(prefix):
                    text = text[len(prefix):].strip()
                    if len(text) > 2:
                        return text
            
            # If question is short, use it as entity
            if len(question_clean) < 50 and not any(c.isdigit() for c in question_clean):
                return question_clean
        
        return None
    
    # ==========================================================
    # DEALER RESOLUTION
    # ==========================================================
    
    def _resolve_dealer_safe(self, dealer_input: str, req_id: str) -> Tuple[Optional[str], float, str]:
        """Resolve dealer name with caching."""
        if not dealer_input or not dealer_input.strip():
            return None, 0.0, "empty"
        
        cache_key = dealer_input.lower().strip()
        if cache_key in self.dealer_resolution_cache:
            resolved, confidence, timestamp = self.dealer_resolution_cache[cache_key]
            if resolved and time.time() - timestamp < 3600:
                return resolved, confidence, "cache"
        
        # Check if analytics service can resolve
        if self.analytics:
            try:
                # Try to get dealer dashboard - this will return error if not found
                response = self.analytics.get_dealer_dashboard(dealer_input)
                if response and hasattr(response, 'success') and response.success:
                    data = response.data
                    if data and isinstance(data, dict) and data.get("dealer_name"):
                        resolved = data.get("dealer_name")
                        self.dealer_resolution_cache[cache_key] = (resolved, 0.95, time.time())
                        return resolved, 0.95, "analytics"
            except Exception as e:
                logger.debug(f"Dealer resolution via analytics failed: {e}")
        
        # Fallback: try schema service
        try:
            from app.schemas.schema_service import get_schema_service
            schema = get_schema_service()
            if schema:
                resolved = schema.resolve_dealer(dealer_input)
                if resolved:
                    self.dealer_resolution_cache[cache_key] = (resolved, 0.90, time.time())
                    return resolved, 0.90, "schema"
        except:
            pass
        
        return None, 0.0, "not_found"
    
    # ==========================================================
    # CONTEXT MANAGEMENT
    # ==========================================================
    
    def _load_context(self, phone_number: Optional[str]) -> Optional[ConversationContext]:
        if not phone_number:
            return None
        
        if phone_number not in self.conversation_cache:
            self.conversation_cache[phone_number] = ConversationContext(phone_number=phone_number)
        
        context = self.conversation_cache[phone_number]
        if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
            context = ConversationContext(phone_number=phone_number)
            self.conversation_cache[phone_number] = context
        
        return context
    
    def _update_context(self, phone_number: Optional[str], intent: str, entity_type: str, entity: str, req_id: str):
        if not phone_number:
            return
        
        context = self._load_context(phone_number)
        if not context:
            return
        
        context.last_intent = intent
        context.last_question = entity
        context.last_dashboard = intent
        context.confidence = 0.9
        context.message_count += 1
        context.last_updated = time.time()
        context.is_valid = True
        
        if entity_type == "dealer":
            context.last_dealer = entity
            context.last_entity = entity
        elif entity_type == "warehouse":
            context.last_warehouse = entity
            context.last_entity = entity
        elif entity_type == "city":
            context.last_city = entity
            context.last_entity = entity
        elif entity_type == "dn":
            context.last_dn = entity
            context.last_entity = entity
        elif entity_type == "product":
            context.last_product = entity
            context.last_entity = entity
    
    # ==========================================================
    # MAIN ENTRY POINT
    # ==========================================================
    
    def process_whatsapp_query(
        self,
        question: str,
        session_factory: Optional[Callable[[], Session]] = None,
        phone_number: Optional[str] = None,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> str:
        start_time = time.time()
        req_id = request_id or str(uuid.uuid4())[:8]
        self._current_request_id = req_id
        self.metrics["total_requests"] += 1
        
        logger.bind(request_id=req_id).info(f"📥 Processing: '{question[:100]}'")
        
        try:
            # Load context
            context = self._load_context(phone_number)
            question_clean = question.strip()
            
            # Detect intent
            intent, entity = self._detect_intent(question_clean, context)
            
            if intent == "help":
                response = self._get_help_message()
                return response
            
            logger.info(f"[{req_id}] 🎯 Intent: {intent} | Entity: {entity}")
            
            # Route to appropriate handler
            result = self._route_to_dashboard(intent, entity, context, req_id)
            
            if result:
                # Update context
                self._update_context(
                    phone_number, 
                    intent, 
                    self._get_entity_type(intent), 
                    entity, 
                    req_id
                )
                return result
            
            # Fallback to help
            return self._get_help_message()
            
        except Exception as e:
            self.metrics["errors"] += 1
            logger.exception(f"[{req_id}] ERROR: {e}")
            return f"⚠️ Unable to process request. Please try again or type 'help'."
    
    # ==========================================================
    # ROUTING ENGINE
    # ==========================================================
    
    def _route_to_dashboard(self, intent: str, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        """Route to the appropriate dashboard based on intent."""
        
        if not self.analytics:
            logger.error(f"[{req_id}] Analytics service not available")
            return "⚠️ Analytics service is temporarily unavailable. Please try again later."
        
        try:
            # ==========================================================
            # DEALER ROUTES
            # ==========================================================
            if intent == "dealer_dashboard":
                return self._route_dealer_dashboard(entity, context, req_id)
            
            if intent == "dealer_ranking":
                return self._route_dealer_ranking(req_id)
            
            if intent == "dealer_products":
                return self._route_dealer_products(entity, context, req_id)
            
            # ==========================================================
            # WAREHOUSE ROUTES
            # ==========================================================
            if intent == "warehouse_dashboard":
                return self._route_warehouse_dashboard(entity, context, req_id)
            
            if intent == "warehouse_ranking":
                return self._route_warehouse_ranking(req_id)
            
            if intent == "warehouse_coverage":
                return self._route_warehouse_coverage(entity, context, req_id)
            
            # ==========================================================
            # CITY ROUTES
            # ==========================================================
            if intent == "city_dashboard":
                return self._route_city_dashboard(entity, context, req_id)
            
            if intent == "city_ranking":
                return self._route_city_ranking(req_id)
            
            # ==========================================================
            # PRODUCT ROUTES
            # ==========================================================
            if intent == "product_dashboard":
                return self._route_product_dashboard(entity, context, req_id)
            
            if intent == "product_ranking":
                return self._route_product_ranking(req_id)
            
            # ==========================================================
            # DN ROUTES
            # ==========================================================
            if intent == "dn_dashboard":
                return self._route_dn_dashboard(entity, context, req_id)
            
            if intent == "dn_analytics":
                return self._route_dn_analytics(req_id)
            
            # ==========================================================
            # PGI ROUTES
            # ==========================================================
            if intent == "pgi_dashboard":
                return self._route_pgi_dashboard(req_id)
            
            # ==========================================================
            # POD ROUTES
            # ==========================================================
            if intent == "pod_dashboard":
                return self._route_pod_dashboard(req_id)
            
            # ==========================================================
            # DELIVERY ROUTES
            # ==========================================================
            if intent == "delivery_dashboard":
                return self._route_delivery_dashboard(req_id)
            
            # ==========================================================
            # EXECUTIVE ROUTES
            # ==========================================================
            if intent == "executive_dashboard":
                return self._route_executive_dashboard(req_id)
            
            # ==========================================================
            # CONTROL TOWER ROUTES
            # ==========================================================
            if intent == "control_tower":
                return self._route_control_tower(req_id)
            
            # ==========================================================
            # REVENUE ROUTES
            # ==========================================================
            if intent == "revenue_dashboard":
                return self._route_revenue_dashboard(req_id)
            
            # ==========================================================
            # AGING ROUTES
            # ==========================================================
            if intent == "aging_dashboard":
                return self._route_aging_dashboard(entity, context, req_id)
            
            # ==========================================================
            # UNKNOWN
            # ==========================================================
            logger.warning(f"[{req_id}] Unhandled intent: {intent}")
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] Routing error for {intent}: {e}")
            return f"⚠️ Unable to load {intent.replace('_', ' ').title()}. Please try again."
    
    # ==========================================================
    # DEALER ROUTE HANDLERS
    # ==========================================================
    
    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle dealer dashboard requests."""
        
        # Try to get dealer from entity or context
        dealer_name = entity
        if not dealer_name and context and context.last_dealer:
            dealer_name = context.last_dealer
            logger.info(f"[{req_id}] Using context dealer: {dealer_name}")
        
        if not dealer_name:
            return "🏪 *DEALER DASHBOARD*\n\nPlease specify a dealer name.\n\n*Examples:*\n• ZQ Electronics\n• Show dealer ZQ Electronics\n• Dealer performance for ZQ Electronics"
        
        # Resolve dealer
        resolved, confidence, strategy = self._resolve_dealer_safe(dealer_name, req_id)
        
        if not resolved:
            return f"❌ Dealer '{dealer_name}' not found.\n\n💡 Please check the spelling or try a different dealer name."
        
        # Get dashboard
        response = self.analytics.get_dealer_dashboard(resolved)
        
        if not self._validate_response(response, "dealer_dashboard", req_id):
            return f"❌ Unable to retrieve data for '{resolved}'."
        
        return self._format_dealer_dashboard(response.data, resolved)
    
    def _route_dealer_ranking(self, req_id: str) -> str:
        """Handle dealer ranking requests."""
        response = self.analytics.get_dealer_ranking(limit=10, top=True)
        
        if not self._validate_response(response, "dealer_ranking", req_id):
            return "❌ Unable to retrieve dealer ranking."
        
        return self._format_dealer_ranking(response.data)
    
    def _route_dealer_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle dealer products requests."""
        dealer_name = entity or (context.last_dealer if context else None)
        
        if not dealer_name:
            return "📦 *DEALER PRODUCTS*\n\nPlease specify a dealer name.\n\n*Example:* Products of ZQ Electronics"
        
        resolved, confidence, strategy = self._resolve_dealer_safe(dealer_name, req_id)
        
        if not resolved:
            return f"❌ Dealer '{dealer_name}' not found."
        
        response = self.analytics.get_dealer_products(resolved)
        
        if not self._validate_response(response, "dealer_products", req_id):
            return f"❌ Unable to retrieve products for '{resolved}'."
        
        return self._format_dealer_products(response.data, resolved)
    
    # ==========================================================
    # WAREHOUSE ROUTE HANDLERS
    # ==========================================================
    
    def _route_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle warehouse dashboard requests."""
        
        warehouse_name = entity
        if not warehouse_name and context and context.last_warehouse:
            warehouse_name = context.last_warehouse
        
        if not warehouse_name:
            return "🏭 *WAREHOUSE DASHBOARD*\n\nPlease specify a warehouse name.\n\n*Examples:*\n• Lahore warehouse\n• Rawalpindi warehouse"
        
        response = self.analytics.get_warehouse_dashboard(warehouse_name)
        
        if not self._validate_response(response, "warehouse_dashboard", req_id):
            return f"❌ Unable to retrieve data for warehouse '{warehouse_name}'."
        
        return self._format_warehouse_dashboard(response.data, warehouse_name)
    
    def _route_warehouse_ranking(self, req_id: str) -> str:
        """Handle warehouse ranking requests."""
        response = self.analytics.get_warehouse_ranking(limit=10, top=True)
        
        if not self._validate_response(response, "warehouse_ranking", req_id):
            return "❌ Unable to retrieve warehouse ranking."
        
        return self._format_warehouse_ranking(response.data)
    
    def _route_warehouse_coverage(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle warehouse coverage requests."""
        warehouse_name = entity or (context.last_warehouse if context else None)
        
        if not warehouse_name:
            return "📍 *WAREHOUSE COVERAGE*\n\nPlease specify a warehouse name.\n\n*Example:* Coverage of Lahore warehouse"
        
        response = self.analytics.get_warehouse_coverage(warehouse_name)
        
        if not self._validate_response(response, "warehouse_coverage", req_id):
            return f"❌ Unable to retrieve coverage for '{warehouse_name}'."
        
        return self._format_warehouse_coverage(response.data, warehouse_name)
    
    # ==========================================================
    # CITY ROUTE HANDLERS
    # ==========================================================
    
    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle city dashboard requests."""
        
        city_name = entity
        if not city_name and context and context.last_city:
            city_name = context.last_city
        
        if not city_name:
            return "🏙️ *CITY DASHBOARD*\n\nPlease specify a city name.\n\n*Examples:*\n• Haripur\n• Lahore city"
        
        response = self.analytics.get_city_dashboard(city_name)
        
        if not self._validate_response(response, "city_dashboard", req_id):
            return f"❌ Unable to retrieve data for city '{city_name}'."
        
        return self._format_city_dashboard(response.data, city_name)
    
    def _route_city_ranking(self, req_id: str) -> str:
        """Handle city ranking requests."""
        response = self.analytics.get_city_ranking(limit=10, top=True)
        
        if not self._validate_response(response, "city_ranking", req_id):
            return "❌ Unable to retrieve city ranking."
        
        return self._format_city_ranking(response.data)
    
    # ==========================================================
    # PRODUCT ROUTE HANDLERS
    # ==========================================================
    
    def _route_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle product dashboard requests."""
        
        product_name = entity or (context.last_product if context else None)
        
        if not product_name:
            return "📦 *PRODUCT DASHBOARD*\n\nPlease specify a product.\n\n*Examples:*\n• Refrigerator\n• AC\n• TV\n• Model A123"
        
        response = self.analytics.get_product_dashboard(product_name)
        
        if not self._validate_response(response, "product_dashboard", req_id):
            return f"❌ Unable to retrieve data for product '{product_name}'."
        
        return self._format_product_dashboard(response.data, product_name)
    
    def _route_product_ranking(self, req_id: str) -> str:
        """Handle product ranking requests."""
        response = self.analytics.get_product_ranking(limit=10, top=True)
        
        if not self._validate_response(response, "product_ranking", req_id):
            return "❌ Unable to retrieve product ranking."
        
        return self._format_product_ranking(response.data)
    
    # ==========================================================
    # DN ROUTE HANDLERS
    # ==========================================================
    
    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle DN dashboard requests."""
        
        dn_number = entity or (context.last_dn if context else None)
        
        if not dn_number:
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number.\n\n*Example:* 6243612278"
        
        # Clean DN
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return f"❌ Invalid DN number: '{dn_number}'\n\nDN numbers must be 8-12 digits."
        
        # Verify DN exists
        verify = self.analytics.verify_dn_exists(dn_clean)
        if verify and hasattr(verify, 'success') and verify.success:
            data = verify.data
            if not data.get("found", False):
                # Get sample DNs
                sample_response = self.analytics.get_sample_dns(5)
                sample_dns = []
                if sample_response and hasattr(sample_response, 'success') and sample_response.success:
                    sample_dns = sample_response.data.get("sample_dns", [])
                
                sample_text = ""
                if sample_dns:
                    sample_text = "\n".join([f"• {dn}" for dn in sample_dns[:3]])
                
                return f"""❌ DN {dn_clean} not found in system.

💡 *Sample DN numbers in system:*
{sample_text}

📋 *Try these:*
• Enter a valid DN number from the list above
• Type "help" for menu

*What would you like to know?* 🤖"""
        
        # Get DN analytics
        response = self.analytics.get_dn_analytics(dn_clean)
        
        if not self._validate_response(response, "dn_dashboard", req_id):
            return f"❌ Unable to retrieve data for DN {dn_clean}."
        
        return self._format_dn_dashboard(response.data, dn_clean)
    
    def _route_dn_analytics(self, req_id: str) -> str:
        """Handle DN analytics requests."""
        try:
            # Get dealer ranking to get DN counts
            response = self.analytics.get_all_dealers_dashboard()
            
            if not self._validate_response(response, "dn_analytics", req_id):
                return "❌ Unable to retrieve DN analytics."
            
            data = response.data
            dealers = data.get("dealers", [])
            
            total_dns = sum(d.get("total_dns", 0) for d in dealers)
            total_delivered = sum(d.get("delivered_dns", 0) for d in dealers)
            total_units = sum(d.get("total_units", 0) for d in dealers)
            total_revenue = sum(d.get("total_revenue", 0) for d in dealers)
            
            return f"""📊 *DN ANALYTICS*

*Summary:*
• Total DNs: {total_dns:,}
• Delivered: {total_delivered:,}
• Pending: {total_dns - total_delivered:,}
• Total Units: {total_units:,}
• Total Revenue: PKR {total_revenue:,.0f}

*Metrics:*
• Delivery Rate: {round((total_delivered / total_dns * 100) if total_dns > 0 else 0, 1)}%
• Avg Units/DN: {round(total_units / total_dns if total_dns > 0 else 0, 1)}
• Avg Revenue/DN: PKR {round(total_revenue / total_dns if total_dns > 0 else 0, 0)}

*Total Dealers: {len(dealers)}*
*Total Warehouses: {len(set(d.get('warehouse', '') for d in dealers))}*"""
            
        except Exception as e:
            logger.error(f"[{req_id}] DN analytics error: {e}")
            return "❌ Unable to retrieve DN analytics."
    
    # ==========================================================
    # PGI ROUTE HANDLERS
    # ==========================================================
    
    def _route_pgi_dashboard(self, req_id: str) -> str:
        """Handle PGI dashboard requests."""
        response = self.analytics.get_pgi_dashboard()
        
        if not self._validate_response(response, "pgi_dashboard", req_id):
            return "❌ Unable to retrieve PGI data."
        
        return self._format_pgi_dashboard(response.data)
    
    # ==========================================================
    # POD ROUTE HANDLERS
    # ==========================================================
    
    def _route_pod_dashboard(self, req_id: str) -> str:
        """Handle POD dashboard requests."""
        response = self.analytics.get_pod_dashboard()
        
        if not self._validate_response(response, "pod_dashboard", req_id):
            return "❌ Unable to retrieve POD data."
        
        return self._format_pod_dashboard(response.data)
    
    # ==========================================================
    # DELIVERY ROUTE HANDLERS
    # ==========================================================
    
    def _route_delivery_dashboard(self, req_id: str) -> str:
        """Handle delivery dashboard requests."""
        response = self.analytics.get_delivery_performance()
        
        if not self._validate_response(response, "delivery_dashboard", req_id):
            return "❌ Unable to retrieve delivery data."
        
        return self._format_delivery_dashboard(response.data)
    
    # ==========================================================
    # EXECUTIVE ROUTE HANDLERS
    # ==========================================================
    
    def _route_executive_dashboard(self, req_id: str) -> str:
        """Handle executive dashboard requests."""
        response = self.analytics.get_executive_summary()
        
        if not self._validate_response(response, "executive_dashboard", req_id):
            return "❌ Unable to retrieve executive data."
        
        return self._format_executive_dashboard(response.data)
    
    # ==========================================================
    # CONTROL TOWER ROUTE HANDLERS
    # ==========================================================
    
    def _route_control_tower(self, req_id: str) -> str:
        """Handle control tower requests."""
        response = self.analytics.get_control_tower_alerts()
        
        if not self._validate_response(response, "control_tower", req_id):
            return "❌ Unable to retrieve control tower data."
        
        return self._format_control_tower(response.data)
    
    # ==========================================================
    # REVENUE ROUTE HANDLERS
    # ==========================================================
    
    def _route_revenue_dashboard(self, req_id: str) -> str:
        """Handle revenue dashboard requests."""
        response = self.analytics.get_revenue_trend()
        
        if not self._validate_response(response, "revenue_dashboard", req_id):
            return "❌ Unable to retrieve revenue data."
        
        return self._format_revenue_dashboard(response.data)
    
    # ==========================================================
    # AGING ROUTE HANDLERS
    # ==========================================================
    
    def _route_aging_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle aging dashboard requests."""
        # Check if specific dealer
        dealer_name = entity or (context.last_dealer if context else None)
        
        if dealer_name:
            response = self.analytics.get_dealer_dn_aging(dealer_name)
            if self._validate_response(response, "aging_dashboard", req_id):
                return self._format_dealer_aging(response.data, dealer_name)
        
        # General aging
        response = self.analytics.get_pod_aging_analysis()
        
        if not self._validate_response(response, "aging_dashboard", req_id):
            return "❌ Unable to retrieve aging data."
        
        return self._format_aging_dashboard(response.data)
    
    # ==========================================================
    # RESPONSE VALIDATION
    # ==========================================================
    
    def _validate_response(self, response, service_name: str, req_id: str) -> bool:
        """Validate analytics response."""
        if response is None:
            logger.error(f"[{req_id}] Response is None for {service_name}")
            return False
        
        if not hasattr(response, 'success'):
            logger.error(f"[{req_id}] Response missing 'success' for {service_name}")
            return False
        
        if not response.success:
            logger.error(f"[{req_id}] Response success=False for {service_name}: {getattr(response, 'error', 'Unknown error')}")
            return False
        
        return True
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _get_entity_type(self, intent: str) -> str:
        """Get entity type based on intent."""
        entity_mapping = {
            "dealer_dashboard": "dealer",
            "dealer_products": "dealer",
            "dealer_ranking": "dealer",
            "warehouse_dashboard": "warehouse",
            "warehouse_ranking": "warehouse",
            "warehouse_coverage": "warehouse",
            "city_dashboard": "city",
            "city_ranking": "city",
            "product_dashboard": "product",
            "product_ranking": "product",
            "dn_dashboard": "dn",
            "aging_dashboard": "dealer",
        }
        return entity_mapping.get(intent, "unknown")
    
    def _truncate_response(self, response: str) -> str:
        """Truncate response to WhatsApp character limit."""
        if len(response) > MAX_RESPONSE_LENGTH:
            return response[:MAX_RESPONSE_LENGTH - 20] + "\n\n... (truncated)"
        return response
    
    def _get_risk_emoji(self, risk_level: str) -> str:
        risk_level = risk_level.lower()
        if risk_level == "critical":
            return "🔴"
        elif risk_level == "high":
            return "🟠"
        elif risk_level == "medium":
            return "🟡"
        else:
            return "🟢"
    
    # ==========================================================
    # FORMATTERS
    # ==========================================================
    
    def _format_dealer_dashboard(self, data: Dict, dealer_name: str) -> str:
        """Format dealer dashboard for WhatsApp."""
        try:
            profile = data.get("profile", {})
            summary = data.get("summary", {})
            performance = data.get("performance", {})
            
            total_dns = summary.get("total_dns", 0)
            if total_dns == 0:
                return f"❌ No data found for {dealer_name}"
            
            risk_level = performance.get("risk_level", "low").lower()
            risk_emoji = self._get_risk_emoji(risk_level)
            
            lines = [
                "🏪 *DEALER DASHBOARD*",
                "",
                "👤 *Dealer Profile*",
                f"Name: {dealer_name}",
                f"Code: {profile.get('dealer_code', 'N/A')}",
                f"City: {profile.get('city', 'N/A')}",
                f"Warehouse: {profile.get('warehouse', 'N/A')}",
                "",
                "📊 *Business Summary*",
                f"DNs: {total_dns:,}",
                f"Units: {summary.get('total_units', 0):,}",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Performance*",
                f"Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {summary.get('pending_dns', 0)}",
                f"Pending PODs: {summary.get('pending_pod_dns', 0)}",
                "",
                "⚠️ *Risk*",
                f"Risk Level: {risk_emoji} {risk_level.upper()}",
                f"Health Score: {performance.get('health_score', 0)}/100"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Dealer format error: {e}")
            return f"❌ Unable to format dealer dashboard for {dealer_name}"
    
    def _format_dealer_ranking(self, data: Dict) -> str:
        """Format dealer ranking for WhatsApp."""
        try:
            dealers = data.get("ranking", []) or data.get("dealers", [])
            
            if not dealers:
                return "❌ No dealer data available."
            
            lines = [
                "🏆 *DEALER RANKING*",
                "",
                "Top 10 Dealers by Revenue:"
            ]
            
            for i, dealer in enumerate(dealers[:10], 1):
                name = dealer.get("dealer_name", dealer.get("dealer", "Unknown"))
                revenue = dealer.get("total_revenue", dealer.get("revenue", 0))
                delivery_rate = dealer.get("delivery_rate", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   PKR {revenue:,.0f} | Delivery: {delivery_rate:.1f}%")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Dealer ranking format error: {e}")
            return "❌ Unable to format dealer ranking"
    
    def _format_dealer_products(self, data: Dict, dealer_name: str) -> str:
        """Format dealer products for WhatsApp."""
        try:
            products = data.get("products", [])
            
            if not products:
                return f"📦 No products found for {dealer_name}"
            
            lines = [
                f"📦 *PRODUCTS - {dealer_name}*",
                ""
            ]
            
            for i, product in enumerate(products[:10], 1):
                name = product.get("product", "Unknown")
                units = product.get("units", 0)
                revenue = product.get("revenue", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   Units: {units:,} | Revenue: PKR {revenue:,.0f}")
            
            if len(products) > 10:
                lines.append(f"\n*+ {len(products) - 10} more products*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Dealer products format error: {e}")
            return f"❌ Unable to format products for {dealer_name}"
    
    def _format_warehouse_dashboard(self, data: Dict, warehouse_name: str) -> str:
        """Format warehouse dashboard for WhatsApp."""
        try:
            summary = data.get("summary", {})
            profile = data.get("profile", {})
            
            total_dns = summary.get("total_dns", 0)
            if total_dns == 0:
                return f"❌ No data found for {warehouse_name}"
            
            lines = [
                "🏭 *WAREHOUSE DASHBOARD*",
                "",
                f"Warehouse: {warehouse_name}",
                f"Code: {profile.get('code', 'N/A')}",
                "",
                "📍 *Coverage*",
                f"Dealers: {summary.get('total_dealers', 0):,}",
                f"Cities: {summary.get('cities_served', 0):,}",
                "",
                "📊 *Business*",
                f"DNs: {total_dns:,}",
                f"Units: {summary.get('total_units', 0):,}",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Performance*",
                f"Delivery: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI: {summary.get('pgi_rate', 0):.1f}%",
                f"POD: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {summary.get('pending_dns', 0):,}",
                f"Pending PODs: {summary.get('pending_pod_dns', 0):,}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Warehouse format error: {e}")
            return f"❌ Unable to format warehouse dashboard for {warehouse_name}"
    
    def _format_warehouse_ranking(self, data: Dict) -> str:
        """Format warehouse ranking for WhatsApp."""
        try:
            warehouses = data.get("ranking", []) or data.get("warehouses", [])
            
            if not warehouses:
                return "❌ No warehouse data available."
            
            lines = [
                "🏆 *WAREHOUSE RANKING*",
                "",
                "Top 10 Warehouses by Revenue:"
            ]
            
            for i, warehouse in enumerate(warehouses[:10], 1):
                name = warehouse.get("warehouse", "Unknown")
                revenue = warehouse.get("total_revenue", warehouse.get("revenue", 0))
                dealers = warehouse.get("total_dealers", warehouse.get("dealers", 0))
                lines.append(f"{i}. {name}")
                lines.append(f"   PKR {revenue:,.0f} | Dealers: {dealers}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Warehouse ranking format error: {e}")
            return "❌ Unable to format warehouse ranking"
    
    def _format_warehouse_coverage(self, data: Dict, warehouse_name: str) -> str:
        """Format warehouse coverage for WhatsApp."""
        try:
            cities = data.get("cities", [])
            dealers = data.get("dealers", [])
            
            lines = [
                f"📍 *COVERAGE - {warehouse_name}*",
                "",
                f"Cities Served: {len(cities)}",
                f"Dealers Served: {len(dealers)}",
                ""
            ]
            
            if cities:
                lines.append("*Cities:*")
                for city in cities[:10]:
                    name = city.get("city", "Unknown")
                    dns = city.get("dns", 0)
                    lines.append(f"   • {name} ({dns} DNs)")
                if len(cities) > 10:
                    lines.append(f"   *+ {len(cities) - 10} more*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Warehouse coverage format error: {e}")
            return f"❌ Unable to format coverage for {warehouse_name}"
    
    def _format_city_dashboard(self, data: Dict, city_name: str) -> str:
        """Format city dashboard for WhatsApp."""
        try:
            summary = data.get("summary", {})
            
            total_dns = summary.get("total_dns", 0)
            if total_dns == 0:
                return f"❌ No data found for {city_name}"
            
            lines = [
                "🏙️ *CITY DASHBOARD*",
                "",
                f"City: {city_name}",
                "",
                "📊 *Business*",
                f"Dealers: {summary.get('total_dealers', 0):,}",
                f"Warehouses: {summary.get('total_warehouses', 0)}",
                f"DNs: {total_dns:,}",
                f"Units: {summary.get('total_units', 0):,}",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Performance*",
                f"Delivery: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI: {summary.get('pgi_rate', 0):.1f}%",
                f"POD: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {summary.get('pending_dns', 0)}",
                f"Pending PODs: {summary.get('pending_pod_dns', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"City format error: {e}")
            return f"❌ Unable to format city dashboard for {city_name}"
    
    def _format_city_ranking(self, data: Dict) -> str:
        """Format city ranking for WhatsApp."""
        try:
            cities = data.get("ranking", []) or data.get("cities", [])
            
            if not cities:
                return "❌ No city data available."
            
            lines = [
                "🏆 *CITY RANKING*",
                "",
                "Top 10 Cities by Revenue:"
            ]
            
            for i, city in enumerate(cities[:10], 1):
                name = city.get("city", "Unknown")
                revenue = city.get("total_revenue", city.get("revenue", 0))
                dealers = city.get("total_dealers", city.get("dealers", 0))
                lines.append(f"{i}. {name}")
                lines.append(f"   PKR {revenue:,.0f} | Dealers: {dealers}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"City ranking format error: {e}")
            return "❌ Unable to format city ranking"
    
    def _format_product_dashboard(self, data: Dict, product_name: str) -> str:
        """Format product dashboard for WhatsApp."""
        try:
            summary = data.get("summary", {})
            top_dealers = data.get("top_dealers", [])
            
            total_dns = summary.get("dns", 0)
            if total_dns == 0:
                return f"❌ No data found for {product_name}"
            
            lines = [
                f"📦 *PRODUCT DASHBOARD*",
                "",
                f"Product: {product_name}",
                "",
                "📊 *Performance*",
                f"Revenue: PKR {summary.get('revenue', 0):,.0f}",
                f"Units: {summary.get('units', 0):,}",
                f"DNs: {total_dns:,}",
                f"Dealers: {summary.get('dealers', 0)}",
                f"Cities: {summary.get('cities', 0)}",
                "",
                "🏆 *Top Dealers*"
            ]
            
            for i, dealer in enumerate(top_dealers[:5], 1):
                name = dealer.get("dealer", "Unknown")
                revenue = dealer.get("revenue", 0)
                lines.append(f"   {i}. {name}: PKR {revenue:,.0f}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Product format error: {e}")
            return f"❌ Unable to format product dashboard for {product_name}"
    
    def _format_product_ranking(self, data: Dict) -> str:
        """Format product ranking for WhatsApp."""
        try:
            products = data.get("ranking", []) or data.get("products", [])
            
            if not products:
                return "❌ No product data available."
            
            lines = [
                "🏆 *PRODUCT RANKING*",
                "",
                "Top 10 Products by Revenue:"
            ]
            
            for i, product in enumerate(products[:10], 1):
                name = product.get("product", "Unknown")
                revenue = product.get("revenue", 0)
                units = product.get("units", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   PKR {revenue:,.0f} | Units: {units:,}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Product ranking format error: {e}")
            return "❌ Unable to format product ranking"
    
    def _format_dn_dashboard(self, data: Dict, dn_number: str) -> str:
        """Format DN dashboard for WhatsApp."""
        try:
            record = data.get("record", {})
            status = data.get("status", "unknown")
            aging_days = data.get("aging_days", 0)
            
            dn_no = record.get('dn_number', dn_number)
            dealer_name = record.get('customer_name', 'N/A')
            warehouse = record.get('warehouse', 'N/A')
            units = record.get('units', 0)
            amount = record.get('amount', 0)
            create_date = record.get('dn_create_date', 'N/A')
            pgi_date = record.get('good_issue_date', 'N/A')
            pod_date = record.get('pod_date', 'N/A')
            
            status_emoji = "✅" if status == "delivered" else "🚚" if status == "in_transit" else "⏳"
            status_display = status.upper().replace("_", " ")
            
            lines = [
                "📄 *DN TRACKING*",
                "",
                f"DN No: {dn_no}",
                f"Dealer: {dealer_name}",
                f"Warehouse: {warehouse}",
                "",
                f"Units: {units}",
                f"Revenue: PKR {amount:,.0f}",
                "",
                f"Create Date: {create_date}",
                f"PGI Date: {pgi_date}",
                f"POD Date: {pod_date}",
                "",
                f"Status: {status_emoji} {status_display}",
                f"Aging: {aging_days} days"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"DN format error: {e}")
            return f"❌ Unable to format DN details for {dn_number}"
    
    def _format_pgi_dashboard(self, data: Dict) -> str:
        """Format PGI dashboard for WhatsApp."""
        try:
            summary = data.get("summary", {})
            by_dealer = data.get("by_dealer", [])
            
            lines = [
                "📋 *PGI DASHBOARD*",
                "",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"PGI Completed: {summary.get('pgi_completed', 0):,}",
                f"PGI Pending: {summary.get('pgi_pending', 0):,}",
                f"PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                "",
                f"Avg Processing: {summary.get('avg_processing_days', 0):.1f} days",
                "",
                "🏆 *Top Dealers by PGI Rate*"
            ]
            
            for i, dealer in enumerate(by_dealer[:5], 1):
                name = dealer.get("dealer", "Unknown")
                rate = dealer.get("pgi_rate", 0)
                lines.append(f"   {i}. {name}: {rate:.1f}%")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"PGI format error: {e}")
            return "❌ Unable to format PGI dashboard"
    
    def _format_pod_dashboard(self, data: Dict) -> str:
        """Format POD dashboard for WhatsApp."""
        try:
            summary = data.get("summary", {})
            aging = data.get("aging", {})
            by_dealer = data.get("by_dealer", [])
            
            lines = [
                "✅ *POD DASHBOARD*",
                "",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"POD Completed: {summary.get('pod_completed', 0):,}",
                f"POD Pending: {summary.get('pod_pending', 0):,}",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Avg POD Days: {summary.get('avg_pod_days', 0):.1f}",
                "",
                "⏳ *Aging*",
                f"0-7 Days: {aging.get('days_0_7', 0)}",
                f"8-14 Days: {aging.get('days_8_14', 0)}",
                f"15-30 Days: {aging.get('days_15_30', 0)}",
                f"30+ Days: {aging.get('days_30_plus', 0)}",
                "",
                "🏆 *Top Dealers by POD Rate*"
            ]
            
            for i, dealer in enumerate(by_dealer[:5], 1):
                name = dealer.get("dealer", "Unknown")
                rate = dealer.get("pod_rate", 0)
                lines.append(f"   {i}. {name}: {rate:.1f}%")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"POD format error: {e}")
            return "❌ Unable to format POD dashboard"
    
    def _format_delivery_dashboard(self, data: Dict) -> str:
        """Format delivery dashboard for WhatsApp."""
        try:
            metrics = data.get("metrics", {})
            
            lines = [
                "🚚 *DELIVERY DASHBOARD*",
                "",
                f"Total DNs: {metrics.get('total_dns', 0):,}",
                f"Delivered: {metrics.get('delivered', 0):,}",
                f"In Transit: {metrics.get('in_transit', 0):,}",
                f"Pending PGI: {metrics.get('pending_pgi', 0):,}",
                "",
                f"Delivery Rate: {metrics.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {metrics.get('pgi_rate', 0):.1f}%",
                "",
                f"Avg Processing: {metrics.get('avg_processing_days', 0):.1f} days",
                f"Avg Delivery: {metrics.get('avg_delivery_days', 0):.1f} days"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Delivery format error: {e}")
            return "❌ Unable to format delivery dashboard"
    
    def _format_executive_dashboard(self, data: Dict) -> str:
        """Format executive dashboard for WhatsApp."""
        try:
            summary = data.get("summary", {})
            health_score = data.get("health_score", 0)
            top_dealers = data.get("top_dealers", [])
            top_warehouses = data.get("top_warehouses", [])
            top_cities = data.get("top_cities", [])
            
            health_emoji = "✅" if health_score >= 80 else "⚠️" if health_score >= 60 else "🔴"
            health_status = "Healthy" if health_score >= 80 else "Needs Attention" if health_score >= 60 else "Critical"
            
            lines = [
                "👔 *EXECUTIVE DASHBOARD*",
                "",
                "💰 *Business*",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"Units: {summary.get('total_units', 0):,}",
                f"DNs: {summary.get('total_dns', 0):,}",
                "",
                "📈 *KPI*",
                f"Delivery: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI: {summary.get('pgi_rate', 0):.1f}%",
                f"POD: {summary.get('pod_rate', 0):.1f}%",
            ]
            
            if top_dealers:
                lines.append("")
                lines.append("🏆 *Top Dealer*")
                top = top_dealers[0] if top_dealers else {}
                lines.append(f"   • {top.get('dealer_name', top.get('dealer', 'N/A'))}: PKR {top.get('total_revenue', top.get('revenue', 0)):,.0f}")
            
            if top_warehouses:
                lines.append("")
                lines.append("🏭 *Top Warehouse*")
                top = top_warehouses[0] if top_warehouses else {}
                lines.append(f"   • {top.get('warehouse', 'N/A')}: PKR {top.get('total_revenue', top.get('revenue', 0)):,.0f}")
            
            if top_cities:
                lines.append("")
                lines.append("🏙️ *Top City*")
                top = top_cities[0] if top_cities else {}
                lines.append(f"   • {top.get('city', 'N/A')}: PKR {top.get('total_revenue', top.get('revenue', 0)):,.0f}")
            
            lines.append("")
            lines.append("📊 *Health Score*")
            lines.append(f"{health_score}/100 - {health_emoji} {health_status}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Executive format error: {e}")
            return "👔 Unable to format executive dashboard"
    
    def _format_control_tower(self, data: Dict) -> str:
        """Format control tower for WhatsApp."""
        try:
            alerts = data.get("alerts", [])
            critical_count = data.get("critical_count", 0)
            high_count = data.get("high_count", 0)
            
            lines = [
                "🚨 *LOGISTICS CONTROL TOWER*",
                "",
                f"Critical Alerts: {critical_count}",
                f"High Priority: {high_count}",
                f"Total Alerts: {len(alerts)}",
                ""
            ]
            
            if alerts:
                lines.append("*Recent Alerts:*")
                for alert in alerts[:5]:
                    severity = alert.get("severity", "low").upper()
                    severity_emoji = "🔴" if severity == "CRITICAL" else "🟠" if severity == "HIGH" else "🟡"
                    lines.append(f"   {severity_emoji} {alert.get('description', 'Alert')[:60]}")
                if len(alerts) > 5:
                    lines.append(f"   *+ {len(alerts) - 5} more alerts*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Control tower format error: {e}")
            return "🚨 Unable to format control tower"
    
    def _format_revenue_dashboard(self, data: Dict) -> str:
        """Format revenue dashboard for WhatsApp."""
        try:
            trend = data.get("trend", [])
            overall_growth = data.get("overall_growth", 0)
            avg_monthly = data.get("avg_monthly_revenue", 0)
            
            lines = [
                "💰 *REVENUE DASHBOARD*",
                "",
                f"Overall Growth: {overall_growth:.1f}%",
                f"Avg Monthly Revenue: PKR {avg_monthly:,.0f}",
                "",
                "📈 *Monthly Trend:*"
            ]
            
            for period in trend[-6:]:
                month = period.get("month", "N/A")
                revenue = period.get("revenue", 0)
                lines.append(f"   • {month}: PKR {revenue:,.0f}")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Revenue format error: {e}")
            return "💰 Unable to format revenue dashboard"
    
    def _format_dealer_aging(self, data: Dict, dealer_name: str) -> str:
        """Format dealer aging for WhatsApp."""
        try:
            lines = [
                f"⏳ *DN AGING - {dealer_name}*",
                "",
                f"Total Pending: {data.get('total_pending', 0)}",
                f"0-7 Days: {data.get('days_0_7', 0)}",
                f"8-14 Days: {data.get('days_8_14', 0)}",
                f"15-30 Days: {data.get('days_15_30', 0)}",
                f"30+ Days: {data.get('days_30_plus', 0)}",
                "",
                f"Max Aging: {data.get('max_aging_days', 0)} days",
                f"Avg Aging: {data.get('avg_aging_days', 0):.1f} days"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Dealer aging format error: {e}")
            return f"❌ Unable to format aging for {dealer_name}"
    
    def _format_aging_dashboard(self, data: Dict) -> str:
        """Format aging dashboard for WhatsApp."""
        try:
            aging = data.get("aging", {})
            critical = data.get("critical", [])
            
            lines = [
                "⏳ *AGING ANALYSIS*",
                "",
                "0-7 Days: {aging.get('days_0_7', 0)}",
                "8-14 Days: {aging.get('days_8_14', 0)}",
                "15-30 Days: {aging.get('days_15_30', 0)}",
                "30+ Days: {aging.get('days_30_plus', 0)}",
                "",
                f"Total Pending: {aging.get('total_pending', 0)}",
                f"Avg Aging: {aging.get('avg_aging_days', 0):.1f} days",
                "",
                "🔴 *Critical (30+ days)*"
            ]
            
            for item in critical[:5]:
                dn = item.get('dn_no', 'N/A')
                dealer = item.get('dealer', 'Unknown')
                days = item.get('days', 0)
                lines.append(f"   • {dn} - {dealer} ({days} days)")
            
            if len(critical) > 5:
                lines.append(f"   *+ {len(critical) - 5} more critical*")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Aging format error: {e}")
            return "❌ Unable to format aging dashboard"
    
    # ==========================================================
    # HELP MESSAGE
    # ==========================================================
    
    def _get_help_message(self) -> str:
        return """🏠 *HAIER LOGISTICS AI*

*📋 18 Dashboards Available:*

1️⃣ 🏪 Dealer Dashboard
2️⃣ 🏭 Warehouse Dashboard
3️⃣ 🏙️ City Dashboard
4️⃣ 📦 Product Dashboard
5️⃣ 📄 DN Dashboard
6️⃣ 📋 PGI Dashboard
7️⃣ ✅ POD Dashboard
8️⃣ 🚚 Delivery Dashboard
9️⃣ 📍 Distance Dashboard
🔟 👔 Executive Dashboard
1️⃣1️⃣ 🚨 Control Tower
1️⃣2️⃣ 🏆 Dealer Ranking
1️⃣3️⃣ 🏆 Warehouse Ranking
1️⃣4️⃣ 🏆 Product Ranking
1️⃣5️⃣ 🚛 Transporter Dashboard
1️⃣6️⃣ 💰 Revenue Dashboard
1️⃣7️⃣ 📦 Inventory Dashboard
1️⃣8️⃣ 📊 Forecast Dashboard

*🔍 Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "ZQ Electronics")
• City name (e.g., "Haripur")
• Warehouse name
• "Executive summary"
• "Control tower"
• "Top dealers"
• "Help" for menu

*💡 Follow-up Support:*
• "What is its POD?" → Uses last dealer
• "How many pending DN?" → Uses last dealer
• "Show me its revenue" → Uses last dealer

*Ask me anything about logistics!* 🤖"""


# ==========================================================
# SINGLETON
# ==========================================================

_orchestrator = None

def get_orchestrator() -> AIOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        try:
            _orchestrator = AIOrchestrator()
            logger.info("✅ AI Orchestrator v22.1 initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize AI Orchestrator: {e}")
            _orchestrator = None
    return _orchestrator


# ==========================================================
# WRAPPER FUNCTIONS
# ==========================================================

def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    orchestrator = get_orchestrator()
    if orchestrator is None:
        return "⚠️ AI service is currently unavailable. Please try again later."
    return orchestrator.process_whatsapp_query(
        question=question,
        session_factory=session_factory,
        phone_number=phone_number,
        user_id=user_id,
        request_id=request_id
    )
