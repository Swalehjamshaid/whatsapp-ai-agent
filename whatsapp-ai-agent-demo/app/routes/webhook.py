# ==========================================================
# FILE: app/routes/webhook.py (v18.1 - WITH ROUTER)
# ==========================================================

import re
import uuid
import asyncio
import time
import os
from datetime import datetime
from typing import Dict, Any, Optional
from collections import deque
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Request, BackgroundTasks, Query, HTTPException
from fastapi.responses import JSONResponse, Response
from loguru import logger
from cachetools import TTLCache

from app.config import config
from app.services.ai_provider_service import process_whatsapp_query
from app.services.whatsapp_service import send_text_message

# ==========================================================
# ROUTER INITIALIZATION - IMPORTANT
# ==========================================================

router = APIRouter(tags=["WhatsApp Webhook"])

# ==========================================================
# CONFIGURATION
# ==========================================================

DEBUG_MODE = getattr(config, 'ENVIRONMENT', 'development') != 'production'
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
# MEMORY UTILITY (No psutil)
# ==========================================================

def get_memory_usage_mb():
    """Get memory usage in MB without psutil"""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except:
        try:
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
    def __init__(self):
        self.state = "CLOSED"
        self.failure_count = 0
        self.last_failure_time = 0
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
# CONVERSATION CONTEXT
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

def get_structured_logger(request_id: str, phone_number: str = None, message_id: str = None):
    context = {"request_id": request_id}
    if phone_number:
        context["phone"] = mask_sensitive_data(phone_number)
    if message_id:
        context["message_id"] = message_id
    return logger.bind(**context)

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

def is_duplicate_message(message_id: str) -> bool:
    if not message_id:
        return False
    if message_id in _processed_messages:
        _metrics.duplicate_messages += 1
        return True
    _processed_messages[message_id] = time.time()
    return False

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
            return False
    except Exception as e:
        logger.exception(f"[{request_id}] Direct send error: {e}")
        return False

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
# WEBHOOK VERIFICATION - GET ENDPOINT
# ==========================================================

@router.get("/webhook")
@router.get("/webhook/")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Meta WhatsApp webhook verification endpoint"""
    _metrics.verification_hits += 1
    logger.info(f"Webhook verification: mode={hub_mode}")
    try:
        verify_token = getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')
        if hub_mode == 'subscribe' and hub_verify_token == verify_token:
            logger.success("Webhook verified successfully")
            return Response(content=hub_challenge, status_code=200, media_type="text/plain")
        logger.warning("Verification failed - token mismatch")
        return JSONResponse(content={"error": "Verification failed"}, status_code=403)
    except Exception as e:
        logger.exception(f"Verification error: {e}")
        return JSONResponse(content={"error": "Internal error"}, status_code=500)

# ==========================================================
# MAIN WEBHOOK HANDLER - POST ENDPOINT
# ==========================================================

@router.post("/webhook")
@router.post("/webhook/")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """Main webhook handler - ALWAYS returns 200 to Meta"""
    _metrics.webhook_hits += 1
    try:
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
            logger.warning("Missing phone_number or message_id")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        message_text = None
        if message_type == 'text':
            message_text = message.get('text', {}).get('body', '')
        elif message_type == 'interactive':
            interactive = message.get('interactive') or {}
            if interactive.get('type') == 'button_reply':
                message_text = interactive.get('button_reply', {}).get('title', '')
        
        if not message_text:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        if is_duplicate_message(message_id):
            return JSONResponse({"status": "ok"}, status_code=200)
        
        if not check_rate_limit(phone_number):
            return JSONResponse({"status": "ok"}, status_code=200)
        
        contacts = value.get('contacts') or []
        sender_name = contacts[0].get('profile', {}).get('name', 'User') if contacts else 'User'
        
        request_id = generate_request_id()
        logger.info(f"📨 {mask_sensitive_data(phone_number)}: {message_text[:50]}...")
        
        background_tasks.add_task(
            process_whatsapp_query, 
            message_text.strip(), 
            None, 
            phone_number, 
            None, 
            request_id
        )
        
        _metrics.messages_received += 1
        _metrics.last_message_time = datetime.now()
        
        return JSONResponse({"status": "ok"}, status_code=200)
        
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return JSONResponse({"status": "ok"}, status_code=200)

# ==========================================================
# HEALTH AND DIAGNOSTICS
# ==========================================================

@router.get("/webhook/ping")
async def webhook_ping():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@router.get("/webhook/health")
async def webhook_health():
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
        'conversation_cache_size': _conversation_tracker.get_stats()["cache_size"]
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

# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("Webhook v18.1 ready")
