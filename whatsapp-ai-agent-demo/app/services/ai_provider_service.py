# ==========================================================
# FILE: app/services/ai_provider_service.py (ENTERPRISE v3.0)
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
# DEDICATED PROMPT TEMPLATES (Improvement 6)
# ==========================================================

def dealer_analysis_prompt(dealer_data: Dict, question: str, role_context: str) -> str:
    """Dealer analysis prompt template"""
    return f"""
You are a Logistics AI Advisor analyzing a dealer.

ROLE CONTEXT: {role_context}

DEALER DATA:
- Name: {dealer_data.get('dealer_name', 'Unknown')}
- Total DNs: {dealer_data.get('total_dns', 0)}
- Delivered: {dealer_data.get('delivered_dns', 0)}
- Pending: {dealer_data.get('pending_dns', 0)}
- POD Pending: {dealer_data.get('pod_pending_dns', 0)}
- Total Value: Rs {dealer_data.get('total_value', 0):,.2f}
- Pending Value: Rs {dealer_data.get('pending_value', 0):,.2f}
- Health Score: {dealer_data.get('health_score', 0)}/100
- Risk Level: {dealer_data.get('risk_level', 'Unknown')}

USER QUESTION: {question}

RESPONSE REQUIREMENTS:
Return a VALID JSON object with EXACTLY this structure:

{{
    "summary": "2-3 sentence performance summary",
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "health_score": 0-100,
    "pending_dns": 0,
    "financial_exposure": 0,
    "root_causes": ["cause1", "cause2"],
    "recommendations": [
        {{"action": "description", "priority": "HIGH|MEDIUM|LOW", "timeline": "days", "expected_impact": "description"}}
    ]
}}
"""


def warehouse_analysis_prompt(warehouse_data: Dict, question: str, role_context: str) -> str:
    """Warehouse analysis prompt template"""
    return f"""
You are a Logistics AI Advisor analyzing a warehouse.

ROLE CONTEXT: {role_context}

WAREHOUSE DATA:
- Name: {warehouse_data.get('warehouse_name', 'Unknown')}
- Total DNs: {warehouse_data.get('total_dns', 0)}
- Pending DNs: {warehouse_data.get('pending_dns', 0)}
- POD Pending: {warehouse_data.get('pod_pending_dns', 0)}
- Backlog Units: {warehouse_data.get('backlog_units', 0):,.0f}
- Backlog Value: Rs {warehouse_data.get('backlog_value', 0):,.2f}
- Efficiency Score: {warehouse_data.get('efficiency_score', 0)}%
- Risk Score: {warehouse_data.get('risk_score', 0)}/100
- Bottlenecks: {warehouse_data.get('bottlenecks', [])}

USER QUESTION: {question}

Return a VALID JSON object:

{{
    "summary": "Warehouse performance summary",
    "efficiency_score": 0-100,
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "pending_dns": 0,
    "bottlenecks": ["bottleneck1", "bottleneck2"],
    "recommendations": [
        {{"action": "description", "priority": "HIGH|MEDIUM|LOW", "expected_improvement": "X%"}}
    ]
}}
"""


def city_analysis_prompt(city_data: Dict, question: str, role_context: str) -> str:
    """City analysis prompt template"""
    return f"""
You are a Logistics AI Advisor analyzing a city.

ROLE CONTEXT: {role_context}

CITY DATA:
- Name: {city_data.get('city', 'Unknown')}
- Delivery Volume: {city_data.get('delivery_volume', 0)} DNs
- Pending Volume: {city_data.get('pending_volume', 0)} DNs
- POD Backlog: {city_data.get('pod_backlog', 0)} DNs
- Revenue Exposure: Rs {city_data.get('revenue_exposure', 0):,.2f}
- Health Score: {city_data.get('city_health_score', 0)}/100
- Risk Score: {city_data.get('city_risk_score', 0)}/100
- Dealers Affected: {city_data.get('dealers_affected', 0)}

USER QUESTION: {question}

Return a VALID JSON object:

{{
    "summary": "City performance summary",
    "performance_score": 0-100,
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "pending_dns": 0,
    "financial_exposure": 0,
    "constraints": ["constraint1", "constraint2"],
    "recommendations": [
        {{"action": "description", "priority": "HIGH|MEDIUM|LOW", "expected_reduction": "X%"}}
    ]
}}
"""


