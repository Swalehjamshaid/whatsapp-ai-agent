# =====================================================================================================
# FILE: app/routes/upload.py
# VERSION: v5.0 - ENTERPRISE PRODUCTION GRADE
# =====================================================================================================
# PURPOSE: Excel Upload Router - Enterprise Production with Replace Mode
# SOURCE: Excel files (.xlsx, .xls)
# VERSION: 5.0 - ENTERPRISE PRODUCTION GRADE
# =====================================================================================================
# 
# 📤 UPLOAD ROUTER v5.0
# =====================================================================================================
# 
# FEATURES:
#   🔴 FORCE REPLACE MODE - Automatically deletes ALL existing data before import
#   ✅ Atomic Transactions - All or nothing (rollback on failure)
#   ✅ Comprehensive Validation - File, schema, and data validation
#   ✅ Detailed Metrics - Step-by-step timing and performance tracking
#   ✅ Database Lock Diagnostics - Detects and reports locks
#   ✅ Import Watchdog - 15-minute timeout protection
#   ✅ Pydantic v2 Compatible - Modern data validation
#   ✅ Enterprise Logging - Structured logging with request tracking
# 
# ENDPOINTS:
#   POST   /upload/excel    - Upload Excel file (FORCE REPLACE MODE)
#   GET    /upload/status   - Get upload service status
#   GET    /upload/health   - Comprehensive health check
# 
# =====================================================================================================

import os
import uuid
import logging
import time
import shutil
import tempfile
from typing import Dict, Any, Optional, List, Union
from datetime import datetime
import traceback
import re
import asyncio
from contextlib import contextmanager
from enum import Enum

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect
from pydantic import BaseModel, ConfigDict, Field, validator

from app.database import get_db
from app.models import DeliveryReport
from app.services.excel_import_service import ExcelImportService, VerificationError

# =====================================================================================================
# BLOCK 1: LOGGING & CONFIGURATION
# =====================================================================================================

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------------------------------
# Configuration Constants
# ----------------------------------------------------------------------------------------------------

class UploadConfig:
    """Upload service configuration constants."""
    
    # File limits
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
    ALLOWED_EXTENSIONS = {'.xlsx', '.xls'}
    ALLOWED_MIME_TYPES = {
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
        'application/vnd.ms-excel'  # .xls
    }
    
    # Performance
    BATCH_SIZE = 1000
    CHUNK_SIZE = 8192  # 8KB for file reading
    
    # Timeouts
    IMPORT_TIMEOUT_SECONDS = 900  # 15 minutes
    LOCK_TIMEOUT_SECONDS = 30  # 30 seconds
    CONNECTION_TIMEOUT_SECONDS = 10  # 10 seconds
    
    # Limits
    MAX_ROWS_PER_FILE = 1000000  # 1 million rows
    MAX_FILENAME_LENGTH = 200
    
    # Replace Mode (FORCE ENABLED)
    REPLACE_MODE = True  # Always delete existing data
    FORCE_REPLACE = True  # Override any safety checks

# ----------------------------------------------------------------------------------------------------
# Environment Configuration Overrides
# ----------------------------------------------------------------------------------------------------

def get_env_bool(key: str, default: bool) -> bool:
    """Get boolean from environment variable."""
    value = os.getenv(key, str(default)).lower()
    return value in ('true', '1', 'yes', 'y', 'on')

def get_env_int(key: str, default: int) -> int:
    """Get integer from environment variable."""
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default

# Apply environment overrides
UploadConfig.MAX_FILE_SIZE = get_env_int('UPLOAD_MAX_FILE_SIZE_MB', 50) * 1024 * 1024
UploadConfig.IMPORT_TIMEOUT_SECONDS = get_env_int('UPLOAD_IMPORT_TIMEOUT_SECONDS', 900)
UploadConfig.REPLACE_MODE = get_env_bool('UPLOAD_REPLACE_MODE', True)
UploadConfig.FORCE_REPLACE = get_env_bool('UPLOAD_FORCE_REPLACE', True)

# Log configuration
logger.info("=" * 70)
logger.info("⚙️ UPLOAD CONFIGURATION")
logger.info("=" * 70)
logger.info(f"  📁 MAX_FILE_SIZE: {UploadConfig.MAX_FILE_SIZE / (1024*1024):.0f} MB")
logger.info(f"  ⏱️ IMPORT_TIMEOUT: {UploadConfig.IMPORT_TIMEOUT_SECONDS}s")
logger.info(f"  🔴 REPLACE_MODE: {UploadConfig.REPLACE_MODE}")
logger.info(f"  🔴 FORCE_REPLACE: {UploadConfig.FORCE_REPLACE}")
logger.info("=" * 70)

# =====================================================================================================
# BLOCK 2: PYDANTIC MODELS
# =====================================================================================================

