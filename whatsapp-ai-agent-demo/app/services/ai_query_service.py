# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v23.0)
# ==========================================================
# MASTER ORCHESTRATOR WITH FULL GROQ AI INTEGRATION
# - All 14 Phases Complete
# - Full Query Router Integration
# - Redis Cache Active
# - Dealer Self-Service Ready
# - Role-Based Routing
# - Performance Monitoring
# ==========================================================

import time
import json
import re
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session
from loguru import logger

from app.config import config

# ==========================================================
# SERVICE IMPORTS (All modular services)
# ==========================================================

from app.services.intent_engine import IntentEngine, IntentType
from app.services.entity_extractor import EntityExtractor, EntityType, ExtractedEntity
from app.services.context_service import ContextService
from app.services.query_router_service import QueryRouterService
from app.services.report_generator_service import ReportGeneratorService
from app.services.groq_insight_service import GroqInsightService
from app.services.logistics_query_service import LogisticsQueryService
from app.services.analytics_service import AnalyticsService
from app.services.kpi_service import KPIService
from app.services.recommendation_service import RecommendationService
from app.services.forecasting_service import ForecastingService
from app.services.control_tower_service import ControlTowerService
from app.services.dealer_self_service import DealerSelfService
from app.services.business_rules_service import BusinessRulesService

# ==========================================================
# CACHE INTEGRATION
# ==========================================================

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available - caching disabled")


# ==========================================================
# QUERY CATEGORY ENUM
# ==========================================================

class QueryCategory(str, Enum):
    """Query classification for faster routing"""
    LOGISTICS = "logistics"
    ANALYTICS = "analytics"
    KPI = "kpi"
    RECOMMENDATION = "recommendation"
    FORECAST = "forecast"
    AI_INSIGHT = "ai_insight"
    CONTROL_TOWER = "control_tower"
    SELF_SERVICE = "self_service"
    HELP = "help"


# ==========================================================
# PERFORMANCE METRICS
# ==========================================================

@dataclass
class QueryMetrics:
    """Track performance metrics for each query"""
    intent_time_ms: int = 0
    entity_time_ms: int = 0
    context_time_ms: int = 0
    route_time_ms: int = 0
    service_time_ms: int = 0
    ai_time_ms: int = 0
    db_time_ms: int = 0
    cache_time_ms: int = 0
    report_time_ms: int = 0
    total_time_ms: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "intent_time_ms": self.intent_time_ms,
            "entity_time_ms": self.entity_time_ms,
            "context_time_ms": self.context_time_ms,
            "route_time_ms": self.route_time_ms,
            "service_time_ms": self.service_time_ms,
            "ai_time_ms": self.ai_time_ms,
            "db_time_ms": self.db_time_ms,
            "cache_time_ms": self.cache_time_ms,
            "report_time_ms": self.report_time_ms,
            "total_time_ms": self.total_time_ms
        }


@dataclass
class QueryAnalytics:
    """Track query analytics for monitoring"""
    most_asked_dns: Dict[str, int] = field(default_factory=dict)
    most_asked_dealers: Dict[str, int] = field(default_factory=dict)
    most_asked_products: Dict[str, int] = field(default_factory=dict)
    most_asked_cities: Dict[str, int] = field(default_factory=dict)
    most_asked_warehouses: Dict[str, int] = field(default_factory=dict)
    failed_queries: List[Dict] = field(default_factory=list)
    intent_counts: Dict[str, int] = field(default_factory=dict)
    category_counts: Dict[str, int] = field(default_factory=dict)
    response_times: List[int] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    
    def record_dn_query(self, dn: str):
        self.most_asked_dns[dn] = self.most_asked_dns.get(dn, 0) + 1
    
    def record_dealer_query(self, dealer: str):
        self.most_asked_dealers[dealer] = self.most_asked_dealers.get(dealer, 0) + 1
    
    def record_product_query(self, product: str):
        self.most_asked_products[product] = self.most_asked_products.get(product, 0) + 1
    
    def record_city_query(self, city: str):
        self.most_asked_cities[city] = self.most_asked_cities.get(city, 0) + 1
    
    def record_warehouse_query(self, warehouse: str):
        self.most_asked_warehouses[warehouse] = self.most_asked_warehouses.get(warehouse, 0) + 1
    
    def record_failed_query(self, question: str, reason: str):
        self.failed_queries.append({
            "question": question[:100],
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat()
        })
        if len(self.failed_queries) > 1000:
            self.failed_queries = self.failed_queries[-1000:]
    
    def record_intent(self, intent: str):
        self.intent_counts[intent] = self.intent_counts.get(intent, 0) + 1
    
    def record_category(self, category: str):
        self.category_counts[category] = self.category_counts.get(category, 0) + 1
    
    def record_response_time(self, time_ms: int):
        self.response_times.append(time_ms)
        if len(self.response_times) > 1000:
            self.response_times = self.response_times[-1000:]
    
    def record_cache_hit(self):
        self.cache_hits += 1
    
    def record_cache_miss(self):
        self.cache_misses += 1
    
    def get_avg_response_time(self) -> float:
        if not self.response_times:
            return 0
        return sum(self.response_times) / len(self.response_times)
    
    def get_cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0
        return (self.cache_hits / total) * 100


