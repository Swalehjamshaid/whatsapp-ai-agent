# ==========================================================
# FILE: app/routes/webhook.py (v35.0 - GROQ AI INTEGRATION)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - Complete Business Rules Engine with GROQ AI
# 
# CAPABILITIES v35.0:
# - ✅ GROQ AI Integration (Llama 3, Mixtral, Gemma)
# - ✅ Delivery Aging (Completed Deliveries)
# - ✅ Pending Delivery Aging 
# - ✅ POD Aging (Completed POD)
# - ✅ Pending POD Aging
# - ✅ DN Details & Status
# - ✅ Dealer Performance Dashboard
# - ✅ Warehouse Analytics
# - ✅ City-wise Analysis
# - ✅ Product/Model Analysis
# - ✅ PGI/POD Status Reports
# - ✅ KPI Dashboard
# - ✅ Control Tower Alerts
# - ✅ Natural Language Understanding via GROQ
# ==========================================================

import json
import time
import uuid
import re
import asyncio
import traceback
import os
from typing import Dict, Any, Optional, List, Tuple
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import text, or_, cast, String, and_, func, desc
from datetime import datetime, date, timedelta
from loguru import logger
from cachetools import TTLCache

from app.config import config
from app.database import SessionLocal
from app.models import DeliveryReport

# GROQ AI Import
try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ GROQ AI SDK loaded successfully")
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("⚠️ GROQ AI SDK not installed. Install with: pip install groq")

# Create router
router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS - PRESERVED
# ==========================================================

MAX_MESSAGE_LENGTH = 3500
REQUEST_TIMEOUT_SECONDS = 35
SEND_MESSAGE_TIMEOUT = 30
MAX_RETRIES = 2
RETRY_DELAYS = [1, 2]

RATE_LIMIT_MAX_MESSAGES = 10
RATE_LIMIT_WINDOW = 60
AUTO_CLEANUP_INTERVAL = 500

DIAGNOSTIC_MODE = True

# GROQ Configuration
GROQ_API_KEY = getattr(config, 'GROQ_API_KEY', os.environ.get('GROQ_API_KEY', ''))
GROQ_MODEL = getattr(config, 'GROQ_MODEL', 'mixtral-8x7b-32768')  # Options: llama3-70b-8192, mixtral-8x7b-32768, gemma2-9b-it
GROQ_ENABLED = getattr(config, 'GROQ_ENABLED', True) and GROQ_AVAILABLE and bool(GROQ_API_KEY)

# ==========================================================
# CACHES - PRESERVED
# ==========================================================

processed_messages = TTLCache(maxsize=5000, ttl=3600)
rate_limit_cache = TTLCache(maxsize=10000, ttl=RATE_LIMIT_WINDOW)
dn_cache = TTLCache(maxsize=1000, ttl=3600)
query_cache = TTLCache(maxsize=500, ttl=300)

# ==========================================================
# METRICS - PRESERVED
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
        "rate_limiter": 0,
        "ai_service": 0,
        "groq_service": 0
    },
    "service_usage": {
        "ai_service_calls": 0,
        "direct_db_calls": 0,
        "groq_calls": 0,
        "fallback_mode": False
    },
    "diagnostics": {
        "dn_lookup_attempts": 0,
        "dn_lookup_successes": 0,
        "dn_lookup_failures": 0,
        "last_failed_dn": None,
        "last_error_trace": None
    }
}

WHATSAPP_SERVICE_AVAILABLE = False
AI_SERVICE_AVAILABLE = False
GROQ_CLIENT = None

# ==========================================================
# SERVICE IMPORTS - PRESERVED
# ==========================================================

try:
    from app.services.whatsapp_service import send_text_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp Service loaded successfully")
except ImportError as e:
    logger.error(f"❌ WhatsApp Service import failed: {e}")
except Exception as e:
    logger.error(f"❌ WhatsApp Service error: {e}")

try:
    from app.services.ai_query_service import process_whatsapp_query, get_query_service, initialize_query_service
    from app.services.logistics_query_service import get_logistics_query_service
    from app.services.analytics_service import AnalyticsService
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ AI Query Service loaded successfully")
except ImportError as e:
    logger.warning(f"⚠️ AI Query Service import failed: {e} - Will use direct DB fallback")
    AI_SERVICE_AVAILABLE = False
except Exception as e:
    logger.warning(f"⚠️ AI Query Service error: {e} - Will use direct DB fallback")
    AI_SERVICE_AVAILABLE = False

# ==========================================================
# GROQ AI INITIALIZATION
# ==========================================================

def init_groq_client():
    """Initialize GROQ AI client"""
    global GROQ_CLIENT, GROQ_ENABLED
    
    if not GROQ_AVAILABLE:
        logger.warning("⚠️ GROQ SDK not available")
        GROQ_ENABLED = False
        return None
    
    if not GROQ_API_KEY:
        logger.warning("⚠️ GROQ_API_KEY not configured")
        GROQ_ENABLED = False
        return None
    
    try:
        GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)
        logger.info(f"✅ GROQ AI Client initialized (Model: {GROQ_MODEL})")
        metrics["service_usage"]["groq_calls"] = 0
        return GROQ_CLIENT
    except Exception as e:
        logger.error(f"❌ GROQ Client initialization failed: {e}")
        GROQ_ENABLED = False
        return None

# Initialize GROQ on startup
if GROQ_ENABLED:
    init_groq_client()

# ==========================================================
# SERVICE INITIALIZATION - PRESERVED
# ==========================================================

_services_initialized = False

def ensure_services_initialized():
    global _services_initialized, AI_SERVICE_AVAILABLE
    if _services_initialized:
        return
    if AI_SERVICE_AVAILABLE:
        try:
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                logistics_service = get_logistics_query_service(db)
                analytics_service = AnalyticsService(db)
                initialize_query_service(
                    analytics_service=analytics_service,
                    logistics_service=logistics_service,
                    kpi_service=None,
                    ai_provider=None
                )
                _services_initialized = True
            finally:
                db.close()
        except Exception as e:
            logger.error(f"❌ Service initialization failed: {e}")
            AI_SERVICE_AVAILABLE = False
    else:
        _services_initialized = True

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def normalize_dn(dn_value) -> str:
    if dn_value is None:
        return ""
    dn_str = str(dn_value).strip()
    if dn_str.endswith('.0'):
        dn_str = dn_str[:-2]
    dn_str = re.sub(r'[^0-9]', '', dn_str)
    return dn_str

def is_dn_number(text: str) -> bool:
    cleaned = re.sub(r'[^0-9]', '', text.strip())
    return bool(re.match(r'^\d{10,12}$', cleaned))

def extract_dn_from_message(message: str) -> Optional[str]:
    match = re.search(r'\b(\d{10,12})\b', message)
    return match.group(1) if match else None

def extract_days_from_message(message: str) -> int:
    match = re.search(r'>\s*(\d+)|more than\s*(\d+)|over\s*(\d+)', message.lower())
    if match:
        return int(match.group(1) or match.group(2) or match.group(3))
    return 0

