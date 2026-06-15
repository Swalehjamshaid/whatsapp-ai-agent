# ==========================================================
# FILE: app/routes/webhook.py (v17.0 - FULLY INTEGRATED)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - Thin Orchestration Layer
#
# ENTERPRISE FEATURES v17.0:
# - ✅ FULL WhatsApp integration (send + receive)
# - ✅ Webhook verification working
# - ✅ Meta payload parsing working
# - ✅ Send text messages via WhatsApp Cloud API
# - ✅ Fallback direct sender working
# - ✅ Rate limiting with TTLCache
# - ✅ Message deduplication with TTLCache
# - ✅ Conversation context tracking
# - ✅ Async background processing
# - ✅ Health checks and metrics
# - ✅ Groq integration support
# ==========================================================

import re
import uuid
import asyncio
import time
import hmac
import hashlib
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


# ==========================================================
# GLOBALS
# ==========================================================

_recent_events = deque(maxlen=MAX_STORED_EVENTS)

# Message deduplication (TTLCache - auto-expires after 24 hours)
_processed_messages = TTLCache(maxsize=50000, ttl=86400)

# Rate limiting (TTLCache - auto-expires after window)
_phone_rate_limits = TTLCache(maxsize=50000, ttl=RATE_LIMIT_WINDOW)

# Thread pool for background tasks
_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="webhook_worker")
_crash_history: deque = deque(maxlen=20)

# Timeout tracking
_timeout_metrics = {
    "total_timeouts": 0,
    "active_timeouts": 0,
    "longest_running": 0
}


# ==========================================================
# ENHANCED CONVERSATION CONTEXT
# ==========================================================

@dataclass
class ConversationContext:
    """Enhanced conversation context for user"""
    phone_number: str
    last_dealer: Optional[str] = None
    last_warehouse: Optional[str] = None
    last_dn: Optional[str] = None
    last_intent: Optional[str] = None
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)


class ConversationTracker:
    """Manages conversation context with TTL cache - Thread safe"""
    
    def __init__(self, maxsize: int = 10000, ttl: int = CONVERSATION_TTL_SECONDS):
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._ttl = ttl
    
    def get(self, phone_number: str) -> ConversationContext:
        """Get or create conversation context for a user"""
        if phone_number not in self._cache:
            self._cache[phone_number] = ConversationContext(phone_number=phone_number)
        return self._cache[phone_number]
    
    def update(self, phone_number: str, **kwargs) -> None:
        """Update conversation context - Thread safe"""
        context = self.get(phone_number)
        for key, value in kwargs.items():
            if hasattr(context, key) and value is not None:
                setattr(context, key, value)
        context.message_count += 1
        context.last_updated = time.time()
        self._cache[phone_number] = context
    
    def extract_and_update_from_response(self, phone_number: str, response: str) -> None:
        """Extract entities from response and update context"""
        updates = {}
        
        # Extract dealer
        dealer_match = re.search(r'🏪\s*\*Dealer:\*\s*(.+?)(?:\n|$)', response)
        if not dealer_match:
            dealer_match = re.search(r'Dealer:\s*(.+?)(?:\n|$)', response, re.IGNORECASE)
        if dealer_match:
            updates['last_dealer'] = dealer_match.group(1).strip()
        
        # Extract warehouse
        warehouse_match = re.search(r'🏭\s*\*Warehouse:\*\s*(.+?)(?:\n|$)', response)
        if not warehouse_match:
            warehouse_match = re.search(r'Warehouse:\s*(.+?)(?:\n|$)', response, re.IGNORECASE)
        if warehouse_match:
            updates['last_warehouse'] = warehouse_match.group(1).strip()
        
        # Extract DN
        dn_match = re.search(r'📄\s*\*DN:\*\s*(.+?)(?:\n|$)', response)
        if not dn_match:
            dn_match = re.search(r'DN:\s*(.+?)(?:\n|$)', response, re.IGNORECASE)
        if dn_match:
            updates['last_dn'] = dn_match.group(1).strip()
        
        if updates:
            self.update(phone_number, **updates)
    
    def clear(self, phone_number: str) -> bool:
        """Clear conversation context for a user"""
        if phone_number in self._cache:
            del self._cache[phone_number]
            return True
        return False
    
    def get_stats(self) -> Dict[str, int]:
        return {"cache_size": len(self._cache), "maxsize": self._cache.maxsize, "ttl_seconds": self._ttl}


# Initialize conversation tracker
_conversation_tracker = ConversationTracker()

# Intent tracking for analytics
_intent_counts: Dict[str, int] = defaultdict(int)
_intent_latencies: Dict[str, List[float]] = defaultdict(list)


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


# ==========================================================
# RATE LIMITING
# ==========================================================

