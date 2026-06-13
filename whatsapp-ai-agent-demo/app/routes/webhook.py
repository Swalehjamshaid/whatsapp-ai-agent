# ==========================================================
# FILE: app/routes/webhook.py (v33.0 - POSTGRESQL VARCHAR FIX)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - Fixed for PostgreSQL VARCHAR columns
# 
# CRITICAL FIX v33.0:
# - ✅ FIXED: DN column is VARCHAR(100) in PostgreSQL
# - ✅ FIXED: Properly tries STRING search after INTEGER fails
# - ✅ FIXED: PostgreSQL specific casting with ::text
# - ✅ PRESERVED: All original attributes and functionality
# - ✅ PRESERVED: Timeout settings and retry logic
# - ✅ PRESERVED: Rate limiting and caching
# - ✅ PRESERVED: WhatsApp service integration
# ==========================================================

import json
import time
import uuid
import re
import asyncio
import traceback
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import text, or_, cast, String, and_
from datetime import datetime, date
from loguru import logger
from cachetools import TTLCache

from app.config import config
from app.database import SessionLocal
from app.models import DeliveryReport

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

# Enable diagnostic mode for troubleshooting
DIAGNOSTIC_MODE = True

# ==========================================================
# CACHES - PRESERVED
# ==========================================================

processed_messages = TTLCache(maxsize=5000, ttl=3600)
rate_limit_cache = TTLCache(maxsize=10000, ttl=RATE_LIMIT_WINDOW)
dn_cache = TTLCache(maxsize=1000, ttl=3600)

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
        "logistics_service": 0,
        "analytics_service": 0
    },
    "service_usage": {
        "ai_service_calls": 0,
        "direct_db_calls": 0,
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

# ==========================================================
# WHATSAPP SERVICE IMPORT - PRESERVED
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
# SERVICE LAYER IMPORTS - PRESERVED
# ==========================================================

try:
    from app.services.ai_query_service import process_whatsapp_query, get_query_service, initialize_query_service
    from app.services.logistics_query_service import get_logistics_query_service
    from app.services.analytics_service import AnalyticsService
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ AI Query Service loaded successfully (v52.1)")
except ImportError as e:
    logger.warning(f"⚠️ AI Query Service import failed: {e} - Will use direct DB fallback")
    AI_SERVICE_AVAILABLE = False
except Exception as e:
    logger.warning(f"⚠️ AI Query Service error: {e} - Will use direct DB fallback")
    AI_SERVICE_AVAILABLE = False

# ==========================================================
# SERVICE INITIALIZATION FLAG - PRESERVED
# ==========================================================

_services_initialized = False

def ensure_services_initialized():
    """Initialize services once at startup (not per request)"""
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
                logger.info("✅ AI Query Service initialized with logistics + analytics")
                _services_initialized = True
            finally:
                db.close()
        except Exception as e:
            logger.error(f"❌ Service initialization failed: {e}")
            AI_SERVICE_AVAILABLE = False
            _services_initialized = False
    else:
        logger.info("⚠️ AI Service not available - using direct database fallback")
        _services_initialized = True

# ==========================================================
# DN NORMALIZATION - PRESERVED & ENHANCED
# ==========================================================

def normalize_dn(dn_value) -> str:
    """
    Normalize DN number for database lookup
    Handles PostgreSQL VARCHAR column properly
    """
    if dn_value is None:
        return ""
    
    dn_str = str(dn_value).strip()
    
    # Remove .0 suffix (common from Excel imports)
    if dn_str.endswith('.0'):
        dn_str = dn_str[:-2]
    
    # Remove any non-numeric characters
    dn_str = re.sub(r'[^0-9]', '', dn_str)
    
    return dn_str


def validate_dn_format(dn_str: str) -> Dict[str, Any]:
    """
    Validate DN format and return diagnostic info
    """
    result = {
        "original": dn_str,
        "normalized": normalize_dn(dn_str),
        "length": len(dn_str),
        "is_numeric": dn_str.isdigit() if dn_str else False,
        "valid_format": False,
        "suggestions": []
    }
    
    normalized = result["normalized"]
    
    if normalized and normalized.isdigit():
        if len(normalized) >= 9 and len(normalized) <= 12:
            result["valid_format"] = True
            result["suggestions"].append("Format looks valid")
        elif len(normalized) < 9:
            result["suggestions"].append(f"DN too short ({len(normalized)} digits). Expected 9-12 digits")
        else:
            result["suggestions"].append(f"DN too long ({len(normalized)} digits). Expected 9-12 digits")
    else:
        result["suggestions"].append("DN contains non-numeric characters")
    
    return result


def is_dn_number(text: str) -> bool:
    """Check if text looks like a DN number"""
    cleaned = re.sub(r'[^0-9]', '', text.strip())
    pattern = r'^\d{10,12}$'
    return bool(re.match(pattern, cleaned))


def calculate_priority(days: int) -> str:
    """Calculate priority based on days"""
    if days > 14:
        return "CRITICAL"
    elif days > 7:
        return "HIGH"
    elif days > 3:
        return "MEDIUM"
    return "LOW"

# ==========================================================
# DATABASE DIAGNOSTIC FUNCTIONS - ENHANCED FOR POSTGRESQL
# ==========================================================

def diagnose_database_schema():
    """
    Diagnostic function to check database schema for PostgreSQL
    """
    db = None
    try:
        db = SessionLocal()
        
        # Check if DeliveryReport table exists
        result = db.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'delivery_reports'
            )
        """)).scalar()
        
        if not result:
            return {"error": "Table delivery_reports does not exist"}
        
        # Get column info for PostgreSQL
        columns = db.execute(text("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name = 'delivery_reports'
            ORDER BY ordinal_position
        """)).fetchall()
        
        column_list = [{"name": c[0], "type": c[1], "udt_name": c[2]} for c in columns]
        
        # Get sample DNs
        sample = db.execute(text("SELECT dn_no FROM delivery_reports WHERE dn_no IS NOT NULL LIMIT 5")).fetchall()
        sample_dns = [str(s[0]) for s in sample] if sample else []
        
        # Get total count
        total_count = db.execute(text("SELECT COUNT(*) FROM delivery_reports")).scalar()
        
        return {
            "table_exists": True,
            "columns": column_list,
            "sample_dns": sample_dns,
            "row_count": total_count,
            "dn_column_type": "VARCHAR(100)"
        }
    except Exception as e:
        logger.error(f"Schema diagnosis failed: {e}")
        return {"error": str(e)}
    finally:
        if db:
            db.close()

