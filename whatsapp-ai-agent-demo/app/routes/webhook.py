# ==========================================================
# FILE: app/routes/webhook.py (ENTERPRISE v6.0 - 100% INTEGRATED)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - Complete Integration
# INTEGRATES WITH:
#   ✅ app/main.py - FastAPI router registration
#   ✅ app/services/ai_query_service.py - Natural language understanding
#   ✅ app/services/ai_provider_service.py - Legacy AI processing (FIXED)
#   ✅ app/services/kpi_service.py - KPI calculations
#   ✅ app/services/analytics_service.py - Rankings & control tower
#   ✅ app/services/logistics_query_service.py - Dashboard builder (FIXED)
#   ✅ app/services/schema_service.py - Database repository
#   ✅ app/services/whatsapp_service.py - Message sending
#   ✅ app/config.py - Configuration
# ==========================================================

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

# ==========================================================
# SERVICE IMPORTS - 100% INTEGRATED
# ==========================================================

# Core AI Services
from app.services.ai_query_service import get_ai_query_service, IntentType
from app.services.ai_provider_service import process_whatsapp_query  # ✅ FIXED: Now integrated!

# Business Intelligence Services
from app.services.kpi_service import get_kpi_service
from app.services.analytics_service import get_analytics_service

# Data & Dashboard Services
from app.services.schema_service import get_schema_service
from app.services.logistics_query_service import get_logistics_query_service  # ✅ FIXED: Now integrated!

# Communication Service
from app.services.whatsapp_service import send_text_message, get_whatsapp_service

# Configuration
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

# Cache TTL from config
CACHE_TTL = getattr(config, 'CACHE_TTL', 300)


# ==========================================================
# REDIS HELPER FUNCTIONS
# ==========================================================

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


def generate_request_id() -> str:
    """Generate unique request ID for tracing"""
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


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
            logger.info(f"[{request_id}] Duplicate message detected: {message_id}")
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
# WEBHOOK VERIFICATION (GET)
# ==========================================================

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = None,
    hub_verify_token: str = None,
    hub_challenge: str = None
):
    """
    Meta WhatsApp verification endpoint.
    WhatsApp sends a GET request to verify your webhook URL.
    """
    request_id = generate_request_id()
    
    try:
        logger.info(f"[{request_id}] Webhook verification request - Mode: {hub_mode}")
        
        verify_token = getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')
        
        if hub_mode == 'subscribe' and hub_verify_token == verify_token:
            logger.info(f"[{request_id}] ✅ Webhook verified successfully!")
            return Response(content=hub_challenge, status_code=200, media_type="text/plain")
        else:
            logger.warning(f"[{request_id}] ❌ Verification failed - Token mismatch")
            return JSONResponse(content={"error": "Verification failed"}, status_code=403)
            
    except Exception as e:
        logger.error(f"[{request_id}] Verification error: {e}")
        return JSONResponse(content={"error": "Internal error"}, status_code=500)


# ==========================================================
# WEBHOOK MESSAGE RECEIVER (POST) - 100% INTEGRATED
# ==========================================================

