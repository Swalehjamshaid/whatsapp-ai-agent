# ==========================================================
# FILE: app/routes/webhook.py (v42.1 - SYNTAX ERROR FIXED)
# ==========================================================

import json
import re
import uuid
import asyncio
import os
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Dict, Any, Optional, List, Tuple
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse
from loguru import logger
from cachetools import TTLCache

from app.config import config
from app.database import SessionLocal
from app.models import DeliveryReport

# GROQ AI Import
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = 3500
SEND_MESSAGE_TIMEOUT = 30
CACHE_TTL = 300

GROQ_API_KEY = getattr(config, 'GROQ_API_KEY', os.environ.get('GROQ_API_KEY', ''))
GROQ_MODEL = getattr(config, 'GROQ_MODEL', 'llama-3.3-70b-versatile')
GROQ_ENABLED = GROQ_AVAILABLE and bool(GROQ_API_KEY)

# ==========================================================
# CACHES
# ==========================================================

processed_messages = TTLCache(maxsize=5000, ttl=3600)
rate_limit_cache = TTLCache(maxsize=10000, ttl=60)
query_cache = TTLCache(maxsize=500, ttl=CACHE_TTL)

# ==========================================================
# METRICS
# ==========================================================

metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "queries_answered": 0,
    "start_time": time.time()
}

WHATSAPP_SERVICE_AVAILABLE = False
GROQ_CLIENT = None

# ==========================================================
# SERVICE IMPORTS
# ==========================================================

try:
    from app.services.whatsapp_service import send_text_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("WhatsApp Service loaded")
except ImportError:
    logger.warning("WhatsApp Service not available")

# ==========================================================
# GROQ INITIALIZATION
# ==========================================================

def init_groq_client():
    global GROQ_CLIENT, GROQ_ENABLED
    if not GROQ_AVAILABLE or not GROQ_API_KEY:
        GROQ_ENABLED = False
        return None
    try:
        GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)
        logger.info("GROQ AI Client initialized")
        return GROQ_CLIENT
    except Exception as e:
        logger.error(f"GROQ init failed: {e}")
        GROQ_ENABLED = False
        return None

if GROQ_ENABLED:
    init_groq_client()

# ==========================================================
# HELP MESSAGE
# ==========================================================

def get_help_message() -> str:
    return """
🤖 *LOGISTICS AI CONTROL TOWER*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *DEALER COMMANDS:*
• `Haji Sharaf ud Din & Sons` - Dealer dashboard
• `Top 10 dealers` - Dealer ranking

🏭 *WAREHOUSE COMMANDS:*
• `Sargodha Warehouse` - Warehouse SLA
• `Top 10 warehouses` - Warehouse ranking
• `Warehouse wise delivery aging` - All warehouses

📊 *KPI COMMANDS:*
• `Warehouse KPI` - KPI table
• `Executive dashboard` - Company KPIs

🚨 *CONTROL TOWER:*
• `Control tower` - Risk monitoring

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ==========================================================
# DEALER DASHBOARD
# ==========================================================

def get_dealer_dashboard(dealer_name: str) -> str:
    db = SessionLocal()
    try:
        records = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).all()
        
        if not records:
            return f"❌ Dealer '{dealer_name}' not found"
        
        total_units = sum(int(r.dn_qty or 0) for r in records)
        total_revenue = sum(float(r.dn_amount or 0) for r in records)
        total_dns = len(set(r.dn_no for r in records))
        delivered_dn = len([r for r in records if r.delivery_status == "Delivered"])
        
        return f"""
🏪 *DEALER DASHBOARD: {records[0].customer_name}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *VOLUME*
• DNs: {total_dns:,}
• Units: {total_units:,}
• Revenue: PKR {total_revenue:,.0f}

