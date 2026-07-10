#!/usr/bin/env python3
# ============================================================
# FILE: app/services/city_service.py
# VERSION: 6.1 - ENTRY PROMPT & ENHANCED DASHBOARD WITH TOP DEALERS/PRODUCTS/EXCEPTIONS
# ============================================================

"""
City Analytics Service – Full-featured city intelligence with enhanced dashboard.
- Entry prompt when selected from main menu.
- Rich dashboard with: Business Overview, Delivery Performance, KPI, Top 5 Dealers, Top 5 Products, Exceptions, AI Insight.
- PostgreSQL as the only source of truth.
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
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple, Union, Set

from cachetools import TTLCache
from sqlalchemy import and_, case, distinct, func, or_, text, desc, asc
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

SERVICE_ID = "6"                     # This service's number in the main menu
VERSION = "6.1"
CACHE_TTL = max(60, int(os.getenv("CITY_ANALYTICS_CACHE_TTL", "300")))
DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))

# ============================================================
# CONSTANTS
# ============================================================

CITY_NAMES: List[str] = [
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta",
    "multan", "peshawar", "gilgit", "hyderabad", "islamabad",
    "sialkot", "gujranwala", "faisalabad", "bahawalpur", "sukkur",
    "dg khan", "rahim yar khan", "gwadar"
]

CITY_ALIASES: Dict[str, str] = {
    "rwp": "rawalpindi",
    "isb": "islamabad",
    "lhr": "lahore",
    "khi": "karachi",
    "fsd": "faisalabad",
    "hyd": "hyderabad",
    "ryk": "rahim yar khan",
}

CITY_EMOJIS: Dict[str, str] = {
    "lahore": "🏛️", "karachi": "🌊", "rawalpindi": "🏔️", "islamabad": "🏛️",
    "multan": "🌅", "peshawar": "🏔️", "quetta": "🏜️", "faisalabad": "🏭",
    "hyderabad": "🌊", "sialkot": "⚽", "gujranwala": "🏭", "bahawalpur": "🌴",
    "sukkur": "🌊", "dg khan": "🏔️", "rahim yar khan": "🌾", "abbottabad": "🏔️",
    "gwadar": "🌊", "gilgit": "🏔️"
}

WAREHOUSE_EMOJIS: Dict[str, str] = {
    "lahore": "🏭", "karachi": "⚓", "rawalpindi": "🏔️", "gujranwala": "🏭",
    "multan": "🌅", "peshawar": "🏔️", "quetta": "🏜️", "faisalabad": "🏭",
    "hyderabad": "🌊", "sialkot": "⚽", "islamabad": "🏛️"
}

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        return str(value).strip() or default
    except (TypeError, ValueError):
        return default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 1) if bottom else 0.0

def _days(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "days"):
        return round(float(value.days), 1)
    return round(_number(value), 1)

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def _growth(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 1)

def format_currency(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"PKR {amount/1_000_000_000:.2f} Billion"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f} Million"
    else:
        return f"PKR {amount:,.2f}"

def get_city_emoji(city_name: str) -> str:
    return CITY_EMOJIS.get(city_name.lower(), "📍")

def get_warehouse_emoji(warehouse_name: str) -> str:
    return WAREHOUSE_EMOJIS.get(warehouse_name.lower(), "🏭")

# ============================================================
# CITY CONTEXT
# ============================================================

class MenuState(Enum):
    MAIN = "main"
    CITY_SELECTION = "city_selection"
    DASHBOARD = "dashboard"
    EXECUTING = "executing"

@dataclass
class CityContext:
    session_id: str
    current_city: Optional[str] = None
    menu_state: MenuState = MenuState.MAIN
    awaiting_city: bool = False
    selected_action: Optional[str] = None
    last_response: str = ""

# ============================================================
# CITY DASHBOARD BUILDER
# ============================================================

class CityDashboardBuilder:
    """Builds city dashboards with all sections: overview, delivery, KPI, top lists, exceptions, insights."""

    def __init__(self, session: Session):
        self.session = session

    def build_full_dashboard(self, city_name: str) -> Optional[Dict[str, Any]]:
        """Build comprehensive dashboard for a city."""
        city_lower = city_name.lower()
        cache_key = f"dashboard_{city_lower}"
        # (cache omitted for brevity – can be added later)

        try:
            # --- Main aggregates ---
            main = self.session.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("delivered_dn"),
                func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_dn"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("dealers"),
                func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no)))).label("pgi_dn"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("pod_dn"),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
                func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
                func.max(DeliveryReport.warehouse).label("primary_warehouse"),
                func.max(DeliveryReport.division).label("division"),
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city_lower
            ).first()

            if not main or not main.total_dn:
                return None

            total_dn = int(main.total_dn or 0)
            delivered_dn = int(main.delivered_dn or 0)
            pending_dn = int(main.pending_dn or 0)
            pgi_dn = int(main.pgi_dn or 0)
            pod_dn = int(main.pod_dn or 0)

            # --- Distance (using approximate coordinates) ---
            warehouse = _text(main.primary_warehouse)
            distance_km = self._calculate_distance(warehouse, city_name)

            # --- Top 5 Dealers ---
            top_dealers = self._get_top_dealers(city_lower, limit=5)

            # --- Top 5 Products (by revenue) ---
            top_products = self._get_top_products(city_lower, limit=5)

            # --- Exceptions ---
            exceptions = self._get_exceptions(city_lower)

            # --- KPI metrics ---
            delivery_achievement = _percent(delivered_dn, total_dn)
            pgi_achievement = _percent(pgi_dn, total_dn)
            pod_achievement = _percent(pod_dn, total_dn)
            avg_delivery = _days(main.avg_delivery)
            avg_pod = _days(main.avg_pod)
            avg_cycle = _days(main.avg_cycle)

            # --- Build dashboard dict ---
            dashboard = {
                "city_name": city_name.title(),
                "primary_warehouse": warehouse,
                "division": _text(main.division, "All Divisions"),
                "total_dealers": int(main.dealers or 0),
                "total_dn": total_dn,
                "total_units": int(main.total_units or 0),
                "total_revenue": float(main.total_revenue or 0.0),
                "delivered_dn": delivered_dn,
                "pending_dn": pending_dn,
                "avg_delivery_days": avg_delivery,
                "target_delivery_days": 2,  # can be made configurable
                "avg_distance_km": distance_km,
                "delivery_achievement": delivery_achievement,
                "pgi_achievement": pgi_achievement,
                "pod_achievement": pod_achievement,
                "avg_pod_days": avg_pod,
                "avg_cycle_days": avg_cycle,
                "top_dealers": top_dealers,
                "top_products": top_products,
                "exceptions": exceptions,
                "ai_insight": self._generate_ai_insight(delivery_achievement, pending_dn, exceptions),
            }

            return dashboard

        except Exception as e:
            logger.error(f"Error building dashboard for {city_name}: {e}")
            return None

    def _calculate_distance(self, warehouse: str, city: str) -> float:
        """Approximate distance using Haversine with hardcoded coords."""
        coords = {
            "lahore": (31.5204, 74.3587),
            "karachi": (24.8607, 67.0011),
            "rawalpindi": (33.5651, 73.0169),
            "multan": (30.1575, 71.5249),
            "peshawar": (34.0151, 71.5249),
            "quetta": (30.1798, 66.9750),
            "hyderabad": (25.3960, 68.3578),
            "faisalabad": (31.4504, 73.1350),
            "sialkot": (32.4945, 74.5229),
            "gujranwala": (32.1617, 74.1883),
            "islamabad": (33.6844, 73.0479),
            "abbottabad": (34.1490, 73.2210),
            "dg khan": (30.0430, 70.6402),
            "sukkur": (27.7060, 68.8530),
            "rahim yar khan": (28.4200, 70.3030),
            "gwadar": (25.1260, 62.3250),
            "gilgit": (35.9208, 74.3144),
        }
        wc = coords.get(warehouse.lower())
        cc = coords.get(city.lower())
        if not wc or not cc:
            return 0.0
        lat1, lon1 = wc
        lat2, lon2 = cc
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c

    def _get_top_dealers(self, city_lower: str, limit: int = 5) -> List[str]:
        try:
            results = self.session.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city_lower,
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            return [_text(r.customer_name) for r in results if r.customer_name]
        except Exception:
            return []

    def _get_top_products(self, city_lower: str, limit: int = 5) -> List[str]:
        try:
            results = self.session.query(
                DeliveryReport.customer_model,
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city_lower,
                DeliveryReport.customer_model.isnot(None)
            ).group_by(
                DeliveryReport.customer_model
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            return [_text(r.customer_model) for r in results if r.customer_model]
        except Exception:
            return []

    def _get_exceptions(self, city_lower: str) -> Dict[str, int]:
        """Return counts for pending >3 days, pending POD, delayed deliveries."""
        try:
            # Pending DNs >3 days (since creation)
            pending_over_3 = self.session.query(
                func.count(DeliveryReport.dn_no)
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city_lower,
                or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                DeliveryReport.dn_create_date <= date.today() - timedelta(days=3)
            ).scalar() or 0

            # Pending PODs (PGI done, no POD)
            pending_pod = self.session.query(
                func.count(DeliveryReport.dn_no)
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city_lower,
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.pod_date.is_(None)
            ).scalar() or 0

            # Delayed deliveries (delivery > target 2 days)
            delayed = self.session.query(
                func.count(DeliveryReport.dn_no)
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city_lower,
                DeliveryReport.pod_date.isnot(None),
                (DeliveryReport.pod_date - DeliveryReport.dn_create_date) > 2
            ).scalar() or 0

            return {
                "pending_over_3": int(pending_over_3),
                "pending_pod": int(pending_pod),
                "delayed_deliveries": int(delayed),
            }
        except Exception:
            return {"pending_over_3": 0, "pending_pod": 0, "delayed_deliveries": 0}

    def _generate_ai_insight(self, delivery_achievement: float, pending_dn: int, exceptions: Dict[str, int]) -> str:
        """Generate a simple AI insight based on data."""
        lines = []
        if delivery_achievement >= 90:
            lines.append(f"✅ {delivery_achievement:.1f}% delivery achievement – performing above target.")
        elif delivery_achievement >= 70:
            lines.append(f"📊 {delivery_achievement:.1f}% delivery achievement – on track.")
        else:
            lines.append(f"⚠️ {delivery_achievement:.1f}% delivery achievement – requires improvement.")

        if pending_dn == 0:
            lines.append("✅ No pending DNs – excellent.")
        else:
            lines.append(f"⏳ {pending_dn} pending DNs – prioritize clearance.")

        if exceptions.get("pending_over_3", 0) > 0:
            lines.append(f"🚨 {exceptions['pending_over_3']} DNs pending >3 days – urgent attention.")
        if exceptions.get("pending_pod", 0) > 0:
            lines.append(f"📄 {exceptions['pending_pod']} pending PODs – follow up.")
        if exceptions.get("delayed_deliveries", 0) > 0:
            lines.append(f"🚚 {exceptions['delayed_deliveries']} delayed deliveries – review logistics.")

        if not lines:
            lines.append("✅ No operational issues detected.")
        return "\n".join(lines)

# ============================================================
# CITY ANALYTICS SERVICE
# ============================================================

class CityAnalyticsService:
    def __init__(self) -> None:
        self._service_name = "city_analytics"
        self._version = VERSION
        self._contexts: Dict[str, CityContext] = {}
        self._lock = threading.RLock()
        logger.info(f"✅ CityAnalyticsService v{self._version} initialized")

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

    def _get_context(self, session_id: str) -> CityContext:
        with self._lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = CityContext(session_id=session_id)
            return self._contexts[session_id]

    def _clear_context(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._contexts:
                del self._contexts[session_id]

    # ------------------------------------------------------------
    # ENTRY PROMPT
    # ------------------------------------------------------------
    def _get_entry_prompt(self) -> str:
        """Displayed when user selects City Analytics from main menu."""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "        🏙️ CITY INTELLIGENCE CENTER",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Please enter the City Name.",
            "",
            "Examples:",
            "Lahore",
            "Karachi",
            "Islamabad",
            "Faisalabad",
            "Multan",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📋 Commands",
            "",
            "🔎 Enter any City Name.",
            "",
            "🏠 Reply *99* to return to the Previous Menu.",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🤖 Awaiting City Name...",
        ])

    # ------------------------------------------------------------
    # DASHBOARD RENDERER (EXACT FORMAT)
    # ------------------------------------------------------------
    def _render_full_dashboard(self, city_name: str, dashboard: Dict[str, Any]) -> str:
        """Render the city dashboard in the exact requested format."""
        emoji = get_city_emoji(city_name)
        warehouse = dashboard.get("primary_warehouse", "Unknown")
        division = dashboard.get("division", "All Divisions")

        dealers = dashboard.get("total_dealers", 0)
        total_dn = dashboard.get("total_dn", 0)
        total_units = dashboard.get("total_units", 0)
        revenue = dashboard.get("total_revenue", 0.0)

        delivered = dashboard.get("delivered_dn", 0)
        pending = dashboard.get("pending_dn", 0)
        avg_delivery = dashboard.get("avg_delivery_days", 0.0)
        target_delivery = dashboard.get("target_delivery_days", 2)
        avg_distance = dashboard.get("avg_distance_km", 0.0)

        delivery_pct = dashboard.get("delivery_achievement", 0.0)
        pgi_pct = dashboard.get("pgi_achievement", 0.0)
        pod_pct = dashboard.get("pod_achievement", 0.0)
        avg_pod = dashboard.get("avg_pod_days", 0.0)
        avg_cycle = dashboard.get("avg_cycle_days", 0.0)

        top_dealers = dashboard.get("top_dealers", [])
        top_products = dashboard.get("top_products", [])
        exceptions = dashboard.get("exceptions", {})
        ai_insight = dashboard.get("ai_insight", "No insight available.")

        lines = []

        # --- Header ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏙️ CITY INTELLIGENCE CENTER")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"📍 City")
        lines.append(f"{city_name.title()}")
        lines.append("")
        lines.append(f"🏭 Primary Warehouse")
        lines.append(f"{warehouse}")
        lines.append("")
        lines.append(f"📦 Division")
        lines.append(f"{division}")
        lines.append("")

        # --- BUSINESS OVERVIEW ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📊 BUSINESS OVERVIEW")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"👥 Active Dealers     : {dealers:,}")
        lines.append(f"📦 Total DNs          : {total_dn:,}")
        lines.append(f"📦 Units Delivered    : {total_units:,}")
        lines.append(f"💰 Total Revenue      : {format_currency(revenue)}")
        lines.append("")

        # --- DELIVERY PERFORMANCE ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🚚 DELIVERY PERFORMANCE")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"🟢 Delivered DNs      : {delivered:,}")
        lines.append(f"🟡 Pending DNs        : {pending:,}")
        lines.append(f"📅 Avg Delivery Time  : {avg_delivery:.1f} Days")
        lines.append(f"🎯 Target Delivery    : {target_delivery} Days")
        lines.append(f"📏 Avg Distance       : {avg_distance:.0f} KM")
        lines.append("")

        # --- KPI PERFORMANCE ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📈 KPI PERFORMANCE")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"✅ Delivery Achievement : {delivery_pct:.1f}%")
        lines.append(f"✅ PGI Achievement      : {pgi_pct:.1f}%")
        lines.append(f"✅ POD Achievement      : {pod_pct:.1f}%")
        lines.append(f"⏱ Avg POD Days         : {avg_pod:.1f} Days")
        lines.append(f"🔄 Total Cycle Time     : {avg_cycle:.1f} Days")
        lines.append("")

        # --- TOP 5 DEALERS ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏆 TOP 5 DEALERS")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        if top_dealers:
            for i, dealer in enumerate(top_dealers, 1):
                lines.append(f"{i}. {dealer}")
        else:
            lines.append("No dealer data available.")
        lines.append("")

        # --- TOP 5 PRODUCTS ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📦 TOP 5 PRODUCTS")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        if top_products:
            for product in top_products:
                lines.append(f"• {product}")
        else:
            lines.append("No product data available.")
        lines.append("")

        # --- EXCEPTIONS ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ EXCEPTIONS")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"🚨 Pending DNs (>3 Days) : {exceptions.get('pending_over_3', 0)}")
        lines.append(f"🚨 Pending PODs          : {exceptions.get('pending_pod', 0)}")
        lines.append(f"🚨 Delayed Deliveries    : {exceptions.get('delayed_deliveries', 0)}")
        lines.append("")

        # --- AI INSIGHT ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🤖 AI BUSINESS INSIGHT")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.extend(ai_insight.split("\n"))
        lines.append("")

        # --- NEXT ACTION ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔄 NEXT ACTION")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("🏙️ Enter another City Name.")
        lines.append("")
        lines.append("🏠 Reply *99* to return to the Main Menu.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        return "\n".join(lines)

    # ------------------------------------------------------------
    # MAIN ENTRY POINT (CALLED BY GATEWAY)
    # ------------------------------------------------------------
    def handle_message(self, message: str, sender: str) -> str:
        """
        Main entry point for WhatsApp messages.
        - If message == SERVICE_ID -> show entry prompt.
        - If message == "99" -> return "99" to unlock.
        - If message is a city name -> build and show dashboard.
        - Otherwise -> show entry prompt.
        """
        msg = message.strip()
        context = self._get_context(sender)

        # If user sends "99" from inside the service, return "99" to unlock
        if msg == "99":
            self._clear_context(sender)
            return "99"

        # If user selects this service from main menu (msg == "6")
        if msg == SERVICE_ID:
            context.menu_state = MenuState.CITY_SELECTION
            context.awaiting_city = True
            return self._get_entry_prompt()

        # If we are awaiting a city name
        if context.awaiting_city:
            city_resolved = self._resolve_city_name(msg)
            if city_resolved:
                context.current_city = city_resolved
                context.awaiting_city = False
                context.menu_state = MenuState.DASHBOARD
                # Build dashboard
                with self._session() as session:
                    builder = CityDashboardBuilder(session)
                    dashboard = builder.build_full_dashboard(city_resolved)
                    if dashboard:
                        return self._render_full_dashboard(city_resolved, dashboard)
                    else:
                        context.awaiting_city = True
                        return "\n".join([
                            f"⚠️ City '{msg}' not found.",
                            "",
                            "Please try again or enter a valid city name.",
                            "",
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                            "🤖 Awaiting City Name...",
                        ])
            else:
                return "\n".join([
                    f"⚠️ Could not resolve city name '{msg}'.",
                    "",
                    "Please try again with a valid city name.",
                    "",
                    "Examples: Lahore, Karachi, Islamabad",
                    "",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "🤖 Awaiting City Name...",
                ])

        # If user sends a city name while not awaiting, treat as quick query
        city_resolved = self._resolve_city_name(msg)
        if city_resolved:
            context.current_city = city_resolved
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build_full_dashboard(city_resolved)
                if dashboard:
                    return self._render_full_dashboard(city_resolved, dashboard)
                else:
                    return f"⚠️ City '{msg}' not found.\n\nPlease try again."

        # If user sends "menu" or "help"
        if msg.lower() in ["menu", "help", "options"]:
            return self._get_entry_prompt()

        # Otherwise, show entry prompt with a hint
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 Please enter a city name (e.g., Lahore) or type 99 to exit.",
            "",
            self._get_entry_prompt()
        ])

    # ------------------------------------------------------------
    # CITY NAME RESOLUTION
    # ------------------------------------------------------------
    def _resolve_city_name(self, input_text: str) -> Optional[str]:
        """Resolve city name from input (case-insensitive, aliases, partial)."""
        input_lower = input_text.lower().strip()
        if input_lower in CITY_NAMES:
            return input_lower
        if input_lower in CITY_ALIASES:
            return CITY_ALIASES[input_lower]
        # partial match
        for city in CITY_NAMES:
            if city in input_lower or input_lower in city:
                return city
        return None

    # ------------------------------------------------------------
    # LEGACY / COMPATIBILITY METHODS
    # ------------------------------------------------------------
    def get_main_menu(self) -> str:
        """Return the main menu for this service (entry prompt)."""
        return self._get_entry_prompt()

    def process_whatsapp_query(self, message: str, sender: str) -> str:
        """Alias for handle_message to match gateway expectations."""
        return self.handle_message(message, sender)

    def health_check(self) -> Dict[str, Any]:
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "source": "PostgreSQL",
        }

# ============================================================
# SINGLETON & EXPORTS
# ============================================================

_service_instance: Optional[CityAnalyticsService] = None

def get_city_analytics_service() -> CityAnalyticsService:
    global _service_instance
    if _service_instance is None:
        _service_instance = CityAnalyticsService()
    return _service_instance

# Alias for gateway
get_city_service = get_city_analytics_service

__all__ = [
    "CityAnalyticsService",
    "get_city_analytics_service",
    "get_city_service",
]
