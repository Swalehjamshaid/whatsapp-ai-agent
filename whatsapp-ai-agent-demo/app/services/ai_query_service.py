# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v22.0)
# ==========================================================
# MASTER ORCHESTRATOR - NO BUSINESS LOGIC
# ==========================================================

import time
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from loguru import logger

from app.config import config

# ==========================================================
# SERVICE IMPORTS (All new modular services)
# ==========================================================

from app.services.intent_engine import IntentEngine, IntentType
from app.services.entity_extractor import EntityExtractor, EntityType, ExtractedEntity
from app.services.context_service import ContextService
from app.services.query_router_service import QueryRouterService
from app.services.report_generator_service import ReportGeneratorService

# ==========================================================
# CACHE INTEGRATION (Phase 12)
# ==========================================================

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available - caching disabled")


# ==========================================================
# PERFORMANCE METRICS (Phase 13)
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
    total_time_ms: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "intent_time_ms": self.intent_time_ms,
            "entity_time_ms": self.entity_time_ms,
            "context_time_ms": self.context_time_ms,
            "route_time_ms": self.route_time_ms,
            "service_time_ms": self.service_time_ms,
            "ai_time_ms": self.ai_time_ms,
            "total_time_ms": self.total_time_ms
        }


@dataclass
class QueryAnalytics:
    """Track query analytics for monitoring (Phase 13)"""
    most_asked_dns: Dict[str, int] = field(default_factory=dict)
    most_asked_dealers: Dict[str, int] = field(default_factory=dict)
    most_asked_products: Dict[str, int] = field(default_factory=dict)
    failed_queries: List[Dict] = field(default_factory=list)
    intent_counts: Dict[str, int] = field(default_factory=dict)
    
    def record_dn_query(self, dn: str):
        self.most_asked_dns[dn] = self.most_asked_dns.get(dn, 0) + 1
    
    def record_dealer_query(self, dealer: str):
        self.most_asked_dealers[dealer] = self.most_asked_dealers.get(dealer, 0) + 1
    
    def record_product_query(self, product: str):
        self.most_asked_products[product] = self.most_asked_products.get(product, 0) + 1
    
    def record_failed_query(self, question: str, reason: str):
        self.failed_queries.append({
            "question": question[:100],
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat()
        })
        # Keep only last 1000
        if len(self.failed_queries) > 1000:
            self.failed_queries = self.failed_queries[-1000:]
    
    def record_intent(self, intent: str):
        self.intent_counts[intent] = self.intent_counts.get(intent, 0) + 1


# ==========================================================
# REDIS CACHE SERVICE (Phase 12)
# ==========================================================

class RedisCacheService:
    """Redis cache for frequently accessed data"""
    
    DEFAULT_TTL = 300  # 5 minutes
    
    def __init__(self):
        self.client = None
        self.enabled = False
        
        if REDIS_AVAILABLE and config.REDIS_URL:
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
    
    def get_dn_cache_key(self, dn: str) -> str:
        return f"dn:{dn}"
    
    def get_dealer_cache_key(self, dealer: str) -> str:
        return f"dealer:{dealer}"
    
    def get_product_cache_key(self, product: str) -> str:
        return f"product:{product}"
    
    def get_executive_cache_key(self) -> str:
        return "executive:dashboard"


# ==========================================================
# MAIN AI QUERY SERVICE - MASTER ORCHESTRATOR
# ==========================================================

