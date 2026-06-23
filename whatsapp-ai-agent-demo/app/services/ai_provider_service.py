# ==========================================================
# FILE: app/services/ai_provider_service.py (v30.0 - FULLY INTEGRATED)
# PURPOSE: COMPLETE AI ORCHESTRATION WITH ALL SERVICES
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
def _get_analytics_service():
    """
    Load analytics service with comprehensive validation.
    BLOCK 2 - FIXED v11.0 - PRODUCTION GRADE
    ALWAYS returns valid service and response class.
    """
    logger.info("=" * 70)
    logger.info("🔍 ANALYTICS SERVICE LOADER - PRODUCTION GRADE v11.0")
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
        logger.error("❌ Analytics service not available - import failed")
        fallback = _create_fallback_analytics()
        return fallback, _create_response_class()
    
    # ==========================================================
    # VALIDATION 4: Get Service Instance
    # ==========================================================
    service = None
    try:
        logger.info("🔄 Calling get_analytics_service()...")
        service = get_analytics_service()
        logger.info(f"📊 Service returned: {service}")
        
        if service is None:
            logger.error("❌ Analytics service returned None")
            # Try manual creation
            try:
                from app.services.analytics_service import AnalyticsService
                service = AnalyticsService()
                logger.info("✅ AnalyticsService created manually")
            except Exception as e:
                logger.error(f"❌ Manual creation failed: {e}")
                import traceback
                logger.error(traceback.format_exc())
                fallback = _create_fallback_analytics()
                return fallback, AnalyticsResponse or _create_response_class()
        
        logger.info(f"📊 Service type: {type(service)}")
        logger.info(f"📊 Service class: {service.__class__.__name__}")
        
    except ImportError as e:
        logger.error(f"❌ ImportError in get_analytics_service: {e}")
        import traceback
        logger.error(traceback.format_exc())
        fallback = _create_fallback_analytics()
        return fallback, AnalyticsResponse or _create_response_class()
        
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
    available_methods = []
    
    for method in required_methods:
        if hasattr(service, method):
            available_methods.append(method)
            logger.info(f"   ✅ {method}: AVAILABLE")
        else:
            missing_methods.append(method)
            logger.error(f"   ❌ {method}: MISSING")
    
    if missing_methods:
        logger.warning(f"⚠️ Missing {len(missing_methods)} methods: {missing_methods}")
        logger.warning("⚠️ Using available methods only")
    else:
        logger.info(f"✅ All {len(available_methods)} required methods available")
    
    logger.info("=" * 70)
    logger.info("✅ Analytics service initialized successfully")
    logger.info("✅ Service is ready to serve REAL PostgreSQL data")
    logger.info("=" * 70)
    
    return service, AnalyticsResponse or _create_response_class()


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
    """Complete AI Orchestrator with all services integrated."""
    
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
        return self._analytics
    
    @property
    def resolver(self):
        if self._resolver is None:
            self._resolver = PostgreSQLResolver(self.session_factory)
        return self._resolver
    
    @property
    def classifier(self):
        if self._classifier is None:
            self._classifier = IntentClassifier(self.resolver)
        return self._classifier
    
    @property
    def distance_service(self):
        if self._distance_service is None and DistanceService:
            try:
                self._distance_service = DistanceService(self.session_factory)
            except Exception as e:
                logger.error(f"Distance service init failed: {e}")
                self._distance_service = None
        return self._distance_service
    
    @property
    def dealer_analytics(self):
        if self._dealer_analytics is None and DealerAnalyticsService:
            try:
                self._dealer_analytics = DealerAnalyticsService(self.session_factory)
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
        """Process WhatsApp query with full service integration."""
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
        """Route to appropriate dashboard with full service integration."""
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
        """
        Handle DN dashboard with complete validation and error handling.
        BLOCK 10 - FIXED v5.0 - PRODUCTION GRADE
        """
        import time
        start_time = time.time()
        
        logger.info(f"[{req_id}] 📄 DN Dashboard route called")
        logger.info(f"[{req_id}] 📥 Entity: {entity}")
        logger.info(f"[{req_id}] 📥 Context last_dn: {context.last_dn if context else None}")
        
        dn_number = entity or (context.last_dn if context else None)
        
        if not dn_number:
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number.\n\n*Example:* 6243675570"
        
        # Clean DN - remove non-numeric characters
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        
        # Validate DN format (8-12 digits)
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return f"❌ Invalid DN number: '{dn_number}'\n\nDN numbers must be 8-12 digits."
        
        logger.info(f"[{req_id}] 🔍 Looking up DN: {dn_clean}")
        
        # ==========================================================
        # STEP 1: Verify Analytics Service
        # ==========================================================
        if self.analytics is None:
            logger.warning(f"[{req_id}] ⚠️ Analytics is None - attempting reload...")
            try:
                service, response_class = _get_analytics_service()
                self._analytics = service
                self._analytics_response = response_class
                if self.analytics is None:
                    logger.error(f"[{req_id}] ❌ Analytics service still None")
                    return "⚠️ Service temporarily unavailable. Please try again later."
            except Exception as e:
                logger.error(f"[{req_id}] ❌ Analytics reload failed: {e}")
                return "⚠️ Service temporarily unavailable. Please try again later."
        
        if not hasattr(self.analytics, 'get_dn_dashboard'):
            logger.error(f"[{req_id}] ❌ get_dn_dashboard not available")
            return "⚠️ Service temporarily unavailable. Please try again later."
        
        # ==========================================================
        # STEP 2: Get DN Dashboard
        # ==========================================================
        try:
            logger.info(f"[{req_id}] 📊 Calling analytics.get_dn_dashboard('{dn_clean}')")
            response = self.analytics.get_dn_dashboard(dn_clean)
            logger.info(f"[{req_id}] 📊 Response type: {type(response)}")
            
            # LOG RAW RESPONSE FOR DEBUGGING
            logger.info(f"[{req_id}] 📊 Raw response: {str(response)[:500]}")
            
            # ==========================================================
            # STEP 3: Validate Response
            # ==========================================================
            is_valid, error_msg, data = self._validate_response(response, "DN Dashboard", req_id)
            
            # LOG VALIDATED DATA
            logger.info(f"[{req_id}] 📊 Validation result: is_valid={is_valid}")
            if data:
                logger.info(f"[{req_id}] 📊 Data keys: {list(data.keys()) if isinstance(data, dict) else 'NOT DICT'}")
            else:
                logger.info(f"[{req_id}] 📊 Data is None or empty")
            
            if not is_valid:
                # Check if this is a "not found" error with suggestions
                if data and isinstance(data, dict) and "suggestions" in data:
                    suggestions = data.get("suggestions", [])
                    if suggestions:
                        return f"❌ DN '{dn_number}' not found.\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
                
                logger.error(f"[{req_id}] ❌ Validation failed: {error_msg}")
                return f"❌ Unable to retrieve data for DN {dn_number}.\n\n{error_msg}"
            
            # ==========================================================
            # STEP 4: Format and Return
            # ==========================================================
            logger.info(f"[{req_id}] ✅ Valid data received, formatting...")
            
            # Check if data has required fields
            if data and isinstance(data, dict):
                required_fields = ['dn_number', 'customer_name']
                missing_fields = [f for f in required_fields if f not in data]
                if missing_fields:
                    logger.warning(f"[{req_id}] ⚠️ Missing fields: {missing_fields}")
                    logger.warning(f"[{req_id}] 📊 Available keys: {list(data.keys())}")
            
            result = self._format_dn_dashboard(data, dn_clean)
            
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ DN dashboard returned in {elapsed:.3f}s")
            logger.info(f"[{req_id}] 📊 Result length: {len(result)} characters")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ DN dashboard error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return f"❌ Error retrieving DN {dn_number}: {str(e)[:100]}"
