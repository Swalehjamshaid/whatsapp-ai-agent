# ==========================================================
# FILE: app/services/groq_service.py (v1.1 - AI INTELLIGENCE LAYER)
# ==========================================================
# PURPOSE: Groq API Integration with Governance
# ==========================================================

import json
from typing import Optional, Dict, Any, List
from loguru import logger

from app.config import config
from app.schemas.schema_service import get_schema_service


class GroqService:
    """AI INTELLIGENCE LAYER - Groq API Integration Only"""
    
    def __init__(self):
        self.api_key = getattr(config, 'GROQ_API_KEY', '')
        self.model = getattr(config, 'GROQ_MODEL', 'llama-3.3-70b-versatile')
        self.is_available = bool(self.api_key)
        self.schema = get_schema_service()
        self._client = None
        
        if self.is_available:
            try:
                from groq import Groq
                self._client = Groq(api_key=self.api_key)
                logger.info("GroqService initialized")
            except ImportError:
                self.is_available = False
                logger.warning("Groq library not available")
    
    def generate_executive_summary(self, insights: Dict[str, Any]) -> Optional[str]:
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
        if not self.is_available:
            return None
        try:
            response = self._call_groq([
                {"role": "system", "content": "You are a Root Cause Analyst. Identify key issues."},
                {"role": "user", "content": f"Issue: {issue}\nData: {json.dumps(data, default=str)}"}
            ])
            if response:
                return f"🔍 *Root Cause Analysis*\n\n{response}"
            return None
        except Exception as e:
            logger.error(f"Groq root cause analysis failed: {e}")
            return None
    
    def generate_recommendations(self, data: Dict[str, Any]) -> Optional[str]:
        if not self.is_available:
            return None
        try:
            response = self._call_groq([
                {"role": "system", "content": "You are a Logistics Analyst. Provide actionable recommendations."},
                {"role": "user", "content": f"Data: {json.dumps(data, default=str)}"}
            ])
            if response:
                return f"💡 *Recommendations*\n\n{response}"
            return None
        except Exception as e:
            logger.error(f"Groq recommendations failed: {e}")
            return None
    
    def chat(self, user_message: str, context: Optional[Dict] = None) -> str:
        if not self.is_available:
            return self._get_fallback_response(user_message)
        try:
            system_prompt = "You are a Logistics AI Assistant. Be helpful, concise, and professional."
            context_note = f"\n[Context: Previous conversation was about dealer '{context.get('last_dealer')}']" if context and context.get("last_dealer") else ""
            response = self._call_groq([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{user_message}{context_note}"}
            ])
            return response if response else self._get_fallback_response(user_message)
        except Exception as e:
            logger.error(f"Groq chat failed: {e}")
            return self._get_fallback_response(user_message)
    
    def _call_groq(self, messages: List[Dict[str, str]]) -> Optional[str]:
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
        q_lower = question.lower()
        if any(w in q_lower for w in ['help', 'what can you do']):
            return """📋 *AI Logistics Assistant - Help*

*DN Tracking:* Send any 10+ digit DN number
*Dealer:* "Show dealer ABC Traders" or "ABC Traders revenue"
*Warehouse:* "Lahore warehouse summary"
*Pending:* "Pending deliveries" or "Pending POD"
*Performance:* "ABC Traders performance"
*Rankings:* "Top 10 dealers by revenue"
*Executive:* "Key issues" or "Critical alerts"

Need help? Just ask! 🤖"""
        if any(w in q_lower for w in ['hello', 'hi', 'hey']):
            return "👋 Hello! I'm your Logistics AI Assistant. How can I help?"
        return f"I understand you're asking: {question[:100]}\n\nType 'Help' for available commands."


def get_groq_service() -> GroqService:
    return GroqService()
