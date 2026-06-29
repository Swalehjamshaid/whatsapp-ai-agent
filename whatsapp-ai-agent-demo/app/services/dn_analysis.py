# ==================================================================================================
# FILE: whatsapp-ai-agent-demo/app/services/dn_analysis.py
# ==================================================================================================
"""Delivery Note analytics backed exclusively by PostgreSQL."""

# ==================================================================================================
# BLOCK 1: IMPORTS AND DATABASE SETUP
# ==================================================================================================

from __future__ import annotations

import logging
import os
import re
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any, Callable, Iterator, Optional, TypeVar, cast

from sqlalchemy import exc, text
from sqlalchemy.orm import Session

try:
    from app.database import SessionLocal
except ImportError:
    SessionLocal = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

DEBUG_MODE = os.environ.get("DN_DEBUG_MODE", "false").lower() == "true"
PRODUCTION_MODE = os.environ.get("DN_PRODUCTION_MODE", "true").lower() == "true"
CONNECTION_RETRY_COUNT = int(os.environ.get("DN_CONNECTION_RETRY", "3"))

Row = dict[str, Any]
Result = dict[str, Any]
F = TypeVar("F", bound=Callable[..., Any])


# ==================================================================================================
# BLOCK 2: BUSINESS DATA MODELS
# ==================================================================================================

@dataclass
class DNAggregate:
    """Business data aggregated across every row belonging to one DN."""

    dn_no: str
    dealer_name: str = "Unknown"
    warehouse: str = "Unknown"
    city: str = "Unknown"
    sales_manager: Optional[str] = None
    sales_office: Optional[str] = None
    division: Optional[str] = None

    total_units: int = 0
    total_revenue: Decimal = Decimal(0)
    material_count: int = 0
    model_count: int = 0
    row_count: int = 0

    average_revenue: Decimal = Decimal(0)
    average_unit_price: Decimal = Decimal(0)

    dn_create_date: Optional[date] = None
    good_issue_date: Optional[date] = None
    pod_date: Optional[date] = None

    products: list[Row] = field(default_factory=list)

    delivery_aging_days: int = 0
    pod_aging_days: int = 0
    total_cycle_days: int = 0

    calculated_stage: str = "Unknown"
    calculated_emoji: str = "❓"

    pgi_status: str = "Unknown"
    pod_status: str = "Unknown"

    pending_flag: bool = True
    pending_flag_text: str = "Yes"


@dataclass
class DNDashboard:
    """Business-only DN dashboard with native WhatsApp rendering."""

    dn_no: str
    dealer_name: str
    warehouse: str
    city: str
    sales_manager: Optional[str]
    sales_office: Optional[str]

    model_count: int
    material_count: int
    total_units: int
    total_revenue: Decimal

    dn_create_date: str
    good_issue_date: str
    pod_date: str

    delivery_aging_text: str
    pod_aging_text: str
    total_cycle_text: str

    delivery_status: str
    pgi_status: str
    pod_status: str
    pending_flag_text: str

    products: list[Row]

    @staticmethod
    def _format_money(value: Any) -> str:
        """Format money without exposing Python Decimal syntax."""
        return f"{safe_decimal(value):,.2f}"

    def to_whatsapp_message(self) -> str:
        """Render a clean business-only WhatsApp message."""
        product_sections: list[str] = []

        for product in self.products:
            product_sections.append(
                "\n".join(
                    (
                        f"Model: {safe_string(product.get('model')) or 'N/A'}",
                        (
                            "Material No: "
                            f"{safe_string(product.get('material_no')) or 'N/A'}"
                        ),
                        f"Quantity: {safe_int(product.get('quantity')):,}",
                        (
                            "Revenue: "
                            f"{self._format_money(product.get('revenue'))}"
                        ),
                    )
                )
            )

        product_details = (
            "\n\n".join(product_sections)
            if product_sections
            else "N/A"
        )

        return "\n".join(
            (
                "📦 Delivery Note",
                self.dn_no,
                "",
                "🏢 Dealer",
                self.dealer_name,
                "",
                "📍 Location",
                f"Warehouse: {self.warehouse}",
                f"City: {self.city}",
                "",
                "👤 Sales",
                f"Sales Manager: {self.sales_manager or 'N/A'}",
                f"Sales Office: {self.sales_office or 'N/A'}",
                "",
                "📦 Products",
                f"Model Count: {self.model_count:,}",
                f"Material Count: {self.material_count:,}",
                f"Total Units: {self.total_units:,}",
                f"Total Revenue: {self._format_money(self.total_revenue)}",
                "",
                "📅 Timeline",
                f"DN Create Date: {self.dn_create_date}",
                f"PGI Date: {self.good_issue_date}",
                f"POD Date: {self.pod_date}",
                "",
                "⏱ Performance",
                f"Delivery Days: {self.delivery_aging_text}",
                f"POD Days: {self.pod_aging_text}",
                f"Total Cycle Days: {self.total_cycle_text}",
                "",
                "🚚 Status",
                f"Delivery Status: {self.delivery_status}",
                f"PGI Status: {self.pgi_status}",
                f"POD Status: {self.pod_status}",
                f"Pending Flag: {self.pending_flag_text}",
                "",
                "📋 Product Details",
                product_details,
            )
        )

    def __str__(self) -> str:
        """Use WhatsApp formatting when converted to a string."""
        return self.to_whatsapp_message()

    def __repr__(self) -> str:
        """Prevent the dataclass representation leaking to WhatsApp."""
        return self.to_whatsapp_message()


