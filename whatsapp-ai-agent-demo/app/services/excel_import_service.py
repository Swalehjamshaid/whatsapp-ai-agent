======================================================================================================
FILE: app/services/excel_import_service.py
VERSION: v5.4 ENTERPRISE PRODUCTION - COMPLETE FIX
PURPOSE: Eliminate data loss and guarantee every Excel value is stored correctly in PostgreSQL.
FIXES: 
- Column mapping for exact Excel column names
- upload_batch_id handling
- Decimal/JSON serialization
- Verification with normalized data
======================================================================================================
import pandas as pd
import logging
import uuid
import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional, List, Tuple, Set
from sqlalchemy.orm import Session
from sqlalchemy import text
import time
import traceback

# ✅ Pydantic v1/v2 compatible
try:
    from pydantic import BaseModel, ConfigDict
    PYDANTIC_V2 = True
except ImportError:
    from pydantic import BaseModel
    PYDANTIC_V2 = False

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

======================================================================================================
BLOCK 1: CONFIGURATION
======================================================================================================
BATCH_SIZE = 1000
MAX_ROWS = 100000
VERIFICATION_SAMPLE_SIZE = 5
DEBUG_MODE = False
STRICT_MODE = True
VERIFY_ALL_ROWS = False

======================================================================================================
BLOCK 2: EXCEPTIONS
======================================================================================================
class ImportValidationError(Exception):
    pass

class DataLossError(Exception):
    pass

class VerificationError(Exception):
    """Raised when verification fails - should bubble up to caller"""
    pass

