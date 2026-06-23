# ==========================================================
# FILE: app/services/ai_provider_service.py (v30.0 - FULLY INTEGRATED)
# PURPOSE: COMPLETE AI ORCHESTRATION WITH ALL SERVICES INTEGRATED
# VERSION: 30.0 - Full Integration with Analytics, Distance, Dealer Analytics
# ==========================================================

import time
import uuid
import re
import os
import requests
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
# BLOCK 1.5: SERVICE IMPORTS - FULL INTEGRATION
# ==========================================================

try:
    from app.services.analytics_service import get_analytics_service, AnalyticsResponse
    logger.info("✅ Analytics service imported successfully")
except ImportError as e:
    logger.error(f"❌ Analytics service import failed: {e}")
    get_analytics_service = None
    AnalyticsResponse = None

try:
    from app.services.distance_service import DistanceService
    logger.info("✅ Distance service imported successfully")
except ImportError as e:
    logger.error(f"❌ Distance service import failed: {e}")
    DistanceService = None

try:
    from app.services.dealer_analytics_service import DealerAnalyticsService, format_dealer_360_dashboard
    logger.info("✅ Dealer Analytics service imported successfully")
except ImportError as e:
    logger.error(f"❌ Dealer Analytics service import failed: {e}")
    DealerAnalyticsService = None
    format_dealer_360_dashboard = None

# ==========================================================
# BLOCK 2: ANALYTICS SERVICE LOADER (FIXED v10.0)
# ==========================================================

def _create_response_class():
    """Create a dummy response class for fallback."""
    class DummyResponse:
        def __init__(self, data=None, success=True, error=None):
            self.data = data or {}
            self.success = success
            self.error = error
    
    return DummyResponse

def _get_analytics_service():
    """
    Load analytics service with comprehensive validation.
    BLOCK 2 - FIXED v10.0 - PRODUCTION GRADE
    ALWAYS returns valid service and response class.
    """
    logger.info("=" * 70)
    logger.info("🔍 ANALYTICS SERVICE LOADER - PRODUCTION GRADE v10.0")
    logger.info("=" * 70)
    
    # ==========================================================
    # VALIDATION 1: Check AI Analysis Enabled
    # ==========================================================
    try:
        from app.config import config
        ai_enabled = getattr(config, 'AI_ANALYSIS_ENABLED', True)
        logger.info(f"📌 AI_ANALYSIS_ENABLED: {ai_enabled}")
        
        if not ai_enabled:
            logger.error("❌ AI_ANALYSIS_ENABLED is False")
            logger.error("   💡 Set AI_ANALYSIS_ENABLED=True in config")
            fallback = _create_fallback_analytics()
            return fallback, _create_response_class()
    except Exception as e:
        logger.error(f"❌ Config validation failed: {e}")
        fallback = _create_fallback_analytics()
        return fallback, _create_response_class()
    
    # ==========================================================
    # VALIDATION 2: Test Database Connection
    # ==========================================================
    db = None
    try:
        db = SessionLocal()
        
        # Get database statistics
        total_records = db.query(DeliveryReport).count()
        
        if total_records == 0:
            logger.error("❌ Database has ZERO records!")
            logger.error("   💡 Insert data into delivery_reports table")
            logger.warning("⚠️ Using fallback analytics due to empty database")
            if db:
                db.close()
            fallback = _create_fallback_analytics()
            return fallback, _create_response_class()
        
        total_dns = db.query(func.count(distinct(DeliveryReport.dn_no))).scalar()
        total_dealers = db.query(func.count(distinct(DeliveryReport.customer_name))).scalar()
        total_warehouses = db.query(func.count(distinct(DeliveryReport.warehouse))).scalar()
        total_cities = db.query(func.count(distinct(DeliveryReport.ship_to_city))).scalar()
        
        db.close()
        
        logger.info(f"📌 PostgreSQL Connection: SUCCESS")
        logger.info(f"   📊 Total Records: {total_records}")
        logger.info(f"   📦 Total DNs: {total_dns}")
        logger.info(f"   🏪 Total Dealers: {total_dealers}")
        logger.info(f"   🏭 Total Warehouses: {total_warehouses}")
        logger.info(f"   🏙️ Total Cities: {total_cities}")
        
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        if db:
            db.close()
        logger.warning("⚠️ Using fallback analytics due to database error")
        fallback = _create_fallback_analytics()
        return fallback, _create_response_class()
    
    # ==========================================================
    # VALIDATION 3: Import Analytics Service
    # ==========================================================
    if get_analytics_service is None:
        logger.error("❌ Analytics service not available")
        fallback = _create_fallback_analytics()
        return fallback, _create_response_class()
    
    # ==========================================================
    # VALIDATION 4: Get Service Instance
    # ==========================================================
    service = None
    try:
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
                fallback = _create_fallback_analytics()
                return fallback, AnalyticsResponse or _create_response_class()
        
        logger.info(f"📊 Service type: {type(service)}")
        logger.info(f"📊 Service class: {service.__class__.__name__}")
        
    except Exception as e:
        logger.error(f"❌ Failed to get analytics service: {e}")
        import traceback
        logger.error(traceback.format_exc())
        fallback = _create_fallback_analytics()
        return fallback, AnalyticsResponse or _create_response_class()
    
    # ==========================================================
    # VALIDATION 5: Verify Required Methods
    # ==========================================================
    required_methods = [
        "get_dn_dashboard",
        "get_dealer_dashboard",
        "get_warehouse_dashboard",
        "get_city_dashboard",
        "get_product_dashboard",
        "search_dn",
        "search_dealer",
        "search_warehouse",
        "search_city",
        "search_product",
        "verify_dn_exists",
        "verify_dealer_exists",
    ]
    
    logger.info("🔍 Verifying analytics methods:")
    missing_methods = []
    
    for method in required_methods:
        if hasattr(service, method):
            logger.info(f"   ✅ {method}: AVAILABLE")
        else:
            missing_methods.append(method)
            logger.error(f"   ❌ {method}: MISSING")
    
    if missing_methods:
        logger.warning(f"⚠️ Missing {len(missing_methods)} methods - using available methods")
    
    logger.info("=" * 70)
    logger.info("✅ Analytics service initialized successfully")
    logger.info("✅ Service is ready to serve REAL PostgreSQL data")
    logger.info("=" * 70)
    
    return service, AnalyticsResponse or _create_response_class()

