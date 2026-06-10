# ==========================================================
# FILE: app/services/ai_provider_service.py (ENTERPRISE v5.0)
# ==========================================================
# PURPOSE: Groq AI Layer - Chat, Insights, Analysis
# ARCHITECTURE: ai_query_service → ai_provider_service → Groq API
# ==========================================================

import os
import json
import time
from typing import Dict, Any, Optional, List
from datetime import datetime
from loguru import logger
from cachetools import TTLCache

# CRITICAL IMPROVEMENT 1: Remove OpenAI completely
try:
    from groq import Groq
    from groq import APIError, APIConnectionError, RateLimitError, APIStatusError
    GROQ_AVAILABLE = True
    logger.info("✅ Groq SDK loaded successfully")
except ImportError as e:
    GROQ_AVAILABLE = False
    logger.warning(f"⚠️ Groq SDK not installed: {e}")
except Exception as e:
    GROQ_AVAILABLE = False
    logger.warning(f"⚠️ Groq import error: {e}")

from app.config import config


# ==========================================================
# CONSTANTS
# ==========================================================

GROQ_TIMEOUT = 15  # seconds
CACHE_TTL = 300  # 5 minutes for response cache
MAX_HISTORY_PER_USER = 20
MAX_CACHE_SIZE = 1000
MAX_CONVERSATION_HISTORY = 5000


# ==========================================================
# AI PROVIDER CONFIGURATION
# ==========================================================

class AIProviderConfig:
    """Configuration for AI providers - using app.config"""
    
    # Groq Models
    GROQ_MODELS = {
        "mixtral": "mixtral-8x7b-32768",
        "llama3_70b": "llama-3.1-70b-versatile",
        "llama3_8b": "llama-3.1-8b-instant",
        "gemma2": "gemma2-9b-it"
    }
    
    # Default configuration
    DEFAULT_MODEL = "mixtral"
    DEFAULT_TEMPERATURE = 0.3
    DEFAULT_MAX_TOKENS = 500
    
    # CRITICAL IMPROVEMENT 10: Make system prompt configurable
    SYSTEM_PROMPT = getattr(config, 'AI_SYSTEM_PROMPT', """You are an AI Logistics Assistant for a supply chain management system. 
Your role is to help users understand logistics data, KPIs, performance metrics, and provide actionable insights.
Be concise, professional, and data-driven. Use bullet points for clarity when appropriate.
Always focus on providing value and actionable recommendations.""")


# ==========================================================
# MAIN AI PROVIDER SERVICE
# ==========================================================

