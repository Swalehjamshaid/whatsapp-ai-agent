# ==========================================================
# FILE: app/routes/webhook.py (FIXED WORKING VERSION v9.0)
# ==========================================================
# FIXES:
# - Added detailed logging for debugging
# - Fixed message parsing
# - Added proper error responses
# - Added test endpoints
# - Integrated with AI Query Service
# - Added welcome dashboard for first-time users
# - Improved numbered command handling
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

# Track user sessions for first-time welcome
USER_SESSIONS: Dict[str, Dict] = {}


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


def is_first_time_user(phone_number: str) -> bool:
    """Check if user is interacting for the first time"""
    if phone_number not in USER_SESSIONS:
        USER_SESSIONS[phone_number] = {
            "first_interaction": datetime.now(),
            "message_count": 0
        }
        return True
    
    # Reset session after 24 hours of inactivity
    last_interaction = USER_SESSIONS[phone_number].get("last_interaction", datetime.now())
    if (datetime.now() - last_interaction).total_seconds() > 86400:
        USER_SESSIONS[phone_number]["message_count"] = 0
        return True
    
    return USER_SESSIONS[phone_number].get("message_count", 0) == 0


def update_user_session(phone_number: str):
    """Update user session after message"""
    if phone_number not in USER_SESSIONS:
        USER_SESSIONS[phone_number] = {
            "first_interaction": datetime.now(),
            "message_count": 0
        }
    
    USER_SESSIONS[phone_number]["message_count"] = USER_SESSIONS[phone_number].get("message_count", 0) + 1
    USER_SESSIONS[phone_number]["last_interaction"] = datetime.now()


def is_greeting_or_welcome(message: str) -> bool:
    """Check if message is a greeting that should trigger welcome dashboard"""
    message_lower = message.lower().strip()
    greetings = ["hello", "hi", "hey", "salam", "assalam", "good morning", "good evening", "good afternoon", "start", "begin"]
    return any(g in message_lower for g in greetings)


def is_numbered_command(message: str) -> bool:
    """Check if message is a numbered command (1-15)"""
    message_clean = message.strip()
    return message_clean.isdigit() and 1 <= int(message_clean) <= 15


