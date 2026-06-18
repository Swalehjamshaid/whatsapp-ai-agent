# ==========================================================
# FILE: app/routes/webhook.py (v19.1 - 422 FIXED)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - Thin Communication Layer
#
# 🔥 CRITICAL FIX: 422 Unprocessable Entity eliminated
# ==========================================================

import re
import uuid
import asyncio
import time
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request, BackgroundTasks, Query, HTTPException
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
RATE_LIMIT_REQUESTS = getattr(config, 'WHATSAPP_RATE_LIMIT', 100)
RATE_LIMIT_WINDOW = 60
PROCESSING_TIMEOUT_SECONDS = 20
MAX_MESSAGE_LENGTH = 4000
CONVERSATION_TTL_SECONDS = 1800

# Thread pool configuration
CPU_COUNT = 2  # Default for safety
MAX_WORKERS = 8

# ==========================================================
# GLOBALS
# ==========================================================

# Message deduplication (TTLCache - auto-expires after 24 hours)
_processed_messages = TTLCache(maxsize=50000, ttl=86400)

# Rate limiting (TTLCache - auto-expires after window)
_phone_rate_limits = TTLCache(maxsize=50000, ttl=RATE_LIMIT_WINDOW)

# Thread pool for background tasks
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="webhook_worker")

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

# ==========================================================
# RATE LIMITING
# ==========================================================

def check_rate_limit(phone_number: str) -> bool:
    now = time.time()
    requests = _phone_rate_limits.get(phone_number, [])
    recent = [t for t in requests if now - t < RATE_LIMIT_WINDOW]
    
    if len(recent) >= RATE_LIMIT_REQUESTS:
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
        return True
    _processed_messages[message_id] = time.time()
    return False

# ==========================================================
# WHATSAPP RESPONSE SENDER
# ==========================================================

async def send_whatsapp_response(phone_number: str, message: str, message_id: str, request_id: str):
    """Send response via WhatsApp Service"""
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_executor, send_text_message, phone_number, message, message_id, request_id)
        logger.debug(f"[{request_id}] Response sent")
        return
    except Exception as e:
        logger.warning(f"[{request_id}] WhatsApp service failed: {e}")

# ==========================================================
# CORE MESSAGE PROCESSING
# ==========================================================

async def handle_message(phone_number: str, message_text: str, sender_name: str, message_id: str, request_id: str):
    """Main message handler - thin orchestration layer"""
    start_time = time.time()
    struct_log = get_structured_logger(request_id, phone_number, message_id)
    
    try:
        struct_log.info(f"Processing message: {message_text[:50]}...")
        
        # Call AI Provider Service
        loop = asyncio.get_event_loop()
        
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(_executor, process_whatsapp_query, message_text, None, phone_number, None, request_id),
                timeout=PROCESSING_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            struct_log.error("Processing timeout")
            await send_whatsapp_response(
                phone_number,
                "⏳ Your request is taking longer than expected. I'll respond shortly.",
                message_id,
                request_id
            )
            return
        except Exception as e:
            struct_log.exception(f"Processing error: {e}")
            await send_whatsapp_response(
                phone_number,
                "⚠️ I encountered an error. Please try again or type 'Help'.",
                message_id,
                request_id
            )
            return
        
        # Send response
        await send_whatsapp_response(phone_number, response, message_id, request_id)
        
    except Exception as e:
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
        struct_log.bind(duration_ms=duration_ms).info("Message processing complete")

# ==========================================================
# 🔥 FIXED: WEBHOOK VERIFICATION - NO PYDANTIC
# ==========================================================

