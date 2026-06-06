# ==========================================================
# FILE: app/services/whatsapp_service.py (ENTERPRISE v2.0)
# PROJECT: AI WhatsApp Customer Service Agent
# FIXED: Duplicate protection, JSON safety, status separation,
#        phone validation, multi-message processing, retry logic
# ==========================================================

import requests
import logging
import json
import re
import time
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, timedelta
from collections import deque

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_API_VERSION,
    WHATSAPP_API_URL
)

# ==========================================================
# LOGGING SETUP (FIX #7 - Removed print statements)
# ==========================================================

logger = logging.getLogger(__name__)

# ==========================================================
# FIX #1: DUPLICATE MESSAGE PROTECTION
# ==========================================================

PROCESSED_MESSAGES = deque(maxlen=10000)  # Store last 10,000 message IDs
MESSAGE_EXPIRY_SECONDS = 3600  # 1 hour

def is_duplicate_message(message_id: str) -> bool:
    """Check if message has been processed recently"""
    if not message_id:
        return False
    
    # Clean old entries (simple approach - just check if exists)
    for stored_id, timestamp in PROCESSED_MESSAGES:
        if stored_id == message_id:
            return True
    
    return False

def mark_message_processed(message_id: str):
    """Mark message as processed to prevent duplicates"""
    if message_id:
        PROCESSED_MESSAGES.append((message_id, datetime.utcnow()))

# ==========================================================
# FIX #4: PHONE NUMBER VALIDATION
# ==========================================================

def validate_phone_number(phone_number: str) -> bool:
    """
    Validate WhatsApp phone number format
    Accepts: 923001234567, +923001234567
    """
    if not phone_number:
        return False
    
    # Remove leading + if present
    cleaned = phone_number.lstrip('+')
    
    # Must be all digits
    if not cleaned.isdigit():
        logger.warning(f"Invalid phone number (not all digits): {phone_number}")
        return False
    
    # Pakistan numbers: 92 followed by 10 digits = 12 digits total
    # International format: minimum 8, maximum 15 digits (E.164 standard)
    if len(cleaned) < 8 or len(cleaned) > 15:
        logger.warning(f"Invalid phone number length ({len(cleaned)}): {phone_number}")
        return False
    
    return True

def normalize_phone_number(phone_number: str) -> str:
    """Normalize phone number to E.164 format (no leading +)"""
    return phone_number.lstrip('+')

# ==========================================================
# FIX #8: REUSABLE HTTP SESSION
# ==========================================================

class WhatsAppSession:
    """Singleton HTTP session for WhatsApp API calls"""
    
    _instance = None
    _session = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._session = requests.Session()
            cls._session.headers.update({
                "Content-Type": "application/json"
            })
        return cls._instance
    
    def get_session(self) -> requests.Session:
        return self._session

_whatsapp_session = WhatsAppSession()

# ==========================================================
# WHATSAPP API URL
# ==========================================================

def get_whatsapp_url():
    """Get WhatsApp API URL with proper version"""
    api_version = WHATSAPP_API_VERSION or "v25.0"
    api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
    
    return (
        f"{api_url}/{api_version}/"
        f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )

# ==========================================================
# FIX #5: JSON RESPONSE CLEANUP
# ==========================================================

def clean_message_for_whatsapp(message: Union[str, Dict, Any]) -> str:
    """
    Ensure message is clean text, not JSON or dict
    FIX #5: Prevents {"response":"...", "confidence":25} from reaching users
    """
    if isinstance(message, dict):
        # Extract response field first, then message, then fallback
        cleaned = message.get("response") or message.get("formatted_message") or message.get("message") or str(message)
    elif isinstance(message, str):
        cleaned = message
    else:
        cleaned = str(message)
    
    # If it looks like JSON, try to parse it
    if cleaned.strip().startswith('{') or cleaned.strip().startswith('['):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                cleaned = parsed.get("response") or parsed.get("formatted_message") or parsed.get("message") or cleaned
            elif isinstance(parsed, list):
                cleaned = "\n".join(str(item) for item in parsed[:5])
        except (json.JSONDecodeError, TypeError):
            pass
    
    return cleaned

# ==========================================================
# FIX #6: MESSAGE LENGTH PROTECTION
# ==========================================================

MAX_MESSAGE_LENGTH = 4000  # WhatsApp limit is 4096, using 4000 for safety

