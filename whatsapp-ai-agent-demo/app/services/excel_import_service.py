# =====================================================================================================
# FILE: app/services/excel_import_service.py
# VERSION: v15.7 - FIXED TRAILING SPACES IN HEADERS
# PURPOSE: Ultra-fast Excel import with complete column mapping
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
# BLOCK 1: LIBRARY IMPORTS WITH FALLBACKS
# =====================================================================================================

try:
    import polars as pl
    HAS_POLARS = True
    logger.info("✅ Polars available - Fast Excel reading enabled")
except ImportError:
    HAS_POLARS = False
    logger.warning("⚠️ Polars not available - Using pandas for Excel reading")

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
    logger.info("✅ RapidFuzz available - Smart column mapping enabled")
except ImportError:
    HAS_RAPIDFUZZ = False
    logger.warning("⚠️ RapidFuzz not available - Using exact column mapping")

try:
    import psutil
    HAS_PSUTIL = True
    logger.info("✅ Psutil available - Memory monitoring enabled")
except ImportError:
    HAS_PSUTIL = False
    logger.warning("⚠️ Psutil not available - Memory monitoring disabled")

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# =====================================================================================================
# BLOCK 2: CONFIGURATION
# =====================================================================================================

BULK_SIZE            = 100000
HEADER_SCAN_ROWS     = 25
STRICT_MODE          = True
GC_INTERVAL          = 5
FUZZY_THRESHOLD      = 85

# =====================================================================================================
# BLOCK 3: EXCEPTIONS
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
# BLOCK 4: HEADER NORMALIZATION - PRESERVES ORIGINAL HEADER
# =====================================================================================================

def normalize_header(header: Any) -> str:
    """
    Normalize Excel header for consistent matching.
    IMPORTANT: This preserves the original header for exact matching.
    """
    if header is None:
        return ""
    
    # Convert to string and strip leading/trailing spaces
    normalized = str(header).strip()
    
    # Replace special separators with space
    normalized = re.sub(r'[_\-./\\#·•:;|]', ' ', normalized)
    
    # Replace non-breaking spaces and other whitespace
    normalized = normalized.replace('\u00a0', ' ')
    normalized = normalized.replace('\t', ' ')
    normalized = normalized.replace('\r', ' ')
    normalized = normalized.replace('\n', ' ')
    
    # Collapse multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    # Convert to lowercase for case-insensitive matching
    normalized = normalized.lower()
    
    return normalized

def get_exact_header(header: Any) -> str:
    """
    Get the exact header as it appears in Excel (preserves case and spaces).
    """
    if header is None:
        return ""
    return str(header).strip()

# =====================================================================================================
# BLOCK 5: WORKSHEET DETECTION
# =====================================================================================================

def detect_worksheet(file_path: str) -> Tuple[str, int, Dict[str, Any]]:
    """Find the worksheet containing delivery data."""
    logger.info("=" * 60)
    logger.info("🔍 WORKSHEET DETECTION v15.7")
    logger.info("=" * 60)
    
    try:
        xl = pd.ExcelFile(file_path, engine='openpyxl')
        sheet_names = xl.sheet_names
    except Exception as e:
        logger.error(f"❌ Failed to read Excel file: {e}")
        raise
    
    logger.info(f"📋 Found {len(sheet_names)} sheets: {sheet_names}")
    
    summary_indicators = ['sum s', 'sum', 'summary', 'total', 'grand total', 'report', 'overview']
    delivery_indicators = ['pgi', 'delivery', 'dn', 'data']
    
    best_sheet       = None
    best_score       = 0
    best_header_row  = 0
    best_info        = {}
    skipped_sheets   = []
    
    for sheet_name in sheet_names:
        if sheet_name.startswith('_') or sheet_name.startswith('$'):
            skipped_sheets.append(f"{sheet_name} (hidden)")
            continue
        
        sheet_name_lower = sheet_name.lower()
        
        is_summary = any(ind in sheet_name_lower for ind in summary_indicators)
        if is_summary:
            skipped_sheets.append(f"{sheet_name} (summary)")
            logger.info(f"  ⏭️ Skipping summary sheet: '{sheet_name}'")
            continue
        
        is_delivery = any(ind in sheet_name_lower for ind in delivery_indicators)
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
                skipped_sheets.append(f"{sheet_name} (empty)")
                continue
            
            header_row, score, matched_headers = detect_header_row(df_sample)
            
            logger.info(f"    📊 Header score: {score}")
            logger.info(f"    📋 Sample headers: {matched_headers[:5] if matched_headers else 'None'}")
            
            has_dn    = any('dn no' == normalize_header(h) for h in matched_headers)
            has_material = any('material no' == normalize_header(h) for h in matched_headers)
            has_amount   = any('dn amount' == normalize_header(h) for h in matched_headers)
            has_qty      = any('dn qty' == normalize_header(h) for h in matched_headers)
            
            if is_delivery:
                score += 50
                logger.info(f"    ✅ Found delivery sheet indicator")
            
            if has_dn and has_material:
                score += 30
                logger.info(f"    ✅ Found DN NO and Material NO")
            
            if has_amount:
                score += 10
                logger.info(f"    ✅ Found DN amount")
            
            if has_qty:
                score += 10
                logger.info(f"    ✅ Found DN Qty")
            
            if score > best_score:
                best_score      = score
                best_sheet      = sheet_name
                best_header_row = header_row
                best_info       = {
                    'score'          : score,
                    'matched_headers': matched_headers,
                    'is_delivery'    : is_delivery,
                    'has_dn'         : has_dn,
                    'has_material'   : has_material,
                    'has_amount'     : has_amount,
                    'has_qty'        : has_qty,
                }
                logger.info(f"    ✅ New best sheet: '{sheet_name}' (score: {score})")
                
        except Exception as e:
            logger.warning(f"    ❌ Error reading sheet '{sheet_name}': {e}")
            continue
    
    if skipped_sheets:
        logger.info(f"  ⏭️ Skipped sheets: {skipped_sheets}")
    
    if best_sheet is None:
        raise WorksheetNotFoundError(f"No worksheet with delivery data found. Available sheets: {sheet_names}")
    
    # Force switch from Sum S to data sheet
    if best_sheet == "Sum S":
        for sheet_name in sheet_names:
            if "PGI" in sheet_name or "Data" in sheet_name:
                logger.info(f"🔄 Forcing switch from 'Sum S' to '{sheet_name}'")
                best_sheet      = sheet_name
                best_header_row = 0
                break
    
    logger.info("=" * 60)
    logger.info(f"✅ SELECTED SHEET: '{best_sheet}'")
    logger.info(f"   Score: {best_score}")
    logger.info(f"   Header Row: {best_header_row}")
    logger.info("=" * 60)
    
    return best_sheet, best_header_row, best_info

