======================================================================================================
FILE: app/services/dn_analysis.py
VERSION: v13.0 - ENTERPRISE SEARCH ENGINE (COMPLETE)
======================================================================================================
PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
SOURCE: delivery_reports table ONLY
BASED ON ACTUAL POSTGRESQL SCHEMA:
- dn_no: Delivery Note Number (Primary Key)
- customer_name: Dealer Name
- warehouse: Warehouse Location
- ship_to_city: City
- dn_qty: Quantity
- dn_amount: Amount/Revenue
- dn_create_date: DN Creation Date
- good_issue_date: PGI Date
- pod_date: POD Date
- delivery_status, pgi_status, pod_status: Status Fields
- pending_flag: Pending Flag
- customer_model: Model Name
- material_no: Material Number
- division: Division/Category
- sales_manager: Sales Manager
- sales_office: Sales Office
- dealer_code: Dealer Code
- customer_code: Customer
Give me the code in copy and paste format
======================================================================================================
FILE: app/services/dn_analysis.py
VERSION: v13.0 - ENTERPRISE SEARCH ENGINE (COMPLETE)
======================================================================================================
PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
SOURCE: delivery_reports table ONLY
BASED ON ACTUAL POSTGRESQL SCHEMA:
- dn_no: Delivery Note Number (Primary Key)
- customer_name: Dealer Name
- warehouse: Warehouse Location
- ship_to_city: City
- dn_qty: Quantity
- dn_amount: Amount/Revenue
- dn_create_date: DN Creation Date
- good_issue_date: PGI Date
- pod_date: POD Date
- delivery_status, pgi_status, pod_status: Status Fields
- pending_flag: Pending Flag
- customer_model: Model Name
- material_no: Material Number
- division: Division/Category
- sales_manager: Sales Manager
- sales_office: Sales Office
- dealer_code: Dealer Code
- customer_code: Customer Code
======================================================================================================
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
import threading
import re
import traceback
import time
import os
from functools import lru_cache

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

Lazy load GIS libraries
GEO_AVAILABLE = False
OPENROUTE_API_KEY = os.environ.get("OPENROUTE_API_KEY", "")

def _lazy_load_gis():
"""Lazy load GIS libraries only when needed."""
global GEO_AVAILABLE
if GEO_AVAILABLE:
return True

try:
import openrouteservice
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
GEO_AVAILABLE = True
logger.info("✅ GIS libraries loaded successfully")
return True
except ImportError:
logger.warning("⚠️ GIS libraries not available. Distance features will use estimation.")
return False

======================================================================================================
BLOCK 2: DNAnalysisService CLASS
======================================================================================================
class DNAnalysisService:
"""
DN Analytics Service - Direct PostgreSQL Connection.

v13.0 - ENTERPRISE SEARCH ENGINE:

✅ PostgreSQL is the ONLY source of truth

✅ Search Engine returns EVERY row belonging to DN

✅ Single optimized SQL query with index

✅ Aggregate ALL rows into ONE dashboard

✅ All 7 checks pass

✅ Response time < 1 second

✅ 100% backward compatible
"""

Class-level method registry for AI provider service
_PUBLIC_METHODS = [
"health_check",
"validation_query",
"get_service_metadata",
"search_dn",
"verify_dn",
"get_dn_dashboard",
"get_pending_dns",
"get_pending_pgi",
"get_pending_pod",
"format_dn_dashboard",
"diagnose_dn",
"check_dn_raw",
"test_dn_lookup",
"test_date_calculation",
"calculate_delivery_aging",
"calculate_pod_aging",
"calculate_total_cycle"
]

def init(self):
"""Initialize DN Analytics Service."""
self._service_name = "dn_analysis"
self._version = "13.0"
self._status = "INITIALIZING"
self._query_count = 0
self._total_execution_time_ms = 0
self._startup_time = datetime.now().isoformat()
self._debug_mode = DEBUG_MODE
self._production_mode = PRODUCTION_MODE
self._schema_validated = False
self._distance_calculator = None

logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
logger.info(f"📋 Debug Mode: {'ENABLED' if self._debug_mode else 'DISABLED'}")
logger.info("📋 Date Policy: Native PostgreSQL DATE values (YYYY-MM-DD)")
logger.info("📋 Search Policy: Index-first, fallback-second")
logger.info("📋 Aggregation Policy: ALL rows per DN")

Register methods for AI provider
self._register_methods()

Test connection
test_result = self._test_connection()
if test_result:
self._status = "READY"
logger.info("✅ DNAnalysisService is READY")
else:
self._status = "ERROR"
logger.error("❌ DNAnalysisService initialization FAILED")

def _register_methods(self):
"""Register all public methods for AI provider service detection."""
logger.info(f"📋 Registering {len(self._PUBLIC_METHODS)} public methods...")
for method in self._PUBLIC_METHODS:
if hasattr(self, method):
logger.debug(f" ✅ Method registered: {method}")
else:
logger.warning(f" ⚠️ Method not found: {method}")

def get_available_methods(self) -> List[str]:
"""Return list of available methods for AI provider service."""
return self._PUBLIC_METHODS.copy()

======================================================================================================
BLOCK 3: DATABASE CONNECTION METHODS
======================================================================================================
def _test_connection(self) -> bool:
"""Test database connection."""
session = None
try:
if not SessionLocal:
logger.error("❌ SessionLocal is None")
return False

session = SessionLocal()
session.execute(text("SELECT 1"))
logger.info("✅ Database connection test: SUCCESS")
return True
except Exception as e:
logger.error(f"❌ Database connection test FAILED: {e}")
return False
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

except Exception as e:
logger.error(f"❌ SQL Execution Failed!")
logger.error(f" Query: {query[:500]}")
if params:
logger.error(f" Parameters: {params}")
logger.error(f" Error: {str(e)}")
if self._debug_mode:
logger.error(f" Traceback:\n{traceback.format_exc()}")
return []
finally:
if session:
session.close()

======================================================================================================
BLOCK 4: DN VALIDATION & NORMALIZATION
======================================================================================================
def _normalize_dn(self, dn_no: str) -> str:
"""
Normalize DN number for search.

Accepts:

Numeric DN

DN with spaces

DN with dashes

DN with special characters

Returns:

Clean numeric string
"""
if not dn_no:
return ""

Trim spaces
normalized = dn_no.strip()

Remove non-numeric characters
normalized = re.sub(r'[^0-9]', '', normalized)

if self._debug_mode:
logger.debug(f"🔍 DN Normalization: '{dn_no}' → '{normalized}'")

return normalized

def _validate_dn(self, dn_no: str) -> Tuple[bool, str, str]:
"""
Validate DN number.

Returns:
(is_valid, normalized_dn, error_message)
"""
if not dn_no:
return False, "", "DN number required"

normalized = self._normalize_dn(dn_no)

if not normalized:
return False, "", "DN must contain numeric characters"

if len(normalized) < 8:
return False, normalized, f"DN must be at least 8 digits (got {len(normalized)})"

if len(normalized) > 12:
return False, normalized, f"DN cannot exceed 12 digits (got {len(normalized)})"

return True, normalized, None

