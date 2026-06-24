# ==========================================================
# FILE: app/routes/webhook.py (v26.0 - COMPLETE PRODUCTION)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - ALWAYS Calls AI
# VERSION: 26.0 - FIXED RESPONSE FORMATTING
# ==========================================================

import json
import time
import uuid
import re
import os
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable, Union
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
    from app.database import SessionLocal, check_database_connection
    DATABASE_AVAILABLE = True
    logger.info("✅ Database module loaded successfully")
except ImportError as e:
    DATABASE_AVAILABLE = False
    logger.error(f"❌ Database module NOT available: {e}")
    raise

# ==========================================================
# MODELS
# ==========================================================

try:
    from app.models import DeliveryReport
    MODELS_AVAILABLE = True
    logger.info("✅ Models loaded successfully")
except ImportError as e:
    MODELS_AVAILABLE = False
    logger.error(f"❌ Models NOT available: {e}")

# ==========================================================
# SERVICES
# ==========================================================

_ai_provider_service = None
_whatsapp_service = None

# ==========================================================
# ✅ FIXED: AI PROVIDER SERVICE
# ==========================================================

def _get_ai_provider_service() -> Optional[Any]:
    """
    Get the AI Provider Service.
    
    Uses get_whatsapp_provider_service() from ai_provider_service.py
    """
    global _ai_provider_service
    
    if _ai_provider_service is not None:
        return _ai_provider_service
    
    try:
        logger.info("🚀 Initializing AI Provider Service v5.0...")
        
        from app.services.ai_provider_service import get_whatsapp_provider_service
        
        if not DATABASE_AVAILABLE:
            logger.error("❌ Database not available")
            return None
        
        _ai_provider_service = get_whatsapp_provider_service()
        
        if _ai_provider_service:
            logger.info("✅ AI Provider Service v5.0 initialized successfully")
            
            try:
                health = _ai_provider_service.get_service_registry_status()
                logger.info(f"   ├── Services Ready: {health.get('ready', 0)}")
                logger.info(f"   ├── In Development: {health.get('in_development', 0)}")
                logger.info(f"   ├── Readiness Score: {health.get('readiness_score', 0):.1f}%")
                
                dn_status = _ai_provider_service.registry.get_service_status("dn")
                if dn_status.get("ready", False):
                    logger.info(f"   ├── DN Service: ✅ READY")
                else:
                    logger.warning(f"   ├── DN Service: 🔧 {dn_status.get('status', 'UNKNOWN')}")
                    
            except Exception as e:
                logger.warning(f"⚠️ Could not get service registry status: {e}")
        else:
            logger.error("❌ Failed to create AI Provider Service")
        
        return _ai_provider_service
        
    except ImportError as e:
        logger.error(f"❌ Failed to import ai_provider_service: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Failed to initialize AI Provider: {e}")
        import traceback
        traceback.print_exc()
        return None

def _get_whatsapp_service():
    """Get WhatsApp service for sending messages."""
    global _whatsapp_service
    
    if _whatsapp_service is not None:
        return _whatsapp_service
    
    try:
        from app.services.whatsapp_service import get_whatsapp_service
        _whatsapp_service = get_whatsapp_service()
        logger.info("✅ WhatsApp Service loaded")
        return _whatsapp_service
    except Exception as e:
        logger.error(f"❌ Failed to load WhatsApp Service: {e}")
        return None

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
    "ai_enabled": False,
    "db_connected": False,
    "architecture": "v16.0 (Built-in Intent Detection)"
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
# ✅ FIXED: RESPONSE FORMATTING HELPER
# ==========================================================

def _ensure_string_response(response_data: Any) -> str:
    """
    Ensure response is always a string for WhatsApp.
    
    ✅ FIXES: "text.body" must be a string error
    """
    if response_data is None:
        return "No data available"
    
    if isinstance(response_data, str):
        return response_data
    
    if isinstance(response_data, dict):
        # Check if it's a dashboard response with 'data' field
        if "data" in response_data:
            data = response_data["data"]
            if isinstance(data, dict):
                # Try to format as dashboard
                return _format_dashboard_response(data)
        # Check if it has 'response' field
        if "response" in response_data:
            return _ensure_string_response(response_data["response"])
        # Check if it has 'error' field
        if "error" in response_data:
            return f"⚠️ {response_data['error']}"
        # Convert dict to string
        return str(response_data)
    
    if isinstance(response_data, list):
        return "\n".join([str(item) for item in response_data])
    
    return str(response_data)

