# ==========================================================
# FILE: app/routes/webhook.py (ENTERPRISE GRADE v3.0)
# PROJECT: AI WhatsApp Logistics Copilot
# ==========================================================

import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, asdict

from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session
from loguru import logger

from app.config import WHATSAPP_VERIFY_TOKEN
from app.services.whatsapp_service import (
    parse_whatsapp_message,
    verify_webhook,
    send_text_message,
    send_structured_message
)
from app.services.ai_query_service import AIQueryService, get_ai_query_service
from app.services.session_service import (
    SessionService,
    get_session_service,
    UserRole,
    ConversationContext
)
from app.services.analytics_service import get_analytics_service
from app.services.query_analytics_service import QueryAnalyticsService
from app.services.user_access_service import UserAccessService, get_user_access_service
from app.services.semantic_search_service import SemanticSearchService, get_semantic_search_service
from app.database import get_db

# ==========================================================
# ROUTER
# ==========================================================

router = APIRouter(
    prefix="/webhook",
    tags=["WhatsApp Webhook"]
)

# ==========================================================
# QUERY CATEGORIES
# ==========================================================

class QueryCategory(str, Enum):
    EXECUTIVE = "executive"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    DN = "dn"
    POD = "pod"
    RCA = "root_cause"
    FORECAST = "forecast"
    RECOMMENDATION = "recommendation"
    RISK = "risk"
    GENERAL = "general"


# ==========================================================
# EXECUTIVE COMMANDS (EXPANDED)
# ==========================================================

EXECUTIVE_COMMANDS = {
    "executive summary": "executive_summary",
    "what should i focus on": "executive_focus",
    "today's priorities": "executive_focus",
    "network health": "network_health",
    "network health score": "network_health",
    "revenue at risk": "revenue_risk",
    "inventory at risk": "inventory_risk",
    "biggest risk": "biggest_risk",
    "top risk": "biggest_risk",
    "weekly review": "weekly_review",
    "management review": "weekly_review",
    "executive review": "weekly_review",
    "ceo briefing": "ceo_briefing",
    "command center": "ceo_briefing",
    "morning briefing": "morning_briefing",
    "daily briefing": "morning_briefing",
    "today's status": "morning_briefing",
    "executive update": "morning_briefing",
    "critical issue": "critical_alert",
    "top risks": "critical_alert",
    "urgent items": "critical_alert",
    "red alerts": "critical_alert"
}

WEEKLY_REVIEW_KEYWORDS = ["weekly", "management review", "executive review", "this week"]

# ==========================================================
# FORECAST COMMANDS
# ==========================================================

FORECAST_COMMANDS = {
    "next month pod": "pod_forecast",
    "pod forecast": "pod_forecast",
    "future risk": "risk_forecast",
    "dealer risk forecast": "dealer_forecast",
    "warehouse forecast": "warehouse_forecast",
    "city forecast": "city_forecast",
    "forecast": "general_forecast"
}

# ==========================================================
# ROOT CAUSE COMMANDS
# ==========================================================

ROOT_CAUSE_COMMANDS = {
    "why are pods increasing": "pod_rca",
    "why is karachi delayed": "city_rca",
    "why is warehouse delayed": "warehouse_rca",
    "what is causing delays": "general_rca",
    "root cause": "general_rca"
}

# ==========================================================
# RECOMMENDATION COMMANDS
# ==========================================================

RECOMMENDATION_COMMANDS = {
    "how can we improve": "general_recommendation",
    "what should management do": "management_recommendation",
    "recommended actions": "general_recommendation",
    "action plan": "action_plan"
}

# ==========================================================
# FOLLOW-UP PATTERNS (EXPANDED)
# ==========================================================

FOLLOW_UP_PATTERNS = [
    "it", "they", "that", "this", "those", "these",
    "how", "why", "what", "where", "when", "who",
    "what about", "tell me more", "explain", "details",
    "show details", "more info", "elaborate",
    "improve", "fix", "solve", "recover", "resolve",
    "which one", "which dealer", "which city", "which warehouse",
    "what next", "next steps", "then what",
    "can you explain", "please explain"
]

# ==========================================================
# DASHBOARD COMMANDS (Role-based)
# ==========================================================

DASHBOARD_COMMANDS = ["dashboard", "show dashboard", "my dashboard", "view dashboard"]

# ==========================================================
# CRITICAL ALERT KEYWORDS
# ==========================================================

CRITICAL_ALERT_KEYWORDS = [
    "critical", "urgent", "emergency", "red alert",
    "immediate attention", "escalate", "crisis"
]

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def get_user_role_from_db(phone_number: str, db: Session) -> Dict[str, Any]:
    """Get user role from database (dynamic role management)"""
    try:
        access_service = get_user_access_service(db)
        user_access = access_service.get_user_by_phone(phone_number)
        
        if user_access:
            return {
                "role": UserRole(user_access.role),
                "department": user_access.department,
                "name": user_access.name,
                "access_level": user_access.access_level
            }
    except Exception as e:
        logger.warning(f"Failed to get user role from DB: {e}")
    
    # Fallback to hardcoded mapping
    return USER_ROLE_MAPPING.get(phone_number, USER_ROLE_MAPPING["default"])

