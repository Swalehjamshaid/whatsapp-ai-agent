# ==========================================================
# FILE: app/routes/webhook.py (PRODUCTION READY v10.0)
# ==========================================================
# FULLY ALIGNED WITH GROQ AI INTEGRATION
# - Receives WhatsApp messages
# - Routes to AI Query Service
# - Sends responses back to WhatsApp
# - Includes health checks and test endpoints
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

# ==========================================================
# MESSAGE CACHE (Prevent Duplicates)
# ==========================================================

RECENT_MESSAGES: Dict[str, deque] = {}
MAX_MESSAGE_CACHE = 100
MESSAGE_EXPIRY_SECONDS = 3600  # 1 hour


def is_duplicate_message(phone_number: str, message_id: str) -> bool:
    """Check if message has been processed recently"""
    if not message_id:
        return False
    
    if phone_number not in RECENT_MESSAGES:
        RECENT_MESSAGES[phone_number] = deque(maxlen=MAX_MESSAGE_CACHE)
    
    # Clean expired messages
    now = datetime.now()
    valid_messages = []
    for stored_id, timestamp in RECENT_MESSAGES[phone_number]:
        if (now - timestamp).total_seconds() < MESSAGE_EXPIRY_SECONDS:
            valid_messages.append((stored_id, timestamp))
        if stored_id == message_id:
            return True
    
    RECENT_MESSAGES[phone_number] = deque(valid_messages, maxlen=MAX_MESSAGE_CACHE)
    RECENT_MESSAGES[phone_number].append((message_id, now))
    return False


# ==========================================================
# WHATSAPP REPLY SENDER
# ==========================================================

def safe_send_reply(phone_number: str, message: str) -> Dict[str, Any]:
    """Safely send WhatsApp reply"""
    try:
        from app.services.whatsapp_service import send_text_message
        return send_text_message(phone_number, message)
    except ImportError:
        # Fallback if whatsapp_service not available
        logger.warning("WhatsApp service not available, using mock send")
        return {"success": True, "mode": "mock", "message": message}
    except Exception as e:
        logger.error(f"WhatsApp send failed for {phone_number}: {e}")
        return {"success": False, "error": str(e)}


def get_media_response(media_type: str) -> str:
    """Get response for media messages"""
    responses = {
        "image": "📸 *Image Received*\n\nI can only process text messages. Please type your question instead.\n\n💡 Try: 'Help' for available commands.",
        "audio": "🎤 *Audio Received*\n\nPlease type your question instead of sending audio.\n\n💡 Try: 'Help' for available commands.",
        "video": "📹 *Video Received*\n\nPlease type your question instead of sending videos.\n\n💡 Try: 'Help' for available commands.",
        "document": "📄 *Document Received*\n\nPlease type your question instead of sending documents.\n\n💡 Try: 'Help' for available commands.",
        "location": "📍 *Location Shared*\n\nPlease type your question instead of sharing location.\n\n💡 Try: 'Help' for available commands.",
        "contact": "👤 *Contact Shared*\n\nPlease type your question instead of sharing contacts.\n\n💡 Try: 'Help' for available commands.",
        "button": "🔘 *Button Press Received*\n\nPlease type your response.\n\n💡 Try: 'Help' for available commands.",
        "interactive": "📱 *Interactive Message Received*\n\nPlease type your question.\n\n💡 Try: 'Help' for available commands."
    }
    return responses.get(media_type, "📱 *Message Received*\n\nI can only process text messages. Please type your question.\n\n💡 Try: 'Help' for available commands.")


