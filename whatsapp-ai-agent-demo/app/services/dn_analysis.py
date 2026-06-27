======================================================================================================
FILE: app/services/dn_analysis.py
VERSION: v14.0 - ENTERPRISE PRODUCTION
======================================================================================================
PURPOSE: DN Analytics Service - Enterprise Grade PostgreSQL Integration
======================================================================================================
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

logger = logging.getLogger(name)

======================================================================================================
BLOCK 1: IMPORTS & DATABASE SETUP
======================================================================================================
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

======================================================================================================
BLOCK 2: DATA CLASSES
======================================================================================================
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

Metrics
total_units: int = 0
total_revenue: Decimal = Decimal(0)
material_count: int = 0
model_count: int = 0
row_count: int = 0

Average metrics
average_revenue: Decimal = Decimal(0)
average_unit_price: Decimal = Decimal(0)

Dates
dn_create_date: Optional[date] = None
good_issue_date: Optional[date] = None
pod_date: Optional[date] = None

Products
products: List[Dict[str, Any]] = field(default_factory=list)

Source
source_file: Optional[str] = None
upload_batch_id: Optional[str] = None
imported_at: Optional[datetime] = None
created_at: Optional[datetime] = None
updated_at: Optional[datetime] = None

Calculated fields
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
"""Complete DN Dashboard."""

Core
dn_no: str
dealer_name: str
dealer_code: Optional[str]
customer_name: str
customer_code: Optional[str]

Location
warehouse: str
warehouse_code: Optional[str]
city: str
delivery_location: Optional[str]

Business
sales_manager: Optional[str]
sales_office: Optional[str]
division: Optional[str]
order_type: Optional[str]
dn_work: Optional[str]

Metrics
total_units: int
total_revenue: Decimal
material_count: int
model_count: int
row_count: int
average_revenue: Decimal
average_unit_price: Decimal

Dates
dn_create_date: str
good_issue_date: str
pod_date: str

Aging
delivery_aging_days: int
pod_aging_days: int
total_cycle_days: int
delivery_aging_text: str
pod_aging_text: str
total_cycle_text: str

Status
calculated_stage: str
calculated_emoji: str
delivery_status: str
pgi_status: str
pod_status: str
pending_flag: bool
pending_flag_text: str

Products
products: List[Dict[str, Any]]

Source
source_file: Optional[str]
upload_batch_id: Optional[str]
imported_at: Optional[str]
created_at: Optional[str]
updated_at: Optional[str]

======================================================================================================
BLOCK 3: HELPER FUNCTIONS
======================================================================================================
def safe_decimal(value: Any) -> Decimal:
"""Safely convert value to Decimal."""
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
"""Safely convert value to int."""
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
"""Safely convert value to string."""
if value is None:
return None
if isinstance(value, str):
return value.strip()
return str(value)

def safe_date(value: Any) -> Optional[date]:
"""Safely convert value to date."""
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
"""Format date for display."""
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
"""Format aging days into human readable text."""
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
"""Calculate days between two dates."""
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
"""Normalize DN number - remove non-numeric characters."""
if not dn_no:
return ""
return re.sub(r'[^0-9]', '', dn_no.strip())

def validate_dn(dn_no: str) -> Tuple[bool, str, str]:
"""
Validate DN number.

Returns:
(is_valid, normalized_dn, error_message)
"""
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

======================================================================================================
BLOCK 4: DECORATORS
======================================================================================================
def timed_execution(func):
"""Decorator to measure execution time."""
@wraps(func)
def wrapper(self, *args, **kwargs):
start_time = time.time()
try:
result = func(self, *args, **kwargs)
execution_time = (time.time() - start_time) * 1000
self._total_execution_time_ms += execution_time
self._query_count += 1

if self._debug_mode:
logger.debug(f"⏱️ {func.name} executed in {execution_time:.2f}ms")

return result
except Exception as e:
execution_time = (time.time() - start_time) * 1000
logger.error(f"❌ {func.name} failed after {execution_time:.2f}ms: {e}")
raise
return wrapper

def handle_errors(func):
"""Decorator for graceful error handling."""
@wraps(func)
def wrapper(self, *args, **kwargs):
try:
return func(self, *args, **kwargs)
except Exception as e:
logger.error(f"❌ Error in {func.name}: {e}")
if self._debug_mode:
logger.error(traceback.format_exc())

