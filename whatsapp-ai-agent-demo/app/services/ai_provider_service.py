# ==========================================================
# FILE: app/services/ai_provider_service.py (ENTERPRISE v6.0 - ENHANCED)
# ==========================================================
# PURPOSE: Groq AI Layer - Chat, Insights, Analysis
# ARCHITECTURE: ai_query_service → ai_provider_service → Groq API
#
# IMPROVEMENTS v6.0:
# - ✅ Enhanced error logging with request_id tracking
# - ✅ Added response validation to prevent empty replies
# - ✅ Added request timing for performance monitoring
# - ✅ Improved fallback responses with more context
# - ✅ Added conversation context pruning (keep last 20 exchanges)
# - ✅ Enhanced health check with detailed status
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
            "start_time": time.time(),
            "average_response_time_ms": 0,
            "total_response_time_ms": 0
        }
        self._initialize_provider()
        logger.info(f"AI Provider Service v6.0 initialized")
    
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
        """Add message to conversation history with pruning."""
        history = self._get_conversation_context(user_id)
        history.append({"role": role, "content": content})
        # Keep only last MAX_HISTORY_PER_USER * 2 messages
        if len(history) > MAX_HISTORY_PER_USER * 2:
            self.conversation_history[user_id] = history[-MAX_HISTORY_PER_USER * 2:]
            logger.debug(f"Pruned conversation history for user {user_id}")
    
    def _call_groq(self, messages: List[Dict], temperature: float = None, max_tokens: int = None, request_id: str = None) -> str:
        """Call Groq API with proper error handling and timing."""
        if not self.client:
            return self._fallback_response()
        
        temp = temperature or 0.3
        tokens = max_tokens or 500
        req_id = request_id or "unknown"
        
        start_time = time.time()
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temp,
                max_tokens=tokens,
                timeout=GROQ_TIMEOUT
            )
            result = response.choices[0].message.content
            
            # Calculate response time
            response_time_ms = (time.time() - start_time) * 1000
            
            # Update metrics
            self.metrics["total_response_time_ms"] += response_time_ms
            if self.metrics["total_requests"] > 0:
                self.metrics["average_response_time_ms"] = self.metrics["total_response_time_ms"] / self.metrics["total_requests"]
            
            logger.debug(f"[{req_id}] Groq API call successful in {response_time_ms:.0f}ms")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] Groq API error: {e}")
            return self._fallback_response()
    
    def _fallback_response(self) -> str:
        """Enhanced fallback response when AI is unavailable."""
        return """🤖 *AI Assistant - Limited Mode*

I'm here to help with logistics insights! However, AI services are currently in limited mode.

*Available Commands:*
• Send any 10+ digit number to track DN
• `Pending POD` - Missing proofs
• `Pending PGI` - Pending dispatches
• `Top dealers` - Dealer rankings
• `Top warehouses` - Warehouse rankings
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Control tower` - All alerts
• `Help` - Complete command list

Type `Help` anytime to see all commands!"""
    
    # ==========================================================
    # PUBLIC METHODS
    # ==========================================================
    
    def chat(self, message: str, user_id: str = "guest", request_id: str = None) -> str:
        """
        Main chat method for general AI conversations.
        
        Args:
            message: User's message
            user_id: User identifier for conversation context
            request_id: Request ID for tracing
        
        Returns:
            AI response string (never empty)
        """
        req_id = request_id or "unknown"
        start_time = time.time()
        
        logger.info(f"[{req_id}] AI Chat: {message[:100]}")
        
        self.metrics["total_requests"] += 1
        
        if not self.client:
            self.metrics["failed_requests"] += 1
            return self._fallback_response()
        
        # Get conversation history
        history = self._get_conversation_context(user_id)
        
        # Build messages
        system_prompt = getattr(config, 'GROQ_SYSTEM_PROMPT', "You are an AI Logistics Assistant. Be concise and helpful. If you don't understand a question, politely ask for clarification or suggest available commands.")
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history (last 10 messages = 5 exchanges)
        for msg in history[-10:]:
            messages.append(msg)
        
        # Add current message
        messages.append({"role": "user", "content": message})
        
        # Get response
        response = self._call_groq(messages, request_id=req_id)
        
        # Validate response - ensure it's not empty
        if not response or len(response.strip()) == 0:
            logger.warning(f"[{req_id}] AI returned empty response, using fallback")
            response = "I'm not sure how to respond to that. Could you please rephrase or type 'Help' to see available commands?"
        
        # Save to history
        self._add_to_history(user_id, "user", message)
        self._add_to_history(user_id, "assistant", response)
        
        self.metrics["successful_requests"] += 1
        
        # Calculate and log response time
        response_time_ms = (time.time() - start_time) * 1000
        logger.info(f"[{req_id}] AI response generated in {response_time_ms:.0f}ms ({len(response)} chars)")
        
        return response
    
    def generate_root_cause_analysis(self, metric: str, data: Dict, request_id: str = None) -> str:
        """
        Generate root cause analysis.
        
        Args:
            metric: Metric to analyze (e.g., "delivery delays")
            data: Context data for analysis
            request_id: Request ID for tracing
        
        Returns:
            Analysis text
        """
        req_id = request_id or "unknown"
        start_time = time.time()
        
        logger.info(f"[{req_id}] Root cause analysis for {metric}")
        
        if not self.client:
            return f"📊 *Root Cause Analysis - {metric}*\n\nAI services are currently in limited mode. Please try again later or use the 'Control tower' command for current alerts."
        
        # Build enhanced prompt with provided data
        prompt = f"""Analyze root causes for {metric} issue in logistics.

Context Data: {json.dumps(data, default=str)[:500]}

Please provide:
1. 2-3 primary root causes
2. Impact assessment
3. Recommended actions

Keep response concise and actionable (max 300 words)."""
        
        response = self._simple_chat(prompt, request_id)
        
        # Validate response
        if not response or len(response.strip()) == 0:
            response = f"📊 *Root Cause Analysis - {metric}*\n\nUnable to complete analysis at this time. Please check the control tower for current issues."
        
        response_time_ms = (time.time() - start_time) * 1000
        logger.info(f"[{req_id}] Root cause analysis completed in {response_time_ms:.0f}ms")
        
        return response
    
    def generate_recommendations(self, issues: List[str], data: Dict, request_id: str = None) -> str:
        """
        Generate recommendations based on issues.
        
        Args:
            issues: List of issues to address
            data: Context data
            request_id: Request ID for tracing
        
        Returns:
            Recommendations text
        """
        req_id = request_id or "unknown"
        start_time = time.time()
        
        logger.info(f"[{req_id}] Recommendations for {len(issues)} issues")
        
        if not self.client:
            return "💡 *Recommendations*\n\nAI services are currently in limited mode. Please check the executive dashboard for KPI targets and current performance."
        
        prompt = f"""Based on these logistics issues: {', '.join(issues[:5])}

Provide actionable recommendations (3-5 bullet points) to address these issues. Focus on:
- Immediate actions
- Process improvements
- Monitoring suggestions

Keep response concise and practical."""
        
        response = self._simple_chat(prompt, request_id)
        
        # Validate response
        if not response or len(response.strip()) == 0:
            response = "💡 *Recommendations*\n\nPlease review the executive dashboard for current KPIs and identify areas needing attention."
        
        response_time_ms = (time.time() - start_time) * 1000
        logger.info(f"[{req_id}] Recommendations generated in {response_time_ms:.0f}ms")
        
        return response
    
    def _simple_chat(self, message: str, request_id: str = None) -> str:
        """Simple chat without history."""
        if not self.client:
            return self._fallback_response()
        
        req_id = request_id or "unknown"
        
        system_prompt = getattr(config, 'GROQ_SYSTEM_PROMPT', "You are an AI Logistics Assistant. Provide helpful, concise responses.")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message}
        ]
        
        return self._call_groq(messages, max_tokens=400, request_id=req_id)
    
    # ==========================================================
    # HEALTH & METRICS
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """
        Enhanced health check with detailed status.
        
        Returns:
            Comprehensive health status
        """
        uptime_seconds = time.time() - self.metrics["start_time"]
        
        return {
            "service": "ai_provider",
            "version": "6.0",
            "provider": self.provider,
            "model": self.model,
            "configured": self.client is not None,
            "status": "healthy" if self.client is not None else "degraded",
            "uptime_seconds": round(uptime_seconds, 2),
            "uptime_hours": round(uptime_seconds / 3600, 2),
            "metrics": self.get_metrics(),
            "capabilities": {
                "chat": self.client is not None,
                "root_cause_analysis": self.client is not None,
                "recommendations": self.client is not None,
                "conversation_history": True
            }
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get service metrics with enhanced tracking.
        
        Returns:
            Service performance metrics
        """
        total = self.metrics["total_requests"]
        success_rate = (self.metrics["successful_requests"] / max(1, total)) * 100
        
        return {
            "total_requests": self.metrics["total_requests"],
            "successful_requests": self.metrics["successful_requests"],
            "failed_requests": self.metrics["failed_requests"],
            "success_rate": round(success_rate, 2),
            "uptime_seconds": round(time.time() - self.metrics["start_time"], 2),
            "average_response_time_ms": round(self.metrics["average_response_time_ms"], 2),
            "provider": self.provider,
            "model": self.model,
            "active_conversations": len(self.conversation_history),
            "total_conversation_messages": sum(len(h) for h in self.conversation_history.values())
        }
    
    def clear_history(self, user_id: str) -> Dict[str, Any]:
        """
        Clear conversation history for a user.
        
        Args:
            user_id: User identifier
        
        Returns:
            Result of operation
        """
        if user_id in self.conversation_history:
            message_count = len(self.conversation_history[user_id])
            del self.conversation_history[user_id]
            logger.info(f"Cleared {message_count} messages for user {user_id}")
            return {"cleared": True, "user_id": user_id, "messages_cleared": message_count}
        return {"cleared": False, "user_id": user_id, "messages_cleared": 0}
    
    def get_conversation_summary(self, user_id: str) -> Dict[str, Any]:
        """
        Get conversation summary for a user.
        
        Args:
            user_id: User identifier
        
        Returns:
            Conversation statistics
        """
        history = self._get_conversation_context(user_id)
        return {
            "user_id": user_id,
            "total_messages": len(history),
            "exchanges": len(history) // 2,
            "has_history": len(history) > 0
        }


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


def clear_user_history(user_id: str) -> Dict[str, Any]:
    """Clear conversation history for a user."""
    return get_ai_provider().clear_history(user_id)


def get_user_conversation_summary(user_id: str) -> Dict[str, Any]:
    """Get conversation summary for a user."""
    return get_ai_provider().get_conversation_summary(user_id)


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("🤖 AI Provider Service v6.0 - Enhanced & Stable")
logger.info(f"   Provider: {get_ai_provider().provider or 'None'}")
logger.info(f"   Model: {get_ai_provider().model or 'N/A'}")
logger.info(f"   Configured: {get_ai_provider().client is not None}")
logger.info(f"   Status: {'✅ Healthy' if get_ai_provider().client else '⚠️ Degraded'}")
logger.info("   Features: Chat | Root Cause Analysis | Recommendations | Metrics | Conversation History")
logger.info("   Improvements: Response Validation | Request Timing | Enhanced Fallbacks | History Pruning")
logger.info("=" * 60)
