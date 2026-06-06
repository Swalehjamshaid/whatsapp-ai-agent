# ==========================================================
# FILE: app/routes/webhook.py (PRODUCTION READY v7.0)
# ==========================================================

import json
import time
import re
from datetime import datetime
from typing import Dict, Any, Optional, List
from collections import deque

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from loguru import logger

from app.config import WHATSAPP_VERIFY_TOKEN
from app.services.whatsapp_service import send_text_message, parse_whatsapp_message
from app.database import get_db

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

RECENT_MESSAGES: Dict[str, deque] = {}
MAX_MESSAGE_CACHE = 100

def is_duplicate_message(phone_number: str, message_id: str) -> bool:
    if phone_number not in RECENT_MESSAGES:
        RECENT_MESSAGES[phone_number] = deque(maxlen=MAX_MESSAGE_CACHE)
    
    for stored_id, timestamp in RECENT_MESSAGES[phone_number]:
        if stored_id == message_id:
            return True
    
    RECENT_MESSAGES[phone_number].append((message_id, datetime.now()))
    return False

def safe_send_reply(phone_number: str, message: str) -> Dict[str, Any]:
    try:
        return send_text_message(phone_number, message)
    except Exception as e:
        logger.error(f"WhatsApp send failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/")
async def webhook_verification(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")

    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN and hub_challenge:
        return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/")
async def receive_message(request: Request, db: Session = Depends(get_db)):
    start_time = time.time()
    
    try:
        payload = await request.json()
        
        # Parse message
        parsed_messages = parse_whatsapp_message(payload)
        
        if not parsed_messages:
            return {"success": True, "message": "No valid messages"}
        
        for parsed in parsed_messages:
            if parsed.get("type") != "text":
                continue
            
            phone_number = parsed.get("from_phone")
            customer_message = parsed.get("text", "")
            message_id = parsed.get("message_id")
            
            if is_duplicate_message(phone_number, message_id):
                logger.info(f"Duplicate ignored: {message_id}")
                continue
            
            logger.info(f"📱 WhatsApp from {phone_number}: {customer_message[:100]}")
            
            # Simple response for now - route to AI service
            from app.services.ai_query_service import process_whatsapp_query
            response = process_whatsapp_query(customer_message, db, phone_number)
            
            safe_send_reply(phone_number, response)
        
        return {"success": True, "processing_time_ms": int((time.time() - start_time) * 1000)}
        
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return {"success": False, "error": str(e)}


@router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}
