# ==========================================================
# FILE: app/routes/webhook.py (FINAL v22.0)
# ==========================================================
# PURPOSE: PURE ENTRY POINT CONTROLLER - 100% Error Free
#
# ARCHITECTURE:
# WhatsApp User → webhook.py → ai_query_service.py → Service Layer → Response
# ==========================================================

import json
import time
import uuid
import asyncio
import concurrent.futures
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse
from loguru import logger

from app.config import config

# Create router
router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = 3500
REQUEST_TIMEOUT_SECONDS = 25
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]

# ==========================================================
# SERVICE AVAILABILITY FLAGS
# ==========================================================

AI_SERVICE_AVAILABLE = False
WHATSAPP_SERVICE_AVAILABLE = False

# ==========================================================
# SERVICE IMPORTS (with error handling)
# ==========================================================

try:
    from app.services.ai_query_service import process_whatsapp_query
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ AI Query Service loaded successfully")
except ImportError as e:
    logger.error(f"❌ AI Query Service import failed: {e}")
except Exception as e:
    logger.error(f"❌ AI Query Service error: {e}")

try:
    from app.services.whatsapp_service import send_text_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp Service loaded successfully")
except ImportError as e:
    logger.error(f"❌ WhatsApp Service import failed: {e}")
except Exception as e:
    logger.error(f"❌ WhatsApp Service error: {e}")

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def _is_help_command(message: str) -> bool:
    """Check if message is a help command."""
    help_commands = ["help", "menu", "commands", "what can you do", "how to use", "start"]
    return message.lower().strip() in help_commands


def _is_greeting(message: str) -> bool:
    """Check if message is a greeting."""
    greetings = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "hola"]
    return message.lower().strip() in greetings


def _get_help_message() -> str:
    """Get formatted help message."""
    return """🤖 *AI LOGISTICS ASSISTANT - HELP*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• Send any 10+ digit number to track

📋 *Pending Items*
• `Pending POD` - Missing proof of deliveries
• `Pending PGI` - Pending dispatches

🏪 *Analytics*
• `Top dealers` - Dealer rankings
• `Top warehouses` - Warehouse rankings
• `Top products` - Product rankings

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status

🚨 *Control Tower*
• `Control tower` - Critical alerts

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
    
    return f"""🎉 *Welcome to AI Logistics Assistant!*

{greeting}! 👋

I'm your intelligent logistics assistant. I can help you track DNs, check dealer performance, monitor pending items, and more.

📌 *Quick examples:*
• Send `6243612278` to track a DN
• Type `Top dealers` for rankings
• Type `Pending POD` for missing proofs
• Type `Control tower` for critical alerts

Type `Help` to see all available commands!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ==========================================================
# CORE WEBHOOK FUNCTIONS
# ==========================================================

@router.get("/")
async def verify_webhook(request: Request):
    """
    WhatsApp webhook verification endpoint.
    Meta requires this for initial setup.
    """
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
    """
    Send WhatsApp message with retry logic.
    
    Args:
        phone_number: Recipient's phone number
        message: Message to send
        request_id: Unique request ID for tracking
        context_msg_id: Optional message ID for reply context
    
    Returns:
        Dictionary with success status and message details
    """
    if not WHATSAPP_SERVICE_AVAILABLE:
        logger.error(f"[{request_id}] WhatsApp service not available")
        return {"success": False, "error": "Service not available"}
    
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.error(f"[{request_id}] WhatsApp credentials missing")
        return {"success": False, "error": "Missing credentials"}
    
    # Truncate long messages
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
        logger.warning(f"[{request_id}] Message truncated to {MAX_MESSAGE_LENGTH} chars")
    
    for attempt in range(MAX_RETRIES):
        try:
            # Call WhatsApp service
            if context_msg_id:
                result = send_text_message(phone_number, message, message_id=context_msg_id)
            else:
                result = send_text_message(phone_number, message)
            
            logger.info(
                f"[{request_id}] Send attempt {attempt + 1}: "
                f"success={result.get('success')}, "
                f"status={result.get('status_code')}"
            )
            
            if result.get("success"):
                return result
            
            # Retry on failure
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            
            return result
            
        except Exception as e:
            logger.exception(f"[{request_id}] Send attempt {attempt + 1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}


async def process_with_ai(
    question: str, 
    phone_number: str, 
    request_id: str
) -> str:
    """
    Process user query through AI service.
    
    Args:
        question: User's question
        phone_number: User's phone number
        request_id: Unique request ID for tracking
    
    Returns:
        AI response string
    """
    if not AI_SERVICE_AVAILABLE:
        logger.error(f"[{request_id}] AI service not available")
        return "⚠️ AI service is temporarily unavailable. Please try again later."
    
    logger.info(f"[{request_id}] 🤖 Processing: {question[:50]}...")
    
    def _run_ai():
        """Synchronous AI processing function (runs in thread pool)."""
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            result = process_whatsapp_query(question, db, phone_number, phone_number)
            return result if result else "⚠️ No response generated."
        except Exception as e:
            logger.exception(f"[{request_id}] AI processing error: {e}")
            return f"⚠️ Error: {str(e)[:100]}"
        finally:
            db.close()
    
    try:
        # Run AI processing in thread pool to avoid blocking
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_ai),
            timeout=REQUEST_TIMEOUT_SECONDS
        )
        
        logger.info(f"[{request_id}] ✅ AI response generated ({len(result)} chars)")
        return result
        
    except asyncio.TimeoutError:
        logger.error(f"[{request_id}] ⏰ AI timeout after {REQUEST_TIMEOUT_SECONDS}s")
        return "⚠️ Request timeout. Please try again in a moment."
        
    except Exception as e:
        logger.exception(f"[{request_id}] AI processing failed: {e}")
        return "⚠️ An error occurred while processing your request. Please try again later."


