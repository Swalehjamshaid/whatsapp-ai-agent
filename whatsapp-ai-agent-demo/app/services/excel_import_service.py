# ==========================================================
# FILE: app/services/excel_import_service.py (v8.1 - ENTERPRISE)
# ==========================================================

import logging
import os
import re
import time
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Callable

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text, Column, Integer, String, DateTime, Float, Text
from sqlalchemy.ext.declarative import declarative_base

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ==========================================================
# CONFIGURATION
# ==========================================================

class ImportConfig:
    BATCH_SIZE = int(os.getenv("IMPORT_BATCH_SIZE", "1000"))
    ALL_OR_NOTHING = os.getenv("IMPORT_ALL_OR_NOTHING", "true").lower() == "true"
    DEDUPLICATE = os.getenv("IMPORT_DEDUPLICATE", "true").lower() == "true"
    PREVIEW_MODE = os.getenv("IMPORT_PREVIEW_MODE", "false").lower() == "true"
    REPLACE_MODE = os.getenv("UPLOAD_REPLACE_MODE", "true").lower() == "true"
    COLUMN_MAPPING_PATH = os.getenv("IMPORT_COLUMN_MAPPING_PATH", "")

# ==========================================================
# IMPORT HISTORY MODEL
# ==========================================================

Base = declarative_base()

class ImportHistory(Base):
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
    try:
        result = db.execute(
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'import_history')")
        ).scalar()
        if not result:
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

# ==========================================================
# DYNAMIC HEADER NORMALIZER
# ==========================================================

def normalize_header(header: str) -> str:
    if not header:
        return ""
    header = header.strip()
    header = re.sub(r'[-/.,]+', '_', header)
    header = re.sub(r'\s+', '_', header)
    header = re.sub(r'[^A-Z0-9_]', '', header.upper())
    header = re.sub(r'_+', '_', header)
    header = header.strip('_')
    return header

# ==========================================================
# CENTRALIZED COLUMN MAPPING
# ==========================================================

BASE_COLUMN_MAP = {
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
    "CUSTOMER_CODE": "customer_code",
    "DEALER_CODE": "dealer_code",
    "WAREHOUSE_CODE": "warehouse_code",
    "DELIVERY_LOCATION": "delivery_location",
    "REMARKS": "remarks",
}

# Required columns (only those that must exist in every Excel file)
REQUIRED_COLUMNS = [
    "ORDER_TYPE", "DN_NO", "DN_AMOUNT", "DN_QTY", "DN_WORK", "DIVISION",
    "MATERIAL_NO", "CUSTOMER_MODEL", "SALES_OFFICE", "SOLD_TO_PARTY_NAME",
    "SHIP_TO_CITY", "STORAGE", "WAREHOUSE", "DN_CREATE_DATE", "SALES_MANAGER"
]

def load_column_mapping() -> Dict[str, str]:
    mapping = BASE_COLUMN_MAP.copy()
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
    delivery_status = "Delivered" if pod_date else "Pending"
    pgi_status = "Completed" if good_issue_date else "Pending"
    pod_status = "Completed" if pod_date else "Pending"
    pending_flag = False if pod_date else True
    return delivery_status, pgi_status, pod_status, pending_flag

