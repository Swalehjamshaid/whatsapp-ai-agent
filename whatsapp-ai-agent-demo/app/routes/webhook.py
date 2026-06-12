# ==========================================================
# FILE: app/routes/webhook.py (v27.2 - FIXED INITIALIZATION CHECK)
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
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 2
RETRY_DELAYS = [1, 2]

RATE_LIMIT_MAX_MESSAGES = 10
RATE_LIMIT_WINDOW = 60
AUTO_CLEANUP_INTERVAL = 500

# ==========================================================
# CACHES & METRICS
# ==========================================================

processed_messages = TTLCache(maxsize=5000, ttl=3600)
rate_limit_cache = TTLCache(maxsize=10000, ttl=RATE_LIMIT_WINDOW)

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

WHATSAPP_SERVICE_AVAILABLE = False

# ==========================================================
# GLOBAL VARIABLES FOR SERVICE INSTANCES
# ==========================================================

_ai_query_service = None
_ai_service_initialized = False
_ai_service_initialization_error = None


# ==========================================================
# SERVICE IMPORTS
# ==========================================================

try:
    from app.services.whatsapp_service import send_text_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp Service loaded successfully")
except ImportError as e:
    logger.error(f"❌ WhatsApp Service import failed: {e}")
except Exception as e:
    logger.error(f"❌ WhatsApp Service error: {e}")

# Import AI service functions
try:
    from app.services.ai_query_service import (
        process_whatsapp_query as ai_process_query,
        get_query_service,
        health_check as ai_health_check,
        initialize_query_service
    )
    logger.info("✅ AI Query Service functions imported successfully")
    AI_FUNCTIONS_AVAILABLE = True
except ImportError as e:
    logger.error(f"❌ AI Query Service import failed: {e}")
    AI_FUNCTIONS_AVAILABLE = False
except Exception as e:
    logger.error(f"❌ AI Query Service import error: {e}")
    AI_FUNCTIONS_AVAILABLE = False


# ==========================================================
# AI SERVICE INITIALIZATION FUNCTION
# ==========================================================

def init_ai_service():
    """Initialize AI Query Service - called from main.py during startup"""
    global _ai_query_service, _ai_service_initialized, _ai_service_initialization_error
    
    if _ai_service_initialized:
        logger.info("AI Service already initialized")
        return True
    
    if not AI_FUNCTIONS_AVAILABLE:
        _ai_service_initialization_error = "AI functions not available"
        logger.error(f"Cannot initialize AI service: {_ai_service_initialization_error}")
        return False
    
    try:
        from app.database import SessionLocal
        
        db = SessionLocal()
        
        try:
            from app.services.analytics_service import AnalyticsService
            from app.services.logistics_query_service import LogisticsQueryService
            from app.services.kpi_service import KPIService
            from app.services.ai_provider_service import AIProviderService
            
            analytics_service = AnalyticsService(db)
            logistics_service = LogisticsQueryService(db)
            kpi_service = KPIService(db)
            ai_provider = AIProviderService()
            
            # Try to get existing service or create new one
            try:
                _ai_query_service = get_query_service()
                logger.info("AI Query Service already exists")
            except RuntimeError:
                _ai_query_service = initialize_query_service(
                    analytics_service=analytics_service,
                    logistics_service=logistics_service,
                    kpi_service=kpi_service,
                    ai_provider=ai_provider
                )
                logger.info("AI Query Service initialized successfully")
            
            _ai_service_initialized = True
            _ai_service_initialization_error = None
            
            # Test the service
            health = _ai_query_service.health_check()
            logger.info(f"AI Service health: {health.get('status', 'unknown')}")
            
            return True
            
        finally:
            db.close()
            
    except Exception as e:
        _ai_service_initialization_error = str(e)
        logger.error(f"Failed to initialize AI service: {e}")
        return False


def is_ai_service_ready() -> bool:
    """Check if AI service is ready to process requests"""
    return _ai_service_initialized and _ai_query_service is not None


def get_ai_service():
    """Get the initialized AI service instance"""
    return _ai_query_service


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def is_dn_number(text: str) -> bool:
    """Check if text looks like a DN number"""
    pattern = r'^(624\d{7}|\d{10,12})$'
    return bool(re.match(pattern, text.strip()))


