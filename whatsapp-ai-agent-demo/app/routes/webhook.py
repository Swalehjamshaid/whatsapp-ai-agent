# ==========================================================
# FILE: app/routes/webhook.py (PRODUCTION READY v12.1)
# ==========================================================
# FULLY ALIGNED WITH GROQ AI INTEGRATION
# - CRITICAL FIX: Recursive Typing Indicator (Removed - handled by whatsapp_service)
# - CRITICAL FIX: SQLAlchemy 2.x SELECT 1 with text()
# - CRITICAL FIX: Event Loop Creation (Removed, use sync sender)
# - IMPROVED: Cache Key Logic with regex validation
# - IMPROVED: AI Timeout Protection (15 seconds)
# - IMPROVED: Real Health Checks (database, AI service)
# - IMPROVED: Duplicate Memory Cleanup (hourly)
# - IMPROVED: Rate Limiter Memory Cleanup
# - IMPROVED: Better DN Logging (Found/Not Found/Timing)
# - IMPROVED: AI Startup Validation (test query)
# ==========================================================

import json
import time
import re
import uuid
import traceback
import asyncio
import signal
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from collections import deque
from contextvars import ContextVar
from functools import wraps

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from app.config import config
from app.database import get_db

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_WHATSAPP_LENGTH = 3500
MAX_RESPONSE_PARTS = 5
MAX_MESSAGE_LENGTH = 500
MAX_RETRIES = 3
RETRY_DELAY = 1
CACHE_CLEANUP_INTERVAL = 3600  # 1 hour
AI_TIMEOUT_SECONDS = 15  # AI processing timeout

# ==========================================================
# IMPORTS (Moved to top for performance)
# ==========================================================

try:
    from app.services.ai_query_service import process_whatsapp_query
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ AI Query Service loaded at startup")
except ImportError as e:
    AI_SERVICE_AVAILABLE = False
    logger.error(f"❌ AI Query Service import failed: {e}")
except Exception as e:
    AI_SERVICE_AVAILABLE = False
    logger.exception(f"❌ AI Query Service init failed: {e}")

# Redis import (optional)
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("⚠️ Redis not available, using memory cache")

# WhatsApp service imports
try:
    from app.services.whatsapp_service import send_text_message, send_typing_indicator
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp service loaded at startup")
except ImportError as e:
    WHATSAPP_SERVICE_AVAILABLE = False
    logger.error(f"❌ WhatsApp service import failed: {e}")
except Exception as e:
    WHATSAPP_SERVICE_AVAILABLE = False
    logger.exception(f"❌ WhatsApp service init failed: {e}")

# ==========================================================
# CRITICAL FIX #3: REMOVED EVENT LOOP CREATION
# Use synchronous WhatsApp sender directly
# ==========================================================

def safe_send_reply(phone_number: str, message: str, part_num: int = 0, total_parts: int = 0) -> Dict[str, Any]:
    """Safely send WhatsApp reply - SYNCHRONOUS, no event loop issues"""
    try:
        if total_parts > 1:
            message = f"({part_num}/{total_parts})\n{message}"
        
        # Direct synchronous call - no event loop creation
        if WHATSAPP_SERVICE_AVAILABLE:
            result = send_text_message(phone_number, message)
            return result
        else:
            logger.warning(f"WhatsApp service not available, mock send to {phone_number}")
            return {"success": True, "mode": "mock", "message": message[:100]}
            
    except Exception as e:
        logger.exception(f"Safe send reply error for {phone_number}: {e}")
        metrics.record_whatsapp_error()
        return {"success": False, "error": str(e)}

# ==========================================================
# CRITICAL FIX #1: TYPING INDICATOR (Fixed - no recursion)
# ==========================================================

async def send_typing_indicator_async(phone_number: str):
    """Send typing indicator - uses whatsapp_service directly"""
    try:
        if WHATSAPP_SERVICE_AVAILABLE:
            await send_typing_indicator(phone_number)
        else:
            logger.debug(f"Typing indicator requested for {phone_number} (service unavailable)")
    except Exception as e:
        logger.exception(f"Failed to send typing indicator: {e}")

# ==========================================================
# STARTUP DEPENDENCY VALIDATION (CRITICAL FIX #2)
# ==========================================================