def truncate_message(message: str) -> str:
    """Truncate message to WhatsApp limit"""
    if len(message) > MAX_MESSAGE_LENGTH:
        logger.warning(f"Message truncated from {len(message)} to {MAX_MESSAGE_LENGTH} chars")
        return message[:MAX_MESSAGE_LENGTH - 100] + "\n\n... (message truncated due to length limit)"
    return message

def split_long_message(message: str, max_length: int = MAX_MESSAGE_LENGTH) -> List[str]:
    """Split long message into multiple chunks"""
    if len(message) <= max_length:
        return [message]
    
    chunks = []
    lines = message.split('\n')
    current_chunk = ""
    
    for line in lines:
        if len(current_chunk) + len(line) + 1 <= max_length:
            current_chunk += line + '\n'
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = line + '\n'
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks

# ==========================================================
# FIX #9: RETRY LOGIC
# ==========================================================

def send_with_retry(
    url: str,
    headers: Dict,
    payload: Dict,
    max_retries: int = 3,
    retry_delays: List[int] = [1, 3, 5]
) -> requests.Response:
    """
    Send request with retry logic for transient errors
    Retry on: 429, 500, 502, 503, 504
    """
    retry_statuses = {429, 500, 502, 503, 504}
    last_response = None
    
    for attempt in range(max_retries + 1):
        try:
            session = _whatsapp_session.get_session()
            response = session.post(url, headers=headers, json=payload, timeout=60)
            
            if response.status_code not in retry_statuses:
                return response
            
            last_response = response
            if attempt < max_retries:
                delay = retry_delays[attempt] if attempt < len(retry_delays) else 5
                logger.warning(f"Retry {attempt + 1}/{max_retries} for status {response.status_code}, waiting {delay}s")
                time.sleep(delay)
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries:
                time.sleep(retry_delays[attempt] if attempt < len(retry_delays) else 5)
            else:
                raise
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error on attempt {attempt + 1}: {e}")
            if attempt < max_retries:
                time.sleep(retry_delays[attempt] if attempt < len(retry_delays) else 5)
            else:
                raise
    
    return last_response

# ==========================================================
# SEND TEXT MESSAGE (ENHANCED)
# ==========================================================

def send_text_message(phone_number: str, message: Union[str, Dict, Any]) -> Dict[str, Any]:
    """
    Send a text message via WhatsApp Cloud API
    
    Args:
        phone_number: Recipient's phone number with country code
        message: Text message to send (string or dict - will be cleaned)
    
    Returns:
        Dictionary with success status and response data
    """
    
    # FIX #5: Clean message - prevent JSON from reaching users
    clean_message = clean_message_for_whatsapp(message)
    
    # FIX #4: Validate phone number
    if not validate_phone_number(phone_number):
        logger.error(f"Invalid phone number rejected: {phone_number}")
        return {
            "success": False,
            "error": "Invalid phone number format",
            "phone_number": phone_number
        }
    
    normalized_phone = normalize_phone_number(phone_number)
    
    # Log the attempt (using logger, not print - FIX #7)
    logger.info(f"Sending WhatsApp message to: {normalized_phone}")
    logger.debug(f"Message content: {clean_message[:100]}{'...' if len(clean_message) > 100 else ''}")
    
    # Demo Mode - No credentials configured
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning(f"Demo Mode: Message to {normalized_phone} would be: {clean_message[:100]}...")
        return {
            "success": True,
            "mode": "demo",
            "phone_number": normalized_phone,
            "message": clean_message,
            "warning": "WhatsApp credentials not configured - message not actually sent"
        }
    
    # FIX #6: Handle long messages
    if len(clean_message) > MAX_MESSAGE_LENGTH:
        chunks = split_long_message(clean_message)
        results = []
        for i, chunk in enumerate(chunks):
            logger.info(f"Sending chunk {i+1}/{len(chunks)}")
            result = _send_text_message_chunk(normalized_phone, chunk)
            results.append(result)
            if i < len(chunks) - 1:
                time.sleep(0.5)  # Small delay between chunks
        
        # Return combined result
        all_success = all(r.get("success", False) for r in results)
        return {
            "success": all_success,
            "chunks_sent": len(chunks),
            "results": results,
            "phone_number": normalized_phone
        }
    
    return _send_text_message_chunk(normalized_phone, clean_message)

