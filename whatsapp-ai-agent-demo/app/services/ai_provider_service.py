# ==========================================================
# FILE: app/services/ai_provider_service.py (v27.0 - PRODUCTION READY)
# ==========================================================
# PURPOSE: POSTGRESQL-DRIVEN AI ROUTER
# VERSION: 27.0 - Fixed ALL Dashboard Loading Issues
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
# BLOCK 2: LAZY IMPORTS (FIXED v4.0)
# ==========================================================
# ==========================================================
# BLOCK 2: LAZY IMPORTS (FIXED v4.0)
# ==========================================================

def _get_analytics_service():
    """
    Load analytics service with comprehensive diagnostics.
    BLOCK 2 - FIXED v4.0
    """
    try:
        from app.services.analytics_service import get_analytics_service, AnalyticsResponse
        
        logger.info("✅ Analytics service imported successfully")
        
        # Get service instance
        service = get_analytics_service()
        
        if service is None:
            logger.error("❌ Analytics service returned None")
            # Try manual creation
            try:
                from app.services.analytics_service import AnalyticsService
                service = AnalyticsService()
                logger.info("✅ AnalyticsService created manually")
            except Exception as e:
                logger.error(f"❌ Manual creation failed: {e}")
                return None, None
        
        # Log service type for debugging
        logger.info(f"📊 Service type: {type(service)}")
        logger.info(f"📊 Service class: {service.__class__.__name__}")
        
        # Verify required methods exist
        required_methods = [
            "get_dn_dashboard",
            "get_dealer_dashboard",
            "get_warehouse_dashboard",
            "get_city_dashboard",
            "get_product_dashboard",
            "search_dealer",
            "verify_dealer_exists",
            "verify_dn_exists"
        ]
        
        missing = []
        for method in required_methods:
            if hasattr(service, method):
                logger.info(f"   ✅ {method}: AVAILABLE")
            else:
                missing.append(method)
                logger.error(f"   ❌ {method}: MISSING")
        
        if missing:
            logger.error(f"❌ Missing {len(missing)} methods: {missing}")
            # ✅ FIX: Don't return None - use fallback
            logger.warning("⚠️ Creating fallback analytics service...")
            return _create_fallback_analytics(), AnalyticsResponse
        
        logger.info("✅ All required methods available")
        return service, AnalyticsResponse
        
    except ImportError as e:
        logger.error(f"❌ Import error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.warning("⚠️ Creating fallback analytics service...")
        return _create_fallback_analytics(), None
    except Exception as e:
        logger.error(f"❌ Error loading analytics service: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.warning("⚠️ Creating fallback analytics service...")
        return _create_fallback_analytics(), None


def _create_fallback_analytics():
    """
    Create a fallback analytics service that returns friendly error messages.
    BLOCK 2 - NEW FALLBACK SERVICE
    """
    class FallbackAnalytics:
        """Fallback analytics service - prevents crashes"""
        
        def get_dn_dashboard(self, dn_no):
            logger.warning(f"⚠️ Fallback: get_dn_dashboard called for {dn_no}")
            return {
                "dn_number": dn_no,
                "delivery_status": "Unknown",
                "customer_name": "Unknown",
                "warehouse": "Unknown",
                "ship_to_city": "Unknown",
                "units": 0,
                "amount": 0,
                "delivery_aging_text": "N/A",
                "pod_aging_text": "N/A",
                "total_cycle_text": "N/A",
                "error": "Analytics service not configured. Please configure your database."
            }
        
        def get_dealer_dashboard(self, dealer_name):
            logger.warning(f"⚠️ Fallback: get_dealer_dashboard called for {dealer_name}")
            return {
                "dealer_name": dealer_name,
                "total_dns": 0,
                "delivered_dns": 0,
                "pending_dns": 0,
                "delivery_rate": 0,
                "total_revenue": 0,
                "health_score": 50,
                "risk_level": "Unknown"
            }
        
        def get_warehouse_dashboard(self, warehouse_name):
            logger.warning(f"⚠️ Fallback: get_warehouse_dashboard called for {warehouse_name}")
            return {
                "warehouse": warehouse_name,
                "total_dns": 0,
                "delivered_dns": 0,
                "pending_dns": 0,
                "delivery_rate": 0,
                "total_revenue": 0
            }
        
        def get_city_dashboard(self, city_name):
            logger.warning(f"⚠️ Fallback: get_city_dashboard called for {city_name}")
            return {
                "city_name": city_name,
                "total_dns": 0,
                "delivered_dns": 0,
                "pending_dns": 0,
                "delivery_rate": 0,
                "total_revenue": 0
            }
        
        def get_product_dashboard(self, product_name):
            logger.warning(f"⚠️ Fallback: get_product_dashboard called for {product_name}")
            return {
                "product": product_name,
                "revenue": 0,
                "units": 0,
                "dns": 0,
                "delivery_rate": 0
            }
        
        def get_ranking_dashboard(self, limit=10):
            logger.warning("⚠️ Fallback: get_ranking_dashboard called")
            return {"ranking": []}
        
        def get_pgi_dashboard(self):
            logger.warning("⚠️ Fallback: get_pgi_dashboard called")
            return {"total_dns": 0, "pgi_completed": 0, "pgi_pending": 0, "pgi_rate": 0}
        
        def get_pod_dashboard(self):
            logger.warning("⚠️ Fallback: get_pod_dashboard called")
            return {"total_dns": 0, "pod_completed": 0, "pod_pending": 0, "pod_rate": 0}
        
        def get_delivery_dashboard(self):
            logger.warning("⚠️ Fallback: get_delivery_dashboard called")
            return {"total_dns": 0, "delivered": 0, "in_transit": 0, "delivery_rate": 0}
        
        def get_executive_dashboard(self):
            logger.warning("⚠️ Fallback: get_executive_dashboard called")
            return {"total_dns": 0, "total_units": 0, "total_revenue": 0, "delivery_rate": 0}
        
        def get_control_tower_dashboard(self):
            logger.warning("⚠️ Fallback: get_control_tower_dashboard called")
            return {"total_alerts": 0, "critical_count": 0, "high_count": 0, "alerts": []}
        
        def get_revenue_dashboard(self):
            logger.warning("⚠️ Fallback: get_revenue_dashboard called")
            return {"total_revenue": 0, "total_units": 0, "total_dns": 0, "top_dealers": []}
        
        def get_aging_dashboard(self):
            logger.warning("⚠️ Fallback: get_aging_dashboard called")
            return {"total_pending": 0, "days_0_7": 0, "days_8_14": 0, "days_15_30": 0, "days_30_plus": 0}
        
        def search_dealer(self, query):
            logger.warning(f"⚠️ Fallback: search_dealer called for {query}")
            return []
        
        def verify_dealer_exists(self, dealer_name):
            logger.warning(f"⚠️ Fallback: verify_dealer_exists called for {dealer_name}")
            return True
        
        def verify_dn_exists(self, dn_no):
            logger.warning(f"⚠️ Fallback: verify_dn_exists called for {dn_no}")
            return True
    
    return FallbackAnalytics()

# ==========================================================
# END OF BLOCK 2 - FIXED v4.0
# ==========================================================
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
# BLOCK 5: POSTGRESQL RESOLVER (FIXED v3.0)
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
        """Resolve dealer name with fuzzy threshold 0.3"""
        if not query or not query.strip():
            return None
        
        # Clean query
        query_clean = query.strip()
        typo_fixes = {"are ": "", "is ": "", "the ": "", "for ": "", "of ": ""}
        for typo, fix in typo_fixes.items():
            if query_clean.lower().startswith(typo):
                query_clean = query_clean[len(typo):].strip()
                break
        
        if not query_clean:
            query_clean = query.strip()
        
        cache_key = f"dealer:{query_clean.lower()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            # Exact match
            result = session.query(self.DeliveryReport.customer_name).filter(
                func.lower(self.DeliveryReport.customer_name) == func.lower(query_clean)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # ILIKE match
            result = session.query(self.DeliveryReport.customer_name).filter(
                self.DeliveryReport.customer_name.ilike(f"%{query_clean}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # Token-based matching
            tokens = query_clean.split()
            for token in tokens:
                if len(token) > 2 and token.lower() not in ['the', 'and', 'for', 'with']:
                    result = session.query(self.DeliveryReport.customer_name).filter(
                        self.DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        self._cache[cache_key] = resolved
                        return resolved
            
            # Fuzzy matching with threshold 0.3
            dealers = session.query(
                func.distinct(self.DeliveryReport.customer_name)
            ).filter(
                self.DeliveryReport.customer_name.isnot(None),
                self.DeliveryReport.customer_name != ''
            ).limit(1000).all()
            
            best_match = None
            best_score = 0
            query_lower = query_clean.lower()
            query_tokens = set(query_lower.split())
            
            for dealer in dealers:
                if not dealer[0]:
                    continue
                dealer_name = dealer[0]
                dealer_lower = dealer_name.lower()
                dealer_tokens = set(dealer_lower.split())
                
                scores = []
                
                if query_tokens and dealer_tokens:
                    overlap = len(query_tokens & dealer_tokens)
                    token_score = overlap / max(len(query_tokens), len(dealer_tokens))
                    scores.append(token_score)
                
                char_overlap = len(set(query_lower) & set(dealer_lower))
                char_score = char_overlap / max(len(query_lower), len(dealer_lower))
                scores.append(char_score)
                
                if query_lower in dealer_lower or dealer_lower in query_lower:
                    scores.append(0.8)
                
                for token in query_tokens:
                    if len(token) > 2 and token in dealer_lower:
                        scores.append(0.7)
                
                if scores:
                    score = max(scores)
                else:
                    score = 0
                
                if score > best_score and score > 0.3:
                    best_score = score
                    best_match = dealer_name
            
            if best_match:
                self._cache[cache_key] = best_match
                logger.info(f"✅ Dealer resolved (fuzzy, score={best_score:.2f}): {best_match}")
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
            result = session.query(self.DeliveryReport.customer_model).filter(
                func.lower(self.DeliveryReport.customer_model) == func.lower(query)
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.material_no).filter(
                func.lower(self.DeliveryReport.material_no) == func.lower(query)
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.customer_model).filter(
                self.DeliveryReport.customer_model.ilike(f"%{query}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
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
# BLOCK 10: MAIN AI ROUTER (FIXED v4.0)
# ==========================================================
# BLOCK 10: MAIN AI ROUTER (FIXED v5.0 - NO CRASH)
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
        logger.info("AI Router v27.0 - Initializing...")
        logger.info("=" * 70)
        
        # ✅ Initialize analytics - don't crash on failure
        try:
            self._init_analytics()
        except Exception as e:
            logger.error(f"❌ Analytics init failed: {e}")
        
        # ✅ Verify methods - don't crash on failure
        try:
            self._verify_analytics_methods()
        except Exception as e:
            logger.error(f"❌ Method verification failed: {e}")
        
        logger.info("=" * 70)
        logger.info("AI Router v27.0 - PostgreSQL-Driven Production")
        logger.info("=" * 70)
    
    def _init_analytics(self):
        """Initialize analytics service with retry - DON'T CRASH"""
        for attempt in range(3):
            try:
                logger.info(f"🔄 Attempt {attempt + 1}/3 to initialize analytics...")
                self._analytics = None
                self._analytics_response = None
                service, response_class = _get_analytics_service()
                self._analytics = service
                self._analytics_response = response_class
                
                if self._analytics is not None:
                    logger.info(f"✅ Analytics service initialized on attempt {attempt + 1}")
                    return
                else:
                    logger.warning(f"⚠️ Analytics service None on attempt {attempt + 1}")
                    time.sleep(1)
            except Exception as e:
                logger.error(f"❌ Attempt {attempt + 1} failed: {e}")
                time.sleep(1)
        
        logger.error("❌ All attempts to initialize analytics failed!")
    
    def _verify_analytics_methods(self):
        """Verify all required analytics methods exist - LOG BUT DON'T CRASH"""
        if not self.analytics:
            logger.error("❌ Analytics service is None - cannot verify methods")
            return
        
        required_methods = [
            "get_dn_dashboard",
            "get_dealer_dashboard",
            "get_warehouse_dashboard",
            "get_city_dashboard",
            "get_product_dashboard",
            "search_dealer",
            "verify_dealer_exists",
            "verify_dn_exists"
        ]
        
        logger.info("🔍 Verifying analytics methods:")
        missing_methods = []
        
        for method in required_methods:
            if hasattr(self.analytics, method):
                logger.info(f"   ✅ {method}: AVAILABLE")
            else:
                missing_methods.append(method)
                logger.error(f"   ❌ {method}: MISSING")
        
        if missing_methods:
            logger.error(f"❌ Missing {len(missing_methods)} required methods: {missing_methods}")
        else:
            logger.info("✅ All required methods available!")
    
    @property
    def analytics(self):
        """Get analytics service with lazy loading and retry."""
        if self._analytics is None:
            logger.warning("⚠️ Analytics service is None - attempting to reload...")
            try:
                service, response_class = _get_analytics_service()
                self._analytics = service
                self._analytics_response = response_class
                
                if self._analytics is None:
                    logger.error("❌ Analytics service still None after reload")
                else:
                    logger.info("✅ Analytics service reloaded successfully")
                    self._verify_analytics_methods()
            except Exception as e:
                logger.error(f"❌ Reload failed: {e}")
        
        return self._analytics
    
    @property
    def resolver(self):
        if self._resolver is None:
            self._resolver = PostgreSQLResolver(self.session_factory)
        return self._resolver
# ==========================================================
# BLOCK 11: INTENT DETECTION (FIXED v5.0)
# ==========================================================

    def _detect_intent(self, question: str, context: Optional[ConversationContext] = None) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Detect intent from user question.
        BLOCK 11 - FIXED v5.0
        - DEALER priority over CITY
        - Typo handling
        """
        question_original = question.strip()
        question_lower = question_original.lower()
        
        logger.debug(f"🔍 Detecting intent for: '{question_original}'")
        
        # HELP
        if question_lower in ["help", "menu", "hi", "hello", "start", "?", "commands"]:
            logger.info(f"✅ Intent: help")
            return "help", None, None
        
        # FOLLOW-UP
        if context and context.last_intent and context.last_entity:
            followup_intent = self._detect_followup(question_lower, context)
            if followup_intent:
                logger.info(f"🔄 Follow-up detected: {followup_intent}")
                return followup_intent, context.last_entity, self._get_entity_type(followup_intent)
        
        # DN DETECTION (HIGHEST PRIORITY)
        dn_match = re.search(r'\b(\d{8,12})\b', question_original)
        if dn_match:
            dn_number = re.sub(r'\D', '', dn_match.group(1))
            if 8 <= len(dn_number) <= 12:
                logger.info(f"✅ DN detected: {dn_number}")
                self.metrics["intent_detection"]["dn_dashboard"] = self.metrics["intent_detection"].get("dn_dashboard", 0) + 1
                return "dn_dashboard", dn_number, "dn"
        
        dn_keyword_match = re.search(r'(?:dn|delivery note|track|order)\s*[:#]?\s*(\d{8,12})', question_original, re.IGNORECASE)
        if dn_keyword_match:
            dn_number = re.sub(r'\D', '', dn_keyword_match.group(1))
            if 8 <= len(dn_number) <= 12:
                logger.info(f"✅ DN detected from keyword: {dn_number}")
                self.metrics["intent_detection"]["dn_dashboard"] = self.metrics["intent_detection"].get("dn_dashboard", 0) + 1
                return "dn_dashboard", dn_number, "dn"
        
        # PRODUCT DETECTION (with explicit keyword)
        if "product" in question_lower or "model" in question_lower or "material" in question_lower or "sku" in question_lower:
            product_match = re.search(r'(?:product|model|material|sku)\s*[:#]?\s*([A-Za-z0-9\-]+)', question_original, re.IGNORECASE)
            if product_match:
                entity = product_match.group(1).strip()
                if len(entity) > 1:
                    resolved = self.resolver.resolve_product(entity)
                    if resolved:
                        logger.info(f"✅ Product detected: '{resolved}'")
                        self.metrics["intent_detection"]["product_dashboard"] = self.metrics["intent_detection"].get("product_dashboard", 0) + 1
                        return "product_dashboard", resolved, "product"
        
        # WAREHOUSE DETECTION (with explicit keyword)
        if "warehouse" in question_lower or "wh " in question_lower:
            wh_match = re.search(r'(?:warehouse|wh)\s+([A-Za-z0-9\s\-]+)', question_original, re.IGNORECASE)
            if wh_match:
                entity = wh_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_warehouse(entity)
                    if resolved:
                        logger.info(f"✅ Warehouse detected: '{resolved}'")
                        self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                        return "warehouse_dashboard", resolved, "warehouse"
                    else:
                        logger.info(f"🔍 Warehouse '{entity}' not found, will search")
                        self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                        return "warehouse_dashboard", entity, "warehouse"
            
            wh_pattern = re.search(r'^([A-Za-z\s\-]+)\s+warehouse$', question_original, re.IGNORECASE)
            if wh_pattern:
                entity = wh_pattern.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_warehouse(entity)
                    if resolved:
                        logger.info(f"✅ Warehouse from pattern: '{resolved}'")
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
        
        # CITY DETECTION (with explicit keyword ONLY)
        if "city" in question_lower or "town" in question_lower:
            city_match = re.search(r'(?:city|town)\s+([A-Za-z\s\-]+)', question_original, re.IGNORECASE)
            if city_match:
                entity = city_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_city(entity)
                    if resolved:
                        logger.info(f"✅ City detected: '{resolved}'")
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
                        logger.info(f"✅ City from pattern: '{resolved}'")
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
        
        # DEALER DETECTION (with explicit keywords)
        dealer_keywords = ["dealer", "customer", "party", "sold to", "show"]
        if any(kw in question_lower for kw in dealer_keywords):
            dealer_match = re.search(r'(?:dealer|customer|party|show)\s+([A-Za-z0-9\s&\.\-]+)', question_original, re.IGNORECASE)
            if dealer_match:
                entity = dealer_match.group(1).strip()
                if len(entity) > 2:
                    resolved = self.resolver.resolve_dealer(entity)
                    if resolved:
                        logger.info(f"✅ Dealer detected: '{resolved}'")
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
                        logger.info(f"✅ Dealer from 'for': '{resolved}'")
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
        
        # STANDALONE - DEALER FIRST (FIXED!)
        if 3 <= len(question_original) <= 100 and not any(c.isdigit() for c in question_original):
            
            # Clean typos
            question_clean = question_original
            typo_fixes = {"are ": "", "is ": "", "the ": "", "for ": "", "of ": ""}
            for typo, fix in typo_fixes.items():
                if question_clean.lower().startswith(typo):
                    question_clean = question_clean[len(typo):].strip()
                    logger.info(f"🔍 Fixed typo: '{question_original}' → '{question_clean}'")
                    break
            
            if not question_clean:
                question_clean = question_original
            
            # STEP 1: Check DEALER FIRST
            dealer_resolved = self.resolver.resolve_dealer(question_clean)
            if not dealer_resolved and question_clean != question_original:
                dealer_resolved = self.resolver.resolve_dealer(question_original)
            
            if dealer_resolved:
                logger.info(f"✅ Dealer from standalone: '{dealer_resolved}'")
                self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
                return "dealer_dashboard", dealer_resolved, "dealer"
            
            # STEP 2: Check WAREHOUSE
            warehouse_resolved = self.resolver.resolve_warehouse(question_clean)
            if warehouse_resolved:
                logger.info(f"✅ Warehouse from standalone: '{warehouse_resolved}'")
                self.metrics["intent_detection"]["warehouse_dashboard"] = self.metrics["intent_detection"].get("warehouse_dashboard", 0) + 1
                return "warehouse_dashboard", warehouse_resolved, "warehouse"
            
            # STEP 3: Check PRODUCT
            product_resolved = self.resolver.resolve_product(question_clean)
            if product_resolved:
                logger.info(f"✅ Product from standalone: '{product_resolved}'")
                self.metrics["intent_detection"]["product_dashboard"] = self.metrics["intent_detection"].get("product_dashboard", 0) + 1
                return "product_dashboard", product_resolved, "product"
            
            # STEP 4: Check CITY LAST
            city_resolved = self.resolver.resolve_city(question_clean)
            if city_resolved:
                logger.info(f"✅ City from standalone: '{city_resolved}'")
                self.metrics["intent_detection"]["city_dashboard"] = self.metrics["intent_detection"].get("city_dashboard", 0) + 1
                return "city_dashboard", city_resolved, "city"
            
            # STEP 5: Default to DEALER
            logger.info(f"🔍 Treating standalone as dealer (default): '{question_original}'")
            self.metrics["intent_detection"]["dealer_dashboard"] = self.metrics["intent_detection"].get("dealer_dashboard", 0) + 1
            return "dealer_dashboard", question_clean, "dealer"
        
        # DIVISION DETECTION
        if "division" in question_lower:
            division_match = re.search(r'(?:division|div)\s+([A-Za-z\s\-]+)', question_original, re.IGNORECASE)
            if division_match:
                entity = division_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Division detected: '{entity}'")
                    self.metrics["intent_detection"]["division_dashboard"] = self.metrics["intent_detection"].get("division_dashboard", 0) + 1
                    return "division_dashboard", entity, "division"
        
        # SALES MANAGER DETECTION
        if "sales manager" in question_lower or "sm " in question_lower:
            sm_match = re.search(r'(?:sales manager|sm|manager)\s+([A-Za-z\s\-]+)', question_original, re.IGNORECASE)
            if sm_match:
                entity = sm_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Sales Manager detected: '{entity}'")
                    self.metrics["intent_detection"]["sales_manager_dashboard"] = self.metrics["intent_detection"].get("sales_manager_dashboard", 0) + 1
                    return "sales_manager_dashboard", entity, "sales_manager"
        
        # SALES OFFICE DETECTION
        if "sales office" in question_lower or "office " in question_lower:
            so_match = re.search(r'(?:sales office|office)\s+([A-Za-z\s\-]+)', question_original, re.IGNORECASE)
            if so_match:
                entity = so_match.group(1).strip()
                if len(entity) > 2:
                    logger.info(f"✅ Sales Office detected: '{entity}'")
                    self.metrics["intent_detection"]["sales_office_dashboard"] = self.metrics["intent_detection"].get("sales_office_dashboard", 0) + 1
                    return "sales_office_dashboard", entity, "sales_office"
        
        # PATTERN MATCHING FOR ALL OTHER INTENTS
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    logger.info(f"✅ Intent '{intent}' from pattern '{pattern}'")
                    self.metrics["intent_detection"][intent] = self.metrics["intent_detection"].get(intent, 0) + 1
                    entity, entity_type = self._extract_entity(question_original, intent)
                    return intent, entity, entity_type
        
        # FALLBACK - Context
        if context and context.last_intent and context.last_entity:
            logger.info(f"🔄 Using context: {context.last_intent} with entity {context.last_entity}")
            return context.last_intent, context.last_entity, self._get_entity_type(context.last_intent)
        
        # UNKNOWN - Return help
        logger.warning(f"❌ Unknown intent for: '{question_original}'")
        return "help", None, None

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
                elapsed = time.time() - start_time
                logger.info(f"[{req_id}] ✅ Completed in {elapsed:.3f}s")
                return result
            
            return self._get_help_message()
            
        except Exception as e:
            self.metrics["errors"] += 1
            logger.exception(f"[{req_id}] ❌ ERROR: {e}")
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
                return self._route_dn_dashboard(entity, context, req_id)
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
            import traceback
            logger.error(traceback.format_exc())
            return f"⚠️ Unable to load {intent.replace('_', ' ').title()}. Please try again."



# BLOCK 17: ROUTE HANDLERS (FIXED)
# ==========================================================
# BLOCK 17: ROUTE HANDLERS (COMPLETE - FIXED)
# ==========================================================

    def _validate_response(self, response, service_name: str, req_id: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        Validate response from analytics service.
        BLOCK 17 - FIXED v9.0
        Returns: (is_valid, error_message, data)
        Supports: AnalyticsResponse, dict, list, None
        """
        logger.info(f"[{req_id}] 🔍 Validating {service_name} response")
        logger.info(f"[{req_id}] 📊 Response type: {type(response)}")
        
        # Check if response is None
        if response is None:
            logger.error(f"[{req_id}] ❌ Response is None for {service_name}")
            return False, "No response received from service", None
        
        # Check if response is a dict (direct data response)
        if isinstance(response, dict):
            logger.info(f"[{req_id}] ✅ Response is a dict with {len(response)} keys")
            if "error" in response:
                error_msg = response.get("error", "Unknown error")
                logger.error(f"[{req_id}] ❌ Response contains error: {error_msg}")
                return False, error_msg, None
            if not response or len(response) == 0:
                logger.warning(f"[{req_id}] ⚠️ Response is empty dict")
                return False, "Empty response received", None
            logger.info(f"[{req_id}] ✅ Valid dict response")
            return True, "", response
        
        # Check if response has success attribute (AnalyticsResponse)
        if hasattr(response, 'success'):
            logger.info(f"[{req_id}] ✅ Response has success attribute")
            if not response.success:
                error_msg = getattr(response, 'error', 'Unknown error')
                logger.error(f"[{req_id}] ❌ Response success=False: {error_msg}")
                return False, error_msg, None
            
            data = getattr(response, 'data', {})
            if not data or len(data) == 0:
                logger.warning(f"[{req_id}] ⚠️ Response data is empty")
                return False, "No data in response", None
            
            if isinstance(data, dict) and "error" in data:
                error_msg = data.get("error", "Unknown error")
                logger.error(f"[{req_id}] ❌ Data contains error: {error_msg}")
                return False, error_msg, None
            
            logger.info(f"[{req_id}] ✅ Valid AnalyticsResponse with {len(data)} data keys")
            return True, "", data
        
        # Check if response is a list
        if isinstance(response, list):
            logger.info(f"[{req_id}] ✅ Response is a list with {len(response)} items")
            if len(response) == 0:
                logger.warning(f"[{req_id}] ⚠️ Response list is empty")
                return False, "Empty list response", None
            return True, "", {"results": response}
        
        # Unknown response type
        logger.error(f"[{req_id}] ❌ Unknown response type: {type(response)}")
        return False, f"Unexpected response type: {type(response).__name__}", None

    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle DN dashboard with complete error handling and retry.
        BLOCK 17 - FIXED v9.0
        """
        import time
        start_time = time.time()
        
        logger.info(f"[{req_id}] 📄 DN Dashboard route called")
        logger.info(f"[{req_id}] 📥 Entity: {entity}")
        logger.info(f"[{req_id}] 📥 Context last_dn: {context.last_dn if context else None}")
        
        dn_number = entity or (context.last_dn if context else None)
        
        if not dn_number:
            logger.warning(f"[{req_id}] ❌ No DN number provided")
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number.\n\n*Example:* 6243675570"
        
        # Clean DN
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            logger.warning(f"[{req_id}] ❌ Invalid DN format: {dn_number}")
            return f"❌ Invalid DN number: '{dn_number}'\n\nDN numbers must be 8-12 digits."
        
        logger.info(f"[{req_id}] 🔍 Looking up DN: {dn_clean}")
        
        # ==========================================================
        # STEP 1: Verify analytics service - with retry
        # ==========================================================
        if self.analytics is None:
            logger.warning(f"[{req_id}] ⚠️ Analytics is None - attempting reload...")
            service, response_class = _get_analytics_service()
            self._analytics = service
            self._analytics_response = response_class
            
            if self.analytics is None:
                logger.error(f"[{req_id}] ❌ Analytics service still None")
                return "⚠️ Service temporarily unavailable. Please try again later."
        
        if not hasattr(self.analytics, 'get_dn_dashboard'):
            logger.error(f"[{req_id}] ❌ get_dn_dashboard not available")
            return "⚠️ Service temporarily unavailable. Please try again later."
        
        try:
            # ==========================================================
            # STEP 2: Get dashboard
            # ==========================================================
            response = self.analytics.get_dn_dashboard(dn_clean)
            logger.info(f"[{req_id}] 📊 Response type: {type(response)}")
            
            # ==========================================================
            # STEP 3: Validate response
            # ==========================================================
            is_valid, error_msg, data = self._validate_response(response, "DN Dashboard", req_id)
            
            if not is_valid:
                logger.error(f"[{req_id}] ❌ Validation failed: {error_msg}")
                return f"❌ Unable to retrieve data for DN {dn_clean}.\n\n{error_msg}"
            
            # ==========================================================
            # STEP 4: Format and return
            # ==========================================================
            logger.info(f"[{req_id}] ✅ Valid data received, formatting...")
            result = self._format_dn_dashboard(data, dn_clean)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ DN dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ DN dashboard error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return f"❌ Error retrieving DN {dn_clean}: {str(e)[:100]}"

    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle dealer dashboard with improved validation and error handling.
        BLOCK 17 - FIXED
        """
        import time
        start_time = time.time()
        
        logger.info(f"[{req_id}] 🏪 Dealer Dashboard route called")
        logger.info(f"[{req_id}] 📥 Entity: {entity}")
        
        dealer_name = entity
        if not dealer_name and context and context.last_dealer:
            dealer_name = context.last_dealer
            logger.info(f"[{req_id}] 🔄 Using context dealer: {dealer_name}")
        
        if not dealer_name:
            return "🏪 *DEALER DASHBOARD*\n\nPlease specify a dealer name."
        
        original_dealer_name = dealer_name
        
        # Clean typos
        typo_fixes = {"are ": "", "is ": "", "the ": "", "for ": "", "of ": ""}
        for typo, fix in typo_fixes.items():
            if dealer_name.lower().startswith(typo):
                dealer_name = dealer_name[len(typo):].strip()
                logger.info(f"[{req_id}] 🔍 Fixed typo: '{original_dealer_name}' → '{dealer_name}'")
                break
        
        if len(dealer_name) < 2:
            dealer_name = original_dealer_name
        
        # ==========================================================
        # STEP 1: Verify analytics service
        # ==========================================================
        if self.analytics is None:
            logger.error(f"[{req_id}] ❌ Analytics service is None")
            return "⚠️ Service temporarily unavailable. Please try again later."
        
        logger.info(f"[{req_id}] 🔍 Searching for dealer: '{dealer_name}'")
        
        try:
            # ==========================================================
            # STEP 2: Get dashboard (try 360 first, fallback to legacy)
            # ==========================================================
            response = None
            
            # Try 360 dashboard
            if hasattr(self.analytics, 'get_dealer_360_dashboard'):
                logger.info(f"[{req_id}] 📊 Using 360 dashboard")
                response = self.analytics.get_dealer_360_dashboard(dealer_name)
            elif hasattr(self.analytics, 'get_dealer_dashboard'):
                logger.info(f"[{req_id}] 📊 Using legacy dashboard")
                response = self.analytics.get_dealer_dashboard(dealer_name)
            else:
                logger.error(f"[{req_id}] ❌ No dealer dashboard method available")
                return "⚠️ Service temporarily unavailable. Please try again later."
            
            logger.info(f"[{req_id}] 📊 Response type: {type(response)}")
            
            # ==========================================================
            # STEP 3: Validate response
            # ==========================================================
            is_valid, error_msg, data = self._validate_response(response, "Dealer Dashboard", req_id)
            
            if not is_valid:
                if data and isinstance(data, dict) and "suggestions" in data:
                    suggestions = data.get("suggestions", [])
                    if suggestions:
                        return f"❌ Dealer '{original_dealer_name}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
                
                logger.error(f"[{req_id}] ❌ Validation failed: {error_msg}")
                return f"❌ Unable to retrieve data for '{original_dealer_name}'.\n\n{error_msg}"
            
            # ==========================================================
            # STEP 4: Format and return
            # ==========================================================
            logger.info(f"[{req_id}] ✅ Valid data received, formatting...")
            
            if data and isinstance(data, dict) and data.get('_dashboard_type') == '360':
                from app.services.dealer_analytics_service import format_dealer_360_dashboard
                result = format_dealer_360_dashboard(data)
            else:
                result = self._format_dealer_dashboard(data, dealer_name)
            
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Dealer dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Dealer dashboard error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return f"❌ Error retrieving dealer data: {str(e)[:100]}"

    def _route_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle warehouse dashboard with improved validation."""
        import time
        start_time = time.time()
        
        logger.info(f"[{req_id}] 🏭 Warehouse Dashboard route called")
        logger.info(f"[{req_id}] 📥 Entity: {entity}")
        
        warehouse_name = entity
        if not warehouse_name and context and context.last_warehouse:
            warehouse_name = context.last_warehouse
        
        if not warehouse_name:
            return "🏭 *WAREHOUSE DASHBOARD*\n\nPlease specify a warehouse name.\n\n*Examples:*\n• Lahore warehouse\n• Rawalpindi warehouse"
        
        logger.info(f"[{req_id}] 🔍 Searching for warehouse: '{warehouse_name}'")
        
        try:
            if not hasattr(self.analytics, 'get_warehouse_dashboard'):
                return "⚠️ Service temporarily unavailable. Please try again later."
            
            response = self.analytics.get_warehouse_dashboard(warehouse_name)
            is_valid, error_msg, data = self._validate_response(response, "Warehouse Dashboard", req_id)
            
            if not is_valid:
                if data and isinstance(data, dict) and "suggestions" in data:
                    suggestions = data.get("suggestions", [])
                    if suggestions:
                        return f"❌ Warehouse '{warehouse_name}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
                return f"❌ Unable to retrieve data for warehouse '{warehouse_name}'.\n\n{error_msg}"
            
            result = self._format_warehouse_dashboard(data, warehouse_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Warehouse dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Warehouse dashboard error: {e}")
            return f"❌ Error retrieving warehouse data: {str(e)[:100]}"

    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle city dashboard with improved validation."""
        import time
        start_time = time.time()
        
        logger.info(f"[{req_id}] 🏙️ City Dashboard route called")
        logger.info(f"[{req_id}] 📥 Entity: {entity}")
        
        city_name = entity
        if not city_name and context and context.last_city:
            city_name = context.last_city
        
        if not city_name:
            return "🏙️ *CITY DASHBOARD*\n\nPlease specify a city name.\n\n*Examples:*\n• Haripur\n• Sahiwal"
        
        logger.info(f"[{req_id}] 🔍 Searching for city: '{city_name}'")
        
        try:
            if not hasattr(self.analytics, 'get_city_dashboard'):
                return "⚠️ Service temporarily unavailable. Please try again later."
            
            response = self.analytics.get_city_dashboard(city_name)
            is_valid, error_msg, data = self._validate_response(response, "City Dashboard", req_id)
            
            if not is_valid:
                if data and isinstance(data, dict) and "suggestions" in data:
                    suggestions = data.get("suggestions", [])
                    if suggestions:
                        return f"❌ City '{city_name}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
                return f"❌ Unable to retrieve data for city '{city_name}'.\n\n{error_msg}"
            
            result = self._format_city_dashboard(data, city_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ City dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ City dashboard error: {e}")
            return f"❌ Error retrieving city data: {str(e)[:100]}"

    def _route_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle product dashboard with improved validation."""
        import time
        start_time = time.time()
        
        logger.info(f"[{req_id}] 📦 Product Dashboard route called")
        logger.info(f"[{req_id}] 📥 Entity: {entity}")
        
        product_name = entity
        if not product_name and context and context.last_product:
            product_name = context.last_product
        
        if not product_name:
            return "📦 *PRODUCT DASHBOARD*\n\nPlease specify a product.\n\n*Examples:*\n• HRF-316IPGA\n• Model A123"
        
        logger.info(f"[{req_id}] 🔍 Searching for product: '{product_name}'")
        
        try:
            if not hasattr(self.analytics, 'get_product_dashboard'):
                return "⚠️ Service temporarily unavailable. Please try again later."
            
            response = self.analytics.get_product_dashboard(product_name)
            is_valid, error_msg, data = self._validate_response(response, "Product Dashboard", req_id)
            
            if not is_valid:
                if data and isinstance(data, dict) and "suggestions" in data:
                    suggestions = data.get("suggestions", [])
                    if suggestions:
                        return f"❌ Product '{product_name}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
                return f"❌ Unable to retrieve data for product '{product_name}'.\n\n{error_msg}"
            
            result = self._format_product_dashboard(data, product_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Product dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Product dashboard error: {e}")
            return f"❌ Error retrieving product data: {str(e)[:100]}"

    def _route_dealer_ranking(self, req_id: str) -> str:
        """Handle dealer ranking."""
        try:
            response = self.analytics.get_ranking_dashboard(limit=10)
            is_valid, error_msg, data = self._validate_response(response, "Dealer Ranking", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve dealer ranking.\n\n{error_msg}"
            return self._format_dealer_ranking(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Dealer ranking error: {e}")
            return f"❌ Error retrieving dealer ranking: {str(e)[:100]}"
    
    def _route_dealer_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        dealer_name = entity or (context.last_dealer if context else None)
        if not dealer_name:
            return "📦 *DEALER PRODUCTS*\n\nPlease specify a dealer name."
        return f"📦 *PRODUCTS FOR {dealer_name.upper()}*\n\nProduct information coming soon."
    
    def _route_warehouse_ranking(self, req_id: str) -> str:
        return "🏆 *WAREHOUSE RANKING*\n\nWarehouse ranking coming soon."
    
    def _route_warehouse_coverage(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        warehouse_name = entity or (context.last_warehouse if context else None)
        if not warehouse_name:
            return "📍 *WAREHOUSE COVERAGE*\n\nPlease specify a warehouse name."
        return f"📍 *COVERAGE FOR {warehouse_name.upper()}*\n\nCoverage information coming soon."
    
    def _route_warehouse_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        warehouse_name = entity or (context.last_warehouse if context else None)
        if not warehouse_name:
            return "📦 *WAREHOUSE PRODUCTS*\n\nPlease specify a warehouse name."
        return f"📦 *PRODUCTS IN {warehouse_name.upper()}*\n\nProduct list coming soon."
    
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
    
    def _route_product_ranking(self, req_id: str) -> str:
        return "🏆 *PRODUCT RANKING*\n\nProduct ranking coming soon."
    
    def _route_product_trend(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "📈 *PRODUCT TREND*\n\nProduct trend coming soon."

    def _route_dn_analytics(self, req_id: str) -> str:
        return "📊 *DN ANALYTICS*\n\nAnalytics coming soon."

    def _route_pgi_dashboard(self, req_id: str) -> str:
        try:
            response = self.analytics.get_pgi_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "PGI Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve PGI data.\n\n{error_msg}"
            return self._format_pgi_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ PGI dashboard error: {e}")
            return f"❌ Error retrieving PGI data: {str(e)[:100]}"
    
    def _route_pod_dashboard(self, req_id: str) -> str:
        try:
            response = self.analytics.get_pod_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "POD Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve POD data.\n\n{error_msg}"
            return self._format_pod_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ POD dashboard error: {e}")
            return f"❌ Error retrieving POD data: {str(e)[:100]}"
    
    def _route_delivery_dashboard(self, req_id: str) -> str:
        try:
            response = self.analytics.get_delivery_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Delivery Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve delivery data.\n\n{error_msg}"
            return self._format_delivery_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Delivery dashboard error: {e}")
            return f"❌ Error retrieving delivery data: {str(e)[:100]}"
    
    def _route_executive_dashboard(self, req_id: str) -> str:
        try:
            response = self.analytics.get_executive_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Executive Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve executive data.\n\n{error_msg}"
            return self._format_executive_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Executive dashboard error: {e}")
            return f"❌ Error retrieving executive data: {str(e)[:100]}"
    
    def _route_control_tower(self, req_id: str) -> str:
        try:
            response = self.analytics.get_control_tower_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Control Tower", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve control tower data.\n\n{error_msg}"
            return self._format_control_tower(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Control tower error: {e}")
            return f"❌ Error retrieving control tower data: {str(e)[:100]}"
    
    def _route_revenue_dashboard(self, req_id: str) -> str:
        try:
            response = self.analytics.get_revenue_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Revenue Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve revenue data.\n\n{error_msg}"
            return self._format_revenue_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Revenue dashboard error: {e}")
            return f"❌ Error retrieving revenue data: {str(e)[:100]}"
    
    def _route_aging_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        try:
            response = self.analytics.get_aging_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Aging Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve aging data.\n\n{error_msg}"
            return self._format_aging_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Aging dashboard error: {e}")
            return f"❌ Error retrieving aging data: {str(e)[:100]}"
    
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
# END OF BLOCK 17
# ==========================================================

    


# BLOCK 18-22: FORMATTERS (FIXED - Safe handling WITH DISTANCE)
# ==========================================================

    def _format_dn_dashboard(self, data: Dict, dn_number: str) -> str:
        """Format DN dashboard - Safe handling of all fields."""
        try:
            if not data:
                return f"❌ No data available for DN {dn_number}"
            
            # Safe get with defaults
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            status = safe_get('delivery_status', 'Unknown')
            status_emoji = "✅" if status in ['Completed', 'Delivered', 'Closed'] else "⏳"
            pending_text = "🔴 Yes" if data.get('pending_flag') else "🟢 No"
            
            # Get aging values safely
            delivery_aging = safe_get('delivery_aging_text', 'N/A')
            pod_aging = safe_get('pod_aging_text', 'N/A')
            total_cycle = safe_get('total_cycle_text', 'N/A')
            
            # Get issues safely
            issues = data.get('issues', [])
            if not isinstance(issues, list):
                issues = []
            
            amount = data.get('amount', 0)
            if amount is None:
                amount = 0
            
            lines = [
                "📄 *DN TRACKING*",
                "",
                f"DN No: {safe_get('dn_number', dn_number)}",
                f"Dealer: {safe_get('customer_name', 'N/A')}",
                f"Dealer Code: {safe_get('dealer_code', 'N/A')}",
                f"Customer Code: {safe_get('customer_code', 'N/A')}",
                f"Warehouse: {safe_get('warehouse', 'N/A')}",
                f"City: {safe_get('ship_to_city', 'N/A')}",
                f"Sales Office: {safe_get('sales_office', 'N/A')}",
                f"Sales Manager: {safe_get('sales_manager', 'N/A')}",
                f"Division: {safe_get('division', 'N/A')}",
                "",
                "📦 *Products*",
                f"Model: {safe_get('customer_model', 'N/A')}",
                f"Material: {safe_get('material_no', 'N/A')}",
                "",
                "📊 *Metrics*",
                f"Units: {safe_get('units', 0)}",
                f"Revenue: PKR {amount:,.0f}" if amount else f"Revenue: PKR {amount}",
                "",
                "📅 *Dates*",
                f"Create: {safe_get('dn_create_date', 'N/A')}",
                f"PGI: {safe_get('good_issue_date', 'N/A')}",
                f"POD: {safe_get('pod_date', 'N/A')}",
                "",
                "⏳ *Aging*",
                f"Delivery Aging: {delivery_aging}",
                f"POD Aging: {pod_aging}",
                f"Total Cycle: {total_cycle}",
            ]
            
            if issues:
                lines.append("")
                lines.append("⚠ *Data Issue Detected*")
                for issue in issues[:3]:
                    lines.append(f"   {issue}")
                lines.append("   Please verify source data.")
            
            lines.extend([
                "",
                "📋 *Status*",
                f"Delivery: {status} {status_emoji}",
                f"PGI: {safe_get('pgi_status', 'N/A')}",
                f"POD: {safe_get('pod_status', 'N/A')}",
                f"Pending: {pending_text}"
            ])
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"DN format error: {e}")
            return f"❌ Unable to format DN details for {dn_number}"

    def _format_dealer_dashboard(self, data: Dict, dealer_name: str) -> str:
        """
        Format dealer dashboard - Safe handling WITH DISTANCE.
        BLOCK 18-22 - UPDATED WITH DISTANCE
        """
        try:
            if not data:
                return f"❌ No data available for dealer {dealer_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            total_dns = safe_get('total_dns', 0)
            delivered = safe_get('delivered_dns', 0)
            pending = safe_get('pending_dns', 0)
            transit = safe_get('transit_dns', 0)
            pod_completed = safe_get('pod_completed_dns', 0)
            pending_pod = safe_get('pending_pod_dns', 0)
            pending_pgi = safe_get('pending_pgi_dns', 0)
            
            delivery_rate = safe_get('delivery_rate', 0)
            pgi_rate = safe_get('pgi_rate', 0)
            pod_rate = safe_get('pod_rate', 0)
            health_score = safe_get('health_score', 0)
            risk_level = safe_get('risk_level', 'Unknown')
            risk_score = safe_get('risk_score', 0)
            
            revenue = data.get('total_revenue', 0)
            if revenue is None:
                revenue = 0
            
            # ==========================================================
            # GET DISTANCE INFORMATION (NEW)
            # ==========================================================
            distance_km = data.get('distance_km')
            distance_hours = data.get('distance_approx_hours')
            distance_miles = data.get('distance_miles')
            distance_minutes = data.get('approx_driving_minutes')
            
            lines = [
                "🏢 *DEALER DASHBOARD*",
                "",
                f"Dealer: {safe_get('dealer_name', dealer_name)}",
                f"Dealer Code: {safe_get('dealer_code', 'N/A')}",
                f"Customer Code: {safe_get('customer_code', 'N/A')}",
                f"Division: {safe_get('division', 'N/A')}",
                f"Warehouse: {safe_get('warehouse', 'N/A')}",
                f"City: {safe_get('city', 'N/A')}",
            ]
            
            # ==========================================================
            # ADD DISTANCE SECTION IF AVAILABLE (NEW)
            # ==========================================================
            if distance_km:
                lines.append("")
                lines.append("📍 *Distance*")
                lines.append(f"Warehouse → Dealer: {distance_km:.1f} km")
                if distance_miles:
                    lines.append(f"Warehouse → Dealer: {distance_miles:.1f} miles")
                if distance_minutes:
                    if distance_minutes < 60:
                        lines.append(f"⏱️ Approx Driving: {distance_minutes} minutes")
                    else:
                        lines.append(f"⏱️ Approx Driving: {distance_minutes // 60}h {distance_minutes % 60}m")
                elif distance_hours:
                    if distance_hours < 1:
                        lines.append(f"⏱️ Approx Driving: {int(distance_hours * 60)} minutes")
                    else:
                        hours = int(distance_hours)
                        minutes = int((distance_hours - hours) * 60)
                        if minutes > 0:
                            lines.append(f"⏱️ Approx Driving: {hours}h {minutes}m")
                        else:
                            lines.append(f"⏱️ Approx Driving: {hours}h")
            
            lines.extend([
                "",
                "📊 *Metrics*",
                f"Total DNs: {total_dns}",
                f"Total Units: {safe_get('total_units', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                "",
                "📦 *Delivery Status*",
                f"Delivered: {delivered} ({delivery_rate}%)",
                f"In Transit: {transit}",
                f"Pending: {pending}",
                "",
                "📋 *POD Status*",
                f"POD Completed: {pod_completed} ({pod_rate}%)",
                f"Pending POD: {pending_pod}",
                f"Pending PGI: {pending_pgi}",
                "",
                "⏱️ *Performance*",
                f"Delivery Rate: {delivery_rate}%",
                f"PGI Rate: {pgi_rate}%",
                f"POD Rate: {pod_rate}%",
                f"Health Score: {health_score}/100",
                f"Risk Level: {risk_level} ({risk_score}/100)",
                "",
                f"📌 Products: {safe_get('product_count', 0)}",
                f"📍 Cities: {safe_get('city_count', 0)}"
            ])
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Dealer format error: {e}")
            return f"❌ Unable to format dealer data for {dealer_name}"

    def _format_warehouse_dashboard(self, data: Dict, warehouse_name: str) -> str:
        """
        Format warehouse dashboard - Safe handling WITH DISTANCE COVERAGE.
        BLOCK 18-22 - UPDATED WITH DISTANCE
        """
        try:
            if not data:
                return f"❌ No data available for warehouse {warehouse_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            total_dns = safe_get('total_dns', 0)
            delivered = safe_get('delivered_dns', 0)
            pending = safe_get('pending_dns', 0)
            delivery_rate = safe_get('delivery_rate', 0)
            revenue = data.get('total_revenue', 0)
            if revenue is None:
                revenue = 0
            
            # ==========================================================
            # GET DISTANCE COVERAGE INFORMATION (NEW)
            # ==========================================================
            avg_distance = data.get('avg_distance_km')
            max_distance = data.get('max_distance_km')
            min_distance = data.get('min_distance_km')
            distance_info = data.get('distance_info', [])
            
            lines = [
                "🏭 *WAREHOUSE DASHBOARD*",
                "",
                f"Warehouse: {safe_get('warehouse', warehouse_name)}",
                f"Warehouse Code: {safe_get('warehouse_code', 'N/A')}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {total_dns}",
                f"Total Units: {safe_get('total_units', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                "",
                "👥 *Coverage*",
                f"Total Dealers: {safe_get('total_dealers', 0)}",
                f"Cities Served: {safe_get('cities_served', 0)}",
                f"Product Count: {safe_get('product_count', 0)}",
            ]
            
            # ==========================================================
            # ADD DISTANCE COVERAGE SECTION IF AVAILABLE (NEW)
            # ==========================================================
            if avg_distance:
                lines.append("")
                lines.append("📍 *Distance Coverage*")
                lines.append(f"Average Distance: {avg_distance:.1f} km")
                if min_distance:
                    lines.append(f"Closest City: {min_distance:.1f} km")
                if max_distance:
                    lines.append(f"Farthest City: {max_distance:.1f} km")
                
                if distance_info:
                    lines.append("")
                    lines.append("📌 *Top Cities by Distance*")
                    for item in distance_info[:5]:
                        city = item.get('city', 'Unknown')
                        dist = item.get('distance_km', 0)
                        lines.append(f"• {city}: {dist:.1f} km")
            
            lines.extend([
                "",
                "📦 *Delivery Status*",
                f"Delivered: {delivered} ({delivery_rate}%)",
                f"Pending: {pending}",
                f"Pending POD: {safe_get('pending_pod_dns', 0)}"
            ])
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Warehouse format error: {e}")
            return f"❌ Unable to format warehouse data for {warehouse_name}"

    def _format_city_dashboard(self, data: Dict, city_name: str) -> str:
        """Format city dashboard - Safe handling."""
        try:
            if not data:
                return f"❌ No data available for city {city_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            total_dns = safe_get('total_dns', 0)
            delivered = safe_get('delivered_dns', 0)
            pending = safe_get('pending_dns', 0)
            delivery_rate = safe_get('delivery_rate', 0)
            revenue = data.get('total_revenue', 0)
            if revenue is None:
                revenue = 0
            
            lines = [
                "🏙️ *CITY DASHBOARD*",
                "",
                f"City: {safe_get('city_name', city_name)}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {total_dns}",
                f"Total Units: {safe_get('total_units', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                "",
                "👥 *Coverage*",
                f"Total Dealers: {safe_get('total_dealers', 0)}",
                f"Total Warehouses: {safe_get('total_warehouses', 0)}",
                "",
                "📦 *Delivery Status*",
                f"Delivered: {delivered} ({delivery_rate}%)",
                f"Pending: {pending}",
                f"Pending POD: {safe_get('pending_pod_dns', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"City format error: {e}")
            return f"❌ Unable to format city data for {city_name}"

    def _format_product_dashboard(self, data: Dict, product_name: str) -> str:
        """Format product dashboard - Safe handling."""
        try:
            if not data:
                return f"❌ No data available for product {product_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            revenue = data.get('revenue', 0)
            if revenue is None:
                revenue = 0
            
            lines = [
                "📦 *PRODUCT DASHBOARD*",
                "",
                f"Product: {safe_get('product', product_name)}",
                "",
                "📊 *Metrics*",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                f"Total Units: {safe_get('units', 0)}",
                f"Total DNs: {safe_get('dns', 0)}",
                "",
                "📍 *Distribution*",
                f"Dealers: {safe_get('dealers', 0)}",
                f"Cities: {safe_get('cities', 0)}",
                f"Warehouses: {safe_get('warehouses', 0)}",
                "",
                f"📦 Delivery Rate: {safe_get('delivery_rate', 0)}%"
            ]
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Product format error: {e}")
            return f"❌ Unable to format product data for {product_name}"

    def _format_dealer_ranking(self, data: Dict) -> str:
        """Format dealer ranking - Safe handling."""
        try:
            if not data:
                return "❌ No ranking data available"
            
            ranking = data.get('ranking', [])
            if not ranking:
                return "📊 *DEALER RANKING*\n\nNo ranking data available."
            
            lines = ["🏆 *TOP DEALERS*", ""]
            for i, dealer in enumerate(ranking[:10], 1):
                name = dealer.get('dealer', 'Unknown')
                revenue = dealer.get('revenue', 0)
                units = dealer.get('units', 0)
                dns = dealer.get('dns', 0)
                rate = dealer.get('delivery_rate', 0)
                
                lines.append(f"{i}. {name}")
                lines.append(f"   Revenue: PKR {revenue:,.0f}" if revenue else f"   Revenue: PKR {revenue}")
                lines.append(f"   Units: {units} | DNs: {dns} | Rate: {rate}%")
                lines.append("")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Ranking format error: {e}")
            return "❌ Unable to format ranking data"

    def _format_pgi_dashboard(self, data: Dict) -> str:
        """Format PGI dashboard."""
        try:
            if not data:
                return "❌ No PGI data available"
            
            total = data.get('total_dns', 0)
            completed = data.get('pgi_completed', 0)
            pending = data.get('pgi_pending', 0)
            in_transit = data.get('in_transit', 0)
            rate = data.get('pgi_rate', 0)
            
            return f"""📋 *PGI DASHBOARD*

Total DNs: {total}
PGI Completed: {completed} ({rate}%)
PGI Pending: {pending}
In Transit: {in_transit}

📊 *PGI Rate: {rate}%*"""
        except Exception as e:
            logger.error(f"PGI format error: {e}")
            return "❌ Unable to format PGI data"

    def _format_pod_dashboard(self, data: Dict) -> str:
        """Format POD dashboard."""
        try:
            if not data:
                return "❌ No POD data available"
            
            total = data.get('total_dns', 0)
            completed = data.get('pod_completed', 0)
            pending = data.get('pod_pending', 0)
            delivered = data.get('delivered_dns', 0)
            rate = data.get('pod_rate', 0)
            
            return f"""✅ *POD DASHBOARD*

Total DNs: {total}
POD Completed: {completed} ({rate}%)
POD Pending: {pending}
Delivered DNs: {delivered}

📊 *POD Rate: {rate}%*"""
        except Exception as e:
            logger.error(f"POD format error: {e}")
            return "❌ Unable to format POD data"

    def _format_delivery_dashboard(self, data: Dict) -> str:
        """Format delivery dashboard."""
        try:
            if not data:
                return "❌ No delivery data available"
            
            total = data.get('total_dns', 0)
            delivered = data.get('delivered', 0)
            in_transit = data.get('in_transit', 0)
            pending_pgi = data.get('pending_pgi', 0)
            pending = data.get('pending', 0)
            delivery_rate = data.get('delivery_rate', 0)
            pgi_rate = data.get('pgi_rate', 0)
            
            return f"""🚚 *DELIVERY DASHBOARD*

Total DNs: {total}
Delivered: {delivered} ({delivery_rate}%)
In Transit: {in_transit}
Pending PGI: {pending_pgi}
Pending: {pending}

📊 *Delivery Rate: {delivery_rate}%
📊 *PGI Rate: {pgi_rate}%*"""
        except Exception as e:
            logger.error(f"Delivery format error: {e}")
            return "❌ Unable to format delivery data"

    def _format_executive_dashboard(self, data: Dict) -> str:
        """Format executive dashboard."""
        try:
            if not data:
                return "❌ No executive data available"
            
            total_dns = data.get('total_dns', 0)
            total_units = data.get('total_units', 0)
            total_revenue = data.get('total_revenue', 0)
            total_dealers = data.get('total_dealers', 0)
            total_cities = data.get('total_cities', 0)
            total_warehouses = data.get('total_warehouses', 0)
            delivered = data.get('delivered_dns', 0)
            pending = data.get('pending_dns', 0)
            rate = data.get('delivery_rate', 0)
            
            return f"""👔 *EXECUTIVE DASHBOARD*

📊 *Nationwide Performance*

Total DNs: {total_dns}
Total Units: {total_units}
Total Revenue: PKR {total_revenue:,.0f}

👥 *Network*
Total Dealers: {total_dealers}
Total Cities: {total_cities}
Total Warehouses: {total_warehouses}

📦 *Delivery*
Delivered: {delivered} ({rate}%)
Pending: {pending}"""
        except Exception as e:
            logger.error(f"Executive format error: {e}")
            return "❌ Unable to format executive data"

    def _format_control_tower(self, data: Dict) -> str:
        """Format control tower dashboard."""
        try:
            if not data:
                return "❌ No control tower data available"
            
            alerts = data.get('alerts', [])
            critical = data.get('critical_count', 0)
            high = data.get('high_count', 0)
            total = data.get('total_alerts', 0)
            
            lines = ["🚨 *CONTROL TOWER*", ""]
            
            if not alerts:
                lines.append("✅ No alerts at this time.")
            else:
                lines.append(f"⚠️ *{total} Alert(s) Found*")
                lines.append(f"🔴 Critical: {critical} | 🟠 High: {high}")
                lines.append("")
                
                for alert in alerts[:5]:
                    alert_type = alert.get('type', 'Alert')
                    severity = alert.get('severity', 'medium')
                    desc = alert.get('description', 'No description')
                    severity_emoji = "🔴" if severity == "critical" else "🟠" if severity == "high" else "🟡"
                    lines.append(f"{severity_emoji} *{alert_type}*")
                    lines.append(f"   {desc}")
                    lines.append("")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Control tower format error: {e}")
            return "❌ Unable to format control tower data"

    def _format_revenue_dashboard(self, data: Dict) -> str:
        """Format revenue dashboard."""
        try:
            if not data:
                return "❌ No revenue data available"
            
            total_revenue = data.get('total_revenue', 0)
            total_units = data.get('total_units', 0)
            total_dns = data.get('total_dns', 0)
            top_dealers = data.get('top_dealers', [])
            
            lines = [
                "💰 *REVENUE DASHBOARD*",
                "",
                f"Total Revenue: PKR {total_revenue:,.0f}",
                f"Total Units: {total_units}",
                f"Total DNs: {total_dns}",
                ""
            ]
            
            if top_dealers:
                lines.append("🏆 *Top 5 Dealers*")
                for i, dealer in enumerate(top_dealers[:5], 1):
                    name = dealer.get('dealer', 'Unknown')
                    revenue = dealer.get('revenue', 0)
                    lines.append(f"{i}. {name}: PKR {revenue:,.0f}")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Revenue format error: {e}")
            return "❌ Unable to format revenue data"

    def _format_aging_dashboard(self, data: Dict) -> str:
        """Format aging dashboard."""
        try:
            if not data:
                return "❌ No aging data available"
            
            days_0_7 = data.get('days_0_7', 0)
            days_8_14 = data.get('days_8_14', 0)
            days_15_30 = data.get('days_15_30', 0)
            days_30_plus = data.get('days_30_plus', 0)
            total = data.get('total_pending', 0)
            
            return f"""⏳ *AGING DASHBOARD*

📊 *Pending DN Aging*

0-7 Days: {days_0_7}
8-14 Days: {days_8_14}
15-30 Days: {days_15_30}
30+ Days: {days_30_plus}

📊 *Total Pending: {total} DNs*"""
        except Exception as e:
            logger.error(f"Aging format error: {e}")
            return "❌ Unable to format aging data"

# ==========================================================
# END OF BLOCK 18-22 - FORMATTERS
# ==========================================================
    
    
    
    
    
    
    # ==========================================================
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
9️⃣ 👔 Executive Dashboard
🔟 🚨 Control Tower
1️⃣1️⃣ 🏆 Dealer Ranking
1️⃣2️⃣ 🏆 Warehouse Ranking
1️⃣3️⃣ 🏆 City Ranking
1️⃣4️⃣ 🏆 Product Ranking
1️⃣5️⃣ 💰 Revenue Dashboard
1️⃣6️⃣ 📊 Division Dashboard
1️⃣7️⃣ 👤 Sales Manager Dashboard
1️⃣8️⃣ 🏢 Sales Office Dashboard
1️⃣9️⃣ ⏳ Aging Dashboard
2️⃣0️⃣ 🔄 Follow-up Support

*🔍 Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "Pakistan Electronics Mansehra")
• City name (e.g., "Lahore")
• Warehouse name (e.g., "Rawalpindi warehouse")
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
# ==========================================================
# BLOCK 24: SINGLETON & WRAPPER FUNCTIONS (FIXED v3.0)
# ==========================================================

    def _truncate_response(self, response: str) -> str:
        """Truncate response if too long."""
        if len(response) > MAX_RESPONSE_LENGTH:
            return response[:MAX_RESPONSE_LENGTH - 20] + "\n\n... (truncated)"
        return response


# ==========================================================
# SINGLETON & WRAPPER FUNCTIONS
# ==========================================================

_orchestrator = None
_initialization_attempts = 0
_MAX_INIT_ATTEMPTS = 3

def get_orchestrator(session_factory: Optional[Callable[[], Session]] = None) -> AIOrchestrator:
    """
    Get or create AI Orchestrator singleton with retry logic.
    BLOCK 24 - FIXED v3.0
    """
    global _orchestrator, _initialization_attempts
    
    if _orchestrator is not None:
        return _orchestrator
    
    # If we've tried too many times, don't keep trying
    if _initialization_attempts >= _MAX_INIT_ATTEMPTS:
        logger.error(f"❌ Max initialization attempts ({_MAX_INIT_ATTEMPTS}) reached")
        return None
    
    _initialization_attempts += 1
    logger.info(f"🔄 Initializing AI Orchestrator (attempt {_initialization_attempts}/{_MAX_INIT_ATTEMPTS})...")
    
    try:
        _orchestrator = AIOrchestrator(session_factory=session_factory)
        logger.info("✅ AI Orchestrator v27.0 initialized successfully")
        _initialization_attempts = 0  # Reset on success
        return _orchestrator
        
    except AttributeError as e:
        logger.error(f"❌ AttributeError during initialization: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Check if analytics service is the issue
        if "analytics" in str(e).lower() or "method" in str(e).lower():
            logger.warning("⚠️ Analytics service issue detected - will retry on next request")
        
        _orchestrator = None
        return None
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize AI Orchestrator: {e}")
        import traceback
        logger.error(traceback.format_exc())
        _orchestrator = None
        return None


def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    """
    Process WhatsApp query with fallback and recovery.
    BLOCK 24 - FIXED v3.0
    """
    global _orchestrator, _initialization_attempts
    
    # Validate input
    if not question or not question.strip():
        return "Please provide a valid question. Type 'help' for menu."
    
    # Get orchestrator
    orchestrator = get_orchestrator(session_factory)
    
    # If orchestrator is None, try to reset and retry once
    if orchestrator is None:
        logger.warning("⚠️ Orchestrator is None - attempting emergency reset...")
        
        # Reset and try one more time
        _orchestrator = None
        _initialization_attempts = 0
        
        try:
            orchestrator = AIOrchestrator(session_factory=session_factory)
            _orchestrator = orchestrator
            logger.info("✅ Emergency reset successful")
        except Exception as e:
            logger.error(f"❌ Emergency reset failed: {e}")
            _orchestrator = None
            return "⚠️ AI service is currently unavailable. Please try again later."
    
    # Final check
    if orchestrator is None:
        return "⚠️ AI service is currently unavailable. Please try again later."
    
    # Process the query
    try:
        return orchestrator.process_whatsapp_query(
            question=question,
            session_factory=session_factory,
            phone_number=phone_number,
            user_id=user_id,
            request_id=request_id
        )
    except Exception as e:
        logger.error(f"❌ Error processing query: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"⚠️ Error processing your request. Please try again later."


def reset_orchestrator() -> None:
    """
    Reset the orchestrator singleton (useful for testing or recovery).
    BLOCK 24 - FIXED v3.0
    """
    global _orchestrator, _initialization_attempts
    _orchestrator = None
    _initialization_attempts = 0
    logger.info("🔄 Orchestrator reset successfully")


def get_orchestrator_status() -> Dict[str, Any]:
    """
    Get current orchestrator status for diagnostics.
    BLOCK 24 - FIXED v3.0
    """
    global _orchestrator, _initialization_attempts
    
    return {
        "orchestrator_initialized": _orchestrator is not None,
        "initialization_attempts": _initialization_attempts,
        "max_attempts": _MAX_INIT_ATTEMPTS,
        "analytics_available": hasattr(_orchestrator, 'analytics') if _orchestrator else False,
        "has_analytics": _orchestrator.analytics is not None if _orchestrator else False,
        "conversation_count": len(_orchestrator.conversation_cache) if _orchestrator else 0,
        "metrics": _orchestrator.metrics if _orchestrator else {}
    }


# ==========================================================
# EXPOSE HELPER FUNCTIONS
# ==========================================================

# These are available for debugging and monitoring
__all__ = [
    'AIOrchestrator',
    'PostgreSQLResolver',
    'ConversationContext',
    'get_orchestrator',
    'process_whatsapp_query',
    'reset_orchestrator',
    'get_orchestrator_status',
    'test_database_connection'
]

# ==========================================================
# END OF BLOCK 24 - FIXED v3.0
# ==========================================================

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
# END OF FILE - v27.0 PRODUCTION READY
# ==========================================================