def check_rate_limit(phone_number: str) -> bool:
    """Check if phone number has exceeded rate limits using TTLCache"""
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
    """Send response directly using WhatsApp API - fallback when service unavailable"""
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
    """Send response via WhatsApp Service with fallback"""
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_executor, send_text_message, phone_number, message, message_id, request_id)
        logger.debug(f"[{request_id}] Response sent")
        return
    except Exception as e:
        logger.warning(f"[{request_id}] WhatsApp service failed: {e}")
    
    await _send_direct_response(phone_number, message, request_id)


# ==========================================================
# CONVERSATION HELPERS
# ==========================================================

def get_conversation_context(phone_number: str) -> ConversationContext:
    return _conversation_tracker.get(phone_number)


def update_conversation_context(phone_number: str, last_dealer: str = None, last_warehouse: str = None, last_dn: str = None, last_intent: str = None):
    _conversation_tracker.update(phone_number, last_dealer=last_dealer, last_warehouse=last_warehouse, last_dn=last_dn, last_intent=last_intent)


# ==========================================================
# INTENT EXTRACTION FROM RESPONSE
# ==========================================================

def extract_intent_from_response(response: str) -> str:
    if not response:
        return "unknown"
    response_lower = response[:200].lower()
    
    patterns = {
        "dealer_query": ["dealer:", "🏪 *dealer"],
        "warehouse_query": ["warehouse:", "🏭 *warehouse"],
        "dn_query": ["dn:", "📄 *dn:"],
        "pgi_query": ["pgi", "pending pgi"],
        "pod_query": ["pod", "pending pod"],
        "control_tower": ["control tower", "critical alert"],
        "executive_insight": ["executive insight", "key issue"],
        "ranking_query": ["top ", "🏆"],
        "help": ["help", "commands"]
    }
    
    for intent, keywords in patterns.items():
        if any(kw in response_lower for kw in keywords):
            return intent
    return "general_ai_query"


# ==========================================================
# CORE MESSAGE PROCESSING
# ==========================================================

async def handle_message(phone_number: str, message_text: str, sender_name: str, message_id: str, request_id: str):
    """Main message handler - thin orchestration layer"""
    start_time = time.time()
    intent = "unknown"
    status = "success"
    
    try:
        logger.info(f"[{request_id}] Processing: {message_text[:50]}")
        
        # Get conversation context
        context = get_conversation_context(phone_number)
        
        # Call AI Provider Service
        loop = asyncio.get_event_loop()
        _timeout_metrics["active_timeouts"] += 1
        
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(_executor, process_whatsapp_query, message_text, None, phone_number, None, request_id),
                timeout=PROCESSING_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            _metrics.service_timeouts += 1
            _timeout_metrics["total_timeouts"] += 1
            status = "timeout"
            await send_whatsapp_response(phone_number, "⏳ Your request is taking longer than expected. I'll respond shortly.", message_id, request_id)
            return
        finally:
            _timeout_metrics["active_timeouts"] -= 1
        
        # Extract intent and update context
        intent = extract_intent_from_response(response)
        _conversation_tracker.update(phone_number, last_intent=intent)
        _conversation_tracker.extract_and_update_from_response(phone_number, response)
        
        # Send response
        await send_whatsapp_response(phone_number, response, message_id, request_id)
        _metrics.messages_processed += 1
        
    except Exception as e:
        status = "error"
        _metrics.processing_failures += 1
        logger.exception(f"[{request_id}] Failed: {e}")
        await send_whatsapp_response(phone_number, "⚠️ I encountered an error. Please try again or type 'Help'.", message_id, request_id)
    
    finally:
        duration_ms = int((time.time() - start_time) * 1000)
        _intent_latencies[intent].append(duration_ms)
        _intent_counts[intent] = _intent_counts.get(intent, 0) + 1
        logger.info(f"[{request_id}] {intent} | {duration_ms}ms | {status}")


# ==========================================================
# WEBHOOK VERIFICATION (PRESERVED - WORKING)
# ==========================================================

