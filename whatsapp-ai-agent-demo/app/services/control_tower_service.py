# ==========================================================
# FILE: app/services/control_tower_service.py
# ==========================================================
# CONTROL TOWER - CRITICAL ALERTS SERVICE
# ==========================================================

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.models import DeliveryReport
from app.services.business_rules_service import BusinessRulesService


class ControlTowerService:
    """Control tower for critical alerts and monitoring"""
    
    def __init__(self, db: Session):
        self.db = db
        self.business_rules = BusinessRulesService()
    
    def get_control_tower_dashboard(self) -> Dict[str, Any]:
        """Get complete control tower dashboard"""
        return {
            "critical_dns": self.get_critical_dns(10),
            "high_risk_dns": self.get_high_risk_dns(10),
            "critical_pods": self.get_critical_pods(10),
            "high_value_pending": self.get_high_value_pending_dns(10),
            "summary": self._get_alert_summary()
        }
    
    def get_critical_dns(self, limit: int = 20) -> List[Dict]:
        """Get critical DNs (delayed > 15 days)"""
        critical_dns = []
        
        try:
            cutoff_date = date.today() - timedelta(days=15)
            
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.pgi_status
            ).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= cutoff_date
            ).order_by(
                DeliveryReport.dn_create_date
            ).limit(limit).all()
            
            for dn in results:
                aging = self.business_rules.calculate_pending_delivery_aging(dn)
                
                critical_dns.append({
                    "dn_no": dn.dn_no,
                    "dealer": dn.customer_name,
                    "value": float(dn.dn_amount or 0),
                    "aging_days": aging,
                    "severity": self._get_severity(aging),
                    "action": "URGENT - Escalate to regional manager"
                })
        
        except Exception as e:
            logger.error(f"Error getting critical DNs: {e}")
        
        return critical_dns
    
    def get_high_risk_dns(self, limit: int = 20) -> List[Dict]:
        """Get high-risk DNs (high value + delayed)"""
        high_risk_dns = []
        
        try:
            # Get DNs with high value and delay
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.pgi_status
            ).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_amount > 500000  # High value threshold
            ).order_by(
                desc(DeliveryReport.dn_amount)
            ).limit(limit).all()
            
            for dn in results:
                aging = self.business_rules.calculate_pending_delivery_aging(dn)
                
                high_risk_dns.append({
                    "dn_no": dn.dn_no,
                    "dealer": dn.customer_name,
                    "value": float(dn.dn_amount or 0),
                    "aging_days": aging,
                    "risk_score": self._calculate_risk_score(dn),
                    "action": "PRIORITY - High value at risk"
                })
        
        except Exception as e:
            logger.error(f"Error getting high-risk DNs: {e}")
        
        return high_risk_dns
    
    def get_critical_pods(self, limit: int = 20) -> List[Dict]:
        """Get critical pending PODs (pending > 7 days)"""
        critical_pods = []
        
        try:
            cutoff_date = date.today() - timedelta(days=7)
            
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.delivery_date,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending",
                DeliveryReport.delivery_date <= cutoff_date
            ).order_by(
                DeliveryReport.delivery_date
            ).limit(limit).all()
            
            for pod in results:
                pending_days = self.business_rules.calculate_pending_pod_aging(pod)
                
                critical_pods.append({
                    "dn_no": pod.dn_no,
                    "dealer": pod.customer_name,
                    "value": float(pod.dn_amount or 0),
                    "pending_days": pending_days,
                    "action": "Send escalation notice to customer"
                })
        
        except Exception as e:
            logger.error(f"Error getting critical PODs: {e}")
        
        return critical_pods
    
    def get_high_value_pending_dns(self, limit: int = 20) -> List[Dict]:
        """Get high-value pending DNs"""
        high_value_dns = []
        
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.pgi_status
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).order_by(
                desc(DeliveryReport.dn_amount)
            ).limit(limit).all()
            
            for dn in results:
                if float(dn.dn_amount or 0) > 1000000:  # > 10 Lakh
                    high_value_dns.append({
                        "dn_no": dn.dn_no,
                        "dealer": dn.customer_name,
                        "value": float(dn.dn_amount or 0),
                        "created_date": dn.dn_create_date,
                        "aging_days": self.business_rules.calculate_pending_delivery_aging(dn),
                        "priority": "CRITICAL"
                    })
        
        except Exception as e:
            logger.error(f"Error getting high-value pending DNs: {e}")
        
        return high_value_dns
    
    def get_top_risks(self, limit: int = 10) -> List[Dict]:
        """Get top risks across the network"""
        risks = []
        
        # Risk 1: High-value pending DNs
        high_value = self.get_high_value_pending_dns(5)
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
            "high_value_pending_count": len(self.get_high_value_pending_dns(100)),
            "total_revenue_at_risk": self._get_total_revenue_at_risk()
        }
    
    def _get_severity(self, aging_days: int) -> str:
        """Get severity based on aging"""
        if aging_days > 30:
            return "SEVERE"
        elif aging_days > 15:
            return "CRITICAL"
        elif aging_days > 7:
            return "HIGH"
        else:
            return "MEDIUM"
    
    def _calculate_risk_score(self, dn) -> int:
        """Calculate risk score for a DN"""
        aging = self.business_rules.calculate_pending_delivery_aging(dn)
        value = float(dn.dn_amount or 0)
        
        return self.business_rules.calculate_risk_score(aging, value)
    
    def _get_total_revenue_at_risk(self) -> float:
        """Get total revenue at risk"""
        try:
            pending_delivery = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            
            pending_pod = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            
            return float(pending_delivery + pending_pod)
        except:
            return 0
