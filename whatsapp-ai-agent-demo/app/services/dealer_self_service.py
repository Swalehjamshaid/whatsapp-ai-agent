# ==========================================================
# FILE: app/services/dealer_self_service.py
# ==========================================================
# DEALER SELF-SERVICE PORTAL
# ==========================================================

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.models import DeliveryReport
from app.services.business_rules_service import BusinessRulesService


class DealerSelfService:
    """Dealer self-service portal for WhatsApp"""
    
    def __init__(self, db: Session):
        self.db = db
        self.business_rules = BusinessRulesService()
    
    def get_my_dashboard(self, dealer_name: str, question: str = None) -> Dict[str, Any]:
        """Get dealer's personal dashboard"""
        if not dealer_name:
            return {"error": "Dealer not identified. Please register your phone number."}
        
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.customer_name == dealer_name
        ).all()
        
        if not records:
            return {"error": f"No records found for dealer: {dealer_name}"}
        
        # Determine what to show based on question
        question_lower = (question or "").lower()
        
        if "pod" in question_lower:
            return self.get_my_pods(dealer_name)
        elif "sales" in question_lower:
            return self.get_my_sales(dealer_name)
        elif "dn" in question_lower or "order" in question_lower:
            return self.get_my_dns(dealer_name)
        else:
            return self._get_full_dashboard(dealer_name, records)
    
    def get_my_dns(self, dealer_name: str, limit: int = 10) -> List[Dict]:
        """Get dealer's DNs"""
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.delivery_date
            ).filter(
                DeliveryReport.customer_name == dealer_name
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(limit).all()
            
            dns = []
            for r in results:
                dns.append({
                    "dn_no": r.dn_no,
                    "value": float(r.dn_amount or 0),
                    "created_date": r.dn_create_date,
                    "status": self.business_rules.get_delivery_status(r.pgi_status, r.pod_status).value,
                    "delivered_date": r.delivery_date
                })
            
            return {"dealer": dealer_name, "dns": dns, "total": len(dns)}
        
        except Exception as e:
            logger.error(f"Error getting dealer DNs: {e}")
            return {"error": "Unable to fetch DNs"}
    
    def get_my_sales(self, dealer_name: str, days: int = 30) -> Dict[str, Any]:
        """Get dealer's sales summary"""
        try:
            cutoff_date = date.today() - timedelta(days=days)
            
            # Total sales
            total_sales = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.dn_create_date >= cutoff_date
            ).scalar() or 0
            
            # Completed sales
            completed_sales = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.dn_create_date >= cutoff_date
            ).scalar() or 0
            
            # Pending POD value
            pending_pod = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            
            return {
                "dealer": dealer_name,
                "period_days": days,
                "total_sales": float(total_sales),
                "completed_sales": float(completed_sales),
                "pending_pod_value": float(pending_pod),
                "realization_rate": (float(completed_sales - pending_pod) / float(total_sales) * 100) if total_sales > 0 else 0
            }
        
        except Exception as e:
            logger.error(f"Error getting dealer sales: {e}")
            return {"error": "Unable to fetch sales data"}
    
    def get_my_pods(self, dealer_name: str) -> List[Dict]:
        """Get dealer's pending PODs"""
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_amount,
                DeliveryReport.delivery_date,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).order_by(
                DeliveryReport.delivery_date
            ).all()
            
            pods = []
            for r in results:
                pending_days = self.business_rules.calculate_pending_pod_aging(r)
                pods.append({
                    "dn_no": r.dn_no,
                    "value": float(r.dn_amount or 0),
                    "delivery_date": r.delivery_date,
                    "pending_days": pending_days,
                    "status": "URGENT" if pending_days > 7 else "PENDING"
                })
            
            return {"dealer": dealer_name, "pending_pods": pods, "total": len(pods)}
        
        except Exception as e:
            logger.error(f"Error getting dealer PODs: {e}")
            return {"error": "Unable to fetch POD data"}
    
    def _get_full_dashboard(self, dealer_name: str, records) -> Dict[str, Any]:
        """Get full dashboard for dealer"""
        total_dns = len(set(r.dn_no for r in records))
        total_value = sum(float(r.dn_amount or 0) for r in records)
        completed_dns = len([r for r in records if r.pgi_status == "Completed"])
        pod_pending = len([r for r in records if r.pgi_status == "Completed" and r.pod_status == "Pending"])
        
        completion_rate = (completed_dns / total_dns * 100) if total_dns > 0 else 0
        
        # Calculate recent activity (last 30 days)
        cutoff_date = date.today() - timedelta(days=30)
        recent_value = sum(float(r.dn_amount or 0) for r in records 
                          if r.dn_create_date and r.dn_create_date >= cutoff_date)
        
        return {
            "dealer": dealer_name,
            "total_dns": total_dns,
            "total_value": total_value,
            "completed_dns": completed_dns,
            "completion_rate": completion_rate,
            "pod_pending": pod_pending,
            "recent_30d_value": recent_value,
            "health_score": completion_rate,  # Simplified health score
            "pending_pods_count": pod_pending
        }