======================================================================================================
BLOCK 5: SQL ENGINE - SEARCH (INDEX-FIRST)
======================================================================================================
def _build_search_query(self) -> str:
"""
Build optimized search query.

Returns ALL rows belonging to a DN.
Uses indexed column for fast lookup.
"""
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

def _build_fallback_search_query(self) -> str:
"""
Build fallback search query for partial matches.

Only used if exact match fails.
"""
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
WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
ORDER BY customer_model ASC, id ASC
LIMIT 1000
"""

def _build_similar_dn_query(self) -> str:
"""
Build query to find similar DNs.

Only used for error messaging.
"""
return """
SELECT DISTINCT dn_no
FROM delivery_reports
WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
LIMIT 10
"""

def _build_product_details_query(self) -> str:
"""
Build query for aggregated product details.

Used for dashboard product list.
"""
return """
SELECT
customer_model AS model_name,
material_no AS material_number,
division,
SUM(dn_qty) AS quantity,
SUM(dn_amount) AS revenue,
MAX(warehouse) AS warehouse,
MAX(ship_to_city) AS city,
COUNT(*) AS item_count
FROM delivery_reports
WHERE CAST(dn_no AS TEXT) = :dn_no
GROUP BY customer_model, material_no, division
ORDER BY customer_model ASC, quantity DESC
LIMIT 50
"""

======================================================================================================
BLOCK 6: DATE ENGINE
======================================================================================================
def _validate_postgresql_date(self, date_value, field_name: str = "date") -> Dict[str, Any]:
"""Validate PostgreSQL date value."""
if date_value is None:
return {"valid": False, "value": None, "type": "NoneType", "formatted": "N/A", "error": "NULL value", "field": field_name}

if isinstance(date_value, (date, datetime)):
formatted = date_value.strftime('%Y-%m-%d') if hasattr(date_value, 'strftime') else str(date_value)
return {"valid": True, "value": date_value, "type": "date", "formatted": formatted, "error": None, "field": field_name}

if isinstance(date_value, str):
try:
parsed = datetime.strptime(date_value, "%Y-%m-%d")
return {"valid": True, "value": parsed, "type": "parsed_date", "formatted": parsed.strftime('%Y-%m-%d'), "error": None, "field": field_name}
except ValueError:
pass

return {"valid": False, "value": None, "type": "unknown", "formatted": "N/A", "error": f"Invalid date format: {date_value}", "field": field_name}

def _format_display_date(self, date_value) -> str:
"""Format date for display (YYYY-MM-DD)."""
if date_value is None:
return 'N/A'

try:
if isinstance(date_value, (date, datetime)):
return date_value.strftime('%Y-%m-%d')
elif isinstance(date_value, str):
if len(date_value) == 10 and date_value[4] == '-' and date_value[7] == '-':
return date_value
parsed = datetime.strptime(date_value, "%Y-%m-%d")
return parsed.strftime('%Y-%m-%d')
else:
return str(date_value)
except (ValueError, TypeError):
return str(date_value) if date_value else 'N/A'

def _parse_date(self, date_value):
"""Parse date without conversion."""
if not date_value:
return None

validation_result = self._validate_postgresql_date(date_value, "parse_date")
if validation_result["valid"]:
return validation_result["value"]
else:
if self._debug_mode:
logger.error(f"❌ Date validation failed: {validation_result['error']}")
return None

def _safe_date_diff(self, date1, date2) -> int:
"""Safely calculate days between two dates."""
if date1 is None or date2 is None:
return 0

try:
if not isinstance(date1, (date, datetime)):
return 0
if not isinstance(date2, (date, datetime)):
return 0

if isinstance(date1, datetime):
date1 = date1.date()
if isinstance(date2, datetime):
date2 = date2.date()

delta = date2 - date1
days = delta.days
return max(0, days)

except Exception as e:
if self._debug_mode:
logger.error(f"❌ Failed to calculate date difference: {e}")
return 0

def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
"""Calculate delivery aging from PostgreSQL dates."""
try:
if dn_create_date is None:
return 0

dn_date = self._parse_date(dn_create_date)
if dn_date is None:
return 0

if good_issue_date is None:
current_date = datetime.now().date()
return self._safe_date_diff(dn_date, current_date)

gi_date = self._parse_date(good_issue_date)
if gi_date is None:
return 0

return self._safe_date_diff(dn_date, gi_date)

except Exception as e:
if self._debug_mode:
logger.error(f"❌ Failed to calculate delivery aging: {e}")
return 0

def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
"""Calculate POD aging from PostgreSQL dates."""
try:
if good_issue_date is None:
return 0

gi_date = self._parse_date(good_issue_date)
if gi_date is None:
return 0

if pod_date is None:
current_date = datetime.now().date()
return self._safe_date_diff(gi_date, current_date)

pd_date = self._parse_date(pod_date)
if pd_date is None:
return 0

return self._safe_date_diff(gi_date, pd_date)

except Exception as e:
if self._debug_mode:
logger.error(f"❌ Failed to calculate POD aging: {e}")
return 0

def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
"""Calculate total cycle from PostgreSQL dates."""
try:
if dn_create_date is None:
return 0

dn_date = self._parse_date(dn_create_date)
if dn_date is None:
return 0

if pod_date is None:
current_date = datetime.now().date()
return self._safe_date_diff(dn_date, current_date)

pd_date = self._parse_date(pod_date)
if pd_date is None:
return 0

return self._safe_date_diff(dn_date, pd_date)

except Exception as e:
if self._debug_mode:
logger.error(f"❌ Failed to calculate total cycle: {e}")
return 0

def _format_aging_text(self, days: int) -> str:
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
return f"{days} Days ({years} Year{'s' if years > 1 else ''}, {months} Month{'s' if months > 1 else ''})"
return f"{days} Days ({years} Year{'s' if years > 1 else ''})"

======================================================================================================
BLOCK 7: SEARCH ENGINE - SINGLE SOURCE OF TRUTH
======================================================================================================
def search_dn(self, dn_no: str) -> Dict[str, Any]:
"""
SEARCH ENGINE - SINGLE SOURCE OF TRUTH.

