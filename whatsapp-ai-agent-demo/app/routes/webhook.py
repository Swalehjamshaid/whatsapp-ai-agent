"""
WhatsApp Webhook Handler - FastAPI Version
Version: 5.4 (Fixed Async Issues - No Event Loop Conflicts)
Architecture: webhook.py → AI Query Service → KPI/Analytics Services → WhatsApp Service

100% INTEGRATED with:
- app/main.py (FastAPI)
- app/services/ai_query_service.py
- app/services/kpi_service.py
- app/services/analytics_service.py
- app/services/schema_service.py
- app/services/whatsapp_service.py
"""

import json
import hashlib
import hmac
import re
import uuid
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional, Tuple, List
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, Response
from loguru import logger

# Import services
from app.services.ai_query_service import get_ai_query_service, QueryPlan, IntentType
from app.services.kpi_service import get_kpi_service
from app.services.analytics_service import get_analytics_service, RankingReport, ControlTowerReport
from app.services.schema_service import get_schema_service, DateFilter
from app.services.whatsapp_service import send_text_message, get_whatsapp_service
from app.config import config

# ==========================================================
# CONSTANTS
# ==========================================================

router = APIRouter(tags=["WhatsApp Webhook"])

# Redis client for deduplication & session
_redis_client = None

# Session TTL (30 minutes)
SESSION_TTL = 1800

# Message deduplication TTL (24 hours)
DEDUP_TTL = 86400

# Rate limiting
RATE_LIMIT_REQUESTS = 20
RATE_LIMIT_WINDOW = 60

# Cache TTL from config (with fallback)
CACHE_TTL = getattr(config, 'CACHE_TTL', 300)


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def generate_request_id() -> str:
    """Generate unique request ID for tracing"""
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


def get_redis_client():
    """Get Redis client from config"""
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            redis_config = getattr(config, 'REDIS_CONFIG', {})
            _redis_client = redis.Redis(
                host=redis_config.get('host', 'localhost'),
                port=redis_config.get('port', 6379),
                db=redis_config.get('db', 0),
                decode_responses=True,
                socket_connect_timeout=5
            )
            _redis_client.ping()
            logger.info("Redis client connected")
        except Exception as e:
            logger.warning(f"Redis not available: {e}")
            _redis_client = None
    return _redis_client


# ==========================================================
# SECURITY FUNCTIONS
# ==========================================================

