"""
File: app/services/dn_analysis.py
Version: 33.0 - DEBUG VERSION WITH BETTER ERROR HANDLING
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
    logger.info("✅ Database imports successful")
except ImportError as e:
    DB_AVAILABLE = False
    logger.error(f"❌ Database import error: {e}")

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
            f"Customer Name: {data.get('customer_name', 'N/A')}",
            f"Dealer: {data.get('dealer', 'N/A')}",
            f"DN Work: {data.get('dn_work', 'N/A')}",
            f"Status: {data.get('delivery_status', 'Pending')}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_pending_list(items: List[Dict[str, Any]]) -> str:
        if not items:
            return "📋 *Pending DNs*\n\n✅ No pending DNs found.\n\n0. Main Menu\n99. Back"
        
        lines = ["📋 *Pending DNs*", ""]
        lines.append(f"Total: {len(items)}")
        lines.append("")
        
        for i, item in enumerate(items[:15], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_name', item.get('customer_code', 'N/A'))
            status = item.get('delivery_status', 'Pending')
            lines.append(f"{i}. *DN {dn_no}*")
            lines.append(f"   Customer: {customer}")
            lines.append(f"   Status: {status}")
            lines.append("")
        
        if len(items) > 15:
            lines.append(f"... and {len(items) - 15} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_search_results(query: str, items: List[Dict[str, Any]]) -> str:
        if not items:
            return f"🔍 No results found for '{query}'\n\n0. Main Menu\n99. Back"
        
        lines = [f"🔍 *Search Results for '{query}'*", ""]
        lines.append(f"Found: {len(items)} DNs")
        lines.append("")
        
        for i, item in enumerate(items[:15], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_name', item.get('customer_code', 'N/A'))
            status = item.get('delivery_status', 'Pending')
            lines.append(f"{i}. *DN {dn_no}* - {customer} ({status})")
        
        if len(items) > 15:
            lines.append(f"... and {len(items) - 15} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)

# ============================================================
# MAIN DN SERVICE
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
        self._version = "33.0"
        self._menu_renderer = DNMenuRenderer()
        
        # Context memory per session
        self._contexts: Dict[str, Dict[str, Any]] = {}
        self._context_lock = threading.RLock()
        
        logger.info("=" * 60)
        logger.info("🚀 DN Service v33.0 initialized (DEBUG VERSION)")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info("=" * 60)
    
    def _get_context(self, session_id: str) -> Dict[str, Any]:
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = {"current_dn": None}
            return self._contexts[session_id]
    
    @staticmethod
    def _get_session() -> Optional[Session]:
        if not DB_AVAILABLE:
            logger.error("❌ Database not available")
            return None
        try:
            session = SessionLocal()
            logger.info("✅ Database session created")
            return session
        except Exception as e:
            logger.error(f"❌ Database session error: {e}")
            return None
    
    def get_main_menu(self) -> str:
        return self._menu_renderer.render_main_menu()
    
    def _debug_query(self, session: Session, dn_no: str) -> bool:
        """Debug function to check if DN exists"""
        try:
            # Try raw count first
            count = session.query(DeliveryReport).filter(DeliveryReport.dn_no == dn_no).count()
            logger.info(f"🔍 DEBUG: DN {dn_no} count = {count}")
            return count > 0
        except Exception as e:
            logger.error(f"🔍 DEBUG: Error checking DN {dn_no}: {e}")
            return False
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📦 DN Service: '{message_clean}' from {sender}")
        
        context = self._get_context(sender)
        
        # STEP 1: Check for "99" - Exit
        if message_clean == "99":
            context["current_dn"] = None
            return "99"
        
        # STEP 2: Check for menu commands
        if message_clean.lower() in ["menu", "help", "options", "0"]:
            return self.get_main_menu()
        
        # STEP 3: Check for menu options (1, 2, 3)
        if message_clean in ["1", "2", "3"]:
            return self._handle_menu_option(sender, message_clean)
        
        # STEP 4: Check for DN number
        dn = _extract_dn(message_clean)
        if dn and _is_valid_dn(dn):
            logger.info(f"🔍 DN detected: {dn}")
            context["current_dn"] = dn
            return self._get_dn_dashboard(sender, dn)
        
        # STEP 5: Check for pending
        if message_clean.lower() in ["pending", "pending dn", "pending dns"]:
            return self._get_pending_dns(sender)
        
        # STEP 6: Check for search
        if "search" in message_clean.lower():
            query = message_clean.replace("search", "").strip()
            if query:
                return self._search_dns(sender, query)
            return "🔍 Please specify what to search."
        
        # STEP 7: Follow-up
        if context.get("current_dn"):
            query_lower = message_clean.lower()
            if "status" in query_lower:
                return self._get_dn_status(sender, context["current_dn"])
            elif "revenue" in query_lower or "amount" in query_lower:
                return self._get_dn_revenue(sender, context["current_dn"])
            elif "units" in query_lower or "quantity" in query_lower:
                return self._get_dn_units(sender, context["current_dn"])
            elif "customer" in query_lower:
                return self._get_dn_customer(sender, context["current_dn"])
            elif "dealer" in query_lower:
                return self._get_dn_dealer(sender, context["current_dn"])
        
        return self._show_help()
    
    def _show_help(self) -> str:
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 *DN Commands:*",
            "• Type a DN number (8-12 digits) for dashboard",
            "• pending - Show pending DNs",
            "• search [keyword] - Search DNs",
            "• status - Status of current DN",
            "• revenue - Revenue of current DN",
            "• units - Units of current DN",
            "• customer - Customer of current DN",
            "• dealer - Dealer of current DN",
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
        """Get DN dashboard with detailed debugging"""
        logger.info(f"🔍 Getting dashboard for DN: {dn_no}")
        
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            # DEBUG: Check if DN exists
            logger.info(f"🔍 Checking if DN {dn_no} exists...")
            exists = self._debug_query(session, dn_no)
            
            if not exists:
                logger.warning(f"⚠️ DN {dn_no} NOT FOUND in database")
                session.close()
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            logger.info(f"✅ DN {dn_no} FOUND! Querying details...")
            
            # Get DN details
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.order_type,
                DeliveryReport.customer_code,
                DeliveryReport.customer_name,
                DeliveryReport.dealer,
                DeliveryReport.dealer_code,
                DeliveryReport.dn_work,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_create_date,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            logger.info(f"✅ DN {dn_no} data retrieved successfully")
            
            data = {
                'dn_no': _text(result.dn_no),
                'division': _text(result.division),
                'order_type': _text(result.order_type),
                'customer_code': _text(result.customer_code),
                'customer_name': _text(result.customer_name),
                'dealer': _text(result.dealer),
                'dealer_code': _text(result.dealer_code),
                'dn_work': _text(result.dn_work),
                'delivery_status': _text(result.delivery_status, 'Pending'),
            }
            
            return self._menu_renderer.render_dn_dashboard(data)
            
        except Exception as e:
            logger.error(f"❌ Dashboard error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            if session:
                session.close()
            return f"⚠️ Error fetching DN {dn_no}: {str(e)[:100]}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_status(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.delivery_status,
                DeliveryReport.dn_create_date,
                DeliveryReport.customer_name,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📊 *DN {dn_no} - Status*",
                "",
                f"Status: {_text(result.delivery_status, 'Pending')}",
                f"Created: {_text(result.dn_create_date, 'N/A')}",
                f"Customer: {_text(result.customer_name)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Status error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching status for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_revenue(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_amount,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            amount = result.dn_amount or 0
            return f"💰 *DN {dn_no} Revenue*\n\nPKR {amount:,.2f}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Revenue error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching revenue for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_units(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_qty,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            qty = result.dn_qty or 0
            return f"📦 *DN {dn_no} Units*\n\n{qty:,}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Units error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching units for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_customer(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"👤 *Customer - DN {dn_no}*",
                "",
                f"Name: {_text(result.customer_name)}",
                f"Code: {_text(result.customer_code)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Customer error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching customer for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_dealer(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dealer,
                DeliveryReport.dealer_code,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🏪 *Dealer - DN {dn_no}*",
                "",
                f"Name: {_text(result.dealer)}",
                f"Code: {_text(result.dealer_code)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Dealer error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching dealer for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_pending_dns(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.delivery_status,
            ).filter(
                or_(
                    DeliveryReport.pending_flag.is_(True),
                    DeliveryReport.pod_date.is_(None)
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(30).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name, row.customer_code),
                    'customer_code': _text(row.customer_code),
                    'delivery_status': _text(row.delivery_status, 'Pending'),
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
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.delivery_status,
            ).filter(
                or_(
                    DeliveryReport.dn_no.ilike(search_pattern),
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern),
                    DeliveryReport.division.ilike(search_pattern),
                    DeliveryReport.dealer.ilike(search_pattern),
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(30).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name, row.customer_code),
                    'customer_code': _text(row.customer_code),
                    'delivery_status': _text(row.delivery_status, 'Pending'),
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
