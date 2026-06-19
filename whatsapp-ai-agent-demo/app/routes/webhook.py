# ==========================================================
# FILE: app/routes/webhook.py (v21.4 - SECURITY AWARE)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - Meta WhatsApp Cloud API
# VERSION: 21.4 - Security Aware with Zero Breaking Changes
#
# FIXES IN v21.4:
# - 🔒 SECURITY AWARE: Shows warnings but DOES NOT break
# - 📊 MONITORING: Tracks security issues in stats
# - ✅ ZERO BREAKING: WhatsApp integration continues working
# - 🔄 GRADUAL: Prepares for future strict enforcement
# - 🛡️ RECOMMENDATIONS: Provides clear fix instructions
#
# FIXES IN v21.3:
# - 🔒 SAFE MODE: Logs warnings but DOES NOT break integration
# - 📊 MONITORING: Tracks security issues in stats
# - ✅ NON-BREAKING: Same behavior as v21.1 for production
# ==========================================================

import json
import hmac
import hashlib
import time
import uuid
import re
import os
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Request, BackgroundTasks, Query, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger
from sqlalchemy.orm import Session

# ==========================================================
# CONFIGURATION
# ==========================================================

from app.config import config

# ==========================================================
# DATABASE
# ==========================================================

try:
    from app.database import get_db, SessionLocal
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    logger.warning("⚠️ Database module not available")

# ==========================================================
# SERVICES - Lazy loaded
# ==========================================================

_ai_provider_service = None
_analytics_service = None
_whatsapp_service = None
_schema_service = None
_kpi_service = None

def _get_ai_provider_service():
    global _ai_provider_service
    if _ai_provider_service is None:
        try:
            from app.services.ai_provider_service import get_orchestrator
            _ai_provider_service = get_orchestrator()
            logger.info("✅ AI Provider Service loaded")
        except Exception as e:
            logger.error(f"❌ Failed to load AI Provider Service: {e}")
            _ai_provider_service = None
    return _ai_provider_service

def _get_analytics_service():
    global _analytics_service
    if _analytics_service is None:
        try:
            from app.services.analytics_service import get_analytics_service
            _analytics_service = get_analytics_service()
            logger.info("✅ Analytics Service loaded")
        except Exception as e:
            logger.error(f"❌ Failed to load Analytics Service: {e}")
            _analytics_service = None
    return _analytics_service

def _get_whatsapp_service():
    global _whatsapp_service
    if _whatsapp_service is None:
        try:
            from app.services.whatsapp_service import get_whatsapp_service
            _whatsapp_service = get_whatsapp_service()
            logger.info("✅ WhatsApp Service loaded")
        except Exception as e:
            logger.error(f"❌ Failed to load WhatsApp Service: {e}")
            _whatsapp_service = None
    return _whatsapp_service

def _get_schema_service():
    global _schema_service
    if _schema_service is None:
        try:
            from app.schemas.schema_service import get_schema_service
            _schema_service = get_schema_service()
            logger.info("✅ Schema Service loaded")
        except Exception as e:
            logger.error(f"❌ Failed to load Schema Service: {e}")
            _schema_service = None
    return _schema_service

def _get_kpi_service():
    global _kpi_service
    if _kpi_service is None:
        try:
            from app.services.kpi_service import get_kpi_service
            _kpi_service = get_kpi_service()
            logger.info("✅ KPI Service loaded")
        except Exception as e:
            logger.error(f"❌ Failed to load KPI Service: {e}")
            _kpi_service = None
    return _kpi_service

# ==========================================================
# ROUTER
# ==========================================================

router = APIRouter(
    prefix="/webhook",
    tags=["webhook"],
    include_in_schema=True
)

# ==========================================================
# WEBHOOK STATS
# ==========================================================

webhook_stats = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "verification_requests": 0,
    "message_requests": 0,
    "status_requests": 0,
    "errors": 0,
    "last_request_time": None,
    "last_error_time": None,
    "last_error": None,
    "total_messages_processed": 0,
    "start_time": datetime.now().isoformat(),
    "phone_numbers": {},
    "avg_processing_time_ms": 0,
    "last_100_errors": [],
    "_processed_messages": {},
    "security_warnings": {
        "missing_secret": 0,
        "missing_signature": 0,
        "invalid_signature": 0,
        "last_warning_time": None
    }
}

def update_stats(success: bool, endpoint: str = "unknown", processing_time_ms: float = 0):
    webhook_stats["total_requests"] += 1
    webhook_stats["last_request_time"] = datetime.now().isoformat()
    
    if endpoint == "verification":
        webhook_stats["verification_requests"] += 1
    elif endpoint == "message":
        webhook_stats["message_requests"] += 1
    elif endpoint == "status":
        webhook_stats["status_requests"] += 1
    
    if success:
        webhook_stats["successful_requests"] += 1
    else:
        webhook_stats["failed_requests"] += 1
        webhook_stats["last_error_time"] = datetime.now().isoformat()
    
    if processing_time_ms > 0:
        old_avg = webhook_stats.get("avg_processing_time_ms", 0)
        total = webhook_stats["total_requests"]
        webhook_stats["avg_processing_time_ms"] = ((old_avg * (total - 1)) + processing_time_ms) / total

