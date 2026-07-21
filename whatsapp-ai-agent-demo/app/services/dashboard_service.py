# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 11.8 - ENTERPRISE SUPPLY CHAIN PLATFORM (PROFESSIONAL PLOTLY CHARTS)
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
        total_dn = raw.get("total_dn", 0)
        pgi_completed = raw.get("pgi_completed", 0)
        delivered_dns = raw.get("delivered_dns", 0)
        pod_completed = raw.get("pod_completed", 0)

        pgi_rate = _pct(pgi_completed, total_dn)
        delivery_rate = _pct(delivered_dns, total_dn)
        pod_rate = _pct(pod_completed, delivered_dns if delivered_dns else 1)
        health = round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)

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
            "on_time_pgis": 0, "late_pgis": 0, "on_time_pods": 0, "late_pods": 0,
            "sum_pgi_days": 0.0, "sum_pod_days": 0.0, "sum_cycle_days": 0.0,
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
            
            pgi_days = row.pgi_days
            pod_days = row.pod_days
            pgi_qty = _safe_int(row.pgi_completed)
            pod_qty = _safe_int(row.delivered_dns)
            
            if pgi_days is not None and pgi_qty > 0:
                thresh = 1 if dist <= 250 else 2 if dist <= 450 else 3 if dist <= 700 else 4 if dist <= 900 else 5
                if pgi_days <= thresh: st["on_time_pgis"] += pgi_qty
                else: st["late_pgis"] += pgi_qty
                st["sum_pgi_days"] += (pgi_days * pgi_qty)
                
            if pod_days is not None and pod_qty > 0:
                thresh = 1 if dist <= 100 else 2 if dist <= 250 else 3 if dist <= 450 else 4 if dist <= 700 else 5 if dist <= 900 else 6
                if pod_days <= thresh: st["on_time_pods"] += pod_qty
                else: st["late_pods"] += pod_qty
                st["sum_pod_days"] += (pod_days * pod_qty)
                st["sum_cycle_days"] += (((pgi_days or 0) + pod_days) * pod_qty)

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
                "units": data["units"],
                "delivery_notes": dn,
                "average_distance": round(avg_dist, 1),
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
    def process_network(rows: List[Any]) -> Dict[str, Any]:
        return {"nodes": [], "edges": []}

# ============================================================
# 4. ENTERPRISE GRAPH ENGINE (Plotly)
# ============================================================

