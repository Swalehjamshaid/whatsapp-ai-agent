"""
File: app/services/dn_analysis.py
Version: 15.0 - POSTGRESQL ONLY - SINGLE SOURCE OF TRUTH
Purpose: Complete DN (Delivery Note) analytics service.
         POSTGRESQL IS THE ONLY SOURCE OF TRUTH.
         No mock data, no fallbacks, no hardcoded values.
         All data comes exclusively from PostgreSQL database.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Final

from cachetools import TTLCache
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

try:
    import openrouteservice  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    openrouteservice = None

try:
    from geopy.geocoders import Nominatim  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    Nominatim = None


TABLE: Final[str] = "delivery_reports"
SEPARATOR: Final[str] = "────────────────────"
SessionFactory = Callable[[], Session]


class DNNumber(BaseModel):
    """Strict input boundary for externally supplied DN numbers."""

    model_config = ConfigDict(str_strip_whitespace=True)
    value: str = Field(min_length=1, max_length=100)

    @field_validator("value")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("DN number contains control characters")
        return value


@dataclass(frozen=True, slots=True)
class DistanceResult:
    distance_km: float | None
    estimated_delivery_time: str | None
    source: str | None


class DeliveryReportRepository:
    """
    All SQL access for the delivery_reports relation.
    POSTGRESQL IS THE ONLY SOURCE OF TRUTH.
    """

    _GROUP_COLUMNS: Final[tuple[str, ...]] = (
        "dn_no", "customer_name", "dealer_code", "customer_code", "warehouse",
        "warehouse_code", "ship_to_city", "delivery_location", "sales_office",
        "sales_manager", "division", "order_type", "dn_work", "dn_create_date",
        "good_issue_date", "pod_date", "pgi_status", "pod_status",
        "delivery_status", "pending_flag", "remarks",
    )

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._verify_table_exists()

    def _verify_table_exists(self) -> None:
        """Verify that the delivery_reports table exists in PostgreSQL."""
        try:
            with self._session() as session:
                result = session.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{TABLE}')")
                ).scalar()
                if result:
                    logger.info(f"✅ Table '{TABLE}' exists in PostgreSQL")
                    # Get row count
                    count = session.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
                    logger.info(f"📊 Table '{TABLE}' has {count} rows")
                else:
                    logger.error(f"❌ Table '{TABLE}' does not exist in PostgreSQL")
                    raise RuntimeError(f"Table '{TABLE}' does not exist in PostgreSQL")
        except Exception as e:
            logger.error(f"❌ Failed to verify table: {e}")
            raise

    @contextmanager
    def _session(self):
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    @classmethod
    def _aggregate_sql(cls, where: str = "TRUE", order_by: str = "dn_create_date DESC") -> str:
        columns = ", ".join(cls._GROUP_COLUMNS)
        return f"""
            SELECT {columns},
                   COALESCE(SUM(dn_qty), 0) AS total_units,
                   COALESCE(SUM(dn_amount), 0) AS total_revenue,
                   COUNT(DISTINCT material_no) AS material_count,
                   COUNT(DISTINCT customer_model) AS model_count
              FROM {TABLE}
             WHERE {where}
             GROUP BY {columns}
             ORDER BY {order_by}
        """

    def fetch_one(self, dn_no: str) -> tuple[dict[str, Any] | None, float]:
        """Fetch a single DN from PostgreSQL."""
        started = time.perf_counter()
        sql = self._aggregate_sql("dn_no = :dn_no") + " LIMIT 1"
        with self._session() as session:
            row = session.execute(text(sql), {"dn_no": dn_no}).mappings().first()
        return (dict(row) if row else None, (time.perf_counter() - started) * 1000)

    def fetch_many(
        self,
        where: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        limit: int = 20,
        order_by: str = "dn_create_date DESC",
    ) -> tuple[list[dict[str, Any]], float]:
        """Fetch multiple DNs from PostgreSQL."""
        started = time.perf_counter()
        safe_limit = min(max(limit, 1), 500)
        sql = self._aggregate_sql(where, order_by) + " LIMIT :limit"
        values = dict(parameters or {}) | {"limit": safe_limit}
        with self._session() as session:
            rows = session.execute(text(sql), values).mappings().all()
        return [dict(row) for row in rows], (time.perf_counter() - started) * 1000

    def fetch_search(self, query: str, limit: int = 10) -> tuple[list[dict[str, Any]], float]:
        """Search for DNs in PostgreSQL by number or customer name."""
        started = time.perf_counter()
        safe_limit = min(max(limit, 1), 100)
        search_pattern = f"%{query}%"
        sql = self._aggregate_sql(
            "dn_no ILIKE :pattern OR customer_name ILIKE :pattern OR dealer_name ILIKE :pattern",
            "dn_create_date DESC"
        ) + " LIMIT :limit"
        with self._session() as session:
            rows = session.execute(
                text(sql), 
                {"pattern": search_pattern, "limit": safe_limit}
            ).mappings().all()
        return [dict(row) for row in rows], (time.perf_counter() - started) * 1000

    def fetch_summary(self) -> tuple[dict[str, Any], float]:
        """Get summary statistics from PostgreSQL."""
        started = time.perf_counter()
        sql = f"""
            SELECT 
                COUNT(*) AS total_dns,
                COUNT(DISTINCT customer_name) AS total_customers,
                COUNT(DISTINCT dealer_name) AS total_dealers,
                COUNT(DISTINCT warehouse) AS total_warehouses,
                COUNT(DISTINCT ship_to_city) AS total_cities,
                COALESCE(SUM(dn_amount), 0) AS total_revenue,
                COALESCE(SUM(dn_qty), 0) AS total_units,
                COALESCE(AVG(dn_amount), 0) AS avg_revenue_per_dn,
                COUNT(*) FILTER (WHERE pod_date IS NULL OR pending_flag = true) AS pending_dns,
                COUNT(*) FILTER (WHERE good_issue_date IS NULL) AS pending_pgi,
                COUNT(*) FILTER (WHERE good_issue_date IS NOT NULL AND pod_date IS NULL) AS pending_pod
            FROM {TABLE}
        """
        with self._session() as session:
            row = session.execute(text(sql)).mappings().first()
        return (dict(row) if row else {}, (time.perf_counter() - started) * 1000)

    def scalar(self, sql: str) -> Any:
        """Execute a scalar query on PostgreSQL."""
        with self._session() as session:
            return session.execute(text(sql)).scalar_one()


class DistanceService:
    """Route distance calculation - purely for enrichment, not source of truth."""

    def __init__(self) -> None:
        self._cache: TTLCache[str, tuple[float, float] | None] = TTLCache(512, 86_400)
        self._ors_key = os.getenv("OPENROUTESERVICE_API_KEY")
        self._geocoder = Nominatim(user_agent="dn-analysis-service", timeout=4) if Nominatim else None

    def _coordinates(self, location: str) -> tuple[float, float] | None:
        key = location.strip().casefold()
        if key in self._cache:
            return self._cache[key]
        coordinates = None
        if self._geocoder and key:
            try:
                result = self._geocoder.geocode(location, exactly_one=True)
                if result:
                    coordinates = (float(result.latitude), float(result.longitude))
            except Exception as exc:
                logger.warning("Geocoding failed for {}: {}", location, exc)
        self._cache[key] = coordinates
        return coordinates

    @staticmethod
    def _haversine(origin: tuple[float, float], destination: tuple[float, float]) -> float:
        lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *destination))
        dlat, dlon = lat2 - lat1, lon2 - lon1
        value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 6_371.0088 * 2 * math.asin(math.sqrt(value))

    def calculate(self, origin_name: str | None, destination_name: str | None) -> DistanceResult:
        if not origin_name or not destination_name:
            return DistanceResult(None, None, None)
        origin, destination = self._coordinates(origin_name), self._coordinates(destination_name)
        if not origin or not destination:
            return DistanceResult(None, None, None)
        if openrouteservice and self._ors_key:
            try:
                client = openrouteservice.Client(key=self._ors_key, timeout=5)
                route = client.directions(
                    [(origin[1], origin[0]), (destination[1], destination[0])],
                    profile="driving-car",
                )["routes"][0]["summary"]
                kilometres = round(float(route["distance"]) / 1000, 1)
                hours = float(route["duration"]) / 3600
                return DistanceResult(kilometres, self._format_duration(hours), "openrouteservice")
            except Exception as exc:
                logger.warning("OpenRouteService failed: {}", exc)
        kilometres = round(self._haversine(origin, destination), 1)
        return DistanceResult(kilometres, self._format_duration(kilometres / 45), "haversine")

    @staticmethod
    def _format_duration(hours: float) -> str:
        total_minutes = max(0, round(hours * 60))
        whole_hours, minutes = divmod(total_minutes, 60)
        return f"{whole_hours} Hours {minutes} Minutes" if minutes else f"{whole_hours} Hours"


class DNAnalysisService:
    """
    Application service for DN-only business analytics.
    POSTGRESQL IS THE ONLY SOURCE OF TRUTH.
    """

    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        *,
        engine: Engine | None = None,
        database_url: str | None = None,
        cache_ttl: int = 300,
    ) -> None:
        if session_factory is None:
            resolved_engine = engine or create_engine(
                database_url or os.getenv("DATABASE_URL", ""),
                pool_pre_ping=True,
                pool_recycle=1_800,
                connect_args={"connect_timeout": 5},
            )
            session_factory = sessionmaker(bind=resolved_engine, expire_on_commit=False)
        self.repository = DeliveryReportRepository(session_factory)
        self.distance = DistanceService()
        self.cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=1_024, ttl=cache_ttl)
        logger.info("✅ DNAnalysisService initialized - PostgreSQL is the source of truth")

    @staticmethod
    def _response(
        success: bool,
        data: Any = None,
        whatsapp_message: str = "",
        error: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "success": success,
            "data": {} if data is None else data,
            "whatsapp_message": whatsapp_message,
            "error": error,
            "metadata": dict(metadata or {}),
        }

    @staticmethod
    def _date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

    @staticmethod
    def _flag(value: Any) -> bool:
        return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "pending"}

    @staticmethod
    def _days(start: date | None, end: date | None) -> int | None:
        return max(0, (end - start).days) if start and end else None

    def _enrich(self, row: Mapping[str, Any], *, include_distance: bool = True) -> dict[str, Any]:
        """Enrich DN data with computed fields."""
        today = datetime.now(timezone.utc).date()
        dn_date = self._date(row.get("dn_create_date"))
        issue_date = self._date(row.get("good_issue_date"))
        pod_date = self._date(row.get("pod_date"))
        pending = self._flag(row.get("pending_flag")) or not pod_date
        distance = self.distance.calculate(
            str(row.get("warehouse") or row.get("warehouse_code") or "") or None,
            str(row.get("delivery_location") or row.get("ship_to_city") or "") or None,
        ) if include_distance else DistanceResult(None, None, None)
        units = Decimal(str(row.get("total_units") or 0))
        revenue = Decimal(str(row.get("total_revenue") or 0))
        result = {key: self._serialise(value) for key, value in row.items()}
        result.update({
            "dealer_name": row.get("customer_name"),
            "city": row.get("ship_to_city"),
            "average_revenue_per_unit": float(revenue / units) if units else 0.0,
            "dn_age": self._days(dn_date, today),
            "days_since_dn_created": self._days(dn_date, today),
            "pgi_days": self._days(dn_date, issue_date),
            "transit_days": self._days(issue_date, pod_date or (today if pending else None)),
            "pod_days": self._days(issue_date, pod_date or (today if pending else None)),
            "pod_delay": self._days(issue_date, pod_date or (today if pending else None)),
            "delivery_days": self._days(dn_date, pod_date or (today if pending else None)),
            "pending_days": self._days(dn_date, today) if pending else 0,
            "distance_km": distance.distance_km,
            "estimated_delivery_time": distance.estimated_delivery_time,
            "distance_source": distance.source,
            "computed_delivery_status": self._status(row, dn_date, issue_date, pod_date, today),
        })
        return result

    @staticmethod
    def _status(row: Mapping[str, Any], dn_date: date | None, issue: date | None, pod: date | None, today: date) -> str:
        delivery = str(row.get("delivery_status") or "").casefold()
        pgi = str(row.get("pgi_status") or "").casefold()
        pod_status = str(row.get("pod_status") or "").casefold()
        if pod or "complete" in pod_status or "deliver" in delivery:
            return "Delivered" if "deliver" in delivery else "Completed"
        if not issue or "pending" in pgi:
            return "Pending PGI"
        if "pending" in pod_status:
            return "Pending POD"
        if issue and (today - issue).days > int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7")):
            return "Delayed"
        if issue:
            return "In Transit"
        return "Pending DN"

    @staticmethod
    def _serialise(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    def _dashboard_message(self, item: Mapping[str, Any]) -> str:
        """Format DN dashboard message for WhatsApp."""
        show = lambda value: "N/A" if value in (None, "") else str(value)
        money = f"PKR {float(item.get('total_revenue') or 0):,.0f}"
        pending = "Yes" if self._flag(item.get("pending_flag")) or not item.get("pod_date") else "No"
        return "\n".join([
            "📦 DN Dashboard", "", "DN", show(item.get("dn_no")), "", "Dealer",
            show(item.get("dealer_name")), "", "Dealer Code", show(item.get("dealer_code")),
            "", "Customer Code", show(item.get("customer_code")), "", "Warehouse",
            show(item.get("warehouse")), "", "Warehouse Code", show(item.get("warehouse_code")),
            "", "City", show(item.get("city")), "", "Delivery Location",
            show(item.get("delivery_location")), "", "Sales Office", show(item.get("sales_office")),
            "", "Sales Manager", show(item.get("sales_manager")), "", "Division",
            show(item.get("division")), "", "Order Type", show(item.get("order_type")), "",
            SEPARATOR, "", "Units", show(item.get("total_units")), "", "Revenue", money,
            "", "Models", show(item.get("model_count")), "", "Materials",
            show(item.get("material_count")), "", SEPARATOR, "", "DN Date",
            show(item.get("dn_create_date")), "", "PGI Date", show(item.get("good_issue_date")),
            "", "POD Date", show(item.get("pod_date")), "", "Transit",
            f"{show(item.get('transit_days'))} Days", "", "Delivery",
            f"{show(item.get('delivery_days'))} Days", "", "Distance",
            f"{show(item.get('distance_km'))} KM", "", "Estimated Time",
            show(item.get("estimated_delivery_time")), "", SEPARATOR, "", "Delivery Status",
            show(item.get("computed_delivery_status")), "", "PGI Status",
            show(item.get("pgi_status")), "", "POD Status", show(item.get("pod_status")),
            "", "Pending", pending,
        ])

    def _run_list(self, operation: str, where: str, parameters: Mapping[str, Any] | None = None, *, limit: int = 20, order_by: str = "dn_create_date DESC") -> dict[str, Any]:
        """Execute a list query on PostgreSQL."""
        request_id, started = str(uuid.uuid4()), time.perf_counter()
        try:
            rows, sql_ms = self.repository.fetch_many(where, parameters, limit=limit, order_by=order_by)
            data = [self._enrich(row, include_distance=False) for row in rows]
            elapsed = (time.perf_counter() - started) * 1000
            logger.bind(request_id=request_id).info("{} rows={} sql_ms={:.2f} total_ms={:.2f}", operation, len(data), sql_ms, elapsed)
            return self._response(True, data, self._list_message(operation, data), metadata={"request_id": request_id, "row_count": len(data), "sql_time_ms": round(sql_ms, 2), "execution_time_ms": round(elapsed, 2)})
        except Exception as exc:
            return self._error(exc, request_id, operation, started)

    @staticmethod
    def _list_message(title: str, rows: Sequence[Mapping[str, Any]]) -> str:
        heading = title.replace("_", " ").title()
        if not rows:
            return f"📋 {heading}\n\nNo delivery notes found in PostgreSQL."
        lines = [f"📋 {heading}", ""]
        for row in rows:
            lines.append(f"• DN {row.get('dn_no')} — {row.get('computed_delivery_status')} — {row.get('total_units')} Units")
        return "\n".join(lines)

    def _error(self, exc: Exception, request_id: str, operation: str, started: float) -> dict[str, Any]:
        """Handle errors from PostgreSQL operations."""
        if isinstance(exc, ValidationError):
            message = "Invalid DN number"
        elif isinstance(exc, OperationalError):
            message = "PostgreSQL connection or timeout error"
        elif isinstance(exc, DBAPIError):
            message = "PostgreSQL operation failed"
        elif isinstance(exc, SQLAlchemyError):
            message = "SQL execution failed on PostgreSQL"
        else:
            message = "DN analytics operation failed"
        logger.bind(request_id=request_id).exception("{} failed: {}", operation, exc)
        return self._response(False, error=message, metadata={"request_id": request_id, "error_type": type(exc).__name__, "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)})

    # ============================================================
    # CORE DN OPERATIONS - ALL DATA FROM POSTGRESQL
    # ============================================================

    def get_dn_dashboard(self, dn_no: str) -> dict[str, Any]:
        """
        Get complete DN dashboard from PostgreSQL.
        POSTGRESQL IS THE ONLY SOURCE OF TRUTH.
        """
        request_id, started = str(uuid.uuid4()), time.perf_counter()
        try:
            validated = DNNumber(value=dn_no).value
            if validated in self.cache:
                cached = dict(self.cache[validated])
                cached["metadata"] = dict(cached["metadata"]) | {"cache_hit": True, "request_id": request_id}
                return cached
            row, sql_ms = self.repository.fetch_one(validated)
            if not row:
                return self._response(False, error="DN not found in PostgreSQL", metadata={"request_id": request_id, "dn_no": validated, "sql_time_ms": round(sql_ms, 2)})
            item = self._enrich(row)
            elapsed = (time.perf_counter() - started) * 1000
            response = self._response(True, item, self._dashboard_message(item), metadata={"request_id": request_id, "dn_no": validated, "rows_returned": 1, "sql_time_ms": round(sql_ms, 2), "execution_time_ms": round(elapsed, 2), "cache_hit": False})
            self.cache[validated] = response
            logger.bind(request_id=request_id, dn_no=validated).info("DN dashboard from PostgreSQL sql_ms={:.2f} total_ms={:.2f}", sql_ms, elapsed)
            return response
        except Exception as exc:
            return self._error(exc, request_id, "get_dn_dashboard", started)

    def get_dn_details(self, dn_no: str) -> dict[str, Any]:
        """Alias for get_dn_dashboard - data from PostgreSQL."""
        return self.get_dn_dashboard(dn_no)

    def get_dn_status(self, dn_no: str) -> dict[str, Any]:
        """Get simplified DN status from PostgreSQL."""
        request_id, started = str(uuid.uuid4()), time.perf_counter()
        try:
            validated = DNNumber(value=dn_no).value
            row, sql_ms = self.repository.fetch_one(validated)
            if not row:
                return self._response(
                    False, 
                    error="DN not found in PostgreSQL",
                    whatsapp_message=f"❌ DN #{dn_no} not found in PostgreSQL.",
                    metadata={"request_id": request_id, "dn_no": validated}
                )
            item = self._enrich(row, include_distance=False)
            elapsed = (time.perf_counter() - started) * 1000
            status_message = (
                f"📊 **DN #{item.get('dn_no')} Status**\n\n"
                f"Status: {item.get('computed_delivery_status')}\n"
                f"Customer: {item.get('customer_name')}\n"
                f"Dealer: {item.get('dealer_name')}\n"
                f"Units: {item.get('total_units')}\n"
                f"Revenue: PKR {float(item.get('total_revenue') or 0):,.0f}\n"
                f"Created: {item.get('dn_create_date')}\n"
                f"PGI Status: {item.get('pgi_status')}\n"
                f"POD Status: {item.get('pod_status')}"
            )
            return self._response(
                True,
                {
                    "dn_number": item.get("dn_no"),
                    "status": item.get("computed_delivery_status"),
                    "status_code": 1 if "Delivered" in item.get("computed_delivery_status", "") else 0,
                    "customer_name": item.get("customer_name"),
                    "dealer_name": item.get("dealer_name"),
                    "total_units": item.get("total_units"),
                    "total_revenue": float(item.get("total_revenue") or 0),
                    "dn_create_date": item.get("dn_create_date"),
                    "pgi_status": item.get("pgi_status"),
                    "pod_status": item.get("pod_status")
                },
                whatsapp_message=status_message,
                metadata={"request_id": request_id, "dn_no": validated, "sql_time_ms": round(sql_ms, 2), "execution_time_ms": round(elapsed, 2)}
            )
        except Exception as exc:
            return self._error(exc, request_id, "get_dn_status", started)

    def get_dn_history(self, dn_no: str) -> dict[str, Any]:
        """Get DN history from PostgreSQL."""
        request_id, started = str(uuid.uuid4()), time.perf_counter()
        try:
            validated = DNNumber(value=dn_no).value
            row, sql_ms = self.repository.fetch_one(validated)
            if not row:
                return self._response(
                    False,
                    error="DN not found in PostgreSQL",
                    whatsapp_message=f"❌ DN #{dn_no} not found in PostgreSQL.",
                    metadata={"request_id": request_id, "dn_no": validated}
                )
            item = self._enrich(row, include_distance=False)
            
            # Build history events from PostgreSQL data
            events = []
            if item.get("dn_create_date"):
                events.append({
                    "timestamp": item.get("dn_create_date"),
                    "status": "Created",
                    "description": f"DN #{dn_no} created for {item.get('customer_name')}"
                })
            
            if item.get("good_issue_date"):
                events.append({
                    "timestamp": item.get("good_issue_date"),
                    "status": "PGI Created",
                    "description": "Goods Issue created in PostgreSQL"
                })
            
            if item.get("pod_date"):
                events.append({
                    "timestamp": item.get("pod_date"),
                    "status": "Delivered",
                    "description": "Proof of Delivery received in PostgreSQL"
                })
            
            elapsed = (time.perf_counter() - started) * 1000
            
            history_message = (
                f"📋 **DN #{dn_no} - History**\n"
                f"👤 {item.get('customer_name')}\n"
                f"🏪 {item.get('dealer_name')}\n\n"
                f"📅 **Event Timeline:**\n"
            )
            for event in events:
                history_message += f"  • {event.get('timestamp')} - {event.get('status')}: {event.get('description')}\n"
            
            history_message += f"\n📊 **Summary:** {item.get('computed_delivery_status')} - {item.get('total_units')} units"
            
            return self._response(
                True,
                {
                    "dn_number": dn_no,
                    "customer_name": item.get("customer_name"),
                    "dealer_name": item.get("dealer_name"),
                    "events": events,
                    "summary": item.get("computed_delivery_status")
                },
                whatsapp_message=history_message,
                metadata={"request_id": request_id, "dn_no": validated, "event_count": len(events), "sql_time_ms": round(sql_ms, 2), "execution_time_ms": round(elapsed, 2)}
            )
        except Exception as exc:
            return self._error(exc, request_id, "get_dn_history", started)

    def search_dns(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Search for DNs in PostgreSQL by number or customer name."""
        request_id, started = str(uuid.uuid4()), time.perf_counter()
        try:
            if not query or len(query.strip()) < 2:
                return self._response(
                    False,
                    error="Query too short",
                    whatsapp_message="⚠️ Please enter at least 2 characters to search in PostgreSQL.",
                    metadata={"request_id": request_id}
                )
            
            rows, sql_ms = self.repository.fetch_search(query.strip(), limit)
            
            if not rows:
                return self._response(
                    True,
                    [],
                    whatsapp_message=f"🔍 No results found in PostgreSQL for '{query}'",
                    metadata={"request_id": request_id, "query": query, "sql_time_ms": round(sql_ms, 2)}
                )
            
            data = [self._enrich(row, include_distance=False) for row in rows]
            elapsed = (time.perf_counter() - started) * 1000
            
            message = f"🔍 **Search Results from PostgreSQL for '{query}'**\n\n"
            for i, item in enumerate(data[:10], 1):
                message += (
                    f"{i}. **DN #{item.get('dn_no')}**\n"
                    f"   👤 {item.get('customer_name')}\n"
                    f"   🏪 {item.get('dealer_name')}\n"
                    f"   📊 {item.get('computed_delivery_status')}\n"
                    f"   📦 {item.get('total_units')} units\n\n"
                )
            
            if len(data) > 10:
                message += f"... and {len(data) - 10} more results from PostgreSQL"
            
            return self._response(
                True,
                data,
                whatsapp_message=message,
                metadata={"request_id": request_id, "query": query, "row_count": len(data), "sql_time_ms": round(sql_ms, 2), "execution_time_ms": round(elapsed, 2)}
            )
        except Exception as exc:
            return self._error(exc, request_id, "search_dns", started)

    def get_dn_summary(self) -> dict[str, Any]:
        """Get summary statistics from PostgreSQL."""
        request_id, started = str(uuid.uuid4()), time.perf_counter()
        try:
            summary, sql_ms = self.repository.fetch_summary()
            elapsed = (time.perf_counter() - started) * 1000
            
            if not summary:
                return self._response(
                    False,
                    error="No data found in PostgreSQL",
                    whatsapp_message="No DN data available in PostgreSQL.",
                    metadata={"request_id": request_id}
                )
            
            total_dns = int(summary.get("total_dns", 0))
            total_revenue = float(summary.get("total_revenue", 0))
            
            summary_message = (
                f"📊 **DN Summary from PostgreSQL**\n\n"
                f"📦 Total DNs: {total_dns}\n"
                f"💰 Total Revenue: PKR {total_revenue:,.0f}\n"
                f"📦 Total Units: {int(summary.get('total_units', 0))}\n"
                f"🏪 Total Customers: {int(summary.get('total_customers', 0))}\n"
                f"🏪 Total Dealers: {int(summary.get('total_dealers', 0))}\n"
                f"🏭 Total Warehouses: {int(summary.get('total_warehouses', 0))}\n"
                f"🏙️ Total Cities: {int(summary.get('total_cities', 0))}\n"
                f"📊 Average Revenue/DN: PKR {float(summary.get('avg_revenue_per_dn', 0)):,.0f}\n\n"
                f"⏳ Pending DNs: {int(summary.get('pending_dns', 0))}\n"
                f"   • Pending PGI: {int(summary.get('pending_pgi', 0))}\n"
                f"   • Pending POD: {int(summary.get('pending_pod', 0))}"
            )
            
            return self._response(
                True,
                {
                    "total_dns": total_dns,
                    "total_revenue": total_revenue,
                    "total_units": int(summary.get("total_units", 0)),
                    "total_customers": int(summary.get("total_customers", 0)),
                    "total_dealers": int(summary.get("total_dealers", 0)),
                    "total_warehouses": int(summary.get("total_warehouses", 0)),
                    "total_cities": int(summary.get("total_cities", 0)),
                    "avg_revenue_per_dn": float(summary.get("avg_revenue_per_dn", 0)),
                    "pending_dns": int(summary.get("pending_dns", 0)),
                    "pending_pgi": int(summary.get("pending_pgi", 0)),
                    "pending_pod": int(summary.get("pending_pod", 0))
                },
                whatsapp_message=summary_message,
                metadata={"request_id": request_id, "sql_time_ms": round(sql_ms, 2), "execution_time_ms": round(elapsed, 2)}
            )
        except Exception as exc:
            return self._error(exc, request_id, "get_dn_summary", started)

    # ============================================================
    # PENDING OPERATIONS - ALL DATA FROM POSTGRESQL
    # ============================================================

    def get_pending_dns(self, limit: int = 20) -> dict[str, Any]:
        """Get pending DNs from PostgreSQL."""
        return self._run_list("pending_dns", "COALESCE(pending_flag::text, '') ILIKE ANY (ARRAY['1','true','yes','y','pending']) OR pod_date IS NULL", limit=limit)

    def get_pending_pgi(self, limit: int = 20) -> dict[str, Any]:
        """Get pending PGI from PostgreSQL."""
        return self._run_list("pending_pgi", "good_issue_date IS NULL OR COALESCE(pgi_status, '') ILIKE '%pending%'", limit=limit)

    def get_pending_pod(self, limit: int = 20) -> dict[str, Any]:
        """Get pending POD from PostgreSQL."""
        return self._run_list("pending_pod", "good_issue_date IS NOT NULL AND (pod_date IS NULL OR COALESCE(pod_status, '') ILIKE '%pending%')", limit=limit)

    def get_recent_dns(self, limit: int = 20) -> dict[str, Any]:
        """Get recent DNs from PostgreSQL."""
        return self._run_list("recent_dns", "TRUE", limit=limit)

    def get_oldest_pending(self, limit: int = 20) -> dict[str, Any]:
        """Get oldest pending DNs from PostgreSQL."""
        return self._run_list("oldest_pending", "pod_date IS NULL", limit=limit, order_by="dn_create_date ASC NULLS LAST")

    def get_delivery_timeline(self, dn_no: str) -> dict[str, Any]:
        """Get delivery timeline from PostgreSQL."""
        result = self.get_dn_dashboard(dn_no)
        if not result["success"]:
            return result
        item = result["data"]
        result["data"] = {key: item.get(key) for key in ("dn_no", "dn_create_date", "good_issue_date", "pod_date", "pgi_days", "transit_days", "delivery_days", "pending_days", "computed_delivery_status")}
        return result

    def get_transit_analysis(self, limit: int = 50) -> dict[str, Any]:
        """Get transit analysis from PostgreSQL."""
        return self._run_list("transit_analysis", "good_issue_date IS NOT NULL", limit=limit, order_by="good_issue_date DESC")

    # ============================================================
    # SERVICE METADATA - POSTGRESQL IS THE SOURCE OF TRUTH
    # ============================================================

    def get_service_metadata(self) -> dict[str, Any]:
        """Get service metadata - PostgreSQL is the source of truth."""
        return self._response(True, {
            "service": "dn_analysis", 
            "version": "15.0", 
            "table": TABLE, 
            "source_of_truth": "PostgreSQL",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database_type": "PostgreSQL",
            "cache_enabled": True,
            "cache_ttl": self.cache.ttl
        })

    def health_check(self) -> dict[str, Any]:
        """Check PostgreSQL health."""
        started = time.perf_counter()
        try:
            database_time = self.repository.scalar("SELECT CURRENT_TIMESTAMP")
            return self._response(True, {
                "status": "healthy", 
                "database": "connected", 
                "source_of_truth": "PostgreSQL",
                "database_time": self._serialise(database_time)
            }, metadata={"execution_time_ms": round((time.perf_counter() - started) * 1000, 2)})
        except Exception as exc:
            return self._error(exc, str(uuid.uuid4()), "health_check", started)

    def validation_query(self) -> dict[str, Any]:
        """Validate PostgreSQL connection and table."""
        started = time.perf_counter()
        try:
            count = self.repository.scalar(f"SELECT COUNT(*) FROM {TABLE}")
            return self._response(True, {
                "table": TABLE, 
                "row_count": int(count), 
                "valid": True,
                "source_of_truth": "PostgreSQL"
            }, metadata={"execution_time_ms": round((time.perf_counter() - started) * 1000, 2)})
        except Exception as exc:
            return self._error(exc, str(uuid.uuid4()), "validation_query", started)


