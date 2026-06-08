# ==========================================================
# FILE: app/routes/webhook.py (PRODUCTION READY v11.0)
# ==========================================================
# FULLY ALIGNED WITH GROQ AI INTEGRATION
# - PHASE 1: Critical Fixes (Error Masking, Stack Traces, Correlation IDs)
# - PHASE 2: AI Observability (Full Flow Logging, Timing)
# - PHASE 3: WhatsApp Performance (Async, Cache, Typing Indicator)
# - PHASE 4: WhatsApp UX (Response Splitting, Rich Formatting)
# - PHASE 5: Security (Rate Limiting, Input Validation)
# - PHASE 6: Dealer Self-Service (Auto Identification)
# - PHASE 7: Production Monitoring (Health Metrics, Error Dashboard)
# ==========================================================

import json
import time
import re
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from collections import deque
from functools import wraps

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from loguru import logger

from app.config import config
from app.database import get_db

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# PHASE 1: REQUEST CORRELATION ID
# ==========================================================

class RequestContext:
    """Store request context for correlation"""
    def __init__(self):
        self.request_id = None
        self.phone_number = None
        self.start_time = None
        self.layers = {}
    
    def set_request_id(self, request_id: str):
        self.request_id = request_id
    
    def set_phone_number(self, phone_number: str):
        self.phone_number = phone_number
    
    def start_layer(self, layer_name: str):
        self.layers[layer_name] = {"start": time.time()}
    
    def end_layer(self, layer_name: str):
        if layer_name in self.layers:
            self.layers[layer_name]["end"] = time.time()
            self.layers[layer_name]["duration_ms"] = (self.layers[layer_name]["end"] - self.layers[layer_name]["start"]) * 1000
    
    def get_total_time_ms(self) -> float:
        if self.start_time:
            return (time.time() - self.start_time) * 1000
        return 0
    
    def get_layer_summary(self) -> Dict:
        return {k: v.get("duration_ms", 0) for k, v in self.layers.items()}

# Thread-local storage for request context
_request_context = {}

def get_current_context() -> Optional[RequestContext]:
    """Get current request context"""
    import threading
    return _request_context.get(threading.current_thread())

def set_current_context(context: RequestContext):
    """Set current request context"""
    import threading
    _request_context[threading.current_thread()] = context

def clear_current_context():
    """Clear current request context"""
    import threading
    if threading.current_thread() in _request_context:
        del _request_context[threading.current_thread()]


# ==========================================================
# PHASE 5: RATE LIMITING
# ==========================================================

RATE_LIMIT_CACHE: Dict[str, List[float]] = {}
RATE_LIMIT_REQUESTS = 20  # Requests per minute
RATE_LIMIT_WINDOW = 60  # Seconds


def check_rate_limit(phone_number: str) -> Tuple[bool, int]:
    """Check if phone number has exceeded rate limit"""
    current_time = time.time()
    
    if phone_number not in RATE_LIMIT_CACHE:
        RATE_LIMIT_CACHE[phone_number] = []
    
    # Clean old entries
    RATE_LIMIT_CACHE[phone_number] = [
        t for t in RATE_LIMIT_CACHE[phone_number] 
        if current_time - t < RATE_LIMIT_WINDOW
    ]
    
    # Check limit
    if len(RATE_LIMIT_CACHE[phone_number]) >= RATE_LIMIT_REQUESTS:
        oldest = min(RATE_LIMIT_CACHE[phone_number])
        wait_time = int(RATE_LIMIT_WINDOW - (current_time - oldest))
        return False, wait_time
    
    # Add current request
    RATE_LIMIT_CACHE[phone_number].append(current_time)
    return True, 0


# ==========================================================
# PHASE 5: INPUT VALIDATION
# ==========================================================

def is_safe_input(text: str) -> bool:
    """Validate input for security"""
    if not text or len(text) > 500:
        return False
    
    # Block SQL keywords
    sql_keywords = [
        "SELECT", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", 
        "ALTER", "EXEC", "UNION", "DECLARE", "CAST"
    ]
    
    text_upper = text.upper()
    for keyword in sql_keywords:
        if keyword in text_upper:
            logger.warning(f"Blocked SQL keyword in input: {keyword}")
            return False
    
    return True


# ==========================================================
# PHASE 1: MESSAGE CACHE (Prevent Duplicates)
# ==========================================================

RECENT_MESSAGES: Dict[str, deque] = {}
MAX_MESSAGE_CACHE = 100
MESSAGE_EXPIRY_SECONDS = 3600  # 1 hour


