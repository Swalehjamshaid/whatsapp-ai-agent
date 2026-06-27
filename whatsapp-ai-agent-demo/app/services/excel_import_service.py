# =====================================================================================================
# FILE: app/services/excel_import_service.py
# VERSION: v8.0 - ENTERPRISE WITH LOOKUP ENRICHMENT
# PURPOSE: Enterprise Excel import with worksheet detection, lookup enrichment, and data integrity
# COMPATIBLE WITH: upload.py v4.2
# =====================================================================================================

import pandas as pd
import logging
import uuid
import re
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, Any, Optional, List, Tuple, Set
from sqlalchemy.orm import Session
import time
import traceback
from functools import lru_cache

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# =====================================================================================================
# CONFIGURATION
# =====================================================================================================

BATCH_SIZE = 1000
HEADER_SCAN_ROWS = 25
STRICT_MODE = True
ATOMIC_COMMIT = False  # False = batch commits, True = single commit at end

# =====================================================================================================
# EXCEPTIONS
# =====================================================================================================

class ImportError(Exception):
    """Base import error"""
    pass

class HeaderNotFoundError(ImportError):
    """Header row could not be detected"""
    pass

class WorksheetNotFoundError(ImportError):
    """No valid worksheet found"""
    pass

class ValidationError(ImportError):
    """Required columns missing"""
    pass

class DataIntegrityError(ImportError):
    """Data integrity check failed"""
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
    for sep in ['_', '-', '.', '/', '\\', '#', '·', '•', '•']:
        normalized = normalized.replace(sep, ' ')
    
    # Replace non-breaking spaces
    normalized = normalized.replace('\u00a0', ' ')
    normalized = normalized.replace('\t', ' ')
    normalized = normalized.replace('\r', ' ')
    normalized = normalized.replace('\n', ' ')
    
    # Remove extra spaces and lowercase
    normalized = ' '.join(normalized.split()).lower()
    
    return normalized

# =====================================================================================================
# WORKSHEET DETECTION
# =====================================================================================================

def detect_worksheet(file_path: str) -> Tuple[str, int, Dict[str, Any]]:
    """
    Find the worksheet containing delivery data.
    
    Returns:
        (sheet_name, header_row, sheet_info)
    """
    logger.info("=" * 60)
    logger.info("🔍 WORKSHEET DETECTION")
    logger.info("=" * 60)
    
    # Read all sheet names
    xl = pd.ExcelFile(file_path)
    sheet_names = xl.sheet_names
    logger.info(f"📋 Found {len(sheet_names)} sheets: {sheet_names}")
    
    # Summary indicators to ignore
    summary_indicators = ['summary', 'sum s', 'sum', 'total', 'grand total', 'report', 
                          'overview', 'cover', 'index', 'contents', 'toc']
    
    # Required headers for a valid worksheet
    required_headers = ['dn no', 'material no']
    recommended_headers = ['order type', 'customer model', 'warehouse', 'ship to city']
    
    best_sheet = None
    best_score = 0
    best_header_row = 0
    best_info = {}
    
    for sheet_name in sheet_names:
        # Skip hidden sheets
        if sheet_name.startswith('_') or sheet_name.startswith('$'):
            logger.info(f"  ⏭️ Skipping hidden sheet: '{sheet_name}'")
            continue
        
        # Skip summary sheets
        if any(ind in sheet_name.lower() for ind in summary_indicators):
            logger.info(f"  ⏭️ Skipping summary sheet: '{sheet_name}'")
            continue
        
        logger.info(f"  📄 Checking sheet: '{sheet_name}'")
        
        try:
            # Read first few rows to detect headers
            df_sample = pd.read_excel(file_path, sheet_name=sheet_name, header=None, nrows=HEADER_SCAN_ROWS + 5)
            
            if len(df_sample) == 0:
                logger.info(f"    ⚠️ Sheet is empty, skipping")
                continue
            
            # Detect header row in this sheet
            header_row, score, matched_headers = detect_header_row_with_details(df_sample)
            
            logger.info(f"    📊 Header score: {score}")
            logger.info(f"    📋 Matched headers: {matched_headers[:5] if matched_headers else 'None'}")
            
            # Check required headers
            has_dn = any('dn no' == normalize_header(h) for h in matched_headers)
            has_material = any('material no' == normalize_header(h) for h in matched_headers)
            
            if has_dn and has_material:
                logger.info(f"    ✅ Found required headers (DN and Material)")
                score += 20
            
            # Check recommended headers
            recommended_count = 0
            for rec in recommended_headers:
                if any(rec in normalize_header(h) for h in matched_headers):
                    recommended_count += 1
            if recommended_count >= 3:
                logger.info(f"    ✅ Found {recommended_count} recommended headers")
                score += 10
            
            if score > best_score:
                best_score = score
                best_sheet = sheet_name
                best_header_row = header_row
                best_info = {
                    'score': score,
                    'matched_headers': matched_headers,
                    'rows': len(df_sample),
                    'has_dn': has_dn,
                    'has_material': has_material,
                    'recommended_count': recommended_count
                }
                logger.info(f"    ✅ New best sheet: '{sheet_name}' (score: {score})")
                
        except Exception as e:
            logger.warning(f"    ❌ Error reading sheet '{sheet_name}': {e}")
            continue
    
    if best_sheet is None or best_score < 3:
        logger.error("❌ No valid worksheet found")
        raise WorksheetNotFoundError(f"No worksheet with delivery data found. Available sheets: {sheet_names}")
    
    logger.info("=" * 60)
    logger.info(f"✅ SELECTED SHEET: '{best_sheet}'")
    logger.info(f"   Score: {best_score}")
    logger.info(f"   Header Row: {best_header_row}")
    logger.info(f"   Has DN: {best_info.get('has_dn', False)}")
    logger.info(f"   Has Material: {best_info.get('has_material', False)}")
    logger.info(f"   Matched Headers: {best_info.get('matched_headers', [])[:10]}")
    logger.info("=" * 60)
    
    return best_sheet, best_header_row, best_info

