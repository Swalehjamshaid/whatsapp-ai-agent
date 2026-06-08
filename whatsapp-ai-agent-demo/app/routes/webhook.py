# ==========================================================
# FILE: app/routes/webhook.py (PRODUCTION READY v13.0)
# ==========================================================
# COMPLETE WEBHOOK WITH RAW PAYLOAD LOGGING
# - Added: Raw request body logging
# - Added: Full payload inspection
# - Added: Debug mode for troubleshooting
# - Fixed: Empty message handling
# - Fixed: Webhook verification issues
# ==========================================================

import json
import time
import re
import uuid
import traceback
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from collections import deque
from contextvars import ContextVar

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
AI_TIMEOUT_SECONDS = 30

# Debug mode - set to True to see full payloads
DEBUG_MODE = getattr(config, 'DEBUG', False) or os.getenv('DEBUG', 'false').lower() == 'true'

# ==========================================================
# IMPORTS
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

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("⚠️ Redis not available")

try:
    from app.services.whatsapp_service import send_text_message, send_typing_indicator
    WHATSAPP_SERVICE_AVAILABLE = True
except ImportError as e:
    WHATSAPP_SERVICE_AVAILABLE = False
    logger.error(f"❌ WhatsApp service import failed: {e}")

# ==========================================================
# REQUEST CONTEXT
# ==========================================================

_request_context: ContextVar[Optional[Dict]] = ContextVar("request_context", default=None)

class RequestContext:
    def __init__(self, request_id: str, phone_number: str = None):
        self.request_id = request_id
        self.phone_number = phone_number
        self.start_time = time.time()
        self.layers = {}
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

def get_current_context() -> Optional[RequestContext]:
    ctx = _request_context.get()
    if ctx and isinstance(ctx, dict):
        return ctx.get("context")
    return None

def get_or_create_context(request_id: str, phone_number: str = None) -> RequestContext:
    context = get_current_context()
    if context is None:
        context = RequestContext(request_id, phone_number)
        set_current_context(context)
    return context

def set_current_context(context: RequestContext):
    _request_context.set({"context": context})

def clear_current_context():
    _request_context.set(None)

# ==========================================================
# SIMPLE MEMORY CACHE
# ==========================================================

class SimpleCache:
    def __init__(self):
        self.cache = {}
        self.ttl = 300
    
    def get(self, key: str) -> Optional[str]:
        if key in self.cache:
            data, expiry = self.cache[key]
            if time.time() < expiry:
                return data
            del self.cache[key]
        return None
    
    def set(self, key: str, value: str):
        self.cache[key] = (value, time.time() + self.ttl)
    
    def get_cache_key(self, message: str) -> str:
        normalized = message.lower().strip()
        normalized = re.sub(r'\s+', ' ', normalized)
        if re.match(r'^\d{10,15}$', normalized):
            return f"dn:{normalized}"
        return normalized

cache_service = SimpleCache()

# ==========================================================
# RATE LIMITER
# ==========================================================

class SimpleRateLimiter:
    def __init__(self):
        self.requests = {}
        self.max_requests = 20
        self.window = 60
    
    def check(self, phone_number: str) -> Tuple[bool, int]:
        now = time.time()
        if phone_number not in self.requests:
            self.requests[phone_number] = []
        
        self.requests[phone_number] = [t for t in self.requests[phone_number] if now - t < self.window]
        
        if len(self.requests[phone_number]) >= self.max_requests:
            wait = int(self.window - (now - self.requests[phone_number][0]))
            return False, wait
        
        self.requests[phone_number].append(now)
        return True, 0

rate_limiter = SimpleRateLimiter()

# ==========================================================
# DUPLICATE DETECTOR
# ==========================================================

class SimpleDuplicateDetector:
    def __init__(self):
        self.processed = {}
        self.expiry = 3600
    
    def is_duplicate(self, phone_number: str, message_id: str) -> bool:
        if not message_id:
            return False
        
        key = f"{phone_number}:{message_id}"
        if key in self.processed:
            return True
        
        self.processed[key] = time.time()
        now = time.time()
        expired = [k for k, v in self.processed.items() if now - v > self.expiry]
        for k in expired:
            del self.processed[k]
        
        return False