def extract_dealer_from_message(message: str) -> Optional[str]:
    cleaned = re.sub(r'(show|dealer|of|for|performance|dashboard|details?|info|data)', '', message, flags=re.IGNORECASE)
    cleaned = re.sub(r'what|is|the|are|tell|me|about', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    if len(cleaned) > 3:
        return cleaned
    return None

def calculate_priority(days: int) -> str:
    if days > 14:
        return "CRITICAL"
    elif days > 7:
        return "HIGH"
    elif days > 3:
        return "MEDIUM"
    return "LOW"

# ==========================================================
# 1. DELIVERY AGING (Completed Delivery)
# ==========================================================

def calculate_delivery_aging(dn_number: str = None, dealer_name: str = None, 
                              warehouse: str = None, city: str = None,
                              days_gt: int = None) -> Dict[str, Any]:
    """Delivery Aging = PGI Date - DN Creation Date"""
    db = SessionLocal()
    try:
        query = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.dn_create_date.isnot(None)
        )
        
        if dn_number:
            query = query.filter(DeliveryReport.dn_no == dn_number)
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        if warehouse:
            query = query.filter(DeliveryReport.warehouse == warehouse)
        if city:
            query = query.filter(DeliveryReport.ship_to_city == city)
        
        results = query.all()
        
        aging_list = []
        for record in results:
            aging = (record.good_issue_date - record.dn_create_date).days
            if days_gt is None or aging > days_gt:
                aging_list.append({
                    "dn_no": str(record.dn_no),
                    "dealer": record.customer_name,
                    "warehouse": record.warehouse,
                    "city": record.ship_to_city,
                    "aging_days": aging,
                    "dn_date": record.dn_create_date.strftime("%Y-%m-%d") if record.dn_create_date else "N/A",
                    "pgi_date": record.good_issue_date.strftime("%Y-%m-%d") if record.good_issue_date else "N/A"
                })
        
        if dn_number and aging_list:
            return {"dn": dn_number, "delivery_aging_days": aging_list[0]["aging_days"]}
        
        return {
            "type": "delivery_aging",
            "total_records": len(aging_list),
            "average_aging": round(sum(d["aging_days"] for d in aging_list) / max(1, len(aging_list)), 1),
            "highest_aging": aging_list[0] if aging_list else None,
            "lowest_aging": aging_list[-1] if aging_list else None,
            "over_3_days": len([d for d in aging_list if d["aging_days"] > 3]),
            "over_7_days": len([d for d in aging_list if d["aging_days"] > 7]),
            "over_15_days": len([d for d in aging_list if d["aging_days"] > 15]),
            "records": aging_list[:20]
        }
    finally:
        db.close()

# ==========================================================
# 2. PENDING DELIVERY AGING
# ==========================================================

def calculate_pending_delivery_aging(days_gt: int = None, dealer_name: str = None,
                                       warehouse: str = None, city: str = None) -> Dict[str, Any]:
    """Pending Delivery Aging = Today - DN Creation Date (when PGI not done)"""
    db = SessionLocal()
    try:
        today = date.today()
        
        query = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.is_(None)
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        if warehouse:
            query = query.filter(DeliveryReport.warehouse == warehouse)
        if city:
            query = query.filter(DeliveryReport.ship_to_city == city)
        
        results = query.all()
        
        pending_list = []
        for record in results:
            if record.dn_create_date:
                aging = (today - record.dn_create_date).days
                if days_gt is None or aging > days_gt:
                    pending_list.append({
                        "dn_no": str(record.dn_no),
                        "dealer": record.customer_name,
                        "warehouse": record.warehouse,
                        "city": record.ship_to_city,
                        "aging_days": aging,
                        "dn_date": record.dn_create_date.strftime("%Y-%m-%d"),
                        "quantity": record.dn_qty or 0,
                        "amount": record.dn_amount or 0
                    })
        
        pending_list.sort(key=lambda x: x["aging_days"], reverse=True)
        
        return {
            "type": "pending_delivery",
            "total_pending": len(pending_list),
            "total_quantity": sum(p["quantity"] for p in pending_list),
            "total_value": sum(p["amount"] for p in pending_list),
            "average_aging": round(sum(p["aging_days"] for p in pending_list) / max(1, len(pending_list)), 1),
            "oldest_pending": pending_list[0] if pending_list else None,
            "over_3_days": len([p for p in pending_list if p["aging_days"] > 3]),
            "over_7_days": len([p for p in pending_list if p["aging_days"] > 7]),
            "over_15_days": len([p for p in pending_list if p["aging_days"] > 15]),
            "top_pending": pending_list[:10]
        }
    finally:
        db.close()

# ==========================================================
# 3. POD AGING (Completed POD)
# ==========================================================

def calculate_pod_aging(dn_number: str = None, dealer_name: str = None,
                         warehouse: str = None, city: str = None,
                         days_gt: int = None) -> Dict[str, Any]:
    """POD Aging = POD Date - PGI Date"""
    db = SessionLocal()
    try:
        query = db.query(DeliveryReport).filter(
            DeliveryReport.pod_date.isnot(None),
            DeliveryReport.good_issue_date.isnot(None)
        )
        
        if dn_number:
            query = query.filter(DeliveryReport.dn_no == dn_number)
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        if warehouse:
            query = query.filter(DeliveryReport.warehouse == warehouse)
        if city:
            query = query.filter(DeliveryReport.ship_to_city == city)
        
        results = query.all()
        
        aging_list = []
        for record in results:
            aging = (record.pod_date - record.good_issue_date).days
            if days_gt is None or aging > days_gt:
                aging_list.append({
                    "dn_no": str(record.dn_no),
                    "dealer": record.customer_name,
                    "warehouse": record.warehouse,
                    "city": record.ship_to_city,
                    "aging_days": aging,
                    "pgi_date": record.good_issue_date.strftime("%Y-%m-%d"),
                    "pod_date": record.pod_date.strftime("%Y-%m-%d")
                })
        
        aging_list.sort(key=lambda x: x["aging_days"], reverse=True)
        
        if dn_number and aging_list:
            return {"dn": dn_number, "pod_aging_days": aging_list[0]["aging_days"]}
        
        return {
            "type": "pod_aging",
            "total_completed_pod": len(aging_list),
            "average_aging": round(sum(a["aging_days"] for a in aging_list) / max(1, len(aging_list)), 1),
            "highest_aging": aging_list[0] if aging_list else None,
            "over_7_days": len([a for a in aging_list if a["aging_days"] > 7]),
            "over_15_days": len([a for a in aging_list if a["aging_days"] > 15]),
            "over_30_days": len([a for a in aging_list if a["aging_days"] > 30]),
            "records": aging_list[:20]
        }
    finally:
        db.close()

# ==========================================================
# 4. PENDING POD AGING
# ==========================================================

