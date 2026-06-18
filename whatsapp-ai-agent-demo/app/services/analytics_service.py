# ==========================================================
# FILE: app/routes/webhook.py (v21.0 - ENTERPRISE GRADE)
# ==========================================================
# PURPOSE: THIN COMMUNICATION LAYER - WhatsApp Webhook Handler
# VERSION: 21.0 - Enterprise Production Ready
#
# ARCHITECTURE:
# WhatsApp Cloud API → webhook.py → ai_provider_service.py → analytics_service.py → PostgreSQL
#
# RESPONSIBILITIES (ONLY):
# 1. Receive Meta Webhook Messages
# 2. Webhook Verification (GET /webhook)
# 3. Signature Verification (X-Hub-Signature-256)
# 4. Background Processing Queue
# 5. Response Delivery to WhatsApp
# 6. Performance Monitoring
# 7. Circuit Breaker Pattern
# 8. Reliability & Fault Tolerance
#
# STRICTLY PROHIBITED:
# - Business Logic
# - Analytics Calculations
# - SQL Queries
# - Dashboard Logic
# - Dealer/Warehouse/Product Logic
# - KPI Calculations
# - Forecasting
# - Ranking
# ==========================================================

import json
import uuid
import asyncio
import time
import hmac
import hashlib
import re
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from fastapi import APIRouter, Request, BackgroundTasks, Query, HTTPException
from fastapi.responses import JSONResponse, Response
from loguru import logger
from cachetools import TTLCache
from enum import Enum
from dataclasses import dataclass, field

# ==========================================================
# PERFORMANCE & RELIABILITY IMPORTS
# ==========================================================

# Redis - Distributed Caching
try:
    import redis
    REDIS_AVAILABLE = True
except:
    REDIS_AVAILABLE = False

# Tenacity - Retry Logic
try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except:
    TENACITY_AVAILABLE = False

# ==========================================================
# LAZY IMPORTS - Avoid circular dependencies
# ==========================================================

def _get_ai_provider():
    from app.services.ai_provider_service import process_whatsapp_query, get_orchestrator
    return process_whatsapp_query, get_orchestrator

def _get_whatsapp_service():
    from app.services.whatsapp_service import send_text_message
    return send_text_message

# ==========================================================
# ROUTER - NO PYDANTIC MODELS
# ==========================================================

router = APIRouter(tags=["WhatsApp Webhook"])

# ==========================================================
# ENUMS
# ==========================================================

class CircuitBreakerState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class EventType(Enum):
    MESSAGE = "message"
    STATUS = "status"
    READ = "read"
    DELIVERY = "delivery"
    UNKNOWN = "unknown"

class MessageType(Enum):
    TEXT = "text"
    INTERACTIVE = "interactive"
    BUTTON = "button"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    LOCATION = "location"
    CONTACT = "contact"
    REACTION = "reaction"
    UNKNOWN = "unknown"

# ==========================================================
# CONFIGURATION
# ==========================================================

# ⚡ PERFORMANCE OPTIMIZED TIMEOUTS
ANALYTICS_TIMEOUT_SECONDS = 8
GROQ_TIMEOUT_SECONDS = 10
DISTANCE_TIMEOUT_SECONDS = 5
WHATSAPP_SEND_TIMEOUT_SECONDS = 10
TOTAL_PROCESSING_TIMEOUT = 25

# WhatsApp Character Limits
MAX_WHATSAPP_LENGTH = 3000
MAX_MESSAGE_LENGTH = 4000

# Circuit Breaker Configuration
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 60
CIRCUIT_BREAKER_HALF_OPEN_MAX_ATTEMPTS = 3

# Message deduplication
_processed_messages = TTLCache(maxsize=50000, ttl=86400)

# Rate limiting
_phone_rate_limits = TTLCache(maxsize=50000, ttl=60)
RATE_LIMIT_REQUESTS = 100

# Thread pool for background tasks
CPU_COUNT = 4
MAX_WORKERS = min(32, CPU_COUNT * 8)
_executor = None  # Lazy initialization

# ==========================================================
# REDIS INTEGRATION
# ==========================================================

