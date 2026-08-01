# ==========================================================
# FILE: app/services/excel_import_service.py (v8.0 - ENTERPRISE)
# ==========================================================
# PURPOSE: Production-grade Excel Import Engine with dynamic header normalization,
#          configurable column mapping, duplicate detection, transaction control,
#          import history, preview mode, progress tracking, and detailed reporting.
# ==========================================================

import logging
import re
import time
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union, Callable

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text, Column, Integer, String, DateTime, Float, Text
from sqlalchemy.ext.declarative import declarative_base

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ==========================================================
# CONFIGURATION (can be overridden via environment)
# ==========================================================

class ImportConfig:
    """Central configuration for import engine."""
    # Batch size for inserts
    BATCH_SIZE = int(os.getenv("IMPORT_BATCH_SIZE", "1000"))
    # If True, all-or-nothing; if False, commit each batch separately
    ALL_OR_NOTHING = os.getenv("IMPORT_ALL_OR_NOTHING", "true").lower() == "true"
    # If True, detect duplicates based on (dn_no, material_no) and skip
    DEDUPLICATE = os.getenv("IMPORT_DEDUPLICATE", "true").lower() == "true"
    # If True, enable preview mode (dry-run)
    PREVIEW_MODE = os.getenv("IMPORT_PREVIEW_MODE", "false").lower() == "true"
    # Replace mode (truncate before insert)
    REPLACE_MODE = os.getenv("UPLOAD_REPLACE_MODE", "true").lower() == "true"
    # Path to column mapping JSON (optional)
    COLUMN_MAPPING_PATH = os.getenv("IMPORT_COLUMN_MAPPING_PATH", "")

# ==========================================================
# IMPORT HISTORY MODEL (if not already defined)
# ==========================================================

Base = declarative_base()

class ImportHistory(Base):
    """Tracks each import operation."""
    __tablename__ = "import_history"

    id = Column(Integer, primary_key=True)
    upload_batch_id = Column(String(100), unique=True, nullable=False)
    filename = Column(String(500), nullable=False)
    rows_read = Column(Integer, default=0)
    rows_inserted = Column(Integer, default=0)
    rows_failed = Column(Integer, default=0)
    rows_skipped = Column(Integer, default=0)
    duplicates_removed = Column(Integer, default=0)
    execution_time_seconds = Column(Float, default=0.0)
    status = Column(String(20), default="PENDING")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

def ensure_import_history_table(db: Session):
    """Create import_history table if it doesn't exist."""
    try:
        # Check if table exists
        result = db.execute(
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'import_history')")
        ).scalar()
        if not result:
            # Create table
            db.execute(
                text("""
                CREATE TABLE import_history (
                    id SERIAL PRIMARY KEY,
                    upload_batch_id VARCHAR(100) UNIQUE NOT NULL,
                    filename VARCHAR(500) NOT NULL,
                    rows_read INTEGER DEFAULT 0,
                    rows_inserted INTEGER DEFAULT 0,
                    rows_failed INTEGER DEFAULT 0,
                    rows_skipped INTEGER DEFAULT 0,
                    duplicates_removed INTEGER DEFAULT 0,
                    execution_time_seconds FLOAT DEFAULT 0,
                    status VARCHAR(20) DEFAULT 'PENDING',
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """)
            )
            db.commit()
            logger.info("✅ Created import_history table")
    except Exception as e:
        logger.warning(f"Could not create import_history table: {e}")
        # Continue anyway

# ==========================================================
# DYNAMIC HEADER NORMALIZER
# ==========================================================

