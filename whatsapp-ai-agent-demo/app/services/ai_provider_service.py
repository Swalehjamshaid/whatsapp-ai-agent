# ==========================================================
# FILE: app/services/ai_provider_service.py (GROQ ENTERPRISE v1.0)
# ==========================================================
# WhatsApp AI Provider - Using GROQ for fast responses
# ==========================================================

import json
import time
import re
import hashlib
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from functools import lru_cache
from enum import Enum
from dataclasses import dataclass

import requests
from loguru import logger
from sqlalchemy.orm import Session

from app.config import config
from app.models import AIResponseLog

# Try to import Groq
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq not installed. Run: pip install groq")


# ==========================================================
# AI PROVIDER STATUS
# ==========================================================

class ProviderStatus(Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


# ==========================================================
# ROLE-BASED CONTEXTS
# ==========================================================

ROLE_CONTEXTS = {
    "ceo": "You are a Logistics AI Advisor for a WhatsApp chat. The user is a CEO. Provide concise, strategic insights about network health, revenue at risk, and top priorities. Use simple formatting with emojis.",
    "manager": "You are a Logistics AI Advisor for a WhatsApp chat. The user is a Logistics Manager. Focus on operational metrics, pending DNs, POD compliance, and actionable insights. Keep responses clear and actionable.",
    "warehouse": "You are a Logistics AI Advisor for a WhatsApp chat. The user is a Warehouse Manager. Focus on efficiency, bottlenecks, backlog, and operational improvements.",
    "dealer": "You are a Logistics AI Advisor for a WhatsApp chat. The user is a Dealer Manager. Focus on dealer performance, pending deliveries, and POD status.",
    "guest": "You are a Logistics AI Advisor for a WhatsApp chat. The user is a guest. Be helpful and suggest specific queries they can try."
}

# Role-specific response formatting instructions
FORMAT_INSTRUCTIONS = """
FORMATTING RULES FOR WHATSAPP:
- Use emojis for visual cues (📊, 🚨, ✅, ❌, 💡, 📦)
- Use bullet points with • or -
- Use **bold** for emphasis
- Keep paragraphs short (2-3 lines max)
- Use line breaks between sections
- NEVER return raw JSON to the user
- ALWAYS return human-readable text
- Be conversational and helpful
"""


# ==========================================================
# WHATSAPP-SPECIFIC PROMPT TEMPLATES
# ==========================================================

def dealer_whatsapp_prompt(dealer_data: Dict, user_role: str) -> str:
    """WhatsApp-friendly dealer analysis prompt"""
    return f"""
{ROLE_CONTEXTS.get(user_role, ROLE_CONTEXTS['guest'])}
{FORMAT_INSTRUCTIONS}

DEALER DATA:
- Name: {dealer_data.get('dealer_name', dealer_data.get('dealer', 'Unknown'))}
- Total DNs: {dealer_data.get('total_dns', 0)}
- Delivered: {dealer_data.get('delivered_dns', 0)}
- Pending: {dealer_data.get('pending_dns', 0)}
- POD Pending: {dealer_data.get('pod_pending_dns', 0)}
- Total Value: Rs {dealer_data.get('total_value', dealer_data.get('total_amount', 0)):,.2f}
- Pending Value: Rs {dealer_data.get('pending_value', dealer_data.get('pending_amount', 0)):,.2f}
- Health Score: {dealer_data.get('health_score', 0)}/100
- Risk Level: {dealer_data.get('risk_level', 'Unknown')}

Create a WhatsApp-friendly response about this dealer's performance.
Be concise, use emojis, and provide actionable insights.
"""


def dn_whatsapp_prompt(dn_data: Dict, user_role: str) -> str:
    """WhatsApp-friendly DN tracking prompt"""
    return f"""
{ROLE_CONTEXTS.get(user_role, ROLE_CONTEXTS['guest'])}
FORMATTING RULES FOR WHATSAPP:
- Use emojis (✅ for delivered, ⏳ for pending, 🚚 for transit, ❌ for issues)
- Use clear section headers
- Keep it readable on mobile

DN DATA:
- DN Number: {dn_data.get('dn_no', 'Unknown')}
- Status: {dn_data.get('status', 'Unknown')}
- Customer: {dn_data.get('customer_name', 'N/A')}
- City: {dn_data.get('city', 'N/A')}
- Warehouse: {dn_data.get('warehouse', 'N/A')}
- Product: {dn_data.get('product', 'N/A')}
- Quantity: {dn_data.get('quantity', 0):,.0f}
- Value: Rs {dn_data.get('value', 0):,.2f}
- Dispatch Age: {dn_data.get('dispatch_age', 0)} days

Create a WhatsApp-friendly tracking update.
"""


def general_whatsapp_prompt(question: str, user_role: str, context: Dict = None) -> str:
    """General WhatsApp-friendly prompt"""
    context_str = ""
    if context:
        if context.get('selected_dealer'):
            context_str += f"\nContext: User previously asked about dealer '{context['selected_dealer']}'"
        if context.get('selected_city'):
            context_str += f"\nContext: User previously asked about city '{context['selected_city']}'"
    
    return f"""
{ROLE_CONTEXTS.get(user_role, ROLE_CONTEXTS['guest'])}
{FORMAT_INSTRUCTIONS}
{context_str}

USER QUESTION: {question}

Provide a helpful, conversational response for WhatsApp.
Use emojis and simple formatting. Be concise (2-3 short paragraphs max).
If you don't know something, suggest what the user can try instead.
"""


# ==========================================================
# GROQ PROVIDER SERVICE
# ==========================================================

class AIProviderService:
    """
    WhatsApp AI Provider using GROQ for fast responses
    """
    
    # Available GROQ models (fast and cost-effective)
    GROQ_MODELS = {
        "llama3-70b": "llama3-70b-8192",      # Most capable
        "llama3-8b": "llama3-8b-8192",        # Fastest
        "mixtral": "mixtral-8x7b-32768",       # Good balance
        "gemma": "gemma2-9b-it"                # Alternative
    }
    
    def __init__(self, db: Session = None, model: str = "llama3-70b"):
        self.db = db
        self.model_name = self.GROQ_MODELS.get(model, self.GROQ_MODELS["llama3-70b"])
        self.cache = {}
        self.cache_ttl = 300  # 5 minutes
        self.retry_count = 2
        
        # Initialize GROQ client
        self.groq_api_key = getattr(config, 'GROQ_API_KEY', None)
        self.groq_client = None
        self.status = ProviderStatus.OFFLINE
        
        if not self.groq_api_key:
            logger.error("❌ GROQ_API_KEY not found in config!")
            logger.info("Please add GROQ_API_KEY to your Railway environment variables")
        elif not GROQ_AVAILABLE:
            logger.error("❌ Groq library not installed! Run: pip install groq")
        else:
            try:
                self.groq_client = Groq(api_key=self.groq_api_key)
                self.status = ProviderStatus.ONLINE
                logger.info(f"✅ GROQ client initialized successfully!")
                logger.info(f"   Model: {self.model_name}")
                logger.info(f"   API Key: {self.groq_api_key[:10]}...")
            except Exception as e:
                logger.error(f"❌ GROQ initialization failed: {e}")
                self.status = ProviderStatus.OFFLINE
        
        self.is_available = self.status == ProviderStatus.ONLINE
        self._log_startup_status()
    
    def _log_startup_status(self):
        """Log comprehensive startup status"""
        logger.info("=" * 60)
        logger.info("🤖 AI PROVIDER SERVICE - GROQ EDITION")
        logger.info(f"GROQ Available: {GROQ_AVAILABLE}")
        logger.info(f"API Key Present: {bool(self.groq_api_key)}")
        logger.info(f"Status: {self.status.value}")
        logger.info(f"Model: {self.model_name}")
        logger.info(f"Cache TTL: {self.cache_ttl}s")
        logger.info("=" * 60)
        
        if not self.is_available:
            logger.error("⚠️⚠️⚠️ GROQ IS NOT AVAILABLE ⚠️⚠️⚠️")
            logger.error("To fix:")
            logger.error("  1. Get a GROQ API key from https://console.groq.com")
            logger.error("  2. Add GROQ_API_KEY to Railway environment variables")
            logger.error("  3. Restart the application")
    
    def _get_cache_key(self, question: str, context_hash: str = "", user_role: str = "") -> str:
        """Generate cache key for question"""
        content_hash = hashlib.md5(f"{question}:{context_hash}:{user_role}".encode()).hexdigest()
        return content_hash
    
    def _is_cache_valid(self, timestamp: float) -> bool:
        """Check if cache entry is still valid"""
        return (time.time() - timestamp) < self.cache_ttl
    
    # ==========================================================
    # MAIN METHOD - Send to WhatsApp
    # ==========================================================
    
    def answer_question(
        self,
        question: str,
        context: Dict[str, Any] = None,
        structured: bool = False,  # GROQ returns text, not JSON
        user_phone: str = None,
        user_role: str = "guest",
        max_tokens: int = 500,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """
        Answer a question using GROQ - Optimized for WhatsApp responses
        """
        start_time = time.time()
        
        logger.info(f"🚀 GROQ REQUEST - User: {user_phone}, Role: {user_role}")
        logger.debug(f"Question: {question[:100]}...")
        
        # Check cache
        cache_key = self._get_cache_key(question, str(hash(str(context))), user_role)
        if cache_key in self.cache:
            cached_response, cached_time, cached_metadata = self.cache[cache_key]
            if self._is_cache_valid(cached_time):
                logger.info(f"✅ Cache hit for: {question[:50]}...")
                return {
                    "success": True,
                    "content": cached_response,
                    "structured_data": None,
                    "confidence": cached_metadata.get("confidence", 85),
                    "provider_used": "groq_cache",
                    "processing_time_ms": 0,
                    "cached": True
                }
        
        # Build appropriate prompt based on context
        prompt = self._build_whatsapp_prompt(question, context, user_role)
        
        # Call GROQ
        if not self.is_available:
            logger.warning("GROQ not available, using fallback")
            response = self._call_fallback(question)
            provider_used = "fallback"
        else:
            response = self._call_groq(prompt, max_tokens, temperature)
            provider_used = "groq"
        
        content = response.get("content", "")
        
        # Clean response for WhatsApp
        content = self._clean_whatsapp_response(content)
        
        # Cache successful response
        if response.get("success") and provider_used == "groq":
            self.cache[cache_key] = (content, time.time(), {"confidence": 85})
        
        processing_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ GROQ RESPONSE - Success: {response.get('success')}, Time: {processing_time}ms")
        
        # Log to database
        self._log_usage(
            user_phone=user_phone,
            question=question,
            response=content,
            provider=provider_used,
            success=response.get("success", False),
            processing_time_ms=processing_time,
            confidence=85
        )
        
        return {
            "success": response.get("success", False),
            "content": content,
            "structured_data": None,  # GROQ returns text, not JSON
            "confidence": 85,
            "provider_used": provider_used,
            "processing_time_ms": processing_time,
            "cached": False
        }
    
    def _build_whatsapp_prompt(self, question: str, context: Dict = None, user_role: str = "guest") -> str:
        """Build appropriate prompt based on context type"""
        
        # Check if this is a dealer query
        if context and context.get("dealer_data"):
            return dealer_whatsapp_prompt(context["dealer_data"], user_role)
        
        # Check if this is a DN query
        if context and context.get("dn_data"):
            return dn_whatsapp_prompt(context["dn_data"], user_role)
        
        # General query
        return general_whatsapp_prompt(question, user_role, context)
    
    def _call_groq(self, prompt: str, max_tokens: int = 500, temperature: float = 0.7) -> Dict[str, Any]:
        """Call GROQ API with retry logic"""
        
        if not self.groq_client:
            return {"success": False, "content": "GROQ client not initialized", "error": "No client"}
        
        for attempt in range(self.retry_count + 1):
            try:
                logger.debug(f"GROQ API call attempt {attempt + 1}")
                
                response = self.groq_client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": "You are a helpful WhatsApp logistics assistant. Always return human-readable text with emojis and simple formatting. Never return raw JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                
                content = response.choices[0].message.content
                
                logger.debug(f"GROQ success: {len(content)} chars")
                return {
                    "success": True,
                    "content": content,
                    "model": self.model_name,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens if hasattr(response.usage, 'prompt_tokens') else 0,
                        "completion_tokens": response.usage.completion_tokens if hasattr(response.usage, 'completion_tokens') else 0,
                        "total_tokens": response.usage.total_tokens if hasattr(response.usage, 'total_tokens') else 0
                    }
                }
                
            except Exception as e:
                logger.warning(f"GROQ attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_count:
                    time.sleep(1)
                else:
                    return {"success": False, "content": "", "error": str(e)}
        
        return {"success": False, "content": "", "error": "Max retries exceeded"}
    
    def _call_fallback(self, question: str) -> Dict[str, Any]:
        """Fallback responses when GROQ is unavailable"""
        
        question_lower = question.lower()
        
        if any(word in question_lower for word in ["pending", "backlog", "delay"]):
            content = """📊 *PENDING STATUS*

I can see you're asking about pending items.

💡 *Try these commands:*
• `Top dealers` - See dealer rankings
• `Executive summary` - Network overview
• `[Dealer name]` - Specific dealer report

Our AI system is currently connecting to GROQ. Please try again in a moment."""
        
        elif any(word in question_lower for word in ["dealer", "customer", "shop"]):
            content = """🏪 *DEALER LOOKUP*

To check a dealer's performance, just type their name.

📝 *Examples:*
• `Bhatti Electronics`
• `Rafi Electronics Oghi`
• `Good Luck Electronics`

Our AI system is currently initializing. Try typing a dealer name!"""
        
        elif any(word in question_lower for word in ["dn", "track", "delivery note", "status"]):
            content = """🔢 *DN TRACKING*

To track a delivery note, send the 10-digit number.

📝 *Examples:*
• `6243611361`
• `DN 6243611361`
• `Status 6243611361`

Our AI system is ready - just send a DN number!"""
        
        else:
            content = """👋 *Welcome to Logistics Assistant*

I can help you with:

📊 • Dealer performance reports
🔢 • DN tracking & status
🌆 • City-wise analytics
👑 • Executive summaries

💡 *Try typing:*
• A dealer name (e.g., "Bhatti Electronics")
• A 10-digit DN number
• "Executive summary"
• "Help" for all commands

GROQ AI is being initialized. Basic commands work now!"""
        
        return {
            "success": True,
            "content": content,
            "model": "fallback",
            "fallback": True
        }
    
    def _clean_whatsapp_response(self, content: str) -> str:
        """Clean response for WhatsApp display"""
        if not content:
            return "I couldn't generate a response. Please try again."
        
        # Remove any JSON that might have slipped through
        if content.strip().startswith('{'):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    content = parsed.get("response") or parsed.get("content") or parsed.get("message") or "Response received."
            except:
                pass
        
        # Ensure response has emojis for better UX (add if missing)
        if not any(c in content for c in ['📊', '✅', '❌', '🚨', '💡', '📦', '🏪', '🔢', '🌆', '👑']):
            content = "📋 " + content
        
        # Truncate if too long (WhatsApp limit ~4000 chars)
        if len(content) > 3800:
            content = content[:3800] + "\n\n... (response truncated)"
        
        return content
    
    def _log_usage(self, user_phone: str, question: str, response: str, provider: str, success: bool, processing_time_ms: int, confidence: int = 0):
        """Log usage to database"""
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
            logger.error(f"Failed to log usage: {e}")
            if self.db:
                self.db.rollback()
    
    def analyze_dn(self, dn_data: Dict, user_phone: str = None) -> Dict[str, Any]:
        """Analyze DN for WhatsApp response"""
        return self.answer_question(
            question=f"Provide status update for DN {dn_data.get('dn_no', 'unknown')}",
            context={"dn_data": dn_data},
            user_phone=user_phone,
            user_role="guest"
        )
    
    def analyze_dealer(self, dealer_data: Dict, user_phone: str = None) -> Dict[str, Any]:
        """Analyze dealer for WhatsApp response"""
        return self.answer_question(
            question=f"Provide performance analysis for dealer",
            context={"dealer_data": dealer_data},
            user_phone=user_phone,
            user_role="manager"
        )


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

ai_provider_service = None


def init_ai_provider_service(db: Session = None, model: str = "llama3-70b") -> AIProviderService:
    """Initialize GROQ AI Provider Service singleton"""
    global ai_provider_service
    
    try:
        ai_provider_service = AIProviderService(db, model)
        
        if ai_provider_service.is_available:
            logger.info("✅ GROQ AI PROVIDER SERVICE LOADED SUCCESSFULLY")
            logger.info(f"   Model: {ai_provider_service.model_name}")
        else:
            logger.warning("⚠️ GROQ AI PROVIDER SERVICE LOADED IN FALLBACK MODE")
            logger.info("   Add GROQ_API_KEY to enable AI features")
        
        return ai_provider_service
        
    except Exception as e:
        logger.error(f"❌ GROQ AI SERVICE INITIALIZATION ERROR: {e}")
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
    logger.info("GROQ AI Provider Service v1.0 initialized")
except Exception as e:
    logger.error(f"Auto-initialization failed: {e}")
    ai_provider_service = None
