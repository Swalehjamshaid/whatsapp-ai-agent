# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 11.6 - ENTERPRISE SUPPLY CHAIN PLATFORM (PYTHON-SIDE DISTANCE CALC)
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
from app.services.geo_service import GeoService

# Optional enterprise libraries
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    import plotly.graph_objects as go
    import plotly.express as px
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

# Optional Geopy for real-world distance calculation if needed
try:
    from geopy.distance import geodesic
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

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

# ============================================================
# PYTHON DISTANCE CALCULATOR
# ============================================================

class PythonDistanceCalculator:
    """
    Handles all distance calculations purely in Python to avoid Postgres column errors.
    """
    @staticmethod
    def get_distance_km(warehouse: str, city: str) -> float:
        """
        Replace this placeholder logic with actual geopy coordinate mapping 
        or a dictionary of known distances between warehouses and cities.
        """
        # Example hardcoded mapping (can be expanded or replaced with geopy)
        distance_map = {
            ("Lahore WH", "Karachi"): 1200.0,
            ("Lahore WH", "Islamabad"): 380.0,
            ("Karachi WH", "Hyderabad"): 160.0
        }
        
        # Fallback heuristic if pair not found in mapping
        return distance_map.get((warehouse, city), 350.0) # Defaulting to 350km for safety

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
# 1. DATABASE REPOSITORY
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
            logger.error(f"❌ SQL execution failed: {str(e)}")
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
        if not row: return {}
        
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
            "dealers": dealers, "warehouses": warehouses, "cities": cities, "products": products
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
        if not row: return {}
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
            FROM delivery_reports WHERE division IS NOT NULL GROUP BY division ORDER BY revenue DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_warehouses_for_python(self) -> List[Any]:
        """
        Retrieves granular data necessary for Python to calculate distances locally.
        We group by warehouse, city, and days taken to keep payload light but accurate.
        """
        sql = """
            SELECT
                warehouse AS warehouse_name,
                ship_to_city,
                (good_issue_date::date - dn_create_date::date) AS pgi_days,
                (pod_date::date - good_issue_date::date) AS pod_days,
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery
            FROM delivery_reports
            WHERE warehouse IS NOT NULL
            GROUP BY warehouse, ship_to_city, pgi_days, pod_days
        """
        return self._execute(sql).fetchall()

    def fetch_raw_dealers(self) -> List[Any]:
        sql = """
            SELECT
                dealer_code, customer_name AS dealer_name,
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days
            FROM delivery_reports WHERE dealer_code IS NOT NULL GROUP BY dealer_code, customer_name ORDER BY revenue DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_products(self) -> List[Any]:
        sql = """
            SELECT
                material_no AS sku, customer_model AS product_name,
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
            FROM delivery_reports WHERE material_no IS NOT NULL GROUP BY material_no, customer_model ORDER BY revenue DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_cities(self) -> List[Any]:
        sql = """
            SELECT
                ship_to_city AS city, COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days
            FROM delivery_reports WHERE ship_to_city IS NOT NULL GROUP BY ship_to_city ORDER BY revenue DESC
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
            FROM delivery_reports WHERE dn_create_date IS NOT NULL GROUP BY month ORDER BY month
        """
        return self._execute(sql).fetchall()

    def fetch_raw_daily_trends(self) -> List[Any]:
        sql = """
            SELECT
                dn_create_date AS date, COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed
            FROM delivery_reports WHERE dn_create_date >= CURRENT_DATE - INTERVAL '30 days' GROUP BY dn_create_date ORDER BY dn_create_date
        """
        return self._execute(sql).fetchall()

    def fetch_raw_network_rows(self) -> List[Any]:
        sql = """
            SELECT warehouse, ship_to_city, dealer_code FROM delivery_reports
            WHERE warehouse IS NOT NULL AND ship_to_city IS NOT NULL AND dealer_code IS NOT NULL GROUP BY warehouse, ship_to_city, dealer_code LIMIT 1000
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
        total_dn = raw.get("total_dn", 0)
        pgi_completed = raw.get("pgi_completed", 0)
        delivered_dns = raw.get("delivered_dns", 0)
        pod_completed = raw.get("pod_completed", 0)

        pgi_rate = _pct(pgi_completed, total_dn)
        delivery_rate = _pct(delivered_dns, total_dn)
        pod_rate = _pct(pod_completed, delivered_dns if delivered_dns else 1)
        health = round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)

        return {
            "total_revenue": total_rev,
            "total_units": raw.get("total_units", 0),
            "total_delivery_notes": total_dn,
            "pgi_completed": pgi_completed,
            "delivered_dns": delivered_dns,
            "pod_completed": pod_completed,
            "active_dealers": raw.get("dealers", 0),
            "active_warehouses": raw.get("warehouses", 0),
            "active_cities": raw.get("cities", 0),
            "active_products": raw.get("products", 0),
            "average_delivery_days": raw.get("avg_delivery_days", 0.0),
            "average_pod_days": raw.get("avg_pod_days", 0.0),
            "average_logistics_cycle": raw.get("avg_cycle_days", 0.0),
            "pgi_achievement_rate": pgi_rate,
            "delivery_achievement_rate": delivery_rate,
            "pod_completion_rate": pod_rate,
            "dashboard_health_score": health,
        }

    @staticmethod
    def calculate_pipeline(raw: Dict[str, Any]) -> Dict[str, Any]:
        total_dn = raw.get("total_dn", 0)
        delivered = raw.get("delivered", 0)
        return {
            "dn_created": total_dn,
            "pgi_completed": raw.get("pgi_done", 0),
            "delivered": delivered,
            "pod_received": raw.get("pod_done", 0),
            "pgi_achievement": _pct(raw.get("pgi_done", 0), total_dn),
            "delivery_achievement": _pct(delivered, total_dn),
            "pod_achievement": _pct(raw.get("pod_done", 0), delivered if delivered else 1),
            "pending_pgi": raw.get("pending_pgi", 0),
            "pending_delivery": raw.get("pending_delivery", 0),
        }

# ============================================================
# 3. ANALYTICS ENGINE (PYTHON-BASED DISTANCE TIERING)
# ============================================================

class AnalyticsEngine:
    @staticmethod
    def process_divisions(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "division": row.division,
                "revenue": _safe_float(row.revenue),
                "units": _safe_int(row.units),
                "dn_qty": _safe_int(row.dn_qty),
                "pgi_qty": _safe_int(row.pgi_qty),
                "gap_qty": _safe_int(row.gap_qty),
                "pgi_achievement": _pct(_safe_int(row.pgi_qty), _safe_int(row.dn_qty)),
            })
        return result

    @staticmethod
    def process_warehouses(rows: List[Any]) -> List[Dict[str, Any]]:
        """
        Aggregates granular warehouse/city rows and calculates distance metrics locally in Python.
        """
        wh_stats = defaultdict(lambda: {
            "revenue": 0.0, "units": 0, "delivery_notes": 0,
            "pgi_completed": 0, "delivered_dns": 0, "pod_completed": 0,
            "pending_pgi": 0, "pending_delivery": 0,
            "on_time_pgis": 0, "late_pgis": 0, "on_time_pods": 0, "late_pods": 0,
            "sum_pgi_days": 0.0, "sum_pod_days": 0.0, "sum_cycle_days": 0.0,
            "distances": []
        })

        # Python-side processing
        for row in rows:
            w_name = row.warehouse_name
            city = row.ship_to_city
            
            dist = PythonDistanceCalculator.get_distance_km(w_name, city)
            
            st = wh_stats[w_name]
            st["revenue"] += _safe_float(row.revenue)
            st["units"] += _safe_int(row.units)
            st["delivery_notes"] += _safe_int(row.delivery_notes)
            st["pgi_completed"] += _safe_int(row.pgi_completed)
            st["delivered_dns"] += _safe_int(row.delivered_dns)
            st["pod_completed"] += _safe_int(row.delivered_dns) # Evaluated at city level
            st["pending_pgi"] += _safe_int(row.pending_pgi)
            st["pending_delivery"] += _safe_int(row.pending_delivery)
            st["distances"].append(dist)
            
            pgi_days = row.pgi_days
            pod_days = row.pod_days
            pgi_qty = _safe_int(row.pgi_completed)
            pod_qty = _safe_int(row.delivered_dns)
            
            # PGI Distance Matrix Evaluation
            if pgi_days is not None and pgi_qty > 0:
                thresh = 1 if dist <= 250 else 2 if dist <= 450 else 3 if dist <= 700 else 4 if dist <= 900 else 5
                if pgi_days <= thresh: st["on_time_pgis"] += pgi_qty
                else: st["late_pgis"] += pgi_qty
                st["sum_pgi_days"] += (pgi_days * pgi_qty)
                
            # POD Distance Matrix Evaluation
            if pod_days is not None and pod_qty > 0:
                thresh = 1 if dist <= 100 else 2 if dist <= 250 else 3 if dist <= 450 else 4 if dist <= 700 else 5 if dist <= 900 else 6
                if pod_days <= thresh: st["on_time_pods"] += pod_qty
                else: st["late_pods"] += pod_qty
                st["sum_pod_days"] += (pod_days * pod_qty)
                st["sum_cycle_days"] += (((pgi_days or 0) + pod_days) * pod_qty)

        # Final Formatting
        result = []
        for w_name, data in wh_stats.items():
            dn = data["delivery_notes"]
            delivered = data["delivered_dns"]
            
            pgi_rate = _pct(data["on_time_pgis"], dn)
            pod_rate = _pct(data["on_time_pods"], delivered if delivered else 1)
            delivery_rate = _pct(delivered, dn)
            health = round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)
            
            avg_dist = (sum(data["distances"]) / len(data["distances"])) if data["distances"] else 0
            act_pgi = (data["sum_pgi_days"] / data["pgi_completed"]) if data["pgi_completed"] else 0
            act_pod = (data["sum_pod_days"] / delivered) if delivered else 0
            act_cycle = (data["sum_cycle_days"] / delivered) if delivered else 0

            result.append({
                "warehouse_name": w_name,
                "revenue": data["revenue"],
                "units": data["units"],
                "delivery_notes": dn,
                "average_distance": round(avg_dist, 1),
                "actual_pgi_days": round(act_pgi, 1),
                "actual_pod_days": round(act_pod, 1),
                "average_logistics_cycle": round(act_cycle, 1),
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "on_time_pgis": data["on_time_pgis"],
                "late_pgis": data["late_pgis"],
                "on_time_pods": data["on_time_pods"],
                "late_pods": data["late_pods"],
                "pending_pgi": data["pending_pgi"],
                "pending_delivery": data["pending_delivery"],
                "health_score": health,
            })
            
        result.sort(key=lambda x: x["health_score"], reverse=True)
        for i, w in enumerate(result): w["ranking"] = i + 1
        return result

    @staticmethod
    def process_dealers(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "dealer_name": row.dealer_name or row.dealer_code,
                "revenue": _safe_float(row.revenue),
                "units": _safe_int(row.units),
                "pgi_achievement_rate": _pct(_safe_int(row.pgi_completed), _safe_int(row.delivery_notes)),
            })
        return result

    @staticmethod
    def process_products(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "product_name": row.product_name or row.sku,
                "revenue": _safe_float(row.revenue),
                "units": _safe_int(row.units),
            })
        return result

    @staticmethod
    def process_cities(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "city": row.city,
                "revenue": _safe_float(row.revenue),
                "units": _safe_int(row.units),
            })
        return result

    @staticmethod
    def process_monthly_trends(rows: List[Any]) -> Dict[str, List]:
        months, revenue, units = [], [], []
        for row in rows:
            months.append(row.month)
            revenue.append(_safe_float(row.revenue))
            units.append(_safe_int(row.units))
        return {"months": months, "revenue": revenue, "units": units}

    @staticmethod
    def process_daily_trends(rows: List[Any]) -> Dict[str, List]:
        dates_list, revenue, units = [], [], []
        for row in rows:
            dates_list.append(row.date.strftime('%Y-%m-%d') if hasattr(row.date, 'strftime') else str(row.date))
            revenue.append(_safe_float(row.revenue))
            units.append(_safe_int(row.units))
        return {"dates": dates_list, "revenue": revenue, "units": units}

    @staticmethod
    def process_network(rows: List[Any]) -> Dict[str, Any]:
        return {"nodes": [], "edges": []}

# ============================================================
# 4. GRAPH ENGINE (Plotly)
# ============================================================

class GraphEngine:
    @staticmethod
    def get_warehouse_charts(warehouses: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not warehouses or not PLOTLY_AVAILABLE: return {}
        names = [w["warehouse_name"] for w in warehouses]
        revenues = [w["revenue"] for w in warehouses]
        charts = {}
        fig = px.bar(x=revenues, y=names, orientation='h', title="Warehouse Revenue Ranking")
        charts["revenue_ranking"] = fig.to_json()
        return charts

    @staticmethod
    def get_dealer_charts(dealers: List[Dict[str, Any]]) -> Dict[str, Any]: return {}
    @staticmethod
    def get_product_charts(products: List[Dict[str, Any]]) -> Dict[str, Any]: return {}
    @staticmethod
    def get_city_charts(cities: List[Dict[str, Any]]) -> Dict[str, Any]: return {}

# ============================================================
# 5. RECOMMENDATION & ALERT ENGINES
# ============================================================

class RecommendationEngine:
    @staticmethod
    def generate(warehouses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return []

class AlertEngine:
    @staticmethod
    def generate(summary: Dict[str, Any], warehouses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return []

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
            "revenue": {"value": summary.get("total_revenue", 0), "target": 150000000, "icon": "fa-chart-line"},
            "health_score": {"value": summary.get("dashboard_health_score", 0), "target": 95, "icon": "fa-heartbeat"},
        }
        
        return {
            "executive": summary,
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
        logger.info("🚀 DashboardService v11.6 initialized with Python Distance Tiering")

    @cached(ttl=5)
    async def get_dashboard_data(
        self,
        filters: Optional[Dict[str, Any]] = None,
        role: str = "viewer",
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        filters = filters or {}

        try:
            raw_sum = self._repo.fetch_raw_summary()
            raw_pipe = self._repo.fetch_raw_pipeline()
            raw_div = self._repo.fetch_raw_divisions()
            raw_wh = self._repo.fetch_raw_warehouses_for_python() # Using the new unaggregated raw Python pull
            raw_dl = self._repo.fetch_raw_dealers()
            raw_pr = self._repo.fetch_raw_products()
            raw_ct = self._repo.fetch_raw_cities()
            raw_mon = self._repo.fetch_raw_monthly_trends()
            raw_dai = self._repo.fetch_raw_daily_trends()
            raw_net = self._repo.fetch_raw_network_rows()
            record_count = self._repo.fetch_record_count()
        except Exception as e:
            logger.error(f"❌ Database execution error caught: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database execution error: {str(e)}")

        summary = BusinessRuleEngine.calculate_summary(raw_sum)
        pipeline = BusinessRuleEngine.calculate_pipeline(raw_pipe)
        division = AnalyticsEngine.process_divisions(raw_div)
        warehouse = AnalyticsEngine.process_warehouses(raw_wh) # Heavy-lifting now handled in-memory
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
            "application_version": "11.6.0",
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
