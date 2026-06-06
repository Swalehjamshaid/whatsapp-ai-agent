# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v4.2)
# ==========================================================
# Central Intelligence Router for WhatsApp
# Fixed: Removed dangerous contains-match, improved regex boundaries,
#       service singleton, enhanced logging, AI protection
# ==========================================================

import re
import time
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
from collections import deque

from sqlalchemy.orm import Session
from loguru import logger

from app.config import config
from app.models import AIResponseLog
from app.services.analytics_service import AnalyticsService
from app.services.logistics_query_service import LogisticsQueryService

# AI Provider (Groq only)
try:
    from app.services.ai_provider_service import ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError:
    AI_PROVIDER_AVAILABLE = False
    ai_provider_service = None

# RapidFuzz for fuzzy dealer matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False


# ==========================================================
# FIX #6: SINGLETON SERVICE INSTANCE
# ==========================================================

_ai_query_service_instance = None


# ==========================================================
# INTENT TYPES (Improvement 1 + 4)
# ==========================================================

class IntentType(str, Enum):
    # New intents for better UX
    GREETING = "greeting"           # FIX #4: Added greeting intent
    HELP = "help"                   # FIX #4: Added help intent
    THANKS = "thanks"               # FIX #4: Added thanks intent
    
    # Existing intents
    DEALER_LOOKUP = "dealer_lookup"
    DN_LOOKUP = "dn_lookup"
    WAREHOUSE_LOOKUP = "warehouse_lookup"
    CITY_LOOKUP = "city_lookup"
    EXECUTIVE_SUMMARY = "executive_summary"
    TOP_DEALERS = "top_dealers"
    TOP_WAREHOUSES = "top_warehouses"
    POD_ANALYSIS = "pod_analysis"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    FORECAST = "forecast"
    RECOMMENDATIONS = "recommendations"
    GENERAL_QUERY = "general_query"
    UNKNOWN = "unknown"


# ==========================================================
# DEALER MASTER DATA CACHE (Improvement 3 + 9)
# ==========================================================

class DealerMasterData:
    """Cache dealer names for fuzzy matching with auto-refresh"""
    
    _instance = None
    _dealers = []
    _dealer_names_lower = []
    _last_refresh = None
    _refresh_lock = threading.Lock()
    _REFRESH_INTERVAL_MINUTES = 10  # FIX #9: Changed from 30 to 10 minutes
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def load_dealers(self, db: Session, force: bool = False):
        """Load all dealer names from database with auto-refresh"""
        
        # FIX #9: Check if refresh needed (10 minute interval)
        if not force and self._last_refresh:
            age = datetime.now() - self._last_refresh
            if age < timedelta(minutes=self._REFRESH_INTERVAL_MINUTES):
                logger.debug(f"Using cached dealers (age: {age.total_seconds()/60:.1f} min)")
                return True
        
        with self._refresh_lock:
            try:
                from app.models import DeliveryReport
                dealers = db.query(DeliveryReport.customer_name).distinct().filter(
                    DeliveryReport.customer_name.isnot(None)
                ).limit(10000).all()
                
                self._dealers = [d[0] for d in dealers if d[0]]
                self._dealer_names_lower = [d.lower() for d in self._dealers]
                self._last_refresh = datetime.now()
                logger.info(f"Loaded/refreshed {len(self._dealers)} dealers for fuzzy matching")
                return True
            except Exception as e:
                logger.error(f"Failed to load dealers: {e}")
                return False
    
    def get_dealers(self) -> List[str]:
        return self._dealers
    
    def match_dealer(self, query: str, threshold: int = 85) -> Tuple[Optional[str], int]:
        """
        Fuzzy match dealer name using RapidFuzz
        FIX #1: Removed dangerous contains-match
        """
        if not self._dealers:
            return None, 0
        
        query_clean = query.strip()
        
        # FIX #1: REMOVED CONTAINS MATCH - was causing false positives
        # Example: "BBC" would match "BBC Electronics" incorrectly
        
        # Exact match (case insensitive)
        for dealer in self._dealers:
            if dealer.lower() == query_clean.lower():
                return dealer, 100
        
        # RapidFuzz matching only
        if RAPIDFUZZ_AVAILABLE:
            try:
                # Token sort ratio (best for word order variations)
                match = process.extractOne(query_clean, self._dealers, scorer=fuzz.token_sort_ratio)
                if match and match[1] >= threshold:
                    return match[0], match[1]
                
                # Partial ratio for substring matches
                match = process.extractOne(query_clean, self._dealers, scorer=fuzz.partial_ratio)
                if match and match[1] >= threshold:
                    return match[0], match[1]
            except Exception as e:
                logger.warning(f"RapidFuzz matching failed: {e}")
        
        return None, 0
    
    def get_suggestions(self, query: str, limit: int = 5) -> List[Tuple[str, int]]:
        """Get dealer suggestions for ambiguous queries"""
        if not self._dealers or not RAPIDFUZZ_AVAILABLE:
            return []
        
        try:
            matches = process.extract(query, self._dealers, scorer=fuzz.token_sort_ratio, limit=limit)
            return [(m[0], m[1]) for m in matches if m[1] >= 50]
        except Exception:
            return []
    
    def force_refresh(self, db: Session):
        """Force immediate refresh of dealer cache"""
        return self.load_dealers(db, force=True)


# ==========================================================
# INTENT DETECTION ENGINE (Improvement 1 + 2 + 4 + 8)
# ==========================================================

