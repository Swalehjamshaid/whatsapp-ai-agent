# ==========================================================
# FILE: app/services/whatsapp_service.py (DEBUG v5.1)
# ==========================================================

import requests
import logging
import json
import time
from typing import Optional, Dict, Any, Union
from datetime import datetime
from loguru import logger

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_API_VERSION,
    WHATSAPP_API_URL
)

MAX_MESSAGE_LENGTH = 4000

def validate_phone_number(phone_number: str) -> bool:
    if not phone_number:
        return False
    cleaned = phone_number.lstrip('+')
    if not cleaned.isdigit():
        return False
    if len(cleaned) < 10 or len(cleaned) > 15:
        return False
    return True

def normalize_phone_number(phone_number: str) -> str:
    return phone_number.lstrip('+')

def get_whatsapp_url():
    api_version = WHATSAPP_API_VERSION or "v25.0"
    api_url = WHATSAPP_API_URL or "https://graph.facebook.com"
    return f"{api_url}/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

def send_text_message(phone_number: str, message: Union[str, Dict, Any]) -> Dict[str, Any]:
    """Send text message via WhatsApp Cloud API"""
    
    # ==========================================================
    # DEBUG: Log all configuration
    # ==========================================================
    logger.info("=" * 60)
    logger.info("🔍 WHATSAPP SEND DEBUG:")
    logger.info(f"   Phone: {phone_number}")
    logger.info(f"   Message length: {len(str(message)) if message else 0}")
    logger.info(f"   ACCESS_TOKEN: {'✓ SET' if WHATSAPP_ACCESS_TOKEN else '✗ MISSING'}")
    logger.info(f"   PHONE_ID: {'✓ SET' if WHATSAPP_PHONE_NUMBER_ID else '✗ MISSING'}")
    logger.info(f"   API_VERSION: {WHATSAPP_API_VERSION or 'default'}")
    logger.info("=" * 60)
    
    start_time = time.time()
    
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
    
    # Clean message
    if isinstance(message, dict):
        clean_message = message.get("response") or message.get("formatted_message") or str(message)
    else:
        clean_message = str(message)
    
    if not clean_message or len(clean_message.strip()) == 0:
        logger.error("Empty message")
        return {"success": False, "error": "Empty message"}
    
    # Validate phone
    if not validate_phone_number(phone_number):
        logger.error(f"Invalid phone number: {phone_number}")
        return {"success": False, "error": "Invalid phone number"}
    
    normalized_phone = normalize_phone_number(phone_number)
    
    # Truncate if needed
    if len(clean_message) > MAX_MESSAGE_LENGTH:
        clean_message = clean_message[:MAX_MESSAGE_LENGTH]
        logger.warning(f"Message truncated to {MAX_MESSAGE_LENGTH} chars")
    
    # ==========================================================
    # MAKE API CALL
    # ==========================================================
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
            "text": {"body": clean_message, "preview_url": False}
        }
        
        logger.info(f"📤 Making WhatsApp API call to: {url}")
        logger.info(f"   Phone: {normalized_phone}")
        logger.info(f"   Message preview: {clean_message[:100]}...")
        
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
                "processing_time_ms": processing_time
            }
        else:
            logger.error(f"❌ WhatsApp API failed: {response.status_code}")
            logger.error(f"   Response: {response.text[:500]}")
            
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text[:500],
                "processing_time_ms": processing_time
            }
            
    except requests.exceptions.Timeout as e:
        processing_time = (time.time() - start_time) * 1000
        logger.error(f"❌ WhatsApp API timeout: {e}")
        return {
            "success": False,
            "error": f"Timeout: {str(e)}",
            "processing_time_ms": processing_time
        }
        
    except Exception as e:
        processing_time = (time.time() - start_time) * 1000
        logger.exception(f"❌ WhatsApp API error: {e}")
        return {
            "success": False,
            "error": str(e),
            "processing_time_ms": processing_time
        }
