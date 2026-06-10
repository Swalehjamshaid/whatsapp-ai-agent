# ==========================================================
# FILE: app/services/whatsapp_service.py (ENTERPRISE v5.0)
# ==========================================================
# PURPOSE: WhatsApp Cloud API Communication Layer
# ARCHITECTURE: webhook.py → ai_query_service.py → ... → whatsapp_service.py → Meta API
# ==========================================================

import re
from typing import Dict, Any, Optional
from datetime import datetime
from loguru import logger
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from cachetools import TTLCache

from app.config import config


# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = 4000
DEFAULT_TIMEOUT = 15  # Reduced from 30 to match webhook timeout
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 1
DEFAULT_API_VERSION = "v20.0"  # Updated from v18.0


# ==========================================================
# MESSAGE TEMPLATES
# ==========================================================

class MessageTemplates:
    HELP = """📋 *Available Commands*

• *Track DN* - Send any 10+ digit number
• *Dealer Performance* - "Show dealer ABC Traders"
• *Warehouse Status* - "Stock at Mumbai warehouse"
• *Pending PODs* - "Pending POD Lahore"
• *KPI Dashboard* - "Show me KPIs"
• *Control Tower* - "Control tower"

Need help? Reply with your question."""
    
    WELCOME = "👋 Welcome to Logistics AI!\n\nSend any 10+ digit number to track your delivery note."


# ==========================================================
# WHATSAPP SERVICE
# ==========================================================

