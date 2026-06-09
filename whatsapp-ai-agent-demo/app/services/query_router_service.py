# ==========================================================
# FILE: app/services/query_router_service.py (ENTERPRISE v4.0)
# ==========================================================
# CENTRAL QUERY ROUTER
# - Routes intents to appropriate services
# - Dynamic service loading with importlib
# - Service health validation and cache expiry
# - Redis caching for frequent queries
# - Role-based access control
# - Rate limiting per user
# - Intent metrics and query logging
# - User-friendly error messages
# - Handler map for clean routing
# ==========================================================

import time
import importlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from functools import wraps

from sqlalchemy.orm import Session
from loguru import logger

from app.services.intent_engine import IntentType


# ==========================================================
# DATA CLASSES
# ==========================================================

@dataclass
class RouteResult:
    """Result of routing a query"""
    service: str
    response: Dict[str, Any]
    service_time_ms: int = 0
    ai_time_ms: int = 0
    success: bool = True
    cached: bool = False


@dataclass
class CachedService:
    """Wrapper for cached service with expiry and health tracking"""
    service: Any
    loaded_at: datetime = field(default_factory=datetime.utcnow)
    ttl_minutes: int = 15
    
    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() - self.loaded_at > timedelta(minutes=self.ttl_minutes)
    
    def is_healthy(self) -> bool:
        if hasattr(self.service, "health_check"):
            try:
                return self.service.health_check()
            except Exception:
                return False
        return True


# ==========================================================
# RATE LIMITER
# ==========================================================

class RateLimiter:
    """Simple in-memory rate limiter per user"""
    
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = timedelta(seconds=window_seconds)
        self.requests: Dict[str, List[datetime]] = defaultdict(list)
    
    def is_allowed(self, user_id: str) -> bool:
        if not user_id:
            return True
        
        now = datetime.utcnow()
        cutoff = now - self.window
        
        # Clean old requests
        self.requests[user_id] = [ts for ts in self.requests[user_id] if ts > cutoff]
        
        # Check limit
        if len(self.requests[user_id]) >= self.max_requests:
            return False
        
        # Add current request
        self.requests[user_id].append(now)
        return True
    
    def get_remaining(self, user_id: str) -> int:
        if not user_id:
            return self.max_requests
        
        now = datetime.utcnow()
        cutoff = now - self.window
        active = [ts for ts in self.requests.get(user_id, []) if ts > cutoff]
        return max(0, self.max_requests - len(active))


# ==========================================================
# QUERY ROUTER SERVICE
# ==========================================================