def executive_prompt(metrics: Dict, question: str, role_context: str) -> str:
    """Executive summary prompt template"""
    return f"""
You are a Logistics AI Advisor preparing an executive briefing.

ROLE CONTEXT: {role_context}

EXECUTIVE DATA:
- Network Health: {metrics.get('network_health', 0)}/100
- Revenue at Risk: Rs {metrics.get('revenue_at_risk', 0):,.2f}
- Inventory at Risk: {metrics.get('inventory_at_risk', 0):,.0f} units
- Pending DNs: {metrics.get('pending_dns', 0)}
- POD Pending: {metrics.get('pod_pending', 0)}
- Top Risk Dealer: {metrics.get('top_risk_dealer', 'Unknown')}
- Top Risk City: {metrics.get('top_risk_city', 'Unknown')}
- Top Risk Warehouse: {metrics.get('top_risk_warehouse', 'Unknown')}

USER QUESTION: {question}

Return a VALID JSON object:

{{
    "summary": "Executive summary (2-3 sentences)",
    "network_health": 0-100,
    "revenue_at_risk": 0,
    "inventory_at_risk": 0,
    "top_risks": [
        {{"type": "dealer|warehouse|city", "name": "name", "severity": 0-100, "financial_impact": 0}}
    ],
    "focus_today": "Single most important action",
    "recommendations": [
        {{"action": "description", "priority": "HIGH|MEDIUM|LOW", "expected_impact": "description", "timeline": "days"}}
    ]
}}
"""


def root_cause_prompt(root_cause_data: Dict, question: str, role_context: str) -> str:
    """Root cause analysis prompt template"""
    return f"""
You are a Logistics AI Advisor performing root cause analysis.

ROLE CONTEXT: {role_context}

ROOT CAUSE DATA:
- Dealer Issues: {root_cause_data.get('dealer_issues', 0)}%
- Warehouse Issues: {root_cause_data.get('warehouse_issues', 0)}%
- Transport Issues: {root_cause_data.get('transport_issues', 0)}%
- Documentation Issues: {root_cause_data.get('documentation_issues', 0)}%
- Primary Cause: {root_cause_data.get('primary_cause', 'Unknown')}

USER QUESTION: {question}

Return a VALID JSON object:

{{
    "summary": "Root cause summary",
    "root_causes": {{
        "dealer_delay": 0-100,
        "warehouse_delay": 0-100,
        "documentation": 0-100,
        "transport": 0-100
    }},
    "primary_cause": "cause_name",
    "recommendations": [
        {{"action": "description", "focus_area": "area", "expected_improvement": "X%"}}
    ]
}}
"""


def forecast_prompt(forecast_data: Dict, question: str, role_context: str) -> str:
    """Forecast prompt template"""
    return f"""
You are a Logistics AI Advisor providing forecast analysis.

ROLE CONTEXT: {role_context}

FORECAST DATA:
- Current POD Pending: {forecast_data.get('current_pod_pending', 0)}
- Forecasted POD Pending (30d): {forecast_data.get('forecasted_pod_pending_30d', 0)}
- Backlog Trend: {forecast_data.get('backlog_trend', 'STABLE')}
- Projected Clearance Days: {forecast_data.get('projected_clearance_days', 0)}

USER QUESTION: {question}

Return a VALID JSON object:

{{
    "summary": "Forecast summary",
    "current_status": 0,
    "forecasted_status_30d": 0,
    "trend": "INCREASING|DECREASING|STABLE",
    "risk_level": "LOW|MEDIUM|HIGH",
    "recommendations": [
        {{"action": "description", "expected_improvement": "X%", "timeline": "days"}}
    ]
}}
"""


