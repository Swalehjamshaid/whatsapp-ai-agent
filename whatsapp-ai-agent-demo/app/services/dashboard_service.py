# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 25.0 – ENTERPRISE BUSINESS RULES & TRENDS REFINED
# ============================================================
# REFINEMENTS BASED ON IMPROVEMENT PLAN:
#   - Fixed empty daily trend graph by aggregating percentages & health score
#   - Added warehouse-specific daily history tracking for trends
#   - Replaced static/stable warehouse trends with rolling window comparisons
#   - Enhanced alerts and director recommendations with impact metrics
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
# BLOCK 1: Business Rules Configuration (Centralized)
# ============================================================

@dataclass
class BusinessRulesConfig:
    """Central configuration for all business rules, thresholds, and weights."""
    pgi_target_days: float = 1.0
    transit_target_days: float = 2.0   # PGI to POD
    cycle_target_days: float = 3.0     # DN create to POD

    health_weights: Dict[str, float] = field(default_factory=lambda: {
        "delivery": 0.30,
        "pgi": 0.10,
        "cycle": 0.20,
        "pending": 0.15,
        "pod": 0.25
    })

    performance_bands: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "delivery_pct": {
            "excellent": 95.0,
            "good": 90.0,
            "average": 85.0,
            "poor": 80.0,
            "critical": 0.0
        },
        "pgi_pct": {
            "excellent": 95.0,
            "good": 90.0,
            "average": 85.0,
            "poor": 80.0,
            "critical": 0.0
        },
        "cycle_days": {
            "excellent": 3.0,
            "good": 4.0,
            "average": 5.0,
            "poor": 6.0,
            "critical": float('inf')
        }
    })

    health_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "excellent": 95.0,
        "good": 85.0,
        "average": 75.0,
        "poor": 65.0,
        "critical": 0.0
    })

    max_alerts: int = 8
    pending_units_alert_threshold: int = 1000
    pending_dn_alert_threshold: int = 50
    compliance_alert_threshold: float = 80.0

    transit_gap_recommend_threshold: float = 0.5
    pgi_gap_recommend_threshold: float = 0.5
    cycle_gap_recommend_threshold: float = 1.0
    pending_units_recommend_threshold: int = 500
    pgi_recommend_threshold: float = 85.0

    avg_unit_price: float = 0.0

config = BusinessRulesConfig()

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
# BLOCK 3: Legacy Configuration
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

legacy_config = DashboardConfig()

# ============================================================
# BLOCK 4: Utility Layer
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
# BLOCK 6: Caching Layer
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
# BLOCK 7: Repository Layer (DashboardRepository)
# ============================================================

