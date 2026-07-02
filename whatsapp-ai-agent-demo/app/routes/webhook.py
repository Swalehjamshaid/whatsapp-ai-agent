"""
File: app/routes/webhook.py
WhatsApp webhook handler - Handles receiving and sending messages
"""

import logging
import httpx
import os
import traceback
from fastapi import APIRouter, Request, Response

# Import the AI provider service
from app.services.ai_provider_service import process_whatsapp_query

logger = logging.getLogger(__name__)
router = APIRouter()

# WhatsApp API Configuration
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v18.0")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")


async def send_whatsapp_message(to: str, text: str) -> bool:
    """
    Send a message to WhatsApp Cloud API
    This is the ONLY place that sends messages to WhatsApp
    """
    if not WHATSAPP_PHONE_NUMBER_ID or not WHATSAPP_ACCESS_TOKEN:
        logger.error("❌ WhatsApp credentials not configured!")
        logger.error(f"  PHONE_NUMBER_ID: {'✅' if WHATSAPP_PHONE_NUMBER_ID else '❌'}")
        logger.error(f"  ACCESS_TOKEN: {'✅' if WHATSAPP_ACCESS_TOKEN else '❌'}")
        return False
    
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    
    logger.info(f"📤 Sending to WhatsApp API")
    logger.info(f"📤 Recipient: {to}")
    logger.info(f"📤 Message length: {len(text)} chars")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=data)
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ WhatsApp message sent successfully to {to}")
                logger.debug(f"✅ Response: {response.json()}")
                return True
            else:
                logger.error(f"❌ Failed to send WhatsApp message: {response.status_code}")
                logger.error(f"❌ Response: {response.text}")
                return False
    except httpx.TimeoutException:
        logger.error("❌ Timeout sending WhatsApp message")
        return False
    except Exception as e:
        logger.error(f"❌ Error sending WhatsApp message: {e}")
        logger.error(traceback.format_exc())
        return False


@router.get("/webhook")
async def verify_webhook(request: Request):
    """
    Verify webhook for WhatsApp
    This endpoint is called by Meta when setting up the webhook
    """
    try:
        params = dict(request.query_params)
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        
        logger.info(f"🔍 Webhook verification: mode={mode}, token={token[:5] if token else 'None'}...")
        
        if mode and token:
            if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
                logger.info("✅ Webhook verified successfully")
                return Response(content=challenge, status_code=200)
            else:
                logger.warning("❌ Webhook verification failed - invalid token")
                return Response(content="Verification failed", status_code=403)
        
        return Response(content="Missing parameters", status_code=400)
        
    except Exception as e:
        logger.error(f"Webhook verification error: {e}")
        return Response(content="Error", status_code=500)


@router.post("/webhook")
async def handle_webhook(request: Request):
    """
    Handle incoming WhatsApp messages
    This endpoint receives messages from WhatsApp and sends responses
    """
    try:
        body = await request.json()
        logger.info(f"📨 Webhook received")
        
        # Extract message details
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        # Handle status updates (delivery receipts, read receipts, etc.)
        if value.get("statuses"):
            logger.info(f"📊 Status update received")
            return {"status": "ok"}
        
        # No messages to process
        if not messages:
            logger.info("No messages found in webhook")
            return {"status": "ok"}
        
        message_data = messages[0]
        sender = message_data.get("from")
        message_type = message_data.get("type")
        
        logger.info(f"📝 Message from: {sender}, Type: {message_type}")
        
        # Only process text messages
        if message_type != "text":
            logger.info(f"Ignoring non-text message: {message_type}")
            return {"status": "ok"}
        
        text = message_data.get("text", {}).get("body", "")
        
        if not text:
            logger.info("Empty message received")
            return {"status": "ok"}
        
        logger.info(f"📝 Processing: {text}")
        
        # Generate response using AI Provider Service
        # The AI Provider Service now sends messages directly, so we don't send here
        try:
            # Call process_whatsapp_query - it will generate AND send the response
            response = await process_whatsapp_query(text, sender)
            logger.info(f"📤 Response generated and sent: {response[:100]}...")
            
        except Exception as e:
            logger.error(f"❌ Error processing query: {e}")
            logger.error(traceback.format_exc())
            # Send error message to user (fallback)
            await send_whatsapp_message(sender, "⚠️ Service error. Please try again later.")
        
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}, 500