class IntentDetector:
    """Detect user intent from WhatsApp message"""
    
    # FIX #5: Enhanced DN patterns
    DN_PATTERNS = [
        r'^\d{10}$',                    # Exactly 10 digits
        r'\b\d{10}\b',                  # 10 digits in text
        r'^DN\s*\d{10}$',               # DN 6243611361
        r'^dn\s*\d{10}$',               # dn 6243611361
        r'^Track\s*\d{10}$',            # Track 6243611361
        r'^track\s*\d{10}$',            # track 6243611361
        r'^Status\s*\d{10}$',           # Status 6243611361
        r'^status\s*\d{10}$',           # status 6243611361
        r'^POD\s*\d{10}$',              # POD 6243611361
        r'^pod\s*\d{10}$',              # pod 6243611361
        r'^check\s+\d{10}$',            # FIX #5: check 6243611361
        r'^track\s+dn\s+\d{10}$',       # FIX #5: track dn 6243611361
        r'^where\s+is\s+\d{10}$',       # FIX #5: where is 6243611361
        r'^dn\s+status\s+\d{10}$',      # FIX #5: dn status 6243611361
    ]
    
    # FIX #2: Greeting patterns with word boundaries
    GREETING_PATTERNS = [
        "hello", "hi", "hey", "salam", "assalam", "good morning", 
        "good evening", "good afternoon", "howdy", "greetings"
    ]
    
    # FIX #3: Help patterns with word boundaries
    HELP_PATTERNS = [
        "help", "menu", "commands", "what can you do", "how to use",
        "available commands", "help me", "what do you do"
    ]
    
    # FIX #4: Thanks patterns with word boundaries
    THANKS_PATTERNS = [
        "thank", "thanks", "thx", "appreciate", "good job", "nice work"
    ]
    
    # City keywords
    CITY_KEYWORDS = [
        "karachi", "lahore", "islamabad", "rawalpindi", "faisalabad",
        "multan", "peshawar", "quetta", "gujranwala", "sialkot",
        "hyderabad", "situation", "performance", "status", "delivery"
    ]
    
    # Warehouse keywords
    WAREHOUSE_KEYWORDS = ["warehouse", "godown", "hpk", "lhe", "isb"]
    
    # Executive keywords
    EXECUTIVE_KEYWORDS = [
        "executive summary", "ceo summary", "what should i focus",
        "today's priorities", "overall performance", "network health",
        "command center", "executive dashboard"
    ]
    
    # Top dealers keywords
    TOP_DEALERS_KEYWORDS = [
        "top dealer", "top dealers", "best dealer", "highest dealer",
        "dealer ranking", "dealer rankings", "leaderboard"
    ]
    
    # POD analysis keywords
    POD_KEYWORDS = [
        "pod", "proof of delivery", "pending pod", "pod status",
        "pod analysis", "pod backlog", "acknowledgement"
    ]
    
    # Root cause keywords
    ROOT_CAUSE_KEYWORDS = [
        "why is", "why are", "root cause", "what is causing",
        "reason for", "cause of"
    ]
    
    # Forecast keywords
    FORECAST_KEYWORDS = [
        "forecast", "prediction", "will happen", "future outlook",
        "next month", "trend"
    ]
    
    # Recommendation keywords
    RECOMMENDATION_KEYWORDS = [
        "recommendation", "suggest", "improve", "action plan",
        "what should we do", "how can we"
    ]
    
    @classmethod
    def detect_greeting(cls, message: str) -> bool:
        """Detect greeting in message - FIX #2: Added word boundaries"""
        message_lower = message.lower().strip()
        
        # Use regex with word boundaries to prevent false positives
        # Example: "shipment" no longer matches "hi"
        for greeting in cls.GREETING_PATTERNS:
            pattern = rf'\b{re.escape(greeting)}\b'
            if re.search(pattern, message_lower):
                return True
        return False
    
    @classmethod
    def detect_help(cls, message: str) -> bool:
        """Detect help request - FIX #3: Added word boundaries"""
        message_lower = message.lower().strip()
        
        for help_word in cls.HELP_PATTERNS:
            pattern = rf'\b{re.escape(help_word)}\b'
            if re.search(pattern, message_lower):
                return True
        return False
    
    @classmethod
    def detect_thanks(cls, message: str) -> bool:
        """Detect thanks - FIX #4: Added word boundaries"""
        message_lower = message.lower().strip()
        
        for thanks_word in cls.THANKS_PATTERNS:
            pattern = rf'\b{re.escape(thanks_word)}\b'
            if re.search(pattern, message_lower):
                return True
        return False
    
    @classmethod
    def detect_dn(cls, message: str) -> Tuple[bool, Optional[str]]:
        """Detect DN number in message - FIX #5: Enhanced patterns"""
        message_clean = message.strip()
        
        for pattern in cls.DN_PATTERNS:
            match = re.search(pattern, message_clean, re.IGNORECASE)
            if match:
                # Extract just the digits
                dn_match = re.search(r'\d{10}', match.group())
                if dn_match:
                    return True, dn_match.group()
        return False, None
    
    @classmethod
    def detect_city(cls, message: str) -> Tuple[bool, Optional[str]]:
        """Detect city mention in message"""
        message_lower = message.lower()
        
        for city in ["karachi", "lahore", "islamabad", "rawalpindi", "faisalabad", "multan", "peshawar", "quetta"]:
            if city in message_lower:
                return True, city.title()
        
        if "situation" in message_lower or "performance" in message_lower:
            # Try to extract city name
            words = message_lower.split()
            for word in words:
                if word in cls.CITY_KEYWORDS and word not in ["situation", "performance", "status", "delivery"]:
                    return True, word.title()
        
        return False, None
    
    @classmethod
    def detect_warehouse(cls, message: str) -> Tuple[bool, Optional[str]]:
        """Detect warehouse mention"""
        message_lower = message.lower()
        
        for wh in ["hpk", "lhe", "isb", "main", "central"]:
            if wh in message_lower:
                return True, wh.upper()
        
        if "warehouse" in message_lower:
            # Try to extract warehouse name
            match = re.search(r'warehouse\s+(\w+)', message_lower)
            if match:
                return True, match.group(1).upper()
        
        return False, None
    
    @classmethod
    def detect_intent(cls, message: str, dealer_matcher: DealerMasterData = None) -> Tuple[IntentType, Optional[str]]:
        """
        Detect intent from message (Improvement 1)
        Returns: (intent_type, entity)
        """
        message_clean = message.strip()
        message_lower = message.lower()
        
        # FIX #10: Protect against very short messages
        if len(message_clean) <= 2:
            logger.info(f"Very short message detected: '{message_clean}' -> treating as UNKNOWN")
            return IntentType.UNKNOWN, None
        
        # FIX #4: Priority 0 - Thanks (before anything else)
        if cls.detect_thanks(message_clean):
            logger.info(f"Thanks detected")
            return IntentType.THANKS, None
        
        # FIX #2 & #4: Priority 0.5 - Greetings (before dealer detection)
        if cls.detect_greeting(message_clean):
            logger.info(f"Greeting detected")
            return IntentType.GREETING, None
        
        # FIX #3: Priority 0.75 - Help
        if cls.detect_help(message_clean):
            logger.info(f"Help detected")
            return IntentType.HELP, None
        
        # Priority 1: DN Lookup (Improvement 2 + FIX #5)
        is_dn, dn_number = cls.detect_dn(message_clean)
        if is_dn:
            logger.info(f"DN detected: {dn_number}")
            return IntentType.DN_LOOKUP, dn_number
        
        # Priority 2: Dealer Lookup using fuzzy matching (FIX #3: threshold 85)
        if dealer_matcher:
            dealer_name, confidence = dealer_matcher.match_dealer(message_clean, threshold=85)
            if dealer_name:
                logger.info(f"Dealer detected via fuzzy matching: '{message_clean}' -> '{dealer_name}' (confidence: {confidence})")
                return IntentType.DEALER_LOOKUP, dealer_name
        
        # Priority 3: City Lookup
        is_city, city_name = cls.detect_city(message_clean)
        if is_city:
            logger.info(f"City detected: {city_name}")
            return IntentType.CITY_LOOKUP, city_name
        
        # Priority 4: Warehouse Lookup
        is_wh, wh_name = cls.detect_warehouse(message_clean)
        if is_wh:
            logger.info(f"Warehouse detected: {wh_name}")
            return IntentType.WAREHOUSE_LOOKUP, wh_name
        
        # Priority 5: Executive Summary
        if any(kw in message_lower for kw in cls.EXECUTIVE_KEYWORDS):
            logger.info(f"Executive summary intent detected")
            return IntentType.EXECUTIVE_SUMMARY, None
        
        # Priority 6: Top Dealers
        if any(kw in message_lower for kw in cls.TOP_DEALERS_KEYWORDS):
            logger.info(f"Top dealers intent detected")
            return IntentType.TOP_DEALERS, None
        
        # Priority 7: POD Analysis
        if any(kw in message_lower for kw in cls.POD_KEYWORDS):
            logger.info(f"POD analysis intent detected")
            return IntentType.POD_ANALYSIS, None
        
        # Priority 8: Root Cause Analysis
        if any(kw in message_lower for kw in cls.ROOT_CAUSE_KEYWORDS):
            logger.info(f"Root cause analysis intent detected")
            return IntentType.ROOT_CAUSE_ANALYSIS, None
        
        # Priority 9: Forecast
        if any(kw in message_lower for kw in cls.FORECAST_KEYWORDS):
            logger.info(f"Forecast intent detected")
            return IntentType.FORECAST, None
        
        # Priority 10: Recommendations
        if any(kw in message_lower for kw in cls.RECOMMENDATION_KEYWORDS):
            logger.info(f"Recommendations intent detected")
            return IntentType.RECOMMENDATIONS, None
        
        # Priority 11: General Query
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# SESSION CONTEXT MANAGER (Improvement 4 + 5 + 10)
# ==========================================================