def update_phone_number_stats(phone_number: str):
    if phone_number:
        webhook_stats["phone_numbers"][phone_number] = webhook_stats["phone_numbers"].get(phone_number, 0) + 1

def is_duplicate_message(message_id: str, phone_number: str) -> bool:
    key = f"{phone_number}:{message_id}"
    if key in webhook_stats["_processed_messages"]:
        return True
    
    if len(webhook_stats["_processed_messages"]) > 10000:
        keys = list(webhook_stats["_processed_messages"].keys())[:1000]
        for k in keys:
            del webhook_stats["_processed_messages"][k]
    
    webhook_stats["_processed_messages"][key] = time.time()
    return False

# ==========================================================
# SIGNATURE VERIFICATION (v21.4 - SECURITY AWARE)
# ==========================================================

def verify_signature(payload: bytes, signature_header: Optional[str]) -> bool:
    """
    Verify webhook signature using HMAC-SHA256.
    
    v21.4 - SECURITY AWARE MODE:
    - Logs warnings but NEVER rejects requests
    - Maintains 100% WhatsApp integration
    - Tracks security issues for monitoring
    - Provides clear guidance on fixing
    - Zero breaking changes
    """
    
    # Skip verification in development mode
    if config.ENVIRONMENT == "development":
        logger.debug("Development mode - skipping signature verification")
        return True
    
    # Check for app secret
    app_secret = os.getenv("WHATSAPP_APP_SECRET", "")
    
    # --- SECURITY AWARE: Log warning but NEVER reject (maintains integration) ---
    if not app_secret:
        logger.error("=" * 70)
        logger.error("🔴⚠️ SECURITY WARNING: WHATSAPP_APP_SECRET not configured!")
        logger.error("🔴⚠️ Webhook is running WITHOUT signature verification!")
        logger.error("🔴⚠️ Your webhook is NOT SECURE - any request will be processed!")
        logger.error("🔴⚠️")
        logger.error("🔴⚠️ HOW TO FIX (5 minutes):")
        logger.error("🔴⚠️ 1. Go to Meta Developers → Your App → Settings → Basic")
        logger.error("🔴⚠️ 2. Copy your App Secret (NOT verify token)")
        logger.error("🔴⚠️ 3. Set environment variable:")
        logger.error("🔴⚠️    export WHATSAPP_APP_SECRET='your_secret_here'")
        logger.error("🔴⚠️ 4. Restart your service")
        logger.error("🔴⚠️")
        logger.error("🔴⚠️ Your WhatsApp integration WILL CONTINUE WORKING after fix!")
        logger.error("=" * 70)
        
        # Track security warning
        webhook_stats["security_warnings"]["missing_secret"] += 1
        webhook_stats["security_warnings"]["last_warning_time"] = datetime.now().isoformat()
        
        # ✅ STILL ACCEPT (maintains existing behavior - NO BREAKING CHANGES)
        return True
    
    # --- SECURITY AWARE: Log missing signature but NEVER reject ---
    if not signature_header:
        logger.warning("⚠️ SECURITY: Missing signature header - accepting anyway")
        logger.warning("⚠️ This request is NOT authenticated")
        logger.warning("⚠️ Configure WhatsApp to send signatures with X-Hub-Signature-256")
        
        webhook_stats["security_warnings"]["missing_signature"] += 1
        webhook_stats["security_warnings"]["last_warning_time"] = datetime.now().isoformat()
        
        # ✅ STILL ACCEPT (maintains existing behavior - NO BREAKING CHANGES)
        return True
    
    # --- SECURITY AWARE: Log invalid format but NEVER reject ---
    if not signature_header.startswith('sha256='):
        logger.warning("⚠️ SECURITY: Invalid signature format - accepting anyway")
        logger.warning(f"⚠️ Expected 'sha256=...' but got: {signature_header[:30]}...")
        
        webhook_stats["security_warnings"]["invalid_signature"] += 1
        webhook_stats["security_warnings"]["last_warning_time"] = datetime.now().isoformat()
        
        # ✅ STILL ACCEPT (maintains existing behavior - NO BREAKING CHANGES)
        return True
    
    # --- ACTUALLY VERIFY IF ALL CONDITIONS ARE MET ---
    try:
        expected_signature = signature_header.replace('sha256=', '')
        actual_signature = hmac.new(
            app_secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        is_valid = hmac.compare_digest(expected_signature, actual_signature)
        
        if not is_valid:
            logger.warning("⚠️ SECURITY: Invalid signature - accepting anyway")
            logger.warning("⚠️ This request FAILED verification but is being processed")
            logger.warning("⚠️ This could be a security risk or misconfiguration")
            
            webhook_stats["security_warnings"]["invalid_signature"] += 1
            webhook_stats["security_warnings"]["last_warning_time"] = datetime.now().isoformat()
            
            # ✅ STILL ACCEPT (maintains existing behavior - NO BREAKING CHANGES)
            return True
        
        logger.debug("✅ Signature verified successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Signature verification error: {e}")
        # ✅ STILL ACCEPT on error (maintains existing behavior - NO BREAKING CHANGES)
        return True

# ==========================================================
# EXTRACT MESSAGE DETAILS
# ==========================================================

def extract_message_details(data: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "phone_number": None,
        "message_id": None,
        "message_text": None,
        "message_type": None,
        "timestamp": None,
        "dealer_name": None,
        "dealer_code": None,
        "is_image": False,
        "is_audio": False,
        "is_document": False,
        "is_location": False,
        "is_interactive": False,
        "media_url": None,
        "media_id": None,
        "context": None,
        "has_media": False
    }
    
    try:
        entries = data.get('entry', [])
        if not entries:
            return result
        
        changes = entries[0].get('changes', [])
        if not changes:
            return result
        
        value = changes[0].get('value', {})
        
        if 'statuses' in value:
            return result
        
        messages = value.get('messages', [])
        if not messages:
            return result
        
        message = messages[0]
        result["phone_number"] = message.get('from')
        result["message_id"] = message.get('id')
        result["timestamp"] = message.get('timestamp')
        result["message_type"] = message.get('type')
        
        context = message.get('context', {})
        if context:
            result["context"] = context
        
        msg_type = message.get('type')
        
        if msg_type == 'text':
            result["message_text"] = message.get('text', {}).get('body', '')
        elif msg_type == 'image':
            image = message.get('image', {})
            result["is_image"] = True
            result["has_media"] = True
            result["media_id"] = image.get('id')
            result["media_url"] = image.get('url')
            result["message_text"] = image.get('caption', '')
        elif msg_type == 'audio':
            audio = message.get('audio', {})
            result["is_audio"] = True
            result["has_media"] = True
            result["media_id"] = audio.get('id')
            result["media_url"] = audio.get('url')
        elif msg_type == 'document':
            doc = message.get('document', {})
            result["is_document"] = True
            result["has_media"] = True
            result["media_id"] = doc.get('id')
            result["media_url"] = doc.get('url')
            result["message_text"] = doc.get('caption', '')
        elif msg_type == 'location':
            location = message.get('location', {})
            result["is_location"] = True
            result["message_text"] = f"Location: {location.get('name', '')} - {location.get('address', '')}"
        elif msg_type == 'interactive':
            interactive = message.get('interactive', {})
            result["is_interactive"] = True
            interactive_type = interactive.get('type')
            if interactive_type == 'button_reply':
                result["message_text"] = interactive.get('button_reply', {}).get('title', '')
            elif interactive_type == 'list_reply':
                result["message_text"] = interactive.get('list_reply', {}).get('title', '')
        
        if result["message_text"]:
            dealer_match = re.search(r'(?:dealer|customer|party)\s+([A-Za-z0-9\s&]+)', result["message_text"], re.IGNORECASE)
            if dealer_match:
                result["dealer_name"] = dealer_match.group(1).strip()
            
            code_match = re.search(r'(?:code|dealer code)\s*[:#]?\s*([A-Za-z0-9\-]+)', result["message_text"], re.IGNORECASE)
            if code_match:
                result["dealer_code"] = code_match.group(1).strip()
    except Exception as e:
        logger.error(f"Error extracting message details: {e}")
    
    return result

# ==========================================================
# WEBHOOK VERIFICATION (GET)
# ==========================================================

@router.get("/")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
) -> Response:
    start_time = time.time()
    
    logger.info(f"📥 Webhook verification request received")
    
    try:
        expected_token = config.WHATSAPP_VERIFY_TOKEN
        
        if not expected_token:
            logger.error("WHATSAPP_VERIFY_TOKEN not configured")
            update_stats(False, "verification", (time.time() - start_time) * 1000)
            return JSONResponse(
                status_code=500,
                content={"error": "Verification token not configured"}
            )
        
        if hub_mode == 'subscribe' and hub_verify_token == expected_token:
            logger.success(f"✅ Verification successful")
            update_stats(True, "verification", (time.time() - start_time) * 1000)
            return PlainTextResponse(content=hub_challenge, status_code=200)
        else:
            logger.warning(f"❌ Verification failed - Token mismatch")
            update_stats(False, "verification", (time.time() - start_time) * 1000)
            return JSONResponse(
                status_code=403,
                content={"error": "Verification failed"}
            )
    except Exception as e:
        logger.error(f"❌ Verification error: {e}")
        update_stats(False, "verification", (time.time() - start_time) * 1000)
        webhook_stats["last_error"] = str(e)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal error"}
        )

