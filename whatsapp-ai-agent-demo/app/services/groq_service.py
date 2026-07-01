"""
File: app/services/groq_service.py
Version: 2.0 - ENTERPRISE AI ENHANCEMENT SERVICE
Purpose: AI enhancement and natural language processing for WhatsApp responses
Integration: 100% integrated with ai_provider_service.py and PostgreSQL
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import httpx
from cachetools import TTLCache

# ============================================================
# CONFIGURATION
# ============================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama3-70b-8192")
GROQ_API_URL = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "30"))
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "500"))
GROQ_TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.7"))
ENABLE_GROQ = os.getenv("ENABLE_GROQ", "true").lower() == "true"

logger = logging.getLogger(__name__)


# ============================================================
# PROMPT TEMPLATES
# ============================================================

class GroqPromptTemplates:
    """Prompt templates for different use cases"""
    
    # System prompt - defines the assistant's role
    SYSTEM_PROMPT = """You are HPK Logistics AI Assistant, an expert logistics analytics bot for Pakistan's supply chain.

You have access to real logistics data from PostgreSQL including:
- Delivery Notes (DN) with status, quantities, revenue
- Dealer performance metrics
- Warehouse operations data
- City-level analytics
- PGI and POD status
- National KPIs

Your role is to:
1. Understand user questions about logistics data
2. Provide clear, concise answers with insights
3. Suggest relevant logistics metrics when appropriate
4. Maintain a professional, helpful tone

Always be specific, data-driven, and actionable in your responses.

Format responses for WhatsApp - use:
- Bullet points (•) for lists
- Bold (**text**) for emphasis
- Emojis for visual cues (📦 🏪 📊 🚚 ✅ ❌ ⚠️)

When you don't know something, say so clearly. Never invent data.
NEVER mention that you are an AI. Present yourself as the Logistics Assistant."""

    # Query enhancement prompt
    QUERY_ENHANCEMENT = """Analyze this logistics query and extract key information:

Query: {query}

Extract:
1. Intent (what the user wants to know)
2. Entity type (DN, Dealer, Warehouse, City, Product, KPI, Pending, Delivery, Revenue, Units, Comparison)
3. Entity name (if any)
4. Time frame (if any)
5. Additional filters or conditions

Return as JSON only:
{{
    "intent": "",
    "entity_type": "",
    "entity_name": "",
    "time_frame": "",
    "conditions": []
}}"""

    # Response enhancement prompt
    RESPONSE_ENHANCEMENT = """Enhance this business data response for WhatsApp:

Context:
- User asked: {user_query}
- Intent: {intent}
- Entity: {entity}

Business Data:
{business_data}

Requirements:
1. Make it clear and scannable for WhatsApp
2. Add relevant insights and context
3. Suggest next steps if appropriate
4. Use bullet points (•) and emojis
5. Keep it concise (max 2000 characters)
6. Highlight key metrics (bold them)
7. Add a brief executive summary at the top

Format the response with:
- A brief summary
- Key metrics with values
- Insights or trends (if applicable)
- Recommendations or next steps (if applicable)

Return only the formatted WhatsApp message."""

    # Explanation prompt
    EXPLANATION_PROMPT = """Explain this logistics term in simple, clear language for WhatsApp:

Term: {term}
Context: {context}

Format for WhatsApp with:
- A simple definition
- Why it matters
- A real example (if applicable)
- Related terms (if applicable)

Keep it under 500 characters. Use bullet points and emojis for readability."""

    # Conversational response prompt
    CONVERSATIONAL_PROMPT = """Respond to this user message as HPK Logistics AI Assistant:

User: {user_message}

Guidelines:
1. Be helpful and professional
2. If it's a greeting, respond warmly
3. If it's a question, answer it directly
4. If unsure, suggest what you can help with
5. Suggest specific logistics topics they can ask about
6. Keep it concise (max 500 characters)

Suggested logistics topics:
- DN Tracking (send a DN number)
- Dealer Analytics (dealer name)
- Warehouse Analytics (warehouse name)
- City Analytics (city name)
- National KPIs
- Pending Deliveries
- Reports & Rankings

Format for WhatsApp with appropriate emojis."""

    # Pending DNs prompt
    PENDING_DNS_PROMPT = """Analyze these pending DNs and provide a helpful summary:

Pending DNs Data:
{pending_data}

Summary Requirements:
1. Total pending count
2. Breakdown by pending type (PGI Pending vs POD Pending)
3. Oldest pending DN
4. Total revenue pending
5. Urgency assessment