# =====================================================================================================
# BLOCK 6: HEADER DETECTION
# =====================================================================================================

def detect_header_row(df: pd.DataFrame, max_rows: int = HEADER_SCAN_ROWS) -> Tuple[int, int, List[str]]:
    """Detect header row using pandas"""
    if len(df) == 0:
        return 0, 0, []
    
    header_keywords = {
        'dn no'        : 3, 'dn'       : 2, 'material no' : 3, 'material' : 2,
        'order type'   : 2, 'customer model' : 2, 'warehouse' : 2,
        'ship to city' : 2, 'dn amount' : 2, 'dn qty'     : 2,
        'division'     : 1, 'sales office' : 1, 'sales manager' : 1,
        'storage'      : 1, 'dn create date' : 1, 'good issue date' : 1,
        'pod date'     : 1, 'work'     : 1, 'remarks'     : 1,
    }
    
    best_score  = 0
    best_row    = 0
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
            best_score  = score
            best_row    = row_idx
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
# BLOCK 7: SMART COLUMN MAPPER - v15.7 WITH TRAILING SPACES FIX
# =====================================================================================================

class SmartColumnMapper:
    """
    Intelligent column mapping with exact matching.
    Includes ALL variations including trailing spaces.
    
    # | Excel Column       | PostgreSQL Column  | Status
    # | ------------------ | ------------------ | --------
    # | 1. Order type      | order_type         | ✅ Direct
    # | 2. DN NO           | dn_no              | ✅ Direct
    # | 3. DN amount       | dn_amount          | ✅ Direct
    # | 4. DN Qty          | dn_qty             | ✅ Direct
    # | 5. DN Work         | dn_work            | ✅ Direct
    # | 6. Division        | division           | ✅ Direct
    # | 7. Material NO     | material_no        | ✅ Direct
    # | 8. Customer Model  | customer_model     | ✅ Direct
    # | 9. sales office    | sales_office       | ✅ Direct
    # |10. Sold-to-party Name | customer_name   | ✅ Direct
    # |11. Ship-to City    | ship_to_city       | ✅ Direct
    # |12. storage         | storage_location   | ✅ Direct
    # |13. Warehouse       | warehouse          | ✅ Direct
    # |14. DN Create date  | dn_create_date     | ✅ Direct
    # |15. Good issue date | good_issue_date    | ✅ Direct
    # |16. POD Date        | pod_date           | ✅ Direct
    # |17. Sales Manager   | sales_manager      | ✅ Direct
    """
    
    HEADER_MAP = {
        # ============================================================
        # 1. ORDER TYPE - COMPLETE (with and without trailing spaces)
        # ============================================================
        'Order type'        : 'order_type',
        'Order type '       : 'order_type',   # WITH trailing space
        'order type'        : 'order_type',
        'order type '       : 'order_type',   # WITH trailing space
        'Order Type'        : 'order_type',
        'Order Type '       : 'order_type',   # WITH trailing space
        'ORDER TYPE'        : 'order_type',
        'ORDER TYPE '       : 'order_type',   # WITH trailing space
        'order_type'        : 'order_type',
        'order_type '       : 'order_type',   # WITH trailing space
        'order'             : 'order_type',
        'order '            : 'order_type',   # WITH trailing space
        
        # ============================================================
        # 2. DN NO - COMPLETE (with and without trailing spaces)
        # ============================================================
        'DN NO'             : 'dn_no',
        'DN NO '            : 'dn_no',        # WITH trailing space
        'DN No'             : 'dn_no',
        'DN No '            : 'dn_no',        # WITH trailing space
        'dn no'             : 'dn_no',
        'dn no '            : 'dn_no',        # WITH trailing space
        'DN'                : 'dn_no',
        'DN '               : 'dn_no',        # WITH trailing space
        'dn_no'             : 'dn_no',
        'dn_no '            : 'dn_no',        # WITH trailing space
        'dn'                : 'dn_no',
        'dn '               : 'dn_no',        # WITH trailing space
        
        # ============================================================
        # 3. DN AMOUNT - COMPLETE (YOUR EXCEL USES "DN amount " WITH SPACE!)
        # ============================================================
        'DN amount'         : 'dn_amount',
        'DN amount '        : 'dn_amount',    # ✅ WITH trailing space - YOUR EXCEL
        'DN Amount'         : 'dn_amount',
        'DN Amount '        : 'dn_amount',    # ✅ WITH trailing space
        'dn amount'         : 'dn_amount',
        'dn amount '        : 'dn_amount',    # ✅ WITH trailing space
        'dn_amount'         : 'dn_amount',
        'dn_amount '        : 'dn_amount',    # ✅ WITH trailing space
        'amount'            : 'dn_amount',
        'amount '           : 'dn_amount',    # ✅ WITH trailing space
        'DN AMOUNT'         : 'dn_amount',
        'DN AMOUNT '        : 'dn_amount',    # ✅ WITH trailing space
        
        # ============================================================
        # 4. DN QTY - COMPLETE (YOUR EXCEL USES "DN Qty " WITH SPACE!)
        # ============================================================
        'DN Qty'            : 'dn_qty',
        'DN Qty '           : 'dn_qty',       # ✅ WITH trailing space - YOUR EXCEL
        'DN QTY'            : 'dn_qty',
        'DN QTY '           : 'dn_qty',       # ✅ WITH trailing space
        'dn qty'            : 'dn_qty',
        'dn qty '           : 'dn_qty',       # ✅ WITH trailing space
        'dn_qty'            : 'dn_qty',
        'dn_qty '           : 'dn_qty',       # ✅ WITH trailing space
        'qty'               : 'dn_qty',
        'qty '              : 'dn_qty',       # ✅ WITH trailing space
        'quantity'          : 'dn_qty',
        'quantity '         : 'dn_qty',       # ✅ WITH trailing space
        'DN QUANTITY'       : 'dn_qty',
        'DN QUANTITY '      : 'dn_qty',       # ✅ WITH trailing space
        
        # ============================================================
        # 5. DN WORK - COMPLETE (with and without trailing spaces)
        # ============================================================
        'DN Work'           : 'dn_work',
        'DN Work '          : 'dn_work',      # ✅ WITH trailing space
        'DN WORK'           : 'dn_work',
        'DN WORK '          : 'dn_work',      # ✅ WITH trailing space
        'dn work'           : 'dn_work',
        'dn work '          : 'dn_work',      # ✅ WITH trailing space
        'dn_work'           : 'dn_work',
        'dn_work '          : 'dn_work',      # ✅ WITH trailing space
        'work'              : 'dn_work',
        'work '             : 'dn_work',      # ✅ WITH trailing space
        'status'            : 'dn_work',
        'status '           : 'dn_work',      # ✅ WITH trailing space
        
        # ============================================================
        # 6. DIVISION - COMPLETE (with and without trailing spaces)
        # ============================================================
        'Division'          : 'division',
        'Division '         : 'division',     # ✅ WITH trailing space
        'division'          : 'division',
        'division '         : 'division',     # ✅ WITH trailing space
        'DIVISION'          : 'division',
        'DIVISION '         : 'division',     # ✅ WITH trailing space
        'div'               : 'division',
        'div '              : 'division',     # ✅ WITH trailing space
        
        # ============================================================
        # 7. MATERIAL NO - COMPLETE (with and without trailing spaces)
        # ============================================================
        'Material NO'       : 'material_no',
        'Material NO '      : 'material_no',  # ✅ WITH trailing space
        'Material No'       : 'material_no',
        'Material No '      : 'material_no',  # ✅ WITH trailing space
        'MATERIAL NO'       : 'material_no',
        'MATERIAL NO '      : 'material_no',  # ✅ WITH trailing space
        'material no'       : 'material_no',
        'material no '      : 'material_no',  # ✅ WITH trailing space
        'material_no'       : 'material_no',
        'material_no '      : 'material_no',  # ✅ WITH trailing space
        'material'          : 'material_no',
        'material '         : 'material_no',  # ✅ WITH trailing space
        'MATERIAL'          : 'material_no',
        'MATERIAL '         : 'material_no',  # ✅ WITH trailing space
        
        # ============================================================
        # 8. CUSTOMER MODEL - COMPLETE (with and without trailing spaces)
        # ============================================================
        'Customer Model'    : 'customer_model',
        'Customer Model '   : 'customer_model', # ✅ WITH trailing space
        'CUSTOMER MODEL'    : 'customer_model',
        'CUSTOMER MODEL '   : 'customer_model', # ✅ WITH trailing space
        'customer model'    : 'customer_model',
        'customer model '   : 'customer_model', # ✅ WITH trailing space
        'customer_model'    : 'customer_model',
        'customer_model '   : 'customer_model', # ✅ WITH trailing space
        'model'             : 'customer_model',
        'model '            : 'customer_model', # ✅ WITH trailing space
        'MODEL'             : 'customer_model',
        'MODEL '            : 'customer_model', # ✅ WITH trailing space
        
        # ============================================================
        # 9. SALES OFFICE - COMPLETE (with and without trailing spaces)
        # ============================================================
        'sales office'      : 'sales_office',
        'sales office '     : 'sales_office', # ✅ WITH trailing space - YOUR EXCEL
        'Sales office'      : 'sales_office',
        'Sales office '     : 'sales_office', # ✅ WITH trailing space
        'Sales Office'      : 'sales_office',
        'Sales Office '     : 'sales_office', # ✅ WITH trailing space
        'SALES OFFICE'      : 'sales_office',
        'SALES OFFICE '     : 'sales_office', # ✅ WITH trailing space
        'sales_office'      : 'sales_office',
        'sales_office '     : 'sales_office', # ✅ WITH trailing space
        'office'            : 'sales_office',
        'office '           : 'sales_office', # ✅ WITH trailing space
        
        # ============================================================
        # 10. SOLD-TO-PARTY NAME - COMPLETE (with and without trailing spaces)
        # ============================================================
        'Sold-to-party Name'   : 'customer_name',
        'Sold-to-party Name '  : 'customer_name', # ✅ WITH trailing space
        'Sold-to-party name'   : 'customer_name',
        'Sold-to-party name '  : 'customer_name', # ✅ WITH trailing space
        'Sold to Party Name'   : 'customer_name',
        'Sold to Party Name '  : 'customer_name', # ✅ WITH trailing space
        'SOLD TO PARTY NAME'   : 'customer_name',
        'SOLD TO PARTY NAME '  : 'customer_name', # ✅ WITH trailing space
        'customer name'        : 'customer_name',
        'customer name '       : 'customer_name', # ✅ WITH trailing space
        'Customer Name'        : 'customer_name',
        'Customer Name '       : 'customer_name', # ✅ WITH trailing space
        'customer_name'        : 'customer_name',
        'customer_name '       : 'customer_name', # ✅ WITH trailing space
        'dealer name'          : 'customer_name',
        'dealer name '         : 'customer_name', # ✅ WITH trailing space
        'customer'             : 'customer_name',
        'customer '            : 'customer_name', # ✅ WITH trailing space
        'dealer'               : 'customer_name',
        'dealer '              : 'customer_name', # ✅ WITH trailing space
        
        # ============================================================
        # 11. SHIP-TO CITY - COMPLETE (with and without trailing spaces)
        # ============================================================
        'Ship-to City'      : 'ship_to_city',
        'Ship-to City '     : 'ship_to_city', # ✅ WITH trailing space
        'Ship-to city'      : 'ship_to_city',
        'Ship-to city '     : 'ship_to_city', # ✅ WITH trailing space
        'Ship To City'      : 'ship_to_city',
        'Ship To City '     : 'ship_to_city', # ✅ WITH trailing space
        'SHIP-TO CITY'      : 'ship_to_city',
        'SHIP-TO CITY '     : 'ship_to_city', # ✅ WITH trailing space
        'ship to city'      : 'ship_to_city',
        'ship to city '     : 'ship_to_city', # ✅ WITH trailing space
        'ship_to_city'      : 'ship_to_city',
        'ship_to_city '     : 'ship_to_city', # ✅ WITH trailing space
        'city'              : 'ship_to_city',
        'city '             : 'ship_to_city', # ✅ WITH trailing space
        
        # ============================================================
        # 12. STORAGE - COMPLETE (with and without trailing spaces)
        # ============================================================
        'storage'           : 'storage_location',
        'storage '          : 'storage_location', # ✅ WITH trailing space - YOUR EXCEL
        'Storage'           : 'storage_location',
        'Storage '          : 'storage_location', # ✅ WITH trailing space
        'STORAGE'           : 'storage_location',
        'STORAGE '          : 'storage_location', # ✅ WITH trailing space
        'storage location'  : 'storage_location',
        'storage location ' : 'storage_location', # ✅ WITH trailing space
        'storage_location'  : 'storage_location',
        'storage_location ' : 'storage_location', # ✅ WITH trailing space
        'location'          : 'storage_location',
        'location '         : 'storage_location', # ✅ WITH trailing space
        
        # ============================================================
        # 13. WAREHOUSE - COMPLETE (with and without trailing spaces)
        # ============================================================
        'Warehouse'         : 'warehouse',
        'Warehouse '        : 'warehouse',    # ✅ WITH trailing space
        'warehouse'         : 'warehouse',
        'warehouse '        : 'warehouse',    # ✅ WITH trailing space
        'WAREHOUSE'         : 'warehouse',
        'WAREHOUSE '        : 'warehouse',    # ✅ WITH trailing space
        'ware house'        : 'warehouse',
        'ware house '       : 'warehouse',    # ✅ WITH trailing space
        'WH'                : 'warehouse',
        'WH '               : 'warehouse',    # ✅ WITH trailing space
        'wh'                : 'warehouse',
        'wh '               : 'warehouse',    # ✅ WITH trailing space
        
        # ============================================================
        # 14. DN CREATE DATE - COMPLETE (with and without trailing spaces)
        # ============================================================
        'DN Create date'    : 'dn_create_date',
        'DN Create date '   : 'dn_create_date', # ✅ WITH trailing space
        'DN Create Date'    : 'dn_create_date',
        'DN Create Date '   : 'dn_create_date', # ✅ WITH trailing space
        'DN create date'    : 'dn_create_date',
        'DN create date '   : 'dn_create_date', # ✅ WITH trailing space
        'dn create date'    : 'dn_create_date',
        'dn create date '   : 'dn_create_date', # ✅ WITH trailing space
        'dn_create_date'    : 'dn_create_date',
        'dn_create_date '   : 'dn_create_date', # ✅ WITH trailing space
        'create date'       : 'dn_create_date',
        'create date '      : 'dn_create_date', # ✅ WITH trailing space
        'created date'      : 'dn_create_date',
        'created date '     : 'dn_create_date', # ✅ WITH trailing space
        'order date'        : 'dn_create_date',
        'order date '       : 'dn_create_date', # ✅ WITH trailing space
        
        # ============================================================
        # 15. GOOD ISSUE DATE - COMPLETE (with and without trailing spaces)
        # ============================================================
        'Good issue date'   : 'good_issue_date',
        'Good issue date '  : 'good_issue_date', # ✅ WITH trailing space
        'Good Issue Date'   : 'good_issue_date',
        'Good Issue Date '  : 'good_issue_date', # ✅ WITH trailing space
        'GOOD ISSUE DATE'   : 'good_issue_date',
        'GOOD ISSUE DATE '  : 'good_issue_date', # ✅ WITH trailing space
        'good issue date'   : 'good_issue_date',
        'good issue date '  : 'good_issue_date', # ✅ WITH trailing space
        'good_issue_date'   : 'good_issue_date',
        'good_issue_date '  : 'good_issue_date', # ✅ WITH trailing space
        'PGI'               : 'good_issue_date',
        'PGI '              : 'good_issue_date', # ✅ WITH trailing space
        'pgi date'          : 'good_issue_date',
        'pgi date '         : 'good_issue_date', # ✅ WITH trailing space
        'dispatch date'     : 'good_issue_date',
        'dispatch date '    : 'good_issue_date', # ✅ WITH trailing space
        'ship date'         : 'good_issue_date',
        'ship date '        : 'good_issue_date', # ✅ WITH trailing space
        
        # ============================================================
        # 16. POD DATE - COMPLETE (with and without trailing spaces)
        # ============================================================
        'POD Date'          : 'pod_date',
        'POD Date '         : 'pod_date',     # ✅ WITH trailing space
        'POD date'          : 'pod_date',
        'POD date '         : 'pod_date',     # ✅ WITH trailing space
        'pod date'          : 'pod_date',
        'pod date '         : 'pod_date',     # ✅ WITH trailing space
        'pod_date'          : 'pod_date',
        'pod_date '         : 'pod_date',     # ✅ WITH trailing space
        'POD'               : 'pod_date',
        'POD '              : 'pod_date',     # ✅ WITH trailing space
        'proof of delivery' : 'pod_date',
        'proof of delivery ': 'pod_date',     # ✅ WITH trailing space
        'received date'     : 'pod_date',
        'received date '    : 'pod_date',     # ✅ WITH trailing space
        'confirmation date' : 'pod_date',
        'confirmation date ' : 'pod_date',    # ✅ WITH trailing space
        
        # ============================================================
        # 17. SALES MANAGER - COMPLETE (with and without trailing spaces)
        # ============================================================
        'Sales Manager'     : 'sales_manager',
        'Sales Manager '    : 'sales_manager', # ✅ WITH trailing space
        'SALES MANAGER'     : 'sales_manager',
        'SALES MANAGER '    : 'sales_manager', # ✅ WITH trailing space
        'sales manager'     : 'sales_manager',
        'sales manager '    : 'sales_manager', # ✅ WITH trailing space
        'sales_manager'     : 'sales_manager',
        'sales_manager '    : 'sales_manager', # ✅ WITH trailing space
        'manager'           : 'sales_manager',
        'manager '          : 'sales_manager', # ✅ WITH trailing space
        'sales rep'         : 'sales_manager',
        'sales rep '        : 'sales_manager', # ✅ WITH trailing space
        'representative'    : 'sales_manager',
        'representative '   : 'sales_manager', # ✅ WITH trailing space
    }
    
    REQUIRED_FIELDS = ['dn_no', 'material_no']
    
    @classmethod
    def map_headers(cls, headers: List[str]) -> Tuple[Dict[str, str], Dict[str, str], List[str], List[str]]:
        """Map Excel headers with exact matching"""
        field_to_column = {}
        column_to_field = {}
        unmapped = []
        
        logger.info("=" * 60)
        logger.info("📋 EXACT 17-COLUMN MAPPING v15.7")
        logger.info("=" * 60)
        
        # Log all headers for debugging
        logger.info("  📋 Excel Headers Found:")
        for h in headers:
            logger.info(f"    '{h}'")
        
        for header in headers:
            if header is None:
                continue
            
            # Try exact match first
            exact = get_exact_header(header)
            field = cls.HEADER_MAP.get(exact)
            
            if field:
                if field not in field_to_column:
                    field_to_column[field] = header
                    column_to_field[header] = field
                    logger.info(f"  ✅ EXACT: '{header}' -> {field}")
                continue
            
            # Try normalized match
            normalized = normalize_header(header)
            field = cls.HEADER_MAP.get(normalized)
            
            if field:
                if field not in field_to_column:
                    field_to_column[field] = header
                    column_to_field[header] = field
                    logger.info(f"  ✅ NORMALIZED: '{header}' -> {field}")
            else:
                unmapped.append(header)
                logger.warning(f"  ⚠️ UNMAPPED: '{header}'")
        
        # Try fuzzy matching for unmapped headers
        if HAS_RAPIDFUZZ and unmapped:
            logger.info("  🔍 Trying fuzzy matching...")
            for header in unmapped[:]:
                normalized = normalize_header(header)
                if not normalized:
                    continue
                
                best_match = None
                best_score = 0
                
                for key, field in cls.HEADER_MAP.items():
                    if field in field_to_column:
                        continue
                    
                    score = fuzz.ratio(normalized, key)
                    if score > FUZZY_THRESHOLD and score > best_score:
                        best_score = score
                        best_match = (key, field)
                
                if best_match:
                    key, field = best_match
                    field_to_column[field] = header
                    column_to_field[header] = field
                    unmapped.remove(header)
                    logger.info(f"  ✅ FUZZY: '{header}' -> {field} ({best_score}%)")
        
        missing = [f for f in cls.REQUIRED_FIELDS if f not in field_to_column]
        
        logger.info("=" * 60)
        logger.info(f"  ✅ Mapped: {len(field_to_column)} columns")
        logger.info(f"  ⚠️ Unmapped: {len(unmapped)} columns")
        if missing:
            logger.error(f"  ❌ Missing required: {missing}")
        if unmapped:
            logger.warning(f"  ⚠️ Unmapped headers: {unmapped}")
        logger.info("=" * 60)
        
        return field_to_column, column_to_field, unmapped, missing

