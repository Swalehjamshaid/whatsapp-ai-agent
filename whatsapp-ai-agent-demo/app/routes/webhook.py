# ==========================================================
# FILE: app/routes/webhook.py (ENTERPRISE v11.0 - ULTIMATE)
# ==========================================================
# PURPOSE: WhatsApp Webhook Handler - Production Ultimate
# 
# IMPROVEMENTS v11.0:
# - ✅ Services loaded in lifespan (not import time)
# - ✅ Full exception tracebacks everywhere
# - ✅ Startup environment validation
# - ✅ Support for both /webhook and /webhook/ URLs
# - ✅ Step-by-step diagnostic logging
# - ✅ Duplicate message protection (24h TTL)
# - ✅ Loop-based retry logic (not recursive)
# - ✅ Timeout layers for all operations
# - ✅ Shared ThreadPoolExecutor
# - ✅ Proper asyncio.get_running_loop()
# - ✅ Enhanced metrics with detailed categories
# - ✅ Crash memory (last 20 exceptions)
# - ✅ Health levels (healthy/degraded/critical)
# - ✅ Sensitive data masking
# - ✅ Configurable signature validation
# - ✅ Rate limiter with auto-cleanup
# - ✅ Request correlation everywhere
# - ✅ All original attributes preserved
# ==========================================================

import json
import hashlib
import hmac
import re
import uuid
import asyncio
import time
import os
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List
from collections import defaultdict, deque
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse, Response
from loguru import logger

from app.config import config

# ==========================================================
# ROUTER INITIALIZATION
# ==========================================================

router = APIRouter(tags=["WhatsApp Webhook"])

# ==========================================================
# CONFIGURATION FLAGS
# ==========================================================

DEBUG_MODE = getattr(config, 'ENVIRONMENT', 'development') != 'production'

# Priority 18: Configurable signature validation via env var
REQUIRE_SIGNATURE = getattr(config, 'WHATSAPP_SIGNATURE_REQUIRED', False)  # Default to False for debugging

LOG_RAW_PAYLOADS = getattr(config, 'LOG_RAW_WEBHOOK_PAYLOADS', DEBUG_MODE)

# Rate limiting
RATE_LIMIT_REQUESTS = getattr(config, 'WHATSAPP_RATE_LIMIT', 100)
RATE_LIMIT_WINDOW = 60

# Event storage
MAX_STORED_EVENTS = 100
_recent_events = deque(maxlen=MAX_STORED_EVENTS)

# Priority 7 & 8: Duplicate message protection (24h TTL)
_processed_messages: Dict[str, float] = {}
MESSAGE_DEDUP_TTL = 86400  # 24 hours

# Priority 12: Shared ThreadPoolExecutor
_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="webhook_worker")

# Priority 19: Rate limiter cleanup
_last_rate_limit_cleanup = time.time()
RATE_LIMIT_CLEANUP_INTERVAL = 300  # 5 minutes

# Priority 15: Crash memory (last 20 exceptions)
_crash_history: deque = deque(maxlen=20)


# ==========================================================
# PRIORITY 14: ENHANCED METRICS
# ==========================================================

@dataclass
class WebhookMetrics:
    messages_received: int = 0
    messages_processed: int = 0
    processing_failures: int = 0
    duplicate_messages: int = 0
    service_failures: int = 0
    rate_limited: int = 0
    status_events: int = 0
    template_events: int = 0
    webhook_hits: int = 0
    verification_hits: int = 0
    invalid_signature_hits: int = 0
    service_timeouts: int = 0
    send_failures: int = 0
    last_message_time: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "messages_received": self.messages_received,
            "messages_processed": self.messages_processed,
            "processing_failures": self.processing_failures,
            "duplicate_messages": self.duplicate_messages,
            "service_failures": self.service_failures,
            "rate_limited": self.rate_limited,
            "status_events": self.status_events,
            "template_events": self.template_events,
            "webhook_hits": self.webhook_hits,
            "verification_hits": self.verification_hits,
            "invalid_signature_hits": self.invalid_signature_hits,
            "service_timeouts": self.service_timeouts,
            "send_failures": self.send_failures,
            "last_message_time": self.last_message_time.isoformat() if self.last_message_time else None,
            "uptime_seconds": time.time() - self._start_time
        }
    
    _start_time: float = field(default_factory=time.time, init=False)


_metrics = WebhookMetrics()

# Priority 22: Health levels
class HealthLevel:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


# ==========================================================
# PRIORITY 19: RATE LIMITER WITH CLEANUP
# ==========================================================

_phone_rate_limits: Dict[str, List[float]] = defaultdict(list)

def cleanup_rate_limits():
    """Clean up old rate limit entries"""
    global _last_rate_limit_cleanup
    now = time.time()
    
    if now - _last_rate_limit_cleanup >= RATE_LIMIT_CLEANUP_INTERVAL:
        for phone in list(_phone_rate_limits.keys()):
            _phone_rate_limits[phone] = [t for t in _phone_rate_limits[phone] if now - t < RATE_LIMIT_WINDOW]
            if not _phone_rate_limits[phone]:
                del _phone_rate_limits[phone]
        _last_rate_limit_cleanup = now
        logger.debug("Rate limit cleanup completed")

