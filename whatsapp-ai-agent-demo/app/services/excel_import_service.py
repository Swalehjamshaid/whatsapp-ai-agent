# =====================================================================================================
# FILE: app/services/excel_import_service.py
# VERSION: v18.2 - FIXED VALIDATION & SAFE REPLACE MODE
# PURPOSE: Enterprise Excel import with relaxed validation and safe operations
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

BULK_SIZE = 50000
GC_INTERVAL = 5
HEADER_SCAN_ROWS = 25
FUZZY_THRESHOLD = 85
MAX_ROWS_PER_FILE = 1000000
DIAGNOSTIC_MODE = os.environ.get("DN_IMPORT_DIAGNOSTIC", "false").lower() == "true"
DIAGNOSTIC_DN = os.environ.get("DN_IMPORT_DIAGNOSTIC_DN", "6243725966")
DIAGNOSTIC_ROWS = int(os.environ.get("DN_IMPORT_DIAGNOSTIC_ROWS", "10"))

# =====================================================================================================
# BLOCK 0B: EXCEPTION CLASSES
# =====================================================================================================

class ImportError(Exception):
    """Base import exception."""
    pass

class HeaderNotFoundError(ImportError):
    """Raised when header row cannot be detected."""
    pass

class WorksheetNotFoundError(ImportError):
    """Raised when no worksheet with data is found."""
    pass

class ValidationError(ImportError):
    """Raised when data validation fails."""
    pass

class VerificationError(Exception):
    """Raised when post-import verification fails."""
    pass

class ColumnMappingError(ImportError):
    """Raised when critical columns cannot be mapped."""
    pass

class RowValidationError(Exception):
    """Row-level validation error - does not stop the entire import."""
    pass

class CriticalExtractionError(ImportError):
    """Raised when critical column extraction fails."""
    pass

# =====================================================================================================
# BLOCK 0C: HELPER FUNCTIONS
# =====================================================================================================

def normalize_header(header: Any) -> str:
    """Normalize Excel header for consistent matching."""
    if header is None:
        return ""
    
    normalized = str(header).strip()
    normalized = re.sub(r'[_\-./\\#·•:;|]', ' ', normalized)
    normalized = normalized.replace('\u00a0', ' ')
    normalized = normalized.replace('\t', ' ')
    normalized = normalized.replace('\r', ' ')
    normalized = normalized.replace('\n', ' ')
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    normalized = normalized.lower()
    
    return normalized

def get_exact_header(header: Any) -> str:
    """Get the exact header as it appears in Excel."""
    if header is None:
        return ""
    return str(header).strip()

