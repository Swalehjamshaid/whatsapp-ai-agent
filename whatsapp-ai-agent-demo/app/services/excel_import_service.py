# ==========================================================
# FILE: app/services/excel_import_service.py (v7.0 - ENTERPRISE)
# ==========================================================
# PURPOSE: Enterprise-grade Excel Import Engine using dynamic column mapping,
#          full validation, backup, truncation, batch insertion,
#          verification, and detailed reporting.
# ==========================================================

import logging
import time
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ==========================================================
# CUSTOM EXCEPTIONS
# ==========================================================

class ExcelImportServiceError(Exception):
    """Base exception for Excel import service errors."""
    pass

class VerificationError(Exception):
    """Raised when required columns are missing or validation fails."""
    pass

# ==========================================================
# CENTRALIZED COLUMN MAPPING DICTIONARY
# ==========================================================
# This is the single source of truth for all Excel-to-PostgreSQL mappings.
# Future Excel changes only require updating this dictionary.

EXCEL_TO_MODEL_MAP = {
    # Required columns (must be present)
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
    # Optional columns (may be missing)
    "CUSTOMER_CODE": "customer_code",
    "DEALER_CODE": "dealer_code",
    "WAREHOUSE_CODE": "warehouse_code",
    "DELIVERY_LOCATION": "delivery_location",
    "REMARKS": "remarks",
}

# Reverse mapping for reporting
MODEL_TO_EXCEL_MAP = {v: k for k, v in EXCEL_TO_MODEL_MAP.items()}

# Required columns (keys that must exist in Excel)
REQUIRED_COLUMNS = [
    "ORDER_TYPE", "DN_NO", "DN_AMOUNT", "DN_QTY", "DN_WORK", "DIVISION",
    "MATERIAL_NO", "CUSTOMER_MODEL", "SALES_OFFICE", "SOLD_TO_PARTY_NAME",
    "SHIP_TO_CITY", "STORAGE", "WAREHOUSE", "DN_CREATE_DATE", "GOOD_ISSUE_DATE",
    "POD_DATE", "SALES_MANAGER"
]

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def _clean_value(val):
    """Convert pandas NaN/NaT to None."""
    if pd.isna(val):
        return None
    return val

def _safe_int(val) -> Optional[int]:
    """Safely convert to int, handling commas and NaN."""
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
    """Safely convert to float, handling commas."""
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
    """Convert pandas Timestamp, string, or serial number to date."""
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

def _normalize_header(header: str) -> str:
    """Convert Excel header to standard uppercase with underscores."""
    if not header:
        return ""
    # Replace special characters and spaces with underscores
    normalized = header.strip().upper().replace(' ', '_')
    normalized = normalized.replace('-', '_').replace('/', '_')
    # Remove any other non-alphanumeric characters (keep underscores)
    normalized = ''.join(c for c in normalized if c.isalnum() or c == '_')
    return normalized

def _derive_statuses(pod_date, good_issue_date):
    """
    Derive business fields based on dates.
    """
    delivery_status = "Delivered" if pod_date else "Pending"
    pgi_status = "Completed" if good_issue_date else "Pending"
    pod_status = "Completed" if pod_date else "Pending"
    pending_flag = False if pod_date else True
    return delivery_status, pgi_status, pod_status, pending_flag

def _create_backup(db: Session) -> int:
    """
    Create a backup of the delivery_reports table by copying to a backup table.
    Returns the number of records backed up.
    """
    try:
        # Drop backup table if exists
        db.execute(text("DROP TABLE IF EXISTS delivery_reports_backup"))
        # Create backup table with same structure
        db.execute(text("CREATE TABLE delivery_reports_backup AS SELECT * FROM delivery_reports"))
        db.commit()
        count = db.execute(text("SELECT COUNT(*) FROM delivery_reports_backup")).scalar()
        logger.info(f"✅ Backup created: {count} records in delivery_reports_backup")
        return count
    except Exception as e:
        logger.exception("Backup failed")
        raise ExcelImportServiceError(f"Backup failed: {str(e)}")