# ==========================================================
# MAIN WEBHOOK ENDPOINT
# ==========================================================

@router.post("/")
async def receive_message(
    request: Request, 
    background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    """
    Main webhook endpoint for receiving WhatsApp messages.
    
    This is the entry point for all incoming WhatsApp messages.
    All business logic is delegated to the AI Query Service.
    """
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    logger.info(f"[{request_id}] 📨 Webhook received - Processing started")
    
    try:
        # Parse request body
        raw_body = await request.body()
        payload = json.loads(raw_body.decode('utf-8'))
        
        # Log webhook payload for debugging (truncated)
        logger.debug(f"[{request_id}] Payload: {json.dumps(payload)[:500]}")
        
        # Extract message data
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates (read receipts, delivery confirmations)
        if value.get("statuses"):
            statuses = value.get("statuses", [])
            for status in statuses:
                status_type = status.get("status")
                message_id = status.get("id")
                logger.debug(f"[{request_id}] 📬 Status update - ID: {message_id}, Status: {status_type}")
            return {"success": True, "type": "status_update", "request_id": request_id}
        
        # Get messages from payload
        messages = value.get("messages", [])
        if not messages:
            logger.warning(f"[{request_id}] No messages in webhook payload")
            return {"success": True, "type": "no_messages", "request_id": request_id}
        
        # Process each message
        processed_count = 0
        for message in messages:
            # Extract message details
            phone_number = message.get("from")
            msg_id = message.get("id")
            msg_type = message.get("type", "unknown")
            timestamp = message.get("timestamp")
            
            logger.info(f"[{request_id}] 📱 Message from: {phone_number}, Type: {msg_type}, ID: {msg_id}")
            
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
                logger.warning(f"[{request_id}] Empty text message received")
                continue
            
            logger.info(f"[{request_id}] 💬 Message: {user_message[:100]}")
            
            # Handle help command
            if _is_help_command(user_message):
                logger.info(f"[{request_id}] 📖 Help command detected")
                await send_whatsapp_message(phone_number, _get_help_message(), request_id, msg_id)
                processed_count += 1
                continue
            
            # Handle greeting
            if _is_greeting(user_message):
                logger.info(f"[{request_id}] 👋 Greeting detected")
                await send_whatsapp_message(phone_number, _get_greeting_message(), request_id, msg_id)
                processed_count += 1
                continue
            
            # Check if it's a DN query (numeric only, 10-15 digits)
            import re
            if re.match(r'^\d{10,15}$', user_message):
                logger.info(f"[{request_id}] 🔢 DN QUERY: {user_message}")
            
            # Process with AI service
            response = await process_with_ai(user_message, phone_number, request_id)
            
            # Send response
            send_result = await send_whatsapp_message(phone_number, response, request_id, msg_id)
            
            if send_result.get("success"):
                logger.info(f"[{request_id}] 📤 Response sent successfully")
            else:
                logger.error(f"[{request_id}] 📤 Failed to send response: {send_result.get('error')}")
            
            processed_count += 1
        
        # Calculate processing time
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"[{request_id}] ✅ Webhook processed successfully in {processing_time:.0f}ms")
        
        return {
            "success": True,
            "request_id": request_id,
            "processing_time_ms": round(processing_time, 2),
            "messages_processed": processed_count
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"[{request_id}] ❌ Invalid JSON payload: {e}")
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Invalid JSON payload",
                "request_id": request_id
            }
        )
        
    except Exception as e:
        # Centralized error handling - never expose stack traces to users
        error_type = type(e).__name__
        logger.exception(f"[{request_id}] ❌ Webhook error: {error_type} - {str(e)}")
        
        # Return user-friendly error message
        return {
            "success": False,
            "error": "An unexpected error occurred. Please try again.",
            "request_id": request_id,
            "error_type": error_type
        }