duplicate_detector = SimpleDuplicateDetector()

# ==========================================================
# METRICS
# ==========================================================

class Metrics:
    def __init__(self):
        self.dn_queries = 0
        self.dn_found = 0
        self.dn_not_found = 0
        self.ai_errors = 0
        self.timeouts = 0
        self.webhook_calls = 0
        self.status_updates = 0
    
    def record_dn_query(self):
        self.dn_queries += 1
    
    def record_dn_found(self):
        self.dn_found += 1
    
    def record_dn_not_found(self):
        self.dn_not_found += 1
    
    def record_ai_error(self):
        self.ai_errors += 1
    
    def record_timeout(self):
        self.timeouts += 1
    
    def record_webhook_call(self):
        self.webhook_calls += 1
    
    def record_status_update(self):
        self.status_updates += 1

metrics = Metrics()

# ==========================================================
# WHATSAPP SENDER
# ==========================================================

def safe_send_reply(phone_number: str, message: str) -> Dict:
    try:
        if WHATSAPP_SERVICE_AVAILABLE:
            result = send_text_message(phone_number, message)
            return result
        else:
            logger.info(f"MOCK SEND to {phone_number}: {message[:100]}")
            return {"success": True, "mode": "mock"}
    except Exception as e:
        logger.exception(f"Send failed: {e}")
        return {"success": False, "error": str(e)}

def get_media_response(media_type: str) -> str:
    return "📱 Please send text messages only. Type 'Help' for available commands."

# ==========================================================
# AI PROCESSING WITH TIMEOUT
# ==========================================================

async def process_with_timeout(question: str, db: Session, phone_number: str) -> str:
    logger.info(f"⏱️ Starting AI processing for: {question[:50]}")
    
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, process_whatsapp_query, question, db, phone_number),
            timeout=AI_TIMEOUT_SECONDS
        )
        logger.info(f"✅ AI processing completed, response length: {len(result) if result else 0}")
        return result
    except asyncio.TimeoutError:
        logger.error(f"⏰ AI processing TIMEOUT after {AI_TIMEOUT_SECONDS}s")
        metrics.record_timeout()
        return "⚠️ Request timeout. Please try again."
    except Exception as e:
        logger.exception(f"❌ AI processing error: {e}")
        metrics.record_ai_error()
        raise

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
# MAIN WEBHOOK ENDPOINT (POST) - WITH RAW PAYLOAD LOGGING
# ==========================================================