@router.get("/webhook")
@router.get("/webhook/")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Meta WhatsApp webhook verification endpoint"""
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
# 🔥 FIXED: MAIN WEBHOOK - NO PYDANTIC, ALWAYS 200
# ==========================================================

@router.post("/webhook")
@router.post("/webhook/")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    🔥 CRITICAL: This endpoint MUST return 200 OK for EVERY request
    🔥 CRITICAL: NO Pydantic validation - manual JSON parsing only
    🔥 CRITICAL: Meta requires 200 OK or they will retry
    """
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 STEP 1: Read raw body (MUST do this first)
    # ═══════════════════════════════════════════════════════════════
    
    try:
        raw_body = await request.body()
    except Exception as e:
        logger.error(f"Failed to read request body: {e}")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 STEP 2: Manual JSON parsing (NO PYDANTIC)
    # ═══════════════════════════════════════════════════════════════
    
    try:
        data = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed: {e}")
        logger.error(f"Raw body: {raw_body[:500]}")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 STEP 3: Validate it's a WhatsApp webhook
    # ═══════════════════════════════════════════════════════════════
    
    if not data or data.get('object') != 'whatsapp_business_account':
        logger.debug("Not a WhatsApp webhook payload")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 STEP 4: Extract messages (manual - NO PYDANTIC)
    # ═══════════════════════════════════════════════════════════════
    
    try:
        entries = data.get('entry') or []
        if not entries:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        changes = entries[0].get('changes') or []
        if not changes:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        value = changes[0].get('value') or {}
        
        # Handle status updates (no response needed)
        if 'statuses' in value:
            logger.debug("Status update received - ignoring")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        # Extract message
        messages = value.get('messages') or []
        if not messages:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        message = messages[0]
        phone_number = message.get('from')
        message_id = message.get('id')
        message_type = message.get('type')
        
        # Validate required fields
        if not phone_number or not message_id:
            logger.warning("Missing phone_number or message_id in payload")
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
            logger.debug(f"No text content in message: {message_type}")
            return JSONResponse({"status": "ok"}, status_code=200)
        
    except Exception as e:
        logger.error(f"Error extracting message: {e}")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 STEP 5: Deduplicate and rate limit
    # ═══════════════════════════════════════════════════════════════
    
    if is_duplicate_message(message_id):
        logger.debug(f"Duplicate: {message_id}")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    if not check_rate_limit(phone_number):
        logger.info(f"Rate limited: {mask_sensitive_data(phone_number)}")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 STEP 6: Process message in background
    # ═══════════════════════════════════════════════════════════════
    
    # Get sender name
    contacts = value.get('contacts') or []
    sender_name = contacts[0].get('profile', {}).get('name', 'User') if contacts else 'User'
    
    # Generate request ID
    request_id = generate_request_id()
    
    # Log received message
    struct_log = get_structured_logger(request_id, phone_number, message_id)
    struct_log.info(f"📨 Message received: {message_text[:50]}...")
    
    # Queue background processing
    background_tasks.add_task(handle_message, phone_number, message_text.strip(), sender_name, message_id, request_id)
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 STEP 7: ALWAYS RETURN 200 OK TO META
    # ═══════════════════════════════════════════════════════════════
    
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
        'status': 'healthy',
        'version': '19.1',
        'timestamp': datetime.now().isoformat(),
        'config': {
            'rate_limit': RATE_LIMIT_REQUESTS,
            'rate_window': RATE_LIMIT_WINDOW,
            'timeout_seconds': PROCESSING_TIMEOUT_SECONDS,
            'max_workers': MAX_WORKERS
        }
    }

@router.get("/webhook/self-test")
async def webhook_self_test():
    return {
        "status": "running",
        "version": "19.1",
        "timestamp": datetime.now().isoformat(),
        "whatsapp_token": bool(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')),
        "phone_number_id": bool(getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')),
        "verify_token": bool(getattr(config, 'WHATSAPP_VERIFY_TOKEN', ''))
    }

# ==========================================================
# GRACEFUL SHUTDOWN
# ==========================================================

@router.on_event("shutdown")
async def shutdown_webhook():
    logger.info("Webhook shutting down...")
    _executor.shutdown(wait=True, cancel_futures=False)
    logger.success("Webhook shutdown complete")

# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("Webhook v19.1 - 422 FIXED")
logger.info("=" * 60)
logger.info(f"  WhatsApp Token: {'✅' if getattr(config, 'WHATSAPP_ACCESS_TOKEN', '') else '❌'}")
logger.info(f"  Phone Number ID: {'✅' if getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '') else '❌'}")
logger.info(f"  Verify Token: {'✅' if getattr(config, 'WHATSAPP_VERIFY_TOKEN', '') else '❌'}")
logger.info(f"  Thread Pool: {MAX_WORKERS} workers")
logger.info(f"  Timeout: {PROCESSING_TIMEOUT_SECONDS}s")
logger.info("=" * 60)
logger.info("✅ 422 FIX: Manual JSON parsing only")
logger.info("✅ 422 FIX: No Pydantic validation")
logger.info("✅ 422 FIX: Always returns 200 OK")
logger.info("=" * 60)