def classify_query(question: str) -> QueryCategory:
    """Classify query into category for routing"""
    question_lower = question.lower()
    
    # Check for executive commands
    if any(cmd in question_lower for cmd in EXECUTIVE_COMMANDS.keys()):
        return QueryCategory.EXECUTIVE
    
    # Check for forecast commands
    if any(cmd in question_lower for cmd in FORECAST_COMMANDS.keys()):
        return QueryCategory.FORECAST
    
    # Check for root cause commands
    if any(cmd in question_lower for cmd in ROOT_CAUSE_COMMANDS.keys()):
        return QueryCategory.RCA
    
    # Check for recommendation commands
    if any(cmd in question_lower for cmd in RECOMMENDATION_COMMANDS.keys()):
        return QueryCategory.RECOMMENDATION
    
    # Check for risk keywords
    if any(word in question_lower for word in ["risk", "critical", "urgent"]):
        return QueryCategory.RISK
    
    # Check for dealer queries
    if "dealer" in question_lower or "customer" in question_lower:
        return QueryCategory.DEALER
    
    # Check for warehouse queries
    if "warehouse" in question_lower or "godown" in question_lower:
        return QueryCategory.WAREHOUSE
    
    # Check for city queries
    if "city" in question_lower or any(city in question_lower for city in ["karachi", "lahore", "islamabad"]):
        return QueryCategory.CITY
    
    # Check for DN queries
    if "dn" in question_lower or "delivery note" in question_lower:
        return QueryCategory.DN
    
    # Check for POD queries
    if "pod" in question_lower or "proof of delivery" in question_lower:
        return QueryCategory.POD
    
    return QueryCategory.GENERAL

def get_role_dashboard(user_role: UserRole) -> str:
    """Get role-appropriate dashboard command"""
    if user_role == UserRole.CEO:
        return "network health"
    elif user_role == UserRole.MANAGER:
        return "executive summary"
    elif user_role == UserRole.WAREHOUSE:
        return "warehouse performance"
    elif user_role == UserRole.DEALER:
        return "dealer performance"
    else:
        return "help"

def format_structured_dashboard(data: Dict[str, Any], dashboard_type: str) -> str:
    """Format structured dashboard responses for WhatsApp"""
    
    if dashboard_type == "dealer":
        return f"""
╔══════════════════════════════╗
║     📊 DEALER DASHBOARD      ║
╚══════════════════════════════╝

📛 *Name:* {data.get('dealer_name', 'Unknown')}
📊 *Health Score:* {data.get('health_score', 0)}/100
⚠️ *Risk Level:* {data.get('risk_level', 'Unknown')}

📦 *Metrics:*
• Total DNs: {data.get('total_dns', 0)}
• Pending DNs: {data.get('pending_dns', 0)}
• POD Pending: {data.get('pod_pending_dns', 0)}

💰 *Financial:*
• Total Value: Rs {data.get('total_value', 0):,.2f}
• Pending Value: Rs {data.get('pending_value', 0):,.2f}

💡 *Recommendation:* {data.get('recommendation', 'Monitor regularly')}
"""
    
    elif dashboard_type == "warehouse":
        return f"""
╔══════════════════════════════╗
║    🏭 WAREHOUSE DASHBOARD    ║
╚══════════════════════════════╝

📛 *Name:* {data.get('warehouse', 'Unknown')}
⚡ *Efficiency:* {data.get('efficiency_score', 0)}/100
⚠️ *Risk Level:* {data.get('risk_level', 'Unknown')}

📦 *Metrics:*
• Total DNs: {data.get('total_dns', 0)}
• Pending DNs: {data.get('pending_dns', 0)}
• POD Pending: {data.get('pod_pending_dns', 0)}

🔍 *Bottlenecks:*
{chr(10).join([f'• {b}' for b in data.get('bottlenecks', [])[:3]])}

💡 *Action:* {data.get('recommendation', 'Optimize operations')}
"""
    
    elif dashboard_type == "executive":
        return f"""
╔══════════════════════════════╗
║   👑 EXECUTIVE DASHBOARD    ║
╚══════════════════════════════╝

📊 *Network Health:* {data.get('network_health', 0)}/100
💰 *Revenue At Risk:* {data.get('revenue_at_risk_formatted', 'Rs 0')}
📦 *Inventory At Risk:* {data.get('inventory_at_risk', 0):,.0f} units

🚨 *Top Risks:*
• Dealer: {data.get('top_risk_dealer', 'None')}
• City: {data.get('top_risk_city', 'None')}

💡 *Priority Action:* Escalate top 20 dealers immediately
"""
    
    else:
        return data.get('formatted_message', str(data))

def should_route_to_semantic_search(confidence: int, question: str) -> bool:
    """Determine if query should be routed to semantic search"""
    if confidence < 60:
        return True
    if len(question.split()) <= 3 and confidence < 75:
        return True
    return False

def extract_conversation_summary(session) -> str:
    """Extract conversation summary from session history"""
    if not session.conversation_history:
        return "No previous conversation"
    
    # Extract unique topics discussed
    topics = set()
    topics_mapping = {
        'dealer': 'dealer',
        'city': 'city', 
        'warehouse': 'warehouse',
        'dn': 'delivery note',
        'pod': 'POD',
        'risk': 'risk'
    }
    
    history_list = session.conversation_history or []
    for entry in history_list[-5:]:  # Last 5 entries
        question = entry.get('question', '').lower()
        for key, topic in topics_mapping.items():
            if key in question:
                topics.add(topic)
    
    if topics:
        return f"User has discussed: {', '.join(topics)}"
    return "Limited conversation history"

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
        logger.info(f"Webhook verified successfully")
        return int(result["challenge"])

    logger.warning(f"Webhook verification failed")
    return {"success": False, "message": "Verification failed"}

