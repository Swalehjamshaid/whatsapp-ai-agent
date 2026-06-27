======================================================================================================
FILE: app/services/excel_import_service.py
VERSION: ENTERPRISE v5.0
PURPOSE: Production-Grade Excel Import Engine for Haier DN & PGI Excel
======================================================================================================
import pandas as pd
import logging
import uuid
import re
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, Any, Optional, List, Tuple, Set
from sqlalchemy.orm import Session
from sqlalchemy import text
from dataclasses import dataclass, field
import time
import traceback

from app.models import DeliveryReport

logger = logging.getLogger(name)

======================================================================================================
BLOCK 1: IMPORT CONFIGURATION
======================================================================================================
BATCH_SIZE = 1000
MAX_ROWS = 100000
DEBUG_MODE = False

======================================================================================================
BLOCK 2: DATA CLASSES FOR IMPORT METRICS
======================================================================================================
@dataclass
class ImportMetrics:
"""Track import metrics for reporting."""
rows_read: int = 0
rows_inserted: int = 0
rows_updated: int = 0
rows_skipped: int = 0
rows_failed: int = 0
duplicate_count: int = 0
missing_dn_count: int = 0
invalid_date_count: int = 0
validation_errors: List[str] = field(default_factory=list)
import_duration: float = 0.0
database_time: float = 0.0
excel_read_time: float = 0.0
commit_time: float = 0.0
batch_id: Optional[str] = None

======================================================================================================
BLOCK 3: CANONICAL COLUMN MAPPING DICTIONARY
======================================================================================================
class ColumnMapper:
"""
Canonical Column Mapping Dictionary.
Maps every Excel column to DeliveryReport fields.
"""

Primary column mappings (exact matches)
PRIMARY_MAPPINGS = {
'dn_no': ['DN NO', 'DN No', 'Dn No', 'dn no', 'DN', 'Dn', 'dn', 'DN_NO', 'DN_Number'],
'dn_work': ['DN Work', 'DN work', 'dn work', 'Work', 'DN_Work', 'DNWork'],
'order_type': ['Order type', 'Order Type', 'order type', 'Order', 'order', 'ORDER_TYPE'],
'division': ['Division', 'division', 'DIVISION'],
'customer_code': ['Customer Code', 'Customer code', 'customer code', 'Customer_Code', 'CustomerCode'],
'dealer_code': ['Dealer Code', 'Dealer code', 'dealer code', 'Dealer_Code', 'DealerCode'],
'customer_name': ['Sold-to-party Name', 'Sold-to-party name', 'Customer Name', 'customer name',
'Customer', 'customer', 'CUSTOMER_NAME', 'CustomerName'],
'customer_model': ['Customer Model', 'Customer model', 'customer model', 'Model', 'model',
'MODEL', 'Customer_Model', 'CustomerModel'],
'material_no': ['Material NO', 'Material No', 'material no', 'Material', 'material',
'MATERIAL_NO', 'MaterialNo', 'Material Number'],
'storage_location': ['Storage Location', 'storage location', 'Storage', 'storage',
'STORAGE_LOCATION', 'StorageLocation'],
'sales_office': ['Sales Office', 'Sales office', 'sales office', 'Office', 'office',
'SALES_OFFICE', 'SalesOffice'],
'sales_manager': ['Sales Manager', 'Sales manager', 'sales manager', 'Manager', 'manager',
'SALES_MANAGER', 'SalesManager'],
'ship_to_city': ['Ship-to City', 'Ship-to city', 'Ship to City', 'City', 'city',
'SHIP_TO_CITY', 'ShipToCity'],
'warehouse': ['Warehouse', 'warehouse', 'WAREHOUSE'],
'warehouse_code': ['Warehouse Code', 'Warehouse code', 'warehouse code', 'WHSE Code',
'WAREHOUSE_CODE', 'WarehouseCode'],
'delivery_location': ['Delivery Location', 'Delivery location', 'delivery location',
'DELIVERY_LOCATION', 'DeliveryLocation'],
'dn_qty': ['DN Qty', 'DN QTY', 'dn qty', 'Qty', 'qty', 'Quantity', 'quantity', 'DN_QTY', 'DNQty'],
'dn_amount': ['DN amount', 'DN Amount', 'dn amount', 'Amount', 'amount', 'AMOUNT', 'DN_AMOUNT', 'DNAmount'],
'dn_create_date': ['DN Create date', 'DN Create Date', 'dn create date', 'Create Date',
'create date', 'DN_CREATE_DATE', 'DNCreateDate'],
'good_issue_date': ['Good issue date', 'Good Issue Date', 'good issue date', 'PGI Date',
'pgi date', 'GOOD_ISSUE_DATE', 'GoodIssueDate'],
'pod_date': ['POD Date', 'POD date', 'pod date', 'POD', 'pod', 'POD_DATE', 'PODDate'],
'remarks': ['Remarks', 'remarks', 'REMARKS', 'Note', 'Notes']
}

