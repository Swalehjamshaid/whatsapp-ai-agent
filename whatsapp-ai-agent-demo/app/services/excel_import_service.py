# =====================================================================================================
# FILE: app/services/excel_import_service.py
# VERSION: v6.0 ENTERPRISE COLUMN MAPPING
# PURPOSE: Enterprise-grade Excel import with smart header detection and normalization
# =====================================================================================================

import pandas as pd
import logging
import uuid
import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional, List, Tuple, Set, Union
from sqlalchemy.orm import Session
from sqlalchemy import text
import time
import traceback

# Pydantic v1/v2 compatible
try:
    from pydantic import BaseModel, ConfigDict
    PYDANTIC_V2 = True
except ImportError:
    from pydantic import BaseModel
    PYDANTIC_V2 = False

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# =====================================================================================================
# BLOCK 1: CONFIGURATION
# =====================================================================================================

BATCH_SIZE = 1000
MAX_ROWS = 100000
VERIFICATION_SAMPLE_SIZE = 5
DEBUG_MODE = False
STRICT_MODE = True
VERIFY_ALL_ROWS = False
HEADER_DETECTION_ROWS = 10

# =====================================================================================================
# BLOCK 2: EXCEPTIONS
# =====================================================================================================

class ImportValidationError(Exception):
    """Raised when validation fails"""
    pass

class DataLossError(Exception):
    """Raised when data loss is detected"""
    pass

class VerificationError(Exception):
    """Raised when verification fails - should bubble up to caller"""
    pass

class HeaderDetectionError(Exception):
    """Raised when header row cannot be detected"""
    pass

# =====================================================================================================
# BLOCK 3: DATA CLASSES - PYDANTIC COMPATIBLE
# =====================================================================================================

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
    header_detection_row: int = 0
    header_matches: Dict[str, str] = {}
    unmapped_headers: List[str] = []

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

# =====================================================================================================
# BLOCK 4: HEADER NORMALIZATION
# =====================================================================================================

def normalize_header(header: Any) -> str:
    """
    Normalize Excel header for consistent mapping.
    
    Converts to string, removes special characters, collapses spaces,
    and converts to lowercase.
    
    Examples:
        "DN NO" -> "dn no"
        "DN_NO" -> "dn no"
        "DN-NO" -> "dn no"
        "DN.NO" -> "dn no"
        " Material NO " -> "material no"
        "Sales Office" -> "sales office"
        "Sold-to-party Name" -> "sold to party name"
    
    Args:
        header: Raw header value from Excel
        
    Returns:
        Normalized header string
    """
    if header is None:
        return ""
    
    # Convert to string
    normalized = str(header)
    
    # Trim spaces
    normalized = normalized.strip()
    
    # Replace common separators with spaces
    separators = ['_', '-', '.', '/', '\\', '#', '&', '@', '|', '·']
    for sep in separators:
        normalized = normalized.replace(sep, ' ')
    
    # Replace non-breaking spaces and other whitespace characters
    normalized = normalized.replace('\u00a0', ' ')  # non-breaking space
    normalized = normalized.replace('\t', ' ')      # tab
    normalized = normalized.replace('\r', ' ')      # carriage return
    normalized = normalized.replace('\n', ' ')      # newline
    
    # Remove extra spaces
    normalized = ' '.join(normalized.split())
    
    # Convert to lowercase
    normalized = normalized.lower()
    
    return normalized

# =====================================================================================================
# BLOCK 5: ENTERPRISE COLUMN MAPPER
# =====================================================================================================

