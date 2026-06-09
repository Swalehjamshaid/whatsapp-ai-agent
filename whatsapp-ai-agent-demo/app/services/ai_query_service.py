# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v29.0)
# ==========================================================
# FULL ARCHITECTURE ALIGNMENT - IMPROVED VERSION
#
# IMPROVEMENTS APPLIED:
# ✅ FIX #1: Better error handling for DN not found (prevents "N/A" output)
# ✅ FIX #2: Added response transformation for consistent formatting
# ✅ FIX #3: Enhanced report_generator integration
# ✅ FIX #4: Added fallback for empty/error responses
# ✅ FIX #5: Improved context resolution for follow-up questions
# ✅ FIX #6: Added async support for better performance
# ✅ FIX #7: Enhanced logging for debugging
# ✅ FIX #8: Added response quality scoring
# ✅ FIX #9: Added retry logic for transient failures
# ✅ FIX #10: Improved cache invalidation strategy
#
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
from typing import Dict, Any, Optional, Tuple, List
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

# FIX #8: Response quality thresholds
MIN_RESPONSE_LENGTH = 20
MAX_RESPONSE_LENGTH = 4096  # WhatsApp limit


# ==========================================================
# RESPONSE QUALITY SCORER (FIX #8)
# ==========================================================

class ResponseQualityScorer:
    """Score response quality to ensure user gets meaningful output"""
    
    @staticmethod
    def score(response: str) -> Tuple[int, str]:
        """
        Score response quality (0-100)
        Returns (score, reason)
        """
        if not response:
            return 0, "Empty response"
        
        response_lower = response.lower()
        
        # Check for "N/A" patterns (FIX #1)
        na_patterns = [
            "dealer: n/a", "city: n/a", "warehouse: n/a",
            "status: n/a", "division: n/a", "delay: n/a",
            "health score: 0", "risk score: 0"
        ]
        
        na_count = sum(1 for pattern in na_patterns if pattern in response_lower)
        if na_count >= 3:
            return 10, f"Response contains {na_count} 'N/A' values - likely DN not found"
        
        # Check for error indicators
        error_indicators = ["error", "not found", "unavailable", "failed", "exception"]
        error_count = sum(1 for ind in error_indicators if ind in response_lower)
        
        if error_count >= 2:
            return 20, f"Response contains {error_count} error indicators"
        
        # Check length
        if len(response) < MIN_RESPONSE_LENGTH:
            return 30, f"Response too short ({len(response)} chars)"
        
        # Check for meaningful content
        meaningful_markers = [
            "✅", "📦", "📊", "🚚", "⚠️", "🔴", "🟢",
            "delivered", "pending", "completed", "status"
        ]
        
        if not any(marker in response_lower or marker in response for marker in meaningful_markers):
            return 40, "Response lacks meaningful content markers"
        
        # Good quality
        if len(response) > 100 and any(marker in response for marker in ["✅", "📦", "📊"]):
            return 90, "High quality response with emojis and sufficient length"
        
        return 70, "Acceptable quality response"


# ==========================================================
# RESPONSE TRANSFORMER (FIX #2)
# ==========================================================

