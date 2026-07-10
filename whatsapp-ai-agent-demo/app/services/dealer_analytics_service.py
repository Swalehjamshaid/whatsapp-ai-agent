"""
File: app/services/product_service.py
Version: 8.2 - DEALER-PATTERN (works like Dealer Analytics)
Purpose: Search product models or divisions using raw SQL.
         Mirrors the Dealer service's logic for reliability.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Dict, List, Tuple

from sqlalchemy import text
from app.database import engine

logger = logging.getLogger(__name__)

VERSION = "8.2"
SERVICE_OPTION = "5"
DEBUG_COMMAND = "DEBUG"
TEST_COMMAND = "TEST"

# ============================================================
# UTILITY FUNCTIONS (same as Dealer)
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    try:
        s = str(value).strip()
        return s if s else default
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
# REPOSITORY – EXACT DEALER PATTERN
# ============================================================

class ProductSearchRepository:
    @staticmethod
    def test_connection() -> Dict[str, Any]:
        """Simple test to verify DB connection and count."""
        try:
            with engine.connect() as conn:
                count = conn.execute(
                    text("SELECT COUNT(*) FROM delivery_reports")
                ).scalar()
                return {
                    "connected": True,
                    "row_count": count,
                    "message": f"✅ Connection OK. Found {count} rows."
                }
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return {
                "connected": False,
                "error": str(e),
                "message": f"❌ Connection failed: {e}"
            }

    @staticmethod
    def get_sample_values() -> Dict[str, list]:
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

    @staticmethod
    def resolve_model(model_input: str) -> Optional[str]:
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

    @staticmethod
    def resolve_division(division_input: str) -> Optional[str]:
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

    @staticmethod
    def get_model_data(model_name: str) -> Optional[Dict[str, Any]]:
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

    @staticmethod
    def get_division_data(division_name: str) -> Optional[Dict[str, Any]]:
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
    def debug_info(samples: Dict[str, list]) -> str:
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

    @staticmethod
    def test_info(info: Dict[str, Any]) -> str:
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🔌 DATABASE TEST",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            info.get("message", "No message"),
        ]
        if info.get("connected"):
            lines.append(f"Total rows: {info.get('row_count', 0)}")
        else:
            lines.append(f"Error: {info.get('error', 'Unknown error')}")
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Type DEBUG for sample values, or search for a product.",
        ])
        return "\n".join(lines)

    @staticmethod
    def model_dashboard(data: Dict[str, Any]) -> str:
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

    @staticmethod
    def division_dashboard(data: Dict[str, Any]) -> str:
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
            "🔄 NEXT ACTION",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📝 Enter another Product Model or Division.",
            "",
            "🏠 Reply *99* to return to the Previous Menu.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])

    @staticmethod
    def error() -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "⚠️ SERVICE ERROR",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "An unexpected error occurred while processing your request.",
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

# ============================================================
# MAIN SERVICE – MIRRORS DEALER PATTERN
# ============================================================

class ProductAnalyticsService:
    def __init__(self) -> None:
        self._version = VERSION
        self._formatter = ProductDashboardFormatter()
        logger.info(f"✅ ProductAnalyticsService v{self._version} (Dealer pattern)")

    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """Main entry point – mirrors Dealer's handle_message."""
        try:
            if not message or not message.strip():
                return self._formatter.welcome()

            msg = message.strip()

            # Exit to gateway
            if msg == "99":
                logger.info(f"Exit signal from {sender}")
                return "99"

            # Show welcome when user selects this service
            if msg == SERVICE_OPTION:
                logger.info(f"Service selected, showing welcome to {sender}")
                return self._formatter.welcome()

            # Diagnostic commands
            if msg == DEBUG_COMMAND:
                samples = ProductSearchRepository.get_sample_values()
                return self._formatter.debug_info(samples)

            if msg == TEST_COMMAND:
                info = ProductSearchRepository.test_connection()
                return self._formatter.test_info(info)

            # Search for product
            logger.info(f"Searching for: '{msg}' from {sender}")

            # 1. Try to resolve as a model
            model_name = ProductSearchRepository.resolve_model(msg)
            if model_name:
                model_data = ProductSearchRepository.get_model_data(model_name)
                if model_data:
                    return self._formatter.model_dashboard(model_data)

            # 2. Try to resolve as a division
            division_name = ProductSearchRepository.resolve_division(msg)
            if division_name:
                division_data = ProductSearchRepository.get_division_data(division_name)
                if division_data:
                    return self._formatter.division_dashboard(division_data)

            # No match
            return self._formatter.not_found(msg)

        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return self._formatter.error()

    def get_main_menu(self) -> str:
        return self._formatter.welcome()

    def handle_message(self, message: str, sender: str) -> str:
        return self.process_whatsapp_query(message, sender)

# ============================================================
# SINGLETON & EXPORTS
# ============================================================

_service_instance: Optional[ProductAnalyticsService] = None

def get_product_analytics_service() -> ProductAnalyticsService:
    global _service_instance
    if _service_instance is None:
        _service_instance = ProductAnalyticsService()
    return _service_instance

__all__ = [
    "ProductAnalyticsService",
    "get_product_analytics_service",
    "VERSION",
]
