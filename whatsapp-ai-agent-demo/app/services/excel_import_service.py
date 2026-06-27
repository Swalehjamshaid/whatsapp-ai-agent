# =====================================================================================================
# FILE: app/services/excel_import_service.py
# VERSION: v15.1 - FIXED CHUNKED READING + FALLBACKS
# PURPOSE: Ultra-fast Excel import with automatic worksheet detection
# COMPATIBLE WITH: upload.py v4.3
# =====================================================================================================

import pandas as pd
import logging
import uuid
import re
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, Any, Optional, List, Tuple, Set
from sqlalchemy.orm import Session
from sqlalchemy import text
import time
import traceback
import gc
import os

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# =====================================================================================================
# LIBRARY IMPORTS WITH FALLBACKS - FIXED
# =====================================================================================================

# Polars - Optional (10x faster Excel reading)
try:
    import polars as pl
    HAS_POLARS = True
    logger.info("✅ Polars available - Fast Excel reading enabled")
except ImportError:
    HAS_POLARS = False
    logger.warning("⚠️ Polars not available - Using pandas for Excel reading")

# RapidFuzz - Optional (smart column mapping)
try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
    logger.info("✅ RapidFuzz available - Smart column mapping enabled")
except ImportError:
    HAS_RAPIDFUZZ = False
    logger.warning("⚠️ RapidFuzz not available - Using exact column mapping")

# Psutil - Optional (memory monitoring)
try:
    import psutil
    HAS_PSUTIL = True
    logger.info("✅ Psutil available - Memory monitoring enabled")
except ImportError:
    HAS_PSUTIL = False
    logger.warning("⚠️ Psutil not available - Memory monitoring disabled")

# NumPy - Optional (faster processing)
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# =====================================================================================================
# CONFIGURATION
# =====================================================================================================

BULK_SIZE = 100000
HEADER_SCAN_ROWS = 25
STRICT_MODE = True
GC_INTERVAL = 5
FUZZY_THRESHOLD = 85

# =====================================================================================================
# EXCEPTIONS
# =====================================================================================================

class ImportError(Exception):
    pass

class HeaderNotFoundError(ImportError):
    pass

class WorksheetNotFoundError(ImportError):
    pass

class ValidationError(ImportError):
    pass

class VerificationError(Exception):
    pass

# =====================================================================================================
# HEADER NORMALIZATION
# =====================================================================================================

_separator_re = re.compile(r'[_\-./\\#·•:;|]')
_whitespace_re = re.compile(r'\s+')

def normalize_header(header: Any) -> str:
    """Normalize Excel header for consistent matching"""
    if header is None:
        return ""
    
    normalized = str(header).strip()
    normalized = _separator_re.sub(' ', normalized)
    normalized = normalized.replace('\u00a0', ' ')
    normalized = normalized.replace('\t', ' ')
    normalized = normalized.replace('\r', ' ')
    normalized = normalized.replace('\n', ' ')
    normalized = _whitespace_re.sub(' ', normalized).strip().lower()
    
    return normalized

# =====================================================================================================
# WORKSHEET DETECTION
# =====================================================================================================