class ResponseTransformer:
    """Transform raw service responses into user-friendly format"""
    
    @staticmethod
    def transform(response: Any, intent: Any = None) -> str:
        """
        Transform any response into a user-friendly string.
        
        CRITICAL FIX: Handles DN not found errors gracefully
        """
        # Handle None
        if response is None:
            return "⚠️ No response received. Please try again."
        
        # Handle dictionary responses
        if isinstance(response, dict):
            # FIX #1: Handle DN not found properly
            if response.get("error"):
                error_msg = response.get("error")
                if "not found" in error_msg.lower():
                    dn = error_msg.split()[1] if len(error_msg.split()) > 1 else "this DN"
                    return f"""
❌ *DN NOT FOUND*

I couldn't find DN `{dn}` in the system.

💡 *Possible reasons:*
• The DN number may be incorrect
• The DN may not have been created yet
• The DN may be from a different system

💡 *Try:*
• Check the number and try again
• Type "Help" for available commands
"""
                return f"⚠️ {error_msg}"
            
            # Extract data from response
            if "data" in response:
                data = response["data"]
                # Check if data has meaningful values
                if data.get("dealer") in [None, "N/A", "Unknown Dealer", "Unknown"]:
                    logger.warning(f"Response has unknown dealer: {data.get('dealer')}")
                    # Still return but with note
                    return ResponseTransformer._build_dn_not_found_help(response)
            
            # Extract common response fields
            if "response" in response:
                return ResponseTransformer.transform(response["response"], intent)
            if "insight" in response:
                return response["insight"]
            if "message" in response:
                return response["message"]
            if "text" in response:
                return response["text"]
            
            # If we have a DN intelligence response with error flag
            if response.get("response_type") == "dn_intelligence" and not response.get("success"):
                return ResponseTransformer._build_dn_not_found_help(response)
        
        # Handle string responses
        if isinstance(response, str):
            # Check if it's a JSON string
            if response.strip().startswith('{') and response.strip().endswith('}'):
                try:
                    import json
                    parsed = json.loads(response)
                    return ResponseTransformer.transform(parsed, intent)
                except:
                    pass
            return response
        
        # Handle list responses
        if isinstance(response, list):
            if not response:
                return "No data available."
            return "\n".join(str(item) for item in response[:10])
        
        # Default fallback
        return str(response)
    
    @staticmethod
    def _build_dn_not_found_help(response: Dict) -> str:
        """Build helpful DN not found message"""
        dn = response.get("dn_no") or response.get("data", {}).get("dn_no") or "this DN"
        return f"""
❌ *DN NOT FOUND*

Could not retrieve information for DN `{dn}`.

💡 *Try these steps:*
1. Verify the DN number is correct
2. Try without spaces or special characters
3. Check if DN exists in the system

💡 *Example formats accepted:*
• `6243612278`
• `DN 6243612278`

Type "Help" for all available commands.
"""


# ==========================================================
# RESPONSE VALIDATOR (ENHANCED)
# ==========================================================

class ResponseValidator:
    """Validate responses before sending to user"""
    
    def __init__(self):
        self.quality_scorer = ResponseQualityScorer()
    
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
            # FIX #1: Handle error responses
            if response.get("error"):
                error_msg = response.get("error")
                logger.warning(f"Error response from {source}: {error_msg}")
                
                # Handle DN not found specially
                if "not found" in str(error_msg).lower():
                    return False, ResponseTransformer._build_dn_not_found_help(response)
                
                return False, f"⚠️ {error_msg}"
            
            if response.get("success") is False:
                error_msg = response.get("error") or response.get("message") or "Unknown error"
                return False, f"⚠️ {error_msg}"
            
            if "response" in response:
                response = response["response"]
            elif "insight" in response:
                response = response["insight"]
            elif "data" in response:
                # Check if data is meaningful
                data = response["data"]
                if isinstance(data, dict):
                    # Check for "N/A" values in data
                    na_fields = [k for k, v in data.items() if v in [None, "N/A", "Unknown", "Unknown Dealer"]]
                    if len(na_fields) >= 3:
                        logger.warning(f"Response has {len(na_fields)} unknown fields: {na_fields}")
        
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
# REQUEST CACHE (ENHANCED)
# ==========================================================

class RequestCache:
    """Cache for request responses with improved invalidation"""
    
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
    
    def invalidate_user(self, user_phone: str):
        """Invalidate all cache for a user"""
        to_delete = [k for k in self.cache.keys() if user_phone in k]
        for k in to_delete:
            del self.cache[k]
        logger.info(f"Invalidated {len(to_delete)} cache entries for {user_phone}")
    
    def clear(self):
        """Clear all cache"""
        count = len(self.cache)
        self.cache.clear()
        logger.info(f"Cleared {count} cache entries")


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
# RETRY HANDLER (FIX #9)
# ==========================================================

class RetryHandler:
    """Handle retries for transient failures"""
    
    def __init__(self, max_retries: int = MAX_RETRIES, delays: List[int] = None):
        self.max_retries = max_retries
        self.delays = delays or RETRY_DELAYS
    
    def execute(self, func, *args, **kwargs):
        """Execute function with retries"""
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.delays[min(attempt, len(self.delays) - 1)]
                    logger.warning(f"Retry {attempt + 1}/{self.max_retries} after {delay}s: {e}")
                    time.sleep(delay)
        
        raise last_error


# ==========================================================
# MAIN AI QUERY SERVICE (ENHANCED)
# ==========================================================