class AIQueryService:
    """
    Master orchestrator for all logistics queries.
    NO BUSINESS LOGIC - only orchestration.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.start_time = None
        
        # Initialize all services (Phase 1-14)
        self.intent_engine = IntentEngine()
        self.entity_extractor = EntityExtractor()
        self.context_service = ContextService(db)
        self.query_router = QueryRouterService(db)
        self.report_generator = ReportGeneratorService()
        self.cache = RedisCacheService()
        
        # Analytics tracking (Phase 13)
        self.query_analytics = QueryAnalytics()
        
        logger.info("=" * 60)
        logger.info("🚀 AI QUERY ORCHESTRATOR v22.0")
        logger.info(f"   Redis Cache: {'Enabled' if self.cache.enabled else 'Disabled'}")
        logger.info("   Architecture: Modular Enterprise")
        logger.info("=" * 60)
    
    def process_query(
        self, 
        question: str, 
        user_phone: str = None, 
        user_role: str = None
    ) -> Dict[str, Any]:
        """
        Master orchestration method.
        
        Flow:
        1. Load context
        2. Extract entities
        3. Detect intent
        4. Route to service
        5. Generate response
        6. Save context
        7. Track metrics
        """
        self.start_time = time.time()
        metrics = QueryMetrics()
        
        question = question.strip()
        logger.info(f"📱 Processing: {question[:100]}")
        
        try:
            # ==========================================================
            # STEP 1: Load Context (Phase 4)
            # ==========================================================
            context_start = time.time()
            context = {}
            if user_phone:
                context = self.context_service.get_context(user_phone)
            metrics.context_time_ms = int((time.time() - context_start) * 1000)
            logger.debug(f"📚 Context loaded in {metrics.context_time_ms}ms")
            
            # ==========================================================
            # STEP 2: Extract Entities (Phase 3)
            # ==========================================================
            entity_start = time.time()
            entities = self.entity_extractor.extract_all(question)
            
            # Resolve follow-up using context
            if user_phone:
                resolved = self.context_service.resolve_follow_up(user_phone, question)
                for entity_type, value in resolved.items():
                    if entity_type not in entities:
                        entities[entity_type] = ExtractedEntity(
                            type=EntityType(entity_type),
                            value=value
                        )
            
            metrics.entity_time_ms = int((time.time() - entity_start) * 1000)
            logger.debug(f"🔍 Entities: {list(entities.keys())} in {metrics.entity_time_ms}ms")
            
            # Track entity analytics
            if EntityType.DN_NUMBER in entities:
                self.query_analytics.record_dn_query(entities[EntityType.DN_NUMBER].value)
            if EntityType.DEALER in entities:
                self.query_analytics.record_dealer_query(entities[EntityType.DEALER].value)
            if EntityType.PRODUCT in entities:
                self.query_analytics.record_product_query(entities[EntityType.PRODUCT].value)
            
            # ==========================================================
            # STEP 3: Detect Intent (Phase 2)
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
            # STEP 4: Route to Service (Phase 5)
            # ==========================================================
            route_start = time.time()
            route_result = self.query_router.route(
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
            metrics.service_time_ms = route_result.get("service_time_ms", 0)
            metrics.ai_time_ms = route_result.get("ai_time_ms", 0)
            
            logger.info(f"🚦 Routed to {service_name} in {metrics.route_time_ms}ms")
            
            # ==========================================================
            # STEP 5: Generate Response (Phase 11)
            # ==========================================================
            response_text = self.report_generator.format_response(
                data=service_response,
                intent=intent,
                format_type="whatsapp"
            )
            
            # ==========================================================
            # STEP 6: Save Context (Phase 4)
            # ==========================================================
            if user_phone and service_response:
                self.context_service.save_context(
                    phone_number=user_phone,
                    entities=entities,
                    intent=intent,
                    response=response_text[:500]
                )
            
            # ==========================================================
            # STEP 7: Calculate Total Metrics
            # ==========================================================
            metrics.total_time_ms = int((time.time() - self.start_time) * 1000)
            
            # Log performance summary
            logger.info(f"⚡ Performance Summary | Total: {metrics.total_time_ms}ms | "
                       f"Intent: {metrics.intent_time_ms}ms | "
                       f"Entity: {metrics.entity_time_ms}ms | "
                       f"Route: {metrics.route_time_ms}ms | "
                       f"Service: {metrics.service_time_ms}ms")
            
            return {
                "success": True,
                "response": response_text,
                "intent": intent.value,
                "confidence": confidence,
                "entities": {k: v.value for k, v in entities.items()},
                "metrics": metrics.to_dict(),
                "service": service_name
            }
            
        except Exception as e:
            logger.error(f"Query processing error: {e}")
            self.query_analytics.record_failed_query(question, str(e))
            
            metrics.total_time_ms = int((time.time() - self.start_time) * 1000) if self.start_time else 0
            
            return {
                "success": False,
                "response": self._get_error_response(),
                "error": str(e),
                "metrics": metrics.to_dict()
            }
    
    def _get_error_response(self) -> str:
        return """⚠️ *Service Temporarily Unavailable*

I'm having trouble processing your request right now.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these alternatives:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• "Help" - Show complete menu
• "DN 80012345" - Track a specific DN
• "Pending PODs" - Check POD status

Please try again in a few moments."""
    
    def get_query_analytics(self) -> Dict:
        """Get query analytics for monitoring (Phase 13)"""
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
            "intent_counts": self.query_analytics.intent_counts,
            "failed_queries_count": len(self.query_analytics.failed_queries)
        }


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
# WELCOME MESSAGE (Moved from orchestrator)
# ==========================================================

WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT v22.0*

Complete logistics intelligence with enterprise-grade architecture.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *WHAT YOU CAN ASK:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN TRACKING*
   • "DN 80012345" - Complete status
   • "Timeline of DN 80012345" - Journey tracking
   • "Products in DN 80012345" - Line items

🏪 *DEALER INSIGHTS*
   • "ABC Electronics" - Dealer dashboard
   • "Top dealers" - Rankings by sales
   • "My DNs" - Dealer self-service

🏭 *WAREHOUSE ANALYTICS*
   • "Lahore warehouse" - Performance dashboard
   • "Warehouse ranking" - Efficiency comparison

🌆 *CITY INTELLIGENCE*
   • "Karachi city" - City dashboard
   • "City ranking" - Performance by city

📦 *PRODUCT ANALYTICS*
   • "Product HSU-18HFPAA" - Product dashboard
   • "Top products" - Best sellers
   • "Fast moving products" - Velocity analysis

👑 *EXECUTIVE REPORTS*
   • "Executive summary" - Complete dashboard
   • "CEO briefing" - Leadership view
   • "Network health" - System status
   • "Top risks" - Critical issues
   • "Recommendations" - Action items

📈 *ADVANCED ANALYTICS*
   • "Why are deliveries delayed?" - Root cause
   • "What are the trends?" - Trend analysis
   • "Forecast next month sales" - Predictive

📋 *POD & PGI*
   • "Pending PODs" - Collection required
   • "Pending PGI" - Dispatch pending

💰 *REVENUE*
   • "Revenue analysis" - Complete breakdown
   • "Revenue at risk" - Exposure analysis

🚨 *CONTROL TOWER*
   • "Control tower" - Critical alerts
   • "Critical DNs" - Urgent attention needed

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRO TIPS:* I remember context! Ask "What products?" after a DN query.
    Type "Help" anytime for this menu.

*Powered by AI Logistics Intelligence v22.0 | Enterprise Architecture*"""