Secondary mappings (for fallback)
SECONDARY_MAPPINGS = {
'customer_name': ['Sold To', 'Sold-to', 'Customer Name', 'Name'],
'dn_qty': ['QTY', 'Quantity', 'Qty'],
'dn_amount': ['Amount', 'Price', 'Value'],
'dn_create_date': ['Created Date', 'Creation Date', 'Date'],
'good_issue_date': ['PGI', 'Goods Issue', 'Issue Date'],
'pod_date': ['POD', 'Proof of Delivery', 'Delivery Date'],
}

@classmethod
def map_column(cls, excel_columns: List[str]) -> Dict[str, str]:
"""
Map Excel columns to DeliveryReport fields.

Returns:
Dict mapping Excel column name to DeliveryReport field name
"""
mapping = {}
remaining_columns = list(excel_columns)

Step 1: Try primary mappings (exact/contains matches)
for field, patterns in cls.PRIMARY_MAPPINGS.items():
for col in remaining_columns:
col_str = str(col).strip()
col_upper = col_str.upper()
for pattern in patterns:
pattern_upper = pattern.upper()
if col_upper == pattern_upper or pattern_upper in col_upper:
mapping[col] = field
remaining_columns.remove(col)
break
if col in mapping:
break

Step 2: Try secondary mappings (fallback)
for field, patterns in cls.SECONDARY_MAPPINGS.items():
if field not in mapping.values():
for col in remaining_columns:
col_str = str(col).strip()
col_upper = col_str.upper()
for pattern in patterns:
pattern_upper = pattern.upper()
if pattern_upper in col_upper or col_upper in pattern_upper:
mapping[col] = field
remaining_columns.remove(col)
break
if col in mapping:
break

Step 3: Log unmapped columns
if remaining_columns:
logger.warning(f"⚠️ Unmapped columns: {remaining_columns}")

return mapping

======================================================================================================
BLOCK 4: DATA NORMALIZATION FUNCTIONS
======================================================================================================
def normalize_string(value: Any) -> Optional[str]:
"""Normalize string values - trim spaces, remove duplicate whitespace."""
if value is None:
return None
if isinstance(value, str):

Remove extra spaces and trim
normalized = ' '.join(value.strip().split())
return normalized if normalized else None
if isinstance(value, (int, float)):
return str(value)
return str(value).strip() if str(value) else None

def parse_amount(value: Any) -> Optional[float]:
"""
Parse amount from Excel to float.

Supports:

117,698 → 117698.0

117698 → 117698.0

117698.00 → 117698.0

NULL → None

Empty → None

Invalid → None
"""
if value is None:
return None

if isinstance(value, (int, float)):
return float(value)

if isinstance(value, str):

Remove commas, currency symbols, spaces
cleaned = re.sub(r'[^\d.]', '', value.strip())
if not cleaned:
return None
try:
return float(cleaned)
except (ValueError, TypeError):
return None

return None

def parse_quantity(value: Any) -> Optional[int]:
"""
Parse quantity from Excel to integer.

Supports:

2 → 2

2.0 → 2

NULL → None

Empty → None

Invalid → None
"""
if value is None:
return None

if isinstance(value, int):
return value

if isinstance(value, float):
return int(value) if value.is_integer() else None