# ==========================================================
# RECEIVE WHATSAPP MESSAGE (ENTERPRISE GRADE v3.0)
# ==========================================================

@router.post("/")
async def receive_message(
    request: Request,
    db: Session = Depends(get_db)
):
    start_time = time.time()
    ai_start_time = None
    db_start_time = None
    
    # Parse incoming message
    payload = await request.json()
    parsed_message = parse_whatsapp_message(payload)

    if not parsed_message:
        logger.debug("No message found in payload")
        return {"success": True, "message": "No message found"}

    phone_number = parsed_message["from"]
    customer_message = parsed_message["text"]
    
    # Get user role from database (dynamic)
    user_metadata = get_user_role_from_db(phone_number, db)
    user_role = user_metadata["role"]
    department = user_metadata["department"]
    user_name = user_metadata["name"]
    access_level = user_metadata.get("access_level", 1)
    
    # Initialize services
    session_service = get_session_service(db)
    ai_service = get_ai_query_service(db)
    analytics_service = get_analytics_service(db)
    query_analytics = QueryAnalyticsService(db)
    semantic_search = get_semantic_search_service()
    
    # Get or create session
    db_start_time = time.time()
    session = session_service.get_or_create_session(
        phone_number=phone_number,
        user_role=user_role.value if user_role else "guest",
        user_name=user_name,
        department=department
    )
    
    # Update last activity
    session_service.update_activity(phone_number)
    
    # Log incoming message
    logger.info("=" * 80)
    logger.info(f"📱 WHATSAPP MESSAGE RECEIVED")
    logger.info(f"📞 From: {phone_number} ({user_name}, {user_role.value if user_role else 'guest'})")
    logger.info(f"💬 Message: {customer_message}")
    logger.info(f"📊 Session Context: Dealer={session.selected_dealer}, City={session.selected_city}, Warehouse={session.selected_warehouse}")
    logger.info("=" * 80)
    
    # ==========================================================
    # CHECK FOR DEALER SELECTION RESPONSE
    # ==========================================================
    selection_result = session_service.handle_dealer_selection(phone_number, customer_message)
    
    if selection_result.get("handled"):
        selected_dealer = selection_result.get("selected_dealer")
        
        # Get dealer dashboard using AI service
        result = ai_service.process_query(
            question=f"Show dealer {selected_dealer} dashboard",
            user_phone=phone_number,
            user_role=user_role.value if user_role else "guest"
        )
        
        ai_reply = format_structured_dashboard(result, "dealer") if result.get("structured_data") else result.get("response", f"Dealer '{selected_dealer}' information retrieved.")
        intent = result.get("question_type", "dealer_lookup_selected")
        confidence = result.get("confidence", 85)
        
        # Log analytics
        query_analytics.log_query(
            phone_number=phone_number,
            question=customer_message,
            intent=intent,
            entity=selected_dealer,
            response_time_ms=int((time.time() - start_time) * 1000),
            ai_used=result.get("ai_used", False),
            confidence=confidence,
            user_role=user_role.value if user_role else "guest",
            provider=result.get("provider_used", "unknown"),
            category="dealer"
        )
        
        # Send response
        whatsapp_response = send_structured_message(phone_number, ai_reply)
        
        logger.info(f"✅ Dealer selection response sent to {phone_number}")
        
        return {
            "success": True,
            "customer_message": customer_message,
            "ai_reply": ai_reply,
            "intent": intent,
            "confidence": confidence,
            "ai_used": result.get("ai_used", False),
            "processing_time_ms": result.get("processing_time_ms", 0),
            "provider": result.get("provider_used", "unknown"),
            "whatsapp_response": whatsapp_response
        }
    
    # ==========================================================
    # CHECK FOR CLEAR CONTEXT COMMAND
    # ==========================================================
    if customer_message.lower() in ["clear context", "reset", "new conversation", "clear"]:
        session_service.clear_context(phone_number)
        ai_reply = "✅ Conversation context cleared. Starting fresh. How can I help you?"
        
        whatsapp_response = send_structured_message(phone_number, ai_reply)
        
        logger.info(f"Context cleared for {phone_number}")
        
        return {
            "success": True,
            "customer_message": customer_message,
            "ai_reply": ai_reply,
            "intent": "context_cleared",
            "confidence": 100,
            "ai_used": False,
            "processing_time_ms": int((time.time() - start_time) * 1000),
            "provider": "system",
            "whatsapp_response": whatsapp_response
        }
    
    # ==========================================================
    # CLASSIFY QUERY
    # ==========================================================
    query_category = classify_query(customer_message)
    logger.info(f"📊 Query classified as: {query_category.value}")
    
    # ==========================================================
    # CHECK FOR CRITICAL ALERTS (Priority 11)
    # ==========================================================
    if any(keyword in customer_message.lower() for keyword in CRITICAL_ALERT_KEYWORDS):
        try:
            critical_risks = analytics_service.top_risk_dealers(5)
            if critical_risks:
                ai_reply = f"""
🚨 *CRITICAL ALERT*

⚠️ *Immediate Attention Required*

*Top 5 Risk Dealers:*
{chr(10).join([f"{i+1}. {d['dealer']} - {d['risk_score']}% risk" for i, d in enumerate(critical_risks[:5])])}

🎯 *Recommended Action:* Escalate immediately to dealer management team
"""
                intent = "critical_alert"
                confidence = 95
                
                whatsapp_response = send_structured_message(phone_number, ai_reply)
                
                query_analytics.log_query(
                    phone_number=phone_number,
                    question=customer_message,
                    intent=intent,
                    entity=None,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    ai_used=False,
                    confidence=confidence,
                    user_role=user_role.value if user_role else "guest",
                    provider="alert_engine",
                    category="risk"
                )
                
                return {
                    "success": True,
                    "customer_message": customer_message,
                    "ai_reply": ai_reply,
                    "intent": intent,
                    "confidence": confidence,
                    "ai_used": False,
                    "processing_time_ms": int((time.time() - start_time) * 1000),
                    "provider": "alert_engine",
                    "whatsapp_response": whatsapp_response
                }
        except Exception as e:
            logger.error(f"Critical alert error: {e}")
    
    # ==========================================================
    # CHECK FOR EXECUTIVE QUERIES (Role-based)
    # ==========================================================
    executive_command = is_executive_query(customer_message)
    
    if executive_command and user_role in [UserRole.CEO, UserRole.MANAGER]:
        logger.info(f"🎯 Executive query detected: {executive_command}")
        
        try:
            if executive_command == "network_health":
                health_data = analytics_service.network_health_score()
                ai_reply = format_executive_response(health_data, "network_health")
                intent = "network_health"
                confidence = 95
                
            elif executive_command == "revenue_risk":
                revenue_data = analytics_service.revenue_at_risk()
                ai_reply = format_executive_response(revenue_data, "revenue_risk")
                intent = "revenue_risk"
                confidence = 95
                
            elif executive_command == "inventory_risk":
                inventory_data = analytics_service.inventory_at_risk()
                ai_reply = format_structured_dashboard(inventory_data, "executive")
                intent = "inventory_risk"
                confidence = 95
                
            elif executive_command == "biggest_risk":
                summary = analytics_service.executive_summary()
                ai_reply = format_structured_dashboard(summary, "executive")
                intent = "biggest_risk"
                confidence = 90
                
            elif executive_command == "morning_briefing":
                summary = analytics_service.executive_summary()
                network = analytics_service.network_health_score()
                ai_reply = f"""
🌅 *MORNING BRIEFING* - {datetime.now().strftime('%Y-%m-%d')}

📊 *Network Health: {network.get('score', 0)}/100*

💰 *Revenue At Risk: {summary.get('revenue_at_risk_formatted', 'Rs 0')}*

🚨 *Top 3 Priorities Today:*
1. Recover POD from top 20 dealers
2. Escalate {summary.get('top_risk_dealer', 'Unknown')}
3. Focus on {summary.get('top_risk_city', 'Unknown')}

💡 *Good morning! Let's focus on reducing the backlog today.*
"""
                intent = "morning_briefing"
                confidence = 95
                
            elif executive_command == "weekly_review" or is_weekly_review_query(customer_message):
                executive_data = analytics_service.executive_summary()
                ai_reply = format_executive_response(executive_data, "weekly_review")
                intent = "weekly_review"
                confidence = 85
                
            elif executive_command == "ceo_briefing":
                summary = analytics_service.executive_summary()
                ai_reply = format_structured_dashboard(summary, "executive")
                intent = "ceo_briefing"
                confidence = 90
                
            else:
                summary = analytics_service.executive_summary()
                ai_reply = format_executive_response(summary, "executive_summary")
                intent = "executive_summary"
                confidence = 85
            
            # Update session with executive mode
            session_service.update_session_context(
                phone_number,
                executive_mode=True,
                last_dashboard="executive",
                last_analysis_type=intent
            )
            
            # Log analytics
            query_analytics.log_query(
                phone_number=phone_number,
                question=customer_message,
                intent=intent,
                entity=None,
                response_time_ms=int((time.time() - start_time) * 1000),
                ai_used=False,
                confidence=confidence,
                user_role=user_role.value if user_role else "guest",
                provider="analytics_service",
                category="executive"
            )
            
            # Send response
            whatsapp_response = send_structured_message(phone_number, ai_reply)
            
            logger.info(f"✅ Executive response sent to {phone_number}")
            
            return {
                "success": True,
                "customer_message": customer_message,
                "ai_reply": ai_reply,
                "intent": intent,
                "confidence": confidence,
                "ai_used": False,
                "processing_time_ms": int((time.time() - start_time) * 1000),
                "provider": "analytics_service",
                "whatsapp_response": whatsapp_response
            }
            
        except Exception as e:
            logger.error(f"Executive query error: {e}")
    
    # ==========================================================
    # CHECK FOR DASHBOARD COMMANDS (Role-based - Priority 8)
    # ==========================================================
    if any(cmd in customer_message.lower() for cmd in DASHBOARD_COMMANDS):
        dashboard_type = get_role_dashboard(user_role)
        
        if dashboard_type == "network health":
            health_data = analytics_service.network_health_score()
            ai_reply = format_structured_dashboard(health_data, "executive")
        elif dashboard_type == "warehouse performance":
            warehouses = analytics_service.warehouse_rankings(5)
            ai_reply = format_structured_dashboard({"warehouses": warehouses}, "warehouse")
        elif dashboard_type == "dealer performance":
            dealers = analytics_service.dealer_rankings(10)
            ai_reply = format_structured_dashboard({"dealers": dealers}, "dealer")
        else:
            summary = analytics_service.executive_summary()
            ai_reply = format_structured_dashboard(summary, "executive")
        
        intent = "role_dashboard"
        confidence = 90
        
        whatsapp_response = send_structured_message(phone_number, ai_reply)
        
        query_analytics.log_query(
            phone_number=phone_number,
            question=customer_message,
            intent=intent,
            entity=None,
            response_time_ms=int((time.time() - start_time) * 1000),
            ai_used=False,
            confidence=confidence,
            user_role=user_role.value if user_role else "guest",
            provider="analytics_service",
            category="dashboard"
        )
        
        return {
            "success": True,
            "customer_message": customer_message,
            "ai_reply": ai_reply,
            "intent": intent,
            "confidence": confidence,
            "ai_used": False,
            "processing_time_ms": int((time.time() - start_time) * 1000),
            "provider": "analytics_service",
            "whatsapp_response": whatsapp_response
        }
    
    # ==========================================================
    # INJECT CONTEXT INTO QUESTION (Enhanced - Priority 3)
    # ==========================================================
    context = session_service.get_context(phone_number)
    enhanced_question = inject_context_into_question(customer_message, context)
    
    if enhanced_question != customer_message:
        logger.info(f"🔄 Context injected: '{customer_message}' -> '{enhanced_question}'")
    
    # ==========================================================
    # GET CONVERSATION SUMMARY (Priority 2)
    # ==========================================================
    conversation_summary = extract_conversation_summary(session)
    logger.info(f"📝 Conversation summary: {conversation_summary}")
    
    # ==========================================================
    # PROCESS QUERY WITH AI QUERY SERVICE (FULL CONTEXT - Priority 1)
    # ==========================================================
    ai_start_time = time.time()
    
    try:
        # Prepare full context for AI service (NOW ACTUALLY PASSED)
        ai_service_context = {
            "selected_dealer": context.selected_dealer,
            "selected_city": context.selected_city,
            "selected_warehouse": context.selected_warehouse,
            "selected_dn": context.selected_dn,
            "last_intent": context.last_intent,
            "last_question": context.last_question,
            "last_response": context.last_response,
            "user_role": user_role.value if user_role else "guest",
            "executive_mode": context.executive_mode,
            "conversation_summary": conversation_summary,
            "conversation_history": session.conversation_history[-3:] if session.conversation_history else [],
            "department": department,
            "access_level": access_level
        }
        
        # Process query with FULL context
        result = ai_service.process_query(
            question=enhanced_question,
            user_phone=phone_number,
            user_role=user_role.value if user_role else "guest",
            context=ai_service_context  # NOW PASSED!
        )
        
        # Extract response data
        ai_reply = result.get("response", "Unable to generate response.")
        intent = result.get("question_type", "general")
        confidence = result.get("confidence", 75)
        ai_used = result.get("ai_used", False)
        provider = result.get("provider_used", "unknown")
        entity = result.get("entity")
        
        # ==========================================================
        # CONFIDENCE ROUTING (Priority 5)
        # ==========================================================
        if confidence < 60:
            # Try semantic search for better matches
            semantic_result = semantic_search.search(customer_message, top_k=3)
            if semantic_result and semantic_result.get("matches"):
                ai_reply = f"""
🔍 *I found multiple possibilities:*

Did you mean:

{chr(10).join([f"{i+1}. {m['text']}" for i, m in enumerate(semantic_result['matches'][:3])])}

Please select the number or be more specific.
"""
                confidence = 85
                ai_used = True
                provider = "semantic_search"
        
        # Update session with new context and conversation history
        if intent == "CITY" and entity:
            session_service.update_session_context(
                phone_number,
                selected_city=entity,
                last_intent=intent,
                last_question=enhanced_question,
                last_response=ai_reply[:500],
                last_analysis_type="city_analysis"
            )
        elif intent == "DEALER" and entity:
            session_service.update_session_context(
                phone_number,
                selected_dealer=entity,
                last_intent=intent,
                last_question=enhanced_question,
                last_response=ai_reply[:500],
                last_analysis_type="dealer_analysis"
            )
        elif intent == "WAREHOUSE" and entity:
            session_service.update_session_context(
                phone_number,
                selected_warehouse=entity,
                last_intent=intent,
                last_question=enhanced_question,
                last_response=ai_reply[:500],
                last_analysis_type="warehouse_analysis"
            )
        elif intent == "DN" and entity:
            session_service.update_session_context(
                phone_number,
                selected_dn=entity,
                last_intent=intent,
                last_question=enhanced_question,
                last_response=ai_reply[:500]
            )
        else:
            session_service.update_session_context(
                phone_number,
                last_intent=intent,
                last_question=enhanced_question,
                last_response=ai_reply[:500]
            )
        
        # Add to conversation history
        session_service.add_to_conversation_history(
            phone_number,
            question=enhanced_question,
            response=ai_reply[:500],
            intent=intent
        )
        
        # Check if this was a fuzzy match that needs dealer selection
        if result.get("fuzzy"):
            matches = result.get("matches", [])
            if matches:
                session_service.set_pending_dealer_selection(phone_number, matches)
                logger.info(f"📋 Multiple dealers found, awaiting selection from user")
        
        # Log analytics with category
        query_analytics.log_query(
            phone_number=phone_number,
            question=customer_message,
            intent=intent,
            entity=entity,
            response_time_ms=int((time.time() - start_time) * 1000),
            ai_used=ai_used,
            confidence=confidence,
            user_role=user_role.value if user_role else "guest",
            provider=provider,
            category=query_category.value
        )
        
        # Log processing details
        logger.info("=" * 80)
        logger.info(f"🤖 AI QUERY SERVICE PROCESSED")
        logger.info(f"📊 Category: {query_category.value}")
        logger.info(f"📊 Intent: {intent}")
        logger.info(f"🎯 Entity: {entity}")
        logger.info(f"🤖 AI Used: {ai_used}")
        logger.info(f"📈 Confidence: {confidence}%")
        logger.info(f"🔧 Provider: {provider}")
        logger.info(f"⏱️ AI Time: {int((time.time() - ai_start_time) * 1000)}ms")
        logger.info(f"📝 Response Preview: {ai_reply[:300]}...")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"❌ Error in AI Query Service: {str(e)}")
        import traceback
        traceback.print_exc()
        
        ai_reply = (
            "⚠️ I'm having trouble accessing the logistics database right now. "
            "Please try again in a moment. If the issue persists, contact support."
        )
        intent = "error"
        confidence = 0
        ai_used = False
        provider = "error"
    
    # ==========================================================
    # SEND STRUCTURED WHATSAPP RESPONSE (Priority 10)
    # ==========================================================
    # Use structured response for better UX
    if intent in ["dealer", "dealer_lookup", "dealer_analysis"]:
        formatted_reply = format_structured_dashboard(result.get("structured_data", {}), "dealer")
    elif intent in ["warehouse", "warehouse_analysis"]:
        formatted_reply = format_structured_dashboard(result.get("structured_data", {}), "warehouse")
    elif intent in ["executive", "executive_summary", "network_health"]:
        formatted_reply = format_structured_dashboard(result.get("structured_data", {}), "executive")
    else:
        formatted_reply = ai_reply
    
    whatsapp_response = send_structured_message(phone_number, formatted_reply)
    
    # Log response
    logger.info("=" * 80)
    logger.info(f"✅ RESPONSE SENT TO {phone_number}")
    logger.info(f"💬 Reply Preview: {formatted_reply[:200]}...")
    logger.info(f"📊 Intent: {intent}")
    logger.info(f"📈 Confidence: {confidence}%")
    logger.info(f"⏱️ Total Time: {int((time.time() - start_time) * 1000)}ms")
    logger.info("=" * 80 + "\n")

    return {
        "success": True,
        "customer_message": customer_message,
        "ai_reply": formatted_reply,
        "intent": intent,
        "confidence": confidence,
        "ai_used": ai_used,
        "processing_time_ms": int((time.time() - start_time) * 1000),
        "ai_processing_time_ms": int((time.time() - ai_start_time) * 1000) if ai_start_time else 0,
        "provider": provider,
        "category": query_category.value,
        "whatsapp_response": whatsapp_response
    }


