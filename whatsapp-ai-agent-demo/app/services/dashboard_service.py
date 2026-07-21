# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 15.0 - ENTERPRISE WAREHOUSE INTELLIGENCE PLATFORM
# ============================================================

import hashlib
import json
import logging
import os
import time
import math
from typing import Optional, Dict, List, Any, Union, Tuple
from collections import defaultdict, Counter
from functools import wraps
from datetime import datetime, timedelta, date
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import text
from fastapi import APIRouter, Depends, Query, HTTPException

from app.database import engine
from app.models import DeliveryReport
from app.services.geo_service import GeoService

# Enterprise Libraries Integration
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    import plotly.graph_objects as go
    import plotly.express as px
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False

try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

try:
    import statsmodels.api as sm
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False

try:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    from sklearn.ensemble import IsolationForest
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    from geopy.distance import geodesic
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION, ENUMERATIONS & CONSTANTS
# ============================================================

class DashboardConfig:
    VERSION: str = "15.0.0"
    CACHE_TTL_SECONDS: int = 5
    DEFAULT_CURRENCY: str = "PKR"
    MAX_RECORDS_LIMIT: int = 10000
    TARGET_CYCLE_DAYS: float = 5.0
    TARGET_PGI_DAYS: float = 1.0

class WarehouseTier(str, Enum):
    TIER_1 = "Tier 1 - National Hub"
    TIER_2 = "Tier 2 - Regional Distribution"
    TIER_3 = "Tier 3 - Local Fulfillment"

class RiskLevel(str, Enum):
    LOW = "Low Risk"
    MEDIUM = "Medium Risk"
    HIGH = "High Risk"
    CRITICAL = "Critical Risk"

class SLAStatus(str, Enum):
    ON_TIME = "On Time"
    SLIGHTLY_DELAYED = "Slightly Delayed"
    DELAYED = "Delayed"
    CRITICAL_DELAY = "Critical Delay"

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
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
    return round((numerator / denominator) * 100.0, 2)

# ============================================================
# DISTANCE & GEOSPATIAL ENGINE
# ============================================================

class GeospatialDistanceEngine:
    """
    Handles enterprise distance matrices, geospatial coordinate lookups,
    and routing distance calculations for regional Pakistan warehouses.
    """
    _WAREHOUSE_COORDINATES = {
        "Lahore WH": (31.5497, 74.3436),
        "Karachi WH": (24.8607, 67.0011),
        "Rawalpindi WH": (33.6844, 73.0479),
        "Multan WH": (30.1575, 71.5249),
        "Faisalabad WH": (31.4504, 73.1350),
        "Peshawar WH": (34.0151, 71.5249)
    }

    _CITY_COORDINATES = {
        "Lahore": (31.5497, 74.3436),
        "Karachi": (24.8607, 67.0011),
        "Islamabad": (33.6844, 73.0479),
        "Rawalpindi": (33.5651, 73.0169),
        "Multan": (30.1575, 71.5249),
        "Faisalabad": (31.4504, 73.1350),
        "Peshawar": (34.0151, 71.5249),
        "Quetta": (30.1798, 66.9750),
        "Hyderabad": (25.3960, 68.3578),
        "Sialkot": (32.4945, 74.5229),
        "Gujranwala": (32.1877, 74.1945)
    }

    @classmethod
    def calculate_distance(cls, warehouse: str, city: str) -> float:
        wh_coords = cls._WAREHOUSE_COORDINATES.get(warehouse)
        city_coords = cls._CITY_COORDINATES.get(city)

        if wh_coords and city_coords and GEOPY_AVAILABLE:
            try:
                return round(geodesic(wh_coords, city_coords).kilometers, 2)
            except Exception:
                pass

        matrix = {
            ("Lahore WH", "Karachi"): 1200.0,
            ("Lahore WH", "Islamabad"): 380.0,
            ("Lahore WH", "Rawalpindi"): 375.0,
            ("Lahore WH", "Multan"): 350.0,
            ("Lahore WH", "Faisalabad"): 130.0,
            ("Lahore WH", "Peshawar"): 540.0,
            ("Lahore WH", "Quetta"): 980.0,
            ("Lahore WH", "Hyderabad"): 1150.0,
            ("Lahore WH", "Sialkot"): 125.0,
            ("Lahore WH", "Gujranwala"): 75.0,
            ("Karachi WH", "Hyderabad"): 160.0,
            ("Karachi WH", "Multan"): 870.0,
            ("Karachi WH", "Lahore"): 1200.0,
            ("Rawalpindi WH", "Peshawar"): 170.0,
            ("Rawalpindi WH", "Lahore"): 375.0,
            ("Multan WH", "Lahore"): 350.0,
            ("Multan WH", "Karachi"): 870.0
        }
        return matrix.get((warehouse, city), 350.0)

