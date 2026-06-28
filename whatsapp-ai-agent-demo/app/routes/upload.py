# =====================================================================================================
# FILE: whatsapp-ai-agent-demo/app/routes/upload.py
# VERSION: v6.0 - CLEAN PRODUCTION UPLOAD ROUTER
# PURPOSE: Excel Upload Router compatible with import_delivery_excel()
# =====================================================================================================

# =====================================================================================================
# BLOCK 1: IMPORTS
# =====================================================================================================

import logging
import os
import re
import tempfile
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DeliveryReport
from app.services.excel_import_service import (
    ExcelImportServiceError,
    VerificationError,
    import_delivery_excel,
)


# =====================================================================================================
# BLOCK 2: LOGGING AND CONFIGURATION
# =====================================================================================================

logger = logging.getLogger(__name__)


class UploadConfig:
    """Upload service configuration constants."""

    MAX_FILE_SIZE = int(os.getenv("UPLOAD_MAX_FILE_SIZE_MB", "50")) * 1024 * 1024
    ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
    ALLOWED_MIME_TYPES = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    }
    REPLACE_MODE = os.getenv("UPLOAD_REPLACE_MODE", "true").lower() == "true"
    FORCE_REPLACE = os.getenv("UPLOAD_FORCE_REPLACE", "true").lower() == "true"
    IMPORT_TIMEOUT_SECONDS = int(os.getenv("UPLOAD_IMPORT_TIMEOUT_SECONDS", "900"))
    MAX_FILENAME_LENGTH = 200
    BATCH_SIZE = 1000


# =====================================================================================================
# BLOCK 3: RESPONSE MODELS
# =====================================================================================================

class UploadStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class UploadResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool = Field(...)
    message: str = Field(...)
    request_id: str = Field(...)
    batch_id: Optional[str] = Field(None)
    filename: Optional[str] = Field(None)
    rows_processed: int = Field(0)
    rows_inserted: int = Field(0)
    rows_updated: int = Field(0)
    rows_skipped: int = Field(0)
    rows_failed: int = Field(0)
    records_deleted: int = Field(0)
    processing_time: float = Field(0.0)
    database_time: float = Field(0.0)
    file_save_time: float = Field(0.0)
    excel_read_time: float = Field(0.0)
    column_mapping_time: float = Field(0.0)
    delete_time: float = Field(0.0)
    insert_time: float = Field(0.0)
    commit_time: float = Field(0.0)
    cleanup_time: float = Field(0.0)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    timestamp: Optional[str] = Field(None)


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: HealthStatus = Field(...)
    router: Dict[str, Any] = Field(...)
    database: Dict[str, Any] = Field(...)
    upload_service: Dict[str, Any] = Field(...)
    excel_import_service: Dict[str, Any] = Field(...)
    delivery_report_model: Dict[str, Any] = Field(...)
    supported_file_types: List[str] = Field(...)
    max_upload_size: int = Field(...)
    max_upload_size_mb: float = Field(...)
    application_version: str = Field(...)
    timestamp: str = Field(...)


class StatusResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool = Field(...)
    message: str = Field(...)
    request_id: str = Field(...)
    status: Dict[str, Any] = Field(...)
    timestamp: Optional[str] = Field(None)


# =====================================================================================================
# BLOCK 4: ROUTER INITIALIZATION
# =====================================================================================================

router = APIRouter(
    prefix="/upload",
    tags=["upload"],
    responses={
        400: {"description": "Bad Request - Invalid file or parameters"},
        413: {"description": "Payload Too Large - File exceeds size limit"},
        415: {"description": "Unsupported Media Type - Invalid file format"},
        500: {"description": "Internal Server Error - Unexpected failure"},
        503: {"description": "Service Unavailable - Database connection failed"},
    },
)


# =====================================================================================================
# BLOCK 5: HELPER FUNCTIONS
# =====================================================================================================

def generate_request_id() -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"REQ_{timestamp}_{uuid.uuid4().hex[:8]}"


def generate_batch_id() -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"BATCH_{timestamp}_{uuid.uuid4().hex[:8]}"


def get_file_extension(filename: Optional[str]) -> str:
    if not filename:
        return ""
    return os.path.splitext(filename)[1].lower()


def normalize_filename(filename: Optional[str]) -> str:
    if not filename:
        return f"upload_{uuid.uuid4().hex}.xlsx"

    filename = os.path.basename(filename)
    filename = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    ext = get_file_extension(filename)
    base = os.path.splitext(filename)[0]

    if ext not in UploadConfig.ALLOWED_EXTENSIONS:
        ext = ".xlsx"

    return f"{base[:UploadConfig.MAX_FILENAME_LENGTH]}{ext}"


