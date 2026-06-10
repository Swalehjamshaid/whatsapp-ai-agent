# ==========================================================
# FILE: app/services/whatsapp_service.py (INTEGRATED v4.1)
# ==========================================================
# PURPOSE: WhatsApp Cloud API Communication Layer
# ==========================================================

import re
import json
import time
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum
from loguru import logger
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config import config


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
        self.api_version = "v18.0"
        self.base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        self.session = self._create_session()
        self._message_tracking = {}
        logger.info(f"WhatsApp Service v4.1 initialized")
    
    def _create_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3, backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
        session.mount("https://", adapter)
        return session
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def _clean_phone_number(self, phone_number: str) -> str:
        cleaned = re.sub(r'\D', '', phone_number)
        if len(cleaned) == 10:
            cleaned = '91' + cleaned
        return cleaned
    
    def send_text_message(self, phone_number: str, message: str, preview_url: bool = False, message_id: Optional[str] = None) -> Dict[str, Any]:
        if not self.access_token or not self.phone_number_id:
            return {"success": False, "error": "WhatsApp service not configured"}
        
        cleaned_number = self._clean_phone_number(phone_number)
        
        if len(message) > 4000:
            message = message[:3997] + "..."
        
        try:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": cleaned_number,
                "type": "text",
                "text": {"preview_url": preview_url, "body": message}
            }
            if message_id:
                payload["context"] = {"message_id": message_id}
            
            response = self.session.post(self.base_url, headers=self._get_headers(), json=payload, timeout=30)
            result = response.json()
            
            if response.status_code in [200, 201]:
                response_message_id = result.get("messages", [{}])[0].get("id")
                self._message_tracking[response_message_id] = {"to": cleaned_number, "status": "sent", "sent_at": datetime.utcnow().isoformat()}
                logger.success(f"✅ Message sent to {cleaned_number}")
                return {"success": True, "status_code": response.status_code, "message_id": response_message_id}
            
            error_msg = result.get("error", {}).get("message", "Unknown error")
            logger.error(f"❌ API Error: {response.status_code} - {error_msg}")
            return {"success": False, "status_code": response.status_code, "error": error_msg}
            
        except requests.Timeout:
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            logger.exception(f"Send failed: {e}")
            return {"success": False, "error": str(e)}
    
    def send_help_message(self, phone_number: str) -> Dict[str, Any]:
        """Send help message directly."""
        return self.send_text_message(phone_number, MessageTemplates.HELP)
    
    def send_welcome_message(self, phone_number: str) -> Dict[str, Any]:
        return self.send_text_message(phone_number, MessageTemplates.WELCOME)
    
    def health_check(self) -> Dict[str, Any]:
        return {
            "service": "whatsapp",
            "version": "4.1",
            "configured": bool(self.access_token and self.phone_number_id),
            "api_version": self.api_version
        }


# ==========================================================
# SINGLETON & COMPATIBILITY FUNCTIONS
# ==========================================================

_whatsapp_service = None

def get_whatsapp_service() -> WhatsAppService:
    global _whatsapp_service
    if _whatsapp_service is None:
        _whatsapp_service = WhatsAppService()
    return _whatsapp_service


def send_text_message(phone_number: str, message: str, message_id: Optional[str] = None) -> Dict[str, Any]:
    service = get_whatsapp_service()
    return service.send_text_message(phone_number, message, message_id=message_id)


def send_help_message(phone_number: str) -> Dict[str, Any]:
    service = get_whatsapp_service()
    return service.send_help_message(phone_number)


def send_welcome_message(phone_number: str) -> Dict[str, Any]:
    service = get_whatsapp_service()
    return service.send_welcome_message(phone_number)


logger.info("=" * 60)
logger.info("📱 WhatsApp Service v4.1 Loaded")
logger.info(f"   Configured: {bool(get_whatsapp_service().access_token)}")
logger.info("=" * 60)