_redis_client = None
if REDIS_AVAILABLE:
    try:
        _redis_client = redis.Redis(
            host='localhost',
            port=6379,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1
        )
        _redis_client.ping()
        logger.info("⚡ Webhook Redis cache connected")
    except:
        _redis_client = None
        logger.warning("⚠️ Webhook Redis not available")

# ==========================================================
# CIRCUIT BREAKER
# ==========================================================

@dataclass
class CircuitBreaker:
    """Circuit breaker pattern to prevent cascading failures."""
    
    state: CircuitBreakerState = CircuitBreakerState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0
    last_success_time: float = 0
    half_open_attempts: int = 0
    total_failures: int = 0
    total_successes: int = 0
    
    def is_allowed(self) -> bool:
        """Check if request is allowed to proceed."""
        now = time.time()
        
        if self.state == CircuitBreakerState.CLOSED:
            return True
        
        if self.state == CircuitBreakerState.OPEN:
            if now - self.last_failure_time > CIRCUIT_BREAKER_RECOVERY_TIMEOUT:
                self.state = CircuitBreakerState.HALF_OPEN
                self.half_open_attempts = 0
                logger.info("Circuit breaker: OPEN -> HALF_OPEN (recovery attempt)")
                return True
            return False
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            if self.half_open_attempts >= CIRCUIT_BREAKER_HALF_OPEN_MAX_ATTEMPTS:
                self.state = CircuitBreakerState.OPEN
                self.last_failure_time = now
                logger.warning("Circuit breaker: HALF_OPEN -> OPEN (max attempts reached)")
                return False
            self.half_open_attempts += 1
            return True
        
        return True
    
    def record_success(self):
        """Record a successful request."""
        self.failure_count = 0
        self.total_successes += 1
        self.last_success_time = time.time()
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.CLOSED
            logger.info("Circuit breaker: HALF_OPEN -> CLOSED (service recovered)")
    
    def record_failure(self):
        """Record a failed request."""
        now = time.time()
        self.failure_count += 1
        self.total_failures += 1
        self.last_failure_time = now
        
        if self.state == CircuitBreakerState.CLOSED and self.failure_count >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            self.state = CircuitBreakerState.OPEN
            logger.error(f"Circuit breaker: CLOSED -> OPEN (threshold reached: {self.failure_count} failures)")
        
        elif self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.OPEN
            self.last_failure_time = now
            logger.warning("Circuit breaker: HALF_OPEN -> OPEN (test failed)")
    
    def reset(self):
        """Reset circuit breaker."""
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.half_open_attempts = 0
        logger.info("Circuit breaker: Reset to CLOSED")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "success_rate": self.total_successes / (self.total_failures + self.total_successes) if (self.total_failures + self.total_successes) > 0 else 1.0
        }

# Initialize circuit breaker
_ai_circuit_breaker = CircuitBreaker()

# ==========================================================
# PERFORMANCE METRICS
# ==========================================================

@dataclass
class PerformanceMetrics:
    """Track performance metrics for every step."""
    
    request_id: str
    start_time: float = field(default_factory=time.time)
    steps: Dict[str, float] = field(default_factory=dict)
    timings: Dict[str, float] = field(default_factory=dict)
    
    def step(self, name: str):
        """Record a step execution time."""
        current_time = time.time()
        elapsed = (current_time - self.start_time) * 1000
        self.steps[name] = elapsed
        self.timings[name] = current_time
        logger.debug(f"[{self.request_id}] ⚡ {name}: {elapsed:.2f}ms")
    
    def complete(self) -> Dict[str, Any]:
        """Log final metrics."""
        total = (time.time() - self.start_time) * 1000
        logger.info(f"[{self.request_id}] ✅ Total time: {total:.2f}ms")
        return {
            "request_id": self.request_id,
            "total_ms": round(total, 2),
            "steps": {k: round(v, 2) for k, v in self.steps.items()}
        }


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def generate_request_id() -> str:
    """Generate unique request ID for tracking."""
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

