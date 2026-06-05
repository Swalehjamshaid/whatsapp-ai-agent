# ==========================================================
# FILE: app/services/ai_provider_service.py (ENTERPRISE GRADE v2)
# ==========================================================

import json
import time
import re
import hashlib
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, date
from functools import lru_cache
from enum import Enum
from dataclasses import dataclass, asdict

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


@dataclass
class ProviderHealth:
    """Provider health information"""
    status: str
    response_time_ms: int
    token_usage: int
    last_success: Optional[datetime]
    error_rate: float


# ==========================================================
# AI PROMPT TEMPLATES (UPGRADED WITH STRUCTURED OUTPUT)
# ==========================================================

PROMPTS = {
    "executive_summary": """
You are a Logistics AI Advisor for a major distribution company.

CONTEXT DATA:
{context}

QUESTION: {question}

RESPONSE REQUIREMENTS:
Return a VALID JSON object with EXACTLY this structure. Do not add any text before or after the JSON.

{
    "summary": "2-3 sentence executive summary",
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "health_score": 0-100 integer,
    "financial_exposure": 0,
    "top_risks": [
        {"risk": "description", "impact": "financial impact", "mitigation": "action"}
    ],
    "recommendations": [
        {"action": "description", "owner": "who", "timeline": "when", "impact": "expected result", "priority": "HIGH|MEDIUM|LOW"}
    ]
}
""",

    "dealer_analysis": """
You are a Logistics AI Advisor analyzing a dealer.

DEALER DATA:
{dealer_data}

QUESTION: {question}

Return a VALID JSON object with EXACTLY this structure:

{
    "summary": "Brief performance summary",
    "dealer_name": "name",
    "health_score": 0-100,
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "pending_dns": 0,
    "financial_exposure": 0,
    "root_causes": ["cause1", "cause2"],
    "recommendations": [
        {"action": "description", "priority": "HIGH|MEDIUM|LOW", "timeline": "days", "expected_impact": "description"}
    ],
    "trend": "IMPROVING|DECLINING|STABLE"
}
""",

    "warehouse_analysis": """
You are a Logistics AI Advisor analyzing a warehouse.

WAREHOUSE DATA:
{warehouse_data}

QUESTION: {question}

Return a VALID JSON object:

{
    "summary": "Warehouse performance summary",
    "warehouse_name": "name",
    "efficiency_score": 0-100,
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "pending_dns": 0,
    "bottlenecks": ["bottleneck1", "bottleneck2"],
    "capacity_utilization": 0-100,
    "recommendations": [
        {"action": "description", "priority": "HIGH|MEDIUM|LOW", "expected_improvement": "X%"}
    ]
}
""",

    "city_analysis": """
You are a Logistics AI Advisor analyzing a city.

CITY DATA:
{city_data}

QUESTION: {question}

Return a VALID JSON object:

{
    "summary": "City performance summary",
    "city_name": "name",
    "performance_score": 0-100,
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "pending_dns": 0,
    "financial_exposure": 0,
    "delay_rate": 0-100,
    "constraints": ["constraint1", "constraint2"],
    "recommendations": [
        {"action": "description", "priority": "HIGH|MEDIUM|LOW", "expected_reduction": "X%"}
    ]
}
""",

    "root_cause": """
You are a Logistics AI Advisor performing root cause analysis.

METRICS:
{pending_metrics}
{pod_metrics}
{risk_metrics}

QUESTION: {question}

Return a VALID JSON object:

{
    "summary": "Root cause summary",
    "root_causes": {
        "dealer_delay": 0-100,
        "warehouse_delay": 0-100,
        "documentation": 0-100,
        "transport": 0-100,
        "other": 0-100
    },
    "primary_cause": "dealer_delay|warehouse_delay|documentation|transport",
    "recommendations": [
        {"action": "description", "focus_area": "area", "expected_improvement": "X%"}
    ]
}
""",

    "recommendations": """
You are a Logistics AI Advisor providing recommendations.

CURRENT STATE:
{current_state}

QUESTION: {question}

Return a VALID JSON object:

{
    "summary": "Overview of recommendations",
    "immediate_actions": [
        {"action": "description", "owner": "who", "timeline": "24h", "expected_impact": "description"}
    ],
    "short_term_actions": [
        {"action": "description", "owner": "who", "timeline": "7d", "expected_impact": "description"}
    ],
    "strategic_actions": [
        {"action": "description", "owner": "who", "timeline": "30d", "expected_impact": "description"}
    ]
}
""",

    "dealer_forecast": """
You are a Logistics AI Advisor forecasting dealer risk.

DEALER HISTORY:
{dealer_history}

Return a VALID JSON object:

{
    "dealer_name": "name",
    "current_risk_score": 0-100,
    "forecasted_risk_score_30d": 0-100,
    "risk_trend": "INCREASING|DECREASING|STABLE",
    "probability_of_escalation": 0-100,
    "expected_pending_dns_30d": 0,
    "recommendations": [
        {"action": "description", "timeline": "days", "expected_impact": "description"}
    ]
}
""",

    "warehouse_forecast": """
You are a Logistics AI Advisor forecasting warehouse performance.

WAREHOUSE HISTORY:
{warehouse_history}

Return a VALID JSON object:

{
    "warehouse_name": "name",
    "current_efficiency": 0-100,
    "forecasted_efficiency_30d": 0-100,
    "bottleneck_risk": "LOW|MEDIUM|HIGH",
    "expected_backlog_30d": 0,
    "capacity_risk": "LOW|MEDIUM|HIGH",
    "recommendations": [
        {"action": "description", "timeline": "days", "expected_improvement": "X%"}
    ]
}
""",

    "city_forecast": """
You are a Logistics AI Advisor forecasting city performance.

CITY HISTORY:
{city_history}

Return a VALID JSON object:

{
    "city_name": "name",
    "current_performance": 0-100,
    "forecasted_performance_30d": 0-100,
    "delay_trend": "INCREASING|DECREASING|STABLE",
    "expected_delay_rate_30d": 0-100,
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "recommendations": [
        {"action": "description", "expected_improvement": "X%"}
    ]
}
""",

    "pod_forecast": """
You are a Logistics AI Advisor forecasting POD backlog.

POD HISTORY:
{pod_history}

Return a VALID JSON object:

{
    "current_pod_pending": 0,
    "forecasted_pod_pending_7d": 0,
    "forecasted_pod_pending_30d": 0,
    "backlog_trend": "INCREASING|DECREASING|STABLE",
    "projected_clearance_days": 0,
    "recommendations": [
        {"action": "description", "expected_reduction": "X%", "timeline": "days"}
    ]
}
""",

    "ceo_briefing": """
You are a Logistics AI Advisor preparing a CEO briefing.

EXECUTIVE DATA:
{executive_data}

Return a VALID JSON object:

{
    "briefing_date": "YYYY-MM-DD",
    "network_health": 0-100,
    "revenue_at_risk": 0,
    "inventory_at_risk": 0,
    "top_risks": [
        {"type": "dealer|warehouse|city", "name": "name", "severity": 0-100, "financial_impact": 0}
    ],
    "worst_dealer": {"name": "name", "pending_dns": 0, "exposure": 0},
    "worst_warehouse": {"name": "name", "efficiency": 0, "pending": 0},
    "worst_city": {"name": "name", "delay_rate": 0, "exposure": 0},
    "recommended_actions": [
        {"action": "description", "priority": "HIGH|MEDIUM|LOW", "expected_impact": "description", "timeline": "days"}
    ],
    "forecast_30d": {
        "expected_improvement": "description",
        "projected_revenue_recovery": 0
    }
}
""",

    "network_health_report": """
You are a Logistics AI Advisor generating a network health report.

NETWORK DATA:
{network_data}

Return a VALID JSON object:

{
    "report_date": "YYYY-MM-DD",
    "overall_health": 0-100,
    "pod_compliance": 0-100,
    "delivery_compliance": 0-100,
    "dealer_health_avg": 0-100,
    "warehouse_health_avg": 0-100,
    "city_health_avg": 0-100,
    "critical_issues": ["issue1", "issue2"],
    "improvement_areas": ["area1", "area2"],
    "recommendations": [
        {"action": "description", "category": "dealer|warehouse|city|process", "impact": "description"}
    ]
}
""",

    "weekly_review": """
You are a Logistics AI Advisor preparing a weekly management review.

WEEKLY DATA:
{weekly_data}

Return a VALID JSON object:

{
    "review_week": "YYYY-WW",
    "key_metrics": {
        "total_dns": 0,
        "delivery_rate": 0-100,
        "pod_compliance": 0-100,
        "revenue_at_risk": 0
    },
    "week_over_week_change": {
        "delivery_rate": -100 to 100,
        "pod_compliance": -100 to 100,
        "revenue_at_risk": -100 to 100
    },
    "top_performers": {
        "dealer": "name",
        "warehouse": "name",
        "city": "name"
    },
    "bottom_performers": {
        "dealer": "name",
        "warehouse": "name",
        "city": "name"
    },
    "action_items": [
        {"action": "description", "owner": "who", "status": "NEW|IN_PROGRESS|COMPLETED"}
    ],
    "next_week_focus": ["focus1", "focus2"]
}
""",

    "action_plan": """
You are a Logistics AI Advisor creating an action plan.

CURRENT SITUATION:
{situation}

Return a VALID JSON object:

{
    "plan_name": "name",
    "created_date": "YYYY-MM-DD",
    "target_completion": "YYYY-MM-DD",
    "phases": [
        {
            "phase": 1,
            "name": "phase name",
            "duration_days": 0,
            "actions": [
                {"action": "description", "owner": "who", "dependencies": [], "expected_outcome": "description"}
            ]
        }
    ],
    "success_metrics": ["metric1", "metric2"],
    "risk_mitigation": ["mitigation1", "mitigation2"]
}
""",

    "general": """
You are a Logistics AI Assistant for a WhatsApp business platform.

CONTEXT:
{context}

QUESTION: {question}

Return a VALID JSON object:

{
    "response": "Your helpful, concise response here",
    "confidence": 0-100,
    "requires_followup": false,
    "suggested_questions": ["question1", "question2"]
}
"""
}


