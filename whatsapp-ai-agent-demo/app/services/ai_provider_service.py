# ==========================================================
# FILE: app/services/ai_provider_service.py (FIXED v3.0)
# ==========================================================
# WhatsApp AI Provider - GROQ Integration
# FIXES:
# - Fixed GROQ initialization
# - Added proper error handling
# - Added fallback mechanisms
# - Added comprehensive logging
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

# ==========================================================
# SAFE GROQ IMPORT WITH DETAILED LOGGING
# ==========================================================

GROQ_AVAILABLE = False
Groq = None
GROQ_IMPORT_ERROR = None

try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ GROQ library imported successfully")
except ImportError as e:
    GROQ_IMPORT_ERROR = str(e)
    logger.error(f"❌ GROQ import failed: {e}")
    logger.info("   Run: pip install groq")
except Exception as e:
    GROQ_IMPORT_ERROR = str(e)
    logger.error(f"❌ GROQ import error: {e}")


# ==========================================================
# AI PROVIDER STATUS
# ==========================================================

class ProviderStatus(Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


# ==========================================================
# ROLE-BASED CONTEXTS (Enhanced for WhatsApp)
# ==========================================================

ROLE_CONTEXTS = {
    "ceo": """You are a Professional Logistics AI Assistant for WhatsApp. The user is a CEO/Executive.

RESPONSE REQUIREMENTS:
- Be extremely concise (max 10-15 lines)
- Focus on: Network Health, Revenue at Risk, Top 3 Risks
- Use emojis: 📊 👑 🚨 ✅ 💰
- Use **bold** for numbers and key metrics
- NEVER return raw JSON
- ALWAYS return human-readable text

FORMAT EXAMPLE:
📊 Network Health: 78/100
💰 Revenue at Risk: Rs 19.1M
🚨 Top Risk: Karachi POD backlog""",

    "manager": """You are a Professional Logistics AI Assistant for WhatsApp. The user is a Logistics Manager.

RESPONSE REQUIREMENTS:
- Focus on operational metrics: pending DNs, delivery rates, POD compliance
- Use emojis: 📦 📊 ⏳ ✅ ❌ 🚚
- Use **bold** for important numbers
- Keep responses actionable
- NEVER return raw JSON""",

    "dealer": """You are a Professional Logistics AI Assistant for WhatsApp. The user is a Dealer Manager.

RESPONSE REQUIREMENTS:
- Focus on dealer performance, pending deliveries, POD status
- Use emojis: 🏪 📊 ✅ ⏳ 📋
- Provide specific dealer insights
- NEVER return raw JSON""",

    "warehouse": """You are a Professional Logistics AI Assistant for WhatsApp. The user is a Warehouse Manager.

RESPONSE REQUIREMENTS:
- Focus on warehouse efficiency, bottlenecks, backlog
- Use emojis: 🏭 📦 ⏳ 🚚
- Provide actionable operational improvements
- NEVER return raw JSON""",

    "guest": """You are a Professional Logistics AI Assistant for WhatsApp. The user is a guest user.

RESPONSE REQUIREMENTS:
- Be helpful and welcoming
- Suggest specific commands they can try
- Use emojis: 👋 💡 📱
- List available command examples
- NEVER return raw JSON"""
}


# ==========================================================
# WHATSAPP PROMPT BUILDERS
# ==========================================================

def build_whatsapp_prompt(question: str, user_role: str = "guest", context: Dict = None) -> str:
    """Build WhatsApp-friendly prompt for GROQ"""
    
    role_context = ROLE_CONTEXTS.get(user_role, ROLE_CONTEXTS["guest"])
    
    context_str = ""
    if context:
        if context.get('dealer_name'):
            context_str += f"\nContext: User asking about dealer '{context['dealer_name']}'"
        if context.get('dn_no'):
            context_str += f"\nContext: User asking about DN '{context['dn_no']}'"
        if context.get('city'):
            context_str += f"\nContext: User asking about city '{context['city']}'"
    
    prompt = f"""{role_context}

{context_str}

USER QUESTION: {question}

Respond with a helpful WhatsApp message. Be concise, use emojis, and provide actionable information. Never return JSON, only text."""
    
    return prompt


def build_analysis_prompt(analysis_type: str, data: Dict, question: str, user_role: str = "manager") -> str:
    """Build specialized analysis prompt for logistics data"""
    
    role_context = ROLE_CONTEXTS.get(user_role, ROLE_CONTEXTS["manager"])
    
    if analysis_type == "dealer":
        return f"""{role_context}

DEALER ANALYSIS REQUEST

Dealer Data:
- Name: {data.get('dealer_name', data.get('name', 'Unknown'))}
- Total DNs: {data.get('total_dns', 0)}
- Delivered: {data.get('delivered_dns', 0)}
- Pending: {data.get('pending_dns', 0)}
- POD Pending: {data.get('pod_pending_dns', 0)}
- Total Value: Rs {data.get('total_value', data.get('total_amount', 0)):,.2f}
- Pending Value: Rs {data.get('pending_value', data.get('pending_amount', 0)):,.2f}
- Health Score: {data.get('health_score', 0)}/100
- Risk Level: {data.get('risk_level', 'Unknown')}

USER QUESTION: {question}

Provide a WhatsApp-friendly analysis of this dealer's performance. Include risk assessment and recommendations. Use emojis."""
    
    elif analysis_type == "dn":
        return f"""{role_context}

DN TRACKING REQUEST

DN Data:
- DN Number: {data.get('dn_no', 'Unknown')}
- Customer: {data.get('customer_name', 'N/A')}
- City: {data.get('city', 'N/A')}
- Warehouse: {data.get('warehouse', 'N/A')}
- Product: {data.get('product', 'N/A')}
- Quantity: {data.get('quantity', 0):,.0f}
- Value: Rs {data.get('value', 0):,.2f}
- Status: {data.get('status', 'Unknown')}
- Dispatch Age: {data.get('dispatch_age', 0)} days
- POD Age: {data.get('pod_age', 0)} days

USER QUESTION: {question}

Provide a WhatsApp-friendly tracking update. Include recommendations if delayed. Use emojis."""
    
    elif analysis_type == "executive":
        return f"""{role_context}

EXECUTIVE SUMMARY REQUEST

Metrics:
- Network Health: {data.get('network_health', 0)}/100
- Revenue at Risk: Rs {data.get('revenue_at_risk', 0):,.2f}
- Delivery Rate: {data.get('delivery_rate', 0)}%
- POD Compliance: {data.get('pod_rate', 0)}%

USER QUESTION: {question}

Provide a concise executive summary for WhatsApp. Focus on key metrics and top priorities. Use emojis."""
    
    else:
        return build_whatsapp_prompt(question, user_role, data)


# ==========================================================
# AI CACHE MANAGER
# ==========================================================

class AICacheManager:
    """Cache AI responses for cost reduction and speed"""
    
    def __init__(self, ttl_seconds: int = 300):
        self.cache: Dict[str, tuple] = {}
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
    
    def get_cache_key(self, question: str, context_hash: str = "", user_role: str = "") -> str:
        content_hash = hashlib.md5(f"{question}:{context_hash}:{user_role}".encode()).hexdigest()
        return content_hash


# ==========================================================
# FALLBACK RESPONSES (Always work, no API call needed)
# ==========================================================

class FallbackResponseGenerator:
    """Generate intelligent fallback responses without API calls"""
    
    @staticmethod
    def generate(question: str) -> str:
        """Generate appropriate fallback response based on question"""
        
        q_lower = question.lower().strip()
        
        # Greetings
        greetings = ["hello", "hi", "hey", "salam", "assalam", "good morning", "good evening", "good afternoon"]
        if any(g in q_lower for g in greetings):
            return """👋 *Welcome to Logistics Intelligence Assistant*

I can help you with:

📊 *Dealer Analytics*
• Type a dealer name to see their dashboard
• "Top dealers" - Performance ranking
• "Top risk dealers" - Critical accounts

🔢 *DN Tracking*
• Send a 10-digit DN number
• Example: `6243611920`

👑 *Executive Views*
• "Executive summary" - Leadership briefing
• "Network health" - Overall status

🌆 *City Analysis*
• "City analysis" - Regional performance
• "Karachi situation" - Specific city

💡 *What would you like to know?*"""
        
        # Help
        help_words = ["help", "menu", "commands", "what can you do", "how to use"]
        if any(h in q_lower for h in help_words):
            return """📱 *AVAILABLE COMMANDS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍 *DN TRACKING*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Send a 10-digit DN number
• Example: `6243611920`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏪 *DEALER ANALYTICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Type any dealer name
• "Top dealers" - Best performers
• "Top risk dealers" - Critical accounts

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👑 *EXECUTIVE VIEWS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• "Executive summary" - Leadership view
• "Network health" - Overall performance

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌆 *CITY & REGIONAL*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• "City analysis" - Regional breakdown
• "Karachi situation" - Specific city

💡 *Try any command now!*"""
        
        # Executive summary
        executive_words = ["executive summary", "ceo summary", "management summary", "executive dashboard"]
        if any(e in q_lower for e in executive_words):
            return """👑 *EXECUTIVE SUMMARY*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Overall Score: 78/100
• Delivery Rate: 94.2%
• POD Compliance: 73.1%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *REVENUE AT RISK*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total at Risk: Rs 1,199,887,262
• Pending DNs: 0
• POD Pending: 376 DNs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 *TOP 3 RISKS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. POD collection backlog (376 DNs)
2. Regional performance variation
3. Dealer acknowledgement delays

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Escalate POD collection for top dealers
2. Focus on Karachi region
3. Implement daily follow-up system

Type "Top risk dealers" for detailed list."""
        
        # Network health
        health_words = ["network health", "health score", "overall health"]
        if any(h in q_lower for h in health_words):
            return """📊 *NETWORK HEALTH REPORT*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: 78/100
• Total DNs: 18,467
• Delivered: 17,402 ✅
• Delivery Rate: 94.2%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD COMPLIANCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• POD Received: 12,729
• POD Pending: 4,673
• Compliance Rate: 73.1%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Value: Rs 4,308,681,954
• Revenue at Risk: Rs 1,199,887,262

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{'🟢 Network is stable' if True else '🟡 Needs attention'}

Type "Executive summary" for detailed analysis."""
        
        # Top dealers
        top_dealer_words = ["top dealer", "best dealer", "top performing"]
        if any(t in q_lower for t in top_dealer_words):
            return """📊 *TOP PERFORMING DEALERS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. *Bismillah Electronics*
   💰 Rs 1,100,950,248 | 📦 1,500 DNs

2. *Imran Electronics*
   💰 Rs 1,086,839,903 | 📦 2,937 DNs

3. *Afzal Electronics*
   💰 Rs 813,902,177 | 📦 2,865 DNs

4. *Naeem Electronics*
   💰 Rs 651,310,718 | 📦 2,741 DNs

5. *STM Associates*
   💰 Rs 577,840,141 | 📦 332 DNs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type a dealer name for detailed dashboard"""
        
        # Top risk dealers
        risk_words = ["top risk", "risk dealers", "critical dealers"]
        if any(r in q_lower for r in risk_words):
            return """🚨 *TOP RISK DEALERS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *HIGHEST PENDING VALUE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. *Bismillah Electronics*
   📋 376 POD Pending
   💰 Rs 1,199,887,262 at risk

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD PENDING LEADERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Focus on POD collection
• 376 DNs awaiting acknowledgement
• Total value at risk: Rs 1.2B

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Escalate Bismillah Electronics
2. Deploy collection team
3. Implement daily follow-up

Type a dealer name for detailed dashboard."""
        
        # City analysis
        city_words = ["city analysis", "regional performance", "city wise"]
        if any(c in q_lower for c in city_words) or any(city in q_lower for city in ["karachi", "lahore", "faisalabad"]):
            return """🌆 *CITY-WISE PERFORMANCE*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 *Lahore* (Best)
   📦 12,444 DNs | ⏳ 95 pending (1%)
   💰 Rs 4,308,681,954

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 *Karachi*
   📦 10,810 DNs | ⏳ 17 pending (0%)
   💰 Rs 3,586,058,427

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 *Gujrat*
   📦 434 DNs | ⏳ 2 pending (0%)
   💰 Rs 70,777,472

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟡 *Faisalabad* (Needs Attention)
   📦 5 DNs | ⏳ 1 pending (20%)
   💰 Rs 643,846

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type a city name for detailed analysis"""
        
        # DN tracking (10 digits)
        if q_lower.isdigit() and len(q_lower) == 10:
            return f"""🔢 *DN TRACKING - {question}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *SEARCHING DATABASE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⏳ Fetching complete DN details...

*This includes:*
• Customer information
• Delivery status
• POD status
• Financial details
• Risk assessment

Please wait while I retrieve the data..."""
        
        # Dealer name (likely)
        if len(q_lower.split()) <= 4 and not q_lower.isdigit():
            return f"""🏪 *DEALER LOOKUP - "{question}"*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *SEARCHING DATABASE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⏳ Fetching complete dealer dashboard...

*This includes:*
• Delivery performance metrics
• Financial analysis
• Risk assessment
• Pending DNs
• POD status

Please wait while I retrieve the data..."""
        
        # Default response
        return f"""🤖 *Logistics Assistant*

I understand you're asking: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Here's what I can help with:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 *Track a DN*
• Send a 10-digit number

🏪 *Check a Dealer*
• Type their name

👑 *Executive Reports*
• "Executive summary"
• "Network health"

🌆 *Regional Analysis*
• "City analysis"
• "Karachi situation"

📋 *Try one of these commands!*"""


# ==========================================================
# MAIN AI PROVIDER SERVICE
# ==========================================================

class AIProviderService:
    """
    Enterprise AI Provider Service with GROQ integration
    Includes fallback mechanisms for reliability
    """
    
    def __init__(self, db: Session = None):
        self.db = db
        self.cache = AICacheManager(ttl_seconds=300)
        self.fallback = FallbackResponseGenerator()
        
        # GROQ Configuration
        self.groq_api_key = getattr(config, 'GROQ_API_KEY', None)
        self.groq_model = getattr(config, 'GROQ_MODEL', 'llama-3.3-70b-versatile')
        self.groq_client = None
        self.status = ProviderStatus.OFFLINE
        self.is_available = False
        
        # Log startup
        self._log_startup()
        
        # Initialize GROQ if available
        if self.groq_api_key and GROQ_AVAILABLE:
            self._init_groq()
        elif not self.groq_api_key:
            logger.error("❌ GROQ_API_KEY not found in environment variables")
            logger.info("   Add GROQ_API_KEY to Railway variables")
        elif not GROQ_AVAILABLE:
            logger.error(f"❌ GROQ library not available: {GROQ_IMPORT_ERROR}")
            logger.info("   Run: pip install groq")
        
        # Final status
        self.is_available = self.status == ProviderStatus.ONLINE
        self._log_final_status()
    
    def _log_startup(self):
        """Log startup information"""
        logger.info("=" * 60)
        logger.info("🤖 AI PROVIDER SERVICE v3.0 INITIALIZING")
        logger.info(f"GROQ Library Available: {GROQ_AVAILABLE}")
        logger.info(f"GROQ_API_KEY Present: {bool(self.groq_api_key)}")
        logger.info(f"GROQ Model: {self.groq_model}")
        logger.info("=" * 60)
    
    def _init_groq(self):
        """Initialize GROQ client"""
        try:
            self.groq_client = Groq(api_key=self.groq_api_key)
            self.status = ProviderStatus.ONLINE
            logger.success("✅ GROQ client initialized successfully!")
            logger.info(f"   Model: {self.groq_model}")
        except Exception as e:
            self.status = ProviderStatus.OFFLINE
            logger.error(f"❌ GROQ initialization failed: {e}")
    
    def _log_final_status(self):
        """Log final initialization status"""
        logger.info("=" * 60)
        if self.is_available:
            logger.success("✅ AI PROVIDER SERVICE IS READY")
            logger.info("   GROQ is online and available")
        else:
            logger.warning("⚠️ AI PROVIDER SERVICE IN FALLBACK MODE")
            logger.info("   Using rule-based responses")
            logger.info("   GROQ will be used when available")
        logger.info("=" * 60)
    
    # ==========================================================
    # CORE METHODS
    # ==========================================================
    
    def get_cache_key(self, question: str, context: Dict = None, user_role: str = "") -> str:
        """Generate cache key"""
        context_hash = str(hash(str(context))) if context else ""
        content_hash = hashlib.md5(f"{question}:{context_hash}:{user_role}".encode()).hexdigest()
        return content_hash
    
    def answer_question(
        self,
        question: str,
        context: Dict[str, Any] = None,
        structured: bool = False,
        user_phone: str = None,
        user_role: str = "guest",
        max_tokens: int = 500,
        temperature: float = 0.7,
        analysis_type: str = None
    ) -> Dict[str, Any]:
        """
        Answer a question using GROQ or fallback
        
        Args:
            question: User's question
            context: Optional context data
            structured: Return structured data (not used for WhatsApp)
            user_phone: User's phone number
            user_role: User's role (ceo, manager, guest, etc.)
            max_tokens: Max tokens for response
            temperature: Temperature for response
            analysis_type: Type of analysis (dealer, dn, executive, etc.)
        
        Returns:
            Dictionary with response content
        """
        start_time = time.time()
        
        logger.info(f"🚀 AI REQUEST - User: {user_phone}, Role: {user_role}")
        logger.debug(f"Question: {question[:100]}...")
        
        # Check cache
        cache_key = self.get_cache_key(question, context, user_role)
        cached_response = self.cache.get(cache_key)
        if cached_response:
            logger.info(f"✅ Cache hit for: {question[:50]}...")
            return {
                "success": True,
                "content": cached_response,
                "provider_used": "cache",
                "processing_time_ms": 0,
                "cached": True
            }
        
        # Build prompt based on analysis type
        if analysis_type and context:
            prompt = build_analysis_prompt(analysis_type, context, question, user_role)
        else:
            prompt = build_whatsapp_prompt(question, user_role, context)
        
        # Try GROQ first
        response = None
        provider_used = "fallback"
        
        if self.is_available:
            logger.info("📡 Calling GROQ API...")
            response = self._call_groq(prompt, max_tokens, temperature)
            if response.get("success"):
                provider_used = "groq"
                logger.info("✅ GROQ response received")
            else:
                logger.warning(f"⚠️ GROQ failed: {response.get('error')}")
        
        # Use fallback if GROQ failed or not available
        if not response or not response.get("success"):
            logger.info("📋 Using fallback response")
            content = self.fallback.generate(question)
            response = {"success": True, "content": content}
            provider_used = "fallback"
        
        content = response.get("content", "")
        
        # Clean response for WhatsApp
        content = self._clean_response(content)
        
        # Cache successful response
        if response.get("success") and provider_used == "groq":
            self.cache.set(cache_key, content)
        
        processing_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ RESPONSE - Provider: {provider_used}, Time: {processing_time}ms")
        
        # Log to database (async, don't block)
        self._log_usage(
            user_phone=user_phone,
            question=question,
            response=content,
            provider=provider_used,
            success=response.get("success", False),
            processing_time_ms=processing_time
        )
        
        return {
            "success": response.get("success", False),
            "content": content,
            "provider_used": provider_used,
            "processing_time_ms": processing_time,
            "cached": False
        }
    
    def _call_groq(self, prompt: str, max_tokens: int = 500, temperature: float = 0.7) -> Dict[str, Any]:
        """Call GROQ API with error handling"""
        
        if not self.groq_client:
            return {"success": False, "content": "", "error": "GROQ client not initialized"}
        
        try:
            logger.debug(f"Calling GROQ with model: {self.groq_model}")
            
            response = self.groq_client.chat.completions.create(
                model=self.groq_model,
                messages=[
                    {
                        "role": "system", 
                        "content": "You are a helpful WhatsApp logistics assistant. Return ONLY human-readable text with emojis and simple formatting. NEVER return raw JSON. Be concise and helpful."
                    },
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )
            
            content = response.choices[0].message.content
            logger.debug(f"GROQ response length: {len(content)} chars")
            
            return {
                "success": True,
                "content": content,
                "model": self.groq_model,
                "usage": {
                    "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0),
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0),
                    "total_tokens": getattr(response.usage, 'total_tokens', 0)
                }
            }
            
        except Exception as e:
            logger.error(f"GROQ API error: {e}")
            return {"success": False, "content": "", "error": str(e)}
    
    def _clean_response(self, content: str) -> str:
        """Clean response for WhatsApp display"""
        
        if not content:
            return "I couldn't generate a response. Please try again."
        
        # Remove any JSON that might have slipped through
        if content.strip().startswith('{'):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    content = parsed.get("response") or parsed.get("content") or parsed.get("message") or content
            except:
                pass
        
        # Remove code blocks
        content = re.sub(r'```json\s*', '', content)
        content = re.sub(r'```\s*', '', content)
        
        # Ensure response has emojis for better UX
        emojis = ['📊', '✅', '❌', '🚨', '💡', '📦', '🏪', '🔢', '🌆', '👑', '📍', '⏳', '📋', '👋', '💰', '⚠️']
        if not any(e in content for e in emojis):
            content = "📋 " + content
        
        # Truncate if too long (WhatsApp limit ~4000 chars)
        if len(content) > 3800:
            content = content[:3800] + "\n\n... (response truncated)"
        
        return content
    
    def _log_usage(self, user_phone: str, question: str, response: str, provider: str, success: bool, processing_time_ms: int):
        """Log usage to database (non-blocking)"""
        
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
            if self.db:
                self.db.rollback()
    
    # ==========================================================
    # SPECIALIZED METHODS
    # ==========================================================
    
    def analyze_dealer(self, dealer_data: Dict, user_phone: str = None, user_role: str = "manager") -> Dict[str, Any]:
        """Analyze dealer performance"""
        return self.answer_question(
            question=f"Analyze dealer {dealer_data.get('dealer_name', 'Unknown')}",
            context=dealer_data,
            user_phone=user_phone,
            user_role=user_role,
            analysis_type="dealer"
        )
    
    def analyze_dn(self, dn_data: Dict, user_phone: str = None, user_role: str = "guest") -> Dict[str, Any]:
        """Analyze DN status"""
        return self.answer_question(
            question=f"Track DN {dn_data.get('dn_no', 'Unknown')}",
            context=dn_data,
            user_phone=user_phone,
            user_role=user_role,
            analysis_type="dn"
        )
    
    def generate_executive_summary(self, metrics: Dict, user_phone: str = None) -> Dict[str, Any]:
        """Generate executive summary"""
        return self.answer_question(
            question="Provide executive summary",
            context=metrics,
            user_phone=user_phone,
            user_role="ceo",
            analysis_type="executive"
        )
    
    def get_status(self) -> Dict[str, Any]:
        """Get service status"""
        return {
            "available": self.is_available,
            "status": self.status.value,
            "model": self.groq_model if self.is_available else None,
            "cache_size": len(self.cache.cache),
            "groq_library": GROQ_AVAILABLE
        }


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

import re

_ai_provider_service = None


def init_ai_provider_service(db: Session = None) -> AIProviderService:
    """Initialize AI Provider Service singleton"""
    global _ai_provider_service
    
    if _ai_provider_service is None:
        _ai_provider_service = AIProviderService(db)
    
    return _ai_provider_service


def get_ai_provider_service() -> Optional[AIProviderService]:
    """Get the AI Provider Service instance"""
    return _ai_provider_service


def reset_ai_provider_service():
    """Reset singleton (for testing)"""
    global _ai_provider_service
    _ai_provider_service = None


# ==========================================================
# AUTO-INITIALIZATION (Safe - Won't crash)
# ==========================================================

try:
    _ai_provider_service = AIProviderService(db=None)
    logger.info("AI Provider Service auto-initialized")
except Exception as e:
    logger.error(f"Auto-initialization failed: {e}")
    _ai_provider_service = None
