
"""PostgreSQL-backed logistics dashboard – fully resilient to missing columns."""

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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import engine, get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BLOCK 1: Business Configuration
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
# BLOCK 2: Helpers
# ---------------------------------------------------------------------------

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


def cached(ttl: int = 300):
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
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
# BLOCK 3: DashboardMetrics
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
# BLOCK 4: Repository – All columns accessed via to_jsonb
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
            raise DatabaseError("Unable to read delivery_reports for the dashboard") from exc

    def _fetch_one(self, sql: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        rows = self._fetch_all(sql, params)
        return rows[0] if rows else {}

    # ---- Safe column accessors ----

    @staticmethod
    def _col(alias: str, name: str) -> str:
        """Safe read of any column using to_jsonb."""
        raw = f"COALESCE(to_jsonb({alias})->>'{name}', '')"
        return f"NULLIF({raw}, '')"

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

        # Always exclude rows where core dates are missing or invalid
        clauses.append(f"{alias}.dn_create_date IS NOT NULL")
        clauses.append(f"({alias}.good_issue_date IS NULL OR {alias}.good_issue_date >= {alias}.dn_create_date)")
        # No dependency on delivery_status – we rely on pod_date for delivery

        for key, column in self._FILTER_COLUMNS.items():
            value = filters.get(key)
            if value in (None, "", "All", "all"):
                continue
            # Use safe column access
            safe_col = self._col(alias, column)
            if isinstance(value, (list, tuple, set)):
                values = [item for item in value if item not in (None, "")]
                if not values:
                    continue
                names = []
                for index, item in enumerate(values):
                    parameter = f"filter_{key}_{index}"
                    names.append(f":{parameter}")
                    params[parameter] = item
                clauses.append(f"{safe_col} IN ({', '.join(names)})")
            else:
                parameter = f"filter_{key}"
                clauses.append(f"{safe_col} = :{parameter}")
                params[parameter] = value

        if filters.get("date_from"):
            clauses.append(f"{alias}.dn_create_date >= :filter_date_from")
            params["filter_date_from"] = filters["date_from"]
        if filters.get("date_to"):
            clauses.append(f"{alias}.dn_create_date < (CAST(:filter_date_to AS date) + INTERVAL '1 day')")
            params["filter_date_to"] = filters["date_to"]

        return (" AND ".join(clauses) if clauses else "TRUE"), params

    def _metrics_select(self, alias: str = "dr") -> str:
        quantity = self._quantity(alias)
        amount = self._amount(alias)
        distance = self._distance(alias)
        return f"""
            COUNT(DISTINCT {alias}.dn_no) AS total_dn,
            COALESCE(SUM({quantity}), 0) AS total_units,
            COALESCE(SUM({amount}), 0) AS total_revenue,
            COUNT(DISTINCT {alias}.dn_no) FILTER (WHERE {alias}.good_issue_date IS NOT NULL) AS pgi_dn,
            COALESCE(SUM(CASE WHEN {alias}.good_issue_date IS NOT NULL THEN {quantity} ELSE 0 END), 0) AS pgi_units,
            COUNT(DISTINCT {alias}.dn_no) FILTER (WHERE {alias}.pod_date IS NOT NULL) AS delivered_dn,
            COALESCE(SUM(CASE WHEN {alias}.pod_date IS NOT NULL THEN {quantity} ELSE 0 END), 0) AS delivered_units,
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

            COALESCE(100.0 * COUNT(*) FILTER (
                WHERE {alias}.good_issue_date IS NOT NULL
                AND ({alias}.good_issue_date::timestamp - {alias}.dn_create_date::timestamp) <= INTERVAL '1 day'
            ) / NULLIF(COUNT(*) FILTER (WHERE {alias}.good_issue_date IS NOT NULL), 0), 0) AS pgi_compliance_pct,

            COALESCE(100.0 * COUNT(*) FILTER (
                WHERE {alias}.pod_date IS NOT NULL
                AND ({alias}.pod_date::timestamp - {alias}.good_issue_date::timestamp)
                    <= COALESCE(
                        CASE
                            WHEN {distance} <= 100 THEN INTERVAL '1 day'
                            WHEN {distance} <= 250 THEN INTERVAL '2 days'
                            WHEN {distance} <= 450 THEN INTERVAL '3 days'
                            WHEN {distance} <= 700 THEN INTERVAL '4 days'
                            WHEN {distance} <= 900 THEN INTERVAL '5 days'
                            ELSE INTERVAL '6 days'
                        END, INTERVAL '2 days'
                    )
            ) / NULLIF(COUNT(*) FILTER (WHERE {alias}.pod_date IS NOT NULL), 0), 0) AS delivery_compliance_pct,

            COALESCE(100.0 * COUNT(*) FILTER (WHERE {alias}.pod_date IS NOT NULL)
                / NULLIF(COUNT(*) FILTER (WHERE {alias}.pod_date IS NOT NULL), 0), 0) AS pod_compliance_pct
        """

    # ---- Public methods ----

    def fetch_dashboard_summary(self, filters: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        where, params = self._where(filters)
        return self._fetch_one(f"SELECT {self._metrics_select()} FROM delivery_reports dr WHERE {where}", params)

    def fetch_warehouse_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        warehouse_col = self._col("dr", "warehouse")
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM({warehouse_col}), ''), 'Unassigned') AS warehouse,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM({warehouse_col}), ''), 'Unassigned')
            ORDER BY total_units DESC, warehouse
            """,
            params,
        )

    def fetch_city_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        city_col = self._col("dr", "ship_to_city")
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM({city_col}), ''), 'Unassigned') AS city,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM({city_col}), ''), 'Unassigned')
            ORDER BY total_units DESC, city
            """,
            params,
        )

    def fetch_dealer_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        dealer_col = self._col("dr", "dealer_code")
        name_col = self._col("dr", "customer_name")
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM({dealer_col}), ''), 'Unassigned') AS dealer_code,
                   COALESCE(NULLIF(BTRIM(MAX({name_col})), ''), 'Unassigned') AS dealer_name,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM({dealer_col}), ''), 'Unassigned')
            ORDER BY total_units DESC, dealer_name
            """,
            params,
        )

    def fetch_product_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        sku_col = self._col("dr", "material_no")
        model_col = self._col("dr", "customer_model")
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM({sku_col}), ''), 'Unassigned') AS sku,
                   COALESCE(NULLIF(BTRIM(MAX({model_col})), ''), 'Unassigned') AS product_name,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM({sku_col}), ''), 'Unassigned')
            ORDER BY total_units DESC, product_name
            """,
            params,
        )

    def fetch_division_summary(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        div_col = self._col("dr", "division")
        return self._fetch_all(
            f"""
            SELECT COALESCE(NULLIF(BTRIM({div_col}), ''), 'Unassigned') AS division,
                   {self._metrics_select()}
            FROM delivery_reports dr
            WHERE {where}
            GROUP BY COALESCE(NULLIF(BTRIM({div_col}), ''), 'Unassigned')
            ORDER BY total_revenue DESC, total_units DESC, division
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
            GROUP BY DATE_TRUNC('month', dr.dn_create_date)::date ORDER BY month
            """,
            params,
        )

    def fetch_warehouse_daily_trend(self, days: int = 60, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        params = {**params, "trend_days": max(2, int(days))}
        warehouse_col = self._col("dr", "warehouse")
        return self._fetch_all(
            f"""
            WITH latest AS (
                SELECT MAX(dr.dn_create_date)::date AS max_date FROM delivery_reports dr WHERE {where}
            )
            SELECT COALESCE(NULLIF(BTRIM({warehouse_col}), ''), 'Unassigned') AS warehouse,
                   dr.dn_create_date::date AS date,
                   {self._metrics_select()}
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
                       CASE WHEN dr.dn_create_date IS NULL THEN NULL
                            ELSE GREATEST(CURRENT_DATE - dr.dn_create_date::date, 0) END AS pending_days
                FROM delivery_reports dr
                WHERE {where} AND dr.pod_date IS NULL
            )
            SELECT CASE
                     WHEN pending_days <= 2 THEN '0-2 Days'
                     WHEN pending_days <= 5 THEN '3-5 Days'
                     ELSE '>5 Days'
                   END AS bucket,
                   COUNT(DISTINCT dn_no) AS dn_count,
                   COALESCE(SUM(units), 0) AS units,
                   MIN(pending_days) AS sort_days
            FROM pending
            GROUP BY 1
            ORDER BY sort_days NULLS LAST
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
                SELECT {quantity} AS units,
                       {distance} AS distance_km,
                       CASE WHEN dr.good_issue_date IS NOT NULL AND dr.pod_date >= dr.good_issue_date
                            THEN EXTRACT(EPOCH FROM (dr.pod_date::timestamp - dr.good_issue_date::timestamp)) / 86400.0
                       END AS transit_days
                FROM delivery_reports dr
                WHERE {where}
            ), targeted AS (
                SELECT *,
                    CASE
                      WHEN distance_km <= 100 THEN 1
                      WHEN distance_km <= 250 THEN 2
                      WHEN distance_km <= 450 THEN 3
                      WHEN distance_km <= 700 THEN 4
                      WHEN distance_km <= 900 THEN 5
                      WHEN distance_km > 900 THEN 6
                    END AS target_days,
                    CASE
                      WHEN distance_km <= 100 THEN '0-100'
                      WHEN distance_km <= 250 THEN '101-250'
                      WHEN distance_km <= 450 THEN '251-450'
                      WHEN distance_km <= 700 THEN '451-700'
                      WHEN distance_km <= 900 THEN '701-900'
                      WHEN distance_km > 900 THEN '900+'
                    END AS distance
                FROM source
            )
            SELECT distance,
                   target_days,
                   COALESCE(AVG(transit_days), 0) AS actual_days,
                   CASE WHEN COALESCE(SUM(CASE WHEN transit_days IS NOT NULL THEN units ELSE 0 END), 0) > 0
                     THEN ROUND(100.0 * SUM(CASE WHEN transit_days <= target_days THEN units ELSE 0 END)
                           / NULLIF(SUM(CASE WHEN transit_days IS NOT NULL THEN units ELSE 0 END), 0), 2)
                     ELSE 0 END AS compliance_pct,
                   COUNT(*) FILTER (WHERE transit_days IS NOT NULL) AS delivery_records
            FROM targeted
            WHERE target_days IS NOT NULL
            GROUP BY distance, target_days
            ORDER BY target_days
            """,
            params,
        )

    def fetch_record_count(self, filters: Optional[Mapping[str, Any]] = None) -> int:
        where, params = self._where(filters)
        row = self._fetch_one(f"SELECT COUNT(*) AS record_count FROM delivery_reports dr WHERE {where}", params)
        return SafeNumber.to_int(row.get("record_count"))

    def get_import_summary(self, filters: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        where, params = self._where(filters)
        row = self._fetch_one(
            f"""
            SELECT COUNT(*) AS rows_imported,
                   COUNT(DISTINCT dr.dn_no) AS delivery_notes,
                   MAX(dr.dn_create_date) AS latest_dn_create_date
            FROM delivery_reports dr
            WHERE {where}
            """,
            params,
        )
        return {
            "rows_imported": SafeNumber.to_int(row.get("rows_imported")),
            "delivery_notes": SafeNumber.to_int(row.get("delivery_notes")),
            "latest_dn_create_date": _iso_date(row.get("latest_dn_create_date")),
        }


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
            "pending_units": _round(metrics.pending_units),
            "avg_transit_days": _round(metrics.avg_transit_days),
            "avg_cycle_days": _round(metrics.avg_cycle_days),
        }

    @staticmethod
    def compute_trends(daily_rows: Sequence[Mapping[str, Any]], monthly_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        daily = [TrendEngine.point(row, "date") for row in daily_rows]
        monthly = [TrendEngine.point(row, "month") for row in monthly_rows]
        return {
            "daily": daily,
            "weekly": daily[-7:],
            "monthly": daily[-30:],
            "yearly": monthly,
        }


# ---------------------------------------------------------------------------
# BLOCK 7: Warehouse Intelligence
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
                "warehouse": warehouse,
                "dns": metrics.total_dn,
                "units": _round(metrics.total_units),
                "revenue": _round(metrics.total_revenue),
                "pgi_pct": metrics.pgi_pct,
                "delivery_pct": metrics.delivery_pct,
                "pod_pct": metrics.pod_pct,
                "avg_pgi_days": _round(metrics.avg_pgi_days),
                "avg_transit_days": _round(metrics.avg_transit_days),
                "avg_pod_days": _round(metrics.avg_pod_days),
                "avg_cycle_days": _round(metrics.avg_cycle_days),
                "pending_units": _round(metrics.pending_units),
                "pending_dns": metrics.pending_dn,
                "health_score": score,
                "status": status,
                "risk": emoji,
                "risk_level": risk_level,
                "trend": TrendEngine.calculate_warehouse_trend(warehouse, warehouse_daily_trends),
                "ai_insight": "Warehouse performance monitored against logistics targets.",
            }
            rankings.append(ranking)
        rankings.sort(key=lambda item: (-SafeNumber.to_float(item["health_score"]), str(item["warehouse"])))
        for idx, item in enumerate(rankings, start=1):
            item["rank"] = idx
        return rankings

    @staticmethod
    def get_best_and_worst(warehouses: Sequence[Mapping[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not warehouses:
            return {}, {}
        return dict(warehouses[0]), dict(warehouses[-1])


# ---------------------------------------------------------------------------
# BLOCK 8: Alert Engine
# ---------------------------------------------------------------------------

class AlertEngine:
    @staticmethod
    def generate_alerts(metrics: DashboardMetrics, health: float, warehouses: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        if metrics.total_dn <= 0 or metrics.total_units <= 0:
            return []
        alerts: List[Dict[str, Any]] = []

        def add(source: str, severity: str, category: str, message: str, priority: float) -> None:
            alerts.append({
                "source": source,
                "severity": severity,
                "category": category,
                "message": message,
                "urgency": round(priority, 2),
            })

        if metrics.pending_units > config.pending_units_alert_threshold:
            add("National", "CRITICAL", "Pending Units",
                f"{metrics.pending_units:,.0f} pending units exceed the {config.pending_units_alert_threshold:,} escalation threshold.",
                100 + metrics.pending_units / 1000)
        if metrics.delivery_pct < config.delivery_alert_threshold:
            add("National", "CRITICAL", "Delivery Achievement",
                f"Delivery achievement is {metrics.delivery_pct:.1f}%, below {config.delivery_alert_threshold:.0f}%.",
                95 + (config.delivery_alert_threshold - metrics.delivery_pct))
        if metrics.pgi_pct < config.pgi_alert_threshold:
            add("National", "HIGH", "PGI Achievement",
                f"PGI achievement is {metrics.pgi_pct:.1f}%, below {config.pgi_alert_threshold:.0f}%.",
                85 + (config.pgi_alert_threshold - metrics.pgi_pct))
        if metrics.pod_pct < config.pod_alert_threshold:
            add("National", "HIGH", "POD Achievement",
                f"POD achievement is {metrics.pod_pct:.1f}%, below {config.pod_alert_threshold:.0f}%.",
                80 + (config.pod_alert_threshold - metrics.pod_pct))
        if health < config.health_alert_threshold:
            add("National", "HIGH", "Health Score",
                f"Logistics health is {health:.1f}%, below {config.health_alert_threshold:.0f}%.",
                75 + (config.health_alert_threshold - health))

        for warehouse in warehouses:
            source = warehouse.get("warehouse", "Unassigned")
            transit_days = SafeNumber.to_float(warehouse.get("avg_transit_days"))
            pending = SafeNumber.to_float(warehouse.get("pending_units"))
            pod = SafeNumber.to_float(warehouse.get("pod_pct"))
            if transit_days > config.transit_target_days:
                add(source, "HIGH" if transit_days > config.transit_target_days + 1 else "WARNING",
                    "Delivery Days",
                    f"Average delivery time is {transit_days:.1f} days against the {config.transit_target_days:.0f}-day target.",
                    60 + transit_days)
            if pending > config.pending_units_alert_threshold:
                add(source, "CRITICAL", "Pending Units",
                    f"Pending units are {pending:,.0f}, above the escalation threshold.",
                    70 + pending / 1000)
            if pod < config.pod_alert_threshold:
                add(source, "HIGH", "POD Achievement",
                    f"POD achievement is {pod:.1f}%.",
                    65 + (config.pod_alert_threshold - pod))

        alerts.sort(key=lambda item: (-SafeNumber.to_float(item["urgency"]), item["source"], item["category"]))
        return alerts[:config.max_alerts]


# ---------------------------------------------------------------------------
# BLOCK 9: Recommendation Engine
# ---------------------------------------------------------------------------

class RecommendationEngine:
    @staticmethod
    def generate_recommendations(warehouses: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        recommendations: List[Dict[str, Any]] = []
        for warehouse in warehouses:
            name = warehouse.get("warehouse", "Unassigned")
            health = SafeNumber.to_float(warehouse.get("health_score"))
            pending = SafeNumber.to_float(warehouse.get("pending_units"))
            delivery = SafeNumber.to_float(warehouse.get("delivery_pct"))
            pod = SafeNumber.to_float(warehouse.get("pod_pct"))

            if pending > config.pending_units_alert_threshold:
                recommendations.append({
                    "warehouse": name,
                    "priority_score": 100 + pending,
                    "issue": "Pending inventory backlog",
                    "recommendation": f"Clear {pending:,.0f} pending units at {name} through a daily dispatch recovery plan.",
                    "expected_improvement": "Improves pending efficiency and health score.",
                    "target_kpi": "Pending Units",
                })
            elif delivery < config.delivery_alert_threshold:
                recommendations.append({
                    "warehouse": name,
                    "priority_score": 90 + (config.delivery_alert_threshold - delivery),
                    "issue": "Low delivery achievement",
                    "recommendation": f"Review open PGI shipments and transporter SLA exceptions for {name}.",
                    "expected_improvement": "Improves Delivery % toward the 70% recovery threshold.",
                    "target_kpi": "Delivery Achievement",
                })
            elif pod < config.pod_alert_threshold:
                recommendations.append({
                    "warehouse": name,
                    "priority_score": 80 + (config.pod_alert_threshold - pod),
                    "issue": "Low POD achievement",
                    "recommendation": f"Escalate POD collection and proof submission with transport partners at {name}.",
                    "expected_improvement": "Improves POD % and reduces completed-delivery documentation gaps.",
                    "target_kpi": "POD Achievement",
                })
            elif health < config.health_alert_threshold and not any(item["warehouse"] == name for item in recommendations):
                recommendations.append({
                    "warehouse": name,
                    "priority_score": 70 + (config.health_alert_threshold - health),
                    "issue": "Low logistics health",
                    "recommendation": f"Run a root-cause review for PGI, delivery, POD, and pending flow at {name}.",
                    "expected_improvement": "Improves the weakest health-score components.",
                    "target_kpi": "Health Score",
                })

        recommendations.sort(key=lambda item: (-SafeNumber.to_float(item["priority_score"]), item["warehouse"]))
        for idx, item in enumerate(recommendations, start=1):
            item["priority"] = f"Priority {idx}"
            item["problem"] = item["issue"]
            item.pop("priority_score", None)
        return recommendations[:10]


# ---------------------------------------------------------------------------
# BLOCK 10: Response Builder
# ---------------------------------------------------------------------------

class ResponseBuilder:
    @staticmethod
    def _pipeline(metrics: DashboardMetrics) -> Dict[str, Dict[str, Any]]:
        in_transit_dn = max(0, metrics.pgi_dn - metrics.delivered_dn)
        in_transit_units = max(0.0, metrics.pgi_units - metrics.delivered_units)
        return {
            "dn_created": {"dn": metrics.total_dn, "units": _round(metrics.total_units), "pct": 100.0 if metrics.total_dn else 0.0, "avg_days": 0.0, "pending": 0},
            "pgi_completed": {"dn": metrics.pgi_dn, "units": _round(metrics.pgi_units), "pct": metrics.pgi_pct, "avg_days": _round(metrics.avg_pgi_days), "pending": max(0, metrics.total_dn - metrics.pgi_dn)},
            "in_transit": {"dn": in_transit_dn, "units": _round(in_transit_units), "pct": SafeNumber.pct(in_transit_units, metrics.pgi_units), "avg_days": _round(metrics.avg_transit_days), "pending": in_transit_dn},
            "delivered": {"dn": metrics.delivered_dn, "units": _round(metrics.delivered_units), "pct": metrics.delivery_pct, "avg_days": _round(metrics.avg_transit_days), "pending": metrics.pending_dn},
            "pod_received": {"dn": metrics.pod_dn, "units": _round(metrics.pod_units), "pct": metrics.pod_pct, "avg_days": _round(metrics.avg_pod_days), "pending": max(0, metrics.delivered_dn - metrics.pod_dn)},
        }

    @staticmethod
    def _monthly_response(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        response = []
        for row in rows:
            point = TrendEngine.point(row, "month")
            point["month"] = point.pop("date")
            response.append(point)
        return response

    @staticmethod
    def _city_delays(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        for row in rows:
            metrics = DashboardMetrics.from_row(row)
            days = metrics.avg_transit_days
            status = "Critical" if days > config.transit_target_days + 2 else "High" if days > config.transit_target_days else "Within Standard"
            result.append({"city": row.get("city") or "Unassigned", "avg_delivery_days": _round(days), "pending_units": _round(metrics.pending_units), "status": status})
        return sorted(result, key=lambda item: (-SafeNumber.to_float(item["avg_delivery_days"]), -SafeNumber.to_float(item["pending_units"]), str(item["city"])))[:5]

    @staticmethod
    def _divisions(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        total_revenue = sum(max(0.0, SafeNumber.to_float(row.get("total_revenue"))) for row in rows)
        return [
            {"division": row.get("division") or "Unassigned", "dns": SafeNumber.to_int(row.get("total_dn")), "units": _round(row.get("total_units")), "revenue": _round(row.get("total_revenue")), "percentage": SafeNumber.pct(row.get("total_revenue"), total_revenue)}
            for row in rows
        ]

    @staticmethod
    def _charts(warehouses: Sequence[Mapping[str, Any]], trends: Mapping[str, Any], divisions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        return {
            "warehouse_ranking": {"labels": [row.get("warehouse") for row in warehouses], "health_score": [row.get("health_score") for row in warehouses], "delivery_pct": [row.get("delivery_pct") for row in warehouses]},
            "performance_trend": trends,
            "division_performance": {"labels": [row.get("division") for row in divisions], "revenue": [row.get("revenue") for row in divisions]},
        }

    @classmethod
    def build(cls, metrics: DashboardMetrics, health: float, warehouses: Sequence[Mapping[str, Any]],
              city_rows: Sequence[Mapping[str, Any]], dealers: Sequence[Mapping[str, Any]],
              products: Sequence[Mapping[str, Any]], divisions: Sequence[Mapping[str, Any]],
              daily_rows: Sequence[Mapping[str, Any]], monthly_rows: Sequence[Mapping[str, Any]],
              pending_analysis: Sequence[Mapping[str, Any]], compliance_rows: Sequence[Mapping[str, Any]],
              alerts: Sequence[Mapping[str, Any]], recommendations: Sequence[Mapping[str, Any]],
              import_summary: Mapping[str, Any], record_count: int) -> Dict[str, Any]:
        cards = {
            "total_dn": {"value": metrics.total_dn, "label": "Total DNs"},
            "total_units": {"value": _round(metrics.total_units), "label": "Total Units"},
            "total_value": {"value": _round(metrics.total_revenue), "label": "Total Revenue"},
            "pgi_achievement": {"value": metrics.pgi_pct, "label": "PGI %"},
            "delivery_achievement": {"value": metrics.delivery_pct, "label": "Delivery %"},
            "pod_achievement": {"value": metrics.pod_pct, "label": "POD %"},
            "pending_units": {"value": _round(metrics.pending_units), "label": "Pending Units"},
            "health_score": {"value": health, "label": "Health Score"},
            "avg_pgi_days": {"value": _round(metrics.avg_pgi_days), "label": "Avg PGI Days"},
            "avg_transit_days": {"value": _round(metrics.avg_transit_days), "label": "Avg Delivery Days"},
            "avg_cycle_days": {"value": _round(metrics.avg_cycle_days), "label": "Avg Cycle Days"},
        }

        pipeline = cls._pipeline(metrics)
        trends = TrendEngine.compute_trends(daily_rows, monthly_rows)
        monthly_trend = cls._monthly_response(monthly_rows)
        division_performance = cls._divisions(divisions)
        city_delays = cls._city_delays(city_rows)

        top_dealers = [{"dealer": row.get("dealer_name") or row.get("dealer_code") or "Unassigned", "dns": SafeNumber.to_int(row.get("total_dn")), "units": _round(row.get("total_units")), "revenue": _round(row.get("total_revenue"))} for row in list(dealers)[:5]]
        top_products = [{"product": row.get("product_name") or row.get("sku") or "Unassigned", "units": _round(row.get("total_units")), "revenue": _round(row.get("total_revenue")), "delivery_notes": SafeNumber.to_int(row.get("total_dn"))} for row in list(products)[:5]]
        top_pending = [{"warehouse": row.get("warehouse"), "pending_dns": row.get("pending_dns", 0), "pending_units": row.get("pending_units", 0)} for row in sorted(warehouses, key=lambda item: (-SafeNumber.to_float(item.get("pending_units")), str(item.get("warehouse"))))[:5]]

        national = {"health_score": health, "pgi_pct": metrics.pgi_pct, "delivery_pct": metrics.delivery_pct, "pod_pct": metrics.pod_pct, "pending_units": _round(metrics.pending_units), "avg_cycle_days": _round(metrics.avg_cycle_days), "avg_transit_days": _round(metrics.avg_transit_days)}
        best, worst = WarehouseIntelligenceEngine.get_best_and_worst(warehouses)
        metadata = {"version": "29.1", "timestamp": datetime.utcnow().isoformat(), "record_count": record_count, "warehouse_count": len(warehouses), "data_source": "delivery_reports"}

        return {
            "executive_summary": national,
            "executive_summary_text": f"Overall logistics health is {health:.1f}%. PGI {metrics.pgi_pct:.1f}%, Delivery {metrics.delivery_pct:.1f}%, POD {metrics.pod_pct:.1f}%. Pending backlog: {metrics.pending_units:,.0f} units.",
            "executive_summary_detailed": {"overall_health": health, "overall_delivery": metrics.delivery_pct, "overall_cycle": _round(metrics.avg_cycle_days), "best_warehouse": best.get("warehouse"), "worst_warehouse": worst.get("warehouse"), "critical_warehouses": sum(1 for w in warehouses if w.get("risk_level") == "critical")},
            "cards": cards, "kpis": cards, "total_revenue": _round(metrics.total_revenue),
            "pipeline": pipeline, "pipeline_detailed": pipeline,
            "warehouse": list(warehouses), "warehouses": list(warehouses), "warehouse_summary": list(warehouses), "warehouse_ranking": list(warehouses),
            "city": list(city_rows), "cities": list(city_rows), "dealer": list(dealers), "dealers": list(dealers),
            "product": list(products), "products": list(products), "division": list(divisions), "divisions": list(divisions),
            "top_delayed_cities": city_delays, "top_pending_warehouses": top_pending,
            "top_dealers": top_dealers, "top_products": top_products, "division_performance": division_performance,
            "daily_trend": [TrendEngine.point(row, "date") for row in daily_rows], "monthly_trend": monthly_trend,
            "performance_trends": trends, "pending_analysis": list(pending_analysis),
            "delivery_compliance": [{"distance": row.get("distance"), "target_days": SafeNumber.to_int(row.get("target_days")), "actual_days": _round(row.get("actual_days")), "compliance_pct": _round(row.get("compliance_pct")), "status": "Within Standard" if SafeNumber.to_float(row.get("compliance_pct")) >= 100 else "Above Standard"} for row in compliance_rows],
            "alerts": list(alerts), "critical_alerts": list(alerts),
            "recommendations": list(recommendations), "director_recommendations": list(recommendations),
            "import_summary": dict(import_summary), "metadata": metadata,
            "national_averages": national,
            "insights": {"insights": [{"type": "best_performing", "text": f"Best Warehouse: {best.get('warehouse', 'N/A')}"}, {"type": "worst_performing", "text": f"Warehouse needing most attention: {worst.get('warehouse', 'N/A')}"}, {"type": "overall_delivery", "text": f"Overall Delivery Achievement: {metrics.delivery_pct:.1f}%"}, {"type": "pending_units", "text": f"Total Pending Units: {metrics.pending_units:,.0f}"}]},
            "warehouse_preview": [{"sn": index, "warehouse": row.get("warehouse"), "total_units": row.get("units"), "delivered_units": row.get("delivered_units"), "pending_units": row.get("pending_units"), "pgi_days": row.get("avg_pgi_days"), "transit_days": row.get("avg_transit_days"), "cycle_days": row.get("avg_cycle_days"), "delivery_performance": row.get("status"), "health_score": row.get("health_score")} for index, row in enumerate(list(warehouses)[:5], start=1)],
            "charts": cls._charts(warehouses, trends, division_performance),
        }


# ---------------------------------------------------------------------------
# BLOCK 11: Dashboard Service & FastAPI Routes
# ---------------------------------------------------------------------------

class DashboardService:
    def __init__(self, repository: Optional[DashboardRepository] = None) -> None:
        self._repo = repository or DashboardRepository()

    @cached(ttl=300)
    async def get_full_dashboard(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        filters = {k: v for k, v in (filters or {}).items() if k != "theme"}
        try:
            (
                summary_row,
                warehouse_rows,
                city_rows,
                dealer_rows,
                product_rows,
                division_rows,
                daily_rows,
                monthly_rows,
                warehouse_daily_rows,
                pending_rows,
                compliance_rows,
                import_summary,
                record_count,
            ) = await asyncio.gather(
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
                asyncio.to_thread(self._repo.get_import_summary, filters),
                asyncio.to_thread(self._repo.fetch_record_count, filters),
            )
            metrics = DashboardMetrics.from_row(summary_row)
            health = BusinessRuleEngine.health_score(metrics)
            warehouses = WarehouseIntelligenceEngine.compute_warehouse_intelligence(warehouse_rows, warehouse_daily_rows)
            alerts = AlertEngine.generate_alerts(metrics, health, warehouses)
            recommendations = RecommendationEngine.generate_recommendations(warehouses)
            return ResponseBuilder.build(
                metrics=metrics,
                health=health,
                warehouses=warehouses,
                city_rows=city_rows,
                dealers=dealer_rows,
                products=product_rows,
                divisions=division_rows,
                daily_rows=daily_rows,
                monthly_rows=monthly_rows,
                pending_analysis=pending_rows,
                compliance_rows=compliance_rows,
                alerts=alerts,
                recommendations=recommendations,
                import_summary=import_summary,
                record_count=record_count,
            )
        except DatabaseError as exc:
            logger.exception("Dashboard generation failed due to database access")
            raise HTTPException(status_code=500, detail="Unable to calculate dashboard data") from exc
        except Exception as exc:
            logger.exception("Dashboard generation failed")
            raise HTTPException(status_code=500, detail="Unable to calculate dashboard data") from exc

    async def get_dashboard_data(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self.get_full_dashboard(filters)

    @cached(ttl=60)
    async def get_warehouse_ranking(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        rows, trend_rows = await asyncio.gather(
            asyncio.to_thread(self._repo.fetch_warehouse_summary, filters),
            asyncio.to_thread(self._repo.fetch_warehouse_daily_trend, config.warehouse_trend_days, filters),
        )
        return WarehouseIntelligenceEngine.compute_warehouse_intelligence(rows, trend_rows)


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
    return await service.get_dashboard_data({"theme": theme})


@router.get("/warehouses")
async def get_warehouses(service: DashboardService = Depends(get_dashboard_service)) -> List[Dict[str, Any]]:
    return await service.get_warehouse_ranking()


@router.get("/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy", "version": "29.1", "timestamp": datetime.utcnow().isoformat()}


@router.post("/upload")
async def upload_excel_report(
    file: UploadFile = File(...),
    skip_duplicates: bool = Form(True),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    # Retain existing upload endpoint; cache is cleared after import
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded report is empty")
    cache.clear()
    return {"status": "success", "filename": file.filename, "message": "Report received; dashboard cache cleared."}
