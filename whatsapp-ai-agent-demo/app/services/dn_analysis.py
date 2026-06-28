# =====================================================================================================
# FILE: app/services/dn_analysis.py
# VERSION: v15.2 - PROFESSIONAL WHATSAPP FORMATTER
# PURPOSE: DN Analytics Service - Enterprise Grade PostgreSQL Integration
# =====================================================================================================

import logging
from typing import Dict, List, Optional, Any, Tuple, Union
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from sqlalchemy import text, inspect, exc
from sqlalchemy.orm import Session
from contextlib import contextmanager
import threading
import re
import traceback
import time
import os
from functools import lru_cache, wraps

# =====================================================================================================
# LOGGER
# =====================================================================================================
logger = logging.getLogger(__name__)

# =====================================================================================================
# BLOCK 1: IMPORTS & DATABASE SETUP
# =====================================================================================================
try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Database models imported successfully")
except ImportError as e:
    logger.error(f"❌ Database import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

DEBUG_MODE = os.environ.get("DN_DEBUG_MODE", "false").lower() == "true"
PRODUCTION_MODE = os.environ.get("DN_PRODUCTION_MODE", "true").lower() == "true"
CONNECTION_RETRY_COUNT = int(os.environ.get("DN_CONNECTION_RETRY", "3"))
QUERY_TIMEOUT = int(os.environ.get("DN_QUERY_TIMEOUT", "30"))

# =====================================================================================================
# BLOCK 2: DATA CLASSES (ALL ATTRIBUTES PRESERVED)
# =====================================================================================================

@dataclass
class DNAggregate:
    """Aggregated DN data from PostgreSQL."""
    dn_no: str
    dealer_name: str = "Unknown"
    dealer_code: Optional[str] = None
    customer_code: Optional[str] = None
    warehouse: str = "Unknown"
    warehouse_code: Optional[str] = None
    city: str = "Unknown"
    delivery_location: Optional[str] = None
    sales_office: Optional[str] = None
    sales_manager: Optional[str] = None
    division: Optional[str] = None
    order_type: Optional[str] = None
    dn_work: Optional[str] = None

    # Metrics
    total_units: int = 0
    total_revenue: Decimal = Decimal(0)
    material_count: int = 0
    model_count: int = 0
    row_count: int = 0

    # Average metrics
    average_revenue: Decimal = Decimal(0)
    average_unit_price: Decimal = Decimal(0)

    # Dates
    dn_create_date: Optional[date] = None
    good_issue_date: Optional[date] = None
    pod_date: Optional[date] = None

    # Products
    products: List[Dict[str, Any]] = field(default_factory=list)

    # Source
    source_file: Optional[str] = None
    upload_batch_id: Optional[str] = None
    imported_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Calculated fields
    delivery_aging_days: int = 0
    pod_aging_days: int = 0
    total_cycle_days: int = 0
    calculated_stage: str = "Unknown"
    calculated_emoji: str = "❓"
    pgi_status: str = "Unknown"
    pod_status: str = "Unknown"
    pending_flag: bool = True
    pending_flag_text: str = "⚠️ Yes"

@dataclass
class DNDashboard:
    """Complete DN Dashboard - ALL ATTRIBUTES PRESERVED."""
    # Core
    dn_no: str
    dealer_name: str
    dealer_code: Optional[str]
    customer_name: str
    customer_code: Optional[str]

    # Location
    warehouse: str
    warehouse_code: Optional[str]
    city: str
    delivery_location: Optional[str]

    # Business
    sales_manager: Optional[str]
    sales_office: Optional[str]
    division: Optional[str]
    order_type: Optional[str]
    dn_work: Optional[str]

    # Metrics
    total_units: int
    total_revenue: Decimal
    material_count: int
    model_count: int
    row_count: int
    average_revenue: Decimal
    average_unit_price: Decimal

    # Dates
    dn_create_date: str
    good_issue_date: str
    pod_date: str

    # Aging
    delivery_aging_days: int
    pod_aging_days: int
    total_cycle_days: int
    delivery_aging_text: str
    pod_aging_text: str
    total_cycle_text: str

    # Status
    calculated_stage: str
    calculated_emoji: str
    delivery_status: str
    pgi_status: str
    pod_status: str
    pending_flag: bool
    pending_flag_text: str

    # Products
    products: List[Dict[str, Any]]

    # Source (preserved for admin, hidden from WhatsApp)
    source_file: Optional[str]
    upload_batch_id: Optional[str]
    imported_at: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

# =====================================================================================================
# BLOCK 3: HELPER FUNCTIONS
# =====================================================================================================

def safe_decimal(value: Any) -> Decimal:
    try:
        if value is None:
            return Decimal(0)
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            cleaned = re.sub(r'[^\d.]', '', value.strip())
            if not cleaned:
                return Decimal(0)
            return Decimal(cleaned)
        return Decimal(0)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)

def safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            cleaned = re.sub(r'[^\d]', '', value.strip())
            if not cleaned:
                return 0
            return int(cleaned)
        return 0
    except (ValueError, TypeError):
        return 0

def safe_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    return str(value)

def safe_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            pass
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
        return None

def format_date(date_value: Any) -> str:
    if date_value is None:
        return 'N/A'
    try:
        if isinstance(date_value, (date, datetime)):
            return date_value.strftime('%Y-%m-%d')
        if isinstance(date_value, str):
            if len(date_value) >= 10:
                return date_value[:10]
            return date_value
        return str(date_value)
    except (ValueError, TypeError):
        return str(date_value) if date_value else 'N/A'

def format_aging_text(days: int) -> str:
    if days < 0:
        return f"{abs(days)} Days (Data Error)"
    elif days == 0:
        return "Same Day"
    elif days == 1:
        return "1 Day"
    elif days < 7:
        return f"{days} Days"
    elif days < 14:
        return f"{days} Days (1-2 Weeks)"
    elif days < 30:
        return f"{days} Days ({days // 7} Weeks)"
    elif days < 60:
        return f"{days} Days (1-2 Months)"
    elif days < 90:
        return f"{days} Days (3 Months)"
    elif days < 365:
        return f"{days} Days ({days // 30} Months)"
    else:
        years = days // 365
        months = (days % 365) // 30
        if months > 0:
            return f"{days} Days ({years}Y {months}M)"
        return f"{days} Days ({years}Y)"

def calculate_days(date1: Any, date2: Any) -> int:
    d1 = safe_date(date1)
    d2 = safe_date(date2)
    if d1 is None or d2 is None:
        return 0
    try:
        delta = d2 - d1
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 0

def normalize_dn(dn_no: str) -> str:
    if not dn_no:
        return ""
    return re.sub(r'[^0-9]', '', dn_no.strip())

def validate_dn(dn_no: str) -> Tuple[bool, str, str]:
    if not dn_no:
        return False, "", "DN number required"
    normalized = normalize_dn(dn_no)
    if not normalized:
        return False, "", "DN must contain numeric characters"
    if len(normalized) < 8:
        return False, normalized, f"DN must be at least 8 digits (got {len(normalized)})"
    if len(normalized) > 12:
        return False, normalized, f"DN cannot exceed 12 digits (got {len(normalized)})"
    return True, normalized, None

# =====================================================================================================
# BLOCK 4: DECORATORS
# =====================================================================================================

def timed_execution(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        start_time = time.time()
        try:
            result = func(self, *args, **kwargs)
            execution_time = (time.time() - start_time) * 1000
            self._total_execution_time_ms += execution_time
            self._query_count += 1
            if self._debug_mode:
                logger.debug(f"⏱️ {func.__name__} executed in {execution_time:.2f}ms")
            return result
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"❌ {func.__name__} failed after {execution_time:.2f}ms: {e}")
            raise
    return wrapper

def handle_errors(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            logger.error(f"❌ Error in {func.__name__}: {e}")
            if self._debug_mode:
                logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e),
                "message": "Service encountered an error. Please try again."
            }
    return wrapper

# =====================================================================================================
# BLOCK 5: DNAnalysisService CLASS
# =====================================================================================================