class ColumnMapper:
    """
    Enterprise-grade column mapper with smart header normalization.
    
    Supports:
        - Exact matching after normalization
        - Multiple variants for each field
        - Auto-detection of header row
        - Detailed mapping diagnostics
    """
    
    # Enterprise mapping: normalized header -> database field
    NORMALIZED_MAP = {
        # DN Number - Primary Key
        'dn no': 'dn_no',
        'dn': 'dn_no',
        'dn#': 'dn_no',
        'delivery note': 'dn_no',
        'delivery note no': 'dn_no',
        'delivery note number': 'dn_no',
        'delivery number': 'dn_no',
        'd n no': 'dn_no',
        'd n': 'dn_no',
        
        # DN Work
        'dn work': 'dn_work',
        'dn work no': 'dn_work',
        'dn work number': 'dn_work',
        'work': 'dn_work',
        'work order': 'dn_work',
        'work no': 'dn_work',
        
        # Order Type
        'order type': 'order_type',
        'order': 'order_type',
        'ordertype': 'order_type',
        'type': 'order_type',
        
        # Division
        'division': 'division',
        'div': 'division',
        
        # Customer Code
        'customer code': 'customer_code',
        'customer code no': 'customer_code',
        'customer no': 'customer_code',
        'cust code': 'customer_code',
        'account code': 'customer_code',
        
        # Dealer Code
        'dealer code': 'dealer_code',
        'dealer code no': 'dealer_code',
        'dealer no': 'dealer_code',
        'distributor code': 'dealer_code',
        
        # Customer Name (Sold-to-party)
        'sold to party name': 'customer_name',
        'sold-to-party name': 'customer_name',
        'sold to party': 'customer_name',
        'sold-to-party': 'customer_name',
        'customer name': 'customer_name',
        'customer': 'customer_name',
        'party name': 'customer_name',
        'dealer name': 'customer_name',
        
        # Customer Model
        'customer model': 'customer_model',
        'model': 'customer_model',
        'model name': 'customer_model',
        'product model': 'customer_model',
        'model no': 'customer_model',
        
        # Material Number
        'material no': 'material_no',
        'material': 'material_no',
        'material#': 'material_no',
        'material number': 'material_no',
        'material code': 'material_no',
        'product no': 'material_no',
        'product number': 'material_no',
        'item no': 'material_no',
        'sku': 'material_no',
        
        # Storage Location
        'storage': 'storage_location',
        'storage location': 'storage_location',
        'storage no': 'storage_location',
        'storagelocation': 'storage_location',
        'bin': 'storage_location',
        
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
        'representative': 'sales_manager',
        
        # Ship-to City
        'ship to city': 'ship_to_city',
        'ship-to city': 'ship_to_city',
        'ship-to-city': 'ship_to_city',
        'shipcity': 'ship_to_city',
        'city': 'ship_to_city',
        'destination city': 'ship_to_city',
        'ship to': 'ship_to_city',
        
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
        'dn amount ': 'dn_amount',  # trailing space handling
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
        
        # Remarks
        'remarks': 'remarks',
        'remark': 'remarks',
        'note': 'remarks',
        'notes': 'remarks',
        'comments': 'remarks',
        'comment': 'remarks',
    }
    
    # Required fields that must be mapped
    REQUIRED_FIELDS = ['dn_no', 'material_no']
    
    @classmethod
    def map_columns(cls, excel_columns: List[str]) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
        """
        Map Excel columns to database fields using normalized matching.
        
        Args:
            excel_columns: List of Excel column headers
            
        Returns:
            Tuple of (mapping, field_to_column, unmapped_columns)
        """
        mapping = {}
        field_to_column = {}
        unmapped = []
        
        logger.info("=" * 80)
        logger.info("📋 ENTERPRISE COLUMN MAPPING")
        logger.info("=" * 80)
        
        # Build normalized mapping
        normalized_headers = {}
        for col in excel_columns:
            normalized = normalize_header(col)
            normalized_headers[normalized] = col
            logger.info(f"  Original: '{col}' → Normalized: '{normalized}'")
        
        # Map each normalized header
        for normalized, original in normalized_headers.items():
            if normalized in cls.NORMALIZED_MAP:
                field = cls.NORMALIZED_MAP[normalized]
                mapping[original] = field
                field_to_column[field] = original
                logger.info(f"  ✅ '{original}' → '{field}'")
            else:
                unmapped.append(original)
                logger.warning(f"  ❌ '{original}' → UNMAPPED")
        
        logger.info("=" * 80)
        logger.info(f"  📊 Mapped: {len(mapping)} columns")
        logger.info(f"  📊 Unmapped: {len(unmapped)} columns")
        
        if unmapped:
            logger.warning(f"  ⚠️ Unmapped headers: {unmapped[:10]}...")
        
        logger.info("=" * 80)
        
        return mapping, field_to_column, unmapped
    
    @classmethod
    def get_field_to_column(cls, mapping: Dict[str, str]) -> Dict[str, str]:
        """Convert column->field mapping to field->column mapping"""
        field_to_col = {}
        for col, field in mapping.items():
            field_to_col[field] = col
        return field_to_col
    
    @classmethod
    def validate_mapping(cls, field_to_column: Dict[str, str], unmapped: List[str]) -> Dict[str, Any]:
        """
        Validate the mapping and return detailed diagnostics.
        
        Returns:
            Dict with validation results and diagnostics
        """
        result = {
            'valid': True,
            'missing_required': [],
            'mapped_fields': list(field_to_column.keys()),
            'unmapped_headers': unmapped,
            'field_to_column': field_to_column
        }
        
        # Check required fields
        for required in cls.REQUIRED_FIELDS:
            if required not in field_to_column:
                result['valid'] = False
                result['missing_required'].append(required)
        
        return result
    
    @classmethod
    def get_header_matches(cls, excel_columns: List[str]) -> Dict[str, Dict[str, str]]:
        """
        Get detailed header match report.
        
        Returns:
            Dict with original, normalized, mapped, and status for each header
        """
        matches = {}
        
        for col in excel_columns:
            normalized = normalize_header(col)
            mapped = cls.NORMALIZED_MAP.get(normalized, None)
            status = 'SUCCESS' if mapped else 'UNMAPPED'
            
            matches[col] = {
                'original': col,
                'normalized': normalized,
                'mapped': mapped,
                'status': status
            }
        
        return matches