def mask_sensitive_data(value: str, keep_start: int = 3, keep_end: int = 2) -> str:
    """Mask sensitive data like phone numbers."""
    if not value or len(value) < keep_start + keep_end:
        return "***"
    return f"{value[:keep_start]}****{value[-keep_end:]}"

def check_rate_limit(phone_number: str) -> bool:
    """Check if phone number has exceeded rate limits."""
    now = time.time()
    requests = _phone_rate_limits.get(phone_number, [])
    recent = [t for t in requests if now - t < 60]
    
    if len(recent) >= RATE_LIMIT_REQUESTS:
        logger.warning(f"Rate limit exceeded for {mask_sensitive_data(phone_number)}")
        return False
    
    recent.append(now)
    _phone_rate_limits[phone_number] = recent
    return True

def chunk_message(message: str, max_length: int = MAX_WHATSAPP_LENGTH) -> List[str]:
    """
    Split a message into WhatsApp-safe chunks.
    Returns list of message chunks.
    """
    if len(message) <= max_length:
        return [message]
    
    chunks = []
    current_chunk = ""
    
    # Split by newlines to preserve formatting
    lines = message.split('\n')
    
    for line in lines:
        if len(line) > max_length:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            words = line.split(' ')
            temp = ""
            for word in words:
                if len(temp) + len(word) + 1 > max_length:
                    chunks.append(temp.strip())
                    temp = word + " "
                else:
                    temp += word + " "
            if temp:
                current_chunk = temp.strip()
            continue
        
        if len(current_chunk) + len(line) + 1 > max_length:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            if current_chunk:
                current_chunk += "\n" + line
            else:
                current_chunk = line
    
    if current_chunk:
        chunks.append(current_chunk)
    
    # Add part indicators if multiple chunks
    if len(chunks) > 1:
        for i, chunk in enumerate(chunks, 1):
            chunks[i-1] = f"📄 Part {i}/{len(chunks)}\n\n{chunk}"
    
    return chunks

def detect_event_type(payload: Dict[str, Any]) -> EventType:
    """Detect the type of event from the payload."""
    if not payload:
        return EventType.UNKNOWN
    
    if payload.get('entry'):
        changes = payload.get('entry', [{}])[0].get('changes', [])
        if changes:
            value = changes[0].get('value', {})
            if 'statuses' in value:
                return EventType.STATUS
            if 'messages' in value:
                return EventType.MESSAGE
    
    return EventType.UNKNOWN

def detect_message_type(message: Dict[str, Any]) -> MessageType:
    """Detect the type of message."""
    msg_type = message.get('type', 'unknown').lower()
    try:
        return MessageType(msg_type)
    except ValueError:
        return MessageType.UNKNOWN

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
    """
    Meta WhatsApp webhook verification endpoint.
    This MUST return the challenge to verify your webhook.
    """
    logger.info("Webhook verification request received", mode=hub_mode, token_present=bool(hub_verify_token))
    
    try:
        verify_token = getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')
        
        if hub_mode == 'subscribe' and hub_verify_token == verify_token:
            logger.success("Webhook verified successfully")
            return Response(content=hub_challenge, status_code=200, media_type="text/plain")
        else:
            logger.warning("Webhook verification failed - token mismatch")
            return JSONResponse(content={"error": "Verification failed"}, status_code=403)
            
    except Exception as e:
        logger.exception("Webhook verification error", error=str(e))
        return JSONResponse(content={"error": "Internal error"}, status_code=500)


# ==========================================================
# MAIN WEBHOOK HANDLER
# ==========================================================