# =====================================================================================================
# HEADER DETECTION WITH DETAILS
# =====================================================================================================

def detect_header_row_with_details(df: pd.DataFrame, max_rows: int = HEADER_SCAN_ROWS) -> Tuple[int, int, List[str]]:
    """
    Detect which row contains column headers and return details.
    
    Returns:
        (header_row_index, score, matched_headers)
    """
    if len(df) == 0:
        return 0, 0, []
    
    # Keywords that indicate a header row with weights
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
        'remark': 1,
        'pgi': 1,
        'pod': 1,
        'delivery': 1
    }
    
    best_score = 0
    best_row = 0
    best_matched = []
    
    rows_to_check = min(max_rows, len(df))
    ignored_rows = []
    
    for row_idx in range(rows_to_check):
        score = 0
        row_data = df.iloc[row_idx]
        matched_keywords = set()
        matched_headers = []
        
        for value in row_data:
            if value is None or not isinstance(value, str):
                continue
            
            normalized = normalize_header(value)
            if not normalized:
                continue
            
            for keyword, weight in header_keywords.items():
                if keyword in normalized and keyword not in matched_keywords:
                    matched_keywords.add(keyword)
                    matched_headers.append(str(value))
                    score += weight
        
        if score > best_score:
            best_score = score
            best_row = row_idx
            best_matched = matched_headers
        elif score == 0 and row_idx < 5:
            ignored_rows.append(row_idx)
    
    # Log ignored rows
    if ignored_rows:
        logger.info(f"    Ignored rows (empty or no headers): {ignored_rows}")
    
    # If score is too low, check for explicit matches
    if best_score < 3:
        for row_idx in range(rows_to_check):
            row_data = df.iloc[row_idx]
            for value in row_data:
                if value and isinstance(value, str):
                    normalized = normalize_header(value)
                    if normalized in ['dn no', 'material no', 'dn', 'material']:
                        logger.info(f"    Found explicit match at row {row_idx}: '{value}'")
                        return row_idx, 5, [str(value)]
    
    logger.info(f"    Selected row: {best_row} (score: {best_score})")
    logger.info(f"    Matched headers: {best_matched[:5] if best_matched else 'None'}")
    
    return best_row, best_score, best_matched

# =====================================================================================================
# COLUMN MAPPER - ENTERPRISE
# =====================================================================================================