def calculate_pending_pod_aging(days_gt: int = None, dealer_name: str = None,
                                  warehouse: str = None, city: str = None) -> Dict[str, Any]:
    """Pending POD Aging = Today - PGI Date (when POD not completed)"""
    db = SessionLocal()
    try:
        today = date.today()
        
        query = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        if warehouse:
            query = query.filter(DeliveryReport.warehouse == warehouse)
        if city:
            query = query.filter(DeliveryReport.ship_to_city == city)
        
        results = query.all()
        
        pending_list = []
        for record in results:
            aging = (today - record.good_issue_date).days
            if days_gt is None or aging > days_gt:
                pending_list.append({
                    "dn_no": str(record.dn_no),
                    "dealer": record.customer_name,
                    "warehouse": record.warehouse,
                    "city": record.ship_to_city,
                    "aging_days": aging,
                    "pgi_date": record.good_issue_date.strftime("%Y-%m-%d"),
                    "quantity": record.dn_qty or 0,
                    "amount": record.dn_amount or 0
                })
        
        pending_list.sort(key=lambda x: x["aging_days"], reverse=True)
        
        # Aggregate by dealer
        dealer_aggregation = {}
        for p in pending_list:
            dealer = p["dealer"]
            if dealer not in dealer_aggregation:
                dealer_aggregation[dealer] = {"total_aging": 0, "count": 0, "max_aging": 0}
            dealer_aggregation[dealer]["total_aging"] += p["aging_days"]
            dealer_aggregation[dealer]["count"] += 1
            dealer_aggregation[dealer]["max_aging"] = max(dealer_aggregation[dealer]["max_aging"], p["aging_days"])
        
        # Aggregate by warehouse
        warehouse_aggregation = {}
        for p in pending_list:
            wh = p["warehouse"] or "Unknown"
            if wh not in warehouse_aggregation:
                warehouse_aggregation[wh] = {"total_aging": 0, "count": 0, "max_aging": 0}
            warehouse_aggregation[wh]["total_aging"] += p["aging_days"]
            warehouse_aggregation[wh]["count"] += 1
            warehouse_aggregation[wh]["max_aging"] = max(warehouse_aggregation[wh]["max_aging"], p["aging_days"])
        
        # Aggregate by city
        city_aggregation = {}
        for p in pending_list:
            c = p["city"] or "Unknown"
            if c not in city_aggregation:
                city_aggregation[c] = {"total_aging": 0, "count": 0, "max_aging": 0}
            city_aggregation[c]["total_aging"] += p["aging_days"]
            city_aggregation[c]["count"] += 1
            city_aggregation[c]["max_aging"] = max(city_aggregation[c]["max_aging"], p["aging_days"])
        
        # Find highest
        highest_dealer = max(dealer_aggregation.items(), key=lambda x: x[1]["max_aging"]) if dealer_aggregation else None
        highest_warehouse = max(warehouse_aggregation.items(), key=lambda x: x[1]["max_aging"]) if warehouse_aggregation else None
        highest_city = max(city_aggregation.items(), key=lambda x: x[1]["max_aging"]) if city_aggregation else None
        
        return {
            "type": "pending_pod",
            "total_pending": len(pending_list),
            "total_quantity": sum(p["quantity"] for p in pending_list),
            "total_value": sum(p["amount"] for p in pending_list),
            "average_aging": round(sum(p["aging_days"] for p in pending_list) / max(1, len(pending_list)), 1),
            "highest_pending": pending_list[0] if pending_list else None,
            "highest_dealer": {"name": highest_dealer[0], "max_aging": highest_dealer[1]["max_aging"], "count": highest_dealer[1]["count"]} if highest_dealer else None,
            "highest_warehouse": {"name": highest_warehouse[0], "max_aging": highest_warehouse[1]["max_aging"], "count": highest_warehouse[1]["count"]} if highest_warehouse else None,
            "highest_city": {"name": highest_city[0], "max_aging": highest_city[1]["max_aging"], "count": highest_city[1]["count"]} if highest_city else None,
            "over_3_days": len([p for p in pending_list if p["aging_days"] > 3]),
            "over_7_days": len([p for p in pending_list if p["aging_days"] > 7]),
            "over_15_days": len([p for p in pending_list if p["aging_days"] > 15]),
            "over_30_days": len([p for p in pending_list if p["aging_days"] > 30]),
            "top_pending": pending_list[:10]
        }
    finally:
        db.close()

# ==========================================================
# 5. DN DETAILS
# ==========================================================

def get_dn_details_from_db(dn_number: str) -> Optional[Dict[str, Any]]:
    """Get DN details directly from database."""
    
    metrics["diagnostics"]["dn_lookup_attempts"] += 1
    
    if dn_number in dn_cache:
        metrics["diagnostics"]["dn_lookup_successes"] += 1
        return dn_cache[dn_number]
    
    db = None
    try:
        db = SessionLocal()
        normalized = normalize_dn(dn_number)
        
        logger.info(f"🔍 Searching for DN: {dn_number}")
        
        records = None
        strategy_used = None
        
        # Strategy 1: STRING exact match (for VARCHAR column)
        string_records = db.query(DeliveryReport).filter(
            DeliveryReport.dn_no == normalized
        ).all()
        
        if string_records:
            records = string_records
            strategy_used = "STRING_EXACT"
        
        # Strategy 2: INTEGER search
        if not records and normalized.isdigit():
            int_records = db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == str(int(normalized))
            ).all()
            if int_records:
                records = int_records
                strategy_used = "INTEGER"
        
        # Strategy 3: With .0 suffix
        if not records:
            dot_zero_records = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == f"{normalized}.0"
            ).all()
            if dot_zero_records:
                records = dot_zero_records
                strategy_used = "DOT_ZERO"
        
        # Strategy 4: CONTAINS
        if not records:
            like_records = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no.like(f"%{normalized}%")
            ).all()
            if like_records:
                records = like_records
                strategy_used = "CONTAINS"
        
        if not records:
            logger.warning(f"❌ DN {dn_number} not found")
            metrics["diagnostics"]["dn_lookup_failures"] += 1
            return None
        
        logger.info(f"✅ DN found via: {strategy_used}")
        
        first = records[0]
        dn_date = first.dn_create_date
        pod_date = first.pod_date
        pgi_date = first.good_issue_date
        today = date.today()
        
        # Calculate aging metrics
        delivery_aging = (pgi_date - dn_date).days if pgi_date and dn_date else 0
        pod_aging = (pod_date - pgi_date).days if pod_date and pgi_date else 0
        pending_pod_aging = (today - pgi_date).days if pgi_date and not pod_date else 0
        pending_delivery_aging = (today - dn_date).days if dn_date and not pgi_date else 0
        
        # Determine status
        if pod_date:
            pod_status = "Completed"
            delivery_status = "Delivered"
        elif pgi_date:
            pod_status = "Pending POD"
            delivery_status = "Dispatched"
        else:
            pod_status = "Delivery Pending"
            delivery_status = "Pending"
        
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
            "pgi_date": pgi_date.strftime("%Y-%m-%d") if pgi_date else "Not Dispatched",
            "pod_date": pod_date.strftime("%Y-%m-%d") if pod_date else "Not Received",
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "pending_delivery_aging_days": pending_delivery_aging,
            "pending_pod_aging_days": pending_pod_aging,
            "delivery_status": delivery_status,
            "pod_status": pod_status,
            "total_models": len(unique_models),
            "models_list": list(unique_models)[:5],
            "total_quantity": total_quantity,
            "total_amount": total_amount,
            "products": products[:5]
        }
        
        dn_cache[dn_number] = result
        metrics["diagnostics"]["dn_lookup_successes"] += 1
        
        return result
        
    except Exception as e:
        logger.error(f"❌ DN lookup error: {e}")
        metrics["diagnostics"]["dn_lookup_failures"] += 1
        return None
    finally:
        if db:
            db.close()

# ==========================================================
# 6. DEALER PERFORMANCE
# ==========================================================