# ============================================================
# SINGLETON INSTANCE
# ============================================================

_default_service: DNAnalysisService | None = None


def configure(*, session_factory: SessionFactory | None = None, engine: Engine | None = None, database_url: str | None = None) -> DNAnalysisService:
    """Configure the singleton used by module-level compatibility functions."""
    global _default_service
    _default_service = DNAnalysisService(session_factory, engine=engine, database_url=database_url)
    return _default_service


def _service() -> DNAnalysisService:
    global _default_service
    if _default_service is None:
        _default_service = DNAnalysisService()
    return _default_service


# ============================================================
# MODULE-LEVEL FUNCTIONS - ALL DATA FROM POSTGRESQL
# ============================================================

def get_dn_dashboard(dn_no: str) -> dict[str, Any]: 
    return _service().get_dn_dashboard(dn_no)

def get_dn_details(dn_no: str) -> dict[str, Any]:
    return _service().get_dn_details(dn_no)

def get_dn_status(dn_no: str) -> dict[str, Any]:
    return _service().get_dn_status(dn_no)

def get_dn_history(dn_no: str) -> dict[str, Any]:
    return _service().get_dn_history(dn_no)

def search_dns(query: str, limit: int = 10) -> dict[str, Any]:
    return _service().search_dns(query, limit)

