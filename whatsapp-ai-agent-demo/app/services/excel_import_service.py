# =====================================================================================================
# FILE: whatsapp-ai-agent-demo/app/services/excel_import_service.py
# VERSION: v3.3 - FIXED COLUMN MAPPING WITH SAFE BATCH
# PURPOSE: High-performance enterprise-grade Excel import with safe batch processing
# =====================================================================================================

from __future__ import annotations

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
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing

import pandas as pd
import numpy as np
from sqlalchemy import text, inspect
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy import event

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# =====================================================================================================
# BLOCK 2: LOGGING AND CONSTANTS
# =====================================================================================================

HEADER_SCAN_ROWS = 25
DEFAULT_BATCH_SIZE = 5000  # SAFE: Prevents SQL truncation
SAFE_INSERT_BATCH_SIZE = 5000  # Maximum rows per INSERT statement
SAFE_DELETE_CHUNK_SIZE = 1000  # Maximum rows per DELETE statement
EXCEL_EPOCH = "1899-12-30"
PROGRESS_LOG_INTERVAL = 5000
MAX_WORKERS = multiprocessing.cpu_count() * 2
USE_POLARS = True

try:
    import polars as pl
    HAS_POLARS = True
except ImportError:
    HAS_POLARS = False
    logger.warning("Polars not available, falling back to pandas")

# =====================================================================================================
# BLOCK 3: WAREHOUSE MAPPING
# =====================================================================================================

CITY_TO_WAREHOUSE_MAP = {
    "gujrat": "Gujrat",
    "gujrat office": "Gujrat",
    "gujrat warehouse": "Gujrat",
    "kharian": "Gujrat",
    "lahore": "Lahore",
    "lahore office": "Lahore",
    "lahore warehouse": "Lahore",
    "faisalabad": "Faisalabad",
    "faisalabad office": "Faisalabad",
    "faisalabad warehouse": "Faisalabad",
    "multan": "Multan",
    "multan office": "Multan",
    "multan warehouse": "Multan",
    "rawalpindi": "Rawalpindi",
    "rawalpindi office": "Rawalpindi",
    "rawalpindi warehouse": "Rawalpindi",
    "islamabad": "Islamabad",
    "islamabad office": "Islamabad",
    "islamabad warehouse": "Islamabad",
    "sialkot": "Sialkot",
    "sialkot office": "Sialkot",
    "sialkot warehouse": "Sialkot",
    "gujranwala": "Gujranwala",
    "gujranwala office": "Gujranwala",
    "gujranwala warehouse": "Gujranwala",
    "sargodha": "Sargodha",
    "sahiwal": "Sahiwal",
    "bahawalpur": "Bahawalpur",
    "karachi": "Karachi",
    "karachi office": "Karachi",
    "karachi warehouse": "Karachi",
    "hyderabad": "Hyderabad",
    "sukkur": "Sukkur",
    "peshawar": "Peshawar",
    "peshawar office": "Peshawar",
    "peshawar warehouse": "Peshawar",
    "abbottabad": "Abbottabad",
    "quetta": "Quetta",
    "quetta office": "Quetta",
    "quetta warehouse": "Quetta",
    "muzaffarabad": "Muzaffarabad",
}

WAREHOUSE_CODE_MAP = {
    "lahore": "LHE",
    "karachi": "KHI",
    "rawalpindi": "RWP",
    "islamabad": "ISB",
    "multan": "MUX",
    "peshawar": "PEW",
    "quetta": "QTA",
    "hyderabad": "HYD",
    "faisalabad": "FSD",
    "sialkot": "SKT",
    "gujranwala": "GJW",
    "gujrat": "GJT",
    "bahawalpur": "BWP",
    "sukkur": "SKR",
    "sahiwal": "SWL",
    "sargodha": "SGD",
    "abbottabad": "ABT",
    "muzaffarabad": "MZD",
}

# =====================================================================================================
# BLOCK 4: CUSTOM EXCEPTIONS
# =====================================================================================================

class ExcelImportServiceError(Exception):
    pass

class WorksheetNotFoundError(ExcelImportServiceError):
    pass

class ColumnMappingError(ExcelImportServiceError):
    pass

class VerificationError(ExcelImportServiceError):
    pass

class ValidationError(ExcelImportServiceError):
    pass

# =====================================================================================================
# BLOCK 5: NORMALIZATION HELPERS
# =====================================================================================================

_REMOVE_NON_DIGIT = re.compile(r"[^0-9]")
_REMOVE_SPECIAL = re.compile(r"[^a-zA-Z0-9]")
_REMOVE_AMOUNT_SPECIAL = re.compile(r"[^\d.\-()]")
_WHITESPACE_CLEAN = re.compile(r"\s+")

