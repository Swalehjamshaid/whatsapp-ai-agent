# ==========================================================
# FILE: app/routes/webhook.py (v28.3 - FIXED INTEGER DN SEARCH)
# ==========================================================

import json
import time
import uuid
import re
import asyncio
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import text, or_, cast, String
from datetime import datetime, date
from loguru import logger
from cachetools import TTLCache

from app.config import config
from app.database import SessionLocal
from app.models import DeliveryReport

# Create router
router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = 3500
RATE_LIMIT_MAX_MESSAGES = 10
RATE_LIMIT_WINDOW = 60
AUTO_CLEANUP_INTERVAL = 500

# ==========================================================
# CACHES
# ==========================================================

processed_messages = TTLCache(maxsize=5000, ttl=3600)
rate_limit_cache = TTLCache(maxsize=10000, ttl=RATE_LIMIT_WINDOW)
dn_cache = TTLCache(maxsize=1000, ttl=3600)

# ==========================================================
# METRICS
# ==========================================================

metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "timeout_requests": 0,
    "rate_limited_requests": 0,
    "duplicate_messages": 0,
    "start_time": time.time(),
    "last_cleanup": time.time(),
    "service_failures": {
        "whatsapp_service": 0,
        "database": 0,
        "rate_limiter": 0
    }
}

WHATSAPP_SERVICE_AVAILABLE = False

# ==========================================================
# WHATSAPP SERVICE IMPORT
# ==========================================================

try:
    from app.services.whatsapp_service import send_text_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp Service loaded successfully")
except ImportError as e:
    logger.error(f"❌ WhatsApp Service import failed: {e}")
except Exception as e:
    logger.error(f"❌ WhatsApp Service error: {e}")

# ==========================================================
# DIRECT DATABASE FUNCTIONS
# ==========================================================

def normalize_dn(dn_value) -> str:
    """Normalize DN number for database lookup"""
    if dn_value is None:
        return ""
    dn_str = str(dn_value).strip()
    if dn_str.endswith('.0'):
        dn_str = dn_str[:-2]
    dn_str = re.sub(r'[^0-9]', '', dn_str)
    return dn_str


def is_dn_number(text: str) -> bool:
    """Check if text looks like a DN number"""
    pattern = r'^(624\d{7}|\d{10,12})$'
    return bool(re.match(pattern, text.strip()))


def calculate_priority(days: int) -> str:
    """Calculate priority based on days"""
    if days > 14:
        return "CRITICAL"
    elif days > 7:
        return "HIGH"
    elif days > 3:
        return "MEDIUM"
    return "LOW"


