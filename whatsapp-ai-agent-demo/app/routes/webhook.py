"""
File: app/routes/webhook.py
WhatsApp webhook handler - Comprehensive version with full error handling
Handles both with and without trailing slash, status updates, and all message types
"""

import logging
import httpx
import os
import traceback
import json
from fastapi import APIRouter, Request, Response
from datetime import datetime

# Import the AI provider service
from app.services.ai_provider_service import process_whatsapp_query

logger = logging.getLogger(__name__)
router = APIRouter()

# WhatsApp API Configuration
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v18.0")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

# Rate limiting (optional)
REQUEST_COUNTS = {}
RATE_LIMIT_WINDOW = 60  # seconds
MAX_REQUESTS_PER_WINDOW = 100


def clean_phone_number(phone: str) -> str:
    """Clean phone number by removing any non-numeric characters"""
    if not phone:
        return phone
    return ''.join(filter(str.isdigit, phone))


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
    
    # Clean the phone number
    to = clean_phone_number(to)
    if not to:
        logger.error("❌ Invalid phone number")
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
    logger.debug(f"📤 Data: {json.dumps(data)}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=data)
            
            if response.status_code in [200, 201]:
                response_data = response.json()
                logger.info(f"✅ WhatsApp message sent successfully to {to}")
                logger.info(f"✅ Message ID: {response_data.get('messages', [{}])[0].get('id', 'N/A')}")
                return True
            else:
                logger.error(f"❌ Failed to send WhatsApp message: {response.status_code}")
                logger.error(f"❌ Response: {response.text}")
                
                # Handle specific error codes
                if response.status_code == 401:
                    logger.error("   → Invalid access token. Please regenerate your token.")
                elif response.status_code == 403:
                    logger.error("   → Forbidden. Check phone number ID and permissions.")
                elif response.status_code == 429:
                    logger.error("   → Rate limit exceeded. Please slow down.")
                elif response.status_code == 404:
                    logger.error("   → Not found. Check API version or phone number ID.")
                
                return False
    except httpx.TimeoutException:
        logger.error("❌ Timeout sending WhatsApp message")
        return False
    except Exception as e:
        logger.error(f"❌ Error sending WhatsApp message: {e}")
        logger.error(traceback.format_exc())
        return False


async def send_interactive_message(to: str, text: str, buttons: list) -> bool:
    """
    Send an interactive message with buttons to WhatsApp
    """
    if not WHATSAPP_PHONE_NUMBER_ID or not WHATSAPP_ACCESS_TOKEN:
        logger.error("❌ WhatsApp credentials not configured!")
        return False
    
    to = clean_phone_number(to)
    if not to:
        logger.error("❌ Invalid phone number")
        return False
    
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": text[:60]
            },
            "body": {
                "text": text
            },
            "action": {
                "buttons": buttons[:3]  # WhatsApp limits to 3 buttons
            }
        }
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=data)
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Interactive message sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send interactive: {response.status_code} - {response.text}")
                return False
    except Exception as e:
        logger.error(f"❌ Error sending interactive: {e}")
        return False


@router.get("/webhook")
@router.get("/webhook/")
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
        
        logger.info(f"🔍 Webhook verification requested")
        logger.info(f"   Mode: {mode}")
        logger.info(f"   Token: {token[:10] if token else 'None'}...")
        logger.info(f"   Challenge: {challenge[:20] if challenge else 'None'}...")
        
        if mode and token:
            if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
                logger.info("✅ Webhook verified successfully")
                return Response(content=challenge, status_code=200)
            else:
                logger.warning("❌ Webhook verification failed - invalid token")
                return Response(content="Verification failed", status_code=403)
        
        logger.warning("❌ Webhook verification failed - missing parameters")
        return Response(content="Missing parameters", status_code=400)
        
    except Exception as e:
        logger.error(f"Webhook verification error: {e}")
        logger.error(traceback.format_exc())
        return Response(content="Error", status_code=500)