class ColumnMapper:
    """Map normalized Excel headers to database fields"""
    
    HEADER_MAP = {
        # DN - Required
        'dn no': 'dn_no',
        'dn': 'dn_no',
        'delivery note': 'dn_no',
        'delivery note no': 'dn_no',
        'delivery note number': 'dn_no',
        'delivery number': 'dn_no',
        'dn number': 'dn_no',
        'd n no': 'dn_no',
        'd n': 'dn_no',
        'delivery note #': 'dn_no',
        'dn#': 'dn_no',
        
        # Material - Required
        'material no': 'material_no',
        'material': 'material_no',
        'material number': 'material_no',
        'material code': 'material_no',
        'material#': 'material_no',
        'sku': 'material_no',
        'product no': 'material_no',
        'product number': 'material_no',
        'item no': 'material_no',
        'item': 'material_no',
        'part no': 'material_no',
        'part number': 'material_no',
        
        # Order Type - Recommended
        'order type': 'order_type',
        'order': 'order_type',
        'ordertype': 'order_type',
        'type': 'order_type',
        'order no': 'order_type',
        'order number': 'order_type',
        'sales order': 'order_type',
        'so': 'order_type',
        
        # DN Work - Recommended
        'dn work': 'dn_work',
        'work': 'dn_work',
        'work order': 'dn_work',
        'work no': 'dn_work',
        'work number': 'dn_work',
        'job': 'dn_work',
        
        # Division - Recommended
        'division': 'division',
        'div': 'division',
        'department': 'division',
        'business unit': 'division',
        
        # Customer Model - Recommended
        'customer model': 'customer_model',
        'model': 'customer_model',
        'model name': 'customer_model',
        'product model': 'customer_model',
        'model no': 'customer_model',
        'model number': 'customer_model',
        'product': 'customer_model',
        'item description': 'customer_model',
        
        # Customer Name - Recommended
        'sold to party name': 'customer_name',
        'sold-to-party name': 'customer_name',
        'sold to party': 'customer_name',
        'sold-to-party': 'customer_name',
        'customer name': 'customer_name',
        'customer': 'customer_name',
        'dealer name': 'customer_name',
        'party name': 'customer_name',
        'client name': 'customer_name',
        'buyer': 'customer_name',
        'customer party': 'customer_name',
        
        # Sales Office - Recommended
        'sales office': 'sales_office',
        'salesoffice': 'sales_office',
        'office': 'sales_office',
        'sales': 'sales_office',
        'sales region': 'sales_office',
        'region': 'sales_office',
        'area': 'sales_office',
        
        # Sales Manager - Recommended
        'sales manager': 'sales_manager',
        'salesmanager': 'sales_manager',
        'manager': 'sales_manager',
        'sales rep': 'sales_manager',
        'representative': 'sales_manager',
        'sales person': 'sales_manager',
        
        # Ship-to City - Recommended
        'ship to city': 'ship_to_city',
        'ship-to city': 'ship_to_city',
        'ship-to-city': 'ship_to_city',
        'shipcity': 'ship_to_city',
        'city': 'ship_to_city',
        'destination city': 'ship_to_city',
        'ship to': 'ship_to_city',
        'delivery city': 'ship_to_city',
        'customer city': 'ship_to_city',
        
        # Storage - Optional
        'storage': 'storage_location',
        'storage location': 'storage_location',
        'storagelocation': 'storage_location',
        'bin': 'storage_location',
        'warehouse bin': 'storage_location',
        'location': 'storage_location',
        
        # Warehouse - Recommended
        'warehouse': 'warehouse',
        'wh': 'warehouse',
        'ware house': 'warehouse',
        'plant': 'warehouse',
        'warehouse name': 'warehouse',
        'warehouse location': 'warehouse',
        'facility': 'warehouse',
        
        # Warehouse Code - Optional
        'warehouse code': 'warehouse_code',
        'warehousecode': 'warehouse_code',
        'wh code': 'warehouse_code',
        'plant code': 'warehouse_code',
        'facility code': 'warehouse_code',
        
        # Delivery Location - Optional
        'delivery location': 'delivery_location',
        'deliverylocation': 'delivery_location',
        'location': 'delivery_location',
        'delivery address': 'delivery_location',
        'address': 'delivery_location',
        'site': 'delivery_location',
        
        # DN Quantity - Required for each row
        'dn qty': 'dn_qty',
        'dn quantity': 'dn_qty',
        'qty': 'dn_qty',
        'quantity': 'dn_qty',
        'dnqty': 'dn_qty',
        'units': 'dn_qty',
        'order qty': 'dn_qty',
        'delivery qty': 'dn_qty',
        
        # DN Amount - Required for each row
        'dn amount': 'dn_amount',
        'dn amount ': 'dn_amount',
        'amount': 'dn_amount',
        'value': 'dn_amount',
        'dnamount': 'dn_amount',
        'net amount': 'dn_amount',
        'total': 'dn_amount',
        'order amount': 'dn_amount',
        'delivery amount': 'dn_amount',
        'amount value': 'dn_amount',
        
        # DN Create Date - Required for each row
        'dn create date': 'dn_create_date',
        'dn created date': 'dn_create_date',
        'create date': 'dn_create_date',
        'created date': 'dn_create_date',
        'dn created': 'dn_create_date',
        'date created': 'dn_create_date',
        'creation date': 'dn_create_date',
        'order date': 'dn_create_date',
        'order created': 'dn_create_date',
        'document date': 'dn_create_date',
        
        # Good Issue Date - Recommended
        'good issue date': 'good_issue_date',
        'good issue': 'good_issue_date',
        'pgi date': 'good_issue_date',
        'pgi': 'good_issue_date',
        'goods issue': 'good_issue_date',
        'dispatch date': 'good_issue_date',
        'shipped date': 'good_issue_date',
        'ship date': 'good_issue_date',
        'delivery date': 'good_issue_date',
        
        # POD Date - Recommended
        'pod date': 'pod_date',
        'pod': 'pod_date',
        'proof of delivery': 'pod_date',
        'delivery date': 'pod_date',
        'received date': 'pod_date',
        'confirmation date': 'pod_date',
        'customer received': 'pod_date',
        'delivery confirmation': 'pod_date',
        
        # Customer Code - Optional (may be derived)
        'customer code': 'customer_code',
        'customer code no': 'customer_code',
        'cust code': 'customer_code',
        'account code': 'customer_code',
        'customer id': 'customer_code',
        'account no': 'customer_code',
        
        # Dealer Code - Optional (may be derived)
        'dealer code': 'dealer_code',
        'dealer code no': 'dealer_code',
        'dealer no': 'dealer_code',
        'distributor code': 'dealer_code',
        'dealer id': 'dealer_code',
        
        # Remarks - Optional
        'remarks': 'remarks',
        'remark': 'remarks',
        'note': 'remarks',
        'notes': 'remarks',
        'comments': 'remarks',
        'comment': 'remarks',
        'special instructions': 'remarks',
        'additional info': 'remarks',
    }
    
    # Field classifications
    REQUIRED_FIELDS = ['dn_no', 'material_no']
    RECOMMENDED_FIELDS = ['order_type', 'customer_model', 'customer_name', 'warehouse', 
                         'ship_to_city', 'sales_office', 'sales_manager', 'division', 'dn_work']
    OPTIONAL_FIELDS = ['customer_code', 'dealer_code', 'storage_location', 'warehouse_code', 
                      'delivery_location', 'remarks']
    
    @classmethod
    def map_headers(cls, headers: List[str]) -> Tuple[Dict[str, str], Dict[str, str], List[str], Dict[str, List[str]]]:
        """Map Excel headers to database fields with classification"""
        field_to_column = {}
        column_to_field = {}
        unmapped = []
        field_counts = {}
        
        logger.info("=" * 60)
        logger.info("📋 COLUMN MAPPING")
        logger.info("=" * 60)
        
        for header in headers:
            if header is None:
                continue
            
            normalized = normalize_header(header)
            field = cls.HEADER_MAP.get(normalized)
            
            if field:
                # Check for duplicate mapping
                if field in field_to_column:
                    logger.warning(f"  ⚠️ Duplicate mapping for '{field}': '{header}' (was '{field_to_column[field]}')")
                    field_counts[field] = field_counts.get(field, 0) + 1
                else:
                    field_to_column[field] = header
                    column_to_field[header] = field
                    logger.info(f"  ✅ '{header}' -> {field}")
            else:
                unmapped.append(header)
                logger.warning(f"  ⚠️ '{header}' -> UNMAPPED")
        
        # Check field classifications
        missing_required = [f for f in cls.REQUIRED_FIELDS if f not in field_to_column]
        missing_recommended = [f for f in cls.RECOMMENDED_FIELDS if f not in field_to_column]
        found_optional = [f for f in cls.OPTIONAL_FIELDS if f in field_to_column]
        
        logger.info("=" * 60)
        logger.info("📊 FIELD CLASSIFICATION:")
        logger.info(f"  ✅ Required found: {[f for f in cls.REQUIRED_FIELDS if f in field_to_column]}")
        if missing_required:
            logger.error(f"  ❌ Missing required: {missing_required}")
        logger.info(f"  📌 Recommended found: {[f for f in cls.RECOMMENDED_FIELDS if f in field_to_column]}")
        if missing_recommended:
            logger.warning(f"  ⚠️ Missing recommended: {missing_recommended}")
        logger.info(f"  📌 Optional found: {found_optional}")
        if unmapped:
            logger.warning(f"  Unmapped columns: {unmapped[:10]}")
        logger.info("=" * 60)
        
        classification = {
            'required_found': [f for f in cls.REQUIRED_FIELDS if f in field_to_column],
            'required_missing': missing_required,
            'recommended_found': [f for f in cls.RECOMMENDED_FIELDS if f in field_to_column],
            'recommended_missing': missing_recommended,
            'optional_found': found_optional
        }
        
        return field_to_column, column_to_field, unmapped, classification

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
# LOOKUP ENRICHMENT
# =====================================================================================================

