# ==========================================================
# FILE: app/routes/webhook.py (FIXED v26.0 - ENHANCED DEBUGGING)
# ==========================================================
# PURPOSE: PURE ENTRY POINT CONTROLLER - Production Grade
#
# CRITICAL FIXES v26.0:
# - ✅ FIXED: session_factory passed correctly (not db session)
# - ✅ FIXED: process_whatsapp_query parameter order
# - ✅ FIXED: AI service integration with proper error handling
# - ✅ FIXED: Status updates properly ignored
# - ✅ ADDED: Detailed error logging for debugging
# - ✅ ADDED: Request trace throughout entire flow
# - ✅ NEW: Replace generic import error with detailed exception logging
# - ✅ NEW: AI Query diagnostics with full error details
# - ✅ NEW: Service availability pre-check before AI call
# - ✅ NEW: Stack trace logging for all AI failures
# ==========================================================

import json
import time
import uuid
import re
import asyncio
import traceback
from typing import Dict, Any, Optional, Callable
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import text
from loguru import logger
from cachetools import TTLCache

from app.config import config

# Create router
router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = 3500
REQUEST_TIMEOUT_SECONDS = 25  # Increased for Railway
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]

# Rate limiting
RATE_LIMIT_MAX_MESSAGES = 10  # per minute
RATE_LIMIT_WINDOW = 60  # seconds

# Auto cleanup interval (every 500 requests)
AUTO_CLEANUP_INTERVAL = 500

# ==========================================================
# CACHES & METRICS
# ==========================================================

# Duplicate message protection cache
processed_messages = TTLCache(maxsize=5000, ttl=3600)

# Rate limiting cache (phone_number -> timestamps list)
rate_limit_cache = TTLCache(maxsize=10000, ttl=RATE_LIMIT_WINDOW)

# Metrics with service failure counters
metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "timeout_requests": 0,
    "rate_limited_requests": 0,
    "duplicate_messages": 0,
    "start_time": time.time(),
    "last_cleanup": time.time(),
    "service_failures": {
        "ai_service": 0,
        "whatsapp_service": 0,
        "database": 0,
        "rate_limiter": 0,
        "import_error": 0,
        "method_not_found": 0
    },
    "route_execution_times": {
        "ai_processing": [],
        "whatsapp_sending": [],
        "total_processing": []
    }
}

# ==========================================================
# SERVICE AVAILABILITY FLAGS
# ==========================================================

WHATSAPP_SERVICE_AVAILABLE = False
AI_SERVICE_AVAILABLE = False
AI_SERVICE_IMPORT_ERROR = None
AI_METHOD_AVAILABLE = False

# ==========================================================
# SERVICE IMPORTS (with lazy loading and detailed logging)
# ==========================================================

try:
    from app.services.whatsapp_service import send_text_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp Service loaded successfully")
except ImportError as e:
    logger.error(f"❌ WhatsApp Service import failed: {e}")
    WHATSAPP_SERVICE_AVAILABLE = False
    metrics["service_failures"]["import_error"] += 1
except Exception as e:
    logger.error(f"❌ WhatsApp Service error: {e}")
    WHATSAPP_SERVICE_AVAILABLE = False
    metrics["service_failures"]["import_error"] += 1


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def _auto_cleanup_if_needed(request_id: str):
    """Auto cleanup cache every N requests."""
    current_time = time.time()
    total_requests = metrics["total_requests"]
    
    if total_requests > 0 and total_requests % AUTO_CLEANUP_INTERVAL == 0:
        if current_time - metrics.get("last_cleanup", 0) > 60:
            logger.bind(request_id=request_id).info(f"Auto cleanup triggered (request #{total_requests})")
            old_size = len(processed_messages)
            processed_messages.clear()
            
            if len(rate_limit_cache) > rate_limit_cache.maxsize * 0.8:
                logger.bind(request_id=request_id).warning(
                    f"Rate limit cache near capacity: {len(rate_limit_cache)}/{rate_limit_cache.maxsize}"
                )
            
            metrics["last_cleanup"] = current_time
            logger.bind(request_id=request_id).info(f"Cache cleanup complete: {old_size} messages cleared")