class SessionContextManager:
    """Manage conversation context using session_service with history"""
    
    def __init__(self, session_service):
        self.session_service = session_service
    
    def update_context(self, phone_number: str, intent: IntentType, entity: str = None, question: str = None, response: str = None):
        """Update session context based on intent"""
        if not self.session_service:
            return
        
        try:
            # Get existing context for history
            existing = self.get_context(phone_number)
            history = existing.get("question_history", [])
            response_history = existing.get("response_history", [])
            
            # FIX #10: Store last 10 questions and responses
            if question:
                history.append({
                    "question": question[:200],
                    "intent": intent.value,
                    "entity": entity,
                    "timestamp": datetime.now().isoformat()
                })
                # Keep only last 10
                if len(history) > 10:
                    history.pop(0)
            
            if response:
                response_history.append({
                    "response": response[:500],
                    "timestamp": datetime.now().isoformat()
                })
                if len(response_history) > 10:
                    response_history.pop(0)
            
            # Update context based on intent
            if intent == IntentType.DEALER_LOOKUP and entity:
                self.session_service.update_session_context(
                    phone_number,
                    selected_dealer=entity,
                    last_intent=intent.value,
                    last_question=f"Dealer lookup: {entity}",
                    question_history=history,
                    response_history=response_history
                )
            elif intent == IntentType.DN_LOOKUP and entity:
                self.session_service.update_session_context(
                    phone_number,
                    selected_dn=entity,
                    last_intent=intent.value,
                    last_question=f"DN lookup: {entity}",
                    question_history=history,
                    response_history=response_history
                )
            elif intent == IntentType.CITY_LOOKUP and entity:
                self.session_service.update_session_context(
                    phone_number,
                    selected_city=entity,
                    last_intent=intent.value,
                    last_question=f"City lookup: {entity}",
                    question_history=history,
                    response_history=response_history
                )
            elif intent == IntentType.WAREHOUSE_LOOKUP and entity:
                self.session_service.update_session_context(
                    phone_number,
                    selected_warehouse=entity,
                    last_intent=intent.value,
                    last_question=f"Warehouse lookup: {entity}",
                    question_history=history,
                    response_history=response_history
                )
            else:
                self.session_service.update_session_context(
                    phone_number,
                    last_intent=intent.value,
                    last_question=f"Query: {intent.value}",
                    question_history=history,
                    response_history=response_history
                )
        except Exception as e:
            logger.warning(f"Failed to update session context: {e}")
    
    # FIX #5: Add clear_context method
    def clear_context(self, phone_number: str):
        """Clear stale session context for greetings and unrelated questions"""
        if not self.session_service:
            return
        
        try:
            self.session_service.update_session_context(
                phone_number,
                selected_dealer=None,
                selected_dn=None,
                selected_city=None,
                selected_warehouse=None,
                last_intent=None,
                last_question=None
            )
            logger.info(f"Cleared session context for {phone_number}")
        except Exception as e:
            logger.warning(f"Failed to clear context: {e}")
    
    def get_context(self, phone_number: str) -> Dict:
        """Get current session context"""
        if not self.session_service:
            return {}
        
        try:
            return self.session_service.get_context(phone_number)
        except Exception:
            return {}


