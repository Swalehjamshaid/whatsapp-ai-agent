# ==========================================================
# FILE: app/services/whatsapp_service.py (ENTERPRISE v5.0)
# ==========================================================
# PRODUCTION READY v5.0
# - ADDED: Database message log persistence
# - ADDED: Full Meta error logging
# - ADDED: Redis-based duplicate detection
# - ADDED: Connection pool retry with urllib3
# - ADDED: Async support with httpx
# - ADDED: Startup configuration validation
# - ADDED: Real WhatsApp health verification
# - ADDED: E.164 phone validation
# - ADDED: Delivery status tracking
# - ADDED: Diagnostic logging
# - ADDED: Comprehensive metrics
# - FIXED: Removed dangerous imports
# ==========================================================

import requests
import logging
import json
import re
import time
import asyncio
from typing import Optional, Dict, Any, List, Union, Tuple
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from urllib3.util.retry import Retry
from urllib3.exceptions import MaxRetryError

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_API_VERSION,
    WHATSAPP_API_URL
)

logger = logging.getLogger(__name__)

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = 4000
MAX_WHATSAPP_LIMIT = 3500
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]
MESSAGE_LOG_TTL = 86400  # 24 hours
HEALTH_CHECK_TIMEOUT = 10

# ==========================================================
# PRIORITY 3: REDIS DUPLICATE CACHE
# ==========================================================

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available - using memory cache")

class RedisMessageCache:
    """Redis-based message cache for duplicate detection"""
    
    def __init__(self):
        self.redis_client = None
        self.redis_available = False
        self.memory_cache = {}
        self.expiry = 3600
    
        if REDIS_AVAILABLE:
            try:
                from app.config import config
                if hasattr(config, 'REDIS_URL') and config.REDIS_URL:
                    self.redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
                    self.redis_client.ping()
                    self.redis_available = True
                    logger.info("✅ Redis message cache enabled")
            except Exception as e:
                logger.warning(f"Redis message cache failed: {e}")
    
    def is_duplicate(self, message_id: str) -> bool:
        if not message_id:
            return False
        
        try:
            if self.redis_available and self.redis_client:
                key = f"msg:{message_id}"
                return self.redis_client.exists(key) > 0
            
            if message_id in self.memory_cache:
                return True
            return False
        except Exception as e:
            logger.error(f"Duplicate check error: {e}")
            return False
    
    def mark_processed(self, message_id: str):
        if not message_id:
            return
        
        try:
            if self.redis_available and self.redis_client:
                key = f"msg:{message_id}"
                self.redis_client.setex(key, self.expiry, "1")
                return
            
            self.memory_cache[message_id] = time.time()
            now = time.time()
            expired = [k for k, v in self.memory_cache.items() if now - v > self.expiry]
            for k in expired:
                del self.memory_cache[k]
        except Exception as e:
            logger.error(f"Mark processed error: {e}")

_message_cache = RedisMessageCache()

def is_duplicate_message(message_id: str) -> bool:
    return _message_cache.is_duplicate(message_id)

def mark_message_processed(message_id: str):
    _message_cache.mark_processed(message_id)

# ==========================================================
# PRIORITY 1: DATABASE MESSAGE LOG
# ==========================================================

