# ==========================================================
# FILE: app/services/kpi_service.py (ENTERPRISE v2.0)
# ==========================================================
# KPI SERVICE - Executive Dashboard
# - Sales metrics (today, MTD, YTD)
# - DN metrics (created, delivered, pending)
# - Network health score
# - CEO briefing
# ==========================================================

from typing import Dict, Any
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from loguru import logger

from app.models import DeliveryReport


class KPIService:
    """Executive KPI Dashboard Service"""
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        logger.info("✅ KPI Service initialized")
    
    def get_executive_dashboard(self) -> Dict[str, Any]:
        """Get complete executive KPI dashboard"""
        try:
            today = date.today()
            month_start = date(today.year, today.month, 1)
            year_start = date(today.year, 1, 1)
            
            # Sales metrics
            sales_today = self._get_sales_for_date(today)
            sales_mtd = self._get_sales_for_period(month_start, today)
            sales_ytd = self._get_sales_for_period(year_start, today)
            
            # DN metrics
            dns_created = self.db.query(func.count(DeliveryReport.dn_no)).scalar() or 0
            dns_delivered = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            dns_pending = dns_created - dns_delivered
            
            # Pending metrics
            pgi_pending = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            pod_pending = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).scalar() or 0
            
            # Revenue at risk
            revenue_at_risk = self._get_revenue_at_risk()
            
            # Network health
            health_score = self._calculate_network_health()
            
            return {
                "sales_today": sales_today,
                "sales_mtd": sales_mtd,
                "sales_ytd": sales_ytd,
                "dns_created": dns_created,
                "dns_delivered": dns_delivered,
                "dns_pending": dns_pending,
                "pgi_pending": pgi_pending,
                "pod_pending": pod_pending,
                "revenue_at_risk": revenue_at_risk,
                "health_score": round(health_score, 1),
                "health_status": self._get_health_status(health_score)
            }
        except Exception as e:
            logger.error(f"Executive dashboard error: {e}")
            return {"error": str(e)}
    
    def get_ceo_briefing(self) -> Dict[str, Any]:
        """Get CEO briefing with strategic insights"""
        dashboard = self.get_executive_dashboard()
        
        if "error" in dashboard:
            return dashboard
        
        return {
            **dashboard,
            "top_risks": self._get_top_risks(),
            "recommendations": self._get_recommendations(dashboard)
        }
    
    def get_network_health(self) -> Dict[str, Any]:
        """Get network health score"""
        try:
            health_score = self._calculate_network_health()
            return {
                "score": round(health_score, 1),
                "status": self._get_health_status(health_score),
                "components": self._get_health_components()
            }
        except Exception as e:
            logger.error(f"Network health error: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # PRIVATE METHODS
    # ==========================================================
    
    def _get_sales_for_date(self, target_date: date) -> float:
        try:
            result = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                func.date(DeliveryReport.dn_create_date) == target_date
            ).scalar()
            return float(result or 0)
        except:
            return 0
    
    def _get_sales_for_period(self, start_date: date, end_date: date) -> float:
        try:
            result = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.dn_create_date >= start_date,
                DeliveryReport.dn_create_date <= end_date
            ).scalar()
            return float(result or 0)
        except:
            return 0
    
    def _get_revenue_at_risk(self) -> float:
        try:
            pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            pod_pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).scalar() or 0
            return float(pending + pod_pending)
        except:
            return 0
    
    def _calculate_network_health(self) -> float:
        try:
            total_dns = self.db.query(func.count(DeliveryReport.dn_no)).scalar() or 1
            delivered = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            pod_received = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pod_status == "Received"
            ).scalar() or 0
            
            delivery_score = (delivered / total_dns) * 100
            pod_score = (pod_received / total_dns) * 100
            
            return (delivery_score * 0.6) + (pod_score * 0.4)
        except:
            return 50
    
    def _get_health_status(self, score: float) -> str:
        if score >= 90:
            return "Excellent"
        elif score >= 80:
            return "Good"
        elif score >= 70:
            return "Fair"
        elif score >= 60:
            return "Poor"
        else:
            return "Critical"
    
    def _get_health_components(self) -> Dict:
        try:
            total_dns = self.db.query(func.count(DeliveryReport.dn_no)).scalar() or 1
            delivered = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            pod_received = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pod_status == "Received"
            ).scalar() or 0
            
            return {
                "delivery_compliance": round((delivered / total_dns) * 100, 1),
                "pod_compliance": round((pod_received / total_dns) * 100, 1)
            }
        except:
            return {"delivery_compliance": 0, "pod_compliance": 0}
    
    def _get_top_risks(self) -> List[Dict]:
        try:
            # High risk dealers
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("pending_dns")
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.count(DeliveryReport.dn_no).desc()
            ).limit(3).all()
            
            return [{"dealer": r.customer_name, "pending_dns": r.pending_dns} for r in results if r.customer_name]
        except:
            return []
    
    def _get_recommendations(self, dashboard: Dict) -> List[str]:
        recommendations = []
        
        if dashboard.get("pod_pending", 0) > 100:
            recommendations.append("Prioritize POD collection - high volume pending")
        
        if dashboard.get("pgi_pending", 0) > 50:
            recommendations.append("Review warehouse processing delays")
        
        if dashboard.get("health_score", 100) < 70:
            recommendations.append("Schedule operational review - health score declining")
        
        if not recommendations:
            recommendations.append("All KPIs within acceptable range - continue monitoring")
        
        return recommendations


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_kpi_service(db: Session, cache_service=None) -> KPIService:
    return KPIService(db, cache_service)