def detect_worksheet(file_path: str) -> Tuple[str, int, Dict[str, Any]]:
    """Find the worksheet containing delivery data."""
    logger.info("=" * 60)
    logger.info("🔍 WORKSHEET DETECTION v15.1")
    logger.info("=" * 60)
    
    try:
        if HAS_POLARS:
            try:
                df_meta = pl.read_excel(file_path, sheet_name=None, engine='calamine')
                sheet_names = list(df_meta.keys())
            except:
                xl = pd.ExcelFile(file_path, engine='openpyxl')
                sheet_names = xl.sheet_names
        else:
            xl = pd.ExcelFile(file_path, engine='openpyxl')
            sheet_names = xl.sheet_names
    except:
        xl = pd.ExcelFile(file_path, engine='openpyxl')
        sheet_names = xl.sheet_names
    
    logger.info(f"📋 Found {len(sheet_names)} sheets: {sheet_names}")
    
    summary_indicators = ['sum s', 'sum', 'summary', 'total', 'grand total', 'report']
    
    best_sheet = None
    best_score = 0
    best_header_row = 0
    best_info = {}
    
    for sheet_name in sheet_names:
        if sheet_name.startswith('_') or sheet_name.startswith('$'):
            continue
        
        is_summary = any(ind in sheet_name.lower() for ind in summary_indicators)
        if is_summary:
            logger.info(f"  ⏭️ Skipping summary sheet: '{sheet_name}'")
            continue
        
        logger.info(f"  📄 Checking sheet: '{sheet_name}'")
        
        try:
            df_sample = pd.read_excel(
                file_path,
                sheet_name=sheet_name,
                header=None,
                nrows=HEADER_SCAN_ROWS + 5,
                engine='openpyxl'
            )
            
            if len(df_sample) == 0:
                continue
            
            header_row, score, matched_headers = detect_header_row(df_sample)
            
            has_dn = any('dn no' == normalize_header(h) for h in matched_headers)
            has_material = any('material no' == normalize_header(h) for h in matched_headers)
            
            if has_dn and has_material:
                score += 30
            
            if score > best_score:
                best_score = score
                best_sheet = sheet_name
                best_header_row = header_row
                best_info = {
                    'score': score,
                    'matched_headers': matched_headers,
                    'has_dn': has_dn,
                    'has_material': has_material
                }
                logger.info(f"    ✅ New best sheet: '{sheet_name}' (score: {score})")
                
        except Exception as e:
            logger.warning(f"    ❌ Error reading sheet '{sheet_name}': {e}")
            continue
    
    if best_sheet is None or best_score < 3:
        raise WorksheetNotFoundError(f"No worksheet with delivery data found. Available sheets: {sheet_names}")
    
    logger.info("=" * 60)
    logger.info(f"✅ SELECTED SHEET: '{best_sheet}'")
    logger.info(f"   Score: {best_score}")
    logger.info(f"   Header Row: {best_header_row}")
    logger.info("=" * 60)
    
    return best_sheet, best_header_row, best_info

# =====================================================================================================
# HEADER DETECTION
# =====================================================================================================

def detect_header_row(df: pd.DataFrame, max_rows: int = HEADER_SCAN_ROWS) -> Tuple[int, int, List[str]]:
    """Detect header row using pandas"""
    if len(df) == 0:
        return 0, 0, []
    
    header_keywords = {
        'dn no': 3, 'dn': 2, 'material no': 3, 'material': 2,
        'order type': 2, 'customer model': 2, 'warehouse': 2,
        'ship to city': 2, 'dn amount': 2, 'dn qty': 2,
        'division': 1, 'sales office': 1, 'sales manager': 1,
        'storage': 1, 'dn create date': 1, 'good issue date': 1,
        'pod date': 1, 'work': 1, 'remarks': 1,
        'model': 1, 'city': 1, 'amount': 1, 'qty': 1,
        'pgi': 1, 'pod': 1, 'delivery': 1
    }
    
    best_score = 0
    best_row = 0
    best_matched = []
    
    rows_to_check = min(max_rows, len(df))
    
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
    
    if best_score < 3:
        for row_idx in range(rows_to_check):
            row_data = df.iloc[row_idx]
            for value in row_data:
                if value and isinstance(value, str):
                    normalized = normalize_header(value)
                    if normalized in ['dn no', 'dn', 'material no', 'material']:
                        return row_idx, 5, [str(value)]
    
    return best_row, best_score, best_matched

# =====================================================================================================
# SMART COLUMN MAPPER
# =====================================================================================================