def get_dealer_performance(dealer_name: str) -> Optional[Dict[str, Any]]:
    """Get complete dealer performance dashboard"""
    db = SessionLocal()
    try:
        records = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).all()
        
        if not records:
            return None
        
        today = date.today()
        total_dns = set()
        total_quantity = 0
        total_amount = 0.0
        delivered_dns = 0
        pending_deliveries = 0
        pending_pod = 0
        completed_pod = 0
        
        delivery_aging_list = []
        pod_aging_list = []
        pending_pod_aging_list = []
        
        for r in records:
            dn_no = normalize_dn(r.dn_no)
            if dn_no:
                total_dns.add(dn_no)
            
            total_quantity += int(r.dn_qty or 0)
            total_amount += float(r.dn_amount or 0)
            
            # Delivery status
            if r.delivery_status == "Delivered":
                delivered_dns += 1
            elif r.delivery_status == "Dispatched":
                pending_pod += 1
            else:
                pending_deliveries += 1
            
            # Aging calculations
            if r.good_issue_date and r.dn_create_date:
                delivery_aging_list.append((r.good_issue_date - r.dn_create_date).days)
            if r.pod_date and r.good_issue_date:
                pod_aging_list.append((r.pod_date - r.good_issue_date).days)
            if r.good_issue_date and not r.pod_date:
                pending_pod_aging_list.append((today - r.good_issue_date).days)
        
        result = {
            "dealer_name": records[0].customer_name,
            "dealer_code": records[0].customer_code or "N/A",
            "city": records[0].ship_to_city or "N/A",
            "sales_office": records[0].division or "N/A",
            "warehouse": records[0].warehouse or "N/A",
            "total_dns": len(total_dns),
            "total_quantity": total_quantity,
            "total_amount": total_amount,
            "delivered_dns": delivered_dns,
            "pending_deliveries": pending_deliveries,
            "pending_pod": pending_pod,
            "completed_pod": completed_pod,
            "avg_delivery_aging": round(sum(delivery_aging_list) / max(1, len(delivery_aging_list)), 1) if delivery_aging_list else 0,
            "avg_pod_aging": round(sum(pod_aging_list) / max(1, len(pod_aging_list)), 1) if pod_aging_list else 0,
            "avg_pending_pod_aging": round(sum(pending_pod_aging_list) / max(1, len(pending_pod_aging_list)), 1) if pending_pod_aging_list else 0,
            "completion_rate": round(delivered_dns / max(1, len(total_dns)) * 100, 1)
        }
        
        # Health score
        health_score = 100
        health_score -= (pending_deliveries * 5)
        health_score -= (pending_pod * 2)
        health_score = max(0, min(100, health_score))
        
        result["health_score"] = health_score
        result["health_status"] = "Excellent" if health_score >= 80 else "Good" if health_score >= 60 else "Needs Attention"
        result["health_emoji"] = "🟢" if health_score >= 80 else "🟡" if health_score >= 60 else "🔴"
        
        return result
        
    finally:
        db.close()

# ==========================================================
# 7. KPI DASHBOARD
# ==========================================================

def get_kpi_dashboard() -> Dict[str, Any]:
    """Get overall KPI dashboard"""
    db = SessionLocal()
    try:
        today = date.today()
        
        # Base counts
        total_records = db.query(DeliveryReport).count()
        total_dns = db.query(DeliveryReport.dn_no).distinct().count()
        total_quantity = db.query(func.sum(DeliveryReport.dn_qty)).scalar() or 0
        total_amount = db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0.0
        
        # Delivery status counts
        delivered_count = db.query(DeliveryReport).filter(DeliveryReport.delivery_status == "Delivered").count()
        dispatched_count = db.query(DeliveryReport).filter(DeliveryReport.delivery_status == "Dispatched").count()
        pending_count = db.query(DeliveryReport).filter(DeliveryReport.delivery_status == "Pending").count()
        
        # POD status
        pod_completed = db.query(DeliveryReport).filter(DeliveryReport.pod_date.isnot(None)).count()
        pod_pending = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        ).count()
        
        # Aging calculations
        delivery_aging = calculate_delivery_aging()
        pending_delivery_aging = calculate_pending_delivery_aging()
        pod_aging = calculate_pod_aging()
        pending_pod_aging = calculate_pending_pod_aging()
        
        # Value calculations
        delivered_amount = db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.delivery_status == "Delivered"
        ).scalar() or 0.0
        
        pending_amount = db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.delivery_status == "Pending"
        ).scalar() or 0.0
        
        return {
            "total_dns": total_dns,
            "total_quantity": int(total_quantity),
            "total_revenue": total_amount,
            "delivered_units": delivered_count,
            "delivered_revenue": delivered_amount,
            "pending_units": pending_count,
            "pending_revenue": pending_amount,
            "pod_completion_rate": round(pod_completed / max(1, pod_completed + pod_pending) * 100, 1),
            "delivery_completion_rate": round(delivered_count / max(1, total_records) * 100, 1),
            "avg_delivery_aging": delivery_aging.get("average_aging", 0),
            "avg_pod_aging": pod_aging.get("average_aging", 0),
            "avg_pending_pod_aging": pending_pod_aging.get("average_aging", 0),
            "pending_pod_count": pending_pod_aging.get("total_pending", 0),
            "pending_pod_value": pending_pod_aging.get("total_value", 0),
            "pending_pod_units": pending_pod_aging.get("total_quantity", 0),
            "pending_delivery_count": pending_delivery_aging.get("total_pending", 0),
            "pending_delivery_value": pending_delivery_aging.get("total_value", 0)
        }
    finally:
        db.close()

# ==========================================================
# 8. CONTROL TOWER ALERTS
# ==========================================================

def get_control_tower_alerts() -> Dict[str, Any]:
    """Get critical logistics alerts"""
    db = SessionLocal()
    try:
        today = date.today()
        
        # DNs stuck > 15 days (pending delivery)
        stuck_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.is_(None),
            DeliveryReport.dn_create_date.isnot(None)
        ).all()
        
        stuck_15_days = []
        stuck_30_days = []
        
        for record in stuck_deliveries:
            days = (today - record.dn_create_date).days
            if days > 15:
                stuck_15_days.append({
                    "dn_no": str(record.dn_no),
                    "dealer": record.customer_name,
                    "days": days,
                    "amount": record.dn_amount or 0
                })
            if days > 30:
                stuck_30_days.append({
                    "dn_no": str(record.dn_no),
                    "dealer": record.customer_name,
                    "days": days,
                    "amount": record.dn_amount or 0
                })
        
        # PODs pending > 15 days
        pod_pending = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        ).all()
        
        delayed_pod = []
        for record in pod_pending:
            days = (today - record.good_issue_date).days
            if days > 15:
                delayed_pod.append({
                    "dn_no": str(record.dn_no),
                    "dealer": record.customer_name,
                    "days": days,
                    "amount": record.dn_amount or 0
                })
        
        # Get dealers with highest pending POD
        dealer_pod_data = {}
        for record in pod_pending:
            dealer = record.customer_name or "Unknown"
            days = (today - record.good_issue_date).days
            if dealer not in dealer_pod_data:
                dealer_pod_data[dealer] = {"max_days": 0, "count": 0, "total_amount": 0}
            dealer_pod_data[dealer]["max_days"] = max(dealer_pod_data[dealer]["max_days"], days)
            dealer_pod_data[dealer]["count"] += 1
            dealer_pod_data[dealer]["total_amount"] += record.dn_amount or 0
        
        top_dealer = max(dealer_pod_data.items(), key=lambda x: x[1]["max_days"]) if dealer_pod_data else None
        
        # Summary
        pending_delivery = calculate_pending_delivery_aging()
        pending_pod = calculate_pending_pod_aging()
        
        return {
            "critical_alerts": {
                "total_stuck_deliveries": len(stuck_15_days),
                "total_critical_stuck": len(stuck_30_days),
                "total_delayed_pod": len(delayed_pod),
                "stuck_15_days": stuck_15_days[:10],
                "stuck_30_days": stuck_30_days[:5],
                "delayed_pod_15_days": delayed_pod[:10]
            },
            "top_dealer_pending_pod": {"name": top_dealer[0], "max_days": top_dealer[1]["max_days"], "count": top_dealer[1]["count"]} if top_dealer else None,
            "summary": {
                "total_pending_deliveries": pending_delivery.get("total_pending", 0),
                "total_pending_pod": pending_pod.get("total_pending", 0),
                "pending_delivery_value": pending_delivery.get("total_value", 0),
                "pending_pod_value": pending_pod.get("total_value", 0)
            }
        }
    finally:
        db.close()

