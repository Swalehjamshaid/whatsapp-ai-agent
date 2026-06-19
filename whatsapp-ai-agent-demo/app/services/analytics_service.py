# ==========================================================
# FILE: app/services/analytics_service.py (v14.1 - FULLY INTEGRATED)
# ==========================================================
# PURPOSE: PRIMARY ANALYTICS ENGINE - Direct PostgreSQL Integration
# VERSION: 14.1 - Full Integration with DN Methods
#
# ROLE: This file is the Analytics Brain.
#       This file must NEVER call Groq.
#       This file is responsible for:
#       * ALL Dashboard Generation (18 Dashboards)
#       * KPI Calculations
#       * Ranking Engine
#       * Risk Engine
#       * Control Tower Engine
#       * Forecasting Engine
#       * Distance Engine
#       * Benchmarking
#       * DN Verification (NEW)
#       * Sample DN Retrieval (NEW)
#
# CHANGES IN v14.1:
# - ✅ ADDED: verify_dn_exists() method
# - ✅ ADDED: get_sample_dns() method
# - ✅ ADDED: Proxy methods in AnalyticsService
# - ✅ 100% Integrated with ai_provider_service.py
# - ✅ Full WhatsApp compatibility maintained
#
# CRITICAL BUSINESS RULES:
# - Dealer Name = customer_name
# - Dealer Code = dealer_code
# - Customer Code = customer_code
# - DN Metrics: COUNT(DISTINCT dn_no)
# - Unit Metrics: SUM(dn_qty)
# - Revenue Metrics: SUM(dn_amount)
# - Never mix DN count and unit count
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import uuid
import re
import math
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func, text, and_, or_, desc, asc, cast, String, case
import os
from functools import lru_cache
import json

# ==========================================================
# HIGH PERFORMANCE LIBRARIES
# ==========================================================

# Polars - Primary Analytics Engine (10x faster than pandas)
try:
    import polars as pl
    POLARS_AVAILABLE = True
except:
    POLARS_AVAILABLE = False

# DuckDB - Heavy Aggregations
try:
    import duckdb
    DUCKDB_AVAILABLE = True
except:
    DUCKDB_AVAILABLE = False

# RapidFuzz - Dealer Matching (100x faster)
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except:
    from difflib import SequenceMatcher
    RAPIDFUZZ_AVAILABLE = False

# Geopy - Coordinate Resolution
try:
    from geopy.distance import geodesic
    GEOPY_AVAILABLE = True
except:
    GEOPY_AVAILABLE = False

# Redis - Caching
try:
    import redis
    REDIS_AVAILABLE = True
except:
    REDIS_AVAILABLE = False

# DiskCache - Fallback Cache
try:
    import diskcache as dc
    DISKCACHE_AVAILABLE = True
except:
    DISKCACHE_AVAILABLE = False

# StatsModels - Forecasting
try:
    import statsmodels.api as sm
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    from statsmodels.tsa.arima.model import ARIMA
    STATSMODELS_AVAILABLE = True
except:
    STATSMODELS_AVAILABLE = False


# ==========================================================
# LAZY IMPORTS - Avoid circular dependencies
# ==========================================================

def _get_schema_service():
    from app.schemas.schema_service import get_schema_service
    return get_schema_service()

def _get_kpi_service():
    from app.services.kpi_service import get_kpi_service
    return get_kpi_service()


# ==========================================================
# CONSTANTS - STANDARD FIELD MAPPING
# ==========================================================

DEALER_NAME_FIELD = "customer_name"
DEALER_CODE_FIELD = "dealer_code"
CUSTOMER_CODE_FIELD = "customer_code"
DN_NO_FIELD = "dn_no"
DN_QTY_FIELD = "dn_qty"
DN_AMOUNT_FIELD = "dn_amount"
DELIVERY_STATUS_FIELD = "delivery_status"
PGI_STATUS_FIELD = "pgi_status"
POD_STATUS_FIELD = "pod_status"
WAREHOUSE_CODE_FIELD = "warehouse_code"
DELIVERY_LOCATION_FIELD = "delivery_location"
DIVISION_FIELD = "division"
WAREHOUSE_FIELD = "warehouse"
SHIP_TO_CITY_FIELD = "ship_to_city"
SALES_OFFICE_FIELD = "sales_office"
SALES_MANAGER_FIELD = "sales_manager"
MATERIAL_NO_FIELD = "material_no"
CUSTOMER_MODEL_FIELD = "customer_model"
GOOD_ISSUE_DATE_FIELD = "good_issue_date"
POD_DATE_FIELD = "pod_date"
DN_CREATE_DATE_FIELD = "dn_create_date"
SOURCE_FILE_FIELD = "source_file"
PENDING_FLAG_FIELD = "pending_flag"

# ==========================================================
# DISTANCE & TRANSIT CONFIGURATION
# ==========================================================

EARTH_RADIUS_KM = 6371.0

TRANSIT_DAYS_RULES = {
    "same_city": 1,
    "0-50": 1,
    "51-150": 2,
    "151-300": 3,
    "301-500": 4,
    "501-800": 5,
    "800+": 7
}

RISK_THRESHOLDS = {
    "low": 0.10,    # <= 10% delay
    "medium": 0.30, # 11-30% delay
    "high": 0.30    # > 30% delay
}

# Recovery Settings
MAX_RECOVERY_ATTEMPTS = 3
RECOVERY_TIMEOUT_SECONDS = 10
CACHE_TTL_SECONDS = 300


# ==========================================================
# RAILWAY POSTGRESQL CONFIGURATION
# ==========================================================

class RailwayPostgresConfig:
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    
    @classmethod
    def is_railway(cls) -> bool:
        return bool(cls.DATABASE_URL)


# ==========================================================
# ENTERPRISE EXCEPTION HIERARCHY
# ==========================================================

class AnalyticsError(Exception):
    pass

class DealerNotFoundError(AnalyticsError):
    def __init__(self, dealer_name: str, error_id: str = None):
        self.dealer_name = dealer_name
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Dealer '{dealer_name}' not found (Error ID: {self.error_id})")

class DashboardGenerationError(AnalyticsError):
    def __init__(self, dealer_name: str, reason: str, error_id: str = None):
        self.dealer_name = dealer_name
        self.reason = reason
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Dashboard generation failed for '{dealer_name}': {reason} (Error ID: {self.error_id})")

class DatabaseQueryError(AnalyticsError):
    def __init__(self, query: str, error: str, error_id: str = None):
        self.query = query
        self.error = error
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Database query failed: {error} (Error ID: {self.error_id})")


# ==========================================================
# RESPONSE CONTRACT
# ==========================================================

class AnalyticsResponse:
    def __init__(self, success: bool = True, data: Dict[str, Any] = None, error: str = None, error_id: str = None, recovery_attempts: int = 0, groq_fallback: bool = False):
        self.success = success
        self.data = data or {}
        self.error = error
        self.error_id = error_id or str(uuid.uuid4())[:8]
        self.timestamp = datetime.now().isoformat()
        self.recovery_attempts = recovery_attempts
        self.groq_fallback = groq_fallback
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "error_id": self.error_id,
            "timestamp": self.timestamp,
            "recovery_attempts": self.recovery_attempts,
            "groq_fallback": self.groq_fallback
        }


# ==========================================================
# KPI ENGINE
# ==========================================================

class KPIEngine:
    """Enterprise KPI Calculation Engine"""
    
    @staticmethod
    def calculate_delivery_rate(delivered_dns: int, total_dns: int) -> float:
        """Calculate delivery rate percentage."""
        if total_dns == 0:
            return 0.0
        return round((delivered_dns / total_dns) * 100, 1)
    
    @staticmethod
    def calculate_pgi_rate(delivered_dns: int, transit_dns: int, total_dns: int) -> float:
        """Calculate PGI rate percentage."""
        if total_dns == 0:
            return 0.0
        return round(((delivered_dns + transit_dns) / total_dns) * 100, 1)
    
    @staticmethod
    def calculate_pod_rate(pod_completed_dns: int, delivered_dns: int) -> float:
        """Calculate POD rate percentage."""
        if delivered_dns == 0:
            return 0.0
        return round((pod_completed_dns / delivered_dns) * 100, 1)
    
    @staticmethod
    def calculate_health_score(metrics: Dict[str, float]) -> int:
        """Calculate overall health score (0-100)."""
        delivery_rate = metrics.get("delivery_rate", 0)
        pod_rate = metrics.get("pod_rate", 0)
        avg_aging = metrics.get("avg_aging", 0)
        revenue = metrics.get("revenue", 0)
        
        score = int(
            (min(delivery_rate / 90 * 100, 100) * 0.40) +
            (min(pod_rate / 90 * 100, 100) * 0.30) +
            (max(100 - min(avg_aging / 30 * 100, 100), 0) * 0.20) +
            (min(revenue / 1000000 * 100, 100) * 0.10)
        )
        return min(score, 100)
    
    @staticmethod
    def calculate_risk_level(delivery_rate: float, pod_rate: float, avg_aging: float) -> Tuple[str, float]:
        """Calculate risk level and score."""
        delivery_risk = 0 if delivery_rate >= 90 else 50 if delivery_rate >= 70 else 100
        pod_risk = 0 if pod_rate >= 90 else 50 if pod_rate >= 70 else 100
        aging_risk = 0 if avg_aging <= 3 else 50 if avg_aging <= 14 else 100
        
        risk_score = (delivery_risk + pod_risk + aging_risk) // 3
        
        if risk_score <= 25:
            return "Low", risk_score
        elif risk_score <= 50:
            return "Medium", risk_score
        else:
            return "High", risk_score
    
    @staticmethod
    def calculate_growth_rate(current: float, previous: float) -> float:
        """Calculate growth rate percentage."""
        if previous == 0:
            return 0.0
        return round(((current - previous) / previous) * 100, 1)


# ==========================================================
# RANKING ENGINE
# ==========================================================

class RankingEngine:
    """Enterprise Ranking Engine"""
    
    def __init__(self, repo):
        self.repo = repo
    
    def get_dealer_ranking(self, metric: str = "revenue", limit: int = 10, top: bool = True) -> List[Dict[str, Any]]:
        """Get dealer ranking by specified metric."""
        metric_map = {
            "revenue": DN_AMOUNT_FIELD,
            "units": DN_QTY_FIELD,
            "dns": DN_NO_FIELD,
            "delivery": "delivery_rate",
            "pod": "pod_rate"
        }
        
        db_field = metric_map.get(metric, DN_AMOUNT_FIELD)
        
        # Build query
        query = self.repo.db.query(
            DeliveryReport.customer_name.label("dealer_name"),
            func.sum(DeliveryReport.dn_amount).label("revenue"),
            func.sum(DeliveryReport.dn_qty).label("units"),
            func.count(func.distinct(DeliveryReport.dn_no)).label("dns"),
            func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered")
        ).filter(
            DeliveryReport.customer_name.isnot(None),
            DeliveryReport.customer_name != ''
        ).group_by(
            DeliveryReport.customer_name
        )
        
        # Apply ordering
        if metric == "revenue":
            query = query.order_by(desc(func.sum(DeliveryReport.dn_amount)))
        elif metric == "units":
            query = query.order_by(desc(func.sum(DeliveryReport.dn_qty)))
        elif metric == "dns":
            query = query.order_by(desc(func.count(func.distinct(DeliveryReport.dn_no))))
        else:
            query = query.order_by(desc(func.sum(DeliveryReport.dn_amount)))
        
        results = query.limit(limit).all()
        
        dealers = []
        for r in results:
            total_dns = r.dns or 1
            delivery_rate = KPIEngine.calculate_delivery_rate(r.delivered or 0, total_dns)
            dealers.append({
                "dealer_name": r.dealer_name or "Unknown",
                "revenue": float(r.revenue or 0),
                "units": int(r.units or 0),
                "dns": r.dns or 0,
                "delivery_rate": delivery_rate
            })
        
        return dealers
    
    def get_warehouse_ranking(self, metric: str = "revenue", limit: int = 10, top: bool = True) -> List[Dict[str, Any]]:
        """Get warehouse ranking by specified metric."""
        results = self.repo.db.query(
            DeliveryReport.warehouse.label("warehouse"),
            func.sum(DeliveryReport.dn_amount).label("revenue"),
            func.sum(DeliveryReport.dn_qty).label("units"),
            func.count(func.distinct(DeliveryReport.dn_no)).label("dns"),
            func.count(func.distinct(DeliveryReport.customer_name)).label("dealers")
        ).filter(
            DeliveryReport.warehouse.isnot(None),
            DeliveryReport.warehouse != ''
        ).group_by(
            DeliveryReport.warehouse
        ).order_by(desc(func.sum(DeliveryReport.dn_amount))).limit(limit).all()
        
        warehouses = []
        for r in results:
            warehouses.append({
                "warehouse": r.warehouse or "Unknown",
                "revenue": float(r.revenue or 0),
                "units": int(r.units or 0),
                "dns": r.dns or 0,
                "dealers": r.dealers or 0
            })
        
        return warehouses
    
    def get_city_ranking(self, metric: str = "revenue", limit: int = 10, top: bool = True) -> List[Dict[str, Any]]:
        """Get city ranking by specified metric."""
        results = self.repo.db.query(
            DeliveryReport.ship_to_city.label("city"),
            func.sum(DeliveryReport.dn_amount).label("revenue"),
            func.sum(DeliveryReport.dn_qty).label("units"),
            func.count(func.distinct(DeliveryReport.dn_no)).label("dns"),
            func.count(func.distinct(DeliveryReport.customer_name)).label("dealers")
        ).filter(
            DeliveryReport.ship_to_city.isnot(None),
            DeliveryReport.ship_to_city != ''
        ).group_by(
            DeliveryReport.ship_to_city
        ).order_by(desc(func.sum(DeliveryReport.dn_amount))).limit(limit).all()
        
        cities = []
        for r in results:
            cities.append({
                "city": r.city or "Unknown",
                "revenue": float(r.revenue or 0),
                "units": int(r.units or 0),
                "dns": r.dns or 0,
                "dealers": r.dealers or 0
            })
        
        return cities
    
    def get_product_ranking(self, limit: int = 10, top: bool = True) -> List[Dict[str, Any]]:
        """Get product ranking by revenue."""
        results = self.repo.db.query(
            func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no, 'UNKNOWN').label("product"),
            func.sum(DeliveryReport.dn_amount).label("revenue"),
            func.sum(DeliveryReport.dn_qty).label("units"),
            func.count(func.distinct(DeliveryReport.dn_no)).label("dns"),
            func.count(func.distinct(DeliveryReport.ship_to_city)).label("cities")
        ).filter(
            DeliveryReport.customer_model.isnot(None)
        ).group_by(
            DeliveryReport.customer_model,
            DeliveryReport.material_no
        ).order_by(desc(func.sum(DeliveryReport.dn_amount))).limit(limit).all()
        
        products = []
        for r in results:
            products.append({
                "product": r.product or "Unknown",
                "revenue": float(r.revenue or 0),
                "units": int(r.units or 0),
                "dns": r.dns or 0,
                "cities": r.cities or 0
            })
        
        return products


