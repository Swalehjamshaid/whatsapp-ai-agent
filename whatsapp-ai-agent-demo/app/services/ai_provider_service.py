# ==========================================================
# FILE: app/services/ai_provider_service.py (v24.0 - COMPLETE)
# ==========================================================
# PURPOSE: POSTGRESQL-DRIVEN AI ROUTER
# VERSION: 24.0 - 100% PostgreSQL Integration
# ==========================================================

import time
import uuid
import re
from typing import Optional, Callable, Any, Dict, List, Tuple
from dataclasses import dataclass, field
from cachetools import TTLCache, LRUCache
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, String, and_, or_

# ==========================================================
# ✅ POSTGRESQL IMPORTS - THE SOURCE OF TRUTH
# ==========================================================

from app.models import DeliveryReport
from app.database import SessionLocal, check_database_connection

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

# ==========================================================
# CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800
MAX_RESPONSE_LENGTH = 2500
QUERY_TIMEOUT_SECONDS = 10
MAX_RETRY_ATTEMPTS = 3

# ==========================================================
# ✅ DATABASE CONNECTION TEST
# ==========================================================

def test_database_connection() -> Dict[str, Any]:
    """
    Test PostgreSQL connection from AI Provider.
    This verifies the connection to delivery_reports table.
    """
    try:
        db = SessionLocal()
        total_records = db.query(DeliveryReport).count()
        db.close()
        
        return {
            "connected": True,
            "total_records": total_records,
            "table_name": "delivery_reports",
            "status": "healthy"
        }
    except Exception as e:
        logger.error(f"AI Database connection test failed: {e}")
        return {
            "connected": False,
            "error": str(e),
            "status": "unhealthy"
        }


# ==========================================================
# POSTGRESQL RESOLVER - PURE POSTGRESQL
# ==========================================================