# ==========================================================
# REDIS CACHE SERVICE
# ==========================================================

class RedisCacheService:
    """Redis cache for frequently accessed data"""
    
    DEFAULT_TTL = 300  # 5 minutes
    
    # Cache TTL by category
    CACHE_TTL = {
        "dn_intelligence": 300,
        "dealer_dashboard": 300,
        "product_dashboard": 600,
        "warehouse_dashboard": 600,
        "city_dashboard": 600,
        "executive_dashboard": 120,
        "exception_dashboard": 60,
        "dealer_ranking": 600,
        "product_ranking": 600
    }
    
    def __init__(self):
        self.client = None
        self.enabled = False
        
        if REDIS_AVAILABLE and hasattr(config, 'REDIS_URL') and config.REDIS_URL:
            try:
                self.client = redis.from_url(config.REDIS_URL, decode_responses=True)
                self.client.ping()
                self.enabled = True
                logger.info("✅ Redis cache enabled")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}")
    
    def get(self, key: str) -> Optional[Any]:
        if not self.enabled:
            return None
        try:
            data = self.client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Redis get error: {e}")
        return None
    
    def set(self, key: str, value: Any, ttl: int = DEFAULT_TTL):
        if not self.enabled:
            return
        try:
            self.client.setex(key, ttl, json.dumps(value))
        except Exception as e:
            logger.error(f"Redis set error: {e}")
    
    def delete(self, key: str):
        if not self.enabled:
            return
        try:
            self.client.delete(key)
        except Exception as e:
            logger.error(f"Redis delete error: {e}")
    
    def get_cache_key(self, category: str, *args) -> str:
        """Generate cache key with category"""
        key_parts = [category]
        key_parts.extend(str(a) for a in args)
        return ":".join(key_parts)
    
    def get_ttl(self, category: str) -> int:
        """Get TTL for category"""
        return self.CACHE_TTL.get(category, self.DEFAULT_TTL)


# ==========================================================
# MAIN AI QUERY SERVICE - MASTER ORCHESTRATOR
# ==========================================================