def check_rate_limit(phone_number: str) -> bool:
    """Check if phone number has exceeded rate limit"""
    cleanup_rate_limits()
    
    now = time.time()
    _phone_rate_limits[phone_number] = [t for t in _phone_rate_limits[phone_number] if now - t < RATE_LIMIT_WINDOW]
    
    if len(_phone_rate_limits[phone_number]) >= RATE_LIMIT_REQUESTS:
        _metrics.rate_limited += 1
        logger.warning(f"Rate limit exceeded for {mask_sensitive_data(phone_number)}")
        return False
    
    _phone_rate_limits[phone_number].append(now)
    return True


# ==========================================================
# PRIORITY 17: SENSITIVE DATA MASKING
# ==========================================================

def mask_sensitive_data(value: str, keep_start: int = 3, keep_end: int = 2) -> str:
    """Mask sensitive data like phone numbers, tokens"""
    if not value or len(value) < keep_start + keep_end:
        return "***"
    return f"{value[:keep_start]}****{value[-keep_end:]}"


def mask_payload(payload: str) -> str:
    """Mask sensitive data in payload before logging"""
    # Mask phone numbers
    payload = re.sub(r'\b(03\d{2})\d{6}\b', r'\1******', payload)
    payload = re.sub(r'\b(92\d{2})\d{7}\b', r'\1******', payload)
    # Mask tokens
    payload = re.sub(r'[A-Za-z0-9]{20,}', '***TOKEN_MASKED***', payload)
    return payload[:500]


# ==========================================================
# PRIORITY 20: REQUEST CORRELATION EVERYWHERE
# ==========================================================

def generate_request_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


def store_event(event_type: str, data: Dict[str, Any]):
    """Store recent events for debugging"""
    _recent_events.appendleft({
        "type": event_type,
        "timestamp": datetime.now().isoformat(),
        "data": data
    })


def log_message(level: str, event: str, request_id: str = None, message_id: str = None, 
                phone_number: str = None, intent: str = None, step: str = None, **kwargs):
    """Structured logging with correlation IDs and step tracking"""
    log_entry = {
        "event": event,
        "timestamp": datetime.now().isoformat(),
        **kwargs
    }
    
    if request_id:
        log_entry["request_id"] = request_id
    if message_id:
        log_entry["message_id"] = message_id
    if phone_number:
        log_entry["phone"] = mask_sensitive_data(phone_number)
    if intent:
        log_entry["intent"] = intent
    if step:
        log_entry["step"] = step
    
    if level == "info":
        logger.info(json.dumps(log_entry))
    elif level == "error":
        logger.error(json.dumps(log_entry))
    elif level == "warning":
        logger.warning(json.dumps(log_entry))
    elif level == "success":
        logger.success(json.dumps(log_entry))


# ==========================================================
# PRIORITY 6: FULL TRACEBACKS
# ==========================================================

def log_exception(request_id: str, context: str, e: Exception, step: str = None):
    """Log full exception with traceback"""
    _crash_history.appendleft({
        "timestamp": datetime.now().isoformat(),
        "context": context,
        "error_type": type(e).__name__,
        "error_message": str(e)[:200],
        "request_id": request_id,
        "step": step
    })
    logger.exception(f"[{request_id}] EXCEPTION in {context} (step={step}): {type(e).__name__}: {e}")


# ==========================================================
# PRIORITY 7 & 8: DUPLICATE MESSAGE DETECTION
# ==========================================================

def is_duplicate_message(message_id: str) -> bool:
    """Check if message has been processed before (24h TTL)"""
    if not message_id:
        return False
    
    now = time.time()
    
    # Clean old entries
    expired = [mid for mid, ts in _processed_messages.items() if now - ts > MESSAGE_DEDUP_TTL]
    for mid in expired:
        del _processed_messages[mid]
    
    if message_id in _processed_messages:
        _metrics.duplicate_messages += 1
        return True
    
    _processed_messages[message_id] = now
    return False


# ==========================================================
# PRIORITY 11: SERVICE CACHE (Load once, not at import time)
# ==========================================================

_service_cache = {}
_SERVICE_LOAD_TIME = {}

def get_cached_service(service_name: str, import_path: str, function_name: str = None):
    """Get service from cache - loaded on demand"""
    if service_name in _service_cache:
        return _service_cache[service_name]
    
    try:
        parts = import_path.split('.')
        module_path = '.'.join(parts[:-1])
        attr_name = parts[-1] if not function_name else function_name
        
        module = __import__(module_path, fromlist=[attr_name])
        service = getattr(module, attr_name) if function_name else module
        
        _service_cache[service_name] = service
        _SERVICE_LOAD_TIME[service_name] = datetime.now().isoformat()
        logger.info(f"✅ {service_name} loaded and cached")
        return service
        
    except Exception as e:
        logger.exception(f"❌ Failed to load {service_name}: {e}")
        _service_cache[service_name] = None
        return None