def normalize_header(header: str) -> str:
    """
    Robust header normalization:
    - Strip whitespace
    - Replace multiple spaces with single underscore
    - Replace hyphens, slashes, dots with underscore
    - Convert to uppercase
    - Remove leading/trailing underscores
    - Collapse multiple underscores
    """
    if not header:
        return ""
    # Remove leading/trailing whitespace
    header = header.strip()
    # Replace any sequence of non-alphanumeric characters (except maybe underscore) with a single underscore
    # We want to keep alphanumeric and underscores, but replace other separators with underscore
    # Steps: Replace hyphens, slashes, dots, commas, etc. with underscore
    header = re.sub(r'[-/.,]+', '_', header)
    # Replace spaces with underscore
    header = re.sub(r'\s+', '_', header)
    # Remove any remaining non-alphanumeric (keep underscores)
    header = re.sub(r'[^A-Z0-9_]', '', header.upper())
    # Collapse multiple underscores
    header = re.sub(r'_+', '_', header)
    # Strip leading/trailing underscores
    header = header.strip('_')
    return header

# ==========================================================
# CENTRALIZED COLUMN MAPPING
# ==========================================================

# Base mapping (can be extended via environment or JSON)
BASE_COLUMN_MAP = {
    # Required columns
    "ORDER_TYPE": "order_type",
    "DN_NO": "dn_no",
    "DN_AMOUNT": "dn_amount",
    "DN_QTY": "dn_qty",
    "DN_WORK": "dn_work",
    "DIVISION": "division",
    "MATERIAL_NO": "material_no",
    "CUSTOMER_MODEL": "customer_model",
    "SALES_OFFICE": "sales_office",
    "SOLD_TO_PARTY_NAME": "customer_name",
    "SHIP_TO_CITY": "ship_to_city",
    "STORAGE": "storage_location",
    "WAREHOUSE": "warehouse",
    "DN_CREATE_DATE": "dn_create_date",
    "GOOD_ISSUE_DATE": "good_issue_date",
    "POD_DATE": "pod_date",
    "SALES_MANAGER": "sales_manager",
    # Optional columns
    "CUSTOMER_CODE": "customer_code",
    "DEALER_CODE": "dealer_code",
    "WAREHOUSE_CODE": "warehouse_code",
    "DELIVERY_LOCATION": "delivery_location",
    "REMARKS": "remarks",
}

# Required columns for validation
REQUIRED_COLUMNS = [
    "ORDER_TYPE", "DN_NO", "DN_AMOUNT", "DN_QTY", "DN_WORK", "DIVISION",
    "MATERIAL_NO", "CUSTOMER_MODEL", "SALES_OFFICE", "SOLD_TO_PARTY_NAME",
    "SHIP_TO_CITY", "STORAGE", "WAREHOUSE", "DN_CREATE_DATE", "GOOD_ISSUE_DATE",
    "POD_DATE", "SALES_MANAGER"
]

def load_column_mapping() -> Dict[str, str]:
    """Load column mapping from configuration, allowing overrides."""
    mapping = BASE_COLUMN_MAP.copy()
    # If a JSON file is specified, load and merge (optional)
    if ImportConfig.COLUMN_MAPPING_PATH:
        try:
            import json
            with open(ImportConfig.COLUMN_MAPPING_PATH, 'r') as f:
                overrides = json.load(f)
                mapping.update(overrides)
            logger.info(f"✅ Loaded column mapping overrides from {ImportConfig.COLUMN_MAPPING_PATH}")
        except Exception as e:
            logger.warning(f"Failed to load column mapping overrides: {e}")
    return mapping

# Global mapping instance
COLUMN_MAPPING = load_column_mapping()

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def _clean_value(val):
    if pd.isna(val):
        return None
    return val

def _safe_int(val) -> Optional[int]:
    if val is None or pd.isna(val):
        return None
    try:
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            clean = val.replace(',', '').strip()
            return int(float(clean))
    except (ValueError, TypeError):
        return None
    return None

def _safe_float(val) -> Optional[float]:
    if val is None or pd.isna(val):
        return None
    try:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            clean = val.replace(',', '').strip()
            return float(clean)
    except (ValueError, TypeError):
        return None
    return None