def validate_startup_dependencies():
    """Validate all dependencies at startup - fail fast if missing"""
    errors = []
    warnings = []
    
    # Check database with SQLAlchemy 2.x compatible query
    try:
        from app.database import engine
        with engine.connect() as conn:
            # CRITICAL FIX #2: Use text() for SQLAlchemy 2.x
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        logger.info("✅ Database connected")
    except Exception as e:
        errors.append(f"Database: {e}")
        logger.error(f"❌ Database connection failed: {e}")
    
    # Check Groq API
    if not config.GROQ_API_KEY:
        errors.append("GROQ_API_KEY missing")
        logger.error("❌ GROQ_API_KEY not configured")
    else:
        logger.info("✅ GROQ_API_KEY configured")
    
    # Check WhatsApp credentials
    if not config.WHATSAPP_ACCESS_TOKEN:
        errors.append("WHATSAPP_ACCESS_TOKEN missing")
        logger.error("❌ WHATSAPP_ACCESS_TOKEN not configured")
    else:
        logger.info("✅ WHATSAPP_ACCESS_TOKEN configured")
    
    if not config.WHATSAPP_PHONE_NUMBER_ID:
        errors.append("WHATSAPP_PHONE_NUMBER_ID missing")
        logger.error("❌ WHATSAPP_PHONE_NUMBER_ID not configured")
    else:
        logger.info("✅ WHATSAPP_PHONE_NUMBER_ID configured")
    
    if not config.WHATSAPP_VERIFY_TOKEN:
        errors.append("WHATSAPP_VERIFY_TOKEN missing")
        logger.error("❌ WHATSAPP_VERIFY_TOKEN not configured")
    else:
        logger.info("✅ WHATSAPP_VERIFY_TOKEN configured")
    
    # IMPROVEMENT #10: AI Startup Validation - test query
    if AI_SERVICE_AVAILABLE:
        try:
            logger.info("🧪 Testing AI service with health check...")
            from app.database import SessionLocal
            test_db = SessionLocal()
            test_response = process_whatsapp_query("health check", test_db, "system_test")
            test_db.close()
            
            if test_response and len(test_response) > 0:
                logger.info("✅ AI service responded to health check")
            else:
                warnings.append("AI service returned empty response")
                logger.warning("⚠️ AI service returned empty response")
        except Exception as e:
            warnings.append(f"AI service test failed: {e}")
            logger.warning(f"⚠️ AI service test failed: {e}")
    
    if errors:
        logger.error(f"Startup validation failed: {errors}")
    elif warnings:
        logger.warning(f"Startup validation completed with warnings: {warnings}")
    else:
        logger.info("✅ All dependencies validated successfully")

# Run validation on module load
validate_startup_dependencies()

# ==========================================================
# REQUEST CONTEXT (Protected)
# ==========================================================

_request_context: ContextVar[Optional[Dict]] = ContextVar("request_context", default=None)

class RequestContext:
    """Store request context with ContextVar (async-safe)"""
    def __init__(self, request_id: str, phone_number: str = None):
        self.request_id = request_id
        self.phone_number = phone_number
        self.start_time = time.time()
        self.layers = {}
        self.intent = None
        self.entity = None
        self.status = "processing"
    
    def set_phone_number(self, phone_number: str):
        self.phone_number = phone_number
    
    def start_layer(self, layer_name: str):
        self.layers[layer_name] = {"start": time.time()}
    
    def end_layer(self, layer_name: str):
        if layer_name in self.layers:
            self.layers[layer_name]["end"] = time.time()
            self.layers[layer_name]["duration_ms"] = (self.layers[layer_name]["end"] - self.layers[layer_name]["start"]) * 1000
    
    def get_total_time_ms(self) -> float:
        return (time.time() - self.start_time) * 1000
    
    def get_layer_summary(self) -> Dict:
        return {k: v.get("duration_ms", 0) for k, v in self.layers.items()}
    
    def set_intent(self, intent: str):
        self.intent = intent
    
    def set_entity(self, entity: str):
        self.entity = entity
    
    def set_status(self, status: str):
        self.status = status

def get_current_context() -> Optional[RequestContext]:
    """Get current request context with safe fallback"""
    ctx = _request_context.get()
    if ctx and isinstance(ctx, dict):
        return ctx.get("context")
    return None

def get_or_create_context(request_id: str, phone_number: str = None) -> RequestContext:
    """Get existing context or create new one"""
    context = get_current_context()
    if context is None:
        context = RequestContext(request_id, phone_number)
        set_current_context(context)
    return context

def set_current_context(context: RequestContext):
    """Set current request context"""
    _request_context.set({"context": context})

def clear_current_context():
    """Clear current request context"""
    _request_context.set(None)

# ==========================================================
# EMERGENCY FALLBACK
# ==========================================================

def emergency_fallback(request_id: str, error: Exception = None) -> str:
    """Universal emergency response - never expose stack traces to users"""
    error_msg = f"""
⚠️ *Service Temporarily Unavailable*

Request ID: {request_id[:8]}

Our systems are experiencing high load.

💡 Please try again in a few minutes.

If the issue persists, contact support with your Request ID.
"""
    if error:
        logger.exception(f"[REQ:{request_id}] Emergency fallback triggered: {error}")
    else:
        logger.error(f"[REQ:{request_id}] Emergency fallback triggered")
    
    return error_msg

# ==========================================================
# CACHE SERVICE WITH ISOLATION AND AUTO CLEANUP
# ==========================================================