def is_duplicate_message(phone_number: str, message_id: str) -> bool:
    """Check if message has been processed recently"""
    if not message_id:
        return False
    
    if phone_number not in RECENT_MESSAGES:
        RECENT_MESSAGES[phone_number] = deque(maxlen=MAX_MESSAGE_CACHE)
    
    # Clean expired messages
    now = datetime.now()
    valid_messages = []
    for stored_id, timestamp in RECENT_MESSAGES[phone_number]:
        if (now - timestamp).total_seconds() < MESSAGE_EXPIRY_SECONDS:
            valid_messages.append((stored_id, timestamp))
        if stored_id == message_id:
            return True
    
    RECENT_MESSAGES[phone_number] = deque(valid_messages, maxlen=MAX_MESSAGE_CACHE)
    RECENT_MESSAGES[phone_number].append((message_id, now))
    return False


# ==========================================================
# PHASE 3: RESPONSE CACHE
# ==========================================================

RESPONSE_CACHE: Dict[str, Dict] = {}
CACHE_TTL = {
    "dn": 300,           # 5 minutes
    "dealer": 600,       # 10 minutes
    "product": 600,      # 10 minutes
    "city": 600,         # 10 minutes
    "warehouse": 600,    # 10 minutes
    "executive": 120,    # 2 minutes
}


def get_cached_response(cache_key: str, cache_type: str) -> Optional[str]:
    """Get cached response"""
    if cache_key in RESPONSE_CACHE:
        cached_data = RESPONSE_CACHE[cache_key]
        cache_age = (datetime.utcnow() - cached_data["timestamp"]).total_seconds()
        ttl = CACHE_TTL.get(cache_type, 300)
        if cache_age < ttl:
            logger.debug(f"Cache HIT for {cache_key}")
            return cached_data["response"]
        else:
            del RESPONSE_CACHE[cache_key]
    return None


def set_cached_response(cache_key: str, cache_type: str, response: str):
    """Cache response"""
    RESPONSE_CACHE[cache_key] = {
        "response": response,
        "timestamp": datetime.utcnow(),
        "type": cache_type
    }
    logger.debug(f"Cached {cache_key} (type: {cache_type})")


def get_cache_key(message: str, intent: str = None) -> str:
    """Generate cache key from message"""
    normalized = message.lower().strip()
    if intent:
        return f"{intent}:{normalized}"
    return normalized


# ==========================================================
# PHASE 4: RESPONSE SPLITTING
# ==========================================================

MAX_WHATSAPP_LENGTH = 3500


def split_long_response(response: str) -> List[str]:
    """Split long response into multiple WhatsApp messages"""
    if len(response) <= MAX_WHATSAPP_LENGTH:
        return [response]
    
    parts = []
    current_part = ""
    
    # Split by double newlines first (paragraphs)
    paragraphs = response.split("\n\n")
    
    for para in paragraphs:
        if len(current_part) + len(para) + 2 <= MAX_WHATSAPP_LENGTH:
            if current_part:
                current_part += "\n\n"
            current_part += para
        else:
            if current_part:
                parts.append(current_part)
                current_part = para
            else:
                # Single paragraph too long, split by lines
                lines = para.split("\n")
                for line in lines:
                    if len(current_part) + len(line) + 1 <= MAX_WHATSAPP_LENGTH:
                        if current_part:
                            current_part += "\n"
                        current_part += line
                    else:
                        if current_part:
                            parts.append(current_part)
                            current_part = line
                        else:
                            # Single line too long, force split
                            for i in range(0, len(line), MAX_WHATSAPP_LENGTH):
                                parts.append(line[i:i + MAX_WHATSAPP_LENGTH])
                            current_part = ""
    
    if current_part:
        parts.append(current_part)
    
    # Add part indicators
    if len(parts) > 1:
        total = len(parts)
        parts = [f"({i+1}/{total})\n{part}" for i, part in enumerate(parts)]
    
    return parts


# ==========================================================
# PHASE 4: TYPING INDICATOR
# ==========================================================

async def send_typing_indicator(phone_number: str):
    """Send typing indicator to WhatsApp"""
    try:
        from app.services.whatsapp_service import send_typing_indicator as send_typing
        await send_typing(phone_number)
    except Exception as e:
        logger.exception(f"Failed to send typing indicator: {e}")


# ==========================================================
# PHASE 6: DEALER SELF-SERVICE
# ==========================================================

# Dealer phone mapping (load from database in production)
DEALER_PHONE_MAP: Dict[str, str] = {}