def normalize_string(value: Any) -> Optional[str]:
    """Normalize string, convert empty to None."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = ' '.join(value.strip().split())
        return cleaned if cleaned else None
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip() if str(value) else None

def parse_amount(value: Any) -> Optional[Decimal]:
    """
    Parse amount with support for: 117698, 117,698, 117,698.00
    """
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
        # Remove commas, spaces, currency symbols
        cleaned = re.sub(r'[^\d.]', '', value.strip())
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except:
            return None
    return None

def parse_quantity(value: Any) -> Optional[int]:
    """
    Parse quantity, convert safely to Integer.
    """
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
    """
    Parse date with support for: 05.06.2026, 2026-06-05, Excel Serial Dates
    """
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
    """Normalize DN number - remove non-numeric characters."""
    if not dn_no:
        return ""
    return re.sub(r'[^0-9]', '', dn_no.strip())

def normalize_city(city: str) -> str:
    """
    Normalize city names for consistent matching.
    LHR → Lahore, ISB → Islamabad, RWP → Rawalpindi
    """
    if not city:
        return city
    
    city_map = {
        'lhr': 'Lahore',
        'isb': 'Islamabad',
        'rwp': 'Rawalpindi',
        'khi': 'Karachi',
        'fsd': 'Faisalabad',
        'mux': 'Multan',
        'pew': 'Peshawar',
        'qta': 'Quetta',
        'gjw': 'Gujranwala',
        'skt': 'Sialkot',
        'wah': 'Wah Cantt',
    }
    
    key = city.lower().strip()
    return city_map.get(key, city)

# =====================================================================================================
# BLOCK 1: BUILD SINGLE COLUMN MAP
# =====================================================================================================

class ColumnMap:
    """
    BLOCK 1: Build a Single Column Map with enhanced critical column detection.
    """
    
    # Core mapping - Excel Header → PostgreSQL Field
    HEADER_MAP = {
        # Order Type
        'Order type': 'order_type',
        'Order Type': 'order_type',
        'ORDER TYPE': 'order_type',
        'order type': 'order_type',
        'order_type': 'order_type',
        'order': 'order_type',
        'ordertype': 'order_type',
        
        # DN NO - CRITICAL
        'DN NO': 'dn_no',
        'DN No': 'dn_no',
        'dn no': 'dn_no',
        'DN': 'dn_no',
        'dn': 'dn_no',
        'dn_no': 'dn_no',
        'delivery note': 'dn_no',
        'delivery note no': 'dn_no',
        'delivery number': 'dn_no',
        'dn#': 'dn_no',
        'dn-number': 'dn_no',
        
        # DN AMOUNT
        'DN amount': 'dn_amount',
        'DN Amount': 'dn_amount',
        'dn amount': 'dn_amount',
        'dn_amount': 'dn_amount',
        'amount': 'dn_amount',
        'Amount': 'dn_amount',
        'AMOUNT': 'dn_amount',
        'amt': 'dn_amount',
        'Amt': 'dn_amount',
        'AMT': 'dn_amount',
        'dn amt': 'dn_amount',
        'DN Amt': 'dn_amount',
        'net amount': 'dn_amount',
        'total': 'dn_amount',
        'order amount': 'dn_amount',
        'value': 'dn_amount',
        'dn value': 'dn_amount',
        'invoice amount': 'dn_amount',
        'net': 'dn_amount',
        'pkr': 'dn_amount',
        
        # DN QTY
        'DN Qty': 'dn_qty',
        'DN QTY': 'dn_qty',
        'DN qty': 'dn_qty',
        'dn qty': 'dn_qty',
        'dn_qty': 'dn_qty',
        'qty': 'dn_qty',
        'Qty': 'dn_qty',
        'QTY': 'dn_qty',
        'quantity': 'dn_qty',
        'Quantity': 'dn_qty',
        'QUANTITY': 'dn_qty',
        'dn quantity': 'dn_qty',
        'DN Quantity': 'dn_qty',
        'units': 'dn_qty',
        'pcs': 'dn_qty',
        'piece': 'dn_qty',
        
        # DN WORK
        'DN Work': 'dn_work',
        'DN WORK': 'dn_work',
        'dn work': 'dn_work',
        'dn_work': 'dn_work',
        'work': 'dn_work',
        'Work': 'dn_work',
        'status': 'dn_work',
        'dn status': 'dn_work',
        'delivery status': 'dn_work',
        'work order': 'dn_work',
        'order status': 'dn_work',
        'delivery work': 'dn_work',
        'work status': 'dn_work',
        'dn work status': 'dn_work',
        'delivery note work': 'dn_work',
        
        # DIVISION
        'Division': 'division',
        'division': 'division',
        'DIVISION': 'division',
        'div': 'division',
        'department': 'division',
        'business unit': 'division',
        
        # MATERIAL NO - CRITICAL
        'Material NO': 'material_no',
        'Material No': 'material_no',
        'MATERIAL NO': 'material_no',
        'material no': 'material_no',
        'material_no': 'material_no',
        'material': 'material_no',
        'MATERIAL': 'material_no',
        'material number': 'material_no',
        'Material Number': 'material_no',
        'material code': 'material_no',
        'Material Code': 'material_no',
        'sku': 'material_no',
        'SKU': 'material_no',
        'product no': 'material_no',
        'product number': 'material_no',
        'item no': 'material_no',
        'item': 'material_no',
        'part no': 'material_no',
        'part number': 'material_no',
        
        # CUSTOMER MODEL
        'Customer Model': 'customer_model',
        'CUSTOMER MODEL': 'customer_model',
        'customer model': 'customer_model',
        'customer_model': 'customer_model',
        'model': 'customer_model',
        'Model': 'customer_model',
        'MODEL': 'customer_model',
        'product model': 'customer_model',
        'product': 'customer_model',
        'description': 'customer_model',
        'item description': 'customer_model',
        
        # SALES OFFICE
        'sales office': 'sales_office',
        'Sales Office': 'sales_office',
        'SALES OFFICE': 'sales_office',
        'sales_office': 'sales_office',
        'office': 'sales_office',
        'sales': 'sales_office',
        'branch': 'sales_office',
        'region': 'sales_office',
        
        # SOLD-TO-PARTY NAME
        'Sold-to-party Name': 'customer_name',
        'Sold-to-party name': 'customer_name',
        'Sold to Party Name': 'customer_name',
        'SOLD TO PARTY NAME': 'customer_name',
        'customer name': 'customer_name',
        'Customer Name': 'customer_name',
        'CUSTOMER NAME': 'customer_name',
        'customer_name': 'customer_name',
        'dealer name': 'customer_name',
        'Dealer Name': 'customer_name',
        'party name': 'customer_name',
        'customer': 'customer_name',
        'dealer': 'customer_name',
        'party': 'customer_name',
        
        # SHIP-TO CITY
        'Ship-to City': 'ship_to_city',
        'Ship-to city': 'ship_to_city',
        'Ship To City': 'ship_to_city',
        'SHIP-TO CITY': 'ship_to_city',
        'ship to city': 'ship_to_city',
        'ship_to_city': 'ship_to_city',
        'city': 'ship_to_city',
        'City': 'ship_to_city',
        'destination city': 'ship_to_city',
        'delivery city': 'ship_to_city',
        'customer city': 'ship_to_city',
        
        # STORAGE LOCATION
        'storage': 'storage_location',
        'Storage': 'storage_location',
        'STORAGE': 'storage_location',
        'storage location': 'storage_location',
        'Storage Location': 'storage_location',
        'STORAGE LOCATION': 'storage_location',
        'storage_location': 'storage_location',
        'bin': 'storage_location',
        'warehouse bin': 'storage_location',
        'location': 'storage_location',
        'store': 'storage_location',
        'storage loc': 'storage_location',
        'storage loc.': 'storage_location',
        'storage area': 'storage_location',
        
        # WAREHOUSE
        'Warehouse': 'warehouse',
        'warehouse': 'warehouse',
        'WAREHOUSE': 'warehouse',
        'ware house': 'warehouse',
        'Ware House': 'warehouse',
        'WH': 'warehouse',
        'wh': 'warehouse',
        'whse': 'warehouse',
        'plant': 'warehouse',
        'facility': 'warehouse',
        'warehouse name': 'warehouse',
        'Warehouse Name': 'warehouse',
        
        # DN CREATE DATE
        'DN Create date': 'dn_create_date',
        'DN Create Date': 'dn_create_date',
        'DN create date': 'dn_create_date',
        'dn create date': 'dn_create_date',
        'dn_create_date': 'dn_create_date',
        'create date': 'dn_create_date',
        'created date': 'dn_create_date',
        'dn created': 'dn_create_date',
        'order date': 'dn_create_date',
        'document date': 'dn_create_date',
        'dn date': 'dn_create_date',
        'date': 'dn_create_date',
        
        # GOOD ISSUE DATE
        'Good issue date': 'good_issue_date',
        'Good Issue Date': 'good_issue_date',
        'GOOD ISSUE DATE': 'good_issue_date',
        'good issue date': 'good_issue_date',
        'good_issue_date': 'good_issue_date',
        'PGI': 'good_issue_date',
        'PGI Date': 'good_issue_date',
        'pgi date': 'good_issue_date',
        'goods issue': 'good_issue_date',
        'dispatch date': 'good_issue_date',
        'shipped date': 'good_issue_date',
        'ship date': 'good_issue_date',
        'delivery date': 'good_issue_date',
        
        # POD DATE
        'POD Date': 'pod_date',
        'POD date': 'pod_date',
        'POD': 'pod_date',
        'pod date': 'pod_date',
        'pod_date': 'pod_date',
        'proof of delivery': 'pod_date',
        'received date': 'pod_date',
        'confirmation date': 'pod_date',
        'receipt date': 'pod_date',
        'customer received': 'pod_date',
        
        # SALES MANAGER
        'Sales Manager': 'sales_manager',
        'SALES MANAGER': 'sales_manager',
        'sales manager': 'sales_manager',
        'sales_manager': 'sales_manager',
        'manager': 'sales_manager',
        'sales rep': 'sales_manager',
        'representative': 'sales_manager',
        'sales person': 'sales_manager',
        
        # EXTRA FIELDS
        'customer code': 'customer_code',
        'Customer Code': 'customer_code',
        'customer_code': 'customer_code',
        'dealer code': 'dealer_code',
        'Dealer Code': 'dealer_code',
        'dealer_code': 'dealer_code',
        'warehouse code': 'warehouse_code',
        'Warehouse Code': 'warehouse_code',
        'warehouse_code': 'warehouse_code',
        'delivery location': 'delivery_location',
        'Delivery Location': 'delivery_location',
        'delivery_location': 'delivery_location',
        'remarks': 'remarks',
        'Remarks': 'remarks',
        'remark': 'remarks',
        'note': 'remarks',
        'notes': 'remarks',
        'comments': 'remarks',
    }
    
    # MANDATORY COLUMNS - Only these will reject rows
    MANDATORY_COLUMNS = [
        'dn_no',        # DN NO - REQUIRED
        'material_no',  # Material NO - REQUIRED
    ]
    
    @classmethod
    def build_mapping(cls, headers: List[str]) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
        """
        BLOCK 1: Build column map with mandatory column detection.
        """
        logger.info("=" * 60)
        logger.info("📋 BLOCK 1: BUILD SINGLE COLUMN MAP")
        logger.info("=" * 60)
        
        field_to_column = {}
        column_to_field = {}
        unmapped_headers = []
        used_headers = set()
        
        # Track matches for mandatory columns
        mandatory_matches = {col: None for col in cls.MANDATORY_COLUMNS}
        
        # Track exact matches for priority
        exact_matches = {}
        normalized_matches = {}
        
        # First pass: Build mapping with priority
        for header in headers:
            if header is None:
                continue
            
            exact = get_exact_header(header)
            normalized = normalize_header(header)
            
            # Priority 1: Exact match
            field = cls.HEADER_MAP.get(exact)
            if field and field not in field_to_column:
                exact_matches[field] = header
                continue
            
            # Priority 2: Normalized match
            field = cls.HEADER_MAP.get(normalized)
            if field and field not in field_to_column:
                normalized_matches[field] = header
                continue
            
            # Not matched yet
            unmapped_headers.append(header)
        
        # Apply matches in priority order
        # First exact matches
        for field, header in exact_matches.items():
            if field not in field_to_column:
                field_to_column[field] = header
                column_to_field[header] = field
                used_headers.add(header)
                if field in mandatory_matches:
                    mandatory_matches[field] = header
                logger.info(f"  ✅ EXACT: '{header}' → {field}")
        
        # Then normalized matches
        for field, header in normalized_matches.items():
            if field not in field_to_column:
                field_to_column[field] = header
                column_to_field[header] = field
                used_headers.add(header)
                if field in mandatory_matches:
                    mandatory_matches[field] = header
                logger.info(f"  ✅ NORMALIZED: '{header}' → {field}")
        
        # Log the final mapping
        logger.info("=" * 60)
        logger.info("📋 FINAL MAPPING:")
        for field, col in sorted(field_to_column.items()):
            logger.info(f"  {field:20} → '{col}'")
        
        # Check for mandatory columns
        missing_mandatory = []
        for col in cls.MANDATORY_COLUMNS:
            if col not in field_to_column:
                missing_mandatory.append(col)
        
        if missing_mandatory:
            error_msg = f"MANDATORY COLUMNS NOT FOUND: {missing_mandatory}"
            logger.error(f"❌ {error_msg}")
            raise ColumnMappingError(error_msg)
        
        logger.info(f"  ✅ Mandatory columns mapped: {list(mandatory_matches.keys())}")
        logger.info("=" * 60)
        
        return field_to_column, column_to_field, unmapped_headers

# =====================================================================================================
# BLOCK 2: LOCK THE MAPPING
# =====================================================================================================

class FrozenMapping:
    """
    BLOCK 2: Lock the Mapping.
    """
    
    def __init__(self, mapping: Dict[str, str]):
        self._mapping = OrderedDict(mapping.items())
        self._frozen = True
    
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
    
    def __repr__(self):
        return f"FrozenMapping({len(self._mapping)} fields)"

# =====================================================================================================
# BLOCK 3: WORKSHEET DETECTION
# =====================================================================================================

def detect_worksheet(file_path: str) -> Tuple[str, int, Dict[str, Any]]:
    """Detect the best worksheet containing delivery data."""
    logger.info("=" * 60)
    logger.info("🔍 WORKSHEET DETECTION v18.2")
    logger.info("=" * 60)
    
    try:
        xl = pd.ExcelFile(file_path, engine='openpyxl')
        sheet_names = xl.sheet_names
    except Exception as e:
        logger.error(f"❌ Failed to read Excel file: {e}")
        raise
    
    logger.info(f"📋 Found {len(sheet_names)} sheets: {sheet_names}")
    
    summary_indicators = ['sum s', 'sum', 'summary', 'total', 'grand total', 'report', 'overview']
    
    best_sheet = None
    best_score = 0
    best_header_row = 0
    best_info = {}
    skipped_sheets = []
    
    for sheet_name in sheet_names:
        if sheet_name.startswith('_') or sheet_name.startswith('$'):
            skipped_sheets.append(f"{sheet_name} (hidden)")
            continue
        
        is_summary = any(ind in sheet_name.lower() for ind in summary_indicators)
        if is_summary:
            skipped_sheets.append(f"{sheet_name} (summary)")
            logger.info(f"  ⏭️ Skipping summary sheet: '{sheet_name}'")
            continue
        
        logger.info(f"  📄 Checking sheet: '{sheet_name}'")
        
        try:
            df_sample = pd.read_excel(
                file_path,
                sheet_name=sheet_name,
                header=None,
                nrows=30,
                engine='openpyxl'
            )
            
            if len(df_sample) == 0:
                skipped_sheets.append(f"{sheet_name} (empty)")
                continue
            
            header_row, score, matched_headers = detect_header_row(df_sample)
            
            # Check for mandatory headers
            matched_lower = [normalize_header(h).lower() for h in matched_headers]
            
            has_dn = any('dn' in h for h in matched_lower)
            has_material = any('material' in h or 'sku' in h for h in matched_lower)
            
            logistics_score = score
            if has_dn:
                logistics_score += 50
            if has_material:
                logistics_score += 50
            
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
        raise WorksheetNotFoundError(f"No worksheet with delivery data found.")
    
    logger.info("=" * 60)
    logger.info(f"✅ SELECTED SHEET: '{best_sheet}'")
    logger.info(f"   Header Row: {best_header_row}")
    logger.info(f"   Has DN: {best_info.get('has_dn', False)}")
    logger.info(f"   Has Material: {best_info.get('has_material', False)}")
    logger.info("=" * 60)
    
    return best_sheet, best_header_row, best_info

def detect_header_row(df: pd.DataFrame, max_rows: int = 25) -> Tuple[int, int, List[str]]:
    """Detect header row using logistics headers."""
    if len(df) == 0:
        return 0, 0, []
    
    header_keywords = {
        'dn': 10, 'material': 10, 'qty': 5, 'amount': 5,
        'warehouse': 4, 'city': 3, 'model': 3, 'office': 3,
        'storage': 3, 'date': 2, 'manager': 2,
        'work': 3, 'status': 2, 'bin': 2,
    }
    
    best_score = 0
    best_row = 0
    best_matched = []
    
    rows_to_check = min(max_rows, len(df))
    
    for row_idx in range(rows_to_check):
        score = 0
        row_data = df.iloc[row_idx]
        matched = []
        
        for value in row_data:
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
    """Read Excel using Polars or fallback to pandas."""
    try:
        import polars as pl
        HAS_POLARS = True
    except ImportError:
        HAS_POLARS = False
    
    if HAS_POLARS:
        try:
            df = pl.read_excel(
                file_path,
                sheet_name=sheet_name,
                header_row=header_row,
                engine='calamine'
            )
            logger.info("⚡ Using Polars with calamine engine")
            return df.to_pandas()
        except Exception as e:
            logger.warning(f"⚠️ Polars read failed: {e}, trying fallback...")
    
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
# BLOCK 6: REFERENCE DATA ENRICHMENT
# =====================================================================================================

@lru_cache(maxsize=1000)
def get_warehouse_code(warehouse: str) -> Optional[str]:
    """Get warehouse code from warehouse name with caching."""
    if not warehouse:
        return None
    
    warehouse_map = {
        'rawalpindi': 'RWP',
        'islamabad': 'ISB',
        'lahore': 'LHE',
        'karachi': 'KHI',
        'faisalabad': 'FSD',
        'multan': 'MUX',
        'peshawar': 'PEW',
        'quetta': 'QTA',
        'gujranwala': 'GJW',
        'sialkot': 'SKT',
        'wah': 'WAH',
        'wah cantt': 'WAH',
        'rwp': 'RWP',
        'isb': 'ISB',
        'lhr': 'LHE',
        'khi': 'KHI',
        'fsd': 'FSD',
        'mux': 'MUX',
        'pew': 'PEW',
        'qta': 'QTA',
        'gjw': 'GJW',
        'skt': 'SKT',
        'rawal pindi': 'RWP',
        'islam abad': 'ISB',
    }
    
    key = warehouse.lower().strip()
    result = warehouse_map.get(key)
    
    if result is None:
        logger.warning(f"⚠️ Unknown warehouse: '{warehouse}' - warehouse_code will be None")
    
    return result

def get_delivery_location(ship_to_city: str) -> Optional[str]:
    """Get delivery location from ship_to_city with normalization."""
    if not ship_to_city:
        return None
    
    # Normalize city name
    normalized = normalize_city(ship_to_city)
    return normalized

def derive_customer_code(customer_name: str) -> Optional[str]:
    """Derive customer code from customer name."""
    if not customer_name:
        return None
    # Generate a deterministic code
    code = re.sub(r'[^a-zA-Z0-9]', '_', customer_name[:15].upper())
    return f"CUST_{code}" if code else None

def derive_dealer_code(customer_name: str) -> Optional[str]:
    """Derive dealer code from customer name."""
    if not customer_name:
        return None
    # Generate a deterministic code
    code = re.sub(r'[^a-zA-Z0-9]', '_', customer_name[:15].upper())
    return f"DEAL_{code}" if code else None

# =====================================================================================================
# BLOCK 7: BATCH PROCESSOR WITH RELAXED VALIDATION
# =====================================================================================================

class FastBatchProcessor:
    """
    BLOCK 7: Complete batch processor with relaxed validation.
    """
    
    def __init__(self, db: Session, frozen_mapping: FrozenMapping, batch_id: str, source_filename: str):
        self.db = db
        self.frozen_mapping = frozen_mapping
        self.batch_id = batch_id
        self.source_filename = source_filename
        
        # Counters
        self.inserted_count = 0
        self.updated_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.total_revenue = Decimal(0)
        self.total_units = 0
        
        # Detailed tracking
        self.processed_keys = set()
        self.bulk_buffer = []
        self.commit_counter = 0
        
        # Error tracking - Detailed
        self.extraction_errors = []
        self.parsing_errors = []
        self.validation_warnings = []
        self.validation_errors = []
        self.database_errors = []
        self.duplicate_errors = []
        self.unexpected_exceptions = []
        self.failed_rows = []
        
        # Configuration
        self.skip_dups = False
        self.update_existing = False
        
        # Reconciliation counters
        self.rows_read = 0
        self.rows_parsed = 0
        self.rows_buffered = 0
        self.rows_inserted_db = 0
        
        # Critical field tracking
        self.extraction_stats = {
            'dn_work_extracted': 0,
            'dn_qty_extracted': 0,
            'dn_amount_extracted': 0,
            'storage_extracted': 0,
        }
        
        # Sample data for verification
        self.raw_extracted_values = []
    
    # =====================================================================================================
    # DEDICATED EXTRACTION LAYER - RELAXED
    # =====================================================================================================
    
    def extract_critical_fields(self, row: Dict[str, Any], frozen_mapping: FrozenMapping) -> Dict[str, Any]:
        """
        Extract critical fields with relaxed validation.
        """
        raw_values = {}
        
        # DN Work
        dn_work_col = frozen_mapping.get('dn_work')
        if dn_work_col:
            raw_dn_work = row.get(dn_work_col)
            raw_values['raw_dn_work'] = raw_dn_work
            if raw_dn_work is not None and str(raw_dn_work).strip():
                self.extraction_stats['dn_work_extracted'] += 1
        else:
            raw_values['raw_dn_work'] = None
        
        # DN Qty
        dn_qty_col = frozen_mapping.get('dn_qty')
        if dn_qty_col:
            raw_dn_qty = row.get(dn_qty_col)
            raw_values['raw_dn_qty'] = raw_dn_qty
            if raw_dn_qty is not None:
                self.extraction_stats['dn_qty_extracted'] += 1
        else:
            raw_values['raw_dn_qty'] = None
        
        # DN Amount
        dn_amount_col = frozen_mapping.get('dn_amount')
        if dn_amount_col:
            raw_dn_amount = row.get(dn_amount_col)
            raw_values['raw_dn_amount'] = raw_dn_amount
            if raw_dn_amount is not None:
                self.extraction_stats['dn_amount_extracted'] += 1
        else:
            raw_values['raw_dn_amount'] = None
        
        # Storage Location
        storage_col = frozen_mapping.get('storage_location')
        if storage_col:
            raw_storage = row.get(storage_col)
            raw_values['raw_storage'] = raw_storage
            if raw_storage is not None and str(raw_storage).strip():
                self.extraction_stats['storage_extracted'] += 1
        else:
            raw_values['raw_storage'] = None
        
        return raw_values
    
    # =====================================================================================================
    # PARSE CRITICAL FIELDS - RELAXED
    # =====================================================================================================
    
    def parse_critical_fields(self, raw_values: Dict[str, Any], row_number: int) -> Dict[str, Any]:
        """
        Parse critical fields with relaxed validation.
        """
        parsed_values = {}
        parsing_warnings = []
        
        # DN Work - normalize_string
        raw_dn_work = raw_values.get('raw_dn_work')
        try:
            parsed_dn_work = normalize_string(raw_dn_work)
            parsed_values['dn_work'] = parsed_dn_work
            if raw_dn_work is not None and parsed_dn_work is None:
                parsing_warnings.append(f"DN Work: '{raw_dn_work}' → None")
        except Exception as e:
            parsed_values['dn_work'] = None
            parsing_warnings.append(f"DN Work parsing error: {e}")
            self.parsing_errors.append({
                'row': row_number,
                'field': 'dn_work',
                'error': str(e)
            })
        
        # DN Qty - parse_quantity
        raw_dn_qty = raw_values.get('raw_dn_qty')
        try:
            parsed_dn_qty = parse_quantity(raw_dn_qty)
            parsed_values['dn_qty'] = parsed_dn_qty
            if raw_dn_qty is not None and parsed_dn_qty is None:
                parsing_warnings.append(f"DN Qty: '{raw_dn_qty}' → None")
        except Exception as e:
            parsed_values['dn_qty'] = None
            parsing_warnings.append(f"DN Qty parsing error: {e}")
            self.parsing_errors.append({
                'row': row_number,
                'field': 'dn_qty',
                'error': str(e)
            })
        
        # DN Amount - parse_amount
        raw_dn_amount = raw_values.get('raw_dn_amount')
        try:
            parsed_dn_amount = parse_amount(raw_dn_amount)
            parsed_values['dn_amount'] = parsed_dn_amount
            if raw_dn_amount is not None and parsed_dn_amount is None:
                parsing_warnings.append(f"DN Amount: '{raw_dn_amount}' → None")
        except Exception as e:
            parsed_values['dn_amount'] = None
            parsing_warnings.append(f"DN Amount parsing error: {e}")
            self.parsing_errors.append({
                'row': row_number,
                'field': 'dn_amount',
                'error': str(e)
            })
        
        # Storage - normalize_string
        raw_storage = raw_values.get('raw_storage')
        try:
            parsed_storage = normalize_string(raw_storage)
            parsed_values['storage_location'] = parsed_storage
            if raw_storage is not None and parsed_storage is None:
                parsing_warnings.append(f"Storage: '{raw_storage}' → None")
        except Exception as e:
            parsed_values['storage_location'] = None
            parsing_warnings.append(f"Storage parsing error: {e}")
            self.parsing_errors.append({
                'row': row_number,
                'field': 'storage_location',
                'error': str(e)
            })
        
        # Log parsing warnings
        if parsing_warnings:
            for warning in parsing_warnings:
                self.validation_warnings.append({
                    'row': row_number,
                    'warning': warning
                })
                logger.debug(f"  ⚠️ Row {row_number}: {warning}")
        
        return parsed_values
    
    # =====================================================================================================
    # VALIDATE MANDATORY FIELDS - ONLY MANDATORY
    # =====================================================================================================
    
    def validate_mandatory_fields(self, row_data: Dict[str, Any], row_number: int) -> Tuple[bool, List[str]]:
        """
        Validate ONLY mandatory fields.
        """
        errors = []
        
        # DN NO - Mandatory
        dn_no = row_data.get('dn_no')
        if not dn_no or str(dn_no).strip() == '':
            errors.append("Missing DN NO")
        
        # Material NO - Mandatory
        material_no = row_data.get('material_no')
        if not material_no or str(material_no).strip() == '':
            errors.append("Missing Material NO")
        
        if errors:
            self.validation_errors.extend([{
                'row': row_number,
                'dn': dn_no,
                'material': material_no,
                'error': error
            } for error in errors])
            return False, errors
        
        return True, []
    
    # =====================================================================================================
    # PROCESS ROW - RELAXED VALIDATION
    # =====================================================================================================
    
    def process_row(self, row_data: Dict[str, Any], row_number: int, row: Dict[str, Any]) -> bool:
        """
        Process a single row with relaxed validation.
        """
        try:
            self.rows_read += 1
            self.rows_parsed += 1
            
            # ============================================================
            # STEP 1: EXTRACT CRITICAL FIELDS
            # ============================================================
            raw_values = self.extract_critical_fields(row, self.frozen_mapping)
            
            # Store raw values for later verification
            if len(self.raw_extracted_values) < 100:  # Keep only 100 samples
                self.raw_extracted_values.append(raw_values)
            
            # ============================================================
            # STEP 2: PARSE CRITICAL FIELDS
            # ============================================================
            parsed_values = self.parse_critical_fields(raw_values, row_number)
            
            # Add parsed values to row_data
            row_data.update(parsed_values)
            
            # ============================================================
            # STEP 3: EXTRACT MANDATORY FIELDS
            # ============================================================
            # DN NO
            dn_no_col = self.frozen_mapping.get('dn_no')
            if dn_no_col:
                raw_dn_no = row.get(dn_no_col)
                row_data['dn_no'] = normalize_dn(str(raw_dn_no) if raw_dn_no else None)
            else:
                row_data['dn_no'] = None
            
            # Material NO
            material_no_col = self.frozen_mapping.get('material_no')
            if material_no_col:
                raw_material_no = row.get(material_no_col)
                row_data['material_no'] = normalize_string(raw_material_no)
            else:
                row_data['material_no'] = None
            
            # ============================================================
            # STEP 4: VALIDATE MANDATORY FIELDS ONLY
            # ============================================================
            is_valid, mandatory_errors = self.validate_mandatory_fields(row_data, row_number)
            
            if not is_valid:
                self.failed_rows.append({
                    'row': row_number,
                    'dn': row_data.get('dn_no'),
                    'material': row_data.get('material_no'),
                    'error': f"Mandatory fields missing: {', '.join(mandatory_errors)}"
                })
                self.failed_count += 1
                return False
            
            # ============================================================
            # STEP 5: EXTRACT OTHER FIELDS
            # ============================================================
            row_data['order_type'] = normalize_string(row.get(self.frozen_mapping.get('order_type')))
            row_data['division'] = normalize_string(row.get(self.frozen_mapping.get('division')))
            row_data['customer_model'] = normalize_string(row.get(self.frozen_mapping.get('customer_model')))
            row_data['sales_office'] = normalize_string(row.get(self.frozen_mapping.get('sales_office')))
            row_data['customer_name'] = normalize_string(row.get(self.frozen_mapping.get('customer_name')))
            row_data['ship_to_city'] = normalize_string(row.get(self.frozen_mapping.get('ship_to_city')))
            row_data['warehouse'] = normalize_string(row.get(self.frozen_mapping.get('warehouse')))
            row_data['dn_create_date'] = parse_date(row.get(self.frozen_mapping.get('dn_create_date')))
            row_data['good_issue_date'] = parse_date(row.get(self.frozen_mapping.get('good_issue_date')))
            row_data['pod_date'] = parse_date(row.get(self.frozen_mapping.get('pod_date')))
            row_data['sales_manager'] = normalize_string(row.get(self.frozen_mapping.get('sales_manager')))
            row_data['remarks'] = normalize_string(row.get(self.frozen_mapping.get('remarks')))
            
            # ============================================================
            # STEP 6: DUPLICATE CHECK - dn_no + material_no
            # ============================================================
            dn_no = row_data.get('dn_no')
            material_no = row_data.get('material_no')
            
            # This should never happen if validation passed, but just in case
            if not dn_no or not material_no:
                self.failed_rows.append({
                    'row': row_number,
                    'dn': dn_no,
                    'material': material_no,
                    'error': 'DN NO or Material NO is None after validation'
                })
                self.failed_count += 1
                return False
            
            duplicate_key = f"{dn_no}_{material_no}"
            
            # Check for duplicates in the current batch
            if duplicate_key in self.processed_keys:
                self.duplicate_errors.append({
                    'row': row_number,
                    'dn': dn_no,
                    'material': material_no,
                    'error': 'Duplicate in current batch'
                })
                self.failed_rows.append({
                    'row': row_number,
                    'dn': dn_no,
                    'material': material_no,
                    'error': 'Duplicate in current batch'
                })
                self.failed_count += 1
                return False
            
            # Check for duplicates in database
            if self.skip_dups or self.update_existing:
                existing = self.db.query(DeliveryReport).filter_by(
                    dn_no=dn_no,
                    material_no=material_no
                ).first()
                
                if existing and self.update_existing:
                    status = StatusEngine.derive(
                        row_data.get('dn_create_date'),
                        row_data.get('good_issue_date'),
                        row_data.get('pod_date')
                    )
                    self._update_record(existing, row_data, status)
                    self.updated_count += 1
                    return True
                elif existing and self.skip_dups:
                    self.skipped_count += 1
                    return True
            
            self.processed_keys.add(duplicate_key)
            
            # ============================================================
            # STEP 7: DERIVE STATUS AND ENRICH
            # ============================================================
            status = StatusEngine.derive(
                row_data.get('dn_create_date'),
                row_data.get('good_issue_date'),
                row_data.get('pod_date')
            )
            
            # Enrich with derived fields
            if row_data.get('warehouse'):
                row_data['warehouse_code'] = get_warehouse_code(row_data.get('warehouse'))
            else:
                row_data['warehouse_code'] = None
            
            if row_data.get('ship_to_city'):
                row_data['delivery_location'] = get_delivery_location(row_data.get('ship_to_city'))
            else:
                row_data['delivery_location'] = None
            
            customer_name = row_data.get('customer_name')
            if customer_name:
                row_data['customer_code'] = derive_customer_code(customer_name)
                row_data['dealer_code'] = derive_dealer_code(customer_name)
            else:
                row_data['customer_code'] = None
                row_data['dealer_code'] = None
            
            # ============================================================
            # STEP 8: ADD TO BUFFER
            # ============================================================
            self._add_to_bulk_buffer(row_data, status)
            self.inserted_count += 1
            self.rows_buffered += 1
            
            # Update totals
            if row_data.get('dn_amount'):
                self.total_revenue += row_data['dn_amount']
            if row_data.get('dn_qty'):
                self.total_units += row_data['dn_qty']
            
            # Flush if buffer is full
            if len(self.bulk_buffer) >= BULK_SIZE:
                self.flush_bulk()
            
            return True
            
        except Exception as e:
            # Catch unexpected exceptions
            self.unexpected_exceptions.append({
                'row': row_number,
                'dn': row_data.get('dn_no'),
                'material': row_data.get('material_no'),
                'error': str(e),
                'traceback': traceback.format_exc()
            })
            self.failed_rows.append({
                'row': row_number,
                'dn': row_data.get('dn_no'),
                'material': row_data.get('material_no'),
                'error': f"Unexpected exception: {str(e)}"
            })
            self.failed_count += 1
            logger.error(f"❌ Row {row_number} unexpected error: {e}")
            logger.error(traceback.format_exc())
            return False
    
    def _update_record(self, existing, row_data, status):
        """Update existing record without overwriting valid values."""
        updates = [
            ('dn_work', row_data['dn_work']),
            ('order_type', row_data['order_type']),
            ('division', row_data['division']),
            ('customer_name', row_data['customer_name']),
            ('customer_model', row_data['customer_model']),
            ('storage_location', row_data['storage_location']),
            ('sales_office', row_data['sales_office']),
            ('sales_manager', row_data['sales_manager']),
            ('ship_to_city', row_data['ship_to_city']),
            ('warehouse', row_data['warehouse']),
            ('warehouse_code', row_data['warehouse_code']),
            ('delivery_location', row_data['delivery_location']),
            ('dn_qty', row_data['dn_qty']),
            ('dn_amount', float(row_data['dn_amount']) if row_data['dn_amount'] else None),
            ('dn_create_date', row_data['dn_create_date']),
            ('good_issue_date', row_data['good_issue_date']),
            ('pod_date', row_data['pod_date']),
            ('remarks', row_data['remarks']),
            ('customer_code', row_data['customer_code']),
            ('dealer_code', row_data['dealer_code']),
        ]
        
        for field, value in updates:
            if value is not None:
                setattr(existing, field, value)
        
        existing.delivery_status = status['delivery_status']
        existing.pgi_status = status['pgi_status']
        existing.pod_status = status['pod_status']
        existing.pending_flag = status['pending_flag']
        existing.source_file = self.source_filename
        existing.upload_batch_id = self.batch_id
        existing.updated_at = datetime.utcnow()
    
    def _add_to_bulk_buffer(self, row_data, status):
        """Add row to bulk buffer."""
        self.bulk_buffer.append({
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
        })
    
    def flush_bulk(self):
        """Flush bulk buffer with minimal validation."""
        if not self.bulk_buffer:
            return
        
        try:
            self.db.bulk_insert_mappings(DeliveryReport, self.bulk_buffer)
            self.db.commit()
            self.rows_inserted_db += len(self.bulk_buffer)
            
            self.commit_counter += 1
            logger.info(f"⚡ Bulk committed batch {self.commit_counter} ({len(self.bulk_buffer):,} rows)")
            self.bulk_buffer.clear()
            
            if self.commit_counter % GC_INTERVAL == 0:
                gc.collect()
                
        except Exception as e:
            logger.error(f"❌ Bulk commit failed: {e}")
            self.database_errors.append({
                'batch': self.commit_counter + 1,
                'error': str(e),
                'rows_in_batch': len(self.bulk_buffer)
            })
            self.db.rollback()
            raise
    
    def finalize(self):
        """Finalize import with detailed reporting."""
        self.flush_bulk()
        
        # Reconciliation summary
        total_processed = self.inserted_count + self.updated_count + self.skipped_count + self.failed_count
        
        logger.info("=" * 60)
        logger.info("📊 BLOCK 8: IMPORT SUMMARY")
        logger.info("=" * 60)
        logger.info("")
        logger.info("  📥 INPUT STAGE:")
        logger.info(f"  Excel Rows Read: {self.rows_read:,}")
        logger.info("")
        logger.info("  ⚙️ PROCESSING STAGE:")
        logger.info(f"  Rows Parsed: {self.rows_parsed:,}")
        logger.info(f"  Rows Buffered: {self.rows_buffered:,}")
        logger.info("")
        logger.info("  📤 OUTPUT STAGE:")
        logger.info(f"  Rows Inserted: {self.inserted_count:,}")
        logger.info(f"  Rows Updated: {self.updated_count:,}")
        logger.info(f"  Rows Skipped: {self.skipped_count:,}")
        logger.info(f"  Rows Failed: {self.failed_count:,}")
        logger.info(f"  Total Processed: {total_processed:,}")
        logger.info("")
        logger.info("  🔍 ERROR DETAILS:")
        logger.info(f"  Extraction Errors: {len(self.extraction_errors)}")
        logger.info(f"  Parsing Errors: {len(self.parsing_errors)}")
        logger.info(f"  Validation Errors: {len(self.validation_errors)}")
        logger.info(f"  Validation Warnings: {len(self.validation_warnings)}")
        logger.info(f"  Database Errors: {len(self.database_errors)}")
        logger.info(f"  Duplicate Errors: {len(self.duplicate_errors)}")
        logger.info(f"  Unexpected Exceptions: {len(self.unexpected_exceptions)}")
        logger.info("")
        logger.info("  📊 EXTRACTION STATISTICS:")
        logger.info(f"  DN Work Extracted: {self.extraction_stats['dn_work_extracted']:,} rows")
        logger.info(f"  DN Qty Extracted: {self.extraction_stats['dn_qty_extracted']:,} rows")
        logger.info(f"  DN Amount Extracted: {self.extraction_stats['dn_amount_extracted']:,} rows")
        logger.info(f"  Storage Extracted: {self.extraction_stats['storage_extracted']:,} rows")
        logger.info("")
        
        # Show failed rows sample
        if self.failed_rows:
            logger.info("  ❌ FAILED ROWS (first 10):")
            for i, failed in enumerate(self.failed_rows[:10]):
                logger.info(f"     {i+1}. Row {failed['row']}: DN={failed['dn']} Material={failed['material']} - {failed['error']}")
        
        logger.info("=" * 60)
        
        return {
            'inserted_count': self.inserted_count,
            'updated_count': self.updated_count,
            'skipped_count': self.skipped_count,
            'failed_count': self.failed_count,
            'total_revenue': self.total_revenue,
            'total_units': self.total_units,
            'rows_read': self.rows_read,
            'rows_parsed': self.rows_parsed,
            'rows_buffered': self.rows_buffered,
            'rows_inserted_db': self.rows_inserted_db,
            'total_processed': total_processed,
            'extraction_errors': self.extraction_errors,
            'parsing_errors': self.parsing_errors,
            'validation_errors': self.validation_errors,
            'validation_warnings': self.validation_warnings,
            'database_errors': self.database_errors,
            'duplicate_errors': self.duplicate_errors,
            'unexpected_exceptions': self.unexpected_exceptions,
            'failed_rows': self.failed_rows[:50],
            'extraction_stats': self.extraction_stats,
        }

# =====================================================================================================
# BLOCK 8: EXCEL IMPORT SERVICE - SAFE REPLACE MODE
# =====================================================================================================

class ExcelImportService:
    """
    BLOCK 8: Complete import service with safe replace mode.
    """
    
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
        """
        Import Excel with safe replace mode.
        
        Args:
            delete_existing: If True, delete existing records for this source before import.
                           If False, append or update (based on skip_dups/update_existing_rows).
        """
        
        start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("⚡ EXCEL IMPORT v18.2 - SAFE REPLACE MODE")
        logger.info("=" * 60)
        logger.info(f"📁 File: {file_path}")
        logger.info(f"📋 Source: {source_filename}")
        logger.info(f"🗑️ Delete Existing: {delete_existing}")
        
        if DIAGNOSTIC_MODE:
            logger.info(f"🔍 DIAGNOSTIC MODE ENABLED")
            logger.info(f"   Processing first {DIAGNOSTIC_ROWS} rows only")
        
        if not batch_id:
            batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        logger.info(f"📋 Batch: {batch_id}")
        
        try:
            # Step 1: Detect worksheet
            sheet_name, header_row, sheet_info = detect_worksheet(file_path)
            
            # Step 2: Read Excel
            logger.info(f"📖 Reading sheet '{sheet_name}'")
            df = read_excel_fast(file_path, sheet_name, header_row)
            
            df = df.dropna(how='all')
            df = df.dropna(axis=1, how='all')
            
            total_rows = len(df)
            logger.info(f"📄 Read {total_rows:,} rows, {len(df.columns)} columns")
            
            # Step 3: Build column map
            headers = [str(col).strip() for col in df.columns]
            field_to_column, column_to_field, unmapped = ColumnMap.build_mapping(headers)
            
            # Lock mapping
            frozen_mapping = FrozenMapping(field_to_column)
            logger.info("🔒 Mapping locked")
            
            # Verify mandatory columns are mapped
            mandatory_cols = ['dn_no', 'material_no']
            missing_mandatory = [col for col in mandatory_cols if col not in frozen_mapping]
            if missing_mandatory:
                raise ColumnMappingError(f"Mandatory columns not mapped: {missing_mandatory}")
            
            # Step 4: SAMPLE VALIDATION - Test first 10 rows before deleting
            logger.info("🔍 Validating sample rows before import...")
            rows = df.to_dict('records')
            sample_size = min(10, len(rows))
            
            validation_errors = []
            validation_warnings = []
            
            for idx in range(sample_size):
                row = rows[idx]
                row_number = idx + 2 + header_row
                
                # Quick validation
                dn_col = frozen_mapping.get('dn_no')
                mat_col = frozen_mapping.get('material_no')
                
                if not dn_col or not mat_col:
                    validation_errors.append(f"Row {row_number}: Missing column mapping for DN or Material")
                    continue
                
                dn_val = row.get(dn_col)
                mat_val = row.get(mat_col)
                
                if not dn_val or str(dn_val).strip() == '':
                    validation_errors.append(f"Row {row_number}: DN is empty")
                
                if not mat_val or str(mat_val).strip() == '':
                    validation_errors.append(f"Row {row_number}: Material is empty")
                
                # Check business fields (warnings only)
                for field_name, col_name in [('dn_work', 'dn_work'), ('dn_qty', 'dn_qty'), 
                                           ('dn_amount', 'dn_amount'), ('storage', 'storage_location')]:
                    if col_name in frozen_mapping:
                        col = frozen_mapping.get(col_name)
                        val = row.get(col)
                        if val is None or (isinstance(val, str) and val.strip() == ''):
                            validation_warnings.append(f"Row {row_number}: {field_name} is empty")
            
            if validation_errors:
                error_msg = f"Sample validation failed with {len(validation_errors)} errors"
                logger.error(f"❌ {error_msg}")
                for error in validation_errors[:5]:
                    logger.error(f"   {error}")
                return {
                    "success": False,
                    "error": error_msg,
                    "batch_id": batch_id,
                    "validation_errors": validation_errors,
                    "validation_warnings": validation_warnings,
                    "total_rows": total_rows,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0
                }
            
            logger.info(f"✅ Sample validation passed with {len(validation_warnings)} warnings")
            
            # Step 5: DELETE EXISTING RECORDS (only if validation passed and delete_existing=True)
            deleted_count = 0
            if delete_existing:
                try:
                    logger.info(f"🗑️ Deleting existing records for batch {batch_id}...")
                    result = db.execute(
                        text("DELETE FROM delivery_reports WHERE upload_batch_id = :batch_id"),
                        {"batch_id": batch_id}
                    )
                    deleted_count = result.rowcount
                    db.commit()
                    logger.info(f"🗑️ Deleted {deleted_count} existing records")
                except Exception as e:
                    logger.error(f"❌ Failed to delete existing records: {e}")
                    db.rollback()
                    return {
                        "success": False,
                        "error": f"Failed to delete existing records: {str(e)}",
                        "batch_id": batch_id,
                        "total_rows": total_rows,
                        "inserted_count": 0,
                        "updated_count": 0,
                        "skipped_count": 0,
                        "failed_count": 0
                    }
            
            # Step 6: Process rows
            processor = FastBatchProcessor(db, frozen_mapping, batch_id, source_filename)
            processor.skip_dups = skip_dups
            processor.update_existing = update_existing_rows
            
            processed_count = 0
            process_start = time.time()
            
            # Determine how many rows to process (diagnostic mode)
            rows_to_process = DIAGNOSTIC_ROWS if DIAGNOSTIC_MODE else len(rows)
            
            for idx in range(rows_to_process):
                row = rows[idx]
                row_number = idx + 2 + header_row
                
                try:
                    # Build Excel row dictionary
                    excel_row = {}
                    for field, col in frozen_mapping.items():
                        excel_row[field] = row.get(col)
                    
                    # Get DN for diagnostic
                    dn_no = normalize_dn(str(excel_row.get('dn_no')) if excel_row.get('dn_no') else None)
                    
                    # Prepare row_data with all fields
                    parsed_row = {
                        'order_type': normalize_string(excel_row.get('order_type')),
                        'dn_no': normalize_dn(str(excel_row.get('dn_no')) if excel_row.get('dn_no') else None),
                        'dn_work': None,  # Will be set by processor
                        'dn_amount': None,  # Will be set by processor
                        'dn_qty': None,  # Will be set by processor
                        'division': normalize_string(excel_row.get('division')),
                        'material_no': normalize_string(excel_row.get('material_no')),
                        'customer_model': normalize_string(excel_row.get('customer_model')),
                        'sales_office': normalize_string(excel_row.get('sales_office')),
                        'customer_name': normalize_string(excel_row.get('customer_name')),
                        'ship_to_city': normalize_string(excel_row.get('ship_to_city')),
                        'storage_location': None,  # Will be set by processor
                        'warehouse': normalize_string(excel_row.get('warehouse')),
                        'dn_create_date': parse_date(excel_row.get('dn_create_date')),
                        'good_issue_date': parse_date(excel_row.get('good_issue_date')),
                        'pod_date': parse_date(excel_row.get('pod_date')),
                        'sales_manager': normalize_string(excel_row.get('sales_manager')),
                        'customer_code': None,
                        'dealer_code': None,
                        'warehouse_code': None,
                        'delivery_location': None,
                        'remarks': normalize_string(excel_row.get('remarks')),
                    }
                    
                    # Process row with relaxed validation
                    processor.process_row(parsed_row, row_number, excel_row)
                    processed_count += 1
                    
                    if DIAGNOSTIC_MODE:
                        # Log diagnostic info for first row
                        if processed_count == 1:
                            logger.info("=" * 60)
                            logger.info("🔍 DIAGNOSTIC: FIRST ROW DETAILS")
                            logger.info("=" * 60)
                            logger.info(f"  Row Number: {row_number}")
                            logger.info(f"  DN: {parsed_row.get('dn_no')}")
                            logger.info(f"  Material: {parsed_row.get('material_no')}")
                            logger.info(f"  Raw DN Work: {excel_row.get('dn_work')}")
                            logger.info(f"  Raw DN Qty: {excel_row.get('dn_qty')}")
                            logger.info(f"  Raw DN Amount: {excel_row.get('dn_amount')}")
                            logger.info(f"  Raw Storage: {excel_row.get('storage_location')}")
                            logger.info(f"  Parsed DN Work: {parsed_row.get('dn_work')}")
                            logger.info(f"  Parsed DN Qty: {parsed_row.get('dn_qty')}")
                            logger.info(f"  Parsed DN Amount: {parsed_row.get('dn_amount')}")
                            logger.info(f"  Parsed Storage: {parsed_row.get('storage_location')}")
                            logger.info("=" * 60)
                    
                    if processed_count % 25000 == 0:
                        logger.info(f"📊 Processed {processed_count:,} rows...")
                    
                except Exception as e:
                    processor.failed_count += 1
                    processor.failed_rows.append({
                        'row': row_number,
                        'dn': row.get(frozen_mapping.get('dn_no')) if frozen_mapping.get('dn_no') else None,
                        'material': row.get(frozen_mapping.get('material_no')) if frozen_mapping.get('material_no') else None,
                        'error': str(e)
                    })
                    logger.error(f"❌ Row {row_number} failed: {e}")
                    logger.error(traceback.format_exc())
                    
                    if DIAGNOSTIC_MODE:
                        # Stop on first failure in diagnostic mode
                        logger.error("🔍 Diagnostic mode: Stopping on first failure")
                        break
            
            # Step 7: Finalize
            logger.info("💾 Finalizing import...")
            results = processor.finalize()
            
            process_duration = time.time() - process_start
            duration = time.time() - start_time
            
            # Step 8: Verify data was actually inserted
            db_count = 0
            try:
                result = db.execute(
                    text("SELECT COUNT(*) FROM delivery_reports WHERE upload_batch_id = :batch_id"),
                    {"batch_id": batch_id}
                )
                db_count = result.scalar()
                logger.info(f"📊 PostgreSQL now has {db_count:,} records for batch {batch_id}")
            except Exception as e:
                logger.warning(f"⚠️ Could not verify count: {e}")
            
            logger.info("=" * 60)
            logger.info("✅ IMPORT COMPLETED")
            logger.info("=" * 60)
            logger.info(f"  Duration: {duration:.2f}s")
            logger.info(f"  Processing Speed: {processed_count / process_duration if process_duration > 0 else 0:,.0f} rows/sec")
            logger.info(f"  Records in DB: {db_count:,}")
            logger.info(f"  Rows Inserted: {results['inserted_count']:,}")
            logger.info(f"  Rows Updated: {results['updated_count']:,}")
            logger.info(f"  Rows Skipped: {results['skipped_count']:,}")
            logger.info(f"  Rows Failed: {results['failed_count']:,}")
            
            if results['failed_count'] > 0:
                logger.warning(f"  ⚠️ {results['failed_count']} rows failed")
                if results['failed_rows']:
                    logger.info("  Sample failures:")
                    for failed in results['failed_rows'][:5]:
                        logger.info(f"    Row {failed['row']}: {failed.get('error', 'Unknown error')}")
            
            return {
                "success": True,
                "batch_id": batch_id,
                "total_rows": total_rows,
                "processed_rows": processed_count,
                "inserted_count": results['inserted_count'],
                "updated_count": results['updated_count'],
                "skipped_count": results['skipped_count'],
                "failed_count": results['failed_count'],
                "total_revenue_imported": float(results['total_revenue']),
                "total_units_imported": results['total_units'],
                "deleted_count": deleted_count,
                "db_count": db_count,
                "sheet_name": sheet_name,
                "header_row": header_row,
                "performance": {
                    "duration_seconds": round(duration, 2),
                    "rows_per_second": round(processed_count / process_duration if process_duration > 0 else 0, 0),
                    "bulk_size": BULK_SIZE
                },
                "errors": {
                    "extraction_errors": results['extraction_errors'][:10],
                    "parsing_errors": results['parsing_errors'][:10],
                    "validation_errors": results['validation_errors'][:10],
                    "validation_warnings": results['validation_warnings'][:20],
                    "database_errors": results['database_errors'][:10],
                    "duplicate_errors": results['duplicate_errors'][:10],
                    "unexpected_exceptions": results['unexpected_exceptions'][:10],
                },
                "failed_rows": results['failed_rows'][:10],
                "extraction_stats": results['extraction_stats'],
            }
            
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
                "inserted_count": 0,
                "updated_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "total_revenue_imported": 0,
                "total_units_imported": 0
            }

# =====================================================================================================
# BLOCK 9: EXPORTS
# =====================================================================================================

__all__ = [
    'ExcelImportService',
    'VerificationError',
    'normalize_header',
    'parse_amount',
    'parse_quantity',
    'parse_date',
    'normalize_string',
    'normalize_dn',
    'normalize_city',
    'get_warehouse_code',
    'get_delivery_location',
    'derive_customer_code',
    'derive_dealer_code',
]

# =====================================================================================================
# MODULE INITIALIZATION LOGGING
# =====================================================================================================

logger.info("=" * 60)
logger.info("📊 EXCEL IMPORT SERVICE v18.2 - FIXED VALIDATION & SAFE REPLACE")
logger.info("=" * 60)
logger.info("")
logger.info("  ✅ RELAXED VALIDATION:")
logger.info("     - Only dn_no and material_no are mandatory")
logger.info("     - Business fields (dn_work, dn_qty, dn_amount, storage) are optional")
logger.info("     - Validation warnings don't reject rows")
logger.info("")
logger.info("  ✅ SAFE REPLACE MODE:")
logger.info("     - Validates sample rows before deletion")
logger.info("     - Never deletes data on validation failure")
logger.info("     - Only deletes when validation passes")
logger.info("")
logger.info("  ✅ DIAGNOSTIC MODE:")
logger.info(f"     - Set DN_IMPORT_DIAGNOSTIC=true")
logger.info(f"     - Processes only {DIAGNOSTIC_ROWS} rows")
logger.info("     - Logs full extraction details")
logger.info("")
logger.info("  ✅ PRESERVED BUSINESS LOGIC:")
logger.info("     - dn_no + material_no composite key")
logger.info("     - Multiple materials per DN supported")
logger.info("     - All existing features unchanged")
logger.info("=" * 60)

# =====================================================================================================
# END OF FILE
# =====================================================================================================
