# ==========================================================
# FILE: app/services/ai_provider_service.py (ENTERPRISE GRADE)
# ==========================================================

import json
import time
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from functools import lru_cache
from enum import Enum

import requests
from loguru import logger
from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import config
from app.models import AIResponseLog


# ==========================================================
# AI PROVIDER STATUS
# ==========================================================

class ProviderStatus(Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


# ==========================================================
# AI PROMPT TEMPLATES
# ==========================================================

PROMPTS = {
    "executive_summary": """
You are a Logistics AI Advisor for a major distribution company.

CONTEXT DATA:
{context}

QUESTION: {question}

RESPONSE REQUIREMENTS:
1. Start with an executive summary (2-3 sentences)
2. List top 3 risks with financial impact
3. Provide 3 specific actionable recommendations
4. Use emojis for visual hierarchy
5. Keep total response under 500 words
6. Be direct and business-focused

FORMAT:
🎯 EXECUTIVE SUMMARY
[summary text]

🚨 TOP RISKS
1. [risk] - [impact]
2. [risk] - [impact]
3. [risk] - [impact]

💡 RECOMMENDATIONS
1. [action]
2. [action]
3. [action]
""",

    "dealer_analysis": """
You are a Logistics AI Advisor analyzing a dealer.

DEALER DATA:
{dealer_data}

QUESTION: {question}

ANALYZE:
1. Dealer health score (0-100)
2. Pending DNs and financial exposure
3. Root causes of any issues
4. Risk level (Low/Medium/High/Critical)
5. Specific action plan

FORMAT:
📊 DEALER SCORE: [score]/100

📦 PENDING: [number] DNs
💰 EXPOSURE: Rs [amount]

⚠️ RISK LEVEL: [level]

🔍 ROOT CAUSES:
• [cause]
• [cause]

✅ ACTION PLAN:
1. [action]
2. [action]
""",

    "warehouse_analysis": """
You are a Logistics AI Advisor analyzing a warehouse.

WAREHOUSE DATA:
{warehouse_data}

QUESTION: {question}

ANALYZE:
1. Warehouse efficiency score (0-100)
2. Pending DNs and bottlenecks
3. Processing capacity issues
4. Recommendations for improvement

FORMAT:
🏭 WAREHOUSE HEALTH: [score]/100

📦 PENDING: [number] DNs
⚡ EFFICIENCY: [percentage]%

🚨 BOTTLENECKS:
• [bottleneck]
• [bottleneck]

✅ OPTIMIZATION:
1. [action]
2. [action]
""",

    "city_analysis": """
You are a Logistics AI Advisor analyzing a city.

CITY DATA:
{city_data}

QUESTION: {question}

ANALYZE:
1. City performance score (0-100)
2. Pending DNs by area
3. Key constraints (transport, warehouse, dealer)
4. Recovery strategy

FORMAT:
🌆 CITY HEALTH: [score]/100

📦 PENDING DNs: [number]
💰 VALUE AT RISK: Rs [amount]

🔑 CONSTRAINTS:
• [constraint]
• [constraint]

🎯 RECOVERY PLAN:
1. [action]
2. [action]
""",

    "root_cause": """
You are a Logistics AI Advisor performing root cause analysis.

METRICS:
{pending_metrics}
{pod_metrics}
{risk_metrics}

QUESTION: {question}

ANALYZE root causes as percentages:
- Dealer delays
- Warehouse processing delays
- Documentation/paperwork issues
- Transport/logistics issues
- Other factors

OUTPUT EXACTLY:
🔍 ROOT CAUSE ANALYSIS

Dealer Delays: [X]%
Warehouse Delays: [X]%
Documentation: [X]%
Transport: [X]%
Other: [X]%

💡 RECOMMENDATION: [one sentence solution]
""",

    "recommendations": """
You are a Logistics AI Advisor providing recommendations.

CURRENT STATE:
{current_state}

QUESTION: {question}

PROVIDE:
1. Immediate actions (next 24 hours)
2. Short-term improvements (this week)
3. Strategic changes (this month)

FORMAT:
⚡ IMMEDIATE (24h):
1. [action]

📅 THIS WEEK:
1. [action]

🏆 STRATEGIC:
1. [action]
""",

    "general": """
You are a Logistics AI Assistant for a WhatsApp business platform.

CONTEXT:
{context}

QUESTION: {question}

INSTRUCTIONS:
1. Be helpful and concise
2. If unsure, suggest contacting support
3. Never hallucinate data
4. Keep responses under 400 words
5. Use emojis for clarity

RESPONSE:
"""
}


# ==========================================================
# AI CACHE MANAGER
# ==========================================================

class AICacheManager:
    """Cache AI responses for cost reduction and speed"""
    
    def __init__(self, ttl_seconds: int = 300):
        self.cache: Dict[str, Tuple[str, float]] = {}
        self.ttl = ttl_seconds
    
    def get(self, key: str) -> Optional[str]:
        if key in self.cache:
            response, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return response
            del self.cache[key]
        return None
    
    def set(self, key: str, response: str):
        self.cache[key] = (response, time.time())
    
    def clear(self):
        self.cache.clear()
    
    def get_cache_key(self, prompt: str, model: str, context_hash: str = "") -> str:
        return f"{model}:{context_hash}:{hash(prompt[:200])}"


# ==========================================================
# AI SAFETY LAYER
# ==========================================================

class AISafetyLayer:
    """Prevent dangerous operations and validate responses"""
    
    DANGEROUS_PATTERNS = [
        r"DROP\s+TABLE",
        r"DELETE\s+FROM",
        r"UPDATE\s+\w+\s+SET",
        r"INSERT\s+INTO",
        r"ALTER\s+TABLE",
        r"TRUNCATE",
        r"EXEC\s*\(",
        r"xp_cmdshell",
        r"UNION\s+SELECT",
        r"--",
        r";\s*DROP",
        r"'\s*OR\s+'1'='1",
    ]
    
    @classmethod
    def validate_prompt(cls, prompt: str) -> Tuple[bool, str]:
        """Check prompt for dangerous content"""
        prompt_upper = prompt.upper()
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, prompt_upper):
                logger.warning(f"Dangerous pattern detected: {pattern}")
                return False, f"Dangerous pattern blocked: {pattern}"
        return True, ""
    
    @classmethod
    def sanitize_response(cls, response: str) -> str:
        """Remove any dangerous content from AI response"""
        # Remove any SQL injection attempts
        for pattern in cls.DANGEROUS_PATTERNS:
            response = re.sub(pattern, "[REDACTED]", response, flags=re.IGNORECASE)
        return response[:4000]  # Limit response length