class GraphEngine:
    """
    Builds world-class, executive-ready enterprise visualizations adhering to SAP/Fabric standards.
    Strictly focuses on logistics, volumes, cycles, and efficiencies without financial interference.
    """
    
    @staticmethod
    def _apply_corporate_layout(fig, title: str, x_title: str = "", y_title: str = "") -> go.Figure:
        fig.update_layout(
            title=dict(text=title, font=dict(family="Segoe UI, Inter, sans-serif", size=16, color="#0B192C"), x=0.02, y=0.95),
            paper_bgcolor="#FFFFFF",
            plot_bgcolor="#F8FAFC",
            font=dict(family="Segoe UI, Inter, sans-serif", size=12, color="#334155"),
            margin=dict(l=60, r=30, t=60, b=50),
            hoverlabel=dict(bgcolor="#0B192C", font_size=12, font_color="#FFFFFF"),
            xaxis=dict(title=x_title, showgrid=True, gridcolor="#E2E8F0", zeroline=False),
            yaxis=dict(title=y_title, showgrid=True, gridcolor="#E2E8F0", zeroline=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        return fig

    @staticmethod
    def get_warehouse_charts(warehouses: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not warehouses or not PLOTLY_AVAILABLE: return {}
        charts = {}
        
        # 1. Warehouse Delivery Performance (DN Count Horizontal Bar)
        df_wh = sorted(warehouses, key=lambda x: x["delivery_notes"])
        names = [w["warehouse_name"] for w in df_wh]
        dns = [w["delivery_notes"] for w in df_wh]
        
        fig_dn = go.Figure(go.Bar(
            x=dns, y=names, orientation='h',
            marker=dict(color=dns, colorscale='Blues', line=dict(color='#1E3A8A', width=1)),
            text=[f"{v:,} DNs" for v in dns], textposition='outside'
        ))
        GraphEngine._apply_corporate_layout(fig_dn, "Warehouse Delivery Performance (by Total DNs)", "Delivery Notes (DNs)", "Warehouse")
        fig_dn.update_layout(xaxis=dict(range=[0, max(dns) * 1.15]))
        charts["delivery_performance"] = fig_dn.to_json()

        # 2. Warehouse Quantity Delivered (Units Horizontal Bar)
        df_qty = sorted(warehouses, key=lambda x: x["units"])
        q_names = [w["warehouse_name"] for w in df_qty]
        q_units = [w["units"] for w in df_qty]
        
        fig_qty = go.Figure(go.Bar(
            x=q_units, y=q_names, orientation='h',
            marker=dict(color=q_units, colorscale='Teal', line=dict(color='#0F766E', width=1)),
            text=[f"{v:,} Units" for v in q_units], textposition='outside'
        ))
        GraphEngine._apply_corporate_layout(fig_qty, "Warehouse Quantity Delivered (Physical Units)", "Total Units Shipped", "Warehouse")
        fig_qty.update_layout(xaxis=dict(range=[0, max(q_units) * 1.15]))
        charts["quantity_performance"] = fig_qty.to_json()

        # 3. PGI Processing Performance (DN -> PGI Days - Lower is Better)
        df_pgi = sorted(warehouses, key=lambda x: x["actual_pgi_days"], reverse=True) # Reversed for top-down ascending order in horizontal bar
        p_names = [w["warehouse_name"] for w in df_pgi]
        p_days = [w["actual_pgi_days"] for w in df_pgi]
        
        fig_pgi = go.Figure(go.Bar(
            x=p_days, y=p_names, orientation='h',
            marker=dict(color=p_days, colorscale='Reds_r', line=dict(color='#991B1B', width=1)), # Greens/Teal for lower is better
            text=[f"{v:.2f} Days" for v in p_days], textposition='outside'
        ))
        GraphEngine._apply_corporate_layout(fig_pgi, "PGI Processing Performance (DN ➔ PGI) [Lower is Better]", "Average Days", "Warehouse")
        fig_pgi.update_layout(xaxis=dict(range=[0, max(p_days) * 1.20]))
        charts["pgi_performance"] = fig_pgi.to_json()

        # 4. Overall Logistics Delivery Cycle (DN -> POD Days)
        df_cycle = sorted(warehouses, key=lambda x: x["average_logistics_cycle"], reverse=True)
        c_names = [w["warehouse_name"] for w in df_cycle]
        c_days = [w["average_logistics_cycle"] for w in df_cycle]
        
        fig_cycle = go.Figure(go.Bar(
            x=c_days, y=c_names, orientation='h',
            marker=dict(color=c_days, colorscale='Spectral_r', line=dict(color='#B45309', width=1)),
            text=[f"{v:.2f} Days" for v in c_days], textposition='outside'
        ))
        GraphEngine._apply_corporate_layout(fig_cycle, "Overall Delivery Cycle (DN ➔ POD) [Lower is Better]", "Average Cycle Days", "Warehouse")
        fig_cycle.update_layout(xaxis=dict(range=[0, max(c_days) * 1.20]))
        charts["overall_cycle"] = fig_cycle.to_json()

        # 5. Warehouse Contribution Share (Donut Chart)
        total_net = sum(dns)
        shares = [round((v / total_net) * 100, 2) if total_net else 0 for v in dns]
        fig_contrib = go.Figure(go.Pie(
            labels=[w["warehouse_name"] for w in df_wh],
            values=shares,
            hole=0.45,
            marker=dict(colors=px.colors.qualitative.Prism)
        ))
        GraphEngine._apply_corporate_layout(fig_contrib, "Warehouse Volume Contribution Share (%)")
        fig_contrib.update_traces(textinfo='percent+label', textposition='inside')
        charts["volume_contribution"] = fig_contrib.to_json()

        return charts

    @staticmethod
    def get_dealer_charts(dealers: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not dealers or not PLOTLY_AVAILABLE: return {}
        charts = {}
        top_dealers = dealers[:10] # Top 10 key accounts
        names = [d["dealer_name"] for d in top_dealers][::-1]
        volumes = [d["delivery_notes"] for d in top_dealers][::-1]
        
        fig = go.Figure(go.Bar(
            x=volumes, y=names, orientation='h',
            marker=dict(color='#0284C7', line=dict(color='#0369A1', width=1)),
            text=[f"{v:,} DNs" for v in volumes], textposition='outside'
        ))
        GraphEngine._apply_corporate_layout(fig, "Top 10 Key Dealer Accounts by Delivery Volume", "Delivery Notes (DNs)", "Dealer Name")
        fig.update_layout(xaxis=dict(range=[0, max(volumes) * 1.15]))
        charts["top_dealers"] = fig.to_json()
        return charts

    @staticmethod
    def get_product_charts(products: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not products or not PLOTLY_AVAILABLE: return {}
        charts = {}
        top_prod = products[:10]
        names = [p["product_name"] for p in top_prod][::-1]
        units = [p["units"] for p in top_prod][::-1]
        
        fig = go.Figure(go.Bar(
            x=units, y=names, orientation='h',
            marker=dict(color='#0D9488', line=dict(color='#0F766E', width=1)),
            text=[f"{v:,} Units" for v in units], textposition='outside'
        ))
        GraphEngine._apply_corporate_layout(fig, "Top 10 Customer Models Shipped by Volume", "Total Units", "Customer Model / SKU")
        fig.update_layout(xaxis=dict(range=[0, max(units) * 1.15]))
        charts["top_products"] = fig.to_json()
        return charts

    @staticmethod
    def get_city_charts(cities: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not cities or not PLOTLY_AVAILABLE: return {}
        charts = {}
        top_cities = cities[:10]
        names = [c["city"] for c in top_cities][::-1]
        dns = [c["delivery_notes"] for c in top_cities][::-1]
        
        fig = go.Figure(go.Bar(
            x=dns, y=names, orientation='h',
            marker=dict(color='#4F46E5', line=dict(color='#3730A3', width=1)),
            text=[f"{v:,} DNs" for v in dns], textposition='outside'
        ))
        GraphEngine._apply_corporate_layout(fig, "Top 10 Destination Cities by Delivery Volume", "Delivery Notes (DNs)", "Ship-to City")
        fig.update_layout(xaxis=dict(range=[0, max(dns) * 1.15]))
        charts["top_cities"] = fig.to_json()
        return charts

    @staticmethod
    def get_daily_trend_charts(daily_data: Dict[str, List]) -> Dict[str, Any]:
        if not daily_data or not daily_data.get("dates") or not PLOTLY_AVAILABLE: return {}
        charts = {}
        dates = daily_data["dates"]
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dates, y=daily_data["dn"], mode='lines+markers', name='Daily DNs', line=dict(color='#2563EB', width=2)))
        fig.add_trace(go.Scatter(x=dates, y=daily_data["pgi"], mode='lines+markers', name='Daily PGI', line=dict(color='#059669', width=2)))
        fig.add_trace(go.Scatter(x=dates, y=daily_data["delivered"], mode='lines+markers', name='Daily Delivered', line=dict(color='#D97706', width=2)))
        
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
                    "warehouse": w["warehouse_name"],
                    "category": "PGI Bottleneck",
                    "priority": "High",
                    "recommendation": f"Optimize warehouse pick-pack staging at {w['warehouse_name']}; current PGI processing averages {w['actual_pgi_days']} days."
                })
            if w.get("average_logistics_cycle", 0) > 6.0:
                recs.append({
                    "warehouse": w["warehouse_name"],
                    "category": "Cycle Time SLA",
                    "priority": "Critical",
                    "recommendation": f"Review transport dispatch schedules for {w['warehouse_name']} to reduce end-to-end delivery cycle."
                })
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
        dealer_charts, product_charts, city_charts, trend_charts
    ) -> Dict[str, Any]:
        
        cards = {
            "total_dn": {"value": summary.get("total_delivery_notes", 0), "icon": "fa-file-invoice"},
            "total_units": {"value": summary.get("total_units", 0), "icon": "fa-boxes"},
            "health_score": {"value": summary.get("dashboard_health_score", 0), "target": 95, "icon": "fa-heartbeat"},
            "avg_cycle": {"value": summary.get("average_logistics_cycle", 0), "unit": "Days", "icon": "fa-clock"}
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
            "daily_trends": daily_trends_raw,
            "trend_charts": trend_charts,
            "network": network,
            "alerts": alerts,
            "recommendations": recommendations,
            "metadata": metadata,
        }

class DashboardService:
    def __init__(self):
        self._repo = DashboardRepository()
        logger.info("🚀 DashboardService v11.8 initialized with Enterprise Plotly Engine")

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
            raw_net = self._repo.fetch_raw_network_rows()
            record_count = self._repo.fetch_record_count()
        except Exception as e:
            logger.error(f"❌ Database execution error caught: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database execution error: {str(e)}")

        summary = BusinessRuleEngine.calculate_summary(raw_sum)
        pipeline = BusinessRuleEngine.calculate_pipeline(raw_pipe)
        division = AnalyticsEngine.process_divisions(raw_div)
        warehouse = AnalyticsEngine.process_warehouses(raw_wh)
        dealer = AnalyticsEngine.process_dealers(raw_dl)
        product = AnalyticsEngine.process_products(raw_pr)
        city = AnalyticsEngine.process_cities(raw_ct)
        daily_trends_raw = AnalyticsEngine.process_daily_trends(raw_dai)
        network = AnalyticsEngine.process_network(raw_net)

        alerts = AlertEngine.generate(summary, warehouse)
        recommendations = RecommendationEngine.generate(warehouse)

        warehouse_charts = GraphEngine.get_warehouse_charts(warehouse)
        dealer_charts = GraphEngine.get_dealer_charts(dealer)
        product_charts = GraphEngine.get_product_charts(product)
        city_charts = GraphEngine.get_city_charts(city)
        trend_charts = GraphEngine.get_daily_trend_charts(daily_trends_raw)

        metadata = {
            "application_version": "11.8.0",
            "database_version": "PostgreSQL",
            "postgresql_status": "connected",
            "record_count": record_count,
            "last_refresh": datetime.utcnow().isoformat(),
            "environment": os.getenv("ENVIRONMENT", "production"),
        }

        return ResponseBuilder.build(
            summary, pipeline, division, warehouse, dealer, product, city,
            daily_trends_raw, network, alerts, recommendations, metadata,
            warehouse_charts, dealer_charts, product_charts, city_charts, trend_charts
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

@router.get("/dealer/charts")
async def dealer_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("dealer_charts", {})

@router.get("/product/charts")
async def product_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("product_charts", {})

@router.get("/city/charts")
async def city_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("city_charts", {})

@router.get("/trends/charts")
async def trend_charts(service: DashboardService = Depends(get_dashboard_service)):
    data = await service.get_dashboard_data({})
    return data.get("trend_charts", {})
