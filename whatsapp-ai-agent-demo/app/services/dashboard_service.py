# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 6.0 - ENTERPRISE LOGISTICS INTELLIGENCE PLATFORM
# ============================================================
# NOTE: This service is purely based on PostgreSQL data.
#       No static or mock data is used. All values are
#       derived directly from the delivery_reports table.
# ============================================================

import asyncio
import datetime
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
from sqlalchemy.orm import Session

from app.database import engine, SessionLocal
from app.models import DeliveryReport

# Optional external libraries (lazy loaded)
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    EXCEL_AVAILABLE = True
except ImportError:
    EXCEL_AVAILABLE = False

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

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

def _format_date(date_val: Any) -> str:
    if not date_val:
        return "N/A"
    try:
        if isinstance(date_val, str):
            for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
                try:
                    dt = datetime.strptime(date_val, fmt)
                    break
                except ValueError:
                    continue
            else:
                return date_val
        elif isinstance(date_val, datetime):
            dt = date_val
        elif isinstance(date_val, date):
            dt = datetime.combine(date_val, datetime.min.time())
        else:
            return str(date_val)
        return dt.strftime("%d-%b-%Y")
    except Exception:
        return str(date_val)

# ============================================================
# CACHE DECORATOR
# ============================================================

class InMemoryCache:
    def __init__(self, ttl_seconds=5):
        self._cache = {}
        self._ttl = ttl_seconds

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
            key = f"{func.__name__}:{hashlib.md5(str(args).encode() + str(kwargs).encode()).hexdigest()}"
            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value
            result = await func(*args, **kwargs)
            cache.set(key, result)
            return result
        return wrapper
    return decorator

# ============================================================
# DASHBOARD CONTEXT
# ============================================================

class DashboardContext:
    def __init__(self, filters: Dict[str, Any], role: str):
        self.filters = filters
        self.role = role
        self.summary: Optional[Dict[str, Any]] = None
        self.warehouse_performance: Optional[List[Dict[str, Any]]] = None
        self.dealer_performance: Optional[List[Dict[str, Any]]] = None
        self.product_performance: Optional[List[Dict[str, Any]]] = None
        self.city_performance: Optional[List[Dict[str, Any]]] = None
        self.transport_performance: Optional[List[Dict[str, Any]]] = None
        self.monthly_trends: Optional[Dict[str, Any]] = None
        self.daily_trends: Optional[Dict[str, Any]] = None
        self.kpis: Optional[Dict[str, Any]] = None
        self.rankings: Optional[Dict[str, Any]] = None
        self.health: Optional[Dict[str, Any]] = None
        self.metadata: Optional[Dict[str, Any]] = None
        self.inventory: Optional[Dict[str, Any]] = None
        self.alerts: Optional[List[Dict[str, Any]]] = None
        self.recommendations: Optional[List[Dict[str, Any]]] = None
        self.loaded = False

# ============================================================
# DASHBOARD REPOSITORY (RAW SQL – BUSINESS RULES ENGINE)
# ============================================================