if isinstance(value, str):
cleaned = re.sub(r'[^\d]', '', value.strip())
if not cleaned:
return None
try:
return int(cleaned)
except (ValueError, TypeError):
return None

return None

def parse_date_excel(value: Any) -> Optional[date]:
"""
Parse date from Excel to date object.

Supports:

Excel serial dates (e.g., 44562)

datetime objects

Timestamp objects

DD.MM.YYYY (your Excel format)

YYYY-MM-DD

DD/MM/YYYY

MM/DD/YYYY
"""
if value is None:
return None

Already a date object
if isinstance(value, date):
return value

Already a datetime object
if isinstance(value, datetime):
return value.date()

Pandas Timestamp
if isinstance(value, pd.Timestamp):
return value.date()

Excel serial date (integer)
if isinstance(value, (int, float)):
try:

Excel serial date: 1 = 1900-01-01
if value > 59: # Excel serial date offset
return pd.Timestamp('1899-12-30') + pd.Timedelta(days=value)
return None
except:
return None

String formats
if isinstance(value, str):
value = value.strip()
if not value:
return None

Try DD.MM.YYYY (your Excel format - highest priority)
try:
return datetime.strptime(value, "%d.%m.%Y").date()
except ValueError:
pass

Try YYYY-MM-DD
try:
return datetime.strptime(value, "%Y-%m-%d").date()
except ValueError:
pass

Try DD/MM/YYYY
try:
return datetime.strptime(value, "%d/%m/%Y").date()
except ValueError:
pass

Try MM/DD/YYYY
try:
return datetime.strptime(value, "%m/%d/%Y").date()
except ValueError:
pass

Try DD-MM-YYYY
try:
return datetime.strptime(value, "%d-%m-%Y").date()
except ValueError:
pass

return None

def normalize_dn(dn_no: str) -> str:
"""Normalize DN number - remove non-numeric characters."""
if not dn_no:
return ""
return re.sub(r'[^0-9]', '', dn_no.strip())

======================================================================================================
BLOCK 5: STATUS ENGINE
======================================================================================================
class StatusEngine:
"""
Derive status from imported dates.
Never hardcode status values.
"""

@staticmethod
def derive_status(dn_create_date: Optional[date],
good_issue_date: Optional[date],
pod_date: Optional[date]) -> Dict[str, Any]:
"""
Derive delivery status from dates.

Business Rules:

Only DN Create Date → Pending Dispatch

DN Create Date + Good Issue Date → Dispatched (PGI Completed)

DN Create Date + Good Issue Date + POD Date → Delivered
"""
has_dn_create = dn_create_date is not None
has_pgi = good_issue_date is not None
has_pod = pod_date is not None

if has_pod and has_pgi and has_dn_create:

Fully delivered
return {
'delivery_status': 'Delivered',
'pgi_status': 'Completed',
'pod_status': 'Completed',
'pending_flag': False
}
elif has_pgi and has_dn_create:

Dispatched, awaiting POD
return {
'delivery_status': 'Dispatched',
'pgi_status': 'Completed',
'pod_status': 'Pending',
'pending_flag': True
}
elif has_dn_create:

Pending dispatch
return {
'delivery_status': 'Pending Dispatch',
'pgi_status': 'Pending',
'pod_status': 'Pending',
'pending_flag': True
}
else:

Unknown status
return {
'delivery_status': 'Unknown',
'pgi_status': 'Unknown',
'pod_status': 'Unknown',
'pending_flag': True
}

======================================================================================================
BLOCK 6: EXCEL READER
======================================================================================================
class ExcelReader:
"""Safe Excel reader with validation."""

@staticmethod
def read_excel(file_path: str) -> Tuple[pd.DataFrame, List[str], float]:
"""
Read Excel file safely.

Returns:
(DataFrame, column_names, read_time)
"""
start_time = time.time()

try:

Read the Excel file
df = pd.read_excel(file_path, engine='openpyxl')

Remove empty rows (all NaN)
df = df.dropna(how='all')

Remove empty columns (all NaN)
df = df.dropna(axis=1, how='all')

Get actual column names
columns = [str(col).strip() for col in df.columns]

read_time = time.time() - start_time