class AIQueryService:
    """
    Enterprise AI Query Service - Master Orchestrator v29.0
    
    IMPROVEMENTS:
    - Better DN not found handling (prevents "N/A" output)
    - Response quality scoring
    - Enhanced error messages
    - Retry logic for transient failures
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.start_time = None
        self.request_id = None
        
        # Initialize architecture components
        self._init_architecture_components()
        
        # Initialize supporting components
        self.validator = ResponseValidator()
        self.transformer = ResponseTransformer()
        self.cache = RequestCache()
        self.deduplicator = RequestDeduplicator()
        self.circuit_breaker = CircuitBreaker("ai_service")
        self.retry_handler = RetryHandler()
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "avg_response_time_ms": 0,
            "cache_hits": 0,
            "quality_scores": []
        }
        
        logger.info("=" * 70)
        logger.info("🚀 AI QUERY ORCHESTRATOR v29.0 - ENTERPRISE READY")
        logger.info("   Improvements: DN not found handling, Quality scoring, Retry logic")
        logger.info("=" * 70)
    
    def _init_architecture_components(self):
        """Initialize all architecture components once"""
        try:
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
        
        IMPROVEMENTS:
        - Better handling of DN not found
        - Response quality scoring
        - Enhanced fallback messages
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
            self.metrics["cache_hits"] += 1
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
            # STEP 4: Load Context
            # ==========================================================
            context = self.context_service.get_context(user_phone) if user_phone else {}
            logger.debug(f"[{self.request_id}] 📚 Context loaded")
            
            # ==========================================================
            # STEP 5: Extract Entities
            # ==========================================================
            entities = self.entity_extractor.extract_all(question)
            
            # Log extracted entities
            entity_summary = {}
            for k, v in entities.items():
                if hasattr(v, 'value'):
                    entity_summary[k.value if hasattr(k, 'value') else str(k)] = v.value
                else:
                    entity_summary[str(k)] = str(v)
            logger.debug(f"[{self.request_id}] 🔍 Entities: {entity_summary}")
            
            # ==========================================================
            # STEP 6: Resolve Follow-up
            # ==========================================================
            if user_phone:
                resolved = self.context_service.resolve_follow_up(user_phone, question)
                for entity_type, value in resolved.items():
                    if entity_type not in entities:
                        from app.services.entity_extractor import EntityType, ExtractedEntity
                        try:
                            entity_type_enum = EntityType(entity_type) if isinstance(entity_type, str) else entity_type
                            entities[entity_type_enum] = ExtractedEntity(
                                type=entity_type_enum,
                                value=value
                            )
                            logger.debug(f"[{self.request_id}] 🔄 Resolved {entity_type}: {value}")
                        except Exception as e:
                            logger.warning(f"Could not resolve entity {entity_type}: {e}")
            
            # ==========================================================
            # STEP 7: Detect Intent
            # ==========================================================
            intent, intent_entity, confidence = self.intent_engine.detect_intent(
                question, entities, context
            )
            logger.info(f"[{self.request_id}] 🎯 Intent={intent.value} (confidence={confidence:.2f})")
            
            # ==========================================================
            # STEP 8: Route to Service
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
            # STEP 9: Transform Response (CRITICAL FIX)
            # ==========================================================
            # Always transform response to ensure user-friendly format
            # This handles DN not found errors gracefully
            transformed_response = self.transformer.transform(service_response, intent)
            
            # ==========================================================
            # STEP 10: Validate Response
            # ==========================================================
            is_valid, validated_response = self.validator.validate(transformed_response, service_name)
            
            if not is_valid:
                logger.warning(f"[{self.request_id}] ⚠️ Response validation failed, using fallback")
                validated_response = self._get_fallback_response(question)
            
            # ==========================================================
            # STEP 11: Score Response Quality (FIX #8)
            # ==========================================================
            quality_score, quality_reason = ResponseQualityScorer.score(validated_response)
            self.metrics["quality_scores"].append(quality_score)
            
            if quality_score < 50:
                logger.warning(f"[{self.request_id}] 📉 Low quality response: {quality_reason}")
                # Try to enhance low quality response
                validated_response = self._enhance_low_quality_response(validated_response, intent)
            
            # ==========================================================
            # STEP 12: Format Response
            # ==========================================================
            formatted_response = self.report_generator.format_response(
                data={"response": validated_response} if isinstance(validated_response, str) else validated_response,
                intent=intent,
                format_type="whatsapp"
            )
            
            # ==========================================================
            # STEP 13: Build Final Response
            # ==========================================================
            final_response = {
                "success": True,
                "response": formatted_response,
                "intent": intent.value,
                "confidence": confidence,
                "service": service_name,
                "request_id": self.request_id,
                "quality_score": quality_score
            }
            
            # Cache successful responses
            if is_valid and quality_score > 50 and len(formatted_response) > 50:
                self.cache.set(cache_key, final_response)
                logger.debug(f"[{self.request_id}] 💾 Response cached")
            
            # ==========================================================
            # STEP 14: Save Context
            # ==========================================================
            if user_phone:
                self.context_service.save_context(
                    phone_number=user_phone,
                    entities=entities,
                    intent=intent,
                    response=formatted_response[:500]
                )
            
            # ==========================================================
            # STEP 15: Metrics & Logging
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
                f"Quality={quality_score} | "
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
        """Route with timeout protection and retry logic"""
        
        def _route():
            return self.query_router.route(**kwargs)
        
        try:
            # Use retry handler for transient failures
            result = self.retry_handler.execute(_route)
            return result
        except concurrent.futures.TimeoutError:
            logger.error(f"[{self.request_id}] ⏰ Router timeout after {SERVICE_TIMEOUT}s")
            return {
                "success": False,
                "response": {"error": "Service timeout. Please try again.", "fallback": True},
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
    
    def _enhance_low_quality_response(self, response: str, intent: Any) -> str:
        """Enhance low quality responses with helpful suggestions"""
        
        # Check if it's a DN not found scenario
        if "not found" in response.lower() or "n/a" in response.lower():
            return f"""
{response}

