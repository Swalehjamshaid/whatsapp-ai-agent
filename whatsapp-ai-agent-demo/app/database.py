# ==========================================================
# FILE: app/services/logistics_query_service.py (UPDATED)
# ==========================================================

from typing import Dict, Any, Optional, List
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from loguru import logger

from app.models import DeliveryReport


class LogisticsQueryService:
    def __init__(self, db: Session):
        self.db = db
        logger.info("Logistics Query Service initialized")
    
    def get_complete_dn_intelligence(self, dn_number: str) -> Dict[str, Any]:
        """Get complete DN intelligence"""
        logger.info(f"Getting DN intelligence for: {dn_number}")
        
        try:
            result = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).first()
            
            if not result:
                return {
                    "dn_number": dn_number,
                    "status": "Not Found",
                    "summary": f"DN {dn_number} not found in system.",
                    "error": "Not found"
                }
            
            # Calculate aging
            aging_days = 0
            if result.good_issue_date:
                aging_days = (date.today() - result.good_issue_date).days
            elif result.dn_create_date:
                aging_days = (date.today() - result.dn_create_date).days
            
            # Determine priority
            pod_status = result.pod_status or "PENDING"
            if pod_status == "PENDING":
                if aging_days > 7:
                    priority = "Critical"
                elif aging_days > 3:
                    priority = "High"
                else:
                    priority = "Medium"
            else:
                priority = "Normal"
            
            return {
                "dn_number": result.dn_no,
                "date": str(result.dn_create_date) if result.dn_create_date else "N/A",
                "status": result.delivery_status or "Active",
                "status_priority": priority,
                "customer_name": result.customer_name or "Unknown",
                "customer_code": result.customer_code or "Unknown",
                "city": result.ship_to_city or "Unknown",
                "region": "N/A",
                "amount": float(result.dn_amount or 0),
                "items_count": result.dn_qty or 0,
                "warehouse": result.warehouse or "Unknown",
                "pod_status": pod_status,
                "pod_date": str(result.pod_date) if result.pod_date else "Not received",
                "pgi_status": result.pgi_status or "PENDING",
                "pgi_date": str(result.good_issue_date) if result.good_issue_date else "Not processed",
                "shipment_date": str(result.good_issue_date) if result.good_issue_date else "Not shipped",
                "delivery_status": result.delivery_status or "PENDING",
                "warehouse_code": result.warehouse_code or "N/A",
                "aging_days": aging_days,
                "summary": f"DN {dn_number} is {pod_status}. Aged {aging_days} days."
            }
            
        except Exception as e:
            logger.error(f"DN intelligence error: {e}")
            return {"error": str(e), "dn_number": dn_number}
    
    def get_dn_timeline(self, dn_number: str) -> List[Dict]:
        """Get DN timeline (placeholder)"""
        return [{"status": "Created", "date": "N/A", "message": "Timeline data not available"}]
    
    def get_dn_products(self, dn_number: str) -> List[Dict]:
        """Get DN products (placeholder)"""
        return []
    
    def get_pod_status(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Get POD status summary"""
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pod_status.in_(['PENDING', 'NOT_RECEIVED'])
            )
            
            if region:
                query = query.filter(DeliveryReport.ship_to_city == region)
            
            pending_count = query.count()
            
            return {
                "pending_count": pending_count,
                "completed_today": 0,
                "avg_aging": 0,
                "top_pending_dealer": "N/A"
            }
        except Exception as e:
            logger.error(f"Error getting POD status: {e}")
            return {"pending_count": 0, "completed_today": 0, "avg_aging": 0}
    
    def get_pending_pgi(self, days: Optional[int] = None) -> List[Dict]:
        """Get pending PGI"""
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status == 'PENDING'
            )
            
            if days:
                cutoff_date = date.today() - timedelta(days=days)
                query = query.filter(DeliveryReport.dn_create_date <= cutoff_date)
            
            results = query.limit(50).all()
            
            return [
                {
                    "dn_number": r.dn_no,
                    "dealer_name": r.customer_name,
                    "amount": float(r.dn_amount or 0),
                    "pending_days": (date.today() - r.dn_create_date).days if r.dn_create_date else 0
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Error getting pending PGI: {e}")
            return []
    
    def get_pending_pods(self, days: Optional[int] = None) -> List[Dict]:
        """Get pending PODs"""
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pod_status.in_(['PENDING', 'NOT_RECEIVED'])
            )
            
            if days:
                cutoff_date = date.today() - timedelta(days=days)
                query = query.filter(DeliveryReport.good_issue_date <= cutoff_date)
            
            results = query.limit(50).all()
            
            return [
                {
                    "dn_number": r.dn_no,
                    "dealer_name": r.customer_name,
                    "city": r.ship_to_city,
                    "amount": float(r.dn_amount or 0),
                    "pending_days": (date.today() - r.good_issue_date).days if r.good_issue_date else 0
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Error getting pending PODs: {e}")
            return []
    
    def get_pending_items(self, region: Optional[str] = None) -> Dict:
        """Get all pending items"""
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pending_flag == True
            )
            
            if region:
                query = query.filter(DeliveryReport.ship_to_city == region)
            
            pending_count = query.count()
            
            return {
                "total_pending": pending_count,
                "pending_pods": query.filter(DeliveryReport.pod_status == 'PENDING').count(),
                "pending_pgi": query.filter(DeliveryReport.pgi_status == 'PENDING').count(),
                "top_dealers": []
            }
        except Exception as e:
            logger.error(f"Error getting pending items: {e}")
            return {"total_pending": 0, "pending_pods": 0, "pending_pgi": 0, "top_dealers": []}
    
    def get_region_performance(self, region: Optional[str] = None) -> Dict:
        """Get region performance"""
        try:
            query = self.db.query(DeliveryReport)
            
            if region:
                query = query.filter(DeliveryReport.ship_to_city == region)
            
            total_dns = query.count()
            pending = query.filter(DeliveryReport.pod_status == 'PENDING').count()
            completed = total_dns - pending
            
            return {
                "region": region or "All",
                "total_dns": total_dns,
                "pending_count": pending,
                "completed_count": completed,
                "success_rate": round((completed / max(1, total_dns)) * 100, 1),
                "total_value": sum(r.dn_amount or 0 for r in query.all())
            }
        except Exception as e:
            logger.error(f"Error getting region performance: {e}")
            return {"region": region, "total_dns": 0, "success_rate": 0}
    
    def health_check(self) -> Dict:
        return {"service": "logistics", "status": "healthy"}