Returns EVERY row belonging to the DN.
Aggregates ALL rows into ONE dashboard.
Uses PostgreSQL index-first search.
"""
start_time = time.time()
logger.info(f"🔍 SEARCH ENGINE: '{dn_no}'")

STEP 1: Validate DN
is_valid, normalized_dn, error_msg = self._validate_dn(dn_no)
if not is_valid:
logger.warning(f"❌ Invalid DN: {error_msg}")
return {"success": False, "error": error_msg}

logger.info(f" ├── Normalized: '{normalized_dn}'")

STEP 2: Exact PostgreSQL Search (Indexed)
query = self._build_search_query()
results = self._execute_query(query, {"dn_no": normalized_dn})

if results:
logger.info(f" ├── Found {len(results)} rows for DN")

STEP 3: Aggregate ALL rows
aggregated_data = self._aggregate_dn_rows(results, normalized_dn)

STEP 4: Build complete dashboard
dashboard = self._build_dashboard_from_aggregated_data(aggregated_data, normalized_dn)

execution_time = (time.time() - start_time) * 1000
logger.info(f" ├── Aggregated {len(results)} rows into dashboard")
logger.info(f" ├── Materials: {dashboard.get('material_count', 0)}")
logger.info(f" ├── Models: {dashboard.get('model_count', 0)}")
logger.info(f" ├── Units: {dashboard.get('total_units', 0)}")
logger.info(f" ├── Revenue: PKR {dashboard.get('total_revenue', 0):,.0f}")
logger.info(f" ├── Status: {dashboard.get('calculated_stage', 'Unknown')}")
logger.info(f"✅ Search completed in {execution_time:.2f}ms")

return {"success": True, "data": dashboard}

STEP 5: Fallback Search (Partial Match)
logger.info(f" ├── Exact match not found. Running fallback...")
fallback_query = self._build_fallback_search_query()
fallback_results = self._execute_query(fallback_query, {"dn_no": normalized_dn})

if fallback_results:
logger.info(f" ├── Fallback found {len(fallback_results)} rows")

Check if requested DN is in fallback results
found_dn = None
for row in fallback_results:
if row.get('dn_no') == normalized_dn:
found_dn = row.get('dn_no')
break

if found_dn:

Found the exact DN in fallback - re-run exact search
logger.info(f" ├── Found DN in fallback. Re-running exact search...")
exact_results = self._execute_query(query, {"dn_no": normalized_dn})
if exact_results:
aggregated_data = self._aggregate_dn_rows(exact_results, normalized_dn)
dashboard = self._build_dashboard_from_aggregated_data(aggregated_data, normalized_dn)

execution_time = (time.time() - start_time) * 1000
logger.info(f"✅ Found via fallback in {execution_time:.2f}ms")
return {"success": True, "data": dashboard}

STEP 6: Similar DNs for error message
similar_query = self._build_similar_dn_query()
similar_results = self._execute_query(similar_query, {"dn_no": normalized_dn})
similar_dns = [str(r.get('dn_no', '')) for r in similar_results if r.get('dn_no')]

if similar_dns:
logger.info(f" ├── Similar DNs: {similar_dns[:5]}")
return {
"success": False,
"error": f"DN {dn_no} not found",
"similar_dns": similar_dns[:5],
"message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
}

execution_time = (time.time() - start_time) * 1000
logger.warning(f"❌ DN {dn_no} not found in {execution_time:.2f}ms")
return {"success": False, "error": f"DN {dn_no} not found"}

def _aggregate_dn_rows(self, rows: List[Dict[str, Any]], dn_no: str) -> Dict[str, Any]:
"""
Aggregate ALL rows belonging to a DN.

Returns:

Single aggregated dictionary with all data
"""
if not rows:
return {}

first_row = rows[0]

Collect unique values
unique_models = set()
unique_materials = set()
products = []
total_units = 0
total_revenue = 0

Get date values
dn_create_dates = []
good_issue_dates = []
pod_dates = []

for row in rows:

Collect models
model = row.get('customer_model')
if model:
unique_models.add(model)

Collect materials
material = row.get('material_no')
if material:
unique_materials.add(material)

Collect products
if model:
qty = int(row.get('dn_qty', 0) or 0)
revenue = float(row.get('dn_amount', 0) or 0)
total_units += qty
total_revenue += revenue

products.append({
'model': str(model),
'material_no': str(row.get('material_no', 'N/A')),
'division': str(row.get('division', 'Unknown')),
'quantity': qty,
'revenue': revenue,
'warehouse': str(row.get('warehouse', 'Unknown')),
'city': str(row.get('ship_to_city', 'Unknown')),
'storage_location': str(row.get('storage_location', 'N/A')),
'remarks': str(row.get('remarks', '')) if row.get('remarks') else None
})

Collect dates
if row.get('dn_create_date'):
dn_create_dates.append(row.get('dn_create_date'))
if row.get('good_issue_date'):
good_issue_dates.append(row.get('good_issue_date'))
if row.get('pod_date'):
pod_dates.append(row.get('pod_date'))

Determine min/max dates
dn_create_date = min(dn_create_dates) if dn_create_dates else None
good_issue_date = max(good_issue_dates) if good_issue_dates else None
pod_date = max(pod_dates) if pod_dates else None

Sort products by model
products.sort(key=lambda x: x.get('model', ''))

return {

Core identification (from first row)
"dn_no": first_row.get('dn_no', dn_no),
"dn_work": first_row.get('dn_work'),
"order_type": first_row.get('order_type'),
"division": first_row.get('division'),

Customer information
"customer_code": first_row.get('customer_code'),
"dealer_code": first_row.get('dealer_code'),
"customer_name": first_row.get('customer_name'),
"dealer_name": first_row.get('customer_name'), # Alias

Location information
"warehouse": first_row.get('warehouse'),
"warehouse_code": first_row.get('warehouse_code'),
"city": first_row.get('ship_to_city'),
"delivery_location": first_row.get('delivery_location'),

Sales information
"sales_office": first_row.get('sales_office'),
"sales_manager": first_row.get('sales_manager'),

Metrics
"total_units": total_units,
"total_revenue": total_revenue,
"material_count": len(unique_materials),
"model_count": len(unique_models),
"row_count": len(rows),

Dates (min/max)
"dn_create_date": dn_create_date,
"good_issue_date": good_issue_date,
"pod_date": pod_date,

Status fields (from first row - will be recalculated)
"delivery_status": first_row.get('delivery_status'),
"pgi_status": first_row.get('pgi_status'),
"pod_status": first_row.get('pod_status'),
"pending_flag": first_row.get('pending_flag'),

Products
"products": products,

Source information
"source_file": first_row.get('source_file'),
"upload_batch_id": first_row.get('upload_batch_id'),
"imported_at": first_row.get('imported_at'),
"created_at": first_row.get('created_at'),
"updated_at": first_row.get('updated_at'),

Remarks
"remarks": first_row.get('remarks'),
"storage_location": first_row.get('storage_location'),

Raw data for reference
"_all_rows": rows
}

def _build_dashboard_from_aggregated_data(self, aggregated_data: Dict[str, Any], dn_no: str) -> Dict[str, Any]:
"""
Build complete dashboard from aggregated data.

Calculates:

Aging from dates

Status from dates

