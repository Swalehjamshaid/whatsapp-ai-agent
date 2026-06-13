import json
import time
import uuid
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse
from loguru import logger

from app.config import config
from app.database import SessionLocal
from app.services.ai_provider_service import process_whatsapp_query

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

@router.get("/")
async def verify_webhook(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        if hub_challenge:
            logger.success("Webhook verified!")
            return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Verification failed")

@router.post("/")
async def receive_message(request: Request):
    try:
        payload = await request.json()
        
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        if value.get("statuses"):
            return {"success": True}
        
        messages = value.get("messages", [])
        if not messages:
            return {"success": True}
        
        for msg in messages:
            phone = msg.get("from")
            msg_type = msg.get("type")
            
            if msg_type != "text":
                continue
            
            text = msg.get("text", {}).get("body", "")
            if not text:
                continue
            
            response = process_whatsapp_query(text, SessionLocal, phone)
            
            # TODO: Send response via WhatsApp API
            logger.info(f"Response to {phone}: {response[:100]}")
        
        return {"success": True}
        
    except Exception as e:
        logger.exception(f"Error: {e}")
        return {"success": False, "error": str(e)}

@router.get("/health")
async def health_check():
    return {"status": "healthy", "version": "1.0"}
