# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 26.0 – ENTERPRISE DASHBOARD (No New Tables)
# ============================================================
# IMPROVEMENTS IMPLEMENTED (NO NEW TABLES):
#   - Enhanced daily_trend query returns all KPIs (health, delivery%, PGI%, etc.)
#   - Warehouse-specific trend using daily aggregates from delivery_reports (last 14 days)
#   - Multi-metric trend data (health, delivery, PGI, pending, revenue, units)
#   - Smart alerts based on pending units, delivery, PGI, cycle compliance
#   - Director recommendations with priority and expected KPI impact
#   - Excel preview returns actual uploaded data with validation
#   - Health score unified with configurable weights
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
    # Target days (fixed, no distance-based logic)
    pgi_target_days: float = 1.0
    transit_target_days: float = 2.0   # PGI to POD
    cycle_target_days: float = 3.0     # DN create to POD

    # Performance weights for Health Score (must sum to 100)
    health_weights: Dict[str, float] = field(default_factory=lambda: {
        "delivery": 0.30,   # Delivery Achievement %
        "pgi": 0.10,        # PGI Achievement %
        "cycle": 0.20,      # Cycle Score (based on cycle days)
        "pending": 0.15,    # Pending Score (100 - pending %)
        "pod": 0.25         # POD Achievement (same as delivery for now, but kept for extensibility)
    })

    # Performance bands for KPIs
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

    # Health score classification thresholds
    health_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "excellent": 95.0,
        "good": 85.0,
        "average": 75.0,
        "poor": 65.0,
        "critical": 0.0
    })

    # Alert thresholds
    max_alerts: int = 8
    pending_units_alert_threshold: int = 1000
    pending_dn_alert_threshold: int = 50
    compliance_alert_threshold: float = 80.0  # For cycle compliance

    # Recommendation thresholds
    transit_gap_recommend_threshold: float = 0.5
    pgi_gap_recommend_threshold: float = 0.5
    cycle_gap_recommend_threshold: float = 1.0
    pending_units_recommend_threshold: int = 500
    pgi_recommend_threshold: float = 85.0

    # Average unit price for revenue fallback
    avg_unit_price: float = 0.0


# Instantiate configuration (can be overridden via environment or DB later)
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
# BLOCK 3: Legacy Configuration (kept for backward compat)
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

