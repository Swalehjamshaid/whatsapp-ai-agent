# ==========================================================
# FILE: app/services/ai_provider_service.py (CLEAN v5.0)
# ==========================================================

import os
import json
import time
from typing import Dict, Any, Optional, List
from loguru import logger

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq SDK not installed")

from app.config import config


class AIProviderService:
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
        logger.info(f"AI Provider Service initialized")
    
    def _initialize_provider(self):
        if not GROQ_AVAILABLE:
            return
        
        api_key = getattr(config, 'GROQ_API_KEY', None)
        if not api_key:
            return
        
        try:
            self.client = Groq(api_key=api_key)
            self.provider = "groq"
            self.model = getattr(config, 'GROQ_MODEL', 'mixtral-8x7b-32768')
            logger.info(f"✅ Groq AI initialized")
        except Exception as e:
            logger.error(f"Groq init failed: {e}")
    
    def chat(self, message: str, user_id: str = "guest", request_id: str = None) -> str:
        self.metrics["total_requests"] += 1
        req_id = request_id or "unknown"
        logger.info(f"[{req_id}] AI Chat: {message[:100]}")
        
        if not self.client:
            self.metrics["failed_requests"] += 1
            return self._fallback_response()
        
        if user_id not in self.conversation_history:
            self.conversation_history[user_id] = []
        history = self.conversation_history[user_id]
        
        system_prompt = getattr(config, 'GROQ_SYSTEM_PROMPT', "You are an AI Logistics Assistant.")
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            messages.append(msg)
        messages.append({"role": "user", "content": message})
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=500,
                timeout=15
            )
            result = response.choices[0].message.content
            
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": result})
            if len(history) > 20:
                self.conversation_history[user_id] = history[-20:]
            
            self.metrics["successful_requests"] += 1
            return result
        except Exception as e:
            logger.error(f"AI chat failed: {e}")
            self.metrics["failed_requests"] += 1
            return self._fallback_response()
    
    def generate_root_cause_analysis(self, metric: str, data: Dict, request_id: str = None) -> str:
        if not self.client:
            return f"📊 Root cause analysis for {metric} will be available when AI is configured."
        return self._simple_chat(f"Analyze root causes for {metric}. Provide 2-3 causes and recommendations.", request_id)
    
    def generate_recommendations(self, issues: List[str], data: Dict, request_id: str = None) -> str:
        if not self.client:
            return "💡 Recommendations will be available when AI is configured."
        return self._simple_chat(f"Based on issues: {issues}, provide actionable recommendations.", request_id)
    
    def _simple_chat(self, message: str, request_id: str = None) -> str:
        if not self.client:
            return self._fallback_response()
        try:
            system_prompt = getattr(config, 'GROQ_SYSTEM_PROMPT', "You are an AI Logistics Assistant.")
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ]
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=400,
                timeout=15
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Simple chat failed: {e}")
            return self._fallback_response()
    
    def _fallback_response(self) -> str:
        return """I'm here to help with logistics insights!

• DN Status - Send any 10+ digit number to track
• Dealer Performance - Ask about any dealer
• Pending Deliveries - "Pending POD"
• KPI Metrics - "Executive dashboard"

What would you like to know?"""
    
    def health_check(self) -> Dict:
        return {
            "service": "ai_provider",
            "provider": self.provider,
            "configured": self.client is not None
        }
    
    def get_metrics(self) -> Dict:
        total = self.metrics["total_requests"]
        success_rate = (self.metrics["successful_requests"] / max(1, total)) * 100
        return {
            "total_requests": self.metrics["total_requests"],
            "successful_requests": self.metrics["successful_requests"],
            "failed_requests": self.metrics["failed_requests"],
            "success_rate": round(success_rate, 2),
            "provider": self.provider,
            "model": self.model
        }


_ai_provider = None

def get_ai_provider() -> AIProviderService:
    global _ai_provider
    if _ai_provider is None:
        _ai_provider = AIProviderService()
    return _ai_provider


def chat(message: str, user_id: str = "guest", request_id: str = None) -> str:
    return get_ai_provider().chat(message, user_id, request_id)


def generate_root_cause(metric: str, data: Dict, request_id: str = None) -> str:
    return get_ai_provider().generate_root_cause_analysis(metric, data, request_id)


def generate_recommendations(issues: List[str], data: Dict, request_id: str = None) -> str:
    return get_ai_provider().generate_recommendations(issues, data, request_id)


def get_ai_metrics() -> Dict:
    return get_ai_provider().get_metrics()


logger.info("=" * 60)
logger.info("🤖 AI Provider Service v5.0 (Clean Version)")
logger.info(f"   Provider: {get_ai_provider().provider or 'None'}")
logger.info("=" * 60)
