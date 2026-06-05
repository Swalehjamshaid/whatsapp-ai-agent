# ==========================================================
# FILE: app/services/ai_provider_service.py (GROQ ONLY v6.0)
# ==========================================================

import os
import json
import time
import re
import hashlib
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session

from app.config import config
from app.models import AIResponseLog

# Groq import
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq not available. Install with: pip install groq")


# ==========================================================
# AI PROVIDER STATUS
# ==========================================================

class ProviderStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DISABLED = "disabled"


# ==========================================================
# ROBUST JSON EXTRACTOR
# ==========================================================

class RobustJSONExtractor:
    """Robust JSON extraction with multiple fallback strategies"""
    
    @staticmethod
    def extract(json_string: str) -> Dict[str, Any]:
        if not json_string:
            return {}
        
        # Strategy 1: Direct parse
        try:
            return json.loads(json_string)
        except json.JSONDecodeError:
            pass
        
        # Strategy 2: Find JSON object in text
        json_match = re.search(r'\{[\s\S]*\}', json_string)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # Strategy 3: Fix common JSON issues
        fixed = json_string
        fixed = re.sub(r',\s*}', '}', fixed)
        fixed = re.sub(r',\s*]', ']', fixed)
        fixed = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', fixed)
        fixed = fixed.replace("'", '"')
        
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        return {"response": json_string[:500], "confidence": 50}


# ==========================================================
# AI CACHE MANAGER
# ==========================================================

class AICacheManager:
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
    
    def get_cache_key(self, prompt: str, model: str, context_hash: str = "", user_role: str = "") -> str:
        content_hash = hashlib.md5(prompt[:500].encode()).hexdigest()
        return f"{model}:{context_hash}:{user_role}:{content_hash}"


# ==========================================================
# AI SAFETY LAYER
# ==========================================================

class AISafetyLayer:
    DANGEROUS_PATTERNS = [
        r"DROP\s+TABLE", r"DELETE\s+FROM", r"UPDATE\s+\w+\s+SET",
        r"INSERT\s+INTO", r"ALTER\s+TABLE", r"TRUNCATE",
        r"EXEC\s*\(", r"xp_cmdshell", r"UNION\s+SELECT",
        r"--", r";\s*DROP", r"'\s*OR\s+'1'='1",
    ]
    
    @classmethod
    def validate_prompt(cls, prompt: str) -> Tuple[bool, str]:
        prompt_upper = prompt.upper()
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, prompt_upper):
                logger.warning(f"Dangerous pattern detected: {pattern}")
                return False, f"Dangerous pattern blocked: {pattern}"
        return True, ""
    
    @classmethod
    def sanitize_response(cls, response: str) -> str:
        for pattern in cls.DANGEROUS_PATTERNS:
            response = re.sub(pattern, "[REDACTED]", response, flags=re.IGNORECASE)
        return response[:4000]


# ==========================================================
# AI COST TRACKER
# ==========================================================

class AICostTracker:
    def __init__(self):
        self.total_cost = Decimal('0')
        self.total_tokens = 0
        self.usage_by_provider = {}
    
    def track_usage(self, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: int):
        # Groq is free for now, just track tokens
        self.total_tokens += prompt_tokens + completion_tokens
        
        if model not in self.usage_by_provider:
            self.usage_by_provider[model] = {"requests": 0, "tokens": 0}
        
        self.usage_by_provider[model]["requests"] += 1
        self.usage_by_provider[model]["tokens"] += prompt_tokens + completion_tokens
    
    def get_summary(self) -> Dict:
        return {
            "total_cost_formatted": "$0.00 (Groq is free)",
            "total_tokens": self.total_tokens,
            "by_provider": self.usage_by_provider
        }


# ==========================================================
# AI PROVIDER SERVICE (GROQ ONLY)
# ==========================================================