class SmartColumnMapper:
    """Intelligent column mapping with fuzzy matching"""
    
    HEADER_MAP = {
        # Order Type
        'order type': 'order_type', 'order': 'order_type', 'ordertype': 'order_type',
        'type': 'order_type', 'order no': 'order_type', 'order number': 'order_type',
        'sales order': 'order_type', 'so': 'order_type', 'order_type': 'order_type',
        'order-type': 'order_type', 'order.type': 'order_type', 'order#': 'order_type',
        
        # DN NO
        'dn no': 'dn_no', 'dn': 'dn_no', 'dn#': 'dn_no', 'd n no': 'dn_no',
        'd n': 'dn_no', 'delivery note': 'dn_no', 'delivery note no': 'dn_no',
        'delivery note number': 'dn_no', 'delivery number': 'dn_no', 'dn number': 'dn_no',
        'delivery note #': 'dn_no', 'dn_no': 'dn_no', 'dn-no': 'dn_no',
        'dn.no': 'dn_no', 'dnnumber': 'dn_no',
        
        # DN amount
        'dn amount': 'dn_amount', 'dn amount ': 'dn_amount', 'dn_amount': 'dn_amount',
        'dn-amount': 'dn_amount', 'dn.amount': 'dn_amount', 'dn#amount': 'dn_amount',
        'dnamount': 'dn_amount', 'amount': 'dn_amount', 'value': 'dn_amount',
        'net amount': 'dn_amount', 'total': 'dn_amount', 'order amount': 'dn_amount',
        'delivery amount': 'dn_amount', 'amount value': 'dn_amount', 'dn amt': 'dn_amount',
        'amt': 'dn_amount', 'amount (pkr)': 'dn_amount', 'pkr': 'dn_amount',
        
        # DN Qty
        'dn qty': 'dn_qty', 'dn quantity': 'dn_qty', 'dn_qty': 'dn_qty',
        'dn-qty': 'dn_qty', 'dn.qty': 'dn_qty', 'dn#qty': 'dn_qty',
        'dnqty': 'dn_qty', 'qty': 'dn_qty', 'quantity': 'dn_qty',
        'units': 'dn_qty', 'order qty': 'dn_qty', 'delivery qty': 'dn_qty',
        'qty (units)': 'dn_qty', 'piece': 'dn_qty', 'pcs': 'dn_qty',
        
        # DN Work
        'dn work': 'dn_work', 'work': 'dn_work', 'work order': 'dn_work',
        'work no': 'dn_work', 'work number': 'dn_work', 'job': 'dn_work',
        'dn_work': 'dn_work', 'dn-work': 'dn_work', 'status': 'dn_work',
        'dn status': 'dn_work', 'delivery status': 'dn_work',
        
        # Division
        'division': 'division', 'div': 'division', 'department': 'division',
        'business unit': 'division', 'division ': 'division',
        'product line': 'division', 'category': 'division',
        
        # Material NO
        'material no': 'material_no', 'material': 'material_no', 'material#': 'material_no',
        'material number': 'material_no', 'material code': 'material_no', 'sku': 'material_no',
        'product no': 'material_no', 'product number': 'material_no', 'item no': 'material_no',
        'item': 'material_no', 'part no': 'material_no', 'part number': 'material_no',
        'material_no': 'material_no', 'material-no': 'material_no',
        'material.number': 'material_no', 'product code': 'material_no',
        
        # Customer Model
        'customer model': 'customer_model', 'model': 'customer_model',
        'model name': 'customer_model', 'product model': 'customer_model',
        'model no': 'customer_model', 'model number': 'customer_model',
        'product': 'customer_model', 'item description': 'customer_model',
        'customer_model': 'customer_model', 'customer-model': 'customer_model',
        'model name': 'customer_model', 'description': 'customer_model',
        
        # Sales Office
        'sales office': 'sales_office', 'salesoffice': 'sales_office',
        'office': 'sales_office', 'sales': 'sales_office',
        'sales region': 'sales_office', 'region': 'sales_office',
        'area': 'sales_office', 'sales_office': 'sales_office',
        'sales-office': 'sales_office', 'branch': 'sales_office',
        'location': 'sales_office',
        
        # Sold-to-party Name
        'sold to party name': 'customer_name', 'sold-to-party name': 'customer_name',
        'sold to party': 'customer_name', 'sold-to-party': 'customer_name',
        'customer name': 'customer_name', 'customer': 'customer_name',
        'dealer name': 'customer_name', 'party name': 'customer_name',
        'client name': 'customer_name', 'buyer': 'customer_name',
        'customer party': 'customer_name', 'customer_name': 'customer_name',
        'customer-name': 'customer_name', 'party': 'customer_name',
        'dealer': 'customer_name',
        
        # Ship-to City
        'ship to city': 'ship_to_city', 'ship-to city': 'ship_to_city',
        'ship-to-city': 'ship_to_city', 'shipcity': 'ship_to_city',
        'city': 'ship_to_city', 'destination city': 'ship_to_city',
        'ship to': 'ship_to_city', 'delivery city': 'ship_to_city',
        'customer city': 'ship_to_city', 'ship_to_city': 'ship_to_city',
        'ship-to-city': 'ship_to_city', 'delivery location': 'ship_to_city',
        'destination': 'ship_to_city',
        
        # Storage
        'storage': 'storage_location', 'storage location': 'storage_location',
        'storagelocation': 'storage_location', 'bin': 'storage_location',
        'warehouse bin': 'storage_location', 'location': 'storage_location',
        'storage_location': 'storage_location', 'storage-location': 'storage_location',
        'store': 'storage_location', 'storage code': 'storage_location',
        
        # Warehouse
        'warehouse': 'warehouse', 'ware house': 'warehouse', 'ware_house': 'warehouse',
        'ware-house': 'warehouse', 'ware.house': 'warehouse', 'wh': 'warehouse',
        'warehouse name': 'warehouse', 'warehouse location': 'warehouse',
        'facility': 'warehouse', 'plant': 'warehouse', 'warehouse ': 'warehouse',
        'whse': 'warehouse', 'store': 'warehouse',
        
        # DN Create date
        'dn create date': 'dn_create_date', 'dn created date': 'dn_create_date',
        'dn_create_date': 'dn_create_date', 'dn-created-date': 'dn_create_date',
        'create date': 'dn_create_date', 'created date': 'dn_create_date',
        'dn created': 'dn_create_date', 'date created': 'dn_create_date',
        'creation date': 'dn_create_date', 'order date': 'dn_create_date',
        'order created': 'dn_create_date', 'document date': 'dn_create_date',
        'dn date': 'dn_create_date', 'date': 'dn_create_date',
        
        # Good issue date
        'good issue date': 'good_issue_date', 'good issue': 'good_issue_date',
        'good_issue_date': 'good_issue_date', 'pgi date': 'good_issue_date',
        'pgi': 'good_issue_date', 'goods issue': 'good_issue_date',
        'dispatch date': 'good_issue_date', 'shipped date': 'good_issue_date',
        'ship date': 'good_issue_date', 'delivery date': 'good_issue_date',
        'good issue date ': 'good_issue_date', 'pgi': 'good_issue_date',
        'goods issue date': 'good_issue_date',
        
        # POD Date
        'pod date': 'pod_date', 'pod': 'pod_date', 'pod_date': 'pod_date',
        'proof of delivery': 'pod_date', 'delivery date': 'pod_date',
        'received date': 'pod_date', 'confirmation date': 'pod_date',
        'customer received': 'pod_date', 'delivery confirmation': 'pod_date',
        'pod_date ': 'pod_date', 'pod date': 'pod_date', 'receipt date': 'pod_date',
        
        # Sales Manager
        'sales manager': 'sales_manager', 'salesmanager': 'sales_manager',
        'manager': 'sales_manager', 'sales rep': 'sales_manager',
        'representative': 'sales_manager', 'sales person': 'sales_manager',
        'sales_manager': 'sales_manager', 'sales-manager': 'sales_manager',
        'sales.manager': 'sales_manager', 'salesperson': 'sales_manager',
        'rep': 'sales_manager', 'sales manager name': 'sales_manager',
    }
    
    _normalized_keys = list(HEADER_MAP.keys())
    _field_names = list(set(HEADER_MAP.values()))
    REQUIRED_FIELDS = ['dn_no', 'material_no']
    
    @classmethod
    def map_headers(cls, headers: List[str]) -> Tuple[Dict[str, str], Dict[str, str], List[str], List[str]]:
        """Map Excel headers with exact + fuzzy matching"""
        field_to_column = {}
        column_to_field = {}
        unmapped = []
        
        logger.info("=" * 60)
        logger.info("📋 SMART COLUMN MAPPING")
        logger.info("=" * 60)
        
        for header in headers:
            if header is None:
                continue
            
            normalized = normalize_header(header)
            field = cls.HEADER_MAP.get(normalized)
            
            if field:
                if field not in field_to_column:
                    field_to_column[field] = header
                    column_to_field[header] = field
                    logger.info(f"  ✅ EXACT: '{header}' -> {field}")
            else:
                unmapped.append(header)
                logger.warning(f"  ⚠️ '{header}' -> UNMAPPED")
        
        missing = [f for f in cls.REQUIRED_FIELDS if f not in field_to_column]
        
        logger.info("=" * 60)
        logger.info(f"  ✅ Mapped: {len(field_to_column)} columns")
        logger.info(f"  ⚠️ Unmapped: {len(unmapped)} columns")
        if missing:
            logger.error(f"  ❌ Missing required: {missing}")
        logger.info("=" * 60)
        
        return field_to_column, column_to_field, unmapped, missing