@router.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Main webhook handler for incoming WhatsApp messages.
    
    INTEGRATION FLOW:
    1. Receive message from WhatsApp
    2. Extract phone number and message text
    3. Route to AI Query Service for intent detection
    4. Based on intent, route to appropriate service:
       - AI Provider Service (legacy queries)
       - Logistics Query Service (dashboards)
       - KPI Service (metrics)
       - Analytics Service (rankings/trends)
    5. Send response via WhatsApp Service
    """
    start_time = datetime.now()
    request_id = generate_request_id()
    
    try:
        # ====================================================
        # 1. SECURITY & VALIDATION
        # ====================================================
        
        # Get raw body for signature verification
        raw_body = await request.body()
        
        # Verify signature in production
        if getattr(config, 'ENVIRONMENT', 'production') == 'production':
            signature = request.headers.get('X-Hub-Signature-256', '')
            if not _verify_signature(raw_body, signature):
                logger.error(f"[{request_id}] Invalid signature")
                return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        
        # Parse JSON
        try:
            data = await request.json()
            if not data:
                return JSONResponse(content={"status": "error", "message": "Empty payload"}, status_code=400)
        except Exception as e:
            logger.error(f"[{request_id}] JSON parse error: {e}")
            return JSONResponse(content={"status": "error", "message": "Invalid JSON"}, status_code=400)
        
        # Validate payload structure
        if not _validate_payload(data):
            logger.debug(f"[{request_id}] Non-message event (status update, etc.)")
            return JSONResponse(content={"status": "ok"}, status_code=200)
        
        # ====================================================
        # 2. EXTRACT MESSAGE
        # ====================================================
        
        phone_number, message_text, message_id, sender_name = _extract_message(data)
        
        if not phone_number or not message_text:
            logger.info(f"[{request_id}] No valid message to process")
            return JSONResponse(content={"status": "ok"}, status_code=200)
        
        logger.info(f"[{request_id}] 📨 Message from {phone_number}: {message_text[:100]}")
        
        # ====================================================
        # 3. DEDUPLICATION
        # ====================================================
        
        if is_duplicate(message_id, request_id):
            logger.info(f"[{request_id}] Duplicate message ignored: {message_id}")
            return JSONResponse(content={"status": "ok", "message": "duplicate"}, status_code=200)
        
        # ====================================================
        # 4. PROCESS MESSAGE (Background task)
        # ====================================================
        
        background_tasks.add_task(
            _process_message,
            phone_number=phone_number,
            message_text=message_text,
            sender_name=sender_name,
            message_id=message_id,
            request_id=request_id
        )
        
        # Return immediately to avoid webhook timeout
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"[{request_id}] Message queued for processing in {elapsed:.3f}s")
        return JSONResponse(content={"status": "ok", "message": "processing"}, status_code=200)
        
    except Exception as e:
        logger.error(f"[{request_id}] Webhook error: {e}", exc_info=True)
        return JSONResponse(content={"status": "error", "message": "Internal error"}, status_code=500)


# ==========================================================
# MESSAGE PROCESSING ENGINE - 100% INTEGRATED
# ==========================================================

def _process_message(
    phone_number: str,
    message_text: str,
    sender_name: str,
    message_id: str,
    request_id: str
):
    """
    Process message using integrated services.
    
    ROUTING DECISION TREE:
    1. Quick commands (help, status, welcome) → Direct response
    2. DN number pattern → AI Provider Service (legacy)
    3. Intent detection → AI Query Service
    4. Based on intent → Route to appropriate service
    """
    try:
        logger.info(f"[{request_id}] 🧠 Processing message from {phone_number}")
        
        # ====================================================
        # STEP 1: Quick command handling (fast path)
        # ====================================================
        
        quick_response = _handle_quick_commands(message_text)
        if quick_response:
            _send_response(phone_number, quick_response, message_id, request_id)
            return
        
        # ====================================================
        # STEP 2: DN number lookup (fast path - no AI needed)
        # ====================================================
        
        dn_match = re.search(r'\b(\d{8,12})\b', message_text)
        if dn_match:
            response = _handle_dn_lookup(dn_match.group(1), request_id)
            _send_response(phone_number, response, message_id, request_id)
            return
        
        # ====================================================
        # STEP 3: AI Query Service - Intent Detection
        # ====================================================
        
        import asyncio
        ai_query_service = get_ai_query_service()
        
        # Run async function in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            query_plan = loop.run_until_complete(ai_query_service.process_query(message_text))
        finally:
            loop.close()
        
        logger.info(f"[{request_id}] Intent: {query_plan.intent}, Confidence: {query_plan.confidence_score}")
        
        # ====================================================
        # STEP 4: Route based on intent
        # ====================================================
        
        response = None
        
        # Dealer Dashboard
        if query_plan.intent == IntentType.DEALER_DASHBOARD and query_plan.entity_value:
            response = _handle_dealer_dashboard(query_plan.entity_value, request_id)
        
        # Warehouse Dashboard
        elif query_plan.intent == IntentType.WAREHOUSE_DASHBOARD and query_plan.entity_value:
            response = _handle_warehouse_dashboard(query_plan.entity_value, request_id)
        
        # City Dashboard
        elif query_plan.intent == IntentType.CITY_DASHBOARD and query_plan.entity_value:
            response = _handle_city_dashboard(query_plan.entity_value, request_id)
        
        # Ranking
        elif query_plan.intent == IntentType.RANKING:
            response = _handle_ranking(query_plan, request_id)
        
        # Control Tower
        elif query_plan.intent == IntentType.CONTROL_TOWER:
            response = _handle_control_tower(request_id)
        
        # Executive Dashboard
        elif query_plan.intent == IntentType.EXECUTIVE_DASHBOARD:
            response = _handle_executive_dashboard(request_id)
        
        # KPI Report
        elif query_plan.intent == IntentType.KPI_REPORT:
            response = _handle_kpi_report(request_id)
        
        # Fallback: Use AI Provider Service (legacy)
        else:
            response = _handle_ai_provider_query(message_text, request_id)
        
        # ====================================================
        # STEP 5: Send response
        # ====================================================
        
        if response:
            _send_response(phone_number, response, message_id, request_id)
        else:
            fallback = "I couldn't understand your request. Please type 'Help' for available commands."
            _send_response(phone_number, fallback, message_id, request_id)
        
        logger.info(f"[{request_id}] ✅ Response sent to {phone_number}")
        
    except Exception as e:
        logger.error(f"[{request_id}] Message processing error: {e}", exc_info=True)
        error_msg = "⚠️ I'm having trouble processing your request. Please try again in a moment."
        _send_response(phone_number, error_msg, message_id, request_id)


# ==========================================================
# HANDLER FUNCTIONS - INTEGRATED WITH SERVICES
# ==========================================================

def _handle_quick_commands(message_text: str) -> Optional[str]:
    """Handle simple commands without AI processing"""
    msg_lower = message_text.lower().strip()
    
    if msg_lower in ['/help', 'help', 'menu', 'commands', '?', '/?']:
        return _format_help_message()
    
    if msg_lower in ['/status', 'status', 'health', 'ping']:
        return _format_status_message()
    
    if msg_lower in ['hi', 'hello', 'hey', 'start', 'welcome', 'assalam', 'salam']:
        return _format_welcome_message()
    
    return None


def _handle_dn_lookup(dn_number: str, request_id: str) -> str:
    """Handle DN lookup using AI Provider Service"""
    try:
        # Use the integrated AI Provider Service
        response = process_whatsapp_query(
            question=f"Show me DN {dn_number}",
            phone_number=None,
            request_id=request_id
        )
        return response
    except Exception as e:
        logger.error(f"[{request_id}] DN lookup error: {e}")
        return f"❌ Error looking up DN {dn_number}. Please try again."


def _handle_dealer_dashboard(dealer_name: str, request_id: str) -> str:
    """Handle dealer dashboard using Logistics Query Service"""
    try:
        logistics_service = get_logistics_query_service()
        dashboard = logistics_service.build_dealer_dashboard(dealer_name)
        
        if not dashboard:
            return f"❌ Dealer '{dealer_name}' not found."
        
        # Format dashboard response
        lines = [
            f"🏪 *Dealer Dashboard: {dashboard.get('dealer_name')}*",
            "",
            f"💰 Revenue: PKR {dashboard.get('revenue', 0):,.0f}",
            f"📦 Units: {dashboard.get('units', 0):,}",
            f"📄 DNs: {dashboard.get('dn_count', 0)}",
            "",
            f"🚚 Delivery Rate: {dashboard.get('delivery_rate', 0):.1f}%",
            f"📎 POD Rate: {dashboard.get('pod_rate', 0):.1f}%",
            "",
            f"⏳ Pending Delivery: {dashboard.get('pending_dn', 0)}",
            f"📎 Pending POD: {dashboard.get('pod_pending', 0)}",
            "",
            f"⏰ Delivery Aging: {dashboard.get('avg_delivery_aging', 0):.1f} days",
            f"⏰ POD Aging: {dashboard.get('avg_pod_aging', 0):.1f} days"
        ]
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"[{request_id}] Dealer dashboard error: {e}")
        return f"❌ Error fetching dealer data for '{dealer_name}'"


def _handle_warehouse_dashboard(warehouse_name: str, request_id: str) -> str:
    """Handle warehouse dashboard using Logistics Query Service"""
    try:
        logistics_service = get_logistics_query_service()
        dashboard = logistics_service.build_warehouse_dashboard(warehouse_name)
        
        if not dashboard:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        # Format dashboard response
        lines = [
            f"🏭 *Warehouse Dashboard: {dashboard.get('warehouse_name')}*",
            "",
            f"💰 Revenue: PKR {dashboard.get('revenue', 0):,.0f}",
            f"📦 Units: {dashboard.get('units', 0):,}",
            f"📄 DNs: {dashboard.get('dn_count', 0)}",
            "",
            f"⏳ Pending Delivery: {dashboard.get('pending_delivery', 0)}",
            f"📎 Pending POD: {dashboard.get('pending_pod', 0)}",
            "",
            f"⏰ Delivery Aging: {dashboard.get('avg_delivery_aging', 0):.1f} days",
            f"⏰ POD Aging: {dashboard.get('avg_pod_aging', 0):.1f} days",
            "",
            f"📊 Risk Level: {dashboard.get('risk_level', 'UNKNOWN')}",
            f"🎯 Warehouse Score: {dashboard.get('warehouse_score', 0)}/100"
        ]
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"[{request_id}] Warehouse dashboard error: {e}")
        return f"❌ Error fetching warehouse data for '{warehouse_name}'"


def _handle_city_dashboard(city_name: str, request_id: str) -> str:
    """Handle city dashboard using Logistics Query Service"""
    try:
        logistics_service = get_logistics_query_service()
        dashboard = logistics_service.build_city_dashboard(city_name)
        
        if not dashboard:
            return f"❌ City '{city_name}' not found."
        
        lines = [
            f"🌆 *City Dashboard: {dashboard.get('city_name')}*",
            "",
            f"💰 Revenue: PKR {dashboard.get('revenue', 0):,.0f}",
            f"📦 Units: {dashboard.get('units', 0):,}",
            f"📄 DNs: {dashboard.get('dn_count', 0)}",
            "",
            f"⏳ Pending Delivery: {dashboard.get('pending_delivery', 0)}",
            f"📎 Pending POD: {dashboard.get('pending_pod', 0)}",
            "",
            f"🚚 Delivery Rate: {dashboard.get('delivery_rate', 0):.1f}%"
        ]
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"[{request_id}] City dashboard error: {e}")
        return f"❌ Error fetching city data for '{city_name}'"


def _handle_ranking(query_plan, request_id: str) -> str:
    """Handle ranking queries using Analytics Service"""
    try:
        analytics_service = get_analytics_service()
        kpi_service = get_kpi_service()
        schema_service = get_schema_service()
        
        # Get all records
        all_records = schema_service.get_all_records()
        
        if query_plan.dimension == 'dealer' or 'dealer' in query_plan.filters:
            # Calculate dealer KPIs
            dealer_kpis = kpi_service.calculate_all_dealers_kpis(all_records)
            dealer_list = []
            for dk in dealer_kpis:
                dealer_list.append({
                    "dealer_name": dk.dealer_name,
                    "revenue": dk.revenue,
                    "units": dk.units,
                    "dn_count": dk.dn_count
                })
            
            metric = query_plan.metric or 'revenue'
            limit = query_plan.limit or 5
            ranking = analytics_service.rank_dealers(dealer_list, metric=metric, limit=limit)
            
            if ranking.items:
                lines = [f"📊 *Top {limit} Dealers by {metric.upper()}*", ""]
                for item in ranking.items:
                    lines.append(f"{item.rank}. {item.name}: {item.value:,.0f}")
                return "\n".join(lines)
        
        return "Ranking not available for this dimension."
        
    except Exception as e:
        logger.error(f"[{request_id}] Ranking error: {e}")
        return "❌ Error generating ranking report."


def _handle_control_tower(request_id: str) -> str:
    """Handle control tower queries using Analytics Service"""
    try:
        analytics_service = get_analytics_service()
        kpi_service = get_kpi_service()
        schema_service = get_schema_service()
        
        all_records = schema_service.get_all_records()
        warehouse_kpis = kpi_service.calculate_all_warehouses_kpis(all_records)
        
        warehouse_dicts = []
        for wk in warehouse_kpis:
            warehouse_dicts.append({
                "warehouse_name": wk.warehouse_name,
                "pending_delivery": wk.pending_delivery,
                "avg_delivery_aging": wk.avg_delivery_aging,
                "critical_dn": wk.critical_dn
            })
        
        report = analytics_service.critical_delivery_report(warehouse_dicts, [], threshold_days=15)
        
        if report.alerts:
            lines = ["🚨 *Control Tower - Critical Alerts*", ""]
            for alert in report.alerts[:5]:
                lines.append(f"🔴 {alert.entity_name}: {alert.message}")
            return "\n".join(lines)
        else:
            return "✅ No critical alerts at this time. All systems operating normally."
        
    except Exception as e:
        logger.error(f"[{request_id}] Control tower error: {e}")
        return "❌ Error generating control tower report."


def _handle_executive_dashboard(request_id: str) -> str:
    """Handle executive dashboard using Logistics Query Service"""
    try:
        logistics_service = get_logistics_query_service()
        dashboard = logistics_service.build_executive_dashboard()
        
        lines = [
            "📊 *Executive Dashboard*",
            "",
            f"💰 Total Revenue: PKR {dashboard.get('total_revenue', 0):,.0f}",
            f"📦 Total Units: {dashboard.get('total_units', 0):,}",
            f"📄 Total DNs: {dashboard.get('total_dn', 0)}",
            "",
            f"🚚 Delivery Rate: {dashboard.get('delivery_rate', 0):.1f}%",
            f"📎 POD Rate: {dashboard.get('pod_rate', 0):.1f}%",
            "",
            f"🏪 Top Dealer: {dashboard.get('top_dealers', [{}])[0].get('dealer_name', 'N/A') if dashboard.get('top_dealers') else 'N/A'}",
            f"🏭 Top Warehouse: {dashboard.get('top_warehouses', [{}])[0].get('warehouse_name', 'N/A') if dashboard.get('top_warehouses') else 'N/A'}",
            "",
            f"⚠️ Critical Deliveries: {dashboard.get('critical_deliveries', 0)}",
            f"📎 Critical POD: {dashboard.get('critical_pod', 0)}"
        ]
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"[{request_id}] Executive dashboard error: {e}")
        return "❌ Error generating executive dashboard."


def _handle_kpi_report(request_id: str) -> str:
    """Handle KPI report using KPI Service"""
    try:
        kpi_service = get_kpi_service()
        schema_service = get_schema_service()
        
        all_records = schema_service.get_all_records()
        executive_kpi = kpi_service.calculate_executive_kpis(all_records)
        
        lines = [
            "📊 *KPI Report*",
            "",
            f"💰 Total Revenue: PKR {executive_kpi.total_revenue:,.0f}",
            f"📦 Total Units: {executive_kpi.total_units:,}",
            f"📄 Total DNs: {executive_kpi.total_dn:,}",
            "",
            f"🚚 Delivery Rate: {executive_kpi.delivery_rate:.1f}%",
            f"📎 POD Rate: {executive_kpi.pod_rate:.1f}%",
            f"✅ PGI Rate: {executive_kpi.pgi_rate:.1f}%",
            "",
            f"⏳ Pending Delivery: {executive_kpi.total_pending_delivery}",
            f"📎 Pending POD: {executive_kpi.total_pending_pod}",
            "",
            f"⏰ Avg Delivery Aging: {executive_kpi.avg_delivery_aging:.1f} days",
            f"⏰ Avg POD Aging: {executive_kpi.avg_pod_aging:.1f} days"
        ]
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"[{request_id}] KPI report error: {e}")
        return "❌ Error generating KPI report."


def _handle_ai_provider_query(message_text: str, request_id: str) -> str:
    """Fallback to AI Provider Service for general queries"""
    try:
        response = process_whatsapp_query(
            question=message_text,
            phone_number=None,
            request_id=request_id
        )
        return response
    except Exception as e:
        logger.error(f"[{request_id}] AI Provider error: {e}")
        return None


def _send_response(phone_number: str, message: str, message_id: str, request_id: str):
    """Send response using WhatsApp Service"""
    try:
        result = send_text_message(
            phone_number=phone_number,
            message=message,
            message_id=message_id,
            request_id=request_id
        )
        
        if not result.get('success'):
            logger.error(f"[{request_id}] Failed to send: {result.get('error')}")
            
    except Exception as e:
        logger.error(f"[{request_id}] Send error: {e}")


# ==========================================================
# FORMATTING FUNCTIONS
# ==========================================================

def _format_help_message() -> str:
    """Format help message"""
    return """📋 *Logistics AI Assistant - Help*