def get_dn_details_from_db(dn_number: str) -> Optional[Dict[str, Any]]:
    """
    Get DN details directly from database.
    CRITICAL FIX: Prioritizes integer search since PostgreSQL stores dn_no as INTEGER.
    """
    
    if dn_number in dn_cache:
        logger.info(f"📦 DN cache hit: {dn_number}")
        return dn_cache[dn_number]
    
    db = None
    try:
        db = SessionLocal()
        normalized = normalize_dn(dn_number)
        
        # CRITICAL: Convert to integer for PostgreSQL integer column
        try:
            normalized_int = int(normalized)
            logger.info(f"🔍 Searching for DN as integer: {normalized_int}")
        except ValueError:
            normalized_int = None
            logger.info(f"🔍 Searching for DN as string: {normalized}")
        
        # Build search conditions - INTEGER FIRST (since your data is stored as integer)
        search_conditions = []
        
        # Priority 1: Integer match (YOUR DATA TYPE - PostgreSQL integer column)
        if normalized_int is not None:
            search_conditions.append(DeliveryReport.dn_no == normalized_int)
        
        # Priority 2: String match
        search_conditions.append(cast(DeliveryReport.dn_no, String) == normalized)
        
        # Priority 3: With .0 suffix
        search_conditions.append(cast(DeliveryReport.dn_no, String) == f"{normalized}.0")
        
        # Priority 4: Contains (last resort)
        search_conditions.append(cast(DeliveryReport.dn_no, String).like(f"%{normalized}%"))
        
        # Execute search
        records = db.query(DeliveryReport).filter(or_(*search_conditions)).all()
        
        if not records:
            logger.warning(f"DN {dn_number} (normalized: {normalized}, int: {normalized_int}) not found in database")
            return None
        
        logger.info(f"✅ DN {dn_number} found! {len(records)} records")
        
        first = records[0]
        
        # Use actual columns from your database
        dn_date = first.dn_create_date
        pod_date = first.pod_date
        
        # Use delivery_status and pod_status
        delivery_status = first.delivery_status or "Pending"
        pod_status = first.pod_status or "Pending"
        
        # Calculate aging and status
        delivery_days = 0
        status = "Delivery Pending"
        status_emoji = "🟡"
        
        if delivery_status == "Delivered" and pod_status == "Received":
            if dn_date and pod_date:
                delivery_days = max(0, (pod_date - dn_date).days)
            status = "Delivered"
            status_emoji = "✅"
        elif delivery_status == "Dispatched" or pod_status == "Pending":
            if dn_date:
                delivery_days = max(0, (date.today() - dn_date).days)
            status = "POD Pending"
            status_emoji = "⏳"
        
        priority = calculate_priority(delivery_days)
        priority_emoji = "🔴" if priority == "CRITICAL" else "🟠" if priority == "HIGH" else "🟡" if priority == "MEDIUM" else "🟢"
        
        # Aggregate products
        unique_models = set()
        total_quantity = 0
        total_amount = 0.0
        products = []
        
        for r in records:
            qty = int(r.dn_qty or 0)
            amt = float(r.dn_amount or 0)
            total_quantity += qty
            total_amount += amt
            
            if r.material_no:
                model = r.customer_model or r.material_no
                if model not in unique_models:
                    unique_models.add(model)
                    products.append({
                        "model": model,
                        "material": r.material_no,
                        "quantity": qty,
                        "amount": amt
                    })
                else:
                    for p in products:
                        if p.get("model") == model:
                            p["quantity"] += qty
                            p["amount"] += amt
                            break
        
        result = {
            "dn_no": str(first.dn_no),
            "dealer_name": first.customer_name or "N/A",
            "dealer_code": first.customer_code or "N/A",
            "sales_office": first.division or "N/A",
            "warehouse": first.warehouse or "N/A",
            "city": first.ship_to_city or "N/A",
            "dn_date": dn_date.strftime("%Y-%m-%d") if dn_date else "N/A",
            "pod_date": pod_date.strftime("%Y-%m-%d") if pod_date else "Not Received",
            "delivery_days": delivery_days,
            "status": status,
            "status_emoji": status_emoji,
            "priority": priority,
            "priority_emoji": priority_emoji,
            "total_models": len(unique_models),
            "models_list": list(unique_models)[:5],
            "total_quantity": total_quantity,
            "total_amount": total_amount,
            "products": products[:5],
            "delivery_status": delivery_status,
            "pod_status": pod_status
        }
        
        dn_cache[dn_number] = result
        logger.info(f"✅ DN {dn_number}: {len(records)} records, status={delivery_status}")
        
        return result
        
    except Exception as e:
        logger.error(f"DN database lookup error: {e}")
        return None
    finally:
        if db:
            db.close()


