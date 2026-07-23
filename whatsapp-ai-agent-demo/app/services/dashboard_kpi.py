# ============================================================
# FILE: app/services/dashboard_kpi.py
# VERSION: 30.0 – KPI & METRICS MODULE
# ============================================================
# RESPONSIBILITIES:
#   - Executive KPI cards
#   - Metadata (record count, warehouse count, etc.)
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DeliveryReport  # assumes this model exists

logger = logging.getLogger(__name__)


def fetch_kpi_data(db: Session, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Query PostgreSQL for all executive KPI aggregates.
    Return a dict with 'cards' and 'metadata'.
    """
    try:
        # Base query – we can apply filters if provided (e.g., warehouse, date range)
        query = select(
            func.count(DeliveryReport.dn_no.distinct()).label("total_dn"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
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
            ).label("pod_dn"),  # same as delivered for simplicity
            func.coalesce(func.sum(DeliveryReport.dn_qty).filter(
                DeliveryReport.pod_date.isnot(None)
            ), 0).label("pod_units"),
            func.coalesce(
                func.avg(
                    func.extract('epoch', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date) / 86400.0
                ).filter(
                    DeliveryReport.good_issue_date.isnot(None),
                    DeliveryReport.dn_create_date.isnot(None)
                ),
                0
            ).label("avg_pgi_days"),
            func.coalesce(
                func.avg(
                    func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400.0
                ).filter(
                    DeliveryReport.pod_date.isnot(None),
                    DeliveryReport.good_issue_date.isnot(None)
                ),
                0
            ).label("avg_transit_days"),
            func.coalesce(
                func.avg(
                    func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.dn_create_date) / 86400.0
                ).filter(
                    DeliveryReport.pod_date.isnot(None),
                    DeliveryReport.dn_create_date.isnot(None)
                ),
                0
            ).label("avg_cycle_days"),
        )

        # Apply filters if any (example: warehouse)
        if filters and filters.get("warehouse"):
            query = query.where(DeliveryReport.warehouse == filters["warehouse"])

        result = db.execute(query).first()
        if not result:
            return {"cards": {}, "metadata": {}}

        # Derive computed KPIs
        total_units = result.total_units
        pgi_units = result.pgi_units
        delivered_units = result.delivered_units
        pod_units = result.pod_units
        pending_units = max(0.0, total_units - delivered_units)
        pending_dn = max(0, result.pgi_dn - result.delivered_dn)

        pgi_pct = SafeNumber.pct(pgi_units, total_units)
        delivery_pct = SafeNumber.pct(delivered_units, pgi_units)
        pod_pct = SafeNumber.pct(pod_units, delivered_units)

        # Health Score (simplified – could be moved to business rule engine)
        health_score = round(
            0.25 * pgi_pct +
            0.35 * delivery_pct +
            0.20 * pod_pct +
            0.10 * (100 - SafeNumber.pct(pending_units, total_units)) +
            0.10 * max(0, 100 - (result.avg_cycle_days * 10)),
            1
        )

        cards = {
            "total_dn": {"value": result.total_dn, "label": "Total DNs"},
            "total_units": {"value": total_units, "label": "Total Units"},
            "total_value": {"value": result.total_revenue, "label": "Total Revenue"},
            "pgi_achievement": {"value": pgi_pct, "label": "PGI %"},
            "delivery_achievement": {"value": delivery_pct, "label": "Delivery %"},
            "pod_achievement": {"value": pod_pct, "label": "POD %"},
            "pending_dn": {"value": pending_dn, "label": "Pending DNs"},
            "pending_units": {"value": pending_units, "label": "Pending Units"},
            "health_score": {"value": health_score, "label": "Health Score"},
            "avg_pgi_days": {"value": result.avg_pgi_days, "label": "Avg PGI Days"},
            "avg_transit_days": {"value": result.avg_transit_days, "label": "Avg Delivery Days"},
            "avg_cycle_days": {"value": result.avg_cycle_days, "label": "Avg Cycle Days"},
        }

        # Metadata
        warehouse_count = db.query(func.count(DeliveryReport.warehouse.distinct())).scalar() or 0
        record_count = db.query(func.count(DeliveryReport.id)).scalar() or 0

        metadata = {
            "warehouse_count": warehouse_count,
            "record_count": record_count,
            "version": "30.0",
            "timestamp": datetime.utcnow().isoformat(),
        }

        return {"cards": cards, "metadata": metadata}

    except Exception as exc:
        logger.exception("KPI module failed")
        return {"cards": {}, "metadata": {}}


# Helper safe number functions (moved here for clarity)
class SafeNumber:
    @staticmethod
    def pct(numerator, denominator):
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)