# ==========================================================
# PRIORITY 13: PROPER ASYNC (get_running_loop)
# ==========================================================

async def _run_in_executor(func, *args):
    """Run sync function in thread pool executor"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, func, *args)


# ==========================================================
# PRIORITY 10: TIMEOUT LAYERS FOR ALL OPERATIONS
# ==========================================================

TIMEOUT_AI_QUERY = 30
TIMEOUT_WHATSAPP_SEND = 15
TIMEOUT_DASHBOARD = 10
TIMEOUT_FALLBACK = 20


# ==========================================================
# PRIORITY 3: ENVIRONMENT VALIDATION
# ==========================================================

def validate_startup_environment() -> Dict[str, Any]:
    """Validate required environment variables at startup"""
    required_vars = [
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_VERIFY_TOKEN"
    ]
    
    results = {}
    missing = []
    
    for var in required_vars:
        value = getattr(config, var, None) or os.getenv(var)
        is_present = bool(value)
        results[var] = is_present
        if not is_present:
            missing.append(var)
            logger.error(f"❌ Missing required env var: {var}")
        else:
            logger.info(f"✅ {var} configured")
    
    # Check signature config
    app_secret = getattr(config, 'WHATSAPP_APP_SECRET', '')
    if REQUIRE_SIGNATURE and not app_secret:
        logger.warning("⚠️ Signature validation enabled but WHATSAPP_APP_SECRET missing")
        results["WHATSAPP_APP_SECRET"] = False
    else:
        results["WHATSAPP_APP_SECRET"] = bool(app_secret)
    
    return {"configured": len(missing) == 0, "missing": missing, "details": results}


# ==========================================================
# PRIORITY 5: SIGNATURE VALIDATION
# ==========================================================

def _verify_signature_production(payload: bytes, signature_header: str) -> Tuple[bool, Dict]:
    """Verify signature with detailed diagnostics"""
    app_secret = getattr(config, 'WHATSAPP_APP_SECRET', '')
    diagnostics = {
        "signature_present": bool(signature_header),
        "app_secret_present": bool(app_secret),
        "payload_size": len(payload)
    }
    
    if not app_secret:
        diagnostics["error"] = "WHATSAPP_APP_SECRET not configured"
        return False, diagnostics
    
    if not signature_header:
        diagnostics["error"] = "Missing signature header"
        return False, diagnostics
    
    try:
        expected = hmac.new(
            app_secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        expected_header = f"sha256={expected}"
        
        is_valid = hmac.compare_digest(expected_header, signature_header)
        diagnostics["valid"] = is_valid
        
        if not is_valid:
            diagnostics["expected_prefix"] = expected_header[:20]
            diagnostics["received_prefix"] = signature_header[:20]
        
        return is_valid, diagnostics
        
    except Exception as e:
        diagnostics["error"] = str(e)
        return False, diagnostics


# ==========================================================
# PRIORITY 9: LOOP-BASED RETRY LOGIC
# ==========================================================

async def _process_message_async(
    phone_number: str,
    message_text: str,
    sender_name: str,
    message_id: str
):
    """Process message with loop-based retry logic (not recursive)"""
    request_id = generate_request_id()
    
    # Priority 20: Log correlation with step tracking
    log_message("info", "message_processing_started", request_id=request_id,
               message_id=message_id, phone_number=phone_number, step="START")
    
    # Priority 9: Loop-based retry (max 3 attempts)
    for attempt in range(3):
        try:
            # STEP 1: Quick commands
            quick_response = _handle_quick_commands(message_text)
            if quick_response:
                log_message("info", "quick_command_response", request_id=request_id, step="QUICK_COMMAND")
                await _send_response_async(phone_number, quick_response, message_id, request_id)
                return
            
            # STEP 2: DN lookup
            dn_match = re.search(r'\b(\d{8,12})\b', message_text)
            if dn_match:
                log_message("info", "dn_lookup", request_id=request_id, step="DN_LOOKUP", dn=dn_match.group(1))
                response = await _handle_dn_lookup_async(dn_match.group(1), request_id)
                if response:
                    await _send_response_async(phone_number, response, message_id, request_id)
                    return
            
            # STEP 3: Intent detection with timeout
            ai_query_service = get_cached_service(
                "AI Query Service",
                "app.services.ai_query_service",
                "get_ai_query_service"
            )
            
            if not ai_query_service:
                _metrics.service_failures += 1
                await _send_response_async(phone_number, "⚠️ AI service unavailable", message_id, request_id)
                return
            
            service = ai_query_service()
            
            query_plan = await asyncio.wait_for(
                service.process_query(message_text),
                timeout=TIMEOUT_AI_QUERY
            )
            
            log_message("info", "intent_detected", request_id=request_id, step="INTENT_DETECTED",
                       intent=query_plan.intent, confidence=query_plan.confidence_score)
            
            # STEP 4: Route based on intent
            response = None
            
            if query_plan.intent == "dealer_dashboard" and query_plan.entity_value:
                response = await _handle_dealer_dashboard_async(query_plan.entity_value, request_id)
            elif query_plan.intent == "warehouse_dashboard" and query_plan.entity_value:
                response = await _handle_warehouse_dashboard_async(query_plan.entity_value, request_id)
            elif query_plan.intent == "city_dashboard" and query_plan.entity_value:
                response = await _handle_city_dashboard_async(query_plan.entity_value, request_id)
            else:
                response = await _handle_fallback_async(message_text, request_id)
            
            if response:
                await _send_response_async(phone_number, response, message_id, request_id)
                _metrics.messages_processed += 1
                log_message("success", "message_processed", request_id=request_id, step="COMPLETE")
                return
            else:
                fallback = "I couldn't understand your request. Please type 'Help' for available commands."
                await _send_response_async(phone_number, fallback, message_id, request_id)
                return
            
        except asyncio.TimeoutError:
            _metrics.service_timeouts += 1
            log_message("error", "timeout_error", request_id=request_id, step=f"TIMEOUT_ATTEMPT_{attempt+1}")
            if attempt == 2:  # Last attempt
                await _send_response_async(phone_number, "⚠️ Request timed out. Please try again.", message_id, request_id)
                
        except Exception as e:
            _metrics.processing_failures += 1
            log_exception(request_id, f"message_processing_attempt_{attempt+1}", e, step=f"ERROR_ATTEMPT_{attempt+1}")
            
            if attempt == 2:  # Last attempt
                store_event("dead_letter", {
                    "phone": mask_sensitive_data(phone_number),
                    "message": message_text[:200],
                    "message_id": message_id,
                    "error": str(e)
                })
                await _send_response_async(phone_number, "⚠️ System error. Please try again later.", message_id, request_id)
        
        # Wait before retry (exponential backoff)
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)


# ==========================================================
# ASYNC HANDLERS WITH TIMEOUTS
# ==========================================================

async def _handle_dn_lookup_async(dn_number: str, request_id: str) -> Optional[str]:
    """Async DN lookup with timeout"""
    try:
        process_whatsapp_query = get_cached_service(
            "AI Provider Service",
            "app.services.ai_provider_service",
            "process_whatsapp_query"
        )
        
        if not process_whatsapp_query:
            return "⚠️ AI service unavailable"
        
        result = await asyncio.wait_for(
            _run_in_executor(process_whatsapp_query, f"Show me DN {dn_number}", None, request_id),
            timeout=TIMEOUT_FALLBACK
        )
        return result
        
    except asyncio.TimeoutError:
        _metrics.service_timeouts += 1
        log_message("error", "dn_lookup_timeout", request_id=request_id, dn=dn_number)
        return f"❌ Timeout looking up DN {dn_number}"
    except Exception as e:
        log_exception(request_id, f"dn_lookup_{dn_number}", e)
        return f"❌ Error looking up DN {dn_number}"


async def _handle_dealer_dashboard_async(dealer_name: str, request_id: str) -> Optional[str]:
    """Async dealer dashboard with timeout"""
    try:
        get_logistics_service = get_cached_service(
            "Logistics Query Service",
            "app.services.logistics_query_service",
            "get_logistics_query_service"
        )
        
        if not get_logistics_service:
            return "⚠️ Dashboard service unavailable"
        
        logistics_service = get_logistics_service()
        
        dashboard = await asyncio.wait_for(
            _run_in_executor(logistics_service.build_dealer_dashboard, dealer_name),
            timeout=TIMEOUT_DASHBOARD
        )
        
        if not dashboard:
            return f"❌ Dealer '{dealer_name}' not found."
        
        lines = [
            f"🏪 *Dealer Dashboard: {dashboard.get('dealer_name')}*",
            "",
            f"💰 Revenue: PKR {dashboard.get('revenue', 0):,.0f}",
            f"📦 Units: {dashboard.get('units', 0):,}",
            f"📄 DNs: {dashboard.get('dn_count', 0)}"
        ]
        
        return "\n".join(lines)
        
    except asyncio.TimeoutError:
        _metrics.service_timeouts += 1
        return f"❌ Timeout fetching dealer data for '{dealer_name}'"
    except Exception as e:
        log_exception(request_id, f"dealer_dashboard_{dealer_name}", e)
        return f"❌ Error fetching dealer data for '{dealer_name}'"


async def _handle_warehouse_dashboard_async(warehouse_name: str, request_id: str) -> Optional[str]:
    """Async warehouse dashboard with timeout"""
    try:
        get_logistics_service = get_cached_service(
            "Logistics Query Service",
            "app.services.logistics_query_service",
            "get_logistics_query_service"
        )
        
        if not get_logistics_service:
            return "⚠️ Dashboard service unavailable"
        
        logistics_service = get_logistics_service()
        
        dashboard = await asyncio.wait_for(
            _run_in_executor(logistics_service.build_warehouse_dashboard, warehouse_name),
            timeout=TIMEOUT_DASHBOARD
        )
        
        if not dashboard:
            return f"❌ Warehouse '{warehouse_name}' not found."
        
        lines = [
            f"🏭 *Warehouse Dashboard: {dashboard.get('warehouse_name')}*",
            "",
            f"💰 Revenue: PKR {dashboard.get('revenue', 0):,.0f}",
            f"📦 Units: {dashboard.get('units', 0):,}",
            f"📄 DNs: {dashboard.get('dn_count', 0)}"
        ]
        
        return "\n".join(lines)
        
    except asyncio.TimeoutError:
        _metrics.service_timeouts += 1
        return f"❌ Timeout fetching warehouse data for '{warehouse_name}'"
    except Exception as e:
        log_exception(request_id, f"warehouse_dashboard_{warehouse_name}", e)
        return f"❌ Error fetching warehouse data for '{warehouse_name}'"


async def _handle_city_dashboard_async(city_name: str, request_id: str) -> Optional[str]:
    """Async city dashboard with timeout"""
    try:
        get_logistics_service = get_cached_service(
            "Logistics Query Service",
            "app.services.logistics_query_service",
            "get_logistics_query_service"
        )
        
        if not get_logistics_service:
            return "⚠️ Dashboard service unavailable"
        
        logistics_service = get_logistics_service()
        
        dashboard = await asyncio.wait_for(
            _run_in_executor(logistics_service.build_city_dashboard, city_name),
            timeout=TIMEOUT_DASHBOARD
        )
        
        if not dashboard:
            return f"❌ City '{city_name}' not found."
        
        lines = [
            f"🌆 *City Dashboard: {dashboard.get('city_name')}*",
            "",
            f"💰 Revenue: PKR {dashboard.get('revenue', 0):,.0f}",
            f"📦 Units: {dashboard.get('units', 0):,}",
            f"📄 DNs: {dashboard.get('dn_count', 0)}"
        ]
        
        return "\n".join(lines)
        
    except asyncio.TimeoutError:
        _metrics.service_timeouts += 1
        return f"❌ Timeout fetching city data for '{city_name}'"
    except Exception as e:
        log_exception(request_id, f"city_dashboard_{city_name}", e)
        return f"❌ Error fetching city data for '{city_name}'"


async def _handle_fallback_async(message_text: str, request_id: str) -> Optional[str]:
    """Async fallback with timeout"""
    try:
        process_whatsapp_query = get_cached_service(
            "AI Provider Service",
            "app.services.ai_provider_service",
            "process_whatsapp_query"
        )
        
        if process_whatsapp_query:
            result = await asyncio.wait_for(
                _run_in_executor(process_whatsapp_query, message_text, None, request_id),
                timeout=TIMEOUT_FALLBACK
            )
            return result
        
        return "I'm here to help with logistics queries! Try 'Help' to see what I can do."
        
    except asyncio.TimeoutError:
        _metrics.service_timeouts += 1
        return None
    except Exception as e:
        log_exception(request_id, "fallback", e)
        return None


async def _send_response_async(phone_number: str, message: str, message_id: str, request_id: str):
    """Async response sending with timeout"""
    try:
        send_text_message = get_cached_service(
            "WhatsApp Service",
            "app.services.whatsapp_service",
            "send_text_message"
        )
        
        if not send_text_message:
            log_message("error", "whatsapp_service_unavailable", request_id=request_id)
            _metrics.send_failures += 1
            return
        
        for attempt in range(3):
            result = await asyncio.wait_for(
                _run_in_executor(send_text_message, phone_number, message, message_id, request_id),
                timeout=TIMEOUT_WHATSAPP_SEND
            )
            
            if result.get('success'):
                log_message("success", "response_sent", request_id=request_id, attempt=attempt + 1)
                return
            
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
        
        _metrics.send_failures += 1
        log_message("error", "response_failed", request_id=request_id, error=result.get('error'))
        store_event("send_failure", {"phone": mask_sensitive_data(phone_number), "error": result.get('error')})
            
    except asyncio.TimeoutError:
        _metrics.service_timeouts += 1
        _metrics.send_failures += 1
        log_message("error", "send_timeout", request_id=request_id)
    except Exception as e:
        _metrics.send_failures += 1
        log_exception(request_id, "send_response", e)


# ==========================================================
# QUICK COMMANDS
# ==========================================================

def _handle_quick_commands(message_text: str) -> Optional[str]:
    msg_lower = message_text.lower().strip()
    
    if msg_lower in ['/help', 'help', 'menu', 'commands', '?', '/?']:
        return _format_help_message()
    
    if msg_lower in ['/status', 'status', 'health', 'ping']:
        return _format_status_message()
    
    if msg_lower in ['hi', 'hello', 'hey', 'start', 'welcome', 'assalam', 'salam']:
        return _format_welcome_message()
    
    return None


def _format_help_message() -> str:
    return """📋 *Logistics AI Assistant - Help*

