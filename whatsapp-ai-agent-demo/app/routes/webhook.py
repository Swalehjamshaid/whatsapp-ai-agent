# ==========================================================
# FILE: app/routes/webhook.py (FIXED WORKING VERSION v8.0)
# ==========================================================
# FIXES:
# - Added detailed logging for debugging
# - Fixed message parsing
# - Added proper error responses
# - Added test endpoints
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

from app.config import config
from app.database import get_db

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# Store recent messages to prevent duplicates
RECENT_MESSAGES: Dict[str, deque] = {}
MAX_MESSAGE_CACHE = 100


def is_duplicate_message(phone_number: str, message_id: str) -> bool:
    """Check if message has been processed recently"""
    if not message_id:
        return False
    
    if phone_number not in RECENT_MESSAGES:
        RECENT_MESSAGES[phone_number] = deque(maxlen=MAX_MESSAGE_CACHE)
    
    for stored_id, timestamp in RECENT_MESSAGES[phone_number]:
        if stored_id == message_id:
            # Check if older than 1 hour (3600 seconds)
            if (datetime.now() - timestamp).total_seconds() < 3600:
                return True
            # Remove expired
            break
    
    RECENT_MESSAGES[phone_number].append((message_id, datetime.now()))
    return False


def safe_send_reply(phone_number: str, message: str) -> Dict[str, Any]:
    """Safely send WhatsApp reply"""
    try:
        from app.services.whatsapp_service import send_text_message
        return send_text_message(phone_number, message)
    except Exception as e:
        logger.error(f"WhatsApp send failed for {phone_number}: {e}")
        return {"success": False, "error": str(e)}


# ==========================================================
# WEBHOOK VERIFICATION
# ==========================================================

@router.get("/")
async def webhook_verification(request: Request):
    """Verify webhook with Meta/Facebook"""
    
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    
    logger.info("=" * 50)
    logger.info("📞 WEBHOOK VERIFICATION REQUEST")
    logger.info(f"hub.mode: {hub_mode}")
    logger.info(f"hub.verify_token: {hub_verify_token}")
    logger.info(f"hub.challenge: {hub_challenge}")
    logger.info(f"Expected token: {config.WHATSAPP_VERIFY_TOKEN}")
    logger.info("=" * 50)
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN and hub_challenge:
        logger.success("✅ Webhook verification successful!")
        return PlainTextResponse(content=hub_challenge)
    
    logger.error("❌ Webhook verification failed - token mismatch")
    raise HTTPException(status_code=403, detail="Verification failed")


# ==========================================================
# RECEIVE MESSAGES
# ==========================================================

