# =====================================================================================================
# FILE: app/services/excel_import_service.py
# VERSION: v7.0 - SIMPLE PRODUCTION IMPORTER
# PURPOSE: Clean, simple Excel import with smart header detection
# COMPATIBLE WITH: upload.py v4.2
# =====================================================================================================

import pandas as pd
import logging
import uuid
import re
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
import time
import traceback

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# =====================================================================================================
# CONFIGURATION
# =====================================================================================================

BATCH_SIZE = 1000
HEADER_SCAN_ROWS = 20
STRICT_MODE = True

# =====================================================================================================
# EXCEPTIONS
# =====================================================================================================

class ImportError(Exception):
    """Base import error"""
    pass

class HeaderNotFoundError(ImportError):
    """Header row could not be detected"""
    pass

class ValidationError(ImportError):
    """Required columns missing"""
    pass

# =====================================================================================================
# ADDED: VerificationError for upload.py compatibility
# =====================================================================================================

class VerificationError(Exception):
    """Raised when verification fails - kept for upload.py compatibility"""
    pass

# =====================================================================================================
# HEADER NORMALIZATION
# =====================================================================================================

def normalize_header(header: Any) -> str:
    """
    Normalize Excel header for consistent matching.
    
    Examples:
        "DN NO" -> "dn no"
        "DN_NO" -> "dn no"  
        "DN-NO" -> "dn no"
        " Material NO " -> "material no"
        "Sold-to-party Name" -> "sold to party name"
    """
    if header is None:
        return ""
    
    normalized = str(header).strip()
    
    # Replace separators with spaces
    for sep in ['_', '-', '.', '/', '\\', '#']:
        normalized = normalized.replace(sep, ' ')
    
    # Remove extra spaces and lowercase
    normalized = ' '.join(normalized.split()).lower()
    
    return normalized

# =====================================================================================================
# HEADER DETECTION
# =====================================================================================================

def detect_header_row(df: pd.DataFrame, max_rows: int = HEADER_SCAN_ROWS) -> int:
    """
    Detect which row contains column headers.
    Returns row index (0-based).
    """
    if len(df) == 0:
        raise HeaderNotFoundError("Excel file is empty")
    
    # Keywords that indicate a header row
    header_keywords = {
        'dn no': 3,
        'material no': 3,
        'dn': 2,
        'material': 2,
        'order type': 2,
        'customer model': 2,
        'warehouse': 2,
        'city': 2,
        'amount': 2,
        'qty': 2
    }
    
    best_score = 0
    best_row = 0
    
    rows_to_check = min(max_rows, len(df))
    
    for row_idx in range(rows_to_check):
        score = 0
        row_data = df.iloc[row_idx]
        matched_keywords = set()
        
        for value in row_data:
            if value is None or not isinstance(value, str):
                continue
            
            normalized = normalize_header(value)
            if not normalized:
                continue
            
            for keyword, weight in header_keywords.items():
                if keyword in normalized and keyword not in matched_keywords:
                    matched_keywords.add(keyword)
                    score += weight
        
        if score > best_score:
            best_score = score
            best_row = row_idx
    
    # If score is too low, check for clear header indicators
    if best_score < 3:
        for row_idx in range(rows_to_check):
            row_data = df.iloc[row_idx]
            for value in row_data:
                if value and isinstance(value, str):
                    normalized = normalize_header(value)
                    if normalized in ['dn no', 'material no', 'dn', 'material']:
                        return row_idx
    
    logger.info(f"Detected header at row {best_row} (score: {best_score})")
    return best_row

# =====================================================================================================
# COLUMN MAPPER
# =====================================================================================================

