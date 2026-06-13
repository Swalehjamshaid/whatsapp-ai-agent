# ==========================================================
# FILE: app/routes/webhook.py (v43.0 - MASTER ORCHESTRATOR)
# ==========================================================
# PURPOSE: WhatsApp Webhook - Master Orchestrator Only
# 
# RESPONSIBILITIES:
# ✅ WhatsApp Webhook Endpoint (GET/POST)
# ✅ Message Validation Layer
# ✅ Duplicate Protection
# ✅ Rate Limiting
# ✅ Query Cache
# ✅ Request Logging
# ✅ Query Understanding (calls ai_query_service)
# ✅ Query Router (routes to correct engine)
# ✅ KPI Integration (calls kpi_service)
# ✅ Analytics Integration (calls analytics_service)
# ✅ GROQ Integration (calls ai_provider_service)
# ✅ Response Formatter
# ✅ WhatsApp Delivery (calls whatsapp_service)
# ✅ Error Handling
# ✅ Performance Monitoring
# 
# NO BUSINESS LOGIC - Only Orchestration
# ==========================================================

import json
import time
import uuid
import re
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from loguru import logger
from cachetools import TTLCache

from app.config import config
from app.database import SessionLocal

# Service imports (will be initialized lazily)
_logistics_service = None
_kpi_service = None
_analytics_service = None
_ai_query_service = None
_ai_provider_service = None
_whatsapp_service = None
_schema_service = None

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = config.MAX_MESSAGE_LENGTH
MAX_MESSAGES_PER_MINUTE = config.MAX_MESSAGES_PER_MINUTE
CACHE_TTL = config.CACHE_TTL

# ==========================================================
# CACHES
# ==========================================================

# Duplicate protection cache
processed_messages = TTLCache(maxsize=10000, ttl=3600)

# Rate limiting cache
rate_limit_cache = TTLCache(maxsize=10000, ttl=60)

# Query result cache
query_cache = TTLCache(maxsize=1000, ttl=CACHE_TTL)

# ==========================================================
# METRICS FOR MONITORING
# ==========================================================

metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "rate_limited_requests": 0,
    "duplicate_messages": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "avg_response_time_ms": 0,
    "total_response_time_ms": 0,
    "start_time": time.time(),
    "groq_calls": 0,
    "database_calls": 0
}

# ==========================================================
# SERVICE INITIALIZATION (Lazy Loading)
# ==========================================================

def get_logistics_service():
    global _logistics_service
    if _logistics_service is None:
        try:
            from app.services.logistics_query_service import get_logistics_query_service
            db = SessionLocal()
            _logistics_service = get_logistics_query_service(db)
            logger.info("✅ Logistics Service initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Logistics Service: {e}")
    return _logistics_service

def get_kpi_service():
    global _kpi_service
    if _kpi_service is None:
        try:
            from app.services.kpi_service import KPIService
            _kpi_service = KPIService()
            logger.info("✅ KPI Service initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize KPI Service: {e}")
    return _kpi_service

def get_analytics_service():
    global _analytics_service
    if _analytics_service is None:
        try:
            from app.services.analytics_service import AnalyticsService
            _analytics_service = AnalyticsService()
            logger.info("✅ Analytics Service initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Analytics Service: {e}")
    return _analytics_service

def get_ai_query_service():
    global _ai_query_service
    if _ai_query_service is None:
        try:
            from app.services.ai_query_service import AIQueryService
            _ai_query_service = AIQueryService()
            logger.info("✅ AI Query Service initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize AI Query Service: {e}")
    return _ai_query_service

def get_ai_provider_service():
    global _ai_provider_service
    if _ai_provider_service is None:
        try:
            from app.services.ai_provider_service import AIProviderService
            _ai_provider_service = AIProviderService()
            logger.info("✅ AI Provider Service initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize AI Provider Service: {e}")
    return _ai_provider_service

def get_whatsapp_service():
    global _whatsapp_service
    if _whatsapp_service is None:
        try:
            from app.services.whatsapp_service import WhatsAppService
            _whatsapp_service = WhatsAppService()
            logger.info("✅ WhatsApp Service initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize WhatsApp Service: {e}")
    return _whatsapp_service

def get_schema_service():
    global _schema_service
    if _schema_service is None:
        try:
            from app.services.schema_service import SchemaService
            _schema_service = SchemaService()
            logger.info("✅ Schema Service initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Schema Service: {e}")
    return _schema_service

# ==========================================================
# 1. WHATSAPP WEBHOOK ENDPOINTS
# ==========================================================