# ==========================================================
# AI PROVIDER SERVICE (ENTERPRISE GRADE)
# ==========================================================

class AIProviderService:
    """
    Enterprise AI Provider Service
    
    Features:
    - Multi-provider support (DeepSeek + OpenAI fallback)
    - Automatic retry with exponential backoff
    - Health monitoring
    - Response caching
    - Usage logging
    - Safety layer
    - Prompt templates
    """
    
    def __init__(self, db: Session = None):
        self.db = db
        self.cache = AICacheManager(ttl_seconds=300)  # 5 minute cache
        self.retry_count = 3
        self.retry_delay = 1  # seconds, exponential backoff
        
        # Initialize DeepSeek client
        self.deepseek_api_key = getattr(config, 'DEEPSEEK_API_KEY', None)
        self.deepseek_client = None
        self.deepseek_status = ProviderStatus.UNKNOWN
        
        if self.deepseek_api_key:
            try:
                self.deepseek_client = OpenAI(
                    api_key=self.deepseek_api_key,
                    base_url="https://api.deepseek.com",
                    timeout=30.0,
                    max_retries=2
                )
                logger.info("✅ DeepSeek client initialized")
                self.deepseek_status = ProviderStatus.ONLINE
            except Exception as e:
                logger.error(f"❌ DeepSeek initialization failed: {e}")
                self.deepseek_status = ProviderStatus.OFFLINE
        
        # Initialize OpenAI fallback
        self.openai_api_key = getattr(config, 'OPENAI_API_KEY', None)
        self.openai_client = None
        self.openai_status = ProviderStatus.UNKNOWN
        
        if self.openai_api_key:
            try:
                self.openai_client = OpenAI(
                    api_key=self.openai_api_key,
                    timeout=30.0,
                    max_retries=2
                )
                logger.info("✅ OpenAI client initialized")
                self.openai_status = ProviderStatus.ONLINE
            except Exception as e:
                logger.error(f"❌ OpenAI initialization failed: {e}")
                self.openai_status = ProviderStatus.OFFLINE
        
        # Overall availability
        self.is_available = (
            self.deepseek_status == ProviderStatus.ONLINE or
            self.openai_status == ProviderStatus.ONLINE
        )
        
        # Log startup status
        self._log_startup_status()
    
    def _log_startup_status(self):
        """Log comprehensive startup status"""
        logger.info("=" * 60)
        logger.info("🤖 AI PROVIDER SERVICE INITIALIZED")
        logger.info(f"DeepSeek: {self.deepseek_status.value}")
        logger.info(f"OpenAI: {self.openai_status.value}")
        logger.info(f"Overall Available: {self.is_available}")
        logger.info(f"Cache TTL: {self.cache.ttl}s")
        logger.info(f"Retry Count: {self.retry_count}")
        logger.info("=" * 60)
    
    def check_health(self) -> Dict[str, Any]:
        """Health check for all providers"""
        health = {
            "deepseek": self.deepseek_status.value,
            "openai": self.openai_status.value,
            "overall": self.is_available,
            "cache_enabled": True,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Try to actually call health endpoint
        if self.deepseek_status == ProviderStatus.ONLINE:
            try:
                test_response = self._call_deepseek("Hello", max_tokens=5)
                if test_response.get("success"):
                    health["deepseek"] = ProviderStatus.ONLINE.value
                else:
                    health["deepseek"] = ProviderStatus.DEGRADED.value
            except:
                health["deepseek"] = ProviderStatus.DEGRADED.value
        
        return health
    
    # ==========================================================
    # CORE AI METHODS
    # ==========================================================
    
    def answer_question(
        self,
        question: str,
        context: Dict[str, Any] = None,
        structured: bool = False,
        user_phone: str = None,
        template: str = "general",
        max_tokens: int = 1000,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """
        Answer a question using AI with fallback support
        
        Args:
            question: User's question
            context: Additional context data
            structured: Whether to return structured response
            user_phone: User identifier for logging
            template: Prompt template to use
            max_tokens: Maximum response tokens
            temperature: Response creativity (0-1)
        
        Returns:
            Dict with success, content, provider_used, etc.
        """
        start_time = time.time()
        
        # Validate prompt safety
        is_safe, error_msg = AISafetyLayer.validate_prompt(question)
        if not is_safe:
            logger.warning(f"Unsafe prompt blocked: {error_msg}")
            return {
                "success": False,
                "content": "⚠️ Your request contains unsafe content and cannot be processed.",
                "error": error_msg,
                "provider_used": "safety_layer",
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # Build prompt using template
        prompt = self._build_prompt(question, context, template)
        
        # Check cache
        cache_key = self.cache.get_cache_key(prompt, "deepseek", str(hash(str(context))))
        cached_response = self.cache.get(cache_key)
        if cached_response:
            logger.info(f"Cache hit for: {question[:50]}...")
            return {
                "success": True,
                "content": cached_response,
                "provider_used": "cache",
                "processing_time_ms": 0,
                "cached": True
            }
        
        # Try providers in order
        response = None
        provider_used = None
        
        # Try DeepSeek first
        if self.deepseek_status == ProviderStatus.ONLINE:
            response = self._call_deepseek(prompt, max_tokens, temperature)
            if response.get("success"):
                provider_used = "deepseek"
        
        # Fallback to OpenAI
        if not response or not response.get("success"):
            if self.openai_status == ProviderStatus.ONLINE:
                response = self._call_openai(prompt, max_tokens, temperature)
                if response.get("success"):
                    provider_used = "openai"
        
        # Final fallback
        if not response or not response.get("success"):
            response = self._call_fallback(question)
            provider_used = "fallback"
        
        # Sanitize response
        if response.get("content"):
            response["content"] = AISafetyLayer.sanitize_response(response["content"])
        
        # Cache successful response
        if response.get("success") and provider_used in ["deepseek", "openai"]:
            self.cache.set(cache_key, response["content"])
        
        # Log usage
        processing_time = int((time.time() - start_time) * 1000)
        response["processing_time_ms"] = processing_time
        response["provider_used"] = provider_used
        
        self._log_usage(
            user_phone=user_phone,
            question=question,
            response=response.get("content", ""),
            provider=provider_used,
            success=response.get("success", False),
            processing_time_ms=processing_time
        )
        
        logger.info(f"AI Response: provider={provider_used}, success={response.get('success')}, time={processing_time}ms")
        
        return response
    
    def _build_prompt(self, question: str, context: Dict = None, template: str = "general") -> str:
        """Build prompt using template and context"""
        template_content = PROMPTS.get(template, PROMPTS["general"])
        
        # Format context
        context_str = json.dumps(context, indent=2, default=str) if context else "No additional context"
        
        # For executive summary, inject metrics
        if template == "executive_summary" and context:
            metrics = context.get("metrics", {})
            context_str = f"""
Pending DNs: {metrics.get('pending_dns', 0)}
Pending Value: Rs {metrics.get('pending_value', 0):,.2f}
POD Pending: {metrics.get('pod_pending', 0)}
Top Risk Dealer: {context.get('top_risk_dealer', 'Unknown')}
Top Warehouse: {context.get('top_warehouse', 'Unknown')}
"""
        
        return template_content.format(
            context=context_str[:2000],
            question=question
        )
    
    def _call_deepseek(self, prompt: str, max_tokens: int = 1000, temperature: float = 0.7) -> Dict[str, Any]:
        """Call DeepSeek API with retry logic"""
        if not self.deepseek_client:
            return {"success": False, "error": "DeepSeek client not initialized"}
        
        for attempt in range(self.retry_count):
            try:
                logger.debug(f"DeepSeek attempt {attempt + 1}/{self.retry_count}")
                
                response = self.deepseek_client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {"role": "system", "content": "You are a logistics AI advisor. Be concise, professional, and data-driven."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False
                )
                
                content = response.choices[0].message.content
                
                logger.info(f"DeepSeek success: {len(content)} chars")
                return {
                    "success": True,
                    "content": content,
                    "model": "deepseek-chat",
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens if hasattr(response, 'usage') else 0,
                        "completion_tokens": response.usage.completion_tokens if hasattr(response, 'usage') else 0
                    }
                }
                
            except Exception as e:
                logger.warning(f"DeepSeek attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_count - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    return {"success": False, "error": str(e)}
        
        return {"success": False, "error": "Max retries exceeded"}
    
    def _call_openai(self, prompt: str, max_tokens: int = 1000, temperature: float = 0.7) -> Dict[str, Any]:
        """Call OpenAI API with retry logic"""
        if not self.openai_client:
            return {"success": False, "error": "OpenAI client not initialized"}
        
        for attempt in range(self.retry_count):
            try:
                logger.debug(f"OpenAI attempt {attempt + 1}/{self.retry_count}")
                
                response = self.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are a logistics AI advisor. Be concise, professional, and data-driven."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False
                )
                
                content = response.choices[0].message.content
                
                logger.info(f"OpenAI success: {len(content)} chars")
                return {
                    "success": True,
                    "content": content,
                    "model": "gpt-3.5-turbo",
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens if hasattr(response, 'usage') else 0,
                        "completion_tokens": response.usage.completion_tokens if hasattr(response, 'usage') else 0
                    }
                }
                
            except Exception as e:
                logger.warning(f"OpenAI attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_count - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    return {"success": False, "error": str(e)}
        
        return {"success": False, "error": "Max retries exceeded"}
    
    def _call_fallback(self, question: str) -> Dict[str, Any]:
        """Rule-based fallback when AI is unavailable"""
        logger.info(f"Using fallback for: {question[:50]}")
        
        question_lower = question.lower()
        
        # Smart fallback responses
        if any(word in question_lower for word in ["pending", "backlog"]):
            content = "📊 I can see you're asking about pending items. Please check the pending dashboard or contact your warehouse manager for real-time updates."
        elif any(word in question_lower for word in ["risk", "critical", "urgent"]):
            content = "🚨 For risk assessment, please review the executive dashboard. Contact operations if you need immediate assistance."
        elif any(word in question_lower for word in ["how", "why", "what"]):
            content = "💡 For detailed analysis, our AI system is temporarily unavailable. Please try again in a few minutes or contact support."
        else:
            content = "🤖 Our AI assistant is currently experiencing high demand. Please try your request again in a moment. For urgent matters, contact operations directly."
        
        return {
            "success": True,
            "content": content,
            "model": "fallback",
            "fallback": True
        }
    
    # ==========================================================
    # SPECIALIZED ANALYSIS METHODS
    # ==========================================================
    
    def analyze_dealer(
        self,
        dealer_data: Dict[str, Any],
        structured: bool = False,
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Analyze dealer performance"""
        question = "Analyze this dealer's performance and provide actionable insights."
        
        context = {
            "dealer_data": dealer_data,
            "type": "dealer_analysis"
        }
        
        return self.answer_question(
            question=question,
            context=context,
            structured=structured,
            user_phone=user_phone,
            template="dealer_analysis",
            max_tokens=800,
            temperature=0.5
        )
    
    def analyze_warehouse(
        self,
        warehouse_data: Dict[str, Any],
        structured: bool = False,
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Analyze warehouse performance"""
        question = "Analyze this warehouse's efficiency and identify bottlenecks."
        
        return self.answer_question(
            question=question,
            context={"warehouse_data": warehouse_data},
            structured=structured,
            user_phone=user_phone,
            template="warehouse_analysis",
            max_tokens=800,
            temperature=0.5
        )
    
    def analyze_city(
        self,
        city_data: Dict[str, Any],
        structured: bool = False,
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Analyze city performance"""
        question = "Analyze this city's logistics performance and constraints."
        
        return self.answer_question(
            question=question,
            context={"city_data": city_data},
            structured=structured,
            user_phone=user_phone,
            template="city_analysis",
            max_tokens=800,
            temperature=0.5
        )
    
    def generate_executive_summary(
        self,
        metrics: Dict[str, Any],
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Generate executive-level summary"""
        question = "Generate an executive summary of logistics performance."
        
        context = {
            "metrics": metrics,
            "top_risk_dealer": metrics.get("top_risk_dealer", "Unknown"),
            "top_warehouse": metrics.get("top_warehouse", "Unknown"),
            "type": "executive_summary"
        }
        
        return self.answer_question(
            question=question,
            context=context,
            structured=False,
            user_phone=user_phone,
            template="executive_summary",
            max_tokens=600,
            temperature=0.4
        )
    
    def generate_root_cause(
        self,
        pending_metrics: Dict[str, Any],
        pod_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Generate root cause analysis"""
        question = "What are the root causes of delivery delays?"
        
        context = {
            "pending_metrics": pending_metrics,
            "pod_metrics": pod_metrics,
            "risk_metrics": risk_metrics
        }
        
        return self.answer_question(
            question=question,
            context=context,
            structured=False,
            user_phone=user_phone,
            template="root_cause",
            max_tokens=300,
            temperature=0.3
        )
    
    def generate_recommendations(
        self,
        current_state: Dict[str, Any],
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Generate actionable recommendations"""
        question = "What actions should be taken to improve logistics performance?"
        
        return self.answer_question(
            question=question,
            context={"current_state": current_state},
            structured=False,
            user_phone=user_phone,
            template="recommendations",
            max_tokens=500,
            temperature=0.5
        )
    
    def generate_ceo_briefing(
        self,
        analytics_data: Dict[str, Any],
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Generate comprehensive CEO briefing"""
        
        briefing_parts = []
        
        # Executive summary
        summary = self.generate_executive_summary(analytics_data, user_phone)
        if summary.get("success"):
            briefing_parts.append(summary.get("content", ""))
        
        # Root cause
        root_cause = self.generate_root_cause(
            analytics_data.get("pending", {}),
            analytics_data.get("pod", {}),
            analytics_data.get("risks", {}),
            user_phone
        )
        if root_cause.get("success"):
            briefing_parts.append("\n" + root_cause.get("content", ""))
        
        # Recommendations
        recommendations = self.generate_recommendations(analytics_data, user_phone)
        if recommendations.get("success"):
            briefing_parts.append("\n" + recommendations.get("content", ""))
        
        combined = "\n\n━━━━━━━━━━━━━━━━━━━━\n\n".join(briefing_parts)
        
        return {
            "success": True,
            "content": combined,
            "provider_used": "ceo_briefing_composite"
        }
    
    def _log_usage(
        self,
        user_phone: str,
        question: str,
        response: str,
        provider: str,
        success: bool,
        processing_time_ms: int
    ):
        """Log AI usage for analytics"""
        if not self.db:
            return
        
        try:
            log_entry = AIResponseLog(
                conversation_id=None,
                prompt=question[:500],
                ai_response=response[:2000],
                model_name=provider,
                success=success,
                created_at=datetime.utcnow()
            )
            self.db.add(log_entry)
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to log AI usage: {e}")
            if self.db:
                self.db.rollback()


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

ai_provider_service = None


def init_ai_provider_service(db: Session = None) -> AIProviderService:
    """Initialize AI Provider Service singleton"""
    global ai_provider_service
    
    try:
        ai_provider_service = AIProviderService(db)
        
        # Log detailed status
        if ai_provider_service.is_available:
            logger.info("✅ AI PROVIDER SERVICE LOADED SUCCESSFULLY")
            health = ai_provider_service.check_health()
            logger.info(f"Health Status: {health}")
        else:
            logger.error("❌ AI PROVIDER SERVICE FAILED TO LOAD")
            logger.error("No AI providers are available. Check API keys.")
        
        return ai_provider_service
        
    except Exception as e:
        logger.error(f"❌ AI PROVIDER SERVICE INITIALIZATION ERROR: {e}")
        ai_provider_service = None
        raise


def get_ai_provider_service() -> Optional[AIProviderService]:
    """Get the AI Provider Service instance"""
    return ai_provider_service


# ==========================================================
# AUTO-INITIALIZATION (for compatibility)
# ==========================================================

# This runs when the module is imported
# But we need db for full init, so this is partial
try:
    # Try to initialize without db first
    ai_provider_service = AIProviderService(db=None)
    logger.info("AI Provider Service auto-initialized (no DB)")
except Exception as e:
    logger.error(f"Auto-initialization failed: {e}")
    ai_provider_service = None
