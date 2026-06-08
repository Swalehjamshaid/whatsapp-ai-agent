# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v28.0)
# ==========================================================
# FULL ARCHITECTURE ALIGNMENT
# WhatsApp User → webhook.py → whatsapp_service.py → THIS FILE
#      │
#      ├── intent_engine.py
#      ├── entity_extractor.py
#      ├── context_service.py
#      └── query_router_service.py
#                    │
#                    ├── logistics_query_service.py
#                    ├── analytics_service.py
#                    ├── kpi_service.py
#                    ├── forecasting_service.py
#                    ├── recommendation_service.py
#                    ├── control_tower_service.py
#                    └── groq_insight_service.py
# ==========================================================

import time
import hashlib
import asyncio
import concurrent.futures
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session
from loguru import logger

from app.config import config


# ==========================================================
# CONSTANTS
# ==========================================================

SERVICE_TIMEOUT = 15  # seconds
CACHE_TTL = 300  # 5 minutes
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_TIMEOUT = 300  # 5 minutes
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]


# ==========================================================
# RESPONSE VALIDATOR
# ==========================================================

class ResponseValidator:
    """Validate responses before sending to user"""
    
    @staticmethod
    def validate(response: Any, source: str = "unknown") -> Tuple[bool, str]:
        """
        Validate response quality
        Returns (is_valid, validated_response)
        """
        if response is None:
            logger.warning(f"Empty response from {source}")
            return False, "⚠️ No response generated. Please try again."
        
        if isinstance(response, dict):
            if response.get("error"):
                logger.warning(f"Error response from {source}: {response.get('error')}")
                return False, f"⚠️ {response.get('error')}"
            
            if "response" in response:
                response = response["response"]
            elif "insight" in response:
                response = response["insight"]
        
        if not isinstance(response, str):
            return False, f"⚠️ Invalid response format: {type(response).__name__}"
        
        if len(response.strip()) == 0:
            return False, "⚠️ Empty response received. Please try again."
        
        # Check for JSON responses
        if response.strip().startswith('{') and response.strip().endswith('}'):
            try:
                import json
                parsed = json.loads(response)
                if isinstance(parsed, dict):
                    if "response" in parsed:
                        response = parsed["response"]
                    elif "error" in parsed:
                        return False, f"⚠️ Error: {parsed['error']}"
            except:
                pass
        
        return True, response


# ==========================================================
# CIRCUIT BREAKER
# ==========================================================