class MessageLogger:
    """Persistent message logging to database"""
    
    def __init__(self):
        self._initialized = False
    
    def _ensure_table(self):
        if self._initialized:
            return
        
        try:
            from sqlalchemy import text
            from app.database import engine
            
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS whatsapp_message_log (
                        id SERIAL PRIMARY KEY,
                        message_id VARCHAR(255) UNIQUE,
                        phone_number VARCHAR(20),
                        request_id VARCHAR(50),
                        message_preview TEXT,
                        status VARCHAR(50),
                        status_code INTEGER,
                        error TEXT,
                        retry_count INTEGER DEFAULT 0,
                        sent_at TIMESTAMP,
                        delivered_at TIMESTAMP,
                        read_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """))
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_msg_phone ON whatsapp_message_log(phone_number);
                    CREATE INDEX IF NOT EXISTS idx_msg_status ON whatsapp_message_log(status);
                    CREATE INDEX IF NOT EXISTS idx_msg_sent_at ON whatsapp_message_log(sent_at);
                """))
                conn.commit()
            self._initialized = True
            logger.info("✅ Message log table initialized")
        except Exception as e:
            logger.error(f"Failed to create message log table: {e}")
    
    def log_send(self, message_id: str, phone_number: str, request_id: str, 
                 message_preview: str, status: str = 'sent', status_code: int = None,
                 error: str = None, retry_count: int = 0):
        """Log message send attempt"""
        try:
            self._ensure_table()
            from sqlalchemy import text
            from app.database import SessionLocal
            
            db = SessionLocal()
            db.execute(text("""
                INSERT INTO whatsapp_message_log 
                (message_id, phone_number, request_id, message_preview, status, status_code, error, retry_count, sent_at)
                VALUES (:message_id, :phone_number, :request_id, :message_preview, :status, :status_code, :error, :retry_count, NOW())
                ON CONFLICT (message_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    status_code = EXCLUDED.status_code,
                    error = EXCLUDED.error,
                    retry_count = EXCLUDED.retry_count
            """), {
                "message_id": message_id,
                "phone_number": phone_number,
                "request_id": request_id,
                "message_preview": message_preview[:200],
                "status": status,
                "status_code": status_code,
                "error": error[:500] if error else None,
                "retry_count": retry_count
            })
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to log message: {e}")
    
    def update_status(self, message_id: str, status: str):
        """Update message delivery status from webhook"""
        try:
            self._ensure_table()
            from sqlalchemy import text
            from app.database import SessionLocal
            
            field = "delivered_at" if status == "delivered" else "read_at" if status == "read" else None
            db = SessionLocal()
            
            if field:
                db.execute(text(f"""
                    UPDATE whatsapp_message_log 
                    SET status = :status, {field} = NOW()
                    WHERE message_id = :message_id
                """), {"status": status, "message_id": message_id})
            else:
                db.execute(text("""
                    UPDATE whatsapp_message_log 
                    SET status = :status
                    WHERE message_id = :message_id
                """), {"status": status, "message_id": message_id})
            
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to update message status: {e}")
    
    def get_stats(self) -> Dict:
        """Get message statistics"""
        try:
            self._ensure_table()
            from sqlalchemy import text
            from app.database import SessionLocal
            
            db = SessionLocal()
            result = db.execute(text("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) as sent,
                    SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) as delivered,
                    SUM(CASE WHEN status = 'read' THEN 1 ELSE 0 END) as read,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
                FROM whatsapp_message_log
                WHERE sent_at > NOW() - INTERVAL '24 hours'
            """)).first()
            db.close()
            
            return {
                "total_24h": result[0] or 0,
                "sent": result[1] or 0,
                "delivered": result[2] or 0,
                "read": result[3] or 0,
                "failed": result[4] or 0
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}

_message_logger = MessageLogger()

# ==========================================================
# PRIORITY 8: E.164 PHONE VALIDATION
# ==========================================================

def validate_e164_phone(phone_number: str) -> Tuple[bool, str]:
    """
    Validate and normalize phone number to E.164 format
    Returns (is_valid, normalized_number)
    """
    if not phone_number:
        return False, phone_number
    
    # Remove all non-digit characters
    cleaned = re.sub(r'\D', '', phone_number)
    
    # Pakistan: Add 92 if missing and number starts with 3
    if len(cleaned) == 10 and cleaned.startswith('3'):
        cleaned = '92' + cleaned
        logger.info(f"Added Pakistan country code: {cleaned}")
    
    # Remove leading zeros after country code detection
    if len(cleaned) > 10 and cleaned.startswith('0'):
        cleaned = cleaned.lstrip('0')
    
    # Add + prefix
    normalized = '+' + cleaned
    
    # Validate length (10-15 digits without +)
    if 10 <= len(cleaned) <= 15:
        return True, normalized
    
    logger.warning(f"Invalid phone number length: {len(cleaned)} digits for {phone_number}")
    return False, phone_number

def validate_phone_number(phone_number: str) -> bool:
    """Legacy validation - returns bool only"""
    is_valid, _ = validate_e164_phone(phone_number)
    return is_valid

def normalize_phone_number(phone_number: str) -> str:
    """Normalize to E.164 format"""
    _, normalized = validate_e164_phone(phone_number)
    return normalized.lstrip('+')  # WhatsApp API expects without +

# ==========================================================
# PRIORITY 6: STARTUP CONFIGURATION VALIDATION
# ==========================================================

def validate_whatsapp_configuration() -> Dict[str, Any]:
    """Validate WhatsApp configuration at startup"""
    errors = []
    warnings = []
    
    # Check required configs
    if not WHATSAPP_ACCESS_TOKEN:
        errors.append("WHATSAPP_ACCESS_TOKEN is missing")
    elif len(WHATSAPP_ACCESS_TOKEN) < 50:
        warnings.append("WHATSAPP_ACCESS_TOKEN seems too short")
    
    if not WHATSAPP_PHONE_NUMBER_ID:
        errors.append("WHATSAPP_PHONE_NUMBER_ID is missing")
    
    # Validate API version format
    if WHATSAPP_API_VERSION:
        if not re.match(r'^v\d+\.\d+$', WHATSAPP_API_VERSION):
            warnings.append(f"WHATSAPP_API_VERSION format may be incorrect: {WHATSAPP_API_VERSION}")
    
    if errors:
        logger.error("WhatsApp configuration validation FAILED:")
        for error in errors:
            logger.error(f"  - {error}")
        return {"valid": False, "errors": errors, "warnings": warnings}
    
    if warnings:
        logger.warning("WhatsApp configuration validation has warnings:")
        for warning in warnings:
            logger.warning(f"  - {warning}")
    
    logger.info("✅ WhatsApp configuration validation passed")
    return {"valid": True, "errors": errors, "warnings": warnings}

# ==========================================================
# PRIORITY 7: REAL WHATSAPP HEALTH VERIFICATION
# ==========================================================

def check_whatsapp_health() -> Dict[str, Any]:
    """Verify WhatsApp API health with actual API call"""
    
    # First validate configuration
    config_check = validate_whatsapp_configuration()
    if not config_check["valid"]:
        return {
            "available": False,
            "mode": "invalid_config",
            "errors": config_check["errors"],
            "api_version": WHATSAPP_API_VERSION or "v25.0",
            "phone_number_id": WHATSAPP_PHONE_NUMBER_ID[:10] + "..." if WHATSAPP_PHONE_NUMBER_ID else None
        }
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {
            "available": False,
            "mode": "demo",
            "error": "Missing credentials",
            "api_version": WHATSAPP_API_VERSION or "v25.0",
            "phone_number_id": None
        }
    
    try:
        api_version = WHATSAPP_API_VERSION or "v25.0"
        api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
        url = f"{api_url}/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        
        response = requests.get(url, headers=headers, timeout=HEALTH_CHECK_TIMEOUT)
        
        # Priority 2: Full error logging
        logger.info("=" * 60)
        logger.info("WhatsApp Health Check Response:")
        logger.info(f"  Status: {response.status_code}")
        logger.info(f"  Headers: {dict(response.headers)}")
        logger.info(f"  Body: {response.text[:500]}")
        logger.info("=" * 60)
        
        if response.status_code == 200:
            data = response.json()
            return {
                "available": True,
                "mode": "production",
                "verified": True,
                "phone_number": data.get("display_phone_number"),
                "quality_rating": data.get("quality_rating"),
                "status": data.get("status"),
                "api_version": api_version,
                "phone_number_id": WHATSAPP_PHONE_NUMBER_ID[:10] + "..."
            }
        elif response.status_code == 401:
            return {
                "available": False,
                "mode": "production",
                "verified": False,
                "error": "Invalid access token - 401 Unauthorized",
                "status_code": 401,
                "api_version": api_version
            }
        elif response.status_code == 403:
            return {
                "available": False,
                "mode": "production",
                "verified": False,
                "error": "Permission denied - 403 Forbidden",
                "status_code": 403,
                "api_version": api_version
            }
        else:
            return {
                "available": False,
                "mode": "production",
                "verified": False,
                "error": f"API returned {response.status_code}",
                "response": response.text[:200],
                "status_code": response.status_code,
                "api_version": api_version
            }
            
    except requests.exceptions.Timeout:
        return {
            "available": False,
            "mode": "production",
            "verified": False,
            "error": "Connection timeout - Meta API unreachable",
            "api_version": WHATSAPP_API_VERSION or "v25.0"
        }
    except Exception as e:
        return {
            "available": False,
            "mode": "production",
            "verified": False,
            "error": str(e),
            "api_version": WHATSAPP_API_VERSION or "v25.0"
        }

# ==========================================================
# WHATSAPP SESSION WITH RETRY (Priority 4)
# ==========================================================

class WhatsAppSession:
    _instance = None
    _session = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            
            # Priority 4: Configure retry strategy
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["POST", "GET"]
            )
            
            cls._session = requests.Session()
            cls._session.headers.update({"Content-Type": "application/json"})
            
            # Mount with retry adapter
            adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
            cls._session.mount("https://", adapter)
            cls._session.mount("http://", adapter)
            
        return cls._instance
    
    def get_session(self) -> requests.Session:
        return self._session

_whatsapp_session = WhatsAppSession()

# ==========================================================
# URL CONSTRUCTION
# ==========================================================

def get_whatsapp_url():
    api_version = WHATSAPP_API_VERSION or "v25.0"
    api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
    return f"{api_url}/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

# ==========================================================
# MESSAGE CLEANING
# ==========================================================

def clean_message_for_whatsapp(message: Union[str, Dict, Any]) -> str:
    if isinstance(message, dict):
        cleaned = message.get("response") or message.get("formatted_message") or message.get("message") or str(message)
    elif isinstance(message, str):
        cleaned = message
    else:
        cleaned = str(message)
    
    if cleaned.strip().startswith('{') or cleaned.strip().startswith('['):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                cleaned = parsed.get("response") or parsed.get("formatted_message") or parsed.get("message") or cleaned
        except:
            pass
    return cleaned

# ==========================================================
# SEND RESULT
# ==========================================================

@dataclass
class SendResult:
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    mode: str = "api"
    processing_time_ms: float = 0
    message_length: int = 0
    phone_number: str = ""
    retry_count: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "message_id": self.message_id,
            "error": self.error,
            "status_code": self.status_code,
            "mode": self.mode,
            "processing_time_ms": self.processing_time_ms,
            "message_length": self.message_length,
            "phone_number": self.phone_number,
            "retry_count": self.retry_count
        }
    
    def log_summary(self):
        if self.success:
            logger.info(f"WhatsApp Send SUCCESS | Phone={self.phone_number} | ID={self.message_id} | Time={self.processing_time_ms:.0f}ms | Len={self.message_length}")
        else:
            logger.error(f"WhatsApp Send FAILED | Phone={self.phone_number} | Error={self.error} | Status={self.status_code}")

# ==========================================================
# PRIORITY 11: METRICS COLLECTOR
# ==========================================================

class WhatsAppMetrics:
    def __init__(self):
        self.total_sent = 0
        self.successful_sends = 0
        self.failed_sends = 0
        self.total_processing_time_ms = 0
        self.last_error = None
        self.retry_count = 0
        self.api_errors = {}
    
    def record_send(self, result: SendResult):
        self.total_sent += 1
        if result.success:
            self.successful_sends += 1
        else:
            self.failed_sends += 1
            self.last_error = result.error
            if result.status_code:
                self.api_errors[result.status_code] = self.api_errors.get(result.status_code, 0) + 1
        self.retry_count += result.retry_count
        self.total_processing_time_ms += result.processing_time_ms
    
    def get_stats(self) -> Dict:
        return {
            "total_sent": self.total_sent,
            "successful_sends": self.successful_sends,
            "failed_sends": self.failed_sends,
            "success_rate": round(self.successful_sends / max(1, self.total_sent) * 100, 1),
            "avg_processing_time_ms": round(self.total_processing_time_ms / max(1, self.total_sent), 2),
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "api_errors": self.api_errors
        }

whatsapp_metrics = WhatsAppMetrics()

# ==========================================================
# PRIORITY 5: ASYNC SEND FUNCTION (Non-blocking)
# ==========================================================

async def send_text_message_async(phone_number: str, message: Union[str, Dict, Any], 
                                   request_id: str = None) -> Dict[str, Any]:
    """
    Async version of send_text_message - non-blocking for FastAPI
    """
    import httpx
    
    start_time = time.time()
    retry_count = 0
    
    if not message:
        return {"success": False, "error": "Empty message"}
    
    clean_message = clean_message_for_whatsapp(message)
    
    if not clean_message or len(clean_message.strip()) == 0:
        return {"success": False, "error": "Message empty after cleaning"}
    
    if len(clean_message) > MAX_WHATSAPP_LIMIT:
        logger.warning(f"Large WhatsApp response: {len(clean_message)} chars")
        clean_message = clean_message[:MAX_WHATSAPP_LIMIT] + "\n\n... (message truncated)"
    
    is_valid, normalized = validate_e164_phone(phone_number)
    if not is_valid:
        return {"success": False, "error": "Invalid phone number format"}
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning(f"DEMO MODE: Would send to {normalized}")
        return {"success": True, "mode": "demo"}
    
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    get_whatsapp_url(),
                    headers={"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": normalized.lstrip('+'),
                        "type": "text",
                        "text": {"body": clean_message[:MAX_MESSAGE_LENGTH], "preview_url": False}
                    }
                )
                
                processing_time = (time.time() - start_time) * 1000
                
                # Priority 2: Full response logging
                logger.info(f"WhatsApp API | Status={response.status_code} | Time={processing_time:.0f}ms | Attempt={attempt+1}")
                
                if response.status_code in [200, 201]:
                    data = response.json()
                    message_id = data.get("messages", [{}])[0].get("id")
                    
                    if message_id and request_id:
                        _message_logger.log_send(
                            message_id=message_id,
                            phone_number=normalized,
                            request_id=request_id,
                            message_preview=clean_message[:100],
                            status="sent",
                            status_code=response.status_code,
                            retry_count=retry_count
                        )
                    
                    result = SendResult(
                        success=True,
                        message_id=message_id,
                        status_code=response.status_code,
                        processing_time_ms=processing_time,
                        message_length=len(clean_message),
                        phone_number=normalized,
                        retry_count=retry_count
                    )
                    whatsapp_metrics.record_send(result)
                    
                    return {
                        "success": True,
                        "message_id": message_id,
                        "status_code": response.status_code,
                        "processing_time_ms": processing_time
                    }
                else:
                    is_retryable = response.status_code in [429, 500, 502, 503, 504]
                    
                    if is_retryable and attempt < MAX_RETRIES - 1:
                        retry_count += 1
                        wait_time = RETRY_DELAYS[attempt]
                        logger.warning(f"Retryable error {response.status_code}, retrying in {wait_time}s")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    # Priority 2: Full error logging
                    logger.error(f"WhatsApp API FAILED | Status={response.status_code}")
                    logger.error(f"  Response Body: {response.text[:500]}")
                    
                    if request_id:
                        _message_logger.log_send(
                            message_id=None,
                            phone_number=normalized,
                            request_id=request_id,
                            message_preview=clean_message[:100],
                            status="failed",
                            status_code=response.status_code,
                            error=response.text[:500],
                            retry_count=retry_count
                        )
                    
                    result = SendResult(
                        success=False,
                        status_code=response.status_code,
                        error=response.text[:200],
                        processing_time_ms=processing_time,
                        message_length=len(clean_message),
                        phone_number=normalized,
                        retry_count=retry_count
                    )
                    whatsapp_metrics.record_send(result)
                    
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "error": response.text[:200],
                        "processing_time_ms": processing_time
                    }
                    
        except (httpx.TimeoutException, requests.exceptions.Timeout) as e:
            if attempt < MAX_RETRIES - 1:
                retry_count += 1
                wait_time = RETRY_DELAYS[attempt]
                logger.warning(f"Timeout on attempt {attempt + 1}, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"WhatsApp API TIMEOUT after {MAX_RETRIES} attempts: {e}")
                return {"success": False, "error": f"Timeout: {str(e)}"}
                
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                retry_count += 1
                wait_time = RETRY_DELAYS[attempt]
                logger.warning(f"Exception on attempt {attempt + 1}, retrying in {wait_time}s: {e}")
                await asyncio.sleep(wait_time)
            else:
                logger.exception(f"WhatsApp API ERROR: {e}")
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}

# ==========================================================
# SYNC WRAPPER (for compatibility)
# ==========================================================

def send_text_message(phone_number: str, message: Union[str, Dict, Any]) -> Dict[str, Any]:
    """Sync wrapper for send_text_message_async"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            send_text_message_async(phone_number, message)
        )
        loop.close()
        return result
    except Exception as e:
        logger.exception(f"Sync send failed: {e}")
        return {"success": False, "error": str(e)}