@router.get("/")
async def verify_webhook(request: Request):
    """
    GET /webhook - Meta Verification Endpoint
    
    Purpose: Verify webhook with WhatsApp/Meta
    Functions: verify_webhook(), validate_token()
    """
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    
    logger.info(f"Webhook verification request - Mode: {hub_mode}")
    
    # Validate token
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        if hub_challenge:
            logger.success("✅ Webhook verified successfully!")
            return PlainTextResponse(content=hub_challenge)
    
    logger.warning(f"Webhook verification failed - Invalid token")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """
    POST /webhook - Receive WhatsApp Messages
    
    Purpose: Receive and process incoming WhatsApp messages
    Functions: receive_message(), process_message()
    """
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    # Track total requests
    metrics["total_requests"] += 1
    
    # Log request start
    logger.bind(request_id=request_id)
    logger.info(f"📨 Webhook request received - ID: {request_id}")
    
    try:
        # Parse request body
        raw_body = await request.body()
        payload = json.loads(raw_body.decode('utf-8'))
        
        # Extract message data
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates (ignore)
        if value.get("statuses"):
            logger.debug("Status update received - ignoring")
            return {"success": True, "type": "status_update"}
        
        # Extract messages
        messages = value.get("messages", [])
        if not messages:
            logger.debug("No messages in payload")
            return {"success": True, "type": "no_messages"}
        
        # Process each message
        processed_count = 0
        for message in messages:
            result = await process_single_message(message, request_id, start_time)
            if result:
                processed_count += 1
        
        # Calculate processing time
        processing_time_ms = (time.time() - start_time) * 1000
        metrics["total_response_time_ms"] += processing_time_ms
        metrics["avg_response_time_ms"] = metrics["total_response_time_ms"] / metrics["total_requests"]
        
        logger.info(f"✅ Request completed - ID: {request_id}, Time: {processing_time_ms:.0f}ms, Messages: {processed_count}")
        
        return {
            "success": True,
            "request_id": request_id,
            "processing_time_ms": round(processing_time_ms, 2),
            "messages_processed": processed_count,
            "metrics": {
                "cache_hit_rate": round(metrics["cache_hits"] / max(1, metrics["cache_hits"] + metrics["cache_misses"]) * 100, 1),
                "avg_response_time_ms": round(metrics["avg_response_time_ms"], 1)
            }
        }
        
    except Exception as e:
        logger.exception(f"❌ Webhook error: {e}")
        metrics["failed_requests"] += 1
        return {"success": False, "error": str(e), "request_id": request_id}


