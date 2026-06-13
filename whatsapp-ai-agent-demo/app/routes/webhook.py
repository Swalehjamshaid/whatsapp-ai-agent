# ==========================================================
# FILE: app/routes/webhook.py (v31.0 - FULL DIAGNOSTIC SUITE)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler with Complete Diagnostic Analysis
# 
# IMPROVEMENTS v31.0:
# - ✅ FULL DIAGNOSTIC SUITE - Identifies exact failure point
# - ✅ Step-by-step execution tracing
# - ✅ Component-level health checks
# - ✅ Real-time failure analysis
# - ✅ Automatic root cause detection
# - ✅ Comprehensive debug output
# ==========================================================

import json
import time
import uuid
import re
import asyncio
import traceback
import sys
from typing import Dict, Any, Optional, List, Tuple
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import text, or_, cast, String, and_, inspect
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
REQUEST_TIMEOUT_SECONDS = 35
SEND_MESSAGE_TIMEOUT = 30
MAX_RETRIES = 2
RETRY_DELAYS = [1, 2]

RATE_LIMIT_MAX_MESSAGES = 10
RATE_LIMIT_WINDOW = 60
AUTO_CLEANUP_INTERVAL = 500

# Enable full diagnostic mode
DIAGNOSTIC_MODE = True
VERBOSE_LOGGING = True

# ==========================================================
# CACHES
# ==========================================================

processed_messages = TTLCache(maxsize=5000, ttl=3600)
rate_limit_cache = TTLCache(maxsize=10000, ttl=RATE_LIMIT_WINDOW)
dn_cache = TTLCache(maxsize=1000, ttl=3600)

# ==========================================================
# COMPREHENSIVE METRICS
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
        "last_error_trace": None,
        "component_status": {},
        "execution_path": []
    }
}

WHATSAPP_SERVICE_AVAILABLE = False
AI_SERVICE_AVAILABLE = False

# ==========================================================
# SERVICE IMPORTS WITH DIAGNOSTICS
# ==========================================================

logger.info("=" * 80)
logger.info("🔍 WEBHOOK v31.0 - FULL DIAGNOSTIC SUITE INITIALIZING")
logger.info("=" * 80)

# Import WhatsApp Service
try:
    from app.services.whatsapp_service import send_text_message
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ [COMPONENT] WhatsApp Service: LOADED")
    metrics["diagnostics"]["component_status"]["whatsapp_service"] = "healthy"
except ImportError as e:
    logger.error(f"❌ [COMPONENT] WhatsApp Service: IMPORT FAILED - {e}")
    metrics["diagnostics"]["component_status"]["whatsapp_service"] = f"import_error: {e}"
except Exception as e:
    logger.error(f"❌ [COMPONENT] WhatsApp Service: ERROR - {e}")
    metrics["diagnostics"]["component_status"]["whatsapp_service"] = f"error: {e}"

# Import AI Query Service
try:
    from app.services.ai_query_service import process_whatsapp_query, get_query_service, initialize_query_service
    from app.services.logistics_query_service import get_logistics_query_service
    from app.services.analytics_service import AnalyticsService
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ [COMPONENT] AI Query Service: LOADED")
    metrics["diagnostics"]["component_status"]["ai_service"] = "loaded"
except ImportError as e:
    logger.warning(f"⚠️ [COMPONENT] AI Query Service: IMPORT FAILED - {e}")
    AI_SERVICE_AVAILABLE = False
    metrics["diagnostics"]["component_status"]["ai_service"] = f"import_error: {e}"
except Exception as e:
    logger.warning(f"⚠️ [COMPONENT] AI Query Service: ERROR - {e}")
    AI_SERVICE_AVAILABLE = False
    metrics["diagnostics"]["component_status"]["ai_service"] = f"error: {e}"

# ==========================================================
# SERVICE INITIALIZATION
# ==========================================================

_services_initialized = False

def ensure_services_initialized():
    global _services_initialized, AI_SERVICE_AVAILABLE
    
    if _services_initialized:
        return
    
    logger.info("🔧 Initializing services...")
    
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
                logger.info("✅ [COMPONENT] AI Query Service: INITIALIZED")
                metrics["diagnostics"]["component_status"]["ai_service"] = "initialized"
                _services_initialized = True
            finally:
                db.close()
        except Exception as e:
            logger.error(f"❌ [COMPONENT] AI Query Service: INIT FAILED - {e}")
            AI_SERVICE_AVAILABLE = False
            metrics["diagnostics"]["component_status"]["ai_service"] = f"init_error: {e}"
            _services_initialized = False
    else:
        logger.info("⚠️ AI Service not available - using direct database fallback")
        metrics["diagnostics"]["component_status"]["ai_service"] = "fallback_mode"
        _services_initialized = True

