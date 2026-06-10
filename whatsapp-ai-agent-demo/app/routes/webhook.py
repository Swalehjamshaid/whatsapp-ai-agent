# ==========================================================
# FILE: app/routes/webhook.py (FIXED v21.1)
# ==========================================================

import json
import time
import uuid
import asyncio
from typing import Dict, Any
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from loguru import logger

from app.config import config

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

MAX_MESSAGE_LENGTH = 3500
REQUEST_TIMEOUT_SECONDS = 30

AI_SERVICE_AVAILABLE = False
WHATSAPP_SERVICE_AVAILABLE = False

try:
    from app.services.ai_query_service import process_whatsapp_query
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ AI Query Service loaded")
except Exception as e:
    logger.error(f"❌ AI Service failed: {e}")

try:
    from app.services.whatsapp_service import send_text_message, send_help_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp Service loaded")
except Exception as e:
    logger.error(f"❌ WhatsApp Service failed: {e}")


@router.get("/")
async def verify_webhook(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        logger.success("✅ Webhook verified")
        return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Verification failed")


async def _send_response(phone_number: str, message: str, request_id: str, context_msg_id: str = None) -> Dict:
    """Send message via WhatsApp service."""
    if not WHATSAPP_SERVICE_AVAILABLE:
        return {"success": False, "error": "Service unavailable"}
    
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        return {"success": False, "error": "Missing credentials"}
    
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
    
    for attempt in range(3):
        try:
            result = send_text_message(phone_number, message, message_id=context_msg_id)
            if result.get("success"):
                return result
            if attempt < 2:
                await asyncio.sleep([1, 2][attempt])
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep([1, 2][attempt])
            else:
                logger.exception(f"[{request_id}] Send failed: {e}")
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}


async def _process_with_ai(question: str, phone_number: str, request_id: str) -> str:
    """Process query through AI service."""
    if not AI_SERVICE_AVAILABLE:
        return "⚠️ AI service is unavailable."
    
    try:
        from app.database import SessionLocal
        from app.services.ai_query_service import process_whatsapp_query
        
        loop = asyncio.get_event_loop()
        
        def _run_ai():
            db = SessionLocal()
            try:
                return process_whatsapp_query(question, db, phone_number, phone_number)
            except Exception as e:
                logger.exception(f"AI error: {e}")
                return f"⚠️ Error: {str(e)[:100]}"
            finally:
                db.close()
        
        result = await asyncio.wait_for(loop.run_in_executor(None, _run_ai), timeout=REQUEST_TIMEOUT_SECONDS)
        return result if result else "⚠️ No response generated."
        
    except asyncio.TimeoutError:
        return "⚠️ Request timeout. Please try again."
    except Exception as e:
        logger.exception(f"[{request_id}] AI failed: {e}")
        return "⚠️ Processing error. Please try again."


async def _handle_help_command(phone_number: str, request_id: str, msg_id: str = None) -> bool:
    """Handle help command - FIXED version."""
    if WHATSAPP_SERVICE_AVAILABLE:
        try:
            # send_help_message returns a Dict, not a string
            result = send_help_message(phone_number)
            if result.get("success"):
                logger.info(f"[{request_id}] Help message sent")
                return True
        except Exception as e:
            logger.error(f"[{request_id}] Help command failed: {e}")
    
    # Fallback
    await _send_response(phone_number, "📋 Type 'help' for available commands.", request_id, msg_id)
    return True


@router.post("/")
async def receive_message(request: Request):
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode('utf-8'))
        
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        if value.get("statuses"):
            return {"success": True, "type": "status_update"}
        
        messages = value.get("messages", [])
        if not messages:
            return {"success": True, "type": "no_messages"}
        
        for message in messages:
            phone_number = message.get("from")
            msg_id = message.get("id")
            msg_type = message.get("type", "unknown")
            
            if msg_type != "text":
                await _send_response(phone_number, "📱 Please send text messages only.", request_id, msg_id)
                continue
            
            user_message = message.get("text", {}).get("body", "").strip()
            if not user_message:
                continue
            
            # Handle help command - FIXED
            if user_message.lower() in ["help", "menu", "commands"]:
                await _handle_help_command(phone_number, request_id, msg_id)
                continue
            
            response = await _process_with_ai(user_message, phone_number, request_id)
            await _send_response(phone_number, response, request_id, msg_id)
        
        processing_time = (time.time() - start_time) * 1000
        return {"success": True, "request_id": request_id, "processing_time_ms": round(processing_time, 2)}
        
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON"})
    except Exception as e:
        logger.exception(f"[{request_id}] Error: {e}")
        return {"success": False, "error": str(e), "request_id": request_id}


@router.get("/health")
async def health_check():
    return {
        "status": "healthy" if (AI_SERVICE_AVAILABLE and WHATSAPP_SERVICE_AVAILABLE) else "degraded",
        "version": "21.1",
        "whatsapp_service": WHATSAPP_SERVICE_AVAILABLE,
        "ai_service": AI_SERVICE_AVAILABLE
    }


@router.get("/status")
async def status():
    return {
        "service": "WhatsApp Webhook",
        "version": "21.1",
        "status": "running",
        "message": "Ready to receive messages"
    }


logger.info("=" * 60)
logger.info("📡 WEBHOOK v21.1 - Fixed Async Issues")
logger.info("=" * 60)
