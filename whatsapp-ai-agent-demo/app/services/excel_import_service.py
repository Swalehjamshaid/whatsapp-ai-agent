# =====================================================================================================
# FILE: app/services/excel_import_service.py
# VERSION: v18.7 - DEBUG - AGGRESSIVE ERROR CAPTURE
# PURPOSE: Enterprise Excel import with aggressive error capture and logging
# =====================================================================================================

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
import gc
import os
from functools import lru_cache
from collections import OrderedDict

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# =====================================================================================================
# BLOCK 0: CONSTANTS & CONFIGURATION
# =====================================================================================================

BULK_SIZE = 1000  # REDUCED FOR DEBUGGING - will show errors sooner
GC_INTERVAL = 5
HEADER_SCAN_ROWS = 25
FUZZY_THRESHOLD = 85
MAX_ROWS_PER_FILE = 1000000
DIAGNOSTIC_MODE = os.environ.get("DN_IMPORT_DIAGNOSTIC", "true").lower() == "true"  # ENABLED BY DEFAULT
DIAGNOSTIC_DN = os.environ.get("DN_IMPORT_DIAGNOSTIC_DN", "6243725966")
DIAGNOSTIC_ROWS = int(os.environ.get("DN_IMPORT_DIAGNOSTIC_ROWS", "10"))

# =====================================================================================================
# BLOCK 0B: EXCEPTION CLASSES
# =====================================================================================================

class ImportError(Exception):
    """Base import exception."""
    pass

class HeaderNotFoundError(ImportError):
    pass

class WorksheetNotFoundError(ImportError):
    pass

class ValidationError(ImportError):
    pass

class VerificationError(Exception):
    pass

class ColumnMappingError(ImportError):
    pass

class BulkInsertError(ImportError):
    pass

# =====================================================================================================
# BLOCK 0C: HELPER FUNCTIONS
# =====================================================================================================

def normalize_header(header: Any) -> str:
    if header is None:
        return ""
    normalized = str(header).strip()
    normalized = re.sub(r'[_\-./\\#·•:;|]', ' ', normalized)
    normalized = normalized.replace('\u00a0', ' ')
    normalized = normalized.replace('\t', ' ')
    normalized = normalized.replace('\r', ' ')
    normalized = normalized.replace('\n', ' ')
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized.lower()

def get_exact_header(header: Any) -> str:
    if header is None:
        return ""
    return str(header).strip()

def normalize_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = ' '.join(value.strip().split())
        return cleaned if cleaned else None
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip() if str(value) else None

def parse_amount(value: Any) -> Optional[Decimal]:
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
        formats = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"]
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
    if not dn_no:
        return ""
    return re.sub(r'[^0-9]', '', dn_no.strip())

def normalize_city(city: str) -> str:
    if not city:
        return city
    city_map = {
        'lhr': 'Lahore', 'isb': 'Islamabad', 'rwp': 'Rawalpindi',
        'khi': 'Karachi', 'fsd': 'Faisalabad', 'mux': 'Multan',
        'pew': 'Peshawar', 'qta': 'Quetta', 'gjw': 'Gujranwala',
        'skt': 'Sialkot', 'wah': 'Wah Cantt',
    }
    return city_map.get(city.lower().strip(), city)

# =====================================================================================================
# BLOCK 1: COLUMN MAP
# =====================================================================================================

class ColumnMap:
    HEADER_MAP = {
        'Order type': 'order_type', 'Order Type': 'order_type', 'ORDER TYPE': 'order_type',
        'DN NO': 'dn_no', 'DN No': 'dn_no', 'dn no': 'dn_no', 'DN': 'dn_no', 'dn': 'dn_no',
        'DN amount': 'dn_amount', 'DN Amount': 'dn_amount', 'dn amount': 'dn_amount',
        'DN Qty': 'dn_qty', 'DN QTY': 'dn_qty', 'DN qty': 'dn_qty', 'dn qty': 'dn_qty',
        'DN Work': 'dn_work', 'DN WORK': 'dn_work', 'dn work': 'dn_work', 'work': 'dn_work',
        'Division': 'division', 'division': 'division',
        'Material NO': 'material_no', 'Material No': 'material_no', 'material no': 'material_no',
        'Customer Model': 'customer_model', 'customer model': 'customer_model',
        'sales office': 'sales_office', 'Sales Office': 'sales_office',
        'Sold-to-party Name': 'customer_name', 'customer name': 'customer_name',
        'Ship-to City': 'ship_to_city', 'ship to city': 'ship_to_city',
        'storage': 'storage_location', 'Storage': 'storage_location',
        'Warehouse': 'warehouse', 'warehouse': 'warehouse',
        'DN Create date': 'dn_create_date', 'dn create date': 'dn_create_date',
        'Good issue date': 'good_issue_date', 'good issue date': 'good_issue_date',
        'POD Date': 'pod_date', 'POD date': 'pod_date',
        'Sales Manager': 'sales_manager', 'sales manager': 'sales_manager',
    }
    
    MANDATORY_COLUMNS = ['dn_no', 'material_no']
    
    @classmethod
    def build_mapping(cls, headers: List[str]) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
        logger.info("=" * 60)
        logger.info("📋 BLOCK 1: BUILD COLUMN MAP")
        logger.info("=" * 60)
        
        field_to_column = {}
        column_to_field = {}
        unmapped_headers = []
        
        for header in headers:
            if header is None:
                continue
            exact = get_exact_header(header)
            normalized = normalize_header(header)
            
            field = cls.HEADER_MAP.get(exact)
            if field:
                field_to_column[field] = header
                column_to_field[header] = field
                logger.info(f"  ✅ EXACT: '{header}' → {field}")
                continue
            
            field = cls.HEADER_MAP.get(normalized)
            if field:
                field_to_column[field] = header
                column_to_field[header] = field
                logger.info(f"  ✅ NORMALIZED: '{header}' → {field}")
                continue
            
            unmapped_headers.append(header)
        
        # Check mandatory columns
        missing = [col for col in cls.MANDATORY_COLUMNS if col not in field_to_column]
        if missing:
            raise ColumnMappingError(f"Mandatory columns not found: {missing}")
        
        logger.info(f"  ✅ Mandatory columns mapped: {list(field_to_column.keys())}")
        logger.info("=" * 60)
        return field_to_column, column_to_field, unmapped_headers

