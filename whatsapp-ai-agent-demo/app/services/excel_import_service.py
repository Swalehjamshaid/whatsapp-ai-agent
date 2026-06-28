# =====================================================================================================
# FILE: whatsapp-ai-agent-demo/app/services/excel_import_service.py
# VERSION: v1.0 - CLEAN PRODUCTION IMPORTER
# PURPOSE: Read delivery Excel files and upsert the data into PostgreSQL
# =====================================================================================================

# =====================================================================================================
# BLOCK 1: IMPORTS
# =====================================================================================================

import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import DeliveryReport


# =====================================================================================================
# BLOCK 2: LOGGING AND CONSTANTS
# =====================================================================================================

logger = logging.getLogger(__name__)

HEADER_SCAN_ROWS = 25
DEFAULT_BATCH_SIZE = 1000
EXCEL_EPOCH = "1899-12-30"


# =====================================================================================================
# BLOCK 3: CUSTOM EXCEPTIONS
# =====================================================================================================

class ExcelImportServiceError(Exception):
    """Base exception for Excel import failures."""


class WorksheetNotFoundError(ExcelImportServiceError):
    """Raised when no usable worksheet is found."""


class ColumnMappingError(ExcelImportServiceError):
    """Raised when mandatory columns are missing."""


# =====================================================================================================
# BLOCK 4: NORMALIZATION HELPERS
# =====================================================================================================

def normalize_header(value: Any) -> str:
    """Normalize Excel header text for reliable matching."""
    if value is None:
        return ""

    text = str(value).strip()
    text = re.sub(r"[_\-./\\#·•:;|]", " ", text)
    text = text.replace("\u00a0", " ")
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


# =====================================================================================================
# BLOCK 5: PARSING HELPERS
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
    """Parse supported date formats and Excel serial dates."""
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
            "%d.%m.%Y",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%d-%m-%Y",
            "%m-%d-%Y",
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
            return None

    return None


# =====================================================================================================
# BLOCK 6: COLUMN MAP
# =====================================================================================================

class ColumnMap:
    """Map Excel headers to application field names."""

    HEADER_ALIASES = {
        "order_type": {"order type"},
        "dn_no": {"dn no", "dn"},
        "dn_amount": {"dn amount"},
        "dn_qty": {"dn qty"},
        "dn_work": {"dn work", "work"},
        "division": {"division"},
        "material_no": {"material no"},
        "customer_model": {"customer model"},
        "sales_office": {"sales office"},
        "customer_name": {"sold to party name", "sold-to-party name", "customer name"},
        "ship_to_city": {"ship to city", "ship-to city"},
        "storage_location": {"storage"},
        "warehouse": {"warehouse"},
        "dn_create_date": {"dn create date"},
        "good_issue_date": {"good issue date"},
        "pod_date": {"pod date"},
        "sales_manager": {"sales manager"},
    }

    MANDATORY_COLUMNS = {"dn_no", "material_no"}

    @classmethod
    def build_mapping(cls, headers: List[Any]) -> Dict[str, Any]:
        """Build a field-to-column mapping from Excel headers."""
        alias_to_field = {
            normalize_header(alias): field
            for field, aliases in cls.HEADER_ALIASES.items()
            for alias in aliases
        }

        mapping: Dict[str, Any] = {}
        for header in headers:
            field = alias_to_field.get(normalize_header(header))
            if field and field not in mapping:
                mapping[field] = header

        missing = sorted(cls.MANDATORY_COLUMNS - set(mapping))
        if missing:
            raise ColumnMappingError(f"Mandatory columns not found: {missing}")

        return mapping


# =====================================================================================================
# BLOCK 7: WORKSHEET DETECTION
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
# BLOCK 8: DELIVERY STATUS DERIVATION
# =====================================================================================================

def derive_status(good_issue_date: Optional[date], pod_date: Optional[date]) -> Dict[str, Any]:
    """Derive delivery, PGI, POD, and pending flags from dates."""
    has_pgi = good_issue_date is not None
    has_pod = pod_date is not None

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

    return {
        "delivery_status": "Pending Dispatch",
        "pgi_status": "Pending",
        "pod_status": "Pending",
        "pending_flag": True,
    }


# =====================================================================================================
# BLOCK 9: MAIN SERVICE
# =====================================================================================================