class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker for external service calls"""
    
    def __init__(self, name: str, failure_threshold: int = CIRCUIT_BREAKER_THRESHOLD,
                 timeout: int = CIRCUIT_BREAKER_TIMEOUT):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
    
    def can_call(self) -> bool:
        if self.state == CircuitBreakerState.CLOSED:
            return True
        
        if self.state == CircuitBreakerState.OPEN:
            if time.time() - self.last_failure_time > self.timeout:
                logger.info(f"Circuit breaker {self.name} transitioning to HALF_OPEN")
                self.state = CircuitBreakerState.HALF_OPEN
                return True
            return False
        
        return True
    
    def record_success(self):
        self.failure_count = 0
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.info(f"Circuit breaker {self.name} closed (success in half-open)")
            self.state = CircuitBreakerState.CLOSED
    
    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.warning(f"Circuit breaker {self.name} re-opening after half-open failure")
            self.state = CircuitBreakerState.OPEN
        elif self.state == CircuitBreakerState.CLOSED and self.failure_count >= self.failure_threshold:
            logger.error(f"Circuit breaker {self.name} OPENING after {self.failure_count} failures")
            self.state = CircuitBreakerState.OPEN
    
    def get_state(self) -> Dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count
        }


# ==========================================================
# REQUEST CACHE
# ==========================================================

class RequestCache:
    """Cache for request responses"""
    
    def __init__(self, ttl: int = CACHE_TTL):
        self.cache = {}
        self.ttl = ttl
    
    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return data
            del self.cache[key]
        return None
    
    def set(self, key: str, value: Any):
        self.cache[key] = (value, time.time())
    
    def get_cache_key(self, question: str, user_phone: str) -> str:
        normalized = question.lower().strip()
        return hashlib.md5(f"{user_phone}:{normalized}".encode()).hexdigest()


# ==========================================================
# REQUEST DEDUPLICATION
# ==========================================================

class RequestDeduplicator:
    """Prevent duplicate request processing"""
    
    def __init__(self, ttl: int = 5):
        self.processing = {}
        self.ttl = ttl
    
    def is_duplicate(self, key: str) -> bool:
        if key in self.processing:
            start_time = self.processing[key]
            if time.time() - start_time < self.ttl:
                return True
            del self.processing[key]
        return False
    
    def start_processing(self, key: str):
        self.processing[key] = time.time()
    
    def finish_processing(self, key: str):
        if key in self.processing:
            del self.processing[key]
    
    def get_key(self, question: str, user_phone: str) -> str:
        return hashlib.md5(f"{user_phone}:{question}".encode()).hexdigest()


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    """
    Enterprise AI Query Service - Master Orchestrator v28.0
    
    ARCHITECTURE FLOW:
    WhatsApp User → webhook.py → whatsapp_service.py → THIS FILE
          │
          ├── intent_engine.py (detect intent)
          ├── entity_extractor.py (extract DN, dealer, etc.)
          ├── context_service.py (load/save conversation context)
          └── query_router_service.py (route to business services)
                        │
                        ├── logistics_query_service.py (DN operations)
                        ├── analytics_service.py (analytics)
                        ├── kpi_service.py (executive KPI)
                        ├── forecasting_service.py (predictions)
                        ├── recommendation_service.py (actions)
                        ├── control_tower_service.py (alerts)
                        └── groq_insight_service.py (AI insights)
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.start_time = None
        self.request_id = None
        
        # Initialize core architecture components
        self._init_architecture_components()
        
        # Initialize supporting components
        self.validator = ResponseValidator()
        self.cache = RequestCache()
        self.deduplicator = RequestDeduplicator()
        self.circuit_breaker = CircuitBreaker("ai_service")
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "avg_response_time_ms": 0
        }
        
        logger.info("=" * 70)
        logger.info("🚀 AI QUERY ORCHESTRATOR v28.0 - ENTERPRISE READY")
        logger.info("   Architecture: Intent → Entity → Context → Router")
        logger.info("=" * 70)
    
    def _init_architecture_components(self):
        """Initialize all architecture components once"""
        try:
            # Core components (must load first)
            from app.services.intent_engine import IntentEngine
            from app.services.entity_extractor import EntityExtractor
            from app.services.context_service import ContextService
            from app.services.query_router_service import QueryRouterService
            from app.services.report_generator_service import ReportGeneratorService
            
            self.intent_engine = IntentEngine()
            self.entity_extractor = EntityExtractor()
            self.context_service = ContextService(self.db)
            self.query_router = QueryRouterService(self.db)
            self.report_generator = ReportGeneratorService()
            
            logger.info("✅ Core architecture components loaded")
            logger.info("   ├── intent_engine.py")
            logger.info("   ├── entity_extractor.py")
            logger.info("   ├── context_service.py")
            logger.info("   └── query_router_service.py")
            
            # Pre-load business services (optional, they will lazy load via router)
            logger.info("✅ Business services ready (lazy loading via router)")
            logger.info("   ├── logistics_query_service.py")
            logger.info("   ├── analytics_service.py")
            logger.info("   ├── kpi_service.py")
            logger.info("   ├── forecasting_service.py")
            logger.info("   ├── recommendation_service.py")
            logger.info("   ├── control_tower_service.py")
            logger.info("   └── groq_insight_service.py")
            
        except Exception as e:
            logger.error(f"Failed to initialize architecture components: {e}")
            raise
    
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
        Master orchestration method - Full Architecture Flow
        
        Flow:
        1. Validate request
        2. Check cache
        3. Check duplicate
        4. Load context (context_service.py)
        5. Extract entities (entity_extractor.py)
        6. Detect intent (intent_engine.py)
        7. Route to service (query_router_service.py)
        8. Validate response
        9. Cache response
        10. Save context (context_service.py)
        """
        self.start_time = time.time()
        self.request_id = hashlib.md5(f"{user_phone}:{question}".encode()).hexdigest()[:8]
        
        # Update metrics
        self.metrics["total_requests"] += 1
        
        question = question.strip()
        
        # Structured logging
        logger.info(f"[{self.request_id}] 📱 REQ | Question={question[:100]} | User={user_phone}")
        
        # ==========================================================
        # STEP 1: Validate Request
        # ==========================================================
        if not question:
            return self._error_response("Empty question", "validation")
        
        # ==========================================================
        # STEP 2: Check Cache
        # ==========================================================
        cache_key = self.cache.get_cache_key(question, user_phone or "anonymous")
        cached_response = self.cache.get(cache_key)
        
        if cached_response:
            logger.info(f"[{self.request_id}] 💾 CACHE HIT")
            return cached_response
        
        # ==========================================================
        # STEP 3: Check Duplicate
        # ==========================================================
        dedup_key = self.deduplicator.get_key(question, user_phone or "anonymous")
        
        if self.deduplicator.is_duplicate(dedup_key):
            logger.warning(f"[{self.request_id}] ⏭️ DUPLICATE REQUEST")
            return self._error_response("Duplicate request detected", "deduplication")
        
        self.deduplicator.start_processing(dedup_key)
        
        try:
            # ==========================================================
            # STEP 4: Load Context (context_service.py)
            # ==========================================================
            context = self.context_service.get_context(user_phone) if user_phone else {}
            logger.debug(f"[{self.request_id}] 📚 Context loaded")
            
            # ==========================================================
            # STEP 5: Extract Entities (entity_extractor.py)
            # ==========================================================
            entities = self.entity_extractor.extract_all(question)
            
            # Log extracted entities
            entity_summary = {}
            for k, v in entities.items():
                if hasattr(v, 'value'):
                    entity_summary[k.value] = v.value
                else:
                    entity_summary[str(k)] = str(v)
            logger.debug(f"[{self.request_id}] 🔍 Entities: {entity_summary}")
            
            # Resolve follow-up using context
            if user_phone:
                resolved = self.context_service.resolve_follow_up(user_phone, question)
                for entity_type, value in resolved.items():
                    if entity_type not in entities:
                        from app.services.entity_extractor import EntityType, ExtractedEntity
                        entities[entity_type] = ExtractedEntity(
                            type=EntityType(entity_type),
                            value=value
                        )
                        logger.debug(f"[{self.request_id}] 🔄 Resolved {entity_type}: {value}")
            
            # ==========================================================
            # STEP 6: Detect Intent (intent_engine.py)
            # ==========================================================
            intent, intent_entity, confidence = self.intent_engine.detect_intent(
                question, entities, context
            )
            logger.info(f"[{self.request_id}] 🎯 Intent={intent.value} (confidence={confidence:.2f})")
            
            # ==========================================================
            # STEP 7: Route to Service (query_router_service.py)
            # ==========================================================
            route_start = time.time()
            
            # Extract DN for DN lookup if present
            from app.services.entity_extractor import EntityType
            if EntityType.DN_NUMBER in entities:
                dn_entity = entities[EntityType.DN_NUMBER]
                intent_entity = dn_entity.value if hasattr(dn_entity, 'value') else str(dn_entity)
                logger.info(f"[{self.request_id}] 🔢 DN resolved: {intent_entity}")
            
            # Route with timeout protection
            route_result = self._route_with_timeout(
                intent=intent,
                entity=intent_entity,
                entities=entities,
                context=context,
                user_phone=user_phone,
                user_role=user_role,
                question=question
            )
            
            route_time = (time.time() - route_start) * 1000
            logger.debug(f"[{self.request_id}] 🚦 Route time: {route_time:.0f}ms")
            
            service_response = route_result.get("response", {})
            service_name = route_result.get("service", "unknown")
            
            # ==========================================================
            # STEP 8: Validate Response
            # ==========================================================
            is_valid, validated_response = self.validator.validate(service_response, service_name)
            
            if not is_valid:
                logger.warning(f"[{self.request_id}] ⚠️ Response validation failed, using fallback")
                validated_response = self._get_fallback_response(question)
            
            # Format response using report generator
            formatted_response = self.report_generator.format_response(
                data={"response": validated_response} if isinstance(validated_response, str) else validated_response,
                intent=intent,
                format_type="whatsapp"
            )
            
            # ==========================================================
            # STEP 9: Cache Response
            # ==========================================================
            final_response = {
                "success": True,
                "response": formatted_response,
                "intent": intent.value,
                "confidence": confidence,
                "service": service_name,
                "request_id": self.request_id
            }
            
            # Cache successful responses
            if is_valid and len(formatted_response) > 50:
                self.cache.set(cache_key, final_response)
                logger.debug(f"[{self.request_id}] 💾 Response cached")
            
            # ==========================================================
            # STEP 10: Save Context (context_service.py)
            # ==========================================================
            if user_phone:
                self.context_service.save_context(
                    phone_number=user_phone,
                    entities=entities,
                    intent=intent,
                    response=formatted_response[:500]
                )
            
            # ==========================================================
            # STEP 11: Metrics & Logging
            # ==========================================================
            total_time = (time.time() - self.start_time) * 1000
            self.metrics["successful_requests"] += 1
            self.metrics["avg_response_time_ms"] = (
                (self.metrics["avg_response_time_ms"] * (self.metrics["total_requests"] - 1) + total_time)
                / self.metrics["total_requests"]
            )
            
            # Flow Summary
            logger.info(
                f"[{self.request_id}] 📊 FLOW SUMMARY | "
                f"Intent={intent.value} | "
                f"Service={service_name} | "
                f"Time={total_time:.0f}ms | "
                f"ResponseLen={len(formatted_response)}"
            )
            
            return final_response
            
        except Exception as e:
            logger.exception(f"[{self.request_id}] ❌ Processing error: {e}")
            self.metrics["failed_requests"] += 1
            return self._error_response(str(e), "processing")
        
        finally:
            self.deduplicator.finish_processing(dedup_key)
    
    def _route_with_timeout(self, **kwargs) -> Dict:
        """Route with timeout protection"""
        
        def _route():
            return self.query_router.route(**kwargs)
        
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(_route)
                return future.result(timeout=SERVICE_TIMEOUT)
        except concurrent.futures.TimeoutError:
            logger.error(f"[{self.request_id}] ⏰ Router timeout after {SERVICE_TIMEOUT}s")
            return {
                "success": False,
                "response": {"error": "Service timeout", "fallback": True},
                "service": "timeout",
                "service_time_ms": SERVICE_TIMEOUT * 1000
            }
        except Exception as e:
            logger.error(f"[{self.request_id}] Router error: {e}")
            return {
                "success": False,
                "response": {"error": str(e), "fallback": True},
                "service": "error",
                "service_time_ms": 0
            }
    
    def _error_response(self, error_msg: str, source: str) -> Dict:
        """Standard error response"""
        return {
            "success": False,
            "response": f"⚠️ Error: {error_msg}",
            "error": error_msg,
            "source": source,
            "request_id": self.request_id
        }
    
    def _get_fallback_response(self, question: str) -> str:
        """Fallback response when all else fails"""
        return f"""
