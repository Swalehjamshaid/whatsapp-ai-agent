# ==========================================================
# FILE: app/routes/upload.py
# ==========================================================

import os
import uuid
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

router = APIRouter(
    prefix="/upload",
    tags=["Excel Upload"]
)


# ==========================================================
# CONFIGURATION
# ==========================================================

UPLOAD_FOLDER = "uploads"

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

    allowed_extensions = [
        ".xlsx",
        ".xls"
    ]

    extension = os.path.splitext(
        file.filename
    )[1].lower()

    if extension not in allowed_extensions:

        raise HTTPException(
            status_code=400,
            detail="Only Excel files (.xlsx, .xls) are allowed"
        )

    batch_id = int(
        datetime.utcnow().timestamp()
    )

    unique_filename = (
        f"{uuid.uuid4()}_{file.filename}"
    )

    file_path = os.path.join(
        UPLOAD_FOLDER,
        unique_filename
    )

    try:

        with open(
            file_path,
            "wb"
        ) as buffer:

            buffer.write(
                await file.read()
            )

        result = import_delivery_report_excel(
            db=db,
            file_path=file_path,
            source_filename=file.filename,
            batch_id=batch_id,
            skip_duplicates=True,
            update_existing=False
        )

        return {
            "success": result.get("success"),
            "batch_id": batch_id,
            "file_name": file.filename,
            "result": result
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


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

    return get_batch_summary(
        db=db,
        batch_id=batch_id
    )


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

    result = delete_import_batch(
        db=db,
        batch_id=batch_id
    )

    if not result.get("success"):

        raise HTTPException(
            status_code=400,
            detail=result.get("error")
        )

    return result


# ==========================================================
# HEALTH CHECK
# ==========================================================

@router.get("/status")
def upload_status():
    """
    Upload module health check.
    """

    return {
        "status": "healthy",
        "upload_folder": UPLOAD_FOLDER,
        "folder_exists": os.path.exists(
            UPLOAD_FOLDER
        )
    }
