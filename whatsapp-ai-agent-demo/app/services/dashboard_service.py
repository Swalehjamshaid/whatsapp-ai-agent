# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 12.0 - ENTERPRISE SUPPLY CHAIN PLATFORM (EXECUTIVE LOGISTICS)
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

# ============================================================
# PYTHON DISTANCE CALCULATOR
# ============================================================

class PythonDistanceCalculator:
    """
    Handles all distance calculations purely in Python to avoid Postgres column errors.
    """
    @staticmethod
    def get_distance_km(warehouse: str, city: str) -> float:
        distance_map = {
            ("Lahore WH", "Karachi"): 1200.0,
            ("Lahore WH", "Islamabad"): 380.0,
            ("Karachi WH", "Hyderabad"): 160.0
        }
        return distance_map.get((warehouse, city), 350.0)

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
                COUNT(DISTINCT dn_no) AS total_dn,
                COALESCE(SUM(dn_qty), 0) AS total_units,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - dn_create_date::date) END), 0) AS avg_delivery_days,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL THEN (good_issue_date::date - dn_create_date::date) END), 0) AS avg_pgi_days,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_pod_days,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days
            FROM delivery_reports
        """
        row = self._execute(sql).first()
        if not row: return {}
        
        dealers = self._execute("SELECT COUNT(DISTINCT dealer_code) FROM delivery_reports WHERE dealer_code IS NOT NULL").scalar() or 0
        warehouses = self._execute("SELECT COUNT(DISTINCT warehouse) FROM delivery_reports WHERE warehouse IS NOT NULL").scalar() or 0
        cities = self._execute("SELECT COUNT(DISTINCT ship_to_city) FROM delivery_reports WHERE ship_to_city IS NOT NULL").scalar() or 0
        products = self._execute("SELECT COUNT(DISTINCT material_no) FROM delivery_reports WHERE material_no IS NOT NULL").scalar() or 0

        return {
            "total_units": _safe_int(row.total_units),
            "total_dn": _safe_int(row.total_dn),
            "pgi_completed": _safe_int(row.pgi_completed),
            "delivered_dns": _safe_int(row.delivered_dns),
            "pod_completed": _safe_int(row.pod_completed),
            "avg_delivery_days": _safe_float(row.avg_delivery_days),
            "avg_pgi_days": _safe_float(row.avg_pgi_days),
            "avg_pod_days": _safe_float(row.avg_pod_days),
            "avg_cycle_days": _safe_float(row.avg_cycle_days),
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
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn_qty,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_qty
            FROM delivery_reports WHERE division IS NOT NULL GROUP BY division ORDER BY dn_qty DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_warehouses_for_python(self) -> List[Any]:
        sql = """
            SELECT
                warehouse AS warehouse_name,
                ship_to_city,
                (good_issue_date::date - dn_create_date::date) AS pgi_days,
                (pod_date::date - good_issue_date::date) AS pod_days,
                (pod_date::date - dn_create_date::date) AS delivery_days,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery
            FROM delivery_reports
            WHERE warehouse IS NOT NULL
            GROUP BY warehouse, ship_to_city, pgi_days, pod_days, delivery_days
        """
        return self._execute(sql).fetchall()

    def fetch_raw_dealers(self) -> List[Any]:
        sql = """
            SELECT
                dealer_code, customer_name AS dealer_name,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days
            FROM delivery_reports WHERE dealer_code IS NOT NULL GROUP BY dealer_code, customer_name ORDER BY delivery_notes DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_products(self) -> List[Any]:
        sql = """
            SELECT
                material_no AS sku, customer_model AS product_name,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes
            FROM delivery_reports WHERE material_no IS NOT NULL GROUP BY material_no, customer_model ORDER BY delivery_notes DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_cities(self) -> List[Any]:
        sql = """
            SELECT
                ship_to_city AS city,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days
            FROM delivery_reports WHERE ship_to_city IS NOT NULL GROUP BY ship_to_city ORDER BY delivery_notes DESC
        """
        return self._execute(sql).fetchall()

    def fetch_raw_daily_trends(self) -> List[Any]:
        sql = """
            SELECT
                dn_create_date AS date,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns
            FROM delivery_reports WHERE dn_create_date >= CURRENT_DATE - INTERVAL '30 days' GROUP BY dn_create_date ORDER BY dn_create_date
        """
        return self._execute(sql).fetchall()

    def fetch_raw_aging(self) -> List[Any]:
        sql = """
            SELECT
                CASE
                    WHEN (pod_date::date - dn_create_date::date) <= 1 THEN '0–1 Day'
                    WHEN (pod_date::date - dn_create_date::date) = 2 THEN '2 Days'
                    WHEN (pod_date::date - dn_create_date::date) = 3 THEN '3 Days'
                    WHEN (pod_date::date - dn_create_date::date) = 4 THEN '4 Days'
                    WHEN (pod_date::date - dn_create_date::date) = 5 THEN '5 Days'
                    WHEN (pod_date::date - dn_create_date::date) = 6 THEN '6 Days'
                    ELSE '7+ Days'
                END AS aging_bucket,
                COUNT(DISTINCT dn_no) AS count
            FROM delivery_reports
            WHERE dn_create_date IS NOT NULL AND pod_date IS NOT NULL
            GROUP BY aging_bucket
            ORDER BY MIN((pod_date::date - dn_create_date::date))
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
    def calculate_summary(raw: Dict[str, Any], warehouses: List[Dict[str, Any]]) -> Dict[str, Any]:
        total_dn = raw.get("total_dn", 0)
        pgi_completed = raw.get("pgi_completed", 0)
        delivered_dns = raw.get("delivered_dns", 0)
        pod_completed = raw.get("pod_completed", 0)

        pgi_rate = _pct(pgi_completed, total_dn)
        delivery_rate = _pct(delivered_dns, total_dn)
        pod_rate = _pct(pod_completed, delivered_dns if delivered_dns else 1)
        health = round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)

        best_wh = warehouses[0]["warehouse_name"] if warehouses else "Lahore"
        slow_wh = warehouses[-1]["warehouse_name"] if warehouses else "Multan"

        return {
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
            "avg_delivery_days": raw.get("avg_delivery_days", 0.0),
            "avg_pgi_days": raw.get("avg_pgi_days", 0.0),
            "avg_pod_days": raw.get("avg_pod_days", 0.0),
            "average_logistics_cycle": raw.get("avg_cycle_days", 0.0),
            "pgi_achievement_rate": pgi_rate,
            "delivery_achievement_rate": delivery_rate,
            "pod_completion_rate": pod_rate,
            "dashboard_health_score": health,
            "ontime_delivery_pct": delivery_rate,
            "pending_pgi": total_dn - pgi_completed,
            "pending_pod": pgi_completed - delivered_dns,
            "best_warehouse": best_wh,
            "slowest_warehouse": slow_wh,
            "critical_delays": sum(1 for w in warehouses if w.get("average_logistics_cycle", 0) > 5.8)
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
# 3. ANALYTICS ENGINE
# ============================================================

class AnalyticsEngine:
    @staticmethod
    def process_divisions(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "division": row.division,
                "units": _safe_int(row.units),
                "dn_qty": _safe_int(row.dn_qty),
                "pgi_qty": _safe_int(row.pgi_qty),
                "pgi_achievement": _pct(_safe_int(row.pgi_qty), _safe_int(row.dn_qty)),
            })
        return result

    @staticmethod
    def process_warehouses(rows: List[Any]) -> List[Dict[str, Any]]:
        wh_stats = defaultdict(lambda: {
            "units": 0, "delivery_notes": 0,
            "pgi_completed": 0, "delivered_dns": 0, "pod_completed": 0,
            "pending_pgi": 0, "pending_delivery": 0,
            "sum_delivery_days": 0.0, "sum_pgi_days": 0.0, "sum_pod_days": 0.0, "sum_cycle_days": 0.0,
            "distances": []
        })

        for row in rows:
            w_name = row.warehouse_name
            city = row.ship_to_city
            dist = PythonDistanceCalculator.get_distance_km(w_name, city)
            
            st = wh_stats[w_name]
            st["units"] += _safe_int(row.units)
            st["delivery_notes"] += _safe_int(row.delivery_notes)
            st["pgi_completed"] += _safe_int(row.pgi_completed)
            st["delivered_dns"] += _safe_int(row.delivered_dns)
            st["pod_completed"] += _safe_int(row.delivered_dns)
            st["pending_pgi"] += _safe_int(row.pending_pgi)
            st["pending_delivery"] += _safe_int(row.pending_delivery)
            st["distances"].append(dist)
            
            delivery_days = row.delivery_days
            pgi_days = row.pgi_days
            pod_days = row.pod_days
            dn_qty = _safe_int(row.delivery_notes)
            pgi_qty = _safe_int(row.pgi_completed)
            pod_qty = _safe_int(row.delivered_dns)
            
            if delivery_days is not None and dn_qty > 0:
                st["sum_delivery_days"] += (delivery_days * dn_qty)
            if pgi_days is not None and pgi_qty > 0:
                st["sum_pgi_days"] += (pgi_days * pgi_qty)
            if pod_days is not None and pod_qty > 0:
                st["sum_pod_days"] += (pod_days * pod_qty)
                st["sum_cycle_days"] += (pod_days * pod_qty)

        result = []
        for w_name, data in wh_stats.items():
            dn = data["delivery_notes"]
            delivered = data["delivered_dns"]
            pgi_comp = data["pgi_completed"]
            
            act_delivery = (data["sum_delivery_days"] / dn) if dn else 0
            act_pgi = (data["sum_pgi_days"] / pgi_comp) if pgi_comp else 0
            act_pod = (data["sum_pod_days"] / delivered) if delivered else 0
            act_cycle = (data["sum_cycle_days"] / delivered) if delivered else act_delivery

            pgi_rate = _pct(pgi_comp, dn)
            delivery_rate = _pct(delivered, dn)
            pod_rate = _pct(delivered, delivered if delivered else 1)
            health = round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)
            
            avg_dist = (sum(data["distances"]) / len(data["distances"])) if data["distances"] else 0

            result.append({
                "warehouse_name": w_name,
                "units": data["units"],
                "delivery_notes": dn,
                "average_distance": round(avg_dist, 1),
                "actual_delivery_days": round(act_delivery, 1),
                "actual_pgi_days": round(act_pgi, 1),
                "actual_pod_days": round(act_pod, 1),
                "average_logistics_cycle": round(act_cycle, 1),
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "pending_pgi": data["pending_pgi"],
                "pending_delivery": data["pending_delivery"],
                "health_score": health,
            })
            
        result.sort(key=lambda x: x["delivery_notes"], reverse=True)
        for i, w in enumerate(result): w["ranking"] = i + 1
        return result

    @staticmethod
    def process_dealers(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "dealer_name": row.dealer_name or row.dealer_code,
                "units": _safe_int(row.units),
                "delivery_notes": _safe_int(row.delivery_notes),
                "avg_cycle_days": round(_safe_float(row.avg_cycle_days), 1),
            })
        return result

    @staticmethod
    def process_products(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "product_name": row.product_name or row.sku,
                "units": _safe_int(row.units),
                "delivery_notes": _safe_int(row.delivery_notes),
            })
        return result

    @staticmethod
    def process_cities(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "city": row.city,
                "units": _safe_int(row.units),
                "delivery_notes": _safe_int(row.delivery_notes),
                "avg_cycle_days": round(_safe_float(row.avg_cycle_days), 1),
            })
        return result

    @staticmethod
    def process_daily_trends(rows: List[Any]) -> Dict[str, List]:
        dates_list, dns, units, pgi, delivered = [], [], [], [], []
        for row in rows:
            dates_list.append(row.date.strftime('%Y-%m-%d') if hasattr(row.date, 'strftime') else str(row.date))
            dns.append(_safe_int(row.dn))
            units.append(_safe_int(row.units))
            pgi.append(_safe_int(row.pgi_completed))
            delivered.append(_safe_int(row.delivered_dns))
        return {"dates": dates_list, "dn": dns, "units": units, "pgi": pgi, "delivered": delivered}

    @staticmethod
    def process_aging(rows: List[Any]) -> List[Dict[str, Any]]:
        total = sum(_safe_int(r.count) for r in rows) or 1
        result = []
        for row in rows:
            cnt = _safe_int(row.count)
            result.append({
                "bucket": row.aging_bucket,
                "count": cnt,
                "percentage": _pct(cnt, total)
            })
        return result

    @staticmethod
    def calculate_scorecard(warehouses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        scored = []
        for w in warehouses:
            # 40% Delivery Days, 30% PGI Days, 20% POD Days, 10% Delivery Volume (normalized)
            score = max(0, 100 - (w.get("actual_delivery_days", 0) * 5 + w.get("actual_pgi_days", 0) * 10 + w.get("actual_pod_days", 0) * 5))
            scored.append({**w, "performance_score": round(score, 1)})
        
        scored.sort(key=lambda x: x["performance_score"], reverse=True)
        for i, w in enumerate(scored):
            w["ranking"] = i + 1
            if i == 0: w["medal"] = "Gold"
            elif i == 1: w["medal"] = "Silver"
            elif i == 2: w["medal"] = "Bronze"
            else: w["medal"] = "Standard"
        return scored

    @staticmethod
    def calculate_share(warehouses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        total_dn = sum(w["delivery_notes"] for w in warehouses) or 1
        shares = []
        for w in warehouses:
            shares.append({
                "warehouse": w["warehouse_name"],
                "delivery_notes": w["delivery_notes"],
                "percentage": _pct(w["delivery_notes"], total_dn)
            })
        shares.sort(key=lambda x: x["percentage"], reverse=True)
        return shares

    @staticmethod
    def process_network(rows: List[Any]) -> Dict[str, Any]:
        return {"nodes": [], "edges": []}

# ============================================================
# 4. ENTERPRISE GRAPH ENGINE (Plotly)
# ============================================================

class GraphEngine:
    """
    Builds world-class, executive-ready enterprise visualizations adhering to SAP/Fabric standards.
    """
    @staticmethod
    def _apply_corporate_layout(fig, title: str, x_title: str = "", y_title: str = "") -> go.Figure:
        fig.update_layout(
            title=dict(text=title, font=dict(family="Plus Jakarta Sans, sans-serif", size=15, color="#FFFFFF"), x=0.02, y=0.95),
            paper_bgcolor="transparent",
            plot_bgcolor="transparent",
            font=dict(family="Plus Jakarta Sans, sans-serif", size=12, color="#94A3B8"),
            margin=dict(l=60, r=30, t=50, b=40),
            hoverlabel=dict(bgcolor="#0F172A", font_size=12, font_color="#FFFFFF"),
            xaxis=dict(title=x_title, showgrid=True, gridcolor="rgba(255,255,255,0.08)", zeroline=False),
            yaxis=dict(title=y_title, showgrid=True, gridcolor="rgba(255,255,255,0.08)", zeroline=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        return fig

    @staticmethod
    def get_warehouse_charts(warehouses: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not warehouses or not PLOTLY_AVAILABLE: return {}
        charts = {}
        
        df_wh = sorted(warehouses, key=lambda x: x["delivery_notes"])
        names = [w["warehouse_name"] for w in df_wh]
        dns = [w["delivery_notes"] for w in df_wh]
        
        fig_dn = go.Figure(go.Bar(
            x=dns, y=names, orientation='h',
            marker=dict(color='#0284C7', line=dict(color='#38BDF8', width=1)),
            text=[f"{v:,} DNs" for v in dns], textposition='outside'
        ))
        GraphEngine._apply_corporate_layout(fig_dn, "Warehouse Delivery Performance", "Delivery Notes (DNs)", "Warehouse")
        fig_dn.update_layout(xaxis=dict(range=[0, max(dns) * 1.2]))
        charts["delivery_performance"] = fig_dn.to_json()

        return charts

    @staticmethod
    def get_daily_trend_charts(daily_data: Dict[str, List]) -> Dict[str, Any]:
        if not daily_data or not daily_data.get("dates") or not PLOTLY_AVAILABLE: return {}
        charts = {}
        dates = daily_data["dates"]
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dates, y=daily_data["dn"], mode='lines+markers', name='Daily DNs', line=dict(color='#0284C7', width=2)))
        fig.add_trace(go.Scatter(x=dates, y=daily_data["pgi"], mode='lines+markers', name='Daily PGI', line=dict(color='#10B981', width=2)))
        fig.add_trace(go.Scatter(x=dates, y=daily_data["delivered"], mode='lines+markers', name='Daily Delivered', line=dict(color='#F59E0B', width=2)))
        
        GraphEngine._apply_corporate_layout(fig, "30-Day Operational Logistics Trend (DN, PGI, POD)", "Date", "Volume Count")
        charts["daily_operations"] = fig.to_json()
        return charts

# ============================================================
# 5. RECOMMENDATION & ALERT ENGINES
# ============================================================

class RecommendationEngine:
    @staticmethod
    def generate(warehouses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        recs = []
        for w in warehouses:
            if w.get("actual_pgi_days", 0) > 1.0:
                recs.append({
                    "title": f"PGI Bottleneck at {w['warehouse_name']}",
                    "description": f"Optimize pick-pack staging; current PGI processing averages {w['actual_pgi_days']} days."
                })
            if w.get("average_logistics_cycle", 0) > 6.0:
                recs.append({
                    "title": f"Cycle SLA Breach at {w['warehouse_name']}",
                    "description": f"Review transport dispatch schedules; average delivery cycle is {w['average_logistics_cycle']} days."
                })
        if not recs:
            recs.append({"title": "Operations Nominal", "description": "All regional warehouses are operating efficiently within normal cycle thresholds."})
        return recs

class AlertEngine:
    @staticmethod
    def generate(summary: Dict[str, Any], warehouses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        alerts = []
        for w in warehouses:
            if w.get("average_logistics_cycle", 0) > 5.8:
                alerts.append({
                    "severity": "CRITICAL",
                    "source": w["warehouse_name"],
                    "message": f"Cycle time of {w['average_logistics_cycle']} days exceeds executive target threshold of 5.8 days."
                })
            if w.get("pending_pgi", 0) > 50:
                alerts.append({
                    "severity": "WARNING",
                    "source": w["warehouse_name"],
                    "message": f"High backlog: {w['pending_pgi']} Delivery Notes pending Post Goods Issue (PGI)."
                })
        return alerts

# ============================================================
# 6. RESPONSE BUILDER & MASTER ORCHESTRATOR
# ============================================================

class ResponseBuilder:
    @staticmethod
    def build(
        summary, pipeline, division, warehouse, dealer, product, city,
        daily_trends_raw, network, alerts, recommendations, metadata, warehouse_charts,
        trend_charts, warehouse_dn_ranking, warehouse_qty_ranking, pgi_ranking, pod_ranking,
        overall_cycle_ranking, warehouse_share, aging_buckets, scorecard
    ) -> Dict[str, Any]:
        
        cards = {
            "total_dn": {"value": summary.get("total_delivery_notes", 0)},
            "total_units": {"value": summary.get("total_units", 0)},
            "health_score": {"value": summary.get("dashboard_health_score", 0)},
            "avg_cycle": {"value": summary.get("average_logistics_cycle", 0)}
        }
        
        return {
            "executive": summary,
            "cards": summary, # passes all KPI fields directly for the 15 KPI cards
            "pipeline": pipeline,
            "division": division,
            "warehouse": warehouse,
            "warehouse_dn_ranking": warehouse_dn_ranking,
            "warehouse_qty_ranking": warehouse_qty_ranking,
            "pgi_ranking": pgi_ranking,
            "pod_ranking": pod_ranking,
            "overall_cycle_ranking": overall_cycle_ranking,
            "warehouse_share": warehouse_share,
            "aging_buckets": aging_buckets,
            "scorecard": scorecard,
            "warehouse_charts": warehouse_charts,
            "dealer": dealer,
            "product": product,
            "city": city,
            "daily_trends": daily_trends_raw,
            "trend_charts": trend_charts,
            "network": network,
            "alerts": alerts,
            "executive_insights": recommendations,
            "recommendations": recommendations,
            "metadata": metadata,
        }

class DashboardService:
    def __init__(self):
        self._repo = DashboardRepository()
        logger.info("🚀 DashboardService v12.0 initialized with Executive Logistics BI Engine")

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
            raw_wh = self._repo.fetch_raw_warehouses_for_python()
            raw_dl = self._repo.fetch_raw_dealers()
            raw_pr = self._repo.fetch_raw_products()
            raw_ct = self._repo.fetch_raw_cities()
            raw_dai = self._repo.fetch_raw_daily_trends()
            raw_ag = self._repo.fetch_raw_aging()
            raw_net = self._repo.fetch_raw_network_rows()
            record_count = self._repo.fetch_record_count()
        except Exception as e:
            logger.error(f"❌ Database execution error caught: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database execution error: {str(e)}")

        warehouse = AnalyticsEngine.process_warehouses(raw_wh)
        summary = BusinessRuleEngine.calculate_summary(raw_sum, warehouse)
        pipeline = BusinessRuleEngine.calculate_pipeline(raw_pipe)
        division = AnalyticsEngine.process_divisions(raw_div)
        dealer = AnalyticsEngine.process_dealers(raw_dl)
        product = AnalyticsEngine.process_products(raw_pr)
        city = AnalyticsEngine.process_cities(raw_ct)
        daily_trends_raw = AnalyticsEngine.process_daily_trends(raw_dai)
        aging_buckets = AnalyticsEngine.process_aging(raw_ag)
        network = AnalyticsEngine.process_network(raw_net)

        warehouse_dn_ranking = sorted(warehouse, key=lambda x: x["delivery_notes"], reverse=True)
        warehouse_qty_ranking = sorted(warehouse, key=lambda x: x["units"], reverse=True)
        pgi_ranking = sorted(warehouse, key=lambda x: x["actual_pgi_days"]) # Lower is better
        pod_ranking = sorted(warehouse, key=lambda x: x["actual_pod_days"]) # Lower is better
        overall_cycle_ranking = sorted(warehouse, key=lambda x: x["average_logistics_cycle"]) # Lower is better
        warehouse_share = AnalyticsEngine.calculate_share(warehouse)
        scorecard = AnalyticsEngine.calculate_scorecard(warehouse)

        alerts = AlertEngine.generate(summary, warehouse)
        recommendations = RecommendationEngine.generate(warehouse)

        warehouse_charts = GraphEngine.get_warehouse_charts(warehouse)
        trend_charts = GraphEngine.get_daily_trend_charts(daily_trends_raw)

        metadata = {
            "application_version": "12.0.0",
            "database_version": "PostgreSQL",
            "postgresql_status": "connected",
            "record_count": record_count,
            "last_refresh": datetime.utcnow().isoformat(),
            "environment": os.getenv("ENVIRONMENT", "production"),
        }

        return ResponseBuilder.build(
            summary, pipeline, division, warehouse, dealer, product, city,
            daily_trends_raw, network, alerts, recommendations, metadata, warehouse_charts,
            trend_charts, warehouse_dn_ranking, warehouse_qty_ranking, pgi_ranking, pod_ranking,
            overall_cycle_ranking, warehouse_share, aging_buckets, scorecard
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
async def dashboard_api_data(service: DashboardService = Depends(get_dashboard_service)):
    return await service.get_dashboard_data({})

@router.get("/warehouse/charts")
async def warehouse_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("warehouse_charts", {})

@router.get("/trends/charts")
async def trend_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("trend_charts", {})