# ==========================================================
# 9. WAREHOUSE & CITY ANALYTICS
# ==========================================================

def get_warehouse_analytics(warehouse_name: str = None) -> Dict[str, Any]:
    """Get warehouse-wise analytics"""
    db = SessionLocal()
    try:
        query = db.query(
            DeliveryReport.warehouse,
            func.count(DeliveryReport.dn_no.distinct()).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).label("total_quantity"),
            func.sum(DeliveryReport.dn_amount).label("total_amount"),
            func.sum(func.nullif(DeliveryReport.dn_amount, 0)).filter(DeliveryReport.delivery_status == "Delivered").label("delivered_amount")
        ).group_by(DeliveryReport.warehouse)
        
        if warehouse_name:
            query = query.filter(DeliveryReport.warehouse == warehouse_name)
        
        results = query.all()
        
        warehouse_data = []
        for r in results:
            if r[0]:  # warehouse name exists
                warehouse_data.append({
                    "warehouse": r[0],
                    "total_dns": r[1],
                    "total_quantity": int(r[2] or 0),
                    "total_amount": float(r[3] or 0),
                    "delivered_amount": float(r[4] or 0)
                })
        
        return {"warehouses": warehouse_data}
    finally:
        db.close()

def get_city_analytics(city_name: str = None) -> Dict[str, Any]:
    """Get city-wise analytics"""
    db = SessionLocal()
    try:
        query = db.query(
            DeliveryReport.ship_to_city,
            func.count(DeliveryReport.dn_no.distinct()).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).label("total_quantity"),
            func.sum(DeliveryReport.dn_amount).label("total_amount")
        ).group_by(DeliveryReport.ship_to_city)
        
        if city_name:
            query = query.filter(DeliveryReport.ship_to_city.ilike(f"%{city_name}%"))
        
        results = query.all()
        
        city_data = []
        for r in results:
            if r[0]:  # city name exists
                city_data.append({
                    "city": r[0],
                    "total_dns": r[1],
                    "total_quantity": int(r[2] or 0),
                    "total_amount": float(r[3] or 0)
                })
        
        return {"cities": city_data}
    finally:
        db.close()

# ==========================================================
# 10. GROQ AI NATURAL LANGUAGE PROCESSING
# ==========================================================

def process_with_groq(message: str, context_data: Dict[str, Any]) -> Optional[str]:
    """Process natural language query with GROQ AI"""
    if not GROQ_ENABLED or not GROQ_CLIENT:
        return None
    
    try:
        metrics["service_usage"]["groq_calls"] += 1
        
        # Build system prompt with context
        system_prompt = f"""You are a logistics AI assistant for a delivery network. 
You have access to the following real-time data:

KPI Dashboard:
- Total DNs: {context_data.get('kpi', {}).get('total_dns', 'N/A')}
- Total Revenue: PKR {context_data.get('kpi', {}).get('total_revenue', 0):,.0f}
- Delivery Completion: {context_data.get('kpi', {}).get('delivery_completion_rate', 0)}%
- POD Completion: {context_data.get('kpi', {}).get('pod_completion_rate', 0)}%
- Pending Deliveries: {context_data.get('kpi', {}).get('pending_delivery_count', 0)}
- Pending POD: {context_data.get('kpi', {}).get('pending_pod_count', 0)}

Control Tower Alerts:
- Stuck >15 days: {context_data.get('alerts', {}).get('critical_alerts', {}).get('total_stuck_deliveries', 0)}
- Delayed POD >15 days: {context_data.get('alerts', {}).get('critical_alerts', {}).get('total_delayed_pod', 0)}

Answer user questions based on this data. Be concise, use bullet points when helpful, and include PKR amounts with proper formatting.
If asking about a specific DN, dealer, warehouse, or city, tell the user to provide the specific name/number.
"""
        
        response = GROQ_CLIENT.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            temperature=0.3,
            max_tokens=500
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"❌ GROQ API error: {e}")
        metrics["service_failures"]["groq_service"] += 1
        return None

# ==========================================================
# RESPONSE FORMATTERS
# ==========================================================

def format_dn_response(details: Dict[str, Any]) -> str:
    """Format DN details response"""
    products_text = ""
    for idx, p in enumerate(details.get('products', [])[:3], 1):
        products_text += f"\n   {idx}. {p['model']} - Qty: {p['quantity']}"
    
    return f"""
📦 *DN DETAILS*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN Number:* {details['dn_no']}
📅 Creation Date: {details['dn_date']}
🚚 PGI Date: {details['pgi_date']}
📋 POD Date: {details['pod_date']}

🏪 *DEALER INFO*
• Name: {details['dealer_name']}
• City: {details['city']}
• Warehouse: {details['warehouse']}

📦 *PRODUCTS*{products_text}

📊 *SUMMARY*
• Models: {details['total_models']}
• Quantity: {details['total_quantity']:,}
• Amount: PKR {details['total_amount']:,.0f}

⏱️ *AGING METRICS*
• Delivery Aging: {details['delivery_aging_days']} days
• POD Aging: {details['pod_aging_days']} days
• Pending POD Aging: {details['pending_pod_aging_days']} days

✅ *STATUS*
• Delivery: {details['delivery_status']}
• POD: {details['pod_status']}

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Help` for more commands
"""

def format_dealer_response(details: Dict[str, Any]) -> str:
    """Format dealer performance response"""
    return f"""
🏪 *DEALER DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *{details['dealer_name']}*
📍 City: {details['city']}
🏢 Office: {details['sales_office']}
🏭 Warehouse: {details['warehouse']}

📊 *PERFORMANCE*
• Total DNs: {details['total_dns']}
• Units: {details['total_quantity']:,}
• Revenue: PKR {details['total_amount']:,.0f}
• Completion Rate: {details['completion_rate']}%

⚠️ *PENDING*
• Deliveries: {details['pending_deliveries']}
• PODs: {details['pending_pod']}

⏱️ *AGING*
• Avg Delivery: {details['avg_delivery_aging']} days
• Avg POD: {details['avg_pod_aging']} days
• Avg Pending POD: {details['avg_pending_pod_aging']} days

{details['health_emoji']} *Health: {details['health_score']} ({details['health_status']})*

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Help` for more commands
"""

def format_kpi_response(kpi: Dict[str, Any]) -> str:
    """Format KPI dashboard response"""
    return f"""
📊 *KPI DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📦 *VOLUME METRICS*
• Total DNs: {kpi['total_dns']:,}
• Total Units: {kpi['total_quantity']:,}
• Total Revenue: PKR {kpi['total_revenue']:,.0f}

✅ *COMPLETION RATES*
• Delivery: {kpi['delivery_completion_rate']}%
• POD: {kpi['pod_completion_rate']}%

⚠️ *PENDING ITEMS*
• Deliveries: {kpi['pending_delivery_count']:,} (PKR {kpi['pending_delivery_value']:,.0f})
• PODs: {kpi['pending_pod_count']:,} (PKR {kpi['pending_pod_value']:,.0f})

⏱️ *AVERAGE AGING*
• Delivery: {kpi['avg_delivery_aging']} days
• POD: {kpi['avg_pod_aging']} days
• Pending POD: {kpi['avg_pending_pod_aging']} days

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Control tower` for alerts
"""

