# ==========================================================
# FILE: app/services/excel_import_service.py (v10.0 - ATOMIC STAGING)
# ==========================================================
# PURPOSE: Finishes existing data first. Validates new file into a staging
#          table, performs an atomic swap, and backs up the old data.
# ==========================================================

import logging
import os
import re
import time
import json
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Callable

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text, Column, Integer, String, DateTime, Float, Text

# CORRECT FIX: Import the main app Base from database.py instead of creating a new one
from app.database import Base
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
    CHUNK_SIZE = int(os.getenv("IMPORT_CHUNK_SIZE", "0"))

# ==========================================================
# IMPORT HISTORY MODEL (Uses the shared app.database Base)
# ==========================================================

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
        # Use raw SQL to avoid ORM conflicts
        db.execute(
            text("""
            CREATE TABLE IF NOT EXISTS import_history (
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
        logger.info("✅ Created/Verified import_history table")
    except Exception as e:
        logger.warning(f"Could not create import_history table: {e}")

# ==========================================================
# DYNAMIC HEADER NORMALIZER
# ==========================================================

def normalize_header(header: str) -> str:
    if not header: return ""
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
    "ORDER_TYPE": "order_type", "DN_NO": "dn_no", "DN_AMOUNT": "dn_amount",
    "DN_QTY": "dn_qty", "DN_WORK": "dn_work", "DIVISION": "division",
    "MATERIAL_NO": "material_no", "CUSTOMER_MODEL": "customer_model",
    "SALES_OFFICE": "sales_office", "SOLD_TO_PARTY_NAME": "customer_name",
    "SHIP_TO_CITY": "ship_to_city", "STORAGE": "storage_location",
    "WAREHOUSE": "warehouse", "DN_CREATE_DATE": "dn_create_date",
    "GOOD_ISSUE_DATE": "good_issue_date", "POD_DATE": "pod_date",
    "SALES_MANAGER": "sales_manager", "CUSTOMER_CODE": "customer_code",
    "DEALER_CODE": "dealer_code", "WAREHOUSE_CODE": "warehouse_code",
    "DELIVERY_LOCATION": "delivery_location", "REMARKS": "remarks",
}

REQUIRED_COLUMNS = [
    "ORDER_TYPE", "DN_NO", "DN_AMOUNT", "DN_QTY", "DN_WORK", "DIVISION",
    "MATERIAL_NO", "CUSTOMER_MODEL", "SALES_OFFICE", "SOLD_TO_PARTY_NAME",
    "SHIP_TO_CITY", "STORAGE", "WAREHOUSE", "DN_CREATE_DATE", "SALES_MANAGER"
]

def load_column_mapping() -> Dict[str, str]:
    mapping = BASE_COLUMN_MAP.copy()
    if ImportConfig.COLUMN_MAPPING_PATH:
        try:
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
    return None if pd.isna(val) else val

def _safe_int(val) -> Optional[int]:
    if val is None or pd.isna(val): return None
    try:
        if isinstance(val, (int, float)): return int(val)
        if isinstance(val, str):
            clean = re.sub(r'[^\d.]', '', val.replace(',', '').strip())
            return int(float(clean))
    except: return None
    return None

def _safe_float(val) -> Optional[float]:
    if val is None or pd.isna(val): return None
    try:
        if isinstance(val, (int, float)): return float(val)
        if isinstance(val, str):
            clean = re.sub(r'[^\d.]', '', val.replace(',', '').strip())
            return float(clean)
    except: return None
    return None

def _parse_date_from_excel(val) -> Optional[date]:
    if val is None or pd.isna(val): return None
    if isinstance(val, (datetime, date)): return val.date() if isinstance(val, datetime) else val
    if hasattr(val, 'to_pydatetime'): return val.to_pydatetime().date()
    if isinstance(val, str):
        val = val.strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
            try: return datetime.strptime(val, fmt).date()
            except ValueError: continue
        logger.warning(f"Could not parse date from string: {val}")
    if isinstance(val, (int, float)):
        from datetime import timedelta
        try: return (datetime(1899, 12, 30) + timedelta(days=float(val))).date()
        except: pass
    return None

def _derive_statuses(pod_date, good_issue_date, dn_work=None):
    delivery_status = "Delivered" if pod_date else "Pending"
    pgi_status = "Completed" if good_issue_date else "Pending"
    pod_status = "Completed" if pod_date else "Pending"
    pending_flag = False if pod_date else True
    return delivery_status, pgi_status, pod_status, pending_flag

# ==========================================================
# CUSTOM EXCEPTIONS
# ==========================================================

class ExcelImportServiceError(Exception): pass
class VerificationError(Exception): pass

# ==========================================================
# CORE IMPORT ENGINE (v10.0 - ATOMIC SWAP)
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
            "rows_read": 0, "rows_valid": 0, "rows_inserted": 0,
            "rows_failed": 0, "rows_skipped": 0, "duplicates_removed": 0,
            "batch_id": upload_batch_id, "filename": source_filename,
            "execution_time_seconds": 0.0, "import_speed_rows_per_second": 0.0,
            "errors": [], "warnings": [], "status": "PENDING"
        }
        self.df = None
        self.mapping = {}
        self.records = []

    def _progress(self, msg: str, pct: int):
        logger.info(f"PROGRESS: {msg} ({pct}%)")
        if self.progress_callback: self.progress_callback(msg, pct)

    def _read_excel(self):
        self._progress("Reading Excel...", 10)
        try:
            if ImportConfig.CHUNK_SIZE > 0:
                chunks = pd.read_excel(self.file_path, engine='openpyxl', dtype=str,
                                       keep_default_na=False, chunksize=ImportConfig.CHUNK_SIZE)
                self.df = pd.concat([chunk.replace(r'^\s*$', np.nan, regex=True) for chunk in chunks], ignore_index=True)
            else:
                self.df = pd.read_excel(self.file_path, engine='openpyxl', dtype=str, keep_default_na=False)
                self.df = self.df.replace(r'^\s*$', np.nan, regex=True)
            self.metrics["rows_read"] = len(self.df)
        except Exception as e:
            logger.exception(f"Failed to read Excel file: {self.file_path}")
            raise ExcelImportServiceError(f"Failed to read Excel file: {str(e)}")
        self._progress("Excel read complete", 20)

    def _normalize_headers(self):
        self._progress("Normalizing headers...", 25)
        self.df.columns = [normalize_header(h) for h in list(self.df.columns)]
        duplicates = [h for h in set(self.df.columns) if list(self.df.columns).count(h) > 1]
        if duplicates:
            self.metrics["warnings"].append(f"Duplicate normalized headers: {', '.join(duplicates)}")
            logger.warning(f"Duplicate normalized headers: {', '.join(duplicates)}")
        self._progress("Headers normalized", 30)

    def _validate_columns(self):
        self._progress("Validating columns...", 35)
        missing = [col for col in REQUIRED_COLUMNS if col not in self.df.columns]
        if missing: raise VerificationError(f"Required columns missing: {', '.join(missing)}")
        self.mapping = {excel: field for excel, field in COLUMN_MAPPING.items() if excel in self.df.columns}
        optional_missing = [k for k in COLUMN_MAPPING if k not in self.df.columns and k not in REQUIRED_COLUMNS]
        if optional_missing:
            self.metrics["warnings"].append(f"Optional columns missing: {', '.join(optional_missing)}")
            logger.warning(f"Optional columns missing: {', '.join(optional_missing)}")
        self._progress("Columns validated", 40)

    def _check_duplicates(self):
        if not ImportConfig.DEDUPLICATE: return
        self._progress("Checking duplicates...", 45)
        if all(col in self.df.columns for col in ["DN_NO", "MATERIAL_NO"]):
            self.df['_key'] = self.df["DN_NO"].astype(str) + "|" + self.df["MATERIAL_NO"].astype(str)
            duplicates = self.df.duplicated(subset=['_key'], keep='first')
            if duplicates.any():
                self.metrics["warnings"].append(f"Detected {duplicates.sum()} duplicate rows based on DN_NO + MATERIAL_NO")
                logger.info(f"Detected {duplicates.sum()} duplicate rows")
                self.metrics["duplicates_removed"] += duplicates.sum()
            self.df = self.df.drop(columns=['_key'])
        self._progress("Duplicate check complete", 50)

    def _transform_data(self):
        self._progress("Transforming data...", 55)
        records, errors, seen_keys = [], [], set()
        for idx, row in self.df.iterrows():
            try:
                dn_no = _clean_value(row.get("DN_NO"))
                if not dn_no: raise ValueError("DN_NO is empty or missing")

                if ImportConfig.DEDUPLICATE:
                    material_no = _clean_value(row.get("MATERIAL_NO"))
                    key = f"{dn_no}|{material_no}"
                    if key in seen_keys: raise ValueError("Duplicate row (DN_NO + MATERIAL_NO)")
                    seen_keys.add(key)

                record = {}
                for excel_col, model_field in self.mapping.items():
                    raw = _clean_value(row[excel_col])
                    if raw is None:
                        record[model_field] = None
                    elif model_field in ("dn_amount",):
                        record[model_field] = _safe_float(raw)
                    elif model_field in ("dn_qty",):
                        record[model_field] = _safe_int(raw)
                    elif model_field in ("dn_create_date", "good_issue_date", "pod_date"):
                        record[model_field] = _parse_date_from_excel(raw)
                    else:
                        record[model_field] = str(raw) if raw is not None else None

                pod_date, good_issue_date = record.get("pod_date"), record.get("good_issue_date")
                ds, ps, pos, pf = _derive_statuses(pod_date, good_issue_date, record.get("dn_work"))
                record.update({"delivery_status": ds, "pgi_status": ps, "pod_status": pos, "pending_flag": pf,
                               "source_file": self.source_filename, "upload_batch_id": self.upload_batch_id})
                records.append(record)
                self.metrics["rows_valid"] += 1
            except Exception as e:
                logger.warning(f"Row {idx+2} failed validation: {e}")
                errors.append({"row": idx+2, "error": str(e)})
                self.metrics["rows_failed"] += 1
        self.records = records
        self.metrics["errors"] = [f"Row {e['row']}: {e['error']}" for e in errors]
        self._progress("Data transformation complete", 65)

    def _insert_into_staging(self):
        self._progress("Inserting into staging table...", 80)
        if not self.records: return

        staging_table = f"delivery_reports_staging_{self.upload_batch_id}"
        try:
            # Create staging table with same structure as live table
            self.db.execute(text(f"CREATE TABLE {staging_table} (LIKE delivery_reports INCLUDING DEFAULTS)"))
            
            batch_size, total_inserted = ImportConfig.BATCH_SIZE, 0
            for i in range(0, len(self.records), batch_size):
                batch = self.records[i:i+batch_size]
                self.db.bulk_insert_mappings(DeliveryReport, batch, render_nulls=True) # render_nulls ensures None maps to NULL
                self.db.flush()
                total_inserted += len(batch)
                self._progress(f"Staged {total_inserted} records...", 80 + int((i+batch_size)/len(self.records)*15))
            
            # Verify staging table count
            count = self.db.execute(text(f"SELECT COUNT(*) FROM {staging_table}")).scalar()
            if count != total_inserted:
                raise VerificationError("Staging table row count mismatch after insert.")
            
            logger.info(f"✅ Successfully inserted {total_inserted} records into staging table: {staging_table}")
            return staging_table
        except Exception as e:
            self.db.rollback()
            logger.exception("Staging insert failed")
            raise ExcelImportServiceError(f"Staging insert failed: {str(e)}")

    def _swap_live_tables(self, staging_table: str):
        self._progress("Swapping staging table with live table...", 95)
        try:
            # 1. Backup the existing live table (finishes existing data safely)
            backup_timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_table = f"delivery_reports_backup_{backup_timestamp}_{self.upload_batch_id[:8]}"
            self.db.execute(text(f"ALTER TABLE delivery_reports RENAME TO {backup_table}"))
            
            # 2. Swap the staging table into the live table's place
            self.db.execute(text(f"ALTER TABLE {staging_table} RENAME TO delivery_reports"))
            
            # 3. Commit the swap
            self.db.commit()
            logger.info(f"✅ Atomic swap complete! Old data backed up to {backup_table}, new data is now live.")
            self.metrics["backup_table"] = backup_table
        except Exception as e:
            self.db.rollback()
            logger.exception("Table swap failed")
            raise ExcelImportServiceError(f"Swap failed: {str(e)}")

    def _record_history(self):
        try:
            ensure_import_history_table(self.db)
            history = ImportHistory(
                upload_batch_id=self.upload_batch_id, filename=self.source_filename,
                rows_read=self.metrics["rows_read"], rows_inserted=self.metrics["rows_inserted"],
                rows_failed=self.metrics["rows_failed"], rows_skipped=self.metrics["rows_skipped"],
                duplicates_removed=self.metrics["duplicates_removed"],
                execution_time_seconds=self.metrics["execution_time_seconds"],
                status="SUCCESS" if self.metrics["rows_failed"] == 0 else "PARTIAL"
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

            # ----------------- PREVIEW MODE -----------------
            if self.preview:
                self._progress("Preview mode - skipping insert", 100)
                return {
                    "batch_id": self.upload_batch_id, "filename": self.source_filename,
                    "rows_read": self.metrics["rows_read"], "rows_valid": self.metrics["rows_valid"],
                    "rows_failed": self.metrics["rows_failed"], "rows_skipped": self.metrics["rows_skipped"],
                    "duplicates_removed": self.metrics["duplicates_removed"], "replace_mode": self.replace_mode,
                    "preview": True, "warnings": self.metrics["warnings"], "errors": self.metrics["errors"],
                    "execution_time_seconds": round(time.time() - start_time, 2)
                }

            # ----------------- STAGING INSERT -----------------
            # Existing PostgreSQL data remains perfectly untouched during this process
            self._progress("Inserting new data into isolated staging table...", 80)
            staging_table = self._insert_into_staging()

            # ----------------- ATOMIC SWAP (Finish existing data, then swap) -----------------
            self._swap_live_tables(staging_table)

            self.metrics["rows_inserted"] = self.metrics["rows_valid"]
            self.metrics["execution_time_seconds"] = round(time.time() - start_time, 2)
            self.metrics["import_speed_rows_per_second"] = round(self.metrics["rows_inserted"] / self.metrics["execution_time_seconds"], 2) if self.metrics["execution_time_seconds"] > 0 else 0.0
            self.metrics["status"] = "SUCCESS" if self.metrics["rows_failed"] == 0 else "PARTIAL"
            self._record_history()
            self._progress("Import complete", 100)
            return self.metrics

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
    engine = ExcelImportEngine(db, file_path, source_filename, upload_batch_id, replace_mode, preview=False)
    return engine.run()
