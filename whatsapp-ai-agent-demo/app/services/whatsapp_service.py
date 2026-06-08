# ==========================================================
# FILE: app/services/whatsapp_service.py (ENTERPRISE v6.0)
# ==========================================================
# FULLY ALIGNED WITH ARCHITECTURE:
# WhatsApp User → webhook.py → whatsapp_service.py → ai_query_service.py → ...
# - Complete error handling
# - Full Meta API logging
# - Retry logic with exponential backoff
# - Credential validation
# - Phone number E.164 validation
# - All original attributes preserved
# ==========================================================

import requests
import logging
import json
import time
import asyncio
from typing import Optional, Dict, Any, Union, Tuple
from datetime import datetime
from urllib3.util.retry import Retry
from loguru import logger

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_API_VERSION,
    WHATSAPP_API_URL
)

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = 4000
MAX_WHATSAPP_LIMIT = 3500
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]

# ==========================================================
# PHONE NUMBER VALIDATION (E.164 Format)
# ==========================================================

def validate_phone_number(phone_number: str) -> bool:
    """
    Validate WhatsApp phone number format
    Returns True if valid, False otherwise
    """
    if not phone_number:
        return False
    
    cleaned = phone_number.lstrip('+')
    
    if not cleaned.isdigit():
        logger.warning(f"Invalid phone number: contains non-digits")
        return False
    
    # WhatsApp requires 10-15 digits including country code
    if len(cleaned) < 10 or len(cleaned) > 15:
        logger.warning(f"Invalid phone number length: {len(cleaned)} (expected 10-15)")
        return False
    
    return True

def normalize_phone_number(phone_number: str) -> str:
    """Normalize phone number by removing '+' and whitespace"""
    return phone_number.lstrip('+').strip()

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

# ==========================================================
# URL CONSTRUCTION
# ==========================================================

def get_whatsapp_url() -> str:
    """Construct WhatsApp API URL"""
    api_version = WHATSAPP_API_VERSION or "v25.0"
    api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
    return f"{api_url}/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

def get_phone_number_health_url() -> str:
    """URL for phone number health check"""
    api_version = WHATSAPP_API_VERSION or "v25.0"
    api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
    return f"{api_url}/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}"

# ==========================================================
# CONFIGURATION VALIDATION
# ==========================================================

def validate_whatsapp_configuration() -> Dict[str, Any]:
    """Validate WhatsApp configuration at startup"""
    errors = []
    warnings = []
    
    if not WHATSAPP_ACCESS_TOKEN:
        errors.append("WHATSAPP_ACCESS_TOKEN is missing")
    elif len(WHATSAPP_ACCESS_TOKEN) < 50:
        warnings.append("WHATSAPP_ACCESS_TOKEN seems too short")
    
    if not WHATSAPP_PHONE_NUMBER_ID:
        errors.append("WHATSAPP_PHONE_NUMBER_ID is missing")
    
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
# MAIN SEND FUNCTION
# ==========================================================

