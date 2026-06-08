# ==========================================================
# FILE: app/services/whatsapp_service.py (ENTERPRISE v3.0)
# ==========================================================
# ENHANCED WITH:
# - Send result logging with detailed status
# - Empty response validation
# - Processing time tracking
# - Enhanced error tracking and metrics
# - All original attributes preserved
# ==========================================================

import requests
import logging
import json
import re
import time
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass, field

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_API_VERSION,
    WHATSAPP_API_URL
)

logger = logging.getLogger(__name__)

# ==========================================================
# PROCESSED MESSAGES CACHE (Original - Preserved)
# ==========================================================

PROCESSED_MESSAGES = deque(maxlen=10000)

def is_duplicate_message(message_id: str) -> bool:
    if not message_id:
        return False
    for stored_id, timestamp in PROCESSED_MESSAGES:
        if stored_id == message_id:
            return True
    return False

def mark_message_processed(message_id: str):
    if message_id:
        PROCESSED_MESSAGES.append((message_id, datetime.utcnow()))

# ==========================================================
# PHONE NUMBER VALIDATION (Original - Preserved)
# ==========================================================

def validate_phone_number(phone_number: str) -> bool:
    if not phone_number:
        return False
    cleaned = phone_number.lstrip('+')
    if not cleaned.isdigit():
        return False
    if len(cleaned) < 8 or len(cleaned) > 15:
        return False
    return True

def normalize_phone_number(phone_number: str) -> str:
    return phone_number.lstrip('+')

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

MAX_MESSAGE_LENGTH = 4000

# ==========================================================
# NEW: Send Result Tracking (Improvement)
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
# ENHANCED: MAIN SEND FUNCTION (Improvement)
# ==========================================================

def send_text_message(phone_number: str, message: Union[str, Dict, Any]) -> Dict[str, Any]:
    """
    Send text message via WhatsApp Cloud API with enhanced logging
    All original functionality preserved
    """
    start_time = time.time()
    
    # NEW: Empty response validation
    if not message:
        logger.error("Empty message provided to send_text_message")
        return {
            "success": False, 
            "error": "Empty message",
            "mode": "validation_error"
        }
    
    clean_message = clean_message_for_whatsapp(message)
    
    # NEW: Empty message after cleaning validation
    if not clean_message or len(clean_message.strip()) == 0:
        logger.error("Message became empty after cleaning")
        return {
            "success": False,
            "error": "Message empty after cleaning",
            "mode": "validation_error"
        }
    
    # Original validation
    if not validate_phone_number(phone_number):
        return {"success": False, "error": "Invalid phone number format"}
    
    normalized_phone = normalize_phone_number(phone_number)
    message_length = len(clean_message)
    
    # NEW: Enhanced logging with message length
    logger.info(f"Sending WhatsApp message to: {normalized_phone}")
    logger.info(f"   Message length: {message_length} chars")
    logger.info(f"   Message preview: {clean_message[:100]}...")
    
    # Original demo mode
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        processing_time = (time.time() - start_time) * 1000
        logger.warning(f"DEMO MODE: Would send to {normalized_phone}: {clean_message[:100]}...")
        return {
            "success": True, 
            "mode": "demo", 
            "phone_number": normalized_phone, 
            "message": clean_message[:MAX_MESSAGE_LENGTH],
            "message_length": message_length,
            "processing_time_ms": processing_time
        }
    
    # Original API call with enhanced logging
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
        
        # Enhanced response logging
        if response.status_code in [200, 201]:
            response_data = response.json()
            message_id = response_data.get("messages", [{}])[0].get("id")
            
            logger.info(f"WhatsApp API SUCCESS | Status={response.status_code} | ID={message_id} | Time={processing_time:.0f}ms")
            
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
            # Enhanced error logging
            logger.error(f"WhatsApp API FAILED | Status={response.status_code} | Time={processing_time:.0f}ms")
            logger.error(f"   Response: {response.text[:200]}")
            
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
        logger.error(f"WhatsApp API TIMEOUT | Time={processing_time:.0f}ms | Error={e}")
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
        logger.exception(f"WhatsApp API ERROR | Time={processing_time:.0f}ms | Error={e}")
        return {
            "success": False,
            "error": str(e),
            "mode": "api",
            "processing_time_ms": processing_time,
            "message_length": message_length,
            "phone_number": normalized_phone
        }

# ==========================================================
# NEW: Enhanced send with result object
# ==========================================================

def send_text_message_with_result(phone_number: str, message: Union[str, Dict, Any]) -> SendResult:
    """
    Send message and return detailed SendResult object
    """
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
# NEW: Bulk send with rate limiting
# ==========================================================

def send_bulk_messages(messages: List[Tuple[str, str]]) -> List[SendResult]:
    """
    Send multiple messages with rate limiting
    """
    results = []
    for i, (phone_number, message) in enumerate(messages):
        if i > 0:
            time.sleep(0.5)  # Rate limiting between messages
        result = send_text_message_with_result(phone_number, message)
        results.append(result)
    return results

# ==========================================================
# ORIGINAL: Parse WhatsApp Message (Preserved)
# ==========================================================

def parse_whatsapp_message(payload: dict) -> Optional[List[Dict[str, Any]]]:
    try:
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        parsed_messages = []
        
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
# ORIGINAL: Send Structured Message (Preserved)
# ==========================================================

def send_structured_message(phone_number: str, message: str) -> Dict[str, Any]:
    return send_text_message(phone_number, message)

# ==========================================================
# NEW: Send Typing Indicator
# ==========================================================

def send_typing_indicator(phone_number: str) -> Dict[str, Any]:
    """
    Send typing indicator to WhatsApp
    """
    if not validate_phone_number(phone_number):
        return {"success": False, "error": "Invalid phone number"}
    
    normalized_phone = normalize_phone_number(phone_number)
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.info(f"DEMO: Typing indicator to {normalized_phone}")
        return {"success": True, "mode": "demo"}
    
    try:
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        payload = {
            "messaging_product": "whatsapp",
            "to": normalized_phone,
            "type": "reaction",
            "reaction": {"message_id": "typing_indicator"}
        }
        
        session = _whatsapp_session.get_session()
        response = session.post(get_whatsapp_url(), headers=headers, json=payload, timeout=10)
        
        if response.status_code in [200, 201]:
            logger.info(f"Typing indicator sent to {normalized_phone}")
            return {"success": True}
        else:
            logger.warning(f"Typing indicator failed: {response.status_code}")
            return {"success": False, "status_code": response.status_code}
            
    except Exception as e:
        logger.error(f"Typing indicator error: {e}")
        return {"success": False, "error": str(e)}

# ==========================================================
# NEW: Metrics Collection
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
# NEW: Health Check Function
# ==========================================================

def check_whatsapp_health() -> Dict[str, Any]:
    """Check WhatsApp service health"""
    return {
        "available": bool(WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID),
        "mode": "production" if (WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID) else "demo",
        "api_version": WHATSAPP_API_VERSION or "v25.0",
        "phone_number_id": WHATSAPP_PHONE_NUMBER_ID[:10] + "..." if WHATSAPP_PHONE_NUMBER_ID else None,
        "metrics": whatsapp_metrics.get_stats(),
        "cache_size": len(PROCESSED_MESSAGES)
    }