# =====================================================================================================
# BLOCK 6: HEADER DETECTION
# =====================================================================================================

class HeaderDetector:
    """
    Automatically detect header row in Excel files.
    
    Scans first N rows to find the row containing column headers
    like 'DN NO', 'Material NO', etc.
    """
    
    # Keywords that indicate a header row
    HEADER_KEYWORDS = [
        'dn no', 'dn', 'dn#', 'delivery note',
        'material no', 'material', 'sku',
        'customer model', 'model',
        'order type', 'order',
        'warehouse', 'wh',
        'city', 'ship to',
        'amount', 'qty', 'quantity'
    ]
    
    @classmethod
    def detect_header_row(cls, df: pd.DataFrame, max_rows: int = HEADER_DETECTION_ROWS) -> int:
        """
        Detect which row contains column headers.
        
        Args:
            df: Pandas DataFrame
            max_rows: Number of rows to scan
            
        Returns:
            Index of the header row (0-based)
            
        Raises:
            HeaderDetectionError: If no header row can be detected
        """
        if len(df) == 0:
            raise HeaderDetectionError("Empty DataFrame - cannot detect headers")
        
        logger.info("=" * 80)
        logger.info("🔍 HEADER DETECTION")
        logger.info("=" * 80)
        
        rows_to_check = min(max_rows, len(df))
        best_score = 0
        best_row = 0
        
        for row_idx in range(rows_to_check):
            row_data = df.iloc[row_idx]
            score = cls._score_row(row_data)
            
            logger.info(f"  Row {row_idx}: Score = {score}")
            
            if score > best_score:
                best_score = score
                best_row = row_idx
        
        if best_score < 2:
            logger.warning(f"  ⚠️ Low confidence score ({best_score}) - using row 0")
            # Try to find any row with recognizable headers
            for row_idx in range(rows_to_check):
                row_data = df.iloc[row_idx]
                if cls._has_header_indicators(row_data):
                    logger.info(f"  ✅ Found header indicators at row {row_idx}")
                    return row_idx
            
            # Default to row 0 if nothing found
            logger.info("  ⚠️ No clear header row detected - using row 0")
            return 0
        
        logger.info(f"  ✅ Detected header at row {best_row} (score: {best_score})")
        logger.info("=" * 80)
        
        return best_row
    
    @classmethod
    def _score_row(cls, row_data: pd.Series) -> int:
        """Score a row based on how likely it is to be a header row."""
        score = 0
        total_headers = 0
        matched_headers = 0
        
        for value in row_data:
            if value is None:
                continue
            if not isinstance(value, str):
                continue
            
            normalized = normalize_header(value)
            if not normalized:
                continue
            
            total_headers += 1
            
            # Check if this normalized header is in our map
            if normalized in ColumnMapper.NORMALIZED_MAP:
                matched_headers += 1
                score += 2
            elif any(keyword in normalized for keyword in cls.HEADER_KEYWORDS):
                score += 1
        
        return score
    
    @classmethod
    def _has_header_indicators(cls, row_data: pd.Series) -> bool:
        """Check if a row contains any header indicators."""
        for value in row_data:
            if value is None:
                continue
            if not isinstance(value, str):
                continue
            
            normalized = normalize_header(value)
            if not normalized:
                continue
            
            if normalized in ColumnMapper.NORMALIZED_MAP:
                return True
            if any(keyword in normalized for keyword in cls.HEADER_KEYWORDS):
                return True
        
        return False