# =====================================================================================================
# BLOCK 2: FROZEN MAPPING
# =====================================================================================================

class FrozenMapping:
    def __init__(self, mapping: Dict[str, str]):
        self._mapping = OrderedDict(mapping.items())
    
    def get(self, field: str) -> Optional[str]:
        return self._mapping.get(field)
    
    def __contains__(self, field: str) -> bool:
        return field in self._mapping
    
    def __getitem__(self, field: str) -> str:
        return self._mapping[field]
    
    def keys(self):
        return self._mapping.keys()
    
    def items(self):
        return self._mapping.items()

# =====================================================================================================
# BLOCK 3: WORKSHEET DETECTION
# =====================================================================================================

def detect_worksheet(file_path: str) -> Tuple[str, int, Dict[str, Any]]:
    logger.info("=" * 60)
    logger.info("🔍 WORKSHEET DETECTION")
    logger.info("=" * 60)
    
    try:
        xl = pd.ExcelFile(file_path, engine='openpyxl')
        sheet_names = xl.sheet_names
    except Exception as e:
        logger.error(f"❌ Failed to read Excel file: {e}")
        raise
    
    logger.info(f"📋 Found {len(sheet_names)} sheets: {sheet_names}")
    
    best_sheet = None
    best_score = 0
    best_header_row = 0
    best_info = {}
    
    for sheet_name in sheet_names:
        if sheet_name.startswith('_') or sheet_name.startswith('$'):
            continue
        
        if any(ind in sheet_name.lower() for ind in ['sum', 'summary', 'total', 'grand total']):
            logger.info(f"  ⏭️ Skipping summary sheet: '{sheet_name}'")
            continue
        
        logger.info(f"  📄 Checking sheet: '{sheet_name}'")
        
        try:
            df_sample = pd.read_excel(file_path, sheet_name=sheet_name, header=None, nrows=30, engine='openpyxl')
            if len(df_sample) == 0:
                continue
            
            header_row, score, matched_headers = detect_header_row(df_sample)
            
            has_dn = any('dn' in normalize_header(h).lower() for h in matched_headers)
            has_material = any('material' in normalize_header(h).lower() for h in matched_headers)
            
            logistics_score = score + (50 if has_dn else 0) + (50 if has_material else 0)
            
            if logistics_score > best_score:
                best_score = logistics_score
                best_sheet = sheet_name
                best_header_row = header_row
                best_info = {
                    'sheet_name': sheet_name,
                    'header_row': header_row,
                    'matched_headers': matched_headers,
                    'has_dn': has_dn,
                    'has_material': has_material,
                }
                logger.info(f"    ✅ New best sheet: '{sheet_name}'")
                
        except Exception as e:
            logger.warning(f"    ❌ Error reading sheet '{sheet_name}': {e}")
            continue
    
    if best_sheet is None:
        raise WorksheetNotFoundError("No worksheet with delivery data found.")
    
    logger.info("=" * 60)
    logger.info(f"✅ SELECTED SHEET: '{best_sheet}' (Header Row: {best_header_row})")
    logger.info("=" * 60)
    return best_sheet, best_header_row, best_info

def detect_header_row(df: pd.DataFrame, max_rows: int = 25) -> Tuple[int, int, List[str]]:
    if len(df) == 0:
        return 0, 0, []
    
    header_keywords = {
        'dn': 10, 'material': 10, 'qty': 5, 'amount': 5,
        'warehouse': 4, 'city': 3, 'model': 3, 'office': 3,
        'storage': 3, 'date': 2, 'manager': 2, 'work': 3,
    }
    
    best_score = 0
    best_row = 0
    best_matched = []
    
    for row_idx in range(min(max_rows, len(df))):
        score = 0
        matched = []
        for value in df.iloc[row_idx]:
            if value is None or not isinstance(value, str):
                continue
            normalized = normalize_header(value)
            if not normalized:
                continue
            for keyword, weight in header_keywords.items():
                if keyword in normalized:
                    score += weight
                    matched.append(str(value))
                    break
        if score > best_score:
            best_score = score
            best_row = row_idx
            best_matched = matched
    
    return best_row, best_score, best_matched