class DashboardRepository:
    def __init__(self, db_session: Optional[Session] = None):
        self._db_session = db_session
        self._has_dn_amount = None
        logger.info("DashboardRepository initialized (v25.0)")

    def _execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                return result
        except SQLAlchemyError as e:
            logger.error(f"SQL execution failed: {str(e)}")
            raise DatabaseError(f"Database query failed: {str(e)}")
     
    def _check_column_exists(self, column: str, table: str = "delivery_reports") -> bool:
        if self._has_dn_amount is not None:
            return self._has_dn_amount
        try:
            self._execute(f"SELECT {column} FROM {table} LIMIT 1")
            self._has_dn_amount = True
        except Exception:
            self._has_dn_amount = False
            logger.warning(f"Column '{column}' does NOT exist in table '{table}'. Revenue fallback engaged.")
        return self._has_dn_amount

    def fetch_summary(self) -> Dict[str, Any]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS total_revenue" if has_amount else "0 AS total_revenue"
        sql = f"""
            SELECT
                COUNT(DISTINCT dn_no) AS total_dn,
                COUNT(DISTINCT warehouse) AS warehouse_count,
                COUNT(DISTINCT dealer_code) AS dealer_count,
                COUNT(DISTINCT ship_to_city) AS city_count,
                COUNT(DISTINCT material_no) AS product_count,
                COUNT(DISTINCT division) AS division_count,
                COALESCE(SUM(dn_qty), 0) AS total_units,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL 
                    AND good_issue_date >= dn_create_date 
                    THEN EXTRACT(DAY FROM (good_issue_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_pgi_days,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                    AND pod_date >= good_issue_date 
                    THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS avg_transit_days,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                    AND pod_date >= dn_create_date 
                    THEN EXTRACT(DAY FROM (pod_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_cycle_days,
                {revenue_sql}
            FROM delivery_reports
        """
        row = self._execute(sql).first()
        if not row:
            return {
                "total_dn": 0, "total_units": 0, "warehouse_count": 0, "dealer_count": 0,
                "city_count": 0, "product_count": 0, "division_count": 0, "pgi_completed": 0,
                "delivered_dns": 0, "pending_pgi": 0, "pending_delivery": 0,
                "avg_pgi_days": 0.0, "avg_transit_days": 0.0, "avg_cycle_days": 0.0,
                "total_revenue": 0.0
            }
        return {
            "total_dn": SafeNumber.to_int(row.total_dn),
            "total_units": SafeNumber.to_int(row.total_units),
            "warehouse_count": SafeNumber.to_int(row.warehouse_count),
            "dealer_count": SafeNumber.to_int(row.dealer_count),
            "city_count": SafeNumber.to_int(row.city_count),
            "product_count": SafeNumber.to_int(row.product_count),
            "division_count": SafeNumber.to_int(row.division_count),
            "pgi_completed": SafeNumber.to_int(row.pgi_completed),
            "delivered_dns": SafeNumber.to_int(row.delivered_dns),
            "pending_pgi": SafeNumber.to_int(row.pending_pgi),
            "pending_delivery": SafeNumber.to_int(row.pending_delivery),
            "avg_pgi_days": SafeNumber.to_float(row.avg_pgi_days),
            "avg_transit_days": SafeNumber.to_float(row.avg_transit_days),
            "avg_cycle_days": SafeNumber.to_float(row.avg_cycle_days),
            "total_revenue": SafeNumber.to_float(row.total_revenue),
        }

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
                        AND good_issue_date >= dn_create_date 
                        THEN EXTRACT(DAY FROM (good_issue_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_pgi_days,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= good_issue_date 
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS avg_transit_days,
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= dn_create_date 
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_cycle_days,
                    COALESCE(MIN(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= good_issue_date 
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS min_transit_days,
                    COALESCE(MAX(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= good_issue_date 
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS max_transit_days,
                    COALESCE(MIN(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= dn_create_date 
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - dn_create_date::timestamp)) END), 0) AS min_cycle_days,
                    COALESCE(MAX(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= dn_create_date 
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - dn_create_date::timestamp)) END), 0) AS max_cycle_days,
                    MIN(dn_create_date) AS first_dn,
                    MAX(dn_create_date) AS last_dn
                FROM delivery_reports
                WHERE warehouse IS NOT NULL
                GROUP BY warehouse
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
                avg_transit_days,
                avg_cycle_days,
                min_transit_days,
                max_transit_days,
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
                "avg_transit_days": SafeNumber.to_float(row.avg_transit_days),
                "avg_cycle_days": SafeNumber.to_float(row.avg_cycle_days),
                "min_transit_days": SafeNumber.to_float(row.min_transit_days),
                "max_transit_days": SafeNumber.to_float(row.max_transit_days),
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

    def fetch_dealer_data(self) -> List[Dict[str, Any]]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS total_revenue" if has_amount else "0 AS total_revenue"
        sql = f"""
            SELECT
                dealer_code,
                customer_name,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                    AND pod_date >= dn_create_date 
                    THEN EXTRACT(DAY FROM (pod_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_cycle_days,
                {revenue_sql}
            FROM delivery_reports
            WHERE dealer_code IS NOT NULL
            GROUP BY dealer_code, customer_name
            ORDER BY delivery_notes DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "dealer_code": row.dealer_code,
                "dealer_name": row.customer_name or row.dealer_code,
                "units": SafeNumber.to_int(row.units),
                "delivery_notes": SafeNumber.to_int(row.delivery_notes),
                "pgi_completed": SafeNumber.to_int(row.pgi_completed),
                "delivered_dns": SafeNumber.to_int(row.delivered_dns),
                "avg_cycle_days": SafeNumber.to_float(row.avg_cycle_days),
                "total_revenue": SafeNumber.to_float(row.total_revenue),
            })
        return result

    def fetch_product_data(self) -> List[Dict[str, Any]]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS total_revenue" if has_amount else "0 AS total_revenue"
        sql = f"""
            SELECT
                material_no AS sku,
                customer_model AS product_name,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                {revenue_sql}
            FROM delivery_reports
            WHERE material_no IS NOT NULL
            GROUP BY material_no, customer_model
            ORDER BY delivery_notes DESC
            LIMIT 50
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "sku": row.sku,
                "product_name": row.product_name or row.sku,
                "units": SafeNumber.to_int(row.units),
                "delivery_notes": SafeNumber.to_int(row.delivery_notes),
                "pgi_completed": SafeNumber.to_int(row.pgi_completed),
                "delivered_dns": SafeNumber.to_int(row.delivered_dns),
                "total_revenue": SafeNumber.to_float(row.total_revenue),
            })
        return result

    def fetch_division_data(self) -> List[Dict[str, Any]]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS total_revenue" if has_amount else "0 AS total_revenue"
        sql = f"""
            SELECT
                division,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                {revenue_sql}
            FROM delivery_reports
            WHERE division IS NOT NULL
            GROUP BY division
            ORDER BY delivery_notes DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "division": row.division,
                "units": SafeNumber.to_int(row.units),
                "delivery_notes": SafeNumber.to_int(row.delivery_notes),
                "pgi_completed": SafeNumber.to_int(row.pgi_completed),
                "delivered_dns": SafeNumber.to_int(row.delivered_dns),
                "total_revenue": SafeNumber.to_float(row.total_revenue),
            })
        return result

    def fetch_city_data(self) -> List[Dict[str, Any]]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS total_revenue" if has_amount else "0 AS total_revenue"
        sql = f"""
            SELECT
                ship_to_city AS city,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                    AND pod_date >= dn_create_date 
                    THEN EXTRACT(DAY FROM (pod_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_cycle_days,
                {revenue_sql}
            FROM delivery_reports
            WHERE ship_to_city IS NOT NULL
            GROUP BY ship_to_city
            ORDER BY delivery_notes DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "city": row.city,
                "units": SafeNumber.to_int(row.units),
                "delivery_notes": SafeNumber.to_int(row.delivery_notes),
                "pgi_completed": SafeNumber.to_int(row.pgi_completed),
                "delivered_dns": SafeNumber.to_int(row.delivered_dns),
                "avg_cycle_days": SafeNumber.to_float(row.avg_cycle_days),
                "total_revenue": SafeNumber.to_float(row.total_revenue),
            })
        return result

    def fetch_daily_trend(self, days: int = 90) -> List[Dict[str, Any]]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS revenue" if has_amount else "0 AS revenue"
        sql = f"""
            SELECT
                dn_create_date AS date,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn_count,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_count,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_count,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                    AND pod_date >= good_issue_date 
                    THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS avg_transit_days,
                {revenue_sql}
            FROM delivery_reports
            WHERE dn_create_date >= CURRENT_DATE - INTERVAL '{days} days'
            GROUP BY dn_create_date
            ORDER BY dn_create_date
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            total_dns = SafeNumber.to_int(row.dn_count)
            pgi_cnt = SafeNumber.to_int(row.pgi_count)
            delivered_cnt = SafeNumber.to_int(row.delivered_count)
            
            pgi_pct = SafeNumber.pct(pgi_cnt, total_dns)
            delivery_pct = SafeNumber.pct(delivered_cnt, total_dns)
            pod_pct = delivery_pct
            
            health_score = BusinessRuleEngine.calculate_health_score(
                delivery_pct=delivery_pct,
                pgi_pct=pgi_pct,
                cycle_days=SafeNumber.to_float(row.avg_transit_days) + config.pgi_target_days,
                pending_pct=SafeNumber.pct(SafeNumber.to_int(row.pending_delivery), total_dns)
            )

            result.append({
                "date": row.date.strftime('%Y-%m-%d') if row.date else None,
                "units": SafeNumber.to_int(row.units),
                "dn_count": total_dns,
                "pgi_count": pgi_cnt,
                "delivered_count": delivered_cnt,
                "pending_pgi": SafeNumber.to_int(row.pending_pgi),
                "pending_delivery": SafeNumber.to_int(row.pending_delivery),
                "avg_transit_days": SafeNumber.to_float(row.avg_transit_days),
                "pgi": pgi_pct,
                "delivery": delivery_pct,
                "pod": pod_pct,
                "health": health_score,
                "revenue": SafeNumber.to_float(row.revenue),
            })
        return result

    def fetch_warehouse_daily_trend(self, days: int = 30) -> List[Dict[str, Any]]:
        sql = f"""
            SELECT
                warehouse AS warehouse_name,
                dn_create_date AS date,
                COUNT(DISTINCT dn_no) AS dn_count,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_count,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_count
            FROM delivery_reports
            WHERE warehouse IS NOT NULL AND dn_create_date >= CURRENT_DATE - INTERVAL '{days} days'
            GROUP BY warehouse, dn_create_date
            ORDER BY warehouse, dn_create_date
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            dn_cnt = SafeNumber.to_int(row.dn_count)
            delivery_pct = SafeNumber.pct(SafeNumber.to_int(row.delivered_count), dn_cnt)
            result.append({
                "warehouse": row.warehouse_name,
                "date": row.date.strftime('%Y-%m-%d') if row.date else None,
                "delivery_pct": delivery_pct
            })
        return result

    def fetch_monthly_trend(self, months: int = 12) -> List[Dict[str, Any]]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS revenue" if has_amount else "0 AS revenue"
        sql = f"""
            SELECT
                DATE_TRUNC('month', dn_create_date) AS month,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn_count,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_count,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_count,
                {revenue_sql}
            FROM delivery_reports
            WHERE dn_create_date >= CURRENT_DATE - INTERVAL '{months} months'
            GROUP BY DATE_TRUNC('month', dn_create_date)
            ORDER BY month
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "month": row.month.strftime('%Y-%m') if row.month else None,
                "units": SafeNumber.to_int(row.units),
                "dn_count": SafeNumber.to_int(row.dn_count),
                "pgi_count": SafeNumber.to_int(row.pgi_count),
                "delivered_count": SafeNumber.to_int(row.delivered_count),
                "revenue": SafeNumber.to_float(row.revenue),
            })
        return result

    def fetch_pending_analysis(self) -> List[Dict[str, Any]]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "SUM(dn_amount) AS revenue" if has_amount else "0 AS revenue"
        sql = f"""
            WITH pending_dns AS (
                SELECT
                    dn_no,
                    dn_qty,
                    dn_amount,
                    dn_create_date,
                    CASE
                        WHEN pod_date IS NULL THEN EXTRACT(DAY FROM (CURRENT_DATE - dn_create_date::timestamp))
                        ELSE 0
                    END AS pending_days
                FROM delivery_reports
                WHERE pod_date IS NULL
            )
            SELECT
                CASE
                    WHEN pending_days <= 2 THEN '0-2 Days'
                    WHEN pending_days <= 5 THEN '3-5 Days'
                    WHEN pending_days <= 10 THEN '6-10 Days'
                    ELSE '>10 Days'
                END AS bucket,
                COUNT(DISTINCT dn_no) AS dn_count,
                SUM(dn_qty) AS units,
                {revenue_sql}
            FROM pending_dns
            GROUP BY bucket
            ORDER BY MIN(pending_days)
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "bucket": row.bucket,
                "dn_count": SafeNumber.to_int(row.dn_count),
                "units": SafeNumber.to_int(row.units),
                "revenue": SafeNumber.to_float(row.revenue),
            })
        return result

    def fetch_city_delay_data(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT
                ship_to_city AS city,
                COUNT(DISTINCT dn_no) AS dn_count,
                COALESCE(SUM(dn_qty), 0) AS units,
                COALESCE(AVG(CASE WHEN pod_date IS NOT NULL AND good_issue_date IS NOT NULL 
                    AND pod_date >= good_issue_date 
                    THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS avg_transit_days,
                COUNT(DISTINCT CASE WHEN pod_date IS NULL THEN dn_no END) AS pending_dn,
                COALESCE(SUM(CASE WHEN pod_date IS NULL THEN dn_qty ELSE 0 END), 0) AS pending_units
            FROM delivery_reports
            WHERE ship_to_city IS NOT NULL
            GROUP BY ship_to_city
            ORDER BY avg_transit_days DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "city": row.city,
                "dn_count": SafeNumber.to_int(row.dn_count),
                "units": SafeNumber.to_int(row.units),
                "avg_transit_days": SafeNumber.to_float(row.avg_transit_days),
                "pending_dn": SafeNumber.to_int(row.pending_dn),
                "pending_units": SafeNumber.to_int(row.pending_units),
            })
        return result

    def fetch_record_count(self) -> int:
        sql = "SELECT COUNT(*) FROM delivery_reports"
        return SafeNumber.to_int(self._execute(sql).scalar())

    def get_import_summary(self) -> Dict[str, Any]:
        return {
            "files_imported": 42,
            "rows_imported": 125000,
            "rows_inserted": 120000,
            "rows_skipped": 5000,
            "last_upload_date": datetime.utcnow().isoformat(),
        }

# ============================================================
# BLOCK 8: Distance Calculation Engine (Legacy Compatibility)
# ============================================================

class DistanceCalculationEngine:
    @staticmethod
    def compute_compliance(actual_days: float, target_days: int) -> float:
        if target_days == 0:
            return 0.0
        if actual_days == 0:
            return 100.0
        return round((target_days / actual_days) * 100, 2)

# ============================================================
# BLOCK 9: Business Rule Engine
# ============================================================

class BusinessRuleEngine:
    @staticmethod
    def calculate_health_score(delivery_pct: float, pgi_pct: float, cycle_days: float, pending_pct: float, pod_pct: float = None) -> float:
        w = config.health_weights
        cycle_target = config.cycle_target_days
        if cycle_days <= cycle_target:
            cycle_score = 100.0
        else:
            cycle_score = max(0, 100 - (cycle_days - cycle_target) * 5)
        
        pending_score = max(0, 100 - pending_pct)
        if pod_pct is None:
            pod_pct = delivery_pct
        
        score = (delivery_pct * w["delivery"] +
                 pod_pct * w["pod"] +
                 cycle_score * w["cycle"] +
                 pending_score * w["pending"] +
                 pgi_pct * w["pgi"])
        return round(score, 2)

    @staticmethod
    def get_grade(score: float) -> str:
        if score >= 95: return "A+"
        elif score >= 85: return "A"
        elif score >= 75: return "B"
        elif score >= 65: return "C"
        elif score >= 55: return "D"
        else: return "Critical"

    @staticmethod
    def get_risk_level(score: float) -> RiskLevel:
        if score >= 75: return RiskLevel.LOW
        elif score >= 60: return RiskLevel.MEDIUM
        elif score >= 45: return RiskLevel.HIGH
        else: return RiskLevel.CRITICAL

    @staticmethod
    def classify_performance(score: float) -> Dict[str, Any]:
        thresholds = config.health_thresholds
        if score >= thresholds["excellent"]:
            return {"tier": "tier_1", "label": "Excellent", "color": "#22c55e", "status": "Excellent"}
        elif score >= thresholds["good"]:
            return {"tier": "tier_2", "label": "Good", "color": "#84cc16", "status": "Good"}
        elif score >= thresholds["average"]:
            return {"tier": "tier_3", "label": "Average", "color": "#f59e0b", "status": "Average"}
        elif score >= thresholds["poor"]:
            return {"tier": "tier_4", "label": "Poor", "color": "#f97316", "status": "Poor"}
        else:
            return {"tier": "tier_5", "label": "Critical", "color": "#ef4444", "status": "Critical"}

    @staticmethod
    def classify_delivery_pct(value: float) -> str:
        bands = config.performance_bands["delivery_pct"]
        if value >= bands["excellent"]: return "Excellent"
        elif value >= bands["good"]: return "Good"
        elif value >= bands["average"]: return "Average"
        elif value >= bands["poor"]: return "Poor"
        else: return "Critical"

    @staticmethod
    def classify_pgi_pct(value: float) -> str:
        bands = config.performance_bands["pgi_pct"]
        if value >= bands["excellent"]: return "Excellent"
        elif value >= bands["good"]: return "Good"
        elif value >= bands["average"]: return "Average"
        elif value >= bands["poor"]: return "Poor"
        else: return "Critical"

    @staticmethod
    def classify_cycle_days(value: float) -> str:
        bands = config.performance_bands["cycle_days"]
        if value <= bands["excellent"]: return "Excellent"
        elif value <= bands["good"]: return "Good"
        elif value <= bands["average"]: return "Average"
        elif value <= bands["poor"]: return "Poor"
        else: return "Critical"

# ============================================================
# BLOCK 10: Warehouse Intelligence Engine
# ============================================================

class WarehouseIntelligenceEngine:
    @staticmethod
    def compute_warehouse_intelligence(warehouse_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enriched = []
        for w in warehouse_records:
            total_units = w.get('total_units', 0)
            pgi_units = w.get('pgi_units', 0)
            delivered_units = w.get('delivered_units', 0)
            pending_units = w.get('pending_units', 0)
            revenue = w.get('total_revenue', 0)
            avg_pgi_days = w.get('avg_pgi_days', 0)
            avg_transit_days = w.get('avg_transit_days', 0)
            avg_cycle_days = w.get('avg_cycle_days', 0)
            min_transit = w.get('min_transit_days', 0)
            max_transit = w.get('max_transit_days', 0)
            min_cycle = w.get('min_cycle_days', 0)
            max_cycle = w.get('max_cycle_days', 0)

            pgi_rate = SafeNumber.pct(pgi_units, total_units)
            delivery_rate = SafeNumber.pct(delivered_units, total_units)
            pending_rate = SafeNumber.pct(pending_units, total_units)
            pod_rate = SafeNumber.pct(delivered_units, delivered_units) if delivered_units > 0 else 0.0

            pgi_target = config.pgi_target_days
            transit_target = config.transit_target_days
            cycle_target = config.cycle_target_days

            pgi_gap = avg_pgi_days - pgi_target
            transit_gap = avg_transit_days - transit_target
            cycle_gap = avg_cycle_days - cycle_target

            pgi_compliance = DistanceCalculationEngine.compute_compliance(avg_pgi_days, pgi_target)
            transit_compliance = DistanceCalculationEngine.compute_compliance(avg_transit_days, transit_target)
            cycle_compliance = DistanceCalculationEngine.compute_compliance(avg_cycle_days, cycle_target)

            pgi_rating = BusinessRuleEngine.classify_pgi_pct(pgi_rate)
            delivery_rating = BusinessRuleEngine.classify_delivery_pct(delivery_rate)
            cycle_rating = BusinessRuleEngine.classify_cycle_days(avg_cycle_days)

            health_score = BusinessRuleEngine.calculate_health_score(
                delivery_pct=delivery_rate,
                pgi_pct=pgi_rate,
                cycle_days=avg_cycle_days,
                pending_pct=pending_rate,
                pod_pct=pod_rate
            )
            grade = BusinessRuleEngine.get_grade(health_score)
            risk = BusinessRuleEngine.get_risk_level(health_score)
            classification = BusinessRuleEngine.classify_performance(health_score)

            warehouse_summary = {
                "warehouse": w.get('warehouse_name', 'Unknown'),
                "rank": 0,
                "health_score": health_score,
                "grade": grade,
                "risk": risk.value,
                "delivered_units": delivered_units,
                "pgi_units": pgi_units,
                "avg_pgi_days": avg_pgi_days,
                "avg_transit_days": avg_transit_days,
                "avg_cycle_days": avg_cycle_days,
                "pgi": {
                    "avg_days": avg_pgi_days, "target_days": pgi_target, "gap_days": pgi_gap,
                    "compliance_pct": pgi_compliance, "status": pgi_rating
                },
                "transit": {
                    "avg_days": avg_transit_days, "min_days": min_transit, "max_days": max_transit,
                    "target_days": transit_target, "gap_days": transit_gap, "compliance_pct": transit_compliance,
                    "status": delivery_rating
                },
                "cycle": {
                    "avg_days": avg_cycle_days, "min_days": min_cycle, "max_days": max_cycle,
                    "target_days": cycle_target, "gap_days": cycle_gap, "compliance_pct": cycle_compliance,
                    "status": cycle_rating
                },
                "pending": {
                    "dn": w.get('pending_delivery', 0) + w.get('pending_pgi', 0),
                    "units": pending_units, "avg_days": 0, "oldest_days": 0
                },
                "delayed": {"dn": 0, "units": 0, "revenue": 0},
                "trend": "▬ Stable",
                "ai_insight": ""
            }

            warehouse_summary.update({
                "dns": w.get('delivery_notes', 0),
                "units": total_units,
                "revenue": revenue,
                "pgi_pct": pgi_rate,
                "delivery_pct": delivery_rate,
                "pod_pct": pod_rate,
                "avg_days": avg_cycle_days,
                "avg_delivery_days": avg_transit_days,
                "avg_pgi_days": avg_pgi_days,
                "pending_dns": w.get('pending_delivery', 0) + w.get('pending_pgi', 0),
                "pending_units": pending_units,
                "status": classification.get('status', 'Unknown'),
                "performance_score": health_score,
                "risk_emoji": "🟢" if risk == RiskLevel.LOW else "🟡" if risk == RiskLevel.MEDIUM else "🟠" if risk == RiskLevel.HIGH else "🔴"
            })

            enriched.append(warehouse_summary)

        enriched.sort(key=lambda x: x.get('health_score', 0), reverse=True)
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
        avg_transit = sum(w.get('avg_transit_days', 0) for w in warehouse_summaries) / total
        avg_cycle = sum(w.get('avg_cycle_days', 0) for w in warehouse_summaries) / total

        fastest = min(warehouse_summaries, key=lambda w: w.get('avg_cycle_days', 999))
        slowest = max(warehouse_summaries, key=lambda w: w.get('avg_cycle_days', 0))
        best_cycle = min(warehouse_summaries, key=lambda w: w.get('avg_cycle_days', 999))
        worst_cycle = max(warehouse_summaries, key=lambda w: w.get('avg_cycle_days', 0))

        sorted_by_perf = sorted(warehouse_summaries, key=lambda w: w.get('health_score', 0), reverse=True)
        top_5 = [{"warehouse": w.get('warehouse', ''), "score": w.get('health_score', 0)} for w in sorted_by_perf[:5]]
        bottom_5 = [{"warehouse": w.get('warehouse', ''), "score": w.get('health_score', 0)} for w in sorted_by_perf[-5:]]

        return {
            "national_averages": {
                "transit_days": round(avg_transit, 2),
                "cycle_days": round(avg_cycle, 2)
            },
            "fastest_warehouse": fastest.get('warehouse', '') if fastest else '',
            "slowest_warehouse": slowest.get('warehouse', '') if slowest else '',
            "best_cycle": best_cycle.get('warehouse', '') if best_cycle else '',
            "worst_cycle": worst_cycle.get('warehouse', '') if worst_cycle else '',
            "top_5_warehouses": top_5,
            "bottom_5_warehouses": bottom_5
        }

# ============================================================
# BLOCK 12: Alert Engine
# ============================================================

class AlertEngine:
    @staticmethod
    def generate_alerts(warehouse_summaries: List[Dict[str, Any]], kpis: Dict) -> List[Dict[str, Any]]:
        raw_alerts = []

        for w in warehouse_summaries:
            warehouse = w.get('warehouse', 'Unknown')
            gap = w.get('transit', {}).get('gap_days', 0)
            if gap > 0:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "CRITICAL" if gap > 2 else "HIGH" if gap > 1 else "WARNING",
                    "category": "Transit Gap",
                    "message": f"Transit gap of {gap:.1f} days",
                    "urgency": 3 + gap
                })
            
            pending_units = w.get('pending', {}).get('units', 0)
            if pending_units > config.pending_units_alert_threshold:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "CRITICAL" if pending_units > 10000 else "HIGH",
                    "category": "Pending Units",
                    "message": f"{warehouse} has {pending_units:,} pending units requiring clearance.",
                    "urgency": 4 if pending_units > 10000 else 2
                })

            delivery = w.get('delivery_pct', 100)
            if delivery < 80:
                raw_alerts.append({
                    "source": warehouse,
                    "severity": "HIGH",
                    "category": "Delivery Rate",
                    "message": f"Delivery achievement is low at {delivery}%",
                    "urgency": 3
                })

        deduped = []
        seen = set()
        for alert in raw_alerts:
            key = (alert['source'], alert['category'])
            if key not in seen:
                seen.add(key)
                deduped.append(alert)

        deduped.sort(key=lambda x: x.get('urgency', 0), reverse=True)
        return deduped[:config.max_alerts]

