# =====================================================================================================
# FILE: whatsapp-ai-agent-demo/app/services/excel_import_service.py
# VERSION: v2.0 - ENTERPRISE READY
# PURPOSE: Enterprise-grade Excel import for Haier Pakistan WhatsApp AI Agent
# =====================================================================================================

# =====================================================================================================
# BLOCK 1: IMPORTS
# =====================================================================================================

import logging
import os
import re
import uuid
import time
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple, Set, Union
from pathlib import Path

import pandas as pd
from sqlalchemy import text, inspect
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError, ProgrammingError
from sqlalchemy.orm import Session

from app.models import DeliveryReport

# =====================================================================================================
# BLOCK 2: LOGGING AND CONSTANTS
# =====================================================================================================

logger = logging.getLogger(__name__)

HEADER_SCAN_ROWS = 25
DEFAULT_BATCH_SIZE = 5000
EXCEL_EPOCH = "1899-12-30"
PROGRESS_LOG_INTERVAL = 5000

# =====================================================================================================
# BLOCK 3: CUSTOM EXCEPTIONS
# =====================================================================================================

class ExcelImportServiceError(Exception):
    """Base exception for Excel import failures."""
    pass

class WorksheetNotFoundError(ExcelImportServiceError):
    """Raised when no usable worksheet is found."""
    pass

class ColumnMappingError(ExcelImportServiceError):
    """Raised when mandatory columns are missing."""
    pass

class VerificationError(ExcelImportServiceError):
    """Backward-compatible verification exception used by upload router."""
    pass

class ValidationError(ExcelImportServiceError):
    """Raised when data validation fails."""
    pass

# =====================================================================================================
# BLOCK 4: NORMALIZATION HELPERS (ENHANCED)
# =====================================================================================================

def normalize_header(value: Any) -> str:
    """Normalize Excel header text for reliable matching."""
    if value is None:
        return ""

    text = str(value).strip()
    text = re.sub(r"[_\-./\\#·•:;|]", " ", text)
    text = text.replace("\u00a0", " ")
    text = text.replace("\t", " ")
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()

def normalize_string(value: Any) -> Optional[str]:
    """Convert values to trimmed strings and return None for blanks."""
    if value is None or pd.isna(value):
        return None

    if isinstance(value, str):
        cleaned = " ".join(value.split())
        return cleaned or None

    return str(value).strip() or None

def normalize_dn(value: Any) -> str:
    """Keep only digits from the DN number."""
    text = normalize_string(value)
    if not text:
        return ""
    return re.sub(r"[^0-9]", "", text)

def normalize_city(value: Any) -> Optional[str]:
    """Normalize common city abbreviations."""
    city = normalize_string(value)
    if not city:
        return None

    city_map = {
        "lhr": "Lahore",
        "isb": "Islamabad",
        "rwp": "Rawalpindi",
        "khi": "Karachi",
        "fsd": "Faisalabad",
        "mux": "Multan",
        "pew": "Peshawar",
        "qta": "Quetta",
        "gjw": "Gujranwala",
        "skt": "Sialkot",
        "wah": "Wah Cantt",
        "skd": "Skardu",
        "hrp": "Haripur",
        "shk": "Shinkiari",
    }
    return city_map.get(city.lower().strip(), city)

def derive_customer_code(customer_name: Optional[str]) -> Optional[str]:
    """Create a simple customer code from customer name."""
    if not customer_name:
        return None
    code = re.sub(r"[^a-zA-Z0-9]", "_", customer_name[:15].upper()).strip("_")
    return f"CUST_{code}" if code else None

def derive_dealer_code(customer_name: Optional[str]) -> Optional[str]:
    """Create a simple dealer code from customer name."""
    if not customer_name:
        return None
    code = re.sub(r"[^a-zA-Z0-9]", "_", customer_name[:15].upper()).strip("_")
    return f"DEAL_{code}" if code else None

def get_warehouse_code(warehouse: Optional[str]) -> Optional[str]:
    """Map warehouse/city name to a warehouse code."""
    if not warehouse:
        return None

    warehouse_map = {
        "rawalpindi": "RWP",
        "islamabad": "ISB",
        "lahore": "LHE",
        "karachi": "KHI",
        "faisalabad": "FSD",
        "multan": "MUX",
        "peshawar": "PEW",
        "quetta": "QTA",
        "gujranwala": "GJW",
        "sialkot": "SKT",
        "wah": "WAH",
        "wah cantt": "WAH",
        "rwp": "RWP",
        "isb": "ISB",
        "lhr": "LHE",
        "skd": "SKD",
        "hrp": "HRP",
        "shk": "SHK",
    }
    return warehouse_map.get(warehouse.lower().strip())

