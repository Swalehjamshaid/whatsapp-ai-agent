# ==========================================================
# FILE: app/routes/webhook.py (PRODUCTION READY v11.1)
# ==========================================================
# FULLY ALIGNED WITH GROQ AI INTEGRATION
# - FIXED: Invalid error response construction
# - FIXED: Thread local memory leak (moved to ContextVar)
# - FIXED: Memory-based cache (Redis-ready)
# - FIXED: Dangerous SQL keyword blocking (word boundaries)
# - FIXED: AI response crash on None
# - FIXED: Import on every request (moved to top)
# - FIXED: WhatsApp flood risk (max_parts limit)
# - FIXED: Cache growth (auto-cleanup)
# ==========================================================

import json
import time
import re
import uuid
import traceback
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from collections import deque
from contextvars import ContextVar

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from loguru import logger

from app.config import config
from app.database import get_db

# ==========================================================
# FIXED: Import at top (Issue #6)
# ==========================================================

try:
    from app.services.ai_query_service import process_whatsapp_query
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ AI Query Service loaded at startup")
except ImportError as e:
    AI_SERVICE_AVAILABLE = False
    logger.error(f"❌ AI Query Service import failed at startup: {e}")
except Exception as e:
    AI_SERVICE_AVAILABLE = False
    logger.exception(f"❌ AI Query Service initialization failed: {e}")

# Try to import Redis for production cache
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available, using memory cache")

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# FIXED: ContextVar instead of thread local (Issue #2)
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

def get_current_context() -> Optional[RequestContext]:
    """Get current request context"""
    ctx = _request_context.get()
    if ctx and isinstance(ctx, dict):
        return ctx.get("context")
    return None

def set_current_context(context: RequestContext):
    """Set current request context"""
    _request_context.set({"context": context})

def clear_current_context():
    """Clear current request context"""
    _request_context.set(None)


# ==========================================================
# FIXED: Redis Cache (Issue #3)
# ==========================================================

class CacheService:
    """Cache service with Redis fallback to memory"""
    
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
        
        if REDIS_AVAILABLE and hasattr(config, 'REDIS_URL') and config.REDIS_URL:
            try:
                self.redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
                self.redis_client.ping()
                self.redis_available = True
                logger.info("✅ Redis cache enabled")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}")
    
    def get(self, key: str) -> Optional[str]:
        if self.redis_available and self.redis_client:
            try:
                return self.redis_client.get(key)
            except Exception as e:
                logger.error(f"Redis get error: {e}")
        
        if key in self.memory_cache:
            cached_data = self.memory_cache[key]
            cache_age = (datetime.utcnow() - cached_data["timestamp"]).total_seconds()
            ttl = self.cache_ttl.get(cached_data.get("type", "general"), 300)
            if cache_age < ttl:
                return cached_data["response"]
            else:
                del self.memory_cache[key]
        return None
    
    def set(self, key: str, value: str, cache_type: str = "general"):
        ttl = self.cache_ttl.get(cache_type, 300)
        
        if self.redis_available and self.redis_client:
            try:
                self.redis_client.setex(key, ttl, value)
                return
            except Exception as e:
                logger.error(f"Redis set error: {e}")
        
        self.memory_cache[key] = {
            "response": value,
            "timestamp": datetime.utcnow(),
            "type": cache_type
        }
        self._cleanup_memory_cache()
    
    def _cleanup_memory_cache(self):
        now = datetime.utcnow()
        expired_keys = []
        for key, data in self.memory_cache.items():
            cache_age = (now - data["timestamp"]).total_seconds()
            ttl = self.cache_ttl.get(data.get("type", "general"), 300)
            if cache_age >= ttl:
                expired_keys.append(key)
        for key in expired_keys:
            del self.memory_cache[key]
    
    def get_cache_key(self, message: str) -> str:
        normalized = message.lower().strip()
        normalized = re.sub(r'\s+', ' ', normalized)
        
        if normalized.isdigit() and len(normalized) >= 10:
            return f"dn:{normalized}"
        elif any(word in normalized for word in ["top dealer", "dealer ranking"]):
            return f"dealer:{normalized}"
        elif "executive" in normalized or "ceo" in normalized:
            return f"executive:{normalized}"
        return normalized
    
    def get_stats(self) -> Dict:
        return {
            "redis_available": self.redis_available,
            "memory_cache_size": len(self.memory_cache),
            "cache_ttl": self.cache_ttl
        }

