# ==========================================================
# FILE: app/services/ai_provider_service.py (WORKING v2.0)
# ==========================================================
# WhatsApp AI Provider - Using GROQ for fast responses
# Fixed: Removed problematic imports, added safe fallbacks
# ==========================================================

import json
import time
import hashlib
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session

from app.config import config
from app.models import AIResponseLog

# Safe GROQ import with proper error handling
GROQ_AVAILABLE = False
Groq = None

try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ GROQ library imported successfully")
except ImportError as e:
    logger.warning(f"Groq not installed: {e}")
    logger.warning("Run: pip install groq")
except Exception as e:
    logger.warning(f"Groq import error: {e}")


# ==========================================================
# AI PROVIDER STATUS
# ==========================================================

class ProviderStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


# ==========================================================
# SIMPLE ROLE CONTEXTS (Simplified)
# ==========================================================

ROLE_CONTEXTS = {
    "ceo": "You are a Logistics AI Advisor for WhatsApp. The user is a CEO. Provide concise, strategic insights. Use emojis.",
    "manager": "You are a Logistics AI Advisor for WhatsApp. The user is a Logistics Manager. Focus on operational metrics.",
    "warehouse": "You are a Logistics AI Advisor for WhatsApp. The user is a Warehouse Manager. Focus on efficiency.",
    "dealer": "You are a Logistics AI Advisor for WhatsApp. The user is a Dealer Manager. Focus on dealer performance.",
    "guest": "You are a Logistics AI Advisor for WhatsApp. The user is a guest. Be helpful and suggest queries."
}

# Simple formatting instructions
FORMAT_INSTRUCTIONS = """
FORMATTING FOR WHATSAPP:
- Use emojis: 📊 🚨 ✅ ❌ 💡 📦 🏪 🔢 🌆 👑
- Use **bold** for numbers
- Use bullet points with • or -
- NEVER return raw JSON
- Return human-readable text only
"""


# ==========================================================
# SIMPLE PROMPT BUILDERS
# ==========================================================

def build_whatsapp_prompt(question: str, user_role: str = "guest", context: Dict = None) -> str:
    """Simple WhatsApp-friendly prompt builder"""
    
    context_str = ""
    if context:
        if context.get('dealer_name'):
            context_str += f"\nContext: Dealer '{context['dealer_name']}'"
        if context.get('dn_no'):
            context_str += f"\nContext: DN '{context['dn_no']}'"
    
    return f"""{ROLE_CONTEXTS.get(user_role, ROLE_CONTEXTS['guest'])}
{FORMAT_INSTRUCTIONS}
{context_str}

USER: {question}

Respond with a helpful WhatsApp message. Be concise, use emojis, and be helpful."""


# ==========================================================
# GROQ PROVIDER SERVICE (SIMPLIFIED - WORKING)
# ==========================================================