# ==================================================================================================
# BLOCK 3: SAFE CONVERSION AND FORMATTING HELPERS
# ==================================================================================================

def safe_decimal(value: Any) -> Decimal:
    """Convert a database value to Decimal without raising."""
    try:
        if value is None:
            return Decimal(0)

        if isinstance(value, Decimal):
            return value

        if isinstance(value, (int, float)):
            return Decimal(str(value))

        if isinstance(value, str):
            cleaned = re.sub(r"[^\d.-]", "", value.strip())
            return Decimal(cleaned) if cleaned else Decimal(0)

    except (InvalidOperation, ValueError, TypeError):
        pass

    return Decimal(0)


def safe_int(value: Any) -> int:
    """Convert a database value to an integer without raising."""
    try:
        if value is None:
            return 0

        if isinstance(value, int):
            return value

        if isinstance(value, (float, Decimal)):
            return int(value)

        if isinstance(value, str):
            cleaned = re.sub(r"[^\d.-]", "", value.strip())
            return int(Decimal(cleaned)) if cleaned else 0

    except (InvalidOperation, ValueError, TypeError):
        pass

    return 0


def safe_string(value: Any) -> Optional[str]:
    """Convert a value to a stripped string, treating blanks as missing."""
    if value is None:
        return None

    result = value.strip() if isinstance(value, str) else str(value).strip()
    return result or None


def safe_date(value: Any) -> Optional[date]:
    """Convert common database date values to date."""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None

    return None


def format_date(date_value: Any) -> str:
    """Format a date for WhatsApp display."""
    parsed = safe_date(date_value)
    return parsed.isoformat() if parsed else "N/A"


def format_aging_text(days: int) -> str:
    """Format elapsed days for WhatsApp display."""
    if days < 0:
        return f"{abs(days)} Days (Data Error)"

    if days == 0:
        return "Same Day"

    if days == 1:
        return "1 Day"

    if days < 7:
        return f"{days} Days"

    if days < 14:
        return f"{days} Days (1-2 Weeks)"

    if days < 30:
        return f"{days} Days ({days // 7} Weeks)"

    if days < 60:
        return f"{days} Days (1-2 Months)"

    if days < 90:
        return f"{days} Days (3 Months)"

    if days < 365:
        return f"{days} Days ({days // 30} Months)"

    years, remaining_days = divmod(days, 365)
    months = remaining_days // 30
    suffix = f"{years}Y {months}M" if months else f"{years}Y"

    return f"{days} Days ({suffix})"


def calculate_days(start: Any, end: Any) -> int:
    """Return non-negative elapsed days or zero for incomplete dates."""
    start_date = safe_date(start)
    end_date = safe_date(end)

    if start_date is None or end_date is None:
        return 0

    return max(0, (end_date - start_date).days)


def normalize_dn(dn_no: str) -> str:
    """Remove non-numeric characters from a DN number."""
    return re.sub(r"[^0-9]", "", dn_no.strip()) if dn_no else ""


