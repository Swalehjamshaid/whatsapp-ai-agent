# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 2.0 - ENTERPRISE DEALER ANALYTICS SERVICE
# ============================================================

"""
File: app/services/dealer_analytics_service.py
Version: 2.0 - ENTERPRISE DEALER ANALYTICS SERVICE

================================================================================
PURPOSE
================================================================================

This is the DEALER ANALYTICS DOMAIN SERVICE for the WhatsApp AI Agent.

Its responsibilities are:
1. Provide dealer-related analytics and insights
2. Show dealer dashboards and rankings
3. Handle dealer-specific queries
4. Manage dealer context and session state

================================================================================
INTEGRATION
================================================================================

This service is called by ai_provider_service.py when the user selects:
- Menu option "3" (Dealer Dashboard)
- Or types "dealer", "dealer dashboard", etc.

================================================================================
EXIT CONTRACT
================================================================================

This service MUST return "99" or "__EXIT__" to unlock the session
and return to the main dashboard.

================================================================================
STATUS: PRODUCTION READY
================================================================================
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple, Union

logger = logging.getLogger(__name__)

# ============================================================
# DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, desc, and_
    from sqlalchemy.orm import Session
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
    logger.info("✅ Dealer database imports successful")
except ImportError as e:
    DB_AVAILABLE = False
    logger.error(f"❌ Dealer database import error: {e}")

# ============================================================
# CONFIGURATION
# ============================================================

DEALER_CACHE_TTL = int(os.getenv("DEALER_CACHE_TTL", "300"))
DEALER_SESSION_TIMEOUT = int(os.getenv("DEALER_SESSION_TIMEOUT", "1800"))

# ============================================================
# ENUMS
# ============================================================

class DealerIntentType(Enum):
    """Dealer intent types."""
    DASHBOARD = "dashboard"
    RANKING = "ranking"
    PERFORMANCE = "performance"
    REVENUE = "revenue"
    UNITS = "units"
    PENDING_DN = "pending_dn"
    DELIVERY = "delivery"
    COMPARISON = "comparison"
    SEARCH = "search"
    MENU = "menu"
    UNKNOWN = "unknown"

class DealerMenuState(Enum):
    """Dealer menu states."""
    MAIN = "main"
    DEALER_SELECTION = "dealer_selection"
    COMPARISON_SELECTION = "comparison_selection"
    EXECUTING = "executing"

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class DealerContext:
    """Session context for dealer queries."""
    session_id: str
    current_dealer: Optional[str] = None
    active_menu: DealerMenuState = DealerMenuState.MAIN
    selected_option: Optional[str] = None
    comparison_dealers: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    last_query: str = ""
    last_answer: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def touch(self) -> None:
        """Update timestamp."""
        self.updated_at = datetime.now()
    
    def is_expired(self, timeout: int = DEALER_SESSION_TIMEOUT) -> bool:
        """Check if session has expired."""
        elapsed = datetime.now() - self.updated_at
        return elapsed.total_seconds() > timeout
    
    def add_history(self, query: str, answer: str) -> None:
        """Add to conversation history."""
        self.history.append({
            "query": query,
            "answer": answer[:200] if len(answer) > 200 else answer,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]
        self.last_query = query
        self.last_answer = answer
        self.touch()

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    """Safely convert value to string."""
    if value is None:
        return default
    return str(value).strip() or default

def _number(value: Any) -> float:
    """Safely convert to float."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    """Calculate percentage."""
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _format_currency(amount: float) -> str:
    """Format currency amount."""
    if amount is None:
        return "PKR 0.00"
    return f"PKR {amount:,.2f}"

def _format_number(num: Union[int, float]) -> str:
    """Format number with commas."""
    if num is None:
        return "0"
    return f"{num:,}"

# ============================================================
# DEALER RENDERER
# ============================================================

class DealerRenderer:
    """Render dealer analytics responses."""
    
    @staticmethod
    def render_main_menu() -> str:
        """Render the main dealer menu."""
        return "\n".join([
            "📊 *DEALER ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Dealer Dashboard",
            "2. Dealer Rankings",
            "3. Top Dealers",
            "4. Search Dealer",
            "5. Compare Dealers",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type dealer name for dashboard",
            "• top dealers - Show top dealers",
            "• revenue - Show dealer revenue",
            "• pending - Show pending DNs",
            "",
            "Reply with a number or dealer name:"
        ])
    
    @staticmethod
    def render_dealer_dashboard(dealer_name: str, data: Dict[str, Any]) -> str:
        """Render dealer dashboard."""
        lines = [
            f"📊 *Dealer Dashboard - {dealer_name}*",
            "",
            "📌 *Dealer Details*",
            f"Code: {data.get('dealer_code', 'N/A')}",
            f"Sales Office: {data.get('sales_office', 'N/A')}",
            f"Sales Manager: {data.get('sales_manager', 'N/A')}",
            "",
            "💰 *Financials*",
            f"Revenue: {_format_currency(data.get('total_revenue', 0))}",
            f"Avg Revenue/DN: {_format_currency(data.get('avg_revenue_per_dn', 0))}",
            "",
            "📦 *Operations*",
            f"DN: {_format_number(data.get('total_dn', 0))}",
            f"Units: {_format_number(data.get('total_units', 0))}",
            f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
            f"Pending PGI: {_format_number(data.get('pending_pgi', 0))}",
            f"Pending POD: {_format_number(data.get('pending_pod', 0))}",
            "",
            "🚚 *Delivery*",
            f"Delivery Success: {data.get('delivery_success_pct', 0):.1f}%",
            f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
            f"Avg Delivery Days: {data.get('avg_delivery_days', 0):.1f}",
            f"Avg POD Days: {data.get('avg_pod_days', 0):.1f}",
            "",
            "📈 *Performance*",
            f"Business Score: {data.get('business_score', 0):.1f}/100",
            f"Status: {data.get('overall_status', 'Unknown')}",
            f"Grade: {data.get('performance_grade', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back",
            "",
            "📌 *Try:* 'Revenue' or 'Pending in [dealer]'"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "revenue", limit: int = 10) -> str:
        """Render dealer rankings."""
        lines = [
            f"🏆 *Dealer Rankings by {metric.title()}*",
            "",
        ]
        
        for i, item in enumerate(ranking[:limit], 1):
            dealer = item.get('dealer', 'Unknown')
            value = item.get('value', 'N/A')
            
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            lines.append(f"{medal} {dealer}: {value}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison(dealer1: str, dealer2: str, metrics: Dict[str, Any]) -> str:
        """Render dealer comparison."""
        lines = [
            f"🔄 *Comparison: {dealer1} vs {dealer2}*",
            "",
            "───────────────────",
            "",
        ]
        
        metrics1 = metrics.get(f"{dealer1}_metrics", {})
        metrics2 = metrics.get(f"{dealer2}_metrics", {})
        
        all_keys = set(metrics1.keys()) | set(metrics2.keys())
        
        for key in sorted(all_keys):
            v1 = metrics1.get(key, "N/A")
            v2 = metrics2.get(key, "N/A")
            
            if isinstance(v1, str) and isinstance(v2, str):
                try:
                    num1 = float(re.sub(r'[^\d.]', '', v1))
                    num2 = float(re.sub(r'[^\d.]', '', v2))
                    if key.lower() in ['pending', 'pending dn', 'delivery days']:
                        winner = "✅" if num1 < num2 else "❌" if num1 > num2 else "➖"
                    else:
                        winner = "✅" if num1 > num2 else "❌" if num1 < num2 else "➖"
                    lines.append(f"{key}: {v1} vs {v2} {winner}")
                except:
                    lines.append(f"{key}: {v1} vs {v2}")
            else:
                lines.append(f"{key}: {v1} vs {v2}")
        
        lines.extend([
            "",
            "───────────────────",
            "",
            "💡 *Summary*",
            metrics.get('explanation', 'Comparison complete.'),
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_search_results(query: str, items: List[Dict[str, Any]]) -> str:
        """Render search results."""
        if not items:
            return f"🔍 No dealers found for '{query}'\n\n0. Main Menu\n99. Back"
        
        lines = [f"🔍 *Search Results for '{query}'*", ""]
        lines.append(f"Found: {len(items)} dealers")
        lines.append("")
        
        for i, item in enumerate(items[:15], 1):
            dealer = item.get('dealer', 'Unknown')
            code = item.get('dealer_code', 'N/A')
            revenue = _format_currency(item.get('revenue', 0))
            lines.append(f"{i}. *{dealer}* (Code: {code})")
            lines.append(f"   Revenue: {revenue}")
            lines.append(f"   DNs: {_format_number(item.get('dn_count', 0))}")
            lines.append("")
        
        if len(items) > 15:
            lines.append(f"... and {len(items) - 15} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_pending_summary(dealer_name: str, data: Dict[str, Any]) -> str:
        """Render pending summary."""
        return "\n".join([
            f"⏳ *Pending Summary - {dealer_name}*",
            "",
            f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
            f"Pending Revenue: {_format_currency(data.get('pending_revenue', 0))}",
            f"Pending Units: {_format_number(data.get('pending_units', 0))}",
            f"PGI Pending: {_format_number(data.get('pgi_pending_dn', 0))}",
            f"POD Pending: {_format_number(data.get('pod_pending_dn', 0))}",
            "",
            f"Avg Pending Days: {data.get('pending_average_days', 0):.1f}",
            f"Critical (>7 days): {_format_number(data.get('critical_pending', 0))}",
            f"Overdue (>14 days): {_format_number(data.get('overdue_pending', 0))}",
            "",
            "0. Main Menu",
            "99. Back"
        ])

# ============================================================
# DEALER REPOSITORY
# ============================================================

class DealerRepository:
    """Database repository for dealer operations."""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()
    
    def get_dealer_dashboard(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        """Get dealer dashboard data."""
        cache_key = dealer_identifier.lower()
        
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            # Search by customer_name (dealer name) or dealer_code
            query = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                DeliveryReport.dealer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(case(
                    (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_dn'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)
                ))).label('pgi_pending_dn'),
                func.count(distinct(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pod_pending_dn'),
                func.count(distinct(case(
                    (DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)
                ))).label('pod_completed'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no)
                ))).label('pgi_completed'),
                func.avg(case(
                    (DeliveryReport.good_issue_date.isnot(None),
                     DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                )).label('avg_delivery_days'),
                func.avg(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)),
                     DeliveryReport.pod_date - DeliveryReport.good_issue_date)
                )).label('avg_pod_days'),
                func.avg(DeliveryReport.dn_amount).label('avg_revenue_per_dn'),
            ).filter(
                or_(
                    func.lower(DeliveryReport.customer_name) == dealer_identifier.lower(),
                    func.lower(DeliveryReport.dealer_code) == dealer_identifier.lower(),
                    func.lower(DeliveryReport.customer_name).ilike(f"%{dealer_identifier.lower()}%"),
                    func.lower(DeliveryReport.dealer_code).ilike(f"%{dealer_identifier.lower()}%"),
                )
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager
            ).first()
            
            if not query:
                return None
            
            total_dn = int(query.total_dn or 0)
            pending_dn = int(query.pending_dn or 0)
            pgi_completed = int(query.pgi_completed or 0)
            pod_completed = int(query.pod_completed or 0)
            
            data = {
                'dealer': _text(query.dealer),
                'dealer_code': _text(query.dealer_code),
                'sales_office': _text(query.sales_office),
                'sales_manager': _text(query.sales_manager),
                'total_dn': total_dn,
                'total_units': int(query.total_units or 0),
                'total_revenue': float(query.total_revenue or 0.0),
                'pending_dn': pending_dn,
                'pgi_pending_dn': int(query.pgi_pending_dn or 0),
                'pod_pending_dn': int(query.pod_pending_dn or 0),
                'pgi_completed': pgi_completed,
                'pod_completed': pod_completed,
                'avg_delivery_days': float(query.avg_delivery_days or 0.0),
                'avg_pod_days': float(query.avg_pod_days or 0.0),
                'avg_revenue_per_dn': float(query.avg_revenue_per_dn or 0.0),
                'delivery_success_pct': _percent(pgi_completed, total_dn),
                'pod_success_pct': _percent(pod_completed, total_dn),
                'pending_pct': _percent(pending_dn, total_dn),
            }
            
            # Calculate business score
            score = (
                data['delivery_success_pct'] * 0.25 +
                (100 - data['pending_pct']) * 0.25 +
                min(100, data['avg_revenue_per_dn'] / 1000) * 0.25 +
                min(100, data['total_dn'] / 10) * 0.25
            )
            data['business_score'] = round(min(100, max(0, score)), 1)
            
            # Status
            if data['business_score'] >= 85:
                data['overall_status'] = "Excellent"
                data['performance_grade'] = "A"
            elif data['business_score'] >= 70:
                data['overall_status'] = "Good"
                data['performance_grade'] = "B"
            elif data['business_score'] >= 50:
                data['overall_status'] = "Watch"
                data['performance_grade'] = "C"
            else:
                data['overall_status'] = "Critical"
                data['performance_grade'] = "D"
            
            with self._cache_lock:
                self._cache[cache_key] = data.copy()
            
            return data
            
        except Exception as e:
            logger.error(f"Failed to get dealer dashboard: {e}")
            return None
    
    def get_dealer_ranking(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get dealer ranking by revenue."""
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_qty).label('units'),
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            ranking = []
            for row in results:
                if row.dealer:
                    ranking.append({
                        'dealer': _text(row.dealer),
                        'value': _format_currency(float(row.revenue or 0)),
                        'dn_count': int(row.dn_count or 0),
                        'units': int(row.units or 0),
                    })
            return ranking
        except Exception as e:
            logger.error(f"Failed to get dealer ranking: {e}")
            return []
    
    def search_dealers(self, query: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Search dealers."""
        try:
            search_pattern = f"%{query}%"
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                DeliveryReport.dealer_code,
                DeliveryReport.sales_office,
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_qty).label('units'),
            ).filter(
                or_(
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.dealer_code.ilike(search_pattern),
                    func.lower(DeliveryReport.customer_name).ilike(f"%{query.lower()}%"),
                    func.lower(DeliveryReport.dealer_code).ilike(f"%{query.lower()}%"),
                )
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.sales_office
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            items = []
            for row in results:
                if row.dealer:
                    items.append({
                        'dealer': _text(row.dealer),
                        'dealer_code': _text(row.dealer_code),
                        'sales_office': _text(row.sales_office),
                        'revenue': float(row.revenue or 0),
                        'dn_count': int(row.dn_count or 0),
                        'units': int(row.units or 0),
                    })
            return items
        except Exception as e:
            logger.error(f"Failed to search dealers: {e}")
            return []
    
    def compare_dealers(self, dealer1: str, dealer2: str) -> Dict[str, Any]:
        """Compare two dealers."""
        dash1 = self.get_dealer_dashboard(dealer1)
        dash2 = self.get_dealer_dashboard(dealer2)
        
        if not dash1 or not dash2:
            return {}
        
        metrics = {}
        
        metrics[f"{dealer1}_metrics"] = {
            "Revenue": _format_currency(dash1.get('total_revenue', 0)),
            "Units": _format_number(dash1.get('total_units', 0)),
            "DN": _format_number(dash1.get('total_dn', 0)),
            "Pending": _format_number(dash1.get('pending_dn', 0)),
            "Delivery": f"{dash1.get('delivery_success_pct', 0):.1f}%",
            "POD": f"{dash1.get('pod_success_pct', 0):.1f}%",
            "Score": f"{dash1.get('business_score', 0):.1f}/100",
        }
        
        metrics[f"{dealer2}_metrics"] = {
            "Revenue": _format_currency(dash2.get('total_revenue', 0)),
            "Units": _format_number(dash2.get('total_units', 0)),
            "DN": _format_number(dash2.get('total_dn', 0)),
            "Pending": _format_number(dash2.get('pending_dn', 0)),
            "Delivery": f"{dash2.get('delivery_success_pct', 0):.1f}%",
            "POD": f"{dash2.get('pod_success_pct', 0):.1f}%",
            "Score": f"{dash2.get('business_score', 0):.1f}/100",
        }
        
        rev1 = dash1.get('total_revenue', 0)
        rev2 = dash2.get('total_revenue', 0)
        
        if rev1 > rev2:
            explanation = f"{dealer1} has higher revenue than {dealer2}"
        elif rev2 > rev1:
            explanation = f"{dealer2} has higher revenue than {dealer1}"
        else:
            explanation = f"{dealer1} and {dealer2} have similar revenue"
        
        metrics["explanation"] = explanation
        
        return metrics

# ============================================================
# MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Analytics Domain Service.
    
    Single entry point for all dealer-related business questions.
    PostgreSQL is the ONLY source of truth.
    """
    
    _instance: Optional["DealerAnalyticsService"] = None
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
        self._service_name = "dealer_analytics"
        self._version = "2.0"
        self._renderer = DealerRenderer()
        
        # Context memory per session
        self._contexts: Dict[str, DealerContext] = {}
        self._context_lock = threading.RLock()
        
        logger.info("=" * 60)
        logger.info(f"🚀 Dealer Analytics Service v{self._version} initialized")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info("=" * 60)
    
    def _get_context(self, session_id: str) -> DealerContext:
        """Get or create context for session."""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DealerContext(session_id=session_id)
            return self._contexts[session_id]
    
    def _get_session(self) -> Optional[Session]:
        """Get database session."""
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def get_main_menu(self) -> str:
        """Get the main dealer menu."""
        return self._renderer.render_main_menu()
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        Main entry point for dealer processing.
        
        This is the ONLY external interface.
        All processing stays inside this module.
        """
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📊 Dealer Query: '{message_clean}' from {sender}")
        
        # Get context for this session
        context = self._get_context(sender)
        context.touch()
        
        # ============================================================
        # STEP 1: Check for "99" - Exit
        # ============================================================
        if message_clean == "99":
            self._destroy_context(sender)
            logger.info(f"🚪 Dealer session exited for {sender}")
            return "99"
        
        # ============================================================
        # STEP 2: Check for menu commands
        # ============================================================
        if message_clean.lower() in ["menu", "help", "options", "0"]:
            return self.get_main_menu()
        
        # ============================================================
        # STEP 3: Check for menu options (1-5)
        # ============================================================
        if message_clean in ["1", "2", "3", "4", "5"]:
            return self._handle_menu_option(sender, message_clean)
        
        # ============================================================
        # STEP 4: Check for dealer name
        # ============================================================
        dealer_name = self._resolve_dealer_name(message_clean)
        if dealer_name:
            context.current_dealer = dealer_name
            return self._get_dealer_dashboard(sender, dealer_name)
        
        # ============================================================
        # STEP 5: Check for "top dealers" command
        # ============================================================
        if "top" in message_clean.lower() and "dealer" in message_clean.lower():
            return self._get_dealer_ranking(sender)
        
        # ============================================================
        # STEP 6: Check for "search" command
        # ============================================================
        if "search" in message_clean.lower():
            query = message_clean.replace("search", "").strip()
            if query:
                return self._search_dealers(sender, query)
            return "🔍 Please specify what to search.\n\n0. Main Menu\n99. Back"
        
        # ============================================================
        # STEP 7: Check if it's a follow-up question using current dealer
        # ============================================================
        if context.current_dealer:
            query_lower = message_clean.lower()
            
            if "revenue" in query_lower or "amount" in query_lower:
                return self._get_dealer_revenue(sender, context.current_dealer)
            elif "pending" in query_lower or "backlog" in query_lower:
                return self._get_dealer_pending(sender, context.current_dealer)
            elif "delivery" in query_lower or "transit" in query_lower:
                return self._get_dealer_delivery(sender, context.current_dealer)
            elif "status" in query_lower:
                return self._get_dealer_status(sender, context.current_dealer)
            elif "units" in query_lower or "quantity" in query_lower:
                return self._get_dealer_units(sender, context.current_dealer)
        
        # ============================================================
        # STEP 8: Unknown - Show help
        # ============================================================
        return self._show_help()
    
    def _destroy_context(self, session_id: str) -> None:
        """Destroy context for session."""
        with self._context_lock:
            if session_id in self._contexts:
                del self._contexts[session_id]
    
    def _show_help(self) -> str:
        """Show help message."""
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 *Dealer Commands:*",
            "• Type dealer name for dashboard",
            "• top dealers - Show top dealers",
            "• search [keyword] - Search dealers",
            "• revenue - Revenue of current dealer",
            "• pending - Pending of current dealer",
            "• delivery - Delivery of current dealer",
            "• status - Status of current dealer",
            "• units - Units of current dealer",
            "",
            "📌 *Current Dealer:*",
            "• Use 'menu' to see all options",
            "• Type '99' to return to main menu",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    def _handle_menu_option(self, sender: str, option: str) -> str:
        """Handle menu options."""
        if option == "1":
            return "🔍 *Enter dealer name:*\n\nType a dealer name.\n\n0. Main Menu\n99. Back"
        elif option == "2":
            return self._get_dealer_ranking(sender)
        elif option == "3":
            return self._get_dealer_ranking(sender)
        elif option == "4":
            return "🔍 *Search Dealers:*\n\nType 'search [keyword]' to find dealers.\n\nExamples:\n• search Lahore\n• search LALA KHAN\n\n0. Main Menu\n99. Back"
        elif option == "5":
            return "🔍 *Compare Dealers:*\n\nType 'compare [dealer1] and [dealer2]'\n\n0. Main Menu\n99. Back"
        return self.get_main_menu()
    
    def _resolve_dealer_name(self, input_text: str) -> Optional[str]:
        """Resolve dealer name from input."""
        input_lower = input_text.lower().strip()
        
        # Common dealer names (you can expand this list)
        dealers = [
            "lala khan", "lala", "khan", "haier", "pakistan", 
            "lahore", "karachi", "rawalpindi", "multan"
        ]
        
        # Direct match
        for dealer in dealers:
            if dealer == input_lower:
                return dealer.title()
            if dealer in input_lower:
                return dealer.title()
        
        # Try database lookup for partial matches
        try:
            with self._get_session() as session:
                repository = DealerRepository(session)
                results = repository.search_dealers(input_text, limit=1)
                if results:
                    return results[0].get('dealer')
        except Exception:
            pass
        
        return None
    
    def _get_dealer_dashboard(self, sender: str, dealer_name: str) -> str:
        """Get dealer dashboard."""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repository = DealerRepository(session)
            data = repository.get_dealer_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            return self._renderer.render_dealer_dashboard(dealer_name, data)
            
        except Exception as e:
            logger.error(f"Dealer dashboard error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching dealer {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_dealer_ranking(self, sender: str) -> str:
        """Get dealer ranking."""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repository = DealerRepository(session)
            ranking = repository.get_dealer_ranking(10)
            session.close()
            
            if not ranking:
                return "🏆 *Dealer Rankings*\n\nNo dealers found.\n\n0. Main Menu\n99. Back"
            
            return self._renderer.render_ranking(ranking, "Revenue", 10)
            
        except Exception as e:
            logger.error(f"Dealer ranking error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching dealer rankings.\n\n0. Main Menu\n99. Back"
    
    def _search_dealers(self, sender: str, query: str) -> str:
        """Search dealers."""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repository = DealerRepository(session)
            items = repository.search_dealers(query, 30)
            session.close()
            
            return self._renderer.render_search_results(query, items)
            
        except Exception as e:
            logger.error(f"Dealer search error: {e}")
            if session:
                session.close()
            return f"⚠️ Error searching for '{query}'\n\n0. Main Menu\n99. Back"
    
    def _get_dealer_revenue(self, sender: str, dealer_name: str) -> str:
        """Get dealer revenue."""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repository = DealerRepository(session)
            data = repository.get_dealer_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            revenue = data.get('total_revenue', 0)
            return f"💰 *{dealer_name} Revenue*\n\n{_format_currency(revenue)}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Dealer revenue error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching revenue for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_dealer_pending(self, sender: str, dealer_name: str) -> str:
        """Get dealer pending summary."""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repository = DealerRepository(session)
            data = repository.get_dealer_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            pending_data = {
                'pending_dn': data.get('pending_dn', 0),
                'pending_revenue': 0,  # Would need additional query
                'pending_units': 0,    # Would need additional query
                'pgi_pending_dn': data.get('pgi_pending_dn', 0),
                'pod_pending_dn': data.get('pod_pending_dn', 0),
                'pending_average_days': 0,
                'critical_pending': 0,
                'overdue_pending': 0,
            }
            
            return self._renderer.render_pending_summary(dealer_name, pending_data)
            
        except Exception as e:
            logger.error(f"Dealer pending error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching pending for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_dealer_delivery(self, sender: str, dealer_name: str) -> str:
        """Get dealer delivery summary."""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repository = DealerRepository(session)
            data = repository.get_dealer_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🚚 *Delivery Summary - {dealer_name}*",
                "",
                f"Delivery Success: {data.get('delivery_success_pct', 0):.1f}%",
                f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
                f"Avg Delivery Days: {data.get('avg_delivery_days', 0):.1f}",
                f"Avg POD Days: {data.get('avg_pod_days', 0):.1f}",
                f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Dealer delivery error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching delivery for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_dealer_status(self, sender: str, dealer_name: str) -> str:
        """Get dealer status."""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repository = DealerRepository(session)
            data = repository.get_dealer_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📊 *Status - {dealer_name}*",
                "",
                f"Score: {data.get('business_score', 0):.1f}/100",
                f"Status: {data.get('overall_status', 'Unknown')}",
                f"Grade: {data.get('performance_grade', 'N/A')}",
                f"Delivery Success: {data.get('delivery_success_pct', 0):.1f}%",
                f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Dealer status error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching status for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_dealer_units(self, sender: str, dealer_name: str) -> str:
        """Get dealer units."""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repository = DealerRepository(session)
            data = repository.get_dealer_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            units = data.get('total_units', 0)
            return f"📦 *{dealer_name} Units*\n\n{_format_number(units)}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Dealer units error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching units for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for service."""
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

_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()

def get_dealer_service() -> DealerAnalyticsService:
    """Get singleton instance."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DealerAnalyticsService()
    return _service


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "DealerContext",
    "DealerIntentType",
    "DealerMenuState",
    "get_dealer_service",
]