# ==========================================================
# RISK ENGINE
# ==========================================================

class RiskEngine:
    """Enterprise Risk Assessment Engine"""
    
    def __init__(self, repo):
        self.repo = repo
        self.risk_cache = {}
    
    def assess_dealer_risk(self, dealer_name: str) -> Dict[str, Any]:
        """Assess risk for a specific dealer."""
        dashboard = self.repo.get_dealer_dashboard(dealer_name)
        if "error" in dashboard:
            return {"risk_level": "Unknown", "risk_score": 0}
        
        delivery_rate = dashboard.get("delivery_rate", 0)
        pod_rate = dashboard.get("pod_rate", 0)
        avg_aging = dashboard.get("avg_total_aging", 0)
        
        risk_level, risk_score = KPIEngine.calculate_risk_level(delivery_rate, pod_rate, avg_aging)
        
        return {
            "dealer_name": dealer_name,
            "risk_level": risk_level,
            "risk_score": risk_score,
            "delivery_rate": delivery_rate,
            "pod_rate": pod_rate,
            "avg_aging": avg_aging
        }
    
    def assess_warehouse_risk(self, warehouse_name: str) -> Dict[str, Any]:
        """Assess risk for a specific warehouse."""
        dashboard = self.repo.get_warehouse_dashboard(warehouse_name)
        if "error" in dashboard:
            return {"risk_level": "Unknown", "risk_score": 0}
        
        summary = dashboard.get("summary", {})
        delivery_rate = summary.get("delivery_rate", 0)
        pod_rate = summary.get("pod_rate", 0)
        pending_dns = summary.get("pending_dns", 0)
        
        # Warehouse specific risk calculation
        delivery_risk = 0 if delivery_rate >= 90 else 50 if delivery_rate >= 70 else 100
        pod_risk = 0 if pod_rate >= 90 else 50 if pod_rate >= 70 else 100
        pending_risk = 0 if pending_dns < 50 else 50 if pending_dns < 200 else 100
        
        risk_score = (delivery_risk + pod_risk + pending_risk) // 3
        
        if risk_score <= 25:
            risk_level = "Low"
        elif risk_score <= 50:
            risk_level = "Medium"
        else:
            risk_level = "High"
        
        return {
            "warehouse_name": warehouse_name,
            "risk_level": risk_level,
            "risk_score": risk_score,
            "delivery_rate": delivery_rate,
            "pod_rate": pod_rate,
            "pending_dns": pending_dns
        }
    
    def get_high_risk_areas(self, threshold: int = 50) -> List[Dict[str, Any]]:
        """Get high risk areas (cities)."""
        results = self.repo.db.query(
            DeliveryReport.ship_to_city.label("city"),
            func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
            func.count(func.distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns")
        ).filter(
            DeliveryReport.ship_to_city.isnot(None),
            DeliveryReport.ship_to_city != ''
        ).group_by(
            DeliveryReport.ship_to_city
        ).having(
            func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))) > threshold
        ).all()
        
        high_risk_areas = []
        for r in results:
            high_risk_areas.append({
                "city": r.city,
                "total_dns": r.total_dns or 0,
                "pending_dns": r.pending_dns or 0,
                "pending_pod_dns": r.pending_pod_dns or 0,
                "risk_score": round((r.pending_dns or 0) / (r.total_dns or 1) * 100, 1)
            })
        
        return sorted(high_risk_areas, key=lambda x: x["risk_score"], reverse=True)


# ==========================================================
# FORECASTING ENGINE
# ==========================================================

class ForecastingEngine:
    """Enterprise Forecasting Engine"""
    
    def __init__(self, repo):
        self.repo = repo
    
    def forecast_revenue(self, dealer_name: Optional[str] = None, periods: int = 3) -> Dict[str, Any]:
        """Forecast revenue for next N periods."""
        # Get historical data
        if dealer_name:
            resolved = self.repo.resolve_dealer(dealer_name)
            if not resolved:
                return {"error": f"Dealer '{dealer_name}' not found"}
            trends = self.repo.get_dealer_monthly_trend(resolved, 12)
        else:
            trends = self.repo.get_national_monthly_trend(12)
        
        if not trends or len(trends) < 3:
            return {"forecast": [], "confidence": 0, "trend": "insufficient_data"}
        
        # Simple trend-based forecasting
        revenues = [t.get("revenue", 0) for t in trends]
        units = [t.get("units", 0) for t in trends]
        dns = [t.get("dns", 0) for t in trends]
        
        if len(revenues) >= 3:
            # Calculate growth rates
            revenue_growth = (revenues[-1] - revenues[0]) / max(revenues[0], 1)
            units_growth = (units[-1] - units[0]) / max(units[0], 1)
            dns_growth = (dns[-1] - dns[0]) / max(dns[0], 1)
            
            avg_revenue = sum(revenues[-3:]) / 3
            avg_units = sum(units[-3:]) / 3
            avg_dns = sum(dns[-3:]) / 3
            
            forecast = []
            for i in range(1, periods + 1):
                forecast.append({
                    "period": f"Month +{i}",
                    "revenue": avg_revenue * (1 + revenue_growth * i),
                    "units": avg_units * (1 + units_growth * i),
                    "dns": avg_dns * (1 + dns_growth * i)
                })
            
            confidence = min(0.95, 0.70 + (len(trends) * 0.02))
            trend = "increasing" if revenue_growth > 0.05 else "decreasing" if revenue_growth < -0.05 else "stable"
            
            return {
                "forecast": forecast,
                "confidence": round(confidence, 2),
                "trend": trend,
                "growth_rate": round(revenue_growth * 100, 1),
                "historical": trends[-3:]
            }
        
        return {"forecast": [], "confidence": 0, "trend": "insufficient_data"}


# ==========================================================
# DISTANCE ENGINE (Enhanced)
# ==========================================================

class DistanceEngine:
    """Enhanced Distance and Transit Engine"""
    
    def __init__(self, repo):
        self.repo = repo
        self.warehouse_coords = self.repo._warehouse_coords
    
    def calculate_distance(self, warehouse: str, dealer: str) -> Dict[str, Any]:
        """Calculate distance between warehouse and dealer with transit info."""
        # Check cache
        cache_key = f"dist:{warehouse}:{dealer}"
        cached = self.repo._get_cached(cache_key)
        if cached:
            return cached
        
        # Get warehouse coordinates
        wh_coords = self.warehouse_coords.get(warehouse.lower())
        if not wh_coords:
            return {"distance_km": 0, "transit_days": 0, "status": "unknown"}
        
        # Get dealer coordinates
        dealer_dashboard = self.repo.get_dealer_dashboard(dealer)
        if "error" in dealer_dashboard:
            return {"distance_km": 0, "transit_days": 0, "status": "unknown"}
        
        # Check same city
        dealer_city = dealer_dashboard.get("city", "").lower()
        warehouse_city = warehouse.lower()
        
        if dealer_city and dealer_city == warehouse_city:
            result = {"distance_km": 0, "transit_days": 1, "status": "same_city"}
            self.repo._set_cached(cache_key, result)
            return result
        
        # Calculate distance
        if GEOPY_AVAILABLE and dealer_dashboard.get("latitude") and dealer_dashboard.get("longitude"):
            try:
                distance = geodesic(
                    (wh_coords[0], wh_coords[1]),
                    (dealer_dashboard["latitude"], dealer_dashboard["longitude"])
                ).kilometers
            except:
                distance = 0
        else:
            distance = 0
        
        # Calculate transit days
        transit_days = self.calculate_transit_days(distance)
        
        result = {
            "distance_km": round(distance, 1),
            "transit_days": transit_days,
            "status": "calculated",
            "route_type": self.get_route_type(distance)
        }
        
        self.repo._set_cached(cache_key, result)
        return result
    
    def calculate_transit_days(self, distance_km: float) -> int:
        """Calculate expected transit days based on distance."""
        if distance_km <= 0:
            return 1
        elif distance_km <= 50:
            return 1
        elif distance_km <= 150:
            return 2
        elif distance_km <= 300:
            return 3
        elif distance_km <= 500:
            return 4
        elif distance_km <= 800:
            return 5
        else:
            return 7
    
    def get_route_type(self, distance_km: float) -> str:
        """Get route type based on distance."""
        if distance_km <= 0:
            return "Same City"
        elif distance_km <= 50:
            return "Short"
        elif distance_km <= 150:
            return "Medium"
        elif distance_km <= 300:
            return "Long"
        elif distance_km <= 500:
            return "Extended"
        else:
            return "Very Long"
    
    def calculate_route_risk(self, distance_km: float, transit_days: int) -> str:
        """Calculate route risk based on distance and transit days."""
        if distance_km <= 50:
            return "Low"
        elif distance_km <= 150:
            return "Low"
        elif distance_km <= 300:
            return "Medium"
        elif distance_km <= 500:
            return "Medium"
        else:
            return "High"


# ==========================================================
# DASHBOARD BUILDER
# ==========================================================