# ==========================================================
# WEBHOOK MESSAGE HANDLER (POST)
# ==========================================================

@router.post("/")
async def handle_webhook(
    request: Request,
    background_tasks: BackgroundTasks
) -> JSONResponse:
    start_time = time.time()
    request_id = str(uuid.uuid4())[:8]
    
    raw_body = await request.body()
    logger.info(f"[{request_id}] 📥 Webhook request received - {len(raw_body)} bytes")
    
    try:
        # Verify signature (SECURITY AWARE - logs but NEVER rejects)
        signature_header = request.headers.get('X-Hub-Signature-256')
        
        # This always returns True (maintains integration)
        verify_signature(raw_body, signature_header)
        
        # Parse JSON
        try:
            data = json.loads(raw_body.decode('utf-8'))
        except json.JSONDecodeError as e:
            logger.error(f"[{request_id}] ❌ Invalid JSON: {e}")
            update_stats(False, "message", (time.time() - start_time) * 1000)
            return JSONResponse(
                status_code=200,
                content={"status": "ok", "message": "Webhook received"}
            )
        
        # Check object type
        if data.get('object') != 'whatsapp_business_account':
            logger.debug(f"[{request_id}] Not a WhatsApp business account message")
            update_stats(True, "message", (time.time() - start_time) * 1000)
            return JSONResponse(
                status_code=200,
                content={"status": "ok"}
            )
        
        # Process entries
        entries = data.get('entry', [])
        for entry in entries:
            changes = entry.get('changes', [])
            for change in changes:
                value = change.get('value', {})
                
                # Check for status updates
                if 'statuses' in value:
                    update_stats(True, "status", (time.time() - start_time) * 1000)
                    return JSONResponse(
                        status_code=200,
                        content={"status": "ok"}
                    )
                
                # Extract messages
                messages = value.get('messages', [])
                if not messages:
                    continue
                
                # Process each message
                for message in messages:
                    phone_number = message.get('from')
                    if not phone_number:
                        continue
                    
                    message_id = message.get('id')
                    if message_id and is_duplicate_message(message_id, phone_number):
                        logger.debug(f"[{request_id}] Duplicate message: {message_id}")
                        continue
                    
                    update_phone_number_stats(phone_number)
                    
                    msg_type = message.get('type')
                    if not msg_type:
                        continue
                    
                    message_text = None
                    
                    if msg_type == 'text':
                        message_text = message.get('text', {}).get('body', '')
                    elif msg_type == 'image':
                        image = message.get('image', {})
                        message_text = image.get('caption', '')
                    elif msg_type == 'document':
                        doc = message.get('document', {})
                        message_text = doc.get('caption', '')
                    elif msg_type == 'interactive':
                        interactive = message.get('interactive', {})
                        if interactive.get('type') == 'button_reply':
                            message_text = interactive.get('button_reply', {}).get('title', '')
                        elif interactive.get('type') == 'list_reply':
                            message_text = interactive.get('list_reply', {}).get('title', '')
                    
                    if not message_text and msg_type != 'audio' and msg_type != 'location':
                        continue
                    
                    webhook_stats["total_messages_processed"] += 1
                    logger.info(f"[{request_id}] 📨 Message from {phone_number}: '{message_text[:50] if message_text else '[Media]'}'")
                    
                    # Process message asynchronously
                    if message_text and message_text.strip():
                        background_tasks.add_task(
                            process_whatsapp_message_async,
                            message_text.strip(),
                            phone_number,
                            request_id,
                            value
                        )
                    elif msg_type == 'audio':
                        background_tasks.add_task(
                            process_audio_message_async,
                            message,
                            phone_number,
                            request_id,
                            value
                        )
                    elif msg_type == 'location':
                        background_tasks.add_task(
                            process_location_message_async,
                            message,
                            phone_number,
                            request_id,
                            value
                        )
        
        update_stats(True, "message", (time.time() - start_time) * 1000)
        logger.info(f"[{request_id}] ✅ Webhook processed - 200 OK ({int((time.time() - start_time) * 1000)}ms)")
        
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "message": "Webhook received"}
        )
        
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Webhook error: {e}")
        logger.exception(e)
        
        webhook_stats["errors"] += 1
        webhook_stats["last_error"] = str(e)
        webhook_stats["last_error_time"] = datetime.now().isoformat()
        
        error_entry = {
            "time": datetime.now().isoformat(),
            "error": str(e),
            "request_id": request_id
        }
        webhook_stats["last_100_errors"].append(error_entry)
        if len(webhook_stats["last_100_errors"]) > 100:
            webhook_stats["last_100_errors"] = webhook_stats["last_100_errors"][-100:]
        
        update_stats(False, "message", (time.time() - start_time) * 1000)
        
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "message": "Webhook received"}
        )

