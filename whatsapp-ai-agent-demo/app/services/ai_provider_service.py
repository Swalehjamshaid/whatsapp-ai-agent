# ==========================================================
# FILE: app/services/ai_provider_service.py (ENTERPRISE v5.2 - SIMPLIFIED)
# ==========================================================
# PURPOSE: Groq AI Layer - Chat, Insights, Analysis
# ARCHITECTURE: ai_query_service → ai_provider_service → Groq API
# ==========================================================

import json
import time
from typing import Dict, Any, Optional, List
from loguru import logger

# Simple Groq import - no complex error handling that causes crashes
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq SDK not installed")

from app.config import config


# ==========================================================
# CONSTANTS
# ==========================================================

GROQ_TIMEOUT = 15
MAX_HISTORY_PER_USER = 20


# ==========================================================
# MAIN AI PROVIDER SERVICE (Simplified - No Circular Imports)
# ==========================================================

class AIProviderService:
    """
    Groq AI Layer - Simplified version that won't cause import crashes
    """
    
    def __init__(self):
        self.client = None
        self.provider = None
        self.model = None
        self.conversation_history = {}
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "start_time": time.time()
        }
        self._initialize_provider()
        logger.info(f"AI Provider Service v5.2 initialized")
    
    def _initialize_provider(self):
        """Initialize Groq AI provider."""
        if not GROQ_AVAILABLE:
            logger.warning("Groq SDK not available")
            return
        
        api_key = getattr(config, 'GROQ_API_KEY', None)
        if not api_key:
            logger.warning("GROQ_API_KEY not configured")
            return
        
        try:
            self.client = Groq(api_key=api_key)
            self.provider = "groq"
            self.model = getattr(config, 'GROQ_MODEL', 'mixtral-8x7b-32768')
            logger.info(f"✅ Groq AI initialized with model: {self.model}")
        except Exception as e:
            logger.error(f"Groq init failed: {e}")
            self.client = None
    
    def _get_conversation_context(self, user_id: str) -> List[Dict]:
        """Get conversation history for a user."""
        if user_id not in self.conversation_history:
            self.conversation_history[user_id] = []
        return self.conversation_history[user_id]
    
    def _add_to_history(self, user_id: str, role: str, content: str):
        """Add message to conversation history."""
        history = self._get_conversation_context(user_id)
        history.append({"role": role, "content": content})
        if len(history) > MAX_HISTORY_PER_USER * 2:
            self.conversation_history[user_id] = history[-MAX_HISTORY_PER_USER * 2:]
    
    def _call_groq(self, messages: List[Dict], temperature: float = None, max_tokens: int = None, request_id: str = None) -> str:
        """Call Groq API with proper error handling."""
        if not self.client:
            return self._fallback_response()
        
        temp = temperature or 0.3
        tokens = max_tokens or 500
        req_id = request_id or "unknown"
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temp,
                max_tokens=tokens,
                timeout=GROQ_TIMEOUT
            )
            result = response.choices[0].message.content
            logger.debug(f"[{req_id}] Groq API call successful")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] Groq API error: {e}")
            return self._fallback_response()
    
    def _fallback_response(self) -> str:
        """Fallback response when AI is unavailable."""
        return """I'm here to help with logistics insights!

• DN Status - Send any 10+ digit number to track
• Dealer Performance - Ask about any dealer
• Pending Deliveries - "Pending POD"
• KPI Metrics - "Executive dashboard"
• Control Tower - "Control tower"

What would you like to know?"""
    
    # ==========================================================
    # PUBLIC METHODS
    # ==========================================================
    
    def chat(self, message: str, user_id: str = "guest", request_id: str = None) -> str:
        """Main chat method for general AI conversations."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] AI Chat: {message[:100]}")
        
        self.metrics["total_requests"] += 1
        
        if not self.client:
            self.metrics["failed_requests"] += 1
            return self._fallback_response()
        
        # Get conversation history
        history = self._get_conversation_context(user_id)
        
        # Build messages
        system_prompt = getattr(config, 'GROQ_SYSTEM_PROMPT', "You are an AI Logistics Assistant. Be concise and helpful.")
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history (last 5 exchanges)
        for msg in history[-10:]:
            messages.append(msg)
        
        # Add current message
        messages.append({"role": "user", "content": message})
        
        # Get response
        response = self._call_groq(messages, request_id=req_id)
        
        # Save to history
        self._add_to_history(user_id, "user", message)
        self._add_to_history(user_id, "assistant", response)
        
        self.metrics["successful_requests"] += 1
        
        return response
    
    def generate_root_cause_analysis(self, metric: str, data: Dict, request_id: str = None) -> str:
        """Generate root cause analysis."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] Root cause analysis for {metric}")
        
        if not self.client:
            return f"📊 Root cause analysis for {metric} will be available when AI is configured."
        
        prompt = f"Analyze root causes for {metric} issue. Provide 2-3 primary causes and recommendations."
        return self._simple_chat(prompt, request_id)
    
    def generate_recommendations(self, issues: List[str], data: Dict, request_id: str = None) -> str:
        """Generate recommendations."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] Recommendations for {len(issues)} issues")
        
        if not self.client:
            return "💡 Recommendations will be available when AI is configured."
        
        prompt = f"Based on these issues: {issues}, provide actionable recommendations."
        return self._simple_chat(prompt, request_id)
    
    def _simple_chat(self, message: str, request_id: str = None) -> str:
        """Simple chat without history."""
        if not self.client:
            return self._fallback_response()
        
        system_prompt = getattr(config, 'GROQ_SYSTEM_PROMPT', "You are an AI Logistics Assistant.")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message}
        ]
        
        return self._call_groq(messages, max_tokens=400, request_id=request_id)
    
    # ==========================================================
    # HEALTH & METRICS
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check."""
        return {
            "service": "ai_provider",
            "version": "5.2",
            "provider": self.provider,
            "model": self.model,
            "configured": self.client is not None,
            "metrics": self.get_metrics()
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics."""
        total = self.metrics["total_requests"]
        success_rate = (self.metrics["successful_requests"] / max(1, total)) * 100
        
        return {
            "total_requests": self.metrics["total_requests"],
            "successful_requests": self.metrics["successful_requests"],
            "failed_requests": self.metrics["failed_requests"],
            "success_rate": round(success_rate, 2),
            "uptime_seconds": round(time.time() - self.metrics["start_time"], 2),
            "provider": self.provider,
            "model": self.model
        }
    
    def clear_history(self, user_id: str) -> Dict[str, Any]:
        """Clear conversation history for a user."""
        if user_id in self.conversation_history:
            del self.conversation_history[user_id]
            return {"cleared": True, "user_id": user_id}
        return {"cleared": False, "user_id": user_id}


# ==========================================================
# SINGLETON & COMPATIBILITY FUNCTIONS
# ==========================================================

_ai_provider = None


def get_ai_provider() -> AIProviderService:
    """Get or create AI provider singleton."""
    global _ai_provider
    if _ai_provider is None:
        _ai_provider = AIProviderService()
    return _ai_provider


def chat(message: str, user_id: str = "guest", request_id: str = None) -> str:
    """Compatibility function for chat."""
    return get_ai_provider().chat(message, user_id, request_id=request_id)


def generate_root_cause(metric: str, data: Dict, request_id: str = None) -> str:
    """Compatibility function for root cause analysis."""
    return get_ai_provider().generate_root_cause_analysis(metric, data, request_id=request_id)


def generate_recommendations(issues: List[str], data: Dict, request_id: str = None) -> str:
    """Compatibility function for recommendations."""
    return get_ai_provider().generate_recommendations(issues, data, request_id=request_id)


def get_ai_metrics() -> Dict[str, Any]:
    """Get AI service metrics."""
    return get_ai_provider().get_metrics()


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("🤖 AI Provider Service v5.2 - Simplified & Stable")
logger.info(f"   Provider: {get_ai_provider().provider or 'None'}")
logger.info(f"   Configured: {get_ai_provider().client is not None}")
logger.info("   Features: Chat | Root Cause Analysis | Recommendations | Metrics")
logger.info("=" * 60)
