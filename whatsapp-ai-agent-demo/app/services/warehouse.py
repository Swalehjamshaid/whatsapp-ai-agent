# ============================================================
# FILE: app/services/warehouse.py
# VERSION: 30.0 – WAREHOUSE RANKINGS & PERFORMANCE
# ============================================================
# RESPONSIBILITIES:
#   - Warehouse ranking with health, risk, trend
#   - Top dealers, products, delayed cities, pending warehouses
#   - Division performance
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, and_
from sqlalchemy.orm import Session

from app.models import DeliveryReport

logger = logging.getLogger(__name__)


def fetch_warehouse_data(db: Session, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Fetch all warehouse-related aggregates and rankings.
    """
    try:
        # 1. Warehouse summary (group by warehouse)
        warehouse_q = select(
            DeliveryReport.warehouse.label("warehouse"),
            func.count(DeliveryReport.dn_no.distinct()).label("dns"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("revenue"),
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
        ).group_by(DeliveryReport.warehouse).order_by(func.sum(DeliveryReport.dn_qty).desc())

        warehouse_rows = db.execute(warehouse_q).all()

        # 2. Compute per-warehouse health, risk, trend (using simple trend based on avg cycle days)
        # We'll compute trend by comparing current avg cycle days to a threshold – in production, we'd fetch warehouse daily trend.
        warehouse_ranking = []
        for row in warehouse_rows:
            total_units = row.units or 0
            delivered_units = row.delivered_units or 0
            pgi_units = row.pgi_units or 0
            pending_units = max(0, total_units - delivered_units)
            pgi_pct = SafeNumber.pct(pgi_units, total_units)
            delivery_pct = SafeNumber.pct(delivered_units, pgi_units)
            health_score = round(
                0.25 * pgi_pct +
                0.35 * delivery_pct +
                0.20 * SafeNumber.pct(row.delivered_units, row.delivered_units) +
                0.10 * (100 - SafeNumber.pct(pending_units, total_units)) +
                0.10 * max(0, 100 - (row.avg_cycle_days * 10)),
                1
            )
            # Risk classification
            if health_score >= 90:
                risk = "low"
                status = "Green"
                emoji = "🟢"
            elif health_score >= 80:
                risk = "medium"
                status = "Yellow"
                emoji = "🟡"
            elif health_score >= 70:
                risk = "high"
                status = "Orange"
                emoji = "🟠"
            else:
                risk = "critical"
                status = "Red"
                emoji = "🔴"

            # Simple trend: compare avg cycle days to previous period? We'll use a static "▬" for now.
            trend = "▬ Stable"
            warehouse_ranking.append({
                "warehouse": row.warehouse or "Unassigned",
                "dns": row.dns,
                "units": total_units,
                "revenue": row.revenue,
                "pgi_pct": pgi_pct,
                "delivery_pct": delivery_pct,
                "pod_pct": SafeNumber.pct(row.delivered_units, row.delivered_units),  # POD %
                "avg_transit_days": row.avg_transit_days,
                "avg_cycle_days": row.avg_cycle_days,
                "pending_units": pending_units,
                "pending_dns": max(0, row.pgi_dn - row.delivered_dn),
                "health_score": health_score,
                "status": status,
                "risk": emoji,
                "risk_level": risk,
                "trend": trend,
                "ai_insight": "Warehouse performance monitored.",
            })

        # 3. Dealer summary
        dealer_q = select(
            DeliveryReport.dealer_code.label("dealer_code"),
            func.coalesce(func.max(DeliveryReport.customer_name), DeliveryReport.dealer_code).label("dealer_name"),
            func.count(DeliveryReport.dn_no.distinct()).label("dns"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("revenue"),
        ).group_by(DeliveryReport.dealer_code).order_by(func.sum(DeliveryReport.dn_amount).desc()).limit(5)
        dealers = db.execute(dealer_q).all()
        top_dealers = [
            {"dealer": d.dealer_name or d.dealer_code or "Unassigned", "dns": d.dns, "units": d.units, "revenue": d.revenue}
            for d in dealers
        ]

        # 4. Product summary
        product_q = select(
            DeliveryReport.material_no.label("sku"),
            func.coalesce(func.max(DeliveryReport.customer_model), DeliveryReport.material_no).label("product_name"),
            func.count(DeliveryReport.dn_no.distinct()).label("delivery_notes"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("revenue"),
        ).group_by(DeliveryReport.material_no).order_by(func.sum(DeliveryReport.dn_qty).desc()).limit(5)
        products = db.execute(product_q).all()
        top_products = [
            {"product": p.product_name or p.sku or "Unassigned", "units": p.units, "revenue": p.revenue, "delivery_notes": p.delivery_notes}
            for p in products
        ]

        # 5. City summary with avg transit days
        city_q = select(
            DeliveryReport.ship_to_city.label("city"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.count(DeliveryReport.dn_no.distinct().filter(DeliveryReport.pod_date.is_(None))).label("pending_dns"),
            func.coalesce(func.sum(DeliveryReport.dn_qty).filter(DeliveryReport.pod_date.is_(None)), 0).label("pending_units"),
            func.coalesce(
                func.avg(
                    func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400.0
                ).filter(
                    DeliveryReport.pod_date.isnot(None),
                    DeliveryReport.good_issue_date.isnot(None)
                ),
                0
            ).label("avg_delivery_days"),
        ).group_by(DeliveryReport.ship_to_city).order_by(func.sum(DeliveryReport.dn_qty).desc())
        cities = db.execute(city_q).all()
        top_delayed = sorted(
            [
                {
                    "city": c.city or "Unassigned",
                    "avg_delivery_days": c.avg_delivery_days,
                    "pending_units": c.pending_units,
                    "status": "Critical" if c.avg_delivery_days > 4 else "High" if c.avg_delivery_days > 2 else "Within Standard"
                }
                for c in cities
            ],
            key=lambda x: x["avg_delivery_days"],
            reverse=True
        )[:5]

        # 6. Top pending warehouses
        pending_wh = sorted(
            [
                {
                    "warehouse": w["warehouse"],
                    "pending_dns": w["pending_dns"],
                    "pending_units": w["pending_units"],
                }
                for w in warehouse_ranking
            ],
            key=lambda x: x["pending_units"],
            reverse=True
        )[:5]

        # 7. Division performance
        div_q = select(
            DeliveryReport.division.label("division"),
            func.count(DeliveryReport.dn_no.distinct()).label("dns"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("revenue"),
        ).group_by(DeliveryReport.division).order_by(func.sum(DeliveryReport.dn_amount).desc())
        divisions = db.execute(div_q).all()
        total_rev = sum(d.revenue for d in divisions)
        division_performance = [
            {
                "division": d.division or "Unassigned",
                "dns": d.dns,
                "units": d.units,
                "revenue": d.revenue,
                "percentage": SafeNumber.pct(d.revenue, total_rev),
            }
            for d in divisions
        ]

        return {
            "warehouse_ranking": warehouse_ranking,
            "top_dealers": top_dealers,
            "top_products": top_products,
            "top_delayed_cities": top_delayed,
            "top_pending_warehouses": pending_wh,
            "division_performance": division_performance,
        }

    except Exception as exc:
        logger.exception("Warehouse module failed")
        return {
            "warehouse_ranking": [],
            "top_dealers": [],
            "top_products": [],
            "top_delayed_cities": [],
            "top_pending_warehouses": [],
            "division_performance": [],
        }


class SafeNumber:
    @staticmethod
    def pct(numerator, denominator):
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)