def get_delivery_location(ship_to_city: Optional[str]) -> Optional[str]:
    """Return normalized delivery location."""
    return normalize_city(ship_to_city)

def generate_batch_id() -> str:
    """Generate a readable batch id for import tracking."""
    return f"BATCH_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

# =====================================================================================================
# BLOCK 5: PARSING HELPERS (ENHANCED)
# =====================================================================================================

def parse_amount(value: Any) -> Optional[Decimal]:
    """Parse amount values like 117,698 or numeric Excel values."""
    if value is None or pd.isna(value):
        return None

    if isinstance(value, Decimal):
        return value

    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        cleaned = re.sub(r"[^\d.\-()]", "", cleaned)

        if not cleaned:
            return None

        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = f"-{cleaned[1:-1]}"

        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None

    return None

def parse_quantity(value: Any) -> Optional[int]:
    """Parse quantity values and reject non-integer values."""
    if value is None or pd.isna(value):
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value) if value.is_integer() else None

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else None

    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not re.fullmatch(r"-?\d+", cleaned):
            return None
        try:
            return int(cleaned)
        except ValueError:
            return None

    return None

def parse_date(value: Any) -> Optional[date]:
    """Parse supported date formats including Excel serial dates and text formats."""
    if value is None or pd.isna(value):
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, pd.Timestamp):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, (int, float)):
        try:
            if float(value) > 59:
                return (pd.Timestamp(EXCEL_EPOCH) + pd.Timedelta(days=float(value))).date()
        except (ValueError, OverflowError):
            return None
        return None

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None

        # Extended date formats
        formats = (
            "%d.%m.%Y",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%d-%m-%Y",
            "%m-%d-%Y",
            "%d-%b-%Y",      # 05-Jun-2026
            "%b %d %Y",      # Jun 05 2026
            "%Y/%m/%d",      # 2026/06/05
            "%Y%m%d",        # 20260605
            "%d %b %Y",      # 05 Jun 2026
            "%b %d, %Y",     # Jun 05, 2026
            "%d %B %Y",      # 05 June 2026
        )

        for fmt in formats:
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue

        # Try Excel serial number as string
        try:
            serial = float(raw)
            if serial > 59:
                return (pd.Timestamp(EXCEL_EPOCH) + pd.Timedelta(days=serial)).date()
        except ValueError:
            pass

        return None

    return None

# =====================================================================================================
# BLOCK 6: BUSINESS VALIDATION (ENHANCED)
# =====================================================================================================

class BusinessValidator:
    """Validate business rules for delivery records."""

    VALID_WAREHOUSES = {
        "rawalpindi", "islamabad", "lahore", "karachi", "faisalabad",
        "multan", "peshawar", "quetta", "gujranwala", "sialkot",
        "wah", "wah cantt", "rwp", "isb", "lhr", "khi", "fsd",
        "mux", "pew", "qta", "gjw", "skt", "skd", "hrp", "shk"
    }

    VALID_SALES_OFFICES = {
        "wah office", "islamabad office", "lahore office",
        "karachi office", "faisalabad office", "multan office",
        "peshawar office", "quetta office"
    }

    VALID_DN_WORK_STATUSES = {
        "invoiced", "pending", "delivered", "in transit",
        "pending dispatch", "completed", "partial", "returned"
    }

    @classmethod
    def validate_record(cls, record: Dict[str, Any]) -> List[str]:
        """Validate a record and return list of validation errors."""
        errors = []

        # DN validation
        dn_no = record.get("dn_no")
        if not dn_no:
            errors.append("DN NO is missing")
        elif len(dn_no) < 10:
            errors.append(f"DN NO '{dn_no}' is too short (expected 10+ digits)")

        # Material validation
        material_no = record.get("material_no")
        if not material_no:
            errors.append("Material NO is missing")
        elif len(material_no) < 4:
            errors.append(f"Material NO '{material_no}' is too short")

        # Warehouse validation
        warehouse = record.get("warehouse")
        if warehouse:
            normalized = warehouse.lower().strip()
            if normalized not in cls.VALID_WAREHOUSES:
                errors.append(f"Unknown warehouse: '{warehouse}'")

        # Sales office validation
        sales_office = record.get("sales_office")
        if sales_office:
            normalized = sales_office.lower().strip()
            if normalized not in cls.VALID_SALES_OFFICES:
                # Warning only - not an error
                logger.warning(f"Unknown sales office: '{sales_office}'")

        # DN Work validation
        dn_work = record.get("dn_work")
        if dn_work:
            normalized = dn_work.lower().strip()
            if normalized not in cls.VALID_DN_WORK_STATUSES:
                logger.warning(f"Unknown DN Work status: '{dn_work}'")

        # Quantity validation
        dn_qty = record.get("dn_qty")
        if dn_qty is not None:
            if not isinstance(dn_qty, int) or dn_qty <= 0:
                errors.append(f"Invalid quantity: {dn_qty} (must be positive integer)")

        # Amount validation
        dn_amount = record.get("dn_amount")
        if dn_amount is not None:
            try:
                amount = Decimal(str(dn_amount))
                if amount <= 0:
                    errors.append(f"Invalid amount: {dn_amount} (must be positive)")
            except:
                errors.append(f"Invalid amount format: {dn_amount}")

        return errors

