# ==========================================================
# FILE: app/services/control_tower_service.py (ENTERPRISE v2.0)
# ==========================================================
# CONTROL TOWER SERVICE
# - Critical alerts and monitoring
# - Real-time operational issues
# - High-risk DNs and dealers
# - Revenue at risk alerts
# ==========================================================

from typing import Dict, Any, List
from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.models import DeliveryReport


class ControlTowerService:
    """Control Tower - Critical Operational Alerts"""
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        logger.info("✅ Control Tower Service initialized")
    
    def get_control_tower_dashboard(self) -> Dict[str, Any]:
        """Get complete control tower dashboard"""
        return {
            "critical_dns": self.get_critical_dns(10),
            "high_risk_dns": self.get_high_risk_dns(10),
            "critical_pods": self.get_critical_pods(10),
            "high_risk_dealers": self.get_high_risk_dealers(10),
            "summary": self._get_alert_summary()
        }
    
    def get_critical_dns(self, limit: int = 20) -> List[Dict]:
        """Get critically delayed DNs (>15 days)"""
        try:
            threshold_date = date.today() - timedelta(days=15)
            
            records = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= threshold_date
            ).order_by(
                DeliveryReport.dn_create_date
            ).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "aging_days": (date.today() - r.dn_create_date).days if r.dn_create_date else 0,
                "severity": "CRITICAL",
                "action": "IMMEDIATE ESCALATION REQUIRED"
            } for r in records]
        except Exception as e:
            logger.error(f"Critical DNs error: {e}")
            return []
    
    def get_high_risk_dns(self, limit: int = 20) -> List[Dict]:
        """Get high-risk DNs (high value + delayed)"""
        try:
            records = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_amount > 1_000_000
            ).order_by(
                desc(DeliveryReport.dn_amount)
            ).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "aging_days": (date.today() - r.dn_create_date).days if r.dn_create_date else 0,
                "risk_score": min(100, (float(r.dn_amount or 0) / 1_000_000) * 50),
                "action": "PRIORITY - High value at risk"
            } for r in records]
        except Exception as e:
            logger.error(f"High risk DNs error: {e}")
            return []
    
    def get_critical_pods(self, limit: int = 20) -> List[Dict]:
        """Get critical pending PODs (>7 days)"""
        try:
            threshold_date = date.today() - timedelta(days=7)
            
            records = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.delivery_date
            ).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received",
                DeliveryReport.delivery_date <= threshold_date
            ).order_by(
                DeliveryReport.delivery_date
            ).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "delivery_date": r.delivery_date,
                "pending_days": (date.today() - r.delivery_date).days if r.delivery_date else 0,
                "action": "Send escalation notice to customer"
            } for r in records]
        except Exception as e:
            logger.error(f"Critical PODs error: {e}")
            return []
    
    def get_high_risk_dealers(self, limit: int = 10) -> List[Dict]:
        """Get high-risk dealers"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(case((DeliveryReport.pgi_status != "Completed", 1), else_=0)).label("pending_dns"),
                func.sum(case((DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount), else_=0)).label("pending_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).having(
                func.sum(case((DeliveryReport.pgi_status != "Completed", 1), else_=0)) > 0
            ).order_by(
                desc("pending_value")
            ).limit(limit).all()
            
            return [{
                "dealer": r.customer_name,
                "pending_dns": r.pending_dns,
                "pending_value": float(r.pending_value or 0),
                "risk_score": round((r.pending_dns / r.total_dns * 100) if r.total_dns else 0, 1)
            } for r in results if r.customer_name]
        except Exception as e:
            logger.error(f"High risk dealers error: {e}")
            return []
    
    def get_top_risks(self, limit: int = 10) -> List[Dict]:
        """Get top risks across the network"""
        risks = []
        
        # Risk 1: High-value pending DNs
        high_value = self.get_high_risk_dns(5)
        if high_value:
            total_value = sum(h["value"] for h in high_value)
            risks.append({
                "type": "HIGH_VALUE_PENDING",
                "severity": "CRITICAL",
                "description": f"{len(high_value)} high-value DNs pending",
                "value_at_risk": total_value,
                "action": "Immediate dispatch coordination"
            })
        
        # Risk 2: Critical delays
        critical = self.get_critical_dns(5)
        if critical:
            risks.append({
                "type": "CRITICAL_DELAYS",
                "severity": "HIGH",
                "description": f"{len(critical)} DNs delayed >15 days",
                "action": "Escalate to regional management"
            })
        
        # Risk 3: Critical PODs
        pod_critical = self.get_critical_pods(5)
        if pod_critical:
            total_pod_value = sum(p["value"] for p in pod_critical)
            risks.append({
                "type": "CRITICAL_PODS",
                "severity": "HIGH",
                "description": f"{len(pod_critical)} PODs pending >7 days",
                "value_at_risk": total_pod_value,
                "action": "Automated reminder + manual follow-up"
            })
        
        return risks[:limit]
    
    def _get_alert_summary(self) -> Dict:
        """Get alert summary counts"""
        return {
            "critical_dns_count": len(self.get_critical_dns(100)),
            "high_risk_count": len(self.get_high_risk_dns(100)),
            "critical_pods_count": len(self.get_critical_pods(100)),
            "high_risk_dealers_count": len(self.get_high_risk_dealers(100))
        }


# Helper for SQL CASE
def case(when, then, else_=0):
    from sqlalchemy import case as sa_case
    return sa_case(when, then, else_=else_)


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_control_tower_service(db: Session, cache_service=None) -> ControlTowerService:
    return ControlTowerService(db, cache_service)
