# ============================================================
# FILE: app/services/timeline.py
# VERSION: 30.0 – TIMELINE & PIPELINE MODULE
# ============================================================
# RESPONSIBILITIES:
#   - Delivery pipeline (funnel)
#   - Monthly trend
#   - Delivery standard compliance (distance bands)
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, extract
from sqlalchemy.orm import Session

from app.models import DeliveryReport

logger = logging.getLogger(__name__)


def fetch_timeline_data(db: Session, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Fetch pipeline, monthly trend, and compliance data.
    """
    try:
        # 1. Pipeline (daily aggregated counts for the latest day)
        # For simplicity, we take today's pipeline numbers from the summary
        # In production, you might query the latest day
        today = func.current_date()
        pipeline_q = select(
            func.count(DeliveryReport.dn_no.distinct()).label("total_dn"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
            func.count(DeliveryReport.dn_no.distinct()).filter(
                DeliveryReport.good_issue_date.isnot(None)
            ).label("pgi_dn"),
            func.coalesce(func.sum(DeliveryReport.dn_qty).filter(
                DeliveryReport.good_issue_date.isnot(None)
            ), 0).label("pgi_units"),
            func.count(DeliveryReport.dn_no.distinct()).filter(
                DeliveryReport.pod_date.isnot(None)
            ).label("delivered_dn"),
            func.coalesce(func.sum(DeliveryReport.dn_qty).filter(
                DeliveryReport.pod_date.isnot(None)
            ), 0).label("delivered_units"),
            func.count(DeliveryReport.dn_no.distinct()).filter(
                DeliveryReport.pod_date.isnot(None)
            ).label("pod_dn"),
            func.coalesce(func.sum(DeliveryReport.dn_qty).filter(
                DeliveryReport.pod_date.isnot(None)
            ), 0).label("pod_units"),
        ).where(DeliveryReport.dn_create_date == today)

        pipeline_row = db.execute(pipeline_q).first()
        if pipeline_row:
            pipeline_detailed = {
                "dn_created": {"dn": pipeline_row.total_dn, "units": pipeline_row.total_units, "pct": 100.0},
                "pgi_completed": {"dn": pipeline_row.pgi_dn, "units": pipeline_row.pgi_units, "pct": SafeNumber.pct(pipeline_row.pgi_units, pipeline_row.total_units)},
                "in_transit": {"dn": max(0, pipeline_row.pgi_dn - pipeline_row.delivered_dn), "units": max(0, pipeline_row.pgi_units - pipeline_row.delivered_units), "pct": SafeNumber.pct(max(0, pipeline_row.pgi_units - pipeline_row.delivered_units), pipeline_row.pgi_units)},
                "delivered": {"dn": pipeline_row.delivered_dn, "units": pipeline_row.delivered_units, "pct": SafeNumber.pct(pipeline_row.delivered_units, pipeline_row.pgi_units)},
                "pod_received": {"dn": pipeline_row.pod_dn, "units": pipeline_row.pod_units, "pct": SafeNumber.pct(pipeline_row.pod_units, pipeline_row.delivered_units)},
            }
        else:
            pipeline_detailed = {}

        # 2. Monthly trend (last 12 months)
        monthly_q = select(
            func.date_trunc('month', DeliveryReport.dn_create_date).label("month"),
            func.count(DeliveryReport.dn_no.distinct()).label("dn_count"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("revenue"),
            func.count(DeliveryReport.dn_no.distinct()).filter(
                DeliveryReport.pod_date.isnot(None)
            ).label("delivered_dn"),
        ).group_by(func.date_trunc('month', DeliveryReport.dn_create_date)
        ).order_by(func.date_trunc('month', DeliveryReport.dn_create_date).desc()).limit(12)

        monthly_rows = db.execute(monthly_q).all()
        monthly_trend = [
            {
                "month": row.month.isoformat() if row.month else None,
                "dn_count": row.dn_count,
                "units": row.units,
                "revenue": row.revenue,
                "delivery_pct": SafeNumber.pct(row.delivered_dn, row.dn_count),
            }
            for row in monthly_rows
        ][::-1]  # reverse to chronological order

        # 3. Delivery compliance by distance bands
        # We assume distance_km column exists
        compliance_q = select(
            func.case(
                (DeliveryReport.distance_km <= 100, '0-100'),
                (DeliveryReport.distance_km <= 250, '101-250'),
                (DeliveryReport.distance_km <= 450, '251-450'),
                (DeliveryReport.distance_km <= 700, '451-700'),
                (DeliveryReport.distance_km <= 900, '701-900'),
                else_='900+'
            ).label("distance"),
            func.case(
                (DeliveryReport.distance_km <= 100, 1),
                (DeliveryReport.distance_km <= 250, 2),
                (DeliveryReport.distance_km <= 450, 3),
                (DeliveryReport.distance_km <= 700, 4),
                (DeliveryReport.distance_km <= 900, 5),
                else_=6
            ).label("target_days"),
            func.coalesce(
                func.avg(
                    func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400.0
                ).filter(
                    DeliveryReport.pod_date.isnot(None),
                    DeliveryReport.good_issue_date.isnot(None)
                ),
                0
            ).label("actual_days"),
            func.count().label("delivery_records"),
        ).group_by(
            func.case(
                (DeliveryReport.distance_km <= 100, '0-100'),
                (DeliveryReport.distance_km <= 250, '101-250'),
                (DeliveryReport.distance_km <= 450, '251-450'),
                (DeliveryReport.distance_km <= 700, '451-700'),
                (DeliveryReport.distance_km <= 900, '701-900'),
                else_='900+'
            ),
            func.case(
                (DeliveryReport.distance_km <= 100, 1),
                (DeliveryReport.distance_km <= 250, 2),
                (DeliveryReport.distance_km <= 450, 3),
                (DeliveryReport.distance_km <= 700, 4),
                (DeliveryReport.distance_km <= 900, 5),
                else_=6
            )
        ).order_by("target_days")

        compliance_rows = db.execute(compliance_q).all()
        delivery_compliance = [
            {
                "distance": row.distance,
                "target_days": row.target_days,
                "actual_days": row.actual_days,
                "compliance_pct": SafeNumber.pct(
                    row.delivery_records if row.actual_days <= row.target_days else 0,
                    row.delivery_records
                ),
                "status": "Within Standard" if row.actual_days <= row.target_days else "Above Standard",
            }
            for row in compliance_rows
        ]

        return {
            "pipeline_detailed": pipeline_detailed,
            "monthly_trend": monthly_trend,
            "delivery_compliance": delivery_compliance,
        }

    except Exception as exc:
        logger.exception("Timeline module failed")
        return {
            "pipeline_detailed": {},
            "monthly_trend": [],
            "delivery_compliance": [],
        }


class SafeNumber:
    @staticmethod
    def pct(numerator, denominator):
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)