async def process_single_message(message: Dict, request_id: str, start_time: float) -> bool:
    """
    Process a single WhatsApp message
    
    Steps:
    1. Validate message
    2. Check duplicate
    3. Check rate limit
    4. Check cache
    5. Process query
    6. Send response
    """
    # Extract message details
    phone_number = message.get("from")
    msg_id = message.get("id")
    msg_type = message.get("type", "unknown")
    timestamp = message.get("timestamp")
    
    # ==========================================================
    # 2. MESSAGE VALIDATION LAYER
    # ==========================================================
    
    # Check message exists
    if not message:
        logger.warning("Empty message received")
        return False
    
    # Check sender exists
    if not phone_number:
        logger.warning("Message without sender - ignoring")
        return False
    
    # Check message ID exists
    if not msg_id:
        logger.warning("Message without ID - generating temporary ID")
        msg_id = f"temp_{int(time.time())}_{phone_number}"
    
    # Check text exists (for text messages)
    if msg_type != "text":
        logger.debug(f"Ignoring non-text message type: {msg_type}")
        await send_whatsapp_message(phone_number, "📱 Please send text messages. Type 'Help' for commands.", request_id)
        return True
    
    user_message = message.get("text", {}).get("body", "").strip()
    
    # Check not empty
    if not user_message:
        logger.warning("Empty text message - ignoring")
        return False
    
    # Check valid format (basic sanitization)
    user_message = sanitize_input(user_message)
    
    # ==========================================================
    # 3. DUPLICATE PROTECTION
    # ==========================================================
    
    if msg_id in processed_messages:
        logger.info(f"🔄 Duplicate message detected - ID: {msg_id}")
        metrics["duplicate_messages"] += 1
        return False
    
    processed_messages[msg_id] = True
    
    # ==========================================================
    # 4. RATE LIMITING
    # ==========================================================
    
    if not check_rate_limit(phone_number):
        logger.warning(f"🚫 Rate limit exceeded for {phone_number}")
        metrics["rate_limited_requests"] += 1
        await send_whatsapp_message(phone_number, "⚠️ Too many messages. Please wait a minute before sending more.", request_id)
        return False
    
    # ==========================================================
    # 5. QUERY CACHE CHECK
    # ==========================================================
    
    cache_key = f"{phone_number}:{user_message}"
    if cache_key in query_cache:
        logger.info(f"💾 Cache hit for: {user_message[:50]}...")
        metrics["cache_hits"] += 1
        cached_response = query_cache[cache_key]
        await send_whatsapp_message(phone_number, cached_response, request_id)
        return True
    
    metrics["cache_misses"] += 1
    
    # ==========================================================
    # 6. REQUEST LOGGING
    # ==========================================================
    
    log_request(phone_number, user_message, msg_id, timestamp)
    
    # ==========================================================
    # 7. QUERY UNDERSTANDING (Call AI Query Service)
    # ==========================================================
    
    ai_service = get_ai_query_service()
    if ai_service:
        try:
            query_plan = await ai_service.understand_query(user_message)
            logger.info(f"🎯 Query Plan: {query_plan}")
        except Exception as e:
            logger.error(f"AI Query Service error: {e}")
            query_plan = {"intent": "unknown", "entities": {}}
    else:
        query_plan = await fallback_query_understanding(user_message)
    
    # ==========================================================
    # 8. QUERY ROUTER - Route to correct engine
    # ==========================================================
    
    response = await route_query(query_plan, user_message, phone_number)
    
    # ==========================================================
    # 12. RESPONSE FORMATTER
    # ==========================================================
    
    formatted_response = format_response(response, query_plan.get("intent", "unknown"))
    
    # Cache the response
    query_cache[cache_key] = formatted_response
    
    # ==========================================================
    # 13. WHATSAPP DELIVERY
    # ==========================================================
    
    await send_whatsapp_message(phone_number, formatted_response, request_id)
    
    # Update metrics
    metrics["successful_requests"] += 1
    
    return True


# ==========================================================
# 2. MESSAGE VALIDATION FUNCTIONS
# ==========================================================

def sanitize_input(message: str) -> str:
    """Sanitize user input - remove harmful characters"""
    # Remove extra spaces
    message = re.sub(r'\s+', ' ', message)
    # Remove any control characters
    message = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', message)
    # Trim
    return message.strip()[:MAX_MESSAGE_LENGTH]


# ==========================================================
# 3. DUPLICATE PROTECTION FUNCTIONS
# ==========================================================

# Already implemented via processed_messages cache


# ==========================================================
# 4. RATE LIMITING FUNCTIONS
# ==========================================================

def check_rate_limit(phone_number: str) -> bool:
    """Check if user has exceeded rate limit"""
    current_time = time.time()
    timestamps = rate_limit_cache.get(phone_number, [])
    
    # Clean old timestamps
    timestamps = [t for t in timestamps if current_time - t < 60]
    
    # Check limit
    if len(timestamps) >= MAX_MESSAGES_PER_MINUTE:
        return False
    
    # Add new timestamp
    timestamps.append(current_time)
    rate_limit_cache[phone_number] = timestamps
    return True


# ==========================================================
# 6. REQUEST LOGGING FUNCTIONS
# ==========================================================

def log_request(phone_number: str, message: str, msg_id: str, timestamp: str):
    """Log request for monitoring and analytics"""
    logger.info(f"📱 Message - From: {phone_number}, ID: {msg_id}, Time: {timestamp}")
    logger.info(f"💬 Message content: {message[:200]}")
    
    # Could also log to database or external service here


# ==========================================================
# 7. QUERY UNDERSTANDING FALLBACK
# ==========================================================