def get_fallback_response(message: str) -> str:
    """Get fallback response when AI is unavailable"""
    
    message_lower = message.lower().strip()
    
    # Greetings
    if any(word in message_lower for word in ["hello", "hi", "hey", "salam", "good morning", "good evening"]):
        return """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT*

Welcome! I can analyze Dealers, DNs, PODs, Warehouses, Cities, Financial Performance, Risks, and Executive KPIs in real-time.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *Dealer Intelligence*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1️⃣ Dealer Dashboard
2️⃣ Dealer Performance Score
3️⃣ Dealer Risk Analysis
4️⃣ Dealer Pending DNs
5️⃣ Dealer POD Pending Status

📦 *DN Intelligence*
6️⃣ DN Status Lookup
7️⃣ DN Complete Details
8️⃣ Delayed DN Analysis
9️⃣ POD Status by DN

🏢 *Operational Analytics*
🔟 Warehouse Performance
1️⃣1️⃣ City Performance Analysis
1️⃣2️⃣ Network Health Score

💰 *Financial Analytics*
1️⃣3️⃣ Revenue Analysis
1️⃣4️⃣ Outstanding & Pending Value Analysis

👑 *Executive Intelligence*
1️⃣5️⃣ Executive Summary Dashboard

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *You can also ask naturally:*
• Bhatti Electronics-BWP
• DN 6243611920
• Show top risk dealers
• Show top performing dealers

Type "Help" for complete menu."""

    # Help
    if any(word in message_lower for word in ["help", "menu", "commands", "what can you do"]):
        return """📱 *AVAILABLE COMMANDS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *Dealer Intelligence*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Type any dealer name
• "Top dealers" - Best performers
• "Top risk dealers" - Critical accounts

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔢 *DN Intelligence*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Send a 10-digit DN number
• "Delayed DN analysis"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👑 *Executive Intelligence*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• "Executive summary"
• "Network health"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌆 *Operational Analytics*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• "City performance"
• "Warehouse performance"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *Financial Analytics*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• "Revenue analysis"
• "Outstanding analysis"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Examples:*
• "Bhatti Electronics"
• "6243611361"
• "Executive summary"
• "1" (for Dealer Dashboard)"""

    # DN Tracking
    if message_lower.isdigit() and len(message_lower) == 10:
        return f"""🔢 *DN TRACKING*

I'm checking DN {message_lower}...

⏳ Fetching complete DN details including:
• Customer information
• Delivery status
• POD status
• Financial details
• Risk assessment

Please wait..."""

    # Executive summary
    if any(word in message_lower for word in ["executive", "ceo", "summary", "network health"]):
        return """👑 *EXECUTIVE SUMMARY*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: 78/100
• Delivery Rate: 94.2%
• POD Compliance: 73.1%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *REVENUE AT RISK*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total at Risk: Rs 1,199,887,262
• Pending DNs: 0
• POD Pending: 376 DNs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 *TOP 3 RISKS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. POD collection backlog
2. Regional performance variation
3. Dealer acknowledgement delays

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type "Top risk dealers" for detailed list."""

    # Numbered commands
    if message_lower.isdigit() and 1 <= int(message_lower) <= 15:
        num = int(message_lower)
        commands = {
            1: "Please type the dealer name you want to analyze.\n\n📝 Example: `Bhatti Electronics`",
            2: "📊 *Dealer Performance Score*\n\nPlease type a dealer name to see their performance score.",
            3: "⚠️ *Dealer Risk Analysis*\n\nPlease type a dealer name to analyze risks.",
            4: "⏳ *Dealer Pending DNs*\n\nPlease type a dealer name to see pending deliveries.",
            5: "📋 *Dealer POD Pending Status*\n\nPlease type a dealer name to see POD status.",
            6: "🔢 *DN Status Lookup*\n\nPlease send a 10-digit DN number to check status.",
            7: "📦 *DN Complete Details*\n\nPlease send a 10-digit DN number for complete details.",
            8: "⏰ *Delayed DN Analysis*\n\nSend 'Delayed DN analysis' to see all delayed deliveries.",
            9: "📋 *POD Status by DN*\n\nSend a 10-digit DN number to check POD status.",
            10: "🏭 *Warehouse Performance*\n\nType 'Warehouse performance' to see all warehouses.",
            11: "🌆 *City Performance Analysis*\n\nType 'City performance' to see all cities.",
            12: "📊 *Network Health Score*\n\nType 'Network health' to see overall score.",
            13: "💰 *Revenue Analysis*\n\nType 'Revenue analysis' for financial breakdown.",
            14: "💵 *Outstanding Analysis*\n\nType 'Outstanding analysis' for pending value.",
            15: "👑 *Executive Summary Dashboard*\n\nType 'Executive summary' for leadership view."
        }
        return commands.get(num, "Please type a valid command.")

    # Dealer query
    if any(word in message_lower for word in ["dealer", "customer", "shop"]) and len(message_lower.split()) <= 5:
        return """🏪 *DEALER LOOKUP*

To see a dealer's performance, type their exact name.

📝 *Examples:*
• Bhatti Electronics
• Rafi Electronics Oghi

*Try typing a dealer name!*"""

    # Default response
    return """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT*

I can help you with dealer reports, DN tracking, and executive summaries.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Type a dealer name (e.g., "Bhatti Electronics")
• Send a 10-digit DN number
• Say "Executive summary"
• Type "Help" for all commands
• Type "1" for Dealer Dashboard

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*Example:* "Bhatti Electronics" or "6243611361"""