def _create_backup(db: Session) -> str:
    """Create a timestamped backup table and return its name."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_table = f"delivery_reports_backup_{timestamp}"
    try:
        db.execute(text(f"DROP TABLE IF EXISTS {backup_table}"))
        db.execute(text(f"CREATE TABLE {backup_table} AS SELECT * FROM delivery_reports"))
        db.commit()
        count = db.execute(text(f"SELECT COUNT(*) FROM {backup_table}")).scalar()
        logger.info(f"✅ Backup created: {backup_table} with {count} records")
        return backup_table
    except Exception as e:
        logger.exception("Backup failed")
        raise ExcelImportServiceError(f"Backup failed: {str(e)}")

def _truncate_table(db: Session):
    try:
        db.execute(text("TRUNCATE TABLE delivery_reports RESTART IDENTITY CASCADE"))
        db.commit()
        logger.info("✅ delivery_reports truncated.")
    except Exception as e:
        logger.exception("Truncation failed")
        raise ExcelImportServiceError(f"Truncation failed: {str(e)}")

# ==========================================================
# CORE IMPORT ENGINE
# ==========================================================

class ExcelImportEngine:
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
        self.mapping = {}
        for excel_col, model_field in COLUMN_MAPPING.items():
            if excel_col in self.df.columns:
                self.mapping[excel_col] = model_field
        optional_missing = [k for k in COLUMN_MAPPING if k not in self.df.columns and k not in REQUIRED_COLUMNS]
        if optional_missing:
            self.metrics["warnings"].append(f"Optional columns missing: {', '.join(optional_missing)}")
            logger.warning(f"Optional columns missing: {', '.join(optional_missing)}")
        self._progress("Columns validated", 40)

    def _check_duplicates(self):
        if not ImportConfig.DEDUPLICATE:
            return
        self._progress("Checking duplicates...", 45)
        key_cols = ["DN_NO", "MATERIAL_NO"]
        if all(col in self.df.columns for col in key_cols):
            self.df['_key'] = self.df["DN_NO"].astype(str) + "|" + self.df["MATERIAL_NO"].astype(str)
            duplicate_mask = self.df.duplicated(subset=['_key'], keep='first')
            self.duplicate_keys = set(self.df[duplicate_mask]['_key'].values)
            if self.duplicate_keys:
                logger.info(f"Detected {len(self.duplicate_keys)} duplicate rows based on DN_NO + MATERIAL_NO")
                self.metrics["warnings"].append(f"Detected {len(self.duplicate_keys)} duplicate rows")
            self.df = self.df.drop(columns=['_key'])
        self._progress("Duplicate check complete", 50)

    def _check_db_duplicates(self):
        """If not replacing, check existing DB for duplicates and skip them."""
        if self.replace_mode or not ImportConfig.DEDUPLICATE:
            return
        self._progress("Checking existing database duplicates...", 52)
        # Get existing keys from DB
        existing_keys = set()
        try:
            result = self.db.execute(
                text("SELECT dn_no, material_no FROM delivery_reports")
            ).fetchall()
            for row in result:
                existing_keys.add(f"{row[0]}|{row[1]}")
        except Exception as e:
            logger.warning(f"Could not fetch existing keys: {e}")
            return
        # Filter out records that already exist
        filtered_records = []
        for record in self.records:
            key = f"{record.get('dn_no')}|{record.get('material_no')}"
            if key in existing_keys:
                self.metrics["duplicates_removed"] += 1
                continue
            filtered_records.append(record)
        removed = len(self.records) - len(filtered_records)
        if removed:
            logger.info(f"Skipped {removed} duplicate rows already in database")
        self.records = filtered_records

    def _transform_data(self):
        self._progress("Transforming data...", 55)
        records = []
        errors = []
        seen_keys = set()

        for idx, row in self.df.iterrows():
            try:
                dn_no = _clean_value(row.get("DN_NO"))
                if not dn_no:
                    raise ValueError("DN_NO is empty or missing")

                if ImportConfig.DEDUPLICATE:
                    material_no = _clean_value(row.get("MATERIAL_NO"))
                    key = f"{dn_no}|{material_no}"
                    if key in seen_keys:
                        raise ValueError("Duplicate row within file (DN_NO + MATERIAL_NO)")
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
        self.metrics["duplicates_removed"] += len(self.duplicate_keys)
        self._progress("Data transformation complete", 65)

    def _preview(self):
        self._progress("Generating preview...", 80)
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
        backup_table = _create_backup(self.db)
        self.metrics["backup_table"] = backup_table
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
                for i in range(0, len(self.records), batch_size):
                    batch = self.records[i:i+batch_size]
                    self.db.bulk_insert_mappings(DeliveryReport, batch)
                self.db.flush()
                total_inserted = len(self.records)
                logger.info(f"✅ Inserted all {total_inserted} records in single transaction")
            else:
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
        self._progress("Verifying insertion...", 97)
        count = self.db.execute(
            text("SELECT COUNT(*) FROM delivery_reports WHERE upload_batch_id = :batch"),
            {"batch": self.upload_batch_id}
        ).scalar()
        if count != self.metrics["rows_inserted"]:
            raise VerificationError(
                f"Verification failed: Expected {self.metrics['rows_inserted']} rows, but found {count} in DB"
            )
        logger.info(f"✅ Verification passed: {count} rows match.")

    def _record_history(self):
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
        start_time = time.time()
        try:
            self._progress("Starting import", 0)
            self._read_excel()
            self._normalize_headers()
            self._validate_columns()
            self._check_duplicates()
            self._transform_data()
            self._check_db_duplicates()  # New check

            if self.preview:
                self._progress("Preview mode - skipping insert", 100)
                preview_data = self._preview()
                preview_data["execution_time_seconds"] = round(time.time() - start_time, 2)
                return preview_data

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
            self.db.rollback()
            self.metrics["status"] = "FAILED"
            self.metrics["errors"].append(str(e))
            logger.error(f"Import failed: {e}")
            raise
        except Exception as e:
            self.db.rollback()
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
    engine = ExcelImportEngine(
        db=db,
        file_path=file_path,
        source_filename=source_filename,
        upload_batch_id=upload_batch_id,
        replace_mode=replace_mode,
        preview=False
    )
    return engine.run()