class LookupEnricher:
    """Enrich data with lookups from master tables"""
    
    def __init__(self, db: Session):
        self.db = db
        self._dealer_cache = {}
        self._customer_cache = {}
        self._warehouse_cache = {}
        self._cache_hits = 0
        self._cache_misses = 0
    
    @lru_cache(maxsize=1000)
    def get_dealer_code(self, customer_name: str) -> Optional[str]:
        """Look up dealer code from customer name"""
        if not customer_name:
            return None
        
        # This is a placeholder - implement with actual dealer master table
        # Example: query a Dealers table
        
        # For now, return None (will be populated from Excel if available)
        return None
    
    @lru_cache(maxsize=1000)
    def get_customer_code(self, customer_name: str) -> Optional[str]:
        """Look up customer code from customer name"""
        if not customer_name:
            return None
        
        # This is a placeholder - implement with actual customer master table
        return None
    
    @lru_cache(maxsize=1000)
    def get_warehouse_code(self, warehouse_name: str) -> Optional[str]:
        """Look up warehouse code from warehouse name"""
        if not warehouse_name:
            return None
        
        # This is a placeholder - implement with actual warehouse master table
        return None
    
    def enrich_row(self, row_data: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich a row with lookup data"""
        result = row_data.copy()
        
        # Enrich dealer code
        if not result.get('dealer_code') and result.get('customer_name'):
            dealer = self.get_dealer_code(result['customer_name'])
            if dealer:
                result['dealer_code'] = dealer
                self._cache_hits += 1
            else:
                self._cache_misses += 1
        
        # Enrich customer code
        if not result.get('customer_code') and result.get('customer_name'):
            customer = self.get_customer_code(result['customer_name'])
            if customer:
                result['customer_code'] = customer
                self._cache_hits += 1
            else:
                self._cache_misses += 1
        
        # Enrich warehouse code
        if not result.get('warehouse_code') and result.get('warehouse'):
            warehouse = self.get_warehouse_code(result['warehouse'])
            if warehouse:
                result['warehouse_code'] = warehouse
                self._cache_hits += 1
            else:
                self._cache_misses += 1
        
        return result

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
        formats = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"]
        
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
# DATA INTEGRITY VALIDATOR
# =====================================================================================================

class DataIntegrityValidator:
    """Validate data integrity before import"""
    
    @staticmethod
    def validate_row(row_data: Dict[str, Any], row_number: int) -> List[str]:
        """Validate a single row and return list of issues"""
        issues = []
        
        # Required fields
        if not row_data.get('dn_no'):
            issues.append(f"Row {row_number}: Missing DN NO")
        if not row_data.get('material_no'):
            issues.append(f"Row {row_number}: Missing Material NO")
        
        # Amount validation
        amount = row_data.get('dn_amount')
        if amount is not None and amount < 0:
            issues.append(f"Row {row_number}: Negative amount {amount}")
        
        # Quantity validation
        qty = row_data.get('dn_qty')
        if qty is not None and qty <= 0:
            issues.append(f"Row {row_number}: Invalid quantity {qty}")
        
        # Date validation
        if row_data.get('dn_create_date') is None:
            issues.append(f"Row {row_number}: Missing DN Create Date")
        
        return issues

# =====================================================================================================
# EXCEL IMPORT SERVICE - ENTERPRISE
# =====================================================================================================

class ExcelImportService:
    """Enterprise Excel import with worksheet detection and lookup enrichment"""
    
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
        lookup_enricher = LookupEnricher(db)
        
        logger.info("=" * 60)
        logger.info("📊 EXCEL IMPORT v8.0 - ENTERPRISE WITH LOOKUP ENRICHMENT")
        logger.info("=" * 60)
        logger.info(f"📁 File: {file_path}")
        logger.info(f"📋 Source: {source_filename}")
        
        # Generate batch ID
        if not batch_id:
            batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        logger.info(f"📋 Batch: {batch_id}")
        
        try:
            # ============================================================
            # STEP 1: Detect Worksheet
            # ============================================================
            sheet_name, header_row, sheet_info = detect_worksheet(file_path)
            
            # ============================================================
            # STEP 2: Read Excel with detected sheet and header
            # ============================================================
            logger.info(f"📖 Reading sheet '{sheet_name}' with header at row {header_row}")
            
            df = pd.read_excel(
                file_path,
                sheet_name=sheet_name,
                header=header_row,
                engine='openpyxl'
            )
            
            df = df.dropna(how='all')
            df = df.dropna(axis=1, how='all')
            
            total_rows = len(df)
            logger.info(f"📄 Read {total_rows} rows, {len(df.columns)} columns")
            
            # ============================================================
            # STEP 3: Map Columns
            # ============================================================
            headers = [str(col).strip() for col in df.columns]
            
            logger.info("=" * 60)
            logger.info("📋 HEADER DIAGNOSTICS")
            logger.info("=" * 60)
            logger.info(f"  Sheet: '{sheet_name}'")
            logger.info(f"  Header Row: {header_row}")
            logger.info(f"  Total Columns: {len(headers)}")
            logger.info(f"  First 10 Headers: {headers[:10]}")
            
            field_to_column, column_to_field, unmapped, classification = ColumnMapper.map_headers(headers)
            
            if classification['required_missing']:
                logger.error(f"❌ Missing required fields: {classification['required_missing']}")
                logger.error(f"   Available headers: {headers}")
                return {
                    "success": False,
                    "error": f"Missing required columns: {classification['required_missing']}",
                    "batch_id": batch_id,
                    "total_rows": 0,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                    "total_revenue_imported": 0,
                    "total_units_imported": 0,
                    "validation_errors": [f"Missing required fields: {classification['required_missing']}"],
                    "sheet_name": sheet_name,
                    "header_row": header_row,
                    "available_headers": headers[:20]
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
            duplicate_in_file = 0
            duplicate_in_db = 0
            integrity_issues = []
            
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
                        duplicate_in_file += 1
                        validation_errors.append(f"Row {row_number}: Duplicate within file")
                        failed_count += 1
                        continue
                    processed_keys.add(key)
                    
                    # Helper to get value
                    def get_value(field: str):
                        col = field_to_column.get(field)
                        return row.get(col) if col else None
                    
                    # Extract all fields
                    row_data = {
                        'dn_no': dn_no,
                        'material_no': material_no,
                        'order_type': normalize_string(get_value('order_type')),
                        'division': normalize_string(get_value('division')),
                        'customer_name': normalize_string(get_value('customer_name')),
                        'customer_model': normalize_string(get_value('customer_model')),
                        'customer_code': normalize_string(get_value('customer_code')),
                        'dealer_code': normalize_string(get_value('dealer_code')),
                        'warehouse': normalize_string(get_value('warehouse')),
                        'warehouse_code': normalize_string(get_value('warehouse_code')),
                        'ship_to_city': normalize_string(get_value('ship_to_city')),
                        'delivery_location': normalize_string(get_value('delivery_location')),
                        'sales_office': normalize_string(get_value('sales_office')),
                        'sales_manager': normalize_string(get_value('sales_manager')),
                        'dn_work': normalize_string(get_value('dn_work')),
                        'storage_location': normalize_string(get_value('storage_location')),
                        'remarks': normalize_string(get_value('remarks')),
                        'dn_qty': parse_quantity(get_value('dn_qty')),
                        'dn_amount': parse_amount(get_value('dn_amount')),
                        'dn_create_date': parse_date(get_value('dn_create_date')),
                        'good_issue_date': parse_date(get_value('good_issue_date')),
                        'pod_date': parse_date(get_value('pod_date'))
                    }
                    
                    # ============================================================
                    # STEP 5: Data Integrity Validation
                    # ============================================================
                    issues = DataIntegrityValidator.validate_row(row_data, row_number)
                    if issues:
                        integrity_issues.extend(issues)
                        # Log warnings but continue (unless strict mode)
                        for issue in issues:
                            logger.warning(f"⚠️ {issue}")
                    
                    # ============================================================
                    # STEP 6: Lookup Enrichment
                    # ============================================================
                    row_data = lookup_enricher.enrich_row(row_data)
                    
                    # Derive status
                    status = StatusEngine.derive(
                        row_data['dn_create_date'],
                        row_data['good_issue_date'],
                        row_data['pod_date']
                    )
                    
                    # ============================================================
                    # STEP 7: Check Existing Record
                    # ============================================================
                    existing = None
                    if skip_dups or update_existing_rows:
                        existing = db.query(DeliveryReport).filter_by(
                            dn_no=dn_no,
                            material_no=material_no
                        ).first()
                    
                    if existing and update_existing_rows:
                        # Update existing record
                        existing.dn_work = row_data['dn_work']
                        existing.order_type = row_data['order_type']
                        existing.division = row_data['division']
                        existing.customer_code = row_data['customer_code']
                        existing.dealer_code = row_data['dealer_code']
                        existing.customer_name = row_data['customer_name']
                        existing.customer_model = row_data['customer_model']
                        existing.storage_location = row_data['storage_location']
                        existing.sales_office = row_data['sales_office']
                        existing.sales_manager = row_data['sales_manager']
                        existing.ship_to_city = row_data['ship_to_city']
                        existing.warehouse = row_data['warehouse']
                        existing.warehouse_code = row_data['warehouse_code']
                        existing.delivery_location = row_data['delivery_location']
                        existing.dn_qty = row_data['dn_qty']
                        existing.dn_amount = float(row_data['dn_amount']) if row_data['dn_amount'] else None
                        existing.dn_create_date = row_data['dn_create_date']
                        existing.good_issue_date = row_data['good_issue_date']
                        existing.pod_date = row_data['pod_date']
                        existing.remarks = row_data['remarks']
                        existing.delivery_status = status['delivery_status']
                        existing.pgi_status = status['pgi_status']
                        existing.pod_status = status['pod_status']
                        existing.pending_flag = status['pending_flag']
                        existing.source_file = source_filename
                        existing.upload_batch_id = batch_id
                        existing.updated_at = datetime.utcnow()
                        updated_count += 1
                        
                    elif existing and skip_dups:
                        duplicate_in_db += 1
                        skipped_count += 1
                        
                    else:
                        # Insert new record
                        record = DeliveryReport(
                            dn_no=row_data['dn_no'],
                            dn_work=row_data['dn_work'],
                            order_type=row_data['order_type'],
                            division=row_data['division'],
                            customer_code=row_data['customer_code'],
                            dealer_code=row_data['dealer_code'],
                            customer_name=row_data['customer_name'],
                            customer_model=row_data['customer_model'],
                            material_no=row_data['material_no'],
                            storage_location=row_data['storage_location'],
                            sales_office=row_data['sales_office'],
                            sales_manager=row_data['sales_manager'],
                            ship_to_city=row_data['ship_to_city'],
                            warehouse=row_data['warehouse'],
                            warehouse_code=row_data['warehouse_code'],
                            delivery_location=row_data['delivery_location'],
                            dn_qty=row_data['dn_qty'],
                            dn_amount=float(row_data['dn_amount']) if row_data['dn_amount'] else None,
                            dn_create_date=row_data['dn_create_date'],
                            good_issue_date=row_data['good_issue_date'],
                            pod_date=row_data['pod_date'],
                            remarks=row_data['remarks'],
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
                    if row_data['dn_amount']:
                        total_revenue += row_data['dn_amount']
                    if row_data['dn_qty']:
                        total_units += row_data['dn_qty']
                    
                    # Commit in batches (if not atomic)
                    if not ATOMIC_COMMIT and (index + 1) % BATCH_SIZE == 0:
                        db.commit()
                        logger.info(f"📊 Committed {index + 1} rows")
                    
                except Exception as e:
                    failed_count += 1
                    validation_errors.append(f"Row {row_number}: {str(e)}")
                    logger.warning(f"⚠️ Row {row_number} failed: {e}")
            
            # ============================================================
            # STEP 8: Final Commit
            # ============================================================
            logger.info("💾 Committing to database...")
            db.commit()
            
            # ============================================================
            # STEP 9: Post-Import Verification
            # ============================================================
            duration = time.time() - start_time
            
            # Verify counts
            if total_rows != (inserted_count + updated_count + skipped_count + failed_count):
                logger.warning(f"⚠️ Count mismatch: Total rows {total_rows} vs processed {inserted_count + updated_count + skipped_count + failed_count}")
            
            logger.info("=" * 60)
            logger.info("✅ IMPORT COMPLETED")
            logger.info("=" * 60)
            logger.info(f"  Duration: {duration:.2f}s")
            logger.info(f"  Sheet: '{sheet_name}'")
            logger.info(f"  Header Row: {header_row}")
            logger.info(f"  Rows Read: {total_rows}")
            logger.info("")
            logger.info("  📊 RESULTS:")
            logger.info(f"  ✅ Inserted: {inserted_count}")
            logger.info(f"  🔄 Updated: {updated_count}")
            logger.info(f"  ⏭️ Skipped: {skipped_count}")
            logger.info(f"  ❌ Failed: {failed_count}")
            logger.info(f"  📌 Duplicate in file: {duplicate_in_file}")
            logger.info(f"  📌 Duplicate in DB: {duplicate_in_db}")
            logger.info("")
            logger.info("  💰 TOTALS:")
            logger.info(f"  Revenue: PKR {total_revenue:,.2f}")
            logger.info(f"  Units: {total_units}")
            logger.info("")
            logger.info("  🔍 LOOKUP ENRICHMENT:")
            logger.info(f"  Cache Hits: {lookup_enricher._cache_hits}")
            logger.info(f"  Cache Misses: {lookup_enricher._cache_misses}")
            logger.info("")
            if integrity_issues:
                logger.warning(f"  ⚠️ Integrity Issues: {len(integrity_issues)}")
                for issue in integrity_issues[:5]:
                    logger.warning(f"    - {issue}")
            logger.info("=" * 60)
            
            return {
                "success": True,
                "batch_id": batch_id,
                "total_rows": total_rows,
                "inserted_count": inserted_count,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
                "duplicate_in_file": duplicate_in_file,
                "duplicate_in_db": duplicate_in_db,
                "total_revenue_imported": float(total_revenue),
                "total_units_imported": total_units,
                "validation_errors": validation_errors[:20],
                "integrity_warnings": integrity_issues[:10],
                "sheet_name": sheet_name,
                "header_row": header_row,
                "classification": classification,
                "lookup_stats": {
                    "cache_hits": lookup_enricher._cache_hits,
                    "cache_misses": lookup_enricher._cache_misses
                }
            }
            
        except WorksheetNotFoundError as e:
            logger.error(f"❌ Worksheet detection failed: {e}")
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