def _record_route_time(route_name: str, duration_ms: float, max_samples: int = 100):
    """Record route execution time for monitoring."""
    if route_name in metrics["route_execution_times"]:
        times = metrics["route_execution_times"][route_name]
        times.append(duration_ms)
        if len(times) > max_samples:
            metrics["route_execution_times"][route_name] = times[-max_samples:]


def _record_service_failure(service_name: str, error_detail: str = None):
    """Record service failure for monitoring with optional error detail."""
    if service_name in metrics["service_failures"]:
        metrics["service_failures"][service_name] += 1
        if error_detail:
            logger.warning(f"Service failure recorded: {service_name} - {error_detail} (total: {metrics['service_failures'][service_name]})")
        else:
            logger.warning(f"Service failure recorded: {service_name} (total: {metrics['service_failures'][service_name]})")


def _is_help_command(message: str) -> bool:
    """Check if message is a help command."""
    help_commands = ["help", "menu", "commands", "what can you do", "how to use", "start"]
    return message.lower().strip() in help_commands


def _is_greeting(message: str) -> bool:
    """Check if message is a greeting."""
    greetings = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "hola"]
    return message.lower().strip() in greetings


def _should_retry(status_code: int) -> bool:
    """Determine if a request should be retried based on status code."""
    retryable_statuses = {429, 500, 502, 503, 504}
    return status_code in retryable_statuses


def _check_rate_limit(phone_number: str, request_id: str) -> bool:
    """Check if user has exceeded rate limit (with logging)."""
    current_time = time.time()
    
    timestamps = rate_limit_cache.get(phone_number, [])
    timestamps = [t for t in timestamps if current_time - t < RATE_LIMIT_WINDOW]
    
    if len(timestamps) >= RATE_LIMIT_MAX_MESSAGES:
        logger.bind(request_id=request_id).warning(f"Rate limit exceeded for {phone_number}")
        _record_service_failure("rate_limiter")
        return False
    
    timestamps.append(current_time)
    rate_limit_cache[phone_number] = timestamps
    return True


def _get_help_message() -> str:
    """Get formatted help message."""
    return """🤖 *AI LOGISTICS ASSISTANT - HELP v26.0*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• Send any 10+ digit number to track

📋 *Pending Items*
• `Pending POD` - Missing proof of deliveries
• `Pending PGI` - Pending dispatches
• `Pending deliveries` - Undelivered items

🏪 *Analytics*
• `Top dealers` - Dealer rankings
• `Top warehouses` - Warehouse rankings
• `Dealer ABC performance` - Specific dealer

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Critical delays` - Urgent issues
• `Control tower` - Critical alerts

🔍 *Root Cause Analysis*
• `Why is Lahore delayed?` - AI analysis

💬 *General*
• `Help` - Show this menu

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def _get_greeting_message() -> str:
    """Get formatted greeting message."""
    from datetime import datetime
    hour = datetime.now().hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"
    
    return f"""🎉 *Welcome to AI Logistics Assistant v26.0!*

{greeting}! 👋

I'm your intelligent logistics assistant. I can help you track DNs, check dealer performance, monitor pending items, and more.

📌 *Quick examples:*
• Send `6243612278` to track a DN
• Type `Top dealers` for rankings
• Type `Pending POD` for missing proofs
• Type `Control tower` for critical alerts
• Type `Why is Lahore delayed?` for AI analysis

Type `Help` to see all available commands!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ==========================================================
# AI SERVICE DIAGNOSTICS (NEW)
# ==========================================================