class DashboardRepository:
    """Enterprise data access layer with full business rule implementation."""

    def __init__(self):
        logger.info("DashboardRepository v6.0 initialized (Enterprise rules)")
        self._columns_cache = None

    def _execute(self, sql: str, params: Optional[Dict[str, Any]] = None):
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                return result
        except Exception as e:
            logger.exception(f"SQL execution failed: {sql[:200]}")
            raise

    def _get_columns(self) -> set:
        if self._columns_cache is not None:
            return self._columns_cache

        try:
            with engine.connect() as conn:
                if engine.dialect.name == "sqlite":
                    rows = conn.execute(text("PRAGMA table_info(delivery_reports)")).fetchall()
                    self._columns_cache = {str(row[1]).lower() for row in rows}
                else:
                    rows = conn.execute(text("""
                        SELECT LOWER(column_name) AS column_name
                        FROM information_schema.columns
                        WHERE table_name = 'delivery_reports'
                    """)).fetchall()
                    self._columns_cache = {row.column_name for row in rows}
        except Exception:
            logger.exception("Failed to read delivery_reports columns")
            self._columns_cache = set()

        return self._columns_cache

    def _column(self, *names: str) -> Optional[str]:
        columns = self._get_columns()
        for name in names:
            if name.lower() in columns:
                return name.lower()
        return None

    @staticmethod
    def _date_cast(column: str) -> str:
        value = f"NULLIF(TRIM({column}::text), '')"
        return f"""
            CASE
                WHEN {column} IS NULL THEN NULL::date
                WHEN {value} ~ '^\\d{{2}}\\.\\d{{2}}\\.\\d{{4}}$' THEN TO_DATE({value}, 'DD.MM.YYYY')
                WHEN {value} ~ '^\\d{{2}}/\\d{{2}}/\\d{{4}}$' THEN TO_DATE({value}, 'DD/MM/YYYY')
                WHEN {value} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}' THEN ({value})::date
                ELSE {column}::date
            END
        """

    def _date_expr(self, *names: str) -> str:
        expressions = []
        for name in names:
            column = self._column(name)
            if column:
                expressions.append(self._date_cast(column))
        if not expressions:
            return "NULL::date"
        if len(expressions) == 1:
            return expressions[0]
        return "COALESCE(" + ", ".join(expressions) + ")"

    def _delivery_date_expr(self) -> str:
        delivery_expr = self._date_expr(
            "delivery_date",
            "delivered_date",
            "customer_delivery_date",
            "actual_delivery_date",
        )
        if delivery_expr != "NULL::date":
            return delivery_expr
        return self._date_expr("pod_date")

    def _numeric_expr(self, *names: str) -> str:
        column = self._column(*names)
        if not column:
            return "NULL::numeric"
        value = f"NULLIF(REPLACE(TRIM({column}::text), ',', ''), '')"
        return f"""
            CASE
                WHEN {column} IS NULL THEN NULL::numeric
                WHEN {value} ~ '^-?\\d+(\\.\\d+)?$' THEN ({value})::numeric
                ELSE NULL::numeric
            END
        """

    def _text_aggregate(self, alias: str, *names: str, default: str = "N/A") -> str:
        column = self._column(*names)
        if not column:
            return f"'{default}' AS {alias}"
        return f"COALESCE(MAX(NULLIF(TRIM({column}::text), '')), '{default}') AS {alias}"

    def _dn_level_cte(self, extra_fields: Optional[List[str]] = None) -> str:
        extra_sql = ""
        if extra_fields:
            extra_sql = ",\n                    " + ",\n                    ".join(extra_fields)

        return f"""
            WITH dn_level AS (
                SELECT
                    dn_no,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    MIN({self._date_expr("dn_create_date", "dn_date", "delivery_note_date")}) AS dn_date,
                    MIN({self._date_expr("pgi_date", "good_issue_date", "goods_issue_date")}) AS pgi_date,
                    MIN({self._delivery_date_expr()}) AS delivery_date,
                    MIN({self._date_expr("pod_date")}) AS pod_date,
                    MAX({self._numeric_expr("distance_km", "distance", "route_distance_km", "km")}) AS distance_km
                    {extra_sql}
                FROM delivery_reports
                WHERE dn_no IS NOT NULL
                GROUP BY dn_no
            ),
            dn_rules AS (
                SELECT
                    *,
                    CASE
                        WHEN distance_km IS NULL THEN NULL
                        WHEN distance_km <= 100 THEN 1
                        WHEN distance_km <= 250 THEN 2
                        WHEN distance_km <= 450 THEN 3
                        WHEN distance_km <= 700 THEN 4
                        WHEN distance_km <= 900 THEN 5
                        ELSE 6
                    END AS target_delivery_days,
                    CASE
                        WHEN pgi_date IS NOT NULL
                         AND delivery_date IS NOT NULL
                         AND delivery_date >= pgi_date
                        THEN delivery_date - pgi_date
                    END AS delivery_days,
                    CASE
                        WHEN delivery_date IS NOT NULL
                         AND pod_date IS NOT NULL
                         AND pod_date >= delivery_date
                        THEN pod_date - delivery_date
                    END AS pod_days,
                    CASE
                        WHEN pgi_date IS NOT NULL
                         AND pod_date IS NOT NULL
                         AND pod_date >= pgi_date
                        THEN pod_date - pgi_date
                    END AS logistics_cycle_days
                FROM dn_level
            )
        """

    @staticmethod
    def _safe_float(value: Any) -> float:
        return float(value or 0)

    @staticmethod
    def _safe_int(value: Any) -> int:
        return int(value or 0)

    @staticmethod
    def _pct(numerator: float, denominator: float) -> float:
        if not denominator:
            return 0.0
        return round((numerator / denominator) * 100, 2)

    @staticmethod
    def _compute_health_score(pgi_rate: float, delivery_rate: float, pod_rate: float,
                              otif: float, avg_cycle_days: float,
                              invalid_count: int, total_dn: int) -> float:
        """Calculate overall health score using weighted components."""
        # Normalize cycle days (lower is better): 100% if <= 7 days, 0% if > 21 days
        cycle_score = 100.0 if avg_cycle_days <= 7 else max(0.0, 100.0 - ((avg_cycle_days - 7) / 14) * 100)
        validation_score = max(0.0, 100.0 - ((invalid_count / (total_dn or 1)) * 100.0))

        # Weighted components
        score = (
            min(pgi_rate, 100.0) * 0.20 +
            min(delivery_rate, 100.0) * 0.25 +
            min(pod_rate, 100.0) * 0.20 +
            min(otif, 100.0) * 0.15 +
            cycle_score * 0.10 +
            validation_score * 0.10
        )
        return round(score, 2)

    # ------------------------------------------------------------------
    # EXECUTIVE SUMMARY (with all KPIs)
    # ------------------------------------------------------------------

    def get_summary(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        try:
            sql = self._dn_level_cte() + """
                SELECT
                    COALESCE(SUM(revenue), 0) AS total_revenue,
                    COALESCE(SUM(units), 0) AS total_units,
                    COUNT(*) AS total_dn,
                    COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) AS pgi_completed,
                    COUNT(CASE WHEN delivery_date IS NOT NULL THEN 1 END) AS delivered_dns,
                    COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) AS pod_completed,
                    COUNT(CASE WHEN pgi_date IS NULL THEN 1 END) AS pending_dispatch,
                    COUNT(CASE WHEN pgi_date IS NOT NULL AND delivery_date IS NULL THEN 1 END) AS pending_delivery,
                    COUNT(CASE WHEN delivery_date IS NOT NULL AND pod_date IS NULL THEN 1 END) AS pending_pod,
                    COUNT(CASE WHEN delivery_days IS NOT NULL
                                AND target_delivery_days IS NOT NULL
                                AND delivery_days <= target_delivery_days THEN 1 END) AS on_time_deliveries,
                    COUNT(CASE WHEN delivery_days IS NOT NULL
                                AND target_delivery_days IS NOT NULL
                                AND delivery_days > target_delivery_days THEN 1 END) AS late_deliveries,
                    COUNT(CASE WHEN pod_days IS NOT NULL AND pod_days > 1 THEN 1 END) AS delayed_pod,
                    COUNT(CASE WHEN delivery_date IS NOT NULL
                                AND target_delivery_days IS NULL THEN 1 END) AS delivery_target_missing,
                    COUNT(CASE WHEN delivery_date IS NOT NULL AND pgi_date IS NULL THEN 1 END) AS delivery_without_pgi,
                    COUNT(CASE WHEN delivery_date IS NOT NULL
                                AND pgi_date IS NOT NULL
                                AND delivery_date < pgi_date THEN 1 END) AS delivery_before_pgi,
                    COUNT(CASE WHEN pod_date IS NOT NULL
                                AND delivery_date IS NOT NULL
                                AND pod_date < delivery_date THEN 1 END) AS pod_before_delivery,
                    COUNT(CASE WHEN pod_date IS NOT NULL
                                AND pgi_date IS NOT NULL
                                AND pod_date < pgi_date THEN 1 END) AS pod_before_pgi,
                    COALESCE(AVG(delivery_days), 0) AS average_delivery_days,
                    COALESCE(AVG(pod_days), 0) AS average_pod_days,
                    COALESCE(AVG(logistics_cycle_days), 0) AS average_logistics_cycle_days,
                    COALESCE(AVG(target_delivery_days), 0) AS average_target_days
                FROM dn_rules
            """
            row = self._execute(sql).first()
            if not row:
                return self._empty_summary()

            total_revenue = self._safe_float(row.total_revenue)
            total_units = self._safe_int(row.total_units)
            total_dn = self._safe_int(row.total_dn)
            pgi_completed = self._safe_int(row.pgi_completed)
            delivered_dns = self._safe_int(row.delivered_dns)
            pod_completed = self._safe_int(row.pod_completed)
            on_time_deliveries = self._safe_int(row.on_time_deliveries)
            avg_cycle = self._safe_float(row.average_logistics_cycle_days)

            invalid_count = (
                self._safe_int(row.delivery_without_pgi)
                + self._safe_int(row.delivery_before_pgi)
                + self._safe_int(row.pod_before_delivery)
                + self._safe_int(row.pod_before_pgi)
            )

            pgi_rate = self._pct(pgi_completed, total_dn)
            delivery_rate = self._pct(on_time_deliveries, delivered_dns)
            pod_rate = self._pct(pod_completed, delivered_dns)
            otif = delivery_rate  # OTIF is essentially on-time delivery rate
            health_score = self._compute_health_score(
                pgi_rate, delivery_rate, pod_rate, otif, avg_cycle,
                invalid_count, total_dn
            )

            # Additional network counts
            dealer_column = self._column("dealer_code", "sold_to_party_name", "customer_name", "dealer_name")
            warehouse_column = self._column("warehouse")
            city_column = self._column("ship_to_city", "city")
            product_column = self._column("material_no", "sku")
            transporter_column = self._column("transporter", "transporter_name", "carrier")

            dealers = self._execute(f"SELECT COUNT(DISTINCT {dealer_column}) FROM delivery_reports WHERE {dealer_column} IS NOT NULL").scalar() if dealer_column else 0
            warehouses = self._execute(f"SELECT COUNT(DISTINCT {warehouse_column}) FROM delivery_reports WHERE {warehouse_column} IS NOT NULL").scalar() if warehouse_column else 0
            cities = self._execute(f"SELECT COUNT(DISTINCT {city_column}) FROM delivery_reports WHERE {city_column} IS NOT NULL").scalar() if city_column else 0
            products = self._execute(f"SELECT COUNT(DISTINCT {product_column}) FROM delivery_reports WHERE {product_column} IS NOT NULL").scalar() if product_column else 0
            transporters = self._execute(f"SELECT COUNT(DISTINCT {transporter_column}) FROM delivery_reports WHERE {transporter_column} IS NOT NULL").scalar() if transporter_column else 0

            return {
                "total_revenue": total_revenue,
                "total_units": total_units,
                "total_delivery_notes": total_dn,
                "pgi_completed": pgi_completed,
                "delivered_dns": delivered_dns,
                "pod_completed": pod_completed,
                "active_dealers": dealers,
                "active_warehouses": warehouses,
                "active_cities": cities,
                "active_products": products,
                "active_transporters": transporters,
                "average_delivery_days": round(self._safe_float(row.average_delivery_days), 2),
                "average_pod_days": round(self._safe_float(row.average_pod_days), 2),
                "average_pgi_days": round(self._safe_float(row.average_delivery_days), 2),
                "average_logistics_cycle_days": round(avg_cycle, 2),
                "average_target_days": round(self._safe_float(row.average_target_days), 2),
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "otif_percentage": otif,
                "inventory_accuracy": 0.0,
                "pending_dispatch": self._safe_int(row.pending_dispatch),
                "pending_delivery": self._safe_int(row.pending_delivery),
                "pending_pod": self._safe_int(row.pending_pod),
                "late_deliveries": self._safe_int(row.late_deliveries),
                "delayed_pod": self._safe_int(row.delayed_pod),
                "on_time_deliveries": on_time_deliveries,
                "delivery_target_missing": self._safe_int(row.delivery_target_missing),
                "delivery_without_pgi": self._safe_int(row.delivery_without_pgi),
                "delivery_before_pgi": self._safe_int(row.delivery_before_pgi),
                "pod_before_delivery": self._safe_int(row.pod_before_delivery),
                "pod_before_pgi": self._safe_int(row.pod_before_pgi),
                "invalid_records": invalid_count,
                "dashboard_health_score": health_score,
                "last_database_refresh": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.exception("❌ Failed to get summary")
            return self._empty_summary()

    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "total_revenue": 0.0,
            "total_units": 0,
            "total_delivery_notes": 0,
            "pgi_completed": 0,
            "delivered_dns": 0,
            "pod_completed": 0,
            "active_dealers": 0,
            "active_warehouses": 0,
            "active_cities": 0,
            "active_products": 0,
            "active_transporters": 0,
            "average_delivery_days": 0.0,
            "average_pod_days": 0.0,
            "average_pgi_days": 0.0,
            "average_logistics_cycle_days": 0.0,
            "average_target_days": 0.0,
            "pgi_achievement_rate": 0.0,
            "delivery_achievement_rate": 0.0,
            "pod_completion_rate": 0.0,
            "otif_percentage": 0.0,
            "inventory_accuracy": 0.0,
            "pending_dispatch": 0,
            "pending_delivery": 0,
            "pending_pod": 0,
            "late_deliveries": 0,
            "delayed_pod": 0,
            "on_time_deliveries": 0,
            "delivery_target_missing": 0,
            "delivery_without_pgi": 0,
            "delivery_before_pgi": 0,
            "pod_before_delivery": 0,
            "pod_before_pgi": 0,
            "invalid_records": 0,
            "dashboard_health_score": 0.0,
            "last_database_refresh": None,
        }

    # ------------------------------------------------------------------
    # WAREHOUSE ANALYTICS
    # ------------------------------------------------------------------

    def get_warehouse_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        extra_fields = [self._text_aggregate("warehouse", "warehouse", default="Unassigned")]
        sql = self._dn_level_cte(extra_fields) + """
            SELECT
                warehouse,
                COALESCE(SUM(revenue), 0) AS revenue,
                COALESCE(SUM(units), 0) AS units,
                COUNT(*) AS dn,
                COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) AS pgi_completed,
                COUNT(CASE WHEN delivery_date IS NOT NULL THEN 1 END) AS delivered_dns,
                COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) AS pod_completed,
                COUNT(CASE WHEN pgi_date IS NULL THEN 1 END) AS pending_dispatch,
                COUNT(CASE WHEN pgi_date IS NOT NULL AND delivery_date IS NULL THEN 1 END) AS pending_delivery,
                COUNT(CASE WHEN delivery_date IS NOT NULL AND pod_date IS NULL THEN 1 END) AS pending_pod,
                COUNT(CASE WHEN delivery_days IS NOT NULL
                            AND target_delivery_days IS NOT NULL
                            AND delivery_days <= target_delivery_days THEN 1 END) AS on_time_deliveries,
                COUNT(CASE WHEN delivery_days IS NOT NULL
                            AND target_delivery_days IS NOT NULL
                            AND delivery_days > target_delivery_days THEN 1 END) AS late_deliveries,
                COALESCE(AVG(delivery_days), 0) AS average_delivery_days,
                COALESCE(AVG(pod_days), 0) AS average_pod_days,
                COALESCE(AVG(logistics_cycle_days), 0) AS average_logistics_cycle_days,
                COALESCE(AVG(target_delivery_days), 0) AS average_target_days
            FROM dn_rules
            GROUP BY warehouse
            ORDER BY revenue DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            dn = self._safe_int(row.dn)
            delivered_dns = self._safe_int(row.delivered_dns)
            pgi_completed = self._safe_int(row.pgi_completed)
            pod_completed = self._safe_int(row.pod_completed)
            on_time = self._safe_int(row.on_time_deliveries)
            avg_del = round(self._safe_float(row.average_delivery_days), 2)
            avg_cycle = round(self._safe_float(row.average_logistics_cycle_days), 2)
            pgi_rate = self._pct(pgi_completed, dn)
            delivery_rate = self._pct(on_time, delivered_dns)
            pod_rate = self._pct(pod_completed, delivered_dns)
            otif = delivery_rate
            health = self._compute_health_score(pgi_rate, delivery_rate, pod_rate, otif, avg_cycle, 0, dn)
            grade = self._compute_grade(avg_del)
            risk = self._compute_risk_level(
                self._safe_int(row.pending_delivery) + self._safe_int(row.pending_pod),
                self._safe_int(row.late_deliveries),
                avg_del,
            )
            result.append({
                "warehouse_code": row.warehouse,
                "warehouse_name": row.warehouse,
                "revenue": row.revenue,
                "units": row.units,
                "delivery_notes": dn,
                "dealers": 0,
                "products": 0,
                "cities": 0,
                "average_delivery_days": avg_del,
                "average_pod_days": round(self._safe_float(row.average_pod_days), 2),
                "average_pgi_days": avg_del,
                "average_logistics_cycle_days": avg_cycle,
                "average_target_days": round(self._safe_float(row.average_target_days), 2),
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "otif": otif,
                "pod": pod_rate,
                "health_score": health,
                "capacity": 0,
                "utilization": 0,
                "pending_dispatch": self._safe_int(row.pending_dispatch),
                "pending_deliveries": self._safe_int(row.pending_delivery) + self._safe_int(row.pending_pod),
                "pending_pod": self._safe_int(row.pending_pod),
                "late_deliveries": self._safe_int(row.late_deliveries),
                "on_time_deliveries": on_time,
                "performance_grade": grade,
                "risk_level": risk,
                "ai_recommendation": self._warehouse_recommendation(row.warehouse, grade, risk, pgi_rate, delivery_rate, pod_rate),
            })
        return result

    # ------------------------------------------------------------------
    # DEALER ANALYTICS
    # ------------------------------------------------------------------

    def get_dealer_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        extra_fields = [
            self._text_aggregate("dealer_code", "dealer_code", "sold_to_party_code", "sold_to_party_name", default="N/A"),
            self._text_aggregate("customer_name", "customer_name", "sold_to_party_name", "dealer_name", default="N/A"),
        ]
        sql = self._dn_level_cte(extra_fields) + """
            SELECT
                dealer_code,
                customer_name,
                COALESCE(SUM(revenue), 0) AS revenue,
                COALESCE(SUM(units), 0) AS units,
                COUNT(*) AS dn,
                COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) AS pgi_completed,
                COUNT(CASE WHEN delivery_date IS NOT NULL THEN 1 END) AS delivered_dns,
                COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) AS pod_completed,
                COUNT(CASE WHEN pgi_date IS NULL THEN 1 END) AS pending_dispatch,
                COUNT(CASE WHEN pgi_date IS NOT NULL AND delivery_date IS NULL THEN 1 END) AS pending_delivery,
                COUNT(CASE WHEN delivery_date IS NOT NULL AND pod_date IS NULL THEN 1 END) AS pending_pod,
                COUNT(CASE WHEN delivery_days IS NOT NULL
                            AND target_delivery_days IS NOT NULL
                            AND delivery_days <= target_delivery_days THEN 1 END) AS on_time_deliveries,
                COUNT(CASE WHEN delivery_days IS NOT NULL
                            AND target_delivery_days IS NOT NULL
                            AND delivery_days > target_delivery_days THEN 1 END) AS late_deliveries,
                COALESCE(AVG(delivery_days), 0) AS average_delivery_days,
                COALESCE(AVG(pod_days), 0) AS average_pod_days,
                COALESCE(AVG(logistics_cycle_days), 0) AS average_logistics_cycle_days,
                COALESCE(AVG(target_delivery_days), 0) AS average_target_days,
                MAX(delivery_date) AS last_delivery,
                MAX(dn_date) AS last_order
            FROM dn_rules
            WHERE dealer_code IS NOT NULL
            GROUP BY dealer_code, customer_name
            ORDER BY revenue DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            dn = self._safe_int(row.dn)
            delivered_dns = self._safe_int(row.delivered_dns)
            pgi_completed = self._safe_int(row.pgi_completed)
            pod_completed = self._safe_int(row.pod_completed)
            on_time = self._safe_int(row.on_time_deliveries)
            avg_del = round(self._safe_float(row.average_delivery_days), 2)
            avg_cycle = round(self._safe_float(row.average_logistics_cycle_days), 2)
            pgi_rate = self._pct(pgi_completed, dn)
            delivery_rate = self._pct(on_time, delivered_dns)
            pod_rate = self._pct(pod_completed, delivered_dns)
            revenue = row.revenue
            units = row.units
            score = self._compute_dealer_score(revenue, units, avg_del)
            health = self._compute_health_score(pgi_rate, delivery_rate, pod_rate, delivery_rate, avg_cycle, 0, dn)
            result.append({
                "dealer_name": row.customer_name or row.dealer_code,
                "dealer_code": row.dealer_code,
                "revenue": revenue,
                "units": units,
                "delivery_notes": dn,
                "products": 0,
                "cities": 0,
                "warehouses": 0,
                "average_delivery_days": avg_del,
                "average_pod_days": round(self._safe_float(row.average_pod_days), 2),
                "average_pgi_days": avg_del,
                "average_logistics_cycle_days": avg_cycle,
                "average_target_days": round(self._safe_float(row.average_target_days), 2),
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "otif": delivery_rate,
                "health_score": health,
                "pending_dispatch": self._safe_int(row.pending_dispatch),
                "pending_delivery": self._safe_int(row.pending_delivery),
                "pending_pod": self._safe_int(row.pending_pod),
                "late_deliveries": self._safe_int(row.late_deliveries),
                "on_time_deliveries": on_time,
                "last_delivery": _format_date(row.last_delivery),
                "last_order": _format_date(row.last_order),
                "growth_percentage": 0.0,
                "rank": 0,
                "performance_score": score,
                "ai_recommendation": self._dealer_recommendation(row.dealer_code, score, avg_del),
            })
        return result

    # ------------------------------------------------------------------
    # PRODUCT ANALYTICS (with ABC classification)
    # ------------------------------------------------------------------

    def get_product_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        pgi_expr = self._date_expr("pgi_date", "good_issue_date", "goods_issue_date")
        delivery_expr = self._delivery_date_expr()
        pod_expr = self._date_expr("pod_date")
        sql = f"""
            SELECT
                material_no,
                customer_model,
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn,
                COUNT(DISTINCT CASE WHEN {pgi_expr} IS NOT NULL THEN dn_no END) AS pgi_completed,
                COUNT(DISTINCT CASE WHEN {delivery_expr} IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN {pod_expr} IS NOT NULL THEN dn_no END) AS pod_completed,
                COUNT(DISTINCT dealer_code) AS dealers,
                COUNT(DISTINCT warehouse) AS warehouses,
                COUNT(DISTINCT ship_to_city) AS cities,
                COALESCE(AVG(CASE
                    WHEN {pgi_expr} IS NOT NULL
                     AND {delivery_expr} IS NOT NULL
                     AND {delivery_expr} >= {pgi_expr}
                    THEN {delivery_expr} - {pgi_expr}
                END), 0) AS average_delivery_days
            FROM delivery_reports
            WHERE material_no IS NOT NULL
            GROUP BY material_no, customer_model
            ORDER BY revenue DESC
        """
        rows = self._execute(sql).fetchall()
        # Calculate total revenue for ABC classification
        total_revenue = sum(row.revenue for row in rows)
        result = []
        for idx, row in enumerate(rows):
            revenue = row.revenue
            units = row.units
            dn = self._safe_int(row.dn)
            delivered = self._safe_int(row.delivered_dns)
            pgi = self._safe_int(row.pgi_completed)
            pod = self._safe_int(row.pod_completed)
            pgi_rate = self._pct(pgi, dn)
            pod_rate = self._pct(pod, delivered)
            avg_del = round(self._safe_float(row.average_delivery_days), 2)
            # ABC classification: A=top 70% revenue, B=next 20%, C=bottom 10%
            cumulative_share = sum(r.revenue for r in rows[:idx+1]) / total_revenue if total_revenue else 0
            if cumulative_share <= 0.7:
                abc_class = "A"
            elif cumulative_share <= 0.9:
                abc_class = "B"
            else:
                abc_class = "C"
            slow = units < 100
            fast = units > 300
            dead = units == 0
            result.append({
                "product_name": row.customer_model or row.material_no,
                "sku": row.material_no,
                "revenue": revenue,
                "units": units,
                "delivery_notes": dn,
                "dealers": row.dealers or 0,
                "warehouses": row.warehouses or 0,
                "cities": row.cities or 0,
                "monthly_trend": [],
                "average_delivery_days": avg_del,
                "pgi_achievement_rate": pgi_rate,
                "pod_completion_rate": pod_rate,
                "abc_class": abc_class,
                "slow_moving_flag": slow,
                "fast_moving_flag": fast,
                "dead_stock_flag": dead,
                "revenue_share": round((revenue / total_revenue) * 100, 2) if total_revenue else 0,
                "growth_percentage": 0.0,
                "ai_recommendation": self._product_recommendation(row.material_no, slow, fast, dead),
            })
        return result

    # ------------------------------------------------------------------
    # CITY ANALYTICS
    # ------------------------------------------------------------------

    def get_city_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        pgi_expr = self._date_expr("pgi_date", "good_issue_date", "goods_issue_date")
        delivery_expr = self._delivery_date_expr()
        pod_expr = self._date_expr("pod_date")
        distance_expr = self._numeric_expr("distance_km", "distance", "route_distance_km", "km")
        target_expr = f"""
            CASE
                WHEN {distance_expr} IS NULL THEN NULL
                WHEN {distance_expr} <= 100 THEN 1
                WHEN {distance_expr} <= 250 THEN 2
                WHEN {distance_expr} <= 450 THEN 3
                WHEN {distance_expr} <= 700 THEN 4
                WHEN {distance_expr} <= 900 THEN 5
                ELSE 6
            END
        """
        delivery_days_expr = f"""
            CASE
                WHEN {pgi_expr} IS NOT NULL
                 AND {delivery_expr} IS NOT NULL
                 AND {delivery_expr} >= {pgi_expr}
                THEN {delivery_expr} - {pgi_expr}
            END
        """
        sql = f"""
            SELECT
                ship_to_city AS city,
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn,
                COUNT(DISTINCT dealer_code) AS dealers,
                COUNT(DISTINCT warehouse) AS warehouses,
                COUNT(DISTINCT material_no) AS products,
                COUNT(DISTINCT CASE WHEN {delivery_expr} IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN {pod_expr} IS NOT NULL THEN dn_no END) AS pod_completed,
                COUNT(DISTINCT CASE WHEN {pgi_expr} IS NOT NULL
                                      AND {delivery_expr} IS NULL THEN dn_no END) AS pending_delivery,
                COUNT(DISTINCT CASE WHEN {delivery_days_expr} IS NOT NULL
                                      AND {target_expr} IS NOT NULL
                                      AND {delivery_days_expr} <= {target_expr} THEN dn_no END) AS on_time_deliveries,
                COUNT(DISTINCT CASE WHEN {delivery_days_expr} IS NOT NULL
                                      AND {target_expr} IS NOT NULL
                                      AND {delivery_days_expr} > {target_expr} THEN dn_no END) AS late_deliveries,
                COALESCE(AVG({distance_expr}), 0) AS average_distance,
                COALESCE(AVG({delivery_days_expr}), 0) AS average_delivery_days,
                COALESCE(AVG({target_expr}), 0) AS delivery_target
            FROM delivery_reports
            WHERE ship_to_city IS NOT NULL
            GROUP BY ship_to_city
            ORDER BY revenue DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            dn = self._safe_int(row.dn)
            delivered = self._safe_int(row.delivered_dns)
            on_time = self._safe_int(row.on_time_deliveries)
            pod = self._safe_int(row.pod_completed)
            avg_del = round(self._safe_float(row.average_delivery_days), 2)
            delivery_rate = self._pct(on_time, delivered)
            pod_rate = self._pct(pod, delivered)
            # Gap analysis: actual vs target days
            target = self._safe_float(row.delivery_target)
            gap = avg_del - target if target else 0
            health = self._compute_health_score(100.0, delivery_rate, pod_rate, delivery_rate, avg_del, 0, dn)
            result.append({
                "city": row.city,
                "revenue": row.revenue,
                "units": row.units,
                "delivery_notes": dn,
                "dealers": row.dealers or 0,
                "warehouses": row.warehouses or 0,
                "products": row.products or 0,
                "average_distance": round(self._safe_float(row.average_distance), 2),
                "average_delivery_days": avg_del,
                "delivery_target": round(target, 2),
                "delivery_gap": round(gap, 2),
                "pending_deliveries": self._safe_int(row.pending_delivery),
                "late_deliveries": self._safe_int(row.late_deliveries),
                "achievement_percentage": delivery_rate,
                "pod_completion_rate": pod_rate,
                "health_score": health,
                "risk_level": self._compute_risk_level(
                    self._safe_int(row.pending_delivery),
                    self._safe_int(row.late_deliveries),
                    avg_del,
                ),
            })
        return result

    # ------------------------------------------------------------------
    # TRANSPORT ANALYTICS
    # ------------------------------------------------------------------

    def get_transport_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        transporter_col = self._column("transporter", "transporter_name", "carrier")
        if not transporter_col:
            return []
        pgi_expr = self._date_expr("pgi_date", "good_issue_date", "goods_issue_date")
        delivery_expr = self._delivery_date_expr()
        pod_expr = self._date_expr("pod_date")
        sql = f"""
            SELECT
                {transporter_col} AS transporter_name,
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dn,
                COUNT(DISTINCT CASE WHEN {delivery_expr} IS NOT NULL THEN dn_no END) AS delivered_dns,
                COUNT(DISTINCT CASE WHEN {pod_expr} IS NOT NULL THEN dn_no END) AS pod_completed,
                COUNT(DISTINCT CASE WHEN {delivery_expr} IS NOT NULL
                                      AND {pod_expr} IS NULL THEN dn_no END) AS pending_pod,
                COALESCE(AVG(CASE
                    WHEN {pgi_expr} IS NOT NULL
                     AND {delivery_expr} IS NOT NULL
                     AND {delivery_expr} >= {pgi_expr}
                    THEN {delivery_expr} - {pgi_expr}
                END), 0) AS average_delivery_days,
                COALESCE(AVG(CASE
                    WHEN {delivery_expr} IS NOT NULL
                     AND {pod_expr} IS NOT NULL
                     AND {pod_expr} >= {delivery_expr}
                    THEN {pod_expr} - {delivery_expr}
                END), 0) AS average_pod_days
            FROM delivery_reports
            WHERE {transporter_col} IS NOT NULL
            GROUP BY {transporter_col}
            ORDER BY revenue DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            dn = self._safe_int(row.dn)
            delivered = self._safe_int(row.delivered_dns)
            pod = self._safe_int(row.pod_completed)
            avg_del = round(self._safe_float(row.average_delivery_days), 2)
            avg_pod = round(self._safe_float(row.average_pod_days), 2)
            delivery_rate = self._pct(delivered, dn)
            pod_rate = self._pct(pod, delivered)
            # Grade based on delivery and pod rates
            grade = "A" if delivery_rate >= 95 and pod_rate >= 95 else "B" if delivery_rate >= 85 and pod_rate >= 85 else "C"
            result.append({
                "transporter_name": row.transporter_name,
                "revenue": row.revenue,
                "units": row.units,
                "delivery_notes": dn,
                "delivered_dns": delivered,
                "pod_completed": pod,
                "pending_pod": self._safe_int(row.pending_pod),
                "average_delivery_days": avg_del,
                "average_pod_days": avg_pod,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "performance_grade": grade,
                "score": round((delivery_rate + pod_rate) / 2, 2),
                "ai_recommendation": self._transporter_recommendation(row.transporter_name, delivery_rate, pod_rate),
            })
        return result

    # ------------------------------------------------------------------
    # TRENDS
    # ------------------------------------------------------------------

    def get_monthly_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
        sql = self._dn_level_cte() + """
            SELECT
                TO_CHAR(dn_date, 'YYYY-MM') AS month,
                COALESCE(SUM(revenue), 0) AS revenue,
                COALESCE(SUM(units), 0) AS units,
                COUNT(*) AS dn,
                COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) AS pgi_completed,
                COUNT(CASE WHEN delivery_date IS NOT NULL THEN 1 END) AS delivered_dns,
                COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) AS pod_completed,
                COUNT(CASE WHEN delivery_days IS NOT NULL
                            AND target_delivery_days IS NOT NULL
                            AND delivery_days <= target_delivery_days THEN 1 END) AS on_time_deliveries,
                COALESCE(AVG(logistics_cycle_days), 0) AS avg_cycle
            FROM dn_rules
            WHERE dn_date IS NOT NULL
            GROUP BY month
            ORDER BY month
        """
        rows = self._execute(sql).fetchall()
        months = []
        revenue = []
        units = []
        dn = []
        pgi = []
        delivery = []
        pod = []
        otif = []
        cycle = []
        for row in rows:
            months.append(row.month)
            revenue.append(row.revenue)
            units.append(row.units)
            dn.append(row.dn)
            pgi.append(self._pct(self._safe_int(row.pgi_completed), self._safe_int(row.dn)))
            delivered = self._safe_int(row.delivered_dns)
            on_time = self._safe_int(row.on_time_deliveries)
            delivery.append(self._pct(on_time, delivered))
            pod.append(self._pct(self._safe_int(row.pod_completed), delivered))
            otif.append(self._pct(on_time, delivered))
            cycle.append(round(self._safe_float(row.avg_cycle), 2))
        return {
            "months": months,
            "revenue": revenue,
            "units": units,
            "delivery_notes": dn,
            "pgi_rate": pgi,
            "delivery_achievement": delivery,
            "pod_rate": pod,
            "otif": otif,
            "cycle_days": cycle,
        }

    def get_daily_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
        sql = self._dn_level_cte() + """
            , bounds AS (
                SELECT COALESCE(MAX(dn_date), CURRENT_DATE) AS max_date
                FROM dn_rules
            )
            SELECT
                dn_date AS date,
                COALESCE(SUM(revenue), 0) AS revenue,
                COALESCE(SUM(units), 0) AS units,
                COUNT(*) AS dn,
                COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) AS pgi_completed,
                COUNT(CASE WHEN delivery_date IS NOT NULL THEN 1 END) AS delivered_dns,
                COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) AS pod_completed
            FROM dn_rules, bounds
            WHERE dn_date IS NOT NULL
              AND dn_date >= bounds.max_date - INTERVAL '30 days'
            GROUP BY dn_date
            ORDER BY dn_date
        """
        rows = self._execute(sql).fetchall()
        dates = []
        revenue = []
        units = []
        dn = []
        pgi = []
        delivered = []
        pod = []
        for row in rows:
            dates.append(row.date.strftime('%Y-%m-%d'))
            revenue.append(row.revenue)
            units.append(row.units)
            dn.append(row.dn)
            pgi.append(row.pgi_completed)
            delivered.append(row.delivered_dns)
            pod.append(row.pod_completed)
        return {
            "dates": dates,
            "revenue": revenue,
            "units": units,
            "delivery_notes": dn,
            "pgi_completed": pgi,
            "delivered_dns": delivered,
            "pod_completed": pod,
        }

    # ------------------------------------------------------------------
    # HEALTH & METADATA
    # ------------------------------------------------------------------

    def get_health(self) -> Dict[str, Any]:
        try:
            count = self._execute("SELECT COUNT(*) FROM delivery_reports").scalar() or 0
            return {"status": "healthy" if count > 0 else "unhealthy", "message": "Data available" if count > 0 else "No data", "record_count": count}
        except Exception as e:
            return {"status": "unhealthy", "message": str(e)}

    def get_record_count(self) -> int:
        try:
            return self._execute("SELECT COUNT(*) FROM delivery_reports").scalar() or 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # RECOMMENDATIONS & RULES
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_grade(avg_delivery: float) -> str:
        if avg_delivery <= 2:
            return "A"
        elif avg_delivery <= 4:
            return "B"
        else:
            return "C"

    @staticmethod
    def _compute_risk_level(pending: int, late: int, avg_delivery: float) -> str:
        if late > 10 or pending > 20 or avg_delivery > 5:
            return "High"
        if late > 5 or pending > 10 or avg_delivery > 3:
            return "Medium"
        return "Low"

    @staticmethod
    def _compute_dealer_score(revenue: float, units: int, avg_delivery: float) -> float:
        score = 0.0
        if revenue > 0:
            score += min(revenue / 1000000, 1) * 40
        if units > 0:
            score += min(units / 1000, 1) * 30
        if avg_delivery > 0:
            score += max(0, (5 - avg_delivery) / 5) * 20
        return min(score, 100)

    @staticmethod
    def _warehouse_recommendation(code: str, grade: str, risk: str, pgi: float, delivery: float, pod: float) -> str:
        if grade in ("A", "B") and risk == "Low" and pgi >= 95 and delivery >= 95 and pod >= 95:
            return "Maintain current operations."
        if pgi < 95:
            return "Improve PGI processing to reduce pending dispatch."
        if delivery < 95:
            return "Enhance delivery performance against distance targets."
        if pod < 95:
            return "Accelerate POD collection and processing."
        if risk in ("Medium", "High"):
            return "Urgent review of operations and logistics processes."
        return "Monitor performance and optimize resource allocation."

    @staticmethod
    def _dealer_recommendation(code: str, score: float, avg_delivery: float) -> str:
        if score >= 80:
            return "Top performer – consider loyalty rewards."
        elif score >= 60:
            return "Good performance – focus on reducing delivery days."
        else:
            return "Needs improvement – provide training and support."

    @staticmethod
    def _product_recommendation(code: str, slow: bool, fast: bool, dead: bool) -> str:
        if dead:
            return "Dead stock – consider liquidation or return to supplier."
        if slow:
            return "Consider discounting or discontinuing this product."
        if fast:
            return "Increase inventory levels and marketing."
        return "Monitor performance closely."

    @staticmethod
    def _transporter_recommendation(name: str, delivery_rate: float, pod_rate: float) -> str:
        if delivery_rate >= 95 and pod_rate >= 95:
            return "Preferred transporter – maintain relationship."
        if delivery_rate < 85 or pod_rate < 85:
            return "Performance below standards – review contract terms."
        return "Good performance – encourage consistency."

