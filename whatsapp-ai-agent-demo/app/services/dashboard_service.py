# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 8.1 - ENTERPRISE LOGISTICS INTELLIGENCE PLATFORM
# ============================================================
# All business logic, KPIs, and graph generation are in Python.
# HTML/JS only render the JSON response.
# ============================================================

import asyncio
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
from fastapi import APIRouter, Depends, Query, HTTPException

from app.database import engine
from app.models import DeliveryReport

# Optional enterprise libraries (lazy loaded)
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    from scipy import stats
    from scipy.signal import savgol_filter
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False

try:
    import plotly.graph_objects as go
    import plotly.express as px
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

try:
    import altair as alt
    ALTAIR_AVAILABLE = True
except ImportError:
    ALTAIR_AVAILABLE = False

try:
    from bokeh.plotting import figure
    from bokeh.embed import json_item
    BOKEH_AVAILABLE = True
except ImportError:
    BOKEH_AVAILABLE = False

try:
    from pyecharts.charts import Gauge, Funnel, TreeMap, Sunburst, Sankey
    from pyecharts import options as opts
    PYECHARTS_AVAILABLE = True
except ImportError:
    PYECHARTS_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _safe_float(value: Any) -> float:
    return float(value or 0)

def _safe_int(value: Any) -> int:
    return int(value or 0)