# =====================================================================================================
# FAST EXCEL READING - FIXED v15.1
# =====================================================================================================

def read_excel_fast(file_path: str, sheet_name: str, header_row: int):
    """Read Excel using Polars or fallback to pandas - FIXED"""
    
    if HAS_POLARS:
        try:
            try:
                df = pl.read_excel(
                    file_path,
                    sheet_name=sheet_name,
                    header_row=header_row,
                    engine='calamine'
                )
                logger.info("⚡ Using Polars with calamine engine (header_row)")
                return df.to_pandas()
            except TypeError:
                df = pl.read_excel(
                    file_path,
                    sheet_name=sheet_name,
                    engine='calamine'
                )
                if header_row > 0:
                    df = df.slice(header_row)
                    new_columns = df.row(0)
                    df.columns = new_columns
                    df = df.slice(1)
                logger.info("⚡ Using Polars with calamine engine (slice method)")
                return df.to_pandas()
        except Exception as e:
            logger.warning(f"⚠️ Polars read failed: {e}, trying fallback...")
    
    # ✅ FIXED: Use pandas without chunksize to avoid error
    logger.info("📖 Using pandas (single read - optimized for compatibility)")
    try:
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row, engine='openpyxl')
        logger.info(f"✅ Read {len(df)} rows with pandas")
        return df
    except Exception as e:
        logger.error(f"❌ Failed to read Excel: {e}")
        raise

