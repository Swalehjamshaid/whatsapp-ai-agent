# ==========================================================
# FILE: app/services/whatsapp_service.py
# VERSION: 2.0
# PURPOSE: WhatsApp Cloud API Service - Complete Production Version
# ARCHITECTURE: Webhook → whatsapp_service
# ==========================================================

import requests
import asyncio
import time
from typing import Dict, Any, Optional, List
from datetime import datetime
from loguru import logger
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config import config


class WhatsAppService:
    """
    WhatsApp Cloud API Service
    Production-ready with retry logic, rate limiting, and comprehensive logging
    """

    def __init__(self):
        self.access_token = config.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = config.WHATSAPP_PHONE_NUMBER_ID
        self.api_version = "v17.0"
        
        self.base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        self.media_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/media"
        
        # Create session with retry strategy
        self.session = self._create_session()
        
        # Rate limiting tracking
        self._rate_limit_remaining = None
        self._rate_limit_reset = None
        
        logger.info(f"WhatsApp Service initialized (API v{self.api_version})")

    def _create_session(self) -> requests.Session:
        """Create requests session with retry strategy"""
        session = requests.Session()
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20
        )
        
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        return session

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def _update_rate_limits(self, response: requests.Response) -> None:
        """Update rate limit tracking from response headers"""
        if "X-RateLimit-Remaining" in response.headers:
            self._rate_limit_remaining = int(response.headers["X-RateLimit-Remaining"])
        if "X-RateLimit-Reset" in response.headers:
            self._rate_limit_reset = int(response.headers["X-RateLimit-Reset"])

    def _check_rate_limit(self) -> bool:
        """Check if we're approaching rate limits"""
        if self._rate_limit_remaining is not None:
            if self._rate_limit_remaining < 5:
                logger.warning(f"Rate limit low: {self._rate_limit_remaining} remaining")
                if self._rate_limit_reset:
                    wait_time = self._rate_limit_reset - time.time()
                    if wait_time > 0 and wait_time < 10:
                        logger.warning(f"Rate limit reset in {wait_time:.1f}s")
                        return False
        return True

    def send_text_message(
        self,
        phone_number: str,
        message: str,
        preview_url: bool = False
    ) -> Dict[str, Any]:
        """
        Send a text message via WhatsApp Cloud API
        
        Args:
            phone_number: Recipient's phone number (format: 1234567890)
            message: Message content (max 4096 characters)
            preview_url: Whether to show URL previews
        
        Returns:
            Dict with success status, message_id, and response data
        """
        
        if not self._check_rate_limit():
            return {
                "success": False,
                "error": "Rate limit approaching, please wait",
                "status_code": 429
            }
        
        if not self.access_token or not self.phone_number_id:
            logger.error("WhatsApp credentials missing")
            return {
                "success": False,
                "error": "WhatsApp service not configured",
                "status_code": 500
            }
        
        # Clean phone number (remove any non-digit characters)
        cleaned_number = re.sub(r'\D', '', phone_number)
        if not cleaned_number.startswith('1') and len(cleaned_number) == 10:
            cleaned_number = '1' + cleaned_number
        
        # Truncate message if too long (WhatsApp limit is 4096)
        if len(message) > 4000:
            message = message[:3997] + "..."
            logger.warning(f"Message truncated to {len(message)} chars")
        
        try:
            headers = self._get_headers()
            
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": cleaned_number,
                "type": "text",
                "text": {
                    "preview_url": preview_url,
                    "body": message
                }
            }
            
            logger.debug(f"Sending message to {cleaned_number}: {message[:50]}...")
            
            response = self.session.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            self._update_rate_limits(response)
            result = response.json()
            
            if response.status_code in [200, 201]:
                message_id = None
                if result.get("messages"):
                    message_id = result["messages"][0].get("id")
                
                logger.success(
                    f"✅ Message sent | To: {cleaned_number} | "
                    f"MsgID: {message_id} | Status: {response.status_code}"
                )
                
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "message_id": message_id,
                    "response": result,
                    "to": cleaned_number,
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Handle specific error cases
            error_data = result.get("error", {})
            error_code = error_data.get("code")
            error_message = error_data.get("message", "Unknown error")
            
            if error_code == 131026:
                logger.warning(f"User blocked or cannot receive messages: {cleaned_number}")
                return {
                    "success": False,
                    "error": "User cannot receive messages",
                    "error_code": error_code,
                    "status_code": response.status_code
                }
            elif error_code == 1006:
                logger.warning(f"Invalid phone number: {cleaned_number}")
                return {
                    "success": False,
                    "error": "Invalid phone number format",
                    "error_code": error_code,
                    "status_code": response.status_code
                }
            
            logger.error(
                f"❌ API Error | To: {cleaned_number} | "
                f"Status: {response.status_code} | Code: {error_code} | "
                f"Message: {error_message}"
            )
            
            return {
                "success": False,
                "status_code": response.status_code,
                "error": error_message,
                "error_code": error_code,
                "response": result
            }
            
        except requests.Timeout:
            logger.error(f"Timeout sending message to {cleaned_number}")
            return {
                "success": False,
                "error": "Request timeout",
                "status_code": 408
            }
        except requests.ConnectionError as e:
            logger.error(f"Connection error: {e}")
            return {
                "success": False,
                "error": "Connection error",
                "status_code": 503
            }
        except Exception as e:
            logger.exception(f"Unexpected error sending message: {e}")
            return {
                "success": False,
                "error": str(e),
                "status_code": 500
            }

    def send_template_message(
        self,
        phone_number: str,
        template_name: str,
        language: str = "en_US",
        components: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        Send a template message via WhatsApp Cloud API
        
        Args:
            phone_number: Recipient's phone number
            template_name: Name of the approved template
            language: Language code (e.g., 'en_US', 'es_MX')
            components: Optional template components (header, body, buttons)
        
        Returns:
            Dict with success status and response data
        """
        
        if not self.access_token or not self.phone_number_id:
            return {
                "success": False,
                "error": "WhatsApp service not configured"
            }
        
        cleaned_number = re.sub(r'\D', '', phone_number)
        if not cleaned_number.startswith('1') and len(cleaned_number) == 10:
            cleaned_number = '1' + cleaned_number
        
        try:
            headers = self._get_headers()
            
            payload = {
                "messaging_product": "whatsapp",
                "to": cleaned_number,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {
                        "code": language
                    }
                }
            }
            
            if components:
                payload["template"]["components"] = components
            
            response = self.session.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            result = response.json()
            
            if response.status_code in [200, 201]:
                message_id = None
                if result.get("messages"):
                    message_id = result["messages"][0].get("id")
                
                logger.success(f"✅ Template sent | To: {cleaned_number} | Template: {template_name}")
                
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "message_id": message_id,
                    "response": result
                }
            
            logger.error(f"❌ Template error | Status: {response.status_code} | Error: {result}")
            
            return {
                "success": False,
                "status_code": response.status_code,
                "error": result.get("error", {}).get("message", "Unknown error"),
                "response": result
            }
            
        except Exception as e:
            logger.exception(f"Template send failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def mark_message_as_read(self, message_id: str) -> Dict[str, Any]:
        """
        Mark a message as read (send read receipt)
        
        Args:
            message_id: ID of the message to mark as read
        
        Returns:
            Dict with success status
        """
        
        if not self.access_token or not self.phone_number_id:
            return {"success": False, "error": "Service not configured"}
        
        try:
            headers = self._get_headers()
            
            payload = {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id
            }
            
            response = self.session.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=10
            )
            
            success = response.status_code in [200, 201]
            
            if success:
                logger.debug(f"✅ Marked as read | MsgID: {message_id}")
            
            return {
                "success": success,
                "status_code": response.status_code
            }
            
        except Exception as e:
            logger.error(f"Failed to mark as read: {e}")
            return {"success": False, "error": str(e)}

    def get_media_url(self, media_id: str) -> Optional[str]:
        """
        Get download URL for media file
        
        Args:
            media_id: Media ID from WhatsApp
        
        Returns:
            Download URL or None
        """
        
        try:
            headers = self._get_headers()
            
            url = f"https://graph.facebook.com/{self.api_version}/{media_id}"
            
            response = self.session.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return data.get("url")
            
            logger.error(f"Failed to get media URL: {response.status_code}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting media URL: {e}")
            return None

    def health_check(self) -> Dict[str, Any]:
        """
        Check service health and configuration
        
        Returns:
            Dict with service status and configuration info
        """
        
        return {
            "service": "whatsapp",
            "version": "2.0",
            "configured": bool(self.access_token and self.phone_number_id),
            "api_version": self.api_version,
            "phone_number_id": self.phone_number_id[:6] + "..." if self.phone_number_id else None,
            "token_configured": bool(self.access_token),
            "rate_limit_remaining": self._rate_limit_remaining,
            "base_url": self.base_url.replace(self.access_token, "***") if self.access_token else None
        }


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_whatsapp_service = None


def get_whatsapp_service() -> WhatsAppService:
    """Get or create the singleton WhatsApp service instance"""
    
    global _whatsapp_service
    
    if _whatsapp_service is None:
        _whatsapp_service = WhatsAppService()
    
    return _whatsapp_service


# ==========================================================
# COMPATIBILITY FUNCTIONS (Used by webhook.py)
# ==========================================================

def send_text_message(phone_number: str, message: str) -> Dict[str, Any]:
    """
    Compatibility function for webhook.py
    Direct function call interface
    
    Args:
        phone_number: Recipient's phone number
        message: Message text
    
    Returns:
        Dict with success status and details
    """
    service = get_whatsapp_service()
    return service.send_text_message(phone_number=phone_number, message=message)


def send_template_message(phone_number: str, template_name: str, **kwargs) -> Dict[str, Any]:
    """
    Compatibility function for template messages
    
    Args:
        phone_number: Recipient's phone number
        template_name: Template name
        **kwargs: Additional parameters (language, components)
    
    Returns:
        Dict with success status
    """
    service = get_whatsapp_service()
    return service.send_template_message(
        phone_number=phone_number,
        template_name=template_name,
        language=kwargs.get('language', 'en_US'),
        components=kwargs.get('components')
    )


def mark_message_as_read(message_id: str) -> Dict[str, Any]:
    """
    Mark a message as read
    
    Args:
        message_id: Message ID to mark as read
    
    Returns:
        Dict with success status
    """
    service = get_whatsapp_service()
    return service.mark_message_as_read(message_id)


# ==========================================================
# ASYNC COMPATIBILITY (for background tasks)
# ==========================================================

async def send_text_message_async(phone_number: str, message: str) -> Dict[str, Any]:
    """
    Async wrapper for send_text_message
    Use this for background tasks to avoid blocking
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, send_text_message, phone_number, message)


async def send_template_message_async(phone_number: str, template_name: str, **kwargs) -> Dict[str, Any]:
    """
    Async wrapper for send_template_message
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, 
        lambda: send_template_message(phone_number, template_name, **kwargs)
    )


# ==========================================================
# IMPORT FOR REGEX (used in phone number cleaning)
# ==========================================================

import re


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("WhatsApp Service v2.0 loaded - Production ready")