def _parse_date_from_excel(val) -> Optional[date]:
    if val is None or pd.isna(val):
        return None
    if isinstance(val, (datetime, date)):
        return val.date() if isinstance(val, datetime) else val
    if hasattr(val, 'to_pydatetime'):
        return val.to_pydatetime().date()
    if isinstance(val, str):
        val = val.strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        logger.warning(f"Could not parse date from string: {val}")
    if isinstance(val, (int, float)):
        from datetime import timedelta
        try:
            return (datetime(1899, 12, 30) + timedelta(days=float(val))).date()
        except Exception:
            pass
    return None

def _derive_statuses(pod_date, good_issue_date, dn_work=None):
    """
    Derive business statuses. This can be extended based on business logic.
    Currently uses dates, but dn_work could influence status.
    Example: if dn_work == 'Invoiced' and pod_date exists, status might be 'Delivered'.
    """
    delivery_status = "Delivered" if pod_date else "Pending"
    pgi_status = "Completed" if good_issue_date else "Pending"
    pod_status = "Completed" if pod_date else "Pending"
    pending_flag = False if pod_date else True
    # Additional logic can be added here
    # if dn_work and 'Shipped' in dn_work and pod_date:
    #     delivery_status = "Shipped"
    return delivery_status, pgi_status, pod_status, pending_flag

def _create_backup(db: Session) -> int:
    """Create a backup table and return count."""
    try:
        db.execute(text("DROP TABLE IF EXISTS delivery_reports_backup"))
        db.execute(text("CREATE TABLE delivery_reports_backup AS SELECT * FROM delivery_reports"))
        db.commit()
        count = db.execute(text("SELECT COUNT(*) FROM delivery_reports_backup")).scalar()
        logger.info(f"✅ Backup created: {count} records in delivery_reports_backup")
        return count
    except Exception as e:
        logger.exception("Backup failed")
        raise ExcelImportServiceError(f"Backup failed: {str(e)}")

def _truncate_table(db: Session):
    try:
        db.execute(text("TRUNCATE TABLE delivery_reports RESTART IDENTITY CASCADE"))
        db.flush()
        logger.info("✅ delivery_reports truncated.")
    except Exception as e:
        logger.exception("Truncation failed")
        raise ExcelImportServiceError(f"Truncation failed: {str(e)}")

# ==========================================================
# CORE IMPORT ENGINE
# ==========================================================

