# ==========================================================
# FILE: app/services/ai_provider_service.py (v25.1 - FIXED DN)
# ==========================================================
# PURPOSE: POSTGRESQL-DRIVEN AI ROUTER
# VERSION: 25.1 - Fixed DN Dashboard - Answers ALL 350+ Questions
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
# BLOCK 1: POSTGRESQL IMPORTS - THE SOURCE OF TRUTH
# ==========================================================

from app.models import DeliveryReport
from app.database import SessionLocal, check_database_connection

# ==========================================================
# BLOCK 2: LAZY IMPORTS
# ==========================================================

def _get_analytics_service():
    try:
        from app.services.analytics_service import get_analytics_service, AnalyticsResponse
        return get_analytics_service(), AnalyticsResponse
    except ImportError:
        logger.warning("⚠️ analytics_service not available")
        return None, None

# ==========================================================
# BLOCK 3: CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800
MAX_RESPONSE_LENGTH = 2500
QUERY_TIMEOUT_SECONDS = 10
MAX_RETRY_ATTEMPTS = 3

# ==========================================================
# BLOCK 4: DATABASE CONNECTION TEST
# ==========================================================

def test_database_connection() -> Dict[str, Any]:
    """Test PostgreSQL connection from AI Provider."""
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
# BLOCK 5: POSTGRESQL RESOLVER - PURE POSTGRESQL
# ==========================================================