def format_control_tower_response(alerts: Dict[str, Any]) -> str:
    """Format control tower alerts response"""
    critical = alerts.get('critical_alerts', {})
    
    response = f"""
🚨 *CONTROL TOWER - CRITICAL ALERTS*
━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ *STUCK DELIVERIES (>15 days)*
• Total: {critical.get('total_stuck_deliveries', 0)}
"""
    
    for stuck in critical.get('stuck_15_days', [])[:5]:
        response += f"   🔴 DN {stuck['dn_no']}: {stuck['days']} days (PKR {stuck['amount']:,.0f})\n"
    
    response += f"""
📋 *DELAYED POD (>15 days)*
• Total: {critical.get('total_delayed_pod', 0)}
"""
    
    for pod in critical.get('delayed_pod_15_days', [])[:5]:
        response += f"   ⏳ DN {pod['dn_no']}: {pod['days']} days (PKR {pod['amount']:,.0f})\n"
    
    if alerts.get('top_dealer_pending_pod'):
        td = alerts['top_dealer_pending_pod']
        response += f"""
🏪 *HIGHEST PENDING POD*
• Dealer: {td['name']}
• Max Aging: {td['max_days']} days
• Count: {td['count']} DNs

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `KPI` for overall dashboard
"""
    
    return response

# ==========================================================
# MAIN PROCESSING FUNCTION
# ==========================================================

def process_message_direct(message: str) -> str:
    """Process message directly - UPDATED with all business rules"""
    msg_lower = message.lower().strip()
    
    # Help command
    if msg_lower in ["help", "menu", "commands", "what can you do", "start"]:
        return """
🤖 *AI LOGISTICS ASSISTANT - COMPLETE COMMANDS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN QUERIES*
• `DN 6243610262` - Complete DN details
• `Status of DN 6243610262` - DN status only
• `Products in DN 6243610262` - Products list
• `Delivery aging of DN 6243610262` - Aging details

📊 *AGING REPORTS*
• `Delivery aging` - Overall delivery aging stats
• `Pending delivery aging` - Pending delivery report
• `POD aging` - Completed POD aging
• `Pending POD aging` - Pending POD report
• `Pending POD > 15 days` - Filtered by days

🏪 *DEALER QUERIES*
• `[Dealer name]` - Dealer dashboard
• `Dealer ranking by revenue` - Top dealers
• `Dealer-wise pending POD` - Per dealer POD status

🏭 *WAREHOUSE & CITY*
• `Warehouse sales` - Warehouse performance
• `Sales in Lahore` - City-wise sales
• `City-wise pending POD` - Per city POD status

📈 *KPI & CONTROL*
• `KPI dashboard` - Overall KPIs
• `Control tower` - Critical alerts
• `Executive dashboard` - Executive summary

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # ========== DN QUERIES ==========
    if is_dn_number(message) or ("dn" in msg_lower and extract_dn_from_message(message)):
        dn = extract_dn_from_message(message) or message.strip()
        details = get_dn_details_from_db(dn)
        if details:
            return format_dn_response(details)
        else:
            return f"""
📦 *DN SEARCH*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN Number:* {dn}

❌ Not found in database

💡 Check the number or type `Help` for assistance
"""
    
    # ========== DELIVERY AGING ==========
    if "delivery aging" in msg_lower:
        if "dn" in msg_lower:
            dn = extract_dn_from_message(message)
            if dn:
                result = calculate_delivery_aging(dn_number=dn)
                if "delivery_aging_days" in result:
                    return f"""
📦 *DELIVERY AGING*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN:* {dn}
⏱️ *Delivery Aging:* {result['delivery_aging_days']} days

📅 *Formula:* PGI Date - DN Creation Date
"""
        result = calculate_delivery_aging()
        return f"""
📊 *DELIVERY AGING REPORT*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 *STATISTICS*
• Completed Deliveries: {result['total_records']}
• Average Aging: {result['average_aging']} days
• Highest: {result['highest_aging']['aging_days'] if result['highest_aging'] else 0} days
• Lowest: {result['lowest_aging']['aging_days'] if result['lowest_aging'] else 0} days

⚠️ *ALERTS*
• >3 days: {result['over_3_days']}
• >7 days: {result['over_7_days']}
• >15 days: {result['over_15_days']}

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # ========== PENDING DELIVERY ==========
    if "pending delivery" in msg_lower or "pending deliveries" in msg_lower:
        days = extract_days_from_message(message)
        result = calculate_pending_delivery_aging(days_gt=days if days > 0 else None)
        
        if days > 0:
            return f"""
⏳ *PENDING DELIVERIES > {days} DAYS*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Total: {result['over_{}_days'.format(days)] if days in [3,7,15] else len([p for p in result['top_pending'] if p['aging_days'] > days])}
💰 Value: PKR {result['total_value']:,.0f}
📦 Units: {result['total_quantity']:,}

🔴 *TOP PRIORITIES*
"""
        else:
            return f"""
⏳ *PENDING DELIVERIES SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Total Pending: {result['total_pending']}
💰 Total Value: PKR {result['total_value']:,.0f}
📦 Total Units: {result['total_quantity']:,}
⏱️ Average Aging: {result['average_aging']} days

⚠️ *BREAKDOWN*
• >3 days: {result['over_3_days']}
• >7 days: {result['over_7_days']}
• >15 days: {result['over_15_days']}

🔴 *OLDEST PENDING*
• DN: {result['oldest_pending']['dn_no'] if result['oldest_pending'] else 'N/A'}
• Aging: {result['oldest_pending']['aging_days'] if result['oldest_pending'] else 0} days

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # ========== POD AGING ==========
    if "pod aging" in msg_lower:
        if "dn" in msg_lower:
            dn = extract_dn_from_message(message)
            if dn:
                result = calculate_pod_aging(dn_number=dn)
                if "pod_aging_days" in result:
                    return f"""
📋 *POD AGING*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN:* {dn}
⏱️ *POD Aging:* {result['pod_aging_days']} days

📅 *Formula:* POD Date - PGI Date
"""
        result = calculate_pod_aging()
        return f"""
📋 *POD AGING REPORT*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 *STATISTICS*
• Completed PODs: {result['total_completed_pod']}
• Average Aging: {result['average_aging']} days
• Highest: {result['highest_aging']['aging_days'] if result['highest_aging'] else 0} days

