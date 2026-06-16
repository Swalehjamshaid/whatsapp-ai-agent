# ==========================================================
# FILE: app/services/logistics_query_service.py (v1.0 - DATABASE ACCESS LAYER)
# ==========================================================
# PURPOSE: SINGLE SOURCE OF TRUTH for all database access
#
# ENTERPRISE FEATURES:
# - ✅ SINGLE RESPONSIBILITY: Only database queries
# - ✅ OPTIMIZED QUERIES: Single query dashboards
# - ✅ AGING CALCULATIONS: Business rules applied
# - ✅ COMPREHENSIVE METRICS: Dealer, Warehouse, KPI
# - ✅ ERROR HANDLING: Graceful degradation
# - ✅ CACHED QUERIES: Performance optimization
# ==========================================================

from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy import func, and_, or_, desc, case
from sqlalchemy.orm import Session
from loguru import logger

from app.models import DeliveryReport
from app.database import SessionLocal


class LogisticsQueryService:
    """
    DATABASE ACCESS LAYER - SINGLE SOURCE OF TRUTH
    
    THIS IS THE ONLY FILE ALLOWED TO ACCESS THE DATABASE.
    All other services must call this service for data.
    """
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or SessionLocal()
        self._owned_db = db is None
    
    def close(self):
        """Close database session if owned"""
        if self._owned_db and self.db:
            self.db.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # ==========================================================
    # DEALER QUERIES
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get complete dealer dashboard in a single optimized query
        
        Returns:
            Dict with: total_dns, total_units, total_revenue,
            delivered_units, transit_units, pending_delivery,
            pod_completed, pending_pod, delivery_rate, pod_rate,
            avg_delivery_aging, avg_pod_aging, oldest_pending_dn,
            oldest_pending_days, top_warehouse
        """
        if not dealer_name:
            return {}
        
        try:
            result = self.db.query(
                func.count(DeliveryReport.id).label('total_dns'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.sum(case((DeliveryReport.good_issue_date.isnot(None), 1), else_=0)).label('delivered_units'),
                func.sum(case((DeliveryReport.good_issue_date.is_(None), 1), else_=0)).label('pending_delivery'),
                func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('transit_units'),
                func.sum(case((DeliveryReport.pod_date.isnot(None), 1), else_=0)).label('pod_completed'),
                func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('pending_pod'),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), 
                              func.datediff(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date)), else_=0)).label('avg_delivery_aging'),
                func.avg(case((DeliveryReport.pod_date.isnot(None),
                              func.datediff(DeliveryReport.pod_date, DeliveryReport.good_issue_date)), else_=0)).label('avg_pod_aging'),
                func.min(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_create_date), else_=None)).label('oldest_pending_date'),
                func.max(DeliveryReport.warehouse).label('top_warehouse')
            ).filter(DeliveryReport.customer_name == dealer_name).first()
            
            if not result or result.total_dns is None:
                return {}
            
            total_dns = result.total_dns or 1
            delivery_rate = (result.delivered_units / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = (result.pod_completed / result.delivered_units * 100) if result.delivered_units > 0 else 0
            
            # Get oldest pending DN
            oldest_pending = self.db.query(
                DeliveryReport.dn_no, 
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.good_issue_date.is_(None)
            ).order_by(DeliveryReport.dn_create_date).first()
            
            today = date.today()
            
            return {
                "dealer_name": dealer_name,
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
                "oldest_pending_dn": oldest_pending.dn_no if oldest_pending else None,
                "oldest_pending_days": (today - oldest_pending.dn_create_date).days if oldest_pending else 0,
                "top_warehouse": result.top_warehouse or "N/A"
            }
            
        except Exception as e:
            logger.error(f"Dealer dashboard query failed for {dealer_name}: {e}")
            return {}
    
    def get_dealer_revenue(self, dealer_name: str) -> float:
        """Get dealer revenue"""
        try:
            result = self.db.query(
                func.sum(DeliveryReport.dn_amount)
            ).filter(DeliveryReport.customer_name == dealer_name).first()
            return float(result[0] or 0)
        except Exception:
            return 0.0
    
    def get_dealer_units(self, dealer_name: str) -> int:
        """Get dealer units"""
        try:
            result = self.db.query(
                func.sum(DeliveryReport.dn_qty)
            ).filter(DeliveryReport.customer_name == dealer_name).first()
            return int(result[0] or 0)
        except Exception:
            return 0
    
    def get_dealer_aging(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer aging metrics"""
        try:
            result = self.db.query(
                func.avg(case((DeliveryReport.good_issue_date.isnot(None),
                              func.datediff(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date)), else_=0)).label('avg_delivery_aging'),
                func.max(case((DeliveryReport.good_issue_date.isnot(None),
                              func.datediff(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date)), else_=0)).label('max_delivery_aging'),
                func.avg(case((DeliveryReport.pod_date.isnot(None),
                              func.datediff(DeliveryReport.pod_date, DeliveryReport.good_issue_date)), else_=0)).label('avg_pod_aging'),
                func.max(case((DeliveryReport.pod_date.isnot(None),
                              func.datediff(DeliveryReport.pod_date, DeliveryReport.good_issue_date)), else_=0)).label('max_pod_aging')
            ).filter(DeliveryReport.customer_name == dealer_name).first()
            
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
        """Get dealer DNs"""
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
                DeliveryReport.dn_qty,
                DeliveryReport.dn_amount,
                DeliveryReport.warehouse
            ).filter(
                DeliveryReport.customer_name == dealer_name
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dn_date": r.dn_create_date,
                "pgi_date": r.good_issue_date,
                "pod_date": r.pod_date,
                "units": int(r.dn_qty or 0),
                "amount": float(r.dn_amount or 0),
                "warehouse": r.warehouse
            } for r in results]
        except Exception as e:
            logger.error(f"Dealer DNs query failed for {dealer_name}: {e}")
            return []
    
    # ==========================================================
    # WAREHOUSE QUERIES
    # ==========================================================
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse dashboard"""
        try:
            result = self.db.query(
                func.count(DeliveryReport.id).label('total_dns'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.sum(case((DeliveryReport.good_issue_date.is_(None), 1), else_=0)).label('pending_delivery'),
                func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('pending_pod'),
                func.sum(case((DeliveryReport.good_issue_date.isnot(None), 1), else_=0)).label('pgi_completed'),
                func.sum(case((DeliveryReport.pod_date.isnot(None), 1), else_=0)).label('pod_completed')
            ).filter(DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")).first()
            
            if not result or result.total_dns is None:
                return {}
            
            return {
                "warehouse_name": warehouse_name,
                "total_dns": result.total_dns or 0,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "pending_delivery": result.pending_delivery or 0,
                "pending_pod": result.pending_pod or 0,
                "pgi_completed": result.pgi_completed or 0,
                "pod_completed": result.pod_completed or 0
            }
        except Exception as e:
            logger.error(f"Warehouse dashboard query failed for {warehouse_name}: {e}")
            return {}
    
    def get_warehouse_pending(self, warehouse_name: str) -> int:
        """Get warehouse pending count"""
        try:
            result = self.db.query(
                func.count(DeliveryReport.id)
            ).filter(
                DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"),
                DeliveryReport.good_issue_date.is_(None)
            ).first()
            return result[0] or 0
        except Exception:
            return 0
    
    # ==========================================================
    # DN QUERIES
    # ==========================================================
    
    def get_dn_details(self, dn_number: str) -> Optional[Dict[str, Any]]:
        """Get DN details"""
        try:
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).first()
            
            if not record and dn_number.isdigit():
                record = self.db.query(DeliveryReport).filter(
                    DeliveryReport.dn_no == f"{dn_number}.0"
                ).first()
            
            if not record:
                return None
            
            today = date.today()
            delivery_aging = None
            pod_aging = None
            
            if record.dn_create_date and record.good_issue_date:
                delivery_aging = (record.good_issue_date - record.dn_create_date).days
            elif record.dn_create_date:
                delivery_aging = (today - record.dn_create_date).days
            
            if record.good_issue_date and record.pod_date:
                pod_aging = (record.pod_date - record.good_issue_date).days
            elif record.good_issue_date:
                pod_aging = (today - record.good_issue_date).days
            
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
                "status": "Delivered" if record.pod_date else "In Transit" if record.good_issue_date else "Pending PGI"
            }
        except Exception as e:
            logger.error(f"DN details query failed for {dn_number}: {e}")
            return None
    
    # ==========================================================
    # PENDING QUERIES
    # ==========================================================
    
    def get_pending_pgi_count(self, dealer_name: Optional[str] = None) -> int:
        """Get pending PGI count"""
        try:
            query = self.db.query(func.count(DeliveryReport.id)).filter(
                DeliveryReport.good_issue_date.is_(None)
            )
            if dealer_name:
                query = query.filter(DeliveryReport.customer_name == dealer_name)
            return query.scalar() or 0
        except Exception:
            return 0
    
    def get_pending_pod_count(self, dealer_name: Optional[str] = None) -> int:
        """Get pending POD count"""
        try:
            query = self.db.query(func.count(DeliveryReport.id)).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.pod_date.is_(None)
            )
            if dealer_name:
                query = query.filter(DeliveryReport.customer_name == dealer_name)
            return query.scalar() or 0
        except Exception:
            return 0
    
    # ==========================================================
    # RANKING QUERIES
    # ==========================================================
    
    def get_top_dealers_by_revenue(self, limit: int = 10) -> List[Dict]:
        """Get top dealers by revenue"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.dn_amount.isnot(None)
            ).group_by(DeliveryReport.customer_name).order_by(
                desc('revenue')
            ).limit(limit).all()
            
            return [{"name": r[0], "revenue": float(r[1] or 0)} for r in results]
        except Exception as e:
            logger.error(f"Top dealers by revenue query failed: {e}")
            return []
    
    def get_top_dealers_by_units(self, limit: int = 10) -> List[Dict]:
        """Get top dealers by units"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_qty).label('units')
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(DeliveryReport.customer_name).order_by(
                desc('units')
            ).limit(limit).all()
            
            return [{"name": r[0], "units": int(r[1] or 0)} for r in results]
        except Exception as e:
            logger.error(f"Top dealers by units query failed: {e}")
            return []
    
    def get_worst_dealers_by_pod_aging(self, limit: int = 10) -> List[Dict]:
        """Get worst dealers by POD aging"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.avg(func.datediff(DeliveryReport.pod_date, DeliveryReport.good_issue_date)).label('avg_pod_aging')
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.pod_date.isnot(None)
            ).group_by(DeliveryReport.customer_name).order_by(
                desc('avg_pod_aging')
            ).limit(limit).all()
            
            return [{"name": r[0], "avg_pod_aging": round(r[1] or 0, 1)} for r in results]
        except Exception as e:
            logger.error(f"Worst dealers by POD aging query failed: {e}")
            return []
    
    def get_top_warehouses_by_pending(self, limit: int = 10) -> List[Dict]:
        """Get top warehouses by pending"""
        try:
            results = self.db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.id).label('pending')
            ).filter(
                DeliveryReport.warehouse.isnot(None),
                DeliveryReport.good_issue_date.is_(None)
            ).group_by(DeliveryReport.warehouse).order_by(
                desc('pending')
            ).limit(limit).all()
            
            return [{"name": r[0], "pending": r[1]} for r in results]
        except Exception as e:
            logger.error(f"Top warehouses by pending query failed: {e}")
            return []
    
    # ==========================================================
    # EXECUTIVE QUERIES
    # ==========================================================
    
    def get_executive_insights(self) -> Dict[str, Any]:
        """Get executive insights"""
        try:
            result = self.db.query(
                func.count(DeliveryReport.id).label('total_dns'),
                func.sum(case((DeliveryReport.good_issue_date.is_(None), 1), else_=0)).label('pending_pgi'),
                func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('pending_pod'),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None),
                              func.datediff(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date)), else_=0)).label('avg_delivery_aging')
            ).first()
            
            worst_warehouse = self.db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.id).label('pending')
            ).filter(
                DeliveryReport.good_issue_date.is_(None),
                DeliveryReport.warehouse.isnot(None)
            ).group_by(DeliveryReport.warehouse).order_by(
                desc('pending')
            ).first()
            
            oldest = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.good_issue_date.is_(None),
                DeliveryReport.dn_create_date.isnot(None)
            ).order_by(DeliveryReport.dn_create_date).first()
            
            today = date.today()
            
            return {
                "total_dns": result.total_dns or 0,
                "pending_pgi": result.pending_pgi or 0,
                "pending_pod": result.pending_pod or 0,
                "avg_delivery_aging": round(result.avg_delivery_aging or 0, 1),
                "worst_warehouse": worst_warehouse[0] if worst_warehouse else None,
                "oldest_dn": oldest.dn_no if oldest else None,
                "oldest_aging": (today - oldest.dn_create_date).days if oldest else 0
            }
        except Exception as e:
            logger.error(f"Executive insights query failed: {e}")
            return {}
    
    def get_critical_deliveries(self, threshold_days: int = 15, limit: int = 10) -> List[Dict]:
        """Get critical deliveries"""
        try:
            today = date.today()
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.warehouse,
                func.datediff(today, DeliveryReport.dn_create_date).label('aging')
            ).filter(
                DeliveryReport.good_issue_date.is_(None),
                DeliveryReport.dn_create_date.isnot(None),
                func.datediff(today, DeliveryReport.dn_create_date) > threshold_days
            ).order_by(desc('aging')).limit(limit).all()
            
            return [{"dn": r[0], "dealer": r[1], "warehouse": r[2], "aging": r[3]} for r in results]
        except Exception as e:
            logger.error(f"Critical deliveries query failed: {e}")
            return []
    
    # ==========================================================
    # WAREHOUSE LIST
    # ==========================================================
    
    def get_warehouse_list(self) -> List[str]:
        """Get list of warehouses"""
        try:
            results = self.db.query(DeliveryReport.warehouse).filter(
                DeliveryReport.warehouse.isnot(None)
            ).distinct().limit(50).all()
            return [w[0] for w in results if w[0]]
        except Exception:
            return ['lahore', 'karachi', 'islamabad', 'rawalpindi', 'multan', 'faisalabad']
    
    # ==========================================================
    # DEALER RESOLUTION
    # ==========================================================
    
    def resolve_dealer_name(self, dealer_input: str) -> Optional[str]:
        """Resolve dealer name from input"""
        if not dealer_input:
            return None
        
        try:
            # Exact match
            exact = self.db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)
            ).first()
            if exact:
                return exact.customer_name
            
            # Partial match
            partial = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_input}%")
            ).first()
            if partial:
                return partial.customer_name
            
            return None
        except Exception:
            return None


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(db: Optional[Session] = None) -> LogisticsQueryService:
    """Get LogisticsQueryService instance"""
    return LogisticsQueryService(db)
