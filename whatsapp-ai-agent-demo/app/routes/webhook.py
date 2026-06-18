# 1. SSH into server
ssh your-server

# 2. Navigate to app directory
cd /path/to/your/app

# 3. Backup old file
cp app/routes/webhook.py app/routes/webhook.py.backup.$(date +%Y%m%d)

# 4. Create new webhook.py with NO Pydantic
cat > app/routes/webhook.py << 'EOF'
# ==========================================================
# FILE: app/routes/webhook.py (v21.1 - 422 COMPLETELY FIXED)
# ==========================================================
# 🔥 NO Pydantic - Manual JSON parsing only
# 🔥 ALWAYS returns 200 OK to Meta
# 🔥 NEVER returns 422
# ==========================================================

import json
import uuid
import asyncio
import time
from datetime import datetime
from fastapi import APIRouter, Request, BackgroundTasks, Query
from fastapi.responses import JSONResponse, Response
from loguru import logger
from cachetools import TTLCache

from app.config import config
from app.services.ai_provider_service import process_whatsapp_query
from app.services.whatsapp_service import send_text_message

router = APIRouter(tags=["WhatsApp Webhook"])

PROCESSING_TIMEOUT_SECONDS = 25
_processed_messages = TTLCache(maxsize=50000, ttl=86400)
_phone_rate_limits = TTLCache(maxsize=50000, ttl=60)
RATE_LIMIT_REQUESTS = 100

def generate_request_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

def mask_sensitive_data(value: str) -> str:
    if not value or len(value) < 5:
        return "***"
    return f"{value[:3]}****{value[-2:]}"

def check_rate_limit(phone_number: str) -> bool:
    now = time.time()
    requests = _phone_rate_limits.get(phone_number, [])
    recent = [t for t in requests if now - t < 60]
    if len(recent) >= RATE_LIMIT_REQUESTS:
        return False
    recent.append(now)
    _phone_rate_limits[phone_number] = recent
    return True

@router.get("/webhook")
@router.get("/webhook/")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    logger.info(f"Webhook verification: mode={hub_mode}")
    verify_token = getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')
    if hub_mode == 'subscribe' and hub_verify_token == verify_token:
        logger.success("✅ Webhook verified successfully!")
        return Response(content=hub_challenge, status_code=200, media_type="text/plain")
    return JSONResponse({"error": "Verification failed"}, status_code=403)

@router.post("/webhook")
@router.post("/webhook/")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    🔥 NO PYDANTIC - Manual JSON parsing only
    🔥 ALWAYS returns 200 OK
    🔥 NEVER returns 422
    """
    
    request_id = generate_request_id()
    
    # Read raw body
    try:
        raw_body = await request.body()
    except Exception as e:
        logger.error(f"[{request_id}] Failed to read body: {e}")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # Parse JSON manually (NO PYDANTIC)
    try:
        data = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as e:
        logger.error(f"[{request_id}] JSON parse failed: {e}")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # Validate it's a WhatsApp webhook
    if not data or data.get('object') != 'whatsapp_business_account':
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # Extract message (manual - NO PYDANTIC)
    try:
        entries = data.get('entry') or []
        if not entries:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        changes = entries[0].get('changes') or []
        if not changes:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        value = changes[0].get('value') or {}
        
        if 'statuses' in value:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        messages = value.get('messages') or []
        if not messages:
            return JSONResponse({"status": "ok"}, status_code=200)
        
        message = messages[0]
        phone_number = message.get('from')
        message_id = message.get('id')
        
        if not phone_number or not message_id:
            logger.warning(f"[{request_id}] Missing phone or message_id")
            return JSONResponse({"status": "ok"}, status_code=200)
        
        message_text = message.get('text', {}).get('body', '')
        if not message_text:
            return JSONResponse({"status": "ok"}, status_code=200)
        
    except Exception as e:
        logger.error(f"[{request_id}] Error extracting: {e}")
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # Deduplicate
    if message_id in _processed_messages:
        return JSONResponse({"status": "ok"}, status_code=200)
    _processed_messages[message_id] = time.time()
    
    # Rate limit
    if not check_rate_limit(phone_number):
        return JSONResponse({"status": "ok"}, status_code=200)
    
    # Process in background
    contacts = value.get('contacts') or []
    sender_name = contacts[0].get('profile', {}).get('name', 'User') if contacts else 'User'
    
    logger.info(f"[{request_id}] 📨 Message from {mask_sensitive_data(phone_number)}: {message_text[:50]}...")
    
    background_tasks.add_task(
        process_whatsapp_message,
        phone_number,
        message_text.strip(),
        sender_name,
        message_id,
        request_id
    )
    
    # ALWAYS RETURN 200 OK
    return JSONResponse({"status": "ok"}, status_code=200)

async def process_whatsapp_message(
    phone_number: str,
    message_text: str,
    sender_name: str,
    message_id: str,
    request_id: str
):
    start_time = time.time()
    
    try:
        logger.info(f"[{request_id}] 🔄 Processing: {message_text[:50]}...")
        
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, process_whatsapp_query, message_text, None, phone_number, None, request_id),
            timeout=PROCESSING_TIMEOUT_SECONDS
        )
        
        if response:
            await asyncio.get_event_loop().run_in_executor(
                None, send_text_message, phone_number, response, message_id, request_id
            )
            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(f"[{request_id}] ✅ Response sent in {duration_ms}ms")
        
    except asyncio.TimeoutError:
        logger.error(f"[{request_id}] ⏳ Timeout")
        send_text_message(phone_number, "⏳ Still processing...", message_id, request_id)
    except Exception as e:
        logger.exception(f"[{request_id}] ❌ Error: {e}")
        send_text_message(phone_number, "⚠️ Error. Please try again.", message_id, request_id)

@router.get("/webhook/health")
async def webhook_health():
    return {
        "status": "healthy",
        "version": "21.1",
        "timestamp": datetime.now().isoformat(),
        "config": {
            "whatsapp_token": bool(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')),
            "phone_number_id": bool(getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')),
            "verify_token": bool(getattr(config, 'WHATSAPP_VERIFY_TOKEN', ''))
        }
    }

@router.get("/webhook/ping")
async def webhook_ping():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

logger.info("=" * 60)
logger.info("Webhook v21.1 - 422 COMPLETELY ELIMINATED")
logger.info("=" * 60)
logger.info("✅ NO Pydantic validation - manual JSON only")
logger.info("✅ ALWAYS returns 200 OK to Meta")
logger.info("✅ Messages processed in background")
logger.info("=" * 60)
logger.info("🚀 Webhook ready to receive messages from WhatsApp!")
EOF

# 5. Verify NO Pydantic in the file
grep -n "from pydantic" app/routes/webhook.py
# Should return NOTHING

# 6. Restart the service
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 7. Monitor logs
tail -f logs/app.log
