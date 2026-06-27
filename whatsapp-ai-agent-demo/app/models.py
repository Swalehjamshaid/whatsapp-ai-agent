==========================================================
FILE: app/services/excel_import_service.py (FIXED - 100% COLUMN MAPPING)
==========================================================
PURPOSE: Excel Import Service - Import ALL rows without skipping
VERSION: v2.1 - 100% COLUMN MAPPING FIXED
==========================================================
import pandas as pd
import logging
import uuid
import re
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.models import DeliveryReport

logger = logging.getLogger(name)

==========================================================
BLOCK 1: HELPER FUNCTIONS FOR DATA PARSING
==========================================================
def parse_amount(value: Any) -> int:
"""
Parse amount from Excel to integer.

Supports:

117,698 → 117698

117698 → 117698

117698.00 → 117698

NULL → 0

Empty → 0
"""
if value is None:
return 0

if isinstance(value, (int, float)):
return int(value)

if isinstance(value, str):

Remove commas, currency symbols, spaces
cleaned = re.sub(r'[^\d.]', '', value.strip())
if not cleaned:
return 0
try:
return int(float(cleaned))
except (ValueError, TypeError):
return 0

return 0

def parse_qty(value: Any) -> int:
"""
Parse quantity from Excel to integer.

Supports:

2 → 2

2.0 → 2

NULL → 0

Empty → 0
"""
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
try:
return int(cleaned)
except (ValueError, TypeError):
return 0

return 0

def parse_date(value: Any) -> Optional[datetime]:
"""
Parse date from Excel to datetime.

Supports:

05.06.2026 → datetime

2026-06-05 → datetime

05/06/2026 → datetime

NULL → None

Empty → None
"""
if value is None:
return None

if isinstance(value, datetime):
return value

if isinstance(value, pd.Timestamp):
return value.to_pydatetime()

if isinstance(value, str):
value = value.strip()
if not value:
return None

Try DD.MM.YYYY format
try:
return datetime.strptime(value, "%d.%m.%Y")
except ValueError:
pass

Try YYYY-MM-DD format
try:
return datetime.strptime(value, "%Y-%m-%d")
except ValueError:
pass

Try DD/MM/YYYY format
try:
return datetime.strptime(value, "%d/%m/%Y")
except ValueError:
pass

Try MM/DD/YYYY format
try:
return datetime.strptime(value, "%m/%d/%Y")
except ValueError:
pass

return None

def parse_string(value: Any) -> Optional[str]:
"""Parse string value from Excel."""
if value is None:
return None
if isinstance(value, str):
return value.strip()
return str(value)

def find_column_by_pattern(columns: List[str], patterns: List[str]) -> Optional[str]:
"""
Find a column by checking multiple patterns (case insensitive).
"""
for col in columns:
col_str = str(col).strip()
col_lower = col_str.lower()
for pattern in patterns:
pattern_lower = pattern.lower()
if col_lower == pattern_lower:
return col
if pattern_lower in col_lower:
return col
if col_lower in pattern_lower:
return col
return None

==========================================================
BLOCK 2: EXCEL IMPORT SERVICE - 100% COLUMN MAPPING
==========================================================
class ExcelImportService:
"""
Excel Import Service - Import ALL rows with 100% column mapping.

FIXED: Exact column names from your Excel file.
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
Import delivery report from Excel file with 100% column mapping.

Excel Columns (EXACT MATCH):

Order type

DN NO

DN amount

DN Qty

DN Work

Division

Material NO

Customer Model

sales office

Sold-to-party Name

Ship-to City

storage

Warehouse

DN Create date

Good issue date

POD Date

Sales Manager
"""

logger.info("=" * 80)
logger.info("📊 EXCEL IMPORT STARTED")
logger.info("=" * 80)
logger.info(f"📁 File: {file_path}")

Generate batch ID if not provided
if not batch_id:
batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
logger.info(f"📋 Generated batch ID: {batch_id}")

try:

Read Excel file
logger.info("📖 Reading Excel file...")
df = pd.read_excel(file_path)
total_rows = len(df)
logger.info(f"📄 Read {total_rows} rows from Excel")

=============================================
STEP 1: SHOW ACTUAL COLUMN NAMES
=============================================
actual_columns = list(df.columns)
logger.info("=" * 80)
logger.info("📋 ACTUAL EXCEL COLUMN NAMES:")
for i, col in enumerate(actual_columns, 1):
logger.info(f" {i}. '{col}'")
logger.info("=" * 80)

=============================================
STEP 2: FIND DN COLUMN (AUTO DETECT)
=============================================
logger.info("🔍 Searching for DN column...")

Try exact match for your known columns
dn_column = None

