# ==========================================================
# FILE: app/services/ai_provider_service.py (INTEGRATED v4.1)
# ==========================================================
# PURPOSE: Groq AI Layer - Chat and Insights
# ==========================================================

import os
import json
from typing import Dict, Any, Optional, List
from loguru import logger

GROQ_AVAILABLE = False
OPENAI_AVAILABLE = False

try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ Groq SDK loaded")
except ImportError:
    logger.warning("⚠️ Groq SDK not installed")

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
    logger.info("✅ OpenAI SDK loaded")
except ImportError:
    logger.warning("⚠️ OpenAI SDK not installed")


class AIProviderConfig:
    GROQ_MODELS = {"mixtral": "mixtral-8x7b-32768", "llama3_70b": "llama-3.1-70b-versatile"}
    DEFAULT_MODEL = "mixtral"
    DEFAULT_TEMPERATURE = 0.3
    DEFAULT_MAX_TOKENS = 500
    SYSTEM_PROMPT = """You are an AI Logistics Assistant. Provide concise, helpful responses about logistics operations, DNs, dealer performance, and KPIs. Be professional and data-driven."""


class AIProviderService:
    def __init__(self):
        self.config = AIProviderConfig()
        self.client = None
        self.provider = None
        self.model = None
        self._initialize_provider()
        self.conversation_history = {}
        logger.info(f"AI Provider Service initialized with {self.provider or 'no provider'}")
    
    def _initialize_provider(self):
        # Try Groq
        if GROQ_AVAILABLE and os.getenv("GROQ_API_KEY"):
            try:
                self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
                self.provider = "groq"
                self.model = self.config.GROQ_MODELS.get(os.getenv("GROQ_MODEL", "mixtral"))
                logger.info(f"✅ Groq AI initialized")
                return
            except Exception as e:
                logger.error(f"Groq init failed: {e}")
        
        # Try OpenAI
        if OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
            try:
                self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                self.provider = "openai"
                self.model = "gpt-3.5-turbo"
                logger.info(f"✅ OpenAI initialized")
                return
            except Exception as e:
                logger.error(f"OpenAI init failed: {e}")
        
        self.client = None
        self.provider = None
        logger.warning("❌ No AI provider available - using fallback responses")
    
    def chat(self, message: str, user_id: str = "guest") -> str:
        """Main chat method for general AI conversations."""
        logger.info(f"AI Chat: {message[:100]}")
        
        if not self.client:
            return self._fallback_response()
        
        history = self.conversation_history.get(user_id, [])
        messages = [{"role": "system", "content": self.config.SYSTEM_PROMPT}]
        messages.extend(history[-10:])
        messages.append({"role": "user", "content": message})
        
        try:
            if self.provider == "groq":
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.config.DEFAULT_TEMPERATURE,
                    max_tokens=self.config.DEFAULT_MAX_TOKENS
                )
                result = response.choices[0].message.content
            elif self.provider == "openai":
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.config.DEFAULT_TEMPERATURE,
                    max_tokens=self.config.DEFAULT_MAX_TOKENS
                )
                result = response.choices[0].message.content
            else:
                result = self._fallback_response()
            
            # Save to history
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": result})
            if len(history) > 20:
                history = history[-20:]
            self.conversation_history[user_id] = history
            
            return result
        except Exception as e:
            logger.error(f"AI chat failed: {e}")
            return self._fallback_response()
    
    def generate_root_cause_analysis(self, metric: str, data: Dict) -> str:
        """Generate root cause analysis."""
        if not self.client:
            return f"📊 Root cause analysis for {metric} will be available when AI service is configured."
        
        prompt = f"Analyze root causes for {metric} issue. Data: {json.dumps(data)[:500]}. Provide 2-3 primary causes and recommendations."
        return self._simple_chat(prompt)
    
    def generate_recommendations(self, issues: List[str], data: Dict) -> str:
        """Generate recommendations."""
        if not self.client:
            return "💡 Recommendations will be available when AI service is configured."
        
        prompt = f"Based on issues: {issues}, provide actionable recommendations. Prioritize by impact."
        return self._simple_chat(prompt)
    
    def _simple_chat(self, message: str) -> str:
        """Simple chat without history."""
        if not self.client:
            return self._fallback_response()
        
        messages = [{"role": "system", "content": self.config.SYSTEM_PROMPT}, {"role": "user", "content": message}]
        try:
            if self.provider == "groq":
                response = self.client.chat.completions.create(model=self.model, messages=messages, max_tokens=400)
                return response.choices[0].message.content
            return self._fallback_response()
        except Exception as e:
            logger.error(f"Simple chat failed: {e}")
            return self._fallback_response()
    
    def _fallback_response(self) -> str:
        return """I'm here to help with logistics insights!

• DN Status - Send any 10+ digit number to track
• Dealer Performance - Ask about any dealer
• Pending Deliveries - "Pending POD"
• KPI Metrics - "Executive dashboard"
• Control Tower - "Control tower"

What would you like to know?"""
    
    def health_check(self) -> Dict:
        return {
            "service": "ai_provider",
            "provider": self.provider,
            "configured": self.client is not None
        }


# ==========================================================
# SINGLETON
# ==========================================================

_ai_provider = None

def get_ai_provider() -> AIProviderService:
    global _ai_provider
    if _ai_provider is None:
        _ai_provider = AIProviderService()
    return _ai_provider


logger.info("=" * 60)
logger.info("🤖 AI Provider Service v4.1 Loaded")
logger.info(f"   Provider: {get_ai_provider().provider or 'None (Fallback)'}")
logger.info("=" * 60)