def format_dn_response(details: Dict[str, Any]) -> str:
    """Format DN response for WhatsApp"""
    
    products_text = ""
    for idx, p in enumerate(details.get('products', []), 1):
        products_text += f"\n   {idx}. {p['model']} - Qty: {p['quantity']}"
    
    if details.get('total_models', 0) > 5:
        products_text += f"\n   ... +{details['total_models'] - 5} more models"
    
    delivery_status_display = "✅ Delivered" if details.get('delivery_status') == "Delivered" else "🚚 In Transit" if details.get('delivery_status') == "Dispatched" else "⏳ Pending"
    
    return f"""
📦 *DN DETAILS*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN Number:* {details['dn_no']}
📅 Date: {details['dn_date']}
{details['status_emoji']} Status: {details['status']}

🏪 *DEALER INFORMATION*
• Name: {details['dealer_name']}
• City: {details['city']}
• Office: {details['sales_office']}
• Warehouse: {details['warehouse']}

📦 *PRODUCTS*{products_text}

📊 *SUMMARY*
• Models: {details['total_models']}
• Quantity: {details['total_quantity']:,}
• Amount: PKR {details['total_amount']:,.0f}

⏱️ *AGING*
• Delivery Aging: {details['delivery_days']} days
{details['priority_emoji']} Priority: {details['priority']}

🚚 *SHIPMENT STATUS*
• Delivery Status: {delivery_status_display}
• POD Date: {details['pod_date']}

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Help` for more commands
"""


def search_dealer_in_db(dealer_name: str) -> Optional[Dict[str, Any]]:
    """Search for dealer in database"""
    
    db = None
    try:
        db = SessionLocal()
        
        records = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).all()
        
        if not records:
            logger.warning(f"Dealer '{dealer_name}' not found")
            return None
        
        # Aggregate by DN
        unique_dns = set()
        total_quantity = 0
        total_amount = 0.0
        delivered_dns = 0
        pending_deliveries = 0
        pending_pod = 0
        
        dn_status = {}
        
        for r in records:
            dn_no = normalize_dn(r.dn_no)
            if not dn_no:
                continue
                
            unique_dns.add(dn_no)
            total_quantity += int(r.dn_qty or 0)
            total_amount += float(r.dn_amount or 0)
            
            if dn_no not in dn_status:
                if r.delivery_status == "Delivered":
                    dn_status[dn_no] = "delivered"
                    delivered_dns += 1
                elif r.delivery_status == "Dispatched":
                    dn_status[dn_no] = "pending_pod"
                    pending_pod += 1
                else:
                    dn_status[dn_no] = "pending_delivery"
                    pending_deliveries += 1
        
        first = records[0]
        total_dns = len(unique_dns)
        completion_rate = round(delivered_dns / max(1, total_dns) * 100, 1)
        
        # Health score calculation
        health_score = 100
        health_score -= (pending_deliveries * 5)
        health_score -= (pending_pod * 2)
        health_score = max(0, min(100, health_score))
        
        if health_score >= 80:
            health_emoji = "🟢"
            health_status = "Excellent"
        elif health_score >= 60:
            health_emoji = "🟡"
            health_status = "Good"
        else:
            health_emoji = "🔴"
            health_status = "Needs Attention"
        
        result = {
            "dealer_name": first.customer_name,
            "dealer_code": first.customer_code or "N/A",
            "city": first.ship_to_city or "N/A",
            "sales_office": first.division or "N/A",
            "warehouse": first.warehouse or "N/A",
            "total_dns": total_dns,
            "delivered_dns": delivered_dns,
            "total_quantity": total_quantity,
            "total_amount": total_amount,
            "pending_deliveries": pending_deliveries,
            "pending_pod": pending_pod,
            "completion_rate": completion_rate,
            "health_score": health_score,
            "health_emoji": health_emoji,
            "health_status": health_status
        }
        
        logger.info(f"✅ Dealer '{dealer_name}': {total_dns} DNs, {delivered_dns} delivered")
        return result
        
    except Exception as e:
        logger.error(f"Dealer search error: {e}")
        return None
    finally:
        if db:
            db.close()