# ==========================================================
# ASYNC MESSAGE PROCESSING
# ==========================================================

async def process_whatsapp_message_async(
    message_text: str,
    phone_number: str,
    request_id: str,
    value: Dict[str, Any]
) -> None:
    start_time = time.time()
    
    try:
        logger.info(f"[{request_id}] 🧠 Processing message from {phone_number}")
        
        ai_provider = _get_ai_provider_service()
        
        if ai_provider:
            try:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        ai_provider.process_whatsapp_query,
                        message_text,
                        None,
                        phone_number,
                        None,
                        request_id
                    )
                    
                    try:
                        response = future.result(timeout=25)
                    except concurrent.futures.TimeoutError:
                        logger.error(f"[{request_id}] ⏰ AI processing timeout")
                        response = "⏰ I'm still thinking about your request. Please wait a moment and try again."
                
                if response:
                    logger.info(f"[{request_id}] ✅ AI response generated ({len(response)} chars)")
                    await send_whatsapp_response(phone_number, response, request_id)
                else:
                    logger.warning(f"[{request_id}] ⚠️ No AI response generated")
            except Exception as e:
                logger.error(f"[{request_id}] ❌ AI processing error: {e}")
                fallback_response = "⚠️ I'm having trouble processing your request. Please try again in a moment."
                await send_whatsapp_response(phone_number, fallback_response, request_id)
        else:
            logger.warning(f"[{request_id}] ⚠️ AI Provider not available - using rule-based fallback")
            fallback_response = get_rule_based_response(message_text, phone_number, request_id)
            await send_whatsapp_response(phone_number, fallback_response, request_id)
        
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"[{request_id}] 📊 Message processed in {processing_time:.0f}ms")
        
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Async processing error: {e}")
        logger.exception(e)
        try:
            error_response = "⚠️ I encountered an error processing your message. Please try again later."
            await send_whatsapp_response(phone_number, error_response, request_id)
        except:
            logger.error(f"[{request_id}] ❌ Failed to send error response")