def validate_upload_file(file: UploadFile, size_bytes: int) -> None:
    ext = get_file_extension(file.filename)

    if ext not in UploadConfig.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file extension: {ext or 'unknown'}",
        )

    if file.content_type and file.content_type not in UploadConfig.ALLOWED_MIME_TYPES:
        logger.warning("Unexpected MIME type for upload: %s", file.content_type)

    if size_bytes <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    if size_bytes > UploadConfig.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {UploadConfig.MAX_FILE_SIZE // (1024 * 1024)} MB.",
        )


def count_table_records(db: Session) -> int:
    return db.query(DeliveryReport).count()


def delete_all_records(db: Session) -> int:
    deleted = db.query(DeliveryReport).delete(synchronize_session=False)
    db.flush()
    return deleted


def validate_postgresql_schema(db: Session) -> List[str]:
    errors: List[str] = []
    inspector = inspect(db.get_bind())
    table_names = inspector.get_table_names()

    if "delivery_reports" not in table_names:
        errors.append("Table 'delivery_reports' does not exist")
        return errors

    columns = {col["name"]: col for col in inspector.get_columns("delivery_reports")}
    required_columns = [
        "dn_no",
        "material_no",
        "customer_name",
        "upload_batch_id",
        "dn_create_date",
        "good_issue_date",
        "pod_date",
    ]

    for col in required_columns:
        if col not in columns:
            errors.append(f"Required column '{col}' missing from delivery_reports table")

    return errors


# =====================================================================================================
# BLOCK 6: UPLOAD ENDPOINT
# =====================================================================================================