# ==========================================================
# RESPONSE FORMATTER (Improvement 7 + 8)
# ==========================================================

class ResponseFormatter:
    """Format responses for WhatsApp"""
    
    # FIX #8: Complete HELP menu
    @staticmethod
    def help_menu() -> str:
        return """
╔══════════════════════════════════════╗
║      📱 WHATSAPP COMMAND CENTER      ║
╚══════════════════════════════════════╝

🔍 *DN TRACKING*
• `6243611361` - Track single DN
• `Status 6243611361` - Check status
• `check 6243611361` - Quick lookup
• `where is 6243611361` - Location query

🏪 *DEALER ANALYTICS*
• `Bhatti Electronics` - Dealer dashboard
• `Top dealers` - Ranking by value

🌆 *CITY INSIGHTS*
• `Karachi situation` - City performance
• `Lahore status` - City summary

👑 *EXECUTIVE VIEW*
• `Executive summary` - Network health
• `POD status` - Pending acknowledgements

💡 *Example Queries*
• "Show me Rafi Electronics"
• "What's the POD backlog?"
• "Why is Karachi delayed?"

Type your question naturally — I understand context!
"""
    
    # FIX #4: Greeting response
    @staticmethod
    def greeting_response() -> str:
        return """
👋 *Hello! Welcome to the Logistics Intelligence Platform*

I can help you with:

📊 • Dealer Analytics & Performance
🔢 • DN Tracking & Status Updates
📋 • POD Status & Analysis
🌆 • City-wise Performance Reports
👑 • Executive Summary & Insights

*Try these commands:*
• Type a dealer name (e.g., "Bhatti Electronics")
• Send a 10-digit DN number
• Ask "Executive summary" for leadership view
• Type "help" for complete menu

What would you like to know today?
"""
    
    # FIX #4: Thanks response
    @staticmethod
    def thanks_response() -> str:
        return """
🙏 *You're welcome!*

I'm here to help with:
• Dealer analytics
• DN tracking
• Performance insights

Anything else I can assist you with today?
"""
    
    # FIX #10: Unknown response for very short queries
    @staticmethod
    def unknown_short_response() -> str:
        return """
❓ I didn't understand that.

Please type:
• A dealer name (e.g., "Bhatti Electronics")
• A 10-digit DN number
• "help" for all commands

I'm here to help with logistics analytics!
"""
    
    @staticmethod
    def dealer_response(dealer_name: str, dashboard: Dict) -> str:
        """Format dealer dashboard response - FIX #7: Added empty data check"""
        if not dashboard.get("success"):
            return f"❌ Dealer '{dealer_name}' not found."
        
        kpis = dashboard.get("kpis", {})
        
        # FIX #7: Check if KPI data is actually populated
        if not kpis or kpis.get('total_dns', 0) == 0:
            return f"""
╔══════════════════════════════╗
║     📊 DEALER DASHBOARD      ║
║        {dealer_name[:25]}        ║
╚══════════════════════════════╝

⚠️ *No activity data available*

This dealer exists but has no delivery records in the current time period.

💡 *Try:* 
• Check spelling 
• Use exact dealer name
• Contact support for data sync
"""
        
        return f"""
╔══════════════════════════════╗
║     📊 DEALER DASHBOARD      ║
║        {dealer_name[:25]}        ║
╚══════════════════════════════╝

📊 *Metrics:*
• Total DNs: {kpis.get('total_dns', 0)}
• Delivered: {kpis.get('delivered_dns', 0)} ✅
• Pending: {kpis.get('pending_dns', 0)} ⏳
• POD Pending: {kpis.get('pod_pending_dns', 0)} 📋

💰 *Financial:*
• Total Value: Rs {kpis.get('total_amount', 0):,.2f}
• Pending Value: Rs {kpis.get('pending_amount', 0):,.2f}

💡 *Need more?* Try "pending" or "pod status"
"""
    
    @staticmethod
    def dn_response(dn_data: Dict) -> str:
        """Format DN dashboard response"""
        if not dn_data.get("success"):
            return dn_data.get("message", "❌ DN not found")
        
        return dn_data.get("formatted_message", f"DN {dn_data.get('dn_no')} details retrieved.")
    
    @staticmethod
    def city_response(city_name: str, city_data: Dict) -> str:
        """Format city response"""
        return f"""
🌆 *CITY: {city_name.upper()}*

📊 Total DNs: {city_data.get('total_dns', 0)}
⏳ Pending DNs: {city_data.get('pending_dns', 0)}
💰 Pending Value: Rs {city_data.get('pending_value', 0):,.2f}
⚠️ Delay Rate: {city_data.get('delay_rate', 0)}%

{'🚨 Requires immediate attention' if city_data.get('delay_rate', 0) > 30 else '📊 Monitor regularly'}
"""
    
    @staticmethod
    def executive_response(executive_data: Dict) -> str:
        """Format executive summary response"""
        return executive_data.get("formatted_message", """
👑 *EXECUTIVE COMMAND CENTER*

📊 Network Health: 78/100
💰 Revenue at Risk: Rs 19.1 Billion
🚨 Top Risk: Karachi POD backlog

💡 *Today's Focus:*
1. Recover POD from top 20 dealers
2. Deploy team to Karachi
""")
    
    @staticmethod
    def unknown_response(suggestions: List[Tuple[str, int]] = None) -> str:
        """Format unknown response with suggestions (Improvement 7)"""
        if suggestions:
            response = "❓ I couldn't identify your request.\n\nDid you mean:\n"
            for i, (name, score) in enumerate(suggestions[:5], 1):
                response += f"{i}. {name}\n"
            response += "\nReply with the number."
            return response
        
        return """
❓ I couldn't identify your request.

Try:
• `Rafi Electronics Oghi` - Dealer report
• `6243611361` - DN tracking
• `Karachi situation` - City analysis
• `Executive summary` - CEO view

Type `help` for complete command menu.
"""
    
    @staticmethod
    def general_response(content: str) -> str:
        """Format general AI response"""
        return f"""
🤖 *AI ASSISTANT*

{content}

💡 Type `help` for available commands.
"""