# ============================================================
# CACHING LAYER
# ============================================================

class InMemoryCacheEngine:
    def __init__(self, ttl_seconds: int = 5):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._ttl = ttl_seconds

    def _generate_key(self, func_name: str, args: tuple, kwargs: dict) -> str:
        serialized = f"{func_name}:{str(args)}:{str(sorted(kwargs.items()))}"
        return hashlib.md5(serialized.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry and (time.time() - entry["timestamp"] < self._ttl):
            return entry["value"]
        return None

    def set(self, key: str, value: Any) -> None:
        self._cache[key] = {"value": value, "timestamp": time.time()}

    def clear(self) -> None:
        self._cache.clear()

_global_cache = InMemoryCacheEngine(ttl_seconds=DashboardConfig.CACHE_TTL_SECONDS)

def cached(ttl: int = 5):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if kwargs.get("no_cache"):
                return await func(*args, **kwargs)
            key = _global_cache._generate_key(func.__name__, args, kwargs)
            cached_val = _global_cache.get(key)
            if cached_val is not None:
                return cached_val
            result = await func(*args, **kwargs)
            _global_cache.set(key, result)
            return result
        return wrapper
    return decorator

# ============================================================
# 1. REPOSITORY LAYER
# ============================================================

class DashboardRepository:
    """
    High-performance SQLAlchemy database repository for PostgreSQL delivery_reports.
    """
    def __init__(self):
        logger.info("🗄️ DashboardRepository initialized for PostgreSQL connection pool")

    def _execute_query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            with engine.connect() as connection:
                result = connection.execute(text(sql), params or {})
                return result
        except Exception as e:
            logger.error(f"❌ PostgreSQL execution error: {str(e)}")
            raise

    def fetch_executive_summary_data(self) -> Dict[str, Any]:
        sql = """
            SELECT
                COUNT(DISTINCT dn_no) AS total_dn,
                COALESCE(SUM(dn_qty), 0) AS total_units,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - dn_create_date::date) END), 0) AS avg_delivery_days,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL THEN (good_issue_date::date - dn_create_date::date) END), 0) AS avg_pgi_days,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_pod_days
            FROM delivery_reports
        """
        row = self._execute_query(sql).first()
        if not row:
            return {}

        warehouses_count = self._execute_query("SELECT COUNT(DISTINCT warehouse) FROM delivery_reports WHERE warehouse IS NOT NULL").scalar() or 0
        dealers_count = self._execute_query("SELECT COUNT(DISTINCT dealer_code) FROM delivery_reports WHERE dealer_code IS NOT NULL").scalar() or 0
        cities_count = self._execute_query("SELECT COUNT(DISTINCT ship_to_city) FROM delivery_reports WHERE ship_to_city IS NOT NULL").scalar() or 0
        products_count = self._execute_query("SELECT COUNT(DISTINCT material_no) FROM delivery_reports WHERE material_no IS NOT NULL").scalar() or 0

        return {
            "total_dn": _safe_int(row.total_dn),
            "total_units": _safe_int(row.total_units),
            "pgi_completed": _safe_int(row.pgi_completed),
            "delivered_dns": _safe_int(row.delivered_dns),
            "pod_completed": _safe_int(row.pod_completed),
            "avg_delivery_days": _safe_float(row.avg_delivery_days),
            "avg_pgi_days": _safe_float(row.avg_pgi_days),
            "avg_pod_days": _safe_float(row.avg_pod_days),
            "warehouses_count": warehouses_count,
            "dealers_count": dealers_count,
            "cities_count": cities_count,
            "products_count": products_count
        }

    def fetch_warehouse_execution_rows(self) -> List[Any]:
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
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_pod
            FROM delivery_reports
            WHERE warehouse IS NOT NULL
            GROUP BY warehouse, ship_to_city, pgi_days, pod_days, delivery_days
        """
        return self._execute_query(sql).fetchall()

    def fetch_city_execution_rows(self) -> List[Any]:
        sql = """
            SELECT
                ship_to_city AS city,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days
            FROM delivery_reports
            WHERE ship_to_city IS NOT NULL
            GROUP BY ship_to_city
            ORDER BY delivery_notes DESC
        """
        return self._execute_query(sql).fetchall()

    def fetch_dealer_execution_rows(self) -> List[Any]:
        sql = """
            SELECT
                dealer_code,
                customer_name AS dealer_name,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN (pod_date::date - good_issue_date::date) END), 0) AS avg_cycle_days
            FROM delivery_reports
            WHERE dealer_code IS NOT NULL
            GROUP BY dealer_code, customer_name
            ORDER BY delivery_notes DESC
        """
        return self._execute_query(sql).fetchall()

    def fetch_delivery_aging_rows(self) -> List[Any]:
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
        return self._execute_query(sql).fetchall()

    def fetch_daily_trend_rows(self) -> List[Any]:
        sql = """
            SELECT
                dn_create_date AS date,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns
            FROM delivery_reports
            WHERE dn_create_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY dn_create_date
            ORDER BY dn_create_date
        """
        return self._execute_query(sql).fetchall()

    def fetch_total_record_count(self) -> int:
        return self._execute_query("SELECT COUNT(*) FROM delivery_reports").scalar() or 0

# ============================================================
# 2. BUSINESS RULE & SLA ENGINE
# ============================================================

class DeliverySLAEngine:
    """
    Implements distance-based delivery targets and SLA classification.
    """
    @staticmethod
    def get_target_days(distance_km: float) -> int:
        if distance_km <= 100:
            return 1
        elif distance_km <= 250:
            return 2
        elif distance_km <= 450:
            return 3
        elif distance_km <= 700:
            return 4
        elif distance_km <= 900:
            return 5
        else:
            return 6

    @classmethod
    def classify_sla_status(cls, actual_days: float, target_days: float) -> str:
        diff = actual_days - target_days
        if diff <= 0:
            return SLAStatus.ON_TIME.value
        elif diff <= 1:
            return SLAStatus.SLIGHTLY_DELAYED.value
        elif diff <= 3:
            return SLAStatus.DELAYED.value
        else:
            return SLAStatus.CRITICAL_DELAY.value

# ============================================================
# 3. WAREHOUSE, CITY & DEALER INTELLIGENCE ENGINES
# ============================================================

class WarehouseIntelligenceEngine:
    """
    Aggregates warehouse performance, computes AI scores (40% Cycle, 25% PGI, 20% POD, 10% Pending, 5% Volume).
    """
    @staticmethod
    def calculate_ai_score(cycle_days: float, pgi_days: float, pod_days: float, pending_work: int, volume: int, max_volume: int) -> Tuple[float, str]:
        cycle_score = max(0.0, 100.0 - (cycle_days * 8.0)) * 0.40
        pgi_score = max(0.0, 100.0 - (pgi_days * 15.0)) * 0.25
        pod_score = max(0.0, 100.0 - (pod_days * 10.0)) * 0.20
        pending_score = max(0.0, 100.0 - (pending_work * 0.2)) * 0.10
        vol_score = (min(float(volume), float(max_volume)) / (float(max_volume) if max_volume else 1.0) * 100.0) * 0.05

        final_score = round(cycle_score + pgi_score + pod_score + pending_score + vol_score, 1)

        if final_score >= 85.0:
            label = "Excellent"
        elif final_score >= 70.0:
            label = "Good"
        elif final_score >= 55.0:
            label = "Average"
        elif final_score >= 40.0:
            label = "Poor"
        else:
            label = "Critical"

        return final_score, label

    @classmethod
    def process_warehouse_statistics(cls, rows: List[Any]) -> List[Dict[str, Any]]:
        wh_stats = defaultdict(lambda: {
            "units": 0,
            "delivery_notes": 0,
            "pgi_completed": 0,
            "delivered_dns": 0,
            "pending_pgi": 0,
            "pending_pod": 0,
            "sum_delivery_days": 0.0,
            "sum_pgi_days": 0.0,
            "sum_pod_days": 0.0,
            "target_days_sum": 0.0,
            "on_time_count": 0,
            "delayed_count": 0,
            "distances": []
        })

        for row in rows:
            w_name = row.warehouse_name
            city = row.ship_to_city
            dist = GeospatialDistanceEngine.calculate_distance(w_name, city)
            target = DeliverySLAEngine.get_target_days(dist)

            st = wh_stats[w_name]
            st["units"] += _safe_int(row.units)
            st["delivery_notes"] += _safe_int(row.delivery_notes)
            st["pgi_completed"] += _safe_int(row.pgi_completed)
            st["delivered_dns"] += _safe_int(row.delivered_dns)
            st["pending_pgi"] += _safe_int(row.pending_pgi)
            st["pending_pod"] += _safe_int(row.pending_pod)
            st["distances"].append(dist)
            st["target_days_sum"] += (target * _safe_int(row.delivery_notes))

            delivery_days = _safe_float(row.delivery_days)
            pgi_days = _safe_float(row.pgi_days)
            pod_days = _safe_float(row.pod_days)
            dn_qty = _safe_int(row.delivery_notes)

            st["sum_delivery_days"] += (delivery_days * dn_qty)
            st["sum_pgi_days"] += (pgi_days * dn_qty)
            st["sum_pod_days"] += (pod_days * dn_qty)

            if delivery_days <= target:
                st["on_time_count"] += dn_qty
            else:
                st["delayed_count"] += dn_qty

        max_vol = max([d["delivery_notes"] for d in wh_stats.values()], default=1)
        result = []

        for w_name, data in wh_stats.items():
            dn = data["delivery_notes"] or 1
            act_delivery = data["sum_delivery_days"] / dn
            act_pgi = data["sum_pgi_days"] / dn
            act_pod = data["sum_pod_days"] / dn
            target_avg = data["target_days_sum"] / dn

            achievement_pct = _pct(data["on_time_count"], dn)
            delay_pct = _pct(data["delayed_count"], dn)
            pending_work_total = data["pending_pgi"] + data["pending_pod"]

            ai_score, ai_label = cls.calculate_ai_score(
                act_delivery, act_pgi, act_pod, pending_work_total, dn, max_vol
            )

            if ai_score >= 75.0:
                traffic_light = "🟢 Green"
            elif ai_score >= 60.0:
                traffic_light = "🟡 Yellow"
            elif ai_score >= 45.0:
                traffic_light = "🟠 Orange"
            else:
                traffic_light = "🔴 Red"

            result.append({
                "warehouse_name": w_name,
                "delivery_notes": data["delivery_notes"],
                "units": data["units"],
                "average_pgi": round(act_pgi, 1),
                "average_pod": round(act_pod, 1),
                "average_cycle": round(act_delivery, 1),
                "target_days": round(target_avg, 1),
                "achievement_pct": achievement_pct,
                "delay_pct": delay_pct,
                "pending_pgi": data["pending_pgi"],
                "pending_pod": data["pending_pod"],
                "overall_score": ai_score,
                "ai_performance_label": ai_label,
                "traffic_light": traffic_light
            })

        result.sort(key=lambda x: x["overall_score"], reverse=True)
        for i, w in enumerate(result):
            w["ranking"] = i + 1
        return result

class CityIntelligenceEngine:
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

class DealerIntelligenceEngine:
    @staticmethod
    def process_dealers(rows: List[Any]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "dealer_name": row.dealer_name or row.dealer_code,
                "dealer_code": row.dealer_code,
                "units": _safe_int(row.units),
                "delivery_notes": _safe_int(row.delivery_notes),
                "avg_cycle_days": round(_safe_float(row.avg_cycle_days), 1),
            })
        return result

# ============================================================
# 4. AI ANALYTICS, FORECAST & PREDICTION ENGINES
# ============================================================

class AIAnalyticsEngine:
    @staticmethod
    def generate_ai_insights(warehouses: List[Dict[str, Any]]) -> Dict[str, str]:
        if not warehouses:
            return {}

        fastest_pgi = min(warehouses, key=lambda x: x["average_pgi"])
        slowest_pgi = max(warehouses, key=lambda x: x["average_pgi"])
        fastest_pod = min(warehouses, key=lambda x: x["average_pod"])
        slowest_pod = max(warehouses, key=lambda x: x["average_pod"])
        highest_vol = max(warehouses, key=lambda x: x["delivery_notes"])
        highest_qty = max(warehouses, key=lambda x: x["units"])
        highest_ppgi = max(warehouses, key=lambda x: x["pending_pgi"])
        highest_ppod = max(warehouses, key=lambda x: x["pending_pod"])
        best_wh = warehouses[0]
        worst_wh = warehouses[-1]

        return {
            "best_performing_warehouse": f"{best_wh['warehouse_name']} (Score: {best_wh['overall_score']})",
            "worst_performing_warehouse": f"{worst_wh['warehouse_name']} (Score: {worst_wh['overall_score']})",
            "fastest_pgi": f"{fastest_pgi['warehouse_name']} ({fastest_pgi['average_pgi']} Days)",
            "slowest_pgi": f"{slowest_pgi['warehouse_name']} ({slowest_pgi['average_pgi']} Days)",
            "fastest_pod": f"{fastest_pod['warehouse_name']} ({fastest_pod['average_pod']} Days)",
            "slowest_pod": f"{slowest_pod['warehouse_name']} ({slowest_pod['average_pod']} Days)",
            "highest_delivery_volume": f"{highest_vol['warehouse_name']} ({highest_vol['delivery_notes']:,} DNs)",
            "highest_quantity_delivered": f"{highest_qty['warehouse_name']} ({highest_qty['units']:,} Units)",
            "highest_pending_pgi": f"{highest_ppgi['warehouse_name']} ({highest_ppgi['pending_pgi']:,} DNs)",
            "highest_pending_pod": f"{highest_ppod['warehouse_name']} ({highest_ppod['pending_pod']:,} Shipments)",
            "most_efficient_warehouse": f"{best_wh['warehouse_name']}",
            "warehouse_needing_immediate_attention": f"{worst_wh['warehouse_name']} due to delayed cycle times."
        }

class RootCauseAnalysisEngine:
    @staticmethod
    def generate_root_cause_analysis(warehouses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        analysis = []
        for w in warehouses:
            if w["overall_score"] < 70.0 or w["delay_pct"] > 15.0:
                causes = []
                if w["average_pgi"] > 1.2:
                    causes.append("Slow warehouse picking and staging delays")
                if w["average_pod"] > 3.0:
                    causes.append("Vehicle dispatch bottlenecks and long-distance transit delays")
                if w["pending_pgi"] > 30:
                    causes.append("Manpower shortage and high unfulfilled workload")
                if not causes:
                    causes.append("Documentation lag and carrier availability constraints")

                analysis.append({
                    "warehouse_name": w["warehouse_name"],
                    "root_causes": causes,
                    "immediate_actions": ["Reallocate shift workforce", "Clear PGI staging backlog"],
                    "short_term_improvements": ["Optimize pick-pack route inside warehouse", "Enforce strict carrier dispatch SLAs"],
                    "long_term_improvements": ["Integrate automated WMS scanning", "Expand regional carrier fleet contracts"],
                    "priority_level": "High" if w["overall_score"] < 50.0 else "Medium",
                    "estimated_improvement": "18-25% faster cycle reduction"
                })
        return analysis

class OperationalRiskEngine:
    @staticmethod
    def evaluate_risks(warehouses: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        risks = []
        for w in warehouses:
            if w["delay_pct"] > 25.0:
                risks.append({
                    "warehouse": w["warehouse_name"],
                    "risk_level": RiskLevel.CRITICAL.value,
                    "description": f"High delay rate of {w['delay_pct']}% threatens regional service agreements."
                })
            elif w["pending_pgi"] > 40:
                risks.append({
                    "warehouse": w["warehouse_name"],
                    "risk_level": RiskLevel.HIGH.value,
                    "description": f"Pending PGI backlog of {w['pending_pgi']} units causes fulfillment queues."
                })
        return risks

class ForecastEngine:
    @staticmethod
    def generate_forecast(daily_trends: Dict[str, List]) -> Dict[str, Any]:
        dns = daily_trends.get("dn", [])
        if not dns:
            return {"projected_next_7_days": 0, "trend_direction": "Stable"}
        
        avg_daily = sum(dns[-7:]) / min(len(dns), 7) if dns else 0
        return {
            "projected_next_7_days": round(avg_daily * 7, 0),
            "trend_direction": "Upward" if len(dns) > 1 and dns[-1] > dns[0] else "Stable"
        }

# ============================================================
# 5. ALERT ENGINE
# ============================================================

class AlertEngine:
    @staticmethod
    def generate_alerts(warehouses: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        alerts = []
        for w in warehouses:
            if w["overall_score"] < 50.0:
                alerts.append({
                    "severity": "CRITICAL",
                    "source": w["warehouse_name"],
                    "message": f"Critical warehouse score of {w['overall_score']} detected. Immediate intervention required."
                })
            elif w["delay_pct"] > 20.0:
                alerts.append({
                    "severity": "WARNING",
                    "source": w["warehouse_name"],
                    "message": f"High delivery delay percentage ({w['delay_pct']}%) exceeding acceptable SLA threshold."
                })
            if w["pending_pgi"] > 50:
                alerts.append({
                    "severity": "WARNING",
                    "source": w["warehouse_name"],
                    "message": f"High PGI backlog of {w['pending_pgi']} Delivery Notes waiting to be processed."
                })
        return alerts

# ============================================================
# 6. ENTERPRISE GRAPH ENGINE (Plotly)
# ============================================================

class GraphEngine:
    """
    Builds enterprise-grade Plotly JSON visualizations for frontend dashboards.
    """
    @staticmethod
    def _apply_corporate_layout(fig: go.Figure, title: str, x_title: str = "", y_title: str = "") -> go.Figure:
        fig.update_layout(
            title=dict(text=title, font=dict(family="Plus Jakarta Sans, sans-serif", size=15, color="#FFFFFF"), x=0.02, y=0.95),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
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
        if not warehouses or not PLOTLY_AVAILABLE:
            return {}
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
        if not daily_data or not daily_data.get("dates") or not PLOTLY_AVAILABLE:
            return {}
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
# 7. DASHBOARD SERVICE & API ORCHESTRATOR
# ============================================================

class DashboardService:
    """
    Master service orchestrator combining repository, analytics, AI insights,
    alert engine, forecast engine, and graph generation.
    """
    def __init__(self):
        self._repo = DashboardRepository()
        logger.info(f"🚀 DashboardService v{DashboardConfig.VERSION} initialized")

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
            raw_sum = self._repo.fetch_executive_summary_data()
            raw_wh = self._repo.fetch_warehouse_execution_rows()
            raw_ct = self._repo.fetch_city_execution_rows()
            raw_dl = self._repo.fetch_dealer_execution_rows()
            raw_ag = self._repo.fetch_delivery_aging_rows()
            raw_dai = self._repo.fetch_daily_trend_rows()
            record_count = self._repo.fetch_total_record_count()
        except Exception as e:
            logger.error(f"❌ Database execution error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database execution error: {str(e)}")

        warehouses = WarehouseIntelligenceEngine.process_warehouse_statistics(raw_wh)
        cities = CityIntelligenceEngine.process_cities(raw_ct)
        dealers = DealerIntelligenceEngine.process_dealers(raw_dl)
        insights = AIAnalyticsEngine.generate_ai_insights(warehouses)
        root_causes = RootCauseAnalysisEngine.generate_root_cause_analysis(warehouses)
        risks = OperationalRiskEngine.evaluate_risks(warehouses)
        alerts = AlertEngine.generate_alerts(warehouses)

        aging_total = sum(_safe_int(r.count) for r in raw_ag) or 1
        aging_buckets = [
            {"bucket": r.aging_bucket, "count": _safe_int(r.count), "percentage": _pct(_safe_int(r.count), aging_total)}
            for r in raw_ag
        ]

        dates_list, dns, units, pgi, delivered = [], [], [], [], []
        for row in raw_dai:
            dates_list.append(row.date.strftime('%Y-%m-%d') if hasattr(row.date, 'strftime') else str(row.date))
            dns.append(_safe_int(row.dn))
            units.append(_safe_int(row.units))
            pgi.append(_safe_int(row.pgi_completed))
            delivered.append(_safe_int(row.delivered_dns))
        daily_trends_raw = {"dates": dates_list, "dn": dns, "units": units, "pgi": pgi, "delivered": delivered}
        forecast = ForecastEngine.generate_forecast(daily_trends_raw)

        total_dn = raw_sum.get("total_dn", 0)
        delivered_dns = raw_sum.get("delivered_dns", 0)
        on_time_pct = _pct(delivered_dns, total_dn)

        cards = {
            "active_warehouses": raw_sum.get("warehouses_count", 0),
            "total_delivery_notes": total_dn,
            "total_dn": total_dn,
            "total_quantity_delivered": raw_sum.get("total_units", 0),
            "total_quantity": raw_sum.get("total_units", 0),
            "total_units": raw_sum.get("total_units", 0),
            "average_delivery_days": raw_sum.get("avg_delivery_days", 0.0),
            "avg_delivery_days": raw_sum.get("avg_delivery_days", 0.0),
            "average_pgi_days": raw_sum.get("avg_pgi_days", 0.0),
            "avg_pgi_days": raw_sum.get("avg_pgi_days", 0.0),
            "average_pod_days": raw_sum.get("avg_pod_days", 0.0),
            "avg_pod_days": raw_sum.get("avg_pod_days", 0.0),
            "ontime_delivery_pct": on_time_pct,
            "active_cities": raw_sum.get("cities_count", 0),
            "active_dealers": raw_sum.get("dealers_count", 0),
            "active_models": raw_sum.get("products_count", 0),
            "pending_pgi": sum(w["pending_pgi"] for w in warehouses),
            "pending_pod": sum(w["pending_pod"] for w in warehouses),
            "best_warehouse": warehouses[0]["warehouse_name"] if warehouses else "Lahore",
            "worst_warehouse": warehouses[-1]["warehouse_name"] if warehouses else "Multan",
            "slowest_warehouse": warehouses[-1]["warehouse_name"] if warehouses else "Multan",
            "critical_delays": sum(1 for w in warehouses if w["overall_score"] < 50.0)
        }

        warehouse_charts = GraphEngine.get_warehouse_charts(warehouses)
        trend_charts = GraphEngine.get_daily_trend_charts(daily_trends_raw)

        return {
            "cards": cards,
            "executive": cards,
            "warehouse_scorecard": warehouses,
            "warehouse": warehouses,
            "warehouse_ranking": warehouses,
            "warehouse_dn_ranking": sorted(warehouses, key=lambda x: x["delivery_notes"], reverse=True),
            "warehouse_qty_ranking": sorted(warehouses, key=lambda x: x["units"], reverse=True),
            "pgi_ranking": sorted(warehouses, key=lambda x: x["average_pgi"]),
            "pod_ranking": sorted(warehouses, key=lambda x: x["average_pod"]),
            "overall_cycle_ranking": sorted(warehouses, key=lambda x: x["average_cycle"]),
            "warehouse_share": [{"warehouse": w["warehouse_name"], "delivery_notes": w["delivery_notes"], "percentage": _pct(w["delivery_notes"], total_dn)} for w in warehouses],
            "city": cities,
            "dealer": dealers,
            "ai_insights": insights,
            "executive_insights": [{"title": k.replace("_", " ").title(), "description": v} for k, v in insights.items()],
            "root_cause_analysis": root_causes,
            "recommendations": root_causes,
            "operational_risks": risks,
            "forecast": forecast,
            "aging_buckets": aging_buckets,
            "scorecard": warehouses,
            "warehouse_charts": warehouse_charts,
            "trend_charts": trend_charts,
            "alerts": alerts,
            "executive_summary": {
                "summary_text": f"Warehouse execution overview across {cards['active_warehouses']} active facilities in Pakistan. Top performance is led by {cards['best_warehouse']}, while {cards['worst_warehouse']} requires immediate logistical intervention.",
                "major_risks": ["Transit bottleneck on long-distance routes", "Staging queue backlog in regional distribution centers"],
                "recommended_actions": ["Streamline PGI generation workflow", "Enforce 24-hour carrier dispatch windows"]
            },
            "metadata": {
                "application_version": DashboardConfig.VERSION,
                "database_version": "PostgreSQL",
                "postgresql_status": "connected",
                "record_count": record_count,
                "last_refresh": datetime.utcnow().isoformat(),
                "environment": os.getenv("ENVIRONMENT", "production")
            }
        }

# ============================================================
# FASTAPI ROUTER & DEPENDENCY INJECTION
# ============================================================

_dashboard_service_instance: Optional[DashboardService] = None

def get_dashboard_service() -> DashboardService:
    global _dashboard_service_instance
    if _dashboard_service_instance is None:
        _dashboard_service_instance = DashboardService()
    return _dashboard_service_instance

router = APIRouter(prefix="/dashboard/api", tags=["dashboard", "warehouse-intelligence"])

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
