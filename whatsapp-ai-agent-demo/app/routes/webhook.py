# ==========================================================
# FILE: app/routes/webhook.py (PRODUCTION READY v13.1)
# ==========================================================
# FIXED: Added missing os import
# FIXED: Removed DEBUG_MODE dependency
# ==========================================================

import os  # CRITICAL - ADDED THIS
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
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN and hub_challenge:
        logger.success("✅ Webhook verification successful!")
        return PlainTextResponse(content=hub_challenge)
    
    logger.error("❌ Webhook verification failed")
    raise HTTPException(status_code=403, detail="Verification failed")

# ==========================================================
# MAIN WEBHOOK ENDPOINT (POST)
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
    
    logger.info("=" * 60)
    logger.info(f"📨 [REQ:{request_id[:8]}] WEBHOOK CALL RECEIVED")
    
    try:
        # Parse JSON payload
        payload = await request.json()
        
        # Log payload structure
        logger.info(f"[REQ:{request_id[:8]}] Payload keys: {list(payload.keys())}")
        
        # Extract message data
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates
        if value.get("statuses"):
            metrics.record_status_update()
            statuses = value.get("statuses", [])
            logger.info(f"[REQ:{request_id[:8]}] STATUS UPDATE - {len(statuses)} status(es)")
            return {"success": True, "type": "status_update", "request_id": request_id}
        
        # Handle messages
        messages = value.get("messages", [])
        
        if not messages:
            logger.warning(f"[REQ:{request_id[:8]}] NO MESSAGES IN PAYLOAD")
            return {"success": True, "type": "no_messages", "request_id": request_id}
        
        logger.info(f"[REQ:{request_id[:8]}] 📨 MESSAGES FOUND: {len(messages)}")
        
        # Process each message
        results = []
        for message in messages:
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
        logger.error(f"[REQ:{request_id[:8]}] Invalid JSON: {e}")
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
        message_type = message.get("type", "unknown")
        phone_number = message.get("from")
        message_id = message.get("id")
        
        context.set_phone_number(phone_number)
        
        logger.info(f"[REQ:{request_id[:8]}] 📱 Phone: {phone_number}")
        logger.info(f"[REQ:{request_id[:8]}] 📂 Type: {message_type}")
        
        if message_type != "text":
            logger.info(f"[REQ:{request_id[:8]}] Non-text message: {message_type}")
            media_response = get_media_response(message_type)
            safe_send_reply(phone_number, media_response)
            return {"skipped": True, "reason": f"non-text ({message_type})"}
        
        customer_message = message.get("text", {}).get("body", "")
        
        if not customer_message:
            logger.warning(f"[REQ:{request_id[:8]}] Empty message")
            return {"skipped": True, "reason": "empty"}
        
        logger.info(f"[REQ:{request_id[:8]}] 💬 Message: {customer_message[:200]}")
        
        # DN query detection
        is_dn_query = bool(re.match(r'^\d{10,15}$', customer_message.strip()))
        
        if is_dn_query:
            dn_query_start = time.time()
            metrics.record_dn_query()
            logger.info(f"[REQ:{request_id[:8]}] 🔢 DN QUERY: {customer_message}")
        
        # Rate limiting
        rate_ok, wait_time = rate_limiter.check(phone_number)
        if not rate_ok:
            safe_send_reply(phone_number, f"⚠️ Rate limit. Please wait {wait_time}s.")
            return {"error": "rate_limit"}
        
        # Duplicate check
        if duplicate_detector.is_duplicate(phone_number, message_id):
            logger.info(f"[REQ:{request_id[:8]}] Duplicate ignored")
            return {"skipped": True, "reason": "duplicate"}
        
        # Cache check
        cache_key = cache_service.get_cache_key(customer_message)
        cached_response = cache_service.get(cache_key)
        
        if cached_response:
            logger.info(f"[REQ:{request_id[:8]}] Cache HIT")
            safe_send_reply(phone_number, cached_response)
            return {"processed": True, "cached": True}
        
        # AI service check
        if not AI_SERVICE_AVAILABLE:
            error_msg = "⚠️ AI Service unavailable. Please try again later."
            safe_send_reply(phone_number, error_msg)
            return {"error": "ai_unavailable"}
        
        # Process with AI
        try:
            logger.info(f"[REQ:{request_id[:8]}] 🤖 Calling AI service...")
            response = await process_with_timeout(customer_message, db, phone_number)
            
            if is_dn_query and dn_query_start:
                dn_query_time = (time.time() - dn_query_start) * 1000
                if "not found" in response.lower():
                    metrics.record_dn_not_found()
                    logger.warning(f"[REQ:{request_id[:8]}] 🔢 DN NOT FOUND: {customer_message} ({dn_query_time:.0f}ms)")
                else:
                    metrics.record_dn_found()
                    logger.info(f"[REQ:{request_id[:8]}] 🔢 DN FOUND: {customer_message} ({dn_query_time:.0f}ms)")
            
            # Cache response
            if response and len(response) > 10:
                cache_service.set(cache_key, response)
            
            # Send response
            safe_send_reply(phone_number, response)
            
            total_time = context.get_total_time_ms()
            logger.info(f"[REQ:{request_id[:8]}] ⚡ Total: {total_time:.0f}ms")
            
            return {"processed": True, "response_length": len(response)}
            
        except Exception as e:
            logger.exception(f"[REQ:{request_id[:8]}] AI error: {e}")
            metrics.record_ai_error()
            error_response = "⚠️ Error processing request. Please try again."
            safe_send_reply(phone_number, error_response)
            return {"processed": True, "error": str(e), "fallback": True}
        
    except Exception as e:
        logger.exception(f"[REQ:{request_id[:8]}] Message error: {e}")
        return {"error": str(e), "processed": False}

# ==========================================================
# TEST ENDPOINTS
# ==========================================================

@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "13.1",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "ai_service": AI_SERVICE_AVAILABLE,
            "whatsapp_service": WHATSAPP_SERVICE_AVAILABLE
        },
        "metrics": {
            "dn_queries": metrics.dn_queries,
            "dn_found": metrics.dn_found,
            "dn_not_found": metrics.dn_not_found,
            "dn_success_rate": round(metrics.dn_found / max(1, metrics.dn_queries) * 100, 1)
        }
    }

@router.get("/test-dn/{dn_number}")
async def test_dn_lookup(dn_number: str, db: Session = Depends(get_db)):
    from app.services.logistics_query_service import LogisticsQueryService
    
    start_time = time.time()
    
    try:
        service = LogisticsQueryService(db)
        result = service.get_complete_dn_intelligence(dn_number)
        elapsed_ms = (time.time() - start_time) * 1000
        
        if "error" in result:
            return {"found": False, "dn": dn_number, "error": result["error"], "elapsed_ms": elapsed_ms}
        else:
            return {
                "found": True,
                "dn": dn_number,
                "dealer": result.get("dealer"),
                "total_value": result.get("total_value"),
                "elapsed_ms": elapsed_ms
            }
    except Exception as e:
        return {"found": False, "error": str(e), "dn": dn_number}

@router.get("/status")
async def status():
    return {
        "service": "WhatsApp Webhook v13.1",
        "ai_service": AI_SERVICE_AVAILABLE,
        "whatsapp_service": WHATSAPP_SERVICE_AVAILABLE,
        "metrics": {
            "dn_queries": metrics.dn_queries,
            "dn_found": metrics.dn_found,
            "dn_not_found": metrics.dn_not_found
        }
    }

@router.get("/test")
async def test():
    return {"success": True, "message": "Webhook is running", "version": "13.1"}