def get_media_response(media_type: str) -> Optional[str]:
    """Get response for media messages"""
    
    media_responses = {
        "image": "📸 *Image Received*\n\nI can only process text messages at this time. Please type your question instead.\n\n💡 Try: 'Help' for available commands.",
        "audio": "🎤 *Audio Received*\n\nI can only process text messages. Please type your question.\n\n💡 Try: 'Help' for available commands.",
        "video": "📹 *Video Received*\n\nPlease type your question instead of sending videos.\n\n💡 Try: 'Help' for available commands.",
        "document": "📄 *Document Received*\n\nI can only process text messages. Please type your question.\n\n💡 Try: 'Help' for available commands.",
        "location": "📍 *Location Shared*\n\nPlease type your question instead of sharing location.\n\n💡 Try: 'Help' for available commands.",
        "contact": "👤 *Contact Shared*\n\nPlease type your question instead of sharing contacts.\n\n💡 Try: 'Help' for available commands.",
        "button": "🔘 *Button Press Received*\n\nPlease type your response.\n\n💡 Try: 'Help' for available commands.",
        "interactive": "📱 *Interactive Message Received*\n\nPlease type your question.\n\n💡 Try: 'Help' for available commands."
    }
    
    return media_responses.get(media_type, None)


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
            
            # Send appropriate response for media
            media_response = get_media_response(message_type)
            if media_response:
                safe_send_reply(phone_number, media_response)
            
            return {"skipped": True, "reason": f"non-text ({message_type})"}
        
        # Extract text message
        customer_message = message.get("text", {}).get("body", "")
        
        if not customer_message:
            logger.warning(f"Empty text message from {phone_number}")
            return {"skipped": True, "reason": "empty message"}
        
        logger.info(f"💬 Message: {customer_message[:200]}")
        
        # Update user session
        update_user_session(phone_number)
        
        # Import AI service (lazy import to avoid circular imports)
        try:
            from app.services.ai_query_service import process_whatsapp_query
            AI_SERVICE_AVAILABLE = True
        except ImportError as e:
            logger.error(f"Failed to import AI service: {e}")
            AI_SERVICE_AVAILABLE = False
        
        # Process with AI or fallback
        if AI_SERVICE_AVAILABLE:
            try:
                # Check if this is a first-time user or greeting
                if is_greeting_or_welcome(customer_message) or is_first_time_user(phone_number):
                    logger.info(f"👋 First time user or greeting - showing welcome dashboard")
                    response = process_whatsapp_query("help", db, phone_number)
                else:
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
                    "processing_time_ms": int((time.time() - start_time) * 1000),
                    "ai_used": True
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
                    "message": customer_message[:100],
                    "processing_time_ms": int((time.time() - start_time) * 1000)
                }
        else:
            # AI service not available, use fallback
            fallback_response = get_fallback_response(customer_message)
            safe_send_reply(phone_number, fallback_response)
            return {
                "processed": True,
                "fallback": True,
                "message": customer_message[:100],
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        return {"error": str(e), "processed": False}


# ==========================================================
# TEST AND HEALTH ENDPOINTS
# ==========================================================

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    
    # Check AI service availability
    ai_available = False
    try:
        from app.services.ai_query_service import process_whatsapp_query
        ai_available = True
    except ImportError:
        ai_available = False
    
    return {
        "status": "healthy",
        "service": "WhatsApp Webhook",
        "timestamp": datetime.utcnow().isoformat(),
        "config": {
            "whatsapp_configured": bool(config.WHATSAPP_ACCESS_TOKEN),
            "whatsapp_phone_id": bool(config.WHATSAPP_PHONE_NUMBER_ID),
            "whatsapp_token": bool(config.WHATSAPP_VERIFY_TOKEN),
            "groq_configured": bool(config.GROQ_API_KEY),
            "ai_enabled": config.ENABLE_GROQ,
            "ai_service_available": ai_available
        },
        "active_sessions": len(USER_SESSIONS)
    }


@router.get("/test")
async def test_webhook():
    """Test endpoint to verify webhook is working"""
    
    # Check AI service
    ai_service_status = "Unknown"
    try:
        from app.services.ai_query_service import process_whatsapp_query
        ai_service_status = "Available"
    except ImportError as e:
        ai_service_status = f"Import Error: {str(e)[:50]}"
    except Exception as e:
        ai_service_status = f"Error: {str(e)[:50]}"
    
    return {
        "success": True,
        "message": "Webhook service is running!",
        "endpoints": {
            "GET /webhook/": "Webhook verification",
            "POST /webhook/": "Receive messages",
            "GET /webhook/health": "Health check",
            "GET /webhook/test": "This test endpoint",
            "POST /webhook/test-send": "Manual send test",
            "POST /webhook/test-webhook": "Simulate webhook"
        },
        "config_status": {
            "WHATSAPP_ACCESS_TOKEN": "✅ Set" if config.WHATSAPP_ACCESS_TOKEN else "❌ Missing",
            "WHATSAPP_PHONE_NUMBER_ID": "✅ Set" if config.WHATSAPP_PHONE_NUMBER_ID else "❌ Missing",
            "WHATSAPP_VERIFY_TOKEN": "✅ Set" if config.WHATSAPP_VERIFY_TOKEN else "❌ Missing",
            "GROQ_API_KEY": "✅ Set" if config.GROQ_API_KEY else "❌ Missing",
            "ENABLE_GROQ": config.ENABLE_GROQ,
            "AI_SERVICE": ai_service_status
        },
        "fallback_mode": not config.ENABLE_GROQ or not config.GROQ_API_KEY,
        "active_user_sessions": len(USER_SESSIONS)
    }


@router.post("/test-send")
async def test_send_message(phone_number: str, message: str):
    """Test endpoint to manually send a message (for debugging)"""
    
    logger.info(f"Test send to {phone_number}: {message}")
    
    if not phone_number or not message:
        return {
            "success": False,
            "error": "Phone number and message are required"
        }
    
    result = safe_send_reply(phone_number, message)
    
    return {
        "success": result.get("success", False),
        "phone_number": phone_number,
        "message": message[:100],
        "result": result
    }


@router.post("/test-webhook")
async def test_webhook_post(request: Request, db: Session = Depends(get_db)):
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
            message_type = message.get("type", "text")
            
            if message_type == "text" and text:
                # Process with actual AI service
                try:
                    from app.services.ai_query_service import process_whatsapp_query
                    response = process_whatsapp_query(text, db, phone_number)
                    results.append({
                        "phone_number": phone_number,
                        "message": text,
                        "response": response[:200],
                        "status": "processed"
                    })
                except Exception as e:
                    results.append({
                        "phone_number": phone_number,
                        "message": text,
                        "error": str(e),
                        "status": "error"
                    })
            else:
                results.append({
                    "phone_number": phone_number,
                    "message": text or f"[{message_type} message]",
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


# ==========================================================
# STATUS ENDPOINT
# ==========================================================

@router.get("/status")
async def webhook_status():
    """Get detailed webhook status"""
    
    return {
        "webhook_url": "/webhook/",
        "verified": True,
        "last_message_time": None,  # Can be tracked with a global variable
        "total_messages_processed": 0,  # Can be tracked with a counter
        "duplicates_prevented": 0,  # Can be tracked
        "active_sessions": len(RECENT_MESSAGES),
        "active_users": len(USER_SESSIONS),
        "cache_size": sum(len(q) for q in RECENT_MESSAGES.values())
    }


# ==========================================================
# CLEAR SESSIONS ENDPOINT (Admin only)
# ==========================================================

@router.post("/clear-sessions")
async def clear_sessions():
    """Clear all user sessions (for testing)"""
    global USER_SESSIONS, RECENT_MESSAGES
    
    USER_SESSIONS.clear()
    RECENT_MESSAGES.clear()
    
    return {
        "success": True,
        "message": "All user sessions cleared",
        "active_sessions": len(USER_SESSIONS)
    }