# ==========================================================
# FIXED: ENHANCED DATABASE LOOKUP FOR POSTGRESQL VARCHAR
# ==========================================================

def get_dn_details_from_db(dn_number: str) -> Optional[Dict[str, Any]]:
    """
    Get DN details directly from database.
    CRITICAL FIX FOR POSTGRESQL: 
    - DN column is VARCHAR(100) in PostgreSQL
    - Must try STRING search when INTEGER search fails
    """
    
    # Update diagnostics metrics
    metrics["diagnostics"]["dn_lookup_attempts"] += 1
    
    diagnostic_log = {
        "dn_number": dn_number,
        "timestamp": datetime.now().isoformat(),
        "search_attempts": []
    }
    
    # Check cache first
    if dn_number in dn_cache:
        logger.info(f"📦 DN cache hit: {dn_number}")
        metrics["diagnostics"]["dn_lookup_successes"] += 1
        return dn_cache[dn_number]
    
    db = None
    try:
        db = SessionLocal()
        normalized = normalize_dn(dn_number)
        
        diagnostic_log["normalized"] = normalized
        
        # Validate format
        validation = validate_dn_format(dn_number)
        diagnostic_log["validation"] = validation
        
        logger.info(f"🔍 Starting DN search for: {dn_number} (normalized: {normalized})")
        logger.info(f"   Database: PostgreSQL, DN column type: VARCHAR(100)")
        
        records = None
        strategy_used = None
        
        # ==========================================================
        # STRATEGY 1: INTEGER search (for numeric PostgreSQL columns)
        # ==========================================================
        try:
            if normalized and normalized.isdigit():
                normalized_int = int(normalized)
                logger.info(f"   📍 Strategy 1: INTEGER search = {normalized_int}")
                diagnostic_log["search_attempts"].append({
                    "strategy": "integer_exact",
                    "value": normalized_int,
                    "result": None
                })
                
                # Using cast to text for PostgreSQL compatibility
                int_records = db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == str(normalized_int)
                ).all()
                
                if int_records:
                    logger.info(f"   ✅ Found {len(int_records)} record(s) via INTEGER search")
                    diagnostic_log["search_attempts"][-1]["result"] = f"found_{len(int_records)}_records"
                    records = int_records
                    strategy_used = "INTEGER"
                else:
                    diagnostic_log["search_attempts"][-1]["result"] = "not_found"
                    logger.info(f"   ❌ INTEGER search - no results, continuing to STRING search...")
        except ValueError as ve:
            logger.warning(f"   Strategy 1 failed: Cannot convert '{normalized}' to integer - {ve}")
            diagnostic_log["search_attempts"].append({
                "strategy": "integer_exact",
                "value": normalized,
                "result": f"conversion_error: {str(ve)}"
            })
        
        # ==========================================================
        # STRATEGY 2: STRING exact match - CRITICAL FOR VARCHAR COLUMN!
        # ==========================================================
        if records is None:
            logger.info(f"   📍 Strategy 2: STRING exact search = '{normalized}'")
            diagnostic_log["search_attempts"].append({
                "strategy": "string_exact",
                "value": normalized,
                "result": None
            })
            
            # Direct string comparison for VARCHAR column
            string_records = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == normalized
            ).all()
            
            if string_records:
                logger.info(f"   ✅ Found {len(string_records)} record(s) via STRING exact search")
                diagnostic_log["search_attempts"][-1]["result"] = f"found_{len(string_records)}_records"
                records = string_records
                strategy_used = "STRING_EXACT"
            else:
                diagnostic_log["search_attempts"][-1]["result"] = "not_found"
                logger.info(f"   ❌ STRING exact search - no results, continuing...")
        
        # ==========================================================
        # STRATEGY 3: Case-insensitive search for PostgreSQL
        # ==========================================================
        if records is None:
            logger.info(f"   📍 Strategy 3: Case-insensitive search = '{normalized}'")
            diagnostic_log["search_attempts"].append({
                "strategy": "case_insensitive",
                "value": normalized,
                "result": None
            })
            
            # Case-insensitive search using ILIKE for PostgreSQL
            from sqlalchemy import func
            insensitive_records = db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.dn_no) == normalized.lower()
            ).all()
            
            if insensitive_records:
                logger.info(f"   ✅ Found {len(insensitive_records)} record(s) via case-insensitive search")
                diagnostic_log["search_attempts"][-1]["result"] = f"found_{len(insensitive_records)}_records"
                records = insensitive_records
                strategy_used = "CASE_INSENSITIVE"
            else:
                diagnostic_log["search_attempts"][-1]["result"] = "not_found"
                logger.info(f"   ❌ Case-insensitive search - no results, continuing...")
        
        # ==========================================================
        # STRATEGY 4: With .0 suffix (common from Excel imports)
        # ==========================================================
        if records is None and not normalized.endswith('.0'):
            with_dot_zero = f"{normalized}.0"
            logger.info(f"   📍 Strategy 4: With .0 suffix = '{with_dot_zero}'")
            diagnostic_log["search_attempts"].append({
                "strategy": "string_with_dot_zero",
                "value": with_dot_zero,
                "result": None
            })
            
            dot_zero_records = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == with_dot_zero
            ).all()
            
            if dot_zero_records:
                logger.info(f"   ✅ Found {len(dot_zero_records)} record(s) via .0 suffix search")
                diagnostic_log["search_attempts"][-1]["result"] = f"found_{len(dot_zero_records)}_records"
                records = dot_zero_records
                strategy_used = "DOT_ZERO_SUFFIX"
            else:
                diagnostic_log["search_attempts"][-1]["result"] = "not_found"
                logger.info(f"   ❌ .0 suffix search - no results, continuing...")
        
        # ==========================================================
        # STRATEGY 5: CONTAINS/LIKE search (most permissive)
        # ==========================================================
        if records is None:
            logger.info(f"   📍 Strategy 5: CONTAINS pattern = '%{normalized}%'")
            diagnostic_log["search_attempts"].append({
                "strategy": "contains_pattern",
                "value": f"%{normalized}%",
                "result": None
            })
            
            like_records = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no.like(f"%{normalized}%")
            ).all()
            
            if like_records:
                logger.info(f"   ✅ Found {len(like_records)} record(s) via CONTAINS search")
                diagnostic_log["search_attempts"][-1]["result"] = f"found_{len(like_records)}_records"
                records = like_records
                strategy_used = "CONTAINS_PATTERN"
            else:
                diagnostic_log["search_attempts"][-1]["result"] = "not_found"
                logger.info(f"   ❌ CONTAINS search - no results")
        
        # ==========================================================
        # If still no records found
        # ==========================================================
        if records is None:
            logger.warning(f"❌ DN {dn_number} not found in database after all search strategies")
            
            # Get diagnostic info for response
            db_diagnosis = diagnose_database_schema()
            
            failure_details = {
                "dn": dn_number,
                "normalized": normalized,
                "validation": validation,
                "strategies_tried": len(diagnostic_log['search_attempts']),
                "database_info": db_diagnosis,
                "timestamp": datetime.now().isoformat()
            }
            
            metrics["diagnostics"]["dn_lookup_failures"] += 1
            metrics["diagnostics"]["last_failed_dn"] = dn_number
            metrics["diagnostics"]["last_error_trace"] = failure_details
            
            if DIAGNOSTIC_MODE:
                logger.error(f"   Diagnostic details for failed DN {dn_number}:")
                logger.error(f"   - Validation: {validation}")
                logger.error(f"   - Search attempts: {len(diagnostic_log['search_attempts'])}")
                for attempt in diagnostic_log['search_attempts']:
                    logger.error(f"     * {attempt['strategy']}: {attempt['result']}")
            
            return None
        
        logger.info(f"✅ DN {dn_number} found! Using strategy: {strategy_used}, Records: {len(records)}")
        
        # ==========================================================
        # Process the found records (PRESERVED from original)
        # ==========================================================
        first = records[0]
        dn_date = first.dn_create_date
        pod_date = first.pod_date
        
        delivery_status = first.delivery_status or "Pending"
        pod_status = first.pod_status or "Pending"
        
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
        
        # Cache the result
        dn_cache[dn_number] = result
        metrics["diagnostics"]["dn_lookup_successes"] += 1
        
        return result
        
    except Exception as e:
        logger.error(f"❌ DN database lookup error: {e}")
        logger.error(traceback.format_exc())
        metrics["diagnostics"]["dn_lookup_failures"] += 1
        metrics["diagnostics"]["last_error_trace"] = str(e)
        return None
    finally:
        if db:
            db.close()