@router.post("/")
async def receive_message(
    request: Request, 
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    metrics.record_webhook_call()
    request_id = str(uuid.uuid4())
    context = RequestContext(request_id)
    set_current_context(context)
    
    logger.info("=" * 70)
    logger.info(f"📨 [REQ:{request_id[:8]}] WEBHOOK CALL RECEIVED")
    
    # ==========================================================
    # CRITICAL: Log raw request body for debugging
    # ==========================================================
    try:
        raw_body = await request.body()
        logger.info(f"[REQ:{request_id[:8]}] RAW BODY (first 1000 chars): {raw_body[:1000].decode('utf-8')}")
    except Exception as e:
        logger.error(f"[REQ:{request_id[:8]}] Failed to read raw body: {e}")
        raw_body = b""
    
    try:
        # Parse JSON payload
        if raw_body:
            payload = json.loads(raw_body.decode('utf-8'))
        else:
            payload = await request.json()
        
        # Log full payload in debug mode
        if DEBUG_MODE:
            logger.debug(f"[REQ:{request_id[:8]}] FULL PAYLOAD: {json.dumps(payload, indent=2)[:2000]}")
        
        # Extract message data
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # ==========================================================
        # HANDLE STATUS UPDATES
        # ==========================================================
        if value.get("statuses"):
            metrics.record_status_update()
            statuses = value.get("statuses", [])
            logger.info(f"[REQ:{request_id[:8]}] STATUS UPDATE - {len(statuses)} status(es)")
            for status in statuses:
                logger.info(f"[REQ:{request_id[:8]}]   Status: {status.get('status')} | ID: {status.get('id')}")
            return {"success": True, "type": "status_update", "request_id": request_id}
        
        # ==========================================================
        # HANDLE MESSAGES
        # ==========================================================
        messages = value.get("messages", [])
        
        if not messages:
            logger.warning(f"[REQ:{request_id[:8]}] NO MESSAGES IN PAYLOAD")
            logger.info(f"[REQ:{request_id[:8]}] PAYLOAD KEYS: {list(payload.keys())}")
            logger.info(f"[REQ:{request_id[:8]}] VALUE KEYS: {list(value.keys())}")
            return {"success": True, "type": "no_messages", "request_id": request_id}
        
        logger.info(f"[REQ:{request_id[:8]}] 📨 MESSAGES FOUND: {len(messages)}")
        
        # Process each message
        results = []
        for idx, message in enumerate(messages):
            logger.info(f"[REQ:{request_id[:8]}] Processing message {idx + 1}/{len(messages)}")
            result = await process_single_message(message, db, background_tasks, request_id)
            results.append(result)
        
        processing_time = int((time.time() - context.start_time) * 1000)
        logger.info(f"[REQ:{request_id[:8]}] ✅ Processed {len(results)} messages in {processing_time}ms")
        
        return {
            "success": True,
            "request_id": request_id,
            "messages_processed": len(results),
            "processing_time_ms": processing_time
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"[REQ:{request_id[:8]}] Invalid JSON payload: {e}")
        logger.error(f"[REQ:{request_id[:8]}] Raw body: {raw_body[:500]}")
        return {"success": False, "error": "Invalid JSON", "request_id": request_id}
        
    except Exception as e:
        logger.exception(f"[REQ:{request_id[:8]}] Webhook error: {e}")
        return {"success": False, "error": str(e), "request_id": request_id}
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
    context = get_or_create_context(request_id)
    dn_query_start = None
    
    try:
        # Extract message details with safe defaults
        message_type = message.get("type", "unknown")
        phone_number = message.get("from")
        message_id = message.get("id")
        timestamp = message.get("timestamp")
        
        context.set_phone_number(phone_number)
        
        logger.info(f"[REQ:{request_id[:8]}] 📱 Phone: {phone_number}")
        logger.info(f"[REQ:{request_id[:8]}] 📝 Message ID: {message_id}")
        logger.info(f"[REQ:{request_id[:8]}] 📂 Type: {message_type}")
        logger.info(f"[REQ:{request_id[:8]}] ⏰ Timestamp: {timestamp}")
        
        # Handle non-text messages
        if message_type != "text":
            logger.info(f"[REQ:{request_id[:8]}] Non-text message: {message_type}")
            media_response = get_media_response(message_type)
            safe_send_reply(phone_number, media_response)
            return {"skipped": True, "reason": f"non-text ({message_type})", "request_id": request_id}
        
        # Extract text message
        text_obj = message.get("text", {})
        customer_message = text_obj.get("body", "") if text_obj else ""
        
        if not customer_message:
            logger.warning(f"[REQ:{request_id[:8]}] Empty text message")
            safe_send_reply(phone_number, "⚠️ Please send a text message with your question.")
            return {"skipped": True, "reason": "empty", "request_id": request_id}
        
        logger.info(f"[REQ:{request_id[:8]}] 💬 Message: {customer_message[:200]}")
        
        # ==========================================================
        # DN QUERY DETECTION
        # ==========================================================
        is_dn_query = bool(re.match(r'^\d{10,15}$', customer_message.strip()))
        
        if is_dn_query:
            dn_query_start = time.time()
            metrics.record_dn_query()
            logger.info(f"[REQ:{request_id[:8]}] 🔢 DN QUERY START: {customer_message}")
        
        # Rate limiting
        rate_ok, wait_time = rate_limiter.check(phone_number)
        if not rate_ok:
            logger.warning(f"[REQ:{request_id[:8]}] Rate limit exceeded for {phone_number}")
            safe_send_reply(phone_number, f"⚠️ Rate limit. Please wait {wait_time} seconds.")
            return {"error": "rate_limit", "wait_seconds": wait_time, "request_id": request_id}
        
        # Duplicate check
        if duplicate_detector.is_duplicate(phone_number, message_id):
            logger.info(f"[REQ:{request_id[:8]}] Duplicate message ignored")
            return {"skipped": True, "reason": "duplicate", "request_id": request_id}
        
        # Check cache
        cache_key = cache_service.get_cache_key(customer_message)
        cached_response = cache_service.get(cache_key)
        
        if cached_response:
            logger.info(f"[REQ:{request_id[:8]}] 💾 Cache HIT for {cache_key}")
            safe_send_reply(phone_number, cached_response)
            
            if is_dn_query and dn_query_start:
                dn_query_time = (time.time() - dn_query_start) * 1000
                logger.info(f"[REQ:{request_id[:8]}] 🔢 DN QUERY COMPLETE (CACHED): {customer_message} ({dn_query_time:.0f}ms)")
            
            return {"processed": True, "cached": True, "request_id": request_id}
        
        # Check AI service
        if not AI_SERVICE_AVAILABLE:
            logger.error(f"[REQ:{request_id[:8]}] AI service not available")
            error_msg = "⚠️ AI Service is currently unavailable. Please try again later."
            safe_send_reply(phone_number, error_msg)
            return {"error": "ai_unavailable", "request_id": request_id}
        
        # Process with AI
        context.start_layer("ai_processing")
        
        try:
            logger.info(f"[REQ:{request_id[:8]}] 🤖 Calling AI service...")
            response = await process_with_timeout(customer_message, db, phone_number)
            logger.info(f"[REQ:{request_id[:8]}] ✅ AI service returned, length: {len(response) if response else 0}")
            
            # DN QUERY RESULT LOGGING
            if is_dn_query and dn_query_start:
                dn_query_time = (time.time() - dn_query_start) * 1000
                if "not found" in response.lower() or "couldn't find" in response.lower():
                    metrics.record_dn_not_found()
                    logger.warning(f"[REQ:{request_id[:8]}] 🔢 DN NOT FOUND: {customer_message} ({dn_query_time:.0f}ms)")
                else:
                    metrics.record_dn_found()
                    logger.info(f"[REQ:{request_id[:8]}] 🔢 DN FOUND: {customer_message} ({dn_query_time:.0f}ms)")
            
            context.end_layer("ai_processing")
            
            # Cache response
            if response and len(response) > 10:
                cache_service.set(cache_key, response)
            
            # Send response
            safe_send_reply(phone_number, response)
            
            total_time = context.get_total_time_ms()
            logger.info(f"[REQ:{request_id[:8]}] ⚡ Total processing time: {total_time:.0f}ms")
            
            return {
                "processed": True,
                "response_length": len(response) if response else 0,
                "processing_time_ms": total_time,
                "request_id": request_id[:8]
            }
            
        except Exception as e:
            logger.exception(f"[REQ:{request_id[:8]}] AI processing ERROR: {e}")
            metrics.record_ai_error()
            
            if is_dn_query and dn_query_start:
                dn_query_time = (time.time() - dn_query_start) * 1000
                logger.error(f"[REQ:{request_id[:8]}] 🔢 DN QUERY FAILED: {customer_message} ({dn_query_time:.0f}ms) - {str(e)[:100]}")
            
            error_response = f"⚠️ Error processing your request.\n\nRequest ID: {request_id[:8]}\n\nPlease try again."
            safe_send_reply(phone_number, error_response)
            
            return {
                "processed": True,
                "error": str(e),
                "error_type": type(e).__name__,
                "fallback": True,
                "request_id": request_id[:8]
            }
        
    except Exception as e:
        logger.exception(f"[REQ:{request_id[:8]}] Message processing error: {e}")
        return {"error": str(e), "processed": False, "request_id": request_id[:8]}

# ==========================================================
# TEST ENDPOINTS
# ==========================================================

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "13.0",
        "debug_mode": DEBUG_MODE,
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "ai_service": AI_SERVICE_AVAILABLE,
            "whatsapp_service": WHATSAPP_SERVICE_AVAILABLE,
            "redis": REDIS_AVAILABLE
        },
        "metrics": {
            "webhook_calls": metrics.webhook_calls,
            "status_updates": metrics.status_updates,
            "dn_queries": metrics.dn_queries,
            "dn_found": metrics.dn_found,
            "dn_not_found": metrics.dn_not_found,
            "dn_success_rate": round(metrics.dn_found / max(1, metrics.dn_queries) * 100, 1),
            "ai_errors": metrics.ai_errors,
            "timeouts": metrics.timeouts
        }
    }

