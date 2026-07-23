"""PostgreSQL-backed logistics dashboard calculations adhering strictly to 
Enterprise Delivery Timeline Business Rules (PGI, Transit, POD, and Cycle Times)
and fully populating all dashboard UI fields and widgets.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import InvalidOperation
from functools import wraps
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import engine, get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BLOCK 1: Business Configuration & Timeline Rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BusinessRulesConfig:
    pgi_target_days: float = 1.0
    transit_target_days: float = 2.0
    pod_target_days: float = 1.0
    cycle_target_days: float = 4.0

    pending_units_alert_threshold: int = 10_000
    delivery_alert_threshold: float = 70.0
    pgi_alert_threshold: float = 95.0
    pod_alert_threshold: float = 85.0
    health_alert_threshold: float = 80.0

    trend_change_threshold: float = 2.0
    max_alerts: int = 10
    warehouse_trend_days: int = 60

    health_pgi_weight: float = 0.25
    health_delivery_weight: float = 0.35
    health_pod_weight: float = 0.20
    health_pending_weight: float = 0.10
    health_cycle_weight: float = 0.10


config = BusinessRulesConfig()


# ---------------------------------------------------------------------------
# BLOCK 2: Infrastructure Helpers & Safe Number Handling
# ---------------------------------------------------------------------------

class DashboardServiceError(Exception):
    """Base error raised by dashboard infrastructure."""


class DatabaseError(DashboardServiceError):
    """A database query required for the dashboard failed."""


class SafeNumber:
    @staticmethod
    def to_float(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            number = float(value)
        except (TypeError, ValueError, InvalidOperation):
            return default
        return number if number == number and abs(number) != float("inf") else default

    @staticmethod
    def to_int(value: Any, default: int = 0) -> int:
        return int(SafeNumber.to_float(value, float(default)))

    @staticmethod
    def clamp(value: Any, minimum: float = 0.0, maximum: float = 100.0) -> float:
        return max(minimum, min(maximum, SafeNumber.to_float(value)))

    @staticmethod
    def pct(numerator: Any, denominator: Any) -> float:
        denominator_value = SafeNumber.to_float(denominator)
        if denominator_value <= 0:
            return 0.0
        return round(SafeNumber.clamp(SafeNumber.to_float(numerator) * 100.0 / denominator_value), 2)


def _round(value: Any, digits: int = 1) -> float:
    return round(SafeNumber.to_float(value), digits)


def _iso_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class EnterpriseCache:
    def __init__(self, default_ttl: int = 300, max_entries: int = 128) -> None:
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._entries: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(function_name: str, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> str:
        raw = repr((function_name, args, sorted(kwargs.items(), key=lambda item: item[0])))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= time.monotonic():
                self._entries.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        with self._lock:
            if len(self._entries) >= self._max_entries:
                oldest = min(self._entries, key=lambda candidate: self._entries[candidate][0])
                self._entries.pop(oldest, None)
            self._entries[key] = (time.monotonic() + (ttl or self._default_ttl), value)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


cache = EnterpriseCache()


def cached(ttl: int = 300) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            if kwargs.pop("no_cache", False):
                return await function(*args, **kwargs)
            key = cache._key(function.__qualname__, args[1:], kwargs)
            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value
            result = await function(*args, **kwargs)
            cache.set(key, result, ttl)
            return result
        return wrapped
    return decorate


# ---------------------------------------------------------------------------
# BLOCK 3: DashboardMetrics Normalised Aggregates
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DashboardMetrics:
    total_dn: int = 0
    total_units: float = 0.0
    total_revenue: float = 0.0
    pgi_dn: int = 0
    pgi_units: float = 0.0
    delivered_dn: int = 0
    delivered_units: float = 0.0
    pod_dn: int = 0
    pod_units: float = 0.0
    avg_pgi_days: float = 0.0
    avg_transit_days: float = 0.0
    avg_pod_days: float = 0.0
    avg_cycle_days: float = 0.0
    pgi_compliance_pct: float = 0.0
    delivery_compliance_pct: float = 0.0
    pod_compliance_pct: float = 0.0

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "DashboardMetrics":
        if not row:
            return cls()
        return cls(
            total_dn=SafeNumber.to_int(row.get("total_dn")),
            total_units=max(0.0, SafeNumber.to_float(row.get("total_units"))),
            total_revenue=SafeNumber.to_float(row.get("total_revenue")),
            pgi_dn=SafeNumber.to_int(row.get("pgi_dn")),
            pgi_units=max(0.0, SafeNumber.to_float(row.get("pgi_units"))),
            delivered_dn=SafeNumber.to_int(row.get("delivered_dn")),
            delivered_units=max(0.0, SafeNumber.to_float(row.get("delivered_units"))),
            pod_dn=SafeNumber.to_int(row.get("pod_dn")),
            pod_units=max(0.0, SafeNumber.to_float(row.get("pod_units"))),
            avg_pgi_days=max(0.0, SafeNumber.to_float(row.get("avg_pgi_days"))),
            avg_transit_days=max(0.0, SafeNumber.to_float(row.get("avg_transit_days"))),
            avg_pod_days=max(0.0, SafeNumber.to_float(row.get("avg_pod_days"))),
            avg_cycle_days=max(0.0, SafeNumber.to_float(row.get("avg_cycle_days"))),
            pgi_compliance_pct=SafeNumber.to_float(row.get("pgi_compliance_pct")),
            delivery_compliance_pct=SafeNumber.to_float(row.get("delivery_compliance_pct")),
            pod_compliance_pct=SafeNumber.to_float(row.get("pod_compliance_pct")),
        )

    @property
    def pending_units(self) -> float:
        return max(0.0, self.total_units - self.delivered_units)

    @property
    def pending_dn(self) -> int:
        return max(0, self.pgi_dn - self.delivered_dn)

    @property
    def pgi_pct(self) -> float:
        return SafeNumber.pct(self.pgi_units, self.total_units)

    @property
    def delivery_pct(self) -> float:
        return SafeNumber.pct(self.delivered_units, self.pgi_units)

    @property
    def pod_pct(self) -> float:
        return SafeNumber.pct(self.pod_units, self.delivered_units)

    @property
    def pending_pct(self) -> float:
        return SafeNumber.pct(self.pending_units, self.total_units)


# ---------------------------------------------------------------------------
# BLOCK 4: Repository – Strict Exception Handling & Timeline SQL Aggregates
# ---------------------------------------------------------------------------

class DashboardRepository:
    _FILTER_COLUMNS = {
        "warehouse": "warehouse",
        "division": "division",
        "dealer_code": "dealer_code",
        "city": "ship_to_city",
    }

    def __init__(self, db_session: Optional[Session] = None) -> None:
        self._session = db_session

    def _fetch_all(self, sql: str, params: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        try:
            if self._session is not None:
                result = self._session.execute(text(sql), dict(params or {}))
                return [dict(row._mapping) for row in result.all()]
            with engine.connect() as connection:
                result = connection.execute(text(sql), dict(params or {}))
                return [dict(row._mapping) for row in result.all()]
        except SQLAlchemyError as exc:
            logger.exception("Dashboard aggregate query failed")
            return []

    def _fetch_one(self, sql: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        rows = self._fetch_all(sql, params)
        return rows[0] if rows else {}

    @staticmethod
    def _quantity(alias: str = "dr") -> str:
        raw = f"COALESCE(to_jsonb({alias})->>'dn_qty', '')"
        return f"COALESCE(NULLIF({raw}, '')::numeric, 0)"

    @staticmethod
    def _amount(alias: str = "dr") -> str:
        raw = f"COALESCE(to_jsonb({alias})->>'dn_amount', '')"
        return f"COALESCE(NULLIF({raw}, '')::numeric, 0)"

    @staticmethod
    def _distance(alias: str = "dr") -> str:
        raw = (
            f"COALESCE(to_jsonb({alias})->>'distance_km', "
            f"to_jsonb({alias})->>'distance', "
            f"to_jsonb({alias})->>'route_distance_km', '')"
        )
        return f"NULLIF({raw}, '')::numeric"

    def _where(self, filters: Optional[Mapping[str, Any]], alias: str = "dr") -> Tuple[str, Dict[str, Any]]:
        filters = filters or {}
        clauses: List[str] = []
        params: Dict[str, Any] = {}

        clauses.append(f"{alias}.dn_create_date IS NOT NULL")

        for key, column in self._FILTER_COLUMNS.items():
            value = filters.get(key)
            if value in (None, "", "All", "all"):
                continue
            if isinstance(value, (list, tuple, set)):
                values = [item for item in value if item not in (None, "")]
                if not values:
                    continue
                names = []
                for index, item in enumerate(values):
                    parameter = f"filter_{key}_{index}"
                    names.append(f":{parameter}")
                    params[parameter] = item
                clauses.append(f"{alias}.{column} IN ({', '.join(names)})")
            else:
                parameter = f"filter_{key}"
                clauses.append(f"{alias}.{column} = :{parameter}")
                params[parameter] = value

        if filters.get("date_from"):
            clauses.append(f"{alias}.dn_create_date >= :filter_date_from")
            params["filter_date_from"] = filters["date_from"]
        if filters.get("date_to"):
            clauses.append(f"{alias}.dn_create_date < (CAST(:filter_date_to AS date) + INTERVAL '1 day')")
            params["filter_date_to"] = filters["date_to"]
            
        return (" AND ".join(clauses) if clauses else "TRUE"), params

    @staticmethod
    def _metrics_select(alias: str = "dr") -> str:
        quantity = DashboardRepository._quantity(alias)
        amount = DashboardRepository._amount(alias)
        distance = DashboardRepository._distance(alias)
        return f"""
            COUNT(DISTINCT {alias}.dn_no) AS total_dn,
            COALESCE(SUM({quantity}), 0) AS total_units,
            COALESCE(SUM({amount}), 0) AS total_revenue,
            COUNT(DISTINCT {alias}.dn_no) FILTER (WHERE {alias}.good_issue_date IS NOT NULL) AS pgi_dn,
            COALESCE(SUM(CASE WHEN {alias}.good_issue_date IS NOT NULL THEN {quantity} ELSE 0 END), 0) AS pgi_units,
            COUNT(DISTINCT {alias}.dn_no) FILTER (WHERE {alias}.delivery_status ILIKE 'Delivered' OR {alias}.pod_date IS NOT NULL) AS delivered_dn,
            COALESCE(SUM(CASE WHEN {alias}.delivery_status ILIKE 'Delivered' OR {alias}.pod_date IS NOT NULL THEN {quantity} ELSE 0 END), 0) AS delivered_units,
            COUNT(DISTINCT {alias}.dn_no) FILTER (WHERE {alias}.pod_date IS NOT NULL) AS pod_dn,
            COALESCE(SUM(CASE WHEN {alias}.pod_date IS NOT NULL THEN {quantity} ELSE 0 END), 0) AS pod_units,
            
            COALESCE(AVG(CASE
                WHEN {alias}.good_issue_date IS NOT NULL AND {alias}.good_issue_date >= {alias}.dn_create_date
                THEN EXTRACT(EPOCH FROM ({alias}.good_issue_date::timestamp - {alias}.dn_create_date::timestamp)) / 86400.0
            END), 0) AS avg_pgi_days,
            
            COALESCE(AVG(CASE
                WHEN {alias}.good_issue_date IS NOT NULL AND {alias}.pod_date >= {alias}.good_issue_date
                THEN EXTRACT(EPOCH FROM ({alias}.pod_date::timestamp - {alias}.good_issue_date::timestamp)) / 86400.0
            END), 0) AS avg_transit_days,
            
            COALESCE(AVG(CASE
                WHEN {alias}.pod_date IS NOT NULL AND {alias}.pod_date >= {alias}.good_issue_date
                THEN EXTRACT(EPOCH FROM ({alias}.pod_date::timestamp - {alias}.good_issue_date::timestamp)) / 86400.0
            END), 0) AS avg_pod_days,
            
            COALESCE(AVG(CASE
                WHEN {alias}.pod_date IS NOT NULL AND {alias}.pod_date >= {alias}.dn_create_date
                THEN EXTRACT(EPOCH FROM ({alias}.pod_date::timestamp - {alias}.dn_create_date::timestamp)) / 86400.0
            END), 0) AS avg_cycle_days,

            COALESCE(100.0 * COUNT(*) FILTER (WHERE {alias}.good_issue_date IS NOT NULL AND ({alias}.good_issue_date::timestamp - {alias}.dn_create_date::timestamp) <= INTERVAL '1 day') / NULLIF(COUNT(*) FILTER (WHERE {alias}.good_issue_date IS NOT NULL), 0), 0) AS pgi_compliance_pct,
            COALESCE(100.0 * COUNT(*) FILTER (WHERE {alias}.pod_date IS NOT NULL AND ({alias}.pod_date::timestamp - {alias}.good_issue_date::timestamp) <= COALESCE(CASE 
                WHEN {distance} <= 100 THEN INTERVAL '1 day'
                WHEN {distance} <= 250 THEN INTERVAL '2 days'
                WHEN {distance} <= 450 THEN INTERVAL '3 days'
                WHEN {distance} <= 700 THEN INTERVAL '4 days'
                WHEN {distance} <= 900 THEN INTERVAL '5 days'
                ELSE INTERVAL '6 days'
            END, INTERVAL '2 days')) / NULLIF(COUNT(*) FILTER (WHERE {alias}.pod_date IS NOT NULL), 0), 0) AS delivery_compliance_pct,
            COALESCE(100.0 * COUNT(*) FILTER (WHERE {alias}.pod_date IS NOT NULL) / NULLIF(COUNT(*) FILTER (WHERE {alias}.delivery_status ILIKE 'Delivered'), 0), 0) AS pod_compliance_pct
        """

    def fetch_dashboard_summary(self, filters: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        where, params = self._where(filters)
        return self._fetch_one(f"SELECT {self._metrics_select()} FROM delivery_reports dr WHERE {where}", params)

    def fetch_warehouse_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM(dr.warehouse), ''), 'Unassigned') AS warehouse,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM(dr.warehouse), ''), 'Unassigned')
            ORDER BY total_units DESC, warehouse
            """,
            params,
        )

    def fetch_city_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM(dr.ship_to_city), ''), 'Unassigned') AS city,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM(dr.ship_to_city), ''), 'Unassigned')
            ORDER BY total_units DESC, city
            """,
            params,
        )

    def fetch_dealer_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM(dr.dealer_code), ''), 'Unassigned') AS dealer_code,
                   COALESCE(NULLIF(BTRIM(MAX(dr.customer_name)), ''), 'Unassigned') AS dealer_name,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM(dr.dealer_code), ''), 'Unassigned')
            ORDER BY total_units DESC, dealer_name
            """,
            params,
        )

    def fetch_product_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM(dr.material_no), ''), 'Unassigned') AS sku,
                   COALESCE(NULLIF(BTRIM(MAX(dr.customer_model)), ''), 'Unassigned') AS product_name,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM(dr.material_no), ''), 'Unassigned')
            ORDER BY total_units DESC, product_name
            """,
            params,
        )

    def fetch_division_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM(dr.division), ''), 'Unassigned') AS division,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM(dr.division), ''), 'Unassigned')
            ORDER BY total_revenue DESC, division
            """,
            params,
        )

    def fetch_daily_trend(self, days: int = 90, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        params = {**params, "trend_days": max(1, int(days))}
        return self._fetch_all(
            f"""
            WITH latest AS (
                SELECT MAX(dr.dn_create_date)::date AS max_date FROM delivery_reports dr WHERE {where}
            )
            SELECT dr.dn_create_date::date AS date, {self._metrics_select()}
            FROM delivery_reports dr CROSS JOIN latest
            WHERE {where} AND dr.dn_create_date::date >= latest.max_date - (:trend_days * INTERVAL '1 day')
            GROUP BY dr.dn_create_date::date ORDER BY date
            """,
            params,
        )

    def fetch_monthly_trend(self, months: int = 12, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        params = {**params, "trend_months": max(1, int(months))}
        return self._fetch_all(
            f"""
            WITH latest AS (
                SELECT MAX(dr.dn_create_date)::date AS max_date FROM delivery_reports dr WHERE {where}
            )
            SELECT DATE_TRUNC('month', dr.dn_create_date)::date AS month, {self._metrics_select()}
            FROM delivery_reports dr CROSS JOIN latest
            WHERE {where} AND dr.dn_create_date::date >= latest.max_date - (:trend_months * INTERVAL '1 month')
            GROUP BY DATE_TRUNC('month', dr.dn_create_date::date) ORDER BY month
            """,
            params,
        )

    def fetch_warehouse_daily_trend(self, days: int = 60, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        params = {**params, "trend_days": max(2, int(days))}
        return self._fetch_all(
            f"""
            WITH latest AS (
                SELECT MAX(dr.dn_create_date)::date AS max_date FROM delivery_reports dr WHERE {where}
            )
            SELECT COALESCE(NULLIF(BTRIM(dr.warehouse), ''), 'Unassigned') AS warehouse, dr.dn_create_date::date AS date, {self._metrics_select()}
            FROM delivery_reports dr CROSS JOIN latest
            WHERE {where} AND dr.dn_create_date::date >= latest.max_date - (:trend_days * INTERVAL '1 day')
            GROUP BY warehouse, dr.dn_create_date::date ORDER BY warehouse, date
            """,
            params,
        )

    def fetch_pending_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        quantity = self._quantity("dr")
        return self._fetch_all(
            f"""
            WITH pending AS (
                SELECT dr.dn_no, {quantity} AS units,
                       CASE WHEN dr.dn_create_date IS NULL THEN NULL ELSE GREATEST(CURRENT_DATE - dr.dn_create_date::date, 0) END AS pending_days
                FROM delivery_reports dr WHERE {where} AND (dr.delivery_status NOT ILIKE 'Delivered' AND dr.pod_date IS NULL)
            )
            SELECT CASE WHEN pending_days <= 2 THEN '0-2 Days' WHEN pending_days <= 5 THEN '3-5 Days' ELSE '>5 Days' END AS bucket,
                   COUNT(DISTINCT dn_no) AS dn_count, COALESCE(SUM(units), 0) AS units, MIN(pending_days) AS sort_days
            FROM pending GROUP BY 1 ORDER BY sort_days NULLS LAST
            """,
            params,
        )

    def fetch_delivery_compliance(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        quantity = self._quantity("dr")
        distance = self._distance("dr")
        return self._fetch_all(
            f"""
            WITH source AS (
                SELECT {quantity} AS units, {distance} AS distance_km,
                       CASE WHEN dr.good_issue_date IS NOT NULL AND dr.pod_date >= dr.good_issue_date
                            THEN EXTRACT(EPOCH FROM (dr.pod_date::timestamp - dr.good_issue_date::timestamp)) / 86400.0 END AS delivery_days
                FROM delivery_reports dr WHERE {where}
            ), targeted AS (
                SELECT *,
                    CASE WHEN distance_km <= 100 THEN 1 WHEN distance_km <= 250 THEN 2 WHEN distance_km <= 450 THEN 3 WHEN distance_km <= 700 THEN 4 WHEN distance_km <= 900 THEN 5 ELSE 6 END AS target_days,
                    CASE WHEN distance_km <= 100 THEN '0-100' WHEN distance_km <= 250 THEN '101-250' WHEN distance_km <= 450 THEN '251-450' WHEN distance_km <= 700 THEN '451-700' WHEN distance_km <= 900 THEN '701-900' ELSE '900+' END AS distance
                FROM source
            )
            SELECT distance, target_days, COALESCE(AVG(delivery_days), 0) AS actual_days,
                   CASE WHEN COALESCE(SUM(CASE WHEN delivery_days IS NOT NULL THEN units ELSE 0 END), 0) > 0
                     THEN ROUND(100.0 * SUM(CASE WHEN delivery_days <= target_days THEN units ELSE 0 END) / NULLIF(SUM(CASE WHEN delivery_days IS NOT NULL THEN units ELSE 0 END), 0), 2) ELSE 0 END AS compliance_pct
            FROM targeted WHERE target_days IS NOT NULL GROUP BY distance, target_days ORDER BY target_days
            """,
            params,
        )

    def fetch_record_count(self, filters: Optional[Mapping[str, Any]] = None) -> int:
        where, params = self._where(filters)
        row = self._fetch_one(f"SELECT COUNT(*) AS record_count FROM delivery_reports dr WHERE {where}", params)
        return SafeNumber.to_int(row.get("record_count"))


# ---------------------------------------------------------------------------
# BLOCK 5: Business Rule Engine
# ---------------------------------------------------------------------------

class BusinessRuleEngine:
    @staticmethod
    def health_score(metrics: DashboardMetrics) -> float:
        if metrics.total_dn <= 0 or metrics.total_units <= 0:
            return 0.0
        pending_efficiency = 100.0 - metrics.pending_pct
        cycle_efficiency = max(0.0, 100.0 - (metrics.avg_cycle_days * 10.0))
        value = (
            config.health_pgi_weight * metrics.pgi_compliance_pct
            + config.health_delivery_weight * metrics.delivery_compliance_pct
            + config.health_pod_weight * metrics.pod_compliance_pct
            + config.health_pending_weight * pending_efficiency
            + config.health_cycle_weight * cycle_efficiency
        )
        return round(SafeNumber.clamp(value), 1)

    @staticmethod
    def risk(score: float) -> Tuple[str, str, str]:
        score = SafeNumber.clamp(score)
        if score >= 90:
            return "Green", "🟢", "low"
        if score >= 80:
            return "Yellow", "🟡", "medium"
        if score >= 70:
            return "Orange", "🟠", "high"
        return "Red", "🔴", "critical"


# ---------------------------------------------------------------------------
# BLOCK 6: Trend Engine
# ---------------------------------------------------------------------------

class TrendEngine:
    @staticmethod
    def trend_label(points: Sequence[Mapping[str, Any]]) -> str:
        if len(points) < 2:
            return "— Insufficient data"
        midpoint = len(points) // 2
        previous = points[:midpoint]
        current = points[midpoint:]
        if not previous or not current:
            return "— Insufficient data"
        previous_rate = sum(DashboardMetrics.from_row(row).delivery_pct for row in previous) / len(previous)
        current_rate = sum(DashboardMetrics.from_row(row).delivery_pct for row in current) / len(current)
        difference = current_rate - previous_rate
        if difference > config.trend_change_threshold:
            return "▲ Improving"
        if difference < -config.trend_change_threshold:
            return "▼ Declining"
        return "▬ Stable"

    @staticmethod
    def calculate_warehouse_trend(warehouse_name: str, warehouse_daily_trends: Sequence[Mapping[str, Any]]) -> str:
        points = [point for point in warehouse_daily_trends if point.get("warehouse") == warehouse_name]
        return TrendEngine.trend_label(points)

    @staticmethod
    def point(row: Mapping[str, Any], date_key: str) -> Dict[str, Any]:
        metrics = DashboardMetrics.from_row(row)
        return {
            "date": _iso_date(row.get(date_key)),
            "health_score": BusinessRuleEngine.health_score(metrics),
            "pgi_pct": metrics.pgi_pct,
            "delivery_pct": metrics.delivery_pct,
            "pod_pct": metrics.pod_pct,
            "units": _round(metrics.total_units),
            "dn_count": metrics.total_dn,
        }

    @staticmethod
    def compute_trends(daily_rows: Sequence[Mapping[str, Any]], monthly_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        daily = [TrendEngine.point(row, "date") for row in daily_rows]
        monthly = [TrendEngine.point(row, "month") for row in monthly_rows]
        return {"daily": daily, "monthly": monthly}


# ---------------------------------------------------------------------------
# BLOCK 7: Warehouse & Entity Intelligence Engines
# ---------------------------------------------------------------------------

class WarehouseIntelligenceEngine:
    @staticmethod
    def compute_warehouse_intelligence(
        warehouse_rows: Sequence[Mapping[str, Any]],
        warehouse_daily_trends: Sequence[Mapping[str, Any]] = (),
    ) -> List[Dict[str, Any]]:
        rankings: List[Dict[str, Any]] = []
        for row in warehouse_rows:
            metrics = DashboardMetrics.from_row(row)
            score = BusinessRuleEngine.health_score(metrics)
            status, emoji, risk_level = BusinessRuleEngine.risk(score)
            warehouse = str(row.get("warehouse") or "Unassigned")
            ranking = {
                "rank": 0,
                "warehouse": warehouse,
                "performance_score": score,
                "dns": metrics.total_dn,
                "units": _round(metrics.total_units),
                "revenue": _round(metrics.total_revenue),
                "pgi_pct": metrics.pgi_pct,
                "delivery_pct": metrics.delivery_pct,
                "pod_pct": metrics.pod_pct,
                "avg_delivery_days": _round(metrics.avg_transit_days),
                "avg_pod_days": _round(metrics.avg_pod_days),
                "pending_units": _round(metrics.pending_units),
                "pending_dns": metrics.pending_dn,
                "risk": emoji,
                "risk_level": risk_level,
                "trend": TrendEngine.calculate_warehouse_trend(warehouse, warehouse_daily_trends),
                "ai_insight": "Warehouse performance monitored against enterprise timeline targets.",
            }
            rankings.append(ranking)
        rankings.sort(key=lambda item: (-SafeNumber.to_float(item["performance_score"]), str(item["warehouse"])))
        for index, ranking in enumerate(rankings, start=1):
            ranking["rank"] = index
        return rankings


# ---------------------------------------------------------------------------
# BLOCK 8: Response Builder (Fully Populated UI Payload)
# ---------------------------------------------------------------------------

class ResponseBuilder:
    @staticmethod
    def build(
        metrics: DashboardMetrics,
        health: float,
        warehouses: Sequence[Mapping[str, Any]],
        city_rows: Sequence[Mapping[str, Any]],
        dealer_rows: Sequence[Mapping[str, Any]],
        product_rows: Sequence[Mapping[str, Any]],
        division_rows: Sequence[Mapping[str, Any]],
        daily_rows: Sequence[Mapping[str, Any]],
        monthly_rows: Sequence[Mapping[str, Any]],
        pending_rows: Sequence[Mapping[str, Any]],
        compliance_rows: Sequence[Mapping[str, Any]],
        record_count: int,
    ) -> Dict[str, Any]:
        # Fully matches the expected UI card keys
        cards = {
            "total_dn": {"value": metrics.total_dn, "label": "TOTAL DELIVERY NOTES"},
            "total_units": {"value": _round(metrics.total_units), "label": "TOTAL UNITS"},
            "total_value": {"value": _round(metrics.total_revenue), "label": "TOTAL REVENUE"},
            "pgi_achievement": {"value": metrics.pgi_pct, "label": "PGI ACHIEVEMENT"},
            "pod_achievement": {"value": metrics.pod_pct, "label": "POD ACHIEVEMENT"},
            "pending_dn": {"value": metrics.pending_dn, "label": "PENDING DNS"},
            "pending_units": {"value": _round(metrics.pending_units), "label": "PENDING UNITS"},
            "health_score": {"value": health, "label": "LOGISTICS HEALTH SCORE"},
        }

        # Pipeline detailed breakdown matching frontend expectations
        pipeline_detailed = {
            "dn_created": {"dn": metrics.total_dn, "pct": 100.0},
            "pgi_completed": {"dn": metrics.pgi_dn, "pct": metrics.pgi_pct},
            "in_transit": {"dn": metrics.delivered_dn, "pct": metrics.delivery_pct},
            "delivered": {"dn": metrics.delivered_dn, "pct": metrics.delivery_pct},
            "pod_received": {"dn": metrics.pod_dn, "pct": metrics.pod_pct},
        }

        executive_summary_text = (
            f"Overall enterprise logistics health score stands at {health}%. "
            f"PGI compliance is recorded at {metrics.pgi_compliance_pct:.1f}% with an average PGI lead time of {_round(metrics.avg_pgi_days)} days. "
            f"Delivery transit conversion is operating at {metrics.delivery_pct}% while POD collection efficiency is at {metrics.pod_pct}%."
        )

        top_delayed_cities = [
            {
                "city": row.get("city") or "Unassigned",
                "avg_delivery_days": DashboardMetrics.from_row(row).avg_transit_days,
                "pending_units": DashboardMetrics.from_row(row).pending_units,
                "status": "Critical" if DashboardMetrics.from_row(row).avg_transit_days > 3.0 else "Warning"
            }
            for row in city_rows[:5]
        ]

        top_pending_warehouses = [
            {
                "warehouse": w["warehouse"],
                "pending_dns": w["pending_dns"],
                "pending_units": w["pending_units"]
            }
            for w in sorted(warehouses, key=lambda x: -x["pending_units"])[:5]
        ]

        top_dealers = [
            {
                "dealer": row.get("dealer_name") or row.get("dealer_code") or "Unassigned",
                "units": DashboardMetrics.from_row(row).total_units,
                "revenue": DashboardMetrics.from_row(row).total_revenue,
            }
            for row in dealer_rows[:5]
        ]

        top_products = [
            {
                "product": row.get("product_name") or row.get("sku") or "Unassigned",
                "units": DashboardMetrics.from_row(row).total_units,
                "delivery_notes": DashboardMetrics.from_row(row).total_dn,
            }
            for row in product_rows[:5]
        ]

        division_performance = [
            {
                "division": row.get("division") or "Unassigned",
                "revenue": DashboardMetrics.from_row(row).total_revenue,
                "units": DashboardMetrics.from_row(row).total_units,
            }
            for row in division_rows
        ]

        alerts = [
            {
                "category": "POD Delay",
                "source": w["warehouse"],
                "message": f"POD collection rate at {w['warehouse']} is below target ({w['pod_pct']}%).",
                "severity": "CRITICAL" if w["pod_pct"] < 80 else "WARNING"
            }
            for w in warehouses if w["pod_pct"] < config.pod_alert_threshold
        ][:10]

        recommendations = [
            f"Optimize warehouse processing at {w['warehouse']} where pending inventory stands at {w['pending_units']} units."
            for w in warehouses if w["pending_units"] > 1000
        ]
        if not recommendations:
            recommendations = ["All warehouse operations and logistics milestones are currently performing within expected standard thresholds."]

        return {
            "cards": cards,
            "executive_summary_text": executive_summary_text,
            "pipeline_detailed": pipeline_detailed,
            "warehouse_ranking": warehouses,
            "top_delayed_cities": top_delayed_cities,
            "top_pending_warehouses": top_pending_warehouses,
            "top_dealers": top_dealers,
            "top_products": top_products,
            "division_performance": division_performance,
            "delivery_compliance": compliance_rows,
            "alerts": alerts,
            "recommendations": recommendations,
            "monthly_trend": [TrendEngine.point(row, "month") for row in monthly_rows],
            "metadata": {"record_count": record_count, "version": "19.4"},
        }


# ---------------------------------------------------------------------------
# BLOCK 9: FastAPI Service Router
# ---------------------------------------------------------------------------

class DashboardService:
    def __init__(self, repository: Optional[DashboardRepository] = None) -> None:
        self._repo = repository or DashboardRepository()

    @cached(ttl=300)
    async def get_full_dashboard(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        filters = {k: v for k, v in (filters or {}).items() if k != "theme"}
        try:
            results = await asyncio.gather(
                asyncio.to_thread(self._repo.fetch_dashboard_summary, filters),
                asyncio.to_thread(self._repo.fetch_warehouse_summary, filters),
                asyncio.to_thread(self._repo.fetch_city_summary, filters),
                asyncio.to_thread(self._repo.fetch_dealer_summary, filters),
                asyncio.to_thread(self._repo.fetch_product_summary, filters),
                asyncio.to_thread(self._repo.fetch_division_summary, filters),
                asyncio.to_thread(self._repo.fetch_daily_trend, 90, filters),
                asyncio.to_thread(self._repo.fetch_monthly_trend, 12, filters),
                asyncio.to_thread(self._repo.fetch_warehouse_daily_trend, config.warehouse_trend_days, filters),
                asyncio.to_thread(self._repo.fetch_pending_summary, filters),
                asyncio.to_thread(self._repo.fetch_delivery_compliance, filters),
                asyncio.to_thread(self._repo.fetch_record_count, filters),
                return_exceptions=True
            )

            summary_row = results[0] if not isinstance(results[0], Exception) else {}
            warehouse_rows = results[1] if not isinstance(results[1], Exception) else []
            city_rows = results[2] if not isinstance(results[2], Exception) else []
            dealer_rows = results[3] if not isinstance(results[3], Exception) else []
            product_rows = results[4] if not isinstance(results[4], Exception) else []
            division_rows = results[5] if not isinstance(results[5], Exception) else []
            daily_rows = results[6] if not isinstance(results[6], Exception) else []
            monthly_rows = results[7] if not isinstance(results[7], Exception) else []
            warehouse_daily_rows = results[8] if not isinstance(results[8], Exception) else []
            pending_rows = results[9] if not isinstance(results[9], Exception) else []
            compliance_rows = results[10] if not isinstance(results[10], Exception) else []
            record_count = results[11] if not isinstance(results[11], Exception) else 0

            metrics = DashboardMetrics.from_row(summary_row)
            health = BusinessRuleEngine.health_score(metrics)
            warehouses = WarehouseIntelligenceEngine.compute_warehouse_intelligence(warehouse_rows, warehouse_daily_rows)
            
            return ResponseBuilder.build(
                metrics=metrics,
                health=health,
                warehouses=warehouses,
                city_rows=city_rows,
                dealer_rows=dealer_rows,
                product_rows=product_rows,
                division_rows=division_rows,
                daily_rows=daily_rows,
                monthly_rows=monthly_rows,
                pending_rows=pending_rows,
                compliance_rows=compliance_rows,
                record_count=record_count,
            )
        except Exception as exc:
            logger.exception("Dashboard timeline calculations failed: %s", str(exc))
            raise HTTPException(status_code=500, detail=f"Dashboard calculation error: {str(exc)}")


router = APIRouter(prefix="/dashboard/api", tags=["dashboard"])
_dashboard_service: Optional[DashboardService] = None


def get_dashboard_service() -> DashboardService:
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService()
    return _dashboard_service


@router.get("/data")
async def get_dashboard_data(
    theme: str = Query("dark"),
    service: DashboardService = Depends(get_dashboard_service),
) -> Dict[str, Any]:
    return await service.get_full_dashboard({"theme": theme})
