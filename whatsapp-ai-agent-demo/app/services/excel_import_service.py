# =====================================================================================================
# FILE: app/services/excel_import_service.py
# VERSION: v8.0 - 1 MILLION ROW OPTIMIZED
# PURPOSE: High-performance Excel import for 1M+ rows with memory optimization
# =====================================================================================================

import pandas as pd
import logging
import uuid
import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional, List, Generator
from sqlalchemy.orm import Session
from sqlalchemy import text
import time
import traceback
import gc
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# =====================================================================================================
# CONFIGURATION
# =====================================================================================================

BATCH_SIZE = 5000  # Increased for 1M rows
COMMIT_BATCH_SIZE = 10000  # Commit every 10K rows
HEADER_SCAN_ROWS = 20
STRICT_MODE = True
CHUNK_SIZE = 50000  # Process 50K rows at a time
MAX_WORKERS = 4  # Parallel processing threads
MEMORY_THRESHOLD = 0.8  # 80% memory usage threshold

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

class MemoryError(ImportError):
    """Memory limit exceeded"""
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
        "DN.NO" -> "dn no"
        "DN#" -> "dn no"
        " Material NO " -> "material no"
        "Sold-to-party Name" -> "sold to party name"
        "Ship-to City" -> "ship to city"
        "DN Create date" -> "dn create date"
        "Good issue date" -> "good issue date"
    """
    if header is None:
        return ""
    
    # Convert to string and clean
    normalized = str(header).strip()
    
    # Replace separators with spaces
    for sep in ['_', '-', '.', '/', '\\', '#', '·', '•']:
        normalized = normalized.replace(sep, ' ')
    
    # Replace non-breaking spaces and other whitespace
    normalized = normalized.replace('\u00a0', ' ')
    normalized = normalized.replace('\t', ' ')
    normalized = normalized.replace('\r', ' ')
    normalized = normalized.replace('\n', ' ')
    
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
    
    # Keywords that indicate a header row (weighted)
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
        'qty': 2,
        'model': 1,
        'storage': 1,
        'date': 1,
        'sales': 1,
        'manager': 1,
        'work': 1,
        'division': 1,
        'remarks': 1,
        'remark': 1
    }
    
    best_score = 0
    best_row = 0
    
    rows_to_check = min(max_rows, len(df))
    
    for row_idx in range(rows_to_check):
        score = 0
        row_data = df.iloc[row_idx]
        matched_keywords = set()
        
        for value in row_data:
            if value is None:
                continue
            if not isinstance(value, str):
                continue
            
            normalized = normalize_header(value)
            if not normalized:
                continue
            
            # Check for keyword matches
            for keyword, weight in header_keywords.items():
                if keyword in normalized:
                    if keyword not in matched_keywords:
                        matched_keywords.add(keyword)
                        score += weight
        
        if score > best_score:
            best_score = score
            best_row = row_idx
    
    # If score is too low, check if any row has clear header indicators
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
# MEMORY MONITOR
# =====================================================================================================

class MemoryMonitor:
    """Monitor memory usage during import"""
    
    def __init__(self, threshold: float = MEMORY_THRESHOLD):
        self.threshold = threshold
        self.peak_memory = 0
        
    def check(self) -> bool:
        """Check if memory usage is above threshold"""
        try:
            import psutil
            memory_percent = psutil.virtual_memory().percent / 100.0
            self.peak_memory = max(self.peak_memory, memory_percent)
            
            if memory_percent > self.threshold:
                logger.warning(f"⚠️ Memory usage: {memory_percent*100:.1f}% (threshold: {self.threshold*100:.1f}%)")
                return True
            return False
        except ImportError:
            return False
    
    def get_peak(self) -> float:
        return self.peak_memory

# =====================================================================================================
# COLUMN MAPPER
# =====================================================================================================

class ColumnMapper:
    """Map normalized Excel headers to database fields"""
    
    # Normalized header -> database field
    HEADER_MAP = {
        # DN - Primary Key
        'dn no': 'dn_no',
        'dn': 'dn_no',
        'delivery note': 'dn_no',
        'delivery note no': 'dn_no',
        'delivery note number': 'dn_no',
        'delivery number': 'dn_no',
        'd n no': 'dn_no',
        'd n': 'dn_no',
        
        # Material
        'material no': 'material_no',
        'material': 'material_no',
        'material number': 'material_no',
        'material#': 'material_no',
        'sku': 'material_no',
        'product no': 'material_no',
        'product number': 'material_no',
        'item no': 'material_no',
        
        # Order Type
        'order type': 'order_type',
        'order': 'order_type',
        'ordertype': 'order_type',
        'type': 'order_type',
        
        # DN Work
        'dn work': 'dn_work',
        'work': 'dn_work',
        'work order': 'dn_work',
        'work no': 'dn_work',
        
        # Division
        'division': 'division',
        'div': 'division',
        
        # Customer Model
        'customer model': 'customer_model',
        'model': 'customer_model',
        'model name': 'customer_model',
        'product model': 'customer_model',
        'model no': 'customer_model',
        
        # Customer Name (Sold-to-party)
        'sold to party name': 'customer_name',
        'sold-to-party name': 'customer_name',
        'sold to party': 'customer_name',
        'sold-to-party': 'customer_name',
        'customer name': 'customer_name',
        'customer': 'customer_name',
        'dealer name': 'customer_name',
        'party name': 'customer_name',
        
        # Sales Office
        'sales office': 'sales_office',
        'salesoffice': 'sales_office',
        'office': 'sales_office',
        'sales': 'sales_office',
        'sales region': 'sales_office',
        
        # Sales Manager
        'sales manager': 'sales_manager',
        'salesmanager': 'sales_manager',
        'manager': 'sales_manager',
        'sales rep': 'sales_manager',
        
        # Ship-to City
        'ship to city': 'ship_to_city',
        'ship-to city': 'ship_to_city',
        'ship-to-city': 'ship_to_city',
        'shipcity': 'ship_to_city',
        'city': 'ship_to_city',
        'destination city': 'ship_to_city',
        'ship to': 'ship_to_city',
        
        # Storage Location
        'storage': 'storage_location',
        'storage location': 'storage_location',
        'storagelocation': 'storage_location',
        'bin': 'storage_location',
        
        # Warehouse
        'warehouse': 'warehouse',
        'wh': 'warehouse',
        'ware house': 'warehouse',
        'plant': 'warehouse',
        
        # Warehouse Code
        'warehouse code': 'warehouse_code',
        'warehousecode': 'warehouse_code',
        'wh code': 'warehouse_code',
        'plant code': 'warehouse_code',
        
        # Delivery Location
        'delivery location': 'delivery_location',
        'deliverylocation': 'delivery_location',
        'location': 'delivery_location',
        'delivery address': 'delivery_location',
        
        # DN Quantity
        'dn qty': 'dn_qty',
        'dn quantity': 'dn_qty',
        'qty': 'dn_qty',
        'quantity': 'dn_qty',
        'dnqty': 'dn_qty',
        'units': 'dn_qty',
        
        # DN Amount
        'dn amount': 'dn_amount',
        'dn amount ': 'dn_amount',
        'amount': 'dn_amount',
        'value': 'dn_amount',
        'dnamount': 'dn_amount',
        'net amount': 'dn_amount',
        'total': 'dn_amount',
        
        # DN Create Date
        'dn create date': 'dn_create_date',
        'dn created date': 'dn_create_date',
        'create date': 'dn_create_date',
        'created date': 'dn_create_date',
        'dn created': 'dn_create_date',
        'date created': 'dn_create_date',
        'creation date': 'dn_create_date',
        'order date': 'dn_create_date',
        
        # Good Issue Date
        'good issue date': 'good_issue_date',
        'good issue': 'good_issue_date',
        'pgi date': 'good_issue_date',
        'pgi': 'good_issue_date',
        'goods issue': 'good_issue_date',
        'dispatch date': 'good_issue_date',
        'shipped date': 'good_issue_date',
        
        # POD Date
        'pod date': 'pod_date',
        'pod': 'pod_date',
        'proof of delivery': 'pod_date',
        'delivery date': 'pod_date',
        'received date': 'pod_date',
        'confirmation date': 'pod_date',
        
        # Customer/Dealer Codes
        'customer code': 'customer_code',
        'customer code no': 'customer_code',
        'cust code': 'customer_code',
        'account code': 'customer_code',
        'dealer code': 'dealer_code',
        'dealer code no': 'dealer_code',
        'dealer no': 'dealer_code',
        'distributor code': 'dealer_code',
        
        # Remarks
        'remarks': 'remarks',
        'remark': 'remarks',
        'note': 'remarks',
        'notes': 'remarks',
        'comments': 'remarks',
        'comment': 'remarks',
    }
    
    REQUIRED_FIELDS = ['dn_no', 'material_no']
    
    @classmethod
    def map_headers(cls, headers: List[str]) -> tuple:
        """
        Map Excel headers to database fields.
        
        Returns:
            (field_to_column, column_to_field, unmapped, missing)
        """
        field_to_column = {}
        column_to_field = {}
        unmapped = []
        
        logger.info("=" * 60)
        logger.info("📋 COLUMN MAPPING")
        logger.info("=" * 60)
        
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
        
        # Check required fields
        missing = [f for f in cls.REQUIRED_FIELDS if f not in field_to_column]
        
        if unmapped:
            logger.warning(f"  Unmapped columns: {unmapped[:10]}")
        if missing:
            logger.error(f"  Missing required fields: {missing}")
        
        logger.info("=" * 60)
        
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
        # Remove currency symbols, commas, spaces
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
        
        # Try common formats
        formats = [
            "%d.%m.%Y",
            "%Y-%m-%d", 
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%d-%m-%Y",
            "%m-%d-%Y"
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        
        # Try Excel serial as string
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
# BATCH PROCESSOR - Optimized for 1M rows
# =====================================================================================================

class BatchProcessor:
    """Process rows in batches for memory efficiency"""
    
    def __init__(self, db: Session, field_to_column: Dict, batch_id: str, source_filename: str):
        self.db = db
        self.field_to_column = field_to_column
        self.batch_id = batch_id
        self.source_filename = source_filename
        
        # Statistics
        self.inserted_count = 0
        self.updated_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.total_revenue = Decimal(0)
        self.total_units = 0
        self.validation_errors = []
        self.processed_keys = set()
        
        # Batch buffer
        self.batch_buffer = []
        self.batch_size = 0
        self.commit_counter = 0
        
    def process_row(self, row: pd.Series, row_number: int) -> bool:
        """Process a single row and add to batch"""
        try:
            # Get DN
            dn_col = self.field_to_column.get('dn_no')
            dn_raw = row.get(dn_col) if dn_col else None
            dn_no = normalize_dn(str(dn_raw)) if dn_raw else None
            
            if not dn_no:
                self.validation_errors.append(f"Row {row_number}: Missing DN NO")
                self.failed_count += 1
                return False
            
            # Get Material
            mat_col = self.field_to_column.get('material_no')
            mat_raw = row.get(mat_col) if mat_col else None
            material_no = normalize_string(mat_raw)
            
            if not material_no:
                self.validation_errors.append(f"Row {row_number}: Missing Material NO")
                self.failed_count += 1
                return False
            
            # Check duplicate within file
            key = f"{dn_no}_{material_no}"
            if key in self.processed_keys:
                self.validation_errors.append(f"Row {row_number}: Duplicate DN {dn_no} + Material {material_no}")
                self.failed_count += 1
                return False
            self.processed_keys.add(key)
            
            # Helper to get field value
            def get_value(field: str):
                col = self.field_to_column.get(field)
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
            if skip_duplicates or update_existing:
                existing = self.db.query(DeliveryReport).filter_by(
                    dn_no=dn_no,
                    material_no=material_no
                ).first()
            
            if existing and update_existing:
                # Update
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
                existing.source_file = self.source_filename
                existing.upload_batch_id = self.batch_id
                existing.updated_at = datetime.utcnow()
                self.updated_count += 1
                
            elif existing and skip_duplicates:
                self.skipped_count += 1
                
            else:
                # Insert - add to buffer
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
                    source_file=self.source_filename,
                    upload_batch_id=self.batch_id,
                    imported_at=datetime.utcnow()
                )
                self.batch_buffer.append(record)
                self.inserted_count += 1
            
            # Update totals
            if dn_amount:
                self.total_revenue += dn_amount
            if dn_qty:
                self.total_units += dn_qty
            
            # Commit if buffer is full
            if len(self.batch_buffer) >= COMMIT_BATCH_SIZE:
                self.flush_batch()
            
            return True
            
        except Exception as e:
            self.validation_errors.append(f"Row {row_number}: {str(e)}")
            self.failed_count += 1
            logger.warning(f"⚠️ Row {row_number} failed: {e}")
            return False
    
    def flush_batch(self):
        """Flush batch buffer to database"""
        if not self.batch_buffer:
            return
        
        try:
            self.db.add_all(self.batch_buffer)
            self.db.commit()
            self.commit_counter += 1
            logger.info(f"📊 Committed batch {self.commit_counter} ({len(self.batch_buffer)} rows)")
            self.batch_buffer.clear()
            
            # Force garbage collection periodically
            if self.commit_counter % 10 == 0:
                gc.collect()
                
        except Exception as e:
            logger.error(f"❌ Batch commit failed: {e}")
            self.db.rollback()
            raise
    
    def finalize(self):
        """Final flush and return statistics"""
        self.flush_batch()
        return {
            'inserted_count': self.inserted_count,
            'updated_count': self.updated_count,
            'skipped_count': self.skipped_count,
            'failed_count': self.failed_count,
            'total_revenue': self.total_revenue,
            'total_units': self.total_units,
            'validation_errors': self.validation_errors
        }

# =====================================================================================================
# EXCEL IMPORT SERVICE
# =====================================================================================================

# Global flags for skip/update
skip_duplicates = False
update_existing = False

class ExcelImportService:
    """High-performance Excel import service for 1M+ rows"""
    
    @staticmethod
    def import_delivery_report_excel(
        db: Session,
        file_path: str,
        source_filename: str,
        batch_id: str = None,
        skip_dups: bool = False,
        update_existing_rows: bool = False
    ) -> Dict[str, Any]:
        
        global skip_duplicates, update_existing
        skip_duplicates = skip_dups
        update_existing = update_existing_rows
        
        start_time = time.time()
        memory_monitor = MemoryMonitor()
        
        logger.info("=" * 60)
        logger.info("📊 EXCEL IMPORT v8.0 - 1 MILLION ROW OPTIMIZED")
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
            
            # Use memory-efficient reading for large files
            df_raw = pd.read_excel(file_path, engine='openpyxl', header=None, nrows=HEADER_SCAN_ROWS + 5)
            
            if len(df_raw) == 0:
                raise ImportError("Excel file is empty")
            
            header_row = detect_header_row(df_raw)
            logger.info(f"✅ Using header row: {header_row}")
            
            # ============================================================
            # STEP 2: Read Excel with detected header in chunks
            # ============================================================
            logger.info("📖 Reading Excel file...")
            
            # Get total rows without loading full file
            try:
                preview = pd.read_excel(file_path, engine='openpyxl', header=header_row, nrows=1)
                total_columns = len(preview.columns)
                logger.info(f"📋 Found {total_columns} columns")
                
                # Estimate total rows using Excel metadata
                from openpyxl import load_workbook
                wb = load_workbook(file_path, read_only=True)
                ws = wb.active
                total_rows_estimate = ws.max_row - header_row - 1
                wb.close()
                logger.info(f"📊 Estimated rows: {total_rows_estimate:,}")
            except Exception as e:
                logger.warning(f"Could not estimate rows: {e}")
                total_rows_estimate = 0
            
            # ============================================================
            # STEP 3: Map Columns
            # ============================================================
            header_sample = pd.read_excel(file_path, engine='openpyxl', header=header_row, nrows=1)
            headers = [str(col).strip() for col in header_sample.columns]
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
            # STEP 4: Process in Chunks
            # ============================================================
            logger.info("=" * 60)
            logger.info("📝 PROCESSING ROWS")
            logger.info("=" * 60)
            
            # Create batch processor
            processor = BatchProcessor(db, field_to_column, batch_id, source_filename)
            
            # Process in chunks for memory efficiency
            chunk_count = 0
            total_rows_processed = 0
            
            # Use chunked reading
            for chunk in pd.read_excel(
                file_path,
                engine='openpyxl',
                header=header_row,
                chunksize=CHUNK_SIZE
            ):
                chunk_count += 1
                chunk_rows = len(chunk)
                total_rows_processed += chunk_rows
                
                logger.info(f"📦 Processing chunk {chunk_count} ({chunk_rows:,} rows)")
                
                # Process each row in chunk
                for idx, row in chunk.iterrows():
                    row_number = idx + 2 + header_row
                    processor.process_row(row, row_number)
                
                # Check memory
                if memory_monitor.check():
                    logger.warning(f"⚠️ High memory usage detected, forcing garbage collection")
                    gc.collect()
                
                logger.info(f"✅ Chunk {chunk_count} complete ({total_rows_processed:,} rows processed so far)")
            
            # ============================================================
            # STEP 5: Finalize
            # ============================================================
            logger.info("💾 Finalizing import...")
            results = processor.finalize()
            
            # ============================================================
            # STEP 6: Results
            # ============================================================
            duration = time.time() - start_time
            rows_per_second = total_rows_processed / duration if duration > 0 else 0
            
            logger.info("=" * 60)
            logger.info("✅ IMPORT COMPLETED")
            logger.info(f"  Duration: {duration:.2f}s")
            logger.info(f"  Rows/Second: {rows_per_second:,.0f}")
            logger.info(f"  Header Row: {header_row}")
            logger.info(f"  Rows Read: {total_rows_processed:,}")
            logger.info(f"  Inserted: {results['inserted_count']:,}")
            logger.info(f"  Updated: {results['updated_count']:,}")
            logger.info(f"  Skipped: {results['skipped_count']:,}")
            logger.info(f"  Failed: {results['failed_count']:,}")
            logger.info(f"  Revenue: PKR {results['total_revenue']:,.2f}")
            logger.info(f"  Units: {results['total_units']:,}")
            logger.info(f"  Peak Memory: {memory_monitor.get_peak()*100:.1f}%")
            logger.info("=" * 60)
            
            return {
                "success": True,
                "batch_id": batch_id,
                "total_rows": total_rows_processed,
                "inserted_count": results['inserted_count'],
                "updated_count": results['updated_count'],
                "skipped_count": results['skipped_count'],
                "failed_count": results['failed_count'],
                "total_revenue_imported": float(results['total_revenue']),
                "total_units_imported": results['total_units'],
                "validation_errors": results['validation_errors'][:20],
                "header_detection_row": header_row,
                "performance": {
                    "duration_seconds": round(duration, 2),
                    "rows_per_second": round(rows_per_second, 0),
                    "peak_memory_percent": round(memory_monitor.get_peak() * 100, 1)
                }
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
