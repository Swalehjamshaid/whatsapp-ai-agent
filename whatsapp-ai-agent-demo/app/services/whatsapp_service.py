# ==========================================================
# FILE: app/services/whatsapp_service.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

import requests
import logging
from typing import Optional, Dict, Any

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_API_VERSION,
    WHATSAPP_API_URL
)

# ==========================================================
# LOGGING SETUP
# ==========================================================

logger = logging.getLogger(__name__)

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
# SEND TEXT MESSAGE
# ==========================================================

def send_text_message(phone_number: str, message: str) -> Dict[str, Any]:
    """
    Send a text message via WhatsApp Cloud API
    
    Args:
        phone_number: Recipient's phone number with country code
        message: Text message to send
    
    Returns:
        Dictionary with success status and response data
    """
    
    # Log the attempt
    print(f"📤 Sending WhatsApp message to: {phone_number}")
    print(f"📝 Message content: {message[:100]}{'...' if len(message) > 100 else ''}")
    logger.info(f"Sending message to {phone_number}: {message[:50]}...")
    
    # Demo Mode - No credentials configured
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        print("⚠️ Demo Mode: WhatsApp credentials not configured")
        logger.warning("WhatsApp message would be sent but credentials missing")
        return {
            "success": True,
            "mode": "demo",
            "phone_number": phone_number,
            "message": message,
            "warning": "WhatsApp credentials not configured - message not actually sent"
        }

    try:
        # Prepare headers
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # Prepare payload
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {
                "body": message
            }
        }
        
        # Log request details (without full token)
        print(f"🔗 API URL: {get_whatsapp_url()}")
        print(f"📦 Payload: {payload}")
        logger.debug(f"WhatsApp API request to {phone_number}")
        
        # Make the request
        response = requests.post(
            get_whatsapp_url(),
            headers=headers,
            json=payload,
            timeout=30
        )
        
        # Log response details
        print(f"📊 WhatsApp API Response Status: {response.status_code}")
        print(f"📄 WhatsApp API Response Body: {response.text}")
        logger.info(f"WhatsApp API responded with status {response.status_code}")
        
        # Check for error responses
        if response.status_code not in [200, 201]:
            print(f"❌ WhatsApp API Error: {response.status_code}")
            logger.error(f"WhatsApp API error {response.status_code}: {response.text}")
            return {
                "success": False,
                "status_code": response.status_code,
                "response": response.text,
                "error": f"HTTP {response.status_code}: {response.text[:200]}"
            }
        
        print(f"✅ WhatsApp message sent successfully to {phone_number}")
        logger.info(f"Successfully sent message to {phone_number}")
        
        return {
            "success": True,
            "status_code": response.status_code,
            "response": response.json(),
            "message_id": response.json().get("messages", [{}])[0].get("id") if response.json() else None
        }
        
    except requests.exceptions.Timeout:
        print(f"❌ WhatsApp API Timeout after 30 seconds")
        logger.error(f"Timeout sending message to {phone_number}")
        return {
            "success": False,
            "error": "Request timeout after 30 seconds",
            "phone_number": phone_number
        }
        
    except requests.exceptions.ConnectionError as e:
        print(f"❌ WhatsApp API Connection Error: {str(e)}")
        logger.error(f"Connection error sending to {phone_number}: {str(e)}")
        return {
            "success": False,
            "error": f"Connection error: {str(e)}",
            "phone_number": phone_number
        }
        
    except Exception as e:
        print(f"❌ WhatsApp API Unexpected Error: {str(e)}")
        logger.error(f"Unexpected error sending to {phone_number}: {str(e)}")
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
    
    print(f"📤 Sending WhatsApp template to: {phone_number}")
    print(f"📝 Template: {template_name}")
    logger.info(f"Sending template {template_name} to {phone_number}")
    
    # Demo Mode
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        print("⚠️ Demo Mode: WhatsApp credentials not configured")
        logger.warning(f"WhatsApp template {template_name} would be sent but credentials missing")
        return {
            "success": True,
            "mode": "demo",
            "template": template_name,
            "phone_number": phone_number
        }

    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
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
        
        response = requests.post(
            get_whatsapp_url(),
            headers=headers,
            json=payload,
            timeout=30
        )
        
        # Log response
        print(f"📊 WhatsApp API Response Status: {response.status_code}")
        print(f"📄 WhatsApp API Response Body: {response.text}")
        logger.info(f"WhatsApp API responded with status {response.status_code}")
        
        # Check for error responses
        if response.status_code not in [200, 201]:
            logger.error(f"WhatsApp API error {response.status_code}: {response.text}")
            return {
                "success": False,
                "status_code": response.status_code,
                "response": response.text,
                "error": f"HTTP {response.status_code}: {response.text[:200]}"
            }
        
        print(f"✅ WhatsApp template sent successfully to {phone_number}")
        logger.info(f"Successfully sent template {template_name} to {phone_number}")
        
        return {
            "success": True,
            "status_code": response.status_code,
            "response": response.json()
        }
        
    except Exception as e:
        print(f"❌ WhatsApp API Error: {str(e)}")
        logger.error(f"Error sending template to {phone_number}: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


# ==========================================================
# SEND REPLY MESSAGE
# ==========================================================

def send_reply_message(phone_number: str, message: str, message_id: str) -> Dict[str, Any]:
    """
    Send a reply message referencing the original message
    
    Args:
        phone_number: Recipient's phone number
        message: Reply message content
        message_id: Original message ID to reply to
    
    Returns:
        Dictionary with success status and response data
    """
    
    print(f"📤 Sending WhatsApp reply to: {phone_number} (reply to: {message_id})")
    logger.info(f"Sending reply to {phone_number} for message {message_id}")
    
    # Demo Mode
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        print("⚠️ Demo Mode: WhatsApp credentials not configured")
        return {
            "success": True,
            "mode": "demo",
            "phone_number": phone_number,
            "message": message,
            "reply_to": message_id
        }
    
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {
                "body": message,
                "preview_url": False
            },
            "context": {
                "message_id": message_id
            }
        }
        
        response = requests.post(
            get_whatsapp_url(),
            headers=headers,
            json=payload,
            timeout=30
        )
        
        print(f"📊 WhatsApp API Response Status: {response.status_code}")
        print(f"📄 WhatsApp API Response Body: {response.text}")
        logger.info(f"WhatsApp API responded with status {response.status_code}")
        
        if response.status_code not in [200, 201]:
            logger.error(f"WhatsApp API error {response.status_code}: {response.text}")
            return {
                "success": False,
                "status_code": response.status_code,
                "response": response.text
            }
        
        print(f"✅ WhatsApp reply sent successfully to {phone_number}")
        return {
            "success": True,
            "status_code": response.status_code,
            "response": response.json()
        }
        
    except Exception as e:
        print(f"❌ WhatsApp API Error: {str(e)}")
        logger.error(f"Error sending reply to {phone_number}: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


# ==========================================================
# PARSE INCOMING MESSAGE
# ==========================================================

def parse_whatsapp_message(payload: dict) -> Optional[Dict[str, Any]]:
    """
    Parse incoming WhatsApp webhook payload
    
    Args:
        payload: Raw webhook payload from Meta
    
    Returns:
        Parsed message data or None if invalid
    """
    
    try:
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if not messages:
            return None
        
        message = messages[0]
        
        parsed_message = {
            "message_id": message.get("id"),
            "from_phone": message.get("from"),
            "type": message.get("type"),
            "timestamp": message.get("timestamp"),
            "text": message.get("text", {}).get("body", "") if message.get("type") == "text" else "",
            "status": value.get("statuses", [{}])[0].get("status") if "statuses" in value else None
        }
        
        # Log parsed message
        print(f"📨 Parsed WhatsApp message from {parsed_message['from_phone']}")
        print(f"📝 Message: {parsed_message['text'][:100]}")
        logger.info(f"Parsed message from {parsed_message['from_phone']}: {parsed_message['text'][:50]}...")
        
        return parsed_message
        
    except Exception as e:
        print(f"❌ Error parsing WhatsApp message: {str(e)}")
        logger.error(f"Error parsing webhook payload: {str(e)}")
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
    
    print(f"🔐 Webhook verification attempt")
    print(f"📝 Token provided: {verify_token[:20]}...")
    print(f"✅ Expected token: {configured_token[:20]}...")
    logger.info(f"Webhook verification: token_match={verify_token == configured_token}")
    
    if verify_token == configured_token:
        print("✅ Webhook verification successful")
        return {
            "success": True,
            "challenge": challenge
        }
    
    print("❌ Webhook verification failed - token mismatch")
    logger.warning(f"Webhook verification failed: token mismatch")
    return {
        "success": False
    }


# ==========================================================
# SEND MEDIA MESSAGE
# ==========================================================

def send_media_message(phone_number: str, media_id: str, media_type: str = "image") -> Dict[str, Any]:
    """
    Send a media message (image, video, audio, document)
    
    Args:
        phone_number: Recipient's phone number
        media_id: WhatsApp media ID from uploaded media
        media_type: Type of media (image, video, audio, document)
    
    Returns:
        Dictionary with success status and response data
    """
    
    print(f"📤 Sending WhatsApp {media_type} to: {phone_number}")
    logger.info(f"Sending {media_type} to {phone_number}")
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        print("⚠️ Demo Mode: WhatsApp credentials not configured")
        return {
            "success": True,
            "mode": "demo",
            "phone_number": phone_number,
            "media_type": media_type
        }
    
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": media_type,
            media_type: {
                "id": media_id
            }
        }
        
        response = requests.post(
            get_whatsapp_url(),
            headers=headers,
            json=payload,
            timeout=60  # Longer timeout for media
        )
        
        print(f"📊 WhatsApp API Response Status: {response.status_code}")
        print(f"📄 WhatsApp API Response Body: {response.text}")
        logger.info(f"WhatsApp API responded with status {response.status_code}")
        
        if response.status_code not in [200, 201]:
            return {
                "success": False,
                "status_code": response.status_code,
                "response": response.text
            }
        
        print(f"✅ WhatsApp {media_type} sent successfully to {phone_number}")
        return {
            "success": True,
            "status_code": response.status_code,
            "response": response.json()
        }
        
    except Exception as e:
        print(f"❌ WhatsApp API Error: {str(e)}")
        logger.error(f"Error sending {media_type} to {phone_number}: {str(e)}")
        return {
            "success": False,
            "error": str(e)
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
                                    "timestamp": "1700000000",
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
# SERVICE STATUS
# ==========================================================

def whatsapp_status() -> Dict[str, Any]:
    """Get WhatsApp service status"""
    
    is_configured = bool(WHATSAPP_ACCESS_TOKEN) and bool(WHATSAPP_PHONE_NUMBER_ID)
    
    return {
        "service": "WhatsApp Cloud API",
        "configured": is_configured,
        "phone_number_id": bool(WHATSAPP_PHONE_NUMBER_ID),
        "api_version": WHATSAPP_API_VERSION or "v25.0",
        "api_url": WHATSAPP_API_URL or "https://graph.facebook.com",
        "status": "active" if is_configured else "demo_mode"
    }


# ==========================================================
# HEALTH CHECK
# ==========================================================

def health_check() -> Dict[str, Any]:
    """Check if WhatsApp service is healthy"""
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {
            "healthy": False,
            "mode": "demo",
            "message": "WhatsApp credentials not configured"
        }
    
    return {
        "healthy": True,
        "mode": "production",
        "api_version": WHATSAPP_API_VERSION or "v25.0"
    }