# =====================================================================================================
# BLOCK 7: ENHANCED COLUMN MAP
# =====================================================================================================

class ColumnMap:
    """Map Excel headers to application field names with extensive aliases."""

    HEADER_ALIASES = {
        # Order Type
        "order_type": {
            "order type", "order-type", "order_type", "order", "ordertype",
            "order no", "order number", "order#", "so no", "so number"
        },

        # DN NO (Mandatory)
        "dn_no": {
            "dn no", "dn", "dn_no", "delivery note", "delivery note no",
            "delivery number", "dn#", "dn-number", "dn number",
            "delivery note number", "delivery no"
        },

        # DN Amount
        "dn_amount": {
            "dn amount", "dn_amount", "amount", "amt", "total",
            "net amount", "order amount", "value", "dn value",
            "invoice amount", "net", "pkr", "total amount",
            "delivery amount", "invoice value", "amount pkr"
        },

        # DN Qty
        "dn_qty": {
            "dn qty", "dn_qty", "qty", "quantity", "units",
            "pcs", "piece", "delivery qty", "delivery quantity",
            "order qty", "order quantity", "qty pcs"
        },

        # DN Work
        "dn_work": {
            "dn work", "dn_work", "work", "status", "dn status",
            "delivery status", "work order", "order status",
            "delivery work", "work status", "dn work status",
            "delivery note work", "invoice status"
        },

        # Division
        "division": {
            "division", "div", "department", "business unit",
            "product division", "category"
        },

        # Material NO (Mandatory)
        "material_no": {
            "material no", "material", "material_no", "material number",
            "material code", "sku", "product no", "product number",
            "item no", "item", "part no", "part number",
            "product code", "item code", "article no"
        },

        # Customer Model
        "customer_model": {
            "customer model", "customer_model", "model", "product model",
            "description", "item description", "product description",
            "model no", "model number"
        },

        # Sales Office
        "sales_office": {
            "sales office", "sales_office", "office", "sales",
            "branch", "region", "sales region", "sales branch",
            "territory", "zone"
        },

        # Customer Name
        "customer_name": {
            "customer name", "customer_name", "sold to party name",
            "sold-to-party name", "sold to party", "sold-to party",
            "dealer name", "party name", "customer", "dealer",
            "account name", "client name", "buyer name"
        },

        # Ship-to City
        "ship_to_city": {
            "ship to city", "ship-to city", "ship_to_city", "city",
            "destination city", "delivery city", "customer city",
            "ship city", "distributor city", "consignee city"
        },

        # Storage Location
        "storage_location": {
            "storage", "storage_location", "storage location",
            "bin", "warehouse bin", "location", "store",
            "storage loc", "storage loc.", "storage area",
            "rack", "shelf"
        },

        # Warehouse
        "warehouse": {
            "warehouse", "ware house", "wh", "plant", "facility",
            "warehouse name", "warehouse code", "warehouse_location",
            "godown", "depot"
        },

        # DN Create Date
        "dn_create_date": {
            "dn create date", "dn_create_date", "create date",
            "created date", "dn created", "order date",
            "document date", "dn date", "date", "entry date",
            "creation date", "doc date", "posting date"
        },

        # Good Issue Date (PGI)
        "good_issue_date": {
            "good issue date", "good_issue_date", "pgi", "pgi date",
            "goods issue", "dispatch date", "shipped date",
            "ship date", "delivery date", "goods issue date",
            "issue date", "outbound date"
        },

        # POD Date
        "pod_date": {
            "pod date", "pod_date", "pod", "proof of delivery",
            "received date", "confirmation date", "receipt date",
            "customer received", "delivery confirmation",
            "acknowledgement date", "signed date"
        },

        # Sales Manager
        "sales_manager": {
            "sales manager", "sales_manager", "manager", "sales rep",
            "representative", "sales person", "sales executive",
            "account manager", "relationship manager"
        },

        # Customer Code (Derived)
        "customer_code": {
            "customer code", "customer_code", "cust code",
            "account code", "client code"
        },

        # Dealer Code (Derived)
        "dealer_code": {
            "dealer code", "dealer_code", "distributor code",
            "channel code"
        },

        # Warehouse Code (Derived)
        "warehouse_code": {
            "warehouse code", "warehouse_code", "wh code",
            "plant code", "facility code"
        },

        # Delivery Location (Derived)
        "delivery_location": {
            "delivery location", "delivery_location", "ship to location"
        },

        # Remarks
        "remarks": {
            "remarks", "remark", "note", "notes", "comments",
            "special instructions", "additional info", "observations"
        },
    }

    # Mandatory columns
    MANDATORY_COLUMNS = {"dn_no", "material_no"}

    @classmethod
    def build_mapping(cls, headers: List[Any], use_fuzzy: bool = True) -> Dict[str, Any]:
        """Build a field-to-column mapping from Excel headers."""
        alias_to_field = {}
        for field, aliases in cls.HEADER_ALIASES.items():
            for alias in aliases:
                normalized = normalize_header(alias)
                alias_to_field[normalized] = field

        mapping: Dict[str, Any] = {}
        unmapped_headers = []

        for header in headers:
            if header is None:
                continue

            normalized = normalize_header(header)
            field = alias_to_field.get(normalized)

            if field and field not in mapping:
                mapping[field] = header
                continue

            # Fuzzy matching for headers with high confidence
            if use_fuzzy:
                best_match = None
                best_score = 0

                for alias, field_name in alias_to_field.items():
                    if field_name in mapping:
                        continue
                    # Simple fuzzy match based on word overlap
                    alias_words = set(alias.split())
                    header_words = set(normalized.split())
                    overlap = len(alias_words & header_words)
                    if overlap > 0 and overlap > best_score:
                        best_score = overlap
                        best_match = (field_name, alias)

                if best_match and best_score >= 1:
                    field_name, _ = best_match
                    mapping[field_name] = header
                    logger.debug(f"Fuzzy matched '{header}' → {field_name} (score: {best_score})")
                    continue

            unmapped_headers.append(header)

        missing = sorted(cls.MANDATORY_COLUMNS - set(mapping))
        if missing:
            raise ColumnMappingError(f"Mandatory columns not found: {missing}")

        return mapping