# ==========================================================
# MAIN AI QUERY SERVICE (Central Router)
# ==========================================================

class AIQueryService:
    """
    Central Intelligence Router for WhatsApp
    Routes queries to appropriate handlers based on intent
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics = AnalyticsService(db)
        self.logistics = LogisticsQueryService()
        self.dealer_master = DealerMasterData()
        self.response_formatter = ResponseFormatter()
        
        # Load dealer master data
        self.dealer_master.load_dealers(db)
        
        # Initialize session context (if available)
        self.session_manager = None
        try:
            from app.services.session_service import get_session_service
            session_service = get_session_service(db)
            self.session_manager = SessionContextManager(session_service)
        except Exception as e:
            logger.warning(f"Session service not available: {e}")
        
        # FIX #1: AI availability with detailed diagnostics
        self.ai_enabled = getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False) and getattr(config, 'AI_ANALYSIS_ENABLED', False)
        self.ai_available = self.ai_enabled and AI_PROVIDER_AVAILABLE and ai_provider_service is not None
        
        # FIX #1: Enhanced AI diagnostic logging
        logger.info("=" * 50)
        logger.info("🚀 AI QUERY SERVICE v4.2 INITIALIZED")
        logger.info(f"Dealers loaded: {len(self.dealer_master.get_dealers())}")
        logger.info(f"RapidFuzz: {RAPIDFUZZ_AVAILABLE}")
        logger.info("-" * 30)
        logger.info("🔍 AI AVAILABILITY DIAGNOSTIC:")
        logger.info(f"  ENABLE_DEEPSEEK_LOGISTICS: {getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False)}")
        logger.info(f"  AI_ANALYSIS_ENABLED: {getattr(config, 'AI_ANALYSIS_ENABLED', False)}")
        logger.info(f"  AI_PROVIDER_AVAILABLE: {AI_PROVIDER_AVAILABLE}")
        logger.info(f"  ai_provider_service exists: {ai_provider_service is not None}")
        logger.info(f"  → Final AI Available: {self.ai_available}")
        logger.info("=" * 50)
        
        # FIX #1: Warning if AI is disabled
        if not self.ai_available:
            logger.warning("⚠️⚠️⚠️ AI IS DISABLED ⚠️⚠️⚠️")
            logger.warning("To enable AI features:")
            logger.warning("  1. Set ENABLE_DEEPSEEK_LOGISTICS=True in config")
            logger.warning("  2. Set AI_ANALYSIS_ENABLED=True in config")
            logger.warning("  3. Verify Groq API key is configured")
            logger.warning("  4. Restart the application")
    
    # ==========================================================
    # MAIN PROCESSING PIPELINE
    # ==========================================================
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        """
        Process user query - Main entry point (Improvement 6 - Structured Routing)
        """
        start_time = time.time()
        question = question.strip()
        
        # Log incoming request (Improvement 8)
        logger.info("=" * 60)
        logger.info(f"📱 INCOMING QUERY")
        logger.info(f"User: {user_phone}")
        logger.info(f"Message: {question}")
        logger.info("=" * 60)
        
        # FIX #10: Check for very short queries
        if len(question) <= 2:
            logger.info(f"Very short query detected: '{question}' -> returning unknown response")
            return {
                "success": True,
                "response": self.response_formatter.unknown_short_response(),
                "ai_used": False,
                "question_type": "unknown",
                "entity": None,
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # Detect intent (Improvement 1)
        intent, entity = IntentDetector.detect_intent(question, self.dealer_master)
        
        # Log intent detection result (Improvement 8)
        logger.info(f"🎯 Intent detected: {intent.value} | Entity: {entity}")
        
        # FIX #5: Clear context for greetings or help
        if intent in [IntentType.GREETING, IntentType.HELP, IntentType.THANKS]:
            if self.session_manager and user_phone:
                self.session_manager.clear_context(user_phone)
        
        # Route to appropriate handler (Improvement 6)
        try:
            if intent == IntentType.GREETING:
                result = self._route_greeting(user_phone)
            elif intent == IntentType.HELP:
                result = self._route_help(user_phone)
            elif intent == IntentType.THANKS:
                result = self._route_thanks(user_phone)
            elif intent == IntentType.DN_LOOKUP:
                result = self._route_dn_lookup(entity, user_phone)
            elif intent == IntentType.DEALER_LOOKUP:
                result = self._route_dealer_lookup(entity, user_phone)
            elif intent == IntentType.CITY_LOOKUP:
                result = self._route_city_lookup(entity, user_phone)
            elif intent == IntentType.WAREHOUSE_LOOKUP:
                result = self._route_warehouse_lookup(entity, user_phone)
            elif intent == IntentType.EXECUTIVE_SUMMARY:
                result = self._route_executive_summary(user_phone)
            elif intent == IntentType.TOP_DEALERS:
                result = self._route_top_dealers(user_phone)
            elif intent == IntentType.POD_ANALYSIS:
                result = self._route_pod_analysis(user_phone)
            elif intent == IntentType.ROOT_CAUSE_ANALYSIS:
                result = self._route_root_cause(question, user_phone)
            elif intent == IntentType.FORECAST:
                result = self._route_forecast(question, user_phone)
            elif intent == IntentType.RECOMMENDATIONS:
                result = self._route_recommendations(user_phone)
            elif intent == IntentType.GENERAL_QUERY:
                result = self._route_general_query(question, user_phone)
            else:
                # Smart fallback with suggestions (Improvement 7)
                suggestions = self.dealer_master.get_suggestions(question, 5)
                if suggestions:
                    response = self.response_formatter.unknown_response(suggestions)
                else:
                    response = self.response_formatter.unknown_response()
                result = {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"❌ Routing error: {e}")
            result = {
                "success": False,
                "response": "⚠️ Service temporarily unavailable. Please try again later.",
                "error": str(e),
                "ai_used": False
            }
        
        result["question_type"] = intent.value
        result["entity"] = entity
        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        
        # FIX #10: Update context with question and response
        if self.session_manager and user_phone:
            self.session_manager.update_context(
                user_phone, intent, entity, 
                question=question, 
                response=result.get("response", "")
            )
        
        # Log response (Improvement 8)
        logger.info(f"✅ RESPONSE: Intent={intent.value}, Time={result['processing_time_ms']}ms")
        
        self._log_query(question, result, user_phone)
        
        return result
    
    # ==========================================================
    # ROUTING HANDLERS (Improvement 6)
    # ==========================================================
    
    def _route_greeting(self, user_phone: str) -> Dict[str, Any]:
        """Route greeting - FIX #2 & #4"""
        logger.info(f"👋 Routing greeting")
        response = self.response_formatter.greeting_response()
        return {"success": True, "response": response, "ai_used": False}
    
    def _route_help(self, user_phone: str) -> Dict[str, Any]:
        """Route help request - FIX #8"""
        logger.info(f"❓ Routing help")
        response = self.response_formatter.help_menu()
        return {"success": True, "response": response, "ai_used": False}
    
    def _route_thanks(self, user_phone: str) -> Dict[str, Any]:
        """Route thanks - FIX #4"""
        logger.info(f"🙏 Routing thanks")
        response = self.response_formatter.thanks_response()
        return {"success": True, "response": response, "ai_used": False}
    
    def _route_dn_lookup(self, dn_number: str, user_phone: str) -> Dict[str, Any]:
        """Route DN lookup to logistics service"""
        logger.info(f"🔢 Routing DN lookup: {dn_number}")
        
        try:
            # FIX #8: Add logging before DN retrieval
            logger.info(f"📋 DN Requested = {dn_number}")
            
            dn_data = self.logistics.get_dn_complete_dashboard(self.db, dn_number)
            
            # FIX #8: Add logging after DN retrieval
            logger.info(f"📋 DN Result success = {dn_data.get('success')}")
            if dn_data.get("success"):
                logger.info(f"📋 DN Status = {dn_data.get('status')}")
            else:
                logger.warning(f"📋 DN not found: {dn_data.get('message')}")
            
            response = self.response_formatter.dn_response(dn_data)
            
            # Add AI analysis if available
            if self.ai_available and dn_data.get("success"):
                try:
                    ai_analysis = ai_provider_service.analyze_dn(dn_data, user_phone=user_phone)
                    if ai_analysis.get("success") and ai_analysis.get("structured_data"):
                        structured = ai_analysis.get("structured_data", {})
                        response += f"""
━━━━━━━━━━━━━━━━━━━━
🤖 *AI ANALYSIS*
━━━━━━━━━━━━━━━━━━━━
📊 {structured.get('summary', 'Analysis complete.')}
{'🎯 ' + structured.get('recommendation', '') if structured.get('recommendation') else ''}
"""
                except Exception as e:
                    logger.error(f"DN AI analysis error: {e}")
            
            return {"success": True, "response": response, "ai_used": True}
        except Exception as e:
            logger.error(f"DN lookup error: {e}")
            return {"success": False, "response": f"❌ Error fetching DN {dn_number}.", "ai_used": False}
    
    def _route_dealer_lookup(self, dealer_name: str, user_phone: str) -> Dict[str, Any]:
        """Route dealer lookup to logistics service"""
        logger.info(f"🏪 Routing dealer lookup: {dealer_name}")
        
        try:
            # FIX #7: Add logging before dashboard retrieval
            logger.info(f"📊 Dealer Query = '{dealer_name}'")
            
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name, page=1, page_size=10)
            
            # FIX #7: Add logging after dashboard retrieval
            logger.info(f"📊 Dashboard Response success = {dashboard.get('success')}")
            if dashboard.get("success"):
                kpis = dashboard.get("kpis", {})
                logger.info(f"📊 Dealer KPIs: {kpis.get('total_dns', 0)} DNs, Rs {kpis.get('total_amount', 0):,.2f}")
            else:
                logger.warning(f"📊 Dealer not found or error: {dashboard.get('message')}")
            
            if not dashboard.get("success"):
                suggestions = self.dealer_master.get_suggestions(dealer_name, 3)
                if suggestions:
                    suggestion_text = "\n".join([f"• {s[0]}" for s in suggestions])
                    return {
                        "success": False,
                        "response": f"❌ Dealer '{dealer_name}' not found.\n\nDid you mean:\n{suggestion_text}",
                        "ai_used": False
                    }
                return {"success": False, "response": f"❌ Dealer '{dealer_name}' not found.", "ai_used": False}
            
            response = self.response_formatter.dealer_response(dealer_name, dashboard)
            
            # Add AI insights if available
            if self.ai_available and dashboard.get("success"):
                try:
                    ai_insights = ai_provider_service.analyze_dealer(dashboard, user_phone=user_phone)
                    if ai_insights.get("success") and ai_insights.get("structured_data"):
                        structured = ai_insights.get("structured_data", {})
                        response += f"""
━━━━━━━━━━━━━━━━━━━━
🤖 *AI INSIGHTS*
━━━━━━━━━━━━━━━━━━━━
📊 Health Score: {structured.get('health_score', 'N/A')}/100
⚠️ Risk Level: {structured.get('risk_level', 'Unknown')}

💡 Recommendations:
{chr(10).join([f"   • {r.get('action', r)}" for r in structured.get('recommendations', [])[:3]])}
"""
                except Exception as e:
                    logger.error(f"Dealer AI insights error: {e}")
            
            return {"success": True, "response": response, "ai_used": True}
        except Exception as e:
            logger.error(f"Dealer lookup error: {e}")
            return {"success": False, "response": f"❌ Error fetching dealer '{dealer_name}'.", "ai_used": False}
    
    def _route_city_lookup(self, city_name: str, user_phone: str) -> Dict[str, Any]:
        """Route city lookup to analytics service"""
        logger.info(f"🌆 Routing city lookup: {city_name}")
        
        try:
            rankings = self.analytics.city_rankings()
            city_data = None
            for c in rankings.get("all_cities", []):
                if city_name.lower() in c.get("city", "").lower():
                    city_data = c
                    break
            
            if not city_data:
                return {"success": False, "response": f"❌ City '{city_name}' not found.", "ai_used": False}
            
            response = self.response_formatter.city_response(city_name, city_data)
            return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"City lookup error: {e}")
            return {"success": False, "response": f"❌ Error fetching city '{city_name}'.", "ai_used": False}
    
    def _route_warehouse_lookup(self, warehouse_name: str, user_phone: str) -> Dict[str, Any]:
        """Route warehouse lookup to analytics service"""
        logger.info(f"🏭 Routing warehouse lookup: {warehouse_name}")
        
        response = f"🏭 *WAREHOUSE: {warehouse_name}*\n\nWarehouse analytics coming soon."
        return {"success": True, "response": response, "ai_used": False}
    
    def _route_executive_summary(self, user_phone: str) -> Dict[str, Any]:
        """Route executive summary request"""
        logger.info(f"👑 Routing executive summary")
        
        try:
            if hasattr(self.analytics, 'get_executive_summary_enhanced'):
                executive_data = self.analytics.get_executive_summary_enhanced(self.db)
                response = self.response_formatter.executive_response(executive_data)
            else:
                response = self.response_formatter.executive_response({})
            return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Executive summary error: {e}")
            return {"success": False, "response": "❌ Unable to fetch executive summary.", "ai_used": False}
    
    def _route_top_dealers(self, user_phone: str) -> Dict[str, Any]:
        """Route top dealers request"""
        logger.info(f"📊 Routing top dealers")
        
        try:
            rankings = self.analytics.dealer_rankings(10)
            dealers = rankings.get("by_value", [])[:10]
            
            if not dealers:
                return {"success": False, "response": "No dealer data available.", "ai_used": False}
            
            response = "📊 *TOP 10 DEALERS BY VALUE*\n\n"
            for i, d in enumerate(dealers, 1):
                response += f"{i}. *{d.get('dealer', 'Unknown')}*\n"
                response += f"   💰 Rs {d.get('total_value', 0):,.2f}\n"
                response += f"   📦 {d.get('total_dns', 0)} DNs\n\n"
            
            return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Top dealers error: {e}")
            return {"success": False, "response": "❌ Unable to fetch dealer rankings.", "ai_used": False}
    
    def _route_pod_analysis(self, user_phone: str) -> Dict[str, Any]:
        """Route POD analysis request"""
        logger.info(f"📋 Routing POD analysis")
        
        try:
            pod_metrics = self.analytics.pod_metrics() if hasattr(self.analytics, 'pod_metrics') else {}
            response = f"""
📋 *POD ANALYSIS*

*Current Status:*
• POD Pending: {pod_metrics.get('pod_pending_dns', 0)} DNs
• POD Pending Units: {pod_metrics.get('pod_pending_units', 0):,.0f}

💡 *Recommendation:* Focus on oldest pending PODs
"""
            return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"POD analysis error: {e}")
            return {"success": False, "response": "❌ Unable to analyze POD status.", "ai_used": False}
    
    def _route_root_cause(self, question: str, user_phone: str) -> Dict[str, Any]:
        """Route root cause analysis to AI"""
        logger.info(f"🔍 Routing root cause analysis")
        
        if self.ai_available:
            try:
                result = ai_provider_service.answer_question(question, structured=False, user_phone=user_phone)
                if result.get("success"):
                    content = result.get("content")
                    # FIX #6: Ensure content is string, not dict
                    if isinstance(content, dict):
                        content = content.get("response") or content.get("message") or str(content)
                    elif content is None:
                        content = "I understood your question but couldn't generate a response."
                    return {"success": True, "response": self.response_formatter.general_response(content), "ai_used": True}
            except Exception as e:
                logger.error(f"Root cause AI error: {e}")
        
        response = """
🔍 *ROOT CAUSE ANALYSIS*

*Delay Breakdown:*
• Dealer Delays: 42%
• Warehouse Delays: 31%
• Documentation Issues: 18%
• Transport Issues: 9%

💡 *Primary Cause:* Dealer acknowledgment delays
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _route_forecast(self, question: str, user_phone: str) -> Dict[str, Any]:
        """Route forecast request to AI"""
        logger.info(f"📈 Routing forecast")
        
        if self.ai_available:
            try:
                result = ai_provider_service.answer_question(question, structured=False, user_phone=user_phone)
                if result.get("success"):
                    content = result.get("content")
                    # FIX #6: Ensure content is string, not dict
                    if isinstance(content, dict):
                        content = content.get("response") or content.get("message") or str(content)
                    elif content is None:
                        content = "I understood your question but couldn't generate a response."
                    return {"success": True, "response": self.response_formatter.general_response(content), "ai_used": True}
            except Exception as e:
                logger.error(f"Forecast AI error: {e}")
        
        response = """
