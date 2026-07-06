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
        lines = [f"🏢 *Dealer Dashboard - {dealer_name}*", ""]
        lines.append(f"Dealer Code: {identity.get('dealer_code', 'N/A')}")
        lines.append(f"City: {identity.get('city', 'N/A')}")
        lines.append("")
        lines.append(f"Revenue: {_format_currency(sales.get('total_revenue', 0))}")
        lines.append(f"Units: {sales.get('total_quantity', 0):,}")
        lines.append("")
        lines.append(f"Pending DN: {delivery.get('pending_dn', 0):,}")
        lines.append("")
        lines.append("0. Main Menu")
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
                "city": dealer.get("city", ""),
            },
            "sales": {
                "total_revenue": dealer.get("total_revenue", 0),
                "total_quantity": dealer.get("total_units", 0),
            },
            "delivery": {
                "pending_dn": dealer.get("dn_count", 0),
            },
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
                    rendered = getattr(self._menu_renderer, 'render_ranking', lambda r, m, l: str(r))(ranking, "Revenue", 10)
                    return {"response": rendered, "menu_type": "dealer_menu", "action": "ranking", "data": {"ranking": ranking}, "exit_menu": False}
            return self._handle_quick_query(context, user_input)

        if context.menu_state == MenuState.DEALER_SELECTION and context.awaiting_dealer:
            return self._handle_dealer_selection(context, user_input)

        return self._handle_quick_query(context, user_input)

    def _handle_dealer_selection(self, context: DealerContext, dealer_input: str) -> Dict[str, Any]:
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
]
