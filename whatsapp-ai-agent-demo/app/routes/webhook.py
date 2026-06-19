# ==========================================================
# FILE: app/routes/webhook.py (v21.0 - COMPLETE INTEGRATION)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - Meta WhatsApp Cloud API
# VERSION: 21.0 - Fully Integrated with AI Provider & Analytics
#
# INTEGRATION:
# - ✅ AI Provider Service (ai_provider_service.py v21.0)
# - ✅ Analytics Service (analytics_service.py v14.0)
# - ✅ Config (config.py with GROQ support)
# - ✅ Database (database.py v3.1)
# - ✅ Main App (main.py v15.2)
#
# CRITICAL RULES:
# 1. MUST return 200 OK for ALL POST requests (Meta requirement)
# 2. NO Pydantic models in this file (causes 422 errors)
# 3. Use Request object + BackgroundTasks for async processing
# 4. Parse JSON manually with json.loads()
# 5. Always process messages asynchronously
# 6. Never crash - always return 200 OK
# ==========================================================

import json
import hmac
import hashlib
import time
import uuid
import re
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from fastapi import APIRouter, Request, BackgroundTasks, Query, Response, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger
from sqlalchemy.orm import Session

# ==========================================================
# CONFIGURATION
# ==========================================================

from app.config import config

# ==========================================================
# DATABASE (for session management)
# ==========================================================

try:
    from app.database import get_db, SessionLocal
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    logger.warning("⚠️ Database module not available - webhook will run without DB")

# ==========================================================
# SERVICES - Lazy loaded to avoid circular imports
# ==========================================================

# Service references
_ai_provider_service = None
_analytics_service = None
_whatsapp_service = None
_schema_service = None
_kpi_service = None

def _get_ai_provider_service():
    """Lazy load AI Provider Service."""
    global _ai_provider_service
    if _ai_provider_service is None:
        try:
            from app.services.ai_provider_service import get_orchestrator
            _ai_provider_service = get_orchestrator()
            logger.info("✅ AI Provider Service loaded")
        except ImportError as e:
            logger.error(f"❌ Failed to load AI Provider Service: {e}")
            _ai_provider_service = None
    return _ai_provider_service

def _get_analytics_service():
    """Lazy load Analytics Service."""
    global _analytics_service
    if _analytics_service is None:
        try:
            from app.services.analytics_service import get_analytics_service
            _analytics_service = get_analytics_service()
            logger.info("✅ Analytics Service loaded")
        except ImportError as e:
            logger.error(f"❌ Failed to load Analytics Service: {e}")
            _analytics_service = None
    return _analytics_service

def _get_whatsapp_service():
    """Lazy load WhatsApp Service."""
    global _whatsapp_service
    if _whatsapp_service is None:
        try:
            from app.services.whatsapp_service import get_whatsapp_service
            _whatsapp_service = get_whatsapp_service()
            logger.info("✅ WhatsApp Service loaded")
        except ImportError as e:
            logger.error(f"❌ Failed to load WhatsApp Service: {e}")
            _whatsapp_service = None
    return _whatsapp_service

def _get_schema_service():
    """Lazy load Schema Service."""
    global _schema_service
    if _schema_service is None:
        try:
            from app.schemas.schema_service import get_schema_service
            _schema_service = get_schema_service()
            logger.info("✅ Schema Service loaded")
        except ImportError as e:
            logger.error(f"❌ Failed to load Schema Service: {e}")
            _schema_service = None
    return _schema_service

def _get_kpi_service():
    """Lazy load KPI Service."""
    global _kpi_service
    if _kpi_service is None:
        try:
            from app.services.kpi_service import get_kpi_service
            _kpi_service = get_kpi_service()
            logger.info("✅ KPI Service loaded")
        except ImportError as e:
            logger.error(f"❌ Failed to load KPI Service: {e}")
            _kpi_service = None
    return _kpi_service

# ==========================================================
# ROUTER DEFINITION
# ==========================================================

router = APIRouter(
    prefix="/webhook",
    tags=["webhook"],
    include_in_schema=True
)