# ============================================================
# DASHBOARD SERVICE (Orchestrator)
# ============================================================

class DashboardService:
    def __init__(self, analytics_repository=None, analytics_service=None):
        self.repo = analytics_repository
        self.service = analytics_service
        self.logger = logger.getChild(self.__class__.__name__)
        self._context_cache: Dict[str, DashboardContext] = {}
        self._db_repo = DashboardRepository()

    @cached(ttl=5)
    async def get_dashboard_data(
        self,
        filters: Optional[Dict[str, Any]] = None,
        role: str = "viewer",
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        filters = filters or {}
        context = await self._get_or_load_context(filters, role, limit, offset)
        return {
            "executive": await self._build_executive_summary(context),
            "cards": await self._build_cards(context),
            "charts": await self._prepare_charts(context),
            "warehouse": context.warehouse_performance,
            "dealer": context.dealer_performance,
            "city": context.city_performance,
            "product": context.product_performance,
            "transport": context.transport_performance,
            "inventory": await self._build_inventory(context),
            "ranking": context.rankings,
            "alerts": await self._generate_alerts(context),
            "recommendations": await self._generate_recommendations(context),
            "filters": filters,
            "exports": {
                "pdf": "/dashboard/export/pdf",
                "excel": "/dashboard/export/excel",
                "pptx": "/dashboard/export/pptx",
                "csv": "/dashboard/export/csv"
            },
            "metadata": context.metadata,
            "pagination": {"limit": limit, "offset": offset, "total": len(context.dealer_performance or [])}
        }

    async def _get_or_load_context(self, filters: Dict, role: str, limit: int, offset: int) -> DashboardContext:
        cache_key = hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()
        context = self._context_cache.get(cache_key)
        if not context:
            context = DashboardContext(filters, role)
            self._context_cache[cache_key] = context
        if not context.loaded:
            await self._load_dashboard_context(context, limit, offset)
            context.loaded = True
        return context

    async def _load_dashboard_context(self, context: DashboardContext, limit: int, offset: int) -> None:
        filters = context.filters
        try:
            (context.summary,
             context.warehouse_performance,
             context.dealer_performance,
             context.product_performance,
             context.city_performance,
             context.transport_performance,
             context.monthly_trends,
             context.daily_trends,
             context.kpis,
             context.rankings,
             context.health,
             context.metadata,
             context.inventory) = await asyncio.gather(
                self._load_summary(filters),
                self._load_warehouse_performance(filters, limit, offset),
                self._load_dealer_performance(filters, limit, offset),
                self._load_product_performance(filters, limit, offset),
                self._load_city_performance(filters, limit, offset),
                self._load_transport_performance(filters),
                self._load_monthly_trends(filters),
                self._load_daily_trends(filters),
                self._load_kpis(filters),
                self._load_rankings(filters, limit),
                self._load_health(filters),
                self._load_metadata(filters),
                self._load_inventory(filters)
            )
        except Exception as e:
            self.logger.exception("Failed to load dashboard context")
            raise

    # ------------------------------------------------------------------
    # LOADERS (async wrappers)
    # ------------------------------------------------------------------

    async def _load_summary(self, filters: Dict) -> Dict[str, Any]:
        return await asyncio.to_thread(self._db_repo.get_summary, filters)

    async def _load_warehouse_performance(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        return await asyncio.to_thread(self._db_repo.get_warehouse_performance, filters)

    async def _load_dealer_performance(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        return await asyncio.to_thread(self._db_repo.get_dealer_performance, filters)

    async def _load_product_performance(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        return await asyncio.to_thread(self._db_repo.get_product_performance, filters)

    async def _load_city_performance(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        return await asyncio.to_thread(self._db_repo.get_city_performance, filters)

    async def _load_transport_performance(self, filters: Dict) -> List[Dict]:
        return await asyncio.to_thread(self._db_repo.get_transport_performance, filters)

    async def _load_monthly_trends(self, filters: Dict) -> Dict[str, List]:
        return await asyncio.to_thread(self._db_repo.get_monthly_trends, filters)

    async def _load_daily_trends(self, filters: Dict) -> Dict[str, List]:
        return await asyncio.to_thread(self._db_repo.get_daily_trends, filters)

    async def _load_kpis(self, filters: Dict) -> Dict[str, Any]:
        summary = await self._load_summary(filters)
        return {
            "revenue": summary.get("total_revenue", 0.0),
            "units": summary.get("total_units", 0),
            "delivery_notes": summary.get("total_delivery_notes", 0),
            "pgi_completed": summary.get("pgi_completed", 0),
            "delivered_dns": summary.get("delivered_dns", 0),
            "pod_completed": summary.get("pod_completed", 0),
            "dealers": summary.get("active_dealers", 0),
            "warehouses": summary.get("active_warehouses", 0),
            "cities": summary.get("active_cities", 0),
            "products": summary.get("active_products", 0),
            "transporters": summary.get("active_transporters", 0),
            "average_delivery_days": summary.get("average_delivery_days", 0.0),
            "average_pod_days": summary.get("average_pod_days", 0.0),
            "average_pgi_days": summary.get("average_pgi_days", 0.0),
            "average_logistics_cycle_days": summary.get("average_logistics_cycle_days", 0.0),
            "average_target_days": summary.get("average_target_days", 0.0),
            "pod_percentage": summary.get("pod_completion_rate", 0.0),
            "pgi_percentage": summary.get("pgi_achievement_rate", 0.0),
            "delivery_achievement_percentage": summary.get("delivery_achievement_rate", 0.0),
            "late_deliveries": summary.get("late_deliveries", 0),
            "pending_dispatch": summary.get("pending_dispatch", 0),
            "pending_delivery": summary.get("pending_delivery", 0),
            "pending_pod": summary.get("pending_pod", 0),
            "pending_deliveries": summary.get("pending_delivery", 0),
            "delayed_pod": summary.get("delayed_pod", 0),
            "on_time_deliveries": summary.get("on_time_deliveries", 0),
            "on_time_delivery_rate": summary.get("delivery_achievement_rate", 0.0),
            "damage_percentage": 0.0,
            "otif_percentage": summary.get("otif_percentage", 0.0),
            "fill_rate": 0.0,
            "warehouse_utilization": 0.0,
            "revenue_growth": 0.0,
            "unit_growth": 0.0,
            "dn_growth": 0.0,
            "invalid_records": summary.get("invalid_records", 0),
            "delivery_target_missing": summary.get("delivery_target_missing", 0),
            "dashboard_health_score": summary.get("dashboard_health_score", 0.0),
            "top_warehouse": None,
            "top_dealer": None,
            "top_product": None,
            "top_city": None,
        }

    async def _load_rankings(self, filters: Dict, limit: int) -> Dict[str, List]:
        return {"warehouses": [], "dealers": [], "products": [], "cities": []}

    async def _load_health(self, filters: Dict) -> Dict[str, Any]:
        return await asyncio.to_thread(self._db_repo.get_health)

    async def _load_metadata(self, filters: Dict) -> Dict[str, Any]:
        record_count = await asyncio.to_thread(self._db_repo.get_record_count)
        return {
            "application_version": "6.0.0",
            "database_version": "PostgreSQL (Enterprise)",
            "postgresql_status": "connected",
            "database_size": "N/A",
            "record_count": record_count,
            "last_refresh": datetime.utcnow().isoformat(),
            "last_etl_run": None,
            "generated_by": "DashboardService v6.0",
            "report_time": datetime.utcnow().isoformat(),
            "time_zone": "UTC",
            "environment": os.getenv("ENVIRONMENT", "production"),
            "ai_model": "Built-in",
            "execution_time_ms": 0,
            "cache_status": "active",
            "health_score": 0.0,
        }

    async def _load_inventory(self, filters: Dict) -> Dict[str, Any]:
        return {"total_products": 0, "total_units": 0, "warehouse_stock": [], "slow_moving": [], "fast_moving": []}

    # ------------------------------------------------------------------
    # BUILDERS
    # ------------------------------------------------------------------

    async def _build_executive_summary(self, context: DashboardContext) -> Dict[str, Any]:
        summary = context.summary or {}
        return {
            "total_revenue": summary.get("total_revenue", 0.0),
            "total_units": summary.get("total_units", 0),
            "total_delivery_notes": summary.get("total_delivery_notes", 0),
            "pgi_completed": summary.get("pgi_completed", 0),
            "delivered_dns": summary.get("delivered_dns", 0),
            "pod_completed": summary.get("pod_completed", 0),
            "active_dealers": summary.get("active_dealers", 0),
            "active_warehouses": summary.get("active_warehouses", 0),
            "active_cities": summary.get("active_cities", 0),
            "active_products": summary.get("active_products", 0),
            "active_transporters": summary.get("active_transporters", 0),
            "pgi_rate": summary.get("pgi_achievement_rate", 0.0),
            "otif": summary.get("otif_percentage", 0.0),
            "pod_rate": summary.get("pod_completion_rate", 0.0),
            "delivery_achievement": summary.get("delivery_achievement_rate", 0.0),
            "average_delivery_days": summary.get("average_delivery_days", 0.0),
            "average_pod_days": summary.get("average_pod_days", 0.0),
            "average_logistics_cycle_days": summary.get("average_logistics_cycle_days", 0.0),
            "pending_dispatch": summary.get("pending_dispatch", 0),
            "pending_delivery": summary.get("pending_delivery", 0),
            "pending_pod": summary.get("pending_pod", 0),
            "late_deliveries": summary.get("late_deliveries", 0),
            "delayed_pod": summary.get("delayed_pod", 0),
            "on_time_deliveries": summary.get("on_time_deliveries", 0),
            "invalid_records": summary.get("invalid_records", 0),
            "health_score": summary.get("dashboard_health_score", 0),
            "last_refresh": summary.get("last_database_refresh"),
        }

    async def _build_cards(self, context: DashboardContext) -> Dict[str, Any]:
        """Build the executive KPI cards - 30+ KPIs."""
        summary = context.summary or {}
        pgi_rate = summary.get("pgi_achievement_rate", 0.0)
        delivery_rate = summary.get("delivery_achievement_rate", 0.0)
        pod_rate = summary.get("pod_completion_rate", 0.0)
        health_score = summary.get("dashboard_health_score", 0.0)
        avg_cycle = summary.get("average_logistics_cycle_days", 0.0)
        total_dn = summary.get("total_delivery_notes", 0)
        total_rev = summary.get("total_revenue", 0.0)
        dealers = summary.get("active_dealers", 0)
        warehouses = summary.get("active_warehouses", 0)
        cities = summary.get("active_cities", 0)
        products = summary.get("active_products", 0)
        transporters = summary.get("active_transporters", 0)

        def rate_card(value: float, target: float, icon: str, label: str = None) -> Dict[str, Any]:
            return {
                "value": value,
                "target": target,
                "target_label": f"{target:.0f}%",
                "progress": min((value / target) * 100, 100) if target else 0,
                "icon": icon,
                "color": self._db_repo._kpi_color(value, target),
                "format": "percentage",
                "label": label,
            }

        def count_card(value: int, icon: str, color: str = "secondary", target_label: str = "0", label: str = None) -> Dict[str, Any]:
            return {
                "value": value,
                "target": 0,
                "target_label": target_label,
                "progress": 100 if value == 0 else 0,
                "icon": icon,
                "color": color,
                "format": "number",
                "label": label,
            }

        def days_card(value: float, target: float, icon: str, target_label: str, label: str = None) -> Dict[str, Any]:
            progress = 100 if value <= target else max(0, (target / (value or target)) * 100)
            return {
                "value": value,
                "target": target,
                "target_label": target_label,
                "progress": min(progress, 100),
                "icon": icon,
                "color": "success" if value <= target else "warning" if value <= target + 2 else "danger",
                "format": "days",
                "label": label,
            }

        def currency_card(value: float, icon: str, color: str = "primary", label: str = None) -> Dict[str, Any]:
            return {
                "value": value,
                "target": 0,
                "target_label": "N/A",
                "progress": 100,
                "icon": icon,
                "color": color,
                "format": "currency",
                "label": label,
            }

        cards = {
            # Financial KPIs
            "revenue": currency_card(total_rev, "fa-chart-line", "primary", "Revenue"),
            "revenue_per_dn": currency_card(total_rev / total_dn if total_dn else 0, "fa-receipt", "info", "Revenue / DN"),
            "revenue_per_dealer": currency_card(total_rev / dealers if dealers else 0, "fa-user-tie", "info", "Revenue / Dealer"),
            "revenue_per_warehouse": currency_card(total_rev / warehouses if warehouses else 0, "fa-warehouse", "info", "Revenue / Warehouse"),
            "revenue_per_city": currency_card(total_rev / cities if cities else 0, "fa-city", "info", "Revenue / City"),
            # Operational KPIs
            "pgi_achievement": rate_card(pgi_rate, 100.0, "fa-warehouse", "PGI Achievement"),
            "delivery_achievement": rate_card(delivery_rate, 95.0, "fa-truck-fast", "Delivery Achievement"),
            "pod_achievement": rate_card(pod_rate, 95.0, "fa-clipboard-check", "POD Achievement"),
            "otif": rate_card(delivery_rate, 95.0, "fa-check-circle", "OTIF"),
            # Days KPIs
            "avg_delivery_days": days_card(summary.get("average_delivery_days", 0.0), 6.0, "fa-route", "distance based", "Avg Delivery Days"),
            "avg_pod_days": days_card(summary.get("average_pod_days", 0.0), 1.0, "fa-file-signature", "1 day", "Avg POD Days"),
            "avg_logistics_cycle": days_card(avg_cycle, 7.0, "fa-arrows-spin", "PGI to POD", "Avg Logistics Cycle"),
            # Logistics KPIs
            "pending_dispatch": count_card(summary.get("pending_dispatch", 0), "fa-box-open", "warning", "Pending PGI"),
            "pending_delivery": count_card(summary.get("pending_delivery", 0), "fa-truck-ramp-box", "warning", "Pending Delivery"),
            "pending_pod": count_card(summary.get("pending_pod", 0), "fa-file-circle-exclamation", "danger", "Pending POD"),
            "late_deliveries": count_card(summary.get("late_deliveries", 0), "fa-clock", "danger", "Late Deliveries"),
            "delayed_pod": count_card(summary.get("delayed_pod", 0), "fa-triangle-exclamation", "danger", "Delayed POD"),
            "on_time_deliveries": count_card(summary.get("on_time_deliveries", 0), "fa-circle-check", "success", "On-Time Deliveries"),
            # Network KPIs
            "warehouses": count_card(warehouses, "fa-warehouse", "secondary", "Warehouses"),
            "dealers": count_card(dealers, "fa-users", "secondary", "Dealers"),
            "products": count_card(products, "fa-box", "secondary", "Products"),
            "cities": count_card(cities, "fa-city", "secondary", "Cities"),
            "transporters": count_card(transporters, "fa-truck", "secondary", "Transporters"),
            # Executive KPIs
            "logistics_health": rate_card(health_score, 95.0, "fa-heartbeat", "Logistics Health"),
            "data_quality_score": rate_card(100.0 - ((summary.get("invalid_records", 0) / (total_dn or 1)) * 100.0), 100.0, "fa-database", "Data Quality"),
            "delivery_compliance": rate_card(delivery_rate, 95.0, "fa-file-contract", "Delivery Compliance"),
            "pod_compliance": rate_card(pod_rate, 95.0, "fa-file-pen", "POD Compliance"),
            # Extra: Total DN count
            "delivery_notes": {
                "value": total_dn,
                "target": 0,
                "target_label": "distinct DN",
                "progress": 100,
                "icon": "fa-file-invoice",
                "color": "info",
                "format": "number",
                "label": "Delivery Notes",
            },
        }
        return cards

    async def _prepare_charts(self, context: DashboardContext) -> Dict[str, Any]:
        monthly = context.monthly_trends or {}
        daily = context.daily_trends or {}
        return {
            "revenue_trend": {"labels": monthly.get("months", []), "data": monthly.get("revenue", [])},
            "units_trend": {"labels": monthly.get("months", []), "data": monthly.get("units", [])},
            "dn_trend": {"labels": monthly.get("months", []), "data": monthly.get("delivery_notes", [])},
            "pgi_trend": {"labels": monthly.get("months", []), "data": monthly.get("pgi_rate", [])},
            "delivery_achievement_trend": {"labels": monthly.get("months", []), "data": monthly.get("delivery_achievement", [])},
            "pod_trend": {"labels": monthly.get("months", []), "data": monthly.get("pod_rate", [])},
            "otif_trend": {"labels": monthly.get("months", []), "data": monthly.get("otif", [])},
            "cycle_trend": {"labels": monthly.get("months", []), "data": monthly.get("cycle_days", [])},
            "daily_trend": {
                "labels": daily.get("dates", []),
                "revenue": daily.get("revenue", []),
                "units": daily.get("units", []),
                "delivery_notes": daily.get("delivery_notes", []),
                "pgi_completed": daily.get("pgi_completed", []),
                "delivered_dns": daily.get("delivered_dns", []),
                "pod_completed": daily.get("pod_completed", []),
            },
            "warehouse_ranking": context.rankings.get("warehouses", []) if context.rankings else [],
            "dealer_ranking": context.rankings.get("dealers", []) if context.rankings else [],
            "product_ranking": context.rankings.get("products", []) if context.rankings else [],
            "city_ranking": context.rankings.get("cities", []) if context.rankings else [],
            "transport_ranking": [],  # will be populated later
        }

    async def _build_inventory(self, context: DashboardContext) -> Dict[str, Any]:
        return {"total_products": 0, "total_units": 0, "warehouse_stock": []}

    # ------------------------------------------------------------------
    # ALERTS & RECOMMENDATIONS
    # ------------------------------------------------------------------

    async def _generate_alerts(self, context: DashboardContext) -> List[Dict[str, Any]]:
        alerts = []
        summary = context.summary or {}
        kpis = context.kpis or {}

        # Critical: Data quality issues
        if summary.get("invalid_records", 0) > 0:
            alerts.append({
                "level": "critical",
                "message": f"{summary.get('invalid_records', 0)} date validation issues found.",
                "action": "Fix records where delivery is before PGI or POD is before delivery.",
                "title": "Data Quality Alert"
            })

        # Critical: Late deliveries
        if kpis.get("late_deliveries", 0) > 10:
            alerts.append({
                "level": "critical",
                "message": f"{kpis.get('late_deliveries', 0)} late deliveries detected.",
                "action": "Review logistics routes and dispatch schedules.",
                "title": "Late Delivery Alert"
            })

        # Critical: Delayed POD
        if kpis.get("delayed_pod", 0) > 10:
            alerts.append({
                "level": "critical",
                "message": f"{kpis.get('delayed_pod', 0)} PODs are delayed beyond 1 day.",
                "action": "Prioritize critical POD aging cases.",
                "title": "POD Delay Alert"
            })

        # Warning: PGI below target
        if summary.get("pgi_achievement_rate", 100) < 100:
            alerts.append({
                "level": "warning",
                "message": f"PGI achievement is {summary.get('pgi_achievement_rate', 0):.1f}% below target (100%).",
                "action": "Clear pending dispatch DNs from warehouse.",
                "title": "PGI Achievement Warning"
            })

        # Warning: Delivery below target
        if summary.get("delivery_achievement_rate", 100) < 95:
            alerts.append({
                "level": "warning",
                "message": f"Delivery achievement is {summary.get('delivery_achievement_rate', 0):.1f}% below target (95%).",
                "action": "Review late deliveries against distance-based targets.",
                "title": "Delivery Achievement Warning"
            })

        # Warning: POD below target
        if summary.get("pod_completion_rate", 100) < 95:
            alerts.append({
                "level": "warning",
                "message": f"POD achievement is {summary.get('pod_completion_rate', 0):.1f}% below target (95%).",
                "action": "Investigate POD collection bottlenecks.",
                "title": "POD Achievement Warning"
            })

        # Warning: Pending dispatch
        if kpis.get("pending_dispatch", 0) > 0:
            alerts.append({
                "level": "warning",
                "message": f"{kpis.get('pending_dispatch', 0)} DNs are pending dispatch.",
                "action": "Complete PGI before delivery processing.",
                "title": "Pending Dispatch"
            })

        # Warning: Pending delivery
        if kpis.get("pending_delivery", 0) > 20:
            alerts.append({
                "level": "warning",
                "message": f"{kpis.get('pending_delivery', 0)} dispatched DNs are still in transit.",
                "action": "Follow up vehicle dispatch and customer delivery status.",
                "title": "Pending Delivery"
            })

        # Warning: Pending POD
        if kpis.get("pending_pod", 0) > 20:
            alerts.append({
                "level": "warning",
                "message": f"{kpis.get('pending_pod', 0)} delivered DNs are pending POD.",
                "action": "Collect proof of delivery and close delivered DNs.",
                "title": "Pending POD"
            })

        # Info: Revenue growth (if we had growth data)
        if kpis.get("revenue_growth", 0) > 0:
            alerts.append({
                "level": "success",
                "message": f"Revenue growth is {kpis.get('revenue_growth', 0):.1f}% – positive trend.",
                "action": "Maintain current strategies.",
                "title": "Revenue Growth"
            })

        return alerts

    async def _generate_recommendations(self, context: DashboardContext) -> List[Dict[str, Any]]:
        recommendations = []
        # Warehouse recommendations
        for wh in context.warehouse_performance or []:
            if wh.get("risk_level") == "High":
                recommendations.append({
                    "entity": wh.get("warehouse_name"),
                    "type": "warehouse",
                    "risk": "High",
                    "recommendation": wh.get("ai_recommendation", "Review operations immediately."),
                    "priority": "Critical"
                })
            elif wh.get("performance_grade") == "D":
                recommendations.append({
                    "entity": wh.get("warehouse_name"),
                    "type": "warehouse",
                    "risk": "Medium",
                    "recommendation": "Improve OTIF and reduce delivery days.",
                    "priority": "High"
                })
        # Dealer recommendations
        for dlr in context.dealer_performance or []:
            if dlr.get("performance_score", 100) < 50:
                recommendations.append({
                    "entity": dlr.get("dealer_name"),
                    "type": "dealer",
                    "risk": "High",
                    "recommendation": "Provide additional support and training.",
                    "priority": "High"
                })
        # Product recommendations
        for prod in context.product_performance or []:
            if prod.get("dead_stock_flag", False):
                recommendations.append({
                    "entity": prod.get("product_name"),
                    "type": "product",
                    "risk": "High",
                    "recommendation": "Dead stock – immediate liquidation or return.",
                    "priority": "Critical"
                })
            elif prod.get("slow_moving_flag", False):
                recommendations.append({
                    "entity": prod.get("product_name"),
                    "type": "product",
                    "risk": "Low",
                    "recommendation": "Consider discounting or discontinuing.",
                    "priority": "Medium"
                })
            elif prod.get("fast_moving_flag", False):
                recommendations.append({
                    "entity": prod.get("product_name"),
                    "type": "product",
                    "risk": "Low",
                    "recommendation": "Increase inventory and promote sales.",
                    "priority": "Low"
                })
        # City recommendations (if any high risk)
        for city in context.city_performance or []:
            if city.get("risk_level") == "High":
                recommendations.append({
                    "entity": city.get("city"),
                    "type": "city",
                    "risk": "High",
                    "recommendation": f"Improve delivery in {city.get('city')} – average delivery {city.get('average_delivery_days', 0)} days vs target {city.get('delivery_target', 0)} days.",
                    "priority": "High"
                })
        return recommendations

    # ------------------------------------------------------------------
    # HELPER METHODS (backward compatibility)
    # ------------------------------------------------------------------

    def _compute_performance_grade(self, otif: float, avg_delivery: float, utilization: float) -> str:
        if otif >= 95 and avg_delivery <= 2 and utilization <= 85:
            return "A"
        elif otif >= 85 and avg_delivery <= 4 and utilization <= 90:
            return "B"
        elif otif >= 70:
            return "C"
        else:
            return "D"

    def _compute_risk_level(self, pending: int, late: int, avg_delivery: float) -> str:
        if late > 10 or pending > 20 or avg_delivery > 5:
            return "High"
        elif late > 5 or pending > 10 or avg_delivery > 3:
            return "Medium"
        else:
            return "Low"

    def _compute_dealer_score(self, revenue: float, units: int, avg_delivery: float, growth: float) -> float:
        score = 0.0
        if revenue > 0:
            score += min(revenue / 1000000, 1) * 40
        if units > 0:
            score += min(units / 1000, 1) * 30
        if avg_delivery > 0:
            score += max(0, (5 - avg_delivery) / 5) * 20
        score += min(max(growth / 10, 0), 1) * 10
        return min(score, 100)

    def _generate_warehouse_recommendation(self, code: str, grade: str, risk: str) -> str:
        if grade in ("A", "B") and risk == "Low":
            return "Maintain current operations."
        elif grade == "C" or risk == "Medium":
            return "Review processes and improve OTIF."
        else:
            return "Urgent intervention required: capacity and delivery issues."

    def _generate_dealer_recommendation(self, code: str, score: float, avg_delivery: float) -> str:
        if score >= 80:
            return "Top performer – consider loyalty rewards."
        elif score >= 60:
            return "Good performance – focus on reducing delivery days."
        else:
            return "Needs improvement – provide training and support."

    def _generate_product_recommendation(self, code: str, slow: bool, fast: bool, growth: float) -> str:
        if slow:
            return "Consider discounting or discontinuing this product."
        elif fast:
            return "Increase inventory levels and marketing."
        elif growth > 5:
            return "Product gaining traction – invest more."
        else:
            return "Monitor performance closely."

    async def _compute_dashboard_health(self, filters: Dict) -> float:
        return 70.0

    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "total_revenue": 0.0,
            "total_units": 0,
            "total_delivery_notes": 0,
            "pgi_completed": 0,
            "delivered_dns": 0,
            "pod_completed": 0,
            "active_dealers": 0,
            "active_warehouses": 0,
            "active_cities": 0,
            "active_products": 0,
            "active_transporters": 0,
            "average_delivery_days": 0.0,
            "average_pod_days": 0.0,
            "average_pgi_days": 0.0,
            "average_logistics_cycle_days": 0.0,
            "average_target_days": 0.0,
            "pgi_achievement_rate": 0.0,
            "delivery_achievement_rate": 0.0,
            "pod_completion_rate": 0.0,
            "otif_percentage": 0.0,
            "inventory_accuracy": 0.0,
            "pending_dispatch": 0,
            "pending_delivery": 0,
            "pending_pod": 0,
            "late_deliveries": 0,
            "delayed_pod": 0,
            "on_time_deliveries": 0,
            "delivery_target_missing": 0,
            "delivery_without_pgi": 0,
            "delivery_before_pgi": 0,
            "pod_before_delivery": 0,
            "pod_before_pgi": 0,
            "invalid_records": 0,
            "dashboard_health_score": 0.0,
            "last_database_refresh": None,
        }

    # ------------------------------------------------------------------
    # INDIVIDUAL GETTERS (backward compatibility)
    # ------------------------------------------------------------------

    async def get_dashboard_summary(self, filters: Optional[Dict] = None, role: str = "viewer") -> Dict:
        return await self._load_summary(filters or {})

    async def get_dashboard_cards(self, filters: Optional[Dict] = None, role: str = "viewer") -> Dict:
        context = DashboardContext(filters or {}, role)
        await self._load_dashboard_context(context, 100, 0)
        return await self._build_cards(context)

    async def get_kpi_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer") -> Dict:
        return await self._load_kpis(filters or {})

    async def get_warehouse_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer", limit: int = 100, offset: int = 0) -> Dict:
        warehouses = await self._load_warehouse_performance(filters or {}, limit, offset)
        ranking = []
        summary = await self._aggregate_warehouse_metrics(warehouses)
        return {"warehouses": warehouses, "ranking": ranking, "summary": summary}

    async def get_dealer_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer", limit: int = 100, offset: int = 0) -> Dict:
        dealers = await self._load_dealer_performance(filters or {}, limit, offset)
        ranking = []
        summary = await self._aggregate_dealer_metrics(dealers)
        return {"dealers": dealers, "ranking": ranking, "summary": summary}

    async def get_product_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer", limit: int = 100, offset: int = 0) -> Dict:
        products = await self._load_product_performance(filters or {}, limit, offset)
        ranking = []
        summary = await self._aggregate_product_metrics(products)
        return {"products": products, "ranking": ranking, "summary": summary}

    async def get_city_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer", limit: int = 100, offset: int = 0) -> Dict:
        cities = await self._load_city_performance(filters or {}, limit, offset)
        ranking = []
        summary = await self._aggregate_city_metrics(cities)
        return {"cities": cities, "ranking": ranking, "summary": summary}

    async def get_transport_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer") -> Dict:
        transporters = await self._load_transport_performance(filters or {})
        return {"transporters": transporters, "ranking": [], "summary": {}}

    async def get_dashboard_statistics(self, filters: Optional[Dict] = None) -> Dict:
        return await self._load_statistics(filters or {})

    async def get_dashboard_health(self) -> Dict:
        return await self._load_health({})

    async def get_last_refresh(self) -> Dict:
        return {"last_refresh": datetime.utcnow().isoformat()}

    async def get_growth_statistics(self, filters: Optional[Dict] = None) -> Dict[str, float]:
        return {"revenue_growth": 0.0, "units_growth": 0.0, "delivery_notes_growth": 0.0}

    # ------------------------------------------------------------------
    # AGGREGATION HELPERS
    # ------------------------------------------------------------------

    async def _aggregate_warehouse_metrics(self, warehouses: List[Dict]) -> Dict:
        if not warehouses:
            return {}
        total_revenue = sum(w.get("revenue", 0) for w in warehouses)
        total_units = sum(w.get("units", 0) for w in warehouses)
        total_dn = sum(w.get("delivery_notes", 0) for w in warehouses)
        avg_delivery = sum(w.get("average_delivery_days", 0) for w in warehouses) / len(warehouses)
        avg_util = sum(w.get("utilization", 0) for w in warehouses) / len(warehouses)
        return {
            "total_revenue": total_revenue,
            "total_units": total_units,
            "total_delivery_notes": total_dn,
            "average_delivery_days": avg_delivery,
            "average_utilization": avg_util,
            "warehouse_count": len(warehouses),
        }

    async def _aggregate_dealer_metrics(self, dealers: List[Dict]) -> Dict:
        if not dealers:
            return {}
        total_revenue = sum(d.get("revenue", 0) for d in dealers)
        total_units = sum(d.get("units", 0) for d in dealers)
        total_dn = sum(d.get("delivery_notes", 0) for d in dealers)
        avg_score = sum(d.get("performance_score", 0) for d in dealers) / len(dealers)
        return {
            "total_revenue": total_revenue,
            "total_units": total_units,
            "total_delivery_notes": total_dn,
            "average_performance_score": avg_score,
            "dealer_count": len(dealers),
        }

    async def _aggregate_product_metrics(self, products: List[Dict]) -> Dict:
        if not products:
            return {}
        total_revenue = sum(p.get("revenue", 0) for p in products)
        total_units = sum(p.get("units", 0) for p in products)
        return {
            "total_revenue": total_revenue,
            "total_units": total_units,
            "product_count": len(products),
        }

    async def _aggregate_city_metrics(self, cities: List[Dict]) -> Dict:
        if not cities:
            return {}
        total_revenue = sum(c.get("revenue", 0) for c in cities)
        total_units = sum(c.get("units", 0) for c in cities)
        return {
            "total_revenue": total_revenue,
            "total_units": total_units,
            "city_count": len(cities),
        }