def _pct(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return round((numerator / denominator) * 100, 2)

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
# DASHBOARD REPOSITORY (all database queries)
# ============================================================

class DashboardRepository:
    def __init__(self):
        logger.info("🗄️ DashboardRepository v8.1 initialized")

    def _execute(self, sql: str, params: Optional[Dict[str, Any]] = None):
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                return result
        except Exception as e:
            logger.exception(f"❌ SQL execution failed: {sql[:200]}")
            raise

    # ------------------------------------------------------------------
    # EXECUTIVE SUMMARY
    # ------------------------------------------------------------------
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
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN pod_date - good_issue_date END), 0) AS avg_cycle_days
                FROM delivery_reports
            """
            row = self._execute(sql).first()
            if not row:
                return self._empty_summary()
            total_revenue = _safe_float(row.total_revenue)
            total_units = _safe_int(row.total_units)
            total_dn = _safe_int(row.total_dn)
            pgi_completed = _safe_int(row.pgi_completed)
            delivered_dns = _safe_int(row.delivered_dns)
            pod_completed = _safe_int(row.pod_completed)
            avg_cycle = _safe_float(row.avg_cycle_days)

            # Count distinct entities
            dealers = self._execute("SELECT COUNT(DISTINCT dealer_code) FROM delivery_reports WHERE dealer_code IS NOT NULL").scalar() or 0
            warehouses = self._execute("SELECT COUNT(DISTINCT warehouse) FROM delivery_reports WHERE warehouse IS NOT NULL").scalar() or 0
            cities = self._execute("SELECT COUNT(DISTINCT ship_to_city) FROM delivery_reports WHERE ship_to_city IS NOT NULL").scalar() or 0
            products = self._execute("SELECT COUNT(DISTINCT material_no) FROM delivery_reports WHERE material_no IS NOT NULL").scalar() or 0

            pgi_rate = _pct(pgi_completed, total_dn)
            delivery_rate = _pct(delivered_dns, total_dn)
            pod_rate = _pct(pod_completed, delivered_dns if delivered_dns else 1)
            health = round((pgi_rate + delivery_rate + pod_rate) / 3, 2)

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
                "average_delivery_days": avg_cycle,
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "otif_percentage": delivery_rate,
                "dashboard_health_score": health,
                "last_database_refresh": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.exception("❌ Summary failed")
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

    # ------------------------------------------------------------------
    # WAREHOUSE PERFORMANCE
    # ------------------------------------------------------------------
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
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN 0 END) AS pending_pod, -- placeholder
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN pod_date - good_issue_date END), 0) AS avg_cycle_days
                FROM delivery_reports
                WHERE warehouse IS NOT NULL
                GROUP BY warehouse
                ORDER BY revenue DESC
                LIMIT 10
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
                avg_cycle = _safe_float(row.avg_cycle_days)
                pending_pgi = _safe_int(row.pending_pgi)
                pending_delivery = _safe_int(row.pending_delivery)
                late_deliveries = 0  # not available yet
                grade = "A" if health >= 90 else "B" if health >= 75 else "C"
                risk = "Low" if health >= 85 else "Medium" if health >= 70 else "High"
                result.append({
                    "warehouse_name": row.warehouse_name,
                    "revenue": _safe_float(row.revenue),
                    "units": _safe_int(row.units),
                    "delivery_notes": dn,
                    "pgi_achievement_rate": pgi_rate,
                    "delivery_achievement_rate": delivery_rate,
                    "pod_completion_rate": pod_rate,
                    "average_delivery_days": avg_cycle,
                    "average_pod_days": 0.0,   # not yet
                    "average_logistics_cycle": avg_cycle,
                    "pending_pgi": pending_pgi,
                    "pending_pod": 0,
                    "late_deliveries": late_deliveries,
                    "health_score": health,
                    "performance_grade": grade,
                    "risk_level": risk,
                    "ranking": 0,
                    "ai_recommendation": self._warehouse_recommendation(row.warehouse_name, pgi_rate, delivery_rate, pod_rate, health)
                })
            # Sort by health
            result.sort(key=lambda x: x["health_score"], reverse=True)
            for i, w in enumerate(result):
                w["ranking"] = i + 1
            logger.info(f"✅ Warehouse: {len(result)} records")
            return result
        except Exception as e:
            logger.exception("❌ Warehouse failed")
            return []

    def _warehouse_recommendation(self, name: str, pgi: float, delivery: float, pod: float, health: float) -> str:
        if health >= 90:
            return "Excellent performance. Maintain standards."
        elif health >= 75:
            if pod < 90:
                return f"POD rate is {pod:.1f}%. Improve POD collection."
            if pgi < 95:
                return f"PGI rate is {pgi:.1f}%. Accelerate PGI processing."
            return "Good performance. Optimize for higher efficiency."
        else:
            if pod < 80:
                return "POD rate critical. Immediate escalation required."
            if pgi < 85:
                return "PGI gap high. Review warehouse dispatch process."
            return "Urgent review of all logistics processes."

    # ------------------------------------------------------------------
    # DEALER PERFORMANCE
    # ------------------------------------------------------------------
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
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN pod_date - good_issue_date END), 0) AS avg_cycle_days
                FROM delivery_reports
                WHERE dealer_code IS NOT NULL
                GROUP BY dealer_code, customer_name
                ORDER BY revenue DESC
                LIMIT 10
            """
            rows = self._execute(sql).fetchall()
            result = []
            for row in rows:
                dn = _safe_int(row.delivery_notes)
                pgi = _safe_int(row.pgi_completed)
                pod = _safe_int(row.pod_completed)
                pgi_rate = _pct(pgi, dn)
                pod_rate = _pct(pod, pgi if pgi else 1)
                health = round((pgi_rate + pod_rate) / 2, 2)
                avg_cycle = _safe_float(row.avg_cycle_days)
                result.append({
                    "dealer_name": row.dealer_name or row.dealer_code,
                    "dealer_code": row.dealer_code,
                    "revenue": _safe_float(row.revenue),
                    "units": _safe_int(row.units),
                    "delivery_notes": dn,
                    "pgi_achievement_rate": pgi_rate,
                    "pod_completion_rate": pod_rate,
                    "average_delivery_days": avg_cycle,
                    "health_score": health,
                    "ranking": 0,
                })
            # Sort by health
            result.sort(key=lambda x: x["health_score"], reverse=True)
            for i, d in enumerate(result):
                d["ranking"] = i + 1
            logger.info(f"✅ Dealer: {len(result)} records")
            return result
        except Exception as e:
            logger.exception("❌ Dealer failed")
            return []

    # ------------------------------------------------------------------
    # PRODUCT PERFORMANCE
    # ------------------------------------------------------------------
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
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
                FROM delivery_reports
                WHERE material_no IS NOT NULL
                GROUP BY material_no, customer_model
                ORDER BY revenue DESC
                LIMIT 10
            """
            rows = self._execute(sql).fetchall()
            result = []
            total_rev = sum(_safe_float(row.revenue) for row in rows)
            for row in rows:
                revenue = _safe_float(row.revenue)
                units = _safe_int(row.units)
                dn = _safe_int(row.delivery_notes)
                pgi = _safe_int(row.pgi_completed)
                pod = _safe_int(row.pod_completed)
                pgi_rate = _pct(pgi, dn)
                pod_rate = _pct(pod, pgi if pgi else 1)
                share = (revenue / total_rev * 100) if total_rev else 0
                abc = "A" if share > 40 else "B" if share > 20 else "C"
                result.append({
                    "product_name": row.product_name or row.sku,
                    "sku": row.sku,
                    "revenue": revenue,
                    "units": units,
                    "delivery_notes": dn,
                    "pgi_achievement_rate": pgi_rate,
                    "pod_completion_rate": pod_rate,
                    "abc_class": abc,
                    "revenue_share": round(share, 2),
                    "slow_moving_flag": units < 50,
                    "fast_moving_flag": units > 500,
                    "dead_stock_flag": units == 0,
                })
            logger.info(f"✅ Product: {len(result)} records")
            return result
        except Exception as e:
            logger.exception("❌ Product failed")
            return []

    # ------------------------------------------------------------------
    # CITY PERFORMANCE
    # ------------------------------------------------------------------
    def get_city_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        logger.info("🌆 Fetching CITY performance...")
        try:
            sql = """
                SELECT
                    ship_to_city AS city,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    COUNT(DISTINCT dn_no) AS delivery_notes,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN pod_date - good_issue_date END), 0) AS avg_cycle_days
                FROM delivery_reports
                WHERE ship_to_city IS NOT NULL
                GROUP BY ship_to_city
                ORDER BY revenue DESC
                LIMIT 10
            """
            rows = self._execute(sql).fetchall()
            result = []
            for row in rows:
                revenue = _safe_float(row.revenue)
                units = _safe_int(row.units)
                dn = _safe_int(row.delivery_notes)
                pgi = _safe_int(row.pgi_completed)
                pod = _safe_int(row.pod_completed)
                pgi_rate = _pct(pgi, dn)
                pod_rate = _pct(pod, pgi if pgi else 1)
                health = round((pgi_rate + pod_rate) / 2, 2)
                avg_cycle = _safe_float(row.avg_cycle_days)
                result.append({
                    "city": row.city,
                    "revenue": revenue,
                    "units": units,
                    "delivery_notes": dn,
                    "pgi_achievement_rate": pgi_rate,
                    "pod_completion_rate": pod_rate,
                    "average_delivery_days": avg_cycle,
                    "health_score": health,
                })
            logger.info(f"✅ City: {len(result)} records")
            return result
        except Exception as e:
            logger.exception("❌ City failed")
            return []

    # ------------------------------------------------------------------
    # TRANSPORT PERFORMANCE (no column – disabled)
    # ------------------------------------------------------------------
    def get_transport_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        logger.warning("⚠️ Transporter column missing – returning empty list")
        return []

    # ------------------------------------------------------------------
    # PIPELINE (DN → PGI → Delivery → POD)
    # ------------------------------------------------------------------
    def get_pipeline(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        try:
            sql = """
                SELECT
                    COUNT(DISTINCT dn_no) AS total_dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_done,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_done,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN 0 END) AS pending_pod
                FROM delivery_reports
            """
            row = self._execute(sql).first()
            if not row:
                return {}
            total_dn = _safe_int(row.total_dn)
            pgi_done = _safe_int(row.pgi_done)
            delivered = _safe_int(row.delivered)
            pod_done = _safe_int(row.pod_done)
            return {
                "dn_created": total_dn,
                "pgi_completed": pgi_done,
                "delivered": delivered,
                "pod_received": pod_done,
                "pgi_achievement": _pct(pgi_done, total_dn),
                "delivery_achievement": _pct(delivered, total_dn),
                "pod_achievement": _pct(pod_done, delivered if delivered else 1),
                "pending_pgi": _safe_int(row.pending_pgi),
                "pending_delivery": _safe_int(row.pending_delivery),
                "pending_pod": 0,
            }
        except Exception as e:
            logger.exception("❌ Pipeline failed")
            return {}

    # ------------------------------------------------------------------
    # DIVISION DASHBOARD
    # ------------------------------------------------------------------
    def get_division_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            sql = """
                SELECT
                    division,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    COUNT(DISTINCT dn_no) AS dn_qty,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_qty,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivery_qty,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS gap_qty,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NULL THEN dn_amount ELSE 0 END), 0) AS gap_amount
                FROM delivery_reports
                WHERE division IS NOT NULL
                GROUP BY division
                ORDER BY revenue DESC
            """
            rows = self._execute(sql).fetchall()
            result = []
            for row in rows:
                dn_qty = _safe_int(row.dn_qty)
                pgi_qty = _safe_int(row.pgi_qty)
                gap_qty = _safe_int(row.gap_qty)
                revenue = _safe_float(row.revenue)
                gap_amount = _safe_float(row.gap_amount)
                pgi_achievement = _pct(pgi_qty, dn_qty)
                gap_pct = _pct(gap_qty, dn_qty)
                variance = revenue - gap_amount
                variance_pct = _pct(variance, revenue) if revenue else 0
                result.append({
                    "division": row.division,
                    "revenue": revenue,
                    "units": _safe_int(row.units),
                    "dn_qty": dn_qty,
                    "pgi_qty": pgi_qty,
                    "delivery_qty": _safe_int(row.delivery_qty),
                    "gap_qty": gap_qty,
                    "gap_amount": gap_amount,
                    "pgi_achievement": pgi_achievement,
                    "gap_percentage": gap_pct,
                    "variance_percentage": variance_pct,
                })
            return result
        except Exception as e:
            logger.exception("❌ Division failed")
            return []

    # ------------------------------------------------------------------
    # MONTHLY & DAILY TRENDS (already implemented earlier)
    # ------------------------------------------------------------------
    def get_monthly_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
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
        return {"months": months, "revenue": revenue, "units": units, "delivery_notes": dn,
                "pgi_rate": pgi, "delivery_achievement": delivery, "pod_rate": pod}

    def get_daily_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
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
        return {"dates": dates, "revenue": revenue, "units": units, "delivery_notes": dn,
                "pgi_completed": pgi, "delivered_dns": delivered, "pod_completed": pod}

    # ------------------------------------------------------------------
    # NETWORK (Warehouse → City → Dealer)
    # ------------------------------------------------------------------
    def get_network_data(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        try:
            sql = """
                SELECT warehouse, ship_to_city, dealer_code
                FROM delivery_reports
                WHERE warehouse IS NOT NULL AND ship_to_city IS NOT NULL AND dealer_code IS NOT NULL
                GROUP BY warehouse, ship_to_city, dealer_code
                LIMIT 1000
            """
            rows = self._execute(sql).fetchall()
            if not NETWORKX_AVAILABLE:
                return {"nodes": [], "edges": []}
            G = nx.Graph()
            for row in rows:
                w = row.warehouse
                c = row.ship_to_city
                d = row.dealer_code
                G.add_node(w, type="warehouse")
                G.add_node(c, type="city")
                G.add_node(d, type="dealer")
                G.add_edge(w, c)
                G.add_edge(c, d)
            nodes = [{"id": n, "label": n, "type": G.nodes[n].get("type", "")} for n in G.nodes]
            edges = [{"from": u, "to": v} for u, v in G.edges]
            return {"nodes": nodes, "edges": edges}
        except Exception as e:
            logger.exception("❌ Network failed")
            return {"nodes": [], "edges": []}

    # ------------------------------------------------------------------
    # ALERTS (rule-based)
    # ------------------------------------------------------------------
    def get_alerts(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        alerts = []
        summary = self.get_summary(filters)
        warehouses = self.get_warehouse_performance(filters)
        if summary.get("pod_completion_rate", 100) < 90:
            alerts.append({"level": "warning", "title": "POD Alert",
                           "message": "Overall POD rate below 90%", "action": "Review POD collection"})
        for wh in warehouses:
            if wh.get("pod_completion_rate", 100) < 80:
                alerts.append({"level": "critical", "title": "Warehouse POD Alert",
                               "message": f"{wh['warehouse_name']} POD rate {wh['pod_completion_rate']:.1f}%",
                               "action": "Investigate warehouse delays"})
            if wh.get("health_score", 0) < 70:
                alerts.append({"level": "critical", "title": "Warehouse Health Alert",
                               "message": f"{wh['warehouse_name']} Health Score below 70", "action": "Escalate to ops"})
        return alerts

    # ------------------------------------------------------------------
    # AI RECOMMENDATIONS (dynamic)
    # ------------------------------------------------------------------
    def get_recommendations(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        recs = []
        warehouses = self.get_warehouse_performance(filters)
        for wh in warehouses:
            if wh.get("pod_completion_rate", 100) < 85:
                recs.append({
                    "entity": wh["warehouse_name"],
                    "type": "warehouse_pod",
                    "recommendation": f"{wh['warehouse_name']} POD rate {wh['pod_completion_rate']:.1f}%. Implement daily POD follow-up.",
                    "priority": "Critical" if wh["pod_completion_rate"] < 75 else "High",
                    "risk": "High" if wh["pod_completion_rate"] < 75 else "Medium"
                })
            if wh.get("health_score", 0) < 70:
                recs.append({
                    "entity": wh["warehouse_name"],
                    "type": "warehouse_health",
                    "recommendation": f"{wh['warehouse_name']} health score {wh['health_score']:.1f}. Immediate review required.",
                    "priority": "Critical",
                    "risk": "High"
                })
        # Add global recommendations
        summary = self.get_summary(filters)
        if summary.get("pod_completion_rate", 100) < 90:
            recs.append({
                "entity": "Global",
                "type": "pod",
                "recommendation": "Global POD rate is below 90%. Launch a company-wide POD improvement initiative.",
                "priority": "High",
                "risk": "Medium"
            })
        return recs

    # ------------------------------------------------------------------
    # METADATA
    # ------------------------------------------------------------------
    def get_metadata(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "application_version": "8.1.0",
            "database_version": "PostgreSQL",
            "postgresql_status": "connected",
            "record_count": self._execute("SELECT COUNT(*) FROM delivery_reports").scalar() or 0,
            "last_refresh": datetime.utcnow().isoformat(),
            "environment": os.getenv("ENVIRONMENT", "production"),
        }

    # ------------------------------------------------------------------
    # WAREHOUSE CHARTS (Plotly JSON)
    # ------------------------------------------------------------------
    def get_warehouse_charts(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        warehouses = self.get_warehouse_performance(filters)
        if not warehouses:
            return {}
        names = [w["warehouse_name"] for w in warehouses]
        revenues = [w["revenue"] for w in warehouses]
        pgi = [w["pgi_achievement_rate"] for w in warehouses]
        delivery = [w["delivery_achievement_rate"] for w in warehouses]
        pod = [w["pod_completion_rate"] for w in warehouses]
        health = [w["health_score"] for w in warehouses]
        avg_cycle = [w["average_logistics_cycle"] for w in warehouses]
        charts = {}
        if PLOTLY_AVAILABLE:
            # 1. Revenue Ranking
            fig = px.bar(x=revenues, y=names, orientation='h', title="Warehouse Revenue Ranking")
            charts["revenue_ranking"] = fig.to_json()
            # 2. Delivery Achievement
            fig2 = px.bar(x=names, y=delivery, title="Warehouse Delivery Achievement")
            charts["delivery_achievement"] = fig2.to_json()
            # 3. POD Achievement
            fig3 = px.bar(x=names, y=pod, title="Warehouse POD Achievement")
            charts["pod_achievement"] = fig3.to_json()
            # 4. Average Delivery Days
            fig4 = px.bar(x=names, y=avg_cycle, title="Warehouse Average Logistics Cycle (days)")
            charts["avg_cycle"] = fig4.to_json()
            # 5. Health Gauge (first warehouse)
            if warehouses:
                fig5 = go.Figure(go.Indicator(mode="gauge+number", value=health[0],
                                              title={"text": f"Health - {names[0]}"},
                                              gauge={"axis": {"range": [0, 100]}}))
                charts["health_gauge"] = fig5.to_json()
            # 6. Radar Chart (multi‑warehouse)
            radar_fig = go.Figure()
            for i, w in enumerate(warehouses):
                radar_fig.add_trace(go.Scatterpolar(
                    r=[pgi[i], delivery[i], pod[i], health[i]],
                    theta=['PGI', 'Delivery', 'POD', 'Health'],
                    fill='toself', name=w["warehouse_name"]
                ))
            radar_fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])))
            charts["radar"] = radar_fig.to_json()
        return charts

    # ------------------------------------------------------------------
    # DEALER CHARTS
    # ------------------------------------------------------------------
    def get_dealer_charts(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        dealers = self.get_dealer_performance(filters)
        if not dealers:
            return {}
        names = [d["dealer_name"] for d in dealers]
        revenues = [d["revenue"] for d in dealers]
        pod = [d["pod_completion_rate"] for d in dealers]
        charts = {}
        if PLOTLY_AVAILABLE:
            fig = px.bar(x=revenues, y=names, orientation='h', title="Dealer Revenue Ranking")
            charts["revenue_ranking"] = fig.to_json()
            fig2 = px.scatter(x=pod, y=revenues, text=names, title="Dealer POD vs Revenue")
            charts["pod_scatter"] = fig2.to_json()
            # Treemap for dealer contribution
            fig3 = px.treemap(names=names, values=revenues, title="Dealer Revenue Contribution")
            charts["treemap"] = fig3.to_json()
        return charts

    # ------------------------------------------------------------------
    # PRODUCT CHARTS
    # ------------------------------------------------------------------
    def get_product_charts(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        products = self.get_product_performance(filters)
        if not products:
            return {}
        names = [p["product_name"] for p in products]
        revenues = [p["revenue"] for p in products]
        units = [p["units"] for p in products]
        charts = {}
        if PLOTLY_AVAILABLE:
            fig = px.treemap(names=names, values=revenues, title="Product Revenue Treemap")
            charts["treemap"] = fig.to_json()
            fig2 = px.sunburst(names=names, values=revenues, title="Product Revenue Sunburst")
            charts["sunburst"] = fig2.to_json()
            # Bubble chart
            fig3 = px.scatter(x=units, y=revenues, text=names, size=revenues, title="Product Units vs Revenue")
            charts["bubble"] = fig3.to_json()
        return charts

    # ------------------------------------------------------------------
    # CITY CHARTS
    # ------------------------------------------------------------------
    def get_city_charts(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        cities = self.get_city_performance(filters)
        if not cities:
            return {}
        names = [c["city"] for c in cities]
        revenues = [c["revenue"] for c in cities]
        pod = [c["pod_completion_rate"] for c in cities]
        charts = {}
        if PLOTLY_AVAILABLE:
            fig = px.bar(x=names, y=revenues, title="City Revenue")
            charts["revenue"] = fig.to_json()
            fig2 = px.bar(x=names, y=pod, title="City POD Achievement")
            charts["pod"] = fig2.to_json()
            # Bubble chart
            fig3 = px.scatter(x=pod, y=revenues, text=names, title="City POD vs Revenue")
            charts["bubble"] = fig3.to_json()
        return charts

# ============================================================
# DASHBOARD SERVICE (Orchestrator)
# ============================================================

class DashboardService:
    def __init__(self):
        self._repository = DashboardRepository()
        logger.info("🚀 DashboardService v8.1 initialized")

    @cached(ttl=5)
    async def get_dashboard_data(
        self,
        filters: Optional[Dict[str, Any]] = None,
        role: str = "viewer",
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        filters = filters or {}
        logger.info(f"📡 Master Dashboard API called with filters: {filters}")

        # Load all sections in parallel
        summary = await asyncio.to_thread(self._repository.get_summary, filters)
        warehouse = await asyncio.to_thread(self._repository.get_warehouse_performance, filters)
        dealer = await asyncio.to_thread(self._repository.get_dealer_performance, filters)
        product = await asyncio.to_thread(self._repository.get_product_performance, filters)
        city = await asyncio.to_thread(self._repository.get_city_performance, filters)
        transport = await asyncio.to_thread(self._repository.get_transport_performance, filters)
        monthly = await asyncio.to_thread(self._repository.get_monthly_trends, filters)
        daily = await asyncio.to_thread(self._repository.get_daily_trends, filters)
        pipeline = await asyncio.to_thread(self._repository.get_pipeline, filters)
        division = await asyncio.to_thread(self._repository.get_division_performance, filters)
        network = await asyncio.to_thread(self._repository.get_network_data, filters)
        alerts = await asyncio.to_thread(self._repository.get_alerts, filters)
        recommendations = await asyncio.to_thread(self._repository.get_recommendations, filters)
        metadata = await asyncio.to_thread(self._repository.get_metadata, filters)
        warehouse_charts = await asyncio.to_thread(self._repository.get_warehouse_charts, filters)
        dealer_charts = await asyncio.to_thread(self._repository.get_dealer_charts, filters)
        product_charts = await asyncio.to_thread(self._repository.get_product_charts, filters)
        city_charts = await asyncio.to_thread(self._repository.get_city_charts, filters)

        # Build cards
        cards = {
            "revenue": {"value": summary.get("total_revenue", 0), "target": 150000000, "icon": "fa-chart-line", "color": "primary", "format": "currency", "label": "Revenue"},
            "units": {"value": summary.get("total_units", 0), "target": 10000, "icon": "fa-box", "color": "success", "format": "number", "label": "Units"},
            "delivery_notes": {"value": summary.get("total_delivery_notes", 0), "target": 5000, "icon": "fa-file-invoice", "color": "info", "format": "number", "label": "Delivery Notes"},
            "pgi_achievement": {"value": summary.get("pgi_achievement_rate", 0), "target": 100, "icon": "fa-warehouse", "color": "success", "format": "percentage", "label": "PGI Achievement"},
            "delivery_achievement": {"value": summary.get("delivery_achievement_rate", 0), "target": 95, "icon": "fa-truck", "color": "warning", "format": "percentage", "label": "Delivery Achievement"},
            "pod_achievement": {"value": summary.get("pod_completion_rate", 0), "target": 95, "icon": "fa-clipboard-check", "color": "danger", "format": "percentage", "label": "POD Achievement"},
            "avg_delivery_days": {"value": summary.get("average_delivery_days", 0), "target": 5, "icon": "fa-clock", "color": "info", "format": "days", "label": "Avg Delivery Days"},
            "health_score": {"value": summary.get("dashboard_health_score", 0), "target": 95, "icon": "fa-heartbeat", "color": "primary", "format": "percentage", "label": "Health Score"},
        }
        # Add progress
        for key, card in cards.items():
            card["progress"] = min((card["value"] / card["target"]) * 100, 100) if card["target"] else 0

        executive = {
            "total_revenue": summary.get("total_revenue", 0),
            "total_units": summary.get("total_units", 0),
            "total_delivery_notes": summary.get("total_delivery_notes", 0),
            "active_dealers": summary.get("active_dealers", 0),
            "active_warehouses": summary.get("active_warehouses", 0),
            "active_cities": summary.get("active_cities", 0),
            "pgi_achievement": summary.get("pgi_achievement_rate", 0),
            "delivery_achievement": summary.get("delivery_achievement_rate", 0),
            "pod_achievement": summary.get("pod_completion_rate", 0),
            "health_score": summary.get("dashboard_health_score", 0),
        }

        return {
            "executive": executive,
            "cards": cards,
            "pipeline": pipeline,
            "division": division,
            "warehouse": warehouse,
            "warehouse_charts": warehouse_charts,
            "dealer": dealer,
            "dealer_charts": dealer_charts,
            "product": product,
            "product_charts": product_charts,
            "city": city,
            "city_charts": city_charts,
            "monthly_trends": monthly,
            "daily_trends": daily,
            "network": network,
            "alerts": alerts,
            "recommendations": recommendations,
            "metadata": metadata,
            "filters": filters,
        }

# ============================================================
# FASTAPI ROUTER (placed after class definition)
# ============================================================

_dashboard_service = None

def get_dashboard_service() -> DashboardService:
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService()
    return _dashboard_service

router = APIRouter(prefix="/dashboard/api", tags=["dashboard"])

@router.get("/data")
async def dashboard_api_data(
    service: DashboardService = Depends(get_dashboard_service),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    warehouse: Optional[str] = Query(None),
    dealer: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    division: Optional[str] = Query(None),
):
    filters = {k: v for k, v in locals().items() if k not in ['service', 'filters'] and v is not None}
    return await service.get_dashboard_data(filters)

@router.get("/executive")
async def executive_summary(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return {"executive": data.get("executive")}

@router.get("/pipeline")
async def pipeline(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_pipeline({})

@router.get("/division")
async def division(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_division_performance({})

@router.get("/warehouse")
async def warehouse(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_warehouse_performance({})

@router.get("/warehouse/charts")
async def warehouse_charts(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_warehouse_charts({})

@router.get("/dealer")
async def dealer(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_dealer_performance({})

@router.get("/dealer/charts")
async def dealer_charts(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_dealer_charts({})

@router.get("/product")
async def product(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_product_performance({})

@router.get("/product/charts")
async def product_charts(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_product_charts({})

@router.get("/city")
async def city(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_city_performance({})

@router.get("/city/charts")
async def city_charts(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_city_charts({})

@router.get("/trends/monthly")
async def monthly_trends(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_monthly_trends({})

@router.get("/trends/daily")
async def daily_trends(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_daily_trends({})

@router.get("/network")
async def network(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_network_data({})

@router.get("/alerts")
async def alerts(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_alerts({})

@router.get("/recommendations")
async def recommendations(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_recommendations({})

@router.get("/metadata")
async def metadata(service: DashboardService = Depends(get_dashboard_service)):
    return service._repository.get_metadata({})

# ============================================================
# END OF FILE
# ============================================================
