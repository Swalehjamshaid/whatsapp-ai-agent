# ==========================================================
# FILE: app/routes/webhook.py (v18.1 - NO PSUTIL DEPENDENCY)
# ==========================================================

import re
import uuid
import asyncio
import time
import hmac
import hashlib
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional, List
from collections import defaultdict, deque
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Request, BackgroundTasks, Query, HTTPException, Header
from fastapi.responses import JSONResponse, Response
from loguru import logger
from cachetools import TTLCache

from app.config import config
from app.services.ai_provider_service import process_whatsapp_query
from app.services.whatsapp_service import send_text_message

# ==========================================================
# ROUTER INITIALIZATION
# ==========================================================

router = APIRouter(tags=["WhatsApp Webhook"])

# ==========================================================
# CONFIGURATION
# ==========================================================

DEBUG_MODE = getattr(config, 'ENVIRONMENT', 'development') != 'production'
REQUIRE_SIGNATURE = getattr(config, 'WHATSAPP_SIGNATURE_REQUIRED', False)
LOG_RAW_PAYLOADS = getattr(config, 'LOG_RAW_WEBHOOK_PAYLOADS', DEBUG_MODE)
RATE_LIMIT_REQUESTS = getattr(config, 'WHATSAPP_RATE_LIMIT', 100)
RATE_LIMIT_WINDOW = 60
MAX_STORED_EVENTS = 100
PROCESSING_TIMEOUT_SECONDS = 20
MAX_MESSAGE_LENGTH = 4000
CONVERSATION_TTL_SECONDS = 1800
ADMIN_SECRET = getattr(config, 'ADMIN_SECRET', '')

# Circuit breaker configuration
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 60
CIRCUIT_BREAKER_HALF_OPEN_MAX_ATTEMPTS = 3

# Thread pool configuration
CPU_COUNT = os.cpu_count() or 2
MAX_WORKERS = min(32, CPU_COUNT * 4)

# ==========================================================
# MEMORY UTILITY (without psutil)
# ==========================================================

def get_memory_mb():
    """Get memory usage in MB without psutil"""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux
    except:
        try:
            # Windows fallback
            import ctypes
            kernel32 = ctypes.windll.kernel32
            process = kernel32.GetCurrentProcess()
            handle = kernel32.OpenProcess(0x0400 | 0x0010, False, process)
            process_memory = ctypes.c_size_t()
            kernel32.GetProcessMemoryInfo(handle, ctypes.byref(process_memory), ctypes.sizeof(process_memory))
            return process_memory.value / 1024 / 1024
        except:
            return 0

# ==========================================================
# GLOBALS
# ==========================================================

_recent_events = deque(maxlen=MAX_STORED_EVENTS)
_processed_messages = TTLCache(maxsize=50000, ttl=86400)
_phone_rate_limits = TTLCache(maxsize=50000, ttl=RATE_LIMIT_WINDOW)
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="webhook_worker")
_crash_history: deque = deque(maxlen=20)

# Performance metrics
_processing_times: deque = deque(maxlen=10000)
_error_timestamps: deque = deque(maxlen=1000)
_timeout_timestamps: deque = deque(maxlen=1000)
_request_timestamps: deque = deque(maxlen=10000)
_active_requests: Dict[str, float] = {}

# ==========================================================
# CIRCUIT BREAKER
# ==========================================================

class AIServiceCircuitBreaker:
    """Circuit breaker for AI service to prevent cascading failures"""
    
    def __init__(self):
        self.state = "CLOSED"
        self.failure_count = 0
        self.last_failure_time = 0
        self.last_success_time = 0
        self.half_open_attempts = 0
        self.total_failures = 0
        self.total_successes = 0
    
    def is_allowed(self) -> bool:
        now = time.time()
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if now - self.last_failure_time > CIRCUIT_BREAKER_RECOVERY_TIMEOUT:
                self.state = "HALF_OPEN"
                self.half_open_attempts = 0
                logger.info("Circuit breaker: OPEN -> HALF_OPEN")
                return True
            return False
        if self.state == "HALF_OPEN":
            if self.half_open_attempts >= CIRCUIT_BREAKER_HALF_OPEN_MAX_ATTEMPTS:
                self.state = "OPEN"
                self.last_failure_time = now
                logger.warning("Circuit breaker: HALF_OPEN -> OPEN")
                return False
            self.half_open_attempts += 1
            return True
        return True
    
    def record_success(self):
        self.failure_count = 0
        self.total_successes += 1
        self.last_success_time = time.time()
        if self.state == "HALF_OPEN":
            self.state = "CLOSED"
            logger.info("Circuit breaker: HALF_OPEN -> CLOSED")
    
    def record_failure(self):
        now = time.time()
        self.failure_count += 1
        self.total_failures += 1
        self.last_failure_time = now
        _error_timestamps.append(now)
        if self.state == "CLOSED" and self.failure_count >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            self.state = "OPEN"
            logger.error(f"Circuit breaker: CLOSED -> OPEN (threshold: {self.failure_count})")
        elif self.state == "HALF_OPEN":
            self.state = "OPEN"
            self.last_failure_time = now
            logger.warning("Circuit breaker: HALF_OPEN -> OPEN")
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "success_rate": self.total_successes / (self.total_failures + self.total_successes) if (self.total_failures + self.total_successes) > 0 else 1.0
        }