Format for WhatsApp with clear sections and emojis."""

    # Dealer insights prompt
    DEALER_INSIGHTS_PROMPT = """Analyze this dealer's performance data and provide actionable insights:

Dealer: {dealer_name}
Data: {dealer_data}

Insights to provide:
1. Overall performance assessment (Excellent/Good/Watch/Critical)
2. Key strengths (top 2-3)
3. Areas for improvement (top 2-3)
4. Specific recommendations (top 2-3)
5. Revenue growth trend

Be specific, data-driven, and actionable. Format for WhatsApp."""


# ============================================================
# GROQ SERVICE
# ============================================================

class GroqService:
    """
    Enterprise Groq AI Service for WhatsApp Logistics Assistant.
    Provides AI enhancement for business data responses.
    """
    
    _instance: Optional["GroqService"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "GroqService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self) -> None:
        if self._initialized:
            return
        
        self._initialized = True
        self._client = None
        self._cache = TTLCache(maxsize=1000, ttl=3600)
        
        # Initialize HTTP client
        if ENABLE_GROQ and GROQ_API_KEY:
            try:
                self._client = httpx.AsyncClient(
                    timeout=GROQ_TIMEOUT,
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    }
                )
                logger.info("✅ GroqService initialized with API key")
            except Exception as e:
                logger.error(f"❌ GroqService initialization failed: {e}")
                self._client = None
        else:
            if not GROQ_API_KEY:
                logger.warning("⚠️ GROQ_API_KEY not set - Groq service disabled")
            if not ENABLE_GROQ:
                logger.info("ℹ️ Groq service disabled by ENABLE_GROQ=false")
            self._client = None
        
        # Initialize prompt templates
        self.prompts = GroqPromptTemplates()
        
        logger.info(f"✅ GroqService initialized (v2.0) - Enabled: {self.is_available()}")
    
    def is_available(self) -> bool:
        """Check if Groq service is available"""
        return self._client is not None and ENABLE_GROQ and GROQ_API_KEY is not None
    
    def _get_cache_key(self, prompt: str, system_prompt: str) -> str:
        """Generate cache key for a request"""
        import hashlib
        key = f"{system_prompt}_{prompt}"
        return hashlib.md5(key.encode()).hexdigest()
    
    async def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = GROQ_TEMPERATURE,
        max_tokens: int = GROQ_MAX_TOKENS,
    ) -> Optional[str]:
        """
        Call Groq API with system and user prompts.
        
        Args:
            system_prompt: System instruction for the AI
            user_prompt: User query or data to process
            temperature: Creativity (0.0 to 1.0)
            max_tokens: Maximum response length
        
        Returns:
            AI response string or None on failure
        """
        if not self.is_available():
            logger.warning("Groq service not available - skipping API call")
            return None
        
        # Check cache
        cache_key = self._get_cache_key(user_prompt, system_prompt)
        if cache_key in self._cache:
            logger.debug(f"✅ Groq cache hit for: {user_prompt[:50]}...")
            return self._cache[cache_key]
        
        try:
            payload = {
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            
            logger.debug(f"📤 Calling Groq API: {GROQ_MODEL}")
            start_time = time.perf_counter()
            
            response = await self._client.post(GROQ_API_URL, json=payload)
            response.raise_for_status()
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            data = response.json()
            
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"]["content"].strip()
                logger.info(f"✅ Groq response received in {elapsed_ms:.2f}ms")
                
                # Cache the response
                self._cache[cache_key] = content
                return content
            
            logger.error(f"❌ Unexpected Groq response format: {data}")
            return None
            
        except httpx.TimeoutException:
            logger.error("❌ Groq API timeout")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"❌ Groq API HTTP error: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"❌ Groq API error: {str(e)}")
            return None
    
    # ============================================================
    # PUBLIC METHODS
    # ============================================================
    
    async def process_query(self, message: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Process a user query with AI enhancement.
        
        Args:
            message: User message
            context: Optional context (intent, entity, business data)
        
        Returns:
            Dict with response, intent, confidence
        """
        if not self.is_available():
            return {
                "success": False,
                "response": "AI service is currently unavailable. Please try again later.",
                "error": "Groq service not available"
            }
        
        try:
            # Detect if it's a greeting
            greeting_patterns = [
                r'^(hi|hello|hey|good morning|good evening|good afternoon|howdy|salam|namaste)',
                r'^(what\'?s up|how are you|how do you do|nice to meet you)',
            ]
            
            for pattern in greeting_patterns:
                if re.search(pattern, message.lower()):
                    response = await self._generate_greeting(message)
                    return {
                        "success": True,
                        "response": response,
                        "intent": "greeting",
                        "confidence": 0.95,
                    }
            
            # Check for help
            if re.search(r'(help|assist|support|what can you do|how to use|commands|menu)', message.lower()):
                response = await self._generate_help(message)
                return {
                    "success": True,
                    "response": response,
                    "intent": "help",
                    "confidence": 0.95,
                }
            
            # Check for explanation
            explanation_match = re.search(r'(what is|explain|definition|meaning|what does|how does)\s+(pod|pgi|dn|aging|kpi|delivery|warehouse|dealer|logistics)', message.lower())
            if explanation_match:
                term = explanation_match.group(2)
                response = await self._generate_explanation(term, message)
                return {
                    "success": True,
                    "response": response,
                    "intent": "explanation",
                    "confidence": 0.90,
                }
            
            # General conversational response
            response = await self._generate_conversational_response(message, context)
            return {
                "success": True,
                "response": response,
                "intent": "conversational",
                "confidence": 0.80,
            }
            
        except Exception as e:
            logger.error(f"❌ Groq process_query failed: {e}")
            return {
                "success": False,
                "response": "I encountered an error processing your request. Please try again.",
                "error": str(e)
            }
    
    async def enhance_response(self, business_data: Dict[str, Any], user_query: str, intent: str = "", entity: str = "") -> Dict[str, Any]:
        """
        Enhance a business data response with AI insights.
        
        Args:
            business_data: Business data from PostgreSQL
            user_query: Original user query
            intent: Detected intent
            entity: Entity name
        
        Returns:
            Enhanced response with AI insights
        """
        if not self.is_available() or not business_data:
            return {
                "success": False,
                "enhanced": False,
                "response": business_data.get("whatsapp_message", str(business_data)),
                "error": "Groq service not available or no data"
            }
        
        try:
            # Format business data as string
            if isinstance(business_data, dict):
                business_str = json.dumps(business_data, indent=2, default=str)
            else:
                business_str = str(business_data)
            
            # Truncate if too long
            if len(business_str) > 4000:
                business_str = business_str[:4000] + "... (truncated)"
            
            # Prepare prompt
            prompt = self.prompts.RESPONSE_ENHANCEMENT.format(
                user_query=user_query[:500],
                intent=intent or "unknown",
                entity=entity or "unknown",
                business_data=business_str[:4000],
            )
            
            # Get AI response
            enhanced = await self._call_api(
                system_prompt=self.prompts.SYSTEM_PROMPT,
                user_prompt=prompt,
                temperature=0.5,
                max_tokens=600,
            )
            
            if enhanced:
                return {
                    "success": True,
                    "enhanced": True,
                    "response": enhanced,
                    "original_data": business_data,
                }
            
            return {
                "success": False,
                "enhanced": False,
                "response": business_data.get("whatsapp_message", str(business_data)),
                "error": "AI enhancement failed"
            }
            
        except Exception as e:
            logger.error(f"❌ Groq enhance_response failed: {e}")
            return {
                "success": False,
                "enhanced": False,
                "response": business_data.get("whatsapp_message", str(business_data)),
                "error": str(e)
            }
    
    async def analyze_pending_dns(self, pending_data: Dict[str, Any]) -> str:
        """
        Analyze pending DNs and provide insights.
        
        Args:
            pending_data: Pending DNs data from PostgreSQL
        
        Returns:
            AI-analyzed summary
        """
        if not self.is_available():
            return "⚠️ AI analysis is currently unavailable."
        
        try:
            pending_str = json.dumps(pending_data, indent=2, default=str)
            if len(pending_str) > 4000:
                pending_str = pending_str[:4000] + "... (truncated)"
            
            prompt = self.prompts.PENDING_DNS_PROMPT.format(pending_data=pending_str)
            
            response = await self._call_api(
                system_prompt=self.prompts.SYSTEM_PROMPT,
                user_prompt=prompt,
                temperature=0.3,
                max_tokens=400,
            )
            
            return response or "Unable to analyze pending DNs at this time."
            
        except Exception as e:
            logger.error(f"❌ Groq analyze_pending_dns failed: {e}")
            return "Unable to analyze pending DNs at this time."
    
    async def generate_dealer_insights(self, dealer_name: str, dealer_data: Dict[str, Any]) -> str:
        """
        Generate AI insights for a dealer.
        
        Args:
            dealer_name: Name of the dealer
            dealer_data: Dealer performance data
        
        Returns:
            AI-generated insights
        """
        if not self.is_available():
            return "⚠️ AI insights are currently unavailable."
        
        try:
            dealer_str = json.dumps(dealer_data, indent=2, default=str)
            if len(dealer_str) > 4000:
                dealer_str = dealer_str[:4000] + "... (truncated)"
            
            prompt = self.prompts.DEALER_INSIGHTS_PROMPT.format(
                dealer_name=dealer_name,
                dealer_data=dealer_str,
            )
            
            response = await self._call_api(
                system_prompt=self.prompts.SYSTEM_PROMPT,
                user_prompt=prompt,
                temperature=0.4,
                max_tokens=500,
            )
            
            return response or f"Unable to generate insights for {dealer_name}."
            
        except Exception as e:
            logger.error(f"❌ Groq generate_dealer_insights failed: {e}")
            return f"Unable to generate insights for {dealer_name}."
    
    async def extract_entities(self, query: str) -> Dict[str, Any]:
        """
        Extract entities from a query using AI.
        
        Args:
            query: User query
        
        Returns:
            Extracted entities as dict
        """
        if not self.is_available():
            return {"intent": "unknown", "entity_type": "unknown", "entity_name": "", "time_frame": "", "conditions": []}
        
        try:
            prompt = self.prompts.QUERY_ENHANCEMENT.format(query=query[:500])
            
            response = await self._call_api(
                system_prompt="You are a logistics query analyzer. Extract entities accurately. Return ONLY JSON.",
                user_prompt=prompt,
                temperature=0.1,
                max_tokens=200,
            )
            
            if response:
                # Try to parse JSON
                try:
                    return json.loads(response)
                except json.JSONDecodeError:
                    # Try to extract JSON from response
                    json_match = re.search(r'\{.*\}', response, re.DOTALL)
                    if json_match:
                        try:
                            return json.loads(json_match.group())
                        except:
                            pass
                    
                    logger.warning(f"Failed to parse Groq entity extraction response: {response[:100]}")
            
            return {"intent": "unknown", "entity_type": "unknown", "entity_name": "", "time_frame": "", "conditions": []}
            
        except Exception as e:
            logger.error(f"❌ Groq extract_entities failed: {e}")
            return {"intent": "unknown", "entity_type": "unknown", "entity_name": "", "time_frame": "", "conditions": []}
    
    # ============================================================
    # PRIVATE METHODS
    # ============================================================
    
    async def _generate_greeting(self, message: str) -> str:
        """Generate a greeting response"""
        prompt = self.prompts.CONVERSATIONAL_PROMPT.format(user_message=message)
        
        response = await self._call_api(
            system_prompt=self.prompts.SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.7,
            max_tokens=200,
        )
        
        if response:
            return response
        
        return """👋 Welcome to HPK Logistics AI Assistant!

I can help you with:
📦 DN Tracking
🏪 Dealer Analytics
🏭 Warehouse Analytics
🏙️ City Analytics
📊 National KPIs
📋 Pending Deliveries

Type 'menu' to see all options or 'help' for commands."""
    
    async def _generate_help(self, message: str) -> str:
        """Generate a help response"""
        prompt = self.prompts.CONVERSATIONAL_PROMPT.format(user_message=message)
        
        response = await self._call_api(
            system_prompt=self.prompts.SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.5,
            max_tokens=300,
        )
        
        if response:
            return response
        
        return """📋 Available Commands:

📦 **DN Queries:**
• Send a DN number (8-12 digits)
• 'Pending DN', 'Pending PGI', 'Pending POD'

🏪 **Dealer Queries:**
• 'Dealer [name]'
• 'Top dealers', 'Bottom dealers'

🏭 **Warehouse Queries:**
• 'Warehouse [name]'

🏙️ **City Queries:**
• 'City [name]'

📊 **Analytics:**
• 'National KPI'
• 'Revenue', 'Total DNs'

📋 **Menu:**
• Type 'menu' to see all options

Need help? Just ask!"""
    
    async def _generate_explanation(self, term: str, context: str) -> str:
        """Generate an explanation response"""
        prompt = self.prompts.EXPLANATION_PROMPT.format(term=term, context=context)
        
        response = await self._call_api(
            system_prompt=self.prompts.SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.3,
            max_tokens=300,
        )
        
        if response:
            return response
        
        explanations = {
            "pod": """📖 **POD (Proof of Delivery)**

• Delivery confirmation document
• Signed by the receiver
• Confirms goods were received
• Critical for billing and closure

💡 Why it matters: POD confirms delivery completion and enables invoicing.""",
            "pgi": """📖 **PGI (Post Goods Issue)**

• Warehouse release confirmation
• Marks goods as shipped
• Triggers delivery process
• Updates inventory records

💡 Why it matters: PGI confirms goods have left the warehouse.""",
            "dn": """📖 **DN (Delivery Note)**

• Document accompanying delivery
• Lists items being delivered
• Tracks delivery status
• Contains customer and order details

💡 Why it matters: DN is the primary document for tracking deliveries.""",
        }
        
        return explanations.get(term.lower(), f"📖 **{term.upper()}**\n\nI'll help you understand this logistics term. Please ask with more context.")
    
    async def _generate_conversational_response(self, message: str, context: Optional[Dict]) -> str:
        """Generate a conversational response"""
        prompt = self.prompts.CONVERSATIONAL_PROMPT.format(user_message=message)
        
        response = await self._call_api(
            system_prompt=self.prompts.SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.7,
            max_tokens=250,
        )
        
        if response:
            return response
        
        return """I'm here to help with your logistics data!

You can ask me about:
📦 DN Tracking - Send any 8-12 digit number
🏪 Dealer Analytics - Dealer performance
🏭 Warehouse Analytics - Operations data
🏙️ City Analytics - City-level metrics
📊 National KPIs - Overall performance

Type 'help' to see all commands or 'menu' for options."""
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for Groq service"""
        return {
            "service": "groq_service",
            "version": "2.0",
            "enabled": ENABLE_GROQ,
            "available": self.is_available(),
            "api_key_set": GROQ_API_KEY is not None,
            "api_key_preview": GROQ_API_KEY[:8] + "..." if GROQ_API_KEY else None,
            "model": GROQ_MODEL,
            "timeout": GROQ_TIMEOUT,
            "cache_size": len(self._cache),
        }
    
    def get_service_metadata(self) -> Dict[str, Any]:
        """Get service metadata"""
        return {
            "service_name": "groq_service",
            "version": "2.0",
            "description": "Enterprise AI Enhancement Service",
            "capabilities": [
                "response_enhancement",
                "entity_extraction",
                "pending_analysis",
                "dealer_insights",
                "conversational_ai",
                "explanation_generation",
            ],
            "supported_models": ["llama3-70b-8192", "mixtral-8x7b-32768", "gemma2-9b-it"],
            "current_model": GROQ_MODEL,
            "enabled": ENABLE_GROQ,
            "available": self.is_available(),
        }


# ============================================================
# SINGLETON INSTANCE
# ============================================================

_groq_service: Optional[GroqService] = None
_groq_service_lock = threading.Lock()


def get_groq_service() -> GroqService:
    """Get singleton instance of GroqService"""
    global _groq_service
    if _groq_service is None:
        with _groq_service_lock:
            if _groq_service is None:
                try:
                    _groq_service = GroqService()
                    logger.info("✅ GroqService singleton initialized")
                except Exception as e:
                    logger.exception(f"❌ GroqService initialization failed: {e}")
                    _groq_service = GroqService.__new__(GroqService)
                    _groq_service._initialized = True
                    _groq_service._client = None
                    _groq_service._cache = TTLCache(maxsize=100, ttl=3600)
                    _groq_service.prompts = GroqPromptTemplates()
                    logger.warning("⚠️ GroqService running in degraded mode")
    return _groq_service


# ============================================================
# MODULE-LEVEL FUNCTIONS
# ============================================================

async def process_whatsapp_query(
    message: str,
    sender_id: Optional[str] = None,
    **context: Any,
) -> Dict[str, Any]:
    """
    Module-level function for processing WhatsApp queries.
    Backward compatible with older integrations.
    """
    service = get_groq_service()
    return await service.process_query(message, context)


async def enhance_response(
    response: Any,
    message: str = "",
    **context: Any,
) -> Dict[str, Any]:
    """
    Module-level function for enhancing responses.
    Backward compatible with older integrations.
    """
    service = get_groq_service()
    
    # Extract business data
    business_data = response if isinstance(response, dict) else {"response": str(response)}
    intent = context.get("intent", "")
    entity = context.get("entity", "")
    
    return await service.enhance_response(business_data, message or context.get("user_query", ""), intent, entity)


async def health_check() -> Dict[str, Any]:
    """Module-level health check"""
    service = get_groq_service()
    return service.health_check()


def get_service_metadata() -> Dict[str, Any]:
    """Module-level service metadata"""
    service = get_groq_service()
    return service.get_service_metadata()


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "GroqService",
    "get_groq_service",
    "process_whatsapp_query",
    "enhance_response",
    "health_check",
    "get_service_metadata",
    "GroqPromptTemplates",
]
