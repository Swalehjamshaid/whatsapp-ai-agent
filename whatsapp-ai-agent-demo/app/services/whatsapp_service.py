# ==========================================================
# FILE: app/services/whatsapp_service.py (ENTERPRISE v4.0)
# ==========================================================
# CRITICAL FIXES v4.0:
# - REMOVED: Fake typing indicator (was causing 400 errors)
# - ADDED: Full Meta API response logging
# - ADDED: Message delivery tracking with database
# - ADDED: Redis-based duplicate cache
# - ADDED: Real WhatsApp health verification
# - ADDED: Improved phone number validation
# - ADDED: Retry logic inside service
# - FIXED: WhatsAppMetrics now properly updated
# - ADDED: Response size monitoring
# - ADDED: Diagnostic test endpoint
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
MAX_WHATSAPP_LIMIT = 3500  # WhatsApp's actual limit
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]
MESSAGE_LOG_TTL = 86400  # 24 hours

# ==========================================================
# REDIS CACHE (Priority 4)
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
            
            # Memory fallback
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
            # Clean old entries
            now = time.time()
            expired = [k for k, v in self.memory_cache.items() if now - v > self.expiry]
            for k in expired:
                del self.memory_cache[k]
        except Exception as e:
            logger.error(f"Mark processed error: {e}")

# Global cache instance
_message_cache = RedisMessageCache()

def is_duplicate_message(message_id: str) -> bool:
    """Check if message was already processed (Redis-backed)"""
    return _message_cache.is_duplicate(message_id)

def mark_message_processed(message_id: str):
    """Mark message as processed (Redis-backed)"""
    _message_cache.mark_processed(message_id)

# ==========================================================
# IMPROVED PHONE NUMBER VALIDATION (Priority 6)
# ==========================================================

def validate_phone_number(phone_number: str) -> bool:
    """
    Validate WhatsApp phone number format
    WhatsApp requires: country code + number (10-15 digits total)
    """
    if not phone_number:
        return False
    
    cleaned = phone_number.lstrip('+')
    
    # Must be all digits
    if not cleaned.isdigit():
        logger.warning(f"Invalid phone number: contains non-digits")
        return False
    
    # WhatsApp requires 10-15 digits including country code
    if len(cleaned) < 10 or len(cleaned) > 15:
        logger.warning(f"Invalid phone number length: {len(cleaned)} (expected 10-15)")
        return False
    
    # Check for valid country code (simple check - starts with valid prefix)
    valid_prefixes = ['1', '7', '8', '9', '20', '21', '22', '23', '24', '25', '26', '27', '28', '29', '30', '31', '32', '33', '34', '35', '36', '37', '38', '39', '40', '41', '42', '43', '44', '45', '46', '47', '48', '49', '50', '51', '52', '53', '54', '55', '56', '57', '58', '59', '60', '61', '62', '63', '64', '65', '66', '67', '68', '69', '70', '71', '72', '73', '74', '75', '76', '77', '78', '79', '80', '81', '82', '83', '84', '85', '86', '87', '88', '89', '90', '91', '92', '93', '94', '95', '96', '97', '98', '99']
    
    # Check first 1-2 digits against valid prefixes
    if cleaned[:1] in valid_prefixes or cleaned[:2] in valid_prefixes:
        return True
    
    logger.warning(f"Invalid country code for phone number: {cleaned[:2]}")
    return False

def normalize_phone_number(phone_number: str) -> str:
    """Normalize phone number by removing '+' and whitespace"""
    return phone_number.lstrip('+').strip()

# ==========================================================
# WHATSAPP SESSION (Original - Preserved)
# ==========================================================

class WhatsAppSession:
    _instance = None
    _session = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._session = requests.Session()
            cls._session.headers.update({"Content-Type": "application/json"})
        return cls._instance
    
    def get_session(self) -> requests.Session:
        return self._session

_whatsapp_session = WhatsAppSession()

# ==========================================================
# URL CONSTRUCTION (Original - Preserved)
# ==========================================================

def get_whatsapp_url():
    api_version = WHATSAPP_API_VERSION or "v25.0"
    api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
    return f"{api_url}/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

