# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 10.0 - ENTERPRISE SUPPLY CHAIN INTELLIGENCE PLATFORM (SEQUENTIAL ENGINE)
# ============================================================

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

# Optional enterprise libraries
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

logger = logging.getLogger(__name__)

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0

def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0

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
# 1. DATABASE REPOSITORY (Strictly Sequential to Prevent Cursor Closures)
# ============================================================

class DashboardRepository:
    def __init__(self):
        logger.info("🗄️ DashboardRepository initialized")

    def _execute(self, sql: str, params: Optional[Dict[str, Any]] = None):
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                return result
        except Exception as e:
            logger.exception(f"❌ SQL execution failed: {sql[:200]} | Error: {str(e)}")
            raise

    def fetch_raw_summary(self) -> Dict[str, Any]:
        sql = """
            SELECT
                COALESCE(SUM(dn_amount), 0) AS total_revenue,
                COALESCE(SUM(dn_qty), 0) AS total_units,
                COUNT(DISTINCT dn_no) AS total_dn,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL THEN (good_issue_date::date - dn_create_date::date) END), 0) AS avg_delivery_days,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_pod_days
            FROM delivery_reports
        """
        row = self._execute(sql).first()
        if not row:
            return {}
        
        dealers = self._execute("SELECT COUNT(DISTINCT dealer_code) FROM delivery_reports WHERE dealer_code IS NOT NULL").scalar() or 0
        warehouses = self._execute("SELECT COUNT(DISTINCT warehouse) FROM delivery_reports WHERE warehouse IS NOT NULL").scalar() or 0
        cities = self._execute("SELECT COUNT(DISTINCT ship_to_city) FROM delivery_reports WHERE ship_to_city IS NOT NULL").scalar() or 0
        products = self._execute("SELECT COUNT(DISTINCT material_no) FROM delivery_reports WHERE material_no IS NOT NULL").scalar() or 0

        return {
            "total_revenue": _safe_float(row.total_revenue),
            "total_units": _safe_int(row.total_units),
            "total_dn": _safe_int(row.total_dn),
            "pgi_completed": _safe_int(row.pgi_completed),
            "delivered_dns": _safe_int(row.delivered_dns),
            "pod_completed": _safe_int(row.pod_completed),
            "avg_cycle_days": _safe_float(row.avg_cycle_days),
            "avg_delivery_days": _safe_float(row.avg_delivery_days),
            "avg_pod_days": _safe_float(row.avg_pod_days),
            "dealers": dealers,
            "warehouses": warehouses,
            "cities": cities,
            "products": products
        }

    def fetch_raw_pipeline(self) -> Dict[str, Any]:
        sql = """
            SELECT
                COUNT(DISTINCT dn_no) AS total_dn,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_done,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_done,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery
            FROM delivery_reports
        """
        row = self._execute(sql).first()
        if not row:
            return {}
        return {
            "total_dn": _safe_int(row.total_dn),
            "pgi_done": _safe_int(row.pgi_done),
            "delivered": _safe_int(row.delivered),
            "pod_done": _safe_int(row.pod_done),
            "pending_pgi": _safe_int(row.pending_pgi),
            "pending_delivery": _safe_int(row.pending_delivery)
        }

    def fetch_raw_divisions(self) -> List[Any]:
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
        return self._execute(sql).fetchall()

    def fetch_raw_warehouses(self) -> List[Any]:
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
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL THEN (good_issue_date::date - dn_create_date::date) END), 0) AS avg_delivery_days,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_pod_days
            FROM delivery_reports
            WHERE warehouse IS NOT NULL
            GROUP BY warehouse
            ORDER BY revenue DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_dealers(self) -> List[Any]:
        sql = """
            SELECT
                dealer_code,
                customer_name AS dealer_name,
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days
            FROM delivery_reports
            WHERE dealer_code IS NOT NULL
            GROUP BY dealer_code, customer_name
            ORDER BY revenue DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_products(self) -> List[Any]:
        sql = """
            SELECT
                material_no AS sku,
                customer_model AS product_name,
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
            FROM delivery_reports
            WHERE material_no IS NOT NULL
            GROUP BY material_no, customer_model
            ORDER BY revenue DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_cities(self) -> List[Any]:
        sql = """
            SELECT
                ship_to_city AS city,
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days
            FROM delivery_reports
            WHERE ship_to_city IS NOT NULL
            GROUP BY ship_to_city
            ORDER BY revenue DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_monthly_trends(self) -> List[Any]:
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
        return self._execute(sql).fetchall()

    def fetch_raw_daily_trends(self) -> List[Any]:
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
        return self._execute(sql).fetchall()

    def fetch_raw_network_rows(self) -> List[Any]:
        sql = """
            SELECT warehouse, ship_to_city, dealer_code
            FROM delivery_reports
            WHERE warehouse IS NOT NULL AND ship_to_city IS NOT NULL AND dealer_code IS NOT NULL
            GROUP BY warehouse, ship_to_city, dealer_code
            LIMIT 1000
        """
        return self._execute(sql).fetchall()

    def fetch_record_count(self) -> int:
        return self._execute("SELECT COUNT(*) FROM delivery_reports").scalar() or 0

# ============================================================
# 2. BUSINESS RULE & KPI ENGINE
# ============================================================

class BusinessRuleEngine:
    @staticmethod
    def calculate_summary(raw: Dict[str, Any]) -> Dict[str, Any]:
        total_rev = raw.get("total_revenue", 0.0)
        total_units = raw.get("total_units", 0)
        total_dn = raw.get("total_dn", 0)
        pgi_completed = raw.get("pgi_completed", 0)
        delivered_dns = raw.get("delivered_dns", 0)
        pod_completed = raw.get("pod_completed", 0)
        avg_cycle = raw.get("avg_cycle_days", 0.0)
        avg_delivery = raw.get("avg_delivery_days", 0.0)
        avg_pod = raw.get("avg_pod_days", 0.0)

        pgi_rate = _pct(pgi_completed, total_dn)
        delivery_rate = _pct(delivered_dns, total_dn)
        pod_rate = _pct(pod_completed, delivered_dns if delivered_dns else 1)
        
        # Weighted Health Score: PGI 35%, Delivery 35%, POD 30%
        health = round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)

        return {
            "total_revenue": total_rev,
            "total_units": total_units,
            "total_delivery_notes": total_dn,
            "pgi_completed": pgi_completed,
            "delivered_dns": delivered_dns,
            "pod_completed": pod_completed,
            "active_dealers": raw.get("dealers", 0),
            "active_warehouses": raw.get("warehouses", 0),
            "active_cities": raw.get("cities", 0),
            "active_products": raw.get("products", 0),
            "active_transporters": 0,
            "average_delivery_days": avg_delivery,
            "average_pod_days": avg_pod,
            "average_logistics_cycle": avg_cycle,
            "pgi_achievement_rate": pgi_rate,
            "delivery_achievement_rate": delivery_rate,
            "pod_completion_rate": pod_rate,
            "otif_percentage": delivery_rate,
            "dashboard_health_score": health,
            "last_database_refresh": datetime.utcnow().isoformat()
        }

    @staticmethod
    def calculate_pipeline(raw: Dict[str, Any]) -> Dict[str, Any]:
        total_dn = raw.get("total_dn", 0)
        pgi_done = raw.get("pgi_done", 0)
        delivered = raw.get("delivered", 0)
        pod_done = raw.get("pod_done", 0)
        return {
            "dn_created": total_dn,
            "pgi_completed": pgi_done,
            "delivered": delivered,
            "pod_received": pod_done,
            "pgi_achievement": _pct(pgi_done, total_dn),
            "delivery_achievement": _pct(delivered, total_dn),
            "pod_achievement": _pct(pod_done, delivered if delivered else 1),
            "pending_pgi": raw.get("pending_pgi", 0),
            "pending_delivery": raw.get("pending_delivery", 0),
            "pending_pod": 0,
        }

# ============================================================
# 3. ANALYTICS ENGINE
# ============================================================

class AnalyticsEngine:
    @staticmethod
    def process_divisions(rows: List[Any]) -> List[Dict[str, Any]]:
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

    @staticmethod
    def process_warehouses(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            dn = _safe_int(row.delivery_notes)
            pgi = _safe_int(row.pgi_completed)
            delivered = _safe_int(row.delivered_dns)
            pod = _safe_int(row.pod_completed)
            pgi_rate = _pct(pgi, dn)
            delivery_rate = _pct(delivered, dn)
            pod_rate = _pct(pod, delivered if delivered else 1)
            health = round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)
            avg_cycle = _safe_float(row.avg_cycle_days)
            avg_delivery = _safe_float(row.avg_delivery_days)
            avg_pod = _safe_float(row.avg_pod_days)
            pending_pgi = _safe_int(row.pending_pgi)
            pending_delivery = _safe_int(row.pending_delivery)
            
            # Grades according to exact prompt rules
            if health >= 95: grade = "Outstanding"
            elif health >= 90: grade = "Excellent"
            elif health >= 85: grade = "Good"
            elif health >= 80: grade = "Needs Improvement"
            else: grade = "Critical"

            # Risk levels according to exact prompt rules
            if health >= 95: risk = "Very Low Risk"
            elif health >= 90: risk = "Low Risk"
            elif health >= 85: risk = "Medium Risk"
            elif health >= 80: risk = "High Risk"
            else: risk = "Critical Risk"
            
            # Recommendation logic
            rec = "Maintain dispatch standards."
            if health < 80:
                rec = "Critical warehouse delay. Immediate management intervention required."
            elif pod_rate < 85:
                rec = "Improve POD follow-up and document collection."
            elif pgi_rate < 90:
                rec = "Review PGI process and increase dispatch planning."
            else:
                rec = "Warehouse operating efficiently."

            result.append({
                "warehouse_name": row.warehouse_name,
                "revenue": _safe_float(row.revenue),
                "units": _safe_int(row.units),
                "delivery_notes": dn,
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "average_delivery_days": avg_delivery,
                "average_pod_days": avg_pod,
                "average_logistics_cycle": avg_cycle,
                "pending_pgi": pending_pgi,
                "pending_delivery": pending_delivery,
                "pending_pod": 0,
                "late_deliveries": 0,
                "health_score": health,
                "performance_grade": grade,
                "risk_level": risk,
                "ranking": 0,
                "ai_recommendation": rec
            })
        result.sort(key=lambda x: x["health_score"], reverse=True)
        for i, w in enumerate(result):
            w["ranking"] = i + 1
        return result

    @staticmethod
    def process_dealers(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            dn = _safe_int(row.delivery_notes)
            pgi = _safe_int(row.pgi_completed)
            delivered = _safe_int(row.delivered_dns)
            pod = _safe_int(row.pod_completed)
            pgi_rate = _pct(pgi, dn)
            delivery_rate = _pct(delivered, dn)
            pod_rate = _pct(pod, delivered if delivered else 1)
            health = round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)
            avg_cycle = _safe_float(row.avg_cycle_days)
            result.append({
                "dealer_name": row.dealer_name or row.dealer_code,
                "dealer_code": row.dealer_code,
                "revenue": _safe_float(row.revenue),
                "units": _safe_int(row.units),
                "delivery_notes": dn,
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "average_delivery_days": avg_cycle,
                "health_score": health,
                "ranking": 0,
            })
        result.sort(key=lambda x: x["health_score"], reverse=True)
        for i, d in enumerate(result):
            d["ranking"] = i + 1
        return result

    @staticmethod
    def process_products(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        total_rev = sum(_safe_float(row.revenue) for row in rows)
        for row in rows:
            revenue = _safe_float(row.revenue)
            units = _safe_int(row.units)
            dn = _safe_int(row.delivery_notes)
            pgi = _safe_int(row.pgi_completed)
            delivered = _safe_int(row.delivered_dns)
            pod = _safe_int(row.pod_completed)
            pgi_rate = _pct(pgi, dn)
            delivery_rate = _pct(delivered, dn)
            pod_rate = _pct(pod, delivered if delivered else 1)
            share = (revenue / total_rev * 100) if total_rev else 0
            
            abc = "A" if share >= 50 else "B" if share >= 20 else "C"
            
            result.append({
                "product_name": row.product_name or row.sku,
                "sku": row.sku,
                "revenue": revenue,
                "units": units,
                "delivery_notes": dn,
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "abc_class": abc,
                "revenue_share": round(share, 2),
                "slow_moving_flag": units < 50,
                "fast_moving_flag": units > 500,
                "dead_stock_flag": units == 0,
            })
        return result

    @staticmethod
    def process_cities(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            revenue = _safe_float(row.revenue)
            units = _safe_int(row.units)
            dn = _safe_int(row.delivery_notes)
            pgi = _safe_int(row.pgi_completed)
            delivered = _safe_int(row.delivered_dns)
            pod = _safe_int(row.pod_completed)
            pgi_rate = _pct(pgi, dn)
            delivery_rate = _pct(delivered, dn)
            pod_rate = _pct(pod, delivered if delivered else 1)
            health = round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)
            avg_cycle = _safe_float(row.avg_cycle_days)
            result.append({
                "city": row.city,
                "revenue": revenue,
                "units": units,
                "delivery_notes": dn,
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "average_delivery_days": avg_cycle,
                "health_score": health,
            })
        return result

    @staticmethod
    def process_monthly_trends(rows: List[Any]) -> Dict[str, List]:
        months, revenue, units, dn, pgi, delivery, pod = [], [], [], [], [], [], []
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

    @staticmethod
    def process_daily_trends(rows: List[Any]) -> Dict[str, List]:
        dates_list, revenue, units, dn, pgi, delivered_list, pod = [], [], [], [], [], [], []
        for row in rows:
            dates_list.append(row.date.strftime('%Y-%m-%d') if hasattr(row.date, 'strftime') else str(row.date))
            revenue.append(_safe_float(row.revenue))
            units.append(_safe_int(row.units))
            dn_val = _safe_int(row.dn)
            delivered_val = _safe_int(row.delivered_dns)
            dn.append(dn_val)
            pgi.append(_pct(_safe_int(row.pgi_completed), dn_val))
            delivered_list.append(_pct(delivered_val, dn_val))
            pod.append(_pct(_safe_int(row.pod_completed), delivered_val if delivered_val else 1))
        return {"dates": dates_list, "revenue": revenue, "units": units, "delivery_notes": dn,
                "pgi_rate": pgi, "delivery_achievement": delivered_list, "pod_rate": pod}

    @staticmethod
    def process_network(rows: List[Any]) -> Dict[str, Any]:
        if not NETWORKX_AVAILABLE:
            return {"nodes": [], "edges": []}
        G = nx.Graph()
        for row in rows:
            w, c, d = row.warehouse, row.ship_to_city, row.dealer_code
            G.add_node(w, type="warehouse")
            G.add_node(c, type="city")
            G.add_node(d, type="dealer")
            G.add_edge(w, c)
            G.add_edge(c, d)
        nodes = [{"id": n, "label": n, "type": G.nodes[n].get("type", "")} for n in G.nodes]
        edges = [{"from": u, "to": v} for u, v in G.edges]
        return {"nodes": nodes, "edges": edges}

# ============================================================
# 4. GRAPH ENGINE (Plotly)
# ============================================================

class GraphEngine:
    @staticmethod
    def get_warehouse_charts(warehouses: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not warehouses or not PLOTLY_AVAILABLE:
            return {}
        names = [w["warehouse_name"] for w in warehouses]
        revenues = [w["revenue"] for w in warehouses]
        delivery = [w["delivery_achievement_rate"] for w in warehouses]
        pod = [w["pod_completion_rate"] for w in warehouses]
        avg_cycle = [w["average_logistics_cycle"] for w in warehouses]
        
        charts = {}
        fig = px.bar(x=revenues, y=names, orientation='h', title="Warehouse Revenue Ranking")
        charts["revenue_ranking"] = fig.to_json()
        fig2 = px.bar(x=names, y=delivery, title="Warehouse Delivery Achievement")
        charts["delivery_achievement"] = fig2.to_json()
        fig3 = px.bar(x=names, y=pod, title="Warehouse POD Achievement")
        charts["pod_achievement"] = fig3.to_json()
        fig4 = px.bar(x=names, y=avg_cycle, title="Warehouse Average Logistics Cycle (days)")
        charts["avg_cycle"] = fig4.to_json()
        return charts

    @staticmethod
    def get_dealer_charts(dealers: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not dealers or not PLOTLY_AVAILABLE:
            return {}
        names = [d["dealer_name"] for d in dealers]
        revenues = [d["revenue"] for d in dealers]
        charts = {}
        fig = px.bar(x=revenues, y=names, orientation='h', title="Dealer Revenue Ranking")
        charts["revenue_ranking"] = fig.to_json()
        return charts

    @staticmethod
    def get_product_charts(products: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not products or not PLOTLY_AVAILABLE:
            return {}
        names = [p["product_name"] for p in products]
        revenues = [p["revenue"] for p in products]
        charts = {}
        fig = px.bar(x=names, y=revenues, title="Product Revenue")
        charts["revenue"] = fig.to_json()
        return charts

    @staticmethod
    def get_city_charts(cities: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not cities or not PLOTLY_AVAILABLE:
            return {}
        names = [c["city"] for c in cities]
        revenues = [c["revenue"] for c in cities]
        charts = {}
        fig = px.bar(x=names, y=revenues, title="City Revenue")
        charts["revenue"] = fig.to_json()
        return charts

# ============================================================
# 5. RECOMMENDATION & ALERT ENGINES
# ============================================================

class RecommendationEngine:
    @staticmethod
    def generate(warehouses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        recs = []
        for wh in warehouses:
            if wh.get("pod_completion_rate", 100) < 85:
                recs.append({
                    "entity": wh["warehouse_name"],
                    "type": "warehouse_pod",
                    "recommendation": f"{wh['warehouse_name']} POD rate {wh['pod_completion_rate']:.1f}%. Implement daily POD follow-up.",
                    "priority": "Critical" if wh["pod_completion_rate"] < 75 else "High",
                    "risk": "High" if wh["pod_completion_rate"] < 75 else "Medium"
                })
        return recs

class AlertEngine:
    @staticmethod
    def generate(summary: Dict[str, Any], warehouses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        alerts = []
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

# ============================================================
# 6. RESPONSE BUILDER & MASTER ORCHESTRATOR
# ============================================================

class ResponseBuilder:
    @staticmethod
    def build(
        summary, pipeline, division, warehouse, dealer, product, city,
        monthly, daily, network, alerts, recommendations, metadata, warehouse_charts,
        dealer_charts, product_charts, city_charts
    ) -> Dict[str, Any]:
        cards = {
            "revenue": {"value": summary.get("total_revenue", 0), "target": 150000000,
                        "icon": "fa-chart-line", "color": "primary", "format": "currency", "label": "Total Revenue"},
            "units": {"value": summary.get("total_units", 0), "target": 10000,
                      "icon": "fa-box", "color": "success", "format": "number", "label": "Total Units"},
            "delivery_notes": {"value": summary.get("total_delivery_notes", 0), "target": 5000,
                               "icon": "fa-file-invoice", "color": "info", "format": "number", "label": "Total Delivery Notes"},
            "pgi_achievement": {"value": summary.get("pgi_achievement_rate", 0), "target": 100,
                                "icon": "fa-warehouse", "color": "success", "format": "percentage", "label": "PGI Achievement %"},
            "delivery_achievement": {"value": summary.get("delivery_achievement_rate", 0), "target": 95,
                                   "icon": "fa-truck", "color": "warning", "format": "percentage", "label": "Delivery Achievement %"},
            "pod_achievement": {"value": summary.get("pod_completion_rate", 0), "target": 95,
                                "icon": "fa-clipboard-check", "color": "danger", "format": "percentage", "label": "POD Achievement %"},
            "avg_delivery_days": {"value": summary.get("average_delivery_days", 0), "target": 5,
                                  "icon": "fa-clock", "color": "info", "format": "days", "label": "Average Delivery Days"},
            "avg_pod_days": {"value": summary.get("average_pod_days", 0), "target": 3,
                             "icon": "fa-calendar-check", "color": "info", "format": "days", "label": "Average POD Days"},
            "avg_logistics_cycle": {"value": summary.get("average_logistics_cycle", 0), "target": 8,
                                    "icon": "fa-hourglass-half", "color": "primary", "format": "days", "label": "Average Logistics Cycle"},
            "health_score": {"value": summary.get("dashboard_health_score", 0), "target": 95,
                             "icon": "fa-heartbeat", "color": "primary", "format": "percentage", "label": "Dashboard Health Score"},
        }
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
        }

class DashboardService:
    def __init__(self):
        self._repo = DashboardRepository()
        logger.info("🚀 DashboardService v10.0 initialized with Sequential Execution Architecture")

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

        # Sequential DB execution to guarantee safety against psycopg2 cursor closing errors
        raw_sum = self._repo.fetch_raw_summary()
        raw_pipe = self._repo.fetch_raw_pipeline()
        raw_div = self._repo.fetch_raw_divisions()
        raw_wh = self._repo.fetch_raw_warehouses()
        raw_dl = self._repo.fetch_raw_dealers()
        raw_pr = self._repo.fetch_raw_products()
        raw_ct = self._repo.fetch_raw_cities()
        raw_mon = self._repo.fetch_raw_monthly_trends()
        raw_dai = self._repo.fetch_raw_daily_trends()
        raw_net = self._repo.fetch_raw_network_rows()
        record_count = self._repo.fetch_record_count()

        # Engine Executions
        summary = BusinessRuleEngine.calculate_summary(raw_sum)
        pipeline = BusinessRuleEngine.calculate_pipeline(raw_pipe)
        division = AnalyticsEngine.process_divisions(raw_div)
        warehouse = AnalyticsEngine.process_warehouses(raw_wh)
        dealer = AnalyticsEngine.process_dealers(raw_dl)
        product = AnalyticsEngine.process_products(raw_pr)
        city = AnalyticsEngine.process_cities(raw_ct)
        monthly = AnalyticsEngine.process_monthly_trends(raw_mon)
        daily = AnalyticsEngine.process_daily_trends(raw_dai)
        network = AnalyticsEngine.process_network(raw_net)

        alerts = AlertEngine.generate(summary, warehouse)
        recommendations = RecommendationEngine.generate(warehouse)

        warehouse_charts = GraphEngine.get_warehouse_charts(warehouse)
        dealer_charts = GraphEngine.get_dealer_charts(dealer)
        product_charts = GraphEngine.get_product_charts(product)
        city_charts = GraphEngine.get_city_charts(city)

        metadata = {
            "application_version": "10.0.0",
            "database_version": "PostgreSQL",
            "postgresql_status": "connected",
            "record_count": record_count,
            "last_refresh": datetime.utcnow().isoformat(),
            "environment": os.getenv("ENVIRONMENT", "production"),
        }

        return ResponseBuilder.build(
            summary, pipeline, division, warehouse, dealer, product, city,
            monthly, daily, network, alerts, recommendations, metadata,
            warehouse_charts, dealer_charts, product_charts, city_charts
        )

# ============================================================
# FASTAPI ROUTER
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
    data = await service.get_dashboard_data({})
    return data.get("pipeline", {})

@router.get("/division")
async def division(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("division", [])

@router.get("/warehouse")
async def warehouse(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("warehouse", [])

@router.get("/warehouse/charts")
async def warehouse_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("warehouse_charts", {})

@router.get("/dealer")
async def dealer(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("dealer", [])

@router.get("/dealer/charts")
async def dealer_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("dealer_charts", {})

@router.get("/product")
async def product(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("product", [])

@router.get("/product/charts")
async def product_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("product_charts", {})

@router.get("/city")
async def city(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("city", [])

@router.get("/city/charts")
async def city_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("city_charts", {})

@router.get("/trends/monthly")
async def monthly_trends(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("monthly_trends", {})

@router.get("/trends/daily")
async def daily_trends(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("daily_trends", {})

@router.get("/network")
async def network(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("network", {})

@router.get("/alerts")
async def alerts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("alerts", [])

@router.get("/recommendations")
async def recommendations(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("recommendations", [])

@router.get("/metadata")
async def metadata(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("metadata", {})