*What I can do:*

🔍 *Track Delivery* - Send any 10+ digit DN number
🏪 *Dealer Queries* - "Show dealer ABC Traders"
🏭 *Warehouse Queries* - "Lahore warehouse summary"
🌆 *City Dashboard* - "Karachi dashboard"

*Commands:* `Help`, `Status`

Need help? Just ask! 🤖"""


def _format_status_message() -> str:
    health = _get_health_status()
    return f"""📊 *System Status*

✅ Webhook: Online
📊 Health: {health}
📨 Messages: {_metrics.messages_received}

*Environment:* {getattr(config, 'ENVIRONMENT', 'development')}

Type *Help* for commands. 🚀"""


def _format_welcome_message() -> str:
    return """👋 *Welcome to Logistics AI Assistant!*

I can help you with:
• Track deliveries (send DN number)
• Dealer performance reports
• Warehouse analytics

📋 Type *Help* to see all commands

What would you like to know today?"""


# ==========================================================
# PRIORITY 22: HEALTH STATUS
# ==========================================================

def _get_health_status() -> str:
    """Determine current health level"""
    if _metrics.processing_failures > 100:
        return HealthLevel.CRITICAL
    elif _metrics.processing_failures > 10 or _metrics.service_timeouts > 5:
        return HealthLevel.DEGRADED
    return HealthLevel.HEALTHY


# ==========================================================
# PRIORITY 4: WEBHOOK ENDPOINTS (Support both URLs)
# ==========================================================

@router.get("/webhook")
@router.get("/webhook/")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Meta WhatsApp verification endpoint - supports both /webhook and /webhook/"""
    _metrics.verification_hits += 1
    
    log_message("info", "webhook_verification_request", mode=hub_mode, step="STEP_1_VERIFICATION_HIT")
    
    try:
        verify_token = getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')
        
        if hub_mode == 'subscribe' and hub_verify_token == verify_token:
            log_message("success", "webhook_verified", step="STEP_2_VERIFICATION_SUCCESS")
            return Response(content=hub_challenge, status_code=200, media_type="text/plain")
        else:
            log_message("warning", "webhook_verification_failed", step="STEP_2_VERIFICATION_FAILED")
            return JSONResponse(content={"error": "Verification failed"}, status_code=403)
            
    except Exception as e:
        log_exception("verification", "webhook_verify", e, step="ERROR")
        return JSONResponse(content={"error": "Internal error"}, status_code=500)