# ==========================================================
# DN NORMALIZATION & VALIDATION
# ==========================================================

def normalize_dn(dn_value) -> str:
    if dn_value is None:
        return ""
    
    dn_str = str(dn_value).strip()
    
    if dn_str.endswith('.0'):
        dn_str = dn_str[:-2]
    
    original_len = len(dn_str)
    dn_str = re.sub(r'[^0-9]', '', dn_str)
    
    if DIAGNOSTIC_MODE and original_len != len(dn_str):
        logger.debug(f"📝 Normalized DN: '{dn_value}' -> '{dn_str}'")
    
    return dn_str


def validate_dn_format(dn_str: str) -> Dict[str, Any]:
    result = {
        "original": dn_str,
        "normalized": normalize_dn(dn_str),
        "length": len(dn_str),
        "is_numeric": dn_str.isdigit() if dn_str else False,
        "starts_with_624": dn_str.startswith("624") if dn_str else False,
        "valid_format": False,
        "suggestions": []
    }
    
    normalized = result["normalized"]
    
    if normalized and normalized.isdigit():
        if len(normalized) >= 9 and len(normalized) <= 12:
            result["valid_format"] = True
            result["suggestions"].append("✓ Format looks valid")
        elif len(normalized) < 9:
            result["suggestions"].append(f"⚠️ DN too short ({len(normalized)} digits). Expected 9-12 digits")
        else:
            result["suggestions"].append(f"⚠️ DN too long ({len(normalized)} digits). Expected 9-12 digits")
    else:
        result["suggestions"].append("❌ DN contains non-numeric characters")
    
    return result


def is_dn_number(text: str) -> bool:
    cleaned = re.sub(r'[^0-9]', '', text.strip())
    pattern = r'^\d{10,12}$'
    return bool(re.match(pattern, cleaned))


def calculate_priority(days: int) -> str:
    if days > 14:
        return "CRITICAL"
    elif days > 7:
        return "HIGH"
    elif days > 3:
        return "MEDIUM"
    return "LOW"


# ==========================================================
# COMPLETE DATABASE DIAGNOSTIC FUNCTION
# ==========================================================

def diagnose_database_complete() -> Dict[str, Any]:
    """Complete database diagnosis - identifies schema and data issues"""
    db = None
    diagnosis = {
        "database_connected": False,
        "table_exists": False,
        "columns": [],
        "dn_column_type": None,
        "total_records": 0,
        "sample_dns": [],
        "dn_length_distribution": {},
        "issues_found": [],
        "recommendations": []
    }
    
    try:
        db = SessionLocal()
        diagnosis["database_connected"] = True
        
        # Check if table exists
        inspector = inspect(db.bind)
        if "delivery_reports" in inspector.get_table_names():
            diagnosis["table_exists"] = True
            
            # Get columns
            columns = inspector.get_columns("delivery_reports")
            diagnosis["columns"] = [c["name"] for c in columns]
            
            # Get dn_no column type
            for col in columns:
                if col["name"] == "dn_no":
                    diagnosis["dn_column_type"] = str(col["type"])
                    break
            
            # Get total records
            diagnosis["total_records"] = db.query(DeliveryReport).count()
            
            # Get sample DNs
            samples = db.query(DeliveryReport.dn_no).limit(10).all()
            diagnosis["sample_dns"] = [str(s[0]) for s in samples if s[0]]
            
            # Analyze DN length distribution
            length_dist = db.execute("""
                SELECT LENGTH(CAST(dn_no AS TEXT)) as dn_len, COUNT(*) 
                FROM delivery_reports 
                WHERE dn_no IS NOT NULL 
                GROUP BY dn_len 
                ORDER BY dn_len
            """).fetchall()
            diagnosis["dn_length_distribution"] = {str(l[0]): l[1] for l in length_dist}
            
            # Identify issues
            if diagnosis["total_records"] == 0:
                diagnosis["issues_found"].append("Database has NO records")
                diagnosis["recommendations"].append("Import delivery data into database")
            
            if not diagnosis["sample_dns"]:
                diagnosis["issues_found"].append("No DN numbers found in database")
                diagnosis["recommendations"].append("Check if dn_no column has data")
            
            if diagnosis["dn_column_type"] and "INTEGER" in diagnosis["dn_column_type"].upper():
                diagnosis["recommendations"].append("DN column is INTEGER - search with numbers only")
            elif diagnosis["dn_column_type"] and "VARCHAR" in diagnosis["dn_column_type"].upper():
                diagnosis["recommendations"].append("DN column is VARCHAR - search with string patterns")
            
        else:
            diagnosis["issues_found"].append("Table 'delivery_reports' does not exist")
            diagnosis["recommendations"].append("Create the delivery_reports table or check database migration")
            
    except Exception as e:
        diagnosis["issues_found"].append(f"Database connection error: {str(e)}")
        diagnosis["recommendations"].append("Check database connection string and credentials")
        
    finally:
        if db:
            db.close()
    
    return diagnosis