def get_dealer_from_phone(phone_number: str) -> Optional[str]:
    """Get dealer name from phone number"""
    return DEALER_PHONE_MAP.get(phone_number)


def register_dealer_phone(phone_number: str, dealer_name: str):
    """Register phone number to dealer mapping"""
    DEALER_PHONE_MAP[phone_number] = dealer_name
    logger.info(f"Registered phone {phone_number} -> dealer {dealer_name}")


# ==========================================================
# PHASE 7: METRICS COLLECTION
# ==========================================================

class MetricsCollector:
    """Collect and track metrics"""
    
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
            "db_failures": self.db_failures
        }

metrics = MetricsCollector()


# ==========================================================
# WHATSAPP REPLY SENDER
# ==========================================================

def safe_send_reply(phone_number: str, message: str) -> Dict[str, Any]:
    """Safely send WhatsApp reply with metrics"""
    try:
        from app.services.whatsapp_service import send_text_message
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


def get_media_response(media_type: str) -> str:
    """Get response for media messages"""
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
    """Verify webhook with Meta/Facebook"""
    
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
    """Receive and process incoming WhatsApp messages"""
    
    # Generate correlation ID for this request
    request_id = str(uuid.uuid4())
    context = RequestContext()
    context.set_request_id(request_id)
    context.start_time = time.time()
    set_current_context(context)
    
    logger.info("=" * 70)
    logger.info(f"📨 [REQ:{request_id}] WEBHOOK POST RECEIVED")
    
    try:
        # Parse request body
        payload = await request.json()
        logger.debug(f"[REQ:{request_id}] Payload (first 500 chars): {json.dumps(payload, indent=2)[:500]}")
        
        # Extract message data
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates (ignore them)
        if value.get("statuses"):
            logger.debug(f"[REQ:{request_id}] Status update ignored")
            return {"success": True, "message": "Status update ignored"}
        
        # Get messages
        messages = value.get("messages", [])
        if not messages:
            logger.debug(f"[REQ:{request_id}] No messages in payload")
            return {"success": True, "message": "No messages"}
        
        # Process each message
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
    """Process a single WhatsApp message with full observability"""
    
    context = get_current_context()
    
    try:
        # Extract message details
        message_type = message.get("type", "unknown")
        phone_number = message.get("from")
        message_id = message.get("id")
        
        context.set_phone_number(phone_number)
        
        logger.info(f"[REQ:{request_id}] 📱 Phone: {phone_number}")
        logger.info(f"[REQ:{request_id}] 📝 Message ID: {message_id}")
        logger.info(f"[REQ:{request_id}] 📂 Type: {message_type}")
        
        # PHASE 5: Rate limiting
        rate_ok, wait_time = check_rate_limit(phone_number)
        if not rate_ok:
            logger.warning(f"[REQ:{request_id}] Rate limit exceeded for {phone_number}")
            error_msg = f"⚠️ *Rate Limit Exceeded*\n\nYou have exceeded {RATE_LIMIT_REQUESTS} requests per minute. Please wait {wait_time} seconds before sending more messages."
            safe_send_reply(phone_number, error_msg)
            return {"error": "rate_limit", "wait_seconds": wait_time}
        
        # Check for duplicate
        if is_duplicate_message(phone_number, message_id):
            logger.info(f"[REQ:{request_id}] ⏭️ Duplicate message ignored")
            return {"skipped": True, "reason": "duplicate"}
        
        # Handle non-text messages
        if message_type != "text":
            logger.info(f"[REQ:{request_id}] ⏭️ Non-text message ignored: {message_type}")
            media_response = get_media_response(message_type)
            safe_send_reply(phone_number, media_response)
            return {"skipped": True, "reason": f"non-text ({message_type})"}
        
        # Extract text message
        customer_message = message.get("text", {}).get("body", "")
        
        if not customer_message:
            logger.warning(f"[REQ:{request_id}] Empty text message")
            return {"skipped": True, "reason": "empty message"}
        
        # PHASE 5: Input validation
        if not is_safe_input(customer_message):
            logger.warning(f"[REQ:{request_id}] Unsafe input blocked: {customer_message[:100]}")
            safe_send_reply(phone_number, "⚠️ *Invalid Input*\n\nYour message contains characters or patterns that cannot be processed.")
            return {"skipped": True, "reason": "unsafe_input"}
        
        logger.info(f"[REQ:{request_id}] 💬 Message: {customer_message[:200]}")
        
        # PHASE 2: AI Flow Logging
        logger.info(f"[REQ:{request_id}] 🤖 START AI PROCESSING")
        logger.info(f"[REQ:{request_id}] 📝 Question: {customer_message}")
        
        # PHASE 4: Send typing indicator
        background_tasks.add_task(send_typing_indicator, phone_number)
        
        # Check response cache (PHASE 3)
        cache_key = get_cache_key(customer_message)
        cache_type = "general"
        
        # Determine cache type based on message
        if customer_message.isdigit() and len(customer_message) >= 10:
            cache_type = "dn"
            cache_key = f"dn:{customer_message}"
        elif any(word in customer_message.lower() for word in ["top dealer", "dealer ranking"]):
            cache_type = "dealer"
        elif "executive" in customer_message.lower() or "ceo" in customer_message.lower():
            cache_type = "executive"
        
        cached_response = get_cached_response(cache_key, cache_type)
        if cached_response:
            metrics.record_cache_hit()
            logger.info(f"[REQ:{request_id}] 💾 Cache HIT for {cache_key}")
            
            # Send response parts
            response_parts = split_long_response(cached_response)
            for part in response_parts:
                safe_send_reply(phone_number, part)
            
            total_time = int((time.time() - context.start_time) * 1000)
            logger.info(f"[REQ:{request_id}] ⚡ Total time: {total_time}ms (CACHED)")
            
            return {
                "processed": True,
                "cached": True,
                "phone_number": phone_number,
                "message": customer_message[:100],
                "response_length": len(cached_response),
                "processing_time_ms": total_time
            }
        
        metrics.record_cache_miss()
        
        # PHASE 3: Track processing layers
        context.start_layer("ai_processing")
        
        # Process with AI Query Service
        try:
            from app.services.ai_query_service import process_whatsapp_query
            
            context.start_layer("ai_service")
            response = process_whatsapp_query(customer_message, db, phone_number)
            context.end_layer("ai_service")
            
            logger.info(f"[REQ:{request_id}] 🤖 Response: {response[:200]}...")
            
            # PHASE 2: Log response length
            logger.info(f"[REQ:{request_id}] 📏 Response length: {len(response)} chars")
            
            # Cache successful response
            if response and not response.startswith("⚠️") and not response.startswith("ERROR"):
                set_cached_response(cache_key, cache_type, response)
                logger.info(f"[REQ:{request_id}] 💾 Cached response for {cache_key}")
            
            context.end_layer("ai_processing")
            
            # PHASE 4: Split and send response
            context.start_layer("whatsapp_send")
            response_parts = split_long_response(response)
            for i, part in enumerate(response_parts):
                send_result = safe_send_reply(phone_number, part)
                if not send_result.get("success"):
                    logger.warning(f"[REQ:{request_id}] Failed to send part {i+1}")
            context.end_layer("whatsapp_send")
            
            # PHASE 2: Log layer timings
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
                "ai_used": True
            }
            
        except ImportError as e:
            # CRITICAL FIX: Full stack trace for AI import errors
            logger.exception(f"[REQ:{request_id}] Failed to import AI service")
            metrics.record_ai_error()
            
            # PHASE 1: Detailed error for DEBUG mode
            if config.DEBUG:
                error_response = f"""
❌ *SYSTEM ERROR*

**Type:** ImportError
**Message:** {str(e)}

**Service:** AI Query Service
**Request ID:** {request_id}

Please check system logs for details.
"""
            else:
                error_response = """
⚠️ *System Temporarily Unavailable*

Our AI service is currently experiencing issues.

Please try again in a few minutes.
"""
            
            response_parts = split_long_response(error_response)
            for part in response_parts:
                safe_send_reply(phone_number, part)
            
            return {
                "processed": True,
                "fallback": True,
                "error": str(e),
                "request_id": request_id
            }
            
        except Exception as e:
            # CRITICAL FIX: Full exception logging with stack trace
            logger.exception(f"[REQ:{request_id}] AI processing error")
            metrics.record_ai_error()
            
            # Track failure type for dashboard
            error_str = str(e).lower()
            if "dn" in error_str or "delivery" in error_str:
                metrics.record_dn_failure()
            elif "dealer" in error_str:
                metrics.record_dealer_failure()
            elif "db" in error_str or "database" in error_str:
                metrics.record_db_failure()
            
            # PHASE 1: Detailed error response based on DEBUG mode
            if config.DEBUG:
                error_response = f"""
❌ *ERROR DETECTED*

**Request ID:** {request_id}
**Phone:** {phone_number}
**Message:** {customer_message[:100]}

**Error Type:** {type(e).__name__}
**Error Message:** {str(e)[:200]}

**Stack Trace:**
```python
import traceback
traceback.format_exc()