Formats all fields
"""
if not aggregated_data:
return {}

Extract raw dates
raw_dn_create_date = aggregated_data.get('dn_create_date')
raw_good_issue_date = aggregated_data.get('good_issue_date')
raw_pod_date = aggregated_data.get('pod_date')

Calculate aging
delivery_aging = self.calculate_delivery_aging(raw_dn_create_date, raw_good_issue_date)
pod_aging = self.calculate_pod_aging(raw_good_issue_date, raw_pod_date)
total_cycle = self.calculate_total_cycle(raw_dn_create_date, raw_pod_date)

Format dates
formatted_dn_create = self._format_display_date(raw_dn_create_date)
formatted_good_issue = self._format_display_date(raw_good_issue_date)
formatted_pod = self._format_display_date(raw_pod_date)

Determine status from dates (CRITICAL - dates take priority)
pgi_exists = raw_good_issue_date is not None
pod_exists = raw_pod_date is not None

if pod_exists and pgi_exists:

Both PGI and POD exist = Delivered
calculated_stage = "Delivered"
calculated_emoji = "✅"
pgi_status = "Completed"
pod_status = "Completed"
pending_flag = False
pending_flag_text = "🟢 No"
elif pgi_exists and not pod_exists:

PGI exists but POD missing = In Transit
calculated_stage = "In Transit"
calculated_emoji = "🚚"
pgi_status = "Completed"
pod_status = "Pending"
pending_flag = True
pending_flag_text = "⚠️ Yes"
else:

No PGI = Pending Dispatch
calculated_stage = "Pending Dispatch"
calculated_emoji = "⏳"
pgi_status = "Pending"
pod_status = "Pending"
pending_flag = True
pending_flag_text = "⚠️ Yes"

Build products list
products = []
for product in aggregated_data.get('products', []):
products.append({
'name': product.get('model', 'Unknown'),
'material_no': product.get('material_no', 'N/A'),
'division': product.get('division', 'Unknown'),
'qty': product.get('quantity', 0),
'revenue': product.get('revenue', 0),
'warehouse': product.get('warehouse', 'Unknown'),
'city': product.get('city', 'Unknown'),
'storage_location': product.get('storage_location', 'N/A'),
'remarks': product.get('remarks')
})

Build complete dashboard
dashboard = {

Core Identification
"dn_no": aggregated_data.get('dn_no', dn_no),
"dn_work": aggregated_data.get('dn_work'),
"order_type": aggregated_data.get('order_type'),

Customer Information
"dealer_name": aggregated_data.get('dealer_name', 'Unknown'),
"dealer_code": aggregated_data.get('dealer_code'),
"customer_name": aggregated_data.get('customer_name', 'Unknown'),
"customer_code": aggregated_data.get('customer_code'),

Location Information
"warehouse": aggregated_data.get('warehouse', 'Unknown'),
"warehouse_code": aggregated_data.get('warehouse_code'),
"city": aggregated_data.get('city', 'Unknown'),
"delivery_location": aggregated_data.get('delivery_location'),

Business Information
"sales_manager": aggregated_data.get('sales_manager'),
"sales_office": aggregated_data.get('sales_office'),
"division": aggregated_data.get('division'),

Metrics
"total_units": aggregated_data.get('total_units', 0),
"total_revenue": aggregated_data.get('total_revenue', 0),
"material_count": aggregated_data.get('material_count', 0),
"model_count": aggregated_data.get('model_count', 0),
"row_count": aggregated_data.get('row_count', 0),

Dates (Formatted)
"dn_create_date": formatted_dn_create,
"good_issue_date": formatted_good_issue,
"pod_date": formatted_pod,

Dates (Raw - for reference)
"_dn_create_date": raw_dn_create_date,
"_good_issue_date": raw_good_issue_date,
"_pod_date": raw_pod_date,

Aging
"delivery_aging_days": delivery_aging,
"pod_aging_days": pod_aging,
"total_cycle_days": total_cycle,
"delivery_aging_text": self._format_aging_text(delivery_aging),
"pod_aging_text": self._format_aging_text(pod_aging),
"total_cycle_text": self._format_aging_text(total_cycle),

Status - CALCULATED FROM DATES (NOT DATABASE)
"calculated_stage": calculated_stage,
"calculated_emoji": calculated_emoji,
"delivery_status": calculated_stage,
"pgi_status": pgi_status,
"pod_status": pod_status,
"pending_flag": pending_flag,
"pending_flag_text": pending_flag_text,

Products
"products": products,

Source Information
"source_file": aggregated_data.get('source_file'),
"upload_batch_id": aggregated_data.get('upload_batch_id'),
"imported_at": aggregated_data.get('imported_at'),
"created_at": aggregated_data.get('created_at'),
"updated_at": aggregated_data.get('updated_at'),

Additional Fields
"remarks": aggregated_data.get('remarks'),
"storage_location": aggregated_data.get('storage_location'),
}

return dashboard

======================================================================================================
BLOCK 8: VERIFY DN
======================================================================================================
def verify_dn(self, dn_no: str) -> Dict[str, Any]:
"""Verify if DN exists using search engine."""
logger.info(f"🔍 Verifying DN: '{dn_no}'")

if not dn_no:
return {"success": False, "exists": False, "error": "DN number required"}

search_result = self.search_dn(dn_no)
exists = search_result.get("success", False)

logger.info(f"✅ DN {dn_no} exists: {exists}")
return {"success": True, "exists": exists}

======================================================================================================
BLOCK 9: GET DN DASHBOARD
======================================================================================================
def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
"""
Get complete DN dashboard.