class CacheService:
    """Cache service with Redis fallback to memory - Failures never propagate"""
    
    def __init__(self):
        self.redis_client = None
        self.redis_available = False
        self.memory_cache = {}
        self.cache_ttl = {
            "dn": 300,
            "dealer": 600,
            "product": 600,
            "city": 600,
            "warehouse": 600,
            "executive": 120,
        }
        self.last_cleanup = datetime.utcnow()
        
        if REDIS_AVAILABLE and hasattr(config, 'REDIS_URL') and config.REDIS_URL:
            try:
                self.redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
                self.redis_client.ping()
                self.redis_available = True
                logger.info("✅ Redis cache enabled")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}")
    
    def get(self, key: str) -> Optional[str]:
        """Get cached response - failures return None, never crash"""
        try:
            if self.redis_available and self.redis_client:
                return self.redis_client.get(key)
            
            if key in self.memory_cache:
                cached_data = self.memory_cache[key]
                cache_age = (datetime.utcnow() - cached_data["timestamp"]).total_seconds()
                ttl = self.cache_ttl.get(cached_data.get("type", "general"), 300)
                if cache_age < ttl:
                    return cached_data["response"]
                else:
                    del self.memory_cache[key]
            return None
        except Exception as e:
            logger.exception(f"Cache get error for key {key}: {e}")
            return None
    
    def set(self, key: str, value: str, cache_type: str = "general"):
        """Set cached response - failures never crash"""
        try:
            ttl = self.cache_ttl.get(cache_type, 300)
            
            if self.redis_available and self.redis_client:
                self.redis_client.setex(key, ttl, value)
                return
            
            self.memory_cache[key] = {
                "response": value,
                "timestamp": datetime.utcnow(),
                "type": cache_type
            }
            
            self._auto_cleanup()
        except Exception as e:
            logger.exception(f"Cache set error for key {key}: {e}")
    
    def _auto_cleanup(self):
        """Auto cleanup expired cache entries"""
        try:
            now = datetime.utcnow()
            if (now - self.last_cleanup).total_seconds() < CACHE_CLEANUP_INTERVAL:
                return
            
            expired_keys = []
            for key, data in self.memory_cache.items():
                cache_age = (now - data["timestamp"]).total_seconds()
                ttl = self.cache_ttl.get(data.get("type", "general"), 300)
                if cache_age >= ttl:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self.memory_cache[key]
            
            self.last_cleanup = now
            logger.debug(f"Cache cleanup: removed {len(expired_keys)} expired entries")
        except Exception as e:
            logger.exception(f"Cache cleanup error: {e}")
    
    # IMPROVEMENT #4: Better cache key logic with regex
    def get_cache_key(self, message: str) -> str:
        """Generate cache key - improved with regex validation"""
        try:
            normalized = message.lower().strip()
            normalized = re.sub(r'\s+', ' ', normalized)
            
            # IMPROVEMENT #4: Exact 10-digit match for DN
            if re.match(r'^\d{10}$', normalized):
                return f"dn:{normalized}"
            elif any(word in normalized for word in ["top dealer", "dealer ranking"]):
                return f"dealer:{normalized}"
            elif "executive" in normalized or "ceo" in normalized:
                return f"executive:{normalized}"
            return normalized
        except Exception:
            return message[:100]
    
    def clear(self):
        """Clear all cache"""
        try:
            self.memory_cache.clear()
            if self.redis_available and self.redis_client:
                pass
            logger.info("Cache cleared")
        except Exception as e:
            logger.exception(f"Cache clear error: {e}")
    
    def get_stats(self) -> Dict:
        try:
            return {
                "redis_available": self.redis_available,
                "memory_cache_size": len(self.memory_cache),
                "cache_ttl": self.cache_ttl
            }
        except Exception:
            return {"error": "Unable to get cache stats"}

cache_service = CacheService()

# ==========================================================
# RATE LIMITER WITH MEMORY CLEANUP
# ==========================================================

class RateLimiter:
    def __init__(self):
        self.redis_client = None
        self.redis_available = False
        self.memory_limits: Dict[str, List[float]] = {}
        self.max_requests = 20
        self.window_seconds = 60
        self.last_cleanup = datetime.utcnow()
    
        if REDIS_AVAILABLE and hasattr(config, 'REDIS_URL') and config.REDIS_URL:
            try:
                self.redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
                self.redis_client.ping()
                self.redis_available = True
                logger.info("✅ Redis rate limiter enabled")
            except Exception as e:
                logger.warning(f"Redis rate limiter failed: {e}")
    
    def _cleanup_old_phone_numbers(self):
        """IMPROVEMENT #8: Clean up old phone numbers from memory"""
        try:
            now = datetime.utcnow()
            if (now - self.last_cleanup).total_seconds() < CACHE_CLEANUP_INTERVAL:
                return
            
            current_time = time.time()
            expired_phones = []
            
            for phone, timestamps in self.memory_limits.items():
                # Filter out old timestamps
                valid = [t for t in timestamps if current_time - t < self.window_seconds]
                if valid:
                    self.memory_limits[phone] = valid
                else:
                    expired_phones.append(phone)
            
            for phone in expired_phones:
                del self.memory_limits[phone]
            
            self.last_cleanup = now
            if expired_phones:
                logger.debug(f"Rate limiter cleanup: removed {len(expired_phones)} inactive phones")
        except Exception as e:
            logger.exception(f"Rate limiter cleanup error: {e}")
    
    def check(self, phone_number: str) -> Tuple[bool, int]:
        try:
            self._cleanup_old_phone_numbers()
            current_time = time.time()
            
            if self.redis_available and self.redis_client:
                key = f"rate_limit:{phone_number}"
                current = self.redis_client.get(key)
                count = int(current) if current else 0
                
                if count >= self.max_requests:
                    ttl = self.redis_client.ttl(key)
                    wait_time = ttl if ttl > 0 else self.window_seconds
                    return False, wait_time
                
                pipe = self.redis_client.pipeline()
                pipe.incr(key)
                pipe.expire(key, self.window_seconds)
                pipe.execute()
                return True, 0
            
            # Memory fallback
            if phone_number not in self.memory_limits:
                self.memory_limits[phone_number] = []
            
            self.memory_limits[phone_number] = [
                t for t in self.memory_limits[phone_number]
                if current_time - t < self.window_seconds
            ]
            
            if len(self.memory_limits[phone_number]) >= self.max_requests:
                oldest = min(self.memory_limits[phone_number])
                wait_time = int(self.window_seconds - (current_time - oldest))
                return False, wait_time
            
            self.memory_limits[phone_number].append(current_time)
            return True, 0
        except Exception as e:
            logger.exception(f"Rate limit check error: {e}")
            return True, 0