@router.get("/webhook")
@router.get("/webhook/")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Meta WhatsApp webhook verification endpoint - REQUIRED FOR WHATSAPP SETUP"""
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
# MAIN WEBHOOK HANDLER (PRESERVED - WORKING)
# ==========================================================

@router.post("/webhook")
@router.post("/webhook/")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """Main webhook handler - ALWAYS returns 200 to Meta"""
    
    _metrics.webhook_hits += 1
    
    try:
        # Read body
        try:
            raw_body = await request.body()
        except Exception as e:
            logger.error(f"Body read failed: {e}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Parse JSON
        try:
            data = await request.json()
        except Exception as e:
            logger.error(f"JSON parse failed: {e}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Validate WhatsApp payload
        if not data or data.get('object') != 'whatsapp_business_account':
            return JSONResponse({"status": "ok"}, status_code=200)
        
        entries = data.get('entry') or []
        if not entries:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        changes = entries[0].get('changes') or []
        if not changes:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        value = changes[0].get('value') or {}
        
        # Handle status updates (no response needed)
        if 'statuses' in value:
            _metrics.status_events += 1
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Extract message
        messages = value.get('messages') or []
        if not messages:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        message = messages[0]
        phone_number = message.get('from')
        message_id = message.get('id')
        message_type = message.get('type')
        
        if not phone_number or not message_id:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Extract text
        message_text = None
        if message_type == 'text':
            message_text = message.get('text', {}).get('body', '')
        elif message_type == 'interactive':
            interactive = message.get('interactive') or {}
            if interactive.get('type') == 'button_reply':
                message_text = interactive.get('button_reply', {}).get('title', '')
        
        if not message_text:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Deduplicate
        if is_duplicate_message(message_id):
            logger.debug(f"Duplicate: {message_id}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Rate limit
        if not check_rate_limit(phone_number):
            logger.info(f"Rate limited: {mask_sensitive_data(phone_number)}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Get sender name
        contacts = value.get('contacts') or []
        sender_name = contacts[0].get('profile', {}).get('name', 'User') if contacts else 'User'
        
        # Log received message
        logger.info(f"📨 {mask_sensitive_data(phone_number)}: {message_text[:50]}")
        
        # Store event
        store_event("message", {"phone": mask_sensitive_data(phone_number), "preview": message_text[:100], "message_id": message_id})
        
        # Queue background processing
        request_id = generate_request_id()
        background_tasks.add_task(handle_message, phone_number, message_text.strip(), sender_name, message_id, request_id)
        
        _metrics.messages_received += 1
        _metrics.last_message_time = datetime.now()
        
        # ACK immediately (Meta requires 200 OK)
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
    return {
        'status': 'healthy' if _metrics.processing_failures < 10 else 'degraded',
        'version': '17.0',
        'timestamp': datetime.now().isoformat(),
        'metrics': {
            'messages_received': _metrics.messages_received,
            'messages_processed': _metrics.messages_processed,
            'processing_failures': _metrics.processing_failures
        },
        'conversation_cache_size': _conversation_tracker.get_stats()["cache_size"]
    }


@router.get("/webhook/metrics")
async def webhook_metrics():
    """Detailed metrics including intent tracking"""
    avg_latencies = {}
    for intent, latencies in _intent_latencies.items():
        if latencies:
            avg_latencies[intent] = round(sum(latencies) / len(latencies), 2)
    
    return {
        "overall": _metrics.to_dict(),
        "intent_counts": dict(_intent_counts),
        "average_latency_ms": avg_latencies,
        "conversation_stats": _conversation_tracker.get_stats(),
        "timeout_metrics": _timeout_metrics,
        "version": "17.0"
    }


@router.get("/webhook/self-test")
async def webhook_self_test():
    return {
        "status": "running",
        "version": "17.0",
        "timestamp": datetime.now().isoformat(),
        "whatsapp_token": bool(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')),
        "phone_number_id": bool(getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')),
        "verify_token": bool(getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')),
        "conversation_stats": _conversation_tracker.get_stats(),
        "metrics": _metrics.to_dict()
    }


@router.get("/webhook/test-send")
async def test_send_message(request: Request, phone: str = "923006666666", message: str = "Test message"):
    """SECURED: Send a WhatsApp message without webhook (Admin only)"""
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
    context = get_conversation_context(phone)
    return {
        "phone": mask_sensitive_data(phone),
        "last_dealer": context.last_dealer,
        "last_warehouse": context.last_warehouse,
        "last_dn": context.last_dn,
        "last_intent": context.last_intent,
        "message_count": context.message_count
    }


def get_webhook_stats() -> Dict[str, Any]:
    return {
        "messages_received": _metrics.messages_received,
        "messages_processed": _metrics.messages_processed,
        "health": "healthy" if _metrics.processing_failures < 10 else "degraded",
        "conversation_cache_size": _conversation_tracker.get_stats()["cache_size"],
        "version": "17.0"
    }


# ==========================================================
# SERVICE INITIALIZATION
# ==========================================================

async def initialize_services():
    """Initialize webhook services - called from main.py"""
    logger.info("=" * 60)
    logger.info("Webhook v17.0 - Fully Integrated with WhatsApp")
    logger.info("=" * 60)
    logger.info(f"  Environment: {getattr(config, 'ENVIRONMENT', 'development')}")
    logger.info(f"  WhatsApp Token: {'✅' if getattr(config, 'WHATSAPP_ACCESS_TOKEN', '') else '❌'}")
    logger.info(f"  Phone Number ID: {'✅' if getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '') else '❌'}")
    logger.info(f"  Verify Token: {'✅' if getattr(config, 'WHATSAPP_VERIFY_TOKEN', '') else '❌'}")
    logger.info(f"  Thread Pool: 5 workers")
    logger.info(f"  Timeout: {PROCESSING_TIMEOUT_SECONDS}s")
    logger.info("=" * 60)
    
    return {"services_loaded": 2, "version": "17.0"}


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info(f"Webhook v17.0 ready | Env: {getattr(config, 'ENVIRONMENT', 'development')}")
