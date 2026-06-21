# ==========================================================
# FILE: app/services/ai_provider_service.py (v24.0 - COMPLETE)
# ==========================================================
# PURPOSE: POSTGRESQL-DRIVEN AI ROUTER
# VERSION: 24.0 - Full Compatibility with Analytics v25.0
#
# CHANGES v24.0:
# - ✅ FIXED: All method name mismatches
# - ✅ ADDED: city_products route
# - ✅ FIXED: delivery_dashboard route
# - ✅ FIXED: executive_dashboard route
# - ✅ FIXED: control_tower route
# - ✅ FIXED: revenue_dashboard route
# - ✅ FIXED: pod_aging route
# - ✅ COMPLETE: 100% compatibility
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
from datetime import datetime, timedelta
from functools import lru_cache
import hashlib

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
# POSTGRESQL RESOLVER - PURE POSTGRESQL
# ==========================================================

class PostgreSQLResolver:
    """Pure PostgreSQL-based entity resolution - No schema_service"""
    
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory
        self._cache = TTLCache(maxsize=2000, ttl=3600)
        
        try:
            from app.models import DeliveryReport
            self.DeliveryReport = DeliveryReport
        except ImportError:
            logger.error("❌ Cannot import DeliveryReport model")
            self.DeliveryReport = None
    
    def _get_session(self) -> Optional[Session]:
        if not self.session_factory:
            return None
        try:
            return self.session_factory()
        except Exception as e:
            logger.error(f"Session creation failed: {e}")
            return None
    
    def resolve_dealer(self, query: str) -> Optional[str]:
        if not query or not query.strip() or not self.DeliveryReport:
            return None
        
        cache_key = f"dealer:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            result = session.query(self.DeliveryReport.customer_name).filter(
                func.lower(self.DeliveryReport.customer_name) == func.lower(query)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.customer_name).filter(
                self.DeliveryReport.customer_name.ilike(f"%{query}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
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
        if not query or not query.strip() or not self.DeliveryReport:
            return None
        
        cache_key = f"warehouse:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            result = session.query(self.DeliveryReport.warehouse).filter(
                func.lower(self.DeliveryReport.warehouse) == func.lower(query)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.warehouse).filter(
                self.DeliveryReport.warehouse.ilike(f"%{query}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
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
        if not query or not query.strip() or not self.DeliveryReport:
            return None
        
        cache_key = f"city:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            result = session.query(self.DeliveryReport.ship_to_city).filter(
                func.lower(self.DeliveryReport.ship_to_city) == func.lower(query)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.ship_to_city).filter(
                self.DeliveryReport.ship_to_city.ilike(f"%{query}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
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
        if not query or not query.strip() or not self.DeliveryReport:
            return None
        
        cache_key = f"product:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
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
        if not query or not query.strip() or not self.DeliveryReport:
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
    
    def get_all_dealers(self) -> List[str]:
        if not self.DeliveryReport:
            return []
        
        cache_key = "all_dealers"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return []
        
        try:
            results = session.query(
                func.distinct(self.DeliveryReport.customer_name)
            ).filter(
                self.DeliveryReport.customer_name.isnot(None),
                self.DeliveryReport.customer_name != ''
            ).order_by(
                self.DeliveryReport.customer_name
            ).limit(1000).all()
            
            dealers = [r[0] for r in results if r[0]]
            self._cache[cache_key] = dealers
            return dealers
            
        except Exception as e:
            logger.error(f"Get all dealers error: {e}")
            return []
        finally:
            session.close()
    
    def get_all_warehouses(self) -> List[str]:
        if not self.DeliveryReport:
            return []
        
        cache_key = "all_warehouses"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return []
        
        try:
            results = session.query(
                func.distinct(self.DeliveryReport.warehouse)
            ).filter(
                self.DeliveryReport.warehouse.isnot(None),
                self.DeliveryReport.warehouse != ''
            ).order_by(
                self.DeliveryReport.warehouse
            ).limit(500).all()
            
            warehouses = [r[0] for r in results if r[0]]
            self._cache[cache_key] = warehouses
            return warehouses
            
        except Exception as e:
            logger.error(f"Get all warehouses error: {e}")
            return []
        finally:
            session.close()
    
    def get_all_cities(self) -> List[str]:
        if not self.DeliveryReport:
            return []
        
        cache_key = "all_cities"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return []
        
        try:
            results = session.query(
                func.distinct(self.DeliveryReport.ship_to_city)
            ).filter(
                self.DeliveryReport.ship_to_city.isnot(None),
                self.DeliveryReport.ship_to_city != ''
            ).order_by(
                self.DeliveryReport.ship_to_city
            ).limit(500).all()
            
            cities = [r[0] for r in results if r[0]]
            self._cache[cache_key] = cities
            return cities
            
        except Exception as e:
            logger.error(f"Get all cities error: {e}")
            return []
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
# INTENT PATTERNS - COMPLETE
# ==========================================================

INTENT_PATTERNS = {
    "dealer_dashboard": [
        "dealer dashboard", "dealer performance", "dealer revenue", 
        "dealer units", "dealer dn", "dealer pod", "dealer pgi",
        "dealer delivery", "dealer pending", "dealer aging",
        "show dealer", "customer dashboard", "customer performance",
        "what is dealer", "tell me about dealer"
    ],
    "dealer_ranking": [
        "top dealer", "top dealers", "best dealer", "best dealers",
        "dealer ranking", "dealer rank", "ranking dealer",
        "top 10 dealers", "best performing dealer", "worst dealer"
    ],
    "dealer_products": [
        "what products does dealer", "products of dealer",
        "top products for dealer", "product mix for dealer",
        "dealer products", "dealer buys", "what dealer buys"
    ],
    "warehouse_dashboard": [
        "warehouse dashboard", "warehouse performance",
        "warehouse revenue", "warehouse units", "warehouse dn",
        "warehouse pgi", "warehouse pod", "show warehouse",
        "warehouse status", "what about warehouse"
    ],
    "warehouse_ranking": [
        "top warehouse", "top warehouses", "warehouse ranking",
        "warehouse rank", "ranking warehouse"
    ],
    "warehouse_coverage": [
        "dealer served by warehouse", "cities served by warehouse",
        "warehouse coverage", "warehouse service",
        "which dealers in warehouse"
    ],
    "city_dashboard": [
        "city dashboard", "city performance", "city revenue",
        "city units", "city dn", "show city", "revenue in",
        "dn count in", "units in", "city status"
    ],
    "city_ranking": [
        "top city", "top cities", "city ranking", "city rank"
    ],
    "city_dealers": [
        "dealers in city", "top dealers in city",
        "which dealers in city"
    ],
    "city_products": [
        "products in city", "top products in city",
        "what products in city"
    ],
    "product_dashboard": [
        "product dashboard", "show product", "product performance",
        "product revenue", "product units", "product dn",
        "refrigerator", "ac dashboard", "tv dashboard",
        "washing machine", "freezer", "product status"
    ],
    "product_ranking": [
        "top product", "top products", "best selling",
        "product ranking", "top model", "top material"
    ],
    "dn_dashboard": [
        "show dn", "dn status", "what is dn", "dn details",
        "dn information", "dn quantity", "dn value",
        "dn pgi date", "dn pod date", "dn delivery date",
        "is dn delivered", "is dn pending", "which dealer",
        "track dn", "dn tracking"
    ],
    "dn_analytics": [
        "how many dns", "total dn count", "dn count",
        "delivered dn count", "pending dn count",
        "dn by warehouse", "dn by city", "dn by dealer",
        "dn by division", "dn overview"
    ],
    "pgi_dashboard": [
        "pgi dashboard", "pgi completed", "pgi pending",
        "average pgi days", "pgi by warehouse", "pgi by city",
        "pgi by dealer", "pgi status", "what is pgi"
    ],
    "pgi_by_warehouse": [
        "pgi by warehouse", "warehouse pgi", "pgi per warehouse"
    ],
    "pod_dashboard": [
        "pod dashboard", "pod pending", "pod completed",
        "pod compliance", "average pod days", "pod by warehouse",
        "pod by dealer", "pod status", "what is pod"
    ],
    "pod_aging": [
        "pod aging", "oldest pod pending", "pod pending more than",
        "longest pending pod", "pod delay"
    ],
    "delivery_dashboard": [
        "delivery dashboard", "delivered dns", "pending dns",
        "average delivery days", "delayed deliveries",
        "delivery by warehouse", "delivery by city", "delivery by dealer",
        "delivery status", "on time delivery"
    ],
    "executive_dashboard": [
        "executive summary", "nationwide performance",
        "total revenue", "total units", "total dns",
        "top warehouse", "top city", "top dealer",
        "revenue by division", "revenue by warehouse",
        "revenue by city", "ceo", "management", "overview"
    ],
    "control_tower": [
        "control tower", "critical issues", "pending pod",
        "pending pgi", "delayed deliveries", "alert",
        "dns pending more than", "pgi pending more than",
        "pod pending more than", "risks", "risk analysis",
        "what is pending", "show alerts"
    ],
    "revenue_dashboard": [
        "revenue dashboard", "total revenue", "revenue by dealer",
        "revenue by warehouse", "revenue by city", "revenue by division",
        "revenue by product", "top revenue dealer", "top revenue warehouse",
        "top revenue city", "revenue growth"
    ],
    "aging_dashboard": [
        "dn aging", "oldest pending dn", "dns pending more than",
        "pgi aging", "average pgi days", "pod aging",
        "average pod days", "longest pending pod", "aging analysis"
    ],
    "division_dashboard": [
        "division dashboard", "division performance",
        "division revenue", "division units", "division dn",
        "revenue by division", "show division"
    ],
    "sales_manager_dashboard": [
        "sales manager", "sales manager performance",
        "sales manager revenue", "show sales manager"
    ],
    "distance_dashboard": [
        "distance", "how far", "distance from warehouse",
        "delivery distance", "km", "kilometers"
    ],
    "help": [
        "help", "menu", "hi", "hello", "start", "?", "commands"
    ]
}


# ==========================================================
# FOLLOW-UP PATTERNS
# ==========================================================

FOLLOWUP_PATTERNS = {
    "what_about": r'(?:what about|how about|tell me about|show me)\s+(.+)',
    "its": r'(?:its|his|her)\s+(\w+)',
    "that_dealer": r'(?:that|this)\s+(?:dealer|customer|party)',
    "same_entity": r'(?:also|and)\s+for\s+(.+)',
    "revenue": r'(?:revenue|sales|amount|value)',
    "pod": r'(?:pod|proof of delivery|delivery proof)',
    "pgi": r'(?:pgi|goods issue|issue)',
    "units": r'(?:units|quantity|qty|pieces)',
    "dn": r'(?:dn|delivery note|order)',
    "aging": r'(?:aging|old|delay|overdue)',
    "pending": r'(?:pending|not completed|waiting)',
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
    "warehouse_code": r'\b(WH\d{3})\b',
}


# ==========================================================
# MAIN AI ROUTER
# ==========================================================

class AIOrchestrator:
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory
        
        # Lazy loaded services
        self._analytics = None
        self._analytics_response = None
        self._resolver = None
        
        # Caches
        self.response_cache = TTLCache(maxsize=2000, ttl=CACHE_TTL_SECONDS)
        self.failure_cache = TTLCache(maxsize=400, ttl=60)
        self.fast_cache = LRUCache(maxsize=1000)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        
        # Request state
        self._current_request_id: Optional[str] = None
        
        # Metrics
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
    def resolver(self):
        if self._resolver is None:
            self._resolver = PostgreSQLResolver(self.session_factory)
        return self._resolver
    
    # ==========================================================
    # INTENT DETECTION - ENHANCED
    # ==========================================================
    
    def _detect_intent(self, question: str, context: Optional[ConversationContext] = None) -> Tuple[str, Optional[str], Optional[str]]:
        question_original = question.strip()
        question_lower = question_original.lower()
        
        logger.debug(f"🔍 Detecting intent for: '{question_original}'")
        
        # 1. CHECK FOR HELP COMMANDS
        if question_lower in ["help", "menu", "hi", "hello", "start", "?", "commands"]:
            return "help", None, None
        
        # 2. CHECK FOR FOLLOW-UP
        if context and context.last_intent and context.last_entity:
            followup_intent = self._detect_followup(question_lower, context)
            if followup_intent:
                logger.info(f"🔄 Follow-up detected: {followup_intent}")
                return followup_intent, context.last_entity, self._get_entity_type(followup_intent)
        
        # 3. DN NUMBER DETECTION (HIGHEST PRIORITY)
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
        
        dn_formatted = re.search(r'(\d{4})[\s\-](\d{6})', question_original)
        if dn_formatted:
            dn_number = f"{dn_formatted.group(1)}{dn_formatted.group(2)}"
            if 8 <= len(dn_number) <= 12:
                logger.info(f"✅ Detected formatted DN: {dn_number}")
                self.metrics["intent_detection"]["dn_dashboard"] = self.metrics["intent_detection"].get("dn_dashboard", 0) + 1
                return "dn_dashboard", dn_number, "dn"
        
        # 4. DEALER DETECTION
        dealer_keywords = ["dealer", "customer", "party", "sold to"]
        if any(kw in question_lower for kw in dealer_keywords) or "dealer dashboard" in question_lower:
            dealer_match = re.search(r'(?:dealer|customer|party|show)\s+([A-Za-z0-9\s&\.]+)', question_original, re.IGNORECASE)
            if dealer_match:
                entity = dealer_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_dealer(entity)
                    if resolved:
                        logger.info(f"✅ Detected dealer: '{resolved}'")
                        self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                        return "dealer_dashboard", resolved, "dealer"
            
            for_match = re.search(r'for\s+([A-Za-z0-9\s&\.]+)', question_original, re.IGNORECASE)
            if for_match:
                entity = for_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_dealer(entity)
                    if resolved:
                        logger.info(f"✅ Detected dealer from 'for': '{resolved}'")
                        self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                        return "dealer_dashboard", resolved, "dealer"
            
            if context and context.last_dealer:
                logger.info(f"🔄 Using context dealer: {context.last_dealer}")
                self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                return "dealer_dashboard", context.last_dealer, "dealer"
        
        if 3 <= len(question_original) <= 50:
            if not any(c.isdigit() for c in question_original):
                resolved = self.resolver.resolve_dealer(question_original)
                if resolved:
                    logger.info(f"✅ Detected dealer from standalone: '{resolved}'")
                    self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                    return "dealer_dashboard", resolved, "dealer"
        
        # 5. WAREHOUSE DETECTION
        if "warehouse" in question_lower or "wh " in question_lower:
            wh_match = re.search(r'(?:warehouse|wh)\s+([A-Za-z\s]+)', question_original, re.IGNORECASE)
            if wh_match:
                entity = wh_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_warehouse(entity)
                    if resolved:
                        logger.info(f"✅ Detected warehouse: '{resolved}'")
                        self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                        return "warehouse_dashboard", resolved, "warehouse"
            
            wh_pattern = re.search(r'^([A-Za-z\s]+)\s+warehouse$', question_original, re.IGNORECASE)
            if wh_pattern:
                entity = wh_pattern.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_warehouse(entity)
                    if resolved:
                        logger.info(f"✅ Detected warehouse from pattern: '{resolved}'")
                        self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                        return "warehouse_dashboard", resolved, "warehouse"
            
            if context and context.last_warehouse:
                logger.info(f"🔄 Using context warehouse: {context.last_warehouse}")
                self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                return "warehouse_dashboard", context.last_warehouse, "warehouse"
        
        # 6. CITY DETECTION
        if "city" in question_lower or "in " in question_lower:
            city_match = re.search(r'(?:city|in)\s+([A-Za-z\s]+)', question_original, re.IGNORECASE)
            if city_match:
                entity = city_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_city(entity)
                    if resolved:
                        logger.info(f"✅ Detected city: '{resolved}'")
                        self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                        return "city_dashboard", resolved, "city"
            
            city_pattern = re.search(r'^([A-Za-z\s]+)\s+city$', question_original, re.IGNORECASE)
            if city_pattern:
                entity = city_pattern.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_city(entity)
                    if resolved:
                        logger.info(f"✅ Detected city from pattern: '{resolved}'")
                        self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                        return "city_dashboard", resolved, "city"
            
            if context and context.last_city:
                logger.info(f"🔄 Using context city: {context.last_city}")
                self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                return "city_dashboard", context.last_city, "city"
        
        # 7. PRODUCT DETECTION
        product_keywords = ["product", "model", "material", "sku"]
        if any(kw in question_lower for kw in product_keywords) or "product dashboard" in question_lower:
            product_match = re.search(r'(?:product|model|material)\s+([A-Za-z0-9\-]+)', question_original, re.IGNORECASE)
            if product_match:
                entity = product_match.group(1).strip()
                if len(entity) > 1:
                    resolved = self.resolver.resolve_product(entity)
                    if resolved:
                        logger.info(f"✅ Detected product: '{resolved}'")
                        self.metrics["intent_detection"]["product_dashboard"] = self.metrics["intent_detection"].get("product_dashboard", 0) + 1
                        return "product_dashboard", resolved, "product"
        
        # 8. DIVISION DETECTION
        if "division" in question_lower:
            division_match = re.search(r'(?:division)\s+([A-Za-z\s]+)', question_original, re.IGNORECASE)
            if division_match:
                entity = division_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected division: '{entity}'")
                    self.metrics["intent_detection"]["division_dashboard"] = self.metrics["intent_detection"].get("division_dashboard", 0) + 1
                    return "division_dashboard", entity, "division"
        
        # 9. SALES MANAGER DETECTION
        if "sales manager" in question_lower or "sm " in question_lower:
            sm_match = re.search(r'(?:sales manager|sm)\s+([A-Za-z\s]+)', question_original, re.IGNORECASE)
            if sm_match:
                entity = sm_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Detected sales manager: '{entity}'")
                    self.metrics["intent_detection"]["sales_manager_dashboard"] = self.metrics["intent_detection"].get("sales_manager_dashboard", 0) + 1
                    return "sales_manager_dashboard", entity, "sales_manager"
        
        # 10. PATTERN MATCHING FOR ALL OTHER INTENTS
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    logger.info(f"✅ Detected intent '{intent}' from pattern '{pattern}'")
                    self.metrics["intent_detection"][intent] = self.metrics["intent_detection"].get(intent, 0) + 1
                    entity, entity_type = self._extract_entity(question_original, intent)
                    return intent, entity, entity_type
        
        # 11. FALLBACK - Use context
        if context and context.last_intent and context.last_entity:
            logger.info(f"🔄 Using context: {context.last_intent} with entity {context.last_entity}")
            return context.last_intent, context.last_entity, self._get_entity_type(context.last_intent)
        
        # 12. UNKNOWN - Return help
        logger.warning(f"❌ Unknown intent for: '{question_original}'")
        return "help", None, None
    
    # ==========================================================
    # FOLLOW-UP DETECTION
    # ==========================================================
    
    def _detect_followup(self, question: str, context: ConversationContext) -> Optional[str]:
        if "what about" in question or "how about" in question or "tell me about" in question:
            for pattern_type, pattern in FOLLOWUP_PATTERNS.items():
                if pattern_type == "what_about":
                    continue
                if pattern in question:
                    return context.last_intent
        
        if "its" in question or "his" in question or "her" in question:
            return context.last_intent
        
        if "revenue" in question or "amount" in question:
            return context.last_intent
        if "pod" in question:
            return "pod_dashboard"
        if "pgi" in question:
            return "pgi_dashboard"
        if "units" in question:
            return context.last_intent
        if "aging" in question:
            return "aging_dashboard"
        if "pending" in question:
            return context.last_intent
        
        return None
    
    # ==========================================================
    # ENTITY EXTRACTION
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
            
            if len(question_clean) < 50 and not any(c.isdigit() for c in question_clean):
                return question_clean, "dealer"
        
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
            "warehouse_code": "warehouse",
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
            "distance_dashboard": "distance",
            "help": "help",
        }
        return entity_mapping.get(intent, "unknown")
    
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
        elif entity_type == "division":
            context.last_division = entity
            context.last_entity = entity
        elif entity_type == "sales_manager":
            context.last_sales_manager = entity
            context.last_entity = entity
        
        self.conversation_cache[phone_number] = context
    
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
    # ROUTING ENGINE - UPDATED FOR v25.0
    # ==========================================================
    
    def _route_to_dashboard(self, intent: str, entity: Optional[str], entity_type: Optional[str], context: Optional[ConversationContext], req_id: str) -> Optional[str]:
        if not self.analytics:
            logger.error(f"[{req_id}] Analytics service not available")
            return "⚠️ Analytics service is temporarily unavailable. Please try again later."
        
        try:
            # Dealer Routes
            if intent == "dealer_dashboard":
                return self._route_dealer_dashboard(entity, context, req_id)
            if intent == "dealer_ranking":
                return self._route_dealer_ranking(req_id)
            if intent == "dealer_products":
                return self._route_dealer_products(entity, context, req_id)
            
            # Warehouse Routes
            if intent == "warehouse_dashboard":
                return self._route_warehouse_dashboard(entity, context, req_id)
            if intent == "warehouse_ranking":
                return self._route_warehouse_ranking(req_id)
            if intent == "warehouse_coverage":
                return self._route_warehouse_coverage(entity, context, req_id)
            
            # City Routes
            if intent == "city_dashboard":
                return self._route_city_dashboard(entity, context, req_id)
            if intent == "city_ranking":
                return self._route_city_ranking(req_id)
            if intent == "city_dealers":
                return self._route_city_dealers(entity, context, req_id)
            if intent == "city_products":
                return self._route_city_products(entity, context, req_id)
            
            # Product Routes
            if intent == "product_dashboard":
                return self._route_product_dashboard(entity, context, req_id)
            if intent == "product_ranking":
                return self._route_product_ranking(req_id)
            
            # DN Routes
            if intent == "dn_dashboard":
                return self._route_dn_dashboard(entity, context, req_id)
            if intent == "dn_analytics":
                return self._route_dn_analytics(req_id)
            
            # PGI Routes
            if intent == "pgi_dashboard":
                return self._route_pgi_dashboard(req_id)
            if intent == "pgi_by_warehouse":
                return self._route_pgi_by_warehouse(entity, context, req_id)
            
            # POD Routes
            if intent == "pod_dashboard":
                return self._route_pod_dashboard(req_id)
            if intent == "pod_aging":
                return self._route_pod_aging(req_id)
            
            # Delivery Routes
            if intent == "delivery_dashboard":
                return self._route_delivery_dashboard(req_id)
            
            # Executive Routes
            if intent == "executive_dashboard":
                return self._route_executive_dashboard(req_id)
            
            # Control Tower Routes
            if intent == "control_tower":
                return self._route_control_tower(req_id)
            
            # Revenue Routes
            if intent == "revenue_dashboard":
                return self._route_revenue_dashboard(req_id)
            
            # Aging Routes
            if intent == "aging_dashboard":
                return self._route_aging_dashboard(entity, context, req_id)
            
            # Division Routes
            if intent == "division_dashboard":
                return self._route_division_dashboard(entity, context, req_id)
            
            # Sales Manager Routes
            if intent == "sales_manager_dashboard":
                return self._route_sales_manager_dashboard(entity, context, req_id)
            
            # Distance Routes
            if intent == "distance_dashboard":
                return self._route_distance_dashboard(entity, context, req_id)
            
            logger.warning(f"[{req_id}] Unhandled intent: {intent}")
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] Routing error for {intent}: {e}")
            return f"⚠️ Unable to load {intent.replace('_', ' ').title()}. Please try again."
    
    # ==========================================================
    # DEALER ROUTE HANDLERS
    # ==========================================================
    
    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        dealer_name = entity
        if not dealer_name and context and context.last_dealer:
            dealer_name = context.last_dealer
            logger.info(f"[{req_id}] Using context dealer: {dealer_name}")
        
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
        response = self.analytics.get_dealer_ranking(limit=10, top=True)
        if not self._validate_response(response, "dealer_ranking", req_id):
            return "❌ Unable to retrieve dealer ranking."
        return self._format_dealer_ranking(response.data)
    
    def _route_dealer_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        dealer_name = entity or (context.last_dealer if context else None)
        if not dealer_name:
            return "📦 *DEALER PRODUCTS*\n\nPlease specify a dealer name.\n\n*Example:* Products of ZQ Electronics"
        
        resolved = self.resolver.resolve_dealer(dealer_name)
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
        warehouse_name = entity
        if not warehouse_name and context and context.last_warehouse:
            warehouse_name = context.last_warehouse
        
        if not warehouse_name:
            return "🏭 *WAREHOUSE DASHBOARD*\n\nPlease specify a warehouse name.\n\n*Examples:*\n• Lahore warehouse\n• Rawalpindi warehouse"
        
        resolved = self.resolver.resolve_warehouse(warehouse_name)
        if not resolved:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        response = self.analytics.get_warehouse_dashboard(resolved)
        if not self._validate_response(response, "warehouse_dashboard", req_id):
            return f"❌ Unable to retrieve data for warehouse '{resolved}'."
        return self._format_warehouse_dashboard(response.data, resolved)
    
    def _route_warehouse_ranking(self, req_id: str) -> str:
        response = self.analytics.get_warehouse_ranking(limit=10, top=True)
        if not self._validate_response(response, "warehouse_ranking", req_id):
            return "❌ Unable to retrieve warehouse ranking."
        return self._format_warehouse_ranking(response.data)
    
    def _route_warehouse_coverage(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        warehouse_name = entity or (context.last_warehouse if context else None)
        if not warehouse_name:
            return "📍 *WAREHOUSE COVERAGE*\n\nPlease specify a warehouse name.\n\n*Example:* Coverage of Lahore warehouse"
        
        resolved = self.resolver.resolve_warehouse(warehouse_name)
        if not resolved:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        response = self.analytics.get_warehouse_coverage(resolved)
        if not self._validate_response(response, "warehouse_coverage", req_id):
            return f"❌ Unable to retrieve coverage for '{resolved}'."
        return self._format_warehouse_coverage(response.data, resolved)
    
    # ==========================================================
    # CITY ROUTE HANDLERS
    # ==========================================================
    
    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        city_name = entity
        if not city_name and context and context.last_city:
            city_name = context.last_city
        
        if not city_name:
            return "🏙️ *CITY DASHBOARD*\n\nPlease specify a city name.\n\n*Examples:*\n• Haripur\n• Lahore city"
        
        resolved = self.resolver.resolve_city(city_name)
        if not resolved:
            return f"❌ City '{city_name}' not found."
        
        response = self.analytics.get_city_dashboard(resolved)
        if not self._validate_response(response, "city_dashboard", req_id):
            return f"❌ Unable to retrieve data for city '{resolved}'."
        return self._format_city_dashboard(response.data, resolved)
    
    def _route_city_ranking(self, req_id: str) -> str:
        response = self.analytics.get_city_ranking(limit=10, top=True)
        if not self._validate_response(response, "city_ranking", req_id):
            return "❌ Unable to retrieve city ranking."
        return self._format_city_ranking(response.data)
    
    def _route_city_dealers(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        city_name = entity or (context.last_city if context else None)
        if not city_name:
            return "📍 *CITY DEALERS*\n\nPlease specify a city name.\n\n*Example:* Dealers in Haripur"
        
        resolved = self.resolver.resolve_city(city_name)
        if not resolved:
            return f"❌ City '{city_name}' not found."
        
        response = self.analytics.get_city_dealers(resolved)
        if not self._validate_response(response, "city_dealers", req_id):
            return f"❌ Unable to retrieve dealers for '{resolved}'."
        return self._format_city_dealers(response.data, resolved)
    
    def _route_city_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        city_name = entity or (context.last_city if context else None)
        if not city_name:
            return "📦 *CITY PRODUCTS*\n\nPlease specify a city name.\n\n*Example:* Products in Haripur"
        
        resolved = self.resolver.resolve_city(city_name)
        if not resolved:
            return f"❌ City '{city_name}' not found."
        
        response = self.analytics.get_city_products(resolved)
        if not self._validate_response(response, "city_products", req_id):
            return f"❌ Unable to retrieve products for '{resolved}'."
        return self._format_city_products(response.data, resolved)
    
    # ==========================================================
    # PRODUCT ROUTE HANDLERS
    # ==========================================================
    
    def _route_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        product_name = entity or (context.last_product if context else None)
        if not product_name:
            return "📦 *PRODUCT DASHBOARD*\n\nPlease specify a product.\n\n*Examples:*\n• Refrigerator\n• AC\n• TV"
        
        resolved = self.resolver.resolve_product(product_name)
        if not resolved:
            return f"❌ Product '{product_name}' not found."
        
        response = self.analytics.get_product_dashboard(resolved)
        if not self._validate_response(response, "product_dashboard", req_id):
            return f"❌ Unable to retrieve data for product '{resolved}'."
        return self._format_product_dashboard(response.data, resolved)
    
    def _route_product_ranking(self, req_id: str) -> str:
        response = self.analytics.get_product_ranking(limit=10, top=True)
        if not self._validate_response(response, "product_ranking", req_id):
            return "❌ Unable to retrieve product ranking."
        return self._format_product_ranking(response.data)
    
    # ==========================================================
    # DN ROUTE HANDLERS
    # ==========================================================
    
    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        dn_number = entity or (context.last_dn if context else None)
        if not dn_number:
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number.\n\n*Example:* 6243612278"
        
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return f"❌ Invalid DN number: '{dn_number}'\n\nDN numbers must be 8-12 digits."
        
        verify = self.analytics.verify_dn_exists(dn_clean)
        if verify and hasattr(verify, 'success') and verify.success:
            data = verify.data
            if not data.get("found", False):
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
        
        response = self.analytics.get_dn_analytics(dn_clean)
        if not self._validate_response(response, "dn_dashboard", req_id):
            return f"❌ Unable to retrieve data for DN {dn_clean}."
        return self._format_dn_dashboard(response.data, dn_clean)
    
    def _route_dn_analytics(self, req_id: str) -> str:
        try:
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

*Total Dealers: {len(dealers)}*"""
            
        except Exception as e:
            logger.error(f"[{req_id}] DN analytics error: {e}")
            return "❌ Unable to retrieve DN analytics."
    
    # ==========================================================
    # PGI ROUTE HANDLERS
    # ==========================================================
    
    def _route_pgi_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_pgi_dashboard()
        if not self._validate_response(response, "pgi_dashboard", req_id):
            return "❌ Unable to retrieve PGI data."
        return self._format_pgi_dashboard(response.data)
    
    def _route_pgi_by_warehouse(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        warehouse_name = entity or (context.last_warehouse if context else None)
        if not warehouse_name:
            return "🏭 *PGI BY WAREHOUSE*\n\nPlease specify a warehouse name.\n\n*Example:* PGI at Lahore warehouse"
        
        resolved = self.resolver.resolve_warehouse(warehouse_name)
        if not resolved:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        response = self.analytics.get_warehouse_dashboard(resolved)
        if not self._validate_response(response, "pgi_by_warehouse", req_id):
            return f"❌ Unable to retrieve PGI data for '{resolved}'."
        
        summary = response.data.get("summary", {})
        return f"""🏭 *PGI - {resolved}*

• Total DNs: {summary.get('total_dns', 0)}
• PGI Completed: {summary.get('total_dns', 0) - summary.get('pending_dns', 0)}
• PGI Pending: {summary.get('pending_dns', 0)}
• PGI Rate: {summary.get('pgi_rate', 0):.1f}%"""
    
    # ==========================================================
    # POD ROUTE HANDLERS
    # ==========================================================
    
    def _route_pod_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_pod_dashboard()
        if not self._validate_response(response, "pod_dashboard", req_id):
            return "❌ Unable to retrieve POD data."
        return self._format_pod_dashboard(response.data)
    
    def _route_pod_aging(self, req_id: str) -> str:
        response = self.analytics.get_pod_aging_analysis()
        if not self._validate_response(response, "pod_aging", req_id):
            return "❌ Unable to retrieve POD aging data."
        return self._format_pod_aging(response.data)
    
    # ==========================================================
    # DELIVERY ROUTE HANDLERS
    # ==========================================================
    
    def _route_delivery_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_delivery_dashboard()
        if not self._validate_response(response, "delivery_dashboard", req_id):
            return "❌ Unable to retrieve delivery data."
        return self._format_delivery_dashboard(response.data)
    
    # ==========================================================
    # EXECUTIVE ROUTE HANDLERS
    # ==========================================================
    
    def _route_executive_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_executive_dashboard()
        if not self._validate_response(response, "executive_dashboard", req_id):
            return "❌ Unable to retrieve executive data."
        return self._format_executive_dashboard(response.data)
    
    # ==========================================================
    # CONTROL TOWER ROUTE HANDLERS
    # ==========================================================
    
    def _route_control_tower(self, req_id: str) -> str:
        response = self.analytics.get_control_tower_dashboard()
        if not self._validate_response(response, "control_tower", req_id):
            return "❌ Unable to retrieve control tower data."
        return self._format_control_tower(response.data)
    
    # ==========================================================
    # REVENUE ROUTE HANDLERS
    # ==========================================================
    
    def _route_revenue_dashboard(self, req_id: str) -> str:
        response = self.analytics.get_revenue_dashboard()
        if not self._validate_response(response, "revenue_dashboard", req_id):
            return "❌ Unable to retrieve revenue data."
        return self._format_revenue_dashboard(response.data)
    
    # ==========================================================
    # AGING ROUTE HANDLERS
    # ==========================================================
    
    def _route_aging_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        dealer_name = entity or (context.last_dealer if context else None)
        
        if dealer_name:
            resolved = self.resolver.resolve_dealer(dealer_name)
            if resolved:
                response = self.analytics.get_dealer_dn_aging(resolved)
                if self._validate_response(response, "aging_dashboard", req_id):
                    return self._format_dealer_aging(response.data, resolved)
        
        response = self.analytics.get_aging_dashboard()
        if not self._validate_response(response, "aging_dashboard", req_id):
            return "❌ Unable to retrieve aging data."
        return self._format_aging_dashboard(response.data)
    
    # ==========================================================
    # DIVISION ROUTE HANDLERS
    # ==========================================================
    
    def _route_division_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        division_name = entity or (context.last_division if context else None)
        if not division_name:
            return "📊 *DIVISION DASHBOARD*\n\nPlease specify a division name.\n\n*Example:* Division Electronics"
        
        response = self.analytics.get_revenue_by_division(division_name)
        if not self._validate_response(response, "division_dashboard", req_id):
            return f"❌ Unable to retrieve data for division '{division_name}'."
        
        data = response.data
        return f"""📊 *DIVISION DASHBOARD*

Division: {data.get('division', division_name)}
Revenue: PKR {data.get('total_revenue', 0):,.0f}
Units: {data.get('total_units', 0):,}
DNs: {data.get('total_dns', 0)}"""
    
    # ==========================================================
    # SALES MANAGER ROUTE HANDLERS
    # ==========================================================
    
    def _route_sales_manager_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        sm_name = entity or (context.last_sales_manager if context else None)
        if not sm_name:
            return "👤 *SALES MANAGER DASHBOARD*\n\nPlease specify a sales manager name.\n\n*Example:* Sales Manager Ali"
        
        try:
            session = self.session_factory() if self.session_factory else None
            if not session:
                return "❌ Unable to connect to database."
            
            from app.models import DeliveryReport
            
            result = session.query(
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue")
            ).filter(
                DeliveryReport.sales_manager.ilike(f"%{sm_name}%")
            ).first()
            
            session.close()
            
            if not result or result.total_dns == 0:
                return f"❌ No data found for sales manager '{sm_name}'."
            
            return f"""👤 *SALES MANAGER DASHBOARD*

Sales Manager: {sm_name}
Total DNs: {result.total_dns or 0:,}
Total Units: {result.total_units or 0:,}
Total Revenue: PKR {result.total_revenue or 0:,.0f}"""
            
        except Exception as e:
            logger.error(f"[{req_id}] Sales manager error: {e}")
            return "❌ Unable to retrieve sales manager data."
    
    # ==========================================================
    # DISTANCE ROUTE HANDLERS
    # ==========================================================
    
    def _route_distance_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        dealer_name = entity or (context.last_dealer if context else None)
        city_name = entity or (context.last_city if context else None)
        
        if not dealer_name and not city_name:
            return "📍 *DISTANCE DASHBOARD*\n\nPlease specify a dealer or city.\n\n*Examples:*\n• Distance for ZQ Electronics\n• Distance to Haripur"
        
        try:
            if dealer_name:
                response = self.analytics.get_dealer_dashboard(dealer_name)
                if response and hasattr(response, 'success') and response.success:
                    dealer_data = response.data
                    city_name = dealer_data.get("profile", {}).get("city")
            
            if city_name:
                city_name = city_name.strip().lower()
                import math
                
                warehouse_coords = {
                    "lahore": (31.5204, 74.3587),
                    "karachi": (24.8607, 67.0011),
                    "rawalpindi": (33.5651, 73.0169),
                    "faisalabad": (31.4504, 73.1350),
                    "multan": (30.1575, 71.5249),
                    "hyderabad": (25.3960, 68.3578),
                    "peshawar": (34.0151, 71.5249),
                    "quetta": (30.1798, 66.9750),
                    "islamabad": (33.6844, 73.0479),
                    "gujranwala": (32.1877, 74.1945),
                    "sialkot": (32.4945, 74.5227),
                    "haripur": (34.0000, 72.9333),
                    "abbottabad": (34.1558, 73.2153),
                    "mansehra": (34.3300, 73.2000),
                }
                
                if city_name in warehouse_coords:
                    city_coords = warehouse_coords[city_name]
                    distances = []
                    for wh_name, wh_coords in warehouse_coords.items():
                        if wh_name != city_name:
                            dist = math.sqrt(
                                (wh_coords[0] - city_coords[0]) ** 2 + 
                                (wh_coords[1] - city_coords[1]) ** 2
                            ) * 111
                            distances.append((wh_name, dist))
                    
                    distances.sort(key=lambda x: x[1])
                    nearest = distances[0] if distances else None
                    
                    if nearest:
                        return f"""📍 *DISTANCE INFO*

City: {city_name.title()}
Nearest Warehouse: {nearest[0].title()}
Estimated Distance: {nearest[1]:.1f} km

*Business Impact:*
• Shorter distance = faster delivery
• Reduced transportation costs
• Better service levels"""
            
            return f"📍 *DISTANCE INFO*\n\nDistance data for {city_name or dealer_name} is being processed.\n\n💡 For accurate distance, please ensure the dealer/city exists in our system."
            
        except Exception as e:
            logger.error(f"[{req_id}] Distance error: {e}")
            return "📍 Distance calculation is currently unavailable."
    
    # ==========================================================
    # RESPONSE VALIDATION
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
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _truncate_response(self, response: str) -> str:
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
        try:
            dealers = data.get("ranking", []) or data.get("dealers", [])
            if not dealers:
                return "❌ No dealer data available."
            
            lines = ["🏆 *DEALER RANKING*", "", "Top 10 Dealers by Revenue:"]
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
        try:
            products = data.get("products", [])
            if not products:
                return f"📦 No products found for {dealer_name}"
            
            lines = [f"📦 *PRODUCTS - {dealer_name}*", ""]
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
        try:
            warehouses = data.get("ranking", []) or data.get("warehouses", [])
            if not warehouses:
                return "❌ No warehouse data available."
            
            lines = ["🏆 *WAREHOUSE RANKING*", "", "Top 10 Warehouses by Revenue:"]
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
        try:
            cities = data.get("ranking", []) or data.get("cities", [])
            if not cities:
                return "❌ No city data available."
            
            lines = ["🏆 *CITY RANKING*", "", "Top 10 Cities by Revenue:"]
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
    
    def _format_city_dealers(self, data: Dict, city_name: str) -> str:
        try:
            dealers = data.get("dealers", [])
            if not dealers:
                return f"📍 No dealers found in {city_name}"
            
            lines = [f"📍 *DEALERS IN {city_name.upper()}*", ""]
            for i, dealer in enumerate(dealers[:10], 1):
                name = dealer.get("dealer", "Unknown")
                revenue = dealer.get("revenue", 0)
                lines.append(f"{i}. {name}")
                lines.append(f"   Revenue: PKR {revenue:,.0f}")
            if len(dealers) > 10:
                lines.append(f"\n*+ {len(dealers) - 10} more dealers*")
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"City dealers format error: {e}")
            return f"❌ Unable to format dealers for {city_name}"
    
    def _format_city_products(self, data: Dict, city_name: str) -> str:
        try:
            products = data.get("products", [])
            if not products:
                return f"📦 No products found in {city_name}"
            
            lines = [f"📦 *PRODUCTS IN {city_name.upper()}*", ""]
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
            logger.error(f"City products format error: {e}")
            return f"❌ Unable to format products for {city_name}"
    
    def _format_product_dashboard(self, data: Dict, product_name: str) -> str:
        try:
            summary = data.get("summary", {})
            top_dealers = data.get("top_dealers", [])
            trend = data.get("trend", [])
            
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
                f"Warehouses: {summary.get('warehouses', 0)}",
                f"Divisions: {summary.get('divisions', 0)}",
                "",
                "🏆 *Top Dealers*"
            ]
            
            for i, dealer in enumerate(top_dealers[:5], 1):
                name = dealer.get("dealer", "Unknown")
                revenue = dealer.get("revenue", 0)
                lines.append(f"   {i}. {name}: PKR {revenue:,.0f}")
            
            if trend:
                lines.append("")
                lines.append("📈 *Trend (Last 6 Months)*")
                for t in trend[-3:]:
                    lines.append(f"   • {t.get('month', 'N/A')}: PKR {t.get('revenue', 0):,.0f}")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Product format error: {e}")
            return f"❌ Unable to format product dashboard for {product_name}"
    
    def _format_product_ranking(self, data: Dict) -> str:
        try:
            products = data.get("ranking", []) or data.get("products", [])
            if not products:
                return "❌ No product data available."
            
            lines = ["🏆 *PRODUCT RANKING*", "", "Top 10 Products by Revenue:"]
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
        try:
            record = data.get("record", {})
            status = data.get("status", "unknown")
            aging = data.get("aging", {})
            validation = data.get("validation", {})
            timeline = data.get("timeline", {})
            
            dn_no = record.get('dn_number', dn_number)
            dealer_name = record.get('customer_name', 'N/A')
            warehouse = record.get('warehouse', 'N/A')
            city = record.get('ship_to_city', 'N/A')
            sales_office = record.get('sales_office', 'N/A')
            sales_manager = record.get('sales_manager', 'N/A')
            division = record.get('division', 'N/A')
            customer_model = record.get('customer_model', 'N/A')
            material_no = record.get('material_no', 'N/A')
            units = record.get('units', 0)
            amount = record.get('amount', 0)
            dealer_code = record.get('dealer_code', 'N/A')
            customer_code = record.get('customer_code', 'N/A')
            delivery_status = record.get('delivery_status', 'N/A')
            pgi_status = record.get('pgi_status', 'N/A')
            pod_status = record.get('pod_status', 'N/A')
            pending_flag = record.get('pending_flag', False)
            
            status_emoji = "✅" if status == "delivered" else "🚚" if status == "in_transit" else "⏳"
            status_display = status.upper().replace("_", " ")
            pending_text = "🔴 Yes" if pending_flag else "🟢 No"
            
            lines = [
                "📄 *DN TRACKING*",
                "",
                f"DN No: {dn_no}",
                f"Dealer: {dealer_name}",
                f"Dealer Code: {dealer_code}",
                f"Customer Code: {customer_code}",
                f"Warehouse: {warehouse}",
                f"City: {city}",
                f"Sales Office: {sales_office}",
                f"Sales Manager: {sales_manager}",
                f"Division: {division}",
                "",
                "📦 *Products*",
                f"Model: {customer_model}",
                f"Material: {material_no}",
                "",
                "📊 *Metrics*",
                f"Units: {units}",
                f"Revenue: PKR {amount:,.0f}",
                "",
                "📅 *Dates*",
                f"Create: {timeline.get('created', 'N/A')}",
                f"PGI: {timeline.get('pgi', 'N/A')}",
                f"POD: {timeline.get('pod', 'N/A')}",
                "",
                "⏳ *Aging*",
                f"Total: {aging.get('total_aging_days', 0)} days",
                f"PGI Aging: {aging.get('pgi_aging_days', 0)} days",
                f"POD Aging: {aging.get('pod_aging_days', 0)} days",
                "",
                "📋 *Status*",
                f"Delivery: {delivery_status} {status_emoji}",
                f"PGI: {pgi_status}",
                f"POD: {pod_status}",
                f"Pending: {pending_text}",
                "",
                "✅ *Validation*"
            ]
            
            if validation.get("issues"):
                lines.append("🔴 Issues Found:")
                for issue in validation.get("issues", [])[:3]:
                    lines.append(f"   • {issue}")
            else:
                lines.append("✅ No issues found")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"DN format error: {e}")
            return f"❌ Unable to format DN details for {dn_number}"
    
    def _format_pgi_dashboard(self, data: Dict) -> str:
        try:
            summary = data.get("summary", {})
            by_dealer = data.get("by_dealer", [])
            
            lines = [
                "📋 *PGI DASHBOARD*",
                "",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"PGI Completed: {summary.get('pgi_completed', 0):,}",
                f"PGI Pending: {summary.get('pgi_pending', 0):,}",
                f"In Transit: {summary.get('in_transit', 0):,}",
                f"PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"PGI Aging > 7 Days: {summary.get('pgi_aging_gt_7', 0)}",
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
        try:
            summary = data.get("summary", {})
            aging = data.get("aging", {})
            by_dealer = data.get("by_dealer", [])
            
            lines = [
                "✅ *POD DASHBOARD*",
                "",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"Delivered DNs: {summary.get('delivered_dns', 0):,}",
                f"POD Completed: {summary.get('pod_completed', 0):,}",
                f"POD Pending: {summary.get('pod_pending', 0):,}",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                f"POD Aging > 7 Days: {summary.get('pod_aging_gt_7', 0)}",
                "",
                f"Avg POD Days: {summary.get('avg_pod_days', 0):.1f}",
                "",
                "⏳ *Aging*",
                f"0-7 Days: {aging.get('days_0_7', 0)}",
                f"8-14 Days: {aging.get('days_8_14', 0)}",
                f"15-30 Days: {aging.get('days_15_30', 0)}",
                f"30+ Days: {aging.get('days_30_plus', 0)}",
                f"Total Pending: {aging.get('total_pending', 0)}",
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
    
    def _format_pod_aging(self, data: Dict) -> str:
        try:
            aging = data.get("aging", {})
            critical = data.get("critical", [])
            
            lines = [
                "⏳ *POD AGING ANALYSIS*",
                "",
                f"Total Pending: {aging.get('total_pending', 0)}",
                f"0-7 Days: {aging.get('days_0_7', 0)}",
                f"8-14 Days: {aging.get('days_8_14', 0)}",
                f"15-30 Days: {aging.get('days_15_30', 0)}",
                f"30+ Days: {aging.get('days_30_plus', 0)}",
                "",
                f"Avg Aging: {aging.get('avg_aging_days', 0):.1f} days",
                f"Max Aging: {aging.get('max_aging_days', 0)} days",
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
            logger.error(f"POD aging format error: {e}")
            return "❌ Unable to format POD aging"
    
    def _format_delivery_dashboard(self, data: Dict) -> str:
        try:
            metrics = data.get("metrics", {})
            
            lines = [
                "🚚 *DELIVERY DASHBOARD*",
                "",
                f"Total DNs: {metrics.get('total_dns', 0):,}",
                f"Delivered: {metrics.get('delivered', 0):,}",
                f"In Transit: {metrics.get('in_transit', 0):,}",
                f"Pending PGI: {metrics.get('pending_pgi', 0):,}",
                f"Pending: {metrics.get('pending', 0):,}",
                f"Delayed > 7 Days: {metrics.get('delayed_gt_7', 0)}",
                "",
                f"Delivery Rate: {metrics.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {metrics.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {metrics.get('pod_rate', 0):.1f}%",
                "",
                f"Avg Processing: {metrics.get('avg_processing_days', 0):.1f} days",
                f"Avg Delivery: {metrics.get('avg_delivery_days', 0):.1f} days"
            ]
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Delivery format error: {e}")
            return "❌ Unable to format delivery dashboard"
    
    def _format_executive_dashboard(self, data: Dict) -> str:
        try:
            summary = data.get("summary", {})
            health_score = data.get("health_score", 0)
            risk_level = data.get("risk_level", "low")
            top_dealers = data.get("top_dealers", [])
            top_warehouses = data.get("top_warehouses", [])
            top_cities = data.get("top_cities", [])
            top_products = data.get("top_products", [])
            
            health_emoji = "✅" if health_score >= 80 else "⚠️" if health_score >= 60 else "🔴"
            health_status = "Healthy" if health_score >= 80 else "Needs Attention" if health_score >= 60 else "Critical"
            risk_emoji = self._get_risk_emoji(risk_level)
            
            lines = [
                "👔 *EXECUTIVE DASHBOARD*",
                "",
                "💰 *Business*",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"Units: {summary.get('total_units', 0):,}",
                f"DNs: {summary.get('total_dns', 0):,}",
                f"Dealers: {summary.get('total_dealers', 0):,}",
                f"Warehouses: {summary.get('total_warehouses', 0)}",
                f"Cities: {summary.get('total_cities', 0)}",
                f"Products: {summary.get('total_products', 0)}",
                "",
                "📈 *KPI*",
                f"Delivery: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI: {summary.get('pgi_rate', 0):.1f}%",
                f"POD: {summary.get('pod_rate', 0):.1f}%",
                f"Avg PGI Aging: {summary.get('avg_pgi_aging', 0):.1f} days",
                f"Avg POD Aging: {summary.get('avg_pod_aging', 0):.1f} days",
            ]
            
            if top_dealers:
                lines.append("")
                lines.append("🏆 *Top Dealer*")
                top = top_dealers[0] if top_dealers else {}
                lines.append(f"   • {top.get('dealer', 'N/A')}: PKR {top.get('revenue', 0):,.0f}")
            
            if top_warehouses:
                lines.append("")
                lines.append("🏭 *Top Warehouse*")
                top = top_warehouses[0] if top_warehouses else {}
                lines.append(f"   • {top.get('warehouse', 'N/A')}: PKR {top.get('revenue', 0):,.0f}")
            
            if top_cities:
                lines.append("")
                lines.append("🏙️ *Top City*")
                top = top_cities[0] if top_cities else {}
                lines.append(f"   • {top.get('city', 'N/A')}: PKR {top.get('revenue', 0):,.0f}")
            
            if top_products:
                lines.append("")
                lines.append("📦 *Top Product*")
                top = top_products[0] if top_products else {}
                lines.append(f"   • {top.get('product', 'N/A')}: PKR {top.get('revenue', 0):,.0f}")
            
            lines.append("")
            lines.append("📊 *Health & Risk*")
            lines.append(f"Health Score: {health_emoji} {health_score}/100 - {health_status}")
            lines.append(f"Risk Level: {risk_emoji} {risk_level.upper()} (Score: {data.get('risk_score', 0)})")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Executive format error: {e}")
            return "👔 Unable to format executive dashboard"
    
    def _format_control_tower(self, data: Dict) -> str:
        try:
            alerts = data.get("alerts", [])
            critical_count = data.get("critical_count", 0)
            high_count = data.get("high_count", 0)
            medium_count = data.get("medium_count", 0)
            
            lines = [
                "🚨 *LOGISTICS CONTROL TOWER*",
                "",
                f"Critical Alerts: {critical_count}",
                f"High Priority: {high_count}",
                f"Medium Priority: {medium_count}",
                f"Total Alerts: {len(alerts)}",
                ""
            ]
            
            if alerts:
                lines.append("*Recent Alerts:*")
                for alert in alerts[:5]:
                    severity = alert.get("severity", "low").upper()
                    severity_emoji = "🔴" if severity == "CRITICAL" else "🟠" if severity == "HIGH" else "🟡"
                    desc = alert.get('description', 'Alert')
                    if len(desc) > 60:
                        desc = desc[:57] + "..."
                    lines.append(f"   {severity_emoji} {desc}")
                if len(alerts) > 5:
                    lines.append(f"   *+ {len(alerts) - 5} more alerts*")
            else:
                lines.append("✅ No alerts at this time")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Control tower format error: {e}")
            return "🚨 Unable to format control tower"
    
    def _format_revenue_dashboard(self, data: Dict) -> str:
        try:
            trend = data.get("trend", [])
            overall_growth = data.get("overall_growth", 0)
            avg_monthly = data.get("avg_monthly_revenue", 0)
            total_revenue = data.get("total_revenue", 0)
            by_division = data.get("by_division", [])
            top_dealers = data.get("top_dealers", [])
            top_warehouses = data.get("top_warehouses", [])
            top_cities = data.get("top_cities", [])
            
            growth_emoji = "📈" if overall_growth >= 0 else "📉"
            
            lines = [
                "💰 *REVENUE DASHBOARD*",
                "",
                f"Total Revenue: PKR {total_revenue:,.0f}",
                f"Overall Growth: {growth_emoji} {overall_growth:.1f}%",
                f"Avg Monthly Revenue: PKR {avg_monthly:,.0f}",
                f"Total Months: {data.get('total_months', 0)}",
                "",
                "📈 *Monthly Trend:*"
            ]
            
            for period in trend[-6:]:
                month = period.get("month", "N/A")
                revenue = period.get("revenue", 0)
                lines.append(f"   • {month}: PKR {revenue:,.0f}")
            
            if by_division:
                lines.append("")
                lines.append("📊 *Top Divisions:*")
                for div in by_division[:3]:
                    lines.append(f"   • {div.get('division', 'N/A')}: PKR {div.get('revenue', 0):,.0f}")
            
            if top_dealers:
                lines.append("")
                lines.append("🏆 *Top Revenue Dealers:*")
                for d in top_dealers[:2]:
                    lines.append(f"   • {d.get('dealer', 'N/A')}: PKR {d.get('revenue', 0):,.0f}")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Revenue format error: {e}")
            return "💰 Unable to format revenue dashboard"
    
    def _format_dealer_aging(self, data: Dict, dealer_name: str) -> str:
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
        try:
            aging = data.get("aging", {})
            critical_dealers = data.get("critical_dealers", [])
            oldest_pending = data.get("oldest_pending", {})
            
            lines = [
                "⏳ *AGING ANALYSIS*",
                "",
                f"0-7 Days: {aging.get('days_0_7', 0)}",
                f"8-14 Days: {aging.get('days_8_14', 0)}",
                f"15-30 Days: {aging.get('days_15_30', 0)}",
                f"30+ Days: {aging.get('days_30_plus', 0)}",
                "",
                f"Total Pending: {aging.get('total_pending', 0)}",
                f"Avg Aging: {aging.get('avg_aging_days', 0):.1f} days",
                f"Max Aging: {aging.get('max_aging_days', 0)} days",
            ]
            
            if oldest_pending:
                lines.append("")
                lines.append("🔴 *Oldest Pending DN*")
                lines.append(f"   • {oldest_pending.get('dn_no', 'N/A')} - {oldest_pending.get('dealer', 'Unknown')} ({oldest_pending.get('days_old', 0)} days)")
            
            if critical_dealers:
                lines.append("")
                lines.append("🏢 *Critical Dealers (30+ days)*")
                for d in critical_dealers[:5]:
                    lines.append(f"   • {d.get('dealer', 'Unknown')}: {d.get('count', 0)} DNs, Max {d.get('max_days', 0)} days")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Aging format error: {e}")
            return "❌ Unable to format aging dashboard"
    
    # ==========================================================
    # HELP MESSAGE
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
# SINGLETON
# ==========================================================

_orchestrator = None

def get_orchestrator(session_factory: Optional[Callable[[], Session]] = None) -> AIOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        try:
            _orchestrator = AIOrchestrator(session_factory)
            logger.info("✅ AI Orchestrator v24.0 initialized")
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
# EXPORTS
# ==========================================================

__all__ = [
    'AIOrchestrator',
    'PostgreSQLResolver',
    'ConversationContext',
    'get_orchestrator',
    'process_whatsapp_query',
]

# ==========================================================
# END OF FILE - v24.0 PRODUCTION READY
# ==========================================================