# ==========================================================
# RESPONSE FORMATTERS - PRESERVED
# ==========================================================

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


def format_diagnostic_response(dn_number: str) -> str:
    """Format diagnostic response when DN not found"""
    
    validation = validate_dn_format(dn_number)
    db_diagnosis = diagnose_database_schema()
    
    diagnostic_sections = []
    
    diagnostic_sections.append("🔍 *FORMAT VALIDATION*")
    diagnostic_sections.append(f"• DN: {dn_number}")
    diagnostic_sections.append(f"• Length: {validation['length']} digits")
    diagnostic_sections.append(f"• Valid Format: {'✅' if validation['valid_format'] else '❌'}")
    
    diagnostic_sections.append("")
    diagnostic_sections.append("🗄️ *DATABASE STATUS*")
    diagnostic_sections.append(f"• Connected: {'✅' if 'error' not in db_diagnosis else '❌'}")
    diagnostic_sections.append(f"• Table exists: {'✅' if db_diagnosis.get('table_exists') else '❌'}")
    diagnostic_sections.append(f"• Total records: {db_diagnosis.get('row_count', 0):,}")
    diagnostic_sections.append(f"• DN column type: VARCHAR(100)")
    
    if db_diagnosis.get('sample_dns'):
        diagnostic_sections.append("")
        diagnostic_sections.append("📋 *SAMPLE DNS IN DATABASE*")
        for sample in db_diagnosis['sample_dns'][:5]:
            diagnostic_sections.append(f"• {sample}")
    
    diagnostic_sections.append("")
    diagnostic_sections.append("💡 *TROUBLESHOOTING*")
    diagnostic_sections.append("• Verify the DN number is correct")
    diagnostic_sections.append("• Check if DN exists in your source system")
    diagnostic_sections.append("• Contact support if issue persists")
    
    diagnostic_text = "\n".join(diagnostic_sections)
    
    return f"""
📦 *DN SEARCH - DIAGNOSTIC REPORT*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN Number:* {dn_number}

❌ *NOT FOUND IN DATABASE*

{diagnostic_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Help` for available commands
"""


