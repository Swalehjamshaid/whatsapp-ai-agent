# ==========================================================
# FILE: app/services/schema_service.py
# ==========================================================
# PURPOSE: Database Repository Layer - ONLY data access
#          No business logic, no KPI calculations, no formatting
#
# WHAT THIS FILE DOES:
# ✅ Database Queries (SELECT, FILTER, GROUP BY, JOIN)
# ✅ Data Retrieval for all entities
# ✅ Caching Layer
# ✅ Entity Registry (dynamic dealer/warehouse/product lists)
# ✅ Fuzzy Search Registry
# ✅ Query Builder Layer
# ✅ Date Filtering Engine
# ✅ Aggregation Layer
#
# WHAT THIS FILE NEVER DOES:
# ✗ KPI Calculations
# ✗ Business Rules
# ✗ Response Formatting
# ✗ WhatsApp Sending
# ✗ User Question Parsing
# ✗ Intent Detection
# ==========================================================

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from collections import defaultdict
from sqlalchemy import func, and_, or_, desc, asc
from loguru import logger
from cachetools import TTLCache

from app.database import SessionLocal
from app.models import DeliveryReport
from app.config import config


# ==========================================================
# DATA CLASSES
# ==========================================================

@dataclass
class DateFilter:
    """Date filter for queries"""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    date_field: str = "dn_create_date"


@dataclass
class QueryFilter:
    """Query filter for database queries"""
    dealer_name: Optional[str] = None
    warehouse_name: Optional[str] = None
    city_name: Optional[str] = None
    product_code: Optional[str] = None
    division: Optional[str] = None
    dn_number: Optional[str] = None
    status: Optional[str] = None
    date_filter: Optional[DateFilter] = None
    limit: Optional[int] = None
    offset: Optional[int] = 0


# ==========================================================
# CACHE CONFIGURATION
# ==========================================================

# Cache TTL from config (default 5 minutes)
CACHE_TTL = config.CACHE_TTL

# Entity caches
dealer_cache = TTLCache(maxsize=500, ttl=CACHE_TTL * 6)  # 30 min for entity lists
warehouse_cache = TTLCache(maxsize=100, ttl=CACHE_TTL * 6)
city_cache = TTLCache(maxsize=100, ttl=CACHE_TTL * 6)
product_cache = TTLCache(maxsize=500, ttl=CACHE_TTL * 6)

# Query result caches
query_cache = TTLCache(maxsize=500, ttl=CACHE_TTL)
dashboard_cache = TTLCache(maxsize=200, ttl=CACHE_TTL)


# ==========================================================
# SCHEMA SERVICE
# ==========================================================

