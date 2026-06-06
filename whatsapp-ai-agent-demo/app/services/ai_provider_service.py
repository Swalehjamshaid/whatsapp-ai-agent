# ==========================================================
# FILE: app/services/ai_provider_service.py (WORKING v4.0)
# ==========================================================

import os
import json
from typing import Dict, Any, Optional
from loguru import logger

from app.config import config

# ==========================================================
# GROQ IMPORT
# ==========================================================

GROQ_AVAILABLE = False
Groq = None

try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ GROQ library imported successfully")
except ImportError as e:
    logger.error(f"❌ GROQ import failed: {e}")
    logger.info("   Run: pip install groq")
except Exception as e:
    logger.error(f"❌ GROQ import error: {e}")


# ==========================================================
# AI PROVIDER SERVICE
# ==========================================================

class AIProviderService:
    """GROQ AI Provider Service for WhatsApp"""
    
    def __init__(self, db=None):
        self.db = db
        self.client = None
        self.is_available = False
        self.api_key = None
        self.model = "llama-3.3-70b-versatile"
        
        # Get API key from config or environment
        self.api_key = getattr(config, 'GROQ_API_KEY', None)
        if not self.api_key:
            self.api_key = os.getenv('GROQ_API_KEY')
        
        logger.info("=" * 50)
        logger.info("🤖 GROQ AI PROVIDER INITIALIZING")
        logger.info(f"GROQ Library: {'✓' if GROQ_AVAILABLE else '✗'}")
        logger.info(f"API Key: {'✓' if self.api_key else '✗'}")
        logger.info(f"Model: {self.model}")
        
        # Initialize if possible
        if not self.api_key:
            logger.error("❌ GROQ_API_KEY not found in config or environment")
        elif not GROQ_AVAILABLE:
            logger.error("❌ Groq library not installed")
        else:
            try:
                self.client = Groq(api_key=self.api_key)
                self.is_available = True
                logger.success("✅ GROQ client initialized successfully!")
            except Exception as e:
                logger.error(f"❌ GROQ initialization failed: {e}")
                self.is_available = False
        
        if not self.is_available:
            logger.warning("⚠️ GROQ NOT AVAILABLE - Using fallback mode")
        logger.info("=" * 50)
    
    def answer_question(self, question: str, context: Dict = None, user_phone: str = None, user_role: str = "guest", **kwargs) -> Dict[str, Any]:
        """Answer a question using GROQ API"""
        
        logger.info(f"🚀 GROQ REQUEST - User: {user_phone}")
        logger.debug(f"Question: {question[:100]}...")
        
        if not self.is_available:
            return {"success": False, "content": "", "error": "GROQ not available"}
        
        try:
            # Build prompt with context if provided
            full_question = question
            if context:
                full_question = f"Context: {json.dumps(context, default=str)[:500]}\n\nQuestion: {question}"
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful WhatsApp logistics assistant. Be concise, use emojis. Never return JSON. Always return human-readable text."},
                    {"role": "user", "content": full_question}
                ],
                max_tokens=500,
                temperature=0.7
            )
            content = response.choices[0].message.content
            logger.success(f"✅ GROQ response received - Length: {len(content)} chars")
            return {"success": True, "content": content}
            
        except Exception as e:
            logger.error(f"❌ GROQ API error: {e}")
            return {"success": False, "content": "", "error": str(e)}


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_ai_provider_service = None


def get_ai_provider_service(db=None):
    """Get or create AI Provider Service singleton"""
    global _ai_provider_service
    if _ai_provider_service is None:
        _ai_provider_service = AIProviderService(db)
        logger.info("✅ AI Provider Service singleton created")
    return _ai_provider_service


def init_ai_provider_service(db=None):
    """Initialize AI Provider Service (alias)"""
    return get_ai_provider_service(db)


def reset_ai_provider_service():
    """Reset singleton (for testing)"""
    global _ai_provider_service
    _ai_provider_service = None
    logger.info("🔄 AI Provider Service reset")


# ==========================================================
# FOR DIRECT TESTING (run with: python -m app.services.ai_provider_service)
# ==========================================================

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("🧪 TESTING GROQ PROVIDER SERVICE")
    print("=" * 50)
    
    service = AIProviderService()
    print(f"GROQ Available: {service.is_available}")
    
    if service.is_available:
        result = service.answer_question("Say 'GROQ is working' in one sentence.")
        print(f"Response: {result.get('content')}")
    else:
        print("GROQ not available - check API key")
        print("\nPossible fixes:")
        print("1. Add GROQ_API_KEY to Railway environment variables")
        print("2. Run: pip install groq")
        print("3. Restart the application")# ==========================================================