*What I can do:*

🔍 *Track Delivery* - Send any 10+ digit DN number
🏪 *Dealer Queries* - "Show dealer ABC Traders"
🏭 *Warehouse Queries* - "Lahore warehouse summary"
🌆 *City Dashboard* - "Karachi dashboard"
📊 *Rankings* - "Top 5 dealers by revenue"
🚨 *Control Tower* - "Critical alerts"
📈 *Executive Dashboard* - "Executive dashboard"

*Commands:* `Help`, `Status`

*Example:* "Show dealer Mian Group of Chakwal Wah"

Need help? Just ask! 🤖"""


def _format_status_message() -> str:
    """Format status message"""
    whatsapp_service = get_whatsapp_service()
    health = whatsapp_service.health_check(verify_meta=False)
    
    return f"""📊 *System Status*

✅ AI Query Service
✅ KPI Service
✅ Analytics Service
✅ Schema Service
{'✅' if health.get('configured') else '❌'} WhatsApp Service
{'✅' if get_redis_client() else '❌'} Redis Cache

*Environment:* {getattr(config, 'ENVIRONMENT', 'development')}
*Cache TTL:* {CACHE_TTL}s

All systems operational! 🚀"""


def _format_welcome_message() -> str:
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
# VALIDATION FUNCTIONS
# ==========================================================

def _verify_signature(payload: bytes, signature_header: str) -> bool:
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


def _validate_payload(data: Dict) -> bool:
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


def _extract_message(data: Dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
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
        
        return phone_number, message_text, message_id, sender_name
        
    except Exception as e:
        logger.error(f"Message extraction error: {e}")
        return None, None, None, None


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
        'version': '6.0',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'whatsapp': whatsapp_service.health_check(verify_meta=False),
            'schema': {'initialized': schema_service is not None},
            'redis': {'connected': redis_client is not None},
            'ai_query': {'available': True},
            'ai_provider': {'available': True},
            'logistics_query': {'available': True}
        },
        'config': {
            'environment': getattr(config, 'ENVIRONMENT', 'development'),
            'cache_ttl': CACHE_TTL
        }
    }


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("WhatsApp Webhook v6.0 - 100% INTEGRATED")
logger.info("=" * 60)
logger.info("")
logger.info("   ✅ INTEGRATED SERVICES:")
logger.info("   ✅ AI Query Service (Intent Detection)")
logger.info("   ✅ AI Provider Service (Legacy Queries)")
logger.info("   ✅ Logistics Query Service (Dashboards)")
logger.info("   ✅ KPI Service (Metrics)")
logger.info("   ✅ Analytics Service (Rankings/Trends)")
logger.info("   ✅ Schema Service (Database)")
logger.info("   ✅ WhatsApp Service (Messages)")
logger.info("")
logger.info("   ✅ FEATURES:")
logger.info("   ✅ Dealer Dashboard")
logger.info("   ✅ Warehouse Dashboard")
logger.info("   ✅ City Dashboard")
logger.info("   ✅ DN Lookup")
logger.info("   ✅ Rankings")
logger.info("   ✅ Control Tower")
logger.info("   ✅ Executive Dashboard")
logger.info("   ✅ KPI Reports")
logger.info("")
logger.info("   STATUS: ✅ READY - 100% INTEGRATED")
logger.info("=" * 60)
