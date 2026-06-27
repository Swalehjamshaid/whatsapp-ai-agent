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

logger.info(f"📊 Starting Excel import from: {file_path}")

Generate batch ID if not provided
if not batch_id:
batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
logger.info(f"📋 Generated batch ID: {batch_id}")

try:

Read Excel file
df = pd.read_excel(file_path)
total_rows = len(df)
logger.info(f"📄 Read {total_rows} rows from Excel")

Log column names for debugging
logger.info(f"📋 Columns found: {list(df.columns)}")

Track statistics
inserted_count = 0
updated_count = 0
skipped_count = 0
failed_count = 0
validation_errors = []

Process EVERY row
for index, row in df.iterrows():
try:

=============================================
EXACT COLUMN MAPPING FROM YOUR EXCEL
=============================================
Required field
dn_no = parse_string(row.get('DN NO'))
if not dn_no:
logger.warning(f"⚠️ Row {index + 1}: Missing DN NO, skipping")
failed_count += 1
validation_errors.append(f"Row {index + 1}: Missing DN NO")
continue

Parse all values with exact column names
dn_amount = parse_amount(row.get('DN amount'))
dn_qty = parse_qty(row.get('DN Qty'))
dn_work = parse_string(row.get('DN Work'))
order_type = parse_string(row.get('Order type'))
division = parse_string(row.get('Division'))
material_no = parse_string(row.get('Material NO'))
customer_model = parse_string(row.get('Customer Model'))
sales_office = parse_string(row.get('sales office'))
customer_name = parse_string(row.get('Sold-to-party Name'))
ship_to_city = parse_string(row.get('Ship-to City'))
storage_location = parse_string(row.get('storage'))
warehouse = parse_string(row.get('Warehouse'))
sales_manager = parse_string(row.get('Sales Manager'))

dn_create_date = parse_date(row.get('DN Create date'))
good_issue_date = parse_date(row.get('Good issue date'))
pod_date = parse_date(row.get('POD Date'))

Log first few rows for debugging
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
logger.debug(f"✅ Inserted row {index + 1}: DN={dn_no}")

Commit in batches
if (index + 1) % 100 == 0:
db.commit()
logger.info(f"📊 Committed {index + 1} rows")

except Exception as e:
failed_count += 1
logger.error(f"❌ Failed to import row {index + 1}: {e}")
logger.error(f" Row data: {row.to_dict() if hasattr(row, 'to_dict') else str(row)}")
validation_errors.append(f"Row {index + 1}: {str(e)}")

Final commit
db.commit()
logger.info(f"✅ Import completed: {inserted_count} inserted, {updated_count} updated, "
f"{skipped_count} skipped, {failed_count} failed")

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
==========================================================
