# ==========================================================
# FILE: app/services/analytics_service.py
# ==========================================================
# COMPLETE VERSION WITH ALL 14 PRIORITIES IMPLEMENTED

from datetime import date, datetime
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case, desc

from app.models import DeliveryReport


class AnalyticsService:

    def __init__(self, db: Session):
        self.db = db

    # ======================================================
    # AGING CALCULATIONS (Fixed)
    # ======================================================

    @staticmethod
    def calculate_dispatch_age(record):
        """
        Dispatch Age = PGI Date - DN Create Date
        """
        if (
            not record.dn_create_date or
            not record.good_issue_date
        ):
            return 0

        return (
            record.good_issue_date -
            record.dn_create_date
        ).days

    @staticmethod
    def calculate_pod_age(record):
        """
        POD Age = Today - PGI Date (Only for pending POD)
        """
        if not record.good_issue_date:
            return 0

        if record.pod_status == "Received":
            return 0

        return (
            date.today() -
            record.good_issue_date
        ).days

    @staticmethod
    def calculate_delivery_cycle(record):
        """
        Delivery Cycle = POD Date - DN Create Date
        """
        if (
            not record.dn_create_date or
            not record.pod_date
        ):
            return 0

        return (
            record.pod_date -
            record.dn_create_date
        ).days

    # ======================================================
    # PRIORITY 1: DN METRICS (Must Do Next)
    # ======================================================

    def dn_metrics(self, dn_no: str) -> Dict[str, Any]:
        """
        Get comprehensive metrics for a single DN.
        Powers: "DN 6243611264" or "Show DN Details"
        """
        records = (
            self.db.query(DeliveryReport)
            .filter(DeliveryReport.dn_no == dn_no)
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"DN {dn_no} not found"}
        
        main = records[0]
        
        # Calculate aging using corrected methods
        dispatch_age = self.calculate_dispatch_age(main)
        pod_age = self.calculate_pod_age(main)
        delivery_cycle = self.calculate_delivery_cycle(main)
        
        # Build products list
        products = []
        total_qty = 0
        total_value = 0
        
        for r in records:
            product = {
                "material_no": r.material_no,
                "product_name": r.customer_model or r.material_no,
                "quantity": float(r.dn_qty or 0),
                "amount": float(r.dn_amount or 0)
            }
            products.append(product)
            total_qty += product["quantity"]
            total_value += product["amount"]
        
        return {
            "success": True,
            "dn_no": dn_no,
            "dealer": main.customer_name,
            "warehouse": main.warehouse,
            "city": main.ship_to_city,
            "dn_date": main.dn_create_date.isoformat() if main.dn_create_date else None,
            "pgi_date": main.good_issue_date.isoformat() if main.good_issue_date else None,
            "pod_date": main.pod_date.isoformat() if main.pod_date else None,
            "dispatch_age": dispatch_age,
            "pod_age": pod_age,
            "delivery_cycle": delivery_cycle,
            "products": products,
            "total_qty": total_qty,
            "total_value": total_value,
            "status": self._get_dn_status(main),
            "pod_status": "Received" if main.pod_status == "Received" else "Pending"
        }
    
    def _get_dn_status(self, record) -> str:
        """Helper to determine DN status"""
        if record.pgi_status == "Completed" and record.pod_status == "Received":
            return "Delivered and Acknowledged"
        elif record.pgi_status == "Completed" and record.pod_status == "Pending":
            return "Delivered, Awaiting Acknowledgement"
        elif record.pgi_status == "Pending":
            return "Pending Dispatch"
        return record.delivery_status or "Unknown"

    # ======================================================
    # PRIORITY 2: DEALER DASHBOARD METRICS (Combined)
    # ======================================================

    def dealer_dashboard_metrics(self, dealer_name: str) -> Dict[str, Any]:
        """
        Combine all dealer metrics into one comprehensive dashboard.
        Powers: "Show Afzal Dashboard"
        """
        # Get all records for this dealer in one query
        records = (
            self.db.query(DeliveryReport)
            .filter(DeliveryReport.customer_name == dealer_name)
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        # Basic dealer metrics
        dealer_stats = self.dealer_metrics(dealer_name)
        
        # Pending metrics
        pending_stats = self.pending_metrics(dealer_name)
        
        # POD metrics
        pod_stats = self.pod_metrics(dealer_name)
        
        # Product metrics
        product_stats = self.product_metrics(dealer_name)
        
        # Aging summary
        aging = self.aging_summary(dealer_name)
        
        # Critical DNs for this dealer
        critical_dns = self._get_dealer_critical_dns(records)
        
        return {
            "success": True,
            "dealer_name": dealer_name,
            "summary": dealer_stats,
            "pending": pending_stats,
            "pod_pending": pod_stats,
            "products": product_stats,
            "aging": aging,
            "critical_dns": critical_dns,
            "scorecard": self.dealer_score(dealer_name) if dealer_stats.get("total_dns", 0) > 0 else None
        }
    
    def _get_dealer_critical_dns(self, records) -> List[Dict]:
        """Get critical DNs for a dealer (dispatch age > 15 or pod age > 15)"""
        critical = []
        seen_dns = set()
        
        for r in records:
            dn_no = str(r.dn_no)
            if dn_no in seen_dns:
                continue
            seen_dns.add(dn_no)
            
            dispatch_age = self.calculate_dispatch_age(r)
            pod_age = self.calculate_pod_age(r)
            
            if dispatch_age > 15 or pod_age > 15:
                critical.append({
                    "dn_no": dn_no,
                    "dispatch_age": dispatch_age,
                    "pod_age": pod_age,
                    "quantity": float(r.dn_qty or 0),
                    "value": float(r.dn_amount or 0)
                })
        
        return sorted(critical, key=lambda x: max(x["dispatch_age"], x["pod_age"]), reverse=True)

    # ======================================================
    # PRIORITY 3: TOP PRODUCT ANALYTICS
    # ======================================================

    def top_products(self, limit: int = 10) -> Dict[str, List]:
        """
        Get top products by total quantity, pending quantity, and POD pending.
        """
        records = self.db.query(DeliveryReport).all()
        
        product_data = defaultdict(lambda: {
            "total_qty": 0,
            "pending_qty": 0,
            "delivered_qty": 0,
            "pod_pending_qty": 0,
            "value": 0,
            "pending_value": 0,
            "dn_count": set()
        })
        
        for r in records:
            product = r.customer_model or r.material_no or "Unknown"
            qty = float(r.dn_qty or 0)
            amount = float(r.dn_amount or 0)
            
            product_data[product]["total_qty"] += qty
            product_data[product]["value"] += amount
            product_data[product]["dn_count"].add(r.dn_no)
            
            if r.pgi_status == "Completed" and r.pod_status == "Received":
                product_data[product]["delivered_qty"] += qty
            elif r.pgi_status == "Completed" and r.pod_status == "Pending":
                product_data[product]["pod_pending_qty"] += qty
                product_data[product]["pending_qty"] += qty
                product_data[product]["pending_value"] += amount
            elif r.pgi_status != "Completed":
                product_data[product]["pending_qty"] += qty
                product_data[product]["pending_value"] += amount
        
        # Convert sets to counts
        for product in product_data:
            product_data[product]["dn_count"] = len(product_data[product]["dn_count"])
        
        # Sort and return
        all_products = sorted(
            [{"name": p, **data} for p, data in product_data.items()],
            key=lambda x: x["total_qty"],
            reverse=True
        )[:limit]
        
        top_pending = sorted(
            [{"name": p, **data} for p, data in product_data.items() if data["pending_qty"] > 0],
            key=lambda x: x["pending_qty"],
            reverse=True
        )[:limit]
        
        top_pod_pending = sorted(
            [{"name": p, **data} for p, data in product_data.items() if data["pod_pending_qty"] > 0],
            key=lambda x: x["pod_pending_qty"],
            reverse=True
        )[:limit]
        
        return {
            "top_products_by_volume": all_products,
            "top_pending_products": top_pending,
            "top_pod_pending_products": top_pod_pending
        }

    # ======================================================
    # PRIORITY 4: TOP RISK DEALERS
    # ======================================================

    def top_risk_dealers(self, limit: int = 10) -> List[Dict]:
        """
        Identify dealers with highest risk based on pending and POD pending.
        Powers: "Which dealer is most risky?"
        """
        records = self.db.query(DeliveryReport).all()
        
        dealer_risk = defaultdict(lambda: {
            "pending_dns": set(),
            "pod_pending_dns": set(),
            "pending_units": 0,
            "pending_value": 0,
            "pod_pending_units": 0,
            "pod_pending_value": 0,
            "oldest_pod_age": 0,
            "total_dns": set()
        })
        
        for r in records:
            if not r.customer_name:
                continue
                
            dealer = r.customer_name
            dealer_risk[dealer]["total_dns"].add(r.dn_no)
            
            if r.pgi_status != "Completed":
                dealer_risk[dealer]["pending_dns"].add(r.dn_no)
                dealer_risk[dealer]["pending_units"] += float(r.dn_qty or 0)
                dealer_risk[dealer]["pending_value"] += float(r.dn_amount or 0)
            
            if r.pgi_status == "Completed" and r.pod_status == "Pending":
                dealer_risk[dealer]["pod_pending_dns"].add(r.dn_no)
                dealer_risk[dealer]["pod_pending_units"] += float(r.dn_qty or 0)
                dealer_risk[dealer]["pod_pending_value"] += float(r.dn_amount or 0)
                pod_age = self.calculate_pod_age(r)
                if pod_age > dealer_risk[dealer]["oldest_pod_age"]:
                    dealer_risk[dealer]["oldest_pod_age"] = pod_age
        
        # Calculate risk score and prepare results
        results = []
        for dealer, data in dealer_risk.items():
            total_dns = len(data["total_dns"])
            pending_dns = len(data["pending_dns"])
            pod_pending_dns = len(data["pod_pending_dns"])
            
            risk_score = (
                (pending_dns / total_dns * 40 if total_dns > 0 else 0) +
                (pod_pending_dns / total_dns * 30 if total_dns > 0 else 0) +
                (min(data["oldest_pod_age"] / 30, 1) * 30)
            )
            
            results.append({
                "dealer": dealer,
                "pending_dns": pending_dns,
                "pending_units": round(data["pending_units"], 0),
                "pending_value": round(data["pending_value"], 2),
                "pod_pending_dns": pod_pending_dns,
                "pod_pending_units": round(data["pod_pending_units"], 0),
                "pod_pending_value": round(data["pod_pending_value"], 2),
                "oldest_pod_age": data["oldest_pod_age"],
                "risk_score": round(risk_score, 1),
                "total_dns": total_dns
            })
        
        return sorted(results, key=lambda x: x["risk_score"], reverse=True)[:limit]

    # ======================================================
    # PRIORITY 5: TOP RISK WAREHOUSES
    # ======================================================

    def top_risk_warehouses(self, limit: int = 10) -> List[Dict]:
        """
        Identify warehouses with highest pending risk.
        """
        records = self.db.query(DeliveryReport).all()
        
        warehouse_risk = defaultdict(lambda: {
            "pending_dns": set(),
            "pod_pending_dns": set(),
            "pending_units": 0,
            "pending_value": 0,
            "total_dns": set()
        })
        
        for r in records:
            if not r.warehouse:
                continue
                
            warehouse = r.warehouse
            warehouse_risk[warehouse]["total_dns"].add(r.dn_no)
            
            if r.pgi_status != "Completed":
                warehouse_risk[warehouse]["pending_dns"].add(r.dn_no)
                warehouse_risk[warehouse]["pending_units"] += float(r.dn_qty or 0)
                warehouse_risk[warehouse]["pending_value"] += float(r.dn_amount or 0)
            
            if r.pgi_status == "Completed" and r.pod_status == "Pending":
                warehouse_risk[warehouse]["pod_pending_dns"].add(r.dn_no)
        
        results = []
        for warehouse, data in warehouse_risk.items():
            results.append({
                "warehouse": warehouse,
                "pending_dns": len(data["pending_dns"]),
                "pending_units": round(data["pending_units"], 0),
                "pending_value": round(data["pending_value"], 2),
                "pod_pending_dns": len(data["pod_pending_dns"]),
                "total_dns": len(data["total_dns"])
            })
        
        return sorted(results, key=lambda x: x["pending_dns"], reverse=True)[:limit]

    # ======================================================
    # PRIORITY 6: TOP RISK CITIES
    # ======================================================

    def top_risk_cities(self, limit: int = 10) -> List[Dict]:
        """
        Identify cities with highest pending risk.
        """
        records = self.db.query(DeliveryReport).all()
        
        city_risk = defaultdict(lambda: {
            "pending_dns": set(),
            "pod_pending_dns": set(),
            "pending_units": 0,
            "pending_value": 0,
            "total_dns": set()
        })
        
        for r in records:
            if not r.ship_to_city:
                continue
                
            city = r.ship_to_city
            city_risk[city]["total_dns"].add(r.dn_no)
            
            if r.pgi_status != "Completed":
                city_risk[city]["pending_dns"].add(r.dn_no)
                city_risk[city]["pending_units"] += float(r.dn_qty or 0)
                city_risk[city]["pending_value"] += float(r.dn_amount or 0)
            
            if r.pgi_status == "Completed" and r.pod_status == "Pending":
                city_risk[city]["pod_pending_dns"].add(r.dn_no)
        
        results = []
        for city, data in city_risk.items():
            results.append({
                "city": city,
                "pending_dns": len(data["pending_dns"]),
                "pending_units": round(data["pending_units"], 0),
                "pending_value": round(data["pending_value"], 2),
                "pod_pending_dns": len(data["pod_pending_dns"]),
                "total_dns": len(data["total_dns"])
            })
        
        return sorted(results, key=lambda x: x["pending_dns"], reverse=True)[:limit]

    # ======================================================
    # PRIORITY 7: AGING BUCKETS (Critical for Logistics)
    # ======================================================

    def aging_summary(self, dealer_name: str = None) -> Dict[str, Any]:
        """
        Get aging buckets for pending dispatches.
        Critical for logistics reporting.
        """
        query = self.db.query(DeliveryReport)
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name == dealer_name)
        
        records = query.filter(DeliveryReport.pgi_status != "Completed").all()
        
        buckets = {
            "0-3": {"dns": 0, "units": 0, "value": 0},
            "4-7": {"dns": 0, "units": 0, "value": 0},
            "8-15": {"dns": 0, "units": 0, "value": 0},
            "16-30": {"dns": 0, "units": 0, "value": 0},
            "30+": {"dns": 0, "units": 0, "value": 0}
        }
        
        for r in records:
            age = self.calculate_dispatch_age(r)
            qty = float(r.dn_qty or 0)
            amount = float(r.dn_amount or 0)
            
            if age <= 3:
                bucket = "0-3"
            elif age <= 7:
                bucket = "4-7"
            elif age <= 15:
                bucket = "8-15"
            elif age <= 30:
                bucket = "16-30"
            else:
                bucket = "30+"
            
            buckets[bucket]["dns"] += 1
            buckets[bucket]["units"] += qty
            buckets[bucket]["value"] += amount
        
        total_pending = sum(b["dns"] for b in buckets.values())
        
        return {
            "buckets": buckets,
            "total_pending_dns": total_pending,
            "critical_count": buckets["16-30"]["dns"] + buckets["30+"]["dns"],
            "critical_units": buckets["16-30"]["units"] + buckets["30+"]["units"],
            "critical_value": buckets["16-30"]["value"] + buckets["30+"]["value"]
        }

    # ======================================================
    # PRIORITY 8: POD AGING BUCKETS
    # ======================================================

    def pod_aging_summary(self, dealer_name: str = None) -> Dict[str, Any]:
        """
        Get aging buckets for POD pending deliveries.
        """
        query = self.db.query(DeliveryReport)
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name == dealer_name)
        
        records = query.filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        ).all()
        
        buckets = {
            "0-7": {"dns": 0, "units": 0, "value": 0},
            "8-15": {"dns": 0, "units": 0, "value": 0},
            "16-30": {"dns": 0, "units": 0, "value": 0},
            "30+": {"dns": 0, "units": 0, "value": 0}
        }
        
        for r in records:
            age = self.calculate_pod_age(r)
            qty = float(r.dn_qty or 0)
            amount = float(r.dn_amount or 0)
            
            if age <= 7:
                bucket = "0-7"
            elif age <= 15:
                bucket = "8-15"
            elif age <= 30:
                bucket = "16-30"
            else:
                bucket = "30+"
            
            buckets[bucket]["dns"] += 1
            buckets[bucket]["units"] += qty
            buckets[bucket]["value"] += amount
        
        total_pod_pending = sum(b["dns"] for b in buckets.values())
        
        return {
            "buckets": buckets,
            "total_pod_pending_dns": total_pod_pending,
            "urgent_count": buckets["16-30"]["dns"] + buckets["30+"]["dns"],
            "urgent_units": buckets["16-30"]["units"] + buckets["30+"]["units"],
            "urgent_value": buckets["16-30"]["value"] + buckets["30+"]["value"],
            "oldest_age": max((self.calculate_pod_age(r) for r in records), default=0)
        }

    # ======================================================
    # PRIORITY 9: AI CONTEXT LAYER FOR DEALER
    # ======================================================

    def build_dealer_ai_context(self, dealer_name: str) -> Dict[str, Any]:
        """
        Build comprehensive AI context for DeepSeek to consume.
        This is the most important upgrade for AI-powered queries.
        """
        dashboard = self.dealer_dashboard_metrics(dealer_name)
        
        if not dashboard.get("success"):
            return {"success": False, "message": dashboard.get("message", "Dealer not found")}
        
        # Get additional insights
        aging = self.aging_summary(dealer_name)
        pod_aging = self.pod_aging_summary(dealer_name)
        top_products = self.top_products(5)
        
        # Find critical products for this dealer
        dealer_products = dashboard.get("products", {})
        critical_products = [
            {"name": p, "pending_qty": data["pending_qty"]}
            for p, data in dealer_products.items()
            if data.get("pending_qty", 0) > 100
        ]
        
        # Generate AI insights
        ai_insights = self._generate_dealer_ai_insights(dashboard, aging, pod_aging)
        
        return {
            "success": True,
            "dealer_name": dealer_name,
            "dealer_summary": dashboard.get("summary", {}),
            "pending_summary": dashboard.get("pending", {}),
            "pod_summary": dashboard.get("pod_pending", {}),
            "top_products": top_products.get("top_pending_products", [])[:5],
            "aging_summary": aging,
            "pod_aging_summary": pod_aging,
            "critical_dns": dashboard.get("critical_dns", [])[:10],
            "critical_products": critical_products[:5],
            "scorecard": dashboard.get("scorecard"),
            "ai_insights": ai_insights,
            "recommendations": self._generate_dealer_recommendations(dashboard, aging, pod_aging)
        }
    
    def _generate_dealer_ai_insights(self, dashboard, aging, pod_aging) -> Dict:
        """Generate AI insights for dealer"""
        pending = dashboard.get("pending", {})
        pod = dashboard.get("pod_pending", {})
        summary = dashboard.get("summary", {})
        
        findings = []
        risk_level = "LOW"
        
        pending_ratio = pending.get("pending_dns", 0) / max(summary.get("total_dns", 1), 1)
        
        if pending_ratio > 0.5:
            risk_level = "HIGH"
            findings.append(f"High pending ratio: {pending_ratio * 100:.0f}% of deliveries are pending")
        elif pending_ratio > 0.25:
            risk_level = "MEDIUM"
            findings.append(f"Moderate pending ratio: {pending_ratio * 100:.0f}% of deliveries are pending")
        
        if pod.get("pod_pending_dns", 0) > 10:
            risk_level = "HIGH"
            findings.append(f"High POD backlog: {pod.get('pod_pending_dns', 0)} deliveries awaiting acknowledgement")
        
        if aging.get("critical_count", 0) > 5:
            risk_level = "HIGH"
            findings.append(f"Critical aging: {aging.get('critical_count', 0)} DNs older than 15 days")
        
        if pod_aging.get("urgent_count", 0) > 5:
            findings.append(f"Urgent POD pending: {pod_aging.get('urgent_count', 0)} DNs with POD age > 15 days")
        
        return {
            "risk_level": risk_level,
            "findings": findings,
            "summary": f"Risk Level: {risk_level} - {len(findings)} issues identified"
        }
    
    def _generate_dealer_recommendations(self, dashboard, aging, pod_aging) -> List[str]:
        """Generate actionable recommendations for dealer"""
        recommendations = []
        
        pending = dashboard.get("pending", {})
        pod = dashboard.get("pod_pending", {})
        
        if pending.get("pending_dns", 0) > 10:
            recommendations.append("Prioritize clearing pending dispatch backlog")
        
        if pod.get("pod_pending_dns", 0) > 5:
            recommendations.append("Follow up with dealer for pending POD submissions")
        
        if aging.get("critical_count", 0) > 0:
            recommendations.append(f"Escalate {aging.get('critical_count', 0)} critical DNs older than 15 days")
        
        if pod_aging.get("urgent_count", 0) > 0:
            recommendations.append(f"Urgent action required for {pod_aging.get('urgent_count', 0)} long-overdue PODs")
        
        if not recommendations:
            recommendations.append("Dealer performance is satisfactory. Continue monitoring.")
        
        return recommendations

    # ======================================================
    # PRIORITY 10: EXECUTIVE AI CONTEXT
    # ======================================================

    def build_executive_ai_context(self) -> Dict[str, Any]:
        """
        Build comprehensive AI context for executive/CEO queries.
        Powers: "What should I focus on today?"
        """
        # Get all metrics
        executive = self.executive_metrics()
        top_dealers = self.top_risk_dealers(10)
        top_warehouses = self.top_risk_warehouses(10)
        top_cities = self.top_risk_cities(10)
        aging = self.aging_summary()
        pod_aging = self.pod_aging_summary()
        top_products_data = self.top_products(10)
        
        # Get critical DNs (oldest pending)
        critical_dns = self._get_global_critical_dns(20)
        
        # Generate executive AI insights
        executive_insights = self._generate_executive_ai_insights(
            executive, top_dealers, aging, pod_aging
        )
        
        return {
            "success": True,
            "overview": executive,
            "top_risk_dealers": top_dealers[:5],
            "top_risk_warehouses": top_warehouses[:5],
            "top_risk_cities": top_cities[:5],
            "critical_dns": critical_dns,
            "aging_summary": aging,
            "pod_aging_summary": pod_aging,
            "top_products": top_products_data.get("top_products_by_volume", [])[:5],
            "top_pending_products": top_products_data.get("top_pending_products", [])[:5],
            "executive_insights": executive_insights,
            "action_items": self._generate_executive_action_items(
                top_dealers, top_warehouses, top_cities, aging, pod_aging
            )
        }
    
    def _get_global_critical_dns(self, limit: int = 20) -> List[Dict]:
        """Get globally critical DNs (oldest pending and POD pending)"""
        records = self.db.query(DeliveryReport).all()
        
        # Deduplicate by DN
        dn_map = {}
        for r in records:
            dn_no = str(r.dn_no)
            if dn_no not in dn_map:
                dn_map[dn_no] = r
        
        critical = []
        for dn_no, r in dn_map.items():
            dispatch_age = self.calculate_dispatch_age(r)
            pod_age = self.calculate_pod_age(r)
            
            if dispatch_age > 15 or pod_age > 15:
                critical.append({
                    "dn_no": dn_no,
                    "dealer": r.customer_name,
                    "dispatch_age": dispatch_age,
                    "pod_age": pod_age,
                    "quantity": float(r.dn_qty or 0),
                    "value": float(r.dn_amount or 0)
                })
        
        return sorted(critical, key=lambda x: max(x["dispatch_age"], x["pod_age"]), reverse=True)[:limit]
    
    def _generate_executive_ai_insights(self, executive, top_dealers, aging, pod_aging) -> Dict:
        """Generate AI insights for executive"""
        findings = []
        recommendations = []
        
        total_dns = executive.get("total_dns", 0)
        pending_count = aging.get("total_pending_dns", 0)
        pod_pending = pod_aging.get("total_pod_pending_dns", 0)
        
        pending_rate = (pending_count / total_dns * 100) if total_dns > 0 else 0
        
        if pending_rate > 30:
            findings.append(f"Critical: {pending_rate:.0f}% of deliveries are pending dispatch")
            recommendations.append("Immediate escalation required for warehouse operations")
        elif pending_rate > 15:
            findings.append(f"Warning: {pending_rate:.0f}% of deliveries are pending dispatch")
            recommendations.append("Review warehouse efficiency and capacity")
        
        if pod_pending > 50:
            findings.append(f"Critical POD backlog: {pod_pending} deliveries awaiting acknowledgement")
            recommendations.append("Dealer follow-up campaign needed immediately")
        
        if top_dealers:
            worst_dealer = top_dealers[0]
            findings.append(f"Highest risk dealer: {worst_dealer['dealer']} with {worst_dealer['pending_dns']} pending DNs")
            recommendations.append(f"Schedule urgent review with {worst_dealer['dealer']}")
        
        if aging.get("critical_count", 0) > 20:
            findings.append(f"Critical aging alert: {aging.get('critical_count', 0)} DNs older than 15 days")
            recommendations.append("Prioritize clearing aged backlog")
        
        return {
            "findings": findings[:5],
            "recommendations": recommendations[:5],
            "summary": f"Executive Summary: {len(findings)} critical issues identified",
            "priority_score": min(100, int(pending_rate * 2 + pod_pending / 5))
        }
    
    def _generate_executive_action_items(self, top_dealers, top_warehouses, top_cities, aging, pod_aging) -> List[Dict]:
        """Generate prioritized action items for executive"""
        actions = []
        
        if top_dealers:
            actions.append({
                "priority": "HIGH",
                "title": "Escalate Top Risk Dealer",
                "description": f"Contact {top_dealers[0]['dealer']} - {top_dealers[0]['pending_dns']} pending DNs",
                "impact": f"Value at risk: Rs {top_dealers[0]['pending_value']:,.2f}"
            })
        
        if top_warehouses:
            actions.append({
                "priority": "HIGH",
                "title": "Warehouse Bottleneck Alert",
                "description": f"{top_warehouses[0]['warehouse']} has {top_warehouses[0]['pending_dns']} pending dispatches",
                "impact": f"Units stuck: {top_warehouses[0]['pending_units']:,.0f}"
            })
        
        if aging.get("critical_count", 0) > 10:
            actions.append({
                "priority": "HIGH",
                "title": "Clear Aged Backlog",
                "description": f"{aging.get('critical_count', 0)} DNs older than 15 days",
                "impact": f"Value: Rs {aging.get('critical_value', 0):,.2f}"
            })
        
        if pod_aging.get("urgent_count", 0) > 10:
            actions.append({
                "priority": "MEDIUM",
                "title": "POD Recovery Campaign",
                "description": f"{pod_aging.get('urgent_count', 0)} DNs with POD age > 15 days",
                "impact": f"Value: Rs {pod_aging.get('urgent_value', 0):,.2f}"
            })
        
        return actions

    # ======================================================
    # PRIORITY 11: DEALER SCORING (Machine Learning Ready)
    # ======================================================

    def dealer_score(self, dealer_name: str) -> Dict[str, Any]:
        """
        Calculate comprehensive dealer score (0-100).
        Based on: POD Compliance, Pending %, Delivery Performance, Aging, Volume
        """
        records = (
            self.db.query(DeliveryReport)
            .filter(DeliveryReport.customer_name == dealer_name)
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        total_dns = len(set(r.dn_no for r in records if r.dn_no))
        if total_dns == 0:
            return {"score": 0, "category": "Watchlist"}
        
        # Calculate metrics
        delivered_dns = len(set(r.dn_no for r in records 
                               if r.pgi_status == "Completed" and r.pod_status == "Received"))
        pending_dns = len(set(r.dn_no for r in records if r.pgi_status != "Completed"))
        pod_pending_dns = len(set(r.dn_no for r in records 
                                 if r.pgi_status == "Completed" and r.pod_status == "Pending"))
        
        # 1. POD Compliance Score (40% weight)
        total_delivered = delivered_dns + pod_pending_dns
        pod_compliance = (delivered_dns / max(total_delivered, 1)) * 100
        
        # 2. Pending % Score (30% weight)
        pending_ratio = pending_dns / total_dns
        pending_score = max(0, 100 - (pending_ratio * 100))
        
        # 3. Delivery Performance (15% weight)
        delivery_performance = (delivered_dns / total_dns) * 100
        
        # 4. Aging Score (10% weight)
        ages = [self.calculate_dispatch_age(r) for r in records if r.pgi_status != "Completed"]
        avg_age = sum(ages) / max(len(ages), 1)
        if avg_age <= 3:
            aging_score = 100
        elif avg_age <= 7:
            aging_score = 80
        elif avg_age <= 15:
            aging_score = 50
        else:
            aging_score = 20
        
        # 5. Volume Score (5% weight)
        volume_score = min(100, (total_dns / 100) * 100)
        
        # Calculate final weighted score
        final_score = (
            pod_compliance * 0.40 +
            pending_score * 0.30 +
            delivery_performance * 0.15 +
            aging_score * 0.10 +
            volume_score * 0.05
        )
        
        # Determine category
        if final_score >= 90:
            category = "Platinum"
            icon = "💎"
        elif final_score >= 80:
            category = "Gold"
            icon = "🥇"
        elif final_score >= 70:
            category = "Silver"
            icon = "🥈"
        else:
            category = "Watchlist"
            icon = "⚠️"
        
        return {
            "success": True,
            "dealer": dealer_name,
            "score": round(final_score, 1),
            "category": category,
            "icon": icon,
            "components": {
                "pod_compliance": round(pod_compliance, 1),
                "pending_score": round(pending_score, 1),
                "delivery_performance": round(delivery_performance, 1),
                "aging_score": round(aging_score, 1),
                "volume_score": round(volume_score, 1)
            },
            "metrics": {
                "total_dns": total_dns,
                "delivered_dns": delivered_dns,
                "pending_dns": pending_dns,
                "pod_pending_dns": pod_pending_dns,
                "avg_dispatch_age": round(avg_age, 1)
            }
        }

    # ======================================================
    # BASE METRICS (Existing, Optimized)
    # ======================================================

    def executive_metrics(self) -> Dict[str, Any]:
        """Get executive-level metrics using optimized queries"""
        # Use SQL aggregation instead of loading all records
        result = self.db.query(
            func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).label("total_units"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(func.distinct(DeliveryReport.customer_name)).label("total_dealers"),
            func.count(func.distinct(DeliveryReport.ship_to_city)).label("total_cities")
        ).first()
        
        return {
            "total_dns": result.total_dns or 0,
            "total_units": float(result.total_units or 0),
            "total_value": float(result.total_value or 0),
            "total_dealers": result.total_dealers or 0,
            "total_cities": result.total_cities or 0
        }

    def dealer_metrics(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer metrics using optimized aggregation"""
        result = self.db.query(
            func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).label("total_units"),
            func.sum(DeliveryReport.dn_amount).label("total_value")
        ).filter(DeliveryReport.customer_name == dealer_name).first()
        
        return {
            "dealer": dealer_name,
            "total_dns": result.total_dns or 0,
            "total_units": float(result.total_units or 0),
            "total_value": float(result.total_value or 0)
        }

    def pending_metrics(self, dealer_name: str = None) -> Dict[str, Any]:
        """Get pending metrics using optimized aggregation"""
        query = self.db.query(
            func.count(func.distinct(DeliveryReport.dn_no)).label("pending_dns"),
            func.sum(DeliveryReport.dn_qty).label("pending_units"),
            func.sum(DeliveryReport.dn_amount).label("pending_value")
        ).filter(DeliveryReport.pgi_status != "Completed")
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name == dealer_name)
        
        result = query.first()
        
        return {
            "pending_dns": result.pending_dns or 0,
            "pending_units": float(result.pending_units or 0),
            "pending_value": float(result.pending_value or 0)
        }

    def pod_metrics(self, dealer_name: str = None) -> Dict[str, Any]:
        """Get POD pending metrics using optimized aggregation"""
        query = self.db.query(
            func.count(func.distinct(DeliveryReport.dn_no)).label("pod_pending_dns"),
            func.sum(DeliveryReport.dn_qty).label("pod_pending_units"),
            func.sum(DeliveryReport.dn_amount).label("pod_pending_value")
        ).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name == dealer_name)
        
        result = query.first()
        
        # Get oldest POD age efficiently
        oldest_query = self.db.query(
            func.max(
                func.date_part('day', 
                    func.age(func.current_date(), DeliveryReport.good_issue_date))
            )
        ).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        )
        
        if dealer_name:
            oldest_query = oldest_query.filter(DeliveryReport.customer_name == dealer_name)
        
        oldest_pod_age = oldest_query.scalar() or 0
        
        return {
            "pod_pending_dns": result.pod_pending_dns or 0,
            "pod_pending_units": float(result.pod_pending_units or 0),
            "pod_pending_value": float(result.pod_pending_value or 0),
            "oldest_pod_age": int(oldest_pod_age)
        }

    def product_metrics(self, dealer_name: str = None) -> Dict[str, Dict]:
        """Get product metrics with SQL aggregation"""
        query = self.db.query(
            DeliveryReport.material_no,
            DeliveryReport.customer_model,
            func.sum(DeliveryReport.dn_qty).label("total_qty"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                else_=0
            )).label("pending_qty"),
            func.sum(case(
                (and_(DeliveryReport.pgi_status == "Completed", DeliveryReport.pod_status == "Received"),
                 DeliveryReport.dn_qty),
                else_=0
            )).label("delivered_qty")
        ).group_by(DeliveryReport.material_no, DeliveryReport.customer_model)
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name == dealer_name)
        
        results = query.all()
        
        products = {}
        for r in results:
            product_name = r.customer_model or r.material_no or "Unknown"
            products[product_name] = {
                "total_qty": float(r.total_qty or 0),
                "total_value": float(r.total_value or 0),
                "pending_qty": float(r.pending_qty or 0),
                "delivered_qty": float(r.delivered_qty or 0)
            }
        
        return products

    def warehouse_metrics(self) -> List[Tuple]:
        """Get warehouse metrics using SQL aggregation"""
        return (
            self.db.query(
                DeliveryReport.warehouse,
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_value")
            )
            .group_by(DeliveryReport.warehouse)
            .all()
        )

    def city_metrics(self) -> List[Tuple]:
        """Get city metrics using SQL aggregation"""
        return (
            self.db.query(
                DeliveryReport.ship_to_city,
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_value")
            )
            .group_by(DeliveryReport.ship_to_city)
            .all()
        )

    # ======================================================
    # PRIORITY 12-14: FORECASTING, SEMANTIC SEARCH, OPTIMIZATION
    # (Structures prepared for future implementation)
    # ======================================================

    def forecast_pending_dns(self, days: int = 30) -> Dict[str, Any]:
        """
        Forecast pending DNS for next N days.
        Placeholder for Prophet/statsmodels integration.
        """
        # TODO: Implement with Prophet when ready
        return {
            "status": "Coming Soon",
            "message": "Forecasting feature will be available in next release",
            "forecast_days": days
        }

    def forecast_pod_backlog(self, days: int = 30) -> Dict[str, Any]:
        """
        Forecast POD backlog growth.
        Placeholder for time series forecasting.
        """
        return {
            "status": "Coming Soon",
            "message": "POD backlog forecasting coming soon",
            "forecast_days": days
        }

    def semantic_search(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Semantic search for similar dealers/products.
        Placeholder for FAISS/FlagEmbedding integration.
        """
        # TODO: Implement with FAISS and embeddings
        return [
            {
                "status": "Coming Soon",
                "message": "Semantic search will be available with vector embeddings",
                "query": query
            }
        ]