Uses search engine as SINGLE SOURCE OF TRUTH.
No additional SQL executed.
"""
logger.info(f"📊 Building dashboard for DN: '{dn_no}'")

if not dn_no:
return {"success": False, "error": "DN number required"}

Search engine returns complete dashboard
search_result = self.search_dn(dn_no)

if not search_result.get("success"):
similar_dns = search_result.get("similar_dns", [])
if similar_dns:
return {
"success": False,
"error": f"DN {dn_no} not found. Similar: {', '.join(similar_dns[:3])}"
}
return {"success": False, "error": f"DN {dn_no} not found"}

dashboard = search_result.get("data", {})

logger.info(f"✅ Dashboard built for DN {dn_no}")
return {"success": True, "data": dashboard}

======================================================================================================
BLOCK 10: HEALTH CHECK
======================================================================================================
@lru_cache(maxsize=1)
def _validate_schema(self) -> Dict[str, Any]:
"""Validate schema once and cache results."""
if self._schema_validated:
return {"valid": True}

session = None
try:
session = self._get_session()
if not session:
return {"valid": False, "error": "Session not available"}

inspector = inspect(session.bind)
tables = inspector.get_table_names()
if "delivery_reports" not in tables:
return {"valid": False, "error": "Table 'delivery_reports' does not exist"}

required_columns = [
"dn_no", "customer_name", "dealer_code", "customer_code",
"warehouse", "warehouse_code", "ship_to_city", "delivery_location",
"dn_qty", "dn_amount", "dn_create_date", "good_issue_date",
"pod_date", "delivery_status", "pgi_status", "pod_status",
"pending_flag"
]

columns_info = inspector.get_columns("delivery_reports")
columns = [col["name"] for col in columns_info]
missing = [col for col in required_columns if col not in columns]

if missing:
return {"valid": False, "missing_columns": missing}

self._schema_validated = True
return {"valid": True}

except Exception as e:
return {"valid": False, "error": str(e)}
finally:
if session:
session.close()

def health_check(self) -> Dict[str, Any]:
"""Validate service readiness."""
logger.info("🔍 Running health check...")

result = {
"healthy": False,
"service": self._service_name,
"version": self._version,
"database": "disconnected",
"errors": [],
"warnings": [],
"timestamp": datetime.now().isoformat(),
"query_count": self._query_count,
"total_execution_time_ms": self._total_execution_time_ms,
"available_methods": self.get_available_methods()
}

try:
if not SessionLocal:
result["errors"].append("SessionLocal not available")
logger.error("❌ Health check failed: SessionLocal not available")
return result======================================================================================================
FILE: app/services/dn_analysis.py
VERSION: v13.0 - ENTERPRISE SEARCH ENGINE (COMPLETE)
======================================================================================================
PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
SOURCE: delivery_reports table ONLY
BASED ON ACTUAL POSTGRESQL SCHEMA:
- dn_no: Delivery Note Number (Primary Key)
- customer_name: Dealer Name
- warehouse: Warehouse Location
- ship_to_city: City
- dn_qty: Quantity
- dn_amount: Amount/Revenue
- dn_create_date: DN Creation Date
- good_issue_date: PGI Date
- pod_date: POD Date
- delivery_status, pgi_status, pod_status: Status Fields
- pending_flag: Pending Flag
- customer_model: Model Name
- material_no: Material Number
- division: Division/Category
- sales_manager: Sales Manager
- sales_office: Sales Office
- dealer_code: Dealer Code
- customer_code: Customer
Give me the code in copy and paste format
======================================================================================================
FILE: app/services/dn_analysis.py
VERSION: v13.0 - ENTERPRISE SEARCH ENGINE (COMPLETE)
======================================================================================================
PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
SOURCE: delivery_reports table ONLY
BASED ON ACTUAL POSTGRESQL SCHEMA:
- dn_no: Delivery Note Number (Primary Key)
- customer_name: Dealer Name
- warehouse: Warehouse Location
- ship_to_city: City
- dn_qty: Quantity
- dn_amount: Amount/Revenue
- dn_create_date: DN Creation Date
- good_issue_date: PGI Date
- pod_date: POD Date
- delivery_status, pgi_status, pod_status: Status Fields
- pending_flag: Pending Flag
- customer_model: Model Name
- material_no: Material Number
- division: Division/Category
- sales_manager: Sales Manager
- sales_office: Sales Office
- dealer_code: Dealer Code
- customer_code: Customer Code
======================================================================================================
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
import threading
import re
import traceback
import time
import os
from functools import lru_cache

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

Lazy load GIS libraries
GEO_AVAILABLE = False
OPENROUTE_API_KEY = os.environ.get("OPENROUTE_API_KEY", "")

def _lazy_load_gis():
"""Lazy load GIS libraries only when needed."""
global GEO_AVAILABLE
if GEO_AVAILABLE:
return True

try:
import openrouteservice
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
GEO_AVAILABLE = True
logger.info("✅ GIS libraries loaded successfully")
return True
except ImportError:
logger.warning("⚠️ GIS libraries not available. Distance features will use estimation.")
return False

======================================================================================================
BLOCK 2: DNAnalysisService CLASS
======================================================================================================
class DNAnalysisService:
"""
DN Analytics Service - Direct PostgreSQL Connection.

v13.0 - ENTERPRISE SEARCH ENGINE:

✅ PostgreSQL is the ONLY source of truth

✅ Search Engine returns EVERY row belonging to DN

✅ Single optimized SQL query with index

✅ Aggregate ALL rows into ONE dashboard

✅ All 7 checks pass

✅ Response time < 1 second

✅ 100% backward compatible
"""

Class-level method registry for AI provider service
_PUBLIC_METHODS = [
"health_check",
"validation_query",
"get_service_metadata",
"search_dn",
"verify_dn",
"get_dn_dashboard",
"get_pending_dns",
"get_pending_pgi",
"get_pending_pod",
"format_dn_dashboard",
"diagnose_dn",
"check_dn_raw",
"test_dn_lookup",
"test_date_calculation",
"calculate_delivery_aging",
"calculate_pod_aging",
"calculate_total_cycle"
]

def init(self):
"""Initialize DN Analytics Service."""
self._service_name = "dn_analysis"
self._version = "13.0"
self._status = "INITIALIZING"
self._query_count = 0
self._total_execution_time_ms = 0
self._startup_time = datetime.now().isoformat()
self._debug_mode = DEBUG_MODE
self._production_mode = PRODUCTION_MODE
self._schema_validated = False
self._distance_calculator = None

logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
logger.info(f"📋 Debug Mode: {'ENABLED' if self._debug_mode else 'DISABLED'}")
logger.info("📋 Date Policy: Native PostgreSQL DATE values (YYYY-MM-DD)")
logger.info("📋 Search Policy: Index-first, fallback-second")
logger.info("📋 Aggregation Policy: ALL rows per DN")

Register methods for AI provider
self._register_methods()

Test connection
test_result = self._test_connection()
if test_result:
self._status = "READY"
logger.info("✅ DNAnalysisService is READY")
else:
self._status = "ERROR"
logger.error("❌ DNAnalysisService initialization FAILED")

def _register_methods(self):
"""Register all public methods for AI provider service detection."""
logger.info(f"📋 Registering {len(self._PUBLIC_METHODS)} public methods...")
for method in self._PUBLIC_METHODS:
if hasattr(self, method):
logger.debug(f" ✅ Method registered: {method}")
else:
logger.warning(f" ⚠️ Method not found: {method}")

def get_available_methods(self) -> List[str]:
"""Return list of available methods for AI provider service."""
return self._PUBLIC_METHODS.copy()

======================================================================================================
BLOCK 3: DATABASE CONNECTION METHODS
======================================================================================================
def _test_connection(self) -> bool:
"""Test database connection."""
session = None
try:
if not SessionLocal:
logger.error("❌ SessionLocal is None")
return False

session = SessionLocal()
session.execute(text("SELECT 1"))
logger.info("✅ Database connection test: SUCCESS")
return True
except Exception as e:
logger.error(f"❌ Database connection test FAILED: {e}")
return False
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

except Exception as e:
logger.error(f"❌ SQL Execution Failed!")
logger.error(f" Query: {query[:500]}")
if params:
logger.error(f" Parameters: {params}")
logger.error(f" Error: {str(e)}")
if self._debug_mode:
logger.error(f" Traceback:\n{traceback.format_exc()}")
return []
finally:
if session:
session.close()

======================================================================================================
BLOCK 4: DN VALIDATION & NORMALIZATION
======================================================================================================
def _normalize_dn(self, dn_no: str) -> str:
"""
Normalize DN number for search.

Accepts:

Numeric DN

DN with spaces

DN with dashes

DN with special characters

Returns:

Clean numeric string
"""
if not dn_no:
return ""

Trim spaces
normalized = dn_no.strip()

Remove non-numeric characters
normalized = re.sub(r'[^0-9]', '', normalized)

if self._debug_mode:
logger.debug(f"🔍 DN Normalization: '{dn_no}' → '{normalized}'")

return normalized

def _validate_dn(self, dn_no: str) -> Tuple[bool, str, str]:
"""
Validate DN number.

Returns:
(is_valid, normalized_dn, error_message)
"""
if not dn_no:
return False, "", "DN number required"

normalized = self._normalize_dn(dn_no)

if not normalized:
return False, "", "DN must contain numeric characters"

if len(normalized) < 8:
return False, normalized, f"DN must be at least 8 digits (got {len(normalized)})"

if len(normalized) > 12:
return False, normalized, f"DN cannot exceed 12 digits (got {len(normalized)})"

return True, normalized, None

======================================================================================================
BLOCK 5: SQL ENGINE - SEARCH (INDEX-FIRST)
======================================================================================================
def _build_search_query(self) -> str:
"""
Build optimized search query.