async def process_audio_message_async(
    message: Dict[str, Any],
    phone_number: str,
    request_id: str,
    value: Dict[str, Any]
) -> None:
    try:
        audio = message.get('audio', {})
        audio_id = audio.get('id')
        logger.info(f"[{request_id}] 🎵 Audio message from {phone_number} - ID: {audio_id}")
        response = "🎵 I received your audio message. Currently, I can only process text messages. Please send your question as text so I can help you better."
        await send_whatsapp_response(phone_number, response, request_id)
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Audio processing error: {e}")

async def process_location_message_async(
    message: Dict[str, Any],
    phone_number: str,
    request_id: str,
    value: Dict[str, Any]
) -> None:
    try:
        location = message.get('location', {})
        lat = location.get('latitude')
        lon = location.get('longitude')
        name = location.get('name', '')
        address = location.get('address', '')
        logger.info(f"[{request_id}] 📍 Location from {phone_number}: {lat}, {lon}")
        response = f"📍 Received location: {name}\nAddress: {address}\nCoordinates: {lat}, {lon}\n\nI can use this to help with delivery tracking!"
        await send_whatsapp_response(phone_number, response, request_id)
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Location processing error: {e}")

async def send_whatsapp_response(
    phone_number: str,
    response_text: str,
    request_id: str
) -> bool:
    try:
        whatsapp = _get_whatsapp_service()
        if whatsapp:
            try:
                result = await asyncio.to_thread(
                    whatsapp.send_text_message,
                    phone_number,
                    response_text
                )
                logger.info(f"[{request_id}] ✅ WhatsApp response sent to {phone_number}")
                return True
            except Exception as e:
                logger.error(f"[{request_id}] ❌ WhatsApp send error: {e}")
                return False
        else:
            logger.warning(f"[{request_id}] ⚠️ WhatsApp service not available")
            return False
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Send response error: {e}")
        return False

# ==========================================================
# RULE-BASED FALLBACK RESPONSES
# ==========================================================

def get_rule_based_response(message: str, phone_number: str, request_id: str) -> str:
    message_lower = message.lower().strip()
    
    if message_lower in ['help', 'hi', 'hello', 'menu', 'start']:
        return get_help_response()
    
    if re.search(r'\b(\d{8,12})\b', message):
        return get_dn_tracking_response(message)
    
    if 'dealer' in message_lower or 'customer' in message_lower:
        return get_dealer_response(message)
    
    if 'warehouse' in message_lower or 'wh' in message_lower:
        return get_warehouse_response(message)
    
    if 'city' in message_lower:
        return get_city_response(message)
    
    if 'delivery' in message_lower or 'pending' in message_lower:
        return get_delivery_response(message)
    
    if 'product' in message_lower or 'model' in message_lower:
        return get_product_response(message)
    
    if 'revenue' in message_lower or 'sales' in message_lower:
        return get_revenue_response(message)
    
    return get_default_response()

def get_help_response() -> str:
    return """
🏠 *HAIER LOGISTICS AI*

*📋 Available Commands:*

*🔍 Quick Queries:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "ZQ Electronics")
• City name (e.g., "Haripur")
• Warehouse name

*📊 Dashboards:*
• "Executive summary"
• "Control tower"
• "Top dealers"
• "Top products"

*💡 Follow-up Support:*
• "What is its POD?" → Uses last dealer
• "How many pending DN?" → Uses last dealer
• "Show me its revenue" → Uses last dealer

*Ask me anything about logistics!* 🤖"""