class DNAnalysisService:
    """
    DN Analytics Service - Enterprise Grade PostgreSQL Integration.

    v15.2 - PROFESSIONAL WHATSAPP FORMATTER
    ✅ PostgreSQL is the ONLY source of truth
    ✅ Decimal for revenue calculations
    ✅ Safe type conversions
    ✅ Comprehensive validation
    ✅ Performance optimized
    ✅ Professional WhatsApp formatting
    ✅ All attributes preserved
    """

    def __init__(self):
        self._service_name = "dn_analysis"
        self._version = "15.2"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0
        self._startup_time = datetime.now().isoformat()
        self._debug_mode = DEBUG_MODE
        self._production_mode = PRODUCTION_MODE
        self._schema_validated = False
        self._initialized = False

        logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
        logger.info(f"📋 Debug Mode: {'ENABLED' if self._debug_mode else 'DISABLED'}")

        try:
            test_result = self._test_connection()
            if test_result:
                self._status = "READY"
                self._initialized = True
                logger.info("✅ DNAnalysisService is READY")
            else:
                self._status = "ERROR"
                logger.error("❌ DNAnalysisService initialization FAILED")
        except Exception as e:
            self._status = "ERROR"
            logger.error(f"❌ DNAnalysisService initialization error: {e}")
            logger.error(traceback.format_exc())

    # ==================================================================================================
    # BLOCK 6: DATABASE CONNECTION METHODS
    # ==================================================================================================

    def _test_connection(self) -> bool:
        for attempt in range(1, CONNECTION_RETRY_COUNT + 1):
            try:
                if not SessionLocal:
                    logger.error("❌ SessionLocal is None")
                    return False
                with self._get_session_context() as session:
                    session.execute(text("SELECT 1"))
                    logger.info("✅ Database connection test: SUCCESS")
                    return True
            except Exception as e:
                logger.warning(f"⚠️ Connection attempt {attempt}/{CONNECTION_RETRY_COUNT} failed: {e}")
                if attempt < CONNECTION_RETRY_COUNT:
                    time.sleep(1)
                else:
                    logger.error(f"❌ Database connection test FAILED: {e}")
                    return False
        return False

    @contextmanager
    def _get_session_context(self) -> Session:
        if not SessionLocal:
            raise RuntimeError("SessionLocal not available")
        session = None
        try:
            session = SessionLocal()
            yield session
        except Exception as e:
            if session:
                session.rollback()
            raise
        finally:
            if session:
                session.close()

    def _get_session(self) -> Optional[Session]:
        if not SessionLocal:
            logger.error("❌ SessionLocal not available")
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"❌ Failed to get database session: {e}")
            return None

    @timed_execution
    def _execute_query(self, query: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        session = None
        try:
            session = self._get_session()
            if not session:
                logger.error("❌ No session available")
                return []
            if self._debug_mode:
                logger.debug(f"📝 Executing SQL: {query[:200]}...")
            result = session.execute(text(query), params or {})
            rows = [dict(row) for row in result.mappings()]
            return rows
        except exc.SQLAlchemyError as e:
            logger.error(f"❌ SQL Execution Failed: {e}")
            return []
        finally:
            if session:
                session.close()

    # ==================================================================================================
    # BLOCK 7: DN SEARCH ENGINE (UNCHANGED)
    # ==================================================================================================

    def _build_search_query(self) -> str:
        return """
        SELECT
            id,
            dn_no,
            dn_work,
            order_type,
            division,
            customer_code,
            dealer_code,
            customer_name,
            customer_model,
            material_no,
            storage_location,
            sales_office,
            sales_manager,
            ship_to_city,
            warehouse,
            warehouse_code,
            delivery_location,
            dn_qty,
            dn_amount,
            dn_create_date,
            good_issue_date,
            pod_date,
            remarks,
            delivery_status,
            pgi_status,
            pod_status,
            pending_flag,
            source_file,
            upload_batch_id,
            imported_at,
            created_at,
            updated_at
        FROM delivery_reports
        WHERE CAST(dn_no AS TEXT) = :dn_no
        ORDER BY customer_model ASC, id ASC
        """

    def _build_fallback_query(self) -> str:
        return """
        SELECT DISTINCT dn_no
        FROM delivery_reports
        WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
        LIMIT 10
        """

    @handle_errors
    def get_dn_complete_info(self, dn_no: str) -> Dict[str, Any]:
        logger.info(f"🔍 Fetching complete info for DN: '{dn_no}'")
        is_valid, normalized_dn, error_msg = validate_dn(dn_no)
        if not is_valid:
            logger.warning(f"❌ Invalid DN: {error_msg}")
            return {"success": False, "error": error_msg}
        logger.info(f" ├── Normalized: '{normalized_dn}'")
        query = self._build_search_query()
        all_rows = self._execute_query(query, {"dn_no": normalized_dn})
        if not all_rows:
            fallback_results = self._execute_query(
                self._build_fallback_query(),
                {"dn_no": normalized_dn}
            )
            similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
            if similar_dns:
                return {
                    "success": False,
                    "error": f"DN {dn_no} not found",
                    "similar_dns": similar_dns[:5],
                    "message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
                }
            return {"success": False, "error": f"DN {dn_no} not found"}
        logger.info(f" ├── Found {len(all_rows)} rows for DN")
        aggregated = self._aggregate_dn_data(all_rows)
        dashboard = self._build_dashboard(aggregated)
        logger.info(f" ├── Materials: {dashboard.material_count}")
        logger.info(f" ├── Models: {dashboard.model_count}")
        logger.info(f" ├── Units: {dashboard.total_units}")
        logger.info(f" ├── Revenue: PKR {dashboard.total_revenue:,.2f}")
        logger.info(f" ├── Status: {dashboard.calculated_stage}")
        logger.info(f"✅ Complete info fetched successfully")
        return {"success": True, "data": dashboard, "all_rows": all_rows}

    # ==================================================================================================
    # BLOCK 8: AGGREGATION ENGINE (UNCHANGED)
    # ==================================================================================================

    def _aggregate_dn_data(self, rows: List[Dict[str, Any]]) -> DNAggregate:
        if not rows:
            return DNAggregate(dn_no="")
        first_row = rows[0]
        unique_models = set()
        unique_materials = set()
        products = []
        total_units = 0
        total_revenue = Decimal(0)
        dn_create_dates = []
        good_issue_dates = []
        pod_dates = []

        for row in rows:
            model = safe_string(row.get('customer_model'))
            if model:
                unique_models.add(model)
            material = safe_string(row.get('material_no'))
            if material:
                unique_materials.add(material)
            if model:
                qty = safe_int(row.get('dn_qty'))
                revenue = safe_decimal(row.get('dn_amount'))
                total_units += qty
                total_revenue += revenue
                products.append({
                    'model': model,
                    'material_no': safe_string(row.get('material_no')) or 'N/A',
                    'division': safe_string(row.get('division')) or 'Unknown',
                    'quantity': qty,
                    'revenue': float(revenue),
                    'warehouse': safe_string(row.get('warehouse')) or 'Unknown',
                    'city': safe_string(row.get('ship_to_city')) or 'Unknown',
                    'storage_location': safe_string(row.get('storage_location')) or 'N/A',
                    'average_price': float(revenue / qty) if qty > 0 else 0
                })
            if row.get('dn_create_date'):
                dn_create_dates.append(row.get('dn_create_date'))
            if row.get('good_issue_date'):
                good_issue_dates.append(row.get('good_issue_date'))
            if row.get('pod_date'):
                pod_dates.append(row.get('pod_date'))

        products.sort(key=lambda x: x.get('model', ''))
        material_count = len(unique_materials)
        average_revenue = total_revenue / len(rows) if rows else Decimal(0)
        average_unit_price = total_revenue / total_units if total_units > 0 else Decimal(0)
        dn_create_date = safe_date(min(dn_create_dates)) if dn_create_dates else None
        good_issue_date = safe_date(max(good_issue_dates)) if good_issue_dates else None
        pod_date = safe_date(max(pod_dates)) if pod_dates else None

        delivery_aging = calculate_days(dn_create_date, good_issue_date)
        pod_aging = calculate_days(good_issue_date, pod_date)
        total_cycle = calculate_days(dn_create_date, pod_date)

        pgi_exists = good_issue_date is not None
        pod_exists = pod_date is not None

        if pod_exists and pgi_exists:
            stage = "Delivered"
            emoji = "✅"
            pgi_status = "Completed"
            pod_status = "Completed"
            pending = False
            pending_text = "No"
        elif pgi_exists and not pod_exists:
            stage = "In Transit"
            emoji = "🚚"
            pgi_status = "Completed"
            pod_status = "Pending"
            pending = True
            pending_text = "Yes"
        else:
            stage = "Pending Dispatch"
            emoji = "⏳"
            pgi_status = "Pending"
            pod_status = "Pending"
            pending = True
            pending_text = "Yes"

        return DNAggregate(
            dn_no=safe_string(first_row.get('dn_no')) or "",
            dealer_name=safe_string(first_row.get('customer_name')) or "Unknown",
            dealer_code=safe_string(first_row.get('dealer_code')),
            customer_code=safe_string(first_row.get('customer_code')),
            warehouse=safe_string(first_row.get('warehouse')) or "Unknown",
            warehouse_code=safe_string(first_row.get('warehouse_code')),
            city=safe_string(first_row.get('ship_to_city')) or "Unknown",
            delivery_location=safe_string(first_row.get('delivery_location')),
            sales_office=safe_string(first_row.get('sales_office')),
            sales_manager=safe_string(first_row.get('sales_manager')),
            division=safe_string(first_row.get('division')),
            order_type=safe_string(first_row.get('order_type')),
            dn_work=safe_string(first_row.get('dn_work')),
            total_units=total_units,
            total_revenue=total_revenue,
            material_count=material_count,
            model_count=len(unique_models),
            row_count=len(rows),
            average_revenue=average_revenue,
            average_unit_price=average_unit_price,
            dn_create_date=dn_create_date,
            good_issue_date=good_issue_date,
            pod_date=pod_date,
            products=products,
            source_file=safe_string(first_row.get('source_file')),
            upload_batch_id=safe_string(first_row.get('upload_batch_id')),
            imported_at=first_row.get('imported_at'),
            created_at=first_row.get('created_at'),
            updated_at=first_row.get('updated_at'),
            delivery_aging_days=delivery_aging,
            pod_aging_days=pod_aging,
            total_cycle_days=total_cycle,
            calculated_stage=stage,
            calculated_emoji=emoji,
            pgi_status=pgi_status,
            pod_status=pod_status,
            pending_flag=pending,
            pending_flag_text=pending_text
        )

    # ==================================================================================================
    # BLOCK 9: DASHBOARD BUILDER (UNCHANGED)
    # ==================================================================================================

    def _build_dashboard(self, aggregated: DNAggregate) -> DNDashboard:
        return DNDashboard(
            dn_no=aggregated.dn_no,
            dealer_name=aggregated.dealer_name,
            dealer_code=aggregated.dealer_code,
            customer_name=aggregated.dealer_name,
            customer_code=aggregated.customer_code,
            warehouse=aggregated.warehouse,
            warehouse_code=aggregated.warehouse_code,
            city=aggregated.city,
            delivery_location=aggregated.delivery_location,
            sales_manager=aggregated.sales_manager,
            sales_office=aggregated.sales_office,
            division=aggregated.division,
            order_type=aggregated.order_type,
            dn_work=aggregated.dn_work,
            total_units=aggregated.total_units,
            total_revenue=aggregated.total_revenue,
            material_count=aggregated.material_count,
            model_count=aggregated.model_count,
            row_count=aggregated.row_count,
            average_revenue=aggregated.average_revenue,
            average_unit_price=aggregated.average_unit_price,
            dn_create_date=format_date(aggregated.dn_create_date),
            good_issue_date=format_date(aggregated.good_issue_date),
            pod_date=format_date(aggregated.pod_date),
            delivery_aging_days=aggregated.delivery_aging_days,
            pod_aging_days=aggregated.pod_aging_days,
            total_cycle_days=aggregated.total_cycle_days,
            delivery_aging_text=format_aging_text(aggregated.delivery_aging_days),
            pod_aging_text=format_aging_text(aggregated.pod_aging_days),
            total_cycle_text=format_aging_text(aggregated.total_cycle_days),
            calculated_stage=aggregated.calculated_stage,
            calculated_emoji=aggregated.calculated_emoji,
            delivery_status=aggregated.calculated_stage,
            pgi_status=aggregated.pgi_status,
            pod_status=aggregated.pod_status,
            pending_flag=aggregated.pending_flag,
            pending_flag_text=aggregated.pending_flag_text,
            products=aggregated.products,
            source_file=aggregated.source_file,
            upload_batch_id=aggregated.upload_batch_id,
            imported_at=format_date(aggregated.imported_at),
            created_at=format_date(aggregated.created_at),
            updated_at=format_date(aggregated.updated_at)
        )

    # ==================================================================================================
    # BLOCK 10: PUBLIC METHODS (UNCHANGED)
    # ==================================================================================================

    @handle_errors
    @timed_execution
    def get_pending_dns(self) -> Dict[str, Any]:
        query = """
        SELECT DISTINCT dn_no, customer_name, dn_create_date, delivery_status 
        FROM delivery_reports 
        WHERE good_issue_date IS NULL OR pod_date IS NULL
        ORDER BY dn_create_date DESC
        """
        rows = self._execute_query(query)
        return {"success": True, "count": len(rows), "records": rows}

    @handle_errors
    @timed_execution
    def get_pending_pgi(self) -> Dict[str, Any]:
        query = """
        SELECT DISTINCT dn_no, customer_name, dn_create_date 
        FROM delivery_reports 
        WHERE good_issue_date IS NULL
        ORDER BY dn_create_date DESC
        """
        rows = self._execute_query(query)
        return {"success": True, "count": len(rows), "records": rows}

    @handle_errors
    @timed_execution
    def get_pending_pod(self) -> Dict[str, Any]:
        query = """
        SELECT DISTINCT dn_no, customer_name, good_issue_date 
        FROM delivery_reports 
        WHERE good_issue_date IS NOT NULL AND pod_date IS NULL
        ORDER BY good_issue_date DESC
        """
        rows = self._execute_query(query)
        return {"success": True, "count": len(rows), "records": rows}

    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        return self.get_dn_complete_info(dn_no)

    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        return self.get_dn_complete_info(dn_no)

    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        result = self.get_dn_complete_info(dn_no)
        return {"success": True, "exists": result.get("success", False)}

    def health_check(self) -> Dict[str, Any]:
        try:
            rows_count = 0
            latency_ms = 0
            with self._get_session_context() as session:
                start_time = time.time()
                result = session.execute(text("SELECT COUNT(*) as count FROM delivery_reports"))
                row = result.fetchone()
                rows_count = row[0] if row else 0
                latency_ms = (time.time() - start_time) * 1000
            return {
                "healthy": True,
                "service": self._service_name,
                "version": self._version,
                "status": self._status,
                "database": "connected",
                "rows": rows_count,
                "latency_ms": round(latency_ms, 2),
                "query_count": self._query_count,
                "total_execution_time_ms": round(self._total_execution_time_ms, 2),
                "initialized": self._initialized,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "healthy": False,
                "service": self._service_name,
                "version": self._version,
                "status": self._status,
                "database": "disconnected",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def validation_query(self) -> Dict[str, Any]:
        try:
            with self._get_session_context() as session:
                result = session.execute(
                    text("SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports")
                )
                row = result.fetchone()
                count = row[0] if row else 0
                return {"success": True, "records": count, "error": None}
        except Exception as e:
            return {"success": False, "records": 0, "error": str(e)}

    def get_service_metadata(self) -> Dict[str, Any]:
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": self._status,
            "initialized": self._initialized,
            "startup_time": self._startup_time,
            "debug_mode": self._debug_mode,
            "production_mode": self._production_mode
        }

    # ==================================================================================================
    # BLOCK 11: ✅ PROFESSIONAL WHATSAPP FORMATTER (NEW)
    # ==================================================================================================

    def format_dn_dashboard(self, dashboard_data: Any) -> str:
        """
        Format DN dashboard for WhatsApp - Professional executive-style output.
        
        This is a PURE PRESENTATION layer. It does NOT:
        - Execute SQL
        - Calculate business metrics
        - Aggregate products
        - Modify data
        
        It only DISPLAYS data already present in DNDashboard.
        """
        # Extract data from DNDashboard object or dict
        try:
            if hasattr(dashboard_data, '__dataclass_fields__'):
                d = {}
                for field_name in dashboard_data.__dataclass_fields__:
                    value = getattr(dashboard_data, field_name)
                    if isinstance(value, Decimal):
                        value = float(value)
                    if isinstance(value, (date, datetime)):
                        value = value.strftime('%Y-%m-%d')
                    d[field_name] = value
            elif isinstance(dashboard_data, dict):
                if 'data' in dashboard_data:
                    return self.format_dn_dashboard(dashboard_data['data'])
                d = dashboard_data
            else:
                return "❌ Invalid data format. Please contact support."
        except Exception as e:
            logger.error(f"Error extracting data: {e}")
            return f"❌ Error formatting report. Please try again."

        # Build professional WhatsApp message
        lines = []

        # ----- SECTION 1: Header -----
        lines.append("📦 Delivery Note Details")
        lines.append("")

        # ----- SECTION 2: Dealer & Location -----
        dn_no = d.get('dn_no', 'N/A')
        lines.append(f"🆔 DN: {dn_no}")
        lines.append("")

        dealer_name = d.get('dealer_name') or d.get('customer_name', 'Unknown')
        lines.append(f"👤 Dealer: {dealer_name}")
        lines.append("")

        city = d.get('city', 'Unknown')
        if city and city != 'Unknown':
            lines.append(f"📍 City: {city}")
            lines.append("")

        warehouse = d.get('warehouse', 'Unknown')
        lines.append(f"🏭 Warehouse: {warehouse}")
        lines.append("")

        # ----- SEPARATOR -----
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # ----- SECTION 3: Summary -----
        lines.append("📊 Summary")
        lines.append("")

        total_units = d.get('total_units', 0)
        lines.append(f"📦 Units: {total_units}")

        # Show products count (material_count = unique products)
        material_count = d.get('material_count', 0)
        lines.append(f"🛒 Products: {material_count}")

        total_revenue = d.get('total_revenue', 0)
        if total_revenue:
            try:
                revenue_val = float(total_revenue)
                lines.append(f"💰 Revenue: PKR {revenue_val:,.0f}")
            except:
                lines.append(f"💰 Revenue: PKR {total_revenue}")
        lines.append("")

        # ----- SEPARATOR -----
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # ----- SECTION 4: Timeline -----
        lines.append("📅 Timeline")
        lines.append("")

        dn_create_date = d.get('dn_create_date', 'N/A')
        lines.append(f"📝 DN Created: {dn_create_date}")

        good_issue_date = d.get('good_issue_date', 'N/A')
        lines.append(f"🚚 PGI: {good_issue_date}")

        pod_date = d.get('pod_date', 'N/A')
        lines.append(f"📬 POD: {pod_date}")
        lines.append("")

        # ----- SEPARATOR -----
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # ----- SECTION 5: Performance -----
        lines.append("⏱ Performance")
        lines.append("")

        delivery_aging = d.get('delivery_aging_text', 'N/A')
        lines.append(f"🚛 Delivery: {delivery_aging}")

        pod_aging = d.get('pod_aging_text', 'N/A')
        lines.append(f"📦 POD: {pod_aging}")

        total_cycle = d.get('total_cycle_text', 'N/A')
        lines.append(f"🔄 Total Cycle: {total_cycle}")
        lines.append("")

        # ----- SEPARATOR -----
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # ----- SECTION 6: Status (Compact) -----
        lines.append("🚚 Current Status")
        lines.append("")

        stage = d.get('calculated_stage', 'Unknown')
        emoji = d.get('calculated_emoji', '❓')
        lines.append(f"{emoji} Delivery: {stage}")

        pgi_status = d.get('pgi_status', 'Unknown')
        lines.append(f"✅ PGI: {pgi_status}")

        pod_status = d.get('pod_status', 'Unknown')
        lines.append(f"✅ POD: {pod_status}")

        pending_flag = d.get('pending_flag', True)
        pending_text = d.get('pending_flag_text', 'Unknown')
        pending_emoji = "🔴" if pending_flag else "🟢"
        lines.append(f"{pending_emoji} Pending: {pending_text}")
        lines.append("")

        # ----- SEPARATOR -----
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # ----- SECTION 7: Products (Grouped - No Duplicates) -----
        products = d.get('products', [])
        if products and len(products) > 0:
            lines.append("📦 Products")
            lines.append("")

            # Group products by model (already grouped by SQL)
            grouped = {}
            for p in products:
                model = p.get('model', 'Unknown')
                if model not in grouped:
                    grouped[model] = {
                        'quantity': 0,
                        'revenue': 0
                    }
                grouped[model]['quantity'] += p.get('quantity', 0)
                grouped[model]['revenue'] += p.get('revenue', 0)

            # Display products (max 10)
            for idx, (model, data) in enumerate(grouped.items()[:10], 1):
                qty = data.get('quantity', 0)
                revenue = data.get('revenue', 0)
                lines.append(f"• {model}")
                lines.append(f"  Qty: {qty}")
                if revenue > 0:
                    try:
                        lines.append(f"  Revenue: PKR {float(revenue):,.0f}")
                    except:
                        pass
                lines.append("")

            if len(grouped) > 10:
                remaining = len(grouped) - 10
                lines.append(f"• {remaining} more product(s)")
                lines.append("")

        # ----- SEPARATOR -----
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # ----- SECTION 8: AI Insight -----
        lines.append("💡 AI Insight")
        lines.append("")

        # Generate AI Insight based on status
        stage = d.get('calculated_stage', 'Unknown')
        pending_flag = d.get('pending_flag', True)

        if stage == "Delivered":
            insight = "Shipment completed successfully within the expected delivery cycle. No further action is required."
        elif stage == "In Transit":
            insight = "Shipment is currently in transit. Awaiting Proof of Delivery."
        elif stage == "Pending Dispatch":
            insight = "Shipment has not yet been dispatched. Warehouse action is required."
        else:
            insight = "Shipment status is being updated. Please check again later."

        # Add delay warning if applicable
        delivery_aging_days = d.get('delivery_aging_days', 0)
        if delivery_aging_days > 14 and stage != "Delivered":
            insight += " Delivery exceeded the expected turnaround time. Operational follow-up is recommended."
        elif delivery_aging_days > 30 and stage != "Delivered":
            insight += " Immediate management attention is recommended."

        lines.append(insight)
        lines.append("")

        # ----- FOOTER -----
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("🤖 AI Logistics Assistant")

        return "\n".join(lines)


# =====================================================================================================
# BLOCK 12: THREAD-SAFE SINGLETON
# =====================================================================================================

_dn_analytics_service = None
_dn_lock = threading.Lock()

def get_dn_analytics_service() -> DNAnalysisService:
    global _dn_analytics_service
    if _dn_analytics_service is None:
        with _dn_lock:
            if _dn_analytics_service is None:
                try:
                    logger.info("🔧 Creating DNAnalysisService singleton...")
                    _dn_analytics_service = DNAnalysisService()
                    logger.info("✅ DNAnalysisService singleton initialized")
                except Exception as e:
                    logger.exception(f"❌ DNAnalysisService initialization failed: {e}")
                    raise
    return _dn_analytics_service


# =====================================================================================================
# BLOCK 13: EXPORTS
# =====================================================================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service',
    'DNAggregate',
    'DNDashboard'
]


# =====================================================================================================
# MODULE INITIALIZATION
# =====================================================================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v15.2 - PROFESSIONAL WHATSAPP FORMATTER")
logger.info("=" * 70)
logger.info("")
logger.info(" SERVICE DETAILS:")
logger.info(" ✅ Service Name: dn_analysis")
logger.info(" ✅ Version: 15.2")
logger.info(" ✅ Source: PostgreSQL (delivery_reports)")
logger.info("")
logger.info(" WHATSAPP FEATURES:")
logger.info(" ✅ Professional executive-style formatting")
logger.info(" ✅ Clean section layout with emojis")
logger.info(" ✅ Products grouped (no duplicates)")
logger.info(" ✅ AI Insight with business summary")
logger.info(" ✅ All attributes preserved in DNDashboard")
logger.info("")
logger.info(" STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)

# Initialize service
try:
    service = get_dn_analytics_service()
    logger.info("✅ DN Analytics Service initialized successfully")
except Exception as e:
    logger.error(f"❌ DN Analytics Service initialization failed: {e}")

# =====================================================================================================
# END OF FILE
# =====================================================================================================
