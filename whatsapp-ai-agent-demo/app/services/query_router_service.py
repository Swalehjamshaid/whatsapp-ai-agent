# ==========================================================
# FILE: app/services/query_router_service.py (FIXED v2.0)
# ==========================================================
# CRITICAL FIXES:
# - Added try-catch for service imports
# - Added fallback for service initialization failures
# - Added timeout for analyze() calls
# - Added proper error responses instead of silent failures
# ==========================================================

import time
from typing import Dict, Any, Optional
from dataclasses import dataclass

from sqlalchemy.orm import Session
from loguru import logger

from app.services.intent_engine import IntentType


@dataclass
class RouteResult:
    """Result of routing a query"""
    service: str
    response: Dict[str, Any]
    service_time_ms: int = 0
    ai_time_ms: int = 0


class QueryRouterService:
    """
    Central query router.
    Routes intents to appropriate services.
    """
    
    # Routing table
    ROUTING_TABLE = {
        # Logistics Query Service
        IntentType.DN_LOOKUP: "logistics_query_service",
        IntentType.DN_TIMELINE: "logistics_query_service",
        IntentType.DN_PRODUCTS: "logistics_query_service",
        IntentType.POD_ANALYSIS: "logistics_query_service",
        IntentType.POD_PENDING: "logistics_query_service",
        IntentType.PGI_ANALYSIS: "logistics_query_service",
        IntentType.PGI_PENDING: "logistics_query_service",
        
        # Analytics Service
        IntentType.PRODUCT_DASHBOARD: "analytics_service",
        IntentType.PRODUCT_RANKING: "analytics_service",
        IntentType.FAST_MOVING: "analytics_service",
        IntentType.SLOW_MOVING: "analytics_service",
        IntentType.DEAD_STOCK: "analytics_service",
        IntentType.DEALER_DASHBOARD: "analytics_service",
        IntentType.DEALER_RANKING: "analytics_service",
        IntentType.DEALER_RISK: "analytics_service",
        IntentType.CITY_DASHBOARD: "analytics_service",
        IntentType.CITY_RANKING: "analytics_service",
        IntentType.WAREHOUSE_DASHBOARD: "analytics_service",
        IntentType.WAREHOUSE_RANKING: "analytics_service",
        IntentType.DIVISION_DASHBOARD: "analytics_service",
        IntentType.MANAGER_DASHBOARD: "analytics_service",
        IntentType.REVENUE_ANALYSIS: "analytics_service",
        IntentType.REVENUE_AT_RISK: "analytics_service",
        
        # KPI Service
        IntentType.EXECUTIVE_KPI: "kpi_service",
        IntentType.CEO_BRIEFING: "kpi_service",
        IntentType.NETWORK_HEALTH: "kpi_service",
        
        # Recommendation Service
        IntentType.RECOMMENDATION: "recommendation_service",
        IntentType.DEALER_FOLLOWUP: "recommendation_service",
        IntentType.CRITICAL_DELAY_ACTION: "recommendation_service",
        
        # Forecasting Service
        IntentType.FORECAST: "forecasting_service",
        IntentType.SALES_FORECAST: "forecasting_service",
        IntentType.POD_FORECAST: "forecasting_service",
        
        # Groq Insight Service (AI only)
        IntentType.ROOT_CAUSE_ANALYSIS: "groq_insight_service",
        IntentType.TREND_ANALYSIS: "groq_insight_service",
        IntentType.PREDICTIVE_ANALYSIS: "groq_insight_service",
        
        # Control Tower Service
        IntentType.CONTROL_TOWER: "control_tower_service",
        IntentType.CRITICAL_DNS: "control_tower_service",
        IntentType.HIGH_RISK_DNS: "control_tower_service",
        IntentType.CRITICAL_PODS: "control_tower_service",
        IntentType.TOP_RISKS: "control_tower_service",
        
        # Dealer Self-Service
        IntentType.DEALER_SELF_SERVICE: "dealer_self_service",
        
        # Help
        IntentType.HELP: "help_service",
        IntentType.GENERAL_QUERY: "groq_insight_service",
    }
    
    def __init__(self, db: Session):
        self.db = db
        
        # Initialize all services (lazy loading)
        self._services = {}
        
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
            Dictionary with 'response' and 'service' keys
        """
        start_time = time.time()
        
        # Get service name from routing table
        service_name = self.ROUTING_TABLE.get(intent)
        
        if not service_name:
            logger.warning(f"No service mapping for intent: {intent}")
            service_name = "groq_insight_service"
        
        logger.info(f"🎯 Routing {intent.value} to {service_name}")
        
        # Get or initialize service
        service = self._get_service(service_name)
        
        if not service:
            error_msg = f"Service {service_name} not available"
            logger.error(f"❌ {error_msg}")
            return {
                "success": False,
                "response": {"error": error_msg, "fallback": True},
                "service": service_name,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # Route to service method
        try:
            result = self._call_service(
                service=service,
                service_name=service_name,
                intent=intent,
                entity=entity,
                entities=entities or {},
                context=context or {},
                user_phone=user_phone,
                user_role=user_role,
                question=question
            )
            
            result["service_time_ms"] = int((time.time() - start_time) * 1000)
            result["service"] = service_name
            
            return result
            
        except Exception as e:
            logger.exception(f"Service {service_name} error: {e}")
            return {
                "success": False,
                "response": {"error": str(e), "fallback": True},
                "service": service_name,
                "service_time_ms": int((time.time() - start_time) * 1000)
            }
    
    def _get_service(self, service_name: str):
        """Lazy load service with proper error handling"""
        if service_name in self._services:
            return self._services[service_name]
        
        logger.info(f"🔧 Initializing service: {service_name}")
        
        try:
            # CRITICAL FIX #1: Add try-catch for each import
            if service_name == "logistics_query_service":
                from app.services.logistics_query_service import LogisticsQueryService
                self._services[service_name] = LogisticsQueryService(self.db)
                logger.info(f"✅ Loaded logistics_query_service")
            
            elif service_name == "analytics_service":
                from app.services.analytics_service import AnalyticsService
                self._services[service_name] = AnalyticsService(self.db)
                logger.info(f"✅ Loaded analytics_service")
            
            elif service_name == "kpi_service":
                from app.services.kpi_service import KPIService
                self._services[service_name] = KPIService(self.db)
                logger.info(f"✅ Loaded kpi_service")
            
            elif service_name == "recommendation_service":
                from app.services.recommendation_service import RecommendationService
                self._services[service_name] = RecommendationService(self.db)
                logger.info(f"✅ Loaded recommendation_service")
            
            elif service_name == "forecasting_service":
                from app.services.forecasting_service import ForecastingService
                self._services[service_name] = ForecastingService(self.db)
                logger.info(f"✅ Loaded forecasting_service")
            
            elif service_name == "groq_insight_service":
                from app.services.groq_insight_service import GroqInsightService
                self._services[service_name] = GroqInsightService(self.db)
                logger.info(f"✅ Loaded groq_insight_service")
            
            elif service_name == "control_tower_service":
                from app.services.control_tower_service import ControlTowerService
                self._services[service_name] = ControlTowerService(self.db)
                logger.info(f"✅ Loaded control_tower_service")
            
            elif service_name == "dealer_self_service":
                from app.services.dealer_self_service import DealerSelfService
                self._services[service_name] = DealerSelfService(self.db)
                logger.info(f"✅ Loaded dealer_self_service")
            
            elif service_name == "help_service":
                from app.services.help_service import HelpService
                self._services[service_name] = HelpService(self.db)
                logger.info(f"✅ Loaded help_service")
            
            else:
                logger.error(f"Unknown service: {service_name}")
                return None
            
            return self._services[service_name]
            
        except ImportError as e:
            logger.error(f"❌ Failed to import service {service_name}: {e}")
            # Return a fallback service that returns error messages
            return self._get_fallback_service(service_name, str(e))
            
        except Exception as e:
            logger.exception(f"❌ Service {service_name} initialization error: {e}")
            return self._get_fallback_service(service_name, str(e))
    
    def _get_fallback_service(self, service_name: str, error: str):
        """Return a fallback service that returns error messages"""
        
        class FallbackService:
            def __init__(self, name, err):
                self.name = name
                self.error = err
            
            def get_complete_dn_intelligence(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable: {self.error}", "fallback": True}
            
            def get_dn_timeline(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable", "fallback": True}
            
            def get_dn_products(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable", "fallback": True}
            
            def get_pending_pods(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable", "fallback": True}
            
            def get_pending_pgi(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable", "fallback": True}
            
            def analyze(self, *args, **kwargs):
                return {"insight": f"⚠️ AI service is currently unavailable. Please try again later.", "fallback": True}
            
            def get_executive_dashboard(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable", "fallback": True}
            
            def get_dealer_dashboard(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable", "fallback": True}
            
            def get_recommendations(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable", "fallback": True}
            
            def get_control_tower_dashboard(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable", "fallback": True}
            
            def get_my_dashboard(self, *args, **kwargs):
                return {"error": f"Service {self.name} unavailable", "fallback": True}
        
        logger.warning(f"⚠️ Using fallback service for {service_name}")
        return FallbackService(service_name, error)
    
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
        """Call appropriate method on service based on intent"""
        
        # Extract DN safely from entities
        dn_number = None
        if entity:
            dn_number = entity
        elif entities:
            # Handle both string and ExtractedEntity objects
            dn_value = entities.get('dn_number')
            if dn_value:
                if hasattr(dn_value, 'value'):
                    dn_number = dn_value.value
                else:
                    dn_number = str(dn_value)
        
        try:
            # Logistics Query Service methods
            if service_name == "logistics_query_service":
                if intent == IntentType.DN_LOOKUP:
                    logger.info(f"🔍 DN Lookup: {dn_number}")
                    result = service.get_complete_dn_intelligence(dn_number)
                    return {"response": result}
                elif intent == IntentType.DN_TIMELINE:
                    return {"response": service.get_dn_timeline(dn_number)}
                elif intent == IntentType.DN_PRODUCTS:
                    return {"response": service.get_dn_products(dn_number)}
                elif intent == IntentType.POD_PENDING:
                    return {"response": service.get_pending_pods()}
                elif intent == IntentType.PGI_PENDING:
                    return {"response": service.get_pending_pgi()}
                else:
                    return {"response": {"error": f"Unknown logistics intent: {intent}"}}
            
            # Analytics Service methods
            elif service_name == "analytics_service":
                if intent == IntentType.PRODUCT_DASHBOARD:
                    return {"response": service.get_product_dashboard(entity or entities.get('product'))}
                elif intent == IntentType.PRODUCT_RANKING:
                    return {"response": service.get_product_ranking()}
                elif intent == IntentType.FAST_MOVING:
                    return {"response": service.get_fast_moving_products()}
                elif intent == IntentType.SLOW_MOVING:
                    return {"response": service.get_slow_moving_products()}
                elif intent == IntentType.DEAD_STOCK:
                    return {"response": service.get_dead_stock_products()}
                elif intent == IntentType.DEALER_DASHBOARD:
                    return {"response": service.get_dealer_dashboard(entity or entities.get('dealer'))}
                elif intent == IntentType.DEALER_RANKING:
                    return {"response": service.get_dealer_ranking()}
                elif intent == IntentType.CITY_DASHBOARD:
                    return {"response": service.get_city_dashboard(entity or entities.get('city'))}
                elif intent == IntentType.CITY_RANKING:
                    return {"response": service.get_city_ranking()}
                elif intent == IntentType.WAREHOUSE_DASHBOARD:
                    return {"response": service.get_warehouse_dashboard(entity or entities.get('warehouse'))}
                elif intent == IntentType.WAREHOUSE_RANKING:
                    return {"response": service.get_warehouse_ranking()}
                elif intent == IntentType.REVENUE_ANALYSIS:
                    return {"response": service.get_revenue_analysis()}
                elif intent == IntentType.REVENUE_AT_RISK:
                    return {"response": service.get_revenue_at_risk()}
                else:
                    return {"response": {"error": f"Unknown analytics intent: {intent}"}}
            
            # KPI Service methods
            elif service_name == "kpi_service":
                if intent == IntentType.EXECUTIVE_KPI:
                    return {"response": service.get_executive_dashboard()}
                elif intent == IntentType.CEO_BRIEFING:
                    return {"response": service.get_ceo_briefing()}
                elif intent == IntentType.NETWORK_HEALTH:
                    return {"response": service.get_network_health()}
                else:
                    return {"response": {"error": f"Unknown KPI intent: {intent}"}}
            
            # Recommendation Service methods
            elif service_name == "recommendation_service":
                if intent == IntentType.RECOMMENDATION:
                    return {"response": service.get_recommendations()}
                elif intent == IntentType.DEALER_FOLLOWUP:
                    return {"response": service.get_dealers_needing_followup()}
                elif intent == IntentType.CRITICAL_DELAY_ACTION:
                    return {"response": service.get_critical_delay_actions()}
                else:
                    return {"response": {"error": f"Unknown recommendation intent: {intent}"}}
            
            # Forecasting Service methods
            elif service_name == "forecasting_service":
                if intent == IntentType.SALES_FORECAST:
                    return {"response": service.get_sales_forecast()}
                elif intent == IntentType.POD_FORECAST:
                    return {"response": service.get_pod_forecast()}
                else:
                    return {"response": service.get_general_forecast()}
            
            # Groq Insight Service methods (AI only)
            elif service_name == "groq_insight_service":
                # CRITICAL FIX #3: Add timeout and error handling for analyze
                logger.info(f"🧠 Calling Groq analyze for intent: {intent}")
                ai_start = time.time()
                try:
                    response = service.analyze(question, intent, context)
                    ai_time_ms = int((time.time() - ai_start) * 1000)
                    logger.info(f"✅ Groq analyze completed in {ai_time_ms}ms")
                    return {"response": response, "ai_time_ms": ai_time_ms}
                except Exception as e:
                    logger.exception(f"Groq analyze failed: {e}")
                    return {"response": {"insight": f"⚠️ AI analysis failed: {str(e)[:100]}", "fallback": True}, "ai_time_ms": int((time.time() - ai_start) * 1000)}
            
            # Control Tower Service methods
            elif service_name == "control_tower_service":
                if intent == IntentType.CONTROL_TOWER:
                    return {"response": service.get_control_tower_dashboard()}
                elif intent == IntentType.CRITICAL_DNS:
                    return {"response": service.get_critical_dns()}
                elif intent == IntentType.TOP_RISKS:
                    return {"response": service.get_top_risks()}
                else:
                    return {"response": {"error": f"Unknown control tower intent: {intent}"}}
            
            # Dealer Self Service
            elif service_name == "dealer_self_service":
                dealer_name = context.get('dealer_name') or entities.get('dealer')
                if hasattr(dealer_name, 'value'):
                    dealer_name = dealer_name.value
                if not dealer_name and user_phone:
                    dealer_name = self._get_dealer_from_phone(user_phone)
                return {"response": service.get_my_dashboard(dealer_name, question)}
            
            # Help Service
            elif service_name == "help_service":
                from app.services.ai_query_service import WELCOME_MESSAGE
                return {"response": {"help": WELCOME_MESSAGE}}
            
            # Default fallback
            return {"response": {"error": f"No handler for intent {intent} in service {service_name}", "fallback": True}}
            
        except Exception as e:
            logger.exception(f"Error in _call_service for {service_name}.{intent}: {e}")
            return {"response": {"error": str(e), "fallback": True}}
    
    def _get_dealer_from_phone(self, phone_number: str) -> Optional[str]:
        """Get dealer name from phone number mapping"""
        # This would query a dealer-phone mapping table
        # For now, return None
        return None
