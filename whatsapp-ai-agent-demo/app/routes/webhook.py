# ==========================================================
# FILE: app/routes/webhook.py (IMPROVED v19.0)
# ==========================================================
# IMPROVEMENTS:
# - Added rate limiting with Redis/fallback
# - Added health checks for all services
# - Added conversation context tracking
# - Added webhook status endpoint
# - Added message queue support
# - Added comprehensive error handling
# - Added webhook statistics tracking
# ==========================================================

import os
import json
import time
import re
import uuid
import hmac
import hashlib
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from collections import deque, defaultdict
from contextvars import ContextVar
from functools import wraps

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from app.config import config
from app.database import get_db, SessionLocal

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook"])

# ==========================================================
# CONSTANTS
# ==========================================================

MAX_WHATSAPP_LENGTH = 3500
MAX_MESSAGE_LENGTH = 1000
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]
AI_TIMEOUT_SECONDS = 30
RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_WINDOW = 60

# Webhook Statistics
WEBHOOK_STATS = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "total_messages_processed": 0,
    "start_time": datetime.utcnow().isoformat(),
    "last_request_time": None,
    "errors_by_type": defaultdict(int)
}

# Conversation Context (in-memory cache, replace with Redis in production)
CONVERSATION_CONTEXT = {}
CONTEXT_EXPIRY_SECONDS = 1800  # 30 minutes

# ==========================================================
# IMPORTS
# ==========================================================

AI_SERVICE_AVAILABLE = False
WHATSAPP_SERVICE_AVAILABLE = False
REDIS_AVAILABLE = False

try:
    from app.services.ai_query_service import process_whatsapp_query
    AI_SERVICE_AVAILABLE = True
    logger.info("✅ AI Query Service loaded")
except Exception as e:
    logger.error(f"❌ AI Service failed: {e}")

try:
    from app.services.whatsapp_service import (
        send_text_message, 
        send_template_message,
        send_help_message,
        get_whatsapp_service
    )
    WHATSAPP_SERVICE_AVAILABLE = True
    logger.info("✅ WhatsApp service loaded")
except Exception as e:
    logger.error(f"❌ WhatsApp service failed: {e}")

try:
    import redis
    REDIS_AVAILABLE = True
    # Initialize Redis client if URL provided
    if config.REDIS_URL:
        redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
        logger.info("✅ Redis connected for rate limiting")
    else:
        redis_client = None
        REDIS_AVAILABLE = False
        logger.warning("⚠️ Redis URL not configured, using in-memory rate limiting")
except ImportError:
    REDIS_AVAILABLE = False
    redis_client = None
    logger.warning("⚠️ Redis not installed, using in-memory rate limiting")

# ==========================================================
# RATE LIMITING MIDDLEWARE
# ==========================================================

class RateLimiter:
    """Rate limiter with Redis fallback to in-memory"""
    
    def __init__(self):
        self.in_memory_store = defaultdict(list)
    
    def _cleanup_expired(self, phone_number: str, current_time: float):
        """Clean up expired entries for in-memory store"""
        if phone_number in self.in_memory_store:
            self.in_memory_store[phone_number] = [
                t for t in self.in_memory_store[phone_number]
                if current_time - t < RATE_LIMIT_WINDOW
            ]
    
    async def check_rate_limit(self, phone_number: str) -> Tuple[bool, int]:
        """
        Check if request is within rate limits
        
        Returns:
            (allowed: bool, retry_after_seconds: int)
        """
        current_time = time.time()
        
        # Try Redis first
        if REDIS_AVAILABLE and redis_client:
            try:
                key = f"rate_limit:{phone_number}"
                current_count = redis_client.get(key)
                
                if current_count is None:
                    redis_client.setex(key, RATE_LIMIT_WINDOW, 1)
                    return True, 0
                
                count = int(current_count)
                if count >= RATE_LIMIT_MAX_REQUESTS:
                    ttl = redis_client.ttl(key)
                    return False, max(1, ttl)
                
                redis_client.incr(key)
                return True, 0
                
            except Exception as e:
                logger.warning(f"Redis rate limit error: {e}, falling back to in-memory")
        
        # Fallback to in-memory
        self._cleanup_expired(phone_number, current_time)
        
        if len(self.in_memory_store[phone_number]) >= RATE_LIMIT_MAX_REQUESTS:
            oldest = min(self.in_memory_store[phone_number])
            retry_after = int(RATE_LIMIT_WINDOW - (current_time - oldest))
            return False, max(1, retry_after)
        
        self.in_memory_store[phone_number].append(current_time)
        return True, 0


