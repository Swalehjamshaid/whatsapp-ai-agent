# ==========================================================
# FILE: app/services/analytics_service.py (ENTERPRISE v3.0)
# ==========================================================
# ANALYTICS SERVICE
# - Dealer Analytics (top dealers, growth, risk)
# - Product Analytics (top products, fast/slow moving, dead stock)
# - City Analytics (performance, ranking)
# - Warehouse Analytics (efficiency, ranking)
# - Revenue Analytics
# ==========================================================

from typing import Dict, Any, List, Optional
from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.models import DeliveryReport


class AnalyticsService:
    """
    Enterprise Analytics Service
    Provides business intelligence across dealers, products, cities, warehouses
    """
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        logger.info("✅ Analytics Service initialized")
    
    # ==========================================================
    # DEALER ANALYTICS
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get comprehensive dealer dashboard"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == dealer_name
            ).all()
            
            if not records:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            total_dns = len(set(r.dn_no for r in records))
            total_value = sum(float(r.dn_amount or 0) for r in records)
            pending_dns = len([r for r in records if r.pgi_status != "Completed"])
            pod_pending = len([r for r in records if r.pgi_status == "Completed" and r.pod_status != "Received"])
            
            completion_rate = ((total_dns - pending_dns) / total_dns * 100) if total_dns else 0
            
            return {
                "dealer": dealer_name,
                "total_dns": total_dns,
                "total_value": total_value,
                "pending_dns": pending_dns,
                "pod_pending": pod_pending,
                "completion_rate": round(completion_rate, 1),
                "health_score": round(completion_rate, 1)
            }
        except Exception as e:
            logger.error(f"Dealer dashboard error: {e}")
            return {"error": str(e)}
    
    def get_dealer_ranking(self, limit: int = 10) -> List[Dict]:
        """Get top dealers by sales value"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).label("total_dns")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("total_value")
            ).limit(limit).all()
            
            return [{
                "dealer": r.customer_name,
                "total_value": float(r.total_value or 0),
                "total_dns": r.total_dns
            } for r in results if r.customer_name]
        except Exception as e:
            logger.error(f"Dealer ranking error: {e}")
            return []
    
    def get_high_risk_dealers(self, limit: int = 10) -> List[Dict]:
        """Get dealers with highest risk (most pending DNs)"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.sum(case((DeliveryReport.pgi_status != "Completed", 1), else_=0)).label("pending_dns")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).having(
                func.sum(case((DeliveryReport.pgi_status != "Completed", 1), else_=0)) > 0
            ).order_by(
                desc("pending_dns")
            ).limit(limit).all()
            
            return [{
                "dealer": r.customer_name,
                "pending_dns": r.pending_dns,
                "pending_value": float(r.total_value or 0) * (r.pending_dns / r.total_dns) if r.total_dns else 0,
                "risk_score": round((r.pending_dns / r.total_dns * 100) if r.total_dns else 0, 1)
            } for r in results if r.customer_name]
        except Exception as e:
            logger.error(f"High risk dealers error: {e}")
            return []
    
    # ==========================================================
    # PRODUCT ANALYTICS
    # ==========================================================
    
    def get_product_dashboard(self, product_code: str) -> Dict[str, Any]:
        """Get product intelligence dashboard"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.material_no == product_code
            ).all()
            
            if not records:
                return {"error": f"Product '{product_code}' not found"}
            
            total_qty = sum(float(r.dn_qty or 0) for r in records)
            total_value = sum(float(r.dn_amount or 0) for r in records)
            delivered_qty = sum(float(r.dn_qty or 0) for r in records if r.pgi_status == "Completed")
            
            fill_rate = (delivered_qty / total_qty * 100) if total_qty else 0
            
            return {
                "product": product_code,
                "total_qty": total_qty,
                "total_value": total_value,
                "delivered_qty": delivered_qty,
                "fill_rate": round(fill_rate, 1),
                "risk_level": "High" if fill_rate < 50 else "Medium" if fill_rate < 80 else "Low"
            }
        except Exception as e:
            logger.error(f"Product dashboard error: {e}")
            return {"error": str(e)}
    
    def get_top_products(self, limit: int = 10) -> List[Dict]:
        """Get top products by revenue"""
        try:
            results = self.db.query(
                DeliveryReport.material_no,
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.sum(DeliveryReport.dn_qty).label("total_qty")
            ).filter(
                DeliveryReport.material_no.isnot(None)
            ).group_by(
                DeliveryReport.material_no
            ).order_by(
                desc("total_value")
            ).limit(limit).all()
            
            return [{
                "product": r.material_no,
                "total_value": float(r.total_value or 0),
                "total_qty": float(r.total_qty or 0)
            } for r in results if r.material_no]
        except Exception as e:
            logger.error(f"Top products error: {e}")
            return []
    
    def get_fast_moving_products(self, limit: int = 10) -> List[Dict]:
        """Get products with highest quantity per DN"""
        try:
            results = self.db.query(
                DeliveryReport.material_no,
                func.avg(DeliveryReport.dn_qty).label("avg_qty_per_dn"),
                func.sum(DeliveryReport.dn_qty).label("total_qty")
            ).filter(
                DeliveryReport.material_no.isnot(None)
            ).group_by(
                DeliveryReport.material_no
            ).order_by(
                desc("avg_qty_per_dn")
            ).limit(limit).all()
            
            return [{
                "product": r.material_no,
                "avg_qty_per_dn": round(float(r.avg_qty_per_dn or 0), 1),
                "total_qty": float(r.total_qty or 0)
            } for r in results if r.material_no]
        except Exception as e:
            logger.error(f"Fast moving products error: {e}")
            return []
    
    def get_dead_stock_products(self, days: int = 90, limit: int = 20) -> List[Dict]:
        """Get products with no activity in last N days"""
        try:
            cutoff_date = date.today() - timedelta(days=days)
            
            active_products = set()
            active_records = self.db.query(DeliveryReport.material_no).filter(
                DeliveryReport.dn_create_date >= cutoff_date
            ).distinct().all()
            for r in active_records:
                if r.material_no:
                    active_products.add(r.material_no)
            
            all_products = self.db.query(
                DeliveryReport.material_no,
                func.sum(DeliveryReport.dn_qty).label("total_qty"),
                func.max(DeliveryReport.dn_create_date).label("last_order")
            ).filter(
                DeliveryReport.material_no.isnot(None)
            ).group_by(
                DeliveryReport.material_no
            ).all()
            
            dead_stock = []
            for r in all_products:
                if r.material_no and r.material_no not in active_products:
                    dead_stock.append({
                        "product": r.material_no,
                        "total_qty": float(r.total_qty or 0),
                        "last_order": r.last_order,
                        "inactive_days": days
                    })
            
            return sorted(dead_stock, key=lambda x: x["total_qty"], reverse=True)[:limit]
        except Exception as e:
            logger.error(f"Dead stock products error: {e}")
            return []
    
    # ==========================================================
    # CITY ANALYTICS
    # ==========================================================
    
    def get_city_dashboard(self, city_name: str) -> Dict[str, Any]:
        """Get city performance dashboard"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.ship_to_city == city_name
            ).all()
            
            if not records:
                return {"error": f"City '{city_name}' not found"}
            
            total_dns = len(set(r.dn_no for r in records))
            pending_dns = len([r for r in records if r.pgi_status != "Completed"])
            delay_rate = (pending_dns / total_dns * 100) if total_dns else 0
            
            return {
                "city": city_name,
                "total_dns": total_dns,
                "pending_dns": pending_dns,
                "delay_rate": round(delay_rate, 1),
                "risk_score": round(delay_rate * 1.5, 1)
            }
        except Exception as e:
            logger.error(f"City dashboard error: {e}")
            return {"error": str(e)}
    
    def get_city_ranking(self, limit: int = 10) -> List[Dict]:
        """Get city ranking by sales"""
        try:
            results = self.db.query(
                DeliveryReport.ship_to_city,
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).label("total_dns")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(
                DeliveryReport.ship_to_city
            ).order_by(
                desc("total_value")
            ).limit(limit).all()
            
            return [{
                "city": r.ship_to_city,
                "total_value": float(r.total_value or 0),
                "total_dns": r.total_dns
            } for r in results if r.ship_to_city]
        except Exception as e:
            logger.error(f"City ranking error: {e}")
            return []
    
    # ==========================================================
    # WAREHOUSE ANALYTICS
    # ==========================================================
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse performance dashboard"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.warehouse == warehouse_name
            ).all()
            
            if not records:
                return {"error": f"Warehouse '{warehouse_name}' not found"}
            
            total_dns = len(set(r.dn_no for r in records))
            completed_dns = len([r for r in records if r.pgi_status == "Completed"])
            efficiency = (completed_dns / total_dns * 100) if total_dns else 0
            
            return {
                "warehouse": warehouse_name,
                "total_dns": total_dns,
                "completed_dns": completed_dns,
                "pending_dns": total_dns - completed_dns,
                "efficiency": round(efficiency, 1)
            }
        except Exception as e:
            logger.error(f"Warehouse dashboard error: {e}")
            return {"error": str(e)}
    
    def get_warehouse_ranking(self, limit: int = 10) -> List[Dict]:
        """Get warehouse ranking by efficiency"""
        try:
            results = self.db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(case((DeliveryReport.pgi_status == "Completed", 1), else_=0)).label("completed_dns")
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            ).all()
            
            rankings = []
            for r in results:
                if r.warehouse and r.total_dns:
                    efficiency = (r.completed_dns / r.total_dns * 100)
                    rankings.append({
                        "warehouse": r.warehouse,
                        "total_dns": r.total_dns,
                        "completed_dns": r.completed_dns,
                        "efficiency": round(efficiency, 1)
                    })
            
            return sorted(rankings, key=lambda x: x["efficiency"], reverse=True)[:limit]
        except Exception as e:
            logger.error(f"Warehouse ranking error: {e}")
            return []
    
    # ==========================================================
    # REVENUE ANALYTICS
    # ==========================================================
    
    def get_revenue_analysis(self) -> Dict[str, Any]:
        """Get revenue analysis summary"""
        try:
            total = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
            delivered = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            pod_pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).scalar() or 0
            
            realized = delivered - pod_pending
            
            return {
                "total_revenue": float(total),
                "realized_revenue": float(realized),
                "pending_delivery": float(total - delivered),
                "pod_pending_value": float(pod_pending),
                "realization_rate": round((realized / total * 100) if total else 0, 1)
            }
        except Exception as e:
            logger.error(f"Revenue analysis error: {e}")
            return {"error": str(e)}
    
    def get_revenue_at_risk(self) -> Dict[str, Any]:
        """Get revenue at risk analysis"""
        try:
            pending_delivery = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            
            pod_pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).scalar() or 0
            
            total_at_risk = pending_delivery + pod_pending
            
            return {
                "total_at_risk": float(total_at_risk),
                "pending_delivery": float(pending_delivery),
                "pod_pending": float(pod_pending),
                "risk_level": "Critical" if total_at_risk > 10_000_000 else "High" if total_at_risk > 5_000_000 else "Medium"
            }
        except Exception as e:
            logger.error(f"Revenue at risk error: {e}")
            return {"error": str(e)}


# Helper for SQL CASE
def case(when, then, else_=0):
    from sqlalchemy import case as sa_case
    return sa_case(when, then, else_=else_)


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_analytics_service(db: Session, cache_service=None) -> AnalyticsService:
    return AnalyticsService(db, cache_service)