# ==========================================================
# AI CACHE MANAGER
# ==========================================================

class AICacheManager:
    """Cache AI responses for cost reduction and speed"""
    
    def __init__(self, ttl_seconds: int = 300):
        self.cache: Dict[str, Tuple[str, float, Dict]] = {}
        self.ttl = ttl_seconds
    
    def get(self, key: str) -> Optional[Tuple[str, Dict]]:
        if key in self.cache:
            response, timestamp, metadata = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return response, metadata
            del self.cache[key]
        return None
    
    def set(self, key: str, response: str, metadata: Dict = None):
        self.cache[key] = (response, time.time(), metadata or {})
    
    def clear(self):
        self.cache.clear()
    
    def get_cache_key(self, prompt: str, model: str, context_hash: str = "", user_role: str = "") -> str:
        content_hash = hashlib.md5(prompt[:500].encode()).hexdigest()
        return f"{model}:{context_hash}:{user_role}:{content_hash}"


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
        for pattern in cls.DANGEROUS_PATTERNS:
            response = re.sub(pattern, "[REDACTED]", response, flags=re.IGNORECASE)
        return response[:4000]
    
    @classmethod
    def extract_json_from_response(cls, response: str) -> Dict[str, Any]:
        """Extract JSON from AI response, handling markdown and extra text"""
        # Try to find JSON in the response
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # If no JSON found, return default structure
        return {"response": response[:500], "confidence": 50, "requires_followup": True, "suggested_questions": []}