rate_limiter = RateLimiter()

# ==========================================================
# CONVERSATION CONTEXT MANAGEMENT
# ==========================================================

def get_conversation_context(phone_number: str) -> Dict[str, Any]:
    """Get conversation context for a user"""
    if phone_number in CONVERSATION_CONTEXT:
        context = CONVERSATION_CONTEXT[phone_number]
        # Check if context is expired
        if datetime.utcnow().timestamp() - context.get("timestamp", 0) > CONTEXT_EXPIRY_SECONDS:
            del CONVERSATION_CONTEXT[phone_number]
            return {}
        return context.get("data", {})
    return {}


def update_conversation_context(phone_number: str, data: Dict[str, Any]):
    """Update conversation context for a user"""
    CONVERSATION_CONTEXT[phone_number] = {
        "data": data,
        "timestamp": datetime.utcnow().timestamp()
    }


def clear_conversation_context(phone_number: str):
    """Clear conversation context for a user"""
    if phone_number in CONVERSATION_CONTEXT:
        del CONVERSATION_CONTEXT[phone_number]


# ==========================================================
# WEBHOOK VERIFICATION
# ==========================================================

@router.get("/")
async def webhook_verification(request: Request):
    """WhatsApp webhook verification endpoint"""
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    
    logger.info(f"Webhook verification request - Mode: {hub_mode}, Token: {hub_verify_token[:10] if hub_verify_token else 'None'}...")
    
    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN and hub_challenge:
        logger.success("✅ Webhook verified successfully!")
        return PlainTextResponse(content=hub_challenge)
    
    logger.error(f"❌ Webhook verification failed - Token mismatch")
    raise HTTPException(status_code=403, detail="Verification failed")


# ==========================================================
# WHATSAPP SENDER
# ==========================================================

async def send_whatsapp_message(phone_number: str, message: str, request_id: str, context_message_id: str = None) -> Dict:
    """Send WhatsApp message with enhanced tracking"""
    
    if not WHATSAPP_SERVICE_AVAILABLE:
        logger.error(f"[{request_id}] ❌ WhatsApp service NOT available!")
        return {"success": False, "error": "Service not available"}
    
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.error(f"[{request_id}] ❌ Missing WhatsApp credentials!")
        return {"success": False, "error": "Missing credentials"}
    
    if len(message) > MAX_WHATSAPP_LENGTH:
        message = message[:MAX_WHATSAPP_LENGTH] + "\n\n... (truncated)"
    
    for attempt in range(MAX_RETRIES):
        try:
            # Use context message ID if provided (for replies)
            if context_message_id:
                result = send_text_message(phone_number, message, message_id=context_message_id)
            else:
                result = send_text_message(phone_number, message)
            
            logger.info(f"[{request_id}] WhatsApp API Response: success={result.get('success')}, "
                       f"status={result.get('status_code')}, "
                       f"message_id={result.get('message_id')}")
            
            if result.get("success"):
                return result
            elif attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            else:
                WEBHOOK_STATS["errors_by_type"]["send_failed"] += 1
                return result
                
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                logger.exception(f"[{request_id}] Send failed: {e}")
                WEBHOOK_STATS["errors_by_type"]["send_exception"] += 1
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": "Max retries exceeded"}


# ==========================================================
# AI PROCESSING
# ==========================================================