def normalize_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"[_\-./\\#·•:;|]", " ", text)
    text = text.replace("\u00a0", " ")
    text = text.replace("\t", " ")
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    text = _WHITESPACE_CLEAN.sub(" ", text).strip()
    return text.lower()

def normalize_string(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        return cleaned or None
    return str(value).strip() or None

def normalize_string_fast(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return str(value).strip() or None

def normalize_dn(value: Any) -> str:
    text = normalize_string_fast(value)
    if not text:
        return ""
    return _REMOVE_NON_DIGIT.sub("", text)

def normalize_city(value: Any) -> Optional[str]:
    city = normalize_string_fast(value)
    if not city:
        return None
    city_map = {
        "lhr": "Lahore", "isb": "Islamabad", "rwp": "Rawalpindi",
        "khi": "Karachi", "fsd": "Faisalabad", "mux": "Multan",
        "pew": "Peshawar", "qta": "Quetta", "gjw": "Gujranwala",
        "skt": "Sialkot", "gjt": "Gujrat",
    }
    return city_map.get(city.lower().strip(), city)

def map_city_to_warehouse(city: Optional[str]) -> Optional[str]:
    if not city:
        return None
    city_lower = city.lower().strip()
    if city_lower in CITY_TO_WAREHOUSE_MAP:
        return CITY_TO_WAREHOUSE_MAP[city_lower]
    for key, warehouse in CITY_TO_WAREHOUSE_MAP.items():
        if key in city_lower or city_lower in key:
            logger.info(f"📍 Mapped city '{city}' to warehouse '{warehouse}'")
            return warehouse
    return None

def get_warehouse_code(warehouse: Optional[str]) -> Optional[str]:
    if not warehouse:
        return None
    warehouse_lower = warehouse.lower().strip()
    if warehouse_lower in WAREHOUSE_CODE_MAP:
        return WAREHOUSE_CODE_MAP[warehouse_lower]
    for key, code in WAREHOUSE_CODE_MAP.items():
        if key in warehouse_lower or warehouse_lower in key:
            return code
    return None

def derive_customer_code(customer_name: Optional[str]) -> Optional[str]:
    if not customer_name:
        return None
    code = _REMOVE_SPECIAL.sub("_", customer_name[:15].upper()).strip("_")
    return f"CUST_{code}" if code else None

def derive_dealer_code(customer_name: Optional[str]) -> Optional[str]:
    if not customer_name:
        return None
    code = _REMOVE_SPECIAL.sub("_", customer_name[:15].upper()).strip("_")
    return f"DEAL_{code}" if code else None

def get_delivery_location(ship_to_city: Optional[str]) -> Optional[str]:
    return normalize_city(ship_to_city)

def generate_batch_id() -> str:
    return f"BATCH_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

# =====================================================================================================
# BLOCK 6: PARSING HELPERS
# =====================================================================================================

def parse_amount(value: Any) -> Optional[Decimal]:
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
        cleaned = _REMOVE_AMOUNT_SPECIAL.sub("", cleaned)
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
        formats = (
            "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
            "%d-%m-%Y", "%m-%d-%Y", "%d-%b-%Y", "%b %d %Y",
            "%Y/%m/%d", "%Y%m%d", "%d %b %Y", "%b %d, %Y", "%d %B %Y"
        )
        for fmt in formats:
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        try:
            serial = float(raw)
            if serial > 59:
                return (pd.Timestamp(EXCEL_EPOCH) + pd.Timedelta(days=serial)).date()
        except ValueError:
            pass
        return None
    return None

# =====================================================================================================
# BLOCK 7: BUSINESS VALIDATION
# =====================================================================================================

class BusinessValidator:
    VALID_WAREHOUSES = {
        "rawalpindi", "islamabad", "lahore", "karachi", "faisalabad",
        "multan", "peshawar", "quetta", "gujranwala", "sialkot",
        "gujrat", "bahawalpur", "sukkur", "sahiwal", "sargodha",
        "hyderabad", "abbottabad", "muzaffarabad",
    }

    @classmethod
    def validate_record(cls, record: Dict[str, Any]) -> List[str]:
        errors = []
        dn_no = record.get("dn_no")
        if not dn_no:
            errors.append("DN NO is missing")
        elif len(dn_no) < 10:
            errors.append(f"DN NO '{dn_no}' is too short")
        material_no = record.get("material_no")
        if not material_no:
            errors.append("Material NO is missing")
        elif len(material_no) < 4:
            errors.append(f"Material NO '{material_no}' is too short")
        warehouse = record.get("warehouse")
        if warehouse and warehouse.lower().strip() not in cls.VALID_WAREHOUSES:
            errors.append(f"Unknown warehouse: '{warehouse}'")
        dn_qty = record.get("dn_qty")
        if dn_qty is not None and (not isinstance(dn_qty, int) or dn_qty <= 0):
            errors.append(f"Invalid quantity: {dn_qty}")
        dn_amount = record.get("dn_amount")
        if dn_amount is not None:
            try:
                if Decimal(str(dn_amount)) <= 0:
                    errors.append(f"Invalid amount: {dn_amount}")
            except:
                errors.append(f"Invalid amount format: {dn_amount}")
        return errors

# =====================================================================================================
# BLOCK 8: COLUMN MAP - EXACT MATCH FOR YOUR EXCEL
# =====================================================================================================

class ColumnMap:
    """Map Excel headers to application field names - EXACT MATCHES for your Excel"""

    HEADER_ALIASES = {
        "order_type": {"order type", "order-type", "order_type", "order", "ordertype", "so no"},
        "dn_no": {"dn no", "dn", "dn_no", "delivery note", "delivery note no", "delivery number", "dn#"},
        "dn_amount": {"dn amount", "dn_amount", "amount", "amt", "total", "net amount", "order amount", "value", "dn value"},
        "dn_qty": {"dn qty", "dn_qty", "qty", "quantity", "units", "pcs"},
        "dn_work": {"dn work", "dn_work", "work", "status", "dn status", "delivery status", "work order"},
        "division": {"division", "div", "department", "business unit", "product division"},
        "material_no": {"material no", "material", "material_no", "material number", "material code", "sku"},
        "customer_model": {"customer model", "customer_model", "model", "product model", "description"},
        "sales_office": {"sales office", "sales_office", "office", "sales", "branch"},
        "customer_name": {"customer name", "customer_name", "sold to party name", "sold-to-party name", "sold to party", "dealer name", "customer"},
        "ship_to_city": {"ship to city", "ship-to city", "ship_to_city", "city", "destination city", "delivery city"},
        "storage_location": {"storage", "storage_location", "storage location", "bin", "location"},
        "warehouse": {"warehouse", "ware house", "wh", "plant", "facility"},
        "dn_create_date": {"dn create date", "dn_create_date", "create date", "created date", "dn created", "order date"},
        "good_issue_date": {"good issue date", "good_issue_date", "pgi", "pgi date", "goods issue", "dispatch date", "shipped date"},
        "pod_date": {"pod date", "pod_date", "pod", "proof of delivery", "received date", "confirmation date"},
        "sales_manager": {"sales manager", "sales_manager", "manager", "sales rep", "representative"},
    }

    MANDATORY_COLUMNS = {"dn_no", "material_no"}

    @classmethod
    def build_mapping(cls, headers: List[Any], use_fuzzy: bool = True) -> Dict[str, Any]:
        alias_to_field = {}
        for field, aliases in cls.HEADER_ALIASES.items():
            for alias in aliases:
                alias_to_field[normalize_header(alias)] = field

        mapping = {}
        for header in headers:
            if header is None:
                continue
            normalized = normalize_header(header)
            field = alias_to_field.get(normalized)
            if field and field not in mapping:
                mapping[field] = header
                continue
            if use_fuzzy:
                best_match = None
                best_score = 0
                header_words = set(normalized.split())
                for alias, field_name in alias_to_field.items():
                    if field_name in mapping:
                        continue
                    alias_words = set(alias.split())
                    overlap = len(alias_words & header_words)
                    if overlap > 0 and overlap > best_score:
                        best_score = overlap
                        best_match = (field_name, alias)
                if best_match and best_score >= 1:
                    mapping[best_match[0]] = header
                    continue

        missing = sorted(cls.MANDATORY_COLUMNS - set(mapping))
        if missing:
            raise ColumnMappingError(f"Mandatory columns not found: {missing}")
        
        logger.info(f"✅ Column mapping: {mapping}")
        return mapping

# =====================================================================================================
# BLOCK 9: WORKSHEET DETECTION
# =====================================================================================================

def detect_header_row(df: pd.DataFrame, max_rows: int = HEADER_SCAN_ROWS) -> Tuple[int, int]:
    header_keywords = {
        "dn": 10, "material": 10, "qty": 5, "amount": 5,
        "warehouse": 4, "city": 3, "model": 3, "office": 3,
        "storage": 3, "date": 2, "manager": 2, "work": 3,
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

def detect_worksheet_fast(file_path: str) -> Tuple[str, int]:
    if HAS_POLARS:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheet_names = wb.sheetnames
            wb.close()
            for sheet_name in sheet_names:
                if sheet_name.startswith(("_", "$")):
                    continue
                if any(word in sheet_name.lower() for word in ("summary", "sum", "total")):
                    continue
                try:
                    df = pl.read_excel(file_path, sheet_name=sheet_name, header_row=0, engine='calamine', infer_schema_length=100)
                    if df.height > 0:
                        return sheet_name, 0
                except:
                    pass
        except:
            pass
    return detect_worksheet(file_path)

def detect_worksheet(file_path: str) -> Tuple[str, int]:
    excel_file = pd.ExcelFile(file_path, engine="openpyxl")
    best_sheet = None
    best_header_row = 0
    best_score = 0
    for sheet_name in excel_file.sheet_names:
        if sheet_name.startswith(("_", "$")):
            continue
        if any(word in sheet_name.lower() for word in ("summary", "sum", "total")):
            continue
        sample = pd.read_excel(file_path, sheet_name=sheet_name, header=None, nrows=HEADER_SCAN_ROWS, engine="openpyxl")
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

def read_excel_fast(file_path: str, sheet_name: str, header_row: int) -> pd.DataFrame:
    if HAS_POLARS:
        try:
            df = pl.read_excel(file_path, sheet_name=sheet_name, header_row=header_row, engine='calamine', infer_schema_length=1000)
            logger.info("⚡ Used Polars with calamine engine")
            return df.to_pandas()
        except Exception as e:
            logger.warning(f"Polars read failed: {e}, falling back to pandas")
    logger.info("📖 Using pandas")
    df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row, engine='openpyxl')
    logger.info(f"✅ Read {len(df)} rows with pandas")
    return df

# =====================================================================================================
# BLOCK 10: STATUS DERIVATION
# =====================================================================================================

def derive_status(good_issue_date: Optional[date], pod_date: Optional[date],
                  dn_work: Optional[str] = None) -> Dict[str, Any]:
    has_pgi = good_issue_date is not None
    has_pod = pod_date is not None
    if has_pgi and has_pod:
        return {"delivery_status": "Delivered", "pgi_status": "Completed", "pod_status": "Completed", "pending_flag": False}
    if has_pgi:
        return {"delivery_status": "In Transit", "pgi_status": "Completed", "pod_status": "Pending", "pending_flag": True}
    if dn_work and "invoiced" in dn_work.lower():
        return {"delivery_status": "Pending Dispatch", "pgi_status": "Pending", "pod_status": "Pending", "pending_flag": True}
    return {"delivery_status": "Pending Dispatch", "pgi_status": "Pending", "pod_status": "Pending", "pending_flag": True}

# =====================================================================================================
# BLOCK 11: DATABASE CONSTRAINT CHECKER
# =====================================================================================================

def check_unique_constraint_exists(db: Session, table_name: str) -> bool:
    try:
        result = db.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE table_name = :table_name AND constraint_type = 'UNIQUE')"),
            {"table_name": table_name}
        )
        return result.scalar() or False
    except Exception as e:
        logger.warning(f"Could not check for unique constraint: {e}")
        return False