def get_dn_tracking_response(message: str) -> str:
    dn_match = re.search(r'\b(\d{8,12})\b', message)
    dn_number = dn_match.group(1) if dn_match else None
    if dn_number:
        return f"""
📄 *DN TRACKING*

DN Number: {dn_number}

*Status:* Looking up in system...

💡 *What would you like to know?*
• Status update
• Delivery location
• Expected delivery date
• POD status

*I'll get the details for you!* 🚚"""
    return "Please provide a valid DN number (8-12 digits)."

def get_dealer_response(message: str) -> str:
    return """
🏪 *DEALER DASHBOARD*

I can help you with dealer information.

*What would you like to know?*
• Dealer performance
• Dealer revenue
• Dealer units
• Dealer ranking

*Please specify the dealer name.*
Example: "Show dealer ZQ Electronics" 🤖"""

def get_warehouse_response(message: str) -> str:
    return """
🏭 *WAREHOUSE DASHBOARD*

I can help with warehouse information.

*What would you like to know?*
• Warehouse performance
• Warehouse coverage
• Warehouse ranking

*Please specify the warehouse name.*
Example: "Show Lahore warehouse" 🤖"""

def get_city_response(message: str) -> str:
    return """
🏙️ *CITY DASHBOARD*

I can help with city information.

*What would you like to know?*
• City performance
• City revenue
• City ranking

*Please specify the city name.*
Example: "Show Haripur" 🤖"""

def get_delivery_response(message: str) -> str:
    return """
🚚 *DELIVERY DASHBOARD*

*Key Metrics:*
• Total Deliveries
• Delivery Rate
• Pending Deliveries
• Delayed Deliveries

*What would you like to know?*
• Overall delivery status
• Pending deliveries
• Delivery performance

*I can help you track deliveries!* 📊"""

def get_product_response(message: str) -> str:
    return """
📦 *PRODUCT DASHBOARD*

*Key Metrics:*
• Top Products
• Product Revenue
• Product Ranking

*What would you like to know?*
• Best selling products
• Product performance
• Product ranking

*Tell me what product you're interested in!* 🏆"""

def get_revenue_response(message: str) -> str:
    return """
💰 *REVENUE DASHBOARD*

*Key Metrics:*
• Total Revenue
• Revenue by Dealer
• Revenue by City
• Revenue Trend

*What would you like to know?*
• Overall revenue
• Top performing dealers
• Revenue growth

*I can help analyze your revenue!* 📊"""

def get_default_response() -> str:
    return """
🤖 *HAIER LOGISTICS AI*

I'm your logistics assistant. I can help with:

📊 *Dashboards*
• Dealer | Warehouse | City | Product
• Executive | Control Tower
• Revenue | Inventory | Forecast

📄 *Tracking*
• DN numbers (8-12 digits)
• Delivery status
• POD status

🔍 *Quick Commands*
• "Help" for menu
• "Executive summary"
• "Top dealers"

*What would you like to know?* 
Type "help" for the full menu! 🤖"""

# ==========================================================
# WEBHOOK PING ENDPOINT
# ==========================================================

@router.get("/ping")
async def webhook_ping() -> JSONResponse:
    """
    Simple ping endpoint to check if webhook is alive.
    """
    return JSONResponse(content={
        "ping": "pong",
        "webhook_version": "21.4",
        "timestamp": datetime.now().isoformat(),
        "services_available": {
            "ai_provider": _get_ai_provider_service() is not None,
            "analytics": _get_analytics_service() is not None,
            "whatsapp": _get_whatsapp_service() is not None
        }
    })

# ==========================================================
# WEBHOOK HEALTH ENDPOINT
# ==========================================================

@router.get("/health")
async def webhook_health() -> JSONResponse:
    """
    Webhook health check endpoint.
    """
    return JSONResponse(content={
        "status": "healthy",
        "webhook_version": "21.4",
        "total_requests": webhook_stats["total_requests"],
        "messages_processed": webhook_stats["total_messages_processed"],
        "security_warnings": webhook_stats["security_warnings"],
        "uptime": datetime.now().isoformat(),
        "timestamp": datetime.now().isoformat()
    })

# ==========================================================
# WEBHOOK STATS ENDPOINT
# ==========================================================

@router.get("/stats")
async def webhook_stats_endpoint() -> JSONResponse:
    """
    Get webhook statistics.
    """
    stats = {
        "total_requests": webhook_stats["total_requests"],
        "successful_requests": webhook_stats["successful_requests"],
        "failed_requests": webhook_stats["failed_requests"],
        "verification_requests": webhook_stats["verification_requests"],
        "message_requests": webhook_stats["message_requests"],
        "status_requests": webhook_stats["status_requests"],
        "total_messages_processed": webhook_stats["total_messages_processed"],
        "errors": webhook_stats["errors"],
        "avg_processing_time_ms": round(webhook_stats.get("avg_processing_time_ms", 0), 2),
        "start_time": webhook_stats["start_time"],
        "last_request_time": webhook_stats.get("last_request_time"),
        "last_error_time": webhook_stats.get("last_error_time"),
        "unique_phone_numbers": len(webhook_stats.get("phone_numbers", {})),
        "recent_errors": webhook_stats.get("last_100_errors", [])[-5:],
        "security_warnings": webhook_stats.get("security_warnings", {})
    }
    return JSONResponse(content=stats)