# ==========================================================
# HELPER FUNCTIONS (Preserved from previous implementation)
# ==========================================================

def format_executive_response(data: Dict[str, Any], response_type: str) -> str:
    """Format executive responses with emojis and structure for WhatsApp"""
    
    if response_type == "network_health":
        health = data.get("network_health", {}) if "network_health" in data else data
        return f"""
📊 *NETWORK HEALTH REPORT*

{health.get('icon', '📊')} *Score: {health.get('score', 0)}/100* ({health.get('category', 'Unknown')})

*Components:*
✅ POD Compliance: {health.get('pod_compliance', 0)}%
🚚 Delivery Compliance: {health.get('delivery_compliance', 0)}%
🏪 Dealer Health: {health.get('dealer_score', 0)}/100
🏭 Warehouse Health: {health.get('warehouse_score', 0)}/100
🌆 City Health: {health.get('city_score', 0)}/100

💡 *Recommendation*: {health.get('category', 'Monitor')} level - {'Immediate action required' if health.get('score', 100) < 70 else 'Maintain current focus'}
"""
    
    elif response_type == "revenue_risk":
        return f"""
💰 *REVENUE AT RISK*

{data.get('icon', '⚠️')} *Amount: {data.get('formatted', 'Rs 0')}*
📊 *Risk Level: {data.get('risk_level', 'Unknown')}*

*Breakdown:*
• Pending Revenue: Rs {data.get('pending_revenue', 0):,.2f}
• POD Pending Revenue: Rs {data.get('pod_pending_revenue', 0):,.2f}

🎯 *Action*: {'Escalate immediately' if data.get('risk_level') == 'Critical' else 'Monitor closely'}
"""
    
    elif response_type == "executive_summary":
        return f"""
👑 *EXECUTIVE COMMAND CENTER*

{data.get('formatted_message', data.get('response', 'No summary available'))}
"""
    
    elif response_type == "weekly_review":
        return f"""
📅 *WEEKLY MANAGEMENT REVIEW*

{data.get('formatted_message', data.get('response', 'No review available'))}
"""
    
    else:
        return data.get("response", data.get("formatted_message", "No response available"))