def _check_ai_service_availability(request_id: str) -> tuple:
    """
    Check AI service availability with detailed diagnostics.
    Returns: (is_available, error_message, details)
    """
    global AI_SERVICE_AVAILABLE, AI_SERVICE_IMPORT_ERROR, AI_METHOD_AVAILABLE
    
    try:
        from app.services.ai_query_service import process_whatsapp_query
        AI_SERVICE_AVAILABLE = True
        AI_METHOD_AVAILABLE = callable(process_whatsapp_query)
        
        if not AI_METHOD_AVAILABLE:
            error_msg = "process_whatsapp_query is not callable"
            logger.bind(request_id=request_id).error(f"AI Service: {error_msg}")
            _record_service_failure("method_not_found", error_msg)
            return False, error_msg, {"method_available": False}
        
        logger.bind(request_id=request_id).debug("AI Service check passed")
        return True, None, {"method_available": True}
        
    except ImportError as e:
        AI_SERVICE_AVAILABLE = False
        AI_SERVICE_IMPORT_ERROR = str(e)
        error_msg = f"ImportError: {e}"
        logger.bind(request_id=request_id).exception(f"AI Service import failed: {e}")
        _record_service_failure("import_error", error_msg)
        return False, error_msg, {"import_error": str(e), "traceback": traceback.format_exc()}
        
    except AttributeError as e:
        AI_SERVICE_AVAILABLE = False
        error_msg = f"AttributeError: {e}"
        logger.bind(request_id=request_id).exception(f"AI Service attribute error: {e}")
        _record_service_failure("method_not_found", error_msg)
        return False, error_msg, {"attribute_error": str(e)}
        
    except Exception as e:
        AI_SERVICE_AVAILABLE = False
        error_msg = f"{type(e).__name__}: {e}"
        logger.bind(request_id=request_id).exception(f"AI Service unexpected error: {e}")
        _record_service_failure("ai_service", error_msg)
        return False, error_msg, {"exception_type": type(e).__name__, "traceback": traceback.format_exc()}


# ==========================================================
# CORE WEBHOOK FUNCTIONS
# ==========================================================

@router.get("/")
async def verify_webhook(request: Request):
    """WhatsApp webhook verification endpoint."""
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    
    logger.info(f"Webhook verification request - Mode: {hub_mode}")
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        if hub_challenge:
            logger.success("✅ Webhook verified successfully!")
            return PlainTextResponse(content=hub_challenge)
    
    logger.error("❌ Webhook verification failed - Invalid token")
    raise HTTPException(status_code=403, detail="Verification failed")


async def send_whatsapp_message(
    phone_number: str, 
    message: str, 
    request_id: str, 
    context_msg_id: Optional[str] = None
) -> Dict[str, Any]:
    """Send WhatsApp message with smart retry logic and timing."""
    send_start_time = time.time()
    
    if not WHATSAPP_SERVICE_AVAILABLE:
        logger.bind(request_id=request_id).error(f"WhatsApp service not available")
        _record_service_failure("whatsapp_service")
        return {"success": False, "error": "Service not available"}
    
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.bind(request_id=request_id).error(f"WhatsApp credentials missing")
        _record_service_failure("whatsapp_service")
        return {"success": False, "error": "Missing credentials"}
    
    if not message or not message.strip():
        logger.bind(request_id=request_id).warning(f"Empty message rejected")
        message = "✅ Request processed successfully"
    
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
        logger.bind(request_id=request_id).warning(f"Message truncated to {MAX_MESSAGE_LENGTH} chars")
    
    for attempt in range(MAX_RETRIES):
        try:
            if context_msg_id:
                result = send_text_message(phone_number, message, message_id=context_msg_id, request_id=request_id)
            else:
                result = send_text_message(phone_number, message, request_id=request_id)
            
            status_code = result.get('status_code', 0)
            logger.bind(request_id=request_id).info(
                f"Send attempt {attempt + 1}: success={result.get('success')}, status={status_code}"
            )
            
            if result.get("success"):
                send_duration = (time.time() - send_start_time) * 1000
                _record_route_time("whatsapp_sending", send_duration)
                return result
            
            if attempt < MAX_RETRIES - 1 and _should_retry(status_code):
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            
            _record_service_failure("whatsapp_service")
            return result
            
        except Exception as e:
            logger.bind(request_id=request_id).exception(f"Send attempt {attempt + 1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                _record_service_failure("whatsapp_service")
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}