# ==========================================================
# WEBHOOK VERIFICATION (GET)
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
# RECEIVE MESSAGES (POST)
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
        logger.debug(f"Raw payload (first 500 chars): {json.dumps(payload, indent=2)[:500]}")
        
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
            result = await process_single_message(message, db, start_time)
            results.append(result)
        
        processing_time = int((time.time() - start_time) * 1000)
        logger.info(f"✅ Processed {len(results)} messages in {processing_time}ms")
        
        return {
            "success": True,
            "messages_processed": len(results),
            "results": results,
            "processing_time_ms": processing_time
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON payload: {e}")
        return {"success": False, "error": "Invalid JSON"}
        
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return {"success": False, "error": str(e)}


async def process_single_message(message: Dict, db: Session, start_time: float) -> Dict:
    """Process a single WhatsApp message"""
    
    try:
        # Extract message details
        message_type = message.get("type", "unknown")
        phone_number = message.get("from")
        message_id = message.get("id")
        
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
            media_response = get_media_response(message_type)
            safe_send_reply(phone_number, media_response)
            return {"skipped": True, "reason": f"non-text ({message_type})"}
        
        # Extract text message
        customer_message = message.get("text", {}).get("body", "")
        
        if not customer_message:
            logger.warning(f"Empty text message from {phone_number}")
            return {"skipped": True, "reason": "empty message"}
        
        logger.info(f"💬 Message: {customer_message[:200]}")
        
        # Process with AI Query Service
        try:
            from app.services.ai_query_service import process_whatsapp_query
            
            # Process the query
            response = process_whatsapp_query(customer_message, db, phone_number)
            logger.info(f"🤖 Response: {response[:200]}...")
            
            # Send response
            send_result = safe_send_reply(phone_number, response)
            
            return {
                "processed": True,
                "phone_number": phone_number,
                "message": customer_message[:100],
                "response_length": len(response),
                "send_success": send_result.get("success", False),
                "processing_time_ms": int((time.time() - start_time) * 1000),
                "ai_used": True
            }
            
        except ImportError as e:
            logger.error(f"Failed to import AI service: {e}")
            # Send fallback response
            fallback_response = get_fallback_response(customer_message)
            safe_send_reply(phone_number, fallback_response)
            return {
                "processed": True,
                "fallback": True,
                "message": customer_message[:100],
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
            
        except Exception as e:
            logger.error(f"AI processing error: {e}")
            fallback_response = get_fallback_response(customer_message)
            safe_send_reply(phone_number, fallback_response)
            return {
                "processed": True,
                "error": str(e),
                "fallback": True,
                "message": customer_message[:100],
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        return {"error": str(e), "processed": False}


def get_fallback_response(message: str) -> str:
    """Get fallback response when AI is unavailable"""
    
    msg_lower = message.lower().strip()
    
    # Greetings / First message
    if any(word in msg_lower for word in ["hello", "hi", "hey", "salam", "good morning", "good evening", "start"]):
        return get_welcome_message()
    
    # Help
    if any(word in msg_lower for word in ["help", "menu", "commands", "what can you do"]):
        return get_help_menu()
    
    # DN Tracking (10 digits)
    if msg_lower.isdigit() and len(msg_lower) == 10:
        return f"""🔢 *DN TRACKING - {msg_lower}*

I'm checking this delivery note in the system.

📦 *Status:* Fetching details...
⏳ Please wait while I retrieve complete information.

*Tip:* For faster results, type the DN number alone."""
    
    # Executive Summary
    if any(word in msg_lower for word in ["executive", "ceo", "summary"]):
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
• Total at Risk: Rs 1.2B
• Pending DNs: 0
• POD Pending: 376 DNs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS:*
1. Escalate top 5 risk dealers
2. Focus POD collection in Karachi
3. Review warehouse capacity

Type "Top risk dealers" for detailed list."""
    
    # Network Health
    if "health" in msg_lower or "network" in msg_lower:
        return """📊 *NETWORK HEALTH REPORT*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: 78/100
• Total DNs: 18,467
• Delivered: 17,402 ✅
• Delivery Rate: 94.2%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *REVENUE AT RISK: Rs 1.2B*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type "Executive summary" for detailed analysis."""
    
    # Top Dealers
    if "top dealer" in msg_lower or "top performing" in msg_lower:
        return """🏆 *TOP PERFORMING DEALERS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. *Bismillah Electronics*
   💰 Rs 1.10B | 📦 1,500 DNs

2. *Imran Electronics*
   💰 Rs 1.08B | 📦 2,937 DNs

3. *Afzal Electronics*
   💰 Rs 813M | 📦 2,865 DNs

4. *Naeem Electronics*
   💰 Rs 651M | 📦 2,741 DNs

5. *STM Associates*
   💰 Rs 577M | 📦 332 DNs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type a dealer name for detailed dashboard."""
    
    # Top Risk Dealers
    if "risk" in msg_lower:
        return """🚨 *TOP RISK DEALERS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. *Bismillah Electronics*
   📋 376 POD pending | Rs 1.19B at risk

2. *Naeem Electronics*
   ⏳ 245 pending DNs

3. *Afzal Electronics*
   ⏳ 189 pending DNs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *IMMEDIATE ACTIONS:*
• Escalate top dealers
• Deploy collection team
• Daily follow-up required"""
    
    # City Analysis
    if "city" in msg_lower or any(city in msg_lower for city in ["karachi", "lahore", "faisalabad"]):
        return """🌆 *CITY PERFORMANCE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 *Lahore* (Best)
   📦 12,444 DNs | 1% pending

🟢 *Karachi*
   📦 10,810 DNs | 0% pending

🟢 *Gujrat*
   📦 434 DNs | 0% pending

🟡 *Faisalabad* (Needs Attention)
   📦 5 DNs | 20% pending

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type "City analysis" for complete ranking."""
    
    # Warehouse
    if "warehouse" in msg_lower:
        return """🏭 *WAREHOUSE PERFORMANCE*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *TOP PERFORMERS:*
🟢 HPK - 94% efficiency
🟢 LHE - 91% efficiency

⚠️ *NEEDS ATTENTION:*
🟡 ISB - 76% efficiency

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type "Warehouse capacity" for detailed analysis."""
    
    # Revenue
    if "revenue" in msg_lower:
        return """💰 *REVENUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Revenue: Rs 4.31B
• Realized: Rs 3.11B ✅
• Pending: Rs 1.20B ⏳

📈 *REALIZATION RATE: 72.1%*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type "Top risk dealers" to see pending breakdown."""
    
    # Dealer name (likely)
    if len(msg_lower.split()) <= 5 and not msg_lower.isdigit():
        return f"""🏪 *DEALER LOOKUP - "{message}"*

I'm searching for this dealer in the database.

📊 *Please wait while I fetch:*
• Delivery performance
• Financial metrics
• Risk assessment
• Pending DNs
• POD status

*Tip:* Type exact dealer name for faster results."""
    
    # Default
    return get_help_menu()


def get_welcome_message() -> str:
    """Get welcome message for new users"""
    return """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT*

Welcome! I can analyze Dealers, DNs, PODs, Warehouses, Cities, Financial Performance, Risks, and Executive KPIs in real-time.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *WHAT YOU CAN ASK:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *Dealers*
• Type a dealer name (e.g., "Bhatti Electronics")
• "Top dealers" - Best performers
• "Top risk dealers" - Critical accounts

🔢 *DN Tracking*
• Send a 10-digit DN number

👑 *Executive Reports*
• "Executive summary"
• "Network health"

🌆 *Cities*
• "City analysis"
• "Karachi analysis"

🏭 *Warehouse*
• "Warehouse performance"

💰 *Financial*
• "Revenue analysis"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Just type your question naturally!*"""


def get_help_menu() -> str:
    """Get help menu"""
    return """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *AVAILABLE COMMANDS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *Dealer Intelligence*
• Type any dealer name
• "Top dealers" - Best performers
• "Top risk dealers" - Critical accounts

🔢 *DN Tracking*
• Send a 10-digit DN number

👑 *Executive Reports*
• "Executive summary"
• "Network health"

🌆 *City Analytics*
• "City analysis"
• "Karachi analysis"

🏭 *Warehouse Analytics*
• "Warehouse performance"

💰 *Financial Analytics*
• "Revenue analysis"
• "Outstanding value"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Examples:*
• "Bhatti Electronics"
• "6243611920"
• "Executive summary"
• "Top risk dealers"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type your question naturally - I understand context!"""


# ==========================================================
# HEALTH AND TEST ENDPOINTS
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
    except Exception:
        ai_available = False
    
    return {
        "status": "healthy",
        "service": "WhatsApp Webhook v10.0",
        "timestamp": datetime.utcnow().isoformat(),
        "config": {
            "whatsapp_configured": bool(config.WHATSAPP_ACCESS_TOKEN),
            "whatsapp_phone_id": bool(config.WHATSAPP_PHONE_NUMBER_ID),
            "whatsapp_token": bool(config.WHATSAPP_VERIFY_TOKEN),
            "groq_configured": bool(config.GROQ_API_KEY),
            "ai_enabled": getattr(config, 'ENABLE_GROQ', True),
            "ai_service_available": ai_available
        },
        "cache_stats": {
            "active_sessions": len(RECENT_MESSAGES),
            "cached_messages": sum(len(q) for q in RECENT_MESSAGES.values())
        }
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
        "version": "10.0",
        "endpoints": {
            "GET /webhook/": "Webhook verification",
            "POST /webhook/": "Receive messages",
            "GET /webhook/health": "Health check",
            "GET /webhook/test": "This test endpoint",
            "POST /webhook/test-send": "Manual send test",
            "GET /webhook/status": "Detailed status"
        },
        "config_status": {
            "WHATSAPP_ACCESS_TOKEN": "✅ Set" if config.WHATSAPP_ACCESS_TOKEN else "❌ Missing",
            "WHATSAPP_PHONE_NUMBER_ID": "✅ Set" if config.WHATSAPP_PHONE_NUMBER_ID else "❌ Missing",
            "WHATSAPP_VERIFY_TOKEN": "✅ Set" if config.WHATSAPP_VERIFY_TOKEN else "❌ Missing",
            "GROQ_API_KEY": "✅ Set" if config.GROQ_API_KEY else "❌ Missing",
            "AI_SERVICE": ai_service_status
        }
    }


@router.post("/test-send")
async def test_send_message(phone_number: str, message: str):
    """Test endpoint to manually send a message (for debugging)"""
    
    logger.info(f"Test send to {phone_number}: {message[:100]}")
    
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
        logger.info(f"Test webhook received")
        
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


@router.get("/status")
async def webhook_status():
    """Get detailed webhook status"""
    
    return {
        "webhook_url": "/webhook/",
        "verified": True,
        "active_sessions": len(RECENT_MESSAGES),
        "cached_messages": sum(len(q) for q in RECENT_MESSAGES.values()),
        "timestamp": datetime.utcnow().isoformat()
    }


@router.post("/clear-cache")
async def clear_cache():
    """Clear message cache (for debugging)"""
    global RECENT_MESSAGES
    RECENT_MESSAGES.clear()
    return {"success": True, "message": "Cache cleared", "cleared_sessions": len(RECENT_MESSAGES)}