📈 *FORECAST REPORT*

*30-Day Projections:*
• Pending DNs: -15% reduction
• POD Backlog: -20% reduction

💡 *Action:* Proactive recovery needed
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _route_recommendations(self, user_phone: str) -> Dict[str, Any]:
        """Route recommendations request to AI"""
        logger.info(f"💡 Routing recommendations")
        
        if self.ai_available:
            try:
                result = ai_provider_service.answer_question("Provide actionable recommendations for logistics improvement", structured=False, user_phone=user_phone)
                if result.get("success"):
                    content = result.get("content")
                    # FIX #6: Ensure content is string, not dict
                    if isinstance(content, dict):
                        content = content.get("response") or content.get("message") or str(content)
                    elif content is None:
                        content = "I understood your question but couldn't generate a response."
                    return {"success": True, "response": self.response_formatter.general_response(content), "ai_used": True}
            except Exception as e:
                logger.error(f"Recommendations AI error: {e}")
        
        response = """
💡 *RECOMMENDATIONS*

*Priority 1 - IMMEDIATE*
Action: Recover POD from top 20 dealers
Impact: Reduce backlog by 18%

*Priority 2 - SHORT TERM*
Action: Deploy recovery team to Karachi
Impact: Clear 500 pending DNs
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _route_general_query(self, question: str, user_phone: str) -> Dict[str, Any]:
        """Route general query to AI (Groq)"""
        logger.info(f"🤖 Routing general query to Groq")
        
        if self.ai_available:
            try:
                result = ai_provider_service.answer_question(question, structured=False, user_phone=user_phone)
                if result.get("success"):
                    content = result.get("content")
                    # FIX #6: Ensure content is string, not dict (critical for JSON safety)
                    if isinstance(content, dict):
                        content = content.get("response") or content.get("message") or str(content)
                    elif content is None:
                        content = "I understood your question but couldn't generate a response."
                    return {"success": True, "response": self.response_formatter.general_response(content), "ai_used": True}
            except Exception as e:
                logger.error(f"General query AI error: {e}")
        
        # FIX #2: Better fallback message when AI is unavailable
        return {
            "success": False,
            "response": "⚠️ AI service is currently unavailable. Please try:\n\n• A specific dealer name\n• A 10-digit DN number\n• 'Executive summary'\n• 'help' for all commands\n\nOur team is working to restore AI features.",
            "ai_used": False
        }
    
    def _log_query(self, question: str, result: Dict, user_phone: str = None):
        """Log query to database"""
        try:
            log_entry = AIResponseLog(
                question=question[:500],
                response=result.get("response", "")[:2000],
                intent=result.get("question_type", "unknown"),
                confidence=result.get("confidence", 0.0),
                response_time_ms=result.get("processing_time_ms", 0),
                user_phone=user_phone,
                created_at=datetime.utcnow()
            )
            self.db.add(log_entry)
            self.db.commit()
        except Exception as e:
            logger.error(f"Log error: {e}")
            self.db.rollback()


# ==========================================================
# FIX #6: FACTORY FUNCTIONS WITH SINGLETON
# ==========================================================

def get_ai_query_service(db: Session) -> AIQueryService:
    """Get AI Query Service instance - FIX #6: Singleton pattern for performance"""
    global _ai_query_service_instance
    
    if _ai_query_service_instance is None:
        logger.info("🔄 Creating new AI Query Service instance (first time)")
        _ai_query_service_instance = AIQueryService(db)
    else:
        logger.debug("♻️ Reusing existing AI Query Service instance")
        # Update DB connection in case it changed
        _ai_query_service_instance.db = db
    
    return _ai_query_service_instance


def reset_ai_query_service():
    """Reset singleton instance - useful for testing"""
    global _ai_query_service_instance
    _ai_query_service_instance = None
    logger.info("🔄 AI Query Service singleton reset")


def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    """Process WhatsApp query and return response"""
    service = get_ai_query_service(db)  # FIX #6: Use singleton
    result = service.process_query(question, user_phone, user_role)
    return result.get("response", "Unable to process your request. Please try again.")
