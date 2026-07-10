#!/usr/bin/env python3
# ============================================================
# FILE: app/services/product_service.py
# VERSION: 9.0 - PRODUCTION READY
# PURPOSE: Search product models or divisions using LOWER exact match.
#          Returns formatted WhatsApp dashboards.
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Optional, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)

VERSION = "9.0"

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

def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def _format_currency(amount: float) -> str:
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: int) -> str:
    return f"{num:,}"

# ============================================================
# REPOSITORY
# ============================================================

class ProductRepository:
    def __init__(self, session: Session):
        self.session = session

    def count_model(self, search: str) -> int:
        """Check if any row matches the model (case-insensitive)."""
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT COUNT(*) FROM delivery_reports
                        WHERE LOWER(customer_model) = LOWER(:search)
                    """),
                    {"search": search}
                ).scalar()
                return result or 0
        except Exception as e:
            logger.error(f"count_model error: {e}")
            return 0

    def count_division(self, search: str) -> int:
        """Check if any row matches the division (case-insensitive)."""
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT COUNT(*) FROM delivery_reports
                        WHERE LOWER(division) = LOWER(:search)
                    """),
                    {"search": search}
                ).scalar()
                return result or 0
        except Exception as e:
            logger.error(f"count_division error: {e}")
            return 0

    def get_model_data(self, model: str) -> Optional[Dict[str, Any]]:
        """Fetch aggregated data for a specific model."""
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT
                            customer_model,
                            division,
                            COUNT(DISTINCT dn_no) AS total_dns,
                            SUM(dn_qty) AS total_units,
                            SUM(dn_amount) AS total_revenue,
                            COUNT(DISTINCT customer_name) AS total_dealers,
                            COUNT(DISTINCT warehouse) AS warehouses,
                            COUNT(DISTINCT ship_to_city) AS cities,
                            COUNT(DISTINCT CASE WHEN delivery_status = 'Delivered' THEN dn_no END) AS delivered_dns,
                            COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END) AS pending_dns,
                            ROUND(AVG(pod_date - good_issue_date), 2) AS avg_delivery_days
                        FROM delivery_reports
                        WHERE LOWER(customer_model) = LOWER(:model)
                        GROUP BY customer_model, division
                    """),
                    {"model": model}
                ).first()
                if not row:
                    return None

                data = {
                    'model': _text(row[0]),
                    'division': _text(row[1]),
                    'total_dns': _int(row[2]),
                    'total_units': _int(row[3]),
                    'total_revenue': _number(row[4]),
                    'total_dealers': _int(row[5]),
                    'warehouses': _int(row[6]),
                    'cities': _int(row[7]),
                    'delivered_dns': _int(row[8]),
                    'pending_dns': _int(row[9]),
                    'avg_delivery_days': _number(row[10]),
                }

                # Top cities
                cities = conn.execute(
                    text("""
                        SELECT ship_to_city, SUM(dn_qty) AS units
                        FROM delivery_reports
                        WHERE LOWER(customer_model) = LOWER(:model)
                        GROUP BY ship_to_city
                        ORDER BY units DESC
                        LIMIT 5
                    """),
                    {"model": model}
                ).fetchall()
                data['top_cities'] = [c[0] for c in cities if c[0]]

                # Top warehouses
                warehouses = conn.execute(
                    text("""
                        SELECT warehouse, SUM(dn_qty) AS units
                        FROM delivery_reports
                        WHERE LOWER(customer_model) = LOWER(:model)
                        GROUP BY warehouse
                        ORDER BY units DESC
                        LIMIT 5
                    """),
                    {"model": model}
                ).fetchall()
                data['top_warehouses'] = [w[0] for w in warehouses if w[0]]

                return data
        except Exception as e:
            logger.error(f"get_model_data error: {e}")
            return None

    def get_division_data(self, division: str) -> Optional[Dict[str, Any]]:
        """Fetch aggregated data for a specific division."""
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT
                            division,
                            COUNT(DISTINCT dn_no) AS total_dns,
                            SUM(dn_qty) AS total_units,
                            SUM(dn_amount) AS total_revenue,
                            COUNT(DISTINCT customer_name) AS total_dealers,
                            COUNT(DISTINCT warehouse) AS warehouses,
                            COUNT(DISTINCT ship_to_city) AS cities,
                            COUNT(DISTINCT CASE WHEN delivery_status = 'Delivered' THEN dn_no END) AS delivered_dns,
                            COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END) AS pending_dns,
                            ROUND(AVG(pod_date - good_issue_date), 2) AS avg_delivery_days
                        FROM delivery_reports
                        WHERE LOWER(division) = LOWER(:division)
                        GROUP BY division
                    """),
                    {"division": division}
                ).first()
                if not row:
                    return None

                data = {
                    'division': _text(row[0]),
                    'total_dns': _int(row[1]),
                    'total_units': _int(row[2]),
                    'total_revenue': _number(row[3]),
                    'total_dealers': _int(row[4]),
                    'warehouses': _int(row[5]),
                    'cities': _int(row[6]),
                    'delivered_dns': _int(row[7]),
                    'pending_dns': _int(row[8]),
                    'avg_delivery_days': _number(row[9]),
                }

                # Top models in this division
                models = conn.execute(
                    text("""
                        SELECT customer_model, SUM(dn_qty) AS units
                        FROM delivery_reports
                        WHERE LOWER(division) = LOWER(:division)
                        GROUP BY customer_model
                        ORDER BY units DESC
                        LIMIT 5
                    """),
                    {"division": division}
                ).fetchall()
                data['top_models'] = [m[0] for m in models if m[0]]

                return data
        except Exception as e:
            logger.error(f"get_division_data error: {e}")
            return None