First try exact match for 'DN NO'
for col in actual_columns:
if str(col).strip() == 'DN NO':
dn_column = col
logger.info(f" ✅ Found DN column: '{dn_column}'")
break

If not found, try case insensitive
if not dn_column:
for col in actual_columns:
col_str = str(col).strip().upper()
if col_str == 'DN NO' or col_str == 'DN' or 'DN' in col_str:
dn_column = col
logger.info(f" ✅ Found DN column: '{dn_column}'")
break

If still not found, try pattern matching
if not dn_column:
dn_column = find_column_by_pattern(actual_columns, [
'DN NO', 'DN No', 'Dn No', 'dn no', 'DN', 'Dn', 'dn',
'DN Number', 'DN number', 'DN_No', 'dn_no'
])
if dn_column:
logger.info(f" ✅ Found DN column via pattern: '{dn_column}'")

if not dn_column:
logger.error("❌ No DN column found in Excel!")
logger.info(" Available columns: " + ", ".join([f"'{c}'" for c in actual_columns]))
return {
"success": False,
"error": "No DN column found in Excel file",
"available_columns": actual_columns
}

=============================================
STEP 3: FIND OTHER COLUMNS (AUTO DETECT)
=============================================
def find_col(patterns):

First try exact match from the Excel
for col in actual_columns:
col_str = str(col).strip()
for pattern in patterns:
if col_str == pattern:
return col

Then try pattern matching
return find_column_by_pattern(actual_columns, patterns)

Find all columns using your exact Excel column names
amount_col = find_col(['DN amount', 'DN Amount', 'dn amount', 'Amount'])
qty_col = find_col(['DN Qty', 'DN QTY', 'dn qty', 'Qty', 'Quantity'])
order_type_col = find_col(['Order type', 'Order Type', 'order type'])
division_col = find_col(['Division', 'division'])
material_no_col = find_col(['Material NO', 'Material No', 'material no', 'Material'])
customer_model_col = find_col(['Customer Model', 'Customer model', 'customer model', 'Model'])
sales_office_col = find_col(['sales office', 'Sales Office', 'sales_office'])
customer_name_col = find_col(['Sold-to-party Name', 'Sold-to-party name', 'Customer Name'])
ship_to_city_col = find_col(['Ship-to City', 'Ship-to city', 'City'])
storage_col = find_col(['storage', 'Storage', 'Storage Location'])
warehouse_col = find_col(['Warehouse', 'warehouse'])
sales_manager_col = find_col(['Sales Manager', 'Sales manager', 'sales manager', 'Manager'])
dn_work_col = find_col(['DN Work', 'DN work', 'dn work', 'Work'])

dn_create_date_col = find_col(['DN Create date', 'DN Create Date', 'dn create date', 'Create Date'])
good_issue_date_col = find_col(['Good issue date', 'Good Issue Date', 'good issue date', 'PGI Date'])
pod_date_col = find_col(['POD Date', 'POD date', 'pod date', 'POD'])

Log what was found
logger.info("=" * 80)
logger.info("📋 COLUMN MAPPING FOUND:")
logger.info(f" DN: '{dn_column}'")
logger.info(f" Amount: '{amount_col}'" if amount_col else " Amount: NOT FOUND")
logger.info(f" Qty: '{qty_col}'" if qty_col else " Qty: NOT FOUND")
logger.info(f" Order Type: '{order_type_col}'" if order_type_col else " Order Type: NOT FOUND")
logger.info(f" Division: '{division_col}'" if division_col else " Division: NOT FOUND")
logger.info(f" Material: '{material_no_col}'" if material_no_col else " Material: NOT FOUND")
logger.info(f" Model: '{customer_model_col}'" if customer_model_col else " Model: NOT FOUND")
logger.info(f" Sales Office: '{sales_office_col}'" if sales_office_col else " Sales Office: NOT FOUND")
logger.info(f" Customer: '{customer_name_col}'" if customer_name_col else " Customer: NOT FOUND")
logger.info(f" City: '{ship_to_city_col}'" if ship_to_city_col else " City: NOT FOUND")
logger.info(f" Storage: '{storage_col}'" if storage_col else " Storage: NOT FOUND")
logger.info(f" Warehouse: '{warehouse_col}'" if warehouse_col else " Warehouse: NOT FOUND")
logger.info(f" Sales Manager: '{sales_manager_col}'" if sales_manager_col else " Sales Manager: NOT FOUND")
logger.info(f" DN Create Date: '{dn_create_date_col}'" if dn_create_date_col else " DN Create Date: NOT FOUND")
logger.info(f" Good Issue Date: '{good_issue_date_col}'" if good_issue_date_col else " Good Issue Date: NOT FOUND")
logger.info(f" POD Date: '{pod_date_col}'" if pod_date_col else " POD Date: NOT FOUND")
logger.info("=" * 80)

