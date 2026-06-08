# ==========================================================
# FILE: app/routes/webhook.py (FIXED v18.0)
# ==========================================================
# CRITICAL FIX: Removed send_typing_indicator import
# ==========================================================

import os
import json
import time
import re
import uuid
import hmac
import hashlib
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from collections import deque
from contextvars import ContextVar

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from app.config import config
from app.database import get_db, SessionLocal

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_WHATSAPP_LENGTH = 3500
MAX_MESSAGE_LENGTH = 1000
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]
AI_TIMEOUT_SECONDS = 30
RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_WINDOW = 60

# ==========================================================
# IMPORTS - FIXED: Removed send_typing_indicator
# ==========================================================

AI_SERVICE_AVAILABLE = False

try:
    from app.services.ai_query_service import process_whatsapp_query
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ AI Query Service loaded")
except Exception as e:
    logger.error(f"❌ AI Service failed: {e}")

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# CRITICAL FIX: Only import what exists!
try:
    from app.services.whatsapp_service import send_text_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp service loaded")
except Exception as e:
    WHATSAPP_SERVICE_AVAILABLE = False
    logger.error(f"❌ WhatsApp service failed: {e}")

# ==========================================================
# DEBUG: Log configuration status
# ==========================================================

logger.info("=" * 60)
logger.info("🔍 CONFIGURATION STATUS:")
logger.info(f"   WHATSAPP_SERVICE_AVAILABLE: {WHATSAPP_SERVICE_AVAILABLE}")
logger.info(f"   AI_SERVICE_AVAILABLE: {AI_SERVICE_AVAILABLE}")
logger.info(f"   WHATSAPP_ACCESS_TOKEN: {'✓ SET' if config.WHATSAPP_ACCESS_TOKEN else '✗ MISSING'}")
logger.info(f"   WHATSAPP_PHONE_NUMBER_ID: {'✓ SET' if config.WHATSAPP_PHONE_NUMBER_ID else '✗ MISSING'}")
logger.info("=" * 60)

# ==========================================================
# WEBHOOK VERIFICATION
# ==========================================================

@router.get("/")
async def webhook_verification(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN and hub_challenge:
        logger.success("✅ Webhook verified!")
        return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Verification failed")

# ==========================================================
# WHATSAPP SENDER - FIXED: No mock mode, always try to send
# ==========================================================

async def send_whatsapp_message(phone_number: str, message: str, request_id: str) -> Dict:
    """Send WhatsApp message - always tries real API"""
    
    if not WHATSAPP_SERVICE_AVAILABLE:
        logger.error(f"[{request_id}] ❌ WhatsApp service NOT available!")
        logger.error(f"[{request_id}] 💡 Check: WhatsApp service import and configuration")
        return {"success": False, "error": "Service not available"}
    
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.error(f"[{request_id}] ❌ Missing WhatsApp credentials!")
        logger.error(f"[{request_id}] 💡 Add WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID to Railway")
        return {"success": False, "error": "Missing credentials"}
    
    if len(message) > MAX_WHATSAPP_LENGTH:
        message = message[:MAX_WHATSAPP_LENGTH] + "\n\n... (truncated)"
    
    for attempt in range(MAX_RETRIES):
        try:
            result = send_text_message(phone_number, message)
            
            logger.info(f"[{request_id}] WhatsApp API Response: success={result.get('success')}, "
                       f"status={result.get('status_code')}, "
                       f"message_id={result.get('message_id')}")
            
            if result.get("success"):
                return result
            elif attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            else:
                return result
                
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                logger.exception(f"[{request_id}] Send failed: {e}")
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}

# ==========================================================
# AI PROCESSING
# ==========================================================