def send_text_message(phone_number: str, message: Union[str, Dict, Any]) -> Dict[str, Any]:
    """
    Send text message via WhatsApp Cloud API
    This is the main entry point called by webhook.py
    """
    
    start_time = time.time()
    
    # ==========================================================
    # DEBUG: Log configuration (helps diagnose issues)
    # ==========================================================
    logger.info("=" * 60)
    logger.info("🔍 WHATSAPP SEND DEBUG:")
    logger.info(f"   Phone: {phone_number}")
    logger.info(f"   Message length: {len(str(message)) if message else 0}")
    logger.info(f"   ACCESS_TOKEN: {'✓ SET' if WHATSAPP_ACCESS_TOKEN else '✗ MISSING'}")
    logger.info(f"   PHONE_ID: {'✓ SET' if WHATSAPP_PHONE_NUMBER_ID else '✗ MISSING'}")
    logger.info(f"   API_VERSION: {WHATSAPP_API_VERSION or 'default'}")
    logger.info("=" * 60)
    
    # ==========================================================
    # CHECK CREDENTIALS FIRST
    # ==========================================================
    if not WHATSAPP_ACCESS_TOKEN:
        error_msg = "WHATSAPP_ACCESS_TOKEN environment variable is missing"
        logger.error(f"❌ {error_msg}")
        return {"success": False, "error": error_msg, "status_code": 401}
    
    if not WHATSAPP_PHONE_NUMBER_ID:
        error_msg = "WHATSAPP_PHONE_NUMBER_ID environment variable is missing"
        logger.error(f"❌ {error_msg}")
        return {"success": False, "error": error_msg, "status_code": 400}
    
    # ==========================================================
    # CLEAN AND VALIDATE MESSAGE
    # ==========================================================
    if isinstance(message, dict):
        clean_message = message.get("response") or message.get("formatted_message") or message.get("message") or str(message)
    elif isinstance(message, str):
        clean_message = message
    else:
        clean_message = str(message)
    
    # Handle JSON responses
    if clean_message.strip().startswith('{') or clean_message.strip().startswith('['):
        try:
            parsed = json.loads(clean_message)
            if isinstance(parsed, dict):
                clean_message = parsed.get("response") or parsed.get("formatted_message") or parsed.get("message") or clean_message
        except:
            pass
    
    if not clean_message or len(clean_message.strip()) == 0:
        logger.error("Empty message after cleaning")
        return {"success": False, "error": "Empty message"}
    
    # Check message size
    if len(clean_message) > MAX_WHATSAPP_LIMIT:
        logger.warning(f"Large WhatsApp response: {len(clean_message)} chars (limit: {MAX_WHATSAPP_LIMIT})")
        clean_message = clean_message[:MAX_WHATSAPP_LIMIT] + "\n\n... (message truncated)"
    
    # ==========================================================
    # VALIDATE PHONE NUMBER
    # ==========================================================
    if not validate_phone_number(phone_number):
        logger.error(f"Invalid phone number format: {phone_number}")
        return {"success": False, "error": "Invalid phone number format"}
    
    normalized_phone = normalize_phone_number(phone_number)
    message_length = len(clean_message)
    
    logger.info(f"📤 Sending WhatsApp message to: {normalized_phone}")
    logger.info(f"   Message length: {message_length} chars")
    logger.info(f"   Message preview: {clean_message[:100]}...")
    
    # ==========================================================
    # MAKE API CALL WITH RETRY LOGIC
    # ==========================================================
    for attempt in range(MAX_RETRIES):
        try:
            url = get_whatsapp_url()
            headers = {
                "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "messaging_product": "whatsapp",
                "to": normalized_phone,
                "type": "text",
                "text": {"body": clean_message[:MAX_MESSAGE_LENGTH], "preview_url": False}
            }
            
            logger.info(f"📤 Making WhatsApp API call (attempt {attempt + 1}/{MAX_RETRIES})")
            logger.info(f"   URL: {url}")
            logger.info(f"   Phone: {normalized_phone}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            processing_time = (time.time() - start_time) * 1000
            
            # Log full response for debugging
            logger.info(f"WhatsApp API Response Status: {response.status_code}")
            logger.info(f"WhatsApp API Response Body: {response.text[:500]}")
            
            if response.status_code in [200, 201]:
                response_data = response.json()
                message_id = response_data.get("messages", [{}])[0].get("id")
                
                logger.success(f"✅ WhatsApp message sent! ID: {message_id} | Time: {processing_time:.0f}ms")
                
                return {
                    "success": True,
                    "message_id": message_id,
                    "status_code": response.status_code,
                    "processing_time_ms": processing_time,
                    "message_length": message_length,
                    "phone_number": normalized_phone,
                    "mode": "api"
                }
            else:
                # Check if retry should happen
                is_retryable = response.status_code in [429, 500, 502, 503, 504]
                
                if is_retryable and attempt < MAX_RETRIES - 1:
                    wait_time = RETRY_DELAYS[attempt]
                    logger.warning(f"Retryable error {response.status_code}, attempt {attempt + 1}, retrying in {wait_time}s")
                    time.sleep(wait_time)
                    continue
                
                logger.error(f"❌ WhatsApp API failed: {response.status_code}")
                logger.error(f"   Response: {response.text[:500]}")
                
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error": response.text[:500],
                    "processing_time_ms": processing_time,
                    "message_length": message_length,
                    "phone_number": normalized_phone,
                    "mode": "api"
                }
                
        except requests.exceptions.Timeout as e:
            processing_time = (time.time() - start_time) * 1000
            
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAYS[attempt]
                logger.warning(f"Timeout on attempt {attempt + 1}, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
                continue
            
            logger.error(f"❌ WhatsApp API TIMEOUT | Time={processing_time:.0f}ms | Error={e}")
            return {
                "success": False,
                "error": f"Timeout: {str(e)}",
                "processing_time_ms": processing_time,
                "message_length": message_length,
                "phone_number": normalized_phone,
                "mode": "api"
            }
            
        except Exception as e:
            processing_time = (time.time() - start_time) * 1000
            
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAYS[attempt]
                logger.warning(f"Exception on attempt {attempt + 1}, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
                continue
            
            logger.exception(f"❌ WhatsApp API ERROR | Time={processing_time:.0f}ms | Error={e}")
            return {
                "success": False,
                "error": str(e),
                "processing_time_ms": processing_time,
                "message_length": message_length,
                "phone_number": normalized_phone,
                "mode": "api"
            }
    
    return {"success": False, "error": "Max retries exceeded"}

# ==========================================================
# HEALTH CHECK FUNCTION
# ==========================================================

def check_whatsapp_health() -> Dict[str, Any]:
    """Verify WhatsApp API health with actual API call"""
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {
            "available": False,
            "mode": "demo",
            "error": "Missing credentials",
            "api_version": WHATSAPP_API_VERSION or "v25.0",
            "phone_number_id": None
        }
    
    try:
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
                "status": data.get("status"),
                "api_version": WHATSAPP_API_VERSION or "v25.0",
                "phone_number_id": WHATSAPP_PHONE_NUMBER_ID[:10] + "..."
            }
        elif response.status_code == 401:
            return {
                "available": False,
                "mode": "production",
                "verified": False,
                "error": "Invalid access token - 401 Unauthorized",
                "status_code": 401,
                "api_version": WHATSAPP_API_VERSION or "v25.0"
            }
        else:
            return {
                "available": False,
                "mode": "production",
                "verified": False,
                "error": f"API returned {response.status_code}",
                "response": response.text[:200],
                "status_code": response.status_code,
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
# DIAGNOSTIC TEST FUNCTION
# ==========================================================

def send_test_message(phone_number: str) -> Dict[str, Any]:
    """
    Send a test message to verify WhatsApp API is working
    Use this to diagnose issues
    """
    test_message = f"""
🧪 *WhatsApp API Test Message*

This is a test message to verify that your WhatsApp integration is working correctly.

✅ If you receive this, the API is working
❌ If not, check your access token and phone number ID

Timestamp: {datetime.utcnow().isoformat()}
"""
    return send_text_message(phone_number, test_message)

# ==========================================================
# BACKWARD COMPATIBILITY FUNCTIONS
# ==========================================================

def send_structured_message(phone_number: str, message: str) -> Dict[str, Any]:
    """Alias for send_text_message - maintains backward compatibility"""
    return send_text_message(phone_number, message)

# ==========================================================
# PROCESSED MESSAGES CACHE (For webhook compatibility)
# ==========================================================

from collections import deque
PROCESSED_MESSAGES = deque(maxlen=10000)

def is_duplicate_message(message_id: str) -> bool:
    """Check if message was already processed"""
    if not message_id:
        return False
    for stored_id, timestamp in PROCESSED_MESSAGES:
        if stored_id == message_id:
            return True
    return False

def mark_message_processed(message_id: str):
    """Mark message as processed"""
    if message_id:
        PROCESSED_MESSAGES.append((message_id, datetime.utcnow()))

# ==========================================================
# WEBHOOK PARSING FUNCTION
# ==========================================================

def parse_whatsapp_message(payload: dict) -> Optional[list]:
    """Parse WhatsApp webhook payload"""
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
# METRICS COLLECTION
# ==========================================================

class WhatsAppMetrics:
    """Track WhatsApp send metrics"""
    
    def __init__(self):
        self.total_sent = 0
        self.successful_sends = 0
        self.failed_sends = 0
        self.total_processing_time_ms = 0
        self.last_error = None
    
    def record_send(self, result: Dict[str, Any]):
        self.total_sent += 1
        if result.get("success"):
            self.successful_sends += 1
        else:
            self.failed_sends += 1
            self.last_error = result.get("error")
        self.total_processing_time_ms += result.get("processing_time_ms", 0)
    
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
# STARTUP VALIDATION
# ==========================================================

# Run validation on module load
config_valid = validate_whatsapp_configuration()
logger.info(f"WhatsApp Service Ready | Config Valid: {config_valid['valid']}")
