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

from app.services.claude_service import (
    ask_claude
)

from app.services.conversation_service import (
    add_user_message,
    add_ai_message
)

# ==========================================================
# FIX 1: Remove unused import - only import what we need
# ==========================================================

from app.services.logistics_query_service import (
    LogisticsQueryService
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
# FIX 2: User Session Tracking (In-memory - will be lost on restart)
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
# FIX 3,4,5,6,7,8,9,10: Enhanced Logistics Prompt Builder
# ==========================================================

def build_logistics_prompt(customer_message: str, context: str, intent: str) -> str:
    """Build specialized logistics prompt for Claude with all business rules."""
    
    # FIX 6: Context length protection
    if len(context) > 6000:
        context = context[:6000]
        context += "\n... (truncated for length)"
    
    prompt = f"""You are HNR Logistics AI Assistant, a professional logistics operations manager.

================================================================================
CRITICAL BUSINESS RULES - MUST FOLLOW:
================================================================================

DELIVERY STATUS INTERPRETATION (NEVER expose raw field names):
- PGI Completed + POD Received = "Delivered and Acknowledged"
- PGI Completed + POD Pending = "Delivered Awaiting Dealer Acknowledgement"  
- PGI Pending = "Pending Dispatch"

NEVER tell a user "PGI Completed" - Instead say "Shipment Delivered"
NEVER tell a user "POD Received" - Instead say "Dealer Acknowledged Receipt"
NEVER tell a user "POD Pending" - Instead say "Awaiting Dealer Acknowledgement"

================================================================================
BUSINESS RULES FOR RESPONSES:
================================================================================

1. PGI Status = "Completed" means: The shipment has been dispatched/delivered
2. PGI Status = "Pending" means: The shipment is still at warehouse, pending dispatch
3. POD Status = "Received" means: The dealer has acknowledged and received the shipment
4. POD Status = "Pending" means: Shipment delivered but awaiting dealer acknowledgement

================================================================================
DEALER BUSINESS RULES:
================================================================================

If user asks about a dealer, ALWAYS provide:
- Total DNs
- Delivered DNs
- Pending DNs
- Total Quantity (units)
- Delivered Quantity (units)
- Pending Quantity (units)
- Pending Amount (Rs)

If dealer has pending deliveries: Mention operational risk and suggest follow-up action.

================================================================================
WAREHOUSE INTELLIGENCE RULES:
================================================================================

If user asks "Which warehouse has highest pending?" ALWAYS provide:
- Warehouse Name
- Pending DNs count
- Pending Quantity (units)
- Pending Amount (Rs)
- Operational risk level
- Suggested action

================================================================================
CITY INTELLIGENCE RULES:
================================================================================

If user asks "Which city has highest pending?" ALWAYS provide:
- City Name
- Pending DNs count
- Pending Quantity (units)
- Pending Amount (Rs)

================================================================================
PRODUCT INTELLIGENCE RULES:
================================================================================

If user asks about products (refrigerators, LED TVs, etc.):
- Show total quantity delivered
- Show pending quantity
- Show which dealers have pending stock
- Suggest follow-up actions

================================================================================
EXECUTIVE SUMMARY RULES:
================================================================================

If user asks for executive summary, logistics summary, or business insights:
- Total Deliveries
- Completion Rate (%)
- Pending Deliveries with Amount
- Pending Dispatch (PGI count)
- Awaiting Dealer Acknowledgement (POD count)
- Top 3 Dealers by delivery volume
- Top 3 Cities by delivery volume
- Top Warehouse by performance
- Biggest Risk & Recommendation
- Focus Area for today

================================================================================
CUSTOMER QUESTION:
================================================================================

{customer_message}

INTENT DETECTED: {intent}

================================================================================
DATABASE INFORMATION:
================================================================================

{context}

================================================================================
RESPONSE GUIDELINES:
================================================================================

1. Act as a professional Logistics Operations Manager
2. Use the business rules above for interpreting status
3. NEVER expose raw field names (PGI/POD)
4. ALWAYS use business terms: "dispatched", "delivered", "acknowledged", "pending"
5. For dealers, provide complete summary with quantities and amounts
6. For pending items, mention operational risk and suggest follow-up
7. Format amounts as Rs X,XXX.XX
8. Format quantities as X,XXX units
9. Be concise but comprehensive
10. For executive queries, provide actionable insights

================================================================================
EXAMPLE RESPONSES:
================================================================================

DN Query: "DN 6243612322 has been delivered and acknowledged by the dealer. 
Total quantity: 50 units. Amount: Rs 150,000.00"

Dealer Query: "Faisal Traders has 152 total DNs. 128 delivered, 24 pending. 
Pending quantity: 750 units worth Rs 8,700,000. Recommend following up on pending deliveries."

Executive Query: "Logistics Summary: 1,247 total deliveries. 85% completion rate. 
Pending deliveries: 187 (Rs 18.2M). Top risk: HPK warehouse with 42 pending DNs. 
Recommend focusing on HPK dispatches today."

================================================================================
RESPONSE:
================================================================================"""
    
    return prompt

# ==========================================================
# FIX 7: Better "No Data Found" Handling
# ==========================================================

def get_no_data_message() -> str:
    """Return helpful message when no data found."""
    return """I couldn't find matching logistics records in our system.

Try asking about:

📦 **DN Number:** Check DN 6243612322
🏢 **Dealer Name:** Show Faisal Traders summary
🏭 **Warehouse:** Which warehouse has highest pending?
🌆 **City:** Show Lahore deliveries
📊 **Summary:** Give me logistics summary
⏳ **Pending:** How many deliveries pending?
✅ **POD:** How many awaiting acknowledgement?

Please provide a specific DN number, dealer name, warehouse, or city for accurate information."""

# ==========================================================
# FIX 5 & 6: Helper to handle dealer selection with confirmation
# ==========================================================

def handle_dealer_selection(customer_message: str, phone_number: str, db: Session) -> Dict[str, Any]:
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
            
            # Get full dealer summary
            dealer_result = LogisticsQueryService.get_dealer_summary(db, selected_dealer)
            
            if dealer_result.get("success"):
                summary = LogisticsQueryService.generate_dealer_summary_text(dealer_result)
                
                # Add confirmation message
                confirmation = f"✅ Dealer Confirmed: {selected_dealer}\n\nGenerating logistics summary...\n\n"
                
                return {
                    "success": True,
                    "summary": confirmation + summary,
                    "intent": "dealer_lookup_selected",
                    "data": dealer_result,
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
    # CHECK FOR DEALER SELECTION RESPONSE FIRST
    # ==========================================================
    selection_result = handle_dealer_selection(customer_message, phone_number, db)
    
    if selection_result.get("success"):
        context = selection_result.get("summary", "Dealer information retrieved.")
        intent = selection_result.get("intent", "dealer_lookup_selected")
        
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
        
        # Update session
        session = get_user_session(phone_number)
        session["last_intent"] = intent
        session["last_question"] = customer_message
        
        # ==========================================================
        # FIX 8 & 9: Handle Executive and Product Queries
        # ==========================================================
        
        # Check if this is an executive/insights query
        executive_keywords = ["executive summary", "logistics summary", "business insights", 
                             "what needs attention", "biggest risk", "ceo report"]
        if any(keyword in customer_message.lower() for keyword in executive_keywords):
            intent = "executive_summary"
            # Get executive summary
            exec_result = LogisticsQueryService.get_executive_summary(db)
            context = exec_result.get("executive_summary", "Executive summary generated.")
        
        # Check if this is a product query
        product_keywords = ["refrigerator", "led tv", "tv", "washing machine", "product", "material"]
        if any(keyword in customer_message.lower() for keyword in product_keywords):
            intent = "product_query"
        
        # ==========================================================
        # HANDLE DEALER LOOKUP WITH MULTIPLE MATCHES
        # ==========================================================
        if intent == "dealer_lookup" and ai_result.get("fuzzy"):
            matches = ai_result.get("matches", [])
            if matches:
                session = get_user_session(phone_number)
                session["pending_dealer_selection"] = True
                session["pending_dealer_matches"] = matches
                
                dealer_list = "\n".join([
                    f"{i+1}. {m['dealer_name']} ({m['total_dns']} DNs, Rs {m['total_amount']:,.2f})"
                    for i, m in enumerate(matches[:5])
                ])
                
                context = f"Multiple dealers found. Please reply with the number:\n\n{dealer_list}\n\nExample: Reply '1' for the first dealer"
                intent = "dealer_selection"
        
        # ==========================================================
        # FIX 7: Better "No Data Found" Handling
        # ==========================================================
        if not ai_result.get("success", True) or context == "No logistics data found":
            ai_reply = get_no_data_message()
            
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
        print(f"📝 Context Length: {len(context)} chars")
        print(f"📝 Context Preview: {context[:300]}...")
        print("="*80)
        
        # ==========================================================
        # GET AI RESPONSE FROM CLAUDE
        # ==========================================================
        ai_reply = ask_claude(prompt)
        
        # FIX 10: Post-process response to ensure business rules are applied
        # Replace any raw field names that might have slipped through
        ai_reply = ai_reply.replace("PGI Completed", "Delivered")
        ai_reply = ai_reply.replace("POD Received", "Dealer Acknowledged")
        ai_reply = ai_reply.replace("POD Pending", "Awaiting Acknowledgement")
        ai_reply = ai_reply.replace("PGI Pending", "Pending Dispatch")
        
    except Exception as e:
        print(f"❌ Error in logistics query: {str(e)}")
        ai_reply = (
            "I'm having trouble accessing the logistics database right now. "
            "Please try again in a moment. If the issue persists, contact support."
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
    print(f"💬 Reply Preview: {ai_reply[:200]}...")
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
        "logistics_integration": True,
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