def get_direct_dn_response(dn_number: str) -> str:
    """Direct DN response when AI service is unavailable"""
    return f"""
📦 *DN SEARCH* (Processing)

🔢 *DN Number:* {dn_number}

⏳ Your request is being processed.
The system is initializing. Please try again in a moment.

📋 *Quick actions:*
• Try again in 30 seconds
• Type `Help` for available commands

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Your request has been recorded
"""


def get_dealer_fallback_response(dealer_name: str) -> str:
    """Fallback response for dealer queries when AI service is unavailable"""
    return f"""
🏪 *DEALER SEARCH* (Processing)

📌 *{dealer_name}*

⏳ Your request is being processed.
The system is initializing. Please try again in a moment.

📋 *Quick actions:*
• Try again in 30 seconds
• Type `Help` for available commands

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Your request has been recorded
"""


def _auto_cleanup_if_needed(request_id: str):
    current_time = time.time()
    total_requests = metrics["total_requests"]
    
    if total_requests > 0 and total_requests % AUTO_CLEANUP_INTERVAL == 0:
        if current_time - metrics.get("last_cleanup", 0) > 60:
            logger.bind(request_id=request_id).info(f"Auto cleanup triggered (request #{total_requests})")
            old_size = len(processed_messages)
            processed_messages.clear()
            metrics["last_cleanup"] = current_time
            logger.bind(request_id=request_id).info(f"Cache cleanup complete: {old_size} messages cleared")


def _record_route_time(route_name: str, duration_ms: float, max_samples: int = 100):
    if route_name in metrics["route_execution_times"]:
        times = metrics["route_execution_times"][route_name]
        times.append(duration_ms)
        if len(times) > max_samples:
            metrics["route_execution_times"][route_name] = times[-max_samples:]


def _record_service_failure(service_name: str, error_detail: str = None):
    if service_name in metrics["service_failures"]:
        metrics["service_failures"][service_name] += 1
        if error_detail:
            logger.warning(f"Service failure: {service_name} - {error_detail}")
        else:
            logger.warning(f"Service failure: {service_name}")


def _is_help_command(message: str) -> bool:
    help_commands = ["help", "menu", "commands", "what can you do", "how to use", "start"]
    return message.lower().strip() in help_commands


def _is_greeting(message: str) -> bool:
    greetings = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "hola"]
    return message.lower().strip() in greetings


def _should_retry(status_code: int) -> bool:
    retryable_statuses = {429, 500, 502, 503, 504}
    return status_code in retryable_statuses


def _check_rate_limit(phone_number: str, request_id: str) -> bool:
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
    return """🤖 *AI LOGISTICS ASSISTANT - HELP*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• Send any 10+ digit number to track

📋 *Pending Items*
• `Pending POD` - Missing proof of deliveries
• `Pending deliveries` - Undelivered items

🏪 *Analytics*
• `Top dealers` - Dealer rankings
• `[Dealer name]` - Dealer dashboard

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Critical delays` - Urgent issues

💬 *General*
• `Help` - Show this menu

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def _get_greeting_message() -> str:
    from datetime import datetime
    hour = datetime.now().hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"
    
    return f"""🎉 *Welcome to AI Logistics Assistant!*

{greeting}! 👋

I'm your intelligent logistics assistant.

📌 *Quick examples:*
• Send any 10+ digit number to track a DN
• Type `Top dealers` for rankings
• Type `Pending POD` for missing proofs
• Type `[Dealer name]` for dealer dashboard

Type `Help` to see all available commands!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ==========================================================
# CORE WEBHOOK FUNCTIONS
# ==========================================================