class ColumnMapper:
    """Map normalized Excel headers to database fields"""
    
    HEADER_MAP = {
        # DN
        'dn no': 'dn_no',
        'dn': 'dn_no',
        'delivery note': 'dn_no',
        'delivery number': 'dn_no',
        
        # Material
        'material no': 'material_no',
        'material': 'material_no',
        'material number': 'material_no',
        'sku': 'material_no',
        
        # Order Type
        'order type': 'order_type',
        'order': 'order_type',
        
        # DN Work
        'dn work': 'dn_work',
        'work': 'dn_work',
        
        # Division
        'division': 'division',
        'div': 'division',
        
        # Customer Model
        'customer model': 'customer_model',
        'model': 'customer_model',
        
        # Customer Name
        'sold to party name': 'customer_name',
        'sold-to-party name': 'customer_name',
        'customer name': 'customer_name',
        'customer': 'customer_name',
        
        # Sales Office
        'sales office': 'sales_office',
        'office': 'sales_office',
        'sales': 'sales_office',
        
        # Sales Manager
        'sales manager': 'sales_manager',
        'manager': 'sales_manager',
        
        # Ship-to City
        'ship to city': 'ship_to_city',
        'ship-to city': 'ship_to_city',
        'city': 'ship_to_city',
        
        # Storage
        'storage': 'storage_location',
        'storage location': 'storage_location',
        
        # Warehouse
        'warehouse': 'warehouse',
        'wh': 'warehouse',
        
        # Warehouse Code
        'warehouse code': 'warehouse_code',
        
        # Delivery Location
        'delivery location': 'delivery_location',
        'location': 'delivery_location',
        
        # DN Quantity
        'dn qty': 'dn_qty',
        'qty': 'dn_qty',
        'quantity': 'dn_qty',
        
        # DN Amount
        'dn amount': 'dn_amount',
        'amount': 'dn_amount',
        'value': 'dn_amount',
        
        # DN Create Date
        'dn create date': 'dn_create_date',
        'create date': 'dn_create_date',
        'created date': 'dn_create_date',
        
        # Good Issue Date
        'good issue date': 'good_issue_date',
        'pgi date': 'good_issue_date',
        'dispatch date': 'good_issue_date',
        
        # POD Date
        'pod date': 'pod_date',
        'pod': 'pod_date',
        'proof of delivery': 'pod_date',
        
        # Codes
        'customer code': 'customer_code',
        'dealer code': 'dealer_code',
        
        # Remarks
        'remarks': 'remarks',
        'remark': 'remarks',
        'notes': 'remarks',
        'comments': 'remarks'
    }
    
    REQUIRED_FIELDS = ['dn_no', 'material_no']
    
    @classmethod
    def map_headers(cls, headers: List[str]) -> tuple:
        """Map Excel headers to database fields"""
        field_to_column = {}
        column_to_field = {}
        unmapped = []
        
        for header in headers:
            if header is None:
                continue
            
            normalized = normalize_header(header)
            field = cls.HEADER_MAP.get(normalized)
            
            if field:
                field_to_column[field] = header
                column_to_field[header] = field
                logger.info(f"  ✅ '{header}' -> {field}")
            else:
                unmapped.append(header)
                logger.warning(f"  ⚠️ '{header}' -> UNMAPPED")
        
        missing = [f for f in cls.REQUIRED_FIELDS if f not in field_to_column]
        
        if unmapped:
            logger.warning(f"  Unmapped columns: {unmapped[:10]}")
        
        return field_to_column, column_to_field, unmapped, missing

# =====================================================================================================
# STATUS ENGINE
# =====================================================================================================

class StatusEngine:
    """Derive delivery status from dates"""
    
    @staticmethod
    def derive(dn_create_date: Optional[date], good_issue_date: Optional[date], pod_date: Optional[date]) -> Dict[str, Any]:
        has_dn = dn_create_date is not None
        has_pgi = good_issue_date is not None
        has_pod = pod_date is not None
        
        if has_pod and has_pgi and has_dn:
            return {
                'delivery_status': 'Delivered',
                'pgi_status': 'Completed',
                'pod_status': 'Completed',
                'pending_flag': False
            }
        elif has_pgi and has_dn:
            return {
                'delivery_status': 'Dispatched',
                'pgi_status': 'Completed',
                'pod_status': 'Pending',
                'pending_flag': True
            }
        elif has_dn:
            return {
                'delivery_status': 'Pending Dispatch',
                'pgi_status': 'Pending',
                'pod_status': 'Pending',
                'pending_flag': True
            }
        else:
            return {
                'delivery_status': 'Unknown',
                'pgi_status': 'Unknown',
                'pod_status': 'Unknown',
                'pending_flag': True
            }

# =====================================================================================================
# DATA PARSING FUNCTIONS
# =====================================================================================================

def normalize_string(value: Any) -> Optional[str]:
    """Clean and normalize string"""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = ' '.join(value.strip().split())
        return cleaned if cleaned else None
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip() if str(value) else None

