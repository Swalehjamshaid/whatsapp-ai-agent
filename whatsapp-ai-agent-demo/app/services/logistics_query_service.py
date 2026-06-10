# ==========================================================
# FILE: app/services/logistics_query_service.py
# VERSION: 3.0
# PURPOSE: All Logistics Data Processing - DN Intelligence, POD, PGI, Delivery, Dealer, Warehouse
# ARCHITECTURE: ai_query_service → logistics_query_service
# ==========================================================

import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta, date
from collections import defaultdict, Counter
from sqlalchemy import text, func, and_, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql import bindparam
from loguru import logger

# ==========================================================
# DATABASE MODELS (Fallback if ORM models not available)
# ==========================================================

# These are SQL table definitions - adjust based on your actual schema
class LogisticsQueries:
    """Container for raw SQL queries"""
    
    # DN Master Queries
    DN_DETAILS = """
        SELECT 
            dn.dn_number,
            dn.dn_date,
            dn.dealer_code,
            dn.dealer_name,
            dn.dealer_city,
            dn.dealer_region,
            dn.amount,
            dn.status,
            dn.created_at,
            dn.updated_at,
            dn.pod_status,
            dn.pod_date,
            dn.pod_reference,
            dn.pgi_status,
            dn.pgi_date,
            dn.pgi_reference,
            dn.shipment_date,
            dn.delivery_date,
            dn.delivery_status,
            dn.warehouse_code,
            dn.warehouse_name
        FROM dn_master dn
        WHERE dn.dn_number = :dn_number
    """
    
    DN_PRODUCTS = """
        SELECT 
            dp.product_code,
            dp.product_name,
            dp.quantity,
            dp.unit_price,
            dp.total_price,
            dp.discount,
            dp.tax_amount,
            dp.net_amount,
            dp.batch_number,
            dp.expiry_date
        FROM dn_products dp
        WHERE dp.dn_number = :dn_number
    """
    
    DN_TIMELINE = """
        SELECT 
            dt.status,
            dt.status_date,
            dt.remarks,
            dt.user_name,
            dt.location
        FROM dn_timeline dt
        WHERE dt.dn_number = :dn_number
        ORDER BY dt.status_date DESC
    """
    
    # Pending POD Queries
    PENDING_PODS = """
        SELECT 
            dn.dn_number,
            dn.dn_date,
            dn.dealer_name,
            dn.dealer_city,
            dn.dealer_region,
            dn.amount,
            DATEDIFF(CURRENT_DATE, dn.shipment_date) as aging_days,
            dn.warehouse_name,
            dn.shipment_date
        FROM dn_master dn
        WHERE dn.pod_status IN ('PENDING', 'NOT_RECEIVED', 'DELAYED')
            AND dn.status NOT IN ('CANCELLED', 'COMPLETED')
        {region_filter}
        ORDER BY aging_days DESC, dn.dn_date ASC
        LIMIT :limit
    """
    
    POD_AGING_SUMMARY = """
        SELECT 
            CASE 
                WHEN DATEDIFF(CURRENT_DATE, dn.shipment_date) <= 3 THEN '0-3 Days'
                WHEN DATEDIFF(CURRENT_DATE, dn.shipment_date) <= 7 THEN '4-7 Days'
                WHEN DATEDIFF(CURRENT_DATE, dn.shipment_date) <= 14 THEN '8-14 Days'
                ELSE '15+ Days'
            END as aging_bucket,
            COUNT(*) as count,
            SUM(dn.amount) as total_amount,
            AVG(DATEDIFF(CURRENT_DATE, dn.shipment_date)) as avg_aging
        FROM dn_master dn
        WHERE dn.pod_status IN ('PENDING', 'NOT_RECEIVED')
            AND dn.shipment_date IS NOT NULL
        GROUP BY aging_bucket
        ORDER BY MIN(DATEDIFF(CURRENT_DATE, dn.shipment_date))
    """
    
    # Pending PGI Queries
    PENDING_PGI = """
        SELECT 
            dn.dn_number,
            dn.dn_date,
            dn.dealer_name,
            dn.dealer_city,
            dn.amount,
            DATEDIFF(CURRENT_DATE, dn.dn_date) as aging_days,
            dn.warehouse_name,
            dn.order_priority
        FROM dn_master dn
        WHERE dn.pgi_status IN ('PENDING', 'NOT_PROCESSED')
            AND dn.status NOT IN ('CANCELLED')
        {region_filter}
        ORDER BY aging_days DESC, dn.order_priority ASC
        LIMIT :limit
    """
    
    # Pending Deliveries
    PENDING_DELIVERIES = """
        SELECT 
            d.delivery_id,
            d.dn_number,
            d.dealer_name,
            d.dealer_address,
            d.dealer_city,
            d.assigned_driver,
            d.driver_phone,
            d.vehicle_number,
            d.scheduled_date,
            d.dispatch_date,
            DATEDIFF(CURRENT_DATE, d.dispatch_date) as aging_days,
            d.delivery_status,
            d.warehouse_name
        FROM deliveries d
        WHERE d.delivery_status IN ('DISPATCHED', 'IN_TRANSIT', 'ASSIGNED')
            AND d.actual_delivery_date IS NULL
        {region_filter}
        ORDER BY aging_days DESC
        LIMIT :limit
    """
    
    # Dealer Performance
    DEALER_PERFORMANCE = """
        SELECT 
            dn.dealer_code,
            dn.dealer_name,
            dn.dealer_city,
            dn.dealer_region,
            COUNT(*) as total_dns,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
            SUM(CASE WHEN dn.pod_status IN ('PENDING', 'NOT_RECEIVED') THEN 1 ELSE 0 END) as pending_dns,
            SUM(dn.amount) as total_value,
            AVG(CASE WHEN dn.pod_date IS NOT NULL 
                THEN DATEDIFF(dn.pod_date, dn.shipment_date) 
                ELSE NULL END) as avg_delivery_days,
            MAX(dn.dn_date) as last_order_date,
            SUM(CASE WHEN dn.pgi_status = 'COMPLETED' THEN 1 ELSE 0 END) as completed_pgi
        FROM dn_master dn
        WHERE dn.dealer_code = :dealer_code
        GROUP BY dn.dealer_code, dn.dealer_name, dn.dealer_city, dn.dealer_region
    """
    
    # Dealer Ranking
    DEALER_RANKING = """
        SELECT 
            dn.dealer_code,
            dn.dealer_name,
            dn.dealer_city,
            dn.dealer_region,
            COUNT(*) as total_dns,
            SUM(dn.amount) as total_value,
            AVG(DATEDIFF(CURRENT_DATE, dn.dn_date)) as avg_aging,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
            RANK() OVER (ORDER BY SUM(dn.amount) DESC) as value_rank,
            RANK() OVER (ORDER BY COUNT(*) DESC) as volume_rank,
            RANK() OVER (ORDER BY AVG(DATEDIFF(CURRENT_DATE, dn.dn_date)) ASC) as efficiency_rank
        FROM dn_master dn
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        {region_filter}
        GROUP BY dn.dealer_code, dn.dealer_name, dn.dealer_city, dn.dealer_region
        ORDER BY total_value DESC
        LIMIT :limit
    """
    
    # Warehouse Performance
    WAREHOUSE_PERFORMANCE = """
        SELECT 
            w.warehouse_code,
            w.warehouse_name,
            w.warehouse_city,
            w.warehouse_region,
            w.capacity_total,
            w.capacity_used,
            ROUND((w.capacity_used / w.capacity_total) * 100, 2) as capacity_percentage,
            COUNT(DISTINCT dn.dn_number) as total_dns_handled,
            SUM(CASE WHEN dn.pgi_status = 'COMPLETED' THEN 1 ELSE 0 END) as pgi_completed,
            SUM(CASE WHEN dn.pgi_status = 'PENDING' THEN 1 ELSE 0 END) as pgi_pending,
            AVG(DATEDIFF(dn.pgi_date, dn.dn_date)) as avg_pgi_processing_days,
            COUNT(DISTINCT d.delivery_id) as total_deliveries,
            AVG(DATEDIFF(d.actual_delivery_date, d.dispatch_date)) as avg_delivery_time
        FROM warehouses w
        LEFT JOIN dn_master dn ON w.warehouse_code = dn.warehouse_code
        LEFT JOIN deliveries d ON w.warehouse_code = d.warehouse_code
        WHERE w.warehouse_code = :warehouse_code
        GROUP BY w.warehouse_code, w.warehouse_name, w.warehouse_city, 
                 w.warehouse_region, w.capacity_total, w.capacity_used
    """
    
    # Warehouse List
    WAREHOUSES_LIST = """
        SELECT 
            warehouse_code,
            warehouse_name,
            warehouse_city,
            warehouse_region,
            capacity_total,
            capacity_used,
            ROUND((capacity_used / capacity_total) * 100, 2) as capacity_percentage,
            manager_name,
            contact_number
        FROM warehouses
        WHERE 1=1
        {city_filter}
        ORDER BY warehouse_name
    """
    
    # Region Performance
    REGION_PERFORMANCE = """
        SELECT 
            dn.dealer_region as region,
            COUNT(DISTINCT dn.dealer_code) as active_dealers,
            COUNT(*) as total_dns,
            SUM(dn.amount) as total_value,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
            SUM(CASE WHEN dn.pod_status IN ('PENDING', 'NOT_RECEIVED') THEN 1 ELSE 0 END) as pending_dns,
            AVG(CASE WHEN dn.pod_date IS NOT NULL 
                THEN DATEDIFF(dn.pod_date, dn.shipment_date) 
                ELSE NULL END) as avg_delivery_days,
            SUM(CASE WHEN dn.pgi_status = 'COMPLETED' THEN 1 ELSE 0 END) as completed_pgi
        FROM dn_master dn
        WHERE dn.dealer_region = :region
            AND dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        GROUP BY dn.dealer_region
    """
    
    # Daily Shipments Trend
    DAILY_SHIPMENTS = """
        SELECT 
            ds.shipment_date,
            COUNT(*) as total_shipments,
            SUM(ds.quantity) as total_quantity,
            SUM(ds.value) as total_value,
            COUNT(DISTINCT ds.dealer_code) as unique_dealers,
            SUM(CASE WHEN ds.status = 'DELIVERED' THEN 1 ELSE 0 END) as delivered_count
        FROM daily_shipments ds
        WHERE ds.shipment_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        {warehouse_filter}
        GROUP BY ds.shipment_date
        ORDER BY ds.shipment_date DESC
    """
    
    # Top Products
    TOP_PRODUCTS = """
        SELECT 
            p.product_code,
            p.product_name,
            p.category,
            SUM(dp.quantity) as total_quantity,
            SUM(dp.net_amount) as total_value,
            COUNT(DISTINCT dp.dn_number) as order_count
        FROM dn_products dp
        JOIN products p ON dp.product_code = p.product_code
        WHERE dp.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        GROUP BY p.product_code, p.product_name, p.category
        ORDER BY total_value DESC
        LIMIT :limit
    """