class AIProviderService:
    """
    AI Provider Service - Groq Only
    No DeepSeek, No OpenAI fallback - Just Groq + Rule-based
    """
    
    def __init__(self, db: Session = None):
        self.db = db
        self.cache = AICacheManager(ttl_seconds=300)
        self.cost_tracker = AICostTracker()
        self.retry_count = 3
        self.retry_delay = 1
        self.groq_model = os.getenv("GROQ_MODEL", "qwen-qwq-32b")
        
        # Initialize Groq client (ONLY provider)
        self.groq_api_key = os.getenv("GROQ_API_KEY") or getattr(config, 'GROQ_API_KEY', None)
        self.groq_client = None
        self.groq_status = ProviderStatus.OFFLINE
        
        if self.groq_api_key and GROQ_AVAILABLE:
            try:
                self.groq_client = Groq(api_key=self.groq_api_key)
                self.groq_status = ProviderStatus.ONLINE
                logger.info(f"✅ Groq client initialized (model: {self.groq_model})")
            except Exception as e:
                logger.error(f"❌ Groq initialization failed: {e}")
        else:
            logger.warning("⚠️ Groq not configured")
        
        self.is_available = self.groq_status == ProviderStatus.ONLINE
        
        self._log_startup_status()
    
    def _log_startup_status(self):
        logger.info("=" * 60)
        logger.info("🤖 AI PROVIDER SERVICE v6.0 INITIALIZED")
        logger.info(f"PRIMARY: Groq = {self.groq_status.value} (model: {self.groq_model})")
        logger.info(f"DeepSeek = DISABLED")
        logger.info(f"OpenAI = DISABLED")
        logger.info(f"Overall Available: {self.is_available}")
        logger.info("=" * 60)
    
    # ==========================================================
    # CORE AI METHOD - Groq Only
    # ==========================================================
    
    def answer_question(
        self,
        question: str,
        context: Dict[str, Any] = None,
        structured: bool = True,
        user_phone: str = None,
        max_tokens: int = 2500,
        temperature: float = 0.3,
        require_json: bool = True
    ) -> Dict[str, Any]:
        """Answer using Groq only, fallback to rule-based"""
        start_time = time.time()
        
        logger.info(f"🚀 AI REQUEST - Provider: Groq, User: {user_phone}")
        
        # Validate prompt safety
        is_safe, error_msg = AISafetyLayer.validate_prompt(question)
        if not is_safe:
            return {
                "success": False,
                "content": "⚠️ Your request contains unsafe content.",
                "structured_data": None,
                "confidence": 0,
                "error": error_msg,
                "provider_used": "safety_layer",
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # Build full prompt
        full_prompt = self._build_prompt(question, context)
        
        # Check cache
        cache_key = self.cache.get_cache_key(
            full_prompt, "groq",
            context_hash=str(hash(str(context))),
            user_role=context.get("user_role", "guest") if context else "guest"
        )
        cached_response = self.cache.get(cache_key)
        if cached_response:
            cached_content, cached_metadata = cached_response
            logger.info(f"✅ Cache hit")
            return {
                "success": True,
                "content": cached_content,
                "structured_data": RobustJSONExtractor.extract(cached_content) if require_json else None,
                "confidence": cached_metadata.get("confidence", 85),
                "provider_used": "cache",
                "processing_time_ms": 0,
                "cached": True
            }
        
        # Try Groq
        response = None
        latency_ms = 0
        
        if self.groq_status == ProviderStatus.ONLINE:
            logger.info("🚀 CALLING GROQ...")
            call_start = time.time()
            response = self._call_groq(full_prompt, max_tokens, temperature)
            latency_ms = int((time.time() - call_start) * 1000)
            
            if response.get("success"):
                logger.info(f"✅ GROQ RESPONSE RECEIVED - Latency: {latency_ms}ms")
                self.cost_tracker.track_usage(
                    "groq",
                    response.get("usage", {}).get("prompt_tokens", 0),
                    response.get("usage", {}).get("completion_tokens", 0),
                    latency_ms
                )
            else:
                logger.warning(f"⚠️ GROQ FAILED: {response.get('error')}")
        
        # Fallback to rule-based if Groq fails
        if not response or not response.get("success"):
            logger.error("❌ GROQ FAILED - Using rule-based fallback")
            response = self._call_fallback(question)
        
        # Process response
        content = response.get("content", "")
        content = AISafetyLayer.sanitize_response(content)
        
        structured_data = None
        confidence = 50
        
        if require_json:
            structured_data = RobustJSONExtractor.extract(content)
            confidence = self._calculate_confidence(structured_data, response.get("usage", {}))
        
        # Cache successful response
        if response.get("success"):
            self.cache.set(cache_key, content, {"confidence": confidence})
        
        processing_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ AI REQUEST COMPLETE - Success: {response.get('success')}, Confidence: {confidence}%, Time: {processing_time}ms")
        
        return {
            "success": response.get("success", False),
            "content": content,
            "structured_data": structured_data if structured else None,
            "confidence": confidence,
            "provider_used": "groq" if response.get("success") else "fallback",
            "processing_time_ms": processing_time,
            "latency_ms": latency_ms,
            "cached": False
        }
    
    def _call_groq(self, prompt: str, max_tokens: int = 2500, temperature: float = 0.3) -> Dict[str, Any]:
        """Call Groq API"""
        if not self.groq_client:
            return {"success": False, "error": "Groq client not initialized"}
        
        for attempt in range(self.retry_count):
            try:
                response = self.groq_client.chat.completions.create(
                    model=self.groq_model,
                    messages=[
                        {"role": "system", "content": "You are a logistics AI advisor. Return ONLY valid JSON when requested. Be concise and data-driven."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                
                content = response.choices[0].message.content
                
                return {
                    "success": True,
                    "content": content,
                    "model": self.groq_model,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens if hasattr(response, 'usage') else 0,
                        "completion_tokens": response.usage.completion_tokens if hasattr(response, 'usage') else 0,
                        "total_tokens": response.usage.total_tokens if hasattr(response, 'usage') else 0
                    }
                }
                
            except Exception as e:
                logger.warning(f"Groq attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_count - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    return {"success": False, "error": str(e)}
        
        return {"success": False, "error": "Max retries exceeded"}
    
    def _call_fallback(self, question: str) -> Dict[str, Any]:
        """Rule-based fallback"""
        logger.info(f"Using fallback for: {question[:50]}")
        
        question_lower = question.lower()
        
        if any(word in question_lower for word in ["pending", "backlog"]):
            content = json.dumps({
                "response": "Check pending dashboard for real-time updates.",
                "confidence": 40
            })
        elif any(word in question_lower for word in ["risk", "critical"]):
            content = json.dumps({
                "response": "Review executive dashboard for risk assessment.",
                "confidence": 35
            })
        else:
            content = json.dumps({
                "response": "AI service temporarily unavailable. Please try again.",
                "confidence": 25
            })
        
        return {
            "success": True,
            "content": content,
            "model": "fallback",
            "fallback": True
        }
    
    def _build_prompt(self, question: str, context: Dict = None) -> str:
        """Build prompt using context"""
        context_str = json.dumps(context, indent=2, default=str, ensure_ascii=False) if context else "No additional context"
        if len(context_str) > 3000:
            context_str = context_str[:3000] + "..."
        
        return f"""
CONTEXT DATA:
{context_str}

QUESTION: {question}

Return a VALID JSON response. Be concise and data-driven.
"""
    
    def _calculate_confidence(self, structured_data: Optional[Dict], usage: Dict) -> int:
        confidence = 70
        if not structured_data:
            return 50
        if usage.get("total_tokens", 0) > 500:
            confidence += 10
        if structured_data.get("recommendations"):
            confidence += 5
        if structured_data.get("risk_level"):
            confidence += 5
        return min(100, confidence)
    
    def health_check(self) -> Dict[str, Any]:
        return {
            "status": "healthy" if self.is_available else "degraded",
            "groq_available": self.groq_status == ProviderStatus.ONLINE,
            "groq_model": self.groq_model
        }
    
    def get_cost_summary(self) -> Dict[str, Any]:
        return self.cost_tracker.get_summary()


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

ai_provider_service = None


def init_ai_provider_service(db: Session = None) -> AIProviderService:
    global ai_provider_service
    try:
        ai_provider_service = AIProviderService(db)
        return ai_provider_service
    except Exception as e:
        logger.error(f"AI Provider Service initialization error: {e}")
        ai_provider_service = None
        raise


def get_ai_provider_service() -> Optional[AIProviderService]:
    return ai_provider_service
