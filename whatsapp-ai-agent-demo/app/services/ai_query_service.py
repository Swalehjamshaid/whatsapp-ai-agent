# ==========================================================
# FILE: app/services/query_router_service.py (COMPLETE v2.0)
# ==========================================================
# CENTRAL ROUTER - ALL INTENTS MAPPED TO SERVICES
# ==========================================================

import time
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass

from sqlalchemy.orm import Session
from loguru import logger

from app.services.intent_engine import IntentType


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


@dataclass
class RouteResult:
    """Result of routing a query"""
    service: str
    category: QueryCategory
    method: str
    response: Dict[str, Any]
    service_time_ms: int = 0
    ai_time_ms: int = 0
    cache_hit: bool = False


class QueryRouterService:
    """
    CENTRAL QUERY ROUTER - COMPLETE IMPLEMENTATION
    
    Routes ALL intents to appropriate services with caching.
    """
    
    # Complete Routing Matrix
    ROUTING_MATRIX = {
        # ========== LOGISTICS SERVICE ==========
        IntentType.DN_LOOKUP: {
            "service": "logistics_query_service",
            "category": QueryCategory.LOGISTICS,
            "method": "get_complete_dn_intelligence",
            "cache_ttl": 300
        },
        IntentType.DN_TIMELINE: {
            "service": "logistics_query_service",
            "category": QueryCategory.LOGISTICS,
            "method": "get_dn_timeline",
            "cache_ttl": 300
        },
        IntentType.DN_PRODUCTS: {
            "service": "logistics_query_service",
            "category": QueryCategory.LOGISTICS,
            "method": "get_dn_products",
            "cache_ttl": 300
        },
        IntentType.POD_PENDING: {
            "service": "logistics_query_service",
            "category": QueryCategory.LOGISTICS,
            "method": "get_pending_pods",
            "cache_ttl": 60
        },
        IntentType.PGI_PENDING: {
            "service": "logistics_query_service",
            "category": QueryCategory.LOGISTICS,
            "method": "get_pending_pgi",
            "cache_ttl": 60
        },
        IntentType.POD_ANALYSIS: {
            "service": "logistics_query_service",
            "category": QueryCategory.LOGISTICS,
            "method": "get_pod_analysis",
            "cache_ttl": 300
        },
        IntentType.PGI_ANALYSIS: {
            "service": "logistics_query_service",
            "category": QueryCategory.LOGISTICS,
            "method": "get_pgi_analysis",
            "cache_ttl": 300
        },
        
        # ========== ANALYTICS SERVICE ==========
        IntentType.DEALER_DASHBOARD: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_dealer_dashboard",
            "cache_ttl": 300
        },
        IntentType.DEALER_RANKING: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_dealer_ranking",
            "cache_ttl": 600
        },
        IntentType.DEALER_RISK: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_high_risk_dealers",
            "cache_ttl": 300
        },
        IntentType.PRODUCT_DASHBOARD: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_product_dashboard",
            "cache_ttl": 300
        },
        IntentType.PRODUCT_RANKING: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_product_ranking",
            "cache_ttl": 600
        },
        IntentType.FAST_MOVING: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_fast_moving_products",
            "cache_ttl": 600
        },
        IntentType.SLOW_MOVING: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_slow_moving_products",
            "cache_ttl": 600
        },
        IntentType.DEAD_STOCK: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_dead_stock_products",
            "cache_ttl": 3600
        },
        IntentType.CITY_DASHBOARD: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_city_dashboard",
            "cache_ttl": 300
        },
        IntentType.CITY_RANKING: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_city_ranking",
            "cache_ttl": 600
        },
        IntentType.WAREHOUSE_DASHBOARD: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_warehouse_dashboard",
            "cache_ttl": 300
        },
        IntentType.WAREHOUSE_RANKING: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_warehouse_ranking",
            "cache_ttl": 600
        },
        IntentType.DIVISION_DASHBOARD: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_division_dashboard",
            "cache_ttl": 300
        },
        IntentType.MANAGER_DASHBOARD: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_manager_dashboard",
            "cache_ttl": 300
        },
        IntentType.REVENUE_ANALYSIS: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_revenue_analysis",
            "cache_ttl": 300
        },
        IntentType.REVENUE_AT_RISK: {
            "service": "analytics_service",
            "category": QueryCategory.ANALYTICS,
            "method": "get_revenue_at_risk",
            "cache_ttl": 300
        },
        
        # ========== KPI SERVICE ==========
        IntentType.EXECUTIVE_KPI: {
            "service": "kpi_service",
            "category": QueryCategory.KPI,
            "method": "get_executive_dashboard",
            "cache_ttl": 300
        },
        IntentType.CEO_BRIEFING: {
            "service": "kpi_service",
            "category": QueryCategory.KPI,
            "method": "get_ceo_briefing",
            "cache_ttl": 600
        },
        IntentType.NETWORK_HEALTH: {
            "service": "kpi_service",
            "category": QueryCategory.KPI,
            "method": "get_network_health",
            "cache_ttl": 300
        },
        IntentType.TOP_RISKS: {
            "service": "kpi_service",
            "category": QueryCategory.KPI,
            "method": "get_top_risks",
            "cache_ttl": 300
        },
        
        # ========== RECOMMENDATION SERVICE ==========
        IntentType.RECOMMENDATION: {
            "service": "recommendation_service",
            "category": QueryCategory.RECOMMENDATION,
            "method": "get_recommendations",
            "cache_ttl": 600
        },
        IntentType.DEALER_FOLLOWUP: {
            "service": "recommendation_service",
            "category": QueryCategory.RECOMMENDATION,
            "method": "get_dealers_needing_followup",
            "cache_ttl": 300
        },
        IntentType.CRITICAL_DELAY_ACTION: {
            "service": "recommendation_service",
            "category": QueryCategory.RECOMMENDATION,
            "method": "get_critical_delay_actions",
            "cache_ttl": 300
        },
        
        # ========== FORECASTING SERVICE ==========
        IntentType.FORECAST: {
            "service": "forecasting_service",
            "category": QueryCategory.FORECAST,
            "method": "get_general_forecast",
            "cache_ttl": 3600
        },
        IntentType.SALES_FORECAST: {
            "service": "forecasting_service",
            "category": QueryCategory.FORECAST,
            "method": "get_sales_forecast",
            "cache_ttl": 3600
        },
        IntentType.PREDICTIVE_ANALYSIS: {
            "service": "forecasting_service",
            "category": QueryCategory.FORECAST,
            "method": "get_predictive_analysis",
            "cache_ttl": 3600
        },
        
        # ========== GROQ AI INSIGHT SERVICE ==========
        IntentType.ROOT_CAUSE_ANALYSIS: {
            "service": "groq_insight_service",
            "category": QueryCategory.AI_INSIGHT,
            "method": "analyze_root_cause",
            "cache_ttl": 0  # No cache for AI insights
        },
        IntentType.TREND_ANALYSIS: {
            "service": "groq_insight_service",
            "category": QueryCategory.AI_INSIGHT,
            "method": "analyze_trends",
            "cache_ttl": 0
        },
        IntentType.GENERAL_QUERY: {
            "service": "groq_insight_service",
            "category": QueryCategory.AI_INSIGHT,
            "method": "analyze",
            "cache_ttl": 0
        },
        
        # ========== CONTROL TOWER SERVICE ==========
        IntentType.CONTROL_TOWER: {
            "service": "control_tower_service",
            "category": QueryCategory.CONTROL_TOWER,
            "method": "get_control_tower_dashboard",
            "cache_ttl": 60
        },
        IntentType.CRITICAL_DNS: {
            "service": "control_tower_service",
            "category": QueryCategory.CONTROL_TOWER,
            "method": "get_critical_dns",
            "cache_ttl": 60
        },
        
        # ========== SELF SERVICE ==========
        IntentType.DEALER_SELF_SERVICE: {
            "service": "dealer_self_service",
            "category": QueryCategory.SELF_SERVICE,
            "method": "get_my_dashboard",
            "cache_ttl": 300
        },
        
        # ========== HELP ==========
        IntentType.HELP: {
            "service": "help_service",
            "category": QueryCategory.HELP,
            "method": "get_help",
            "cache_ttl": 86400
        },
    }
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        self._services = {}
        
        # Performance tracking
        self.cache_hits = 0
        self.cache_misses = 0
        self.total_routes = 0
        
        logger.info("✅ Query Router Service initialized with complete routing matrix")
        logger.info(f"   Total routes: {len(self.ROUTING_MATRIX)}")
    
    def route(
        self,
        intent: IntentType,
        entity: Optional[str] = None,
        entities: Dict = None,
        context: Dict = None,
        user_phone: str = None,
        user_role: str = None,
        question: str = None
    ) -> Dict[str, Any]:
        """
        Route intent to appropriate service with caching.
        """
        start_time = time.time()
        self.total_routes += 1
        
        # Get routing configuration
        route_config = self.ROUTING_MATRIX.get(intent)
        
        if not route_config:
            logger.warning(f"No route found for intent: {intent}")
            return self._fallback_route(intent, question)
        
        service_name = route_config["service"]
        method_name = route_config["method"]
        category = route_config["category"]
        cache_ttl = route_config.get("cache_ttl", 0)
        
        # Build cache key
        cache_key = self._build_cache_key(intent, entity, entities, user_role)
        
        # Check cache first (skip for AI insights)
        cache_hit = False
        cached_response = None
        
        if cache_ttl > 0 and self.cache and self.cache.enabled:
            cached_response = self.cache.get(cache_key)
            if cached_response:
                cache_hit = True
                self.cache_hits += 1
                logger.debug(f"Cache HIT for {intent.value}")
                
                # Return cached response with metadata
                return {
                    "response": cached_response,
                    "service": service_name,
                    "category": category.value,
                    "cache_hit": True,
                    "service_time_ms": 0
                }
            else:
                self.cache_misses += 1
        
        # Get or initialize service
        service = self._get_service(service_name)
        
        if not service:
            logger.error(f"Service not available: {service_name}")
            return self._fallback_route(intent, question)
        
        # Call service method
        try:
            method = getattr(service, method_name, None)
            if not method:
                logger.error(f"Method {method_name} not found in {service_name}")
                return self._fallback_route(intent, question)
            
            # Prepare arguments based on method signature
            result = self._call_method(
                method, entity, entities, context, user_phone, user_role, question
            )
            
            # Cache result if TTL > 0
            if cache_ttl > 0 and self.cache and self.cache.enabled and result:
                self.cache.set(cache_key, result, ttl=cache_ttl)
            
            service_time_ms = int((time.time() - start_time) * 1000)
            
            return {
                "response": result,
                "service": service_name,
                "category": category.value,
                "cache_hit": False,
                "service_time_ms": service_time_ms,
                "ai_time_ms": result.get("ai_time_ms", 0) if isinstance(result, dict) else 0
            }
            
        except Exception as e:
            logger.error(f"Service {service_name} method {method_name} error: {e}")
            return self._fallback_route(intent, question)
    
    def _build_cache_key(self, intent: IntentType, entity: str, entities: Dict, user_role: str) -> str:
        """Build cache key for request"""
        key_parts = [intent.value]
        
        if entity:
            key_parts.append(entity)
        
        if entities:
            for k, v in entities.items():
                if hasattr(v, 'value'):
                    key_parts.append(f"{k}:{v.value}")
        
        if user_role:
            key_parts.append(f"role:{user_role}")
        
        return ":".join(key_parts)
    
    def _get_service(self, service_name: str):
        """Lazy load service"""
        if service_name in self._services:
            return self._services[service_name]
        
        try:
            if service_name == "logistics_query_service":
                from app.services.logistics_query_service import LogisticsQueryService
                self._services[service_name] = LogisticsQueryService(self.db, self.cache)
            
            elif service_name == "analytics_service":
                from app.services.analytics_service import AnalyticsService
                self._services[service_name] = AnalyticsService(self.db, self.cache)
            
            elif service_name == "kpi_service":
                from app.services.kpi_service import KPIService
                self._services[service_name] = KPIService(self.db, self.cache)
            
            elif service_name == "recommendation_service":
                from app.services.recommendation_service import RecommendationService
                self._services[service_name] = RecommendationService(self.db, self.cache)
            
            elif service_name == "forecasting_service":
                from app.services.forecasting_service import ForecastingService
                self._services[service_name] = ForecastingService(self.db, self.cache)
            
            elif service_name == "groq_insight_service":
                from app.services.groq_insight_service import GroqInsightService
                self._services[service_name] = GroqInsightService(self.db, self.cache)
            
            elif service_name == "control_tower_service":
                from app.services.control_tower_service import ControlTowerService
                self._services[service_name] = ControlTowerService(self.db, self.cache)
            
            elif service_name == "dealer_self_service":
                from app.services.dealer_self_service import DealerSelfService
                self._services[service_name] = DealerSelfService(self.db, self.cache)
            
            elif service_name == "help_service":
                from app.services.help_service import HelpService
                self._services[service_name] = HelpService(self.db, self.cache)
            
            else:
                logger.error(f"Unknown service: {service_name}")
                return None
            
            logger.info(f"✅ Loaded service: {service_name}")
            return self._services[service_name]
            
        except Exception as e:
            logger.error(f"Failed to load service {service_name}: {e}")
            return None
    
    def _call_method(self, method, entity, entities, context, user_phone, user_role, question):
        """Call method with appropriate arguments"""
        import inspect
        
        sig = inspect.signature(method)
        params = list(sig.parameters.keys())
        
        kwargs = {}
        
        if 'entity' in params and entity:
            kwargs['entity'] = entity
        if 'entities' in params and entities:
            kwargs['entities'] = entities
        if 'context' in params and context:
            kwargs['context'] = context
        if 'user_phone' in params and user_phone:
            kwargs['user_phone'] = user_phone
        if 'user_role' in params and user_role:
            kwargs['user_role'] = user_role
        if 'question' in params and question:
            kwargs['question'] = question
        
        return method(**kwargs)
    
    def _fallback_route(self, intent: IntentType, question: str) -> Dict:
        """Fallback when routing fails"""
        return {
            "response": {
                "error": f"Unable to process {intent.value}. Please try a different query.",
                "suggestions": [
                    "DN <number> - Track a delivery note",
                    "Top dealers - View dealer rankings",
                    "Pending PODs - Check pending collections",
                    "Help - Show complete menu"
                ]
            },
            "service": "fallback",
            "category": "help",
            "cache_hit": False,
            "service_time_ms": 0
        }
    
    def get_stats(self) -> Dict:
        """Get router statistics"""
        return {
            "total_routes": self.total_routes,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "hit_rate": self.cache_hits / max(1, self.cache_hits + self.cache_misses) * 100,
            "active_services": list(self._services.keys())
        }