@router.post("/webhook")
@router.post("/webhook/")
async def handle_webhook(request: Request):
    """
    Handle incoming WhatsApp messages
    This endpoint receives messages from WhatsApp and sends responses
    """
    try:
        # Parse the incoming webhook
        body = await request.json()
        logger.info(f"📨 Webhook received at {datetime.utcnow().isoformat()}")
        logger.debug(f"📨 Full body: {json.dumps(body)}")
        
        # Extract message details
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates (delivery receipts, read receipts, etc.)
        if value.get("statuses"):
            statuses = value.get("statuses", [])
            for status in statuses:
                status_id = status.get("id", "N/A")
                status_type = status.get("status", "N/A")
                recipient = status.get("recipient_id", "N/A")
                logger.info(f"📊 Status update: ID={status_id}, Status={status_type}, Recipient={recipient}")
            return {"status": "ok"}
        
        # Handle contact updates
        if value.get("contacts"):
            contacts = value.get("contacts", [])
            for contact in contacts:
                wa_id = contact.get("wa_id", "N/A")
                profile = contact.get("profile", {})
                name = profile.get("name", "N/A")
                logger.info(f"👤 Contact update: WA_ID={wa_id}, Name={name}")
        
        # Get messages
        messages = value.get("messages", [])
        
        if not messages:
            logger.info("No messages found in webhook")
            return {"status": "ok"}
        
        message_data = messages[0]
        sender = message_data.get("from")
        message_type = message_data.get("type")
        timestamp = message_data.get("timestamp")
        
        logger.info(f"📝 Message received at {datetime.fromtimestamp(int(timestamp)).isoformat() if timestamp else 'N/A'}")
        logger.info(f"📝 From: {sender}")
        logger.info(f"📝 Type: {message_type}")
        
        # Rate limiting check
        if sender:
            sender = clean_phone_number(sender)
            current_time = datetime.now().timestamp()
            if sender in REQUEST_COUNTS:
                count, last_time = REQUEST_COUNTS[sender]
                if current_time - last_time < RATE_LIMIT_WINDOW:
                    if count >= MAX_REQUESTS_PER_WINDOW:
                        logger.warning(f"⚠️ Rate limit exceeded for {sender}")
                        await send_whatsapp_message(sender, "⚠️ Too many requests. Please wait a moment.")
                        return {"status": "ok"}
                    REQUEST_COUNTS[sender] = (count + 1, last_time)
                else:
                    REQUEST_COUNTS[sender] = (1, current_time)
            else:
                REQUEST_COUNTS[sender] = (1, current_time)
        
        # Process based on message type
        if message_type == "text":
            text = message_data.get("text", {}).get("body", "")
            
            if not text:
                logger.info("Empty text message received")
                return {"status": "ok"}
            
            logger.info(f"📝 Processing: {text}")
            
            # Generate response using AI Provider Service
            try:
                # Call process_whatsapp_query - it returns the response string
                response = await process_whatsapp_query(text, sender)
                logger.info(f"📤 Response generated: {response[:100]}...")
                
                # Send the response back to WhatsApp
                if response and response.strip():
                    success = await send_whatsapp_message(sender, response)
                    if success:
                        logger.info(f"✅ Response sent to {sender}")
                    else:
                        logger.error(f"❌ Failed to send response to {sender}")
                        # Try to send error message
                        await send_whatsapp_message(sender, "⚠️ Failed to send response. Please try again.")
                else:
                    logger.warning("⚠️ Empty response generated")
                    await send_whatsapp_message(sender, "⚠️ I didn't understand that. Type *menu* for options.")
                    
            except Exception as e:
                logger.error(f"❌ Error processing query: {e}")
                logger.error(traceback.format_exc())
                # Send error message to user
                await send_whatsapp_message(sender, "⚠️ Service error. Please try again later.")
        
        elif message_type == "interactive":
            # Handle button/quick reply interactions
            interactive = message_data.get("interactive", {})
            interactive_type = interactive.get("type")
            
            if interactive_type == "button_reply":
                button_id = interactive.get("button_reply", {}).get("id")
                button_title = interactive.get("button_reply", {}).get("title")
                logger.info(f"🔘 Button clicked: {button_title} (ID: {button_id})")
                
                # Process the button click as a text message
                response = await process_whatsapp_query(button_title, sender)
                await send_whatsapp_message(sender, response)
            
            elif interactive_type == "list_reply":
                list_id = interactive.get("list_reply", {}).get("id")
                list_title = interactive.get("list_reply", {}).get("title")
                logger.info(f"📋 List selected: {list_title} (ID: {list_id})")
                
                # Process the list selection as a text message
                response = await process_whatsapp_query(list_title, sender)
                await send_whatsapp_message(sender, response)
        
        elif message_type == "image":
            logger.info("🖼️ Image received - processing not implemented")
            await send_whatsapp_message(sender, "📸 I received your image. Image analysis coming soon!")
        
        elif message_type == "audio":
            logger.info("🎵 Audio received - processing not implemented")
            await send_whatsapp_message(sender, "🎵 I received your audio. Voice processing coming soon!")
        
        elif message_type == "video":
            logger.info("🎬 Video received - processing not implemented")
            await send_whatsapp_message(sender, "🎬 I received your video. Video processing coming soon!")
        
        elif message_type == "document":
            logger.info("📄 Document received - processing not implemented")
            await send_whatsapp_message(sender, "📄 I received your document. Document processing coming soon!")
        
        elif message_type == "location":
            location = message_data.get("location", {})
            lat = location.get("latitude")
            lng = location.get("longitude")
            logger.info(f"📍 Location received: ({lat}, {lng})")
            await send_whatsapp_message(sender, f"📍 Location received: Latitude {lat}, Longitude {lng}")
        
        else:
            logger.info(f"Unsupported message type: {message_type}")
            await send_whatsapp_message(sender, f"⚠️ I don't support {message_type} messages yet.")
        
        # Always return 200 OK to WhatsApp
        return {"status": "ok"}
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ Invalid JSON in webhook: {e}")
        return {"status": "error", "message": "Invalid JSON"}, 400
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}, 500


@router.get("/webhook/health")
async def webhook_health_check():
    """
    Health check endpoint for the webhook
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "config": {
            "phone_number_id": bool(WHATSAPP_PHONE_NUMBER_ID),
            "access_token": bool(WHATSAPP_ACCESS_TOKEN),
            "verify_token": bool(WHATSAPP_VERIFY_TOKEN),
            "api_version": WHATSAPP_API_VERSION
        },
        "rate_limits": {
            "active_sessions": len(REQUEST_COUNTS)
        }
    }


# Clean up rate limit data periodically (optional)
async def cleanup_rate_limits():
    """Remove expired rate limit entries"""
    current_time = datetime.now().timestamp()
    expired = []
    for sender, (count, last_time) in REQUEST_COUNTS.items():
        if current_time - last_time > RATE_LIMIT_WINDOW * 2:
            expired.append(sender)
    for sender in expired:
        del REQUEST_COUNTS[sender]
    if expired:
        logger.info(f"🧹 Cleaned up {len(expired)} expired rate limit entries")