# =====================================================================================================
# BLOCK 4: FAST EXCEL READING
# =====================================================================================================

def read_excel_fast(file_path: str, sheet_name: str, header_row: int):
    try:
        import polars as pl
        try:
            df = pl.read_excel(file_path, sheet_name=sheet_name, header_row=header_row, engine='calamine')
            logger.info("⚡ Using Polars with calamine engine")
            return df.to_pandas()
        except:
            pass
    except:
        pass
    
    logger.info("📖 Using pandas")
    df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row, engine='openpyxl')
    logger.info(f"✅ Read {len(df)} rows with pandas")
    return df

# =====================================================================================================
# BLOCK 5: STATUS ENGINE
# =====================================================================================================

class StatusEngine:
    @staticmethod
    def derive(dn_create_date: Optional[date], good_issue_date: Optional[date], pod_date: Optional[date]) -> Dict[str, Any]:
        has_pgi = good_issue_date is not None
        has_pod = pod_date is not None
        
        if has_pod and has_pgi:
            return {'delivery_status': 'Delivered', 'pgi_status': 'Completed', 'pod_status': 'Completed', 'pending_flag': False}
        elif has_pgi:
            return {'delivery_status': 'In Transit', 'pgi_status': 'Completed', 'pod_status': 'Pending', 'pending_flag': True}
        else:
            return {'delivery_status': 'Pending Dispatch', 'pgi_status': 'Pending', 'pod_status': 'Pending', 'pending_flag': True}

# =====================================================================================================
# BLOCK 6: REFERENCE DATA
# =====================================================================================================

@lru_cache(maxsize=1000)
def get_warehouse_code(warehouse: str) -> Optional[str]:
    if not warehouse:
        return None
    warehouse_map = {
        'rawalpindi': 'RWP', 'islamabad': 'ISB', 'lahore': 'LHE',
        'karachi': 'KHI', 'faisalabad': 'FSD', 'multan': 'MUX',
        'peshawar': 'PEW', 'quetta': 'QTA', 'gujranwala': 'GJW',
        'sialkot': 'SKT', 'wah': 'WAH', 'wah cantt': 'WAH',
        'rwp': 'RWP', 'isb': 'ISB', 'lhr': 'LHE',
    }
    return warehouse_map.get(warehouse.lower().strip())

def get_delivery_location(ship_to_city: str) -> Optional[str]:
    if not ship_to_city:
        return None
    return normalize_city(ship_to_city)

def derive_customer_code(customer_name: str) -> Optional[str]:
    if not customer_name:
        return None
    code = re.sub(r'[^a-zA-Z0-9]', '_', customer_name[:15].upper())
    return f"CUST_{code}" if code else None

def derive_dealer_code(customer_name: str) -> Optional[str]:
    if not customer_name:
        return None
    code = re.sub(r'[^a-zA-Z0-9]', '_', customer_name[:15].upper())
    return f"DEAL_{code}" if code else None

# =====================================================================================================
# BLOCK 7: BATCH PROCESSOR - AGGRESSIVE ERROR CAPTURE
# =====================================================================================================

