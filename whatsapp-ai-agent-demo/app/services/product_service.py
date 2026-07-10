"""
File: app/services/product_service.py
Version: 6.5 - COMPLETE PRODUCT SEARCH ENGINE (with full error handling)
Purpose: Single‑entry search for Product Models or Divisions.
         Shows welcome on "5", searches on any other input.
         Catches and logs all errors.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import func, distinct, or_, and_
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

VERSION = "6.5"
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
# REPOSITORY
# ============================================================

class ProductSearchRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_sample_values(self) -> Dict[str, list]:
        """Return sample values from key columns for debugging."""
        try:
            models = self.session.query(
                distinct(DeliveryReport.customer_model)
            ).filter(DeliveryReport.customer_model.isnot(None)).limit(5).all()
            materials = self.session.query(
                distinct(DeliveryReport.material_no)
            ).filter(DeliveryReport.material_no.isnot(None)).limit(5).all()
            divisions = self.session.query(
                distinct(DeliveryReport.division)
            ).filter(DeliveryReport.division.isnot(None)).limit(5).all()
            return {
                "customer_model": [m[0] for m in models if m[0]],
                "material_no": [m[0] for m in materials if m[0]],
                "division": [m[0] for m in divisions if m[0]],
            }
        except Exception as e:
            logger.error(f"Error getting sample values: {e}")
            return {"error": str(e)}

    def get_model_data(self, model: str) -> Optional[Dict[str, Any]]:
        model_clean = model.strip()
        if not model_clean:
            return None

        try:
            filter_cond = or_(
                func.lower(DeliveryReport.customer_model).ilike(f"%{model_clean.lower()}%"),
                func.lower(DeliveryReport.material_no).ilike(f"%{model_clean.lower()}%")
            )

            result = self.session.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label('model'),
                func.coalesce(DeliveryReport.division, 'Unknown').label('division'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.count(distinct(DeliveryReport.warehouse)).label('warehouse_count'),
                func.count(distinct(DeliveryReport.ship_to_city)).label('city_count'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealer_count'),
                func.count(distinct(
                    and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)
                )).label('delivered_dn'),
                func.count(distinct(
                    and_(DeliveryReport.pod_date.is_(None), DeliveryReport.dn_no)
                )).label('pending_dn'),
                func.avg(
                    func.extract('day', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                ).label('avg_delivery_days')
            ).filter(filter_cond).group_by(
                DeliveryReport.customer_model,
                DeliveryReport.division,
                DeliveryReport.material_no
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).first()

            if not result or result.total_revenue is None:
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

            top_cities = self.session.query(
                DeliveryReport.ship_to_city,
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(filter_cond).group_by(
                DeliveryReport.ship_to_city
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(5).all()

            data['top_cities'] = [_text(city.ship_to_city) for city in top_cities if city.ship_to_city]
            return data

        except SQLAlchemyError as e:
            logger.error(f"SQL error in get_model_data: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in get_model_data: {e}")
            return None

    def get_division_data(self, division: str) -> Optional[Dict[str, Any]]:
        division_clean = division.strip()
        if not division_clean:
            return None

        try:
            filter_cond = func.lower(DeliveryReport.division).ilike(f"%{division_clean.lower()}%")

            result = self.session.query(
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.count(distinct(DeliveryReport.warehouse)).label('warehouse_count'),
                func.count(distinct(DeliveryReport.ship_to_city)).label('city_count'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealer_count'),
                func.count(distinct(
                    and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)
                )).label('delivered_dn'),
                func.count(distinct(
                    and_(DeliveryReport.pod_date.is_(None), DeliveryReport.dn_no)
                )).label('pending_dn'),
                func.avg(
                    func.extract('day', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                ).label('avg_delivery_days')
            ).filter(filter_cond).first()

            if not result or result.total_revenue is None:
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

            top_products = self.session.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label('product'),
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(filter_cond).group_by('product').order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(5).all()

            data['top_products'] = [_text(p.product) for p in top_products if p.product]
            return data

        except SQLAlchemyError as e:
            logger.error(f"SQL error in get_division_data: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in get_division_data: {e}")
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
            "💡 Compare your search with these values.",
            "If you see your product here, we need to adjust the search.",
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
        logger.info(f"✅ ProductAnalyticsService v{self._version} (complete)")

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

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
                with self._session() as session:
                    repo = ProductSearchRepository(session)
                    samples = repo.get_sample_values()
                return self._formatter.debug_info(samples)

            logger.info(f"Searching for: '{msg}' from {sender}")

            with self._session() as session:
                repo = ProductSearchRepository(session)
                model_data = repo.get_model_data(msg)
                if model_data:
                    return self._formatter.model_dashboard(model_data)

                division_data = repo.get_division_data(msg)
                if division_data:
                    return self._formatter.division_dashboard(division_data)

            return self._formatter.not_found(msg)

        except Exception as e:
            logger.error(f"Unexpected error in process_whatsapp_query: {e}", exc_info=True)
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