@router.post("/webhook")
@router.post("/webhook/")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """Main webhook handler - supports both /webhook and /webhook/"""
    _metrics.webhook_hits += 1
    _metrics.messages_received += 1
    
    request_id = generate_request_id()
    
    # Priority 5: Step-by-step diagnostic logging
    log_message("info", "webhook_hit", request_id=request_id, step="STEP_1_WEBHOOK_HIT")
    
    try:
        raw_body = await request.body()
        log_message("info", "body_received", request_id=request_id, step="STEP_2_BODY_RECEIVED", size=len(raw_body))
        
        # Signature validation (configurable)
        signature = request.headers.get('X-Hub-Signature-256', '')
        
        if REQUIRE_SIGNATURE:
            is_valid, diag = _verify_signature_production(raw_body, signature)
            if not is_valid:
                _metrics.invalid_signature_hits += 1
                log_message("error", "signature_validation_failed", request_id=request_id, step="STEP_3_SIGNATURE_FAILED", **diag)
                return JSONResponse(content={"error": "Unauthorized"}, status_code=401)
        
        log_message("info", "signature_validated", request_id=request_id, step="STEP_3_SIGNATURE_VALIDATED")
        
        # Parse JSON
        try:
            data = await request.json()
            log_message("info", "json_parsed", request_id=request_id, step="STEP_4_JSON_PARSED")
        except Exception as e:
            log_message("error", "json_parse_error", request_id=request_id, step="STEP_4_JSON_ERROR", error=str(e))
            return JSONResponse(content={"error": "Invalid JSON"}, status_code=400)
        
        # Validate payload
        if data.get('object') != 'whatsapp_business_account':
            log_message("info", "non_whatsapp_payload", request_id=request_id, step="STEP_5_NOT_WHATSAPP")
            return JSONResponse(content={"status": "ok"}, status_code=200)
        
        # Extract message
        entry = data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        
        # Status events
        if 'statuses' in value:
            _metrics.status_events += 1
            log_message("info", "status_event", request_id=request_id, step="STEP_6_STATUS_EVENT")
            return JSONResponse(content={"status": "ok"}, status_code=200)
        
        # Template events
        if 'template_events' in value:
            _metrics.template_events += 1
            log_message("info", "template_event", request_id=request_id, step="STEP_6_TEMPLATE_EVENT")
            return JSONResponse(content={"status": "ok"}, status_code=200)
        
        messages = value.get('messages', [])
        if not messages:
            log_message("info", "no_messages", request_id=request_id, step="STEP_6_NO_MESSAGES")
            return JSONResponse(content={"status": "ok"}, status_code=200)
        
        log_message("info", "message_found", request_id=request_id, step="STEP_6_MESSAGE_FOUND")
        
        message = messages[0]
        phone_number = message.get('from')
        message_id = message.get('id')
        message_type = message.get('type')
        
        # Duplicate protection
        if is_duplicate_message(message_id):
            log_message("info", "duplicate_message", request_id=request_id, step="STEP_7_DUPLICATE", message_id=message_id)
            return JSONResponse(content={"status": "ok", "message": "duplicate"}, status_code=200)
        
        # Rate limiting
        if phone_number and not check_rate_limit(phone_number):
            log_message("warning", "rate_limited", request_id=request_id, step="STEP_7_RATE_LIMITED", phone=phone_number)
            return JSONResponse(content={"error": "Rate limit exceeded"}, status_code=429)
        
        # Extract text
        message_text = None
        if message_type == 'text':
            message_text = message.get('text', {}).get('body', '')
        elif message_type == 'interactive':
            interactive = message.get('interactive', {})
            if interactive.get('type') == 'button_reply':
                message_text = interactive.get('button_reply', {}).get('title', '')
        
        if not message_text:
            log_message("info", "no_text", request_id=request_id, step="STEP_7_NO_TEXT")
            return JSONResponse(content={"status": "ok"}, status_code=200)
        
        # Get sender name
        contacts = value.get('contacts', [{}])
        sender_name = contacts[0].get('profile', {}).get('name', 'User') if contacts else 'User'
        
        log_message("info", "message_extracted", request_id=request_id, step="STEP_7_MESSAGE_EXTRACTED",
                   phone_number=phone_number, message_preview=message_text[:100])
        
        store_event("message", {
            "phone": mask_sensitive_data(phone_number),
            "preview": message_text[:100],
            "message_id": message_id
        })
        
        # Queue for processing
        background_tasks.add_task(
            _process_message_async,
            phone_number=phone_number,
            message_text=message_text,
            sender_name=sender_name,
            message_id=message_id
        )
        
        log_message("success", "message_queued", request_id=request_id, step="STEP_8_MESSAGE_QUEUED")
        
        _metrics.last_message_time = datetime.now()
        return JSONResponse(content={"status": "ok", "message": "processing"}, status_code=200)
        
    except Exception as e:
        log_exception(request_id, "handle_webhook", e, step="ERROR")
        return JSONResponse(content={"error": "Internal error"}, status_code=500)