# =====================================================================================================
# BLOCK 8: FAST EXCEL READING
# =====================================================================================================

def read_excel_fast(file_path: str, sheet_name: str, header_row: int):
    """Read Excel using Polars or fallback to pandas"""
    
    if HAS_POLARS:
        try:
            try:
                df = pl.read_excel(
                    file_path,
                    sheet_name=sheet_name,
                    header_row=header_row,
                    engine='calamine'
                )
                logger.info("⚡ Using Polars with calamine engine")
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
    
    logger.info("📖 Using pandas")
    try:
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row, engine='openpyxl')
        logger.info(f"✅ Read {len(df)} rows with pandas")
        return df
    except Exception as e:
        logger.error(f"❌ Failed to read Excel: {e}")
        raise

# =====================================================================================================
# BLOCK 9: STATUS ENGINE
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
# BLOCK 10: DATA PARSING FUNCTIONS
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
# BLOCK 11: FAST BATCH PROCESSOR
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
        existing.dn_work          = row_data['dn_work']
        existing.order_type       = row_data['order_type']
        existing.division         = row_data['division']
        existing.customer_code    = row_data['customer_code']
        existing.dealer_code      = row_data['dealer_code']
        existing.customer_name    = row_data['customer_name']
        existing.customer_model   = row_data['customer_model']
        existing.storage_location = row_data['storage_location']
        existing.sales_office     = row_data['sales_office']
        existing.sales_manager    = row_data['sales_manager']
        existing.ship_to_city     = row_data['ship_to_city']
        existing.warehouse        = row_data['warehouse']
        existing.warehouse_code   = row_data['warehouse_code']
        existing.delivery_location = row_data['delivery_location']
        existing.dn_qty           = row_data['dn_qty']
        existing.dn_amount        = float(row_data['dn_amount']) if row_data['dn_amount'] else None
        existing.dn_create_date   = row_data['dn_create_date']
        existing.good_issue_date  = row_data['good_issue_date']
        existing.pod_date         = row_data['pod_date']
        existing.remarks          = row_data['remarks']
        existing.delivery_status  = status['delivery_status']
        existing.pgi_status       = status['pgi_status']
        existing.pod_status       = status['pod_status']
        existing.pending_flag     = status['pending_flag']
        existing.source_file      = self.source_filename
        existing.upload_batch_id  = self.batch_id
        existing.updated_at       = datetime.utcnow()
    
    def _add_to_bulk_buffer(self, row_data, status):
        self.bulk_buffer.append({
            'dn_no'              : row_data['dn_no'],
            'dn_work'            : row_data['dn_work'],
            'order_type'         : row_data['order_type'],
            'division'           : row_data['division'],
            'customer_code'      : row_data['customer_code'],
            'dealer_code'        : row_data['dealer_code'],
            'customer_name'      : row_data['customer_name'],
            'customer_model'     : row_data['customer_model'],
            'material_no'        : row_data['material_no'],
            'storage_location'   : row_data['storage_location'],
            'sales_office'       : row_data['sales_office'],
            'sales_manager'      : row_data['sales_manager'],
            'ship_to_city'       : row_data['ship_to_city'],
            'warehouse'          : row_data['warehouse'],
            'warehouse_code'     : row_data['warehouse_code'],
            'delivery_location'  : row_data['delivery_location'],
            'dn_qty'             : row_data['dn_qty'],
            'dn_amount'          : float(row_data['dn_amount']) if row_data['dn_amount'] else None,
            'dn_create_date'     : row_data['dn_create_date'],
            'good_issue_date'    : row_data['good_issue_date'],
            'pod_date'           : row_data['pod_date'],
            'remarks'            : row_data['remarks'],
            'delivery_status'    : status['delivery_status'],
            'pgi_status'         : status['pgi_status'],
            'pod_status'         : status['pod_status'],
            'pending_flag'       : status['pending_flag'],
            'source_file'        : self.source_filename,
            'upload_batch_id'    : self.batch_id,
            'imported_at'        : datetime.utcnow()
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
            'inserted_count'   : self.inserted_count,
            'updated_count'    : self.updated_count,
            'skipped_count'    : self.skipped_count,
            'failed_count'     : self.failed_count,
            'total_revenue'    : self.total_revenue,
            'total_units'      : self.total_units,
            'validation_errors': self.validation_errors
        }

# =====================================================================================================
# BLOCK 12: EXCEL IMPORT SERVICE - v15.7 FINAL
# =====================================================================================================

class ExcelImportService:
    """Ultra-fast Excel import with exact 17-column mapping - v15.7"""
    
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
        logger.info("⚡ EXCEL IMPORT v15.7 - FIXED TRAILING SPACES")
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
            
            df = df.dropna(how='all')
            df = df.dropna(axis=1, how='all')
            
            total_rows = len(df)
            logger.info(f"📄 Read {total_rows:,} rows, {len(df.columns)} columns")
            
            # Log ALL column names with their exact representation
            logger.info("📋 Excel Columns Found (exact):")
            for i, col in enumerate(df.columns):
                logger.info(f"    {i+1}. '{col}'")
            
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
                    # Get DN NO (Column 2)
                    dn_col = field_to_column.get('dn_no')
                    dn_raw = row.get(dn_col) if dn_col else None
                    dn_no = normalize_dn(str(dn_raw)) if dn_raw else None
                    
                    if not dn_no:
                        validation_errors.append(f"Row {row_number}: Missing DN NO")
                        processor.failed_count += 1
                        continue
                    
                    # Get Material NO (Column 7)
                    mat_col = field_to_column.get('material_no')
                    mat_raw = row.get(mat_col) if mat_col else None
                    material_no = normalize_string(mat_raw)
                    
                    if not material_no:
                        validation_errors.append(f"Row {row_number}: Missing Material NO")
                        processor.failed_count += 1
                        continue
                    
                    # Build row data with all 17 columns
                    row_data = {
                        # 1. Order type
                        'order_type'       : normalize_string(row.get(field_to_column.get('order_type'))),
                        # 2. DN NO
                        'dn_no'            : dn_no,
                        # 3. DN amount
                        'dn_amount'        : parse_amount(row.get(field_to_column.get('dn_amount'))),
                        # 4. DN Qty
                        'dn_qty'           : parse_quantity(row.get(field_to_column.get('dn_qty'))),
                        # 5. DN Work
                        'dn_work'          : normalize_string(row.get(field_to_column.get('dn_work'))),
                        # 6. Division
                        'division'         : normalize_string(row.get(field_to_column.get('division'))),
                        # 7. Material NO
                        'material_no'      : material_no,
                        # 8. Customer Model
                        'customer_model'   : normalize_string(row.get(field_to_column.get('customer_model'))),
                        # 9. sales office
                        'sales_office'     : normalize_string(row.get(field_to_column.get('sales_office'))),
                        # 10. Sold-to-party Name
                        'customer_name'    : normalize_string(row.get(field_to_column.get('customer_name'))),
                        # 11. Ship-to City
                        'ship_to_city'     : normalize_string(row.get(field_to_column.get('ship_to_city'))),
                        # 12. storage
                        'storage_location' : normalize_string(row.get(field_to_column.get('storage_location'))),
                        # 13. Warehouse
                        'warehouse'        : normalize_string(row.get(field_to_column.get('warehouse'))),
                        # 14. DN Create date
                        'dn_create_date'   : parse_date(row.get(field_to_column.get('dn_create_date'))),
                        # 15. Good issue date
                        'good_issue_date'  : parse_date(row.get(field_to_column.get('good_issue_date'))),
                        # 16. POD Date
                        'pod_date'         : parse_date(row.get(field_to_column.get('pod_date'))),
                        # 17. Sales Manager
                        'sales_manager'    : normalize_string(row.get(field_to_column.get('sales_manager'))),
                        # Extra fields (NULL by default)
                        'customer_code'    : None,
                        'dealer_code'      : None,
                        'warehouse_code'   : None,
                        'delivery_location': None,
                        'remarks'          : None,
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
# BLOCK 13: EXPORTS
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
# BLOCK 14: MODULE INITIALIZATION LOGGING
# =====================================================================================================

logger.info("=" * 60)
logger.info("📊 EXCEL IMPORT SERVICE v15.7")
logger.info("=" * 60)
logger.info("")
logger.info("  SERVICE DETAILS:")
logger.info("  ✅ Version: 15.7 (Fixed Trailing Spaces)")
logger.info("  ✅ Service: ExcelImportService")
logger.info("  ✅ Status: PRODUCTION READY")
logger.info("")
logger.info("  COLUMN MAPPING (17 COLUMNS):")
logger.info("  1.  Order type         -> order_type")
logger.info("  2.  DN NO              -> dn_no")
logger.info("  3.  DN amount          -> dn_amount")
logger.info("  4.  DN Qty             -> dn_qty")
logger.info("  5.  DN Work            -> dn_work")
logger.info("  6.  Division           -> division")
logger.info("  7.  Material NO        -> material_no")
logger.info("  8.  Customer Model     -> customer_model")
logger.info("  9.  sales office       -> sales_office")
logger.info(" 10.  Sold-to-party Name -> customer_name")
logger.info(" 11.  Ship-to City       -> ship_to_city")
logger.info(" 12.  storage            -> storage_location")
logger.info(" 13.  Warehouse          -> warehouse")
logger.info(" 14.  DN Create date     -> dn_create_date")
logger.info(" 15.  Good issue date    -> good_issue_date")
logger.info(" 16.  POD Date           -> pod_date")
logger.info(" 17.  Sales Manager      -> sales_manager")
logger.info("")
logger.info("  FIXED:")
logger.info("  ✅ Added ALL headers with trailing spaces")
logger.info("  ✅ Added ALL headers without trailing spaces")
logger.info("  ✅ Added case variations")
logger.info("  ✅ Added fuzzy matching fallback")
logger.info("")
logger.info("  STATUS: ✅ ENTERPRISE PRODUCTION READY")
logger.info("=" * 60)

# =====================================================================================================
# END OF FILE
# =====================================================================================================