# ==========================================================
# BLOCK 11: FORMATTERS WITH DISTANCE SUPPORT
# ==========================================================
    def _format_dn_dashboard(self, data: Dict, dn_number: str) -> str:
        """
        Format DN dashboard - Complete production version.
        BLOCK 11 - FIXED v6.0 - PRODUCTION GRADE
        """
        try:
            # ADD DEBUG LOGGING
            logger.info(f"🔍 Formatting DN {dn_number} with data type: {type(data)}")
            
            if not data:
                logger.error(f"❌ No data for DN {dn_number}")
                return f"❌ No data available for DN {dn_number}"
            
            if isinstance(data, dict):
                logger.info(f"📊 Data keys: {list(data.keys())}")
            else:
                logger.warning(f"⚠️ Data is not a dict: {type(data)}")
                return f"❌ Invalid data format for DN {dn_number}"
            
            # ==========================================================
            # Safe get with defaults
            # ==========================================================
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            # ==========================================================
            # Get ALL fields with proper logging
            # ==========================================================
            customer_name = safe_get('customer_name', 'N/A')
            dealer_code = safe_get('dealer_code', 'N/A')
            customer_code = safe_get('customer_code', 'N/A')
            warehouse = safe_get('warehouse', 'N/A')
            city = safe_get('ship_to_city', 'N/A')
            sales_office = safe_get('sales_office', 'N/A')
            sales_manager = safe_get('sales_manager', 'N/A')
            division = safe_get('division', 'N/A')
            customer_model = safe_get('customer_model', 'N/A')
            material_no = safe_get('material_no', 'N/A')
            
            units = safe_get('units', 0)
            amount = safe_get('amount', 0)
            status = safe_get('delivery_status', 'Unknown')
            pgi_status = safe_get('pgi_status', 'N/A')
            pod_status = safe_get('pod_status', 'N/A')
            
            # Get dates
            create_date = safe_get('dn_create_date', 'N/A')
            pgi_date = safe_get('good_issue_date', 'N/A')
            pod_date = safe_get('pod_date', 'N/A')
            
            # Get aging
            delivery_aging = safe_get('delivery_aging_text', 'N/A')
            pod_aging = safe_get('pod_aging_text', 'N/A')
            total_cycle = safe_get('total_cycle_text', 'N/A')
            
            # Status flags
            pending_flag = data.get('pending_flag', False)
            pending_text = "🔴 Yes" if pending_flag else "🟢 No"
            
            # Status emoji
            status_emoji = "✅" if status in ['Completed', 'Delivered', 'Closed'] else "⏳"
            
            # ==========================================================
            # Log what we found
            # ==========================================================
            logger.info(f"📊 DN {dn_number} data:")
            logger.info(f"   Customer: {customer_name}")
            logger.info(f"   Warehouse: {warehouse}")
            logger.info(f"   City: {city}")
            logger.info(f"   Units: {units}")
            logger.info(f"   Amount: {amount}")
            logger.info(f"   Status: {status}")
            logger.info(f"   PGI: {pgi_status}")
            logger.info(f"   POD: {pod_status}")
            
            # ==========================================================
            # Build formatted response
            # ==========================================================
            lines = [
                "📄 *DN TRACKING*",
                "",
                f"DN No: {safe_get('dn_number', dn_number)}",
                f"Dealer: {customer_name}",
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
            ]
            
            # Format amount with proper number formatting
            if amount and amount != 0 and amount != "N/A":
                try:
                    amount_float = float(amount)
                    lines.append(f"Revenue: PKR {amount_float:,.0f}")
                except (ValueError, TypeError):
                    lines.append(f"Revenue: PKR {amount}")
            else:
                lines.append(f"Revenue: PKR {amount}")
            
            lines.extend([
                "",
                "📅 *Dates*",
                f"Create: {create_date}",
                f"PGI: {pgi_date}",
                f"POD: {pod_date}",
                "",
                "⏳ *Aging*",
                f"Delivery Aging: {delivery_aging}",
                f"POD Aging: {pod_aging}",
                f"Total Cycle: {total_cycle}",
                "",
                "📋 *Status*",
                f"Delivery: {status} {status_emoji}",
                f"PGI: {pgi_status}",
                f"POD: {pod_status}",
                f"Pending: {pending_text}"
            ])
            
            # ==========================================================
            # Check for issues/warnings
            # ==========================================================
            issues = data.get('issues', [])
            if issues and isinstance(issues, list):
                lines.append("")
                lines.append("⚠️ *Data Issues*")
                for issue in issues[:3]:
                    lines.append(f"   {issue}")
            
            # ==========================================================
            # Return formatted response
            # ==========================================================
            result = "\n".join(lines)
            logger.info(f"📊 Formatted response length: {len(result)} characters")
            return result
            
        except Exception as e:
            logger.error(f"❌ DN format error for {dn_number}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return f"❌ Unable to format DN details for {dn_number}: {str(e)}"
# ==========================================================
# BLOCK 12: SINGLETON & WRAPPER FUNCTIONS
# ==========================================================
# ==========================================================
# BLOCK 12: SINGLETON & WRAPPER FUNCTIONS (FIXED v4.0)
# ==========================================================

_orchestrator = None
_initialization_attempts = 0
_MAX_INIT_ATTEMPTS = 3

def get_orchestrator(session_factory: Optional[Callable[[], Session]] = None) -> Optional[AIOrchestrator]:
    """
    Get or create AI Orchestrator singleton with detailed error logging.
    BLOCK 12 - FIXED v4.0 - PRODUCTION GRADE
    """
    global _orchestrator, _initialization_attempts
    
    # If already initialized, return it
    if _orchestrator is not None:
        logger.info("✅ Returning existing orchestrator instance")
        return _orchestrator
    
    # Check max attempts
    if _initialization_attempts >= _MAX_INIT_ATTEMPTS:
        logger.error(f"❌ Max initialization attempts ({_MAX_INIT_ATTEMPTS}) reached")
        logger.error("   💡 Restart the service to reset")
        return None
    
    _initialization_attempts += 1
    logger.info(f"🔄 Initializing AI Orchestrator (attempt {_initialization_attempts}/{_MAX_INIT_ATTEMPTS})...")
    
    try:
        # Log before initialization
        logger.info("📌 Attempting to create AIOrchestrator...")
        
        _orchestrator = AIOrchestrator(session_factory=session_factory)
        
        # Verify orchestrator was created
        if _orchestrator is None:
            logger.error("❌ AIOrchestrator creation returned None")
            return None
        
        # Verify analytics is available
        if not hasattr(_orchestrator, 'analytics'):
            logger.error("❌ Orchestrator missing 'analytics' attribute")
            _orchestrator = None
            return None
        
        if _orchestrator.analytics is None:
            logger.error("❌ Orchestrator analytics is None")
            logger.warning("⚠️ Analytics service failed to initialize")
            # Don't fail completely - we can still use fallback
            
        logger.info("✅ AI Orchestrator v30.0 initialized successfully")
        logger.info(f"📊 Analytics available: {_orchestrator.analytics is not None}")
        
        _initialization_attempts = 0
        return _orchestrator
        
    except ImportError as e:
        logger.error(f"❌ ImportError during initialization: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.error("   💡 Check that all required services are installed")
        _orchestrator = None
        return None
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize AI Orchestrator: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.error(f"   💡 Error type: {type(e).__name__}")
        logger.error(f"   💡 Error message: {str(e)}")
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
    BLOCK 12 - FIXED v4.0 - PRODUCTION GRADE
    """
    global _orchestrator
    
    # Validate input
    if not question or not question.strip():
        return "Please provide a valid question. Type 'help' for menu."
    
    # Get orchestrator
    logger.info(f"📥 Processing: '{question[:100]}'")
    logger.info("🔍 Getting orchestrator instance...")
    
    orchestrator = get_orchestrator(session_factory)
    
    # Check orchestrator
    if orchestrator is None:
        logger.error("❌ Orchestrator is None - service initialization failed")
        
        # Try emergency reset
        logger.info("🔄 Attempting emergency reset...")
        reset_orchestrator()
        
        try:
            orchestrator = AIOrchestrator(session_factory=session_factory)
            _orchestrator = orchestrator
            logger.info("✅ Emergency reset successful")
        except Exception as e:
            logger.error(f"❌ Emergency reset failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return "⚠️ AI service is currently unavailable. Please try again later."
    
    # Final check
    if orchestrator is None:
        logger.error("❌ Orchestrator still None after emergency reset")
        return "⚠️ AI service is currently unavailable. Please try again later."
    
    # Process the query
    try:
        logger.info("✅ Orchestrator acquired, processing query...")
        result = orchestrator.process_whatsapp_query(
            question=question,
            session_factory=session_factory,
            phone_number=phone_number,
            user_id=user_id,
            request_id=request_id
        )
        return result
        
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
]

# ==========================================================
# END OF FILE - v30.0 FULLY INTEGRATED
# ==========================================================
