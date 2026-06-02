# ==========================================================
# FILE: app/services/logistics_query_service.py
# ==========================================================

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import DeliveryReport


class LogisticsQueryService:

    # ======================================================
    # DN SEARCH
    # ======================================================

    @staticmethod
    def get_dn_status(
        db: Session,
        dn_no: str
    ):
        deliveries = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.dn_no == dn_no
            )
            .all()
        )

        if not deliveries:
            return {
                "success": False,
                "message": f"DN {dn_no} not found"
            }

        return {
            "success": True,
            "dn_no": dn_no,
            "total_lines": len(deliveries),
            "records": [
                {
                    "dealer_code": d.dealer_code,
                    "customer_name": d.customer_name,
                    "material_no": d.material_no,
                    "city": d.ship_to_city,
                    "warehouse": d.warehouse,
                    "division": d.division,
                    "delivery_status": d.delivery_status,
                    "pgi_status": d.pgi_status,
                    "pod_status": d.pod_status,
                    "pending_flag": d.pending_flag,
                    "dn_amount": float(d.dn_amount or 0)
                }
                for d in deliveries
            ]
        }

    # ======================================================
    # PENDING DELIVERIES
    # ======================================================

    @staticmethod
    def get_pending_deliveries(
        db: Session,
        limit: int = 100
    ):
        rows = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.pending_flag.is_(True)
            )
            .limit(limit)
            .all()
        )

        return {
            "count": len(rows),
            "records": rows
        }

    # ======================================================
    # PENDING POD
    # ======================================================

    @staticmethod
    def get_pending_pod(
        db: Session
    ):
        count = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.pod_status == "Pending"
            )
            .count()
        )

        return {
            "pending_pod": count
        }

    # ======================================================
    # PENDING PGI
    # ======================================================

    @staticmethod
    def get_pending_pgi(
        db: Session
    ):
        count = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.pgi_status == "Pending"
            )
            .count()
        )

        return {
            "pending_pgi": count
        }

    # ======================================================
    # CITY SEARCH
    # ======================================================

    @staticmethod
    def get_city_deliveries(
        db: Session,
        city: str,
        limit: int = 100
    ):
        rows = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.ship_to_city.ilike(
                    f"%{city}%"
                )
            )
            .limit(limit)
            .all()
        )

        return {
            "city": city,
            "count": len(rows),
            "records": rows
        }

    # ======================================================
    # DEALER SEARCH
    # ======================================================

    @staticmethod
    def get_dealer_deliveries(
        db: Session,
        dealer_code: str,
        limit: int = 100
    ):
        rows = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.dealer_code.ilike(
                    f"%{dealer_code}%"
                )
            )
            .limit(limit)
            .all()
        )

        return {
            "dealer_code": dealer_code,
            "count": len(rows),
            "records": rows
        }

    # ======================================================
    # WAREHOUSE SEARCH
    # ======================================================

    @staticmethod
    def get_warehouse_deliveries(
        db: Session,
        warehouse: str,
        limit: int = 100
    ):
        rows = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.warehouse.ilike(
                    f"%{warehouse}%"
                )
            )
            .limit(limit)
            .all()
        )

        return {
            "warehouse": warehouse,
            "count": len(rows),
            "records": rows
        }

    # ======================================================
    # DIVISION SEARCH
    # ======================================================

    @staticmethod
    def get_division_deliveries(
        db: Session,
        division: str
    ):
        rows = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.division.ilike(
                    f"%{division}%"
                )
            )
            .all()
        )

        return {
            "division": division,
            "count": len(rows),
            "records": rows
        }

    # ======================================================
    # DASHBOARD SUMMARY
    # ======================================================

    @staticmethod
    def get_dashboard_summary(
        db: Session
    ):
        total_records = (
            db.query(DeliveryReport)
            .count()
        )

        pending_deliveries = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.pending_flag.is_(True)
            )
            .count()
        )

        pending_pod = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.pod_status == "Pending"
            )
            .count()
        )

        pending_pgi = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.pgi_status == "Pending"
            )
            .count()
        )

        pending_amount = (
            db.query(
                func.sum(
                    DeliveryReport.dn_amount
                )
            )
            .filter(
                DeliveryReport.pending_flag.is_(True)
            )
            .scalar()
        ) or 0

        return {
            "total_records": total_records,
            "pending_deliveries": pending_deliveries,
            "pending_pod": pending_pod,
            "pending_pgi": pending_pgi,
            "pending_amount": float(
                pending_amount
            )
        }

    # ======================================================
    # TOP CITIES
    # ======================================================

    @staticmethod
    def get_top_cities(
        db: Session,
        limit: int = 10
    ):
        rows = (
            db.query(
                DeliveryReport.ship_to_city,
                func.count(
                    DeliveryReport.id
                ).label("count")
            )
            .group_by(
                DeliveryReport.ship_to_city
            )
            .order_by(
                func.count(
                    DeliveryReport.id
                ).desc()
            )
            .limit(limit)
            .all()
        )

        return [
            {
                "city": row.ship_to_city,
                "count": row.count
            }
            for row in rows
            if row.ship_to_city
        ]