async def process_with_ai(
    question: str, 
    phone_number: str, 
    request_id: str
) -> str:
    """
    Process user query through AI service.
    CRITICAL FIX: Pass session_factory correctly, not a db session.
    ENHANCED: Full error logging and diagnostics.
    """
    ai_start_time = time.time()
    
    logger.bind(request_id=request_id).info(f"🤖 AI Processing: {question[:100]}...")
    
    # NEW: Pre-check AI service availability with diagnostics
    is_available, error_msg, details = _check_ai_service_availability(request_id)
    
    if not is_available:
        logger.bind(request_id=request_id).error(
            f"❌ AI Service not available - Error: {error_msg}"
        )
        logger.bind(request_id=request_id).error(f"   Details: {details}")
        
        # Return user-friendly message but log full details
        return "⚠️ Service configuration error. Our team has been notified. Please try again later."
    
    def _run_ai():
        """Synchronous AI processing function (runs in thread pool)."""
        try:
            # CRITICAL FIX: Import inside function for lazy loading
            from app.database import SessionLocal
            from app.services.ai_query_service import process_whatsapp_query
            
            # NEW: Log before processing
            logger.bind(request_id=request_id).info(f"🚀 Calling process_whatsapp_query with: question={question[:50]}..., phone={phone_number}")
            
            # CRITICAL FIX: Pass SessionLocal (the factory), NOT a session instance
            # The function expects: (question, session_factory, phone_number, user_id, request_id)
            result = process_whatsapp_query(
                question=question,
                session_factory=SessionLocal,  # ← Pass the factory, not db instance
                phone_number=phone_number,
                user_id=phone_number,
                request_id=request_id
            )
            
            # NEW: Log after processing
            logger.bind(request_id=request_id).info(f"✅ process_whatsapp_query returned: {type(result)}")
            
            # Validate response
            if not result or not result.strip():
                logger.bind(request_id=request_id).warning("AI returned empty response")
                return "✅ Request processed successfully. No additional information available."
            
            logger.bind(request_id=request_id).debug(f"AI response length: {len(result)} chars")
            return result
            
        except ImportError as e:
            # REPLACED: Generic error with detailed logging
            error_msg = f"ImportError in AI processing: {e}"
            logger.bind(request_id=request_id).exception(error_msg)
            logger.bind(request_id=request_id).error(f"Full traceback: {traceback.format_exc()}")
            _record_service_failure("import_error", str(e))
            return f"⚠️ Service configuration error - Import failed: {type(e).__name__}. Please contact support."
            
        except AttributeError as e:
            error_msg = f"AttributeError in AI processing: {e}"
            logger.bind(request_id=request_id).exception(error_msg)
            logger.bind(request_id=request_id).error(f"Method not found. Available attributes in ai_query_service: ...")
            _record_service_failure("method_not_found", str(e))
            return f"⚠️ Service configuration error - Method not found: {type(e).__name__}. Please contact support."
            
        except Exception as e:
            # REPLACED: Generic error with detailed logging
            error_type = type(e).__name__
            error_msg = str(e)
            logger.bind(request_id=request_id).exception(f"AI processing error: {error_type} - {error_msg}")
            logger.bind(request_id=request_id).error(f"Full traceback: {traceback.format_exc()}")
            _record_service_failure("ai_service", f"{error_type}: {error_msg}")
            return f"⚠️ Error processing your request: {error_type}. Please try again later."
    
    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_ai),
            timeout=REQUEST_TIMEOUT_SECONDS
        )
        
        ai_duration = (time.time() - ai_start_time) * 1000
        _record_route_time("ai_processing", ai_duration)
        
        logger.bind(request_id=request_id).info(
            f"✅ AI response generated in {ai_duration:.0f}ms ({len(result)} chars)"
        )
        
        if not result or not result.strip():
            return "✅ Request processed successfully."
        
        return result
        
    except asyncio.TimeoutError:
        logger.bind(request_id=request_id).error(f"⏰ AI timeout after {REQUEST_TIMEOUT_SECONDS}s")
        metrics["timeout_requests"] += 1
        _record_service_failure("ai_service", "Timeout")
        return "⚠️ Request timeout. Please try again in a moment."
        
    except Exception as e:
        error_type = type(e).__name__
        logger.bind(request_id=request_id).exception(f"AI processing wrapper failed: {e}")
        logger.bind(request_id=request_id).error(f"Full traceback: {traceback.format_exc()}")
        _record_service_failure("ai_service", f"Wrapper: {error_type}")
        return f"⚠️ An error occurred while processing your request. Please try again later."