# ============================================================
# FORMATTERS
# ============================================================

class ProductDashboardFormatter:
    @staticmethod
    def welcome() -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "      📦 PRODUCT INTELLIGENCE CENTER",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Search by:",
            "",
            "✅ Product Model",
            "✅ Product Division",
            "",
            "Examples:",
            "",
            "📦 Product Model",
            "HWM120-AS MG",
            "",
            "📂 Product Division",
            "Washing Machine",
            "Refrigerator",
            "Deep Freezer",
            "LED TV",
            "Air Conditioner",
            "Kitchen Appliances",
            "Small Domestic Appliances",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📋 Commands",
            "",
            "🔎 Enter a Product Model or Product Division.",
            "",
            "🏠 Reply *99* to return to the Previous Menu.",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🤖 Awaiting Product Search...",
        ])

    @staticmethod
    def model_dashboard(data: Dict[str, Any]) -> str:
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📦 PRODUCT ANALYTICS",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"Model",
            f"{data.get('model', 'N/A')}",
            "",
            f"Division",
            f"{data.get('division', 'N/A')}",
            "",
            f"Revenue",
            f"{_format_currency(data.get('total_revenue', 0))}",
            "",
            f"Total Units",
            f"{_format_number(data.get('total_units', 0))}",
            "",
            f"Total DNs",
            f"{_format_number(data.get('total_dns', 0))}",
            "",
            f"Dealers",
            f"{_format_number(data.get('total_dealers', 0))}",
            "",
            f"Warehouses",
            f"{_format_number(data.get('warehouses', 0))}",
            "",
            f"Cities",
            f"{_format_number(data.get('cities', 0))}",
            "",
            f"Delivered DNs",
            f"{_format_number(data.get('delivered_dns', 0))}",
            "",
            f"Pending DNs",
            f"{_format_number(data.get('pending_dns', 0))}",
            "",
            f"Average Delivery",
            f"{data.get('avg_delivery_days', 0):.1f} Days",
            "",
            "Top Cities",
        ]
        top_cities = data.get('top_cities', [])
        if top_cities:
            for city in top_cities:
                lines.append(f"• {city}")
        else:
            lines.append("• No data")

        lines.append("")
        lines.append("Top Warehouses")
        top_wh = data.get('top_warehouses', [])
        if top_wh:
            for wh in top_wh:
                lines.append(f"• {wh}")
        else:
            lines.append("• No data")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Reply another Product or 99",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        return "\n".join(lines)

    @staticmethod
    def division_dashboard(data: Dict[str, Any]) -> str:
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📦 PRODUCT DIVISION",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"Division",
            f"{data.get('division', 'N/A')}",
            "",
            f"Revenue",
            f"{_format_currency(data.get('total_revenue', 0))}",
            "",
            f"Units",
            f"{_format_number(data.get('total_units', 0))}",
            "",
            f"DNs",
            f"{_format_number(data.get('total_dns', 0))}",
            "",
            f"Dealers",
            f"{_format_number(data.get('total_dealers', 0))}",
            "",
            f"Warehouses",
            f"{_format_number(data.get('warehouses', 0))}",
            "",
            f"Cities",
            f"{_format_number(data.get('cities', 0))}",
            "",
            f"Delivered DNs",
            f"{_format_number(data.get('delivered_dns', 0))}",
            "",
            f"Pending DNs",
            f"{_format_number(data.get('pending_dns', 0))}",
            "",
            f"Average Delivery",
            f"{data.get('avg_delivery_days', 0):.1f} Days",
            "",
            "Top Models",
        ]
        top_models = data.get('top_models', [])
        if top_models:
            for model in top_models:
                lines.append(f"• {model}")
        else:
            lines.append("• No data")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Reply another Product or 99",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        return "\n".join(lines)

    @staticmethod
    def not_found(query: str) -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "❌ PRODUCT NOT FOUND",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"No matching Product Model or Division found for '{query}'.",
            "",
            "Please check your spelling and try again.",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Reply another Product or 99",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])

    @staticmethod
    def error() -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "⚠️ SERVICE ERROR",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "An unexpected error occurred. Please try again.",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Reply another Product or 99",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])