def search_dealer_in_db(dealer_name: str) -> Optional[Dict[str, Any]]:
    """Search for dealer in database - PRESERVED"""
    
    db = None
    try:
        db = SessionLocal()
        
        records = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).all()
        
        if not records:
            logger.warning(f"Dealer '{dealer_name}' not found")
            return None
        
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
        
        return result
        
    except Exception as e:
        logger.error(f"Dealer search error: {e}")
        return None
    finally:
        if db:
            db.close()


def format_dealer_response(details: Dict[str, Any]) -> str:
    """Format dealer response for WhatsApp - PRESERVED"""
    
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
    """Get pending deliveries summary - PRESERVED"""
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
    """Format pending response - PRESERVED"""
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
# MESSAGE PROCESSING - PRESERVED
# ==========================================================

def process_message_with_service(message: str, user_id: str = "guest") -> str:
    """
    Process message using AI Query Service with fallback to direct DB
    """
    process_start = time.time()
    
    # Try AI Query Service first if available
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
            logger.info(f"✅ AI Service processed in {process_time:.0f}ms: {message[:50]}")
            
            return response
            
        except Exception as e:
            logger.error(f"❌ AI Service failed, falling back to direct DB: {e}")
            metrics["service_failures"]["ai_service"] += 1
            metrics["service_usage"]["fallback_mode"] = True
    
    # Fallback to direct database processing
    metrics["service_usage"]["direct_db_calls"] += 1
    return process_message_direct(message)