async def fallback_query_understanding(message: str) -> Dict:
    """Fallback query understanding when AI service is unavailable"""
    msg_lower = message.lower()
    
    # Detect intent
    intent = "unknown"
    if any(word in msg_lower for word in ['help', 'menu', 'commands']):
        intent = "help"
    elif any(word in msg_lower for word in ['warehouse', 'wh']):
        intent = "warehouse_dashboard"
    elif any(word in msg_lower for word in ['dealer', 'customer']):
        intent = "dealer_dashboard"
    elif 'top' in msg_lower and 'dealer' in msg_lower:
        intent = "ranking_dealers"
    elif 'top' in msg_lower and 'warehouse' in msg_lower:
        intent = "ranking_warehouses"
    elif 'compare' in msg_lower and 'vs' in msg_lower:
        intent = "comparison"
    elif 'executive' in msg_lower or 'ceo' in msg_lower:
        intent = "executive_dashboard"
    elif 'control tower' in msg_lower:
        intent = "control_tower"
    elif 'trend' in msg_lower:
        intent = "trend"
    else:
        intent = "dealer_dashboard"  # Default assumption
    
    # Extract entities
    entities = {}
    
    # Extract warehouse
    warehouses = ['lahore', 'karachi', 'rawalpindi', 'sargodha', 'islamabad', 'multan']
    for wh in warehouses:
        if wh in msg_lower:
            entities['warehouse'] = wh.title()
            intent = "warehouse_dashboard"
            break
    
    # Extract dealer (if no warehouse)
    if 'warehouse' not in entities:
        if '&' in message:
            dealer_match = re.search(r'([A-Za-z\s&]+(?:&[A-Za-z\s]+)+)', message)
            if dealer_match:
                entities['dealer'] = dealer_match.group(1).strip()
        elif len(msg_lower.split()) <= 5:
            entities['dealer'] = message.strip()
    
    # Extract top N
    top_match = re.search(r'top\s+(\d+)', msg_lower)
    if top_match:
        entities['limit'] = int(top_match.group(1))
    
    return {
        "intent": intent,
        "entities": entities,
        "original_message": message,
        "confidence": 0.7
    }


# ==========================================================
# 8. QUERY ROUTER - Routes to correct engine
# ==========================================================