def _create_fallback_analytics():
    """Create fallback analytics with clear error messages."""
    class FallbackAnalytics:
        def get_dn_dashboard(self, dn_no):
            return {
                "dn_number": dn_no,
                "error": f"DN {dn_no} not found",
                "hint": "Please add data to delivery_reports table"
            }
        
        def get_dealer_dashboard(self, dealer_name):
            return {
                "dealer_name": dealer_name,
                "error": f"No data found for dealer '{dealer_name}'",
                "total_dns": 0,
                "total_revenue": 0,
                "delivery_rate": 0
            }
        
        def get_warehouse_dashboard(self, warehouse_name):
            return {
                "warehouse": warehouse_name,
                "error": f"No data found for warehouse '{warehouse_name}'",
                "total_dns": 0,
                "total_revenue": 0
            }
        
        def get_city_dashboard(self, city_name):
            return {
                "city_name": city_name,
                "error": f"No data found for city '{city_name}'",
                "total_dns": 0,
                "total_revenue": 0
            }
        
        def get_product_dashboard(self, product_name):
            return {
                "product": product_name,
                "error": f"No data found for product '{product_name}'",
                "revenue": 0,
                "units": 0
            }
        
        def search_dn(self, query):
            return []
        
        def search_dealer(self, query):
            return []
        
        def search_warehouse(self, query):
            return []
        
        def search_city(self, query):
            return []
        
        def search_product(self, query):
            return []
        
        def verify_dn_exists(self, dn_no):
            return False
        
        def verify_dealer_exists(self, dealer_name):
            return False
        
        def get_dealer_360_dashboard(self, dealer_name):
            return {
                "error": f"No data found for dealer '{dealer_name}'",
                "profile": {
                    "dealer_name": dealer_name,
                    "dealer_code": "N/A",
                    "warehouse": "N/A",
                    "city": "N/A"
                },
                "business_volume": {
                    "total_dns": 0,
                    "total_units": 0,
                    "total_revenue": 0
                }
            }
    
    return FallbackAnalytics()


# ==========================================================
# BLOCK 3: CONFIGURATION
# ==========================================================

from app.config import config

CACHE_TTL_SECONDS = getattr(config, 'CACHE_TTL', 300)
CONTEXT_TTL_SECONDS = getattr(config, 'CACHE_TTL_SESSION', 1800)
MAX_RESPONSE_LENGTH = 2500
QUERY_TIMEOUT_SECONDS = getattr(config, 'AI_TIMEOUT_SECONDS', 10)
MAX_RETRY_ATTEMPTS = getattr(config, 'AI_MAX_RETRIES', 3)
AI_ANALYSIS_ENABLED = getattr(config, 'AI_ANALYSIS_ENABLED', True)
FUZZY_MATCH_THRESHOLD = float(os.getenv('FUZZY_MATCH_THRESHOLD', '0.3'))
MAX_FUZZY_RESULTS = int(os.getenv('MAX_FUZZY_RESULTS', '1000'))


# ==========================================================
# BLOCK 4: POSTGRESQL RESOLVER
# ==========================================================

