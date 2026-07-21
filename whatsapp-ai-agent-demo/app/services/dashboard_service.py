# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 7.6 - MATCHES ACTUAL POSTGRES SCHEMA
# ============================================================
# NOTE: All column names are exactly as they appear in the
#       delivery_reports table. No hypothetical columns.
# ============================================================

import asyncio
import datetime
import hashlib
import json
import logging
import os
import time
from typing import Optional, Dict, List, Any, Union, Tuple
from collections import defaultdict
from functools import wraps
from datetime import datetime, timedelta, date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import engine, SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _format_currency(amount: float) -> str:
    if amount is None:
        return "PKR 0.00"
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: Union[int, float]) -> str:
    if num is None:
        return "0"
    return f"{num:,}"

def _format_date(date_val: Any) -> str:
    if not date_val:
        return "N/A"
    try:
        if isinstance(date_val, str):
            for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
                try:
                    dt = datetime.strptime(date_val, fmt)
                    break
                except ValueError:
                    continue
            else:
                return date_val
        elif isinstance(date_val, datetime):
            dt = date_val
        elif isinstance(date_val, date):
            dt = datetime.combine(date_val, datetime.min.time())
        else:
            return str(date_val)
        return dt.strftime("%d-%b-%Y")
    except Exception:
        return str(date_val)

def _safe_float(value: Any) -> float:
    return float(value or 0)

def _safe_int(value: Any) -> int:
    return int(value or 0)