def format_dealer_response(details: Dict[str, Any]) -> str:
    """Format dealer response for WhatsApp"""
    
    return f"""
🏪 *DEALER DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *{details['dealer_name']}*
📍 City: {details['city']}
🏢 Office: {details['sales_office']}
🏭 Warehouse: {details['warehouse']}

📊 *PERFORMANCE SUMMARY*
• Total DNs: {details['total_dns']}
• Delivered: {details['delivered_dns']}
• Quantity: {details['total_quantity']:,}
• Revenue: PKR {details['total_amount']:,.0f}
• Completion Rate: {details['completion_rate']}%

⚠️ *PENDING ITEMS*
• Pending Deliveries: {details['pending_deliveries']}
• Pending PODs: {details['pending_pod']}

{details['health_emoji']} *Health Score: {details['health_score']} ({details['health_status']})*

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Help` for more commands
"""


def get_pending_summary_from_db() -> Dict[str, Any]:
    """Get pending deliveries summary"""
    db = None
    try:
        db = SessionLocal()
        
        records = db.query(DeliveryReport).filter(
            DeliveryReport.delivery_status != "Delivered"
        ).all()
        
        pending_list = []
        for r in records:
            dn_no = normalize_dn(r.dn_no)
            days = (date.today() - r.dn_create_date).days if r.dn_create_date else 0
            priority = calculate_priority(days)
            pending_list.append({
                "dn_no": dn_no,
                "dealer": r.customer_name,
                "days": days,
                "priority": priority,
                "status": r.delivery_status
            })
        
        pending_list.sort(key=lambda x: x["days"], reverse=True)
        critical = [p for p in pending_list if p["priority"] == "CRITICAL"]
        
        return {
            "total": len(pending_list),
            "critical": len(critical),
            "list": pending_list[:10]
        }
    finally:
        if db:
            db.close()


def format_pending_response(data: Dict[str, Any], title: str, emoji: str) -> str:
    """Format pending response"""
    if data["total"] == 0:
        return f"{emoji} *{title}*\n━━━━━━━━━━━━━━━━━━━━\n✅ No pending items found!"
    
    response = f"{emoji} *{title}*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    response += f"📊 Total: {data['total']}\n"
    response += f"⚠️ Critical: {data['critical']}\n\n"
    response += "🔴 *Top Priority Items:*\n"
    
    for item in data["list"][:5]:
        if item["priority"] == "CRITICAL":
            emoji_item = "🔴"
        elif item["priority"] == "HIGH":
            emoji_item = "🟠"
        else:
            emoji_item = "🟡"
        response += f"{emoji_item} DN {item['dn_no']}: {item['days']} days\n"
        response += f"   Dealer: {item['dealer']}\n"
    
    response += "\n━━━━━━━━━━━━━━━━━━━━\n💡 Type `Help` for more commands"
    return response


# ==========================================================
# MAIN PROCESSING FUNCTION
# ==========================================================

def process_message_direct(message: str) -> str:
    """Process message directly using database queries"""
    msg_lower = message.lower().strip()
    
    # Help command
    if msg_lower in ["help", "menu", "commands", "what can you do", "start"]:
        return """🤖 *AI LOGISTICS ASSISTANT - HELP*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• Send any 10+ digit number to track

📋 *Pending Items*
• `Pending deliveries` - Undelivered items

🏪 *Analytics*
• `[Dealer name]` - Dealer dashboard

💬 *General*
• `Help` - Show this menu

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # Greeting
    if msg_lower in ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "hola"]:
        hour = datetime.now().hour
        if hour < 12:
            greeting = "Good morning"
        elif hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"
        
        return f"""🎉 *Welcome to AI Logistics Assistant!*

{greeting}! 👋

I'm your intelligent logistics assistant.

📌 *Quick examples:*
• Send any 10+ digit number to track a DN
• Type `Pending deliveries` for delayed shipments
• Type `[Dealer name]` for dealer dashboard