# =====================================================================================================
# BLOCK 8: WORKSHEET DETECTION
# =====================================================================================================

def detect_header_row(df: pd.DataFrame, max_rows: int = HEADER_SCAN_ROWS) -> Tuple[int, int]:
    """Detect the most likely header row by scoring keywords."""
    header_keywords = {
        "dn": 10,
        "material": 10,
        "qty": 5,
        "amount": 5,
        "warehouse": 4,
        "city": 3,
        "model": 3,
        "office": 3,
        "storage": 3,
        "date": 2,
        "manager": 2,
        "work": 3,
        "dealer": 2,
        "customer": 2,
    }

    best_row = 0
    best_score = 0

    for row_idx in range(min(max_rows, len(df))):
        score = 0
        for value in df.iloc[row_idx].tolist():
            normalized = normalize_header(value)
            if not normalized:
                continue
            for keyword, weight in header_keywords.items():
                if keyword in normalized:
                    score += weight
                    break

        if score > best_score:
            best_row = row_idx
            best_score = score

    return best_row, best_score

def detect_worksheet(file_path: str) -> Tuple[str, int]:
    """Detect the best worksheet and its header row."""
    excel_file = pd.ExcelFile(file_path, engine="openpyxl")

    best_sheet: Optional[str] = None
    best_header_row = 0
    best_score = 0

    for sheet_name in excel_file.sheet_names:
        lowered = sheet_name.lower()

        if sheet_name.startswith(("_", "$")):
            continue

        if any(word in lowered for word in ("summary", "sum", "total", "grand total")):
            continue

        sample = pd.read_excel(
            file_path,
            sheet_name=sheet_name,
            header=None,
            nrows=HEADER_SCAN_ROWS,
            engine="openpyxl",
        )

        if sample.empty:
            continue

        header_row, score = detect_header_row(sample)
        if score > best_score:
            best_sheet = sheet_name
            best_header_row = header_row
            best_score = score

    if not best_sheet:
        raise WorksheetNotFoundError("No worksheet with delivery data was found.")

    return best_sheet, best_header_row

# =====================================================================================================
# BLOCK 9: ENHANCED STATUS DERIVATION
# =====================================================================================================

