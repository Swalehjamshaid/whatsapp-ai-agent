# ==========================================================
# FILE: app/services/dn_analysis.py (v12.1 - DN SEARCH FIX)
# ==========================================================
# PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
# SOURCE: delivery_reports table ONLY
# VERSION: 12.1 - DN SEARCH FIX
#
# ENTERPRISE FEATURES:
# - ✅ Robust DN search with 5 fallback strategies
# - ✅ Index-friendly SQL with wildcard support
# - ✅ Flexible length validation
# - ✅ Partial match and fuzzy search
# - ✅ 100% backward compatible
# ==========================================================

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date, timedelta
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
import threading
import re
import traceback
import time
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS & DATABASE SETUP
# ==========================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Database models imported successfully")
except ImportError as e:
    logger.error(f"❌ Database import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

# Debug mode - enable with environment variable
DEBUG_MODE = os.environ.get("DN_DEBUG_MODE", "false").lower() == "true"
PRODUCTION_MODE = os.environ.get("DN_PRODUCTION_MODE", "true").lower() == "true"

# Distance libraries (lazy loaded)
GEO_AVAILABLE = False
OPENROUTE_API_KEY = os.environ.get("OPENROUTE_API_KEY", "")

def _lazy_load_geo():
    """Lazy load GIS libraries only when needed."""
    global GEO_AVAILABLE
    if not GEO_AVAILABLE:
        try:
            import openrouteservice
            from geopy.geocoders import Nominatim
            from geopy.distance import geodesic
            GEO_AVAILABLE = True
            if not PRODUCTION_MODE:
                logger.info("✅ GIS libraries loaded")
            return True
        except ImportError:
            if not PRODUCTION_MODE:
                logger.warning("⚠️ GIS libraries not available. Distance features will use estimation.")
            return False
    return True

# ==========================================================
# BLOCK 2: DNAnalysisService CLASS
# ==========================================================

class DNAnalysisService:
    """
    DN Analytics Service - Direct PostgreSQL Connection.
    
    OPTIMIZED v12.1:
    - ✅ Robust DN search with multiple fallback strategies
    - ✅ Index-friendly SQL with wildcard support
    - ✅ Flexible length validation (5+ digits)
    - ✅ Partial match and fuzzy search
    - ✅ 100% backward compatible
    """
    
    def __init__(self):
        """Initialize DN Analytics Service."""
        self._service_name = "dn_analysis"
        self._version = "12.1"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0
        self._startup_time = datetime.now().isoformat()
        self._debug_mode = DEBUG_MODE
        self._production_mode = PRODUCTION_MODE
        
        # Lazy-loaded services
        self._distance_calculator = None
        self._schema_cache = None
        self._geo_loaded = False
        
        if not self._production_mode:
            logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
            logger.info(f"📋 Debug Mode: {'ENABLED' if self._debug_mode else 'DISABLED'}")
        
        # Test connection
        test_result = self._test_connection()
        if test_result:
            self._status = "READY"
            if not self._production_mode:
                logger.info("✅ DNAnalysisService is READY")
        else:
            self._status = "ERROR"
            logger.error("❌ DNAnalysisService initialization FAILED")
    
    # ==========================================================
    # BLOCK 3: DATABASE CONNECTION METHODS
    # ==========================================================
    
    def _test_connection(self) -> bool:
        """Test database connection - lightweight."""
        session = None
        try:
            if not SessionLocal:
                logger.error("❌ SessionLocal is None")
                return False
            
            session = SessionLocal()
            session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"❌ Database connection test FAILED: {e}")
            return False
        finally:
            if session:
                session.close()
    
    def _get_session(self) -> Optional[Session]:
        """Get database session."""
        if not SessionLocal:
            logger.error("❌ SessionLocal not available")
            return None
        
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"❌ Failed to get database session: {e}")
            return None
    
    def _execute_query(self, query: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Execute raw SQL query and return results as dicts."""
        start_time = time.time()
        session = None
        try:
            session = self._get_session()
            if not session:
                logger.error("❌ No session available")
                return []
            
            if self._debug_mode:
                logger.debug(f"📝 Executing SQL: {query[:200]}...")
                if params:
                    logger.debug(f"📝 Parameters: {params}")
            
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            
            execution_time_ms = (time.time() - start_time) * 1000
            self._query_count += 1
            self._total_execution_time_ms += execution_time_ms
            
            if self._debug_mode:
                logger.debug(f"✅ Query returned {len(rows)} rows in {execution_time_ms:.2f}ms")
            return rows
            
        except Exception as e:
            logger.error(f"❌ SQL Execution Failed!")
            logger.error(f"   Query: {query[:500]}")
            if params:
                logger.error(f"   Parameters: {params}")
            logger.error(f"   Error: {str(e)}")
            if self._debug_mode:
                logger.error(f"   Traceback:\n{traceback.format_exc()}")
            return []
        finally:
            if session:
                session.close()
    
    # ==========================================================
    # BLOCK 4: DN SEARCH QUERY BUILDERS (OPTIMIZED)
    # ==========================================================
    
    def _normalize_dn(self, dn_no: str) -> str:
        """Normalize DN number for search - removes non-numeric characters."""
        if not dn_no:
            return ""
        normalized = re.sub(r'[^0-9]', '', dn_no.strip())
        if self._debug_mode:
            logger.debug(f"🔍 DN Normalization: '{dn_no}' → '{normalized}'")
        return normalized
    
    def _build_primary_dn_query(self) -> str:
        """
        PRIMARY DN query - index-friendly with multiple search patterns.
        
        This query tries multiple search strategies in one CTE:
        1. Exact match (fastest, uses index)
        2. Exact match without separators
        3. Pattern match with wildcards
        """
        return """
            WITH dn_data AS (
                SELECT 
                    dn_no,
                    customer_name,
                    dealer_code,
                    customer_code,
                    warehouse,
                    warehouse_code,
                    ship_to_city,
                    delivery_location,
                    sales_manager,
                    sales_office,
                    division,
                    dn_qty,
                    dn_amount,
                    dn_create_date,
                    good_issue_date,
                    pod_date,
                    delivery_status,
                    pgi_status,
                    pod_status,
                    pending_flag,
                    customer_model,
                    material_no,
                    source_file,
                    upload_batch_id,
                    created_at,
                    updated_at,
                    imported_at
                FROM delivery_reports
                WHERE 
                    -- Strategy 1: Exact match (indexed, fastest)
                    dn_no = :dn_no
                    -- Strategy 2: Match without separators
                    OR REPLACE(dn_no, '-', '') = :dn_no
                    OR REPLACE(dn_no, '/', '') = :dn_no
                    OR REPLACE(dn_no, ' ', '') = :dn_no
                    -- Strategy 3: Wildcard match (for partial matches)
                    OR dn_no LIKE CONCAT('%', :dn_no, '%')
                    -- Strategy 4: Match last 6 digits (for similar DNs)
                    OR RIGHT(dn_no, 6) = RIGHT(:dn_no, 6)
            )
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(customer_name) AS customer_name,
                MAX(dealer_code) AS dealer_code,
                MAX(customer_code) AS customer_code,
                MAX(warehouse) AS warehouse,
                MAX(warehouse_code) AS warehouse_code,
                MAX(ship_to_city) AS city,
                MAX(delivery_location) AS delivery_location,
                MAX(sales_manager) AS sales_manager,
                MAX(sales_office) AS sales_office,
                MAX(division) AS division,
                COALESCE(SUM(dn_qty), 0) AS total_units,
                COALESCE(SUM(dn_amount), 0) AS total_revenue,
                COUNT(DISTINCT customer_model) AS model_count,
                COUNT(DISTINCT material_no) AS material_count_distinct,
                COUNT(*) AS material_count,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                MAX(delivery_status) AS delivery_status,
                MAX(pgi_status) AS pgi_status,
                MAX(pod_status) AS pod_status,
                MAX(pending_flag) AS pending_flag,
                MAX(source_file) AS source_file,
                MAX(upload_batch_id) AS upload_batch_id,
                MIN(created_at) AS created_at,
                MAX(updated_at) AS updated_at,
                MAX(imported_at) AS imported_at
            FROM dn_data
            GROUP BY dn_no
            LIMIT 1
        """
    
    def _build_fallback_dn_query(self) -> str:
        """
        FALLBACK DN query - tries multiple patterns to find similar DNs.
        """
        return """
            WITH matching_dns AS (
                SELECT DISTINCT dn_no
                FROM delivery_reports
                WHERE 
                    dn_no LIKE :pattern1
                    OR dn_no LIKE :pattern2
                    OR dn_no LIKE :pattern3
                    OR RIGHT(dn_no, 6) = RIGHT(:dn_no, 6)
                LIMIT 20
            )
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(customer_name) AS customer_name,
                MAX(dealer_code) AS dealer_code,
                MAX(customer_code) AS customer_code,
                MAX(warehouse) AS warehouse,
                MAX(warehouse_code) AS warehouse_code,
                MAX(ship_to_city) AS city,
                MAX(delivery_location) AS delivery_location,
                MAX(sales_manager) AS sales_manager,
                MAX(sales_office) AS sales_office,
                MAX(division) AS division,
                COALESCE(SUM(dn_qty), 0) AS total_units,
                COALESCE(SUM(dn_amount), 0) AS total_revenue,
                COUNT(DISTINCT customer_model) AS model_count,
                COUNT(DISTINCT material_no) AS material_count_distinct,
                COUNT(*) AS material_count,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                MAX(delivery_status) AS delivery_status,
                MAX(pgi_status) AS pgi_status,
                MAX(pod_status) AS pod_status,
                MAX(pending_flag) AS pending_flag,
                MAX(source_file) AS source_file,
                MAX(upload_batch_id) AS upload_batch_id,
                MIN(created_at) AS created_at,
                MAX(updated_at) AS updated_at,
                MAX(imported_at) AS imported_at
            FROM delivery_reports
            WHERE dn_no IN (SELECT dn_no FROM matching_dns)
            GROUP BY dn_no
            ORDER BY 
                -- Prioritize exact matches
                CASE WHEN dn_no = :dn_no THEN 1 ELSE 0 END DESC,
                -- Then matches without separators
                CASE WHEN REPLACE(dn_no, '-', '') = :dn_no THEN 1 ELSE 0 END DESC,
                -- Then matches by suffix
                CASE WHEN RIGHT(dn_no, 6) = RIGHT(:dn_no, 6) THEN 1 ELSE 0 END DESC
            LIMIT 10
        """
    
    def _build_products_query(self) -> str:
        """Build products query."""
        return """
            WITH product_data AS (
                SELECT 
                    customer_model,
                    material_no,
                    division,
                    SUM(dn_qty) AS quantity,
                    SUM(dn_amount) AS revenue
                FROM delivery_reports
                WHERE 
                    dn_no = :dn_no
                    OR REPLACE(dn_no, '-', '') = :dn_no
                    OR REPLACE(dn_no, '/', '') = :dn_no
                    OR REPLACE(dn_no, ' ', '') = :dn_no
                    OR dn_no LIKE CONCAT('%', :dn_no, '%')
                GROUP BY customer_model, material_no, division
            )
            SELECT 
                customer_model AS model_name,
                material_no AS material_number,
                division,
                quantity,
                revenue
            FROM product_data
            WHERE customer_model IS NOT NULL
            ORDER BY quantity DESC
            LIMIT 20
        """
    
    # ==========================================================
    # BLOCK 5: HEALTH & VALIDATION METHODS
    # ==========================================================
    
    @lru_cache(maxsize=1)
    def _get_cached_schema(self) -> Dict[str, Any]:
        """Cache schema information to avoid repeated inspection."""
        session = None
        try:
            session = self._get_session()
            if not session:
                return {"error": "No session available"}
            
            inspector = inspect(session.bind)
            tables = inspector.get_table_names()
            columns = {}
            
            for table in tables:
                columns[table] = [col["name"] for col in inspector.get_columns(table)]
            
            return {"tables": tables, "columns": columns}
        except Exception as e:
            logger.error(f"❌ Schema cache failed: {e}")
            return {"error": str(e)}
        finally:
            if session:
                session.close()
    
    def health_check(self) -> Dict[str, Any]:
        """Validate service readiness."""
        result = {
            "healthy": False,
            "service": self._service_name,
            "version": self._version,
            "database": "disconnected",
            "errors": [],
            "warnings": [],
            "timestamp": datetime.now().isoformat(),
            "query_count": self._query_count,
            "total_execution_time_ms": self._total_execution_time_ms
        }
        
        try:
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                logger.error("❌ Health check failed: SessionLocal not available")
                return result
            
            # Test connection
            session = SessionLocal()
            try:
                session.execute(text("SELECT 1"))
                result["database"] = "connected"
            except Exception as e:
                result["errors"].append(f"Connection failed: {str(e)}")
                logger.error(f"❌ Database connection failed: {e}")
                return result
            finally:
                session.close()
            
            # Use cached schema
            schema = self._get_cached_schema()
            if "error" in schema:
                result["errors"].append(f"Schema check failed: {schema['error']}")
                logger.error(f"❌ Schema check failed: {schema['error']}")
                return result
            
            if "delivery_reports" not in schema.get("tables", []):
                result["errors"].append("Table 'delivery_reports' does not exist")
                logger.error("❌ Table 'delivery_reports' not found")
                return result
            
            result["healthy"] = True
            self._status = "READY"
            
            if not self._production_mode:
                logger.info("✅ Health check PASSED")
            return result
            
        except Exception as e:
            result["errors"].append(f"Health check failed: {str(e)}")
            logger.error(f"❌ Health check failed: {e}")
            return result
    
    def validation_query(self) -> Dict[str, Any]:
        """Used by ai_provider_service.py for validation."""
        result = {
            "success": False,
            "records": 0,
            "error": None
        }
        
        session = None
        try:
            session = self._get_session()
            if not session:
                result["error"] = "SessionLocal not available"
                logger.error("❌ Validation failed: SessionLocal not available")
                return result
            
            query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports"
            query_result = session.execute(text(query))
            row = query_result.fetchone()
            
            if row:
                count = row[0] or 0
                result["success"] = True
                result["records"] = count
                if not self._production_mode:
                    logger.info(f"✅ Validation query successful: {count} DNs")
            else:
                result["error"] = "Query returned no results"
                logger.error("❌ Validation query returned no results")
            
            return result
            
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"❌ Validation query failed: {e}")
            return result
        finally:
            if session:
                session.close()
    
    def get_service_metadata(self) -> Dict[str, Any]:
        """Get service metadata for ai_provider_service.py."""
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": self._status,
            "module": "DN Analytics",
            "description": "DN Analytics Service - Enterprise Optimized with Robust Search",
            "date_policy": "Native PostgreSQL DATE values (YYYY-MM-DD)",
            "debug_mode": self._debug_mode,
            "production_mode": self._production_mode,
            "methods": [
                "health_check",
                "validation_query",
                "get_service_metadata",
                "search_dn",
                "search_dn_flexible",
                "verify_dn",
                "get_dn_dashboard",
                "diagnose_dn",
                "check_dn_raw",
                "test_dn_lookup",
                "test_date_calculation",
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod",
                "calculate_delivery_aging",
                "calculate_pod_aging",
                "calculate_total_cycle",
                "format_dn_dashboard"
            ]
        }
    
    # ==========================================================
    # BLOCK 6: DATE ENGINE
    # ==========================================================
    
    def _validate_postgresql_date(self, date_value, field_name: str = "date") -> Dict[str, Any]:
        """CENTRAL DATE VALIDATOR."""
        result = {
            "valid": False,
            "value": None,
            "type": "unknown",
            "formatted": "N/A",
            "error": None,
            "field": field_name
        }
        
        if date_value is None:
            result["error"] = "NULL value"
            result["type"] = "NoneType"
            return result
        
        if isinstance(date_value, (date, datetime)):
            result["type"] = "date" if isinstance(date_value, date) else "datetime"
            result["value"] = date_value
            result["formatted"] = date_value.strftime('%Y-%m-%d')
            result["valid"] = True
            return result
        
        elif isinstance(date_value, str):
            result["type"] = "string"
            if self._debug_mode:
                logger.warning(f"⚠️ {field_name}: Expected DATE object but received string: '{date_value}'")
            
            parts = date_value.split('-')
            if len(parts) == 3:
                try:
                    year = int(parts[0])
                    month = int(parts[1])
                    day = int(parts[2])
                    if 1 <= year <= 9999 and 1 <= month <= 12 and 1 <= day <= 31:
                        parsed = datetime(year, month, day)
                        result["value"] = parsed
                        result["formatted"] = parsed.strftime('%Y-%m-%d')
                        result["valid"] = True
                        return result
                except ValueError:
                    pass
            
            result["error"] = f"Invalid date format: {date_value}"
            return result
        
        else:
            result["error"] = f"Unsupported type: {type(date_value)}"
            return result
    
    def _format_display_date(self, date_value) -> str:
        """Format PostgreSQL date for display (YYYY-MM-DD)."""
        if date_value is None:
            return 'N/A'
        
        try:
            if isinstance(date_value, (date, datetime)):
                return date_value.strftime('%Y-%m-%d')
            elif isinstance(date_value, str):
                if len(date_value) == 10 and date_value[4] == '-' and date_value[7] == '-':
                    return date_value
                parsed = datetime.strptime(date_value, "%Y-%m-%d")
                return parsed.strftime('%Y-%m-%d')
            else:
                return str(date_value)
        except (ValueError, TypeError):
            return str(date_value) if date_value else 'N/A'
    
    def _parse_date(self, date_value):
        """Parse PostgreSQL date WITHOUT any conversion."""
        if not date_value:
            return None
        
        validation_result = self._validate_postgresql_date(date_value, "parse_date")
        if validation_result["valid"]:
            return validation_result["value"]
        return None
    
    def _format_aging_text(self, days: int) -> str:
        """Format aging days into human readable text."""
        if days < 0:
            return f"{abs(days)} Days (Data Error)"
        elif days == 0:
            return "Same Day"
        elif days == 1:
            return "1 Day"
        elif days < 7:
            return f"{days} Days"
        elif days < 14:
            return f"{days} Days (1-2 Weeks)"
        elif days < 30:
            return f"{days} Days ({days // 7} Weeks)"
        elif days < 60:
            return f"{days} Days (1-2 Months)"
        elif days < 90:
            return f"{days} Days (3 Months)"
        elif days < 365:
            return f"{days} Days ({days // 30} Months)"
        else:
            years = days // 365
            months = (days % 365) // 30
            if months > 0:
                return f"{days} Days ({years} Year{'s' if years > 1 else ''}, {months} Month{'s' if months > 1 else ''})"
            return f"{days} Days ({years} Year{'s' if years > 1 else ''})"
    
    def _safe_date_diff(self, date1, date2) -> int:
        """Safely calculate days between two dates."""
        if date1 is None or date2 is None:
            return 0
        
        try:
            if not isinstance(date1, (date, datetime)):
                return 0
            if not isinstance(date2, (date, datetime)):
                return 0
            
            if isinstance(date1, datetime):
                date1 = date1.date()
            if isinstance(date2, datetime):
                date2 = date2.date()
            
            delta = date2 - date1
            return max(0, delta.days)
        except Exception:
            return 0
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        """Calculate delivery aging."""
        try:
            if dn_create_date is None:
                return 0
            
            dn_date = self._parse_date(dn_create_date)
            if dn_date is None:
                return 0
            
            if good_issue_date is None:
                current_date = datetime.now().date()
                return self._safe_date_diff(dn_date, current_date)
            
            gi_date = self._parse_date(good_issue_date)
            if gi_date is None:
                return 0
            
            return self._safe_date_diff(dn_date, gi_date)
        except Exception:
            return 0
    
    def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
        """Calculate POD aging."""
        try:
            if good_issue_date is None:
                return 0
            
            gi_date = self._parse_date(good_issue_date)
            if gi_date is None:
                return 0
            
            if pod_date is None:
                current_date = datetime.now().date()
                return self._safe_date_diff(gi_date, current_date)
            
            pd_date = self._parse_date(pod_date)
            if pd_date is None:
                return 0
            
            return self._safe_date_diff(gi_date, pd_date)
        except Exception:
            return 0
    
    def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
        """Calculate total cycle."""
        try:
            if dn_create_date is None:
                return 0
            
            dn_date = self._parse_date(dn_create_date)
            if dn_date is None:
                return 0
            
            if pod_date is None:
                current_date = datetime.now().date()
                return self._safe_date_diff(dn_date, current_date)
            
            pd_date = self._parse_date(pod_date)
            if pd_date is None:
                return 0
            
            return self._safe_date_diff(dn_date, pd_date)
        except Exception:
            return 0
    
    # ==========================================================
    # BLOCK 7: DN SEARCH - ROBUST WITH 5 STRATEGIES
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """
        ROBUST DN search with multiple fallback strategies.
        
        Strategies:
        1. Primary optimized query (exact match + patterns)
        2. Fallback query with multiple patterns
        3. Partial match with suffix
        4. Fuzzy match with any pattern
        5. Direct database check
        """
        start_time = time.time()
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        
        if self._debug_mode:
            logger.debug(f"🔍 Searching for DN: '{dn_no}' → '{normalized_dn}'")
        
        # Allow shorter DNs (5+ digits)
        if len(normalized_dn) < 5:
            return {"success": False, "error": f"Invalid DN format: {normalized_dn} (must be at least 5 digits)"}
        
        # ==========================================================
        # STRATEGY 1: PRIMARY OPTIMIZED LOOKUP
        # ==========================================================
        
        query = self._build_primary_dn_query()
        results = self._execute_query(query, {"dn_no": normalized_dn})
        
        if results:
            data = results[0]
            execution_time = (time.time() - start_time) * 1000
            logger.info(f"✅ DN {dn_no} found (Strategy 1) | Materials: {data.get('material_count', 1)} | Time: {execution_time:.2f}ms")
            return {"success": True, "data": data}
        
        # ==========================================================
        # STRATEGY 2: TRY RAW DN (if normalized different)
        # ==========================================================
        
        if dn_no != normalized_dn:
            if self._debug_mode:
                logger.debug(f"🔄 Strategy 2: Trying raw DN: '{dn_no}'")
            
            results = self._execute_query(query, {"dn_no": dn_no})
            if results:
                data = results[0]
                execution_time = (time.time() - start_time) * 1000
                logger.info(f"✅ DN {dn_no} found (Strategy 2 - Raw) | Time: {execution_time:.2f}ms")
                return {"success": True, "data": data}
        
        # ==========================================================
        # STRATEGY 3: FALLBACK WITH MULTIPLE PATTERNS
        # ==========================================================
        
        if self._debug_mode:
            logger.debug(f"🔄 Strategy 3: Trying fallback with patterns")
        
        # Try different pattern variations
        patterns = [
            f"%{normalized_dn}%",
            f"%{normalized_dn[-6:]}%" if len(normalized_dn) >= 6 else None,
            f"{normalized_dn[:6]}%" if len(normalized_dn) >= 6 else None,
        ]
        
        for pattern in patterns:
            if not pattern:
                continue
            
            fallback_query = self._build_fallback_dn_query()
            results = self._execute_query(
                fallback_query,
                {
                    "dn_no": normalized_dn,
                    "pattern1": f"%{normalized_dn}%",
                    "pattern2": f"%{normalized_dn[-6:]}%" if len(normalized_dn) >= 6 else f"%{normalized_dn}%",
                    "pattern3": f"{normalized_dn[:6]}%" if len(normalized_dn) >= 6 else f"%{normalized_dn}%"
                }
            )
            
            if results:
                data = results[0]
                execution_time = (time.time() - start_time) * 1000
                logger.info(f"✅ DN {dn_no} found (Strategy 3 - Fallback) | Time: {execution_time:.2f}ms")
                return {"success": True, "data": data}
        
        # ==========================================================
        # STRATEGY 4: SIMILAR DNs - FIND AND RETURN FOR SUGGESTION
        # ==========================================================
        
        if self._debug_mode:
            logger.debug(f"🔄 Strategy 4: Looking for similar DNs")
        
        similar_query = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE 
                dn_no LIKE :pattern1
                OR RIGHT(dn_no, 6) = RIGHT(:dn_no, 6)
            LIMIT 10
        """
        
        similar_results = self._execute_query(
            similar_query,
            {
                "dn_no": normalized_dn,
                "pattern1": f"%{normalized_dn[:4]}%"
            }
        )
        similar_dns = [str(r.get('dn_no', '')) for r in similar_results if r.get('dn_no')]
        
        if similar_dns:
            logger.info(f"📋 Similar DNs found: {similar_dns[:5]}")
            return {
                "success": False,
                "error": f"DN {dn_no} not found",
                "similar_dns": similar_dns[:5],
                "message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
            }
        
        # ==========================================================
        # STRATEGY 5: ULTIMATE FALLBACK - DIRECT DATABASE CHECK
        # ==========================================================
        
        if self._debug_mode:
            logger.debug(f"🔄 Strategy 5: Ultimate fallback - direct check")
        
        direct_result = self._direct_dn_check(normalized_dn)
        if direct_result.get("found"):
            similar_dns = direct_result.get("similar_dns", [])
            if similar_dns:
                return {
                    "success": False,
                    "error": f"DN {dn_no} not found",
                    "similar_dns": similar_dns[:5],
                    "message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
                }
        
        # ==========================================================
        # NO RESULTS
        # ==========================================================
        
        logger.warning(f"❌ DN {dn_no} not found - no matches after 5 strategies")
        return {"success": False, "error": f"DN {dn_no} not found"}
    
    def _direct_dn_check(self, dn_no: str) -> Dict[str, Any]:
        """Direct database check for DN existence."""
        session = None
        try:
            session = self._get_session()
            if not session:
                return {"found": False, "similar_dns": []}
            
            # Try multiple direct queries
            queries = [
                "SELECT dn_no FROM delivery_reports WHERE dn_no = :dn_no LIMIT 1",
                "SELECT dn_no FROM delivery_reports WHERE dn_no LIKE :pattern LIMIT 10",
                "SELECT dn_no FROM delivery_reports WHERE RIGHT(dn_no, 6) = RIGHT(:dn_no, 6) LIMIT 10",
            ]
            
            for query in queries:
                params = {"dn_no": dn_no} if "=" in query else {"pattern": f"%{dn_no}%", "dn_no": dn_no}
                result = session.execute(text(query), params)
                rows = result.fetchall()
                if rows:
                    dns = [row[0] for row in rows]
                    return {"found": True, "similar_dns": dns}
            
            return {"found": False, "similar_dns": []}
        except Exception as e:
            logger.error(f"❌ Direct DN check failed: {e}")
            return {"found": False, "similar_dns": []}
        finally:
            if session:
                session.close()
    
    def search_dn_flexible(self, dn_no: str) -> Dict[str, Any]:
        """
        FLEXIBLE DN search - tries multiple formats and strategies.
        Specifically designed for problematic DNs.
        """
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        # Try different formats
        formats = [
            dn_no,
            dn_no.strip(),
            dn_no.replace('-', ''),
            dn_no.replace('/', ''),
            dn_no.replace(' ', ''),
            dn_no.replace('_', ''),
            dn_no.lstrip('0'),  # Remove leading zeros
        ]
        
        # Remove duplicates
        unique_formats = list(set(formats))
        
        # Try each format
        for dn_format in unique_formats:
            if dn_format and len(dn_format) >= 5:
                result = self.search_dn(dn_format)
                if result.get("success"):
                    logger.info(f"✅ Found DN {dn_format} (from {dn_no})")
                    return result
        
        # Try partial match with last 6 digits
        normalized = self._normalize_dn(dn_no)
        if len(normalized) >= 6:
            suffix_query = """
                SELECT DISTINCT dn_no
                FROM delivery_reports
                WHERE RIGHT(dn_no, 6) = RIGHT(:dn_no, 6)
                LIMIT 10
            """
            results = self._execute_query(suffix_query, {"dn_no": normalized})
            similar_dns = [str(r.get('dn_no', '')) for r in results if r.get('dn_no')]
            
            if similar_dns:
                # Try each similar DN
                for similar_dn in similar_dns:
                    result = self.search_dn(similar_dn)
                    if result.get("success"):
                        logger.info(f"✅ Found DN {similar_dn} as match for {dn_no}")
                        return result
                
                return {
                    "success": False,
                    "error": f"DN {dn_no} not found",
                    "similar_dns": similar_dns[:5],
                    "message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
                }
        
        return {"success": False, "error": f"DN {dn_no} not found"}
    
    # ==========================================================
    # BLOCK 8: VERIFY DN
    # ==========================================================
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        """Verify if DN exists using robust search."""
        if not dn_no:
            return {"success": False, "exists": False, "error": "DN number required"}
        
        # Use flexible search
        result = self.search_dn_flexible(dn_no)
        exists = result.get("success", False)
        
        logger.info(f"✅ DN {dn_no} exists: {exists}")
        return {"success": True, "exists": exists}
    
    # ==========================================================
    # BLOCK 9: TEST DN LOOKUP
    # ==========================================================
    
    def test_dn_lookup(self, dn_no: str) -> Dict[str, Any]:
        """Test DN lookup with full diagnostics."""
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        results = {
            "dn": dn_no,
            "normalized": normalized_dn,
            "exact_count": 0,
            "like_count": 0,
            "suffix_count": 0,
            "matching_dns": [],
            "diagnostics": []
        }
        
        # Test exact match
        query1 = "SELECT COUNT(*) as count FROM delivery_reports WHERE dn_no = :dn_no"
        r1 = self._execute_query(query1, {"dn_no": normalized_dn})
        results["exact_count"] = r1[0].get('count', 0) if r1 else 0
        results["diagnostics"].append(f"Exact match: {results['exact_count']}")
        
        # Test like match
        query2 = "SELECT COUNT(*) as count FROM delivery_reports WHERE dn_no LIKE :pattern"
        r2 = self._execute_query(query2, {"pattern": f"%{normalized_dn}%"})
        results["like_count"] = r2[0].get('count', 0) if r2 else 0
        results["diagnostics"].append(f"LIKE match: {results['like_count']}")
        
        # Test suffix match
        if len(normalized_dn) >= 6:
            query3 = "SELECT COUNT(*) as count FROM delivery_reports WHERE RIGHT(dn_no, 6) = RIGHT(:dn_no, 6)"
            r3 = self._execute_query(query3, {"dn_no": normalized_dn})
            results["suffix_count"] = r3[0].get('count', 0) if r3 else 0
            results["diagnostics"].append(f"Suffix match: {results['suffix_count']}")
        
        # Get matching DNs
        query4 = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE dn_no LIKE :pattern
            LIMIT 10
        """
        r4 = self._execute_query(query4, {"pattern": f"%{normalized_dn[:4]}%"})
        results["matching_dns"] = [str(r.get('dn_no', '')) for r in r4 if r.get('dn_no')]
        
        results["found"] = results["exact_count"] > 0 or results["like_count"] > 0
        results["diagnostics"].append(f"Total matching DNs: {len(results['matching_dns'])}")
        
        logger.info(f"✅ Test DN lookup complete: found={results['found']}")
        return {"success": True, "data": results}
    
    # ==========================================================
    # BLOCK 10: DN DASHBOARD BUILDER
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete DN dashboard with enterprise analytics.
        Uses flexible search to find DNs.
        """
        start_time = time.time()
        logger.info(f"📊 Dashboard started for DN: '{dn_no}'")
        
        if not dn_no:
            logger.warning("⚠️ Dashboard: DN number required")
            return {"success": False, "error": "DN number required"}
        
        # ==========================================================
        # STEP 1: GET DATA USING FLEXIBLE SEARCH
        # ==========================================================
        
        search_result = self.search_dn_flexible(dn_no)
        
        if not search_result.get("success"):
            similar_dns = search_result.get("similar_dns", [])
            error_msg = f"DN {dn_no} not found"
            if similar_dns:
                error_msg += f". Similar: {', '.join(similar_dns[:3])}"
            logger.warning(f"⚠️ Dashboard: {error_msg}")
            return {"success": False, "error": error_msg}
        
        data = search_result.get("data", {})
        
        # ==========================================================
        # STEP 2: EXTRACT VALUES
        # ==========================================================
        
        dn_no_raw = data.get('dn_no')
        dealer_name = data.get('dealer_name', 'Unknown')
        dealer_code = data.get('dealer_code')
        customer_name = data.get('customer_name', dealer_name)
        customer_code = data.get('customer_code')
        warehouse = data.get('warehouse', 'Unknown')
        warehouse_code = data.get('warehouse_code')
        city = data.get('city', 'Unknown')
        delivery_location = data.get('delivery_location')
        sales_manager = data.get('sales_manager')
        sales_office = data.get('sales_office')
        division = data.get('division')
        
        total_units = int(data.get('total_units', 0) or 0)
        total_revenue = float(data.get('total_revenue', 0) or 0)
        material_count = int(data.get('material_count', 1) or 1)
        model_count = int(data.get('model_count', 0) or 0)
        
        raw_dn_create = data.get('dn_create_date')
        raw_good_issue = data.get('good_issue_date')
        raw_pod = data.get('pod_date')
        
        db_delivery_status = data.get('delivery_status')
        db_pgi_status = data.get('pgi_status')
        db_pod_status = data.get('pod_status')
        db_pending_flag = data.get('pending_flag')
        
        source_file = data.get('source_file')
        upload_batch_id = data.get('upload_batch_id')
        created_at = data.get('created_at')
        updated_at = data.get('updated_at')
        imported_at = data.get('imported_at')
        
        # ==========================================================
        # STEP 3: CALCULATE AGING
        # ==========================================================
        
        delivery_aging = self.calculate_delivery_aging(raw_dn_create, raw_good_issue)
        pod_aging = self.calculate_pod_aging(raw_good_issue, raw_pod)
        total_cycle = self.calculate_total_cycle(raw_dn_create, raw_pod)
        
        # ==========================================================
        # STEP 4: FORMAT DATES
        # ==========================================================
        
        formatted_dn_create = self._format_display_date(raw_dn_create)
        formatted_good_issue = self._format_display_date(raw_good_issue)
        formatted_pod = self._format_display_date(raw_pod)
        
        # ==========================================================
        # STEP 5: GET PRODUCTS
        # ==========================================================
        
        products = []
        try:
            if dn_no_raw:
                normalized_dn = self._normalize_dn(dn_no_raw)
                product_query = self._build_products_query()
                product_results = self._execute_query(product_query, {"dn_no": normalized_dn})
                
                product_units = 0
                product_revenue = 0
                
                for row in product_results:
                    model_name = row.get('model_name')
                    if not model_name:
                        continue
                    
                    qty = int(row.get('quantity', 0) or 0)
                    revenue = float(row.get('revenue', 0) or 0)
                    material_no = row.get('material_number', 'N/A')
                    product_division = row.get('division', division or 'Unknown')
                    
                    products.append({
                        'name': str(model_name),
                        'material_no': str(material_no),
                        'division': str(product_division),
                        'qty': qty,
                        'revenue': revenue
                    })
                    
                    product_units += qty
                    product_revenue += revenue
                
                if products and product_units > 0:
                    total_units = product_units
                if products and product_revenue > 0:
                    total_revenue = product_revenue
                    
        except Exception as e:
            if self._debug_mode:
                logger.error(f"❌ Dashboard: Product query failed: {e}")
        
        # ==========================================================
        # STEP 6: DETERMINE STATUS
        # ==========================================================
        
        pgi_exists = raw_good_issue is not None
        pod_exists = raw_pod is not None
        
        if pod_exists and pgi_exists:
            calculated_stage = "Delivered"
            calculated_emoji = "✅"
            pgi_status_display = "Completed"
            pod_status_display = "Completed"
            pending_flag = False
            pending_flag_text = "🟢 No"
        elif pgi_exists and not pod_exists:
            calculated_stage = "Dispatched"
            calculated_emoji = "🚚"
            pgi_status_display = "Completed"
            pod_status_display = "Pending"
            pending_flag = True
            pending_flag_text = "⚠️ Yes"
        else:
            calculated_stage = "Pending Dispatch"
            calculated_emoji = "⏳"
            pgi_status_display = "Pending"
            pod_status_display = "Pending"
            pending_flag = True
            pending_flag_text = "⚠️ Yes"
        
        delivery_status = db_delivery_status or calculated_stage
        pgi_status = db_pgi_status or pgi_status_display
        pod_status = db_pod_status or pod_status_display
        final_pending_flag = db_pending_flag if db_pending_flag is not None else pending_flag
        
        # ==========================================================
        # STEP 7: BUILD DASHBOARD
        # ==========================================================
        
        dashboard = {
            "dn_no": dn_no_raw,
            "dealer_name": dealer_name,
            "dealer_code": dealer_code,
            "customer_name": customer_name,
            "customer_code": customer_code,
            "warehouse": warehouse,
            "warehouse_code": warehouse_code,
            "city": city,
            "delivery_location": delivery_location,
            "sales_manager": sales_manager,
            "sales_office": sales_office,
            "division": division,
            "total_units": total_units,
            "total_revenue": total_revenue,
            "material_count": material_count,
            "model_count": model_count,
            "dn_create_date": formatted_dn_create,
            "good_issue_date": formatted_good_issue,
            "pod_date": formatted_pod,
            "_dn_create_date": raw_dn_create,
            "_good_issue_date": raw_good_issue,
            "_pod_date": raw_pod,
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "total_cycle_days": total_cycle,
            "delivery_aging_text": self._format_aging_text(delivery_aging),
            "pod_aging_text": self._format_aging_text(pod_aging),
            "total_cycle_text": self._format_aging_text(total_cycle),
            "delivery_status": delivery_status,
            "pgi_status": pgi_status,
            "pod_status": pod_status,
            "pending_flag": final_pending_flag,
            "pending_flag_text": pending_flag_text,
            "calculated_stage": calculated_stage,
            "calculated_emoji": calculated_emoji,
            "products": products,
            "source_file": source_file,
            "upload_batch_id": upload_batch_id,
            "created_at": self._format_display_date(created_at),
            "updated_at": self._format_display_date(updated_at),
            "imported_at": self._format_display_date(imported_at),
        }
        
        execution_time = (time.time() - start_time) * 1000
        logger.info(
            f"✅ Dashboard completed | DN: {dn_no_raw} | "
            f"Units: {dashboard['total_units']} | "
            f"Revenue: {dashboard['total_revenue']} | "
            f"Products: {len(products)} | "
            f"Time: {execution_time:.2f}ms"
        )
        
        return {"success": True, "data": dashboard}
    
    # ==========================================================
    # BLOCK 11: DIAGNOSTIC METHODS
    # ==========================================================
    
    def diagnose_dn(self, dn_no: str) -> Dict[str, Any]:
        """Diagnose DN issues."""
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        
        result = {
            "dn": dn_no,
            "normalized": normalized_dn,
            "exact_match_count": 0,
            "partial_match_count": 0,
            "similar_dns": [],
            "exists": False,
            "diagnostic": []
        }
        
        # Exact match
        exact_query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports WHERE dn_no = :dn_no"
        exact_results = self._execute_query(exact_query, {"dn_no": normalized_dn})
        exact_count = exact_results[0].get('count', 0) if exact_results else 0
        result["exact_match_count"] = exact_count
        result["exists"] = exact_count > 0
        result["diagnostic"].append(f"Exact match: {exact_count} found")
        
        # Partial match
        partial_query = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE dn_no LIKE :pattern
            LIMIT 10
        """
        partial_results = self._execute_query(partial_query, {"pattern": f"%{normalized_dn}%"})
        similar_dns = [str(r.get('dn_no', '')) for r in partial_results if r.get('dn_no')]
        result["partial_match_count"] = len(similar_dns)
        result["similar_dns"] = similar_dns[:10]
        result["diagnostic"].append(f"Partial matches: {len(similar_dns)} found")
        
        if similar_dns:
            result["diagnostic"].append(f"Similar DNs: {', '.join(similar_dns[:5])}")
        
        logger.info(f"✅ Diagnosis complete for {dn_no}: exists={result['exists']}")
        return {"success": True, "data": result}
    
    def check_dn_raw(self, dn_no: str) -> Dict[str, Any]:
        """Check raw DN existence without any normalization."""
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        query = "SELECT DISTINCT dn_no FROM delivery_reports WHERE dn_no LIKE :pattern LIMIT 10"
        results = self._execute_query(query, {"pattern": f"%{dn_no}%"})
        
        similar_dns = [str(r.get('dn_no', '')) for r in results if r.get('dn_no')]
        
        return {
            "success": True,
            "dn": dn_no,
            "found": len(similar_dns) > 0,
            "similar_dns": similar_dns[:10],
            "count": len(similar_dns)
        }
    
    def debug_aging_calculation(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        """Debug aging calculations."""
        delivery_aging = self.calculate_delivery_aging(dn_create_date, good_issue_date)
        pod_aging = self.calculate_pod_aging(good_issue_date, pod_date)
        total_cycle = self.calculate_total_cycle(dn_create_date, pod_date)
        
        result = {
            "input_dates": {
                "dn_create_date": self._format_display_date(dn_create_date),
                "pgi_date": self._format_display_date(good_issue_date),
                "pod_date": self._format_display_date(pod_date)
            },
            "calculations": {
                "delivery_aging_days": delivery_aging,
                "pod_aging_days": pod_aging,
                "total_cycle_days": total_cycle
            },
            "formatted": {
                "delivery_aging_text": self._format_aging_text(delivery_aging),
                "pod_aging_text": self._format_aging_text(pod_aging) if pod_aging > 0 else "Not Started",
                "total_cycle_text": self._format_aging_text(total_cycle)
            },
            "timestamp": datetime.now().isoformat()
        }
        
        return result
    
    # ==========================================================
    # BLOCK 12: PENDING METHODS
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get all pending DNs."""
        logger.info(f"🔍 Getting pending DNs (limit: {limit}, offset: {offset})")
        
        try:
            limit = min(limit, 1000)
            
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR delivery_status = 'Pending'
                   OR pending_flag = TRUE
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
                    "message": "No pending DNs found"
                }
            
            pending_query = """
                SELECT 
                    dn_no,
                    MAX(customer_name) AS dealer_name,
                    MAX(warehouse) AS warehouse,
                    MAX(ship_to_city) AS city,
                    SUM(dn_qty) AS total_units,
                    SUM(dn_amount) AS total_revenue,
                    MIN(dn_create_date) AS dn_create_date,
                    MAX(good_issue_date) AS good_issue_date,
                    MAX(pod_date) AS pod_date,
                    MAX(delivery_status) AS delivery_status,
                    MAX(pgi_status) AS pgi_status,
                    MAX(pod_status) AS pod_status,
                    MAX(pending_flag) AS pending_flag,
                    MAX(sales_manager) AS sales_manager,
                    MAX(division) AS division,
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR delivery_status = 'Pending'
                   OR pending_flag = TRUE
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(
                pending_query,
                {"limit": limit, "offset": offset}
            )
            
            formatted_results = []
            for row in results:
                delivery_aging = self.calculate_delivery_aging(
                    row.get('dn_create_date'),
                    row.get('good_issue_date')
                )
                
                for date_field in ['dn_create_date', 'good_issue_date', 'pod_date']:
                    if row.get(date_field):
                        if isinstance(row[date_field], (datetime, date)):
                            row[date_field] = row[date_field].strftime("%Y-%m-%d")
                
                pending_flag = row.get('pending_flag')
                if pending_flag is True or pending_flag == 'true' or pending_flag == 'True' or pending_flag == 1:
                    pending_flag_text = '⚠️ Yes'
                else:
                    pending_flag_text = '🟢 No'
                
                formatted_row = {
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": row.get('dn_create_date'),
                    "good_issue_date": row.get('good_issue_date'),
                    "pod_date": row.get('pod_date'),
                    "delivery_status": row.get('delivery_status') or "Pending",
                    "pgi_status": row.get('pgi_status') or "Pending",
                    "pod_status": row.get('pod_status') or "Unknown",
                    "pending_flag": pending_flag,
                    "pending_flag_text": pending_flag_text,
                    "delivery_aging_days": delivery_aging,
                    "delivery_aging_text": self._format_aging_text(delivery_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                }
                formatted_results.append(formatted_row)
            
            return {
                "success": True,
                "data": formatted_results,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted_results)
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending DNs: {e}")
            return {"success": False, "error": str(e)}
    
    def get_pending_pgi(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get all pending PGI deliveries."""
        logger.info(f"🔍 Getting pending PGI (limit: {limit}, offset: {offset})")
        
        try:
            limit = min(limit, 1000)
            
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NULL
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
                    "message": "No pending PGI found"
                }
            
            pending_query = """
                SELECT 
                    dn_no,
                    MAX(customer_name) AS dealer_name,
                    MAX(warehouse) AS warehouse,
                    MAX(ship_to_city) AS city,
                    SUM(dn_qty) AS total_units,
                    SUM(dn_amount) AS total_revenue,
                    MIN(dn_create_date) AS dn_create_date,
                    MAX(good_issue_date) AS good_issue_date,
                    MAX(pod_date) AS pod_date,
                    MAX(delivery_status) AS delivery_status,
                    MAX(pgi_status) AS pgi_status,
                    MAX(pod_status) AS pod_status,
                    MAX(pending_flag) AS pending_flag,
                    MAX(sales_manager) AS sales_manager,
                    MAX(division) AS division,
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(
                pending_query,
                {"limit": limit, "offset": offset}
            )
            
            formatted_results = []
            for row in results:
                delivery_aging = self.calculate_delivery_aging(
                    row.get('dn_create_date'),
                    row.get('good_issue_date')
                )
                
                for date_field in ['dn_create_date', 'good_issue_date', 'pod_date']:
                    if row.get(date_field):
                        if isinstance(row[date_field], (datetime, date)):
                            row[date_field] = row[date_field].strftime("%Y-%m-%d")
                
                pending_flag = row.get('pending_flag')
                if pending_flag is True or pending_flag == 'true' or pending_flag == 'True' or pending_flag == 1:
                    pending_flag_text = '⚠️ Yes'
                else:
                    pending_flag_text = '🟢 No'
                
                formatted_row = {
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": row.get('dn_create_date'),
                    "good_issue_date": row.get('good_issue_date'),
                    "pod_date": row.get('pod_date'),
                    "delivery_status": row.get('delivery_status') or "Pending",
                    "pgi_status": row.get('pgi_status') or "Pending",
                    "pod_status": row.get('pod_status') or "Unknown",
                    "pending_flag": pending_flag,
                    "pending_flag_text": pending_flag_text,
                    "delivery_aging_days": delivery_aging,
                    "delivery_aging_text": self._format_aging_text(delivery_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                }
                formatted_results.append(formatted_row)
            
            return {
                "success": True,
                "data": formatted_results,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted_results)
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending PGI: {e}")
            return {"success": False, "error": str(e)}
    
    def get_pending_pod(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get all pending POD deliveries."""
        logger.info(f"🔍 Getting pending POD (limit: {limit}, offset: {offset})")
        
        try:
            limit = min(limit, 1000)
            
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NOT NULL
                  AND pod_date IS NULL
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
                    "message": "No pending POD found"
                }
            
            pending_query = """
                SELECT 
                    dn_no,
                    MAX(customer_name) AS dealer_name,
                    MAX(warehouse) AS warehouse,
                    MAX(ship_to_city) AS city,
                    SUM(dn_qty) AS total_units,
                    SUM(dn_amount) AS total_revenue,
                    MIN(dn_create_date) AS dn_create_date,
                    MAX(good_issue_date) AS good_issue_date,
                    MAX(pod_date) AS pod_date,
                    MAX(delivery_status) AS delivery_status,
                    MAX(pgi_status) AS pgi_status,
                    MAX(pod_status) AS pod_status,
                    MAX(pending_flag) AS pending_flag,
                    MAX(sales_manager) AS sales_manager,
                    MAX(division) AS division,
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE good_issue_date IS NOT NULL
                  AND pod_date IS NULL
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(
                pending_query,
                {"limit": limit, "offset": offset}
            )
            
            formatted_results = []
            for row in results:
                pod_aging = self.calculate_pod_aging(
                    row.get('good_issue_date'),
                    row.get('pod_date')
                )
                
                for date_field in ['dn_create_date', 'good_issue_date', 'pod_date']:
                    if row.get(date_field):
                        if isinstance(row[date_field], (datetime, date)):
                            row[date_field] = row[date_field].strftime("%Y-%m-%d")
                
                pending_flag = row.get('pending_flag')
                if pending_flag is True or pending_flag == 'true' or pending_flag == 'True' or pending_flag == 1:
                    pending_flag_text = '⚠️ Yes'
                else:
                    pending_flag_text = '🟢 No'
                
                formatted_row = {
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": row.get('dn_create_date'),
                    "good_issue_date": row.get('good_issue_date'),
                    "pod_date": row.get('pod_date'),
                    "delivery_status": row.get('delivery_status') or "In Transit",
                    "pgi_status": row.get('pgi_status') or "Completed",
                    "pod_status": row.get('pod_status') or "Pending",
                    "pending_flag": pending_flag,
                    "pending_flag_text": pending_flag_text,
                    "pod_aging_days": pod_aging,
                    "pod_aging_text": self._format_aging_text(pod_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                }
                formatted_results.append(formatted_row)
            
            return {
                "success": True,
                "data": formatted_results,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted_results)
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending POD: {e}")
            return {"success": False, "error": str(e)}
    
    # ==========================================================
    # BLOCK 13: WHATSAPP RESPONSE FORMATTER
    # ==========================================================
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """
        Format DN dashboard for WhatsApp response.
        This format MUST remain identical for backward compatibility.
        """
        data = dashboard_data.get('data', {})
        
        lines = []
        
        # Header
        lines.append("📦 *DN: {}*".format(data.get('dn_no', 'N/A')))
        lines.append("")
        
        # Dealer & Warehouse
        lines.append("*Dealer:*")
        lines.append("{}".format(data.get('dealer_name', 'Unknown')))
        lines.append("")
        
        lines.append("*Warehouse:*")
        lines.append("{}".format(data.get('warehouse', 'Unknown')))
        lines.append("")
        
        lines.append("*City:*")
        lines.append("{}".format(data.get('city', 'Unknown')))
        lines.append("")
        
        delivery_location = data.get('delivery_location')
        if delivery_location:
            lines.append("*Delivery Location:*")
            lines.append("{}".format(delivery_location))
            lines.append("")
        
        sales_manager = data.get('sales_manager')
        if sales_manager:
            lines.append("*Sales Manager:*")
            lines.append("{}".format(sales_manager))
            lines.append("")
        
        division = data.get('division')
        if division:
            lines.append("*Division:*")
            lines.append("{}".format(division))
            lines.append("")
        
        dealer_code = data.get('dealer_code')
        if dealer_code:
            lines.append("*Dealer Code:*")
            lines.append("{}".format(dealer_code))
            lines.append("")
        
        warehouse_code = data.get('warehouse_code')
        if warehouse_code:
            lines.append("*Warehouse Code:*")
            lines.append("{}".format(warehouse_code))
            lines.append("")
        
        # Metrics
        lines.append("*📊 Metrics:*")
        units = data.get('total_units', 0)
        lines.append("Units: {}".format(units))
        revenue = data.get('total_revenue', 0)
        if revenue:
            lines.append("Revenue: PKR {:,}".format(revenue))
        else:
            lines.append("Revenue: PKR 0")
        lines.append("")
        
        material_count = data.get('material_count', 1)
        lines.append("Materials: {}".format(material_count))
        model_count = data.get('model_count', 0)
        if model_count > 0:
            lines.append("Models: {}".format(model_count))
        lines.append("")
        
        # Dates
        lines.append("*📅 Dates:*")
        lines.append("DN Create: {}".format(data.get('dn_create_date', 'N/A')))
        lines.append("PGI: {}".format(data.get('good_issue_date', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_date', 'N/A')))
        lines.append("")
        
        # Aging
        lines.append("*⏳ Aging:*")
        lines.append("Delivery: {}".format(data.get('delivery_aging_text', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_aging_text', 'N/A')))
        lines.append("Total Cycle: {}".format(data.get('total_cycle_text', 'N/A')))
        lines.append("")
        
        # Status
        calculated_stage = data.get('calculated_stage', 'Unknown')
        calculated_emoji = data.get('calculated_emoji', '❓')
        
        raw_good_issue = data.get('_good_issue_date')
        raw_pod = data.get('_pod_date')
        
        pgi_exists = raw_good_issue is not None
        pod_exists = raw_pod is not None
        
        if pod_exists and pgi_exists:
            pgi_display = "✅ Completed"
            pod_display = "Done"
            pending_display = "🟢 No"
        elif pgi_exists and not pod_exists:
            pgi_display = "✅ Completed"
            pod_display = "⏳ Pending"
            pending_display = "⚠️ Yes"
        else:
            pgi_display = "⏳ Pending"
            pod_display = "⏳ Pending"
            pending_display = "⚠️ Yes"
        
        lines.append("*📋 Status:*")
        lines.append("Delivery: {} {}".format(calculated_emoji, calculated_stage))
        lines.append("PGI: {}".format(pgi_display))
        lines.append("POD: {}".format(pod_display))
        lines.append("Pending: {}".format(pending_display))
        lines.append("")
        
        # Products
        products = data.get('products', [])
        if products:
            lines.append("*📦 Product Details:*")
            for idx, product in enumerate(products[:10], 1):
                model_name = product.get('name', 'Unknown')
                material_no = product.get('material_no', 'N/A')
                qty = product.get('qty', 0)
                
                lines.append("{}. {}: {} units".format(idx, model_name, qty))
                if material_no != 'N/A':
                    lines.append("   Material: {}".format(material_no))
            
            if len(products) > 10:
                remaining = len(products) - 10
                total_units_remaining = sum(p.get('qty', 0) for p in products[10:])
                lines.append("... and {} more models ({} units)".format(remaining, total_units_remaining))
            lines.append("")
        
        return "\n".join(lines)

# ==========================================================
# BLOCK 14: REGRESSION TESTS
# ==========================================================

def test_date_calculation(self) -> Dict[str, Any]:
    """Regression tests for date calculations."""
    from datetime import date as date_type
    
    test_results = []
    all_passed = True
    
    # Test 1
    tc1_dn_create = date_type(2026, 5, 5)
    tc1_pgi = date_type(2026, 5, 7)
    tc1_pod = date_type(2026, 5, 25)
    
    tc1_delivery = self.calculate_delivery_aging(tc1_dn_create, tc1_pgi)
    tc1_pod_aging = self.calculate_pod_aging(tc1_pgi, tc1_pod)
    tc1_total = self.calculate_total_cycle(tc1_dn_create, tc1_pod)
    
    tc1_passed = (tc1_delivery == 2 and tc1_pod_aging == 18 and tc1_total == 20)
    if not tc1_passed:
        all_passed = False
    
    test_results.append({
        "name": "Test 1: 2026-05-05, 2026-05-07, 2026-05-25",
        "expected": {"delivery": 2, "pod": 18, "total": 20},
        "actual": {"delivery": tc1_delivery, "pod": tc1_pod_aging, "total": tc1_total},
        "passed": tc1_passed
    })
    
    # Test 2
    tc2_dn_create = date_type(2026, 5, 23)
    tc2_pgi = date_type(2026, 5, 24)
    tc2_pod = date_type(2026, 5, 25)
    
    tc2_delivery = self.calculate_delivery_aging(tc2_dn_create, tc2_pgi)
    tc2_pod_aging = self.calculate_pod_aging(tc2_pgi, tc2_pod)
    tc2_total = self.calculate_total_cycle(tc2_dn_create, tc2_pod)
    
    tc2_passed = (tc2_delivery == 1 and tc2_pod_aging == 1 and tc2_total == 2)
    if not tc2_passed:
        all_passed = False
    
    test_results.append({
        "name": "Test 2: 2026-05-23, 2026-05-24, 2026-05-25",
        "expected": {"delivery": 1, "pod": 1, "total": 2},
        "actual": {"delivery": tc2_delivery, "pod": tc2_pod_aging, "total": tc2_total},
        "passed": tc2_passed
    })
    
    # Build result
    result = {
        "test_name": "Regression Tests - Native PostgreSQL Dates",
        "date_policy": "YYYY-MM-DD (Native PostgreSQL)",
        "tests": test_results,
        "all_passed": all_passed,
        "total_tests": len(test_results),
        "passed_tests": sum(1 for t in test_results if t.get("passed", False)),
        "timestamp": datetime.now().isoformat()
    }
    
    return result

# ==========================================================
# BLOCK 15: DISTANCE CALCULATOR
# ==========================================================

class DistanceCalculator:
    """Simple distance calculator with fallbacks."""
    
    def __init__(self):
        self._cache = {}
        self._geolocator = None
        self._client = None
        
        if _lazy_load_geo():
            try:
                from geopy.geocoders import Nominatim
                import openrouteservice
                
                if OPENROUTE_API_KEY:
                    self._client = openrouteservice.Client(key=OPENROUTE_API_KEY)
                self._geolocator = Nominatim(user_agent="haier-logistics-agent")
            except Exception as e:
                if not PRODUCTION_MODE:
                    logger.warning(f"⚠️ GIS initialization failed: {e}")
    
    def get_coordinates(self, location: str) -> Optional[Tuple[float, float]]:
        if not location or not self._geolocator:
            return None
        
        cache_key = location.lower().strip()
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        try:
            result = self._geolocator.geocode(location, timeout=10)
            if result:
                coords = (result.latitude, result.longitude)
                self._cache[cache_key] = coords
                return coords
            
            result = self._geolocator.geocode(f"{location}, Pakistan", timeout=10)
            if result:
                coords = (result.latitude, result.longitude)
                self._cache[cache_key] = coords
                return coords
            
            return None
        except Exception:
            return None
    
    def calculate_distance(self, origin: str, destination: str) -> Dict[str, Any]:
        result = {
            "distance_km": 0,
            "duration_text": "Unknown",
            "source": "unknown"
        }
        
        if not origin or not destination:
            return result
        
        if self._client:
            try:
                origin_coords = self.get_coordinates(origin)
                dest_coords = self.get_coordinates(destination)
                
                if origin_coords and dest_coords:
                    coords = [[origin_coords[1], origin_coords[0]], [dest_coords[1], dest_coords[0]]]
                    routes = self._client.directions(
                        coordinates=coords,
                        profile='driving-car',
                        format='json'
                    )
                    
                    if routes and routes.get('features'):
                        feature = routes['features'][0]
                        segments = feature.get('properties', {}).get('segments', [])
                        if segments:
                            segment = segments[0]
                            distance_km = segment.get('distance', 0) / 1000
                            duration_sec = segment.get('duration', 0)
                            
                            result = {
                                'distance_km': round(distance_km, 1),
                                'duration_text': self._format_duration(duration_sec),
                                'source': 'openrouteservice'
                            }
                            return result
            except Exception as e:
                if not PRODUCTION_MODE:
                    logger.warning(f"⚠️ OpenRouteService failed: {e}")
        
        if self._geolocator:
            try:
                from geopy.distance import geodesic
                origin_coords = self.get_coordinates(origin)
                dest_coords = self.get_coordinates(destination)
                
                if origin_coords and dest_coords:
                    distance_km = geodesic(origin_coords, dest_coords).kilometers
                    duration_hours = distance_km / 60
                    duration_sec = duration_hours * 3600
                    
                    result = {
                        'distance_km': round(distance_km, 1),
                        'duration_text': self._format_duration(duration_sec),
                        'source': 'geopy_approximate'
                    }
                    return result
            except Exception:
                pass
        
        distance_km = self._estimate_distance(origin, destination)
        if distance_km > 0:
            duration_hours = distance_km / 60
            duration_sec = duration_hours * 3600
            
            result = {
                'distance_km': round(distance_km, 1),
                'duration_text': self._format_duration(duration_sec),
                'source': 'estimated'
            }
        
        return result
    
    def _format_duration(self, seconds: int) -> str:
        if seconds < 60:
            return "Less than 1 minute"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes} minute{'s' if minutes > 1 else ''}"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            if minutes == 0:
                return f"{hours} hour{'s' if hours > 1 else ''}"
            return f"{hours}h {minutes}m"
        else:
            days = int(seconds / 86400)
            hours = int((seconds % 86400) / 3600)
            return f"{days}d {hours}h"
    
    def _estimate_distance(self, origin: str, destination: str) -> float:
        city_distances = {
            ("rawalpindi", "abbottabad"): 70,
            ("rawalpindi", "attock"): 90,
            ("rawalpindi", "hassanabdal"): 50,
            ("rawalpindi", "wah cantt"): 50,
            ("rawalpindi", "islamabad"): 20,
            ("rawalpindi", "peshawar"): 170,
        }
        
        origin_key = origin.lower().strip()
        dest_key = destination.lower().strip()
        
        key = (origin_key, dest_key)
        if key in city_distances:
            return city_distances[key]
        
        key_rev = (dest_key, origin_key)
        if key_rev in city_distances:
            return city_distances[key_rev]
        
        return 0

# ==========================================================
# BLOCK 16: THREAD-SAFE SINGLETON
# ==========================================================

_dn_analytics_service = None
_dn_lock = threading.Lock()


def get_dn_analytics_service() -> DNAnalysisService:
    """Thread-safe singleton getter."""
    global _dn_analytics_service
    
    if _dn_analytics_service is None:
        with _dn_lock:
            if _dn_analytics_service is None:
                try:
                    if not PRODUCTION_MODE:
                        logger.info("🔧 Creating DNAnalysisService singleton...")
                    _dn_analytics_service = DNAnalysisService()
                    if not PRODUCTION_MODE:
                        logger.info("✅ DNAnalysisService singleton initialized")
                except Exception as e:
                    logger.exception(f"❌ DNAnalysisService initialization failed: {e}")
                    raise
    
    return _dn_analytics_service


# ==========================================================
# BLOCK 17: EXPORTS
# ==========================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service'
]


# ==========================================================
# BLOCK 18: MODULE INITIALIZATION
# ==========================================================

if not PRODUCTION_MODE:
    logger.info("=" * 70)
    logger.info("DNAnalysisService v12.1 - DN SEARCH FIX")
    logger.info("=" * 70)
    logger.info("")
    logger.info("   ENTERPRISE FEATURES:")
    logger.info("   ✅ Robust DN search with 5 fallback strategies")
    logger.info("   ✅ Index-friendly SQL with wildcard support")
    logger.info("   ✅ Flexible length validation (5+ digits)")
    logger.info("   ✅ Partial match and suffix search")
    logger.info("   ✅ 100% backward compatible")
    logger.info("")
    logger.info("   SEARCH STRATEGIES:")
    logger.info("   1. Primary optimized query (exact + patterns)")
    logger.info("   2. Raw DN fallback")
    logger.info("   3. Multiple pattern fallback")
    logger.info("   4. Similar DNs with suffix matching")
    logger.info("   5. Direct database check")
    logger.info("")
    logger.info("   STATUS: ✅ PRODUCTION READY")
    logger.info("=" * 70)

# Run regression tests
try:
    service = get_dn_analytics_service()
    test_result = service.test_date_calculation()
    if test_result.get("all_passed"):
        if not PRODUCTION_MODE:
            logger.info("✅ Regression Tests: ALL PASSED")
    else:
        logger.warning("⚠️ Regression Tests: SOME FAILED")
except Exception as e:
    logger.error(f"❌ Regression Tests failed: {e}")