class DashboardRepository:
    def __init__(self, db_session: Optional[Session] = None):
        self._db_session = db_session
        self._has_dn_amount = None
        logger.info("DashboardRepository initialized (v26.0)")

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
            logger.info(f"Column '{column}' exists in table '{table}'.")
        except Exception:
            self._has_dn_amount = False
            logger.warning(f"Column '{column}' does NOT exist in table '{table}'. Revenue will use avg_unit_price fallback.")
        return self._has_dn_amount

    # ---------- Core Summary ----------
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
                -- PGI Days (good_issue_date - dn_create_date)
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL 
                    AND good_issue_date >= dn_create_date  -- data validation
                    THEN EXTRACT(DAY FROM (good_issue_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_pgi_days,
                -- Transit Days (pod_date - good_issue_date) [formerly delivery days]
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                    AND pod_date >= good_issue_date  -- data validation
                    THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS avg_transit_days,
                -- Cycle Days (pod_date - dn_create_date)
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                    AND pod_date >= dn_create_date  -- data validation
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

    # ---------- Warehouse Data (aggregated per warehouse) - FIXED with validation ----------
    def fetch_warehouse_data(self) -> List[Dict[str, Any]]:
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS total_revenue" if has_amount else "0 AS total_revenue"
        # Use EXTRACT with validation
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
                    -- PGI Days (validated)
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL 
                        AND good_issue_date >= dn_create_date
                        THEN EXTRACT(DAY FROM (good_issue_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_pgi_days,
                    -- Transit Days (validated)
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= good_issue_date
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS avg_transit_days,
                    -- Cycle Days (validated)
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= dn_create_date
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_cycle_days,
                    -- Min/Max for transit
                    COALESCE(MIN(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= good_issue_date
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS min_transit_days,
                    COALESCE(MAX(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= good_issue_date
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS max_transit_days,
                    -- Min/Max for cycle
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

    # ---------- Dealer Data ----------
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

    # ---------- Product Data ----------
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

    # ---------- Division Data ----------
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

    # ---------- City Data ----------
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

    # ---------- Enhanced Daily Trend (returns all KPIs) ----------
    def fetch_daily_trend_detailed(self, days: int = 90) -> List[Dict[str, Any]]:
        """
        Returns daily aggregated metrics including calculated percentages and health score.
        """
        has_amount = self._check_column_exists("dn_amount")
        revenue_sql = "COALESCE(SUM(dn_amount), 0) AS revenue" if has_amount else "0 AS revenue"
        sql = f"""
            WITH daily_agg AS (
                SELECT
                    dn_create_date AS date,
                    COALESCE(SUM(dn_qty), 0) AS total_units,
                    COUNT(DISTINCT dn_no) AS dn_count,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL THEN dn_qty ELSE 0 END), 0) AS pgi_units,
                    COALESCE(SUM(CASE WHEN pod_date IS NOT NULL THEN dn_qty ELSE 0 END), 0) AS delivered_units,
                    COALESCE(SUM(CASE WHEN pod_date IS NULL THEN dn_qty ELSE 0 END), 0) AS pending_units,
                    {revenue_sql},
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL 
                        AND good_issue_date >= dn_create_date
                        THEN EXTRACT(DAY FROM (good_issue_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_pgi_days,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= good_issue_date
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS avg_transit_days,
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= dn_create_date
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_cycle_days
                FROM delivery_reports
                WHERE dn_create_date >= CURRENT_DATE - INTERVAL '{days} days'
                GROUP BY dn_create_date
            )
            SELECT
                date,
                total_units,
                dn_count,
                pgi_units,
                delivered_units,
                pending_units,
                revenue,
                avg_pgi_days,
                avg_transit_days,
                avg_cycle_days,
                CASE WHEN total_units > 0 THEN ROUND((pgi_units * 100.0 / total_units), 2) ELSE 0 END AS pgi_pct,
                CASE WHEN total_units > 0 THEN ROUND((delivered_units * 100.0 / total_units), 2) ELSE 0 END AS delivery_pct,
                CASE WHEN total_units > 0 THEN ROUND((pending_units * 100.0 / total_units), 2) ELSE 0 END AS pending_pct,
                -- Compute health score using same formula as BusinessRuleEngine
                CASE 
                    WHEN total_units > 0 THEN 
                        ROUND(
                            (CASE WHEN total_units > 0 THEN (delivered_units * 100.0 / total_units) ELSE 0 END) * 0.30 +
                            (CASE WHEN total_units > 0 THEN (pgi_units * 100.0 / total_units) ELSE 0 END) * 0.10 +
                            GREATEST(0, 100 - (avg_cycle_days - 1) * 5) * 0.20 +
                            (100 - (CASE WHEN total_units > 0 THEN (pending_units * 100.0 / total_units) ELSE 0 END)) * 0.15 +
                            (CASE WHEN total_units > 0 THEN (delivered_units * 100.0 / total_units) ELSE 0 END) * 0.25
                        , 2)
                    ELSE 0
                END AS health_score
            FROM daily_agg
            ORDER BY date
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "date": row.date.strftime('%Y-%m-%d') if row.date else None,
                "total_units": SafeNumber.to_int(row.total_units),
                "dn_count": SafeNumber.to_int(row.dn_count),
                "pgi_units": SafeNumber.to_int(row.pgi_units),
                "delivered_units": SafeNumber.to_int(row.delivered_units),
                "pending_units": SafeNumber.to_int(row.pending_units),
                "revenue": SafeNumber.to_float(row.revenue),
                "avg_pgi_days": SafeNumber.to_float(row.avg_pgi_days),
                "avg_transit_days": SafeNumber.to_float(row.avg_transit_days),
                "avg_cycle_days": SafeNumber.to_float(row.avg_cycle_days),
                "pgi_pct": SafeNumber.to_float(row.pgi_pct),
                "delivery_pct": SafeNumber.to_float(row.delivery_pct),
                "pending_pct": SafeNumber.to_float(row.pending_pct),
                "health_score": SafeNumber.to_float(row.health_score)
            })
        return result

    # ---------- Monthly Trend (kept as before, but can be enhanced similarly if needed) ----------
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

    # ---------- Pending Analysis ----------
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

    # ---------- City Delays ----------
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

    # ---------- Distance Pairs (kept for compatibility but not used for targets) ----------
    def fetch_warehouse_city_pairs(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT
                warehouse,
                ship_to_city,
                COUNT(DISTINCT dn_no) AS dn_count,
                SUM(dn_qty) AS total_units,
                AVG(EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp))) AS avg_transit_days,
                MIN(EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp))) AS min_transit_days,
                MAX(EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp))) AS max_transit_days
            FROM delivery_reports
            WHERE warehouse IS NOT NULL AND ship_to_city IS NOT NULL AND pod_date IS NOT NULL
            GROUP BY warehouse, ship_to_city
        """
        rows = self._execute(sql).fetchall()
        return [
            {
                "warehouse": row.warehouse,
                "city": row.ship_to_city,
                "dn_count": SafeNumber.to_int(row.dn_count),
                "total_units": SafeNumber.to_int(row.total_units),
                "avg_transit_days": SafeNumber.to_float(row.avg_transit_days),
                "min_transit_days": SafeNumber.to_float(row.min_transit_days),
                "max_transit_days": SafeNumber.to_float(row.max_transit_days),
            }
            for row in rows
        ]

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

    # ---------- NEW: Warehouse daily aggregates for trend (no history table) ----------
    def fetch_warehouse_daily_aggregates(self, warehouse: str, days: int = 14) -> List[Dict[str, Any]]:
        """
        Returns daily health score, delivery%, etc., for a specific warehouse over the last N days.
        This allows trend computation without a history table.
        """
        sql = f"""
            WITH daily_agg AS (
                SELECT
                    dn_create_date AS date,
                    COALESCE(SUM(dn_qty), 0) AS total_units,
                    COALESCE(SUM(CASE WHEN pod_date IS NOT NULL THEN dn_qty ELSE 0 END), 0) AS delivered_units,
                    COALESCE(SUM(CASE WHEN pod_date IS NULL THEN dn_qty ELSE 0 END), 0) AS pending_units,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL THEN dn_qty ELSE 0 END), 0) AS pgi_units,
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL 
                        AND good_issue_date >= dn_create_date
                        THEN EXTRACT(DAY FROM (good_issue_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_pgi_days,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= good_issue_date
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - good_issue_date::timestamp)) END), 0) AS avg_transit_days,
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= dn_create_date
                        THEN EXTRACT(DAY FROM (pod_date::timestamp - dn_create_date::timestamp)) END), 0) AS avg_cycle_days
                FROM delivery_reports
                WHERE warehouse = :warehouse
                  AND dn_create_date >= CURRENT_DATE - INTERVAL '{days} days'
                GROUP BY dn_create_date
            )
            SELECT
                date,
                total_units,
                delivered_units,
                pending_units,
                pgi_units,
                avg_pgi_days,
                avg_transit_days,
                avg_cycle_days,
                CASE WHEN total_units > 0 THEN ROUND((pgi_units * 100.0 / total_units), 2) ELSE 0 END AS pgi_pct,
                CASE WHEN total_units > 0 THEN ROUND((delivered_units * 100.0 / total_units), 2) ELSE 0 END AS delivery_pct,
                CASE WHEN total_units > 0 THEN ROUND((pending_units * 100.0 / total_units), 2) ELSE 0 END AS pending_pct,
                CASE 
                    WHEN total_units > 0 THEN 
                        ROUND(
                            (CASE WHEN total_units > 0 THEN (delivered_units * 100.0 / total_units) ELSE 0 END) * 0.30 +
                            (CASE WHEN total_units > 0 THEN (pgi_units * 100.0 / total_units) ELSE 0 END) * 0.10 +
                            GREATEST(0, 100 - (avg_cycle_days - 1) * 5) * 0.20 +
                            (100 - (CASE WHEN total_units > 0 THEN (pending_units * 100.0 / total_units) ELSE 0 END)) * 0.15 +
                            (CASE WHEN total_units > 0 THEN (delivered_units * 100.0 / total_units) ELSE 0 END) * 0.25
                        , 2)
                    ELSE 0
                END AS health_score
            FROM daily_agg
            ORDER BY date
        """
        rows = self._execute(sql, {"warehouse": warehouse}).fetchall()
        result = []
        for row in rows:
            result.append({
                "date": row.date.strftime('%Y-%m-%d') if row.date else None,
                "health_score": SafeNumber.to_float(row.health_score),
                "delivery_pct": SafeNumber.to_float(row.delivery_pct),
                "pgi_pct": SafeNumber.to_float(row.pgi_pct),
                "pending_units": SafeNumber.to_int(row.pending_units),
                "avg_cycle_days": SafeNumber.to_float(row.avg_cycle_days),
                "avg_transit_days": SafeNumber.to_float(row.avg_transit_days),
            })
        return result

# ============================================================
# BLOCK 8: Distance Calculation Engine (DEPRECATED – kept only for legacy compatibility)
# ============================================================

class DistanceCalculationEngine:
    """DEPRECATED: Distance-based logic removed. Kept for compatibility only."""
    @staticmethod
    def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        # kept for potential future use, but not used for targets
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
    
    @classmethod
    def calculate_distance(cls, origin: str, destination: str) -> float:
        # not used
        return 0.0

    @classmethod
    def get_target_days(cls, distance_km: float) -> int:
        # DEPRECATED: returns fixed target from config
        return int(config.cycle_target_days)

    @classmethod
    def compute_compliance(cls, actual_days: float, target_days: int) -> float:
        if target_days == 0:
            return 0.0
        if actual_days == 0:
            return 100.0
        return round((target_days / actual_days) * 100, 2)

    @classmethod
    def get_performance_rating(cls, gap: float) -> str:
        # Not used anymore; kept for compatibility
        return "Average"

# ============================================================
# BLOCK 9: Business Rule Engine (Unified Health Score, Classification)
# ============================================================

class BusinessRuleEngine:
    @staticmethod
    def calculate_health_score(delivery_pct: float, pgi_pct: float, cycle_days: float, pending_pct: float, pod_pct: float = None) -> float:
        """
        Unified Health Score calculation.
        Weights are taken from config.health_weights.
        - delivery_pct: Delivery Achievement % (units basis)
        - pgi_pct: PGI Achievement % (units basis)
        - cycle_days: Average cycle days (used to compute cycle score)
        - pending_pct: Pending % (used to compute pending score = 100 - pending_pct)
        - pod_pct: POD Achievement % (if not provided, use delivery_pct as fallback)
        """
        w = config.health_weights
        
        # Cycle Score: 100 - (cycle_days - target) * penalty factor (5 points per day over target)
        cycle_target = config.cycle_target_days
        if cycle_days <= cycle_target:
            cycle_score = 100.0
        else:
            cycle_score = max(0, 100 - (cycle_days - cycle_target) * 5)
        
        # Pending Score
        pending_score = max(0, 100 - pending_pct)
        
        # POD score (use delivery if not provided)
        if pod_pct is None:
            pod_pct = delivery_pct
        
        # Weighted sum
        score = (delivery_pct * w["delivery"] +
                 pod_pct * w["pod"] +
                 cycle_score * w["cycle"] +
                 pending_score * w["pending"] +
                 pgi_pct * w["pgi"])
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

    # ----- KPI-specific performance bands -----
    @staticmethod
    def classify_delivery_pct(value: float) -> str:
        bands = config.performance_bands["delivery_pct"]
        if value >= bands["excellent"]:
            return "Excellent"
        elif value >= bands["good"]:
            return "Good"
        elif value >= bands["average"]:
            return "Average"
        elif value >= bands["poor"]:
            return "Poor"
        else:
            return "Critical"

    @staticmethod
    def classify_pgi_pct(value: float) -> str:
        bands = config.performance_bands["pgi_pct"]
        if value >= bands["excellent"]:
            return "Excellent"
        elif value >= bands["good"]:
            return "Good"
        elif value >= bands["average"]:
            return "Average"
        elif value >= bands["poor"]:
            return "Poor"
        else:
            return "Critical"

    @staticmethod
    def classify_cycle_days(value: float) -> str:
        bands = config.performance_bands["cycle_days"]
        if value <= bands["excellent"]:
            return "Excellent"
        elif value <= bands["good"]:
            return "Good"
        elif value <= bands["average"]:
            return "Average"
        elif value <= bands["poor"]:
            return "Poor"
        else:
            return "Critical"

# ============================================================
# BLOCK 10: Warehouse Intelligence Engine (Uses config, no distance)
# ============================================================

class WarehouseIntelligenceEngine:
    @staticmethod
    def compute_warehouse_intelligence(
        warehouse_records: List[Dict[str, Any]],
        # Removed distance parameters; using fixed config targets
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
            avg_pgi_days = w.get('avg_pgi_days', 0)
            avg_transit_days = w.get('avg_transit_days', 0)
            avg_cycle_days = w.get('avg_cycle_days', 0)
            min_transit = w.get('min_transit_days', 0)
            max_transit = w.get('max_transit_days', 0)
            min_cycle = w.get('min_cycle_days', 0)
            max_cycle = w.get('max_cycle_days', 0)

            # Achievements (units basis)
            pgi_rate = SafeNumber.pct(pgi_units, total_units)
            delivery_rate = SafeNumber.pct(delivered_units, total_units)
            pending_rate = SafeNumber.pct(pending_units, total_units)
            pod_rate = SafeNumber.pct(delivered_units, delivered_units) if delivered_units > 0 else 0.0

            # Targets from config
            pgi_target = config.pgi_target_days
            transit_target = config.transit_target_days
            cycle_target = config.cycle_target_days

            # Gaps
            pgi_gap = avg_pgi_days - pgi_target
            transit_gap = avg_transit_days - transit_target
            cycle_gap = avg_cycle_days - cycle_target

            # Compliance (as percentage of target)
            pgi_compliance = DistanceCalculationEngine.compute_compliance(avg_pgi_days, pgi_target)
            transit_compliance = DistanceCalculationEngine.compute_compliance(avg_transit_days, transit_target)
            cycle_compliance = DistanceCalculationEngine.compute_compliance(avg_cycle_days, cycle_target)

            # Ratings using KPI-specific bands
            pgi_rating = BusinessRuleEngine.classify_pgi_pct(pgi_rate)  # using achievement %
            delivery_rating = BusinessRuleEngine.classify_delivery_pct(delivery_rate)
            cycle_rating = BusinessRuleEngine.classify_cycle_days(avg_cycle_days)

            # Health Score (unified)
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
                    "avg_days": avg_pgi_days,
                    "target_days": pgi_target,
                    "gap_days": pgi_gap,
                    "compliance_pct": pgi_compliance,
                    "status": pgi_rating
                },
                "transit": {
                    "avg_days": avg_transit_days,
                    "min_days": min_transit,
                    "max_days": max_transit,
                    "target_days": transit_target,
                    "gap_days": transit_gap,
                    "compliance_pct": transit_compliance,
                    "status": delivery_rating  # using delivery performance classification
                },
                "cycle": {
                    "avg_days": avg_cycle_days,
                    "min_days": min_cycle,
                    "max_days": max_cycle,
                    "target_days": cycle_target,
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
                "trend": "▬ Stable",  # will be updated later using daily aggregates
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
                "avg_days": avg_cycle_days,
                "avg_delivery_days": avg_transit_days,   # legacy key, now transit
                "avg_pgi_days": avg_pgi_days,
                "pending_dns": w.get('pending_delivery', 0) + w.get('pending_pgi', 0),
                "pending_units": pending_units,
                "status": classification.get('status', 'Unknown'),
                "performance_score": health_score,
                "risk_emoji": "🟢" if risk == RiskLevel.LOW else "🟡" if risk == RiskLevel.MEDIUM else "🟠" if risk == RiskLevel.HIGH else "🔴",
                "total_units": total_units,  # duplicate for consistency
                "delivery_achievement_rate": delivery_rate,
                "pgi_achievement_rate": pgi_rate,
                "pending_rate": pending_rate,
                "total_revenue": revenue,
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
            "units_growth": SafeNumber.pct(today.get('total_units', 0) - yesterday.get('total_units', 0), yesterday.get('total_units', 1)),
            "revenue_growth": SafeNumber.pct(today.get('revenue', 0) - yesterday.get('revenue', 0), yesterday.get('revenue', 1)),
            "health_growth": SafeNumber.pct(today.get('health_score', 0) - yesterday.get('health_score', 0), yesterday.get('health_score', 1)),
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
# BLOCK 12: Enhanced Alert Engine (Smart Alerts)
# ============================================================

class AlertEngine:
    @staticmethod
    def generate_alerts(warehouse_summaries: List[Dict[str, Any]], kpis: Dict) -> List[Dict[str, Any]]:
        alerts = []
        
        # 1. High pending units per warehouse
        for w in warehouse_summaries:
            pending = w.get('pending', {}).get('units', 0)
            warehouse = w.get('warehouse', 'Unknown')
            if pending > config.pending_units_alert_threshold * 5:  # >5000
                alerts.append({
                    "source": warehouse,
                    "severity": "CRITICAL" if pending > 10000 else "HIGH",
                    "category": "Pending Units",
                    "message": f"{pending:,} pending units",
                    "urgency": 3 if pending > 10000 else 2,
                    "expected_delay": round(pending / 1000, 1)
                })
            elif pending > config.pending_units_alert_threshold:
                alerts.append({
                    "source": warehouse,
                    "severity": "MEDIUM",
                    "category": "Pending Units",
                    "message": f"{pending:,} pending units",
                    "urgency": 1
                })
        
        # 2. Delivery below 80%
        for w in warehouse_summaries:
            delivery = w.get('delivery_pct', 100)
            warehouse = w.get('warehouse', 'Unknown')
            if delivery < 70:
                alerts.append({
                    "source": warehouse,
                    "severity": "HIGH",
                    "category": "Low Delivery",
                    "message": f"Delivery at {delivery}% (target 90%)",
                    "urgency": 2
                })
            elif delivery < 80:
                alerts.append({
                    "source": warehouse,
                    "severity": "MEDIUM",
                    "category": "Low Delivery",
                    "message": f"Delivery at {delivery}%",
                    "urgency": 1
                })
        
        # 3. PGI below 85%
        for w in warehouse_summaries:
            pgi = w.get('pgi_pct', 100)
            warehouse = w.get('warehouse', 'Unknown')
            if pgi < 80:
                alerts.append({
                    "source": warehouse,
                    "severity": "HIGH",
                    "category": "PGI Drop",
                    "message": f"PGI at {pgi}% (target 95%)",
                    "urgency": 2
                })
            elif pgi < 85:
                alerts.append({
                    "source": warehouse,
                    "severity": "MEDIUM",
                    "category": "PGI Drop",
                    "message": f"PGI at {pgi}%",
                    "urgency": 1
                })
        
        # 4. System-level alerts
        total_pending = kpis.get('pending_units', {}).get('value', 0)
        if total_pending > 20000:
            alerts.append({
                "source": "System",
                "severity": "CRITICAL",
                "category": "System Pending",
                "message": f"Total pending units: {total_pending:,}",
                "urgency": 4
            })
        elif total_pending > 10000:
            alerts.append({
                "source": "System",
                "severity": "HIGH",
                "category": "System Pending",
                "message": f"Total pending units: {total_pending:,}",
                "urgency": 3
            })
        
        # 5. Cycle compliance alerts
        for w in warehouse_summaries:
            compliance = w.get('cycle', {}).get('compliance_pct', 100)
            warehouse = w.get('warehouse', 'Unknown')
            if compliance < 70:
                alerts.append({
                    "source": warehouse,
                    "severity": "HIGH",
                    "category": "Cycle Compliance",
                    "message": f"Cycle compliance at {compliance}% (target 80%)",
                    "urgency": 2
                })
            elif compliance < 80:
                alerts.append({
                    "source": warehouse,
                    "severity": "MEDIUM",
                    "category": "Cycle Compliance",
                    "message": f"Cycle compliance at {compliance}%",
                    "urgency": 1
                })
        
        # Deduplicate (keep highest urgency)
        seen = set()
        deduped = []
        for alert in sorted(alerts, key=lambda x: x.get('urgency', 0), reverse=True):
            key = (alert['source'], alert['category'])
            if key not in seen:
                seen.add(key)
                deduped.append(alert)
        
        return deduped[:config.max_alerts]

# ============================================================
# BLOCK 13: Enhanced Recommendation Engine (with impact & priority)
# ============================================================

class RecommendationEngine:
    @staticmethod
    def generate_recommendations(warehouse_summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        recs = []
        for w in warehouse_summaries:
            warehouse = w.get('warehouse', 'Unknown')
            pending = w.get('pending', {}).get('units', 0)
            delivery = w.get('delivery_pct', 100)
            pgi = w.get('pgi_pct', 100)
            cycle_gap = w.get('cycle', {}).get('gap_days', 0)
            transit_gap = w.get('transit', {}).get('gap_days', 0)
            pgi_gap = w.get('pgi', {}).get('gap_days', 0)
            
            if pending > 10000:
                impact = round((pending / 1000) * 0.5, 1)
                recs.append({
                    "warehouse": warehouse,
                    "priority": "Priority 1",
                    "problem": f"{pending:,} pending units",
                    "recommendation": "Immediate clearance required. Prioritize oldest pending DNs.",
                    "expected_improvement": f"+{impact}% Health Score",
                    "status": "Immediate",
                    "target_kpi": "Pending Units"
                })
            elif pending > 5000:
                impact = round((pending / 1000) * 0.3, 1)
                recs.append({
                    "warehouse": warehouse,
                    "priority": "Priority 2",
                    "problem": f"{pending:,} pending units",
                    "recommendation": "Expedite dispatch for pending orders.",
                    "expected_improvement": f"+{impact}% Health Score",
                    "status": "High",
                    "target_kpi": "Pending Units"
                })
            elif delivery < 75:
                recs.append({
                    "warehouse": warehouse,
                    "priority": "Priority 2",
                    "problem": f"Delivery at {delivery}% (target 90%)",
                    "recommendation": "Analyze transit delays and optimize routing.",
                    "expected_improvement": "+3-5% Delivery",
                    "status": "High",
                    "target_kpi": "Delivery"
                })
            elif delivery < 85:
                recs.append({
                    "warehouse": warehouse,
                    "priority": "Priority 3",
                    "problem": f"Delivery at {delivery}%",
                    "recommendation": "Improve last-mile execution.",
                    "expected_improvement": "+2% Delivery",
                    "status": "Medium",
                    "target_kpi": "Delivery"
                })
            elif pgi < 85:
                recs.append({
                    "warehouse": warehouse,
                    "priority": "Priority 3",
                    "problem": f"PGI at {pgi}% (target 95%)",
                    "recommendation": "Accelerate PGI process by reducing order-to-dispatch time.",
                    "expected_improvement": "+5% PGI",
                    "status": "Medium",
                    "target_kpi": "PGI"
                })
            elif cycle_gap > 1.0:
                recs.append({
                    "warehouse": warehouse,
                    "priority": "Priority 3",
                    "problem": f"Cycle gap of {cycle_gap:.1f} days",
                    "recommendation": "Reduce total cycle time by synchronizing PGI and transit.",
                    "expected_improvement": f"Reduce cycle by {cycle_gap:.1f} days",
                    "status": "Medium",
                    "target_kpi": "Cycle Time"
                })
            elif transit_gap > 0.5:
                recs.append({
                    "warehouse": warehouse,
                    "priority": "Priority 3",
                    "problem": f"Transit gap of {transit_gap:.1f} days",
                    "recommendation": "Improve transit efficiency.",
                    "expected_improvement": f"Reduce transit by {transit_gap:.1f} days",
                    "status": "Medium",
                    "target_kpi": "Transit Time"
                })
        
        # Sort: Priority 1 first, then 2, then 3
        recs.sort(key=lambda x: 0 if "Priority 1" in x["priority"] else 1 if "Priority 2" in x["priority"] else 2)
        return recs[:5]

    @staticmethod
    def generate_short_insight(warehouse: Dict[str, Any]) -> str:
        pgi = warehouse.get('pgi_pct', 0)
        delivery = warehouse.get('delivery_pct', 0)
        transit = warehouse.get('avg_transit_days', 0)
        cycle = warehouse.get('avg_cycle_days', 0)
        pending = warehouse.get('pending', {}).get('units', 0)
        health = warehouse.get('health_score', 0)

        if health >= 90 and delivery >= 95 and pgi >= 95 and cycle <= config.cycle_target_days:
            return "🟢 Excellent performance."
        if delivery < 80:
            return "🔴 Delivery delay increasing."
        if pgi < 80:
            return "🟡 PGI process needs attention."
        if pending > 1000:
            return "🟠 High pending units."
        if cycle > config.cycle_target_days:
            return "🟡 Cycle time above target."
        if health >= 70:
            return "🟡 Performance stable, monitor closely."
        return "🔴 Critical risk – immediate action required."

# ============================================================
# BLOCK 14: Enhanced Performance Trend Engine (Multi-metric, history from queries)
# ============================================================

class PerformanceTrendEngine:
    @staticmethod
    def compute_trends(daily_trend: List[Dict]) -> Dict[str, Any]:
        """
        Takes the detailed daily trend (with health, delivery, pgi, etc.) and returns
        daily/weekly/monthly/yearly aggregates with all metrics.
        """
        if not daily_trend:
            return {"daily": [], "weekly": [], "monthly": [], "yearly": []}
        
        # Transform to consistent format
        trend_data = []
        for day in daily_trend:
            trend_data.append({
                "date": day.get("date"),
                "health_score": day.get("health_score", 0),
                "delivery_pct": day.get("delivery_pct", 0),
                "pgi_pct": day.get("pgi_pct", 0),
                "pod_pct": day.get("delivery_pct", 0),  # same as delivery for now
                "pending_units": day.get("pending_units", 0),
                "pending_pct": day.get("pending_pct", 0),
                "revenue": day.get("revenue", 0),
                "dn_count": day.get("dn_count", 0),
                "total_units": day.get("total_units", 0),
                "avg_transit_days": day.get("avg_transit_days", 0),
                "avg_cycle_days": day.get("avg_cycle_days", 0)
            })
        
        return {
            "daily": trend_data,
            "weekly": trend_data[-7:] if len(trend_data) >= 7 else trend_data,
            "monthly": trend_data[-30:] if len(trend_data) >= 30 else trend_data,
            "yearly": trend_data[-365:] if len(trend_data) >= 365 else trend_data
        }

    @staticmethod
    def calculate_warehouse_trend(warehouse: str, repo: DashboardRepository) -> str:
        """
        Uses warehouse daily aggregates (from delivery_reports) to compute trend.
        Compares last 7 days vs previous 7 days using health score.
        """
        history = repo.fetch_warehouse_daily_aggregates(warehouse, days=14)
        if len(history) < 14:
            return "▬ Stable"
        # Use health_score for trend
        last_7 = [h["health_score"] for h in history[-7:]]
        prev_7 = [h["health_score"] for h in history[-14:-7]]
        if not last_7 or not prev_7 or sum(prev_7) == 0:
            return "▬ Stable"
        avg_last = sum(last_7) / len(last_7)
        avg_prev = sum(prev_7) / len(prev_7)
        if avg_prev == 0:
            return "▬ Stable"
        change = (avg_last - avg_prev) / avg_prev * 100
        if change > 5:
            return "▲ Improving"
        elif change < -5:
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
        pending_dn = kpis.get('pending_dn', {}).get('value', 0)
        pending_units = kpis.get('pending_units', {}).get('value', 0)

        best = warehouses[0] if warehouses else None
        worst = warehouses[-1] if warehouses else None

        if not warehouses and health == 0 and delivery_pct == 0:
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

        if pending_dn > 0 or pending_units > 0:
            lines.append(f"{pending_dn} DNs and {pending_units} units are still pending.")
        else:
            lines.append("No pending DNs or units at this time.")

        if best:
            lines.append(f"{best.get('warehouse', 'Unknown')} warehouse is the top performer.")
        if worst:
            lines.append(f"{worst.get('warehouse', 'Unknown')} warehouse needs immediate attention.")

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
        avg_delivery = sum(w.get('delivery_pct', 0) for w in warehouse_summaries) / total
        avg_cycle = sum(w.get('avg_cycle_days', 0) for w in warehouse_summaries) / total

        best = max(warehouse_summaries, key=lambda w: w.get('health_score', 0))
        worst = min(warehouse_summaries, key=lambda w: w.get('health_score', 0))
        fastest = min(warehouse_summaries, key=lambda w: w.get('avg_cycle_days', 999))
        highest_delay = max(warehouse_summaries, key=lambda w: w.get('transit', {}).get('gap_days', 0))

        critical = [w for w in warehouse_summaries if w.get('grade') == 'Critical' or w.get('risk') == 'critical']

        if critical:
            rec = f"Focus on {len(critical)} critical warehouses: {', '.join([w.get('warehouse', '') for w in critical[:3]])}. Immediate action required."
        elif avg_delivery < 80:
            rec = "Overall delivery compliance is below 80%. Review dispatch processes across all warehouses."
        elif avg_cycle > config.cycle_target_days:
            rec = "Average cycle time exceeds target. Optimize PGI and transit processes."
        else:
            rec = "Performance is satisfactory. Continue monitoring and optimize further."

        return {
            "overall_health": round(avg_health, 2),
            "overall_delivery": round(avg_delivery, 2),
            "overall_cycle": round(avg_cycle, 2),
            "best_warehouse": best.get('warehouse', ''),
            "worst_warehouse": worst.get('warehouse', ''),
            "fastest_warehouse": fastest.get('warehouse', ''),
            "highest_delay_warehouse": highest_delay.get('warehouse', ''),
            "critical_warehouses": len(critical),
            "ai_recommendation": rec
        }

# ============================================================
# BLOCK 16: Response Builder (Updated with new trend and preview)
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

        # Executive KPI Cards
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

        growth = KPIEngine.compute_day_over_day(daily_trend)
        for key in ["total_dn", "total_units", "total_value"]:
            if key in cards:
                cards[key]["vs_yesterday"] = growth.get(key.replace("total_", "").replace("_value", "revenue") + "_growth", 0)
        cards["health_score"]["vs_yesterday"] = growth.get("health_growth", 0)

        # Pipeline
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

        # Warehouse ranking with trend and AI insight
        warehouse_ranking = []
        for w in warehouse_summaries:
            risk_map = {
                "Excellent": "🟢",
                "Good": "🟢",
                "Average": "🟡",
                "Poor": "🟠",
                "Critical": "🔴"
            }
            risk_emoji = risk_map.get(w.get('status', 'Unknown'), "⚪")
            ai_insight = RecommendationEngine.generate_short_insight(w)
            warehouse_ranking.append({
                "rank": w.get('rank', 0),
                "warehouse": w.get('warehouse', ''),
                "dns": w.get('dns', 0),
                "units": w.get('units', 0),
                "revenue": w.get('revenue', 0),
                "pgi_pct": w.get('pgi_pct', 0),
                "delivery_pct": w.get('delivery_pct', 0),
                "avg_days": w.get('avg_cycle_days', 0),
                "avg_transit_days": w.get('avg_transit_days', 0),
                "avg_pgi_days": w.get('avg_pgi_days', 0),
                "pending_dns": w.get('pending_dns', 0),
                "pending_units": w.get('pending_units', 0),
                "status": w.get('status', 'Unknown'),
                "performance_score": w.get('health_score', 0),
                "risk": risk_emoji,
                "trend": w.get('trend', '▬ Stable'),
                "ai_insight": ai_insight,
            })

        # Excel Preview table (using actual warehouse data, but frontend can also display the uploaded preview separately)
        warehouse_preview = []
        for idx, w in enumerate(warehouse_summaries[:5], start=1):
            total_units = w.get('units', 0)
            delivered = w.get('delivered_units', 0)
            pending = w.get('pending_units', 0)
            pgi_days = w.get('avg_pgi_days', 0)
            transit_days = w.get('avg_transit_days', 0)
            cycle_days = w.get('avg_cycle_days', 0)
            delivery_pct = w.get('delivery_pct', 0)
            pgi_pct = w.get('pgi_pct', 0)
            health = w.get('health_score', 0)

            warehouse_preview.append({
                "sn": idx,
                "warehouse": w.get('warehouse', ''),
                "total_units": total_units,
                "delivered_units": delivered,
                "pending_units": pending,
                "pgi_days": round(pgi_days, 1),
                "transit_days": round(transit_days, 1),
                "cycle_days": round(cycle_days, 1),
                "delivery_pct": round(delivery_pct, 1),
                "pgi_pct": round(pgi_pct, 1),
                "health_score": round(health, 1),
            })

        # Top delayed cities (now using transit days)
        top_delayed_cities = []
        for city in sorted(city_delays, key=lambda x: x.get('avg_transit_days', 0), reverse=True)[:10]:
            days = city.get('avg_transit_days', 0)
            risk = "Critical" if days > 5 else "High" if days > 4 else "Medium" if days > 3 else "Low"
            top_delayed_cities.append({
                "city": city.get('city', ''),
                "avg_transit_days": days,
                "pending_units": city.get('pending_units', 0),
                "status": risk,
            })

        # Top pending warehouses
        sorted_by_pending = sorted(warehouse_summaries, key=lambda w: w.get('pending', {}).get('units', 0), reverse=True)[:5]
        top_pending_warehouses = [
            {"warehouse": w.get('warehouse', ''), "pending_dns": w.get('pending', {}).get('dn', 0), "pending_units": w.get('pending', {}).get('units', 0)}
            for w in sorted_by_pending
        ]

        # Top dealers
        sorted_dealers = sorted(dealers, key=lambda d: d.get('total_revenue', 0), reverse=True)[:5]
        top_dealers = [
            {"dealer": d.get('dealer_name', ''), "dns": d.get('delivery_notes', 0), "units": d.get('units', 0), "revenue": d.get('total_revenue', 0)}
            for d in sorted_dealers
        ]

        # Top products
        sorted_products = sorted(products, key=lambda p: p.get('units', 0), reverse=True)[:5]
        top_products = [
            {"product": p.get('product_name', ''), "units": p.get('units', 0), "revenue": p.get('total_revenue', 0), "delivery_notes": p.get('delivery_notes', 0)}
            for p in sorted_products
        ]

        # Division performance
        division_performance = [
            {"division": d.get('division', ''), "dns": d.get('delivery_notes', 0), "units": d.get('units', 0), "revenue": d.get('total_revenue', 0)}
            for d in divisions
        ]

        # Compliance data (now based on fixed targets)
        compliance = []
        for c in compliance_data[:6]:
            compliance.append({
                "category": c.get('warehouse', 'Unknown'),
                "target_days": c.get('target_days', 0),
                "actual_days": c.get('actual_days', 0),
                "compliance_pct": c.get('compliance_pct', 0),
                "status": c.get('status', ''),
            })

        # Final response
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
            "performance_trends": trends,  # New: contains daily/weekly/monthly/yearly with all metrics
            "warehouse_ranking": warehouse_ranking,
            "warehouse_preview": warehouse_preview,
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
# BLOCK 17: Dashboard Service (Main orchestrator)
# ============================================================

class DashboardService:
    def __init__(self):
        self._repo = DashboardRepository()
        logger.info("DashboardService initialized (v26.0 - No new tables)")

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
            # Use enhanced daily trend
            daily_trend = self._repo.fetch_daily_trend_detailed(90)
            monthly_trend = self._repo.fetch_monthly_trend(12)
            pending_analysis = self._repo.fetch_pending_analysis()
            city_delays = self._repo.fetch_city_delay_data()
            import_summary = self._repo.get_import_summary()
            record_count = self._repo.fetch_record_count()

            # 2. Build warehouse intelligence (no distance)
            warehouse_summaries = WarehouseIntelligenceEngine.compute_warehouse_intelligence(
                warehouse_raw
            )

            # 3. Compute per-warehouse trend using daily aggregates (no history table)
            for w in warehouse_summaries:
                wh = w.get('warehouse')
                if wh:
                    w['trend'] = PerformanceTrendEngine.calculate_warehouse_trend(wh, self._repo)
                else:
                    w['trend'] = "▬ Stable"
                w['ai_insight'] = RecommendationEngine.generate_short_insight(w)

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

            # Health Score using unified method
            health = BusinessRuleEngine.calculate_health_score(
                delivery_pct=delivery_rate,
                pgi_pct=pgi_rate,
                cycle_days=avg_cycle,
                pending_pct=SafeNumber.pct(pending_units, total_units),
                pod_pct=delivery_rate  # pod = delivery for now
            )

            kpis = {
                "total_dn": {"value": summary.get('total_dn', 0)},
                "total_units": {"value": total_units},
                "total_value": {"value": summary.get('total_revenue', 0) or (total_units * config.avg_unit_price)},
                "pgi_achievement": {"value": pgi_rate},
                "delivery_achievement": {"value": delivery_rate},
                "pod_achievement": {"value": delivery_rate},  # simplified
                "pending_units": {"value": pending_units},
                "health_score": {"value": health},
                "pending_dn": {"value": summary.get('pending_delivery', 0) + summary.get('pending_pgi', 0)},
                "avg_cycle_days": {"value": avg_cycle},
                "avg_transit_days": {"value": summary.get('avg_transit_days', 0)},
                "avg_pgi_days": {"value": summary.get('avg_pgi_days', 0)},
            }

            # 6. Alerts & Recommendations (using enhanced engines)
            alerts = AlertEngine.generate_alerts(warehouse_summaries, kpis)
            recommendations = RecommendationEngine.generate_recommendations(warehouse_summaries)

            # 7. Trends (multi-metric)
            trends = PerformanceTrendEngine.compute_trends(daily_trend)

            # 8. Executive Summary
            exec_summary_text = ExecutiveSummaryEngine.generate_summary(kpis, warehouse_summaries, alerts, recommendations)
            detailed_summary = ExecutiveSummaryEngine.generate_detailed_summary(warehouse_summaries, national_kpis)

            # 9. Pipeline
            pipeline = {
                "dn_created": {"dn": summary.get('total_dn', 0), "units": total_units, "pct": 100, "avg_days": 0, "pending": 0},
                "pgi_completed": {"dn": summary.get('pgi_completed', 0), "units": pgi_units, "pct": SafeNumber.pct(summary.get('pgi_completed', 0), summary.get('total_dn', 1)), "avg_days": summary.get('avg_pgi_days', 0), "pending": summary.get('total_dn', 0) - summary.get('pgi_completed', 0)},
                "in_transit": {"dn": summary.get('delivered_dns', 0), "units": delivered_units, "pct": SafeNumber.pct(summary.get('delivered_dns', 0), summary.get('total_dn', 1)), "avg_days": summary.get('avg_transit_days', 0), "pending": summary.get('total_dn', 0) - summary.get('delivered_dns', 0)},
                "delivered": {"dn": summary.get('delivered_dns', 0), "units": delivered_units, "pct": SafeNumber.pct(summary.get('delivered_dns', 0), summary.get('total_dn', 1)), "avg_days": summary.get('avg_transit_days', 0), "pending": 0},
            }

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

            # Compliance data (for display)
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
                "version": "26.0",
                "timestamp": datetime.utcnow().isoformat(),
                "record_count": record_count,
                "warehouse_count": len(warehouse_summaries),
            }

            # 10. Build final response
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
        summaries = WarehouseIntelligenceEngine.compute_warehouse_intelligence(warehouse_raw)
        for w in summaries:
            w['ai_insight'] = RecommendationEngine.generate_short_insight(w)
            wh = w.get('warehouse')
            if wh:
                w['trend'] = PerformanceTrendEngine.calculate_warehouse_trend(wh, self._repo)
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
    return {"status": "healthy", "version": "26.0", "timestamp": datetime.utcnow().isoformat()}

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

@router.post("/upload/preview")
async def preview_excel(file: UploadFile = File(...)):
    """Preview the first 10 rows of the uploaded Excel file with validation status."""
    try:
        contents = await file.read()
        if not PANDAS_AVAILABLE:
            raise HTTPException(status_code=500, detail="Pandas not available")
        df = pd.read_excel(io.BytesIO(contents))
        preview_rows = df.head(10).to_dict(orient='records')
        # Add simple validation: check for required columns
        required_cols = ["dn_no", "warehouse", "dealer_code", "ship_to_city", "dn_qty", "dn_create_date"]
        missing = [c for c in required_cols if c not in df.columns]
        validation_status = "Valid" if not missing else f"Missing columns: {', '.join(missing)}"
        return {
            "preview": preview_rows,
            "columns": df.columns.tolist(),
            "row_count": len(df),
            "validation_status": validation_status
        }
    except Exception as e:
        logger.error(f"Preview error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

logger.info("DashboardService router mounted (v26.0 - Enterprise Dashboard, No New Tables)")