def inject_context_into_question(question: str, context: ConversationContext) -> str:
    """Inject context into question for follow-up queries (Enhanced - Priority 3)"""
    
    question_lower = question.lower()
    
    # Check if this is a follow-up using expanded patterns
    is_follow_up = any(pattern in question_lower for pattern in FOLLOW_UP_PATTERNS)
    
    # Also check for very short questions (likely follow-ups)
    if len(question.split()) <= 4:
        is_follow_up = True
    
    # Check for specific follow-up types
    if question_lower in ["details", "more", "elaborate", "explain"]:
        is_follow_up = True
    
    if not is_follow_up:
        return question
    
    # Inject context based on last intent with enhanced logic
    if context.last_intent == "CITY" and context.selected_city:
        if any(word in question_lower for word in ["improve", "fix", "solve", "recover"]):
            return f"How can we improve {context.selected_city}? {question}"
        elif "why" in question_lower or "reason" in question_lower or "cause" in question_lower:
            return f"What are the reasons for issues in {context.selected_city}? {question}"
        elif "which" in question_lower or "who" in question_lower:
            return f"Regarding {context.selected_city}: which specific entity is causing the issue? {question}"
        return f"Regarding {context.selected_city}: {question}"
    
    elif context.last_intent == "DEALER" and context.selected_dealer:
        if any(word in question_lower for word in ["improve", "fix", "solve", "recover"]):
            return f"How can we improve {context.selected_dealer}? {question}"
        elif "issue" in question_lower or "problem" in question_lower:
            return f"What are the specific issues with {context.selected_dealer}? {question}"
        elif "why" in question_lower or "reason" in question_lower:
            return f"Why is {context.selected_dealer} having problems? {question}"
        elif "details" in question_lower or "more" in question_lower:
            return f"Provide more details about {context.selected_dealer}: {question}"
        return f"Regarding {context.selected_dealer}: {question}"
    
    elif context.last_intent == "WAREHOUSE" and context.selected_warehouse:
        if "backlog" in question_lower or "pending" in question_lower:
            return f"What is the backlog at warehouse {context.selected_warehouse}? {question}"
        elif "efficiency" in question_lower or "performance" in question_lower:
            return f"How is warehouse {context.selected_warehouse} performing? {question}"
        return f"Regarding warehouse {context.selected_warehouse}: {question}"
    
    elif context.last_intent == "DN" and context.selected_dn:
        if "status" in question_lower:
            return f"What is the status of DN {context.selected_dn}? {question}"
        return f"Regarding DN {context.selected_dn}: {question}"
    
    return question