logger.info(f"📄 Read {len(df)} rows, {len(columns)} columns in {read_time:.2f}s")

return df, columns, read_time

except Exception as e:
logger.error(f"❌ Failed to read Excel: {e}")
raise

======================================================================================================
BLOCK 7: VALIDATION ENGINE
======================================================================================================
class ValidationEngine:
"""Validate data before import."""

@staticmethod
def validate_row(row: Dict[str, Any], index: int) -> List[str]:
"""Validate a single row."""
errors = []

Check required fields
dn_no = row.get('dn_no')
if not dn_no:
errors.append(f"Row {index}: Missing DN NO")

material_no = row.get('material_no')
if not material_no:
errors.append(f"Row {index}: Missing Material NO")

return errors

@staticmethod
def validate_date(date_value: Any, field_name: str) -> Tuple[bool, Optional[date]]:
"""Validate and parse a date."""
parsed = parse_date_excel(date_value)
if parsed is None and date_value is not None:
logger.warning(f"⚠️ Invalid {field_name}: '{date_value}'")
return False, None
return True, parsed

======================================================================================================
BLOCK 8: EXCEL IMPORT SERVICE - ENTERPRISE v5.0
======================================================================================================
class ExcelImportService:
"""
Enterprise Excel Import Service.

Imports Haier DN & PGI Excel files into PostgreSQL.
"""

@staticmethod
def import_delivery_report_excel(
db: Session,
file_path: str,
source_filename: str,
batch_id: str = None,
skip_duplicates: bool = False,
update_existing: bool = False
) -> Dict[str, Any]:
"""
Import delivery report from Excel file.

Args:
db: Database session
file_path: Path to Excel file
source_filename: Original filename
batch_id: Batch ID for tracking
skip_duplicates: Skip duplicate rows (DN + Material)
update_existing: Update existing rows

Returns:
Dict with import metrics
"""

start_time = time.time()
metrics = ImportMetrics()

logger.info("=" * 80)
logger.info("📊 ENTERPRISE EXCEL IMPORT STARTED v5.0")
logger.info("=" * 80)
logger.info(f"📁 File: {file_path}")
logger.info(f"📋 Source: {source_filename}")

Generate batch ID
if not batch_id:
batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
metrics.batch_id = batch_id
logger.info(f"📋 Batch ID: {batch_id}")

try:

=============================================
STEP 1: Read Excel
=============================================
df, excel_columns, excel_read_time = ExcelReader.read_excel(file_path)
metrics.excel_read_time = excel_read_time
metrics.rows_read = len(df)

logger.info(f"📋 Excel columns: {excel_columns}")

=============================================
STEP 2: Map Columns
=============================================
column_mapping = ColumnMapper.map_column(excel_columns)

Reverse mapping: field_name -> excel_column
field_to_column = {}
for col, field in column_mapping.items():
field_to_column[field] = col

logger.info("=" * 80)
logger.info("📋 COLUMN MAPPING:")
for field, col in field_to_column.items():
logger.info(f" {field} ← '{col}'")
logger.info("=" * 80)

Check required fields
required_fields = ['dn_no', 'customer_model', 'material_no']
missing_fields = [f for f in required_fields if f not in field_to_column]
if missing_fields:
error_msg = f"Missing required columns: {missing_fields}"
logger.error(f"❌ {error_msg}")
return {
"success": False,
"error": error_msg,
"available_columns": excel_columns
}

=============================================
STEP 3: Process Rows
=============================================
inserted_count = 0
updated_count = 0
skipped_count = 0
failed_count = 0
duplicate_count = 0
missing_dn_count = 0
validation_errors = []

Track processed DNs and Materials for duplicate detection
processed_records = set()

logger.info("=" * 80)
logger.info("📝 PROCESSING ROWS")
logger.info("=" * 80)

for index, row in df.iterrows():
try:

Get DN
dn_column = field_to_column.get('dn_no')
dn_no_raw = row.get(dn_column) if dn_column else None
dn_no = normalize_dn(str(dn_no_raw)) if dn_no_raw else None

if not dn_no:
logger.warning(f"⚠️ Row {index + 2}: Missing DN NO")
missing_dn_count += 1
failed_count += 1
validation_errors.append(f"Row {index + 2}: Missing DN NO")
continue

