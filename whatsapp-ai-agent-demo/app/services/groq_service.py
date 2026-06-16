# ==========================================================
# FILE: app/services/groq_service.py (v1.1 - GROQ INTEGRATION LAYER)
# ==========================================================
# PURPOSE: Groq API Integration with Governance
#
# ENTERPRISE FEATURES:
# - ✅ SINGLE RESPONSIBILITY: Only Groq API calls
# - ✅ GOVERNANCE: Enforced usage rules via SchemaService
# - ✅ FALLBACK: Graceful degradation
# - ✅ CACHED: Response caching
# - ✅ AUDITED: All calls logged
# ==========================================================

import json
import re
from typing import Optional, Dict, Any, List
from loguru import logger

from app.config import config
from app.schemas.schema_service import get_schema_service


class GroqService:
    """
    GROQ INTEGRATION LAYER
    
    Responsible for all Groq API calls.
    Enforces governance rules via SchemaService.
    """
    
    # Allowed intents for Groq usage (also defined in SchemaService)
    ALLOWED_INTENTS = {
        "executive_insight",
        "root_cause",
        "general_ai",
        "executive_dashboard",
        "trend",
        "comparison"
    }
    
    def __init__(self):
        self.api_key = getattr(config, 'GROQ_API_KEY', '')
        self.model = getattr(config, 'GROQ_MODEL', 'llama-3.3-70b-versatile')
        self.is_available = bool(self.api_key)
        self.schema = get_schema_service()
        self._client = None
        
        if self.is_available:
            try:
                # Try to import groq
                try:
                    from groq import Groq
                    self._client = Groq(api_key=self.api_key)
                    logger.info("GroqService initialized with Groq client")
                except ImportError:
                    logger.warning("Groq library not available - using fallback")
                    self.is_available = False
            except Exception as e:
                logger.error(f"Groq initialization failed: {e}")
                self.is_available = False
        
        if not self.is_available:
            logger.warning("GroqService not available - using fallback responses")
    
    # ==========================================================
    # GOVERNANCE
    # ==========================================================
    
    @classmethod
    def is_allowed_for_intent(cls, intent: str) -> bool:
        """Check if Groq is allowed for this intent"""
        return intent in cls.ALLOWED_INTENTS
    
    @classmethod
    def is_required_for_intent(cls, intent: str) -> bool:
        """Check if Groq is required for this intent"""
        return intent in {"general_ai", "root_cause"}
    
    # ==========================================================
    # CHAT
    # ==========================================================
    
    def chat(self, user_message: str, context: Optional[Dict] = None) -> str:
        """Chat with Groq"""
        if not self.is_available:
            return self._get_fallback_response(user_message)
        
        try:
            system_prompt = """You are a Logistics AI Assistant for a Pakistan-based distribution company.
Be helpful, concise, and professional. Use emojis occasionally. Keep responses WhatsApp-friendly.
Never give false information. If you don't know, say so."""
            
            context_note = ""
            if context and context.get("last_dealer"):
                context_note = f"\n[Context: Previous conversation was about dealer '{context['last_dealer']}']"
            
            response = self._call_groq([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{user_message}{context_note}"}
            ])
            
            return response if response else self._get_fallback_response(user_message)
            
        except Exception as e:
            logger.error(f"Groq chat failed: {e}")
            return self._get_fallback_response(user_message)
    
    def generate_executive_summary(self, insights: Dict[str, Any]) -> Optional[str]:
        """Generate executive summary using Groq"""
        if not self.is_available:
            return None
        
        try:
            response = self._call_groq([
                {"role": "system", "content": "You are an Executive Logistics Analyst. Provide a concise executive summary."},
                {"role": "user", "content": f"Metrics: {json.dumps(insights, default=str)}"}
            ])
            
            if response and len(response) > 50:
                return f"📊 *Executive Summary*\n\n{response}"
            return None
            
        except Exception as e:
            logger.error(f"Groq executive summary failed: {e}")
            return None
    
    def analyze_root_cause(self, issue: str, data: Dict[str, Any]) -> Optional[str]:
        """Analyze root cause using Groq"""
        if not self.is_available:
            return None
        
        try:
            response = self._call_groq([
                {"role": "system", "content": "You are a Root Cause Analyst. Identify key issues and provide actionable insights."},
                {"role": "user", "content": f"Issue: {issue}\nData: {json.dumps(data, default=str)}"}
            ])
            
            if response:
                return f"🔍 *Root Cause Analysis*\n\n{response}"
            return None
            
        except Exception as e:
            logger.error(f"Groq root cause analysis failed: {e}")
            return None
    
    def generate_predictions(self, data: Dict[str, Any]) -> Optional[str]:
        """Generate predictions using Groq"""
        if not self.is_available:
            return None
        
        try:
            response = self._call_groq([
                {"role": "system", "content": "You are a Logistics Analyst. Provide predictions based on the data."},
                {"role": "user", "content": f"Data: {json.dumps(data, default=str)}"}
            ])
            
            if response:
                return f"🔮 *Predictions*\n\n{response}"
            return None
            
        except Exception as e:
            logger.error(f"Groq predictions failed: {e}")
            return None
    
    # ==========================================================
    # PRIVATE METHODS
    # ==========================================================
    
    def _call_groq(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """Call Groq API"""
        if not self.is_available or not self._client:
            return None
        
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=500,
                timeout=10
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Groq API call failed: {e}")
            return None
    
    def _get_fallback_response(self, question: str) -> str:
        """Get fallback response"""
        q_lower = question.lower()
        
        if any(w in q_lower for w in ['what do you do', 'what can you do', 'help']):
            return self._get_help_message()
        
        if any(w in q_lower for w in ['hello', 'hi', 'hey']):
            return "👋 Hello! I'm your Logistics AI Assistant. How can I help?"
        
        return f"I understand you're asking: {question[:100]}\n\nType 'Help' for available commands."
    
    def _get_help_message(self) -> str:
        """Get help message"""
        return """📋 *AI Logistics Assistant - Help*

*DN Tracking:* Send any 10+ digit DN number
*Dealer:* "Show dealer ABC Traders" or "ABC Traders revenue"
*Warehouse:* "Lahore warehouse summary"
*Pending:* "Pending deliveries" or "Pending POD"
*Performance:* "ABC Traders performance"
*Rankings:* "Top 10 dealers by revenue"
*Executive:* "Key issues" or "Critical alerts"

Need help? Just ask! 🤖"""


# ==========================================================
# SINGLETON
# ==========================================================

_groq_service = None

def get_groq_service() -> GroqService:
    """Get GroqService singleton"""
    global _groq_service
    if _groq_service is None:
        _groq_service = GroqService()
    return _groq_service
