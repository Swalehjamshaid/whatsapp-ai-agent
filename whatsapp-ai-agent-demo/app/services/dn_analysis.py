"""
File: app/services/dn_analysis.py
Version: 28.0 - SIMPLIFIED DN SERVICE
USES ONLY EXISTING COLUMNS
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)

# ============================================================
# DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, desc
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

DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _extract_dn(text: str) -> Optional[str]:
    match = re.search(r'\b(\d{8,12})\b', text)
    return match.group(1) if match else None

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
            "2. Pending DN",
            "3. Search DN",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type DN number for dashboard",
            "• pending - Show pending DNs",
            "• search [keyword] - Search DNs",
            "",
            "Reply with a number or DN number:"
        ])
    
    @staticmethod
    def render_dn_dashboard(data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        return "\n".join([
            f"📦 *DN Dashboard - {dn_no}*",
            "",
            "📊 *Key Information*",
            f"Division: {data.get('division', 'N/A')}",
            f"Order Type: {data.get('order_type', 'N/A')}",
            f"Customer Code: {data.get('customer_code', 'N/A')}",
            f"Dealer: {data.get('dealer', 'N/A')}",
            f"DN Work: {data.get('dn_work', 'N/A')}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_pending_list(items: List[Dict[str, Any]]) -> str:
        if not items:
            return "📋 *Pending DNs*\n\n✅ No pending DNs found.\n\n0. Main Menu\n99. Back"
        
        lines = ["📋 *Pending DNs*", ""]
        for i, item in enumerate(items[:10], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_code', 'N/A')
            division = item.get('division', 'N/A')
            lines.append(f"{i}. *DN {dn_no}*")
            lines.append(f"   Customer: {customer}")
            lines.append(f"   Division: {division}")
            lines.append("")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_search_results(query: str, items: List[Dict[str, Any]]) -> str:
        if not items:
            return f"🔍 No results found for '{query}'\n\n0. Main Menu\n99. Back"
        
        lines = [f"🔍 *Search Results for '{query}'*", ""]
        for i, item in enumerate(items[:10], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_code', 'N/A')
            lines.append(f"{i}. *DN {dn_no}* - {customer}")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)

# ============================================================
# MAIN DN SERVICE - SIMPLIFIED
# ============================================================

class DNAnalysisService:
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
        self._version = "28.0"
        self._menu_renderer = DNMenuRenderer()
        
        logger.info("=" * 60)
        logger.info("🚀 DN Service v28.0 initialized (SIMPLIFIED)")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info("=" * 60)
    
    @staticmethod
    def _get_session() -> Optional[Session]:
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def get_main_menu(self) -> str:
        return self._menu_renderer.render_main_menu()
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📦 DN Service: '{message_clean}' from {sender}")
        
        # Check for "99" - Exit
        if message_clean == "99":
            return "99"
        
        # Check for menu
        if message_clean.lower() in ["menu", "help", "options", "0"]:
            return self.get_main_menu()
        
        # Check for menu options
        if message_clean in ["1", "2", "3"]:
            return self._handle_menu_option(sender, message_clean)
        
        # Check for DN number
        dn = _extract_dn(message_clean)
        if dn and _is_valid_dn(dn):
            return self._get_dn_dashboard(sender, dn)
        
        # Check for pending
        if message_clean.lower() in ["pending", "pending dn", "pending dns"]:
            return self._get_pending_dns(sender)
        
        # Check for search
        if "search" in message_clean.lower():
            query = message_clean.replace("search", "").strip()
            if query:
                return self._search_dns(sender, query)
            return "🔍 Please specify what to search."
        
        # Help
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 *Commands:*",
            "• Type DN number for dashboard",
            "• pending - Show pending DNs",
            "• search [keyword] - Search DNs",
            "• menu - Show DN menu",
            "• 99 - Return to main menu",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    def _handle_menu_option(self, sender: str, option: str) -> str:
        if option == "1":
            return "🔍 *Enter DN number:*\n\nType an 8-12 digit DN number.\n\n0. Main Menu\n99. Back"
        elif option == "2":
            return self._get_pending_dns(sender)
        elif option == "3":
            return "🔍 *Search DNs:*\n\nType 'search [keyword]' to find DNs.\n\n0. Main Menu\n99. Back"
        return self.get_main_menu()
    
    def _get_dn_dashboard(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.order_type,
                DeliveryReport.customer_code,
                DeliveryReport.dealer,
                DeliveryReport.dn_work,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            data = {
                'dn_no': _text(result.dn_no),
                'division': _text(result.division),
                'order_type': _text(result.order_type),
                'customer_code': _text(result.customer_code),
                'dealer': _text(result.dealer),
                'dn_work': _text(result.dn_work),
            }
            
            return self._menu_renderer.render_dn_dashboard(data)
            
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_pending_dns(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_code,
                DeliveryReport.division,
            ).order_by(
                desc(DeliveryReport.id)
            ).limit(20).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_code': _text(row.customer_code),
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
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            search_pattern = f"%{query}%"
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_code,
            ).filter(
                or_(
                    DeliveryReport.dn_no.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern),
                    DeliveryReport.division.ilike(search_pattern),
                )
            ).order_by(
                desc(DeliveryReport.id)
            ).limit(20).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_code': _text(row.customer_code),
                })
            
            session.close()
            return self._menu_renderer.render_search_results(query, items)
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            if session:
                session.close()
            return f"⚠️ Error searching for '{query}'\n\n0. Main Menu\n99. Back"
    
    def health_check(self) -> Dict[str, Any]:
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "exit_command": "99",
            "timestamp": datetime.now().isoformat()
        }

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
    result = service.process_whatsapp_query(user_input, session_id)
    
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
    service = get_dn_analysis_service()
    return service.get_main_menu()

__all__ = [
    "DNAnalysisService",
    "get_dn_analysis_service",
    "process_dn_menu",
    "get_dn_main_menu",
]