# ==========================================================
# AI COST TRACKER
# ==========================================================

class AICostTracker:
    """Track AI usage costs"""
    
    # Cost per 1M tokens (approximate)
    COST_PER_M_TOKEN = {
        "deepseek-chat": 0.14,  # DeepSeek input
        "deepseek-chat-output": 0.28,  # DeepSeek output
        "gpt-4": 30.00,
        "gpt-4-turbo": 10.00,
        "gpt-3.5-turbo": 0.50,
    }
    
    def __init__(self):
        self.total_cost = Decimal('0')
        self.total_tokens = 0
        self.usage_by_provider = {}
    
    def calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        """Calculate cost for a request"""
        input_cost = (prompt_tokens / 1_000_000) * self.COST_PER_M_TOKEN.get(model, 0.50)
        output_cost = (completion_tokens / 1_000_000) * self.COST_PER_M_TOKEN.get(f"{model}-output", 0.50)
        return Decimal(str(input_cost + output_cost))
    
    def track_usage(self, model: str, prompt_tokens: int, completion_tokens: int):
        """Track usage for analytics"""
        cost = self.calculate_cost(model, prompt_tokens, completion_tokens)
        self.total_cost += cost
        self.total_tokens += prompt_tokens + completion_tokens
        
        if model not in self.usage_by_provider:
            self.usage_by_provider[model] = {"requests": 0, "tokens": 0, "cost": Decimal('0')}
        
        self.usage_by_provider[model]["requests"] += 1
        self.usage_by_provider[model]["tokens"] += prompt_tokens + completion_tokens
        self.usage_by_provider[model]["cost"] += cost
    
    def get_summary(self) -> Dict:
        """Get usage summary"""
        return {
            "total_cost": float(self.total_cost),
            "total_cost_formatted": f"${float(self.total_cost):.4f}",
            "total_tokens": self.total_tokens,
            "by_provider": {
                provider: {
                    "requests": data["requests"],
                    "tokens": data["tokens"],
                    "cost": float(data["cost"])
                }
                for provider, data in self.usage_by_provider.items()
            }
        }


