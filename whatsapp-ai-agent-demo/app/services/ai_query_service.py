# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v23.1)
# ==========================================================
# MASTER ORCHESTRATOR WITH FULL GROQ AI INTEGRATION
# - FIXED: Cache Logic Bug (Operator Precedence)
# - FIXED: QueryRouter Integration (No Duplication)
# - FIXED: DN Entity Extraction Bug
# - FIXED: Exception Logging with Stack Trace
# ==========================================================

import time
import json
import traceback
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
# HELPER FUNCTION: Extract DN Value Safely
# ==========================================================

def extract_dn_value(dn_entity) -> Optional[str]:
    """
    Safely extract DN string value from various possible formats.
    
    Handles:
    - String: "6243612278"
    - ExtractedEntity: ExtractedEntity(value="6243612278")
    - Dict: {"value": "6243612278"}
    - None
    """
    if not dn_entity:
        return None
    
    if isinstance(dn_entity, str):
        return dn_entity
    
    if hasattr(dn_entity, 'value'):
        return str(dn_entity.value)
    
    if isinstance(dn_entity, dict):
        return str(dn_entity.get('value', dn_entity.get('dn_number', '')))
    
    return str(dn_entity) if dn_entity else None


# ==========================================================
# MAIN AI QUERY SERVICE - MASTER ORCHESTRATOR
# ==========================================================