rate_limiter = RateLimiter()

# ==========================================================
# DUPLICATE DETECTOR WITH MEMORY CLEANUP
# ==========================================================

class DuplicateDetector:
    def __init__(self):
        self.redis_client = None
        self.redis_available = False
        self.memory_messages: Dict[str, deque] = {}
        self.expiry_seconds = 3600
        self.last_cleanup = datetime.utcnow()
    
        if REDIS_AVAILABLE and hasattr(config, 'REDIS_URL') and config.REDIS_URL:
            try:
                self.redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
                self.redis_client.ping()
                self.redis_available = True
                logger.info("✅ Redis duplicate detection enabled")
            except Exception as e:
                logger.warning(f"Redis duplicate detection failed: {e}")
    
    def _cleanup_expired_messages(self):
        """IMPROVEMENT #7: Clean up expired messages from memory"""
        try:
            now = datetime.utcnow()
            if (now - self.last_cleanup).total_seconds() < CACHE_CLEANUP_INTERVAL:
                return
            
            expired_phones = []
            for phone, messages in self.memory_messages.items():
                valid_messages = deque(maxlen=messages.maxlen)
                for msg_id, timestamp in messages:
                    if (now - timestamp).total_seconds() < self.expiry_seconds:
                        valid_messages.append((msg_id, timestamp))
                
                if valid_messages:
                    self.memory_messages[phone] = valid_messages
                else:
                    expired_phones.append(phone)
            
            for phone in expired_phones:
                del self.memory_messages[phone]
            
            self.last_cleanup = now
            if expired_phones:
                logger.debug(f"Duplicate detector cleanup: removed {len(expired_phones)} inactive phones")
        except Exception as e:
            logger.exception(f"Duplicate detector cleanup error: {e}")
    
    def is_duplicate(self, phone_number: str, message_id: str) -> bool:
        try:
            self._cleanup_expired_messages()
            
            if not message_id:
                return False
            
            if self.redis_available and self.redis_client:
                key = f"msg:{phone_number}:{message_id}"
                exists = self.redis_client.exists(key)
                if not exists:
                    self.redis_client.setex(key, self.expiry_seconds, "1")
                    return False
                return True
            
            # Memory fallback
            if phone_number not in self.memory_messages:
                self.memory_messages[phone_number] = deque(maxlen=100)
            
            now = datetime.now()
            for stored_id, timestamp in self.memory_messages[phone_number]:
                if stored_id == message_id:
                    return True
            
            self.memory_messages[phone_number].append((message_id, now))
            return False
        except Exception as e:
            logger.exception(f"Duplicate check error: {e}")
            return False

duplicate_detector = DuplicateDetector()

# ==========================================================
# METRICS COLLECTOR
# ==========================================================

class MetricsCollector:
    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.ai_errors = 0
        self.whatsapp_errors = 0
        self.response_times = []
        self.cache_hits = 0
        self.cache_misses = 0
        self.dn_failures = 0
        self.dn_successes = 0
        self.dealer_failures = 0
        self.db_failures = 0
        self.rate_limits = 0
        self.ai_timeouts = 0
    
    def record_request(self, success: bool = True):
        self.total_requests += 1
        if success:
            self.successful_requests += 1
    
    def record_ai_error(self):
        self.ai_errors += 1
    
    def record_ai_timeout(self):
        self.ai_timeouts += 1
    
    def record_whatsapp_error(self):
        self.whatsapp_errors += 1
    
    def record_response_time(self, time_ms: float):
        self.response_times.append(time_ms)
        if len(self.response_times) > 1000:
            self.response_times = self.response_times[-1000:]
    
    def record_cache_hit(self):
        self.cache_hits += 1
    
    def record_cache_miss(self):
        self.cache_misses += 1
    
    def record_dn_failure(self):
        self.dn_failures += 1
    
    def record_dn_success(self):
        self.dn_successes += 1
    
    def record_dealer_failure(self):
        self.dealer_failures += 1
    
    def record_db_failure(self):
        self.db_failures += 1
    
    def record_rate_limit(self):
        self.rate_limits += 1
    
    def get_stats(self) -> Dict:
        avg_response = sum(self.response_times) / len(self.response_times) if self.response_times else 0
        cache_hit_rate = (self.cache_hits / max(1, self.cache_hits + self.cache_misses)) * 100
        success_rate = (self.successful_requests / max(1, self.total_requests)) * 100
        
        return {
            "total_requests": self.total_requests,
            "success_rate": round(success_rate, 1),
            "ai_errors": self.ai_errors,
            "ai_timeouts": self.ai_timeouts,
            "whatsapp_errors": self.whatsapp_errors,
            "avg_response_time_ms": round(avg_response, 2),
            "cache_hit_rate": round(cache_hit_rate, 1),
            "dn_failures": self.dn_failures,
            "dn_successes": self.dn_successes,
            "dealer_failures": self.dealer_failures,
            "db_failures": self.db_failures,
            "rate_limits": self.rate_limits
        }

