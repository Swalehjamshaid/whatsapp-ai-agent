# ==========================================================
# FILE: app/routes/upload.py
# ==========================================================

import os
import uuid
import logging
from datetime import datetime

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Depends,
    HTTPException
)

from sqlalchemy.orm import Session

from app.database import get_db
from app.services.excel_import_service import (
    import_delivery_report_excel,
    get_batch_summary,
    delete_import_batch
)

# IMPROVEMENT 3: Add logging
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/upload",
    tags=["Excel Upload"]
)


# ==========================================================
# CONFIGURATION
# ==========================================================

UPLOAD_FOLDER = "uploads"
MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


# ==========================================================
# UPLOAD EXCEL
# ==========================================================

@router.post("/excel")
async def upload_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Upload DN / PGI / POD Excel file.
    """
    file_path = None
    
    try:
        # IMPROVEMENT 3: Log upload start
        logger.info(f"Starting upload: {file.filename}")
        print(f"📤 Uploading file: {file.filename}")
        
        # Validate file extension
        allowed_extensions = [".xlsx", ".xls"]
        extension = os.path.splitext(file.filename)[1].lower()
        
        if extension not in allowed_extensions:
            logger.warning(f"Invalid file type: {extension}")
            raise HTTPException(
                status_code=400,
                detail=f"Only Excel files (.xlsx, .xls) are allowed. Got: {extension}"
            )
        
        # IMPROVEMENT 2: Read and validate file size
        contents = await file.read()
        file_size_mb = len(contents) / (1024 * 1024)
        
        if len(contents) > MAX_FILE_SIZE_BYTES:
            logger.warning(f"File too large: {file_size_mb:.2f}MB > {MAX_FILE_SIZE_MB}MB")
            raise HTTPException(
                status_code=400,
                detail=f"File exceeds {MAX_FILE_SIZE_MB}MB limit. Current size: {file_size_mb:.2f}MB"
            )
        
        logger.info(f"File size: {file_size_mb:.2f}MB - Accepted")
        
        # Generate batch ID and file path
        batch_id = int(datetime.utcnow().timestamp())
        unique_filename = f"{uuid.uuid4()}_{file.filename}"
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        # Save file
        with open(file_path, "wb") as buffer:
            buffer.write(contents)
        
        logger.info(f"File saved to: {file_path}")
        print(f"💾 File saved. Batch ID: {batch_id}")
        
        # Import Excel data
        result = import_delivery_report_excel(
            db=db,
            file_path=file_path,
            source_filename=file.filename,
            batch_id=batch_id,
            skip_duplicates=True,
            update_existing=False
        )
        
        # IMPROVEMENT 3: Log import results
        if result.get("success"):
            logger.info(
                f"Batch {batch_id} imported successfully: "
                f"{result.get('inserted_count', 0)} inserted, "
                f"{result.get('updated_count', 0)} updated, "
                f"{result.get('skipped_count', 0)} skipped, "
                f"{result.get('total_rows', 0)} total rows"
            )
            print(f"✅ Import successful: {result.get('inserted_count', 0)} records inserted")
        else:
            logger.error(f"Batch {batch_id} import failed: {result.get('error')}")
            print(f"❌ Import failed: {result.get('error')}")
        
        return {
            "success": result.get("success"),
            "batch_id": batch_id,
            "file_name": file.filename,
            "file_size_mb": round(file_size_mb, 2),
            "result": result
        }
        
    except HTTPException:
        raise
        
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        print(f"❌ Upload error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {str(e)}"
        )
        
    finally:
        # IMPROVEMENT 1: Delete temporary file
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Temporary file deleted: {file_path}")
                print(f"🗑️ Temporary file cleaned up: {os.path.basename(file_path)}")
            except Exception as e:
                logger.warning(f"Failed to delete temporary file {file_path}: {e}")


# ==========================================================
# GET BATCH SUMMARY
# ==========================================================

@router.get("/batch/{batch_id}")
def batch_summary(
    batch_id: int,
    db: Session = Depends(get_db)
):
    """
    Get upload batch statistics.
    """
    logger.info(f"Fetching summary for batch {batch_id}")
    
    result = get_batch_summary(
        db=db,
        batch_id=batch_id
    )
    
    if "error" in result:
        logger.warning(f"Batch {batch_id} not found")
        raise HTTPException(
            status_code=404,
            detail=result["error"]
        )
    
    logger.info(f"Batch {batch_id} summary: {result.get('total_records', 0)} records")
    return result


# ==========================================================
# DELETE BATCH
# ==========================================================

@router.delete("/batch/{batch_id}")
def delete_batch(
    batch_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete uploaded batch records.
    """
    logger.info(f"Deleting batch {batch_id}")
    
    result = delete_import_batch(
        db=db,
        batch_id=batch_id
    )

    if not result.get("success"):
        logger.error(f"Failed to delete batch {batch_id}: {result.get('error')}")
        raise HTTPException(
            status_code=400,
            detail=result.get("error")
        )

    logger.info(f"Batch {batch_id} deleted: {result.get('deleted_count', 0)} records removed")
    return result


# ==========================================================
# HEALTH CHECK
# ==========================================================

@router.get("/status")
def upload_status():
    """
    Upload module health check.
    """
    # Check folder size (optional - for monitoring)
    folder_size_bytes = 0
    file_count = 0
    
    if os.path.exists(UPLOAD_FOLDER):
        for file in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, file)
            if os.path.isfile(file_path):
                file_count += 1
                folder_size_bytes += os.path.getsize(file_path)
    
    folder_size_mb = round(folder_size_bytes / (1024 * 1024), 2)
    
    return {
        "status": "healthy",
        "upload_folder": UPLOAD_FOLDER,
        "folder_exists": os.path.exists(UPLOAD_FOLDER),
        "max_file_size_mb": MAX_FILE_SIZE_MB,
        "current_file_count": file_count,
        "current_folder_size_mb": folder_size_mb
    }


# ==========================================================
# ADDITIONAL UTILITY ENDPOINTS
# ==========================================================

@router.get("/batches/recent")
def recent_batches(
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """
    Get most recent upload batches.
    """
    from app.models import DeliveryReport
    from sqlalchemy import func
    
    batches = db.query(
        DeliveryReport.upload_batch_id,
        func.count(DeliveryReport.id).label('record_count'),
        func.max(DeliveryReport.imported_at).label('uploaded_at'),
        DeliveryReport.source_file
    ).group_by(
        DeliveryReport.upload_batch_id,
        DeliveryReport.source_file
    ).order_by(
        func.max(DeliveryReport.imported_at).desc()
    ).limit(limit).all()
    
    return {
        "batches": [
            {
                "batch_id": batch.upload_batch_id,
                "record_count": batch.record_count,
                "uploaded_at": batch.uploaded_at.isoformat() if batch.uploaded_at else None,
                "source_file": batch.source_file
            }
            for batch in batches if batch.upload_batch_id
        ]
    }
