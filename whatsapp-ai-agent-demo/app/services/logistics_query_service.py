# ==========================================================
# FILE: app/services/logistics_query_service.py (UPDATED v4.0)
# ==========================================================

from typing import Dict, Any, Optional, List
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from loguru import logger

from app.models import DeliveryReport


class LogisticsQueryService:
    def __init__(self, db: Session):
        self.db = db
        logger.info("Logistics Query Service initialized v4.0")
    
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
                "region": result.division or "N/A",
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
        """Get DN timeline"""
        logger.info(f"Getting DN timeline for: {dn_number}")
        
        try:
            result = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).first()
            
            if not result:
                return []
            
            timeline = []
            
            # DN Created
            if result.dn_create_date:
                timeline.append({
                    "status": "DN Created",
                    "date": str(result.dn_create_date),
                    "remarks": f"DN {dn_number} created",
                    "location": result.ship_to_city or "N/A"
                })
            
            # Goods Issue (PGI)
            if result.good_issue_date:
                timeline.append({
                    "status": "Goods Issue (PGI)",
                    "date": str(result.good_issue_date),
                    "remarks": "Goods issued from warehouse",
                    "location": result.warehouse or "N/A"
                })
            
            # POD Received
            if result.pod_date:
                timeline.append({
                    "status": "POD Received",
                    "date": str(result.pod_date),
                    "remarks": "Proof of Delivery received",
                    "location": result.ship_to_city or "N/A"
                })
            
            return timeline if timeline else [{
                "status": "Information Only",
                "date": "N/A",
                "remarks": "Detailed timeline not available",
                "location": "N/A"
            }]
            
        except Exception as e:
            logger.error(f"Error getting DN timeline: {e}")
            return []
    
    def get_dn_products(self, dn_number: str) -> List[Dict]:
        """Get DN products"""
        logger.info(f"Getting DN products for: {dn_number}")
        
        try:
            result = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).first()
            
            if not result:
                return []
            
            products = []
            
            if result.material_no:
                products.append({
                    "product_code": result.material_no,
                    "product_name": result.customer_model or "N/A",
                    "quantity": result.dn_qty or 0,
                    "unit_price": 0,
                    "total_price": float(result.dn_amount or 0),
                    "status": "Shipped"
                })
            
            return products
            
        except Exception as e:
            logger.error(f"Error getting DN products: {e}")
            return []
    
    def get_pod_status(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Get POD status summary"""
        logger.info(f"Getting POD status for region: {region}")
        
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pod_status.in_(['PENDING', 'NOT_RECEIVED'])
            )
            
            if region:
                query = query.filter(DeliveryReport.ship_to_city == region)
            
            pending_count = query.count()
            
            # Get top pending dealer
            top_dealer_data = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.id).label('count')
            ).filter(
                DeliveryReport.pod_status.in_(['PENDING', 'NOT_RECEIVED'])
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.count(DeliveryReport.id).desc()
            ).first()
            
            top_pending_dealer = top_dealer_data[0] if top_dealer_data else "N/A"
            
            # Calculate average aging
            aging_sum = 0
            count = 0
            pending_records = query.all()
            
            for record in pending_records:
                if record.good_issue_date:
                    aging = (date.today() - record.good_issue_date).days
                    aging_sum += aging
                    count += 1
            
            avg_aging = round(aging_sum / count, 1) if count > 0 else 0
            
            return {
                "pending_count": pending_count,
                "completed_today": 0,
                "avg_aging": avg_aging,
                "top_pending_dealer": top_pending_dealer
            }
        except Exception as e:
            logger.error(f"Error getting POD status: {e}")
            return {"pending_count": 0, "completed_today": 0, "avg_aging": 0, "top_pending_dealer": "N/A"}
    
    def get_pending_pgi(self, days: Optional[int] = None) -> List[Dict]:
        """Get pending PGI"""
        logger.info(f"Getting pending PGI for days: {days}")
        
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status == 'PENDING'
            )
            
            if days:
                cutoff_date = date.today() - timedelta(days=days)
                query = query.filter(DeliveryReport.dn_create_date <= cutoff_date)
            
            results = query.limit(50).all()
            
            pending_list = []
            for r in results:
                pending_days = 0
                if r.dn_create_date:
                    pending_days = (date.today() - r.dn_create_date).days
                
                pending_list.append({
                    "dn_number": r.dn_no,
                    "dealer_name": r.customer_name or "Unknown",
                    "amount": float(r.dn_amount or 0),
                    "pending_days": pending_days,
                    "priority": "High" if pending_days > 5 else "Medium" if pending_days > 2 else "Low"
                })
            
            return pending_list
        except Exception as e:
            logger.error(f"Error getting pending PGI: {e}")
            return []
    
    def get_pending_pods(self, days: Optional[int] = None) -> List[Dict]:
        """Get pending PODs"""
        logger.info(f"Getting pending PODs for days: {days}")
        
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pod_status.in_(['PENDING', 'NOT_RECEIVED'])
            )
            
            if days:
                cutoff_date = date.today() - timedelta(days=days)
                query = query.filter(DeliveryReport.good_issue_date <= cutoff_date)
            
            results = query.limit(50).all()
            
            pending_list = []
            for r in results:
                pending_days = 0
                if r.good_issue_date:
                    pending_days = (date.today() - r.good_issue_date).days
                elif r.dn_create_date:
                    pending_days = (date.today() - r.dn_create_date).days
                
                pending_list.append({
                    "dn_number": r.dn_no,
                    "dealer_name": r.customer_name or "Unknown",
                    "city": r.ship_to_city or "Unknown",
                    "amount": float(r.dn_amount or 0),
                    "pending_days": pending_days,
                    "priority": "Critical" if pending_days > 7 else "High" if pending_days > 3 else "Medium"
                })
            
            return pending_list
        except Exception as e:
            logger.error(f"Error getting pending PODs: {e}")
            return []
    
    def get_pending_items(self, region: Optional[str] = None) -> Dict:
        """Get all pending items"""
        logger.info(f"Getting pending items for region: {region}")
        
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pending_flag == True
            )
            
            if region:
                query = query.filter(DeliveryReport.ship_to_city == region)
            
            total_pending = query.count()
            pending_pods = query.filter(DeliveryReport.pod_status == 'PENDING').count()
            pending_pgi = query.filter(DeliveryReport.pgi_status == 'PENDING').count()
            
            # Get top 5 dealers with most pending items
            top_dealers_data = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.id).label('pending_count')
            ).filter(
                DeliveryReport.pending_flag == True
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.count(DeliveryReport.id).desc()
            ).limit(5).all()
            
            top_dealers = [
                {"name": dealer[0] or "Unknown", "pending_count": dealer[1]}
                for dealer in top_dealers_data
            ]
            
            # Calculate high/medium/low priority counts
            high_priority = 0
            medium_priority = 0
            low_priority = 0
            
            for item in query.all():
                aging = 0
                if item.good_issue_date:
                    aging = (date.today() - item.good_issue_date).days
                elif item.dn_create_date:
                    aging = (date.today() - item.dn_create_date).days
                
                if aging > 7:
                    high_priority += 1
                elif aging > 3:
                    medium_priority += 1
                else:
                    low_priority += 1
            
            return {
                "total_pending": total_pending,
                "pending_pods": pending_pods,
                "pending_pgi": pending_pgi,
                "pending_deliveries": 0,
                "high_priority": high_priority,
                "medium_priority": medium_priority,
                "low_priority": low_priority,
                "top_dealers": top_dealers
            }
        except Exception as e:
            logger.error(f"Error getting pending items: {e}")
            return {
                "total_pending": 0, 
                "pending_pods": 0, 
                "pending_pgi": 0,
                "pending_deliveries": 0,
                "high_priority": 0,
                "medium_priority": 0,
                "low_priority": 0,
                "top_dealers": []
            }
    
    def get_region_performance(self, region: Optional[str] = None) -> Dict:
        """Get region performance"""
        logger.info(f"Getting region performance for: {region}")
        
        try:
            query = self.db.query(DeliveryReport)
            
            if region:
                query = query.filter(DeliveryReport.ship_to_city == region)
            
            total_dns = query.count()
            pending = query.filter(DeliveryReport.pod_status == 'PENDING').count()
            completed = query.filter(DeliveryReport.pod_status == 'RECEIVED').count()
            
            # Calculate total value
            total_value = sum(r.dn_amount or 0 for r in query.all())
            
            # Calculate average delivery days
            delivery_days_sum = 0
            delivery_count = 0
            for r in query.all():
                if r.pod_date and r.good_issue_date:
                    days = (r.pod_date - r.good_issue_date).days
                    if days > 0:
                        delivery_days_sum += days
                        delivery_count += 1
            
            avg_delivery_days = round(delivery_days_sum / delivery_count, 1) if delivery_count > 0 else 0
            
            return {
                "region": region or "All",
                "total_dns": total_dns,
                "pending_count": pending,
                "completed_count": completed,
                "success_rate": round((completed / max(1, total_dns)) * 100, 1),
                "total_value": total_value,
                "avg_delivery_days": avg_delivery_days,
                "active_dealers": self.db.query(DeliveryReport.customer_code).distinct().count()
            }
        except Exception as e:
            logger.error(f"Error getting region performance: {e}")
            return {"region": region, "total_dns": 0, "success_rate": 0}
    
    def get_region_information(self, region: str) -> Dict:
        """Alias for get_region_performance"""
        return self.get_region_performance(region)
    
    def get_dealer_performance(self, dealer_name: str) -> Dict:
        """Get performance for a specific dealer"""
        logger.info(f"Getting dealer performance for: {dealer_name}")
        
        try:
            results = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).all()
            
            if not results:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            total_dns = len(results)
            completed_dns = sum(1 for r in results if r.pod_status == 'RECEIVED')
            pending_dns = total_dns - completed_dns
            total_value = sum(r.dn_amount or 0 for r in results)
            
            # Calculate average delivery days
            delivery_days = []
            for r in results:
                if r.pod_date and r.good_issue_date:
                    days = (r.pod_date - r.good_issue_date).days
                    if days > 0:
                        delivery_days.append(days)
            
            avg_delivery_days = round(sum(delivery_days) / len(delivery_days), 1) if delivery_days else 0
            
            return {
                "dealer_name": dealer_name,
                "dealer_city": results[0].ship_to_city if results else "Unknown",
                "dealer_region": results[0].division if results else "Unknown",
                "total_dns": total_dns,
                "completed_dns": completed_dns,
                "pending_count": pending_dns,
                "total_value": total_value,
                "avg_delivery_days": avg_delivery_days,
                "completion_rate": round((completed_dns / max(1, total_dns)) * 100, 1)
            }
        except Exception as e:
            logger.error(f"Error getting dealer performance: {e}")
            return {"error": str(e)}
    
    def get_warehouse_status(self, warehouse_name: str) -> Dict:
        """Get warehouse status"""
        logger.info(f"Getting warehouse status for: {warehouse_name}")
        
        try:
            results = self.db.query(DeliveryReport).filter(
                DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")
            ).all()
            
            if not results:
                return {"error": f"Warehouse '{warehouse_name}' not found"}
            
            total_dns = len(results)
            pending_pgi = sum(1 for r in results if r.pgi_status == 'PENDING')
            completed_pgi = total_dns - pending_pgi
            
            return {
                "warehouse_name": warehouse_name,
                "warehouse_city": results[0].ship_to_city if results else "Unknown",
                "warehouse_region": results[0].division if results else "Unknown",
                "total_dns_handled": total_dns,
                "pgi_completed": completed_pgi,
                "pgi_pending": pending_pgi,
                "capacity_percentage": 65,  # Default value, would come from warehouse table
                "status": "Active",
                "status_icon": "🟢"
            }
        except Exception as e:
            logger.error(f"Error getting warehouse status: {e}")
            return {"error": str(e)}
    
    def health_check(self) -> Dict:
        return {"service": "logistics", "status": "healthy", "version": "4.0"}