# ==========================================================
# CONVERSATION CONTEXT MANAGER
# ==========================================================

class ConversationContextManager:
    """Manage conversation context for follow-up questions"""
    
    def __init__(self):
        self.contexts: Dict[str, Dict] = {}
    
    def update(self, user_phone: str, intent: str = None, entity: str = None, 
               last_question: str = None, last_response: str = None):
        """Update conversation context"""
        if user_phone not in self.contexts:
            self.contexts[user_phone] = {
                "last_intent": None,
                "last_entity": None,
                "last_question": None,
                "last_response": None,
                "history": []
            }
        
        context = self.contexts[user_phone]
        if intent:
            context["last_intent"] = intent
        if entity:
            context["last_entity"] = entity
        if last_question:
            context["last_question"] = last_question
        if last_response:
            context["last_response"] = last_response
        
        if last_question:
            context["history"].append({
                "question": last_question,
                "timestamp": datetime.utcnow().isoformat()
            })
            if len(context["history"]) > 10:
                context["history"].pop(0)
    
    def get_context(self, user_phone: str, current_question: str) -> Dict:
        """Get enriched context for AI"""
        context = self.contexts.get(user_phone, {})
        
        # Detect if this is a follow-up
        follow_up_patterns = ["it", "they", "that", "this", "how", "why", "what", "improve", "fix"]
        is_follow_up = any(pattern in current_question.lower() for pattern in follow_up_patterns)
        
        enriched = {
            "last_intent": context.get("last_intent"),
            "last_entity": context.get("last_entity"),
            "last_question": context.get("last_question"),
            "is_follow_up": is_follow_up,
            "conversation_history": context.get("history", [])[-3:]
        }
        
        # If follow-up and has context, inject the context
        if is_follow_up and context.get("last_entity"):
            enriched["referenced_entity"] = context.get("last_entity")
            enriched["referenced_intent"] = context.get("last_intent")
        
        return enriched
    
    def clear(self, user_phone: str):
        if user_phone in self.contexts:
            del self.contexts[user_phone]


# ==========================================================
# AI PROVIDER SERVICE (ENTERPRISE GRADE v2)
# ==========================================================

