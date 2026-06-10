# ==========================================================
# FILE: app/services/logistics_query_service.py (INTEGRATED v3.1)
# ==========================================================
# PURPOSE: Logistics Data Processing - DN Intelligence, POD, PGI
# ==========================================================

from typing import Dict, Any, Optional, List
from datetime import datetime, date
from sqlalchemy import text
from sqlalchemy.orm import Session
from loguru import logger


class LogisticsQueryService:
    def __init__(self, db: Session):
        self.db = db
        logger.info("Logistics Query Service initialized")
    
    def get_complete_dn_intelligence(self, dn_number: str) -> Dict[str, Any]:
        """Get complete DN intelligence."""
        logger.info(f"Getting DN intelligence for: {dn_number}")
        
        try:
            # Try to get from database
            query = text("""
                SELECT 
                    dn_number, dn_date, dealer_name, dealer_city, dealer_region,
                    amount, status, pod_status, pgi_status, warehouse_name,
                    shipment_date
                FROM dn_master 
                WHERE dn_number = :dn_number
            """)
            result = self.db.execute(query, {"dn_number": dn_number}).fetchone()
            
            if not result:
                return {
                    "dn_number": dn_number,
                    "status": "Not Found",
                    "summary": f"DN {dn_number} not found in system. Please check the number and try again.",
                    "error": "Not found"
                }
            
            row = dict(result._mapping)
            
            # Calculate aging
            aging_days = 0
            if row.get('shipment_date'):
                if isinstance(row['shipment_date'], date):
                    aging_days = (date.today() - row['shipment_date']).days
            
            # Determine priority
            if row.get('pod_status') == 'PENDING':
                if aging_days > 7:
                    priority = "Critical"
                elif aging_days > 3:
                    priority = "High"
                else:
                    priority = "Medium"
            else:
                priority = "Normal"
            
            return {
                "dn_number": row.get('dn_number'),
                "date": str(row.get('dn_date')) if row.get('dn_date') else "N/A",
                "status": row.get('status', 'Active'),
                "status_priority": priority,
                "customer_name": row.get('dealer_name', 'Unknown'),
                "city": row.get('dealer_city', 'Unknown'),
                "region": row.get('dealer_region', 'Unknown'),
                "amount": float(row.get('amount', 0)),
                "items_count": 0,
                "warehouse": row.get('warehouse_name', 'Unknown'),
                "pod_status": row.get('pod_status', 'PENDING'),
                "pgi_status": row.get('pgi_status', 'PENDING'),
                "aging_days": aging_days,
                "summary": f"DN {dn_number} is {row.get('pod_status', 'PENDING')}. Shipped {aging_days} days ago."
            }
            
        except Exception as e:
            logger.error(f"DN intelligence error: {e}")
            return {"error": str(e), "dn_number": dn_number}
    
    def get_dn_timeline(self, dn_number: str) -> List[Dict]:
        """Get DN timeline."""
        return [{"status": "Created", "date": "N/A", "message": "Timeline data not available"}]
    
    def get_dn_products(self, dn_number: str) -> List[Dict]:
        """Get DN products."""
        return []
    
    def get_pod_status(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Get POD status."""
        return {"pending_count": 0, "completed_today": 0, "avg_aging": 0}
    
    def get_pending_pgi(self, days: Optional[int] = None) -> List[Dict]:
        """Get pending PGI."""
        return []
    
    def get_pending_pods(self, days: Optional[int] = None) -> List[Dict]:
        """Get pending PODs."""
        return []
    
    def get_pending_items(self, region: Optional[str] = None) -> Dict:
        """Get all pending items."""
        return {"total_pending": 0, "pending_pods": 0, "pending_pgi": 0, "top_dealers": []}
    
    def get_region_performance(self, region: Optional[str] = None) -> Dict:
        """Get region performance."""
        return {"region": region or "All", "total_dns": 0, "success_rate": 0}
    
    def health_check(self) -> Dict:
        return {"service": "logistics", "status": "healthy"}