def process_message_direct(message: str) -> str:
    """
    Process message directly using database queries - PRESERVED
    """
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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
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
            return format_diagnostic_response(message)
    
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
# HELPER FUNCTIONS - PRESERVED
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
    """
    Send WhatsApp message with proper timeout handling - PRESERVED
    """
    send_start_time = time.time()
    
    if not WHATSAPP_SERVICE_AVAILABLE:
        logger.bind(request_id=request_id).error(f"WhatsApp service not available")
        return {"success": False, "error": "Service not available"}
    
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.bind(request_id=request_id).error(f"WhatsApp credentials missing")
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
                logger.bind(request_id=request_id).info(f"✅ Message sent in {send_duration:.0f}ms")
                return result
            
            if attempt < MAX_RETRIES - 1 and _should_retry(result.get('status_code', 0)):
                logger.bind(request_id=request_id).warning(f"Retry {attempt + 1} for {phone_number}")
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            
            return result
            
        except asyncio.TimeoutError:
            logger.bind(request_id=request_id).error(f"⏰ Timeout sending message to {phone_number} after {SEND_MESSAGE_TIMEOUT}s")
            metrics["timeout_requests"] += 1
            
            if attempt < MAX_RETRIES - 1:
                logger.bind(request_id=request_id).info(f"Retrying... (attempt {attempt + 2})")
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            
            return {"success": False, "error": f"Request timeout after {SEND_MESSAGE_TIMEOUT}s"}
            
        except Exception as e:
            logger.bind(request_id=request_id).exception(f"Send attempt {attempt + 1} failed: {e}")
            
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}