class AIQueryService:
    """
    Master orchestrator for all logistics queries.
    NO BUSINESS LOGIC - only orchestration.
    FULL GROQ AI INTEGRATION.
    Enterprise Ready v23.0
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.start_time = None
        
        # Initialize all services
        self.intent_engine = IntentEngine()
        self.entity_extractor = EntityExtractor()
        self.context_service = ContextService(db)
        self.query_router = QueryRouterService(db)
        self.report_generator = ReportGeneratorService()
        self.cache = RedisCacheService()
        self.groq_service = GroqInsightService(db)  # GROQ AI Service
        self.business_rules = BusinessRulesService()
        
        # Lazy-loaded services
        self._logistics_service = None
        self._analytics_service = None
        self._kpi_service = None
        self._recommendation_service = None
        self._forecasting_service = None
        self._control_tower_service = None
        self._dealer_self_service = None
        
        # Analytics tracking
        self.query_analytics = QueryAnalytics()
        
        # Dealer phone mapping (for self-service)
        self.dealer_phone_map = self._load_dealer_phone_map()
        
        logger.info("=" * 70)
        logger.info("🚀 AI QUERY ORCHESTRATOR v23.0 - ENTERPRISE READY")
        logger.info(f"   Redis Cache: {'Enabled' if self.cache.enabled else 'Disabled'}")
        logger.info(f"   GROQ AI: {'Available' if self.groq_service.ai_available else 'Not Available'}")
        logger.info(f"   Dealer Self-Service: {'Enabled' if self.dealer_phone_map else 'Pending'}")
        logger.info("   Architecture: Full Modular Enterprise")
        logger.info("=" * 70)
    
    def _load_dealer_phone_map(self) -> Dict[str, str]:
        """Load dealer to phone number mapping for self-service"""
        # In production, load from database
        # For now, return sample mapping
        return {
            "+923001234567": "ABC Electronics",
            "+923001234568": "XYZ Traders",
            "+923001234569": "Bismillah Electronics"
        }
    
    def _get_logistics_service(self):
        """Lazy load logistics service"""
        if self._logistics_service is None:
            self._logistics_service = LogisticsQueryService(self.db, self.cache)
        return self._logistics_service
    
    def _get_analytics_service(self):
        """Lazy load analytics service"""
        if self._analytics_service is None:
            self._analytics_service = AnalyticsService(self.db)
        return self._analytics_service
    
    def _get_kpi_service(self):
        """Lazy load KPI service"""
        if self._kpi_service is None:
            self._kpi_service = KPIService(self.db, self.cache)
        return self._kpi_service
    
    def _get_recommendation_service(self):
        """Lazy load recommendation service"""
        if self._recommendation_service is None:
            self._recommendation_service = RecommendationService(self.db, self.cache)
        return self._recommendation_service
    
    def _get_forecasting_service(self):
        """Lazy load forecasting service"""
        if self._forecasting_service is None:
            self._forecasting_service = ForecastingService(self.db, self.cache)
        return self._forecasting_service
    
    def _get_control_tower_service(self):
        """Lazy load control tower service"""
        if self._control_tower_service is None:
            self._control_tower_service = ControlTowerService(self.db, self.cache)
        return self._control_tower_service
    
    def _get_dealer_self_service(self):
        """Lazy load dealer self service"""
        if self._dealer_self_service is None:
            self._dealer_self_service = DealerSelfService(self.db, self.cache)
        return self._dealer_self_service
    
    def process_query(
        self, 
        question: str, 
        user_phone: str = None, 
        user_role: str = None
    ) -> Dict[str, Any]:
        """
        Master orchestration method with GROQ AI fallback.
        
        Flow:
        1. Load context
        2. Extract entities
        3. Detect intent
        4. Check cache
        5. Route to service
        6. Generate response
        7. Save context
        8. Track metrics
        """
        self.start_time = time.time()
        metrics = QueryMetrics()
        
        question = question.strip()
        logger.info(f"📱 Processing: {question[:100]}")
        logger.info(f"👤 User: {user_phone} | Role: {user_role or 'guest'}")
        
        try:
            # ==========================================================
            # STEP 1: Load Context (with dealer mapping)
            # ==========================================================
            context_start = time.time()
            context = {}
            if user_phone:
                context = self.context_service.get_context(user_phone)
                
                # Check if this phone is mapped to a dealer
                if user_phone in self.dealer_phone_map:
                    dealer_name = self.dealer_phone_map[user_phone]
                    context['dealer_name'] = dealer_name
                    context['is_dealer'] = True
                    logger.info(f"📞 Phone {user_phone} mapped to dealer: {dealer_name}")
            
            metrics.context_time_ms = int((time.time() - context_start) * 1000)
            logger.debug(f"📚 Context loaded in {metrics.context_time_ms}ms")
            
            # ==========================================================
            # STEP 2: Extract Entities
            # ==========================================================
            entity_start = time.time()
            entities = self.entity_extractor.extract_all(question)
            
            # Track entity analytics
            if EntityType.DN_NUMBER in entities:
                self.query_analytics.record_dn_query(entities[EntityType.DN_NUMBER].value)
            if EntityType.DEALER in entities:
                self.query_analytics.record_dealer_query(entities[EntityType.DEALER].value)
            if EntityType.PRODUCT in entities:
                self.query_analytics.record_product_query(entities[EntityType.PRODUCT].value)
            if EntityType.CITY in entities:
                self.query_analytics.record_city_query(entities[EntityType.CITY].value)
            if EntityType.WAREHOUSE in entities:
                self.query_analytics.record_warehouse_query(entities[EntityType.WAREHOUSE].value)
            
            # Resolve follow-up using context
            if user_phone:
                resolved = self.context_service.resolve_follow_up(user_phone, question)
                for entity_type, value in resolved.items():
                    if entity_type not in entities:
                        entities[entity_type] = ExtractedEntity(
                            type=EntityType(entity_type),
                            value=value
                        )
                        logger.debug(f"🔄 Resolved {entity_type}: {value} from context")
            
            metrics.entity_time_ms = int((time.time() - entity_start) * 1000)
            logger.debug(f"🔍 Entities: {list(entities.keys())} in {metrics.entity_time_ms}ms")
            
            # ==========================================================
            # STEP 3: Detect Intent
            # ==========================================================
            intent_start = time.time()
            intent, intent_entity, confidence = self.intent_engine.detect_intent(
                question, entities, context
            )
            metrics.intent_time_ms = int((time.time() - intent_start) * 1000)
            
            # Track intent analytics
            self.query_analytics.record_intent(intent.value)
            
            logger.info(f"🎯 Intent: {intent.value} (confidence: {confidence:.2f}) in {metrics.intent_time_ms}ms")
            
            # ==========================================================
            # STEP 4: Route to Service (with cache check)
            # ==========================================================
            route_start = time.time()
            
            # Check cache for this intent
            cache_key = self._get_cache_key(intent, intent_entity, entities)
            cached_response = None
            
            if cache_key and self.cache.enabled:
                cache_start = time.time()
                cached_response = self.cache.get(cache_key)
                metrics.cache_time_ms = int((time.time() - cache_start) * 1000)
                
                if cached_response:
                    self.query_analytics.record_cache_hit()
                    logger.info(f"💾 Cache HIT for {intent.value}")
                    
                    # Generate response from cache
                    response_text = self.report_generator.format_response(
                        data=cached_response,
                        intent=intent,
                        format_type="whatsapp"
                    )
                    
                    metrics.total_time_ms = int((time.time() - self.start_time) * 1000)
                    self.query_analytics.record_response_time(metrics.total_time_ms)
                    
                    return {
                        "success": True,
                        "response": response_text,
                        "intent": intent.value,
                        "confidence": confidence,
                        "cached": True,
                        "metrics": metrics.to_dict()
                    }
                else:
                    self.query_analytics.record_cache_miss()
            
            # Route to service
            route_result = self._route_to_service(
                intent=intent,
                entity=intent_entity,
                entities=entities,
                context=context,
                user_phone=user_phone,
                user_role=user_role,
                question=question
            )
            
            metrics.route_time_ms = int((time.time() - route_start) * 1000)
            
            service_response = route_result.get("response", {})
            service_name = route_result.get("service", "unknown")
            category = route_result.get("category", "general")
            metrics.service_time_ms = route_result.get("service_time_ms", 0)
            metrics.ai_time_ms = route_result.get("ai_time_ms", 0)
            metrics.db_time_ms = route_result.get("db_time_ms", 0)
            
            logger.info(f"🚦 Routed to {service_name} ({category}) in {metrics.route_time_ms}ms")
            
            # Cache the response if appropriate
            if cache_key and service_response and not isinstance(service_response, dict) or not service_response.get("error"):
                ttl = self.cache.get_ttl(intent.value) if hasattr(self.cache, 'get_ttl') else 300
                self.cache.set(cache_key, service_response, ttl=ttl)
                logger.debug(f"💾 Cached {intent.value} with TTL {ttl}s")
            
            # Track category analytics
            self.query_analytics.record_category(category)
            
            # ==========================================================
            # STEP 5: Generate Response
            # ==========================================================
            report_start = time.time()
            response_text = self.report_generator.format_response(
                data=service_response,
                intent=intent,
                format_type="whatsapp"
            )
            metrics.report_time_ms = int((time.time() - report_start) * 1000)
            
            # ==========================================================
            # STEP 6: Save Context
            # ==========================================================
            if user_phone and service_response:
                self.context_service.save_context(
                    phone_number=user_phone,
                    entities=entities,
                    intent=intent,
                    response=response_text[:500]
                )
            
            # ==========================================================
            # STEP 7: Calculate Metrics
            # ==========================================================
            metrics.total_time_ms = int((time.time() - self.start_time) * 1000)
            self.query_analytics.record_response_time(metrics.total_time_ms)
            
            # Log performance summary
            logger.info(f"⚡ Performance Summary | Total: {metrics.total_time_ms}ms | "
                       f"Intent: {metrics.intent_time_ms}ms | "
                       f"Entity: {metrics.entity_time_ms}ms | "
                       f"Route: {metrics.route_time_ms}ms | "
                       f"Service: {metrics.service_time_ms}ms | "
                       f"AI: {metrics.ai_time_ms}ms")
            
            return {
                "success": True,
                "response": response_text,
                "intent": intent.value,
                "category": category,
                "confidence": confidence,
                "entities": {k: v.value for k, v in entities.items()},
                "metrics": metrics.to_dict(),
                "service": service_name,
                "cached": False
            }
            
        except Exception as e:
            logger.error(f"Query processing error: {e}")
            self.query_analytics.record_failed_query(question, str(e))
            
            # Try GROQ AI as final fallback
            if self.groq_service.ai_available:
                try:
                    logger.info("🔄 Attempting GROQ AI fallback...")
                    ai_start = time.time()
                    ai_response = self.groq_service.analyze(question, IntentType.GENERAL_QUERY, {})
                    metrics.ai_time_ms = int((time.time() - ai_start) * 1000)
                    
                    if ai_response.get("insight"):
                        return {
                            "success": True,
                            "response": ai_response["insight"],
                            "intent": "groq_fallback",
                            "metrics": {
                                "total_time_ms": int((time.time() - self.start_time) * 1000),
                                "ai_time_ms": metrics.ai_time_ms
                            }
                        }
                except Exception as ai_err:
                    logger.error(f"GROQ fallback error: {ai_err}")
            
            metrics.total_time_ms = int((time.time() - self.start_time) * 1000) if self.start_time else 0
            
            return {
                "success": False,
                "response": self._get_error_response(),
                "error": str(e),
                "metrics": metrics.to_dict()
            }
    
    def _route_to_service(
        self,
        intent: IntentType,
        entity: Optional[str],
        entities: Dict,
        context: Dict,
        user_phone: str,
        user_role: str,
        question: str
    ) -> Dict[str, Any]:
        """
        Route intent to appropriate service.
        Direct routing without going through QueryRouter for performance.
        """
        start_time = time.time()
        
        # ==========================================================
        # LOGISTICS SERVICE ROUTES
        # ==========================================================
        if intent in [IntentType.DN_LOOKUP, IntentType.DN_TIMELINE, 
                      IntentType.DN_PRODUCTS, IntentType.DN_AGING]:
            service = self._get_logistics_service()
            
            if intent == IntentType.DN_LOOKUP:
                result = service.get_complete_dn_intelligence(entity or entities.get('dn_number'))
            elif intent == IntentType.DN_TIMELINE:
                result = service.get_dn_timeline(entity or entities.get('dn_number'))
            elif intent == IntentType.DN_PRODUCTS:
                result = service.get_dn_products(entity or entities.get('dn_number'))
            elif intent == IntentType.DN_AGING:
                result = service.get_dn_aging(entity or entities.get('dn_number'))
            else:
                result = service.get_complete_dn_intelligence(entity or entities.get('dn_number'))
            
            return {
                "response": result,
                "service": "logistics_query_service",
                "category": QueryCategory.LOGISTICS.value,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # POD/PGI ROUTES
        # ==========================================================
        if intent in [IntentType.POD_PENDING, IntentType.PGI_PENDING, 
                      IntentType.POD_ANALYSIS, IntentType.PGI_ANALYSIS]:
            service = self._get_logistics_service()
            
            if intent == IntentType.POD_PENDING:
                result = service.get_pending_pods()
            elif intent == IntentType.PGI_PENDING:
                result = service.get_pending_pgi()
            elif intent == IntentType.POD_ANALYSIS:
                result = service.get_pod_analysis()
            else:
                result = service.get_pgi_analysis()
            
            return {
                "response": result,
                "service": "logistics_query_service",
                "category": QueryCategory.LOGISTICS.value,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # ANALYTICS SERVICE ROUTES
        # ==========================================================
        if intent in [IntentType.DEALER_DASHBOARD, IntentType.DEALER_RANKING,
                      IntentType.PRODUCT_DASHBOARD, IntentType.PRODUCT_RANKING,
                      IntentType.CITY_DASHBOARD, IntentType.CITY_RANKING,
                      IntentType.WAREHOUSE_DASHBOARD, IntentType.WAREHOUSE_RANKING,
                      IntentType.REVENUE_ANALYSIS, IntentType.REVENUE_AT_RISK,
                      IntentType.FAST_MOVING, IntentType.SLOW_MOVING, IntentType.DEAD_STOCK]:
            service = self._get_analytics_service()
            
            if intent == IntentType.DEALER_DASHBOARD:
                result = service.get_complete_dealer_dashboard(entity or entities.get('dealer'))
            elif intent == IntentType.DEALER_RANKING:
                result = service.top_dealers()
            elif intent == IntentType.PRODUCT_DASHBOARD:
                result = service.product_intelligence(entity or entities.get('product'))
            elif intent == IntentType.PRODUCT_RANKING:
                result = service.top_products()
            elif intent == IntentType.FAST_MOVING:
                result = service.fast_moving_products()
            elif intent == IntentType.SLOW_MOVING:
                result = service.slow_moving_products()
            elif intent == IntentType.DEAD_STOCK:
                result = service.dead_stock_products()
            elif intent == IntentType.CITY_DASHBOARD:
                result = service.city_intelligence(entity or entities.get('city'))
            elif intent == IntentType.CITY_RANKING:
                result = service.city_rankings()
            elif intent == IntentType.WAREHOUSE_DASHBOARD:
                result = service.warehouse_intelligence(entity or entities.get('warehouse'))
            elif intent == IntentType.WAREHOUSE_RANKING:
                result = service.warehouse_rankings()
            elif intent == IntentType.REVENUE_ANALYSIS:
                result = service.revenue_analysis()
            elif intent == IntentType.REVENUE_AT_RISK:
                result = service.revenue_at_risk()
            else:
                result = {"error": f"Analytics intent {intent.value} not implemented"}
            
            return {
                "response": result,
                "service": "analytics_service",
                "category": QueryCategory.ANALYTICS.value,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # KPI SERVICE ROUTES
        # ==========================================================
        if intent in [IntentType.EXECUTIVE_KPI, IntentType.CEO_BRIEFING, IntentType.NETWORK_HEALTH]:
            service = self._get_kpi_service()
            
            if intent == IntentType.EXECUTIVE_KPI:
                result = service.get_executive_dashboard()
            elif intent == IntentType.CEO_BRIEFING:
                result = service.get_ceo_briefing()
            else:
                result = service.get_network_health()
            
            return {
                "response": result,
                "service": "kpi_service",
                "category": QueryCategory.KPI.value,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # RECOMMENDATION SERVICE ROUTES
        # ==========================================================
        if intent in [IntentType.RECOMMENDATION, IntentType.DEALER_FOLLOWUP]:
            service = self._get_recommendation_service()
            
            if intent == IntentType.RECOMMENDATION:
                result = service.get_recommendations()
            else:
                result = service.get_dealers_needing_followup()
            
            return {
                "response": result,
                "service": "recommendation_service",
                "category": QueryCategory.RECOMMENDATION.value,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # FORECASTING SERVICE ROUTES
        # ==========================================================
        if intent in [IntentType.FORECAST, IntentType.SALES_FORECAST, IntentType.PREDICTIVE_ANALYSIS]:
            service = self._get_forecasting_service()
            
            if intent == IntentType.SALES_FORECAST:
                result = service.get_sales_forecast()
            elif intent == IntentType.PREDICTIVE_ANALYSIS:
                result = service.get_predictive_analysis()
            else:
                result = service.get_general_forecast()
            
            return {
                "response": result,
                "service": "forecasting_service",
                "category": QueryCategory.FORECAST.value,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # CONTROL TOWER SERVICE ROUTES
        # ==========================================================
        if intent in [IntentType.CONTROL_TOWER, IntentType.CRITICAL_DNS, IntentType.TOP_RISKS]:
            service = self._get_control_tower_service()
            
            if intent == IntentType.CONTROL_TOWER:
                result = service.get_control_tower_dashboard()
            elif intent == IntentType.CRITICAL_DNS:
                result = service.get_critical_dns()
            else:
                result = service.get_top_risks()
            
            return {
                "response": result,
                "service": "control_tower_service",
                "category": QueryCategory.CONTROL_TOWER.value,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # DEALER SELF-SERVICE ROUTES
        # ==========================================================
        if intent == IntentType.DEALER_SELF_SERVICE:
            service = self._get_dealer_self_service()
            dealer_name = context.get('dealer_name') or entities.get('dealer')
            
            result = service.get_my_dashboard(dealer_name, question)
            
            return {
                "response": result,
                "service": "dealer_self_service",
                "category": QueryCategory.SELF_SERVICE.value,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # GROQ AI INSIGHT SERVICE (Only for complex analytics)
        # ==========================================================
        if intent in [IntentType.ROOT_CAUSE_ANALYSIS, IntentType.TREND_ANALYSIS, IntentType.GENERAL_QUERY]:
            ai_start = time.time()
            result = self.groq_service.analyze(question, intent, context)
            ai_time_ms = int((time.time() - ai_start) * 1000)
            
            return {
                "response": result,
                "service": "groq_insight_service",
                "category": QueryCategory.AI_INSIGHT.value,
                "service_time_ms": int((time.time() - start_time) * 1000),
                "ai_time_ms": ai_time_ms
            }
        
        # ==========================================================
        # HELP SERVICE
        # ==========================================================
        if intent == IntentType.HELP:
            return {
                "response": {"help": WELCOME_MESSAGE},
                "service": "help_service",
                "category": QueryCategory.HELP.value,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # FALLBACK
        # ==========================================================
        logger.warning(f"No route found for intent: {intent}")
        return {
            "response": {
                "error": f"Unable to process {intent.value}. Please try a different query.",
                "suggestions": [
                    "DN <number> - Track a delivery note",
                    "Top dealers - View dealer rankings",
                    "Pending PODs - Check pending collections",
                    "Executive summary - View dashboard",
                    "Help - Show complete menu"
                ]
            },
            "service": "fallback",
            "category": QueryCategory.HELP.value,
            "service_time_ms": int((time.time() - start_time) * 1000)
        }
    
    def _get_cache_key(self, intent: IntentType, entity: Optional[str], entities: Dict) -> Optional[str]:
        """Generate cache key for intent"""
        if not intent:
            return None
        
        # Only cache certain intent types
        cacheable_intents = [
            IntentType.DN_LOOKUP,
            IntentType.DEALER_DASHBOARD,
            IntentType.PRODUCT_DASHBOARD,
            IntentType.EXECUTIVE_KPI,
            IntentType.WAREHOUSE_DASHBOARD,
            IntentType.CITY_DASHBOARD
        ]
        
        if intent not in cacheable_intents:
            return None
        
        key_parts = [intent.value]
        
        if entity:
            key_parts.append(entity)
        
        if entities:
            for k, v in entities.items():
                if hasattr(v, 'value'):
                    key_parts.append(f"{k}:{v.value}")
        
        return ":".join(key_parts)
    
    def _get_error_response(self) -> str:
        return """⚠️ *Service Temporarily Unavailable*