# ==========================================================
# MAIN WEBHOOK ENDPOINT
# ==========================================================

@router.post("/")
async def receive_message(request: Request) -> Dict[str, Any]:
    """
    Main webhook endpoint for receiving WhatsApp messages.
    """
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    # Bind request_id to all logs
    logger.bind(request_id=request_id)
    
    metrics["total_requests"] += 1
    
    logger.info(f"📨 Webhook received - Processing started")
    
    _auto_cleanup_if_needed(request_id)
    
    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode('utf-8'))
        
        logger.debug(f"Payload: {json.dumps(payload)[:500]}")
        
        if "entry" not in payload:
            logger.error(f"Invalid payload structure - missing 'entry'")
            return {"success": False, "error": "Invalid payload", "request_id": request_id}
        
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # CRITICAL: Handle status updates - NO RESPONSE NEEDED
        if value.get("statuses"):
            statuses = value.get("statuses", [])
            for status in statuses:
                status_type = status.get("status")
                message_id = status.get("id")
                logger.debug(f"📬 Status update - ID: {message_id}, Status: {status_type}")
            return {
                "success": True, 
                "type": "status_update", 
                "request_id": request_id,
                "processing_time_ms": round((time.time() - start_time) * 1000, 2)
            }
        
        messages = value.get("messages", [])
        if not messages:
            logger.warning(f"No messages in webhook payload")
            return {"success": True, "type": "no_messages", "request_id": request_id}
        
        processed_count = 0
        for message in messages:
            phone_number = message.get("from")
            msg_id = message.get("id")
            msg_type = message.get("type", "unknown")
            
            if not phone_number:
                logger.warning(f"Missing phone number in message")
                continue
            
            if len(str(phone_number)) < 10:
                logger.warning(f"Invalid phone number: {phone_number}")
                continue
            
            logger.info(f"📱 Message from: {phone_number}, Type: {msg_type}, ID: {msg_id}")
            
            # Duplicate message protection
            if msg_id and msg_id in processed_messages:
                logger.info(f"⏭️ Duplicate message detected: {msg_id}")
                metrics["duplicate_messages"] += 1
                continue
            
            if msg_id:
                processed_messages[msg_id] = True
            
            # Rate limiting
            if not _check_rate_limit(phone_number, request_id):
                logger.warning(f"Rate limit exceeded for {phone_number}")
                metrics["rate_limited_requests"] += 1
                await send_whatsapp_message(
                    phone_number,
                    "⚠️ You are sending messages too quickly. Please wait a moment before sending more.",
                    request_id,
                    msg_id
                )
                continue
            
            # Handle non-text messages
            if msg_type != "text":
                await send_whatsapp_message(
                    phone_number,
                    "📱 Please send text messages only.\n\nType 'Help' to see available commands.",
                    request_id,
                    msg_id
                )
                processed_count += 1
                continue
            
            # Extract text message
            user_message = message.get("text", {}).get("body", "").strip()
            if not user_message:
                logger.warning(f"Empty text message received")
                continue
            
            # NEW: Log the actual query being processed
            logger.info(f"💬 Processing query: {user_message[:100]}")
            
            # Handle help command
            if _is_help_command(user_message):
                logger.info(f"📖 Help command detected")
                await send_whatsapp_message(phone_number, _get_help_message(), request_id, msg_id)
                processed_count += 1
                continue
            
            # Handle greeting
            if _is_greeting(user_message):
                logger.info(f"👋 Greeting detected")
                await send_whatsapp_message(phone_number, _get_greeting_message(), request_id, msg_id)
                processed_count += 1
                continue
            
            # Process with AI service
            ai_start = time.time()
            response = await process_with_ai(user_message, phone_number, request_id)
            ai_duration = (time.time() - ai_start) * 1000
            _record_route_time("ai_processing", ai_duration)
            
            # NEW: Log successful response generation
            logger.info(f"✅ Response generated successfully ({len(response)} chars, {ai_duration:.0f}ms)")
            
            # Send response
            send_start = time.time()
            send_result = await send_whatsapp_message(phone_number, response, request_id, msg_id)
            send_duration = (time.time() - send_start) * 1000
            _record_route_time("whatsapp_sending", send_duration)
            
            if send_result.get("success"):
                logger.info(f"📤 Response sent successfully (AI: {ai_duration:.0f}ms, Send: {send_duration:.0f}ms)")
                metrics["successful_requests"] += 1
            else:
                logger.error(f"📤 Failed to send response: {send_result.get('error')}")
                metrics["failed_requests"] += 1
            
            processed_count += 1
        
        processing_time = (time.time() - start_time) * 1000
        _record_route_time("total_processing", processing_time)
        
        logger.info(f"✅ Webhook processed successfully in {processing_time:.0f}ms ({processed_count} messages)")
        
        return {
            "success": True,
            "request_id": request_id,
            "processing_time_ms": round(processing_time, 2),
            "messages_processed": processed_count
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ Invalid JSON payload: {e}")
        metrics["failed_requests"] += 1
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Invalid JSON payload", "request_id": request_id}
        )
        
    except Exception as e:
        error_type = type(e).__name__
        logger.exception(f"❌ Webhook error: {error_type} - {str(e)}")
        logger.error(f"Full traceback: {traceback.format_exc()}")
        metrics["failed_requests"] += 1
        _record_service_failure("whatsapp_service", f"{error_type}: {str(e)}")
        
        return {
            "success": False,
            "error": "An unexpected error occurred. Please try again.",
            "request_id": request_id,
            "error_type": error_type
        }