# ==========================================================
# ENHANCED DN LOOKUP WITH FULL TRACING
# ==========================================================

def get_dn_details_from_db(dn_number: str) -> Optional[Dict[str, Any]]:
    """Get DN details with complete execution tracing"""
    
    # Track this lookup attempt
    metrics["diagnostics"]["dn_lookup_attempts"] += 1
    execution_trace = []
    
    # Add to execution path
    trace_entry = {
        "timestamp": datetime.now().isoformat(),
        "dn": dn_number,
        "step": "start_lookup",
        "status": "initiated"
    }
    metrics["diagnostics"]["execution_path"].append(trace_entry)
    execution_trace.append(trace_entry)
    
    # Check cache first
    if dn_number in dn_cache:
        logger.info(f"📦 DN cache hit: {dn_number}")
        metrics["diagnostics"]["dn_lookup_successes"] += 1
        trace_entry = {"step": "cache_check", "status": "hit", "result": "cached"}
        execution_trace.append(trace_entry)
        return dn_cache[dn_number]
    
    trace_entry = {"step": "cache_check", "status": "miss"}
    execution_trace.append(trace_entry)
    
    db = None
    try:
        db = SessionLocal()
        normalized = normalize_dn(dn_number)
        
        # Validate format
        validation = validate_dn_format(dn_number)
        trace_entry = {"step": "validate_format", "valid": validation["valid_format"], "normalized": normalized}
        execution_trace.append(trace_entry)
        
        logger.info(f"🔍 DN Lookup: {dn_number} (normalized: {normalized})")
        
        # Strategy 1: INTEGER exact match
        records = None
        strategy_used = None
        
        try:
            if normalized and normalized.isdigit():
                normalized_int = int(normalized)
                logger.info(f"   📍 Strategy 1: INTEGER = {normalized_int}")
                trace_entry = {"step": "strategy_1_integer", "value": normalized_int}
                
                int_records = db.query(DeliveryReport).filter(DeliveryReport.dn_no == normalized_int).all()
                
                if int_records:
                    logger.info(f"   ✅ Found {len(int_records)} record(s) via INTEGER")
                    records = int_records
                    strategy_used = "INTEGER_EXACT"
                    trace_entry["status"] = "success"
                    trace_entry["count"] = len(int_records)
                    execution_trace.append(trace_entry)
                else:
                    trace_entry["status"] = "not_found"
                    execution_trace.append(trace_entry)
        except Exception as e:
            trace_entry = {"step": "strategy_1_integer", "status": "error", "error": str(e)}
            execution_trace.append(trace_entry)
        
        # Strategy 2: STRING exact match
        if not records:
            logger.info(f"   📍 Strategy 2: STRING = '{normalized}'")
            trace_entry = {"step": "strategy_2_string", "value": normalized}
            
            string_records = db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).all()
            
            if string_records:
                logger.info(f"   ✅ Found {len(string_records)} record(s) via STRING")
                records = string_records
                strategy_used = "STRING_EXACT"
                trace_entry["status"] = "success"
                trace_entry["count"] = len(string_records)
                execution_trace.append(trace_entry)
            else:
                trace_entry["status"] = "not_found"
                execution_trace.append(trace_entry)
        
        # Strategy 3: With .0 suffix
        if not records and not normalized.endswith('.0'):
            with_dot_zero = f"{normalized}.0"
            logger.info(f"   📍 Strategy 3: With .0 = '{with_dot_zero}'")
            trace_entry = {"step": "strategy_3_dot_zero", "value": with_dot_zero}
            
            dot_zero_records = db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == with_dot_zero
            ).all()
            
            if dot_zero_records:
                logger.info(f"   ✅ Found {len(dot_zero_records)} record(s) via .0 suffix")
                records = dot_zero_records
                strategy_used = "DOT_ZERO_SUFFIX"
                trace_entry["status"] = "success"
                trace_entry["count"] = len(dot_zero_records)
                execution_trace.append(trace_entry)
            else:
                trace_entry["status"] = "not_found"
                execution_trace.append(trace_entry)
        
        # Strategy 4: CONTAINS pattern
        if not records:
            logger.info(f"   📍 Strategy 4: CONTAINS '%{normalized}%'")
            trace_entry = {"step": "strategy_4_contains", "pattern": f"%{normalized}%"}
            
            like_records = db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String).like(f"%{normalized}%")
            ).all()
            
            if like_records:
                logger.info(f"   ✅ Found {len(like_records)} record(s) via CONTAINS")
                records = like_records
                strategy_used = "CONTAINS_PATTERN"
                trace_entry["status"] = "success"
                trace_entry["count"] = len(like_records)
                execution_trace.append(trace_entry)
            else:
                trace_entry["status"] = "not_found"
                execution_trace.append(trace_entry)
        
        # If no records found - DIAGNOSE WHY
        if not records:
            logger.warning(f"❌ DN {dn_number} NOT FOUND after all strategies")
            
            # Get diagnostic info
            diagnosis = diagnose_database_complete()
            
            # Find similar DNs
            similar_dns = []
            if normalized and len(normalized) >= 5:
                prefix = normalized[:5]
                db_similar = db.query(DeliveryReport.dn_no).filter(
                    cast(DeliveryReport.dn_no, String).like(f"{prefix}%")
                ).limit(10).all()
                similar_dns = [str(s[0]) for s in db_similar]
            
            # Store failure details
            failure_details = {
                "dn": dn_number,
                "normalized": normalized,
                "validation": validation,
                "strategies_tried": len([t for t in execution_trace if "strategy" in t.get("step", "")]),
                "similar_dns_in_db": similar_dns[:5],
                "database_diagnosis": diagnosis,
                "execution_trace": execution_trace,
                "timestamp": datetime.now().isoformat()
            }
            
            metrics["diagnostics"]["dn_lookup_failures"] += 1
            metrics["diagnostics"]["last_failed_dn"] = dn_number
            metrics["diagnostics"]["last_error_trace"] = failure_details
            
            return None
        
        # Process found records
        logger.info(f"✅ DN found using strategy: {strategy_used}")
        
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
            "pod_status": pod_status,
            "_diagnostic": {
                "strategy_used": strategy_used,
                "records_found": len(records),
                "trace_summary": execution_trace[-3:]  # Last 3 steps
            }
        }
        
        dn_cache[dn_number] = result
        metrics["diagnostics"]["dn_lookup_successes"] += 1
        
        return result
        
    except Exception as e:
        logger.error(f"❌ DN lookup error: {e}")
        logger.error(traceback.format_exc())
        
        error_details = {
            "dn": dn_number,
            "error": str(e),
            "trace": traceback.format_exc(),
            "execution_trace": execution_trace
        }
        metrics["diagnostics"]["dn_lookup_failures"] += 1
        metrics["diagnostics"]["last_error_trace"] = error_details
        
        return None
    finally:
        if db:
            db.close()