# ============================================================
# MAIN SERVICE
# ============================================================

class ProductAnalyticsService:
    def __init__(self) -> None:
        self._version = VERSION
        self._formatter = ProductDashboardFormatter()
        logger.info(f"✅ ProductAnalyticsService v{self._version} initialized")

    def handle_message(self, message: str, sender: str) -> str:
        return self._process(message, sender)

    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        return self._process(message, sender)

    def _process(self, message: str, sender: str) -> str:
        try:
            msg = message.strip()
            if not msg:
                return self._formatter.welcome()

            if msg == "99":
                logger.info("[Service] Exit")
                return "99"

            if msg == "5":
                logger.info("[Service] Product service selected")
                return self._formatter.welcome()

            # Check if it's a greeting
            if msg.lower() in ['hi', 'hello', 'hey', 'start']:
                return self._formatter.welcome()

            logger.info(f"Searching: '{msg}'")

            # Determine search type
            repo = ProductRepository(self._session())

            # 1. Check if it's a model
            model_count = repo.count_model(msg)
            if model_count > 0:
                data = repo.get_model_data(msg)
                if data:
                    return self._formatter.model_dashboard(data)

            # 2. Check if it's a division
            division_count = repo.count_division(msg)
            if division_count > 0:
                data = repo.get_division_data(msg)
                if data:
                    return self._formatter.division_dashboard(data)

            # No match
            return self._formatter.not_found(msg)

        except Exception as e:
            logger.exception("Error in _process")
            return self._formatter.error()

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

# ============================================================
# SINGLETON
# ============================================================

_product_service: Optional[ProductAnalyticsService] = None

def get_product_analytics_service() -> ProductAnalyticsService:
    global _product_service
    if _product_service is None:
        try:
            logger.info("🔧 Creating ProductAnalyticsService...")
            _product_service = ProductAnalyticsService()
            logger.info("✅ ProductAnalyticsService created")
        except Exception as e:
            logger.error(f"Failed to create service: {e}")
            _product_service = ProductAnalyticsService()
    return _product_service

__all__ = [
    "ProductAnalyticsService",
    "get_product_analytics_service",
    "VERSION"
]