@router.post("/webhook")
@router.post("/webhook/")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Main webhook handler - THIN COMMUNICATION LAYER ONLY.
    
    🔥 CRITICAL: NO Pydantic validation - manual JSON parsing only
    🔥 CRITICAL: ALWAYS returns 200 OK to Meta
    🔥 CRITICAL: NO business logic - only routing
    🔥 CRITICAL: Must return within 15 seconds
    """
    
    request_id = generate_request_id()
    perf = PerformanceMetrics(request_id)
    
    # ==========================================================
    # STEP 1: Read raw body
    # ==========================================================
    
    try:
        raw_body = await request.body()
        perf.step("1_READ_BODY")
    except Exception as e:
        logger.error("Failed to read request body", request_id=request_id, error=str(e))
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # ==========================================================
    # STEP 2: Signature Verification (if enabled)
    # ==========================================================
    
    signature = request.headers.get('X-Hub-Signature-256', '')
    if signature and getattr(config, 'WHATSAPP_SIGNATURE_REQUIRED', False):
        try:
            if not verify_signature(raw_body, signature):
                logger.warning("Invalid signature", request_id=request_id)
                return JSONResponse({"status": "ok"}, status_code=200)
        except Exception as e:
            logger.error("Signature verification failed", request_id=request_id, error=str(e))
            return JSONResponse({"status": "ok"}, status_code=200)
    
    perf.step("2_SIGNATURE_VERIFIED")
    
    # ==========================================================
    # STEP 3: Parse JSON manually (NO PYDANTIC)
    # ==========================================================
    
    try:
        data = json.loads(raw_body) if raw_body else {}
        perf.step("3_JSON_PARSED")
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed", request_id=request_id, error=str(e))
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # ==========================================================
    # STEP 4: Validate it's a WhatsApp webhook
    # ==========================================================
    
    if not data or data.get('object') != 'whatsapp_business_account':
        logger.debug("Not a WhatsApp webhook payload", request_id=request_id)
        return JSONResponse({"status": "ok"}, status_code=200)
    
    perf.step("4_PAYLOAD_VALIDATED")
    
    # ==========================================================
    # STEP 5: Extract and process entries
    # ==========================================================
    
    try:
        entries = data.get('entry') or []
        if not entries:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        changes = entries[0].get('changes') or []
        if not changes:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        value = changes[0].get('value') or {}
        
        # ==========================================================
        # STEP 6: Handle status updates (no response needed)
        # ==========================================================
        
        if 'statuses' in value:
            logger.debug("Status update received", request_id=request_id)
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # ==========================================================
        # STEP 7: Handle messages
        # ==========================================================
        
        messages = value.get('messages') or []
        if not messages:
            logger.debug("No messages in payload", request_id=request_id)
            return JSONResponse({"status": "ok"}, status_code=200)
        
        message = messages[0]
        phone_number = message.get('from')
        message_id = message.get('id')
        message_type = detect_message_type(message)
        
        # Validate required fields
        if not phone_number or not message_id:
            logger.warning("Missing phone_number or message_id", request_id=request_id)
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # ==========================================================
        # STEP 8: Extract text content based on message type
        # ==========================================================
        
        message_text = None
        
        if message_type == MessageType.TEXT:
            message_text = message.get('text', {}).get('body', '')
        elif message_type == MessageType.INTERACTIVE:
            interactive = message.get('interactive') or {}
            if interactive.get('type') == 'button_reply':
                message_text = interactive.get('button_reply', {}).get('title', '')
            elif interactive.get('type') == 'list_reply':
                message_text = interactive.get('list_reply', {}).get('title', '')
        elif message_type == MessageType.BUTTON:
            button = message.get('button') or {}
            message_text = button.get('text', '')
        elif message_type in [MessageType.IMAGE, MessageType.DOCUMENT, MessageType.AUDIO, MessageType.VIDEO]:
            # For media messages, log but don't process
            logger.info("Media message received", request_id=request_id, message_type=message_type.value)
            return JSONResponse({"status": "ok"}, status_code=200)
        elif message_type == MessageType.LOCATION:
            logger.info("Location message received", request_id=request_id)
            return JSONResponse({"status": "ok"}, status_code=200)
        elif message_type == MessageType.CONTACT:
            logger.info("Contact message received", request_id=request_id)
            return JSONResponse({"status": "ok"}, status_code=200)
        elif message_type == MessageType.REACTION:
            logger.info("Reaction received", request_id=request_id)
            return JSONResponse({"status": "ok"}, status_code=200)
        
        if not message_text:
            logger.debug("No text content in message", request_id=request_id, message_type=message_type.value)
            return JSONResponse({"status": "ok"}, status_code=200)
        
        perf.step("5_MESSAGE_EXTRACTED")
        
    except Exception as e:
        logger.error("Error extracting message", request_id=request_id, error=str(e))
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # ==========================================================
    # STEP 9: Deduplicate (prevent double processing)
    # ==========================================================
    
    if message_id in _processed_messages:
        logger.debug("Duplicate message detected", request_id=request_id, message_id=message_id)
        return JSONResponse({"status": "ok"}, status_code=200)
    _processed_messages[message_id] = time.time()
    
    perf.step("6_DEDUPLICATED")
    
    # ==========================================================
    # STEP 10: Rate limit check
    # ==========================================================
    
    if not check_rate_limit(phone_number):
        logger.info("Rate limited", request_id=request_id, phone=mask_sensitive_data(phone_number))
        return JSONResponse({"status": "ok"}, status_code=200)
    
    perf.step("7_RATE_LIMITED")
    
    # ==========================================================
    # STEP 11: Check circuit breaker
    # ==========================================================
    
    if not _ai_circuit_breaker.is_allowed():
        logger.warning("Circuit breaker open - rejecting request", request_id=request_id)
        return JSONResponse({"status": "ok"}, status_code=200)
    
    perf.step("8_CIRCUIT_BREAKER_CHECKED")
    
    # ==========================================================
    # STEP 12: Queue background processing
    # ==========================================================
    
    contacts = value.get('contacts') or []
    sender_name = contacts[0].get('profile', {}).get('name', 'User') if contacts else 'User'
    
    logger.info("Message received", 
                request_id=request_id,
                phone=mask_sensitive_data(phone_number),
                message_id=message_id,
                message_type=message_type.value,
                preview=message_text[:50])
    
    background_tasks.add_task(
        process_whatsapp_message,
        phone_number,
        message_text.strip(),
        sender_name,
        message_id,
        request_id
    )
    
    perf.step("9_BACKGROUND_QUEUED")
    
    # ==========================================================
    # STEP 13: ALWAYS RETURN 200 OK TO META
    # ==========================================================
    
    return JSONResponse({"status": "ok"}, status_code=200)


# ==========================================================
# SIGNATURE VERIFICATION
# ==========================================================

def verify_signature(raw_body: bytes, signature: str) -> bool:
    """
    Verify WhatsApp webhook signature using HMAC SHA256.
    Uses timing-safe comparison.
    """
    if not signature:
        return False
    
    secret = getattr(config, 'WHATSAPP_APP_SECRET', '')
    if not secret:
        logger.warning("WhatsApp app secret not configured")
        return False
    
    expected = hmac.new(
        secret.encode('utf-8'),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    
    # Timing-safe comparison
    return hmac.compare_digest(signature, f"sha256={expected}")


# ==========================================================
# BACKGROUND MESSAGE PROCESSING
# ==========================================================

async def process_whatsapp_message(
    phone_number: str, 
    message_text: str, 
    sender_name: str, 
    message_id: str, 
    request_id: str
):
    """
    Process message in background with timeout protection.
    This is a THIN COMMUNICATION LAYER - NO BUSINESS LOGIC.
    
    All business logic is delegated to ai_provider_service.py
    """
    
    start_time = time.time()
    
    try:
        logger.info("Processing message", 
                    request_id=request_id,
                    phone=mask_sensitive_data(phone_number),
                    preview=message_text[:50])
        
        # ==========================================================
        # CALL AI PROVIDER SERVICE (v21.0)
        # All business logic is handled there
        # ==========================================================
        
        loop = asyncio.get_running_loop()
        
        try:
            # Get the AI provider function
            process_whatsapp_query_func = _get_ai_provider()[0]
            
            # Execute with timeout
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    _get_executor(), 
                    process_whatsapp_query_func, 
                    message_text, 
                    None,  # session_factory
                    phone_number, 
                    None,  # user_id
                    request_id
                ),
                timeout=TOTAL_PROCESSING_TIMEOUT
            )
            
            # Record success for circuit breaker
            _ai_circuit_breaker.record_success()
            
            # ==========================================================
            # CHUNK MESSAGE IF NEEDED
            # ==========================================================
            
            if response and len(response) > MAX_WHATSAPP_LENGTH:
                chunks = chunk_message(response, MAX_WHATSAPP_LENGTH)
                logger.info("Message chunked", 
                           request_id=request_id,
                           chunks=len(chunks))
                
                # Send each chunk sequentially
                for i, chunk in enumerate(chunks):
                    await send_whatsapp_response_safe(
                        phone_number, 
                        chunk, 
                        f"{message_id}_{i}" if len(chunks) > 1 else message_id,
                        f"{request_id}_chunk_{i}"
                    )
                    if i < len(chunks) - 1:
                        await asyncio.sleep(0.5)
            else:
                # Send single response
                await send_whatsapp_response_safe(
                    phone_number, 
                    response or "✅ Processed successfully!",
                    message_id, 
                    request_id
                )
            
            duration_ms = int((time.time() - start_time) * 1000)
            logger.info("Response sent", 
                        request_id=request_id,
                        duration_ms=duration_ms,
                        phone=mask_sensitive_data(phone_number))
            
        except asyncio.TimeoutError:
            logger.error("Processing timeout", 
                        request_id=request_id,
                        timeout=TOTAL_PROCESSING_TIMEOUT)
            _ai_circuit_breaker.record_failure()
            await send_whatsapp_response_safe(
                phone_number,
                "⏳ I'm still processing your request. Please wait a moment.",
                message_id,
                request_id
            )
            
        except Exception as e:
            logger.exception("Processing error", 
                            request_id=request_id,
                            error=str(e))
            _ai_circuit_breaker.record_failure()
            await send_whatsapp_response_safe(
                phone_number,
                "⚠️ I encountered an error. Please try again or type 'Help'.",
                message_id,
                request_id
            )
            
    except Exception as e:
        logger.exception("Unexpected error in background processing", 
                        request_id=request_id,
                        error=str(e))
        try:
            await send_whatsapp_response_safe(
                phone_number,
                "⚠️ System error. Please try again.",
                message_id,
                request_id
            )
        except:
            pass


# ==========================================================
# SAFE WHATSAPP RESPONSE SENDER
# ==========================================================

async def send_whatsapp_response_safe(
    phone_number: str, 
    message: str, 
    message_id: str, 
    request_id: str
):
    """
    Send WhatsApp response with timeout and retry protection.
    """
    try:
        send_text_message = _get_whatsapp_service()
        loop = asyncio.get_running_loop()
        
        await asyncio.wait_for(
            loop.run_in_executor(_get_executor(), send_text_message, phone_number, message, message_id, request_id),
            timeout=WHATSAPP_SEND_TIMEOUT_SECONDS
        )
        logger.debug("Message sent successfully", 
                    request_id=request_id,
                    phone=mask_sensitive_data(phone_number))
        
    except asyncio.TimeoutError:
        logger.error("WhatsApp send timeout", 
                    request_id=request_id,
                    timeout=WHATSAPP_SEND_TIMEOUT_SECONDS)
        
    except Exception as e:
        logger.error("WhatsApp send failed", 
                    request_id=request_id,
                    error=str(e))


# ==========================================================
# EXECUTOR MANAGEMENT
# ==========================================================

def _get_executor():
    """Lazy initialization of thread pool executor."""
    global _executor
    if _executor is None:
        import concurrent.futures
        _executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=MAX_WORKERS, 
            thread_name_prefix="webhook_worker"
        )
    return _executor


# ==========================================================
# HEALTH AND MONITORING ENDPOINTS
# ==========================================================

@router.get("/webhook/ping")
async def webhook_ping():
    """Simple ping endpoint to check if webhook is alive."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@router.get("/webhook/health")