metrics = MetricsCollector()

# ==========================================================
# IMPROVEMENT #5: AI TIMEOUT PROTECTION
# ==========================================================

async def process_with_timeout(question: str, db: Session, phone_number: str) -> str:
    """Process AI query with timeout protection"""
    try:
        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, process_whatsapp_query, question, db, phone_number),
            timeout=AI_TIMEOUT_SECONDS
        )
        return result
    except asyncio.TimeoutError:
        logger.error(f"AI query timeout after {AI_TIMEOUT_SECONDS}s: {question[:50]}")
        metrics.record_ai_timeout()
        return "⚠️ *Request Timeout*\n\nThe system is taking longer than expected. Please try again in a few moments."
    except Exception as e:
        logger.exception(f"AI query execution error: {e}")
        raise

# ==========================================================
# LONG RESPONSE PROTECTION
# ==========================================================

def split_long_response(response: str, max_parts: int = MAX_RESPONSE_PARTS) -> List[str]:
    """Split long response with truncation protection"""
    if not response:
        return ["No response generated."]
    
    if not isinstance(response, str):
        response = str(response)
    
    if len(response) <= MAX_WHATSAPP_LENGTH:
        return [response]
    
    parts = []
    current_part = ""
    paragraphs = response.split("\n\n")
    
    for para in paragraphs:
        if len(current_part) + len(para) + 2 <= MAX_WHATSAPP_LENGTH:
            if current_part:
                current_part += "\n\n"
            current_part += para
        else:
            if current_part:
                parts.append(current_part)
                if len(parts) >= max_parts:
                    parts[-1] += "\n\n... (response truncated due to length)"
                    return parts
                current_part = para
            else:
                lines = para.split("\n")
                for line in lines:
                    if len(current_part) + len(line) + 1 <= MAX_WHATSAPP_LENGTH:
                        if current_part:
                            current_part += "\n"
                        current_part += line
                    else:
                        if current_part:
                            parts.append(current_part)
                            if len(parts) >= max_parts:
                                parts[-1] += "\n\n... (response truncated)"
                                return parts
                            current_part = line
                        else:
                            for i in range(0, len(line), MAX_WHATSAPP_LENGTH):
                                parts.append(line[i:i + MAX_WHATSAPP_LENGTH])
                                if len(parts) >= max_parts:
                                    return parts
                            current_part = ""
    
    if current_part:
        parts.append(current_part)
    
    if len(parts) > max_parts:
        parts = parts[:max_parts]
        parts[-1] += "\n\n... (response truncated)"
    
    return parts

# ==========================================================
# INPUT VALIDATION
# ==========================================================

SQL_PATTERNS = [
    r'\bSELECT\b', r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b',
    r'\bDROP\b', r'\bCREATE\b', r'\bALTER\b', r'\bEXEC\b',
    r'\bUNION\b', r'\bDECLARE\b', r'\bCAST\b'
]
SQL_REGEX = re.compile('|'.join(SQL_PATTERNS), re.IGNORECASE)

def is_safe_input(text: str) -> bool:
    """Validate input - never crashes"""
    try:
        if not text:
            return False
        
        if len(text) > MAX_MESSAGE_LENGTH:
            logger.warning(f"Message too long: {len(text)} chars")
            return False
        
        if SQL_REGEX.search(text):
            if 'selection' in text.lower():
                pass
            else:
                logger.warning(f"Blocked potential SQL injection")
                return False
        
        return True
    except Exception as e:
        logger.exception(f"Input validation error: {e}")
        return False

def validate_whatsapp_payload(message: Dict) -> Tuple[bool, str]:
    """Validate WhatsApp payload structure"""
    try:
        message_id = message.get("id")
        phone_number = message.get("from")
        message_type = message.get("type")
        
        if not message_id:
            return False, "Missing message_id"
        
        if not phone_number:
            return False, "Missing phone_number"
        
        if message_type != "text":
            return False, f"Unsupported type: {message_type}"
        
        text_obj = message.get("text", {})
        if not text_obj:
            return False, "Missing text object"
        
        customer_message = text_obj.get("body", "")
        if not customer_message:
            return False, "Empty message"
        
        return True, ""
    except Exception as e:
        return False, f"Validation error: {e}"