class AIProviderService:
    """
    Enterprise AI Provider Service v2
    
    New Features:
    - Structured JSON responses
    - Forecast templates
    - CEO briefing template
    - Cost tracking
    - Conversation context
    - Confidence scoring
    - Enhanced health checks
    """
    
    def __init__(self, db: Session = None):
        self.db = db
        self.cache = AICacheManager(ttl_seconds=300)
        self.cost_tracker = AICostTracker()
        self.conversation_context = ConversationContextManager()
        self.retry_count = 3
        self.retry_delay = 1
        
        # Provider health tracking
        self.provider_health: Dict[str, ProviderHealth] = {}
        
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
        
        # Initialize OpenAI client (using GPT-4 Turbo for better quality)
        self.openai_api_key = getattr(config, 'OPENAI_API_KEY', None)
        self.openai_client = None
        self.openai_status = ProviderStatus.UNKNOWN
        self.openai_model = getattr(config, 'OPENAI_MODEL', 'gpt-4-turbo')
        
        if self.openai_api_key:
            try:
                self.openai_client = OpenAI(
                    api_key=self.openai_api_key,
                    timeout=30.0,
                    max_retries=2
                )
                logger.info(f"✅ OpenAI client initialized (model: {self.openai_model})")
                self.openai_status = ProviderStatus.ONLINE
            except Exception as e:
                logger.error(f"❌ OpenAI initialization failed: {e}")
                self.openai_status = ProviderStatus.OFFLINE
        
        self.is_available = (
            self.deepseek_status == ProviderStatus.ONLINE or
            self.openai_status == ProviderStatus.ONLINE
        )
        
        self._log_startup_status()
    
    def _log_startup_status(self):
        """Log comprehensive startup status"""
        logger.info("=" * 60)
        logger.info("🤖 AI PROVIDER SERVICE v2 INITIALIZED")
        logger.info(f"DeepSeek: {self.deepseek_status.value}")
        logger.info(f"OpenAI: {self.openai_status.value} (model: {self.openai_model})")
        logger.info(f"Overall Available: {self.is_available}")
        logger.info(f"Cache TTL: {self.cache.ttl}s")
        logger.info(f"Retry Count: {self.retry_count}")
        logger.info("Structured JSON Responses: ENABLED")
        logger.info("Cost Tracking: ENABLED")
        logger.info("Conversation Context: ENABLED")
        logger.info("=" * 60)
    
    # ==========================================================
    # IMPROVED HEALTH CHECK
    # ==========================================================
    
    def check_provider_status(self, provider: str) -> ProviderHealth:
        """Detailed health check for a provider"""
        start_time = time.time()
        
        if provider == "deepseek":
            client = self.deepseek_client
            status = self.deepseek_status
        elif provider == "openai":
            client = self.openai_client
            status = self.openai_status
        else:
            return ProviderHealth(
                status="unknown",
                response_time_ms=0,
                token_usage=0,
                last_success=None,
                error_rate=100.0
            )
        
        if not client or status != ProviderStatus.ONLINE:
            return ProviderHealth(
                status=status.value if status else "offline",
                response_time_ms=0,
                token_usage=0,
                last_success=None,
                error_rate=100.0
            )
        
        # Test with minimal call
        try:
            test_start = time.time()
            if provider == "deepseek":
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": "OK"}],
                    max_tokens=5
                )
            else:
                response = client.chat.completions.create(
                    model=self.openai_model,
                    messages=[{"role": "user", "content": "OK"}],
                    max_tokens=5
                )
            
            response_time_ms = int((time.time() - test_start) * 1000)
            token_usage = response.usage.total_tokens if hasattr(response, 'usage') else 0
            
            # Update health status
            health = ProviderHealth(
                status="online",
                response_time_ms=response_time_ms,
                token_usage=token_usage,
                last_success=datetime.utcnow(),
                error_rate=0.0
            )
            
            self.provider_health[provider] = health
            return health
            
        except Exception as e:
            logger.warning(f"{provider} health check failed: {e}")
            health = self.provider_health.get(provider, ProviderHealth(
                status="degraded",
                response_time_ms=0,
                token_usage=0,
                last_success=None,
                error_rate=100.0
            ))
            return health
    
    def check_health(self) -> Dict[str, Any]:
        """Comprehensive health check"""
        deepseek_health = self.check_provider_status("deepseek")
        openai_health = self.check_provider_status("openai")
        
        return {
            "deepseek": {
                "status": deepseek_health.status,
                "response_time_ms": deepseek_health.response_time_ms,
                "last_success": deepseek_health.last_success.isoformat() if deepseek_health.last_success else None,
                "error_rate": deepseek_health.error_rate
            },
            "openai": {
                "status": openai_health.status,
                "response_time_ms": openai_health.response_time_ms,
                "last_success": openai_health.last_success.isoformat() if openai_health.last_success else None,
                "error_rate": openai_health.error_rate
            },
            "overall": self.is_available,
            "cache_enabled": True,
            "cost_summary": self.cost_tracker.get_summary(),
            "timestamp": datetime.utcnow().isoformat()
        }
    
    # ==========================================================
    # CORE AI METHOD (UPGRADED WITH STRUCTURED OUTPUT)
    # ==========================================================
    
    def answer_question(
        self,
        question: str,
        context: Dict[str, Any] = None,
        structured: bool = True,
        user_phone: str = None,
        template: str = "general",
        max_tokens: int = 1000,
        temperature: float = 0.7,
        require_json: bool = True
    ) -> Dict[str, Any]:
        """
        Answer a question using AI with structured output
        
        Args:
            question: User's question
            context: Additional context data
            structured: Whether to return structured response
            user_phone: User identifier for logging
            template: Prompt template to use
            max_tokens: Maximum response tokens
            temperature: Response creativity (0-1)
            require_json: Whether to parse response as JSON
        
        Returns:
            Dict with success, content, structured_data, confidence, etc.
        """
        start_time = time.time()
        
        # Validate prompt safety
        is_safe, error_msg = AISafetyLayer.validate_prompt(question)
        if not is_safe:
            logger.warning(f"Unsafe prompt blocked: {error_msg}")
            return {
                "success": False,
                "content": "⚠️ Your request contains unsafe content and cannot be processed.",
                "structured_data": None,
                "confidence": 0,
                "error": error_msg,
                "provider_used": "safety_layer",
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # Get conversation context for follow-up
        conv_context = {}
        if user_phone:
            conv_context = self.conversation_context.get_context(user_phone, question)
            if context is None:
                context = {}
            context["conversation"] = conv_context
        
        # Build prompt using template
        prompt = self._build_prompt(question, context, template)
        
        # Check cache (only for non-follow-up questions)
        cache_key = None
        if not conv_context.get("is_follow_up", False):
            cache_key = self.cache.get_cache_key(
                prompt, "deepseek", 
                context_hash=str(hash(str(context))),
                user_role=context.get("user_role", "guest") if context else "guest"
            )
            cached_response = self.cache.get(cache_key)
            if cached_response:
                cached_content, cached_metadata = cached_response
                logger.info(f"Cache hit for: {question[:50]}...")
                return {
                    "success": True,
                    "content": cached_content,
                    "structured_data": AISafetyLayer.extract_json_from_response(cached_content) if require_json else None,
                    "confidence": cached_metadata.get("confidence", 85),
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
                # Track cost
                self.cost_tracker.track_usage(
                    "deepseek-chat",
                    response.get("usage", {}).get("prompt_tokens", 0),
                    response.get("usage", {}).get("completion_tokens", 0)
                )
        
        # Fallback to OpenAI
        if not response or not response.get("success"):
            if self.openai_status == ProviderStatus.ONLINE:
                response = self._call_openai(prompt, max_tokens, temperature)
                if response.get("success"):
                    provider_used = "openai"
                    self.cost_tracker.track_usage(
                        self.openai_model,
                        response.get("usage", {}).get("prompt_tokens", 0),
                        response.get("usage", {}).get("completion_tokens", 0)
                    )
        
        # Final fallback
        if not response or not response.get("success"):
            response = self._call_fallback(question)
            provider_used = "fallback"
        
        # Process response
        content = response.get("content", "")
        content = AISafetyLayer.sanitize_response(content)
        
        # Extract structured data if requested
        structured_data = None
        confidence = 50  # Default confidence
        
        if require_json:
            structured_data = AISafetyLayer.extract_json_from_response(content)
            # Calculate confidence based on response quality
            confidence = self._calculate_confidence(structured_data, provider_used, response.get("usage", {}))
        else:
            # For non-JSON responses, still try to extract if possible
            structured_data = AISafetyLayer.extract_json_from_response(content) if "{" in content else None
        
        # Cache successful response
        if response.get("success") and provider_used in ["deepseek", "openai"] and cache_key:
            self.cache.set(cache_key, content, {"confidence": confidence})
        
        # Update conversation context
        if user_phone:
            self.conversation_context.update(
                user_phone,
                intent=context.get("intent") if context else None,
                entity=context.get("entity") if context else None,
                last_question=question,
                last_response=content[:500]
            )
        
        # Log usage
        processing_time = int((time.time() - start_time) * 1000)
        
        self._log_usage(
            user_phone=user_phone,
            question=question,
            response=content,
            provider=provider_used,
            success=response.get("success", False),
            processing_time_ms=processing_time,
            tokens=response.get("usage", {}).get("total_tokens", 0),
            confidence=confidence
        )
        
        result = {
            "success": response.get("success", False),
            "content": content,
            "structured_data": structured_data if structured else None,
            "confidence": confidence,
            "provider_used": provider_used,
            "processing_time_ms": processing_time,
            "cached": False
        }
        
        logger.info(f"AI Response: provider={provider_used}, success={response.get('success')}, confidence={confidence}, time={processing_time}ms")
        
        return result
    
    def _calculate_confidence(self, structured_data: Optional[Dict], provider: str, usage: Dict) -> int:
        """Calculate confidence score for AI response"""
        confidence = 70  # Base confidence
        
        if not structured_data:
            return 50
        
        # Boost based on provider
        if provider == "deepseek":
            confidence += 5
        elif provider == "openai":
            confidence += 10
        
        # Boost based on token usage (more tokens = more detailed = higher confidence)
        if usage.get("total_tokens", 0) > 500:
            confidence += 10
        elif usage.get("total_tokens", 0) > 200:
            confidence += 5
        
        # Boost based on data completeness
        if structured_data.get("health_score") is not None:
            confidence += 5
        if structured_data.get("recommendations"):
            confidence += 5
        if structured_data.get("risk_level"):
            confidence += 5
        
        return min(100, confidence)
    
    def _build_prompt(self, question: str, context: Dict = None, template: str = "general") -> str:
        """Build prompt using template and context"""
        template_content = PROMPTS.get(template, PROMPTS["general"])
        
        # Format context as JSON
        context_str = json.dumps(context, indent=2, default=str, ensure_ascii=False) if context else "No additional context"
        
        # Limit context length
        if len(context_str) > 3000:
            context_str = context_str[:3000] + "..."
        
        return template_content.format(
            context=context_str,
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
                        {"role": "system", "content": "You are a logistics AI advisor. Return ONLY valid JSON when requested. Be concise, professional, and data-driven."},
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
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
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
                    model=self.openai_model,
                    messages=[
                        {"role": "system", "content": "You are a logistics AI advisor. Return ONLY valid JSON when requested. Be concise, professional, and data-driven."},
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
                    "model": self.openai_model,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
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
        
        if any(word in question_lower for word in ["pending", "backlog"]):
            content = json.dumps({
                "response": "I can see you're asking about pending items. Please check the pending dashboard or contact your warehouse manager for real-time updates.",
                "confidence": 40,
                "requires_followup": True,
                "suggested_questions": ["Show me top pending dealers", "What is the total pending value?"]
            })
        elif any(word in question_lower for word in ["risk", "critical", "urgent"]):
            content = json.dumps({
                "response": "For risk assessment, please review the executive dashboard. Contact operations if you need immediate assistance.",
                "confidence": 35,
                "requires_followup": True,
                "suggested_questions": ["What are the top risks?", "Show me risk dashboard"]
            })
        elif any(word in question_lower for word in ["health", "score", "network"]):
            content = json.dumps({
                "response": "Network health data is available in the analytics dashboard. Our AI system is temporarily unavailable for detailed analysis.",
                "confidence": 30,
                "requires_followup": False,
                "suggested_questions": ["Show me executive summary", "What should I focus on?"]
            })
        else:
            content = json.dumps({
                "response": "Our AI assistant is currently experiencing high demand. Please try your request again in a moment. For urgent matters, contact operations directly.",
                "confidence": 25,
                "requires_followup": True,
                "suggested_questions": ["Show me help", "What can you do?", "Executive summary"]
            })
        
        return {
            "success": True,
            "content": content,
            "model": "fallback",
            "fallback": True,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }
    
    # ==========================================================
    # SPECIALIZED PRD METHODS
    # ==========================================================
    
    def generate_network_health_report(self, network_data: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate comprehensive network health report"""
        return self.answer_question(
            question="Generate a network health report based on the data provided.",
            context=network_data,
            structured=True,
            user_phone=user_phone,
            template="network_health_report",
            max_tokens=1000,
            temperature=0.3
        )
    
    def generate_dealer_briefing(self, dealer_data: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate dealer briefing"""
        return self.answer_question(
            question="Provide a comprehensive briefing on this dealer's performance.",
            context=dealer_data,
            structured=True,
            user_phone=user_phone,
            template="dealer_analysis",
            max_tokens=800,
            temperature=0.4
        )
    
    def generate_city_briefing(self, city_data: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate city briefing"""
        return self.answer_question(
            question="Provide a comprehensive briefing on this city's logistics performance.",
            context=city_data,
            structured=True,
            user_phone=user_phone,
            template="city_analysis",
            max_tokens=800,
            temperature=0.4
        )
    
    def generate_warehouse_briefing(self, warehouse_data: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate warehouse briefing"""
        return self.answer_question(
            question="Provide a comprehensive briefing on this warehouse's efficiency.",
            context=warehouse_data,
            structured=True,
            user_phone=user_phone,
            template="warehouse_analysis",
            max_tokens=800,
            temperature=0.4
        )
    
    def generate_weekly_management_review(self, weekly_data: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate weekly management review"""
        return self.answer_question(
            question="Generate a weekly management review based on the data provided.",
            context=weekly_data,
            structured=True,
            user_phone=user_phone,
            template="weekly_review",
            max_tokens=1200,
            temperature=0.3
        )
    
    def generate_forecast_report(self, forecast_data: Dict[str, Any], forecast_type: str, user_phone: str = None) -> Dict[str, Any]:
        """Generate forecast report for dealer, warehouse, city, or POD"""
        template_map = {
            "dealer": "dealer_forecast",
            "warehouse": "warehouse_forecast",
            "city": "city_forecast",
            "pod": "pod_forecast"
        }
        
        template = template_map.get(forecast_type, "dealer_forecast")
        
        return self.answer_question(
            question=f"Generate a {forecast_type} forecast report.",
            context=forecast_data,
            structured=True,
            user_phone=user_phone,
            template=template,
            max_tokens=600,
            temperature=0.3
        )
    
    def generate_action_plan(self, situation: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate action plan based on current situation"""
        return self.answer_question(
            question="Create an action plan to address the current situation.",
            context=situation,
            structured=True,
            user_phone=user_phone,
            template="action_plan",
            max_tokens=1000,
            temperature=0.4
        )
    
    def generate_ceo_briefing(self, executive_data: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate comprehensive CEO briefing"""
        return self.answer_question(
            question="Prepare a comprehensive CEO briefing.",
            context=executive_data,
            structured=True,
            user_phone=user_phone,
            template="ceo_briefing",
            max_tokens=1200,
            temperature=0.3
        )
    
    def generate_dealer_forecast(self, dealer_history: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate dealer risk forecast"""
        return self.generate_forecast_report(dealer_history, "dealer", user_phone)
    
    def generate_warehouse_forecast(self, warehouse_history: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate warehouse performance forecast"""
        return self.generate_forecast_report(warehouse_history, "warehouse", user_phone)
    
    def generate_city_forecast(self, city_history: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate city performance forecast"""
        return self.generate_forecast_report(city_history, "city", user_phone)
    
    def generate_pod_forecast(self, pod_history: Dict[str, Any], user_phone: str = None) -> Dict[str, Any]:
        """Generate POD backlog forecast"""
        return self.generate_forecast_report(pod_history, "pod", user_phone)
    
    # ==========================================================
    # EXISTING SPECIALIZED METHODS (UPDATED)
    # ==========================================================
    
    def analyze_dealer(
        self,
        dealer_data: Dict[str, Any],
        structured: bool = True,
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Analyze dealer performance with structured output"""
        return self.generate_dealer_briefing(dealer_data, user_phone)
    
    def analyze_warehouse(
        self,
        warehouse_data: Dict[str, Any],
        structured: bool = True,
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Analyze warehouse performance with structured output"""
        return self.generate_warehouse_briefing(warehouse_data, user_phone)
    
    def analyze_city(
        self,
        city_data: Dict[str, Any],
        structured: bool = True,
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Analyze city performance with structured output"""
        return self.generate_city_briefing(city_data, user_phone)
    
    def generate_executive_summary(
        self,
        metrics: Dict[str, Any],
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Generate executive summary with structured output"""
        return self.answer_question(
            question="Generate an executive summary of logistics performance.",
            context={"metrics": metrics},
            structured=True,
            user_phone=user_phone,
            template="executive_summary",
            max_tokens=600,
            temperature=0.3
        )
    
    def generate_root_cause(
        self,
        pending_metrics: Dict[str, Any],
        pod_metrics: Dict[str, Any],
        risk_metrics: Dict[str, Any],
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Generate root cause analysis with structured output"""
        return self.answer_question(
            question="What are the root causes of delivery delays?",
            context={
                "pending_metrics": pending_metrics,
                "pod_metrics": pod_metrics,
                "risk_metrics": risk_metrics
            },
            structured=True,
            user_phone=user_phone,
            template="root_cause",
            max_tokens=400,
            temperature=0.3
        )
    
    def generate_recommendations(
        self,
        current_state: Dict[str, Any],
        user_phone: str = None
    ) -> Dict[str, Any]:
        """Generate actionable recommendations with structured output"""
        return self.answer_question(
            question="What actions should be taken to improve logistics performance?",
            context={"current_state": current_state},
            structured=True,
            user_phone=user_phone,
            template="recommendations",
            max_tokens=700,
            temperature=0.4
        )
    
    def get_cost_summary(self) -> Dict[str, Any]:
        """Get AI usage cost summary"""
        return self.cost_tracker.get_summary()
    
    def get_conversation_context(self, user_phone: str) -> Dict[str, Any]:
        """Get conversation context for a user"""
        return self.conversation_context.get_context(user_phone, "")
    
    def clear_conversation_context(self, user_phone: str):
        """Clear conversation context for a user"""
        self.conversation_context.clear(user_phone)
    
    def _log_usage(
        self,
        user_phone: str,
        question: str,
        response: str,
        provider: str,
        success: bool,
        processing_time_ms: int,
        tokens: int = 0,
        confidence: int = 0
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
        
        if ai_provider_service.is_available:
            logger.info("✅ AI PROVIDER SERVICE LOADED SUCCESSFULLY")
            health = ai_provider_service.check_health()
            logger.info(f"Health Status: {health.get('deepseek', {}).get('status')}, {health.get('openai', {}).get('status')}")
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
# AUTO-INITIALIZATION
# ==========================================================

try:
    ai_provider_service = AIProviderService(db=None)
    logger.info("AI Provider Service auto-initialized (no DB)")
except Exception as e:
    logger.error(f"Auto-initialization failed: {e}")
    ai_provider_service = None