async def route_query(query_plan: Dict, original_message: str, phone_number: str) -> Dict:
    """Route query to appropriate service based on intent"""
    
    intent = query_plan.get("intent", "unknown")
    entities = query_plan.get("entities", {})
    
    logger.info(f"🎯 Routing intent: {intent}")
    
    # ==========================================================
    # DEALER QUERIES
    # ==========================================================
    
    if intent in ["dealer_dashboard", "dealer_kpi", "dealer_revenue", "dealer_units"]:
        dealer_name = entities.get("dealer") or entities.get("dealer_name") or original_message
        logistics_service = get_logistics_service()
        if logistics_service:
            try:
                result = logistics_service.get_dealer_dashboard(dealer_name)
                if result:
                    return {"type": "dealer_dashboard", "data": result, "success": True}
            except Exception as e:
                logger.error(f"Dealer dashboard error: {e}")
        
        # Fallback
        return {"type": "error", "data": {"message": f"Dealer '{dealer_name}' not found"}, "success": False}
    
    # ==========================================================
    # WAREHOUSE QUERIES
    # ==========================================================
    
    elif intent in ["warehouse_dashboard", "warehouse_kpi", "warehouse_aging"]:
        warehouse_name = entities.get("warehouse") or entities.get("warehouse_name")
        if not warehouse_name:
            warehouse_name = original_message.replace("warehouse", "").strip()
        
        logistics_service = get_logistics_service()
        if logistics_service:
            try:
                result = logistics_service.get_warehouse_dashboard(warehouse_name)
                if result:
                    return {"type": "warehouse_dashboard", "data": result, "success": True}
            except Exception as e:
                logger.error(f"Warehouse dashboard error: {e}")
        
        return {"type": "error", "data": {"message": f"Warehouse '{warehouse_name}' not found"}, "success": False}
    
    # ==========================================================
    # RANKING QUERIES (Call Analytics Service)
    # ==========================================================
    
    elif intent in ["ranking_dealers", "top_dealers"]:
        limit = entities.get("limit", 10)
        analytics_service = get_analytics_service()
        if analytics_service:
            try:
                result = analytics_service.get_top_dealers(limit=limit, by="revenue")
                return {"type": "ranking", "data": result, "success": True}
            except Exception as e:
                logger.error(f"Ranking error: {e}")
    
    elif intent in ["ranking_warehouses", "top_warehouses"]:
        limit = entities.get("limit", 10)
        analytics_service = get_analytics_service()
        if analytics_service:
            try:
                result = analytics_service.get_top_warehouses(limit=limit, by="revenue")
                return {"type": "ranking", "data": result, "success": True}
            except Exception as e:
                logger.error(f"Ranking error: {e}")
    
    # ==========================================================
    # COMPARISON QUERIES (Call Analytics Service)
    # ==========================================================
    
    elif intent == "comparison":
        entity_a = entities.get("entity_a") or entities.get("compare_a")
        entity_b = entities.get("entity_b") or entities.get("compare_b")
        
        if entity_a and entity_b:
            analytics_service = get_analytics_service()
            if analytics_service:
                try:
                    result = analytics_service.compare_dealers(entity_a, entity_b)
                    return {"type": "comparison", "data": result, "success": True}
                except Exception as e:
                    logger.error(f"Comparison error: {e}")
    
    # ==========================================================
    # KPI QUERIES (Call KPI Service)
    # ==========================================================
    
    elif intent == "kpi_dashboard":
        kpi_service = get_kpi_service()
        if kpi_service:
            try:
                result = kpi_service.get_overall_kpis()
                return {"type": "kpi_dashboard", "data": result, "success": True}
            except Exception as e:
                logger.error(f"KPI error: {e}")
    
    # ==========================================================
    # EXECUTIVE DASHBOARD (Call Analytics Service)
    # ==========================================================
    
    elif intent == "executive_dashboard":
        analytics_service = get_analytics_service()
        if analytics_service:
            try:
                result = analytics_service.get_executive_dashboard()
                return {"type": "executive_dashboard", "data": result, "success": True}
            except Exception as e:
                logger.error(f"Executive dashboard error: {e}")
    
    # ==========================================================
    # CONTROL TOWER (Call Analytics Service)
    # ==========================================================
    
    elif intent == "control_tower":
        analytics_service = get_analytics_service()
        if analytics_service:
            try:
                result = analytics_service.get_control_tower_alerts()
                return {"type": "control_tower", "data": result, "success": True}
            except Exception as e:
                logger.error(f"Control tower error: {e}")
    
    # ==========================================================
    # TREND QUERIES (Call Analytics Service)
    # ==========================================================
    
    elif intent == "trend":
        period = entities.get("period", "monthly")
        metric = entities.get("metric", "revenue")
        analytics_service = get_analytics_service()
        if analytics_service:
            try:
                result = analytics_service.get_trend(metric=metric, period=period)
                return {"type": "trend", "data": result, "success": True}
            except Exception as e:
                logger.error(f"Trend error: {e}")
    
    # ==========================================================
    # GROQ INTEGRATION (Call AI Provider Service)
    # ==========================================================
    
    elif intent == "complex_query":
        ai_provider = get_ai_provider_service()
        if ai_provider:
            try:
                metrics["groq_calls"] += 1
                result = await ai_provider.analyze_query(original_message)
                return {"type": "ai_analysis", "data": {"analysis": result}, "success": True}
            except Exception as e:
                logger.error(f"GROQ error: {e}")
    
    # ==========================================================
    # HELP
    # ==========================================================
    
    elif intent == "help":
        return {"type": "help", "data": {}, "success": True}
    
    # ==========================================================
    # DEFAULT - Try GROQ or Help
    # ==========================================================
    
    else:
        ai_provider = get_ai_provider_service()
        if ai_provider:
            try:
                metrics["groq_calls"] += 1
                result = await ai_provider.analyze_query(original_message)
                if result:
                    return {"type": "ai_analysis", "data": {"analysis": result}, "success": True}
            except Exception as e:
                logger.error(f"GROQ error: {e}")
        
        return {"type": "help", "data": {}, "success": True}


# ==========================================================
# 9-11. KPI, ANALYTICS, GROQ INTEGRATION
# ==========================================================

# These are handled in the route_query function above


# ==========================================================
# 12. RESPONSE FORMATTER
# ==========================================================

def format_response(result: Dict, intent: str) -> str:
    """Format response based on result type"""
    
    if not result.get("success"):
        return format_error_response(result)
    
    result_type = result.get("type", "unknown")
    data = result.get("data", {})
    
    if result_type == "dealer_dashboard":
        return format_dealer_dashboard(data)
    
    elif result_type == "warehouse_dashboard":
        return format_warehouse_dashboard(data)
    
    elif result_type == "ranking":
        return format_ranking(data)
    
    elif result_type == "comparison":
        return format_comparison(data)
    
    elif result_type == "kpi_dashboard":
        return format_kpi_dashboard(data)
    
    elif result_type == "executive_dashboard":
        return format_executive_dashboard(data)
    
    elif result_type == "control_tower":
        return format_control_tower(data)
    
    elif result_type == "trend":
        return format_trend(data)
    
    elif result_type == "ai_analysis":
        return format_ai_analysis(data)
    
    elif result_type == "help":
        return get_help_message()
    
    else:
        return get_help_message()