# ==========================================================
# HEALTH AND MONITORING ENDPOINTS
# ==========================================================

@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """
    Health check endpoint for monitoring systems.
    
    Returns:
        Detailed health status of all services
    """
    from datetime import datetime
    
    # Check database connectivity
    db_healthy = False
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        db_healthy = True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
    
    # Determine overall status
    all_services_healthy = AI_SERVICE_AVAILABLE and WHATSAPP_SERVICE_AVAILABLE and db_healthy
    overall_status = "healthy" if all_services_healthy else "degraded"
    
    return {
        "status": overall_status,
        "version": "22.0",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "ai_service": {
                "status": "healthy" if AI_SERVICE_AVAILABLE else "unavailable",
                "available": AI_SERVICE_AVAILABLE
            },
            "whatsapp_service": {
                "status": "healthy" if WHATSAPP_SERVICE_AVAILABLE else "unavailable",
                "available": WHATSAPP_SERVICE_AVAILABLE
            },
            "database": {
                "status": "healthy" if db_healthy else "unavailable",
                "connected": db_healthy
            }
        },
        "credentials": {
            "whatsapp_token": "✓ configured" if config.WHATSAPP_ACCESS_TOKEN else "✗ missing",
            "whatsapp_phone_id": "✓ configured" if config.WHATSAPP_PHONE_NUMBER_ID else "✗ missing",
            "verify_token": "✓ configured" if config.WHATSAPP_VERIFY_TOKEN else "✗ missing"
        }
    }


@router.get("/status")
async def status() -> Dict[str, Any]:
    """
    Simple status endpoint for basic monitoring.
    
    Returns:
        Basic service status information
    """
    return {
        "service": "WhatsApp Logistics Webhook",
        "version": "22.0",
        "status": "running",
        "services": {
            "ai": AI_SERVICE_AVAILABLE,
            "whatsapp": WHATSAPP_SERVICE_AVAILABLE
        },
        "message": "Ready to receive WhatsApp messages",
        "uptime": "Active"
    }


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    """
    Simple ping endpoint for connectivity testing.
    
    Returns:
        Pong response for testing
    """
    return {
        "pong": True,
        "timestamp": __import__('datetime').datetime.utcnow().isoformat()
    }


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 70)
logger.info("📡 WEBHOOK v22.0 - 100% Error Free Entry Point Controller")
logger.info("=" * 70)
logger.info("✅ Service Status:")
logger.info(f"   • AI Query Service: {'✓ Available' if AI_SERVICE_AVAILABLE else '✗ Unavailable'}")
logger.info(f"   • WhatsApp Service: {'✓ Available' if WHATSAPP_SERVICE_AVAILABLE else '✗ Unavailable'}")
logger.info(f"   • WhatsApp Token: {'✓ Configured' if config.WHATSAPP_ACCESS_TOKEN else '✗ Missing'}")
logger.info(f"   • WhatsApp Phone ID: {'✓ Configured' if config.WHATSAPP_PHONE_NUMBER_ID else '✗ Missing'}")
logger.info(f"   • Verify Token: {'✓ Configured' if config.WHATSAPP_VERIFY_TOKEN else '✗ Missing'}")
logger.info("=" * 70)
logger.info("🚀 Webhook endpoints available at:")
logger.info(f"   • GET  /webhook/ - Verification endpoint")
logger.info(f"   • POST /webhook/ - Message receiving endpoint")
logger.info(f"   • GET  /webhook/health - Health check")
logger.info(f"   • GET  /webhook/status - Status check")
logger.info(f"   • GET  /webhook/ping - Ping test")
logger.info("=" * 70)