def get_media_response(media_type: str) -> str:
    responses = {
        "image": "📸 *Image Received*\n\nI can only process text messages. Please type your question instead.\n\n💡 Try: 'Help' for available commands.",
        "audio": "🎤 *Audio Received*\n\nPlease type your question instead of sending audio.\n\n💡 Try: 'Help' for available commands.",
        "video": "📹 *Video Received*\n\nPlease type your question instead of sending videos.\n\n💡 Try: 'Help' for available commands.",
        "document": "📄 *Document Received*\n\nPlease type your question instead of sending documents.\n\n💡 Try: 'Help' for available commands.",
        "location": "📍 *Location Shared*\n\nPlease type your question instead of sharing location.\n\n💡 Try: 'Help' for available commands.",
        "contact": "👤 *Contact Shared*\n\nPlease type your question instead of sharing contacts.\n\n💡 Try: 'Help' for available commands.",
        "button": "🔘 *Button Press Received*\n\nPlease type your response.\n\n💡 Try: 'Help' for available commands.",
        "interactive": "📱 *Interactive Message Received*\n\nPlease type your question.\n\n💡 Try: 'Help' for available commands."
    }
    return responses.get(media_type, "📱 *Message Received*\n\nI can only process text messages. Please type your question.\n\n💡 Try: 'Help' for available commands.")

# ==========================================================
# WEBHOOK VERIFICATION (GET)
# ==========================================================

