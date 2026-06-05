# ==========================================================
# FILE: app/routes/webhook.py (ENTERPRISE GRADE v4.0)
# PROJECT: AI WhatsApp Logistics Copilot
# ==========================================================

import json
import time
import re
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
    send_text_message as send_structured_message
)
from app.services.ai_query_service import AIQueryService, get_ai_query_service
from app.services.session_service import (
    SessionService,
    get_session_service,
    UserRole,
    ConversationContext
)
from app.services.analytics_service import get_analytics_service
from app.services.query_analytics_service import (
    QueryAnalyticsService, 
    get_query_analytics_service
)
from app.services.user_access_service import UserAccessService, get_user_access_service
from app.services.semantic_search_service import SemanticSearchService, get_semantic_search_service
from app.services.root_cause_service import RootCauseService, get_root_cause_service
from app.services.recommendation_service import RecommendationService, get_recommendation_service
from app.services.forecast_service import ForecastService, get_forecast_service
from app.services.city_master_service import CityMasterService, get_city_master_service
from app.database import get_db

# ==========================================================
# ROUTER
# ==========================================================

router = APIRouter(
    prefix="/webhook",
    tags=["WhatsApp Webhook"]
)

# ==========================================================
# USER ROLE MAPPING
# ==========================================================

USER_ROLE_MAPPING = {
    "+923001234567": {"role": UserRole.CEO, "department": "executive", "name": "CEO", "access_level": 100},
    "+923007654321": {"role": UserRole.CEO, "department": "executive", "name": "COO", "access_level": 100},
    "+923001111111": {"role": UserRole.MANAGER, "department": "logistics", "name": "Logistics Manager", "access_level": 80},
    "+923002222222": {"role": UserRole.MANAGER, "department": "operations", "name": "Operations Manager", "access_level": 80},
    "+923003333333": {"role": UserRole.WAREHOUSE, "department": "warehouse", "name": "Warehouse Manager HPK", "access_level": 60},
    "+923004444444": {"role": UserRole.WAREHOUSE, "department": "warehouse", "name": "Warehouse Manager LHE", "access_level": 60},
    "+923005555555": {"role": UserRole.DEALER, "department": "dealer_management", "name": "Dealer Manager", "access_level": 50},
    "+923006666666": {"role": UserRole.VENDOR, "department": "procurement", "name": "Vendor Relations", "access_level": 40},
    "default": {"role": UserRole.GUEST, "department": "unknown", "name": "Guest User", "access_level": 10}
}

# ==========================================================
# ROLE-BASED PROMPT CONTEXTS
# ==========================================================

ROLE_CONTEXTS = {
    "ceo": "You are addressing the CEO. Focus on strategic insights, financial impact, network health, and high-level recommendations. Be concise and business-focused.",
    "manager": "You are addressing a Logistics Manager. Focus on operational metrics, dealer performance, warehouse efficiency, and actionable insights.",
    "warehouse": "You are addressing a Warehouse Manager. Focus on warehouse efficiency, bottlenecks, pending shipments, and operational improvements.",
    "dealer": "You are addressing a Dealer Manager. Focus on dealer performance, pending DNs, POD compliance, and dealer-specific recommendations.",
    "guest": "You are addressing a guest user. Provide general information and suggest specific queries for better assistance."
}

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
# EXECUTIVE COMMANDS
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
    "dealer forecast": "dealer_forecast",
    "future risk": "risk_forecast",
    "next month pod": "pod_forecast",
    "pod forecast": "pod_forecast",
    "delivery trend": "delivery_forecast",
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
    "root cause": "general_rca",
    "why is": "specific_rca"
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
# FOLLOW-UP PATTERNS
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
# DASHBOARD COMMANDS
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
# DN PATTERNS (Enhanced)
# ==========================================================

DN_PATTERNS = [
    r'\b(\d{10})\b',  # Exactly 10 digits
    r'\b(\d{8,15})\b',  # 8-15 digits
    r'dn[\s:]*(\d{8,15})',
    r'delivery[\s-]?note[\s:]*(\d{8,15})',
    r'track[\s:]*(\d{8,15})'
]

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def get_user_role_from_db(phone_number: str, db: Session) -> Dict[str, Any]:
    """Get user role from database (dynamic role management)"""
    try:
        from app.models import UserAccess
        user_access = db.query(UserAccess).filter(UserAccess.phone_number == phone_number).first()
        
        if user_access:
            return {
                "role": UserRole(user_access.role) if isinstance(user_access.role, str) else user_access.role,
                "department": user_access.department,
                "name": user_access.name,
                "access_level": user_access.access_level
            }
    except Exception as e:
        logger.warning(f"Failed to get user role from DB: {e}")
    
    return USER_ROLE_MAPPING.get(phone_number, USER_ROLE_MAPPING["default"])