class DashboardBuilder:
    """Enterprise Dashboard Builder Pattern"""
    
    def __init__(self, repo):
        self.repo = repo
        self.kpi_engine = KPIEngine()
        self.ranking_engine = RankingEngine(repo)
        self.risk_engine = RiskEngine(repo)
        self.forecast_engine = ForecastingEngine(repo)
        self.distance_engine = DistanceEngine(repo)
    
    # ==========================================================
    # 1. DEALER DASHBOARD (360 Degree)
    # ==========================================================
    
    def build_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Build comprehensive 360-degree dealer dashboard."""
        try:
            resolved = self.repo.resolve_dealer(dealer_name)
            if not resolved:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            # Get base data
            base_data = self.repo.get_dealer_dashboard(resolved)
            if "error" in base_data:
                return base_data
            
            # Get distance
            warehouse = base_data.get("warehouse", "")
            distance_info = {}
            if warehouse:
                distance_info = self.distance_engine.calculate_distance(warehouse, resolved)
            
            # Get ranking
            ranking = self.ranking_engine.get_dealer_ranking(limit=100)
            dealer_rank = None
            for i, d in enumerate(ranking, 1):
                if d.get("dealer_name") == resolved:
                    dealer_rank = {"rank": i, "total": len(ranking)}
                    break
            
            # Build profile
            profile = {
                "dealer_name": resolved,
                "dealer_code": base_data.get("dealer_code") or "",
                "customer_code": base_data.get("customer_code") or "",
                "division": base_data.get("division") or "",
                "sales_office": base_data.get("sales_office") or "",
                "warehouse": warehouse,
                "city": base_data.get("city") or "",
                "dealer_status": base_data.get("dealer_status") or "Active"
            }
            
            # Build summary
            total_dns = base_data.get("total_dns", 0)
            delivered_dns = base_data.get("delivered_dns", 0)
            transit_dns = base_data.get("transit_dns", 0)
            pod_completed_dns = base_data.get("pod_completed_dns", 0)
            
            summary = {
                "total_dns": total_dns,
                "total_units": base_data.get("total_units", 0),
                "total_revenue": base_data.get("total_revenue", 0),
                "delivered_dns": delivered_dns,
                "pending_dns": base_data.get("pending_dns", 0),
                "transit_dns": transit_dns,
                "pending_pod_dns": base_data.get("pending_pod_dns", 0),
                "pending_flag_dns": base_data.get("pending_flag_dns", 0),
                "delivery_rate": base_data.get("delivery_rate", 0),
                "pgi_rate": base_data.get("pgi_rate", 0),
                "pod_rate": base_data.get("pod_rate", 0),
                "avg_pgi_aging": base_data.get("avg_pgi_aging", 0),
                "avg_pod_aging": base_data.get("avg_pod_aging", 0),
                "avg_total_aging": base_data.get("avg_total_aging", 0)
            }
            
            # Build performance
            health_score = KPIEngine.calculate_health_score(summary)
            risk_level, risk_score = KPIEngine.calculate_risk_level(
                summary["delivery_rate"],
                summary["pod_rate"],
                summary["avg_total_aging"]
            )
            
            performance = {
                "health_score": health_score,
                "risk_level": risk_level,
                "risk_score": risk_score,
                "rank": dealer_rank
            }
            
            return {
                "profile": profile,
                "summary": summary,
                "performance": performance,
                "distance": distance_info,
                "products": base_data.get("products", []),
                "monthly_trend": base_data.get("monthly_trend", []),
                "timeline": base_data.get("timeline", [])
            }
            
        except Exception as e:
            logger.error(f"Build dealer dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 2. WAREHOUSE DASHBOARD
    # ==========================================================
    
    def build_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Build comprehensive warehouse dashboard."""
        try:
            base_data = self.repo.get_warehouse_dashboard(warehouse_name)
            if "error" in base_data:
                return base_data
            
            summary = base_data.get("summary", {})
            
            # Get ranking
            ranking = self.ranking_engine.get_warehouse_ranking(limit=100)
            warehouse_rank = None
            for i, w in enumerate(ranking, 1):
                if w.get("warehouse") == warehouse_name:
                    warehouse_rank = {"rank": i, "total": len(ranking)}
                    break
            
            # Get top dealers
            top_dealers = self.ranking_engine.get_dealer_ranking(limit=5)
            
            # Get top products
            top_products = self.ranking_engine.get_product_ranking(limit=5)
            
            # Get distance coverage
            distance_coverage = self._get_warehouse_distance_coverage(warehouse_name)
            
            return {
                "profile": {
                    "warehouse": warehouse_name,
                    "code": base_data.get("warehouse_code") or ""
                },
                "summary": {
                    "total_dns": summary.get("total_dns", 0),
                    "total_units": summary.get("total_units", 0),
                    "total_revenue": summary.get("total_revenue", 0),
                    "total_dealers": summary.get("total_dealers", 0),
                    "cities_served": summary.get("cities_served", 0),
                    "delivery_rate": summary.get("delivery_rate", 0),
                    "pgi_rate": summary.get("pgi_rate", 0),
                    "pod_rate": summary.get("pod_rate", 0),
                    "pending_dns": summary.get("pending_dns", 0),
                    "pending_pod_dns": summary.get("pending_pod_dns", 0)
                },
                "performance": {
                    "rank": warehouse_rank,
                    "health_score": KPIEngine.calculate_health_score(summary)
                },
                "top_dealers": top_dealers[:5],
                "top_products": top_products[:5],
                "distance_coverage": distance_coverage,
                "monthly_trend": base_data.get("monthly_trend", {})
            }
            
        except Exception as e:
            logger.error(f"Build warehouse dashboard failed: {e}")
            return {"error": str(e)}
    
    def _get_warehouse_distance_coverage(self, warehouse_name: str) -> Dict[str, Any]:
        """Get distance coverage for warehouse."""
        try:
            # Get all dealers for this warehouse
            results = self.repo.db.query(
                DeliveryReport.customer_name.label("dealer"),
                DeliveryReport.ship_to_city.label("city")
            ).filter(
                DeliveryReport.warehouse == warehouse_name,
                DeliveryReport.customer_name.isnot(None)
            ).distinct().all()
            
            distances = []
            for r in results:
                if r.dealer:
                    dist = self.distance_engine.calculate_distance(warehouse_name, r.dealer)
                    distances.append({
                        "dealer": r.dealer,
                        "city": r.city or "Unknown",
                        "distance_km": dist.get("distance_km", 0),
                        "transit_days": dist.get("transit_days", 0)
                    })
            
            avg_distance = sum(d["distance_km"] for d in distances) / len(distances) if distances else 0
            max_distance = max(d["distance_km"] for d in distances) if distances else 0
            
            return {
                "total_dealers": len(distances),
                "avg_distance_km": round(avg_distance, 1),
                "max_distance_km": round(max_distance, 1),
                "distances": distances[:10]  # Top 10 for display
            }
            
        except Exception as e:
            logger.error(f"Get warehouse distance coverage failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 3. CITY DASHBOARD
    # ==========================================================
    
    def build_city_dashboard(self, city_name: str) -> Dict[str, Any]:
        """Build comprehensive city dashboard."""
        try:
            base_data = self.repo.get_city_dashboard(city_name)
            if "error" in base_data:
                return base_data
            
            summary = base_data.get("summary", {})
            
            # Get ranking
            ranking = self.ranking_engine.get_city_ranking(limit=100)
            city_rank = None
            for i, c in enumerate(ranking, 1):
                if c.get("city") == city_name:
                    city_rank = {"rank": i, "total": len(ranking)}
                    break
            
            # Get top dealers
            top_dealers = self.repo.get_city_top_dealers(city_name, 5)
            
            # Get top products
            top_products = self.repo.get_city_top_products(city_name, 5)
            
            return {
                "profile": {
                    "city": city_name
                },
                "summary": {
                    "total_dns": summary.get("total_dns", 0),
                    "total_units": summary.get("total_units", 0),
                    "total_revenue": summary.get("total_revenue", 0),
                    "total_dealers": summary.get("total_dealers", 0),
                    "total_warehouses": summary.get("total_warehouses", 0),
                    "delivery_rate": summary.get("delivery_rate", 0),
                    "pgi_rate": summary.get("pgi_rate", 0),
                    "pod_rate": summary.get("pod_rate", 0),
                    "pending_dns": summary.get("pending_dns", 0),
                    "pending_pod_dns": summary.get("pending_pod_dns", 0)
                },
                "performance": {
                    "rank": city_rank
                },
                "top_dealers": top_dealers,
                "top_products": top_products,
                "monthly_trend": base_data.get("monthly_trend", {})
            }
            
        except Exception as e:
            logger.error(f"Build city dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 4. PRODUCT DASHBOARD
    # ==========================================================
    
    def build_product_dashboard(self, product_name: Optional[str] = None) -> Dict[str, Any]:
        """Build comprehensive product dashboard."""
        try:
            query = self.repo.db.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no, 'UNKNOWN').label("product"),
                func.sum(DeliveryReport.dn_amount).label("revenue"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("dns"),
                func.count(func.distinct(DeliveryReport.customer_name)).label("dealers"),
                func.count(func.distinct(DeliveryReport.ship_to_city)).label("cities"),
                func.count(func.distinct(DeliveryReport.warehouse)).label("warehouses")
            )
            
            if product_name:
                query = query.filter(
                    or_(
                        DeliveryReport.customer_model.ilike(f"%{product_name}%"),
                        DeliveryReport.material_no.ilike(f"%{product_name}%")
                    )
                )
            
            result = query.group_by(
                DeliveryReport.customer_model,
                DeliveryReport.material_no
            ).order_by(desc(func.sum(DeliveryReport.dn_amount))).first()
            
            if not result:
                return {"error": f"Product '{product_name}' not found" if product_name else "No products found"}
            
            # Get top dealers for this product
            top_dealers = self.repo.db.query(
                DeliveryReport.customer_name.label("dealer"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                or_(
                    DeliveryReport.customer_model == result.product,
                    DeliveryReport.material_no == result.product
                ),
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(desc(func.sum(DeliveryReport.dn_amount))).limit(5).all()
            
            return {
                "profile": {
                    "product": result.product,
                    "material": result.product
                },
                "summary": {
                    "revenue": float(result.revenue or 0),
                    "units": int(result.units or 0),
                    "dns": result.dns or 0,
                    "dealers": result.dealers or 0,
                    "cities": result.cities or 0,
                    "warehouses": result.warehouses or 0
                },
                "top_dealers": [{"dealer": d.dealer, "revenue": float(d.revenue or 0)} for d in top_dealers]
            }
            
        except Exception as e:
            logger.error(f"Build product dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 5. EXECUTIVE DASHBOARD
    # ==========================================================
    
    def build_executive_dashboard(self) -> Dict[str, Any]:
        """Build comprehensive executive dashboard."""
        try:
            # National KPIs
            national = self.repo.get_national_kpis()
            
            # Top lists
            top_dealers = self.ranking_engine.get_dealer_ranking(limit=10)
            top_warehouses = self.ranking_engine.get_warehouse_ranking(limit=10)
            top_cities = self.ranking_engine.get_city_ranking(limit=10)
            top_products = self.ranking_engine.get_product_ranking(limit=10)
            
            # Critical risks
            high_risk_areas = self.risk_engine.get_high_risk_areas()
            high_risk_dealers = self.repo.get_high_risk_dealers(5)
            
            # Health score
            health_score = KPIEngine.calculate_health_score(national)
            risk_level, risk_score = KPIEngine.calculate_risk_level(
                national.get("delivery_rate", 0),
                national.get("pod_rate", 0),
                national.get("avg_aging", 0)
            )
            
            return {
                "national_kpis": national,
                "health_score": health_score,
                "risk_level": risk_level,
                "risk_score": risk_score,
                "top_dealers": top_dealers[:10],
                "top_warehouses": top_warehouses[:10],
                "top_cities": top_cities[:10],
                "top_products": top_products[:10],
                "critical_risks": {
                    "high_risk_areas": high_risk_areas[:5],
                    "high_risk_dealers": high_risk_dealers
                },
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Build executive dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 6. CONTROL TOWER
    # ==========================================================
    
    def build_control_tower(self) -> Dict[str, Any]:
        """Build logistics control tower dashboard."""
        try:
            # Live alerts
            alerts = []
            
            # Check PGI aging
            pgi_aging = self.repo.get_pgi_aging_alerts()
            if pgi_aging:
                alerts.extend(pgi_aging)
            
            # Check POD aging
            pod_aging = self.repo.get_pod_aging_alerts()
            if pod_aging:
                alerts.extend(pod_aging)
            
            # Check delayed deliveries
            delayed = self.repo.get_delayed_deliveries()
            if delayed:
                alerts.extend(delayed)
            
            # Check warehouse risks
            warehouse_risks = self.repo.get_warehouse_risks()
            if warehouse_risks:
                alerts.extend(warehouse_risks)
            
            # Check dealer risks
            dealer_risks = self.repo.get_dealer_risks()
            if dealer_risks:
                alerts.extend(dealer_risks)
            
            # Sort alerts by severity
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            alerts.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 4))
            
            return {
                "alerts": alerts[:20],
                "critical_count": sum(1 for a in alerts if a.get("severity") == "critical"),
                "high_count": sum(1 for a in alerts if a.get("severity") == "high"),
                "total_alerts": len(alerts),
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Build control tower failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 7. DISTANCE DASHBOARD
    # ==========================================================
    
    def build_distance_dashboard(self, dealer_name: str, warehouse_name: Optional[str] = None) -> Dict[str, Any]:
        """Build distance dashboard for dealer-warehouse pair."""
        try:
            resolved = self.repo.resolve_dealer(dealer_name)
            if not resolved:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            # Get dealer data
            dealer_dashboard = self.repo.get_dealer_dashboard(resolved)
            if "error" in dealer_dashboard:
                return dealer_dashboard
            
            # Get warehouse
            if not warehouse_name:
                warehouse_name = dealer_dashboard.get("warehouse", "")
            
            if not warehouse_name:
                return {"error": "No warehouse found for this dealer"}
            
            # Calculate distance
            distance_info = self.distance_engine.calculate_distance(warehouse_name, resolved)
            
            # Calculate route risk
            route_risk = self.distance_engine.calculate_route_risk(
                distance_info.get("distance_km", 0),
                distance_info.get("transit_days", 0)
            )
            
            return {
                "dealer": resolved,
                "warehouse": warehouse_name,
                "distance_km": distance_info.get("distance_km", 0),
                "transit_days": distance_info.get("transit_days", 0),
                "route_type": distance_info.get("route_type", "Unknown"),
                "route_risk": route_risk,
                "status": distance_info.get("status", "unknown")
            }
            
        except Exception as e:
            logger.error(f"Build distance dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 8. TRANSPORTER DASHBOARD
    # ==========================================================
    
    def build_transporter_dashboard(self, transporter_name: Optional[str] = None) -> Dict[str, Any]:
        """Build transporter dashboard."""
        try:
            # Get transporter performance metrics
            results = self.repo.db.query(
                DeliveryReport.sales_manager.label("transporter"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.avg(case((and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.good_issue_date.isnot(None)), func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400), else_=None)).label("avg_delivery_days")
            ).filter(
                DeliveryReport.sales_manager.isnot(None),
                DeliveryReport.sales_manager != ''
            )
            
            if transporter_name:
                results = results.filter(DeliveryReport.sales_manager.ilike(f"%{transporter_name}%"))
            
            result = results.group_by(DeliveryReport.sales_manager).first()
            
            if not result:
                return {"error": f"Transporter '{transporter_name}' not found" if transporter_name else "No transporter data"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            delivery_rate = KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            
            return {
                "profile": {
                    "transporter": result.transporter or "Unknown"
                },
                "summary": {
                    "total_dns": total_dns,
                    "total_units": int(result.total_units or 0),
                    "total_revenue": float(result.total_revenue or 0),
                    "delivered_dns": delivered_dns,
                    "delivery_rate": delivery_rate,
                    "avg_delivery_days": round(result.avg_delivery_days or 0, 1)
                },
                "performance": {
                    "health_score": KPIEngine.calculate_health_score({
                        "delivery_rate": delivery_rate,
                        "pod_rate": 0,
                        "avg_aging": result.avg_delivery_days or 0,
                        "revenue": float(result.total_revenue or 0)
                    })
                }
            }
            
        except Exception as e:
            logger.error(f"Build transporter dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 9. INVENTORY DASHBOARD
    # ==========================================================
    
    def build_inventory_dashboard(self, warehouse_name: Optional[str] = None) -> Dict[str, Any]:
        """Build inventory dashboard."""
        try:
            query = self.repo.db.query(
                DeliveryReport.material_no.label("material"),
                func.coalesce(DeliveryReport.customer_model, 'UNKNOWN').label("model"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.max(DeliveryReport.dn_create_date).label("last_movement"),
                func.avg(case((and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.good_issue_date.isnot(None)), func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400), else_=None)).label("avg_turnover_days")
            )
            
            if warehouse_name:
                query = query.filter(DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"))
            
            results = query.filter(
                DeliveryReport.material_no.isnot(None)
            ).group_by(
                DeliveryReport.material_no,
                DeliveryReport.customer_model
            ).order_by(desc(func.sum(DeliveryReport.dn_qty))).limit(50).all()
            
            inventory_items = []
            for r in results:
                total_units = int(r.total_units or 0)
                inventory_items.append({
                    "material": r.material or "Unknown",
                    "model": r.model or "Unknown",
                    "total_units": total_units,
                    "total_dns": r.total_dns or 0,
                    "avg_turnover_days": round(r.avg_turnover_days or 0, 1),
                    "last_movement": r.last_movement.strftime("%Y-%m-%d") if r.last_movement else "Never",
                    "status": "Fast Moving" if total_units > 100 else "Slow Moving" if total_units > 10 else "Slow Moving"
                })
            
            return {
                "summary": {
                    "total_materials": len(inventory_items),
                    "total_units": sum(i["total_units"] for i in inventory_items),
                    "avg_turnover_days": round(sum(i["avg_turnover_days"] for i in inventory_items) / len(inventory_items) if inventory_items else 0, 1)
                },
                "inventory_items": inventory_items[:20],
                "fast_moving": [i for i in inventory_items if i.get("status") == "Fast Moving"][:10],
                "slow_moving": [i for i in inventory_items if i.get("status") == "Slow Moving"][:10]
            }
            
        except Exception as e:
            logger.error(f"Build inventory dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 10. FORECAST DASHBOARD
    # ==========================================================
    
    def build_forecast_dashboard(self, dealer_name: Optional[str] = None) -> Dict[str, Any]:
        """Build forecast dashboard."""
        try:
            if dealer_name:
                resolved = self.repo.resolve_dealer(dealer_name)
                if not resolved:
                    return {"error": f"Dealer '{dealer_name}' not found"}
                forecast = self.forecast_engine.forecast_revenue(resolved)
                dealer = resolved
            else:
                forecast = self.forecast_engine.forecast_revenue()
                dealer = None
            
            return {
                "dealer": dealer,
                "forecast": forecast,
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Build forecast dashboard failed: {e}")
            return {"error": str(e)}


# ==========================================================
# ANALYTICS REPOSITORY - PRIMARY DATA ACCESS LAYER
# ==========================================================

class AnalyticsRepository:
    """PRIMARY DATA ACCESS LAYER - Direct PostgreSQL queries with recovery."""
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or SessionLocal()
        self._owned_db = db is None
        self.table_name = "delivery_reports"
        
        # ==========================================================
        # WAREHOUSE COORDINATES CACHE
        # ==========================================================
        
        self._warehouse_coords: Dict[str, Tuple[float, float]] = {
            "lahore": (31.5204, 74.3587),
            "karachi": (24.8607, 67.0011),
            "rawalpindi": (33.5651, 73.0169),
            "faisalabad": (31.4504, 73.1350),
            "multan": (30.1575, 71.5249),
            "hyderabad": (25.3960, 68.3578),
            "peshawar": (34.0151, 71.5249),
            "quetta": (30.1798, 66.9750),
            "islamabad": (33.6844, 73.0479),
            "gujranwala": (32.1877, 74.1945),
            "sialkot": (32.4945, 74.5227),
            "sukkur": (27.7036, 68.8578),
            "larkana": (27.5622, 68.2024),
            "sahiwal": (30.6681, 73.1033),
            "okara": (30.8089, 73.4516),
            "bahawalpur": (29.3981, 71.6757),
            "dera ghazi khan": (30.0478, 70.6483),
            "sargodha": (32.0836, 72.6711),
            "mianwali": (32.5906, 71.5391),
            "abbottabad": (34.1688, 73.2215),
            "mansehra": (34.3372, 73.1957),
            "haripur": (34.0000, 72.9333),
            "taxila": (33.7461, 72.8511),
            "wah cantt": (33.7800, 72.7100),
            "attock": (33.7667, 72.3667),
            "chakwal": (32.9333, 72.8500),
            "jhelum": (32.9333, 73.7333),
            "gujrat": (32.5738, 74.0789),
            "mandi bahauddin": (32.5833, 73.4833),
            "hafizabad": (32.0667, 73.6833),
            "chiniot": (31.7167, 72.9833),
            "toba tek singh": (30.9667, 72.4833),
            "jhang": (31.2667, 72.3167),
            "bhawana": (31.5667, 72.6500),
            "kabirwala": (30.4000, 71.8667),
            "lodhran": (29.5333, 71.6167),
            "vehari": (30.0333, 72.3500),
            "pakpattan": (30.3500, 73.4000),
            "arifwala": (30.2833, 73.0667),
            "depalpur": (30.6500, 73.6500),
            "kasur": (31.1167, 74.4500),
            "burewala": (30.1500, 72.6500),
            "chichawatni": (30.5333, 72.7000),
            "kamalia": (30.7333, 72.6333),
            "khanewal": (30.3000, 71.9333),
            "mailsi": (29.8000, 72.1667),
            "hasilpur": (29.6833, 72.5337),
            "ahmadpur east": (29.1333, 71.2500),
            "bahawalnagar": (30.0000, 73.2500),
            "fort abbas": (29.4667, 72.8667),
            "haroonabad": (29.6167, 73.1333),
            "minchinabad": (30.1667, 73.5667),
            "renala khurd": (30.8667, 73.5833),
            "dunyapur": (29.8000, 71.7333),
            "kahror pakka": (29.6167, 71.9333),
            "lal suhanra": (29.3000, 72.1500),
            "ubauro": (28.1667, 69.7333),
            "pano aqil": (27.8500, 69.1167),
            "ghotki": (28.0000, 69.3167),
            "daharki": (28.0500, 69.7000),
            "mirpur mathelo": (28.0167, 69.5500),
            "kandhkot": (28.3000, 69.1833),
            "kashmore": (28.4333, 69.5833),
            "shikarpur": (27.9500, 68.6333),
            "jacobabad": (28.2833, 68.4333),
            "thul": (28.2333, 68.7833),
            "garhi yasin": (28.1000, 68.5167),
            "ratodero": (27.8000, 68.2833),
            "bhira": (27.5333, 68.1667),
            "moro": (26.6667, 67.9167),
            "naushahro feroze": (26.8333, 68.1167),
            "mehrabpur": (26.8167, 68.0000),
            "padidan": (26.8833, 68.3000),
            "bhitshah": (25.8667, 68.5000),
            "hala": (25.8167, 68.4167),
            "matiari": (25.6000, 68.4333),
            "tando allahyar": (25.4667, 68.7167),
            "tando muhammad khan": (25.1333, 68.5333),
            "thattha": (24.7500, 67.9167),
            "badin": (24.6667, 68.8333),
            "golarchi": (24.6833, 68.7333),
            "mirpur bathoro": (24.7333, 68.2333),
            "sujawal": (24.6000, 68.0833),
            "jhudo": (24.4833, 68.6667),
            "kunri": (24.4167, 69.0333),
            "pithoro": (25.4333, 69.3667),
            "digri": (25.3833, 69.1167),
            "mirwah": (25.2000, 69.0000),
            "chor": (24.4167, 69.2333),
            "islamkot": (24.7000, 70.1833),
            "diplo": (24.4667, 69.5833),
            "chachro": (24.2167, 70.2500),
            "mithi": (24.7333, 69.8000),
            "nagarparkar": (24.3500, 70.7667),
            "khairpur": (27.5333, 68.7667),
            "kingri": (27.2833, 68.5000),
            "gambat": (27.3500, 68.5167),
            "ranipur": (27.2833, 68.5000),
            "manchar": (27.1667, 68.4167),
            "lakhi": (27.0833, 68.1000),
            "pir jo goth": (27.0500, 68.3333),
            "kandiaro": (27.0667, 68.2167),
            "sohbat pur": (26.9167, 68.3167),
            "shahdadkot": (27.8500, 67.9000),
            "warah": (27.4500, 67.8000),
            "qambar": (27.5833, 67.9833),
            "mubarakpur": (27.5333, 67.9667),
            "nasirabad": (27.3833, 67.9167),
            "jhabel": (27.2833, 67.8833),
            "kamber": (27.5833, 67.9833),
            "shahpur chakar": (26.1500, 68.6500),
            "sakrand": (26.1333, 68.2667),
            "dadu": (26.7333, 67.7833),
            "mehar": (26.6000, 67.8333),
            "khairpur nathan shah": (26.5833, 67.7333),
            "johi": (26.6667, 67.6167),
            "sewan": (26.4000, 67.7000),
            "bhan": (26.4500, 67.7167),
            "lakki marwat": (32.6000, 70.9000),
            "tank": (32.2167, 70.3833),
            "dera ismail khan": (31.8333, 70.9000),
            "kulachi": (31.9333, 70.4500),
            "daraban": (31.6667, 70.3500),
            "parachinar": (33.9000, 70.1000),
            "hangu": (33.5333, 71.0667),
            "kohat": (33.5833, 71.4333),
            "karak": (33.1167, 71.1000),
            "bannu": (32.9833, 70.6000),
            "mardan": (34.2000, 72.0500),
            "swabi": (34.1167, 72.4667),
            "charsadda": (34.1500, 71.7333),
            "nowshera": (34.0167, 71.9833),
            "risalpur": (34.0667, 71.9667),
            "pabbi": (34.0167, 72.1000),
            "tordher": (34.0167, 72.2667),
            "topi": (34.0667, 72.6167),
            "jehangira": (34.0333, 72.2833),
            "khalabat": (34.0167, 72.3333),
            "nathia gali": (34.0667, 73.3833),
            "murree": (33.9000, 73.3833)
        }
        
        # ==========================================================
        # REDIS CACHE
        # ==========================================================
        
        self._redis_client = None
        if REDIS_AVAILABLE:
            try:
                self._redis_client = redis.Redis(
                    host='localhost',
                    port=6379,
                    decode_responses=True,
                    socket_connect_timeout=1,
                    socket_timeout=1
                )
                self._redis_client.ping()
                logger.info("⚡ Analytics Redis cache connected")
            except:
                self._redis_client = None
        
        # ==========================================================
        # DISKCACHE - Fallback Cache
        # ==========================================================
        
        self._disk_cache = None
        if DISKCACHE_AVAILABLE:
            try:
                self._disk_cache = dc.Cache('/tmp/analytics_cache')
                logger.info("⚡ Analytics DiskCache connected")
            except:
                self._disk_cache = None
        
        # ==========================================================
        # DUCKDB - In-Memory Analytics Engine
        # ==========================================================
        
        self._duckdb_conn = None
        if DUCKDB_AVAILABLE:
            try:
                self._duckdb_conn = duckdb.connect(':memory:')
                self._duckdb_conn.execute("INSTALL httpfs; LOAD httpfs;")
                logger.info("⚡ Analytics DuckDB connected")
            except:
                self._duckdb_conn = None
        
        self.close()
    
    def close(self):
        if self._owned_db and self.db:
            self.db.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def normalize_dn(self, dn_no: str) -> Optional[str]:
        if not dn_no:
            return None
        normalized = re.sub(r'\D', '', str(dn_no).strip())
        if len(normalized) < 8 or len(normalized) > 12:
            return None
        return normalized
    
    # ==========================================================
    # CACHE HELPERS
    # ==========================================================
    
    def _get_cached(self, key: str) -> Optional[Any]:
        # Check Redis first
        if self._redis_client:
            try:
                cached = self._redis_client.get(f"analytics:{key}")
                if cached:
                    import json
                    return json.loads(cached)
            except:
                pass
        
        # Check DiskCache
        if self._disk_cache:
            try:
                if key in self._disk_cache:
                    return self._disk_cache[key]
            except:
                pass
        
        return None
    
    def _set_cached(self, key: str, value: Any, ttl_seconds: int = CACHE_TTL_SECONDS):
        # Only cache if value is not an error
        if isinstance(value, dict) and value.get("error"):
            return
        
        # Cache in Redis
        if self._redis_client:
            try:
                import json
                self._redis_client.setex(f"analytics:{key}", ttl_seconds, json.dumps(value))
            except:
                pass
        
        # Cache in DiskCache
        if self._disk_cache:
            try:
                self._disk_cache[key] = value
                self._disk_cache.expire(key, ttl_seconds)
            except:
                pass
    
    # ==========================================================
    # SELF-HEALING DN QUERIES
    # ==========================================================
    
    def get_dn(self, dn_no: str, recovery_attempts: int = 0) -> Optional[DeliveryReport]:
        max_attempts = MAX_RECOVERY_ATTEMPTS
        current_attempt = 0
        
        while current_attempt < max_attempts:
            try:
                normalized = self.normalize_dn(dn_no)
                if not normalized:
                    return None
                return self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == normalized
                ).first()
            except Exception as e:
                current_attempt += 1
                logger.warning(f"DN query attempt {current_attempt} failed: {e}")
                if current_attempt >= max_attempts:
                    raise
                time.sleep(0.5)
        return None
    
    # ==========================================================
    # DN VERIFICATION & SAMPLE METHODS - NEW v14.1
    # ==========================================================
    
    def verify_dn_exists(self, dn_no: str) -> Dict[str, Any]:
        """
        Verify if a DN number exists in the database.
        
        Args:
            dn_no: DN number to verify
            
        Returns:
            Dict with found status and record details if found
        """
        try:
            normalized = self.normalize_dn(dn_no)
            if not normalized:
                return {"dn": dn_no, "normalized": None, "found": False, "error": "Invalid DN format"}
            
            record = self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
            
            found = record is not None
            result = {"dn": dn_no, "normalized": normalized, "found": found}
            
            if found and record:
                result["record"] = {
                    "dn_no": record.dn_no,
                    "customer_name": record.customer_name,
                    "dealer_code": record.dealer_code or "",
                    "customer_code": record.customer_code or "",
                    "warehouse": record.warehouse,
                    "ship_to_city": record.ship_to_city,
                    "dn_qty": int(record.dn_qty) if record.dn_qty else 0,
                    "dn_amount": float(record.dn_amount) if record.dn_amount else 0,
                    "pending_flag": record.pending_flag or False
                }
            
            logger.debug(f"DN verification: {dn_no} → found={found}")
            return result
        except Exception as e:
            logger.error(f"Verify DN failed for {dn_no}: {e}")
            return {"dn": dn_no, "found": False, "error": str(e)}
    
    def get_sample_dns(self, limit: int = 5) -> List[str]:
        """
        Get sample DN numbers from database.
        
        Args:
            limit: Maximum number of sample DNs to return
            
        Returns:
            List of sample DN numbers
        """
        try:
            results = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.dn_no.isnot(None),
                DeliveryReport.dn_no != ''
            ).distinct().limit(limit).all()
            
            sample_dns = [r[0] for r in results if r[0]]
            logger.debug(f"Retrieved {len(sample_dns)} sample DNs")
            return sample_dns
        except Exception as e:
            logger.error(f"Failed to get sample DNs: {e}")
            return []
    
    def verify_dn_exists(self, dn_no: str) -> Dict[str, Any]:
        try:
            normalized = self.normalize_dn(dn_no)
            if not normalized:
                return {"dn": dn_no, "normalized": None, "found": False, "error": "Invalid DN format"}
            record = self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
            found = record is not None
            result = {"dn": dn_no, "normalized": normalized, "found": found}
            if found and record:
                result["record"] = {
                    "dn_no": record.dn_no,
                    "customer_name": record.customer_name,
                    "dealer_code": record.dealer_code or "",
                    "customer_code": record.customer_code or "",
                    "warehouse": record.warehouse,
                    "ship_to_city": record.ship_to_city,
                    "dn_qty": int(record.dn_qty) if record.dn_qty else 0,
                    "dn_amount": float(record.dn_amount) if record.dn_amount else 0,
                    "pending_flag": record.pending_flag or False
                }
            return result
        except Exception as e:
            logger.error(f"Verify DN failed: {e}")
            return {"dn": dn_no, "found": False, "error": str(e)}
    
    def debug_dn(self, dn_no: str) -> Dict[str, Any]:
        try:
            normalized = self.normalize_dn(dn_no)
            if not normalized:
                return {"input": dn_no, "normalized": None, "rows_found": 0, "error": "Invalid DN format"}
            count = self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).count()
            record = self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
            result = {"input": dn_no, "normalized": normalized, "rows_found": count}
            if record:
                result["record"] = {
                    "dn_no": record.dn_no,
                    "customer_name": record.customer_name,
                    "dealer_code": record.dealer_code or "",
                    "customer_code": record.customer_code or "",
                    "warehouse": record.warehouse,
                    "ship_to_city": record.ship_to_city,
                    "dn_qty": int(record.dn_qty) if record.dn_qty else 0,
                    "dn_amount": float(record.dn_amount) if record.dn_amount else 0,
                    "delivery_status": record.delivery_status,
                    "pgi_status": record.pgi_status,
                    "pod_status": record.pod_status,
                    "pending_flag": record.pending_flag or False,
                    "dn_create_date": record.dn_create_date.isoformat() if record.dn_create_date else None,
                    "good_issue_date": record.good_issue_date.isoformat() if record.good_issue_date else None,
                    "pod_date": record.pod_date.isoformat() if record.pod_date else None
                }
            return result
        except Exception as e:
            logger.error(f"Debug DN failed: {e}")
            return {"input": dn_no, "error": str(e)}
    
    # ==========================================================
    # SELF-HEALING DEALER RESOLUTION (RapidFuzz + Fallback)
    # ==========================================================
    
    def resolve_dealer(self, dealer_input: str, recovery_attempts: int = 0) -> Optional[str]:
        """Resolve dealer with multiple recovery strategies."""
        if not dealer_input or not dealer_input.strip():
            return None
        
        dealer_input = dealer_input.strip()
        
        # ==========================================================
        # STRATEGY 1: RapidFuzz (Ultra-fast - 100x faster)
        # ==========================================================
        
        if RAPIDFUZZ_AVAILABLE:
            try:
                # Get all dealers from database
                dealers = self.db.query(
                    func.distinct(DeliveryReport.customer_name)
                ).filter(
                    DeliveryReport.customer_name.isnot(None),
                    DeliveryReport.customer_name != ''
                ).all()
                
                dealer_names = [d[0] for d in dealers if d[0]]
                
                if dealer_names:
                    matches = process.extract(
                        dealer_input,
                        dealer_names,
                        scorer=fuzz.ratio,
                        limit=3
                    )
                    
                    if matches and matches[0][1] >= 70:
                        resolved = matches[0][0]
                        logger.info(f"Dealer resolved via RapidFuzz: '{resolved}' (score: {matches[0][1]})")
                        return resolved
            except Exception as e:
                logger.debug(f"RapidFuzz strategy failed: {e}")
        
        # ==========================================================
        # STRATEGY 2: Exact match on customer_name
        # ==========================================================
        
        try:
            record = self.db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)
            ).first()
            if record:
                logger.info(f"Dealer resolved via exact match: '{record.customer_name}'")
                return record.customer_name
        except Exception as e:
            logger.debug(f"Exact match failed: {e}")
        
        # ==========================================================
        # STRATEGY 3: ILIKE match on customer_name
        # ==========================================================
        
        try:
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(dealer_input)
            ).first()
            if record:
                logger.info(f"Dealer resolved via ILIKE: '{record.customer_name}'")
                return record.customer_name
        except Exception as e:
            logger.debug(f"ILIKE match failed: {e}")
        
        # ==========================================================
        # STRATEGY 4: Wildcard match on customer_name
        # ==========================================================
        
        try:
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_input}%")
            ).first()
            if record:
                logger.info(f"Dealer resolved via wildcard: '{record.customer_name}'")
                return record.customer_name
        except Exception as e:
            logger.debug(f"Wildcard match failed: {e}")
        
        logger.warning(f"All dealer resolution strategies failed for: {dealer_input}")
        return None
    
    # ==========================================================
    # DEALER DASHBOARD WITH RECOVERY
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str, recovery_attempts: int = 0) -> Dict[str, Any]:
        try:
            # Try cache first
            cache_key = f"dealer_dashboard:{dealer_name.lower()}"
            cached = self._get_cached(cache_key)
            if cached:
                return cached
            
            resolved = self.resolve_dealer(dealer_name, recovery_attempts)
            if not resolved:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            logger.info(f"DEALER_SEARCH={dealer_name}")
            logger.info(f"DEALER_RESOLVED={resolved}")
            
            result = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                func.max(DeliveryReport.dealer_code).label("dealer_code"),
                func.max(DeliveryReport.customer_code).label("customer_code"),
                func.max(DeliveryReport.division).label("division"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.delivery_location).label("delivery_location"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.warehouse).label("top_warehouse"),
                func.max(DeliveryReport.ship_to_city).label("city"),
                func.min(DeliveryReport.dn_create_date).label("first_dn_date"),
                func.max(DeliveryReport.dn_create_date).label("last_dn_date"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
                func.count(func.distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.good_issue_date.isnot(None)), DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(func.distinct(case((or_(DeliveryReport.delivery_status != 'Completed', DeliveryReport.good_issue_date.is_(None)), DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                func.count(func.distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("transit_dns"),
                func.count(func.distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns"),
                func.count(func.distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_flag_dns"),
                func.coalesce(func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_create_date.isnot(None)), func.extract('epoch', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date) / 86400), else_=None)), 0).label("avg_pgi_aging"),
                func.coalesce(func.avg(case((and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.good_issue_date.isnot(None)), func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400), else_=None)), 0).label("avg_pod_aging"),
                func.coalesce(func.avg(case((and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_create_date.isnot(None)), func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.dn_create_date) / 86400), else_=None)), 0).label("avg_total_aging"),
                func.coalesce(func.sum(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_qty), else_=0)), 0).label("delivered_units"),
                func.coalesce(func.sum(case((or_(DeliveryReport.delivery_status != 'Completed', DeliveryReport.good_issue_date.is_(None)), DeliveryReport.dn_qty), else_=0)), 0).label("pending_units"),
                func.coalesce(func.sum(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_qty), else_=0)), 0).label("transit_units")
            ).filter(DeliveryReport.customer_name == resolved).group_by(DeliveryReport.customer_name).first()
            
            if not result or result.total_dns == 0:
                return {"dealer_name": resolved, "total_dns": 0, "error": "No records found"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            pod_completed_dns = result.pod_completed_dns or 0
            delivery_rate = round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
            pod_rate = round((pod_completed_dns / (delivered_dns or 1) * 100) if delivered_dns > 0 else 0, 1)
            pgi_rate = round(((delivered_dns + result.transit_dns) / total_dns * 100) if total_dns > 0 else 0, 1)
            
            if delivered_dns == 0 and total_dns == 0:
                dealer_status = "Inactive"
            elif total_dns < 10:
                dealer_status = "Low Activity"
            elif delivery_rate >= 90:
                dealer_status = "Active - High Performance"
            else:
                dealer_status = "Active - Needs Attention"
            
            # Get top products
            top_products = self.get_top_products_for_dealer(resolved, 5)
            
            # Get monthly trend
            monthly_trend = self.get_dealer_monthly_trend(resolved, 6)
            
            # Calculate distance and transit if warehouse available
            warehouse = result.top_warehouse or ""
            distance_info = {}
            if warehouse:
                distance, transit_days, status = self._calculate_distance_and_transit(
                    warehouse, resolved
                )
                if status != "unknown":
                    distance_info = {
                        "distance_km": round(distance, 1),
                        "transit_days": transit_days,
                        "status": status,
                        "summary": self._get_distance_summary(distance, transit_days, status, resolved)
                    }
            
            # Calculate risk level
            avg_aging = float(result.avg_total_aging or 0)
            risk_level, risk_percentage = self._calculate_risk_level(
                int(avg_aging),
                1 if avg_aging <= 1 else 2 if avg_aging <= 2 else 3
            )
            
            dashboard = {
                "dealer_name": resolved,
                "dealer_code": result.dealer_code or "",
                "customer_code": result.customer_code or "",
                "division": result.division or "",
                "sales_office": result.sales_office or "",
                "sales_manager": result.sales_manager or "",
                "city": result.city or "",
                "warehouse": warehouse,
                "warehouse_code": result.warehouse_code or "",
                "delivery_location": result.delivery_location or "",
                "dealer_status": dealer_status,
                "first_dn_date": result.first_dn_date,
                "last_dn_date": result.last_dn_date,
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "transit_dns": result.transit_dns or 0,
                "pod_completed_dns": pod_completed_dns,
                "pending_pod_dns": result.pending_pod_dns or 0,
                "pending_flag_dns": result.pending_flag_dns or 0,
                "delivered_units": int(result.delivered_units or 0),
                "pending_units": int(result.pending_units or 0),
                "transit_units": int(result.transit_units or 0),
                "delivery_rate": delivery_rate,
                "pgi_rate": pgi_rate,
                "pod_rate": pod_rate,
                "avg_pgi_aging": round(result.avg_pgi_aging or 0, 1),
                "avg_pod_aging": round(result.avg_pod_aging or 0, 1),
                "avg_total_aging": round(result.avg_total_aging or 0, 1),
                "risk_level": risk_level,
                "risk_percentage": round(risk_percentage * 100, 1),
                "distance_info": distance_info,
                "products": top_products,
                "monthly_trend": monthly_trend
            }
            
            # Cache the dashboard
            self._set_cached(cache_key, dashboard)
            
            return dashboard
            
        except Exception as e:
            logger.error(f"Get dealer dashboard failed: {e}")
            return {"error": str(e)}
    
    def get_top_products_for_dealer(self, dealer_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            resolved = self.resolve_dealer(dealer_name)
            if not resolved:
                return []
            results = self.db.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no, 'UNKNOWN').label("product_name"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("dn_count")
            ).filter(
                DeliveryReport.customer_name == resolved,
                DeliveryReport.customer_model.isnot(None)
            ).group_by(
                DeliveryReport.customer_model,
                DeliveryReport.material_no
            ).order_by(desc("total_revenue")).limit(limit).all()
            
            products = []
            for r in results:
                products.append({
                    "name": r.product_name or "Unknown",
                    "revenue": float(r.total_revenue or 0),
                    "units": int(r.total_units or 0),
                    "dn_count": r.dn_count or 0
                })
            return products
        except Exception as e:
            logger.error(f"Get top products for dealer failed: {e}")
            return []
    
    def get_dealer_monthly_trend(self, dealer_name: str, months: int = 6) -> List[Dict[str, Any]]:
        try:
            resolved = self.resolve_dealer(dealer_name)
            if not resolved:
                return []
            results = self.db.query(
                func.date_trunc('month', DeliveryReport.dn_create_date).label("month"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("dn_count"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue")
            ).filter(
                DeliveryReport.customer_name == resolved,
                DeliveryReport.dn_create_date.isnot(None)
            ).group_by(
                func.date_trunc('month', DeliveryReport.dn_create_date)
            ).order_by(desc("month")).limit(months).all()
            
            trends = []
            for r in results:
                trends.append({
                    "period": r.month.strftime("%b-%Y") if r.month else "N/A",
                    "dns": r.dn_count or 0,
                    "units": int(r.total_units or 0),
                    "revenue": float(r.total_revenue or 0)
                })
            return trends
        except Exception as e:
            logger.error(f"Get dealer monthly trend failed: {e}")
            return []
    
    def get_dealer_profile(self, dealer_name: str) -> Dict[str, Any]:
        try:
            resolved = self.resolve_dealer(dealer_name)
            if not resolved:
                return {}
            result = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                func.max(DeliveryReport.dealer_code).label("dealer_code"),
                func.max(DeliveryReport.customer_code).label("customer_code"),
                func.max(DeliveryReport.division).label("division"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.delivery_location).label("delivery_location"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.ship_to_city).label("city"),
                func.min(DeliveryReport.dn_create_date).label("first_dn_date"),
                func.max(DeliveryReport.dn_create_date).label("last_dn_date"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns")
            ).filter(DeliveryReport.customer_name == resolved).group_by(DeliveryReport.customer_name).first()
            if not result:
                return {}
            return {
                "dealer_name": result.dealer_name or "",
                "dealer_code": result.dealer_code or "",
                "customer_code": result.customer_code or "",
                "division": result.division or "",
                "warehouse": result.warehouse or "",
                "warehouse_code": result.warehouse_code or "",
                "delivery_location": result.delivery_location or "",
                "sales_office": result.sales_office or "",
                "sales_manager": result.sales_manager or "",
                "city": result.city or "",
                "first_dn_date": result.first_dn_date,
                "last_dn_date": result.last_dn_date,
                "total_dns": result.total_dns or 0
            }
        except Exception as e:
            logger.error(f"Get dealer profile failed: {e}")
            return {}
    
    def get_dealer_timeline(self, dealer_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            resolved = self.resolve_dealer(dealer_name)
            if not resolved:
                return []
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == resolved
            ).order_by(desc(DeliveryReport.dn_create_date)).limit(limit).all()
            timeline = []
            for record in records:
                timeline.append({
                    "dn_no": record.dn_no,
                    "dn_qty": int(record.dn_qty) if record.dn_qty else 0,
                    "dn_amount": float(record.dn_amount) if record.dn_amount else 0,
                    "dn_create_date": record.dn_create_date.isoformat() if record.dn_create_date else None,
                    "good_issue_date": record.good_issue_date.isoformat() if record.good_issue_date else None,
                    "pod_date": record.pod_date.isoformat() if record.pod_date else None,
                    "warehouse": record.warehouse,
                    "ship_to_city": record.ship_to_city,
                    "delivery_status": record.delivery_status,
                    "pgi_status": record.pgi_status,
                    "pod_status": record.pod_status,
                    "pending_flag": record.pending_flag or False
                })
            return timeline
        except Exception as e:
            logger.error(f"Get dealer timeline failed: {e}")
            return []
    
    def get_product_dashboard(self, dealer_name: str) -> List[Dict[str, Any]]:
        try:
            resolved = self.resolve_dealer(dealer_name)
            if not resolved:
                return []
            results = self.db.query(
                func.coalesce(DeliveryReport.material_no, 'UNKNOWN').label("product_code"),
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no, 'UNKNOWN').label("product_name"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("dn_count"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.avg(DeliveryReport.dn_amount).label("avg_revenue_per_dn"),
                func.max(DeliveryReport.dn_amount).label("max_revenue"),
                func.min(DeliveryReport.dn_amount).label("min_revenue"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_count"),
                func.count(func.distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_count")
            ).filter(DeliveryReport.customer_name == resolved).group_by(
                DeliveryReport.material_no, DeliveryReport.customer_model
            ).order_by(desc("total_revenue")).limit(50).all()
            products = []
            for r in results:
                dn_count = r.dn_count or 1
                delivered_count = r.delivered_count or 0
                products.append({
                    "product_code": r.product_code,
                    "product_name": r.product_name,
                    "dn_count": dn_count,
                    "total_units": int(r.total_units or 0),
                    "total_revenue": float(r.total_revenue or 0),
                    "avg_revenue_per_dn": float(r.avg_revenue_per_dn or 0),
                    "max_revenue": float(r.max_revenue or 0),
                    "min_revenue": float(r.min_revenue or 0),
                    "delivery_rate": round((delivered_count / dn_count * 100) if dn_count > 0 else 0, 1)
                })
            return products
        except Exception as e:
            logger.error(f"Get product dashboard failed: {e}")
            return []
    
    # ==========================================================
    # CITY DASHBOARD
    # ==========================================================
    
    def get_city_dashboard(self, city_name: str) -> Dict[str, Any]:
        try:
            if not city_name or not city_name.strip():
                return {"error": "City name cannot be empty"}
            
            cache_key = f"city_dashboard:{city_name.lower()}"
            cached = self._get_cached(cache_key)
            if cached:
                return cached
            
            result = self.db.query(
                DeliveryReport.ship_to_city.label("city"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(func.distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(func.distinct(DeliveryReport.warehouse)).label("total_warehouses"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_flag_dns"),
                func.count(func.distinct(case((DeliveryReport.pod_status != 'Completed', DeliveryReport.dn_no), else_=None))).label("pending_pod_dns")
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_name}%")
            ).group_by(DeliveryReport.ship_to_city).first()
            
            if not result or result.total_dns == 0:
                return {"error": f"City '{city_name}' not found"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            pgi_rate = round(((delivered_dns + (result.total_dns - delivered_dns - (result.pending_flag_dns or 0))) / total_dns * 100) if total_dns > 0 else 0, 1)
            pod_rate = round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
            
            # Get top dealers
            top_dealers_result = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue")
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_name}%")
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(desc("total_revenue")).limit(5).all()
            
            top_dealers = []
            for r in top_dealers_result:
                top_dealers.append({
                    "name": r.dealer_name or "Unknown",
                    "revenue": float(r.total_revenue or 0)
                })
            
            # Get top products
            top_products_result = self.db.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no, 'UNKNOWN').label("product"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue")
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_name}%"),
                DeliveryReport.customer_model.isnot(None)
            ).group_by(
                DeliveryReport.customer_model,
                DeliveryReport.material_no
            ).order_by(desc("total_revenue")).limit(5).all()
            
            top_products = []
            for r in top_products_result:
                top_products.append({
                    "name": r.product or "Unknown",
                    "revenue": float(r.total_revenue or 0)
                })
            
            # Monthly trend
            monthly_trend = {}
            monthly_result = self.db.query(
                func.date_trunc('month', DeliveryReport.dn_create_date).label("month"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("dns"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_name}%"),
                DeliveryReport.dn_create_date.isnot(None)
            ).group_by(
                func.date_trunc('month', DeliveryReport.dn_create_date)
            ).order_by(desc("month")).limit(6).all()
            
            if monthly_result:
                latest = monthly_result[0]
                monthly_trend = {
                    "period": latest.month.strftime("%b-%Y") if latest.month else "N/A",
                    "dns": latest.dns or 0,
                    "units": int(latest.units or 0),
                    "revenue": float(latest.revenue or 0)
                }
            
            dashboard = {
                "city_name": result.city,
                "summary": {
                    "total_dns": total_dns,
                    "total_units": int(result.total_units or 0),
                    "total_revenue": float(result.total_revenue or 0),
                    "total_dealers": result.total_dealers or 0,
                    "total_warehouses": result.total_warehouses or 0,
                    "delivered_dns": delivered_dns,
                    "pending_flag_dns": result.pending_flag_dns or 0,
                    "pending_dns": result.pending_flag_dns or 0,
                    "pending_pod_dns": result.pending_pod_dns or 0,
                    "delivery_rate": round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1),
                    "pgi_rate": pgi_rate,
                    "pod_rate": pod_rate,
                    "late_deliveries": result.pending_flag_dns or 0,
                    "pending_pod_dealers": self.db.query(func.count(func.distinct(DeliveryReport.customer_name))).filter(
                        DeliveryReport.ship_to_city.ilike(f"%{city_name}%"),
                        DeliveryReport.pod_status != 'Completed'
                    ).scalar() or 0,
                    "pending_pgi_dealers": self.db.query(func.count(func.distinct(DeliveryReport.customer_name))).filter(
                        DeliveryReport.ship_to_city.ilike(f"%{city_name}%"),
                        DeliveryReport.good_issue_date.is_(None)
                    ).scalar() or 0
                },
                "top_dealers": top_dealers,
                "top_products": top_products,
                "monthly_trend": monthly_trend
            }
            
            self._set_cached(cache_key, dashboard)
            return dashboard
            
        except Exception as e:
            logger.error(f"Get city dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # WAREHOUSE DASHBOARD
    # ==========================================================
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        try:
            if not warehouse_name or not warehouse_name.strip():
                return {"error": "Warehouse name cannot be empty"}
            
            cache_key = f"warehouse_dashboard:{warehouse_name.lower()}"
            cached = self._get_cached(cache_key)
            if cached:
                return cached
            
            warehouse_pattern = f"%{warehouse_name.strip()}%"
            
            result = self.db.query(
                DeliveryReport.warehouse.label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(func.distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(func.distinct(DeliveryReport.ship_to_city)).label("cities_served"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_flag_dns"),
                func.count(func.distinct(case((DeliveryReport.pod_status != 'Completed', DeliveryReport.dn_no), else_=None))).label("pending_pod_dns"),
                func.count(func.distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi_dns")
            ).filter(
                DeliveryReport.warehouse.ilike(warehouse_pattern)
            ).group_by(DeliveryReport.warehouse).first()
            
            if not result or result.total_dns == 0:
                return {"error": f"Warehouse '{warehouse_name}' not found"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            delivery_rate = round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
            pgi_rate = round(((delivered_dns + (result.total_dns - delivered_dns - (result.pending_pgi_dns or 0))) / total_dns * 100) if total_dns > 0 else 0, 1)
            pod_rate = round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
            
            # Top cities served
            top_cities_result = self.db.query(
                DeliveryReport.ship_to_city.label("city"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("dns")
            ).filter(
                DeliveryReport.warehouse.ilike(warehouse_pattern),
                DeliveryReport.ship_to_city.isnot(None),
                DeliveryReport.ship_to_city != ''
            ).group_by(
                DeliveryReport.ship_to_city
            ).order_by(desc("dns")).limit(5).all()
            
            top_cities = []
            for r in top_cities_result:
                if r.city:
                    top_cities.append({
                        "name": r.city,
                        "dns": r.dns or 0
                    })
            
            # Top dealers served
            top_dealers_result = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue")
            ).filter(
                DeliveryReport.warehouse.ilike(warehouse_pattern),
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(desc("total_revenue")).limit(5).all()
            
            top_dealers = []
            for r in top_dealers_result:
                if r.dealer_name:
                    top_dealers.append({
                        "name": r.dealer_name,
                        "revenue": float(r.total_revenue or 0)
                    })
            
            # Monthly trend
            monthly_trend = {}
            monthly_result = self.db.query(
                func.date_trunc('month', DeliveryReport.dn_create_date).label("month"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("dns"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                DeliveryReport.warehouse.ilike(warehouse_pattern),
                DeliveryReport.dn_create_date.isnot(None)
            ).group_by(
                func.date_trunc('month', DeliveryReport.dn_create_date)
            ).order_by(desc("month")).limit(6).all()
            
            if monthly_result:
                latest = monthly_result[0]
                monthly_trend = {
                    "period": latest.month.strftime("%b-%Y") if latest.month else "N/A",
                    "dns": latest.dns or 0,
                    "units": int(latest.units or 0),
                    "revenue": float(latest.revenue or 0)
                }
            
            dashboard = {
                "warehouse_name": result.warehouse,
                "warehouse_code": result.warehouse_code or "",
                "summary": {
                    "total_dns": total_dns,
                    "total_units": int(result.total_units or 0),
                    "total_revenue": float(result.total_revenue or 0),
                    "total_dealers": result.total_dealers or 0,
                    "cities_served": result.cities_served or 0,
                    "delivered_dns": delivered_dns,
                    "pending_flag_dns": result.pending_flag_dns or 0,
                    "pending_dns": result.pending_flag_dns or 0,
                    "pending_pgi_dns": result.pending_pgi_dns or 0,
                    "pending_pod_dns": result.pending_pod_dns or 0,
                    "delivery_rate": delivery_rate,
                    "pgi_rate": pgi_rate,
                    "pod_rate": pod_rate,
                    "delayed_deliveries": result.pending_flag_dns or 0
                },
                "top_cities": top_cities,
                "top_dealers": top_dealers,
                "monthly_trend": monthly_trend
            }
            
            self._set_cached(cache_key, dashboard)
            return dashboard
            
        except Exception as e:
            logger.error(f"Get warehouse dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # ADDITIONAL METHODS FOR DASHBOARD BUILDER
    # ==========================================================
    
    def get_national_kpis(self) -> Dict[str, Any]:
        """Get national KPIs for executive dashboard."""
        try:
            result = self.db.query(
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(func.distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(func.distinct(DeliveryReport.ship_to_city)).label("total_cities"),
                func.count(func.distinct(DeliveryReport.warehouse)).label("total_warehouses"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_flag_dns"),
                func.count(func.distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns"),
                func.count(func.distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi_dns"),
                func.coalesce(func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_create_date.isnot(None)), func.extract('epoch', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date) / 86400), else_=None)), 0).label("avg_pgi_aging"),
                func.coalesce(func.avg(case((and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.good_issue_date.isnot(None)), func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400), else_=None)), 0).label("avg_pod_aging"),
                func.coalesce(func.avg(case((and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_create_date.isnot(None)), func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.dn_create_date) / 86400), else_=None)), 0).label("avg_total_aging")
            ).first()
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            pod_completed = result.delivered_dns or 0
            
            return {
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "total_dealers": result.total_dealers or 0,
                "total_cities": result.total_cities or 0,
                "total_warehouses": result.total_warehouses or 0,
                "delivered_dns": delivered_dns,
                "pending_flag_dns": result.pending_flag_dns or 0,
                "pending_pod_dns": result.pending_pod_dns or 0,
                "pending_pgi_dns": result.pending_pgi_dns or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered_dns, total_dns),
                "pod_rate": KPIEngine.calculate_pod_rate(pod_completed, delivered_dns) if delivered_dns > 0 else 0,
                "avg_pgi_aging": round(result.avg_pgi_aging or 0, 1),
                "avg_pod_aging": round(result.avg_pod_aging or 0, 1),
                "avg_total_aging": round(result.avg_total_aging or 0, 1)
            }
        except Exception as e:
            logger.error(f"Get national KPIs failed: {e}")
            return {}
    
    def get_national_monthly_trend(self, months: int = 12) -> List[Dict[str, Any]]:
        """Get national monthly trend."""
        try:
            results = self.db.query(
                func.date_trunc('month', DeliveryReport.dn_create_date).label("month"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("dns"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                DeliveryReport.dn_create_date.isnot(None)
            ).group_by(
                func.date_trunc('month', DeliveryReport.dn_create_date)
            ).order_by(desc("month")).limit(months).all()
            
            trends = []
            for r in results:
                trends.append({
                    "period": r.month.strftime("%b-%Y") if r.month else "N/A",
                    "dns": r.dns or 0,
                    "units": int(r.units or 0),
                    "revenue": float(r.revenue or 0)
                })
            return trends
        except Exception as e:
            logger.error(f"Get national monthly trend failed: {e}")
            return []
    
    def get_city_top_dealers(self, city_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top dealers for a city."""
        try:
            results = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns")
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_name}%"),
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(desc(func.sum(DeliveryReport.dn_amount))).limit(limit).all()
            
            dealers = []
            for r in results:
                dealers.append({
                    "name": r.dealer_name or "Unknown",
                    "revenue": float(r.total_revenue or 0),
                    "dns": r.total_dns or 0
                })
            return dealers
        except Exception as e:
            logger.error(f"Get city top dealers failed: {e}")
            return []
    
    def get_city_top_products(self, city_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top products for a city."""
        try:
            results = self.db.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no, 'UNKNOWN').label("product"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue")
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_name}%"),
                DeliveryReport.customer_model.isnot(None)
            ).group_by(
                DeliveryReport.customer_model,
                DeliveryReport.material_no
            ).order_by(desc("total_revenue")).limit(limit).all()
            
            products = []
            for r in results:
                products.append({
                    "name": r.product or "Unknown",
                    "revenue": float(r.total_revenue or 0)
                })
            return products
        except Exception as e:
            logger.error(f"Get city top products failed: {e}")
            return []
    
    def get_high_risk_dealers(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get high risk dealers."""
        try:
            results = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_count"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns")
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).group_by(
                DeliveryReport.customer_name
            ).having(
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))) > 0
            ).order_by(desc("pending_count")).limit(limit).all()
            
            dealers = []
            for r in results:
                dealers.append({
                    "dealer_name": r.dealer_name,
                    "pending_count": r.pending_count or 0,
                    "total_dns": r.total_dns or 0,
                    "risk_score": round((r.pending_count or 0) / (r.total_dns or 1) * 100, 1)
                })
            return dealers
        except Exception as e:
            logger.error(f"Get high risk dealers failed: {e}")
            return []
    
    def get_pgi_aging_alerts(self) -> List[Dict[str, Any]]:
        """Get PGI aging alerts."""
        try:
            results = self.db.query(
                DeliveryReport.dn_no.label("dn"),
                DeliveryReport.customer_name.label("dealer"),
                DeliveryReport.dn_create_date.label("created_date"),
                func.date_part('day', func.now() - DeliveryReport.dn_create_date).label("days_old")
            ).filter(
                DeliveryReport.good_issue_date.is_(None),
                DeliveryReport.dn_create_date.isnot(None),
                func.date_part('day', func.now() - DeliveryReport.dn_create_date) > 5
            ).order_by(desc("days_old")).limit(10).all()
            
            alerts = []
            for r in results:
                alerts.append({
                    "type": "PGI Aging",
                    "severity": "high" if r.days_old > 10 else "medium",
                    "description": f"DN {r.dn} for {r.dealer} pending PGI for {int(r.days_old)} days",
                    "dn": r.dn,
                    "dealer": r.dealer,
                    "days_old": int(r.days_old)
                })
            return alerts
        except Exception as e:
            logger.error(f"Get PGI aging alerts failed: {e}")
            return []
    
    def get_pod_aging_alerts(self) -> List[Dict[str, Any]]:
        """Get POD aging alerts."""
        try:
            results = self.db.query(
                DeliveryReport.dn_no.label("dn"),
                DeliveryReport.customer_name.label("dealer"),
                DeliveryReport.good_issue_date.label("pgi_date"),
                func.date_part('day', func.now() - DeliveryReport.good_issue_date).label("days_old")
            ).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.pod_date.is_(None),
                func.date_part('day', func.now() - DeliveryReport.good_issue_date) > 3
            ).order_by(desc("days_old")).limit(10).all()
            
            alerts = []
            for r in results:
                alerts.append({
                    "type": "POD Aging",
                    "severity": "high" if r.days_old > 7 else "medium",
                    "description": f"DN {r.dn} for {r.dealer} pending POD for {int(r.days_old)} days",
                    "dn": r.dn,
                    "dealer": r.dealer,
                    "days_old": int(r.days_old)
                })
            return alerts
        except Exception as e:
            logger.error(f"Get POD aging alerts failed: {e}")
            return []
    
    def get_delayed_deliveries(self) -> List[Dict[str, Any]]:
        """Get delayed deliveries."""
        try:
            results = self.db.query(
                DeliveryReport.dn_no.label("dn"),
                DeliveryReport.customer_name.label("dealer"),
                DeliveryReport.good_issue_date.label("pgi_date"),
                func.date_part('day', func.now() - DeliveryReport.good_issue_date).label("days_old")
            ).filter(
                DeliveryReport.pending_flag == True,
                DeliveryReport.good_issue_date.isnot(None)
            ).order_by(desc("days_old")).limit(10).all()
            
            alerts = []
            for r in results:
                alerts.append({
                    "type": "Delayed Delivery",
                    "severity": "critical" if r.days_old > 14 else "high",
                    "description": f"DN {r.dn} for {r.dealer} delayed for {int(r.days_old)} days",
                    "dn": r.dn,
                    "dealer": r.dealer,
                    "days_old": int(r.days_old)
                })
            return alerts
        except Exception as e:
            logger.error(f"Get delayed deliveries failed: {e}")
            return []
    
    def get_warehouse_risks(self) -> List[Dict[str, Any]]:
        """Get warehouse risks."""
        try:
            results = self.db.query(
                DeliveryReport.warehouse.label("warehouse"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_count"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns")
            ).filter(
                DeliveryReport.warehouse.isnot(None),
                DeliveryReport.warehouse != ''
            ).group_by(
                DeliveryReport.warehouse
            ).having(
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))) > 10
            ).order_by(desc("pending_count")).limit(5).all()
            
            alerts = []
            for r in results:
                alerts.append({
                    "type": "Warehouse Risk",
                    "severity": "high" if r.pending_count > 50 else "medium",
                    "description": f"Warehouse {r.warehouse} has {r.pending_count} pending deliveries",
                    "warehouse": r.warehouse,
                    "pending_count": r.pending_count or 0,
                    "total_dns": r.total_dns or 0
                })
            return alerts
        except Exception as e:
            logger.error(f"Get warehouse risks failed: {e}")
            return []
    
    def get_dealer_risks(self) -> List[Dict[str, Any]]:
        """Get dealer risks."""
        try:
            results = self.db.query(
                DeliveryReport.customer_name.label("dealer"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_count"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns")
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).group_by(
                DeliveryReport.customer_name
            ).having(
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))) > 5
            ).order_by(desc("pending_count")).limit(5).all()
            
            alerts = []
            for r in results:
                alerts.append({
                    "type": "Dealer Risk",
                    "severity": "high" if r.pending_count > 20 else "medium",
                    "description": f"Dealer {r.dealer} has {r.pending_count} pending deliveries",
                    "dealer": r.dealer,
                    "pending_count": r.pending_count or 0,
                    "total_dns": r.total_dns or 0
                })
            return alerts
        except Exception as e:
            logger.error(f"Get dealer risks failed: {e}")
            return []


# ==========================================================
# MAIN ANALYTICS SERVICE
# ==========================================================

class AnalyticsService:
    DEALER_NAME_FIELD = DEALER_NAME_FIELD
    
    def __init__(self, use_redis: bool = False):
        self._start_time = time.time()
        self.is_railway = RailwayPostgresConfig.is_railway()
        if self.is_railway:
            logger.info("🚆 Running on Railway - 100% PostgreSQL mode enabled")
        self.kpi = _get_kpi_service()
        self.schema = _get_schema_service()
        self.repo = AnalyticsRepository()
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, datetime] = {}
        self._dealer_cache: Dict[str, Tuple[str, datetime]] = {}
        
        # ==========================================================
        # ENGINES
        # ==========================================================
        
        self.kpi_engine = KPIEngine()
        self.ranking_engine = RankingEngine(self.repo)
        self.risk_engine = RiskEngine(self.repo)
        self.forecast_engine = ForecastingEngine(self.repo)
        self.distance_engine = DistanceEngine(self.repo)
        self.dashboard_builder = DashboardBuilder(self.repo)
        
        # ==========================================================
        # METRICS
        # ==========================================================
        
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_duration_ms": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "dealer_resolution_success": 0,
            "dealer_resolution_failure": 0,
            "postgresql_queries": 0,
            "slow_queries": 0,
            "errors_by_type": defaultdict(int),
            "dn_lookups": 0,
            "dn_lookups_success": 0,
            "dn_lookups_failure": 0,
            "groq_fallbacks": 0,
            "distance_calculations": 0,
            "risk_assessments": 0,
            "redis_hits": 0,
            "diskcache_hits": 0,
            "forecast_requests": 0
        }
        
        self._test_postgresql()
        logger.info("=" * 70)
        logger.info("AnalyticsService v14.1 - Fully Integrated")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ROLE: Analytics Brain - NEVER calls Groq")
        logger.info("")
        logger.info("   ✅ ENGINES:")
        logger.info("      - KPI Engine")
        logger.info("      - Ranking Engine")
        logger.info("      - Risk Engine")
        logger.info("      - Control Tower Engine")
        logger.info("      - Forecasting Engine")
        logger.info("      - Distance Engine")
        logger.info("      - Dashboard Builder")
        logger.info("")
        logger.info("   📊 18 DASHBOARDS SUPPORTED:")
        logger.info("      1. Dealer Dashboard (360 Degree)")
        logger.info("      2. Warehouse Dashboard")
        logger.info("      3. City Dashboard")
        logger.info("      4. Product Dashboard")
        logger.info("      5. Executive Dashboard")
        logger.info("      6. Control Tower")
        logger.info("      7. Distance Dashboard")
        logger.info("      8. Transporter Dashboard")
        logger.info("      9. Inventory Dashboard")
        logger.info("      10. Forecast Dashboard")
        logger.info("")
        logger.info("   ✅ v14.1 NEW METHODS:")
        logger.info("      - verify_dn_exists()")
        logger.info("      - get_sample_dns()")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
    def _test_postgresql(self):
        try:
            result = self.repo.db.execute(text("SELECT version()"))
            version = result.first()[0]
            logger.info(f"✅ PostgreSQL connected: {version[:50]}...")
        except Exception as e:
            logger.error(f"❌ PostgreSQL connection test failed: {e}")
    
    def close(self):
        self.repo.close()
        if hasattr(self.kpi, 'close'):
            self.kpi.close()
    
    # ==========================================================
    # DN VERIFICATION METHODS - NEW v14.1
    # ==========================================================
    
    def verify_dn_exists(self, dn_no: str) -> Dict[str, Any]:
        """
        Verify if a DN exists - proxy to repository.
        
        Args:
            dn_no: DN number to verify
            
        Returns:
            Dict with found status and record details
        """
        return self.repo.verify_dn_exists(dn_no)
    
    def get_sample_dns(self, limit: int = 5) -> List[str]:
        """
        Get sample DNs - proxy to repository.
        
        Args:
            limit: Maximum number of sample DNs to return
            
        Returns:
            List of sample DN numbers
        """
        return self.repo.get_sample_dns(limit)
    
    # ==========================================================
    # PUBLIC DASHBOARD METHODS
    # ==========================================================
    
    def get_dealer_dashboard_360(self, dealer_name: str) -> AnalyticsResponse:
        """Get 360-degree dealer dashboard."""
        try:
            start_time = time.time()
            self.metrics["total_requests"] += 1
            
            cache_key = f"dealer_360:{dealer_name.lower()}"
            cached = self._get_cached(cache_key)
            if cached:
                self.metrics["cache_hits"] += 1
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_dealer_dashboard(dealer_name)
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 600)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get dealer dashboard 360 failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_warehouse_dashboard_full(self, warehouse_name: str) -> AnalyticsResponse:
        """Get comprehensive warehouse dashboard."""
        try:
            cache_key = f"warehouse_full:{warehouse_name.lower()}"
            cached = self._get_cached(cache_key)
            if cached:
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_warehouse_dashboard(warehouse_name)
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 600)
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Get warehouse dashboard full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_city_dashboard_full(self, city_name: str) -> AnalyticsResponse:
        """Get comprehensive city dashboard."""
        try:
            cache_key = f"city_full:{city_name.lower()}"
            cached = self._get_cached(cache_key)
            if cached:
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_city_dashboard(city_name)
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 600)
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Get city dashboard full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_product_dashboard_full(self, product_name: Optional[str] = None) -> AnalyticsResponse:
        """Get comprehensive product dashboard."""
        try:
            cache_key = f"product_full:{product_name.lower() if product_name else 'all'}"
            cached = self._get_cached(cache_key)
            if cached:
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_product_dashboard(product_name)
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 600)
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Get product dashboard full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_executive_dashboard_full(self) -> AnalyticsResponse:
        """Get comprehensive executive dashboard."""
        try:
            cache_key = "executive_full"
            cached = self._get_cached(cache_key)
            if cached:
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_executive_dashboard()
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 600)
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Get executive dashboard full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_control_tower_full(self) -> AnalyticsResponse:
        """Get logistics control tower dashboard."""
        try:
            cache_key = "control_tower_full"
            cached = self._get_cached(cache_key)
            if cached:
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_control_tower()
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 300)
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Get control tower full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_distance_dashboard_full(self, dealer_name: str, warehouse_name: Optional[str] = None) -> AnalyticsResponse:
        """Get distance dashboard."""
        try:
            cache_key = f"distance:{dealer_name.lower()}:{warehouse_name.lower() if warehouse_name else 'all'}"
            cached = self._get_cached(cache_key)
            if cached:
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_distance_dashboard(dealer_name, warehouse_name)
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 3600)
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Get distance dashboard full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_transporter_dashboard_full(self, transporter_name: Optional[str] = None) -> AnalyticsResponse:
        """Get transporter dashboard."""
        try:
            cache_key = f"transporter:{transporter_name.lower() if transporter_name else 'all'}"
            cached = self._get_cached(cache_key)
            if cached:
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_transporter_dashboard(transporter_name)
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 600)
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Get transporter dashboard full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_inventory_dashboard_full(self, warehouse_name: Optional[str] = None) -> AnalyticsResponse:
        """Get inventory dashboard."""
        try:
            cache_key = f"inventory:{warehouse_name.lower() if warehouse_name else 'all'}"
            cached = self._get_cached(cache_key)
            if cached:
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_inventory_dashboard(warehouse_name)
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 600)
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Get inventory dashboard full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_forecast_dashboard_full(self, dealer_name: Optional[str] = None) -> AnalyticsResponse:
        """Get forecast dashboard."""
        try:
            self.metrics["forecast_requests"] += 1
            
            cache_key = f"forecast:{dealer_name.lower() if dealer_name else 'national'}"
            cached = self._get_cached(cache_key)
            if cached:
                return AnalyticsResponse(success=True, data=cached)
            
            dashboard = self.dashboard_builder.build_forecast_dashboard(dealer_name)
            
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            
            self._set_cached(cache_key, dashboard, 3600)
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Get forecast dashboard full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # RANKING METHODS
    # ==========================================================
    
    def get_dealer_ranking_full(self, metric: str = "revenue", limit: int = 10) -> AnalyticsResponse:
        """Get dealer ranking."""
        try:
            ranking = self.ranking_engine.get_dealer_ranking(metric, limit, top=True)
            return AnalyticsResponse(success=True, data={"ranking": ranking, "metric": metric, "total": len(ranking)})
        except Exception as e:
            logger.error(f"Get dealer ranking full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_warehouse_ranking_full(self, metric: str = "revenue", limit: int = 10) -> AnalyticsResponse:
        """Get warehouse ranking."""
        try:
            ranking = self.ranking_engine.get_warehouse_ranking(metric, limit, top=True)
            return AnalyticsResponse(success=True, data={"ranking": ranking, "metric": metric, "total": len(ranking)})
        except Exception as e:
            logger.error(f"Get warehouse ranking full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_city_ranking_full(self, metric: str = "revenue", limit: int = 10) -> AnalyticsResponse:
        """Get city ranking."""
        try:
            ranking = self.ranking_engine.get_city_ranking(metric, limit, top=True)
            return AnalyticsResponse(success=True, data={"ranking": ranking, "metric": metric, "total": len(ranking)})
        except Exception as e:
            logger.error(f"Get city ranking full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_product_ranking_full(self, limit: int = 10) -> AnalyticsResponse:
        """Get product ranking."""
        try:
            ranking = self.ranking_engine.get_product_ranking(limit, top=True)
            return AnalyticsResponse(success=True, data={"ranking": ranking, "total": len(ranking)})
        except Exception as e:
            logger.error(f"Get product ranking full failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # RISK METHODS
    # ==========================================================
    
    def get_dealer_risk(self, dealer_name: str) -> AnalyticsResponse:
        """Get dealer risk assessment."""
        try:
            risk = self.risk_engine.assess_dealer_risk(dealer_name)
            return AnalyticsResponse(success=True, data=risk)
        except Exception as e:
            logger.error(f"Get dealer risk failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_high_risk_areas(self) -> AnalyticsResponse:
        """Get high risk areas."""
        try:
            areas = self.risk_engine.get_high_risk_areas()
            return AnalyticsResponse(success=True, data={"high_risk_areas": areas})
        except Exception as e:
            logger.error(f"Get high risk areas failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # FORECAST METHODS
    # ==========================================================
    
    def get_revenue_forecast(self, dealer_name: Optional[str] = None) -> AnalyticsResponse:
        """Get revenue forecast."""
        try:
            self.metrics["forecast_requests"] += 1
            forecast = self.forecast_engine.forecast_revenue(dealer_name)
            return AnalyticsResponse(success=True, data=forecast)
        except Exception as e:
            logger.error(f"Get revenue forecast failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DISTANCE METHODS
    # ==========================================================
    
    def get_dealer_distance(self, dealer_name: str, warehouse_name: Optional[str] = None) -> AnalyticsResponse:
        """Get dealer-warehouse distance."""
        try:
            resolved = self.repo.resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            if not warehouse_name:
                dashboard = self.repo.get_dealer_dashboard(resolved)
                warehouse_name = dashboard.get("warehouse", "")
            
            if not warehouse_name:
                return AnalyticsResponse(success=False, error="No warehouse found for this dealer")
            
            distance = self.distance_engine.calculate_distance(warehouse_name, resolved)
            return AnalyticsResponse(success=True, data=distance)
            
        except Exception as e:
            logger.error(f"Get dealer distance failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # CACHE HELPERS
    # ==========================================================
    
    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache and key in self._cache_ttl:
            if datetime.now() < self._cache_ttl[key]:
                self.metrics["cache_hits"] += 1
                return self._cache[key]
        self.metrics["cache_misses"] += 1        return None
    
    def _set_cached(self, key: str, value: Any, ttl_seconds: int = 300):
        if isinstance(value, dict) and value.get("error"):
            logger.debug(f"Not caching error response for {key}")
            return
        self._cache[key] = value
        self._cache_ttl[key] = datetime.now() + timedelta(seconds=ttl_seconds)
    
    def _get_cached_dealer(self, dealer_input: str) -> Optional[str]:
        if dealer_input in self._dealer_cache:
            resolved, expiry = self._dealer_cache[dealer_input]
            if datetime.now() < expiry:
                return resolved
        return None
    
    def _set_cached_dealer(self, dealer_input: str, resolved: str):
        self._dealer_cache[dealer_input] = (resolved, datetime.now() + timedelta(hours=24))
    
    def clear_cache(self):
        self._cache.clear()
        self._cache_ttl.clear()
        self._dealer_cache.clear()
        if self.repo:
            self.repo._redis_client = None
            self.repo._disk_cache = None
        logger.info("All caches cleared")
    
    def _resolve_dealer(self, dealer_input: str) -> Optional[str]:
        if not dealer_input:
            return None
        cached = self._get_cached_dealer(dealer_input)
        if cached:
            return cached
        resolved = self.repo.resolve_dealer(dealer_input)
        if resolved:
            self._set_cached_dealer(dealer_input, resolved)
            self.metrics["dealer_resolution_success"] += 1
        else:
            self.metrics["dealer_resolution_failure"] += 1
        return resolved
    
    def _normalize_dn(self, dn: str) -> Optional[str]:
        return self.repo.normalize_dn(dn)
    
    # ==========================================================
    # LEGACY METHODS (Maintaining Compatibility)
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Legacy method - redirect to 360 dashboard."""
        return self.get_dealer_dashboard_360(dealer_name)
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> AnalyticsResponse:
        """Legacy method - redirect to full warehouse dashboard."""
        return self.get_warehouse_dashboard_full(warehouse_name)
    
    def get_city_dashboard(self, city_name: str) -> AnalyticsResponse:
        """Legacy method - redirect to full city dashboard."""
        return self.get_city_dashboard_full(city_name)
    
    def get_executive_summary(self) -> AnalyticsResponse:
        """Legacy method - redirect to executive dashboard."""
        return self.get_executive_dashboard_full()
    
    def get_control_tower_alerts(self) -> AnalyticsResponse:
        """Legacy method - redirect to control tower."""
        return self.get_control_tower_full()
    
    def get_dn_analytics(self, dn_number: str) -> AnalyticsResponse:
        """Legacy method - DN tracking."""
        return self.repo.get_dn_analytics(dn_number)
    
    def get_all_dealers_dashboard(self) -> AnalyticsResponse:
        """Legacy method - get all dealers."""
        try:
            dealers = self.ranking_engine.get_dealer_ranking("revenue", 100)
            return AnalyticsResponse(success=True, data={"dealers": dealers, "total": len(dealers)})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_delivery_performance(self) -> AnalyticsResponse:
        """Legacy method - delivery performance."""
        return self.repo.get_delivery_performance()
    
    def get_root_cause_insights(self) -> AnalyticsResponse:
        """Legacy method - root cause insights."""
        return self.repo.get_root_cause_insights()
    
    def get_dealer_ranking(self, limit: int = 10, top: bool = True) -> AnalyticsResponse:
        """Legacy method - dealer ranking."""
        return self.get_dealer_ranking_full("revenue", limit)
    
    def get_warehouse_ranking(self, limit: int = 10, top: bool = True) -> AnalyticsResponse:
        """Legacy method - warehouse ranking."""
        return self.get_warehouse_ranking_full("revenue", limit)
    
    def get_city_ranking(self, limit: int = 10, top: bool = True) -> AnalyticsResponse:
        """Legacy method - city ranking."""
        return self.get_city_ranking_full("revenue", limit)


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

_analytics_service = None

def get_analytics_service(use_redis: bool = False) -> AnalyticsService:
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService(use_redis=use_redis)
    return _analytics_service

__all__ = [
    'AnalyticsService',
    'AnalyticsResponse',
    'get_analytics_service',
    'DEALER_NAME_FIELD',
    'DEALER_CODE_FIELD',
    'CUSTOMER_CODE_FIELD',
    'DN_NO_FIELD',
    'DELIVERY_STATUS_FIELD',
    'PGI_STATUS_FIELD',
    'POD_STATUS_FIELD'
]