# ==========================================================
# RESPONSE FORMATTERS
# ==========================================================

def format_dn_response(details: Dict[str, Any]) -> str:
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


def format_diagnostic_not_found_response(dn_number: str) -> str:
    """Format response with full diagnostic analysis when DN not found"""
    
    failure_details = metrics["diagnostics"].get("last_error_trace", {})
    validation = validate_dn_format(dn_number)
    
    # Get database diagnosis
    db_diagnosis = diagnose_database_complete()
    
    # Build diagnostic message
    diagnostic_sections = []
    
    # Section 1: Format Validation
    diagnostic_sections.append("🔍 *FORMAT VALIDATION*")
    diagnostic_sections.append(f"• DN: {dn_number}")
    diagnostic_sections.append(f"• Length: {validation['length']} digits")
    diagnostic_sections.append(f"• Valid Format: {'✅' if validation['valid_format'] else '❌'}")
    for suggestion in validation['suggestions']:
        diagnostic_sections.append(f"• {suggestion}")
    
    # Section 2: Database Status
    diagnostic_sections.append("")
    diagnostic_sections.append("🗄️ *DATABASE STATUS*")
    diagnostic_sections.append(f"• Connected: {'✅' if db_diagnosis['database_connected'] else '❌'}")
    diagnostic_sections.append(f"• Table exists: {'✅' if db_diagnosis['table_exists'] else '❌'}")
    diagnostic_sections.append(f"• Total records: {db_diagnosis['total_records']:,}")
    diagnostic_sections.append(f"• DN column type: {db_diagnosis.get('dn_column_type', 'Unknown')}")
    
    # Section 3: Sample DNs
    if db_diagnosis.get('sample_dns'):
        diagnostic_sections.append("")
        diagnostic_sections.append("📋 *SAMPLE DNS IN DATABASE*")
        for sample in db_diagnosis['sample_dns'][:5]:
            diagnostic_sections.append(f"• {sample}")
    
    # Section 4: Similar DNs
    if failure_details and failure_details.get("similar_dns_in_db"):
        diagnostic_sections.append("")
        diagnostic_sections.append("🔎 *SIMILAR DNS FOUND*")
        for similar in failure_details["similar_dns_in_db"][:3]:
            diagnostic_sections.append(f"• {similar}")
    
    # Section 5: Search Attempts
    if failure_details and failure_details.get("execution_trace"):
        strategies = [t for t in failure_details["execution_trace"] if "strategy" in t.get("step", "")]
        diagnostic_sections.append("")
        diagnostic_sections.append("🎯 *SEARCH STRATEGIES TRIED*")
        for s in strategies:
            status_icon = "✅" if s.get("status") == "success" else "❌" if s.get("status") == "not_found" else "⚠️"
            diagnostic_sections.append(f"• {status_icon} {s.get('step', 'Unknown').replace('_', ' ').title()}")
    
    # Section 6: Recommendations
    if db_diagnosis.get('recommendations'):
        diagnostic_sections.append("")
        diagnostic_sections.append("💡 *RECOMMENDATIONS*")
        for rec in db_diagnosis['recommendations'][:3]:
            diagnostic_sections.append(f"• {rec}")
    
    diagnostic_sections.append("")
    diagnostic_sections.append("📞 *NEXT STEPS*")
    diagnostic_sections.append("• Verify the DN number is correct")
    diagnostic_sections.append("• Check if DN exists in your source system")
    diagnostic_sections.append("• Contact support with this diagnostic info")
    
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


