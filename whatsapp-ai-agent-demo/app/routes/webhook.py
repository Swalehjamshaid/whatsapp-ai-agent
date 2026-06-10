# ==========================================================
# FILE: app/routes/webhook.py (REFACTORED v20.0)
# ==========================================================
# PURPOSE: PURE ENTRY POINT CONTROLLER - Thin Layer Only
#
# ARCHITECTURE:
# WhatsApp User → webhook.py → ai_query_service.py → Service Layer → Response
#
# RESPONSIBILITIES (ONLY):
# 1. Verify Webhook (GET)
# 2. Receive Messages (POST)
# 3. Call AI Service
# 4. Send Response via WhatsApp
# 5. Health Check
# 6. Centralized Error Handling
#
# WHAT THIS FILE DOES NOT CONTAIN:
# - No Database Logic
# - No Intent Detection
# - No Entity Extraction
# - No Conversation Context
# - No Statistics Tracking
# - No Rate Limiting
# - No Business Rules
# - No Analytics
# - No KPI Calculations
# ==========================================================

import json
import time
import uuid
from typing import Dict, Any
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.orm import Session
from loguru import logger

from app.config import config
from app.database import get_db

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = 3500
REQUEST_TIMEOUT_SECONDS = 30

# ==========================================================
# SERVICE IMPORTS (Lazy-loaded for faster startup)
# ==========================================================

AI_SERVICE_AVAILABLE = False
WHATSAPP_SERVICE_AVAILABLE = False

try:
    from app.services.ai_query_service import process_whatsapp_query
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ AI Query Service loaded")
except Exception as e:
    logger.error(f"❌ AI Service failed: {e}")

try:
    from app.services.whatsapp_service import send_text_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp Service loaded")
except Exception as e:
    logger.error(f"❌ WhatsApp Service failed: {e}")


# ==========================================================
# PHASE 1: WEBHOOK VERIFICATION (Pure Entry Point)
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
    
    logger.info(f"Webhook verification - Mode: {hub_mode}")
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        logger.success("✅ Webhook verified successfully")
        return PlainTextResponse(content=hub_challenge)
    
    logger.error("❌ Webhook verification failed - Invalid token")
    raise HTTPException(status_code=403, detail="Verification failed")


# ==========================================================
# PHASE 2: RECEIVE MESSAGE (Pure Entry Point)
# ==========================================================

@router.post("/")
async def receive_message(request: Request):
    """
    Main webhook endpoint for receiving WhatsApp messages.
    Pure entry point - only calls services, no business logic.
    """
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    logger.info(f"[{request_id}] 📨 Webhook received")
    
    try:
        # Parse request body
        raw_body = await request.body()
        payload = json.loads(raw_body.decode('utf-8'))
        
        # Extract message data
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates (ignore, just log)
        if value.get("statuses"):
            statuses = value.get("statuses", [])
            for status in statuses:
                logger.debug(f"[{request_id}] Status update: {status.get('status')}")
            return {"success": True, "type": "status_update"}
        
        # Extract messages
        messages = value.get("messages", [])
        if not messages:
            logger.warning(f"[{request_id}] No messages in webhook")
            return {"success": True, "type": "no_messages"}
        
        # Process each message
        for message in messages:
            phone_number = message.get("from")
            msg_id = message.get("id")
            msg_type = message.get("type", "unknown")
            
            logger.info(f"[{request_id}] 📱 From: {phone_number}, Type: {msg_type}")
            
            # Only process text messages
            if msg_type != "text":
                await _send_response(
                    phone_number,
                    "📱 Please send text messages only.\n\nType 'help' for available commands.",
                    request_id,
                    msg_id
                )
                continue
            
            # Extract user message
            user_message = message.get("text", {}).get("body", "").strip()
            if not user_message:
                continue
            
            logger.info(f"[{request_id}] 💬 Message: {user_message[:100]}")
            
            # PHASE 3: Call AI Service (all logic delegated)
            response = await _process_with_ai(user_message, phone_number, request_id)
            
            # PHASE 4: Send response
            await _send_response(phone_number, response, request_id, msg_id)
        
        # Log processing time
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"[{request_id}] ✅ Completed in {processing_time:.0f}ms")
        
        return {
            "success": True,
            "request_id": request_id,
            "processing_time_ms": round(processing_time, 2)
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"[{request_id}] Invalid JSON: {e}")
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Invalid JSON payload"}
        )
        
    except Exception as e:
        # PHASE 8: Centralized error handling
        return _handle_error(e, request_id)


# ==========================================================
# PHASE 3: AI PROCESSING (Delegated to Service)
# ==========================================================

async def _process_with_ai(question: str, phone_number: str, request_id: str) -> str:
    """
    Process query through AI service.
    All business logic delegated to ai_query_service.py
    """
    if not AI_SERVICE_AVAILABLE:
        logger.error(f"[{request_id}] AI Service unavailable")
        return "⚠️ AI service is unavailable. Please try again later."
    
    try:
        # Import here to avoid circular imports
        from app.database import SessionLocal
        from app.services.ai_query_service import process_whatsapp_query
        
        # Run AI processing in thread pool to avoid blocking
        import asyncio
        loop = asyncio.get_event_loop()
        
        def _run_ai():
            db = SessionLocal()
            try:
                result = process_whatsapp_query(question, db, phone_number, phone_number)
                return result if result else "⚠️ No response generated."
            except Exception as e:
                logger.exception(f"AI processing error: {e}")
                return f"⚠️ Error: {str(e)[:100]}"
            finally:
                db.close()
        
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_ai),
            timeout=REQUEST_TIMEOUT_SECONDS
        )
        
        logger.info(f"[{request_id}] ✅ AI response generated ({len(result)} chars)")
        return result
        
    except asyncio.TimeoutError:
        logger.error(f"[{request_id}] AI timeout after {REQUEST_TIMEOUT_SECONDS}s")
        return "⚠️ Request timeout. Please try again."
        
    except Exception as e:
        logger.exception(f"[{request_id}] AI processing failed: {e}")
        return f"⚠️ Processing error. Please try again later."