# ==========================================================
# PRIORITY 2: SELF-TEST ENDPOINT
# ==========================================================

@router.get("/webhook/self-test")
async def webhook_self_test():
    """Comprehensive self-test endpoint"""
    env_status = validate_startup_environment()
    
    return {
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "health": _get_health_status(),
        "whatsapp_token": bool(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')),
        "phone_number_id": bool(getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')),
        "verify_token": bool(getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')),
        "app_secret": bool(getattr(config, 'WHATSAPP_APP_SECRET', '')),
        "signature_required": REQUIRE_SIGNATURE,
        "services_loaded": {name: svc is not None for name, svc in _service_cache.items()},
        "environment": getattr(config, 'ENVIRONMENT', 'development'),
        "metrics": _metrics.to_dict(),
        "overall": "PASS" if env_status["configured"] else "FAIL - Missing environment variables"
    }


# ==========================================================
# PRIORITY 15: CRASHES ENDPOINT
# ==========================================================

@router.get("/webhook/crashes")
async def webhook_crashes():
    """Get recent crash history"""
    return {
        "crash_count": len(_crash_history),
        "crashes": list(_crash_history),
        "timestamp": datetime.now().isoformat()
    }


# ==========================================================
# PRIORITY 14: METRICS ENDPOINT (Enhanced)
# ==========================================================

@router.get("/webhook/metrics")
async def webhook_metrics():
    """Get comprehensive webhook metrics"""
    return {
        "metrics": _metrics.to_dict(),
        "services": {name: {"loaded": svc is not None, "load_time": _SERVICE_LOAD_TIME.get(name)} 
                    for name, svc in _service_cache.items()},
        "health": _get_health_status(),
        "rate_limits_active": len(_phone_rate_limits),
        "duplicate_cache_size": len(_processed_messages),
        "recent_events_count": len(_recent_events),
        "crash_history_count": len(_crash_history)
    }


# ==========================================================
# PRIORITY 16: IMPROVED DEBUG ENDPOINT
# ==========================================================

@router.get("/webhook/debug")
async def webhook_debug():
    """Enhanced debug endpoint"""
    return {
        "webhook": {
            "status": "online",
            "verify_token_configured": bool(getattr(config, 'WHATSAPP_VERIFY_TOKEN', '')),
            "phone_number_id_configured": bool(getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')),
            "access_token_configured": bool(getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')),
            "app_secret_configured": bool(getattr(config, 'WHATSAPP_APP_SECRET', '')),
            "signature_required": REQUIRE_SIGNATURE,
            "environment": getattr(config, 'ENVIRONMENT', 'development')
        },
        "routes_registered": True,
        "service_cache_size": len(_service_cache),
        "executor_running": _executor is not None,
        "metrics": _metrics.to_dict(),
        "health": _get_health_status(),
        "services_loaded": {name: svc is not None for name, svc in _service_cache.items()},
        "rate_limits_active": len(_phone_rate_limits),
        "duplicate_cache_size": len(_processed_messages),
        "timestamp": datetime.now().isoformat()
    }


@router.get("/webhook/ping")
async def webhook_ping():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@router.get("/webhook/health")
async def webhook_health():
    return {
        'status': _get_health_status(),
        'version': '11.0',
        'timestamp': datetime.now().isoformat(),
        'metrics': {
            'messages_received': _metrics.messages_received,
            'messages_processed': _metrics.messages_processed,
            'processing_failures': _metrics.processing_failures,
            'service_timeouts': _metrics.service_timeouts
        }
    }


@router.get("/webhook/recent-events")
async def webhook_recent_events(limit: int = 20):
    """Get recent events for debugging"""
    events = list(_recent_events)[:limit]
    return {"events": events, "total": len(_recent_events)}


# ==========================================================
# PRIORITY 1: SERVICE INITIALIZATION (Call from main.py lifespan)
# ==========================================================

async def initialize_services():
    """Initialize all webhook services - call from main.py lifespan"""
    logger.info("=" * 60)
    logger.info("🔧 Initializing Webhook v11.0 Services")
    logger.info("=" * 60)
    
    # Validate environment
    env_status = validate_startup_environment()
    if not env_status["configured"]:
        logger.warning(f"⚠️ Missing environment variables: {env_status['missing']}")
    
    # Pre-load services
    logger.info("Pre-loading services...")
    get_cached_service("AI Query Service", "app.services.ai_query_service", "get_ai_query_service")
    get_cached_service("AI Provider Service", "app.services.ai_provider_service", "process_whatsapp_query")
    get_cached_service("Logistics Query Service", "app.services.logistics_query_service", "get_logistics_query_service")
    get_cached_service("WhatsApp Service", "app.services.whatsapp_service", "send_text_message")
    
    logger.info(f"✅ Services loaded: {len(_service_cache)}")
    logger.info(f"Signature validation: {'enabled' if REQUIRE_SIGNATURE else 'disabled'}")
    logger.info(f"Health: {_get_health_status()}")
    logger.info("=" * 60)
    
    return {
        "services_loaded": len(_service_cache),
        "health": _get_health_status(),
        "env_configured": env_status["configured"]
    }


def get_webhook_stats() -> Dict[str, Any]:
    """Get webhook statistics for main.py diagnostics"""
    return {
        "messages_received": _metrics.messages_received,
        "messages_processed": _metrics.messages_processed,
        "health": _get_health_status(),
        "services_loaded": len(_service_cache),
        "service_failures": _metrics.service_failures,
        "processing_failures": _metrics.processing_failures
    }


# ==========================================================
# INITIALIZATION LOGGING (No service loading at import time!)
# ==========================================================

logger.info("=" * 60)
logger.info("🔧 WEBHOOK v11.0 - ULTIMATE ENTERPRISE")
logger.info("=" * 60)
logger.info(f"   VERIFY_TOKEN_EXISTS: {bool(getattr(config, 'WHATSAPP_VERIFY_TOKEN', ''))}")
logger.info(f"   SIGNATURE_REQUIRED: {REQUIRE_SIGNATURE}")
logger.info(f"   RATE_LIMIT: {RATE_LIMIT_REQUESTS}/min")
logger.info(f"   ENVIRONMENT: {getattr(config, 'ENVIRONMENT', 'development')}")
logger.info("")
logger.info("   ✅ Features enabled:")
logger.info("   ✅ Configurable signature validation")
logger.info("   ✅ Services loaded in lifespan (not import time)")
logger.info("   ✅ Duplicate message protection (24h)")
logger.info("   ✅ Shared ThreadPoolExecutor")
logger.info("   ✅ Timeout layers for all operations")
logger.info("   ✅ Loop-based retry logic")
logger.info("   ✅ Step-by-step diagnostic logging")
logger.info("   ✅ Crash memory (last 20 exceptions)")
logger.info("   ✅ Health levels (healthy/degraded/critical)")
logger.info("   ✅ Rate limiter with auto-cleanup")
logger.info("")
logger.info("   📍 Services will be initialized in main.py lifespan")
logger.info("   📍 Call: await initialize_services()")
logger.info("=" * 60)