🤖 *AI LOGISTICS ASSISTANT*

I received: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 "6243612278" - Track a DN
🏪 "Top dealers" - Dealer rankings
👑 "Executive summary" - Dashboard
📋 "Pending PODs" - Collection status
❓ "Help" - Complete menu

*Powered by Enterprise Logistics Intelligence v28.0*
"""
    
    # ==========================================================
    # HEALTH CHECK
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check for monitoring"""
        
        # Check database
        db_healthy = False
        try:
            self.db.execute("SELECT 1")
            db_healthy = True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
        
        # Check router
        router_healthy = self.query_router is not None
        
        return {
            "status": "healthy" if (db_healthy and router_healthy) else "degraded",
            "version": "28.0",
            "components": {
                "database": db_healthy,
                "router": router_healthy,
                "cache": bool(self.cache.cache),
                "circuit_breaker": self.circuit_breaker.get_state()
            },
            "architecture": {
                "intent_engine": True,
                "entity_extractor": True,
                "context_service": True,
                "query_router": True
            },
            "metrics": {
                "total_requests": self.metrics["total_requests"],
                "successful_requests": self.metrics["successful_requests"],
                "failed_requests": self.metrics["failed_requests"],
                "success_rate": round(
                    self.metrics["successful_requests"] / max(1, self.metrics["total_requests"]) * 100, 1
                ),
                "avg_response_time_ms": round(self.metrics["avg_response_time_ms"], 2)
            }
        }
    
    def get_metrics(self) -> Dict:
        """Get service metrics"""
        return self.metrics


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(
    question: str, 
    db: Session, 
    user_phone: str = None, 
    user_role: str = None
) -> str:
    """
    Process WhatsApp query and return response.
    
    This is the main entry point called by whatsapp_service.py.
    It creates an instance of AIQueryService and orchestrates the response.
    """
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.exception(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


# ==========================================================
# HEALTH CHECK FUNCTION
# ==========================================================

def health_check(db: Session) -> Dict[str, Any]:
    """Health check for the AI Query Service"""
    try:
        service = AIQueryService(db)
        return service.health_check()
    except Exception as e:
        logger.exception(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "version": "28.0"
        }