⚠️ *ALERTS*
• >7 days: {result['over_7_days']}
• >15 days: {result['over_15_days']}
• >30 days: {result['over_30_days']}

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # ========== PENDING POD ==========
    if "pending pod" in msg_lower:
        days = extract_days_from_message(message)
        result = calculate_pending_pod_aging(days_gt=days if days > 0 else None)
        
        if "count" in msg_lower:
            return f"📋 *Pending POD Count:* {result['total_pending']}"
        if "value" in msg_lower:
            return f"💰 *Pending POD Value:* PKR {result['total_value']:,.0f}"
        if "units" in msg_lower:
            return f"📦 *Pending POD Units:* {result['total_quantity']:,}"
        if "dealer" in msg_lower and "highest" in msg_lower:
            hd = result.get('highest_dealer')
            if hd:
                return f"""
🏪 *HIGHEST PENDING POD - DEALER*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *{hd['name']}*
⏱️ Max Aging: {hd['max_aging']} days
📋 Pending DNs: {hd['count']}
"""
        if "warehouse" in msg_lower and "highest" in msg_lower:
            hw = result.get('highest_warehouse')
            if hw:
                return f"""
🏭 *HIGHEST PENDING POD - WAREHOUSE*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *{hw['name']}*
⏱️ Max Aging: {hw['max_aging']} days
📋 Pending DNs: {hw['count']}
"""
        if "city" in msg_lower and "highest" in msg_lower:
            hc = result.get('highest_city')
            if hc:
                return f"""
📍 *HIGHEST PENDING POD - CITY*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *{hc['name']}*
⏱️ Max Aging: {hc['max_aging']} days
📋 Pending DNs: {hc['count']}
"""
        
        return f"""
📋 *PENDING POD REPORT*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Total Pending: {result['total_pending']}
💰 Total Value: PKR {result['total_value']:,.0f}
📦 Total Units: {result['total_quantity']:,}
⏱️ Average Aging: {result['average_aging']} days

⚠️ *BREAKDOWN*
• >3 days: {result['over_3_days']}
• >7 days: {result['over_7_days']}
• >15 days: {result['over_15_days']}
• >30 days: {result['over_30_days']}

🔴 *HIGHEST*
• DN: {result['highest_pending']['dn_no'] if result['highest_pending'] else 'N/A'}
• Aging: {result['highest_pending']['aging_days'] if result['highest_pending'] else 0} days

🏪 *Top Dealer: {result['highest_dealer']['name'] if result['highest_dealer'] else 'N/A'} ({result['highest_dealer']['max_aging'] if result['highest_dealer'] else 0} days)

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # ========== DEALER QUERIES ==========
    if "dealer ranking" in msg_lower or "top dealer" in msg_lower:
        # This would need additional implementation
        return """
🏪 *DEALER RANKINGS*
━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 To see dealer rankings, please specify:
• `Dealer ranking by revenue`
• `Dealer ranking by units`
• `Dealer ranking by pending POD`

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # ========== WAREHOUSE QUERIES ==========
    if "warehouse" in msg_lower:
        if "sales" in msg_lower or "performance" in msg_lower:
            result = get_warehouse_analytics()
            response = "🏭 *WAREHOUSE PERFORMANCE*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for wh in result.get('warehouses', [])[:10]:
                response += f"📌 *{wh['warehouse']}*\n"
                response += f"   DNs: {wh['total_dns']} | Units: {wh['total_quantity']:,} | Revenue: PKR {wh['total_amount']:,.0f}\n\n"
            return response
    
    # ========== CITY QUERIES ==========
    if "sales in" in msg_lower or "city" in msg_lower:
        for city in ["lahore", "karachi", "islamabad", "rawalpindi", "faisalabad"]:
            if city in msg_lower:
                result = get_city_analytics(city.title())
                cities = result.get('cities', [])
                if cities:
                    c = cities[0]
                    return f"""
📍 *SALES IN {city.upper()}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Total DNs: {c['total_dns']}
📦 Total Units: {c['total_quantity']:,}
💰 Total Revenue: PKR {c['total_amount']:,.0f}

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # ========== KPI DASHBOARD ==========
    if any(kw in msg_lower for kw in ["kpi", "dashboard", "overall", "executive dashboard", "executive summary"]):
        kpi = get_kpi_dashboard()
        return format_kpi_response(kpi)
    
    # ========== CONTROL TOWER ==========
    if any(kw in msg_lower for kw in ["control tower", "urgent", "critical", "alerts", "stuck", "need attention"]):
        alerts = get_control_tower_alerts()
        return format_control_tower_response(alerts)
    
    # ========== PGI QUERIES ==========
    if "pgi" in msg_lower:
        if "today" in msg_lower:
            return "🚚 *PGI Today* - Feature coming soon"
        if "pending" in msg_lower:
            result = calculate_pending_delivery_aging()
            return f"🚚 *Pending PGI:* {result['total_pending']} DNs"
    
    # ========== POD QUERIES ==========
    if "pod" in msg_lower:
        if "completed today" in msg_lower:
            return "✅ *POD Completed Today* - Feature coming soon"
        if "completion" in msg_lower and "%" in msg_lower:
            kpi = get_kpi_dashboard()
            return f"📋 *POD Completion Rate:* {kpi['pod_completion_rate']}%"
    
    # ========== PRODUCT/MODEL QUERIES ==========
    if any(kw in msg_lower for kw in ["model", "product", "hrf", "hwm"]):
        # Extract model from message
        model_match = re.search(r'([A-Z0-9-]{5,20})', message.upper())
        if model_match:
            model = model_match.group(1)
            db = SessionLocal()
            try:
                records = db.query(DeliveryReport).filter(
                    DeliveryReport.customer_model.ilike(f"%{model}%")
                ).all()
                if records:
                    total_qty = sum(r.dn_qty or 0 for r in records)
                    total_amt = sum(r.dn_amount or 0 for r in records)
                    return f"""
📦 *MODEL: {model}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Total Units: {total_qty:,}
💰 Total Revenue: PKR {total_amt:,.0f}
📋 DNs: {len(set(r.dn_no for r in records))}

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            finally:
                db.close()
    
    # ========== DEALER NAME (FALLBACK) ==========
    dealer_name = extract_dealer_from_message(message)
    if dealer_name and len(dealer_name) > 3:
        details = get_dealer_performance(dealer_name)
        if details:
            return format_dealer_response(details)
    
    # ========== TRY GROQ AI FOR COMPLEX QUERIES ==========
    if GROQ_ENABLED and len(msg_lower) > 10:
        context_data = {
            "kpi": get_kpi_dashboard(),
            "alerts": get_control_tower_alerts()
        }
        groq_response = process_with_groq(message, context_data)
        if groq_response:
            return groq_response + "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 Type `Help` for available commands"
    
    # ========== DEFAULT RESPONSE ==========
    return """
🤖 *AI LOGISTICS ASSISTANT*

I can help you with:

🔢 *DN Tracking* - Send any 10+ digit number
📊 *Aging Reports* - Delivery, POD, Pending
🏪 *Dealer Analytics* - Send dealer name
🏭 *Warehouse/City* - Warehouse sales, City sales
📈 *KPI Dashboard* - Type `KPI dashboard`
🚨 *Control Tower* - Type `Control tower`

Type `Help` for complete command list
"""

# ==========================================================
# WEBHOOK ENDPOINTS
# ==========================================================

def _auto_cleanup_if_needed(request_id: str):
    current_time = time.time()
    total_requests = metrics["total_requests"]
    
    if total_requests > 0 and total_requests % AUTO_CLEANUP_INTERVAL == 0:
        if current_time - metrics.get("last_cleanup", 0) > 60:
            logger.info(f"Auto cleanup triggered")
            old_size = len(processed_messages)
            processed_messages.clear()
            metrics["last_cleanup"] = current_time