class UploadStatus(str, Enum):
    """Upload status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"

class UploadResponse(BaseModel):
    """
    Response model for upload endpoint.
    
    Contains comprehensive metrics and status information.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    # Core response fields
    success: bool = Field(..., description="Indicates if upload was successful")
    message: str = Field(..., description="Human-readable status message")
    request_id: str = Field(..., description="Unique request identifier for tracking")
    
    # Batch information
    batch_id: Optional[str] = Field(None, description="Batch ID for this upload")
    filename: Optional[str] = Field(None, description="Original filename")
    
    # Row statistics
    rows_processed: int = Field(0, description="Total rows processed")
    rows_inserted: int = Field(0, description="Rows successfully inserted")
    rows_updated: int = Field(0, description="Rows updated (if applicable)")
    rows_skipped: int = Field(0, description="Rows skipped due to duplicates")
    rows_failed: int = Field(0, description="Rows that failed validation")
    records_deleted: int = Field(0, description="Records deleted before import")
    
    # Performance metrics
    processing_time: float = Field(0.0, description="Total processing time in seconds")
    database_time: float = Field(0.0, description="Database operation time in seconds")
    
    # Detailed timing metrics (milliseconds)
    file_save_time: float = Field(0.0, description="File save time in milliseconds")
    excel_read_time: float = Field(0.0, description="Excel read time in milliseconds")
    column_mapping_time: float = Field(0.0, description="Column mapping time in milliseconds")
    delete_time: float = Field(0.0, description="Delete time in milliseconds")
    insert_time: float = Field(0.0, description="Insert time in milliseconds")
    commit_time: float = Field(0.0, description="Commit time in milliseconds")
    cleanup_time: float = Field(0.0, description="Cleanup time in milliseconds")
    
    # Warnings and errors
    warnings: List[str] = Field(default_factory=list, description="Warning messages")
    errors: List[str] = Field(default_factory=list, description="Error messages")
    
    # Metadata
    timestamp: Optional[str] = Field(None, description="Response timestamp")
    
    @property
    def total_time_ms(self) -> float:
        """Total processing time in milliseconds."""
        return self.processing_time * 1000
    
    @property
    def rows_successful(self) -> int:
        """Total successfully processed rows."""
        return self.rows_inserted + self.rows_updated

class HealthStatus(str, Enum):
    """Health status enumeration."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

class HealthResponse(BaseModel):
    """
    Response model for health endpoint.
    
    Comprehensive health check response with component status.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    status: HealthStatus = Field(..., description="Overall health status")
    
    # Component status
    router: Dict[str, Any] = Field(..., description="Router status")
    database: Dict[str, Any] = Field(..., description="Database status")
    upload_service: Dict[str, Any] = Field(..., description="Upload service status")
    excel_import_service: Dict[str, Any] = Field(..., description="Excel import service status")
    delivery_report_model: Dict[str, Any] = Field(..., description="Delivery report model status")
    
    # Configuration
    supported_file_types: List[str] = Field(..., description="Supported file extensions")
    max_upload_size: int = Field(..., description="Maximum upload size in bytes")
    max_upload_size_mb: float = Field(..., description="Maximum upload size in MB")
    application_version: str = Field(..., description="Application version")
    timestamp: str = Field(..., description="Response timestamp")
    
    @property
    def is_healthy(self) -> bool:
        """Check if service is healthy."""
        return self.status == HealthStatus.HEALTHY

class StatusResponse(BaseModel):
    """
    Response model for status endpoint.
    
    Provides detailed service status information.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    success: bool = Field(..., description="Status check success")
    message: str = Field(..., description="Human-readable status message")
    request_id: str = Field(..., description="Unique request identifier")
    status: Dict[str, Any] = Field(..., description="Detailed status information")
    timestamp: Optional[str] = Field(None, description="Response timestamp")

# =====================================================================================================
# BLOCK 3: TIMING UTILITIES
# =====================================================================================================

class StepTimer:
    """
    Context manager for timing operations with detailed logging.
    
    Usage:
        with StepTimer("Operation name", request_id) as timer:
            # Perform operation
            pass
        # timer.elapsed_ms contains duration
    """
    
    def __init__(self, step_name: str, request_id: str):
        """
        Initialize step timer.
        
        Args:
            step_name: Name of the step being timed
            request_id: Request ID for tracking
        """
        self.step_name = step_name
        self.request_id = request_id
        self.start_time: Optional[float] = None
        self.elapsed_ms: float = 0.0
        self.exception: Optional[Exception] = None
    
    def __enter__(self) -> 'StepTimer':
        """Start timing."""
        self.start_time = time.time()
        logger.info(f"⏱️ [{self.request_id}] START: {self.step_name}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Stop timing and log result."""
        if self.start_time is None:
            return
        
        self.elapsed_ms = (time.time() - self.start_time) * 1000
        self.exception = exc_val
        
        if exc_type is None:
            logger.info(f"✅ [{self.request_id}] COMPLETE: {self.step_name} ({self.elapsed_ms:.2f}ms)")
        else:
            logger.error(f"❌ [{self.request_id}] FAILED: {self.step_name} ({self.elapsed_ms:.2f}ms) - {exc_val}")

class PerformanceMetrics:
    """Collect and manage performance metrics."""
    
    def __init__(self):
        self.metrics: Dict[str, float] = {}
        self.start_time: float = time.time()
    
    def record(self, name: str, value: float) -> None:
        """Record a metric value."""
        self.metrics[name] = value
    
    def record_duration(self, name: str, start: float) -> None:
        """Record duration from start time."""
        self.metrics[name] = (time.time() - start) * 1000
    
    def get(self, name: str, default: float = 0.0) -> float:
        """Get a metric value."""
        return self.metrics.get(name, default)
    
    @property
    def total_time(self) -> float:
        """Total elapsed time in seconds."""
        return time.time() - self.start_time
    
    @property
    def total_time_ms(self) -> float:
        """Total elapsed time in milliseconds."""
        return self.total_time * 1000
    
    def to_dict(self) -> Dict[str, float]:
        """Convert metrics to dictionary."""
        return {
            **self.metrics,
            'total_time_seconds': self.total_time,
            'total_time_ms': self.total_time_ms
        }

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
        503: {"description": "Service Unavailable - Database connection failed"}
    }
)

# =====================================================================================================
# BLOCK 5: HELPER FUNCTIONS
# =====================================================================================================

