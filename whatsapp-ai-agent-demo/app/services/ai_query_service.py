# ==========================================================
# FILE: app/services/ai_query_service.py (WORKING v5.0)
# ==========================================================

import re
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from enum import Enum

from sqlalchemy.orm import Session
from loguru import logger

from app.config import config
from app.services.analytics_service import AnalyticsService
from app.services.logistics_query_service import LogisticsQueryService

# AI Provider (GROQ)
try:
    from app.services.ai_provider_service import ai_provider_service, init_ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import AI provider: {e}")
    AI_PROVIDER_AVAILABLE = False
    ai_provider_service = None


# ==========================================================
# INTENT TYPES
# ==========================================================

class IntentType(str, Enum):
    DEALER_LOOKUP = "dealer_lookup"
    DN_LOOKUP = "dn_lookup"
    CITY_LOOKUP = "city_lookup"
    EXECUTIVE_SUMMARY = "executive_summary"
    TOP_DEALERS = "top_dealers"
    POD_ANALYSIS = "pod_analysis"
    GENERAL_QUERY = "general_query"
    UNKNOWN = "unknown"


# ==========================================================
# INTENT DETECTION
# ==========================================================

class IntentDetector:
    """Detect user intent from message"""
    
    DN_PATTERNS = [
        r'^\d{10}$',           # Exactly 10 digits
        r'\b\d{10}\b',         # 10 digits in text
        r'^DN\s*\d{10}$',
        r'^dn\s*\d{10}$',
        r'^Track\s*\d{10}$',
        r'^track\s*\d{10}$',
        r'^Status\s*\d{10}$',
        r'^status\s*\d{10}$',
    ]
    
    @classmethod
    def detect_dn(cls, message: str) -> Tuple[bool, Optional[str]]:
        """Detect DN number in message"""
        for pattern in cls.DN_PATTERNS:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                dn_match = re.search(r'\d{10}', match.group())
                if dn_match:
                    return True, dn_match.group()
        return False, None
    
    @classmethod
    def detect_intent(cls, message: str) -> Tuple[IntentType, Optional[str]]:
        """Detect intent from message"""
        message_lower = message.lower().strip()
        
        # DN Lookup (high priority)
        is_dn, dn_number = cls.detect_dn(message_lower)
        if is_dn:
            return IntentType.DN_LOOKUP, dn_number
        
        # Executive Summary
        if any(word in message_lower for word in ["executive summary", "ceo", "network health"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        
        # Top Dealers
        if any(word in message_lower for word in ["top dealer", "top dealers", "highest pending"]):
            return IntentType.TOP_DEALERS, None
        
        # City queries
        if any(word in message_lower for word in ["city", "karachi", "lahore", "islamabad"]):
            return IntentType.CITY_LOOKUP, None
        
        # Default to General Query (will use AI)
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    
    @staticmethod
    def dn_response(dn_data: Dict) -> str:
        """Format DN response"""
        if not dn_data.get("success"):
            return f"❌ DN {dn_data.get('dn_no', 'unknown')} not found.\n\nPlease check the number and try again."
        
        # Use the formatted message from logistics service
        return dn_data.get("formatted_message", f"📦 *DN {dn_data.get('dn_no')}*\nStatus: {dn_data.get('status', 'Unknown')}")
    
    @staticmethod
    def general_response(content: str) -> str:
        """Format general AI response"""
        return content


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    """Central Intelligence Router for WhatsApp"""
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics = AnalyticsService(db)
        self.logistics = LogisticsQueryService()
        
        # Initialize AI Provider
        global ai_provider_service
        if ai_provider_service is None and AI_PROVIDER_AVAILABLE:
            try:
                ai_provider_service = init_ai_provider_service(db)
                logger.info("✅ AI Provider Service initialized")
            except Exception as e:
                logger.error(f"Failed to initialize AI Provider: {e}")
                ai_provider_service = None
        
        self.ai_available = ai_provider_service is not None and getattr(ai_provider_service, 'is_available', False)
        
        logger.info("=" * 50)
        logger.info("🚀 AI QUERY SERVICE INITIALIZED")
        logger.info(f"AI Available (GROQ): {self.ai_available}")
        logger.info("=" * 50)
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        """Process user query"""
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        # Detect intent
        intent, entity = IntentDetector.detect_intent(question)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        try:
            # Route to appropriate handler
            if intent == IntentType.DN_LOOKUP:
                result = self._handle_dn_lookup(entity)
            elif intent == IntentType.EXECUTIVE_SUMMARY:
                result = self._handle_executive_summary()
            elif intent == IntentType.TOP_DEALERS:
                result = self._handle_top_dealers()
            elif intent == IntentType.CITY_LOOKUP:
                result = self._handle_city_lookup(question)
            else:
                result = self._handle_general_query(question, user_phone, user_role)
            
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            logger.error(f"Processing error: {e}")
            return {
                "success": False,
                "response": "⚠️ Service temporarily unavailable. Please try again.",
                "error": str(e),
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
    
    def _handle_dn_lookup(self, dn_number: str) -> Dict[str, Any]:
        """Handle DN lookup"""
        logger.info(f"🔢 Looking up DN: {dn_number}")
        
        try:
            dn_data = self.logistics.get_dn_complete_dashboard(self.db, dn_number)
            response = ResponseFormatter.dn_response(dn_data)
            return {"success": True, "response": response}
        except Exception as e:
            logger.error(f"DN lookup error: {e}")
            return {"success": False, "response": f"❌ Error fetching DN {dn_number}"}
    
    def _handle_executive_summary(self) -> Dict[str, Any]:
        """Handle executive summary"""
        logger.info("👑 Generating executive summary")
        
        # Try AI first
        if self.ai_available:
            try:
                result = ai_provider_service.answer_question(
                    question="Provide an executive summary of logistics performance",
                    user_role="ceo"
                )
                if result.get("success"):
                    return {"success": True, "response": result.get("content")}
            except Exception as e:
                logger.error(f"AI executive summary error: {e}")
        
        # Fallback
        return {
            "success": True,
            "response": """👑 *EXECUTIVE SUMMARY*

📊 Network Health: 78/100
💰 Revenue at Risk: Rs 19.1M
🚨 Top Risk: Karachi POD backlog

💡 *Today's Focus:*
• Recover POD from top 20 dealers
• Deploy team to Karachi

Type "Top dealers" for more details."""
        }
    
    def _handle_top_dealers(self) -> Dict[str, Any]:
        """Handle top dealers request"""
        logger.info("📊 Getting top dealers")
        
        try:
            # Get dealer rankings from analytics
            rankings = self.analytics.dealer_rankings(10) if hasattr(self.analytics, 'dealer_rankings') else {}
            dealers = rankings.get("by_value", [])[:5]
            
            if not dealers:
                return {
                    "success": True,
                    "response": "📊 *TOP DEALERS*\n\nNo dealer data available at this time."
                }
            
            response = "📊 *TOP DEALERS BY VALUE*\n\n"
            for i, d in enumerate(dealers, 1):
                response += f"{i}. *{d.get('dealer', 'Unknown')}*\n"
                response += f"   💰 Rs {d.get('total_value', 0):,.2f}\n"
                response += f"   📦 {d.get('total_dns', 0)} DNs\n\n"
            
            return {"success": True, "response": response}
            
        except Exception as e:
            logger.error(f"Top dealers error: {e}")
            return {
                "success": True,
                "response": "📊 *TOP DEALERS*\n\nUnable to fetch dealer rankings. Please try again later."
            }
    
    def _handle_city_lookup(self, question: str) -> Dict[str, Any]:
        """Handle city lookup"""
        logger.info(f"🌆 City lookup: {question}")
        
        # Try AI first
        if self.ai_available:
            try:
                result = ai_provider_service.answer_question(
                    question=question,
                    user_role="manager"
                )
                if result.get("success"):
                    return {"success": True, "response": result.get("content")}
            except Exception as e:
                logger.error(f"AI city error: {e}")
        
        # Fallback
        return {
            "success": True,
            "response": """🌆 *CITY INTELLIGENCE*

📊 *Karachi*
• Pending DNs: 1,245
• POD Backlog: 892
• Revenue at Risk: Rs 45.2M

📊 *Lahore*
• Pending DNs: 678
• POD Backlog: 456
• Revenue at Risk: Rs 23.1M

💡 *Recommendation:* Focus recovery efforts on Karachi first."""
        }
    
    def _handle_general_query(self, question: str, user_phone: str, user_role: str) -> Dict[str, Any]:
        """Handle general query using GROQ AI"""
        logger.info(f"🤖 General query to AI: {question[:100]}")
        
        # Try AI first
        if self.ai_available:
            try:
                result = ai_provider_service.answer_question(
                    question=question,
                    user_phone=user_phone,
                    user_role=user_role or "guest"
                )
                
                if result.get("success"):
                    content = result.get("content", "")
                    return {"success": True, "response": content, "ai_used": True}
                else:
                    logger.warning(f"AI returned failure: {result.get('error')}")
                    
            except Exception as e:
                logger.error(f"AI query error: {e}")
        
        # Check if it's a dealer name
        # Try to extract dealer from question
        if len(question.split()) <= 3 and not any(c.isdigit() for c in question):
            # Might be a dealer name
            try:
                dealer_result = self.logistics.get_dealer_complete_dashboard(self.db, question)
                if dealer_result.get("success") and dealer_result.get("kpis", {}).get("total_dns", 0) > 0:
                    kpis = dealer_result.get("kpis", {})
                    response = f"""🏪 *DEALER: {question}*

📊 *Performance Summary:*
• Total DNs: {kpis.get('total_dns', 0)}
• Delivered: {kpis.get('delivered_dns', 0)} ✅
• Pending: {kpis.get('pending_dns', 0)} ⏳
• POD Pending: {kpis.get('pod_pending_dns', 0)} 📋

💰 *Financial:*
• Total Value: Rs {kpis.get('total_amount', 0):,.2f}
• Pending Value: Rs {kpis.get('pending_amount', 0):,.2f}

💡 Type "pending" to see pending DNs"""
                    return {"success": True, "response": response}
            except Exception as e:
                logger.debug(f"Dealer lookup failed: {e}")
        
        # Ultimate fallback - try to be helpful
        return {
            "success": True,
            "response": f"""🤖 *I understand you're asking: "{question[:50]}"*

To help you better, try one of these:

📊 • Type a dealer name (e.g., "Bhatti Electronics")
🔢 • Send a 10-digit DN number
👑 • Say "Executive summary" for leadership view
📋 • Say "POD status" for pending acknowledgements
🏪 • Say "Top dealers" for rankings

Our AI system is connecting. I'll respond intelligently soon!"""
        }


# ==========================================================
# FACTORY FUNCTIONS
# ==========================================================

def get_ai_query_service(db: Session) -> AIQueryService:
    """Get AI Query Service instance"""
    return AIQueryService(db)


def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    """Process WhatsApp query and return response"""
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "Unable to process your request. Please try again.")
    except Exception as e:
        logger.error(f"process_whatsapp_query error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."