class AIProviderService:
    """
    Groq AI Layer - Chat, Insights, Root Cause Analysis, Recommendations
    ENHANCEMENTS v5.0:
    - OpenAI removed (Groq only)
    - TTLCache for conversation history (no memory leak)
    - Request timeout protection
    - Model validation
    - Enhanced health check
    - Request ID tracking
    - Metrics collection
    - Response caching
    - Specialized logistics methods
    """
    
    def __init__(self):
        self.config = AIProviderConfig()
        self.client = None
        self.provider = "groq"  # Fixed to groq only
        self.model = None
        self._initialization_error = None
        
        # CRITICAL IMPROVEMENT 3: TTLCache for conversation history (prevents memory leak)
        self.conversation_history = TTLCache(
            maxsize=MAX_CONVERSATION_HISTORY,
            ttl=86400  # 24 hours
        )
        
        # HIGH PRIORITY IMPROVEMENT 12: Response cache to reduce Groq costs
        self.response_cache = TTLCache(
            maxsize=MAX_CACHE_SIZE,
            ttl=CACHE_TTL
        )
        
        # HIGH PRIORITY IMPROVEMENT 9: Metrics tracking
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "total_tokens_estimated": 0,
            "start_time": time.time(),
            "errors_by_type": {}
        }
        
        # Initialize provider
        self._initialize_provider()
        
        logger.info(f"AI Provider Service v5.0 initialized with {self.provider}")
        if self.model:
            logger.info(f"   Model: {self.model}")
        logger.info(f"   Cache: {MAX_CACHE_SIZE} responses, {CACHE_TTL}s TTL")
        logger.info(f"   Conversation History: {MAX_CONVERSATION_HISTORY} users, 24h TTL")
    
    def _initialize_provider(self):
        """Initialize Groq AI provider with validation."""
        
        # CRITICAL IMPROVEMENT 2: Use config instead of os.getenv
        api_key = config.GROQ_API_KEY
        
        if not GROQ_AVAILABLE:
            self._initialization_error = "Groq SDK not installed"
            logger.error(f"❌ {self._initialization_error}")
            return
        
        if not api_key:
            self._initialization_error = "GROQ_API_KEY not configured"
            logger.error(f"❌ {self._initialization_error}")
            return
        
        try:
            self.client = Groq(api_key=api_key)
            
            # CRITICAL IMPROVEMENT 5: Validate model name
            model_key = getattr(config, 'GROQ_MODEL', 'mixtral')
            
            if model_key not in self.config.GROQ_MODELS:
                logger.warning(f"Unknown model '{model_key}', using default '{self.config.DEFAULT_MODEL}'")
                model_key = self.config.DEFAULT_MODEL
            
            self.model = self.config.GROQ_MODELS[model_key]
            logger.info(f"✅ Groq AI initialized with model: {self.model}")
            
        except Exception as e:
            self._initialization_error = f"Groq init failed: {str(e)}"
            logger.error(f"❌ {self._initialization_error}")
            self.client = None
    
    def _get_cache_key(self, message: str, system_prompt: str = None) -> str:
        """Generate cache key for response caching."""
        prompt = system_prompt or self.config.SYSTEM_PROMPT
        return f"{prompt[:100]}:{message[:200]}"
    
    def _get_conversation_context(self, user_id: str) -> List[Dict]:
        """Get conversation history for a user."""
        if user_id not in self.conversation_history:
            self.conversation_history[user_id] = []
        return self.conversation_history[user_id]
    
    def _add_to_history(self, user_id: str, role: str, content: str):
        """Add message to conversation history."""
        history = self._get_conversation_context(user_id)
        history.append({"role": role, "content": content})
        
        # Trim history if too long
        if len(history) > MAX_HISTORY_PER_USER * 2:
            self.conversation_history[user_id] = history[-MAX_HISTORY_PER_USER * 2:]
    
    def _update_metrics(self, success: bool, error_type: str = None, cached: bool = False, estimated_tokens: int = 0):
        """Update service metrics."""
        self.metrics["total_requests"] += 1
        
        if cached:
            self.metrics["cache_hits"] += 1
        else:
            self.metrics["cache_misses"] += 1
        
        if success:
            self.metrics["successful_requests"] += 1
        else:
            self.metrics["failed_requests"] += 1
            if error_type:
                if error_type not in self.metrics["errors_by_type"]:
                    self.metrics["errors_by_type"][error_type] = 0
                self.metrics["errors_by_type"][error_type] += 1
        
        self.metrics["total_tokens_estimated"] += estimated_tokens
    
    def _call_groq(self, messages: List[Dict], temperature: float = None, max_tokens: int = None, request_id: str = None) -> str:
        """
        Call Groq API with proper error handling.
        
        CRITICAL IMPROVEMENT 4: Added timeout
        CRITICAL IMPROVEMENT 8: Better error categorization
        """
        if not self.client:
            return self._fallback_response("AI service not configured")
        
        temp = temperature or self.config.DEFAULT_TEMPERATURE
        tokens = max_tokens or self.config.DEFAULT_MAX_TOKENS
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
            estimated_tokens = len(result) // 4  # Rough estimate
            
            self._update_metrics(success=True, estimated_tokens=estimated_tokens)
            logger.debug(f"[{req_id}] Groq API call successful, estimated tokens: {estimated_tokens}")
            
            return result
            
        except Exception as e:
            error_type = type(e).__name__
            
            # CRITICAL IMPROVEMENT 8: Better error categorization
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                error_msg = "Groq API timeout"
                logger.error(f"[{req_id}] {error_msg}: {e}")
            elif "rate_limit" in str(e).lower() or "too many requests" in str(e).lower():
                error_msg = "Groq rate limit exceeded"
                logger.warning(f"[{req_id}] {error_msg}: {e}")
            elif "authentication" in str(e).lower() or "api_key" in str(e).lower():
                error_msg = "Groq authentication failed"
                logger.error(f"[{req_id}] {error_msg}: {e}")
            elif "connection" in str(e).lower():
                error_msg = "Groq connection error"
                logger.error(f"[{req_id}] {error_msg}: {e}")
            else:
                error_msg = f"Groq API error: {error_type}"
                logger.error(f"[{req_id}] {error_msg}: {e}")
            
            self._update_metrics(success=False, error_type=error_type)
            return self._fallback_response(error_msg)
    
    def _fallback_response(self, error_msg: str = None) -> str:
        """Fallback response when AI is unavailable."""
        if error_msg:
            logger.debug(f"Using fallback response due to: {error_msg}")
        
        return """I'm here to help with logistics insights!

• DN Status - Send any 10+ digit number to track
• Dealer Performance - Ask about any dealer
• Pending Deliveries - "Pending POD"
• KPI Metrics - "Executive dashboard"
• Control Tower - "Control tower"

What would you like to know?"""
    
    # ==========================================================
    # PUBLIC METHODS - Chat & General
    # ==========================================================
    
    def chat(self, message: str, user_id: str = "guest", request_id: str = None) -> str:
        """
        Main chat method for general AI conversations.
        
        CRITICAL IMPROVEMENT 7: Added request_id support
        HIGH PRIORITY IMPROVEMENT 12: Added response caching
        """
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] AI Chat - User: {user_id}, Message: {message[:100]}")
        
        # Check cache first
        cache_key = self._get_cache_key(message)
        if cache_key in self.response_cache:
            logger.info(f"[{req_id}] Cache hit for query: {message[:50]}")
            self._update_metrics(success=True, cached=True)
            return self.response_cache[cache_key]
        
        # Get conversation history
        history = self._get_conversation_context(user_id)
        
        # Build messages
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPT}
        ]
        
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
        
        # Cache response
        self.response_cache[cache_key] = response
        
        return response
    
    # ==========================================================
    # HIGH PRIORITY IMPROVEMENT 11: Specialized Logistics Methods
    # ==========================================================
    
    def generate_dn_analysis(self, dn_number: str, dn_data: Dict[str, Any], request_id: str = None) -> str:
        """Generate specialized analysis for a Delivery Note."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] Generating DN analysis for: {dn_number}")
        
        prompt = f"""Analyze this Delivery Note and provide insights:

DN Number: {dn_number}
Status: {dn_data.get('status', 'Unknown')}
Aging Days: {dn_data.get('aging_days', 0)}
Amount: {dn_data.get('amount', 0)}
Customer: {dn_data.get('customer_name', 'Unknown')}
City: {dn_data.get('city', 'Unknown')}

Provide:
1. Current status assessment
2. Risk level (Low/Medium/High/Critical)
3. Recommended action
4. Expected resolution timeline

Be concise and actionable."""
        
        messages = [
            {"role": "system", "content": "You are a logistics analyst specializing in delivery note analysis."},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_groq(messages, temperature=0.3, max_tokens=400, request_id=req_id)
    
    def generate_pod_analysis(self, pod_data: Dict[str, Any], request_id: str = None) -> str:
        """Generate specialized analysis for POD performance."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] Generating POD analysis")
        
        prompt = f"""Analyze this POD performance data:

Pending PODs: {pod_data.get('pending_count', 0)}
Average Aging: {pod_data.get('avg_aging', 0)} days
Top Pending Dealer: {pod_data.get('top_pending_dealer', 'Unknown')}

Provide:
1. Performance assessment
2. Root causes for delays
3. Recommendations for improvement
4. Priority actions"""
        
        messages = [
            {"role": "system", "content": "You are a logistics analyst specializing in POD performance."},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_groq(messages, temperature=0.4, max_tokens=500, request_id=req_id)
    
    def generate_kpi_analysis(self, kpi_data: Dict[str, Any], request_id: str = None) -> str:
        """Generate specialized analysis for KPI performance."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] Generating KPI analysis")
        
        prompt = f"""Analyze this KPI data:

Overall Score: {kpi_data.get('overall_score', 0)}%
POD Score: {kpi_data.get('pod_score', 0)}%
PGI Score: {kpi_data.get('pgi_score', 0)}%
Delivery Score: {kpi_data.get('delivery_score', 0)}%

Provide:
1. Executive summary
2. Areas of strength
3. Areas needing improvement
4. Strategic recommendations"""
        
        messages = [
            {"role": "system", "content": "You are an executive logistics analyst specializing in KPI analysis."},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_groq(messages, temperature=0.3, max_tokens=600, request_id=req_id)
    
    def generate_control_tower_summary(self, alerts: Dict[str, Any], request_id: str = None) -> str:
        """Generate control tower summary for critical alerts."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] Generating control tower summary")
        
        prompt = f"""Analyze these control tower alerts:

Critical Alerts: {len(alerts.get('critical_alerts', []))}
Active Warnings: {len(alerts.get('warnings', []))}
Total Value at Risk: {alerts.get('total_value_at_risk', 0)}

Provide:
1. Situation assessment
2. Highest priority issues
3. Recommended escalation path
4. Expected business impact"""
        
        messages = [
            {"role": "system", "content": "You are a control tower analyst specializing in logistics alerts."},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_groq(messages, temperature=0.2, max_tokens=500, request_id=req_id)
    
    def generate_executive_insights(self, dashboard_data: Dict[str, Any], request_id: str = None) -> str:
        """Generate executive-level insights from dashboard data."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] Generating executive insights")
        
        prompt = f"""Based on this executive dashboard data, provide strategic insights:

Total DNs: {dashboard_data.get('total_records', 0)}
Pending Deliveries: {dashboard_data.get('pending_deliveries', 0)}
Pending Amount: {dashboard_data.get('pending_amount', 0)}
Top Dealers: {dashboard_data.get('top_dealers', [])[:3]}

Provide:
1. Executive summary for leadership
2. Key risks to highlight
3. Strategic recommendations
4. Success metrics to track"""
        
        messages = [
            {"role": "system", "content": "You are an executive logistics strategist. Provide high-level insights for leadership."},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_groq(messages, temperature=0.3, max_tokens=700, request_id=req_id)
    
    def generate_root_cause_analysis(self, metric: str, data: Dict, request_id: str = None) -> str:
        """Generate root cause analysis for performance issues."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] Generating root cause analysis for {metric}")
        
        if not self.client:
            return f"📊 Root cause analysis for {metric} will be available when AI service is configured."
        
        prompt = f"Analyze root causes for {metric} issue. Data: {json.dumps(data)[:500]}. Provide 2-3 primary causes and recommendations."
        return self._simple_chat(prompt, request_id=req_id)
    
    def generate_recommendations(self, issues: List[str], data: Dict, request_id: str = None) -> str:
        """Generate actionable recommendations."""
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] Generating recommendations for {len(issues)} issues")
        
        if not self.client:
            return "💡 Recommendations will be available when AI service is configured."
        
        prompt = f"Based on issues: {issues}, provide actionable recommendations. Prioritize by impact."
        return self._simple_chat(prompt, request_id=req_id)
    
    def _simple_chat(self, message: str, request_id: str = None) -> str:
        """Simple chat without history."""
        if not self.client:
            return self._fallback_response()
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPT},
            {"role": "user", "content": message}
        ]
        
        return self._call_groq(messages, max_tokens=400, request_id=request_id)
    
    # ==========================================================
    # CRITICAL IMPROVEMENT 6: Enhanced Health Check
    # ==========================================================
    
    def health_check(self, verify_api: bool = False) -> Dict[str, Any]:
        """
        Enhanced health check with optional API verification.
        
        Args:
            verify_api: If True, makes a test call to Groq API
        """
        result = {
            "service": "ai_provider",
            "version": "5.0",
            "provider": self.provider,
            "model": self.model,
            "configured": self.client is not None,
            "initialization_error": self._initialization_error,
            "cache_size": len(self.response_cache),
            "cache_maxsize": MAX_CACHE_SIZE,
            "conversation_history_size": len(self.conversation_history)
        }
        
        # HIGH PRIORITY IMPROVEMENT 9: Add metrics to health check
        result["metrics"] = self.get_metrics()
        
        # CRITICAL IMPROVEMENT 6: Optional API verification
        if verify_api and self.client:
            try:
                test_response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5,
                    timeout=5
                )
                result["api_status"] = "healthy"
                result["api_response"] = test_response.choices[0].message.content[:50]
            except Exception as e:
                result["api_status"] = "unhealthy"
                result["api_error"] = str(e)
        
        return result
    
    # ==========================================================
    # HIGH PRIORITY IMPROVEMENT 9: Metrics Collection
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics for monitoring."""
        total = self.metrics["total_requests"]
        success_rate = (self.metrics["successful_requests"] / max(1, total)) * 100
        
        return {
            "total_requests": self.metrics["total_requests"],
            "successful_requests": self.metrics["successful_requests"],
            "failed_requests": self.metrics["failed_requests"],
            "success_rate": round(success_rate, 2),
            "cache_hits": self.metrics["cache_hits"],
            "cache_misses": self.metrics["cache_misses"],
            "cache_hit_rate": round((self.metrics["cache_hits"] / max(1, self.metrics["cache_hits"] + self.metrics["cache_misses"])) * 100, 2),
            "estimated_tokens": self.metrics["total_tokens_estimated"],
            "uptime_seconds": round(time.time() - self.metrics["start_time"], 2),
            "errors_by_type": self.metrics["errors_by_type"],
            "provider": self.provider,
            "model": self.model
        }
    
    def clear_cache(self) -> Dict[str, Any]:
        """Clear response cache for debugging."""
        old_size = len(self.response_cache)
        self.response_cache.clear()
        logger.info(f"Cleared response cache: {old_size} entries removed")
        
        return {
            "cleared_entries": old_size,
            "cache_size": len(self.response_cache)
        }
    
    def clear_history(self, user_id: str) -> Dict[str, Any]:
        """Clear conversation history for a specific user."""
        if user_id in self.conversation_history:
            del self.conversation_history[user_id]
            logger.info(f"Cleared conversation history for user: {user_id}")
            return {"cleared": True, "user_id": user_id}
        
        return {"cleared": False, "user_id": user_id, "error": "User not found"}


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
    """Compatibility function for chat with request_id support."""
    provider = get_ai_provider()
    return provider.chat(message, user_id, request_id=request_id)


def generate_root_cause(metric: str, data: Dict, request_id: str = None) -> str:
    """Compatibility function for root cause analysis."""
    provider = get_ai_provider()
    return provider.generate_root_cause_analysis(metric, data, request_id=request_id)


def generate_recommendations(issues: List[str], data: Dict, request_id: str = None) -> str:
    """Compatibility function for recommendations."""
    provider = get_ai_provider()
    return provider.generate_recommendations(issues, data, request_id=request_id)


def get_ai_metrics() -> Dict[str, Any]:
    """Get AI service metrics."""
    provider = get_ai_provider()
    return provider.get_metrics()


def clear_ai_cache() -> Dict[str, Any]:
    """Clear AI response cache."""
    provider = get_ai_provider()
    return provider.clear_cache()


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

try:
    _test_provider = get_ai_provider()
    logger.info("=" * 60)
    logger.info("🤖 AI Provider Service v5.0 - Groq Only")
    logger.info(f"   Provider: {_test_provider.provider or 'None'}")
    logger.info(f"   Model: {_test_provider.model or 'N/A'}")
    logger.info(f"   Configured: {_test_provider.client is not None}")
    logger.info(f"   Cache: {MAX_CACHE_SIZE} responses, {CACHE_TTL}s TTL")
    logger.info("   Features: Specialized Analysis | Response Caching | Metrics | Request Tracking")
    logger.info("=" * 60)
except Exception as e:
    logger.warning(f"AI Provider Service loaded with issues: {e}")
    logger.info("=" * 60)
    logger.info("🤖 AI Provider Service v5.0 - Fallback Mode Only")
    logger.info("   Set GROQ_API_KEY in environment to enable AI")
    logger.info("=" * 60)
