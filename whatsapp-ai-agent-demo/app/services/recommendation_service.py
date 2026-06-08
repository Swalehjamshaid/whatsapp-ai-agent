# ==========================================================
# FILE: app/services/recommendation_service.py
# ==========================================================
# RECOMMENDATION ENGINE SERVICE
# ==========================================================

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.models import DeliveryReport
from app.services.business_rules_service import BusinessRulesService


class RecommendationService:
    """Recommendation engine for actionable insights"""
    
    def __init__(self, db: Session):
        self.db = db
        self.business_rules = BusinessRulesService()
    
    def get_recommendations(self) -> Dict[str, Any]:
        """Get all recommendations"""
        return {
            "dealer_followups": self.get_dealers_needing_followup(),
            "critical_actions": self.get_critical_delay_actions(),
            "inventory_recommendations": self.get_inventory_recommendations(),
            "process_improvements": self.get_process_improvements(),
            "priority_actions": self.get_priority_actions()
        }
    
    def get_dealers_needing_followup(self, limit: int = 10) -> List[Dict]:
        """Get dealers that need follow-up"""
        dealers = []
        
        try:
            # Dealers with pending POD > 5 days
            pending_pod_dealers = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("pending_count"),
                func.sum(DeliveryReport.dn_amount).label("pending_value"),
                func.max(DeliveryReport.delivery_date).label("oldest_pending")
            ).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending",
                DeliveryReport.delivery_date <= date.today() - timedelta(days=5)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("pending_value")
            ).limit(limit).all()
            
            for d in pending_pod_dealers:
                dealers.append({
                    "dealer": d.customer_name,
                    "reason": "Pending POD",
                    "pending_count": d.pending_count,
                    "pending_value": float(d.pending_value or 0),
                    "oldest_days": (date.today() - d.oldest_pending).days if d.oldest_pending else 0,
                    "action": "Send POD reminder and call customer"
                })
            
            # Dealers with pending PGI > 7 days
            if len(dealers) < limit:
                pending_pgi_dealers = self.db.query(
                    DeliveryReport.customer_name,
                    func.count(DeliveryReport.dn_no).label("pending_count"),
                    func.sum(DeliveryReport.dn_amount).label("pending_value"),
                    func.min(DeliveryReport.dn_create_date).label("oldest_pending")
                ).filter(
                    DeliveryReport.pgi_status != "Completed",
                    DeliveryReport.dn_create_date <= date.today() - timedelta(days=7)
                ).group_by(
                    DeliveryReport.customer_name
                ).order_by(
                    desc("pending_value")
                ).limit(limit - len(dealers)).all()
                
                for d in pending_pgi_dealers:
                    dealers.append({
                        "dealer": d.customer_name,
                        "reason": "Pending Dispatch",
                        "pending_count": d.pending_count,
                        "pending_value": float(d.pending_value or 0),
                        "oldest_days": (date.today() - d.oldest_pending).days if d.oldest_pending else 0,
                        "action": "Coordinate with warehouse for priority dispatch"
                    })
        
        except Exception as e:
            logger.error(f"Error getting dealers needing followup: {e}")
        
        return dealers
    
    def get_critical_delay_actions(self, limit: int = 10) -> List[Dict]:
        """Get critical actions for delays"""
        actions = []
        
        try:
            # Get most delayed DNs
            delayed_dns = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.pgi_status
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).order_by(
                DeliveryReport.dn_create_date
            ).limit(limit).all()
            
            for dn in delayed_dns:
                aging = self.business_rules.calculate_pending_delivery_aging(dn)
                
                action = {
                    "dn_no": dn.dn_no,
                    "dealer": dn.customer_name,
                    "value": float(dn.dn_amount or 0),
                    "aging_days": aging
                }
                
                if aging > 15:
                    action["severity"] = "CRITICAL"
                    action["action"] = "ESCALATE TO REGIONAL MANAGER"
                elif aging > 7:
                    action["severity"] = "HIGH"
                    action["action"] = "URGENT - Coordinate with warehouse"
                else:
                    action["severity"] = "MEDIUM"
                    action["action"] = "Follow up with warehouse"
                
                actions.append(action)
        
        except Exception as e:
            logger.error(f"Error getting critical delay actions: {e}")
        
        return actions
    
    def get_inventory_recommendations(self) -> List[Dict]:
        """Get inventory-related recommendations"""
        recommendations = []
        
        try:
            # Get top products by demand
            top_products = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_demand"),
                func.count(DeliveryReport.dn_no).label("order_count")
            ).group_by(
                DeliveryReport.product
            ).order_by(
                desc("total_demand")
            ).limit(10).all()
            
            for p in top_products:
                recommendations.append({
                    "product": p.product,
                    "recommendation": "High demand product - Ensure adequate stock",
                    "demand_qty": float(p.total_demand or 0),
                    "priority": "HIGH"
                })
            
            # Get slow-moving products
            slow_products = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_demand"),
                func.count(DeliveryReport.dn_no).label("order_count")
            ).group_by(
                DeliveryReport.product
            ).order_by(
                desc("total_demand")
            ).offset(50).limit(10).all()
            
            for p in slow_products:
                recommendations.append({
                    "product": p.product,
                    "recommendation": "Slow moving - Consider discount or bundle",
                    "demand_qty": float(p.total_demand or 0),
                    "priority": "LOW"
                })
        
        except Exception as e:
            logger.error(f"Error getting inventory recommendations: {e}")
        
        return recommendations
    
    def get_process_improvements(self) -> List[Dict]:
        """Get process improvement recommendations"""
        improvements = []
        
        try:
            # Check warehouse performance
            warehouse_performance = self.db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.dn_no).label("total"),
                func.sum(case((DeliveryReport.pgi_status == "Completed", 1), else_=0)).label("completed")
            ).group_by(
                DeliveryReport.warehouse
            ).all()
            
            for wh in warehouse_performance:
                if wh.total > 0:
                    completion_rate = (wh.completed / wh.total) * 100
                    if completion_rate < 70:
                        improvements.append({
                            "area": f"Warehouse: {wh.warehouse}",
                            "issue": f"Low completion rate: {completion_rate:.1f}%",
                            "recommendation": "Review warehouse processes and staffing",
                            "priority": "HIGH"
                        })
            
            # Check POD collection
            total_delivered = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 1
            
            pod_received = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pod_status == "Received"
            ).scalar() or 0
            
            pod_rate = (pod_received / total_delivered) * 100
            
            if pod_rate < 80:
                improvements.append({
                    "area": "POD Collection",
                    "issue": f"Low POD collection rate: {pod_rate:.1f}%",
                    "recommendation": "Implement automated POD reminders and tracking",
                    "priority": "HIGH"
                })
        
        except Exception as e:
            logger.error(f"Error getting process improvements: {e}")
        
        return improvements
    
    def get_priority_actions(self) -> List[Dict]:
        """Get prioritized action items"""
        all_actions = []
        
        # Combine all recommendations with priority
        for d in self.get_dealers_needing_followup(5):
            all_actions.append({
                "type": "DEALER_FOLLOWUP",
                "priority": self._get_priority_from_value(d.get("pending_value", 0)),
                "item": d
            })
        
        for a in self.get_critical_delay_actions(5):
            all_actions.append({
                "type": "DELAY_ACTION",
                "priority": a.get("severity", "MEDIUM"),
                "item": a
            })
        
        # Sort by priority
        priority_order = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}
        all_actions.sort(key=lambda x: priority_order.get(x.get("priority", "LOW"), 5))
        
        return all_actions[:10]
    
    def _get_priority_from_value(self, value: float) -> str:
        """Get priority based on value"""
        if value > 1000000:
            return "CRITICAL"
        elif value > 500000:
            return "HIGH"
        elif value > 100000:
            return "MEDIUM"
        else:
            return "LOW"


def case(when, then, else_=0):
    from sqlalchemy import case as sa_case
    return sa_case(when, then, else_=else_)