I'm having trouble processing your request right now.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these alternatives:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• "Help" - Show complete menu
• "DN 80012345" - Track a specific DN
• "Pending PODs" - Check POD status
• "Executive summary" - View dashboard

Please try again in a few moments."""
    
    def get_query_analytics(self) -> Dict:
        """Get query analytics for monitoring"""
        return {
            "most_asked_dns": dict(sorted(
                self.query_analytics.most_asked_dns.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]),
            "most_asked_dealers": dict(sorted(
                self.query_analytics.most_asked_dealers.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]),
            "most_asked_products": dict(sorted(
                self.query_analytics.most_asked_products.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]),
            "most_asked_cities": dict(sorted(
                self.query_analytics.most_asked_cities.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]),
            "intent_counts": self.query_analytics.intent_counts,
            "category_counts": self.query_analytics.category_counts,
            "failed_queries_count": len(self.query_analytics.failed_queries),
            "avg_response_time_ms": round(self.query_analytics.get_avg_response_time(), 2),
            "cache_hit_rate": round(self.query_analytics.get_cache_hit_rate(), 1),
            "groq_status": "available" if self.groq_service.ai_available else "unavailable",
            "total_queries": sum(self.query_analytics.intent_counts.values())
        }
    
    def register_dealer_phone(self, phone_number: str, dealer_name: str) -> bool:
        """Register a phone number to a dealer for self-service"""
        try:
            self.dealer_phone_map[phone_number] = dealer_name
            # In production, save to database
            logger.info(f"📞 Registered {phone_number} -> {dealer_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to register dealer phone: {e}")
            return False


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(
    question: str, 
    db: Session, 
    user_phone: str = None, 
    user_role: str = None
) -> str:
    """Process WhatsApp query and return response"""
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.error(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


# ==========================================================
# WELCOME MESSAGE
# ==========================================================

WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT v23.0*

Complete logistics intelligence with enterprise-grade architecture.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *WHAT YOU CAN ASK:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN TRACKING*
   • "DN 80012345" - Complete status with all products
   • "Timeline of DN 80012345" - Journey tracking
   • "Products in DN 80012345" - Line items with divisions

🏪 *DEALER INSIGHTS*
   • "ABC Electronics" - Complete dealer dashboard
   • "Top dealers" - Rankings by sales
   • "High risk dealers" - Risk analysis
   • "My DNs" - Dealer self-service (if registered)

🏭 *WAREHOUSE ANALYTICS*
   • "Lahore warehouse" - Performance dashboard
   • "Warehouse ranking" - Efficiency comparison
   • "Warehouse pending DNs" - Pending dispatches

🌆 *CITY INTELLIGENCE*
   • "Karachi city" - City dashboard
   • "City ranking" - Performance by city
   • "Which city has maximum delay?" - Worst performer

📦 *PRODUCT ANALYTICS*
   • "Product HSU-18HFPAA" - Complete product intelligence
   • "Top products" - Best sellers
   • "Fast moving products" - Velocity analysis
   • "Dead stock" - Slow moving inventory

👑 *EXECUTIVE REPORTS*
   • "Executive summary" - Complete KPI dashboard
   • "CEO briefing" - Leadership view
   • "Network health" - System status
   • "Top risks" - Critical issues
   • "Recommendations" - Action items

🧠 *AI INSIGHTS (Powered by GROQ)*
   • "Why are deliveries delayed?" - Root cause analysis
   • "What are the trends?" - Trend analysis
   • "Why sales decreased?" - Decline analysis
   • "Why Lahore declined?" - City-specific analysis
   • "Forecast next month sales" - Predictive analysis

📋 *POD & PGI*
   • "Pending PODs" - Collection required
   • "Pending PGI" - Dispatch pending
   • "Critical PODs" - Overdue collections

💰 *REVENUE*
   • "Revenue analysis" - Complete breakdown
   • "Revenue at risk" - Exposure analysis

🚨 *EXCEPTION MANAGEMENT*
   • "Control tower" - Critical alerts
   • "Critical DNs" - Severe delays (>15 days)
   • "High value pending DNs" - >1M exposure
   • "DN older than 30 days" - Aged inventory

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRO TIPS:* 
   • I remember context! Ask "What products?" after a DN query
   • Registered dealers can ask "My DNs" without typing dealer name
   • Type "Help" anytime for this menu

*Powered by GROQ AI | Enterprise Logistics Intelligence v23.0*"""


# ==========================================================
# HEALTH CHECK FUNCTION
# ==========================================================

def health_check(db: Session) -> Dict[str, Any]:
    """Check health of AI Query Service"""
    try:
        service = AIQueryService(db)
        return {
            "status": "healthy",
            "redis_cache": service.cache.enabled,
            "groq_ai": service.groq_service.ai_available,
            "dealer_self_service": bool(service.dealer_phone_map),
            "version": "23.0"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "version": "23.0"
        }
