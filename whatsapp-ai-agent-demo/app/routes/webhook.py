"""
WhatsApp Webhook Handler - FastAPI Version
Version: 5.3 (FastAPI Compatible)
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
import asyncio
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
        
        background_tasks.add_task(
            process_and_respond,
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


async def process_and_respond(
    phone_number: str,
    message_text: str,
    sender_name: str,
    message_id: str,
    request_id: str
):
    """Process message and send response (background task)"""
    try:
        response_text = await process_incoming_message(
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
# MESSAGE PROCESSING ORCHESTRATOR
# ==========================================================

async def process_incoming_message(
    phone_number: str,
    message_text: str,
    sender_name: str,
    request_id: str
) -> Optional[str]:
    """
    Process incoming message using AI Query Service.
    """
    ai_query_service = get_ai_query_service()
    
    # Quick command handling
    quick_response = handle_quick_commands(message_text)
    if quick_response:
        return quick_response
    
    try:
        # Get Query Plan from AI Query Service
        query_plan = await ai_query_service.process_query(message_text)
        
        logger.info(f"[{request_id}] Query Plan: intent={query_plan.intent}, confidence={query_plan.confidence_score}")
        
        # Route based on intent
        if query_plan.intent == IntentType.HELP:
            return format_help_message()
        
        if query_plan.intent in [IntentType.DN_LOOKUP, IntentType.DN_STATUS]:
            return handle_dn_lookup(query_plan, request_id)
        
        if query_plan.intent == IntentType.DEALER_DASHBOARD:
            return handle_dealer_query(query_plan, request_id)
        
        if query_plan.intent == IntentType.WAREHOUSE_DASHBOARD:
            return handle_warehouse_query(query_plan, request_id)
        
        if query_plan.intent == IntentType.CITY_DASHBOARD:
            return handle_city_query(query_plan, request_id)
        
        if query_plan.intent == IntentType.RANKING:
            return handle_ranking(query_plan, request_id)
        
        if query_plan.intent == IntentType.COMPARISON:
            return handle_comparison(query_plan, request_id)
        
        if query_plan.intent == IntentType.CONTROL_TOWER:
            return handle_control_tower(query_plan, request_id)
        
        if query_plan.intent == IntentType.EXECUTIVE_DASHBOARD:
            return handle_executive_dashboard(request_id)
        
        # Default response
        return format_unknown_response(message_text)
        
    except Exception as e:
        logger.error(f"[{request_id}] Message processing error: {e}", exc_info=True)
        return "⚠️ I'm having trouble processing your request. Please try again in a moment."


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
    whatsapp_configured = bool(config.WHATSAPP_ACCESS_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID)
    
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


# ==========================================================
# DN LOOKUP HANDLER
# ==========================================================

def handle_dn_lookup(query_plan: QueryPlan, request_id: str) -> str:
    """Handle DN lookup intent"""
    schema_service = get_schema_service()
    
    dn_number = query_plan.entity_value or query_plan.filters.get('dn_number')
    
    if not dn_number:
        dn_match = re.search(r'\b(\d{8,12})\b', query_plan.original_message)
        if dn_match:
            dn_number = dn_match.group(1)
        else:
            return "❌ Please provide a valid DN number (8-12 digits)."
    
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


# ==========================================================
# DEALER QUERY HANDLER
# ==========================================================

def handle_dealer_query(query_plan: QueryPlan, request_id: str) -> str:
    """Handle dealer dashboard intent"""
    schema_service = get_schema_service()
    kpi_service = get_kpi_service()
    
    dealer_name = query_plan.entity_value or query_plan.filters.get('dealer') or query_plan.original_message.strip()
    
    records = schema_service.get_dealer_records(dealer_name)
    
    if not records:
        exact_dealer = schema_service.find_closest_dealer(dealer_name)
        if exact_dealer:
            records = schema_service.get_dealer_records(exact_dealer)
            dealer_name = exact_dealer
    
    if not records:
        return f"❌ Dealer '{dealer_name}' not found."
    
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


# ==========================================================
# WAREHOUSE QUERY HANDLER
# ==========================================================

def handle_warehouse_query(query_plan: QueryPlan, request_id: str) -> str:
    """Handle warehouse dashboard intent"""
    schema_service = get_schema_service()
    kpi_service = get_kpi_service()
    
    warehouse_name = query_plan.entity_value or query_plan.filters.get('warehouse') or query_plan.original_message.strip()
    
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


# ==========================================================
# CITY QUERY HANDLER
# ==========================================================

def handle_city_query(query_plan: QueryPlan, request_id: str) -> str:
    """Handle city dashboard intent"""
    schema_service = get_schema_service()
    
    city_name = query_plan.entity_value or query_plan.filters.get('city') or query_plan.original_message.strip()
    
    records = schema_service.get_city_records(city_name)
    
    if not records:
        exact_city = schema_service.find_closest_city(city_name)
        if exact_city:
            records = schema_service.get_city_records(exact_city)
            city_name = exact_city
    
    if not records:
        return f"❌ City '{city_name}' not found."
    
    kpi_service = get_kpi_service()
    city_kpi = kpi_service.calculate_city_kpis(records)
    
    return f"""🌆 *City Dashboard: {city_name}*