async def process_with_timeout(question: str, phone_number: str, request_id: str) -> str:
    """Process AI query with timeout"""
    
    if not AI_SERVICE_AVAILABLE:
        return "⚠️ AI service is unavailable. Please try again later."
    
    logger.info(f"[{request_id}] 🤖 Processing: {question[:50]}")
    
    def _run_ai():
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            result = process_whatsapp_query(question, db, phone_number, "guest")
            return result if result else "⚠️ No response generated."
        except Exception as e:
            logger.exception(f"AI error: {e}")
            return f"⚠️ Error: {str(e)[:100]}"
        finally:
            db.close()
    
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_ai),
            timeout=AI_TIMEOUT_SECONDS
        )
        logger.info(f"[{request_id}] ✅ AI completed, response length: {len(result)}")
        return result
    except asyncio.TimeoutError:
        logger.error(f"[{request_id}] ⏰ AI timeout")
        return "⚠️ Request timeout. Please try again."
    except Exception as e:
        logger.exception(f"[{request_id}] AI error: {e}")
        return f"⚠️ Processing error: {str(e)[:100]}"

# ==========================================================
# MAIN WEBHOOK ENDPOINT
# ==========================================================

@router.post("/")
async def receive_message(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    request_id = str(uuid.uuid4())[:8]
    logger.info(f"[{request_id}] 📨 Webhook received")
    
    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode('utf-8'))
        
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates
        if value.get("statuses"):
            logger.info(f"[{request_id}] Status update ignored")
            return {"success": True}
        
        # Get messages
        messages = value.get("messages", [])
        if not messages:
            logger.warning(f"[{request_id}] No messages")
            return {"success": True}
        
        # Process each message
        for message in messages:
            msg_type = message.get("type", "unknown")
            phone_number = message.get("from")
            msg_id = message.get("id")
            
            logger.info(f"[{request_id}] 📱 From: {phone_number}, Type: {msg_type}")
            
            if msg_type != "text":
                await send_whatsapp_message(phone_number, "📱 Please send text messages only.", request_id)
                continue
            
            user_message = message.get("text", {}).get("body", "")
            if not user_message:
                continue
            
            logger.info(f"[{request_id}] 💬 Message: {user_message[:100]}")
            
            # Check if it's a DN query
            if re.match(r'^\d{10,15}$', user_message.strip()):
                logger.info(f"[{request_id}] 🔢 DN QUERY: {user_message}")
            
            # Process with AI
            response = await process_with_timeout(user_message, phone_number, request_id)
            
            # Send response
            send_result = await send_whatsapp_message(phone_number, response, request_id)
            
            logger.info(f"[{request_id}] 📤 Send result: {send_result.get('success')}")
        
        return {"success": True, "request_id": request_id}
        
    except Exception as e:
        logger.exception(f"[{request_id}] Webhook error: {e}")
        return {"success": False, "error": str(e)}

# ==========================================================
# HEALTH ENDPOINTS
# ==========================================================

@router.get("/health")
async def health_check():
    return {
        "status": "healthy" if (AI_SERVICE_AVAILABLE and WHATSAPP_SERVICE_AVAILABLE) else "degraded",
        "version": "18.0",
        "whatsapp_service": WHATSAPP_SERVICE_AVAILABLE,
        "ai_service": AI_SERVICE_AVAILABLE,
        "credentials": {
            "token": "✓" if config.WHATSAPP_ACCESS_TOKEN else "✗",
            "phone_id": "✓" if config.WHATSAPP_PHONE_NUMBER_ID else "✗"
        }
    }

@router.get("/test-dn/{dn_number}")
async def test_dn_lookup(dn_number: str):
    from app.services.logistics_query_service import LogisticsQueryService
    
    db = SessionLocal()
    try:
        service = LogisticsQueryService(db)
        result = service.get_complete_dn_intelligence(dn_number)
        return {"found": "error" not in result, "result": result}
    except Exception as e:
        return {"found": False, "error": str(e)}
    finally:
        db.close()

@router.get("/status")
async def status():
    return {
        "service": "Webhook v18.0",
        "whatsapp_service": WHATSAPP_SERVICE_AVAILABLE,
        "ai_service": AI_SERVICE_AVAILABLE,
        "message": "Ready to receive messages"
    }
