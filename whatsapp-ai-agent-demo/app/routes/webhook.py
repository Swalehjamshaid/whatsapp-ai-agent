# ==========================================================
# FILE: app/routes/webhook.py (v24.0 - COMPLETE PRODUCTION)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - 100% AI Driven
# VERSION: 24.0 - ALWAYS Calls AI, No Fallback
# ==========================================================

import json
import time
import uuid
import re
import os
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
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
_analytics_service = None
_whatsapp_service = None

# ==========================================================
# ✅ FIXED: AI PROVIDER SERVICE
# ==========================================================

def _get_ai_provider_service() -> Optional[Any]:
    global _ai_provider_service
    
    if _ai_provider_service is not None:
        return _ai_provider_service
    
    try:
        logger.info("🚀 Initializing AI Provider Service...")
        from app.services.ai_provider_service import get_orchestrator
        
        if not DATABASE_AVAILABLE:
            logger.error("❌ Database not available")
            return None
        
        def session_factory() -> Session:
            try:
                return SessionLocal()
            except Exception as e:
                logger.error(f"❌ Session creation failed: {e}")
                raise
        
        logger.info("🔧 Creating AI Orchestrator with session_factory...")
        _ai_provider_service = get_orchestrator(session_factory=session_factory)
        
        if _ai_provider_service:
            logger.info("✅ AI Orchestrator created successfully")
            try:
                test_session = session_factory()
                if MODELS_AVAILABLE:
                    count = test_session.query(DeliveryReport).count()
                    logger.info(f"✅ PostgreSQL connected! Found {count} records")
                test_session.close()
            except Exception as e:
                logger.error(f"❌ PostgreSQL connection test FAILED: {e}")
        else:
            logger.error("❌ Failed to create AI Orchestrator")
        
        return _ai_provider_service
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize AI Provider: {e}")
        import traceback
        traceback.print_exc()
        return None

def _get_analytics_service():
    global _analytics_service
    
    if _analytics_service is not None:
        return _analytics_service
    
    try:
        logger.info("🚀 Initializing Analytics Service...")
        from app.services.analytics_service import get_analytics_service
        
        if not DATABASE_AVAILABLE:
            return None
        
        db = SessionLocal()
        _analytics_service = get_analytics_service(db)
        logger.info("✅ Analytics Service loaded")
        return _analytics_service
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize Analytics: {e}")
        return None

def _get_whatsapp_service():
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
# ✅ FIXED: WEBHOOK MESSAGE HANDLER (POST)
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
                    
                    # ✅ PROCESS ALL MESSAGES WITH AI
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
# ✅ FIXED: PROCESS MESSAGE WITH AI (NO FALLBACK)
# ==========================================================

async def process_message_with_ai(
    message_text: str,
    phone_number: str,
    request_id: str
) -> None:
    """
    ✅ ALWAYS calls the AI Orchestrator.
    ✅ NO FALLBACK - AI handles everything.
    """
    start_time = time.time()
    
    try:
        logger.info(f"[{request_id}] 🧠 Processing with AI: '{message_text[:50]}'")
        
        # ✅ Get AI Provider
        ai_provider = _get_ai_provider_service()
        
        if not ai_provider:
            logger.error(f"[{request_id}] ❌ AI Provider is None")
            error_msg = "⚠️ AI service is currently unavailable. Please try again later."
            await send_whatsapp_response(phone_number, error_msg, request_id)
            return
        
        logger.info(f"[{request_id}] ✅ AI Provider available")
        
        # ✅ Create session factory for this request
        def session_factory() -> Session:
            try:
                return SessionLocal()
            except Exception as e:
                logger.error(f"[{request_id}] ❌ Session factory error: {e}")
                raise
        
        # ✅ CALL AI ORCHESTRATOR
        try:
            logger.info(f"[{request_id}] 📤 Calling AI Orchestrator...")
            
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    ai_provider.process_whatsapp_query,
                    message_text,
                    session_factory,
                    phone_number,
                    None,
                    request_id
                )
                
                try:
                    response = future.result(timeout=30)
                    logger.info(f"[{request_id}] ✅ AI response: {len(response)} chars")
                except concurrent.futures.TimeoutError:
                    logger.error(f"[{request_id}] ⏰ AI timeout after 30s")
                    response = "⏰ I'm still thinking. Please wait a moment and try again."
                except Exception as e:
                    logger.error(f"[{request_id}] ❌ AI error: {e}")
                    response = None
            
            # ✅ Send response
            if response:
                await send_whatsapp_response(phone_number, response, request_id)
            else:
                logger.warning(f"[{request_id}] ⚠️ No AI response")
                fallback = "⚠️ I'm having trouble processing your request. Please try again in a moment."
                await send_whatsapp_response(phone_number, fallback, request_id)
                
        except Exception as e:
            logger.error(f"[{request_id}] ❌ AI processing error: {e}")
            import traceback
            traceback.print_exc()
            fallback = "⚠️ I encountered an error. Please try again."
            await send_whatsapp_response(phone_number, fallback, request_id)
        
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
# SEND WHATSAPP RESPONSE
# ==========================================================