# =====================================================================================================
# STATUS ENGINE
# =====================================================================================================

class StatusEngine:
    @staticmethod
    def derive(dn_create_date: Optional[date], good_issue_date: Optional[date], pod_date: Optional[date]) -> Dict[str, Any]:
        has_dn = dn_create_date is not None
        has_pgi = good_issue_date is not None
        has_pod = pod_date is not None
        
        if has_pod and has_pgi and has_dn:
            return {'delivery_status': 'Delivered', 'pgi_status': 'Completed', 'pod_status': 'Completed', 'pending_flag': False}
        elif has_pgi and has_dn:
            return {'delivery_status': 'Dispatched', 'pgi_status': 'Completed', 'pod_status': 'Pending', 'pending_flag': True}
        elif has_dn:
            return {'delivery_status': 'Pending Dispatch', 'pgi_status': 'Pending', 'pod_status': 'Pending', 'pending_flag': True}
        else:
            return {'delivery_status': 'Unknown', 'pgi_status': 'Unknown', 'pod_status': 'Unknown', 'pending_flag': True}

# =====================================================================================================
# DATA PARSING FUNCTIONS
# =====================================================================================================

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

# =====================================================================================================
# FAST BATCH PROCESSOR
# =====================================================================================================

class FastBatchProcessor:
    """Fast batch processor with bulk insert"""
    
    def __init__(self, db: Session, field_to_column: Dict, batch_id: str, source_filename: str):
        self.db = db
        self.field_to_column = field_to_column
        self.batch_id = batch_id
        self.source_filename = source_filename
        
        self.inserted_count = 0
        self.updated_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.total_revenue = Decimal(0)
        self.total_units = 0
        self.validation_errors = []
        self.processed_keys = set()
        
        self.bulk_buffer = []
        self.commit_counter = 0
        
        self.skip_dups = False
        self.update_existing = False
    
    def process_row(self, row_data: Dict[str, Any], row_number: int) -> bool:
        try:
            dn_no = row_data['dn_no']
            material_no = row_data['material_no']
            
            key = f"{dn_no}_{material_no}"
            if key in self.processed_keys:
                self.validation_errors.append(f"Row {row_number}: Duplicate")
                self.failed_count += 1
                return False
            self.processed_keys.add(key)
            
            status = StatusEngine.derive(
                row_data['dn_create_date'],
                row_data['good_issue_date'],
                row_data['pod_date']
            )
            
            existing = None
            if self.skip_dups or self.update_existing:
                existing = self.db.query(DeliveryReport).filter_by(
                    dn_no=dn_no,
                    material_no=material_no
                ).first()
            
            if existing and self.update_existing:
                self._update_record(existing, row_data, status)
                self.updated_count += 1
            elif existing and self.skip_dups:
                self.skipped_count += 1
            else:
                self._add_to_bulk_buffer(row_data, status)
                self.inserted_count += 1
            
            if row_data['dn_amount']:
                self.total_revenue += row_data['dn_amount']
            if row_data['dn_qty']:
                self.total_units += row_data['dn_qty']
            
            if len(self.bulk_buffer) >= BULK_SIZE:
                self.flush_bulk()
            
            return True
            
        except Exception as e:
            self.validation_errors.append(f"Row {row_number}: {str(e)}")
            self.failed_count += 1
            logger.warning(f"⚠️ Row {row_number} failed: {e}")
            return False
    
    def _update_record(self, existing, row_data, status):
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
        existing.source_file = self.source_filename
        existing.upload_batch_id = self.batch_id
        existing.updated_at = datetime.utcnow()
    
    def _add_to_bulk_buffer(self, row_data, status):
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
        if not self.bulk_buffer:
            return
        
        try:
            self.db.bulk_insert_mappings(DeliveryReport, self.bulk_buffer)
            self.db.commit()
            
            self.commit_counter += 1
            logger.info(f"⚡ Bulk committed batch {self.commit_counter} ({len(self.bulk_buffer):,} rows)")
            self.bulk_buffer.clear()
            
            if self.commit_counter % GC_INTERVAL == 0:
                gc.collect()
                
        except Exception as e:
            logger.error(f"❌ Bulk commit failed: {e}")
            self.db.rollback()
            raise
    
    def finalize(self):
        self.flush_bulk()
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
# EXCEL IMPORT SERVICE - v15.1 FIXED
# =====================================================================================================