def _send_text_message_chunk(phone_number: str, message: str) -> Dict[str, Any]:
    """Internal function to send a single message chunk"""
    
    try:
        # Prepare headers
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"
        }
        
        # Prepare payload
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {
                "body": message,
                "preview_url": False
            }
        }
        
        logger.debug(f"WhatsApp API request to {phone_number}")
        
        # FIX #9: Use retry logic
        response = send_with_retry(
            url=get_whatsapp_url(),
            headers=headers,
            payload=payload,
            max_retries=3
        )
        
        # Log response details
        logger.info(f"WhatsApp API Response Status: {response.status_code}")
        
        # Check for error responses
        if response.status_code not in [200, 201]:
            logger.error(f"WhatsApp API error {response.status_code}: {response.text[:500]}")
            return {
                "success": False,
                "status_code": response.status_code,
                "response": response.text[:500],
                "error": f"HTTP {response.status_code}: {response.text[:200]}",
                "phone_number": phone_number
            }
        
        response_data = response.json()
        message_id = response_data.get("messages", [{}])[0].get("id") if response_data else None
        
        logger.info(f"WhatsApp message sent successfully to {phone_number}, message_id: {message_id}")
        
        return {
            "success": True,
            "status_code": response.status_code,
            "response": response_data,
            "message_id": message_id,
            "phone_number": phone_number
        }
        
    except requests.exceptions.Timeout:
        logger.error(f"WhatsApp API Timeout after 60 seconds for {phone_number}")
        return {
            "success": False,
            "error": "Request timeout after 60 seconds",
            "phone_number": phone_number
        }
        
    except requests.exceptions.ConnectionError as e:
        logger.error(f"WhatsApp API Connection Error for {phone_number}: {str(e)}")
        return {
            "success": False,
            "error": f"Connection error: {str(e)}",
            "phone_number": phone_number
        }
        
    except Exception as e:
        logger.error(f"WhatsApp API Unexpected Error for {phone_number}: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "phone_number": phone_number
        }


# ==========================================================
# SEND TEMPLATE MESSAGE
# ==========================================================

def send_template_message(phone_number: str, template_name: str, components: Optional[list] = None) -> Dict[str, Any]:
    """
    Send a template message via WhatsApp Cloud API
    
    Args:
        phone_number: Recipient's phone number with country code
        template_name: Name of the WhatsApp template
        components: Optional template components (variables)
    
    Returns:
        Dictionary with success status and response data
    """
    
    # FIX #4: Validate phone number
    if not validate_phone_number(phone_number):
        logger.error(f"Invalid phone number rejected: {phone_number}")
        return {
            "success": False,
            "error": "Invalid phone number format",
            "phone_number": phone_number
        }
    
    normalized_phone = normalize_phone_number(phone_number)
    
    logger.info(f"Sending WhatsApp template '{template_name}' to {normalized_phone}")
    
    # Demo Mode
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning(f"Demo Mode: Template '{template_name}' would be sent to {normalized_phone}")
        return {
            "success": True,
            "mode": "demo",
            "template": template_name,
            "phone_number": normalized_phone
        }

    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": normalized_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": "en_US"
                }
            }
        }
        
        # Add components if provided (for template variables)
        if components:
            payload["template"]["components"] = components
        
        # FIX #9: Use retry logic
        response = send_with_retry(
            url=get_whatsapp_url(),
            headers=headers,
            payload=payload,
            max_retries=3
        )
        
        logger.info(f"WhatsApp API Response Status: {response.status_code}")
        
        if response.status_code not in [200, 201]:
            logger.error(f"WhatsApp API error {response.status_code}: {response.text[:500]}")
            return {
                "success": False,
                "status_code": response.status_code,
                "response": response.text[:500],
                "error": f"HTTP {response.status_code}: {response.text[:200]}",
                "phone_number": normalized_phone
            }
        
        logger.info(f"WhatsApp template sent successfully to {normalized_phone}")
        
        return {
            "success": True,
            "status_code": response.status_code,
            "response": response.json(),
            "phone_number": normalized_phone
        }
        
    except Exception as e:
        logger.error(f"Error sending template to {normalized_phone}: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "phone_number": normalized_phone
        }


# ==========================================================
# SEND REPLY MESSAGE
# ==========================================================