def _format_dashboard_response(data: Dict[str, Any]) -> str:
    """Format DN dashboard data into WhatsApp message."""
    if not data:
        return "No data available"
    
    lines = []
    
    # DN Number
    dn_no = data.get('dn_no', 'N/A')
    lines.append(f"📦 *DN: {dn_no}*")
    lines.append("")
    
    # Dealer
    dealer = data.get('dealer_name') or data.get('customer_name') or 'Unknown'
    lines.append("*Dealer:*")
    lines.append(f"{dealer}")
    lines.append("")
    
    # Warehouse
    warehouse = data.get('warehouse', 'Unknown')
    lines.append("*Warehouse:*")
    lines.append(f"{warehouse}")
    lines.append("")
    
    # City
    city = data.get('city', 'Unknown')
    lines.append("*City:*")
    lines.append(f"{city}")
    lines.append("")
    
    # Metrics
    lines.append("*📊 Metrics:*")
    lines.append(f"Units: {data.get('total_units', 0)}")
    revenue = data.get('total_revenue', 0)
    lines.append(f"Revenue: PKR {revenue:,.2f}" if revenue else "Revenue: PKR 0")
    lines.append("")
    
    # Dates
    lines.append("*📅 Dates:*")
    lines.append(f"DN Create: {data.get('dn_create_date', 'N/A')}")
    lines.append(f"PGI: {data.get('good_issue_date', 'N/A')}")
    lines.append(f"POD: {data.get('pod_date', 'N/A')}")
    lines.append("")
    
    # Aging
    lines.append("*⏳ Aging:*")
    lines.append(f"Delivery: {data.get('delivery_aging_text', 'N/A')}")
    lines.append(f"POD: {data.get('pod_aging_text', 'N/A')}")
    lines.append(f"Total Cycle: {data.get('total_cycle_text', 'N/A')}")
    lines.append("")
    
    # Status
    lines.append("*📋 Status:*")
    lines.append(f"Delivery: {data.get('status_emoji', '❓')} {data.get('status_text', 'Unknown')}")
    lines.append(f"PGI: {data.get('pgi_status_text', 'Unknown')}")
    lines.append(f"POD: {data.get('pod_status_text', 'Unknown')}")
    lines.append(f"Pending: {data.get('pending_flag_text', 'Unknown')}")
    
    return "\n".join(lines)

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
            logger.error("❌ WHATSAPP_VERIFY_TOKEN not configured")
            update_stats(False, "verification", (time.time() - start_time) * 1000)
            return JSONResponse(
                status_code=500,
                content={"error": "Verification token not configured"}
            )
        
        if hub_mode == 'subscribe' and hub_verify_token == expected_token:
            logger.success(f"✅ Webhook verification successful!")
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
        try:
            data = json.loads(raw_body.decode('utf-8'))
        except json.JSONDecodeError as e:
            logger.error(f"[{request_id}] ❌ Invalid JSON: {e}")
            return JSONResponse(
                status_code=200,
                content={"status": "ok", "message": "Webhook received"}
            )
        
        if data.get('object') != 'whatsapp_business_account':
            return JSONResponse(
                status_code=200,
                content={"status": "ok"}
            )
        
        entries = data.get('entry', [])
        
        for entry in entries:
            changes = entry.get('changes', [])
            for change in changes:
                value = change.get('value', {})
                
                if 'statuses' in value:
                    logger.debug(f"[{request_id}] Status update - ignoring")
                    continue
                
                messages = value.get('messages', [])
                if not messages:
                    continue
                
                for message in messages:
                    phone_number = message.get('from')
                    if not phone_number:
                        continue
                    
                    message_id = message.get('id')
                    if message_id and is_duplicate_message(message_id, phone_number):
                        logger.debug(f"[{request_id}] Duplicate message: {message_id}")
                        continue
                    
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
                    
                    if message_text and message_text.strip():
                        background_tasks.add_task(
                            process_message_with_ai,
                            message_text.strip(),
                            phone_number,
                            request_id
                        )
                    elif msg_type == 'audio':
                        background_tasks.add_task(
                            process_audio_message,
                            message,
                            phone_number,
                            request_id
                        )
                    elif msg_type == 'location':
                        background_tasks.add_task(
                            process_location_message,
                            message,
                            phone_number,
                            request_id
                        )
        
        update_stats(True, "message", (time.time() - start_time) * 1000)
        logger.info(f"[{request_id}] ✅ Webhook processed - 200 OK")
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "message": "Webhook received"}
        )
        
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Webhook error: {e}")
        logger.exception(e)
        update_stats(False, "message", (time.time() - start_time) * 1000)
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "message": "Webhook received"}
        )