@router.post("/excel", response_model=UploadResponse)
async def upload_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResponse:
    request_id = generate_request_id()
    batch_id = generate_batch_id()
    temp_path: Optional[str] = None
    started_at = time.time()
    file_save_started = time.time()

    logger.info("[%s] Excel upload started for file=%s", request_id, file.filename)

    try:
        schema_errors = validate_postgresql_schema(db)
        if schema_errors:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="; ".join(schema_errors),
            )

        content = await file.read()
        validate_upload_file(file, len(content))

        suffix = get_file_extension(file.filename) or ".xlsx"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name

        file_save_time = (time.time() - file_save_started) * 1000

        delete_started = time.time()
        deleted_records = 0
        if UploadConfig.REPLACE_MODE or UploadConfig.FORCE_REPLACE:
            deleted_records = delete_all_records(db)
        delete_time = (time.time() - delete_started) * 1000

        import_started = time.time()
        normalized_filename = normalize_filename(file.filename)
        result = import_delivery_excel(
            db=db,
            file_path=temp_path,
            source_filename=normalized_filename,
            upload_batch_id=batch_id,
            batch_size=UploadConfig.BATCH_SIZE,
        )
        import_time_ms = (time.time() - import_started) * 1000

        rows_processed = int(result.get("rows_read", 0))
        rows_inserted = int(result.get("rows_upserted", 0))
        rows_failed = int(result.get("rows_failed", 0))
        rows_valid = int(result.get("rows_valid", 0))
        rows_skipped = max(rows_processed - rows_valid - rows_failed, 0)

        return UploadResponse(
            success=True,
            message="Excel file uploaded successfully.",
            request_id=request_id,
            batch_id=result.get("batch_id", batch_id),
            filename=normalized_filename,
            rows_processed=rows_processed,
            rows_inserted=rows_inserted,
            rows_updated=0,
            rows_skipped=rows_skipped,
            rows_failed=rows_failed,
            records_deleted=deleted_records,
            processing_time=round(time.time() - started_at, 2),
            database_time=round(import_time_ms / 1000, 2),
            file_save_time=round(file_save_time, 2),
            excel_read_time=round(import_time_ms, 2),
            column_mapping_time=0.0,
            delete_time=round(delete_time, 2),
            insert_time=round(import_time_ms, 2),
            commit_time=0.0,
            cleanup_time=0.0,
            warnings=[],
            errors=[f"Row {item['row']}: {item['error']}" for item in result.get("errors", [])],
            timestamp=datetime.utcnow().isoformat(),
        )

    except HTTPException:
        db.rollback()
        raise
    except VerificationError as exc:
        db.rollback()
        logger.exception("[%s] Verification failed", request_id)
        return UploadResponse(
            success=False,
            message="Upload failed",
            request_id=request_id,
            batch_id=None,
            filename=None,
            rows_processed=0,
            rows_inserted=0,
            rows_updated=0,
            rows_skipped=0,
            rows_failed=0,
            records_deleted=0,
            processing_time=round(time.time() - started_at, 2),
            database_time=0.0,
            file_save_time=0.0,
            excel_read_time=0.0,
            column_mapping_time=0.0,
            delete_time=0.0,
            insert_time=0.0,
            commit_time=0.0,
            cleanup_time=0.0,
            warnings=[],
            errors=[str(exc)],
            timestamp=datetime.utcnow().isoformat(),
        )
    except ExcelImportServiceError as exc:
        db.rollback()
        logger.exception("[%s] Import service failed", request_id)
        return UploadResponse(
            success=False,
            message="Upload failed",
            request_id=request_id,
            batch_id=None,
            filename=None,
            rows_processed=0,
            rows_inserted=0,
            rows_updated=0,
            rows_skipped=0,
            rows_failed=0,
            records_deleted=0,
            processing_time=round(time.time() - started_at, 2),
            database_time=0.0,
            file_save_time=0.0,
            excel_read_time=0.0,
            column_mapping_time=0.0,
            delete_time=0.0,
            insert_time=0.0,
            commit_time=0.0,
            cleanup_time=0.0,
            warnings=[],
            errors=[str(exc)],
            timestamp=datetime.utcnow().isoformat(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("[%s] Unexpected upload error", request_id)
        return UploadResponse(
            success=False,
            message="Upload failed",
            request_id=request_id,
            batch_id=None,
            filename=None,
            rows_processed=0,
            rows_inserted=0,
            rows_updated=0,
            rows_skipped=0,
            rows_failed=0,
            records_deleted=0,
            processing_time=round(time.time() - started_at, 2),
            database_time=0.0,
            file_save_time=0.0,
            excel_read_time=0.0,
            column_mapping_time=0.0,
            delete_time=0.0,
            insert_time=0.0,
            commit_time=0.0,
            cleanup_time=0.0,
            warnings=[],
            errors=[str(exc)],
            timestamp=datetime.utcnow().isoformat(),
        )
    finally:
        cleanup_started = time.time()
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning("[%s] Failed to delete temp file %s", request_id, temp_path)
        _cleanup_time_ms = (time.time() - cleanup_started) * 1000


# =====================================================================================================
# BLOCK 7: STATUS ENDPOINT
# =====================================================================================================

@router.get("/status", response_model=StatusResponse)
def get_upload_status(db: Session = Depends(get_db)) -> StatusResponse:
    request_id = generate_request_id()
    payload = {
        "router_registered": True,
        "replace_mode": UploadConfig.REPLACE_MODE,
        "force_replace": UploadConfig.FORCE_REPLACE,
        "max_file_size_mb": UploadConfig.MAX_FILE_SIZE / (1024 * 1024),
        "allowed_extensions": sorted(UploadConfig.ALLOWED_EXTENSIONS),
        "record_count": count_table_records(db),
    }

    return StatusResponse(
        success=True,
        message="Upload router is available.",
        request_id=request_id,
        status=payload,
        timestamp=datetime.utcnow().isoformat(),
    )


# =====================================================================================================
# BLOCK 8: HEALTH ENDPOINT
# =====================================================================================================

@router.get("/health", response_model=HealthResponse)
def upload_health_check(db: Session = Depends(get_db)) -> HealthResponse:
    now = datetime.utcnow().isoformat()
    overall_status = HealthStatus.HEALTHY

    try:
        db.execute(text("SELECT 1"))
        schema_errors = validate_postgresql_schema(db)
        database_status = {
            "connected": True,
            "message": "Database connection is healthy.",
        }
        delivery_report_status = {
            "table_exists": len(schema_errors) == 0 or "Table 'delivery_reports' does not exist" not in schema_errors,
            "schema_errors": schema_errors,
        }

        if schema_errors:
            overall_status = HealthStatus.DEGRADED

    except Exception as exc:
        overall_status = HealthStatus.UNHEALTHY
        database_status = {
            "connected": False,
            "message": str(exc),
        }
        delivery_report_status = {
            "table_exists": False,
            "schema_errors": [str(exc)],
        }

    return HealthResponse(
        status=overall_status,
        router={"registered": True, "prefix": "/upload"},
        database=database_status,
        upload_service={
            "replace_mode": UploadConfig.REPLACE_MODE,
            "force_replace": UploadConfig.FORCE_REPLACE,
            "max_file_size_mb": UploadConfig.MAX_FILE_SIZE / (1024 * 1024),
        },
        excel_import_service={
            "available": True,
            "entrypoint": "import_delivery_excel",
        },
        delivery_report_model=delivery_report_status,
        supported_file_types=sorted(UploadConfig.ALLOWED_EXTENSIONS),
        max_upload_size=UploadConfig.MAX_FILE_SIZE,
        max_upload_size_mb=UploadConfig.MAX_FILE_SIZE / (1024 * 1024),
        application_version="6.0",
        timestamp=now,
    )


# =====================================================================================================
# BLOCK 9: EXPORTED SYMBOLS
# =====================================================================================================

__all__ = [
    "router",
    "UploadConfig",
    "UploadResponse",
    "StatusResponse",
    "HealthResponse",
]