class ExcelImportService:
    """Service that reads Excel delivery data and upserts it into PostgreSQL."""

    def __init__(self, db: Session, batch_size: int = DEFAULT_BATCH_SIZE):
        self.db = db
        self.batch_size = batch_size
        self.table = DeliveryReport.__table__
        self.table_columns = set(self.table.columns.keys())

    def import_file(
        self,
        file_path: str,
        source_filename: Optional[str] = None,
        sheet_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import one Excel file into the DeliveryReport table."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

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

        logger.info("Importing Excel file %s from sheet %s", file_path, sheet_name)

        df = pd.read_excel(
            file_path,
            sheet_name=sheet_name,
            header=header_row,
            engine="openpyxl",
        )

        if df.empty:
            return {
                "sheet_name": sheet_name,
                "rows_read": 0,
                "rows_valid": 0,
                "rows_upserted": 0,
                "rows_failed": 0,
                "errors": [],
            }

        mapping = ColumnMap.build_mapping(df.columns.tolist())

        records: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        seen_keys = set()

        for index, row in df.iterrows():
            excel_row_number = header_row + index + 2

            try:
                record = self._build_record(
                    row=row,
                    mapping=mapping,
                    source_filename=source_filename or os.path.basename(file_path),
                )

                row_key = (record["dn_no"], record["material_no"])
                if row_key in seen_keys:
                    continue

                seen_keys.add(row_key)
                records.append(record)
            except ExcelImportServiceError as exc:
                errors.append({"row": excel_row_number, "error": str(exc)})

        rows_upserted = self._upsert_records(records)

        return {
            "sheet_name": sheet_name,
            "rows_read": int(len(df)),
            "rows_valid": int(len(records)),
            "rows_upserted": int(rows_upserted),
            "rows_failed": int(len(errors)),
            "errors": errors[:50],
        }

    def _build_record(
        self,
        row: pd.Series,
        mapping: Dict[str, Any],
        source_filename: str,
    ) -> Dict[str, Any]:
        """Build one database-ready record from one Excel row."""
        dn_no = normalize_dn(row.get(mapping["dn_no"]))
        material_no = normalize_string(row.get(mapping["material_no"]))

        if not dn_no:
            raise ExcelImportServiceError("DN NO is required.")

        if not material_no:
            raise ExcelImportServiceError("Material NO is required.")

        dn_create_date = parse_date(row.get(mapping.get("dn_create_date")))
        good_issue_date = parse_date(row.get(mapping.get("good_issue_date")))
        pod_date = parse_date(row.get(mapping.get("pod_date")))

        record = {
            "order_type": normalize_string(row.get(mapping.get("order_type"))),
            "dn_no": dn_no,
            "dn_amount": parse_amount(row.get(mapping.get("dn_amount"))),
            "dn_qty": parse_quantity(row.get(mapping.get("dn_qty"))),
            "dn_work": normalize_string(row.get(mapping.get("dn_work"))),
            "division": normalize_string(row.get(mapping.get("division"))),
            "material_no": material_no,
            "customer_model": normalize_string(row.get(mapping.get("customer_model"))),
            "sales_office": normalize_string(row.get(mapping.get("sales_office"))),
            "customer_name": normalize_string(row.get(mapping.get("customer_name"))),
            "ship_to_city": normalize_string(row.get(mapping.get("ship_to_city"))),
            "storage_location": normalize_string(row.get(mapping.get("storage_location"))),
            "warehouse": normalize_string(row.get(mapping.get("warehouse"))),
            "dn_create_date": dn_create_date,
            "good_issue_date": good_issue_date,
            "pod_date": pod_date,
            "sales_manager": normalize_string(row.get(mapping.get("sales_manager"))),
            "source_filename": source_filename,
            "updated_at": datetime.utcnow(),
        }
        record.update(derive_status(good_issue_date, pod_date))

        if "created_at" in self.table_columns:
            record["created_at"] = datetime.utcnow()

        return {key: value for key, value in record.items() if key in self.table_columns}

    def _upsert_records(self, records: List[Dict[str, Any]]) -> int:
        """Bulk upsert records using PostgreSQL ON CONFLICT."""
        if not records:
            return 0

        total = 0
        protected_fields = {"id", "created_at"}

        for start in range(0, len(records), self.batch_size):
            batch = records[start : start + self.batch_size]

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

        self.db.commit()
        return total


# =====================================================================================================
# BLOCK 10: PUBLIC ENTRY POINT
# =====================================================================================================

def import_delivery_excel(
    db: Session,
    file_path: str,
    source_filename: Optional[str] = None,
    sheet_name: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Dict[str, Any]:
    """
    Read a delivery Excel file and upsert it into PostgreSQL through the DeliveryReport model.

    Requirements:
    - DeliveryReport must map to a PostgreSQL table.
    - The table must have a unique index on (dn_no, material_no).
    """
    service = ExcelImportService(db=db, batch_size=batch_size)
    return service.import_file(
        file_path=file_path,
        source_filename=source_filename,
        sheet_name=sheet_name,
    )


# =====================================================================================================
# BLOCK 11: EXPORTED SYMBOLS
# =====================================================================================================

__all__ = [
    "ExcelImportService",
    "ExcelImportServiceError",
    "WorksheetNotFoundError",
    "ColumnMappingError",
    "import_delivery_excel",
]