cache_service = CacheService()


# ==========================================================
# FIXED: Rate Limiting with Redis support
# ==========================================================

class RateLimiter:
    def __init__(self):
        self.redis_client = None
        self.redis_available = False
        self.memory_limits = {}
        self.max_requests = 20
        self.window_seconds = 60
    
        if REDIS_AVAILABLE and hasattr(config, 'REDIS_URL') and config.REDIS_URL:
            try:
                self.redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
                self.redis_client.ping()
                self.redis_available = True
                logger.info("✅ Redis rate limiter enabled")
            except Exception as e:
                logger.warning(f"Redis rate limiter failed: {e}")
    
    def check(self, phone_number: str) -> Tuple[bool, int]:
        current_time = time.time()
        
        if self.redis_available and self.redis_client:
            try:
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
            except Exception as e:
                logger.error(f"Redis rate limit error: {e}")
        
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

rate_limiter = RateLimiter()


# ==========================================================
# FIXED: Duplicate Detection with Redis
# ==========================================================

class DuplicateDetector:
    def __init__(self):
        self.redis_client = None
        self.redis_available = False
        self.memory_messages: Dict[str, deque] = {}
        self.expiry_seconds = 3600
    
        if REDIS_AVAILABLE and hasattr(config, 'REDIS_URL') and config.REDIS_URL:
            try:
                self.redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
                self.redis_client.ping()
                self.redis_available = True
                logger.info("✅ Redis duplicate detection enabled")
            except Exception as e:
                logger.warning(f"Redis duplicate detection failed: {e}")
    
    def is_duplicate(self, phone_number: str, message_id: str) -> bool:
        if not message_id:
            return False
        
        if self.redis_available and self.redis_client:
            try:
                key = f"msg:{phone_number}:{message_id}"
                exists = self.redis_client.exists(key)
                if not exists:
                    self.redis_client.setex(key, self.expiry_seconds, "1")
                    return False
                return True
            except Exception as e:
                logger.error(f"Redis duplicate check error: {e}")
        
        if phone_number not in self.memory_messages:
            self.memory_messages[phone_number] = deque(maxlen=100)
        
        now = datetime.now()
        valid_messages = []
        for stored_id, timestamp in self.memory_messages[phone_number]:
            if (now - timestamp).total_seconds() < self.expiry_seconds:
                valid_messages.append((stored_id, timestamp))
            if stored_id == message_id:
                return True
        
        self.memory_messages[phone_number] = deque(valid_messages, maxlen=100)
        self.memory_messages[phone_number].append((message_id, now))
        return False

duplicate_detector = DuplicateDetector()


# ==========================================================
# Metrics Collector
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
        self.dealer_failures = 0
        self.db_failures = 0
        self.rate_limits = 0
    
    def record_request(self, success: bool = True):
        self.total_requests += 1
        if success:
            self.successful_requests += 1
    
    def record_ai_error(self):
        self.ai_errors += 1
    
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
            "whatsapp_errors": self.whatsapp_errors,
            "avg_response_time_ms": round(avg_response, 2),
            "cache_hit_rate": round(cache_hit_rate, 1),
            "dn_failures": self.dn_failures,
            "dealer_failures": self.dealer_failures,
            "db_failures": self.db_failures,
            "rate_limits": self.rate_limits
        }

metrics = MetricsCollector()


# ==========================================================
# WhatsApp Reply Sender with Flood Protection
# ==========================================================

MAX_WHATSAPP_LENGTH = 3500
MAX_RESPONSE_PARTS = 5


def safe_send_reply(phone_number: str, message: str, part_num: int = 0, total_parts: int = 0) -> Dict[str, Any]:
    try:
        from app.services.whatsapp_service import send_text_message
        
        if total_parts > 1:
            message = f"({part_num}/{total_parts})\n{message}"
        
        result = send_text_message(phone_number, message)
        if not result.get("success"):
            metrics.record_whatsapp_error()
        return result
    except ImportError:
        logger.warning("WhatsApp service not available, using mock send")
        return {"success": True, "mode": "mock", "message": message[:100]}
    except Exception as e:
        logger.exception(f"WhatsApp send failed for {phone_number}")
        metrics.record_whatsapp_error()
        return {"success": False, "error": str(e)}


def split_long_response(response: str, max_parts: int = MAX_RESPONSE_PARTS) -> List[str]:
    if not response:
        return ["No response generated."]
    
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
                    parts[-1] += "\n\n... (response truncated)"
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
# Input Validation with Word Boundaries
# ==========================================================