Type `Help` to see all available commands!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # DN number
    if is_dn_number(message):
        details = get_dn_details_from_db(message)
        if details:
            return format_dn_response(details)
        else:
            return f"""
📦 *DN SEARCH*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN Number:* {message}

❌ Not found in database

💡 Type `Help` for available commands
"""
    
    # Pending deliveries
    if "pending delivery" in msg_lower or "how many pending" in msg_lower or "pending" in msg_lower:
        data = get_pending_summary_from_db()
        return format_pending_response(data, "PENDING DELIVERIES", "🚚")
    
    # Dealer search
    if len(message) > 3:
        details = search_dealer_in_db(message)
        if details:
            return format_dealer_response(details)
    
    # Default response
    return """
🤖 *AI LOGISTICS ASSISTANT*

I can help you with:
• 🔢 DN Tracking - Send any 10+ digit number
• 🚚 Pending Deliveries - Type `Pending deliveries`
• 🏪 Dealer Dashboard - Send dealer name

Type `Help` for complete menu
"""


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def _auto_cleanup_if_needed(request_id: str):
    current_time = time.time()
    total_requests = metrics["total_requests"]
    
    if total_requests > 0 and total_requests % AUTO_CLEANUP_INTERVAL == 0:
        if current_time - metrics.get("last_cleanup", 0) > 60:
            logger.bind(request_id=request_id).info(f"Auto cleanup triggered")
            old_size = len(processed_messages)
            processed_messages.clear()
            metrics["last_cleanup"] = current_time
            logger.bind(request_id=request_id).info(f"Cache cleanup complete: {old_size} messages cleared")


def _check_rate_limit(phone_number: str, request_id: str) -> bool:
    current_time = time.time()
    timestamps = rate_limit_cache.get(phone_number, [])
    timestamps = [t for t in timestamps if current_time - t < RATE_LIMIT_WINDOW]
    
    if len(timestamps) >= RATE_LIMIT_MAX_MESSAGES:
        logger.bind(request_id=request_id).warning(f"Rate limit exceeded for {phone_number}")
        return False
    
    timestamps.append(current_time)
    rate_limit_cache[phone_number] = timestamps
    return True


async def send_whatsapp_message(
    phone_number: str, 
    message: str, 
    request_id: str, 
    context_msg_id: Optional[str] = None
) -> Dict[str, Any]:
    if not WHATSAPP_SERVICE_AVAILABLE:
        return {"success": False, "error": "Service not available"}
    
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
    
    try:
        if context_msg_id:
            result = send_text_message(phone_number, message, message_id=context_msg_id, request_id=request_id)
        else:
            result = send_text_message(phone_number, message, request_id=request_id)
        return result
    except Exception as e:
        logger.bind(request_id=request_id).error(f"Send failed: {e}")
        return {"success": False, "error": str(e)}


# ==========================================================
# WEBHOOK ENDPOINTS
# ==========================================================

@router.get("/")
async def verify_webhook(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        if hub_challenge:
            logger.success("✅ Webhook verified successfully!")
            return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/")
async def receive_message(request: Request) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    logger.bind(request_id=request_id)
    metrics["total_requests"] += 1
    
    logger.info(f"📨 Webhook received")
    _auto_cleanup_if_needed(request_id)
    
    try:
        raw_body = await asyncio.wait_for(request.body(), timeout=10.0)
        payload = json.loads(raw_body.decode('utf-8'))
        
        if "entry" not in payload:
            return {"success": False, "error": "Invalid payload", "request_id": request_id}
        
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        if value.get("statuses"):
            return {"success": True, "type": "status_update", "request_id": request_id}
        
        messages = value.get("messages", [])
        if not messages:
            return {"success": True, "type": "no_messages", "request_id": request_id}
        
        processed_count = 0
        for message in messages:
            phone_number = message.get("from")
            msg_id = message.get("id")
            msg_type = message.get("type", "unknown")
            
            if not phone_number:
                continue
            
            logger.info(f"📱 From: {phone_number}, Type: {msg_type}")
            
            if msg_id and msg_id in processed_messages:
                logger.info(f"Duplicate: {msg_id}")
                continue
            if msg_id:
                processed_messages[msg_id] = True
            
            if not _check_rate_limit(phone_number, request_id):
                await send_whatsapp_message(phone_number, "⚠️ Too many messages. Please wait.", request_id, msg_id)
                continue
            
            if msg_type != "text":
                await send_whatsapp_message(phone_number, "📱 Please send text messages only. Type 'Help'.", request_id, msg_id)
                continue
            
            user_message = message.get("text", {}).get("body", "").strip()
            if not user_message:
                continue
            
            logger.info(f"💬 Query: {user_message[:100]}")
            
            response = process_message_direct(user_message)
            await send_whatsapp_message(phone_number, response, request_id, msg_id)
            processed_count += 1
        
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"✅ Done: {processing_time:.0f}ms, {processed_count} messages")
        
        return {
            "success": True,
            "request_id": request_id,
            "processing_time_ms": round(processing_time, 2),
            "messages_processed": processed_count
        }
        
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return {"success": False, "error": str(e), "request_id": request_id}