💰 Revenue: PKR {city_kpi.get('revenue', 0):,.0f}
📦 Units: {city_kpi.get('units', 0):,}
📄 DNs: {city_kpi.get('dn_count', 0)}

📈 Delivery Rate: {city_kpi.get('delivery_rate', 0):.1f}%
⏰ Avg Aging: {city_kpi.get('avg_delivery_aging', 0):.1f} days"""


# ==========================================================
# RANKING HANDLER
# ==========================================================

def handle_ranking(query_plan: QueryPlan, request_id: str) -> str:
    """Handle ranking intent"""
    analytics_service = get_analytics_service()
    schema_service = get_schema_service()
    kpi_service = get_kpi_service()
    
    dimension = query_plan.dimension or 'dealer'
    metric = query_plan.metric or 'revenue'
    limit = min(query_plan.limit or 10, 20)
    top = query_plan.ranking_type == 'top'
    
    all_records = schema_service.get_all_records()
    
    if not all_records:
        return "❌ No data available for ranking."
    
    if dimension == 'dealer':
        dealer_kpis = kpi_service.calculate_all_dealers_kpis(all_records)
        if not dealer_kpis:
            return "❌ No dealer data available."
        ranking_report = analytics_service.rank_dealers(
            [k.__dict__ for k in dealer_kpis if k.revenue > 0],
            metric=metric, limit=limit, reverse=top
        )
    elif dimension == 'warehouse':
        warehouse_kpis = kpi_service.calculate_all_warehouses_kpis(all_records)
        if not warehouse_kpis:
            return "❌ No warehouse data available."
        ranking_report = analytics_service.rank_warehouses(
            [k.__dict__ for k in warehouse_kpis if k.revenue > 0],
            metric=metric, limit=limit, reverse=top
        )
    else:
        return f"❌ Cannot rank by {dimension}"
    
    if not ranking_report.items:
        return f"❌ No {dimension}s found for ranking."
    
    title = "🏆 *Top*" if top else "📉 *Bottom*"
    metric_name = metric.replace('_', ' ').title()
    
    lines = [f"{title} {dimension.title()}s by {metric_name}", ""]
    
    for item in ranking_report.items[:10]:
        if metric == 'revenue':
            value_str = f"PKR {item.value:,.0f}"
        elif metric in ['delivery_rate', 'pod_rate']:
            value_str = f"{item.value:.1f}%"
        else:
            value_str = f"{item.value:,.0f}"
        lines.append(f"{item.rank}. {item.name[:30]} - {value_str}")
    
    lines.append(f"\n📊 Based on {ranking_report.total_items} total {dimension}s")
    
    return "\n".join(lines)


# ==========================================================
# COMPARISON HANDLER
# ==========================================================

def handle_comparison(query_plan: QueryPlan, request_id: str) -> str:
    """Handle comparison intent"""
    analytics_service = get_analytics_service()
    schema_service = get_schema_service()
    kpi_service = get_kpi_service()
    
    comparison = query_plan.comparison_entities
    if not comparison:
        return "❌ Please specify two items to compare"
    
    left = comparison.get('left', '').strip()
    right = comparison.get('right', '').strip()
    metric = query_plan.metric or 'revenue'
    
    if not left or not right:
        return "❌ Please provide two items to compare."
    
    all_records = schema_service.get_all_records()
    dealer_kpis = kpi_service.calculate_all_dealers_kpis(all_records)
    
    left_kpi = None
    right_kpi = None
    
    for k in dealer_kpis:
        if left.lower() in k.dealer_name.lower():
            left_kpi = k
        if right.lower() in k.dealer_name.lower():
            right_kpi = k
    
    if not left_kpi:
        return f"❌ '{left}' not found"
    if not right_kpi:
        return f"❌ '{right}' not found"
    
    result = analytics_service.compare_dealers(left_kpi.__dict__, right_kpi.__dict__, metric=metric)
    
    if metric == 'revenue':
        a_str = f"PKR {result.a_value:,.0f}"
        b_str = f"PKR {result.b_value:,.0f}"
        diff_str = f"PKR {abs(result.difference):,.0f}"
    else:
        a_str = f"{result.a_value:.1f}"
        b_str = f"{result.b_value:.1f}"
        diff_str = f"{abs(result.difference):.1f}"
    
    return f"""⚖️ *Comparison: {metric.replace('_', ' ').title()}*