_ai_circuit_breaker = AIServiceCircuitBreaker()

# ==========================================================
# SIMPLIFIED CONVERSATION CONTEXT
# ==========================================================

@dataclass
class ConversationContext:
    phone_number: str
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    last_request_id: Optional[str] = None

class ConversationTracker:
    def __init__(self, maxsize: int = 10000, ttl: int = CONVERSATION_TTL_SECONDS):
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._ttl = ttl
    
    def get(self, phone_number: str) -> ConversationContext:
        if phone_number not in self._cache:
            self._cache[phone_number] = ConversationContext(phone_number=phone_number)
        return self._cache[phone_number]
    
    def update(self, phone_number: str, **kwargs) -> None:
        context = self.get(phone_number)
        for key, value in kwargs.items():
            if hasattr(context, key) and value is not None:
                setattr(context, key, value)
        context.message_count += 1
        context.last_updated = time.time()
        self._cache[phone_number] = context
    
    def clear(self, phone_number: str) -> bool:
        if phone_number in self._cache:
            del self._cache[phone_number]
            return True
        return False
    
    def get_stats(self) -> Dict[str, int]:
        return {"cache_size": len(self._cache), "maxsize": self._cache.maxsize, "ttl_seconds": self._ttl}

_conversation_tracker = ConversationTracker()

# ==========================================================
# METRICS
# ==========================================================

@dataclass
class WebhookMetrics:
    messages_received: int = 0
    messages_processed: int = 0
    processing_failures: int = 0
    duplicate_messages: int = 0
    service_failures: int = 0
    rate_limited: int = 0
    status_events: int = 0
    webhook_hits: int = 0
    verification_hits: int = 0
    service_timeouts: int = 0
    invalid_signatures: int = 0
    circuit_breaker_rejections: int = 0
    last_message_time: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "messages_received": self.messages_received,
            "messages_processed": self.messages_processed,
            "processing_failures": self.processing_failures,
            "duplicate_messages": self.duplicate_messages,
            "service_failures": self.service_failures,
            "rate_limited": self.rate_limited,
            "status_events": self.status_events,
            "webhook_hits": self.webhook_hits,
            "verification_hits": self.verification_hits,
            "service_timeouts": self.service_timeouts,
            "invalid_signatures": self.invalid_signatures,
            "circuit_breaker_rejections": self.circuit_breaker_rejections,
            "last_message_time": self.last_message_time.isoformat() if self.last_message_time else None,
            "uptime_seconds": time.time() - self._start_time
        }
    
    _start_time: float = field(default_factory=time.time, init=False)

_metrics = WebhookMetrics()

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def mask_sensitive_data(value: str, keep_start: int = 3, keep_end: int = 2) -> str:
    if not value or len(value) < keep_start + keep_end:
        return "***"
    return f"{value[:keep_start]}****{value[-keep_end:]}"

def generate_request_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

def store_event(event_type: str, data: Dict[str, Any]):
    _recent_events.appendleft({"type": event_type, "timestamp": datetime.now().isoformat(), "data": data})

def is_admin_request(request: Request) -> bool:
    if DEBUG_MODE:
        return True
    admin_key = request.headers.get('X-Admin-Key', '')
    return admin_key == ADMIN_SECRET

def get_structured_logger(request_id: str, phone_number: str = None, message_id: str = None):
    context = {"request_id": request_id}
    if phone_number:
        context["phone"] = mask_sensitive_data(phone_number)
    if message_id:
        context["message_id"] = message_id
    return logger.bind(**context)