# ==========================================================
# WEBHOOK STATS (In-memory for monitoring)
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
    "last_100_errors": []
}

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def update_stats(success: bool, endpoint: str = "unknown", processing_time_ms: float = 0):
    """Update webhook statistics."""
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
    
    # Update average processing time
    if processing_time_ms > 0:
        old_avg = webhook_stats.get("avg_processing_time_ms", 0)
        total = webhook_stats["total_requests"]
        webhook_stats["avg_processing_time_ms"] = ((old_avg * (total - 1)) + processing_time_ms) / total

def update_phone_number_stats(phone_number: str):
    """Update phone number statistics."""
    if phone_number:
        webhook_stats["phone_numbers"][phone_number] = webhook_stats["phone_numbers"].get(phone_number, 0) + 1

def is_duplicate_message(message_id: str, phone_number: str) -> bool:
    """Check if message is duplicate (simple in-memory deduplication)."""
    if not hasattr(webhook_stats, "_processed_messages"):
        webhook_stats["_processed_messages"] = {}
    
    key = f"{phone_number}:{message_id}"
    if key in webhook_stats["_processed_messages"]:
        return True
    
    # Clean up old entries (keep last 10000)
    if len(webhook_stats["_processed_messages"]) > 10000:
        # Remove oldest 1000
        keys = list(webhook_stats["_processed_messages"].keys())[:1000]
        for k in keys:
            del webhook_stats["_processed_messages"][k]
    
    webhook_stats["_processed_messages"][key] = time.time()
    return False