# ==========================================================
# ✅ FIXED: PROCESS MESSAGE WITH AI
# ==========================================================

async def process_message_with_ai(
    message_text: str,
    phone_number: str,
    request_id: str
) -> None:
    """
    ✅ ALWAYS calls the AI Orchestrator.
    ✅ Uses the CORRECT API: process_whatsapp_query(message, sender_id)
    ✅ FIXED: Ensures response is always a string
    """
    start_time = time.time()
    
    try:
        logger.info(f"[{request_id}] 🧠 Processing with AI: '{message_text[:50]}'")
        
        ai_provider = _get_ai_provider_service()
        
        if not ai_provider:
            logger.error(f"[{request_id}] ❌ AI Provider is None")
            error_msg = "⚠️ AI service is currently unavailable. Please try again later."
            await send_whatsapp_response(phone_number, error_msg, request_id)
            return
        
        logger.info(f"[{request_id}] ✅ AI Provider available")
        
        try:
            logger.info(f"[{request_id}] 📤 Calling AI Orchestrator...")
            
            response = await ai_provider.process_whatsapp_query(
                message=message_text,
                sender_id=phone_number
            )
            
            logger.info(f"[{request_id}] ✅ AI response received")
            
            # ✅ FIXED: Always convert to string
            response_text = _ensure_string_response(response)
            
            # ✅ Ensure it's not empty
            if not response_text or response_text == "None":
                response_text = "⚠️ I couldn't process your request. Please try again."
            
            logger.info(f"[{request_id}] 📤 Response: {response_text[:100]}...")
            
            await send_whatsapp_response(phone_number, response_text, request_id)
            
        except Exception as e:
            logger.error(f"[{request_id}] ❌ AI processing error: {e}")
            import traceback
            traceback.print_exc()
            
            error_msg = "⚠️ I encountered an error processing your request. Please try again."
            await send_whatsapp_response(phone_number, error_msg, request_id)
        
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"[{request_id}] 📊 Message processed in {processing_time:.0f}ms")
        
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Critical error: {e}")
        import traceback
        traceback.print_exc()
        try:
            error_response = "⚠️ I encountered a critical error. Please try again later."
            await send_whatsapp_response(phone_number, error_response, request_id)
        except:
            logger.error(f"[{request_id}] ❌ Failed to send error response")

# ==========================================================
# MEDIA MESSAGE HANDLERS
# ==========================================================

async def process_audio_message(
    message: Dict[str, Any],
    phone_number: str,
    request_id: str
) -> None:
    try:
        audio = message.get('audio', {})
        audio_id = audio.get('id')
        logger.info(f"[{request_id}] 🎵 Audio message from {phone_number} - ID: {audio_id}")
        response = "🎵 I received your audio. Please send text for better assistance."
        await send_whatsapp_response(phone_number, response, request_id)
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Audio processing error: {e}")

async def process_location_message(
    message: Dict[str, Any],
    phone_number: str,
    request_id: str
) -> None:
    try:
        location = message.get('location', {})
        lat = location.get('latitude')
        lon = location.get('longitude')
        name = location.get('name', '')
        logger.info(f"[{request_id}] 📍 Location from {phone_number}: {lat}, {lon}")
        response = f"📍 Received location: {name}\nCoordinates: {lat}, {lon}"
        await send_whatsapp_response(phone_number, response, request_id)
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Location processing error: {e}")

# ==========================================================
# ✅ FIXED: SEND WHATSAPP RESPONSE
# ==========================================================

