# ==========================================================
# FILE: app/services/ai_provider_service.py (CLEAN v5.0)
# ==========================================================
# GROQ AI PROVIDER SERVICE - SINGLE RESPONSIBILITY
# ==========================================================

import os
import json
from typing import Dict, Any, Optional
from loguru import logger

from app.config import config

GROQ_AVAILABLE = False
Groq = None

try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ GROQ library imported successfully")
except ImportError:
    logger.error("❌ GROQ import failed - run: pip install groq")


class AIProviderService:
    """GROQ AI Provider Service"""
    
    def __init__(self, db=None):
        self.db = db
        self.client = None
        self.is_available = False
        self.model = "llama-3.3-70b-versatile"
        
        api_key = getattr(config, 'GROQ_API_KEY', None) or os.getenv('GROQ_API_KEY')
        
        if not api_key:
            logger.error("❌ GROQ_API_KEY not found")
        elif not GROQ_AVAILABLE:
            logger.error("❌ Groq library not installed")
        else:
            try:
                self.client = Groq(api_key=api_key)
                self.is_available = True
                logger.success("✅ GROQ client initialized!")
            except Exception as e:
                logger.error(f"❌ GROQ init failed: {e}")
    
    def answer_question(self, question: str, **kwargs) -> Dict[str, Any]:
        """Answer question using GROQ"""
        if not self.is_available:
            return {"success": False, "content": "", "error": "GROQ not available"}
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful WhatsApp logistics assistant. Be concise, use emojis."},
                    {"role": "user", "content": question}
                ],
                max_tokens=500,
                temperature=0.7
            )
            content = response.choices[0].message.content
            return {"success": True, "content": content}
        except Exception as e:
            logger.error(f"GROQ API error: {e}")
            return {"success": False, "content": "", "error": str(e)}


_ai_provider_service = None

def get_ai_provider_service(db=None):
    global _ai_provider_service
    if _ai_provider_service is None:
        _ai_provider_service = AIProviderService(db)
    return _ai_provider_service