# ==========================================================
# OTHER DATABASE FUNCTIONS
# ==========================================================

def search_dealer_in_db(dealer_name: str) -> Optional[Dict[str, Any]]:
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
# MESSAGE PROCESSING
# ==========================================================

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
            logger.info(f"✅ AI Service: {process_time:.0f}ms")
            
            return response
            
        except Exception as e:
            logger.error(f"❌ AI Service failed: {e}")
            metrics["service_failures"]["ai_service"] += 1
            metrics["service_usage"]["fallback_mode"] = True
    
    metrics["service_usage"]["direct_db_calls"] += 1
    return process_message_direct(message)


def process_message_direct(message: str) -> str:
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
            return format_diagnostic_not_found_response(message)
    
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
            logger.info(f"Auto cleanup triggered")
            old_size = len(processed_messages)
            processed_messages.clear()
            metrics["last_cleanup"] = current_time
            logger.info(f"Cache cleanup: {old_size} messages cleared")


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
            logger.success("✅ Webhook verified!")
            return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/")
async def receive_message(request: Request) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    logger.bind(request_id=request_id)
    metrics["total_requests"] += 1
    
    logger.info(f"📨 Webhook received (v31.0 - Diagnostic Suite)")
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
# DIAGNOSTIC ENDPOINTS - FOR TROUBLESHOOTING
# ==========================================================

