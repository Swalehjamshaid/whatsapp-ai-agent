"""
File: app/services/product_service.py
Version: 7.0 - RAW SQL WITH DEBUG (like Dealer Analytics)
Purpose: Search product models or divisions using PostgreSQL ILIKE.
         Uses engine.connect() for reliability.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, List

from sqlalchemy import text
from app.database import engine

logger = logging.getLogger(__name__)

VERSION = "7.0"
SERVICE_OPTION = "5"
DEBUG_COMMAND = "DEBUG"

# ============================================================
# UTILITY FUNCTIONS
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
# REPOSITORY (raw SQL)
# ============================================================

class ProductSearchRepository:
    @staticmethod
    def get_sample_values() -> Dict[str, list]:
        """Return up to 5 distinct values from customer_model and division."""
        try:
            with engine.connect() as conn:
                # customer_model samples
                models = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(customer_model)
                        FROM delivery_reports
                        WHERE customer_model IS NOT NULL AND TRIM(customer_model) != ''
                        LIMIT 5
                    """)
                ).fetchall()
                # division samples
                divisions = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(division)
                        FROM delivery_reports
                        WHERE division IS NOT NULL AND TRIM(division) != ''
                        LIMIT 5
                    """)
                ).fetchall()
                # material_no samples (optional)
                materials = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(material_no)
                        FROM delivery_reports
                        WHERE material_no IS NOT NULL AND TRIM(material_no) != ''
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
    def get_model_data(model: str) -> Optional[Dict[str, Any]]:
        """Get aggregated data for a product model (case‑insensitive ILIKE)."""
        model_clean = model.strip()
        if not model_clean:
            return None

        try:
            with engine.connect() as conn:
                # Main aggregates
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
                        WHERE TRIM(customer_model) ILIKE TRIM(:model)
                           OR TRIM(material_no) ILIKE TRIM(:model)
                        GROUP BY customer_model, material_no, division
                        ORDER BY total_revenue DESC
                        LIMIT 1
                    """),
                    {"model": f"%{model_clean}%"}
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

                # Top cities for this model
                cities = conn.execute(
                    text("""
                        SELECT TRIM(ship_to_city) AS city, SUM(dn_amount) AS revenue
                        FROM delivery_reports
                        WHERE TRIM(customer_model) ILIKE TRIM(:model)
                           OR TRIM(material_no) ILIKE TRIM(:model)
                        GROUP BY ship_to_city
                        ORDER BY revenue DESC
                        LIMIT 5
                    """),
                    {"model": f"%{model_clean}%"}
                ).fetchall()
                data['top_cities'] = [c[0] for c in cities if c[0]]

                return data

        except Exception as e:
            logger.error(f"Error in get_model_data for '{model_clean}': {e}")
            return None

    @staticmethod
    def get_division_data(division: str) -> Optional[Dict[str, Any]]:
        """Get aggregated data for a product division (case‑insensitive ILIKE)."""
        division_clean = division.strip()
        if not division_clean:
            return None

        try:
            with engine.connect() as conn:
                # Main aggregates
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
                        WHERE TRIM(division) ILIKE TRIM(:division)
                    """),
                    {"division": f"%{division_clean}%"}
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

                # Top 5 models in this division
                products = conn.execute(
                    text("""
                        SELECT TRIM(COALESCE(customer_model, material_no, 'Unknown')) AS product,
                               SUM(dn_amount) AS revenue
                        FROM delivery_reports
                        WHERE TRIM(division) ILIKE TRIM(:division)
                        GROUP BY customer_model, material_no
                        ORDER BY revenue DESC
                        LIMIT 5
                    """),
                    {"division": f"%{division_clean}%"}
                ).fetchall()
                data['top_products'] = [p[0] for p in products if p[0]]

                return data

        except Exception as e:
            logger.error(f"Error in get_division_data for '{division_clean}': {e}")
            return None

# ============================================================
# FORMATTERS (unchanged)
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
            "💡 Compare your search with these values.",
            "If you see your product here, search should work.",
            "",
            "Type another search or 99 to exit.",
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
# MAIN SERVICE
# ============================================================

class ProductAnalyticsService:
    def __init__(self) -> None:
        self._version = VERSION
        self._formatter = ProductDashboardFormatter()
        logger.info(f"✅ ProductAnalyticsService v{self._version} (raw SQL)")

    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        try:
            if not message or not message.strip():
                return self._formatter.welcome()

            msg = message.strip()

            if msg == "99":
                logger.info(f"Exit signal from {sender}")
                return "99"

            if msg == SERVICE_OPTION:
                logger.info(f"Service selected, showing welcome to {sender}")
                return self._formatter.welcome()

            if msg == DEBUG_COMMAND:
                samples = ProductSearchRepository.get_sample_values()
                return self._formatter.debug_info(samples)

            logger.info(f"Searching for: '{msg}' from {sender}")

            # Try model
            model_data = ProductSearchRepository.get_model_data(msg)
            if model_data:
                return self._formatter.model_dashboard(model_data)

            # Try division
            division_data = ProductSearchRepository.get_division_data(msg)
            if division_data:
                return self._formatter.division_dashboard(division_data)

            return self._formatter.not_found(msg)

        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return self._formatter.error()

    def get_main_menu(self) -> str:
        return self._formatter.welcome()

    def handle_message(self, message: str, sender: str) -> str:
        return self.process_whatsapp_query(message, sender)

# ============================================================
# SINGLETON
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