def _should_retry(status_code: int) -> bool:
    """Determine if request should be retried based on status code"""
    retryable_statuses = {429, 500, 502, 503, 504}
    return status_code in retryable_statuses

# ==========================================================
# WEBHOOK ENDPOINTS - PRESERVED
# ==========================================================

@router.get("/")
async def verify_webhook(request: Request):
    """Webhook verification endpoint - PRESERVED"""
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
    """
    Receive WhatsApp message - PRESERVED with enhanced logging
    """
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    logger.bind(request_id=request_id)
    metrics["total_requests"] += 1
    
    logger.info(f"📨 Webhook received (v33.0 - PostgreSQL VARCHAR Fixed)")
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
            logger.info(f"🤖 Processing time: {ai_duration:.0f}ms")
            
            await send_whatsapp_message(phone_number, response, request_id, msg_id)
            processed_count += 1
        
        processing_time = (time.time() - start_time) * 1000
        
        logger.info(f"✅ Done: {processing_time:.0f}ms, {processed_count} messages")
        
        return {
            "success": True,
            "request_id": request_id,
            "processing_time_ms": round(processing_time, 2),
            "messages_processed": processed_count,
            "service_mode": "ai_service" if AI_SERVICE_AVAILABLE and metrics["service_usage"]["ai_service_calls"] > 0 else "direct_fallback"
        }
        
    except asyncio.TimeoutError:
        logger.error(f"Request body timeout")
        metrics["timeout_requests"] += 1
        return {
            "success": False,
            "error": "Request timeout",
            "request_id": request_id
        }
        
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return {"success": False, "error": str(e), "request_id": request_id}

# ==========================================================
# DEBUG ENDPOINTS - PRESERVED
# ==========================================================

@router.get("/debug/check-dn/{dn_number}")
async def debug_check_dn(dn_number: str):
    """Debug endpoint to check DN in database - PRESERVED & ENHANCED"""
    db = SessionLocal()
    try:
        normalized = normalize_dn(dn_number)
        validation = validate_dn_format(dn_number)
        
        try:
            normalized_int = int(normalized) if normalized and normalized.isdigit() else None
        except ValueError:
            normalized_int = None
        
        results = {
            "searched": dn_number,
            "normalized": normalized,
            "normalized_int": normalized_int,
            "validation": validation,
            "matches": {},
            "sample_matches": []
        }
        
        # Test all search strategies
        if normalized_int is not None:
            int_match = db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == str(normalized_int)
            ).all()
            results["matches"]["integer_exact"] = len(int_match)
            if int_match:
                results["sample_matches"].extend([
                    {"dn_no": str(r.dn_no), "customer": r.customer_name, "strategy": "integer_exact"} 
                    for r in int_match[:3]
                ])
        
        string_match = db.query(DeliveryReport).filter(DeliveryReport.dn_no == normalized).all()
        results["matches"]["string_exact"] = len(string_match)
        if string_match and not results["sample_matches"]:
            results["sample_matches"].extend([
                {"dn_no": str(r.dn_no), "customer": r.customer_name, "strategy": "string_exact"} 
                for r in string_match[:3]
            ])
        
        dot_zero_match = db.query(DeliveryReport).filter(DeliveryReport.dn_no == f"{normalized}.0").all()
        results["matches"]["dot_zero_suffix"] = len(dot_zero_match)
        
        like_match = db.query(DeliveryReport).filter(DeliveryReport.dn_no.like(f"%{normalized}%")).all()
        results["matches"]["contains"] = len(like_match)
        if like_match and not results["sample_matches"]:
            results["sample_matches"].extend([
                {"dn_no": str(r.dn_no), "customer": r.customer_name, "strategy": "contains"} 
                for r in like_match[:3]
            ])
        
        sample_dns = db.query(DeliveryReport.dn_no).limit(20).all()
        results["sample_dns_in_db"] = [str(d[0]) for d in sample_dns if d[0]]
        
        results["found"] = any(results["matches"].values())
        
        if not results["found"]:
            results["suggestions"] = []
            if normalized and len(normalized) >= 5:
                prefix = normalized[:5]
                similar = db.query(DeliveryReport).filter(
                    DeliveryReport.dn_no.like(f"{prefix}%")
                ).limit(5).all()
                if similar:
                    results["suggestions"].append(f"Found DNs with similar prefix '{prefix}': {[str(s.dn_no) for s in similar]}")
        
        return results
    finally:
        db.close()