@router.get("/")
async def verify_webhook(request: Request):
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
    send_start_time = time.time()
    
    if not WHATSAPP_SERVICE_AVAILABLE:
        logger.bind(request_id=request_id).error(f"WhatsApp service not available")
        return {"success": False, "error": "Service not available"}
    
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.bind(request_id=request_id).error(f"WhatsApp credentials missing")
        return {"success": False, "error": "Missing credentials"}
    
    if not message or not message.strip():
        message = "✅ Request processed successfully"
    
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
    
    for attempt in range(MAX_RETRIES):
        try:
            if context_msg_id:
                result = send_text_message(phone_number, message, message_id=context_msg_id, request_id=request_id)
            else:
                result = send_text_message(phone_number, message, request_id=request_id)
            
            if result.get("success"):
                send_duration = (time.time() - send_start_time) * 1000
                _record_route_time("whatsapp_sending", send_duration)
                return result
            
            if attempt < MAX_RETRIES - 1 and _should_retry(result.get('status_code', 0)):
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            
            return result
            
        except Exception as e:
            logger.bind(request_id=request_id).exception(f"Send attempt {attempt + 1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}


# ==========================================================
# PROCESS QUERY FUNCTION (Uses the global service)
# ==========================================================

async def process_user_query(
    question: str, 
    phone_number: str, 
    request_id: str
) -> str:
    """
    Process user query using the initialized AI service.
    """
    start_time = time.time()
    
    logger.bind(request_id=request_id).info(f"🤖 Processing: {question[:100]}...")
    
    # Check if AI service is ready
    if not is_ai_service_ready():
        logger.warning(f"AI Service not ready: {_ai_service_initialization_error}")
        
        # Fallback for DN numbers
        if is_dn_number(question):
            return get_direct_dn_response(question)
        
        # Fallback for dealer names (basic detection)
        if len(question) > 3 and not any(kw in question.lower() for kw in ["help", "pending", "pod", "delivery"]):
            return get_dealer_fallback_response(question)
        
        return "⚠️ AI Service is initializing. Please wait 30 seconds and try again.\n\n💡 Type `Help` to see available commands."
    
    try:
        # Use the global AI service
        response = _ai_query_service.process(question, phone_number, request_id)
        
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"✅ Response: {len(response)} chars, {elapsed:.0f}ms")
        
        return response
        
    except Exception as e:
        logger.exception(f"Processing error: {e}")
        _record_service_failure("ai_service", str(e))
        
        # Fallback for DN numbers
        if is_dn_number(question):
            return get_direct_dn_response(question)
        
        return f"⚠️ Error: {type(e).__name__}. Please try again."


# ==========================================================
# MAIN WEBHOOK ENDPOINT
# ==========================================================

@router.post("/")
async def receive_message(request: Request) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    logger.bind(request_id=request_id)
    metrics["total_requests"] += 1
    
    logger.info(f"📨 Webhook received")
    _auto_cleanup_if_needed(request_id)
    
    try:
        raw_body = await asyncio.wait_for(request.body(), timeout=10.0)
        payload = json.loads(raw_body.decode('utf-8'))
        
        if "entry" not in payload:
            logger.error(f"Invalid payload - missing 'entry'")
            return {"success": False, "error": "Invalid payload", "request_id": request_id}
        
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates
        if value.get("statuses"):
            for status in value.get("statuses", []):
                logger.debug(f"Status update: {status.get('status')}")
            return {
                "success": True, 
                "type": "status_update", 
                "request_id": request_id,
                "processing_time_ms": round((time.time() - start_time) * 1000, 2)
            }
        
        messages = value.get("messages", [])
        if not messages:
            logger.warning(f"No messages in payload")
            return {"success": True, "type": "no_messages", "request_id": request_id}
        
        processed_count = 0
        for message in messages:
            phone_number = message.get("from")
            msg_id = message.get("id")
            msg_type = message.get("type", "unknown")
            
            if not phone_number or len(str(phone_number)) < 10:
                logger.warning(f"Invalid phone number: {phone_number}")
                continue
            
            logger.info(f"📱 From: {phone_number}, Type: {msg_type}")
            
            # Duplicate check
            if msg_id and msg_id in processed_messages:
                logger.info(f"Duplicate: {msg_id}")
                metrics["duplicate_messages"] += 1
                continue
            
            if msg_id:
                processed_messages[msg_id] = True
            
            # Rate limit
            if not _check_rate_limit(phone_number, request_id):
                metrics["rate_limited_requests"] += 1
                await send_whatsapp_message(
                    phone_number,
                    "⚠️ Too many messages. Please wait a moment before sending more.",
                    request_id,
                    msg_id
                )
                continue
            
            # Non-text messages
            if msg_type != "text":
                await send_whatsapp_message(
                    phone_number,
                    "📱 Please send text messages only. Type 'Help' for commands.",
                    request_id,
                    msg_id
                )
                processed_count += 1
                continue
            
            # Extract text
            user_message = message.get("text", {}).get("body", "").strip()
            if not user_message:
                continue
            
            logger.info(f"💬 Query: {user_message[:100]}")
            
            # Help command - direct response
            if _is_help_command(user_message):
                await send_whatsapp_message(phone_number, _get_help_message(), request_id, msg_id)
                processed_count += 1
                metrics["successful_requests"] += 1
                continue
            
            # Greeting - direct response
            if _is_greeting(user_message):
                await send_whatsapp_message(phone_number, _get_greeting_message(), request_id, msg_id)
                processed_count += 1
                metrics["successful_requests"] += 1
                continue
            
            # Process with AI service
            response = await process_user_query(user_message, phone_number, request_id)
            
            # Send response
            send_result = await send_whatsapp_message(phone_number, response, request_id, msg_id)
            
            if send_result.get("success"):
                logger.info(f"✅ Response sent")
                metrics["successful_requests"] += 1
            else:
                logger.error(f"❌ Send failed: {send_result.get('error')}")
                metrics["failed_requests"] += 1
            
            processed_count += 1
        
        processing_time = (time.time() - start_time) * 1000
        _record_route_time("total_processing", processing_time)
        
        logger.info(f"✅ Done: {processing_time:.0f}ms, {processed_count} messages")
        
        return {
            "success": True,
            "request_id": request_id,
            "processing_time_ms": round(processing_time, 2),
            "messages_processed": processed_count
        }
        
    except asyncio.TimeoutError:
        logger.error(f"Request body timeout")
        metrics["timeout_requests"] += 1
        return {
            "success": False,
            "error": "Request timeout",
            "request_id": request_id
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        metrics["failed_requests"] += 1
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Invalid JSON", "request_id": request_id}
        )
        
    except Exception as e:
        error_type = type(e).__name__
        logger.exception(f"Webhook error: {error_type}")
        metrics["failed_requests"] += 1
        
        return {
            "success": False,
            "error": str(e),
            "request_id": request_id,
            "error_type": error_type
        }