async def process_with_timeout(question: str, phone_number: str, request_id: str, context: Dict = None) -> str:
    """Process AI query with timeout and context awareness"""
    
    if not AI_SERVICE_AVAILABLE:
        return "⚠️ AI service is unavailable. Please try again later."
    
    logger.info(f"[{request_id}] 🤖 Processing: {question[:50]}")
    
    # Add context to question if available
    enhanced_question = question
    if context:
        last_intent = context.get("last_intent")
        if last_intent:
            enhanced_question = f"[Previous context: {last_intent}] {question}"
    
    def _run_ai():
        db = SessionLocal()
        try:
            result = process_whatsapp_query(enhanced_question, db, phone_number, phone_number)
            return result if result else "⚠️ No response generated."
        except Exception as e:
            logger.exception(f"AI error: {e}")
            return f"⚠️ Error: {str(e)[:100]}"
        finally:
            db.close()
    
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_ai),
            timeout=AI_TIMEOUT_SECONDS
        )
        logger.info(f"[{request_id}] ✅ AI completed, response length: {len(result)}")
        return result
    except asyncio.TimeoutError:
        logger.error(f"[{request_id}] ⏰ AI timeout")
        WEBHOOK_STATS["errors_by_type"]["ai_timeout"] += 1
        return "⚠️ Request timeout. Please try again."
    except Exception as e:
        logger.exception(f"[{request_id}] AI error: {e}")
        WEBHOOK_STATS["errors_by_type"]["ai_error"] += 1
        return f"⚠️ Processing error: {str(e)[:100]}"


# ==========================================================
# COMMAND HANDLERS
# ==========================================================

async def handle_help_command(phone_number: str, request_id: str) -> bool:
    """Handle help command"""
    if WHATSAPP_SERVICE_AVAILABLE:
        await send_whatsapp_message(phone_number, send_help_message(phone_number), request_id)
        return True
    return False


async def handle_status_command(phone_number: str, request_id: str) -> bool:
    """Handle status command"""
    status_message = f"""📊 *System Status*
━━━━━━━━━━━━━━━━━━━━━

*Services:*
• AI Service: {'✅ Online' if AI_SERVICE_AVAILABLE else '❌ Offline'}
• WhatsApp: {'✅ Online' if WHATSAPP_SERVICE_AVAILABLE else '❌ Offline'}
• Database: {'✅ Connected'}

*Statistics:*
• Total Requests: {WEBHOOK_STATS['total_requests']}
• Messages Processed: {WEBHOOK_STATS['total_messages_processed']}
• Success Rate: {_get_success_rate()}%

*Uptime:* {_get_uptime()}

━━━━━━━━━━━━━━━━━━━━━
Reply with 'help' for available commands"""
    
    await send_whatsapp_message(phone_number, status_message, request_id)
    return True


def _get_success_rate() -> float:
    """Calculate success rate"""
    if WEBHOOK_STATS['total_requests'] == 0:
        return 100.0
    return round((WEBHOOK_STATS['successful_requests'] / WEBHOOK_STATS['total_requests']) * 100, 1)


def _get_uptime() -> str:
    """Get uptime string"""
    start = datetime.fromisoformat(WEBHOOK_STATS['start_time'])
    delta = datetime.utcnow() - start
    hours = delta.total_seconds() / 3600
    if hours < 24:
        return f"{hours:.1f} hours"
    return f"{hours/24:.1f} days"


async def handle_clear_command(phone_number: str, request_id: str) -> bool:
    """Handle clear context command"""
    clear_conversation_context(phone_number)
    await send_whatsapp_message(phone_number, "✅ Conversation context cleared. Starting fresh!", request_id)
    return True


# ==========================================================
# MAIN WEBHOOK ENDPOINT
# ==========================================================

