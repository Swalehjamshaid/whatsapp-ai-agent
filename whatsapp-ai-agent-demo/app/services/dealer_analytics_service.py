#!/usr/bin/env python3
"""Dealer Analytics Service

Cleaned and simplified implementation of the dealer analytics service
that integrates with SQLAlchemy `SessionLocal` and `DeliveryReport`.
This version is focused on being syntactically correct and importable
for local testing (SQLite) or PostgreSQL when `DATABASE_URL` is set.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache
from sqlalchemy import case, distinct, func, or_, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)


# ------------------------------
# Enums & Dataclasses
# ------------------------------

class MenuState(Enum):
    MAIN = "main"
    DEALER_SELECTION = "dealer_selection"
    COMPARISON_SELECTION = "comparison_selection"


@dataclass
class DealerContext:
    current_dealer: Optional[str] = None
    menu_state: MenuState = MenuState.MAIN
    selected_option: Optional[str] = None
    comparison_dealers: List[str] = field(default_factory=list)
    awaiting_dealer: bool = False


@dataclass
class QueryPlan:
    dealer: Optional[str] = None
    format: str = "standard"


@dataclass
class DealerAnswer:
    question: str
    plan: QueryPlan
    dashboard: Optional[Dict[str, Any]] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    explanation: str = ""


# ------------------------------
# Utility helpers
# ------------------------------

def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        s = str(value).strip()
        return s if s else default
    except Exception:
        return default


def _format_currency(amount: float) -> str:
    try:
        amount = float(amount or 0)
    except Exception:
        amount = 0.0
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    if amount >= 1_000:
        return f"PKR {amount/1_000:.2f}K"
    return f"PKR {amount:,.0f}"


def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ------------------------------
# Menu renderer (WhatsApp-friendly)
# ------------------------------

class DealerMenuRenderer:
    @staticmethod
    def render_main_menu() -> str:
        return "\n".join([
            "🏢 *DEALER ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Dealer Dashboard",
            "2. Dealer Revenue",
            "3. Dealer Units",
            "10. Compare Dealers",
            "11. Dealer Ranking",
            "18. Search Dealers",
            "99. Back",
        ])

    @staticmethod
    def render_dealer_dashboard(dealer_name: str, data: Dict[str, Any]) -> str:
        identity = data.get("identity", {})
        sales = data.get("sales", {})
        delivery = data.get("delivery", {})
        distance = data.get("distance", {})
        product = data.get("product", {})
        performance = data.get("performance", {})
        dates = data.get("dates", {})

        def _fmt_currency_whatsapp(amount: float) -> str:
            s = _format_currency(amount)
            # make suffix spaced: "32.45M" -> "32.45 M"
            s = re.sub(r"([KM])$", r" \1", s)
            return s

        lines: List[str] = []
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("Dealer")
        lines.append(_text(identity.get('customer_name', dealer_name)))
        lines.append("")
        lines.append("Dealer Code")
        lines.append(_text(identity.get('dealer_code', 'N/A')))
        lines.append("")
        lines.append("Customer Code")
        lines.append(_text(identity.get('customer_code', 'N/A')))
        lines.append("")
        lines.append("Warehouse")
        lines.append(_text(identity.get('warehouse', identity.get('primary_warehouse', 'N/A'))))
        lines.append("")
        lines.append("Dealer City")
        lines.append(_text(identity.get('city', 'N/A')))
        lines.append("")
        lines.append("Sales Office")
        lines.append(_text(identity.get('sales_office', 'N/A')))
        lines.append("")
        lines.append("Sales Manager")
        lines.append(_text(identity.get('sales_manager', 'N/A')))
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("📍 Logistics")
        lines.append("")
        lines.append("Road Distance")
        lines.append(f"{_text(distance.get('distance_km', 'N/A'))} KM")
        lines.append("")
        lines.append("Estimated Delivery")
        lines.append(_text(distance.get('estimated_delivery', 'N/A')))
        lines.append("")
        lines.append("Transportation Zone")
        lines.append(_text(distance.get('transportation_zone', 'N/A')))
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("📦 Delivery Performance")
        lines.append("")
        lines.append("Total DN")
        total_dn = delivery.get('total_dn', delivery.get('dn_count', 0))
        lines.append(f"{int(total_dn):,}")
        lines.append("")
        lines.append("Total Quantity")
        lines.append(f"{int(sales.get('total_quantity', sales.get('total_units', 0))):,} Units")
        lines.append("")
        lines.append("Total Sales")
        lines.append(_fmt_currency_whatsapp(sales.get('total_revenue', 0)))
        lines.append("")
        lines.append("Delivered")
        lines.append(f"{int(delivery.get('delivered_dn', delivery.get('pod_completed', 0))):,}")
        lines.append("")
        lines.append("Pending")
        lines.append(f"{int(delivery.get('pending_dn', 0)):,}")
        lines.append("")
        lines.append("PGI Pending")
        lines.append(f"{int(delivery.get('pgi_pending', 0)):,}")
        lines.append("")
        lines.append("POD Pending")
        lines.append(f"{int(delivery.get('pod_pending', 0)):,}")
        lines.append("")
        lines.append("Delivery Success")
        lines.append(f"{float(delivery.get('delivery_rate', 0)):.1f}%")
        lines.append("")
        lines.append("Average Delivery Days")
        lines.append(f"{float(delivery.get('avg_delivery_days', 0)):.1f} Days")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("🏆 Top Selling Models")
        lines.append("")
        top_models = product.get('top_models', []) or []
        if top_models:
            for i, m in enumerate(top_models[:10], 1):
                lines.append(f"{i}. {_text(m)}")
        else:
            lines.append("No model data available.")
        lines.append("")
        lines.append("📊 Business Performance")
        lines.append("")
        lines.append("Business Score")
        lines.append(f"{performance.get('business_score', 0)} / 100")
        lines.append("")
        lines.append("Dealer Rating")
        try:
            stars = int(performance.get('dealer_rating', 0))
        except Exception:
            stars = 0
        lines.append("⭐" * stars if stars > 0 else "N/A")
        lines.append("")
        lines.append("Performance")
        lines.append(_text(performance.get('performance_tier', performance.get('performance', 'N/A'))))
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("📅 Latest Activity")
        lines.append("")
        lines.append("Last DN")
        lines.append(_text(dates.get('last_dn', dates.get('last_delivery_dn', dates.get('last_sale', 'N/A')))))
        lines.append("")
        lines.append("Last Delivery")
        last_delivery = dates.get('last_delivery_date') or dates.get('last_sale') or dates.get('last_pod_date') or 'N/A'
        if isinstance(last_delivery, (date, datetime)):
            last_delivery = last_delivery.strftime("%d-%b-%Y")
        lines.append(_text(last_delivery))
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("💡 Executive Summary")
        lines.append("")
        exec_summary = data.get('executive_summary')
        if exec_summary:
            if isinstance(exec_summary, (list, tuple)):
                for s in exec_summary:
                    lines.append(f"• {_text(s)}")
            else:
                for s in str(exec_summary).split("\n"):
                    s = s.strip()
                    if s:
                        lines.append(f"• {s}")
        else:
            insights = data.get('insights', []) or []
            if insights:
                for s in insights[:6]:
                    lines.append(f"• {_text(s)}")
            else:
                lines.append("• Summary not available.")
        lines.append("")
        lines.append("Reply with:")
        lines.append("• Models")
        lines.append("• Pending")
        lines.append("• DN")
        lines.append("• Sales")
        lines.append("• Performance")
        lines.append("• 99 (Return to Main Menu)")

        return "\n".join(lines)


# ------------------------------
# Repository - database access
# ------------------------------

class DealerRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_dealer_by_name(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        if not dealer_identifier:
            return None
        try:
            dealer_identifier_lower = dealer_identifier.lower()
            q = (
                self.session.query(
                    DeliveryReport.customer_name,
                    DeliveryReport.dealer_code,
                    DeliveryReport.customer_code,
                    DeliveryReport.ship_to_city,
                    func.count(distinct(DeliveryReport.dn_no)).label("dn_count"),
                    func.sum(DeliveryReport.dn_qty).label("total_units"),
                    func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                )
                .filter(
                    or_(
                        func.lower(DeliveryReport.customer_name).ilike(f"%{dealer_identifier_lower}%"),
                        func.lower(DeliveryReport.dealer_code).ilike(f"%{dealer_identifier_lower}%"),
                    )
                )
                .group_by(
                    DeliveryReport.customer_name,
                    DeliveryReport.dealer_code,
                    DeliveryReport.customer_code,
                    DeliveryReport.ship_to_city,
                )
                .first()
            )

            if not q:
                return None

            return {
                "customer_name": _text(q.customer_name),
                "dealer_code": _text(q.dealer_code),
                "customer_code": _text(q.customer_code),
                "city": _text(q.ship_to_city),
                "dn_count": int(q.dn_count or 0),
                "total_units": int(q.total_units or 0),
                "total_revenue": float(q.total_revenue or 0.0),
            }
        except Exception as e:
            logger.exception("get_dealer_by_name failed")
            return None

    def search_dealers(self, query: str) -> List[Dict[str, Any]]:
        try:
            if not query:
                return []
            pattern = f"%{query}%"
            rows = (
                self.session.query(
                    DeliveryReport.customer_name.label("dealer"),
                    DeliveryReport.dealer_code,
                    DeliveryReport.customer_code,
                    DeliveryReport.ship_to_city,
                )
                .filter(
                    or_(
                        DeliveryReport.customer_name.ilike(pattern),
                        DeliveryReport.dealer_code.ilike(pattern),
                    )
                )
                .distinct()
                .limit(20)
                .all()
            )
            out = []
            for r in rows:
                out.append({
                    "dealer": _text(r.dealer),
                    "dealer_code": _text(r.dealer_code),
                    "customer_code": _text(r.customer_code),
                    "city": _text(r.ship_to_city),
                })
            return out
        except Exception:
            logger.exception("search_dealers failed")
            return []

    def get_top_dealers_by_revenue(self, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            rows = (
                self.session.query(
                    DeliveryReport.customer_name.label("dealer"),
                    func.sum(DeliveryReport.dn_amount).label("revenue"),
                )
                .filter(DeliveryReport.customer_name.isnot(None))
                .group_by(DeliveryReport.customer_name)
                .order_by(func.sum(DeliveryReport.dn_amount).desc())
                .limit(limit)
                .all()
            )
            return [{"dealer": _text(r.dealer), "value": _format_currency(r.revenue or 0)} for r in rows]
        except Exception:
            logger.exception("get_top_dealers_by_revenue failed")
            return []


# ------------------------------
# Dashboard builder
# ------------------------------

class DealerDashboardBuilder:
    def __init__(self, session: Session):
        self.session = session
        self.repository = DealerRepository(session)

    def build(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        dealer = self.repository.get_dealer_by_name(dealer_identifier)
        if not dealer:
            return None
        dashboard = {
            "identity": {
                "customer_name": dealer.get("customer_name", ""),
                "dealer_code": dealer.get("dealer_code", ""),
                "customer_code": dealer.get("customer_code", ""),
                "city": dealer.get("city", ""),
                "warehouse": dealer.get("warehouse", ""),
                "warehouse_code": dealer.get("warehouse_code", ""),
                "delivery_location": dealer.get("delivery_location", ""),
                "sales_office": dealer.get("sales_office", ""),
                "sales_manager": dealer.get("sales_manager", ""),
                "division": dealer.get("division", ""),
            },
            "sales": {
                "total_revenue": dealer.get("total_revenue", 0),
                "total_quantity": dealer.get("total_units", 0),
            },
            "delivery": {
                "total_dn": dealer.get("dn_count", 0),
                "pending_dn": dealer.get("pending_dn", 0),
                "pgi_pending": dealer.get("pgi_pending_dn", 0),
                "pod_pending": dealer.get("pod_pending_dn", 0),
                "delivered_dn": dealer.get("pod_completed", 0),
                "pgi_completed": dealer.get("pgi_completed", 0),
                "pod_completed": dealer.get("pod_completed", 0),
                "delivery_rate": dealer.get("delivery_success_pct", 0),
                "pgi_rate": dealer.get("pgi_rate", 0),
                "pod_rate": dealer.get("pod_rate", 0),
                "avg_delivery_days": dealer.get("avg_delivery_days", 0),
                "avg_pod_days": dealer.get("avg_pod_days", 0),
            },
            "product": {
                "total_models": dealer.get("total_models", 0),
                "top_models": dealer.get("top_models", []),
            },
            "warehouse": {
                "primary_warehouse": dealer.get("warehouse", ""),
                "warehouses_used": dealer.get("warehouse_count", 0),
            },
            "city": {
                "cities_served": dealer.get("city_count", 0),
                "top_destination_cities": dealer.get("top_destination_cities", []),
            },
            "performance": {
                "business_score": dealer.get("business_score", 0),
                "risk_score": dealer.get("risk_score", 0),
                "performance_tier": dealer.get("performance_tier", "Standard"),
                "dealer_rating": dealer.get("dealer_rating", 0),
                "dealer_rank": dealer.get("dealer_rank", 0),
            },
            "distance": dealer.get("distance", {}),
            "dates": {
                "last_delivery_date": dealer.get("last_sale", 'N/A'),
                "last_pgi_date": dealer.get("last_pgi_date", 'N/A'),
                "last_pod_date": dealer.get("last_pod_date", 'N/A'),
                "last_dn": dealer.get("last_dn", dealer.get("last_delivery_dn", 'N/A')),
            },
            "insights": dealer.get("insights", []),
            "recommendations": dealer.get("recommendations", []),
            "executive_summary": dealer.get("executive_summary", ""),
        }
        return dashboard


# ------------------------------
# Main service
# ------------------------------

class DealerAnalyticsService:
    def __init__(self) -> None:
        self._menu_renderer = DealerMenuRenderer()
        self._contexts: Dict[str, DealerContext] = {}
        self._context_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=2)

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

    def get_main_menu(self) -> str:
        return self._menu_renderer.render_main_menu()

    def _get_context(self, session_id: str) -> DealerContext:
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DealerContext()
            return self._contexts[session_id]

    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        context = self._get_context(session_id)
        user_input = (user_input or "").strip()

        if user_input in ("0", "99"):
            context.menu_state = MenuState.MAIN
            context.awaiting_dealer = False
            return {"response": self.get_main_menu(), "menu_type": "dealer_menu", "action": "main_menu", "data": {}, "exit_menu": True}

        if context.menu_state == MenuState.MAIN:
            if user_input == "1":
                context.menu_state = MenuState.DEALER_SELECTION
                context.selected_option = "dashboard"
                context.awaiting_dealer = True
                return {"response": "Enter dealer name:", "menu_type": "dealer_menu", "action": "dealer_selection", "data": {}, "exit_menu": False}
            if user_input == "11":
                with self._session() as session:
                    repo = DealerRepository(session)
                    ranking = repo.get_top_dealers_by_revenue(10)
                    # render_ranking may not exist in simplified renderer, fallback to string
                    rendered = getattr(self._menu_renderer, 'render_ranking', lambda r, m, l: str(r))(ranking, "Revenue", 10)
                    return {"response": rendered, "menu_type": "dealer_menu", "action": "ranking", "data": {"ranking": ranking}, "exit_menu": False}
            # Quick fallback: treat as dealer name
            return self._handle_quick_query(context, user_input)

        if context.menu_state == MenuState.DEALER_SELECTION and context.awaiting_dealer:
            return self._handle_dealer_selection(context, user_input)

        return self._handle_quick_query(context, user_input)

    def _handle_dealer_selection(self, context: DealerContext, dealer_input: str) -> Dict[str, Any]:
        # resolve via DB
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                dealers = repo.search_dealers(dealer_input)
                if not dealers:
                    return {"response": "❌ Dealer not found. Try again.\n0. Main Menu", "menu_type": "dealer_menu", "action": "not_found", "data": {}, "exit_menu": False}
                dealer_name = dealers[0].get("dealer")
                context.current_dealer = dealer_name
                context.menu_state = MenuState.MAIN
                context.awaiting_dealer = False
                # perform previously selected action
                if context.selected_option == "dashboard":
                    return self._get_dealer_dashboard(context, dealer_name)
                return {"response": self.get_main_menu(), "menu_type": "dealer_menu", "action": "main_menu", "data": {}, "exit_menu": True}
        except Exception:
            logger.exception("_handle_dealer_selection error")
            return {"response": "⚠️ Service error.", "menu_type": "dealer_menu", "action": "error", "data": {}, "exit_menu": False}

    def _get_dealer_dashboard(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                if not dashboard:
                    return {"response": f"⚠️ Dealer '{dealer_name}' not found.\n0. Main Menu", "menu_type": "dealer_menu", "action": "dashboard", "data": {"dealer": dealer_name}, "exit_menu": False}
                return {"response": self._menu_renderer.render_dealer_dashboard(dealer_name, dashboard), "menu_type": "dealer_menu", "action": "dashboard", "data": {"dealer": dealer_name, "dashboard": dashboard}, "exit_menu": False}
        except Exception as e:
            logger.exception("_get_dealer_dashboard error")
            return {"response": f"⚠️ Service error: {str(e)[:120]}", "menu_type": "dealer_menu", "action": "error", "data": {}, "exit_menu": False}

    def _handle_quick_query(self, context: DealerContext, query: str) -> Dict[str, Any]:
        # If user typed a dealer name, try to resolve and show dashboard
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                dealers = repo.search_dealers(query)
                if dealers:
                    return self._get_dealer_dashboard(context, dealers[0].get("dealer"))
        except Exception:
            pass
        return {"response": "❌ I didn't understand that. Try a dealer name or '1' for dashboard.", "menu_type": "dealer_menu", "action": "unknown", "data": {}, "exit_menu": False}


# ------------------------------
# Service singleton & exports
# ------------------------------

_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()

def get_dealer_analytics_service() -> DealerAnalyticsService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DealerAnalyticsService()
    return _service


def process_dealer_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    service = get_dealer_analytics_service()
    return service.process_menu_input(session_id, user_input)


def get_dealer_main_menu() -> str:
    service = get_dealer_analytics_service()
    return service.get_main_menu()


__all__ = [
    "DealerAnalyticsService",
    "get_dealer_analytics_service",
    "process_dealer_menu",
    "get_dealer_main_menu",
    "get_dealer_service",
]


def get_dealer_service() -> DealerAnalyticsService:
    """Compatibility wrapper expected by AI Provider: returns service instance."""
    return get_dealer_analytics_service()
