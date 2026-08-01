# ==========================================================
# FILE: app/services/excel_import_service.py (v6.0 - PANDAS)
# PURPOSE: Reads Excel using pandas, maps all columns,
#          replaces old data (truncates) if replace_mode=True,
#          and inserts new rows in batches of 1000.
# ==========================================================

import logging
import pandas as pd
import numpy as np
from datetime import datetime, date
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ==========================================================
# CUSTOM EXCEPTIONS
# ==========================================================

class ExcelImportServiceError(Exception):
    pass

class VerificationError(Exception):
    pass

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

# ==========================================================
# MAIN IMPORT FUNCTION (WITH TRUNCATE)
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
    Reads an Excel file using pandas, maps all columns to the
    DeliveryReport model, and inserts the data in batches.
    
    If replace_mode is True, the table is truncated before insertion.
    """
    logger.info(f"Starting import for batch: {upload_batch_id}, file: {source_filename}")

    metrics = {
        "rows_read": 0,
        "rows_upserted": 0,
        "rows_failed": 0,
        "rows_valid": 0,
        "batch_id": upload_batch_id,
        "errors": []
    }

    # ==========================================================
    # STEP 0: REPLACE MODE – TRUNCATE OLD DATA
    # ==========================================================
    if replace_mode:
        try:
            db.execute(text("TRUNCATE TABLE delivery_reports RESTART IDENTITY CASCADE"))
            db.flush()
            logger.info("✅ Existing data truncated (replace_mode=True).")
        except Exception as e:
            logger.exception("Truncation failed")
            raise ExcelImportServiceError(f"Truncation failed: {str(e)}")

    # ==========================================================
    # STEP 1: READ EXCEL WITH PANDAS
    # ==========================================================
    try:
        df = pd.read_excel(file_path, engine='openpyxl', dtype=str, keep_default_na=False)
        df = df.replace(r'^\s*$', np.nan, regex=True)
    except Exception as e:
        logger.exception(f"Failed to read Excel file: {file_path}")
        raise ExcelImportServiceError(f"Failed to read Excel file: {str(e)}")

    # Normalise column names: strip, uppercase, replace spaces with underscore
    df.columns = df.columns.str.strip().str.upper().str.replace(' ', '_')

    # ==========================================================
    # STEP 2: VERIFY REQUIRED COLUMN
    # ==========================================================
    required_col = "DN_NO"
    if required_col not in df.columns:
        raise VerificationError(f"Required column '{required_col}' not found in Excel header.")

    # Map dataframe columns to model fields
    column_mapping = {
        "ORDER_TYPE": "order_type",
        "DN_NO": "dn_no",
        "DN_AMOUNT": "dn_amount",
        "DN_QTY": "dn_qty",
        "DN_WORK": "dn_work",
        "DIVISION": "division",
        "MATERIAL_NO": "material_no",
        "CUSTOMER_MODEL": "customer_model",
        "SALES_OFFICE": "sales_office",
        "SOLD-TO-PARTY_NAME": "customer_name",
        "CUSTOMER_CODE": "customer_code",
        "DEALER_CODE": "dealer_code",
        "SHIP-TO_CITY": "ship_to_city",
        "STORAGE": "storage_location",
        "WAREHOUSE": "warehouse",
        "WAREHOUSE_CODE": "warehouse_code",
        "DELIVERY_LOCATION": "delivery_location",
        "DN_CREATE_DATE": "dn_create_date",
        "GOOD_ISSUE_DATE": "good_issue_date",
        "POD_DATE": "pod_date",
        "SALES_MANAGER": "sales_manager",
        "REMARKS": "remarks",
    }

    # Check which optional columns are present
    present_cols = set(df.columns)
    missing_cols = set(column_mapping.keys()) - present_cols
    if missing_cols:
        logger.warning(f"Optional columns missing: {', '.join(missing_cols)}")

    # ==========================================================
    # STEP 3: TRANSFORM DATAFRAME TO LIST OF DICTIONARIES
    # ==========================================================
    records = []
    errors = []

    for idx, row in df.iterrows():
        metrics["rows_read"] += 1
        try:
            dn_no = _clean_value(row.get("DN_NO"))
            if not dn_no:
                raise ValueError("DN_NO is empty or missing")

            # Build the record dictionary
            record = {}
            for excel_col, model_field in column_mapping.items():
                if excel_col not in df.columns:
                    record[model_field] = None
                    continue
                raw = _clean_value(row[excel_col])
                # Apply appropriate conversion based on model field
                if model_field in ("dn_amount",):
                    record[model_field] = _safe_float(raw)
                elif model_field in ("dn_qty",):
                    record[model_field] = _safe_int(raw)
                elif model_field in ("dn_create_date", "good_issue_date", "pod_date"):
                    record[model_field] = _parse_date_from_excel(raw)
                else:
                    record[model_field] = str(raw) if raw is not None else None

            # Derive statuses from dates
            pod_date = record.get("pod_date")
            good_issue_date = record.get("good_issue_date")
            record["delivery_status"] = "Delivered" if pod_date else "Pending"
            record["pgi_status"] = "Completed" if good_issue_date else "Pending"
            record["pod_status"] = "Completed" if pod_date else "Pending"
            record["pending_flag"] = False if pod_date else True
            record["source_file"] = source_filename
            record["upload_batch_id"] = upload_batch_id

            records.append(record)
            metrics["rows_valid"] += 1

        except Exception as e:
            logger.warning(f"Row {idx+2} failed validation: {e}")
            errors.append({"row": idx+2, "error": str(e)})
            metrics["rows_failed"] += 1

    # ==========================================================
    # STEP 4: BULK INSERT IN BATCHES OF 1000
    # ==========================================================
    if records:
        try:
            total_inserted = 0
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

    metrics["errors"] = [f"Row {err['row']}: {err['error']}" for err in errors]
    logger.info(f"Import completed for batch {upload_batch_id}. "
                f"Read: {metrics['rows_read']}, "
                f"Inserted: {metrics['rows_upserted']}, "
                f"Failed: {metrics['rows_failed']}")
    return metrics
