#!/usr/bin/env python3
# ============================================================
# FILE: app/services/product_service.py
# VERSION: 8.3 - DEALER-PATTERN FINAL
# PURPOSE: Search product models or divisions using raw SQL.
#          Mirrors the dealer_analytics_service.py structure.
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Optional, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "8.3"

# ============================================================
# UTILITY FUNCTIONS (exactly as in dealer_analytics_service)
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
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: int) -> str:
    return f"{num:,}"

# ============================================================
# REPOSITORY – DEALER PATTERN
# ============================================================

class ProductRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_sample_values(self) -> Dict[str, list]:
        """Return up to 5 distinct values from each key column."""
        try:
            with engine.connect() as conn:
                models = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(customer_model)
                        FROM delivery_reports
                        WHERE customer_model IS NOT NULL AND TRIM(customer_model) != ''
                        LIMIT 5
                    """)
                ).fetchall()
                materials = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(material_no)
                        FROM delivery_reports
                        WHERE material_no IS NOT NULL AND TRIM(material_no) != ''
                        LIMIT 5
                    """)
                ).fetchall()
                divisions = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(division)
                        FROM delivery_reports
                        WHERE division IS NOT NULL AND TRIM(division) != ''
                        LIMIT 5
                    """)
                ).fetchall()
                return {
                    "customer_model": [r[0] for r in models if r[0]],
                    "material_no": [r[0] for r in materials if r[0]],
                    "division": [r[0] for r in divisions if r[0]],
                }
        except Exception as e:
            logger.error(f"Error in get_sample_values: {e}")
            return {"error": str(e)}

    def resolve_model(self, model_input: str) -> Optional[str]:
        """Find an exact or partial match for a product model."""
        if not model_input or not model_input.strip():
            return None
        model_clean = model_input.strip()
        logger.info(f"[Repository] Resolving model: '{model_clean}'")

        try:
            with engine.connect() as conn:
                # Exact match (case‑insensitive, trimmed)
                result = conn.execute(
                    text("""
                        SELECT TRIM(COALESCE(customer_model, material_no)) AS model
                        FROM delivery_reports
                        WHERE LOWER(TRIM(COALESCE(customer_model, material_no))) = LOWER(TRIM(:model))
                        LIMIT 1
                    """),
                    {"model": model_clean}
                ).first()
                if result:
                    logger.info(f"[Repository] Exact match found: {result[0]}")
                    return result[0]

                # Partial match with ILIKE
                result = conn.execute(
                    text("""
                        SELECT TRIM(COALESCE(customer_model, material_no)) AS model
                        FROM delivery_reports
                        WHERE TRIM(COALESCE(customer_model, material_no)) ILIKE TRIM(:pattern)
                        LIMIT 1
                    """),
                    {"pattern": f"%{model_clean}%"}
                ).first()
                if result:
                    logger.info(f"[Repository] ILIKE match found: {result[0]}")
                    return result[0]

                logger.info(f"[Repository] No model match for '{model_clean}'")
                return None

        except Exception as e:
            logger.error(f"Error resolving model '{model_clean}': {e}")
            return None

    def resolve_division(self, division_input: str) -> Optional[str]:
        """Find an exact or partial match for a division."""
        if not division_input or not division_input.strip():
            return None
        division_clean = division_input.strip()
        logger.info(f"[Repository] Resolving division: '{division_clean}'")

        try:
            with engine.connect() as conn:
                # Exact match
                result = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(division)
                        FROM delivery_reports
                        WHERE LOWER(TRIM(division)) = LOWER(TRIM(:division))
                        LIMIT 1
                    """),
                    {"division": division_clean}
                ).first()
                if result:
                    logger.info(f"[Repository] Exact match found: {result[0]}")
                    return result[0]

                # Partial match with ILIKE
                result = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(division)
                        FROM delivery_reports
                        WHERE TRIM(division) ILIKE TRIM(:pattern)
                        LIMIT 1
                    """),
                    {"pattern": f"%{division_clean}%"}
                ).first()
                if result:
                    logger.info(f"[Repository] ILIKE match found: {result[0]}")
                    return result[0]

                logger.info(f"[Repository] No division match for '{division_clean}'")
                return None

        except Exception as e:
            logger.error(f"Error resolving division '{division_clean}': {e}")
            return None

    def get_model_data(self, model_name: str) -> Optional[Dict[str, Any]]:
        """Get full aggregated data for a resolved model name."""
        model_clean = model_name.strip()
        if not model_clean:
            return None

        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT
                            TRIM(COALESCE(customer_model, material_no, 'Unknown')) AS model,
                            TRIM(COALESCE(division, 'Unknown')) AS division,
                            COALESCE(SUM(dn_amount), 0) AS total_revenue,
                            COALESCE(SUM(dn_qty), 0) AS total_units,
                            COUNT(DISTINCT dn_no) AS dn_count,
                            COUNT(DISTINCT warehouse) AS warehouse_count,
                            COUNT(DISTINCT ship_to_city) AS city_count,
                            COUNT(DISTINCT customer_name) AS dealer_count,
                            COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dn,
                            COUNT(DISTINCT CASE WHEN pod_date IS NULL THEN dn_no END) AS pending_dn,
                            AVG(EXTRACT(DAY FROM (good_issue_date - dn_create_date))) AS avg_delivery_days
                        FROM delivery_reports
                        WHERE TRIM(COALESCE(customer_model, material_no)) = TRIM(:model)
                        GROUP BY customer_model, material_no, division
                        ORDER BY total_revenue DESC
                        LIMIT 1
                    """),
                    {"model": model_clean}
                ).first()

                if not result or result.total_revenue == 0:
                    return None

                data = {
                    'model': _text(result.model),
                    'division': _text(result.division),
                    'total_revenue': _number(result.total_revenue),
                    'total_units': _int(result.total_units),
                    'dn_count': _int(result.dn_count),
                    'warehouse_count': _int(result.warehouse_count),
                    'city_count': _int(result.city_count),
                    'dealer_count': _int(result.dealer_count),
                    'delivered_dn': _int(result.delivered_dn),
                    'pending_dn': _int(result.pending_dn),
                    'avg_delivery_days': round(_number(result.avg_delivery_days), 1),
                }

                # Top cities
                cities = conn.execute(
                    text("""
                        SELECT TRIM(ship_to_city) AS city, SUM(dn_amount) AS revenue
                        FROM delivery_reports
                        WHERE TRIM(COALESCE(customer_model, material_no)) = TRIM(:model)
                        GROUP BY ship_to_city
                        ORDER BY revenue DESC
                        LIMIT 5
                    """),
                    {"model": model_clean}
                ).fetchall()
                data['top_cities'] = [c[0] for c in cities if c[0]]

                return data

        except Exception as e:
            logger.error(f"Error getting model data for '{model_clean}': {e}")
            return None

    def get_division_data(self, division_name: str) -> Optional[Dict[str, Any]]:
        """Get full aggregated data for a resolved division name."""
        division_clean = division_name.strip()
        if not division_clean:
            return None

        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT
                            COALESCE(SUM(dn_amount), 0) AS total_revenue,
                            COALESCE(SUM(dn_qty), 0) AS total_units,
                            COUNT(DISTINCT dn_no) AS dn_count,
                            COUNT(DISTINCT warehouse) AS warehouse_count,
                            COUNT(DISTINCT ship_to_city) AS city_count,
                            COUNT(DISTINCT customer_name) AS dealer_count,
                            COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dn,
                            COUNT(DISTINCT CASE WHEN pod_date IS NULL THEN dn_no END) AS pending_dn,
                            AVG(EXTRACT(DAY FROM (good_issue_date - dn_create_date))) AS avg_delivery_days
                        FROM delivery_reports
                        WHERE TRIM(division) = TRIM(:division)
                    """),
                    {"division": division_clean}
                ).first()

                if not result or result.total_revenue == 0:
                    return None

                data = {
                    'division': division_clean,
                    'total_revenue': _number(result.total_revenue),
                    'total_units': _int(result.total_units),
                    'dn_count': _int(result.dn_count),
                    'warehouse_count': _int(result.warehouse_count),
                    'city_count': _int(result.city_count),
                    'dealer_count': _int(result.dealer_count),
                    'delivered_dn': _int(result.delivered_dn),
                    'pending_dn': _int(result.pending_dn),
                    'avg_delivery_days': round(_number(result.avg_delivery_days), 1),
                }

                # Top products in this division
                products = conn.execute(
                    text("""
                        SELECT TRIM(COALESCE(customer_model, material_no, 'Unknown')) AS product,
                               SUM(dn_amount) AS revenue
                        FROM delivery_reports
                        WHERE TRIM(division) = TRIM(:division)
                        GROUP BY customer_model, material_no
                        ORDER BY revenue DESC
                        LIMIT 5
                    """),
                    {"division": division_clean}
                ).fetchall()
                data['top_products'] = [p[0] for p in products if p[0]]

                return data

        except Exception as e:
            logger.error(f"Error getting division data for '{division_clean}': {e}")
            return None

# ============================================================
# MAIN SERVICE – EXACT DEALER STRUCTURE
# ============================================================

class ProductAnalyticsService:
    def __init__(self) -> None:
        self._version = VERSION
        logger.info(f"✅ ProductAnalyticsService v{self._version} initialized")
        logger.info("   Using raw SQL with Dealer pattern")

    def handle_message(self, message: str, sender: str) -> str:
        """Main entry point – mirrors dealer_analytics_service handle_message."""
        try:
            message_clean = message.strip()

            # SPECIAL: 99 exits to main menu
            if message_clean == "99":
                logger.info("[Service] Exit command detected, returning 99")
                return "99"

            # Numeric command '5' shows welcome (entry point)
            if message_clean == "5":
                logger.info("[Service] Product service selected, showing welcome")
                return self._get_welcome_message()

            # Diagnostic commands
            if message_clean.upper() == "DEBUG":
                return self._get_debug_info()

            if message_clean.upper() == "TEST":
                return self._test_connection()

            # Check if it's a greeting or empty
            if not message_clean or message_clean.lower() in ['hi', 'hello', 'hey', 'start']:
                return self._get_welcome_message()

            logger.info("[Service] Searching for: '%s' from %s", message_clean, sender)

            # Search for product
            result = self._search_product(message_clean)
            return result

        except Exception as e:
            logger.exception("[Service] Error in handle_message")
            return f"⚠️ Error: {str(e)}\n\nPlease try again with a different product name or division."

    def _get_welcome_message(self) -> str:
        """Welcome message for the product intelligence center."""
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

    def _get_debug_info(self) -> str:
        """Return debug sample values."""
        try:
            repo = ProductRepository(self._session())
            samples = repo.get_sample_values()
            lines = [
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "🔍 DEBUG SAMPLE VALUES",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "These are the first 5 distinct values from each column:",
                "",
                "📦 customer_model:"
            ]
            for val in samples.get("customer_model", []):
                lines.append(f"  • {val}")
            lines.append("")
            lines.append("🔢 material_no:")
            for val in samples.get("material_no", []):
                lines.append(f"  • {val}")
            lines.append("")
            lines.append("📂 division:")
            for val in samples.get("division", []):
                lines.append(f"  • {val}")
            if "error" in samples:
                lines.append("")
                lines.append(f"⚠️ Error: {samples['error']}")
            lines.extend([
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "💡 If you see your values above, search should work.",
                "If the list is empty, check the database connection.",
                "",
                "Type another search or 99 to exit.",
            ])
            return "\n".join(lines)
        except Exception as e:
            logger.exception("Error in _get_debug_info")
            return f"⚠️ Error: {str(e)}"

    def _test_connection(self) -> str:
        """Test database connection and return row count."""
        try:
            with engine.connect() as conn:
                count = conn.execute(
                    text("SELECT COUNT(*) FROM delivery_reports")
                ).scalar()
                return "\n".join([
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "🔌 DATABASE TEST",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                    f"✅ Connection OK. Found {count} rows.",
                    "",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "Type DEBUG for sample values, or search for a product.",
                ])
        except Exception as e:
            logger.exception("Connection test failed")
            return "\n".join([
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "🔌 DATABASE TEST",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                f"❌ Connection failed: {e}",
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            ])

    def _search_product(self, query: str) -> str:
        """Search for a product model or division and return dashboard."""
        try:
            repo = ProductRepository(self._session())

            # 1. Try to resolve as a model
            model_name = repo.resolve_model(query)
            if model_name:
                model_data = repo.get_model_data(model_name)
                if model_data:
                    return self._render_model_dashboard(model_data)

            # 2. Try to resolve as a division
            division_name = repo.resolve_division(query)
            if division_name:
                division_data = repo.get_division_data(division_name)
                if division_data:
                    return self._render_division_dashboard(division_data)

            # No match
            return self._format_not_found(query)

        except Exception as e:
            logger.exception(f"Error searching for '{query}'")
            return self._format_error(str(e))

    def _render_model_dashboard(self, data: Dict[str, Any]) -> str:
        """Render model dashboard."""
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "      📦 PRODUCT INTELLIGENCE CENTER",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📦 Product Model",
            f"{data.get('model', 'N/A')}",
            "",
            f"📂 Division",
            f"{data.get('division', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📊 SALES OVERVIEW",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"💰 Revenue          : {_format_currency(data.get('total_revenue', 0))}",
            f"📦 Total Units      : {_format_number(data.get('total_units', 0))}",
            f"🚚 Total DNs        : {_format_number(data.get('dn_count', 0))}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🌍 COVERAGE",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"🏭 Warehouses       : {_format_number(data.get('warehouse_count', 0))}",
            f"🏙 Cities           : {_format_number(data.get('city_count', 0))}",
            f"👤 Dealers          : {_format_number(data.get('dealer_count', 0))}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🚚 DELIVERY KPI",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"🟢 Delivered DNs    : {_format_number(data.get('delivered_dn', 0))}",
            f"🟡 Pending DNs      : {_format_number(data.get('pending_dn', 0))}",
            "",
            f"📅 Avg Delivery     : {data.get('avg_delivery_days', 0)} Days",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏆 TOP CITIES",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        top_cities = data.get('top_cities', [])
        if top_cities:
            for city in top_cities:
                lines.append(f"• {city}")
        else:
            lines.append("• No data")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🤖 AI INSIGHT",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "✅ High demand product.",
            "✅ Excellent delivery performance." if data.get('avg_delivery_days', 99) <= 3 else "⚠️ Delivery time can be improved.",
            "⚠ Pending deliveries require follow-up." if data.get('pending_dn', 0) > 0 else "✅ No pending deliveries.",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🔄 NEXT ACTION",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📝 Enter another Product Model or Division.",
            "",
            "🏠 Reply *99* to return to the Previous Menu.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        return "\n".join(lines)

    def _render_division_dashboard(self, data: Dict[str, Any]) -> str:
        """Render division dashboard."""
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "      📦 PRODUCT DIVISION ANALYTICS",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📂 Division",
            f"{data.get('division', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📊 BUSINESS OVERVIEW",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"💰 Revenue           : {_format_currency(data.get('total_revenue', 0))}",
            f"📦 Units Sold        : {_format_number(data.get('total_units', 0))}",
            f"🚚 Total DNs         : {_format_number(data.get('dn_count', 0))}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🌍 COVERAGE",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"🏭 Warehouses        : {_format_number(data.get('warehouse_count', 0))}",
            f"🏙 Cities            : {_format_number(data.get('city_count', 0))}",
            f"👤 Active Dealers    : {_format_number(data.get('dealer_count', 0))}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🚚 DELIVERY KPI",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"🟢 Delivered DNs     : {_format_number(data.get('delivered_dn', 0))}",
            f"🟡 Pending DNs       : {_format_number(data.get('pending_dn', 0))}",
            "",
            f"📅 Avg Delivery      : {data.get('avg_delivery_days', 0)} Days",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏆 TOP 5 PRODUCTS",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        top_products = data.get('top_products', [])
        if top_products:
            for product in top_products:
                lines.append(f"• {product}")
        else:
            lines.append("• No products")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🤖 AI INSIGHT",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "✅ High demand division.",
            "⚠ Improve delivery performance in pending locations." if data.get('pending_dn', 0) > 0 else "✅ Excellent delivery performance.",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🔄 NEXT ACTION",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📝 Enter another Product Model or Division.",
            "",
            "🏠 Reply *99* to return to the Previous Menu.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        return "\n".join(lines)

    def _format_not_found(self, query: str) -> str:
        """Format not found message."""
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
            "🔄 NEXT ACTION",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📝 Enter another Product Model or Division.",
            "",
            "🏠 Reply *99* to return to the Previous Menu.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])

    def _format_error(self, error: str) -> str:
        """Format error message."""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "⚠️ SERVICE ERROR",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"An error occurred: {error}",
            "",
            "Please try again later.",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🔄 NEXT ACTION",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📝 Enter another Product Model or Division.",
            "",
            "🏠 Reply *99* to return to the Previous Menu.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

# ============================================================
# SINGLETON & EXPORTS
# ============================================================

_product_service: Optional[ProductAnalyticsService] = None

def get_product_analytics_service() -> ProductAnalyticsService:
    global _product_service
    try:
        if _product_service is None:
            logger.info("🔧 Creating ProductAnalyticsService instance...")
            _product_service = ProductAnalyticsService()
            logger.info("✅ ProductAnalyticsService instance created successfully")
        return _product_service
    except Exception as e:
        logger.error(f"❌ Failed to create ProductAnalyticsService: {e}")
        import traceback
        logger.error(traceback.format_exc())
        _product_service = ProductAnalyticsService()
        return _product_service

__all__ = [
    "ProductAnalyticsService",
    "get_product_analytics_service",
    "VERSION"
]