async def send_whatsapp_response(
    phone_number: str,
    response_text: str,
    request_id: str
) -> bool:
    """
    Send WhatsApp response.
    
    ✅ FIXED: Ensures response_text is always a string
    """
    try:
        # ✅ Ensure response_text is a string
        if not isinstance(response_text, str):
            response_text = _ensure_string_response(response_text)
        
        # ✅ Ensure it's not empty
        if not response_text or response_text.strip() == "":
            response_text = "No data available"
        
        whatsapp = _get_whatsapp_service()
        if whatsapp:
            try:
                await asyncio.to_thread(
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
            print(f"[{request_id}] RESPONSE TO {phone_number}: {response_text[:200]}")
            return False
    except Exception as e:
        logger.error(f"[{request_id}] ❌ Send response error: {e}")
        return False

# ==========================================================
# STATUS ENDPOINTS
# ==========================================================

@router.get("/ping")
async def webhook_ping() -> JSONResponse:
    ai = _get_ai_provider_service()
    return JSONResponse(content={
        "ping": "pong",
        "webhook_version": "26.0",
        "architecture": "v16.0 (Built-in Intent Detection)",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "ai_provider": "healthy" if ai else "unhealthy",
            "database": "connected" if webhook_stats.get("db_connected", False) else "disconnected"
        },
        "stats": {
            "total_messages": webhook_stats["total_messages_processed"],
            "total_requests": webhook_stats["total_requests"]
        }
    })

@router.get("/health")
async def webhook_health() -> JSONResponse:
    ai = _get_ai_provider_service()
    return JSONResponse(content={
        "status": "healthy" if ai else "degraded",
        "webhook_version": "26.0",
        "architecture": "v16.0 (Built-in Intent Detection)",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "ai_provider": "healthy" if ai else "unhealthy",
            "database": "connected" if webhook_stats.get("db_connected", False) else "disconnected"
        },
        "stats": {
            "total_requests": webhook_stats["total_requests"],
            "messages_processed": webhook_stats["total_messages_processed"]
        }
    })

# ==========================================================
# DIAGNOSTIC ENDPOINT
# ==========================================================

@router.get("/test-dn")
async def test_dn_lookup(dn: str = Query(..., description="DN number to test")):
    """Test DN lookup directly."""
    try:
        ai = _get_ai_provider_service()
        if not ai:
            return JSONResponse(
                status_code=503,
                content={"error": "AI Provider not available"}
            )
        
        # Get dn_analysis service
        dn_service = ai.registry.get_service_instance("dn")
        if not dn_service:
            return JSONResponse(
                status_code=503,
                content={"error": "DN Service not available"}
            )
        
        # Test the DN
        result = dn_service.test_dn_lookup(dn)
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"❌ Test DN error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("🌐 WEBHOOK ROUTER v26.0 - FIXED RESPONSE FORMATTING")
logger.info("=" * 70)

logger.info("🚀 Pre-initializing AI Provider Service...")
ai = _get_ai_provider_service()
if ai:
    logger.info("✅ AI Provider Service v5.0 initialized successfully")
    webhook_stats["ai_enabled"] = True
else:
    logger.error("❌ AI Provider Service initialization FAILED")
    webhook_stats["ai_enabled"] = False

try:
    if DATABASE_AVAILABLE:
        db = SessionLocal()
        from sqlalchemy import text
        result = db.execute(text("SELECT 1")).scalar()
        logger.info(f"✅ Database connection test: {result}")
        webhook_stats["db_connected"] = True
        
        if MODELS_AVAILABLE:
            count = db.query(DeliveryReport).count()
            logger.info(f"✅ DeliveryReport records: {count}")
            if count == 0:
                logger.warning("⚠️ WARNING: delivery_reports table is EMPTY!")
                logger.warning("⚠️ You need to import data to answer questions.")
        db.close()
except Exception as e:
    logger.error(f"❌ Database connection test FAILED: {e}")
    webhook_stats["db_connected"] = False

logger.info("")
logger.info("   📌 ARCHITECTURE: v16.0 (Built-in Intent Detection)")
logger.info("   📌 AI Provider: v5.0 (NO ai_query_service.py)")
logger.info("   📌 Routing: IntentDetectionEngine (built-in)")
logger.info("   📌 Response Formatting: ✅ FIXED (always string)")
logger.info("")
logger.info("=" * 70)

__all__ = ['router']