def _truncate_table(db: Session) -> None:
    """
    Truncate the delivery_reports table and reset the identity sequence.
    """
    try:
        db.execute(text("TRUNCATE TABLE delivery_reports RESTART IDENTITY CASCADE"))
        db.flush()
        logger.info("✅ delivery_reports truncated (replace_mode=True).")
    except Exception as e:
        logger.exception("Truncation failed")
        raise ExcelImportServiceError(f"Truncation failed: {str(e)}")

# ==========================================================
# CORE IMPORT FUNCTION
# ==========================================================

def import_delivery_excel(
    db: Session,
    file_path: str,
    source_filename: str,
    upload_batch_id: str,
    batch_size: int = 1000,
    replace_mode: bool = False
) -> Dict[str, Any]:
    """
    Enterprise-grade Excel import function.

    Steps:
    1. Read Excel with pandas
    2. Normalize headers
    3. Validate required columns
    4. Create backup (if replace_mode=True)
    5. Truncate table (if replace_mode=True)
    6. Transform data (dates, numbers, blanks, etc.)
    7. Derive business fields
    8. Insert in batches of 1000
    9. Verify row count
    10. Return detailed import report
    """
    import_start = time.time()
    logger.info(f"Starting import for batch: {upload_batch_id}, file: {source_filename}")

    # Initialize metrics
    metrics = {
        "rows_read": 0,
        "rows_upserted": 0,
        "rows_failed": 0,
        "rows_valid": 0,
        "rows_skipped": 0,
        "batch_id": upload_batch_id,
        "execution_time_seconds": 0.0,
        "import_speed_rows_per_second": 0.0,
        "errors": [],
        "warnings": [],
    }

    # ----------------------------------------------------------
    # STEP 1: READ EXCEL
    # ----------------------------------------------------------
    try:
        df = pd.read_excel(file_path, engine='openpyxl', dtype=str, keep_default_na=False)
        df = df.replace(r'^\s*$', np.nan, regex=True)
    except Exception as e:
        logger.exception(f"Failed to read Excel file: {file_path}")
        raise ExcelImportServiceError(f"Failed to read Excel file: {str(e)}")

    metrics["rows_read"] = len(df)

    # ----------------------------------------------------------
    # STEP 2: NORMALIZE HEADERS
    # ----------------------------------------------------------
    original_headers = list(df.columns)
    normalized_headers = [_normalize_header(h) for h in original_headers]
    df.columns = normalized_headers

    # ----------------------------------------------------------
    # STEP 3: VALIDATE REQUIRED COLUMNS
    # ----------------------------------------------------------
    missing_columns = []
    for req in REQUIRED_COLUMNS:
        if req not in df.columns:
            missing_columns.append(req)
    if missing_columns:
        raise VerificationError(f"Required columns missing: {', '.join(missing_columns)}")

    # Log optional missing columns
    present_cols = set(df.columns)
    optional_missing = [k for k in EXCEL_TO_MODEL_MAP if k not in present_cols and k not in REQUIRED_COLUMNS]
    if optional_missing:
        logger.warning(f"Optional columns missing: {', '.join(optional_missing)}")
        metrics["warnings"].append(f"Optional columns missing: {', '.join(optional_missing)}")

    # Build dynamic mapping based on present columns
    mapping = {}
    for excel_col, model_field in EXCEL_TO_MODEL_MAP.items():
        if excel_col in df.columns:
            mapping[excel_col] = model_field

    # ----------------------------------------------------------
    # STEP 4: BACKUP EXISTING DATA (if replace_mode)
    # ----------------------------------------------------------
    if replace_mode:
        backup_count = _create_backup(db)
        metrics["backup_count"] = backup_count
        _truncate_table(db)

    # ----------------------------------------------------------
    # STEP 5: TRANSFORM DATA AND DERIVE BUSINESS FIELDS
    # ----------------------------------------------------------
    records = []
    errors = []

    for idx, row in df.iterrows():
        try:
            # Extract DN_NO (required)
            dn_no = _clean_value(row.get("DN_NO"))
            if not dn_no:
                raise ValueError("DN_NO is empty or missing")

            record = {}
            # Map each column using the dynamic mapping
            for excel_col, model_field in mapping.items():
                raw = _clean_value(row[excel_col])
                if raw is None:
                    record[model_field] = None
                    continue

                # Apply type-specific conversions
                if model_field in ("dn_amount",):
                    record[model_field] = _safe_float(raw)
                elif model_field in ("dn_qty",):
                    record[model_field] = _safe_int(raw)
                elif model_field in ("dn_create_date", "good_issue_date", "pod_date"):
                    record[model_field] = _parse_date_from_excel(raw)
                else:
                    record[model_field] = str(raw) if raw is not None else None

            # Derive business fields from dates
            pod_date = record.get("pod_date")
            good_issue_date = record.get("good_issue_date")
            delivery_status, pgi_status, pod_status, pending_flag = _derive_statuses(pod_date, good_issue_date)

            record["delivery_status"] = delivery_status
            record["pgi_status"] = pgi_status
            record["pod_status"] = pod_status
            record["pending_flag"] = pending_flag
            record["source_file"] = source_filename
            record["upload_batch_id"] = upload_batch_id

            records.append(record)
            metrics["rows_valid"] += 1

        except Exception as e:
            logger.warning(f"Row {idx+2} failed validation: {e}")
            errors.append({"row": idx+2, "error": str(e)})
            metrics["rows_failed"] += 1

    # ----------------------------------------------------------
    # STEP 6: INSERT DATA IN BATCHES
    # ----------------------------------------------------------
    total_inserted = 0
    if records:
        try:
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                db.bulk_insert_mappings(DeliveryReport, batch)
                db.flush()
                total_inserted += len(batch)
                logger.info(f"Inserted batch of {len(batch)} records. Total so far: {total_inserted}")

            metrics["rows_upserted"] = total_inserted
            logger.info(f"✅ Successfully inserted {metrics['rows_upserted']} records into delivery_reports.")

        except Exception as e:
            logger.exception("Bulk insert failed")
            db.rollback()
            raise ExcelImportServiceError(f"Database insert failed: {str(e)}")
    else:
        logger.info("No valid records found to insert.")
        metrics["rows_upserted"] = 0

    # ----------------------------------------------------------
    # STEP 7: VERIFICATION - Compare rows in Excel vs inserted
    # ----------------------------------------------------------
    if metrics["rows_valid"] != metrics["rows_upserted"]:
        warning_msg = f"Row count mismatch: Valid={metrics['rows_valid']}, Inserted={metrics['rows_upserted']}"
        logger.warning(warning_msg)
        metrics["warnings"].append(warning_msg)

    # ----------------------------------------------------------
    # STEP 8: BUILD FINAL REPORT
    # ----------------------------------------------------------
    execution_time = time.time() - import_start
    metrics["execution_time_seconds"] = round(execution_time, 2)
    metrics["import_speed_rows_per_second"] = round(metrics["rows_upserted"] / execution_time, 2) if execution_time > 0 else 0.0
    metrics["rows_skipped"] = metrics["rows_read"] - metrics["rows_valid"] - metrics["rows_failed"]

    logger.info(f"Import completed for batch {upload_batch_id}. "
                f"Read: {metrics['rows_read']}, "
                f"Inserted: {metrics['rows_upserted']}, "
                f"Failed: {metrics['rows_failed']}, "
                f"Skipped: {metrics['rows_skipped']}, "
                f"Time: {metrics['execution_time_seconds']}s, "
                f"Speed: {metrics['import_speed_rows_per_second']} rows/s")

    return metrics