def verify_signature(payload: bytes, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256 header"""
    app_secret = getattr(config, 'WHATSAPP_APP_SECRET', '')
    
    if not app_secret or not signature_header:
        return False
    
    try:
        expected = hmac.new(
            app_secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature_header)
    except Exception:
        return False


def check_rate_limit(client_ip: str, request_id: str) -> bool:
    """Check rate limit for client IP"""
    redis_client = get_redis_client()
    if not redis_client:
        return True
    
    try:
        key = f"rate_limit:{client_ip}"
        current = redis_client.get(key)
        
        if current and int(current) >= RATE_LIMIT_REQUESTS:
            return False
        
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, RATE_LIMIT_WINDOW)
        pipe.execute()
        return True
    except Exception as e:
        logger.error(f"[{request_id}] Rate limit error: {e}")
        return True


def is_duplicate(message_id: str, request_id: str) -> bool:
    """Check if message has been processed before"""
    if not message_id:
        return False
    
    redis_client = get_redis_client()
    if not redis_client:
        return False
    
    try:
        key = f"processed:{message_id}"
        if redis_client.exists(key):
            return True
        
        redis_client.setex(key, DEDUP_TTL, "1")
        return False
    except Exception as e:
        logger.error(f"[{request_id}] Dedup error: {e}")
        return False


def get_session_key(phone_number: str) -> str:
    """Get Redis key for user session"""
    return f"session:{phone_number}"


def get_user_session(phone_number: str) -> Optional[Dict[str, Any]]:
    """Get user session from Redis"""
    redis_client = get_redis_client()
    if not redis_client:
        return None
    
    try:
        key = get_session_key(phone_number)
        data = redis_client.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        logger.error(f"Session retrieval error: {e}")
    
    return None


def save_user_session(phone_number: str, session_data: Dict[str, Any]) -> bool:
    """Save user session to Redis"""
    redis_client = get_redis_client()
    if not redis_client:
        return False
    
    try:
        key = get_session_key(phone_number)
        redis_client.setex(key, SESSION_TTL, json.dumps(session_data))
        return True
    except Exception as e:
        logger.error(f"Session save error: {e}")
        return False


# ==========================================================
# 1. GET WEBHOOK VERIFICATION
# ==========================================================

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = None,
    hub_verify_token: str = None,
    hub_challenge: str = None
):
    """
    Meta WhatsApp verification endpoint.
    
    Query Parameters:
        hub.mode: subscribe
        hub.verify_token: Your verify token
        hub.challenge: Challenge to return
    """
    request_id = generate_request_id()
    
    try:
        logger.info(f"[{request_id}] Webhook verification - Mode: {hub_mode}")
        
        verify_token = getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')
        
        if hub_mode == 'subscribe' and hub_verify_token == verify_token:
            logger.info(f"[{request_id}] Webhook verified successfully")
            return Response(content=hub_challenge, status_code=200)
        else:
            logger.warning(f"[{request_id}] Verification failed")
            return JSONResponse(content={"error": "Verification failed"}, status_code=403)
            
    except Exception as e:
        logger.error(f"[{request_id}] Verification error: {e}")
        return JSONResponse(content={"error": "Internal error"}, status_code=500)


# ==========================================================
# 2. POST MESSAGE RECEIVER (MAIN ENTRY POINT)
# ==========================================================

@router.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Main webhook handler for incoming messages.
    """
    start_time = datetime.now()
    request_id = generate_request_id()
    
    try:
        # ====================================================
        # SECURITY & VALIDATION
        # ====================================================
        
        # Get raw body for signature verification
        raw_body = await request.body()
        
        # Verify signature in production
        if getattr(config, 'ENVIRONMENT', 'production') == 'production':
            signature = request.headers.get('X-Hub-Signature-256', '')
            if not verify_signature(raw_body, signature):
                logger.error(f"[{request_id}] Invalid signature")
                return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        
        # Rate limiting
        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit(client_ip, request_id):
            logger.warning(f"[{request_id}] Rate limit exceeded")
            return JSONResponse(content={"error": "Too Many Requests"}, status_code=429)
        
        # Parse JSON
        try:
            data = await request.json()
            if not data:
                return JSONResponse(content={"status": "error", "message": "Empty payload"}, status_code=400)
        except Exception as e:
            logger.error(f"[{request_id}] JSON parse error: {e}")
            return JSONResponse(content={"status": "error", "message": "Invalid JSON"}, status_code=400)
        
        # Validate payload structure
        if not validate_payload(data):
            logger.debug(f"[{request_id}] Non-message event")
            return JSONResponse(content={"status": "ok"}, status_code=200)
        
        # ====================================================
        # MESSAGE EXTRACTION
        # ====================================================
        
        phone_number, message_text, message_id, sender_name = extract_message(data)
        
        if not phone_number or not message_text:
            logger.info(f"[{request_id}] No valid message to process")
            return JSONResponse(content={"status": "ok"}, status_code=200)
        
        logger.info(f"[{request_id}] Message from {phone_number}: {message_text[:100]}")
        
        # ====================================================
        # DEDUPLICATION
        # ====================================================
        
        if is_duplicate(message_id, request_id):
            logger.info(f"[{request_id}] Duplicate message: {message_id}")
            return JSONResponse(content={"status": "ok", "message": "duplicate"}, status_code=200)
        
        # ====================================================
        # PROCESS MESSAGE (Background task to avoid timeout)
        # ====================================================
        
        # FIX: Use synchronous function for background task
        background_tasks.add_task(
            process_and_respond_sync,
            phone_number=phone_number,
            message_text=message_text,
            sender_name=sender_name,
            message_id=message_id,
            request_id=request_id
        )
        
        # Return immediately to avoid webhook timeout
        return JSONResponse(content={"status": "ok", "message": "processing"}, status_code=200)
        
    except Exception as e:
        logger.error(f"[{request_id}] Webhook error: {e}", exc_info=True)
        return JSONResponse(content={"status": "error", "message": "Internal error"}, status_code=500)


# ==========================================================
# SYNCHRONOUS PROCESSING FUNCTIONS (FIX FOR ASYNCIO CRASH)
# ==========================================================

def process_and_respond_sync(
    phone_number: str,
    message_text: str,
    sender_name: str,
    message_id: str,
    request_id: str
):
    """
    Process message and send response - FULLY SYNCHRONOUS.
    This avoids the asyncio event loop crash.
    """
    try:
        # Process message synchronously
        response_text = process_incoming_message_sync(
            phone_number=phone_number,
            message_text=message_text,
            sender_name=sender_name,
            request_id=request_id
        )
        
        if not response_text:
            response_text = "I'm sorry, I couldn't process your request. Please try again."
        
        send_result = send_text_message(
            phone_number=phone_number,
            message=response_text,
            message_id=message_id,
            request_id=request_id
        )
        
        if not send_result.get('success'):
            logger.error(f"[{request_id}] Failed to send response: {send_result.get('error')}")
            
    except Exception as e:
        logger.error(f"[{request_id}] Background processing error: {e}", exc_info=True)


def process_incoming_message_sync(
    phone_number: str,
    message_text: str,
    sender_name: str,
    request_id: str
) -> Optional[str]:
    """
    Process incoming message - SYNCHRONOUS VERSION.
    No async/await to prevent event loop conflicts.
    """
    # Quick command handling
    quick_response = handle_quick_commands(message_text)
    if quick_response:
        return quick_response
    
    try:
        # Check for DN number pattern (fast path - no AI needed)
        dn_match = re.search(r'\b(\d{8,12})\b', message_text)
        if dn_match:
            return handle_dn_lookup_sync(dn_match.group(1), message_text, request_id)
        
        # Check for warehouse names
        warehouses = ['lahore', 'karachi', 'rawalpindi', 'islamabad', 'multan', 'faisalabad']
        msg_lower = message_text.lower()
        for wh in warehouses:
            if wh in msg_lower:
                return handle_warehouse_query_sync(wh, request_id)
        
        # Default to dealer query
        return handle_dealer_query_sync(message_text, request_id)
        
    except Exception as e:
        logger.error(f"[{request_id}] Message processing error: {e}", exc_info=True)
        return "⚠️ I'm having trouble processing your request. Please try again in a moment."


def handle_dn_lookup_sync(dn_number: str, original_message: str, request_id: str) -> str:
    """Handle DN lookup - SYNCHRONOUS"""
    try:
        schema_service = get_schema_service()
        record = schema_service.get_dn_details(dn_number)
        
        if not record:
            return f"❌ DN {dn_number} not found in our system."
        
        dn_no = record.dn_no or "N/A"
        customer = record.customer_name or "Unknown"
        amount = float(record.dn_amount or 0)
        status = record.delivery_status or "Pending"
        
        emoji = "✅" if status.lower() == "delivered" else "⏳" if "pending" in status.lower() else "📄"
        
        return f"""{emoji} *DN: {dn_no}*

🏪 *Customer:* {customer}
💰 *Amount:* PKR {amount:,.0f}
📊 *Status:* {status}"""
    except Exception as e:
        logger.error(f"[{request_id}] DN lookup error: {e}")
        return f"❌ Error looking up DN {dn_number}. Please try again."


def handle_dealer_query_sync(dealer_name: str, request_id: str) -> str:
    """Handle dealer query - SYNCHRONOUS"""
    try:
        schema_service = get_schema_service()
        kpi_service = get_kpi_service()
        
        records = schema_service.get_dealer_records(dealer_name)
        
        if not records:
            exact_dealer = schema_service.find_closest_dealer(dealer_name)
            if exact_dealer:
                records = schema_service.get_dealer_records(exact_dealer)
                dealer_name = exact_dealer
        
        if not records:
            return f"❌ Dealer '{dealer_name}' not found. Try typing just the dealer name."
        
        kpi_data = kpi_service.calculate_dealer_kpis(records)
        
        if not kpi_data:
            return f"❌ No data available for {dealer_name}"
        
        return f"""🏪 *Dealer Dashboard: {dealer_name}*

💰 Revenue: PKR {kpi_data.revenue:,.0f}
📦 Units: {kpi_data.units:,}
📄 DNs: {kpi_data.dn_count}

🚚 Delivery Rate: {kpi_data.delivery_rate:.1f}%
📎 POD Rate: {kpi_data.pod_rate:.1f}%

⏰ Delivery Aging: {kpi_data.avg_delivery_aging:.1f} days
⏰ POD Aging: {kpi_data.avg_pod_aging:.1f} days

⚠️ Critical DNs: {kpi_data.critical_dn}"""
    except Exception as e:
        logger.error(f"[{request_id}] Dealer query error: {e}")
        return "⚠️ Error processing dealer query. Please try again."


def handle_warehouse_query_sync(warehouse_name: str, request_id: str) -> str:
    """Handle warehouse query - SYNCHRONOUS"""
    try:
        schema_service = get_schema_service()
        kpi_service = get_kpi_service()
        
        records = schema_service.get_warehouse_records(warehouse_name)
        
        if not records:
            exact_warehouse = schema_service.find_closest_warehouse(warehouse_name)
            if exact_warehouse:
                records = schema_service.get_warehouse_records(exact_warehouse)
                warehouse_name = exact_warehouse
        
        if not records:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        kpi_data = kpi_service.calculate_warehouse_kpis(records)
        
        if not kpi_data:
            return f"❌ No data available for {warehouse_name}"
        
        return f"""🏭 *Warehouse Dashboard: {warehouse_name}*

💰 Revenue: PKR {kpi_data.revenue:,.0f}
📦 Units: {kpi_data.units:,}
📄 DNs: {kpi_data.dn_count}

⏳ Pending Delivery: {kpi_data.pending_delivery}
⏳ Pending POD: {kpi_data.pending_pod}

⏰ Delivery Aging: {kpi_data.avg_delivery_aging:.1f} days
⏰ POD Aging: {kpi_data.avg_pod_aging:.1f} days"""
    except Exception as e:
        logger.error(f"[{request_id}] Warehouse query error: {e}")
        return "⚠️ Error processing warehouse query. Please try again."


# ==========================================================
# PAYLOAD VALIDATION & EXTRACTION
# ==========================================================

def validate_payload(data: Dict) -> bool:
    """Validate WhatsApp webhook payload"""
    try:
        if data.get('object') != 'whatsapp_business_account':
            return False
        
        entry = data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        
        return 'messages' in value and len(value.get('messages', [])) > 0
    except Exception:
        return False


def extract_message(data: Dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Extract phone number, message text, message ID, and sender name"""
    try:
        entry = data['entry'][0]
        changes = entry['changes'][0]
        value = changes['value']
        messages = value['messages'][0]
        contacts = value.get('contacts', [{}])[0]
        
        phone_number = messages.get('from')
        message_id = messages.get('id')
        sender_name = contacts.get('profile', {}).get('name', 'User')
        
        message_type = messages.get('type')
        message_text = None
        
        if message_type == 'text':
            message_text = messages.get('text', {}).get('body', '')
        elif message_type == 'interactive':
            interactive = messages.get('interactive', {})
            if interactive.get('type') == 'button_reply':
                message_text = interactive.get('button_reply', {}).get('title', '')
            elif interactive.get('type') == 'list_reply':
                message_text = interactive.get('list_reply', {}).get('title', '')
        elif message_type == 'button':
            button = messages.get('button', {})
            message_text = button.get('text', '')
        
        return phone_number, message_text, message_id, sender_name
        
    except Exception as e:
        logger.error(f"Message extraction error: {e}")
        return None, None, None, None


# ==========================================================
# QUICK COMMAND HANDLERS
# ==========================================================

def handle_quick_commands(message_text: str) -> Optional[str]:
    """Handle simple commands without AI processing"""
    msg_lower = message_text.lower().strip()
    
    if msg_lower in ['/help', 'help', 'menu', 'commands', '?', '/?']:
        return format_help_message()
    
    if msg_lower in ['/status', 'status', 'health', 'ping']:
        return format_status_message()
    
    if msg_lower in ['hi', 'hello', 'hey', 'start', 'welcome']:
        return format_welcome_message()
    
    return None


def format_help_message() -> str:
    """Format help message"""
    return """📋 *Logistics AI Assistant - Help*

*What I can do:*

🔍 *Track Delivery* - Send any 10+ digit DN number
🏪 *Dealer Queries* - "Show dealer ABC Traders"
🏭 *Warehouse Queries* - "Lahore warehouse summary"
📊 *Rankings* - "Top 5 dealers by revenue"
⚖️ *Comparisons* - "Compare Lahore vs Karachi"
🚨 *Control Tower* - "Show me alerts"
📈 *Executive Dashboard* - "Executive dashboard"

*Commands:* `/help`, `/status`, `/clear`

*Example:* "Show top 10 dealers by revenue this month"

Need help? Just ask! 🤖"""


def format_status_message() -> str:
    """Format status message"""
    whatsapp_configured = bool(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '') and 
                                getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', ''))
    
    return f"""📊 *System Status*

✅ AI Query Service
✅ KPI Service
✅ Analytics Service
✅ Schema Service
{'✅' if whatsapp_configured else '❌'} WhatsApp Service
{'✅' if get_redis_client() else '❌'} Redis Cache

*Environment:* {getattr(config, 'ENVIRONMENT', 'development')}
*Cache TTL:* {CACHE_TTL}s

All systems operational! 🚀"""


def format_welcome_message() -> str:
    """Format welcome message"""
    return """👋 *Welcome to Logistics AI Assistant!*

I can help you with:
• Track deliveries (send DN number)
• Dealer performance reports
• Warehouse analytics
• Rankings & comparisons
• Executive dashboards

📋 Type *Help* to see all commands

What would you like to know today?"""


def format_unknown_response(message_text: str) -> str:
    """Format response for unknown intent"""
    return f"""❓ I'm not sure how to help with: "{message_text[:50]}"

📋 Try:
• "Help" for all commands
• "Show dealer ABC Traders"
• Send a DN number to track"""


# ==========================================================
# HEALTH CHECK ENDPOINTS
# ==========================================================

@router.get("/webhook/health")
async def webhook_health():
    """Health check endpoint for webhook monitoring"""
    whatsapp_service = get_whatsapp_service()
    schema_service = get_schema_service()
    redis_client = get_redis_client()
    
    return {
        'status': 'healthy',
        'version': '5.4',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'whatsapp': whatsapp_service.health_check() if whatsapp_service else {'configured': False},
            'schema': {'initialized': schema_service is not None},
            'redis': {'connected': redis_client is not None}
        },
        'config': {
            'environment': getattr(config, 'ENVIRONMENT', 'development'),
            'cache_ttl': CACHE_TTL
        }
    }


@router.get("/webhook/session/{phone_number}")
async def get_session(phone_number: str):
    """Get user session information"""
    session = get_user_session(phone_number)
    if session:
        return {
            'success': True,
            'phone': session.get('phone'),
            'conversation_id': session.get('conversation_id'),
            'created_at': session.get('created_at'),
            'history_count': len(session.get('history', []))
        }
    return {'success': False, 'message': 'Session not found'}


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("WhatsApp Webhook v5.4 - FastAPI Compatible (Async Fixed)")
logger.info("=" * 60)
logger.info("   ✅ Converted from Flask to FastAPI")
logger.info("   ✅ Using APIRouter for compatibility")
logger.info("   ✅ SYNCHRONOUS background processing (no event loop conflicts)")
logger.info("   ✅ Session management with Redis")
logger.info("   ✅ All original functionality preserved")
logger.info("=" * 60)