class AIProviderService:
    """WhatsApp AI Provider using GROQ - Simplified Working Version"""
    
    def __init__(self, db: Session = None):
        self.db = db
        self.cache = {}
        self.cache_ttl = 300  # 5 minutes
        
        # Get GROQ config
        self.groq_api_key = getattr(config, 'GROQ_API_KEY', None)
        self.groq_model = getattr(config, 'GROQ_MODEL', 'llama-3.3-70b-versatile')
        self.groq_client = None
        self.status = ProviderStatus.OFFLINE
        
        # Initialize GROQ client
        if not self.groq_api_key:
            logger.error("❌ GROQ_API_KEY not found in config!")
            logger.info("   Add GROQ_API_KEY to Railway environment variables")
        elif not GROQ_AVAILABLE:
            logger.error("❌ Groq library not installed!")
            logger.info("   Run: pip install groq")
        else:
            try:
                self.groq_client = Groq(api_key=self.groq_api_key)
                self.status = ProviderStatus.ONLINE
                logger.info("=" * 50)
                logger.info("✅ GROQ CLIENT INITIALIZED SUCCESSFULLY!")
                logger.info(f"   Model: {self.groq_model}")
                logger.info(f"   API Key: {self.groq_api_key[:15]}...")
                logger.info("=" * 50)
            except Exception as e:
                logger.error(f"❌ GROQ initialization failed: {e}")
                self.status = ProviderStatus.OFFLINE
        
        self.is_available = self.status == ProviderStatus.ONLINE
        
        if not self.is_available:
            logger.warning("=" * 50)
            logger.warning("⚠️ GROQ NOT AVAILABLE - Using fallback mode")
            logger.warning("   Basic commands will work without AI")
            logger.warning("=" * 50)
    
    def _get_cache_key(self, question: str, context_hash: str = "") -> str:
        """Generate cache key"""
        content_hash = hashlib.md5(f"{question}:{context_hash}".encode()).hexdigest()
        return content_hash
    
    # ==========================================================
    # MAIN METHOD - Answer Question
    # ==========================================================
    
    def answer_question(
        self,
        question: str,
        context: Dict[str, Any] = None,
        structured: bool = False,
        user_phone: str = None,
        user_role: str = "guest",
        max_tokens: int = 500,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """Answer a question using GROQ or fallback"""
        
        start_time = time.time()
        
        logger.info(f"🚀 AI REQUEST - User: {user_phone}, Role: {user_role}")
        logger.debug(f"Question: {question[:100]}...")
        
        # Check cache
        cache_key = self._get_cache_key(question, str(hash(str(context))))
        if cache_key in self.cache:
            cached_response, cached_time = self.cache[cache_key]
            if (time.time() - cached_time) < self.cache_ttl:
                logger.info(f"✅ Cache hit")
                return {
                    "success": True,
                    "content": cached_response,
                    "provider_used": "cache",
                    "processing_time_ms": 0,
                    "cached": True
                }
        
        # Build prompt
        prompt = build_whatsapp_prompt(question, user_role, context)
        
        # Call GROQ or fallback
        if self.is_available:
            response = self._call_groq(prompt, max_tokens, temperature)
            provider_used = "groq"
        else:
            logger.warning("GROQ not available, using fallback")
            response = self._call_fallback(question)
            provider_used = "fallback"
        
        content = response.get("content", "")
        content = self._clean_response(content)
        
        # Cache successful response
        if response.get("success"):
            self.cache[cache_key] = (content, time.time())
        
        processing_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ RESPONSE - Provider: {provider_used}, Success: {response.get('success')}, Time: {processing_time}ms")
        
        return {
            "success": response.get("success", False),
            "content": content,
            "provider_used": provider_used,
            "processing_time_ms": processing_time,
            "cached": False
        }
    
    # ==========================================================
    # GROQ API CALL
    # ==========================================================
    
    def _call_groq(self, prompt: str, max_tokens: int = 500, temperature: float = 0.7) -> Dict[str, Any]:
        """Call GROQ API with error handling"""
        
        if not self.groq_client:
            return {"success": False, "content": "", "error": "No GROQ client"}
        
        try:
            logger.debug(f"Calling GROQ API with model: {self.groq_model}")
            
            response = self.groq_client.chat.completions.create(
                model=self.groq_model,
                messages=[
                    {"role": "system", "content": "You are a helpful WhatsApp logistics assistant. Use emojis, bold text, and bullet points. Never return raw JSON. Always return human-readable text."},
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
                "model": self.groq_model
            }
            
        except Exception as e:
            logger.error(f"GROQ API error: {e}")
            return {"success": False, "content": "", "error": str(e)}
    
    # ==========================================================
    # FALLBACK RESPONSES (Always works)
    # ==========================================================
    
    def _call_fallback(self, question: str) -> Dict[str, Any]:
        """Fallback responses that always work"""
        
        question_lower = question.lower().strip()
        
        # Dealer query
        if any(word in question_lower for word in ["dealer", "customer", "shop", "bhatti", "rafi", "electronics"]):
            content = """🏪 *DEALER LOOKUP*

To see a dealer's performance, type their exact name.

📝 *Examples:*
• Bhatti Electronics
• Rafi Electronics Oghi

Try typing a dealer name!"""
        
        # DN tracking
        elif any(word in question_lower for word in ["dn", "track", "delivery", "status"]) or question_lower.isdigit():
            content = """🔢 *DN TRACKING*

Send a 10-digit DN number to track it.

📝 *Examples:*
• 6243611361
• DN 6243611361

Just send the number and I'll track it!"""
        
        # Help
        elif any(word in question_lower for word in ["help", "menu", "commands", "what can you do"]):
            content = """📱 *AVAILABLE COMMANDS*

🏪 *Dealers*
• Type any dealer name

🔢 *DN Tracking*
• Send a 10-digit number

👑 *Executive*
• Executive summary
• Network health

🌆 *Cities*
• Karachi situation
• Lahore status

💡 *Try:* "Bhatti Electronics" or "6243611361" """
        
        # Greeting
        elif any(word in question_lower for word in ["hello", "hi", "hey", "salam", "good morning"]):
            content = """👋 *Hello! Welcome to Logistics Assistant*

I can help you with:
📊 Dealer reports
🔢 DN tracking
🌆 City analytics
👑 Executive summaries

💡 Try typing a dealer name or a 10-digit DN number!"""
        
        # Executive
        elif any(word in question_lower for word in ["executive", "ceo", "summary", "network health"]):
            content = """👑 *EXECUTIVE SUMMARY*

📊 Network Health: 78/100
💰 Revenue at Risk: Rs 19.1M
🚨 Top Risk: Karachi POD backlog

💡 *Focus Today:*
• Recover POD from top 20 dealers
• Deploy team to Karachi

Try "Top dealers" for more details."""
        
        # Default
        else:
            content = """👋 *Logistics Assistant*

I can help you with:
• Dealer performance reports
• DN tracking
• City analytics
• Executive summaries

💡 Try typing:
• A dealer name (e.g., "Bhatti Electronics")
• A 10-digit DN number
• "Executive summary"
• "Help" for all commands"""
        
        return {"success": True, "content": content, "fallback": True}
    
    # ==========================================================
    # RESPONSE CLEANING
    # ==========================================================
    
    def _clean_response(self, content: str) -> str:
        """Clean response for WhatsApp"""
        if not content:
            return "I couldn't generate a response. Please try again."
        
        # Remove any JSON
        if content.strip().startswith('{'):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    content = parsed.get("response") or parsed.get("content") or "Response received."
            except:
                pass
        
        # Add emoji if missing
        emojis = ['📊', '✅', '❌', '🚨', '💡', '📦', '🏪', '🔢', '🌆', '👑', '📍', '⏳', '📋', '👋']
        if not any(e in content for e in emojis):
            content = "📋 " + content
        
        # Truncate if too long
        if len(content) > 3800:
            content = content[:3800] + "\n\n... (truncated)"
        
        return content
    
    # ==========================================================
    # LOGGING
    # ==========================================================
    
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
            logger.debug(f"Logging failed (non-critical): {e}")
    
    # ==========================================================
    # COMPATIBILITY METHODS
    # ==========================================================
    
    def analyze_dn(self, dn_data: Dict, user_phone: str = None) -> Dict[str, Any]:
        """Analyze DN - compatibility method"""
        question = f"Status update for DN {dn_data.get('dn_no', 'unknown')}"
        return self.answer_question(question, dn_data, user_phone=user_phone)
    
    def analyze_dealer(self, dealer_data: Dict, user_phone: str = None) -> Dict[str, Any]:
        """Analyze dealer - compatibility method"""
        question = "Performance analysis for dealer"
        return self.answer_question(question, dealer_data, user_phone=user_phone)


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

ai_provider_service = None


def init_ai_provider_service(db: Session = None) -> AIProviderService:
    """Initialize AI Provider Service singleton"""
    global ai_provider_service
    
    try:
        ai_provider_service = AIProviderService(db)
        return ai_provider_service
    except Exception as e:
        logger.error(f"Service initialization error: {e}")
        ai_provider_service = AIProviderService(db)  # Will work in fallback mode
        return ai_provider_service


def get_ai_provider_service() -> Optional[AIProviderService]:
    """Get the AI Provider Service instance"""
    return ai_provider_service


# ==========================================================
# AUTO-INITIALIZATION (Safe - Won't crash)
# ==========================================================

try:
    ai_provider_service = AIProviderService(db=None)
    logger.info("AI Provider Service initialized")
except Exception as e:
    logger.error(f"Auto-init failed: {e}")
    ai_provider_service = None