def validate_dn(dn_no: str) -> tuple[bool, str, Optional[str]]:
    """Validate and normalize a DN number."""
    if not dn_no:
        return False, "", "DN number required"

    normalized = normalize_dn(dn_no)

    if not normalized:
        return False, "", "DN must contain numeric characters"

    if len(normalized) < 8:
        return (
            False,
            normalized,
            f"DN must be at least 8 digits (got {len(normalized)})",
        )

    if len(normalized) > 12:
        return (
            False,
            normalized,
            f"DN cannot exceed 12 digits (got {len(normalized)})",
        )

    return True, normalized, None


# ==================================================================================================
# BLOCK 4: SERVICE DECORATORS
# ==================================================================================================

def timed_execution(func: F) -> F:
    """Record service execution statistics."""

    @wraps(func)
    def wrapper(
        self: "DNAnalysisService",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        started = time.perf_counter()

        try:
            return func(self, *args, **kwargs)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._total_execution_time_ms += elapsed_ms
            self._query_count += 1

            if self._debug_mode:
                logger.debug(
                    "%s executed in %.2fms",
                    func.__name__,
                    elapsed_ms,
                )

    return cast(F, wrapper)


def handle_errors(func: F) -> F:
    """Convert unexpected service errors into the established response."""

    @wraps(func)
    def wrapper(
        self: "DNAnalysisService",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        try:
            return func(self, *args, **kwargs)

        except Exception as error:
            logger.error("Error in %s: %s", func.__name__, error)

            if self._debug_mode:
                logger.error(traceback.format_exc())

            return {
                "success": False,
                "error": str(error),
                "message": "Service encountered an error. Please try again.",
            }

    return cast(F, wrapper)


# ==================================================================================================
# BLOCK 5: DN ANALYSIS SERVICE
# ==================================================================================================

class DNAnalysisService:
    """Read and aggregate Delivery Note business data from PostgreSQL."""

    def __init__(self) -> None:
        self._service_name = "dn_analysis"
        self._version = "16.1"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0.0
        self._startup_time = datetime.now().isoformat()
        self._debug_mode = DEBUG_MODE
        self._production_mode = PRODUCTION_MODE

        self._initialized = self._test_connection()
        self._status = "READY" if self._initialized else "ERROR"

    # ==============================================================================================
    # BLOCK 6: DATABASE CONNECTION AND QUERY EXECUTION
    # ==============================================================================================

    def _test_connection(self) -> bool:
        """Test database connectivity with bounded retries."""
        for attempt in range(1, CONNECTION_RETRY_COUNT + 1):
            try:
                with self._get_session_context() as session:
                    session.execute(text("SELECT 1"))

                return True

            except Exception as error:
                logger.warning(
                    "Connection attempt %s/%s failed: %s",
                    attempt,
                    CONNECTION_RETRY_COUNT,
                    error,
                )

                if attempt < CONNECTION_RETRY_COUNT:
                    time.sleep(1)

        return False

    @contextmanager
    def _get_session_context(self) -> Iterator[Session]:
        """Provide a transaction-safe database session."""
        if SessionLocal is None:
            raise RuntimeError("SessionLocal not available")

        session = SessionLocal()

        try:
            yield session

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def _get_session(self) -> Optional[Session]:
        """Create a database session while preserving the legacy API."""
        if SessionLocal is None:
            logger.error("SessionLocal not available")
            return None

        try:
            return SessionLocal()

        except Exception as error:
            logger.error("Failed to get database session: %s", error)
            return None

    @timed_execution
    def _execute_query(
        self,
        query: str,
        params: Optional[dict[str, Any]] = None,
    ) -> list[Row]:
        """Execute SQL and return mapping rows."""
        session = self._get_session()

        if session is None:
            return []

        try:
            result = session.execute(text(query), params or {})
            return [dict(row) for row in result.mappings()]

        except exc.SQLAlchemyError as error:
            logger.error("SQL execution failed: %s", error)
            return []

        finally:
            session.close()

    # ==============================================================================================
    # BLOCK 7: BUSINESS-ONLY DN SEARCH ENGINE
    # ==============================================================================================

    def _build_search_query(self) -> str:
        """Build the business-only DN query used by WhatsApp."""
        return """
            SELECT
                dn_no,
                customer_name,
                warehouse,
                ship_to_city,
                sales_manager,
                sales_office,
                division,
                customer_model,
                material_no,
                dn_qty,
                dn_amount,
                dn_create_date,
                good_issue_date,
                pod_date,
                delivery_status,
                pgi_status,
                pod_status,
                pending_flag
            FROM delivery_reports
            WHERE dn_no = :dn_no
            ORDER BY
                customer_model ASC,
                material_no ASC
        """

    def _build_fallback_query(self) -> str:
        """Build the business-only similar-DN lookup."""
        return """
            SELECT DISTINCT
                dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            ORDER BY dn_no
            LIMIT 10
        """

    @handle_errors
    def get_dn_complete_info(self, dn_no: str) -> Result:
        """Fetch, aggregate, and format one complete DN."""
        is_valid, normalized_dn, error_message = validate_dn(dn_no)

        if not is_valid:
            return {
                "success": False,
                "error": error_message,
            }

        rows = self._execute_query(
            self._build_search_query(),
            {"dn_no": normalized_dn},
        )

        if not rows:
            similar_rows = self._execute_query(
                self._build_fallback_query(),
                {"dn_no": normalized_dn},
            )

            return {
                "success": False,
                "error": f"DN {normalized_dn} not found",
                "similar_dns": [
                    str(row["dn_no"])
                    for row in similar_rows
                ],
            }

        aggregated = self._aggregate_dn_data(rows)
        dashboard = self._build_dashboard(aggregated)

        return {
            "success": True,
            "data": dashboard,
            "whatsapp_message": dashboard.to_whatsapp_message(),
            "all_rows": rows,
        }

    # ==============================================================================================
    # BLOCK 8: SINGLE-PASS DN AGGREGATION ENGINE
    # ==============================================================================================

    def _aggregate_dn_data(
        self,
        rows: list[Row],
    ) -> DNAggregate:
        """Aggregate every row for one DN in a single O(n) pass."""
        if not rows:
            return DNAggregate(dn_no="")

        first = rows[0]

        models: set[str] = set()
        materials: set[str] = set()
        product_map: dict[tuple[str, str], Row] = {}

        total_units = 0
        total_revenue = Decimal(0)

        dn_create_date: Optional[date] = None
        good_issue_date: Optional[date] = None
        pod_date: Optional[date] = None

        for row in rows:
            model = safe_string(row.get("customer_model"))
            material_no = safe_string(row.get("material_no"))
            quantity = safe_int(row.get("dn_qty"))
            revenue = safe_decimal(row.get("dn_amount"))

            if model:
                models.add(model)

            if material_no:
                materials.add(material_no)

            total_units += quantity
            total_revenue += revenue

            product_key = (
                model or "N/A",
                material_no or "N/A",
            )

            product = product_map.get(product_key)

            if product is None:
                product_map[product_key] = {
                    "model": product_key[0],
                    "material_no": product_key[1],
                    "quantity": quantity,
                    "revenue": revenue,
                    "average_price": (
                        revenue / quantity
                        if quantity
                        else Decimal(0)
                    ),
                }

            else:
                product_quantity = (
                    safe_int(product.get("quantity"))
                    + quantity
                )
                product_revenue = (
                    safe_decimal(product.get("revenue"))
                    + revenue
                )

                product["quantity"] = product_quantity
                product["revenue"] = product_revenue
                product["average_price"] = (
                    product_revenue / product_quantity
                    if product_quantity
                    else Decimal(0)
                )

            row_create_date = safe_date(
                row.get("dn_create_date")
            )
            row_issue_date = safe_date(
                row.get("good_issue_date")
            )
            row_pod_date = safe_date(
                row.get("pod_date")
            )

            if row_create_date and (
                dn_create_date is None
                or row_create_date < dn_create_date
            ):
                dn_create_date = row_create_date

            if row_issue_date and (
                good_issue_date is None
                or row_issue_date > good_issue_date
            ):
                good_issue_date = row_issue_date

            if row_pod_date and (
                pod_date is None
                or row_pod_date > pod_date
            ):
                pod_date = row_pod_date

        products = sorted(
            product_map.values(),
            key=lambda product: (
                product["model"],
                product["material_no"],
            ),
        )

        row_count = len(rows)

        fallback_stage, calculated_emoji = self._calculate_stage(
            good_issue_date,
            pod_date,
        )

        # PostgreSQL remains authoritative. Derived values are only
        # used if a database status is NULL or blank.
        delivery_status = (
            safe_string(first.get("delivery_status"))
            or fallback_stage
        )

        pgi_status = (
            safe_string(first.get("pgi_status"))
            or ("Completed" if good_issue_date else "Pending")
        )

        pod_status = (
            safe_string(first.get("pod_status"))
            or ("Completed" if pod_date else "Pending")
        )

        raw_pending_flag = first.get("pending_flag")

        if raw_pending_flag is None:
            pending_flag = (
                good_issue_date is None
                or pod_date is None
            )

        elif isinstance(raw_pending_flag, str):
            pending_flag = raw_pending_flag.strip().lower() in {
                "1",
                "true",
                "yes",
                "y",
                "pending",
            }

        else:
            pending_flag = bool(raw_pending_flag)

        return DNAggregate(
            dn_no=safe_string(first.get("dn_no")) or "",
            dealer_name=(
                safe_string(first.get("customer_name"))
                or "Unknown"
            ),
            warehouse=(
                safe_string(first.get("warehouse"))
                or "Unknown"
            ),
            city=(
                safe_string(first.get("ship_to_city"))
                or "Unknown"
            ),
            sales_manager=safe_string(
                first.get("sales_manager")
            ),
            sales_office=safe_string(
                first.get("sales_office")
            ),
            division=safe_string(
                first.get("division")
            ),
            total_units=total_units,
            total_revenue=total_revenue,
            material_count=len(materials),
            model_count=len(models),
            row_count=row_count,
            average_revenue=(
                total_revenue / row_count
                if row_count
                else Decimal(0)
            ),
            average_unit_price=(
                total_revenue / total_units
                if total_units
                else Decimal(0)
            ),
            dn_create_date=dn_create_date,
            good_issue_date=good_issue_date,
            pod_date=pod_date,
            products=products,
            delivery_aging_days=calculate_days(
                dn_create_date,
                good_issue_date,
            ),
            pod_aging_days=calculate_days(
                good_issue_date,
                pod_date,
            ),
            total_cycle_days=calculate_days(
                dn_create_date,
                pod_date,
            ),
            calculated_stage=delivery_status,
            calculated_emoji=calculated_emoji,
            pgi_status=pgi_status,
            pod_status=pod_status,
            pending_flag=pending_flag,
            pending_flag_text=(
                "Yes" if pending_flag else "No"
            ),
        )

    @staticmethod
    def _calculate_stage(
        good_issue_date: Optional[date],
        pod_date: Optional[date],
    ) -> tuple[str, str]:
        """Derive a delivery stage when the database status is blank."""
        if pod_date is not None:
            return "Delivered", "✅"

        if good_issue_date is not None:
            return "In Transit", "🚚"

        return "Pending Dispatch", "⏳"

    # ==============================================================================================
    # BLOCK 9: BUSINESS-ONLY WHATSAPP DASHBOARD BUILDER
    # ==============================================================================================

    def _build_dashboard(
        self,
        aggregated: DNAggregate,
    ) -> DNDashboard:
        """Build the business-only WhatsApp dashboard."""
        return DNDashboard(
            dn_no=aggregated.dn_no,
            dealer_name=aggregated.dealer_name,
            warehouse=aggregated.warehouse,
            city=aggregated.city,
            sales_manager=aggregated.sales_manager,
            sales_office=aggregated.sales_office,
            model_count=aggregated.model_count,
            material_count=aggregated.material_count,
            total_units=aggregated.total_units,
            total_revenue=aggregated.total_revenue,
            dn_create_date=format_date(
                aggregated.dn_create_date
            ),
            good_issue_date=format_date(
                aggregated.good_issue_date
            ),
            pod_date=format_date(
                aggregated.pod_date
            ),
            delivery_aging_text=format_aging_text(
                aggregated.delivery_aging_days
            ),
            pod_aging_text=format_aging_text(
                aggregated.pod_aging_days
            ),
            total_cycle_text=format_aging_text(
                aggregated.total_cycle_days
            ),
            delivery_status=aggregated.calculated_stage,
            pgi_status=aggregated.pgi_status,
            pod_status=aggregated.pod_status,
            pending_flag_text=aggregated.pending_flag_text,
            products=aggregated.products,
        )

    # ==============================================================================================
    # BLOCK 10: PUBLIC API AND WORKFLOW METHODS
    # ==============================================================================================

    @handle_errors
    @timed_execution
    def get_pending_dns(self) -> Result:
        """Return DNs whose delivery lifecycle is incomplete."""
        query = """
            SELECT DISTINCT
                dn_no,
                customer_name,
                dn_create_date,
                delivery_status
            FROM delivery_reports
            WHERE
                good_issue_date IS NULL
                OR pod_date IS NULL
            ORDER BY dn_create_date DESC
        """

        rows = self._execute_query(query)

        return {
            "success": True,
            "count": len(rows),
            "records": rows,
        }

    @handle_errors
    @timed_execution
    def get_pending_pgi(self) -> Result:
        """Return DNs without a goods issue date."""
        query = """
            SELECT DISTINCT
                dn_no,
                customer_name,
                dn_create_date
            FROM delivery_reports
            WHERE good_issue_date IS NULL
            ORDER BY dn_create_date DESC
        """

        rows = self._execute_query(query)

        return {
            "success": True,
            "count": len(rows),
            "records": rows,
        }

    @handle_errors
    @timed_execution
    def get_pending_pod(self) -> Result:
        """Return issued DNs without proof of delivery."""
        query = """
            SELECT DISTINCT
                dn_no,
                customer_name,
                good_issue_date
            FROM delivery_reports
            WHERE
                good_issue_date IS NOT NULL
                AND pod_date IS NULL
            ORDER BY good_issue_date DESC
        """

        rows = self._execute_query(query)

        return {
            "success": True,
            "count": len(rows),
            "records": rows,
        }

    def get_dn_dashboard(self, dn_no: str) -> Result:
        """Return the complete business dashboard for a DN."""
        return self.get_dn_complete_info(dn_no)

    def search_dn(self, dn_no: str) -> Result:
        """Search for a DN."""
        return self.get_dn_complete_info(dn_no)

    def verify_dn(self, dn_no: str) -> Result:
        """Report whether a DN exists."""
        result = self.get_dn_complete_info(dn_no)

        return {
            "success": True,
            "exists": result.get("success", False),
        }

    def health_check(self) -> Result:
        """Return service and database health information."""
        try:
            with self._get_session_context() as session:
                started = time.perf_counter()

                row = session.execute(
                    text(
                        "SELECT COUNT(dn_no) "
                        "FROM delivery_reports"
                    )
                ).fetchone()

                latency_ms = (
                    time.perf_counter() - started
                ) * 1000

            return {
                "healthy": True,
                "service": self._service_name,
                "version": self._version,
                "status": self._status,
                "database": "connected",
                "rows": row[0] if row else 0,
                "latency_ms": round(latency_ms, 2),
                "query_count": self._query_count,
                "total_execution_time_ms": round(
                    self._total_execution_time_ms,
                    2,
                ),
                "initialized": self._initialized,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as error:
            return {
                "healthy": False,
                "service": self._service_name,
                "version": self._version,
                "status": self._status,
                "database": "disconnected",
                "error": str(error),
                "timestamp": datetime.now().isoformat(),
            }

    def validation_query(self) -> Result:
        """Return the number of distinct DNs."""
        try:
            with self._get_session_context() as session:
                row = session.execute(
                    text(
                        "SELECT COUNT(DISTINCT dn_no) "
                        "FROM delivery_reports"
                    )
                ).fetchone()

            return {
                "success": True,
                "records": row[0] if row else 0,
                "error": None,
            }

        except Exception as error:
            return {
                "success": False,
                "records": 0,
                "error": str(error),
            }

    def get_service_metadata(self) -> Result:
        """Return compatibility metadata for the provider service."""
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": self._status,
            "initialized": self._initialized,
            "startup_time": self._startup_time,
            "debug_mode": self._debug_mode,
            "production_mode": self._production_mode,
        }


# ==================================================================================================
# BLOCK 11: THREAD-SAFE SERVICE SINGLETON
# ==================================================================================================

_dn_analytics_service: Optional[DNAnalysisService] = None
_dn_lock = threading.Lock()


def get_dn_analytics_service() -> DNAnalysisService:
    """Return the process-wide thread-safe service instance."""
    global _dn_analytics_service

    if _dn_analytics_service is None:
        with _dn_lock:
            if _dn_analytics_service is None:
                _dn_analytics_service = DNAnalysisService()

    return _dn_analytics_service


# ==================================================================================================
# BLOCK 12: MODULE EXPORTS
# ==================================================================================================

__all__ = [
    "DNAnalysisService",
    "DNAggregate",
    "DNDashboard",
    "get_dn_analytics_service",
]


# ==================================================================================================
# END OF FILE: whatsapp-ai-agent-demo/app/services/dn_analysis.py
# ==================================================================================================