@router.get("/diagnose/full")
async def full_diagnosis():
    """Complete system diagnosis - identifies exact issue"""
    
    # Database diagnosis
    db_diagnosis = diagnose_database_complete()
    
    # Component status
    component_status = metrics["diagnostics"]["component_status"]
    
    # DN lookup stats
    dn_stats = {
        "attempts": metrics["diagnostics"]["dn_lookup_attempts"],
        "successes": metrics["diagnostics"]["dn_lookup_successes"],
        "failures": metrics["diagnostics"]["dn_lookup_failures"],
        "success_rate": round(
            metrics["diagnostics"]["dn_lookup_successes"] / max(1, metrics["diagnostics"]["dn_lookup_attempts"]) * 100, 2
        )
    }
    
    # Root cause analysis
    root_cause = []
    recommendations = []
    
    if not db_diagnosis["database_connected"]:
        root_cause.append("DATABASE_CONNECTION_FAILED")
        recommendations.append("Check database connection string and network connectivity")
    
    if not db_diagnosis["table_exists"]:
        root_cause.append("TABLE_NOT_FOUND")
        recommendations.append("Run database migrations to create delivery_reports table")
    
    if db_diagnosis["total_records"] == 0:
        root_cause.append("NO_DATA_IN_TABLE")
        recommendations.append("Import delivery data into the database")
    
    if dn_stats["failures"] > 0 and dn_stats["successes"] == 0:
        root_cause.append("DN_LOOKUP_FAILING")
        recommendations.append("Check DN format and verify data exists in database")
    
    if AI_SERVICE_AVAILABLE is False:
        root_cause.append("AI_SERVICE_UNAVAILABLE")
        recommendations.append("Check ai_query_service.py file and its dependencies")
    
    return {
        "service": "webhook_diagnostic",
        "version": "31.0",
        "timestamp": datetime.now().isoformat(),
        "status": "healthy" if not root_cause else "degraded" if len(root_cause) < 2 else "unhealthy",
        "root_cause": root_cause,
        "recommendations": recommendations,
        "database": db_diagnosis,
        "components": component_status,
        "dn_lookup_stats": dn_stats,
        "last_failed_dn": metrics["diagnostics"]["last_failed_dn"],
        "execution_path_length": len(metrics["diagnostics"]["execution_path"])
    }