class FastBatchProcessor:
    def __init__(self, db: Session, frozen_mapping: FrozenMapping, batch_id: str, source_filename: str):
        self.db = db
        self.frozen_mapping = frozen_mapping
        self.batch_id = batch_id
        self.source_filename = source_filename
        
        # Counters
        self.rows_read = 0
        self.rows_extracted = 0
        self.rows_parsed = 0
        self.rows_validated = 0
        self.rows_buffered = 0
        self.rows_insert_attempted = 0
        self.rows_inserted = 0
        self.rows_failed = 0
        
        # Legacy compatibility
        self.inserted_count = 0
        self.updated_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        
        # Statistics
        self.total_revenue = Decimal(0)
        self.total_units = 0
        
        # Tracking
        self.processed_keys = set()
        self.duplicate_tracker = {}
        self.bulk_buffer = []
        self.commit_counter = 0
        
        # Error tracking - ALL ERRORS CAPTURED
        self.failure_stages = {
            'EXTRACTION': 0, 'PARSING': 0, 'VALIDATION': 0,
            'DUPLICATE_CHECK': 0, 'BUFFER_CREATION': 0,
            'DATABASE_INSERT': 0, 'COMMIT': 0,
        }
        
        self.first_failed_row = None
        self.validation_errors = []
        self.parsing_errors = []
        self.database_errors = []
        self.duplicate_errors = []
        self.unexpected_exceptions = []
        self.failed_rows = []
        self.extraction_stats = {
            'dn_work_extracted': 0, 'dn_qty_extracted': 0,
            'dn_amount_extracted': 0, 'storage_extracted': 0,
        }
        
        # Configuration
        self.skip_dups = False
        self.update_existing = False
        
        # Debug: log first few rows
        self.row_counter = 0
        self.logged_error = False
    
    def extract_critical_fields(self, row: Dict[str, Any], row_number: int) -> Dict[str, Any]:
        """Extract critical fields with detailed tracking."""
        raw_values = {'row_number': row_number}
        
        for field, raw_key in [('dn_work', 'raw_dn_work'), ('dn_qty', 'raw_dn_qty'), 
                               ('dn_amount', 'raw_dn_amount'), ('storage_location', 'raw_storage')]:
            col = self.frozen_mapping.get(field)
            if col:
                val = row.get(col)
                raw_values[raw_key] = val
                if val is not None and str(val).strip():
                    self.extraction_stats[f'{field}_extracted'] = self.extraction_stats.get(f'{field}_extracted', 0) + 1
        
        return raw_values
    
    def parse_critical_fields(self, raw_values: Dict[str, Any]) -> Dict[str, Any]:
        """Parse critical fields."""
        parsed = {}
        row_number = raw_values.get('row_number', 0)
        
        # DN Work
        try:
            parsed['dn_work'] = normalize_string(raw_values.get('raw_dn_work'))
        except Exception as e:
            parsed['dn_work'] = None
            self.parsing_errors.append({'row': row_number, 'field': 'dn_work', 'error': str(e)})
        
        # DN Qty
        try:
            parsed['dn_qty'] = parse_quantity(raw_values.get('raw_dn_qty'))
        except Exception as e:
            parsed['dn_qty'] = None
            self.parsing_errors.append({'row': row_number, 'field': 'dn_qty', 'error': str(e)})
        
        # DN Amount
        try:
            parsed['dn_amount'] = parse_amount(raw_values.get('raw_dn_amount'))
        except Exception as e:
            parsed['dn_amount'] = None
            self.parsing_errors.append({'row': row_number, 'field': 'dn_amount', 'error': str(e)})
        
        # Storage
        try:
            parsed['storage_location'] = normalize_string(raw_values.get('raw_storage'))
        except Exception as e:
            parsed['storage_location'] = None
            self.parsing_errors.append({'row': row_number, 'field': 'storage_location', 'error': str(e)})
        
        return parsed
    
    def process_row(self, row_data: Dict[str, Any], row_number: int, row: Dict[str, Any]) -> bool:
        """Process a single row with aggressive error capture."""
        self.row_counter += 1
        
        try:
            self.rows_read += 1
            
            # ============================================================
            # STAGE 1: EXTRACTION
            # ============================================================
            raw_values = self.extract_critical_fields(row, row_number)
            self.rows_extracted += 1
            
            # ============================================================
            # STAGE 2: PARSING
            # ============================================================
            parsed_values = self.parse_critical_fields(raw_values)
            self.rows_parsed += 1
            
            # Add parsed values
            row_data.update(parsed_values)
            
            # ============================================================
            # STAGE 3: EXTRACT MANDATORY FIELDS
            # ============================================================
            dn_no_col = self.frozen_mapping.get('dn_no')
            if dn_no_col:
                row_data['dn_no'] = normalize_dn(str(row.get(dn_no_col)) if row.get(dn_no_col) else None)
            else:
                row_data['dn_no'] = None
            
            mat_no_col = self.frozen_mapping.get('material_no')
            if mat_no_col:
                row_data['material_no'] = normalize_string(row.get(mat_no_col))
            else:
                row_data['material_no'] = None
            
            # ============================================================
            # STAGE 4: EXTRACT OTHER FIELDS
            # ============================================================
            for field in ['order_type', 'division', 'customer_model', 'sales_office', 
                         'customer_name', 'ship_to_city', 'warehouse', 'sales_manager', 'remarks']:
                col = self.frozen_mapping.get(field)
                if col:
                    row_data[field] = normalize_string(row.get(col))
                else:
                    row_data[field] = None
            
            # Dates
            for field in ['dn_create_date', 'good_issue_date', 'pod_date']:
                col = self.frozen_mapping.get(field)
                if col:
                    row_data[field] = parse_date(row.get(col))
                else:
                    row_data[field] = None
            
            # ============================================================
            # STAGE 5: VALIDATION - CRITICAL
            # ============================================================
            dn_no = row_data.get('dn_no')
            material_no = row_data.get('material_no')
            
            validation_errors = []
            if not dn_no:
                validation_errors.append("Missing DN NO")
            if not material_no:
                validation_errors.append("Missing Material NO")
            
            if validation_errors:
                self.rows_failed += 1
                self.failed_count += 1
                self.failure_stages['VALIDATION'] += 1
                
                error_msg = f"Validation failed: {', '.join(validation_errors)}"
                self.validation_errors.append({'row': row_number, 'dn': dn_no, 'material': material_no, 'error': error_msg})
                self.failed_rows.append({'row': row_number, 'dn': dn_no, 'material': material_no, 'stage': 'VALIDATION', 'error': error_msg})
                
                # Store first failed row
                if self.first_failed_row is None:
                    self.first_failed_row = {
                        'row': row_number, 'dn': dn_no, 'material': material_no,
                        'stage': 'VALIDATION', 'errors': validation_errors,
                        'raw_values': raw_values, 'parsed_values': parsed_values
                    }
                
                # Log the error immediately
                logger.error(f"❌ Row {row_number}: {error_msg} (DN={dn_no}, Material={material_no})")
                return False
            
            self.rows_validated += 1
            
            # ============================================================
            # STAGE 6: DUPLICATE CHECK
            # ============================================================
            duplicate_key = f"{dn_no}_{material_no}"
            
            if duplicate_key in self.processed_keys:
                prev_row = self.duplicate_tracker.get(duplicate_key)
                self.rows_failed += 1
                self.failed_count += 1
                self.failure_stages['DUPLICATE_CHECK'] += 1
                
                error_msg = f"Duplicate key: {duplicate_key} (previous row: {prev_row})"
                self.duplicate_errors.append({'row': row_number, 'dn': dn_no, 'material': material_no, 'previous_row': prev_row})
                self.failed_rows.append({'row': row_number, 'dn': dn_no, 'material': material_no, 'stage': 'DUPLICATE_CHECK', 'error': error_msg})
                
                if self.first_failed_row is None:
                    self.first_failed_row = {
                        'row': row_number, 'dn': dn_no, 'material': material_no,
                        'stage': 'DUPLICATE_CHECK', 'errors': [error_msg],
                        'raw_values': raw_values, 'parsed_values': parsed_values
                    }
                
                logger.warning(f"⚠️ Row {row_number}: {error_msg}")
                return False
            
            self.processed_keys.add(duplicate_key)
            self.duplicate_tracker[duplicate_key] = row_number
            
            # ============================================================
            # STAGE 7: DERIVE STATUS
            # ============================================================
            try:
                status = StatusEngine.derive(
                    row_data.get('dn_create_date'),
                    row_data.get('good_issue_date'),
                    row_data.get('pod_date')
                )
                row_data['warehouse_code'] = get_warehouse_code(row_data.get('warehouse'))
                row_data['delivery_location'] = get_delivery_location(row_data.get('ship_to_city'))
                
                if row_data.get('customer_name'):
                    row_data['customer_code'] = derive_customer_code(row_data.get('customer_name'))
                    row_data['dealer_code'] = derive_dealer_code(row_data.get('customer_name'))
                else:
                    row_data['customer_code'] = None
                    row_data['dealer_code'] = None
            except Exception as e:
                logger.warning(f"⚠️ Row {row_number}: Enrichment error: {e}")
                status = {'delivery_status': 'Pending', 'pgi_status': 'Pending', 'pod_status': 'Pending', 'pending_flag': True}
                row_data['warehouse_code'] = None
                row_data['delivery_location'] = None
                row_data['customer_code'] = None
                row_data['dealer_code'] = None
            
            # ============================================================
            # STAGE 8: BUFFER CREATION
            # ============================================================
            try:
                buffer_record = {
                    'dn_no': row_data['dn_no'],
                    'dn_work': row_data['dn_work'],
                    'order_type': row_data['order_type'],
                    'division': row_data['division'],
                    'customer_code': row_data['customer_code'],
                    'dealer_code': row_data['dealer_code'],
                    'customer_name': row_data['customer_name'],
                    'customer_model': row_data['customer_model'],
                    'material_no': row_data['material_no'],
                    'storage_location': row_data['storage_location'],
                    'sales_office': row_data['sales_office'],
                    'sales_manager': row_data['sales_manager'],
                    'ship_to_city': row_data['ship_to_city'],
                    'warehouse': row_data['warehouse'],
                    'warehouse_code': row_data['warehouse_code'],
                    'delivery_location': row_data['delivery_location'],
                    'dn_qty': row_data['dn_qty'],
                    'dn_amount': float(row_data['dn_amount']) if row_data['dn_amount'] else None,
                    'dn_create_date': row_data['dn_create_date'],
                    'good_issue_date': row_data['good_issue_date'],
                    'pod_date': row_data['pod_date'],
                    'remarks': row_data['remarks'],
                    'delivery_status': status['delivery_status'],
                    'pgi_status': status['pgi_status'],
                    'pod_status': status['pod_status'],
                    'pending_flag': status['pending_flag'],
                    'source_file': self.source_filename,
                    'upload_batch_id': self.batch_id,
                    'imported_at': datetime.utcnow()
                }
                
                self.bulk_buffer.append(buffer_record)
                self.rows_buffered += 1
                
            except Exception as e:
                self.rows_failed += 1
                self.failed_count += 1
                self.failure_stages['BUFFER_CREATION'] += 1
                
                error_msg = f"Buffer creation failed: {str(e)}"
                self.failed_rows.append({'row': row_number, 'dn': dn_no, 'material': material_no, 'stage': 'BUFFER_CREATION', 'error': error_msg})
                
                if self.first_failed_row is None:
                    self.first_failed_row = {
                        'row': row_number, 'dn': dn_no, 'material': material_no,
                        'stage': 'BUFFER_CREATION', 'errors': [error_msg],
                        'raw_values': raw_values, 'parsed_values': parsed_values
                    }
                
                logger.error(f"❌ Row {row_number}: {error_msg}")
                logger.error(traceback.format_exc())
                return False
            
            # ============================================================
            # STAGE 9: STATISTICS
            # ============================================================
            try:
                if row_data.get('dn_amount'):
                    self.total_revenue += row_data['dn_amount']
                if row_data.get('dn_qty'):
                    self.total_units += row_data['dn_qty']
            except Exception as e:
                logger.warning(f"⚠️ Row {row_number}: Statistics update failed: {e}")
            
            # ============================================================
            # FLUSH IF BUFFER FULL
            # ============================================================
            if len(self.bulk_buffer) >= BULK_SIZE:
                self.flush_bulk()
            
            return True
            
        except Exception as e:
            # Catch-all for unexpected errors
            self.rows_failed += 1
            self.failed_count += 1
            self.failure_stages['EXTRACTION'] += 1
            
            error_msg = f"Unexpected error: {str(e)}"
            self.unexpected_exceptions.append({
                'row': row_number, 'dn': row_data.get('dn_no'), 
                'material': row_data.get('material_no'), 'error': str(e),
                'traceback': traceback.format_exc()
            })
            self.failed_rows.append({
                'row': row_number, 'dn': row_data.get('dn_no'),
                'material': row_data.get('material_no'),
                'stage': 'EXTRACTION', 'error': error_msg
            })
            
            if self.first_failed_row is None:
                self.first_failed_row = {
                    'row': row_number, 'dn': row_data.get('dn_no'),
                    'material': row_data.get('material_no'),
                    'stage': 'EXTRACTION', 'errors': [error_msg],
                    'raw_values': raw_values if 'raw_values' in locals() else {},
                    'parsed_values': parsed_values if 'parsed_values' in locals() else {},
                    'exception_type': type(e).__name__,
                    'exception_message': str(e),
                    'stack_trace': traceback.format_exc()
                }
            
            logger.error(f"❌ Row {row_number}: {error_msg}")
            logger.error(traceback.format_exc())
            return False
    
    def flush_bulk(self, is_final: bool = False):
        """Flush bulk buffer with error capture."""
        if not self.bulk_buffer:
            return
        
        self.rows_insert_attempted += len(self.bulk_buffer)
        
        logger.info(f"📊 Flushing {len(self.bulk_buffer):,} rows...")
        
        try:
            self.db.bulk_insert_mappings(DeliveryReport, self.bulk_buffer)
            self.db.commit()
            
            batch_size = len(self.bulk_buffer)
            self.rows_inserted += batch_size
            self.inserted_count += batch_size
            self.rows_insert_attempted -= batch_size
            
            self.commit_counter += 1
            logger.info(f"✅ Committed batch {self.commit_counter} ({batch_size:,} rows)")
            self.bulk_buffer.clear()
            
        except Exception as e:
            # Find offending record
            offending = None
            if self.bulk_buffer:
                for idx, record in enumerate(self.bulk_buffer[:10]):  # Check first 10
                    try:
                        self.db.bulk_insert_mappings(DeliveryReport, [record])
                        self.db.rollback()
                    except Exception as re:
                        offending = {
                            'index': idx,
                            'dn_no': record.get('dn_no'),
                            'material_no': record.get('material_no'),
                            'dn_work': record.get('dn_work'),
                            'dn_qty': record.get('dn_qty'),
                            'dn_amount': record.get('dn_amount'),
                            'storage_location': record.get('storage_location'),
                            'error': str(re)
                        }
                        break
            
            if offending:
                error_msg = f"Bulk insert failed at record {offending['index']}: DN={offending['dn_no']}, Material={offending['material_no']}"
                logger.error(f"❌ {error_msg}")
                logger.error(f"   DN Work: {offending['dn_work']}")
                logger.error(f"   DN Qty: {offending['dn_qty']}")
                logger.error(f"   DN Amount: {offending['dn_amount']}")
                logger.error(f"   Storage: {offending['storage_location']}")
                logger.error(f"   Error: {offending['error']}")
                
                self.database_errors.append({
                    'error': str(e),
                    'offending_record': offending,
                    'rows_in_batch': len(self.bulk_buffer)
                })
                
                if self.first_failed_row is None:
                    self.first_failed_row = {
                        'row': 0,
                        'dn': offending['dn_no'],
                        'material': offending['material_no'],
                        'stage': 'DATABASE_INSERT',
                        'errors': [str(e)],
                        'parsed_values': {
                            'dn_work': offending['dn_work'],
                            'dn_qty': offending['dn_qty'],
                            'dn_amount': offending['dn_amount'],
                            'storage_location': offending['storage_location'],
                        },
                        'exception_type': type(e).__name__,
                        'exception_message': str(e),
                        'stack_trace': traceback.format_exc()
                    }
            else:
                logger.error(f"❌ Bulk insert failed: {e}")
                self.database_errors.append({'error': str(e), 'rows_in_batch': len(self.bulk_buffer)})
            
            self.bulk_buffer.clear()
            raise BulkInsertError(str(e)) from e
    
    def execute_transaction(self, delete_existing: bool = False) -> Dict[str, Any]:
        """Execute transaction with rollback."""
        try:
            if self.bulk_buffer:
                self.flush_bulk(is_final=True)
            
            deleted_count = 0
            if delete_existing:
                result = self.db.execute(
                    text("DELETE FROM delivery_reports WHERE upload_batch_id = :batch_id"),
                    {"batch_id": self.batch_id}
                )
                deleted_count = result.rowcount
                logger.info(f"🗑️ Deleted {deleted_count} existing records")
            
            self.db.commit()
            return {'success': True, 'deleted_count': deleted_count}
            
        except Exception as e:
            logger.error(f"❌ Transaction failed: {e}")
            self.db.rollback()
            raise
    
    def finalize(self, delete_existing: bool = False) -> Dict[str, Any]:
        """Finalize with full error reporting."""
        transaction_result = self.execute_transaction(delete_existing)
        
        logger.info("=" * 60)
        logger.info("📊 IMPORT FINAL SUMMARY")
        logger.info("=" * 60)
        logger.info(f"  Rows Read: {self.rows_read:,}")
        logger.info(f"  Rows Extracted: {self.rows_extracted:,}")
        logger.info(f"  Rows Parsed: {self.rows_parsed:,}")
        logger.info(f"  Rows Validated: {self.rows_validated:,}")
        logger.info(f"  Rows Buffered: {self.rows_buffered:,}")
        logger.info(f"  Rows Inserted: {self.rows_inserted:,}")
        logger.info(f"  Rows Failed: {self.rows_failed:,}")
        logger.info("")
        
        if self.validation_errors:
            logger.error(f"  ❌ Validation Errors: {len(self.validation_errors)}")
            for err in self.validation_errors[:5]:
                logger.error(f"     Row {err.get('row')}: {err.get('error')}")
        
        if self.database_errors:
            logger.error(f"  ❌ Database Errors: {len(self.database_errors)}")
            for err in self.database_errors[:5]:
                logger.error(f"     {err.get('error')}")
        
        if self.duplicate_errors:
            logger.warning(f"  ⚠️ Duplicate Errors: {len(self.duplicate_errors)}")
        
        if self.first_failed_row:
            logger.error("")
            logger.error("  🔴 FIRST FAILED ROW:")
            logger.error(f"     Row: {self.first_failed_row.get('row')}")
            logger.error(f"     DN: {self.first_failed_row.get('dn')}")
            logger.error(f"     Material: {self.first_failed_row.get('material')}")
            logger.error(f"     Stage: {self.first_failed_row.get('stage')}")
            logger.error(f"     Errors: {self.first_failed_row.get('errors', [])}")
        
        logger.info("=" * 60)
        
        return {
            'rows_read': self.rows_read,
            'rows_extracted': self.rows_extracted,
            'rows_parsed': self.rows_parsed,
            'rows_validated': self.rows_validated,
            'rows_buffered': self.rows_buffered,
            'rows_insert_attempted': self.rows_insert_attempted,
            'rows_inserted': self.rows_inserted,
            'rows_failed': self.rows_failed,
            'inserted_count': self.inserted_count,
            'updated_count': self.updated_count,
            'skipped_count': self.skipped_count,
            'failed_count': self.failed_count,
            'total_revenue': float(self.total_revenue),
            'total_units': self.total_units,
            'deleted_count': transaction_result.get('deleted_count', 0),
            'failure_stage_counts': self.failure_stages,
            'extraction_stats': self.extraction_stats,
            'first_failed_row': self.first_failed_row,
            'failed_rows': self.failed_rows[:10],
            'duplicate_errors': self.duplicate_errors[:20],
            'validation_errors': self.validation_errors[:10],
            'parsing_errors': self.parsing_errors[:10],
            'database_errors': self.database_errors[:10],
            'unexpected_exceptions': self.unexpected_exceptions[:10],
        }