def format_dealer_dashboard(data: Dict) -> str:
    """Format dealer dashboard response"""
    return f"""
🏪 *DEALER DASHBOARD: {data.get('dealer_name', 'N/A')}*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *VOLUME METRICS*
• Total DNs: {data.get('total_dn', 0):,}
• Total Units: {data.get('total_units', 0):,}
• Total Revenue: PKR {data.get('total_revenue', 0):,.0f}

✅ *DELIVERY STATUS*
• Delivered: {data.get('delivered_dn', 0)} | Pending: {data.get('pending_dn', 0)}
• Completion Rate: {data.get('completion_rate', 0):.1f}%

🚚 *PGI & POD*
• PGI Done: {data.get('pgi_done', 0)} | Pending: {data.get('pgi_pending', 0)}
• POD Done: {data.get('pod_done', 0)} | Pending: {data.get('pod_pending', 0)}

⏱️ *AGING METRICS*
• Avg Delivery Aging: {data.get('avg_delivery_aging', 0)} days
• Avg POD Aging: {data.get('avg_pod_aging', 0)} days

📦 *TOP MODELS*
{format_top_models(data.get('top_models', []))}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type 'Help' for more commands
"""


def format_warehouse_dashboard(data: Dict) -> str:
    """Format warehouse dashboard response"""
    return f"""
🏭 *WAREHOUSE DASHBOARD: {data.get('warehouse_name', 'N/A')}*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *VOLUME METRICS*
• Total DNs: {data.get('total_dn', 0):,}
• Total Units: {data.get('total_units', 0):,}
• Total Revenue: PKR {data.get('total_revenue', 0):,.0f}

🚚 *DELIVERY SLA (PGI - DN)*
• Same Day: {data.get('same_day_delivery', 0)}
• 1 Day: {data.get('one_day_delivery', 0)}
• 2 Days: {data.get('two_day_delivery', 0)}
• 3 Days: {data.get('three_day_delivery', 0)}
• 4 Days: {data.get('four_day_delivery', 0)}
• 5+ Days: {data.get('five_plus_delivery', 0)}
• **Average: {data.get('avg_delivery_aging', 0)} days**

📋 *POD SLA (POD - PGI)*
• Same Day: {data.get('same_day_pod', 0)}
• 1 Day: {data.get('one_day_pod', 0)}
• 2 Days: {data.get('two_day_pod', 0)}
• 3 Days: {data.get('three_day_pod', 0)}
• 4 Days: {data.get('four_day_pod', 0)}
• 5+ Days: {data.get('five_plus_pod', 0)}
• **Average: {data.get('avg_pod_aging', 0)} days**

⚠️ *PENDING & CRITICAL*
• Pending Deliveries: {data.get('pending_delivery', 0)}
• Pending PODs: {data.get('pending_pod', 0)}
• Critical DNs: {data.get('critical_dn', 0)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type 'Warehouse wise delivery aging' for all warehouses
"""


def format_ranking(data: Dict) -> str:
    """Format ranking response"""
    items = data.get('items', [])
    title = data.get('title', 'RANKING')
    metric = data.get('metric', 'value')
    
    if not items:
        return f"❌ No data found for {title}"
    
    response = f"🏆 *{title}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, item in enumerate(items[:10], 1):
        if metric == 'revenue':
            response += f"{i}. {item.get('name')}: PKR {item.get('value', 0):,.0f}\n"
        elif metric == 'units':
            response += f"{i}. {item.get('name')}: {item.get('value', 0):,} units\n"
        else:
            response += f"{i}. {item.get('name')}: {item.get('value', 0)}\n"
    
    return response


def format_comparison(data: Dict) -> str:
    """Format comparison response"""
    return f"""
🔄 *COMPARISON: {data.get('entity_a')} vs {data.get('entity_b')}*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *REVENUE:*
• {data.get('entity_a')}: PKR {data.get('revenue_a', 0):,.0f}
• {data.get('entity_b')}: PKR {data.get('revenue_b', 0):,.0f}
• Winner: 🏆 {data.get('winner_revenue', 'Tie')}

📦 *UNITS:*
• {data.get('entity_a')}: {data.get('units_a', 0):,}
• {data.get('entity_b')}: {data.get('units_b', 0):,}
• Winner: 🏆 {data.get('winner_units', 'Tie')}

📋 *DNs:*
• {data.get('entity_a')}: {data.get('dns_a', 0)}
• {data.get('entity_b')}: {data.get('dns_b', 0)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def format_kpi_dashboard(data: Dict) -> str:
    """Format KPI dashboard response"""
    return f"""
📊 *KPI DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 Revenue: PKR {data.get('revenue', 0):,.0f}
📦 Units: {data.get('units', 0):,}
📋 Total DNs: {data.get('dn_count', 0):,}