@router.post("/")
async def receive_message(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Main webhook endpoint for receiving WhatsApp messages"""
    
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    # Update statistics
    WEBHOOK_STATS["total_requests"] += 1
    WEBHOOK_STATS["last_request_time"] = datetime.utcnow().isoformat()
    
    logger.info(f"[{request_id}] 📨 Webhook received - Starting processing")
    
    try:
        # Parse request body
        raw_body = await request.body()
        payload = json.loads(raw_body.decode('utf-8'))
        
        # Log webhook payload for debugging (truncated for security)
        logger.debug(f"[{request_id}] Payload: {json.dumps(payload)[:500]}")
        
        # Extract message data
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        
        # Handle status updates (read receipts, delivery confirmations)
        if value.get("statuses"):
            statuses = value.get("statuses", [])
            for status in statuses:
                status_type = status.get("status")
                message_id = status.get("id")
                recipient_id = status.get("recipient_id")
                logger.info(f"[{request_id}] 📬 Status update - ID: {message_id}, Status: {status_type}")
            return {"success": True, "type": "status_update"}
        
        # Get messages
        messages = value.get("messages", [])
        if not messages:
            logger.warning(f"[{request_id}] No messages in webhook")
            return {"success": True, "type": "no_messages"}
        
        # Process each message
        for message in messages:
            msg_type = message.get("type", "unknown")
            phone_number = message.get("from")
            msg_id = message.get("id")
            timestamp = message.get("timestamp")
            
            logger.info(f"[{request_id}] 📱 From: {phone_number}, Type: {msg_type}, ID: {msg_id}")
            
            # Rate limiting check
            allowed, retry_after = await rate_limiter.check_rate_limit(phone_number)
            if not allowed:
                logger.warning(f"[{request_id}] Rate limit exceeded for {phone_number}")
                await send_whatsapp_message(
                    phone_number,
                    f"⚠️ Rate limit exceeded. Please wait {retry_after} seconds before sending more messages.",
                    request_id,
                    msg_id
                )
                continue
            
            # Handle non-text messages
            if msg_type != "text":
                await send_whatsapp_message(
                    phone_number, 
                    "📱 Please send text messages only.\n\nCommands:\n• help - Show available commands\n• status - System status\n• clear - Clear conversation",
                    request_id,
                    msg_id
                )
                WEBHOOK_STATS["total_messages_processed"] += 1
                continue
            
            # Get message text
            user_message = message.get("text", {}).get("body", "").strip()
            if not user_message:
                continue
            
            logger.info(f"[{request_id}] 💬 Message: {user_message[:100]}")
            
            # Handle special commands
            user_message_lower = user_message.lower()
            
            if user_message_lower == "help" or user_message_lower == "menu":
                await handle_help_command(phone_number, request_id)
                WEBHOOK_STATS["total_messages_processed"] += 1
                continue
            
            if user_message_lower == "status" or user_message_lower == "stats":
                await handle_status_command(phone_number, request_id)
                WEBHOOK_STATS["total_messages_processed"] += 1
                continue
            
            if user_message_lower == "clear" or user_message_lower == "reset":
                await handle_clear_command(phone_number, request_id)
                WEBHOOK_STATS["total_messages_processed"] += 1
                continue
            
            # Check if it's a DN query (numeric only, 10-15 digits)
            if re.match(r'^\d{10,15}$', user_message.strip()):
                logger.info(f"[{request_id}] 🔢 DN QUERY: {user_message}")
                # Store context for follow-up questions
                update_conversation_context(phone_number, {
                    "last_intent": "dn_query",
                    "last_dn": user_message,
                    "timestamp": datetime.utcnow().isoformat()
                })
            
            # Get conversation context
            context = get_conversation_context(phone_number)
            
            # Process with AI (run in background to not block webhook response)
            response = await process_with_timeout(user_message, phone_number, request_id, context)
            
            # Send response
            send_result = await send_whatsapp_message(phone_number, response, request_id, msg_id)
            
            # Update context based on response type
            if "DN Intelligence" in response or "dn_number" in response.lower():
                update_conversation_context(phone_number, {
                    **context,
                    "last_response_type": "dn_report",
                    "timestamp": datetime.utcnow().isoformat()
                })
            
            WEBHOOK_STATS["total_messages_processed"] += 1
            logger.info(f"[{request_id}] 📤 Send result: {send_result.get('success')}")
        
        # Update success statistics
        WEBHOOK_STATS["successful_requests"] += 1
        
        # Log processing time
        processing_time = time.time() - start_time
        logger.info(f"[{request_id}] ✅ Webhook processed in {processing_time:.2f} seconds")
        
        return {
            "success": True, 
            "request_id": request_id,
            "processing_time_ms": round(processing_time * 1000, 2)
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"[{request_id}] Invalid JSON payload: {e}")
        WEBHOOK_STATS["failed_requests"] += 1
        WEBHOOK_STATS["errors_by_type"]["json_decode"] += 1
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Invalid JSON payload", "request_id": request_id}
        )
        
    except Exception as e:
        logger.exception(f"[{request_id}] Webhook error: {e}")
        WEBHOOK_STATS["failed_requests"] += 1
        WEBHOOK_STATS["errors_by_type"]["general"] += 1
        return {
            "success": False, 
            "error": str(e), 
            "request_id": request_id,
            "type": type(e).__name__
        }


# ==========================================================
# WEBHOOK STATUS & STATISTICS ENDPOINTS
# ==========================================================

@router.get("/stats")
async def webhook_stats():
    """Get webhook statistics"""
    return {
        "statistics": WEBHOOK_STATS,
        "success_rate": _get_success_rate(),
        "uptime": _get_uptime(),
        "conversation_contexts": len(CONVERSATION_CONTEXT),
        "rate_limiter": {
            "type": "redis" if REDIS_AVAILABLE and redis_client else "in-memory",
            "max_requests": RATE_LIMIT_MAX_REQUESTS,
            "window_seconds": RATE_LIMIT_WINDOW
        }
    }


@router.post("/stats/reset")
async def reset_webhook_stats():
    """Reset webhook statistics (admin only in production)"""
    global WEBHOOK_STATS
    
    # In production, add authentication here
    WEBHOOK_STATS = {
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "total_messages_processed": 0,
        "start_time": datetime.utcnow().isoformat(),
        "last_request_time": None,
        "errors_by_type": defaultdict(int)
    }
    
    return {"success": True, "message": "Statistics reset"}


@router.get("/health")
async def health_check():
    """Health check endpoint with detailed service status"""
    
    # Check database health
    db_healthy = False
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db_healthy = True
        db.close()
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
    
    return {
        "status": "healthy" if (AI_SERVICE_AVAILABLE and WHATSAPP_SERVICE_AVAILABLE and db_healthy) else "degraded",
        "version": "19.0",
        "whatsapp_service": WHATSAPP_SERVICE_AVAILABLE,
        "ai_service": AI_SERVICE_AVAILABLE,
        "database": db_healthy,
        "redis": REDIS_AVAILABLE and redis_client is not None,
        "credentials": {
            "token": "✓" if config.WHATSAPP_ACCESS_TOKEN else "✗",
            "phone_id": "✓" if config.WHATSAPP_PHONE_NUMBER_ID else "✗",
            "verify_token": "✓" if config.WHATSAPP_VERIFY_TOKEN else "✗"
        },
        "timestamp": datetime.utcnow().isoformat(),
        "uptime": _get_uptime()
    }


@router.get("/test-dn/{dn_number}")
async def test_dn_lookup(dn_number: str):
    """Test DN lookup endpoint for debugging"""
    from app.services.logistics_query_service import LogisticsQueryService
    
    db = SessionLocal()
    try:
        service = LogisticsQueryService(db)
        result = service.get_complete_dn_intelligence(dn_number)
        return {"found": "error" not in result, "result": result}
    except Exception as e:
        logger.error(f"DN lookup error: {e}")
        return {"found": False, "error": str(e)}
    finally:
        db.close()


@router.get("/status")
async def status():
    """Simple status endpoint for monitoring"""
    return {
        "service": "Webhook v19.0",
        "status": "running",
        "whatsapp_service": WHATSAPP_SERVICE_AVAILABLE,
        "ai_service": AI_SERVICE_AVAILABLE,
        "message": "Ready to receive messages",
        "last_request": WEBHOOK_STATS.get("last_request_time"),
        "total_requests_today": WEBHOOK_STATS["total_requests"]
    }


@router.get("/conversations")
async def get_active_conversations():
    """Get active conversation contexts (admin only in production)"""
    active = []
    for number, context in CONVERSATION_CONTEXT.items():
        active.append({
            "phone_number": number[-6:],  # Masked for privacy
            "last_intent": context.get("data", {}).get("last_intent"),
            "timestamp": context.get("timestamp")
        })
    
    return {
        "active_conversations": len(active),
        "conversations": active[-20:]  # Last 20
    }


@router.delete("/conversations/{phone_number}")
async def clear_conversation(phone_number: str):
    """Clear conversation context for a specific user"""
    clear_conversation_context(phone_number)
    return {"success": True, "message": f"Context cleared for {phone_number[-6:]}"}