@router.get("/debug/check-dn/{dn_number}")
async def debug_check_dn(dn_number: str):
    """Check specific DN with full diagnostics"""
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
            "sample_matches": [],
            "database_info": {}
        }
        
        # Test all search strategies
        if normalized_int is not None:
            int_match = db.query(DeliveryReport).filter(DeliveryReport.dn_no == normalized_int).all()
            results["matches"]["integer_exact"] = len(int_match)
            if int_match:
                results["sample_matches"].extend([
                    {"dn_no": str(r.dn_no), "customer": r.customer_name, "strategy": "integer_exact"} 
                    for r in int_match[:3]
                ])
        
        string_match = db.query(DeliveryReport).filter(cast(DeliveryReport.dn_no, String) == normalized).all()
        results["matches"]["string_exact"] = len(string_match)
        if string_match and not results["sample_matches"]:
            results["sample_matches"].extend([
                {"dn_no": str(r.dn_no), "customer": r.customer_name, "strategy": "string_exact"} 
                for r in string_match[:3]
            ])
        
        dot_zero_match = db.query(DeliveryReport).filter(cast(DeliveryReport.dn_no, String) == f"{normalized}.0").all()
        results["matches"]["dot_zero_suffix"] = len(dot_zero_match)
        
        like_match = db.query(DeliveryReport).filter(cast(DeliveryReport.dn_no, String).like(f"%{normalized}%")).all()
        results["matches"]["contains"] = len(like_match)
        if like_match and not results["sample_matches"]:
            results["sample_matches"].extend([
                {"dn_no": str(r.dn_no), "customer": r.customer_name, "strategy": "contains"} 
                for r in like_match[:3]
            ])
        
        # Get database info
        results["database_info"]["total_records"] = db.query(DeliveryReport).count()
        results["database_info"]["unique_dns"] = db.query(DeliveryReport.dn_no).distinct().count()
        
        sample_dns = db.query(DeliveryReport.dn_no).limit(20).all()
        results["database_info"]["sample_dns"] = [str(d[0]) for d in sample_dns if d[0]]
        
        results["found"] = any(results["matches"].values())
        
        # Provide diagnosis
        if not results["found"]:
            results["diagnosis"] = {
                "issue": "DN_NOT_FOUND",
                "possible_causes": [],
                "suggestions": []
            }
            
            if normalized and len(normalized) >= 5:
                prefix = normalized[:5]
                similar = db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String).like(f"{prefix}%")
                ).limit(5).all()
                if similar:
                    results["diagnosis"]["possible_causes"].append("DN might have wrong suffix")
                    results["diagnosis"]["suggestions"].append(f"Try similar DNs: {[str(s.dn_no) for s in similar]}")
            
            col_info = db.execute("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'delivery_reports' AND column_name = 'dn_no'
            """).fetchone()
            
            if col_info:
                results["database_info"]["dn_column_type"] = col_info[0]
                if col_info[0].upper() == "INTEGER" and not normalized_int:
                    results["diagnosis"]["possible_causes"].append("DN column is INTEGER but value cannot be converted")
                    results["diagnosis"]["suggestions"].append("Check if DN contains non-numeric characters")
                elif col_info[0].upper() == "VARCHAR":
                    results["diagnosis"]["suggestions"].append("DN column is VARCHAR - search is case-sensitive")
        
        return results
    finally:
        db.close()


@router.get("/diagnose/database")
async def diagnose_database_endpoint():
    """Diagnose database issues"""
    return diagnose_database_complete()


@router.get("/diagnose/dn/{dn_number}")
async def diagnose_specific_dn(dn_number: str):
    """Diagnose why a specific DN is not found"""
    db = SessionLocal()
    try:
        normalized = normalize_dn(dn_number)
        
        # Check if DN exists in any form
        found_forms = []
        
        # Check as integer
        try:
            int_val = int(normalized)
            if db.query(DeliveryReport).filter(DeliveryReport.dn_no == int_val).first():
                found_forms.append(f"integer: {int_val}")
        except:
            pass
        
        # Check as string
        if db.query(DeliveryReport).filter(cast(DeliveryReport.dn_no, String) == normalized).first():
            found_forms.append(f"string: {normalized}")
        
        # Check with .0
        if db.query(DeliveryReport).filter(cast(DeliveryReport.dn_no, String) == f"{normalized}.0").first():
            found_forms.append(f"string with .0: {normalized}.0")
        
        # Find similar DNs
        similar = []
        if len(normalized) >= 5:
            prefix = normalized[:5]
            similar = db.query(DeliveryReport.dn_no).filter(
                cast(DeliveryReport.dn_no, String).like(f"{prefix}%")
            ).limit(10).all()
            similar = [str(s[0]) for s in similar]
        
        return {
            "dn_searched": dn_number,
            "normalized": normalized,
            "found_in_database": len(found_forms) > 0,
            "forms_found": found_forms,
            "similar_dns_in_database": similar[:10],
            "total_dns_in_db": db.query(DeliveryReport).count(),
            "diagnosis": "DN EXISTS" if found_forms else "DN DOES NOT EXIST IN DATABASE",
            "recommendation": "Use the exact format as shown in forms_found" if found_forms else "Verify DN number from source system"
        }
    finally:
        db.close()


@router.get("/diagnose/trace")
async def get_execution_trace():
    """Get recent execution trace for debugging"""
    return {
        "execution_path": metrics["diagnostics"]["execution_path"][-20:],
        "total_traces": len(metrics["diagnostics"]["execution_path"]),
        "last_failed_dn": metrics["diagnostics"]["last_failed_dn"],
        "last_error": metrics["diagnostics"]["last_error_trace"][:500] if metrics["diagnostics"]["last_error_trace"] else None
    }


@router.get("/health")
async def health_check():
    """Health check with component status"""
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
        "version": "31.0",
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "FULL_DIAGNOSTIC_SUITE",
        "diagnostic_mode": DIAGNOSTIC_MODE,
        "services": {
            "whatsapp_service": {"available": WHATSAPP_SERVICE_AVAILABLE},
            "database": {"connected": db_healthy},
            "ai_query_service": {
                "available": AI_SERVICE_AVAILABLE,
                "healthy": ai_healthy,
                "version": ai_version
            }
        },
        "dn_lookup_stats": {
            "attempts": metrics["diagnostics"]["dn_lookup_attempts"],
            "successes": metrics["diagnostics"]["dn_lookup_successes"],
            "failures": metrics["diagnostics"]["dn_lookup_failures"],
            "success_rate": round(
                metrics["diagnostics"]["dn_lookup_successes"] / max(1, metrics["diagnostics"]["dn_lookup_attempts"]) * 100, 2
            ) if metrics["diagnostics"]["dn_lookup_attempts"] > 0 else 0
        }
    }


@router.get("/diagnose/reset")
async def reset_diagnostics():
    """Reset diagnostic metrics"""
    metrics["diagnostics"]["dn_lookup_attempts"] = 0
    metrics["diagnostics"]["dn_lookup_successes"] = 0
    metrics["diagnostics"]["dn_lookup_failures"] = 0
    metrics["diagnostics"]["last_failed_dn"] = None
    metrics["diagnostics"]["last_error_trace"] = None
    metrics["diagnostics"]["execution_path"] = []
    dn_cache.clear()
    return {"success": True, "message": "Diagnostics reset"}


@router.get("/ping")
async def ping():
    return {
        "pong": True, 
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "full_diagnostic",
        "diagnostic_mode": DIAGNOSTIC_MODE,
        "dn_lookup_failures": metrics["diagnostics"]["dn_lookup_failures"]
    }


@router.get("/cache/clear")
async def clear_cache():
    old_size = len(dn_cache)
    dn_cache.clear()
    return {"success": True, "cleared": old_size}


@router.get("/metrics")
async def get_metrics():
    return {
        "total_requests": metrics["total_requests"],
        "successful_requests": metrics["successful_requests"],
        "failed_requests": metrics["failed_requests"],
        "timeout_requests": metrics["timeout_requests"],
        "rate_limited_requests": metrics["rate_limited_requests"],
        "service_usage": metrics["service_usage"],
        "service_failures": metrics["service_failures"],
        "diagnostics": {
            "dn_lookup_stats": {
                "attempts": metrics["diagnostics"]["dn_lookup_attempts"],
                "successes": metrics["diagnostics"]["dn_lookup_successes"],
                "failures": metrics["diagnostics"]["dn_lookup_failures"],
                "last_failed_dn": metrics["diagnostics"]["last_failed_dn"]
            }
        },
        "uptime_seconds": time.time() - metrics["start_time"]
    }


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 80)
logger.info("🔍 WEBHOOK v31.0 - FULL DIAGNOSTIC SUITE")
logger.info("=" * 80)
logger.info("")
logger.info("   DIAGNOSTIC CAPABILITIES:")
logger.info("   ✅ Complete execution tracing")
logger.info("   ✅ Component-level health checks")
logger.info("   ✅ Real-time failure analysis")
logger.info("   ✅ Automatic root cause detection")
logger.info("   ✅ DN format validation")
logger.info("   ✅ Database schema diagnosis")
logger.info("   ✅ Similar DN discovery")
logger.info("")
logger.info("   DIAGNOSTIC ENDPOINTS:")
logger.info("   • GET /webhook/diagnose/full - Complete system diagnosis")
logger.info("   • GET /webhook/diagnose/database - Database health")
logger.info("   • GET /webhook/diagnose/dn/{number} - Specific DN analysis")
logger.info("   • GET /webhook/diagnose/trace - Execution trace")
logger.info("   • GET /webhook/debug/check-dn/{number} - Full DN debug")
logger.info("")
logger.info("   SERVICE STATUS:")
logger.info(f"   WhatsApp Service: {'✅ AVAILABLE' if WHATSAPP_SERVICE_AVAILABLE else '❌ UNAVAILABLE'}")
logger.info(f"   AI Query Service: {'✅ AVAILABLE' if AI_SERVICE_AVAILABLE else '⚠️ FALLBACK MODE'}")
logger.info(f"   Diagnostic Mode: {'✅ ENABLED' if DIAGNOSTIC_MODE else '❌ DISABLED'}")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY WITH FULL DIAGNOSTICS")
logger.info("=" * 80)

# Run initial diagnosis
if DIAGNOSTIC_MODE:
    logger.info("🔍 Running initial system diagnosis...")
    diagnosis = diagnose_database_complete()
    if diagnosis["database_connected"] and diagnosis["table_exists"]:
        logger.info(f"   ✅ Database: {diagnosis['total_records']:,} records, {len(diagnosis['sample_dns'])} sample DNs")
    else:
        logger.warning(f"   ⚠️ Database issues detected: {diagnosis.get('issues_found', ['Unknown'])}")

# Initialize services
ensure_services_initialized()