# =====================================================================================================
# BLOCK 7: NORMALIZATION FUNCTIONS
# =====================================================================================================

def normalize_string(value: Any) -> Optional[str]:
    """Normalize string value."""
    if value is None:
        return None
    if isinstance(value, str):
        normalized = ' '.join(value.strip().split())
        return normalized if normalized else None
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip() if str(value) else None

def parse_amount_decimal(value: Any) -> Optional[Decimal]:
    """Parse amount from various formats."""
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
    """Parse quantity from various formats."""
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
    """Parse date from various Excel formats."""
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
    """Normalize DN number - extract digits only."""
    if not dn_no:
        return ""
    return re.sub(r'[^0-9]', '', dn_no.strip())

# =====================================================================================================
# BLOCK 8: STATUS ENGINE
# =====================================================================================================

class StatusEngine:
    """Derive delivery status from dates."""
    
    @staticmethod
    def derive_status(
        dn_create_date: Optional[date],
        good_issue_date: Optional[date],
        pod_date: Optional[date]
    ) -> Dict[str, Any]:
        has_dn_create = dn_create_date is not None
        has_pgi = good_issue_date is not None
        has_pod = pod_date is not None

        if has_pod and has_pgi and has_dn_create:
            return {
                'delivery_status': 'Delivered',
                'pgi_status': 'Completed',
                'pod_status': 'Completed',
                'pending_flag': False
            }
        elif has_pgi and has_dn_create:
            return {
                'delivery_status': 'Dispatched',
                'pgi_status': 'Completed',
                'pod_status': 'Pending',
                'pending_flag': True
            }
        elif has_dn_create:
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
# BLOCK 9: VERIFICATION ENGINE
# =====================================================================================================

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