# ==========================================================
# MAIN LOGISTICS QUERY SERVICE
# ==========================================================

class LogisticsQueryService:
    """
    All Logistics Data Processing Service
    Handles DN Intelligence, POD, PGI, Delivery, Dealer, and Warehouse queries
    """
    
    def __init__(self, db: Session):
        self.db = db
        logger.info("Logistics Query Service initialized (v3.0)")
    
    # ==========================================================
    # DN INTELLIGENCE METHODS
    # ==========================================================
    
    def get_dn_details(self, dn_number: str) -> Optional[Dict[str, Any]]:
        """
        Get basic DN details from master table
        
        Args:
            dn_number: Delivery Note number
        
        Returns:
            Dictionary with DN details or None
        """
        try:
            query = text(LogisticsQueries.DN_DETAILS)
            result = self.db.execute(query, {"dn_number": dn_number}).fetchone()
            
            if result:
                return dict(result._mapping)
            return None
            
        except Exception as e:
            logger.error(f"Error getting DN details for {dn_number}: {e}")
            return None
    
    def get_dn_products(self, dn_number: str) -> List[Dict[str, Any]]:
        """
        Get products in a DN
        
        Args:
            dn_number: Delivery Note number
        
        Returns:
            List of products with details
        """
        try:
            query = text(LogisticsQueries.DN_PRODUCTS)
            results = self.db.execute(query, {"dn_number": dn_number}).fetchall()
            
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting DN products for {dn_number}: {e}")
            return []
    
    def get_dn_timeline(self, dn_number: str) -> List[Dict[str, Any]]:
        """
        Get timeline/status history for a DN
        
        Args:
            dn_number: Delivery Note number
        
        Returns:
            List of timeline events
        """
        try:
            query = text(LogisticsQueries.DN_TIMELINE)
            results = self.db.execute(query, {"dn_number": dn_number}).fetchall()
            
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting DN timeline for {dn_number}: {e}")
            return []
    
    def get_complete_dn_intelligence(self, dn_number: str) -> Dict[str, Any]:
        """
        Get complete intelligence for a DN including details, products, timeline
        
        This is the main method called by ai_query_service.py
        
        Args:
            dn_number: Delivery Note number
        
        Returns:
            Complete DN intelligence report
        """
        logger.info(f"Getting complete DN intelligence for: {dn_number}")
        
        try:
            # Get basic details
            details = self.get_dn_details(dn_number)
            if not details:
                return {"error": f"DN {dn_number} not found"}
            
            # Get products
            products = self.get_dn_products(dn_number)
            
            # Get timeline
            timeline = self.get_dn_timeline(dn_number)
            
            # Calculate aging
            aging_days = 0
            if details.get('shipment_date'):
                shipment_date = details['shipment_date']
                if isinstance(shipment_date, date):
                    aging_days = (date.today() - shipment_date).days
            
            # Determine status priority
            status_priority = "Normal"
            if details.get('pod_status') == 'PENDING':
                if aging_days > 7:
                    status_priority = "Critical"
                elif aging_days > 3:
                    status_priority = "High"
                else:
                    status_priority = "Medium"
            
            # Build intelligence report
            intelligence = {
                "dn_number": dn_number,
                "date": str(details.get('dn_date')) if details.get('dn_date') else "N/A",
                "status": details.get('status', 'N/A'),
                "customer_name": details.get('dealer_name', 'N/A'),
                "customer_code": details.get('dealer_code', 'N/A'),
                "city": details.get('dealer_city', 'N/A'),
                "region": details.get('dealer_region', 'N/A'),
                "amount": float(details.get('amount', 0)),
                "items_count": len(products),
                "weight": "Not specified",  # Would come from products table
                "pending_items": sum(1 for p in products if p.get('status') == 'PENDING'),
                "pod_status": details.get('pod_status', 'N/A'),
                "pod_date": str(details.get('pod_date')) if details.get('pod_date') else "Not received",
                "pod_reference": details.get('pod_reference', 'N/A'),
                "pgi_status": details.get('pgi_status', 'N/A'),
                "pgi_date": str(details.get('pgi_date')) if details.get('pgi_date') else "Not processed",
                "shipment_date": str(details.get('shipment_date')) if details.get('shipment_date') else "Not shipped",
                "delivery_date": str(details.get('delivery_date')) if details.get('delivery_date') else "Not delivered",
                "delivery_status": details.get('delivery_status', 'N/A'),
                "warehouse": details.get('warehouse_name', 'N/A'),
                "aging_days": aging_days,
                "status_priority": status_priority,
                "products": products[:5] if products else [],  # Top 5 products
                "timeline": timeline[:10] if timeline else [],  # Last 10 events
                "summary": self._generate_dn_summary(details, aging_days, len(products))
            }
            
            return intelligence
            
        except Exception as e:
            logger.exception(f"Error getting DN intelligence for {dn_number}: {e}")
            return {"error": f"Error retrieving DN {dn_number}: {str(e)}"}
    
    def _generate_dn_summary(self, details: Dict, aging_days: int, items_count: int) -> str:
        """Generate human-readable summary for DN"""
        if not details:
            return "No information available"
        
        status = details.get('pod_status', 'UNKNOWN')
        
        if status == 'RECEIVED':
            return f"DN {details.get('dn_number')} has been delivered and POD received."
        elif status == 'PENDING':
            if aging_days > 7:
                return f"DN {details.get('dn_number')} is critically delayed by {aging_days} days. Immediate attention required."
            elif aging_days > 3:
                return f"DN {details.get('dn_number')} is delayed by {aging_days} days. Follow up required."
            else:
                return f"DN {details.get('dn_number')} is pending POD. Shipped {aging_days} days ago."
        else:
            return f"DN {details.get('dn_number')} is {status}. Contact logistics for details."
    
    # ==========================================================
    # POD (Proof of Delivery) METHODS
    # ==========================================================
    
    def get_pending_pods(self, region: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get pending PODs with aging
        
        Args:
            region: Optional region filter
            limit: Maximum number of records
        
        Returns:
            List of pending PODs with details
        """
        try:
            region_filter = ""
            params = {"limit": limit}
            
            if region:
                region_filter = "AND dn.dealer_region = :region"
                params["region"] = region
            
            query = text(LogisticsQueries.PENDING_PODS.format(region_filter=region_filter))
            results = self.db.execute(query, params).fetchall()
            
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting pending PODs: {e}")
            return []
    
    def get_pod_aging_summary(self) -> Dict[str, Any]:
        """
        Get POD aging summary by buckets
        
        Returns:
            Aging summary with counts and amounts
        """
        try:
            query = text(LogisticsQueries.POD_AGING_SUMMARY)
            results = self.db.execute(query).fetchall()
            
            summary = {
                "buckets": [],
                "total_pending": 0,
                "total_amount": 0,
                "max_aging_days": 0
            }
            
            for row in results:
                row_dict = dict(row._mapping)
                summary["buckets"].append(row_dict)
                summary["total_pending"] += row_dict.get("count", 0)
                summary["total_amount"] += row_dict.get("total_amount", 0)
            
            if summary["buckets"] and summary["buckets"][-1].get("avg_aging"):
                summary["max_aging_days"] = summary["buckets"][-1].get("avg_aging", 0)
            
            return summary
            
        except Exception as e:
            logger.error(f"Error getting POD aging summary: {e}")
            return {"buckets": [], "total_pending": 0, "total_amount": 0, "max_aging_days": 0}
    
    def get_pod_status(self, region: Optional[str] = None) -> Dict[str, Any]:
        """
        Get comprehensive POD status summary
        
        This method is called by ai_query_service.py for POD queries
        
        Args:
            region: Optional region filter
        
        Returns:
            POD status summary
        """
        logger.info(f"Getting POD status for region: {region}")
        
        try:
            pending_pods = self.get_pending_pods(region, 100)
            aging_summary = self.get_pod_aging_summary()
            
            # Calculate additional metrics
            completed_today = 0
            top_pending_dealer = None
            
            if pending_pods:
                # Count pending by dealer
                dealer_pending = defaultdict(int)
                for pod in pending_pods:
                    dealer_pending[pod.get('dealer_name', 'Unknown')] += 1
                
                if dealer_pending:
                    top_pending_dealer = max(dealer_pending, key=dealer_pending.get)
                
                # Calculate average aging
                aging_days = [p.get('aging_days', 0) for p in pending_pods if p.get('aging_days')]
                avg_aging = sum(aging_days) / len(aging_days) if aging_days else 0
            else:
                avg_aging = 0
            
            return {
                "pending_count": len(pending_pods),
                "completed_today": completed_today,
                "avg_aging": round(avg_aging, 1),
                "top_pending_dealer": top_pending_dealer or "N/A",
                "aging_summary": aging_summary,
                "pending_list": pending_pods[:10]  # Top 10 for details
            }
            
        except Exception as e:
            logger.error(f"Error getting POD status: {e}")
            return {
                "pending_count": 0,
                "completed_today": 0,
                "avg_aging": 0,
                "top_pending_dealer": "N/A",
                "aging_summary": {},
                "pending_list": []
            }
    
    # ==========================================================
    # PGI (Packing/Goods Issue) METHODS
    # ==========================================================
    
    def get_pending_pgi(self, region: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get pending PGI (Packing/Goods Issue)
        
        Args:
            region: Optional region filter
            limit: Maximum number of records
        
        Returns:
            List of pending PGI items
        """
        try:
            region_filter = ""
            params = {"limit": limit}
            
            if region:
                region_filter = "AND dn.dealer_region = :region"
                params["region"] = region
            
            query = text(LogisticsQueries.PENDING_PGI.format(region_filter=region_filter))
            results = self.db.execute(query, params).fetchall()
            
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting pending PGI: {e}")
            return []
    
    def get_pgi_performance(self, region: Optional[str] = None) -> Dict[str, Any]:
        """
        Get PGI performance metrics
        
        Args:
            region: Optional region filter
        
        Returns:
            PGI performance summary
        """
        logger.info(f"Getting PGI performance for region: {region}")
        
        try:
            pending_pgi = self.get_pending_pgi(region, 100)
            
            if pending_pgi:
                aging_days = [p.get('aging_days', 0) for p in pending_pgi if p.get('aging_days')]
                avg_aging = sum(aging_days) / len(aging_days) if aging_days else 0
                
                # Count by priority
                high_priority = sum(1 for p in pending_pgi if p.get('order_priority') == 'HIGH')
                medium_priority = sum(1 for p in pending_pgi if p.get('order_priority') == 'MEDIUM')
                low_priority = sum(1 for p in pending_pgi if p.get('order_priority') == 'LOW')
            else:
                avg_aging = 0
                high_priority = medium_priority = low_priority = 0
            
            return {
                "pending_count": len(pending_pgi),
                "avg_aging_days": round(avg_aging, 1),
                "high_priority": high_priority,
                "medium_priority": medium_priority,
                "low_priority": low_priority,
                "pending_list": pending_pgi[:10]
            }
            
        except Exception as e:
            logger.error(f"Error getting PGI performance: {e}")
            return {
                "pending_count": 0,
                "avg_aging_days": 0,
                "high_priority": 0,
                "medium_priority": 0,
                "low_priority": 0,
                "pending_list": []
            }
    
    # ==========================================================
    # DELIVERY METHODS
    # ==========================================================
    
    def get_pending_deliveries(self, region: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get pending deliveries
        
        Args:
            region: Optional region filter
            limit: Maximum number of records
        
        Returns:
            List of pending deliveries
        """
        try:
            region_filter = ""
            params = {"limit": limit}
            
            if region:
                region_filter = "AND d.dealer_city = :region"
                params["region"] = region
            
            query = text(LogisticsQueries.PENDING_DELIVERIES.format(region_filter=region_filter))
            results = self.db.execute(query, params).fetchall()
            
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting pending deliveries: {e}")
            return []
    
    def get_delivery_performance(self, region: Optional[str] = None) -> Dict[str, Any]:
        """
        Get delivery performance metrics
        
        Args:
            region: Optional region filter
        
        Returns:
            Delivery performance summary
        """
        logger.info(f"Getting delivery performance for region: {region}")
        
        try:
            pending = self.get_pending_deliveries(region, 100)
            
            if pending:
                aging_days = [d.get('aging_days', 0) for d in pending if d.get('aging_days')]
                avg_aging = sum(aging_days) / len(aging_days) if aging_days else 0
                
                # Count delayed deliveries (aging > 3 days)
                delayed = sum(1 for d in pending if d.get('aging_days', 0) > 3)
            else:
                avg_aging = 0
                delayed = 0
            
            return {
                "pending_count": len(pending),
                "avg_aging_days": round(avg_aging, 1),
                "delayed_count": delayed,
                "pending_list": pending[:10]
            }
            
        except Exception as e:
            logger.error(f"Error getting delivery performance: {e}")
            return {
                "pending_count": 0,
                "avg_aging_days": 0,
                "delayed_count": 0,
                "pending_list": []
            }
    
    # ==========================================================
    # DEALER METHODS
    # ==========================================================
    
    def get_dealer_information(self, dealer_code: str) -> Optional[Dict[str, Any]]:
        """
        Get dealer information
        
        Args:
            dealer_code: Dealer code or name
        
        Returns:
            Dealer details
        """
        try:
            # Try by code first, then by name
            query = text("""
                SELECT 
                    dealer_code,
                    dealer_name,
                    dealer_city,
                    dealer_region,
                    dealer_address,
                    contact_person,
                    contact_number,
                    email,
                    gst_number,
                    credit_limit,
                    outstanding_amount,
                    account_status,
                    registration_date
                FROM dealers
                WHERE dealer_code = :dealer_code OR dealer_name LIKE :dealer_name
                LIMIT 1
            """)
            
            result = self.db.execute(
                query, 
                {"dealer_code": dealer_code, "dealer_name": f"%{dealer_code}%"}
            ).fetchone()
            
            if result:
                return dict(result._mapping)
            return None
            
        except Exception as e:
            logger.error(f"Error getting dealer info for {dealer_code}: {e}")
            return None
    
    def get_dealer_performance(self, dealer_code: str, days: int = 90) -> Dict[str, Any]:
        """
        Get dealer performance metrics
        
        This method is called by ai_query_service.py for dealer queries
        
        Args:
            dealer_code: Dealer code or name
            days: Number of days to analyze
        
        Returns:
            Dealer performance metrics
        """
        logger.info(f"Getting dealer performance for: {dealer_code}")
        
        try:
            # First get dealer info
            dealer_info = self.get_dealer_information(dealer_code)
            if not dealer_info:
                return {"error": f"Dealer {dealer_code} not found"}
            
            # Get performance metrics
            query = text(LogisticsQueries.DEALER_PERFORMANCE)
            result = self.db.execute(
                query, 
                {"dealer_code": dealer_info.get('dealer_code')}
            ).fetchone()
            
            if not result:
                return {
                    "dealer_name": dealer_info.get('dealer_name'),
                    "dealer_code": dealer_info.get('dealer_code'),
                    "total_dns": 0,
                    "pending_count": 0,
                    "completed_count": 0,
                    "total_value": 0,
                    "avg_aging": 0
                }
            
            row = dict(result._mapping)
            
            # Get recent orders
            recent_orders = self.get_dealer_recent_orders(dealer_info.get('dealer_code'), 5)
            
            return {
                "dealer_name": row.get('dealer_name'),
                "dealer_code": row.get('dealer_code'),
                "dealer_city": row.get('dealer_city'),
                "dealer_region": row.get('dealer_region'),
                "total_dns": row.get('total_dns', 0),
                "pending_count": row.get('pending_dns', 0),
                "completed_count": row.get('completed_dns', 0),
                "total_value": float(row.get('total_value', 0)),
                "avg_delivery_days": round(row.get('avg_delivery_days', 0), 1),
                "last_order_date": str(row.get('last_order_date')) if row.get('last_order_date') else "N/A",
                "completed_pgi": row.get('completed_pgi', 0),
                "recent_orders": recent_orders
            }
            
        except Exception as e:
            logger.error(f"Error getting dealer performance: {e}")
            return {"error": str(e)}
    
    def get_dealer_recent_orders(self, dealer_code: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent orders for a dealer"""
        try:
            query = text("""
                SELECT 
                    dn_number,
                    dn_date,
                    amount,
                    pod_status,
                    shipment_date
                FROM dn_master
                WHERE dealer_code = :dealer_code
                ORDER BY dn_date DESC
                LIMIT :limit
            """)
            
            results = self.db.execute(query, {"dealer_code": dealer_code, "limit": limit}).fetchall()
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting recent orders: {e}")
            return []
    
    def get_dealer_ranking(self, region: Optional[str] = None, days: int = 90, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get dealer ranking by performance
        
        Args:
            region: Optional region filter
            days: Analysis period in days
            limit: Number of dealers to return
        
        Returns:
            List of dealers with rankings
        """
        try:
            region_filter = ""
            params = {"days": days, "limit": limit}
            
            if region:
                region_filter = "AND dn.dealer_region = :region"
                params["region"] = region
            
            query = text(LogisticsQueries.DEALER_RANKING.format(region_filter=region_filter))
            results = self.db.execute(query, params).fetchall()
            
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting dealer ranking: {e}")
            return []
    
    # ==========================================================
    # WAREHOUSE METHODS
    # ==========================================================
    
    def get_warehouse_information(self, warehouse_code: str) -> Optional[Dict[str, Any]]:
        """
        Get warehouse information
        
        Args:
            warehouse_code: Warehouse code or name
        
        Returns:
            Warehouse details
        """
        try:
            # Try by code first, then by name
            query = text("""
                SELECT 
                    warehouse_code,
                    warehouse_name,
                    warehouse_city,
                    warehouse_region,
                    capacity_total,
                    capacity_used,
                    manager_name,
                    contact_number,
                    address,
                    operating_hours,
                    status
                FROM warehouses
                WHERE warehouse_code = :code OR warehouse_name LIKE :name
                LIMIT 1
            """)
            
            result = self.db.execute(
                query,
                {"code": warehouse_code, "name": f"%{warehouse_code}%"}
            ).fetchone()
            
            if result:
                return dict(result._mapping)
            return None
            
        except Exception as e:
            logger.error(f"Error getting warehouse info for {warehouse_code}: {e}")
            return None
    
    def get_warehouse_status(self, warehouse_code: str) -> Dict[str, Any]:
        """
        Get warehouse status and performance metrics
        
        This method is called by ai_query_service.py for warehouse queries
        
        Args:
            warehouse_code: Warehouse code or name
        
        Returns:
            Warehouse status and metrics
        """
        logger.info(f"Getting warehouse status for: {warehouse_code}")
        
        try:
            # Get warehouse info
            warehouse = self.get_warehouse_information(warehouse_code)
            if not warehouse:
                return {"error": f"Warehouse {warehouse_code} not found"}
            
            # Get performance metrics
            query = text(LogisticsQueries.WAREHOUSE_PERFORMANCE)
            result = self.db.execute(
                query,
                {"warehouse_code": warehouse.get('warehouse_code')}
            ).fetchone()
            
            if result:
                metrics = dict(result._mapping)
            else:
                metrics = {}
            
            # Get current pending items
            pending_pgi = self.get_pending_pgi()
            warehouse_pending = [p for p in pending_pgi if p.get('warehouse_name') == warehouse.get('warehouse_name')]
            
            return {
                "warehouse_name": warehouse.get('warehouse_name'),
                "warehouse_code": warehouse.get('warehouse_code'),
                "warehouse_city": warehouse.get('warehouse_city'),
                "warehouse_region": warehouse.get('warehouse_region'),
                "capacity_total": warehouse.get('capacity_total', 0),
                "capacity_used": warehouse.get('capacity_used', 0),
                "capacity_percentage": metrics.get('capacity_percentage', 0),
                "total_dns_handled": metrics.get('total_dns_handled', 0),
                "pgi_completed": metrics.get('pgi_completed', 0),
                "pgi_pending": metrics.get('pgi_pending', 0),
                "avg_pgi_processing_days": round(metrics.get('avg_pgi_processing_days', 0), 1),
                "total_deliveries": metrics.get('total_deliveries', 0),
                "avg_delivery_time": round(metrics.get('avg_delivery_time', 0), 1),
                "current_pending_pgi": len(warehouse_pending),
                "status": warehouse.get('status', 'ACTIVE'),
                "manager": warehouse.get('manager_name', 'N/A'),
                "contact": warehouse.get('contact_number', 'N/A')
            }
            
        except Exception as e:
            logger.error(f"Error getting warehouse status: {e}")
            return {"error": str(e)}
    
    def get_warehouse_list(self, city: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get list of all warehouses
        
        Args:
            city: Optional city filter
        
        Returns:
            List of warehouses
        """
        try:
            city_filter = ""
            params = {}
            
            if city:
                city_filter = "AND warehouse_city = :city"
                params["city"] = city
            
            query = text(LogisticsQueries.WAREHOUSES_LIST.format(city_filter=city_filter))
            results = self.db.execute(query, params).fetchall()
            
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting warehouse list: {e}")
            return []
    
    # ==========================================================
    # REGION METHODS
    # ==========================================================
    
    def get_region_information(self, region: str, days: int = 90) -> Dict[str, Any]:
        """
        Get region performance information
        
        This method is called by ai_query_service.py for region queries
        
        Args:
            region: Region name
            days: Analysis period in days
        
        Returns:
            Region performance metrics
        """
        logger.info(f"Getting region information for: {region}")
        
        try:
            query = text(LogisticsQueries.REGION_PERFORMANCE)
            result = self.db.execute(query, {"region": region, "days": days}).fetchone()
            
            if not result:
                return {"error": f"Region {region} not found or no data available"}
            
            row = dict(result._mapping)
            
            # Calculate success rate
            total_dns = row.get('total_dns', 0)
            completed_dns = row.get('completed_dns', 0)
            success_rate = (completed_dns / total_dns * 100) if total_dns > 0 else 0
            
            return {
                "region": row.get('region'),
                "active_dealers": row.get('active_dealers', 0),
                "total_dns": total_dns,
                "total_value": float(row.get('total_value', 0)),
                "completed_dns": completed_dns,
                "pending_dns": row.get('pending_dns', 0),
                "success_rate": round(success_rate, 1),
                "avg_delivery_days": round(row.get('avg_delivery_days', 0), 1),
                "completed_pgi": row.get('completed_pgi', 0)
            }
            
        except Exception as e:
            logger.error(f"Error getting region information: {e}")
            return {"error": str(e)}
    
    def get_region_performance(self, region: Optional[str] = None) -> Dict[str, Any]:
        """
        Get region performance summary
        
        Args:
            region: Optional region name
        
        Returns:
            Region performance metrics
        """
        logger.info(f"Getting region performance for: {region or 'all regions'}")
        
        try:
            if region:
                return self.get_region_information(region)
            
            # Get all regions summary
            query = text("""
                SELECT 
                    dealer_region as region,
                    COUNT(DISTINCT dealer_code) as active_dealers,
                    COUNT(*) as total_dns,
                    SUM(amount) as total_value,
                    SUM(CASE WHEN pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
                    AVG(CASE WHEN pod_date IS NOT NULL 
                        THEN DATEDIFF(pod_date, shipment_date) 
                        ELSE NULL END) as avg_delivery_days
                FROM dn_master
                WHERE dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL 90 DAY)
                    AND dealer_region IS NOT NULL
                GROUP BY dealer_region
                ORDER BY total_value DESC
            """)
            
            results = self.db.execute(query).fetchall()
            
            regions = []
            for row in results:
                row_dict = dict(row._mapping)
                total_dns = row_dict.get('total_dns', 0)
                completed_dns = row_dict.get('completed_dns', 0)
                success_rate = (completed_dns / total_dns * 100) if total_dns > 0 else 0
                
                regions.append({
                    "region": row_dict.get('region'),
                    "active_dealers": row_dict.get('active_dealers', 0),
                    "total_dns": total_dns,
                    "total_value": float(row_dict.get('total_value', 0)),
                    "success_rate": round(success_rate, 1),
                    "avg_delivery_days": round(row_dict.get('avg_delivery_days', 0), 1)
                })
            
            return {
                "regions": regions,
                "total_regions": len(regions),
                "total_dns": sum(r['total_dns'] for r in regions),
                "total_value": sum(r['total_value'] for r in regions)
            }
            
        except Exception as e:
            logger.error(f"Error getting region performance: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # PENDING ITEMS METHODS
    # ==========================================================
    
    def get_pending_items(self, region: Optional[str] = None) -> Dict[str, Any]:
        """
        Get all pending items across POD, PGI, and Deliveries
        
        This method is called by ai_query_service.py for pending queries
        
        Args:
            region: Optional region filter
        
        Returns:
            Comprehensive pending items summary
        """
        logger.info(f"Getting pending items for region: {region}")
        
        try:
            pending_pods = self.get_pending_pods(region, 100)
            pending_pgi = self.get_pending_pgi(region, 100)
            pending_deliveries = self.get_pending_deliveries(region, 100)
            
            # Categorize by priority
            high_priority = []
            medium_priority = []
            low_priority = []
            
            for pod in pending_pods:
                aging = pod.get('aging_days', 0)
                if aging > 7:
                    high_priority.append(pod)
                elif aging > 3:
                    medium_priority.append(pod)
                else:
                    low_priority.append(pod)
            
            # Get top dealers with pending items
            dealer_pending = defaultdict(lambda: {'pods': 0, 'pgi': 0, 'deliveries': 0})
            
            for pod in pending_pods:
                dealer_pending[pod.get('dealer_name', 'Unknown')]['pods'] += 1
            
            for pgi in pending_pgi:
                dealer_pending[pgi.get('dealer_name', 'Unknown')]['pgi'] += 1
            
            for delivery in pending_deliveries:
                dealer_pending[delivery.get('dealer_name', 'Unknown')]['deliveries'] += 1
            
            top_dealers = []
            for dealer, counts in sorted(dealer_pending.items(), 
                                        key=lambda x: x[1]['pods'] + x[1]['pgi'] + x[1]['deliveries'], 
                                        reverse=True)[:5]:
                top_dealers.append({
                    'name': dealer,
                    'pending_count': counts['pods'] + counts['pgi'] + counts['deliveries'],
                    'pods': counts['pods'],
                    'pgi': counts['pgi'],
                    'deliveries': counts['deliveries']
                })
            
            return {
                "total_pending": len(pending_pods) + len(pending_pgi) + len(pending_deliveries),
                "pending_pods": len(pending_pods),
                "pending_pgi": len(pending_pgi),
                "pending_deliveries": len(pending_deliveries),
                "high_priority": len(high_priority),
                "medium_priority": len(medium_priority),
                "low_priority": len(low_priority),
                "top_dealers": top_dealers,
                "details": {
                    "pods": pending_pods[:5],
                    "pgi": pending_pgi[:5],
                    "deliveries": pending_deliveries[:5]
                }
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
                "top_dealers": [],
                "details": {}
            }
    
    # ==========================================================
    # TRENDS & ANALYTICS METHODS
    # ==========================================================
    
    def get_daily_shipments_trend(self, days: int = 30, warehouse: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get daily shipments trend
        
        Args:
            days: Number of days to analyze
            warehouse: Optional warehouse filter
        
        Returns:
            Daily shipment data
        """
        try:
            warehouse_filter = ""
            params = {"days": days}
            
            if warehouse:
                warehouse_filter = "AND ds.warehouse_code = :warehouse"
                params["warehouse"] = warehouse
            
            query = text(LogisticsQueries.DAILY_SHIPMENTS.format(warehouse_filter=warehouse_filter))
            results = self.db.execute(query, params).fetchall()
            
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting daily shipments trend: {e}")
            return []
    
    def get_top_products(self, days: int = 30, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get top products by value and quantity
        
        Args:
            days: Analysis period in days
            limit: Number of products to return
        
        Returns:
            List of top products
        """
        try:
            query = text(LogisticsQueries.TOP_PRODUCTS)
            results = self.db.execute(query, {"days": days, "limit": limit}).fetchall()
            
            return [dict(row._mapping) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting top products: {e}")
            return []


# ==========================================================
# COMPATIBILITY FUNCTIONS (Called by ai_query_service.py)
# ==========================================================

def get_complete_dn_intelligence(dn_number: str, db: Session) -> Dict[str, Any]:
    """Compatibility function for DN intelligence"""
    service = LogisticsQueryService(db)
    return service.get_complete_dn_intelligence(dn_number)

def get_pending_pods(db: Session, region: Optional[str] = None) -> Dict[str, Any]:
    """Compatibility function for pending PODs"""
    service = LogisticsQueryService(db)
    return service.get_pod_status(region)

def get_dealer_performance(dealer_code: str, db: Session) -> Dict[str, Any]:
    """Compatibility function for dealer performance"""
    service = LogisticsQueryService(db)
    return service.get_dealer_performance(dealer_code)

def get_warehouse_status(warehouse_code: str, db: Session) -> Dict[str, Any]:
    """Compatibility function for warehouse status"""
    service = LogisticsQueryService(db)
    return service.get_warehouse_status(warehouse_code)

def get_pending_items(db: Session, region: Optional[str] = None) -> Dict[str, Any]:
    """Compatibility function for pending items"""
    service = LogisticsQueryService(db)
    return service.get_pending_items(region)

def get_region_performance(db: Session, region: Optional[str] = None) -> Dict[str, Any]:
    """Compatibility function for region performance"""
    service = LogisticsQueryService(db)
    return service.get_region_performance(region)


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📦 Logistics Query Service v3.0 Loaded")
logger.info("   Features: DN Intelligence | POD | PGI | Delivery | Dealer | Warehouse | Region")
logger.info("=" * 60)