# =====================================================================================================
# BLOCK 8: EXCEL IMPORT SERVICE
# =====================================================================================================

class ExcelImportService:
    @staticmethod
    def import_delivery_report_excel(
        db: Session,
        file_path: str,
        source_filename: str,
        batch_id: str = None,
        skip_dups: bool = False,
        update_existing_rows: bool = False,
        delete_existing: bool = False
    ) -> Dict[str, Any]:
        """Import Excel with aggressive error capture."""
        
        start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("⚡ EXCEL IMPORT v18.7 - AGGRESSIVE ERROR CAPTURE")
        logger.info("=" * 60)
        logger.info(f"📁 File: {file_path}")
        logger.info(f"🗑️ Delete Existing: {delete_existing}")
        
        if not batch_id:
            batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        logger.info(f"📋 Batch: {batch_id}")
        
        try:
            # Detect worksheet
            sheet_name, header_row, sheet_info = detect_worksheet(file_path)
            
            # Read Excel
            logger.info(f"📖 Reading sheet '{sheet_name}'")
            df = read_excel_fast(file_path, sheet_name, header_row)
            df = df.dropna(how='all')
            df = df.dropna(axis=1, how='all')
            
            total_rows = len(df)
            logger.info(f"📄 Read {total_rows:,} rows, {len(df.columns)} columns")
            
            # Log column names for debugging
            logger.info("📋 Excel Columns:")
            for i, col in enumerate(df.columns):
                logger.info(f"  {i}: '{col}'")
            
            # Build column map
            headers = [str(col).strip() for col in df.columns]
            field_to_column, column_to_field, unmapped = ColumnMap.build_mapping(headers)
            
            frozen_mapping = FrozenMapping(field_to_column)
            logger.info("🔒 Mapping locked")
            
            # Process rows
            processor = FastBatchProcessor(db, frozen_mapping, batch_id, source_filename)
            processor.skip_dups = skip_dups
            processor.update_existing = update_existing_rows
            
            rows = df.to_dict('records')
            rows_to_process = DIAGNOSTIC_ROWS if DIAGNOSTIC_MODE else len(rows)
            processed_count = 0
            
            logger.info(f"🔄 Processing {rows_to_process:,} rows...")
            
            for idx in range(rows_to_process):
                row = rows[idx]
                row_number = idx + 2 + header_row
                
                try:
                    # Build row data
                    row_data = {}
                    processor.process_row(row_data, row_number, row)
                    processed_count += 1
                    
                    if processed_count % 1000 == 0:
                        logger.info(f"📊 Processed {processed_count:,} rows...")
                    
                    if DIAGNOSTIC_MODE and processed_count >= DIAGNOSTIC_ROWS:
                        logger.info(f"🔍 Diagnostic mode: Processed {processed_count} rows, stopping")
                        break
                    
                except Exception as e:
                    logger.error(f"❌ Row {row_number} processing error: {e}")
                    logger.error(traceback.format_exc())
                    processor.rows_failed += 1
                    processor.failed_count += 1
                    processor.failed_rows.append({
                        'row': row_number,
                        'error': str(e),
                        'stage': 'PROCESSING'
                    })
                    
                    if DIAGNOSTIC_MODE:
                        break
            
            # Finalize
            logger.info("💾 Finalizing import...")
            results = processor.finalize(delete_existing=delete_existing)
            
            duration = time.time() - start_time
            
            response = {
                "success": True,
                "batch_id": batch_id,
                "total_rows": total_rows,
                "processed_rows": processed_count,
                "rows_read": results['rows_read'],
                "rows_extracted": results['rows_extracted'],
                "rows_parsed": results['rows_parsed'],
                "rows_validated": results['rows_validated'],
                "rows_buffered": results['rows_buffered'],
                "rows_insert_attempted": results['rows_insert_attempted'],
                "rows_inserted": results['rows_inserted'],
                "rows_failed": results['rows_failed'],
                "inserted_count": results['inserted_count'],
                "updated_count": results['updated_count'],
                "skipped_count": results['skipped_count'],
                "failed_count": results['failed_count'],
                "total_revenue_imported": results['total_revenue'],
                "total_units_imported": results['total_units'],
                "deleted_count": results['deleted_count'],
                "sheet_name": sheet_name,
                "header_row": header_row,
                "performance": {
                    "duration_seconds": round(duration, 2),
                    "rows_per_second": round(processed_count / duration if duration > 0 else 0, 0),
                    "bulk_size": BULK_SIZE
                },
                "failure_stage_counts": results['failure_stage_counts'],
                "extraction_stats": results['extraction_stats'],
                "first_failed_row": results['first_failed_row'],
                "failed_rows": results['failed_rows'][:10],
                "duplicate_errors": results['duplicate_errors'][:20],
                "validation_errors": results['validation_errors'][:10],
                "parsing_errors": results['parsing_errors'][:10],
                "database_errors": results['database_errors'][:10],
                "unexpected_exceptions": results['unexpected_exceptions'][:10],
            }
            
            return response
            
        except Exception as e:
            logger.error(f"❌ Import failed: {e}")
            logger.error(traceback.format_exc())
            db.rollback()
            
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "batch_id": batch_id,
                "total_rows": 0,
                "rows_read": 0,
                "rows_inserted": 0,
                "rows_failed": 0,
                "deleted_count": 0,
                "rollback": True
            }

# =====================================================================================================
# BLOCK 9: EXPORTS
# =====================================================================================================

__all__ = ['ExcelImportService', 'VerificationError', 'normalize_header', 'normalize_dn']

# =====================================================================================================
# MODULE INITIALIZATION
# =====================================================================================================

logger.info("=" * 60)
logger.info("📊 EXCEL IMPORT SERVICE v18.7 - AGGRESSIVE ERROR CAPTURE")
logger.info("=" * 60)
logger.info("")
logger.info("  ✅ DIAGNOSTIC MODE: ENABLED (first 10 rows)")
logger.info("  ✅ BULK SIZE: 1000 (for faster error detection)")
logger.info("  ✅ AGGRESSIVE ERROR CAPTURE")
logger.info("  ✅ FULL ERROR REPORTING")
logger.info("=" * 60)

# =====================================================================================================
# END OF FILE
# =====================================================================================================