def parse_amount(value: Any) -> Optional[Decimal]:
    """Parse amount from various formats"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except:
            return None
    if isinstance(value, str):
        cleaned = re.sub(r'[^\d.]', '', value.strip())
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except:
            return None
    return None

def parse_quantity(value: Any) -> Optional[int]:
    """Parse quantity as integer"""
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
        except:
            return None
    return None

def parse_date(value: Any) -> Optional[date]:
    """Parse date from various formats"""
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
        
        formats = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]
        
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        
        try:
            serial = float(value)
            if serial > 59:
                return pd.Timestamp('1899-12-30') + pd.Timedelta(days=serial)
        except:
            pass
        
        return None
    return None

def normalize_dn(dn_no: str) -> str:
    """Extract digits only from DN number"""
    if not dn_no:
        return ""
    return re.sub(r'[^0-9]', '', dn_no.strip())

# =====================================================================================================
# EXCEL IMPORT SERVICE
# =====================================================================================================

class ExcelImportService:
    """Simple, fast Excel import service"""
    
    @staticmethod
    def import_delivery_report_excel(
        db: Session,
        file_path: str,
        source_filename: str,
        batch_id: str = None,
        skip_dups: bool = False,
        update_existing_rows: bool = False
    ) -> Dict[str, Any]:
        
        start_time = time.time()
        validation_errors = []
        
        logger.info("=" * 60)
        logger.info("📊 EXCEL IMPORT v7.0 - SIMPLE PRODUCTION")
        logger.info("=" * 60)
        logger.info(f"📁 File: {file_path}")
        logger.info(f"📋 Source: {source_filename}")
        
        # Generate batch ID
        if not batch_id:
            batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        logger.info(f"📋 Batch: {batch_id}")
        
        try:
            # ============================================================
            # STEP 1: Detect Header Row
            # ============================================================
            logger.info("🔍 Detecting header row...")
            
            df_raw = pd.read_excel(file_path, engine='openpyxl', header=None, nrows=HEADER_SCAN_ROWS + 5)
            
            if len(df_raw) == 0:
                raise ImportError("Excel file is empty")
            
            header_row = detect_header_row(df_raw)
            logger.info(f"✅ Using header row: {header_row}")
            
            # ============================================================
            # STEP 2: Read Excel with detected header
            # ============================================================
            df = pd.read_excel(file_path, engine='openpyxl', header=header_row)
            
            df = df.dropna(how='all')
            df = df.dropna(axis=1, how='all')
            
            total_rows = len(df)
            logger.info(f"📄 Read {total_rows} rows, {len(df.columns)} columns")
            
            # ============================================================
            # STEP 3: Map Columns
            # ============================================================
            headers = [str(col).strip() for col in df.columns]
            field_to_column, column_to_field, unmapped, missing = ColumnMapper.map_headers(headers)
            
            if missing:
                logger.error(f"❌ Missing required fields: {missing}")
                return {
                    "success": False,
                    "error": f"Missing required columns: {missing}",
                    "batch_id": batch_id,
                    "total_rows": 0,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                    "total_revenue_imported": 0,
                    "total_units_imported": 0,
                    "validation_errors": [f"Missing required fields: {missing}"]
                }
            
            # ============================================================
            # STEP 4: Process Rows
            # ============================================================
            inserted_count = 0
            updated_count = 0
            skipped_count = 0
            failed_count = 0
            total_revenue = Decimal(0)
            total_units = 0
            
            processed_keys = set()
            
            logger.info("📝 Processing rows...")
            
            for index, row in df.iterrows():
                row_number = index + 2 + header_row
                
                try:
                    # Get DN
                    dn_col = field_to_column.get('dn_no')
                    dn_raw = row.get(dn_col) if dn_col else None
                    dn_no = normalize_dn(str(dn_raw)) if dn_raw else None
                    
                    if not dn_no:
                        validation_errors.append(f"Row {row_number}: Missing DN NO")
                        failed_count += 1
                        continue
                    
                    # Get Material
                    mat_col = field_to_column.get('material_no')
                    mat_raw = row.get(mat_col) if mat_col else None
                    material_no = normalize_string(mat_raw)
                    
                    if not material_no:
                        validation_errors.append(f"Row {row_number}: Missing Material NO")
                        failed_count += 1
                        continue
                    
                    # Check duplicate within file
                    key = f"{dn_no}_{material_no}"
                    if key in processed_keys:
                        validation_errors.append(f"Row {row_number}: Duplicate")
                        failed_count += 1
                        continue
                    processed_keys.add(key)
                    
                    # Helper to get value
                    def get_value(field: str):
                        col = field_to_column.get(field)
                        return row.get(col) if col else None
                    
                    # Extract all fields
                    order_type = normalize_string(get_value('order_type'))
                    division = normalize_string(get_value('division'))
                    customer_name = normalize_string(get_value('customer_name'))
                    customer_model = normalize_string(get_value('customer_model'))
                    customer_code = normalize_string(get_value('customer_code'))
                    dealer_code = normalize_string(get_value('dealer_code'))
                    warehouse = normalize_string(get_value('warehouse'))
                    warehouse_code = normalize_string(get_value('warehouse_code'))
                    ship_to_city = normalize_string(get_value('ship_to_city'))
                    delivery_location = normalize_string(get_value('delivery_location'))
                    sales_office = normalize_string(get_value('sales_office'))
                    sales_manager = normalize_string(get_value('sales_manager'))
                    dn_work = normalize_string(get_value('dn_work'))
                    storage_location = normalize_string(get_value('storage_location'))
                    remarks = normalize_string(get_value('remarks'))
                    
                    dn_qty = parse_quantity(get_value('dn_qty'))
                    dn_amount = parse_amount(get_value('dn_amount'))
                    
                    dn_create_date = parse_date(get_value('dn_create_date'))
                    good_issue_date = parse_date(get_value('good_issue_date'))
                    pod_date = parse_date(get_value('pod_date'))
                    
                    # Derive status
                    status = StatusEngine.derive(dn_create_date, good_issue_date, pod_date)
                    
                    # Check existing record
                    existing = None
                    if skip_dups or update_existing_rows:
                        existing = db.query(DeliveryReport).filter_by(
                            dn_no=dn_no,
                            material_no=material_no
                        ).first()
                    
                    if existing and update_existing_rows:
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
                        
                    elif existing and skip_dups:
                        skipped_count += 1
                        
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
                    
                    # Update totals
                    if dn_amount:
                        total_revenue += dn_amount
                    if dn_qty:
                        total_units += dn_qty
                    
                    # Commit in batches
                    if (index + 1) % BATCH_SIZE == 0:
                        db.commit()
                        logger.info(f"📊 Committed {index + 1} rows")
                    
                except Exception as e:
                    failed_count += 1
                    validation_errors.append(f"Row {row_number}: {str(e)}")
                    logger.warning(f"⚠️ Row {row_number} failed: {e}")
            
            # ============================================================
            # STEP 5: Final Commit
            # ============================================================
            logger.info("💾 Committing to database...")
            db.commit()
            
            # ============================================================
            # STEP 6: Results
            # ============================================================
            duration = time.time() - start_time
            
            logger.info("=" * 60)
            logger.info("✅ IMPORT COMPLETED")
            logger.info(f"  Duration: {duration:.2f}s")
            logger.info(f"  Header Row: {header_row}")
            logger.info(f"  Rows Read: {total_rows}")
            logger.info(f"  Inserted: {inserted_count}")
            logger.info(f"  Updated: {updated_count}")
            logger.info(f"  Skipped: {skipped_count}")
            logger.info(f"  Failed: {failed_count}")
            logger.info(f"  Revenue: PKR {total_revenue:,.2f}")
            logger.info(f"  Units: {total_units}")
            logger.info("=" * 60)
            
            return {
                "success": True,
                "batch_id": batch_id,
                "total_rows": total_rows,
                "inserted_count": inserted_count,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
                "total_revenue_imported": float(total_revenue),
                "total_units_imported": total_units,
                "validation_errors": validation_errors[:20]
            }
            
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
                "total_revenue_imported": 0,
                "total_units_imported": 0,
                "validation_errors": [str(e)]
            }

# =====================================================================================================
# EXPORTS
# =====================================================================================================

__all__ = [
    'ExcelImportService',
    'VerificationError',
    'normalize_header',
    'parse_amount',
    'parse_quantity',
    'parse_date',
    'normalize_string',
    'normalize_dn'
]

# =====================================================================================================
# END OF FILE
# =====================================================================================================