@router.get("/status")
async def status():
    """Detailed status endpoint"""
    return {
        "service": "WhatsApp Webhook v13.0",
        "webhook_url": "/webhook/",
        "verified": True,
        "debug_mode": DEBUG_MODE,
        "ai_service_available": AI_SERVICE_AVAILABLE,
        "whatsapp_service_available": WHATSAPP_SERVICE_AVAILABLE,
        "metrics": {
            "webhook_calls": metrics.webhook_calls,
            "status_updates": metrics.status_updates,
            "dn_queries": metrics.dn_queries,
            "dn_found": metrics.dn_found,
            "dn_not_found": metrics.dn_not_found,
            "dn_success_rate": round(metrics.dn_found / max(1, metrics.dn_queries) * 100, 1),
            "ai_errors": metrics.ai_errors,
            "timeouts": metrics.timeouts
        },
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/test-dn/{dn_number}")
async def test_dn_lookup(dn_number: str, db: Session = Depends(get_db)):
    """Direct DN lookup test endpoint"""
    from app.services.logistics_query_service import LogisticsQueryService
    
    logger.info(f"🔍 TEST DN LOOKUP: {dn_number}")
    start_time = time.time()
    
    try:
        service = LogisticsQueryService(db)
        result = service.get_complete_dn_intelligence(dn_number)
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        if "error" in result:
            logger.warning(f"❌ DN {dn_number} NOT FOUND ({elapsed_ms:.0f}ms)")
            return {
                "found": False,
                "dn": dn_number,
                "error": result["error"],
                "elapsed_ms": elapsed_ms
            }
        else:
            logger.info(f"✅ DN {dn_number} FOUND: {result.get('dealer')} ({elapsed_ms:.0f}ms)")
            return {
                "found": True,
                "dn": dn_number,
                "dealer": result.get("dealer"),
                "total_value": result.get("total_value"),
                "total_units": result.get("total_units"),
                "status": result.get("status"),
                "stage": result.get("stage"),
                "elapsed_ms": elapsed_ms
            }
    except Exception as e:
        logger.exception(f"DN lookup error: {e}")
        return {"found": False, "error": str(e), "dn": dn_number}

@router.post("/test-webhook")
async def test_webhook(request: Request, db: Session = Depends(get_db)):
    """Test endpoint to simulate webhook message"""
    try:
        payload = await request.json()
        logger.info(f"📨 TEST WEBHOOK RECEIVED")
        
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        messages = value.get("messages", [])
        results = []
        
        for message in messages:
            phone_number = message.get("from", "test_user")
            text = message.get("text", {}).get("body", "Test message")
            
            logger.info(f"Processing test message: {text}")
            response = process_whatsapp_query(text, db, phone_number)
            
            results.append({
                "phone_number": phone_number,
                "message": text,
                "response": response[:200],
                "status": "processed"
            })
        
        return {
            "success": True,
            "message": "Test webhook processed",
            "messages_processed": len(results),
            "results": results
        }
        
    except Exception as e:
        logger.exception(f"Test webhook error: {e}")
        return {"success": False, "error": str(e)}

@router.get("/test")
async def test():
    """Simple test endpoint"""
    return {
        "success": True,
        "message": "Webhook service is running!",
        "version": "13.0",
        "endpoints": {
            "GET /webhook/": "Meta verification",
            "POST /webhook/": "Receive WhatsApp messages",
            "GET /webhook/health": "Health check",
            "GET /webhook/status": "Detailed status",
            "GET /webhook/test": "Test endpoint",
            "GET /webhook/test-dn/{dn_number}": "Test DN lookup",
            "POST /webhook/test-webhook": "Simulate webhook"
        }
    }

@router.post("/clear-cache")
async def clear_cache():
    """Clear response cache"""
    try:
        cache_service.cache.clear()
        return {"success": True, "message": "Cache cleared"}
    except Exception as e:
        logger.exception(f"Clear cache error: {e}")
        return {"success": False, "error": str(e)}