@router.get("/debug/diagnose-db")
async def diagnose_database():
    """Comprehensive database diagnostic endpoint"""
    return diagnose_database_schema()


@router.get("/debug/dn-stats")
async def get_dn_stats():
    """Get DN statistics from database"""
    db = SessionLocal()
    try:
        total_count = db.query(DeliveryReport).count()
        unique_dns = db.query(DeliveryReport.dn_no).distinct().count()
        
        samples = db.execute(text("""
            SELECT DISTINCT dn_no 
            FROM delivery_reports 
            WHERE dn_no IS NOT NULL 
            LIMIT 10
        """)).fetchall()
        
        return {
            "total_records": total_count,
            "unique_dns": unique_dns,
            "sample_dns": [str(s[0]) for s in samples if s[0]],
            "diagnostic_mode": DIAGNOSTIC_MODE,
            "database_type": "PostgreSQL",
            "dn_column_type": "VARCHAR(100)"
        }
    finally:
        db.close()


@router.get("/debug/diagnostics")
async def get_diagnostics():
    """Get diagnostic metrics"""
    return {
        "diagnostic_mode": DIAGNOSTIC_MODE,
        "dn_lookup_stats": {
            "attempts": metrics["diagnostics"]["dn_lookup_attempts"],
            "successes": metrics["diagnostics"]["dn_lookup_successes"],
            "failures": metrics["diagnostics"]["dn_lookup_failures"],
            "success_rate": round(
                metrics["diagnostics"]["dn_lookup_successes"] / max(1, metrics["diagnostics"]["dn_lookup_attempts"]) * 100, 
                2
            ),
            "last_failed_dn": metrics["diagnostics"]["last_failed_dn"]
        },
        "cache_stats": {
            "dn_cache_size": len(dn_cache),
            "processed_messages_size": len(processed_messages),
            "rate_limit_cache_size": len(rate_limit_cache)
        },
        "service_status": {
            "whatsapp_available": WHATSAPP_SERVICE_AVAILABLE,
            "ai_service_available": AI_SERVICE_AVAILABLE,
            "ai_service_initialized": _services_initialized
        }
    }


@router.get("/health")
async def health_check():
    """Health check - UPDATED for PostgreSQL"""
    db_healthy = False
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_healthy = True
    except Exception as e:
        logger.error(f"DB health failed: {e}")
    
    ai_healthy = False
    ai_version = None
    if AI_SERVICE_AVAILABLE:
        try:
            ensure_services_initialized()
            from app.services.ai_query_service import health_check as ai_health
            ai_status = ai_health()
            ai_healthy = ai_status.get("status") == "healthy" or ai_status.get("status") == "degraded"
            ai_version = ai_status.get("version")
        except Exception as e:
            logger.warning(f"AI health check failed: {e}")
    
    overall_status = "healthy" if db_healthy else "degraded"
    
    return {
        "status": overall_status,
        "version": "33.0",
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "POSTGRESQL_VARCHAR_FIX",
        "database_type": "PostgreSQL",
        "diagnostic_mode": DIAGNOSTIC_MODE,
        "timeout_settings": {
            "request_timeout": REQUEST_TIMEOUT_SECONDS,
            "send_message_timeout": SEND_MESSAGE_TIMEOUT,
            "max_retries": MAX_RETRIES
        },
        "services": {
            "whatsapp_service": {"available": WHATSAPP_SERVICE_AVAILABLE},
            "database": {"connected": db_healthy, "type": "PostgreSQL"},
            "ai_query_service": {
                "available": AI_SERVICE_AVAILABLE,
                "healthy": ai_healthy,
                "version": ai_version
            }
        },
        "cache": {"dn_cache_size": len(dn_cache)}
    }