# FILE: app/services/ai_provider_service.py (WORKING v4.0)
# ==========================================================

import os
import json
from typing import Dict, Any, Optional
from loguru import logger

from app.config import config

# ==========================================================
# GROQ IMPORT
# ==========================================================

GROQ_AVAILABLE = False
Groq = None

try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ GROQ library imported successfully")
except ImportError as e:
    logger.error(f"❌ GROQ import failed: {e}")
    logger.info("   Run: pip install groq")
except Exception as e:
    logger.error(f"❌ GROQ import error: {e}")


# ==========================================================
# AI PROVIDER SERVICE
# ==========================================================

class AIProviderService:
    """GROQ AI Provider Service for WhatsApp"""
    
    def __init__(self, db=None):
        self.db = db
        self.client = None
        self.is_available = False
        self.api_key = None
        self.model = "llama-3.3-70b-versatile"
        
        # Get API key from config or environment
        self.api_key = getattr(config, 'GROQ_API_KEY', None)
        if not self.api_key:
            self.api_key = os.getenv('GROQ_API_KEY')
        
        logger.info("=" * 50)
        logger.info("🤖 GROQ AI PROVIDER INITIALIZING")
        logger.info(f"GROQ Library: {'✓' if GROQ_AVAILABLE else '✗'}")
        logger.info(f"API Key: {'✓' if self.api_key else '✗'}")
        logger.info(f"Model: {self.model}")
        
        # Initialize if possible
        if not self.api_key:
            logger.error("❌ GROQ_API_KEY not found in config or environment")
        elif not GROQ_AVAILABLE:
            logger.error("❌ Groq library not installed")
        else:
            try:
                self.client = Groq(api_key=self.api_key)
                self.is_available = True
                logger.success("✅ GROQ client initialized successfully!")
            except Exception as e:
                logger.error(f"❌ GROQ initialization failed: {e}")
                self.is_available = False
        
        if not self.is_available:
            logger.warning("⚠️ GROQ NOT AVAILABLE - Using fallback mode")
        logger.info("=" * 50)
    
    def answer_question(self, question: str, context: Dict = None, user_phone: str = None, user_role: str = "guest", **kwargs) -> Dict[str, Any]:
        """Answer a question using GROQ API"""
        
        logger.info(f"🚀 GROQ REQUEST - User: {user_phone}")
        logger.debug(f"Question: {question[:100]}...")
        
        if not self.is_available:
            return {"success": False, "content": "", "error": "GROQ not available"}
        
        try:
            # Build prompt with context if provided
            full_question = question
            if context:
                full_question = f"Context: {json.dumps(context, default=str)[:500]}\n\nQuestion: {question}"
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful WhatsApp logistics assistant. Be concise, use emojis. Never return JSON. Always return human-readable text."},
                    {"role": "user", "content": full_question}
                ],
                max_tokens=500,
                temperature=0.7
            )
            content = response.choices[0].message.content
            logger.success(f"✅ GROQ response received - Length: {len(content)} chars")
            return {"success": True, "content": content}
            
        except Exception as e:
            logger.error(f"❌ GROQ API error: {e}")
            return {"success": False, "content": "", "error": str(e)}


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_ai_provider_service = None


def get_ai_provider_service(db=None):
    """Get or create AI Provider Service singleton"""
    global _ai_provider_service
    if _ai_provider_service is None:
        _ai_provider_service = AIProviderService(db)
        logger.info("✅ AI Provider Service singleton created")
    return _ai_provider_service


def init_ai_provider_service(db=None):
    """Initialize AI Provider Service (alias)"""
    return get_ai_provider_service(db)


def reset_ai_provider_service():
    """Reset singleton (for testing)"""
    global _ai_provider_service
    _ai_provider_service = None
    logger.info("🔄 AI Provider Service reset")


# ==========================================================
# FOR DIRECT TESTING (run with: python -m app.services.ai_provider_service)
# ==========================================================

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("🧪 TESTING GROQ PROVIDER SERVICE")
    print("=" * 50)
    
    service = AIProviderService()
    print(f"GROQ Available: {service.is_available}")
    
    if service.is_available:
        result = service.answer_question("Say 'GROQ is working' in one sentence.")
        print(f"Response: {result.get('content')}")
    else:
        print("GROQ not available - check API key")
        print("\nPossible fixes:")
        print("1. Add GROQ_API_KEY to Railway environment variables")
        print("2. Run: pip install groq")
        print("3. Restart the application")