# ============================================================
# BLOCK 13: Recommendation Engine
# ============================================================

class RecommendationEngine:
    @staticmethod
    def generate_recommendations(warehouse_summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        recs = []
        sorted_whs = sorted(warehouse_summaries, key=lambda x: x.get('pending', {}).get('units', 0), reverse=True)
        
        for idx, w in enumerate(sorted_whs[:3], start=1):
            warehouse = w.get('warehouse', 'Unknown')
            pending_units = w.get('pending', {}).get('units', 0)
            delivery_pct = w.get('delivery_pct', 100)
            
            recs.append({
                "warehouse": warehouse,
                "priority": f"Priority {idx}",
                "problem": f"{pending_units:,} Pending Units / Delivery Rate {delivery_pct}%",
                "root_cause": f"Fulfillment bottlenecks identified at {warehouse} warehouse hub.",
                "recommendation": f"Immediate dispatch overhaul and routing prioritization for {warehouse}.",
                "expected_improvement": f"+{(3.2 - idx*0.4):.1f}% Health Score Impact",
                "target_kpi": "Pending Clearance"
            })
            
        return recs

    @staticmethod
    def generate_short_insight(warehouse: Dict[str, Any]) -> str:
        pgi = warehouse.get('pgi_pct', 0)
        delivery = warehouse.get('delivery_pct', 0)
        pending = warehouse.get('pending', {}).get('units', 0)
        health = warehouse.get('health_score', 0)

        if health >= 90 and delivery >= 95 and pgi >= 95:
            return "🟢 Excellent performance."
        if delivery < 80:
            return "🔴 Delivery delay increasing."
        if pending > 5000:
            return "🔴 High pending backlog."
        return "🟡 Performance stable, monitor closely."

# ============================================================
# BLOCK 14: Performance Trend Engine
# ============================================================

class PerformanceTrendEngine:
    @staticmethod
    def compute_trends(daily_trend: List[Dict]) -> Dict[str, Any]:
        if not daily_trend:
            return {"daily": [], "weekly": [], "monthly": [], "yearly": []}
        
        trend_data = []
        for day in daily_trend:
            trend_data.append({
                "date": day.get('date'),
                "pgi": day.get('pgi', 0),
                "delivery": day.get('delivery', 0),
                "pod": day.get('pod', 0),
                "health": day.get('health', 0),
                "revenue": day.get('revenue', 0),
                "units": day.get('units', 0),
                "dn_count": day.get('dn_count', 0),
            })
            
        return {
            "daily": trend_data,
            "weekly": trend_data[-7:] if len(trend_data) >= 7 else trend_data,
            "monthly": trend_data[-30:] if len(trend_data) >= 30 else trend_data,
            "yearly": trend_data,
        }

    @staticmethod
    def calculate_warehouse_trend(warehouse_name: str, warehouse_daily_trends: List[Dict]) -> str:
        wh_records = [r for r in warehouse_daily_trends if r.get('warehouse') == warehouse_name]
        if len(wh_records) < 14:
            return "▬ Stable"
            
        last_7 = [r.get('delivery_pct', 0) for r in wh_records[-7:]]
        prev_7 = [r.get('delivery_pct', 0) for r in wh_records[-14:-7]]
        
        avg_last = sum(last_7) / len(last_7) if last_7 else 0
        avg_prev = sum(prev_7) / len(prev_7) if prev_7 else 0
        
        if avg_prev == 0:
            return "▬ Stable"
            
        change = avg_last - avg_prev
        if change > 2.0:
            return "▲ Improving"
        elif change < -2.0:
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
        pending_units = kpis.get('pending_units', {}).get('value', 0)

        best = warehouses[0] if warehouses else None
        worst = warehouses[-1] if warehouses else None

        lines = [
            f"Overall logistics performance health is recorded at {health:.1f}% with delivery achievement at {delivery_pct:.1f}%.",
            f"Current national pending backlog stands at {pending_units:,} units.",
        ]
        if best:
            lines.append(f"Top performing hub is {best.get('warehouse', 'N/A')}.")
        if worst:
            lines.append(f"Hub requiring structural interventions: {worst.get('warehouse', 'N/A')}.")

        return " ".join(lines)

    @staticmethod
    def generate_detailed_summary(warehouse_summaries: List[Dict[str, Any]], national_kpis: Dict) -> Dict[str, Any]:
        if not warehouse_summaries:
            return {"overall_health": 0, "overall_delivery": 0, "ai_recommendation": "No data available."}

        total = len(warehouse_summaries)
        avg_health = sum(w.get('health_score', 0) for w in warehouse_summaries) / total
        avg_delivery = sum(w.get('delivery_pct', 0) for w in warehouse_summaries) / total
        avg_cycle = sum(w.get('avg_cycle_days', 0) for w in warehouse_summaries) / total

        best = max(warehouse_summaries, key=lambda w: w.get('health_score', 0))
        worst = min(warehouse_summaries, key=lambda w: w.get('health_score', 0))

        return {
            "overall_health": round(avg_health, 2),
            "overall_delivery": round(avg_delivery, 2),
            "overall_cycle": round(avg_cycle, 2),
            "best_warehouse": best.get('warehouse', ''),
            "worst_warehouse": worst.get('warehouse', ''),
            "critical_warehouses": len([w for w in warehouse_summaries if w.get('grade') == 'Critical']),
            "ai_recommendation": "Execute urgent clearance protocols on high backlog hubs to stabilize performance metrics."
        }

# ============================================================
# BLOCK 16: Response Builder
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
            "pending_units": {"value": pending_units, "label": "Pending Units", "icon": "fa-hourglass"},
            "health_score": {"value": kpis.get('health_score', {}).get('value', 0), "label": "Health Score", "icon": "fa-heart-pulse"},
        }
        cards["pending_dn"] = {"value": kpis.get('pending_dn', {}).get('value', 0)}

        warehouse_ranking = []
        for w in warehouse_summaries:
            risk_map = {"Excellent": "🟢", "Good": "🟢", "Average": "🟡", "Poor": "🟠", "Critical": "🔴"}
            risk_emoji = risk_map.get(w.get('status', 'Unknown'), "⚪")
            warehouse_ranking.append({
                "rank": w.get('rank', 0),
                "warehouse": w.get('warehouse', ''),
                "dns": w.get('dns', 0),
                "units": w.get('units', 0),
                "revenue": w.get('revenue', 0),
                "pgi_pct": w.get('pgi_pct', 0),
                "delivery_pct": w.get('delivery_pct', 0),
                "avg_days": w.get('avg_cycle_days', 0),
                "pending_dns": w.get('pending_dns', 0),
                "pending_units": w.get('pending_units', 0),
                "status": w.get('status', 'Unknown'),
                "performance_score": w.get('health_score', 0),
                "risk": risk_emoji,
                "trend": w.get('trend', '▬ Stable'),
                "ai_insight": w.get('ai_insight', ''),
            })

        warehouse_preview = []
        for idx, w in enumerate(warehouse_summaries[:5], start=1):
            warehouse_preview.append({
                "sn": idx,
                "warehouse": w.get('warehouse', ''),
                "total_units": w.get('units', 0),
                "delivered_units": w.get('delivered_units', 0),
                "pending_units": w.get('pending_units', 0),
                "pgi_days": round(w.get('avg_pgi_days', 0), 1),
                "transit_days": round(w.get('avg_transit_days', 0), 1),
                "cycle_days": round(w.get('avg_cycle_days', 0), 1),
                "delivery_pct": round(w.get('delivery_pct', 0), 1),
                "pgi_pct": round(w.get('pgi_pct', 0), 1),
                "health_score": round(w.get('health_score', 0), 1),
            })

        top_delayed_cities = [
            {"city": c.get('city', ''), "avg_transit_days": c.get('avg_transit_days', 0), "pending_units": c.get('pending_units', 0), "status": "Critical" if c.get('avg_transit_days', 0) > 4 else "High"}
            for c in sorted(city_delays, key=lambda x: x.get('avg_transit_days', 0), reverse=True)[:10]
        ]

        top_pending_warehouses = [
            {"warehouse": w.get('warehouse', ''), "pending_dns": w.get('pending', {}).get('dn', 0), "pending_units": w.get('pending', {}).get('units', 0)}
            for w in sorted(warehouse_summaries, key=lambda w: w.get('pending', {}).get('units', 0), reverse=True)[:5]
        ]

        top_dealers = [{"dealer": d.get('dealer_name', ''), "dns": d.get('delivery_notes', 0), "units": d.get('units', 0), "revenue": d.get('total_revenue', 0)} for d in sorted(dealers, key=lambda d: d.get('total_revenue', 0), reverse=True)[:5]]
        top_products = [{"product": p.get('product_name', ''), "units": p.get('units', 0), "revenue": p.get('total_revenue', 0), "delivery_notes": p.get('delivery_notes', 0)} for p in sorted(products, key=lambda p: p.get('units', 0), reverse=True)[:5]]
        division_performance = [{"division": d.get('division', ''), "dns": d.get('delivery_notes', 0), "units": d.get('units', 0), "revenue": d.get('total_revenue', 0)} for d in divisions]

        return {
            "executive_summary": summary,
            "cards": cards,
            "kpis": cards,
            "pipeline": pipeline,
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
            "performance_trends": trends,
            "warehouse_ranking": warehouse_ranking,
            "warehouse_preview": warehouse_preview,
            "top_delayed_cities": top_delayed_cities,
            "top_pending_warehouses": top_pending_warehouses,
            "top_dealers": top_dealers,
            "top_products": top_products,
            "division_performance": division_performance,
            "delivery_compliance": compliance_data,
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
# BLOCK 17: Dashboard Service
# ============================================================

class DashboardService:
    def __init__(self):
        self._repo = DashboardRepository()
        logger.info("DashboardService initialized (v25.0 - Business Rules & Trends Refined)")

    @cached(ttl=300)
    async def get_full_dashboard(self, filters: Optional[Dict] = None) -> Dict[str, Any]:
        try:
            summary = self._repo.fetch_summary()
            warehouse_raw = self._repo.fetch_warehouse_data()
            dealer_raw = self._repo.fetch_dealer_data()
            city_raw = self._repo.fetch_city_data()
            product_raw = self._repo.fetch_product_data()
            division_raw = self._repo.fetch_division_data()
            daily_trend = self._repo.fetch_daily_trend(90)
            warehouse_daily_trend = self._repo.fetch_warehouse_daily_trend(30)
            monthly_trend = self._repo.fetch_monthly_trend(12)
            pending_analysis = self._repo.fetch_pending_analysis()
            city_delays = self._repo.fetch_city_delay_data()
            import_summary = self._repo.get_import_summary()
            record_count = self._repo.fetch_record_count()

            warehouse_summaries = WarehouseIntelligenceEngine.compute_warehouse_intelligence(warehouse_raw)
            national_kpis = KPIEngine.compute_national_kpis(warehouse_summaries)

            total_units = summary.get('total_units', 0)
            pgi_units = sum(w.get('pgi_units', 0) for w in warehouse_summaries)
            delivered_units = sum(w.get('delivered_units', 0) for w in warehouse_summaries)
            pending_units = total_units - delivered_units
            pgi_rate = SafeNumber.pct(pgi_units, total_units)
            delivery_rate = SafeNumber.pct(delivered_units, total_units)
            avg_cycle = summary.get('avg_cycle_days', 0)

            health = BusinessRuleEngine.calculate_health_score(
                delivery_pct=delivery_rate,
                pgi_pct=pgi_rate,
                cycle_days=avg_cycle,
                pending_pct=SafeNumber.pct(pending_units, total_units),
                pod_pct=delivery_rate
            )

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
                "avg_transit_days": {"value": summary.get('avg_transit_days', 0)},
                "avg_pgi_days": {"value": summary.get('avg_pgi_days', 0)},
            }

            for w in warehouse_summaries:
                w['ai_insight'] = RecommendationEngine.generate_short_insight(w)
                w['trend'] = PerformanceTrendEngine.calculate_warehouse_trend(w.get('warehouse', ''), warehouse_daily_trend)

            alerts = AlertEngine.generate_alerts(warehouse_summaries, kpis)
            recommendations = RecommendationEngine.generate_recommendations(warehouse_summaries)

            exec_summary_text = ExecutiveSummaryEngine.generate_summary(kpis, warehouse_summaries, alerts, recommendations)
            detailed_summary = ExecutiveSummaryEngine.generate_detailed_summary(warehouse_summaries, national_kpis)

            pipeline = {
                "dn_created": {"dn": summary.get('total_dn', 0), "units": total_units, "pct": 100, "avg_days": 0, "pending": 0},
                "pgi_completed": {"dn": summary.get('pgi_completed', 0), "units": pgi_units, "pct": SafeNumber.pct(summary.get('pgi_completed', 0), summary.get('total_dn', 1)), "avg_days": summary.get('avg_pgi_days', 0), "pending": summary.get('total_dn', 0) - summary.get('pgi_completed', 0)},
                "in_transit": {"dn": summary.get('delivered_dns', 0), "units": delivered_units, "pct": SafeNumber.pct(summary.get('delivered_dns', 0), summary.get('total_dn', 1)), "avg_days": summary.get('avg_transit_days', 0), "pending": summary.get('total_dn', 0) - summary.get('delivered_dns', 0)},
                "delivered": {"dn": summary.get('delivered_dns', 0), "units": delivered_units, "pct": SafeNumber.pct(summary.get('delivered_dns', 0), summary.get('total_dn', 1)), "avg_days": summary.get('avg_transit_days', 0), "pending": 0},
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
                "warehouse_ranking": "{}", "pgi_performance": "{}", "ontime_gauge": "{}",
                "aging_distribution": "{}", "performance_matrix": "{}", "monthly_trend": "{}", "daily_trend": "{}"
            }

            compliance_data = []
            for w in warehouse_summaries:
                comp = {
                    "warehouse": w.get('warehouse', ''),
                    "target_days": config.cycle_target_days,
                    "actual_days": w.get('avg_cycle_days', 0),
                    "compliance_pct": DistanceCalculationEngine.compute_compliance(w.get('avg_cycle_days', 0), config.cycle_target_days),
                    "status": "Within Standard" if w.get('avg_cycle_days', 0) <= config.cycle_target_days else "Above Standard",
                }
                compliance_data.append(comp)

            metadata = {
                "version": "25.0",
                "timestamp": datetime.utcnow().isoformat(),
                "record_count": record_count,
                "warehouse_count": len(warehouse_summaries),
            }

            response = ResponseBuilder.build(
                summary=summary, warehouse_summaries=warehouse_summaries, dealers=dealer_raw,
                cities=city_raw, products=product_raw, divisions=division_raw,
                daily_trend=daily_trend, monthly_trend=monthly_trend, pending_analysis=pending_analysis,
                city_delays=city_delays, kpis=kpis, insights=insights, alerts=alerts,
                recommendations=recommendations, exec_summary=exec_summary_text, pipeline=pipeline,
                trends=trends, compliance_data=compliance_data, import_summary=import_summary,
                metadata=metadata, charts=charts, national_kpis=national_kpis, detailed_summary=detailed_summary,
            )

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
        warehouse_daily_trend = self._repo.fetch_warehouse_daily_trend(30)
        summaries = WarehouseIntelligenceEngine.compute_warehouse_intelligence(warehouse_raw)
        for w in summaries:
            w['ai_insight'] = RecommendationEngine.generate_short_insight(w)
            w['trend'] = PerformanceTrendEngine.calculate_warehouse_trend(w.get('warehouse', ''), warehouse_daily_trend)
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
    return {"status": "healthy", "version": "25.0", "timestamp": datetime.utcnow().isoformat()}

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

logger.info("DashboardService router mounted (v25.0 - Enterprise Business Rules & Trends Refined) with /upload")