@router.get("/ping")
async def ping():
    """Ping endpoint - PRESERVED"""
    return {
        "pong": True, 
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "postgresql_varchar_fixed",
        "ai_available": AI_SERVICE_AVAILABLE,
        "diagnostic_mode": DIAGNOSTIC_MODE,
        "cache_size": len(dn_cache),
        "timeout_seconds": SEND_MESSAGE_TIMEOUT
    }


@router.get("/cache/clear")
async def clear_cache():
    """Clear cache - PRESERVED"""
    old_size = len(dn_cache)
    dn_cache.clear()
    return {"success": True, "cleared": old_size}


@router.get("/timeout-status")
async def timeout_status():
    """Check current timeout configuration - PRESERVED"""
    return {
        "send_message_timeout_seconds": SEND_MESSAGE_TIMEOUT,
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "max_retries": MAX_RETRIES,
        "retry_delays": RETRY_DELAYS
    }


@router.get("/metrics")
async def get_metrics():
    """Get detailed metrics - PRESERVED"""
    return {
        "total_requests": metrics["total_requests"],
        "successful_requests": metrics["successful_requests"],
        "failed_requests": metrics["failed_requests"],
        "timeout_requests": metrics["timeout_requests"],
        "rate_limited_requests": metrics["rate_limited_requests"],
        "service_usage": metrics["service_usage"],
        "service_failures": metrics["service_failures"],
        "diagnostics": metrics["diagnostics"],
        "ai_available": AI_SERVICE_AVAILABLE,
        "uptime_seconds": time.time() - metrics["start_time"]
    }

# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("📡 WEBHOOK v33.0 - POSTGRESQL VARCHAR FIX")
logger.info("=" * 70)
logger.info("")
logger.info("   CRITICAL FIXES:")
logger.info("   ✅ DN column is VARCHAR(100) in PostgreSQL")
logger.info("   ✅ Fixed search order: INTEGER → STRING → Case-Insensitive → .0 → CONTAINS")
logger.info("   ✅ Properly continues to next strategy when previous fails")
logger.info("")
logger.info("   PRESERVED ATTRIBUTES:")
logger.info(f"   ✅ Send message timeout: {SEND_MESSAGE_TIMEOUT}s")
logger.info(f"   ✅ Request timeout: {REQUEST_TIMEOUT_SECONDS}s")
logger.info(f"   ✅ Max retries: {MAX_RETRIES}")
logger.info(f"   ✅ Rate limiting: {RATE_LIMIT_MAX_MESSAGES} messages per {RATE_LIMIT_WINDOW}s")
logger.info("")
logger.info(f"   SERVICE STATUS:")
logger.info(f"   ✅ WhatsApp Service: {'AVAILABLE' if WHATSAPP_SERVICE_AVAILABLE else 'UNAVAILABLE'}")
logger.info(f"   ✅ AI Query Service: {'AVAILABLE' if AI_SERVICE_AVAILABLE else 'UNAVAILABLE (fallback mode)'}")
logger.info(f"   ✅ Diagnostic Mode: {'ENABLED' if DIAGNOSTIC_MODE else 'DISABLED'}")
logger.info(f"   ✅ Database: PostgreSQL (VARCHAR column)")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY - POSTGRESQL FIXED")
logger.info("=" * 70)

# Run database schema diagnosis on startup
if DIAGNOSTIC_MODE:
    logger.info("🔍 Running PostgreSQL database diagnosis...")
    schema_info = diagnose_database_schema()
    if "error" not in schema_info:
        logger.info(f"   ✅ Database has {schema_info.get('row_count', 0):,} records")
        logger.info(f"   📊 Sample DNs: {schema_info.get('sample_dns', [])[:5]}")
        logger.info(f"   📋 DN Column Type: VARCHAR(100)")
    else:
        logger.error(f"   ❌ Schema diagnosis failed: {schema_info.get('error')}")

# Initialize services on startup
ensure_services_initialized()