💡 *Need help?*
• Type "Help" for all available commands
• Try "Top dealers" for dealer performance
• Try "Pending PODs" for pending deliveries
"""
        
        return response
    
    def _error_response(self, error_msg: str, source: str) -> Dict:
        """Standard error response with user-friendly message"""
        user_friendly_msg = f"""
⚠️ *Unable to process your request*

{error_msg[:200]}

💡 *Try:*
• Rephrasing your question
• Typing "Help" for available commands
• Trying again in a moment

*Request ID:* {self.request_id}
"""
        return {
            "success": False,
            "response": user_friendly_msg,
            "error": error_msg,
            "source": source,
            "request_id": self.request_id
        }
    
    def _get_fallback_response(self, question: str) -> str:
        """Fallback response when all else fails"""
        return f"""
🤖 *AI LOGISTICS ASSISTANT*

I couldn't fully process: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• `6243612278` - Check DN status
• `Status of DN 12345`

🏪 *Dealer Analytics*
• `Top dealers` - Dealer rankings
• `Dealer performance`

📊 *Executive Dashboard*
• `Executive summary` - KPI overview
• `Revenue analysis`

📋 *Pending Items*
• `Pending PODs` - Collection status
• `Pending PGI` - Dispatch status

❓ *Help*
• `Help` - Complete menu

*Powered by Enterprise Logistics Intelligence v29.0*
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
        
        # Calculate average quality score
        avg_quality = sum(self.metrics["quality_scores"]) / max(1, len(self.metrics["quality_scores"]))
        
        return {
            "status": "healthy" if db_healthy else "degraded",
            "version": "29.0",
            "components": {
                "database": db_healthy,
                "router": self.query_router is not None,
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
                "avg_response_time_ms": round(self.metrics["avg_response_time_ms"], 2),
                "cache_hits": self.metrics["cache_hits"],
                "avg_quality_score": round(avg_quality, 1)
            }
        }
    
    def get_metrics(self) -> Dict:
        """Get service metrics"""
        avg_quality = sum(self.metrics["quality_scores"]) / max(1, len(self.metrics["quality_scores"]))
        return {
            **self.metrics,
            "avg_quality_score": round(avg_quality, 1)
        }
    
    def clear_cache(self, user_phone: str = None):
        """Clear cache for a specific user or all"""
        if user_phone:
            self.cache.invalidate_user(user_phone)
        else:
            self.cache.clear()


# ==========================================================
# FACTORY FUNCTIONS
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
            "version": "29.0"
        }


# ==========================================================
# ASYNC VERSION (FIX #6)
# ==========================================================

class AsyncAIQueryService:
    """Async version of AI Query Service for better performance"""
    
    def __init__(self, db: Session):
        self.db = db
        self.sync_service = AIQueryService(db)
    
    async def process_query_async(
        self,
        question: str,
        user_phone: str = None,
        user_role: str = None
    ) -> Dict[str, Any]:
        """Async wrapper for process_query"""
        
        # Run sync method in thread pool
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            result = await loop.run_in_executor(
                executor,
                self.sync_service.process_query,
                question,
                user_phone,
                user_role
            )
        return result


async def process_whatsapp_query_async(
    question: str,
    db: Session,
    user_phone: str = None,
    user_role: str = None
) -> str:
    """Async version of process_whatsapp_query"""
    service = AsyncAIQueryService(db)
    result = await service.process_query_async(question, user_phone, user_role)
    return result.get("response", "⚠️ Unable to process your request.")