# =====================================================================================================
# BLOCK 10: EXCEL IMPORT SERVICE - v6.0
# =====================================================================================================

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
        logger.info("📊 ENTERPRISE EXCEL IMPORT v6.0 - SMART COLUMN MAPPING")
        logger.info("=" * 80)
        logger.info(f"📁 File: {file_path}")
        logger.info(f"📋 Source: {source_filename}")
        logger.info(f"🔍 Strict Mode: {STRICT_MODE}")

        if not batch_id:
            batch_id = f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        metrics.batch_id = batch_id
        logger.info(f"📋 Batch ID: {batch_id}")

        try:
            # ==========================================================================================
            # STEP 1: Read Excel with header detection
            # ==========================================================================================
            read_start = time.time()
            
            # First, read without header to detect header row
            df_raw = pd.read_excel(file_path, engine='openpyxl', header=None)
            
            if len(df_raw) == 0:
                error_msg = "Excel file is empty"
                logger.error(f"❌ {error_msg}")
                return {"success": False, "error": error_msg}
            
            # Detect header row
            header_row = HeaderDetector.detect_header_row(df_raw)
            metrics.header_detection_row = header_row
            
            logger.info(f"📋 Using header row: {header_row}")
            logger.info(f"📋 First 5 rows sample:")
            for i in range(min(5, len(df_raw))):
                row_values = [str(v)[:30] if v is not None else 'None' for v in df_raw.iloc[i].values[:5]]
                logger.info(f"  Row {i}: {row_values}")
            
            # Read with detected header
            df = pd.read_excel(file_path, engine='openpyxl', header=header_row)
            
            # Clean up
            df = df.dropna(how='all')
            df = df.dropna(axis=1, how='all')
            
            metrics.excel_read_time = time.time() - read_start
            metrics.rows_read = len(df)

            logger.info(f"📄 Read {metrics.rows_read} data rows, {len(df.columns)} columns")

            # ==========================================================================================
            # STEP 2: Column mapping with diagnostics
            # ==========================================================================================
            excel_columns = [str(col).strip() for col in df.columns]
            
            logger.info("=" * 80)
            logger.info("📋 HEADER MAPPING DIAGNOSTICS")
            logger.info("=" * 80)
            
            # Get detailed header matches
            header_matches = ColumnMapper.get_header_matches(excel_columns)
            
            # Log detailed header mapping
            for col in excel_columns:
                match = header_matches[col]
                status_icon = "✅" if match['mapped'] else "❌"
                logger.info(f"  {status_icon} '{match['original']}'")
                logger.info(f"      → Normalized: '{match['normalized']}'")
                logger.info(f"      → Mapped to: '{match['mapped'] or 'UNMAPPED'}'")
                logger.info(f"      → Status: {match['status']}")
            
            # Create mapping
            mapping, field_to_column, unmapped = ColumnMapper.map_columns(excel_columns)
            
            # Store for metrics
            metrics.header_matches = {col: header_matches[col]['mapped'] for col in excel_columns}
            metrics.unmapped_headers = unmapped

            # ==========================================================================================
            # STEP 3: Validate required columns
            # ==========================================================================================
            validation = ColumnMapper.validate_mapping(field_to_column, unmapped)
            
            if not validation['valid']:
                logger.error("=" * 80)
                logger.error("❌ REQUIRED COLUMN VALIDATION FAILED")
                logger.error("=" * 80)
                logger.error(f"  Missing required fields: {validation['missing_required']}")
                logger.error(f"  Mapped fields: {validation['mapped_fields']}")
                logger.error(f"  Unmapped headers: {validation['unmapped_headers'][:10]}")
                logger.error("=" * 80)
                logger.error("  📋 DETECTED HEADERS (First 20):")
                for i, col in enumerate(excel_columns[:20]):
                    logger.error(f"    {i+1}. '{col}'")
                
                if unmapped:
                    logger.error("  📋 UNMAPPED HEADERS:")
                    for col in unmapped[:10]:
                        logger.error(f"    - '{col}'")
                
                logger.error("=" * 80)
                
                error_msg = f"Missing required columns: {validation['missing_required']}"
                return {
                    "success": False,
                    "error": error_msg,
                    "available_columns": excel_columns,
                    "unmapped_headers": unmapped,
                    "mapped_fields": validation['mapped_fields'],
                    "missing_required": validation['missing_required'],
                    "header_matches": header_matches,
                    "header_detection_row": header_row
                }

            # ==========================================================================================
            # STEP 4: Process rows
            # ==========================================================================================
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
                row_number = index + 2 + header_row  # Adjust for header row
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

                    # Helper to get value
                    def get_value(field_name: str):
                        col = field_to_column.get(field_name)
                        if col:
                            return row.get(col)
                        return None

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

                    dn_qty_raw = get_value('dn_qty')
                    dn_qty = parse_quantity_int(dn_qty_raw)

                    dn_amount_raw = get_value('dn_amount')
                    dn_amount = parse_amount_decimal(dn_amount_raw)

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

                    # Store normalized data for verification
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
                        # Update existing record
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
                        # Insert new record
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

            # ==========================================================================================
            # STEP 5: Final commit and verification
            # ==========================================================================================
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

            # ==========================================================================================
            # STEP 6: Final report
            # ==========================================================================================
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
            logger.info(f"  Header Detection Row: {metrics.header_detection_row}")
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
                "header_detection_row": metrics.header_detection_row,
                "header_matches": metrics.header_matches,
                "unmapped_headers": metrics.unmapped_headers,
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
                "validation_errors": [str(e)],
                "header_detection_row": metrics.header_detection_row,
                "header_matches": metrics.header_matches,
                "unmapped_headers": metrics.unmapped_headers
            }

# =====================================================================================================
# BLOCK 11: EXPORTS
# =====================================================================================================

__all__ = [
    'ExcelImportService',
    'ColumnMapper',
    'StatusEngine',
    'ImportMetrics',
    'RowAudit',
    'VerificationEngine',
    'HeaderDetector',
    'normalize_header',
    'parse_amount_decimal',
    'parse_quantity_int',
    'parse_date_excel',
    'normalize_string',
    'normalize_dn',
    'STRICT_MODE',
    'VERIFY_ALL_ROWS',
    'VerificationError',
    'HeaderDetectionError'
]

# =====================================================================================================
# END OF FILE
# =====================================================================================================
