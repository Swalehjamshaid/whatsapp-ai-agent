"""
File: app/services/dn_analysis.py
Version: 25.0 - COMPLETE INDEPENDENT DN SERVICE
TAKES FULL CONTROL AFTER PRESSING "1"

WHAT THIS FILE DOES:
- ✅ When you press "1", you ENTER this file
- ✅ ALL communication stays in this file
- ✅ Handles ALL DN commands naturally
- ✅ Uses AI for content recognition
- ✅ ONLY "99" exits back to main menu
- ✅ Complete independence from router

COMMANDS SUPPORTED:
- Any 8-12 digit number → DN Dashboard
- "pending" → Pending DNs list
- "status [DN]" → DN status
- "search [keyword]" → Search DNs
- "revenue [DN]" → Revenue info
- "units [DN]" → Units info
- "compare DN1 DN2" → Compare DNs
- "trend" → DN trends
- "forecast" → DN forecast
- "insights" → DN insights
- "recommendations" → Improvement ideas
- "menu" → Show DN menu
- "99" → EXIT to main menu
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any, Optional, Dict, List, Tuple, Union
import hashlib

logger = logging.getLogger(__name__)

# ============================================================
# AI LIBRARIES - Graceful Loading
# ============================================================

# Groq - Primary AI Provider
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

# OpenAI - Fallback
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Semantic Router - Intent Detection
try:
    from semantic_router import Route, Router
    from semantic_router.encoders import HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False

# RapidFuzz - Fuzzy Matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

# spaCy - NER
try:
    import spacy
    SPACY_AVAILABLE = True
    nlp = None
    try:
        nlp = spacy.load("en_core_web_sm")
    except:
        try:
            import subprocess
            subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
            nlp = spacy.load("en_core_web_sm")
        except:
            pass
except ImportError:
    SPACY_AVAILABLE = False
    nlp = None

# ============================================================
# DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, and_, desc, asc
    from sqlalchemy.orm import Session
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logger.warning("⚠️ Database not available")

# ============================================================
# CONFIGURATION
# ============================================================

CACHE_TTL = int(os.getenv("DN_ANALYTICS_CACHE_TTL", "300"))
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq")
AI_MODEL = os.getenv("AI_MODEL", "llama3-70b-8192")
USE_AI_ENHANCEMENT = os.getenv("USE_AI_ENHANCEMENT", "true").lower() == "true"
DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))

# ============================================================
# DATACLASSES
# ============================================================

@dataclass
class DNContext:
    """DN session context - persists while in DN service"""
    current_dn: Optional[str] = None
    in_dn_service: bool = False
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    last_intent: Optional[str] = None
    last_response: Optional[str] = None
    search_results: Optional[List[Dict[str, Any]]] = None
    session_start: datetime = field(default_factory=datetime.now)

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _format_date(value: Any) -> str:
    if not value:
        return "N/A"
    if hasattr(value, "strftime"):
        return value.strftime("%d-%b-%Y")
    return str(value)

def _is_valid_dn(dn: str) -> bool:
    if not dn:
        return False
    cleaned = re.sub(r'[\s-]', '', dn)
    return cleaned.isdigit() and 8 <= len(cleaned) <= 12

def _extract_dn(text: str) -> Optional[str]:
    match = re.search(r'\b(\d{8,12})\b', text)
    return match.group(1) if match else None

def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

# ============================================================
# AI CONTENT RECOGNITION ENGINE
# ============================================================

class DNContentRecognizer:
    """AI-powered content recognition for DN queries"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        
        self._client = None
        self._provider = AI_PROVIDER
        self._router = None
        
        # Initialize AI client
        self._init_client()
        
        # Initialize semantic router
        self._init_router()
        
        # Cache for responses
        self._cache: Dict[str, str] = {}
        self._cache_lock = threading.RLock()
        
        logger.info(f"🧠 DNContentRecognizer initialized (Provider: {AI_PROVIDER})")
    
    def _init_client(self):
        """Initialize AI client"""
        if AI_PROVIDER == "groq" and GROQ_AVAILABLE:
            try:
                self._client = Groq()
                logger.info("✅ Groq client initialized")
                return
            except Exception as e:
                logger.warning(f"Groq init failed: {e}")
        
        if AI_PROVIDER == "openai" and OPENAI_AVAILABLE:
            try:
                self._client = OpenAI()
                logger.info("✅ OpenAI client initialized")
                self._provider = "openai"
                return
            except Exception as e:
                logger.warning(f"OpenAI init failed: {e}")
        
        logger.warning("⚠️ No AI client available")
        self._client = None
    
    def _init_router(self):
        """Initialize semantic router"""
        if not SEMANTIC_ROUTER_AVAILABLE:
            return
        
        try:
            routes = [
                Route(name="dashboard", utterances=[
                    "show dn", "dn dashboard", "dn details", "dn info"
                ]),
                Route(name="status", utterances=[
                    "dn status", "status of dn", "where is dn", "track dn"
                ]),
                Route(name="pending", utterances=[
                    "pending dns", "pending deliveries", "overdue dns"
                ]),
                Route(name="search", utterances=[
                    "search dn", "find dn", "lookup dn", "dn with customer"
                ]),
                Route(name="revenue", utterances=[
                    "dn revenue", "revenue from dn", "dn amount", "value of dn"
                ]),
                Route(name="units", utterances=[
                    "dn units", "dn quantity", "how many units", "dn qty"
                ]),
                Route(name="compare", utterances=[
                    "compare dns", "dn vs dn", "comparison"
                ]),
                Route(name="trend", utterances=[
                    "dn trends", "dn pattern", "dn over time"
                ]),
                Route(name="forecast", utterances=[
                    "dn forecast", "predict dn", "future dns"
                ]),
                Route(name="insights", utterances=[
                    "dn insights", "dn analysis", "key findings"
                ]),
                Route(name="recommendations", utterances=[
                    "dn recommendations", "improve dns", "suggestions"
                ]),
            ]
            encoder = HuggingFaceEncoder()
            self._router = Router(routes=routes, encoder=encoder)
            logger.info("✅ Semantic Router initialized")
        except Exception as e:
            logger.warning(f"Semantic Router init failed: {e}")
    
    def recognize(self, query: str) -> Dict[str, Any]:
        """Recognize intent and extract entities"""
        result = {
            "intent": "unknown",
            "confidence": 0.0,
            "entities": {},
            "dn": None,
            "explanation": ""
        }
        
        # Extract DN number
        dn = _extract_dn(query)
        if dn:
            result["dn"] = dn
            result["entities"]["dn"] = dn
        
        # Use semantic router
        if self._router:
            try:
                route_result = self._router.route(query)
                if route_result and hasattr(route_result, 'name'):
                    result["intent"] = route_result.name
                    result["confidence"] = 0.8
                    result["explanation"] = f"Semantic routing: {route_result.name}"
                    return result
            except Exception:
                pass
        
        # Use keyword detection
        query_lower = query.lower()
        
        if "status" in query_lower or "track" in query_lower or "where" in query_lower:
            result["intent"] = "status"
            result["confidence"] = 0.7
        elif "pending" in query_lower or "overdue" in query_lower:
            result["intent"] = "pending"
            result["confidence"] = 0.7
        elif "search" in query_lower or "find" in query_lower or "lookup" in query_lower:
            result["intent"] = "search"
            result["confidence"] = 0.7
        elif "revenue" in query_lower or "amount" in query_lower or "value" in query_lower:
            result["intent"] = "revenue"
            result["confidence"] = 0.7
        elif "units" in query_lower or "quantity" in query_lower or "qty" in query_lower:
            result["intent"] = "units"
            result["confidence"] = 0.7
        elif "compare" in query_lower or "vs" in query_lower:
            result["intent"] = "compare"
            result["confidence"] = 0.7
        elif "trend" in query_lower or "pattern" in query_lower:
            result["intent"] = "trend"
            result["confidence"] = 0.7
        elif "forecast" in query_lower or "predict" in query_lower:
            result["intent"] = "forecast"
            result["confidence"] = 0.7
        elif "insight" in query_lower or "analysis" in query_lower:
            result["intent"] = "insights"
            result["confidence"] = 0.7
        elif "recommend" in query_lower or "suggest" in query_lower or "improve" in query_lower:
            result["intent"] = "recommendations"
            result["confidence"] = 0.7
        elif dn:
            result["intent"] = "dashboard"
            result["confidence"] = 0.9
            result["explanation"] = "DN number detected"
        
        return result
    
    def generate_response(self, query: str, data: Dict[str, Any]) -> str:
        """Generate AI-enhanced response"""
        if not self._client or not USE_AI_ENHANCEMENT:
            return None
        
        try:
            prompt = f"""You are a logistics DN assistant. Answer the user's question based on this data.

User Question: {query}

DN Data:
- DN Number: {data.get('dn_no', 'N/A')}
- Division: {data.get('division', 'N/A')}
- Order Type: {data.get('order_type', 'N/A')}
- Customer: {data.get('customer_name', data.get('customer_code', 'N/A'))}
- Dealer: {data.get('dealer', 'N/A')}
- Status: {data.get('delivery_status', 'Pending')}
- PGI: {data.get('pgi_status', 'Pending')}
- POD: {data.get('pod_status', 'Pending')}
- Pending: {'Yes' if data.get('pending_flag') else 'No'}
- Created: {_format_date(data.get('dn_create_date'))}
- Revenue: PKR {data.get('dn_amount', 0):,.2f}
- Units: {data.get('dn_qty', 0):,}
- Warehouse: {data.get('warehouse', 'N/A')}
- City: {data.get('ship_to_city', 'N/A')}

Provide a clear, helpful answer. Use emojis. Keep it concise for WhatsApp.
"""
            
            if self._provider == "groq":
                response = self._client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a logistics DN assistant. Provide helpful, concise answers for WhatsApp."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                return response.choices[0].message.content
            
            elif self._provider == "openai":
                response = self._client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a logistics DN assistant. Provide helpful, concise answers for WhatsApp."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                return response.choices[0].message.content
                
        except Exception as e:
            logger.error(f"AI generation failed: {e}")
        
        return None