class PostgreSQLResolver:
    """Pure PostgreSQL-based entity resolution - No schema_service"""
    
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory
        self._cache = TTLCache(maxsize=2000, ttl=3600)
        self.DeliveryReport = DeliveryReport
    
    def _get_session(self) -> Optional[Session]:
        if not self.session_factory:
            logger.error("❌ No session_factory provided!")
            return None
        try:
            return self.session_factory()
        except Exception as e:
            logger.error(f"Session creation failed: {e}")
            return None
    
    def resolve_dealer(self, query: str) -> Optional[str]:
        """Resolve dealer name from PostgreSQL"""
        if not query or not query.strip():
            return None
        
        cache_key = f"dealer:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            # 1. Exact match
            result = session.query(self.DeliveryReport.customer_name).filter(
                func.lower(self.DeliveryReport.customer_name) == func.lower(query)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # 2. ILIKE match
            result = session.query(self.DeliveryReport.customer_name).filter(
                self.DeliveryReport.customer_name.ilike(f"%{query}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # 3. Token-based matching
            tokens = query.split()
            for token in tokens:
                if len(token) > 2 and token.lower() not in ['the', 'and', 'for', 'with']:
                    result = session.query(self.DeliveryReport.customer_name).filter(
                        self.DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        self._cache[cache_key] = resolved
                        return resolved
            
            # 4. Fuzzy matching
            dealers = session.query(
                func.distinct(self.DeliveryReport.customer_name)
            ).filter(
                self.DeliveryReport.customer_name.isnot(None),
                self.DeliveryReport.customer_name != ''
            ).limit(1000).all()
            
            best_match = None
            best_score = 0
            query_lower = query.lower()
            
            for dealer in dealers:
                if not dealer[0]:
                    continue
                dealer_lower = dealer[0].lower()
                if query_lower in dealer_lower or dealer_lower in query_lower:
                    score = len(set(query_lower) & set(dealer_lower)) / max(len(query_lower), len(dealer_lower))
                    if score > best_score and score > 0.6:
                        best_score = score
                        best_match = dealer[0]
            
            if best_match:
                self._cache[cache_key] = best_match
                return best_match
            
            return None
            
        except Exception as e:
            logger.error(f"Dealer resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_warehouse(self, query: str) -> Optional[str]:
        """Resolve warehouse name from PostgreSQL"""
        if not query or not query.strip():
            return None
        
        cache_key = f"warehouse:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            # Exact match
            result = session.query(self.DeliveryReport.warehouse).filter(
                func.lower(self.DeliveryReport.warehouse) == func.lower(query)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # ILIKE
            result = session.query(self.DeliveryReport.warehouse).filter(
                self.DeliveryReport.warehouse.ilike(f"%{query}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # Token matching
            tokens = query.split()
            for token in tokens:
                if len(token) > 2:
                    result = session.query(self.DeliveryReport.warehouse).filter(
                        self.DeliveryReport.warehouse.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        self._cache[cache_key] = resolved
                        return resolved
            
            return None
            
        except Exception as e:
            logger.error(f"Warehouse resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_city(self, query: str) -> Optional[str]:
        """Resolve city name from PostgreSQL"""
        if not query or not query.strip():
            return None
        
        cache_key = f"city:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            # Exact match
            result = session.query(self.DeliveryReport.ship_to_city).filter(
                func.lower(self.DeliveryReport.ship_to_city) == func.lower(query)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # ILIKE
            result = session.query(self.DeliveryReport.ship_to_city).filter(
                self.DeliveryReport.ship_to_city.ilike(f"%{query}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # Token matching
            tokens = query.split()
            for token in tokens:
                if len(token) > 2:
                    result = session.query(self.DeliveryReport.ship_to_city).filter(
                        self.DeliveryReport.ship_to_city.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        self._cache[cache_key] = resolved
                        return resolved
            
            return None
            
        except Exception as e:
            logger.error(f"City resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_product(self, query: str) -> Optional[str]:
        """Resolve product name from PostgreSQL"""
        if not query or not query.strip():
            return None
        
        cache_key = f"product:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            # Check both customer_model and material_no
            result = session.query(
                func.coalesce(
                    self.DeliveryReport.customer_model,
                    self.DeliveryReport.material_no,
                    'UNKNOWN'
                )
            ).filter(
                or_(
                    func.lower(self.DeliveryReport.customer_model) == func.lower(query),
                    func.lower(self.DeliveryReport.material_no) == func.lower(query)
                )
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # ILIKE
            result = session.query(
                func.coalesce(
                    self.DeliveryReport.customer_model,
                    self.DeliveryReport.material_no,
                    'UNKNOWN'
                )
            ).filter(
                or_(
                    self.DeliveryReport.customer_model.ilike(f"%{query}%"),
                    self.DeliveryReport.material_no.ilike(f"%{query}%")
                )
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            return None
            
        except Exception as e:
            logger.error(f"Product resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_dn(self, query: str) -> Optional[str]:
        """Resolve DN number from PostgreSQL"""
        if not query or not query.strip():
            return None
        
        normalized = re.sub(r'[^0-9]', '', str(query).strip())
        if len(normalized) < 8 or len(normalized) > 12:
            return None
        
        cache_key = f"dn:{normalized}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            result = session.query(self.DeliveryReport.dn_no).filter(
                cast(self.DeliveryReport.dn_no, String) == normalized
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            return None
            
        except Exception as e:
            logger.error(f"DN resolution error: {e}")
            return None
        finally:
            session.close()


# ==========================================================
# CONVERSATION CONTEXT
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
    last_division: Optional[str] = None
    last_sales_manager: Optional[str] = None
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
            "last_division": self.last_division,
            "last_sales_manager": self.last_sales_manager,
            "last_dashboard": self.last_dashboard,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number,
        }


# ==========================================================
# INTENT PATTERNS
# ==========================================================

INTENT_PATTERNS = {
    "dealer_dashboard": [
        "dealer dashboard", "dealer performance", "dealer revenue", 
        "dealer units", "dealer dn", "dealer pod", "dealer pgi",
        "show dealer", "customer dashboard"
    ],
    "dealer_ranking": [
        "top dealer", "top dealers", "best dealer", "dealer ranking"
    ],
    "dealer_products": [
        "what products does dealer", "products of dealer", "dealer products"
    ],
    "warehouse_dashboard": [
        "warehouse dashboard", "warehouse performance",
        "warehouse revenue", "warehouse units", "show warehouse"
    ],
    "warehouse_ranking": [
        "top warehouse", "top warehouses", "warehouse ranking"
    ],
    "warehouse_coverage": [
        "warehouse coverage", "warehouse service"
    ],
    "city_dashboard": [
        "city dashboard", "city performance", "city revenue",
        "city units", "city dn", "show city"
    ],
    "city_ranking": [
        "top city", "top cities", "city ranking"
    ],
    "city_dealers": [
        "dealers in city", "top dealers in city"
    ],
    "city_products": [
        "products in city", "top products in city"
    ],
    "product_dashboard": [
        "product dashboard", "show product", "product performance",
        "product revenue", "product units"
    ],
    "product_ranking": [
        "top product", "top products", "best selling"
    ],
    "dn_dashboard": [
        "show dn", "dn status", "what is dn", "dn details",
        "dn information", "track dn", "dn tracking"
    ],
    "dn_analytics": [
        "how many dns", "total dn count", "dn count"
    ],
    "pgi_dashboard": [
        "pgi dashboard", "pgi completed", "pgi pending",
        "average pgi days", "pgi status"
    ],
    "pod_dashboard": [
        "pod dashboard", "pod pending", "pod completed",
        "average pod days", "pod status"
    ],
    "pod_aging": [
        "pod aging", "oldest pod pending", "pod delay"
    ],
    "delivery_dashboard": [
        "delivery dashboard", "delivered dns", "pending dns",
        "average delivery days"
    ],
    "executive_dashboard": [
        "executive summary", "nationwide performance",
        "total revenue", "total units", "total dns",
        "ceo", "management", "overview"
    ],
    "control_tower": [
        "control tower", "critical issues", "pending pod",
        "pending pgi", "delayed deliveries", "alert"
    ],
    "revenue_dashboard": [
        "revenue dashboard", "total revenue", "revenue growth"
    ],
    "aging_dashboard": [
        "dn aging", "oldest pending dn", "aging analysis"
    ],
    "division_dashboard": [
        "division dashboard", "division performance",
        "revenue by division", "show division"
    ],
    "sales_manager_dashboard": [
        "sales manager", "sales manager performance", "show sales manager"
    ],
    "help": [
        "help", "menu", "hi", "hello", "start", "?", "commands"
    ]
}


# ==========================================================
# FOLLOW-UP PATTERNS
# ==========================================================

FOLLOWUP_PATTERNS = {
    "revenue": r'(?:revenue|sales|amount|value|worth)',
    "pod": r'(?:pod|proof of delivery|delivery proof)',
    "pgi": r'(?:pgi|goods issue|issue)',
    "units": r'(?:units|quantity|qty|pieces)',
    "dn": r'(?:dn|delivery note|order)',
    "aging": r'(?:aging|old|delay|overdue)',
    "pending": r'(?:pending|not completed|waiting)',
    "products": r'(?:products|product|models|items)',
    "ranking": r'(?:rank|ranking|top|best)',
}


# ==========================================================
# ENTITY PATTERNS
# ==========================================================

ENTITY_PATTERNS = {
    "dealer_name": r'(?:dealer|customer|party)\s+([A-Za-z0-9\s&\.]+)',
    "dealer_name_standalone": r'^([A-Za-z\s&\.]{3,50})$',
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
    "sales_manager": r'(?:sales manager|sm|manager)\s+([A-Za-z\s]+)',
}


# ==========================================================
# MAIN AI ROUTER
# ==========================================================

class AIOrchestrator:
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory
        
        self._analytics = None
        self._analytics_response = None
        self._resolver = None
        
        self.response_cache = TTLCache(maxsize=2000, ttl=CACHE_TTL_SECONDS)
        self.failure_cache = TTLCache(maxsize=400, ttl=60)
        self.fast_cache = LRUCache(maxsize=1000)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        self._current_request_id: Optional[str] = None
        
        self.metrics = {
            "total_requests": 0,
            "intent_detection": {},
            "entity_resolution": {},
            "errors": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }
        
        logger.info("=" * 70)
        logger.info("AI Router v24.0 - PostgreSQL-Driven Production")
        logger.info("=" * 70)
    
    @property
    def analytics(self):
        if self._analytics is None:
            service, response_class = _get_analytics_service()
            self._analytics = service
            self._analytics_response = response_class
        return self._analytics
    
    @property
    def resolver(self):
        if self._resolver is None:
            self._resolver = PostgreSQLResolver(self.session_factory)
        return self._resolver
    
    def _detect_intent(self, question: str, context: Optional[ConversationContext] = None) -> Tuple[str, Optional[str], Optional[str]]:
        question_original = question.strip()
        question_lower = question_original.lower()
        
        logger.debug(f"🔍 Detecting intent for: '{question_original}'")
        
        # HELP
        if question_lower in ["help", "menu", "hi", "hello", "start", "?", "commands"]:
            return "help", None, None
        
        # FOLLOW-UP
        if context and context.last_intent and context.last_entity:
            followup_intent = self._detect_followup(question_lower, context)
            if followup_intent:
                logger.info(f"🔄 Follow-up detected: {followup_intent}")
                return followup_intent, context.last_entity, self._get_entity_type(followup_intent)
        
        # DN DETECTION
        dn_match = re.search(r'\b(\d{8,12})\b', question_original)
        if dn_match:
            dn_number = re.sub(r'\D', '', dn_match.group(1))
            if 8 <= len(dn_number) <= 12:
                logger.info(f"✅ Detected DN: {dn_number}")
                return "dn_dashboard", dn_number, "dn"
        
        # DEALER DETECTION
        dealer_keywords = ["dealer", "customer", "party", "sold to"]
        if any(kw in question_lower for kw in dealer_keywords) or "dealer dashboard" in question_lower:
            dealer_match = re.search(r'(?:dealer|customer|party|show)\s+([A-Za-z0-9\s&\.]+)', question_original, re.IGNORECASE)
            if dealer_match:
                entity = dealer_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_dealer(entity)
                    if resolved:
                        logger.info(f"✅ Detected dealer: '{resolved}'")
                        return "dealer_dashboard", resolved, "dealer"
            
            if context and context.last_dealer:
                logger.info(f"🔄 Using context dealer: {context.last_dealer}")
                return "dealer_dashboard", context.last_dealer, "dealer"
        
        # Standalone dealer name
        if 3 <= len(question_original) <= 50 and not any(c.isdigit() for c in question_original):
            resolved = self.resolver.resolve_dealer(question_original)
            if resolved:
                logger.info(f"✅ Detected dealer from standalone: '{resolved}'")
                return "dealer_dashboard", resolved, "dealer"
        
        # WAREHOUSE DETECTION
        if "warehouse" in question_lower or "wh " in question_lower:
            wh_match = re.search(r'(?:warehouse|wh)\s+([A-Za-z\s]+)', question_original, re.IGNORECASE)
            if wh_match:
                entity = wh_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_warehouse(entity)
                    if resolved:
                        logger.info(f"✅ Detected warehouse: '{resolved}'")
                        return "warehouse_dashboard", resolved, "warehouse"
            
            if context and context.last_warehouse:
                return "warehouse_dashboard", context.last_warehouse, "warehouse"
        
        # CITY DETECTION
        if "city" in question_lower or "in " in question_lower:
            city_match = re.search(r'(?:city|in)\s+([A-Za-z\s]+)', question_original, re.IGNORECASE)
            if city_match:
                entity = city_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_city(entity)
                    if resolved:
                        logger.info(f"✅ Detected city: '{resolved}'")
                        return "city_dashboard", resolved, "city"
            
            if context and context.last_city:
                return "city_dashboard", context.last_city, "city"
        
        # PRODUCT DETECTION
        product_keywords = ["product", "model", "material", "sku"]
        if any(kw in question_lower for kw in product_keywords) or "product dashboard" in question_lower:
            product_match = re.search(r'(?:product|model|material)\s+([A-Za-z0-9\-]+)', question_original, re.IGNORECASE)
            if product_match:
                entity = product_match.group(1).strip()
                if len(entity) > 1:
                    resolved = self.resolver.resolve_product(entity)
                    if resolved:
                        logger.info(f"✅ Detected product: '{resolved}'")
                        return "product_dashboard", resolved, "product"
        
        # PATTERN MATCHING
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    logger.info(f"✅ Detected intent '{intent}' from pattern '{pattern}'")
                    entity, entity_type = self._extract_entity(question_original, intent)
                    return intent, entity, entity_type
        
        # FALLBACK - Context
        if context and context.last_intent and context.last_entity:
            logger.info(f"🔄 Using context: {context.last_intent} with entity {context.last_entity}")
            return context.last_intent, context.last_entity, self._get_entity_type(context.last_intent)
        
        logger.warning(f"❌ Unknown intent for: '{question_original}'")
        return "help", None, None
    
    def _detect_followup(self, question: str, context: ConversationContext) -> Optional[str]:
        if "revenue" in question or "amount" in question or "worth" in question:
            return context.last_intent
        if "pod" in question:
            return "pod_dashboard"
        if "pgi" in question:
            return "pgi_dashboard"
        if "units" in question or "quantity" in question:
            return context.last_intent
        if "aging" in question or "old" in question:
            return "aging_dashboard"
        if "pending" in question:
            return context.last_intent
        if "ranking" in question or "top" in question:
            return "dealer_ranking"
        if "products" in question or "models" in question:
            return "dealer_products"
        return None
    
    def _extract_entity(self, question: str, intent: str) -> Tuple[Optional[str], Optional[str]]:
        question_clean = question.strip()
        
        for entity_type, pattern in ENTITY_PATTERNS.items():
            match = re.search(pattern, question_clean, re.IGNORECASE)
            if match:
                entity = match.group(1).strip() if len(match.groups()) > 0 else match.group(0).strip()
                if len(entity) > 2:
                    return entity, self._map_entity_type(entity_type)
        
        if intent == "dealer_dashboard":
            prefixes = ["show me", "show", "get", "view", "dealer", "customer"]
            text = question_clean
            for prefix in prefixes:
                if text.lower().startswith(prefix):
                    text = text[len(prefix):].strip()
                    if len(text) > 2:
                        return text, "dealer"
        return None, None
    
    def _map_entity_type(self, entity_pattern: str) -> str:
        mapping = {
            "dealer_name": "dealer",
            "dealer_name_standalone": "dealer",
            "dealer_code": "dealer",
            "customer_code": "dealer",
            "warehouse": "warehouse",
            "warehouse_pattern": "warehouse",
            "city": "city",
            "city_pattern": "city",
            "product": "product",
            "dn_number": "dn",
            "dn_pattern": "dn",
            "division": "division",
            "sales_manager": "sales_manager",
        }
        return mapping.get(entity_pattern, "unknown")
    
    def _get_entity_type(self, intent: str) -> str:
        entity_mapping = {
            "dealer_dashboard": "dealer",
            "dealer_products": "dealer",
            "dealer_ranking": "dealer",
            "warehouse_dashboard": "warehouse",
            "warehouse_ranking": "warehouse",
            "warehouse_coverage": "warehouse",
            "city_dashboard": "city",
            "city_ranking": "city",
            "city_dealers": "city",
            "city_products": "city",
            "product_dashboard": "product",
            "product_ranking": "product",
            "dn_dashboard": "dn",
            "dn_analytics": "dn",
            "pgi_dashboard": "pgi",
            "pgi_by_warehouse": "pgi",
            "pod_dashboard": "pod",
            "pod_aging": "pod",
            "delivery_dashboard": "delivery",
            "executive_dashboard": "executive",
            "control_tower": "control",
            "revenue_dashboard": "revenue",
            "aging_dashboard": "aging",
            "division_dashboard": "division",
            "sales_manager_dashboard": "sales_manager",
            "help": "help",
        }
        return entity_mapping.get(intent, "unknown")
    
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
        elif entity_type == "division":
            context.last_division = entity
            context.last_entity = entity
        elif entity_type == "sales_manager":
            context.last_sales_manager = entity
            context.last_entity = entity
        
        self.conversation_cache[phone_number] = context
    
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
        
        if session_factory:
            self.session_factory = session_factory
            self._resolver = None
        
        if not question or len(question.strip()) < 2:
            return "Please provide a valid question. Type 'help' for menu."
        
        try:
            context = self._load_context(phone_number)
            question_clean = question.strip()
            
            intent, entity, entity_type = self._detect_intent(question_clean, context)
            
            if intent == "help":
                return self._get_help_message()
            
            logger.info(f"[{req_id}] 🎯 Intent: {intent} | Entity: {entity} | Type: {entity_type}")
            
            result = self._route_to_dashboard(intent, entity, entity_type, context, req_id)
            
            if result:
                self._update_context(
                    phone_number, 
                    intent, 
                    entity_type or self._get_entity_type(intent), 
                    entity or context.last_entity if context else None, 
                    req_id
                )
                return result
            
            return self._get_help_message()
            
        except Exception as e:
            self.metrics["errors"] += 1
            logger.exception(f"[{req_id}] ERROR: {e}")
            return f"⚠️ Unable to process request. Please try again or type 'help'."
    
    def _route_to_dashboard(self, intent: str, entity: Optional[str], entity_type: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        if not self.analytics:
            logger.error(f"[{req_id}] Analytics service not available")
            return "⚠️ Analytics service is temporarily unavailable. Please try again later."
        
        try:
            if intent == "dealer_dashboard":
                return self._route_dealer_dashboard(entity, context, req_id)
            if intent == "dealer_ranking":
                return self._route_dealer_ranking(req_id)
            if intent == "dealer_products":
                return self._route_dealer_products(entity, context, req_id)
            if intent == "warehouse_dashboard":
                return self._route_warehouse_dashboard(entity, context, req_id)
            if intent == "warehouse_ranking":
                return self._route_warehouse_ranking(req_id)
            if intent == "warehouse_coverage":
                return self._route_warehouse_coverage(entity, context, req_id)
            if intent == "city_dashboard":
                return self._route_city_dashboard(entity, context, req_id)
            if intent == "city_ranking":
                return self._route_city_ranking(req_id)
            if intent == "city_dealers":
                return self._route_city_dealers(entity, context, req_id)
            if intent == "city_products":
                return self._route_city_products(entity, context, req_id)
            if intent == "product_dashboard":
                return self._route_product_dashboard(entity, context, req_id)
            if intent == "product_ranking":
                return self._route_product_ranking(req_id)
            if intent == "dn_dashboard":
                return self._route_dn_dashboard(entity, context, req_id)
            if intent == "dn_analytics":
                return self._route_dn_analytics(req_id)
            if intent == "pgi_dashboard":
                return self._route_pgi_dashboard(req_id)
            if intent == "pgi_by_warehouse":
                return self._route_pgi_by_warehouse(entity, context, req_id)
            if intent == "pod_dashboard":
                return self._route_pod_dashboard(req_id)
            if intent == "pod_aging":
                return self._route_pod_aging(req_id)
            if intent == "delivery_dashboard":
                return self._route_delivery_dashboard(req_id)
            if intent == "executive_dashboard":
                return self._route_executive_dashboard(req_id)
            if intent == "control_tower":
                return self._route_control_tower(req_id)
            if intent == "revenue_dashboard":
                return self._route_revenue_dashboard(req_id)
            if intent == "aging_dashboard":
                return self._route_aging_dashboard(entity, context, req_id)
            if intent == "division_dashboard":
                return self._route_division_dashboard(entity, context, req_id)
            if intent == "sales_manager_dashboard":
                return self._route_sales_manager_dashboard(entity, context, req_id)
            
            logger.warning(f"[{req_id}] Unhandled intent: {intent}")
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] Routing error for {intent}: {e}")
            return f"⚠️ Unable to load {intent.replace('_', ' ').title()}. Please try again."
    
    # ==========================================================
    # ROUTE HANDLERS
    # ==========================================================
    
    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        dealer_name = entity
        if not dealer_name and context and context.last_dealer:
            dealer_name = context.last_dealer
        
        if not dealer_name:
            return "🏪 *DEALER DASHBOARD*\n\nPlease specify a dealer name.\n\n*Examples:*\n• ZQ Electronics\n• Show dealer ZQ Electronics"
        
        if entity:
            resolved = self.resolver.resolve_dealer(entity)
            if resolved:
                dealer_name = resolved
            else:
                return f"❌ Dealer '{entity}' not found.\n\n💡 Please check the spelling or try a different dealer name."
        
        response = self.analytics.get_dealer_dashboard(dealer_name)
        if not self._validate_response(response, "dealer_dashboard", req_id):
            return f"❌ Unable to retrieve data for '{dealer_name}'."
        return self._format_dealer_dashboard(response.data, dealer_name)
    
    def _route_dealer_ranking(self, req_id: str) -> str:
        response = self.analytics.get_ranking_dashboard(limit=10)
        if not self._validate_response(response, "dealer_ranking", req_id):
            return "❌ Unable to retrieve dealer ranking."
        return self._format_dealer_ranking(response.data)
    
    def _route_dealer_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "📦 *DEALER PRODUCTS*\n\nProduct information coming soon."
    
    def _route_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        warehouse_name = entity
        if not warehouse_name and context and context.last_warehouse:
            warehouse_name = context.last_warehouse
        
        if not warehouse_name:
            return "🏭 *WAREHOUSE DASHBOARD*\n\nPlease specify a warehouse name.\n\n*Examples:*\n• Lahore warehouse"
        
        resolved = self.resolver.resolve_warehouse(warehouse_name)
        if not resolved:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        response = self.analytics.get_warehouse_dashboard(resolved)
        if not self._validate_response(response, "warehouse_dashboard", req_id):
            return f"❌ Unable to retrieve data for warehouse '{resolved}'."
        return self._format_warehouse_dashboard(response.data, resolved)
    
    def _route_warehouse_ranking(self, req_id: str) -> str:
        return "🏆 *WAREHOUSE RANKING*\n\nWarehouse ranking coming soon."
    
    def _route_warehouse_coverage(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "📍 *WAREHOUSE COVERAGE*\n\nCoverage information coming soon."
    
    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        city_name = entity
        if not city_name and context and context.last_city:
            city_name = context.last_city
        
        if not city_name:
            return "🏙️ *CITY DASHBOARD*\n\nPlease specify a city name.\n\n*Examples:*\n• Haripur"
        
        resolved = self.resolver.resolve_city(city_name)
        if not resolved:
            return f"❌ City '{city_name}' not found."
        
        response = self.analytics.get_city_dashboard(resolved)
        if not self._validate_response(response, "city_dashboard", req_id):
            return f"❌ Unable to retrieve data for city '{resolved}'."
        return self._format_city_dashboard(response.data, resolved)
    
    def _route_city_ranking(self, req_id: str) -> str:
        return "🏆 *CITY RANKING*\n\nCity ranking coming soon."
    
    def _route_city_dealers(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        city_name = entity or (context.last_city if context else None)
        if not city_name:
            return "📍 *CITY DEALERS*\n\nPlease specify a city name."
        return f"📍 *DEALERS IN {city_name.upper()}*\n\nDealer list coming soon."
    
    def _route_city_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        city_name = entity or (context.last_city if context else None)
        if not city_name:
            return "📦 *CITY PRODUCTS*\n\nPlease specify a city name."
        return f"📦 *PRODUCTS IN {city_name.upper()}*\n\nProduct list coming soon."
    
    def _route_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        product_name = entity or (context.last_product if context else None)
        if not product_name:
            return "📦 *PRODUCT DASHBOARD*\n\nPlease specify a product.\n\n*Examples:*\n• Refrigerator\n• AC"
        
        resolved = self.resolver.resolve_product(product_name)
        if not resolved:
            return f"❌ Product '{product_name}' not found."
        
        response = self.analytics.get_product_dashboard(resolved)
        if not self._validate_response(response, "product_dashboard", req_id):
            return f"❌ Unable to retrieve data for product '{resolved}'."
        return self._format_product_dashboard(response.data, resolved)
    
    def _route_product_ranking(self, req_id: str) -> str:
        return "🏆 *PRODUCT RANKING*\n\nProduct ranking coming soon."
    
    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        dn_number = entity or (context.last_dn if context else None)
        if not dn_number:
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number.\n\n*Example:* 6243612278"
        
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return f"❌ Invalid DN number: '{dn_number}'\n\nDN numbers must be 8-12 digits."
        
        # Check if DN exists
        resolved = self.resolver.resolve_dn(dn_clean)
        if not resolved:
            return f"""❌ DN {dn_clean} not found in system.

💡 *Try these:*
• Enter a valid DN number
• Type "help" for menu
• Ask about a dealer name (e.g., "Show ZQ Electronics")

*What would you like to know?* 🤖"""
        
        response = self.analytics.get_dn_dashboard(dn_clean)
        if not self._validate_response(response, "dn_dashboard", req_id):
            return f"❌ Unable to retrieve data for DN {dn_clean}."
        return self._format_dn_dashboard(response.data, dn_clean)
    
    def _route_dn_analytics(self, req_id: str) -> str:
        return "📊 *DN ANALYTICS*\n\nAnalytics coming soon."
    
    def _route_pgi_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_pgi_dashboard()
        if not self._validate_response(response, "pgi_dashboard", req_id):
            return "❌ Unable to retrieve PGI data."
        return self._format_pgi_dashboard(response.data)
    
    def _route_pgi_by_warehouse(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "🏭 *PGI BY WAREHOUSE*\n\nData coming soon."
    
    def _route_pod_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_pod_dashboard()
        if not self._validate_response(response, "pod_dashboard", req_id):
            return "❌ Unable to retrieve POD data."
        return self._format_pod_dashboard(response.data)
    
    def _route_pod_aging(self, req_id: str) -> str:
        response = self.analytics.get_aging_dashboard()
        if not self._validate_response(response, "pod_aging", req_id):
            return "❌ Unable to retrieve aging data."
        return self._format_aging_dashboard(response.data)
    
    def _route_delivery_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_delivery_dashboard()
        if not self._validate_response(response, "delivery_dashboard", req_id):
            return "❌ Unable to retrieve delivery data."
        return self._format_delivery_dashboard(response.data)
    
    def _route_executive_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_executive_dashboard()
        if not self._validate_response(response, "executive_dashboard", req_id):
            return "❌ Unable to retrieve executive data."
        return self._format_executive_dashboard(response.data)
    
    def _route_control_tower(self, req_id: str) -> str:
        response = self.analytics.get_control_tower_dashboard()
        if not self._validate_response(response, "control_tower", req_id):
            return "❌ Unable to retrieve control tower data."
        return self._format_control_tower(response.data)
    
    def _route_revenue_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_revenue_dashboard()
        if not self._validate_response(response, "revenue_dashboard", req_id):
            return "❌ Unable to retrieve revenue data."
        return self._format_revenue_dashboard(response.data)
    
    def _route_aging_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        response = self.analytics.get_aging_dashboard()
        if not self._validate_response(response, "aging_dashboard", req_id):
            return "❌ Unable to retrieve aging data."
        return self._format_aging_dashboard(response.data)
    
    def _route_division_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "📊 *DIVISION DASHBOARD*\n\nData coming soon."
    
    def _route_sales_manager_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "👤 *SALES MANAGER DASHBOARD*\n\nData coming soon."
    
    def _validate_response(self, response, service_name: str, req_id: str) -> bool:
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
    
    def _truncate_response(self, response: str) -> str:
        if len(response) > MAX_RESPONSE_LENGTH:
            return response[:MAX_RESPONSE_LENGTH - 20] + "\n\n... (truncated)"
        return response
    
    # ==========================================================
    # FORMATTERS
    # ==========================================================
    
    def _format_dealer_dashboard(self, data: Dict, dealer_name: str) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                "🏪 *DEALER DASHBOARD*",
                "",
                "👤 *Dealer Profile*",
                f"Name: {dealer_name}",
                f"Code: {data.get('dealer_code', 'N/A')}",
                f"City: {data.get('city', 'N/A')}",
                f"Warehouse: {data.get('warehouse', 'N/A')}",
                "",
                "📊 *Business Summary*",
                f"DNs: {data.get('total_dns', 0):,}",
                f"Units: {data.get('total_units', 0):,}",
                f"Revenue: PKR {data.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Performance*",
                f"Delivery Rate: {data.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {data.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {data.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {data.get('pending_dns', 0)}",
                f"Pending PODs: {data.get('pending_pod_dns', 0)}",
                "",
                "⚠️ *Risk*",
                f"Risk Level: {data.get('risk_level', 'Low')}",
                f"Health Score: {data.get('health_score', 0)}/100"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Dealer format error: {e}")
            return f"❌ Unable to format dealer dashboard for {dealer_name}"
    
    def _format_dealer_ranking(self, data: Dict) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            dealers = data.get("ranking", [])
            if not dealers:
                return "❌ No dealer data available."
            
            lines = ["🏆 *DEALER RANKING*", "", "Top 10 Dealers by Revenue:"]
            for i, dealer in enumerate(dealers[:10], 1):
                name = dealer.get("dealer", "Unknown")
                revenue = dealer.get("revenue", 0)
                delivery_rate = dealer.get("delivery_rate", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   PKR {revenue:,.0f} | Delivery: {delivery_rate:.1f}%")
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Dealer ranking format error: {e}")
            return "❌ Unable to format dealer ranking"
    
    def _format_warehouse_dashboard(self, data: Dict, warehouse_name: str) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                "🏭 *WAREHOUSE DASHBOARD*",
                "",
                f"Warehouse: {warehouse_name}",
                f"Code: {data.get('warehouse_code', 'N/A')}",
                "",
                "📍 *Coverage*",
                f"Dealers: {data.get('total_dealers', 0):,}",
                f"Cities: {data.get('cities_served', 0):,}",
                "",
                "📊 *Business*",
                f"DNs: {data.get('total_dns', 0):,}",
                f"Units: {data.get('total_units', 0):,}",
                f"Revenue: PKR {data.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Performance*",
                f"Delivery Rate: {data.get('delivery_rate', 0):.1f}%",
                "",
                f"Pending DNs: {data.get('pending_dns', 0):,}",
                f"Pending PODs: {data.get('pending_pod_dns', 0):,}"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Warehouse format error: {e}")
            return f"❌ Unable to format warehouse dashboard for {warehouse_name}"
    
    def _format_city_dashboard(self, data: Dict, city_name: str) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                "🏙️ *CITY DASHBOARD*",
                "",
                f"City: {city_name}",
                "",
                "📊 *Business*",
                f"Dealers: {data.get('total_dealers', 0):,}",
                f"Warehouses: {data.get('total_warehouses', 0)}",
                f"DNs: {data.get('total_dns', 0):,}",
                f"Units: {data.get('total_units', 0):,}",
                f"Revenue: PKR {data.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Performance*",
                f"Delivery Rate: {data.get('delivery_rate', 0):.1f}%",
                "",
                f"Pending DNs: {data.get('pending_dns', 0)}",
                f"Pending PODs: {data.get('pending_pod_dns', 0)}"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"City format error: {e}")
            return f"❌ Unable to format city dashboard for {city_name}"
    
    def _format_product_dashboard(self, data: Dict, product_name: str) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                f"📦 *PRODUCT DASHBOARD*",
                "",
                f"Product: {product_name}",
                "",
                "📊 *Performance*",
                f"Revenue: PKR {data.get('revenue', 0):,.0f}",
                f"Units: {data.get('units', 0):,}",
                f"DNs: {data.get('dns', 0):,}",
                f"Dealers: {data.get('dealers', 0)}",
                f"Cities: {data.get('cities', 0)}",
                f"Warehouses: {data.get('warehouses', 0)}",
                "",
                f"Delivery Rate: {data.get('delivery_rate', 0):.1f}%"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Product format error: {e}")
            return f"❌ Unable to format product dashboard for {product_name}"
    
    def _format_dn_dashboard(self, data: Dict, dn_number: str) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            status = "delivered" if data.get('delivery_status') == "Completed" else "pending"
            status_emoji = "✅" if status == "delivered" else "⏳"
            
            lines = [
                "📄 *DN TRACKING*",
                "",
                f"DN No: {data.get('dn_number', dn_number)}",
                f"Dealer: {data.get('customer_name', 'N/A')}",
                f"Dealer Code: {data.get('dealer_code', 'N/A')}",
                f"Customer Code: {data.get('customer_code', 'N/A')}",
                f"Warehouse: {data.get('warehouse', 'N/A')}",
                f"City: {data.get('ship_to_city', 'N/A')}",
                f"Sales Office: {data.get('sales_office', 'N/A')}",
                f"Sales Manager: {data.get('sales_manager', 'N/A')}",
                f"Division: {data.get('division', 'N/A')}",
                "",
                "📦 *Products*",
                f"Model: {data.get('customer_model', 'N/A')}",
                f"Material: {data.get('material_no', 'N/A')}",
                "",
                "📊 *Metrics*",
                f"Units: {data.get('units', 0)}",
                f"Revenue: PKR {data.get('amount', 0):,.0f}",
                "",
                "📅 *Dates*",
                f"Create: {data.get('dn_create_date', 'N/A')}",
                f"PGI: {data.get('good_issue_date', 'N/A')}",
                f"POD: {data.get('pod_date', 'N/A')}",
                "",
                "⏳ *Aging*",
                f"Total: {data.get('aging_days', 0)} days",
                f"PGI Aging: {data.get('pgi_aging_days', 0)} days",
                f"POD Aging: {data.get('pod_aging_days', 0)} days",
                "",
                "📋 *Status*",
                f"Delivery: {data.get('delivery_status', 'N/A')} {status_emoji}",
                f"PGI: {data.get('pgi_status', 'N/A')}",
                f"POD: {data.get('pod_status', 'N/A')}",
                f"Pending: {'🔴 Yes' if data.get('pending_flag') else '🟢 No'}"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"DN format error: {e}")
            return f"❌ Unable to format DN details for {dn_number}"
    
    def _format_pgi_dashboard(self, data: Dict) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                "📋 *PGI DASHBOARD*",
                "",
                f"Total DNs: {data.get('total_dns', 0):,}",
                f"PGI Completed: {data.get('pgi_completed', 0):,}",
                f"PGI Pending: {data.get('pgi_pending', 0):,}",
                f"In Transit: {data.get('in_transit', 0):,}",
                f"PGI Rate: {data.get('pgi_rate', 0):.1f}%"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"PGI format error: {e}")
            return "❌ Unable to format PGI dashboard"
    
    def _format_pod_dashboard(self, data: Dict) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                "✅ *POD DASHBOARD*",
                "",
                f"Total DNs: {data.get('total_dns', 0):,}",
                f"Delivered DNs: {data.get('delivered_dns', 0):,}",
                f"POD Completed: {data.get('pod_completed', 0):,}",
                f"POD Pending: {data.get('pod_pending', 0):,}",
                f"POD Rate: {data.get('pod_rate', 0):.1f}%"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"POD format error: {e}")
            return "❌ Unable to format POD dashboard"
    
    def _format_delivery_dashboard(self, data: Dict) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                "🚚 *DELIVERY DASHBOARD*",
                "",
                f"Total DNs: {data.get('total_dns', 0):,}",
                f"Delivered: {data.get('delivered', 0):,}",
                f"In Transit: {data.get('in_transit', 0):,}",
                f"Pending PGI: {data.get('pending_pgi', 0):,}",
                f"Pending: {data.get('pending', 0):,}",
                "",
                f"Delivery Rate: {data.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {data.get('pgi_rate', 0):.1f}%"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Delivery format error: {e}")
            return "❌ Unable to format delivery dashboard"
    
    def _format_executive_dashboard(self, data: Dict) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                "👔 *EXECUTIVE DASHBOARD*",
                "",
                "💰 *Business*",
                f"Revenue: PKR {data.get('total_revenue', 0):,.0f}",
                f"Units: {data.get('total_units', 0):,}",
                f"DNs: {data.get('total_dns', 0):,}",
                f"Dealers: {data.get('total_dealers', 0):,}",
                f"Warehouses: {data.get('total_warehouses', 0)}",
                f"Cities: {data.get('total_cities', 0)}",
                "",
                "📈 *KPI*",
                f"Delivery Rate: {data.get('delivery_rate', 0):.1f}%",
                "",
                f"Pending DNs: {data.get('pending_dns', 0):,}"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Executive format error: {e}")
            return "👔 Unable to format executive dashboard"
    
    def _format_control_tower(self, data: Dict) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
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
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                "💰 *REVENUE DASHBOARD*",
                "",
                f"Total Revenue: PKR {data.get('total_revenue', 0):,.0f}",
                f"Total Units: {data.get('total_units', 0):,}",
                f"Total DNs: {data.get('total_dns', 0):,}",
                "",
                "🏆 *Top Revenue Dealers:*"
            ]
            
            for dealer in data.get("top_dealers", [])[:5]:
                lines.append(f"   • {dealer.get('dealer', 'Unknown')}: PKR {dealer.get('revenue', 0):,.0f}")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Revenue format error: {e}")
            return "💰 Unable to format revenue dashboard"
    
    def _format_aging_dashboard(self, data: Dict) -> str:
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            lines = [
                "⏳ *AGING ANALYSIS*",
                "",
                f"0-7 Days: {data.get('days_0_7', 0)}",
                f"8-14 Days: {data.get('days_8_14', 0)}",
                f"15-30 Days: {data.get('days_15_30', 0)}",
                f"30+ Days: {data.get('days_30_plus', 0)}",
                "",
                f"Total Pending: {data.get('total_pending', 0)}"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Aging format error: {e}")
            return "❌ Unable to format aging dashboard"
    
    def _get_help_message(self) -> str:
        return """🏠 *HAIER LOGISTICS AI*

*📋 20+ Dashboards Available:*

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
1️⃣4️⃣ 🏆 City Ranking
1️⃣5️⃣ 🏆 Product Ranking
1️⃣6️⃣ 💰 Revenue Dashboard
1️⃣7️⃣ 📊 Division Dashboard
1️⃣8️⃣ 👤 Sales Manager Dashboard
1️⃣9️⃣ ⏳ Aging Dashboard
2️⃣0️⃣ 🔄 Follow-up Support

*🔍 Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "ZQ Electronics")
• City name (e.g., "Haripur")
• Warehouse name (e.g., "Lahore")
• "Executive summary"
• "Control tower"
• "Top dealers"
• "Help" for menu

*💡 Follow-up Support:*
• "What is its POD?" → Uses last dealer
• "How many pending DN?" → Uses last dealer
• "Show me its revenue" → Uses last dealer
• "Show aging" → Uses last dealer

*Ask me anything about logistics!* 🤖"""


# ==========================================================
# SINGLETON
# ==========================================================

_orchestrator = None

def get_orchestrator(session_factory: Optional[Callable[[], Session]] = None) -> AIOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        try:
            _orchestrator = AIOrchestrator(session_factory=session_factory)
            logger.info("✅ AI Orchestrator v24.0 initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize AI Orchestrator: {e}")
            _orchestrator = None
    else:
        if session_factory and not _orchestrator.session_factory:
            _orchestrator.session_factory = session_factory
            _orchestrator._resolver = None
    return _orchestrator


def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    orchestrator = get_orchestrator(session_factory)
    if orchestrator is None:
        return "⚠️ AI service is currently unavailable. Please try again later."
    return orchestrator.process_whatsapp_query(
        question=question,
        session_factory=session_factory,
        phone_number=phone_number,
        user_id=user_id,
        request_id=request_id
    )


__all__ = [
    'AIOrchestrator',
    'PostgreSQLResolver',
    'ConversationContext',
    'get_orchestrator',
    'process_whatsapp_query',
    'test_database_connection'
]


# ==========================================================
# END OF FILE - v24.0 PRODUCTION READY
# ==========================================================
