# =====================================================================================================
# FILE: app/services/excel_import_service.py
# VERSION: v18.6 - ATTRIBUTE ALIGNMENT FIX
# PURPOSE: Enterprise Excel import with all attributes properly aligned
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
import sys

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

class BulkInsertError(ImportError):
    """Raised when bulk insert fails with details about offending row."""
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
    BLOCK 1: Build a Single Column Map.
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
    logger.info("🔍 WORKSHEET DETECTION v18.6")
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
    
    normalized = normalize_city(ship_to_city)
    return normalized

def derive_customer_code(customer_name: str) -> Optional[str]:
    """Derive customer code from customer name."""
    if not customer_name:
        return None
    code = re.sub(r'[^a-zA-Z0-9]', '_', customer_name[:15].upper())
    return f"CUST_{code}" if code else None

def derive_dealer_code(customer_name: str) -> Optional[str]:
    """Derive dealer code from customer name."""
    if not customer_name:
        return None
    code = re.sub(r'[^a-zA-Z0-9]', '_', customer_name[:15].upper())
    return f"DEAL_{code}" if code else None

# =====================================================================================================
# BLOCK 7: BATCH PROCESSOR WITH ALL ATTRIBUTES ALIGNED
# =====================================================================================================

class FastBatchProcessor:
    """
    BLOCK 7: Complete batch processor with all attributes properly aligned.
    """
    
    def __init__(self, db: Session, frozen_mapping: FrozenMapping, batch_id: str, source_filename: str):
        self.db = db
        self.frozen_mapping = frozen_mapping
        self.batch_id = batch_id
        self.source_filename = source_filename
        
        # ============================================================
        # STAGE COUNTERS - Using consistent names
        # ============================================================
        self.rows_read = 0
        self.rows_extracted = 0
        self.rows_parsed = 0
        self.rows_validated = 0
        self.rows_buffered = 0
        self.rows_insert_attempted = 0
        self.rows_inserted = 0
        self.rows_failed = 0
        self.rows_updated = 0
        self.rows_skipped = 0
        
        # Legacy compatibility - these are used by some code
        self.inserted_count = 0  # Alias for rows_inserted
        self.updated_count = 0   # Alias for rows_updated
        self.skipped_count = 0   # Alias for rows_skipped
        self.failed_count = 0    # Alias for rows_failed
        
        # ============================================================
        # STATISTICS
        # ============================================================
        self.total_revenue = Decimal(0)
        self.total_units = 0
        
        # ============================================================
        # TRACKING
        # ============================================================
        self.processed_keys = set()
        self.duplicate_tracker = {}  # key -> row_number
        self.bulk_buffer = []
        self.commit_counter = 0
        
        # ============================================================
        # FAILURE STAGE TRACKING
        # ============================================================
        self.failure_stages = {
            'EXTRACTION': 0,
            'PARSING': 0,
            'VALIDATION': 0,
            'DUPLICATE_CHECK': 0,
            'BUFFER_CREATION': 0,
            'STATISTICS': 0,
            'DATABASE_INSERT': 0,
            'COMMIT': 0,
        }
        
        # ============================================================
        # FIRST FAILED ROW
        # ============================================================
        self.first_failed_row = None
        
        # ============================================================
        # ERROR TRACKING
        # ============================================================
        self.extraction_errors = []
        self.parsing_errors = []
        self.validation_warnings = []
        self.validation_errors = []
        self.database_errors = []
        self.duplicate_errors = []
        self.unexpected_exceptions = []
        self.failed_rows = []
        self.statistics_errors = []
        
        # ============================================================
        # EXTRACTION STATISTICS
        # ============================================================
        self.extraction_stats = {
            'dn_work_extracted': 0,
            'dn_qty_extracted': 0,
            'dn_amount_extracted': 0,
            'storage_extracted': 0,
        }
        
        # ============================================================
        # SAMPLES
        # ============================================================
        self.excel_samples = []
        self.parsed_samples = []
        self.buffer_samples = []
        self.comparison_log = []
        self.db_insert_sample = None
        
        # ============================================================
        # CONFIGURATION
        # ============================================================
        self.skip_dups = False
        self.update_existing = False
    
    # =====================================================================================================
    # EXTRACTION LAYER
    # =====================================================================================================
    
    def extract_critical_fields(self, row: Dict[str, Any], row_number: int) -> Dict[str, Any]:
        """Extract critical fields with detailed tracking."""
        raw_values = {
            'row_number': row_number,
            'raw_dn_work': None,
            'raw_dn_qty': None,
            'raw_dn_amount': None,
            'raw_storage': None,
        }
        
        # DN Work
        dn_work_col = self.frozen_mapping.get('dn_work')
        if dn_work_col:
            raw_dn_work = row.get(dn_work_col)
            raw_values['raw_dn_work'] = raw_dn_work
            if raw_dn_work is not None and str(raw_dn_work).strip():
                self.extraction_stats['dn_work_extracted'] += 1
        else:
            self.extraction_errors.append({
                'row': row_number,
                'field': 'dn_work',
                'error': 'Column not found in mapping'
            })
        
        # DN Qty
        dn_qty_col = self.frozen_mapping.get('dn_qty')
        if dn_qty_col:
            raw_dn_qty = row.get(dn_qty_col)
            raw_values['raw_dn_qty'] = raw_dn_qty
            if raw_dn_qty is not None:
                self.extraction_stats['dn_qty_extracted'] += 1
        else:
            self.extraction_errors.append({
                'row': row_number,
                'field': 'dn_qty',
                'error': 'Column not found in mapping'
            })
        
        # DN Amount
        dn_amount_col = self.frozen_mapping.get('dn_amount')
        if dn_amount_col:
            raw_dn_amount = row.get(dn_amount_col)
            raw_values['raw_dn_amount'] = raw_dn_amount
            if raw_dn_amount is not None:
                self.extraction_stats['dn_amount_extracted'] += 1
        else:
            self.extraction_errors.append({
                'row': row_number,
                'field': 'dn_amount',
                'error': 'Column not found in mapping'
            })
        
        # Storage Location
        storage_col = self.frozen_mapping.get('storage_location')
        if storage_col:
            raw_storage = row.get(storage_col)
            raw_values['raw_storage'] = raw_storage
            if raw_storage is not None and str(raw_storage).strip():
                self.extraction_stats['storage_extracted'] += 1
        else:
            self.extraction_errors.append({
                'row': row_number,
                'field': 'storage_location',
                'error': 'Column not found in mapping'
            })
        
        return raw_values
    
    # =====================================================================================================
    # PARSE CRITICAL FIELDS
    # =====================================================================================================
    
    def parse_critical_fields(self, raw_values: Dict[str, Any]) -> Dict[str, Any]:
        """Parse critical fields with detailed tracking."""
        parsed_values = {
            'dn_work': None,
            'dn_qty': None,
            'dn_amount': None,
            'storage_location': None,
        }
        
        row_number = raw_values.get('row_number', 0)
        
        # DN Work
        raw_dn_work = raw_values.get('raw_dn_work')
        try:
            parsed_dn_work = normalize_string(raw_dn_work)
            parsed_values['dn_work'] = parsed_dn_work
            if raw_dn_work is not None and parsed_dn_work is None:
                self.parsing_errors.append({
                    'row': row_number,
                    'field': 'dn_work',
                    'raw': raw_dn_work,
                    'parsed': parsed_dn_work,
                    'error': 'Normalization returned None'
                })
        except Exception as e:
            parsed_values['dn_work'] = None
            self.parsing_errors.append({
                'row': row_number,
                'field': 'dn_work',
                'raw': raw_dn_work,
                'parsed': None,
                'error': str(e)
            })
        
        # DN Qty
        raw_dn_qty = raw_values.get('raw_dn_qty')
        try:
            parsed_dn_qty = parse_quantity(raw_dn_qty)
            parsed_values['dn_qty'] = parsed_dn_qty
            if raw_dn_qty is not None and parsed_dn_qty is None:
                self.parsing_errors.append({
                    'row': row_number,
                    'field': 'dn_qty',
                    'raw': raw_dn_qty,
                    'parsed': parsed_dn_qty,
                    'error': 'Parsing returned None'
                })
        except Exception as e:
            parsed_values['dn_qty'] = None
            self.parsing_errors.append({
                'row': row_number,
                'field': 'dn_qty',
                'raw': raw_dn_qty,
                'parsed': None,
                'error': str(e)
            })
        
        # DN Amount
        raw_dn_amount = raw_values.get('raw_dn_amount')
        try:
            parsed_dn_amount = parse_amount(raw_dn_amount)
            parsed_values['dn_amount'] = parsed_dn_amount
            if raw_dn_amount is not None and parsed_dn_amount is None:
                self.parsing_errors.append({
                    'row': row_number,
                    'field': 'dn_amount',
                    'raw': raw_dn_amount,
                    'parsed': parsed_dn_amount,
                    'error': 'Parsing returned None'
                })
        except Exception as e:
            parsed_values['dn_amount'] = None
            self.parsing_errors.append({
                'row': row_number,
                'field': 'dn_amount',
                'raw': raw_dn_amount,
                'parsed': None,
                'error': str(e)
            })
        
        # Storage
        raw_storage = raw_values.get('raw_storage')
        try:
            parsed_storage = normalize_string(raw_storage)
            parsed_values['storage_location'] = parsed_storage
            if raw_storage is not None and parsed_storage is None:
                self.parsing_errors.append({
                    'row': row_number,
                    'field': 'storage_location',
                    'raw': raw_storage,
                    'parsed': parsed_storage,
                    'error': 'Normalization returned None'
                })
        except Exception as e:
            parsed_values['storage_location'] = None
            self.parsing_errors.append({
                'row': row_number,
                'field': 'storage_location',
                'raw': raw_storage,
                'parsed': None,
                'error': str(e)
            })
        
        return parsed_values
    
    # =====================================================================================================
    # COMPARE STAGES
    # =====================================================================================================
    
    def compare_stages(self, raw_values: Dict[str, Any], parsed_values: Dict[str, Any], 
                       row_number: int, dn_no: str, material_no: str):
        """Compare raw extracted values against parsed values."""
        comparisons = []
        
        fields = [
            ('DN Work', 'raw_dn_work', 'dn_work'),
            ('DN Qty', 'raw_dn_qty', 'dn_qty'),
            ('DN Amount', 'raw_dn_amount', 'dn_amount'),
            ('Storage', 'raw_storage', 'storage_location'),
        ]
        
        for display_name, raw_key, parsed_key in fields:
            raw_val = raw_values.get(raw_key)
            parsed_val = parsed_values.get(parsed_key)
            
            if raw_val is not None and parsed_val is None:
                comparisons.append({
                    'field': display_name,
                    'raw': raw_val,
                    'parsed': parsed_val,
                    'status': '⚠️ VALUE LOST',
                    'detail': f"Raw: '{raw_val}' → Parsed: None"
                })
            elif raw_val is not None and parsed_val is not None:
                if isinstance(raw_val, str) and isinstance(parsed_val, str):
                    if raw_val.strip().lower() != parsed_val.strip().lower():
                        comparisons.append({
                            'field': display_name,
                            'raw': raw_val,
                            'parsed': parsed_val,
                            'status': '⚠️ VALUE CHANGED',
                            'detail': f"Raw: '{raw_val}' → Parsed: '{parsed_val}'"
                        })
                    else:
                        comparisons.append({
                            'field': display_name,
                            'raw': raw_val,
                            'parsed': parsed_val,
                            'status': '✅',
                            'detail': f"Raw: '{raw_val}' → Parsed: '{parsed_val}'"
                        })
                else:
                    if str(raw_val).strip() != str(parsed_val).strip():
                        comparisons.append({
                            'field': display_name,
                            'raw': raw_val,
                            'parsed': parsed_val,
                            'status': '⚠️ VALUE CHANGED',
                            'detail': f"Raw: '{raw_val}' → Parsed: '{parsed_val}'"
                        })
                    else:
                        comparisons.append({
                            'field': display_name,
                            'raw': raw_val,
                            'parsed': parsed_val,
                            'status': '✅',
                            'detail': f"Raw: '{raw_val}' → Parsed: '{parsed_val}'"
                        })
            else:
                comparisons.append({
                    'field': display_name,
                    'raw': raw_val,
                    'parsed': parsed_val,
                    'status': 'ℹ️',
                    'detail': f"Raw: None (optional field)"
                })
        
        if DIAGNOSTIC_MODE:
            logger.info("=" * 60)
            logger.info(f"🔍 COMPARISON: Row {row_number}, DN={dn_no}, Material={material_no}")
            for comp in comparisons:
                logger.info(f"  {comp['status']} {comp['field']}: {comp['detail']}")
            logger.info("=" * 60)
        
        self.comparison_log.append({
            'row': row_number,
            'dn_no': dn_no,
            'material_no': material_no,
            'comparisons': comparisons
        })
        
        return comparisons
    
    # =====================================================================================================
    # VALIDATE MANDATORY FIELDS
    # =====================================================================================================
    
    def validate_mandatory_fields(self, row_data: Dict[str, Any], row_number: int) -> Tuple[bool, List[str]]:
        """Validate ONLY mandatory fields."""
        errors = []
        
        dn_no = row_data.get('dn_no')
        if not dn_no or str(dn_no).strip() == '':
            errors.append("Missing DN NO")
        
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
    # UPDATE STATISTICS - ISOLATED, NEVER FAILS
    # =====================================================================================================
    
    def update_statistics(self, row_data: Dict[str, Any], row_number: int) -> None:
        """
        Update statistics - isolated, never fails a row.
        """
        try:
            if row_data.get('dn_amount'):
                self.total_revenue += row_data['dn_amount']
            if row_data.get('dn_qty'):
                self.total_units += row_data['dn_qty']
        except Exception as e:
            # Statistics failure should NEVER fail a row
            self.statistics_errors.append({
                'row': row_number,
                'error': str(e)
            })
            logger.warning(f"⚠️ Statistics update failed for row {row_number}: {e}")
    
    # =====================================================================================================
    # PROCESS ROW - WITH ISOLATED STAGES
    # =====================================================================================================
    
    def process_row(self, row_data: Dict[str, Any], row_number: int, row: Dict[str, Any]) -> bool:
        """
        Process a single row with isolated stages.
        """
        try:
            self.rows_read += 1
            
            # ============================================================
            # STAGE 1: EXTRACTION
            # ============================================================
            raw_values = self.extract_critical_fields(row, row_number)
            self.rows_extracted += 1
            
            # Store raw values for verification
            if len(self.excel_samples) < 100:
                self.excel_samples.append(raw_values.copy())
            
            # ============================================================
            # STAGE 2: PARSING
            # ============================================================
            parsed_values = self.parse_critical_fields(raw_values)
            self.rows_parsed += 1
            
            # Add parsed values to row_data
            row_data.update(parsed_values)
            
            # Store parsed values for verification
            if len(self.parsed_samples) < 100:
                self.parsed_samples.append({
                    'row': row_number,
                    'dn_work': parsed_values['dn_work'],
                    'dn_qty': parsed_values['dn_qty'],
                    'dn_amount': parsed_values['dn_amount'],
                    'storage_location': parsed_values['storage_location'],
                })
            
            # ============================================================
            # STAGE 3: EXTRACT MANDATORY FIELDS
            # ============================================================
            dn_no_col = self.frozen_mapping.get('dn_no')
            if dn_no_col:
                raw_dn_no = row.get(dn_no_col)
                row_data['dn_no'] = normalize_dn(str(raw_dn_no) if raw_dn_no else None)
            else:
                row_data['dn_no'] = None
            
            material_no_col = self.frozen_mapping.get('material_no')
            if material_no_col:
                raw_material_no = row.get(material_no_col)
                row_data['material_no'] = normalize_string(raw_material_no)
            else:
                row_data['material_no'] = None
            
            # ============================================================
            # STAGE 4: COMPARE STAGES
            # ============================================================
            dn_no = row_data.get('dn_no')
            material_no = row_data.get('material_no')
            self.compare_stages(raw_values, parsed_values, row_number, dn_no, material_no)
            
            # ============================================================
            # STAGE 5: VALIDATION
            # ============================================================
            is_valid, mandatory_errors = self.validate_mandatory_fields(row_data, row_number)
            
            if not is_valid:
                self.rows_failed += 1
                self.failed_count += 1  # Alias
                self.failure_stages['VALIDATION'] += 1
                
                # Store first failed row
                if self.first_failed_row is None:
                    self.first_failed_row = {
                        'row': row_number,
                        'dn': dn_no,
                        'material': material_no,
                        'stage': 'VALIDATION',
                        'raw_values': raw_values.copy(),
                        'parsed_values': parsed_values.copy(),
                        'errors': mandatory_errors,
                        'comparisons': self.comparison_log[-1] if self.comparison_log else [],
                        'exception_type': None,
                        'exception_message': None,
                        'stack_trace': None
                    }
                
                self.failed_rows.append({
                    'row': row_number,
                    'dn': dn_no,
                    'material': material_no,
                    'stage': 'VALIDATION',
                    'error': f"Mandatory fields missing: {', '.join(mandatory_errors)}"
                })
                return False
            
            self.rows_validated += 1
            
            # ============================================================
            # STAGE 6: EXTRACT OTHER FIELDS
            # ============================================================
            try:
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
            except Exception as e:
                # Field extraction failed - log but continue
                logger.warning(f"⚠️ Row {row_number}: Field extraction error: {e}")
                # Continue processing - we have the mandatory fields
            
            # ============================================================
            # STAGE 7: DUPLICATE CHECK
            # ============================================================
            dn_no = row_data.get('dn_no')
            material_no = row_data.get('material_no')
            
            if not dn_no or not material_no:
                self.rows_failed += 1
                self.failed_count += 1  # Alias
                self.failure_stages['VALIDATION'] += 1
                
                if self.first_failed_row is None:
                    self.first_failed_row = {
                        'row': row_number,
                        'dn': dn_no,
                        'material': material_no,
                        'stage': 'VALIDATION',
                        'raw_values': raw_values.copy(),
                        'parsed_values': parsed_values.copy(),
                        'errors': ['DN NO or Material NO missing after validation'],
                        'comparisons': self.comparison_log[-1] if self.comparison_log else [],
                        'exception_type': None,
                        'exception_message': None,
                        'stack_trace': None
                    }
                
                self.failed_rows.append({
                    'row': row_number,
                    'dn': dn_no,
                    'material': material_no,
                    'stage': 'VALIDATION',
                    'error': 'DN NO or Material NO is None after validation'
                })
                return False
            
            duplicate_key = f"{dn_no}_{material_no}"
            
            if duplicate_key in self.processed_keys:
                previous_row = self.duplicate_tracker.get(duplicate_key)
                duplicate_info = {
                    'row': row_number,
                    'dn': dn_no,
                    'material': material_no,
                    'previous_row': previous_row
                }
                self.duplicate_errors.append(duplicate_info)
                self.rows_failed += 1
                self.failed_count += 1  # Alias
                self.failure_stages['DUPLICATE_CHECK'] += 1
                
                if self.first_failed_row is None:
                    self.first_failed_row = {
                        'row': row_number,
                        'dn': dn_no,
                        'material': material_no,
                        'stage': 'DUPLICATE_CHECK',
                        'raw_values': raw_values.copy(),
                        'parsed_values': parsed_values.copy(),
                        'errors': [f'Duplicate key: {duplicate_key} (previous row: {previous_row})'],
                        'comparisons': self.comparison_log[-1] if self.comparison_log else [],
                        'exception_type': None,
                        'exception_message': None,
                        'stack_trace': None
                    }
                
                self.failed_rows.append({
                    'row': row_number,
                    'dn': dn_no,
                    'material': material_no,
                    'stage': 'DUPLICATE_CHECK',
                    'error': f'Duplicate in current batch (previous row: {previous_row})'
                })
                return False
            
            self.processed_keys.add(duplicate_key)
            self.duplicate_tracker[duplicate_key] = row_number
            
            # ============================================================
            # STAGE 8: DERIVE STATUS AND ENRICH
            # ============================================================
            try:
                status = StatusEngine.derive(
                    row_data.get('dn_create_date'),
                    row_data.get('good_issue_date'),
                    row_data.get('pod_date')
                )
                
                row_data['warehouse_code'] = get_warehouse_code(row_data.get('warehouse'))
                row_data['delivery_location'] = get_delivery_location(row_data.get('ship_to_city'))
                
                customer_name = row_data.get('customer_name')
                if customer_name:
                    row_data['customer_code'] = derive_customer_code(customer_name)
                    row_data['dealer_code'] = derive_dealer_code(customer_name)
                else:
                    row_data['customer_code'] = None
                    row_data['dealer_code'] = None
            except Exception as e:
                # Enrichment failed - log but continue
                logger.warning(f"⚠️ Row {row_number}: Enrichment error: {e}")
                # Use default status
                status = {'delivery_status': 'Pending', 'pgi_status': 'Pending', 'pod_status': 'Pending', 'pending_flag': True}
                row_data['warehouse_code'] = None
                row_data['delivery_location'] = None
                row_data['customer_code'] = None
                row_data['dealer_code'] = None
            
            # ============================================================
            # STAGE 9: BUFFER CREATION
            # ============================================================
            try:
                buffer_record = self._prepare_buffer_record(row_data, status)
                self.bulk_buffer.append(buffer_record)
                self.rows_buffered += 1
                
                # Store buffer sample for verification
                if len(self.buffer_samples) < 100:
                    self.buffer_samples.append({
                        'row': row_number,
                        'dn_no': buffer_record['dn_no'],
                        'material_no': buffer_record['material_no'],
                        'dn_work': buffer_record['dn_work'],
                        'dn_qty': buffer_record['dn_qty'],
                        'dn_amount': buffer_record['dn_amount'],
                        'storage_location': buffer_record['storage_location'],
                    })
            except Exception as e:
                self.rows_failed += 1
                self.failed_count += 1  # Alias
                self.failure_stages['BUFFER_CREATION'] += 1
                
                if self.first_failed_row is None:
                    self.first_failed_row = {
                        'row': row_number,
                        'dn': dn_no,
                        'material': material_no,
                        'stage': 'BUFFER_CREATION',
                        'raw_values': raw_values.copy(),
                        'parsed_values': parsed_values.copy(),
                        'errors': [str(e)],
                        'comparisons': self.comparison_log[-1] if self.comparison_log else [],
                        'exception_type': type(e).__name__,
                        'exception_message': str(e),
                        'stack_trace': traceback.format_exc()
                    }
                
                self.failed_rows.append({
                    'row': row_number,
                    'dn': dn_no,
                    'material': material_no,
                    'stage': 'BUFFER_CREATION',
                    'error': str(e)
                })
                return False
            
            # ============================================================
            # STAGE 10: STATISTICS - ISOLATED, NEVER FAILS
            # ============================================================
            self.update_statistics(row_data, row_number)
            
            # Update inserted count alias - will be finalized later
            return True
            
        except Exception as e:
            # Catch unexpected exceptions
            self.rows_failed += 1
            self.failed_count += 1  # Alias
            self.failure_stages['EXTRACTION'] += 1  # Default stage
            
            if self.first_failed_row is None:
                self.first_failed_row = {
                    'row': row_number,
                    'dn': row_data.get('dn_no') if row_data else None,
                    'material': row_data.get('material_no') if row_data else None,
                    'stage': 'UNKNOWN',
                    'raw_values': raw_values if 'raw_values' in locals() else {},
                    'parsed_values': parsed_values if 'parsed_values' in locals() else {},
                    'errors': [str(e)],
                    'comparisons': self.comparison_log[-1] if self.comparison_log else [],
                    'exception_type': type(e).__name__,
                    'exception_message': str(e),
                    'stack_trace': traceback.format_exc()
                }
            
            self.unexpected_exceptions.append({
                'row': row_number,
                'dn': row_data.get('dn_no') if row_data else None,
                'material': row_data.get('material_no') if row_data else None,
                'error': str(e),
                'traceback': traceback.format_exc()
            })
            
            self.failed_rows.append({
                'row': row_number,
                'dn': row_data.get('dn_no') if row_data else None,
                'material': row_data.get('material_no') if row_data else None,
                'stage': 'EXTRACTION',
                'error': f"Unexpected exception: {str(e)}"
            })
            
            logger.error(f"❌ Row {row_number} unexpected error: {e}")
            logger.error(traceback.format_exc())
            return False
    
    def _prepare_buffer_record(self, row_data: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare a record for the bulk buffer."""
        return {
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
    
    # =====================================================================================================
    # FLUSH BULK WITH COMPREHENSIVE DIAGNOSTICS
    # =====================================================================================================
    
    def flush_bulk(self, is_final: bool = False):
        """
        Flush bulk buffer with comprehensive diagnostics.
        """
        if not self.bulk_buffer:
            logger.warning("⚠️ flush_bulk called with empty buffer")
            return
        
        self.rows_insert_attempted += len(self.bulk_buffer)
        
        # Log buffer details
        logger.info("=" * 60)
        logger.info(f"📊 FLUSH BULK - Batch {self.commit_counter + 1}")
        logger.info(f"  Buffer Size: {len(self.bulk_buffer):,}")
        
        if self.bulk_buffer:
            first_record = self.bulk_buffer[0]
            last_record = self.bulk_buffer[-1]
            logger.info(f"  First Record: DN={first_record.get('dn_no')}, Material={first_record.get('material_no')}")
            logger.info(f"  Last Record: DN={last_record.get('dn_no')}, Material={last_record.get('material_no')}")
            
            # Log first record details
            logger.info("  First Record Details:")
            logger.info(f"    DN Work: {first_record.get('dn_work')}")
            logger.info(f"    DN Qty: {first_record.get('dn_qty')}")
            logger.info(f"    DN Amount: {first_record.get('dn_amount')}")
            logger.info(f"    Storage: {first_record.get('storage_location')}")
            
            self.db_insert_sample = {
                'first': first_record,
                'last': last_record,
                'sample': self.bulk_buffer[0] if self.bulk_buffer else None
            }
        logger.info("=" * 60)
        
        # Execute bulk insert with error handling
        try:
            self.db.bulk_insert_mappings(DeliveryReport, self.bulk_buffer)
            self.db.commit()
            
            batch_size = len(self.bulk_buffer)
            self.rows_inserted += batch_size
            self.inserted_count += batch_size  # Alias
            self.rows_insert_attempted -= batch_size  # Remove successful ones from attempted
            
            self.commit_counter += 1
            logger.info(f"✅ Bulk committed batch {self.commit_counter} ({batch_size:,} rows)")
            self.bulk_buffer.clear()
            
            if self.commit_counter % GC_INTERVAL == 0:
                gc.collect()
                
        except Exception as e:
            self.failure_stages['DATABASE_INSERT'] += 1
            self.rows_failed += len(self.bulk_buffer)
            self.failed_count += len(self.bulk_buffer)  # Alias
            
            # Identify the offending record
            offending_record = None
            if self.bulk_buffer:
                for idx, record in enumerate(self.bulk_buffer):
                    try:
                        # Attempt to validate this record individually
                        test_record = [record]
                        self.db.bulk_insert_mappings(DeliveryReport, test_record)
                        self.db.rollback()
                    except Exception as record_error:
                        offending_record = {
                            'index': idx,
                            'dn_no': record.get('dn_no'),
                            'material_no': record.get('material_no'),
                            'dn_work': record.get('dn_work'),
                            'dn_qty': record.get('dn_qty'),
                            'dn_amount': record.get('dn_amount'),
                            'storage_location': record.get('storage_location'),
                            'error': str(record_error)
                        }
                        break
            
            error_msg = f"Bulk insert failed"
            if offending_record:
                error_msg += f" - Offending record at index {offending_record['index']}: DN={offending_record['dn_no']}, Material={offending_record['material_no']}"
                logger.error(f"❌ {error_msg}")
                logger.error(f"   Record details:")
                logger.error(f"   DN Work: {offending_record['dn_work']}")
                logger.error(f"   DN Qty: {offending_record['dn_qty']}")
                logger.error(f"   DN Amount: {offending_record['dn_amount']}")
                logger.error(f"   Storage: {offending_record['storage_location']}")
                logger.error(f"   SQLAlchemy Error: {offending_record['error']}")
                
                self.database_errors.append({
                    'batch': self.commit_counter + 1,
                    'error': str(e),
                    'offending_record': offending_record,
                    'rows_in_batch': len(self.bulk_buffer)
                })
                
                # Set first failed row if not already set
                if self.first_failed_row is None:
                    self.first_failed_row = {
                        'row': offending_record.get('row', 0),
                        'dn': offending_record.get('dn_no'),
                        'material': offending_record.get('material_no'),
                        'stage': 'DATABASE_INSERT',
                        'raw_values': None,
                        'parsed_values': {
                            'dn_work': offending_record.get('dn_work'),
                            'dn_qty': offending_record.get('dn_qty'),
                            'dn_amount': offending_record.get('dn_amount'),
                            'storage_location': offending_record.get('storage_location'),
                        },
                        'errors': [str(e)],
                        'comparisons': [],
                        'exception_type': type(e).__name__,
                        'exception_message': str(e),
                        'stack_trace': traceback.format_exc()
                    }
            else:
                logger.error(f"❌ {error_msg}: {e}")
                self.database_errors.append({
                    'batch': self.commit_counter + 1,
                    'error': str(e),
                    'rows_in_batch': len(self.bulk_buffer)
                })
            
            self.bulk_buffer.clear()
            raise BulkInsertError(error_msg) from e
    
    # =====================================================================================================
    # EXECUTE TRANSACTION - SAFE REPLACE MODE
    # =====================================================================================================
    
    def execute_transaction(self, delete_existing: bool = False) -> Dict[str, Any]:
        """
        Execute the full transaction with rollback on failure.
        """
        logger.info("=" * 60)
        logger.info("🔄 EXECUTING TRANSACTION")
        logger.info("=" * 60)
        
        try:
            # Flush any remaining buffer
            if self.bulk_buffer:
                self.flush_bulk(is_final=True)
            
            # Delete existing records if requested (within transaction)
            deleted_count = 0
            if delete_existing:
                logger.info(f"🗑️ Deleting existing records for batch {self.batch_id}...")
                result = self.db.execute(
                    text("DELETE FROM delivery_reports WHERE upload_batch_id = :batch_id"),
                    {"batch_id": self.batch_id}
                )
                deleted_count = result.rowcount
                logger.info(f"🗑️ Deleted {deleted_count} existing records")
            
            self.db.commit()
            logger.info("✅ Transaction committed successfully")
            
            return {'success': True, 'deleted_count': deleted_count}
            
        except BulkInsertError:
            # Already logged, re-raise
            raise
        except Exception as e:
            self.failure_stages['COMMIT'] += 1
            
            logger.error(f"❌ Transaction failed: {e}")
            self.db.rollback()
            
            if self.first_failed_row is None:
                self.first_failed_row = {
                    'row': 0,
                    'dn': None,
                    'material': None,
                    'stage': 'COMMIT',
                    'raw_values': None,
                    'parsed_values': None,
                    'errors': [str(e)],
                    'comparisons': [],
                    'exception_type': type(e).__name__,
                    'exception_message': str(e),
                    'stack_trace': traceback.format_exc()
                }
            
            raise
    
    # =====================================================================================================
    # POST INSERT VERIFICATION
    # =====================================================================================================
    
    def verify_post_insert(self) -> Dict[str, Any]:
        """
        Verify inserted records against Excel samples.
        """
        verification_results = {
            'rows_verified': 0,
            'mismatches': [],
            'dn_work_verified': 0,
            'dn_qty_verified': 0,
            'dn_amount_verified': 0,
            'storage_verified': 0,
        }
        
        if not self.inserted_count or not self.buffer_samples:
            return verification_results
        
        try:
            sample_size = min(10, len(self.buffer_samples))
            sample_buffer = self.buffer_samples[:sample_size]
            
            for sample in sample_buffer:
                dn = sample.get('dn_no')
                material = sample.get('material_no')
                
                if not dn or not material:
                    continue
                
                try:
                    # Query the record from PostgreSQL
                    result = self.db.query(DeliveryReport).filter_by(
                        dn_no=dn,
                        material_no=material
                    ).first()
                    
                    if not result:
                        verification_results['mismatches'].append({
                            'dn': dn,
                            'material': material,
                            'error': 'Record not found in PostgreSQL'
                        })
                        continue
                    
                    verification_results['rows_verified'] += 1
                    
                    # Compare each field
                    fields_to_compare = [
                        ('dn_work', sample.get('dn_work'), result.dn_work),
                        ('dn_qty', sample.get('dn_qty'), result.dn_qty),
                        ('dn_amount', sample.get('dn_amount'), result.dn_amount),
                        ('storage_location', sample.get('storage_location'), result.storage_location),
                    ]
                    
                    for field, expected, actual in fields_to_compare:
                        if expected is None:
                            continue
                        
                        if field == 'dn_qty':
                            if expected != actual:
                                verification_results['mismatches'].append({
                                    'dn': dn,
                                    'material': material,
                                    'field': field,
                                    'expected': expected,
                                    'actual': actual
                                })
                            else:
                                verification_results['dn_qty_verified'] += 1
                        
                        elif field == 'dn_amount':
                            expected_dec = Decimal(str(expected)) if expected is not None else None
                            actual_dec = Decimal(str(actual)) if actual is not None else None
                            if expected_dec is None or actual_dec is None or expected_dec != actual_dec:
                                verification_results['mismatches'].append({
                                    'dn': dn,
                                    'material': material,
                                    'field': field,
                                    'expected': expected,
                                    'actual': actual
                                })
                            else:
                                verification_results['dn_amount_verified'] += 1
                        
                        else:
                            if str(expected).strip().lower() != str(actual or '').strip().lower():
                                verification_results['mismatches'].append({
                                    'dn': dn,
                                    'material': material,
                                    'field': field,
                                    'expected': expected,
                                    'actual': actual
                                })
                            else:
                                if field == 'dn_work':
                                    verification_results['dn_work_verified'] += 1
                                elif field == 'storage_location':
                                    verification_results['storage_verified'] += 1
                
                except Exception as e:
                    verification_results['mismatches'].append({
                        'dn': dn,
                        'material': material,
                        'error': f'Verification query failed: {str(e)}'
                    })
            
        except Exception as e:
            logger.error(f"❌ Post-insert verification failed: {e}")
            verification_results['error'] = str(e)
        
        return verification_results
    
    # =====================================================================================================
    # FINALIZE WITH COMPREHENSIVE REPORTING
    # =====================================================================================================
    
    def finalize(self, delete_existing: bool = False) -> Dict[str, Any]:
        """
        Finalize import with comprehensive reporting.
        """
        # Execute the transaction
        transaction_result = self.execute_transaction(delete_existing)
        
        # Post-insert verification
        verification_results = self.verify_post_insert()
        
        # Calculate final statistics
        total_processed = self.rows_read
        
        # Log summary
        logger.info("=" * 60)
        logger.info("📊 FINAL IMPORT SUMMARY")
        logger.info("=" * 60)
        logger.info("")
        logger.info("  📥 INPUT STAGE:")
        logger.info(f"  Rows Read: {self.rows_read:,}")
        logger.info("")
        logger.info("  ⚙️ PROCESSING STAGE:")
        logger.info(f"  Rows Extracted: {self.rows_extracted:,}")
        logger.info(f"  Rows Parsed: {self.rows_parsed:,}")
        logger.info(f"  Rows Validated: {self.rows_validated:,}")
        logger.info(f"  Rows Buffered: {self.rows_buffered:,}")
        logger.info("")
        logger.info("  📤 OUTPUT STAGE:")
        logger.info(f"  Rows Insert Attempted: {self.rows_insert_attempted:,}")
        logger.info(f"  Rows Inserted: {self.rows_inserted:,}")
        logger.info(f"  Rows Updated: {self.rows_updated:,}")
        logger.info(f"  Rows Skipped: {self.rows_skipped:,}")
        logger.info(f"  Rows Failed: {self.rows_failed:,}")
        logger.info("")
        logger.info("  📊 EXTRACTION STATISTICS:")
        logger.info(f"  DN Work Extracted: {self.extraction_stats['dn_work_extracted']:,} / {self.rows_read:,}")
        logger.info(f"  DN Qty Extracted: {self.extraction_stats['dn_qty_extracted']:,} / {self.rows_read:,}")
        logger.info(f"  DN Amount Extracted: {self.extraction_stats['dn_amount_extracted']:,} / {self.rows_read:,}")
        logger.info(f"  Storage Extracted: {self.extraction_stats['storage_extracted']:,} / {self.rows_read:,}")
        logger.info("")
        logger.info("  🔍 FAILURE STAGE COUNTS:")
        for stage, count in self.failure_stages.items():
            if count > 0:
                logger.info(f"  {stage}: {count:,}")
        logger.info("")
        logger.info("  📊 TOTAL STATISTICS:")
        logger.info(f"  Total Revenue: {self.total_revenue}")
        logger.info(f"  Total Units: {self.total_units}")
        
        if self.first_failed_row:
            logger.info("")
            logger.info("  🔴 FIRST FAILED ROW:")
            logger.info(f"     Row: {self.first_failed_row.get('row')}")
            logger.info(f"     DN: {self.first_failed_row.get('dn')}")
            logger.info(f"     Material: {self.first_failed_row.get('material')}")
            logger.info(f"     Stage: {self.first_failed_row.get('stage')}")
            logger.info(f"     Errors: {self.first_failed_row.get('errors', [])}")
        
        if verification_results.get('mismatches'):
            logger.info("")
            logger.error("  ❌ POST-INSERT VERIFICATION MISMATCHES:")
            for mismatch in verification_results['mismatches'][:5]:
                logger.error(f"     {mismatch}")
        
        logger.info("=" * 60)
        
        return {
            'rows_read': self.rows_read,
            'rows_extracted': self.rows_extracted,
            'rows_parsed': self.rows_parsed,
            'rows_validated': self.rows_validated,
            'rows_buffered': self.rows_buffered,
            'rows_insert_attempted': self.rows_insert_attempted,
            'rows_inserted': self.rows_inserted,
            'rows_updated': self.rows_updated,
            'rows_skipped': self.rows_skipped,
            'rows_failed': self.rows_failed,
            'inserted_count': self.inserted_count,  # Legacy compatibility
            'updated_count': self.updated_count,    # Legacy compatibility
            'skipped_count': self.skipped_count,    # Legacy compatibility
            'failed_count': self.failed_count,      # Legacy compatibility
            'total_revenue': float(self.total_revenue),
            'total_units': self.total_units,
            'deleted_count': transaction_result.get('deleted_count', 0),
            'failure_stage_counts': self.failure_stages,
            'extraction_stats': self.extraction_stats,
            'first_failed_row': self.first_failed_row,
            'failed_rows': self.failed_rows[:10],
            'duplicate_errors': self.duplicate_errors[:20],
            'database_errors': self.database_errors[:10],
            'validation_errors': self.validation_errors[:10],
            'parsing_errors': self.parsing_errors[:10],
            'unexpected_exceptions': self.unexpected_exceptions[:10],
            'statistics_errors': self.statistics_errors[:10],
            'verification': verification_results,
        }

# =====================================================================================================
# BLOCK 8: EXCEL IMPORT SERVICE
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
        
        Workflow:
        1. Read Excel
        2. Validate Headers
        3. Process ALL Rows
        4. Buffer ALL Records
        5. Validate Buffer
        6. BEGIN TRANSACTION
        7. Delete Existing Records (if delete_existing)
        8. Insert New Records
        9. Commit
        
        If any step fails: Rollback, don't delete existing data.
        """
        
        start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("⚡ EXCEL IMPORT v18.6 - ATTRIBUTE ALIGNMENT FIX")
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
            # ============================================================
            # STEP 1: DETECT WORKSHEET
            # ============================================================
            sheet_name, header_row, sheet_info = detect_worksheet(file_path)
            
            # ============================================================
            # STEP 2: READ EXCEL
            # ============================================================
            logger.info(f"📖 Reading sheet '{sheet_name}'")
            df = read_excel_fast(file_path, sheet_name, header_row)
            
            df = df.dropna(how='all')
            df = df.dropna(axis=1, how='all')
            
            total_rows = len(df)
            logger.info(f"📄 Read {total_rows:,} rows, {len(df.columns)} columns")
            
            # ============================================================
            # STEP 3: BUILD COLUMN MAP
            # ============================================================
            headers = [str(col).strip() for col in df.columns]
            field_to_column, column_to_field, unmapped = ColumnMap.build_mapping(headers)
            
            frozen_mapping = FrozenMapping(field_to_column)
            logger.info("🔒 Mapping locked")
            
            # Verify mandatory columns are mapped
            mandatory_cols = ['dn_no', 'material_no']
            missing_mandatory = [col for col in mandatory_cols if col not in frozen_mapping]
            if missing_mandatory:
                raise ColumnMappingError(f"Mandatory columns not mapped: {missing_mandatory}")
            
            # ============================================================
            # STEP 4: SAMPLE VALIDATION - Test before processing
            # ============================================================
            logger.info("🔍 Validating sample rows before import...")
            rows = df.to_dict('records')
            sample_size = min(10, len(rows))
            
            validation_errors = []
            
            for idx in range(sample_size):
                row = rows[idx]
                row_number = idx + 2 + header_row
                
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
                    "total_rows": total_rows,
                }
            
            logger.info(f"✅ Sample validation passed")
            
            # ============================================================
            # STEP 5: PROCESS ALL ROWS
            # ============================================================
            processor = FastBatchProcessor(db, frozen_mapping, batch_id, source_filename)
            processor.skip_dups = skip_dups
            processor.update_existing = update_existing_rows
            
            processed_count = 0
            process_start = time.time()
            
            rows_to_process = DIAGNOSTIC_ROWS if DIAGNOSTIC_MODE else len(rows)
            
            for idx in range(rows_to_process):
                row = rows[idx]
                row_number = idx + 2 + header_row
                
                try:
                    # Build Excel row dictionary
                    excel_row = {}
                    for field, col in frozen_mapping.items():
                        excel_row[field] = row.get(col)
                    
                    # Prepare row_data
                    parsed_row = {
                        'order_type': normalize_string(excel_row.get('order_type')),
                        'dn_no': normalize_dn(str(excel_row.get('dn_no')) if excel_row.get('dn_no') else None),
                        'dn_work': None,
                        'dn_amount': None,
                        'dn_qty': None,
                        'division': normalize_string(excel_row.get('division')),
                        'material_no': normalize_string(excel_row.get('material_no')),
                        'customer_model': normalize_string(excel_row.get('customer_model')),
                        'sales_office': normalize_string(excel_row.get('sales_office')),
                        'customer_name': normalize_string(excel_row.get('customer_name')),
                        'ship_to_city': normalize_string(excel_row.get('ship_to_city')),
                        'storage_location': None,
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
                    
                    # Process row
                    processor.process_row(parsed_row, row_number, excel_row)
                    processed_count += 1
                    
                    if DIAGNOSTIC_MODE and processed_count == 1:
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
                    processor.rows_failed += 1
                    processor.failed_count += 1
                    processor.failed_rows.append({
                        'row': row_number,
                        'dn': row.get(frozen_mapping.get('dn_no')) if frozen_mapping.get('dn_no') else None,
                        'material': row.get(frozen_mapping.get('material_no')) if frozen_mapping.get('material_no') else None,
                        'stage': 'PROCESSING',
                        'error': str(e)
                    })
                    logger.error(f"❌ Row {row_number} failed: {e}")
                    logger.error(traceback.format_exc())
                    
                    if DIAGNOSTIC_MODE:
                        logger.error("🔍 Diagnostic mode: Stopping on first failure")
                        break
            
            process_duration = time.time() - process_start
            
            # ============================================================
            # STEP 6: FINALIZE AND EXECUTE TRANSACTION
            # ============================================================
            logger.info("💾 Finalizing import and executing transaction...")
            results = processor.finalize(delete_existing=delete_existing)
            
            duration = time.time() - start_time
            
            # ============================================================
            # STEP 7: RETURN RESULTS
            # ============================================================
            logger.info("=" * 60)
            logger.info("✅ IMPORT COMPLETED")
            logger.info("=" * 60)
            logger.info(f"  Duration: {duration:.2f}s")
            logger.info(f"  Rows Read: {results['rows_read']:,}")
            logger.info(f"  Rows Inserted: {results['rows_inserted']:,}")
            logger.info(f"  Rows Failed: {results['rows_failed']:,}")
            
            # Build response - use all available data
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
                "rows_updated": results['rows_updated'],
                "rows_skipped": results['rows_skipped'],
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
                    "rows_per_second": round(processed_count / process_duration if process_duration > 0 else 0, 0),
                    "bulk_size": BULK_SIZE
                },
                "failure_stage_counts": results['failure_stage_counts'],
                "extraction_stats": results['extraction_stats'],
                "first_failed_row": results['first_failed_row'],
                "failed_rows": results['failed_rows'][:10],
                "duplicate_errors": results['duplicate_errors'][:20],
                "errors": {
                    "database_errors": results['database_errors'][:10],
                    "validation_errors": results['validation_errors'][:10],
                    "parsing_errors": results['parsing_errors'][:10],
                    "unexpected_exceptions": results['unexpected_exceptions'][:10],
                    "statistics_errors": results['statistics_errors'][:10],
                },
                "verification": results['verification'],
            }
            
            # Ensure we never return errors=[] when rows_failed > 0
            if response['rows_failed'] > 0:
                if not response['first_failed_row'] and not response['failed_rows']:
                    # This should never happen, but just in case
                    response['first_failed_row'] = {
                        'row': 0,
                        'dn': None,
                        'material': None,
                        'stage': 'UNKNOWN',
                        'errors': ['Rows failed but no failure details captured'],
                    }
            
            return response
            
        except BulkInsertError as e:
            logger.error(f"❌ Bulk insert failed: {e}")
            logger.error(traceback.format_exc())
            db.rollback()
            
            return {
                "success": False,
                "error": str(e),
                "batch_id": batch_id,
                "total_rows": 0,
                "rows_read": 0,
                "rows_inserted": 0,
                "rows_failed": 0,
                "deleted_count": 0,
                "rollback": True,
                "first_failed_row": getattr(e, 'first_failed_row', None)
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
                "rows_read": 0,
                "rows_inserted": 0,
                "rows_failed": 0,
                "deleted_count": 0,
                "rollback": True
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
logger.info("📊 EXCEL IMPORT SERVICE v18.6 - ATTRIBUTE ALIGNMENT FIX")
logger.info("=" * 60)
logger.info("")
logger.info("  ✅ ALL ATTRIBUTES ALIGNED:")
logger.info("     - inserted_count (alias for rows_inserted)")
logger.info("     - updated_count (alias for rows_updated)")
logger.info("     - skipped_count (alias for rows_skipped)")
logger.info("     - failed_count (alias for rows_failed)")
logger.info("")
logger.info("  ✅ STATISTICS ISOLATED - Never fails a row")
logger.info("  ✅ SAFE REPLACE MODE - Transaction-safe")
logger.info("  ✅ COMPREHENSIVE API RESPONSE")
logger.info("=" * 60)

# =====================================================================================================
# END OF FILE
# =====================================================================================================