def extract_message_details(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract message details from webhook payload."""
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
        # Extract entry and changes
        entries = data.get('entry', [])
        if not entries:
            return result
        
        changes = entries[0].get('changes', [])
        if not changes:
            return result
        
        value = changes[0].get('value', {})
        
        # Check for statuses (ignore)
        if 'statuses' in value:
            return result
        
        # Get messages
        messages = value.get('messages', [])
        if not messages:
            return result
        
        message = messages[0]
        
        # Extract phone number
        result["phone_number"] = message.get('from')
        result["message_id"] = message.get('id')
        result["timestamp"] = message.get('timestamp')
        result["message_type"] = message.get('type')
        
        # Extract context (for replies)
        context = message.get('context', {})
        if context:
            result["context"] = context
        
        # Extract message based on type
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
        
        # Try to extract dealer info from message
        if result["message_text"]:
            # Check for dealer name patterns
            dealer_match = re.search(r'(?:dealer|customer|party)\s+([A-Za-z0-9\s&]+)', result["message_text"], re.IGNORECASE)
            if dealer_match:
                result["dealer_name"] = dealer_match.group(1).strip()
            
            # Check for dealer code patterns
            code_match = re.search(r'(?:code|dealer code)\s*[:#]?\s*([A-Za-z0-9\-]+)', result["message_text"], re.IGNORECASE)
            if code_match:
                result["dealer_code"] = code_match.group(1).strip()
        
    except Exception as e:
        logger.error(f"Error extracting message details: {e}")
    
    return result

def verify_signature(payload: bytes, signature_header: Optional[str]) -> bool:
    """Verify webhook signature using HMAC-SHA256."""
    if not signature_header or not signature_header.startswith('sha256='):
        logger.warning("No signature header or invalid format - skipping verification")
        return True  # Skip verification if header missing
    
    # Get the secret
    app_secret = config.WHATSAPP_ACCESS_TOKEN or os.getenv("WHATSAPP_APP_SECRET", "")
    if not app_secret:
        logger.warning("No app secret configured - skipping verification")
        return True
    
    try:
        # Extract the signature
        expected_signature = signature_header.replace('sha256=', '')
        
        # Compute actual signature
        actual_signature = hmac.new(
            app_secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        # Compare using constant time comparison
        return hmac.compare_digest(expected_signature, actual_signature)
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False

# ==========================================================
# WEBHOOK VERIFICATION (GET)
# ==========================================================

@router.get("/")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
) -> Response:
    """
    WhatsApp Webhook Verification Endpoint.
    
    Meta sends a GET request with hub.mode, hub.verify_token, and hub.challenge.
    Must respond with the challenge if the token matches.
    """
    start_time = time.time()
    
    logger.info(f"📥 Webhook verification request received")
    logger.info(f"   hub.mode: {hub_mode}")
    logger.info(f"   hub.verify_token: {hub_verify_token[:10] if hub_verify_token else 'None'}...")
    logger.info(f"   hub.challenge: {hub_challenge}")
    
    try:
        # Get verification token from config
        expected_token = config.WHATSAPP_VERIFY_TOKEN
        
        if not expected_token:
            logger.error("WHATSAPP_VERIFY_TOKEN not configured")
            update_stats(False, "verification", (time.time() - start_time) * 1000)
            return JSONResponse(
                status_code=500,
                content={"error": "Verification token not configured"}
            )
        
        # Check verification
        if hub_mode == 'subscribe' and hub_verify_token == expected_token:
            logger.success(f"✅ Verification successful - returning challenge: {hub_challenge}")
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
# WEBHOOK MESSAGE HANDLER (POST) - CRITICAL: MUST RETURN 200 OK
# ==========================================================

@router.post("/")
async def handle_webhook(
    request: Request,
    background_tasks: BackgroundTasks
) -> JSONResponse:
    """
    WhatsApp Webhook Handler.
    
    CRITICAL RULES:
    - MUST return 200 OK for ALL requests (Meta requirement)
    - NO Pydantic validation (causes 422 errors)
    - Process messages asynchronously with BackgroundTasks
    - Always handle errors gracefully
    
    FLOW:
    1. Receive webhook from Meta
    2. Parse JSON manually
    3. Verify signature (optional)
    4. Extract message details
    5. Process message asynchronously
    6. Return 200 OK immediately
    """
    start_time = time.time()
    request_id = str(uuid.uuid4())[:8]
    
    # Get raw body for signature verification
    raw_body = await request.body()
    
    logger.info(f"[{request_id}] 📥 Webhook request received - {len(raw_body)} bytes")
    
    try:
        # ==========================================================
        # STEP 1: Verify Signature (Optional but recommended)
        # ==========================================================
        
        signature_header = request.headers.get('X-Hub-Signature-256')
        if signature_header:
            if not verify_signature(raw_body, signature_header):
                logger.warning(f"[{request_id}] ⚠️ Invalid signature")
                # Still return 200 to avoid Meta retries
                update_stats(False, "message", (time.time() - start_time) * 1000)
                return JSONResponse(
                    status_code=200,
                    content={"status": "ok", "message": "Webhook received"}
                )
        else:
            logger.debug(f"[{request_id}] No signature header - skipping verification")
        
        # ==========================================================
        # STEP 2: Parse JSON (Manual - NO Pydantic)
        # ==========================================================
        
        try:
            data = json.loads(raw_body.decode('utf-8'))
        except json.JSONDecodeError as e:
            logger.error(f"[{request_id}] ❌ Invalid JSON: {e}")
            # Still return 200 to Meta
            update_stats(False, "message", (time.time() - start_time) * 1000)
            return JSONResponse(
                status_code=200,
                content={"status": "ok", "message": "Webhook received"}
            )
        
        # ==========================================================
        # STEP 3: Check Object Type
        # ==========================================================
        
        if data.get('object') != 'whatsapp_business_account':
            logger.debug(f"[{request_id}] Not a WhatsApp business account message")
            update_stats(True, "message", (time.time() - start_time) * 1000)
            return JSONResponse(
                status_code=200,
                content={"status": "ok"}
            )
        
        # ==========================================================
        # STEP 4: Process the Webhook
        # ==========================================================
        
        # Extract entry and changes
        entries = data.get('entry', [])
        for entry in entries:
            changes = entry.get('changes', [])
            for change in changes:
                value = change.get('value', {})
                
                # Check for status updates (ignore)
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
                    # Extract phone number
                    phone_number = message.get('from')
                    if not phone_number:
                        continue
                    
                    # Check for duplicate
                    message_id = message.get('id')
                    if message_id and is_duplicate_message(message_id, phone_number):
                        logger.debug(f"[{request_id}] Duplicate message: {message_id}")
                        continue
                    
                    # Update phone number stats
                    update_phone_number_stats(phone_number)
                    
                    # Extract message details
                    msg_type = message.get('type')
                    
                    # Skip if no message type
                    if not msg_type:
                        continue
                    
                    # Extract message text based on type
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
                    elif msg_type == 'audio' or msg_type == 'location':
                        # Handle as special types
                        pass
                    
                    # ==========================================================
                    # STEP 5: Process Message (Asynchronously)
                    # ==========================================================
                    
                    # Skip empty text messages
                    if not message_text and msg_type != 'audio' and msg_type != 'location':
                        continue
                    
                    # Increment message counter
                    webhook_stats["total_messages_processed"] += 1
                    
                    # Log message
                    logger.info(f"[{request_id}] 📨 Message from {phone_number}: '{message_text[:50] if message_text else '[Media]'}'")
                    
                    # ==========================================================
                    # STEP 6: Process using AI Provider Service (Async)
                    # ==========================================================
                    
                    # Use BackgroundTasks for async processing
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
        
        # ==========================================================
        # STEP 7: Return 200 OK (Always)
        # ==========================================================
        
        update_stats(True, "message", (time.time() - start_time) * 1000)
        logger.info(f"[{request_id}] ✅ Webhook processed - 200 OK ({int((time.time() - start_time) * 1000)}ms)")
        
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "message": "Webhook received"}
        )
        
    except Exception as e:
        # ==========================================================
        # STEP 8: Error Handling - ALWAYS return 200 OK
        # ==========================================================
        
        logger.error(f"[{request_id}] ❌ Webhook error: {e}")
        logger.exception(e)
        
        webhook_stats["errors"] += 1
        webhook_stats["last_error"] = str(e)
        webhook_stats["last_error_time"] = datetime.now().isoformat()
        
        # Store last 100 errors
        error_entry = {
            "time": datetime.now().isoformat(),
            "error": str(e),
            "request_id": request_id
        }
        webhook_stats["last_100_errors"].append(error_entry)
        if len(webhook_stats["last_100_errors"]) > 100:
            webhook_stats["last_100_errors"] = webhook_stats["last_100_errors"][-100:]
        
        update_stats(False, "message", (time.time() - start_time) * 1000)
        
        # ALWAYS return 200 OK to Meta
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "message": "Webhook received"}
        )

# ==========================================================
# ASYNC MESSAGE PROCESSING FUNCTIONS
# ==========================================================

async def process_whatsapp_message_async(
    message_text: str,
    phone_number: str,
    request_id: str,
    value: Dict[str, Any]
) -> None:
    """
    Process WhatsApp message asynchronously using AI Provider Service.
    
    This function runs in a background task and handles:
    1. AI Processing via AI Provider Service
    2. Response sending via WhatsApp Service
    3. Error handling and logging
    """
    start_time = time.time()
    
    try:
        logger.info(f"[{request_id}] 🧠 Processing message from {phone_number}: '{message_text[:50]}...'")
        
        # ==========================================================
        # STEP 1: Process with AI Provider Service
        # ==========================================================
        
        ai_provider = _get_ai_provider_service()
        
        if ai_provider:
            try:
                # Use a thread pool for AI processing (blocks)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        ai_provider.process_whatsapp_query,
                        message_text,
                        None,  # session_factory
                        phone_number,
                        None,  # user_id
                        request_id
                    )
                    
                    try:
                        response = future.result(timeout=25)  # 25 second timeout
                    except concurrent.futures.TimeoutError:
                        logger.error(f"[{request_id}] ⏰ AI processing timeout")
                        response = "⏰ I'm still thinking about your request. Please wait a moment and try again."
                
                if response:
                    logger.info(f"[{request_id}] ✅ AI response generated ({len(response)} chars)")
                    # Send response via WhatsApp
                    await send_whatsapp_response(phone_number, response, request_id)
                else:
                    logger.warning(f"[{request_id}] ⚠️ No AI response generated")
                    
            except Exception as e:
                logger.error(f"[{request_id}] ❌ AI processing error: {e}")
                # Send fallback response
                fallback_response = "⚠️ I'm having trouble processing your request. Please try again in a moment."
                await send_whatsapp_response(phone_number, fallback_response, request_id)
        else:
            # AI Provider not available - use rule-based fallback
            logger.warning(f"[{request_id}] ⚠️ AI Provider not available - using rule-based fallback")
            fallback_response = get_rule_based_response(message_text, phone_number, request_id)
            await send_whatsapp_response(phone_number, fallback_response, request_id)
        
        # ==========================================================
        # STEP 2: Log processing time
        # ==========================================================
        
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"[{request_id}] 📊 Message processed in {processing_time:.0f}ms")
        
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Async processing error: {e}")
        logger.exception(e)
        
        # Try to send error response
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
    """Process audio message asynchronously."""
    try:
        # Get audio details
        audio = message.get('audio', {})
        audio_id = audio.get('id')
        audio_url = audio.get('url')
        
        logger.info(f"[{request_id}] 🎵 Audio message from {phone_number} - ID: {audio_id}")
        
        # Response for audio
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
    """Process location message asynchronously."""
    try:
        # Get location details
        location = message.get('location', {})
        lat = location.get('latitude')
        lon = location.get('longitude')
        name = location.get('name', '')
        address = location.get('address', '')
        
        logger.info(f"[{request_id}] 📍 Location from {phone_number}: {lat}, {lon}")
        
        # Response for location
        response = f"📍 Received location: {name}\nAddress: {address}\nCoordinates: {lat}, {lon}\n\nI can use this to help with delivery tracking!"
        await send_whatsapp_response(phone_number, response, request_id)
        
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Location processing error: {e}")

async def send_whatsapp_response(
    phone_number: str,
    response_text: str,
    request_id: str
) -> bool:
    """
    Send response via WhatsApp Service.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Get WhatsApp service
        whatsapp = _get_whatsapp_service()
        
        if whatsapp:
            try:
                # Send message
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
# RULE-BASED FALLBACK RESPONSE
# ==========================================================

def get_rule_based_response(message: str, phone_number: str, request_id: str) -> str:
    """Generate rule-based fallback response when AI is not available."""
    message_lower = message.lower().strip()
    
    # Help commands
    if message_lower in ['help', 'hi', 'hello', 'menu', 'start']:
        return get_help_response()
    
    # DN tracking
    if re.search(r'\b(\d{8,12})\b', message):
        return get_dn_tracking_response(message)
    
    # Dealer queries
    if 'dealer' in message_lower or 'customer' in message_lower:
        return get_dealer_response(message)
    
    # Warehouse queries
    if 'warehouse' in message_lower or 'wh' in message_lower:
        return get_warehouse_response(message)
    
    # City queries
    if 'city' in message_lower:
        return get_city_response(message)
    
    # Delivery queries
    if 'delivery' in message_lower or 'pending' in message_lower:
        return get_delivery_response(message)
    
    # Product queries
    if 'product' in message_lower or 'model' in message_lower:
        return get_product_response(message)
    
    # Revenue queries
    if 'revenue' in message_lower or 'sales' in message_lower:
        return get_revenue_response(message)
    
    # Default response
    return get_default_response()

def get_help_response() -> str:
    """Get help menu response."""
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
    """Get DN tracking response."""
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
    """Get dealer response."""
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
    """Get warehouse response."""
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
    """Get city response."""
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
    """Get delivery response."""
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
    """Get product response."""
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
    """Get revenue response."""
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
    """Get default response."""
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
# WEBHOOK SELF-TEST ENDPOINT
# ==========================================================

@router.get("/self-test")
async def webhook_self_test() -> JSONResponse:
    """
    Self-test endpoint to verify webhook configuration.
    
    Tests:
    1. Verify token configuration
    2. Service availability
    3. Database connection (if available)
    """
    results = {
        "status": "ok",
        "webhook_version": "21.0",
        "timestamp": datetime.now().isoformat(),
        "checks": {}
    }
    
    # Check verify token
    verify_token = config.WHATSAPP_VERIFY_TOKEN
    results["checks"]["verify_token"] = {
        "configured": bool(verify_token),
        "status": "ok" if verify_token else "warning",
        "message": "Configured" if verify_token else "Not configured - verification will fail"
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
            "message": "Available" if service else "Not available - functionality limited"
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
    else:
        results["checks"]["database"] = {
            "available": False,
            "status": "warning",
            "message": "Database module not available"
        }
    
    # Check stats
    results["stats"] = {
        "total_requests": webhook_stats["total_requests"],
        "successful_requests": webhook_stats["successful_requests"],
        "failed_requests": webhook_stats["failed_requests"],
        "messages_processed": webhook_stats["total_messages_processed"],
        "avg_processing_time_ms": round(webhook_stats.get("avg_processing_time_ms", 0), 2),
        "start_time": webhook_stats["start_time"],
        "last_request_time": webhook_stats.get("last_request_time"),
        "unique_phone_numbers": len(webhook_stats.get("phone_numbers", {}))
    }
    
    # Overall status
    critical_failures = [
        check for check in results["checks"].values()
        if check.get("status") == "error"
    ]
    
    if critical_failures:
        results["overall_status"] = "degraded"
        results["warnings"] = [f"{k}: {v.get('message', 'Error')}" for k, v in results["checks"].items() if v.get("status") == "error"]
    else:
        results["overall_status"] = "healthy"
    
    return JSONResponse(content=results)

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
        "webhook_version": "21.0",
        "total_requests": webhook_stats["total_requests"],
        "messages_processed": webhook_stats["total_messages_processed"],
        "uptime": datetime.now().isoformat(),
        "timestamp": datetime.now().isoformat()
    })

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
        "webhook_version": "21.0",
        "timestamp": datetime.now().isoformat(),
        "services_available": {
            "ai_provider": _get_ai_provider_service() is not None,
            "analytics": _get_analytics_service() is not None,
            "whatsapp": _get_whatsapp_service() is not None
        }
    })

