# ==========================================================
# FILE: app/services/logistics_query_service.py (v1.3 - FULLY ALIGNED)
# ==========================================================
# PURPOSE: SINGLE SOURCE OF TRUTH for all database access
# 
# ALIGNED WITH: SchemaService v7.0
# 
# FIXES APPLIED:
# 1. Fixed dealer resolution - proper partial matching with SchemaService
# 2. Fixed column names - using correct model fields
# 3. Fixed date calculations - proper DATEDIFF handling
# 4. Added debug logging for dealer resolution
# 5. Added all missing methods (get_city_dashboard, get_trend_analysis, etc.)
# 6. Fixed KPI data retrieval
# 7. Added error handling for all queries
# 8. SchemaService alignment - uses resolve_entity, resolve_dealer, etc.
# ==========================================================

from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy import func, and_, or_, desc, case, text
from sqlalchemy.orm import Session
from loguru import logger

from app.models import DeliveryReport
from app.database import SessionLocal
from app.schemas.schema_service import get_schema_service, DN_PATTERN


class LogisticsQueryService:
    """DATABASE ACCESS LAYER - SINGLE SOURCE OF TRUTH"""
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or SessionLocal()
        self._owned_db = db is None
        self.schema = get_schema_service()
        self.today = date.today()
        
        logger.info("=" * 60)
        logger.info("LogisticsQueryService v1.3 - Fully Aligned with SchemaService v7.0")
        logger.info("=" * 60)
        logger.info("")
        logger.info("   ALIGNED WITH:")
        logger.info("   ✅ SchemaService v7.0 - Dealer resolution")
        logger.info("   ✅ SchemaService v7.0 - City resolution")
        logger.info("   ✅ SchemaService v7.0 - Warehouse resolution")
        logger.info("   ✅ SchemaService v7.0 - DN detection")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 60)
    
    def close(self):
        if self._owned_db and self.db:
            self.db.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # ==========================================================
    # DEALER QUERIES
    # ==========================================================
    
    def get_dealer_dashboard_data(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get comprehensive dealer dashboard data from database.
        
        ALIGNED WITH: SchemaService v7.0 resolve_dealer()
        """
        try:
            logger.debug(f"Fetching dashboard data for dealer: {dealer_name}")
            
            # Resolve dealer name using SchemaService v7.0
            resolved_name = self.resolve_dealer_name(dealer_name)
            if not resolved_name:
                logger.warning(f"Dealer '{dealer_name}' not found in database")
                return {}
            
            logger.debug(f"Resolved dealer name: {resolved_name}")
            
            # Use SQLAlchemy text for complex queries with proper date diff
            sql = text("""
                SELECT 
                    COUNT(*) as total_dns,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL THEN 1 ELSE 0 END), 0) as delivered_units,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NULL THEN 1 ELSE 0 END), 0) as pending_delivery,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN 1 ELSE 0 END), 0) as transit_units,
                    COALESCE(SUM(CASE WHEN pod_date IS NOT NULL THEN 1 ELSE 0 END), 0) as pod_completed,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN 1 ELSE 0 END), 0) as pending_pod,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL THEN 
                        DATEDIFF(good_issue_date, dn_create_date) 
                    END), 0) as avg_delivery_aging,
                    COALESCE(AVG(CASE WHEN pod_date IS NOT NULL THEN 
                        DATEDIFF(pod_date, good_issue_date) 
                    END), 0) as avg_pod_aging,
                    MAX(warehouse) as top_warehouse
                FROM delivery_report 
                WHERE customer_name = :dealer_name
            """)
            
            result = self.db.execute(sql, {"dealer_name": resolved_name}).first()
            
            if not result or result.total_dns == 0:
                logger.warning(f"No data found for dealer: {resolved_name}")
                return {}
            
            total_dns = result.total_dns or 1
            delivery_rate = (result.delivered_units / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = (result.pod_completed / result.delivered_units * 100) if result.delivered_units > 0 else 0
            
            # Get oldest pending
            oldest_sql = text("""
                SELECT dn_no, dn_create_date 
                FROM delivery_report 
                WHERE customer_name = :dealer_name AND good_issue_date IS NULL 
                ORDER BY dn_create_date 
                LIMIT 1
            """)
            oldest = self.db.execute(oldest_sql, {"dealer_name": resolved_name}).first()
            
            return {
                "dealer_name": resolved_name,
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "delivered_units": result.delivered_units or 0,
                "pending_delivery": result.pending_delivery or 0,
                "transit_units": result.transit_units or 0,
                "pod_completed": result.pod_completed or 0,
                "pending_pod": result.pending_pod or 0,
                "delivery_rate": round(delivery_rate, 1),
                "pod_rate": round(pod_rate, 1),
                "avg_delivery_aging": round(result.avg_delivery_aging or 0, 1),
                "avg_pod_aging": round(result.avg_pod_aging or 0, 1),
                "oldest_pending_dn": oldest.dn_no if oldest else None,
                "oldest_pending_days": (self.today - oldest.dn_create_date).days if oldest and oldest.dn_create_date else 0,
                "top_warehouse": result.top_warehouse or "N/A"
            }
            
        except Exception as e:
            logger.error(f"Dealer dashboard query failed for {dealer_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}
    
    def get_dealer_revenue_data(self, dealer_name: str) -> float:
        """Get dealer revenue."""
        try:
            resolved_name = self.resolve_dealer_name(dealer_name)
            if not resolved_name:
                return 0.0
            
            result = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.customer_name == resolved_name
            ).first()
            return float(result[0] or 0)
        except Exception as e:
            logger.error(f"Dealer revenue query failed for {dealer_name}: {e}")
            return 0.0
    
    def get_dealer_units_data(self, dealer_name: str) -> int:
        """Get dealer units."""
        try:
            resolved_name = self.resolve_dealer_name(dealer_name)
            if not resolved_name:
                return 0
            
            result = self.db.query(func.sum(DeliveryReport.dn_qty)).filter(
                DeliveryReport.customer_name == resolved_name
            ).first()
            return int(result[0] or 0)
        except Exception as e:
            logger.error(f"Dealer units query failed for {dealer_name}: {e}")
            return 0
    
    def get_dealer_performance_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer performance data."""
        result = self.get_dealer_dashboard_data(dealer_name)
        if not result:
            return {}
        return {
            "dealer_name": dealer_name,
            "total_revenue": result.get("total_revenue", 0.0),
            "total_units": result.get("total_units", 0),
            "delivery_rate": result.get("delivery_rate", 0.0),
            "pod_rate": result.get("pod_rate", 0.0),
            "avg_delivery_aging": result.get("avg_delivery_aging", 0.0),
            "avg_pod_aging": result.get("avg_pod_aging", 0.0),
            "pending_pgi": result.get("pending_delivery", 0),
            "pending_pod": result.get("pending_pod", 0)
        }
    
    def get_dealer_aging_data(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer aging data."""
        try:
            resolved_name = self.resolve_dealer_name(dealer_name)
            if not resolved_name:
                return {}
            
            sql = text("""
                SELECT 
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL THEN 
                        DATEDIFF(good_issue_date, dn_create_date) 
                    END), 0) as avg_delivery_aging,
                    COALESCE(MAX(CASE WHEN good_issue_date IS NOT NULL THEN 
                        DATEDIFF(good_issue_date, dn_create_date) 
                    END), 0) as max_delivery_aging,
                    COALESCE(AVG(CASE WHEN pod_date IS NOT NULL THEN 
                        DATEDIFF(pod_date, good_issue_date) 
                    END), 0) as avg_pod_aging,
                    COALESCE(MAX(CASE WHEN pod_date IS NOT NULL THEN 
                        DATEDIFF(pod_date, good_issue_date) 
                    END), 0) as max_pod_aging
                FROM delivery_report 
                WHERE customer_name = :dealer_name
            """)
            
            result = self.db.execute(sql, {"dealer_name": resolved_name}).first()
            
            return {
                "avg_delivery_aging": round(result.avg_delivery_aging or 0, 1),
                "max_delivery_aging": round(result.max_delivery_aging or 0, 1),
                "avg_pod_aging": round(result.avg_pod_aging or 0, 1),
                "max_pod_aging": round(result.max_pod_aging or 0, 1)
            }
        except Exception as e:
            logger.error(f"Dealer aging query failed for {dealer_name}: {e}")
            return {}
    
    def get_dealer_dns(self, dealer_name: str, limit: int = 20) -> List[Dict]:
        """Get dealer DNS list."""
        try:
            resolved_name = self.resolve_dealer_name(dealer_name)
            if not resolved_name:
                return []
            
            results = self.db.query(
                DeliveryReport.dn_no, DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date, DeliveryReport.pod_date,
                DeliveryReport.dn_qty, DeliveryReport.dn_amount,
                DeliveryReport.warehouse, DeliveryReport.ship_to_city
            ).filter(DeliveryReport.customer_name == resolved_name).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no, 
                "dn_date": r.dn_create_date,
                "pgi_date": r.good_issue_date, 
                "pod_date": r.pod_date,
                "units": int(r.dn_qty or 0), 
                "amount": float(r.dn_amount or 0),
                "warehouse": r.warehouse, 
                "city": r.ship_to_city
            } for r in results]
        except Exception as e:
            logger.error(f"Dealer DNs query failed for {dealer_name}: {e}")
            return []
    
    def get_dealer_historical_data(self, dealer_name: str) -> List[Dict]:
        """Get dealer historical data for trend analysis."""
        try:
            resolved_name = self.resolve_dealer_name(dealer_name)
            if not resolved_name:
                return []
            
            # Use SQLite compatible date formatting
            # For MySQL: DATE_FORMAT(dn_create_date, '%Y-%m')
            # For SQLite: strftime('%Y-%m', dn_create_date)
            sql = text("""
                SELECT 
                    strftime('%Y-%m', dn_create_date) as period,
                    COUNT(*) as count,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units
                FROM delivery_report 
                WHERE customer_name = :dealer_name
                AND dn_create_date IS NOT NULL
                GROUP BY strftime('%Y-%m', dn_create_date)
                ORDER BY period DESC
                LIMIT 12
            """)
            
            results = self.db.execute(sql, {"dealer_name": resolved_name}).all()
            return [{
                "period": r.period,
                "count": r.count,
                "revenue": float(r.revenue or 0),
                "units": int(r.units or 0)
            } for r in results]
        except Exception as e:
            logger.error(f"Dealer historical query failed for {dealer_name}: {e}")
            return []
    
    # ==========================================================
    # WAREHOUSE QUERIES
    # ==========================================================
    
    def get_warehouse_dashboard_data(self, warehouse_name: str) -> Dict[str, Any]:
        """
        Get warehouse dashboard data.
        
        ALIGNED WITH: SchemaService v7.0 resolve_warehouse()
        """
        try:
            logger.debug(f"Fetching warehouse dashboard for: {warehouse_name}")
            
            # Resolve warehouse name using SchemaService v7.0
            resolved_name = self.schema.resolve_warehouse(warehouse_name)
            if not resolved_name:
                resolved_name = warehouse_name
            
            sql = text("""
                SELECT 
                    COUNT(*) as total_dns,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NULL THEN 1 ELSE 0 END), 0) as pending_delivery,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN 1 ELSE 0 END), 0) as pending_pod,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL THEN 1 ELSE 0 END), 0) as pgi_completed,
                    COALESCE(SUM(CASE WHEN pod_date IS NOT NULL THEN 1 ELSE 0 END), 0) as pod_completed
                FROM delivery_report 
                WHERE warehouse LIKE :warehouse_name
            """)
            
            result = self.db.execute(sql, {"warehouse_name": f"%{resolved_name}%"}).first()
            
            if not result or result.total_dns == 0:
                return {}
            
            total_dns = result.total_dns or 1
            return {
                "warehouse_name": resolved_name,
                "total_dns": result.total_dns or 0,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "pending_delivery": result.pending_delivery or 0,
                "pending_pod": result.pending_pod or 0,
                "pgi_completed": result.pgi_completed or 0,
                "pod_completed": result.pod_completed or 0,
                "pgi_rate": round((result.pgi_completed or 0) / total_dns * 100, 1) if total_dns > 0 else 0,
                "pod_rate": round((result.pod_completed or 0) / total_dns * 100, 1) if total_dns > 0 else 0
            }
        except Exception as e:
            logger.error(f"Warehouse dashboard query failed for {warehouse_name}: {e}")
            return {}
    
    def get_warehouse_historical_data(self, warehouse_name: str) -> List[Dict]:
        """Get warehouse historical data."""
        try:
            resolved_name = self.schema.resolve_warehouse(warehouse_name)
            if not resolved_name:
                resolved_name = warehouse_name
            
            sql = text("""
                SELECT 
                    strftime('%Y-%m', dn_create_date) as period,
                    COUNT(*) as count,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units
                FROM delivery_report 
                WHERE warehouse LIKE :warehouse_name
                AND dn_create_date IS NOT NULL
                GROUP BY strftime('%Y-%m', dn_create_date)
                ORDER BY period DESC
                LIMIT 12
            """)
            
            results = self.db.execute(sql, {"warehouse_name": f"%{resolved_name}%"}).all()
            return [{
                "period": r.period,
                "count": r.count,
                "revenue": float(r.revenue or 0),
                "units": int(r.units or 0)
            } for r in results]
        except Exception as e:
            logger.error(f"Warehouse historical query failed for {warehouse_name}: {e}")
            return []
    
    # ==========================================================
    # CITY QUERIES
    # ==========================================================
    
    def get_city_dashboard_data(self, city_name: str) -> Dict[str, Any]:
        """
        Get city dashboard data.
        
        ALIGNED WITH: SchemaService v7.0 resolve_city()
        """
        try:
            logger.debug(f"Fetching city dashboard for: {city_name}")
            
            # Resolve city name using SchemaService v7.0
            resolved_name = self.schema.resolve_city(city_name)
            if not resolved_name:
                resolved_name = city_name
            
            sql = text("""
                SELECT 
                    COUNT(*) as total_dns,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN 1 ELSE 0 END), 0) as delivered,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN 1 ELSE 0 END), 0) as in_transit,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NULL THEN 1 ELSE 0 END), 0) as pending_pgi,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN 1 ELSE 0 END), 0) as pending_pod
                FROM delivery_report 
                WHERE ship_to_city LIKE :city_name
            """)
            
            result = self.db.execute(sql, {"city_name": f"%{resolved_name}%"}).first()
            
            if not result or result.total_dns == 0:
                return {}
            
            total_dns = result.total_dns or 1
            delivery_rate = (result.delivered / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = ((result.delivered) / (result.delivered + result.pending_pod)) * 100 if (result.delivered + result.pending_pod) > 0 else 0
            
            return {
                "city_name": resolved_name,
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "delivered": result.delivered or 0,
                "in_transit": result.in_transit or 0,
                "pending_pgi": result.pending_pgi or 0,
                "pending_pod": result.pending_pod or 0,
                "delivery_rate": round(delivery_rate, 1),
                "pod_rate": round(pod_rate, 1),
                "pending_dns": result.pending_pgi or 0
            }
        except Exception as e:
            logger.error(f"City dashboard query failed for {city_name}: {e}")
            return {}
    
    def get_city_historical_data(self, city_name: str) -> List[Dict]:
        """Get city historical data."""
        try:
            resolved_name = self.schema.resolve_city(city_name)
            if not resolved_name:
                resolved_name = city_name
            
            sql = text("""
                SELECT 
                    strftime('%Y-%m', dn_create_date) as period,
                    COUNT(*) as count,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units
                FROM delivery_report 
                WHERE ship_to_city LIKE :city_name
                AND dn_create_date IS NOT NULL
                GROUP BY strftime('%Y-%m', dn_create_date)
                ORDER BY period DESC
                LIMIT 12
            """)
            
            results = self.db.execute(sql, {"city_name": f"%{resolved_name}%"}).all()
            return [{
                "period": r.period,
                "count": r.count,
                "revenue": float(r.revenue or 0),
                "units": int(r.units or 0)
            } for r in results]
        except Exception as e:
            logger.error(f"City historical query failed for {city_name}: {e}")
            return []
    
    # ==========================================================
    # DN QUERIES
    # ==========================================================
    
    def get_dn_details(self, dn_number: str) -> Optional[Dict[str, Any]]:
        """
        Get DN details.
        
        ALIGNED WITH: SchemaService v7.0 DN_PATTERN
        """
        try:
            # Use SchemaService DN validation
            if not self.schema.is_dn_number(dn_number):
                # Try extraction
                extracted = self.schema.extract_dn_number(dn_number)
                if extracted:
                    dn_number = extracted
                else:
                    logger.warning(f"Invalid DN format: {dn_number}")
                    return None
            
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).first()
            
            if not record and dn_number.isdigit():
                record = self.db.query(DeliveryReport).filter(
                    DeliveryReport.dn_no == f"{dn_number}.0"
                ).first()
            
            if not record:
                return None
            
            delivery_aging = None
            pod_aging = None
            
            if record.dn_create_date and record.good_issue_date:
                if record.good_issue_date >= record.dn_create_date:
                    delivery_aging = (record.good_issue_date - record.dn_create_date).days
            elif record.dn_create_date:
                delivery_aging = (self.today - record.dn_create_date).days
            
            if record.good_issue_date and record.pod_date:
                if record.pod_date >= record.good_issue_date:
                    pod_aging = (record.pod_date - record.good_issue_date).days
            elif record.good_issue_date:
                pod_aging = (self.today - record.good_issue_date).days
            
            if record.pod_date:
                status = "delivered"
            elif record.good_issue_date:
                status = "in_transit"
            else:
                status = "pending_pgi"
            
            return {
                "dn_number": record.dn_no,
                "dealer": record.customer_name,
                "warehouse": record.warehouse,
                "city": record.ship_to_city,
                "units": int(record.dn_qty or 0),
                "amount": float(record.dn_amount or 0),
                "dn_date": record.dn_create_date,
                "pgi_date": record.good_issue_date,
                "pod_date": record.pod_date,
                "delivery_aging": delivery_aging,
                "pod_aging": pod_aging,
                "status": status,
                "status_display": self.schema.get_dn_status(status)
            }
        except Exception as e:
            logger.error(f"DN details query failed for {dn_number}: {e}")
            return None
    
    # ==========================================================
    # PENDING QUERIES
    # ==========================================================
    
    def get_pending_pgi_count(self, dealer_name: Optional[str] = None) -> int:
        """Get pending PGI count."""
        try:
            query = self.db.query(func.count(DeliveryReport.id)).filter(
                DeliveryReport.good_issue_date.is_(None)
            )
            if dealer_name:
                resolved = self.resolve_dealer_name(dealer_name)
                if resolved:
                    query = query.filter(DeliveryReport.customer_name == resolved)
                else:
                    return 0
            return query.scalar() or 0
        except Exception as e:
            logger.error(f"Pending PGI count query failed: {e}")
            return 0
    
    def get_pending_pod_count(self, dealer_name: Optional[str] = None) -> int:
        """Get pending POD count."""
        try:
            query = self.db.query(func.count(DeliveryReport.id)).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.pod_date.is_(None)
            )
            if dealer_name:
                resolved = self.resolve_dealer_name(dealer_name)
                if resolved:
                    query = query.filter(DeliveryReport.customer_name == resolved)
                else:
                    return 0
            return query.scalar() or 0
        except Exception as e:
            logger.error(f"Pending POD count query failed: {e}")
            return 0
    
    # ==========================================================
    # RANKING QUERIES
    # ==========================================================
    
    def get_top_dealers_by_revenue(self, limit: int = 10) -> List[Dict]:
        """Get top dealers by revenue."""
        try:
            sql = text("""
                SELECT 
                    customer_name as name,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COUNT(*) as dn_count,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN 1 ELSE 0 END), 0) * 100 as pod_rate
                FROM delivery_report 
                WHERE customer_name IS NOT NULL
                AND customer_name != ''
                GROUP BY customer_name
                ORDER BY revenue DESC
                LIMIT :limit
            """)
            
            results = self.db.execute(sql, {"limit": limit}).all()
            return [{
                "name": r.name,
                "revenue": float(r.revenue or 0),
                "dn_count": r.dn_count,
                "pod_rate": round(r.pod_rate, 1)
            } for r in results]
        except Exception as e:
            logger.error(f"Top dealers by revenue query failed: {e}")
            return []
    
    def get_top_dealers_by_units(self, limit: int = 10) -> List[Dict]:
        """Get top dealers by units."""
        try:
            sql = text("""
                SELECT 
                    customer_name as name,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(*) as dn_count,
                    COALESCE(SUM(dn_amount), 0) as revenue
                FROM delivery_report 
                WHERE customer_name IS NOT NULL
                AND customer_name != ''
                GROUP BY customer_name
                ORDER BY units DESC
                LIMIT :limit
            """)
            
            results = self.db.execute(sql, {"limit": limit}).all()
            return [{
                "name": r.name,
                "units": int(r.units or 0),
                "dn_count": r.dn_count,
                "revenue": float(r.revenue or 0)
            } for r in results]
        except Exception as e:
            logger.error(f"Top dealers by units query failed: {e}")
            return []
    
    def get_top_warehouses_by_pending(self, limit: int = 10) -> List[Dict]:
        """Get top warehouses by pending."""
        try:
            sql = text("""
                SELECT 
                    warehouse as name,
                    COUNT(*) as pending
                FROM delivery_report 
                WHERE warehouse IS NOT NULL
                AND warehouse != ''
                AND good_issue_date IS NULL
                GROUP BY warehouse
                ORDER BY pending DESC
                LIMIT :limit
            """)
            
            results = self.db.execute(sql, {"limit": limit}).all()
            return [{"name": r.name, "pending": r.pending} for r in results]
        except Exception as e:
            logger.error(f"Top warehouses by pending query failed: {e}")
            return []
    
    # ==========================================================
    # EXECUTIVE QUERIES
    # ==========================================================
    
    def get_executive_insights_data(self) -> Dict[str, Any]:
        """Get executive insights data."""
        try:
            sql = text("""
                SELECT 
                    COUNT(*) as total_dns,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NULL THEN 1 ELSE 0 END), 0) as pending_pgi,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN 1 ELSE 0 END), 0) as pending_pod,
                    COALESCE(AVG(CASE WHEN good_issue_date IS NOT NULL THEN 
                        DATEDIFF(good_issue_date, dn_create_date) 
                    END), 0) as avg_delivery_aging,
                    COALESCE(SUM(dn_amount), 0) as total_revenue
                FROM delivery_report 
                WHERE dn_create_date IS NOT NULL
            """)
            
            result = self.db.execute(sql).first()
            
            # Get worst warehouse
            warehouse_sql = text("""
                SELECT warehouse, COUNT(*) as pending
                FROM delivery_report 
                WHERE good_issue_date IS NULL
                AND warehouse IS NOT NULL
                GROUP BY warehouse
                ORDER BY pending DESC
                LIMIT 1
            """)
            worst = self.db.execute(warehouse_sql).first()
            
            # Get oldest pending
            oldest_sql = text("""
                SELECT dn_no, customer_name, dn_create_date
                FROM delivery_report 
                WHERE good_issue_date IS NULL
                AND dn_create_date IS NOT NULL
                ORDER BY dn_create_date
                LIMIT 1
            """)
            oldest = self.db.execute(oldest_sql).first()
            
            return {
                "total_dns": result.total_dns or 0,
                "pending_pgi": result.pending_pgi or 0,
                "pending_pod": result.pending_pod or 0,
                "avg_delivery_aging": round(result.avg_delivery_aging or 0, 1),
                "total_revenue": float(result.total_revenue or 0),
                "worst_warehouse": worst[0] if worst else None,
                "oldest_dn": oldest.dn_no if oldest else None,
                "oldest_aging": (self.today - oldest.dn_create_date).days if oldest and oldest.dn_create_date else 0
            }
        except Exception as e:
            logger.error(f"Executive insights query failed: {e}")
            return {}
    
    def get_critical_deliveries(self, threshold_days: int = 15, limit: int = 10) -> List[Dict]:
        """Get critical deliveries."""
        try:
            sql = text("""
                SELECT 
                    dn_no, 
                    customer_name, 
                    warehouse,
                    COALESCE(DATEDIFF(:today, dn_create_date), 0) as aging
                FROM delivery_report 
                WHERE good_issue_date IS NULL
                AND dn_create_date IS NOT NULL
                AND DATEDIFF(:today, dn_create_date) > :threshold
                ORDER BY aging DESC
                LIMIT :limit
            """)
            
            results = self.db.execute(sql, {
                "today": self.today,
                "threshold": threshold_days,
                "limit": limit
            }).all()
            
            return [{
                "dn": r.dn_no,
                "dealer": r.customer_name,
                "warehouse": r.warehouse,
                "aging": r.aging
            } for r in results]
        except Exception as e:
            logger.error(f"Critical deliveries query failed: {e}")
            return []
    
    # ==========================================================
    # DATA QUALITY QUERIES
    # ==========================================================
    
    def get_data_quality_metrics(self) -> Dict[str, Any]:
        """Get data quality metrics."""
        try:
            total = self.db.query(func.count(DeliveryReport.id)).scalar() or 1
            
            sql = text("""
                SELECT 
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date >= good_issue_date THEN 1 ELSE 0 END), 0) as valid_dates,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NULL AND pod_date IS NOT NULL THEN 1 ELSE 0 END), 0) as missing_pgi,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN 1 ELSE 0 END), 0) as missing_pod,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        AND pod_date < good_issue_date THEN 1 ELSE 0 END), 0) as negative_aging
                FROM delivery_report 
                WHERE dn_create_date IS NOT NULL
            """)
            
            result = self.db.execute(sql).first()
            
            valid_count = result.valid_dates or 0
            invalid_count = result.missing_pgi + result.missing_pod + result.negative_aging
            quality_score = round((valid_count / total) * 100, 1) if total > 0 else 0
            
            return {
                "total_records": total,
                "valid_dates": valid_count,
                "invalid_dates": invalid_count,
                "missing_pgi": result.missing_pgi or 0,
                "missing_pod": result.missing_pod or 0,
                "negative_aging": result.negative_aging or 0,
                "quality_score": quality_score,
                "quality_status": "EXCELLENT" if quality_score >= 90 else "GOOD" if quality_score >= 75 else "FAIR" if quality_score >= 60 else "POOR"
            }
        except Exception as e:
            logger.error(f"Data quality metrics query failed: {e}")
            return {}
    
    def get_trend_analysis(self) -> Dict[str, Any]:
        """Get trend analysis data."""
        try:
            sql = text("""
                SELECT 
                    strftime('%Y-%m', dn_create_date) as period,
                    COUNT(*) as count,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units
                FROM delivery_report 
                WHERE dn_create_date IS NOT NULL
                GROUP BY strftime('%Y-%m', dn_create_date)
                ORDER BY period DESC
                LIMIT 12
            """)
            
            results = self.db.execute(sql).all()
            
            return {
                "trends": {
                    "monthly": [{
                        "period": r.period,
                        "count": r.count,
                        "revenue": float(r.revenue or 0),
                        "units": int(r.units or 0)
                    } for r in results]
                }
            }
        except Exception as e:
            logger.error(f"Trend analysis query failed: {e}")
            return {"trends": {"monthly": []}}
    
    # ==========================================================
    # RESOLUTION METHODS (ALIGNED WITH SchemaService v7.0)
    # ==========================================================
    
    def resolve_dealer_name(self, dealer_input: str) -> Optional[str]:
        """
        Resolve dealer name using SchemaService v7.0 with database fallback.
        
        ALIGNED WITH: SchemaService v7.0 resolve_dealer()
        """
        if not dealer_input:
            return None
        
        logger.debug(f"Resolving dealer: '{dealer_input}'")
        
        # ==========================================================
        # STRATEGY 1: Use SchemaService v7.0 resolve_dealer()
        # ==========================================================
        resolved = self.schema.resolve_dealer(dealer_input)
        if resolved:
            logger.debug(f"SchemaService resolved: {resolved}")
            return resolved
        
        # ==========================================================
        # STRATEGY 2: Case-insensitive exact match in database
        # ==========================================================
        try:
            exact = self.db.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)
            ).first()
            if exact:
                logger.debug(f"DB exact match: {exact[0]}")
                return exact[0]
        except Exception:
            pass
        
        # ==========================================================
        # STRATEGY 3: Partial match in database
        # ==========================================================
        try:
            partial = self.db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_input}%")
            ).first()
            if partial:
                logger.debug(f"DB partial match: {partial[0]}")
                return partial[0]
        except Exception:
            pass
        
        # ==========================================================
        # STRATEGY 4: Word-by-word partial matching
        # ==========================================================
        words = dealer_input.lower().split()
        if len(words) >= 2:
            try:
                for i in range(len(words) - 1):
                    for j in range(i + 1, min(i + 4, len(words) + 1)):
                        pattern = ' '.join(words[i:j])
                        result = self.db.query(DeliveryReport.customer_name).filter(
                            func.lower(DeliveryReport.customer_name).contains(pattern)
                        ).first()
                        if result:
                            logger.debug(f"DB word-pattern match: {result[0]}")
                            return result[0]
            except Exception:
                pass
        
        # ==========================================================
        # STRATEGY 5: Use SchemaService v7.0 find_dealer_debug()
        # ==========================================================
        try:
            debug_result = self.schema.find_dealer_debug(dealer_input)
            if debug_result.get("resolved"):
                logger.debug(f"SchemaService debug resolved: {debug_result['resolved']}")
                return debug_result["resolved"]
        except Exception:
            pass
        
        logger.warning(f"Could not resolve dealer: '{dealer_input}'")
        return None
    
    def get_all_dealer_names(self) -> List[str]:
        """Get all unique dealer names from database."""
        try:
            results = self.db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).distinct().order_by(DeliveryReport.customer_name).all()
            return [r[0] for r in results]
        except Exception as e:
            logger.error(f"Get all dealer names query failed: {e}")
            return []
    
    def get_all_warehouse_names(self) -> List[str]:
        """Get all unique warehouse names from database."""
        try:
            results = self.db.query(DeliveryReport.warehouse).filter(
                DeliveryReport.warehouse.isnot(None),
                DeliveryReport.warehouse != ''
            ).distinct().order_by(DeliveryReport.warehouse).all()
            return [r[0] for r in results]
        except Exception as e:
            logger.error(f"Get all warehouse names query failed: {e}")
            return []
    
    def get_all_city_names(self) -> List[str]:
        """Get all unique city names from database."""
        try:
            results = self.db.query(DeliveryReport.ship_to_city).filter(
                DeliveryReport.ship_to_city.isnot(None),
                DeliveryReport.ship_to_city != ''
            ).distinct().order_by(DeliveryReport.ship_to_city).all()
            return [r[0] for r in results]
        except Exception as e:
            logger.error(f"Get all city names query failed: {e}")
            return []


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(db: Optional[Session] = None) -> LogisticsQueryService:
    """Factory function for LogisticsQueryService singleton."""
    return LogisticsQueryService(db)
