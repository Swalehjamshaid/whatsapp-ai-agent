# ============================================================
# FILE: app/services/dashboard_ai.py
# VERSION: 30.0 – AI INSIGHTS & ALERTS MODULE
# ============================================================
# RESPONSIBILITIES:
#   - Executive summary text
#   - Critical alerts
#   - Director recommendations with priorities
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DeliveryReport

logger = logging.getLogger(__name__)


def fetch_ai_data(db: Session, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Generate AI insights: alerts, recommendations, and executive summary.
    """
    try:
        # 1. Fetch warehouse summaries to base alerts on
        warehouse_q = select(
            DeliveryReport.warehouse.label("warehouse"),
            func.count(DeliveryReport.dn_no.distinct()).label("dns"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.coalesce(func.sum(DeliveryReport.dn_qty).filter(
                DeliveryReport.pod_date.isnot(None)
            ), 0).label("delivered_units"),
            func.coalesce(func.sum(DeliveryReport.dn_qty).filter(
                DeliveryReport.pod_date.is_(None)
            ), 0).label("pending_units"),
            func.coalesce(
                func.avg(
                    func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400.0
                ).filter(
                    DeliveryReport.pod_date.isnot(None),
                    DeliveryReport.good_issue_date.isnot(None)
                ),
                0
            ).label("avg_transit_days"),
        ).group_by(DeliveryReport.warehouse)

        warehouse_rows = db.execute(warehouse_q).all()

        # 2. Compute metrics for alerts
        alerts = []
        for row in warehouse_rows:
            warehouse = row.warehouse or "Unassigned"
            pending = row.pending_units
            delivery_pct = SafeNumber.pct(row.delivered_units, row.units)
            transit = row.avg_transit_days

            if pending > 10000:
                alerts.append({
                    "source": warehouse,
                    "severity": "CRITICAL",
                    "category": "Pending Units",
                    "message": f"{pending:,.0f} pending units exceed threshold.",
                    "urgency": 100 + pending / 1000,
                })
            if delivery_pct < 70:
                alerts.append({
                    "source": warehouse,
                    "severity": "CRITICAL",
                    "category": "Delivery Achievement",
                    "message": f"Delivery achievement is {delivery_pct:.1f}%.",
                    "urgency": 95 + (70 - delivery_pct),
                })
            if transit > 3:
                alerts.append({
                    "source": warehouse,
                    "severity": "HIGH" if transit > 4 else "WARNING",
                    "category": "Delivery Days",
                    "message": f"Average transit is {transit:.1f} days.",
                    "urgency": 60 + transit,
                })

        # Also national alerts if pending > threshold
        total_pending = db.query(func.sum(DeliveryReport.dn_qty).filter(DeliveryReport.pod_date.is_(None))).scalar() or 0
        if total_pending > 10000:
            alerts.append({
                "source": "National",
                "severity": "CRITICAL",
                "category": "Pending Units",
                "message": f"Total pending units: {total_pending:,.0f}.",
                "urgency": 100 + total_pending / 1000,
            })

        # Sort and limit alerts
        alerts.sort(key=lambda x: x.get("urgency", 0), reverse=True)
        alerts = alerts[:10]

        # 3. Recommendations (prioritised)
        recommendations = []
        for row in warehouse_rows:
            warehouse = row.warehouse or "Unassigned"
            pending = row.pending_units
            delivery_pct = SafeNumber.pct(row.delivered_units, row.units)

            if pending > 10000:
                recommendations.append({
                    "warehouse": warehouse,
                    "priority": "Priority 1",
                    "problem": "Pending inventory backlog",
                    "recommendation": f"Clear {pending:,.0f} pending units at {warehouse}.",
                    "expected_improvement": "Improves pending efficiency.",
                    "target_kpi": "Pending Units",
                })
            elif delivery_pct < 70:
                recommendations.append({
                    "warehouse": warehouse,
                    "priority": "Priority 2",
                    "problem": "Low delivery achievement",
                    "recommendation": f"Review open shipments at {warehouse}.",
                    "expected_improvement": "Improves Delivery %.",
                    "target_kpi": "Delivery Achievement",
                })
        recommendations.sort(key=lambda x: 0 if "Priority 1" in x["priority"] else 1)
        recommendations = recommendations[:5]

        # 4. Executive summary
        total_dn = db.query(func.count(DeliveryReport.dn_no.distinct())).scalar() or 0
        if total_dn == 0:
            executive_summary_text = "No delivery reports available."
        else:
            # Compute overall health score from all records
            pgi_units = db.query(func.coalesce(func.sum(DeliveryReport.dn_qty).filter(
                DeliveryReport.good_issue_date.isnot(None)
            ), 0)).scalar() or 0
            delivered_units = db.query(func.coalesce(func.sum(DeliveryReport.dn_qty).filter(
                DeliveryReport.pod_date.isnot(None)
            ), 0)).scalar() or 0
            total_units = db.query(func.coalesce(func.sum(DeliveryReport.dn_qty), 0)).scalar() or 0
            pgi_pct = SafeNumber.pct(pgi_units, total_units)
            delivery_pct = SafeNumber.pct(delivered_units, pgi_units)
            pending_units = total_units - delivered_units
            health = round(
                0.25 * pgi_pct +
                0.35 * delivery_pct +
                0.20 * 100 +  # assume pod = delivered
                0.10 * (100 - SafeNumber.pct(pending_units, total_units)) +
                0.10 * 80,  # assume cycle efficiency
                1
            )
            executive_summary_text = (
                f"Overall logistics health is {health:.1f}%. "
                f"PGI {pgi_pct:.1f}%, Delivery {delivery_pct:.1f}%. "
                f"Pending backlog: {pending_units:,.0f} units."
            )

        return {
            "alerts": alerts,
            "recommendations": recommendations,
            "executive_summary_text": executive_summary_text,
        }

    except Exception as exc:
        logger.exception("AI module failed")
        return {
            "alerts": [],
            "recommendations": [],
            "executive_summary_text": "Unable to generate AI insights at this time.",
        }


class SafeNumber:
    @staticmethod
    def pct(numerator, denominator):
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)