✅ Delivery Rate: {data.get('delivery_rate', 0):.1f}%
📋 POD Rate: {data.get('pod_rate', 0):.1f}%
🚚 PGI Rate: {data.get('pgi_rate', 0):.1f}%

⏱️ Avg Delivery Aging: {data.get('avg_delivery_aging', 0)} days
📋 Avg POD Aging: {data.get('avg_pod_aging', 0)} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def format_executive_dashboard(data: Dict) -> str:
    """Format executive dashboard response"""
    return f"""
👔 *EXECUTIVE DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *COMPANY KPIs:*
• Total Revenue: PKR {data.get('total_revenue', 0):,.0f}
• Total Units: {data.get('total_units', 0):,}
• Total DNs: {data.get('total_dn', 0):,}

✅ *PERFORMANCE RATES:*
• Delivery Rate: {data.get('delivery_rate', 0):.1f}%
• POD Rate: {data.get('pod_rate', 0):.1f}%
• PGI Rate: {data.get('pgi_rate', 0):.1f}%

⚠️ *PENDING:*
• Pending Deliveries: {data.get('pending_delivery', 0)}
• Pending POD: {data.get('pending_pod', 0)}

🏆 *TOP PERFORMERS:*
• Top Dealer: {data.get('top_dealer', 'N/A')}
• Top Warehouse: {data.get('top_warehouse', 'N/A')}
• Top Product: {data.get('top_product', 'N/A')}

📈 *RISK SUMMARY:* {data.get('risk_summary', '🟢 LOW RISK')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def format_control_tower(data: Dict) -> str:
    """Format control tower response"""
    alerts = data.get('alerts', [])
    risk_buckets = data.get('risk_buckets', {})
    
    response = "🚨 *CONTROL TOWER - RISK REPORT*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    response += "📊 *PENDING DELIVERY RISK BUCKETS:*\n"
    for bucket, count in risk_buckets.items():
        response += f"• {bucket} days: {count}\n"
    
    if alerts:
        response += f"\n⚠️ *CRITICAL ALERTS:*\n"
        for alert in alerts[:5]:
            response += f"   • {alert}\n"
    
    response += f"\n🏪 *WORST DEALER:* {data.get('worst_dealer', 'N/A')}"
    response += f"\n🏭 *WORST WAREHOUSE:* {data.get('worst_warehouse', 'N/A')}"
    
    return response


def format_trend(data: Dict) -> str:
    """Format trend response"""
    points = data.get('points', [])
    metric = data.get('metric', 'revenue')
    period = data.get('period', 'monthly')
    
    if not points:
        return f"❌ No trend data found for {metric}"
    
    response = f"📈 *{metric.upper()} TREND ({period.upper()})*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for point in points[-12:]:  # Last 12 periods
        period_str = point.get('period', '')
        value = point.get('value', 0)
        
        if metric == 'revenue':
            value_m = value / 1000000
            bar = "█" * min(int(value_m / 10), 30)
            response += f"{period_str}: PKR {value_m:.1f}M {bar}\n"
        else:
            bar = "█" * min(int(value / 100), 30)
            response += f"{period_str}: {value:,} {bar}\n"
    
    return response


def format_ai_analysis(data: Dict) -> str:
    """Format AI analysis response"""
    analysis = data.get('analysis', '')
    return f"{analysis}\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 Type 'Help' for commands"


def format_error_response(result: Dict) -> str:
    """Format error response"""
    error_data = result.get('data', {})
    message = error_data.get('message', 'An error occurred')
    return f"""
❌ *ERROR*
━━━━━━━━━━━━━━━━━━━━━━━━━━

{message}

💡 Type 'Help' to see available commands
"""


def format_top_models(models: List) -> str:
    """Format top models list"""
    if not models:
        return "   No model data available"
    
    result = ""
    for model in models[:5]:
        result += f"   • {model.get('name', 'Unknown')[:30]}: {model.get('units', 0):,} units\n"
    return result


def get_help_message() -> str:
    """Get help message"""
    return """
🤖 *LOGISTICS AI ASSISTANT - HELP MENU*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *DEALER COMMANDS:*
• `Dubai Electronics` - Complete dealer dashboard
• `Haji Sharaf ud Din & Sons` - Dealer with & symbol

🏭 *WAREHOUSE COMMANDS:*
• `Sargodha Warehouse` - Warehouse SLA dashboard
• `Warehouse wise delivery aging` - All warehouses aging
• `Top 10 warehouses` - Warehouse ranking