class WhatsAppService:
    def __init__(self):
        self.access_token = config.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = config.WHATSAPP_PHONE_NUMBER_ID
        
        # CRITICAL FIX #2: Use configurable API version
        self.api_version = getattr(config, 'WHATSAPP_API_VERSION', DEFAULT_API_VERSION)
        self.base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        
        self.session = self._create_session()
        
        # CRITICAL FIX #2: Use TTLCache instead of dict to prevent memory leak
        self._message_tracking = TTLCache(maxsize=10000, ttl=86400)  # 24 hours TTL
        self._request_tracking = TTLCache(maxsize=10000, ttl=3600)    # 1 hour TTL for request tracking
        
        # Health check cache
        self._last_health_check = None
        self._health_check_result = None
        
        logger.info(f"WhatsApp Service v5.0 initialized (API: {self.api_version})")
    
    def _create_session(self) -> requests.Session:
        """Create requests session with retry strategy."""
        session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
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
        """Get request headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def _clean_phone_number(self, phone_number: str) -> str:
        """
        CRITICAL FIX #1: Fix country code handling for Pakistan.
        Removes all non-digit characters and formats correctly.
        """
        cleaned = re.sub(r'\D', '', phone_number)
        
        # Handle Pakistan numbers
        if cleaned.startswith('0'):
            # Convert 03XXXXXXXXX to 923XXXXXXXXX
            cleaned = '92' + cleaned[1:]
        elif cleaned.startswith('92'):
            # Already has Pakistan code
            pass
        elif len(cleaned) == 10:
            # Assume 10-digit number is a local Pakistan number
            cleaned = '92' + cleaned
        
        logger.debug(f"Phone number cleaned: {phone_number} -> {cleaned}")
        return cleaned
    
    def _validate_message(self, message: str) -> bool:
        """
        MEDIUM FIX #4: Validate message before sending.
        """
        if not message or not message.strip():
            logger.warning("Empty message rejected")
            return False
        return True
    
    def send_text_message(
        self, 
        phone_number: str, 
        message: str, 
        preview_url: bool = False, 
        message_id: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a text message via WhatsApp Cloud API.
        
        Args:
            phone_number: Recipient's phone number
            message: Message content
            preview_url: Whether to show URL previews
            message_id: Optional message ID for reply context
            request_id: Optional request ID for tracing (CRITICAL FIX #5)
        
        Returns:
            Dict with success status and response data
        """
        # Log request with ID if provided
        req_id = request_id or "unknown"
        
        # Check configuration
        if not self.access_token or not self.phone_number_id:
            logger.error(f"[{req_id}] WhatsApp service not configured")
            return {"success": False, "error": "WhatsApp service not configured"}
        
        # Validate message (MEDIUM FIX #4)
        if not self._validate_message(message):
            logger.warning(f"[{req_id}] Empty message rejected")
            return {"success": False, "error": "Empty message"}
        
        # Clean phone number
        cleaned_number = self._clean_phone_number(phone_number)
        
        # Truncate long messages (CRITICAL FIX #6)
        if len(message) > MAX_MESSAGE_LENGTH:
            message = message[:MAX_MESSAGE_LENGTH - 50] + "\n\n... (truncated)"
            logger.warning(f"[{req_id}] Message truncated to {MAX_MESSAGE_LENGTH} chars")
        
        try:
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
            
            # Add context for reply
            if message_id:
                payload["context"] = {"message_id": message_id}
            
            # CRITICAL FIX #3: Make request with proper error handling
            response = self.session.post(
                self.base_url, 
                headers=self._get_headers(), 
                json=payload, 
                timeout=DEFAULT_TIMEOUT
            )
            
            # CRITICAL FIX #3: Safe JSON parsing
            try:
                result = response.json()
            except Exception as json_err:
                logger.error(f"[{req_id}] Failed to parse JSON response: {json_err}")
                result = {
                    "error": {
                        "message": f"Invalid JSON response: {response.text[:200]}"
                    }
                }
            
            # Track request for monitoring
            request_key = f"{cleaned_number}_{datetime.utcnow().timestamp()}"
            self._request_tracking[request_key] = {
                "status_code": response.status_code,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            if response.status_code in [200, 201]:
                response_message_id = result.get("messages", [{}])[0].get("id")
                
                # CRITICAL FIX #2: Store in TTLCache instead of dict
                if response_message_id:
                    self._message_tracking[response_message_id] = {
                        "to": cleaned_number,
                        "status": "sent",
                        "sent_at": datetime.utcnow().isoformat(),
                        "request_id": req_id
                    }
                
                logger.success(f"[{req_id}] ✅ Message sent to {cleaned_number} (ID: {response_message_id})")
                return {
                    "success": True, 
                    "status_code": response.status_code, 
                    "message_id": response_message_id
                }
            
            # Handle error response
            error_msg = result.get("error", {}).get("message", f"HTTP {response.status_code}")
            logger.error(f"[{req_id}] ❌ API Error: {response.status_code} - {error_msg}")
            
            return {
                "success": False, 
                "status_code": response.status_code, 
                "error": error_msg
            }
            
        except requests.Timeout:
            logger.error(f"[{req_id}] Request timeout after {DEFAULT_TIMEOUT}s for {cleaned_number}")
            return {"success": False, "error": "Request timeout"}
        
        except requests.ConnectionError as e:
            logger.error(f"[{req_id}] Connection error: {e}")
            return {"success": False, "error": "Connection error"}
        
        except Exception as e:
            logger.exception(f"[{req_id}] Send failed: {e}")
            return {"success": False, "error": str(e)}
    
    def send_help_message(self, phone_number: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Send help message directly."""
        return self.send_text_message(phone_number, MessageTemplates.HELP, request_id=request_id)
    
    def send_welcome_message(self, phone_number: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Send welcome message."""
        return self.send_text_message(phone_number, MessageTemplates.WELCOME, request_id=request_id)
    
    def get_message_status(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a sent message from cache."""
        return self._message_tracking.get(message_id)
    
    def health_check(self, verify_meta: bool = False) -> Dict[str, Any]:
        """
        CRITICAL FIX #4: Enhanced health check with Meta verification.
        
        Args:
            verify_meta: If True, actually calls Meta API to verify connectivity
        """
        import time
        
        # Check if we have a cached result (refresh every 60 seconds)
        current_time = time.time()
        if self._last_health_check and (current_time - self._last_health_check) < 60:
            return self._health_check_result
        
        result = {
            "service": "whatsapp",
            "version": "5.0",
            "configured": bool(self.access_token and self.phone_number_id),
            "api_version": self.api_version,
            "api_key_valid_format": bool(self.access_token and len(self.access_token) > 20),
            "phone_id_valid": bool(self.phone_number_id),
            "cache_size": len(self._message_tracking),
            "request_tracking_size": len(self._request_tracking),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # CRITICAL FIX #4: Optional Meta API verification
        if verify_meta and result["configured"]:
            try:
                # Test endpoint to verify token works
                test_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}"
                response = self.session.get(
                    test_url,
                    headers=self._get_headers(),
                    timeout=10
                )
                
                if response.status_code == 200:
                    result["meta_api_status"] = "healthy"
                    result["meta_api_verified"] = True
                else:
                    result["meta_api_status"] = "unhealthy"
                    result["meta_api_error"] = f"HTTP {response.status_code}"
                    result["meta_api_verified"] = False
            except Exception as e:
                result["meta_api_status"] = "error"
                result["meta_api_error"] = str(e)
                result["meta_api_verified"] = False
        else:
            result["meta_api_verified"] = False
        
        # Cache the result
        self._last_health_check = current_time
        self._health_check_result = result
        
        return result
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics for monitoring."""
        return {
            "message_tracking_size": len(self._message_tracking),
            "request_tracking_size": len(self._request_tracking),
            "message_tracking_maxsize": self._message_tracking.maxsize,
            "request_tracking_maxsize": self._request_tracking.maxsize,
            "session_pool_connections": 10,
            "session_pool_maxsize": 20,
            "timeout_seconds": DEFAULT_TIMEOUT,
            "api_version": self.api_version
        }
    
    def clear_cache(self) -> Dict[str, Any]:
        """Clear tracking caches for debugging."""
        old_message_size = len(self._message_tracking)
        old_request_size = len(self._request_tracking)
        
        self._message_tracking.clear()
        self._request_tracking.clear()
        
        logger.info(f"Cleared caches: {old_message_size} messages, {old_request_size} requests")
        
        return {
            "cleared_messages": old_message_size,
            "cleared_requests": old_request_size
        }


# ==========================================================
# SINGLETON & COMPATIBILITY FUNCTIONS
# ==========================================================

_whatsapp_service = None


def get_whatsapp_service() -> WhatsAppService:
    """Get or create WhatsApp service singleton."""
    global _whatsapp_service
    if _whatsapp_service is None:
        _whatsapp_service = WhatsAppService()
    return _whatsapp_service


def send_text_message(
    phone_number: str, 
    message: str, 
    message_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Compatibility function for webhook.py
    
    Args:
        phone_number: Recipient's phone number
        message: Message text
        message_id: Optional message ID for reply context
        request_id: Optional request ID for tracing
    """
    service = get_whatsapp_service()
    return service.send_text_message(
        phone_number=phone_number, 
        message=message, 
        message_id=message_id,
        request_id=request_id
    )


def send_help_message(phone_number: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Send help message."""
    service = get_whatsapp_service()
    return service.send_help_message(phone_number, request_id=request_id)


def send_welcome_message(phone_number: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Send welcome message."""
    service = get_whatsapp_service()
    return service.send_welcome_message(phone_number, request_id=request_id)


def get_whatsapp_metrics() -> Dict[str, Any]:
    """Get WhatsApp service metrics."""
    service = get_whatsapp_service()
    return service.get_metrics()


def clear_whatsapp_cache() -> Dict[str, Any]:
    """Clear WhatsApp service caches."""
    service = get_whatsapp_service()
    return service.clear_cache()


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📱 WhatsApp Service v5.0 - Enterprise Grade")
logger.info(f"   API Version: {get_whatsapp_service().api_version}")
logger.info(f"   Configured: {bool(get_whatsapp_service().access_token and get_whatsapp_service().phone_number_id)}")
logger.info(f"   Cache Size: 10,000 messages (24h TTL)")
logger.info(f"   Timeout: {DEFAULT_TIMEOUT}s")
logger.info("   Features: Pakistan Phone Support | TTLCache | Request Tracing | Meta Health Check")
logger.info("=" * 60)