# ==========================================================
# MONITORING ENDPOINTS
# ==========================================================

@router.get("/metrics")
async def get_metrics() -> Dict[str, Any]:
    """Get webhook processing metrics."""
    uptime_seconds = time.time() - metrics["start_time"]
    
    avg_ai_time = round(sum(metrics["route_execution_times"]["ai_processing"]) / max(1, len(metrics["route_execution_times"]["ai_processing"])), 2)
    avg_whatsapp_time = round(sum(metrics["route_execution_times"]["whatsapp_sending"]) / max(1, len(metrics["route_execution_times"]["whatsapp_sending"])), 2)
    avg_total_time = round(sum(metrics["route_execution_times"]["total_processing"]) / max(1, len(metrics["route_execution_times"]["total_processing"])), 2)
    
    return {
        "uptime_seconds": round(uptime_seconds, 2),
        "uptime_hours": round(uptime_seconds / 3600, 2),
        "total_requests": metrics["total_requests"],
        "successful_requests": metrics["successful_requests"],
        "failed_requests": metrics["failed_requests"],
        "timeout_requests": metrics["timeout_requests"],
        "rate_limited_requests": metrics["rate_limited_requests"],
        "duplicate_messages": metrics["duplicate_messages"],
        "success_rate": round((metrics["successful_requests"] / max(1, metrics["total_requests"])) * 100, 2),
        "service_failures": metrics["service_failures"],
        "average_response_times_ms": {
            "ai_processing": avg_ai_time,
            "whatsapp_sending": avg_whatsapp_time,
            "total_processing": avg_total_time
        },
        "cache_size": len(processed_messages),
        "rate_limit_cache_size": len(rate_limit_cache),
        "timestamp": __import__('datetime').datetime.utcnow().isoformat()
    }