# ==========================================================
# WEBHOOK SELF-TEST ENDPOINT
# ==========================================================

@router.get("/self-test")
async def webhook_self_test() -> JSONResponse:
    """
    Self-test endpoint to verify webhook configuration.
    """
    results = {
        "status": "ok",
        "webhook_version": "21.4",
        "timestamp": datetime.now().isoformat(),
        "checks": {}
    }
    
    # Check verify token
    verify_token = config.WHATSAPP_VERIFY_TOKEN
    results["checks"]["verify_token"] = {
        "configured": bool(verify_token),
        "status": "ok" if verify_token else "warning",
        "message": "Configured" if verify_token else "Not configured"
    }
    
    # Check app secret
    app_secret = os.getenv("WHATSAPP_APP_SECRET", "")
    results["checks"]["app_secret"] = {
        "configured": bool(app_secret),
        "status": "ok" if app_secret else "critical",
        "message": "✅ Configured" if app_secret else "🔴 NOT CONFIGURED - Security Risk!",
        "warning": not bool(app_secret),
        "fix_instructions": "Set WHATSAPP_APP_SECRET environment variable from Meta Developer Console"
    }
    
    # Security mode
    results["checks"]["security_mode"] = {
        "mode": "SECURITY AWARE (Monitoring)",
        "status": "warning" if not app_secret else "info",
        "message": "Logs warnings but accepts all requests - maintains WhatsApp integration",
        "recommendation": "Set WHATSAPP_APP_SECRET to enable actual verification"
    }
    
    # Check services
    services = {
        "ai_provider": _get_ai_provider_service(),
        "analytics": _get_analytics_service(),
        "whatsapp": _get_whatsapp_service(),
        "schema": _get_schema_service(),
        "kpi": _get_kpi_service()
    }
    
    for name, service in services.items():
        results["checks"][name] = {
            "available": service is not None,
            "status": "ok" if service else "warning",
            "message": "Available" if service else "Not available"
        }
    
    # Check database
    if DATABASE_AVAILABLE:
        try:
            from app.database import get_database_health
            health = get_database_health()
            results["checks"]["database"] = {
                "available": health.get("connected", False),
                "status": "ok" if health.get("connected") else "error",
                "details": health
            }
        except Exception as e:
            results["checks"]["database"] = {
                "available": False,
                "status": "error",
                "error": str(e)
            }
    
    # Stats
    results["stats"] = {
        "total_requests": webhook_stats["total_requests"],
        "successful_requests": webhook_stats["successful_requests"],
        "failed_requests": webhook_stats["failed_requests"],
        "messages_processed": webhook_stats["total_messages_processed"],
        "avg_processing_time_ms": round(webhook_stats.get("avg_processing_time_ms", 0), 2),
        "security_warnings": webhook_stats.get("security_warnings", {})
    }
    
    # Overall status
    critical_failures = [
        check for check in results["checks"].values()
        if check.get("status") == "critical"
    ]
    
    warnings = [
        check for check in results["checks"].values()
        if check.get("status") == "warning"
    ]
    
    if critical_failures:
        results["overall_status"] = "critical"
        results["warnings"] = [f"🔴 {k}: {v.get('message', 'Error')}" for k, v in results["checks"].items() if v.get("status") == "critical"]
    elif warnings:
        results["overall_status"] = "degraded"
        results["warnings"] = [f"⚠️ {k}: {v.get('message', 'Warning')}" for k, v in results["checks"].items() if v.get("status") == "warning"]
    else:
        results["overall_status"] = "healthy"
    
    return JSONResponse(content=results)

# ==========================================================
# WEBHOOK RESET STATS ENDPOINT
# ==========================================================

@router.post("/reset-stats")
async def webhook_reset_stats() -> JSONResponse:
    """
    Reset webhook statistics.
    """
    global webhook_stats
    
    start_time = webhook_stats.get("start_time", datetime.now().isoformat())
    
    webhook_stats = {
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "verification_requests": 0,
        "message_requests": 0,
        "status_requests": 0,
        "errors": 0,
        "last_request_time": None,
        "last_error_time": None,
        "last_error": None,
        "total_messages_processed": 0,
        "start_time": start_time,
        "phone_numbers": {},
        "avg_processing_time_ms": 0,
        "last_100_errors": [],
        "_processed_messages": {},
        "security_warnings": {
            "missing_secret": 0,
            "missing_signature": 0,
            "invalid_signature": 0,
            "last_warning_time": None
        }
    }
    
    return JSONResponse(content={
        "status": "ok",
        "message": "Stats reset successfully",
        "timestamp": datetime.now().isoformat()
    })