def is_executive_query(question: str) -> Optional[str]:
    """Check if query is an executive-level query"""
    question_lower = question.lower().strip()
    
    for keyword, command in EXECUTIVE_COMMANDS.items():
        if keyword in question_lower:
            return command
    
    return None

def is_weekly_review_query(question: str) -> bool:
    """Check if query is asking for weekly review"""
    question_lower = question.lower()
    return any(keyword in question_lower for keyword in WEEKLY_REVIEW_KEYWORDS)


# ==========================================================
# TEST AND STATUS ENDPOINTS
# ==========================================================

@router.get("/test")
async def test_webhook():
    return {
        "success": True,
        "message": "Webhook is active - Enterprise v3.0",
        "features": [
            "PostgreSQL Session Storage",
            "Full Context Injection",
            "Conversation Memory Summary",
            "Dynamic Role Management",
            "Confidence Routing",
            "Semantic Search Escalation",
            "Executive Dashboard",
            "Role-Based Dashboards",
            "Query Categorization",
            "Structured Responses",
            "Critical Alert Engine",
            "Forecast Commands",
            "Root Cause Commands",
            "Recommendation Commands",
            "Performance Monitoring"
        ]
    }


@router.post("/demo")
async def demo_message(db: Session = Depends(get_db)):
    customer_message = "How many pending deliveries?"
    
    ai_service = get_ai_query_service(db)
    session_service = get_session_service(db)
    
    result = ai_service.process_query(
        question=customer_message,
        user_phone="demo_user",
        user_role="guest"
    )
    
    ai_reply = result.get("response", "Unable to generate response.")
    intent = result.get("question_type", "general")
    
    return {
        "success": True,
        "customer_message": customer_message,
        "ai_reply": ai_reply,
        "intent": intent,
        "confidence": result.get("confidence", 75),
        "ai_used": result.get("ai_used", False),
        "processing_time_ms": result.get("processing_time_ms", 0),
        "provider": result.get("provider_used", "unknown")
    }