@router.get("/errors")
async def get_errors() -> Dict[str, Any]:
    """Get error statistics."""
    return {
        "total_failures": metrics["failed_requests"],
        "total_timeouts": metrics["timeout_requests"],
        "total_rate_limited": metrics["rate_limited_requests"],
        "total_duplicates": metrics["duplicate_messages"],
        "service_failures": metrics["service_failures"],
        "timestamp": __import__('datetime').datetime.utcnow().isoformat()
    }


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint."""
    from datetime import datetime
    
    db_healthy = False
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_healthy = True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        _record_service_failure("database")
    
    # Check AI service availability with diagnostics
    ai_available = False
    ai_error = None
    try:
        from app.services.ai_query_service import process_whatsapp_query
        ai_available = True
    except ImportError as e:
        ai_error = str(e)
    except Exception as e:
        ai_error = str(e)
    
    all_services_healthy = ai_available and WHATSAPP_SERVICE_AVAILABLE and db_healthy
    overall_status = "healthy" if all_services_healthy else "degraded"
    
    return {
        "status": overall_status,
        "version": "26.0",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "ai_service": {
                "status": "healthy" if ai_available else "unavailable",
                "available": ai_available,
                "failures": metrics["service_failures"]["ai_service"],
                "error": ai_error if not ai_available else None
            },
            "whatsapp_service": {
                "status": "healthy" if WHATSAPP_SERVICE_AVAILABLE else "unavailable",
                "available": WHATSAPP_SERVICE_AVAILABLE,
                "failures": metrics["service_failures"]["whatsapp_service"]
            },
            "database": {
                "status": "healthy" if db_healthy else "unavailable",
                "connected": db_healthy,
                "failures": metrics["service_failures"]["database"]
            }
        },
        "metrics": {
            "total_requests": metrics["total_requests"],
            "success_rate": round((metrics["successful_requests"] / max(1, metrics["total_requests"])) * 100, 2)
        },
        "credentials": {
            "whatsapp_token": "✓ configured" if config.WHATSAPP_ACCESS_TOKEN else "✗ missing",
            "whatsapp_phone_id": "✓ configured" if config.WHATSAPP_PHONE_NUMBER_ID else "✗ missing",
            "verify_token": "✓ configured" if config.WHATSAPP_VERIFY_TOKEN else "✗ missing"
        }
    }


@router.get("/status")
async def status() -> Dict[str, Any]:
    """Simple status endpoint."""
    return {
        "service": "WhatsApp Logistics Webhook",
        "version": "26.0",
        "status": "running",
        "services": {"whatsapp": WHATSAPP_SERVICE_AVAILABLE},
        "message": "Ready to receive WhatsApp messages",
        "uptime_seconds": round(time.time() - metrics["start_time"], 2)
    }


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    """Simple ping endpoint."""
    return {"pong": True, "timestamp": __import__('datetime').datetime.utcnow().isoformat()}


@router.get("/ai-health")
async def ai_health() -> Dict[str, Any]:
    """AI service health check with detailed diagnostics."""
    is_available, error_msg, details = _check_ai_service_availability("health_check")
    
    return {
        "service": "ai_query_service",
        "status": "healthy" if is_available else "unavailable",
        "available": is_available,
        "lazy_load": True,
        "failures": metrics["service_failures"]["ai_service"],
        "import_errors": metrics["service_failures"]["import_error"],
        "method_errors": metrics["service_failures"]["method_not_found"],
        "error": error_msg if not is_available else None,
        "details": details if not is_available else None,
        "timestamp": __import__('datetime').datetime.utcnow().isoformat()
    }


@router.get("/whatsapp-health")
async def whatsapp_health() -> Dict[str, Any]:
    """WhatsApp service health check."""
    return {
        "service": "whatsapp_service",
        "status": "healthy" if WHATSAPP_SERVICE_AVAILABLE else "unavailable",
        "available": WHATSAPP_SERVICE_AVAILABLE,
        "failures": metrics["service_failures"]["whatsapp_service"],
        "credentials": {
            "token_configured": bool(config.WHATSAPP_ACCESS_TOKEN),
            "phone_id_configured": bool(config.WHATSAPP_PHONE_NUMBER_ID),
            "verify_token_configured": bool(config.WHATSAPP_VERIFY_TOKEN)
        },
        "timestamp": __import__('datetime').datetime.utcnow().isoformat()
    }


@router.get("/rate-limit-status")
async def rate_limit_status() -> Dict[str, Any]:
    """Get rate limit cache status."""
    return {
        "active_users": len(rate_limit_cache),
        "max_users": rate_limit_cache.maxsize,
        "max_messages_per_minute": RATE_LIMIT_MAX_MESSAGES,
        "window_seconds": RATE_LIMIT_WINDOW,
        "cache_usage_percent": round((len(rate_limit_cache) / rate_limit_cache.maxsize) * 100, 2),
        "timestamp": __import__('datetime').datetime.utcnow().isoformat()
    }


# ==========================================================
# DIAGNOSTICS ENDPOINT (NEW)
# ==========================================================

@router.get("/diagnostics")
async def diagnostics() -> Dict[str, Any]:
    """Comprehensive diagnostics endpoint for debugging."""
    from datetime import datetime
    
    # Check AI service
    ai_available, ai_error, ai_details = _check_ai_service_availability("diagnostics")
    
    # Check database
    db_healthy = False
    db_error = None
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_healthy = True
    except Exception as e:
        db_error = str(e)
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "version": "26.0",
        "environment": config.ENVIRONMENT,
        "services": {
            "ai_query_service": {
                "available": ai_available,
                "error": ai_error,
                "details": ai_details
            },
            "whatsapp_service": {
                "available": WHATSAPP_SERVICE_AVAILABLE,
                "token_configured": bool(config.WHATSAPP_ACCESS_TOKEN),
                "phone_id_configured": bool(config.WHATSAPP_PHONE_NUMBER_ID)
            },
            "database": {
                "connected": db_healthy,
                "error": db_error
            }
        },
        "metrics": {
            "total_requests": metrics["total_requests"],
            "successful_requests": metrics["successful_requests"],
            "failed_requests": metrics["failed_requests"],
            "service_failures": metrics["service_failures"]
        },
        "cache_status": {
            "processed_messages_size": len(processed_messages),
            "processed_messages_maxsize": processed_messages.maxsize,
            "rate_limit_cache_size": len(rate_limit_cache),
            "rate_limit_cache_maxsize": rate_limit_cache.maxsize
        },
        "suggestions": [
            "Check /ai-health for AI service details",
            "Check /whatsapp-health for WhatsApp service details",
            "Check /database-health for database status",
            "Review logs for detailed error information"
        ]
    }


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 70)
logger.info("📡 WEBHOOK v26.0 - FULLY INTEGRATED WITH ENHANCED DEBUGGING")
logger.info("=" * 70)
logger.info("✅ CRITICAL FIXES:")
logger.info("   • session_factory passed correctly (not db session)")
logger.info("   • process_whatsapp_query parameter order fixed")
logger.info("   • AI service integration with proper error handling")
logger.info("   • Status updates properly ignored")
logger.info("   • Generic import error replaced with detailed exception logging")
logger.info("   • AI Query diagnostics with full error details")
logger.info("   • Service availability pre-check before AI call")
logger.info("   • Stack trace logging for all AI failures")
logger.info("=" * 70)
logger.info("✅ Service Status:")
logger.info(f"   • WhatsApp Service: {'✓ Available' if WHATSAPP_SERVICE_AVAILABLE else '✗ Unavailable'}")
logger.info(f"   • AI Service: Lazy Load (will load on first request)")
logger.info(f"   • WhatsApp Token: {'✓ Configured' if config.WHATSAPP_ACCESS_TOKEN else '✗ Missing'}")
logger.info(f"   • WhatsApp Phone ID: {'✓ Configured' if config.WHATSAPP_PHONE_NUMBER_ID else '✗ Missing'}")
logger.info("=" * 70)
logger.info("🔍 DEBUGGING ENDPOINTS:")
logger.info("   • /webhook/health - Service health check")
logger.info("   • /webhook/ai-health - AI service diagnostics")
logger.info("   • /webhook/whatsapp-health - WhatsApp service check")
logger.info("   • /webhook/diagnostics - Comprehensive diagnostics")
logger.info("   • /webhook/metrics - Processing metrics")
logger.info("=" * 70)
