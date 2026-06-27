# ==========================================================
# FILE: app/services/excel_import_service.py (DEBUG VERSION)
# ==========================================================

import pandas as pd
import logging
import uuid
import re
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def parse_amount(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = re.sub(r'[^\d.]', '', value.strip())
        if not cleaned:
            return 0
        try:
            return int(float(cleaned))
        except (ValueError, TypeError):
            return 0
    return 0

def parse_qty(value: Any) -> int:
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
        try:
            return datetime.strptime(value, "%d.%m.%Y")
        except ValueError:
            pass
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            pass
        try:
            return datetime.strptime(value, "%d/%m/%Y")
        except ValueError:
            pass
        try:
            return datetime.strptime(value, "%m/%d/%Y")
        except ValueError:
            pass
    return None

def parse_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    return str(value)

# ==========================================================
# EXCEL IMPORT SERVICE
# ==========================================================

class ExcelImportService:
    
    @staticmethod
    def import_delivery_report_excel(
        db: Session,
        file_path: str,
        source_filename: str,
        batch_id: str = None,
        skip_duplicates: bool = False,
        update_existing: bool = False
    ) -> Dict[str, Any]:
        
        logger.info("=" * 80)
        logger.info("📊 EXCEL IMPORT STARTED")
        logger.info("=" * 80)
        logger.info(f"📁 File: {file_path}")
        
        if not batch_id:
            batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            logger.info(f"📋 Generated batch ID: {batch_id}")
        
        try:
            # Read Excel file
            logger.info("📖 Reading Excel file...")
            df = pd.read_excel(file_path)
            total_rows = len(df)
            logger.info(f"📄 Read {total_rows} rows from Excel")
            
            # =============================================
            # STEP 1: SHOW ACTUAL COLUMN NAMES
            # =============================================
            actual_columns = list(df.columns)
            logger.info("=" * 80)
            logger.info("📋 ACTUAL EXCEL COLUMN NAMES (RAW):")
            for i, col in enumerate(actual_columns, 1):
                logger.info(f"   {i}. '{col}' (type: {type(col).__name__})")
            logger.info("=" * 80)
            
            # =============================================
            # STEP 2: SHOW COLUMN NAMES WITH THEIR INDEXES
            # =============================================
            logger.info("📋 COLUMN INDEX MAPPING:")
            for i, col in enumerate(actual_columns):
                logger.info(f"   Index {i}: '{col}'")
            logger.info("=" * 80)
            
            # =============================================
            # STEP 3: SHOW FIRST ROW DATA
            # =============================================
            if total_rows > 0:
                first_row = df.iloc[0]
                logger.info("📋 FIRST ROW DATA:")
                for col in actual_columns:
                    value = first_row.get(col)
                    logger.info(f"   '{col}' = '{value}' (type: {type(value).__name__})")
                logger.info("=" * 80)
            
            # =============================================
            # STEP 4: FIND DN COLUMN
            # =============================================
            logger.info("🔍 Searching for DN column...")
            
            # Try exact match
            dn_column = None
            for col in actual_columns:
                col_str = str(col).strip()
                if col_str in ['DN NO', 'DN No', 'Dn No', 'dn no', 'DN', 'Dn', 'dn']:
                    dn_column = col
                    logger.info(f"   ✅ Found DN column: '{dn_column}'")
                    break
            
            # Try case insensitive
            if not dn_column:
                for col in actual_columns:
                    col_str = str(col).strip().upper()
                    if 'DN' in col_str:
                        dn_column = col
                        logger.info(f"   ✅ Found DN column (contains 'DN'): '{dn_column}'")
                        break
            
            if not dn_column:
                logger.error("❌ No DN column found in Excel!")
                logger.info("   Available columns: " + ", ".join([f"'{c}'" for c in actual_columns]))
                return {
                    "success": False,
                    "error": "No DN column found in Excel file",
                    "available_columns": actual_columns
                }
            
            # =============================================
            # STEP 5: CHECK DN VALUES
            # =============================================
            logger.info(f"🔍 Checking DN values in column: '{dn_column}'")
            dn_values = df[dn_column].head(10).tolist()
            logger.info(f"   First 10 DN values: {dn_values}")
            
            # Count non-empty DN values
            non_empty = df[dn_column].notna().sum()
            logger.info(f"   Non-empty DN values: {non_empty} out of {total_rows}")
            
            if non_empty == 0:
                logger.error("❌ All DN values are empty!")
                return {
                    "success": False,
                    "error": "All DN values are empty",
                    "column": dn_column
                }
            
            # =============================================
            # STEP 6: PROCESS ROWS
            # =============================================
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
                    # Get DN value
                    dn_no = parse_string(row.get(dn_column))
                    logger.info(f"   Row {index + 1}: DN = '{dn_no}'")
                    
                    if not dn_no:
                        logger.warning(f"   ⚠️ Row {index + 1}: Missing DN NO")
                        failed_count += 1
                        validation_errors.append(f"Row {index + 1}: Missing DN NO")
                        continue
                    
                    # Get other columns by exact match
                    # Since we know the column names from your Excel, use exact names
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
                    
                    logger.info(f"   ✅ Row {index + 1}: DN={dn_no}, Amount={dn_amount}, Qty={dn_qty}, Model={customer_model}")
                    
                    # INSERT new record
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
                    logger.info(f"   ✅ Inserted row {index + 1}: DN={dn_no}")
                    
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
            logger.info(f"   Inserted: {inserted_count}")
            logger.info(f"   Updated: {updated_count}")
            logger.info(f"   Skipped: {skipped_count}")
            logger.info(f"   Failed: {failed_count}")
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

# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'ExcelImportService',
    'parse_amount',
    'parse_qty',
    'parse_date',
    'parse_string'
]

# ==========================================================
# END OF FILE
# ==========================================================