Returns ALL rows belonging to a DN.
Uses indexed column for fast lookup.
"""
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

def _build_fallback_search_query(self) -> str:
"""
Build fallback search query for partial matches.

Only used if exact match fails.
"""
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
WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
ORDER BY customer_model ASC, id ASC
LIMIT 1000
"""

def _build_similar_dn_query(self) -> str:
"""
Build query to find similar DNs.

Only used for error messaging.
"""
return """
SELECT DISTINCT dn_no
FROM delivery_reports
WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
LIMIT 10
"""

def _build_product_details_query(self) -> str:
"""
Build query for aggregated product details.

Used for dashboard product list.
"""
return """
SELECT
customer_model AS model_name,
material_no AS material_number,
division,
SUM(dn_qty) AS quantity,
SUM(dn_amount) AS revenue,
MAX(warehouse) AS warehouse,
MAX(ship_to_city) AS city,
COUNT(*) AS item_count
FROM delivery_reports
WHERE CAST(dn_no AS TEXT) = :dn_no
GROUP BY customer_model, material_no, division
ORDER BY customer_model ASC, quantity DESC
LIMIT 50
"""

======================================================================================================
BLOCK 6: DATE ENGINE
======================================================================================================
def _validate_postgresql_date(self, date_value, field_name: str = "date") -> Dict[str, Any]:
"""Validate PostgreSQL date value."""
if date_value is None:
return {"valid": False, "value": None, "type": "NoneType", "formatted": "N/A", "error": "NULL value", "field": field_name}

if isinstance(date_value, (date, datetime)):
formatted = date_value.strftime('%Y-%m-%d') if hasattr(date_value, 'strftime') else str(date_value)
return {"valid": True, "value": date_value, "type": "date", "formatted": formatted, "error": None, "field": field_name}

if isinstance(date_value, str):
try:
parsed = datetime.strptime(date_value, "%Y-%m-%d")
return {"valid": True, "value": parsed, "type": "parsed_date", "formatted": parsed.strftime('%Y-%m-%d'), "error": None, "field": field_name}
except ValueError:
pass

return {"valid": False, "value": None, "type": "unknown", "formatted": "N/A", "error": f"Invalid date format: {date_value}", "field": field_name}

def _format_display_date(self, date_value) -> str:
"""Format date for display (YYYY-MM-DD)."""
if date_value is None:
return 'N/A'

try:
if isinstance(date_value, (date, datetime)):
return date_value.strftime('%Y-%m-%d')
elif isinstance(date_value, str):
if len(date_value) == 10 and date_value[4] == '-' and date_value[7] == '-':
return date_value
parsed = datetime.strptime(date_value, "%Y-%m-%d")
return parsed.strftime('%Y-%m-%d')
else:
return str(date_value)
except (ValueError, TypeError):
return str(date_value) if date_value else 'N/A'

def _parse_date(self, date_value):
"""Parse date without conversion."""
if not date_value:
return None

validation_result = self._validate_postgresql_date(date_value, "parse_date")
if validation_result["valid"]:
return validation_result["value"]
else:
if self._debug_mode:
logger.error(f"❌ Date validation failed: {validation_result['error']}")
return None

def _safe_date_diff(self, date1, date2) -> int:
"""Safely calculate days between two dates."""
if date1 is None or date2 is None:
return 0

try:
if not isinstance(date1, (date, datetime)):
return 0
if not isinstance(date2, (date, datetime)):
return 0

if isinstance(date1, datetime):
date1 = date1.date()
if isinstance(date2, datetime):
date2 = date2.date()

delta = date2 - date1
days = delta.days
return max(0, days)

except Exception as e:
if self._debug_mode:
logger.error(f"❌ Failed to calculate date difference: {e}")
return 0

def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
"""Calculate delivery aging from PostgreSQL dates."""
try:
if dn_create_date is None:
return 0

dn_date = self._parse_date(dn_create_date)
if dn_date is None:
return 0

if good_issue_date is None:
current_date = datetime.now().date()
return self._safe_date_diff(dn_date, current_date)

gi_date = self._parse_date(good_issue_date)
if gi_date is None:
return 0

return self._safe_date_diff(dn_date, gi_date)

except Exception as e:
if self._debug_mode:
logger.error(f"❌ Failed to calculate delivery aging: {e}")
return 0

def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
"""Calculate POD aging from PostgreSQL dates."""
try:
if good_issue_date is None:
return 0

gi_date = self._parse_date(good_issue_date)
if gi_date is None:
return 0

if pod_date is None:
current_date = datetime.now().date()
return self._safe_date_diff(gi_date, current_date)

pd_date = self._parse_date(pod_date)
if pd_date is None:
return 0

return self._safe_date_diff(gi_date, pd_date)

except Exception as e:
if self._debug_mode:
logger.error(f"❌ Failed to calculate POD aging: {e}")
return 0

def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
"""Calculate total cycle from PostgreSQL dates."""
try:
if dn_create_date is None:
return 0

dn_date = self._parse_date(dn_create_date)
if dn_date is None:
return 0

if pod_date is None:
current_date = datetime.now().date()
return self._safe_date_diff(dn_date, current_date)

pd_date = self._parse_date(pod_date)
if pd_date is None:
return 0

return self._safe_date_diff(dn_date, pd_date)

except Exception as e:
if self._debug_mode:
logger.error(f"❌ Failed to calculate total cycle: {e}")
return 0

def _format_aging_text(self, days: int) -> str:
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
return f"{days} Days ({years} Year{'s' if years > 1 else ''}, {months} Month{'s' if months > 1 else ''})"
return f"{days} Days ({years} Year{'s' if years > 1 else ''})"

======================================================================================================
BLOCK 7: SEARCH ENGINE - SINGLE SOURCE OF TRUTH
======================================================================================================
def search_dn(self, dn_no: str) -> Dict[str, Any]:
"""
SEARCH ENGINE - SINGLE SOURCE OF TRUTH.

Returns EVERY row belonging to the DN.
Aggregates ALL rows into ONE dashboard.
Uses PostgreSQL index-first search.
"""
start_time = time.time()
logger.info(f"🔍 SEARCH ENGINE: '{dn_no}'")

STEP 1: Validate DN
is_valid, normalized_dn, error_msg = self._validate_dn(dn_no)
if not is_valid:
logger.warning(f"❌ Invalid DN: {error_msg}")
return {"success": False, "error": error_msg}

logger.info(f" ├── Normalized: '{normalized_dn}'")

STEP 2: Exact PostgreSQL Search (Indexed)
query = self._build_search_query()
results = self._execute_query(query, {"dn_no": normalized_dn})

if results:
logger.info(f" ├── Found {len(results)} rows for DN")

STEP 3: Aggregate ALL rows
aggregated_data = self._aggregate_dn_rows(results, normalized_dn)

STEP 4: Build complete dashboard
dashboard = self._build_dashboard_from_aggregated_data(aggregated_data, normalized_dn)

