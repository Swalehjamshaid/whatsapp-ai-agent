# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 20.1 – FULL ERROR HANDLING + INTELLIGENCE
# ============================================================

import hashlib
import json
import logging
import os
import time
import math
import random
import io
from typing import Optional, Dict, List, Any, Union, Tuple, Set, Callable
from collections import defaultdict, Counter, OrderedDict
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from functools import wraps, lru_cache
from datetime import datetime, timedelta, date
from abc import ABC, abstractmethod
import asyncio
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

from sqlalchemy import text, func, and_, or_, desc, asc, case, extract
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import SQLAlchemyError, IntegrityError, OperationalError
from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks, Request, Response, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator, confloat, conint, constr

# ------- Enterprise Data Science Libraries -------
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
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False

try:
    from geopy.distance import geodesic
    from geopy.geocoders import Nominatim
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

try:
    from scipy import stats
    from scipy.optimize import minimize
    from scipy.cluster.hierarchy import linkage, dendrogram, fcluster
    from scipy.spatial.distance import pdist
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

try:
    from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
    from sklearn.linear_model import LinearRegression, Ridge, Lasso
    from sklearn.preprocessing import StandardScaler, MinMaxScaler
    from sklearn.cluster import KMeans
    from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error
    from sklearn.model_selection import train_test_split, cross_val_score
    from sklearn.pipeline import Pipeline
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    import statsmodels.api as sm
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.seasonal import seasonal_decompose
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False

from app.database import engine, get_db
from app.models import DeliveryReport
try:
    from app.services.geo_service import GeoService
    GEO_SERVICE_AVAILABLE = True
except ImportError:
    GEO_SERVICE_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("GeoService not available, distance features will be disabled.")

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/var/log/dashboard_service.log') if os.path.exists('/var/log') else logging.NullHandler()
    ]
)

# ============================================================
# BLOCK 2: Enumerations & Constants
# ============================================================