def send_reply_message(phone_number: str, message: Union[str, Dict, Any], message_id: str) -> Dict[str, Any]:
    """
    Send a reply message referencing the original message
    
    Args:
        phone_number: Recipient's phone number
        message: Reply message content
        message_id: Original message ID to reply to
    
    Returns:
        Dictionary with success status and response data
    """
    
    # FIX #5: Clean message
    clean_message = clean_message_for_whatsapp(message)
    
    # FIX #4: Validate phone number
    if not validate_phone_number(phone_number):
        logger.error(f"Invalid phone number rejected: {phone_number}")
        return {
            "success": False,
            "error": "Invalid phone number format",
            "phone_number": phone_number
        }
    
    normalized_phone = normalize_phone_number(phone_number)
    
    logger.info(f"Sending WhatsApp reply to {normalized_phone} (reply to: {message_id})")
    
    # Demo Mode
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning(f"Demo Mode: Reply to {normalized_phone} would be: {clean_message[:100]}...")
        return {
            "success": True,
            "mode": "demo",
            "phone_number": normalized_phone,
            "message": clean_message,
            "reply_to": message_id
        }
    
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": normalized_phone,
            "type": "text",
            "text": {
                "body": clean_message,
                "preview_url": False
            },
            "context": {
                "message_id": message_id
            }
        }
        
        # FIX #9: Use retry logic
        response = send_with_retry(
            url=get_whatsapp_url(),
            headers=headers,
            payload=payload,
            max_retries=3
        )
        
        logger.info(f"WhatsApp API Response Status: {response.status_code}")
        
        if response.status_code not in [200, 201]:
            logger.error(f"WhatsApp API error {response.status_code}: {response.text[:500]}")
            return {
                "success": False,
                "status_code": response.status_code,
                "response": response.text[:500],
                "phone_number": normalized_phone
            }
        
        logger.info(f"WhatsApp reply sent successfully to {normalized_phone}")
        return {
            "success": True,
            "status_code": response.status_code,
            "response": response.json(),
            "phone_number": normalized_phone
        }
        
    except Exception as e:
        logger.error(f"Error sending reply to {normalized_phone}: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "phone_number": normalized_phone
        }


# ==========================================================
# FIX #2 & #3: ENHANCED PARSE WHATSAPP MESSAGE
# ==========================================================

def parse_whatsapp_message(payload: dict) -> Optional[List[Dict[str, Any]]]:
    """
    Parse incoming WhatsApp webhook payload - returns list of messages
    FIX #2: Process multiple messages
    FIX #3: Separate status handling
    """
    
    try:
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        parsed_messages = []
        
        # FIX #3: Separate status handling
        statuses = value.get("statuses", [])
        for status in statuses:
            parsed_status = parse_whatsapp_status(status)
            if parsed_status:
                parsed_messages.append(parsed_status)
        
        # FIX #2: Process ALL messages, not just first
        messages = value.get("messages", [])
        for message in messages:
            parsed_message = parse_single_message(message, value)
            if parsed_message:
                # FIX #1: Duplicate protection
                if is_duplicate_message(parsed_message["message_id"]):
                    logger.info(f"Duplicate message ignored: {parsed_message['message_id']}")
                    continue
                mark_message_processed(parsed_message["message_id"])
                parsed_messages.append(parsed_message)
        
        return parsed_messages if parsed_messages else None
        
    except Exception as e:
        logger.error(f"Error parsing WhatsApp webhook payload: {str(e)}")
        return None


def parse_whatsapp_status(status: dict) -> Optional[Dict[str, Any]]:
    """
    Parse WhatsApp status update separately
    FIX #3: Dedicated status handler
    """
    try:
        return {
            "type": "status",
            "message_id": status.get("id"),
            "status": status.get("status"),
            "timestamp": status.get("timestamp"),
            "recipient_id": status.get("recipient_id"),
            "conversation": status.get("conversation"),
            "pricing": status.get("pricing")
        }
    except Exception as e:
        logger.warning(f"Error parsing status: {e}")
        return None


