# ==========================================================
# FILE: app/services/analytics_service.py (FINAL IMPROVED VERSION)
# ==========================================================
# REMAINING FIXES:
# - Reduced .all() usage in critical methods
# - Added dealer suggestions for ambiguous matches
# - Normalized status in SQL queries (uses Python filtering for now)
# - Prepared for status normalization at import time

from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
from functools import lru_cache
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case, desc, distinct, or_
from loguru import logger

from app.models import DeliveryReport


class AnalyticsService:

    def __init__(self, db: Session):
        self.db = db
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes cache

    # ======================================================
    # NORMALIZATION HELPERS (For consistency)
    # ======================================================

    @staticmethod
    def is_pod_received(status: Optional[str]) -> bool:
        """Check if POD status indicates received (normalized check)"""
        if not status:
            return False
        status_lower = status.lower().strip()
        return status_lower in ["received", "received ", "pod received", "done", "completed", "yes"]

    @staticmethod
    def is_pod_pending(status: Optional[str]) -> bool:
        """Check if POD status indicates pending (normalized check)"""
        if not status:
            return True
        status_lower = status.lower().strip()
        return status_lower not in ["received", "received ", "pod received", "done", "completed", "yes"]

    @staticmethod
    def is_pgi_completed(status: Optional[str]) -> bool:
        """Check if PGI status indicates completed (normalized check)"""
        if not status:
            return False
        status_lower = status.lower().strip()
        return status_lower in ["completed", "done", "yes", "dispatched"]

    @staticmethod
    def calculate_dispatch_age(record):
        if not record.dn_create_date or not record.good_issue_date:
            return 0
        return (record.good_issue_date - record.dn_create_date).days

    @staticmethod
    def calculate_pod_age(record):
        if not record.good_issue_date:
            return 0
        if AnalyticsService.is_pod_received(record.pod_status):
            return 0
        return (date.today() - record.good_issue_date).days

    # ======================================================
    # ISSUE 4: DEALER DASHBOARD WITH AMBIGUOUS MATCH HANDLING
    # ======================================================

    def find_dealers(self, dealer_name: str, limit: int = 10) -> List[str]:
        """Find dealers matching the given name (supports partial match)"""
        results = self.db.query(DeliveryReport.customer_name).filter(
            DeliveryReport.customer_name.isnot(None),
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).distinct().limit(limit).all()
        
        return [r[0] for r in results if r[0]]

    def dealer_dashboard_metrics(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer dashboard with ambiguous match handling.
        ISSUE 4 FIX: Returns suggestions if multiple dealers match.
        """
        # Find all matching dealers
        matching_dealers = self.find_dealers(dealer_name, limit=5)
        
        if not matching_dealers:
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        # If multiple matches, return suggestions
        if len(matching_dealers) > 1:
            return {
                "success": False,
                "fuzzy": True,
                "message": f"Multiple dealers found matching '{dealer_name}'",
                "matches": matching_dealers,
                "summary": f"🔍 Multiple dealers found. Did you mean:\n" + "\n".join([f"• {d}" for d in matching_dealers])
            }
        
        # Exact single match
        exact_dealer_name = matching_dealers[0]
        
        # Use SQL aggregation for metrics (no .all() for large datasets)
        metrics = self.db.query(
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).label("total_units"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                else_=0
            )).label("pending_units"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                else_=0
            )).label("pending_value"),
            func.count(distinct(case(
                (and_(DeliveryReport.pgi_status == "Completed", DeliveryReport.pod_status == "Pending"),
                 DeliveryReport.dn_no),
                else_=None
            ))).label("pod_pending_dns")
        ).filter(
            DeliveryReport.customer_name == exact_dealer_name
        ).first()
        
        total_dns = metrics.total_dns or 0
        
        # Get aging summary (uses limited query)
        aging = self.aging_summary(exact_dealer_name)
        pod_aging = self.pod_aging_summary(exact_dealer_name)
        
        # Get critical DNs (limited to 10)
        critical_dns = self._get_dealer_critical_dns(exact_dealer_name, limit=10)
        
        formatted_message = self._format_dealer_dashboard(
            exact_dealer_name,
            total_dns,
            float(metrics.total_value or 0),
            metrics.pending_dns or 0,
            float(metrics.pending_value or 0),
            metrics.pod_pending_dns or 0,
            aging,
            pod_aging
        )
        
        return {
            "success": True,
            "dealer_name": exact_dealer_name,
            "total_dns": total_dns,
            "total_value": float(metrics.total_value or 0),
            "pending_dns": metrics.pending_dns or 0,
            "pending_value": float(metrics.pending_value or 0),
            "pod_pending_dns": metrics.pod_pending_dns or 0,
            "aging_summary": aging,
            "pod_aging_summary": pod_aging,
            "critical_dns": critical_dns,
            "formatted_message": formatted_message
        }

    def _get_dealer_critical_dns(self, dealer_name: str, limit: int = 10) -> List[Dict]:
        """Get critical DNs for a dealer using limited query"""
        records = self.db.query(
            DeliveryReport.dn_no,
            DeliveryReport.dn_create_date,
            DeliveryReport.good_issue_date,
            DeliveryReport.pod_status,
            DeliveryReport.dn_qty,
            DeliveryReport.dn_amount
        ).filter(
            DeliveryReport.customer_name == dealer_name,
            or_(
                DeliveryReport.pgi_status != "Completed",
                and_(
                    DeliveryReport.pgi_status == "Completed",
                    DeliveryReport.pod_status == "Pending"
                )
            )
        ).limit(limit * 2).all()  # Get extra for filtering
        
        seen_dns = set()
        critical = []
        
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
                if len(critical) >= limit:
                    break
        
        return critical

    def _format_dealer_dashboard(self, dealer_name: str, total_dns: int, total_value: float,
                                  pending_dns: int, pending_value: float, pod_pending_dns: int,
                                  aging: Dict, pod_aging: Dict) -> str:
        """Format dealer dashboard message"""
        response = f"📊 *DEALER DASHBOARD: {dealer_name}*\n\n"
        response += f"📦 Total DNs: {total_dns}\n"
        response += f"💰 Total Value: Rs {total_value:,.2f}\n\n"
        response += f"⏳ Pending DNs: {pending_dns}\n"
        response += f"💰 Pending Value: Rs {pending_value:,.2f}\n\n"
        response += f"📋 POD Pending: {pod_pending_dns} DNs\n"
        
        if aging.get("critical_count", 0) > 0:
            response += f"\n⚠️ *CRITICAL:* {aging.get('critical_count', 0)} DNs older than 15 days\n"
        
        if pod_aging.get("urgent_count", 0) > 0:
            response += f"⚠️ *URGENT:* {pod_aging.get('urgent_count', 0)} PODs overdue\n"
        
        return response

    # ======================================================
    # ISSUE 2: REDUCED .all() USAGE
    # ======================================================

    def aging_summary(self, dealer_name: str = None) -> Dict[str, Any]:
        """Get aging buckets using limited query and deduplication"""
        query = self.db.query(
            DeliveryReport.dn_no,
            DeliveryReport.dn_create_date,
            DeliveryReport.good_issue_date,
            DeliveryReport.dn_qty,
            DeliveryReport.dn_amount,
            DeliveryReport.customer_name
        ).filter(DeliveryReport.pgi_status != "Completed")
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name == dealer_name)
        
        # Limit to reasonable amount for aging analysis
        records = query.limit(5000).all()
        
        # Deduplicate by DN
        seen_dns = set()
        unique_records = []
        for r in records:
            if r.dn_no not in seen_dns:
                seen_dns.add(r.dn_no)
                unique_records.append(r)
        
        buckets = {
            "0-3": {"dns": 0, "units": 0, "value": 0},
            "4-7": {"dns": 0, "units": 0, "value": 0},
            "8-15": {"dns": 0, "units": 0, "value": 0},
            "16-30": {"dns": 0, "units": 0, "value": 0},
            "30+": {"dns": 0, "units": 0, "value": 0}
        }
        
        for r in unique_records:
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

    def pod_aging_summary(self, dealer_name: str = None) -> Dict[str, Any]:
        """Get POD aging buckets using limited query"""
        query = self.db.query(
            DeliveryReport.dn_no,
            DeliveryReport.good_issue_date,
            DeliveryReport.pod_status,
            DeliveryReport.dn_qty,
            DeliveryReport.dn_amount,
            DeliveryReport.customer_name
        ).filter(
            DeliveryReport.pgi_status == "Completed"
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name == dealer_name)
        
        records = query.limit(5000).all()
        
        # ISSUE 3 FIX: Use normalized check for POD status
        pending_records = [r for r in records if not self.is_pod_received(r.pod_status)]
        
        # Deduplicate by DN
        seen_dns = set()
        unique_records = []
        for r in pending_records:
            if r.dn_no not in seen_dns:
                seen_dns.add(r.dn_no)
                unique_records.append(r)
        
        buckets = {
            "0-7": {"dns": 0, "units": 0, "value": 0},
            "8-15": {"dns": 0, "units": 0, "value": 0},
            "16-30": {"dns": 0, "units": 0, "value": 0},
            "30+": {"dns": 0, "units": 0, "value": 0}
        }
        
        for r in unique_records:
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
            "oldest_age": max((self.calculate_pod_age(r) for r in unique_records), default=0)
        }

    # ======================================================
    # DEALER RANKINGS (SQL Aggregation - Efficient)
    # ======================================================

    def dealer_rankings(self, limit: int = 10) -> Dict[str, List]:
        """Get dealer rankings using SQL aggregation"""
        results = self.db.query(
            DeliveryReport.customer_name,
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).label("total_units"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                else_=0
            )).label("pending_units"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                else_=0
            )).label("pending_value"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns")
        ).filter(
            DeliveryReport.customer_name.isnot(None)
        ).group_by(
            DeliveryReport.customer_name
        ).all()
        
        dealers = []
        for r in results:
            if not r.customer_name:
                continue
            total_dns = r.total_dns or 1
            pending_dns = r.pending_dns or 0
            score = max(0, 100 - ((pending_dns / total_dns) * 100))
            
            dealers.append({
                "dealer": r.customer_name,
                "total_dns": total_dns,
                "total_units": float(r.total_units or 0),
                "total_value": float(r.total_value or 0),
                "pending_dns": pending_dns,
                "pending_units": float(r.pending_units or 0),
                "pending_value": float(r.pending_value or 0),
                "score": round(score, 1)
            })
        
        by_value = sorted(dealers, key=lambda x: x["total_value"], reverse=True)[:limit]
        by_pending = sorted(dealers, key=lambda x: x["pending_value"], reverse=True)[:limit]
        by_score = sorted(dealers, key=lambda x: x["score"], reverse=True)[:limit]
        
        return {
            "by_value": by_value,
            "by_pending": by_pending,
            "by_score": by_score
        }

    def warehouse_rankings(self, limit: int = 10) -> Dict[str, List]:
        """Get warehouse rankings using SQL aggregation"""
        results = self.db.query(
            DeliveryReport.warehouse,
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).label("total_units"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                else_=0
            )).label("pending_units"),
            func.count(distinct(case(
                (and_(DeliveryReport.pgi_status == "Completed", 
                      ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])),
                 DeliveryReport.dn_no),
                else_=None
            ))).label("pod_pending_dns")
        ).filter(
            DeliveryReport.warehouse.isnot(None)
        ).group_by(
            DeliveryReport.warehouse
        ).all()
        
        warehouses = []
        for r in results:
            if not r.warehouse:
                continue
            total_dns = r.total_dns or 1
            pending_dns = r.pending_dns or 0
            efficiency_score = max(0, 100 - ((pending_dns / total_dns) * 100))
            
            warehouses.append({
                "warehouse": r.warehouse,
                "total_dns": total_dns,
                "total_units": float(r.total_units or 0),
                "total_value": float(r.total_value or 0),
                "pending_dns": pending_dns,
                "pending_units": float(r.pending_units or 0),
                "pod_pending_dns": r.pod_pending_dns or 0,
                "efficiency_score": round(efficiency_score, 1)
            })
        
        by_efficiency = sorted(warehouses, key=lambda x: x["efficiency_score"], reverse=True)[:limit]
        by_pending = sorted(warehouses, key=lambda x: x["pending_dns"], reverse=True)[:limit]
        
        return {
            "by_efficiency": by_efficiency,
            "by_pending": by_pending,
            "all_warehouses": warehouses
        }

    def city_rankings(self, limit: int = 10) -> Dict[str, List]:
        """Get city rankings using SQL aggregation"""
        results = self.db.query(
            DeliveryReport.ship_to_city,
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).label("total_units"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                else_=0
            )).label("pending_units"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                else_=0
            )).label("pending_value")
        ).filter(
            DeliveryReport.ship_to_city.isnot(None)
        ).group_by(
            DeliveryReport.ship_to_city
        ).all()
        
        cities = []
        for r in results:
            if not r.ship_to_city:
                continue
            total_dns = r.total_dns or 1
            pending_dns = r.pending_dns or 0
            delay_rate = (pending_dns / total_dns) * 100
            performance_score = max(0, 100 - delay_rate)
            
            cities.append({
                "city": r.ship_to_city,
                "total_dns": total_dns,
                "total_units": float(r.total_units or 0),
                "total_value": float(r.total_value or 0),
                "pending_dns": pending_dns,
                "pending_units": float(r.pending_units or 0),
                "pending_value": float(r.pending_value or 0),
                "delay_rate": round(delay_rate, 1),
                "performance_score": round(performance_score, 1)
            })
        
        by_performance = sorted(cities, key=lambda x: x["performance_score"], reverse=True)[:limit]
        by_pending = sorted(cities, key=lambda x: x["pending_dns"], reverse=True)[:limit]
        
        return {
            "by_performance": by_performance,
            "by_pending": by_pending,
            "all_cities": cities
        }

    # ======================================================
    # TOP RISK DEALERS (Using normalized status)
    # ======================================================

    def top_risk_dealers(self, limit: int = 10) -> List[Dict]:
        """Identify dealers with highest risk using SQL aggregation"""
        results = self.db.query(
            DeliveryReport.customer_name,
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                else_=0
            )).label("pending_value"),
            func.count(distinct(case(
                (and_(DeliveryReport.pgi_status == "Completed", 
                      ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])),
                 DeliveryReport.dn_no),
                else_=None
            ))).label("pod_pending_dns"),
            func.max(case(
                (and_(DeliveryReport.pgi_status == "Completed", 
                      ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])),
                 func.date_part('day', func.age(func.current_date(), DeliveryReport.good_issue_date))),
                else_=0
            )).label("oldest_pod_age")
        ).filter(
            DeliveryReport.customer_name.isnot(None)
        ).group_by(
            DeliveryReport.customer_name
        ).having(
            func.count(distinct(DeliveryReport.dn_no)) > 0
        ).all()
        
        dealer_list = []
        for r in results:
            if not r.customer_name:
                continue
            
            total_dns = r.total_dns or 1
            pending_dns = r.pending_dns or 0
            pod_pending_dns = r.pod_pending_dns or 0
            oldest_pod_age = r.oldest_pod_age or 0
            
            risk_score = (
                (pending_dns / total_dns * 40) +
                (pod_pending_dns / total_dns * 30) +
                (min(oldest_pod_age, 30) / 30 * 30)
            )
            
            dealer_list.append({
                "dealer": r.customer_name,
                "pending_dns": pending_dns,
                "pending_value": float(r.pending_value or 0),
                "pod_pending_dns": pod_pending_dns,
                "oldest_pod_age": int(oldest_pod_age),
                "risk_score": round(risk_score, 1),
                "total_dns": total_dns
            })
        
        return sorted(dealer_list, key=lambda x: x["risk_score"], reverse=True)[:limit]

    # ======================================================
    # TOP PRODUCTS (Using normalized status)
    # ======================================================

    def top_products(self, limit: int = 10) -> Dict[str, List]:
        """Get top products using SQL aggregation"""
        results = self.db.query(
            DeliveryReport.customer_model,
            DeliveryReport.material_no,
            func.sum(DeliveryReport.dn_qty).label("total_qty"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                else_=0
            )).label("pending_qty"),
            func.sum(case(
                (and_(DeliveryReport.pgi_status == "Completed", 
                      ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])),
                 DeliveryReport.dn_qty),
                else_=0
            )).label("pod_pending_qty"),
            func.count(distinct(DeliveryReport.dn_no)).label("dn_count")
        ).group_by(
            DeliveryReport.customer_model, DeliveryReport.material_no
        ).having(
            func.sum(DeliveryReport.dn_qty) > 0
        ).all()
        
        products = []
        for r in results:
            product_name = r.customer_model or r.material_no or "Unknown"
            total_qty = float(r.total_qty or 0)
            pending_qty = float(r.pending_qty or 0)
            
            products.append({
                "name": product_name,
                "total_qty": total_qty,
                "total_value": float(r.total_value or 0),
                "pending_qty": pending_qty,
                "pod_pending_qty": float(r.pod_pending_qty or 0),
                "dn_count": r.dn_count or 0,
                "fulfillment_rate": round(((total_qty - pending_qty) / total_qty * 100) if total_qty > 0 else 100, 1)
            })
        
        by_volume = sorted(products, key=lambda x: x["total_qty"], reverse=True)[:limit]
        by_pending = sorted([p for p in products if p["pending_qty"] > 0], key=lambda x: x["pending_qty"], reverse=True)[:limit]
        
        return {
            "top_products_by_volume": by_volume,
            "top_pending_products": by_pending
        }

    # ======================================================
    # DEALER SCORE (Using normalized status)
    # ======================================================

    def dealer_score(self, dealer_name: str) -> Dict[str, Any]:
        """Calculate dealer score using SQL aggregation"""
        metrics = self.db.query(
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.count(distinct(case(
                (and_(DeliveryReport.pgi_status == "Completed", 
                      func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])),
                 DeliveryReport.dn_no),
                else_=None
            ))).label("delivered_dns"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns"),
            func.count(distinct(case(
                (and_(DeliveryReport.pgi_status == "Completed", 
                      ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])),
                 DeliveryReport.dn_no),
                else_=None
            ))).label("pod_pending_dns"),
            func.avg(case(
                (DeliveryReport.pgi_status != "Completed",
                 func.date_part('day', func.age(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date))),
                else_=0
            )).label("avg_dispatch_age")
        ).filter(
            DeliveryReport.customer_name == dealer_name
        ).first()
        
        total_dns = metrics.total_dns or 0
        if total_dns == 0:
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        delivered_dns = metrics.delivered_dns or 0
        pending_dns = metrics.pending_dns or 0
        pod_pending_dns = metrics.pod_pending_dns or 0
        
        pod_compliance = (delivered_dns / max(delivered_dns + pod_pending_dns, 1)) * 100
        pending_score = max(0, 100 - ((pending_dns / total_dns) * 100))
        delivery_performance = (delivered_dns / total_dns) * 100
        
        avg_age = metrics.avg_dispatch_age or 0
        if avg_age <= 3:
            aging_score = 100
        elif avg_age <= 7:
            aging_score = 80
        elif avg_age <= 15:
            aging_score = 50
        else:
            aging_score = 20
        
        volume_score = min(100, (total_dns / 100) * 100)
        
        final_score = (
            pod_compliance * 0.40 +
            pending_score * 0.30 +
            delivery_performance * 0.15 +
            aging_score * 0.10 +
            volume_score * 0.05
        )
        
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
    # DN METRICS (Limited query)
    # ======================================================

    def dn_metrics(self, dn_no: str) -> Dict[str, Any]:
        """Get comprehensive metrics for a single DN"""
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.dn_no == dn_no
        ).limit(100).all()
        
        if not records:
            return {"success": False, "message": f"DN {dn_no} not found"}
        
        main = records[0]
        
        # Use SQL aggregation for products
        product_agg = self.db.query(
            DeliveryReport.material_no,
            DeliveryReport.customer_model,
            func.sum(DeliveryReport.dn_qty).label("total_qty"),
            func.sum(DeliveryReport.dn_amount).label("total_amount")
        ).filter(DeliveryReport.dn_no == dn_no).group_by(
            DeliveryReport.material_no, DeliveryReport.customer_model
        ).all()
        
        products = []
        total_qty = 0
        total_value = 0
        
        for p in product_agg:
            product = {
                "material_no": p.material_no,
                "product_name": p.customer_model or p.material_no or "Unknown",
                "quantity": float(p.total_qty or 0),
                "amount": float(p.total_amount or 0)
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
            "dispatch_age": self.calculate_dispatch_age(main),
            "pod_age": self.calculate_pod_age(main),
            "delivery_cycle": self.calculate_delivery_cycle(main),
            "products": products,
            "total_qty": total_qty,
            "total_value": total_value,
            "status": self._get_dn_status(main),
            "pod_status": "Received" if self.is_pod_received(main.pod_status) else "Pending"
        }

    def _get_dn_status(self, record) -> str:
        pgi_completed = self.is_pgi_completed(record.pgi_status)
        pod_received = self.is_pod_received(record.pod_status)
        
        if pgi_completed and pod_received:
            return "Delivered and Acknowledged"
        elif pgi_completed and not pod_received:
            return "Delivered, Awaiting Acknowledgement"
        elif not pgi_completed:
            return "Pending Dispatch"
        return record.delivery_status or "Unknown"

    # ======================================================
    # EXECUTIVE METRICS (Cached)
    # ======================================================

    def executive_metrics(self) -> Dict[str, Any]:
        """Get executive-level metrics using SQL aggregation"""
        result = self.db.query(
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).label("total_units"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
            func.count(distinct(DeliveryReport.ship_to_city)).label("total_cities")
        ).first()
        
        return {
            "total_dns": result.total_dns or 0,
            "total_units": float(result.total_units or 0),
            "total_value": float(result.total_value or 0),
            "total_dealers": result.total_dealers or 0,
            "total_cities": result.total_cities or 0
        }

    def build_executive_ai_context(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Build executive AI context with caching"""
        cache_key = "executive_context"
        
        if not force_refresh:
            cached = self._get_cached(cache_key)
            if cached:
                return cached
        
        executive = self.executive_metrics()
        top_dealers = self.top_risk_dealers(10)
        top_warehouses = self.top_risk_warehouses(10)
        top_cities = self.top_risk_cities(10)
        aging = self.aging_summary()
        pod_aging = self.pod_aging_summary()
        top_products_data = self.top_products(10)
        
        result = {
            "success": True,
            "overview": executive,
            "top_risk_dealers": top_dealers[:5],
            "top_risk_warehouses": top_warehouses[:5],
            "top_risk_cities": top_cities[:5],
            "aging_summary": aging,
            "pod_aging_summary": pod_aging,
            "top_products": top_products_data.get("top_products_by_volume", [])[:5],
            "top_pending_products": top_products_data.get("top_pending_products", [])[:5]
        }
        
        self._set_cache(cache_key, result)
        return result

    def build_dealer_ai_context(self, dealer_name: str) -> Dict[str, Any]:
        """Build AI context for dealer analysis"""
        dashboard = self.dealer_dashboard_metrics(dealer_name)
        
        if not dashboard.get("success"):
            if dashboard.get("fuzzy"):
                return {"success": False, "fuzzy": True, "matches": dashboard.get("matches", [])}
            return {"success": False, "message": dashboard.get("message", "Dealer not found")}
        
        return {
            "success": True,
            "dealer_name": dashboard.get("dealer_name"),
            "total_dns": dashboard.get("total_dns", 0),
            "pending_dns": dashboard.get("pending_dns", 0),
            "pending_value": dashboard.get("pending_value", 0),
            "pod_pending_dns": dashboard.get("pod_pending_dns", 0),
            "critical_dns_count": len(dashboard.get("critical_dns", [])),
            "aging_summary": dashboard.get("aging_summary", {})
        }

    # ======================================================
    # CACHE HELPERS
    # ======================================================

    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache:
            value, timestamp = self._cache[key]
            if (datetime.utcnow() - timestamp).seconds < self._cache_ttl:
                return value
            del self._cache[key]
        return None

    def _set_cache(self, key: str, value: Any):
        self._cache[key] = (value, datetime.utcnow())


# ======================================================
# FACTORY FUNCTION
# ======================================================

def get_analytics_service(db: Session) -> AnalyticsService:
    return AnalyticsService(db)
