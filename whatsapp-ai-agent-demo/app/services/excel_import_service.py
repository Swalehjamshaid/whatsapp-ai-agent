# ==========================================================
# FILE: app/services/excel_import_service.py (v6.0 - STABLE)
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
    logger.info(f"Starting import for batch: {upload_batch_id}, file: {source_filename}")

    metrics = {
        "rows_read": 0,
        "rows_upserted": 0,
        "rows_failed": 0,
        "rows_valid": 0,
        "batch_id": upload_batch_id,
        "errors": []
    }

    if replace_mode:
        try:
            db.execute(text("TRUNCATE TABLE delivery_reports RESTART IDENTITY CASCADE"))
            db.flush()
            logger.info("✅ Existing data truncated (replace_mode=True).")
        except Exception as e:
            logger.exception("Truncation failed")
            raise ExcelImportServiceError(f"Truncation failed: {str(e)}")

    try:
        df = pd.read_excel(file_path, engine='openpyxl', dtype=str, keep_default_na=False)
        df = df.replace(r'^\s*$', np.nan, regex=True)
    except Exception as e:
        logger.exception(f"Failed to read Excel file: {file_path}")
        raise ExcelImportServiceError(f"Failed to read Excel file: {str(e)}")

    df.columns = df.columns.str.strip().str.upper().str.replace(' ', '_')

    required_col = "DN_NO"
    if required_col not in df.columns:
        raise VerificationError(f"Required column '{required_col}' not found in Excel header.")

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

    present_cols = set(df.columns)
    missing_cols = set(column_mapping.keys()) - present_cols
    if missing_cols:
        logger.warning(f"Optional columns missing: {', '.join(missing_cols)}")

    records = []
    errors = []

    for idx, row in df.iterrows():
        metrics["rows_read"] += 1
        try:
            dn_no = _clean_value(row.get("DN_NO"))
            if not dn_no:
                raise ValueError("DN_NO is empty or missing")

            record = {}
            for excel_col, model_field in column_mapping.items():
                if excel_col not in df.columns:
                    record[model_field] = None
                    continue
                raw = _clean_value(row[excel_col])
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

    if records:
        try:
            records_dict = [
                {
                    "order_type": r.order_type,
                    "dn_no": r.dn_no,
                    "dn_amount": r.dn_amount,
                    "dn_qty": r.dn_qty,
                    "dn_work": r.dn_work,
                    "division": r.division,
                    "material_no": r.material_no,
                    "customer_model": r.customer_model,
                    "sales_office": r.sales_office,
                    "customer_name": r.customer_name,
                    "customer_code": r.customer_code,
                    "dealer_code": r.dealer_code,
                    "ship_to_city": r.ship_to_city,
                    "storage_location": r.storage_location,
                    "warehouse": r.warehouse,
                    "warehouse_code": r.warehouse_code,
                    "delivery_location": r.delivery_location,
                    "dn_create_date": r.dn_create_date,
                    "good_issue_date": r.good_issue_date,
                    "pod_date": r.pod_date,
                    "sales_manager": r.sales_manager,
                    "remarks": r.remarks,
                    "delivery_status": r.delivery_status,
                    "pgi_status": r.pgi_status,
                    "pod_status": r.pod_status,
                    "pending_flag": r.pending_flag,
                    "source_file": r.source_file,
                    "upload_batch_id": r.upload_batch_id
                } for r in records
            ]

            total_inserted = 0
            for i in range(0, len(records_dict), batch_size):
                batch = records_dict[i:i + batch_size]
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