# ==========================================================
# DEBUG ENDPOINTS
# ==========================================================

@router.get("/debug/check-dn/{dn_number}")
async def debug_check_dn(dn_number: str):
    """Debug endpoint to check DN in database"""
    db = SessionLocal()
    try:
        normalized = normalize_dn(dn_number)
        try:
            normalized_int = int(normalized)
        except ValueError:
            normalized_int = None
        
        # Try all search methods
        results = {
            "searched": dn_number,
            "normalized": normalized,
            "normalized_int": normalized_int,
            "matches": {}
        }
        
        if normalized_int is not None:
            int_match = db.query(DeliveryReport).filter(DeliveryReport.dn_no == normalized_int).all()
            results["matches"]["integer_match"] = len(int_match)
            if int_match:
                results["sample"] = [{"dn_no": str(r.dn_no), "customer": r.customer_name} for r in int_match[:3]]
        
        string_match = db.query(DeliveryReport).filter(cast(DeliveryReport.dn_no, String) == normalized).all()
        results["matches"]["string_match"] = len(string_match)
        
        like_match = db.query(DeliveryReport).filter(cast(DeliveryReport.dn_no, String).like(f"%{normalized}%")).all()
        results["matches"]["contains_match"] = len(like_match)
        
        # Get sample DNs
        sample_dns = db.query(DeliveryReport.dn_no).limit(10).all()
        results["sample_dns_in_db"] = [str(d[0]) for d in sample_dns if d[0]]
        
        results["found"] = any(results["matches"].values())
        
        return results
    finally:
        db.close()


# ==========================================================
# MONITORING ENDPOINTS
# ==========================================================

@router.get("/health")
async def health_check():
    db_healthy = False
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_healthy = True
    except Exception as e:
        logger.error(f"DB health failed: {e}")
    
    return {
        "status": "healthy" if db_healthy else "degraded",
        "version": "28.3",
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "DIRECT_DATABASE",
        "integer_search": "ENABLED",
        "services": {
            "whatsapp_service": {"available": WHATSAPP_SERVICE_AVAILABLE},
            "database": {"connected": db_healthy}
        },
        "cache": {"dn_cache_size": len(dn_cache)}
    }


@router.get("/ping")
async def ping():
    return {
        "pong": True, 
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "direct_database",
        "cache_size": len(dn_cache)
    }


@router.get("/cache/clear")
async def clear_cache():
    old_size = len(dn_cache)
    dn_cache.clear()
    return {"success": True, "cleared": old_size}


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("📡 WEBHOOK v28.3 - FIXED INTEGER DN SEARCH")
logger.info("=" * 70)
logger.info("")
logger.info("   CRITICAL FIXES:")
logger.info("   ✅ Integer search for PostgreSQL dn_no column")
logger.info("   ✅ Multiple search patterns (int, string, .0, contains)")
logger.info("   ✅ Debug endpoint for DN lookup troubleshooting")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