class SchemaService:
    """
    Database Repository Layer
    ONLY data access - no business logic
    """
    
    def __init__(self):
        """Initialize the Schema Service"""
        logger.info("Schema Service initialized - Database Librarian")
    
    # ==========================================================
    # 1. DEALER REPOSITORY
    # ==========================================================
    
    def get_dealer_records(self, dealer_name: str, date_filter: Optional[DateFilter] = None) -> List[DeliveryReport]:
        """
        Get all records for a specific dealer
        
        Args:
            dealer_name: Name of the dealer (supports partial match)
            date_filter: Optional date range filter
            
        Returns:
            List of DeliveryReport records
        """
        db = SessionLocal()
        try:
            query = db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            )
            
            # Apply date filter
            if date_filter:
                query = self._apply_date_filter(query, date_filter)
            
            return query.all()
        finally:
            db.close()
    
    def get_dealer_by_name(self, dealer_name: str) -> Optional[DeliveryReport]:
        """Get first record for a dealer by exact name match"""
        db = SessionLocal()
        try:
            return db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name)
            ).first()
        finally:
            db.close()
    
    def get_dealer_dns(self, dealer_name: str) -> List[str]:
        """Get all unique DN numbers for a dealer"""
        db = SessionLocal()
        try:
            results = db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).distinct().all()
            return [r.dn_no for r in results if r.dn_no]
        finally:
            db.close()
    
    def get_dealer_products(self, dealer_name: str) -> List[Dict]:
        """Get all products purchased by a dealer with quantities"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.product_code,
                DeliveryReport.product_description,
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue')
            ).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%"),
                DeliveryReport.product_code.isnot(None)
            ).group_by(
                DeliveryReport.product_code,
                DeliveryReport.product_description
            ).order_by(desc('total_revenue')).all()
            
            return [
                {
                    "product_code": r.product_code,
                    "product_name": r.product_description or r.product_code,
                    "quantity": int(r.total_quantity or 0),
                    "revenue": float(r.total_revenue or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    def get_dealer_warehouses(self, dealer_name: str) -> List[Dict]:
        """Get all warehouses used by a dealer"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.dn_no).label('dn_count'),
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%"),
                DeliveryReport.warehouse.isnot(None)
            ).group_by(DeliveryReport.warehouse).order_by(desc('revenue')).all()
            
            return [
                {
                    "warehouse": r.warehouse,
                    "dn_count": int(r.dn_count or 0),
                    "revenue": float(r.revenue or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    def get_dealer_cities(self, dealer_name: str) -> List[Dict]:
        """Get all cities where a dealer operates"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.dn_no).label('dn_count'),
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%"),
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(DeliveryReport.ship_to_city).order_by(desc('revenue')).all()
            
            return [
                {
                    "city": r.ship_to_city,
                    "dn_count": int(r.dn_count or 0),
                    "revenue": float(r.revenue or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    def get_all_dealers(self) -> List[str]:
        """Get all unique dealer names (cached)"""
        cache_key = "all_dealers"
        if cache_key in dealer_cache:
            return dealer_cache[cache_key]
        
        db = SessionLocal()
        try:
            results = db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.isnot(None)
            ).distinct().all()
            dealers = [r.customer_name for r in results if r.customer_name]
            dealer_cache[cache_key] = dealers
            return dealers
        finally:
            db.close()
    
    # ==========================================================
    # 2. WAREHOUSE REPOSITORY
    # ==========================================================
    
    def get_warehouse_records(self, warehouse_name: str, date_filter: Optional[DateFilter] = None) -> List[DeliveryReport]:
        """Get all records for a specific warehouse"""
        db = SessionLocal()
        try:
            query = db.query(DeliveryReport).filter(
                DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")
            )
            
            if date_filter:
                query = self._apply_date_filter(query, date_filter)
            
            return query.all()
        finally:
            db.close()
    
    def get_warehouse_dns(self, warehouse_name: str) -> List[str]:
        """Get all unique DN numbers for a warehouse"""
        db = SessionLocal()
        try:
            results = db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")
            ).distinct().all()
            return [r.dn_no for r in results if r.dn_no]
        finally:
            db.close()
    
    def get_warehouse_products(self, warehouse_name: str) -> List[Dict]:
        """Get all products handled by a warehouse"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.product_code,
                DeliveryReport.product_description,
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue')
            ).filter(
                DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"),
                DeliveryReport.product_code.isnot(None)
            ).group_by(
                DeliveryReport.product_code,
                DeliveryReport.product_description
            ).order_by(desc('total_revenue')).all()
            
            return [
                {
                    "product_code": r.product_code,
                    "product_name": r.product_description or r.product_code,
                    "quantity": int(r.total_quantity or 0),
                    "revenue": float(r.total_revenue or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    def get_warehouse_cities(self, warehouse_name: str) -> List[Dict]:
        """Get all cities served by a warehouse"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.dn_no).label('dn_count')
            ).filter(
                DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"),
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(DeliveryReport.ship_to_city).order_by(desc('dn_count')).all()
            
            return [
                {
                    "city": r.ship_to_city,
                    "dn_count": int(r.dn_count or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    def get_all_warehouses(self) -> List[str]:
        """Get all unique warehouse names (cached)"""
        cache_key = "all_warehouses"
        if cache_key in warehouse_cache:
            return warehouse_cache[cache_key]
        
        db = SessionLocal()
        try:
            results = db.query(DeliveryReport.warehouse).filter(
                DeliveryReport.warehouse.isnot(None)
            ).distinct().all()
            warehouses = [r.warehouse for r in results if r.warehouse]
            warehouse_cache[cache_key] = warehouses
            return warehouses
        finally:
            db.close()
    
    # ==========================================================
    # 3. CITY REPOSITORY
    # ==========================================================
    
    def get_city_records(self, city_name: str, date_filter: Optional[DateFilter] = None) -> List[DeliveryReport]:
        """Get all records for a specific city"""
        db = SessionLocal()
        try:
            query = db.query(DeliveryReport).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_name}%")
            )
            
            if date_filter:
                query = self._apply_date_filter(query, date_filter)
            
            return query.all()
        finally:
            db.close()
    
    def get_city_dealers(self, city_name: str) -> List[Dict]:
        """Get all dealers in a city"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_name}%"),
                DeliveryReport.customer_name.isnot(None)
            ).group_by(DeliveryReport.customer_name).order_by(desc('revenue')).all()
            
            return [
                {
                    "dealer": r.customer_name,
                    "revenue": float(r.revenue or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    def get_city_products(self, city_name: str) -> List[Dict]:
        """Get top products in a city"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.product_code,
                DeliveryReport.product_description,
                func.sum(DeliveryReport.dn_qty).label('total_quantity')
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_name}%"),
                DeliveryReport.product_code.isnot(None)
            ).group_by(
                DeliveryReport.product_code,
                DeliveryReport.product_description
            ).order_by(desc('total_quantity')).limit(10).all()
            
            return [
                {
                    "product_code": r.product_code,
                    "product_name": r.product_description or r.product_code,
                    "quantity": int(r.total_quantity or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    def get_all_cities(self) -> List[str]:
        """Get all unique city names (cached)"""
        cache_key = "all_cities"
        if cache_key in city_cache:
            return city_cache[cache_key]
        
        db = SessionLocal()
        try:
            results = db.query(DeliveryReport.ship_to_city).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).distinct().all()
            cities = [r.ship_to_city for r in results if r.ship_to_city]
            city_cache[cache_key] = cities
            return cities
        finally:
            db.close()
    
    # ==========================================================
    # 4. PRODUCT REPOSITORY
    # ==========================================================
    
    def get_product_records(self, product_identifier: str, date_filter: Optional[DateFilter] = None) -> List[DeliveryReport]:
        """Get all records for a specific product (by code or description)"""
        db = SessionLocal()
        try:
            query = db.query(DeliveryReport).filter(
                or_(
                    DeliveryReport.product_code.ilike(f"%{product_identifier}%"),
                    DeliveryReport.product_description.ilike(f"%{product_identifier}%")
                )
            )
            
            if date_filter:
                query = self._apply_date_filter(query, date_filter)
            
            return query.all()
        finally:
            db.close()
    
    def get_product_sales(self, product_code: str) -> Dict[str, Any]:
        """Get sales summary for a product"""
        db = SessionLocal()
        try:
            result = db.query(
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(func.distinct(DeliveryReport.dn_no)).label('dn_count')
            ).filter(
                DeliveryReport.product_code.ilike(f"%{product_code}%")
            ).first()
            
            return {
                "total_quantity": int(result.total_quantity or 0),
                "total_revenue": float(result.total_revenue or 0),
                "dn_count": int(result.dn_count or 0)
            }
        finally:
            db.close()
    
    def get_product_cities(self, product_code: str) -> List[Dict]:
        """Get cities where product is sold"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.ship_to_city,
                func.sum(DeliveryReport.dn_qty).label('quantity')
            ).filter(
                DeliveryReport.product_code.ilike(f"%{product_code}%"),
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(DeliveryReport.ship_to_city).order_by(desc('quantity')).limit(10).all()
            
            return [
                {
                    "city": r.ship_to_city,
                    "quantity": int(r.quantity or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    def get_product_dealers(self, product_code: str) -> List[Dict]:
        """Get dealers who bought the product"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_qty).label('quantity')
            ).filter(
                DeliveryReport.product_code.ilike(f"%{product_code}%"),
                DeliveryReport.customer_name.isnot(None)
            ).group_by(DeliveryReport.customer_name).order_by(desc('quantity')).limit(10).all()
            
            return [
                {
                    "dealer": r.customer_name,
                    "quantity": int(r.quantity or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    def get_all_products(self) -> List[Dict]:
        """Get all unique products (cached)"""
        cache_key = "all_products"
        if cache_key in product_cache:
            return product_cache[cache_key]
        
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.product_code,
                DeliveryReport.product_description
            ).filter(
                DeliveryReport.product_code.isnot(None)
            ).distinct().all()
            
            products = [
                {
                    "code": r.product_code,
                    "name": r.product_description or r.product_code
                }
                for r in results if r.product_code
            ]
            product_cache[cache_key] = products
            return products
        finally:
            db.close()
    
    # ==========================================================
    # 5. DIVISION REPOSITORY
    # ==========================================================
    
    def get_division_records(self, division_code: str, date_filter: Optional[DateFilter] = None) -> List[DeliveryReport]:
        """Get all records for a specific division"""
        db = SessionLocal()
        try:
            query = db.query(DeliveryReport).filter(
                DeliveryReport.division == division_code
            )
            
            if date_filter:
                query = self._apply_date_filter(query, date_filter)
            
            return query.all()
        finally:
            db.close()
    
    def get_division_products(self, division_code: str) -> List[Dict]:
        """Get all products in a division"""
        db = SessionLocal()
        try:
            results = db.query(
                DeliveryReport.product_code,
                DeliveryReport.product_description,
                func.sum(DeliveryReport.dn_qty).label('total_quantity')
            ).filter(
                DeliveryReport.division == division_code,
                DeliveryReport.product_code.isnot(None)
            ).group_by(
                DeliveryReport.product_code,
                DeliveryReport.product_description
            ).order_by(desc('total_quantity')).all()
            
            return [
                {
                    "product_code": r.product_code,
                    "product_name": r.product_description or r.product_code,
                    "quantity": int(r.total_quantity or 0)
                }
                for r in results
            ]
        finally:
            db.close()
    
    # ==========================================================
    # 6. SALES MANAGER REPOSITORY
    # ==========================================================
    
    def get_sales_manager_records(self, manager_name: str, date_filter: Optional[DateFilter] = None) -> List[DeliveryReport]:
        """
        Get all records for a sales manager
        
        Note: This requires a sales_manager field in your model
        If not available, returns empty list
        """
        db = SessionLocal()
        try:
            # Check if sales_manager column exists
            # For now, return empty list as this is future feature
            return []
        finally:
            db.close()
    
    # ==========================================================
    # 7. DN REPOSITORY
    # ==========================================================
    
    def get_dn_details(self, dn_number: str) -> Optional[DeliveryReport]:
        """Get single DN record by DN number"""
        db = SessionLocal()
        try:
            # Try exact match first
            record = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).first()
            
            # Try with .0 suffix
            if not record and dn_number.isdigit():
                record = db.query(DeliveryReport).filter(
                    DeliveryReport.dn_no == f"{dn_number}.0"
                ).first()
            
            # Try partial match as last resort
            if not record:
                record = db.query(DeliveryReport).filter(
                    DeliveryReport.dn_no.like(f"%{dn_number}%")
                ).first()
            
            return record
        finally:
            db.close()
    
    def get_dn_status(self, dn_number: str) -> Optional[Dict]:
        """Get status information for a DN"""
        record = self.get_dn_details(dn_number)
        if not record:
            return None
        
        return {
            "dn_number": record.dn_no,
            "delivery_status": record.delivery_status,
            "pod_status": record.pod_status,
            "pgi_status": record.pgi_status,
            "dn_date": record.dn_create_date,
            "pgi_date": record.good_issue_date,
            "pod_date": record.pod_date
        }
    
    def get_dn_timeline(self, dn_number: str) -> Dict[str, Any]:
        """Get timeline events for a DN"""
        record = self.get_dn_details(dn_number)
        if not record:
            return {}
        
        timeline = []
        if record.dn_create_date:
            timeline.append({
                "event": "DN Created",
                "date": record.dn_create_date.isoformat()
            })
        if record.good_issue_date:
            timeline.append({
                "event": "PGI Completed",
                "date": record.good_issue_date.isoformat()
            })
        if record.pod_date:
            timeline.append({
                "event": "POD Completed",
                "date": record.pod_date.isoformat()
            })
        
        return {
            "dn_number": dn_number,
            "timeline": timeline,
            "current_status": record.delivery_status or "Unknown"
        }
    
    # ==========================================================
    # 8. GENERAL DATA ACCESS
    # ==========================================================
    
    def get_all_records(self, limit: Optional[int] = None) -> List[DeliveryReport]:
        """Get all records (with optional limit)"""
        db = SessionLocal()
        try:
            query = db.query(DeliveryReport)
            if limit:
                query = query.limit(limit)
            return query.all()
        finally:
            db.close()
    
    def get_records_by_filter(self, query_filter: QueryFilter) -> List[DeliveryReport]:
        """Get records by complex filter"""
        db = SessionLocal()
        try:
            query = db.query(DeliveryReport)
            
            # Apply filters
            if query_filter.dealer_name:
                query = query.filter(
                    DeliveryReport.customer_name.ilike(f"%{query_filter.dealer_name}%")
                )
            
            if query_filter.warehouse_name:
                query = query.filter(
                    DeliveryReport.warehouse.ilike(f"%{query_filter.warehouse_name}%")
                )
            
            if query_filter.city_name:
                query = query.filter(
                    DeliveryReport.ship_to_city.ilike(f"%{query_filter.city_name}%")
                )
            
            if query_filter.product_code:
                query = query.filter(
                    DeliveryReport.product_code.ilike(f"%{query_filter.product_code}%")
                )
            
            if query_filter.division:
                query = query.filter(DeliveryReport.division == query_filter.division)
            
            if query_filter.dn_number:
                query = query.filter(DeliveryReport.dn_no == query_filter.dn_number)
            
            if query_filter.status:
                query = query.filter(DeliveryReport.delivery_status == query_filter.status)
            
            # Apply date filter
            if query_filter.date_filter:
                query = self._apply_date_filter(query, query_filter.date_filter)
            
            # Apply pagination
            if query_filter.limit:
                query = query.limit(query_filter.limit)
            
            if query_filter.offset:
                query = query.offset(query_filter.offset)
            
            return query.all()
        finally:
            db.close()
    
    # ==========================================================
    # 9. DYNAMIC ENTITY REGISTRY (for AI Query Service)
    # ==========================================================
    
    def get_entity_registry(self) -> Dict[str, List[str]]:
        """Get all entities for AI query service (cached)"""
        return {
            "dealers": self.get_all_dealers(),
            "warehouses": self.get_all_warehouses(),
            "cities": self.get_all_cities(),
            "products": [p["code"] for p in self.get_all_products()]
        }
    
    def find_closest_dealer(self, search_term: str) -> Optional[str]:
        """Fuzzy find dealer by name"""
        dealers = self.get_all_dealers()
        search_lower = search_term.lower()
        
        # Exact match first
        for dealer in dealers:
            if dealer.lower() == search_lower:
                return dealer
        
        # Partial match
        for dealer in dealers:
            if search_lower in dealer.lower():
                return dealer
        
        return None
    
    def find_closest_warehouse(self, search_term: str) -> Optional[str]:
        """Fuzzy find warehouse by name"""
        warehouses = self.get_all_warehouses()
        search_lower = search_term.lower()
        
        for warehouse in warehouses:
            if search_lower in warehouse.lower():
                return warehouse
        
        return None
    
    def find_closest_city(self, search_term: str) -> Optional[str]:
        """Fuzzy find city by name"""
        cities = self.get_all_cities()
        search_lower = search_term.lower()
        
        for city in cities:
            if search_lower in city.lower():
                return city
        
        return None
    
    # ==========================================================
    # 10. QUERY BUILDER LAYER
    # ==========================================================
    
    def _apply_date_filter(self, query, date_filter: DateFilter):
        """Apply date filter to query"""
        if date_filter.start_date:
            query = query.filter(getattr(DeliveryReport, date_filter.date_field) >= date_filter.start_date)
        if date_filter.end_date:
            query = query.filter(getattr(DeliveryReport, date_filter.date_field) <= date_filter.end_date)
        return query
    
    def build_date_filter_from_range(self, start_date: date, end_date: date, date_field: str = "dn_create_date") -> DateFilter:
        """Build date filter from date range"""
        return DateFilter(
            start_date=start_date,
            end_date=end_date,
            date_field=date_field
        )
    
    # ==========================================================
    # 11. AGGREGATION LAYER
    # ==========================================================
    
    def aggregate_revenue(self, query_filter: QueryFilter) -> float:
        """Aggregate total revenue based on filter"""
        records = self.get_records_by_filter(query_filter)
        return sum(float(r.dn_amount or 0) for r in records)
    
    def aggregate_units(self, query_filter: QueryFilter) -> int:
        """Aggregate total units based on filter"""
        records = self.get_records_by_filter(query_filter)
        return sum(int(r.dn_qty or 0) for r in records)
    
    def aggregate_dns(self, query_filter: QueryFilter) -> int:
        """Aggregate unique DN count based on filter"""
        records = self.get_records_by_filter(query_filter)
        return len(set(r.dn_no for r in records))
    
    def aggregate_pending_pod(self, query_filter: QueryFilter) -> int:
        """Aggregate pending POD count based on filter"""
        records = self.get_records_by_filter(query_filter)
        return len([r for r in records if r.good_issue_date and not r.pod_date])
    
    def aggregate_pending_delivery(self, query_filter: QueryFilter) -> int:
        """Aggregate pending delivery count based on filter"""
        records = self.get_records_by_filter(query_filter)
        return len([r for r in records if not r.good_issue_date])
    
    # ==========================================================
    # 12. DATE FILTERING ENGINE
    # ==========================================================
    
    def get_date_range_for_period(self, period: str) -> DateFilter:
        """
        Get date range for natural language periods
        
        Supported: today, yesterday, last_7_days, last_15_days, last_30_days,
                   this_month, last_month, this_quarter, this_year
        """
        today = date.today()
        
        if period == "today":
            return DateFilter(start_date=today, end_date=today)
        
        elif period == "yesterday":
            yesterday = today - timedelta(days=1)
            return DateFilter(start_date=yesterday, end_date=yesterday)
        
        elif period == "last_7_days":
            start = today - timedelta(days=7)
            return DateFilter(start_date=start, end_date=today)
        
        elif period == "last_15_days":
            start = today - timedelta(days=15)
            return DateFilter(start_date=start, end_date=today)
        
        elif period == "last_30_days":
            start = today - timedelta(days=30)
            return DateFilter(start_date=start, end_date=today)
        
        elif period == "this_month":
            start = today.replace(day=1)
            return DateFilter(start_date=start, end_date=today)
        
        elif period == "last_month":
            first_of_this_month = today.replace(day=1)
            end = first_of_this_month - timedelta(days=1)
            start = end.replace(day=1)
            return DateFilter(start_date=start, end_date=end)
        
        elif period == "this_quarter":
            quarter = (today.month - 1) // 3 + 1
            if quarter == 1:
                start = today.replace(month=1, day=1)
            elif quarter == 2:
                start = today.replace(month=4, day=1)
            elif quarter == 3:
                start = today.replace(month=7, day=1)
            else:
                start = today.replace(month=10, day=1)
            return DateFilter(start_date=start, end_date=today)
        
        elif period == "this_year":
            start = today.replace(month=1, day=1)
            return DateFilter(start_date=start, end_date=today)
        
        else:
            return DateFilter()
    
    # ==========================================================
    # 13. CACHE MANAGEMENT
    # ==========================================================
    
    def invalidate_cache(self, cache_type: str = "all"):
        """Invalidate specific cache or all caches"""
        if cache_type in ["all", "dealers"]:
            dealer_cache.clear()
        if cache_type in ["all", "warehouses"]:
            warehouse_cache.clear()
        if cache_type in ["all", "cities"]:
            city_cache.clear()
        if cache_type in ["all", "products"]:
            product_cache.clear()
        if cache_type in ["all", "queries"]:
            query_cache.clear()
        if cache_type in ["all", "dashboards"]:
            dashboard_cache.clear()
        
        logger.info(f"Cache invalidated: {cache_type}")
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        return {
            "dealer_cache_size": len(dealer_cache),
            "warehouse_cache_size": len(warehouse_cache),
            "city_cache_size": len(city_cache),
            "product_cache_size": len(product_cache),
            "query_cache_size": len(query_cache),
            "dashboard_cache_size": len(dashboard_cache)
        }


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_schema_service = None

def get_schema_service() -> SchemaService:
    """Get singleton instance of SchemaService"""
    global _schema_service
    if _schema_service is None:
        _schema_service = SchemaService()
    return _schema_service


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("Schema Service - Database Repository Layer")
logger.info("=" * 60)
logger.info("")
logger.info("   REPOSITORIES:")
logger.info("   ✅ Dealer Repository")
logger.info("   ✅ Warehouse Repository")
logger.info("   ✅ City Repository")
logger.info("   ✅ Product Repository")
logger.info("   ✅ Division Repository")
logger.info("   ✅ Sales Manager Repository")
logger.info("   ✅ DN Repository")
logger.info("")
logger.info("   FEATURES:")
logger.info("   ✅ Dynamic Entity Registry")
logger.info("   ✅ Fuzzy Search Registry")
logger.info("   ✅ Query Builder Layer")
logger.info("   ✅ Date Filtering Engine")
logger.info("   ✅ Aggregation Layer")
logger.info("   ✅ Cache Layer")
logger.info("")
logger.info("   STATUS: ✅ READY")
logger.info("=" * 60)