Return error response
return {
"success": False,
"error": str(e),
"message": "Service encountered an error. Please try again."
}
return wrapper

======================================================================================================
BLOCK 5: DNAnalysisService CLASS
======================================================================================================
class DNAnalysisService:
"""
DN Analytics Service - Enterprise Grade PostgreSQL Integration.

v14.0 - ENTERPRISE PRODUCTION:

✅ PostgreSQL is the ONLY source of truth

✅ Decimal for revenue calculations

✅ Safe type conversions

✅ Comprehensive validation

✅ Performance optimized

✅ 100% backward compatible
"""

def init(self):
"""Initialize DN Analytics Service."""
self._service_name = "dn_analysis"
self._version = "14.0"
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

Test connection
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

==================================================================================================
BLOCK 6: DATABASE CONNECTION METHODS
==================================================================================================
def _test_connection(self) -> bool:
"""Test database connection with retry."""
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
"""Context manager for database session."""
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
"""Get database session."""
if not SessionLocal:
logger.error("❌ SessionLocal not available")
return None

try:
return SessionLocal()
except Exception as e:
logger.error(f"❌ Failed to get database session: {e}")
return None

def _execute_query(self, query: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
"""Execute raw SQL query and return results as dicts."""
start_time = time.time()
session = None
try:
session = self._get_session()
if not session:
logger.error("❌ No session available")
return []

if self._debug_mode:
logger.debug(f"📝 Executing SQL: {query[:200]}...")
logger.debug(f"📝 Parameters: {params}")

result = session.execute(text(query), params or {})
columns = result.keys()
rows = [dict(zip(columns, row)) for row in result.fetchall()]

execution_time_ms = (time.time() - start_time) * 1000
self._query_count += 1
self._total_execution_time_ms += execution_time_ms

if self._debug_mode:
logger.debug(f"✅ Query returned {len(rows)} rows in {execution_time_ms:.2f}ms")
return rows

except exc.SQLAlchemyError as e:
logger.error(f"❌ SQL Execution Failed: {e}")
return []
finally:
if session:
session.close()

==================================================================================================
BLOCK 7: DN SEARCH ENGINE
==================================================================================================
def _build_search_query(self) -> str:
"""Build optimized search query."""
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
"""Build fallback query for similar DNs."""
return """
SELECT DISTINCT dn_no
FROM delivery_reports
WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
LIMIT 10
"""

@handle_errors
def get_dn_complete_info(self, dn_no: str) -> Dict[str, Any]:
"""
Fetch COMPLETE information for a DN.

Returns:

success: bool

data: DNDashboard

all_rows: List[Dict]

error: str (if failed)
"""
logger.info(f"🔍 Fetching complete info for DN: '{dn_no}'")

Validate DN
is_valid, normalized_dn, error_msg = validate_dn(dn_no)
if not is_valid:
logger.warning(f"❌ Invalid DN: {error_msg}")
return {"success": False, "error": error_msg}

logger.info(f" ├── Normalized: '{normalized_dn}'")

Get ALL rows for this DN
query = self._build_search_query()
all_rows = self._execute_query(query, {"dn_no": normalized_dn})

if not all_rows:

Try fallback
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

Aggregate ALL data
aggregated = self._aggregate_dn_data(all_rows)

Build complete dashboard
dashboard = self._build_dashboard(aggregated)

logger.info(f" ├── Materials: {dashboard.material_count}")
logger.info(f" ├── Models: {dashboard.model_count}")
logger.info(f" ├── Units: {dashboard.total_units}")
logger.info(f" ├── Revenue: PKR {dashboard.total_revenue:,.2f}")
logger.info(f" ├── Status: {dashboard.calculated_stage}")
logger.info(f"✅ Complete info fetched successfully")

return {"success": True, "data": dashboard, "all_rows": all_rows}

==================================================================================================
BLOCK 8: AGGREGATION ENGINE
==================================================================================================
def _aggregate_dn_data(self, rows: List[Dict[str, Any]]) -> DNAggregate:
"""Aggregate ALL rows for a DN."""
if not rows:
return DNAggregate(dn_no="")

first_row = rows[0]

Collections
unique_models = set()
unique_materials = set()
products = []
total_units = 0
total_revenue = Decimal(0)
dn_create_dates = []
good_issue_dates = []
pod_dates = []

for row in rows:

Models
model = safe_string(row.get('customer_model'))
if model:
unique_models.add(model)

Materials
material = safe_string(row.get('material_no'))
if material:
unique_materials.add(material)

Products
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

Dates
if row.get('dn_create_date'):
dn_create_dates.append(row.get('dn_create_date'))
if row.get('good_issue_date'):
good_issue_dates.append(row.get('good_issue_date'))
if row.get('pod_date'):
pod_dates.append(row.get('pod_date'))

Sort products
products.sort(key=lambda x: x.get('model', ''))

Calculate averages
material_count = len(unique_materials)
average_revenue = total_revenue / len(rows) if rows else Decimal(0)
average_unit_price = total_revenue / total_units if total_units > 0 else Decimal(0)

Determine dates
dn_create_date = safe_date(min(dn_create_dates)) if dn_create_dates else None
good_issue_date = safe_date(max(good_issue_dates)) if good_issue_dates else None
pod_date = safe_date(max(pod_dates)) if pod_dates else None

Calculate aging
delivery_aging = calculate_days(dn_create_date, good_issue_date)
pod_aging = calculate_days(good_issue_date, pod_date)
total_cycle = calculate_days(dn_create_date, pod_date)

Determine status
pgi_exists = good_issue_date is not None
pod_exists = pod_date is not None

if pod_exists and pgi_exists:
stage = "Delivered"
emoji = "✅"
pgi_status = "Completed"
pod_status = "Completed"
pending = False
pending_text = "🟢 No"
elif pgi_exists and not pod_exists:
stage = "In Transit"
emoji = "🚚"
pgi_status = "Completed"
pod_status = "Pending"
pending = True
pending_text = "⚠️ Yes"
else:
stage = "Pending Dispatch"
emoji = "⏳"
pgi_status = "Pending"
pod_status = "Pending"
pending = True
pending_text = "⚠️ Yes"

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

==================================================================================================
BLOCK 9: DASHBOARD BUILDER
==================================================================================================
def _build_dashboard(self, aggregated: DNAggregate) -> DNDashboard:
"""Build complete dashboard from aggregated data."""
return DNDashboard(

Core
dn_no=aggregated.dn_no,
dealer_name=aggregated.dealer_name,
dealer_code=aggregated.dealer_code,
customer_name=aggregated.dealer_name,
customer_code=aggregated.customer_code,

Location
warehouse=aggregated.warehouse,
warehouse_code=aggregated.warehouse_code,
city=aggregated.city,
delivery_location=aggregated.delivery_location,

Business
sales_manager=aggregated.sales_manager,
sales_office=aggregated.sales_office,
division=aggregated.division,
order_type=aggregated.order_type,
dn_work=aggregated.dn_work,

Metrics
total_units=aggregated.total_units,
total_revenue=aggregated.total_revenue,
material_count=aggregated.material_count,
model_count=aggregated.model_count,
row_count=aggregated.row_count,
average_revenue=aggregated.average_revenue,
average_unit_price=aggregated.average_unit_price,

Dates
dn_create_date=format_date(aggregated.dn_create_date),
good_issue_date=format_date(aggregated.good_issue_date),
pod_date=format_date(aggregated.pod_date),

Aging
delivery_aging_days=aggregated.delivery_aging_days,
pod_aging_days=aggregated.pod_aging_days,
total_cycle_days=aggregated.total_cycle_days,
delivery_aging_text=format_aging_text(aggregated.delivery_aging_days),
pod_aging_text=format_aging_text(aggregated.pod_aging_days),
total_cycle_text=format_aging_text(aggregated.total_cycle_days),

Status
calculated_stage=aggregated.calculated_stage,
calculated_emoji=aggregated.calculated_emoji,
delivery_status=aggregated.calculated_stage,
pgi_status=aggregated.pgi_status,
pod_status=aggregated.pod_status,
pending_flag=aggregated.pending_flag,
pending_flag_text=aggregated.pending_flag_text,

Products
products=aggregated.products,

Source
source_file=aggregated.source_file,
upload_batch_id=aggregated.upload_batch_id,
imported_at=format_date(aggregated.imported_at),
created_at=format_date(aggregated.created_at),
updated_at=format_date(aggregated.updated_at)
)

==================================================================================================
BLOCK 10: PUBLIC METHODS (COMPATIBILITY)
==================================================================================================
def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
"""Get complete DN dashboard - main method."""
return self.get_dn_complete_info(dn_no)

def search_dn(self, dn_no: str) -> Dict[str, Any]:
"""Search for DN - alias for get_dn_complete_info."""
return self.get_dn_complete_info(dn_no)

def verify_dn(self, dn_no: str) -> Dict[str, Any]:
"""Verify if DN exists."""
result = self.get_dn_complete_info(dn_no)
return {
"success": True,
"exists": result.get("success", False)
}

def health_check(self) -> Dict[str, Any]:
"""Health check endpoint."""
try:
rows_count = 0
latency_ms = 0

Get row count
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
"""Validation query for ai_provider_service."""
try:
with self._get_session_context() as session:
result = session.execute(
text("SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports")
)
row = result.fetchone()
count = row[0] if row else 0

return {
"success": True,
"records": count,
"error": None
}
except Exception as e:
return {
"success": False,
"records": 0,
"error": str(e)
}

def get_service_metadata(self) -> Dict[str, Any]:
"""Get service metadata for ai_provider_service."""
return {
"service_name": self._service_name,
"version": self._version,
"status": self._status,
"module": "DN Analytics",
"description": "Enterprise DN Analytics Service",
"initialized": self._initialized,
"methods": [
"health_check",
"validation_query",
"get_service_metadata",
"search_dn",
"verify_dn",
"get_dn_dashboard",
"get_pending_dns",
"get_pending_pgi",
"get_pending_pod",
"format_dn_dashboard"
]
}

==================================================================================================
BLOCK 11: PENDING REPORTS
==================================================================================================
def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
"""Get all pending DNs."""
try:
limit = min(limit, 1000)

count_query = """
SELECT COUNT(DISTINCT dn_no) AS total_pending
FROM delivery_reports
WHERE pod_date IS NULL
OR delivery_status = 'Pending'
"""
count_result = self._execute_query(count_query)
total_pending = count_result[0].get('total_pending', 0) if count_result else 0

if total_pending == 0:
return {
"success": True,
"data": [],
"total": 0,
"limit": limit,
"offset": offset,
"message": "No pending DNs found"
}

pending_query = """
SELECT
dn_no,
MAX(customer_name) AS dealer_name,
MAX(warehouse) AS warehouse,
MAX(ship_to_city) AS city,
SUM(dn_qty) AS total_units,
SUM(dn_amount) AS total_revenue,
MIN(dn_create_date) AS dn_create_date,
MAX(good_issue_date) AS good_issue_date,
MAX(pod_date) AS pod_date,
MAX(delivery_status) AS delivery_status,
MAX(pgi_status) AS pgi_status,
MAX(pod_status) AS pod_status,
MAX(pending_flag) AS pending_flag,
MAX(sales_manager) AS sales_manager,
MAX(division) AS division,
COUNT(*) AS material_count
FROM delivery_reports
WHERE pod_date IS NULL
OR delivery_status = 'Pending'
GROUP BY dn_no
ORDER BY MIN(dn_create_date) ASC
LIMIT :limit OFFSET :offset
"""

results = self._execute_query(
pending_query,
{"limit": limit, "offset": offset}
)

formatted_results = []
for row in results:
formatted_results.append({
"dn_no": row.get('dn_no'),
"dealer_name": row.get('dealer_name') or "Unknown Dealer",
"warehouse": row.get('warehouse') or "Unknown Warehouse",
"city": row.get('city') or "Unknown City",
"total_units": safe_int(row.get('total_units')),
"total_revenue": float(safe_decimal(row.get('total_revenue'))),
"dn_create_date": format_date(row.get('dn_create_date')),
"good_issue_date": format_date(row.get('good_issue_date')),
"pod_date": format_date(row.get('pod_date')),
"delivery_status": row.get('delivery_status') or "Pending",
"pending_flag": row.get('pending_flag', True),
"sales_manager": row.get('sales_manager'),
"division": row.get('division'),
"material_count": row.get('material_count', 1)
})

return {
"success": True,
"data": formatted_results,
"total": total_pending,
"limit": limit,
"offset": offset,
"returned": len(formatted_results)
}

except Exception as e:
logger.error(f"❌ Failed to get pending DNs: {e}")
return {"success": False, "error": str(e)}

def get_pending_pgi(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
"""Get all pending PGI deliveries."""
try:
limit = min(limit, 1000)

count_query = """
SELECT COUNT(DISTINCT dn_no) AS total_pending
FROM delivery_reports
WHERE good_issue_date IS NULL
"""
count_result = self._execute_query(count_query)
total_pending = count_result[0].get('total_pending', 0) if count_result else 0

if total_pending == 0:
return {
"success": True,
"data": [],
"total": 0,
"limit": limit,
"offset": offset,
"message": "No pending PGI found"
}

pending_query = """
SELECT
dn_no,
MAX(customer_name) AS dealer_name,
MAX(warehouse) AS warehouse,
MAX(ship_to_city) AS city,
SUM(dn_qty) AS total_units,
SUM(dn_amount) AS total_revenue,
MIN(dn_create_date) AS dn_create_date,
MAX(good_issue_date) AS good_issue_date,
MAX(pod_date) AS pod_date,
MAX(delivery_status) AS delivery_status,
MAX(pgi_status) AS pgi_status,
MAX(pod_status) AS pod_status,
MAX(pending_flag) AS pending_flag,
MAX(sales_manager) AS sales_manager,
MAX(division) AS division,
COUNT(*) AS material_count
FROM delivery_reports
WHERE good_issue_date IS NULL
GROUP BY dn_no
ORDER BY MIN(dn_create_date) ASC
LIMIT :limit OFFSET :offset
"""

results = self._execute_query(
pending_query,
{"limit": limit, "offset": offset}
)

formatted_results = []
for row in results:
formatted_results.append({
"dn_no": row.get('dn_no'),
"dealer_name": row.get('dealer_name') or "Unknown Dealer",
"warehouse": row.get('warehouse') or "Unknown Warehouse",
"city": row.get('city') or "Unknown City",
"total_units": safe_int(row.get('total_units')),
"total_revenue": float(safe_decimal(row.get('total_revenue'))),
"dn_create_date": format_date(row.get('dn_create_date')),
"good_issue_date": format_date(row.get('good_issue_date')),
"pod_date": format_date(row.get('pod_date')),
"delivery_status": row.get('delivery_status') or "Pending",
"pending_flag": row.get('pending_flag', True),
"sales_manager": row.get('sales_manager'),
"division": row.get('division'),
"material_count": row.get('material_count', 1)
})

return {
"success": True,
"data": formatted_results,
"total": total_pending,
"limit": limit,
"offset": offset,
"returned": len(formatted_results)
}

except Exception as e:
logger.error(f"❌ Failed to get pending PGI: {e}")
return {"success": False, "error": str(e)}

def get_pending_pod(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
"""Get all pending POD deliveries."""
try:
limit = min(limit, 1000)

count_query = """
SELECT COUNT(DISTINCT dn_no) AS total_pending
FROM delivery_reports
WHERE good_issue_date IS NOT NULL
AND pod_date IS NULL
"""
count_result = self._execute_query(count_query)
total_pending = count_result[0].get('total_pending', 0) if count_result else 0

if total_pending == 0:
return {
"success": True,
"data": [],
"total": 0,
"limit": limit,
"offset": offset,
"message": "No pending POD found"
}

pending_query = """
SELECT
dn_no,
MAX(customer_name) AS dealer_name,
MAX(warehouse) AS warehouse,
MAX(ship_to_city) AS city,
SUM(dn_qty) AS total_units,
SUM(dn_amount) AS total_revenue,
MIN(dn_create_date) AS dn_create_date,
MAX(good_issue_date) AS good_issue_date,
MAX(pod_date) AS pod_date,
MAX(delivery_status) AS delivery_status,
MAX(pgi_status) AS pgi_status,
MAX(pod_status) AS pod_status,
MAX(pending_flag) AS pending_flag,
MAX(sales_manager) AS sales_manager,
MAX(division) AS division,
COUNT(*) AS material_count
FROM delivery_reports
WHERE good_issue_date IS NOT NULL
AND pod_date IS NULL
GROUP BY dn_no
ORDER BY MIN(dn_create_date) ASC
LIMIT :limit OFFSET :offset
"""

results = self._execute_query(
pending_query,
{"limit": limit, "offset": offset}
)

formatted_results = []
for row in results:
pod_aging = calculate_days(
row.get('good_issue_date'),
row.get('pod_date')
)

formatted_results.append({
"dn_no": row.get('dn_no'),
"dealer_name": row.get('dealer_name') or "Unknown Dealer",
"warehouse": row.get('warehouse') or "Unknown Warehouse",
"city": row.get('city') or "Unknown City",
"total_units": safe_int(row.get('total_units')),
"total_revenue": float(safe_decimal(row.get('total_revenue'))),
"dn_create_date": format_date(row.get('dn_create_date')),
"good_issue_date": format_date(row.get('good_issue_date')),
"pod_date": format_date(row.get('pod_date')),
"delivery_status": "In Transit",
"pending_flag": row.get('pending_flag', True),
"pod_aging_days": pod_aging,
"pod_aging_text": format_aging_text(pod_aging),
"sales_manager": row.get('sales_manager'),
"division": row.get('division'),
"material_count": row.get('material_count', 1)
})

return {
"success": True,
"data": formatted_results,
"total": total_pending,
"limit": limit,
"offset": offset,
"returned": len(formatted_results)
}

except Exception as e:
logger.error(f"❌ Failed to get pending POD: {e}")
return {"success": False, "error": str(e)}

==================================================================================================
BLOCK 12: WHATSAPP FORMATTER
==================================================================================================
def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
"""
Format DN dashboard for WhatsApp.

Returns formatted string with emojis and clean layout.
"""
data = dashboard_data.get('data', {})

Handle dictionary or dataclass
if hasattr(data, 'dataclass_fields'):

It's a dataclass
d = {
'dn_no': data.dn_no,
'dealer_name': data.dealer_name,
'dealer_code': data.dealer_code,
'customer_name': data.customer_name,
'customer_code': data.customer_code,
'warehouse': data.warehouse,
'warehouse_code': data.warehouse_code,
'city': data.city,
'delivery_location': data.delivery_location,
'sales_manager': data.sales_manager,
'sales_office': data.sales_office,
'division': data.division,
'order_type': data.order_type,
'dn_work': data.dn_work,
'total_units': data.total_units,
'total_revenue': data.total_revenue,
'material_count': data.material_count,
'model_count': data.model_count,
'row_count': data.row_count,
'average_revenue': data.average_revenue,
'average_unit_price': data.average_unit_price,
'dn_create_date': data.dn_create_date,
'good_issue_date': data.good_issue_date,
'pod_date': data.pod_date,
'delivery_aging_text': data.delivery_aging_text,
'pod_aging_text': data.pod_aging_text,
'total_cycle_text': data.total_cycle_text,
'calculated_stage': data.calculated_stage,
'calculated_emoji': data.calculated_emoji,
'pgi_status': data.pgi_status,
'pod_status': data.pod_status,
'pending_flag_text': data.pending_flag_text,
'products': data.products,
'source_file': data.source_file,
'upload_batch_id': data.upload_batch_id,
'created_at': data.created_at,
'updated_at': data.updated_at
}
else:
d = data

lines = []

Header
lines.append("📦 DN: {}".format(d.get('dn_no', 'N/A')))
lines.append("")

Dealer
dealer_name = d.get('dealer_name', 'Unknown')
if dealer_name and dealer_name != 'Unknown':
lines.append("Dealer:")
lines.append("{}".format(dealer_name))
lines.append("")

Warehouse
warehouse = d.get('warehouse', 'Unknown')
if warehouse and warehouse != 'Unknown':
lines.append("Warehouse:")
lines.append("{}".format(warehouse))
lines.append("")

City
city = d.get('city', 'Unknown')
if city and city != 'Unknown':
lines.append("City:")
lines.append("{}".format(city))
lines.append("")

Sales Manager
sales_manager = d.get('sales_manager')
if sales_manager:
lines.append("Sales Manager:")
lines.append("{}".format(sales_manager))
lines.append("")

Division
division = d.get('division')
if division:
lines.append("Division:")
lines.append("{}".format(division))
lines.append("")

Dealer Code
dealer_code = d.get('dealer_code')
if dealer_code:
lines.append("Dealer Code:")
lines.append("{}".format(dealer_code))
lines.append("")

Warehouse Code
warehouse_code = d.get('warehouse_code')
if warehouse_code:
lines.append("Warehouse Code:")
lines.append("{}".format(warehouse_code))
lines.append("")

Metrics
lines.append("📊 Metrics:")
lines.append("Units: {}".format(d.get('total_units', 0)))

revenue = d.get('total_revenue', 0)
if revenue:
if isinstance(revenue, Decimal):
lines.append("Revenue: PKR {:,}".format(revenue.quantize(Decimal('0.01'))))
else:
lines.append("Revenue: PKR {:,}".format(revenue))
else:
lines.append("Revenue: PKR 0")

lines.append("")
lines.append("Materials: {}".format(d.get('material_count', 1)))
model_count = d.get('model_count', 0)
if model_count > 0:
lines.append("Models: {}".format(model_count))
lines.append("")

Average metrics
avg_price = d.get('average_unit_price', 0)
if avg_price:
if isinstance(avg_price, Decimal):
lines.append("Avg Price: PKR {:,}".format(avg_price.quantize(Decimal('0.01'))))
else:
lines.append("Avg Price: PKR {:,}".format(avg_price))
lines.append("")

Dates
lines.append("📅 Dates:")
lines.append("DN Create: {}".format(d.get('dn_create_date', 'N/A')))
lines.append("PGI: {}".format(d.get('good_issue_date', 'N/A')))
lines.append("POD: {}".format(d.get('pod_date', 'N/A')))
lines.append("")

Aging
lines.append("⏳ Aging:")
lines.append("Delivery: {}".format(d.get('delivery_aging_text', 'N/A')))
lines.append("POD: {}".format(d.get('pod_aging_text', 'N/A')))
lines.append("Total Cycle: {}".format(d.get('total_cycle_text', 'N/A')))
lines.append("")

Status
stage = d.get('calculated_stage', 'Unknown')
emoji = d.get('calculated_emoji', '❓')
pgi_status = d.get('pgi_status', 'Unknown')
pod_status = d.get('pod_status', 'Unknown')
pending_text = d.get('pending_flag_text', 'Unknown')

lines.append("📋 Status:")
lines.append("Delivery: {} {}".format(emoji, stage))
lines.append("PGI: {}".format(pgi_status))
lines.append("POD: {}".format(pod_status))
lines.append("Pending: {}".format(pending_text))
lines.append("")

Products
products = d.get('products', [])
if products:
lines.append("📦 Product Details:")
for idx, product in enumerate(products[:10], 1):
model_name = product.get('model', 'Unknown')
material_no = product.get('material_no', 'N/A')
qty = product.get('quantity', 0)
revenue_val = product.get('revenue', 0)
avg_price_val = product.get('average_price', 0)

lines.append("{}. {}: {} units".format(idx, model_name, qty))
if material_no != 'N/A':
lines.append(" Material: {}".format(material_no))
if revenue_val > 0:
lines.append(" Revenue: PKR {:,}".format(revenue_val))
if avg_price_val > 0:
lines.append(" Avg Price: PKR {:,}".format(avg_price_val))

if len(products) > 10:
remaining = len(products) - 10
lines.append("... and {} more products".format(remaining))
lines.append("")

Source info
source_file = d.get('source_file')
batch_id = d.get('upload_batch_id')
if source_file or batch_id:
lines.append("📁 Source:")
if source_file:
lines.append("File: {}".format(source_file))
if batch_id:
lines.append("Batch: {}".format(batch_id))
lines.append("")

return "\n".join(lines)

======================================================================================================
BLOCK 13: THREAD-SAFE SINGLETON
======================================================================================================
_dn_analytics_service = None
_dn_lock = threading.Lock()

def get_dn_analytics_service() -> DNAnalysisService:
"""Thread-safe singleton getter."""
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

======================================================================================================
BLOCK 14: EXPORTS
======================================================================================================
all = [
'DNAnalysisService',
'get_dn_analytics_service',
'DNAggregate',
'DNDashboard'
]

======================================================================================================
BLOCK 15: MODULE INITIALIZATION
======================================================================================================
logger.info("=" * 70)
logger.info("DNAnalysisService v14.0 - ENTERPRISE PRODUCTION")
logger.info("=" * 70)
logger.info("")
logger.info(" SERVICE DETAILS:")
logger.info(" ✅ Service Name: dn_analysis")
logger.info(" ✅ Version: 14.0 (Enterprise)")
logger.info(" ✅ Source: PostgreSQL (delivery_reports)")
logger.info("")
logger.info(" ENTERPRISE FEATURES:")
logger.info(" ✅ PostgreSQL is the ONLY source of truth")
logger.info(" ✅ Decimal for revenue calculations")
logger.info(" ✅ Safe type conversions")
logger.info(" ✅ Comprehensive validation")
logger.info(" ✅ Performance optimized")
logger.info(" ✅ Complete WhatsApp formatting")
logger.info("")
logger.info(" STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)

Initialize service
try:
service = get_dn_analytics_service()
logger.info("✅ DN Analytics Service initialized successfully")
except Exception as e:
logger.error(f"❌ DN Analytics Service initialization failed: {e}")