class PostgreSQLResolver:
    """Pure PostgreSQL-based entity resolution."""
    
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory or SessionLocal
        self._cache = TTLCache(maxsize=2000, ttl=3600)
        self.DeliveryReport = DeliveryReport
        self.fuzzy_threshold = FUZZY_MATCH_THRESHOLD
        self.max_fuzzy_results = MAX_FUZZY_RESULTS
    
    def _get_session(self) -> Optional[Session]:
        try:
            return self.session_factory()
        except Exception as e:
            logger.error(f"Session creation failed: {e}")
            return None
    
    def resolve_dealer(self, query: str) -> Optional[str]:
        """Resolve dealer name with multiple strategies."""
        if not query or not query.strip():
            return None
        
        query_clean = query.strip()
        cache_key = f"dealer:{query_clean.lower()}"
        
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            # STRATEGY 1: Exact match
            result = session.query(self.DeliveryReport.customer_name).filter(
                func.lower(self.DeliveryReport.customer_name) == func.lower(query_clean)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # STRATEGY 2: ILIKE match
            result = session.query(self.DeliveryReport.customer_name).filter(
                self.DeliveryReport.customer_name.ilike(f"%{query_clean}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # STRATEGY 3: Token-based matching
            tokens = query_clean.split()
            for token in tokens:
                if len(token) > 2:
                    result = session.query(self.DeliveryReport.customer_name).filter(
                        self.DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        self._cache[cache_key] = resolved
                        return resolved
            
            return None
            
        except Exception as e:
            logger.error(f"Dealer resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_warehouse(self, query: str) -> Optional[str]:
        """Resolve warehouse name."""
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
            
            return None
            
        except Exception as e:
            logger.error(f"Warehouse resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_city(self, query: str) -> Optional[str]:
        """Resolve city name."""
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
            
            return None
            
        except Exception as e:
            logger.error(f"City resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_product(self, query: str) -> Optional[str]:
        """Resolve product name."""
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
            
            result = session.query(self.DeliveryReport.customer_model).filter(
                self.DeliveryReport.customer_model.ilike(f"%{query}%")
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
        """Resolve DN number."""
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
# BLOCK 5: CONVERSATION CONTEXT
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
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)


# ==========================================================
# BLOCK 6: INTENT CLASSIFIER
# ==========================================================

@dataclass
class IntentResult:
    intent: str
    confidence: float
    entity_type: Optional[str] = None
    entity_value: Optional[str] = None
    raw_query: str = ""

class IntentClassifier:
    """Enhanced intent classifier with priority-based routing."""
    
    PRODUCT_KEYWORDS = {
        'refrigerator': 0.95, 'fridge': 0.95, 'freezer': 0.95,
        'ac': 0.95, 'air conditioner': 0.95,
        'washing machine': 0.95, 'washer': 0.90,
        'led': 0.90, 'tv': 0.90, 'television': 0.90,
        'microwave': 0.95, 'oven': 0.90,
        'water dispenser': 0.95, 'cooler': 0.90,
    }
    
    DEALER_INDICATORS = {
        'electronics': 0.70, 'trading': 0.60,
        'enterprise': 0.60, 'corporation': 0.60,
        'industries': 0.60, 'traders': 0.60,
    }
    
    WAREHOUSE_KEYWORDS = {
        'warehouse': 0.95, 'wh': 0.90, 'depot': 0.90,
    }
    
    CITY_KEYWORDS = {
        'city': 0.95, 'town': 0.85, 'district': 0.85,
    }
    
    def __init__(self, resolver: PostgreSQLResolver):
        self.resolver = resolver
        self._cache = TTLCache(maxsize=1000, ttl=300)
    
    def classify(self, query: str, context: Optional[ConversationContext] = None) -> IntentResult:
        """Classify intent with priority-based routing."""
        if not query or not query.strip():
            return IntentResult(intent="help", confidence=1.0, raw_query=query)
        
        query_clean = query.strip()
        query_lower = query_clean.lower()
        cache_key = f"intent:{query_lower}"
        
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if cached.confidence > 0.5:
                return cached
        
        result = self._classify_with_priority(query_clean, query_lower, context)
        self._cache[cache_key] = result
        return result
    
    def _classify_with_priority(self, query: str, query_lower: str, context: Optional[ConversationContext]) -> IntentResult:
        """Classify intent with strict priority order."""
        
        # PRIORITY 1: DN Number
        dn_match = re.search(r'\b(\d{8,12})\b', query)
        if dn_match:
            return IntentResult(
                intent="dn_dashboard",
                confidence=1.0,
                entity_type="dn",
                entity_value=dn_match.group(1),
                raw_query=query
            )
        
        # PRIORITY 2: Warehouse
        for keyword in self.WAREHOUSE_KEYWORDS:
            if keyword in query_lower:
                warehouse_match = re.search(rf'(?:{keyword})\s+([A-Za-z0-9\s\-&]+)', query, re.IGNORECASE)
                if warehouse_match:
                    warehouse_name = warehouse_match.group(1).strip()
                    resolved = self.resolver.resolve_warehouse(warehouse_name)
                    return IntentResult(
                        intent="warehouse_dashboard",
                        confidence=0.95,
                        entity_type="warehouse",
                        entity_value=resolved or warehouse_name,
                        raw_query=query
                    )
        
        # PRIORITY 3: City
        for keyword in self.CITY_KEYWORDS:
            if keyword in query_lower:
                city_match = re.search(rf'(?:{keyword})\s+([A-Za-z\s\-]+)', query, re.IGNORECASE)
                if city_match:
                    city_name = city_match.group(1).strip()
                    resolved = self.resolver.resolve_city(city_name)
                    return IntentResult(
                        intent="city_dashboard",
                        confidence=0.95,
                        entity_type="city",
                        entity_value=resolved or city_name,
                        raw_query=query
                    )
        
        # PRIORITY 4: Product
        for keyword, confidence in self.PRODUCT_KEYWORDS.items():
            if keyword in query_lower:
                resolved = self.resolver.resolve_product(keyword)
                return IntentResult(
                    intent="product_dashboard",
                    confidence=confidence,
                    entity_type="product",
                    entity_value=resolved or keyword,
                    raw_query=query
                )
        
        # PRIORITY 5: Dealer (last priority)
        dealer_score = 0
        for keyword, score in self.DEALER_INDICATORS.items():
            if keyword in query_lower:
                dealer_score = max(dealer_score, score)
        
        if dealer_score > 0.3:
            resolved = self.resolver.resolve_dealer(query)
            if resolved:
                return IntentResult(
                    intent="dealer_dashboard",
                    confidence=dealer_score,
                    entity_type="dealer",
                    entity_value=resolved,
                    raw_query=query
                )
        
        # Try standalone detection
        if len(query) > 2 and not any(c.isdigit() for c in query):
            # Try product first
            product_resolved = self.resolver.resolve_product(query)
            if product_resolved:
                return IntentResult(
                    intent="product_dashboard",
                    confidence=0.7,
                    entity_type="product",
                    entity_value=product_resolved,
                    raw_query=query
                )
            
            # Try dealer last
            dealer_resolved = self.resolver.resolve_dealer(query)
            if dealer_resolved:
                return IntentResult(
                    intent="dealer_dashboard",
                    confidence=0.6,
                    entity_type="dealer",
                    entity_value=dealer_resolved,
                    raw_query=query
                )
        
        # Context fallback
        if context and context.last_intent:
            return IntentResult(
                intent=context.last_intent,
                confidence=0.5,
                entity_type=context.last_entity,
                entity_value=context.last_dealer or context.last_entity,
                raw_query=query
            )
        
        return IntentResult(intent="help", confidence=1.0, raw_query=query)


# ==========================================================
# BLOCK 7: MAIN AI ORCHESTRATOR (FULLY INTEGRATED)
# ==========================================================

class AIOrchestrator:
    """
    Complete AI Orchestrator with all services integrated.
    
    Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                    AIOrchestrator                          │
    │  - Intent Classification                                   │
    │  - Entity Resolution                                       │
    │  - Context Management                                      │
    │  - Dashboard Routing                                       │
    └─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
    AnalyticsService    DistanceService    DealerAnalyticsService
          │                   │                   │
          └───────────────────┼───────────────────┘
                              │
                              ▼
                        PostgreSQL
                     delivery_reports
    """
    
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory or SessionLocal
        
        # Initialize all services
        self._analytics = None
        self._analytics_response = None
        self._resolver = None
        self._classifier = None
        self._distance_service = None
        self._dealer_analytics = None
        
        # Caches
        self.response_cache = TTLCache(maxsize=2000, ttl=CACHE_TTL_SECONDS)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "intent_detection": {},
            "errors": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }
        
        logger.info("=" * 70)
        logger.info("AI Router v30.0 - FULLY INTEGRATED")
        logger.info("Architecture: AnalyticsService + DistanceService + DealerAnalyticsService")
        logger.info("=" * 70)
        
        # Initialize all services
        self._init_services()
        
        logger.info("=" * 70)
        logger.info("✅ AI Router v30.0 initialized successfully")
        logger.info("✅ All services integrated and ready")
        logger.info("=" * 70)
    
    def _init_services(self):
        """Initialize all services with error handling."""
        # 1. Analytics Service
        try:
            self._init_analytics()
        except Exception as e:
            logger.error(f"❌ Analytics init failed: {e}")
            self._analytics = None
        
        # 2. Resolver
        try:
            self._resolver = PostgreSQLResolver(self.session_factory)
            logger.info("✅ Resolver initialized")
        except Exception as e:
            logger.error(f"❌ Resolver init failed: {e}")
            self._resolver = None
        
        # 3. Classifier
        try:
            self._classifier = IntentClassifier(self.resolver)
            logger.info("✅ Classifier initialized")
        except Exception as e:
            logger.error(f"❌ Classifier init failed: {e}")
            self._classifier = None
        
        # 4. Distance Service
        try:
            if DistanceService:
                self._distance_service = DistanceService(self.session_factory)
                logger.info("✅ Distance service initialized")
            else:
                logger.warning("⚠️ Distance service not available")
        except Exception as e:
            logger.error(f"❌ Distance service init failed: {e}")
            self._distance_service = None
        
        # 5. Dealer Analytics Service
        try:
            if DealerAnalyticsService:
                self._dealer_analytics = DealerAnalyticsService(self.session_factory)
                logger.info("✅ Dealer Analytics service initialized")
            else:
                logger.warning("⚠️ Dealer Analytics service not available")
        except Exception as e:
            logger.error(f"❌ Dealer Analytics service init failed: {e}")
            self._dealer_analytics = None
    
    def _init_analytics(self):
        """Initialize analytics service with retry."""
        for attempt in range(3):
            try:
                logger.info(f"🔄 Attempt {attempt + 1}/3 to initialize analytics...")
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
        # Create fallback as last resort
        self._analytics = _create_fallback_analytics()
        self._analytics_response = _create_response_class()
        logger.warning("⚠️ Using fallback analytics service")
    
    @property
    def analytics(self):
        """Get analytics service."""
        if self._analytics is None:
            logger.warning("⚠️ Analytics service is None - attempting to reload...")
            try:
                service, response_class = _get_analytics_service()
                self._analytics = service
                self._analytics_response = response_class
                if self._analytics is None:
                    logger.error("❌ Analytics service still None after reload")
            except Exception as e:
                logger.error(f"❌ Reload failed: {e}")
        return self._analytics
    
    @property
    def resolver(self):
        """Get resolver with lazy initialization."""
        if self._resolver is None:
            self._resolver = PostgreSQLResolver(self.session_factory)
        return self._resolver
    
    @property
    def classifier(self):
        """Get classifier with lazy initialization."""
        if self._classifier is None:
            self._classifier = IntentClassifier(self.resolver)
        return self._classifier
    
    @property
    def distance_service(self):
        """Get distance service with lazy initialization."""
        if self._distance_service is None and DistanceService:
            try:
                self._distance_service = DistanceService(self.session_factory)
                logger.info("✅ Distance service initialized (lazy)")
            except Exception as e:
                logger.error(f"Distance service init failed: {e}")
                self._distance_service = None
        return self._distance_service
    
    @property
    def dealer_analytics(self):
        """Get dealer analytics service with lazy initialization."""
        if self._dealer_analytics is None and DealerAnalyticsService:
            try:
                self._dealer_analytics = DealerAnalyticsService(self.session_factory)
                logger.info("✅ Dealer Analytics service initialized (lazy)")
            except Exception as e:
                logger.error(f"Dealer Analytics init failed: {e}")
                self._dealer_analytics = None
        return self._dealer_analytics
    
    def _load_context(self, phone_number: Optional[str]) -> Optional[ConversationContext]:
        """Load conversation context."""
        if not phone_number:
            return None
        
        if phone_number not in self.conversation_cache:
            self.conversation_cache[phone_number] = ConversationContext(phone_number=phone_number)
        
        context = self.conversation_cache[phone_number]
        if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
            context = ConversationContext(phone_number=phone_number)
            self.conversation_cache[phone_number] = context
        
        return context
    
    def _update_context(self, phone_number: Optional[str], intent: str, entity_type: str, entity: str):
        """Update conversation context."""
        if not phone_number or not entity:
            return
        
        context = self._load_context(phone_number)
        if not context:
            return
        
        context.last_intent = intent
        context.last_entity = entity
        context.message_count += 1
        context.last_updated = time.time()
        
        if entity_type == "dealer":
            context.last_dealer = entity
        elif entity_type == "warehouse":
            context.last_warehouse = entity
        elif entity_type == "city":
            context.last_city = entity
        elif entity_type == "dn":
            context.last_dn = entity
        elif entity_type == "product":
            context.last_product = entity
        
        self.conversation_cache[phone_number] = context
    
    def _validate_response(self, response, service_name: str, req_id: str) -> Tuple[bool, str, Optional[Dict]]:
        """Validate response from any service."""
        if response is None:
            return False, "No response received", None
        
        if isinstance(response, dict):
            if "error" in response:
                return False, response.get("error", "Unknown error"), response
            if not response:
                return False, "Empty response", None
            return True, "", response
        
        if hasattr(response, 'success'):
            if not response.success:
                error = getattr(response, 'error', 'Unknown error')
                return False, error, getattr(response, 'data', {})
            data = getattr(response, 'data', {})
            return True, "", data
        
        if isinstance(response, list):
            return True, "", {"results": response}
        
        return True, "", {"data": response}
    
    def _truncate_response(self, response: str) -> str:
        """Truncate response if too long."""
        if len(response) > MAX_RESPONSE_LENGTH:
            return response[:MAX_RESPONSE_LENGTH - 20] + "\n\n... (truncated)"
        return response


# ==========================================================
# BLOCK 8: MAIN ENTRY POINT
# ==========================================================

    def process_whatsapp_query(
        self,
        question: str,
        session_factory: Optional[Callable[[], Session]] = None,
        phone_number: Optional[str] = None,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> str:
        """
        Process WhatsApp query with full service integration.
        
        Flow:
        1. Validate input
        2. Classify intent
        3. Route to appropriate dashboard
        4. Format response
        5. Update context
        6. Return response
        """
        start_time = time.time()
        
        if not getattr(config, 'AI_ANALYSIS_ENABLED', True):
            return "⚠️ AI service is currently disabled. Please contact support."
        
        req_id = request_id or str(uuid.uuid4())[:8]
        self.metrics["total_requests"] += 1
        
        logger.info(f"[{req_id}] 📥 Processing: '{question[:100]}'")
        
        if session_factory:
            self.session_factory = session_factory
        
        if not question or len(question.strip()) < 2:
            return "Please provide a valid question. Type 'help' for menu."
        
        try:
            context = self._load_context(phone_number)
            
            # Classify intent
            intent_result = self.classifier.classify(question.strip(), context)
            
            logger.info(f"[{req_id}] 🎯 Intent: {intent_result.intent}")
            logger.info(f"[{req_id}] 📊 Entity: {intent_result.entity_value}")
            
            if intent_result.intent == "help":
                return self._get_help_message()
            
            # Route to appropriate dashboard
            result = self._route_to_dashboard(
                intent_result.intent,
                intent_result.entity_value,
                intent_result.entity_type,
                context,
                req_id
            )
            
            if result:
                self._update_context(
                    phone_number,
                    intent_result.intent,
                    intent_result.entity_type or "unknown",
                    intent_result.entity_value
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
# BLOCK 9: ROUTING ENGINE WITH FULL INTEGRATION
# ==========================================================

    def _route_to_dashboard(self, intent: str, entity: Optional[str], 
                            entity_type: Optional[str], 
                            context: Optional[ConversationContext], 
                            req_id: str) -> Optional[str]:
        """
        Route to appropriate dashboard with full service integration.
        
        Services Used:
        - AnalyticsService: Core data
        - DistanceService: Distance calculations
        - DealerAnalyticsService: 360 dealer dashboards
        """
        if not self.analytics:
            return "⚠️ Analytics service is temporarily unavailable. Please try again later."
        
        ROUTE_MAP = {
            "dn_dashboard": self._route_dn_dashboard,
            "dealer_dashboard": self._route_dealer_dashboard,
            "warehouse_dashboard": self._route_warehouse_dashboard,
            "city_dashboard": self._route_city_dashboard,
            "product_dashboard": self._route_product_dashboard,
        }
        
        try:
            handler = ROUTE_MAP.get(intent)
            if handler:
                return handler(entity, context, req_id)
            return None
        except Exception as e:
            logger.error(f"[{req_id}] Routing error: {e}")
            return f"⚠️ Unable to load {intent.replace('_', ' ').title()}."


# ==========================================================
# BLOCK 10: ROUTE HANDLERS WITH SERVICE INTEGRATION
# ==========================================================

    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle DN dashboard with analytics integration."""
        dn_number = entity or (context.last_dn if context else None)
        
        if not dn_number:
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number."
        
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return f"❌ Invalid DN number: '{dn_number}'"
        
        try:
            response = self.analytics.get_dn_dashboard(dn_clean)
            is_valid, error_msg, data = self._validate_response(response, "DN Dashboard", req_id)
            
            if not is_valid:
                return f"❌ Unable to retrieve data for DN {dn_clean}.\n\n{error_msg}"
            
            return self._format_dn_dashboard(data, dn_clean)
        except Exception as e:
            logger.error(f"[{req_id}] DN dashboard error: {e}")
            return f"❌ Error retrieving DN {dn_clean}: {str(e)[:100]}"

    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle dealer dashboard with full service integration.
        
        Integration Flow:
        1. DealerAnalyticsService → 360 dashboard
        2. DistanceService → Distance calculation
        3. AnalyticsService → Legacy fallback
        """
        dealer_name = entity or (context.last_dealer if context else None)
        
        if not dealer_name:
            return "🏪 *DEALER DASHBOARD*\n\nPlease specify a dealer name."
        
        original_name = dealer_name
        
        # ==========================================================
        # STEP 1: Get Dealer 360 Dashboard from DealerAnalyticsService
        # ==========================================================
        data = None
        if self.dealer_analytics:
            try:
                logger.info(f"[{req_id}] 📊 Getting 360 dashboard for: {dealer_name}")
                response = self.dealer_analytics.get_dealer_360_dashboard(dealer_name)
                
                if hasattr(response, 'success') and response.success:
                    data = response.data if hasattr(response, 'data') else {}
                    logger.info(f"[{req_id}] ✅ 360 dashboard retrieved")
                elif isinstance(response, dict) and not response.get('error'):
                    data = response
                    logger.info(f"[{req_id}] ✅ 360 dashboard retrieved as dict")
            except Exception as e:
                logger.warning(f"[{req_id}] ⚠️ 360 dashboard failed: {e}")
        
        # ==========================================================
        # STEP 2: Add Distance Information from DistanceService
        # ==========================================================
        if data and self.distance_service:
            try:
                warehouse = data.get('warehouse')
                city = data.get('city')
                if warehouse and city:
                    distance_info = self.distance_service.calculate_warehouse_distance(warehouse, city)
                    if distance_info and distance_info.get('success'):
                        data['distance_km'] = distance_info.get('distance_km')
                        data['approx_driving_minutes'] = distance_info.get('approx_driving_minutes')
                        logger.info(f"[{req_id}] ✅ Distance: {distance_info.get('distance_km')} km")
            except Exception as e:
                logger.warning(f"[{req_id}] ⚠️ Distance calculation failed: {e}")
        
        # ==========================================================
        # STEP 3: Fallback to Legacy AnalyticsService
        # ==========================================================
        if not data:
            try:
                logger.info(f"[{req_id}] 📊 Using legacy analytics for: {dealer_name}")
                response = self.analytics.get_dealer_dashboard(dealer_name)
                if hasattr(response, 'success') and response.success:
                    data = response.data if hasattr(response, 'data') else {}
                elif isinstance(response, dict) and not response.get('error'):
                    data = response
            except Exception as e:
                logger.warning(f"[{req_id}] ⚠️ Legacy dashboard failed: {e}")
        
        # ==========================================================
        # STEP 4: Validate and Format
        # ==========================================================
        if not data or (isinstance(data, dict) and data.get('error')):
            error = data.get('error', 'No data') if isinstance(data, dict) else 'No data'
            
            # Check for suggestions
            if isinstance(data, dict) and 'suggestions' in data:
                suggestions = data.get('suggestions', [])
                if suggestions:
                    return f"❌ Dealer '{original_name}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
            
            return f"❌ Unable to retrieve data for '{original_name}'.\n\n{error}"
        
        # Use 360 formatter if available
        if data.get('_dashboard_type') == '360' or 'profile' in data:
            try:
                if format_dealer_360_dashboard:
                    return format_dealer_360_dashboard(data)
            except Exception as e:
                logger.warning(f"[{req_id}] ⚠️ 360 formatter failed: {e}")
        
        return self._format_dealer_dashboard(data, dealer_name)

    def _route_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle warehouse dashboard with coverage integration.
        
        Integration Flow:
        1. AnalyticsService → Warehouse data
        2. DistanceService → Coverage information
        """
        warehouse_name = entity or (context.last_warehouse if context else None)
        
        if not warehouse_name:
            return "🏭 *WAREHOUSE DASHBOARD*\n\nPlease specify a warehouse name."
        
        try:
            response = self.analytics.get_warehouse_dashboard(warehouse_name)
            is_valid, error_msg, data = self._validate_response(response, "Warehouse Dashboard", req_id)
            
            if not is_valid:
                return f"❌ Unable to retrieve data for warehouse '{warehouse_name}'.\n\n{error_msg}"
            
            # Add distance coverage information from DistanceService
            if data and self.distance_service:
                try:
                    coverage = self.distance_service.get_warehouse_coverage(warehouse_name)
                    if coverage and coverage.get('success'):
                        data['avg_distance_km'] = coverage.get('avg_distance_km')
                        data['max_distance_km'] = coverage.get('max_distance_km')
                        data['min_distance_km'] = coverage.get('min_distance_km')
                        data['distance_info'] = coverage.get('cities', [])
                        logger.info(f"[{req_id}] ✅ Coverage info added")
                except Exception as e:
                    logger.warning(f"[{req_id}] ⚠️ Coverage info failed: {e}")
            
            return self._format_warehouse_dashboard(data, warehouse_name)
        except Exception as e:
            logger.error(f"[{req_id}] Warehouse dashboard error: {e}")
            return f"❌ Error retrieving warehouse data: {str(e)[:100]}"

    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle city dashboard."""
        city_name = entity or (context.last_city if context else None)
        
        if not city_name:
            return "🏙️ *CITY DASHBOARD*\n\nPlease specify a city name."
        
        try:
            response = self.analytics.get_city_dashboard(city_name)
            is_valid, error_msg, data = self._validate_response(response, "City Dashboard", req_id)
            
            if not is_valid:
                return f"❌ Unable to retrieve data for city '{city_name}'.\n\n{error_msg}"
            
            return self._format_city_dashboard(data, city_name)
        except Exception as e:
            logger.error(f"[{req_id}] City dashboard error: {e}")
            return f"❌ Error retrieving city data: {str(e)[:100]}"

    def _route_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle product dashboard."""
        product_name = entity or (context.last_product if context else None)
        
        if not product_name:
            return "📦 *PRODUCT DASHBOARD*\n\nPlease specify a product."
        
        try:
            response = self.analytics.get_product_dashboard(product_name)
            is_valid, error_msg, data = self._validate_response(response, "Product Dashboard", req_id)
            
            if not is_valid:
                return f"❌ Unable to retrieve data for product '{product_name}'.\n\n{error_msg}"
            
            return self._format_product_dashboard(data, product_name)
        except Exception as e:
            logger.error(f"[{req_id}] Product dashboard error: {e}")
            return f"❌ Error retrieving product data: {str(e)[:100]}"


# ==========================================================
# BLOCK 11: FORMATTERS WITH DISTANCE SUPPORT
# ==========================================================

    def _format_dn_dashboard(self, data: Dict, dn_number: str) -> str:
        """Format DN dashboard."""
        try:
            if not data:
                return f"❌ No data available for DN {dn_number}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            lines = [
                "📄 *DN TRACKING*",
                "",
                f"DN No: {safe_get('dn_number', dn_number)}",
                f"Dealer: {safe_get('customer_name', 'N/A')}",
                f"Warehouse: {safe_get('warehouse', 'N/A')}",
                f"City: {safe_get('ship_to_city', 'N/A')}",
                "",
                "📊 *Metrics*",
                f"Units: {safe_get('units', 0)}",
                f"Revenue: PKR {safe_get('amount', 0):,.0f}",
                "",
                "📋 *Status*",
                f"Delivery: {safe_get('delivery_status', 'Unknown')}",
                f"PGI: {safe_get('pgi_status', 'N/A')}",
                f"POD: {safe_get('pod_status', 'N/A')}"
            ]
            
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"DN format error: {e}")
            return f"❌ Unable to format DN details: {str(e)}"

    def _format_dealer_dashboard(self, data: Dict, dealer_name: str) -> str:
        """Format dealer dashboard with distance information."""
        try:
            if not data:
                return f"❌ No data available for dealer {dealer_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            revenue = data.get('total_revenue', 0)
            delivery_rate = safe_get('delivery_rate', 0)
            total_dns = safe_get('total_dns', 0)
            
            lines = [
                "🏢 *DEALER DASHBOARD*",
                "",
                f"Dealer: {safe_get('dealer_name', dealer_name)}",
                f"Warehouse: {safe_get('warehouse', 'N/A')}",
                f"City: {safe_get('city', 'N/A')}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {total_dns}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                f"Delivery Rate: {delivery_rate}%",
            ]
            
            # Add distance information if available
            distance_km = data.get('distance_km')
            if distance_km:
                lines.append("")
                lines.append("📍 *Distance*")
                lines.append(f"Warehouse → Dealer: {distance_km:.1f} km")
                
                approx_minutes = data.get('approx_driving_minutes')
                if approx_minutes:
                    if approx_minutes < 60:
                        lines.append(f"⏱️ Approx Driving: {approx_minutes} minutes")
                    else:
                        hours = int(approx_minutes // 60)
                        minutes = int(approx_minutes % 60)
                        lines.append(f"⏱️ Approx Driving: {hours}h {minutes}m")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Dealer format error: {e}")
            return f"❌ Unable to format dealer data: {str(e)}"

    def _format_warehouse_dashboard(self, data: Dict, warehouse_name: str) -> str:
        """Format warehouse dashboard with coverage information."""
        try:
            if not data:
                return f"❌ No data available for warehouse {warehouse_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            revenue = data.get('total_revenue', 0)
            
            lines = [
                "🏭 *WAREHOUSE DASHBOARD*",
                "",
                f"Warehouse: {safe_get('warehouse', warehouse_name)}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {safe_get('total_dns', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                f"Total Dealers: {safe_get('total_dealers', 0)}",
                f"Cities Served: {safe_get('cities_served', 0)}",
            ]
            
            # Add distance coverage
            avg_distance = data.get('avg_distance_km')
            if avg_distance:
                lines.append("")
                lines.append("📍 *Distance Coverage*")
                lines.append(f"Average Distance: {avg_distance:.1f} km")
                
                max_distance = data.get('max_distance_km')
                if max_distance:
                    lines.append(f"Farthest City: {max_distance:.1f} km")
                
                distance_info = data.get('distance_info', [])
                if distance_info:
                    lines.append("")
                    lines.append("📌 *Top Cities by Distance*")
                    for item in distance_info[:5]:
                        city = item.get('city', 'Unknown')
                        dist = item.get('distance_km', 0)
                        lines.append(f"• {city}: {dist:.1f} km")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Warehouse format error: {e}")
            return f"❌ Unable to format warehouse data: {str(e)}"

    def _format_city_dashboard(self, data: Dict, city_name: str) -> str:
        """Format city dashboard."""
        try:
            if not data:
                return f"❌ No data available for city {city_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            revenue = data.get('total_revenue', 0)
            
            lines = [
                "🏙️ *CITY DASHBOARD*",
                "",
                f"City: {safe_get('city_name', city_name)}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {safe_get('total_dns', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                f"Total Dealers: {safe_get('total_dealers', 0)}",
                f"Total Warehouses: {safe_get('total_warehouses', 0)}",
                "",
                f"📦 Delivery Rate: {safe_get('delivery_rate', 0)}%"
            ]
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"City format error: {e}")
            return f"❌ Unable to format city data: {str(e)}"

    def _format_product_dashboard(self, data: Dict, product_name: str) -> str:
        """Format product dashboard."""
        try:
            if not data:
                return f"❌ No data available for product {product_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            revenue = data.get('revenue', 0)
            
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
            return f"❌ Unable to format product data: {str(e)}"

    def _get_help_message(self) -> str:
        """Get help message."""
        return """🏠 *HAIER LOGISTICS AI*

📋 *Available Dashboards:*

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

🔍 *Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "Pakistan Electronics")
• Product name (e.g., "Refrigerator")
• City name (e.g., "Lahore City")
• Warehouse name (e.g., "Rawalpindi warehouse")
• "Help" for menu

*Ask me anything about logistics!* 🤖"""


# ==========================================================
# BLOCK 12: SINGLETON & WRAPPER FUNCTIONS
# ==========================================================

_orchestrator = None
_initialization_attempts = 0
_MAX_INIT_ATTEMPTS = 3

def get_orchestrator(session_factory: Optional[Callable[[], Session]] = None) -> Optional[AIOrchestrator]:
    """Get or create AI Orchestrator singleton."""
    global _orchestrator, _initialization_attempts
    
    if _orchestrator is not None:
        return _orchestrator
    
    if _initialization_attempts >= _MAX_INIT_ATTEMPTS:
        logger.error(f"❌ Max initialization attempts ({_MAX_INIT_ATTEMPTS}) reached")
        return None
    
    _initialization_attempts += 1
    logger.info(f"🔄 Initializing AI Orchestrator (attempt {_initialization_attempts}/{_MAX_INIT_ATTEMPTS})...")
    
    try:
        _orchestrator = AIOrchestrator(session_factory=session_factory)
        logger.info("✅ AI Orchestrator v30.0 initialized successfully")
        _initialization_attempts = 0
        return _orchestrator
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
    Process WhatsApp query with all services integrated.
    
    Architecture Flow:
    ┌─────────────────────────────────────────────────────────────┐
    │                    WhatsApp User                           │
    └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   webhook.py    │
                    └─────────────────┘
                              │
                              ▼
              ┌─────────────────────────────────┐
              │    process_whatsapp_query()     │
              │    (AI Orchestrator Entry)      │
              └─────────────────────────────────┘
                              │
                              ▼
              ┌─────────────────────────────────┐
              │    AIOrchestrator               │
              │  - Intent Classification        │
              │  - Entity Resolution            │
              │  - Context Management           │
              │  - Dashboard Routing            │
              └─────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────┐
│ AnalyticsService│ │ DistanceService │ │DealerAnalyticsService│
│  (PostgreSQL)   │ │   (GeoPy)       │ │   (360 Dashboard)    │
└─────────────────┘ └─────────────────┘ └─────────────────────┘
          │                   │                   │
          └───────────────────┼───────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   PostgreSQL    │
                    │ delivery_reports│
                    └─────────────────┘
    """
    global _orchestrator
    
    if not question or not question.strip():
        return "Please provide a valid question. Type 'help' for menu."
    
    orchestrator = get_orchestrator(session_factory)
    
    if orchestrator is None:
        return "⚠️ AI service is currently unavailable. Please try again later."
    
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
    """Reset the orchestrator singleton."""
    global _orchestrator, _initialization_attempts
    _orchestrator = None
    _initialization_attempts = 0
    logger.info("🔄 Orchestrator reset successfully")


def get_orchestrator_status() -> Dict[str, Any]:
    """Get current orchestrator status for diagnostics."""
    global _orchestrator, _initialization_attempts
    
    return {
        "orchestrator_initialized": _orchestrator is not None,
        "initialization_attempts": _initialization_attempts,
        "max_attempts": _MAX_INIT_ATTEMPTS,
        "analytics_available": hasattr(_orchestrator, 'analytics') and _orchestrator.analytics is not None if _orchestrator else False,
        "distance_available": hasattr(_orchestrator, 'distance_service') and _orchestrator.distance_service is not None if _orchestrator else False,
        "dealer_analytics_available": hasattr(_orchestrator, 'dealer_analytics') and _orchestrator.dealer_analytics is not None if _orchestrator else False,
        "conversation_count": len(_orchestrator.conversation_cache) if _orchestrator else 0,
        "metrics": _orchestrator.metrics if _orchestrator else {}
    }


# ==========================================================
# BLOCK 13: EXPORTS
# ==========================================================

__all__ = [
    'AIOrchestrator',
    'PostgreSQLResolver',
    'ConversationContext',
    'IntentClassifier',
    'IntentResult',
    'get_orchestrator',
    'process_whatsapp_query',
    'reset_orchestrator',
    'get_orchestrator_status',
]

# ==========================================================
# END OF FILE - v30.0 FULLY INTEGRATED
# ==========================================================