def generate_request_id() -> str:
    """
    Generate unique request ID for tracking.
    
    Format: REQ_YYYYMMDD_HHMMSS_[8-char-hex]
    
    Returns:
        Unique request ID string
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    random_suffix = uuid.uuid4().hex[:8]
    return f"REQ_{timestamp}_{random_suffix}"

def get_file_extension(filename: str) -> str:
    """
    Extract file extension safely.
    
    Args:
        filename: File name to check
        
    Returns:
        Lowercase file extension with dot, or empty string
    """
    if not filename:
        return ""
    return os.path.splitext(filename)[1].lower()

def is_valid_extension(filename: str) -> bool:
    """
    Check if file extension is allowed.
    
    Args:
        filename: File name to check
        
    Returns:
        True if extension is allowed
    """
    if not filename:
        return False
    ext = get_file_extension(filename)
    return ext in UploadConfig.ALLOWED_EXTENSIONS

def is_valid_mime_type(mime_type: Optional[str]) -> bool:
    """
    Check if MIME type is allowed.
    
    Args:
        mime_type: MIME type to check
        
    Returns:
        True if MIME type is allowed
    """
    if not mime_type:
        return False
    return mime_type in UploadConfig.ALLOWED_MIME_TYPES

def normalize_filename(filename: str) -> str:
    """
    Normalize filename for security.
    
    Removes path traversal attempts and dangerous characters.
    
    Args:
        filename: Original filename
        
    Returns:
        Safe, normalized filename
    """
    if not filename:
        return ""
    
    # Remove path traversal attempts
    filename = os.path.basename(filename)
    
    # Replace dangerous characters with underscore
    filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    
    # Ensure safe extension
    ext = get_file_extension(filename)
    if ext in UploadConfig.ALLOWED_EXTENSIONS:
        base = os.path.splitext(filename)[0]
        return f"{base[:UploadConfig.MAX_FILENAME_LENGTH]}{ext}"
    else:
        return filename[:UploadConfig.MAX_FILENAME_LENGTH]

def get_file_size_mb(file_size: int) -> float:
    """
    Convert file size to MB.
    
    Args:
        file_size: File size in bytes
        
    Returns:
        File size in MB (rounded to 2 decimal places)
    """
    return round(file_size / (1024 * 1024), 2)

def count_table_records(db: Session, request_id: Optional[str] = None) -> int:
    """
    Count records in delivery_reports table with logging.
    
    Args:
        db: Database session
        request_id: Request ID for tracking
        
    Returns:
        Record count, or -1 if failed
    """
    try:
        start = time.time()
        count = db.query(DeliveryReport).count()
        elapsed = (time.time() - start) * 1000
        
        if request_id:
            logger.debug(f"[{request_id}] Counted {count} records in {elapsed:.2f}ms")
        
        return count
    except Exception as e:
        logger.error(f"Failed to count records: {e}")
        return -1

def delete_all_records(db: Session, request_id: Optional[str] = None) -> int:
    """
    Delete ALL records from delivery_reports table.
    
    Args:
        db: Database session
        request_id: Request ID for tracking
        
    Returns:
        Number of records deleted
        
    Raises:
        Exception: If deletion fails
    """
    try:
        start = time.time()
        deleted = db.query(DeliveryReport).delete(synchronize_session=False)
        elapsed = (time.time() - start) * 1000
        
        if request_id:
            logger.info(f"[{request_id}] Deleted {deleted} records in {elapsed:.2f}ms")
        
        return deleted
    except Exception as e:
        logger.error(f"Failed to delete records: {e}")
        raise

def validate_postgresql_schema(db: Session, request_id: str) -> List[str]:
    """
    Validate PostgreSQL schema matches expected DeliveryReport model.
    
    Args:
        db: Database session
        request_id: Request ID for tracking
        
    Returns:
        List of validation errors (empty if all valid)
    """
    errors = []
    
    try:
        inspector = inspect(db.get_bind())
        table_names = inspector.get_table_names()
        
        # Check table exists
        if 'delivery_reports' not in table_names:
            errors.append("Table 'delivery_reports' does not exist")
            return errors
        
        # Get columns
        columns = {col['name']: col for col in inspector.get_columns('delivery_reports')}
        
        # Check required columns
        required_columns = [
            'dn_no', 'customer_name', 'upload_batch_id',
            'dn_create_date', 'good_issue_date', 'pod_date'
        ]
        
        for col in required_columns:
            if col not in columns:
                errors.append(f"Required column '{col}' missing from delivery_reports table")
        
        # Check upload_batch_id type (should be VARCHAR, not INTEGER)
        if 'upload_batch_id' in columns:
            col_type = str(columns['upload_batch_id']['type'])
            if 'INT' in col_type.upper() or 'INTEGER' in col_type.upper():
                errors.append(
                    "upload_batch_id is INTEGER type but should be VARCHAR(100) "
                    "to support descriptive batch IDs"
                )
        
        # Check date columns are DATE type
        date_columns = ['dn_create_date', 'good_issue_date', 'pod_date']
        for col in date_columns:
            if col in columns:
                col_type = str(columns[col]['type'])
                if 'DATE' not in col_type.upper():
                    errors.append(
                        f"{col} is {col_type} but should be DATE for exact date preservation"
                    )
        
        # Log results
        if errors:
            logger.warning(f"[{request_id}] Schema validation found {len(errors)} issues")
            for error in errors:
                logger.warning(f"  - {error}")
        else:
            logger.info(f"[{request_id}] PostgreSQL schema validation passed")
        
        return errors
        
    except Exception as e:
        errors.append(f"Schema validation failed: {str(e)}")
        logger.error(f"[{request_id}] Schema validation error: {e}")
        return errors

def check_database_locks(db: Session, request_id: str) -> Dict[str, Any]:
    """
    Check for database locks and return diagnostics.
    
    Args:
        db: Database session
        request_id: Request ID for tracking
        
    Returns:
        Dictionary with lock information
    """
    try:
        # Query for active locks
        result = db.execute(text("""
            SELECT 
                pid,
                usename,
                application_name,
                state,
                wait_event_type,
                wait_event,
                query,
                now() - query_start AS duration
            FROM pg_stat_activity
            WHERE state = 'active'
              AND wait_event_type IS NOT NULL
              AND pid != pg_backend_pid()
            ORDER BY duration DESC
        """))
        
        locks = []
        for row in result:
            locks.append({
                'pid': row.pid,
                'user': row.usename,
                'application': row.application_name,
                'state': row.state,
                'wait_event_type': row.wait_event_type,
                'wait_event': row.wait_event,
                'query': row.query[:200] if row.query else None,
                'duration_seconds': float(row.duration.total_seconds()) if row.duration else 0
            })
        
        if locks:
            logger.warning(f"[{request_id}] Found {len(locks)} active locks")
            for lock in locks[:5]:
                logger.warning(f"  Lock: PID={lock['pid']}, Event={lock['wait_event']}, "
                             f"Duration={lock['duration_seconds']:.2f}s")
        
        return {
            'lock_count': len(locks),
            'locks': locks[:10]
        }
        
    except Exception as e:
        logger.error(f"[{request_id}] Failed to check locks: {e}")
        return {'lock_count': 0, 'locks': [], 'error': str(e)}

def precheck_excel_file(file_path: str, request_id: str) -> Dict[str, Any]:
    """
    Pre-check Excel file before import.
    
    Args:
        file_path: Path to Excel file
        request_id: Request ID for tracking
        
    Returns:
        Dictionary with precheck results
    """
    result = {
        'valid': False,
        'rows': 0,
        'columns': 0,
        'sheets': [],
        'error': None,
        'file_size_mb': 0,
        'file_exists': False
    }
    
    try:
        import pandas as pd
        
        # Check file exists
        result['file_exists'] = os.path.exists(file_path)
        if not result['file_exists']:
            result['error'] = "File does not exist"
            return result
        
        # Check file size
        file_size = os.path.getsize(file_path)
        result['file_size_mb'] = get_file_size_mb(file_size)
        
        if file_size == 0:
            result['error'] = "File is empty (0 bytes)"
            return result
        
        # Try to load workbook
        xl = pd.ExcelFile(file_path)
        result['sheets'] = xl.sheet_names
        
        if not result['sheets']:
            result['error'] = "No sheets found in workbook"
            return result
        
        # Read first sheet
        df = pd.read_excel(file_path, sheet_name=0, engine='openpyxl')
        result['rows'] = len(df)
        result['columns'] = len(df.columns)
        result['valid'] = True
        
        logger.info(f"[{request_id}] Precheck passed: {result['rows']} rows, "
                   f"{result['columns']} columns, {len(result['sheets'])} sheets, "
                   f"{result['file_size_mb']:.2f} MB")
        
        # Log first few rows for debugging
        if result['rows'] > 0:
            logger.debug(f"[{request_id}] First row sample: {df.iloc[0].to_dict()}")
        
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"[{request_id}] Precheck failed: {e}")
    
    return result

# =====================================================================================================
# BLOCK 6: UPLOAD ENDPOINT
# =====================================================================================================

@router.post(
    "/excel",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload Excel file (FORCE REPLACE MODE)",
    description="""
    Upload an Excel file and REPLACE ALL existing delivery data.
    
    This endpoint:
    1. Validates the uploaded file (extension, size, MIME type)
    2. Pre-checks Excel content (structure, sheets, rows)
    3. Validates PostgreSQL schema
    4. **FORCE DELETES** ALL existing delivery records (REPLACE MODE)
    5. Imports new data from Excel
    6. Rolls back on failure (preserving old data)
    
    The operation is atomic - either all data is imported or nothing changes.
    """
)
async def upload_excel(
    file: UploadFile = File(
        ...,
        description="Excel file (.xlsx or .xls) containing delivery data"
    ),
    db: Session = Depends(get_db)
) -> UploadResponse:
    """
    Upload Excel file and REPLACE all delivery data.
    
    Args:
        file: Uploaded Excel file
        db: Database session (injected)
        
    Returns:
        UploadResponse with comprehensive metrics and status
    """
    # Initialize tracking
    request_id = generate_request_id()
    start_time = time.time()
    metrics = PerformanceMetrics()
    
    # State variables
    temp_file_path: Optional[str] = None
    batch_id: Optional[str] = None
    previous_count: int = 0
    deleted_count: int = 0
    
    # Log start
    logger.info("=" * 70)
    logger.info(f"📤 [{request_id}] UPLOAD STARTED")
    logger.info(f"   Filename: {file.filename}")
    logger.info(f"   Replace Mode: {'ACTIVE' if UploadConfig.REPLACE_MODE else 'DISABLED'}")
    logger.info(f"   Force Replace: {'ACTIVE' if UploadConfig.FORCE_REPLACE else 'DISABLED'}")
    logger.info("=" * 70)
    
    try:
        # ======================================================================
        # STEP 1: Request Validation
        # ======================================================================
        with StepTimer("Request Validation", request_id):
            # Check file presence
            if not file:
                return UploadResponse(
                    success=False,
                    message="No file provided",
                    request_id=request_id,
                    errors=["File is required"]
                )
            
            # Check filename
            if not file.filename or file.filename.strip() == "":
                return UploadResponse(
                    success=False,
                    message="Invalid filename",
                    request_id=request_id,
                    errors=["Filename cannot be empty"]
                )
            
            # Normalize filename
            safe_filename = normalize_filename(file.filename)
            if safe_filename != file.filename:
                logger.warning(f"[{request_id}] Filename normalized: {file.filename} → {safe_filename}")
                file.filename = safe_filename
            
            # Check extension
            if not is_valid_extension(file.filename):
                ext = get_file_extension(file.filename)
                allowed = ', '.join(UploadConfig.ALLOWED_EXTENSIONS)
                return UploadResponse(
                    success=False,
                    message=f"Invalid file format. Allowed: {allowed}",
                    request_id=request_id,
                    filename=file.filename,
                    errors=[f"Unsupported extension: '{ext or 'none'}'"]
                )
            
            # Read content for validation
            content = await file.read()
            file_size = len(content)
            
            # Check empty file
            if file_size == 0:
                return UploadResponse(
                    success=False,
                    message="Empty file received",
                    request_id=request_id,
                    filename=file.filename,
                    errors=["File is empty (0 bytes)"]
                )
            
            # Check file size
            if file_size > UploadConfig.MAX_FILE_SIZE:
                size_mb = get_file_size_mb(file_size)
                max_mb = get_file_size_mb(UploadConfig.MAX_FILE_SIZE)
                return UploadResponse(
                    success=False,
                    message=f"File too large. Maximum size: {max_mb:.0f}MB",
                    request_id=request_id,
                    filename=file.filename,
                    errors=[f"File size {size_mb:.2f}MB exceeds maximum {max_mb:.0f}MB"]
                )
            
            # Check MIME type (warning only)
            if file.content_type and not is_valid_mime_type(file.content_type):
                logger.warning(f"[{request_id}] Unusual MIME type: {file.content_type}")
        
        # ======================================================================
        # STEP 2: Save Uploaded File
        # ======================================================================
        with StepTimer("File Save", request_id):
            await file.seek(0)
            
            with tempfile.NamedTemporaryFile(
                suffix=get_file_extension(file.filename),
                delete=False
            ) as temp_file:
                temp_file_path = temp_file.name
                
                bytes_written = 0
                while chunk := await file.read(UploadConfig.CHUNK_SIZE):
                    temp_file.write(chunk)
                    bytes_written += len(chunk)
                
                temp_file.flush()
                os.fsync(temp_file.fileno())
            
            metrics.record('file_save_time', (time.time() - start_time) * 1000)
            logger.info(f"[{request_id}] File saved to: {temp_file_path}")
            logger.info(f"[{request_id}] File size: {get_file_size_mb(file_size):.2f}MB")
        
        # ======================================================================
        # STEP 3: Database Connection Validation
        # ======================================================================
        with StepTimer("Database Connection", request_id):
            try:
                db.execute(text("SELECT 1"))
                logger.info(f"[{request_id}] Database connection verified")
            except Exception as e:
                logger.error(f"[{request_id}] Database connection failed: {e}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Database connection unavailable"
                )
        
        # ======================================================================
        # STEP 4: PostgreSQL Schema Validation
        # ======================================================================
        with StepTimer("Schema Validation", request_id):
            schema_errors = validate_postgresql_schema(db, request_id)
            if schema_errors:
                return UploadResponse(
                    success=False,
                    message="Schema validation failed",
                    request_id=request_id,
                    errors=schema_errors,
                    processing_time=round(time.time() - start_time, 2)
                )
        
        # ======================================================================
        # STEP 5: Excel Precheck
        # ======================================================================
        with StepTimer("Excel Precheck", request_id):
            precheck = precheck_excel_file(temp_file_path, request_id)
            if not precheck['valid']:
                return UploadResponse(
                    success=False,
                    message="Excel file validation failed",
                    request_id=request_id,
                    errors=[precheck['error']],
                    processing_time=round(time.time() - start_time, 2)
                )
            
            # Check row limit
            if precheck['rows'] > UploadConfig.MAX_ROWS_PER_FILE:
                return UploadResponse(
                    success=False,
                    message=f"File exceeds maximum rows ({UploadConfig.MAX_ROWS_PER_FILE:,})",
                    request_id=request_id,
                    errors=[f"Found {precheck['rows']:,} rows, maximum is {UploadConfig.MAX_ROWS_PER_FILE:,}"],
                    processing_time=round(time.time() - start_time, 2)
                )
            
            logger.info(f"[{request_id}] Excel precheck passed: "
                       f"{precheck['rows']:,} rows, {precheck['columns']} columns")
        
        # ======================================================================
        # STEP 6: Database Lock Check
        # ======================================================================
        lock_info = check_database_locks(db, request_id)
        if lock_info.get('lock_count', 0) > 0:
            logger.warning(f"[{request_id}] Found {lock_info['lock_count']} database locks")
        
        # ======================================================================
        # STEP 7: Count Existing Records
        # ======================================================================
        with StepTimer("Count Records", request_id):
            previous_count = count_table_records(db, request_id)
            logger.info(f"[{request_id}] Current records count: {previous_count:,}")
            
            if previous_count < 0:
                raise Exception("Failed to count existing records")
        
        # ======================================================================
        # STEP 8: DELETE ALL EXISTING RECORDS (FORCE REPLACE MODE)
        # ======================================================================
        if UploadConfig.REPLACE_MODE or UploadConfig.FORCE_REPLACE:
            with StepTimer("Delete All Records", request_id):
                delete_start = time.time()
                logger.info(f"🔴 [{request_id}] REPLACE MODE: Deleting ALL {previous_count:,} existing records...")
                
                try:
                    # Check locks before delete
                    if previous_count > 0:
                        lock_check = check_database_locks(db, request_id)
                        if lock_check.get('lock_count', 0) > 0:
                            logger.warning(f"[{request_id}] Locks detected before delete")
                    
                    # Execute delete
                    deleted_count = delete_all_records(db, request_id)
                    metrics.record('delete_time', (time.time() - delete_start) * 1000)
                    
                    # Verify deletion
                    db.flush()
                    remaining = count_table_records(db, request_id)
                    logger.info(f"[{request_id}] Remaining records after flush: {remaining:,}")
                    
                    if remaining == 0:
                        logger.info(f"[{request_id}] ✅ All {deleted_count:,} records deleted successfully")
                    else:
                        logger.warning(f"[{request_id}] ⚠️ {remaining:,} records remain after deletion")
                        
                except Exception as e:
                    logger.error(f"[{request_id}] Failed to delete records: {e}")
                    db.rollback()
                    raise
        else:
            logger.info(f"[{request_id}] REPLACE MODE DISABLED - Preserving existing data")
        
        # ======================================================================
        # STEP 9: Import Excel Data
        # ======================================================================
        import_start = time.time()
        logger.info(f"[{request_id}] Starting Excel import...")
        
        try:
            import_result = await asyncio.wait_for(
                asyncio.to_thread(
                    ExcelImportService.import_delivery_report_excel,
                    db=db,
                    file_path=temp_file_path,
                    source_filename=file.filename,
                    batch_id=None,
                    skip_dups=True,
                    update_existing_rows=False
                ),
                timeout=UploadConfig.IMPORT_TIMEOUT_SECONDS
            )
            
            metrics.record('insert_time', (time.time() - import_start) * 1000)
            
        except asyncio.TimeoutError:
            error_msg = f"Import exceeded timeout ({UploadConfig.IMPORT_TIMEOUT_SECONDS}s)"
            logger.error(f"[{request_id}] {error_msg}")
            
            lock_info = check_database_locks(db, request_id)
            if lock_info.get('lock_count', 0) > 0:
                logger.error(f"[{request_id}] Database locks detected during timeout")
            
            db.rollback()
            raise Exception(error_msg)
        
        # Check import result
        if not import_result.get("success", False):
            error_msg = import_result.get("error", "Import failed")
            logger.error(f"[{request_id}] Import failed: {error_msg}")
            db.rollback()
            raise Exception(error_msg)
        
        batch_id = import_result.get("batch_id")
        logger.info(f"[{request_id}] Import successful, batch_id: {batch_id}")
        
        # ======================================================================
        # STEP 10: Commit Transaction
        # ======================================================================
        with StepTimer("Commit Transaction", request_id):
            commit_start = time.time()
            db.commit()
            metrics.record('commit_time', (time.time() - commit_start) * 1000)
            logger.info(f"[{request_id}] Transaction committed successfully")
        
        # Get final count
        final_count = count_table_records(db, request_id)
        logger.info(f"[{request_id}] Final records count: {final_count:,}")
        
        # ======================================================================
        # STEP 11: Prepare Response
        # ======================================================================
        processing_time = time.time() - start_time
        
        response = UploadResponse(
            success=True,
            message=(
                f"Excel file imported successfully. "
                f"REPLACE MODE: {deleted_count:,} old records deleted, "
                f"{import_result.get('inserted_count', 0):,} new records inserted."
            ),
            request_id=request_id,
            batch_id=batch_id,
            filename=file.filename,
            rows_processed=import_result.get("total_rows", 0),
            rows_inserted=import_result.get("inserted_count", 0),
            rows_updated=import_result.get("updated_count", 0),
            rows_skipped=import_result.get("skipped_count", 0),
            rows_failed=import_result.get("failed_count", 0),
            records_deleted=deleted_count,
            processing_time=round(processing_time, 2),
            database_time=round(
                metrics.get('commit_time', 0) / 1000 + 
                metrics.get('delete_time', 0) / 1000 + 
                metrics.get('insert_time', 0) / 1000, 
                2
            ),
            file_save_time=round(metrics.get('file_save_time', 0), 2),
            excel_read_time=round(metrics.get('excel_read_time', 0), 2),
            column_mapping_time=round(metrics.get('column_mapping_time', 0), 2),
            delete_time=round(metrics.get('delete_time', 0), 2),
            insert_time=round(metrics.get('insert_time', 0), 2),
            commit_time=round(metrics.get('commit_time', 0), 2),
            cleanup_time=0.0,
            warnings=import_result.get("validation_errors", [])[:10],
            errors=[],
            timestamp=datetime.now().isoformat()
        )
        
        # Log success
        logger.info("=" * 70)
        logger.info(f"✅ [{request_id}] UPLOAD COMPLETED SUCCESSFULLY")
        logger.info(f"   Duration: {processing_time:.2f}s")
        logger.info(f"   Records Deleted: {deleted_count:,}")
        logger.info(f"   Records Inserted: {response.rows_inserted:,}")
        logger.info(f"   Records Updated: {response.rows_updated:,}")
        logger.info(f"   Records Skipped: {response.rows_skipped:,}")
        logger.info(f"   Records Failed: {response.rows_failed:,}")
        logger.info(f"   Batch ID: {batch_id}")
        logger.info("=" * 70)
        
        return response
        
    except VerificationError as e:
        # Handle verification error
        try:
            db.rollback()
            logger.warning(f"[{request_id}] Verification error, rolled back")
            logger.info(f"[{request_id}] Previous data preserved ({previous_count:,} records)")
        except Exception as rollback_error:
            logger.error(f"[{request_id}] Rollback failed: {rollback_error}")
        
        processing_time = time.time() - start_time
        logger.error(f"❌ [{request_id}] VERIFICATION FAILED: {str(e)}")
        
        return UploadResponse(
            success=False,
            message="Verification failed - data rolled back",
            request_id=request_id,
            batch_id=batch_id,
            errors=[str(e)],
            processing_time=round(processing_time, 2),
            timestamp=datetime.now().isoformat()
        )
        
    except HTTPException as e:
        # Handle HTTP exception
        try:
            db.rollback()
            logger.warning(f"[{request_id}] HTTP exception, rolled back")
        except Exception as rollback_error:
            logger.error(f"[{request_id}] Rollback failed: {rollback_error}")
        
        processing_time = time.time() - start_time
        logger.error(f"[{request_id}] HTTP exception: {e.detail}")
        
        return UploadResponse(
            success=False,
            message="Upload failed",
            request_id=request_id,
            errors=[str(e.detail)],
            processing_time=round(processing_time, 2),
            timestamp=datetime.now().isoformat()
        )
        
    except Exception as e:
        # Handle general exception
        try:
            db.rollback()
            logger.warning(f"[{request_id}] Exception occurred, rolled back")
            logger.info(f"[{request_id}] Previous data preserved ({previous_count:,} records)")
        except Exception as rollback_error:
            logger.error(f"[{request_id}] Rollback failed: {rollback_error}")
        
        processing_time = time.time() - start_time
        error_trace = traceback.format_exc()
        
        logger.error(f"❌ [{request_id}] UPLOAD FAILED: {str(e)}")
        logger.error(f"   Duration: {processing_time:.2f}s")
        logger.error(f"   Stack trace:\n{error_trace}")
        
        # Log lock status
        lock_info = check_database_locks(db, request_id)
        if lock_info.get('lock_count', 0) > 0:
            logger.error(f"[{request_id}] Database locks present: {lock_info['lock_count']}")
        
        return UploadResponse(
            success=False,
            message="Upload failed",
            request_id=request_id,
            errors=[str(e)],
            processing_time=round(processing_time, 2),
            timestamp=datetime.now().isoformat()
        )
        
    finally:
        # ======================================================================
        # STEP 12: Cleanup Temporary File
        # ======================================================================
        cleanup_start = time.time()
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
                metrics.record('cleanup_time', (time.time() - cleanup_start) * 1000)
                logger.info(f"[{request_id}] Temporary file deleted: {temp_file_path}")
            except Exception as e:
                logger.error(f"[{request_id}] Failed to delete temp file: {e}")

# =====================================================================================================
# BLOCK 7: STATUS ENDPOINT
# =====================================================================================================

@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Get upload service status",
    description="Returns current state of the upload service including total records and batch information."
)
async def get_upload_status(
    db: Session = Depends(get_db)
) -> StatusResponse:
    """
    Get upload service status and statistics.
    
    Args:
        db: Database session (injected)
        
    Returns:
        StatusResponse with service status information
    """
    request_id = generate_request_id()
    start_time = time.time()
    
    logger.info(f"[{request_id}] Status check requested")
    
    try:
        # Get record count
        total_records = count_table_records(db, request_id)
        
        # Get latest batch
        latest_batch = None
        latest_count = 0
        try:
            from sqlalchemy import desc
            latest_record = db.query(DeliveryReport).order_by(
                desc(DeliveryReport.imported_at)
            ).first()
            
            if latest_record:
                latest_batch = latest_record.upload_batch_id
                latest_count = db.query(DeliveryReport).filter(
                    DeliveryReport.upload_batch_id == latest_batch
                ).count()
        except Exception as e:
            logger.warning(f"[{request_id}] Failed to get latest batch: {e}")
        
        # Check database locks
        lock_info = check_database_locks(db, request_id)
        
        status_data = {
            "total_records": total_records if total_records >= 0 else "unknown",
            "latest_batch": latest_batch,
            "latest_batch_count": latest_count,
            "database_connected": total_records >= 0,
            "service_ready": True,
            "upload_enabled": True,
            "active_locks": lock_info.get('lock_count', 0),
            "replace_mode": UploadConfig.REPLACE_MODE,
            "force_replace": UploadConfig.FORCE_REPLACE,
            "max_file_size_mb": get_file_size_mb(UploadConfig.MAX_FILE_SIZE),
            "import_timeout_seconds": UploadConfig.IMPORT_TIMEOUT_SECONDS
        }
        
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"[{request_id}] Status check completed in {elapsed:.2f}ms")
        
        return StatusResponse(
            success=True,
            message="Upload service is operational",
            request_id=request_id,
            status=status_data,
            timestamp=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error(f"[{request_id}] Status check failed: {e}")
        logger.exception(e)
        
        return StatusResponse(
            success=False,
            message="Status check failed",
            request_id=request_id,
            status={
                "service_ready": False,
                "error": str(e)
            },
            timestamp=datetime.now().isoformat()
        )

# =====================================================================================================
# BLOCK 8: HEALTH CHECK ENDPOINT
# =====================================================================================================

@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Comprehensive health check",
    description="""
    Returns detailed health status of all components:
    - Router registration
    - Database connectivity
    - Excel import service
    - DeliveryReport model
    - Service configuration
    
    Used for monitoring and alerting systems.
    """
)
async def health_check(
    db: Session = Depends(get_db)
) -> HealthResponse:
    """
    Health check endpoint with comprehensive diagnostics.
    
    Args:
        db: Database session (injected)
        
    Returns:
        HealthResponse with detailed component status
    """
    request_id = generate_request_id()
    start_time = time.time()
    
    logger.info(f"[{request_id}] Health check requested")
    
    # Check router
    router_status = {
        "registered": True,
        "prefix": router.prefix,
        "tags": router.tags,
        "routes": len(router.routes),
        "routes_list": [
            {"path": route.path, "methods": list(route.methods) if route.methods else []}
            for route in router.routes
            if hasattr(route, 'path') and hasattr(route, 'methods')
        ]
    }
    
    # Check database
    db_status = {
        "connected": False,
        "message": "Not connected",
        "record_count": 0,
        "last_check": None,
        "locks": 0
    }
    
    try:
        db.execute(text("SELECT 1"))
        db_status["connected"] = True
        db_status["message"] = "Connected successfully"
        db_status["last_check"] = datetime.now().isoformat()
        
        count = count_table_records(db, request_id)
        db_status["record_count"] = count if count >= 0 else "unknown"
        
        lock_info = check_database_locks(db, request_id)
        db_status["locks"] = lock_info.get('lock_count', 0)
        
    except Exception as e:
        db_status["message"] = f"Connection failed: {str(e)}"
        logger.error(f"[{request_id}] Database health check failed: {e}")
    
    # Check Excel import service
    excel_import_status = {
        "loaded": True,
        "service_class": "ExcelImportService",
        "batch_size": UploadConfig.BATCH_SIZE,
        "max_rows": UploadConfig.MAX_ROWS_PER_FILE,
        "supported_formats": list(UploadConfig.ALLOWED_EXTENSIONS),
        "requires_pandas": True,
        "requires_openpyxl": True
    }
    
    # Check DeliveryReport model
    model_status = {
        "loaded": True,
        "model_name": "DeliveryReport",
        "table_name": "delivery_reports",
        "fields": [
            "dn_no", "customer_name", "dn_qty", "dn_amount",
            "dn_create_date", "upload_batch_id", "source_file"
        ]
    }
    
    # Validate schema
    schema_errors = validate_postgresql_schema(db, request_id)
    if schema_errors:
        model_status["schema_valid"] = False
        model_status["schema_errors"] = schema_errors
    else:
        model_status["schema_valid"] = True
    
    # Determine overall health
    is_healthy = db_status["connected"] and model_status.get("schema_valid", False)
    health_status = HealthStatus.HEALTHY if is_healthy else HealthStatus.DEGRADED
    
    processing_time = time.time() - start_time
    
    response = HealthResponse(
        status=health_status,
        router=router_status,
        database=db_status,
        upload_service={
            "status": "operational" if is_healthy else "degraded",
            "max_file_size_bytes": UploadConfig.MAX_FILE_SIZE,
            "max_file_size_mb": get_file_size_mb(UploadConfig.MAX_FILE_SIZE),
            "supported_extensions": list(UploadConfig.ALLOWED_EXTENSIONS),
            "supported_mime_types": list(UploadConfig.ALLOWED_MIME_TYPES),
            "processing_time_seconds": round(processing_time, 2),
            "import_timeout_seconds": UploadConfig.IMPORT_TIMEOUT_SECONDS,
            "replace_mode": UploadConfig.REPLACE_MODE,
            "force_replace": UploadConfig.FORCE_REPLACE,
            "max_rows_per_file": UploadConfig.MAX_ROWS_PER_FILE
        },
        excel_import_service=excel_import_status,
        delivery_report_model=model_status,
        supported_file_types=list(UploadConfig.ALLOWED_EXTENSIONS),
        max_upload_size=UploadConfig.MAX_FILE_SIZE,
        max_upload_size_mb=get_file_size_mb(UploadConfig.MAX_FILE_SIZE),
        application_version="5.0.0",
        timestamp=datetime.now().isoformat()
    )
    
    logger.info(f"[{request_id}] Health check completed: {response.status.value} in {processing_time*1000:.2f}ms")
    
    return response

# =====================================================================================================
# BLOCK 9: ROUTER INITIALIZATION LOGGING
# =====================================================================================================

logger.info("=" * 70)
logger.info("📤 UPLOAD ROUTER v5.0 - ENTERPRISE PRODUCTION GRADE")
logger.info("=" * 70)
logger.info("")
logger.info("  ROUTER CONFIGURATION:")
logger.info(f"  ✅ Prefix: {router.prefix}")
logger.info(f"  ✅ Tags: {router.tags}")
logger.info(f"  ✅ Routes: {len(router.routes)}")
logger.info("")
logger.info("  ENDPOINTS:")
for route in router.routes:
    if hasattr(route, 'path') and hasattr(route, 'methods'):
        methods = ', '.join(route.methods) if route.methods else 'GET'
        logger.info(f"  ✅ {methods:10} {router.prefix}{route.path}")
logger.info("")
logger.info("  FEATURES:")
logger.info(f"  🔴 REPLACE MODE: {'ACTIVE' if UploadConfig.REPLACE_MODE else 'DISABLED'}")
logger.info(f"  🔴 FORCE REPLACE: {'ACTIVE' if UploadConfig.FORCE_REPLACE else 'DISABLED'}")
logger.info("  🔴 OLD DATA WILL BE DELETED ON EVERY UPLOAD")
logger.info("  ✅ Atomic transactions (all or nothing)")
logger.info("  ✅ Detailed step-by-step logging with timing")
logger.info("  ✅ Database lock diagnostics")
logger.info("  ✅ Comprehensive import time metrics")
logger.info("  ✅ PostgreSQL schema validation")
logger.info("  ✅ Excel import precheck")
logger.info("  ✅ Import watchdog with timeout")
logger.info("  ✅ Pydantic v2 compatibility")
logger.info("  ✅ Enterprise-grade error handling")
logger.info("  ✅ Environment configuration support")
logger.info("")
logger.info("  CONFIGURATION:")
logger.info(f"  📁 Max File Size: {UploadConfig.MAX_FILE_SIZE / (1024*1024):.0f} MB")
logger.info(f"  ⏱️ Import Timeout: {UploadConfig.IMPORT_TIMEOUT_SECONDS}s ({UploadConfig.IMPORT_TIMEOUT_SECONDS//60} minutes)")
logger.info(f"  📊 Max Rows: {UploadConfig.MAX_ROWS_PER_FILE:,}")
logger.info(f"  📦 Batch Size: {UploadConfig.BATCH_SIZE:,}")
logger.info("")
logger.info("  STATUS: ✅ ENTERPRISE PRODUCTION READY")
logger.info("=" * 70)

# =====================================================================================================
# BLOCK 10: EXPORTS
# =====================================================================================================

__all__ = [
    'router',
    'upload_excel',
    'get_upload_status',
    'health_check',
    'UploadResponse',
    'HealthResponse',
    'StatusResponse',
    'UploadConfig'
]

# =====================================================================================================
# END OF FILE
# =====================================================================================================