Get Material Number
material_column = field_to_column.get('material_no')
material_no = normalize_string(row.get(material_column)) if material_column else None

if not material_no:
logger.warning(f"⚠️ Row {index + 2}: Missing Material NO for DN {dn_no}")
failed_count += 1
validation_errors.append(f"Row {index + 2}: Missing Material NO")
continue

Get Customer Model
model_column = field_to_column.get('customer_model')
customer_model = normalize_string(row.get(model_column)) if model_column else None

Check for duplicates (DN + Material)
record_key = f"{dn_no}_{material_no}"
if record_key in processed_records:
duplicate_count += 1
logger.warning(f"⚠️ Row {index + 2}: Duplicate DN {dn_no} + Material {material_no}")
failed_count += 1
validation_errors.append(f"Row {index + 2}: Duplicate DN {dn_no} + Material {material_no}")
continue
processed_records.add(record_key)

Get all values
def get_field(field_name: str):
col = field_to_column.get(field_name)
if col:
return row.get(col)
return None

Customer Information
customer_name = normalize_string(get_field('customer_name'))
customer_code = normalize_string(get_field('customer_code'))
dealer_code = normalize_string(get_field('dealer_code'))

Location Information
warehouse = normalize_string(get_field('warehouse'))
warehouse_code = normalize_string(get_field('warehouse_code'))
ship_to_city = normalize_string(get_field('ship_to_city'))
delivery_location = normalize_string(get_field('delivery_location'))

Sales Information
sales_office = normalize_string(get_field('sales_office'))
sales_manager = normalize_string(get_field('sales_manager'))
division = normalize_string(get_field('division'))
order_type = normalize_string(get_field('order_type'))
dn_work = normalize_string(get_field('dn_work'))

Material Information
storage_location = normalize_string(get_field('storage_location'))

Quantity and Amount
dn_qty = parse_quantity(get_field('dn_qty'))
dn_amount = parse_amount(get_field('dn_amount'))

Dates
dn_create_date_raw = get_field('dn_create_date')
good_issue_date_raw = get_field('good_issue_date')
pod_date_raw = get_field('pod_date')

dn_create_date = parse_date_excel(dn_create_date_raw)
good_issue_date = parse_date_excel(good_issue_date_raw)
pod_date = parse_date_excel(pod_date_raw)

Remarks
remarks = normalize_string(get_field('remarks'))

Derive status
status = StatusEngine.derive_status(dn_create_date, good_issue_date, pod_date)

Log first few rows
if index < 3:
logger.info(f"📝 Row {index + 2}: DN={dn_no}, Model={customer_model}, "
f"Amount={dn_amount}, Qty={dn_qty}")

Check if record exists
existing = None
if skip_duplicates or update_existing:
existing = db.query(DeliveryReport).filter_by(
dn_no=dn_no,
material_no=material_no
).first()

if existing and update_existing:

UPDATE existing record
existing.dn_work = dn_work
existing.order_type = order_type
existing.division = division
existing.customer_code = customer_code
existing.dealer_code = dealer_code
existing.customer_name = customer_name
existing.customer_model = customer_model
existing.storage_location = storage_location
existing.sales_office = sales_office
existing.sales_manager = sales_manager
existing.ship_to_city = ship_to_city
existing.warehouse = warehouse
existing.warehouse_code = warehouse_code
existing.delivery_location = delivery_location
existing.dn_qty = dn_qty
existing.dn_amount = dn_amount
existing.dn_create_date = dn_create_date
existing.good_issue_date = good_issue_date
existing.pod_date = pod_date
existing.remarks = remarks
existing.delivery_status = status['delivery_status']
existing.pgi_status = status['pgi_status']
existing.pod_status = status['pod_status']
existing.pending_flag = status['pending_flag']
existing.source_file = source_filename
existing.upload_batch_id = batch_id
existing.updated_at = datetime.utcnow()

updated_count += 1
logger.debug(f"✅ Updated row {index + 2}: DN={dn_no}, Material={material_no}")

elif existing and skip_duplicates:

SKIP duplicate
skipped_count += 1
logger.debug(f"⏭️ Skipped duplicate row {index + 2}: DN={dn_no}, Material={material_no}")

else:

INSERT new record
record = DeliveryReport(
dn_no=dn_no,
dn_work=dn_work,
order_type=order_type,
division=division,
customer_code=customer_code,
dealer_code=dealer_code,
customer_name=customer_name,
customer_model=customer_model,
material_no=material_no,
storage_location=storage_location,
sales_office=sales_office,
sales_manager=sales_manager,
ship_to_city=ship_to_city,
warehouse=warehouse,
warehouse_code=warehouse_code,
delivery_location=delivery_location,
dn_qty=dn_qty,
dn_amount=dn_amount,
dn_create_date=dn_create_date,
good_issue_date=good_issue_date,
pod_date=pod_date,
remarks=remarks,
delivery_status=status['delivery_status'],
pgi_status=status['pgi_status'],
pod_status=status['pod_status'],
pending_flag=status['pending_flag'],
source_file=source_filename,
upload_batch_id=batch_id,
imported_at=datetime.utcnow()
)

db.add(record)
inserted_count += 1
logger.debug(f"✅ Inserted row {index + 2}: DN={dn_no}, Material={material_no}")

Commit in batches
if (index + 1) % BATCH_SIZE == 0:
db.commit()
logger.info(f"📊 Committed {index + 1} rows")

except Exception as e:
failed_count += 1
logger.error(f"❌ Failed to import row {index + 2}: {e}")
validation_errors.append(f"Row {index + 2}: {str(e)}")

Final commit
commit_start = time.time()
db.commit()
metrics.commit_time = time.time() - commit_start

Update metrics
metrics.rows_inserted = inserted_count
metrics.rows_updated = updated_count
metrics.rows_skipped = skipped_count
metrics.rows_failed = failed_count
metrics.duplicate_count = duplicate_count
metrics.missing_dn_count = missing_dn_count
metrics.validation_errors = validation_errors
metrics.import_duration = time.time() - start_time

logger.info("=" * 80)
logger.info(f"✅ IMPORT COMPLETED")
logger.info(f" Read: {metrics.rows_read}")
logger.info(f" Inserted: {metrics.rows_inserted}")
logger.info(f" Updated: {metrics.rows_updated}")
logger.info(f" Skipped: {metrics.rows_skipped}")
logger.info(f" Failed: {metrics.rows_failed}")
logger.info(f" Duplicates: {metrics.duplicate_count}")
logger.info(f" Missing DN: {metrics.missing_dn_count}")
logger.info(f" Duration: {metrics.import_duration:.2f}s")
logger.info("=" * 80)

return {
"success": True,
"batch_id": batch_id,
"total_rows": metrics.rows_read,
"inserted_count": metrics.rows_inserted,
"updated_count": metrics.rows_updated,
"skipped_count": metrics.rows_skipped,
"failed_count": metrics.rows_failed,
"duplicate_count": metrics.duplicate_count,
"validation_errors": metrics.validation_errors[:10],
"date_validation_errors": [],
"metrics": {
"excel_read_time": round(metrics.excel_read_time, 2),
"commit_time": round(metrics.commit_time, 2),
"import_duration": round(metrics.import_duration, 2),
"rows_per_second": round(metrics.rows_read / metrics.import_duration, 2) if metrics.import_duration > 0 else 0
}
}

except Exception as e:
logger.error(f"❌ Import failed: {e}")
logger.error(traceback.format_exc())
db.rollback()

metrics.import_duration = time.time() - start_time

return {
"success": False,
"error": str(e),
"batch_id": batch_id,
"total_rows": 0,
"inserted_count": 0,
"updated_count": 0,
"skipped_count": 0,
"failed_count": 0,
"validation_errors": [str(e)]
}

======================================================================================================
BLOCK 9: EXPORTS
======================================================================================================
all = [
'ExcelImportService',
'ColumnMapper',
'StatusEngine',
'ImportMetrics',
'parse_amount',
'parse_quantity',
'parse_date_excel',
'normalize_string',
'normalize_dn'
]

======================================================================================================
END OF FILE
=====================================================================================================