def parse_single_message(message: dict, value: dict) -> Optional[Dict[str, Any]]:
    """
    Parse a single WhatsApp message
    FIX #11: Support multiple message types
    """
    try:
        message_type = message.get("type", "unknown")
        from_phone = message.get("from")
        message_id = message.get("id")
        timestamp = message.get("timestamp")
        
        parsed = {
            "type": message_type,
            "message_id": message_id,
            "from_phone": from_phone,
            "timestamp": timestamp,
        }
        
        # Handle different message types
        if message_type == "text":
            parsed["text"] = message.get("text", {}).get("body", "")
            
        elif message_type == "image":
            image = message.get("image", {})
            parsed["text"] = "[Image sent]"
            parsed["media_id"] = image.get("id")
            parsed["caption"] = image.get("caption", "")
            parsed["mime_type"] = image.get("mime_type")
            
        elif message_type == "document":
            document = message.get("document", {})
            parsed["text"] = f"[Document: {document.get('filename', 'unknown')}]"
            parsed["media_id"] = document.get("id")
            parsed["filename"] = document.get("filename")
            parsed["mime_type"] = document.get("mime_type")
            
        elif message_type == "audio":
            audio = message.get("audio", {})
            parsed["text"] = "[Audio message]"
            parsed["media_id"] = audio.get("id")
            parsed["mime_type"] = audio.get("mime_type")
            
        elif message_type == "video":
            video = message.get("video", {})
            parsed["text"] = "[Video message]"
            parsed["media_id"] = video.get("id")
            parsed["caption"] = video.get("caption", "")
            
        elif message_type == "interactive":
            interactive = message.get("interactive", {})
            interactive_type = interactive.get("type")
            if interactive_type == "button_reply":
                parsed["text"] = interactive.get("button_reply", {}).get("title", "")
                parsed["button_id"] = interactive.get("button_reply", {}).get("id")
            elif interactive_type == "list_reply":
                parsed["text"] = interactive.get("list_reply", {}).get("title", "")
                parsed["list_item_id"] = interactive.get("list_reply", {}).get("id")
            else:
                parsed["text"] = f"[Interactive: {interactive_type}]"
                
        elif message_type == "button":
            button = message.get("button", {})
            parsed["text"] = button.get("text", "")
            parsed["payload"] = button.get("payload")
            
        elif message_type == "location":
            location = message.get("location", {})
            parsed["text"] = f"Location: {location.get('latitude')}, {location.get('longitude')}"
            parsed["latitude"] = location.get("latitude")
            parsed["longitude"] = location.get("longitude")
            parsed["name"] = location.get("name")
            
        else:
            parsed["text"] = f"[{message_type} message received]"
        
        logger.info(f"Parsed {message_type} message from {from_phone}")
        logger.debug(f"Message preview: {parsed.get('text', '')[:100]}")
        
        return parsed
        
    except Exception as e:
        logger.error(f"Error parsing message: {str(e)}")
        return None


# ==========================================================
# VERIFY WEBHOOK
# ==========================================================

def verify_webhook(verify_token: str, challenge: str, configured_token: str) -> Dict[str, Any]:
    """
    Verify WhatsApp webhook endpoint
    
    Args:
        verify_token: Token from Meta (hub.verify_token)
        challenge: Challenge from Meta (hub.challenge)
        configured_token: Your configured verify token
    
    Returns:
        Verification result with challenge if successful
    """
    
    logger.info(f"Webhook verification attempt")
    logger.debug(f"Token provided: {verify_token[:20] if verify_token else 'None'}...")
    
    if verify_token == configured_token:
        logger.info("Webhook verification successful")
        return {
            "success": True,
            "challenge": challenge
        }
    
    logger.warning(f"Webhook verification failed: token mismatch")
    return {
        "success": False
    }


# ==========================================================
# SEND MEDIA MESSAGE
# ==========================================================