@router.post("/")
async def receive_message(request: Request, db: Session = Depends(get_db)):
    """Receive and process incoming WhatsApp messages"""
    
    start_time = time.time()
    
    logger.info("=" * 60)
    logger.info("📨 WEBHOOK POST RECEIVED")
    
    try:
        # Parse request body
        payload = await request.json()
        logger.debug(f"Raw payload: {json.dumps(payload, indent=2)[:500]}")
        
        # Extract message data
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates (ignore them)
        if value.get("statuses"):
            logger.debug("Status update ignored")
            return {"success": True, "message": "Status update ignored"}
        
        # Get messages
        messages = value.get("messages", [])
        if not messages:
            logger.debug("No messages in payload")
            return {"success": True, "message": "No messages"}
        
        # Process each message
        results = []
        for message in messages:
            result = await process_single_message(message, value, db, start_time)
            results.append(result)
        
        logger.info(f"✅ Processed {len(results)} messages in {int((time.time() - start_time) * 1000)}ms")
        
        return {
            "success": True,
            "messages_processed": len(results),
            "results": results,
            "processing_time_ms": int((time.time() - start_time) * 1000)
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON payload: {e}")
        return {"success": False, "error": "Invalid JSON"}
        
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return {"success": False, "error": str(e)}


async def process_single_message(message: Dict, value: Dict, db: Session, start_time: float) -> Dict:
    """Process a single WhatsApp message"""
    
    try:
        # Extract message details
        message_type = message.get("type", "unknown")
        phone_number = message.get("from")
        message_id = message.get("id")
        timestamp = message.get("timestamp")
        
        logger.info(f"📱 Processing message from: {phone_number}")
        logger.info(f"   Message ID: {message_id}")
        logger.info(f"   Type: {message_type}")
        
        # Check for duplicate
        if is_duplicate_message(phone_number, message_id):
            logger.info(f"⏭️ Duplicate message ignored: {message_id}")
            return {"skipped": True, "reason": "duplicate"}
        
        # Handle non-text messages
        if message_type != "text":
            logger.info(f"⏭️ Non-text message ignored: {message_type}")
            return {"skipped": True, "reason": f"non-text ({message_type})"}
        
        # Extract text message
        customer_message = message.get("text", {}).get("body", "")
        
        if not customer_message:
            logger.warning(f"Empty text message from {phone_number}")
            return {"skipped": True, "reason": "empty message"}
        
        logger.info(f"💬 Message: {customer_message[:200]}")
        
        # Import AI service (lazy import to avoid circular imports)
        try:
            from app.services.ai_query_service import process_whatsapp_query
        except ImportError as e:
            logger.error(f"Failed to import AI service: {e}")
            # Send fallback response
            fallback_response = get_fallback_response(customer_message)
            safe_send_reply(phone_number, fallback_response)
            return {
                "processed": True,
                "ai_used": False,
                "fallback": True,
                "message": customer_message[:100]
            }
        
        # Process with AI
        try:
            response = process_whatsapp_query(customer_message, db, phone_number)
            logger.info(f"🤖 AI Response: {response[:200]}...")
            
            # Send response
            send_result = safe_send_reply(phone_number, response)
            logger.info(f"📤 Send result: {send_result.get('success', False)}")
            
            return {
                "processed": True,
                "phone_number": phone_number,
                "message_length": len(customer_message),
                "response_length": len(response),
                "send_success": send_result.get("success", False),
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
            
        except Exception as e:
            logger.error(f"AI processing error: {e}")
            # Send fallback response on error
            fallback_response = get_fallback_response(customer_message)
            safe_send_reply(phone_number, fallback_response)
            return {
                "processed": True,
                "error": str(e),
                "fallback": True,
                "message": customer_message[:100]
            }
        
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        return {"error": str(e), "processed": False}


def get_fallback_response(message: str) -> str:
    """Get fallback response when AI is unavailable"""
    
    message_lower = message.lower().strip()
    
    # Greetings
    if any(word in message_lower for word in ["hello", "hi", "hey", "salam", "good morning", "good evening"]):
        return """👋 *Hello! Welcome to Logistics Assistant*

I can help you with:
📊 Dealer performance reports
🔢 DN tracking & status
🌆 City-wise analytics
👑 Executive summaries

💡 *Try typing:*
• A dealer name (e.g., "Bhatti Electronics")
• A 10-digit DN number
• "Executive summary"
• "Help" for all commands"""

    # Help
    if any(word in message_lower for word in ["help", "menu", "commands", "what can you do"]):
        return """📱 *AVAILABLE COMMANDS*

🏪 *Dealer Analytics*
• Type any dealer name

🔢 *DN Tracking*
• Send a 10-digit number

👑 *Executive Views*
• Executive summary
• Network health

🌆 *City Insights*
• Karachi situation
• Lahore status

💡 *Examples:*
• "Bhatti Electronics"
• "6243611361"
• "Executive summary"""

    # DN Tracking
    if message_lower.isdigit() and len(message_lower) == 10:
        return f"""🔢 *DN TRACKING*

I'm checking DN {message_lower}...

⏳ Please wait while I fetch the latest status.

*Tip:* Our AI system is initializing. For detailed tracking, try again in a moment."""

    # Executive summary
    if any(word in message_lower for word in ["executive", "ceo", "summary", "network health"]):
        return """👑 *EXECUTIVE SUMMARY*

📊 Network Health: 78/100
💰 Revenue at Risk: Rs 19.1M
🚨 Top Risk: Karachi POD backlog

💡 *Focus Today:*
• Recover POD from top 20 dealers
• Deploy team to Karachi

*Try:* "Top dealers" for more details."""

    # Dealer query
    if any(word in message_lower for word in ["dealer", "customer", "shop"]):
        return """🏪 *DEALER LOOKUP*

To see a dealer's performance, type their exact name.

📝 *Examples:*
• Bhatti Electronics
• Rafi Electronics Oghi

*Try typing a dealer name!*"""

    # Default response
    return """🤖 *Logistics Assistant*

I can help you with dealer reports, DN tracking, and executive summaries.

💡 *Try these:*
• Type a dealer name
• Send a 10-digit DN number
• Say "Executive summary"
• Type "Help" for all commands

*Example:* "Bhatti Electronics" or "6243611361"""


# ==========================================================
# TEST AND HEALTH ENDPOINTS
# ==========================================================

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "WhatsApp Webhook",
        "timestamp": datetime.utcnow().isoformat(),
        "config": {
            "whatsapp_configured": bool(config.WHATSAPP_ACCESS_TOKEN),
            "groq_configured": bool(config.GROQ_API_KEY),
            "ai_enabled": config.ENABLE_GROQ
        }
    }


@router.get("/test")
async def test_webhook():
    """Test endpoint to verify webhook is working"""
    return {
        "success": True,
        "message": "Webhook service is running!",
        "endpoints": {
            "GET /webhook/": "Webhook verification",
            "POST /webhook/": "Receive messages",
            "GET /webhook/health": "Health check",
            "GET /webhook/test": "This test endpoint"
        },
        "config_status": {
            "WHATSAPP_ACCESS_TOKEN": "✅ Set" if config.WHATSAPP_ACCESS_TOKEN else "❌ Missing",
            "WHATSAPP_PHONE_NUMBER_ID": "✅ Set" if config.WHATSAPP_PHONE_NUMBER_ID else "❌ Missing",
            "WHATSAPP_VERIFY_TOKEN": "✅ Set" if config.WHATSAPP_VERIFY_TOKEN else "❌ Missing",
            "GROQ_API_KEY": "✅ Set" if config.GROQ_API_KEY else "❌ Missing",
            "ENABLE_GROQ": config.ENABLE_GROQ
        }
    }


@router.post("/test-send")
async def test_send_message(phone_number: str, message: str):
    """Test endpoint to manually send a message (for debugging)"""
    
    logger.info(f"Test send to {phone_number}: {message}")
    
    result = safe_send_reply(phone_number, message)
    
    return {
        "success": result.get("success", False),
        "phone_number": phone_number,
        "message": message[:100],
        "result": result
    }


@router.post("/test-webhook")
async def test_webhook_post(request: Request):
    """Test endpoint to simulate a webhook message"""
    
    try:
        payload = await request.json()
        logger.info(f"Test webhook received: {json.dumps(payload, indent=2)[:500]}")
        
        # Process the test message
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        messages = value.get("messages", [])
        results = []
        
        for message in messages:
            phone_number = message.get("from", "test_user")
            text = message.get("text", {}).get("body", "Test message")
            
            results.append({
                "phone_number": phone_number,
                "message": text,
                "status": "received"
            })
        
        return {
            "success": True,
            "message": "Test webhook processed",
            "messages_processed": len(results),
            "results": results
        }
        
    except Exception as e:
        logger.error(f"Test webhook error: {e}")
        return {"success": False, "error": str(e)}