# ==========================================================
# WEBHOOK STATS ENDPOINT
# ==========================================================

@router.get("/stats")
async def webhook_stats_endpoint() -> JSONResponse:
    """
    Get webhook statistics.
    """
    # Clean up stats for display
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
        "recent_errors": webhook_stats.get("last_100_errors", [])[-5:]  # Last 5 errors
    }
    
    return JSONResponse(content=stats)

# ==========================================================
# WEBHOOK RESET STATS ENDPOINT
# ==========================================================

@router.post("/reset-stats")
async def webhook_reset_stats() -> JSONResponse:
    """
    Reset webhook statistics (for debugging).
    """
    global webhook_stats
    
    # Keep start time and uptime
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
        "last_100_errors": []
    }
    
    return JSONResponse(content={
        "status": "ok",
        "message": "Stats reset successfully",
        "timestamp": datetime.now().isoformat()
    })

# ==========================================================
# SERVICE INITIALIZATION FUNCTION
# ==========================================================

async def initialize_services() -> Dict[str, Any]:
    """
    Initialize all webhook services.
    Called from main.py during startup.
    """
    results = {
        "ai_provider": False,
        "analytics": False,
        "whatsapp": False,
        "schema": False,
        "kpi": False,
        "database": DATABASE_AVAILABLE,
        "errors": []
    }
    
    # Initialize AI Provider Service
    try:
        ai = _get_ai_provider_service()
        results["ai_provider"] = ai is not None
    except Exception as e:
        results["errors"].append(f"AI Provider: {e}")
    
    # Initialize Analytics Service
    try:
        analytics = _get_analytics_service()
        results["analytics"] = analytics is not None
    except Exception as e:
        results["errors"].append(f"Analytics: {e}")
    
    # Initialize WhatsApp Service
    try:
        whatsapp = _get_whatsapp_service()
        results["whatsapp"] = whatsapp is not None
    except Exception as e:
        results["errors"].append(f"WhatsApp: {e}")
    
    # Initialize Schema Service
    try:
        schema = _get_schema_service()
        results["schema"] = schema is not None
    except Exception as e:
        results["errors"].append(f"Schema: {e}")
    
    # Initialize KPI Service
    try:
        kpi = _get_kpi_service()
        results["kpi"] = kpi is not None
    except Exception as e:
        results["errors"].append(f"KPI: {e}")
    
    # Log results
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
    
    logger.info("=" * 60)
    
    return results

def get_webhook_stats() -> Dict[str, Any]:
    """Get webhook statistics (for main.py integration)."""
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
logger.info("🌐 WEBHOOK ROUTER v21.0 - COMPLETE INTEGRATION")
logger.info("=" * 60)
logger.info("")
logger.info("   INTEGRATION:")
logger.info("   ✅ AI Provider Service (v21.0)")
logger.info("   ✅ Analytics Service (v14.0)")
logger.info("   ✅ Config (GROQ Support)")
logger.info("   ✅ Database (v3.1)")
logger.info("   ✅ Main App (v15.2)")
logger.info("")
logger.info("   CRITICAL RULES:")
logger.info("   ✅ Always returns 200 OK")
logger.info("   ✅ NO Pydantic models")
logger.info("   ✅ Manual JSON parsing")
logger.info("   ✅ Async processing")
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
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 60)

# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'router',
    'get_webhook_stats',
    'initialize_services'
]
