# ==========================================================
# FILE: app/services/whatsapp_service.py (ENTERPRISE v2.0)
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

logger = logging.getLogger(__name__)

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

def get_whatsapp_url():
    api_version = WHATSAPP_API_VERSION or "v25.0"
    api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
    return f"{api_url}/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

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

def send_text_message(phone_number: str, message: Union[str, Dict, Any]) -> Dict[str, Any]:
    clean_message = clean_message_for_whatsapp(message)
    
    if not validate_phone_number(phone_number):
        return {"success": False, "error": "Invalid phone number format"}
    
    normalized_phone = normalize_phone_number(phone_number)
    logger.info(f"Sending WhatsApp message to: {normalized_phone}")
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"success": True, "mode": "demo", "phone_number": normalized_phone, "message": clean_message}
    
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
        
        if response.status_code not in [200, 201]:
            return {"success": False, "status_code": response.status_code, "error": response.text[:200]}
        
        return {"success": True, "message_id": response.json().get("messages", [{}])[0].get("id")}
        
    except Exception as e:
        logger.error(f"WhatsApp send error: {e}")
        return {"success": False, "error": str(e)}


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

def send_structured_message(phone_number: str, message: str) -> Dict[str, Any]:
    return send_text_message(phone_number, message)