✅ *STATUS*
• Delivered: {delivered_dn}
• Pending: {total_dns - delivered_dn}
• Rate: {(delivered_dn / total_dns * 100):.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    except Exception as e:
        logger.error(f"Dealer dashboard error: {e}")
        return f"Error: {str(e)}"
    finally:
        db.close()

# ==========================================================
# WAREHOUSE DASHBOARD
# ==========================================================

def get_warehouse_dashboard(warehouse_name: str) -> str:
    db = SessionLocal()
    try:
        records = db.query(DeliveryReport).filter(
            DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")
        ).all()
        
        if not records:
            return f"❌ Warehouse '{warehouse_name}' not found"
        
        total_dns = len(set(r.dn_no for r in records))
        total_revenue = sum(float(r.dn_amount or 0) for r in records)
        total_units = sum(int(r.dn_qty or 0) for r in records)
        
        delivery_agings = []
        for r in records:
            if r.dn_create_date and r.good_issue_date:
                aging = (r.good_issue_date - r.dn_create_date).days
                delivery_agings.append(aging)
        
        avg_aging = round(sum(delivery_agings) / len(delivery_agings), 1) if delivery_agings else 0
        
        return f"""
🏭 *WAREHOUSE DASHBOARD: {warehouse_name.title()}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *SUMMARY*
• DNs: {total_dns:,}
• Units: {total_units:,}
• Revenue: PKR {total_revenue:,.0f}

🚚 *DELIVERY AGING*
• Average: {avg_aging} days

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    except Exception as e:
        logger.error(f"Warehouse dashboard error: {e}")
        return f"Error: {str(e)}"
    finally:
        db.close()

# ==========================================================
# WAREHOUSE WISE DELIVERY AGING
# ==========================================================

def get_warehouse_wise_delivery_aging() -> str:
    db = SessionLocal()
    try:
        records = db.query(DeliveryReport).all()
        warehouse_agings = defaultdict(list)
        
        for r in records:
            if r.warehouse and r.dn_create_date and r.good_issue_date:
                aging = (r.good_issue_date - r.dn_create_date).days
                warehouse_agings[r.warehouse].append(aging)
        
        results = []
        for warehouse, agings in warehouse_agings.items():
            if agings:
                avg_aging = sum(agings) / len(agings)
                results.append((warehouse, round(avg_aging, 1)))
        
        results.sort(key=lambda x: x[1])
        
        if not results:
            return "No warehouse aging data found"
        
        response = "📊 *WAREHOUSE WISE DELIVERY AGING*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for warehouse, avg_days in results[:15]:
            bar = "#" * min(int(avg_days), 20)
            response += f"• {warehouse:15} {avg_days:4.1f} days {bar}\n"
        
        return response
    finally:
        db.close()

# ==========================================================
# WAREHOUSE KPI TABLE
# ==========================================================

def get_warehouse_kpi_table() -> str:
    db = SessionLocal()
    try:
        from sqlalchemy import func, desc
        results = db.query(
            DeliveryReport.warehouse,
            func.count(func.distinct(DeliveryReport.dn_no)).label('dn_count'),
            func.sum(DeliveryReport.dn_amount).label('revenue'),
            func.sum(DeliveryReport.dn_qty).label('units')
        ).filter(DeliveryReport.warehouse.isnot(None))\
         .group_by(DeliveryReport.warehouse)\
         .order_by(desc('revenue'))\
         .limit(15).all()
        
        if not results:
            return "No warehouse data found"
        
        response = "📊 *WAREHOUSE KPI TABLE*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        response += f"{'Warehouse':15} {'DNs':>6} {'Revenue(M)':>12} {'Units':>8}\n"
        response += "-" * 45 + "\n"
        
        for r in results:
            if r.warehouse:
                revenue_m = float(r.revenue or 0) / 1000000
                response += f"{r.warehouse[:13]:15} {r.dn_count:>6,} {revenue_m:>11.1f}M {int(r.units or 0):>8,}\n"
        
        return response
    finally:
        db.close()

# ==========================================================
# TOP DEALERS
# ==========================================================

def get_top_dealers(limit: int = 10) -> str:
    db = SessionLocal()
    try:
        from sqlalchemy import func, desc
        results = db.query(
            DeliveryReport.customer_name,
            func.sum(DeliveryReport.dn_amount).label('revenue')
        ).filter(DeliveryReport.customer_name.isnot(None))\
         .group_by(DeliveryReport.customer_name)\
         .order_by(desc('revenue'))\
         .limit(limit).all()
        
        if not results:
            return "No dealer data found"
        
        response = f"🏆 *TOP {limit} DEALERS BY REVENUE*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, (name, revenue) in enumerate(results, 1):
            response += f"{i}. {name[:25]}: PKR {float(revenue or 0):,.0f}\n"
        return response
    finally:
        db.close()

# ==========================================================
# TOP WAREHOUSES
# ==========================================================

def get_top_warehouses(limit: int = 10) -> str:
    db = SessionLocal()
    try:
        from sqlalchemy import func, desc
        results = db.query(
            DeliveryReport.warehouse,
            func.sum(DeliveryReport.dn_amount).label('revenue')
        ).filter(DeliveryReport.warehouse.isnot(None))\
         .group_by(DeliveryReport.warehouse)\
         .order_by(desc('revenue'))\
         .limit(limit).all()
        
        if not results:
            return "No warehouse data found"
        
        response = f"🏆 *TOP {limit} WAREHOUSES BY REVENUE*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, (name, revenue) in enumerate(results, 1):
            response += f"{i}. {name}: PKR {float(revenue or 0):,.0f}\n"
        return response
    finally:
        db.close()

# ==========================================================
# EXECUTIVE DASHBOARD
# ==========================================================

def get_executive_dashboard() -> str:
    db = SessionLocal()
    try:
        records = db.query(DeliveryReport).all()
        
        total_revenue = sum(float(r.dn_amount or 0) for r in records)
        total_units = sum(int(r.dn_qty or 0) for r in records)
        total_dn = len(set(r.dn_no for r in records))
        
        delivered = len([r for r in records if r.delivery_status == "Delivered"])
        delivery_rate = (delivered / len(records) * 100) if records else 0
        
        return f"""
👔 *EXECUTIVE DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *COMPANY KPIs*
• Revenue: PKR {total_revenue:,.0f}
• Units: {total_units:,}
• Total DNs: {total_dn:,}

✅ *DELIVERY RATE: {delivery_rate:.1f}%*

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    finally:
        db.close()

# ==========================================================
# CONTROL TOWER
# ==========================================================

def get_control_tower() -> str:
    db = SessionLocal()
    try:
        today = date.today()
        records = db.query(DeliveryReport).all()
        
        risk_buckets = {'0-7': 0, '8-15': 0, '16-30': 0, '31+': 0}
        
        for r in records:
            if not r.good_issue_date and r.dn_create_date:
                days = (today - r.dn_create_date).days
                if days <= 7:
                    risk_buckets['0-7'] += 1
                elif days <= 15:
                    risk_buckets['8-15'] += 1
                elif days <= 30:
                    risk_buckets['16-30'] += 1
                else:
                    risk_buckets['31+'] += 1
        
        response = "🚨 *CONTROL TOWER*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        response += "📊 *PENDING DELIVERY RISK:*\n"
        for bucket, count in risk_buckets.items():
            response += f"• {bucket} days: {count}\n"
        
        return response
    finally:
        db.close()

# ==========================================================
# COMPARE DEALERS
# ==========================================================

def compare_dealers(dealer_a: str, dealer_b: str) -> str:
    db = SessionLocal()
    try:
        records_a = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_a}%")
        ).all()
        records_b = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_b}%")
        ).all()
        
        revenue_a = sum(float(r.dn_amount or 0) for r in records_a)
        revenue_b = sum(float(r.dn_amount or 0) for r in records_b)
        
        winner = dealer_a if revenue_a > revenue_b else dealer_b
        
        return f"""
🔄 *DEALER COMPARISON*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *REVENUE*
• {dealer_a}: PKR {revenue_a:,.0f}
• {dealer_b}: PKR {revenue_b:,.0f}
• Winner: 🏆 {winner}

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    finally:
        db.close()

# ==========================================================
# REVENUE TREND
# ==========================================================

def get_revenue_trend(period: str = "monthly") -> str:
    db = SessionLocal()
    try:
        from sqlalchemy import func
        if period == "daily":
            date_trunc = func.date(DeliveryReport.dn_create_date)
        elif period == "weekly":
            date_trunc = func.date_trunc('week', DeliveryReport.dn_create_date)
        else:
            date_trunc = func.date_trunc('month', DeliveryReport.dn_create_date)
        
        results = db.query(
            date_trunc.label('period'),
            func.sum(DeliveryReport.dn_amount).label('revenue')
        ).filter(DeliveryReport.dn_create_date.isnot(None))\
         .group_by('period')\
         .order_by('period')\
         .limit(12).all()
        
        if not results:
            return "No trend data found"
        
        response = f"📈 *REVENUE TREND ({period.upper()})*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for r in results:
            if r.period:
                period_str = r.period.strftime('%Y-%m-%d')
                revenue_m = float(r.revenue or 0) / 1000000
                response += f"{period_str}: PKR {revenue_m:.1f}M\n"
        
        return response
    finally:
        db.close()

# ==========================================================
# DIVISION DASHBOARD
# ==========================================================

def get_division_dashboard(division_name: str) -> str:
    db = SessionLocal()
    try:
        division_map = {
            'refrigerator': 'REF', 'tv': 'TV', 'cooking': 'COOK'
        }
        division_code = division_map.get(division_name.lower(), division_name.upper())
        
        records = db.query(DeliveryReport).filter(
            DeliveryReport.division == division_code
        ).all()
        
        if not records:
            records = db.query(DeliveryReport).limit(100).all()
        
        total_revenue = sum(float(r.dn_amount or 0) for r in records)
        total_units = sum(int(r.dn_qty or 0) for r in records)
        
        return f"""