# ==========================================================
# PHASE 4: SEND RESPONSE (Delegated to WhatsApp Service)
# ==========================================================

async def _send_response(phone_number: str, message: str, request_id: str, context_msg_id: str = None) -> Dict:
    """
    Send message via WhatsApp service.
    All WhatsApp logic delegated to whatsapp_service.py
    """
    if not WHATSAPP_SERVICE_AVAILABLE:
        logger.error(f"[{request_id}] WhatsApp Service unavailable")
        return {"success": False, "error": "Service unavailable"}
    
    # Check credentials
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.error(f"[{request_id}] Missing WhatsApp credentials")
        return {"success": False, "error": "Missing credentials"}
    
    # Truncate message if too long
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
    
    # Retry logic
    max_retries = 3
    retry_delays = [1, 2, 4]
    
    for attempt in range(max_retries):
        try:
            # Call WhatsApp service
            if context_msg_id:
                result = send_text_message(phone_number, message, message_id=context_msg_id)
            else:
                result = send_text_message(phone_number, message)
            
            logger.info(f"[{request_id}] Send result: success={result.get('success')}, "
                       f"status={result.get('status_code')}")
            
            if result.get("success"):
                return result
                
            if attempt < max_retries - 1:
                import asyncio
                await asyncio.sleep(retry_delays[attempt])
                continue
                
            return result
            
        except Exception as e:
            if attempt < max_retries - 1:
                import asyncio
                await asyncio.sleep(retry_delays[attempt])
            else:
                logger.exception(f"[{request_id}] Send failed: {e}")
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}


# ==========================================================
# PHASE 8: CENTRALIZED ERROR HANDLING
# ==========================================================

def _handle_error(error: Exception, request_id: str) -> Dict:
    """
    Centralized error handler.
    Never exposes stack traces to users.
    """
    error_type = type(error).__name__
    logger.exception(f"[{request_id}] Webhook error: {error_type} - {error}")
    
    # User-friendly messages for common errors
    if "timeout" in str(error).lower():
        user_message = "Request timeout. Please try again."
    elif "connection" in str(error).lower():
        user_message = "Service temporarily unavailable. Please try again later."
    else:
        user_message = "An unexpected error occurred. Please try again."
    
    return {
        "success": False,
        "error": user_message,
        "request_id": request_id,
        "type": error_type
    }


# ==========================================================
# PHASE 10: HEALTH CHECK ENDPOINT
# ==========================================================

@router.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring.
    Calls service health checks for accurate status.
    """
    health_status = {
        "status": "healthy",
        "timestamp": None,
        "services": {}
    }
    
    from datetime import datetime
    health_status["timestamp"] = datetime.utcnow().isoformat()
    
    # Check AI Service
    try:
        from app.database import SessionLocal
        from app.services.ai_query_service import AIQueryService
        
        db = SessionLocal()
        try:
            ai_service = AIQueryService(db)
            ai_health = ai_service.health_check()
            health_status["services"]["ai"] = {
                "status": "healthy" if ai_health.get("status") == "healthy" else "degraded",
                "version": ai_health.get("version", "unknown"),
                "mode": ai_health.get("mode", "unknown")
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"AI health check failed: {e}")
        health_status["services"]["ai"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "degraded"
    
    # Check WhatsApp Service
    try:
        from app.services.whatsapp_service import get_whatsapp_service
        
        whatsapp = get_whatsapp_service()
        whatsapp_health = whatsapp.health_check()
        health_status["services"]["whatsapp"] = {
            "status": "healthy" if whatsapp_health.get("configured") else "degraded",
            "configured": whatsapp_health.get("configured", False),
            "version": whatsapp_health.get("version", "unknown")
        }
        if not whatsapp_health.get("configured"):
            health_status["status"] = "degraded"
    except Exception as e:
        logger.error(f"WhatsApp health check failed: {e}")
        health_status["services"]["whatsapp"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "degraded"
    
    # Check Database (via simple connection test)
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        health_status["services"]["database"] = {"status": "healthy"}
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        health_status["services"]["database"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "degraded"
    
    # Check Groq (via AI Provider)
    try:
        from app.services.ai_provider_service import get_ai_provider
        provider = get_ai_provider()
        health_status["services"]["groq"] = {
            "status": "healthy" if provider.provider else "degraded",
            "provider": provider.provider or "none",
            "model": provider.model or "none"
        }
        if not provider.provider:
            health_status["status"] = "degraded"
    except Exception as e:
        logger.error(f"Groq health check failed: {e}")
        health_status["services"]["groq"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "degraded"
    
    return health_status


# ==========================================================
# SIMPLE STATUS ENDPOINT
# ==========================================================

@router.get("/status")
async def status():
    """Simple status endpoint for basic monitoring"""
    return {
        "service": "WhatsApp Webhook",
        "version": "20.0",
        "status": "running",
        "ai_service": AI_SERVICE_AVAILABLE,
        "whatsapp_service": WHATSAPP_SERVICE_AVAILABLE,
        "message": "Ready to receive messages"
    }


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📡 WEBHOOK v20.0 - Pure Entry Point Controller")
logger.info("   Responsibilities: Verify | Receive | Route | Send | Health")
logger.info("   NO Database | NO Intent | NO Context | NO Stats | NO Rate Limit")
logger.info("=" * 60)
