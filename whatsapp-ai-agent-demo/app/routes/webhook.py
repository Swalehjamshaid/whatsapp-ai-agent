# ==========================================================
# FILE: app/routes/webhook.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any
import json

from app.config import (
    WHATSAPP_VERIFY_TOKEN
)

from app.services.whatsapp_service import (
    parse_whatsapp_message,
    verify_webhook,
    send_text_message
)

# ==========================================================
# STEP 1: REMOVED claude_service import
# ==========================================================

from app.services.conversation_service import (
    add_user_message,
    add_ai_message
)

# ==========================================================
# STEP 1: ADDED ai_query_service import
# ==========================================================

from app.services.ai_query_service import (
    AIQueryService
)

from app.database import get_db

# ==========================================================
# ROUTER
# ==========================================================

router = APIRouter(
    prefix="/webhook",
    tags=["WhatsApp Webhook"]
)

# ==========================================================
# User Session Tracking (In-memory - will be lost on restart)
# TODO: Move to PostgreSQL for production
# ==========================================================

user_sessions: Dict[str, Dict[str, Any]] = {}

def get_user_session(phone_number: str) -> Dict[str, Any]:
    """Get or create user session."""
    if phone_number not in user_sessions:
        user_sessions[phone_number] = {
            "pending_dealer_selection": None,
            "pending_dealer_matches": [],
            "last_intent": None,
            "selected_dealer": None,
            "last_question": None
        }
    return user_sessions[phone_number]

def clear_user_session(phone_number: str):
    """Clear user session."""
    if phone_number in user_sessions:
        user_sessions[phone_number] = {
            "pending_dealer_selection": None,
            "pending_dealer_matches": [],
            "last_intent": None,
            "selected_dealer": None,
            "last_question": None
        }

# ==========================================================
# HELPER FOR DEALER SELECTION (UPDATED)
# ==========================================================

def handle_dealer_selection(customer_message: str, phone_number: str, db: Session, ai_service: AIQueryService) -> Dict[str, Any]:
    """Handle dealer selection flow with confirmation message."""
    session = get_user_session(phone_number)
    
    # Check if user is responding to a dealer selection prompt
    if session.get("pending_dealer_selection") and customer_message.strip().isdigit():
        selection = int(customer_message.strip()) - 1
        matches = session.get("pending_dealer_matches", [])
        
        if 0 <= selection < len(matches):
            # User selected a dealer
            selected_dealer = matches[selection]["dealer_name"]
            session["selected_dealer"] = selected_dealer
            session["pending_dealer_selection"] = None
            session["pending_dealer_matches"] = []
            session["last_intent"] = "dealer_lookup_selected"
            
            # Get full dealer summary using AI Query Service
            result = ai_service.process_query(
                question=f"Show dealer {selected_dealer} dashboard",
                user_phone=phone_number
            )
            
            if result.get("success"):
                return {
                    "success": True,
                    "summary": result.get("response", "Dealer information retrieved."),
                    "intent": "dealer_lookup_selected",
                    "data": result.get("dashboard", {}),
                    "dealer_name": selected_dealer
                }
    
    return {"success": False}

# ==========================================================
# WEBHOOK VERIFICATION
# ==========================================================

@router.get("/")
async def webhook_verification(
    hub_mode: str = "",
    hub_verify_token: str = "",
    hub_challenge: str = ""
):
    result = verify_webhook(
        hub_verify_token,
        hub_challenge,
        WHATSAPP_VERIFY_TOKEN
    )

    if result["success"]:
        return int(result["challenge"])

    return {
        "success": False,
        "message": "Verification failed"
    }

# ==========================================================
# RECEIVE WHATSAPP MESSAGE (FULLY UPDATED)
# ==========================================================