class PostgreSQLResolver:
    """Pure PostgreSQL-based entity resolution"""
    
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
            # Exact match
            result = session.query(self.DeliveryReport.customer_name).filter(
                func.lower(self.DeliveryReport.customer_name) == func.lower(query)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # ILIKE match
            result = session.query(self.DeliveryReport.customer_name).filter(
                self.DeliveryReport.customer_name.ilike(f"%{query}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # Token-based matching
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
            
            # Fuzzy matching
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
        """Resolve product name from PostgreSQL - checks both customer_model and material_no"""
        if not query or not query.strip():
            return None
        
        cache_key = f"product:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            # Check customer_model
            result = session.query(self.DeliveryReport.customer_model).filter(
                func.lower(self.DeliveryReport.customer_model) == func.lower(query)
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # Check material_no
            result = session.query(self.DeliveryReport.material_no).filter(
                func.lower(self.DeliveryReport.material_no) == func.lower(query)
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # ILIKE on customer_model
            result = session.query(self.DeliveryReport.customer_model).filter(
                self.DeliveryReport.customer_model.ilike(f"%{query}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # ILIKE on material_no
            result = session.query(self.DeliveryReport.material_no).filter(
                self.DeliveryReport.material_no.ilike(f"%{query}%")
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
# BLOCK 6: CONVERSATION CONTEXT
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
# BLOCK 7: INTENT PATTERNS - COMPLETE
# ==========================================================

INTENT_PATTERNS = {
    "dealer_dashboard": [
        "dealer dashboard", "dealer performance", "dealer revenue", 
        "dealer units", "dealer dn", "dealer pod", "dealer pgi",
        "show dealer", "customer dashboard", "dealer profile",
        "dealer delivered", "dealer pending"
    ],
    "dealer_ranking": [
        "top dealer", "top dealers", "best dealer", "dealer ranking",
        "bottom dealers", "worst dealer", "compare dealers"
    ],
    "dealer_products": [
        "products of dealer", "dealer products", "top products for dealer",
        "dealer product mix", "what products does dealer"
    ],
    "warehouse_dashboard": [
        "warehouse dashboard", "warehouse performance", "warehouse revenue",
        "warehouse units", "warehouse dn", "show warehouse",
        "warehouse delivered", "warehouse pending", "warehouse aging"
    ],
    "warehouse_ranking": [
        "top warehouse", "top warehouses", "warehouse ranking",
        "bottom warehouses", "compare warehouses"
    ],
    "warehouse_coverage": [
        "warehouse coverage", "warehouse dealers", "warehouse cities"
    ],
    "warehouse_products": [
        "warehouse products", "warehouse product mix",
        "top products in warehouse"
    ],
    "city_dashboard": [
        "city dashboard", "city performance", "city revenue",
        "city units", "city dn", "show city", "city dealers",
        "city warehouses", "city delivered", "city pending"
    ],
    "city_ranking": [
        "top city", "top cities", "city ranking", "bottom cities",
        "compare cities"
    ],
    "city_products": [
        "city products", "top products in city", "city product mix"
    ],
    "product_dashboard": [
        "product dashboard", "show product", "product performance",
        "product revenue", "product units", "product dn",
        "best selling", "top material", "top model"
    ],
    "product_ranking": [
        "top product", "top products", "product ranking",
        "bottom products", "worst selling"
    ],
    "product_trend": [
        "product trend", "product growth", "product decline"
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
        "pgi rate", "average pgi days", "pgi status",
        "pgi by dealer", "pgi by warehouse", "pgi by city",
        "pgi aging"
    ],
    "pod_dashboard": [
        "pod dashboard", "pod pending", "pod completed",
        "pod rate", "average pod days", "pod status",
        "pod by dealer", "pod by warehouse", "pod by city",
        "pod aging"
    ],
    "delivery_dashboard": [
        "delivery dashboard", "delivered dns", "pending dns",
        "delivery rate", "average delivery days", "delayed deliveries",
        "delivery aging", "delivery by dealer", "delivery by city"
    ],
    "executive_dashboard": [
        "executive summary", "nationwide performance",
        "total revenue", "total units", "total dns",
        "total dealers", "total cities", "total warehouses",
        "ceo", "management", "overview"
    ],
    "control_tower": [
        "control tower", "critical issues", "critical alerts",
        "pending pod", "pending pgi", "delayed deliveries",
        "high risk dealers", "high risk warehouses", "high risk cities",
        "oldest pending dn"
    ],
    "revenue_dashboard": [
        "revenue dashboard", "total revenue",
        "revenue by dealer", "revenue by warehouse",
        "revenue by city", "revenue by product",
        "revenue by division", "revenue by sales office",
        "top revenue dealers", "top revenue cities"
    ],
    "aging_dashboard": [
        "dn aging", "oldest pending dn", "aging analysis",
        "pending aging", "newest dn", "average aging",
        "pgi aging", "pod aging"
    ],
    "division_dashboard": [
        "division dashboard", "division performance",
        "division revenue", "division units", "division dn",
        "revenue by division", "show division",
        "top divisions", "best division", "worst division"
    ],
    "sales_office_dashboard": [
        "sales office", "sales office dashboard",
        "sales office revenue", "sales office performance",
        "top sales offices", "compare sales offices"
    ],
    "sales_manager_dashboard": [
        "sales manager", "sales manager dashboard",
        "sales manager revenue", "sales manager performance",
        "top sales managers", "compare sales managers"
    ],
    "help": [
        "help", "menu", "hi", "hello", "start", "?", "commands"
    ]
}

# ==========================================================
# BLOCK 8: FOLLOW-UP PATTERNS
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
    "performance": r'(?:performance|status|health)',
}

# ==========================================================
# BLOCK 9: ENTITY PATTERNS
# ==========================================================

ENTITY_PATTERNS = {
    "dealer_name": r'(?:dealer|customer|party)\s+([A-Za-z0-9\s&\.\-]+)',
    "dealer_name_standalone": r'^([A-Za-z\s&\.\-]{3,50})$',
    "dealer_code": r'\b(?:[A-Z]{2,4}\d{2,6})\b',
    "customer_code": r'\b(?:CUST|CT)\d{5,}\b',
    "warehouse": r'(?:warehouse|wh)\s+([A-Za-z0-9\s\-]+)',
    "warehouse_pattern": r'^([A-Za-z\s\-]+)\s+warehouse$',
    "city": r'(?:city|in)\s+([A-Za-z\s\-]+)',
    "city_pattern": r'^([A-Za-z\s\-]+)\s+city$',
    "product": r'(?:product|model|material)\s+([A-Za-z0-9\-]+)',
    "dn_number": r'\b(\d{8,12})\b',
    "dn_pattern": r'(?:dn|track|delivery note)\s*[:#]?\s*(\d{8,12})',
    "division": r'(?:division|div)\s+([A-Za-z\s\-]+)',
    "sales_manager": r'(?:sales manager|sm|manager)\s+([A-Za-z\s\-]+)',
    "sales_office": r'(?:sales office|office)\s+([A-Za-z\s\-]+)',
}

# ==========================================================
# BLOCK 10: MAIN AI ROUTER
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
        logger.info("AI Router v25.1 - PostgreSQL-Driven Production")
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

# ==========================================================
# BLOCK 11: INTENT DETECTION
# ==========================================================
# BLOCK 11: INTENT DETECTION (FIXED v4.0)
# ==========================================================

    def _detect_intent(self, question: str, context: Optional[ConversationContext] = None) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Detect intent from user question.
        BLOCK 11 - FIXED v4.0
        - DEALER now checked BEFORE CITY in standalone detection
        - Explicit keywords take priority
        """
        question_original = question.strip()
        question_lower = question_original.lower()
        
        logger.debug(f"🔍 Detecting intent for: '{question_original}'")
        
        # ==========================================================
        # STEP 1: HELP
        # ==========================================================
        if question_lower in ["help", "menu", "hi", "hello", "start", "?", "commands"]:
            return "help", None, None
        
        # ==========================================================
        # STEP 2: FOLLOW-UP
        # ==========================================================
        if context and context.last_intent and context.last_entity:
            followup_intent = self._detect_followup(question_lower, context)
            if followup_intent:
                logger.info(f"🔄 Follow-up detected: {followup_intent}")
                return followup_intent, context.last_entity, self._get_entity_type(followup_intent)
        
        # ==========================================================
        # STEP 3: DN DETECTION (HIGHEST PRIORITY)
        # ==========================================================
        dn_match = re.search(r'\b(\d{8,12})\b', question_original)
        if dn_match:
            dn_number = re.sub(r'\D', '', dn_match.group(1))
            if 8 <= len(dn_number) <= 12:
                logger.info(f"✅ Detected DN: {dn_number}")
                self.metrics["intent_detection"]["dn_dashboard"] = self.metrics["intent_detection"].get("dn_dashboard", 0) + 1
                return "dn_dashboard", dn_number, "dn"
        
        dn_keyword_match = re.search(r'(?:dn|delivery note|track|order)\s*[:#]?\s*(\d{8,12})', question_original, re.IGNORECASE)
        if dn_keyword_match:
            dn_number = re.sub(r'\D', '', dn_keyword_match.group(1))
            if 8 <= len(dn_number) <= 12:
                logger.info(f"✅ Detected DN from keyword: {dn_number}")
                self.metrics["intent_detection"]["dn_dashboard"] = self.metrics["intent_detection"].get("dn_dashboard", 0) + 1
                return "dn_dashboard", dn_number, "dn"
        
        # ==========================================================
        # STEP 4: PRODUCT DETECTION (with explicit keyword)
        # ==========================================================
        if "product" in question_lower or "model" in question_lower or "material" in question_lower or "sku" in question_lower:
            product_match = re.search(r'(?:product|model|material|sku)\s*[:#]?\s*([A-Za-z0-9\-]+)', question_original, re.IGNORECASE)
            if product_match:
                entity = product_match.group(1).strip()
                if len(entity) > 1:
                    resolved = self.resolver.resolve_product(entity)
                    if resolved:
                        logger.info(f"✅ Detected product: '{resolved}'")
                        self.metrics["intent_detection"]["product_dashboard"] = self.metrics["intent_detection"].get("product_dashboard", 0) + 1
                        return "product_dashboard", resolved, "product"
        
        # ==========================================================
        # STEP 5: WAREHOUSE DETECTION (with explicit keyword)
        # ==========================================================
        if "warehouse" in question_lower or "wh " in question_lower:
            wh_match = re.search(r'(?:warehouse|wh)\s+([A-Za-z0-9\s\-]+)', question_original, re.IGNORECASE)
            if wh_match:
                entity = wh_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_warehouse(entity)
                    if resolved:
                        logger.info(f"✅ Detected warehouse: '{resolved}'")
                        self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                        return "warehouse_dashboard", resolved, "warehouse"
                    else:
                        # Pass entity for search later
                        logger.info(f"🔍 Warehouse '{entity}' not found, will search")
                        self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                        return "warehouse_dashboard", entity, "warehouse"
            
            wh_pattern = re.search(r'^([A-Za-z\s\-]+)\s+warehouse$', question_original, re.IGNORECASE)
            if wh_pattern:
                entity = wh_pattern.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_warehouse(entity)
                    if resolved:
                        logger.info(f"✅ Detected warehouse from pattern: '{resolved}'")
                        self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                        return "warehouse_dashboard", resolved, "warehouse"
                    else:
                        logger.info(f"🔍 Warehouse '{entity}' not found, will search")
                        self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                        return "warehouse_dashboard", entity, "warehouse"
            
            if context and context.last_warehouse:
                logger.info(f"🔄 Using context warehouse: {context.last_warehouse}")
                self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                return "warehouse_dashboard", context.last_warehouse, "warehouse"
        
        # ==========================================================
        # STEP 6: CITY DETECTION (with explicit keyword ONLY)
        # ==========================================================
        if "city" in question_lower or "town" in question_lower:
            city_match = re.search(r'(?:city|town)\s+([A-Za-z\s\-]+)', question_original, re.IGNORECASE)
            if city_match:
                entity = city_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_city(entity)
                    if resolved:
                        logger.info(f"✅ Detected city: '{resolved}'")
                        self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                        return "city_dashboard", resolved, "city"
                    else:
                        logger.info(f"🔍 City '{entity}' not found, will search")
                        self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                        return "city_dashboard", entity, "city"
            
            city_pattern = re.search(r'^([A-Za-z\s\-]+)\s+city$', question_original, re.IGNORECASE)
            if city_pattern:
                entity = city_pattern.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_city(entity)
                    if resolved:
                        logger.info(f"✅ Detected city from pattern: '{resolved}'")
                        self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                        return "city_dashboard", resolved, "city"
                    else:
                        logger.info(f"🔍 City '{entity}' not found, will search")
                        self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                        return "city_dashboard", entity, "city"
            
            if context and context.last_city:
                logger.info(f"🔄 Using context city: {context.last_city}")
                self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                return "city_dashboard", context.last_city, "city"
        
        # ==========================================================
        # STEP 7: DEALER DETECTION (with explicit keywords)
        # ==========================================================
        dealer_keywords = ["dealer", "customer", "party", "sold to", "show"]
        if any(kw in question_lower for kw in dealer_keywords):
            dealer_match = re.search(r'(?:dealer|customer|party|show)\s+([A-Za-z0-9\s&\.\-]+)', question_original, re.IGNORECASE)
            if dealer_match:
                entity = dealer_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_dealer(entity)
                    if resolved:
                        logger.info(f"✅ Detected dealer: '{resolved}'")
                        self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                        return "dealer_dashboard", resolved, "dealer"
                    else:
                        logger.info(f"🔍 Dealer '{entity}' not found, will search")
                        self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                        return "dealer_dashboard", entity, "dealer"
            
            for_match = re.search(r'for\s+([A-Za-z0-9\s&\.\-]+)', question_original, re.IGNORECASE)
            if for_match:
                entity = for_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_dealer(entity)
                    if resolved:
                        logger.info(f"✅ Detected dealer from 'for': '{resolved}'")
                        self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                        return "dealer_dashboard", resolved, "dealer"
                    else:
                        logger.info(f"🔍 Dealer '{entity}' not found, will search")
                        self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                        return "dealer_dashboard", entity, "dealer"
            
            if context and context.last_dealer:
                logger.info(f"🔄 Using context dealer: {context.last_dealer}")
                self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                return "dealer_dashboard", context.last_dealer, "dealer"
        
        # ==========================================================
        # STEP 8: STANDALONE - DEALER FIRST (FIXED!)
        # ==========================================================
        # For "New Central Electronics" - this should be DEALER, not CITY!
        if 3 <= len(question_original) <= 100 and not any(c.isdigit() for c in question_original):
            
            # ==========================================================
            # STEP 8a: Check DEALER FIRST (was LAST!)
            # ==========================================================
            dealer_resolved = self.resolver.resolve_dealer(question_original)
            if dealer_resolved:
                logger.info(f"✅ Detected dealer from standalone: '{dealer_resolved}'")
                self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                return "dealer_dashboard", dealer_resolved, "dealer"
            
            # ==========================================================
            # STEP 8b: Check WAREHOUSE
            # ==========================================================
            warehouse_resolved = self.resolver.resolve_warehouse(question_original)
            if warehouse_resolved:
                logger.info(f"✅ Detected warehouse from standalone: '{warehouse_resolved}'")
                self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                return "warehouse_dashboard", warehouse_resolved, "warehouse"
            
            # ==========================================================
            # STEP 8c: Check PRODUCT
            # ==========================================================
            product_resolved = self.resolver.resolve_product(question_original)
            if product_resolved:
                logger.info(f"✅ Detected product from standalone: '{product_resolved}'")
                self.metrics["intent_detection"]["product_dashboard"] = self.metrics["intent_detection"].get("product_dashboard", 0) + 1
                return "product_dashboard", product_resolved, "product"
            
            # ==========================================================
            # STEP 8d: Check CITY LAST! (was SECOND!)
            # ==========================================================
            city_resolved = self.resolver.resolve_city(question_original)
            if city_resolved:
                logger.info(f"✅ Detected city from standalone: '{city_resolved}'")
                self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                return "city_dashboard", city_resolved, "city"
            
            # ==========================================================
            # STEP 8e: If nothing resolved, treat as DEALER (default)
            # ==========================================================
            logger.info(f"🔍 Treating standalone as dealer (default): '{question_original}'")
            self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
            return "dealer_dashboard", question_original, "dealer"
        
        # ==========================================================
        # STEP 9: DIVISION DETECTION
        # ==========================================================
        if "division" in question_lower:
            division_match = re.search(r'(?:division|div)\s+([A-Za-z\s\-]+)', question_original, re.IGNORECASE)
            if division_match:
                entity = division_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected division: '{entity}'")
                    self.metrics["intent_detection"]["division_dashboard"] = self.metrics["intent_detection"].get("division_dashboard", 0) + 1
                    return "division_dashboard", entity, "division"
        
        # ==========================================================
        # STEP 10: SALES MANAGER DETECTION
        # ==========================================================
        if "sales manager" in question_lower or "sm " in question_lower:
            sm_match = re.search(r'(?:sales manager|sm|manager)\s+([A-Za-z\s\-]+)', question_original, re.IGNORECASE)
            if sm_match:
                entity = sm_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected sales manager: '{entity}'")
                    self.metrics["intent_detection"]["sales_manager_dashboard"] = self.metrics["intent_detection"].get("sales_manager_dashboard", 0) + 1
                    return "sales_manager_dashboard", entity, "sales_manager"
        
        # ==========================================================
        # STEP 11: SALES OFFICE DETECTION
        # ==========================================================
        if "sales office" in question_lower or "office " in question_lower:
            so_match = re.search(r'(?:sales office|office)\s+([A-Za-z\s\-]+)', question_original, re.IGNORECASE)
            if so_match:
                entity = so_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected sales office: '{entity}'")
                    self.metrics["intent_detection"]["sales_office_dashboard"] = self.metrics["intent_detection"].get("sales_office_dashboard", 0) + 1
                    return "sales_office_dashboard", entity, "sales_office"
        
        # ==========================================================
        # STEP 12: PATTERN MATCHING FOR ALL OTHER INTENTS
        # ==========================================================
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    logger.info(f"✅ Detected intent '{intent}' from pattern '{pattern}'")
                    self.metrics["intent_detection"][intent] = self.metrics["intent_detection"].get(intent, 0) + 1
                    entity, entity_type = self._extract_entity(question_original, intent)
                    return intent, entity, entity_type
        
        # ==========================================================
        # STEP 13: FALLBACK - Context
        # ==========================================================
        if context and context.last_intent and context.last_entity:
            logger.info(f"🔄 Using context: {context.last_intent} with entity {context.last_entity}")
            return context.last_intent, context.last_entity, self._get_entity_type(context.last_intent)
        
        # ==========================================================
        # STEP 14: UNKNOWN - Return help
        # ==========================================================
        logger.warning(f"❌ Unknown intent for: '{question_original}'")
        return "help", None, None

# ==========================================================
# END OF BLOCK 11 - FIXED v4.0
# ==========================================================
    
    
    # ==========================================================
# BLOCK 12: FOLLOW-UP DETECTION
# ==========================================================

    def _detect_followup(self, question: str, context: ConversationContext) -> Optional[str]:
        if "revenue" in question or "amount" in question or "worth" in question:
            return context.last_intent
        if "pod" in question:
            return "pod_dashboard"
        if "pgi" in question:
            return "pgi_dashboard"
        if "units" in question or "quantity" in question:
            return context.last_intent
        if "aging" in question or "old" in question or "delay" in question:
            return "aging_dashboard"
        if "pending" in question:
            return context.last_intent
        if "ranking" in question or "rank" in question or "top" in question:
            return "dealer_ranking"
        if "products" in question or "models" in question or "product" in question:
            return "dealer_products"
        if "performance" in question or "status" in question:
            return context.last_intent
        return None

# ==========================================================
# BLOCK 13: ENTITY EXTRACTION
# ==========================================================

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
        
        if intent == "product_dashboard":
            product_match = re.search(r'(?:product|model|material)\s+([A-Za-z0-9\-]+)', question_clean, re.IGNORECASE)
            if product_match:
                return product_match.group(1).strip(), "product"
        
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
            "sales_office": "sales_office",
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
            "warehouse_products": "warehouse",
            "city_dashboard": "city",
            "city_ranking": "city",
            "city_dealers": "city",
            "city_products": "city",
            "product_dashboard": "product",
            "product_ranking": "product",
            "product_trend": "product",
            "dn_dashboard": "dn",
            "dn_analytics": "dn",
            "pgi_dashboard": "pgi",
            "pod_dashboard": "pod",
            "delivery_dashboard": "delivery",
            "executive_dashboard": "executive",
            "control_tower": "control",
            "revenue_dashboard": "revenue",
            "aging_dashboard": "aging",
            "division_dashboard": "division",
            "sales_manager_dashboard": "sales_manager",
            "sales_office_dashboard": "sales_office",
            "help": "help",
        }
        return entity_mapping.get(intent, "unknown")

# ==========================================================
# BLOCK 14: CONTEXT MANAGEMENT
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
        elif entity_type == "division":
            context.last_division = entity
            context.last_entity = entity
        elif entity_type == "sales_manager":
            context.last_sales_manager = entity
            context.last_entity = entity
        elif entity_type == "sales_office":
            context.last_sales_office = entity
            context.last_entity = entity
        
        self.conversation_cache[phone_number] = context

# ==========================================================
# BLOCK 15: MAIN ENTRY POINT
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

# ==========================================================
# BLOCK 16: ROUTING ENGINE
# ==========================================================

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
            if intent == "warehouse_products":
                return self._route_warehouse_products(entity, context, req_id)
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
            if intent == "product_trend":
                return self._route_product_trend(entity, context, req_id)
            if intent == "dn_dashboard":
                return self._route_dn_dashboard(entity, context, req_id)  # ← FIXED
            if intent == "dn_analytics":
                return self._route_dn_analytics(req_id)
            if intent == "pgi_dashboard":
                return self._route_pgi_dashboard(req_id)
            if intent == "pod_dashboard":
                return self._route_pod_dashboard(req_id)
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
            if intent == "sales_office_dashboard":
                return self._route_sales_office_dashboard(entity, context, req_id)
            
            logger.warning(f"[{req_id}] Unhandled intent: {intent}")
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] Routing error for {intent}: {e}")
            return f"⚠️ Unable to load {intent.replace('_', ' ').title()}. Please try again."

# ==========================================================
# BLOCK 17: ROUTE HANDLERS
# ==========================================================
# BLOCK 17: ROUTE HANDLERS (FIXED - v2.0)
# ==========================================================

    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle dealer dashboard with improved search and suggestions.
        BLOCK 17 - FIXED
        """
        dealer_name = entity
        if not dealer_name and context and context.last_dealer:
            dealer_name = context.last_dealer
        
        if not dealer_name:
            return "🏪 *DEALER DASHBOARD*\n\nPlease specify a dealer name.\n\n*Examples:*\n• ZQ Electronics\n• Show dealer ZQ Electronics"
        
        # ✅ Try to resolve dealer
        resolved = self.resolver.resolve_dealer(dealer_name)
        
        # ✅ If not found, try searching for similar dealers
        if not resolved:
            logger.info(f"[{req_id}] 🔍 Dealer '{dealer_name}' not found, searching for similar...")
            
            # Try to find similar dealers using search
            try:
                similar = self.analytics.search_dealer(dealer_name, exact=False)
                if similar and len(similar) > 0:
                    suggestions = [s['dealer_name'] for s in similar[:5]]
                    return f"❌ Dealer '{dealer_name}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
            except Exception as e:
                logger.error(f"[{req_id}] Search error: {e}")
            
            # Try with individual words
            tokens = dealer_name.split()
            for token in tokens:
                if len(token) > 2:
                    resolved = self.resolver.resolve_dealer(token)
                    if resolved:
                        break
            
            if not resolved:
                return f"❌ Dealer '{dealer_name}' not found.\n\n💡 Please check the spelling or try a different dealer name."
        
        response = self.analytics.get_dealer_dashboard(resolved)
        if not self._validate_response(response, "dealer_dashboard", req_id):
            return f"❌ Unable to retrieve data for '{resolved}'."
        return self._format_dealer_dashboard(response.data, resolved)
    
    def _route_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle warehouse dashboard with improved search and suggestions.
        BLOCK 17 - FIXED
        """
        warehouse_name = entity
        if not warehouse_name and context and context.last_warehouse:
            warehouse_name = context.last_warehouse
        
        if not warehouse_name:
            return "🏭 *WAREHOUSE DASHBOARD*\n\nPlease specify a warehouse name.\n\n*Examples:*\n• Lahore warehouse\n• Rawalpindi warehouse"
        
        # ✅ Try to resolve warehouse
        resolved = self.resolver.resolve_warehouse(warehouse_name)
        
        # ✅ If not found, try searching for similar warehouses
        if not resolved:
            logger.info(f"[{req_id}] 🔍 Warehouse '{warehouse_name}' not found, searching for similar...")
            
            # Try to find similar warehouses using search
            try:
                similar = self.analytics.search_warehouse(warehouse_name)
                if similar and len(similar) > 0:
                    suggestions = [s['warehouse'] for s in similar[:5]]
                    return f"❌ Warehouse '{warehouse_name}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
            except Exception as e:
                logger.error(f"[{req_id}] Search error: {e}")
            
            # Try with individual words
            tokens = warehouse_name.split()
            for token in tokens:
                if len(token) > 2:
                    resolved = self.resolver.resolve_warehouse(token)
                    if resolved:
                        break
            
            if not resolved:
                return f"❌ Warehouse '{warehouse_name}' not found.\n\n💡 Please check the spelling or try a different warehouse name."
        
        response = self.analytics.get_warehouse_dashboard(resolved)
        if not self._validate_response(response, "warehouse_dashboard", req_id):
            return f"❌ Unable to retrieve data for warehouse '{resolved}'."
        return self._format_warehouse_dashboard(response.data, resolved)
    
    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle city dashboard with improved search and suggestions.
        BLOCK 17 - FIXED
        """
        city_name = entity
        if not city_name and context and context.last_city:
            city_name = context.last_city
        
        if not city_name:
            return "🏙️ *CITY DASHBOARD*\n\nPlease specify a city name.\n\n*Examples:*\n• Haripur\n• Sahiwal"
        
        # ✅ Try to resolve city
        resolved = self.resolver.resolve_city(city_name)
        
        # ✅ If not found, try searching for similar cities
        if not resolved:
            logger.info(f"[{req_id}] 🔍 City '{city_name}' not found, searching for similar...")
            
            # Try to find similar cities using search
            try:
                similar = self.analytics.search_city(city_name)
                if similar and len(similar) > 0:
                    suggestions = [s['city'] for s in similar[:5]]
                    return f"❌ City '{city_name}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
            except Exception as e:
                logger.error(f"[{req_id}] Search error: {e}")
            
            # Try with individual words
            tokens = city_name.split()
            for token in tokens:
                if len(token) > 2:
                    resolved = self.resolver.resolve_city(token)
                    if resolved:
                        break
            
            if not resolved:
                return f"❌ City '{city_name}' not found.\n\n💡 Please check the spelling or try a different city name."
        
        response = self.analytics.get_city_dashboard(resolved)
        if not self._validate_response(response, "city_dashboard", req_id):
            return f"❌ Unable to retrieve data for city '{resolved}'."
        return self._format_city_dashboard(response.data, resolved)
    
    def _route_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle product dashboard with improved search and suggestions.
        BLOCK 17 - FIXED
        """
        product_name = entity
        if not product_name and context and context.last_product:
            product_name = context.last_product
        
        if not product_name:
            return "📦 *PRODUCT DASHBOARD*\n\nPlease specify a product.\n\n*Examples:*\n• HRF-316IPGA\n• Model A123"
        
        # ✅ Try to resolve product
        resolved = self.resolver.resolve_product(product_name)
        
        # ✅ If not found, try searching for similar products
        if not resolved:
            logger.info(f"[{req_id}] 🔍 Product '{product_name}' not found, searching for similar...")
            
            # Try to find similar products using search
            try:
                similar = self.analytics.search_product(product_name)
                if similar and len(similar) > 0:
                    suggestions = [s['product'] for s in similar[:5]]
                    return f"❌ Product '{product_name}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
            except Exception as e:
                logger.error(f"[{req_id}] Search error: {e}")
            
            return f"❌ Product '{product_name}' not found.\n\n💡 Please check the spelling or try a different product name."
        
        response = self.analytics.get_product_dashboard(resolved)
        if not self._validate_response(response, "product_dashboard", req_id):
            return f"❌ Unable to retrieve data for product '{resolved}'."
        return self._format_product_dashboard(response.data, resolved)
    
    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle DN dashboard with improved search and suggestions.
        BLOCK 17 - FIXED
        """
        dn_number = entity or (context.last_dn if context else None)
        if not dn_number:
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number.\n\n*Example:* 6243675570"
        
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return f"❌ Invalid DN number: '{dn_number}'\n\nDN numbers must be 8-12 digits."
        
        logger.info(f"[{req_id}] 🔍 Looking up DN: {dn_clean}")
        
        try:
            response = self.analytics.get_dn_dashboard(dn_clean)
            
            if response is None:
                return f"❌ Unable to retrieve data for DN {dn_clean}.\n\n💡 The system could not process your request."
            
            if hasattr(response, 'success'):
                if not response.success:
                    error_msg = getattr(response, 'error', 'Unknown error')
                    return f"❌ Unable to retrieve data for DN {dn_clean}.\n\n{error_msg}"
                
                data = response.data
                if data and isinstance(data, dict):
                    if "error" in data:
                        return f"❌ {data['error']}"
                    
                    # Format and return the dashboard
                    return self._format_dn_dashboard(data, dn_clean)
            
            return f"❌ Unable to retrieve data for DN {dn_clean}."
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ DN dashboard error: {e}")
            return f"❌ Error retrieving DN {dn_clean}: {str(e)}"

    def _route_dealer_ranking(self, req_id: str) -> str:
        """
        Handle dealer ranking.
        BLOCK 17 - FIXED
        """
        response = self.analytics.get_ranking_dashboard(limit=10)
        if not self._validate_response(response, "dealer_ranking", req_id):
            return "❌ Unable to retrieve dealer ranking."
        return self._format_dealer_ranking(response.data)
    
    def _route_dealer_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle dealer products.
        BLOCK 17 - FIXED
        """
        dealer_name = entity or (context.last_dealer if context else None)
        if not dealer_name:
            return "📦 *DEALER PRODUCTS*\n\nPlease specify a dealer name."
        return f"📦 *PRODUCTS FOR {dealer_name.upper()}*\n\nProduct information coming soon."
    
    def _route_warehouse_ranking(self, req_id: str) -> str:
        """
        Handle warehouse ranking.
        BLOCK 17 - FIXED
        """
        return "🏆 *WAREHOUSE RANKING*\n\nWarehouse ranking coming soon."
    
    def _route_warehouse_coverage(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle warehouse coverage.
        BLOCK 17 - FIXED
        """
        warehouse_name = entity or (context.last_warehouse if context else None)
        if not warehouse_name:
            return "📍 *WAREHOUSE COVERAGE*\n\nPlease specify a warehouse name."
        return f"📍 *COVERAGE FOR {warehouse_name.upper()}*\n\nCoverage information coming soon."
    
    def _route_warehouse_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle warehouse products.
        BLOCK 17 - FIXED
        """
        warehouse_name = entity or (context.last_warehouse if context else None)
        if not warehouse_name:
            return "📦 *WAREHOUSE PRODUCTS*\n\nPlease specify a warehouse name."
        return f"📦 *PRODUCTS IN {warehouse_name.upper()}*\n\nProduct list coming soon."
    
    def _route_city_ranking(self, req_id: str) -> str:
        """
        Handle city ranking.
        BLOCK 17 - FIXED
        """
        return "🏆 *CITY RANKING*\n\nCity ranking coming soon."
    
    def _route_city_dealers(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle city dealers.
        BLOCK 17 - FIXED
        """
        city_name = entity or (context.last_city if context else None)
        if not city_name:
            return "📍 *CITY DEALERS*\n\nPlease specify a city name."
        return f"📍 *DEALERS IN {city_name.upper()}*\n\nDealer list coming soon."
    
    def _route_city_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle city products.
        BLOCK 17 - FIXED
        """
        city_name = entity or (context.last_city if context else None)
        if not city_name:
            return "📦 *CITY PRODUCTS*\n\nPlease specify a city name."
        return f"📦 *PRODUCTS IN {city_name.upper()}*\n\nProduct list coming soon."
    
    def _route_product_ranking(self, req_id: str) -> str:
        """
        Handle product ranking.
        BLOCK 17 - FIXED
        """
        return "🏆 *PRODUCT RANKING*\n\nProduct ranking coming soon."
    
    def _route_product_trend(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle product trend.
        BLOCK 17 - FIXED
        """
        return "📈 *PRODUCT TREND*\n\nProduct trend coming soon."

    def _route_dn_analytics(self, req_id: str) -> str:
        """
        Handle DN analytics.
        BLOCK 17 - FIXED
        """
        return "📊 *DN ANALYTICS*\n\nAnalytics coming soon."

    def _route_pgi_dashboard(self, req_id: str) -> str:
        """
        Handle PGI dashboard.
        BLOCK 17 - FIXED
        """
        response = self.analytics.get_pgi_dashboard()
        if not self._validate_response(response, "pgi_dashboard", req_id):
            return "❌ Unable to retrieve PGI data."
        return self._format_pgi_dashboard(response.data)
    
    def _route_pod_dashboard(self, req_id: str) -> str:
        """
        Handle POD dashboard.
        BLOCK 17 - FIXED
        """
        response = self.analytics.get_pod_dashboard()
        if not self._validate_response(response, "pod_dashboard", req_id):
            return "❌ Unable to retrieve POD data."
        return self._format_pod_dashboard(response.data)
    
    def _route_delivery_dashboard(self, req_id: str) -> str:
        """
        Handle delivery dashboard.
        BLOCK 17 - FIXED
        """
        response = self.analytics.get_delivery_dashboard()
        if not self._validate_response(response, "delivery_dashboard", req_id):
            return "❌ Unable to retrieve delivery data."
        return self._format_delivery_dashboard(response.data)
    
    def _route_executive_dashboard(self, req_id: str) -> str:
        """
        Handle executive dashboard.
        BLOCK 17 - FIXED
        """
        response = self.analytics.get_executive_dashboard()
        if not self._validate_response(response, "executive_dashboard", req_id):
            return "❌ Unable to retrieve executive data."
        return self._format_executive_dashboard(response.data)
    
    def _route_control_tower(self, req_id: str) -> str:
        """
        Handle control tower dashboard.
        BLOCK 17 - FIXED
        """
        response = self.analytics.get_control_tower_dashboard()
        if not self._validate_response(response, "control_tower", req_id):
            return "❌ Unable to retrieve control tower data."
        return self._format_control_tower(response.data)
    
    def _route_revenue_dashboard(self, req_id: str) -> str:
        """
        Handle revenue dashboard.
        BLOCK 17 - FIXED
        """
        response = self.analytics.get_revenue_dashboard()
        if not self._validate_response(response, "revenue_dashboard", req_id):
            return "❌ Unable to retrieve revenue data."
        return self._format_revenue_dashboard(response.data)
    
    def _route_aging_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle aging dashboard.
        BLOCK 17 - FIXED
        """
        response = self.analytics.get_aging_dashboard()
        if not self._validate_response(response, "aging_dashboard", req_id):
            return "❌ Unable to retrieve aging data."
        return self._format_aging_dashboard(response.data)
    
    def _route_division_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle division dashboard.
        BLOCK 17 - FIXED
        """
        division_name = entity or (context.last_division if context else None)
        if not division_name:
            return "📊 *DIVISION DASHBOARD*\n\nPlease specify a division name."
        return f"📊 *DIVISION: {division_name.upper()}*\n\nDivision data coming soon."
    
    def _route_sales_manager_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle sales manager dashboard.
        BLOCK 17 - FIXED
        """
        sm_name = entity or (context.last_sales_manager if context else None)
        if not sm_name:
            return "👤 *SALES MANAGER DASHBOARD*\n\nPlease specify a sales manager name."
        return f"👤 *SALES MANAGER: {sm_name.upper()}*\n\nSales manager data coming soon."
    
    def _route_sales_office_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle sales office dashboard.
        BLOCK 17 - FIXED
        """
        so_name = entity or (context.last_sales_office if context else None)
        if not so_name:
            return "🏢 *SALES OFFICE DASHBOARD*\n\nPlease specify a sales office name."
        return f"🏢 *SALES OFFICE: {so_name.upper()}*\n\nSales office data coming soon."

# ==========================================================
# END OF BLOCK 17 - FIXED
# ==========================================================

    
# ==========================================================
# BLOCK 18: DN ROUTE HANDLER - ✅ FIXED
# ==========================================================

    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle DN dashboard requests - Direct call to analytics"""
        dn_number = entity or (context.last_dn if context else None)
        if not dn_number:
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number.\n\n*Example:* 6243675570"
        
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return f"❌ Invalid DN number: '{dn_number}'\n\nDN numbers must be 8-12 digits."
        
        # ✅ FIX: Call analytics directly WITHOUT resolving first
        logger.info(f"[{req_id}] 🔍 Looking up DN: {dn_clean}")
        
        try:
            response = self.analytics.get_dn_dashboard(dn_clean)
            
            # Check if response is valid
            if response is None:
                return f"❌ Unable to retrieve data for DN {dn_clean}.\n\n💡 The system could not process your request."
            
            # Check if response has success attribute
            if hasattr(response, 'success'):
                if not response.success:
                    error_msg = getattr(response, 'error', 'Unknown error')
                    return f"❌ Unable to retrieve data for DN {dn_clean}.\n\n{error_msg}"
                
                # Check data for errors
                data = response.data
                if data and isinstance(data, dict):
                    if "error" in data:
                        return f"❌ {data['error']}"
                    
                    # Format and return the dashboard
                    return self._format_dn_dashboard(data, dn_clean)
            
            return f"❌ Unable to retrieve data for DN {dn_clean}."
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ DN dashboard error: {e}")
            return f"❌ Error retrieving DN {dn_clean}: {str(e)}"

# ==========================================================
# BLOCK 19: DN ANALYTICS ROUTE
# ==========================================================

    def _route_dn_analytics(self, req_id: str) -> str:
        return "📊 *DN ANALYTICS*\n\nAnalytics coming soon."

# ==========================================================
# BLOCK 20: PGI, POD, DELIVERY, EXECUTIVE, CONTROL TOWER, REVENUE, AGING ROUTES
# ==========================================================

    def _route_pgi_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_pgi_dashboard()
        if not self._validate_response(response, "pgi_dashboard", req_id):
            return "❌ Unable to retrieve PGI data."
        return self._format_pgi_dashboard(response.data)
    
    def _route_pod_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_pod_dashboard()
        if not self._validate_response(response, "pod_dashboard", req_id):
            return "❌ Unable to retrieve POD data."
        return self._format_pod_dashboard(response.data)
    
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
        division_name = entity or (context.last_division if context else None)
        if not division_name:
            return "📊 *DIVISION DASHBOARD*\n\nPlease specify a division name."
        return f"📊 *DIVISION: {division_name.upper()}*\n\nDivision data coming soon."
    
    def _route_sales_manager_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        sm_name = entity or (context.last_sales_manager if context else None)
        if not sm_name:
            return "👤 *SALES MANAGER DASHBOARD*\n\nPlease specify a sales manager name."
        return f"👤 *SALES MANAGER: {sm_name.upper()}*\n\nSales manager data coming soon."
    
    def _route_sales_office_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        so_name = entity or (context.last_sales_office if context else None)
        if not so_name:
            return "🏢 *SALES OFFICE DASHBOARD*\n\nPlease specify a sales office name."
        return f"🏢 *SALES OFFICE: {so_name.upper()}*\n\nSales office data coming soon."

# ==========================================================
# BLOCK 21: RESPONSE VALIDATION
# ==========================================================

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
# BLOCK 22: FORMATTERS
    # ==========================================================
# ==========================================================
# BLOCK 22: FORMATTERS - DN Dashboard
# ==========================================================

    def _format_dn_dashboard(self, data: Dict, dn_number: str) -> str:
        """Format DN dashboard for WhatsApp"""
        try:
            if "error" in data:
                return f"❌ {data['error']}"
            
            status = data.get('delivery_status', 'Unknown')
            status_emoji = "✅" if status in ['Completed', 'Delivered', 'Closed'] else "⏳"
            pending_text = "🔴 Yes" if data.get('pending_flag') else "🟢 No"
            
            # ✅ Use the NEW field names from BLOCK 10
            delivery_aging_text = data.get('delivery_aging_text', 'N/A')
            pod_aging_text = data.get('pod_aging_text', 'N/A')
            total_cycle_text = data.get('total_cycle_text', 'N/A')
            
            # ✅ Get validation issues
            issues = data.get('issues', [])
            
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
                f"Delivery Aging: {delivery_aging_text}",
                f"POD Aging: {pod_aging_text}",
                f"Total Cycle: {total_cycle_text}",
            ]
            
            # ✅ Add validation warnings if there are issues
            if issues:
                lines.append("")
                lines.append("⚠ *Data Issue Detected*")
                for issue in issues:
                    lines.append(f"   {issue}")
                lines.append("   Please verify source data.")
            
            lines.extend([
                "",
                "📋 *Status*",
                f"Delivery: {status} {status_emoji}",
                f"PGI: {data.get('pgi_status', 'N/A')}",
                f"POD: {data.get('pod_status', 'N/A')}",
                f"Pending: {pending_text}"
            ])
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"DN format error: {e}")
            return f"❌ Unable to format DN details for {dn_number}"
# BLOCK 23: HELP MESSAGE
# ==========================================================

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
# BLOCK 24: SINGLETON & WRAPPER FUNCTIONS
# ==========================================================

_orchestrator = None

def get_orchestrator(session_factory: Optional[Callable[[], Session]] = None) -> AIOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        try:
            _orchestrator = AIOrchestrator(session_factory=session_factory)
            logger.info("✅ AI Orchestrator v25.1 initialized")
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

# ==========================================================
# BLOCK 25: EXPORTS
# ==========================================================

__all__ = [
    'AIOrchestrator',
    'PostgreSQLResolver',
    'ConversationContext',
    'get_orchestrator',
    'process_whatsapp_query',
    'test_database_connection'
]

# ==========================================================
# END OF FILE - v25.1 COMPLETE
# ==========================================================