class ExcelImportEngine:
    """
    Enterprise Excel Import Engine.
    Handles all steps from reading to reporting.
    """

    def __init__(self, db: Session, file_path: str, source_filename: str,
                 upload_batch_id: str, replace_mode: bool = None,
                 preview: bool = None, progress_callback: Callable = None):
        self.db = db
        self.file_path = file_path
        self.source_filename = source_filename
        self.upload_batch_id = upload_batch_id
        self.replace_mode = replace_mode if replace_mode is not None else ImportConfig.REPLACE_MODE
        self.preview = preview if preview is not None else ImportConfig.PREVIEW_MODE
        self.progress_callback = progress_callback or (lambda msg, pct: None)
        self.metrics = {
            "rows_read": 0,
            "rows_valid": 0,
            "rows_inserted": 0,
            "rows_failed": 0,
            "rows_skipped": 0,
            "duplicates_removed": 0,
            "batch_id": upload_batch_id,
            "filename": source_filename,
            "execution_time_seconds": 0.0,
            "import_speed_rows_per_second": 0.0,
            "errors": [],
            "warnings": [],
            "status": "PENDING"
        }
        self.df = None
        self.mapping = {}
        self.records = []
        self.duplicate_keys = set()

    def _progress(self, msg: str, pct: int):
        """Report progress."""
        logger.info(f"PROGRESS: {msg} ({pct}%)")
        if self.progress_callback:
            self.progress_callback(msg, pct)

    def _read_excel(self):
        self._progress("Reading Excel...", 10)
        try:
            df = pd.read_excel(self.file_path, engine='openpyxl', dtype=str, keep_default_na=False)
            df = df.replace(r'^\s*$', np.nan, regex=True)
            self.df = df
            self.metrics["rows_read"] = len(df)
        except Exception as e:
            logger.exception(f"Failed to read Excel file: {self.file_path}")
            raise ExcelImportServiceError(f"Failed to read Excel file: {str(e)}")
        self._progress("Excel read complete", 20)

    def _normalize_headers(self):
        self._progress("Normalizing headers...", 25)
        original_headers = list(self.df.columns)
        normalized = [normalize_header(h) for h in original_headers]
        self.df.columns = normalized
        # Warn about duplicates in normalized headers
        seen = set()
        duplicates = []
        for h in normalized:
            if h in seen:
                duplicates.append(h)
            else:
                seen.add(h)
        if duplicates:
            warn_msg = f"Duplicate normalized headers: {', '.join(duplicates)}"
            self.metrics["warnings"].append(warn_msg)
            logger.warning(warn_msg)
        self._progress("Headers normalized", 30)

    def _validate_columns(self):
        self._progress("Validating columns...", 35)
        missing = [col for col in REQUIRED_COLUMNS if col not in self.df.columns]
        if missing:
            raise VerificationError(f"Required columns missing: {', '.join(missing)}")
        # Build dynamic mapping from present columns
        self.mapping = {}
        for excel_col, model_field in COLUMN_MAPPING.items():
            if excel_col in self.df.columns:
                self.mapping[excel_col] = model_field
        # Log optional missing
        optional_missing = [k for k in COLUMN_MAPPING if k not in self.df.columns and k not in REQUIRED_COLUMNS]
        if optional_missing:
            self.metrics["warnings"].append(f"Optional columns missing: {', '.join(optional_missing)}")
            logger.warning(f"Optional columns missing: {', '.join(optional_missing)}")
        self._progress("Columns validated", 40)

    def _check_duplicates(self):
        """Detect duplicates based on (dn_no, material_no) and prepare to skip."""
        if not ImportConfig.DEDUPLICATE:
            return
        self._progress("Checking duplicates...", 45)
        # Find duplicates in the dataframe
        key_cols = ["DN_NO", "MATERIAL_NO"]
        # Ensure the columns exist
        if all(col in self.df.columns for col in key_cols):
            # Create a composite key
            self.df['_key'] = self.df["DN_NO"].astype(str) + "|" + self.df["MATERIAL_NO"].astype(str)
            # Keep first occurrence, mark duplicates
            duplicate_mask = self.df.duplicated(subset=['_key'], keep='first')
            self.duplicate_keys = set(self.df[duplicate_mask]['_key'].values)
            if self.duplicate_keys:
                logger.info(f"Detected {len(self.duplicate_keys)} duplicate rows based on DN_NO + MATERIAL_NO")
                self.metrics["warnings"].append(f"Detected {len(self.duplicate_keys)} duplicate rows")
            # Drop the temporary key column
            self.df = self.df.drop(columns=['_key'])
        self._progress("Duplicate check complete", 50)

    def _transform_data(self):
        self._progress("Transforming data...", 55)
        records = []
        errors = []
        seen_keys = set()  # for duplicate detection within the file

        for idx, row in self.df.iterrows():
            try:
                dn_no = _clean_value(row.get("DN_NO"))
                if not dn_no:
                    raise ValueError("DN_NO is empty or missing")

                # Duplicate detection (if enabled and we have keys)
                if ImportConfig.DEDUPLICATE:
                    material_no = _clean_value(row.get("MATERIAL_NO"))
                    key = f"{dn_no}|{material_no}"
                    if key in seen_keys:
                        raise ValueError("Duplicate row (DN_NO + MATERIAL_NO)")
                    seen_keys.add(key)

                record = {}
                for excel_col, model_field in self.mapping.items():
                    raw = _clean_value(row[excel_col])
                    if raw is None:
                        record[model_field] = None
                        continue
                    if model_field in ("dn_amount",):
                        record[model_field] = _safe_float(raw)
                    elif model_field in ("dn_qty",):
                        record[model_field] = _safe_int(raw)
                    elif model_field in ("dn_create_date", "good_issue_date", "pod_date"):
                        record[model_field] = _parse_date_from_excel(raw)
                    else:
                        record[model_field] = str(raw) if raw is not None else None

                # Derive statuses
                pod_date = record.get("pod_date")
                good_issue_date = record.get("good_issue_date")
                dn_work = record.get("dn_work")
                delivery_status, pgi_status, pod_status, pending_flag = _derive_statuses(
                    pod_date, good_issue_date, dn_work
                )
                record["delivery_status"] = delivery_status
                record["pgi_status"] = pgi_status
                record["pod_status"] = pod_status
                record["pending_flag"] = pending_flag
                record["source_file"] = self.source_filename
                record["upload_batch_id"] = self.upload_batch_id

                records.append(record)
                self.metrics["rows_valid"] += 1

            except Exception as e:
                logger.warning(f"Row {idx+2} failed validation: {e}")
                errors.append({"row": idx+2, "error": str(e)})
                self.metrics["rows_failed"] += 1

        self.records = records
        self.metrics["errors"] = [f"Row {e['row']}: {e['error']}" for e in errors]
        self.metrics["duplicates_removed"] = len(self.duplicate_keys)
        self._progress("Data transformation complete", 65)

    def _preview(self):
        """Return preview summary without inserting."""
        self._progress("Generating preview...", 80)
        # Simulate truncation count if replace mode
        preview_data = {
            "batch_id": self.upload_batch_id,
            "filename": self.source_filename,
            "rows_read": self.metrics["rows_read"],
            "rows_valid": self.metrics["rows_valid"],
            "rows_failed": self.metrics["rows_failed"],
            "rows_skipped": self.metrics["rows_skipped"],
            "duplicates_removed": self.metrics["duplicates_removed"],
            "replace_mode": self.replace_mode,
            "preview": True,
            "warnings": self.metrics["warnings"],
            "errors": self.metrics["errors"]
        }
        return preview_data

    def _backup_and_truncate(self):
        if not self.replace_mode:
            return
        self._progress("Backing up existing data...", 70)
        backup_count = _create_backup(self.db)
        self.metrics["backup_count"] = backup_count
        self._progress("Truncating table...", 75)
        _truncate_table(self.db)

    def _insert_batches(self):
        self._progress("Inserting data...", 80)
        if not self.records:
            logger.info("No valid records to insert.")
            return

        batch_size = ImportConfig.BATCH_SIZE
        total_inserted = 0
        try:
            if ImportConfig.ALL_OR_NOTHING:
                # Single transaction for all batches
                for i in range(0, len(self.records), batch_size):
                    batch = self.records[i:i+batch_size]
                    self.db.bulk_insert_mappings(DeliveryReport, batch)
                self.db.flush()
                total_inserted = len(self.records)
                logger.info(f"✅ Inserted all {total_inserted} records in single transaction")
            else:
                # Commit each batch separately
                for i in range(0, len(self.records), batch_size):
                    batch = self.records[i:i+batch_size]
                    self.db.bulk_insert_mappings(DeliveryReport, batch)
                    self.db.flush()
                    self.db.commit()
                    total_inserted += len(batch)
                    self._progress(f"Inserted {total_inserted} records...", 80 + int((i+batch_size)/len(self.records)*15))
                logger.info(f"✅ Inserted {total_inserted} records in batches of {batch_size}")
        except Exception as e:
            logger.exception("Bulk insert failed")
            self.db.rollback()
            raise ExcelImportServiceError(f"Database insert failed: {str(e)}")

        self.metrics["rows_inserted"] = total_inserted
        self._progress("Insert complete", 95)

    def _verify(self):
        """Verify that inserted rows match expected."""
        self._progress("Verifying insertion...", 97)
        # Count rows in delivery_reports for this batch
        count = self.db.execute(
            text("SELECT COUNT(*) FROM delivery_reports WHERE upload_batch_id = :batch"),
            {"batch": self.upload_batch_id}
        ).scalar()
        if count != self.metrics["rows_inserted"]:
            self.metrics["warnings"].append(
                f"Verification mismatch: Expected {self.metrics['rows_inserted']}, but found {count} in DB"
            )
            logger.warning(f"Verification mismatch: Inserted {self.metrics['rows_inserted']}, DB has {count}")
        else:
            logger.info(f"✅ Verification passed: {count} rows match.")

    def _record_history(self):
        """Record import history."""
        try:
            ensure_import_history_table(self.db)
            history = ImportHistory(
                upload_batch_id=self.upload_batch_id,
                filename=self.source_filename,
                rows_read=self.metrics["rows_read"],
                rows_inserted=self.metrics["rows_inserted"],
                rows_failed=self.metrics["rows_failed"],
                rows_skipped=self.metrics["rows_skipped"],
                duplicates_removed=self.metrics["duplicates_removed"],
                execution_time_seconds=self.metrics["execution_time_seconds"],
                status="SUCCESS" if self.metrics["rows_failed"] == 0 else "PARTIAL",
                error_message=None
            )
            self.db.add(history)
            self.db.commit()
            logger.info(f"✅ Import history recorded for batch {self.upload_batch_id}")
        except Exception as e:
            logger.warning(f"Failed to record import history: {e}")

    def run(self) -> Dict[str, Any]:
        """Execute the full import pipeline."""
        start_time = time.time()
        try:
            self._progress("Starting import", 0)
            self._read_excel()
            self._normalize_headers()
            self._validate_columns()
            self._check_duplicates()
            self._transform_data()

            # If preview mode, return summary without inserting
            if self.preview:
                self._progress("Preview mode - skipping insert", 100)
                preview_data = self._preview()
                preview_data["execution_time_seconds"] = round(time.time() - start_time, 2)
                return preview_data

            # Proceed with actual import
            self._backup_and_truncate()
            self._insert_batches()
            self._verify()
            self.metrics["execution_time_seconds"] = round(time.time() - start_time, 2)
            self.metrics["import_speed_rows_per_second"] = round(
                self.metrics["rows_inserted"] / self.metrics["execution_time_seconds"],
                2
            ) if self.metrics["execution_time_seconds"] > 0 else 0.0
            self.metrics["status"] = "SUCCESS" if self.metrics["rows_failed"] == 0 else "PARTIAL"
            self._record_history()
            self._progress("Import complete", 100)
            return self.metrics
        except VerificationError as e:
            self.metrics["status"] = "FAILED"
            self.metrics["errors"].append(str(e))
            logger.error(f"Import failed: {e}")
            raise
        except Exception as e:
            self.metrics["status"] = "FAILED"
            self.metrics["errors"].append(str(e))
            logger.exception("Import failed")
            raise

# ==========================================================
# PUBLIC INTERFACE (backward compatible)
# ==========================================================

def import_delivery_excel(
    db: Session,
    file_path: str,
    source_filename: str,
    upload_batch_id: str,
    batch_size: int = 1000,
    replace_mode: bool = None
) -> Dict[str, Any]:
    """
    Legacy-compatible entry point.
    """
    # Instantiate engine with default config
    engine = ExcelImportEngine(
        db=db,
        file_path=file_path,
        source_filename=source_filename,
        upload_batch_id=upload_batch_id,
        replace_mode=replace_mode,
        preview=False
    )
    return engine.run()