def derive_status(good_issue_date: Optional[date], pod_date: Optional[date],
                  dn_work: Optional[str] = None, dn_qty: Optional[int] = None) -> Dict[str, Any]:
    """Derive rich status information from dates and business context."""
    has_pgi = good_issue_date is not None
    has_pod = pod_date is not None

    # Base status from dates
    if has_pgi and has_pod:
        return {
            "delivery_status": "Delivered",
            "pgi_status": "Completed",
            "pod_status": "Completed",
            "pending_flag": False,
        }

    if has_pgi:
        return {
            "delivery_status": "In Transit",
            "pgi_status": "Completed",
            "pod_status": "Pending",
            "pending_flag": True,
        }

    # Check dn_work for additional context
    if dn_work:
        dn_work_lower = dn_work.lower().strip()
        if "invoiced" in dn_work_lower:
            return {
                "delivery_status": "Pending Dispatch",
                "pgi_status": "Pending",
                "pod_status": "Pending",
                "pending_flag": True,
            }
        if "partial" in dn_work_lower:
            return {
                "delivery_status": "Partial Delivery",
                "pgi_status": "Pending",
                "pod_status": "Pending",
                "pending_flag": True,
            }
        if "return" in dn_work_lower:
            return {
                "delivery_status": "Returned",
                "pgi_status": "N/A",
                "pod_status": "N/A",
                "pending_flag": False,
            }

    return {
        "delivery_status": "Pending Dispatch",
        "pgi_status": "Pending",
        "pod_status": "Pending",
        "pending_flag": True,
    }

# =====================================================================================================
# BLOCK 10: DATABASE CONSTRAINT CHECKER
# =====================================================================================================

def check_unique_constraint_exists(db: Session, table_name: str, columns: List[str]) -> bool:
    """Check if a unique constraint exists on the specified columns."""
    try:
        # Query pg_constraint
        columns_str = ", ".join(columns)
        query = text("""
            SELECT 1 FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE t.relname = :table_name
            AND n.nspname = 'public'
            AND c.contype = 'u'
            AND c.conkey = (
                SELECT array_agg(attnum)
                FROM pg_attribute a
                WHERE a.attrelid = t.oid
                AND a.attname IN :columns
                ORDER BY a.attnum
            )
            LIMIT 1
        """)

        result = db.execute(query, {"table_name": table_name, "columns": tuple(columns)})
        return result.fetchone() is not None

    except Exception as e:
        logger.warning(f"Could not check for unique constraint: {e}")
        return False

def create_unique_constraint_if_missing(db: Session, table_name: str, columns: List[str]) -> bool:
    """Create unique constraint if it doesn't exist."""
    try:
        # Check if it exists first
        if check_unique_constraint_exists(db, table_name, columns):
            logger.info(f"Unique constraint on ({', '.join(columns)}) already exists")
            return True

        # Create the constraint
        constraint_name = f"uq_{table_name}_{'_'.join(columns)}"
        columns_str = ", ".join(columns)

        # Check if index with same name exists
        index_check = db.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :idx_name"),
            {"idx_name": constraint_name}
        )
        if index_check.fetchone():
            logger.info(f"Index {constraint_name} already exists, not creating again")
            return True

        # Create the unique constraint
        db.execute(text(f"""
            ALTER TABLE {table_name}
            ADD CONSTRAINT {constraint_name} UNIQUE ({columns_str})
        """))
        db.commit()
        logger.info(f"✅ Created unique constraint {constraint_name} on ({columns_str})")
        return True

    except Exception as e:
        logger.error(f"Failed to create unique constraint: {e}")
        db.rollback()
        return False

# =====================================================================================================
# BLOCK 11: MAIN SERVICE
# =====================================================================================================