# ============================================================
# DN MENU RENDERER
# ============================================================

class DNMenuRenderer:
    """DN Menu Renderer - WhatsApp Format"""
    
    @staticmethod
    def render_main_menu() -> str:
        """Main DN menu - shown when you press "1" or type "menu" """
        return "\n".join([
            "📦 *DN ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. DN Dashboard",
            "2. DN Status",
            "3. Pending DN",
            "4. Search DN",
            "5. AI Query",
            "99. Back to Main",
            "",
            "📌 *Commands (Stay in DN Service):*",
            "• Type DN number for dashboard",
            "• 'status [DN]' - DN status",
            "• 'pending' - Show pending DNs",
            "• 'search [keyword]' - Search DNs",
            "• 'revenue [DN]' - Check revenue",
            "• 'units [DN]' - Check units",
            "• 'compare DN1 DN2' - Compare DNs",
            "• 'trend' - DN trends",
            "• 'forecast' - DN forecast",
            "• 'insights' - DN insights",
            "• 'recommendations' - Improvement ideas",
            "",
            "Reply with a number or command:"
        ])
    
    @staticmethod
    def render_dn_dashboard(data: Dict[str, Any]) -> str:
        """Full DN dashboard with all 10 key questions"""
        dn_no = data.get('dn_no', 'N/A')
        
        return "\n".join([
            f"📦 *DN Dashboard - {dn_no}*",
            "",
            "📊 *10 Key Questions Answered:*",
            "",
            "1️⃣ *Status:*",
            f"   {data.get('delivery_status', 'Pending')}",
            "",
            "2️⃣ *Creation Date:*",
            f"   {_format_date(data.get('dn_create_date'))}",
            "",
            "3️⃣ *Customer:*",
            f"   {data.get('customer_name', data.get('customer_code', 'N/A'))}",
            "",
            "4️⃣ *Warehouse:*",
            f"   {data.get('warehouse', 'N/A')}",
            "",
            "5️⃣ *Revenue:*",
            f"   PKR {data.get('dn_amount', 0):,.2f}",
            "",
            "6️⃣ *Units:*",
            f"   {data.get('dn_qty', 0):,}",
            "",
            "7️⃣ *PGI Status:*",
            f"   {data.get('pgi_status', 'Pending')}",
            "",
            "8️⃣ *POD Status:*",
            f"   {data.get('pod_status', 'Pending')}",
            "",
            "9️⃣ *Pending:*",
            f"   {'✅ Yes' if data.get('pending_flag') else '❌ No'}",
            "",
            "🔟 *SLA:*",
            f"   {data.get('sla_compliant', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main",
            "",
            "📌 *Still in DN Service - Type 'menu' for options*"
        ])
    
    @staticmethod
    def render_pending_list(items: List[Dict[str, Any]]) -> str:
        """Render pending DNs list"""
        if not items:
            return "📋 *Pending DNs*\n\n✅ No pending DNs found.\n\n0. Main Menu\n99. Back"
        
        lines = ["📋 *Pending DNs*", ""]
        lines.append(f"Total: {len(items)}")
        lines.append("")
        
        for i, item in enumerate(items[:10], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_name', item.get('customer_code', 'N/A'))
            status = item.get('delivery_status', 'Pending')
            lines.append(f"{i}. *DN {dn_no}*")
            lines.append(f"   Customer: {customer}")
            lines.append(f"   Status: {status}")
            lines.append("")
        
        if len(items) > 10:
            lines.append(f"... and {len(items) - 10} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_search_results(query: str, items: List[Dict[str, Any]]) -> str:
        """Render search results"""
        if not items:
            return f"🔍 No results found for '{query}'\n\n0. Main Menu\n99. Back"
        
        lines = [f"🔍 *Search Results for '{query}'*", ""]
        lines.append(f"Found: {len(items)} DNs")
        lines.append("")
        
        for i, item in enumerate(items[:10], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_name', item.get('customer_code', 'N/A'))
            lines.append(f"{i}. *DN {dn_no}* - {customer}")
        
        if len(items) > 10:
            lines.append(f"... and {len(items) - 10} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_dn_status(data: Dict[str, Any]) -> str:
        """Render DN status"""
        dn_no = data.get('dn_no', 'N/A')
        return "\n".join([
            f"📊 *DN {dn_no} - Status*",
            "",
            f"Status: {data.get('delivery_status', 'Pending')}",
            f"PGI: {data.get('pgi_status', 'Pending')}",
            f"POD: {data.get('pod_status', 'Pending')}",
            f"Pending: {'✅ Yes' if data.get('pending_flag') else '❌ No'}",
            "",
            f"Created: {_format_date(data.get('dn_create_date'))}",
            f"Customer: {data.get('customer_name', data.get('customer_code', 'N/A'))}",
            "",
            "0. Main Menu",
            "99. Back"
        ])

# ============================================================
# MAIN DN SERVICE - TAKES FULL CONTROL
# ============================================================

class DNAnalysisService:
    """
    COMPLETE INDEPENDENT DN SERVICE
    TAKES FULL CONTROL AFTER PRESSING "1"
    ALL COMMUNICATION STAYS IN THIS FILE UNTIL "99"
    """
    
    _instance: Optional["DNAnalysisService"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._service_name = "dn_analysis"
        self._version = "25.0"
        
        # Initialize components
        self._content_recognizer = DNContentRecognizer()
        self._menu_renderer = DNMenuRenderer()
        
        # Context memory - persists while in DN service
        self._contexts: Dict[str, DNContext] = {}
        self._context_lock = threading.RLock()
        
        logger.info("=" * 60)
        logger.info("🚀 DNAnalysisService v25.0 initialized")
        logger.info("   📦 TAKES FULL CONTROL AFTER PRESSING '1'")
        logger.info("   🔒 ALL communication stays in this file")
        logger.info(f"   🧠 AI: {AI_PROVIDER if GROQ_AVAILABLE or OPENAI_AVAILABLE else 'Disabled'}")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info("   🔑 ONLY '99' exits to main menu")
        logger.info("=" * 60)
    
    @staticmethod
    def _get_session() -> Optional[Session]:
        """Get database session"""
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def _get_context(self, session_id: str) -> DNContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DNContext()
                self._contexts[session_id].in_dn_service = True
            return self._contexts[session_id]
    
    def get_main_menu(self) -> str:
        """Get the main DN menu"""
        return self._menu_renderer.render_main_menu()
    
    # ============================================================
    # MAIN PROCESSING - ENTRY POINT
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - Handles ALL DN queries
        TAKES FULL CONTROL - ALL communication stays here
        ONLY "99" exits back to main menu
        """
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📦 DN Service (FULL CONTROL): '{message_clean}' from {sender}")
        
        # Get or create context - mark as in DN service
        context = self._get_context(sender)
        context.in_dn_service = True
        
        # ============================================================
        # STEP 1: Check for "99" - EXIT to main menu (ONLY EXIT!)
        # ============================================================
        if message_clean == "99":
            context.in_dn_service = False
            context.current_dn = None
            logger.info(f"🔄 User {sender} EXITING DN service (99)")
            return "99"  # Signal to router to exit
        
        # ============================================================
        # STEP 2: Check for "menu" or "0" - Show DN menu
        # ============================================================
        if message_clean.lower() in ["menu", "help", "options", "0"]:
            return self.get_main_menu()
        
        # ============================================================
        # STEP 3: Check for menu options (1-5)
        # ============================================================
        if message_clean in ["1", "2", "3", "4", "5"]:
            return self._handle_menu_option(sender, message_clean)
        
        # ============================================================
        # STEP 4: Check for DN number (8-12 digits) - AUTO-DETECT
        # ============================================================
        dn = _extract_dn(message_clean)
        if dn and _is_valid_dn(dn):
            context.current_dn = dn
            return self._get_dn_dashboard(sender, dn, message_clean)
        
        # ============================================================
        # STEP 5: AI Content Recognition for Natural Language
        # ============================================================
        recognized = self._content_recognizer.recognize(message_clean)
        logger.info(f"🎯 Recognized: {recognized['intent']} (confidence: {recognized['confidence']})")
        
        # Route based on recognized intent
        intent = recognized.get("intent", "unknown")
        entities = recognized.get("entities", {})
        
        # Check if a DN was recognized
        if recognized.get("dn"):
            context.current_dn = recognized["dn"]
            return self._get_dn_dashboard(sender, recognized["dn"], message_clean)
        
        # Route to specific handlers
        if intent == "status":
            dn = _extract_dn(message_clean)
            if dn:
                return self._get_dn_status(sender, dn)
            return "📊 Please provide a DN number. Example: 'status 6243700919'\n\n0. Main Menu\n99. Back"
        
        if intent == "pending":
            return self._get_pending_dns(sender)
        
        if intent == "search":
            query = message_clean
            for word in ["search", "find", "lookup"]:
                query = query.replace(word, "").strip()
            if query:
                return self._search_dns(sender, query)
            return "🔍 Please specify what to search. Example: 'search Lahore'\n\n0. Main Menu\n99. Back"
        
        if intent == "revenue":
            dn = _extract_dn(message_clean)
            if dn:
                return self._get_dn_metric(sender, dn, "revenue")
            return "💰 Please provide a DN number. Example: 'revenue 6243700919'\n\n0. Main Menu\n99. Back"
        
        if intent == "units":
            dn = _extract_dn(message_clean)
            if dn:
                return self._get_dn_metric(sender, dn, "units")
            return "📦 Please provide a DN number. Example: 'units 6243700919'\n\n0. Main Menu\n99. Back"
        
        if intent == "compare":
            return self._handle_comparison(sender, message_clean)
        
        if intent == "trend":
            return self._get_trends(sender)
        
        if intent == "forecast":
            return self._get_forecast(sender)
        
        if intent == "insights":
            return self._get_insights(sender)
        
        if intent == "recommendations":
            return self._get_recommendations(sender)
        
        # ============================================================
        # STEP 6: Unknown - Show help (STAYS IN DN SERVICE)
        # ============================================================
        return self._show_help()
    
    # ============================================================
    # MENU OPTIONS
    # ============================================================
    
    def _handle_menu_option(self, sender: str, option: str) -> str:
        """Handle menu options 1-5"""
        if option == "1":
            return "🔍 *Enter DN number:*\n\nType an 8-12 digit DN number for full dashboard.\n\n0. Main Menu\n99. Back"
        elif option == "2":
            return "📊 *Enter DN number for status:*\n\nType an 8-12 digit DN number.\n\n0. Main Menu\n99. Back"
        elif option == "3":
            return self._get_pending_dns(sender)
        elif option == "4":
            return "🔍 *Search DNs:*\n\nType 'search [keyword]' to find DNs.\n\nExample: search Lahore\n\n0. Main Menu\n99. Back"
        elif option == "5":
            return "🤖 *AI Query:*\n\nAsk any DN-related question naturally.\n\nExamples:\n• What is the status of DN 6243700919?\n• Show pending DNs\n• Revenue of DN 6243700919\n\n0. Main Menu\n99. Back"
        return self.get_main_menu()
    
    def _show_help(self) -> str:
        """Show help - STAYS IN DN SERVICE"""
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 *DN Service Commands (Stay in DN):*",
            "• Type a DN number (8-12 digits) for dashboard",
            "• 'status [DN]' - DN status",
            "• 'pending' - Show pending DNs",
            "• 'search [keyword]' - Search DNs",
            "• 'revenue [DN]' - Check revenue",
            "• 'units [DN]' - Check units",
            "• 'compare DN1 DN2' - Compare DNs",
            "• 'trend' - DN trends",
            "• 'forecast' - DN forecast",
            "• 'insights' - DN insights",
            "• 'recommendations' - Improvement ideas",
            "• 'menu' - Show DN menu",
            "",
            "📌 *To Exit:* Type '99'",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    # ============================================================
    # DN OPERATIONS - ALL DATA FROM POSTGRESQL
    # ============================================================
    
    def _get_dn_dashboard(self, sender: str, dn_no: str, query: str = "") -> str:
        """Get DN dashboard - Answers ALL 10 questions"""
        session = self._get_session()
        if not session:
            return self._get_fallback_dashboard(dn_no)
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.order_type,
                DeliveryReport.customer_code,
                DeliveryReport.dealer,
                DeliveryReport.dn_work,
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.pending_flag,
                DeliveryReport.dn_create_date,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_qty,
                DeliveryReport.customer_name,
                DeliveryReport.warehouse,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
                DeliveryReport.ship_to_city,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            if not result:
                session.close()
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            data = {
                'dn_no': _text(result.dn_no),
                'division': _text(result.division),
                'order_type': _text(result.order_type),
                'customer_code': _text(result.customer_code),
                'dealer': _text(result.dealer),
                'dn_work': _text(result.dn_work),
                'delivery_status': _text(result.delivery_status, 'Pending'),
                'pgi_status': _text(result.pgi_status, 'Pending'),
                'pod_status': _text(result.pod_status, 'Pending'),
                'pending_flag': bool(result.pending_flag) if result.pending_flag is not None else True,
                'dn_create_date': result.dn_create_date,
                'dn_amount': _safe_float(result.dn_amount),
                'dn_qty': _safe_int(result.dn_qty),
                'customer_name': _text(result.customer_name),
                'warehouse': _text(result.warehouse),
                'good_issue_date': result.good_issue_date,
                'pod_date': result.pod_date,
                'ship_to_city': _text(result.ship_to_city),
                'sla_compliant': '✅ Compliant' if result.pod_date else '⏳ Pending',
            }
            
            session.close()
            
            # Try AI enhancement
            if query and self._content_recognizer._client and USE_AI_ENHANCEMENT:
                ai_response = self._content_recognizer.generate_response(query, data)
                if ai_response:
                    return ai_response + "\n\n0. Main Menu\n99. Back"
            
            return self._menu_renderer.render_dn_dashboard(data)
            
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            if session:
                session.close()
            return self._get_fallback_dashboard(dn_no)
    
    def _get_fallback_dashboard(self, dn_no: str) -> str:
        """Fallback when database is unavailable"""
        return "\n".join([
            f"📦 *DN Dashboard - {dn_no}*",
            "",
            "⚠️ Database is currently unavailable.",
            "",
            "💡 *Try:*",
            "• Check database connection",
            "• Try again later",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    def _get_dn_status(self, sender: str, dn_no: str) -> str:
        """Get DN status"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.pending_flag,
                DeliveryReport.dn_create_date,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            if not result:
                session.close()
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            data = {
                'dn_no': _text(result.dn_no),
                'delivery_status': _text(result.delivery_status, 'Pending'),
                'pgi_status': _text(result.pgi_status, 'Pending'),
                'pod_status': _text(result.pod_status, 'Pending'),
                'pending_flag': bool(result.pending_flag) if result.pending_flag is not None else True,
                'dn_create_date': result.dn_create_date,
                'customer_name': _text(result.customer_name),
                'customer_code': _text(result.customer_code),
            }
            
            session.close()
            return self._menu_renderer.render_dn_status(data)
            
        except Exception as e:
            logger.error(f"Status error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching status for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_metric(self, sender: str, dn_no: str, metric: str) -> str:
        """Get specific DN metric"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_qty,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            if not result:
                session.close()
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            session.close()
            
            if metric == "revenue":
                return f"💰 *DN {dn_no} Revenue*\n\nPKR {_safe_float(result.dn_amount):,.2f}\n\n0. Main Menu\n99. Back"
            else:
                return f"📦 *DN {dn_no} Units*\n\n{_safe_int(result.dn_qty):,}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Metric error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching {metric} for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_pending_dns(self, sender: str) -> str:
        """Get pending DNs"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_code,
                DeliveryReport.customer_name,
                DeliveryReport.delivery_status,
                DeliveryReport.division,
            ).filter(
                or_(
                    DeliveryReport.pending_flag.is_(True),
                    DeliveryReport.pod_date.is_(None)
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(20).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_code': _text(row.customer_code),
                    'customer_name': _text(row.customer_name),
                    'delivery_status': _text(row.delivery_status, 'Pending'),
                    'division': _text(row.division),
                })
            
            session.close()
            return self._menu_renderer.render_pending_list(items)
            
        except Exception as e:
            logger.error(f"Pending error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching pending DNs.\n\n0. Main Menu\n99. Back"
    
    def _search_dns(self, sender: str, query: str) -> str:
        """Search DNs"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            search_pattern = f"%{query}%"
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_code,
                DeliveryReport.customer_name,
                DeliveryReport.division,
            ).filter(
                or_(
                    DeliveryReport.dn_no.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern),
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.division.ilike(search_pattern),
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(20).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_code': _text(row.customer_code),
                    'customer_name': _text(row.customer_name),
                    'division': _text(row.division),
                })
            
            session.close()
            return self._menu_renderer.render_search_results(query, items)
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            if session:
                session.close()
            return f"⚠️ Error searching for '{query}'\n\n0. Main Menu\n99. Back"
    
    def _handle_comparison(self, sender: str, query: str) -> str:
        """Handle DN comparison"""
        dns = re.findall(r'\b(\d{8,12})\b', query)
        if len(dns) < 2:
            return "🔄 *Compare DNs*\n\nPlease provide two DN numbers.\n\nExample: compare 6243700919 6243714234\n\n0. Main Menu\n99. Back"
        
        dn1, dn2 = dns[0], dns[1]
        
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result1 = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_qty,
            ).filter(
                DeliveryReport.dn_no == dn1
            ).first()
            
            result2 = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_qty,
            ).filter(
                DeliveryReport.dn_no == dn2
            ).first()
            
            session.close()
            
            if not result1 or not result2:
                return "⚠️ One or both DNs not found.\n\n0. Main Menu\n99. Back"
            
            revenue1 = _safe_float(result1.dn_amount)
            revenue2 = _safe_float(result2.dn_amount)
            
            return "\n".join([
                f"🔄 *Comparison: DN {dn1} vs DN {dn2}*",
                "",
                "📊 *Metrics*",
                f"Division: {_text(result1.division)} vs {_text(result2.division)}",
                f"Status: {_text(result1.delivery_status, 'Pending')} vs {_text(result2.delivery_status, 'Pending')}",
                f"Revenue: PKR {revenue1:,.2f} vs PKR {revenue2:,.2f}",
                f"Units: {_safe_int(result1.dn_qty)} vs {_safe_int(result2.dn_qty)}",
                "",
                "🏆 *Winner:*",
                f"{dn1} has higher revenue" if revenue1 > revenue2 else f"{dn2} has higher revenue",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            if session:
                session.close()
            return f"⚠️ Error comparing DNs.\n\n0. Main Menu\n99. Back"
    
    def _get_trends(self, sender: str) -> str:
        """Get DN trends"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            from sqlalchemy import extract
            
            results = session.query(
                extract('week', DeliveryReport.dn_create_date).label('week'),
                func.count(DeliveryReport.dn_no).label('count'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
            ).filter(
                DeliveryReport.dn_create_date.isnot(None)
            ).group_by(
                extract('week', DeliveryReport.dn_create_date)
            ).order_by(
                desc(extract('week', DeliveryReport.dn_create_date))
            ).limit(4).all()
            
            session.close()
            
            if not results:
                return "📈 No trend data available.\n\n0. Main Menu\n99. Back"
            
            lines = ["📈 *DN Trends (Last 4 Weeks)*", ""]
            for row in results:
                week = int(row.week)
                count = _safe_int(row.count)
                revenue = _safe_float(row.revenue)
                lines.append(f"Week {week}:")
                lines.append(f"   DNs: {count}")
                lines.append(f"   Revenue: PKR {revenue:,.2f}")
                lines.append("")
            
            lines.extend(["0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Trend error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching trends.\n\n0. Main Menu\n99. Back"
    
    def _get_forecast(self, sender: str) -> str:
        """Get DN forecast"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            from datetime import timedelta
            
            results = session.query(
                func.count(DeliveryReport.dn_no).label('total'),
                func.count(func.distinct(func.date(DeliveryReport.dn_create_date))).label('days'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
            ).filter(
                DeliveryReport.dn_create_date >= datetime.now().date() - timedelta(days=30)
            ).first()
            
            session.close()
            
            if not results or not results.days:
                return "🔮 Insufficient data for forecast.\n\n0. Main Menu\n99. Back"
            
            total = _safe_int(results.total)
            days = _safe_int(results.days)
            revenue = _safe_float(results.revenue)
            
            avg_daily = total / days if days > 0 else 0
            avg_daily_revenue = revenue / days if days > 0 else 0
            
            return "\n".join([
                "🔮 *DN Forecast*",
                "",
                f"Average Daily DNs: {avg_daily:.1f}",
                f"Average Daily Revenue: PKR {avg_daily_revenue:,.2f}",
                f"Next 7 Days Forecast: {int(avg_daily * 7)} DNs",
                f"Next 7 Days Revenue: PKR {avg_daily_revenue * 7:,.2f}",
                "",
                "📌 *Based on last 30 days data*",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Forecast error: {e}")
            if session:
                session.close()
            return "⚠️ Error generating forecast.\n\n0. Main Menu\n99. Back"
    
    def _get_insights(self, sender: str) -> str:
        """Get DN insights"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            from sqlalchemy import case
            
            results = session.query(
                func.count(DeliveryReport.dn_no).label('total'),
                func.sum(case((DeliveryReport.pod_date.isnot(None), 1), else_=0)).label('delivered'),
                func.sum(case((DeliveryReport.pending_flag.is_(True), 1), else_=0)).label('pending'),
                func.avg(DeliveryReport.dn_amount).label('avg_revenue'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
            ).first()
            
            session.close()
            
            total = _safe_int(results.total)
            delivered = _safe_int(results.delivered)
            pending = _safe_int(results.pending)
            avg_revenue = _safe_float(results.avg_revenue)
            total_revenue = _safe_float(results.total_revenue)
            
            delivery_rate = (delivered / total * 100) if total > 0 else 0
            
            return "\n".join([
                "💡 *DN Insights*",
                "",
                f"📊 Total DNs: {total:,}",
                f"✅ Delivered: {delivered:,} ({delivery_rate:.1f}%)",
                f"⏳ Pending: {pending:,}",
                f"💰 Total Revenue: PKR {total_revenue:,.2f}",
                f"📈 Avg Revenue/DN: PKR {avg_revenue:,.2f}",
                "",
                "🎯 *Key Findings:*",
                f"• Delivery rate is {delivery_rate:.1f}%",
                f"• {pending} DNs need attention",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Insights error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching insights.\n\n0. Main Menu\n99. Back"
    
    def _get_recommendations(self, sender: str) -> str:
        """Get DN recommendations"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            from datetime import timedelta
            
            pending_count = session.query(
                func.count(DeliveryReport.dn_no)
            ).filter(
                or_(
                    DeliveryReport.pending_flag.is_(True),
                    DeliveryReport.pod_date.is_(None)
                )
            ).scalar() or 0
            
            threshold = datetime.now().date() - timedelta(days=DN_DELAY_THRESHOLD_DAYS)
            
            delayed_count = session.query(
                func.count(DeliveryReport.dn_no)
            ).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.good_issue_date < threshold,
                DeliveryReport.pod_date.is_(None)
            ).scalar() or 0
            
            session.close()
            
            recommendations = []
            
            if pending_count > 10:
                recommendations.append(f"🚨 {pending_count} pending DNs need resolution")
            elif pending_count > 5:
                recommendations.append(f"📋 Review {pending_count} pending DNs")
            
            if delayed_count > 5:
                recommendations.append(f"⏰ {delayed_count} DNs are delayed > {DN_DELAY_THRESHOLD_DAYS} days")
            
            if pending_count <= 5 and delayed_count <= 5:
                recommendations.append("✅ Current DN performance is good")
                recommendations.append("📊 Continue monitoring key metrics")
            
            if not recommendations:
                recommendations.append("✅ Maintain current performance")
            
            lines = ["🎯 *DN Recommendations*", ""]
            for rec in recommendations:
                lines.append(f"• {rec}")
            
            lines.extend(["", "0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Recommendations error: {e}")
            if session:
                session.close()
            return "⚠️ Error generating recommendations.\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for service"""
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "ai": "enabled" if (GROQ_AVAILABLE or OPENAI_AVAILABLE) else "disabled",
            "takes_full_control": True,
            "exit_command": "99",
            "timestamp": datetime.now().isoformat()
        }

# ============================================================
# SERVICE SINGLETON
# ============================================================

_service: Optional[DNAnalysisService] = None
_service_lock = threading.Lock()

def get_dn_analysis_service() -> DNAnalysisService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DNAnalysisService()
    return _service

def process_dn_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    """Process DN menu input for WhatsApp integration"""
    service = get_dn_analysis_service()
    result = service.process_whatsapp_query(user_input, session_id)
    
    # Check if we need to exit to main menu
    if result == "99":
        return {
            "response": "99",
            "menu_type": "dn_menu",
            "action": "exit_to_main",
            "data": {},
            "exit_menu": True
        }
    
    return {
        "response": result,
        "menu_type": "dn_menu",
        "action": "dn_response",
        "data": {},
        "exit_menu": False
    }

def get_dn_main_menu() -> str:
    """Get the main DN menu for WhatsApp"""
    service = get_dn_analysis_service()
    return service.get_main_menu()

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DNAnalysisService",
    "DNContext",
    "get_dn_analysis_service",
    "process_dn_menu",
    "get_dn_main_menu",
]