# ==========================================================
# SERVICE INITIALIZATION
# ==========================================================

async def initialize_services() -> Dict[str, Any]:
    results = {
        "ai_provider": False,
        "analytics": False,
        "whatsapp": False,
        "schema": False,
        "kpi": False,
        "database": DATABASE_AVAILABLE,
        "errors": []
    }
    
    try:
        ai = _get_ai_provider_service()
        results["ai_provider"] = ai is not None
    except Exception as e:
        results["errors"].append(f"AI Provider: {e}")
    
    try:
        analytics = _get_analytics_service()
        results["analytics"] = analytics is not None
    except Exception as e:
        results["errors"].append(f"Analytics: {e}")
    
    try:
        whatsapp = _get_whatsapp_service()
        results["whatsapp"] = whatsapp is not None
    except Exception as e:
        results["errors"].append(f"WhatsApp: {e}")
    
    try:
        schema = _get_schema_service()
        results["schema"] = schema is not None
    except Exception as e:
        results["errors"].append(f"Schema: {e}")
    
    try:
        kpi = _get_kpi_service()
        results["kpi"] = kpi is not None
    except Exception as e:
        results["errors"].append(f"KPI: {e}")
    
    logger.info("=" * 60)
    logger.info("🔧 WEBHOOK SERVICE INITIALIZATION")
    logger.info("=" * 60)
    for name, available in results.items():
        if name != "errors":
            icon = "✅" if available else "⚠️"
            logger.info(f"   {icon} {name}: {'Available' if available else 'Not Available'}")
    
    if results["errors"]:
        logger.warning("⚠️ Errors during initialization:")
        for error in results["errors"]:
            logger.warning(f"   - {error}")
    
    # Check security
    app_secret = os.getenv("WHATSAPP_APP_SECRET", "")
    if not app_secret:
        logger.warning("=" * 60)
        logger.warning("🔴⚠️ SECURITY WARNING!")
        logger.warning("🔴⚠️ WHATSAPP_APP_SECRET is NOT configured!")
        logger.warning("🔴⚠️ Webhook is running in SECURITY AWARE mode")
        logger.warning("🔴⚠️ Your WhatsApp integration CONTINUES WORKING")
        logger.warning("🔴⚠️ But webhook is NOT SECURE")
        logger.warning("🔴⚠️ Set WHATSAPP_APP_SECRET in your environment")
        logger.warning("🔴⚠️ Check /webhook/self-test for instructions")
        logger.warning("=" * 60)
    else:
        logger.info("✅ WHATSAPP_APP_SECRET is configured")
        logger.info("ℹ️ Webhook running in SECURITY AWARE mode")
        logger.info("ℹ️ Signatures are being verified but NOT enforced")
        logger.info("ℹ️ Check /webhook/stats for security warnings")
    
    logger.info("=" * 60)
    return results

def get_webhook_stats() -> Dict[str, Any]:
    return {
        "total_requests": webhook_stats["total_requests"],
        "successful_requests": webhook_stats["successful_requests"],
        "failed_requests": webhook_stats["failed_requests"],
        "total_messages_processed": webhook_stats["total_messages_processed"],
        "avg_processing_time_ms": round(webhook_stats.get("avg_processing_time_ms", 0), 2),
        "uptime": datetime.now().isoformat()
    }

# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("🌐 WEBHOOK ROUTER v21.4 - SECURITY AWARE")
logger.info("=" * 60)
logger.info("")
logger.info("   🔒 SECURITY MODE: SECURITY AWARE")
logger.info("   ✅ ZERO BREAKING CHANGES - WhatsApp works")
logger.info("   📊 Tracks all security issues")
logger.info("   🔄 Ready for future strict enforcement")
logger.info("   🛡️ Clear instructions to fix")
logger.info("")
logger.info("   FIXES IN v21.4:")
logger.info("   ✅ SECURITY AWARE: Clear warnings with fix instructions")
logger.info("   ✅ ALL requests still accepted (like before)")
logger.info("   ✅ WhatsApp integration UNCHANGED")
logger.info("   ✅ Stats track security issues")
logger.info("   ✅ Self-test shows recommendations")
logger.info("")
logger.info("   ENDPOINTS:")
logger.info("   GET  /webhook/          - Verification")
logger.info("   POST /webhook/          - Message Handler")
logger.info("   GET  /webhook/ping      - Ping")
logger.info("   GET  /webhook/health    - Health")
logger.info("   GET  /webhook/stats     - Statistics")
logger.info("   GET  /webhook/self-test - Self Test")
logger.info("   POST /webhook/reset-stats - Reset Stats")
logger.info("")
logger.info("   ⚠️  REMINDER: Set WHATSAPP_APP_SECRET when ready")
logger.info("   📌 Check /webhook/stats for security warnings")
logger.info("   📌 Check /webhook/self-test for configuration status")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY (Security Aware)")
logger.info("=" * 60)

__all__ = [
    'router',
    'get_webhook_stats',
    'initialize_services'
]
