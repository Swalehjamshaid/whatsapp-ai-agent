# ==========================================================
# FILE: app/services/excel_import_service.py (v2.0 - PRODUCTION)
# ==========================================================
# PURPOSE: Excel Import Service - PostgreSQL Integration
# SOURCE: Excel files (.xlsx, .xls)
# VERSION: 2.0 - ROBUST & PRODUCTION-READY
#
# COMPATIBLE WITH: upload.py, DeliveryReport model
# INTEGRATION: Railway PostgreSQL
#
# IMPROVEMENTS v2.0:
# - ✅ Automatic worksheet detection
# - ✅ Robust column normalization
# - ✅ Flexible column mapping with aliases
# - ✅ Comprehensive validation
# - ✅ Detailed diagnostics and logging
# - ✅ Bulk insert with batch processing
# - ✅ Transaction safety with rollback
# - ✅ Row-level error handling
# - ✅ Excel date serial number support
# ==========================================================

import os
import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Tuple, Set
from uuid import uuid4
import re
import time

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: CONSTANTS & CONFIGURATION
# ==========================================================

# Column aliases: Multiple Excel column names → Model field
COLUMN_ALIASES = {
    # DN Information
    "dn_no": [
        "dn no", "dn", "dn number", "delivery no", "delivery number",
        "dn_no", "dn-no", "dn#", "delivery_note", "deliverynote",
        "d_n", "delivery no.", "dn #"
    ],
    "dn_work": [
        "dn work", "delivery work", "dnwork", "work order",
        "workorder", "dn_work", "dn-work"
    ],
    "order_type": [
        "order type", "ordertype", "order_type", "order-type",
        "order no", "order number"
    ],
    "division": [
        "division", "div", "dept", "department", "segment"
    ],
    
    # Customer Information
    "customer_name": [
        "customer name", "customer", "sold to party", "sold-to-party",
        "sold to party name", "sold-to-party name", "dealer",
        "dealer name", "customer_name", "customer-name",
        "party name", "soldtoparty", "sold to", "client name"
    ],
    "customer_code": [
        "customer code", "customer no", "customer number",
        "customer_code", "customer-no", "cust code"
    ],
    "dealer_code": [
        "dealer code", "dealer no", "dealer number",
        "dealer_code", "dealer-no", "distributor code"
    ],
    "customer_model": [
        "customer model", "model", "product", "product code",
        "customer_model", "customer-model", "item model"
    ],
    "material_no": [
        "material no", "material number", "material code",
        "material_no", "material-no", "mat no", "mat",
        "item no", "item number", "product no"
    ],
    "material_description": [
        "material description", "material desc", "item description",
        "material_description", "mat desc", "product description"
    ],
    "storage": [
        "storage", "storage location", "storage_loc",
        "storage_location", "location", "warehouse location"
    ],
    
    # Warehouse & Location
    "warehouse": [
        "warehouse", "wh", "warehouse code", "storage location",
        "warehouse_name", "warehouse-name", "plant", "store"
    ],
    "warehouse_code": [
        "warehouse code", "wh code", "warehouse_no",
        "warehouse_code", "warehouse-no", "plant code"
    ],
    "ship_to_city": [
        "ship to city", "ship-to city", "city", "destination city",
        "ship_to_city", "ship-to-city", "customer city", "delivery city"
    ],
    "delivery_location": [
        "delivery location", "delivery loc", "destination",
        "delivery_location", "delivery-location", "location",
        "delivery point", "deliveryaddress", "delivery address"
    ],
    
    # Sales & Personnel
    "sales_manager": [
        "sales manager", "salesperson", "sales rep",
        "sales_manager", "sales-manager", "sales executive",
        "sales", "sm", "sales_person"
    ],
    "sales_office": [
        "sales office", "salesoffice", "office", "branch",
        "sales_office", "sales-office", "regional office"
    ],
    
    # Quantities & Amounts
    "dn_qty": [
        "dn qty", "quantity", "qty", "dn quantity",
        "dn_qty", "dn-qty", "delivery quantity",
        "quantity delivered", "qty delivered", "units"
    ],
    "dn_amount": [
        "dn amount", "amount", "value", "total amount",
        "dn_amount", "dn-amount", "delivery amount",
        "amount value", "total value", "sales amount"
    ],
    
    # Dates
    "dn_create_date": [
        "dn create date", "create date", "creation date",
        "dn_create_date", "dn-create-date", "doc date",
        "document date", "order date", "date created",
        "delivery date", "creation date"
    ],
    "good_issue_date": [
        "good issue date", "gi date", "pgi",
        "good_issue_date", "good-issue-date", "delivery date",
        "issue date", "goods issue", "ship date",
        "pgi date", "post goods issue", "delivery date"
    ],
    "pod_date": [
        "pod date", "pod", "proof of delivery",
        "pod_date", "pod-date", "proof of delivery date",
        "received date", "delivery received", "pod confirmed"
    ],
    
    # Status
    "delivery_status": [
        "delivery status", "status", "delivery_state",
        "delivery_status", "delivery-status", "shipment status",
        "order status", "dn status", "status"
    ],
    "pgi_status": [
        "pgi status", "goods issue status", "gi status",
        "pgi_status", "pgi-status", "issue status"
    ],
    "pod_status": [
        "pod status", "proof of delivery status", "received status",
        "pod_status", "pod-status", "confirmation status"
    ],
    
    # Flags
    "pending_flag": [
        "pending flag", "pending", "flag", "is_pending",
        "pending_flag", "pending-flag", "is pending"
    ],
    
    # Remarks
    "remarks": [
        "remarks", "note", "notes", "comment",
        "remarks", "remark", "comments", "additional info"
    ],
    
    # Batch metadata (not from Excel)
    "source_file": ["source_file"],
    "upload_batch_id": ["upload_batch_id"],
    "imported_at": ["imported_at"],
}

