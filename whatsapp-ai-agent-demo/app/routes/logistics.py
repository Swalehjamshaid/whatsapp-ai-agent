# ==========================================================
# FILE: app/routes/logistics.py
# ==========================================================

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query
)

from sqlalchemy.orm import Session

from app.database import get_db

from app.services.logistics_query_service import (
    LogisticsQueryService
)


# ==========================================================
# ROUTER
# ==========================================================

router = APIRouter(
    prefix="/logistics",
    tags=["Logistics"]
)


# ==========================================================
# DASHBOARD SUMMARY
# ==========================================================

@router.get("/summary")
def dashboard_summary(
    db: Session = Depends(get_db)
):
    """
    Dashboard KPI summary.
    """

    return LogisticsQueryService.get_dashboard_summary(
        db
    )


# ==========================================================
# DN STATUS
# ==========================================================

@router.get("/dn/{dn_no}")
def dn_status(
    dn_no: str,
    db: Session = Depends(get_db)
):
    """
    Get DN details and status.
    """

    result = (
        LogisticsQueryService.get_dn_status(
            db=db,
            dn_no=dn_no
        )
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=404,
            detail=result.get("message")
        )

    return result


# ==========================================================
# PENDING DELIVERIES
# ==========================================================

@router.get("/pending")
def pending_deliveries(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000
    ),
    db: Session = Depends(get_db)
):
    """
    List pending deliveries.
    """

    return (
        LogisticsQueryService
        .get_pending_deliveries(
            db=db,
            limit=limit
        )
    )


# ==========================================================
# PENDING POD
# ==========================================================

@router.get("/pending-pod")
def pending_pod(
    db: Session = Depends(get_db)
):
    """
    Pending POD count.
    """

    return (
        LogisticsQueryService
        .get_pending_pod(
            db=db
        )
    )


# ==========================================================
# PENDING PGI
# ==========================================================

@router.get("/pending-pgi")
def pending_pgi(
    db: Session = Depends(get_db)
):
    """
    Pending PGI count.
    """

    return (
        LogisticsQueryService
        .get_pending_pgi(
            db=db
        )
    )


# ==========================================================
# CITY SEARCH
# ==========================================================

@router.get("/city/{city}")
def city_deliveries(
    city: str,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000
    ),
    db: Session = Depends(get_db)
):
    """
    Search deliveries by city.
    """

    return (
        LogisticsQueryService
        .get_city_deliveries(
            db=db,
            city=city,
            limit=limit
        )
    )


# ==========================================================
# DEALER SEARCH
# ==========================================================

@router.get("/dealer/{dealer_code}")
def dealer_deliveries(
    dealer_code: str,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000
    ),
    db: Session = Depends(get_db)
):
    """
    Search deliveries by dealer.
    """

    return (
        LogisticsQueryService
        .get_dealer_deliveries(
            db=db,
            dealer_code=dealer_code,
            limit=limit
        )
    )


# ==========================================================
# WAREHOUSE SEARCH
# ==========================================================

@router.get("/warehouse/{warehouse}")
def warehouse_deliveries(
    warehouse: str,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000
    ),
    db: Session = Depends(get_db)
):
    """
    Search deliveries by warehouse.
    """

    return (
        LogisticsQueryService
        .get_warehouse_deliveries(
            db=db,
            warehouse=warehouse,
            limit=limit
        )
    )


# ==========================================================
# DIVISION SEARCH
# ==========================================================

@router.get("/division/{division}")
def division_deliveries(
    division: str,
    db: Session = Depends(get_db)
):
    """
    Search deliveries by division.
    """

    return (
        LogisticsQueryService
        .get_division_deliveries(
            db=db,
            division=division
        )
    )


# ==========================================================
# TOP CITIES
# ==========================================================

@router.get("/top-cities")
def top_cities(
    limit: int = Query(
        default=10,
        ge=1,
        le=50
    ),
    db: Session = Depends(get_db)
):
    """
    Top cities by delivery volume.
    """

    return (
        LogisticsQueryService
        .get_top_cities(
            db=db,
            limit=limit
        )
    )


# ==========================================================
# HEALTH CHECK
# ==========================================================

@router.get("/health")
def logistics_health():
    """
    Logistics module health.
    """

    return {
        "status": "healthy",
        "module": "logistics"
    }