class QueryRouterService:
    """
    Central Query Router
    
    Routes intents to appropriate services based on routing table.
    Supports dynamic service loading, health checks, caching, RBAC, and rate limiting.
    """
    
    # ==========================================================
    # SERVICE REGISTRY (Dynamic Loading)
    # ==========================================================
    
    SERVICE_REGISTRY: Dict[str, Dict[str, Any]] = {
        "logistics_query_service": {
            "module": "app.services.logistics_query_service",
            "class": "LogisticsQueryService",
            "timeout": 30,
            "retries": 2,
            "ttl_minutes": 15
        },
        "analytics_service": {
            "module": "app.services.analytics_service",
            "class": "AnalyticsService",
            "timeout": 45,
            "retries": 1,
            "ttl_minutes": 15
        },
        "kpi_service": {
            "module": "app.services.kpi_service",
            "class": "KPIService",
            "timeout": 30,
            "retries": 1,
            "ttl_minutes": 15
        },
        "recommendation_service": {
            "module": "app.services.recommendation_service",
            "class": "RecommendationService",
            "timeout": 30,
            "retries": 1,
            "ttl_minutes": 15
        },
        "forecasting_service": {
            "module": "app.services.forecasting_service",
            "class": "ForecastingService",
            "timeout": 30,
            "retries": 1,
            "ttl_minutes": 15
        },
        "groq_insight_service": {
            "module": "app.services.groq_insight_service",
            "class": "GroqInsightService",
            "timeout": 60,
            "retries": 2,
            "ttl_minutes": 5
        },
        "control_tower_service": {
            "module": "app.services.control_tower_service",
            "class": "ControlTowerService",
            "timeout": 30,
            "retries": 1,
            "ttl_minutes": 15
        },
        "dealer_self_service": {
            "module": "app.services.dealer_self_service",
            "class": "DealerSelfService",
            "timeout": 30,
            "retries": 1,
            "ttl_minutes": 15
        },
        "help_service": {
            "module": "app.services.help_service",
            "class": "HelpService",
            "timeout": 5,
            "retries": 0,
            "ttl_minutes": 60
        },
    }
    
    # ==========================================================
    # INTENT HANDLER MAP (Clean routing)
    # ==========================================================
    
    INTENT_HANDLER_MAP: Dict[IntentType, Tuple[str, str]] = {
        # ========== Logistics Query Service ==========
        IntentType.DN_LOOKUP: ("logistics_query_service", "get_complete_dn_intelligence"),
        IntentType.DN_TIMELINE: ("logistics_query_service", "get_dn_timeline"),
        IntentType.DN_PRODUCTS: ("logistics_query_service", "get_dn_products"),
        IntentType.DN_AGING: ("logistics_query_service", "get_dn_aging"),
        IntentType.POD_ANALYSIS: ("logistics_query_service", "get_pod_analysis"),
        IntentType.POD_PENDING: ("logistics_query_service", "get_pending_pods"),
        IntentType.PGI_ANALYSIS: ("logistics_query_service", "get_pgi_analysis"),
        IntentType.PGI_PENDING: ("logistics_query_service", "get_pending_pgi"),
        
        # ========== Analytics Service ==========
        IntentType.PRODUCT_DASHBOARD: ("analytics_service", "get_product_dashboard"),
        IntentType.PRODUCT_RANKING: ("analytics_service", "get_product_ranking"),
        IntentType.FAST_MOVING: ("analytics_service", "get_fast_moving_products"),
        IntentType.SLOW_MOVING: ("analytics_service", "get_slow_moving_products"),
        IntentType.DEAD_STOCK: ("analytics_service", "get_dead_stock_products"),
        IntentType.DEALER_DASHBOARD: ("analytics_service", "get_dealer_dashboard"),
        IntentType.DEALER_RANKING: ("analytics_service", "get_dealer_ranking"),
        IntentType.DEALER_RISK: ("analytics_service", "get_dealer_risk"),
        IntentType.DEALER_GROWTH: ("analytics_service", "get_dealer_growth"),
        IntentType.CITY_DASHBOARD: ("analytics_service", "get_city_dashboard"),
        IntentType.CITY_RANKING: ("analytics_service", "get_city_ranking"),
        IntentType.CITY_ANALYSIS: ("analytics_service", "get_city_analysis"),
        IntentType.WAREHOUSE_DASHBOARD: ("analytics_service", "get_warehouse_dashboard"),
        IntentType.WAREHOUSE_RANKING: ("analytics_service", "get_warehouse_ranking"),
        IntentType.WAREHOUSE_DELAY: ("analytics_service", "get_warehouse_delay"),
        IntentType.DIVISION_DASHBOARD: ("analytics_service", "get_division_dashboard"),
        IntentType.DIVISION_RANKING: ("analytics_service", "get_division_ranking"),
        IntentType.MANAGER_DASHBOARD: ("analytics_service", "get_manager_dashboard"),
        IntentType.MANAGER_RANKING: ("analytics_service", "get_manager_ranking"),
        IntentType.REVENUE_ANALYSIS: ("analytics_service", "get_revenue_analysis"),
        IntentType.REVENUE_AT_RISK: ("analytics_service", "get_revenue_at_risk"),
        
        # ========== KPI Service ==========
        IntentType.EXECUTIVE_KPI: ("kpi_service", "get_executive_dashboard"),
        IntentType.CEO_BRIEFING: ("kpi_service", "get_ceo_briefing"),
        IntentType.NETWORK_HEALTH: ("kpi_service", "get_network_health"),
        IntentType.TOP_RISKS: ("kpi_service", "get_top_risks"),
        
        # ========== Recommendation Service ==========
        IntentType.RECOMMENDATION: ("recommendation_service", "get_recommendations"),
        IntentType.DEALER_FOLLOWUP: ("recommendation_service", "get_dealers_needing_followup"),
        IntentType.CRITICAL_DELAY_ACTION: ("recommendation_service", "get_critical_delay_actions"),
        
        # ========== Forecasting Service ==========
        IntentType.FORECAST: ("forecasting_service", "get_general_forecast"),
        IntentType.SALES_FORECAST: ("forecasting_service", "get_sales_forecast"),
        IntentType.POD_FORECAST: ("forecasting_service", "get_pod_forecast"),
        
        # ========== Control Tower Service ==========
        IntentType.CONTROL_TOWER: ("control_tower_service", "get_control_tower_dashboard"),
        IntentType.CRITICAL_DNS: ("control_tower_service", "get_critical_dns"),
        IntentType.HIGH_RISK_DNS: ("control_tower_service", "get_high_risk_dns"),
        IntentType.CRITICAL_PODS: ("control_tower_service", "get_critical_pods"),
        
        # ========== Dealer Self-Service ==========
        IntentType.DEALER_SELF_SERVICE: ("dealer_self_service", "get_my_dashboard"),
        
        # ========== Help and Greeting ==========
        IntentType.HELP: ("help_service", "get_help"),
        IntentType.GREETING: ("help_service", "get_greeting"),
    }
    
    # ==========================================================
    # ROLE-BASED ACCESS CONTROL
    # ==========================================================
    
    ROLE_PERMISSIONS: Dict[str, List[Any]] = {
        "CEO": ["*"],  # All access
        "Director": ["*"],
        "VP": ["*"],
        "Manager": [
            IntentType.EXECUTIVE_KPI,
            IntentType.DEALER_DASHBOARD,
            IntentType.DEALER_RANKING,
            IntentType.PRODUCT_DASHBOARD,
            IntentType.PRODUCT_RANKING,
            IntentType.DN_LOOKUP,
            IntentType.POD_PENDING,
            IntentType.PGI_PENDING,
            IntentType.CONTROL_TOWER,
            IntentType.REVENUE_ANALYSIS,
        ],
        "Dealer": [
            IntentType.DEALER_SELF_SERVICE,
            IntentType.DN_LOOKUP,
            IntentType.POD_PENDING,
            IntentType.HELP,
            IntentType.GREETING,
        ],
        "Warehouse": [
            IntentType.WAREHOUSE_DASHBOARD,
            IntentType.WAREHOUSE_RANKING,
            IntentType.PGI_PENDING,
            IntentType.DN_LOOKUP,
            IntentType.HELP,
            IntentType.GREETING,
        ],
        "Sales": [
            IntentType.DEALER_DASHBOARD,
            IntentType.DEALER_RANKING,
            IntentType.PRODUCT_RANKING,
            IntentType.REVENUE_ANALYSIS,
            IntentType.DN_LOOKUP,
            IntentType.HELP,
            IntentType.GREETING,
        ],
        "default": [
            IntentType.HELP,
            IntentType.GREETING,
            IntentType.GENERAL_QUERY,
        ],
    }
    
    # ==========================================================
    # USER-FRIENDLY ERROR MESSAGES
    # ==========================================================
    
    ERROR_MESSAGES = {
        "database": "⚠️ The system is temporarily unavailable. Our team has been notified. Please try again in a few minutes.",
        "timeout": "⏰ The request is taking longer than expected. Please try again in a moment.",
        "permission": "🔒 You don't have permission to access this information. Please contact your administrator.",
        "rate_limit": "⏰ Too many requests. Please wait a moment before trying again.",
        "service_unavailable": "⚠️ A service is temporarily unavailable. Please try again later.",
        "invalid_input": "❌ I couldn't understand that request. Please try rephrasing or ask for 'Help'.",
        "default": "⚠️ Unable to complete your request. Please try again later or contact support."
    }
    
    # ==========================================================
    # WELCOME MESSAGE (Inline to fix import issue)
    # ==========================================================
    
    WELCOME_MESSAGE = """🤖 **Logistics AI Assistant**

I can help you with:

📦 **Track Shipments**
• "Check DN 6243612278"
• "Status of delivery note 12345"

📋 **Pending Items**
• "Pending POD"
• "Pending PGI"
• "Pending deliveries"

📊 **Analytics & Reports**
• "Executive dashboard"
• "Top 10 dealers"
• "Best selling products"
• "Slow moving products"
• "Revenue analysis"

⚡ **Control Tower**
• "Critical deliveries"
• "High risk shipments"
• "Control tower dashboard"

🏪 **Dealer Portal**
• "My dashboard"
• "My pending deliveries"

💡 **Just ask naturally!** I understand context and can answer follow-up questions.

*Type anything to get started or ask for specific help.*"""
    
    # ==========================================================
    # INITIALIZATION
    # ==========================================================
    
    def __init__(self, db: Session, redis_client=None):
        self.db = db
        self.redis_client = redis_client
        self._services: Dict[str, Optional[CachedService]] = {}
        self.rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
        
        # Preload frequently used services
        self._preload_services([
            "logistics_query_service",
            "groq_insight_service",
            "help_service"
        ])
        
        logger.info("✅ Query Router Service initialized (Enterprise v4.0)")
        logger.info(f"   - {len(self.SERVICE_REGISTRY)} services registered")
        logger.info(f"   - {len(self.INTENT_HANDLER_MAP)} intent handlers mapped")
        logger.info(f"   - Redis cache: {'enabled' if redis_client else 'disabled'}")
    
    # ==========================================================
    # SERVICE LOADING (Dynamic with importlib)
    # ==========================================================
    
    def _preload_services(self, service_names: List[str]) -> None:
        """Preload frequently used services for faster response"""
        for name in service_names:
            try:
                service = self._get_service(name)
                if service:
                    logger.info(f"✅ Preloaded: {name}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to preload {name}: {e}")
    
    def _get_service(self, service_name: str):
        """Lazy load service with health check and expiry using dynamic import"""
        
        # Check cached service
        if service_name in self._services:
            cached = self._services[service_name]
            if cached and not cached.is_expired and cached.is_healthy():
                return cached.service
            else:
                logger.info(f"🔄 Service {service_name} expired/unhealthy, reloading")
                del self._services[service_name]
        
        # Get registry entry
        registry = self.SERVICE_REGISTRY.get(service_name)
        if not registry:
            logger.error(f"❌ Unknown service: {service_name}")
            return self._get_fallback_service(service_name, "Service not registered")
        
        logger.info(f"🔧 Initializing service: {service_name}")
        
        try:
            # Dynamic import using importlib
            module = importlib.import_module(registry["module"])
            service_class = getattr(module, registry["class"])
            
            # Initialize service
            service_instance = service_class(self.db)
            
            # Cache with TTL
            self._services[service_name] = CachedService(
                service=service_instance,
                ttl_minutes=registry.get("ttl_minutes", 15)
            )
            
            logger.info(f"✅ Loaded service: {service_name}")
            return service_instance
            
        except ImportError as e:
            logger.error(f"❌ Failed to import {service_name}: {e}")
            return self._get_fallback_service(service_name, str(e))
            
        except Exception as e:
            logger.exception(f"❌ Service {service_name} initialization error: {e}")
            return self._get_fallback_service(service_name, str(e))
    
    def _get_fallback_service(self, service_name: str, error: str):
        """Return a fallback service that returns user-friendly error messages"""
        
        class FallbackService:
            def __init__(self, name, err):
                self.name = name
                self.error = err
            
            def __getattr__(self, name):
                """Catch any method call and return error"""
                def error_method(*args, **kwargs):
                    return {
                        "error": f"Service '{self.name}' is currently unavailable.",
                        "message": QueryRouterService.ERROR_MESSAGES["service_unavailable"],
                        "fallback": True,
                        "details": self.error if logger.level <= "DEBUG" else None
                    }
                return error_method
        
        logger.warning(f"⚠️ Using fallback service for {service_name}")
        return FallbackService(service_name, error)
    
    # ==========================================================
    # CACHE HELPERS
    # ==========================================================
    
    def _get_cache_key(self, intent: IntentType, entity: Optional[str], entities: Dict, user_role: str) -> str:
        """Generate cache key for request"""
        key_parts = [intent.value, user_role or "default"]
        if entity:
            key_parts.append(str(entity))
        if entities:
            # Sort for consistent keys
            for k in sorted(entities.keys()):
                val = entities[k]
                if hasattr(val, 'value'):
                    val = val.value
                key_parts.append(f"{k}:{val}")
        return f"qr:{':'.join(key_parts)}"
    
    def _get_cached_response(self, cache_key: str) -> Optional[Dict]:
        """Get response from Redis cache"""
        if not self.redis_client:
            return None
        
        try:
            import json
            cached = self.redis_client.get(cache_key)
            if cached:
                logger.debug(f"🎯 Cache hit: {cache_key}")
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Cache read error: {e}")
        
        return None
    
    def _set_cached_response(self, cache_key: str, response: Dict, ttl_seconds: int = 300) -> None:
        """Store response in Redis cache"""
        if not self.redis_client or not response or 'error' in response:
            return
        
        try:
            import json
            self.redis_client.setex(cache_key, ttl_seconds, json.dumps(response))
            logger.debug(f"💾 Cached: {cache_key} (TTL: {ttl_seconds}s)")
        except Exception as e:
            logger.warning(f"Cache write error: {e}")
    
    # ==========================================================
    # PERMISSION CHECK
    # ==========================================================
    
    def _check_permission(self, intent: IntentType, user_role: Optional[str]) -> Tuple[bool, Optional[str]]:
        """Check if user role has permission for intent"""
        
        if not user_role:
            user_role = "default"
        
        allowed = self.ROLE_PERMISSIONS.get(user_role, self.ROLE_PERMISSIONS["default"])
        
        # Check wildcard or direct match
        if "*" in allowed or intent in allowed:
            return True, None
        
        return False, self.ERROR_MESSAGES["permission"]
    
    # ==========================================================
    # USER-FRIENDLY ERROR
    # ==========================================================
    
    def _get_user_friendly_error(self, error: Exception, intent: IntentType) -> str:
        """Convert technical error to user-friendly message"""
        error_str = str(error).lower()
        
        if "timeout" in error_str:
            return self.ERROR_MESSAGES["timeout"]
        elif "permission" in error_str or "access" in error_str:
            return self.ERROR_MESSAGES["permission"]
        elif "database" in error_str or "sql" in error_str or "connection" in error_str:
            return self.ERROR_MESSAGES["database"]
        elif "service" in error_str and "unavailable" in error_str:
            return self.ERROR_MESSAGES["service_unavailable"]
        else:
            return self.ERROR_MESSAGES["default"]
    
    # ==========================================================
    # ARGUMENT BUILDER
    # ==========================================================
    
    def _build_method_args(self, method, entity: Optional[str], entities: Dict, context: Dict) -> Dict:
        """Build arguments dynamically based on method signature"""
        import inspect
        
        args = {}
        sig = inspect.signature(method)
        
        # Extract DN safely
        dn_number = None
        if entity:
            dn_number = entity
        elif entities:
            dn_value = entities.get('dn_number')
            if dn_value:
                if hasattr(dn_value, 'value'):
                    dn_number = dn_value.value
                else:
                    dn_number = str(dn_value)
        
        # Map common parameter names
        param_mapping = {
            'dn_number': dn_number,
            'product': entity or entities.get('product'),
            'dealer': entity or entities.get('dealer'),
            'city': entity or entities.get('city'),
            'warehouse': entity or entities.get('warehouse'),
            'division': entity or entities.get('division'),
            'manager': entity or entities.get('manager'),
        }
        
        for param_name in sig.parameters:
            if param_name in param_mapping and param_mapping[param_name] is not None:
                args[param_name] = param_mapping[param_name]
            elif param_name == 'context' and context:
                args[param_name] = context
        
        return args
    
    # ==========================================================
    # QUERY LOGGING
    # ==========================================================
    
    def _log_query(
        self,
        phone_number: Optional[str],
        user_role: Optional[str],
        intent: IntentType,
        question: Optional[str],
        response_time_ms: int,
        ai_time_ms: int,
        success: bool,
        error_message: Optional[str] = None
    ) -> None:
        """Log query to database for analytics"""
        try:
            # Import here to avoid circular imports
            from app.models.query_log import QueryLog
            
            query_log = QueryLog(
                phone_number=phone_number,
                user_role=user_role,
                intent=intent.value,
                question=question[:500] if question else None,
                response_time_ms=response_time_ms,
                ai_time_ms=ai_time_ms,
                success=success,
                error_message=error_message[:500] if error_message else None
            )
            
            self.db.add(query_log)
            self.db.commit()
            
            # Also log to metrics
            logger.info(f"📊 Query logged: {intent.value} | {user_role} | {response_time_ms}ms | success={success}")
            
        except Exception as e:
            logger.warning(f"Failed to log query: {e}")
            self.db.rollback()
    
    # ==========================================================
    # MAIN ROUTE METHOD
    # ==========================================================
    
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
        Route intent to appropriate service.
        
        Returns:
            Dictionary with 'response', 'service', 'success' keys
        """
        start_time = time.time()
        entities = entities or {}
        context = context or {}
        
        # ========== Rate Limiting ==========
        if user_phone and not self.rate_limiter.is_allowed(user_phone):
            remaining = self.rate_limiter.get_remaining(user_phone)
            logger.warning(f"🚫 Rate limit exceeded for {user_phone}")
            
            return {
                "success": False,
                "response": {
                    "message": self.ERROR_MESSAGES["rate_limit"],
                    "remaining": remaining,
                    "reset_seconds": 60
                },
                "service": "rate_limiter",
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ========== Permission Check ==========
        has_permission, permission_error = self._check_permission(intent, user_role)
        if not has_permission:
            logger.warning(f"🔒 Access denied: {user_role} -> {intent.value}")
            
            self._log_query(
                phone_number=user_phone,
                user_role=user_role,
                intent=intent,
                question=question,
                response_time_ms=int((time.time() - start_time) * 1000),
                ai_time_ms=0,
                success=False,
                error_message=permission_error
            )
            
            return {
                "success": False,
                "response": {"message": permission_error},
                "service": "access_denied",
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ========== Check Cache (for non-AI intents) ==========
        cache_ttl = {
            IntentType.DN_LOOKUP: 300,      # 5 minutes
            IntentType.POD_PENDING: 120,    # 2 minutes
            IntentType.PGI_PENDING: 120,    # 2 minutes
            IntentType.DEALER_RANKING: 600, # 10 minutes
            IntentType.PRODUCT_RANKING: 600,
        }
        
        if intent in cache_ttl:
            cache_key = self._get_cache_key(intent, entity, entities, user_role or "")
            cached_response = self._get_cached_response(cache_key)
            if cached_response:
                logger.info(f"🎯 Cache hit for {intent.value}")
                return {
                    "success": True,
                    "response": cached_response,
                    "service": "cache",
                    "service_time_ms": 0,
                    "cached": True
                }
        
        # ========== Get Service ==========
        service_name = self.INTENT_HANDLER_MAP.get(intent, (None, None))[0]
        
        if not service_name:
            # Handle general query with AI
            if intent == IntentType.GENERAL_QUERY:
                service_name = "groq_insight_service"
            else:
                logger.warning(f"No service mapping for intent: {intent}")
                service_name = "groq_insight_service"
        
        logger.info(f"🎯 Routing {intent.value} to {service_name}")
        
        service = self._get_service(service_name)
        
        if not service:
            error_msg = f"Service {service_name} not available"
            logger.error(f"❌ {error_msg}")
            
            self._log_query(
                phone_number=user_phone,
                user_role=user_role,
                intent=intent,
                question=question,
                response_time_ms=int((time.time() - start_time) * 1000),
                ai_time_ms=0,
                success=False,
                error_message=error_msg
            )
            
            return {
                "success": False,
                "response": {"message": self.ERROR_MESSAGES["service_unavailable"]},
                "service": service_name,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ========== Call Service ==========
        try:
            result = self._call_service(
                service=service,
                service_name=service_name,
                intent=intent,
                entity=entity,
                entities=entities,
                context=context,
                user_phone=user_phone,
                user_role=user_role,
                question=question
            )
            
            service_time_ms = int((time.time() - start_time) * 1000)
            result["service_time_ms"] = service_time_ms
            result["service"] = service_name
            result["success"] = True
            
            # Cache response if applicable
            if intent in cache_ttl and result.get("response") and "error" not in result["response"]:
                self._set_cached_response(cache_key, result["response"], cache_ttl[intent])
            
            # Log successful query
            self._log_query(
                phone_number=user_phone,
                user_role=user_role,
                intent=intent,
                question=question,
                response_time_ms=service_time_ms,
                ai_time_ms=result.get("ai_time_ms", 0),
                success=True
            )
            
            return result
            
        except Exception as e:
            logger.exception(f"Service {service_name} error: {e}")
            
            user_message = self._get_user_friendly_error(e, intent)
            service_time_ms = int((time.time() - start_time) * 1000)
            
            self._log_query(
                phone_number=user_phone,
                user_role=user_role,
                intent=intent,
                question=question,
                response_time_ms=service_time_ms,
                ai_time_ms=0,
                success=False,
                error_message=str(e)[:500]
            )
            
            return {
                "success": False,
                "response": {"message": user_message, "fallback": True},
                "service": service_name,
                "service_time_ms": service_time_ms
            }
    
    # ==========================================================
    # SERVICE CALLER (Clean handler map based)
    # ==========================================================
    
    def _call_service(
        self,
        service,
        service_name: str,
        intent: IntentType,
        entity: Optional[str],
        entities: Dict,
        context: Dict,
        user_phone: str,
        user_role: str,
        question: str
    ) -> Dict[str, Any]:
        """Call appropriate method on service based on intent handler map"""
        
        # ========== Special case: AI Service ==========
        if service_name == "groq_insight_service":
            logger.info(f"🧠 Calling Groq analyze for intent: {intent}")
            ai_start = time.time()
            
            try:
                # Handle different AI service method signatures
                if hasattr(service, 'analyze'):
                    response = service.analyze(question, intent, context)
                elif hasattr(service, 'get_insight'):
                    response = service.get_insight(question, intent)
                else:
                    response = service.process_query(question, context)
                
                ai_time_ms = int((time.time() - ai_start) * 1000)
                logger.info(f"✅ Groq analyze completed in {ai_time_ms}ms")
                return {"response": response, "ai_time_ms": ai_time_ms}
                
            except Exception as e:
                logger.exception(f"Groq analyze failed: {e}")
                ai_time_ms = int((time.time() - ai_start) * 1000)
                return {
                    "response": {
                        "message": self.ERROR_MESSAGES["service_unavailable"],
                        "fallback": True
                    },
                    "ai_time_ms": ai_time_ms
                }
        
        # ========== Special case: Help Service ==========
        if service_name == "help_service":
            if intent == IntentType.HELP or intent == IntentType.GREETING:
                return {"response": {"message": self.WELCOME_MESSAGE}}
            return {"response": {"message": self.WELCOME_MESSAGE}}
        
        # ========== Special case: Dealer Self Service ==========
        if service_name == "dealer_self_service":
            dealer_name = context.get('dealer_name') or entities.get('dealer')
            if hasattr(dealer_name, 'value'):
                dealer_name = dealer_name.value
            if not dealer_name and user_phone:
                dealer_name = self._get_dealer_from_phone(user_phone)
            return {"response": service.get_my_dashboard(dealer_name, question)}
        
        # ========== Standard routing via handler map ==========
        handler = self.INTENT_HANDLER_MAP.get(intent)
        
        if not handler:
            logger.error(f"No handler found for intent: {intent}")
            return {"response": {"message": self.ERROR_MESSAGES["invalid_input"]}}
        
        expected_service, method_name = handler
        
        if expected_service != service_name:
            logger.error(f"Intent {intent} mapped to {expected_service} but got {service_name}")
            return {"response": {"message": self.ERROR_MESSAGES["default"]}}
        
        # Get method and call
        method = getattr(service, method_name, None)
        
        if not method:
            logger.error(f"Method {method_name} not found on {service_name}")
            return {"response": {"message": self.ERROR_MESSAGES["default"]}}
        
        # Build arguments dynamically
        args = self._build_method_args(method, entity, entities, context)
        
        # Call method
        try:
            response = method(**args)
            return {"response": response}
        except TypeError as e:
            # Fallback: try without args
            logger.warning(f"Method signature mismatch for {method_name}: {e}")
            response = method()
            return {"response": response}
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _get_dealer_from_phone(self, phone_number: str) -> Optional[str]:
        """Get dealer name from phone number mapping"""
        try:
            from app.models.dealer import Dealer
            dealer = self.db.query(Dealer).filter(Dealer.phone == phone_number).first()
            if dealer:
                return dealer.name
        except Exception as e:
            logger.warning(f"Failed to get dealer from phone: {e}")
        return None
    
    def get_service_status(self) -> Dict[str, Dict[str, Any]]:
        """Get detailed status of all services"""
        status = {}
        
        for name, cached in self._services.items():
            if cached:
                status[name] = {
                    "loaded": True,
                    "healthy": cached.is_healthy(),
                    "expired": cached.is_expired,
                    "loaded_at": cached.loaded_at.isoformat(),
                    "ttl_minutes": cached.ttl_minutes
                }
            else:
                status[name] = {
                    "loaded": False,
                    "healthy": False,
                    "expired": False
                }
        
        return status
    
    def clear_cache(self, pattern: str = None) -> int:
        """Clear Redis cache"""
        if not self.redis_client:
            return 0
        
        try:
            if pattern:
                keys = self.redis_client.keys(f"qr:{pattern}*")
            else:
                keys = self.redis_client.keys("qr:*")
            
            if keys:
                self.redis_client.delete(*keys)
                logger.info(f"🗑️ Cleared {len(keys)} cache keys")
                return len(keys)
        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
        
        return 0
    
    def get_rate_limit_status(self, user_phone: str) -> Dict[str, Any]:
        """Get rate limit status for a user"""
        return {
            "remaining": self.rate_limiter.get_remaining(user_phone),
            "max_requests": self.rate_limiter.max_requests,
            "window_seconds": int(self.rate_limiter.window.total_seconds())
        }
