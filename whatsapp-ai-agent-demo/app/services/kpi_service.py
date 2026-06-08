# ==========================================================
# FILE: app/services/kpi_service.py
# ==========================================================
# EXECUTIVE KPI DASHBOARD SERVICE
# ==========================================================

from typing import Dict, Any, Optional
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from loguru import logger

from app.models import DeliveryReport


class KPIService:
    """Executive KPI dashboard service"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_executive_dashboard(self) -> Dict[str, Any]:
        """Get complete executive dashboard"""
        today = date.today()
        month_start = date(today.year, today.month, 1)
        year_start = date(today.year, 1, 1)
        
        return {
            "sales_today": self._get_sales_for_date(today),
            "sales_mtd": self._get_sales_for_period(month_start, today),
            "sales_ytd": self._get_sales_for_period(year_start, today),
            "dns_created_today": self._get_dns_count_for_date(today),
            "dns_delivered_today": self._get_delivered_count_for_date(today),
            "dns_pending": self._get_pending_dns_count(),
            "pod_pending": self._get_pending_pod_count(),
            "revenue_at_risk": self._get_revenue_at_risk(),
            "target_progress": self._get_target_progress(),
            "network_health": self._get_network_health(),
            "sla_compliance": self._get_sla_compliance()
        }
    
    def get_ceo_briefing(self) -> Dict[str, Any]:
        """Get CEO briefing with strategic insights"""
        dashboard = self.get_executive_dashboard()
        
        return {
            **dashboard,
            "top_5_dealers": self._get_top_dealers(5),
            "top_5_products": self._get_top_products(5),
            "critical_alerts": self._get_critical_alerts(),
            "recommendations": self._get_strategic_recommendations()
        }
    
    def get_network_health(self) -> Dict[str, Any]:
        """Get overall network health score"""
        return {
            "overall_health": self._get_network_health(),
            "warehouse_health": self._get_warehouse_health(),
            "dealer_health": self._get_dealer_health(),
            "pod_health": self._get_pod_health(),
            "revenue_health": self._get_revenue_health()
        }
    
    def sales_today(self) -> float:
        """Get today's sales"""
        return self._get_sales_for_date(date.today())
    
    def sales_mtd(self) -> float:
        """Get month-to-date sales"""
        today = date.today()
        month_start = date(today.year, today.month, 1)
        return self._get_sales_for_period(month_start, today)
    
    def sales_ytd(self) -> float:
        """Get year-to-date sales"""
        today = date.today()
        year_start = date(today.year, 1, 1)
        return self._get_sales_for_period(year_start, today)
    
    def dns_created(self) -> int:
        """Get total DNs created"""
        return self._get_dns_count()
    
    def dns_delivered(self) -> int:
        """Get total DNs delivered"""
        return self._get_delivered_count()
    
    def dns_pending(self) -> int:
        """Get pending DNs count"""
        return self._get_pending_dns_count()
    
    def pod_pending(self) -> int:
        """Get pending POD count"""
        return self._get_pending_pod_count()
    
    def pgi_pending(self) -> int:
        """Get pending PGI count"""
        return self._get_pending_pgi_count()
    
    # ==========================================================
    # PRIVATE METHODS
    # ==========================================================
    
    def _get_sales_for_date(self, target_date: date) -> float:
        """Get sales for a specific date"""
        try:
            result = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                func.date(DeliveryReport.dn_create_date) == target_date
            ).scalar()
            return float(result or 0)
        except Exception as e:
            logger.error(f"Error getting sales for date: {e}")
            return 0
    
    def _get_sales_for_period(self, start_date: date, end_date: date) -> float:
        """Get sales for a date period"""
        try:
            result = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.dn_create_date >= start_date,
                DeliveryReport.dn_create_date <= end_date
            ).scalar()
            return float(result or 0)
        except Exception as e:
            logger.error(f"Error getting sales for period: {e}")
            return 0
    
    def _get_dns_count_for_date(self, target_date: date) -> int:
        """Get DNS count for a specific date"""
        try:
            result = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                func.date(DeliveryReport.dn_create_date) == target_date
            ).scalar()
            return result or 0
        except Exception as e:
            logger.error(f"Error getting DNS count: {e}")
            return 0
    
    def _get_delivered_count_for_date(self, target_date: date) -> int:
        """Get delivered count for a specific date"""
        try:
            result = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                func.date(DeliveryReport.delivery_date) == target_date,
                DeliveryReport.pgi_status == "Completed"
            ).scalar()
            return result or 0
        except Exception as e:
            logger.error(f"Error getting delivered count: {e}")
            return 0
    
    def _get_dns_count(self) -> int:
        """Get total DNS count"""
        try:
            result = self.db.query(func.count(DeliveryReport.dn_no)).scalar()
            return result or 0
        except Exception as e:
            logger.error(f"Error getting DNS count: {e}")
            return 0
    
    def _get_delivered_count(self) -> int:
        """Get total delivered count"""
        try:
            result = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar()
            return result or 0
        except Exception as e:
            logger.error(f"Error getting delivered count: {e}")
            return 0
    
    def _get_pending_dns_count(self) -> int:
        """Get pending DNS count"""
        try:
            result = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar()
            return result or 0
        except Exception as e:
            logger.error(f"Error getting pending DNS: {e}")
            return 0
    
    def _get_pending_pod_count(self) -> int:
        """Get pending POD count"""
        try:
            result = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar()
            return result or 0
        except Exception as e:
            logger.error(f"Error getting pending POD: {e}")
            return 0
    
    def _get_pending_pgi_count(self) -> int:
        """Get pending PGI count"""
        try:
            result = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar()
            return result or 0
        except Exception as e:
            logger.error(f"Error getting pending PGI: {e}")
            return 0
    
    def _get_revenue_at_risk(self) -> float:
        """Get revenue at risk"""
        try:
            # Revenue at risk = pending delivery + pending POD
            pending_delivery = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            
            pending_pod = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            
            return float(pending_delivery + pending_pod)
        except Exception as e:
            logger.error(f"Error getting revenue at risk: {e}")
            return 0
    
    def _get_target_progress(self) -> float:
        """Get progress towards monthly target"""
        # Assuming monthly target is configurable
        monthly_target = 10000000  # 10 Crore
        sales_mtd = self.sales_mtd()
        
        if monthly_target > 0:
            return (sales_mtd / monthly_target) * 100
        return 0
    
    def _get_network_health(self) -> float:
        """Calculate overall network health score (0-100)"""
        try:
            # Combine multiple metrics
            sla_score = self._get_sla_compliance()
            pod_score = self._get_pod_health()
            delivery_score = self._get_delivery_health()
            
            return (sla_score * 0.4 + pod_score * 0.3 + delivery_score * 0.3)
        except Exception as e:
            logger.error(f"Error getting network health: {e}")
            return 50
    
    def _get_warehouse_health(self) -> float:
        """Get warehouse health score"""
        try:
            # Calculate average warehouse performance
            results = self.db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.dn_no).label("total"),
                func.sum(case((DeliveryReport.pgi_status == "Completed", 1), else_=0)).label("completed")
            ).group_by(DeliveryReport.warehouse).all()
            
            if not results:
                return 50
            
            scores = []
            for r in results:
                if r.total > 0:
                    score = (r.completed / r.total) * 100
                    scores.append(score)
            
            return sum(scores) / len(scores) if scores else 50
        except:
            return 50
    
    def _get_dealer_health(self) -> float:
        """Get dealer health score"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("total"),
                func.sum(case((DeliveryReport.pod_status == "Received", 1), else_=0)).label("pod_received")
            ).group_by(DeliveryReport.customer_name).all()
            
            if not results:
                return 50
            
            scores = []
            for r in results:
                if r.total > 0:
                    score = (r.pod_received / r.total) * 100
                    scores.append(score)
            
            return sum(scores) / len(scores) if scores else 50
        except:
            return 50
    
    def _get_pod_health(self) -> float:
        """Get POD health score"""
        try:
            total_completed = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 1
            
            pod_received = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Received"
            ).scalar() or 0
            
            return (pod_received / total_completed) * 100
        except:
            return 50
    
    def _get_delivery_health(self) -> float:
        """Get delivery health score"""
        try:
            total_dns = self.db.query(func.count(DeliveryReport.dn_no)).scalar() or 1
            delivered = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            
            return (delivered / total_dns) * 100
        except:
            return 50
    
    def _get_revenue_health(self) -> float:
        """Get revenue health score"""
        try:
            total_revenue = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 1
            realized_revenue = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Received"
            ).scalar() or 0
            
            return (realized_revenue / total_revenue) * 100
        except:
            return 50
    
    def _get_sla_compliance(self) -> float:
        """Get SLA compliance rate"""
        try:
            # Simplified SLA calculation
            total = self.db.query(func.count(DeliveryReport.dn_no)).scalar() or 1
            on_time = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            
            return (on_time / total) * 100
        except:
            return 70
    
    def _get_top_dealers(self, limit: int = 5) -> list:
        """Get top dealers by sales"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label("total_sales"),
                func.count(DeliveryReport.dn_no).label("total_dns")
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            return [
                {
                    "name": r.customer_name,
                    "sales": float(r.total_sales or 0),
                    "dns": r.total_dns
                }
                for r in results
            ]
        except:
            return []
    
    def _get_top_products(self, limit: int = 5) -> list:
        """Get top products by sales"""
        try:
            results = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_amount).label("total_sales"),
                func.sum(DeliveryReport.dn_qty).label("total_qty")
            ).group_by(
                DeliveryReport.product
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            return [
                {
                    "product": r.product,
                    "sales": float(r.total_sales or 0),
                    "qty": float(r.total_qty or 0)
                }
                for r in results
            ]
        except:
            return []
    
    def _get_critical_alerts(self) -> list:
        """Get critical alerts"""
        alerts = []
        
        # Check pending POD > 7 days
        pending_pod_old = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending",
            DeliveryReport.delivery_date <= date.today() - timedelta(days=7)
        ).count()
        
        if pending_pod_old > 0:
            alerts.append(f"⚠️ {pending_pod_old} PODs pending for >7 days")
        
        # Check pending PGI > 7 days
        pending_pgi_old = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status != "Completed",
            DeliveryReport.dn_create_date <= date.today() - timedelta(days=7)
        ).count()
        
        if pending_pgi_old > 0:
            alerts.append(f"⏰ {pending_pgi_old} DNs pending PGI for >7 days")
        
        return alerts
    
    def _get_strategic_recommendations(self) -> list:
        """Get strategic recommendations"""
        recommendations = []
        
        pod_health = self._get_pod_health()
        if pod_health < 70:
            recommendations.append("Improve POD collection process - target 90% compliance")
        
        delivery_health = self._get_delivery_health()
        if delivery_health < 80:
            recommendations.append("Optimize warehouse-to-delivery lead times")
        
        return recommendations


# Helper for case when
def case(when, then, else_=0):
    from sqlalchemy import case as sa_case
    return sa_case(when, then, else_=else_)