class AIQueryService:
    """
    Master orchestrator for all logistics queries.
    NO BUSINESS LOGIC - only orchestration.
    FULL GROQ AI INTEGRATION.
    Enterprise Ready v23.1
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.start_time = None
        
        # Initialize all services
        self.intent_engine = IntentEngine()
        self.entity_extractor = EntityExtractor()
        self.context_service = ContextService(db)
        self.query_router = QueryRouterService(db)  # SINGLE SOURCE OF TRUTH
        self.report_generator = ReportGeneratorService()
        self.cache = RedisCacheService()
        self.groq_service = GroqInsightService(db)  # GROQ AI Service
        
        # Analytics tracking
        self.query_analytics = QueryAnalytics()
        
        # Dealer phone mapping (for self-service)
        self.dealer_phone_map = self._load_dealer_phone_map()
        
        logger.info("=" * 70)
        logger.info("🚀 AI QUERY ORCHESTRATOR v23.1 - ENTERPRISE READY")
        logger.info(f"   Redis Cache: {'Enabled' if self.cache.enabled else 'Disabled'}")
        logger.info(f"   GROQ AI: {'Available' if self.groq_service.ai_available else 'Not Available'}")
        logger.info(f"   QueryRouter: Integrated (Single Source of Truth)")
        logger.info(f"   Dealer Self-Service: {'Enabled' if self.dealer_phone_map else 'Pending'}")
        logger.info("   Architecture: Full Modular Enterprise")
        logger.info("=" * 70)
    
    def _load_dealer_phone_map(self) -> Dict[str, str]:
        """Load dealer to phone number mapping for self-service"""
        # In production, load from database
        return {}
    
    # ==========================================================
    # HELPER: Extract Entity Value Safely
    # ==========================================================
    
    def _extract_entity_value(self, entity) -> Optional[str]:
        """Safely extract string value from entity"""
        if not entity:
            return None
        if isinstance(entity, str):
            return entity
        if hasattr(entity, 'value'):
            return str(entity.value)
        if isinstance(entity, dict):
            return str(entity.get('value', ''))
        return str(entity)
    
    def _extract_dn_from_entities(self, entities: Dict) -> Optional[str]:
        """
        CRITICAL FIX: Extract DN number from entities safely.
        Handles both string and ExtractedEntity objects.
        """
        if EntityType.DN_NUMBER in entities:
            dn_entity = entities[EntityType.DN_NUMBER]
            return extract_dn_value(dn_entity)
        
        # Also check for direct dn_number key
        if 'dn_number' in entities:
            return self._extract_entity_value(entities['dn_number'])
        
        return None
    
    # ==========================================================
    # MAIN PROCESSING METHOD
    # ==========================================================
    
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
        5. Route to service (via QueryRouter - SINGLE SOURCE)
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
            # STEP 1: Load Context
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
            
            # Log extracted entities for debugging
            entity_summary = {}
            for k, v in entities.items():
                if hasattr(v, 'value'):
                    entity_summary[k.value] = v.value
                else:
                    entity_summary[str(k)] = str(v)
            logger.debug(f"🔍 Extracted entities: {entity_summary}")
            
            # Track entity analytics
            dn_value = self._extract_dn_from_entities(entities)
            if dn_value:
                self.query_analytics.record_dn_query(dn_value)
            
            if EntityType.DEALER in entities:
                dealer_val = self._extract_entity_value(entities[EntityType.DEALER])
                if dealer_val:
                    self.query_analytics.record_dealer_query(dealer_val)
            
            if EntityType.PRODUCT in entities:
                product_val = self._extract_entity_value(entities[EntityType.PRODUCT])
                if product_val:
                    self.query_analytics.record_product_query(product_val)
            
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
            # STEP 4: Check Cache
            # ==========================================================
            cache_key = self._get_cache_key(intent, intent_entity, entities)
            cached_response = None
            
            if cache_key and self.cache.enabled:
                cache_start = time.time()
                cached_response = self.cache.get(cache_key)
                metrics.cache_time_ms = int((time.time() - cache_start) * 1000)
                
                if cached_response:
                    self.query_analytics.record_cache_hit()
                    logger.info(f"💾 Cache HIT for {intent.value}")
                    
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
            
            # ==========================================================
            # STEP 5: Route to Service (VIA QUERY ROUTER - SINGLE SOURCE)
            # ==========================================================
            route_start = time.time()
            
            # CRITICAL FIX: Extract DN value properly for DN lookup
            if intent == IntentType.DN_LOOKUP or intent == IntentType.DN_TIMELINE or intent == IntentType.DN_PRODUCTS:
                # Use the extracted DN value
                dn_number = self._extract_dn_from_entities(entities)
                if dn_number:
                    intent_entity = dn_number
                    logger.info(f"🔢 DN resolved: {dn_number}")
                else:
                    logger.warning(f"⚠️ DN intent but no DN number found in entities: {entities}")
            
            # Use QueryRouter as SINGLE SOURCE OF TRUTH
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
            category = route_result.get("category", "general")
            metrics.service_time_ms = route_result.get("service_time_ms", 0)
            metrics.ai_time_ms = route_result.get("ai_time_ms", 0)
            
            logger.info(f"🚦 Routed to {service_name} ({category}) in {metrics.route_time_ms}ms")
            
            # ==========================================================
            # STEP 5b: Cache Response (FIXED OPERATOR PRECEDENCE)
            # ==========================================================
            # CRITICAL FIX: Proper operator precedence for cache condition
            should_cache = (
                cache_key
                and service_response
                and (
                    not isinstance(service_response, dict)
                    or not service_response.get("error")
                )
            )
            
            if should_cache:
                ttl = self.cache.get_ttl(intent.value) if hasattr(self.cache, 'get_ttl') else 300
                self.cache.set(cache_key, service_response, ttl=ttl)
                logger.debug(f"💾 Cached {intent.value} with TTL {ttl}s")
            
            # Track category analytics
            self.query_analytics.record_category(category)
            
            # ==========================================================
            # STEP 6: Generate Response
            # ==========================================================
            report_start = time.time()
            response_text = self.report_generator.format_response(
                data=service_response,
                intent=intent,
                format_type="whatsapp"
            )
            metrics.report_time_ms = int((time.time() - report_start) * 1000)
            
            # ==========================================================
            # STEP 7: Save Context
            # ==========================================================
            if user_phone and service_response:
                self.context_service.save_context(
                    phone_number=user_phone,
                    entities=entities,
                    intent=intent,
                    response=response_text[:500]
                )
            
            # ==========================================================
            # STEP 8: Calculate Metrics
            # ==========================================================
            metrics.total_time_ms = int((time.time() - self.start_time) * 1000)
            self.query_analytics.record_response_time(metrics.total_time_ms)
            
            # Log performance summary
            logger.info(f"⚡ Performance | Total: {metrics.total_time_ms}ms | "
                       f"Intent: {metrics.intent_time_ms}ms | "
                       f"Route: {metrics.route_time_ms}ms | "
                       f"Service: {metrics.service_time_ms}ms")
            
            return {
                "success": True,
                "response": response_text,
                "intent": intent.value,
                "category": category,
                "confidence": confidence,
                "entities": entity_summary,
                "metrics": metrics.to_dict(),
                "service": service_name,
                "cached": False
            }
            
        except Exception as e:
            # CRITICAL FIX: Use logger.exception for full stack trace
            logger.exception(f"Query processing error: {e}")
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
                    logger.exception(f"GROQ fallback error: {ai_err}")
            
            metrics.total_time_ms = int((time.time() - self.start_time) * 1000) if self.start_time else 0
            
            return {
                "success": False,
                "response": self._get_error_response(),
                "error": str(e),
                "traceback": traceback.format_exc() if config.DEBUG else None,
                "metrics": metrics.to_dict()
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
            key_parts.append(str(entity))
        
        if entities:
            for k, v in entities.items():
                if hasattr(v, 'value'):
                    key_parts.append(f"{k}:{v.value}")
                elif isinstance(v, str):
                    key_parts.append(f"{k}:{v}")
        
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
            logger.info(f"📞 Registered {phone_number} -> {dealer_name}")
            return True
        except Exception as e:
            logger.exception(f"Failed to register dealer phone: {e}")
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
        logger.exception(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


# ==========================================================
# WELCOME MESSAGE
# ==========================================================

WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT v23.1*

Complete logistics intelligence with enterprise-grade architecture.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *WHAT YOU CAN ASK:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN TRACKING*
   • "DN 80012345" - Complete status with all products
   • "Timeline of DN 80012345" - Journey tracking
   • "Products in DN 80012345" - Line items

🏪 *DEALER INSIGHTS*
   • "ABC Electronics" - Complete dealer dashboard
   • "Top dealers" - Rankings by sales
   • "My DNs" - Dealer self-service

🏭 *WAREHOUSE ANALYTICS*
   • "Lahore warehouse" - Performance dashboard
   • "Warehouse ranking" - Efficiency comparison

🌆 *CITY INTELLIGENCE*
   • "Karachi city" - City dashboard
   • "City ranking" - Performance by city

📦 *PRODUCT ANALYTICS*
   • "Product HSU-18HFPAA" - Product intelligence
   • "Top products" - Best sellers

👑 *EXECUTIVE REPORTS*
   • "Executive summary" - Complete KPI dashboard
   • "CEO briefing" - Leadership view
   • "Network health" - System status

🧠 *AI INSIGHTS (Powered by GROQ)*
   • "Why are deliveries delayed?" - Root cause analysis
   • "What are the trends?" - Trend analysis
   • "Forecast next month sales" - Predictive analysis

📋 *POD & PGI*
   • "Pending PODs" - Collection required
   • "Pending PGI" - Dispatch pending

💰 *REVENUE*
   • "Revenue analysis" - Complete breakdown
   • "Revenue at risk" - Exposure analysis

🚨 *EXCEPTION MANAGEMENT*
   • "Control tower" - Critical alerts
   • "Critical DNs" - Severe delays

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRO TIPS:* I remember context! Ask "What products?" after a DN query.
    Type "Help" anytime for this menu.

*Powered by GROQ AI | Enterprise Logistics Intelligence v23.1*"""


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
            "query_router": "integrated",
            "dealer_self_service": bool(service.dealer_phone_map),
            "version": "23.1"
        }
    except Exception as e:
        logger.exception(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "version": "23.1"
        }
