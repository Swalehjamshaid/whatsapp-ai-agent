# ==========================================================
# FILE: app/routes/webhook.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.config import (
    WHATSAPP_VERIFY_TOKEN
)

from app.services.whatsapp_service import (
    parse_whatsapp_message,
    verify_webhook,
    send_text_message
)

from app.services.claude_service import (
    ask_claude
)

from app.services.conversation_service import (
    add_user_message,
    add_ai_message,
    get_user_context,
    set_user_context
)

# ==========================================================
# NEW: LOGISTICS SERVICE IMPORT
# ==========================================================

from app.services.logistics_query_service import (
    LogisticsQueryService,
    handle_ai_query
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
# USER SESSION TRACKING (For dealer selection)
# ==========================================================

# In-memory session tracking (can be moved to database)
user_sessions: Dict[str, Dict[str, Any]] = {}

def get_user_session(phone_number: str) -> Dict[str, Any]:
    """Get or create user session."""
    if phone_number not in user_sessions:
        user_sessions[phone_number] = {
            "pending_dealer_selection": None,
            "pending_dealer_matches": [],
            "last_intent": None,
            "selected_dealer": None
        }
    return user_sessions[phone_number]

def clear_user_session(phone_number: str):
    """Clear user session."""
    if phone_number in user_sessions:
        user_sessions[phone_number] = {
            "pending_dealer_selection": None,
            "pending_dealer_matches": [],
            "last_intent": None,
            "selected_dealer": None
        }

# ==========================================================
# HELPER: Build Logistics Prompt for Claude
# ==========================================================

def build_logistics_prompt(customer_message: str, context: str, intent: str) -> str:
    """Build specialized logistics prompt for Claude."""
    
    prompt = f"""You are HNR Logistics AI Assistant, a professional logistics operations manager.

BUSINESS RULES FOR LOGISTICS INTERPRETATION:
1. PGI Status = "Completed" means: The shipment has been dispatched/delivered from warehouse
2. PGI Status = "Pending" means: The shipment is still at warehouse, pending dispatch
3. POD Status = "Received" means: The dealer has acknowledged and received the shipment
4. POD Status = "Pending" means: Shipment delivered but awaiting dealer acknowledgement
5. When PGI is Completed but POD is Pending: State "Shipment delivered and awaiting dealer acknowledgement"
6. When POD is Received: State "Dealer has received and acknowledged the shipment"
7. When PGI is Pending: State "Shipment is pending dispatch from warehouse"
8. Never expose raw database field names (PGI/POD) to end users
9. Always use business-friendly terms: "dispatched", "delivered", "acknowledged", "pending"

RESPONSE GUIDELINES:
1. Act as a professional Logistics Operations Manager
2. Explain delivery status clearly using business terms
3. Explain business impact when relevant
4. Mention pending risks if applicable
5. Keep responses professional but conversational
6. Be concise (2-4 sentences for simple queries, more for detailed summaries)
7. Format amounts as Rs X,XXX.XX
8. Format quantities as X,XXX units
9. For pending items, suggest follow-up actions

USER QUESTION:
{customer_message}

INTENT DETECTED: {intent}

DATABASE INFORMATION:
{context}

RESPONSE STYLE:
- Professional but approachable
- Data-driven but human-readable
- Action-oriented for pending items
- Acknowledge risks and delays

RESPONSE:"""
    
    return prompt

# ==========================================================
# HELPER: Handle Dealer Selection
# ==========================================================

def handle_dealer_selection(customer_message: str, phone_number: str, db: Session) -> Dict[str, Any]:
    """Handle dealer selection flow (when user picks from multiple matches)."""
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
            
            # Get full dealer summary
            dealer_result = LogisticsQueryService.get_dealer_summary(db, selected_dealer)
            
            if dealer_result.get("success"):
                summary = LogisticsQueryService.generate_dealer_summary_text(dealer_result)
                return {
                    "success": True,
                    "summary": summary,
                    "intent": "dealer_lookup",
                    "data": dealer_result
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
# RECEIVE WHATSAPP MESSAGE (UPDATED WITH LOGISTICS)
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
    # CHECK FOR DEALER SELECTION RESPONSE FIRST
    # ==========================================================
    selection_result = handle_dealer_selection(customer_message, phone_number, db)
    
    if selection_result.get("success"):
        # User selected a dealer from multiple matches
        ai_reply = selection_result.get("summary", "Dealer information retrieved.")
        context = ai_reply
        intent = "dealer_lookup_selected"
        
        # Build and send AI response
        prompt = build_logistics_prompt(customer_message, context, intent)
        ai_reply = ask_claude(prompt)
        
        # Save AI response
        add_ai_message(phone_number, ai_reply)
        
        # Send WhatsApp response
        whatsapp_response = send_text_message(phone_number, ai_reply)
        
        return {
            "success": True,
            "customer_message": customer_message,
            "ai_reply": ai_reply,
            "whatsapp_response": whatsapp_response
        }
    
    # ==========================================================
    # DETECT INTENT AND QUERY LOGISTICS DATABASE
    # ==========================================================
    try:
        # Get AI context from logistics database
        ai_result = LogisticsQueryService.handle_ai_query(
            question=customer_message,
            db=db,
            openai_client=None  # We'll use Claude instead
        )
        
        context = ai_result.get("summary", "No logistics data found")
        intent = ai_result.get("intent", "general_query")
        
        # ==========================================================
        # HANDLE DEALER LOOKUP WITH MULTIPLE MATCHES
        # ==========================================================
        if intent == "dealer_lookup" and ai_result.get("fuzzy"):
            # Multiple dealers found - ask user to select
            matches = ai_result.get("matches", [])
            if matches:
                session = get_user_session(phone_number)
                session["pending_dealer_selection"] = True
                session["pending_dealer_matches"] = matches
                
                dealer_list = "\n".join([
                    f"{i+1}. {m['dealer_name']} ({m['total_dns']} DNs, Rs {m['total_amount']:,.2f})"
                    for i, m in enumerate(matches[:5])
                ])
                
                context = f"Multiple dealers found:\n{dealer_list}\n\nPlease reply with the number of your dealer."
                intent = "dealer_selection"
        
        # ==========================================================
        # CHECK IF DATA WAS FOUND
        # ==========================================================
        if not ai_result.get("success", True):
            # No data found - return helpful message
            ai_reply = (
                "I could not find matching logistics data in our system. "
                "Please provide a valid DN number (e.g., 6243612322), "
                "dealer name (e.g., Faisal Traders), "
                "city name, or warehouse code."
            )
            
            add_ai_message(phone_number, ai_reply)
            whatsapp_response = send_text_message(phone_number, ai_reply)
            
            return {
                "success": True,
                "customer_message": customer_message,
                "ai_reply": ai_reply,
                "whatsapp_response": whatsapp_response
            }
        
        # ==========================================================
        # BUILD SPECIALIZED LOGISTICS PROMPT FOR CLAUDE
        # ==========================================================
        prompt = build_logistics_prompt(customer_message, context, intent)
        
        # ==========================================================
        # LOGGING: Show what's being sent to Claude
        # ==========================================================
        print("\n" + "="*80)
        print(f"🤖 SENDING TO CLAUDE")
        print(f"📊 Intent: {intent}")
        print(f"📝 Context: {context[:500]}...")
        print("="*80)
        
        # ==========================================================
        # GET AI RESPONSE FROM CLAUDE
        # ==========================================================
        ai_reply = ask_claude(prompt)
        
    except Exception as e:
        # Handle any errors in logistics query
        print(f"❌ Error in logistics query: {str(e)}")
        ai_reply = (
            "I'm having trouble accessing the logistics database right now. "
            "Please try again in a moment."
        )

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
    print(f"💬 Reply: {ai_reply[:200]}...")
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
# DEMO MESSAGE (UPDATED WITH LOGISTICS)
# ==========================================================

@router.post("/demo")
async def demo_message(db: Session = Depends(get_db)):
    customer_message = "How many pending deliveries?"
    
    # Use logistics service
    ai_result = LogisticsQueryService.handle_ai_query(
        question=customer_message,
        db=db,
        openai_client=None
    )
    
    context = ai_result.get("summary", "No logistics data found")
    intent = ai_result.get("intent", "general_query")
    
    prompt = build_logistics_prompt(customer_message, context, intent)
    ai_reply = ask_claude(prompt)

    return {
        "success": True,
        "customer_message": customer_message,
        "ai_reply": ai_reply,
        "intent": intent,
        "summary": context
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
        "logistics_integration": True
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
# TEST LOGISTICS QUERY ENDPOINT
# ==========================================================

@router.get("/test-logistics")
async def test_logistics_query(
    q: str,
    db: Session = Depends(get_db)
):
    """Test endpoint for logistics queries."""
    result = LogisticsQueryService.handle_ai_query(
        question=q,
        db=db,
        openai_client=None
    )
    
    return {
        "question": q,
        "intent": result.get("intent"),
        "summary": result.get("summary"),
        "has_data": result.get("metadata", {}).get("has_data", False)
    }
