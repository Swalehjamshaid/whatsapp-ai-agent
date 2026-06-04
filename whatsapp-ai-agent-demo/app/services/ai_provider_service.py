# ==========================================================
# FILE: app/services/ai_provider_service.py (CORRECTED)
# ==========================================================
# COMPLETE VERSION WITH PROVIDER FACTORY, CACHING, TOKEN TRACKING, RAG
# FIXED: Uses AIResponseLog instead of AIQueryLog

from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta
import hashlib
import json
from enum import Enum

from openai import OpenAI
from anthropic import Anthropic
from loguru import logger
import redis
from sqlalchemy.orm import Session

from app.core.config import settings


# ======================================================
# PROVIDER TYPES
# ======================================================

class AIProviderType(str, Enum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    CLAUDE = "claude"
    GEMINI = "gemini"
    OLLAMA = "ollama"


# ======================================================
# AI PROVIDER FACTORY
# ======================================================

class AIProviderFactory:
    """Factory pattern for multiple AI providers"""
    
    @staticmethod
    def get_provider(provider_name: str = None):
        """Get AI provider client based on configuration"""
        
        provider = provider_name or getattr(settings, "AI_PROVIDER", "deepseek")
        
        if provider == AIProviderType.DEEPSEEK:
            return DeepSeekProvider()
        elif provider == AIProviderType.OPENAI:
            return OpenAIProvider()
        elif provider == AIProviderType.CLAUDE:
            return ClaudeProvider()
        elif provider == AIProviderType.GEMINI:
            return GeminiProvider()
        elif provider == AIProviderType.OLLAMA:
            return OllamaProvider()
        else:
            logger.warning(f"Unknown provider {provider}, falling back to DeepSeek")
            return DeepSeekProvider()


# ======================================================
# BASE PROVIDER ABSTRACT CLASS
# ======================================================

class BaseAIProvider:
    """Abstract base class for all AI providers"""
    
    def __init__(self):
        self.provider_name = None
        self.model = None
        self.client = None
    
    def generate(self, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        """Generate response from AI provider"""
        raise NotImplementedError
    
    def get_token_count(self, response) -> int:
        """Get token count from response"""
        raise NotImplementedError
    
    def get_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost based on provider pricing"""
        raise NotImplementedError


# ======================================================
# DEEPSEEK PROVIDER
# ======================================================

class DeepSeekProvider(BaseAIProvider):
    
    def __init__(self):
        super().__init__()
        self.provider_name = "deepseek"
        self.model = getattr(settings, "DEEPSEEK_MODEL", "deepseek-chat")
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL
        )
    
    def generate(self, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.3),
                max_tokens=kwargs.get("max_tokens", 2500)
            )
            
            return {
                "success": True,
                "content": response.choices[0].message.content,
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "provider": self.provider_name,
                "model": self.model,
                "raw_response": response
            }
        except Exception as e:
            logger.error(f"DeepSeek error: {str(e)}")
            return {"success": False, "error": str(e), "provider": self.provider_name}
    
    def get_token_count(self, response) -> int:
        return getattr(response.usage, "total_tokens", 0)
    
    def get_cost(self, input_tokens: int, output_tokens: int) -> float:
        # DeepSeek pricing: $0.14 per 1M input, $0.28 per 1M output
        input_cost = (input_tokens / 1_000_000) * 0.14
        output_cost = (output_tokens / 1_000_000) * 0.28
        return round(input_cost + output_cost, 6)


# ======================================================
# OPENAI PROVIDER
# ======================================================

class OpenAIProvider(BaseAIProvider):
    
    def __init__(self):
        super().__init__()
        self.provider_name = "openai"
        self.model = getattr(settings, "OPENAI_MODEL", "gpt-3.5-turbo")
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    def generate(self, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.3),
                max_tokens=kwargs.get("max_tokens", 2500)
            )
            
            return {
                "success": True,
                "content": response.choices[0].message.content,
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "provider": self.provider_name,
                "model": self.model,
                "raw_response": response
            }
        except Exception as e:
            logger.error(f"OpenAI error: {str(e)}")
            return {"success": False, "error": str(e), "provider": self.provider_name}
    
    def get_token_count(self, response) -> int:
        return getattr(response.usage, "total_tokens", 0)
    
    def get_cost(self, input_tokens: int, output_tokens: int) -> float:
        # GPT-3.5 Turbo pricing
        input_cost = (input_tokens / 1_000_000) * 0.50
        output_cost = (output_tokens / 1_000_000) * 1.50
        return round(input_cost + output_cost, 6)


# ======================================================
# CLAUDE PROVIDER
# ======================================================

class ClaudeProvider(BaseAIProvider):
    
    def __init__(self):
        super().__init__()
        self.provider_name = "claude"
        self.model = getattr(settings, "CLAUDE_MODEL", "claude-3-sonnet-20240229")
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    
    def generate(self, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        try:
            # Convert OpenAI format to Claude format
            system_msg = None
            user_messages = []
            
            for msg in messages:
                if msg["role"] == "system":
                    system_msg = msg["content"]
                else:
                    user_messages.append(msg)
            
            response = self.client.messages.create(
                model=self.model,
                system=system_msg,
                messages=user_messages,
                temperature=kwargs.get("temperature", 0.3),
                max_tokens=kwargs.get("max_tokens", 2500)
            )
            
            return {
                "success": True,
                "content": response.content[0].text,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                "provider": self.provider_name,
                "model": self.model,
                "raw_response": response
            }
        except Exception as e:
            logger.error(f"Claude error: {str(e)}")
            return {"success": False, "error": str(e), "provider": self.provider_name}
    
    def get_token_count(self, response) -> int:
        return getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0)
    
    def get_cost(self, input_tokens: int, output_tokens: int) -> float:
        # Claude 3 Sonnet pricing
        input_cost = (input_tokens / 1_000_000) * 3.00
        output_cost = (output_tokens / 1_000_000) * 15.00
        return round(input_cost + output_cost, 6)


# ======================================================
# GEMINI PROVIDER (Placeholder)
# ======================================================

class GeminiProvider(BaseAIProvider):
    
    def __init__(self):
        super().__init__()
        self.provider_name = "gemini"
        self.model = getattr(settings, "GEMINI_MODEL", "gemini-pro")
    
    def generate(self, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        return {
            "success": False,
            "error": "Gemini provider not fully implemented yet",
            "provider": self.provider_name
        }
    
    def get_token_count(self, response) -> int:
        return 0
    
    def get_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0


# ======================================================
# OLLAMA PROVIDER (Local LLM - Placeholder)
# ======================================================

class OllamaProvider(BaseAIProvider):
    
    def __init__(self):
        super().__init__()
        self.provider_name = "ollama"
        self.model = getattr(settings, "OLLAMA_MODEL", "llama2")
    
    def generate(self, messages: List[Dict], **kwargs) -> Dict[str, Any]:
        return {
            "success": False,
            "error": "Ollama provider not fully implemented yet",
            "provider": self.provider_name
        }
    
    def get_token_count(self, response) -> int:
        return 0
    
    def get_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0


# ======================================================
# RESPONSE CACHE
# ======================================================

class AIResponseCache:
    """Redis-based cache for AI responses"""
    
    def __init__(self):
        self.redis_client = None
        self.cache_ttl = getattr(settings, "AI_RESPONSE_CACHE_TTL", 300)
        self.cache_enabled = getattr(settings, "CACHE_AI_RESPONSES", True)
        
        if self.cache_enabled:
            try:
                self.redis_client = redis.Redis(
                    host=getattr(settings, "REDIS_HOST", "localhost"),
                    port=getattr(settings, "REDIS_PORT", 6379),
                    db=getattr(settings, "REDIS_DB", 0),
                    decode_responses=True
                )
                self.redis_client.ping()
                logger.info("Redis cache connected successfully")
            except Exception as e:
                logger.warning(f"Redis not available: {e}. Caching disabled.")
                self.redis_client = None
                self.cache_enabled = False
    
    def get_cache_key(self, question: str, context_hash: str = None) -> str:
        """Generate cache key from question and context"""
        key_data = question.lower().strip()
        if context_hash:
            key_data += f":{context_hash}"
        return f"ai_response:{hashlib.md5(key_data.encode()).hexdigest()}"
    
    def get(self, key: str) -> Optional[Dict]:
        """Get cached response"""
        if not self.cache_enabled or not self.redis_client:
            return None
        
        try:
            cached = self.redis_client.get(key)
            if cached:
                logger.info(f"Cache hit for key: {key[:20]}...")
                return json.loads(cached)
        except Exception as e:
            logger.error(f"Cache read error: {e}")
        
        return None
    
    def set(self, key: str, response: Dict):
        """Cache response"""
        if not self.cache_enabled or not self.redis_client:
            return
        
        try:
            self.redis_client.setex(
                key,
                self.cache_ttl,
                json.dumps(response, default=str)
            )
            logger.info(f"Cached response for key: {key[:20]}...")
        except Exception as e:
            logger.error(f"Cache write error: {e}")
    
    def clear(self, pattern: str = None):
        """Clear cache entries"""
        if not self.redis_client:
            return
        
        try:
            if pattern:
                keys = self.redis_client.keys(f"ai_response:{pattern}*")
                if keys:
                    self.redis_client.delete(*keys)
            else:
                keys = self.redis_client.keys("ai_response:*")
                if keys:
                    self.redis_client.delete(*keys)
            logger.info(f"Cleared {len(keys)} cache entries")
        except Exception as e:
            logger.error(f"Cache clear error: {e}")


# ======================================================
# TOKEN TRACKING (FIXED - Uses AIResponseLog)
# ======================================================

class TokenTracker:
    """Track AI usage for cost management - uses AIResponseLog model"""
    
    def __init__(self, db: Session = None):
        self.db = db
    
    def log_query(
        self,
        question: str,
        context: Dict,
        response: Dict,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        cache_hit: bool = False,
        conversation_id: int = None,
        user_phone: str = None
    ):
        """Log AI query to database using AIResponseLog model"""
        if not self.db:
            return
        
        try:
            # Import here to avoid circular imports
            from app.models import AIResponseLog
            
            log_entry = AIResponseLog(
                conversation_id=conversation_id,  # Can be None
                prompt=question[:500],  # Using 'prompt' field
                ai_response=response.get("content", "")[:2000],  # Using 'ai_response' field
                model_name=f"{provider}/{model}",
                success=response.get("success", False),
                created_at=datetime.utcnow()
            )
            self.db.add(log_entry)
            self.db.commit()
            logger.info(f"Logged AI query: {input_tokens + output_tokens} tokens")
        except Exception as e:
            logger.error(f"Failed to log query: {e}")
            if self.db:
                self.db.rollback()


# ======================================================
# MAIN AI SERVICE (FIXED - Uses AIResponseLog)
# ======================================================

class DeepSeekService:
    """
    Unified AI Service with provider abstraction, caching, and token tracking
    """
    
    def __init__(self, db: Session = None):
        self.provider = None
        self.cache = AIResponseCache()
        self.token_tracker = TokenTracker(db)
        self.db = db
        self._init_provider()
    
    def _init_provider(self):
        """Initialize AI provider with fallback support"""
        primary_provider = getattr(settings, "AI_PROVIDER", "deepseek")
        self.provider = AIProviderFactory.get_provider(primary_provider)
        self.fallback_provider_name = getattr(settings, "AI_FALLBACK_PROVIDER", None)
        self.fallback_provider = None
        
        if self.fallback_provider_name:
            self.fallback_provider = AIProviderFactory.get_provider(self.fallback_provider_name)
        
        logger.info(f"AI Provider initialized: {self.provider.provider_name}")
        if self.fallback_provider:
            logger.info(f"Fallback provider: {self.fallback_provider.provider_name}")
    
    def _switch_provider(self, provider_name: str):
        """Switch to different provider on failure"""
        logger.warning(f"Switching from {self.provider.provider_name} to {provider_name}")
        self.provider = AIProviderFactory.get_provider(provider_name)
    
    # ======================================================
    # GENERATE AI RESPONSE (Unified Method)
    # ======================================================
    
    def generate_ai_response(
        self,
        prompt: str,
        context: Dict[str, Any] = None,
        system_prompt: str = None,
        temperature: float = 0.3,
        max_tokens: int = 2500,
        force_refresh: bool = False,
        structured: bool = False,
        user_phone: str = None,
        conversation_id: int = None
    ) -> Dict[str, Any]:
        """
        Unified AI response generation with caching, fallback, and structured output.
        """
        
        # Prepare messages
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        else:
            messages.append({
                "role": "system",
                "content": getattr(settings, "DEEPSEEK_SYSTEM_PROMPT", 
                    "You are a professional logistics operations manager. Provide concise, actionable insights.")
            })
        
        # Add context to user message
        user_content = prompt
        if context:
            user_content = f"Context:\n{json.dumps(context, indent=2, default=str)}\n\nQuestion:\n{prompt}"
        
        messages.append({"role": "user", "content": user_content})
        
        # Generate cache key
        context_hash = hashlib.md5(json.dumps(context or {}, sort_keys=True, default=str).encode()).hexdigest()[:16] if context else "no_context"
        cache_key = self.cache.get_cache_key(prompt, context_hash)
        
        # Check cache
        if not force_refresh:
            cached_response = self.cache.get(cache_key)
            if cached_response:
                cached_response["cache_hit"] = True
                return cached_response
        
        # Try primary provider
        response = self._try_provider(self.provider, messages, temperature, max_tokens)
        
        # Try fallback if primary failed
        if not response["success"] and self.fallback_provider:
            logger.warning(f"Primary provider failed, trying fallback: {self.fallback_provider.provider_name}")
            response = self._try_provider(self.fallback_provider, messages, temperature, max_tokens)
            if response["success"]:
                self._switch_provider(self.fallback_provider.provider_name)
        
        # If all AI fails, return rule-based response
        if not response["success"]:
            if getattr(settings, "AI_FALLBACK_TO_RULE_BASED", True):
                return self._generate_rule_based_response(prompt, context)
            else:
                return {
                    "success": False,
                    "error": "All AI providers failed",
                    "content": "Unable to generate response at this time. Please try again later."
                }
        
        # Track tokens and cost (using fixed TokenTracker)
        cost = self.provider.get_cost(response.get("input_tokens", 0), response.get("output_tokens", 0))
        self.token_tracker.log_query(
            question=prompt,
            context=context or {},
            response=response,
            provider=response["provider"],
            model=response["model"],
            input_tokens=response.get("input_tokens", 0),
            output_tokens=response.get("output_tokens", 0),
            cost=cost,
            cache_hit=False,
            conversation_id=conversation_id,
            user_phone=user_phone
        )
        
        # Add cost to response
        response["cost"] = cost
        response["cache_hit"] = False
        
        # Structure response if requested
        if structured:
            response = self._structure_response(response["content"])
        
        # Cache the response
        self.cache.set(cache_key, response)
        
        return response
    
    def _try_provider(self, provider, messages, temperature, max_tokens) -> Dict:
        """Try a specific provider with error handling"""
        try:
            return provider.generate(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
        except Exception as e:
            logger.error(f"Provider {provider.provider_name} failed: {e}")
            return {"success": False, "error": str(e), "provider": provider.provider_name}
    
    def _structure_response(self, content: str) -> Dict[str, Any]:
        """Convert free text to structured response"""
        try:
            if content.strip().startswith("{"):
                structured = json.loads(content)
                return {
                    "success": True,
                    "structured": True,
                    "summary": structured.get("summary", ""),
                    "risks": structured.get("risks", []),
                    "recommendations": structured.get("recommendations", []),
                    "actions": structured.get("actions", []),
                    "metrics": structured.get("metrics", {}),
                    "raw_content": content
                }
        except:
            pass
        
        return {
            "success": True,
            "structured": False,
            "summary": content[:500],
            "risks": [],
            "recommendations": [],
            "actions": [],
            "raw_content": content
        }
    
    def _generate_rule_based_response(self, prompt: str, context: Dict) -> Dict:
        """Generate rule-based response when AI fails"""
        return {
            "success": True,
            "content": "I'm currently using rule-based responses. For full AI analysis, please check your API configuration.",
            "provider": "rule_based",
            "model": "fallback",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0,
            "cache_hit": False,
            "rule_based": True
        }
    
    # ======================================================
    # SPECIALIZED METHODS
    # ======================================================
    
    def analyze_dealer(self, dealer_context: Dict[str, Any], structured: bool = True, user_phone: str = None) -> Dict:
        """Analyze dealer performance"""
        prompt = """
Analyze this dealer and provide:
1. Executive Summary
2. Key Risks
3. Opportunities
4. Recommendations
5. Priority Actions

Return as JSON with keys: summary, risks, recommendations, actions, metrics
"""
        return self.generate_ai_response(prompt, dealer_context, structured=structured, user_phone=user_phone)
    
    def analyze_executive(self, executive_context: Dict[str, Any], structured: bool = True, user_phone: str = None) -> Dict:
        """Analyze overall logistics operations"""
        prompt = """
Analyze this logistics operation and provide:
1. Executive Summary
2. Top Risks
3. Top Performing Areas
4. Immediate Attention Items
5. Recommended Actions

Return as JSON with keys: summary, risks, recommendations, actions, metrics
"""
        return self.generate_ai_response(prompt, executive_context, structured=structured, user_phone=user_phone)
    
    def analyze_product(self, product_context: Dict[str, Any], structured: bool = True, user_phone: str = None) -> Dict:
        """Analyze product performance"""
        prompt = """
Analyze this product and provide:
1. Product Performance Summary
2. Demand Trends
3. Risk Areas
4. Recommendations

Return as JSON with keys: summary, risks, recommendations, actions, metrics
"""
        return self.generate_ai_response(prompt, product_context, structured=structured, user_phone=user_phone)
    
    def analyze_warehouse(self, warehouse_context: Dict[str, Any], structured: bool = True, user_phone: str = None) -> Dict:
        """Analyze warehouse operations"""
        prompt = """
Analyze this warehouse and provide:
1. Performance Summary
2. Bottlenecks
3. Risks
4. Recommendations

Return as JSON with keys: summary, risks, recommendations, actions, metrics
"""
        return self.generate_ai_response(prompt, warehouse_context, structured=structured, user_phone=user_phone)
    
    def analyze_city(self, city_context: Dict[str, Any], structured: bool = True, user_phone: str = None) -> Dict:
        """Analyze city logistics"""
        prompt = """
Analyze this city and provide:
1. Performance Summary
2. Risks
3. Delivery Challenges
4. Recommendations

Return as JSON with keys: summary, risks, recommendations, actions, metrics
"""
        return self.generate_ai_response(prompt, city_context, structured=structured, user_phone=user_phone)
    
    def analyze_dn(self, dn_context: Dict[str, Any], structured: bool = True, user_phone: str = None) -> Dict:
        """Analyze specific delivery note"""
        prompt = """
Analyze this Delivery Note and provide:
1. DN Summary
2. Product Summary
3. Delivery Status
4. Risks
5. Recommendations

Return as JSON with keys: summary, risks, recommendations, actions, metrics
"""
        return self.generate_ai_response(prompt, dn_context, structured=structured, user_phone=user_phone)
    
    def compare_entities(self, comparison_context: Dict[str, Any], structured: bool = True, user_phone: str = None) -> Dict:
        """Compare multiple entities"""
        prompt = """
Compare these entities and provide:
1. Comparison Summary
2. Strengths
3. Weaknesses
4. Winner
5. Recommendations

Return as JSON with keys: summary, risks, recommendations, actions, metrics
"""
        return self.generate_ai_response(prompt, comparison_context, structured=structured, user_phone=user_phone)
    
    def analyze_action_plan(self, action_plan: List[Dict], structured: bool = True, user_phone: str = None) -> Dict:
        """Analyze action plan"""
        prompt = f"""
Review this action plan and provide:
1. Overall Assessment
2. Highest Priority Items
3. Risks
4. Recommended Execution Sequence

Action Plan: {json.dumps(action_plan, indent=2)}

Return as JSON with keys: summary, risks, recommendations, actions, metrics
"""
        return self.generate_ai_response(prompt, structured=structured, user_phone=user_phone)
    
    def answer_question(self, question: str, context: Dict[str, Any] = None, structured: bool = False, user_phone: str = None) -> Dict:
        """Answer free-form logistics question"""
        return self.generate_ai_response(question, context or {}, structured=structured, user_phone=user_phone)
    
    def answer_question_simple(self, question: str, user_phone: str = None) -> Dict:
        """Simple question answering without complex context"""
        return self.generate_ai_response(question, {}, structured=False, user_phone=user_phone)
    
    # ======================================================
    # RAG LAYER (Placeholder)
    # ======================================================
    
    def answer_with_rag(self, question: str, db: Session, structured: bool = False, user_phone: str = None) -> Dict:
        """
        Enhanced RAG-based answer using vector search.
        TODO: Implement with FAISS and sentence-transformers
        """
        logger.info("RAG layer - to be implemented with FAISS and embeddings")
        return self.answer_question(question, {}, structured=structured, user_phone=user_phone)


# ======================================================
# SINGLETON INSTANCE
# ======================================================

deepseek_service = DeepSeekService()
ai_provider_service = deepseek_service  # Alias for backward compatibility