# ==========================================================
# MONITORING ENDPOINTS
# ==========================================================

@router.get("/health")
async def health_check():
    from datetime import datetime
    
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
        "status": "healthy" if is_ai_service_ready() and db_healthy else "degraded",
        "version": "27.2",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "ai_query_service": {
                "available": is_ai_service_ready(),
                "error": _ai_service_initialization_error
            },
            "whatsapp_service": {
                "available": WHATSAPP_SERVICE_AVAILABLE
            },
            "database": {
                "connected": db_healthy,
                "error": db_error
            }
        }
    }


@router.get("/metrics")
async def get_metrics():
    uptime = time.time() - metrics["start_time"]
    
    return {
        "webhook": {
            "uptime_seconds": round(uptime, 2),
            "total_requests": metrics["total_requests"],
            "successful_requests": metrics["successful_requests"],
            "failed_requests": metrics["failed_requests"],
            "timeout_requests": metrics["timeout_requests"],
            "rate_limited_requests": metrics["rate_limited_requests"],
            "duplicate_messages": metrics["duplicate_messages"],
            "success_rate": round((metrics["successful_requests"] / max(1, metrics["total_requests"])) * 100, 2),
            "service_failures": metrics["service_failures"],
            "ai_service_ready": is_ai_service_ready()
        },
        "timestamp": __import__('datetime').datetime.utcnow().isoformat()
    }


@router.get("/ping")
async def ping():
    return {
        "pong": True, 
        "timestamp": __import__('datetime').datetime.utcnow().isoformat(),
        "ai_service_ready": is_ai_service_ready(),
        "ai_service_error": _ai_service_initialization_error
    }


@router.post("/init-ai")
async def initialize_ai():
    """Manual endpoint to initialize AI service (for debugging)"""
    success = init_ai_service()
    return {
        "success": success,
        "ready": is_ai_service_ready(),
        "error": _ai_service_initialization_error if not success else None
    }


# ==========================================================
# INITIALIZATION
# ==========================================================

# Try to initialize on module load (will be called when app starts)
try:
    logger.info("=" * 70)
    logger.info("📡 WEBHOOK v27.2 - INITIALIZING")
    logger.info("=" * 70)
    
    # Don't block on initialization - let main.py handle it
    # This prevents import-time failures
    logger.info("Webhook loaded. AI service will be initialized by main.py")
    
except Exception as e:
    logger.error(f"Webhook initialization error: {e}")

logger.info("=" * 70)
logger.info("📡 WEBHOOK v27.2 - READY")
logger.info("   ✅ AI service initialization delegated to main.py")
logger.info("   ✅ Fallback responses for DN numbers and dealer names")
logger.info("   ✅ Manual /webhook/init-ai endpoint for debugging")
logger.info("=" * 70)