def _pct(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return round((numerator / denominator) * 100, 2)

# ============================================================
# CACHE ENGINE
# ============================================================

class InMemoryCache:
    def __init__(self, ttl_seconds=5):
        self._cache = {}
        self._ttl = ttl_seconds

    def _make_key(self, func_name: str, args: tuple, kwargs: dict) -> str:
        key_parts = [func_name, str(args), str(sorted(kwargs.items()))]
        return hashlib.md5("|".join(key_parts).encode()).hexdigest()

    def get(self, key):
        entry = self._cache.get(key)
        if entry and (time.time() - entry['timestamp'] < self._ttl):
            return entry['value']
        return None

    def set(self, key, value):
        self._cache[key] = {'value': value, 'timestamp': time.time()}

    def clear(self):
        self._cache.clear()

cache = InMemoryCache(ttl_seconds=5)

def cached(ttl=5):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if kwargs.get('no_cache'):
                return await func(*args, **kwargs)
            key = cache._make_key(func.__name__, args, kwargs)
            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value
            result = await func(*args, **kwargs)
            cache.set(key, result)
            return result
        return wrapper
    return decorator

# ============================================================
# DASHBOARD REPOSITORY - MATCHES ACTUAL SCHEMA
# ============================================================

class DashboardRepository:
    def __init__(self):
        logger.info("🗄️ DashboardRepository v7.6 initialized (schema matched)")

    def _execute(self, sql: str, params: Optional[Dict[str, Any]] = None):
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                return result
        except Exception as e:
            logger.exception(f"❌ SQL execution failed: {sql[:200]}")
            raise

    # ==================================================================
    # EXECUTIVE SUMMARY
    # ==================================================================

    def get_summary(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("🔍 Fetching SUMMARY from PostgreSQL...")
        try:
            sql = """
                SELECT
                    COALESCE(SUM(dn_amount), 0) AS total_revenue,
                    COALESCE(SUM(dn_qty), 0) AS total_units,
                    COUNT(DISTINCT dn_no) AS total_dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
                FROM delivery_reports
            """
            row = self._execute(sql).first()
            if not row:
                logger.warning("⚠️ No summary data found")
                return self._empty_summary()

            total_revenue = _safe_float(row.total_revenue)
            total_units = _safe_int(row.total_units)
            total_dn = _safe_int(row.total_dn)
            pgi_completed = _safe_int(row.pgi_completed)
            delivered_dns = _safe_int(row.delivered_dns)
            pod_completed = _safe_int(row.pod_completed)

            # Count distinct entities using actual column names
            dealers = self._execute("SELECT COUNT(DISTINCT dealer_code) FROM delivery_reports WHERE dealer_code IS NOT NULL").scalar() or 0
            warehouses = self._execute("SELECT COUNT(DISTINCT warehouse) FROM delivery_reports WHERE warehouse IS NOT NULL").scalar() or 0
            cities = self._execute("SELECT COUNT(DISTINCT ship_to_city) FROM delivery_reports WHERE ship_to_city IS NOT NULL").scalar() or 0
            products = self._execute("SELECT COUNT(DISTINCT material_no) FROM delivery_reports WHERE material_no IS NOT NULL").scalar() or 0

            pgi_rate = _pct(pgi_completed, total_dn)
            delivery_rate = _pct(delivered_dns, total_dn)
            pod_rate = _pct(pod_completed, delivered_dns if delivered_dns else 1)
            health_score = round((pgi_rate + delivery_rate + pod_rate) / 3, 2)

            logger.info(f"✅ Summary: revenue={total_revenue}, units={total_units}, dn={total_dn}")

            return {
                "total_revenue": total_revenue,
                "total_units": total_units,
                "total_delivery_notes": total_dn,
                "pgi_completed": pgi_completed,
                "delivered_dns": delivered_dns,
                "pod_completed": pod_completed,
                "active_dealers": dealers,
                "active_warehouses": warehouses,
                "active_cities": cities,
                "active_products": products,
                "active_transporters": 0,
                "average_delivery_days": 0.0,
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "otif_percentage": delivery_rate,
                "dashboard_health_score": health_score,
                "last_database_refresh": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.exception("❌ Failed to get summary")
            return self._empty_summary()

    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "total_revenue": 0.0,
            "total_units": 0,
            "total_delivery_notes": 0,
            "pgi_completed": 0,
            "delivered_dns": 0,
            "pod_completed": 0,
            "active_dealers": 0,
            "active_warehouses": 0,
            "active_cities": 0,
            "active_products": 0,
            "active_transporters": 0,
            "average_delivery_days": 0.0,
            "pgi_achievement_rate": 0.0,
            "delivery_achievement_rate": 0.0,
            "pod_completion_rate": 0.0,
            "otif_percentage": 0.0,
            "dashboard_health_score": 0.0,
            "last_database_refresh": None,
        }

    # ==================================================================
    # WAREHOUSE PERFORMANCE
    # ==================================================================

    def get_warehouse_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        logger.info("🏢 Fetching WAREHOUSE performance...")
        try:
            sql = """
                SELECT
                    warehouse AS warehouse_name,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    COUNT(DISTINCT dn_no) AS delivery_notes,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
                FROM delivery_reports
                WHERE warehouse IS NOT NULL
                GROUP BY warehouse
                ORDER BY revenue DESC
                LIMIT 5
            """
            rows = self._execute(sql).fetchall()
            result = []
            for row in rows:
                dn = _safe_int(row.delivery_notes)
                pgi = _safe_int(row.pgi_completed)
                delivered = _safe_int(row.delivered_dns)
                pod = _safe_int(row.pod_completed)
                pgi_rate = _pct(pgi, dn)
                delivery_rate = _pct(delivered, dn)
                pod_rate = _pct(pod, delivered if delivered else 1)
                health = round((pgi_rate + delivery_rate + pod_rate) / 3, 2)
                result.append({
                    "warehouse_name": row.warehouse_name,
                    "revenue": _safe_float(row.revenue),
                    "units": _safe_int(row.units),
                    "delivery_notes": dn,
                    "pgi_achievement_rate": pgi_rate,
                    "delivery_achievement_rate": delivery_rate,
                    "pod_completion_rate": pod_rate,
                    "health_score": health,
                    "risk_level": "Low" if health >= 85 else "Medium" if health >= 70 else "High",
                    "performance_grade": "A" if health >= 90 else "B" if health >= 80 else "C",
                })
            logger.info(f"✅ Warehouse performance: {len(result)} records")
            return result
        except Exception as e:
            logger.exception("❌ Failed to get warehouse performance")
            return []

    # ==================================================================
    # DEALER PERFORMANCE
    # ==================================================================

    def get_dealer_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        logger.info("👥 Fetching DEALER performance...")
        try:
            sql = """
                SELECT
                    dealer_code,
                    customer_name AS dealer_name,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    COUNT(DISTINCT dn_no) AS delivery_notes,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
                FROM delivery_reports
                WHERE dealer_code IS NOT NULL
                GROUP BY dealer_code, customer_name
                ORDER BY revenue DESC
                LIMIT 5
            """
            rows = self._execute(sql).fetchall()
            result = []
            for row in rows:
                dn = _safe_int(row.delivery_notes)
                pod = _safe_int(row.pod_completed)
                pod_rate = _pct(pod, dn)
                score = pod_rate
                result.append({
                    "dealer_name": row.dealer_name or row.dealer_code,
                    "dealer_code": row.dealer_code,
                    "revenue": _safe_float(row.revenue),
                    "units": _safe_int(row.units),
                    "delivery_notes": dn,
                    "pod_completion_rate": pod_rate,
                    "average_delivery_days": 0.0,
                    "performance_score": score,
                    "ai_recommendation": "Top performer" if score > 80 else "Needs improvement" if score < 60 else "Good",
                })
            logger.info(f"✅ Dealer performance: {len(result)} records")
            return result
        except Exception as e:
            logger.exception("❌ Failed to get dealer performance")
            return []

    # ==================================================================
    # PRODUCT PERFORMANCE
    # ==================================================================

    def get_product_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        logger.info("📦 Fetching PRODUCT performance...")
        try:
            sql = """
                SELECT
                    material_no AS sku,
                    customer_model AS product_name,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    COUNT(DISTINCT dn_no) AS delivery_notes,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
                FROM delivery_reports
                WHERE material_no IS NOT NULL
                GROUP BY material_no, customer_model
                ORDER BY revenue DESC
                LIMIT 5
            """
            rows = self._execute(sql).fetchall()
            result = []
            total_revenue = sum(row.revenue for row in rows)
            for row in rows:
                dn = _safe_int(row.delivery_notes)
                pod = _safe_int(row.pod_completed)
                pod_rate = _pct(pod, dn)
                revenue = _safe_float(row.revenue)
                units = _safe_int(row.units)
                share = (revenue / total_revenue * 100) if total_revenue else 0
                abc = "A" if share > 40 else "B" if share > 20 else "C"
                result.append({
                    "product_name": row.product_name or row.sku,
                    "sku": row.sku,
                    "revenue": revenue,
                    "units": units,
                    "delivery_notes": dn,
                    "pod_completion_rate": pod_rate,
                    "abc_class": abc,
                    "revenue_share": round(share, 2),
                    "slow_moving_flag": units < 50,
                    "fast_moving_flag": units > 500,
                    "dead_stock_flag": units == 0,
                })
            logger.info(f"✅ Product performance: {len(result)} records")
            return result
        except Exception as e:
            logger.exception("❌ Failed to get product performance")
            return []

    # ==================================================================
    # CITY PERFORMANCE
    # ==================================================================

    def get_city_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        logger.info("🌆 Fetching CITY performance...")
        try:
            sql = """
                SELECT
                    ship_to_city AS city,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    COUNT(DISTINCT dn_no) AS delivery_notes,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
                FROM delivery_reports
                WHERE ship_to_city IS NOT NULL
                GROUP BY ship_to_city
                ORDER BY revenue DESC
                LIMIT 5
            """
            rows = self._execute(sql).fetchall()
            result = []
            for row in rows:
                dn = _safe_int(row.delivery_notes)
                pod = _safe_int(row.pod_completed)
                pod_rate = _pct(pod, dn)
                health = pod_rate
                result.append({
                    "city": row.city,
                    "revenue": _safe_float(row.revenue),
                    "units": _safe_int(row.units),
                    "delivery_notes": dn,
                    "pod_completion_rate": pod_rate,
                    "average_delivery_days": 0.0,
                    "health_score": health,
                    "risk_level": "High" if health < 70 else "Medium" if health < 85 else "Low",
                })
            logger.info(f"✅ City performance: {len(result)} records")
            return result
        except Exception as e:
            logger.exception("❌ Failed to get city performance")
            return []

    # ==================================================================
    # TRANSPORT PERFORMANCE (no transporter column – disabled)
    # ==================================================================

    def get_transport_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        logger.warning("⚠️ Transporter column does not exist – returning empty list")
        return []

    # ==================================================================
    # MONTHLY TRENDS
    # ==================================================================

    def get_monthly_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
        logger.info("📈 Fetching MONTHLY trends...")
        try:
            sql = """
                SELECT
                    TO_CHAR(dn_create_date, 'YYYY-MM') AS month,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    COUNT(DISTINCT dn_no) AS dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
                FROM delivery_reports
                WHERE dn_create_date IS NOT NULL
                GROUP BY month
                ORDER BY month
            """
            rows = self._execute(sql).fetchall()
            months = []
            revenue = []
            units = []
            dn = []
            pgi = []
            delivery = []
            pod = []
            for row in rows:
                months.append(row.month)
                revenue.append(_safe_float(row.revenue))
                units.append(_safe_int(row.units))
                dn.append(_safe_int(row.dn))
                pgi.append(_pct(_safe_int(row.pgi_completed), _safe_int(row.dn)))
                delivered = _safe_int(row.delivered_dns)
                delivery.append(_pct(delivered, _safe_int(row.dn)))
                pod.append(_pct(_safe_int(row.pod_completed), delivered if delivered else 1))
            logger.info(f"✅ Monthly trends: {len(months)} months")
            return {
                "months": months,
                "revenue": revenue,
                "units": units,
                "delivery_notes": dn,
                "pgi_rate": pgi,
                "delivery_achievement": delivery,
                "pod_rate": pod,
            }
        except Exception as e:
            logger.exception("❌ Failed to get monthly trends")
            return {"months": [], "revenue": [], "units": [], "delivery_notes": [], "pgi_rate": [], "delivery_achievement": [], "pod_rate": []}

    # ==================================================================
    # DAILY TRENDS
    # ==================================================================

    def get_daily_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
        logger.info("📊 Fetching DAILY trends...")
        try:
            sql = """
                SELECT
                    dn_create_date AS date,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    COUNT(DISTINCT dn_no) AS dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
                FROM delivery_reports
                WHERE dn_create_date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY dn_create_date
                ORDER BY dn_create_date
            """
            rows = self._execute(sql).fetchall()
            dates = []
            revenue = []
            units = []
            dn = []
            pgi = []
            delivered = []
            pod = []
            for row in rows:
                dates.append(row.date.strftime('%Y-%m-%d'))
                revenue.append(_safe_float(row.revenue))
                units.append(_safe_int(row.units))
                dn.append(_safe_int(row.dn))
                pgi.append(_safe_int(row.pgi_completed))
                delivered.append(_safe_int(row.delivered_dns))
                pod.append(_safe_int(row.pod_completed))
            logger.info(f"✅ Daily trends: {len(dates)} days")
            return {
                "dates": dates,
                "revenue": revenue,
                "units": units,
                "delivery_notes": dn,
                "pgi_completed": pgi,
                "delivered_dns": delivered,
                "pod_completed": pod,
            }
        except Exception as e:
            logger.exception("❌ Failed to get daily trends")
            return {"dates": [], "revenue": [], "units": [], "delivery_notes": [], "pgi_completed": [], "delivered_dns": [], "pod_completed": []}

    # ==================================================================
    # HEALTH & RECORD COUNT
    # ==================================================================

    def get_health(self) -> Dict[str, Any]:
        try:
            count = self._execute("SELECT COUNT(*) FROM delivery_reports").scalar() or 0
            return {"status": "healthy" if count > 0 else "unhealthy", "message": "Data available" if count > 0 else "No data", "record_count": count}
        except Exception as e:
            return {"status": "unhealthy", "message": str(e)}

    def get_record_count(self) -> int:
        try:
            return self._execute("SELECT COUNT(*) FROM delivery_reports").scalar() or 0
        except Exception:
            return 0

    def get_metadata(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "application_version": "7.6.0",
            "database_version": "PostgreSQL",
            "postgresql_status": "connected",
            "record_count": self.get_record_count(),
            "last_refresh": datetime.utcnow().isoformat(),
            "environment": os.getenv("ENVIRONMENT", "production"),
        }

# ============================================================
# DASHBOARD SERVICE (Orchestrator)
# ============================================================

class DashboardService:
    def __init__(self):
        self._repository = DashboardRepository()
        logger.info("🚀 DashboardService v7.6 initialized (schema matched)")

    @cached(ttl=5)
    async def get_dashboard_data(
        self,
        filters: Optional[Dict[str, Any]] = None,
        role: str = "viewer",
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        filters = filters or {}
        logger.info(f"📡 Dashboard API called with filters: {filters}")

        summary = await asyncio.to_thread(self._repository.get_summary, filters)
        warehouse = await asyncio.to_thread(self._repository.get_warehouse_performance, filters)
        dealer = await asyncio.to_thread(self._repository.get_dealer_performance, filters)
        product = await asyncio.to_thread(self._repository.get_product_performance, filters)
        city = await asyncio.to_thread(self._repository.get_city_performance, filters)
        transport = await asyncio.to_thread(self._repository.get_transport_performance, filters)
        monthly = await asyncio.to_thread(self._repository.get_monthly_trends, filters)
        daily = await asyncio.to_thread(self._repository.get_daily_trends, filters)
        health = await asyncio.to_thread(self._repository.get_health)
        metadata = await asyncio.to_thread(self._repository.get_metadata, filters)

        cards = {
            "revenue": {"value": summary.get("total_revenue", 0), "target": 150000000, "progress": 0, "icon": "fa-chart-line", "color": "primary", "format": "currency", "label": "Revenue"},
            "delivery_notes": {"value": summary.get("total_delivery_notes", 0), "target": 5000, "progress": 0, "icon": "fa-file-invoice", "color": "info", "format": "number", "label": "Delivery Notes"},
            "pgi_achievement": {"value": summary.get("pgi_achievement_rate", 0), "target": 100, "progress": 0, "icon": "fa-warehouse", "color": "success", "format": "percentage", "label": "PGI Achievement"},
            "pod_achievement": {"value": summary.get("pod_completion_rate", 0), "target": 95, "progress": 0, "icon": "fa-clipboard-check", "color": "warning", "format": "percentage", "label": "POD Achievement"},
        }

        executive = {
            "total_revenue": summary.get("total_revenue", 0),
            "total_units": summary.get("total_units", 0),
            "total_delivery_notes": summary.get("total_delivery_notes", 0),
            "active_dealers": summary.get("active_dealers", 0),
            "active_warehouses": summary.get("active_warehouses", 0),
            "active_cities": summary.get("active_cities", 0),
            "health_score": summary.get("dashboard_health_score", 0),
        }

        charts = {
            "revenue_trend": {"labels": monthly.get("months", []), "data": monthly.get("revenue", [])},
            "units_trend": {"labels": monthly.get("months", []), "data": monthly.get("units", [])},
            "dn_trend": {"labels": monthly.get("months", []), "data": monthly.get("delivery_notes", [])},
            "pgi_trend": {"labels": monthly.get("months", []), "data": monthly.get("pgi_rate", [])},
            "delivery_achievement_trend": {"labels": monthly.get("months", []), "data": monthly.get("delivery_achievement", [])},
            "pod_trend": {"labels": monthly.get("months", []), "data": monthly.get("pod_rate", [])},
            "daily_trend": {
                "labels": daily.get("dates", []),
                "revenue": daily.get("revenue", []),
                "units": daily.get("units", []),
                "delivery_notes": daily.get("delivery_notes", []),
                "pgi_completed": daily.get("pgi_completed", []),
                "delivered_dns": daily.get("delivered_dns", []),
                "pod_completed": daily.get("pod_completed", []),
            },
        }

        alerts = []
        if summary.get("pod_completion_rate", 100) < 90:
            alerts.append({"level": "warning", "message": "POD rate below 90%", "action": "Investigate POD collection", "title": "POD Alert"})

        return {
            "executive": executive,
            "cards": cards,
            "charts": charts,
            "warehouse": warehouse,
            "dealer": dealer,
            "product": product,
            "city": city,
            "transport": transport,
            "alerts": alerts,
            "recommendations": [],
            "metadata": metadata,
            "filters": filters,
        }

# ============================================================
# END OF FILE
# ============================================================