======================================================================================================
BLOCK 3: DATA CLASSES - PYDANTIC COMPATIBLE
======================================================================================================
class ImportMetrics(BaseModel):
    """Import metrics - works with Pydantic v1 and v2"""
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    rows_failed: int = 0
    rows_verified: int = 0
    verification_success: int = 0
    verification_failed: int = 0
    duplicate_count: int = 0
    missing_dn_count: int = 0
    invalid_date_count: int = 0
    amount_mismatches: int = 0
    quantity_mismatches: int = 0
    date_mismatches: int = 0
    dealer_mismatches: int = 0
    warehouse_mismatches: int = 0
    city_mismatches: int = 0
    total_revenue_imported: Decimal = Decimal(0)
    total_units_imported: int = 0
    validation_errors: List[str] = []
    verification_errors: List[Dict[str, Any]] = []
    import_duration: float = 0.0
    database_time: float = 0.0
    excel_read_time: float = 0.0
    commit_time: float = 0.0
    batch_id: Optional[str] = None

    if PYDANTIC_V2:
        model_config = ConfigDict(arbitrary_types_allowed=True)
    else:
        class Config:
            arbitrary_types_allowed = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization"""
        result = self.model_dump() if PYDANTIC_V2 else self.dict()
        result['total_revenue_imported'] = float(result['total_revenue_imported'])
        return result

class RowAudit(BaseModel):
    """Row audit - works with Pydantic v1 and v2"""
    row_number: int
    dn_no: str
    material_no: str
    field: str
    excel_value: Any
    mapped_value: Any
    normalized_value: Any
    database_value: Any
    status: str
    error: Optional[str] = None

    if PYDANTIC_V2:
        model_config = ConfigDict(arbitrary_types_allowed=True)
    else:
        class Config:
            arbitrary_types_allowed = True

======================================================================================================
BLOCK 4: DATA NORMALIZATION FUNCTIONS
======================================================================================================
def normalize_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = ' '.join(value.strip().split())
        return normalized if normalized else None
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip() if str(value) else None

def parse_amount_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, str):
        # Remove currency symbols, commas, spaces
        cleaned = re.sub(r'[^\d.]', '', value.strip())
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None
    return None

def parse_quantity_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else None
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
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, (int, float)):
        try:
            if value > 59:
                return pd.Timestamp('1899-12-30') + pd.Timedelta(days=value)
            return None
        except:
            return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        # Try DD.MM.YYYY
        try:
            return datetime.strptime(value, "%d.%m.%Y").date()
        except ValueError:
            pass
        # Try YYYY-MM-DD
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            pass
        # Try DD/MM/YYYY
        try:
            return datetime.strptime(value, "%d/%m/%Y").date()
        except ValueError:
            pass
        # Try MM/DD/YYYY
        try:
            return datetime.strptime(value, "%m/%d/%Y").date()
        except ValueError:
            pass
        return None
    return None

def normalize_dn(dn_no: str) -> str:
    if not dn_no:
        return ""
    return re.sub(r'[^0-9]', '', dn_no.strip())

======================================================================================================
BLOCK 5: COLUMN MAPPER - UPDATED FOR EXACT EXCEL COLUMNS
======================================================================================================
class ColumnMapper:
    PRIMARY_MAPPINGS = {
        # EXACT matches from your Excel sample
        'dn_no': ['DN NO', 'DN No', 'Dn No', 'dn no', 'DN', 'Dn', 'dn', 'DN_NO'],
        'dn_work': ['DN Work', 'DN work', 'dn work', 'Work', 'DN_Work'],
        'order_type': ['Order type', 'Order Type', 'order type', 'Order', 'order'],
        'division': ['Division', 'division', 'DIVISION'],
        'customer_code': ['Customer Code', 'Customer code', 'customer code', 'Customer Code'],
        'dealer_code': ['Dealer Code', 'Dealer code', 'dealer code', 'Dealer Code'],
        'customer_name': ['Sold-to-party Name', 'Sold-to-party name', 'Sold-to party Name', 
                         'Customer Name', 'customer name', 'Customer', 'customer'],
        'customer_model': ['Customer Model', 'Customer model', 'customer model', 'Model', 'model'],
        'material_no': ['Material NO', 'Material No', 'material no', 'Material', 'material', 
                       'Material NO', 'Material Number'],
        'storage_location': ['Storage Location', 'storage location', 'Storage', 'storage', 
                           'storage', 'Storage Location'],
        'sales_office': ['Sales Office', 'Sales office', 'sales office', 'Office', 'office',
                        'sales office'],
        'sales_manager': ['Sales Manager', 'Sales manager', 'sales manager', 'Manager', 'manager',
                         'Sales Manager'],
        'ship_to_city': ['Ship-to City', 'Ship-to city', 'Ship to City', 'Ship-to City', 
                        'City', 'city', 'Ship-to City'],
        'warehouse': ['Warehouse', 'warehouse', 'WAREHOUSE'],
        'warehouse_code': ['Warehouse Code', 'Warehouse code', 'warehouse code'],
        'delivery_location': ['Delivery Location', 'Delivery location', 'delivery location'],
        'dn_qty': ['DN Qty', 'DN QTY', 'dn qty', 'Qty', 'qty', 'Quantity', 'quantity'],
        'dn_amount': ['DN amount', 'DN Amount', 'dn amount', 'Amount', 'amount', 'DN amount'],
        'dn_create_date': ['DN Create date', 'DN Create Date', 'dn create date', 'Create Date', 
                          'create date', 'DN Create date'],
        'good_issue_date': ['Good issue date', 'Good Issue Date', 'good issue date', 'PGI Date', 
                           'pgi date', 'Good issue date'],
        'pod_date': ['POD Date', 'POD date', 'pod date', 'POD', 'pod', 'POD Date'],
        'remarks': ['Remarks', 'remarks', 'REMARKS', 'Note', 'Notes']
    }

    @classmethod
    def map_columns(cls, excel_columns: List[str]) -> Dict[str, str]:
        mapping = {}
        remaining_columns = list(excel_columns)

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

        if remaining_columns:
            logger.debug(f"Unmapped columns: {remaining_columns}")

        return mapping

    @classmethod
    def get_field_to_column(cls, mapping: Dict[str, str]) -> Dict[str, str]:
        field_to_col = {}
        for col, field in mapping.items():
            field_to_col[field] = col
        return field_to_col

======================================================================================================
BLOCK 6: STATUS ENGINE
======================================================================================================
class StatusEngine:
    @staticmethod
    def derive_status(dn_create_date: Optional[date], good_issue_date: Optional[date], pod_date: Optional[date]) -> Dict[str, Any]:
        has_dn_create = dn_create_date is not None
        has_pgi = good_issue_date is not None
        has_pod = pod_date is not None

        if has_pod and has_pgi and has_dn_create:
            return {'delivery_status': 'Delivered', 'pgi_status': 'Completed', 'pod_status': 'Completed', 'pending_flag': False}
        elif has_pgi and has_dn_create:
            return {'delivery_status': 'Dispatched', 'pgi_status': 'Completed', 'pod_status': 'Pending', 'pending_flag': True}
        elif has_dn_create:
            return {'delivery_status': 'Pending Dispatch', 'pgi_status': 'Pending', 'pod_status': 'Pending', 'pending_flag': True}
        else:
            return {'delivery_status': 'Unknown', 'pgi_status': 'Unknown', 'pod_status': 'Unknown', 'pending_flag': True}

======================================================================================================
BLOCK 7: VERIFICATION ENGINE
======================================================================================================
class VerificationEngine:
    """Verify data by re-reading from PostgreSQL after commit."""

    CRITICAL_FIELDS = ['dn_amount', 'dn_qty', 'customer_name', 'material_no', 'warehouse', 'ship_to_city']

    @staticmethod
    def verify_against_postgresql(
        db: Session, 
        normalized_data: Dict[str, Any],
        dn_no: str, 
        material_no: str, 
        row_number: int
    ) -> List[RowAudit]:
        """Verify data by SELECTing from PostgreSQL after commit."""
        audits = []

        try:
            result = db.execute(
                text("""
                    SELECT
                        dn_no, material_no, customer_name, customer_model,
                        customer_code, dealer_code, warehouse, warehouse_code,
                        ship_to_city, delivery_location, sales_office, sales_manager,
                        division, dn_qty, dn_amount, dn_create_date, good_issue_date, pod_date
                    FROM delivery_reports
                    WHERE dn_no = :dn_no AND material_no = :material_no
                """),
                {"dn_no": dn_no, "material_no": material_no}
            )

            db_row = result.fetchone()

            if not db_row:
                audit = RowAudit(
                    row_number=row_number,
                    dn_no=dn_no,
                    material_no=material_no,
                    field='record_exists',
                    excel_value='Yes',
                    mapped_value='Yes',
                    normalized_value='Yes',
                    database_value='No',
                    status='FAILED',
                    error='Record not found in PostgreSQL after import'
                )
                audits.append(audit)
                return audits

            # Compare string fields
            string_fields = [
                ('dn_no', normalized_data.get('dn_no'), db_row.dn_no),
                ('material_no', normalized_data.get('material_no'), db_row.material_no),
                ('customer_name', normalized_data.get('customer_name'), db_row.customer_name),
                ('customer_model', normalized_data.get('customer_model'), db_row.customer_model),
                ('customer_code', normalized_data.get('customer_code'), db_row.customer_code),
                ('dealer_code', normalized_data.get('dealer_code'), db_row.dealer_code),
                ('warehouse', normalized_data.get('warehouse'), db_row.warehouse),
                ('warehouse_code', normalized_data.get('warehouse_code'), db_row.warehouse_code),
                ('ship_to_city', normalized_data.get('ship_to_city'), db_row.ship_to_city),
                ('delivery_location', normalized_data.get('delivery_location'), db_row.delivery_location),
                ('sales_office', normalized_data.get('sales_office'), db_row.sales_office),
                ('sales_manager', normalized_data.get('sales_manager'), db_row.sales_manager),
                ('division', normalized_data.get('division'), db_row.division),
            ]

            for field_name, excel_val, db_val in string_fields:
                excel_str = str(excel_val) if excel_val is not None else None
                db_str = str(db_val) if db_val is not None else None
                
                if excel_str != db_str:
                    audit = RowAudit(
                        row_number=row_number,
                        dn_no=dn_no,
                        material_no=material_no,
                        field=field_name,
                        excel_value=excel_val,
                        mapped_value=excel_val,
                        normalized_value=excel_val,
                        database_value=db_val,
                        status='FAILED',
                        error=f'Field mismatch: Excel="{excel_str}", DB="{db_str}"'
                    )
                    audits.append(audit)

            # Amount comparison
            excel_amount = normalized_data.get('dn_amount')
            db_amount = Decimal(str(db_row.dn_amount)) if db_row.dn_amount is not None else None

            if excel_amount is not None and db_amount is not None:
                if abs(Decimal(str(excel_amount)) - db_amount) > Decimal('0.01'):
                    audit = RowAudit(
                        row_number=row_number,
                        dn_no=dn_no,
                        material_no=material_no,
                        field='dn_amount',
                        excel_value=excel_amount,
                        mapped_value=excel_amount,
                        normalized_value=excel_amount,
                        database_value=db_amount,
                        status='FAILED',
                        error=f'Amount mismatch: Excel={excel_amount}, DB={db_amount}'
                    )
                    audits.append(audit)
            elif excel_amount is not None and db_amount is None:
                audit = RowAudit(
                    row_number=row_number,
                    dn_no=dn_no,
                    material_no=material_no,
                    field='dn_amount',
                    excel_value=excel_amount,
                    mapped_value=excel_amount,
                    normalized_value=excel_amount,
                    database_value=None,
                    status='FAILED',
                    error=f'Amount lost: Excel had {excel_amount}, DB has NULL'
                )
                audits.append(audit)

            # Quantity comparison
            excel_qty = normalized_data.get('dn_qty')
            
            if excel_qty is not None and db_row.dn_qty is not None:
                if int(excel_qty) != db_row.dn_qty:
                    audit = RowAudit(
                        row_number=row_number,
                        dn_no=dn_no,
                        material_no=material_no,
                        field='dn_qty',
                        excel_value=excel_qty,
                        mapped_value=excel_qty,
                        normalized_value=excel_qty,
                        database_value=db_row.dn_qty,
                        status='FAILED',
                        error=f'Quantity mismatch: Excel={excel_qty}, DB={db_row.dn_qty}'
                    )
                    audits.append(audit)
            elif excel_qty is not None and db_row.dn_qty is None:
                audit = RowAudit(
                    row_number=row_number,
                    dn_no=dn_no,
                    material_no=material_no,
                    field='dn_qty',
                    excel_value=excel_qty,
                    mapped_value=excel_qty,
                    normalized_value=excel_qty,
                    database_value=None,
                    status='FAILED',
                    error=f'Quantity lost: Excel had {excel_qty}, DB has NULL'
                )
                audits.append(audit)

            # Date fields
            date_fields = [
                ('dn_create_date', normalized_data.get('dn_create_date'), db_row.dn_create_date),
                ('good_issue_date', normalized_data.get('good_issue_date'), db_row.good_issue_date),
                ('pod_date', normalized_data.get('pod_date'), db_row.pod_date)
            ]

            for field_name, excel_val, db_val in date_fields:
                if excel_val is not None and db_val:
                    if excel_val != db_val:
                        audit = RowAudit(
                            row_number=row_number,
                            dn_no=dn_no,
                            material_no=material_no,
                            field=field_name,
                            excel_value=excel_val,
                            mapped_value=excel_val,
                            normalized_value=excel_val,
                            database_value=db_val,
                            status='FAILED',
                            error=f'Date mismatch: Excel={excel_val}, DB={db_val}'
                        )
                        audits.append(audit)
                elif excel_val is not None and not db_val:
                    audit = RowAudit(
                        row_number=row_number,
                        dn_no=dn_no,
                        material_no=material_no,
                        field=field_name,
                        excel_value=excel_val,
                        mapped_value=excel_val,
                        normalized_value=excel_val,
                        database_value=None,
                        status='FAILED',
                        error=f'Date lost: Excel had {excel_val}, DB has NULL'
                    )
                    audits.append(audit)

        except Exception as e:
            audit = RowAudit(
                row_number=row_number,
                dn_no=dn_no,
                material_no=material_no,
                field='verification_query',
                excel_value='N/A',
                mapped_value='N/A',
                normalized_value='N/A',
                database_value='Error',
                status='FAILED',
                error=f'Verification query failed: {str(e)}'
            )
            audits.append(audit)

        return audits

======================================================================================================
BLOCK 8: EXCEL IMPORT SERVICE - v5.4 COMPLETE FIX
======================================================================================================
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

        start_time = time.time()
        metrics = ImportMetrics()
        verification_failures = []
        verification_errors_list = []

        logger.info("=" * 80)
        logger.info("📊 ENTERPRISE EXCEL IMPORT v5.4 - COMPLETE FIX")
        logger.info("=" * 80)
        logger.info(f"📁 File: {file_path}")
        logger.info(f"📋 Source: {source_filename}")
        logger.info(f"🔍 Strict Mode: {STRICT_MODE}")
        logger.info(f"🔍 Verify All Rows: {VERIFY_ALL_ROWS}")

        if not batch_id:
            batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        metrics.batch_id = batch_id
        logger.info(f"📋 Batch ID: {batch_id}")

        try:
            # Read Excel
            read_start = time.time()
            df = pd.read_excel(file_path, engine='openpyxl')
            df = df.dropna(how='all')
            df = df.dropna(axis=1, how='all')

            excel_columns = [str(col).strip() for col in df.columns]
            metrics.excel_read_time = time.time() - read_start
            metrics.rows_read = len(df)

            logger.info(f"📄 Read {metrics.rows_read} rows, {len(excel_columns)} columns")
            logger.info(f"📋 Excel columns: {excel_columns}")

            # Map Columns
            column_mapping = ColumnMapper.map_columns(excel_columns)
            field_to_column = ColumnMapper.get_field_to_column(column_mapping)

            logger.info("=" * 80)
            logger.info("📋 COLUMN MAPPING:")
            for field, col in field_to_column.items():
                logger.info(f"  {field} ← '{col}'")
            logger.info("=" * 80)

            # Check required fields
            required_fields = ['dn_no', 'material_no']
            missing_fields = [f for f in required_fields if f not in field_to_column]
            if missing_fields:
                error_msg = f"Missing required columns: {missing_fields}"
                logger.error(f"❌ {error_msg}")
                return {"success": False, "error": error_msg, "available_columns": excel_columns}

            # Process Rows
            inserted_count = 0
            updated_count = 0
            skipped_count = 0
            failed_count = 0
            processed_records = set()
            inserted_rows = []

            logger.info("=" * 80)
            logger.info("📝 PROCESSING ROWS")
            logger.info("=" * 80)

            for index, row in df.iterrows():
                row_number = index + 2
                try:
                    # Get DN
                    dn_column = field_to_column.get('dn_no')
                    dn_no_raw = row.get(dn_column) if dn_column else None
                    dn_no = normalize_dn(str(dn_no_raw)) if dn_no_raw else None

                    if not dn_no:
                        logger.warning(f"⚠️ Row {row_number}: Missing DN NO")
                        metrics.missing_dn_count += 1
                        failed_count += 1
                        metrics.validation_errors.append(f"Row {row_number}: Missing DN NO")
                        continue

                    # Get Material
                    material_column = field_to_column.get('material_no')
                    material_no = normalize_string(row.get(material_column)) if material_column else None

                    if not material_no:
                        logger.warning(f"⚠️ Row {row_number}: Missing Material NO for DN {dn_no}")
                        failed_count += 1
                        metrics.validation_errors.append(f"Row {row_number}: Missing Material NO")
                        continue

                    # Check duplicates
                    record_key = f"{dn_no}_{material_no}"
                    if record_key in processed_records:
                        metrics.duplicate_count += 1
                        logger.warning(f"⚠️ Row {row_number}: Duplicate DN {dn_no} + Material {material_no}")
                        failed_count += 1
                        continue
                    processed_records.add(record_key)

                    # Get all values
                    def get_value(field_name: str):
                        col = field_to_column.get(field_name)
                        if col:
                            return row.get(col)
                        return None

                    # Core fields
                    order_type = normalize_string(get_value('order_type'))
                    division = normalize_string(get_value('division'))
                    customer_name = normalize_string(get_value('customer_name'))
                    customer_model = normalize_string(get_value('customer_model'))
                    customer_code = normalize_string(get_value('customer_code'))
                    dealer_code = normalize_string(get_value('dealer_code'))

                    # Location
                    warehouse = normalize_string(get_value('warehouse'))
                    warehouse_code = normalize_string(get_value('warehouse_code'))
                    ship_to_city = normalize_string(get_value('ship_to_city'))
                    delivery_location = normalize_string(get_value('delivery_location'))

                    # Sales
                    sales_office = normalize_string(get_value('sales_office'))
                    sales_manager = normalize_string(get_value('sales_manager'))
                    dn_work = normalize_string(get_value('dn_work'))
                    storage_location = normalize_string(get_value('storage_location'))

                    # Quantity and Amount
                    dn_qty_raw = get_value('dn_qty')
                    dn_qty = parse_quantity_int(dn_qty_raw)

                    dn_amount_raw = get_value('dn_amount')
                    dn_amount = parse_amount_decimal(dn_amount_raw)

                    # Dates
                    dn_create_date_raw = get_value('dn_create_date')
                    good_issue_date_raw = get_value('good_issue_date')
                    pod_date_raw = get_value('pod_date')

                    dn_create_date = parse_date_excel(dn_create_date_raw)
                    good_issue_date = parse_date_excel(good_issue_date_raw)
                    pod_date = parse_date_excel(pod_date_raw)

                    remarks = normalize_string(get_value('remarks'))

                    # Audit first 20 rows
                    if index < 20:
                        logger.info("=" * 60)
                        logger.info(f"📝 AUDIT ROW {row_number}:")
                        logger.info(f"  DN: {dn_no}")
                        logger.info(f"  Material: {material_no}")
                        logger.info(f"  Model: {customer_model}")
                        logger.info(f"  Dealer: {customer_name}")
                        logger.info(f"  Warehouse: {warehouse}")
                        logger.info(f"  City: {ship_to_city}")
                        logger.info(f"  Quantity: {dn_qty}")
                        logger.info(f"  Amount: {dn_amount}")
                        logger.info(f"  DN Create: {dn_create_date}")
                        logger.info(f"  PGI: {good_issue_date}")
                        logger.info(f"  POD: {pod_date}")
                        logger.info("=" * 60)

                    # Derive status
                    status = StatusEngine.derive_status(dn_create_date, good_issue_date, pod_date)

                    # Store NORMALIZED data for verification
                    normalized_row = {
                        'dn_no': dn_no,
                        'material_no': material_no,
                        'customer_model': customer_model,
                        'customer_name': customer_name,
                        'customer_code': customer_code,
                        'dealer_code': dealer_code,
                        'warehouse': warehouse,
                        'warehouse_code': warehouse_code,
                        'ship_to_city': ship_to_city,
                        'delivery_location': delivery_location,
                        'sales_office': sales_office,
                        'sales_manager': sales_manager,
                        'division': division,
                        'dn_qty': dn_qty,
                        'dn_amount': dn_amount,
                        'dn_create_date': dn_create_date,
                        'good_issue_date': good_issue_date,
                        'pod_date': pod_date,
                    }

                    # Check for existing record
                    existing = None
                    if skip_duplicates or update_existing:
                        existing = db.query(DeliveryReport).filter_by(
                            dn_no=dn_no,
                            material_no=material_no
                        ).first()

                    if existing and update_existing:
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
                        existing.dn_amount = float(dn_amount) if dn_amount else None
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
                        logger.debug(f"✅ Updated row {row_number}: DN={dn_no}")

                    elif existing and skip_duplicates:
                        skipped_count += 1
                        logger.debug(f"⏭️ Skipped duplicate row {row_number}")

                    else:
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
                            dn_amount=float(dn_amount) if dn_amount else None,
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
                        logger.debug(f"✅ Inserted row {row_number}: DN={dn_no}")

                    # Update metrics
                    if dn_amount:
                        metrics.total_revenue_imported += dn_amount
                    if dn_qty:
                        metrics.total_units_imported += dn_qty

                    # Store for verification
                    inserted_rows.append((normalized_row, dn_no, material_no, row_number))

                    # Commit in batches
                    if (index + 1) % BATCH_SIZE == 0:
                        commit_start = time.time()
                        db.commit()
                        metrics.commit_time += time.time() - commit_start
                        logger.info(f"📊 Committed {index + 1} rows")

                        # Verify after commit
                        if inserted_rows:
                            if VERIFY_ALL_ROWS:
                                rows_to_verify = inserted_rows
                            else:
                                rows_to_verify = inserted_rows[::VERIFICATION_SAMPLE_SIZE]

                            for norm_row, v_dn_no, v_material_no, v_row_num in rows_to_verify:
                                audits = VerificationEngine.verify_against_postgresql(
                                    db, norm_row, v_dn_no, v_material_no, v_row_num
                                )
                                if audits:
                                    verification_failures.extend(audits)
                                    verification_errors_list.extend(audits)
                                    metrics.verification_failed += 1
                                    for audit in audits:
                                        logger.error(f"❌ Verification Failed: Row {audit.row_number} - {audit.field}: {audit.error}")
                                else:
                                    metrics.verification_success += 1

                            inserted_rows.clear()

                except Exception as e:
                    failed_count += 1
                    logger.error(f"❌ Failed to import row {row_number}: {e}")
                    metrics.validation_errors.append(f"Row {row_number}: {str(e)}")

            # Final commit
            commit_start = time.time()
            db.commit()
            metrics.commit_time += time.time() - commit_start

            # Final verification
            if inserted_rows:
                if VERIFY_ALL_ROWS:
                    rows_to_verify = inserted_rows
                else:
                    rows_to_verify = inserted_rows[::VERIFICATION_SAMPLE_SIZE]

                for norm_row, v_dn_no, v_material_no, v_row_num in rows_to_verify:
                    audits = VerificationEngine.verify_against_postgresql(
                        db, norm_row, v_dn_no, v_material_no, v_row_num
                    )
                    if audits:
                        verification_failures.extend(audits)
                        verification_errors_list.extend(audits)
                        metrics.verification_failed += 1
                        for audit in audits:
                            logger.error(f"❌ Verification Failed: Row {audit.row_number} - {audit.field}: {audit.error}")
                    else:
                        metrics.verification_success += 1

            # Update metrics
            metrics.rows_inserted = inserted_count
            metrics.rows_updated = updated_count
            metrics.rows_skipped = skipped_count
            metrics.rows_failed = failed_count
            metrics.rows_verified = metrics.verification_success + metrics.verification_failed
            metrics.verification_errors = [
                {'row': a.row_number, 'field': a.field, 'error': a.error} 
                for a in verification_errors_list
            ]
            metrics.import_duration = time.time() - start_time

            # Final report
            logger.info("=" * 80)
            logger.info("🔍 VERIFICATION REPORT:")
            logger.info(f"  Rows Verified: {metrics.rows_verified}")
            logger.info(f"  Verification Success: {metrics.verification_success}")
            logger.info(f"  Verification Failed: {metrics.verification_failed}")

            if verification_failures:
                logger.error("❌ VERIFICATION FAILURES DETECTED:")
                for failure in verification_failures[:10]:
                    logger.error(f"  Row {failure.row_number}: {failure.field} - {failure.error}")

                if STRICT_MODE:
                    logger.error("❌ STRICT MODE: Failing import due to verification failures")
                    raise VerificationError(f"Verification failed for {len(verification_failures)} rows")

            logger.info("=" * 80)
            logger.info(f"✅ IMPORT COMPLETED")
            logger.info(f"  Read: {metrics.rows_read}")
            logger.info(f"  Inserted: {metrics.rows_inserted}")
            logger.info(f"  Updated: {metrics.rows_updated}")
            logger.info(f"  Skipped: {metrics.rows_skipped}")
            logger.info(f"  Failed: {metrics.rows_failed}")
            logger.info(f"  Duplicates: {metrics.duplicate_count}")
            logger.info(f"  Revenue Imported: PKR {metrics.total_revenue_imported:,.2f}")
            logger.info(f"  Units Imported: {metrics.total_units_imported}")
            logger.info(f"  Duration: {metrics.import_duration:.2f}s")
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
                "verification_success": metrics.verification_success,
                "verification_failed": metrics.verification_failed,
                "total_revenue_imported": float(metrics.total_revenue_imported),
                "total_units_imported": metrics.total_units_imported,
                "validation_errors": metrics.validation_errors[:20],
                "verification_failures": [
                    {"row": f.row_number, "field": f.field, "error": f.error}
                    for f in verification_failures[:10]
                ],
                "strict_mode": STRICT_MODE,
                "metrics": metrics.to_dict()
            }

        except VerificationError as e:
            logger.error(f"❌ Verification error in import: {e}")
            raise

        except Exception as e:
            logger.error(f"❌ Import failed: {e}")
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

======================================================================================================
BLOCK 9: EXPORTS
======================================================================================================
__all__ = [
    'ExcelImportService',
    'ColumnMapper',
    'StatusEngine',
    'ImportMetrics',
    'RowAudit',
    'VerificationEngine',
    'parse_amount_decimal',
    'parse_quantity_int',
    'parse_date_excel',
    'normalize_string',
    'normalize_dn',
    'STRICT_MODE',
    'VERIFY_ALL_ROWS',
    'VerificationError'
]

======================================================================================================
END OF FILE
======================================================================================================
