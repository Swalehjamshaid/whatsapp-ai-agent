# ==========================================================
# FILE: app/services/analytics_service.py
# VERSION: v31.1 - DN RESOLUTION HOTFIX
# PURPOSE: Single Source of Truth for ALL Analytics
# ==========================================================

import logging
from typing import Dict, List, Optional, Any, Tuple, Union
from datetime import datetime, timedelta, date
from sqlalchemy import text, func, and_, or_, desc, asc, cast, String, Integer, Float, Date
from sqlalchemy.orm import Session, aliased
from dataclasses import dataclass, field
import json
import re
import uuid
import threading
from cachetools import TTLCache, LRUCache

# Configure logging
logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS FROM APP
# ==========================================================

try:
    from app.models import DeliveryReport
    from app.database import SessionLocal
    logger.info("✅ DeliveryReport model imported")
except ImportError as e:
    logger.error(f"❌ Failed to import DeliveryReport: {e}")
    # Fallback - define model inline if needed
    DeliveryReport = None
    SessionLocal = None

# ==========================================================
# BLOCK 2: RESPONSE CLASS
# ==========================================================

@dataclass
class AnalyticsResponse:
    """Standard response wrapper for analytics operations"""
    data: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None
    error_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "data": self.data,
            "success": self.success,
            "error": self.error,
            "error_id": self.error_id,
            "timestamp": self.timestamp
        }


# ==========================================================
# BLOCK 3: ANALYTICS SERVICE - MAIN CLASS
# ==========================================================