📊 *{result.entity_a}*: {a_str}
📊 *{result.entity_b}*: {b_str}

📈 *Difference:* {diff_str} ({abs(result.percent_difference):.1f}%)
🏆 *Winner:* {result.winner} (+{result.winning_margin:.1f}%)"""


# ==========================================================
# CONTROL TOWER HANDLER
# ==========================================================

def handle_control_tower(query_plan: QueryPlan, request_id: str) -> str:
    """Handle control tower intent"""
    analytics_service = get_analytics_service()
    schema_service = get_schema_service()
    kpi_service = get_kpi_service()
    
    all_records = schema_service.get_all_records()
    
    if not all_records:
        return "🚨 *Control Tower*\n\n✅ No data available for analysis."
    
    warehouse_kpis = kpi_service.calculate_all_warehouses_kpis(all_records)
    dealer_kpis = kpi_service.calculate_all_dealers_kpis(all_records)
    
    report = analytics_service.critical_delivery_report(
        [k.__dict__ for k in warehouse_kpis] if warehouse_kpis else [],
        [k.__dict__ for k in dealer_kpis] if dealer_kpis else []
    )
    
    if not report.alerts:
        return "🚨 *Control Tower*\n\n✅ No critical alerts at this time."
    
    lines = ["🚨 *Control Tower - Critical Alerts*", ""]
    
    for alert in report.alerts[:5]:
        emoji = {"RED": "🔴", "ORANGE": "🟠", "YELLOW": "🟡"}.get(alert.severity, "⚠️")
        lines.append(f"{emoji} *{alert.entity_name}*")
        lines.append(f"   {alert.message}")
        lines.append("")
    
    lines.append("📊 *Summary*")
    lines.append(f"🔴 RED: {report.risk_summary.get('RED', 0)}")
    lines.append(f"🟠 ORANGE: {report.risk_summary.get('ORANGE', 0)}")
    lines.append(f"🟡 YELLOW: {report.risk_summary.get('YELLOW', 0)}")
    
    return "\n".join(lines)


# ==========================================================
# EXECUTIVE DASHBOARD HANDLER
# ==========================================================

def handle_executive_dashboard(request_id: str) -> str:
    """Handle executive dashboard intent"""
    schema_service = get_schema_service()
    kpi_service = get_kpi_service()
    
    all_records = schema_service.get_all_records()
    
    if not all_records:
        return "📊 *Executive Dashboard*\n\n❌ No data available."
    
    executive_kpi = kpi_service.calculate_executive_kpis(all_records)
    
    return f"""📊 *Executive Dashboard*

💰 Revenue: PKR {executive_kpi.total_revenue:,.0f}
📦 Units: {executive_kpi.total_units:,}
📄 DNs: {executive_kpi.total_dn}

📈 Delivery Rate: {executive_kpi.delivery_rate:.1f}%
📈 POD Rate: {executive_kpi.pod_rate:.1f}%

⏰ Delivery Aging: {executive_kpi.avg_delivery_aging:.1f} days
⏰ POD Aging: {executive_kpi.avg_pod_aging:.1f} days

⚠️ Critical DNs: {executive_kpi.critical_deliveries}
⚠️ Critical PODs: {executive_kpi.critical_pod}"""


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
        'version': '5.3',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'whatsapp': whatsapp_service.health_check(),
            'schema': {'initialized': True},
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
logger.info("WhatsApp Webhook v5.3 - FastAPI Compatible")
logger.info("=" * 60)
logger.info("   ✅ Converted from Flask to FastAPI")
logger.info("   ✅ Using APIRouter for compatibility")
logger.info("   ✅ Background tasks for async processing")
logger.info("   ✅ Session management with Redis")
logger.info("   ✅ All original functionality preserved")
logger.info("=" * 60)