class ExcelImportService:
    """Ultra-fast Excel import with perfect worksheet detection - v15.1"""
    
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
        
        if HAS_PSUTIL:
            try:
                mem = psutil.virtual_memory()
                logger.info(f"💾 Available Memory: {mem.available / (1024**3):.1f} GB")
            except:
                pass
        
        logger.info("=" * 60)
        logger.info("⚡ EXCEL IMPORT v15.1 - FIXED")
        logger.info("=" * 60)
        logger.info(f"📁 File: {file_path}")
        logger.info(f"📋 Source: {source_filename}")
        logger.info(f"⚡ Bulk Size: {BULK_SIZE:,} rows")
        
        if not batch_id:
            batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        logger.info(f"📋 Batch: {batch_id}")
        
        try:
            # STEP 1: Detect Worksheet
            sheet_name, header_row, sheet_info = detect_worksheet(file_path)
            
            # STEP 2: Read Excel
            logger.info(f"📖 Reading sheet '{sheet_name}' with header at row {header_row}")
            df = read_excel_fast(file_path, sheet_name, header_row)
            
            # Clean data
            df = df.dropna(how='all')
            df = df.dropna(axis=1, how='all')
            
            total_rows = len(df)
            logger.info(f"📄 Read {total_rows:,} rows, {len(df.columns)} columns")
            
            # STEP 3: Map Columns
            headers = [str(col).strip() for col in df.columns]
            
            logger.info("=" * 60)
            logger.info("📋 HEADER DIAGNOSTICS")
            logger.info("=" * 60)
            logger.info(f"  Sheet: '{sheet_name}'")
            logger.info(f"  Header Row: {header_row}")
            logger.info(f"  Total Columns: {len(headers)}")
            
            field_to_column, column_to_field, unmapped, missing = SmartColumnMapper.map_headers(headers)
            
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
                    "validation_errors": [f"Missing required fields: {missing}"],
                    "sheet_name": sheet_name,
                    "header_row": header_row,
                    "available_headers": headers
                }
            
            # STEP 4: Process Rows
            logger.info("=" * 60)
            logger.info("📝 PROCESSING ROWS (BULK INSERT)")
            logger.info("=" * 60)
            
            processor = FastBatchProcessor(db, field_to_column, batch_id, source_filename)
            processor.skip_dups = skip_dups
            processor.update_existing = update_existing_rows
            
            processed_count = 0
            rows = df.to_dict('records')
            
            for idx, row in enumerate(rows):
                row_number = idx + 2 + header_row
                
                try:
                    dn_col = field_to_column.get('dn_no')
                    dn_raw = row.get(dn_col) if dn_col else None
                    dn_no = normalize_dn(str(dn_raw)) if dn_raw else None
                    
                    if not dn_no:
                        validation_errors.append(f"Row {row_number}: Missing DN NO")
                        processor.failed_count += 1
                        continue
                    
                    mat_col = field_to_column.get('material_no')
                    mat_raw = row.get(mat_col) if mat_col else None
                    material_no = normalize_string(mat_raw)
                    
                    if not material_no:
                        validation_errors.append(f"Row {row_number}: Missing Material NO")
                        processor.failed_count += 1
                        continue
                    
                    row_data = {
                        'dn_no': dn_no,
                        'material_no': material_no,
                        'order_type': normalize_string(row.get(field_to_column.get('order_type'))),
                        'division': normalize_string(row.get(field_to_column.get('division'))),
                        'customer_name': normalize_string(row.get(field_to_column.get('customer_name'))),
                        'customer_model': normalize_string(row.get(field_to_column.get('customer_model'))),
                        'customer_code': normalize_string(row.get(field_to_column.get('customer_code'))),
                        'dealer_code': normalize_string(row.get(field_to_column.get('dealer_code'))),
                        'warehouse': normalize_string(row.get(field_to_column.get('warehouse'))),
                        'warehouse_code': normalize_string(row.get(field_to_column.get('warehouse_code'))),
                        'ship_to_city': normalize_string(row.get(field_to_column.get('ship_to_city'))),
                        'delivery_location': normalize_string(row.get(field_to_column.get('delivery_location'))),
                        'sales_office': normalize_string(row.get(field_to_column.get('sales_office'))),
                        'sales_manager': normalize_string(row.get(field_to_column.get('sales_manager'))),
                        'dn_work': normalize_string(row.get(field_to_column.get('dn_work'))),
                        'storage_location': normalize_string(row.get(field_to_column.get('storage_location'))),
                        'remarks': normalize_string(row.get(field_to_column.get('remarks'))),
                        'dn_qty': parse_quantity(row.get(field_to_column.get('dn_qty'))),
                        'dn_amount': parse_amount(row.get(field_to_column.get('dn_amount'))),
                        'dn_create_date': parse_date(row.get(field_to_column.get('dn_create_date'))),
                        'good_issue_date': parse_date(row.get(field_to_column.get('good_issue_date'))),
                        'pod_date': parse_date(row.get(field_to_column.get('pod_date')))
                    }
                    
                    processor.process_row(row_data, row_number)
                    processed_count += 1
                    
                    if processed_count % 25000 == 0:
                        logger.info(f"📊 Processed {processed_count:,} rows...")
                    
                except Exception as e:
                    processor.failed_count += 1
                    validation_errors.append(f"Row {row_number}: {str(e)}")
                    logger.warning(f"⚠️ Row {row_number} failed: {e}")
            
            logger.info("💾 Finalizing bulk import...")
            results = processor.finalize()
            
            duration = time.time() - start_time
            rows_per_second = total_rows / duration if duration > 0 else 0
            
            logger.info("=" * 60)
            logger.info("✅ IMPORT COMPLETED")
            logger.info("=" * 60)
            logger.info(f"  Duration: {duration:.2f}s")
            logger.info(f"  Speed: {rows_per_second:,.0f} rows/sec")
            logger.info(f"  Sheet: '{sheet_name}'")
            logger.info(f"  Header Row: {header_row}")
            logger.info(f"  Rows Read: {total_rows:,}")
            logger.info("")
            logger.info("  📊 RESULTS:")
            logger.info(f"  ✅ Inserted: {results['inserted_count']:,}")
            logger.info(f"  🔄 Updated: {results['updated_count']:,}")
            logger.info(f"  ⏭️ Skipped: {results['skipped_count']:,}")
            logger.info(f"  ❌ Failed: {results['failed_count']:,}")
            logger.info("")
            logger.info("  💰 TOTALS:")
            logger.info(f"  Revenue: PKR {results['total_revenue']:,.2f}")
            logger.info(f"  Units: {results['total_units']:,}")
            logger.info("=" * 60)
            
            return {
                "success": True,
                "batch_id": batch_id,
                "total_rows": total_rows,
                "inserted_count": results['inserted_count'],
                "updated_count": results['updated_count'],
                "skipped_count": results['skipped_count'],
                "failed_count": results['failed_count'],
                "total_revenue_imported": float(results['total_revenue']),
                "total_units_imported": results['total_units'],
                "validation_errors": results['validation_errors'][:20],
                "sheet_name": sheet_name,
                "header_row": header_row,
                "performance": {
                    "duration_seconds": round(duration, 2),
                    "rows_per_second": round(rows_per_second, 0),
                    "bulk_size": BULK_SIZE
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