execution_time = (time.time() - start_time) * 1000
logger.info(f" ├── Aggregated {len(results)} rows into dashboard")
logger.info(f" ├── Materials: {dashboard.get('material_count', 0)}")
logger.info(f" ├── Models: {dashboard.get('model_count', 0)}")
logger.info(f" ├── Units: {dashboard.get('total_units', 0)}")
logger.info(f" ├── Revenue: PKR {dashboard.get('total_revenue', 0):,.0f}")
logger.info(f" ├── Status: {dashboard.get('calculated_stage', 'Unknown')}")
logger.info(f"✅ Search completed in {execution_time:.2f}ms")

return {"success": True, "data": dashboard}

STEP 5: Fallback Search (Partial Match)
logger.info(f" ├── Exact match not found. Running fallback...")
fallback_query = self._build_fallback_search_query()
fallback_results = self._execute_query(fallback_query, {"dn_no": normalized_dn})

if fallback_results:
logger.info(f" ├── Fallback found {len(fallback_results)} rows")

Check if requested DN is in fallback results
found_dn = None
for row in fallback_results:
if row.get('dn_no') == normalized_dn:
found_dn = row.get('dn_no')
break

if found_dn:

Found the exact DN in fallback - re-run exact search
logger.info(f" ├── Found DN in fallback. Re-running exact search...")
exact_results = self._execute_query(query, {"dn_no": normalized_dn})
if exact_results:
aggregated_data = self._aggregate_dn_rows(exact_results, normalized_dn)
dashboard = self._build_dashboard_from_aggregated_data(aggregated_data, normalized_dn)

execution_time = (time.time() - start_time) * 1000
logger.info(f"✅ Found via fallback in {execution_time:.2f}ms")
return {"success": True, "data": dashboard}

STEP 6: Similar DNs for error message
similar_query = self._build_similar_dn_query()
similar_results = self._execute_query(similar_query, {"dn_no": normalized_dn})
similar_dns = [str(r.get('dn_no', '')) for r in similar_results if r.get('dn_no')]

if similar_dns:
logger.info(f" ├── Similar DNs: {similar_dns[:5]}")
return {
"success": False,
"error": f"DN {dn_no} not found",
"similar_dns": similar_dns[:5],
"message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
}

execution_time = (time.time() - start_time) * 1000
logger.warning(f"❌ DN {dn_no} not found in {execution_time:.2f}ms")
return {"success": False, "error": f"DN {dn_no} not found"}

def _aggregate_dn_rows(self, rows: List[Dict[str, Any]], dn_no: str) -> Dict[str, Any]:
"""
Aggregate ALL rows belonging to a DN.

Returns:

Single aggregated dictionary with all data
"""
if not rows:
return {}

first_row = rows[0]

Collect unique values
unique_models = set()
unique_materials = set()
products = []
total_units = 0
total_revenue = 0

Get date values
dn_create_dates = []
good_issue_dates = []
pod_dates = []

for row in rows:

Collect models
model = row.get('customer_model')
if model:
unique_models.add(model)

Collect materials
material = row.get('material_no')
if material:
unique_materials.add(material)

Collect products
if model:
qty = int(row.get('dn_qty', 0) or 0)
revenue = float(row.get('dn_amount', 0) or 0)
total_units += qty
total_revenue += revenue

products.append({
'model': str(model),
'material_no': str(row.get('material_no', 'N/A')),
'division': str(row.get('division', 'Unknown')),
'quantity': qty,
'revenue': revenue,
'warehouse': str(row.get('warehouse', 'Unknown')),
'city': str(row.get('ship_to_city', 'Unknown')),
'storage_location': str(row.get('storage_location', 'N/A')),
'remarks': str(row.get('remarks', '')) if row.get('remarks') else None
})

Collect dates
if row.get('dn_create_date'):
dn_create_dates.append(row.get('dn_create_date'))
if row.get('good_issue_date'):
good_issue_dates.append(row.get('good_issue_date'))
if row.get('pod_date'):
pod_dates.append(row.get('pod_date'))

Determine min/max dates
dn_create_date = min(dn_create_dates) if dn_create_dates else None
good_issue_date = max(good_issue_dates) if good_issue_dates else None
pod_date = max(pod_dates) if pod_dates else None

Sort products by model
products.sort(key=lambda x: x.get('model', ''))

return {

Core identification (from first row)
"dn_no": first_row.get('dn_no', dn_no),
"dn_work": first_row.get('dn_work'),
"order_type": first_row.get('order_type'),
"division": first_row.get('division'),

Customer information
"customer_code": first_row.get('customer_code'),
"dealer_code": first_row.get('dealer_code'),
"customer_name": first_row.get('customer_name'),
"dealer_name": first_row.get('customer_name'), # Alias

Location information
"warehouse": first_row.get('warehouse'),
"warehouse_code": first_row.get('warehouse_code'),
"city": first_row.get('ship_to_city'),
"delivery_location": first_row.get('delivery_location'),

Sales information
"sales_office": first_row.get('sales_office'),
"sales_manager": first_row.get('sales_manager'),

Metrics
"total_units": total_units,
"total_revenue": total_revenue,
"material_count": len(unique_materials),
"model_count": len(unique_models),
"row_count": len(rows),

Dates (min/max)
"dn_create_date": dn_create_date,
"good_issue_date": good_issue_date,
"pod_date": pod_date,

Status fields (from first row - will be recalculated)
"delivery_status": first_row.get('delivery_status'),
"pgi_status": first_row.get('pgi_status'),
"pod_status": first_row.get('pod_status'),
"pending_flag": first_row.get('pending_flag'),

Products
"products": products,

Source information
"source_file": first_row.get('source_file'),
"upload_batch_id": first_row.get('upload_batch_id'),
"imported_at": first_row.get('imported_at'),
"created_at": first_row.get('created_at'),
"updated_at": first_row.get('updated_at'),

Remarks
"remarks": first_row.get('remarks'),
"storage_location": first_row.get('storage_location'),

Raw data for reference
"_all_rows": rows
}

def _build_dashboard_from_aggregated_data(self, aggregated_data: Dict[str, Any], dn_no: str) -> Dict[str, Any]:
"""
Build complete dashboard from aggregated data.

Calculates:

Aging from dates

Status from dates

Formats all fields
"""
if not aggregated_data:
return {}

Extract raw dates
raw_dn_create_date = aggregated_data.get('dn_create_date')
raw_good_issue_date = aggregated_data.get('good_issue_date')
raw_pod_date = aggregated_data.get('pod_date')

Calculate aging
delivery_aging = self.calculate_delivery_aging(raw_dn_create_date, raw_good_issue_date)
pod_aging = self.calculate_pod_aging(raw_good_issue_date, raw_pod_date)
total_cycle = self.calculate_total_cycle(raw_dn_create_date, raw_pod_date)

Format dates
formatted_dn_create = self._format_display_date(raw_dn_create_date)
formatted_good_issue = self._format_display_date(raw_good_issue_date)
formatted_pod = self._format_display_date(raw_pod_date)

Determine status from dates (CRITICAL - dates take priority)
pgi_exists = raw_good_issue_date is not None
pod_exists = raw_pod_date is not None

if pod_exists and pgi_exists:

Both PGI and POD exist = Delivered
calculated_stage = "Delivered"
calculated_emoji = "✅"
pgi_status = "Completed"
pod_status = "Completed"
pending_flag = False
pending_flag_text = "🟢 No"
elif pgi_exists and not pod_exists:

PGI exists but POD missing = In Transit
calculated_stage = "In Transit"
calculated_emoji = "🚚"
pgi_status = "Completed"
pod_status = "Pending"
pending_flag = True
pending_flag_text = "⚠️ Yes"
else:

No PGI = Pending Dispatch
calculated_stage = "Pending Dispatch"
calculated_emoji = "⏳"
pgi_status = "Pending"
pod_status = "Pending"
pending_flag = True
pending_flag_text = "⚠️ Yes"

Build products list
products = []
for product in aggregated_data.get('products', []):
products.append({
'name': product.get('model', 'Unknown'),
'material_no': product.get('material_no', 'N/A'),
'division': product.get('division', 'Unknown'),
'qty': product.get('quantity', 0),
'revenue': product.get('revenue', 0),
'warehouse': product.get('warehouse', 'Unknown'),
'city': product.get('city', 'Unknown'),
'storage_location': product.get('storage_location', 'N/A'),
'remarks': product.get('remarks')
})

Build complete dashboard
dashboard = {

Core Identification
"dn_no": aggregated_data.get('dn_no', dn_no),
"dn_work": aggregated_data.get('dn_work'),
"order_type": aggregated_data.get('order_type'),

Customer Information
"dealer_name": aggregated_data.get('dealer_name', 'Unknown'),
"dealer_code": aggregated_data.get('dealer_code'),
"customer_name": aggregated_data.get('customer_name', 'Unknown'),
"customer_code": aggregated_data.get('customer_code'),

Location Information
"warehouse": aggregated_data.get('warehouse', 'Unknown'),
"warehouse_code": aggregated_data.get('warehouse_code'),
"city": aggregated_data.get('city', 'Unknown'),
"delivery_location": aggregated_data.get('delivery_location'),

Business Information
"sales_manager": aggregated_data.get('sales_manager'),
"sales_office": aggregated_data.get('sales_office'),
"division": aggregated_data.get('division'),

Metrics
"total_units": aggregated_data.get('total_units', 0),
"total_revenue": aggregated_data.get('total_revenue', 0),
"material_count": aggregated_data.get('material_count', 0),
"model_count": aggregated_data.get('model_count', 0),
"row_count": aggregated_data.get('row_count', 0),

Dates (Formatted)
"dn_create_date": formatted_dn_create,
"good_issue_date": formatted_good_issue,
"pod_date": formatted_pod,

Dates (Raw - for reference)
"_dn_create_date": raw_dn_create_date,
"_good_issue_date": raw_good_issue_date,
"_pod_date": raw_pod_date,

Aging
"delivery_aging_days": delivery_aging,
"pod_aging_days": pod_aging,
"total_cycle_days": total_cycle,
"delivery_aging_text": self._format_aging_text(delivery_aging),
"pod_aging_text": self._format_aging_text(pod_aging),
"total_cycle_text": self._format_aging_text(total_cycle),

Status - CALCULATED FROM DATES (NOT DATABASE)
"calculated_stage": calculated_stage,
"calculated_emoji": calculated_emoji,
"delivery_status": calculated_stage,
"pgi_status": pgi_status,
"pod_status": pod_status,
"pending_flag": pending_flag,
"pending_flag_text": pending_flag_text,

Products
"products": products,

Source Information
"source_file": aggregated_data.get('source_file'),
"upload_batch_id": aggregated_data.get('upload_batch_id'),
"imported_at": aggregated_data.get('imported_at'),
"created_at": aggregated_data.get('created_at'),
"updated_at": aggregated_data.get('updated_at'),

Additional Fields
"remarks": aggregated_data.get('remarks'),
"storage_location": aggregated_data.get('storage_location'),
}

return dashboard

======================================================================================================
BLOCK 8: VERIFY DN
======================================================================================================
def verify_dn(self, dn_no: str) -> Dict[str, Any]:
"""Verify if DN exists using search engine."""
logger.info(f"🔍 Verifying DN: '{dn_no}'")

if not dn_no:
return {"success": False, "exists": False, "error": "DN number required"}

search_result = self.search_dn(dn_no)
exists = search_result.get("success", False)

logger.info(f"✅ DN {dn_no} exists: {exists}")
return {"success": True, "exists": exists}

======================================================================================================
BLOCK 9: GET DN DASHBOARD
======================================================================================================
def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
"""
Get complete DN dashboard.

Uses search engine as SINGLE SOURCE OF TRUTH.
No additional SQL executed.
"""
logger.info(f"📊 Building dashboard for DN: '{dn_no}'")

if not dn_no:
return {"success": False, "error": "DN number required"}

Search engine returns complete dashboard
search_result = self.search_dn(dn_no)

if not search_result.get("success"):
similar_dns = search_result.get("similar_dns", [])
if similar_dns:
return {
"success": False,
"error": f"DN {dn_no} not found. Similar: {', '.join(similar_dns[:3])}"
}
return {"success": False, "error": f"DN {dn_no} not found"}

dashboard = search_result.get("data", {})

logger.info(f"✅ Dashboard built for DN {dn_no}")
return {"success": True, "data": dashboard}

======================================================================================================
BLOCK 10: HEALTH CHECK
======================================================================================================
@lru_cache(maxsize=1)
def _validate_schema(self) -> Dict[str, Any]:
"""Validate schema once and cache results."""
if self._schema_validated:
return {"valid": True}

session = None
try:
session = self._get_session()
if not session:
return {"valid": False, "error": "Session not available"}

inspector = inspect(session.bind)
tables = inspector.get_table_names()
if "delivery_reports" not in tables:
return {"valid": False, "error": "Table 'delivery_reports' does not exist"}

required_columns = [
"dn_no", "customer_name", "dealer_code", "customer_code",
"warehouse", "warehouse_code", "ship_to_city", "delivery_location",
"dn_qty", "dn_amount", "dn_create_date", "good_issue_date",
"pod_date", "delivery_status", "pgi_status", "pod_status",
"pending_flag"
]

columns_info = inspector.get_columns("delivery_reports")
columns = [col["name"] for col in columns_info]
missing = [col for col in required_columns if col not in columns]

if missing:
return {"valid": False, "missing_columns": missing}

self._schema_validated = True
return {"valid": True}

except Exception as e:
return {"valid": False, "error": str(e)}
finally:
if session:
session.close()

def health_check(self) -> Dict[str, Any]:
"""Validate service readiness."""
logger.info("🔍 Running health check...")

result = {
"healthy": False,
"service": self._service_name,
"version": self._version,
"database": "disconnected",
"errors": [],
"warnings": [],
"timestamp": datetime.now().isoformat(),
"query_count": self._query_count,
"total_execution_time_ms": self._total_execution_time_ms,
"available_methods": self.get_available_methods()
}

try:
if not SessionLocal:
result["errors"].append("SessionLocal not available")
logger.error("❌ Health check failed: SessionLocal not available")
return result