@router.post("/")
async def receive_message(
    request: Request,
    db: Session = Depends(get_db)
):
    payload = await request.json()

    parsed_message = parse_whatsapp_message(payload)

    if not parsed_message:
        return {
            "success": True,
            "message": "No message found"
        }

    phone_number = parsed_message["from"]
    customer_message = parsed_message["text"]

    # ==========================================================
    # LOGGING: Track incoming message
    # ==========================================================
    print("\n" + "="*80)
    print(f"📱 WHATSAPP MESSAGE RECEIVED")
    print(f"📞 From: {phone_number}")
    print(f"💬 Message: {customer_message}")
    print("="*80)

    # ==========================================================
    # SAVE CUSTOMER MESSAGE
    # ==========================================================
    add_user_message(phone_number, customer_message)

    # ==========================================================
    # STEP 2: CREATE AI SERVICE INSTANCE
    # ==========================================================
    ai_service = AIQueryService(db)

    # ==========================================================
    # CHECK FOR DEALER SELECTION RESPONSE FIRST
    # ==========================================================
    selection_result = handle_dealer_selection(customer_message, phone_number, db, ai_service)
    
    if selection_result.get("success"):
        ai_reply = selection_result.get("summary", "Dealer information retrieved.")
        intent = selection_result.get("intent", "dealer_lookup_selected")
        
        # Save AI response
        add_ai_message(phone_number, ai_reply)
        
        # Send WhatsApp response
        whatsapp_response = send_text_message(phone_number, ai_reply)
        
        # Log response
        print("\n" + "="*80)
        print(f"✅ RESPONSE SENT TO {phone_number}")
        print(f"💬 Reply Preview: {ai_reply[:200]}...")
        print(f"📊 Intent: {intent}")
        print("="*80 + "\n")
        
        return {
            "success": True,
            "customer_message": customer_message,
            "ai_reply": ai_reply,
            "intent": intent,
            "whatsapp_response": whatsapp_response
        }
    
    # ==========================================================
    # STEP 3: PROCESS QUERY WITH AI QUERY SERVICE
    # ==========================================================
    try:
        # Use AI Query Service to process the question
        result = ai_service.process_query(
            question=customer_message,
            user_phone=phone_number
        )
        
        # Extract response and intent
        ai_reply = result.get("response", "Unable to generate response.")
        intent = result.get("question_type", "general")
        
        # Update session
        session = get_user_session(phone_number)
        session["last_intent"] = intent
        session["last_question"] = customer_message
        
        # Check if this is a fuzzy match that needs dealer selection
        if result.get("fuzzy"):
            matches = result.get("matches", [])
            if matches:
                session = get_user_session(phone_number)
                session["pending_dealer_selection"] = True
                session["pending_dealer_matches"] = matches
                
                # The response already contains the selection prompt
                # No need to modify ai_reply
                print(f"📋 Multiple dealers found, awaiting selection from user")
        
        # ==========================================================
        # LOGGING: Show what was processed
        # ==========================================================
        print("\n" + "="*80)
        print(f"🤖 AI QUERY SERVICE PROCESSED")
        print(f"📊 Intent: {intent}")
        print(f"🎯 Question Type: {result.get('question_type', 'unknown')}")
        print(f"🤖 AI Used: {result.get('ai_used', False)}")
        print(f"⏱️ Processing Time: {result.get('processing_time_ms', 0)}ms")
        print(f"📝 Response Preview: {ai_reply[:300]}...")
        print("="*80)
        
    except Exception as e:
        print(f"❌ Error in AI Query Service: {str(e)}")
        import traceback
        traceback.print_exc()
        
        ai_reply = (
            "I'm having trouble accessing the logistics database right now. "
            "Please try again in a moment. If the issue persists, contact support."
        )
        intent = "error"

    # ==========================================================
    # SAVE AI RESPONSE
    # ==========================================================
    add_ai_message(phone_number, ai_reply)

    # ==========================================================
    # SEND WHATSAPP RESPONSE
    # ==========================================================
    whatsapp_response = send_text_message(phone_number, ai_reply)
    
    # ==========================================================
    # LOGGING: Response sent
    # ==========================================================
    print("\n" + "="*80)
    print(f"✅ RESPONSE SENT TO {phone_number}")
    print(f"💬 Reply Preview: {ai_reply[:200]}...")
    print(f"📊 Intent: {intent}")
    print("="*80 + "\n")

    return {
        "success": True,
        "customer_message": customer_message,
        "ai_reply": ai_reply,
        "intent": intent,
        "whatsapp_response": whatsapp_response
    }

# ==========================================================
# TEST WEBHOOK
# ==========================================================

@router.get("/test")
async def test_webhook():
    return {
        "success": True,
        "message": "Webhook is active"
    }

# ==========================================================
# DEMO MESSAGE (UPDATED WITH AI QUERY SERVICE)
# ==========================================================

@router.post("/demo")
async def demo_message(db: Session = Depends(get_db)):
    customer_message = "How many pending deliveries?"
    
    # ==========================================================
    # STEP 5: Use AI Query Service for demo
    # ==========================================================
    ai_service = AIQueryService(db)
    
    result = ai_service.process_query(
        question=customer_message,
        user_phone="demo"
    )
    
    ai_reply = result.get("response", "Unable to generate response.")
    intent = result.get("question_type", "general")
    
    return {
        "success": True,
        "customer_message": customer_message,
        "ai_reply": ai_reply,
        "intent": intent,
        "processing_time_ms": result.get("processing_time_ms", 0),
        "ai_used": result.get("ai_used", False)
    }

# ==========================================================
# WEBHOOK STATUS
# ==========================================================

@router.get("/status")
async def webhook_status():
    return {
        "service": "WhatsApp Webhook",
        "status": "running",
        "verify_token": bool(WHATSAPP_VERIFY_TOKEN),
        "ai_query_service_integration": True,
        "active_sessions": len(user_sessions)
    }

# ==========================================================
# CLEAR SESSION (For testing)
# ==========================================================

@router.post("/clear-session/{phone_number}")
async def clear_session(phone_number: str):
    """Clear user session for testing."""
    clear_user_session(phone_number)
    return {
        "success": True,
        "message": f"Session cleared for {phone_number}"
    }

# ==========================================================
# TEST LOGISTICS QUERY ENDPOINT (UPDATED)
# ==========================================================

@router.get("/test-logistics")
async def test_logistics_query(
    q: str,
    db: Session = Depends(get_db)
):
    """Test endpoint for logistics queries using AI Query Service."""
    ai_service = AIQueryService(db)
    
    result = ai_service.process_query(
        question=q,
        user_phone="test"
    )
    
    return {
        "question": q,
        "question_type": result.get("question_type", "unknown"),
        "response": result.get("response", "No response"),
        "ai_used": result.get("ai_used", False),
        "processing_time_ms": result.get("processing_time_ms", 0),
        "success": result.get("success", False)
    }

# ==========================================================
# SESSION STATUS ENDPOINT
# ==========================================================

@router.get("/session/{phone_number}")
async def get_session(phone_number: str):
    """Get user session for debugging."""
    session = get_user_session(phone_number)
    return {
        "phone_number": phone_number,
        "session": session
    }

# ==========================================================
# HEALTH CHECK ENDPOINT
# ==========================================================

@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "WhatsApp Webhook with AI Query Service",
        "architecture": "WhatsApp → Webhook → AIQueryService → AnalyticsService → AIProviderService"
    }
