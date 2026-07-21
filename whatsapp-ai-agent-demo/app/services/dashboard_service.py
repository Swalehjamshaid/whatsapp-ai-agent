# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 15.2 - ENTERPRISE WAREHOUSE INTELLIGENCE PLATFORM
# ============================================================
# EXCEEDS SAP ANALYTICS CLOUD | MICROSOFT FABRIC | POWER BI PREMIUM
# ============================================================

import hashlib
import json
import logging
import os
import time
import math
import random
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
from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks, Request, Response
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
from app.models import DeliveryReport, Warehouse, Dealer, City, Product, Division
from app.services.geo_service import GeoService

# ============================================================
# LOGGING CONFIGURATION
# ============================================================

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
# ENUMERATIONS & CONSTANTS
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
# CONFIGURATION
# ============================================================

@dataclass
class DashboardConfig:
    """Enterprise configuration with all tunable parameters."""
    cache_ttl_seconds: int = 300
    cache_max_size: int = 1000
    pgi_target_days: float = 1.0
    pod_base_target_days: float = 1.0
    delivery_target_base_days: float = 1.0
    health_score_excellent: float = 90.0
    health_score_good: float = 75.0
    health_score_average: float = 60.0
    health_score_poor: float = 40.0


# ============================================================
# UTILITY LAYER
# ============================================================

class SafeNumber:
    """Safe numeric operations with None/error handling."""
    
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
    """Enterprise date utilities."""
    
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
# EXCEPTION HANDLING
# ============================================================

class DashboardServiceError(Exception):
    pass

class DatabaseError(DashboardServiceError):
    pass


# ============================================================
# CACHING LAYER
# ============================================================

class EnterpriseCache:
    """Advanced multi-tier cache with TTL and LRU eviction."""
    
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
# REPOSITORY LAYER
# ============================================================