async def send_whatsapp_response(
    phone_number: str,
    response_text: str,
    request_id: str
) -> bool:
    try:
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
# ✅ FIXED: HELP MESSAGE - ONLY FALLBACK
# ==========================================================

def get_help_response() -> str:
    return """
🏠 *HAIER LOGISTICS AI*

*📋 20+ Dashboards Available:*

1️⃣ 🏪 Dealer Dashboard
2️⃣ 🏭 Warehouse Dashboard
3️⃣ 🏙️ City Dashboard
4️⃣ 📦 Product Dashboard
5️⃣ 📄 DN Dashboard
6️⃣ 📋 PGI Dashboard
7️⃣ ✅ POD Dashboard
8️⃣ 🚚 Delivery Dashboard
9️⃣ 📍 Distance Dashboard
🔟 👔 Executive Dashboard
1️⃣1️⃣ 🚨 Control Tower
1️⃣2️⃣ 🏆 Dealer Ranking
1️⃣3️⃣ 🏆 Warehouse Ranking
1️⃣4️⃣ 🏆 City Ranking
1️⃣5️⃣ 🏆 Product Ranking
1️⃣6️⃣ 💰 Revenue Dashboard
1️⃣7️⃣ 📊 Division Dashboard
1️⃣8️⃣ 👤 Sales Manager Dashboard
1️⃣9️⃣ ⏳ Aging Dashboard
2️⃣0️⃣ 🔄 Follow-up Support

*🔍 Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "ZQ Electronics")
• City name (e.g., "Haripur")
• Warehouse name (e.g., "Lahore")
• "Executive summary"
• "Control tower"
• "Top dealers"
• "Help" for menu

*💡 Follow-up Support:*
• "What is its POD?" → Uses last dealer
• "How many pending DN?" → Uses last dealer
• "Show me its revenue" → Uses last dealer
• "Show aging" → Uses last dealer

*Ask me anything about logistics!* 🤖"""

# ==========================================================
# STATUS ENDPOINTS
# ==========================================================

@router.get("/ping")
async def webhook_ping() -> JSONResponse:
    ai = _get_ai_provider_service()
    return JSONResponse(content={
        "ping": "pong",
        "webhook_version": "24.0",
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
        "webhook_version": "24.0",
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
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("🌐 WEBHOOK ROUTER v24.0 - COMPLETE PRODUCTION")
logger.info("=" * 70)

# ✅ Force initialize AI on startup
logger.info("🚀 Pre-initializing AI Provider Service...")
ai = _get_ai_provider_service()
if ai:
    logger.info("✅ AI Provider Service initialized successfully")
    webhook_stats["ai_enabled"] = True
else:
    logger.error("❌ AI Provider Service initialization FAILED")
    webhook_stats["ai_enabled"] = False

# ✅ Test database connection
try:
    if DATABASE_AVAILABLE:
        db = SessionLocal()
        from sqlalchemy import text
        result = db.execute(text("SELECT 1")).scalar()
        logger.info(f"✅ Database connection test: {result}")
        webhook_stats["db_connected"] = True
        
        # ✅ Check if table has data
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

logger.info("=" * 70)

__all__ = ['router']
