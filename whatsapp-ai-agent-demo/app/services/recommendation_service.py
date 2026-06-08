# ==========================================================
# FILE: app/services/recommendation_service.py (ENTERPRISE v2.0)
# ==========================================================
# RECOMMENDATION SERVICE
# - Dealer follow-up recommendations
# - Critical delay actions
# - Inventory recommendations
# - Process improvements
# ==========================================================

from typing import Dict, Any, List
from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.models import DeliveryReport


class RecommendationService:
    """Actionable Recommendations Engine"""
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        logger.info("✅ Recommendation Service initialized")
    
    def get_recommendations(self) -> Dict[str, Any]:
        """Get all recommendations"""
        return {
            "dealer_followups": self.get_dealers_needing_followup(5),
            "critical_actions": self.get_critical_delay_actions(5),
            "inventory_recommendations": self.get_inventory_recommendations(),
            "priority_actions": self.get_priority_actions()
        }
    
    def get_dealers_needing_followup(self, limit: int = 10) -> List[Dict]:
        """Get dealers that need follow-up"""
        try:
            # Dealers with pending POD > 7 days
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("pending_count"),
                func.sum(DeliveryReport.dn_amount).label("pending_value"),
                func.max(DeliveryReport.delivery_date).label("oldest_pending")
            ).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received",
                DeliveryReport.delivery_date <= date.today() - timedelta(days=7)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("pending_value")
            ).limit(limit).all()
            
            return [{
                "dealer": r.customer_name,
                "reason": "Pending POD >7 days",
                "pending_count": r.pending_count,
                "pending_value": float(r.pending_value or 0),
                "action": "Send POD reminder and call customer"
            } for r in results if r.customer_name]
        except Exception as e:
            logger.error(f"Dealers needing followup error: {e}")
            return []
    
    def get_critical_delay_actions(self, limit: int = 10) -> List[Dict]:
        """Get critical actions for delays"""
        try:
            records = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= date.today() - timedelta(days=7)
            ).order_by(
                DeliveryReport.dn_create_date
            ).limit(limit).all()
            
            actions = []
            for r in records:
                aging = (date.today() - r.dn_create_date).days if r.dn_create_date else 0
                actions.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "value": float(r.dn_amount or 0),
                    "aging_days": aging,
                    "severity": "CRITICAL" if aging > 15 else "HIGH",
                    "action": "Escalate to regional manager" if aging > 15 else "Coordinate with warehouse"
                })
            return actions
        except Exception as e:
            logger.error(f"Critical delay actions error: {e}")
            return []
    
    def get_inventory_recommendations(self) -> List[Dict]:
        """Get inventory-related recommendations"""
        recommendations = []
        
        try:
            # Fast moving products (high demand)
            fast_moving = self.db.query(
                DeliveryReport.material_no,
                func.sum(DeliveryReport.dn_qty).label("total_demand")
            ).filter(
                DeliveryReport.material_no.isnot(None)
            ).group_by(
                DeliveryReport.material_no
            ).order_by(
                desc("total_demand")
            ).limit(5).all()
            
            for p in fast_moving:
                if p.material_no:
                    recommendations.append({
                        "product": p.material_no,
                        "recommendation": "High demand - Ensure adequate stock",
                        "priority": "HIGH"
                    })
            
            # Slow moving products (last 90 days)
            cutoff_date = date.today() - timedelta(days=90)
            slow_moving = self.db.query(
                DeliveryReport.material_no,
                func.sum(DeliveryReport.dn_qty).label("total_demand")
            ).filter(
                DeliveryReport.material_no.isnot(None),
                DeliveryReport.dn_create_date >= cutoff_date
            ).group_by(
                DeliveryReport.material_no
            ).order_by(
                desc("total_demand")
            ).offset(20).limit(5).all()
            
            for p in slow_moving:
                if p.material_no:
                    recommendations.append({
                        "product": p.material_no,
                        "recommendation": "Slow moving - Consider discount or bundle",
                        "priority": "LOW"
                    })
        except Exception as e:
            logger.error(f"Inventory recommendations error: {e}")
        
        return recommendations
    
    def get_priority_actions(self) -> List[Dict]:
        """Get prioritized action items"""
        all_actions = []
        
        # Add dealer follow-ups
        for d in self.get_dealers_needing_followup(5):
            all_actions.append({
                "type": "DEALER_FOLLOWUP",
                "priority": self._get_priority_from_value(d.get("pending_value", 0)),
                "item": d
            })
        
        # Add critical delays
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
        if value > 5_000_000:
            return "CRITICAL"
        elif value > 1_000_000:
            return "HIGH"
        elif value > 500_000:
            return "MEDIUM"
        else:
            return "LOW"


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_recommendation_service(db: Session, cache_service=None) -> RecommendationService:
    return RecommendationService(db, cache_service)