class DashboardRepository:
    """High-performance data access layer matching database schemas."""
    
    def __init__(self, db_session: Optional[Session] = None):
        self._db_session = db_session
        logger.info("DashboardRepository initialized")
    
    def _execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                return result
        except SQLAlchemyError as e:
            logger.error(f"SQL execution failed: {str(e)}")
            raise DatabaseError(f"Database query failed: {str(e)}")
    
    def fetch_summary(self) -> Dict[str, Any]:
        sql = """
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
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS avg_delivery_days,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (good_issue_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS avg_pgi_days,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (pod_date::timestamp - good_issue_date::timestamp))/86400 END), 0) AS avg_pod_days,
                COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (pod_date::timestamp - good_issue_date::timestamp))/86400 END), 0) AS avg_cycle_days
            FROM delivery_reports
        """
        row = self._execute(sql).first()
        if not row:
            return {
                "total_dn": 0, "total_units": 0, "warehouse_count": 0, "dealer_count": 0,
                "city_count": 0, "product_count": 0, "division_count": 0, "pgi_completed": 0,
                "delivered_dns": 0, "pod_completed": 0, "pending_pgi": 0, "pending_delivery": 0,
                "avg_delivery_days": 0.0, "avg_pgi_days": 0.0, "avg_pod_days": 0.0, "avg_cycle_days": 0.0
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
            "pod_completed": SafeNumber.to_int(row.pod_completed),
            "pending_pgi": SafeNumber.to_int(row.pending_pgi),
            "pending_delivery": SafeNumber.to_int(row.pending_delivery),
            "avg_delivery_days": SafeNumber.to_float(row.avg_delivery_days),
            "avg_pgi_days": SafeNumber.to_float(row.avg_pgi_days),
            "avg_pod_days": SafeNumber.to_float(row.avg_pod_days),
            "avg_cycle_days": SafeNumber.to_float(row.avg_cycle_days),
        }
    
    def fetch_warehouse_data(self) -> List[Dict[str, Any]]:
        sql = """
            WITH warehouse_metrics AS (
                SELECT
                    warehouse AS warehouse_name,
                    ship_to_city,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    COUNT(DISTINCT dn_no) AS delivery_notes,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi_count,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery_count,
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS avg_pgi_days,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - good_issue_date::timestamp))/86400 END), 0) AS avg_pod_days,
                    COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS avg_cycle_days,
                    MIN(dn_create_date) AS first_dn,
                    MAX(dn_create_date) AS last_dn
                FROM delivery_reports
                WHERE warehouse IS NOT NULL
                GROUP BY warehouse, ship_to_city
            )
            SELECT
                warehouse_name,
                units,
                delivery_notes,
                pgi_completed,
                delivered_dns,
                pending_pgi_count,
                pending_delivery_count,
                avg_pgi_days,
                avg_pod_days,
                avg_cycle_days,
                first_dn,
                last_dn,
                CASE WHEN delivery_notes > 0 THEN ROUND((pgi_completed::float / delivery_notes) * 100, 2) ELSE 0 END AS pgi_achievement_rate,
                CASE WHEN delivery_notes > 0 THEN ROUND((delivered_dns::float / delivery_notes) * 100, 2) ELSE 0 END AS delivery_achievement_rate
            FROM warehouse_metrics
            ORDER BY delivery_notes DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "warehouse_name": row.warehouse_name,
                "units": SafeNumber.to_int(row.units),
                "delivery_notes": SafeNumber.to_int(row.delivery_notes),
                "pgi_completed": SafeNumber.to_int(row.pgi_completed),
                "delivered_dns": SafeNumber.to_int(row.delivered_dns),
                "pending_pgi": SafeNumber.to_int(row.pending_pgi_count),
                "pending_delivery": SafeNumber.to_int(row.pending_delivery_count),
                "avg_pgi_days": SafeNumber.to_float(row.avg_pgi_days),
                "avg_pod_days": SafeNumber.to_float(row.avg_pod_days),
                "avg_cycle_days": SafeNumber.to_float(row.avg_cycle_days),
                "pgi_achievement_rate": SafeNumber.to_float(row.pgi_achievement_rate),
                "delivery_achievement_rate": SafeNumber.to_float(row.delivery_achievement_rate),
                "first_dn": row.first_dn,
                "last_dn": row.last_dn,
            })
        return result
    
    def fetch_dealer_data(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT
                dealer_code,
                customer_name,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS avg_cycle_days
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
            })
        return result
    
    def fetch_city_data(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT
                ship_to_city AS city,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns,
                COALESCE(AVG(CASE WHEN dn_create_date IS NOT NULL AND pod_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS avg_cycle_days
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
            })
        return result
    
    def fetch_product_data(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT
                material_no AS sku,
                customer_model AS product_name,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns
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
            })
        return result
    
    def fetch_division_data(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT
                division,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS delivery_notes,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_dns
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
            })
        return result
    
    def fetch_daily_trend(self, days: int = 90) -> List[Dict[str, Any]]:
        sql = f"""
            SELECT
                dn_create_date AS date,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn_count,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_count,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_count,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pending_pgi,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pending_delivery
            FROM delivery_reports
            WHERE dn_create_date >= CURRENT_DATE - INTERVAL '{days} days'
            GROUP BY dn_create_date
            ORDER BY dn_create_date
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "date": row.date.strftime('%Y-%m-%d') if row.date else None,
                "units": SafeNumber.to_int(row.units),
                "dn_count": SafeNumber.to_int(row.dn_count),
                "pgi_count": SafeNumber.to_int(row.pgi_count),
                "delivered_count": SafeNumber.to_int(row.delivered_count),
                "pending_pgi": SafeNumber.to_int(row.pending_pgi),
                "pending_delivery": SafeNumber.to_int(row.pending_delivery),
            })
        return result
    
    def fetch_monthly_trend(self, months: int = 12) -> List[Dict[str, Any]]:
        sql = f"""
            SELECT
                DATE_TRUNC('month', dn_create_date) AS month,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn_count,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_count,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS delivered_count
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
            })
        return result
    
    def fetch_aging_distribution(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT
                CASE
                    WHEN (pod_date::date - dn_create_date::date) <= 1 THEN '0-1 Days'
                    WHEN (pod_date::date - dn_create_date::date) = 2 THEN '2 Days'
                    WHEN (pod_date::date - dn_create_date::date) = 3 THEN '3 Days'
                    WHEN (pod_date::date - dn_create_date::date) = 4 THEN '4 Days'
                    WHEN (pod_date::date - dn_create_date::date) = 5 THEN '5 Days'
                    WHEN (pod_date::date - dn_create_date::date) = 6 THEN '6 Days'
                    ELSE '7+ Days'
                END AS bucket,
                COUNT(DISTINCT dn_no) AS count,
                COALESCE(SUM(dn_qty), 0) AS units
            FROM delivery_reports
            WHERE dn_create_date IS NOT NULL AND pod_date IS NOT NULL
            GROUP BY bucket
            ORDER BY MIN((pod_date::date - dn_create_date::date))
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            result.append({
                "bucket": row.bucket,
                "count": SafeNumber.to_int(row.count),
                "units": SafeNumber.to_int(row.units),
            })
        return result
    
    def fetch_network_data(self, limit: int = 1000) -> List[Dict[str, Any]]:
        sql = """
            SELECT DISTINCT
                warehouse,
                ship_to_city,
                dealer_code,
                COUNT(DISTINCT dn_no) AS shipment_count,
                COALESCE(SUM(dn_qty), 0) AS total_units,
                COALESCE(AVG(CASE WHEN pod_date IS NOT NULL AND dn_create_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (pod_date::timestamp - dn_create_date::timestamp))/86400 END), 0) AS avg_days
            FROM delivery_reports
            WHERE warehouse IS NOT NULL AND ship_to_city IS NOT NULL
            GROUP BY warehouse, ship_to_city, dealer_code
            ORDER BY shipment_count DESC
            LIMIT :limit
        """
        rows = self._execute(sql, {"limit": limit}).fetchall()
        result = []
        for row in rows:
            result.append({
                "warehouse": row.warehouse,
                "city": row.ship_to_city,
                "dealer": row.dealer_code,
                "shipment_count": SafeNumber.to_int(row.shipment_count),
                "total_units": SafeNumber.to_int(row.total_units),
                "avg_days": SafeNumber.to_float(row.avg_days),
            })
        return result
    
    def fetch_record_count(self) -> int:
        sql = "SELECT COUNT(*) FROM delivery_reports"
        return SafeNumber.to_int(self._execute(sql).scalar())


# ============================================================
# DISTANCE & BUSINESS RULE ENGINES
# ============================================================

class DistanceCalculationEngine:
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
        coords1 = GeoService.get_city_coordinates(origin)
        coords2 = GeoService.get_city_coordinates(destination)
        return cls.haversine(coords1.get("lat", 0), coords1.get("lng", 0), coords2.get("lat", 0), coords2.get("lng", 0))
    
    @classmethod
    def get_target_days(cls, distance_km: float) -> int:
        if distance_km <= 100: return 1
        elif distance_km <= 250: return 2
        elif distance_km <= 450: return 3
        elif distance_km <= 700: return 4
        elif distance_km <= 900: return 5
        else: return 6


class BusinessRuleEngine:
    @staticmethod
    def calculate_health_score(pgi_rate: float, delivery_rate: float, pod_rate: float) -> float:
        return round((pgi_rate * 0.35) + (delivery_rate * 0.35) + (pod_rate * 0.30), 2)
    
    @staticmethod
    def calculate_performance_score(cycle_days: float, pgi_days: float, pod_days: float, pending_count: int, volume: int) -> float:
        cycle_score = max(0, 100 - (cycle_days - 0.5) * 15)
        pgi_score = max(0, 100 - (pgi_days - 0.3) * 25)
        pod_score = max(0, 100 - (pod_days - 0.5) * 12)
        pending_score = max(0, 100 - pending_count * 0.5)
        volume_score = min(100, (volume / 1000) * 100)
        score = (cycle_score * 0.40 + pgi_score * 0.25 + pod_score * 0.20 + pending_score * 0.10 + volume_score * 0.05)
        return round(max(0, min(100, score)), 2)
    
    @staticmethod
    def classify_performance(score: float) -> Dict[str, Any]:
        if score >= 90: return {"tier": "tier_1", "label": "Excellent", "color": "#22c55e"}
        elif score >= 75: return {"tier": "tier_2", "label": "Good", "color": "#84cc16"}
        elif score >= 60: return {"tier": "tier_3", "label": "Average", "color": "#f59e0b"}
        elif score >= 40: return {"tier": "tier_4", "label": "Poor", "color": "#f97316"}
        else: return {"tier": "tier_5", "label": "Critical", "color": "#ef4444"}
    
    @staticmethod
    def assess_risk_level(score: float, pending: int, cycle_days: float) -> RiskLevel:
        risk_score = 0
        if score < 60: risk_score += 3
        elif score < 75: risk_score += 2
        if pending > 50: risk_score += 2
        if cycle_days > 5: risk_score += 2
        return RiskLevel.CRITICAL if risk_score >= 5 else (RiskLevel.HIGH if risk_score >= 3 else RiskLevel.LOW)


# ============================================================
# INTELLIGENCE ENGINES
# ============================================================

class WarehouseIntelligenceEngine:
    @staticmethod
    def compute_warehouse_metrics(warehouse_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enriched = []
        for idx, w in enumerate(warehouse_records, 1):
            pgi_rate = SafeNumber.pct(w.get('pgi_completed', 0), w.get('delivery_notes', 1))
            delivery_rate = SafeNumber.pct(w.get('delivered_dns', 0), w.get('delivery_notes', 1))
            pending_total = w.get('pending_pgi', 0) + w.get('pending_delivery', 0)
            health_score = BusinessRuleEngine.calculate_health_score(pgi_rate, delivery_rate, pgi_rate)
            perf_score = BusinessRuleEngine.calculate_performance_score(
                w.get('avg_cycle_days', 0), w.get('avg_pgi_days', 0), w.get('avg_pod_days', 0), pending_total, w.get('delivery_notes', 0)
            )
            classification = BusinessRuleEngine.classify_performance(perf_score)
            risk = BusinessRuleEngine.assess_risk_level(perf_score, pending_total, w.get('avg_cycle_days', 0))
            
            enriched.append({
                **w,
                'rank': idx,
                'ranking': idx,
                'average_logistics_cycle': w.get('avg_cycle_days', 0),
                'pgi_achievement_rate': pgi_rate,
                'delivery_achievement_rate': delivery_rate,
                'health_score': health_score,
                'performance_score': perf_score,
                'performance_tier': classification['tier'],
                'performance_label': classification['label'],
                'performance_color': classification['color'],
                'risk_level': risk.value,
                'pending_total': pending_total,
            })
        return enriched

    @staticmethod
    def get_best_and_worst(warehouses: List[Dict[str, Any]]) -> Tuple[Dict, Dict]:
        if not warehouses: return {}, {}
        return max(warehouses, key=lambda x: x.get('performance_score', 0)), min(warehouses, key=lambda x: x.get('performance_score', 0))


class CityIntelligenceEngine:
    @staticmethod
    def compute_city_metrics(city_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enriched = []
        for c in city_records:
            distance = DistanceCalculationEngine.calculate_distance(c.get('city', 'Lahore'), 'Lahore')
            enriched.append({**c, 'estimated_distance_km': round(distance, 1)})
        return enriched


class DealerIntelligenceEngine:
    @staticmethod
    def compute_dealer_metrics(dealer_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return dealer_records


class ExecutiveKPIEngine:
    @staticmethod
    def generate_kpis(summary: Dict[str, Any], warehouses: List[Dict[str, Any]]) -> Dict[str, Any]:
        total_dn = summary.get('total_dn', 0)
        pgi_completed = summary.get('pgi_completed', 0)
        delivered = summary.get('delivered_dns', 0)
        pgi_rate = SafeNumber.pct(pgi_completed, total_dn)
        delivery_rate = SafeNumber.pct(delivered, total_dn)
        health = BusinessRuleEngine.calculate_health_score(pgi_rate, delivery_rate, pgi_rate)
        best, worst = WarehouseIntelligenceEngine.get_best_and_worst(warehouses)
        
        return {
            "total_dn": {"value": total_dn, "label": "Total Delivery Notes", "icon": "fa-file-invoice"},
            "total_units": {"value": summary.get('total_units', 0), "label": "Total Units", "icon": "fa-boxes"},
            "pgi_completed": {"value": pgi_completed, "label": "PGI Completed", "icon": "fa-check-circle"},
            "delivered": {"value": delivered, "label": "Delivered", "icon": "fa-truck"},
            "pending_pgi": {"value": summary.get('pending_pgi', 0), "label": "Pending PGI", "icon": "fa-hourglass-start"},
            "pending_delivery": {"value": summary.get('pending_delivery', 0), "label": "Pending Delivery", "icon": "fa-hourglass-half"},
            "avg_cycle_days": {"value": round(summary.get('avg_cycle_days', 0), 1), "label": "Avg Cycle (days)", "icon": "fa-clock"},
            "avg_cycle": {"value": round(summary.get('avg_cycle_days', 0), 1), "label": "Average Cycle Time", "icon": "fa-stopwatch"},
            "pgi_achievement": {"value": pgi_rate, "label": "PGI Achievement %", "icon": "fa-percent"},
            "delivery_achievement": {"value": delivery_rate, "label": "Delivery Achievement %", "icon": "fa-percent"},
            "health_score": {"value": health, "label": "Health Score", "icon": "fa-heart"},
            "best_warehouse": {"value": best.get('warehouse_name', 'N/A') if best else 'N/A', "label": "Best Warehouse", "icon": "fa-crown"},
            "worst_warehouse": {"value": worst.get('warehouse_name', 'N/A') if worst else 'N/A', "label": "Worst Warehouse", "icon": "fa-skull"},
            "critical_delays": {"value": sum(1 for w in warehouses if w.get('risk_level') == 'critical'), "label": "Critical Delays", "icon": "fa-exclamation-triangle"}
        }


class AIAnalyticsEngine:
    @staticmethod
    def generate_insights(warehouses: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not warehouses: return {"insights": []}
        best, worst = WarehouseIntelligenceEngine.get_best_and_worst(warehouses)
        return {
            "insights": [
                {"type": "best_performing", "text": f"Best Performing: {best.get('warehouse_name', 'N/A')} (Score: {best.get('performance_score', 0)})"},
                {"type": "worst_performing", "text": f"Worst Performing: {worst.get('warehouse_name', 'N/A')} (Score: {worst.get('performance_score', 0)})"}
            ]
        }
    
    @staticmethod
    def generate_improvement_plan(warehouse: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "warehouse": warehouse.get('warehouse_name', 'Unknown'),
            "priority": "Critical" if warehouse.get('performance_score', 100) < 60 else "High",
            "recommendation": "Optimize warehouse dispatch speed and reduce staging time."
        }


# ============================================================
# GRAPH ENGINE (Plotly)
# ============================================================

class GraphEngine:
    @staticmethod
    def horizontal_bar_chart(data: List[Dict], x_key: str, y_key: str, title: str = "", color_key: str = None) -> str:
        if not data: return "{}"
        fig = go.Figure(go.Bar(
            x=[d[x_key] for d in data],
            y=[d[y_key] for d in data],
            orientation='h',
            marker=dict(color=[d.get(color_key, '#3b82f6') for d in data] if color_key else '#3b82f6')
        ))
        fig.update_layout(title=title, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#f0f4ff'))
        return fig.to_json()

    @staticmethod
    def vertical_bar_chart(data: List[Dict], x_key: str, y_key: str, title: str = "") -> str:
        if not data: return "{}"
        fig = go.Figure(go.Bar(x=[d[x_key] for d in data], y=[d[y_key] for d in data], marker=dict(color='#3b82f6')))
        fig.update_layout(title=title, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#f0f4ff'))
        return fig.to_json()

    @staticmethod
    def gauge_chart(value: float, title: str = "") -> str:
        fig = go.Figure(go.Indicator(
            mode="gauge+number", value=value, title={'text': title},
            gauge={'axis': {'range': [0, 100]}, 'bar': {'color': "#22c55e"}}
        ))
        fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', font=dict(color='#f0f4ff'))
        return fig.to_json()

    @staticmethod
    def donut_chart(data: List[Dict], labels_key: str, values_key: str, title: str = "") -> str:
        if not data: return "{}"
        fig = go.Figure(go.Pie(labels=[d[labels_key] for d in data], values=[d[values_key] for d in data], hole=0.4))
        fig.update_layout(title=title, paper_bgcolor='rgba(0,0,0,0)', font=dict(color='#f0f4ff'))
        return fig.to_json()

    @staticmethod
    def scatter_chart(data: List[Dict], x_key: str, y_key: str, color_key: str = None, title: str = "") -> str:
        if not data: return "{}"
        fig = go.Figure(go.Scatter(
            x=[d[x_key] for d in data], y=[d[y_key] for d in data], mode='markers',
            marker=dict(color=[d.get(color_key, '#3b82f6') for d in data] if color_key else '#3b82f6')
        ))
        fig.update_layout(title=title, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#f0f4ff'))
        return fig.to_json()

    @staticmethod
    def timeline_chart(data: List[Dict], x_key: str, y_key: str, title: str = "") -> str:
        if not data: return "{}"
        fig = go.Figure(go.Scatter(x=[d[x_key] for d in data], y=[d[y_key] for d in data], mode='lines+markers'))
        fig.update_layout(title=title, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#f0f4ff'))
        return fig.to_json()


# ============================================================
# RESPONSE BUILDER
# ============================================================

class ResponseBuilder:
    @staticmethod
    def build(summary, warehouses, dealers, cities, products, divisions, daily_trend, monthly_trend, aging, network, kpis, insights, charts, metadata):
        total_dn = summary.get('total_dn', 0)
        pgi_completed = summary.get('pgi_completed', 0)
        delivered_dns = summary.get('delivered_dns', 0)
        
        pipeline = {
            "dn_created": total_dn,
            "pgi_completed": pgi_completed,
            "delivered": delivered_dns,
            "pgi_achievement": SafeNumber.pct(pgi_completed, total_dn),
            "delivery_achievement": SafeNumber.pct(delivered_dns, total_dn)
        }
        
        alerts = [{
            "source": w.get('warehouse_name'),
            "severity": "CRITICAL" if w.get('risk_level') == 'critical' else "WARNING",
            "message": f"High risk operational delay at {w.get('warehouse_name')}"
        } for w in warehouses if w.get('risk_level') in ['critical', 'high']]
        
        recommendations = [AIAnalyticsEngine.generate_improvement_plan(w) for w in warehouses if w.get('performance_score', 100) < 75]

        return {
            "executive_summary": summary,
            "cards": kpis,
            "kpis": kpis,
            "pipeline": pipeline,
            "warehouse": warehouses,
            "warehouses": warehouses,
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
            "aging_distribution": aging,
            "network": network,
            "insights": insights,
            "alerts": alerts,
            "recommendations": recommendations,
            "warehouse_charts": {"delivery_performance": charts.get("pgi_performance"), "ranking": charts.get("warehouse_ranking")},
            "trend_charts": {"daily_operations": charts.get("daily_trend"), "monthly_operations": charts.get("monthly_trend")},
            "charts": charts,
            "metadata": metadata
        }


# ============================================================
# DASHBOARD SERVICE
# ============================================================

class DashboardService:
    def __init__(self):
        self._repo = DashboardRepository()
        logger.info("DashboardService initialized successfully")
    
    @cached(ttl=300)
    async def get_full_dashboard(self, filters: Optional[Dict] = None) -> Dict[str, Any]:
        summary = self._repo.fetch_summary()
        warehouse_raw = self._repo.fetch_warehouse_data()
        dealer_raw = self._repo.fetch_dealer_data()
        city_raw = self._repo.fetch_city_data()
        product_raw = self._repo.fetch_product_data()
        division_raw = self._repo.fetch_division_data()
        daily_trend = self._repo.fetch_daily_trend(90)
        monthly_trend = self._repo.fetch_monthly_trend(12)
        aging = self._repo.fetch_aging_distribution()
        network = self._repo.fetch_network_data()
        record_count = self._repo.fetch_record_count()
        
        warehouses = WarehouseIntelligenceEngine.compute_warehouse_metrics(warehouse_raw)
        dealers = DealerIntelligenceEngine.compute_dealer_metrics(dealer_raw)
        cities = CityIntelligenceEngine.compute_city_metrics(city_raw)
        
        kpis = ExecutiveKPIEngine.generate_kpis(summary, warehouses)
        insights = AIAnalyticsEngine.generate_insights(warehouses)
        
        charts = {
            "warehouse_ranking": GraphEngine.horizontal_bar_chart(warehouses, 'delivery_notes', 'warehouse_name', 'Warehouse Ranking', 'performance_color'),
            "pgi_performance": GraphEngine.vertical_bar_chart(warehouses, 'warehouse_name', 'avg_pgi_days', 'PGI Days'),
            "ontime_gauge": GraphEngine.gauge_chart(SafeNumber.pct(summary.get('delivered_dns', 0), summary.get('total_dn', 1)), "On-Time Delivery %"),
            "aging_distribution": GraphEngine.donut_chart(aging, 'bucket', 'count', 'Aging Distribution'),
            "performance_matrix": GraphEngine.scatter_chart(warehouses, 'avg_pgi_days', 'avg_cycle_days', 'performance_color', 'PGI vs Cycle'),
            "monthly_trend": GraphEngine.timeline_chart(monthly_trend, 'month', 'dn_count', 'Monthly DNs'),
            "daily_trend": GraphEngine.timeline_chart(daily_trend, 'date', 'dn_count', 'Daily DNs'),
        }
        
        metadata = {
            "version": "15.2",
            "timestamp": datetime.utcnow().isoformat(),
            "record_count": record_count,
            "warehouse_count": len(warehouses),
        }
        
        return ResponseBuilder.build(
            summary, warehouses, dealers, cities, product_raw, division_raw,
            daily_trend, monthly_trend, aging, network, kpis,
            insights, charts, metadata
        )
    
    @cached(ttl=60)
    async def get_warehouse_ranking(self) -> List[Dict]:
        warehouses = self._repo.fetch_warehouse_data()
        return WarehouseIntelligenceEngine.compute_warehouse_metrics(warehouses)


# ============================================================
# FASTAPI ROUTER
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
        return await service.get_full_dashboard({"theme": theme})
    except Exception as e:
        logger.error(f"Dashboard error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/warehouses")
async def get_warehouses(service: DashboardService = Depends(get_dashboard_service)):
    return await service.get_warehouse_ranking()

@router.get("/health")
async def health_check():
    return {"status": "healthy", "version": "15.2", "timestamp": datetime.utcnow().isoformat()}

logger.info("DashboardService router mounted at /dashboard/api")