=============================================
STEP 4: CHECK DN VALUES
=============================================
non_empty = df[dn_column].notna().sum()
logger.info(f"🔍 Non-empty DN values: {non_empty} out of {total_rows}")

if non_empty == 0:
logger.error("❌ All DN values are empty!")
return {
"success": False,
"error": "All DN values are empty",
"column": dn_column
}

=============================================
STEP 5: PROCESS ROWS
=============================================
inserted_count = 0
updated_count = 0
skipped_count = 0
failed_count = 0
validation_errors = []

logger.info("=" * 80)
logger.info("📝 PROCESSING ROWS")
logger.info("=" * 80)

for index, row in df.iterrows():
try:

Get DN value
dn_no = parse_string(row.get(dn_column))

if not dn_no:
logger.warning(f"⚠️ Row {index + 1}: Missing DN NO")
failed_count += 1
validation_errors.append(f"Row {index + 1}: Missing DN NO")
continue

Get other values from detected columns
dn_amount = parse_amount(row.get(amount_col)) if amount_col else 0
dn_qty = parse_qty(row.get(qty_col)) if qty_col else 0
order_type = parse_string(row.get(order_type_col)) if order_type_col else None
division = parse_string(row.get(division_col)) if division_col else None
material_no = parse_string(row.get(material_no_col)) if material_no_col else None
customer_model = parse_string(row.get(customer_model_col)) if customer_model_col else None
sales_office = parse_string(row.get(sales_office_col)) if sales_office_col else None
customer_name = parse_string(row.get(customer_name_col)) if customer_name_col else None
ship_to_city = parse_string(row.get(ship_to_city_col)) if ship_to_city_col else None
storage_location = parse_string(row.get(storage_col)) if storage_col else None
warehouse = parse_string(row.get(warehouse_col)) if warehouse_col else None
sales_manager = parse_string(row.get(sales_manager_col)) if sales_manager_col else None
dn_work = parse_string(row.get(dn_work_col)) if dn_work_col else None

dn_create_date = parse_date(row.get(dn_create_date_col)) if dn_create_date_col else None
good_issue_date = parse_date(row.get(good_issue_date_col)) if good_issue_date_col else None
pod_date = parse_date(row.get(pod_date_col)) if pod_date_col else None

Log first few rows
if index < 3:
logger.info(f"📝 Row {index + 1}: DN={dn_no}, Model={customer_model}, "
f"Amount={dn_amount}, Qty={dn_qty}")

INSERT new record
record = DeliveryReport(
dn_no=dn_no,
dn_amount=dn_amount,
dn_qty=dn_qty,
dn_work=dn_work,
order_type=order_type,
division=division,
material_no=material_no,
customer_model=customer_model,
sales_office=sales_office,
customer_name=customer_name,
ship_to_city=ship_to_city,
storage_location=storage_location,
warehouse=warehouse,
sales_manager=sales_manager,
dn_create_date=dn_create_date,
good_issue_date=good_issue_date,
pod_date=pod_date,
source_file=source_filename,
upload_batch_id=batch_id,
delivery_status='Pending',
pgi_status='Pending',
pod_status='Pending',
pending_flag=True,
imported_at=datetime.utcnow()
)

db.add(record)
inserted_count += 1
logger.info(f" ✅ Inserted row {index + 1}: DN={dn_no}")

if (index + 1) % 100 == 0:
db.commit()
logger.info(f"📊 Committed {index + 1} rows")

except Exception as e:
failed_count += 1
logger.error(f"❌ Failed to import row {index + 1}: {e}")
validation_errors.append(f"Row {index + 1}: {str(e)}")

db.commit()

logger.info("=" * 80)
logger.info(f"✅ IMPORT COMPLETED")
logger.info(f" Inserted: {inserted_count}")
logger.info(f" Updated: {updated_count}")
logger.info(f" Skipped: {skipped_count}")
logger.info(f" Failed: {failed_count}")
logger.info("=" * 80)

return {
"success": True,
"batch_id": batch_id,
"total_rows": total_rows,
"inserted_count": inserted_count,
"updated_count": updated_count,
"skipped_count": skipped_count,
"failed_count": failed_count,
"validation_errors": validation_errors,
"date_validation_errors": []
}

except Exception as e:
logger.error(f"❌ Import failed: {e}")
import traceback
logger.error(traceback.format_exc())
db.rollback()

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

==========================================================
BLOCK 3: EXPORTS
==========================================================
all = [
'ExcelImportService',
'parse_amount',
'parse_qty',
'parse_date',
'parse_string'
]

==========================================================
END OF FILE
=========================================================