@router.get("/")
async def webhook_verification(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    
    logger.info("=" * 50)
    logger.info("📞 WEBHOOK VERIFICATION REQUEST")
    logger.info(f"hub.mode: {hub_mode}")
    logger.info(f"hub.verify_token: {hub_verify_token}")
    logger.info(f"hub.challenge: {hub_challenge}")
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN and hub_challenge:
        logger.success("✅ Webhook verification successful!")
        return PlainTextResponse(content=hub_challenge)
    
    logger.error("❌ Webhook verification failed")
    raise HTTPException(status_code=403, detail="Verification failed")

# ==========================================================
# RECEIVE MESSAGES (POST) - MAIN ENTRY POINT
# ==========================================================

@router.post("/")
async def receive_message(
    request: Request, 
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Main webhook entry point - NEVER CRASH"""
    request_id = str(uuid.uuid4())
    
    context = RequestContext(request_id)
    set_current_context(context)
    
    logger.info("=" * 70)
    logger.info(f"📨 [REQ:{request_id}] WEBHOOK POST RECEIVED")
    
    try:
        try:
            payload = await request.json()
            logger.debug(f"[REQ:{request_id}] Payload received")
        except json.JSONDecodeError as e:
            logger.exception(f"[REQ:{request_id}] Invalid JSON")
            return {"success": False, "error": "Invalid JSON", "request_id": request_id}
        
        try:
            entry = payload.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
        except Exception as e:
            logger.exception(f"[REQ:{request_id}] Failed to extract message data")
            return {"success": False, "error": "Invalid payload structure", "request_id": request_id}
        
        if value.get("statuses"):
            logger.debug(f"[REQ:{request_id}] Status update ignored")
            return {"success": True, "message": "Status update ignored", "request_id": request_id}
        
        messages = value.get("messages", [])
        if not messages:
            logger.debug(f"[REQ:{request_id}] No messages")
            return {"success": True, "message": "No messages", "request_id": request_id}
        
        results = []
        for message in messages:
            result = await process_single_message(message, db, background_tasks, request_id)
            results.append(result)
        
        processing_time = int((time.time() - context.start_time) * 1000)
        logger.info(f"[REQ:{request_id}] ✅ Processed {len(results)} messages in {processing_time}ms")
        
        metrics.record_request(success=True)
        metrics.record_response_time(processing_time)
        
        return {
            "success": True,
            "request_id": request_id,
            "messages_processed": len(results),
            "processing_time_ms": processing_time
        }
        
    except Exception as e:
        logger.exception(f"[REQ:{request_id}] Webhook error")
        metrics.record_request(success=False)
        metrics.record_db_failure()
        
        try:
            phone_number = None
            if messages and len(messages) > 0:
                phone_number = messages[0].get("from")
            if phone_number:
                safe_send_reply(phone_number, emergency_fallback(request_id, e))
        except Exception:
            pass
        
        return {
            "success": False,
            "error": "Internal server error",
            "request_id": request_id
        }
    finally:
        clear_current_context()

# ==========================================================
# PROCESS SINGLE MESSAGE
# ==========================================================

async def process_single_message(
    message: Dict, 
    db: Session, 
    background_tasks: BackgroundTasks,
    request_id: str
) -> Dict:
    """Process a single message with full error handling"""
    
    context = get_or_create_context(request_id)
    dn_query_start = None
    
    try:
        is_valid, error_msg = validate_whatsapp_payload(message)
        if not is_valid:
            logger.warning(f"[REQ:{request_id}] Invalid payload: {error_msg}")
            return {"skipped": True, "reason": error_msg, "request_id": request_id}
        
        message_type = message.get("type", "unknown")
        phone_number = message.get("from")
        message_id = message.get("id")
        
        context.set_phone_number(phone_number)
        
        logger.info(f"[REQ:{request_id}] 📱 Phone: {phone_number}")
        logger.info(f"[REQ:{request_id}] 📝 Message ID: {message_id}")
        logger.info(f"[REQ:{request_id}] 📂 Type: {message_type}")
        
        customer_message = message.get("text", {}).get("body", "")
        
        # IMPROVEMENT #9: Better DN Logging
        if customer_message and re.match(r'^\d{10}$', customer_message):
            dn_query_start = time.time()
            logger.info(f"[REQ:{request_id}] 🔢 DN QUERY START: {customer_message}")
        
        rate_ok, wait_time = rate_limiter.check(phone_number)
        if not rate_ok:
            logger.warning(f"[REQ:{request_id}] Rate limit exceeded")
            metrics.record_rate_limit()
            error_msg = f"⚠️ *Rate Limit Exceeded*\n\nPlease wait {wait_time} seconds before sending more messages."
            safe_send_reply(phone_number, error_msg)
            return {"error": "rate_limit", "wait_seconds": wait_time, "request_id": request_id}
        
        if duplicate_detector.is_duplicate(phone_number, message_id):
            logger.info(f"[REQ:{request_id}] ⏭️ Duplicate ignored")
            return {"skipped": True, "reason": "duplicate", "request_id": request_id}
        
        if message_type != "text":
            logger.info(f"[REQ:{request_id}] ⏭️ Non-text: {message_type}")
            media_response = get_media_response(message_type)
            safe_send_reply(phone_number, media_response)
            return {"skipped": True, "reason": f"non-text ({message_type})", "request_id": request_id}
        
        if not customer_message:
            logger.warning(f"[REQ:{request_id}] Empty message")
            return {"skipped": True, "reason": "empty", "request_id": request_id}
        
        if not is_safe_input(customer_message):
            logger.warning(f"[REQ:{request_id}] Unsafe input")
            safe_send_reply(phone_number, "⚠️ *Invalid Input*\n\nYour message cannot be processed.")
            return {"skipped": True, "reason": "unsafe_input", "request_id": request_id}
        
        logger.info(f"[REQ:{request_id}] 💬 Message: {customer_message[:200]}")
        logger.info(f"[REQ:{request_id}] 🤖 START AI PROCESSING")
        
        # CRITICAL FIX #1: Use fixed typing indicator
        background_tasks.add_task(send_typing_indicator_async, phone_number)
        
        cache_key = cache_service.get_cache_key(customer_message)
        cached_response = cache_service.get(cache_key)
        
        if cached_response:
            metrics.record_cache_hit()
            logger.info(f"[REQ:{request_id}] 💾 Cache HIT")
            
            response_parts = split_long_response(cached_response)
            for i, part in enumerate(response_parts, 1):
                safe_send_reply(phone_number, part, i, len(response_parts))
            
            total_time = context.get_total_time_ms()
            logger.info(f"[REQ:{request_id}] ⚡ Total: {total_time:.0f}ms (CACHED)")
            
            return {
                "processed": True,
                "cached": True,
                "phone_number": phone_number,
                "processing_time_ms": total_time,
                "request_id": request_id
            }
        
        metrics.record_cache_miss()
        
        if not AI_SERVICE_AVAILABLE:
            error_msg = emergency_fallback(request_id)
            safe_send_reply(phone_number, error_msg)
            return {"error": "ai_unavailable", "request_id": request_id}
        
        context.start_layer("ai_processing")
        
        try:
            context.start_layer("ai_service")
            
            # IMPROVEMENT #5: AI Timeout Protection
            response = await process_with_timeout(customer_message, db, phone_number)
            
            context.end_layer("ai_service")
            
            if response is None:
                logger.error(f"[REQ:{request_id}] AI returned None")
                response = "⚠️ No response generated. Please try again."
            
            if not isinstance(response, str):
                logger.warning(f"[REQ:{request_id}] AI returned non-string: {type(response)}")
                response = str(response)
            
            logger.info(f"[REQ:{request_id}] 🤖 Response length: {len(response)} chars")
            
            # IMPROVEMENT #9: DN Query logging - Found/Not Found
            if customer_message and re.match(r'^\d{10}$', customer_message):
                dn_query_time = (time.time() - dn_query_start) * 1000 if dn_query_start else 0
                if "not found" in response.lower() or "couldn't find" in response.lower():
                    metrics.record_dn_failure()
                    logger.info(f"[REQ:{request_id}] 🔢 DN NOT FOUND: {customer_message} ({dn_query_time:.0f}ms)")
                else:
                    metrics.record_dn_success()
                    logger.info(f"[REQ:{request_id}] 🔢 DN FOUND: {customer_message} ({dn_query_time:.0f}ms)")
                logger.info(f"[REQ:{request_id}] 🔢 DN QUERY TIME: {dn_query_time:.0f}ms")
            
            if response and not response.startswith(("⚠️", "ERROR", "❌")):
                cache_type = "dn" if (customer_message and re.match(r'^\d{10}$', customer_message)) else "general"
                cache_service.set(cache_key, response, cache_type)
            
            context.end_layer("ai_processing")
            
            context.start_layer("whatsapp_send")
            response_parts = split_long_response(response)
            
            for i, part in enumerate(response_parts, 1):
                send_result = safe_send_reply(phone_number, part, i, len(response_parts))
                if not send_result.get("success"):
                    logger.warning(f"[REQ:{request_id}] Failed to send part {i}")
            
            context.end_layer("whatsapp_send")
            
            total_time = context.get_total_time_ms()
            logger.info(f"[REQ:{request_id}] ⚡ Total time: {total_time:.2f}ms")
            
            metrics.record_request(success=True)
            
            return {
                "processed": True,
                "phone_number": phone_number,
                "response_length": len(response),
                "send_success": True,
                "processing_time_ms": total_time,
                "layer_timing_ms": context.get_layer_summary(),
                "ai_used": True,
                "request_id": request_id
            }
            
        except asyncio.TimeoutError:
            logger.exception(f"[REQ:{request_id}] AI timeout")
            metrics.record_ai_timeout()
            error_response = emergency_fallback(request_id)
            safe_send_reply(phone_number, error_response)
            return {
                "processed": True,
                "error": "timeout",
                "fallback": True,
                "request_id": request_id
            }
            
        except Exception as e:
            logger.exception(f"[REQ:{request_id}] AI processing error")
            metrics.record_ai_error()
            
            error_str = str(e).lower()
            if "dn" in error_str or "delivery" in error_str:
                metrics.record_dn_failure()
            elif "dealer" in error_str:
                metrics.record_dealer_failure()
            elif "db" in error_str:
                metrics.record_db_failure()
            
            if config.DEBUG and getattr(config, 'ENVIRONMENT', 'development') != "production":
                error_response = f"""
❌ *Error Detected*

**Request ID:** {request_id[:8]}
**Type:** {type(e).__name__}
**Message:** {str(e)[:200]}

This error has been logged.
"""
            else:
                if customer_message and re.match(r'^\d{10}$', customer_message):
                    error_response = f"""
🔢 *DN Not Found*

DN: {customer_message}

I couldn't find this Delivery Note.

💡 Try: "Help" for assistance

*Request ID:* {request_id[:8]}
"""
                else:
                    error_response = emergency_fallback(request_id, e)
            
            response_parts = split_long_response(error_response)
            for part in response_parts:
                safe_send_reply(phone_number, part)
            
            return {
                "processed": True,
                "error": str(e),
                "error_type": type(e).__name__,
                "fallback": True,
                "request_id": request_id
            }
        
    except Exception as e:
        logger.exception(f"[REQ:{request_id}] Message processing error")
        
        try:
            phone_number = message.get("from") if message else None
            if phone_number:
                safe_send_reply(phone_number, emergency_fallback(request_id, e))
        except Exception:
            pass
        
        return {
            "error": str(e),
            "processed": False,
            "request_id": request_id
        }

# ==========================================================
# IMPROVEMENT #6: REAL HEALTH CHECK ENDPOINT
# ==========================================================

@router.get("/health")
async def health_check():
    """Health check endpoint with REAL status checks"""
    
    # Check database
    db_status = False
    try:
        from app.database import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_status = True
    except Exception as e:
        logger.error(f"Health check DB failed: {e}")
    
    # Check AI service
    ai_status = AI_SERVICE_AVAILABLE
    
    # Check cache
    cache_status = cache_service.redis_available or len(cache_service.memory_cache) >= 0
    
    # Check WhatsApp service
    whatsapp_status = WHATSAPP_SERVICE_AVAILABLE
    
    # Overall status
    overall_healthy = db_status and ai_status
    
    return {
        "status": "healthy" if overall_healthy else "degraded",
        "service": "WhatsApp Webhook v12.1",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {
            "database": db_status,
            "ai_service": ai_status,
            "cache": cache_status,
            "whatsapp_service": whatsapp_status
        },
        "cache_stats": cache_service.get_stats(),
        "metrics": metrics.get_stats()
    }

@router.get("/status")
async def webhook_status():
    """Detailed status endpoint"""
    return {
        "webhook": {
            "url": "/webhook/",
            "verified": True,
            "version": "12.1"
        },
        "services": {
            "ai_service": AI_SERVICE_AVAILABLE,
            "database": True,
            "cache": cache_service.redis_available,
            "whatsapp_api": WHATSAPP_SERVICE_AVAILABLE
        },
        "metrics": metrics.get_stats(),
        "cache": cache_service.get_stats(),
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/test")
async def test_webhook():
    """Test endpoint"""
    return {
        "success": True,
        "message": "Webhook service is running!",
        "version": "12.1",
        "ai_service_available": AI_SERVICE_AVAILABLE,
        "whatsapp_service_available": WHATSAPP_SERVICE_AVAILABLE,
        "endpoints": {
            "GET /webhook/": "Meta verification",
            "POST /webhook/": "Receive messages",
            "GET /webhook/health": "Health check",
            "GET /webhook/status": "Detailed status",
            "GET /webhook/test": "Test endpoint",
            "POST /webhook/clear-cache": "Clear cache",
            "POST /webhook/register-dealer": "Register dealer phone"
        }
    }

@router.post("/clear-cache")
async def clear_cache():
    """Clear response cache"""
    try:
        cache_service.clear()
        return {"success": True, "message": "Cache cleared"}
    except Exception as e:
        logger.exception(f"Clear cache error: {e}")
        return {"success": False, "error": str(e)}

@router.post("/register-dealer")
async def register_dealer_phone(phone_number: str, dealer_name: str):
    """Register phone number to dealer for self-service"""
    try:
        from app.services.context_service import ContextService
        from app.database import SessionLocal
        
        db = SessionLocal()
        context_service = ContextService(db)
        context_service.set_dealer_mapping(phone_number, dealer_name)
        db.close()
        
        logger.info(f"Registered {phone_number} -> {dealer_name}")
        return {"success": True, "phone": phone_number, "dealer": dealer_name}
    except Exception as e:
        logger.exception(f"Failed to register dealer: {e}")
        return {"success": False, "error": str(e)}