class AnalyticsService:
    """
    Enterprise Analytics Service - Single Source of Truth for ALL Analytics
    
    This service provides comprehensive analytics capabilities including:
    - DN Dashboard & Tracking
    - Dealer Dashboard & Performance
    - Warehouse Dashboard & Coverage
    - City Dashboard & Metrics
    - Product Dashboard & Trends
    - KPI Engine with 10+ KPIs
    - Aging Engine with 7+ metrics
    - Ranking Engine for Top/Bottom N
    - Trend Engine for Daily/Weekly/Monthly
    - Search Engine with Fuzzy Matching
    - Health Framework with PostgreSQL validation
    
    All data comes exclusively from PostgreSQL delivery_reports table.
    NO DUMMY DATA. NO HARDCODED DATA. NO FALLBACK DASHBOARDS.
    """
    
    def __init__(self):
        """Initialize Analytics Service with lazy loading"""
        self.db = None
        self._resolver = None
        self._distance_service = None
        self._dealer_analytics = None
        
        # Caches with TTL
        self._dn_cache = TTLCache(maxsize=2000, ttl=300)
        self._dealer_cache = TTLCache(maxsize=2000, ttl=300)
        self._warehouse_cache = TTLCache(maxsize=2000, ttl=300)
        self._city_cache = TTLCache(maxsize=2000, ttl=300)
        self._product_cache = TTLCache(maxsize=2000, ttl=300)
        self._ranking_cache = TTLCache(maxsize=500, ttl=600)
        self._trend_cache = TTLCache(maxsize=500, ttl=900)
        
        # Health status
        self._health_status = {
            "initialized": False,
            "database_connected": False,
            "last_check": None,
            "status": "unknown",
            "errors": [],
            "warnings": []
        }
        
        # Initialize services
        self._init_services()
        
        logger.info("=" * 70)
        logger.info("📊 Analytics Service v31.1 - DN RESOLUTION HOTFIX")
        logger.info("=" * 70)
        logger.info("✅ Service initialized successfully")
        logger.info("✅ PostgreSQL is the ONLY source of truth")
        logger.info("✅ Enhanced DN resolution with multiple strategies")
        logger.info("=" * 70)
    
    def _init_services(self):
        """Initialize all sub-services with lazy loading"""
        try:
            self._init_postgresql()
            self._init_resolver()
            self._init_distance_service()
            self._init_dealer_analytics()
            self._health_status["initialized"] = True
            logger.info("✅ All services initialized successfully")
        except Exception as e:
            logger.error(f"❌ Service initialization error: {e}")
            self._health_status["initialized"] = False
            self._health_status["errors"].append(str(e))
    
    def _init_postgresql(self):
        """Initialize PostgreSQL connection"""
        try:
            if SessionLocal:
                # Test connection
                session = SessionLocal()
                session.execute(text("SELECT 1"))
                session.close()
                self._health_status["database_connected"] = True
                self._health_status["status"] = "healthy"
                logger.info("✅ PostgreSQL connection established")
            else:
                logger.warning("⚠️ SessionLocal not available")
                self._health_status["database_connected"] = False
        except Exception as e:
            logger.error(f"❌ PostgreSQL connection failed: {e}")
            self._health_status["database_connected"] = False
            self._health_status["status"] = "critical"
            self._health_status["errors"].append(str(e))
    
    def _init_resolver(self):
        """Initialize PostgreSQL resolver"""
        try:
            from app.services.postgresql_resolver import PostgreSQLResolver
            self._resolver = PostgreSQLResolver(SessionLocal)
            logger.info("✅ PostgreSQLResolver initialized")
        except ImportError:
            logger.warning("⚠️ PostgreSQLResolver not available, using built-in resolver")
            self._resolver = None
    
    def _init_distance_service(self):
        """Initialize distance service with lazy loading"""
        try:
            from app.services.distance_service import DistanceService
            self._distance_service = DistanceService()
            logger.info("✅ DistanceService initialized")
        except ImportError:
            logger.warning("⚠️ DistanceService not available")
            self._distance_service = None
    
    def _init_dealer_analytics(self):
        """Initialize dealer analytics with lazy loading"""
        try:
            from app.services.dealer_analytics_service import DealerAnalyticsService
            self._dealer_analytics = DealerAnalyticsService()
            logger.info("✅ DealerAnalyticsService initialized")
        except ImportError:
            logger.warning("⚠️ DealerAnalyticsService not available")
            self._dealer_analytics = None
    
    def _get_session(self) -> Optional[Session]:
        """Get database session"""
        try:
            if SessionLocal:
                return SessionLocal()
            return None
        except Exception as e:
            logger.error(f"❌ Failed to get database session: {e}")
            return None
    
    def _execute_query(self, query: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Execute a raw SQL query and return results as dicts"""
        session = self._get_session()
        if not session:
            return []
        
        try:
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            session.close()
            return rows
        except Exception as e:
            logger.error(f"❌ Query execution failed: {e}")
            session.close()
            return []
    
    def _safe_get(self, data: Dict, key: str, default: Any = None) -> Any:
        """Safely get value from dict with default"""
        if not data:
            return default
        val = data.get(key, default)
        if val is None or val == "":
            return default
        return val

    # ==========================================================
    # BLOCK 4: ENHANCED DN DASHBOARD - COMPLETE REPLACEMENT
    # ==========================================================
    # ==========================================================
# BLOCK 4: ENHANCED DN DASHBOARD - COMPLETE REPLACEMENT
# ==========================================================
    
    def _resolve_dn_enhanced(self, dn_input: str) -> Dict[str, Any]:
        """
        Enhanced DN resolution with multiple strategies and AGGREGATION
        
        Strategies:
        1. Exact match as VARCHAR (aggregated by DN)
        2. Exact match as INTEGER (CAST)
        3. Pattern match with wildcards
        4. Remove leading zeros and try again
        5. Handle decimal points
        6. Handle whitespace and special characters
        """
        if not dn_input:
            return {"found": False, "error": "Empty DN input"}
        
        # Clean the input
        dn_clean = str(dn_input).strip()
        
        # Remove decimal points
        if '.' in dn_clean:
            dn_clean = dn_clean.split('.')[0]
        
        # Remove any non-numeric characters except for the number itself
        dn_numeric = re.sub(r'[^0-9]', '', dn_clean)
        
        if len(dn_numeric) < 8 or len(dn_numeric) > 12:
            return {
                "found": False,
                "error": f"Invalid DN format: {dn_input} (must be 8-12 digits)",
                "normalized": dn_numeric,
                "suggestions": []
            }
        
        session = self._get_session()
        if not session:
            return {"found": False, "error": "Database session unavailable"}
        
        try:
            # ==========================================================
            # STRATEGY 1: Direct VARCHAR match with AGGREGATION
            # ==========================================================
            query_varchar = """
                SELECT 
                    dn_no,
                    MAX(customer_name) AS customer_name,
                    MAX(warehouse) AS warehouse,
                    MAX(ship_to_city) AS ship_to_city,
                    SUM(dn_qty) AS dn_qty,
                    SUM(dn_amount) AS dn_amount,
                    MIN(dn_create_date) AS dn_create_date,
                    MAX(good_issue_date) AS good_issue_date,
                    MAX(pod_date) AS pod_date,
                    MAX(delivery_status) AS delivery_status,
                    MAX(pgi_status) AS pgi_status,
                    MAX(pod_status) AS pod_status,
                    MAX(pending_flag) AS pending_flag,
                    MAX(division) AS division,
                    MAX(customer_code) AS customer_code,
                    MAX(dealer_code) AS dealer_code,
                    MAX(sales_office) AS sales_office,
                    MAX(sales_manager) AS sales_manager,
                    MAX(dn_work) AS dn_work,
                    MAX(order_type) AS order_type,
                    MAX(storage_location) AS storage_location,
                    MAX(delivery_location) AS delivery_location,
                    MAX(remarks) AS remarks,
                    MAX(source_file) AS source_file,
                    MAX(upload_batch_id) AS upload_batch_id,
                    MAX(created_at) AS created_at,
                    MAX(updated_at) AS updated_at,
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE dn_no = :dn_no
                GROUP BY dn_no
            """
            result = session.execute(text(query_varchar), {"dn_no": dn_numeric}).fetchone()
            
            if result:
                session.close()
                logger.info(f"✅ DN found via VARCHAR match (aggregated): {dn_numeric}")
                return self._row_to_dict(result, dn_numeric)
            
            # ==========================================================
            # STRATEGY 2: Try as INTEGER (CAST) with AGGREGATION
            # ==========================================================
            query_int = """
                SELECT 
                    dn_no,
                    MAX(customer_name) AS customer_name,
                    MAX(warehouse) AS warehouse,
                    MAX(ship_to_city) AS ship_to_city,
                    SUM(dn_qty) AS dn_qty,
                    SUM(dn_amount) AS dn_amount,
                    MIN(dn_create_date) AS dn_create_date,
                    MAX(good_issue_date) AS good_issue_date,
                    MAX(pod_date) AS pod_date,
                    MAX(delivery_status) AS delivery_status,
                    MAX(pgi_status) AS pgi_status,
                    MAX(pod_status) AS pod_status,
                    MAX(pending_flag) AS pending_flag,
                    MAX(division) AS division,
                    MAX(customer_code) AS customer_code,
                    MAX(dealer_code) AS dealer_code,
                    MAX(sales_office) AS sales_office,
                    MAX(sales_manager) AS sales_manager,
                    MAX(dn_work) AS dn_work,
                    MAX(order_type) AS order_type,
                    MAX(storage_location) AS storage_location,
                    MAX(delivery_location) AS delivery_location,
                    MAX(remarks) AS remarks,
                    MAX(source_file) AS source_file,
                    MAX(upload_batch_id) AS upload_batch_id,
                    MAX(created_at) AS created_at,
                    MAX(updated_at) AS updated_at,
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE CAST(dn_no AS VARCHAR) = :dn_no
                GROUP BY dn_no
            """
            result = session.execute(text(query_int), {"dn_no": dn_numeric}).fetchone()
            
            if result:
                session.close()
                logger.info(f"✅ DN found via CAST match (aggregated): {dn_numeric}")
                return self._row_to_dict(result, dn_numeric)
            
            # ==========================================================
            # STRATEGY 3: Pattern match with AGGREGATION
            # ==========================================================
            query_pattern = """
                SELECT 
                    dn_no,
                    MAX(customer_name) AS customer_name,
                    MAX(warehouse) AS warehouse,
                    MAX(ship_to_city) AS ship_to_city,
                    SUM(dn_qty) AS dn_qty,
                    SUM(dn_amount) AS dn_amount,
                    MIN(dn_create_date) AS dn_create_date,
                    MAX(good_issue_date) AS good_issue_date,
                    MAX(pod_date) AS pod_date,
                    MAX(delivery_status) AS delivery_status,
                    MAX(pgi_status) AS pgi_status,
                    MAX(pod_status) AS pod_status,
                    MAX(pending_flag) AS pending_flag,
                    MAX(division) AS division,
                    MAX(customer_code) AS customer_code,
                    MAX(dealer_code) AS dealer_code,
                    MAX(sales_office) AS sales_office,
                    MAX(sales_manager) AS sales_manager,
                    MAX(dn_work) AS dn_work,
                    MAX(order_type) AS order_type,
                    MAX(storage_location) AS storage_location,
                    MAX(delivery_location) AS delivery_location,
                    MAX(remarks) AS remarks,
                    MAX(source_file) AS source_file,
                    MAX(upload_batch_id) AS upload_batch_id,
                    MAX(created_at) AS created_at,
                    MAX(updated_at) AS updated_at,
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE dn_no LIKE :pattern
                GROUP BY dn_no
            """
            result = session.execute(text(query_pattern), {"pattern": f"%{dn_numeric}%"}).fetchone()
            
            if result:
                session.close()
                logger.info(f"✅ DN found via pattern match (aggregated): {dn_numeric}")
                return self._row_to_dict(result, dn_numeric)
            
            # ==========================================================
            # STRATEGY 4: Remove leading zeros and try again
            # ==========================================================
            dn_no_leading_zeros = dn_numeric.lstrip('0')
            if dn_no_leading_zeros != dn_numeric and len(dn_no_leading_zeros) >= 8:
                result = session.execute(text(query_varchar), {"dn_no": dn_no_leading_zeros}).fetchone()
                if result:
                    session.close()
                    logger.info(f"✅ DN found after removing leading zeros (aggregated): {dn_no_leading_zeros}")
                    return self._row_to_dict(result, dn_no_leading_zeros)
            
            # ==========================================================
            # STRATEGY 5: Try with different case (if alphanumeric)
            # ==========================================================
            if not dn_numeric.isdigit():
                query_case = """
                    SELECT 
                        dn_no,
                        MAX(customer_name) AS customer_name,
                        MAX(warehouse) AS warehouse,
                        MAX(ship_to_city) AS ship_to_city,
                        SUM(dn_qty) AS dn_qty,
                        SUM(dn_amount) AS dn_amount,
                        MIN(dn_create_date) AS dn_create_date,
                        MAX(good_issue_date) AS good_issue_date,
                        MAX(pod_date) AS pod_date,
                        MAX(delivery_status) AS delivery_status,
                        MAX(pgi_status) AS pgi_status,
                        MAX(pod_status) AS pod_status,
                        MAX(pending_flag) AS pending_flag,
                        MAX(division) AS division,
                        MAX(customer_code) AS customer_code,
                        MAX(dealer_code) AS dealer_code,
                        MAX(sales_office) AS sales_office,
                        MAX(sales_manager) AS sales_manager,
                        MAX(dn_work) AS dn_work,
                        MAX(order_type) AS order_type,
                        MAX(storage_location) AS storage_location,
                        MAX(delivery_location) AS delivery_location,
                        MAX(remarks) AS remarks,
                        MAX(source_file) AS source_file,
                        MAX(upload_batch_id) AS upload_batch_id,
                        MAX(created_at) AS created_at,
                        MAX(updated_at) AS updated_at,
                        COUNT(*) AS material_count
                    FROM delivery_reports
                    WHERE UPPER(dn_no) = UPPER(:dn_no)
                    GROUP BY dn_no
                """
                result = session.execute(text(query_case), {"dn_no": dn_numeric}).fetchone()
                if result:
                    session.close()
                    logger.info(f"✅ DN found via case-insensitive match (aggregated): {dn_numeric}")
                    return self._row_to_dict(result, dn_numeric)
            
            session.close()
            
            # ==========================================================
            # No match found - try to find similar DNs for suggestions
            # ==========================================================
            suggestions = self._find_similar_dns(dn_numeric)
            
            logger.warning(f"⚠️ DN {dn_numeric} not found. Suggestions: {suggestions[:3]}")
            
            return {
                "found": False,
                "error": f"DN {dn_input} not found in database",
                "normalized": dn_numeric,
                "suggestions": suggestions[:5] if suggestions else [],
                "suggestion_message": f"Did you mean one of these?" if suggestions else "No similar DNs found"
            }
            
        except Exception as e:
            session.close()
            logger.error(f"❌ Enhanced DN resolution error: {e}")
            return {"found": False, "error": f"Database error: {str(e)}"}

    def _row_to_dict(self, row, dn_no: str) -> Dict[str, Any]:
        """Convert database row to dictionary with proper validation"""
        if not row:
            return {"found": False}
        
        # Extract values with validation
        customer_name = str(row[1]) if row[1] is not None else None
        warehouse = str(row[2]) if row[2] is not None else None
        ship_to_city = str(row[3]) if row[3] is not None else None
        
        # Validate data quality
        data_quality_warnings = []
        
        if customer_name is None or customer_name.strip() == '':
            data_quality_warnings.append("Customer name is NULL or empty")
            customer_name = "Unknown Dealer"
        
        if warehouse is None or warehouse.strip() == '':
            data_quality_warnings.append("Warehouse is NULL or empty")
            warehouse = "Unknown Warehouse"
        
        if ship_to_city is None or ship_to_city.strip() == '':
            data_quality_warnings.append("Ship to city is NULL or empty")
            ship_to_city = "Unknown City"
        
        data = {
            "dn_no": row[0] or dn_no,
            "customer_name": customer_name,
            "warehouse": warehouse,
            "ship_to_city": ship_to_city,
            "dn_qty": int(row[4]) if row[4] is not None else 0,
            "dn_amount": float(row[5]) if row[5] is not None else 0,
            "dn_create_date": row[6],
            "good_issue_date": row[7],
            "pod_date": row[8],
            "delivery_status": row[9] or "Unknown",
            "pgi_status": row[10] or "Unknown",
            "pod_status": row[11] or "Unknown",
            "pending_flag": row[12] or "N",
            "division": row[13] if len(row) > 13 else "Unknown",
            "customer_code": row[14] if len(row) > 14 else "Unknown",
            "dealer_code": row[15] if len(row) > 15 else "Unknown",
            "sales_office": row[16] if len(row) > 16 else "Unknown",
            "sales_manager": row[17] if len(row) > 17 else "Unknown",
            "dn_work": row[18] if len(row) > 18 else None,
            "order_type": row[19] if len(row) > 19 else None,
            "storage_location": row[20] if len(row) > 20 else None,
            "delivery_location": row[21] if len(row) > 21 else None,
            "remarks": row[22] if len(row) > 22 else None,
            "source_file": row[23] if len(row) > 23 else None,
            "upload_batch_id": row[24] if len(row) > 24 else None,
            "created_at": row[25] if len(row) > 25 else None,
            "updated_at": row[26] if len(row) > 26 else None,
            "material_count": row[27] if len(row) > 27 else 1,
            "found": True,
            "data_quality_warnings": data_quality_warnings
        }
        
        # Format dates
        for date_field in ['dn_create_date', 'good_issue_date', 'pod_date', 'created_at', 'updated_at']:
            if data.get(date_field):
                if isinstance(data[date_field], (datetime, date)):
                    data[date_field] = data[date_field].strftime("%Y-%m-%d %H:%M:%S")
        
        # Calculate aging
        data['dn_aging_days'] = self._calculate_aging_days(data.get('dn_create_date'))
        data['pgi_aging_days'] = self._calculate_aging_days(data.get('good_issue_date'))
        data['pod_aging_days'] = self._calculate_aging_days(data.get('pod_date'))
        
        # Calculate total cycle
        if data.get('dn_create_date') and data.get('pod_date'):
            try:
                dn_date = datetime.fromisoformat(data['dn_create_date']) if isinstance(data['dn_create_date'], str) else data['dn_create_date']
                pod_date = datetime.fromisoformat(data['pod_date']) if isinstance(data['pod_date'], str) else data['pod_date']
                if isinstance(dn_date, (datetime, date)) and isinstance(pod_date, (datetime, date)):
                    data['total_cycle_days'] = (pod_date - dn_date).days
            except:
                data['total_cycle_days'] = 0
        
        # Add status emoji
        status = data.get('delivery_status', '')
        if status in ['Completed', 'Delivered', 'Closed']:
            data['status_emoji'] = '✅'
        elif status in ['In Transit', 'Transit']:
            data['status_emoji'] = '🚚'
        elif status in ['Pending', 'Open']:
            data['status_emoji'] = '⏳'
        else:
            data['status_emoji'] = '❓'
        
        return data

    def _find_similar_dns(self, dn_no: str, limit: int = 5) -> List[str]:
        """Find similar DN numbers for suggestions"""
        session = self._get_session()
        if not session:
            return []
        
        try:
            # Strategy 1: Same prefix
            prefix = dn_no[:4] if len(dn_no) >= 4 else dn_no
            query1 = """
                SELECT DISTINCT dn_no
                FROM delivery_reports
                WHERE CAST(dn_no AS VARCHAR) LIKE :prefix
                AND CAST(dn_no AS VARCHAR) != :dn_no
                LIMIT :limit
            """
            results1 = session.execute(text(query1), {
                "prefix": f"{prefix}%",
                "dn_no": dn_no,
                "limit": limit
            }).fetchall()
            
            if results1:
                session.close()
                return [str(r[0]) for r in results1 if r[0]]
            
            # Strategy 2: Same suffix
            suffix = dn_no[-4:] if len(dn_no) >= 4 else dn_no
            query2 = """
                SELECT DISTINCT dn_no
                FROM delivery_reports
                WHERE CAST(dn_no AS VARCHAR) LIKE :suffix
                AND CAST(dn_no AS VARCHAR) != :dn_no
                LIMIT :limit
            """
            results2 = session.execute(text(query2), {
                "suffix": f"%{suffix}",
                "dn_no": dn_no,
                "limit": limit
            }).fetchall()
            
            if results2:
                session.close()
                return [str(r[0]) for r in results2 if r[0]]
            
            # Strategy 3: Any DNs with similar length
            query3 = """
                SELECT DISTINCT dn_no
                FROM delivery_reports
                WHERE LENGTH(CAST(dn_no AS VARCHAR)) = :length
                AND CAST(dn_no AS VARCHAR) != :dn_no
                LIMIT :limit
            """
            results3 = session.execute(text(query3), {
                "length": len(dn_no),
                "dn_no": dn_no,
                "limit": limit
            }).fetchall()
            
            session.close()
            
            if results3:
                return [str(r[0]) for r in results3 if r[0]]
            
            return []
            
        except Exception as e:
            logger.warning(f"⚠️ Similar DN search failed: {e}")
            try:
                session.close()
            except:
                pass
            return []

    def _calculate_aging_days(self, date_value) -> int:
        """Calculate aging days from a date"""
        if not date_value:
            return 0
        
        try:
            if isinstance(date_value, str):
                date_value = datetime.fromisoformat(date_value.replace('Z', '+00:00'))
            elif isinstance(date_value, datetime):
                pass
            elif isinstance(date_value, date):
                date_value = datetime.combine(date_value, datetime.min.time())
            else:
                return 0
            
            return (datetime.now().date() - date_value.date()).days
        except:
            return 0

    # ==========================================================
    # BLOCK 4A: GET DN DASHBOARD - COMPLETE REPLACEMENT
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete DN dashboard with enhanced resolution and AGGREGATION
        
        Returns:
            DN Number, Dealer, Warehouse, City, Units, Revenue,
            PGI, POD, Delivery Status, Aging, and all metadata
        """
        if not dn_no:
            return self._error_response("DN number is required", "INVALID_DN")
        
        # Clean the input
        dn_clean = str(dn_no).strip()
        
        # Check cache first
        cache_key = f"dn_dashboard:{dn_clean}"
        if cache_key in self._dn_cache:
            cached = self._dn_cache[cache_key]
            # Only return if it's a successful result or a not-found with suggestions
            if cached.get('found', False) or cached.get('suggestions'):
                logger.info(f"✅ DN dashboard cache hit: {dn_clean}")
                return cached
        
        logger.info(f"🔍 Retrieving DN dashboard for: {dn_clean}")
        
        # Use enhanced resolution
        resolution_result = self._resolve_dn_enhanced(dn_clean)
        
        if not resolution_result.get('found', False):
            # Return structured error with suggestions
            suggestions = resolution_result.get('suggestions', [])
            error_msg = resolution_result.get('error', f"DN {dn_clean} not found")
            normalized = resolution_result.get('normalized', dn_clean)
            
            result = self._error_response(
                error_msg,
                "DN_NOT_FOUND",
                {
                    "dn_no": dn_clean,
                    "normalized": normalized,
                    "suggestions": suggestions[:5] if suggestions else []
                }
            )
            
            # Add suggestions to the response
            if suggestions:
                result["suggestions"] = suggestions[:5]
                result["message"] = f"DN {dn_clean} not found. Did you mean: {', '.join(suggestions[:3])}?"
                result["suggestion_count"] = len(suggestions)
            
            self._dn_cache[cache_key] = result
            logger.warning(f"⚠️ DN {dn_clean} not found. Suggestions: {suggestions[:3] if suggestions else 'None'}")
            return result
        
        # Extract the found data
        data = resolution_result
        
        # Check for data quality warnings
        data_quality_warnings = data.get('data_quality_warnings', [])
        if data_quality_warnings:
            logger.warning(f"⚠️ Data quality warnings for DN {dn_clean}: {data_quality_warnings}")
        
        # Format the response
        response = {
            "success": True,
            "data": data,
            "dn_no": data.get('dn_no', dn_clean),
            "found": True,
            "resolution_strategy": "enhanced_match",
            "material_count": data.get('material_count', 1),
            "data_quality_warnings": data_quality_warnings
        }
        
        # Add distance information if available
        if self._distance_service:
            try:
                warehouse = data.get('warehouse')
                city = data.get('ship_to_city')
                if warehouse and city and warehouse != 'Unknown' and city != 'Unknown':
                    distance_result = self._distance_service.calculate_warehouse_distance(warehouse, city)
                    if distance_result and distance_result.get('success'):
                        response["distance_km"] = distance_result.get('distance_km')
                        response["distance_miles"] = distance_result.get('distance_miles')
                        response["approx_driving_hours"] = distance_result.get('approx_driving_hours')
                        response["approx_driving_minutes"] = distance_result.get('approx_driving_minutes')
                        response["distance_type"] = distance_result.get('distance_type', 'unknown')
                        logger.info(f"✅ Distance calculated: {distance_result.get('distance_km')} km")
            except Exception as e:
                logger.warning(f"⚠️ Distance calculation failed: {e}")
        
        self._dn_cache[cache_key] = response
        logger.info(f"✅ DN dashboard retrieved for: {dn_clean}")
        return response

        # ==========================================================
    # BLOCK 4A: GET DN DASHBOARD - COMPLETE REPLACEMENT
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete DN dashboard with enhanced resolution
        
        Returns:
            DN Number, Dealer, Warehouse, City, Units, Revenue,
            PGI, POD, Delivery Status, Aging, and all metadata
        """
        if not dn_no:
            return self._error_response("DN number is required", "INVALID_DN")
        
        # Clean the input
        dn_clean = str(dn_no).strip()
        
        # Check cache first
        cache_key = f"dn_dashboard:{dn_clean}"
        if cache_key in self._dn_cache:
            cached = self._dn_cache[cache_key]
            # Only return if it's a successful result or a not-found with suggestions
            if cached.get('found', False) or cached.get('suggestions'):
                logger.info(f"✅ DN dashboard cache hit: {dn_clean}")
                return cached
        
        logger.info(f"🔍 Retrieving DN dashboard for: {dn_clean}")
        
        # Use enhanced resolution
        resolution_result = self._resolve_dn_enhanced(dn_clean)
        
        if not resolution_result.get('found', False):
            # Return structured error with suggestions
            suggestions = resolution_result.get('suggestions', [])
            error_msg = resolution_result.get('error', f"DN {dn_clean} not found")
            normalized = resolution_result.get('normalized', dn_clean)
            
            result = self._error_response(
                error_msg,
                "DN_NOT_FOUND",
                {
                    "dn_no": dn_clean,
                    "normalized": normalized,
                    "suggestions": suggestions[:5] if suggestions else []
                }
            )
            
            # Add suggestions to the response
            if suggestions:
                result["suggestions"] = suggestions[:5]
                result["message"] = f"DN {dn_clean} not found. Did you mean: {', '.join(suggestions[:3])}?"
                result["suggestion_count"] = len(suggestions)
            
            self._dn_cache[cache_key] = result
            logger.warning(f"⚠️ DN {dn_clean} not found. Suggestions: {suggestions[:3] if suggestions else 'None'}")
            return result
        
        # Extract the found data
        data = resolution_result
        
        # Format the response
        response = {
            "success": True,
            "data": data,
            "dn_no": data.get('dn_no', dn_clean),
            "found": True,
            "resolution_strategy": "enhanced_match"
        }
        
        # Add distance information if available
        if self._distance_service:
            try:
                warehouse = data.get('warehouse')
                city = data.get('ship_to_city')
                if warehouse and city and warehouse != 'Unknown' and city != 'Unknown':
                    distance_result = self._distance_service.calculate_warehouse_distance(warehouse, city)
                    if distance_result and distance_result.get('success'):
                        response["distance_km"] = distance_result.get('distance_km')
                        response["distance_miles"] = distance_result.get('distance_miles')
                        response["approx_driving_hours"] = distance_result.get('approx_driving_hours')
                        response["approx_driving_minutes"] = distance_result.get('approx_driving_minutes')
                        response["distance_type"] = distance_result.get('distance_type', 'unknown')
                        logger.info(f"✅ Distance calculated: {distance_result.get('distance_km')} km")
            except Exception as e:
                logger.warning(f"⚠️ Distance calculation failed: {e}")
        
        self._dn_cache[cache_key] = response
        logger.info(f"✅ DN dashboard retrieved for: {dn_clean}")
        return response

    # ==========================================================
    # BLOCK 5: DEALER DASHBOARD
    # ==========================================================
  # BLOCK 5: DEALER DASHBOARD - IMPROVED MATCHING
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer: str) -> Dict[str, Any]:
        """
        Get comprehensive dealer dashboard with improved matching
        
        Uses TRIM(LOWER()) for better matching
        Supports partial matching with LIKE
        Aggregates all DN data for the dealer
        """
        if not dealer:
            return self._error_response("Dealer name is required", "INVALID_DEALER")
        
        dealer_clean = str(dealer).strip()
        
        # Check cache
        cache_key = f"dealer_dashboard:{dealer_clean.lower()}"
        if cache_key in self._dealer_cache:
            cached = self._dealer_cache[cache_key]
            if cached.get('success', False):
                logger.info(f"✅ Dealer dashboard cache hit: {dealer_clean}")
                return cached
        
        logger.info(f"🔍 Retrieving dealer dashboard for: {dealer_clean}")
        
        # Query dealer data with improved matching
        query = """
            SELECT 
                MAX(customer_name) as dealer_name,
                MAX(dealer_code) as dealer_code,
                MAX(customer_code) as customer_code,
                MAX(division) as division,
                MAX(warehouse) as warehouse,
                MAX(warehouse_code) as warehouse_code,
                MAX(ship_to_city) as city,
                MAX(sales_office) as sales_office,
                MAX(sales_manager) as sales_manager,
                COUNT(DISTINCT dn_no) as total_dns,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                COUNT(DISTINCT warehouse) as warehouses_used,
                COUNT(DISTINCT ship_to_city) as cities_served,
                -- Status breakdown
                COUNT(DISTINCT CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN dn_no END) as delivered_dns,
                COUNT(DISTINCT CASE WHEN delivery_status IN ('Pending', 'Open') THEN dn_no END) as pending_dns,
                COUNT(DISTINCT CASE WHEN delivery_status IN ('In Transit', 'Transit') THEN dn_no END) as transit_dns,
                -- PGI status
                COUNT(DISTINCT CASE WHEN pgi_status = 'Completed' THEN dn_no END) as pgi_completed,
                -- POD status
                COUNT(DISTINCT CASE WHEN pod_status = 'Completed' THEN dn_no END) as pod_completed,
                -- Pending flag
                COUNT(DISTINCT CASE WHEN pending_flag = 'Y' THEN dn_no END) as pending_flag_count,
                -- Date metrics
                MIN(dn_create_date) as first_dn_date,
                MAX(dn_create_date) as last_dn_date,
                -- Average values
                AVG(dn_qty) as avg_units_per_dn,
                AVG(dn_amount) as avg_revenue_per_dn,
                -- Aging (average across all DNs)
                AVG(EXTRACT(DAY FROM COALESCE(CURRENT_DATE, dn_create_date) - dn_create_date)) as avg_aging_days,
                -- Calculate rates
                CASE WHEN COUNT(DISTINCT dn_no) > 0 
                    THEN ROUND(COUNT(DISTINCT CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN dn_no END) * 100.0 / COUNT(DISTINCT dn_no), 2)
                    ELSE 0 
                END as delivery_rate,
                CASE WHEN COUNT(DISTINCT dn_no) > 0 
                    THEN ROUND(COUNT(DISTINCT CASE WHEN pgi_status = 'Completed' THEN dn_no END) * 100.0 / COUNT(DISTINCT dn_no), 2)
                    ELSE 0 
                END as pgi_rate,
                CASE WHEN COUNT(DISTINCT dn_no) > 0 
                    THEN ROUND(COUNT(DISTINCT CASE WHEN pod_status = 'Completed' THEN dn_no END) * 100.0 / COUNT(DISTINCT dn_no), 2)
                    ELSE 0 
                END as pod_rate
            FROM delivery_reports
            WHERE TRIM(LOWER(customer_name)) LIKE TRIM(LOWER(:dealer))
            GROUP BY customer_name
            ORDER BY total_revenue DESC
            LIMIT 1
        """
        
        results = self._execute_query(query, {"dealer": f"%{dealer_clean}%"})
        
        if not results:
            result = self._error_response(
                f"Dealer '{dealer_clean}' not found",
                "DEALER_NOT_FOUND",
                {"dealer": dealer_clean}
            )
            self._dealer_cache[cache_key] = result
            return result
        
        data = results[0]
        
        # Calculate derived KPIs
        total_dns = data.get('total_dns', 0) or 0
        delivered = data.get('delivered_dns', 0) or 0
        pending = data.get('pending_dns', 0) or 0
        transit = data.get('transit_dns', 0) or 0
        pgi_completed = data.get('pgi_completed', 0) or 0
        pod_completed = data.get('pod_completed', 0) or 0
        
        data['delivery_rate'] = round((delivered / total_dns * 100) if total_dns > 0 else 0, 2)
        data['pgi_rate'] = round((pgi_completed / total_dns * 100) if total_dns > 0 else 0, 2)
        data['pod_rate'] = round((pod_completed / total_dns * 100) if total_dns > 0 else 0, 2)
        data['pending_rate'] = round((pending / total_dns * 100) if total_dns > 0 else 0, 2)
        data['transit_rate'] = round((transit / total_dns * 100) if total_dns > 0 else 0, 2)
        
        # Health Score
        health_score = self._calculate_health_score(data)
        data['health_score'] = health_score
        data['health_score_text'] = self._get_health_score_text(health_score)
        
        # Risk Score
        risk_score = self._calculate_risk_score(data)
        data['risk_score'] = risk_score
        data['risk_level'] = self._get_risk_level(risk_score)
        
        # Performance Score
        performance_score = self._calculate_performance_score(data)
        data['performance_score'] = performance_score
        data['performance_level'] = self._get_performance_level(performance_score)
        
        # Add distance if available
        if self._distance_service:
            try:
                warehouse = data.get('warehouse')
                city = data.get('city')
                if warehouse and city and warehouse != 'Unknown' and city != 'Unknown':
                    distance_result = self._distance_service.calculate_warehouse_distance(warehouse, city)
                    if distance_result and distance_result.get('success'):
                        data['distance_km'] = distance_result.get('distance_km')
                        data['distance_miles'] = distance_result.get('distance_miles')
                        data['approx_driving_hours'] = distance_result.get('approx_driving_hours')
                        data['approx_driving_minutes'] = distance_result.get('approx_driving_minutes')
            except Exception as e:
                logger.warning(f"⚠️ Distance calculation failed: {e}")
        
        # Get recent DNs (aggregated)
        recent_query = """
            SELECT 
                dn_no,
                MAX(customer_name) as dealer,
                MAX(warehouse) as warehouse,
                MAX(ship_to_city) as city,
                SUM(dn_qty) as units,
                SUM(dn_amount) as revenue,
                MIN(dn_create_date) as dn_create_date,
                MAX(good_issue_date) as pgi_date,
                MAX(pod_date) as pod_date,
                MAX(delivery_status) as delivery_status
            FROM delivery_reports
            WHERE TRIM(LOWER(customer_name)) LIKE TRIM(LOWER(:dealer))
            GROUP BY dn_no
            ORDER BY MIN(dn_create_date) DESC
            LIMIT 5
        """
        recent_results = self._execute_query(recent_query, {"dealer": f"%{dealer_clean}%"})
        
        # Format recent DNs
        for r in recent_results:
            for date_field in ['dn_create_date', 'pgi_date', 'pod_date']:
                if r.get(date_field):
                    if isinstance(r[date_field], (datetime, date)):
                        r[date_field] = r[date_field].strftime("%Y-%m-%d")
        
        data['recent_dns'] = recent_results
        
        # Get monthly trend
        trend_query = """
            SELECT 
                DATE_TRUNC('month', dn_create_date) as month,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units
            FROM delivery_reports
            WHERE TRIM(LOWER(customer_name)) LIKE TRIM(LOWER(:dealer))
            GROUP BY DATE_TRUNC('month', dn_create_date)
            ORDER BY month DESC
            LIMIT 6
        """
        trend_results = self._execute_query(trend_query, {"dealer": f"%{dealer_clean}%"})
        
        for r in trend_results:
            if r.get('month'):
                if isinstance(r['month'], (datetime, date)):
                    r['month'] = r['month'].strftime("%Y-%m")
        
        data['monthly_trend'] = trend_results
        
        result = {
            "success": True,
            "data": data,
            "dealer": dealer_clean,
            "found": True
        }
        
        self._dealer_cache[cache_key] = result
        logger.info(f"✅ Dealer dashboard retrieved for: {dealer_clean}")
        return result

    # ==========================================================
    # BLOCK 9: SEARCH METHODS - IMPROVED WITH AGGREGATION
    # ==========================================================
    
    def search_dn(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for DNs matching the query with AGGREGATION
        
        Returns DN-level aggregated data:
        - DN Count = COUNT(DISTINCT dn_no)
        - Units = SUM(dn_qty)
        - Revenue = SUM(dn_amount)
        - Dealer = MAX(customer_name)
        - Warehouse = MAX(warehouse)
        - City = MAX(ship_to_city)
        """
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        # If query is numeric, try exact match first
        if query_clean.isdigit():
            exact_query = """
                SELECT 
                    dn_no,
                    MAX(customer_name) as dealer,
                    MAX(warehouse) as warehouse,
                    MAX(ship_to_city) as city,
                    SUM(dn_qty) as units,
                    SUM(dn_amount) as revenue,
                    MIN(dn_create_date) as dn_create_date,
                    MAX(good_issue_date) as pgi_date,
                    MAX(pod_date) as pod_date,
                    MAX(delivery_status) as delivery_status,
                    COUNT(*) as material_count
                FROM delivery_reports
                WHERE dn_no = :dn_no
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) DESC
                LIMIT :limit
            """
            results = self._execute_query(exact_query, {"dn_no": query_clean, "limit": limit})
            if results:
                return self._format_search_results(results)
        
        # Generic search with AGGREGATION
        search_query = """
            SELECT 
                dn_no,
                MAX(customer_name) as dealer,
                MAX(warehouse) as warehouse,
                MAX(ship_to_city) as city,
                SUM(dn_qty) as units,
                SUM(dn_amount) as revenue,
                MIN(dn_create_date) as dn_create_date,
                MAX(good_issue_date) as pgi_date,
                MAX(pod_date) as pod_date,
                MAX(delivery_status) as delivery_status,
                COUNT(*) as material_count
            FROM delivery_reports
            WHERE dn_no LIKE :query
               OR customer_name LIKE :query
               OR warehouse LIKE :query
               OR ship_to_city LIKE :query
            GROUP BY dn_no
            ORDER BY MIN(dn_create_date) DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def search_dealer(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for dealers matching the query with improved TRIM matching"""
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        search_query = """
            SELECT 
                MAX(customer_name) as dealer,
                MAX(dealer_code) as dealer_code,
                MAX(customer_code) as customer_code,
                MAX(warehouse) as main_warehouse,
                MAX(ship_to_city) as main_city,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                ROUND(COUNT(DISTINCT CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN dn_no END) * 100.0 / COUNT(DISTINCT dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE TRIM(LOWER(customer_name)) LIKE TRIM(LOWER(:query))
            GROUP BY customer_name
            ORDER BY total_revenue DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def search_warehouse(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for warehouses matching the query"""
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        search_query = """
            SELECT 
                warehouse,
                warehouse_code,
                COUNT(DISTINCT customer_name) as dealers,
                COUNT(DISTINCT ship_to_city) as cities,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                ROUND(COUNT(DISTINCT CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN dn_no END) * 100.0 / COUNT(DISTINCT dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE TRIM(LOWER(warehouse)) LIKE TRIM(LOWER(:query))
            GROUP BY warehouse, warehouse_code
            ORDER BY total_revenue DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def search_city(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for cities matching the query"""
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        search_query = """
            SELECT 
                ship_to_city as city,
                COUNT(DISTINCT customer_name) as dealers,
                COUNT(DISTINCT warehouse) as warehouses,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                ROUND(COUNT(DISTINCT CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN dn_no END) * 100.0 / COUNT(DISTINCT dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE TRIM(LOWER(ship_to_city)) LIKE TRIM(LOWER(:query))
            GROUP BY ship_to_city
            ORDER BY total_revenue DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def search_product(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for products matching the query"""
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        search_query = """
            SELECT 
                customer_model as product,
                material_no,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                COUNT(DISTINCT customer_name) as dealers,
                COUNT(DISTINCT ship_to_city) as cities,
                ROUND(COUNT(DISTINCT CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN dn_no END) * 100.0 / COUNT(DISTINCT dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE TRIM(LOWER(customer_model)) LIKE TRIM(LOWER(:query))
            GROUP BY customer_model, material_no
            ORDER BY total_revenue DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def _format_search_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format search results for consistent output"""
        formatted = []
        for r in results:
            # Format dates
            for date_field in ['dn_create_date', 'pgi_date', 'pod_date', 'created_at', 'updated_at']:
                if r.get(date_field):
                    if isinstance(r[date_field], (datetime, date)):
                        r[date_field] = r[date_field].strftime("%Y-%m-%d")
            
            # Format numeric values
            for num_field in ['units', 'revenue', 'dn_count', 'total_units', 'total_revenue', 'dn_qty', 'dn_amount']:
                if r.get(num_field) is not None:
                    if isinstance(r[num_field], (int, float)):
                        if 'revenue' in num_field or 'amount' in num_field:
                            r[num_field] = round(float(r[num_field]), 2)
                        else:
                            r[num_field] = int(r[num_field])
            
            formatted.append(r)
        return formatted
    # ==========================================================
    # BLOCK 6: WAREHOUSE DASHBOARD
    # ==========================================================
    
    def get_warehouse_dashboard(self, warehouse: str) -> Dict[str, Any]:
        """
        Get comprehensive warehouse dashboard with metrics, coverage,
        and performance analytics
        """
        if not warehouse:
            return self._error_response("Warehouse name is required", "INVALID_WAREHOUSE")
        
        warehouse_clean = str(warehouse).strip()
        
        # Check cache
        cache_key = f"warehouse_dashboard:{warehouse_clean.lower()}"
        if cache_key in self._warehouse_cache:
            cached = self._warehouse_cache[cache_key]
            if cached.get('success', False):
                logger.info(f"✅ Warehouse dashboard cache hit: {warehouse_clean}")
                return cached
        
        logger.info(f"🔍 Retrieving warehouse dashboard for: {warehouse_clean}")
        
        query = """
            SELECT 
                warehouse,
                warehouse_code,
                MAX(sales_office) as sales_office,
                COUNT(DISTINCT customer_name) as unique_dealers,
                COUNT(DISTINCT ship_to_city) as cities_served,
                COUNT(DISTINCT dn_no) as total_dns,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                -- Status breakdown
                COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) as delivered_dns,
                COUNT(CASE WHEN delivery_status IN ('Pending', 'Open') THEN 1 END) as pending_dns,
                COUNT(CASE WHEN delivery_status IN ('In Transit', 'Transit') THEN 1 END) as transit_dns,
                -- Rates
                CASE WHEN COUNT(dn_no) > 0 
                    THEN ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2)
                    ELSE 0 
                END as delivery_rate,
                -- Average values
                AVG(dn_qty) as avg_units_per_dn,
                AVG(dn_amount) as avg_revenue_per_dn,
                -- Aging
                AVG(EXTRACT(DAY FROM COALESCE(CURRENT_DATE, dn_create_date) - dn_create_date)) as avg_aging_days,
                -- Date ranges
                MIN(dn_create_date) as first_dn_date,
                MAX(dn_create_date) as last_dn_date
            FROM delivery_reports
            WHERE LOWER(warehouse) LIKE LOWER(:warehouse)
            GROUP BY warehouse, warehouse_code
            ORDER BY total_revenue DESC
            LIMIT 1
        """
        
        results = self._execute_query(query, {"warehouse": f"%{warehouse_clean}%"})
        
        if not results:
            result = self._error_response(
                f"Warehouse '{warehouse_clean}' not found",
                "WAREHOUSE_NOT_FOUND",
                {"warehouse": warehouse_clean}
            )
            self._warehouse_cache[cache_key] = result
            return result
        
        data = results[0]
        
        # Calculate derived metrics
        total_dns = data.get('total_dns', 0) or 0
        delivered = data.get('delivered_dns', 0) or 0
        pending = data.get('pending_dns', 0) or 0
        transit = data.get('transit_dns', 0) or 0
        
        data['pending_rate'] = round((pending / total_dns * 100) if total_dns > 0 else 0, 2)
        data['transit_rate'] = round((transit / total_dns * 100) if total_dns > 0 else 0, 2)
        
        # Health Score
        health_score = self._calculate_health_score(data)
        data['health_score'] = health_score
        data['health_score_text'] = self._get_health_score_text(health_score)
        
        # Performance Score
        performance_score = self._calculate_performance_score(data)
        data['performance_score'] = performance_score
        data['performance_level'] = self._get_performance_level(performance_score)
        
        # Get top dealers
        dealers_query = """
            SELECT 
                customer_name as dealer,
                COUNT(dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE LOWER(warehouse) LIKE LOWER(:warehouse)
            GROUP BY customer_name
            ORDER BY revenue DESC
            LIMIT 5
        """
        top_dealers = self._execute_query(dealers_query, {"warehouse": f"%{warehouse_clean}%"})
        data['top_dealers'] = top_dealers
        
        # Get cities served
        cities_query = """
            SELECT 
                ship_to_city as city,
                COUNT(dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units,
                COUNT(DISTINCT customer_name) as dealers
            FROM delivery_reports
            WHERE LOWER(warehouse) LIKE LOWER(:warehouse)
            GROUP BY ship_to_city
            ORDER BY revenue DESC
            LIMIT 10
        """
        cities = self._execute_query(cities_query, {"warehouse": f"%{warehouse_clean}%"})
        data['cities_served_list'] = cities
        
        # Add distance coverage if available
        if self._distance_service:
            try:
                coverage = self._distance_service.get_warehouse_coverage(warehouse_clean)
                if coverage and coverage.get('success'):
                    data['avg_distance_km'] = coverage.get('average_distance_km')
                    data['max_distance_km'] = coverage.get('max_distance_km')
                    data['min_distance_km'] = coverage.get('min_distance_km')
                    data['coverage_cities'] = coverage.get('cities', [])
            except Exception as e:
                logger.warning(f"⚠️ Coverage calculation failed: {e}")
        
        result = {
            "success": True,
            "data": data,
            "warehouse": warehouse_clean,
            "found": True
        }
        
        self._warehouse_cache[cache_key] = result
        logger.info(f"✅ Warehouse dashboard retrieved for: {warehouse_clean}")
        return result

    # ==========================================================
    # BLOCK 7: CITY DASHBOARD
    # ==========================================================
    
    def get_city_dashboard(self, city: str) -> Dict[str, Any]:
        """
        Get comprehensive city dashboard with metrics, dealers,
        warehouses, and performance analytics
        """
        if not city:
            return self._error_response("City name is required", "INVALID_CITY")
        
        city_clean = str(city).strip()
        
        # Check cache
        cache_key = f"city_dashboard:{city_clean.lower()}"
        if cache_key in self._city_cache:
            cached = self._city_cache[cache_key]
            if cached.get('success', False):
                logger.info(f"✅ City dashboard cache hit: {city_clean}")
                return cached
        
        logger.info(f"🔍 Retrieving city dashboard for: {city_clean}")
        
        query = """
            SELECT 
                ship_to_city as city,
                COUNT(DISTINCT customer_name) as unique_dealers,
                COUNT(DISTINCT warehouse) as unique_warehouses,
                COUNT(DISTINCT dn_no) as total_dns,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                -- Status breakdown
                COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) as delivered_dns,
                COUNT(CASE WHEN delivery_status IN ('Pending', 'Open') THEN 1 END) as pending_dns,
                COUNT(CASE WHEN delivery_status IN ('In Transit', 'Transit') THEN 1 END) as transit_dns,
                -- Rates
                CASE WHEN COUNT(dn_no) > 0 
                    THEN ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2)
                    ELSE 0 
                END as delivery_rate,
                -- Average values
                AVG(dn_qty) as avg_units_per_dn,
                AVG(dn_amount) as avg_revenue_per_dn,
                -- Aging
                AVG(EXTRACT(DAY FROM COALESCE(CURRENT_DATE, dn_create_date) - dn_create_date)) as avg_aging_days
            FROM delivery_reports
            WHERE LOWER(ship_to_city) LIKE LOWER(:city)
            GROUP BY ship_to_city
            ORDER BY total_revenue DESC
            LIMIT 1
        """
        
        results = self._execute_query(query, {"city": f"%{city_clean}%"})
        
        if not results:
            result = self._error_response(
                f"City '{city_clean}' not found",
                "CITY_NOT_FOUND",
                {"city": city_clean}
            )
            self._city_cache[cache_key] = result
            return result
        
        data = results[0]
        
        # Calculate derived metrics
        total_dns = data.get('total_dns', 0) or 0
        delivered = data.get('delivered_dns', 0) or 0
        pending = data.get('pending_dns', 0) or 0
        transit = data.get('transit_dns', 0) or 0
        
        data['pending_rate'] = round((pending / total_dns * 100) if total_dns > 0 else 0, 2)
        data['transit_rate'] = round((transit / total_dns * 100) if total_dns > 0 else 0, 2)
        
        # Performance Score
        performance_score = self._calculate_performance_score(data)
        data['performance_score'] = performance_score
        data['performance_level'] = self._get_performance_level(performance_score)
        
        # Get top dealers
        dealers_query = """
            SELECT 
                customer_name as dealer,
                COUNT(dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE LOWER(ship_to_city) LIKE LOWER(:city)
            GROUP BY customer_name
            ORDER BY revenue DESC
            LIMIT 5
        """
        top_dealers = self._execute_query(dealers_query, {"city": f"%{city_clean}%"})
        data['top_dealers'] = top_dealers
        
        # Get warehouses serving this city
        warehouses_query = """
            SELECT 
                warehouse,
                COUNT(dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units,
                COUNT(DISTINCT customer_name) as dealers
            FROM delivery_reports
            WHERE LOWER(ship_to_city) LIKE LOWER(:city)
            GROUP BY warehouse
            ORDER BY revenue DESC
            LIMIT 10
        """
        warehouses = self._execute_query(warehouses_query, {"city": f"%{city_clean}%"})
        data['warehouses_serving'] = warehouses
        
        # Get monthly trend
        trend_query = """
            SELECT 
                DATE_TRUNC('month', dn_create_date) as month,
                COUNT(dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units
            FROM delivery_reports
            WHERE LOWER(ship_to_city) LIKE LOWER(:city)
            GROUP BY DATE_TRUNC('month', dn_create_date)
            ORDER BY month DESC
            LIMIT 6
        """
        trend_results = self._execute_query(trend_query, {"city": f"%{city_clean}%"})
        
        for r in trend_results:
            if r.get('month'):
                if isinstance(r['month'], (datetime, date)):
                    r['month'] = r['month'].strftime("%Y-%m")
        
        data['monthly_trend'] = trend_results
        
        result = {
            "success": True,
            "data": data,
            "city": city_clean,
            "found": True
        }
        
        self._city_cache[cache_key] = result
        logger.info(f"✅ City dashboard retrieved for: {city_clean}")
        return result

    # ==========================================================
    # BLOCK 8: PRODUCT DASHBOARD
    # ==========================================================
    
    def get_product_dashboard(self, product: str) -> Dict[str, Any]:
        """
        Get comprehensive product dashboard with metrics, dealers,
        cities, and performance analytics
        """
        if not product:
            return self._error_response("Product name is required", "INVALID_PRODUCT")
        
        product_clean = str(product).strip()
        
        # Check cache
        cache_key = f"product_dashboard:{product_clean.lower()}"
        if cache_key in self._product_cache:
            cached = self._product_cache[cache_key]
            if cached.get('success', False):
                logger.info(f"✅ Product dashboard cache hit: {product_clean}")
                return cached
        
        logger.info(f"🔍 Retrieving product dashboard for: {product_clean}")
        
        # Try customer_model first, then material_no
        query = """
            SELECT 
                customer_model as product,
                material_no,
                COUNT(DISTINCT dn_no) as total_dns,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                COUNT(DISTINCT customer_name) as unique_dealers,
                COUNT(DISTINCT ship_to_city) as unique_cities,
                COUNT(DISTINCT warehouse) as unique_warehouses,
                -- Status breakdown
                COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) as delivered_dns,
                COUNT(CASE WHEN delivery_status IN ('Pending', 'Open') THEN 1 END) as pending_dns,
                COUNT(CASE WHEN delivery_status IN ('In Transit', 'Transit') THEN 1 END) as transit_dns,
                -- Rates
                CASE WHEN COUNT(dn_no) > 0 
                    THEN ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2)
                    ELSE 0 
                END as delivery_rate,
                -- Average values
                AVG(dn_qty) as avg_units_per_dn,
                AVG(dn_amount) as avg_revenue_per_dn,
                -- Aging
                AVG(EXTRACT(DAY FROM COALESCE(CURRENT_DATE, dn_create_date) - dn_create_date)) as avg_aging_days,
                MIN(dn_create_date) as first_dn_date,
                MAX(dn_create_date) as last_dn_date
            FROM delivery_reports
            WHERE LOWER(customer_model) LIKE LOWER(:product)
            GROUP BY customer_model, material_no
            ORDER BY total_revenue DESC
            LIMIT 1
        """
        
        results = self._execute_query(query, {"product": f"%{product_clean}%"})
        
        if not results:
            # Try material_no
            query_material = """
                SELECT 
                    material_no as product,
                    customer_model,
                    COUNT(DISTINCT dn_no) as total_dns,
                    SUM(dn_qty) as total_units,
                    SUM(dn_amount) as total_revenue,
                    COUNT(DISTINCT customer_name) as unique_dealers,
                    COUNT(DISTINCT ship_to_city) as unique_cities,
                    COUNT(DISTINCT warehouse) as unique_warehouses,
                    CASE WHEN COUNT(dn_no) > 0 
                        THEN ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2)
                        ELSE 0 
                    END as delivery_rate,
                    AVG(dn_qty) as avg_units_per_dn,
                    AVG(dn_amount) as avg_revenue_per_dn,
                    AVG(EXTRACT(DAY FROM COALESCE(CURRENT_DATE, dn_create_date) - dn_create_date)) as avg_aging_days
                FROM delivery_reports
                WHERE LOWER(material_no) LIKE LOWER(:product)
                GROUP BY material_no, customer_model
                ORDER BY total_revenue DESC
                LIMIT 1
            """
            results = self._execute_query(query_material, {"product": f"%{product_clean}%"})
        
        if not results:
            result = self._error_response(
                f"Product '{product_clean}' not found",
                "PRODUCT_NOT_FOUND",
                {"product": product_clean}
            )
            self._product_cache[cache_key] = result
            return result
        
        data = results[0]
        
        # Calculate derived metrics
        total_dns = data.get('total_dns', 0) or 0
        delivered = data.get('delivered_dns', 0) or 0
        pending = data.get('pending_dns', 0) or 0
        transit = data.get('transit_dns', 0) or 0
        
        data['pending_rate'] = round((pending / total_dns * 100) if total_dns > 0 else 0, 2)
        data['transit_rate'] = round((transit / total_dns * 100) if total_dns > 0 else 0, 2)
        
        # Performance Score
        performance_score = self._calculate_performance_score(data)
        data['performance_score'] = performance_score
        data['performance_level'] = self._get_performance_level(performance_score)
        
        # Get top dealers for this product
        dealers_query = """
            SELECT 
                customer_name as dealer,
                COUNT(dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units
            FROM delivery_reports
            WHERE LOWER(customer_model) LIKE LOWER(:product)
            GROUP BY customer_name
            ORDER BY revenue DESC
            LIMIT 5
        """
        top_dealers = self._execute_query(dealers_query, {"product": f"%{product_clean}%"})
        data['top_dealers'] = top_dealers
        
        # Get cities where product is sold
        cities_query = """
            SELECT 
                ship_to_city as city,
                COUNT(dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units
            FROM delivery_reports
            WHERE LOWER(customer_model) LIKE LOWER(:product)
            GROUP BY ship_to_city
            ORDER BY revenue DESC
            LIMIT 10
        """
        cities = self._execute_query(cities_query, {"product": f"%{product_clean}%"})
        data['cities_sold'] = cities
        
        # Get monthly trend
        trend_query = """
            SELECT 
                DATE_TRUNC('month', dn_create_date) as month,
                COUNT(dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units
            FROM delivery_reports
            WHERE LOWER(customer_model) LIKE LOWER(:product)
            GROUP BY DATE_TRUNC('month', dn_create_date)
            ORDER BY month DESC
            LIMIT 6
        """
        trend_results = self._execute_query(trend_query, {"product": f"%{product_clean}%"})
        
        for r in trend_results:
            if r.get('month'):
                if isinstance(r['month'], (datetime, date)):
                    r['month'] = r['month'].strftime("%Y-%m")
        
        data['monthly_trend'] = trend_results
        
        result = {
            "success": True,
            "data": data,
            "product": product_clean,
            "found": True
        }
        
        self._product_cache[cache_key] = result
        logger.info(f"✅ Product dashboard retrieved for: {product_clean}")
        return result

    # ==========================================================
    # BLOCK 9: SEARCH METHODS
    # ==========================================================
    
    def search_dn(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for DNs matching the query
        
        Supports exact match, partial match, and numeric search
        """
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        # If query is numeric, try exact match first
        if query_clean.isdigit():
            exact_query = """
                SELECT 
                    dn_no,
                    customer_name as dealer,
                    warehouse,
                    ship_to_city as city,
                    dn_qty as units,
                    dn_amount as revenue,
                    dn_create_date,
                    good_issue_date as pgi_date,
                    pod_date,
                    delivery_status
                FROM delivery_reports
                WHERE dn_no = :dn_no
                ORDER BY dn_create_date DESC
                LIMIT :limit
            """
            results = self._execute_query(exact_query, {"dn_no": query_clean, "limit": limit})
            if results:
                return self._format_search_results(results)
        
        # Generic search
        search_query = """
            SELECT 
                dn_no,
                customer_name as dealer,
                warehouse,
                ship_to_city as city,
                dn_qty as units,
                dn_amount as revenue,
                dn_create_date,
                good_issue_date as pgi_date,
                pod_date,
                delivery_status
            FROM delivery_reports
            WHERE dn_no LIKE :query
               OR customer_name LIKE :query
               OR warehouse LIKE :query
               OR ship_to_city LIKE :query
            ORDER BY dn_create_date DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def search_dealer(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for dealers matching the query"""
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        search_query = """
            SELECT 
                customer_name as dealer,
                dealer_code,
                customer_code,
                MAX(warehouse) as main_warehouse,
                MAX(ship_to_city) as main_city,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE LOWER(customer_name) LIKE LOWER(:query)
            GROUP BY customer_name, dealer_code, customer_code
            ORDER BY total_revenue DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def search_warehouse(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for warehouses matching the query"""
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        search_query = """
            SELECT 
                warehouse,
                warehouse_code,
                COUNT(DISTINCT customer_name) as dealers,
                COUNT(DISTINCT ship_to_city) as cities,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE LOWER(warehouse) LIKE LOWER(:query)
            GROUP BY warehouse, warehouse_code
            ORDER BY total_revenue DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def search_city(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for cities matching the query"""
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        search_query = """
            SELECT 
                ship_to_city as city,
                COUNT(DISTINCT customer_name) as dealers,
                COUNT(DISTINCT warehouse) as warehouses,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE LOWER(ship_to_city) LIKE LOWER(:query)
            GROUP BY ship_to_city
            ORDER BY total_revenue DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def search_product(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for products matching the query"""
        if not query:
            return []
        
        query_clean = str(query).strip()
        
        search_query = """
            SELECT 
                customer_model as product,
                material_no,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                COUNT(DISTINCT customer_name) as dealers,
                COUNT(DISTINCT ship_to_city) as cities,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate
            FROM delivery_reports
            WHERE LOWER(customer_model) LIKE LOWER(:query)
            GROUP BY customer_model, material_no
            ORDER BY total_revenue DESC
            LIMIT :limit
        """
        results = self._execute_query(search_query, {"query": f"%{query_clean}%", "limit": limit})
        return self._format_search_results(results)
    
    def _format_search_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format search results for consistent output"""
        formatted = []
        for r in results:
            # Format dates
            for date_field in ['dn_create_date', 'pgi_date', 'pod_date', 'created_at', 'updated_at']:
                if r.get(date_field):
                    if isinstance(r[date_field], (datetime, date)):
                        r[date_field] = r[date_field].strftime("%Y-%m-%d")
            
            # Format numeric values
            for num_field in ['units', 'revenue', 'dn_count', 'total_units', 'total_revenue', 'dn_qty', 'dn_amount']:
                if r.get(num_field):
                    if isinstance(r[num_field], (int, float)):
                        if 'revenue' in num_field or 'amount' in num_field:
                            r[num_field] = round(float(r[num_field]), 2)
                        else:
                            r[num_field] = int(r[num_field])
            
            formatted.append(r)
        return formatted

    # ==========================================================
    # BLOCK 10: VERIFICATION METHODS
    # ==========================================================
    # BLOCK 10: VERIFICATION METHODS - IMPROVED
    # ==========================================================
    
    def verify_dn_exists(self, dn_no: str) -> bool:
        """Verify if a DN exists in the system"""
        if not dn_no:
            return False
        
        dn_clean = re.sub(r'\D', '', str(dn_no).strip())
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return False
        
        # Try multiple strategies
        query = """
            SELECT COUNT(DISTINCT dn_no) as count 
            FROM delivery_reports 
            WHERE dn_no = :dn_no 
               OR CAST(dn_no AS VARCHAR) = :dn_no
               OR dn_no LIKE :pattern
        """
        results = self._execute_query(query, {
            "dn_no": dn_clean,
            "pattern": f"%{dn_clean}%"
        })
        return results and results[0].get('count', 0) > 0
    
    def verify_dealer_exists(self, dealer: str) -> bool:
        """Verify if a dealer exists in the system with improved TRIM matching"""
        if not dealer:
            return False
        
        dealer_clean = str(dealer).strip()
        
        query = """
            SELECT COUNT(DISTINCT customer_name) as count 
            FROM delivery_reports 
            WHERE TRIM(LOWER(customer_name)) LIKE TRIM(LOWER(:dealer))
        """
        results = self._execute_query(query, {"dealer": f"%{dealer_clean}%"})
        return results and results[0].get('count', 0) > 0
    
    def verify_warehouse_exists(self, warehouse: str) -> bool:
        """Verify if a warehouse exists in the system"""
        if not warehouse:
            return False
        
        query = """
            SELECT COUNT(DISTINCT warehouse) as count 
            FROM delivery_reports 
            WHERE TRIM(LOWER(warehouse)) LIKE TRIM(LOWER(:warehouse))
        """
        results = self._execute_query(query, {"warehouse": f"%{str(warehouse).strip()}%"})
        return results and results[0].get('count', 0) > 0
    
    def verify_city_exists(self, city: str) -> bool:
        """Verify if a city exists in the system"""
        if not city:
            return False
        
        query = """
            SELECT COUNT(DISTINCT ship_to_city) as count 
            FROM delivery_reports 
            WHERE TRIM(LOWER(ship_to_city)) LIKE TRIM(LOWER(:city))
        """
        results = self._execute_query(query, {"city": f"%{str(city).strip()}%"})
        return results and results[0].get('count', 0) > 0
    
    def verify_product_exists(self, product: str) -> bool:
        """Verify if a product exists in the system"""
        if not product:
            return False
        
        product_clean = str(product).strip()
        query = """
            SELECT COUNT(DISTINCT customer_model) as count 
            FROM delivery_reports 
            WHERE TRIM(LOWER(customer_model)) LIKE TRIM(LOWER(:product)) 
               OR TRIM(LOWER(material_no)) LIKE TRIM(LOWER(:product))
        """
        results = self._execute_query(query, {"product": f"%{product_clean}%"})
        return results and results[0].get('count', 0) > 0

    # ==========================================================
    # BLOCK 16: NEW METHOD - GET PENDING DELIVERIES
    # ==========================================================
    
    def get_pending_deliveries(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """
        Get all pending deliveries with complete information
        
        Pending DN if:
        - pod_date IS NULL
        - OR delivery_status = 'Pending'
        - OR pending_flag = True
        - OR pgi_status != 'Completed'
        - OR pod_status != 'Completed'
        
        Returns:
            List of pending DNs with complete metrics
            Total count of pending DNs
            Pagination information
        """
        try:
            logger.info(f"🔍 Retrieving pending deliveries (limit: {limit}, offset: {offset})")
            
            # First, get total count of pending DNs
            count_query = """
                SELECT COUNT(DISTINCT dn_no) as total_pending
                FROM delivery_reports
                WHERE pod_date IS NULL
                   OR delivery_status = 'Pending'
                   OR pending_flag = 'Y'
                   OR pgi_status != 'Completed'
                   OR pod_status != 'Completed'
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            if total_pending == 0:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "message": "No pending deliveries found"
                }
            
            # Get pending DNs with aggregation
            pending_query = """
                SELECT 
                    dn_no,
                    MAX(customer_name) as dealer,
                    MAX(warehouse) as warehouse,
                    MAX(ship_to_city) as city,
                    SUM(dn_qty) as units,
                    SUM(dn_amount) as revenue,
                    MIN(dn_create_date) as dn_create_date,
                    MAX(good_issue_date) as pgi_date,
                    MAX(pod_date) as pod_date,
                    MAX(delivery_status) as delivery_status,
                    MAX(pgi_status) as pgi_status,
                    MAX(pod_status) as pod_status,
                    MAX(pending_flag) as pending_flag,
                    COUNT(*) as material_count,
                    MAX(division) as division,
                    MAX(customer_code) as customer_code,
                    MAX(dealer_code) as dealer_code,
                    MAX(sales_office) as sales_office,
                    MAX(sales_manager) as sales_manager,
                    MAX(dn_work) as dn_work,
                    MAX(order_type) as order_type
                FROM delivery_reports
                WHERE pod_date IS NULL
                   OR delivery_status = 'Pending'
                   OR pending_flag = 'Y'
                   OR pgi_status != 'Completed'
                   OR pod_status != 'Completed'
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            pending_results = self._execute_query(
                pending_query, 
                {"limit": limit, "offset": offset}
            )
            
            # Format results with aging calculations
            formatted_results = []
            for row in pending_results:
                # Calculate aging
                dn_create = row.get('dn_create_date')
                pgi_date = row.get('pgi_date')
                pod_date = row.get('pod_date')
                
                # Calculate delivery aging (from create date)
                delivery_aging = 0
                if dn_create:
                    if isinstance(dn_create, (datetime, date)):
                        delivery_aging = (datetime.now().date() - dn_create.date()).days
                    elif isinstance(dn_create, str):
                        try:
                            dn_date = datetime.fromisoformat(dn_create.replace('Z', '+00:00'))
                            delivery_aging = (datetime.now().date() - dn_date.date()).days
                        except:
                            pass
                
                # Calculate POD aging (from PGI date)
                pod_aging = 0
                if pgi_date:
                    if isinstance(pgi_date, (datetime, date)):
                        pod_aging = (datetime.now().date() - pgi_date.date()).days
                    elif isinstance(pgi_date, str):
                        try:
                            pgi_date_obj = datetime.fromisoformat(pgi_date.replace('Z', '+00:00'))
                            pod_aging = (datetime.now().date() - pgi_date_obj.date()).days
                        except:
                            pass
                
                # Calculate total cycle (from create to POD, if available)
                total_cycle = None
                if dn_create and pod_date:
                    try:
                        if isinstance(dn_create, (datetime, date)) and isinstance(pod_date, (datetime, date)):
                            total_cycle = (pod_date - dn_create).days
                        elif isinstance(dn_create, str) and isinstance(pod_date, str):
                            dn_date = datetime.fromisoformat(dn_create.replace('Z', '+00:00'))
                            pod_date_obj = datetime.fromisoformat(pod_date.replace('Z', '+00:00'))
                            total_cycle = (pod_date_obj - dn_date).days
                    except:
                        pass
                
                # Format dates for output
                for date_field in ['dn_create_date', 'pgi_date', 'pod_date']:
                    if row.get(date_field):
                        if isinstance(row[date_field], (datetime, date)):
                            row[date_field] = row[date_field].strftime("%Y-%m-%d %H:%M:%S")
                
                formatted_row = {
                    "dn_no": row.get('dn_no'),
                    "dealer": row.get('dealer') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "units": int(row.get('units') or 0),
                    "revenue": float(row.get('revenue') or 0),
                    "dn_create_date": row.get('dn_create_date'),
                    "pgi_date": row.get('pgi_date'),
                    "pod_date": row.get('pod_date'),
                    "delivery_status": row.get('delivery_status') or "Pending",
                    "pgi_status": row.get('pgi_status') or "Unknown",
                    "pod_status": row.get('pod_status') or "Unknown",
                    "pending_flag": row.get('pending_flag') or "N",
                    "delivery_aging_days": delivery_aging,
                    "pod_aging_days": pod_aging,
                    "total_cycle_days": total_cycle,
                    "material_count": row.get('material_count', 1),
                    "division": row.get('division'),
                    "customer_code": row.get('customer_code'),
                    "dealer_code": row.get('dealer_code'),
                    "sales_office": row.get('sales_office'),
                    "sales_manager": row.get('sales_manager'),
                    "dn_work": row.get('dn_work'),
                    "order_type": row.get('order_type'),
                    # Determine reason for pending
                    "pending_reason": self._determine_pending_reason(row)
                }
                
                # Add aging text
                formatted_row['delivery_aging_text'] = self._format_aging_text(delivery_aging)
                formatted_row['pod_aging_text'] = self._format_aging_text(pod_aging)
                if total_cycle is not None:
                    formatted_row['total_cycle_text'] = self._format_aging_text(total_cycle)
                
                # Add urgency indicator
                if delivery_aging > 30:
                    formatted_row['urgency'] = '🔴 CRITICAL'
                elif delivery_aging > 14:
                    formatted_row['urgency'] = '🟡 HIGH'
                elif delivery_aging > 7:
                    formatted_row['urgency'] = '🟠 MEDIUM'
                else:
                    formatted_row['urgency'] = '🟢 NORMAL'
                
                formatted_results.append(formatted_row)
            
            return {
                "success": True,
                "data": formatted_results,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted_results),
                "has_more": (offset + limit) < total_pending,
                "next_offset": offset + limit if (offset + limit) < total_pending else None,
                "summary": {
                    "total_pending": total_pending,
                    "critical_count": len([r for r in formatted_results if r.get('urgency') == '🔴 CRITICAL']),
                    "high_count": len([r for r in formatted_results if r.get('urgency') == '🟡 HIGH']),
                    "medium_count": len([r for r in formatted_results if r.get('urgency') == '🟠 MEDIUM']),
                    "normal_count": len([r for r in formatted_results if r.get('urgency') == '🟢 NORMAL'])
                }
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending deliveries: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_type": "PENDING_DELIVERIES_ERROR",
                "timestamp": datetime.now().isoformat()
            }
    
    def _determine_pending_reason(self, row: Dict[str, Any]) -> str:
        """Determine why a DN is pending"""
        reasons = []
        
        if row.get('pod_date') is None:
            reasons.append("POD not received")
        
        if row.get('delivery_status') == 'Pending':
            reasons.append("Delivery status is Pending")
        
        if row.get('pending_flag') == 'Y':
            reasons.append("Pending flag is set")
        
        if row.get('pgi_status') != 'Completed' and row.get('pgi_status') is not None:
            reasons.append(f"PGI status is {row.get('pgi_status')}")
        
        if row.get('pod_status') != 'Completed' and row.get('pod_status') is not None:
            reasons.append(f"POD status is {row.get('pod_status')}")
        
        return ", ".join(reasons) if reasons else "Unknown reason"

    # ==========================================================
    # BLOCK 11: RANKING ENGINE
    # ==========================================================
    
    def get_top_dealers(self, limit: int = 10, metric: str = 'revenue') -> List[Dict[str, Any]]:
        """Get top dealers by revenue or units"""
        if metric == 'revenue':
            order_by = "total_revenue DESC"
        else:
            order_by = "total_units DESC"
        
        query = f"""
            SELECT 
                customer_name as dealer,
                dealer_code,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate,
                AVG(EXTRACT(DAY FROM COALESCE(CURRENT_DATE, dn_create_date) - dn_create_date)) as avg_aging_days
            FROM delivery_reports
            GROUP BY customer_name, dealer_code
            ORDER BY {order_by}
            LIMIT :limit
        """
        return self._execute_query(query, {"limit": limit})
    
    def get_top_warehouses(self, limit: int = 10, metric: str = 'revenue') -> List[Dict[str, Any]]:
        """Get top warehouses by revenue or units"""
        if metric == 'revenue':
            order_by = "total_revenue DESC"
        else:
            order_by = "total_units DESC"
        
        query = f"""
            SELECT 
                warehouse,
                warehouse_code,
                COUNT(DISTINCT customer_name) as dealers,
                COUNT(DISTINCT ship_to_city) as cities,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate
            FROM delivery_reports
            GROUP BY warehouse, warehouse_code
            ORDER BY {order_by}
            LIMIT :limit
        """
        return self._execute_query(query, {"limit": limit})
    
    def get_top_cities(self, limit: int = 10, metric: str = 'revenue') -> List[Dict[str, Any]]:
        """Get top cities by revenue or units"""
        if metric == 'revenue':
            order_by = "total_revenue DESC"
        else:
            order_by = "total_units DESC"
        
        query = f"""
            SELECT 
                ship_to_city as city,
                COUNT(DISTINCT customer_name) as dealers,
                COUNT(DISTINCT warehouse) as warehouses,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate
            FROM delivery_reports
            GROUP BY ship_to_city
            ORDER BY {order_by}
            LIMIT :limit
        """
        return self._execute_query(query, {"limit": limit})
    
    def get_top_products(self, limit: int = 10, metric: str = 'revenue') -> List[Dict[str, Any]]:
        """Get top products by revenue or units"""
        if metric == 'revenue':
            order_by = "total_revenue DESC"
        else:
            order_by = "total_units DESC"
        
        query = f"""
            SELECT 
                customer_model as product,
                material_no,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_amount) as total_revenue,
                SUM(dn_qty) as total_units,
                COUNT(DISTINCT customer_name) as dealers,
                COUNT(DISTINCT ship_to_city) as cities,
                ROUND(COUNT(CASE WHEN delivery_status IN ('Completed', 'Delivered', 'Closed') THEN 1 END) * 100.0 / COUNT(dn_no), 2) as delivery_rate
            FROM delivery_reports
            GROUP BY customer_model, material_no
            ORDER BY {order_by}
            LIMIT :limit
        """
        return self._execute_query(query, {"limit": limit})

    # ==========================================================
    # BLOCK 12: KPI ENGINE
    # ==========================================================
    
    def _calculate_health_score(self, data: Dict[str, Any]) -> float:
        """
        Calculate health score (0-100) based on multiple metrics
        
        Higher score = healthier operation
        """
        score = 0.0
        
        # Delivery rate (30 points max)
        delivery_rate = data.get('delivery_rate', 0) or 0
        score += min(delivery_rate * 0.3, 30)
        
        # PGI rate (20 points max)
        pgi_rate = data.get('pgi_rate', 0) or 0
        score += min(pgi_rate * 0.2, 20)
        
        # POD rate (20 points max)
        pod_rate = data.get('pod_rate', 0) or 0
        score += min(pod_rate * 0.2, 20)
        
        # Aging penalty (15 points max)
        avg_aging = data.get('avg_aging_days', 0) or 0
        if avg_aging <= 7:
            score += 15
        elif avg_aging <= 14:
            score += 10
        elif avg_aging <= 30:
            score += 5
        else:
            score += 0
        
        # Pending penalty (15 points max)
        pending_rate = data.get('pending_rate', 0) or 0
        score += max(0, 15 - pending_rate * 0.15)
        
        # Cap at 100
        return min(round(score, 2), 100)
    
    def _calculate_risk_score(self, data: Dict[str, Any]) -> float:
        """
        Calculate risk score (0-100)
        
        Higher score = higher risk
        """
        score = 0.0
        
        # Pending rate (35 points max)
        pending_rate = data.get('pending_rate', 0) or 0
        score += min(pending_rate * 0.35, 35)
        
        # Aging (25 points max)
        avg_aging = data.get('avg_aging_days', 0) or 0
        if avg_aging > 30:
            score += 25
        elif avg_aging > 14:
            score += 15
        elif avg_aging > 7:
            score += 8
        else:
            score += 0
        
        # Delivery rate penalty (20 points max)
        delivery_rate = data.get('delivery_rate', 0) or 0
        score += max(0, 20 - delivery_rate * 0.2)
        
        # Transit rate (20 points max)
        transit_rate = data.get('transit_rate', 0) or 0
        score += min(transit_rate * 0.2, 20)
        
        # Cap at 100
        return min(round(score, 2), 100)
    
    def _calculate_performance_score(self, data: Dict[str, Any]) -> float:
        """
        Calculate performance score (0-100)
        
        Higher score = better performance
        """
        score = 0.0
        
        # Delivery rate (30 points)
        delivery_rate = data.get('delivery_rate', 0) or 0
        score += delivery_rate * 0.3
        
        # Revenue per DN (25 points)
        avg_revenue = data.get('avg_revenue_per_dn', 0) or 0
        if avg_revenue > 100000:
            score += 25
        elif avg_revenue > 50000:
            score += 18
        elif avg_revenue > 25000:
            score += 10
        else:
            score += 5
        
        # Volume (25 points)
        total_dns = data.get('total_dns', 0) or 0
        if total_dns > 100:
            score += 25
        elif total_dns > 50:
            score += 18
        elif total_dns > 20:
            score += 10
        else:
            score += 5
        
        # Aging (20 points)
        avg_aging = data.get('avg_aging_days', 0) or 0
        if avg_aging <= 7:
            score += 20
        elif avg_aging <= 14:
            score += 14
        elif avg_aging <= 30:
            score += 7
        else:
            score += 0
        
        # Cap at 100
        return min(round(score, 2), 100)
    
    def _get_health_score_text(self, score: float) -> str:
        """Get health score text description"""
        if score >= 80:
            return "🌟 Excellent"
        elif score >= 60:
            return "✅ Good"
        elif score >= 40:
            return "⚠️ Fair"
        else:
            return "🔴 Needs Attention"
    
    def _get_risk_level(self, score: float) -> str:
        """Get risk level description"""
        if score >= 60:
            return "🔴 High Risk"
        elif score >= 30:
            return "🟡 Medium Risk"
        else:
            return "🟢 Low Risk"
    
    def _get_performance_level(self, score: float) -> str:
        """Get performance level description"""
        if score >= 80:
            return "🌟 Top Performer"
        elif score >= 60:
            return "✅ Good Performer"
        elif score >= 40:
            return "📈 Developing"
        else:
            return "📉 Needs Improvement"

    # ==========================================================
    # BLOCK 13: AGING ENGINE
    # ==========================================================
    
    def _format_aging_text(self, days: int) -> str:
        """Format aging days into human readable text"""
        if days <= 0:
            return "Same day"
        elif days < 0:
            return f"{abs(days)} days (future)"
        
        if days < 1:
            return "Today"
        elif days == 1:
            return "1 day"
        elif days < 7:
            return f"{days} days"
        elif days < 14:
            return f"{days} days (1-2 weeks)"
        elif days < 30:
            return f"{days} days ({days // 7} weeks)"
        elif days < 60:
            return f"{days} days (1-2 months)"
        elif days < 90:
            return f"{days} days (3 months)"
        else:
            return f"{days} days ({days // 30} months)"

    # ==========================================================
    # BLOCK 14: TREND ENGINE
    # ==========================================================
    
    def get_daily_trend(self, entity: str, entity_type: str, days: int = 30) -> List[Dict[str, Any]]:
        """Get daily trend for any entity"""
        if entity_type == 'dealer':
            filter_clause = f"LOWER(customer_name) LIKE LOWER('{entity}')"
        elif entity_type == 'warehouse':
            filter_clause = f"LOWER(warehouse) LIKE LOWER('{entity}')"
        elif entity_type == 'city':
            filter_clause = f"LOWER(ship_to_city) LIKE LOWER('{entity}')"
        elif entity_type == 'product':
            filter_clause = f"LOWER(customer_model) LIKE LOWER('{entity}')"
        else:
            return []
        
        query = f"""
            SELECT 
                DATE_TRUNC('day', dn_create_date) as date,
                COUNT(dn_no) as dn_count,
                SUM(dn_amount) as revenue,
                SUM(dn_qty) as units
            FROM delivery_reports
            WHERE {filter_clause}
              AND dn_create_date >= CURRENT_DATE - INTERVAL '{days} days'
            GROUP BY DATE_TRUNC('day', dn_create_date)
            ORDER BY date ASC
        """
        results = self._execute_query(query)
        
        for r in results:
            if r.get('date'):
                if isinstance(r['date'], (datetime, date)):
                    r['date'] = r['date'].strftime("%Y-%m-%d")
        
        return results

    # ==========================================================
    # BLOCK 15: HEALTH FRAMEWORK
    # ==========================================================
    
    def validate_postgresql_connection(self) -> bool:
        """Validate PostgreSQL connection is healthy"""
        try:
            session = self._get_session()
            if not session:
                return False
            result = session.execute(text("SELECT 1")).scalar()
            session.close()
            self._health_status["database_connected"] = result == 1
            self._health_status["last_check"] = datetime.now().isoformat()
            return result == 1
        except Exception as e:
            logger.error(f"❌ PostgreSQL connection validation failed: {e}")
            self._health_status["database_connected"] = False
            self._health_status["errors"].append(str(e))
            return False
    
    def validate_table_structure(self) -> Dict[str, Any]:
        """Validate table structure and required columns"""
        result = {
            "valid": False,
            "columns": {},
            "missing_columns": [],
            "errors": []
        }
        
        required_columns = [
            "dn_no", "customer_name", "warehouse", "ship_to_city",
            "dn_qty", "dn_amount", "dn_create_date", "delivery_status"
        ]
        
        try:
            query = """
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'delivery_reports'
            """
            columns = self._execute_query(query)
            
            if not columns:
                result["errors"].append("Table 'delivery_reports' not found")
                return result
            
            existing_columns = [c.get('column_name') for c in columns if c.get('column_name')]
            result["columns"] = {c.get('column_name'): c.get('data_type') for c in columns}
            
            missing = [col for col in required_columns if col not in existing_columns]
            result["missing_columns"] = missing
            
            result["valid"] = len(missing) == 0
            if not result["valid"]:
                result["errors"].append(f"Missing columns: {missing}")
            
        except Exception as e:
            result["errors"].append(f"Table validation failed: {str(e)}")
        
        return result
    
    def get_database_health(self) -> Dict[str, Any]:
        """Get comprehensive database health report"""
        result = {
            "status": "unknown",
            "connected": False,
            "record_count": 0,
            "dn_count": 0,
            "dealer_count": 0,
            "warehouse_count": 0,
            "city_count": 0,
            "product_count": 0,
            "last_check": datetime.now().isoformat(),
            "errors": [],
            "warnings": []
        }
        
        try:
            # Check connection
            connected = self.validate_postgresql_connection()
            result["connected"] = connected
            
            if not connected:
                result["status"] = "critical"
                result["errors"].append("Database connection failed")
                return result
            
            # Get counts
            count_query = "SELECT COUNT(*) as total FROM delivery_reports"
            count_result = self._execute_query(count_query)
            result["record_count"] = count_result[0].get('total', 0) if count_result else 0
            
            # Get distinct counts
            distinct_query = """
                SELECT 
                    COUNT(DISTINCT dn_no) as dns,
                    COUNT(DISTINCT customer_name) as dealers,
                    COUNT(DISTINCT warehouse) as warehouses,
                    COUNT(DISTINCT ship_to_city) as cities,
                    COUNT(DISTINCT customer_model) as products
                FROM delivery_reports
            """
            distinct_result = self._execute_query(distinct_query)
            if distinct_result:
                d = distinct_result[0]
                result["dn_count"] = d.get('dns', 0) or 0
                result["dealer_count"] = d.get('dealers', 0) or 0
                result["warehouse_count"] = d.get('warehouses', 0) or 0
                result["city_count"] = d.get('cities', 0) or 0
                result["product_count"] = d.get('products', 0) or 0
            
            # Determine status
            if result["record_count"] == 0:
                result["status"] = "critical"
                result["errors"].append("No records found in database")
            elif result["dn_count"] == 0:
                result["status"] = "critical"
                result["errors"].append("No DNs found in database")
            elif result["dealer_count"] == 0:
                result["status"] = "warning"
                result["warnings"].append("No dealers found in database")
            elif result["warehouse_count"] == 0:
                result["status"] = "warning"
                result["warnings"].append("No warehouses found in database")
            else:
                result["status"] = "healthy"
            
        except Exception as e:
            result["status"] = "critical"
            result["errors"].append(f"Health check failed: {str(e)}")
        
        self._health_status.update(result)
        return result
    
    def get_health_report(self) -> Dict[str, Any]:
        """Get comprehensive health report of the analytics service"""
        try:
            # Check database health
            db_health = self.get_database_health()
            
            # Check table structure
            table_structure = self.validate_table_structure()
            
            # Check service status
            service_status = {
                "initialized": self._health_status.get("initialized", False),
                "resolver_available": self._resolver is not None,
                "distance_available": self._distance_service is not None,
                "dealer_analytics_available": self._dealer_analytics is not None
            }
            
            # Check all methods are available
            required_methods = [
                'get_dn_dashboard', 'get_dealer_dashboard', 'get_warehouse_dashboard',
                'get_city_dashboard', 'get_product_dashboard',
                'search_dn', 'search_dealer', 'search_warehouse', 'search_city', 'search_product',
                'verify_dn_exists', 'verify_dealer_exists', 'verify_warehouse_exists',
                'verify_city_exists', 'verify_product_exists',
                'get_health_report', 'validate_postgresql_connection', 'is_service_healthy'
            ]
            
            methods_available = {}
            missing_methods = []
            for method in required_methods:
                if hasattr(self, method):
                    methods_available[method] = True
                else:
                    methods_available[method] = False
                    missing_methods.append(method)
            
            return {
                "service_name": "AnalyticsService",
                "version": "31.1",
                "healthy": db_health["status"] == "healthy" and len(missing_methods) == 0,
                "status": "healthy" if db_health["status"] == "healthy" and len(missing_methods) == 0 else db_health["status"],
                "database": db_health,
                "table_structure": table_structure,
                "service_status": service_status,
                "methods_available": methods_available,
                "missing_methods": missing_methods,
                "all_methods_present": len(missing_methods) == 0,
                "cache_stats": {
                    "dn_cache": len(self._dn_cache),
                    "dealer_cache": len(self._dealer_cache),
                    "warehouse_cache": len(self._warehouse_cache),
                    "city_cache": len(self._city_cache),
                    "product_cache": len(self._product_cache)
                },
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"❌ Health report generation failed: {e}")
            return {
                "service_name": "AnalyticsService",
                "healthy": False,
                "status": "critical",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def is_service_healthy(self) -> bool:
        """Check if the service is healthy"""
        try:
            report = self.get_health_report()
            return report.get("healthy", False)
        except Exception as e:
            logger.error(f"❌ Service health check failed: {e}")
            return False

    # ==========================================================
    # BLOCK 16: ERROR RESPONSE
    # ==========================================================
    
    def _error_response(self, message: str, error_type: str = "ERROR", context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Create a standardized error response"""
        error_id = str(uuid.uuid4())[:8]
        
        response = {
            "success": False,
            "error": message,
            "error_type": error_type,
            "error_id": error_id,
            "timestamp": datetime.now().isoformat(),
            "suggested_action": "Please verify the input and try again."
        }
        
        if context:
            response["context"] = context
        
        logger.warning(f"⚠️ Error response: {error_type} - {message} (ID: {error_id})")
        return response

    # ==========================================================
    # BLOCK 17: SERVICE STATUS
    # ==========================================================
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get current service status"""
        return {
            "service": "AnalyticsService",
            "version": "31.1",
            "initialized": self._health_status.get("initialized", False),
            "database_connected": self._health_status.get("database_connected", False),
            "status": self._health_status.get("status", "unknown"),
            "resolver_available": self._resolver is not None,
            "distance_available": self._distance_service is not None,
            "dealer_analytics_available": self._dealer_analytics is not None,
            "cache_sizes": {
                "dn": len(self._dn_cache),
                "dealer": len(self._dealer_cache),
                "warehouse": len(self._warehouse_cache),
                "city": len(self._city_cache),
                "product": len(self._product_cache)
            },
            "timestamp": datetime.now().isoformat()
        }


# ==========================================================
# BLOCK 18: FACTORY FUNCTION
# ==========================================================

_analytics_service_instance = None
_analytics_service_lock = threading.Lock()

def get_analytics_service() -> AnalyticsService:
    """
    Get the AnalyticsService instance (singleton pattern)
    
    Returns:
        AnalyticsService instance
        
    Raises:
        Exception: If service initialization fails
    """
    global _analytics_service_instance
    
    if _analytics_service_instance is None:
        with _analytics_service_lock:
            if _analytics_service_instance is None:
                try:
                    logger.info("=" * 70)
                    logger.info("🔍 Initializing AnalyticsService (singleton)...")
                    _analytics_service_instance = AnalyticsService()
                    
                    # Validate service health
                    if not _analytics_service_instance.is_service_healthy():
                        logger.warning("⚠️ AnalyticsService health check returned False")
                        # Still return the instance - it may still work
                    
                    logger.info("=" * 70)
                    logger.info("✅ AnalyticsService initialized successfully")
                    logger.info("=" * 70)
                    
                except Exception as e:
                    logger.error(f"❌ Failed to initialize AnalyticsService: {e}")
                    raise Exception(f"Analytics Service initialization failed: {e}")
    
    return _analytics_service_instance


def reset_analytics_service():
    """Reset the AnalyticsService singleton (for testing)"""
    global _analytics_service_instance
    with _analytics_service_lock:
        _analytics_service_instance = None
        logger.info("🔄 AnalyticsService singleton reset")


# ==========================================================
# BLOCK 19: EXPORTS
# ==========================================================

__all__ = [
    'AnalyticsService',
    'get_analytics_service',
    'reset_analytics_service',
    'AnalyticsResponse',
]

# ==========================================================
# END OF FILE - v31.1 DN RESOLUTION HOTFIX
# ==========================================================