@router.get("/status")
async def webhook_status(db: Session = Depends(get_db)):
    session_service = get_session_service(db)
    active_sessions = session_service.get_active_sessions_count(hours=24)
    
    return {
        "service": "WhatsApp Webhook - Enterprise v3.0",
        "status": "running",
        "verify_token": bool(WHATSAPP_VERIFY_TOKEN),
        "postgres_session_storage": True,
        "full_context_injection": True,
        "conversation_memory": True,
        "dynamic_roles": True,
        "semantic_search": True,
        "confidence_routing": True,
        "active_sessions_24h": active_sessions,
        "features": {
            "executive_commands": True,
            "forecast_commands": True,
            "root_cause_commands": True,
            "recommendation_commands": True,
            "alert_engine": True,
            "role_dashboards": True,
            "structured_responses": True,
            "performance_monitoring": True
        }
    }


@router.post("/clear-session/{phone_number}")
async def clear_session(phone_number: str, db: Session = Depends(get_db)):
    """Clear user session for testing."""
    session_service = get_session_service(db)
    session_service.clear_context(phone_number)
    
    logger.info(f"Session cleared for {phone_number}")
    
    return {
        "success": True,
        "message": f"Session cleared for {phone_number}"
    }


@router.get("/test-logistics")
async def test_logistics_query(
    q: str,
    db: Session = Depends(get_db)
):
    """Test endpoint for logistics queries."""
    ai_service = get_ai_query_service(db)
    
    result = ai_service.process_query(
        question=q,
        user_phone="test_user",
        user_role="guest"
    )
    
    return {
        "question": q,
        "question_type": result.get("question_type", "unknown"),
        "response": result.get("response", "No response"),
        "confidence": result.get("confidence", 75),
        "ai_used": result.get("ai_used", False),
        "processing_time_ms": result.get("processing_time_ms", 0),
        "provider": result.get("provider_used", "unknown"),
        "success": result.get("success", False)
    }


