# ==========================================================
# FILE: app/routes/webhook.py (PRODUCTION READY v7.0)
# PROJECT: AI WhatsApp Logistics Copilot
# FIXED: Dealer detection without keywords, regex boundaries,
#        context pollution prevention, threshold 85,
#        logging, context clearing, DN patterns, JSON safety
# ==========================================================

import json
import time
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, asdict
from collections import deque

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from loguru import logger

from app.config import WHATSAPP_VERIFY_TOKEN
from app.services.whatsapp_service import (
    parse_whatsapp_message,
    send_text_message,
    send_text_message as send_structured_message
)

# ==========================================================
# SECTION 1: SAFE IMPORTS (Critical - Prevents Railway Crashes)
# ==========================================================

# Core services (must exist)
try:
    from app.services.ai_query_service import AIQueryService, get_ai_query_service
    AI_QUERY_AVAILABLE = True
except Exception as e:
    logger.error(f"Failed to import AIQueryService: {e}")
    AI_QUERY_AVAILABLE = False
    get_ai_query_service = None

try:
    from app.services.session_service import (
        SessionService,
        get_session_service,
        UserRole,
        ConversationContext
    )
    SESSION_AVAILABLE = True
except Exception as e:
    logger.error(f"Failed to import session_service: {e}")
    SESSION_AVAILABLE = False
    get_session_service = None
    UserRole = None
    ConversationContext = None

try:
    from app.services.analytics_service import get_analytics_service
    ANALYTICS_AVAILABLE = True
except Exception as e:
    logger.error(f"Failed to import analytics_service: {e}")
    ANALYTICS_AVAILABLE = False
    get_analytics_service = None

try:
    from app.services.query_analytics_service import (
        QueryAnalyticsService, 
        get_query_analytics_service
    )
    QUERY_ANALYTICS_AVAILABLE = True
except Exception as e:
    logger.error(f"Failed to import query_analytics_service: {e}")
    QUERY_ANALYTICS_AVAILABLE = False
    get_query_analytics_service = None

try:
    from app.services.user_access_service import UserAccessService, get_user_access_service
    USER_ACCESS_AVAILABLE = True
except Exception as e:
    logger.error(f"Failed to import user_access_service: {e}")
    USER_ACCESS_AVAILABLE = False
    get_user_access_service = None

# Optional services (safe fallback)
try:
    from app.services.semantic_search_service import SemanticSearchService, get_semantic_search_service
    SEMANTIC_SEARCH_AVAILABLE = True
except Exception as e:
    logger.warning(f"Semantic search not available: {e}")
    SEMANTIC_SEARCH_AVAILABLE = False
    get_semantic_search_service = None

try:
    from app.services.root_cause_service import RootCauseService, get_root_cause_service
    ROOT_CAUSE_AVAILABLE = True
except Exception as e:
    logger.warning(f"Root cause service not available: {e}")
    ROOT_CAUSE_AVAILABLE = False
    get_root_cause_service = None

try:
    from app.services.recommendation_service import RecommendationService, get_recommendation_service
    RECOMMENDATION_AVAILABLE = True
except Exception as e:
    logger.warning(f"Recommendation service not available: {e}")
    RECOMMENDATION_AVAILABLE = False
    get_recommendation_service = None

try:
    from app.services.forecast_service import ForecastService, get_forecast_service
    FORECAST_AVAILABLE = True
except Exception as e:
    logger.warning(f"Forecast service not available: {e}")
    FORECAST_AVAILABLE = False
    get_forecast_service = None

try:
    from app.services.city_master_service import CityMasterService, get_city_master_service
    CITY_MASTER_AVAILABLE = True
except Exception as e:
    logger.warning(f"City master service not available: {e}")
    CITY_MASTER_AVAILABLE = False
    get_city_master_service = None

from app.database import get_db

# ==========================================================
# SECTION 2: DUPLICATE MESSAGE PROTECTION (FIX #7)
# ==========================================================

# Store last 100 message IDs per phone number (memory cache)
RECENT_MESSAGES: Dict[str, deque] = {}
MAX_MESSAGE_CACHE = 100

# FIX #7: Consider Redis/Database for persistence across restarts
# For now, add timestamp to help with cleanup
MESSAGE_EXPIRY_SECONDS = 3600  # 1 hour

def is_duplicate_message(phone_number: str, message_id: str) -> bool:
    """Check if message has been processed recently"""
    if phone_number not in RECENT_MESSAGES:
        RECENT_MESSAGES[phone_number] = deque(maxlen=MAX_MESSAGE_CACHE)
    
    # Check for message ID
    for stored_id, timestamp in RECENT_MESSAGES[phone_number]:
        if stored_id == message_id:
            # Check if expired
            if (datetime.now() - timestamp).total_seconds() > MESSAGE_EXPIRY_SECONDS:
                # Remove expired and continue
                continue
            return True
    
    # Store with timestamp
    RECENT_MESSAGES[phone_number].append((message_id, datetime.now()))
    return False

def cleanup_old_messages():
    """Cleanup old messages from cache (run periodically)"""
    now = datetime.now()
    for phone in list(RECENT_MESSAGES.keys()):
        original_len = len(RECENT_MESSAGES[phone])
        RECENT_MESSAGES[phone] = deque(
            [(msg_id, ts) for msg_id, ts in RECENT_MESSAGES[phone] 
             if (now - ts).total_seconds() <= MESSAGE_EXPIRY_SECONDS],
            maxlen=MAX_MESSAGE_CACHE
        )
        if len(RECENT_MESSAGES[phone]) < original_len:
            logger.debug(f"Cleaned {original_len - len(RECENT_MESSAGES[phone])} old messages for {phone}")

# ==========================================================
# SECTION 3: SAFE WHATSAPP REPLY FUNCTION
# ==========================================================

def safe_send_reply(phone_number: str, message: str) -> Dict[str, Any]:
    """Unified safe WhatsApp reply sender"""
    try:
        return send_structured_message(phone_number, message)
    except Exception as e:
        logger.error(f"WhatsApp send failed for {phone_number}: {e}")
        return {"success": False, "error": str(e)}