def recommendation_prompt(recommendation_data: Dict, question: str, role_context: str) -> str:
    """Recommendation prompt template"""
    return f"""
You are a Logistics AI Advisor providing actionable recommendations.

ROLE CONTEXT: {role_context}

CURRENT STATE:
- Network Health: {recommendation_data.get('network_health', 0)}/100
- Pending DNs: {recommendation_data.get('pending_dns', 0)}
- POD Pending: {recommendation_data.get('pod_pending', 0)}
- Revenue at Risk: Rs {recommendation_data.get('revenue_at_risk', 0):,.2f}
- Top Risks: {recommendation_data.get('top_risks', [])}

USER QUESTION: {question}

Return a VALID JSON object:

{{
    "summary": "Recommendation summary",
    "priority_actions": [
        {{
            "priority": "HIGH|MEDIUM|LOW",
            "action": "description",
            "impact": "expected result",
            "owner": "who should own this",
            "timeline": "days",
            "expected_improvement": "X%"
        }}
    ],
    "strategic_actions": [
        {{
            "action": "description",
            "timeline": "days",
            "expected_impact": "description"
        }}
    ]
}}
"""


# ==========================================================
# ROLE-BASED CONTEXTS (Improvement 4)
# ==========================================================

ROLE_CONTEXTS = {
    "ceo": "You are addressing the CEO. Focus on: network health (0-100 score), top 3 risks with financial impact, revenue at risk (Rs amount), inventory at risk (units), and 3-5 high-level strategic recommendations. Be concise and business-focused. Use executive language.",
    "manager": "You are addressing a Logistics Manager. Focus on: KPIs (pending DNs, POD compliance, delivery rates), dealer performance issues, warehouse bottlenecks, city-level problems, and actionable operational recommendations.",
    "dealer": "You are addressing a Dealer Manager. Focus on: dealer scorecards, pending deliveries, POD collection status, dealer-specific risks, and recovery actions for individual dealers.",
    "warehouse": "You are addressing a Warehouse Manager. Focus on: warehouse efficiency scores, backlog analysis, bottleneck identification, capacity utilization, and operational improvements.",
    "guest": "You are addressing a guest user. Provide helpful information about logistics operations and suggest specific queries for better assistance."
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
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {"response": response[:500], "confidence": 50, "requires_followup": True}


# ==========================================================
# AI COST TRACKER (Improvement 7)
# ==========================================================

class AICostTracker:
    """Track AI usage costs"""
    
    COST_PER_M_TOKEN = {
        "deepseek-chat": 0.14,
        "deepseek-chat-output": 0.28,
        "gpt-4-turbo": 10.00,
        "gpt-3.5-turbo": 0.50,
    }
    
    def __init__(self):
        self.total_cost = Decimal('0')
        self.total_tokens = 0
        self.usage_by_provider = {}
        self.requests_log = []
    
    def calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        input_cost = (prompt_tokens / 1_000_000) * self.COST_PER_M_TOKEN.get(model, 0.50)
        output_cost = (completion_tokens / 1_000_000) * self.COST_PER_M_TOKEN.get(f"{model}-output", 0.50)
        return Decimal(str(input_cost + output_cost))
    
    def track_usage(self, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: int):
        cost = self.calculate_cost(model, prompt_tokens, completion_tokens)
        self.total_cost += cost
        self.total_tokens += prompt_tokens + completion_tokens
        
        if model not in self.usage_by_provider:
            self.usage_by_provider[model] = {"requests": 0, "tokens": 0, "cost": Decimal('0'), "total_latency_ms": 0}
        
        self.usage_by_provider[model]["requests"] += 1
        self.usage_by_provider[model]["tokens"] += prompt_tokens + completion_tokens
        self.usage_by_provider[model]["cost"] += cost
        self.usage_by_provider[model]["total_latency_ms"] += latency_ms
        
        self.requests_log.append({
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost": float(cost),
            "latency_ms": latency_ms,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Keep only last 1000 requests
        if len(self.requests_log) > 1000:
            self.requests_log = self.requests_log[-1000:]
    
    def get_summary(self) -> Dict:
        return {
            "total_cost": float(self.total_cost),
            "total_cost_formatted": f"${float(self.total_cost):.4f}",
            "total_tokens": self.total_tokens,
            "by_provider": {
                provider: {
                    "requests": data["requests"],
                    "tokens": data["tokens"],
                    "cost": float(data["cost"]),
                    "avg_latency_ms": data["total_latency_ms"] / data["requests"] if data["requests"] > 0 else 0
                }
                for provider, data in self.usage_by_provider.items()
            }
        }


# ==========================================================
# AI PROVIDER SERVICE (ENTERPRISE v3.0)
# ==========================================================

class AIProviderService:
    """
    Enterprise AI Provider Service v3.0
    
    Features:
    - DeepSeek as PRIMARY provider (Improvement 1)
    - OpenAI as fallback
    - Rule-based as final fallback
    - Comprehensive logging (Improvement 2)
    - Dedicated logistics analysis functions (Improvement 3)
    - Role-based responses (Improvement 4)
    - Structured JSON responses (Improvement 5)
    - Prompt templates (Improvement 6)
    - Cost tracking (Improvement 7)
    - Context-aware follow-ups (Improvement 8)
    """
    
    def __init__(self, db: Session = None):
        self.db = db
        self.cache = AICacheManager(ttl_seconds=300)
        self.cost_tracker = AICostTracker()
        self.retry_count = 3
        self.retry_delay = 1
        
        # PRIMARY: DeepSeek (Improvement 1)
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
                logger.info("✅ DeepSeek client initialized (PRIMARY PROVIDER)")
                self.deepseek_status = ProviderStatus.ONLINE
            except Exception as e:
                logger.error(f"❌ DeepSeek initialization failed: {e}")
                self.deepseek_status = ProviderStatus.OFFLINE
        
        # FALLBACK: OpenAI
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
                logger.info(f"✅ OpenAI client initialized (FALLBACK PROVIDER, model: {self.openai_model})")
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
        logger.info("🤖 AI PROVIDER SERVICE v3.0 INITIALIZED")
        logger.info(f"PRIMARY: DeepSeek = {self.deepseek_status.value}")
        logger.info(f"FALLBACK: OpenAI = {self.openai_status.value} (model: {self.openai_model})")
        logger.info(f"Overall Available: {self.is_available}")
        logger.info(f"Cache TTL: {self.cache.ttl}s")
        logger.info(f"Retry Count: {self.retry_count}")
        logger.info("Structured JSON Responses: ENABLED")
        logger.info("Cost Tracking: ENABLED")
        logger.info("Role-Based Responses: ENABLED")
        logger.info("=" * 60)
    
    # ==========================================================
    # DEDICATED LOGISTICS ANALYSIS FUNCTIONS (Improvement 3)
    # ==========================================================
    
    def generate_logistics_analysis(
        self,
        analysis_type: str,
        logistics_data: Dict[str, Any],
        user_phone: str = None,
        user_role: str = "manager",
        conversation_context: Dict = None
    ) -> Dict[str, Any]:
        """
        Generate logistics analysis using appropriate template
        
        Supported types: dealer, warehouse, city, executive, forecast, root_cause, recommendation
        """
        # Get role-specific context
        role_context = ROLE_CONTEXTS.get(user_role, ROLE_CONTEXTS["manager"])
        
        # Select appropriate prompt template
        if analysis_type == "dealer":
            prompt = dealer_analysis_prompt(logistics_data, "Analyze this dealer's performance.", role_context)
        elif analysis_type == "warehouse":
            prompt = warehouse_analysis_prompt(logistics_data, "Analyze this warehouse's performance.", role_context)
        elif analysis_type == "city":
            prompt = city_analysis_prompt(logistics_data, "Analyze this city's logistics performance.", role_context)
        elif analysis_type == "executive":
            prompt = executive_prompt(logistics_data, "Provide executive summary.", role_context)
        elif analysis_type == "forecast":
            prompt = forecast_prompt(logistics_data, "Provide forecast analysis.", role_context)
        elif analysis_type == "root_cause":
            prompt = root_cause_prompt(logistics_data, "Perform root cause analysis.", role_context)
        elif analysis_type == "recommendation":
            prompt = recommendation_prompt(logistics_data, "Provide actionable recommendations.", role_context)
        else:
            prompt = f"Analyze this logistics data: {json.dumps(logistics_data, default=str)}"
        
        # Add conversation context for follow-ups (Improvement 8)
        if conversation_context:
            prompt += f"\n\nCONVERSATION CONTEXT: {json.dumps(conversation_context)}"
        
        # Call AI
        result = self.answer_question(
            question=prompt,
            context=logistics_data,
            structured=True,
            user_phone=user_phone,
            template=None,  # Using our custom prompt
            require_json=True
        )
        
        return result
    
    def generate_dealer_analysis(self, dealer_data: Dict, user_role: str = "manager", user_phone: str = None) -> Dict:
        """Generate dealer analysis"""
        return self.generate_logistics_analysis("dealer", dealer_data, user_phone, user_role)
    
    def generate_warehouse_analysis(self, warehouse_data: Dict, user_role: str = "manager", user_phone: str = None) -> Dict:
        """Generate warehouse analysis"""
        return self.generate_logistics_analysis("warehouse", warehouse_data, user_phone, user_role)
    
    def generate_city_analysis(self, city_data: Dict, user_role: str = "manager", user_phone: str = None) -> Dict:
        """Generate city analysis"""
        return self.generate_logistics_analysis("city", city_data, user_phone, user_role)
    
    def generate_executive_summary_enhanced(self, metrics: Dict, user_role: str = "ceo", user_phone: str = None) -> Dict:
        """Generate enhanced executive summary"""
        return self.generate_logistics_analysis("executive", metrics, user_phone, user_role)
    
    def generate_forecast_analysis(self, forecast_data: Dict, user_role: str = "manager", user_phone: str = None) -> Dict:
        """Generate forecast analysis"""
        return self.generate_logistics_analysis("forecast", forecast_data, user_phone, user_role)
    
    def generate_root_cause_analysis(self, root_cause_data: Dict, user_role: str = "manager", user_phone: str = None) -> Dict:
        """Generate root cause analysis"""
        return self.generate_logistics_analysis("root_cause", root_cause_data, user_phone, user_role)
    
    def generate_recommendations_enhanced(self, recommendation_data: Dict, user_role: str = "manager", user_phone: str = None) -> Dict:
        """Generate enhanced recommendations"""
        return self.generate_logistics_analysis("recommendation", recommendation_data, user_phone, user_role)
    
    # ==========================================================
    # WHAT SHOULD I FOCUS ON TODAY? (Improvement 10)
    # ==========================================================
    
    def generate_executive_focus(self, metrics: Dict, user_phone: str = None) -> Dict[str, Any]:
        """Generate executive focus for 'What should I focus on today?'"""
        role_context = ROLE_CONTEXTS["ceo"]
        
        prompt = f"""
You are a Logistics AI Advisor helping a CEO prioritize their day.

ROLE CONTEXT: {role_context}

CURRENT METRICS:
- Network Health: {metrics.get('network_health', 0)}/100
- Revenue at Risk: Rs {metrics.get('revenue_at_risk', 0):,.2f}
- Inventory at Risk: {metrics.get('inventory_at_risk', 0):,.0f} units
- Pending DNs: {metrics.get('pending_dns', 0)}
- POD Pending: {metrics.get('pod_pending', 0)}
- Top Risk Dealer: {metrics.get('top_risk_dealer', 'Unknown')}
- Top Risk City: {metrics.get('top_risk_city', 'Unknown')}

QUESTION: What should I focus on today?

Return a VALID JSON object:

{{
    "summary": "One sentence focus recommendation",
    "top_priority": {{
        "action": "single most important action today",
        "impact": "expected financial/operational impact",
        "timeline": "today or this week"
    }},
    "secondary_focus": {{
        "action": "second priority",
        "impact": "expected impact"
    }},
    "delegation": "What can be delegated to the team",
    "expected_outcome": "What success looks like after taking action"
}}
"""
        return self.answer_question(
            question=prompt,
            context=metrics,
            structured=True,
            user_phone=user_phone,
            require_json=True
        )
    
    # ==========================================================
    # CORE AI METHOD
    # ==========================================================
    
    def answer_question(
        self,
        question: str,
        context: Dict[str, Any] = None,
        structured: bool = True,
        user_phone: str = None,
        template: str = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        require_json: bool = True
    ) -> Dict[str, Any]:
        """
        Answer a question using AI with DeepSeek as primary (Improvement 1 & 2)
        """
        start_time = time.time()
        
        # Log incoming request (Improvement 2)
        logger.info(f"🚀 AI REQUEST - Provider: DeepSeek (primary), User: {user_phone}, Question: {question[:100]}...")
        
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
        
        # Build prompt
        prompt = self._build_prompt(question, context, template)
        
        # Check cache
        cache_key = self.cache.get_cache_key(
            prompt, "deepseek",
            context_hash=str(hash(str(context))),
            user_role=context.get("user_role", "guest") if context else "guest"
        )
        cached_response = self.cache.get(cache_key)
        if cached_response:
            cached_content, cached_metadata = cached_response
            logger.info(f"✅ AI RESPONSE - Cache hit for: {question[:50]}...")
            return {
                "success": True,
                "content": cached_content,
                "structured_data": AISafetyLayer.extract_json_from_response(cached_content) if require_json else None,
                "confidence": cached_metadata.get("confidence", 85),
                "provider_used": "cache",
                "processing_time_ms": 0,
                "cached": True
            }
        
        # PRIMARY: Try DeepSeek first (Improvement 1)
        response = None
        provider_used = None
        latency_ms = 0
        
        if self.deepseek_status == ProviderStatus.ONLINE:
            logger.info("🚀 Calling DeepSeek API...")
            call_start = time.time()
            response = self._call_deepseek(prompt, max_tokens, temperature)
            latency_ms = int((time.time() - call_start) * 1000)
            
            if response.get("success"):
                provider_used = "deepseek"
                logger.info(f"✅ DeepSeek Response Received - Latency: {latency_ms}ms, Tokens: {response.get('usage', {}).get('total_tokens', 0)}")
                self.cost_tracker.track_usage(
                    "deepseek-chat",
                    response.get("usage", {}).get("prompt_tokens", 0),
                    response.get("usage", {}).get("completion_tokens", 0),
                    latency_ms
                )
            else:
                logger.warning(f"⚠️ DeepSeek failed: {response.get('error')} - Falling back to OpenAI")
        
        # FALLBACK 1: OpenAI
        if not response or not response.get("success"):
            if self.openai_status == ProviderStatus.ONLINE:
                logger.warning("⚠️ Falling back to OpenAI...")
                call_start = time.time()
                response = self._call_openai(prompt, max_tokens, temperature)
                latency_ms = int((time.time() - call_start) * 1000)
                
                if response.get("success"):
                    provider_used = "openai"
                    logger.info(f"✅ OpenAI Response Received - Latency: {latency_ms}ms")
                    self.cost_tracker.track_usage(
                        self.openai_model,
                        response.get("usage", {}).get("prompt_tokens", 0),
                        response.get("usage", {}).get("completion_tokens", 0),
                        latency_ms
                    )
                else:
                    logger.error(f"❌ OpenAI also failed: {response.get('error')}")
        
        # FALLBACK 2: Rule-based
        if not response or not response.get("success"):
            logger.error("❌ Both DeepSeek and OpenAI failed - Using rule-based fallback")
            response = self._call_fallback(question)
            provider_used = "fallback"
        
        # Process response
        content = response.get("content", "")
        content = AISafetyLayer.sanitize_response(content)
        
        # Extract structured data
        structured_data = None
        confidence = 50
        
        if require_json:
            structured_data = AISafetyLayer.extract_json_from_response(content)
            confidence = self._calculate_confidence(structured_data, provider_used, response.get("usage", {}))
        
        # Cache successful response
        if response.get("success") and provider_used in ["deepseek", "openai"]:
            self.cache.set(cache_key, content, {"confidence": confidence})
        
        processing_time = int((time.time() - start_time) * 1000)
        
        # Log final result (Improvement 2)
        logger.info(f"✅ AI REQUEST COMPLETE - Provider: {provider_used}, Success: {response.get('success')}, Confidence: {confidence}%, Total Time: {processing_time}ms")
        
        # Log to database
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
            "latency_ms": latency_ms,
            "cached": False
        }
        
        return result
    
    def _calculate_confidence(self, structured_data: Optional[Dict], provider: str, usage: Dict) -> int:
        """Calculate confidence score"""
        confidence = 70
        
        if not structured_data:
            return 50
        
        if provider == "deepseek":
            confidence += 10
        elif provider == "openai":
            confidence += 5
        
        if usage.get("total_tokens", 0) > 500:
            confidence += 10
        
        if structured_data.get("recommendations"):
            confidence += 5
        if structured_data.get("risk_level"):
            confidence += 5
        
        return min(100, confidence)
    
    def _build_prompt(self, question: str, context: Dict = None, template: str = None) -> str:
        """Build prompt using context"""
        if template:
            return template.format(context=json.dumps(context, default=str) if context else "No context", question=question)
        
        context_str = json.dumps(context, indent=2, default=str, ensure_ascii=False) if context else "No additional context"
        if len(context_str) > 3000:
            context_str = context_str[:3000] + "..."
        
        return f"""
You are a Logistics AI Advisor for a major distribution company.

CONTEXT DATA:
{context_str}

QUESTION: {question}

Return a VALID JSON response when appropriate. Be concise, professional, and data-driven.
"""
    
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
                
                logger.debug(f"DeepSeek success: {len(content)} chars")
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
                
                logger.debug(f"OpenAI success: {len(content)} chars")
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
    
    def _log_usage(self, user_phone: str, question: str, response: str, provider: str, success: bool, processing_time_ms: int, tokens: int = 0, confidence: int = 0):
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
    
    def get_cost_summary(self) -> Dict[str, Any]:
        """Get AI usage cost summary"""
        return self.cost_tracker.get_summary()


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
            logger.info("✅ AI PROVIDER SERVICE v3.0 LOADED SUCCESSFULLY")
            logger.info(f"   PRIMARY: DeepSeek")
            logger.info(f"   FALLBACK: OpenAI")
            cost_summary = ai_provider_service.get_cost_summary()
            logger.info(f"   Total Cost to Date: {cost_summary.get('total_cost_formatted', '$0')}")
        else:
            logger.error("❌ AI PROVIDER SERVICE FAILED TO LOAD - No AI providers available")
        
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
    logger.info("AI Provider Service v3.0 auto-initialized (no DB)")
except Exception as e:
    logger.error(f"Auto-initialization failed: {e}")
    ai_provider_service = None