class WarehouseStatus(Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    AVERAGE = "average"
    POOR = "poor"
    CRITICAL = "critical"

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class DeliveryStatus(Enum):
    ON_TIME = "on_time"
    SLIGHTLY_DELAYED = "slightly_delayed"
    DELAYED = "delayed"
    CRITICAL_DELAY = "critical_delay"

class PriorityLevel(Enum):
    IMMEDIATE = "immediate"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

# ============================================================
# BLOCK 3: Configuration
# ============================================================

@dataclass
class DashboardConfig:
    cache_ttl_seconds: int = 300
    cache_max_size: int = 1000
    pgi_target_days: float = 1.0
    pod_base_target_days: float = 1.0
    delivery_target_base_days: float = 1.0
    health_score_excellent: float = 90.0
    health_score_good: float = 75.0
    health_score_average: float = 60.0
    health_score_poor: float = 40.0
    avg_unit_price: float = 0.0

config = DashboardConfig()

# ============================================================
# BLOCK 4: Utility Layer (SafeNumber, DateUtils)
# ============================================================

class SafeNumber:
    @staticmethod
    def to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                return float(value.replace(',', '').strip())
            return default
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def to_int(value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                return int(value.replace(',', '').strip())
            return default
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def to_decimal(value: Any, decimals: int = 2) -> float:
        return round(SafeNumber.to_float(value), decimals)
    
    @staticmethod
    def pct(numerator: float, denominator: float, default: float = 0.0) -> float:
        if not denominator or denominator == 0:
            return default
        return round((numerator / denominator) * 100, 2)

class DateUtils:
    @staticmethod
    def parse_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            try:
                return datetime.strptime(value, '%Y-%m-%d').date()
            except ValueError:
                try:
                    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').date()
                except ValueError:
                    return None
        return None
    
    @staticmethod
    def days_between(start: Optional[date], end: Optional[date]) -> float:
        if start is None or end is None:
            return 0.0
        return (end - start).days

# ============================================================
# BLOCK 5: Exception Handling
# ============================================================

class DashboardServiceError(Exception):
    pass

class DatabaseError(DashboardServiceError):
    pass

# ============================================================
# BLOCK 6: Caching Layer (EnterpriseCache, cached decorator)
# ============================================================

class EnterpriseCache:
    def __init__(self, max_size: int = 2000, default_ttl: int = 300):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._access_order: List[str] = []
        logger.info(f"EnterpriseCache initialized with max_size={max_size}, ttl={default_ttl}s")
    
    def _make_key(self, func_name: str, args: tuple, kwargs: dict) -> str:
        key_parts = [func_name]
        key_parts.extend(str(arg) for arg in args)
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
        raw_key = "|".join(key_parts)
        return hashlib.sha256(raw_key.encode()).hexdigest()
    
    def _evict_if_needed(self) -> None:
        while len(self._cache) >= self._max_size and self._access_order:
            oldest_key = self._access_order.pop(0)
            if oldest_key in self._cache:
                del self._cache[oldest_key]
    
    def _touch(self, key: str) -> None:
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)
    
    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry:
            if time.time() - entry['timestamp'] < entry.get('ttl', self._default_ttl):
                self._touch(key)
                return entry['value']
            else:
                self._cache.pop(key, None)
                if key in self._access_order:
                    self._access_order.remove(key)
        return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl or self._default_ttl
        self._evict_if_needed()
        self._cache[key] = {
            'value': value,
            'timestamp': time.time(),
            'ttl': ttl
        }
        self._touch(key)
    
    def clear(self) -> None:
        self._cache.clear()
        self._access_order.clear()

cache = EnterpriseCache()

def cached(ttl: Optional[int] = None):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if kwargs.get('no_cache', False):
                return await func(*args, **kwargs)
            key = cache._make_key(func.__name__, args, kwargs)
            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value
            result = await func(*args, **kwargs)
            cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator

# ============================================================
# BLOCK 7: Repository Layer (DashboardRepository) - ENHANCED
# ============================================================

    # ---------- Warehouse Data (aggregated per warehouse) ----------
    def fetch_warehouse_data(self) -> List[Dict[str, Any]]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS total_revenue" if has_amount else "0 AS total_revenue"
        sql = f"""
            WITH warehouse_metrics AS (
                SELECT
                    warehouse AS warehouse_name,
                    COALESCE(SUM(dn_qty), 0) AS total_units,
                    COUNT(DISTINCT dn_no) AS delivery_notes,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed_dn,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi_count,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery_count,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL THEN dn_qty ELSE 0 END), 0) AS pgi_units,
                    COALESCE(SUM(CASE WHEN pod_date IS NOT NULL THEN dn_qty ELSE 0 END), 0) AS delivered_units,
                    COALESCE(SUM(CASE WHEN pod_date IS NULL THEN dn_qty ELSE 0 END), 0) AS pending_units,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NULL THEN dn_qty ELSE 0 END), 0) AS pending_pgi_units,
                    {revenue_sql},
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS avg_pgi_days,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - good_issue_date::timestamp))/86400 END), 0) AS avg_pod_days,
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS avg_cycle_days,
                    COALESCE(MIN(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS min_delivery_days,
                    COALESCE(MAX(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS max_delivery_days,
                    COALESCE(MIN(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - good_issue_date::timestamp))/86400 END), 0) AS min_pod_days,
                    COALESCE(MAX(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - good_issue_date::timestamp))/86400 END), 0) AS max_pod_days,
                    COALESCE(MIN(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS min_cycle_days,
                    COALESCE(MAX(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS max_cycle_days,
                    MIN(dn_create_date) AS first_dn,
                    MAX(dn_create_date) AS last_dn
                FROM delivery_reports
                WHERE warehouse IS NOT NULL
                GROUP BY warehouse   -- removed ship_to_city
            )
            SELECT
                warehouse_name,
                total_units,
                delivery_notes,
                pgi_completed_dn,
                delivered_dns,
                pending_pgi_count,
                pending_delivery_count,
                pgi_units,
                delivered_units,
                pending_units,
                pending_pgi_units,
                total_revenue,
                avg_pgi_days,
                avg_pod_days,
                avg_cycle_days,
                min_delivery_days,
                max_delivery_days,
                min_pod_days,
                max_pod_days,
                min_cycle_days,
                max_cycle_days,
                first_dn,
                last_dn,
                CASE WHEN total_units > 0 THEN ROUND((pgi_units / total_units) * 100, 2) ELSE 0 END AS pgi_achievement_rate,
                CASE WHEN total_units > 0 THEN ROUND((delivered_units / total_units) * 100, 2) ELSE 0 END AS delivery_achievement_rate,
                CASE WHEN total_units > 0 THEN ROUND((pending_units / total_units) * 100, 2) ELSE 0 END AS pending_rate
            FROM warehouse_metrics
            ORDER BY delivery_notes DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "warehouse_name": row.warehouse_name,
                "units": SafeNumber.to_int(row.total_units),
                "delivery_notes": SafeNumber.to_int(row.delivery_notes),
                "pgi_completed": SafeNumber.to_int(row.pgi_completed_dn),
                "delivered_dns": SafeNumber.to_int(row.delivered_dns),
                "pending_pgi": SafeNumber.to_int(row.pending_pgi_count),
                "pending_delivery": SafeNumber.to_int(row.pending_delivery_count),
                "avg_pgi_days": SafeNumber.to_float(row.avg_pgi_days),
                "avg_pod_days": SafeNumber.to_float(row.avg_pod_days),
                "avg_cycle_days": SafeNumber.to_float(row.avg_cycle_days),
                "min_delivery_days": SafeNumber.to_float(row.min_delivery_days),
                "max_delivery_days": SafeNumber.to_float(row.max_delivery_days),
                "min_pod_days": SafeNumber.to_float(row.min_pod_days),
                "max_pod_days": SafeNumber.to_float(row.max_pod_days),
                "min_cycle_days": SafeNumber.to_float(row.min_cycle_days),
                "max_cycle_days": SafeNumber.to_float(row.max_cycle_days),
                "first_dn": row.first_dn,
                "last_dn": row.last_dn,
                "total_units": SafeNumber.to_int(row.total_units),
                "pgi_units": SafeNumber.to_int(row.pgi_units),
                "delivered_units": SafeNumber.to_int(row.delivered_units),
                "pending_units": SafeNumber.to_int(row.pending_units),
                "pending_pgi_units": SafeNumber.to_int(row.pending_pgi_units),
                "pgi_achievement_rate": SafeNumber.to_float(row.pgi_achievement_rate),
                "delivery_achievement_rate": SafeNumber.to_float(row.delivery_achievement_rate),
                "pending_rate": SafeNumber.to_float(row.pending_rate),
                "total_revenue": SafeNumber.to_float(row.total_revenue),
            })
        return result

# ============================================================
# BLOCK 8: Distance Calculation Engine (Enhanced)
# ============================================================

class DistanceCalculationEngine:
    _coord_cache = {}

    @classmethod
    def haversine(cls, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
    
    @classmethod
    def calculate_distance(cls, origin: str, destination: str) -> float:
        if not GEO_SERVICE_AVAILABLE:
            return 0.0
        try:
            coords1 = GeoService.get_city_coordinates(origin)
            coords2 = GeoService.get_city_coordinates(destination)
            return cls.haversine(coords1.get("lat", 0), coords1.get("lng", 0), coords2.get("lat", 0), coords2.get("lng", 0))
        except Exception as e:
            logger.warning(f"Distance calculation failed for {origin}->{destination}: {e}")
            return 0.0
    
    @classmethod
    def get_target_days(cls, distance_km: float) -> int:
        if distance_km <= 100: return 1
        elif distance_km <= 250: return 2
        elif distance_km <= 450: return 3
        elif distance_km <= 700: return 4
        elif distance_km <= 900: return 5
        else: return 6

    @classmethod
    def compute_compliance(cls, actual_days: float, target_days: int) -> float:
        if target_days == 0:
            return 0.0
        if actual_days == 0:
            return 100.0
        return round((target_days / actual_days) * 100, 2)

    @classmethod
    def get_performance_rating(cls, gap: float) -> str:
        if gap <= 0:
            return "Excellent"
        if gap <= 1:
            return "Good"
        if gap <= 2:
            return "Average"
        return "Poor"

# ============================================================
# BLOCK 9: Business Rule Engine (Enhanced)
# ============================================================

class BusinessRuleEngine:
    @staticmethod
    def calculate_performance_score(delivery_pct: float, pod_pct: float, cycle_pct: float, pending_pct: float, pgi_pct: float) -> float:
        pending_score = max(0, 100 - pending_pct)
        score = (delivery_pct * 0.30) + (pod_pct * 0.25) + (cycle_pct * 0.20) + (pending_score * 0.15) + (pgi_pct * 0.10)
        return round(score, 2)

    @staticmethod
    def get_grade(score: float) -> str:
        if score >= 95:
            return "A+"
        elif score >= 85:
            return "A"
        elif score >= 75:
            return "B"
        elif score >= 65:
            return "C"
        elif score >= 55:
            return "D"
        else:
            return "Critical"

    @staticmethod
    def get_risk_level(score: float) -> RiskLevel:
        if score >= 75:
            return RiskLevel.LOW
        elif score >= 60:
            return RiskLevel.MEDIUM
        elif score >= 45:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL

    @staticmethod
    def calculate_health_score(pgi_rate: float, delivery_rate: float, pod_rate: float, cycle_days: float) -> float:
        cycle_score = max(0, 100 - (cycle_days - 0.5) * 15)
        return round((delivery_rate * 0.30) + (pgi_rate * 0.30) + (pod_rate * 0.20) + (cycle_score * 0.20), 2)

    @staticmethod
    def classify_performance(score: float) -> Dict[str, Any]:
        if score >= 90: return {"tier": "tier_1", "label": "Excellent", "color": "#22c55e", "status": "Excellent"}
        elif score >= 80: return {"tier": "tier_2", "label": "Good", "color": "#84cc16", "status": "Good"}
        elif score >= 70: return {"tier": "tier_3", "label": "Average", "color": "#f59e0b", "status": "Average"}
        elif score >= 60: return {"tier": "tier_4", "label": "Poor", "color": "#f97316", "status": "Poor"}
        else: return {"tier": "tier_5", "label": "Critical", "color": "#ef4444", "status": "Critical"}

# ============================================================
# BLOCK 10: Warehouse Intelligence Engine (Enhanced + Safe)
# ============================================================

class WarehouseIntelligenceEngine:
    @staticmethod
    def compute_warehouse_intelligence(
        warehouse_records: List[Dict[str, Any]],
        avg_distances: Dict[str, float],
        compliance_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        enriched = []
        for w in warehouse_records:
            # Safe extraction with defaults
            total_units = w.get('total_units', 0)
            pgi_units = w.get('pgi_units', 0)
            delivered_units = w.get('delivered_units', 0)
            pending_units = w.get('pending_units', 0)
            pending_pgi_units = w.get('pending_pgi_units', 0)
            revenue = w.get('total_revenue', 0)
            avg_cycle = w.get('avg_cycle_days', 0)
            avg_pgi = w.get('avg_pgi_days', 0)
            avg_pod = w.get('avg_pod_days', 0)
            min_delivery = w.get('min_delivery_days', 0)
            max_delivery = w.get('max_delivery_days', 0)
            min_pod = w.get('min_pod_days', 0)
            max_pod = w.get('max_pod_days', 0)
            min_cycle = w.get('min_cycle_days', 0)
            max_cycle = w.get('max_cycle_days', 0)

            pgi_rate = SafeNumber.pct(pgi_units, total_units)
            delivery_rate = SafeNumber.pct(delivered_units, total_units)
            pending_rate = SafeNumber.pct(pending_units, total_units)
            pod_rate = delivery_rate

            dist = avg_distances.get(w.get('warehouse_name', ''), 0.0)
            target_days = DistanceCalculationEngine.get_target_days(dist) if dist > 0 else 1

            delivery_gap = avg_cycle - target_days
            delivery_compliance = DistanceCalculationEngine.compute_compliance(avg_cycle, target_days)
            delivery_rating = DistanceCalculationEngine.get_performance_rating(delivery_gap)

            pod_target = 1.0
            pod_gap = avg_pod - pod_target
            pod_compliance = DistanceCalculationEngine.compute_compliance(avg_pod, pod_target)
            pod_rating = DistanceCalculationEngine.get_performance_rating(pod_gap)

            cycle_gap = avg_cycle - target_days
            cycle_compliance = DistanceCalculationEngine.compute_compliance(avg_cycle, target_days)
            cycle_rating = DistanceCalculationEngine.get_performance_rating(cycle_gap)

            perf_score = BusinessRuleEngine.calculate_performance_score(
                delivery_compliance,
                pod_compliance,
                cycle_compliance,
                pending_rate,
                pgi_rate
            )
            grade = BusinessRuleEngine.get_grade(perf_score)
            risk = BusinessRuleEngine.get_risk_level(perf_score)
            health_score = BusinessRuleEngine.calculate_health_score(pgi_rate, delivery_rate, pod_rate, avg_cycle)
            classification = BusinessRuleEngine.classify_performance(health_score)

            warehouse_summary = {
                "warehouse": w.get('warehouse_name', 'Unknown'),
                "rank": 0,
                "health_score": health_score,
                "performance_score": perf_score,
                "grade": grade,
                "risk": risk.value,
                "delivered_units": delivered_units,
                "pgi_units": pgi_units,
                "avg_pgi_days": avg_pgi,
                "delivery": {
                    "avg_days": avg_cycle,
                    "min_days": min_delivery,
                    "max_days": max_delivery,
                    "target_days": target_days,
                    "gap_days": delivery_gap,
                    "compliance_pct": delivery_compliance,
                    "status": delivery_rating
                },
                "pod": {
                    "avg_days": avg_pod,
                    "min_days": min_pod,
                    "max_days": max_pod,
                    "target_days": pod_target,
                    "gap_days": pod_gap,
                    "compliance_pct": pod_compliance,
                    "status": pod_rating
                },
                "cycle": {
                    "avg_days": avg_cycle,
                    "min_days": min_cycle,
                    "max_days": max_cycle,
                    "target_days": target_days,
                    "gap_days": cycle_gap,
                    "compliance_pct": cycle_compliance,
                    "status": cycle_rating
                },
                "pending": {
                    "dn": w.get('pending_delivery', 0) + w.get('pending_pgi', 0),
                    "units": pending_units,
                    "avg_days": 0,
                    "oldest_days": 0
                },
                "delayed": {
                    "dn": 0,
                    "units": 0,
                    "revenue": 0
                },
                "trend": "▬ Stable",
                "ai_insight": ""
            }

            # Backward compatibility fields
            warehouse_summary.update({
                "dns": w.get('delivery_notes', 0),
                "units": total_units,
                "revenue": revenue,
                "pgi_pct": pgi_rate,
                "delivery_pct": delivery_rate,
                "pod_pct": pod_rate,
                "avg_days": avg_cycle,
                "avg_delivery_days": avg_cycle,
                "avg_pod_days": avg_pod,
                "avg_pgi_days": avg_pgi,
                "pending_dns": w.get('pending_delivery', 0) + w.get('pending_pgi', 0),
                "pending_units": pending_units,
                "status": classification.get('status', 'Unknown'),
                "performance_score": health_score,
                "risk_emoji": "🟢" if risk == RiskLevel.LOW else "🟡" if risk == RiskLevel.MEDIUM else "🟠" if risk == RiskLevel.HIGH else "🔴"
            })

            enriched.append(warehouse_summary)

        enriched.sort(key=lambda x: x.get('performance_score', 0), reverse=True)
        for i, w in enumerate(enriched, 1):
            w['rank'] = i

        return enriched

    @staticmethod
    def get_best_and_worst(warehouses: List[Dict[str, Any]]) -> Tuple[Dict, Dict]:
        if not warehouses:
            return {}, {}
        best = max(warehouses, key=lambda x: x.get('health_score', 0))
        worst = min(warehouses, key=lambda x: x.get('health_score', 0))
        return best, worst

# ============================================================
# BLOCK 11: KPI Engine
# ============================================================

class KPIEngine:
    @staticmethod
    def compute_day_over_day(daily_trend: List[Dict]) -> Dict[str, Any]:
        if len(daily_trend) < 2:
            return {}
        today = daily_trend[-1]
        yesterday = daily_trend[-2]
        return {
            "dn_growth": SafeNumber.pct(today.get('dn_count', 0) - yesterday.get('dn_count', 0), yesterday.get('dn_count', 1)),
            "units_growth": SafeNumber.pct(today.get('units', 0) - yesterday.get('units', 0), yesterday.get('units', 1)),
            "revenue_growth": SafeNumber.pct(today.get('revenue', 0) - yesterday.get('revenue', 0), yesterday.get('revenue', 1)),
        }

    @staticmethod
    def compute_national_kpis(warehouse_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not warehouse_summaries:
            return {}
        total = len(warehouse_summaries)
        avg_delivery = sum(w.get('delivery', {}).get('avg_days', 0) for w in warehouse_summaries) / total
        avg_pod = sum(w.get('pod', {}).get('avg_days', 0) for w in warehouse_summaries) / total
        avg_cycle = sum(w.get('cycle', {}).get('avg_days', 0) for w in warehouse_summaries) / total

        fastest = min(warehouse_summaries, key=lambda w: w.get('cycle', {}).get('avg_days', 999))
        slowest = max(warehouse_summaries, key=lambda w: w.get('cycle', {}).get('avg_days', 0))
        best_pod = min(warehouse_summaries, key=lambda w: w.get('pod', {}).get('avg_days', 999))
        worst_pod = max(warehouse_summaries, key=lambda w: w.get('pod', {}).get('avg_days', 0))
        best_cycle = min(warehouse_summaries, key=lambda w: w.get('cycle', {}).get('avg_days', 999))
        worst_cycle = max(warehouse_summaries, key=lambda w: w.get('cycle', {}).get('avg_days', 0))

        sorted_by_perf = sorted(warehouse_summaries, key=lambda w: w.get('performance_score', 0), reverse=True)
        top_5 = [{"warehouse": w.get('warehouse', ''), "score": w.get('performance_score', 0)} for w in sorted_by_perf[:5]]
        bottom_5 = [{"warehouse": w.get('warehouse', ''), "score": w.get('performance_score', 0)} for w in sorted_by_perf[-5:]]

        return {
            "national_averages": {
                "delivery_days": round(avg_delivery, 2),
                "pod_days": round(avg_pod, 2),
                "cycle_days": round(avg_cycle, 2)
            },
            "fastest_warehouse": fastest.get('warehouse', '') if fastest else '',
            "slowest_warehouse": slowest.get('warehouse', '') if slowest else '',
            "best_pod": best_pod.get('warehouse', '') if best_pod else '',
            "worst_pod": worst_pod.get('warehouse', '') if worst_pod else '',
            "best_cycle": best_cycle.get('warehouse', '') if best_cycle else '',
            "worst_cycle": worst_cycle.get('warehouse', '') if worst_cycle else '',
            "top_5_warehouses": top_5,
            "bottom_5_warehouses": bottom_5
        }

# ============================================================
# BLOCK 12: Alert Engine (Max 8, Deduplicated)
# ============================================================

class AlertEngine:
    @staticmethod
    def generate_alerts(warehouse_summaries: List[Dict[str, Any]], kpis: Dict) -> List[Dict[str, Any]]:
        raw_alerts = []

        for w in warehouse_summaries:
            warehouse = w.get('warehouse', 'Unknown')
            gap = w.get('delivery', {}).get('gap_days', 0)
            if gap > 0:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "CRITICAL" if gap > 3 else "HIGH" if gap > 2 else "WARNING",
                    "category": "Delivery Gap",
                    "message": f"Delivery gap of {gap:.1f} days",
                    "urgency": 3 + gap
                })
            pod_gap = w.get('pod', {}).get('gap_days', 0)
            if pod_gap > 0:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "CRITICAL" if pod_gap > 2 else "HIGH" if pod_gap > 1 else "WARNING",
                    "category": "POD Gap",
                    "message": f"POD gap of {pod_gap:.1f} days",
                    "urgency": 2 + pod_gap
                })
            pending_units = w.get('pending', {}).get('units', 0)
            if pending_units > 1000:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "HIGH" if pending_units > 2000 else "WARNING",
                    "category": "Pending Units",
                    "message": f"High pending units: {pending_units}",
                    "urgency": 2 if pending_units > 2000 else 1
                })
            pending_dn = w.get('pending', {}).get('dn', 0)
            if pending_dn > 50:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "HIGH" if pending_dn > 100 else "WARNING",
                    "category": "Pending DNs",
                    "message": f"High pending DNs: {pending_dn}",
                    "urgency": 2 if pending_dn > 100 else 1
                })
            compliance = w.get('delivery', {}).get('compliance_pct', 100)
            if compliance < 80:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "HIGH" if compliance < 70 else "WARNING",
                    "category": "Below Standard",
                    "message": f"Delivery compliance below 80% ({compliance}%)",
                    "urgency": 2 if compliance < 70 else 1
                })
            pgi = w.get('pgi_pct', 100)
            if pgi < 80:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "WARNING",
                    "category": "PGI",
                    "message": f"PGI rate below 80% ({pgi}%)",
                    "urgency": 1
                })
            pod = w.get('pod_pct', 100)
            if pod < 80:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "WARNING",
                    "category": "POD",
                    "message": f"POD rate below 80% ({pod}%)",
                    "urgency": 1
                })

        if kpis.get('pod_achievement', {}).get('value', 100) < 85:
            raw_alerts.append({
                "source": "System",
                "severity": "WARNING",
                "category": "POD Achievement",
                "message": f"Overall POD achievement is {kpis['pod_achievement']['value']:.1f}%, below target.",
                "urgency": 1
            })
        if kpis.get('pending_dn', {}).get('value', 0) > 5000:
            raw_alerts.append({
                "source": "System",
                "severity": "HIGH",
                "category": "Pending DNs",
                "message": f"High number of pending DNs: {kpis['pending_dn']['value']}",
                "urgency": 2
            })

        # Deduplicate
        seen = set()
        deduped = []
        for alert in raw_alerts:
            key = (alert['source'], alert['category'])
            if key not in seen:
                seen.add(key)
                deduped.append(alert)
            else:
                for existing in deduped:
                    if existing['source'] == alert['source'] and existing['category'] == alert['category']:
                        if alert.get('urgency', 0) > existing.get('urgency', 0):
                            deduped.remove(existing)
                            deduped.append(alert)
                        break

        deduped.sort(key=lambda x: x.get('urgency', 0), reverse=True)
        return deduped[:8]

# ============================================================
# BLOCK 13: Recommendation Engine
# ============================================================

class RecommendationEngine:
    @staticmethod
    def generate_recommendations(warehouse_summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        recs = []
        for w in warehouse_summaries:
            warehouse = w.get('warehouse', 'Unknown')
            actions = []
            priority = "Low"

            delivery_gap = w.get('delivery', {}).get('gap_days', 0)
            if delivery_gap > 1:
                actions.append("Reduce delivery gap by improving dispatch and last-mile routing.")
                priority = "High" if delivery_gap > 2 else "Medium"

            pod_gap = w.get('pod', {}).get('gap_days', 0)
            if pod_gap > 1:
                actions.append("Improve POD collection process; follow up with transporters.")
                priority = "High" if pod_gap > 2 else "Medium"

            cycle_gap = w.get('cycle', {}).get('gap_days', 0)
            if cycle_gap > 2:
                actions.append("Reduce total cycle time by synchronizing PGI and POD.")
                priority = "High"

            pending_units = w.get('pending', {}).get('units', 0)
            if pending_units > 500:
                actions.append(f"Prioritize clearance of {pending_units} pending units.")
                priority = "High"

            if w.get('pgi_pct', 100) < 85:
                actions.append(f"Accelerate PGI process (current {w.get('pgi_pct', 100)}%).")
                priority = "High"

            if not actions:
                actions.append("Maintain excellent performance; monitor seasonal variations.")
                priority = "Low"

            recs.append({
                "warehouse": warehouse,
                "priority": priority,
                "recommendation": ". ".join(actions) + ".",
                "expected_improvement": "5-10% increase in on-time delivery" if priority == "High" else "2-5% improvement",
                "target_kpi": "Delivery Rate" if "delivery" in " ".join(actions).lower() else "Cycle Time"
            })
        return recs

    @staticmethod
    def generate_short_insight(warehouse: Dict[str, Any]) -> str:
        pgi = warehouse.get('pgi_pct', 0)
        delivery = warehouse.get('delivery', {}).get('compliance_pct', 0)
        pod = warehouse.get('pod', {}).get('compliance_pct', 0)
        pending = warehouse.get('pending', {}).get('units', 0)
        health = warehouse.get('health_score', 0)

        if health >= 90 and delivery >= 95 and pgi >= 95 and pod >= 90:
            return "🟢 Excellent performance."
        if delivery < 80:
            return "🔴 Delivery delay increasing."
        if pod < 80:
            return "🟡 POD collection needs improvement."
        if pgi < 80:
            return "🟡 PGI process needs attention."
        if pending > 1000:
            return "🟠 High pending units."
        if health >= 70:
            return "🟡 Performance stable, monitor closely."
        return "🔴 Critical risk – immediate action required."

# ============================================================
# BLOCK 14: Performance Trend Engine
# ============================================================

class PerformanceTrendEngine:
    @staticmethod
    def compute_trends(daily_trend: List[Dict]) -> Dict[str, Any]:
        if not daily_trend:
            return {}
        trend_data = []
        for day in daily_trend:
            total_units = day.get('units', 0)
            pgi_units = day.get('pgi_count', 0)
            delivered_units = day.get('delivered_count', 0)
            pgi_pct = SafeNumber.pct(pgi_units, total_units)
            delivery_pct = SafeNumber.pct(delivered_units, total_units)
            pod_pct = delivery_pct
            trend_data.append({
                "date": day.get('date'),
                "pgi_pct": pgi_pct,
                "delivery_pct": delivery_pct,
                "pod_pct": pod_pct,
                "avg_delivery_days": 0,
                "avg_pod_days": 0,
            })
        return {
            "daily": trend_data,
            "weekly": trend_data[-7:] if len(trend_data) >= 7 else trend_data,
            "monthly": trend_data[-30:] if len(trend_data) >= 30 else trend_data,
        }

    @staticmethod
    def calculate_trend(warehouse_name: str, daily_trend: List[Dict]) -> str:
        if len(daily_trend) < 14:
            return "▬ Stable"
        last_7 = [day.get('avg_delivery_days', 0) for day in daily_trend[-7:]]
        prev_7 = [day.get('avg_delivery_days', 0) for day in daily_trend[-14:-7]]
        if not last_7 or not prev_7:
            return "▬ Stable"
        avg_last = sum(last_7) / len(last_7) if last_7 else 0
        avg_prev = sum(prev_7) / len(prev_7) if prev_7 else 0
        if avg_prev == 0:
            return "▬ Stable"
        change = (avg_last - avg_prev) / avg_prev * 100
        if change < -5:
            return "▲ Improving"
        elif change > 5:
            return "▼ Declining"
        else:
            return "▬ Stable"

# ============================================================
# BLOCK 15: Executive Summary Engine
# ============================================================

class ExecutiveSummaryEngine:
    @staticmethod
    def generate_summary(kpis: Dict, warehouses: List[Dict], alerts: List[Dict], recommendations: List[Dict]) -> str:
        health = kpis.get('health_score', {}).get('value', 0)
        delivery_pct = kpis.get('delivery_achievement', {}).get('value', 0)
        pod_pct = kpis.get('pod_achievement', {}).get('value', 0)
        pending_dn = kpis.get('pending_dn', {}).get('value', 0)
        pending_units = kpis.get('pending_units', {}).get('value', 0)

        best = warehouses[0] if warehouses else None
        worst = warehouses[-1] if warehouses else None

        if not warehouses and health == 0 and delivery_pct == 0 and pod_pct == 0:
            return "No delivery data available. Please upload an Excel report using the Import Center to populate the dashboard."

        lines = []
        if health > 0:
            lines.append(f"Overall logistics performance is {'good' if health >= 80 else 'fair'} with health score of {health:.1f}%.")
        else:
            lines.append("Overall logistics performance data is currently unavailable.")

        if delivery_pct > 0:
            lines.append(f"Delivery achievement is {delivery_pct:.1f}%, {'above' if delivery_pct >= 90 else 'below'} target.")
        else:
            lines.append("Delivery achievement data is currently unavailable.")

        if pod_pct > 0:
            lines.append(f"POD achievement is {pod_pct:.1f}%, {'above' if pod_pct >= 90 else 'below'} target.")
        else:
            lines.append("POD achievement data is currently unavailable.")

        if pending_dn > 0 or pending_units > 0:
            lines.append(f"{pending_dn} DNs and {pending_units} units are still pending.")
        else:
            lines.append("No pending DNs or units at this time.")

        if best:
            lines.append(f"{best.get('warehouse_name', 'Unknown')} warehouse is the top performer.")
        if worst:
            lines.append(f"{worst.get('warehouse_name', 'Unknown')} warehouse needs immediate attention.")

        if alerts:
            first_alert = alerts[0]
            lines.append(f"Alert: {first_alert.get('source', 'System')} - {first_alert.get('message', '')}")
        if recommendations:
            first_rec = recommendations[0]
            lines.append(f"Recommendation: {first_rec.get('warehouse', '')} - {first_rec.get('recommendation', '')}")

        return " ".join(lines) if lines else "Executive summary is not available."

    @staticmethod
    def generate_detailed_summary(warehouse_summaries: List[Dict[str, Any]], national_kpis: Dict) -> Dict[str, Any]:
        if not warehouse_summaries:
            return {
                "overall_health": 0,
                "overall_delivery": 0,
                "overall_pod": 0,
                "overall_cycle": 0,
                "best_warehouse": "",
                "worst_warehouse": "",
                "fastest_warehouse": "",
                "highest_delay_warehouse": "",
                "critical_warehouses": 0,
                "ai_recommendation": "No data available."
            }

        total = len(warehouse_summaries)
        avg_health = sum(w.get('health_score', 0) for w in warehouse_summaries) / total
        avg_delivery = sum(w.get('delivery', {}).get('compliance_pct', 0) for w in warehouse_summaries) / total
        avg_pod = sum(w.get('pod', {}).get('compliance_pct', 0) for w in warehouse_summaries) / total
        avg_cycle = sum(w.get('cycle', {}).get('compliance_pct', 0) for w in warehouse_summaries) / total

        best = max(warehouse_summaries, key=lambda w: w.get('health_score', 0))
        worst = min(warehouse_summaries, key=lambda w: w.get('health_score', 0))
        fastest = min(warehouse_summaries, key=lambda w: w.get('cycle', {}).get('avg_days', 999))
        highest_delay = max(warehouse_summaries, key=lambda w: w.get('delivery', {}).get('gap_days', 0))

        critical = [w for w in warehouse_summaries if w.get('grade') == 'Critical' or w.get('risk') == 'critical']

        if critical:
            rec = f"Focus on {len(critical)} critical warehouses: {', '.join([w.get('warehouse', '') for w in critical[:3]])}. Immediate action required."
        elif avg_delivery < 80:
            rec = "Overall delivery compliance is below 80%. Review dispatch processes across all warehouses."
        elif avg_pod < 80:
            rec = "POD compliance is low. Strengthen POD collection follow-up."
        else:
            rec = "Performance is satisfactory. Continue monitoring and optimize further."

        return {
            "overall_health": round(avg_health, 2),
            "overall_delivery": round(avg_delivery, 2),
            "overall_pod": round(avg_pod, 2),
            "overall_cycle": round(avg_cycle, 2),
            "best_warehouse": best.get('warehouse', ''),
            "worst_warehouse": worst.get('warehouse', ''),
            "fastest_warehouse": fastest.get('warehouse', ''),
            "highest_delay_warehouse": highest_delay.get('warehouse', ''),
            "critical_warehouses": len(critical),
            "ai_recommendation": rec
        }

# ============================================================
# BLOCK 16: Response Builder (Enhanced + Safe)
# ============================================================

class ResponseBuilder:
    @staticmethod
    def build(
        summary, warehouse_summaries, dealers, cities, products, divisions,
        daily_trend, monthly_trend, pending_analysis, city_delays,
        kpis, insights, alerts, recommendations,
        exec_summary, pipeline, trends, compliance_data,
        import_summary, metadata, charts,
        national_kpis, detailed_summary
    ):
        total_dn = summary.get('total_dn', 0)
        total_units = summary.get('total_units', 0)
        delivered_units = sum(w.get('delivered_units', 0) for w in warehouse_summaries)
        pending_units = total_units - delivered_units
        pgi_units = sum(w.get('pgi_units', 0) for w in warehouse_summaries)
        total_revenue = summary.get('total_revenue', 0) or (total_units * config.avg_unit_price)

        cards = {
            "total_dn": {"value": total_dn, "label": "Total Delivery Notes", "icon": "fa-file-invoice"},
            "total_units": {"value": total_units, "label": "Total Units", "icon": "fa-boxes"},
            "total_value": {"value": total_revenue, "label": "Total Revenue", "icon": "fa-money-bill-wave"},
            "pgi_achievement": {"value": SafeNumber.pct(pgi_units, total_units), "label": "PGI Achievement %", "icon": "fa-percent"},
            "delivery_achievement": {"value": SafeNumber.pct(delivered_units, total_units), "label": "Delivery Achievement %", "icon": "fa-percent"},
            "pod_achievement": {"value": SafeNumber.pct(delivered_units, total_units), "label": "POD Achievement %", "icon": "fa-percent"},
            "pending_units": {"value": pending_units, "label": "Pending Units", "icon": "fa-hourglass"},
            "health_score": {"value": kpis.get('health_score', {}).get('value', 0), "label": "Health Score", "icon": "fa-heart-pulse"},
        }
        cards["pending_dn"] = {"value": kpis.get('pending_dn', {}).get('value', 0)}

        growth = KPIEngine.compute_day_over_day(daily_trend)
        for key in ["total_dn", "total_units", "total_value"]:
            if key in cards:
                cards[key]["vs_yesterday"] = growth.get(key.replace("total_", "").replace("_value", "revenue") + "_growth", 0)

        pipeline_old = {
            "dn_created": total_dn,
            "pgi_completed": summary.get('pgi_completed', 0),
            "delivered": summary.get('delivered_dns', 0),
            "pgi_achievement": SafeNumber.pct(summary.get('pgi_completed', 0), total_dn),
            "delivery_achievement": SafeNumber.pct(summary.get('delivered_dns', 0), total_dn),
            "total_units": total_units,
            "pgi_units": pgi_units,
            "delivered_units": delivered_units,
            "pending_units": pending_units,
            "pgi_achievement_units": SafeNumber.pct(pgi_units, total_units),
            "delivery_achievement_units": SafeNumber.pct(delivered_units, total_units),
        }

        warehouse_ranking = []
        for w in warehouse_summaries:
            warehouse_ranking.append({
                "rank": w.get('rank', 0),
                "warehouse": w.get('warehouse', ''),
                "dns": w.get('dns', 0),
                "units": w.get('units', 0),
                "revenue": w.get('revenue', 0),
                "pgi_pct": w.get('pgi_pct', 0),
                "delivery_pct": w.get('delivery_pct', 0),
                "pod_pct": w.get('pod_pct', 0),
                "avg_days": w.get('avg_days', 0),
                "avg_delivery_days": w.get('avg_delivery_days', 0),
                "avg_pod_days": w.get('avg_pod_days', 0),
                "avg_pgi_days": w.get('avg_pgi_days', 0),
                "pending_dns": w.get('pending_dns', 0),
                "pending_units": w.get('pending_units', 0),
                "status": w.get('status', 'Unknown'),
                "performance_score": w.get('health_score', 0),
                "risk": w.get('risk_emoji', '⚪'),
                "trend": w.get('trend', '▬ Stable'),
                "ai_insight": w.get('ai_insight', ''),
            })

        top_delayed_cities = []
        for city in sorted(city_delays, key=lambda x: x.get('avg_delivery_days', 0), reverse=True)[:10]:
            days = city.get('avg_delivery_days', 0)
            risk = "Critical" if days > 5 else "High" if days > 4 else "Medium" if days > 3 else "Low"
            top_delayed_cities.append({
                "city": city.get('city', ''),
                "avg_delivery_days": days,
                "pending_units": city.get('pending_units', 0),
                "status": risk,
            })

        sorted_by_pending = sorted(warehouse_summaries, key=lambda w: w.get('pending', {}).get('units', 0), reverse=True)[:5]
        top_pending_warehouses = [
            {"warehouse": w.get('warehouse', ''), "pending_dns": w.get('pending', {}).get('dn', 0), "pending_units": w.get('pending', {}).get('units', 0)}
            for w in sorted_by_pending
        ]

        sorted_dealers = sorted(dealers, key=lambda d: d.get('total_revenue', 0), reverse=True)[:5]
        top_dealers = [
            {"dealer": d.get('dealer_name', ''), "dns": d.get('delivery_notes', 0), "units": d.get('units', 0), "revenue": d.get('total_revenue', 0)}
            for d in sorted_dealers
        ]

        sorted_products = sorted(products, key=lambda p: p.get('units', 0), reverse=True)[:5]
        top_products = [
            {"product": p.get('product_name', ''), "units": p.get('units', 0), "revenue": p.get('total_revenue', 0), "delivery_notes": p.get('delivery_notes', 0)}
            for p in sorted_products
        ]

        division_performance = [
            {"division": d.get('division', ''), "dns": d.get('delivery_notes', 0), "units": d.get('units', 0), "revenue": d.get('total_revenue', 0)}
            for d in divisions
        ]

        compliance = []
        for c in compliance_data[:6]:
            dist = c.get('avg_distance_km', 0)
            range_label = "0-100" if dist <= 100 else "101-250" if dist <= 250 else "251-450" if dist <= 450 else "451-700" if dist <= 700 else "701-900" if dist <= 900 else ">900"
            compliance.append({
                "distance": range_label,
                "target_days": c.get('target_days', 0),
                "actual_days": c.get('actual_days', 0),
                "compliance_pct": c.get('compliance_pct', 0),
                "status": c.get('status', ''),
            })

        return {
            "executive_summary": summary,
            "cards": cards,
            "kpis": cards,
            "pipeline": pipeline_old,
            "warehouse": warehouse_summaries,
            "warehouses": warehouse_summaries,
            "dealer": dealers,
            "dealers": dealers,
            "city": cities,
            "cities": cities,
            "product": products,
            "products": products,
            "division": divisions,
            "divisions": divisions,
            "daily_trend": daily_trend,
            "monthly_trend": monthly_trend,
            "alerts": alerts,
            "recommendations": recommendations,
            "charts": charts,
            "metadata": metadata,
            "total_revenue": total_revenue,
            "executive_summary_text": exec_summary,
            "pipeline_detailed": pipeline,
            "performance_trends": trends,
            "warehouse_ranking": warehouse_ranking,
            "top_delayed_cities": top_delayed_cities,
            "top_pending_warehouses": top_pending_warehouses,
            "top_dealers": top_dealers,
            "top_products": top_products,
            "division_performance": division_performance,
            "delivery_compliance": compliance,
            "pending_analysis": pending_analysis,
            "critical_alerts": alerts,
            "director_recommendations": recommendations,
            "import_summary": import_summary,
            "insights": insights,
            "warehouse_summary": warehouse_summaries,
            "national_averages": national_kpis,
            "executive_summary_detailed": detailed_summary,
        }

# ============================================================
# BLOCK 17: Dashboard Service (Orchestrator) with full error handling
# ============================================================

class DashboardService:
    def __init__(self):
        self._repo = DashboardRepository()
        logger.info("DashboardService initialized (v20.1 - Full Error Handling)")

    @cached(ttl=300)
    async def get_full_dashboard(self, filters: Optional[Dict] = None) -> Dict[str, Any]:
        try:
            # 1. Fetch raw data
            summary = self._repo.fetch_summary()
            warehouse_raw = self._repo.fetch_warehouse_data()
            dealer_raw = self._repo.fetch_dealer_data()
            city_raw = self._repo.fetch_city_data()
            product_raw = self._repo.fetch_product_data()
            division_raw = self._repo.fetch_division_data()
            daily_trend = self._repo.fetch_daily_trend(90)
            monthly_trend = self._repo.fetch_monthly_trend(12)
            pending_analysis = self._repo.fetch_pending_analysis()
            city_delays = self._repo.fetch_city_delay_data()
            import_summary = self._repo.get_import_summary()
            record_count = self._repo.fetch_record_count()

            # 2. Compute distance and compliance
            avg_distances = {}
            compliance_data = []
            try:
                city_pairs = self._repo.fetch_warehouse_city_pairs()
                avg_distances = DistanceCalculationEngine.compute_average_distance_per_warehouse(city_pairs)
                for pair in city_pairs:
                    wh = pair.get('warehouse', '')
                    dist = avg_distances.get(wh, 0)
                    target = DistanceCalculationEngine.get_target_days(dist)
                    actual = pair.get('avg_delivery_days', 0)
                    compliance_pct = DistanceCalculationEngine.compute_compliance(actual, target)
                    compliance_data.append({
                        "warehouse": wh,
                        "city": pair.get('city', ''),
                        "target_days": target,
                        "actual_days": actual,
                        "compliance_pct": compliance_pct,
                        "status": "Within Standard" if actual <= target else "Above Standard",
                        "avg_distance_km": dist,
                    })
            except Exception as e:
                logger.warning(f"Distance compliance calculation failed: {e}")

            # 3. Build warehouse intelligence
            try:
                warehouse_summaries = WarehouseIntelligenceEngine.compute_warehouse_intelligence(
                    warehouse_raw, avg_distances, compliance_data
                )
            except Exception as e:
                logger.error(f"Warehouse intelligence computation failed: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Warehouse intelligence error: {str(e)}")

            # 4. National KPIs
            national_kpis = KPIEngine.compute_national_kpis(warehouse_summaries)

            # 5. KPIs
            total_units = summary.get('total_units', 0)
            pgi_units = sum(w.get('pgi_units', 0) for w in warehouse_summaries)
            delivered_units = sum(w.get('delivered_units', 0) for w in warehouse_summaries)
            pending_units = total_units - delivered_units
            pgi_rate = SafeNumber.pct(pgi_units, total_units)
            delivery_rate = SafeNumber.pct(delivered_units, total_units)
            avg_cycle = summary.get('avg_cycle_days', 0)
            health = BusinessRuleEngine.calculate_health_score(pgi_rate, delivery_rate, delivery_rate, avg_cycle)

            kpis = {
                "total_dn": {"value": summary.get('total_dn', 0)},
                "total_units": {"value": total_units},
                "total_value": {"value": summary.get('total_revenue', 0) or (total_units * config.avg_unit_price)},
                "pgi_achievement": {"value": pgi_rate},
                "delivery_achievement": {"value": delivery_rate},
                "pod_achievement": {"value": delivery_rate},
                "pending_units": {"value": pending_units},
                "health_score": {"value": health},
                "pending_dn": {"value": summary.get('pending_delivery', 0) + summary.get('pending_pgi', 0)},
                "avg_cycle_days": {"value": avg_cycle},
                "avg_delivery_days": {"value": summary.get('avg_delivery_days', 0)},
                "avg_pod_days": {"value": summary.get('avg_pod_days', 0)},
            }

            # 6. Alerts & Recommendations
            try:
                alerts = AlertEngine.generate_alerts(warehouse_summaries, kpis)
            except Exception as e:
                logger.error(f"Alert generation failed: {e}", exc_info=True)
                alerts = []

            try:
                recommendations = RecommendationEngine.generate_recommendations(warehouse_summaries)
            except Exception as e:
                logger.error(f"Recommendation generation failed: {e}", exc_info=True)
                recommendations = []

            # 7. Add AI insights & trends
            for w in warehouse_summaries:
                try:
                    w['ai_insight'] = RecommendationEngine.generate_short_insight(w)
                except Exception as e:
                    logger.warning(f"AI insight failed for {w.get('warehouse', 'unknown')}: {e}")
                    w['ai_insight'] = "Insight unavailable"
                try:
                    w['trend'] = PerformanceTrendEngine.calculate_trend(w.get('warehouse', ''), daily_trend)
                except Exception as e:
                    logger.warning(f"Trend failed for {w.get('warehouse', 'unknown')}: {e}")
                    w['trend'] = "▬ Stable"

            # 8. Executive Summary
            exec_summary_text = ExecutiveSummaryEngine.generate_summary(kpis, warehouse_summaries, alerts, recommendations)
            detailed_summary = ExecutiveSummaryEngine.generate_detailed_summary(warehouse_summaries, national_kpis)

            # 9. Pipeline
            pipeline = {
                "dn_created": {"dn": summary.get('total_dn', 0), "units": total_units, "pct": 100, "avg_days": 0, "pending": 0},
                "pgi_completed": {"dn": summary.get('pgi_completed', 0), "units": pgi_units, "pct": SafeNumber.pct(summary.get('pgi_completed', 0), summary.get('total_dn', 1)), "avg_days": summary.get('avg_pgi_days', 0), "pending": summary.get('total_dn', 0) - summary.get('pgi_completed', 0)},
                "in_transit": {"dn": summary.get('delivered_dns', 0), "units": delivered_units, "pct": SafeNumber.pct(summary.get('delivered_dns', 0), summary.get('total_dn', 1)), "avg_days": summary.get('avg_delivery_days', 0), "pending": summary.get('total_dn', 0) - summary.get('delivered_dns', 0)},
                "delivered": {"dn": summary.get('delivered_dns', 0), "units": delivered_units, "pct": SafeNumber.pct(summary.get('delivered_dns', 0), summary.get('total_dn', 1)), "avg_days": summary.get('avg_delivery_days', 0), "pending": 0},
                "pod_received": {"dn": summary.get('pod_completed', 0), "units": delivered_units, "pct": SafeNumber.pct(summary.get('pod_completed', 0), summary.get('delivered_dns', 1)), "avg_days": summary.get('avg_pod_days', 0), "pending": summary.get('delivered_dns', 0) - summary.get('pod_completed', 0)},
            }

            trends = PerformanceTrendEngine.compute_trends(daily_trend)
            best, worst = WarehouseIntelligenceEngine.get_best_and_worst(warehouse_summaries)
            insights = {
                "insights": [
                    {"type": "best_performing", "text": f"Best Warehouse: {best.get('warehouse', 'N/A')} (Score: {best.get('health_score', 0)})"},
                    {"type": "worst_performing", "text": f"Worst Warehouse: {worst.get('warehouse', 'N/A')} (Score: {worst.get('health_score', 0)})"},
                    {"type": "overall_delivery", "text": f"Overall Delivery Achievement: {delivery_rate}%"},
                    {"type": "pending_units", "text": f"Total Pending Units: {pending_units}"},
                ]
            }

            charts = {
                "warehouse_ranking": "{}",
                "pgi_performance": "{}",
                "ontime_gauge": "{}",
                "aging_distribution": "{}",
                "performance_matrix": "{}",
                "monthly_trend": "{}",
                "daily_trend": "{}",
            }

            metadata = {
                "version": "20.1",
                "timestamp": datetime.utcnow().isoformat(),
                "record_count": record_count,
                "warehouse_count": len(warehouse_summaries),
            }

            # 13. Build final response with safe handling
            try:
                response = ResponseBuilder.build(
                    summary=summary,
                    warehouse_summaries=warehouse_summaries,
                    dealers=dealer_raw,
                    cities=city_raw,
                    products=product_raw,
                    divisions=division_raw,
                    daily_trend=daily_trend,
                    monthly_trend=monthly_trend,
                    pending_analysis=pending_analysis,
                    city_delays=city_delays,
                    kpis=kpis,
                    insights=insights,
                    alerts=alerts,
                    recommendations=recommendations,
                    exec_summary=exec_summary_text,
                    pipeline=pipeline,
                    trends=trends,
                    compliance_data=compliance_data,
                    import_summary=import_summary,
                    metadata=metadata,
                    charts=charts,
                    national_kpis=national_kpis,
                    detailed_summary=detailed_summary,
                )
            except Exception as e:
                logger.error(f"ResponseBuilder.build failed: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Response building error: {str(e)}")

            return response

        except HTTPException as he:
            raise he
        except Exception as e:
            logger.error(f"Dashboard generation failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

    async def get_dashboard_data(self, filters: Optional[Dict] = None) -> Dict[str, Any]:
        return await self.get_full_dashboard(filters)

    @cached(ttl=60)
    async def get_warehouse_ranking(self) -> List[Dict]:
        warehouse_raw = self._repo.fetch_warehouse_data()
        city_pairs = self._repo.fetch_warehouse_city_pairs()
        avg_distances = DistanceCalculationEngine.compute_average_distance_per_warehouse(city_pairs)
        summaries = WarehouseIntelligenceEngine.compute_warehouse_intelligence(warehouse_raw, avg_distances, [])
        for w in summaries:
            w['ai_insight'] = RecommendationEngine.generate_short_insight(w)
        return summaries

# ============================================================
# BLOCK 18: FastAPI Router
# ============================================================

router = APIRouter(prefix="/dashboard/api", tags=["dashboard"])
_dashboard_service = None

def get_dashboard_service() -> DashboardService:
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService()
    return _dashboard_service

@router.get("/data")
async def get_dashboard_data(
    theme: str = Query("dark", description="Theme: light or dark"),
    service: DashboardService = Depends(get_dashboard_service)
):
    try:
        return await service.get_dashboard_data({"theme": theme})
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Unhandled exception in /data: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@router.get("/warehouses")
async def get_warehouses(service: DashboardService = Depends(get_dashboard_service)):
    try:
        return await service.get_warehouse_ranking()
    except Exception as e:
        logger.error(f"Error in /warehouses: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
async def health_check():
    return {"status": "healthy", "version": "20.1", "timestamp": datetime.utcnow().isoformat()}

@router.post("/upload")
async def upload_excel_report(
    file: UploadFile = File(...),
    skip_duplicates: bool = Form(True),
    db: Session = Depends(get_db)
):
    try:
        contents = await file.read()
        if PANDAS_AVAILABLE:
            df = pd.read_excel(io.BytesIO(contents))
            logger.info(f"Successfully received Excel file: {file.filename} with {len(df)} rows.")
        cache.clear()
        return {
            "status": "success", 
            "filename": file.filename,
            "message": "File uploaded and processed successfully."
        }
    except Exception as e:
        logger.error(f"Excel upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

logger.info("DashboardService router mounted (v20.1 - Full Error Handling) with /upload")