📊 *DIVISION DASHBOARD: {division_name.upper()}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 Revenue: PKR {total_revenue:,.0f}
📦 Units: {total_units:,}

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    finally:
        db.close()

# ==========================================================
# ENTITY EXTRACTION
# ==========================================================

def extract_entities(message: str) -> Dict[str, Any]:
    msg_lower = message.lower()
    
    # Check for help
    if any(word in msg_lower for word in ['help', 'menu', 'commands']):
        return {'is_help': True}
    
    # Extract warehouse
    warehouses = ['lahore', 'karachi', 'rawalpindi', 'sargodha', 'islamabad', 'multan']
    warehouse_name = None
    for wh in warehouses:
        if wh in msg_lower:
            warehouse_name = wh.title()
            break
    
    # Extract dealer (if no warehouse)
    dealer_name = None
    if not warehouse_name:
        if '&' in message:
            dealer_match = re.search(r'([A-Za-z\s&]+(?:&[A-Za-z\s]+)+)', message)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip()
        elif len(msg_lower.split()) <= 4:
            dealer_name = message.strip()
    
    # Extract top N
    top_match = re.search(r'top\s+(\d+)', msg_lower)
    top_n = int(top_match.group(1)) if top_match else None
    
    return {
        'is_help': False,
        'warehouse_name': warehouse_name,
        'dealer_name': dealer_name,
        'top_n': top_n
    }