# ==========================================================
# PUBLIC API FUNCTIONS
# ==========================================================

def send_text_message_with_result(phone_number: str, message: Union[str, Dict, Any]) -> SendResult:
    """Send message and return SendResult"""
    start_time = time.time()
    result = SendResult(success=False, phone_number=phone_number)
    
    try:
        api_result = send_text_message(phone_number, message)
        result.success = api_result.get("success", False)
        result.message_id = api_result.get("message_id")
        result.error = api_result.get("error")
        result.status_code = api_result.get("status_code")
        result.mode = api_result.get("mode", "api")
        result.processing_time_ms = (time.time() - start_time) * 1000
        result.log_summary()
        return result
    except Exception as e:
        result.error = str(e)
        result.processing_time_ms = (time.time() - start_time) * 1000
        logger.exception(f"Send failed: {e}")
        return result

def send_bulk_messages(messages: List[Tuple[str, str]]) -> List[SendResult]:
    """Send multiple messages with rate limiting"""
    results = []
    for i, (phone_number, message) in enumerate(messages):
        if i > 0:
            time.sleep(0.5)
        result = send_text_message_with_result(phone_number, message)
        results.append(result)
    return results

def send_structured_message(phone_number: str, message: str) -> Dict[str, Any]:
    return send_text_message(phone_number, message)