def get_phone_number_health_url():
    """URL for phone number health check (Priority 5)"""
    api_version = WHATSAPP_API_VERSION or "v25.0"
    api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
    return f"{api_url}/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}"

# ==========================================================
# MESSAGE CLEANING (Original - Preserved)
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
# MESSAGE DELIVERY TRACKING (Priority 3)
# ==========================================================

class MessageDeliveryTracker:
    """Track message delivery status in database"""
    
    def __init__(self):
        self._db = None
    
    def _get_db(self):
        if self._db is None:
            from app.database import SessionLocal
            self._db = SessionLocal()
        return self._db
    
    def record_send(self, message_id: str, phone_number: str, message_preview: str):
        """Record message send attempt"""
        try:
            db = self._get_db()
            # Create table if not exists
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS whatsapp_message_log (
                    id SERIAL PRIMARY KEY,
                    message_id VARCHAR(255) UNIQUE,
                    phone_number VARCHAR(20),
                    message_preview TEXT,
                    status VARCHAR(50),
                    sent_at TIMESTAMP,
                    delivered_at TIMESTAMP,
                    read_at TIMESTAMP,
                    error TEXT
                )
            """))
            db.commit()
            
            # Insert record
            db.execute(text("""
                INSERT INTO whatsapp_message_log (message_id, phone_number, message_preview, status, sent_at)
                VALUES (:message_id, :phone_number, :message_preview, 'sent', NOW())
                ON CONFLICT (message_id) DO NOTHING
            """), {
                "message_id": message_id,
                "phone_number": phone_number,
                "message_preview": message_preview[:100]
            })
            db.commit()
        except Exception as e:
            logger.error(f"Failed to record message send: {e}")
    
    def update_status(self, message_id: str, status: str):
        """Update message delivery status from webhook"""
        try:
            db = self._get_db()
            field = "delivered_at" if status == "delivered" else "read_at" if status == "read" else None
            if field:
                db.execute(text(f"""
                    UPDATE whatsapp_message_log 
                    SET status = :status, {field} = NOW()
                    WHERE message_id = :message_id
                """), {"status": status, "message_id": message_id})
                db.commit()
        except Exception as e:
            logger.error(f"Failed to update message status: {e}")

_delivery_tracker = MessageDeliveryTracker()

# ==========================================================
# SEND RESULT TRACKING
# ==========================================================

@dataclass
class SendResult:
    """Detailed send result tracking"""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    mode: str = "api"
    processing_time_ms: float = 0
    message_length: int = 0
    phone_number: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "message_id": self.message_id,
            "error": self.error,
            "status_code": self.status_code,
            "mode": self.mode,
            "processing_time_ms": self.processing_time_ms,
            "message_length": self.message_length,
            "phone_number": self.phone_number
        }
    
    def log_summary(self):
        if self.success:
            logger.info(f"WhatsApp Send SUCCESS | Phone={self.phone_number} | ID={self.message_id} | Time={self.processing_time_ms:.0f}ms | Len={self.message_length}")
        else:
            logger.error(f"WhatsApp Send FAILED | Phone={self.phone_number} | Error={self.error} | Status={self.status_code}")

# ==========================================================
# WHATSAPP METRICS (Now properly updated)
# ==========================================================

class WhatsAppMetrics:
    """Track WhatsApp send metrics"""
    
    def __init__(self):
        self.total_sent = 0
        self.successful_sends = 0
        self.failed_sends = 0
        self.total_processing_time_ms = 0
        self.last_error = None
    
    def record_send(self, result: SendResult):
        self.total_sent += 1
        if result.success:
            self.successful_sends += 1
        else:
            self.failed_sends += 1
            self.last_error = result.error
        self.total_processing_time_ms += result.processing_time_ms
    
    def get_stats(self) -> Dict:
        return {
            "total_sent": self.total_sent,
            "successful_sends": self.successful_sends,
            "failed_sends": self.failed_sends,
            "success_rate": round(self.successful_sends / max(1, self.total_sent) * 100, 1),
            "avg_processing_time_ms": round(self.total_processing_time_ms / max(1, self.total_sent), 2),
            "last_error": self.last_error
        }

# Global metrics instance
whatsapp_metrics = WhatsAppMetrics()

# ==========================================================
# MAIN SEND FUNCTION WITH RETRY (Priority 7)
# ==========================================================

def send_text_message(phone_number: str, message: Union[str, Dict, Any]) -> Dict[str, Any]:
    """
    Send text message via WhatsApp Cloud API with retry logic
    """
    start_time = time.time()
    
    # Empty response validation
    if not message:
        logger.error("Empty message provided to send_text_message")
        return {
            "success": False, 
            "error": "Empty message",
            "mode": "validation_error"
        }
    
    clean_message = clean_message_for_whatsapp(message)
    
    # Empty message after cleaning validation
    if not clean_message or len(clean_message.strip()) == 0:
        logger.error("Message became empty after cleaning")
        return {
            "success": False,
            "error": "Message empty after cleaning",
            "mode": "validation_error"
        }
    
    # Priority 9: Response size monitoring
    if len(clean_message) > MAX_WHATSAPP_LIMIT:
        logger.warning(f"Large WhatsApp response: {len(clean_message)} chars (limit: {MAX_WHATSAPP_LIMIT})")
        clean_message = clean_message[:MAX_WHATSAPP_LIMIT] + "\n\n... (message truncated)"
    
    # Phone validation
    if not validate_phone_number(phone_number):
        return {"success": False, "error": "Invalid phone number format"}
    
    normalized_phone = normalize_phone_number(phone_number)
    message_length = len(clean_message)
    
    logger.info(f"Sending WhatsApp message to: {normalized_phone}")
    logger.info(f"   Message length: {message_length} chars")
    logger.info(f"   Message preview: {clean_message[:100]}...")
    
    # Demo mode
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        processing_time = (time.time() - start_time) * 1000
        logger.warning(f"DEMO MODE: Would send to {normalized_phone}: {clean_message[:100]}...")
        
        result = SendResult(
            success=True,
            mode="demo",
            processing_time_ms=processing_time,
            message_length=message_length,
            phone_number=normalized_phone
        )
        whatsapp_metrics.record_send(result)
        
        return {
            "success": True, 
            "mode": "demo", 
            "phone_number": normalized_phone, 
            "message": clean_message[:MAX_MESSAGE_LENGTH],
            "message_length": message_length,
            "processing_time_ms": processing_time
        }
    
    # Priority 7: Retry logic
    for attempt in range(MAX_RETRIES):
        try:
            headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
            payload = {
                "messaging_product": "whatsapp",
                "to": normalized_phone,
                "type": "text",
                "text": {"body": clean_message[:MAX_MESSAGE_LENGTH], "preview_url": False}
            }
            
            session = _whatsapp_session.get_session()
            response = session.post(get_whatsapp_url(), headers=headers, json=payload, timeout=60)
            
            processing_time = (time.time() - start_time) * 1000
            
            # Priority 2: Full Meta API response logging
            logger.info("=" * 80)
            logger.info(f"WhatsApp API Request Details:")
            logger.info(f"  URL: {get_whatsapp_url()}")
            logger.info(f"  Phone: {normalized_phone}")
            logger.info(f"  Message Length: {message_length}")
            logger.info(f"=" * 80)
            logger.info(f"WhatsApp API Response:")
            logger.info(f"  Status: {response.status_code}")
            logger.info(f"  Headers: {dict(response.headers)}")
            logger.info(f"  Body: {response.text[:500]}")
            logger.info("=" * 80)
            
            if response.status_code in [200, 201]:
                response_data = response.json()
                message_id = response_data.get("messages", [{}])[0].get("id")
                
                # Priority 3: Track delivery
                if message_id:
                    _delivery_tracker.record_send(message_id, normalized_phone, clean_message[:100])
                
                logger.info(f"WhatsApp API SUCCESS | Status={response.status_code} | ID={message_id} | Time={processing_time:.0f}ms")
                
                result = SendResult(
                    success=True,
                    message_id=message_id,
                    status_code=response.status_code,
                    mode="api",
                    processing_time_ms=processing_time,
                    message_length=message_length,
                    phone_number=normalized_phone
                )
                whatsapp_metrics.record_send(result)
                
                return {
                    "success": True,
                    "message_id": message_id,
                    "status_code": response.status_code,
                    "mode": "api",
                    "processing_time_ms": processing_time,
                    "message_length": message_length,
                    "phone_number": normalized_phone
                }
            else:
                # Check if retry should happen
                is_retryable = response.status_code in [429, 500, 502, 503, 504]
                
                if is_retryable and attempt < MAX_RETRIES - 1:
                    wait_time = RETRY_DELAYS[attempt]
                    logger.warning(f"Retryable error {response.status_code}, attempt {attempt + 1}, retrying in {wait_time}s")
                    time.sleep(wait_time)
                    continue
                
                logger.error(f"WhatsApp API FAILED | Status={response.status_code} | Time={processing_time:.0f}ms")
                logger.error(f"   Response: {response.text[:500]}")
                
                result = SendResult(
                    success=False,
                    status_code=response.status_code,
                    error=response.text[:200],
                    mode="api",
                    processing_time_ms=processing_time,
                    message_length=message_length,
                    phone_number=normalized_phone
                )
                whatsapp_metrics.record_send(result)
                
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error": response.text[:200],
                    "mode": "api",
                    "processing_time_ms": processing_time,
                    "message_length": message_length,
                    "phone_number": normalized_phone
                }
            
        except requests.exceptions.Timeout as e:
            processing_time = (time.time() - start_time) * 1000
            
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAYS[attempt]
                logger.warning(f"Timeout on attempt {attempt + 1}, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
                continue
            
            logger.error(f"WhatsApp API TIMEOUT | Time={processing_time:.0f}ms | Error={e}")
            
            result = SendResult(
                success=False,
                error=f"Timeout: {str(e)}",
                mode="api",
                processing_time_ms=processing_time,
                message_length=message_length,
                phone_number=normalized_phone
            )
            whatsapp_metrics.record_send(result)
            
            return {
                "success": False,
                "error": f"Timeout: {str(e)}",
                "mode": "api",
                "processing_time_ms": processing_time,
                "message_length": message_length,
                "phone_number": normalized_phone
            }
            
        except Exception as e:
            processing_time = (time.time() - start_time) * 1000
            
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAYS[attempt]
                logger.warning(f"Exception on attempt {attempt + 1}, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
                continue
            
            logger.exception(f"WhatsApp API ERROR | Time={processing_time:.0f}ms | Error={e}")
            
            result = SendResult(
                success=False,
                error=str(e),
                mode="api",
                processing_time_ms=processing_time,
                message_length=message_length,
                phone_number=normalized_phone
            )
            whatsapp_metrics.record_send(result)
            
            return {
                "success": False,
                "error": str(e),
                "mode": "api",
                "processing_time_ms": processing_time,
                "message_length": message_length,
                "phone_number": normalized_phone
            }
    
    # Should never reach here
    return {"success": False, "error": "Max retries exceeded"}

# ==========================================================
# SEND WITH RESULT OBJECT
# ==========================================================

def send_text_message_with_result(phone_number: str, message: Union[str, Dict, Any]) -> SendResult:
    """Send message and return detailed SendResult object"""
    start_time = time.time()
    
    result = SendResult(
        success=False,
        phone_number=phone_number,
        message_length=len(str(message)) if message else 0
    )
    
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

# ==========================================================
# BULK SEND WITH RATE LIMITING
# ==========================================================

def send_bulk_messages(messages: List[Tuple[str, str]]) -> List[SendResult]:
    """Send multiple messages with rate limiting"""
    results = []
    for i, (phone_number, message) in enumerate(messages):
        if i > 0:
            time.sleep(0.5)
        result = send_text_message_with_result(phone_number, message)
        results.append(result)
    return results

# ==========================================================
# PARSE WHATSAPP WEBHOOK (Preserved)
# ==========================================================

def parse_whatsapp_message(payload: dict) -> Optional[List[Dict[str, Any]]]:
    try:
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        parsed_messages = []
        
        # Handle message status updates (delivered, read)
        statuses = value.get("statuses", [])
        for status in statuses:
            status_id = status.get("id")
            status_type = status.get("status")
            recipient_id = status.get("recipient_id")
            
            if status_id:
                # Update delivery tracking
                _delivery_tracker.update_status(status_id, status_type)
                logger.info(f"Message {status_id} status: {status_type} for {recipient_id}")
        
        messages = value.get("messages", [])
        for message in messages:
            message_type = message.get("type", "unknown")
            from_phone = message.get("from")
            message_id = message.get("id")
            
            if is_duplicate_message(message_id):
                continue
            mark_message_processed(message_id)
            
            parsed = {"type": message_type, "message_id": message_id, "from_phone": from_phone}
            
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
# SEND STRUCTURED MESSAGE (Alias)
# ==========================================================

def send_structured_message(phone_number: str, message: str) -> Dict[str, Any]:
    return send_text_message(phone_number, message)

# ==========================================================
# REMOVED: send_typing_indicator() - Was causing 400 errors
# ==========================================================
# The typing indicator functionality has been removed
# because it was using an invalid API call that returned 400 errors.
# If needed, implement the correct typing indicator using:
# POST /vXX.X/phone_number_id/messages with type "typing"

# ==========================================================
# REAL WHATSAPP HEALTH VERIFICATION (Priority 5)
# ==========================================================

def check_whatsapp_health() -> Dict[str, Any]:
    """Verify WhatsApp API health with actual API call"""
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {
            "available": False,
            "mode": "demo",
            "error": "Missing credentials",
            "api_version": WHATSAPP_API_VERSION or "v25.0",
            "phone_number_id": None,
            "metrics": whatsapp_metrics.get_stats(),
            "cache_size": len(PROCESSED_MESSAGES) if 'PROCESSED_MESSAGES' in dir() else 0
        }
    
    try:
        # Make actual API call to verify phone number
        url = get_phone_number_health_url()
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return {
                "available": True,
                "mode": "production",
                "verified": True,
                "phone_number": data.get("display_phone_number"),
                "quality_rating": data.get("quality_rating"),
                "api_version": WHATSAPP_API_VERSION or "v25.0",
                "phone_number_id": WHATSAPP_PHONE_NUMBER_ID[:10] + "...",
                "metrics": whatsapp_metrics.get_stats(),
                "cache_size": len(PROCESSED_MESSAGES) if 'PROCESSED_MESSAGES' in dir() else 0
            }
        else:
            return {
                "available": False,
                "mode": "production",
                "verified": False,
                "error": f"API returned {response.status_code}",
                "api_version": WHATSAPP_API_VERSION or "v25.0",
                "phone_number_id": WHATSAPP_PHONE_NUMBER_ID[:10] + "...",
                "metrics": whatsapp_metrics.get_stats(),
                "cache_size": len(PROCESSED_MESSAGES) if 'PROCESSED_MESSAGES' in dir() else 0
            }
            
    except Exception as e:
        return {
            "available": False,
            "mode": "production",
            "verified": False,
            "error": str(e),
            "api_version": WHATSAPP_API_VERSION or "v25.0",
            "phone_number_id": WHATSAPP_PHONE_NUMBER_ID[:10] + "...",
            "metrics": whatsapp_metrics.get_stats(),
            "cache_size": len(PROCESSED_MESSAGES) if 'PROCESSED_MESSAGES' in dir() else 0
        }

# ==========================================================
# DIAGNOSTIC TEST ENDPOINT (Priority 10)
# ==========================================================

def send_test_message(phone_number: str) -> Dict[str, Any]:
    """
    Send a test message to verify WhatsApp API is working
    Use this to diagnose issues
    """
    test_message = """
🧪 *WhatsApp API Test Message*

This is a test message to verify that your WhatsApp integration is working correctly.

✅ If you receive this, the API is working
❌ If not, check your access token and phone number ID

Timestamp: {timestamp}

*Request ID:* {request_id}
""".format(
        timestamp=datetime.utcnow().isoformat(),
        request_id=hash(phone_number) % 10000
    )
    
    return send_text_message(phone_number, test_message)

# ==========================================================
# PROCESSED MESSAGES CACHE (For backward compatibility)
# ==========================================================

PROCESSED_MESSAGES = deque(maxlen=10000)

# ==========================================================
# FALLBACK PARSE FUNCTION (Original preserved)
# ==========================================================

def parse_whatsapp_message_legacy(payload: dict) -> Optional[List[Dict[str, Any]]]:
    """Legacy parse function - preserved for compatibility"""
    return parse_whatsapp_message(payload)