def get_dn_summary() -> dict[str, Any]:
    return _service().get_dn_summary()

def get_pending_dns(limit: int = 20) -> dict[str, Any]: 
    return _service().get_pending_dns(limit)

def get_pending_pgi(limit: int = 20) -> dict[str, Any]: 
    return _service().get_pending_pgi(limit)

def get_pending_pod(limit: int = 20) -> dict[str, Any]: 
    return _service().get_pending_pod(limit)

def get_recent_dns(limit: int = 20) -> dict[str, Any]: 
    return _service().get_recent_dns(limit)

def get_oldest_pending(limit: int = 20) -> dict[str, Any]: 
    return _service().get_oldest_pending(limit)

def get_delivery_timeline(dn_no: str) -> dict[str, Any]: 
    return _service().get_delivery_timeline(dn_no)

def get_transit_analysis(limit: int = 50) -> dict[str, Any]: 
    return _service().get_transit_analysis(limit)

def get_service_metadata() -> dict[str, Any]: 
    return _service().get_service_metadata()

def health_check() -> dict[str, Any]: 
    return _service().health_check()

def validation_query() -> dict[str, Any]: 
    return _service().validation_query()


__all__ = [
    "DNAnalysisService", 
    "DeliveryReportRepository", 
    "configure",
    "get_dn_dashboard",
    "get_dn_details",
    "get_dn_status", 
    "get_dn_history",
    "search_dns",
    "get_dn_summary",
    "get_pending_dns", 
    "get_pending_pgi", 
    "get_pending_pod", 
    "get_recent_dns",
    "get_oldest_pending", 
    "get_delivery_timeline", 
    "get_transit_analysis",
    "get_service_metadata", 
    "health_check", 
    "validation_query",
]