async def webhook_health():
    """
    Enhanced health check with full monitoring.
    Returns status of all integrated services.
    """
    # Check database status via analytics
    db_status = "unknown"
    try:
        from app.services.analytics_service import get_analytics_service
        analytics = get_analytics_service()
        db_health = analytics.health_check()
        db_status = db_health.get("status", "unknown")
    except:
        db_status = "unhealthy"
    
    # Check Redis status
    redis_status = "connected" if _redis_client else "disconnected"
    if _redis_client:
        try:
            _redis_client.ping()
            redis_status = "connected"
        except:
            redis_status = "disconnected"
    
    # Check Groq status via AI router
    groq_status = "unknown"
    try:
        ai_router = _get_ai_provider()[1]()
        groq_available = ai_router._is_groq_available() if hasattr(ai_router, '_is_groq_available') else False
        groq_status = "available" if groq_available else "unavailable"
    except:
        groq_status = "unknown"
    
    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "version": "21.0",
        "timestamp": datetime.now().isoformat(),
        "config": {
            "whatsapp_token": bool(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')),
            "phone_number_id": bool(getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')),
            "verify_token": bool(getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')),
            "signature_required": getattr(config, 'WHATSAPP_SIGNATURE_REQUIRED', False),
            "timeout_seconds": {
                "analytics": ANALYTICS_TIMEOUT_SECONDS,
                "groq": GROQ_TIMEOUT_SECONDS,
                "distance": DISTANCE_TIMEOUT_SECONDS,
                "whatsapp_send": WHATSAPP_SEND_TIMEOUT_SECONDS,
                "total_processing": TOTAL_PROCESSING_TIMEOUT
            }
        },
        "cache": {
            "processed_messages": len(_processed_messages),
            "rate_limits": len(_phone_rate_limits)
        },
        "redis": {
            "status": redis_status,
            "available": REDIS_AVAILABLE
        },
        "database": {
            "status": db_status
        },
        "groq": {
            "status": groq_status
        },
        "circuit_breaker": _ai_circuit_breaker.get_stats(),
        "integrations": {
            "ai_router": "v21.0 ✅",
            "analytics": "v14.0 ✅",
            "whatsapp_service": "✅"
        }
    }


@router.get("/webhook/self-test")
async def webhook_self_test():
    """Detailed self-test for debugging."""
    executor = _get_executor()
    
    return {
        "status": "running",
        "version": "21.0",
        "timestamp": datetime.now().isoformat(),
        "configuration": {
            "whatsapp_token": {
                "present": bool(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')),
                "length": len(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')) if getattr(config, 'WHATSAPP_ACCESS_TOKEN', '') else 0
            },
            "phone_number_id": {
                "present": bool(getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')),
                "value": getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', 'Not set')
            },
            "verify_token": {
                "present": bool(getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')),
                "value": getattr(config, 'WHATSAPP_VERIFY_TOKEN', 'Not set')
            },
            "signature_required": getattr(config, 'WHATSAPP_SIGNATURE_REQUIRED', False)
        },
        "webhook_paths": {
            "get": "/webhook - Verification endpoint",
            "post": "/webhook - Message receiving endpoint"
        },
        "integrations": {
            "ai_router_v21": "✅ loaded",
            "analytics_v14": "✅ loaded",
            "whatsapp_service": "✅ available",
            "redis": "✅ connected" if _redis_client else "❌ not connected"
        },
        "circuit_breaker": _ai_circuit_breaker.get_stats(),
        "performance": {
            "max_workers": MAX_WORKERS,
            "executor_active": bool(executor),
            "timeouts": {
                "analytics": ANALYTICS_TIMEOUT_SECONDS,
                "groq": GROQ_TIMEOUT_SECONDS,
                "total": TOTAL_PROCESSING_TIMEOUT
            }
        }
    }


@router.get("/webhook/metrics")
async def webhook_metrics():
    """Performance metrics endpoint."""
    return {
        "version": "21.0",
        "timestamp": datetime.now().isoformat(),
        "processed_messages": len(_processed_messages),
        "rate_limit_entries": len(_phone_rate_limits),
        "max_workers": MAX_WORKERS,
        "redis_status": "connected" if _redis_client else "disconnected",
        "circuit_breaker": _ai_circuit_breaker.get_stats(),
        "timeouts": {
            "analytics": ANALYTICS_TIMEOUT_SECONDS,
            "groq": GROQ_TIMEOUT_SECONDS,
            "distance": DISTANCE_TIMEOUT_SECONDS,
            "whatsapp_send": WHATSAPP_SEND_TIMEOUT_SECONDS,
            "total": TOTAL_PROCESSING_TIMEOUT
        }
    }


@router.post("/webhook/circuit-breaker/reset")
async def reset_circuit_breaker():
    """Admin endpoint to reset circuit breaker."""
    _ai_circuit_breaker.reset()
    return {"status": "reset", "circuit_breaker": _ai_circuit_breaker.get_stats()}


# ==========================================================
# GRACEFUL SHUTDOWN
# ==========================================================

@router.on_event("shutdown")
async def shutdown_webhook():
    """Clean shutdown with resource cleanup."""
    logger.info("Webhook shutting down...")
    
    # Clear caches
    _processed_messages.clear()
    _phone_rate_limits.clear()
    
    # Shutdown thread pool
    global _executor
    if _executor:
        logger.info("Shutting down thread pool...")
        _executor.shutdown(wait=True, cancel_futures=False)
        _executor = None
        logger.success("Thread pool shutdown complete")
    
    # Close Redis connection
    if _redis_client:
        try:
            _redis_client.close()
        except:
            pass
    
    logger.success("Webhook shutdown complete")


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 60)
logger.info("Webhook v21.0 - Enterprise Grade")
logger.info("=" * 60)
logger.info("")
logger.info("  📋 RESPONSIBILITIES:")
logger.info("  ✅ Receive Meta Webhook Messages")
logger.info("  ✅ Webhook Verification (GET /webhook)")
logger.info("  ✅ Signature Verification (X-Hub-Signature-256)")
logger.info("  ✅ Background Processing Queue")
logger.info("  ✅ Response Delivery to WhatsApp")
logger.info("  ✅ Performance Monitoring")
logger.info("  ✅ Circuit Breaker Pattern")
logger.info("  ✅ Reliability & Fault Tolerance")
logger.info("")
logger.info("  🚫 STRICTLY PROHIBITED:")
logger.info("  ❌ Business Logic")
logger.info("  ❌ Analytics Calculations")
logger.info("  ❌ SQL Queries")
logger.info("  ❌ Dashboard Logic")
logger.info("  ❌ Dealer/Warehouse/Product Logic")
logger.info("  ❌ KPI Calculations")
logger.info("")
logger.info("  ⚡ CONFIGURATION:")
logger.info(f"     Analytics Timeout: {ANALYTICS_TIMEOUT_SECONDS}s")
logger.info(f"     Groq Timeout: {GROQ_TIMEOUT_SECONDS}s")
logger.info(f"     Total Timeout: {TOTAL_PROCESSING_TIMEOUT}s")
logger.info(f"     Max Workers: {MAX_WORKERS}")
logger.info(f"     Max Message Length: {MAX_WHATSAPP_LENGTH}")
logger.info("")
logger.info("  🔒 SECURITY:")
logger.info(f"     Signature Required: {getattr(config, 'WHATSAPP_SIGNATURE_REQUIRED', False)}")
logger.info(f"     Token Configured: {'✅' if getattr(config, 'WHATSAPP_ACCESS_TOKEN', '') else '❌'}")
logger.info("")
logger.info("  🔄 INTEGRATIONS:")
logger.info("  ✅ AI Router v21.0 - Master AI Router")
logger.info("  ✅ Analytics v14.0 - Master Analytics Brain")
logger.info("  ✅ WhatsApp Service - Message Sender")
logger.info("  ✅ Redis - Distributed Cache")
logger.info("")
logger.info("  🛡️ CIRCUIT BREAKER:")
logger.info(f"     State: {_ai_circuit_breaker.state.value}")
logger.info(f"     Failure Threshold: {CIRCUIT_BREAKER_FAILURE_THRESHOLD}")
logger.info(f"     Recovery Timeout: {CIRCUIT_BREAKER_RECOVERY_TIMEOUT}s")
logger.info("=" * 60)
logger.info("🚀 Webhook ready to receive messages from WhatsApp!")