class ExcelImportService:
    """Enterprise-grade service that reads Excel delivery data and upserts it into PostgreSQL."""

    def __init__(
        self,
        db: Session,
        batch_size: int = DEFAULT_BATCH_SIZE,
        auto_create_constraint: bool = True,
        validate_business_rules: bool = True,
        conflict_strategy: str = "upsert",  # "upsert", "delete_insert", "skip"
    ):
        self.db = db
        self.batch_size = batch_size
        self.auto_create_constraint = auto_create_constraint
        self.validate_business_rules = validate_business_rules
        self.conflict_strategy = conflict_strategy
        self.table = DeliveryReport.__table__
        self.table_columns = set(self.table.columns.keys())
        self._unique_constraint_exists = None

        # Metrics
        self.metrics = {
            "import_start": None,
            "import_end": None,
            "database_time": 0,
            "rows_read": 0,
            "rows_valid": 0,
            "rows_upserted": 0,
            "rows_duplicate": 0,
            "rows_skipped": 0,
            "rows_invalid": 0,
            "invalid_dates": 0,
            "invalid_amounts": 0,
            "validation_errors": [],
            "duplicate_rows": [],
        }

    def _ensure_unique_constraint(self) -> bool:
        """Ensure the unique constraint exists on (dn_no, material_no)."""
        if self._unique_constraint_exists is not None:
            return self._unique_constraint_exists

        table_name = DeliveryReport.__tablename__
        columns = ["dn_no", "material_no"]

        self._unique_constraint_exists = check_unique_constraint_exists(self.db, table_name, columns)

        if not self._unique_constraint_exists and self.auto_create_constraint:
            self._unique_constraint_exists = create_unique_constraint_if_missing(
                self.db, table_name, columns
            )

        if not self._unique_constraint_exists:
            logger.warning(
                f"⚠️ No unique constraint on ({', '.join(columns)}) in {table_name}. "
                f"Using {self.conflict_strategy} strategy."
            )

        return self._unique_constraint_exists

    def import_file(
        self,
        file_path: str,
        source_filename: Optional[str] = None,
        sheet_name: Optional[str] = None,
        upload_batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import one Excel file into the DeliveryReport table."""
        import_start = time.time()
        self.metrics["import_start"] = datetime.utcnow().isoformat()

        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        batch_id = upload_batch_id or generate_batch_id()

        # Step 1: Detect worksheet
        if sheet_name is None:
            sheet_name, header_row = detect_worksheet(file_path)
        else:
            preview = pd.read_excel(
                file_path,
                sheet_name=sheet_name,
                header=None,
                nrows=HEADER_SCAN_ROWS,
                engine="openpyxl",
            )
            header_row, _ = detect_header_row(preview)

        logger.info("📄 Importing Excel file %s from sheet %s", file_path, sheet_name)

        # Step 2: Read Excel
        df = pd.read_excel(
            file_path,
            sheet_name=sheet_name,
            header=header_row,
            engine="openpyxl",
        )

        if df.empty:
            return self._build_response(
                sheet_name=sheet_name,
                batch_id=batch_id,
                success=True,
            )

        # Step 3: Build column mapping
        mapping = ColumnMap.build_mapping(df.columns.tolist())

        # Step 4: Ensure unique constraint exists (or determine strategy)
        self._ensure_unique_constraint()

        # Step 5: Process rows with validation
        records = []
        errors = []
        seen_keys = set()

        for index, row in df.iterrows():
            excel_row_number = header_row + index + 2

            try:
                record = self._build_record(
                    row=row,
                    mapping=mapping,
                    source_filename=source_filename or os.path.basename(file_path),
                    upload_batch_id=batch_id,
                )

                # Business validation
                if self.validate_business_rules:
                    validation_errors = BusinessValidator.validate_record(record)
                    if validation_errors:
                        self.metrics["rows_invalid"] += 1
                        errors.append({
                            "row": excel_row_number,
                            "dn": record.get("dn_no"),
                            "material": record.get("material_no"),
                            "errors": validation_errors,
                            "type": "validation"
                        })
                        continue

                # Duplicate check
                row_key = (record["dn_no"], record["material_no"])
                if row_key in seen_keys:
                    self.metrics["rows_duplicate"] += 1
                    self.metrics["duplicate_rows"].append({
                        "row": excel_row_number,
                        "dn": record["dn_no"],
                        "material": record["material_no"],
                    })
                    if self.conflict_strategy == "skip":
                        continue

                seen_keys.add(row_key)
                records.append(record)
                self.metrics["rows_valid"] += 1

            except ExcelImportServiceError as exc:
                self.metrics["rows_invalid"] += 1
                errors.append({
                    "row": excel_row_number,
                    "dn": row.get(mapping.get("dn_no")),
                    "material": row.get(mapping.get("material_no")),
                    "errors": [str(exc)],
                    "type": "parsing"
                })
            except Exception as exc:
                self.metrics["rows_invalid"] += 1
                errors.append({
                    "row": excel_row_number,
                    "error": f"Unexpected error: {str(exc)}",
                    "type": "unexpected"
                })
                logger.error(f"Unexpected error at row {excel_row_number}: {exc}")

        # Step 6: Upsert records
        database_start = time.time()
        rows_upserted = self._upsert_records(records)
        database_time = time.time() - database_start
        self.metrics["database_time"] = database_time
        self.metrics["rows_upserted"] = rows_upserted

        # Step 7: Progress logging
        self._log_progress()

        self.metrics["import_end"] = datetime.utcnow().isoformat()
        self.metrics["rows_read"] = int(len(df))
        self.metrics["validation_errors"] = errors[:50]

        return self._build_response(
            sheet_name=sheet_name,
            batch_id=batch_id,
            success=True,
            errors=errors[:50],
        )

    def _build_record(
        self,
        row: pd.Series,
        mapping: Dict[str, Any],
        source_filename: str,
        upload_batch_id: Optional[str],
    ) -> Dict[str, Any]:
        """Build one database-ready record from one Excel row."""
        dn_no = normalize_dn(row.get(mapping["dn_no"]))
        material_no = normalize_string(row.get(mapping["material_no"]))

        if not dn_no:
            raise ExcelImportServiceError("DN NO is required.")

        if not material_no:
            raise ExcelImportServiceError("Material NO is required.")

        customer_name = normalize_string(row.get(mapping.get("customer_name")))
        ship_to_city = normalize_city(row.get(mapping.get("ship_to_city")))
        warehouse = normalize_string(row.get(mapping.get("warehouse")))
        amount_decimal = parse_amount(row.get(mapping.get("dn_amount")))
        dn_work = normalize_string(row.get(mapping.get("dn_work")))

        dn_create_date = parse_date(row.get(mapping.get("dn_create_date")))
        good_issue_date = parse_date(row.get(mapping.get("good_issue_date")))
        pod_date = parse_date(row.get(mapping.get("pod_date")))

        # Track parsing issues
        if not dn_create_date and row.get(mapping.get("dn_create_date")) is not None:
            self.metrics["invalid_dates"] += 1

        if amount_decimal is None and row.get(mapping.get("dn_amount")) is not None:
            self.metrics["invalid_amounts"] += 1

        record = {
            "order_type": normalize_string(row.get(mapping.get("order_type"))),
            "dn_no": dn_no,
            "dn_amount": float(amount_decimal) if amount_decimal is not None else None,
            "dn_qty": parse_quantity(row.get(mapping.get("dn_qty"))),
            "dn_work": dn_work,
            "division": normalize_string(row.get(mapping.get("division"))),
            "material_no": material_no,
            "customer_model": normalize_string(row.get(mapping.get("customer_model"))),
            "sales_office": normalize_string(row.get(mapping.get("sales_office"))),
            "customer_name": customer_name,
            "customer_code": derive_customer_code(customer_name),
            "dealer_code": derive_dealer_code(customer_name),
            "ship_to_city": ship_to_city,
            "storage_location": normalize_string(row.get(mapping.get("storage_location"))),
            "warehouse": warehouse,
            "warehouse_code": get_warehouse_code(warehouse),
            "delivery_location": get_delivery_location(ship_to_city),
            "dn_create_date": dn_create_date,
            "good_issue_date": good_issue_date,
            "pod_date": pod_date,
            "sales_manager": normalize_string(row.get(mapping.get("sales_manager"))),
            "remarks": normalize_string(row.get(mapping.get("remarks"))),
            "source_file": source_filename,
            "upload_batch_id": upload_batch_id,
            "imported_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        # Derive status with business context
        status = derive_status(good_issue_date, pod_date, dn_work)
        record.update(status)

        # Created at
        if "created_at" in self.table_columns:
            record["created_at"] = datetime.utcnow()

        return {key: value for key, value in record.items() if key in self.table_columns}

    def _upsert_records(self, records: List[Dict[str, Any]]) -> int:
        """Bulk upsert records using the configured conflict strategy."""
        if not records:
            return 0

        total = 0
        protected_fields = {"id", "created_at"}

        try:
            # Determine if we can use ON CONFLICT
            has_constraint = self._unique_constraint_exists

            for start in range(0, len(records), self.batch_size):
                batch = records[start:start + self.batch_size]

                if has_constraint and self.conflict_strategy == "upsert":
                    # Use ON CONFLICT DO UPDATE
                    stmt = insert(self.table).values(batch)
                    update_fields = {
                        column.name: getattr(stmt.excluded, column.name)
                        for column in self.table.columns
                        if column.name not in ({"dn_no", "material_no"} | protected_fields)
                    }

                    stmt = stmt.on_conflict_do_update(
                        index_elements=["dn_no", "material_no"],
                        set_=update_fields,
                    )

                    result = self.db.execute(stmt)
                    total += result.rowcount or 0

                elif has_constraint and self.conflict_strategy == "skip":
                    # Use ON CONFLICT DO NOTHING
                    stmt = insert(self.table).values(batch).on_conflict_do_nothing(
                        index_elements=["dn_no", "material_no"]
                    )
                    result = self.db.execute(stmt)
                    total += result.rowcount or 0

                else:
                    # Use delete-and-insert strategy
                    for record in batch:
                        dn_no = record.get("dn_no")
                        material_no = record.get("material_no")

                        # Delete existing if any
                        if self.conflict_strategy == "delete_insert":
                            self.db.execute(
                                text("DELETE FROM delivery_reports WHERE dn_no = :dn_no AND material_no = :material_no"),
                                {"dn_no": dn_no, "material_no": material_no}
                            )

                        # Insert
                        self.db.execute(insert(self.table).values(record))
                        total += 1

                # Commit after each batch
                self.db.commit()
                self._log_progress(total)

            return total

        except SQLAlchemyError as exc:
            self.db.rollback()
            message = str(exc)

            if "no unique or exclusion constraint matching the ON CONFLICT specification" in message:
                # Fall back to delete-insert strategy
                logger.warning("ON CONFLICT failed, falling back to delete-insert strategy")
                self._unique_constraint_exists = False
                return self._upsert_records(records)

            raise ExcelImportServiceError(f"Database upsert failed: {message}") from exc

    def _log_progress(self, processed_count: int = None):
        """Log progress for large imports."""
        if processed_count is None:
            return

        if processed_count % PROGRESS_LOG_INTERVAL == 0:
            logger.info(f"📊 Import progress: {processed_count:,} rows processed")

    def _build_response(
        self,
        sheet_name: str,
        batch_id: str,
        success: bool,
        errors: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Build the import response with metrics."""
        import_duration = 0
        if self.metrics["import_start"] and self.metrics["import_end"]:
            start = datetime.fromisoformat(self.metrics["import_start"])
            end = datetime.fromisoformat(self.metrics["import_end"])
            import_duration = (end - start).total_seconds()

        return {
            "success": success,
            "sheet_name": sheet_name,
            "batch_id": batch_id,
            "metrics": {
                "rows_read": self.metrics["rows_read"],
                "rows_valid": self.metrics["rows_valid"],
                "rows_upserted": self.metrics["rows_upserted"],
                "rows_duplicate": self.metrics["rows_duplicate"],
                "rows_invalid": self.metrics["rows_invalid"],
                "rows_skipped": self.metrics["rows_skipped"],
                "invalid_dates": self.metrics["invalid_dates"],
                "invalid_amounts": self.metrics["invalid_amounts"],
                "import_duration_seconds": round(import_duration, 2),
                "database_time_seconds": round(self.metrics["database_time"], 2),
                "rows_per_second": round(
                    self.metrics["rows_read"] / import_duration if import_duration > 0 else 0, 2
                ),
            },
            "validation_errors": errors or [],
            "duplicate_rows": self.metrics["duplicate_rows"][:50],
        }

# =====================================================================================================
# BLOCK 12: PUBLIC ENTRY POINT
# =====================================================================================================

def import_delivery_excel(
    db: Session,
    file_path: str,
    source_filename: Optional[str] = None,
    sheet_name: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    upload_batch_id: Optional[str] = None,
    auto_create_constraint: bool = True,
    validate_business_rules: bool = True,
    conflict_strategy: str = "upsert",  # "upsert", "delete_insert", "skip"
) -> Dict[str, Any]:
    """
    Read a delivery Excel file and upsert it into PostgreSQL through the DeliveryReport model.

    Args:
        db: SQLAlchemy session
        file_path: Path to Excel file
        source_filename: Original filename for tracking
        sheet_name: Specific sheet name (auto-detected if None)
        batch_size: Number of records per batch
        upload_batch_id: Batch ID for tracking
        auto_create_constraint: Create unique constraint if missing
        validate_business_rules: Enable business validation
        conflict_strategy: How to handle conflicts ('upsert', 'delete_insert', 'skip')

    Requirements:
        - DeliveryReport must map to a PostgreSQL table.
        - The table should have a unique index on (dn_no, material_no) or use delete-insert.
    """
    service = ExcelImportService(
        db=db,
        batch_size=batch_size,
        auto_create_constraint=auto_create_constraint,
        validate_business_rules=validate_business_rules,
        conflict_strategy=conflict_strategy,
    )

    return service.import_file(
        file_path=file_path,
        source_filename=source_filename,
        sheet_name=sheet_name,
        upload_batch_id=upload_batch_id,
    )

# =====================================================================================================
# BLOCK 13: EXPORTED SYMBOLS
# =====================================================================================================

__all__ = [
    "ExcelImportService",
    "ExcelImportServiceError",
    "WorksheetNotFoundError",
    "ColumnMappingError",
    "VerificationError",
    "ValidationError",
    "import_delivery_excel",
    "check_unique_constraint_exists",
    "create_unique_constraint_if_missing",
    "BusinessValidator",
]

# =====================================================================================================
# MODULE INITIALIZATION LOGGING
# =====================================================================================================

logger.info("=" * 60)
logger.info("📊 EXCEL IMPORT SERVICE v2.0 - ENTERPRISE READY")
logger.info("=" * 60)
logger.info("  ✅ Enhanced Column Mapping with 100+ aliases")
logger.info("  ✅ Fuzzy Header Matching")
logger.info("  ✅ Business Validation")
logger.info("  ✅ Rich Status Derivation")
logger.info("  ✅ Comprehensive Metrics")
logger.info("  ✅ Progress Logging")
logger.info("  ✅ Configurable Conflict Strategy")
logger.info("  ✅ Automatic Constraint Creation")
logger.info("=" * 60)

# =====================================================================================================
# END OF FILE
# =====================================================================================================