def create_unique_constraint_if_missing(db: Session, table_name: str, columns: List[str]) -> bool:
    try:
        exists = db.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE table_name = :table_name AND constraint_type = 'UNIQUE')"),
            {"table_name": table_name}
        ).scalar()
        if exists:
            logger.info(f"✅ Unique constraint already exists on {table_name}")
            return True
        constraint_name = f"uq_{table_name}_{'_'.join(columns)}"
        columns_str = ", ".join(columns)
        try:
            db.execute(text(f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} UNIQUE ({columns_str})"))
            db.commit()
            logger.info(f"✅ Created unique constraint {constraint_name} on ({columns_str})")
            return True
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to create unique constraint: {e}")
            return False
    except Exception as e:
        logger.error(f"Failed to create unique constraint: {e}")
        db.rollback()
        return False

# =====================================================================================================
# BLOCK 12: MAIN SERVICE - WITH SAFE BATCH MECHANISM
# =====================================================================================================

class ExcelImportService:
    def __init__(
        self,
        db: Session,
        batch_size: int = DEFAULT_BATCH_SIZE,
        auto_create_constraint: bool = True,
        validate_business_rules: bool = True,
        conflict_strategy: str = "upsert",
        use_vectorization: bool = True,
    ):
        self.db = db
        self.batch_size = min(batch_size, SAFE_INSERT_BATCH_SIZE)
        self.auto_create_constraint = auto_create_constraint
        self.validate_business_rules = validate_business_rules
        self.conflict_strategy = conflict_strategy
        self.use_vectorization = use_vectorization
        self.table = DeliveryReport.__table__
        self.table_columns = set(self.table.columns.keys())
        self._unique_constraint_exists = None
        self.metrics = {
            "import_start": None, "import_end": None, "database_time": 0,
            "parse_time": 0, "rows_read": 0, "rows_valid": 0,
            "rows_upserted": 0, "rows_duplicate": 0, "rows_skipped": 0,
            "rows_invalid": 0, "invalid_dates": 0, "invalid_amounts": 0,
            "validation_errors": [], "duplicate_rows": [], "batch_count": 0,
        }

    def _ensure_unique_constraint(self) -> bool:
        if self._unique_constraint_exists is not None:
            return self._unique_constraint_exists
        table_name = DeliveryReport.__tablename__
        columns = ["dn_no", "material_no"]
        self._unique_constraint_exists = check_unique_constraint_exists(self.db, table_name)
        if not self._unique_constraint_exists and self.auto_create_constraint:
            self._unique_constraint_exists = create_unique_constraint_if_missing(self.db, table_name, columns)
        if not self._unique_constraint_exists:
            logger.warning(f"⚠️ No unique constraint on ({', '.join(columns)}) in {table_name}. Using {self.conflict_strategy} strategy.")
        return self._unique_constraint_exists

    def import_file(
        self,
        file_path: str,
        source_filename: Optional[str] = None,
        sheet_name: Optional[str] = None,
        upload_batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        import_start = time.time()
        self.metrics["import_start"] = datetime.utcnow().isoformat()
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)
        batch_id = upload_batch_id or generate_batch_id()
        if sheet_name is None:
            sheet_name, header_row = detect_worksheet_fast(file_path)
        else:
            preview = pd.read_excel(file_path, sheet_name=sheet_name, header=None, nrows=HEADER_SCAN_ROWS, engine="openpyxl")
            header_row, _ = detect_header_row(preview)
        logger.info(f"📄 Importing Excel file {file_path} from sheet '{sheet_name}'")
        df = read_excel_fast(file_path, sheet_name, header_row)
        if df.empty:
            return self._build_response(sheet_name=sheet_name, batch_id=batch_id, success=True)
        mapping = ColumnMap.build_mapping(df.columns.tolist())
        self._ensure_unique_constraint()
        parse_start = time.time()
        if self.use_vectorization:
            records, errors, duplicates = self._process_vectorized(df, mapping, source_filename, batch_id, header_row)
        else:
            records, errors, duplicates = self._process_row_by_row(df, mapping, source_filename, batch_id, header_row)
        self.metrics["parse_time"] = time.time() - parse_start
        database_start = time.time()
        rows_upserted, batch_count = self._upsert_records_with_safe_batches(records)
        self.metrics["database_time"] = time.time() - database_start
        self.metrics["rows_upserted"] = rows_upserted
        self.metrics["batch_count"] = batch_count
        self.metrics["rows_read"] = int(len(df))
        self.metrics["rows_valid"] = len(records)
        self.metrics["rows_duplicate"] = len(duplicates)
        self.metrics["duplicate_rows"] = duplicates[:50]
        self.metrics["validation_errors"] = errors[:50]
        self.metrics["import_end"] = datetime.utcnow().isoformat()
        logger.info(f"✅ Import completed: {rows_upserted} rows upserted in {batch_count} batches")
        return self._build_response(sheet_name=sheet_name, batch_id=batch_id, success=True, errors=errors[:50])

    def _process_vectorized(
        self,
        df: pd.DataFrame,
        mapping: Dict[str, Any],
        source_filename: Optional[str],
        batch_id: str,
        header_row: int
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        records = []
        errors = []
        duplicates = []
        seen_keys = set()

        col_dn_no = mapping.get("dn_no")
        col_material_no = mapping.get("material_no")
        col_customer_name = mapping.get("customer_name")
        col_ship_to_city = mapping.get("ship_to_city")
        col_warehouse = mapping.get("warehouse")
        col_dn_amount = mapping.get("dn_amount")
        col_dn_qty = mapping.get("dn_qty")
        col_dn_work = mapping.get("dn_work")
        col_order_type = mapping.get("order_type")
        col_division = mapping.get("division")
        col_customer_model = mapping.get("customer_model")
        col_sales_office = mapping.get("sales_office")
        col_storage_location = mapping.get("storage_location")
        col_sales_manager = mapping.get("sales_manager")
        col_dn_create_date = mapping.get("dn_create_date")
        col_good_issue_date = mapping.get("good_issue_date")
        col_pod_date = mapping.get("pod_date")

        for idx, row in df.iterrows():
            excel_row_number = header_row + idx + 2
            try:
                dn_no = normalize_dn(row.get(col_dn_no))
                material_no = normalize_string(row.get(col_material_no))
                if not dn_no or not material_no:
                    errors.append({"row": excel_row_number, "dn": dn_no, "material": material_no, "errors": ["DN NO and Material NO are required"], "type": "validation"})
                    continue

                customer_name = normalize_string(row.get(col_customer_name))
                ship_to_city = normalize_city(row.get(col_ship_to_city))
                warehouse = normalize_string(row.get(col_warehouse))
                if not warehouse and ship_to_city:
                    warehouse = map_city_to_warehouse(ship_to_city)
                amount_decimal = parse_amount(row.get(col_dn_amount))
                dn_work = normalize_string(row.get(col_dn_work))
                dn_create_date = parse_date(row.get(col_dn_create_date))
                good_issue_date = parse_date(row.get(col_good_issue_date))
                pod_date = parse_date(row.get(col_pod_date))

                if not dn_create_date and row.get(col_dn_create_date) is not None:
                    self.metrics["invalid_dates"] += 1
                if amount_decimal is None and row.get(col_dn_amount) is not None:
                    self.metrics["invalid_amounts"] += 1

                record = {
                    "order_type": normalize_string(row.get(col_order_type)),
                    "dn_no": dn_no,
                    "dn_amount": float(amount_decimal) if amount_decimal is not None else None,
                    "dn_qty": parse_quantity(row.get(col_dn_qty)),
                    "dn_work": dn_work,
                    "division": normalize_string(row.get(col_division)),
                    "material_no": material_no,
                    "customer_model": normalize_string(row.get(col_customer_model)),
                    "sales_office": normalize_string(row.get(col_sales_office)),
                    "customer_name": customer_name,
                    "customer_code": derive_customer_code(customer_name),
                    "dealer_code": derive_dealer_code(customer_name),
                    "ship_to_city": ship_to_city,
                    "storage_location": normalize_string(row.get(col_storage_location)),
                    "warehouse": warehouse,
                    "warehouse_code": get_warehouse_code(warehouse),
                    "delivery_location": get_delivery_location(ship_to_city),
                    "dn_create_date": dn_create_date,
                    "good_issue_date": good_issue_date,
                    "pod_date": pod_date,
                    "sales_manager": normalize_string(row.get(col_sales_manager)),
                    "remarks": None,
                    "source_file": source_filename or os.path.basename(file_path),
                    "upload_batch_id": batch_id,
                    "imported_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
                status = derive_status(good_issue_date, pod_date, dn_work)
                record.update(status)
                if "created_at" in self.table_columns:
                    record["created_at"] = datetime.utcnow()

                if self.validate_business_rules:
                    val_errors = BusinessValidator.validate_record(record)
                    if val_errors:
                        errors.append({"row": excel_row_number, "dn": dn_no, "material": material_no, "errors": val_errors, "type": "validation"})
                        continue

                row_key = (dn_no, material_no)
                if row_key in seen_keys:
                    duplicates.append({"row": excel_row_number, "dn": dn_no, "material": material_no})
                    if self.conflict_strategy == "skip":
                        continue
                seen_keys.add(row_key)
                filtered_record = {k: v for k, v in record.items() if k in self.table_columns}
                records.append(filtered_record)
            except Exception as e:
                errors.append({"row": excel_row_number, "dn": row.get(col_dn_no), "material": row.get(col_material_no), "errors": [str(e)], "type": "error"})
                logger.error(f"Error at row {excel_row_number}: {e}")
        return records, errors, duplicates

    def _process_row_by_row(
        self,
        df: pd.DataFrame,
        mapping: Dict[str, Any],
        source_filename: Optional[str],
        batch_id: str,
        header_row: int
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        records = []
        errors = []
        duplicates = []
        seen_keys = set()
        for idx, row in df.iterrows():
            excel_row_number = header_row + idx + 2
            try:
                dn_no = normalize_dn(row.get(mapping.get("dn_no")))
                material_no = normalize_string(row.get(mapping.get("material_no")))
                if not dn_no or not material_no:
                    errors.append({"row": excel_row_number, "dn": dn_no, "material": material_no, "errors": ["DN NO and Material NO are required"], "type": "validation"})
                    continue
                customer_name = normalize_string(row.get(mapping.get("customer_name")))
                ship_to_city = normalize_city(row.get(mapping.get("ship_to_city")))
                warehouse = normalize_string(row.get(mapping.get("warehouse")))
                if not warehouse and ship_to_city:
                    warehouse = map_city_to_warehouse(ship_to_city)
                amount_decimal = parse_amount(row.get(mapping.get("dn_amount")))
                dn_work = normalize_string(row.get(mapping.get("dn_work")))
                dn_create_date = parse_date(row.get(mapping.get("dn_create_date")))
                good_issue_date = parse_date(row.get(mapping.get("good_issue_date")))
                pod_date = parse_date(row.get(mapping.get("pod_date")))
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
                    "remarks": None,
                    "source_file": source_filename or os.path.basename(file_path),
                    "upload_batch_id": batch_id,
                    "imported_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
                status = derive_status(good_issue_date, pod_date, dn_work)
                record.update(status)
                if "created_at" in self.table_columns:
                    record["created_at"] = datetime.utcnow()
                if self.validate_business_rules:
                    val_errors = BusinessValidator.validate_record(record)
                    if val_errors:
                        errors.append({"row": excel_row_number, "dn": dn_no, "material": material_no, "errors": val_errors, "type": "validation"})
                        continue
                row_key = (dn_no, material_no)
                if row_key in seen_keys:
                    duplicates.append({"row": excel_row_number, "dn": dn_no, "material": material_no})
                    if self.conflict_strategy == "skip":
                        continue
                seen_keys.add(row_key)
                filtered_record = {k: v for k, v in record.items() if k in self.table_columns}
                records.append(filtered_record)
            except Exception as e:
                errors.append({"row": excel_row_number, "dn": row.get(mapping.get("dn_no")), "material": row.get(mapping.get("material_no")), "errors": [str(e)], "type": "error"})
        return records, errors, duplicates

    def _upsert_records_with_safe_batches(self, records: List[Dict[str, Any]]) -> Tuple[int, int]:
        if not records:
            return 0, 0
        total = 0
        batch_count = 0
        protected_fields = {"id", "created_at"}
        has_constraint = self._unique_constraint_exists
        safe_batch_size = min(self.batch_size, SAFE_INSERT_BATCH_SIZE)
        logger.info(f"📊 Starting upsert with safe batch size: {safe_batch_size} rows per batch")
        try:
            for start in range(0, len(records), safe_batch_size):
                batch = records[start:start + safe_batch_size]
                batch_count += 1
                logger.info(f"📦 Batch {batch_count}: Processing rows {start+1} to {min(start+safe_batch_size, len(records))} ({len(batch)} rows)")
                if has_constraint and self.conflict_strategy == "upsert":
                    stmt = insert(self.table).values(batch)
                    update_fields = {column.name: getattr(stmt.excluded, column.name) for column in self.table.columns if column.name not in ({"dn_no", "material_no"} | protected_fields)}
                    stmt = stmt.on_conflict_do_update(index_elements=["dn_no", "material_no"], set_=update_fields)
                    result = self.db.execute(stmt)
                    total += result.rowcount or 0
                elif has_constraint and self.conflict_strategy == "skip":
                    stmt = insert(self.table).values(batch).on_conflict_do_nothing(index_elements=["dn_no", "material_no"])
                    result = self.db.execute(stmt)
                    total += result.rowcount or 0
                else:
                    dn_material_pairs = [(r["dn_no"], r["material_no"]) for r in batch]
                    if dn_material_pairs and self.conflict_strategy == "delete_insert":
                        for i in range(0, len(dn_material_pairs), SAFE_DELETE_CHUNK_SIZE):
                            chunk = dn_material_pairs[i:i+SAFE_DELETE_CHUNK_SIZE]
                            delete_placeholders = []
                            delete_params = {}
                            for idx, (dn, mat) in enumerate(chunk):
                                delete_placeholders.append(f"(:dn_{idx}, :mat_{idx})")
                                delete_params[f"dn_{idx}"] = dn
                                delete_params[f"mat_{idx}"] = mat
                            if delete_placeholders:
                                delete_sql = f"DELETE FROM delivery_reports WHERE (dn_no, material_no) IN ({', '.join(delete_placeholders)})"
                                self.db.execute(text(delete_sql), delete_params)
                    self.db.execute(insert(self.table).values(batch))
                    total += len(batch)
                self.db.commit()
                logger.info(f"✅ Batch {batch_count} committed: {len(batch)} rows")
                if total > 0 and total % PROGRESS_LOG_INTERVAL == 0:
                    logger.info(f"📊 Total progress: {total:,} rows upserted")
            logger.info(f"✅ All batches completed: {total} rows upserted in {batch_count} batches")
            return total, batch_count
        except SQLAlchemyError as exc:
            self.db.rollback()
            message = str(exc)
            logger.error(f"❌ Database error in batch {batch_count}: {message[:200]}")
            if "no unique or exclusion constraint matching the ON CONFLICT specification" in message:
                logger.warning("ON CONFLICT failed, falling back to delete-insert strategy")
                self._unique_constraint_exists = False
                return self._upsert_records_with_safe_batches(records)
            raise ExcelImportServiceError(f"Database upsert failed at batch {batch_count}: {message[:200]}") from exc

    def _build_response(self, sheet_name: str, batch_id: str, success: bool, errors: Optional[List[Dict]] = None) -> Dict[str, Any]:
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
                "parse_duration_seconds": round(self.metrics["parse_time"], 2),
                "database_duration_seconds": round(self.metrics["database_time"], 2),
                "import_duration_seconds": round(import_duration, 2),
                "rows_per_second": round(self.metrics["rows_read"] / import_duration if import_duration > 0 else 0, 2),
                "batch_count": self.metrics["batch_count"],
                "batch_size": self.batch_size,
            },
            "validation_errors": errors or [],
            "duplicate_rows": self.metrics["duplicate_rows"][:50],
        }

# =====================================================================================================
# BLOCK 13: PUBLIC ENTRY POINT
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
    conflict_strategy: str = "upsert",
    use_vectorization: bool = True,
) -> Dict[str, Any]:
    service = ExcelImportService(
        db=db,
        batch_size=batch_size,
        auto_create_constraint=auto_create_constraint,
        validate_business_rules=validate_business_rules,
        conflict_strategy=conflict_strategy,
        use_vectorization=use_vectorization,
    )
    return service.import_file(
        file_path=file_path,
        source_filename=source_filename,
        sheet_name=sheet_name,
        upload_batch_id=upload_batch_id,
    )

# =====================================================================================================
# BLOCK 14: FIX MISSING WAREHOUSE DATA
# =====================================================================================================

def fix_missing_warehouse_data(db: Session) -> Dict[str, Any]:
    try:
        result = db.execute(
            text("""
                SELECT id, dn_no, ship_to_city, warehouse
                FROM delivery_reports
                WHERE (warehouse IS NULL OR TRIM(warehouse) = '')
                AND ship_to_city IS NOT NULL
                AND TRIM(ship_to_city) != ''
            """)
        ).fetchall()
        if not result:
            return {"success": True, "message": "No records with missing warehouse found", "updated_count": 0}
        updated_count = 0
        for row in result:
            record_id, dn_no, ship_to_city, warehouse = row
            mapped_warehouse = map_city_to_warehouse(ship_to_city)
            if mapped_warehouse:
                db.execute(
                    text("""
                        UPDATE delivery_reports
                        SET warehouse = :warehouse,
                            warehouse_code = :warehouse_code,
                            updated_at = :updated_at
                        WHERE id = :id
                    """),
                    {"id": record_id, "warehouse": mapped_warehouse, "warehouse_code": get_warehouse_code(mapped_warehouse), "updated_at": datetime.utcnow()}
                )
                updated_count += 1
                logger.info(f"✅ Updated record {record_id} (DN: {dn_no}) with warehouse '{mapped_warehouse}' from city '{ship_to_city}'")
        db.commit()
        logger.info(f"✅ Fixed {updated_count} records with missing warehouse data")
        return {"success": True, "message": f"Fixed {updated_count} records with missing warehouse data", "updated_count": updated_count}
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Failed to fix missing warehouse data: {e}")
        return {"success": False, "message": str(e), "updated_count": 0}

# =====================================================================================================
# BLOCK 15: EXPORTED SYMBOLS
# =====================================================================================================

__all__ = [
    "ExcelImportService",
    "ExcelImportServiceError",
    "WorksheetNotFoundError",
    "ColumnMappingError",
    "VerificationError",
    "ValidationError",
    "import_delivery_excel",
    "fix_missing_warehouse_data",
    "check_unique_constraint_exists",
    "create_unique_constraint_if_missing",
    "BusinessValidator",
    "map_city_to_warehouse",
    "CITY_TO_WAREHOUSE_MAP",
    "DEFAULT_BATCH_SIZE",
    "SAFE_INSERT_BATCH_SIZE",
]

# =====================================================================================================
# MODULE INITIALIZATION LOGGING
# =====================================================================================================

logger.info("=" * 60)
logger.info("📊 EXCEL IMPORT SERVICE v3.3 - FIXED COLUMN MAPPING")
logger.info("=" * 60)
logger.info(f"  ✅ Insert Batch Size: {SAFE_INSERT_BATCH_SIZE:,} rows")
logger.info(f"  ✅ Delete Chunk Size: {SAFE_DELETE_CHUNK_SIZE:,} rows")
logger.info(f"  ✅ Polars Engine: {'Enabled' if HAS_POLARS else 'Disabled'}")
logger.info("  ✅ Safe Batch Mechanism: Enabled")
logger.info(f"  ✅ City-to-Warehouse Mapping: {len(CITY_TO_WAREHOUSE_MAP)} mappings")
logger.info("=" * 60)

# =====================================================================================================
# END OF FILE
# =====================================================================================================