def _check_rate_limit(phone_number: str, request_id: str) -> bool:
    current_time = time.time()
    timestamps = rate_limit_cache.get(phone_number, [])
    timestamps = [t for t in timestamps if current_time - t < RATE_LIMIT_WINDOW]
    
    if len(timestamps) >= RATE_LIMIT_MAX_MESSAGES:
        logger.warning(f"Rate limit exceeded for {phone_number}")
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
    send_start_time = time.time()
    
    if not WHATSAPP_SERVICE_AVAILABLE:
        logger.error(f"WhatsApp service not available")
        return {"success": False, "error": "Service not available"}
    
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.error(f"WhatsApp credentials missing")
        return {"success": False, "error": "Missing credentials"}
    
    if not message or not message.strip():
        message = "✅ Request processed successfully"
    
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
    
    for attempt in range(MAX_RETRIES):
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: send_text_message(
                        phone_number, 
                        message, 
                        message_id=context_msg_id, 
                        request_id=request_id
                    ) if context_msg_id else send_text_message(
                        phone_number, 
                        message, 
                        request_id=request_id
                    )
                ),
                timeout=SEND_MESSAGE_TIMEOUT
            )
            
            if result.get("success"):
                send_duration = (time.time() - send_start_time) * 1000
                logger.info(f"✅ Message sent in {send_duration:.0f}ms")
                return result
            
            if attempt < MAX_RETRIES - 1 and _should_retry(result.get('status_code', 0)):
                logger.warning(f"Retry {attempt + 1} for {phone_number}")
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            
            return result
            
        except asyncio.TimeoutError:
            logger.error(f"⏰ Timeout sending message after {SEND_MESSAGE_TIMEOUT}s")
            metrics["timeout_requests"] += 1
            
            if attempt < MAX_RETRIES - 1:
                logger.info(f"Retrying... (attempt {attempt + 2})")
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            
            return {"success": False, "error": f"Request timeout after {SEND_MESSAGE_TIMEOUT}s"}
            
        except Exception as e:
            logger.exception(f"Send attempt {attempt + 1} failed: {e}")
            
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}

def _should_retry(status_code: int) -> bool:
    retryable_statuses = {429, 500, 502, 503, 504}
    return status_code in retryable_statuses

def process_message_with_service(message: str, user_id: str = "guest") -> str:
    process_start = time.time()
    
    if AI_SERVICE_AVAILABLE:
        try:
            ensure_services_initialized()
            response = process_whatsapp_query(
                question=message,
                session_factory=None,
                phone_number=user_id,
                user_id=user_id,
                request_id=None
            )
            process_time = (time.time() - process_start) * 1000
            metrics["service_usage"]["ai_service_calls"] += 1
            logger.info(f"✅ AI Service processed in {process_time:.0f}ms")
            return response
        except Exception as e:
            logger.error(f"❌ AI Service failed: {e}")
            metrics["service_failures"]["ai_service"] += 1
    
    metrics["service_usage"]["direct_db_calls"] += 1
    return process_message_direct(message)

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
    
    logger.info(f"📨 Webhook received (v35.0 - GROQ AI Integration)")
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
            
            ai_start = time.time()
            response = process_message_with_service(user_message, phone_number)
            ai_duration = (time.time() - ai_start) * 1000
            logger.info(f"🤖 Processing: {ai_duration:.0f}ms")
            
            await send_whatsapp_message(phone_number, response, request_id, msg_id)
            processed_count += 1
        
        processing_time = (time.time() - start_time) * 1000
        
        logger.info(f"✅ Done: {processing_time:.0f}ms, {processed_count} messages")
        
        return {
            "success": True,
            "request_id": request_id,
            "processing_time_ms": round(processing_time, 2),
            "messages_processed": processed_count,
            "groq_enabled": GROQ_ENABLED,
            "groq_model": GROQ_MODEL if GROQ_ENABLED else None
        }
        
    except asyncio.TimeoutError:
        logger.error(f"Request body timeout")
        metrics["timeout_requests"] += 1
        return {"success": False, "error": "Request timeout", "request_id": request_id}
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return {"success": False, "error": str(e), "request_id": request_id}

# ==========================================================
# DEBUG ENDPOINTS
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
        "version": "35.0",
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "GROQ_AI_INTEGRATED",
        "groq": {
            "enabled": GROQ_ENABLED,
            "model": GROQ_MODEL if GROQ_ENABLED else None,
            "api_key_configured": bool(GROQ_API_KEY)
        },
        "services": {
            "whatsapp_service": {"available": WHATSAPP_SERVICE_AVAILABLE},
            "database": {"connected": db_healthy},
            "ai_service": {"available": AI_SERVICE_AVAILABLE}
        },
        "metrics": {
            "total_requests": metrics["total_requests"],
            "groq_calls": metrics["service_usage"]["groq_calls"],
            "dn_lookup_success_rate": round(
                metrics["diagnostics"]["dn_lookup_successes"] / max(1, metrics["diagnostics"]["dn_lookup_attempts"]) * 100, 2
            )
        }
    }

@router.get("/ping")
async def ping():
    return {
        "pong": True,
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "groq_ai_integrated",
        "groq_enabled": GROQ_ENABLED,
        "version": "35.0"
    }

@router.get("/cache/clear")
async def clear_cache():
    old_size = len(dn_cache)
    dn_cache.clear()
    query_cache.clear()
    return {"success": True, "cleared": old_size}

# ==========================================================
# GROQ CONFIGURATION ENDPOINTS
# ==========================================================

@router.get("/groq/status")
async def groq_status():
    """Check GROQ AI status"""
    return {
        "groq_available": GROQ_AVAILABLE,
        "groq_enabled": GROQ_ENABLED,
        "api_key_configured": bool(GROQ_API_KEY),
        "model": GROQ_MODEL if GROQ_ENABLED else None,
        "total_calls": metrics["service_usage"]["groq_calls"],
        "failures": metrics["service_failures"]["groq_service"]
    }

@router.post("/groq/test")
async def test_groq():
    """Test GROQ AI connection"""
    if not GROQ_ENABLED:
        return {"error": "GROQ not enabled. Check API key and SDK installation."}
    
    try:
        response = GROQ_CLIENT.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": "Say 'GROQ AI is working!'"}],
            max_tokens=50
        )
        return {
            "success": True,
            "response": response.choices[0].message.content,
            "model": GROQ_MODEL
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 80)
logger.info("🚀 WEBHOOK v35.0 - GROQ AI INTEGRATION")
logger.info("=" * 80)
logger.info("")
logger.info("   FEATURES:")
logger.info("   ✅ Delivery Aging (Completed Deliveries)")
logger.info("   ✅ Pending Delivery Aging")
logger.info("   ✅ POD Aging (Completed POD)")
logger.info("   ✅ Pending POD Aging")
logger.info("   ✅ DN Details & Status")
logger.info("   ✅ Dealer Performance Dashboard")
logger.info("   ✅ Warehouse & City Analytics")
logger.info("   ✅ KPI Dashboard")
logger.info("   ✅ Control Tower Alerts")
logger.info("   ✅ GROQ AI Natural Language Understanding")
logger.info("")
logger.info(f"   GROQ AI STATUS:")
logger.info(f"   ✅ SDK Available: {GROQ_AVAILABLE}")
logger.info(f"   ✅ API Key: {'Configured' if GROQ_API_KEY else 'MISSING'}")
logger.info(f"   ✅ Model: {GROQ_MODEL if GROQ_ENABLED else 'N/A'}")
logger.info(f"   ✅ Status: {'ENABLED' if GROQ_ENABLED else 'DISABLED'}")
logger.info("")
logger.info(f"   SERVICE STATUS:")
logger.info(f"   WhatsApp Service: {'✅ AVAILABLE' if WHATSAPP_SERVICE_AVAILABLE else '❌ UNAVAILABLE'}")
logger.info(f"   AI Query Service: {'✅ AVAILABLE' if AI_SERVICE_AVAILABLE else '⚠️ FALLBACK'}")
logger.info(f"   Database: PostgreSQL")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY - GROQ AI INTEGRATED")
logger.info("=" * 80)

# Initialize GROQ
if GROQ_ENABLED and not GROQ_CLIENT:
    init_groq_client()

# Initialize services
ensure_services_initialized()