SQL_PATTERNS = [
    r'\bSELECT\b', r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b',
    r'\bDROP\b', r'\bCREATE\b', r'\bALTER\b', r'\bEXEC\b',
    r'\bUNION\b', r'\bDECLARE\b', r'\bCAST\b'
]
SQL_REGEX = re.compile('|'.join(SQL_PATTERNS), re.IGNORECASE)


def is_safe_input(text: str) -> bool:
    if not text or len(text) > 500:
        return False
    
    if SQL_REGEX.search(text):
        if 'selection' in text.lower():
            pass
        else:
            logger.warning(f"Blocked potential SQL injection: {text[:100]}")
            return False
    return True


async def send_typing_indicator(phone_number: str):
    try:
        from app.services.whatsapp_service import send_typing_indicator as send_typing
        await send_typing(phone_number)
    except Exception as e:
        logger.exception(f"Failed to send typing indicator: {e}")


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
    logger.info(f"Expected token: {config.WHATSAPP_VERIFY_TOKEN}")
    logger.info("=" * 50)
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN and hub_challenge:
        logger.success("✅ Webhook verification successful!")
        return PlainTextResponse(content=hub_challenge)
    
    logger.error("❌ Webhook verification failed - token mismatch")
    raise HTTPException(status_code=403, detail="Verification failed")


# ==========================================================
# RECEIVE MESSAGES (POST)
# ==========================================================

@router.post("/")
async def receive_message(
    request: Request, 
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    request_id = str(uuid.uuid4())
    context = RequestContext(request_id)
    set_current_context(context)
    
    logger.info("=" * 70)
    logger.info(f"📨 [REQ:{request_id}] WEBHOOK POST RECEIVED")
    
    try:
        payload = await request.json()
        logger.debug(f"[REQ:{request_id}] Payload (first 500 chars): {json.dumps(payload, indent=2)[:500]}")
        
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        if value.get("statuses"):
            logger.debug(f"[REQ:{request_id}] Status update ignored")
            return {"success": True, "message": "Status update ignored", "request_id": request_id}
        
        messages = value.get("messages", [])
        if not messages:
            logger.debug(f"[REQ:{request_id}] No messages in payload")
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
            "results": results,
            "processing_time_ms": processing_time
        }
        
    except json.JSONDecodeError as e:
        logger.exception(f"[REQ:{request_id}] Invalid JSON payload")
        metrics.record_request(success=False)
        return {"success": False, "error": "Invalid JSON", "request_id": request_id}
        
    except Exception as e:
        logger.exception(f"[REQ:{request_id}] Webhook error")
        metrics.record_request(success=False)
        metrics.record_db_failure()
        return {"success": False, "error": str(e), "request_id": request_id}
    finally:
        clear_current_context()