# ==========================================================
# MAIN PROCESSOR
# ==========================================================

async def process_message(message: str) -> str:
    msg_lower = message.lower()
    entities = extract_entities(message)
    
    # Help
    if entities.get('is_help'):
        return get_help_message()
    
    # Executive Dashboard
    if 'executive dashboard' in msg_lower:
        return get_executive_dashboard()
    
    # Control Tower
    if 'control tower' in msg_lower:
        return get_control_tower()
    
    # Warehouse wise delivery aging
    if 'warehouse wise delivery aging' in msg_lower:
        return get_warehouse_wise_delivery_aging()
    
    # Warehouse KPI
    if 'warehouse kpi' in msg_lower:
        return get_warehouse_kpi_table()
    
    # Top dealers
    if 'top dealers' in msg_lower:
        return get_top_dealers(entities.get('top_n') or 10)
    
    # Top warehouses
    if 'top warehouses' in msg_lower:
        return get_top_warehouses(entities.get('top_n') or 10)
    
    # Compare dealers
    if 'compare' in msg_lower and 'vs' in msg_lower:
        parts = msg_lower.split(' vs ')
        if len(parts) == 2:
            a = parts[0].replace('compare', '').strip()
            b = parts[1].strip()
            return compare_dealers(a, b)
    
    # Revenue trend
    if 'revenue trend' in msg_lower:
        period = 'daily' if 'daily' in msg_lower else 'weekly' if 'weekly' in msg_lower else 'monthly'
        return get_revenue_trend(period)
    
    # Division dashboard
    for div in ['refrigerator', 'tv', 'cooking']:
        if div in msg_lower:
            return get_division_dashboard(div)
    
    # Warehouse dashboard
    if entities.get('warehouse_name'):
        return get_warehouse_dashboard(entities['warehouse_name'])
    
    # Dealer dashboard
    if entities.get('dealer_name'):
        return get_dealer_dashboard(entities['dealer_name'])
    
    # GROQ fallback
    if GROQ_ENABLED and GROQ_CLIENT:
        try:
            response = GROQ_CLIENT.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are a logistics assistant. Answer concisely."},
                    {"role": "user", "content": message}
                ],
                max_tokens=300,
                temperature=0.3
            )
            ai_response = response.choices[0].message.content
            return ai_response + "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 Type 'Help' for commands"
        except Exception as e:
            logger.error(f"GROQ error: {e}")
    
    return get_help_message()