def get_role_context(user_role: UserRole) -> str:
    """Get role-specific prompt context"""
    role_key = user_role.value if user_role else "guest"
    return ROLE_CONTEXTS.get(role_key, ROLE_CONTEXTS["guest"])

def extract_dn_from_question(question: str) -> Optional[str]:
    """Extract DN number from question using regex patterns"""
    for pattern in DN_PATTERNS:
        match = re.search(pattern, question, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def extract_city_from_question(question: str, city_service) -> Optional[str]:
    """Extract city from question using city master service"""
    question_lower = question.lower()
    
    # First check exact matches from city master
    cities = city_service.get_all_cities() if city_service else []
    for city in cities:
        if city.lower() in question_lower:
            return city
    
    # Common city names (fallback)
    common_cities = ["karachi", "lahore", "islamabad", "rawalpindi", 
                     "faisalabad", "multan", "peshawar", "quetta"]
    for city in common_cities:
        if city in question_lower:
            return city.title()
    
    return None

def extract_dealer_with_rapidfuzz(question: str, session_service, phone_number: str) -> Optional[str]:
    """Extract dealer name using RapidFuzz"""
    try:
        from rapidfuzz import process, fuzz
        
        # Get recent dealers from session history
        session = session_service.get_session_by_phone(phone_number)
        if session and session.conversation_history:
            recent_dealers = []
            for entry in session.conversation_history[-10:]:
                if entry.get('intent') == 'DEALER' and entry.get('entity'):
                    recent_dealers.append(entry.get('entity'))
            
            if recent_dealers:
                # Check if question contains any recent dealer
                for dealer in recent_dealers:
                    if dealer.lower() in question.lower():
                        return dealer
    except Exception as e:
        logger.warning(f"RapidFuzz dealer extraction failed: {e}")
    
    return None

def classify_query(question: str) -> QueryCategory:
    """Classify query into category for routing"""
    question_lower = question.lower()
    
    if any(cmd in question_lower for cmd in EXECUTIVE_COMMANDS.keys()):
        return QueryCategory.EXECUTIVE
    
    if any(cmd in question_lower for cmd in FORECAST_COMMANDS.keys()):
        return QueryCategory.FORECAST
    
    if any(cmd in question_lower for cmd in ROOT_CAUSE_COMMANDS.keys()):
        return QueryCategory.RCA
    
    if any(cmd in question_lower for cmd in RECOMMENDATION_COMMANDS.keys()):
        return QueryCategory.RECOMMENDATION
    
    if any(word in question_lower for word in ["risk", "critical", "urgent"]):
        return QueryCategory.RISK
    
    if "dealer" in question_lower or "customer" in question_lower:
        return QueryCategory.DEALER
    
    if "warehouse" in question_lower or "godown" in question_lower:
        return QueryCategory.WAREHOUSE
    
    if "city" in question_lower:
        return QueryCategory.CITY
    
    if "dn" in question_lower or "delivery note" in question_lower or extract_dn_from_question(question):
        return QueryCategory.DN
    
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

📛 *Name:* {data.get('dealer_name', data.get('dealer', 'Unknown'))}
📊 *Health Score:* {data.get('health_score', data.get('score', 0))}/100
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

📊 *Network Health:* {data.get('network_health', data.get('score', 0))}/100
💰 *Revenue At Risk:* {data.get('revenue_at_risk_formatted', data.get('formatted', 'Rs 0'))}
📦 *Inventory At Risk:* {data.get('inventory_at_risk', 0):,.0f} units

🚨 *Top Risks:*
• Dealer: {data.get('top_risk_dealer', 'None')}
• City: {data.get('top_risk_city', 'None')}

💡 *Priority Action:* Escalate top 20 dealers immediately
"""
    
    else:
        return data.get('formatted_message', data.get('response', str(data)))

def generate_suggested_followups(intent: str, entity: str = None) -> List[str]:
    """Generate suggested follow-up questions based on intent"""
    suggestions = {
        "DEALER": [
            "Show pending DNs",
            "What are the risks?",
            "How can we improve?"
        ],
        "CITY": [
            "What are the top risks?",
            "Show warehouse performance",
            "How to improve delivery?"
        ],
        "WAREHOUSE": [
            "Show bottlenecks",
            "What is the backlog?",
            "Recovery plan"
        ],
        "EXECUTIVE": [
            "Network health score",
            "Revenue at risk",
            "Weekly review"
        ],
        "general": [
            "Show dealer dashboard",
            "Network health score",
            "Top risk dealers"
        ]
    }
    
    base_suggestions = suggestions.get(intent, suggestions["general"])
    
    if entity and intent == "DEALER":
        return [f"Details for {entity}", f"Pending DNs for {entity}", f"Risks for {entity}"] + base_suggestions[:2]
    elif entity and intent == "CITY":
        return [f"{entity} risks", f"{entity} warehouse status", f"Improve {entity}"] + base_suggestions[:2]
    
    return base_suggestions[:3]

def extract_conversation_summary(session, max_messages: int = 20) -> str:
    """Extract conversation summary from session history (enhanced)"""
    if not hasattr(session, 'conversation_history') or not session.conversation_history:
        return "No previous conversation"
    
    # Extract unique topics discussed
    topics = set()
    entities = []
    topics_mapping = {
        'dealer': 'dealer',
        'city': 'city', 
        'warehouse': 'warehouse',
        'dn': 'delivery note',
        'pod': 'POD',
        'risk': 'risk',
        'forecast': 'forecast',
        'rca': 'root cause'
    }
    
    history_list = session.conversation_history or []
    for entry in history_list[-max_messages:]:
        question = entry.get('question', '').lower()
        intent = entry.get('intent', '')
        entity = entry.get('entity', '')
        
        if entity and intent in ['DEALER', 'CITY', 'WAREHOUSE']:
            entities.append(f"{intent.lower()} '{entity}'")
        
        for key, topic in topics_mapping.items():
            if key in question or (intent and intent.lower() == key):
                topics.add(topic)
    
    summary_parts = []
    if entities:
        summary_parts.append(f"Discussed: {', '.join(list(dict.fromkeys(entities))[:5])}")
    if topics:
        summary_parts.append(f"Topics: {', '.join(topics)}")
    
    if summary_parts:
        return " | ".join(summary_parts)
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
# RECEIVE WHATSAPP MESSAGE (ENTERPRISE GRADE v4.0)
# ==========================================================

@router.post("/")
async def receive_message(
    request: Request,
    db: Session = Depends(get_db)
):
    start_time = time.time()
    ai_start_time = None
    
    # Parse incoming message
    payload = await request.json()
    parsed_message = parse_whatsapp_message(payload)

    if not parsed_message:
        logger.debug("No message found in payload")
        return {"success": True, "message": "No message found"}

    phone_number = parsed_message["from"]
    customer_message = parsed_message["text"]
    
    # Get user role
    user_metadata = get_user_role_from_db(phone_number, db)
    user_role = user_metadata["role"]
    department = user_metadata["department"]
    user_name = user_metadata["name"]
    access_level = user_metadata.get("access_level", 1)
    
    # Initialize services (using singleton pattern)
    session_service = get_session_service(db)
    ai_service = get_ai_query_service(db)
    analytics_service = get_analytics_service(db)
    query_analytics = get_query_analytics_service(db)
    city_service = get_city_master_service(db)
    
    # Initialize specialized services
    try:
        semantic_search = get_semantic_search_service()
        root_cause_service = get_root_cause_service(db)
        recommendation_service = get_recommendation_service(db)
        forecast_service = get_forecast_service(db)
    except Exception as e:
        logger.warning(f"Specialized services not available: {e}")
        semantic_search = None
        root_cause_service = None
        recommendation_service = None
        forecast_service = None
    
    # Get or create session
    session = session_service.get_or_create_session(
        phone_number=phone_number,
        user_role=user_role.value if user_role else "guest",
        user_name=user_name,
        department=department
    )
    
    session_service.update_activity(phone_number)
    
    # Log incoming message
    logger.info("=" * 80)
    logger.info(f"📱 WHATSAPP MESSAGE RECEIVED")
    logger.info(f"📞 From: {phone_number} ({user_name}, {user_role.value if user_role else 'guest'})")
    logger.info(f"💬 Message: {customer_message}")
    logger.info(f"📊 Session Context: Dealer={session.selected_dealer}, City={session.selected_city}")
    logger.info("=" * 80)
    
    # ==========================================================
    # CHECK FOR DEALER SELECTION RESPONSE
    # ==========================================================
    selection_result = session_service.handle_dealer_selection(phone_number, customer_message)
    
    if selection_result.get("handled"):
        selected_dealer = selection_result.get("selected_dealer")
        
        result = ai_service.process_query(
            question=f"Show dealer {selected_dealer} dashboard",
            user_phone=phone_number,
            user_role=user_role.value if user_role else "guest"
        )
        
        ai_reply = format_structured_dashboard(result, "dealer") if result.get("structured_data") else result.get("response", f"Dealer '{selected_dealer}' information retrieved.")
        intent = result.get("question_type", "dealer_lookup_selected")
        confidence = result.get("confidence", 85)
        
        # Add suggested follow-ups
        suggestions = generate_suggested_followups("DEALER", selected_dealer)
        ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
        
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
        
        whatsapp_response = send_structured_message(phone_number, ai_reply)
        
        return {
            "success": True,
            "customer_message": customer_message,
            "ai_reply": ai_reply,
            "intent": intent,
            "confidence": confidence,
            "suggestions": suggestions[:3],
            "whatsapp_response": whatsapp_response
        }
    
    # ==========================================================
    # CHECK FOR CLEAR CONTEXT COMMAND
    # ==========================================================
    if customer_message.lower() in ["clear context", "reset", "new conversation", "clear"]:
        session_service.clear_context(phone_number)
        ai_reply = "✅ Conversation context cleared. Starting fresh. How can I help you?"
        
        suggestions = generate_suggested_followups("general")
        ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
        
        whatsapp_response = send_structured_message(phone_number, ai_reply)
        
        return {
            "success": True,
            "customer_message": customer_message,
            "ai_reply": ai_reply,
            "intent": "context_cleared",
            "confidence": 100,
            "suggestions": suggestions[:3],
            "whatsapp_response": whatsapp_response
        }
    
    # ==========================================================
    # DN DETECTION (Enhanced)
    # ==========================================================
    dn_number = extract_dn_from_question(customer_message)
    if dn_number:
        logger.info(f"🔢 DN detected: {dn_number}")
        result = ai_service.process_query(
            question=f"Show DN {dn_number} details",
            user_phone=phone_number,
            user_role=user_role.value if user_role else "guest"
        )
        
        ai_reply = result.get("response", f"DN {dn_number} information retrieved.")
        intent = "dn_tracking"
        
        suggestions = generate_suggested_followups("general")
        ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
        
        query_analytics.log_query(
            phone_number=phone_number,
            question=customer_message,
            intent=intent,
            entity=dn_number,
            response_time_ms=int((time.time() - start_time) * 1000),
            ai_used=result.get("ai_used", False),
            confidence=result.get("confidence", 85),
            user_role=user_role.value if user_role else "guest",
            provider=result.get("provider_used", "unknown"),
            category="dn"
        )
        
        whatsapp_response = send_structured_message(phone_number, ai_reply)
        
        return {
            "success": True,
            "customer_message": customer_message,
            "ai_reply": ai_reply,
            "intent": intent,
            "dn_number": dn_number,
            "suggestions": suggestions[:3],
            "whatsapp_response": whatsapp_response
        }
    
    # ==========================================================
    # CITY DETECTION (Using City Master)
    # ==========================================================
    city_name = extract_city_from_question(customer_message, city_service)
    if city_name:
        logger.info(f"🌆 City detected: {city_name}")
        session_service.update_session_context(phone_number, selected_city=city_name)
    
    # ==========================================================
    # CLASSIFY QUERY
    # ==========================================================
    query_category = classify_query(customer_message)
    logger.info(f"📊 Query classified as: {query_category.value}")
    
    # ==========================================================
    # ROOT CAUSE ANALYSIS ROUTING
    # ==========================================================
    if query_category == QueryCategory.RCA and root_cause_service:
        try:
            rca_result = root_cause_service.analyze(customer_message)
            ai_reply = rca_result.get("formatted_message", rca_result.get("response", "Root cause analysis complete."))
            intent = "root_cause_analysis"
            confidence = 85
            
            suggestions = generate_suggested_followups("general")
            ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
            
            query_analytics.log_query(
                phone_number=phone_number,
                question=customer_message,
                intent=intent,
                entity=None,
                response_time_ms=int((time.time() - start_time) * 1000),
                ai_used=True,
                confidence=confidence,
                user_role=user_role.value if user_role else "guest",
                provider="root_cause_service",
                category="rca"
            )
            
            whatsapp_response = send_structured_message(phone_number, ai_reply)
            
            return {
                "success": True,
                "customer_message": customer_message,
                "ai_reply": ai_reply,
                "intent": intent,
                "confidence": confidence,
                "suggestions": suggestions[:3],
                "whatsapp_response": whatsapp_response
            }
        except Exception as e:
            logger.error(f"Root cause analysis error: {e}")
    
    # ==========================================================
    # FORECAST ROUTING
    # ==========================================================
    if query_category == QueryCategory.FORECAST and forecast_service:
        try:
            forecast_result = forecast_service.generate_forecast(customer_message)
            ai_reply = forecast_result.get("formatted_message", forecast_result.get("response", "Forecast generated."))
            intent = "forecast"
            confidence = 80
            
            suggestions = generate_suggested_followups("general")
            ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
            
            query_analytics.log_query(
                phone_number=phone_number,
                question=customer_message,
                intent=intent,
                entity=None,
                response_time_ms=int((time.time() - start_time) * 1000),
                ai_used=True,
                confidence=confidence,
                user_role=user_role.value if user_role else "guest",
                provider="forecast_service",
                category="forecast"
            )
            
            whatsapp_response = send_structured_message(phone_number, ai_reply)
            
            return {
                "success": True,
                "customer_message": customer_message,
                "ai_reply": ai_reply,
                "intent": intent,
                "confidence": confidence,
                "suggestions": suggestions[:3],
                "whatsapp_response": whatsapp_response
            }
        except Exception as e:
            logger.error(f"Forecast error: {e}")
    
    # ==========================================================
    # RECOMMENDATION ROUTING
    # ==========================================================
    if query_category == QueryCategory.RECOMMENDATION and recommendation_service:
        try:
            rec_result = recommendation_service.generate(customer_message)
            ai_reply = rec_result.get("formatted_message", rec_result.get("response", "Recommendations generated."))
            intent = "recommendation"
            confidence = 85
            
            suggestions = generate_suggested_followups("general")
            ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
            
            query_analytics.log_query(
                phone_number=phone_number,
                question=customer_message,
                intent=intent,
                entity=None,
                response_time_ms=int((time.time() - start_time) * 1000),
                ai_used=True,
                confidence=confidence,
                user_role=user_role.value if user_role else "guest",
                provider="recommendation_service",
                category="recommendation"
            )
            
            whatsapp_response = send_structured_message(phone_number, ai_reply)
            
            return {
                "success": True,
                "customer_message": customer_message,
                "ai_reply": ai_reply,
                "intent": intent,
                "confidence": confidence,
                "suggestions": suggestions[:3],
                "whatsapp_response": whatsapp_response
            }
        except Exception as e:
            logger.error(f"Recommendation error: {e}")
    
    # ==========================================================
    # CHECK FOR CRITICAL ALERTS
    # ==========================================================
    if any(keyword in customer_message.lower() for keyword in CRITICAL_ALERT_KEYWORDS):
        try:
            critical_risks = analytics_service.top_risk_dealers(5)
            if critical_risks:
                network_health = analytics_service.network_health_score()
                revenue_risk = analytics_service.revenue_at_risk()
                
                ai_reply = f"""
🚨 *CRITICAL ALERT*

⚠️ *Immediate Attention Required*

📊 *Network Health: {network_health.get('score', 0)}/100*
💰 *Revenue at Risk: {revenue_risk.get('formatted', 'Rs 0')}*

*Top 5 Risk Dealers:*
{chr(10).join([f"{i+1}. {d['dealer']} - {d['risk_score']}% risk" for i, d in enumerate(critical_risks[:5])])}

🎯 *Recommended Action:* Escalate immediately to dealer management team
"""
                intent = "critical_alert"
                confidence = 95
                
                suggestions = generate_suggested_followups("EXECUTIVE")
                ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
                
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
                    "suggestions": suggestions[:3],
                    "whatsapp_response": whatsapp_response
                }
        except Exception as e:
            logger.error(f"Critical alert error: {e}")
    
    # ==========================================================
    # CHECK FOR EXECUTIVE QUERIES (With Enhanced Briefing)
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
                revenue = analytics_service.revenue_at_risk()
                risk_dealers = analytics_service.top_risk_dealers(3)
                
                ai_reply = f"""
🌅 *MORNING BRIEFING* - {datetime.now().strftime('%Y-%m-%d')}

📊 *NETWORK HEALTH: {network.get('score', 0)}/100* ({network.get('category', 'Unknown')})

💰 *REVENUE AT RISK: {summary.get('revenue_at_risk_formatted', 'Rs 0')}*
📦 *INVENTORY AT RISK: {inventory_data.get('formatted', '0')} units*

🚨 *TOP 3 RISKS TODAY:*
1. Dealer: {risk_dealers[0]['dealer'] if risk_dealers else 'None'} ({risk_dealers[0]['risk_score'] if risk_dealers else 0}%)
2. City: {summary.get('top_risk_city', 'Unknown')}
3. Warehouse: Pending backlog

🎯 *TODAY'S PRIORITIES:*
• Recover POD from top 20 dealers
• Escalate {risk_dealers[0]['dealer'] if risk_dealers else 'top dealers'}
• Focus on {summary.get('top_risk_city', 'Karachi')}

💡 *Potential Recovery:* Rs {revenue.get('amount', 0) * 0.3:,.0f} (30% reduction achievable)
"""
                intent = "morning_briefing"
                confidence = 95
                
            elif executive_command == "weekly_review" or is_weekly_review_query(customer_message):
                executive_data = analytics_service.executive_summary()
                network = analytics_service.network_health_score()
                revenue = analytics_service.revenue_at_risk()
                
                ai_reply = f"""
📅 *WEEKLY MANAGEMENT REVIEW* - Week {datetime.now().isocalendar()[1]}

📊 *NETWORK HEALTH:* {network.get('score', 0)}/100 ({network.get('category', 'Unknown')})
📈 *TREND:* {'Improving' if network.get('score', 0) > 75 else 'Stable' if network.get('score', 0) > 60 else 'Declining'}

💰 *REVENUE AT RISK:* {revenue.get('formatted', 'Rs 0')}
📦 *INVENTORY AT RISK:* {inventory_data.get('formatted', '0')} units

🚨 *TOP ISSUES THIS WEEK:*
• Dealer POD compliance below target
• Warehouse backlog in HPK
• City delays in Karachi

🎯 *RECOMMENDATIONS:*
1. Escalate top 20 dealers
2. Deploy recovery team to Karachi
3. Audit HPK warehouse processes

📅 *NEXT WEEK FOCUS:* Reduce pending DNs by 15%
"""
                intent = "weekly_review"
                confidence = 85
                
            elif executive_command == "ceo_briefing":
                summary = analytics_service.executive_summary()
                network = analytics_service.network_health_score()
                revenue = analytics_service.revenue_at_risk()
                risk_dealers = analytics_service.top_risk_dealers(5)
                
                ai_reply = f"""
👑 *CEO BRIEFING* - {datetime.now().strftime('%Y-%m-%d')}

╔══════════════════════════════════════╗
║         NETWORK OVERVIEW             ║
╚══════════════════════════════════════╝

📊 *NETWORK HEALTH SCORE:* {network.get('score', 0)}/100
🏆 *CATEGORY:* {network.get('category', 'Unknown')}

╔══════════════════════════════════════╗
║         FINANCIAL EXPOSURE           ║
╚══════════════════════════════════════╝

💰 *REVENUE AT RISK:* {revenue.get('formatted', 'Rs 0')}
📦 *INVENTORY AT RISK:* {inventory_data.get('formatted', '0')} units

╔══════════════════════════════════════╗
║           TOP 5 RISK DEALERS         ║
╚══════════════════════════════════════╝

{chr(10).join([f"{i+1}. {d['dealer']} - {d['risk_score']}% risk (Rs {d.get('pending_value', 0):,.0f})" for i, d in enumerate(risk_dealers[:5])])}

╔══════════════════════════════════════╗
║         STRATEGIC RECOMMENDATIONS     ║
╚══════════════════════════════════════╝

1. 🚨 Escalate top 5 risk dealers immediately
2. 🏭 Deploy recovery team to HPK warehouse
3. 🌆 Focus recovery efforts on Karachi
4. 📋 Implement daily POD follow-up automation

╔══════════════════════════════════════╗
║         30-DAY FORECAST              ║
╚══════════════════════════════════════╝

• Expected backlog reduction: 15-20%
• Revenue recovery potential: Rs {revenue.get('amount', 0) * 0.3:,.0f}
• Network health improvement: +5-10 points
"""
                intent = "ceo_briefing"
                confidence = 90
                
            else:
                summary = analytics_service.executive_summary()
                ai_reply = format_executive_response(summary, "executive_summary")
                intent = "executive_summary"
                confidence = 85
            
            # Update session
            session_service.update_session_context(
                phone_number,
                executive_mode=True,
                last_dashboard="executive",
                last_analysis_type=intent
            )
            
            # Add suggested follow-ups
            suggestions = generate_suggested_followups("EXECUTIVE")
            ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
            
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
            
            whatsapp_response = send_structured_message(phone_number, ai_reply)
            
            return {
                "success": True,
                "customer_message": customer_message,
                "ai_reply": ai_reply,
                "intent": intent,
                "confidence": confidence,
                "suggestions": suggestions[:3],
                "whatsapp_response": whatsapp_response
            }
            
        except Exception as e:
            logger.error(f"Executive query error: {e}")
    
    # ==========================================================
    # CHECK FOR DASHBOARD COMMANDS (Role-based)
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
        
        suggestions = generate_suggested_followups(intent.upper())
        ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
        
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
            "suggestions": suggestions[:3],
            "whatsapp_response": whatsapp_response
        }
    
    # ==========================================================
    # INJECT CONTEXT INTO QUESTION
    # ==========================================================
    context = session_service.get_context(phone_number)
    enhanced_question = inject_context_into_question(customer_message, context)
    
    if enhanced_question != customer_message:
        logger.info(f"🔄 Context injected: '{customer_message}' -> '{enhanced_question}'")
    
    # ==========================================================
    # GET CONVERSATION SUMMARY
    # ==========================================================
    conversation_summary = extract_conversation_summary(session, max_messages=20)
    logger.info(f"📝 Conversation summary: {conversation_summary}")
    
    # ==========================================================
    # PROCESS QUERY WITH AI QUERY SERVICE (WITH FULL CONTEXT)
    # ==========================================================
    ai_start_time = time.time()
    
    try:
        # Get role-specific context
        role_context = get_role_context(user_role)
        
        # Prepare full context for AI service
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
            "conversation_history": session.conversation_history[-10:] if hasattr(session, 'conversation_history') and session.conversation_history else [],
            "department": department,
            "access_level": access_level,
            "role_context": role_context
        }
        
        # Process query with FULL context (CRITICAL FIX)
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
        # RAPIDFUZZ DEALER MATCHING (Priority 3)
        # ==========================================================
        if confidence < 70 and not entity:
            rapidfuzz_dealer = extract_dealer_with_rapidfuzz(customer_message, session_service, phone_number)
            if rapidfuzz_dealer:
                logger.info(f"🔍 RapidFuzz dealer match: {rapidfuzz_dealer}")
                result = ai_service.process_query(
                    question=f"Show dealer {rapidfuzz_dealer} dashboard",
                    user_phone=phone_number,
                    user_role=user_role.value if user_role else "guest"
                )
                ai_reply = result.get("response", ai_reply)
                entity = rapidfuzz_dealer
                intent = "DEALER"
                confidence = 85
        
        # Update session with new context
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
        if hasattr(session_service, 'add_to_conversation_history'):
            session_service.add_to_conversation_history(
                phone_number,
                question=enhanced_question,
                response=ai_reply[:500],
                intent=intent,
                entity=entity
            )
        
        # Check for fuzzy match dealer selection
        if result.get("fuzzy"):
            matches = result.get("matches", [])
            if matches:
                session_service.set_pending_dealer_selection(phone_number, matches)
                logger.info(f"📋 Multiple dealers found, awaiting selection")
        
        # Add suggested follow-ups
        suggestions = generate_suggested_followups(intent, entity)
        ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
        
        # Log analytics
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
        suggestions = generate_suggested_followups("general")
    
    # ==========================================================
    # SEND STRUCTURED RESPONSE
    # ==========================================================
    result = result if 'result' in locals() else {}
    
    if intent in ["dealer", "dealer_lookup", "dealer_analysis"]:
        formatted_reply = format_structured_dashboard(result.get("structured_data", {}), "dealer")
    elif intent in ["warehouse", "warehouse_analysis"]:
        formatted_reply = format_structured_dashboard(result.get("structured_data", {}), "warehouse")
    elif intent in ["executive", "executive_summary", "network_health"]:
        formatted_reply = format_structured_dashboard(result.get("structured_data", {}), "executive")
    else:
        formatted_reply = ai_reply
    
    whatsapp_response = send_structured_message(phone_number, formatted_reply)
    
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
        "suggestions": suggestions[:3] if 'suggestions' in locals() else generate_suggested_followups("general")[:3],
        "whatsapp_response": whatsapp_response
    }


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def format_executive_response(data: Dict[str, Any], response_type: str) -> str:
    """Format executive responses with emojis and structure"""
    
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
    
    else:
        return data.get("response", data.get("formatted_message", "No response available"))

def inject_context_into_question(question: str, context: ConversationContext) -> str:
    """Inject context into question for follow-up queries"""
    
    question_lower = question.lower()
    
    is_follow_up = any(pattern in question_lower for pattern in FOLLOW_UP_PATTERNS)
    
    if len(question.split()) <= 4:
        is_follow_up = True
    
    if question_lower in ["details", "more", "elaborate", "explain"]:
        is_follow_up = True
    
    if not is_follow_up:
        return question
    
    if context.last_intent == "CITY" and context.selected_city:
        if any(word in question_lower for word in ["improve", "fix", "solve", "recover"]):
            return f"How can we improve {context.selected_city}? {question}"
        elif "why" in question_lower or "reason" in question_lower or "cause" in question_lower:
            return f"What are the reasons for issues in {context.selected_city}? {question}"
        return f"Regarding {context.selected_city}: {question}"
    
    elif context.last_intent == "DEALER" and context.selected_dealer:
        if any(word in question_lower for word in ["improve", "fix", "solve", "recover"]):
            return f"How can we improve {context.selected_dealer}? {question}"
        elif "issue" in question_lower or "problem" in question_lower:
            return f"What are the specific issues with {context.selected_dealer}? {question}"
        elif "why" in question_lower or "reason" in question_lower:
            return f"Why is {context.selected_dealer} having problems? {question}"
        return f"Regarding {context.selected_dealer}: {question}"
    
    elif context.last_intent == "WAREHOUSE" and context.selected_warehouse:
        if "backlog" in question_lower or "pending" in question_lower:
            return f"What is the backlog at warehouse {context.selected_warehouse}? {question}"
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
        "message": "Webhook is active - Enterprise v4.0",
        "features": [
            "Full Context Injection (Fixed)",
            "Role-Based Prompt Injection",
            "RapidFuzz Dealer Matching",
            "Enhanced DN Detection (10-digit)",
            "City Master Database",
            "Network Health Score",
            "Executive Briefing Engine",
            "Root Cause Analysis",
            "Forecast Commands",
            "Recommendation Engine",
            "Suggested Follow-ups",
            "Singleton Services (Performance)"
        ]
    }


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Comprehensive health check"""
    query_analytics = get_query_analytics_service(db)
    recent_analytics = query_analytics.get_summary(hours=1)
    
    return {
        "status": "healthy",
        "service": "WhatsApp Webhook - Enterprise v4.0",
        "version": "4.0.0",
        "features": [
            "Full Context Injection",
            "Role-Based Routing",
            "RapidFuzz Matching",
            "Enhanced DN Detection",
            "Executive Briefing",
            "Root Cause Analysis",
            "Forecast Engine",
            "Recommendation Engine"
        ],
        "recent_performance": {
            "avg_response_time_ms": recent_analytics.get("average_response_time_ms", 0),
            "avg_confidence": recent_analytics.get("average_confidence", 0),
            "success_rate": recent_analytics.get("success_rate", 100)
        },
        "timestamp": datetime.utcnow().isoformat()
    }