def send_test_message(phone_number: str) -> Dict[str, Any]:
    """Send a test message to verify API is working"""
    test_message = f"""
🧪 *WhatsApp API Test Message*

This is a test message to verify that your WhatsApp integration is working correctly.

✅ If you receive this, the API is working
❌ If not, check your access token and phone number ID

Timestamp: {datetime.utcnow().isoformat()}
"""
    return send_text_message(phone_number, test_message)

# ==========================================================
# WEBHOOK PARSING WITH DELIVERY TRACKING
# ==========================================================

def parse_whatsapp_message(payload: dict) -> Optional[List[Dict[str, Any]]]:
    """Parse webhook payload and track delivery status"""
    try:
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        parsed_messages = []
        
        # Priority 9: Track delivery status
        statuses = value.get("statuses", [])
        for status in statuses:
            status_id = status.get("id")
            status_type = status.get("status")
            recipient_id = status.get("recipient_id")
            
            if status_id:
                _message_logger.update_status(status_id, status_type)
                logger.info(f"Message {status_id} status: {status_type} for {recipient_id}")
        
        messages = value.get("messages", [])
        for message in messages:
            message_type = message.get("type", "unknown")
            from_phone = message.get("from")
            message_id = message.get("id")
            
            if is_duplicate_message(message_id):
                continue
            mark_message_processed(message_id)
            
            parsed = {
                "type": message_type,
                "message_id": message_id,
                "from_phone": from_phone
            }
            
            if message_type == "text":
                parsed["text"] = message.get("text", {}).get("body", "")
            else:
                parsed["text"] = f"[{message_type} message received]"
            
            parsed_messages.append(parsed)
        
        return parsed_messages if parsed_messages else None
        
    except Exception as e:
        logger.error(f"Error parsing webhook: {e}")
        return None

# ==========================================================
# COMPATIBILITY
# ==========================================================

PROCESSED_MESSAGES = deque(maxlen=10000)

def parse_whatsapp_message_legacy(payload: dict) -> Optional[List[Dict[str, Any]]]:
    """Legacy parse function - preserved for compatibility"""
    return parse_whatsapp_message(payload)

# ==========================================================
# HEALTH CHECK EXPORT
# ==========================================================

def get_whatsapp_status() -> Dict[str, Any]:
    """Get comprehensive WhatsApp service status"""
    health = check_whatsapp_health()
    metrics = whatsapp_metrics.get_stats()
    message_stats = _message_logger.get_stats()
    
    return {
        "health": health,
        "metrics": metrics,
        "message_stats": message_stats,
        "config": {
            "api_version": WHATSAPP_API_VERSION or "v25.0",
            "has_token": bool(WHATSAPP_ACCESS_TOKEN),
            "has_phone_id": bool(WHATSAPP_PHONE_NUMBER_ID)
        }
    }