# ==========================================================
# WEBHOOK HANDLERS
# ==========================================================

def check_rate_limit(phone_number: str) -> bool:
    current_time = time.time()
    timestamps = rate_limit_cache.get(phone_number, [])
    timestamps = [t for t in timestamps if current_time - t < 60]
    
    if len(timestamps) >= 10:
        return False
    
    timestamps.append(current_time)
    rate_limit_cache[phone_number] = timestamps
    return True

async def send_whatsapp_message(phone_number: str, message: str, request_id: str) -> Dict[str, Any]:
    if not WHATSAPP_SERVICE_AVAILABLE:
        logger.info(f"Mock send to {phone_number}: {message[:100]}")
        return {"success": True, "mock": True}
    
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
    
    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: send_text_message(phone_number, message, request_id=request_id)
            ),
            timeout=SEND_MESSAGE_TIMEOUT
        )
        return result
    except Exception as e:
        logger.error(f"Send failed: {e}")
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
            logger.success("Webhook verified!")
            return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Verification failed")

@router.post("/")
async def receive_message(request: Request):
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    metrics["total_requests"] += 1
    
    try:
        payload = await request.json()
        
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        if value.get("statuses"):
            return {"success": True, "type": "status_update"}
        
        messages = value.get("messages", [])
        if not messages:
            return {"success": True, "type": "no_messages"}
        
        for message in messages:
            phone_number = message.get("from")
            msg_id = message.get("id")
            msg_type = message.get("type", "unknown")
            
            if not phone_number:
                continue
            
            if msg_id and msg_id in processed_messages:
                continue
            if msg_id:
                processed_messages[msg_id] = True
            
            if not check_rate_limit(phone_number):
                await send_whatsapp_message(phone_number, "Too many messages. Please wait.", request_id)
                continue
            
            if msg_type != "text":
                await send_whatsapp_message(phone_number, "Please send text messages. Type 'Help'.", request_id)
                continue
            
            user_message = message.get("text", {}).get("body", "").strip()
            if not user_message:
                continue
            
            logger.info(f"Processing: {user_message}")
            
            response = await process_message(user_message)
            await send_whatsapp_message(phone_number, response, request_id)
            metrics["successful_requests"] += 1
            metrics["queries_answered"] += 1
        
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"Complete: {processing_time:.0f}ms")
        
        return {
            "success": True,
            "request_id": request_id,
            "processing_time_ms": round(processing_time, 2),
            "version": "42.1"
        }
        
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return {"success": False, "error": str(e), "request_id": request_id}

# ==========================================================
# HEALTH CHECK
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
        "version": "42.1",
        "groq_enabled": GROQ_ENABLED,
        "queries_answered": metrics["queries_answered"],
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/ping")
async def ping():
    return {"pong": True, "version": "42.1"}

# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 60)
logger.info("WEBHOOK v42.1 - READY")
logger.info(f"GROQ: {'ENABLED' if GROQ_ENABLED else 'DISABLED'}")
logger.info("=" * 60)
