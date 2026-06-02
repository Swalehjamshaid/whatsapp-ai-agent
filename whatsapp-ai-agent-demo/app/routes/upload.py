# ==========================================================
# FILE: app/routes/upload.py
# ==========================================================

import os
import uuid
import logging
import shutil
from datetime import datetime
from tempfile import NamedTemporaryFile

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

# Import DeliveryReport model for deletion
from app.models import DeliveryReport

# Logging
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
    db: Session = Depends(get_db),
    replace_previous: bool = True  # New parameter: whether to replace previous data
):
    """
    Upload DN / PGI / POD Excel file.
    
    - replace_previous=True: Deletes all previous uploads, keeps only this batch
    - replace_previous=False: Keeps all previous uploads, adds this batch
    """
    file_path = None
    
    try:
        # PRIORITY 4: Validate filename
        if not file.filename:
            logger.warning("No file selected")
            raise HTTPException(
                status_code=400,
                detail="No file selected. Please choose an Excel file to upload."
            )
        
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
        
        # PRIORITY 3: Read and validate file is not empty
        contents = await file.read()
        
        if len(contents) == 0:
            logger.warning("Uploaded file is empty")
            raise HTTPException(
                status_code=400,
                detail="Uploaded file is empty. Please check your file and try again."
            )
        
        file_size_mb = len(contents) / (1024 * 1024)
        
        # Validate file size
        if len(contents) > MAX_FILE_SIZE_BYTES:
            logger.warning(f"File too large: {file_size_mb:.2f}MB > {MAX_FILE_SIZE_MB}MB")
            raise HTTPException(
                status_code=400,
                detail=f"File exceeds {MAX_FILE_SIZE_MB}MB limit. Current size: {file_size_mb:.2f}MB"
            )
        
        logger.info(f"File size: {file_size_mb:.2f}MB - Accepted")
        print(f"📊 File size: {file_size_mb:.2f}MB")
        
        # Generate batch ID and file path
        batch_id = int(datetime.utcnow().timestamp())
        unique_filename = f"{uuid.uuid4()}_{file.filename}"
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        # PRIORITY 5: For future - stream large files directly to disk
        # Currently using memory for simplicity, but can be optimized
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
        
        # PRIORITY 2: Only delete previous data if import was successful AND rows were inserted
        if result.get("success") and result.get("inserted_count", 0) > 0:
            if replace_previous:
                # PRIORITY 1: Delete all previous batches except current one
                old_records_deleted = (
                    db.query(DeliveryReport)
                    .filter(
                        DeliveryReport.upload_batch_id != batch_id
                    )
                    .delete(synchronize_session=False)
                )
                db.commit()
                
                logger.info(f"Deleted {old_records_deleted} old records from previous batches")
                print(f"🗑️ Replaced {old_records_deleted} old records with new data")
            else:
                print(f"📦 Keeping previous data, added {result.get('inserted_count', 0)} new records")
        elif result.get("success") and result.get("inserted_count", 0) == 0:
            print(f"⚠️ Import completed but no new records were inserted. Previous data preserved.")
            logger.warning(f"Batch {batch_id} imported 0 records - no data replaced")
        
        # PRIORITY 3: Log import results
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
        
        # PRIORITY 6: Return simplified response structure
        return {
            "success": result.get("success"),
            "batch_id": batch_id,
            "file_name": file.filename,
            "file_size_mb": round(file_size_mb, 2),
            "replace_previous": replace_previous,
            # Simplified statistics for dashboard
            "inserted": result.get("inserted_count", 0),
            "updated": result.get("updated_count", 0),
            "skipped": result.get("skipped_count", 0),
            "total_rows": result.get("total_rows", 0),
            # Keep full result for debugging
            "details": result
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
        # Delete temporary file
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
# GET LATEST BATCH (Utility endpoint)
# ==========================================================

@router.get("/batch/latest")
def latest_batch(
    db: Session = Depends(get_db)
):
    """
    Get the most recent upload batch.
    """
    from sqlalchemy import func
    
    latest = db.query(
        DeliveryReport.upload_batch_id,
        func.max(DeliveryReport.imported_at).label('uploaded_at')
    ).group_by(
        DeliveryReport.upload_batch_id
    ).order_by(
        func.max(DeliveryReport.imported_at).desc()
    ).first()
    
    if not latest or not latest.upload_batch_id:
        raise HTTPException(
            status_code=404,
            detail="No upload batches found"
        )
    
    result = get_batch_summary(
        db=db,
        batch_id=latest.upload_batch_id
    )
    
    return result


# ==========================================================
# DELETE BATCH
# ==========================================================

@router.delete("/batch/{batch_id}")
def delete_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    confirm: bool = False
):
    """
    Delete uploaded batch records.
    
    - confirm=True: Actually delete the batch
    - confirm=False: Return info about what would be deleted
    """
    logger.info(f"Delete request for batch {batch_id}, confirm={confirm}")
    
    # First, get info about what will be deleted
    count_query = db.query(DeliveryReport).filter(
        DeliveryReport.upload_batch_id == batch_id
    )
    
    record_count = count_query.count()
    
    if record_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Batch {batch_id} not found or has no records"
        )
    
    if not confirm:
        return {
            "confirm_required": True,
            "message": f"This will delete {record_count} records from batch {batch_id}",
            "batch_id": batch_id,
            "record_count": record_count,
            "endpoint": f"/upload/batch/{batch_id}?confirm=true"
        }
    
    # Perform deletion
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
# DELETE ALL BATCHES (Admin utility)
# ==========================================================

@router.delete("/all")
def delete_all_batches(
    db: Session = Depends(get_db),
    confirm: bool = False
):
    """
    DELETE ALL upload batches (Admin only).
    
    - confirm=True: Delete all data
    - confirm=False: Return warning
    """
    total_records = db.query(DeliveryReport).count()
    
    if not confirm:
        return {
            "confirm_required": True,
            "warning": "⚠️ DANGER: This will delete ALL delivery records!",
            "total_records": total_records,
            "endpoint": "/upload/all?confirm=true"
        }
    
    # Delete all records
    deleted = db.query(DeliveryReport).delete()
    db.commit()
    
    # Also clean up upload folder
    if os.path.exists(UPLOAD_FOLDER):
        for file in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, file)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.warning(f"Failed to delete {file_path}: {e}")
    
    logger.warning(f"ALL DATA DELETED: {deleted} records removed")
    
    return {
        "success": True,
        "message": "All delivery records have been deleted",
        "deleted_count": deleted
    }


# ==========================================================
# HEALTH CHECK
# ==========================================================

@router.get("/status")
def upload_status():
    """
    Upload module health check.
    """
    # Check folder size
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


# ==========================================================
# GET STATISTICS
# ==========================================================

@router.get("/statistics")
def upload_statistics(
    db: Session = Depends(get_db)
):
    """
    Get overall upload statistics.
    """
    from sqlalchemy import func
    
    total_records = db.query(DeliveryReport).count()
    total_batches = db.query(DeliveryReport.upload_batch_id).distinct().count()
    
    last_upload = db.query(
        DeliveryReport.upload_batch_id,
        func.max(DeliveryReport.imported_at).label('uploaded_at')
    ).group_by(
        DeliveryReport.upload_batch_id
    ).order_by(
        func.max(DeliveryReport.imported_at).desc()
    ).first()
    
    return {
        "total_records": total_records,
        "total_batches": total_batches,
        "last_batch_id": last_upload.upload_batch_id if last_upload else None,
        "last_upload_date": last_upload.uploaded_at.isoformat() if last_upload and last_upload.uploaded_at else None
    }