@router.get("/session/{phone_number}")
async def get_session(phone_number: str, db: Session = Depends(get_db)):
    """Get user session for debugging."""
    session_service = get_session_service(db)
    session = session_service.get_session_by_phone(phone_number)
    
    if not session:
        return {"phone_number": phone_number, "session": None, "message": "No session found"}
    
    return {
        "phone_number": phone_number,
        "session": {
            "user_role": session.user_role,
            "user_name": session.user_name,
            "department": session.department,
            "selected_dealer": session.selected_dealer,
            "selected_city": session.selected_city,
            "selected_warehouse": session.selected_warehouse,
            "selected_dn": session.selected_dn,
            "last_intent": session.last_intent,
            "last_question": session.last_question,
            "last_response_preview": session.last_response[:200] if session.last_response else None,
            "executive_mode": session.executive_mode,
            "conversation_history_length": len(session.conversation_history) if session.conversation_history else 0,
            "conversation_summary": extract_conversation_summary(session),
            "last_activity": session.updated_at.isoformat() if session.updated_at else None
        }
    }


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Comprehensive health check with performance metrics"""
    session_service = get_session_service(db)
    query_analytics = QueryAnalyticsService(db)
    
    recent_analytics = query_analytics.get_summary(hours=1)
    
    return {
        "status": "healthy",
        "service": "WhatsApp Webhook - Enterprise v3.0",
        "version": "3.0.0",
        "architecture": "WhatsApp → Webhook → SessionService → AIQueryService → AnalyticsService → AIProviderService",
        "features": [
            "PostgreSQL Session Storage",
            "Full Context Injection",
            "Conversation Memory Summary",
            "Dynamic Role Management",
            "Confidence Routing",
            "Semantic Search Escalation",
            "Executive Dashboard",
            "Role-Based Dashboards",
            "Query Categorization",
            "Structured Responses",
            "Critical Alert Engine",
            "Forecast Commands",
            "Root Cause Commands",
            "Recommendation Commands",
            "Performance Monitoring"
        ],
        "recent_performance": {
            "avg_response_time_ms": recent_analytics.get("average_response_time_ms", 0),
            "avg_confidence": recent_analytics.get("average_confidence", 0),
            "success_rate": recent_analytics.get("success_rate", 100)
        },
        "timestamp": datetime.utcnow().isoformat()
    }


# ==========================================================
# ANALYTICS ENDPOINTS
# ==========================================================

@router.get("/analytics")
async def get_analytics(
    hours: int = 24,
    db: Session = Depends(get_db)
):
    """Get query analytics for monitoring"""
    query_analytics = QueryAnalyticsService(db)
    
    summary = query_analytics.get_summary(hours=hours)
    
    return {
        "success": True,
        "period_hours": hours,
        "summary": summary
    }


@router.get("/analytics/user/{phone_number}")
async def get_user_analytics(
    phone_number: str,
    hours: int = 168,
    db: Session = Depends(get_db)
):
    """Get analytics for specific user"""
    query_analytics = QueryAnalyticsService(db)
    
    user_stats = query_analytics.get_user_stats(phone_number, hours=hours)
    
    return {
        "success": True,
        "phone_number": phone_number,
        "period_hours": hours,
        "stats": user_stats
    }


@router.get("/analytics/category/{category}")
async def get_category_analytics(
    category: str,
    hours: int = 168,
    db: Session = Depends(get_db)
):
    """Get analytics by query category"""
    query_analytics = QueryAnalyticsService(db)
    
    category_stats = query_analytics.get_category_stats(category, hours=hours)
    
    return {
        "success": True,
        "category": category,
        "period_hours": hours,
        "stats": category_stats
    }