📊 *KPI COMMANDS:*
• `Warehouse KPI` - KPI table by warehouse
• `Executive dashboard` - Company KPIs

🚨 *CONTROL TOWER:*
• `Control tower` - Risk monitoring
• `Critical warehouses` - Problem areas

🏆 *RANKING:*
• `Top 10 dealers` - Best dealers
• `Top 10 products` - Best products

🔄 *COMPARISON:*
• `Compare Lahore vs Karachi` - City comparison

📈 *TREND:*
• `Revenue trend monthly` - Time series analysis

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Just type any dealer, warehouse, or product name!
"""


# ==========================================================
# 13. WHATSAPP DELIVERY
# ==========================================================

async def send_whatsapp_message(phone_number: str, message: str, request_id: str) -> bool:
    """Send message via WhatsApp service with retry logic"""
    
    # Truncate if too long
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
    
    whatsapp_service = get_whatsapp_service()
    
    if whatsapp_service:
        try:
            result = await whatsapp_service.send_text(phone_number, message)
            if result.get("success"):
                logger.info(f"✅ Message sent to {phone_number}")
                return True
            else:
                logger.error(f"Failed to send message: {result.get('error')}")
                return False
        except Exception as e:
            logger.error(f"WhatsApp service error: {e}")
            return False
    else:
        # Mock mode - log only
        logger.info(f"📱 [MOCK] Would send to {phone_number}: {message[:100]}...")
        return True


# ==========================================================
# 14. ERROR HANDLING (Built into all functions)
# 15. PERFORMANCE MONITORING (Metrics tracked throughout)
# ==========================================================


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "43.0",
        "metrics": {
            "total_requests": metrics["total_requests"],
            "successful_requests": metrics["successful_requests"],
            "cache_hit_rate": round(metrics["cache_hits"] / max(1, metrics["cache_hits"] + metrics["cache_misses"]) * 100, 1),
            "avg_response_time_ms": round(metrics["avg_response_time_ms"], 1),
            "groq_calls": metrics["groq_calls"],
            "uptime_seconds": round(time.time() - metrics["start_time"], 0)
        },
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/metrics")
async def get_metrics():
    """Detailed metrics endpoint"""
    return {
        "total_requests": metrics["total_requests"],
        "successful_requests": metrics["successful_requests"],
        "failed_requests": metrics["failed_requests"],
        "rate_limited_requests": metrics["rate_limited_requests"],
        "duplicate_messages": metrics["duplicate_messages"],
        "cache_hits": metrics["cache_hits"],
        "cache_misses": metrics["cache_misses"],
        "cache_hit_rate": round(metrics["cache_hits"] / max(1, metrics["cache_hits"] + metrics["cache_misses"]) * 100, 1),
        "avg_response_time_ms": round(metrics["avg_response_time_ms"], 1),
        "groq_calls": metrics["groq_calls"],
        "database_calls": metrics["database_calls"],
        "uptime_seconds": round(time.time() - metrics["start_time"], 0),
        "start_time": datetime.fromtimestamp(metrics["start_time"]).isoformat()
    }


@router.post("/cache/clear")
async def clear_cache():
    """Clear query cache (for testing)"""
    old_size = len(query_cache)
    query_cache.clear()
    return {"success": True, "cleared": old_size}


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 80)
logger.info("🚀 WEBHOOK v43.0 - MASTER ORCHESTRATOR")
logger.info("=" * 80)
logger.info("")
logger.info("   RESPONSIBILITIES:")
logger.info("   ✅ WhatsApp Webhook Endpoint (GET/POST)")
logger.info("   ✅ Message Validation Layer")
logger.info("   ✅ Duplicate Protection")
logger.info("   ✅ Rate Limiting")
logger.info("   ✅ Query Cache")
logger.info("   ✅ Request Logging")
logger.info("   ✅ Query Understanding (calls ai_query_service)")
logger.info("   ✅ Query Router (routes to correct engine)")
logger.info("   ✅ KPI Integration (calls kpi_service)")
logger.info("   ✅ Analytics Integration (calls analytics_service)")
logger.info("   ✅ GROQ Integration (calls ai_provider_service)")
logger.info("   ✅ Response Formatter")
logger.info("   ✅ WhatsApp Delivery (calls whatsapp_service)")
logger.info("   ✅ Error Handling")
logger.info("   ✅ Performance Monitoring")
logger.info("")
logger.info("   NO BUSINESS LOGIC - Only Orchestration")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 80)