# ==========================================================
# RATE LIMITING
# ==========================================================

def check_rate_limit(phone_number: str) -> bool:
    global _metrics
    now = time.time()
    requests = _phone_rate_limits.get(phone_number, [])
    recent = [t for t in requests if now - t < RATE_LIMIT_WINDOW]
    if len(recent) >= RATE_LIMIT_REQUESTS:
        _metrics.rate_limited += 1
        logger.warning(f"Rate limit exceeded for {mask_sensitive_data(phone_number)}")
        return False
    recent.append(now)
    _phone_rate_limits[phone_number] = recent
    return True

# ==========================================================
# MESSAGE DEDUPLICATION
# ==========================================================

def is_duplicate_message(message_id: str) -> bool:
    if not message_id:
        return False
    if message_id in _processed_messages:
        _metrics.duplicate_messages += 1
        return True
    _processed_messages[message_id] = time.time()
    return False

# ==========================================================
# DIRECT RESPONSE SENDER (FALLBACK)
# ==========================================================

async def _send_direct_response(phone_number: str, message: str, request_id: str):
    try:
        token = getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')
        phone_id = getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')
        if not token or not phone_id:
            logger.error(f"[{request_id}] WhatsApp credentials missing")
            return False
        cleaned = re.sub(r'\D', '', phone_number)
        if cleaned.startswith('0'):
            cleaned = '92' + cleaned[1:]
        elif len(cleaned) == 10:
            cleaned = '92' + cleaned
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://graph.facebook.com/v20.0/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"messaging_product": "whatsapp", "to": cleaned, "type": "text", "text": {"body": message[:MAX_MESSAGE_LENGTH]}}
            )
            if response.status_code in [200, 201]:
                logger.success(f"[{request_id}] Direct response sent")
                return True
            else:
                logger.error(f"[{request_id}] Direct send failed: {response.status_code}")
                return False
    except Exception as e:
        logger.exception(f"[{request_id}] Direct send error: {e}")
        return False

# ==========================================================
# WHATSAPP RESPONSE SENDER (PRIMARY)
# ==========================================================

async def send_whatsapp_response(phone_number: str, message: str, message_id: str, request_id: str):
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_executor, send_text_message, phone_number, message, message_id, request_id)
        logger.debug(f"[{request_id}] Response sent")
        return
    except Exception as e:
        logger.warning(f"[{request_id}] WhatsApp service failed: {e}")
    await _send_direct_response(phone_number, message, request_id)

# ==========================================================
# CORE MESSAGE PROCESSING
# ==========================================================

async def handle_message(phone_number: str, message_text: str, sender_name: str, message_id: str, request_id: str):
    start_time = time.time()
    status = "success"
    error_type = None
    
    _active_requests[request_id] = start_time
    _request_timestamps.append(start_time)
    struct_log = get_structured_logger(request_id, phone_number, message_id)
    
    try:
        struct_log.info(f"Processing message: {message_text[:50]}...")
        context = _conversation_tracker.get(phone_number)
        _conversation_tracker.update(phone_number, last_request_id=request_id)
        
        if not _ai_circuit_breaker.is_allowed():
            _metrics.circuit_breaker_rejections += 1
            struct_log.warning("Circuit breaker open - sending fallback response")
            await send_whatsapp_response(
                phone_number,
                "🔌 I'm currently experiencing high load. Please try again in a few minutes.",
                message_id,
                request_id
            )
            return
        
        loop = asyncio.get_event_loop()
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(_executor, process_whatsapp_query, message_text, None, phone_number, None, request_id),
                timeout=PROCESSING_TIMEOUT_SECONDS
            )
            _ai_circuit_breaker.record_success()
            _metrics.messages_processed += 1
        except asyncio.TimeoutError:
            _metrics.service_timeouts += 1
            _timeout_timestamps.append(time.time())
            status = "timeout"
            error_type = "timeout"
            _ai_circuit_breaker.record_failure()
            struct_log.error("Processing timeout")
            await send_whatsapp_response(
                phone_number,
                "⏳ Your request is taking longer than expected. I'll respond shortly.",
                message_id,
                request_id
            )
            return
        except Exception as e:
            error_type = type(e).__name__
            status = "error"
            _metrics.processing_failures += 1
            _ai_circuit_breaker.record_failure()
            struct_log.exception(f"Processing error: {e}")
            await send_whatsapp_response(
                phone_number,
                "⚠️ I encountered an error. Please try again or type 'Help'.",
                message_id,
                request_id
            )
            return
        
        await send_whatsapp_response(phone_number, response, message_id, request_id)
        
    except Exception as e:
        status = "error"
        error_type = type(e).__name__
        _metrics.processing_failures += 1
        struct_log.exception(f"Unexpected error: {e}")
        try:
            await send_whatsapp_response(
                phone_number,
                "⚠️ I encountered an error. Please try again or type 'Help'.",
                message_id,
                request_id
            )
        except Exception as send_error:
            struct_log.exception(f"Failed to send error response: {send_error}")
    
    finally:
        duration_ms = int((time.time() - start_time) * 1000)
        _processing_times.append(duration_ms)
        _active_requests.pop(request_id, None)
        struct_log.bind(
            duration_ms=duration_ms,
            status=status,
            error_type=error_type
        ).info("Message processing complete")