def send_media_message(phone_number: str, media_id: str, media_type: str = "image", caption: str = None) -> Dict[str, Any]:
    """
    Send a media message (image, video, audio, document)
    
    Args:
        phone_number: Recipient's phone number
        media_id: WhatsApp media ID from uploaded media
        media_type: Type of media (image, video, audio, document)
        caption: Optional caption for image/video
    
    Returns:
        Dictionary with success status and response data
    """
    
    # FIX #4: Validate phone number
    if not validate_phone_number(phone_number):
        logger.error(f"Invalid phone number rejected: {phone_number}")
        return {
            "success": False,
            "error": "Invalid phone number format",
            "phone_number": phone_number
        }
    
    normalized_phone = normalize_phone_number(phone_number)
    
    logger.info(f"Sending WhatsApp {media_type} to: {normalized_phone}")
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning(f"Demo Mode: {media_type} would be sent to {normalized_phone}")
        return {
            "success": True,
            "mode": "demo",
            "phone_number": normalized_phone,
            "media_type": media_type
        }
    
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": normalized_phone,
            "type": media_type,
            media_type: {
                "id": media_id
            }
        }
        
        # Add caption if provided and applicable
        if caption and media_type in ["image", "video"]:
            payload[media_type]["caption"] = caption[:1024]  # Caption limit is 1024 chars
        
        # FIX #9: Use retry logic (longer timeout for media)
        response = send_with_retry(
            url=get_whatsapp_url(),
            headers=headers,
            payload=payload,
            max_retries=3,
            retry_delays=[2, 5, 10]  # Longer delays for media
        )
        
        logger.info(f"WhatsApp API Response Status: {response.status_code}")
        
        if response.status_code not in [200, 201]:
            logger.error(f"WhatsApp API error {response.status_code}: {response.text[:500]}")
            return {
                "success": False,
                "status_code": response.status_code,
                "response": response.text[:500],
                "phone_number": normalized_phone
            }
        
        logger.info(f"WhatsApp {media_type} sent successfully to {normalized_phone}")
        return {
            "success": True,
            "status_code": response.status_code,
            "response": response.json(),
            "phone_number": normalized_phone
        }
        
    except Exception as e:
        logger.error(f"Error sending {media_type} to {normalized_phone}: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "phone_number": normalized_phone
        }


# ==========================================================
# DEMO MESSAGE (for testing)
# ==========================================================

def create_demo_message() -> Dict[str, Any]:
    """Create a demo WhatsApp webhook payload for testing"""
    
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "demo123",
                                    "from": "923001234567",
                                    "type": "text",
                                    "timestamp": str(int(time.time())),
                                    "text": {
                                        "body": "Where is my order?"
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }


# ==========================================================
# FIX #10: IMPROVED HEALTH CHECK
# ==========================================================

def health_check() -> Dict[str, Any]:
    """
    Check if WhatsApp service is healthy
    FIX #10: Verify actual API connectivity, not just token existence
    """
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {
            "healthy": False,
            "mode": "demo",
            "message": "WhatsApp credentials not configured",
            "timestamp": datetime.utcnow().isoformat()
        }
    
    # Verify API connectivity
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"
        }
        
        # Try to get phone number info - lightweight API call
        api_version = WHATSAPP_API_VERSION or "v25.0"
        api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
        debug_url = f"{api_url}/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}"
        
        response = requests.get(debug_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return {
                "healthy": True,
                "mode": "production",
                "api_version": api_version,
                "api_connected": True,
                "phone_number_verified": True,
                "timestamp": datetime.utcnow().isoformat()
            }
        elif response.status_code == 401:
            return {
                "healthy": False,
                "mode": "production",
                "api_version": api_version,
                "api_connected": False,
                "error": "Invalid or expired access token",
                "timestamp": datetime.utcnow().isoformat()
            }
        else:
            return {
                "healthy": False,
                "mode": "production",
                "api_version": api_version,
                "api_connected": False,
                "status_code": response.status_code,
                "error": f"API returned {response.status_code}",
                "timestamp": datetime.utcnow().isoformat()
            }
            
    except Exception as e:
        return {
            "healthy": False,
            "mode": "production",
            "api_version": WHATSAPP_API_VERSION or "v25.0",
            "api_connected": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


def whatsapp_status() -> Dict[str, Any]:
    """Get WhatsApp service status (alias for health_check)"""
    return health_check()


# ==========================================================
# FIX #12: ENHANCED ERROR LOGGING
# ==========================================================

def log_whatsapp_error(
    error: Exception,
    phone_number: str = None,
    message_id: str = None,
    payload: Dict = None,
    status_code: int = None,
    response_body: str = None
):
    """Enhanced error logging for WhatsApp API issues"""
    
    error_details = {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if phone_number:
        error_details["phone_number"] = phone_number
    if message_id:
        error_details["message_id"] = message_id
    if status_code:
        error_details["status_code"] = status_code
    if response_body:
        error_details["response_preview"] = response_body[:500]
    
    # Don't log full payload in production unless debug
    if payload and logger.isEnabledFor(logging.DEBUG):
        error_details["payload"] = payload
    
    logger.error(f"WhatsApp API Error: {json.dumps(error_details, default=str)}")