# ==========================================================
# ROUTER
# ==========================================================

router = APIRouter(
    prefix="/webhook",
    tags=["WhatsApp Webhook"]
)

# ==========================================================
# USER ROLE MAPPING (Fallback when database unavailable)
# ==========================================================

USER_ROLE_MAPPING = {
    "+923001234567": {"role": "ceo", "department": "executive", "name": "CEO", "access_level": 100},
    "+923007654321": {"role": "ceo", "department": "executive", "name": "COO", "access_level": 100},
    "+923001111111": {"role": "manager", "department": "logistics", "name": "Logistics Manager", "access_level": 80},
    "+923002222222": {"role": "manager", "department": "operations", "name": "Operations Manager", "access_level": 80},
    "+923003333333": {"role": "warehouse", "department": "warehouse", "name": "Warehouse Manager HPK", "access_level": 60},
    "+923004444444": {"role": "warehouse", "department": "warehouse", "name": "Warehouse Manager LHE", "access_level": 60},
    "+923005555555": {"role": "dealer", "department": "dealer_management", "name": "Dealer Manager", "access_level": 50},
    "+923006666666": {"role": "vendor", "department": "procurement", "name": "Vendor Relations", "access_level": 40},
    "default": {"role": "guest", "department": "unknown", "name": "Guest User", "access_level": 10}
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
    GENERAL_CHAT = "general_chat"
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
# FIX #2: GENERAL CHAT KEYWORDS WITH WORD BOUNDARIES
# ==========================================================

GENERAL_CHAT_PATTERNS = [
    r'\bhello\b', r'\bhi\b', r'\bhey\b', r'\bsalam\b', r'\bassalam\b',
    r'\bgood morning\b', r'\bgood evening\b', r'\bgood afternoon\b',
    r'\bhow are you\b', r'\bwhat\'s up\b', r'\bhowdy\b',
    r'\bcan i ask\b', r'\bi have a question\b',
    r'\bhelp\b', r'\bplease help\b', r'\bi need help\b',
    r'\bthanks?\b', r'\bthank you\b', r'\bappreciate\b',
    r'\bok\b', r'\bokay\b', r'\bgot it\b', r'\bunderstood\b',
    r'\bgreat\b', r'\bawesome\b', r'\bnice\b', r'\bgood\b'
]

# ==========================================================
# DEALER KEYWORDS (For explicit dealer queries)
# ==========================================================

DEALER_KEYWORDS = [
    "dealer dashboard",
    "dealer report",
    "dealer performance",
    "show dealer",
    "dealer health",
    "dealer details",
    "dealer information",
    "tell me about dealer"
]

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
# FIX #8: ENHANCED DN PATTERNS
# ==========================================================

DN_PATTERNS = [
    r'\b(\d{10})\b',                          # 6243611361
    r'\b(\d{8,15})\b',                        # 8-15 digits
    r'dn[\s:]*(\d{8,15})',                    # DN 6243611361
    r'^DN\s*(\d{10})$',                       # DN6243611361
    r'^dn\s*(\d{10})$',                       # dn6243611361
    r'^status\s+(\d{10})$',                   # status 6243611361
    r'^track\s+dn\s+(\d{10})$',               # track dn 6243611361
    r'^where\s+is\s+dn\s+(\d{10})$',          # where is dn 6243611361
    r'^track\s+(\d{10})$',                    # track 6243611361
    r'^check\s+(\d{10})$',                    # check 6243611361
    r'delivery[\s-]?note[\s:]*(\d{8,15})',    # delivery note 6243611361
    r'track[\s:]*(\d{8,15})'                  # track:6243611361
]

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def extract_message_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Centralized message extraction from webhook payload"""
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        
        # Check for status updates
        if value.get("statuses"):
            return {"type": "status", "text": None, "from_phone": None, "message_id": None}
        
        # Extract contact
        contacts = value.get("contacts", [])
        if not contacts:
            return {"type": "unknown", "text": None, "from_phone": None, "message_id": None}
        
        wa_id = contacts[0].get("wa_id", "")
        
        # Extract messages
        messages = value.get("messages", [])
        if not messages:
            return {"type": "no_message", "text": None, "from_phone": wa_id, "message_id": None}
        
        message = messages[0]
        message_id = message.get("id", "")
        message_type = message.get("type", "unknown")
        
        if message_type == "text":
            text = message.get("text", {}).get("body", "")
            return {
                "type": "text",
                "text": text,
                "from_phone": wa_id,
                "message_id": message_id
            }
        else:
            return {
                "type": message_type,
                "text": None,
                "from_phone": wa_id,
                "message_id": message_id
            }
    
    except Exception as e:
        logger.error(f"Message extraction error: {e}")
        return {"type": "error", "text": None, "from_phone": None, "message_id": None}

def get_user_role_from_db(phone_number: str, db: Session) -> Dict[str, Any]:
    """Get user role from database (dynamic role management)"""
    try:
        if USER_ACCESS_AVAILABLE and get_user_access_service:
            access_service = get_user_access_service(db)
            user_access = access_service.get_user_by_phone(phone_number)
            
            if user_access:
                return {
                    "role": user_access.role,
                    "department": user_access.department,
                    "name": user_access.name,
                    "access_level": user_access.access_level
                }
    except Exception as e:
        logger.warning(f"Failed to get user role from DB: {e}")
    
    return USER_ROLE_MAPPING.get(phone_number, USER_ROLE_MAPPING["default"])

def get_role_context(user_role: str) -> str:
    """Get role-specific prompt context"""
    return ROLE_CONTEXTS.get(user_role, ROLE_CONTEXTS["guest"])

def extract_dn_from_question(question: str) -> Optional[str]:
    """Extract DN number from question using regex patterns"""
    try:
        for pattern in DN_PATTERNS:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                dn = match.group(1)
                logger.info(f"🔢 DN extracted: '{dn}' from pattern '{pattern}'")
                return dn
    except Exception as e:
        logger.warning(f"DN extraction error: {e}")
    return None

def extract_city_from_question(question: str, city_service) -> Optional[str]:
    """Extract city from question using city master service"""
    try:
        question_lower = question.lower()
        
        if city_service and CITY_MASTER_AVAILABLE:
            cities = city_service.get_all_cities() if hasattr(city_service, 'get_all_cities') else []
            for city in cities:
                if city.lower() in question_lower:
                    return city
        
        common_cities = ["karachi", "lahore", "islamabad", "rawalpindi", 
                         "faisalabad", "multan", "peshawar", "quetta"]
        for city in common_cities:
            if city in question_lower:
                return city.title()
    except Exception as e:
        logger.warning(f"City extraction error: {e}")
    return None

# ==========================================================
# FIX #1 & #4: DEALER DETECTION FUNCTION
# ==========================================================

def detect_dealer_in_message(question: str, analytics_service, threshold: int = 85) -> Optional[str]:
    """
    Detect dealer name in message without requiring keywords
    FIX #1: Direct detection for "Bhatti Electronics-Bwp" style queries
    FIX #4: Threshold increased to 85
    """
    try:
        from rapidfuzz import process, fuzz
        
        # Get dealer list from analytics
        if not analytics_service:
            return None
        
        dealers = analytics_service.dealer_rankings(200)
        dealer_names = [d.get('dealer', '') for d in dealers if d.get('dealer')]
        
        if not dealer_names:
            return None
        
        # FIX #4: Use threshold 85 (increased from 70)
        matches = process.extract(question, dealer_names, scorer=fuzz.token_sort_ratio, limit=1)
        
        if matches and matches[0][1] >= threshold:
            dealer_name = matches[0][0]
            confidence = matches[0][1]
            logger.info(f"🏪 Dealer detected: '{dealer_name}' (confidence: {confidence}%)")
            return dealer_name
        
        # Try partial ratio for shorter queries
        matches = process.extract(question, dealer_names, scorer=fuzz.partial_ratio, limit=1)
        if matches and matches[0][1] >= threshold:
            dealer_name = matches[0][0]
            confidence = matches[0][1]
            logger.info(f"🏪 Dealer detected (partial): '{dealer_name}' (confidence: {confidence}%)")
            return dealer_name
        
    except Exception as e:
        logger.warning(f"Dealer detection error: {e}")
    
    return None

def is_general_chat(question: str) -> bool:
    """Check if message is general chat using regex word boundaries"""
    question_lower = question.lower().strip()
    for pattern in GENERAL_CHAT_PATTERNS:
        if re.search(pattern, question_lower):
            return True
    return False

def classify_query(question: str) -> QueryCategory:
    """Classify query into category for routing"""
    try:
        question_lower = question.lower()
        
        # FIX #2: Use regex for general chat detection
        if is_general_chat(question):
            return QueryCategory.GENERAL_CHAT
        
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
        
        # Check for dealer queries (explicit keywords)
        if any(keyword in question_lower for keyword in DEALER_KEYWORDS):
            return QueryCategory.DEALER
        
        # Check for warehouse queries
        if "warehouse" in question_lower or "godown" in question_lower:
            return QueryCategory.WAREHOUSE
        
        # Check for city queries
        if "city" in question_lower:
            return QueryCategory.CITY
        
        # Check for DN queries
        if "dn" in question_lower or "delivery note" in question_lower or extract_dn_from_question(question):
            return QueryCategory.DN
        
        # Check for POD queries
        if "pod" in question_lower or "proof of delivery" in question_lower:
            return QueryCategory.POD
    
    except Exception as e:
        logger.warning(f"Query classification error: {e}")
    
    return QueryCategory.GENERAL

def get_role_dashboard(user_role: str) -> str:
    """Get role-appropriate dashboard command"""
    if user_role == "ceo":
        return "network health"
    elif user_role == "manager":
        return "executive summary"
    elif user_role == "warehouse":
        return "warehouse performance"
    elif user_role == "dealer":
        return "dealer performance"
    else:
        return "help"

# ==========================================================
# FIX #10: VALIDATED DASHBOARD FORMATTING
# ==========================================================

def format_structured_dashboard(data: Dict[str, Any], dashboard_type: str) -> str:
    """Format structured dashboard responses for WhatsApp with validation"""
    try:
        if dashboard_type == "dealer":
            dealer_name = data.get('dealer_name', data.get('dealer', 'Unknown'))
            total_dns = data.get('total_dns', 0)
            
            # FIX #10: Check for empty dataset
            if total_dns == 0 and dealer_name != 'Unknown':
                return f"""
╔══════════════════════════════╗
║     📊 DEALER DASHBOARD      ║
║        {dealer_name[:25]}        ║
╚══════════════════════════════╝

⚠️ *No activity data available*

This dealer exists but has no delivery records in the current time period.

💡 *Possible reasons:*
• No DNs created recently
• Data sync in progress
• New dealer account

📞 Contact support if this seems incorrect.
"""
            
            return f"""
╔══════════════════════════════╗
║     📊 DEALER DASHBOARD      ║
╚══════════════════════════════╝

📛 *Name:* {dealer_name}
📊 *Health Score:* {data.get('health_score', data.get('score', 0))}/100
⚠️ *Risk Level:* {data.get('risk_level', 'Unknown')}

📦 *Metrics:*
• Total DNs: {total_dns}
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
    except Exception as e:
        logger.error(f"Dashboard formatting error: {e}")
        return data.get('response', str(data))
    
    return data.get('formatted_message', data.get('response', str(data)))

def generate_suggested_followups(intent: str, entity: str = None) -> List[str]:
    """Generate suggested follow-up questions based on intent"""
    try:
        suggestions = {
            "DEALER": ["Show pending DNs", "What are the risks?", "How can we improve?"],
            "CITY": ["What are the top risks?", "Show warehouse performance", "How to improve delivery?"],
            "WAREHOUSE": ["Show bottlenecks", "What is the backlog?", "Recovery plan"],
            "EXECUTIVE": ["Network health score", "Revenue at risk", "Weekly review"],
            "general": ["Show dealer dashboard", "Network health score", "Top risk dealers"]
        }
        
        base_suggestions = suggestions.get(intent, suggestions["general"])
        
        if entity and intent == "DEALER":
            return [f"Details for {entity}", f"Pending DNs for {entity}", f"Risks for {entity}"] + base_suggestions[:2]
        elif entity and intent == "CITY":
            return [f"{entity} risks", f"{entity} warehouse status", f"Improve {entity}"] + base_suggestions[:2]
    except Exception as e:
        logger.warning(f"Suggestions generation error: {e}")
    
    return ["Show dealer dashboard", "Network health score", "Top risk dealers"]

def extract_conversation_summary(session, max_messages: int = 20) -> str:
    """Extract conversation summary from session history"""
    try:
        if not hasattr(session, 'conversation_history') or not session.conversation_history:
            return "No previous conversation"
        
        topics = set()
        entities = []
        topics_mapping = {
            'dealer': 'dealer', 'city': 'city', 'warehouse': 'warehouse',
            'dn': 'delivery note', 'pod': 'POD', 'risk': 'risk',
            'forecast': 'forecast', 'rca': 'root cause'
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
    except Exception as e:
        logger.warning(f"Conversation summary error: {e}")
    
    return "Limited conversation history"

# ==========================================================
# FIX #3 & #6: IMPROVED CONTEXT INJECTION
# ==========================================================

def inject_context_into_question(question: str, context, query_category: QueryCategory = None) -> str:
    """
    Inject context into question for follow-up queries
    FIX #3: Don't inject for general chat
    FIX #6: Clear context on greetings
    """
    try:
        # FIX #3: Never inject dealer context into greetings
        if query_category == QueryCategory.GENERAL_CHAT:
            logger.debug(f"General chat detected - skipping context injection")
            return question
        
        question_lower = question.lower()
        
        is_follow_up = any(pattern in question_lower for pattern in FOLLOW_UP_PATTERNS)
        
        if len(question.split()) <= 4:
            is_follow_up = True
        
        if question_lower in ["details", "more", "elaborate", "explain"]:
            is_follow_up = True
        
        if not is_follow_up:
            return question
        
        if hasattr(context, 'last_intent') and hasattr(context, 'selected_city'):
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
    except Exception as e:
        logger.warning(f"Context injection error: {e}")
    
    return question

def is_executive_query(question: str) -> Optional[str]:
    """Check if query is an executive-level query"""
    try:
        question_lower = question.lower().strip()
        for keyword, command in EXECUTIVE_COMMANDS.items():
            if keyword in question_lower:
                return command
    except Exception as e:
        logger.warning(f"Executive query detection error: {e}")
    return None

def is_weekly_review_query(question: str) -> bool:
    """Check if query is asking for weekly review"""
    try:
        question_lower = question.lower()
        return any(keyword in question_lower for keyword in WEEKLY_REVIEW_KEYWORDS)
    except Exception:
        return False

def format_executive_response(data: Dict[str, Any], response_type: str) -> str:
    """Format executive responses with emojis and structure"""
    try:
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
    except Exception as e:
        logger.error(f"Executive response formatting error: {e}")
    
    return data.get("response", data.get("formatted_message", "No response available"))

# ==========================================================
# INTENT HANDLERS (Split for clarity)
# ==========================================================

async def handle_dn_query(
    dn_number: str,
    phone_number: str,
    user_role: str,
    ai_service,
    query_analytics,
    start_time: float,
    customer_message: str
) -> Dict[str, Any]:
    """Handle DN tracking queries"""
    logger.info(f"🔢 DN detected: {dn_number}")
    
    result = ai_service.process_query(
        question=f"Show DN {dn_number} details",
        user_phone=phone_number,
        user_role=user_role
    )
    
    ai_reply = result.get("response", f"DN {dn_number} information retrieved.")
    intent = "dn_tracking"
    suggestions = generate_suggested_followups("general")
    ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
    
    if query_analytics:
        query_analytics.log_query(
            phone_number=phone_number,
            question=customer_message,
            intent=intent,
            entity=dn_number,
            response_time_ms=int((time.time() - start_time) * 1000),
            ai_used=result.get("ai_used", False),
            confidence=result.get("confidence", 85),
            user_role=user_role,
            provider=result.get("provider_used", "unknown"),
            category="dn"
        )
    
    whatsapp_response = safe_send_reply(phone_number, ai_reply)
    
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
# FIX #5: ENHANCED DEALER QUERY HANDLER WITH LOGGING
# ==========================================================

async def handle_dealer_query(
    customer_message: str,
    phone_number: str,
    user_role: str,
    ai_service,
    session_service,
    query_analytics,
    start_time: float,
    analytics_service
) -> Dict[str, Any]:
    """Handle dealer-specific queries"""
    logger.info(f"🏪 Dealer query detected")
    
    # Try to extract dealer from message
    dealer_name = None
    
    # FIX #1: First try direct dealer detection (no keywords needed)
    dealer_name = detect_dealer_in_message(customer_message, analytics_service, threshold=85)
    
    if dealer_name:
        logger.info(f"🏪 Direct dealer detection: '{dealer_name}'")
    
    # Check if there's a pending selection
    if not dealer_name and session_service:
        selection_result = session_service.handle_dealer_selection(phone_number, customer_message)
        if selection_result.get("handled"):
            dealer_name = selection_result.get("selected_dealer")
            logger.info(f"🏪 Dealer from session selection: '{dealer_name}'")
    
    if not dealer_name:
        # Try to extract from message using RapidFuzz
        from rapidfuzz import process, fuzz
        if analytics_service:
            dealers = analytics_service.dealer_rankings(200)
            dealer_names = [d.get('dealer', '') for d in dealers if d.get('dealer')]
            if dealer_names:
                # FIX #4: Use threshold 85
                matches = process.extract(customer_message, dealer_names, scorer=fuzz.token_sort_ratio, limit=1)
                if matches and matches[0][1] >= 85:
                    dealer_name = matches[0][0]
                    confidence = matches[0][1]
                    # FIX #5: Add logging for dealer match
                    logger.info(f"🏪 Dealer Match = '{dealer_name}' (confidence: {confidence}%)")
    
    if dealer_name:
        question = f"Show dealer {dealer_name} dashboard"
    else:
        question = f"Show dealer dashboard for: {customer_message}"
    
    result = ai_service.process_query(
        question=question,
        user_phone=phone_number,
        user_role=user_role
    )
    
    # FIX #9: Ensure no raw JSON in response
    ai_reply = result.get("response", "Dealer information retrieved.")
    if isinstance(ai_reply, dict):
        ai_reply = ai_reply.get("response", str(ai_reply))
    elif isinstance(ai_reply, str) and ai_reply.startswith('{'):
        try:
            parsed = json.loads(ai_reply)
            ai_reply = parsed.get("response", parsed.get("formatted_message", ai_reply))
        except:
            pass
    
    ai_reply = format_structured_dashboard(result, "dealer") if result.get("structured_data") else ai_reply
    intent = result.get("question_type", "dealer_lookup")
    confidence = result.get("confidence", 85)
    
    suggestions = generate_suggested_followups("DEALER", dealer_name)
    ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
    
    if query_analytics:
        query_analytics.log_query(
            phone_number=phone_number,
            question=customer_message,
            intent=intent,
            entity=dealer_name,
            response_time_ms=int((time.time() - start_time) * 1000),
            ai_used=result.get("ai_used", False),
            confidence=confidence,
            user_role=user_role,
            provider=result.get("provider_used", "unknown"),
            category="dealer"
        )
    
    whatsapp_response = safe_send_reply(phone_number, ai_reply)
    
    return {
        "success": True,
        "customer_message": customer_message,
        "ai_reply": ai_reply,
        "intent": intent,
        "dealer_name": dealer_name,
        "confidence": confidence,
        "suggestions": suggestions[:3],
        "whatsapp_response": whatsapp_response
    }

async def handle_general_chat(
    customer_message: str,
    phone_number: str,
    user_role: str,
    ai_service,
    query_analytics,
    start_time: float
) -> Dict[str, Any]:
    """Handle general chat messages"""
    logger.info(f"💬 General chat detected")
    
    result = ai_service.process_query(
        question=customer_message,
        user_phone=phone_number,
        user_role=user_role
    )
    
    ai_reply = result.get(
        "response",
        "Hello! How can I assist you today?"
    )
    
    # FIX #9: Ensure no raw JSON
    if isinstance(ai_reply, dict):
        ai_reply = ai_reply.get("response", str(ai_reply))
    elif isinstance(ai_reply, str) and ai_reply.startswith('{'):
        try:
            parsed = json.loads(ai_reply)
            ai_reply = parsed.get("response", parsed.get("formatted_message", ai_reply))
        except:
            pass
    
    suggestions = generate_suggested_followups("general")
    ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
    
    if query_analytics:
        query_analytics.log_query(
            phone_number=phone_number,
            question=customer_message,
            intent="general_chat",
            entity=None,
            response_time_ms=int((time.time() - start_time) * 1000),
            ai_used=result.get("ai_used", False),
            confidence=result.get("confidence", 80),
            user_role=user_role,
            provider=result.get("provider_used", "unknown"),
            category="general_chat"
        )
    
    whatsapp_response = safe_send_reply(phone_number, ai_reply)
    
    return {
        "success": True,
        "customer_message": customer_message,
        "ai_reply": ai_reply,
        "intent": "general_chat",
        "suggestions": suggestions[:3],
        "whatsapp_response": whatsapp_response
    }

# ==========================================================
# FIX #9: AI FALLBACK WITH JSON SAFETY
# ==========================================================

async def handle_ai_fallback(
    enhanced_question: str,
    customer_message: str,
    phone_number: str,
    user_role: str,
    ai_service,
    session_service,
    context,
    conversation_summary: str,
    department: str,
    access_level: int,
    query_analytics,
    start_time: float,
    query_category: QueryCategory,
    session
) -> Dict[str, Any]:
    """Handle AI fallback for uncategorized queries"""
    logger.info(f"🤖 AI Fallback handler")
    
    ai_start_time = time.time()
    
    try:
        if ai_service:
            role_context = get_role_context(user_role)
            
            ai_service_context = {
                "selected_dealer": getattr(context, 'selected_dealer', None),
                "selected_city": getattr(context, 'selected_city', None),
                "selected_warehouse": getattr(context, 'selected_warehouse', None),
                "selected_dn": getattr(context, 'selected_dn', None),
                "last_intent": getattr(context, 'last_intent', None),
                "last_question": getattr(context, 'last_question', None),
                "last_response": getattr(context, 'last_response', None),
                "user_role": user_role,
                "executive_mode": getattr(context, 'executive_mode', False),
                "conversation_summary": conversation_summary,
                "department": department,
                "access_level": access_level,
                "role_context": role_context
            }
            
            try:
                result = ai_service.process_query(
                    question=enhanced_question,
                    user_phone=phone_number,
                    user_role=user_role,
                    context=ai_service_context
                )
            except TypeError:
                logger.warning("Context parameter not supported, falling back")
                result = ai_service.process_query(
                    question=enhanced_question,
                    user_phone=phone_number,
                    user_role=user_role
                )
        else:
            result = {
                "success": False,
                "response": "AI service is currently unavailable. Please try again later.",
                "question_type": "error",
                "confidence": 0,
                "ai_used": False,
                "provider_used": "unavailable"
            }
    except Exception as e:
        logger.error(f"AI processing error: {e}")
        result = {
            "success": False,
            "response": "AI service temporarily unavailable. Please try again later.",
            "question_type": "error",
            "confidence": 0,
            "ai_used": False,
            "provider_used": "error"
        }
    
    # FIX #9: CRITICAL - Ensure no raw JSON in response
    ai_reply = result.get("response", "Unable to generate response.")
    
    # If response is a dict, extract string
    if isinstance(ai_reply, dict):
        ai_reply = ai_reply.get("response") or ai_reply.get("formatted_message") or str(ai_reply)
    
    # If response looks like JSON string, parse it
    if isinstance(ai_reply, str) and ai_reply.strip().startswith('{'):
        try:
            parsed = json.loads(ai_reply)
            ai_reply = parsed.get("response") or parsed.get("formatted_message") or ai_reply
        except:
            pass
    
    intent = result.get("question_type", "general")
    confidence = result.get("confidence", 75)
    ai_used = result.get("ai_used", False)
    provider = result.get("provider_used", "unknown")
    entity = result.get("entity")
    
    # Update session context
    try:
        if session_service:
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
    except Exception as e:
        logger.warning(f"Session context update error: {e}")
    
    # Add conversation history
    try:
        if session_service and hasattr(session_service, 'add_to_conversation_history'):
            session_service.add_to_conversation_history(
                phone_number,
                question=enhanced_question,
                response=ai_reply[:500],
                intent=intent,
                entity=entity
            )
    except Exception as e:
        logger.warning(f"Conversation history error: {e}")
    
    suggestions = generate_suggested_followups(intent, entity)
    ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
    
    # Log analytics
    if query_analytics:
        try:
            query_analytics.log_query(
                phone_number=phone_number,
                question=customer_message,
                intent=intent,
                entity=entity,
                response_time_ms=int((time.time() - start_time) * 1000),
                ai_used=ai_used,
                confidence=confidence,
                user_role=user_role,
                provider=provider,
                category=query_category.value
            )
        except Exception as e:
            logger.warning(f"Analytics logging error: {e}")
    
    # Format response based on intent
    result_data = result if result else {}
    
    if intent in ["dealer", "dealer_lookup", "dealer_analysis"]:
        formatted_reply = format_structured_dashboard(result_data.get("structured_data", {}), "dealer")
    elif intent in ["warehouse", "warehouse_analysis"]:
        formatted_reply = format_structured_dashboard(result_data.get("structured_data", {}), "warehouse")
    elif intent in ["executive", "executive_summary", "network_health"]:
        formatted_reply = format_structured_dashboard(result_data.get("structured_data", {}), "executive")
    else:
        formatted_reply = ai_reply
    
    whatsapp_response = safe_send_reply(phone_number, formatted_reply)
    
    logger.info(f"⏱️ AI Time: {int((time.time() - ai_start_time) * 1000)}ms")
    
    return {
        "success": True,
        "customer_message": customer_message,
        "ai_reply": formatted_reply,
        "intent": intent,
        "confidence": confidence,
        "ai_used": ai_used,
        "processing_time_ms": int((time.time() - start_time) * 1000),
        "ai_processing_time_ms": int((time.time() - ai_start_time) * 1000),
        "provider": provider,
        "category": query_category.value,
        "suggestions": suggestions[:3],
        "whatsapp_response": whatsapp_response
    }

# ==========================================================
# WEBHOOK VERIFICATION (IMPROVED - PRODUCTION READY)
# ==========================================================

@router.get("/")
async def webhook_verification(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")

    logger.info("=" * 50)
    logger.info("📞 WEBHOOK VERIFICATION REQUEST")
    logger.info(f"hub.mode = {hub_mode}")
    logger.info(f"hub.verify_token = {hub_verify_token}")
    logger.info(f"hub.challenge = {hub_challenge}")
    logger.info(f"server WHATSAPP_VERIFY_TOKEN = {WHATSAPP_VERIFY_TOKEN}")
    logger.info("=" * 50)

    if (
        hub_mode == "subscribe"
        and hub_verify_token == WHATSAPP_VERIFY_TOKEN
        and hub_challenge
    ):
        logger.success("✅ Webhook verification successful!")
        return PlainTextResponse(content=hub_challenge)

    logger.error("❌ Webhook verification failed!")
    logger.error(
        f"Reason: hub_mode={hub_mode}, "
        f"token_match={hub_verify_token == WHATSAPP_VERIFY_TOKEN}"
    )

    raise HTTPException(
        status_code=403,
        detail="Verification failed. Check your verify token."
    )

# ==========================================================
# RECEIVE WHATSAPP MESSAGE (PRODUCTION READY v7.0)
# ==========================================================

@router.post("/")
async def receive_message(
    request: Request,
    db: Session = Depends(get_db)
):
    start_time = time.time()
    
    # Cleanup old messages periodically
    cleanup_old_messages()
    
    try:
        # Parse incoming payload
        payload = await request.json()
        
        # Priority 2: Move status filtering to the top
        try:
            value = payload["entry"][0]["changes"][0]["value"]
            
            # Skip status updates immediately
            if value.get("statuses"):
                logger.debug("Ignoring WhatsApp status update")
                return {"success": True, "message": "Status update ignored"}
        except Exception:
            pass
        
        # Extract message using centralized function
        parsed_message = extract_message_from_payload(payload)
        
        # Check if we have a valid text message
        if parsed_message["type"] != "text":
            logger.debug(f"Ignoring non-text message type: {parsed_message['type']}")
            return {"success": True, "message": f"Ignored {parsed_message['type']}"}
        
        phone_number = parsed_message["from_phone"]
        customer_message = parsed_message["text"]
        message_id = parsed_message["message_id"]
        
        # Priority 4: Duplicate message protection
        if is_duplicate_message(phone_number, message_id):
            logger.info(f"Duplicate message detected: {message_id}")
            return {"success": True, "message": "Duplicate ignored"}
        
        # Clean logging - no full payload dump
        logger.info(f"📱 WhatsApp from {phone_number}: {customer_message[:100]}")
        
        # ==========================================================
        # SERVICE INITIALIZATION
        # ==========================================================
        
        session_service = None
        try:
            if SESSION_AVAILABLE and get_session_service:
                session_service = get_session_service(db)
        except Exception as e:
            logger.error(f"Session service init failed: {e}")
        
        ai_service = None
        try:
            if AI_QUERY_AVAILABLE and get_ai_query_service:
                ai_service = get_ai_query_service(db)
        except Exception as e:
            logger.error(f"AI service init failed: {e}")
        
        analytics_service = None
        try:
            if ANALYTICS_AVAILABLE and get_analytics_service:
                analytics_service = get_analytics_service(db)
        except Exception as e:
            logger.error(f"Analytics service init failed: {e}")
        
        query_analytics = None
        try:
            if QUERY_ANALYTICS_AVAILABLE and get_query_analytics_service:
                query_analytics = get_query_analytics_service(db)
        except Exception as e:
            logger.warning(f"Query analytics not available: {e}")
        
        city_service = None
        try:
            if CITY_MASTER_AVAILABLE and get_city_master_service:
                city_service = get_city_master_service(db)
        except Exception as e:
            logger.warning(f"City master service not available: {e}")
        
        root_cause_service = None
        try:
            if ROOT_CAUSE_AVAILABLE and get_root_cause_service:
                root_cause_service = get_root_cause_service(db)
        except Exception as e:
            logger.debug(f"Root cause service not available: {e}")
        
        recommendation_service = None
        try:
            if RECOMMENDATION_AVAILABLE and get_recommendation_service:
                recommendation_service = get_recommendation_service(db)
        except Exception as e:
            logger.debug(f"Recommendation service not available: {e}")
        
        forecast_service = None
        try:
            if FORECAST_AVAILABLE and get_forecast_service:
                forecast_service = get_forecast_service(db)
        except Exception as e:
            logger.debug(f"Forecast service not available: {e}")
        
        # Get user role
        user_metadata = get_user_role_from_db(phone_number, db)
        user_role = user_metadata["role"]
        department = user_metadata["department"]
        user_name = user_metadata["name"]
        access_level = user_metadata.get("access_level", 1)
        
        # Session context
        session = None
        context = None
        
        try:
            if session_service:
                session = session_service.get_or_create_session(
                    phone_number=phone_number,
                    user_role=user_role,
                    user_name=user_name,
                    department=department
                )
                session_service.update_activity(phone_number)
                context = session_service.get_context(phone_number)
            else:
                context = type('SimpleContext', (), {
                    'selected_dealer': None,
                    'selected_city': None,
                    'selected_warehouse': None,
                    'selected_dn': None,
                    'last_intent': None,
                    'last_question': None,
                    'last_response': None,
                    'executive_mode': False
                })()
        except Exception as e:
            logger.error(f"Session context error: {e}")
            context = type('SimpleContext', (), {
                'selected_dealer': None,
                'selected_city': None,
                'selected_warehouse': None,
                'selected_dn': None,
                'last_intent': None,
                'last_question': None,
                'last_response': None,
                'executive_mode': False
            })()
        
        # ==========================================================
        # PRIORITY 5: INTENT ROUTING SECTION
        # ==========================================================
        
        # FIX #8: Check for DN first (high priority with enhanced patterns)
        dn_number = extract_dn_from_question(customer_message)
        if dn_number and ai_service:
            return await handle_dn_query(
                dn_number, phone_number, user_role, ai_service,
                query_analytics, start_time, customer_message
            )
        
        # Classify query intent
        query_category = classify_query(customer_message)
        logger.info(f"📊 Intent: {query_category.value}")
        
        # ==========================================================
        # FIX #1: DIRECT DEALER DETECTION (NO KEYWORDS NEEDED)
        # ==========================================================
        
        # Try to detect dealer in message even without keywords
        detected_dealer = None
        if ai_service and analytics_service:
            detected_dealer = detect_dealer_in_message(customer_message, analytics_service, threshold=85)
            if detected_dealer:
                logger.info(f"🏪 Direct dealer detection changed category from {query_category.value} to DEALER")
                query_category = QueryCategory.DEALER
        
        # ==========================================================
        # FIX #6: CLEAR CONTEXT ON GREETINGS
        # ==========================================================
        
        if query_category == QueryCategory.GENERAL_CHAT:
            try:
                if session_service:
                    session_service.update_session_context(
                        phone_number,
                        selected_dealer=None,
                        selected_dn=None,
                        selected_city=None,
                        selected_warehouse=None,
                        last_intent=None,
                        last_question=None
                    )
                    logger.info(f"🧹 Cleared context for {phone_number} due to greeting")
            except Exception as e:
                logger.warning(f"Failed to clear context on greeting: {e}")
        
        # ==========================================================
        # ROUTE BY INTENT
        # ==========================================================
        
        # 1. Executive queries
        if query_category == QueryCategory.EXECUTIVE and user_role in ["ceo", "manager"]:
            executive_command = is_executive_query(customer_message)
            
            if executive_command and analytics_service:
                # Executive processing here (keeping original logic)
                inventory_data = {"formatted": "N/A"}
                try:
                    if analytics_service:
                        inventory_data = analytics_service.inventory_at_risk()
                except Exception as e:
                    logger.error(f"Inventory Error: {e}")
                
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
                else:
                    summary = analytics_service.executive_summary()
                    ai_reply = format_executive_response(summary, "executive_summary")
                    intent = "executive_summary"
                    confidence = 85
                
                suggestions = generate_suggested_followups("EXECUTIVE")
                ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
                
                whatsapp_response = safe_send_reply(phone_number, ai_reply)
                
                return {
                    "success": True,
                    "customer_message": customer_message,
                    "ai_reply": ai_reply,
                    "intent": intent,
                    "confidence": confidence,
                    "suggestions": suggestions[:3],
                    "whatsapp_response": whatsapp_response,
                    "processing_time_ms": int((time.time() - start_time) * 1000)
                }
        
        # 2. Dealer queries (including direct detection)
        if query_category == QueryCategory.DEALER and ai_service:
            return await handle_dealer_query(
                customer_message, phone_number, user_role, ai_service,
                session_service, query_analytics, start_time, analytics_service
            )
        
        # 3. General chat (greetings, small talk)
        if query_category == QueryCategory.GENERAL_CHAT and ai_service:
            return await handle_general_chat(
                customer_message, phone_number, user_role,
                ai_service, query_analytics, start_time
            )
        
        # 4. Root cause analysis
        if query_category == QueryCategory.RCA and root_cause_service:
            try:
                rca_result = root_cause_service.analyze(customer_message)
                ai_reply = rca_result.get("formatted_message", rca_result.get("response", "Root cause analysis complete."))
                suggestions = generate_suggested_followups("general")
                ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
                whatsapp_response = safe_send_reply(phone_number, ai_reply)
                
                return {
                    "success": True,
                    "customer_message": customer_message,
                    "ai_reply": ai_reply,
                    "intent": "root_cause_analysis",
                    "suggestions": suggestions[:3],
                    "whatsapp_response": whatsapp_response,
                    "processing_time_ms": int((time.time() - start_time) * 1000)
                }
            except Exception as e:
                logger.error(f"Root cause error: {e}")
        
        # 5. Forecast queries
        if query_category == QueryCategory.FORECAST and forecast_service:
            try:
                forecast_result = forecast_service.generate_forecast(customer_message)
                ai_reply = forecast_result.get("formatted_message", forecast_result.get("response", "Forecast generated."))
                suggestions = generate_suggested_followups("general")
                ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
                whatsapp_response = safe_send_reply(phone_number, ai_reply)
                
                return {
                    "success": True,
                    "customer_message": customer_message,
                    "ai_reply": ai_reply,
                    "intent": "forecast",
                    "suggestions": suggestions[:3],
                    "whatsapp_response": whatsapp_response,
                    "processing_time_ms": int((time.time() - start_time) * 1000)
                }
            except Exception as e:
                logger.error(f"Forecast error: {e}")
        
        # 6. Recommendation queries
        if query_category == QueryCategory.RECOMMENDATION and recommendation_service:
            try:
                rec_result = recommendation_service.generate(customer_message)
                ai_reply = rec_result.get("formatted_message", rec_result.get("response", "Recommendations generated."))
                suggestions = generate_suggested_followups("general")
                ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
                whatsapp_response = safe_send_reply(phone_number, ai_reply)
                
                return {
                    "success": True,
                    "customer_message": customer_message,
                    "ai_reply": ai_reply,
                    "intent": "recommendation",
                    "suggestions": suggestions[:3],
                    "whatsapp_response": whatsapp_response,
                    "processing_time_ms": int((time.time() - start_time) * 1000)
                }
            except Exception as e:
                logger.error(f"Recommendation error: {e}")
        
        # 7. Dashboard commands
        if any(cmd in customer_message.lower() for cmd in DASHBOARD_COMMANDS):
            dashboard_type = get_role_dashboard(user_role)
            
            if analytics_service:
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
            else:
                ai_reply = "Dashboard temporarily unavailable."
            
            suggestions = generate_suggested_followups("general")
            ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
            whatsapp_response = safe_send_reply(phone_number, ai_reply)
            
            return {
                "success": True,
                "customer_message": customer_message,
                "ai_reply": ai_reply,
                "intent": "role_dashboard",
                "suggestions": suggestions[:3],
                "whatsapp_response": whatsapp_response,
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # 8. Clear context command
        if customer_message.lower() in ["clear context", "reset", "new conversation", "clear"]:
            try:
                if session_service:
                    session_service.clear_context(phone_number)
            except Exception as e:
                logger.error(f"Clear context error: {e}")
            
            ai_reply = "✅ Conversation context cleared. Starting fresh. How can I help you?"
            suggestions = generate_suggested_followups("general")
            ai_reply += f"\n\n💡 *Try:*\n• " + "\n• ".join(suggestions[:3])
            whatsapp_response = safe_send_reply(phone_number, ai_reply)
            
            return {
                "success": True,
                "customer_message": customer_message,
                "ai_reply": ai_reply,
                "intent": "context_cleared",
                "suggestions": suggestions[:3],
                "whatsapp_response": whatsapp_response,
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # ==========================================================
        # PRIORITY 9: AI FALLBACK (Final catch-all)
        # ==========================================================
        
        # FIX #3: Only inject context if not general chat
        if query_category != QueryCategory.GENERAL_CHAT:
            enhanced_question = inject_context_into_question(customer_message, context, query_category)
        else:
            enhanced_question = customer_message
            
        if enhanced_question != customer_message:
            logger.info(f"🔄 Context injected: '{customer_message}' -> '{enhanced_question}'")
        
        # Get conversation summary
        conversation_summary = extract_conversation_summary(session, max_messages=20)
        
        # Use AI fallback for everything else
        return await handle_ai_fallback(
            enhanced_question, customer_message, phone_number, user_role,
            ai_service, session_service, context, conversation_summary,
            department, access_level, query_analytics, start_time,
            query_category, session
        )
    
    except Exception as e:
        logger.exception(f"UNHANDLED WEBHOOK ERROR: {e}")
        return {
            "success": False,
            "message": "An unexpected error occurred. Our team has been notified.",
            "error": str(e) if str(e) else "Unknown error"
        }


# ==========================================================
# TEST AND STATUS ENDPOINTS
# ==========================================================

@router.get("/test")
async def test_webhook():
    return {
        "success": True,
        "message": "Webhook is active - Production Ready v7.0",
        "features": [
            "Safe Imports",
            "Fixed Indentation",
            "Status Filtering at Top",
            "Centralized Message Extraction",
            "Duplicate Message Protection",
            "Intent Routing Section",
            "General Chat Handler",
            "Unified WhatsApp Reply",
            "AI Fallback Route",
            "Improved Dealer Detection",
            "Processing Timers",
            "Split Handlers",
            "Direct Dealer Detection (No Keywords)",
            "Regex Word Boundaries",
            "Context Pollution Prevention",
            "Threshold 85",
            "Dealer Match Logging",
            "Context Clearing on Greetings",
            "Enhanced DN Patterns",
            "JSON Response Safety"
        ]
    }


@router.get("/health")
async def health_check():
    """Comprehensive health check"""
    return {
        "status": "healthy",
        "service": "WhatsApp Webhook - Production Ready v7.0",
        "version": "7.0.0",
        "services_status": {
            "ai_query_service": AI_QUERY_AVAILABLE,
            "session_service": SESSION_AVAILABLE,
            "analytics_service": ANALYTICS_AVAILABLE,
            "query_analytics": QUERY_ANALYTICS_AVAILABLE,
            "semantic_search": SEMANTIC_SEARCH_AVAILABLE,
            "root_cause": ROOT_CAUSE_AVAILABLE,
            "recommendation": RECOMMENDATION_AVAILABLE,
            "forecast": FORECAST_AVAILABLE,
            "city_master": CITY_MASTER_AVAILABLE
        },
        "timestamp": datetime.utcnow().isoformat()
    }