async def process_single_message(
    message: Dict, 
    db: Session, 
    background_tasks: BackgroundTasks,
    request_id: str
) -> Dict:
    context = get_current_context()
    
    try:
        message_type = message.get("type", "unknown")
        phone_number = message.get("from")
        message_id = message.get("id")
        
        context.set_phone_number(phone_number)
        
        logger.info(f"[REQ:{request_id}] 📱 Phone: {phone_number}")
        logger.info(f"[REQ:{request_id}] 📝 Message ID: {message_id}")
        logger.info(f"[REQ:{request_id}] 📂 Type: {message_type}")
        
        rate_ok, wait_time = rate_limiter.check(phone_number)
        if not rate_ok:
            logger.warning(f"[REQ:{request_id}] Rate limit exceeded for {phone_number}")
            metrics.record_rate_limit()
            error_msg = f"⚠️ *Rate Limit Exceeded*\n\nPlease wait {wait_time} seconds before sending more messages."
            safe_send_reply(phone_number, error_msg)
            return {"error": "rate_limit", "wait_seconds": wait_time, "request_id": request_id}
        
        if duplicate_detector.is_duplicate(phone_number, message_id):
            logger.info(f"[REQ:{request_id}] ⏭️ Duplicate message ignored")
            return {"skipped": True, "reason": "duplicate", "request_id": request_id}
        
        if message_type != "text":
            logger.info(f"[REQ:{request_id}] ⏭️ Non-text message ignored: {message_type}")
            media_response = get_media_response(message_type)
            safe_send_reply(phone_number, media_response)
            return {"skipped": True, "reason": f"non-text ({message_type})", "request_id": request_id}
        
        customer_message = message.get("text", {}).get("body", "")
        
        if not customer_message:
            logger.warning(f"[REQ:{request_id}] Empty text message")
            return {"skipped": True, "reason": "empty message", "request_id": request_id}
        
        if not is_safe_input(customer_message):
            logger.warning(f"[REQ:{request_id}] Unsafe input blocked: {customer_message[:100]}")
            safe_send_reply(phone_number, "⚠️ *Invalid Input*\n\nYour message contains characters that cannot be processed.")
            return {"skipped": True, "reason": "unsafe_input", "request_id": request_id}
        
        logger.info(f"[REQ:{request_id}] 💬 Message: {customer_message[:200]}")
        logger.info(f"[REQ:{request_id}] 🤖 START AI PROCESSING")
        logger.info(f"[REQ:{request_id}] 📝 Question: {customer_message}")
        
        background_tasks.add_task(send_typing_indicator, phone_number)
        
        cache_key = cache_service.get_cache_key(customer_message)
        cached_response = cache_service.get(cache_key)
        
        if cached_response:
            metrics.record_cache_hit()
            logger.info(f"[REQ:{request_id}] 💾 Cache HIT for {cache_key}")
            
            response_parts = split_long_response(cached_response)
            for i, part in enumerate(response_parts, 1):
                safe_send_reply(phone_number, part, i, len(response_parts))
            
            total_time = int((time.time() - context.start_time) * 1000)
            logger.info(f"[REQ:{request_id}] ⚡ Total time: {total_time}ms (CACHED)")
            
            return {
                "processed": True,
                "cached": True,
                "phone_number": phone_number,
                "message": customer_message[:100],
                "response_length": len(cached_response),
                "processing_time_ms": total_time,
                "request_id": request_id
            }
        
        metrics.record_cache_miss()
        
        if not AI_SERVICE_AVAILABLE:
            error_msg = f"""
⚠️ *AI Service Unavailable*

The intelligence service is currently offline.

Please try again in a few minutes.

💡 Request ID: {request_id}
"""
            safe_send_reply(phone_number, error_msg)
            return {"error": "ai_service_unavailable", "request_id": request_id}
        
        context.start_layer("ai_processing")
        
        try:
            context.start_layer("ai_service")
            response = process_whatsapp_query(customer_message, db, phone_number)
            context.end_layer("ai_service")
            
            if response is None:
                raise ValueError("AI service returned None response")
            
            logger.info(f"[REQ:{request_id}] 🤖 Response length: {len(response)} chars")
            
            if response and not response.startswith("⚠️") and not response.startswith("ERROR") and not response.startswith("❌"):
                cache_type = "general"
                if customer_message.isdigit() and len(customer_message) >= 10:
                    cache_type = "dn"
                elif "executive" in customer_message.lower():
                    cache_type = "executive"
                cache_service.set(cache_key, response, cache_type)
                logger.info(f"[REQ:{request_id}] 💾 Cached response for {cache_key}")
            
            context.end_layer("ai_processing")
            
            context.start_layer("whatsapp_send")
            response_parts = split_long_response(response)
            
            for i, part in enumerate(response_parts, 1):
                send_result = safe_send_reply(phone_number, part, i, len(response_parts))
                if not send_result.get("success"):
                    logger.warning(f"[REQ:{request_id}] Failed to send part {i}")
            
            context.end_layer("whatsapp_send")
            
            layer_summary = context.get_layer_summary()
            logger.info(f"[REQ:{request_id}] 📊 Layer timings: {layer_summary}")
            
            total_time = context.get_total_time_ms()
            logger.info(f"[REQ:{request_id}] ⚡ Total time: {total_time:.2f}ms")
            
            metrics.record_request(success=True)
            
            return {
                "processed": True,
                "phone_number": phone_number,
                "message": customer_message[:100],
                "response_length": len(response),
                "send_success": True,
                "processing_time_ms": total_time,
                "layer_timing_ms": layer_summary,
                "ai_used": True,
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
            elif "db" in error_str or "database" in error_str:
                metrics.record_db_failure()
            
            if config.DEBUG:
                stack_trace = traceback.format_exc()
                error_response = f"""
❌ *ERROR DETECTED*

**Request ID:** {request_id}
**Phone:** {phone_number}
**Message:** {customer_message[:100]}

**Error Type:** {type(e).__name__}
**Error Message:** {str(e)[:200]}

**Stack Trace:**