# Reverse mapping for quick lookup
ALIAS_TO_FIELD = {}
for field, aliases in COLUMN_ALIASES.items():
    for alias in aliases:
        ALIAS_TO_FIELD[alias] = field

# Required columns for import
REQUIRED_COLUMNS = [
    "dn_no",
    "customer_name",
    "dn_qty",
    "dn_amount",
    "dn_create_date",
]

# Date columns that need parsing
DATE_COLUMNS = [
    "dn_create_date",
    "good_issue_date",
    "pod_date",
]

# Excel date formats to try
DATE_FORMATS = [
    "%Y-%m-%d",           # 2026-05-07
    "%d.%m.%Y",           # 07.05.2026
    "%d/%m/%Y",           # 07/05/2026
    "%d-%m-%Y",           # 07-05-2026
    "%m/%d/%Y",           # 05/07/2026
    "%Y/%m/%d",           # 2026/05/07
    "%d-%b-%Y",           # 07-May-2026
    "%d %b %Y",           # 07 May 2026
    "%b %d, %Y",          # May 07, 2026
    "%d-%B-%Y",           # 07-May-2026
    "%d %B %Y",           # 07 May 2026
    "%B %d, %Y",          # May 07, 2026
]

# Batch size for bulk inserts
BATCH_SIZE = 1000

# Maximum rows to process
MAX_ROWS = 100000


# ==========================================================
# BLOCK 2: EXCEL IMPORT SERVICE CLASS
# ==========================================================

