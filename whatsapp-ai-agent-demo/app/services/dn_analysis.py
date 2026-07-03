"""
File: app/services/dn_analysis.py
Version: 22.0 - ADAPTED TO YOUR TABLE SCHEMA

THIS VERSION USES THE COLUMNS YOU ACTUALLY HAVE:
- dn_no ✅
- dn_work ✅
- order_type ✅
- division ✅
- customer_code ✅
- dealer ✅
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple, Union, Set

logger = logging.getLogger(__name__)

# ============================================================
# CORE DEPENDENCIES
# ============================================================

try:
    from cachetools import TTLCache
except ImportError:
    class TTLCache:
        def __init__(self, maxsize, ttl):
            self.maxsize = maxsize
            self.ttl = ttl
            self._cache = {}
        def get(self, key):
            return self._cache.get(key)
        def set(self, key, value):
            if len(self._cache) >= self.maxsize:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = value
        def __contains__(self, key):
            return key in self._cache
        def __getitem__(self, key):
            return self._cache[key]
        def __setitem__(self, key, value):
            self.set(key, value)

try:
    from sqlalchemy import and_, case, distinct, func, or_, text, desc, asc
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session
    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SQLALCHEMY_AVAILABLE = False
    logger.warning("⚠️ SQLAlchemy not available")

try:
    from app.database import SessionLocal
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    logger.warning("⚠️ Database module not available")

try:
    from app.models import DeliveryReport
    MODELS_AVAILABLE = True
except ImportError:
    MODELS_AVAILABLE = False
    logger.warning("⚠️ Models not available")

# ============================================================
# CONFIGURATION
# ============================================================

CACHE_TTL = max(60, int(os.getenv("DN_ANALYTICS_CACHE_TTL", "300")))
DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))
SLA_TARGET_DAYS = int(os.getenv("DN_SLA_TARGET_DAYS", "3"))
FALLBACK_MODE = not (SQLALCHEMY_AVAILABLE and DATABASE_AVAILABLE and MODELS_AVAILABLE)

if FALLBACK_MODE:
    logger.warning("⚠️ DN Service running in FALLBACK MODE")

# ============================================================
# ENUMS
# ============================================================

class IntentType(Enum):
    DASHBOARD = "dashboard"
    STATUS = "status"
    HISTORY = "history"
    PENDING = "pending"
    SEARCH = "search"
    MENU = "menu"
    UNKNOWN = "unknown"

class MenuState(Enum):
    MAIN = "main"
    DN_SELECTION = "dn_selection"

# ============================================================
# DATACLASSES
# ============================================================

@dataclass
class DNContext:
    current_dn: Optional[str] = None
    last_question: Optional[str] = None
    menu_state: MenuState = MenuState.MAIN
    selected_option: Optional[str] = None
    awaiting_dn: bool = False

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except (TypeError, ValueError):
        return default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _format_date(value: Any) -> str:
    if not value:
        return "N/A"
    if isinstance(value, datetime):
        return value.strftime("%d-%b-%Y")
    if isinstance(value, date):
        return value.strftime("%d-%b-%Y")
    return str(value)

def _extract_dn_numbers(text: str) -> List[str]:
    return re.findall(r'(?<!\d)(\d{8,12})(?!\d)', text)

def _is_valid_dn(dn: str) -> bool:
    if not dn:
        return False
    cleaned = re.sub(r'[\s-]', '', dn)
    return cleaned.isdigit() and 8 <= len(cleaned) <= 12

# ============================================================
# MENU RENDERER
# ============================================================

class DNMenuRenderer:
    @staticmethod
    def render_main_menu() -> str:
        return "\n".join([
            "📦 *DN ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. DN Dashboard",
            "2. DN Status",
            "3. Pending DN",
            "4. Search DN",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type DN number for dashboard",
            "• Search [keyword]",
            "",
            "Reply with a number or DN number:"
        ])
    
    @staticmethod
    def render_dn_selection(prompt: str = "Enter DN number:") -> str:
        return "\n".join([
            "🔍 *DN Selection*",
            "",
            prompt,
            "",
            "💡 *Format:* 8-12 digit number",
            "Example: 6243714234",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_dn_dashboard(dn_no: str, data: Dict[str, Any]) -> str:
        lines = [
            f"📦 *DN Dashboard - {dn_no}*",
            "",
            "📊 *Key Information*",
            f"Division: {data.get('division', 'N/A')}",
            f"Order Type: {data.get('order_type', 'N/A')}",
            f"Customer Code: {data.get('customer_code', 'N/A')}",
            f"Dealer: {data.get('dealer', 'N/A')}",
            f"DN Work: {data.get('dn_work', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_pending_list(title: str, dns: List[Dict[str, Any]]) -> str:
        if not dns:
            return f"📋 *{title}*\n\n✅ No pending DNs found."
        
        lines = [f"📋 *{title}*", ""]
        for i, item in enumerate(dns[:10], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_code', 'N/A')
            division = item.get('division', 'N/A')
            lines.append(f"{i}. *DN {dn_no}*")
            lines.append(f"   Customer: {customer}")
            lines.append(f"   Division: {division}")
            lines.append("")
        
        if len(dns) > 10:
            lines.append(f"... and {len(dns) - 10} more")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)

# ============================================================
# INTENT ENGINE
# ============================================================

class IntentEngine:
    def detect_intent(self, question: str):
        question_lower = question.lower()
        
        if question_lower in ["menu", "dn menu", "options", "help"]:
            return IntentType.MENU, 1.0
        
        if "pending" in question_lower:
            return IntentType.PENDING, 0.8
        
        if "search" in question_lower or "find" in question_lower:
            return IntentType.SEARCH, 0.8
        
        return IntentType.UNKNOWN, 0.5

# ============================================================
# ENTITY ENGINE
# ============================================================

class EntityEngine:
    def extract_entities(self, question: str) -> Dict[str, Any]:
        entities = {
            "dn_numbers": [],
            "search_query": None,
        }
        
        dns = _extract_dn_numbers(question)
        if dns:
            entities["dn_numbers"] = dns
        
        # Extract search query
        match = re.search(r'(?:search|find|for)\s+([a-zA-Z0-9]+)', question, re.IGNORECASE)
        if match:
            entities["search_query"] = match.group(1)
        
        return entities

# ============================================================
# DN DASHBOARD BUILDER - USING YOUR TABLE COLUMNS
# ============================================================

class DNDashboardBuilder:
    def __init__(self, session: Session):
        self.session = session
    
    def build(self, dn_no: str) -> Optional[Dict[str, Any]]:
        try:
            query = self.session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.order_type,
                DeliveryReport.customer_code,
                DeliveryReport.dealer,
                DeliveryReport.dn_work,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            if not query:
                return None
            
            return {
                "dn_no": _text(query.dn_no),
                "division": _text(query.division),
                "order_type": _text(query.order_type),
                "customer_code": _text(query.customer_code),
                "dealer": _text(query.dealer),
                "dn_work": _text(query.dn_work),
            }
            
        except Exception as e:
            logger.error(f"Failed to build dashboard for DN {dn_no}: {e}")
            return None

# ============================================================
# MAIN DN ANALYTICS SERVICE
# ============================================================

class DNAnalysisService:
    _instance: Optional["DNAnalysisService"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "DNAnalysisService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self) -> None:
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._service_name = "dn_analysis"
        self._version = "22.0"
        
        self._intent_engine = IntentEngine()
        self._entity_engine = EntityEngine()
        self._menu_renderer = DNMenuRenderer()
        
        self._contexts: Dict[str, DNContext] = {}
        self._context_lock = threading.RLock()
        
        logger.info(f"✅ DNAnalysisService initialized (v{self._version})")
        if FALLBACK_MODE:
            logger.warning("   ⚠️ Running in FALLBACK MODE")
    
    @staticmethod
    def _session():
        if FALLBACK_MODE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Failed to get database session: {e}")
            return None
    
    def get_main_menu(self) -> str:
        return self._menu_renderer.render_main_menu()
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📦 DN Service processing: '{message_clean}'")
        
        if message_clean.lower() in ["menu", "help", "options"]:
            return self.get_main_menu()
        
        result = self.process_menu_input(sender, message_clean)
        response = result.get("response", self.get_main_menu())
        
        if result.get("exit_menu", False):
            return response
        
        return response
    
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        context = self._get_context(session_id)
        user_input = user_input.strip()
        
        # Check for "99" - Return to main menu
        if user_input == "99":
            return self._handle_main_menu_return(context)
        
        if user_input == "0":
            return self._handle_main_menu_return(context)
        
        # Check for DN number
        dns = _extract_dn_numbers(user_input)
        if dns and len(dns) == 1:
            context.current_dn = dns[0]
            result = self._get_dn_dashboard(context, dns[0])
            result["exit_menu"] = False
            return result
        
        # Natural language
        intent, confidence = self._intent_engine.detect_intent(user_input)
        entities = self._entity_engine.extract_entities(user_input)
        
        if intent == IntentType.PENDING:
            return self._get_pending_dns(context)
        
        if intent == IntentType.SEARCH:
            query = entities.get("search_query") or user_input
            return self._search_dns(context, query)
        
        # Menu options
        if context.menu_state == MenuState.MAIN:
            return self._handle_main_menu_option(context, user_input)
        
        return {
            "response": "\n".join([
                "❌ I didn't understand that.",
                "",
                "💡 *DN Service Commands:*",
                "• Type a DN number (8-12 digits) for dashboard",
                "• 'Pending' - Show pending DNs",
                "• 'Search [keyword]' - Search DNs",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "dn_menu",
            "action": "unknown_query",
            "data": {},
            "exit_menu": False
        }
    
    def _handle_main_menu_return(self, context: DNContext) -> Dict[str, Any]:
        context.menu_state = MenuState.MAIN
        context.selected_option = None
        context.awaiting_dn = False
        
        return {
            "response": self._menu_renderer.render_main_menu(),
            "menu_type": "dn_menu",
            "action": "main_menu",
            "data": {},
            "exit_menu": True
        }
    
    def _handle_main_menu_option(self, context: DNContext, option: str) -> Dict[str, Any]:
        if option == "1":
            context.menu_state = MenuState.DN_SELECTION
            context.selected_option = "dashboard"
            context.awaiting_dn = True
            
            return {
                "response": self._menu_renderer.render_dn_selection(),
                "menu_type": "dn_menu",
                "action": "dn_selection",
                "data": {},
                "exit_menu": False
            }
        
        return self._handle_quick_query(context, option)
    
    def _get_dn_dashboard(self, context: DNContext, dn_no: str) -> Dict[str, Any]:
        try:
            session = self._session()
            if session is None:
                return {
                    "response": f"⚠️ Database unavailable. Please check connection.\n\n0. Main Menu",
                    "menu_type": "dn_menu",
                    "action": "error",
                    "data": {},
                    "exit_menu": False
                }
            
            builder = DNDashboardBuilder(session)
            dashboard = builder.build(dn_no)
            
            if not dashboard:
                return {
                    "response": f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu",
                    "menu_type": "dn_menu",
                    "action": "dashboard",
                    "data": {"dn": dn_no, "error": "not_found"},
                    "exit_menu": False
                }
            
            return {
                "response": self._menu_renderer.render_dn_dashboard(dn_no, dashboard),
                "menu_type": "dn_menu",
                "action": "dashboard",
                "data": {"dn": dn_no, "dashboard": dashboard},
                "exit_menu": False
            }
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_pending_dns(self, context: DNContext) -> Dict[str, Any]:
        try:
            session = self._session()
            if session is None:
                return {
                    "response": "⚠️ Database unavailable.\n\n0. Main Menu",
                    "menu_type": "dn_menu",
                    "action": "error",
                    "data": {},
                    "exit_menu": False
                }
            
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_code,
                DeliveryReport.division,
            ).filter(
                DeliveryReport.id.isnot(None)
            ).order_by(
                DeliveryReport.id.desc()
            ).limit(20).all()
            
            dns = []
            for row in results:
                dns.append({
                    "dn_no": _text(row.dn_no),
                    "customer_code": _text(row.customer_code),
                    "division": _text(row.division),
                })
            
            return {
                "response": self._menu_renderer.render_pending_list("📋 Recent DNs", dns),
                "menu_type": "dn_menu",
                "action": "pending",
                "data": {"dns": dns},
                "exit_menu": False
            }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _search_dns(self, context: DNContext, query: str) -> Dict[str, Any]:
        try:
            session = self._session()
            if session is None:
                return {
                    "response": "⚠️ Database unavailable.\n\n0. Main Menu",
                    "menu_type": "dn_menu",
                    "action": "error",
                    "data": {},
                    "exit_menu": False
                }
            
            search_pattern = f"%{query}%"
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_code,
                DeliveryReport.division,
            ).filter(
                or_(
                    DeliveryReport.dn_no.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern),
                    DeliveryReport.division.ilike(search_pattern),
                )
            ).order_by(
                DeliveryReport.id.desc()
            ).limit(20).all()
            
            dns = []
            for row in results:
                dns.append({
                    "dn_no": _text(row.dn_no),
                    "customer_code": _text(row.customer_code),
                    "division": _text(row.division),
                })
            
            if not dns:
                return {
                    "response": f"🔍 No results found for '{query}'\n\n0. Main Menu",
                    "menu_type": "dn_menu",
                    "action": "search",
                    "data": {"query": query, "dns": []},
                    "exit_menu": False
                }
            
            return {
                "response": self._menu_renderer.render_pending_list(f"🔍 Search Results for '{query}'", dns),
                "menu_type": "dn_menu",
                "action": "search",
                "data": {"query": query, "dns": dns},
                "exit_menu": False
            }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _handle_quick_query(self, context: DNContext, query: str) -> Dict[str, Any]:
        if _is_valid_dn(query):
            context.current_dn = query
            return self._get_dn_dashboard(context, query)
        
        return {
            "response": "\n".join([
                "❌ I didn't understand that.",
                "",
                "💡 *Try one of these:*",
                "• '6243714234' - Show DN dashboard",
                "• 'Pending' - Show recent DNs",
                "• 'Search [keyword]' - Search DNs",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "dn_menu",
            "action": "unknown_query",
            "data": {},
            "exit_menu": False
        }
    
    def _get_context(self, session_id: str) -> DNContext:
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DNContext()
            return self._contexts[session_id]

# ============================================================
# SERVICE SINGLETON
# ============================================================

_service: Optional[DNAnalysisService] = None
_service_lock = threading.Lock()

def get_dn_analysis_service() -> DNAnalysisService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DNAnalysisService()
    return _service

def process_dn_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    service = get_dn_analysis_service()
    return service.process_menu_input(session_id, user_input)

def get_dn_main_menu() -> str:
    service = get_dn_analysis_service()
    return service.get_main_menu()

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DNAnalysisService",
    "get_dn_analysis_service",
    "process_dn_menu",
    "get_dn_main_menu",
]