# ==========================================================
# WEBHOOK VERIFICATION
# ==========================================================

@router.get("/webhook")
@router.get("/webhook/")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    _metrics.verification_hits += 1
    logger.info(f"Webhook verification: mode={hub_mode}")
    try:
        verify_token = getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')
        if hub_mode == 'subscribe' and hub_verify_token == verify_token:
            logger.success("Webhook verified successfully")
            return Response(content=hub_challenge, status_code=200, media_type="text/plain")
        else:
            logger.warning("Verification failed - token mismatch")
            return JSONResponse(content={"error": "Verification failed"}, status_code=403)
    except Exception as e:
        logger.exception(f"Verification error: {e}")
        return JSONResponse(content={"error": "Internal error"}, status_code=500)

# ==========================================================
# MAIN WEBHOOK HANDLER
# ==========================================================

@router.post("/webhook")
@router.post("/webhook/")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    _metrics.webhook_hits += 1
    try:
        try:
            raw_body = await request.body()
        except Exception as e:
            logger.error(f"Body read failed: {e}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        try:
            data = await request.json()
        except Exception as e:
            logger.error(f"JSON parse failed: {e}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        if not data or data.get('object') != 'whatsapp_business_account':
            return JSONResponse({"status": "ok"}, status_code=200)
        
        entries = data.get('entry') or []
        if not entries:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        changes = entries[0].get('changes') or []
        if not changes:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        value = changes[0].get('value') or {}
        
        if 'statuses' in value:
            _metrics.status_events += 1
            return JSONResponse({"status": "ok"}, status_code=200)
        
        messages = value.get('messages') or []
        if not messages:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        message = messages[0]
        phone_number = message.get('from')
        message_id = message.get('id')
        message_type = message.get('type')
        
        if not phone_number or not message_id:
            logger.warning("Missing phone_number or message_id in payload")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        message_text = None
        if message_type == 'text':
            message_text = message.get('text', {}).get('body', '')
        elif message_type == 'interactive':
            interactive = message.get('interactive') or {}
            if interactive.get('type') == 'button_reply':
                message_text = interactive.get('button_reply', {}).get('title', '')
        
        if not message_text:
            logger.debug(f"No text content in message: {message_type}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        if is_duplicate_message(message_id):
            logger.debug(f"Duplicate: {message_id}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        if not check_rate_limit(phone_number):
            logger.info(f"Rate limited: {mask_sensitive_data(phone_number)}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        contacts = value.get('contacts') or []
        sender_name = contacts[0].get('profile', {}).get('name', 'User') if contacts else 'User'
        
        request_id = generate_request_id()
        struct_log = get_structured_logger(request_id, phone_number, message_id)
        struct_log.info(f"📨 Message received: {message_text[:50]}...")
        
        store_event("message", {
            "phone": mask_sensitive_data(phone_number),
            "preview": message_text[:100],
            "message_id": message_id
        })
        
        background_tasks.add_task(handle_message, phone_number, message_text.strip(), sender_name, message_id, request_id)
        
        _metrics.messages_received += 1
        _metrics.last_message_time = datetime.now()
        
        return JSONResponse({"status": "ok"}, status_code=200)
        
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return JSONResponse({"status": "ok"}, status_code=200)

# ==========================================================
# HEALTH AND DIAGNOSTICS ENDPOINTS
# ==========================================================

@router.get("/webhook/ping")
async def webhook_ping():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@router.get("/webhook/health")
async def webhook_health():
    memory_mb = get_memory_mb()
    return {
        'status': 'healthy',
        'version': '18.1',
        'timestamp': datetime.now().isoformat(),
        'metrics': {
            'messages_received': _metrics.messages_received,
            'messages_processed': _metrics.messages_processed,
            'processing_failures': _metrics.processing_failures,
            'service_timeouts': _metrics.service_timeouts,
        },
        'conversation_cache_size': _conversation_tracker.get_stats()["cache_size"],
        'memory_mb': round(memory_mb, 2) if memory_mb else 0
    }

@router.get("/webhook/metrics")
async def webhook_metrics():
    return {
        "overall": _metrics.to_dict(),
        "conversation_stats": _conversation_tracker.get_stats(),
        "circuit_breaker": _ai_circuit_breaker.get_stats(),
        "version": "18.1"
    }

@router.get("/webhook/self-test")
async def webhook_self_test():
    return {
        "status": "running",
        "version": "18.1",
        "timestamp": datetime.now().isoformat(),
        "whatsapp_token": bool(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')),
        "phone_number_id": bool(getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')),
        "verify_token": bool(getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')),
        "conversation_stats": _conversation_tracker.get_stats(),
        "metrics": _metrics.to_dict()
    }

@router.get("/webhook/test-send")
async def test_send_message(request: Request, phone: str = "923006666666", message: str = "Test message"):
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    logger.info(f"TEST SEND: phone={mask_sensitive_data(phone)}")
    try:
        result = send_text_message(phone_number=phone, message=f"🧪 TEST: {message}", request_id=generate_request_id())
        return {"status": "sent", "result": result}
    except Exception as e:
        return {"error": str(e)}

@router.get("/webhook/conversation/clear/{phone}")
async def clear_conversation(phone: str):
    result = _conversation_tracker.clear(phone)
    return {"cleared": result, "phone": mask_sensitive_data(phone)}

@router.get("/webhook/conversation/{phone}")
async def get_conversation(phone: str):
    context = _conversation_tracker.get(phone)
    return {
        "phone": mask_sensitive_data(phone),
        "message_count": context.message_count,
        "created_at": datetime.fromtimestamp(context.created_at).isoformat(),
        "last_updated": datetime.fromtimestamp(context.last_updated).isoformat(),
        "last_request_id": context.last_request_id
    }

@router.post("/webhook/circuit-breaker/reset")
async def reset_circuit_breaker(request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    _ai_circuit_breaker.state = "CLOSED"
    _ai_circuit_breaker.failure_count = 0
    _ai_circuit_breaker.half_open_attempts = 0
    logger.info("Circuit breaker manually reset")
    return {"status": "reset", "state": "CLOSED"}

# ==========================================================
# GRACEFUL SHUTDOWN
# ==========================================================

@router.on_event("shutdown")
async def shutdown_webhook():
    logger.info("Webhook shutting down...")
    logger.info("Shutting down thread pool...")
    _executor.shutdown(wait=True, cancel_futures=False)
    logger.success("Thread pool shutdown complete")
    _processed_messages.clear()
    _phone_rate_limits.clear()
    _conversation_tracker._cache.clear()
    logger.success("Webhook shutdown complete")

# ==========================================================
# SERVICE INITIALIZATION
# ==========================================================

async def initialize_services():
    logger.info("=" * 60)
    logger.info("Webhook v18.1 - WhatsApp Gateway Layer")
    logger.info("=" * 60)
    logger.info(f"  Environment: {getattr(config, 'ENVIRONMENT', 'development')}")
    logger.info(f"  WhatsApp Token: {'✅' if getattr(config, 'WHATSAPP_ACCESS_TOKEN', '') else '❌'}")
    logger.info(f"  Phone Number ID: {'✅' if getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '') else '❌'}")
    logger.info(f"  Verify Token: {'✅' if getattr(config, 'WHATSAPP_VERIFY_TOKEN', '') else '❌'}")
    logger.info(f"  Thread Pool: {MAX_WORKERS} workers")
    logger.info(f"  Timeout: {PROCESSING_TIMEOUT_SECONDS}s")
    logger.info("=" * 60)
    return {"services_loaded": 1, "version": "18.1"}

logger.info(f"Webhook v18.1 ready | Env: {getattr(config, 'ENVIRONMENT', 'development')} | Workers: {MAX_WORKERS}")