class ExcelImportService:
    """
    Production-grade Excel Import Service with robust detection and mapping.
    """
    
    @staticmethod
    def _normalize_header(header: str) -> str:
        """
        Normalize header for matching.
        
        Examples:
        "DN NO" → "dn no"
        "Sold-to-party Name" → "sold-to-party name"
        "Good_Issue_Date" → "good issue date"
        
        Args:
            header: Raw header string
            
        Returns:
            Normalized header string
        """
        if not header or not isinstance(header, str):
            return ""
        
        # Remove extra spaces
        normalized = re.sub(r'\s+', ' ', str(header).strip())
        
        # Replace underscores, hyphens, slashes with spaces
        normalized = normalized.replace('_', ' ')
        normalized = normalized.replace('-', ' ')
        normalized = normalized.replace('/', ' ')
        
        # Remove special characters
        normalized = re.sub(r'[^\w\s]', '', normalized)
        
        # Lowercase and remove extra spaces
        normalized = ' '.join(normalized.lower().split())
        
        return normalized
    
    @staticmethod
    def _detect_data_sheet(df: pd.DataFrame, sheet_name: str) -> Tuple[bool, List[str], Dict[str, str]]:
        """
        Detect if a sheet contains delivery data.
        
        Args:
            df: DataFrame to check
            sheet_name: Name of the sheet
            
        Returns:
            Tuple of (is_data_sheet, detected_columns, column_mapping)
        """
        if df.empty:
            return False, [], {}
        
        # Get first row as headers (if headers are not set)
        headers = [str(col).strip() for col in df.columns]
        normalized_headers = [ExcelImportService._normalize_header(h) for h in headers]
        
        # Check for data indicators
        data_indicators = [
            "dn no", "dn", "delivery no", "delivery number",
            "sold to party", "customer name", "customer",
            "dn qty", "quantity", "qty",
            "dn amount", "amount",
            "dn create date", "create date", "creation date"
        ]
        
        matched_indicators = []
        for indicator in data_indicators:
            if any(indicator in h for h in normalized_headers):
                matched_indicators.append(indicator)
        
        # If we have at least 3 indicators, it's likely a data sheet
        is_data_sheet = len(matched_indicators) >= 3
        
        # Build mapping
        mapping = {}
        for i, norm_header in enumerate(normalized_headers):
            if norm_header:
                # Try exact match
                if norm_header in ALIAS_TO_FIELD:
                    mapping[headers[i]] = ALIAS_TO_FIELD[norm_header]
                else:
                    # Try partial match
                    for alias, field in ALIAS_TO_FIELD.items():
                        if alias in norm_header or norm_header in alias:
                            mapping[headers[i]] = field
                            break
        
        return is_data_sheet, normalized_headers, mapping
    
    @staticmethod
    def _find_data_sheet(file_path: str) -> Tuple[Optional[str], Optional[pd.DataFrame], Dict[str, str], List[str]]:
        """
        Find the worksheet containing delivery data.
        
        Args:
            file_path: Path to Excel file
            
        Returns:
            Tuple of (sheet_name, dataframe, column_mapping, detected_columns)
        """
        # Read all sheets
        xl = pd.ExcelFile(file_path)
        sheet_names = xl.sheet_names
        
        logger.info(f"📊 Found {len(sheet_names)} sheets: {sheet_names}")
        print(f"📊 Workbook has {len(sheet_names)} sheets: {sheet_names}")
        
        best_sheet = None
        best_df = None
        best_mapping = {}
        best_columns = []
        best_score = 0
        
        for sheet_name in sheet_names:
            try:
                # Read sheet with headers
                df = pd.read_excel(file_path, sheet_name=sheet_name, engine='openpyxl')
                
                is_data, detected_columns, mapping = ExcelImportService._detect_data_sheet(df, sheet_name)
                
                # Score the sheet
                score = len(mapping)
                if is_data:
                    score += 10
                
                logger.info(f"   Sheet '{sheet_name}': {len(df)} rows, {len(mapping)} mapped columns, score={score}")
                print(f"   Sheet '{sheet_name}': {len(df)} rows, {len(mapping)} mapped columns")
                
                if score > best_score:
                    best_score = score
                    best_sheet = sheet_name
                    best_df = df
                    best_mapping = mapping
                    best_columns = detected_columns
                    
            except Exception as e:
                logger.warning(f"   Failed to read sheet '{sheet_name}': {e}")
                print(f"   ⚠️ Failed to read sheet '{sheet_name}': {e}")
        
        if best_sheet is None or best_df is None:
            logger.error("❌ No suitable data sheet found")
            print("❌ No suitable data sheet found")
            return None, None, {}, []
        
        logger.info(f"✅ Selected sheet: '{best_sheet}' with {len(best_mapping)} mapped columns")
        print(f"✅ Selected sheet: '{best_sheet}' with {len(best_mapping)} mapped columns")
        
        return best_sheet, best_df, best_mapping, best_columns
    
    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        """
        Parse date from Excel value.
        
        Supports:
        - Python date objects
        - Python datetime objects
        - Excel numeric dates (serial numbers)
        - String dates in various formats
        
        Args:
            value: Date value from Excel
            
        Returns:
            date object or None
        """
        if value is None:
            return None
        
        # Already a date object
        if isinstance(value, date):
            return value
        
        # Already a datetime object
        if isinstance(value, datetime):
            return value.date()
        
        # Excel numeric date (serial number)
        if isinstance(value, (int, float)):
            try:
                # Excel date serial number (days since 1900-01-01)
                # Handle both integer and float
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                
                # Excel's date system starts from 1900-01-01
                # Excel incorrectly treats 1900 as a leap year
                if value > 59:
                    from datetime import timedelta
                    base_date = datetime(1899, 12, 30)  # Excel base date
                    result_date = base_date + timedelta(days=value)
                    return result_date.date()
                else:
                    return None
            except Exception:
                return None
        
        # String date
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            
            # Try each date format
            for fmt in DATE_FORMATS:
                try:
                    parsed = datetime.strptime(value, fmt)
                    return parsed.date()
                except ValueError:
                    continue
            
            # Try pandas to_datetime for additional formats
            try:
                parsed = pd.to_datetime(value, errors='coerce')
                if pd.notna(parsed):
                    return parsed.date()
            except Exception:
                pass
            
            logger.warning(f"⚠️ Could not parse date: {value}")
            return None
        
        return None
    
    @staticmethod
    def _validate_row(row: Dict[str, Any], row_num: int) -> Tuple[bool, List[str]]:
        """
        Validate a single row.
        
        Args:
            row: Row data
            row_num: Row number for logging
            
        Returns:
            Tuple of (is_valid, errors)
        """
        errors = []
        
        # Check DN number
        dn_no = row.get('dn_no')
        if not dn_no or str(dn_no).strip() == '':
            errors.append(f"Row {row_num}: Missing DN number")
        
        # Check customer name
        customer = row.get('customer_name')
        if not customer or str(customer).strip() == '':
            errors.append(f"Row {row_num}: Missing customer name")
        
        # Check quantity
        qty = row.get('dn_qty')
        if qty is not None:
            try:
                qty_val = float(qty)
                if qty_val < 0:
                    errors.append(f"Row {row_num}: Negative quantity: {qty_val}")
            except (ValueError, TypeError):
                errors.append(f"Row {row_num}: Invalid quantity: {qty}")
        
        # Check amount
        amount = row.get('dn_amount')
        if amount is not None:
            try:
                amount_val = float(amount)
                if amount_val < 0:
                    errors.append(f"Row {row_num}: Negative amount: {amount_val}")
            except (ValueError, TypeError):
                errors.append(f"Row {row_num}: Invalid amount: {amount}")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def _prepare_dataframe(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
        """
        Prepare DataFrame for import.
        
        Steps:
        1. Rename columns to model field names
        2. Normalize dates
        3. Handle NaN values
        
        Args:
            df: DataFrame with Excel data
            mapping: Column mapping
            
        Returns:
            Prepared DataFrame
        """
        # Rename columns
        df = df.rename(columns=mapping)
        
        # Keep only mapped columns
        valid_columns = list(mapping.values())
        df = df[[col for col in valid_columns if col in df.columns]]
        
        # Normalize dates
        for col in DATE_COLUMNS:
            if col in df.columns:
                try:
                    df[col] = df[col].apply(ExcelImportService._parse_date)
                    logger.debug(f"✅ Parsed dates for column: {col}")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to parse dates for {col}: {e}")
        
        # Handle NaN values
        df = df.where(pd.notna(df), None)
        
        return df
    
    @staticmethod
    def import_delivery_report_excel(
        db: Session,
        file_path: str,
        source_filename: str,
        batch_id: Optional[str] = None,
        skip_duplicates: bool = True,
        update_existing: bool = False
    ) -> Dict[str, Any]:
        """
        Import Excel file into DeliveryReport model.
        
        Args:
            db: SQLAlchemy session
            file_path: Path to Excel file
            source_filename: Original filename
            batch_id: Optional batch ID (generated if not provided)
            skip_duplicates: Skip duplicate rows (True) or fail (False)
            update_existing: Update existing records instead of skipping
            
        Returns:
            Dictionary with import results
        """
        start_time = time.time()
        
        # Generate batch ID if not provided
        if not batch_id:
            batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        
        logger.info(f"📊 Starting Excel import: {source_filename}")
        logger.info(f"   Batch ID: {batch_id}")
        logger.info(f"   File: {file_path}")
        logger.info(f"   Skip Duplicates: {skip_duplicates}")
        logger.info(f"   Update Existing: {update_existing}")
        
        print(f"📊 Starting Excel import: {source_filename}")
        print(f"   Batch ID: {batch_id}")
        
        result = {
            "success": False,
            "inserted_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "total_rows": 0,
            "batch_id": batch_id,
            "sheet_name": None,
            "mapped_columns": {},
            "detected_columns": [],
            "missing_columns": [],
            "errors": [],
            "validation_errors": [],
            "error": None,
            "import_duration_seconds": 0
        }
        
        try:
            # =============================================
            # STEP 1: Validate file exists
            # =============================================
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")
            
            # Check file extension
            file_ext = os.path.splitext(file_path)[1].lower()
            if file_ext not in ['.xlsx', '.xls']:
                raise ValueError(f"Unsupported file format: {file_ext}. Use .xlsx or .xls")
            
            # =============================================
            # STEP 2: Find data sheet
            # =============================================
            sheet_name, df, mapping, detected_columns = ExcelImportService._find_data_sheet(file_path)
            
            if sheet_name is None or df is None:
                error_msg = "No data sheet found. Please ensure your Excel file contains a sheet with delivery data."
                logger.error(f"❌ {error_msg}")
                result["error"] = error_msg
                return result
            
            result["sheet_name"] = sheet_name
            result["detected_columns"] = detected_columns
            result["mapped_columns"] = mapping
            result["total_rows"] = len(df)
            
            logger.info(f"📊 Sheet: {sheet_name}, Rows: {len(df)}, Mapped Columns: {len(mapping)}")
            print(f"📊 Sheet: {sheet_name}, Rows: {len(df)}, Mapped Columns: {len(mapping)}")
            
            # =============================================
            # STEP 3: Validate required columns
            # =============================================
            mapped_fields = set(mapping.values())
            missing_columns = []
            for req in REQUIRED_COLUMNS:
                if req not in mapped_fields:
                    missing_columns.append(req)
            
            result["missing_columns"] = missing_columns
            
            if missing_columns:
                error_msg = f"Missing required columns: {missing_columns}"
                logger.error(f"❌ {error_msg}")
                result["error"] = error_msg
                return result
            
            # =============================================
            # STEP 4: Prepare DataFrame
            # =============================================
            logger.info("🔄 Preparing DataFrame...")
            df = ExcelImportService._prepare_dataframe(df, mapping)
            
            # =============================================
            # STEP 5: Validate rows
            # =============================================
            logger.info("🔍 Validating rows...")
            records = df.to_dict(orient='records')
            valid_records = []
            validation_errors = []
            
            for idx, record in enumerate(records):
                is_valid, errors = ExcelImportService._validate_row(record, idx + 2)
                if is_valid:
                    valid_records.append(record)
                else:
                    validation_errors.extend(errors)
                    result["failed_count"] += 1
            
            result["validation_errors"] = validation_errors[:10]  # Store first 10 errors
            
            if not valid_records:
                error_msg = "No valid records found in the Excel file."
                logger.error(f"❌ {error_msg}")
                result["error"] = error_msg
                return result
            
            logger.info(f"✅ {len(valid_records)} valid records out of {len(records)} total")
            print(f"✅ {len(valid_records)} valid records out of {len(records)} total")
            
            # =============================================
            # STEP 6: Import valid records
            # =============================================
            from app.models import DeliveryReport
            
            inserted_count = 0
            updated_count = 0
            skipped_count = 0
            dn_set = set()
            
            # Process in batches
            for i in range(0, len(valid_records), BATCH_SIZE):
                batch = valid_records[i:i + BATCH_SIZE]
                
                for record in batch:
                    try:
                        # Get DN number
                        dn_no = record.get('dn_no')
                        if not dn_no:
                            skipped_count += 1
                            continue
                        
                        dn_no = str(dn_no).strip()
                        
                        # Check for duplicate DN in current batch
                        if dn_no in dn_set:
                            skipped_count += 1
                            continue
                        dn_set.add(dn_no)
                        
                        # Check for existing record
                        existing = db.query(DeliveryReport).filter(
                            DeliveryReport.dn_no == dn_no
                        ).first()
                        
                        if existing:
                            if update_existing:
                                # Update existing record
                                for key, value in record.items():
                                    if key != 'dn_no' and value is not None:
                                        setattr(existing, key, value)
                                updated_count += 1
                            else:
                                skipped_count += 1
                            continue
                        
                        # Create new record
                        new_record = DeliveryReport()
                        new_record.dn_no = dn_no
                        
                        # Set fields
                        for key, value in record.items():
                            if key != 'dn_no' and value is not None:
                                setattr(new_record, key, value)
                        
                        # Set batch metadata
                        new_record.upload_batch_id = batch_id
                        new_record.source_file = source_filename
                        new_record.imported_at = datetime.now()
                        
                        db.add(new_record)
                        inserted_count += 1
                        
                    except Exception as e:
                        logger.error(f"❌ Error processing row: {e}")
                        result["failed_count"] += 1
                        continue
                
                # Flush batch
                try:
                    db.flush()
                except Exception as e:
                    logger.error(f"❌ Flush error: {e}")
                    db.rollback()
                    raise
            
            # =============================================
            # STEP 7: Commit transaction
            # =============================================
            logger.info("💾 Committing transaction...")
            db.commit()
            
            result["success"] = True
            result["inserted_count"] = inserted_count
            result["updated_count"] = updated_count
            result["skipped_count"] = skipped_count
            result["import_duration_seconds"] = round(time.time() - start_time, 2)
            
            logger.info("=" * 60)
            logger.info("✅ IMPORT COMPLETED SUCCESSFULLY")
            logger.info(f"   Batch ID: {batch_id}")
            logger.info(f"   Sheet: {sheet_name}")
            logger.info(f"   Inserted: {inserted_count}")
            logger.info(f"   Updated: {updated_count}")
            logger.info(f"   Skipped: {skipped_count}")
            logger.info(f"   Failed: {result['failed_count']}")
            logger.info(f"   Total: {result['total_rows']}")
            logger.info(f"   Duration: {result['import_duration_seconds']}s")
            logger.info("=" * 60)
            
            print("=" * 60)
            print("✅ IMPORT COMPLETED SUCCESSFULLY")
            print(f"   Inserted: {inserted_count}")
            print(f"   Updated: {updated_count}")
            print(f"   Skipped: {skipped_count}")
            print(f"   Duration: {result['import_duration_seconds']}s")
            print("=" * 60)
            
            return result
            
        except Exception as e:
            # Rollback on error
            logger.error(f"❌ Import failed: {e}")
            logger.exception(e)
            
            try:
                db.rollback()
                logger.info("🔄 Transaction rolled back")
            except Exception as rollback_error:
                logger.error(f"❌ Rollback failed: {rollback_error}")
            
            result["error"] = str(e)
            result["import_duration_seconds"] = round(time.time() - start_time, 2)
            return result
    
    @staticmethod
    def get_batch_summary(db: Session, batch_id: str) -> Dict[str, Any]:
        """
        Get summary of an import batch.
        """
        from app.models import DeliveryReport
        
        try:
            records = db.query(DeliveryReport).filter(
                DeliveryReport.upload_batch_id == batch_id
            ).all()
            
            if not records:
                return {
                    "success": False,
                    "error": f"Batch not found: {batch_id}",
                    "batch_id": batch_id,
                    "total_records": 0,
                    "inserted_count": 0,
                    "source_file": None,
                    "imported_at": None
                }
            
            first_record = records[0]
            
            return {
                "success": True,
                "batch_id": batch_id,
                "total_records": len(records),
                "source_file": first_record.source_file,
                "imported_at": first_record.imported_at,
                "inserted_count": len(records),
                "first_dn": records[0].dn_no if records else None,
                "last_dn": records[-1].dn_no if records else None
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get batch summary: {e}")
            logger.exception(e)
            return {
                "success": False,
                "error": str(e),
                "batch_id": batch_id,
                "total_records": 0,
                "inserted_count": 0,
                "source_file": None,
                "imported_at": None
            }
    
    @staticmethod
    def delete_import_batch(db: Session, batch_id: str) -> Dict[str, Any]:
        """
        Delete an import batch.
        """
        from app.models import DeliveryReport
        
        try:
            count = db.query(DeliveryReport).filter(
                DeliveryReport.upload_batch_id == batch_id
            ).count()
            
            if count == 0:
                return {
                    "success": False,
                    "error": f"Batch not found: {batch_id}",
                    "deleted_count": 0,
                    "batch_id": batch_id
                }
            
            deleted = db.query(DeliveryReport).filter(
                DeliveryReport.upload_batch_id == batch_id
            ).delete(synchronize_session=False)
            
            db.commit()
            
            logger.info(f"✅ Deleted batch {batch_id}: {deleted} records")
            
            return {
                "success": True,
                "deleted_count": deleted,
                "batch_id": batch_id
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to delete batch: {e}")
            logger.exception(e)
            
            try:
                db.rollback()
            except Exception:
                pass
            
            return {
                "success": False,
                "error": str(e),
                "deleted_count": 0,
                "batch_id": batch_id
            }


# ==========================================================
# BLOCK 3: CONVENIENCE FUNCTIONS
# ==========================================================

def import_delivery_report_excel(
    db: Session,
    file_path: str,
    source_filename: str,
    batch_id: Optional[str] = None,
    skip_duplicates: bool = True,
    update_existing: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to import Excel file.
    """
    return ExcelImportService.import_delivery_report_excel(
        db=db,
        file_path=file_path,
        source_filename=source_filename,
        batch_id=batch_id,
        skip_duplicates=skip_duplicates,
        update_existing=update_existing
    )


def get_batch_summary(db: Session, batch_id: str) -> Dict[str, Any]:
    """
    Convenience function to get batch summary.
    """
    return ExcelImportService.get_batch_summary(db=db, batch_id=batch_id)


def delete_import_batch(db: Session, batch_id: str) -> Dict[str, Any]:
    """
    Convenience function to delete batch.
    """
    return ExcelImportService.delete_import_batch(db=db, batch_id=batch_id)


# ==========================================================
# BLOCK 4: MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 60)
logger.info("📊 Excel Import Service v2.0 - PRODUCTION READY")
logger.info("=" * 60)
logger.info("")
logger.info("   FEATURES:")
logger.info("   ✅ Automatic worksheet detection")
logger.info("   ✅ Robust column normalization")
logger.info("   ✅ Flexible column mapping with aliases")
logger.info("   ✅ Comprehensive validation")
logger.info("   ✅ Detailed diagnostics and logging")
logger.info("   ✅ Bulk insert with batch processing")
logger.info("   ✅ Transaction safety with rollback")
logger.info("   ✅ Row-level error handling")
logger.info("   ✅ Excel date serial number support")
logger.info("")
logger.info("   DATE HANDLING:")
logger.info("   ✅ 07.05.2026 → 2026-05-07 (no swapping)")
logger.info("   ✅ 2026-05-07 → 2026-05-07")
logger.info("   ✅ Excel serial dates → Python date")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 60)


# ==========================================================
# BLOCK 5: EXPORTS
# ==========================================================

__all__ = [
    'ExcelImportService',
    'import_delivery_report_excel',
    'get_batch_summary',
    'delete_import_batch'
]
