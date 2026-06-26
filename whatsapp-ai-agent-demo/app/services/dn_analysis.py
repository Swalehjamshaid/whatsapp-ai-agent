# ==========================================================
# FILE: app/services/dn_analysis.py (v11.0 - ENTERPRISE SAFE UPGRADE)
# ==========================================================
# PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
# SOURCE: delivery_reports table ONLY
# VERSION: 11.0 - ENTERPRISE SAFE UPGRADE
#
# COMPATIBLE WITH: ai_provider_service.py v5.0
# INTEGRATION: Railway PostgreSQL
#
# SAFE UPGRADE RULES:
# - ✅ DN EXTRACTION PIPELINE IS LOCKED (DO NOT MODIFY)
# - ✅ Only ADDING missing information
# - ✅ Only FIXING metrics aggregation
# - ✅ Only APPENDING new sections
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

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS & DATABASE SETUP (UNCHANGED)
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

# Distance libraries (optional - graceful fallback)
GEO_AVAILABLE = False
try:
    import openrouteservice
    from geopy.geocoders import Nominatim
    from geopy.distance import geodesic
    GEO_AVAILABLE = True
    logger.info("✅ GIS libraries available")
except ImportError:
    logger.warning("⚠️ GIS libraries not available. Distance features will use estimation.")

OPENROUTE_API_KEY = os.environ.get("OPENROUTE_API_KEY", "")

# ==========================================================
# BLOCK 2: DNAnalysisService CLASS
# ==========================================================

class DNAnalysisService:
    """
    DN Analytics Service - Direct PostgreSQL Connection.
    
    This service connects directly to PostgreSQL without any repository layer.
    All data comes from delivery_reports table.
    
    DATE POLICY (v11.0):
    - PostgreSQL DATE values are used AS-IS
    - No YYYY-DD-MM conversion
    - Native datetime arithmetic for aging calculations
    - Full raw PostgreSQL verification
    - Database consistency checks
    - Modular validation, formatting, aging, dashboard
    
    COMPATIBLE WITH: ai_provider_service.py v5.0
    """
    
    def __init__(self):
        """Initialize DN Analytics Service."""
        self._service_name = "dn_analysis"
        self._version = "11.0"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0
        self._startup_time = datetime.now().isoformat()
        self._debug_mode = DEBUG_MODE
        
        # Initialize distance calculator (NEW - non-breaking)
        self._distance_calculator = None
        if GEO_AVAILABLE:
            try:
                self._distance_calculator = DistanceCalculator()
                logger.info("✅ DistanceCalculator initialized")
            except Exception as e:
                logger.warning(f"⚠️ DistanceCalculator initialization failed: {e}")
        
        logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
        logger.info(f"📋 Debug Mode: {'ENABLED' if self._debug_mode else 'DISABLED'}")
        logger.info("📋 Date Policy: Native PostgreSQL DATE values (YYYY-MM-DD)")
        logger.info("📋 No month/day swapping")
        logger.info("📋 Native datetime arithmetic")
        
        # Test connection
        test_result = self._test_connection()
        if test_result:
            self._status = "READY"
            logger.info("✅ DNAnalysisService is READY")
        else:
            self._status = "ERROR"
            logger.error("❌ DNAnalysisService initialization FAILED")
    
    # ==========================================================
    # BLOCK 3: DATABASE CONNECTION METHODS
    # ==========================================================
    
    def _test_connection(self) -> bool:
        """Test database connection."""
        session = None
        try:
            if not SessionLocal:
                logger.error("❌ SessionLocal is None")
                return False
            
            session = SessionLocal()
            session.execute(text("SELECT 1"))
            logger.info("✅ Database connection test: SUCCESS")
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
            
            logger.debug(f"📝 Executing SQL: {query[:200]}...")
            logger.debug(f"📝 Parameters: {params}")
            
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            
            execution_time_ms = (time.time() - start_time) * 1000
            self._query_count += 1
            self._total_execution_time_ms += execution_time_ms
            
            logger.debug(f"✅ Query returned {len(rows)} rows in {execution_time_ms:.2f}ms")
            return rows
            
        except Exception as e:
            logger.error(f"❌ SQL Execution Failed!")
            logger.error(f"   Query: {query[:500]}")
            logger.error(f"   Parameters: {params}")
            logger.error(f"   Error: {str(e)}")
            logger.error(f"   Traceback:\n{traceback.format_exc()}")
            return []
        finally:
            if session:
                session.close()
    
    # ==========================================================
    # BLOCK 4: DN SEARCH NORMALIZATION
    # ==========================================================
    
    def _normalize_dn(self, dn_no: str) -> str:
        """Normalize DN number for search - removes non-numeric characters."""
        if not dn_no:
            return ""
        normalized = re.sub(r'[^0-9]', '', dn_no.strip())
        logger.info(f"🔍 DN Normalization: '{dn_no}' → '{normalized}'")
        return normalized
    
    def _build_normalized_dn_query(self) -> str:
        """Build DN query with multiple matching strategies."""
        return """
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
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                COUNT(DISTINCT customer_model) AS model_count,
                COUNT(DISTINCT material_no) AS material_count,
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
                MAX(imported_at) AS imported_at,
                COUNT(*) AS material_count
            FROM delivery_reports
            WHERE 
                CAST(dn_no AS TEXT) = :dn_no
                OR CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
                OR REPLACE(CAST(dn_no AS TEXT), '-', '') = :dn_no
                OR REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
            GROUP BY dn_no
            LIMIT 1
        """
    
    def _build_product_details_query(self) -> str:
        """Build query for product details (NEW - non-breaking)."""
        return """
            SELECT 
                customer_model AS model_name,
                material_no AS material_number,
                division,
                SUM(dn_qty) AS quantity,
                SUM(dn_amount) AS revenue,
                COUNT(*) AS item_count
            FROM delivery_reports
            WHERE 
                CAST(dn_no AS TEXT) = :dn_no
                OR CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
                OR REPLACE(CAST(dn_no AS TEXT), '-', '') = :dn_no
                OR REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
            GROUP BY customer_model, material_no, division
            ORDER BY quantity DESC
            LIMIT 20
        """
    
    def _build_exact_match_query(self) -> str:
        """Build exact match query for diagnostic purposes."""
        return """
            SELECT *
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
            LIMIT 1
        """
    
    def _build_count_query(self) -> str:
        """Build count query for diagnostic purposes."""
        return """
            SELECT COUNT(*) as count
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
        """
    
    def _build_fallback_dn_query(self) -> str:
        """Build fallback DN query for partial matches."""
        return """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
    
    def _build_raw_dn_query(self) -> str:
        """Build raw DN query to check if DN exists without normalization."""
        return """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
    
    # ==========================================================
    # BLOCK 5: HEALTH & VALIDATION METHODS
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Validate service readiness."""
        logger.info("🔍 Running health check...")
        session = None
        
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
            
            session = SessionLocal()
            try:
                session.execute(text("SELECT 1"))
                result["database"] = "connected"
                logger.info("✅ Database connection: connected")
            except Exception as e:
                result["errors"].append(f"Connection failed: {str(e)}")
                logger.error(f"❌ Database connection failed: {e}")
                return result
            
            try:
                inspector = inspect(session.bind)
                tables = inspector.get_table_names()
                if "delivery_reports" not in tables:
                    result["errors"].append("Table 'delivery_reports' does not exist")
                    logger.error("❌ Table 'delivery_reports' not found")
                    return result
                logger.info("✅ Table 'delivery_reports' exists")
            except Exception as e:
                result["errors"].append(f"Table check failed: {str(e)}")
                logger.error(f"❌ Table check failed: {e}")
                return result
            
            try:
                required_columns = [
                    "dn_no", "customer_name", "dealer_code", "customer_code",
                    "warehouse", "warehouse_code", "ship_to_city", "delivery_location",
                    "dn_qty", "dn_amount", "dn_create_date", "good_issue_date",
                    "pod_date", "delivery_status", "pgi_status", "pod_status",
                    "pending_flag"
                ]
                columns_info = inspector.get_columns("delivery_reports")
                columns = [col["name"] for col in columns_info]
                
                logger.info("📊 PostgreSQL Column Types:")
                for col in columns_info:
                    logger.info(f"   ├── {col['name']}: {col['type']}")
                
                missing = [col for col in required_columns if col not in columns]
                
                if missing:
                    result["warnings"].append(f"Missing columns: {missing}")
                    logger.warning(f"⚠️ Missing columns: {missing}")
                else:
                    logger.info("✅ Required columns exist")
            except Exception as e:
                result["errors"].append(f"Column check failed: {str(e)}")
                logger.error(f"❌ Column check failed: {e}")
                return result
            
            try:
                test_query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports LIMIT 1"
                session.execute(text(test_query))
                logger.info("✅ Test query executed successfully")
            except Exception as e:
                result["errors"].append(f"Test query failed: {str(e)}")
                logger.error(f"❌ Test query failed: {e}")
                return result
            
            result["healthy"] = True
            result["database"] = "connected"
            self._status = "READY"
            
            logger.info("✅ Health check PASSED - Service is READY")
            return result
            
        except Exception as e:
            result["errors"].append(f"Health check failed: {str(e)}")
            logger.error(f"❌ Health check failed: {e}")
            return result
        finally:
            if session:
                session.close()
    
    def validation_query(self) -> Dict[str, Any]:
        """Used by ai_provider_service.py for validation."""
        logger.info("🔍 Running validation query...")
        session = None
        
        result = {
            "success": False,
            "records": 0,
            "error": None
        }
        
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
        logger.info("🔍 Returning service metadata...")
        
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": self._status,
            "module": "DN Analytics",
            "description": "DN Analytics Service - Native PostgreSQL Date Handling",
            "date_policy": "Native PostgreSQL DATE values (YYYY-MM-DD)",
            "debug_mode": self._debug_mode,
            "methods": [
                "health_check",
                "validation_query",
                "get_service_metadata",
                "search_dn",
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
    # BLOCK 6: DATE VALIDATOR
    # ==========================================================
    
    def _validate_postgresql_date(self, date_value, field_name: str = "date") -> Dict[str, Any]:
        """
        CENTRAL DATE VALIDATOR - Single source of truth for date validation.
        
        Responsibilities:
        - Verify object type
        - Reject invalid strings (with warning)
        - Only allow YYYY-MM-DD format
        - Log warnings for string inputs (should be date objects)
        
        Args:
            date_value: Date from PostgreSQL
            field_name: Name of the field for logging
            
        Returns:
            Dict with validation results
        """
        result = {
            "valid": False,
            "value": None,
            "type": "unknown",
            "formatted": "N/A",
            "error": None,
            "field": field_name
        }
        
        # Check for None
        if date_value is None:
            result["error"] = "NULL value"
            result["type"] = "NoneType"
            logger.warning(f"⚠️ {field_name}: NULL value received")
            return result
        
        # Check type - Date object (preferred)
        if isinstance(date_value, date) and not isinstance(date_value, datetime):
            result["type"] = "date"
            result["value"] = date_value
            result["formatted"] = date_value.strftime('%Y-%m-%d')
            result["valid"] = True
            if self._debug_mode:
                logger.debug(f"✅ {field_name}: Valid date object {result['formatted']} (type: date)")
            return result
        
        # Check type - Datetime object
        elif isinstance(date_value, datetime):
            result["type"] = "datetime"
            result["value"] = date_value
            result["formatted"] = date_value.strftime('%Y-%m-%d')
            result["valid"] = True
            if self._debug_mode:
                logger.debug(f"✅ {field_name}: Valid datetime object {result['formatted']} (type: datetime)")
            return result
        
        # Check type - String (should not happen from PostgreSQL)
        elif isinstance(date_value, str):
            result["type"] = "string"
            
            # Log warning - PostgreSQL should return date objects
            logger.warning(
                f"⚠️ {field_name}: Expected PostgreSQL DATE object but received string: '{date_value}'"
            )
            
            # Only accept YYYY-MM-DD format
            parts = date_value.split('-')
            if len(parts) == 3:
                try:
                    year = int(parts[0])
                    month = int(parts[1])
                    day = int(parts[2])
                    
                    # Validate year, month, day ranges
                    if year < 1:
                        result["error"] = f"Invalid year: {year}"
                        logger.warning(f"⚠️ {field_name}: Invalid year {year}")
                        return result
                    if month < 1 or month > 12:
                        result["error"] = f"Invalid month: {month}"
                        logger.warning(f"⚠️ {field_name}: Invalid month {month}")
                        return result
                    if day < 1 or day > 31:
                        result["error"] = f"Invalid day: {day}"
                        logger.warning(f"⚠️ {field_name}: Invalid day {day}")
                        return result
                    
                    parsed = datetime(year, month, day)
                    result["value"] = parsed
                    result["formatted"] = parsed.strftime('%Y-%m-%d')
                    result["valid"] = True
                    logger.info(f"✅ {field_name}: Parsed string date {result['formatted']} (format: YYYY-MM-DD)")
                    return result
                    
                except ValueError as e:
                    result["error"] = f"Invalid date components: {date_value} - {e}"
                    logger.warning(f"⚠️ {field_name}: {result['error']}")
                    return result
            else:
                # Check for other formats and reject
                if '.' in date_value:
                    result["error"] = f"Invalid format (contains .): {date_value} - expected YYYY-MM-DD"
                elif '/' in date_value:
                    result["error"] = f"Invalid format (contains /): {date_value} - expected YYYY-MM-DD"
                else:
                    result["error"] = f"Invalid format: {date_value} - expected YYYY-MM-DD"
                
                logger.error(f"❌ {field_name}: {result['error']}")
                return result
        
        # Unsupported type
        else:
            result["error"] = f"Unsupported type: {type(date_value)}"
            logger.warning(f"⚠️ {field_name}: {result['error']}")
            return result
    
    # ==========================================================
    # BLOCK 6.1: DATE FORMATTER
    # ==========================================================
    
    def _format_display_date(self, date_value) -> str:
        """
        Format PostgreSQL date for display (YYYY-MM-DD).
        
        ✅ ONLY formats, does NOT parse.
        ✅ Preserves original PostgreSQL format.
        ✅ No month/day swapping.
        """
        if date_value is None:
            return 'N/A'
        
        try:
            if isinstance(date_value, (date, datetime)):
                return date_value.strftime('%Y-%m-%d')
            elif isinstance(date_value, str):
                # If already in YYYY-MM-DD format, return as-is
                if len(date_value) == 10 and date_value[4] == '-' and date_value[7] == '-':
                    return date_value
                # Try to parse and reformat
                parsed = datetime.strptime(date_value, "%Y-%m-%d")
                return parsed.strftime('%Y-%m-%d')
            else:
                return str(date_value)
        except (ValueError, TypeError) as e:
            logger.warning(f"⚠️ Failed to format display date: {date_value} - {e}")
            return str(date_value) if date_value else 'N/A'
    
    def _format_date_dmy_long(self, date_value) -> str:
        """Format datetime → DD Month YYYY for display."""
        if not date_value:
            return 'N/A'
        
        try:
            parsed = self._parse_date(date_value)
            if parsed:
                return parsed.strftime('%-d %B %Y')
            return str(date_value)
        except Exception as e:
            logger.warning(f"⚠️ Date formatting error: {e}")
            return 'N/A'
    
    def _format_date_dmy_short(self, date_value) -> str:
        """Format datetime → DD-MMM-YY for display."""
        if not date_value:
            return 'N/A'
        
        try:
            parsed = self._parse_date(date_value)
            if parsed:
                return parsed.strftime('%-d-%b-%y')
            return str(date_value)
        except Exception as e:
            logger.warning(f"⚠️ Date formatting error: {e}")
            return 'N/A'
    
    def _parse_date(self, date_value):
        """Parse PostgreSQL date WITHOUT any conversion."""
        if not date_value:
            return None
        
        validation_result = self._validate_postgresql_date(date_value, "parse_date")
        if validation_result["valid"]:
            return validation_result["value"]
        else:
            logger.error(f"❌ Date validation failed: {validation_result['error']}")
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
    
    # ==========================================================
    # BLOCK 6.2: DATE INTEGRITY CHECK
    # ==========================================================
    
    def _check_date_integrity(self, dn_no: str, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        """
        Check date integrity before aging calculations.
        
        Verifies: DN Create <= PGI <= POD
        
        Logs detailed errors for any inconsistency.
        """
        result = {
            "valid": True,
            "warnings": [],
            "errors": [],
            "dn_create": self._format_display_date(dn_create_date),
            "pgi": self._format_display_date(good_issue_date),
            "pod": self._format_display_date(pod_date)
        }
        
        # Check if DN Create is after PGI
        if dn_create_date and good_issue_date:
            if dn_create_date > good_issue_date:
                result["valid"] = False
                msg = f"DN Create ({self._format_display_date(dn_create_date)}) > PGI ({self._format_display_date(good_issue_date)})"
                result["errors"].append(msg)
                logger.error(f"❌ DN {dn_no}: {msg}")
        
        # Check if PGI is after POD
        if good_issue_date and pod_date:
            if good_issue_date > pod_date:
                result["valid"] = False
                msg = f"PGI ({self._format_display_date(good_issue_date)}) > POD ({self._format_display_date(pod_date)})"
                result["errors"].append(msg)
                logger.error(f"❌ DN {dn_no}: {msg}")
        
        # Check if DN Create is after POD
        if dn_create_date and pod_date:
            if dn_create_date > pod_date:
                result["valid"] = False
                msg = f"DN Create ({self._format_display_date(dn_create_date)}) > POD ({self._format_display_date(pod_date)})"
                result["errors"].append(msg)
                logger.error(f"❌ DN {dn_no}: {msg}")
        
        if result["valid"]:
            logger.info(f"✅ DN {dn_no}: Date integrity check PASSED")
            logger.info(f"   DN Create: {result['dn_create']} <= PGI: {result['pgi']} <= POD: {result['pod']}")
        else:
            logger.error(f"❌ DN {dn_no}: Date integrity check FAILED")
            for error in result["errors"]:
                logger.error(f"   ❌ {error}")
        
        return result
    
    # ==========================================================
    # BLOCK 6.3: RAW POSTGRESQL VERIFICATION
    # ==========================================================
    
    def _log_raw_postgresql_values(self, data: Dict[str, Any], dn_no: str) -> None:
        """
        Log raw PostgreSQL values before any processing.
        
        This is the HIGHEST PRIORITY check - it immediately tells us
        whether the database or the service is responsible for any issues.
        """
        logger.info("=" * 70)
        logger.info(f"📊 RAW POSTGRESQL VALUES for DN {dn_no}")
        logger.info("=" * 70)
        logger.info(f"DN: {data.get('dn_no')}")
        logger.info("")
        
        # DN Create Date
        dn_create = data.get('dn_create_date')
        logger.info(f"DN Create: {dn_create!r} ({type(dn_create).__name__})")
        
        # PGI Date
        pgi = data.get('good_issue_date')
        logger.info(f"PGI:       {pgi!r} ({type(pgi).__name__})")
        
        # POD Date
        pod = data.get('pod_date')
        logger.info(f"POD:       {pod!r} ({type(pod).__name__})")
        logger.info("=" * 70)
        
        # Validate each date
        for field, value in [("dn_create_date", dn_create), 
                            ("good_issue_date", pgi), 
                            ("pod_date", pod)]:
            if value is not None:
                if not isinstance(value, (date, datetime)):
                    logger.warning(f"⚠️ {field}: Expected date object, got {type(value).__name__}: {value!r}")
    
    # ==========================================================
    # BLOCK 6.4: AGING CALCULATOR
    # ==========================================================
    
    def _safe_date_diff(self, date1, date2) -> int:
        """Safely calculate days between two dates using native date subtraction."""
        if date1 is None or date2 is None:
            return 0
        
        try:
            if not isinstance(date1, (date, datetime)):
                logger.warning(f"⚠️ Invalid date1 type: {type(date1)}")
                return 0
            if not isinstance(date2, (date, datetime)):
                logger.warning(f"⚠️ Invalid date2 type: {type(date2)}")
                return 0
            
            if isinstance(date1, datetime):
                date1 = date1.date()
            if isinstance(date2, datetime):
                date2 = date2.date()
            
            delta = date2 - date1
            days = delta.days
            return max(0, days)
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate date difference: {e}")
            return 0
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        """Calculate delivery aging using native PostgreSQL dates."""
        try:
            if dn_create_date is None:
                logger.warning("⚠️ DN Create Date Missing - Returning 0")
                return 0
            
            dn_date = self._parse_date(dn_create_date)
            if dn_date is None:
                logger.warning("⚠️ Failed to parse DN Create date - Returning 0")
                return 0
            
            if good_issue_date is None:
                logger.info("📊 Delivery Aging: PGI missing - Using Current Date")
                current_date = datetime.now().date()
                days = self._safe_date_diff(dn_date, current_date)
                logger.info(f"✅ Delivery Aging (Current Date): {days} days")
                return days
            
            gi_date = self._parse_date(good_issue_date)
            if gi_date is None:
                logger.warning("⚠️ Failed to parse PGI date - Returning 0")
                return 0
            
            days = self._safe_date_diff(dn_date, gi_date)
            
            logger.info(
                f"✅ Delivery Aging: "
                f"DN Create: {self._format_display_date(dn_create_date)} → "
                f"PGI: {self._format_display_date(good_issue_date)} = {days} days"
            )
            return days
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate delivery aging: {e}")
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return 0
    
    def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
        """Calculate POD aging using native PostgreSQL dates."""
        try:
            if good_issue_date is None:
                logger.info("📊 POD Aging: PGI missing - Cannot calculate")
                return 0
            
            gi_date = self._parse_date(good_issue_date)
            if gi_date is None:
                logger.warning("⚠️ Failed to parse PGI date - Returning 0")
                return 0
            
            if pod_date is None:
                logger.info("📊 POD Aging: POD missing - Using Current Date")
                current_date = datetime.now().date()
                days = self._safe_date_diff(gi_date, current_date)
                logger.info(f"✅ POD Aging (Current Date): {days} days")
                return days
            
            pd_date = self._parse_date(pod_date)
            if pd_date is None:
                logger.warning("⚠️ Failed to parse POD date - Returning 0")
                return 0
            
            days = self._safe_date_diff(gi_date, pd_date)
            
            logger.info(
                f"✅ POD Aging: "
                f"PGI: {self._format_display_date(good_issue_date)} → "
                f"POD: {self._format_display_date(pod_date)} = {days} days"
            )
            return days
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate POD aging: {e}")
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return 0
    
    def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
        """Calculate total cycle using native PostgreSQL dates."""
        try:
            if dn_create_date is None:
                logger.warning("⚠️ DN Create Date Missing - Returning 0")
                return 0
            
            dn_date = self._parse_date(dn_create_date)
            if dn_date is None:
                logger.warning("⚠️ Failed to parse DN Create date - Returning 0")
                return 0
            
            if pod_date is None:
                logger.info("📊 Total Cycle: POD missing - Using Current Date")
                current_date = datetime.now().date()
                days = self._safe_date_diff(dn_date, current_date)
                logger.info(f"✅ Total Cycle (Current Date): {days} days")
                return days
            
            pd_date = self._parse_date(pod_date)
            if pd_date is None:
                logger.warning("⚠️ Failed to parse POD date - Returning 0")
                return 0
            
            days = self._safe_date_diff(dn_date, pd_date)
            
            logger.info(
                f"✅ Total Cycle: "
                f"DN Create: {self._format_display_date(dn_create_date)} → "
                f"POD: {self._format_display_date(pod_date)} = {days} days"
            )
            return days
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate total cycle: {e}")
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return 0
    
    # ==========================================================
    # BLOCK 6.5: DISTANCE CALCULATOR (NEW - NON-BREAKING)
    # ==========================================================
    
    class DistanceCalculator:
        """Simple distance calculator with fallbacks."""
        
        def __init__(self):
            self._cache = {}
            self._geolocator = None
            self._client = None
            
            if GEO_AVAILABLE:
                try:
                    if OPENROUTE_API_KEY:
                        self._client = openrouteservice.Client(key=OPENROUTE_API_KEY)
                        logger.info("✅ OpenRouteService initialized")
                    self._geolocator = Nominatim(user_agent="haier-logistics-agent")
                    logger.info("✅ Geopy initialized")
                except Exception as e:
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
            
            # Try OpenRouteService
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
                    logger.warning(f"⚠️ OpenRouteService failed: {e}")
            
            # Try geopy
            if self._geolocator:
                try:
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
            
            # Estimate distance
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
    # BLOCK 7: DN SEARCH
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """Search for a specific DN with multiple matching strategies."""
        logger.info(f"🔍 Searching for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        logger.info(f"   ├── Normalized: '{normalized_dn}'")
        logger.info(f"   ├── Length: {len(normalized_dn)}")
        
        if len(normalized_dn) < 8:
            return {"success": False, "error": f"Invalid DN format: {normalized_dn} (must be 8-12 digits)"}
        
        query = self._build_normalized_dn_query()
        results = self._execute_query(query, {"dn_no": normalized_dn})
        
        logger.info(f"📊 DN Search | Input={dn_no} | Normalized={normalized_dn} | Results={len(results)}")
        
        if results:
            logger.info(f"✅ DN {dn_no} found with {results[0].get('material_count', 1)} materials")
            return {"success": True, "data": results[0]}
        
        count_query = self._build_count_query()
        count_results = self._execute_query(count_query, {"dn_no": normalized_dn})
        exact_count = count_results[0].get('count', 0) if count_results else 0
        logger.info(f"   ├── Exact match count: {exact_count}")
        
        if exact_count > 0:
            logger.info(f"   ├── Exact match found! Trying direct query...")
            exact_query = self._build_exact_match_query()
            exact_results = self._execute_query(exact_query, {"dn_no": normalized_dn})
            if exact_results:
                data = self._aggregate_dn_results(exact_results, normalized_dn)
                if data:
                    logger.info(f"✅ DN {dn_no} found via direct exact match")
                    return {"success": True, "data": data}
        
        logger.warning(f"⚠️ Primary match not found for {dn_no}. Running fallback search...")
        fallback_query = self._build_fallback_dn_query()
        fallback_results = self._execute_query(fallback_query, {"dn_no": normalized_dn})
        
        similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
        
        requested_dn_found = any(dn == normalized_dn or dn == dn_no for dn in similar_dns)
        
        if requested_dn_found:
            logger.info(f"   ├── Requested DN found in fallback! Auto-retrying with exact DN...")
            exact_query = self._build_exact_match_query()
            exact_results = self._execute_query(exact_query, {"dn_no": normalized_dn})
            if exact_results:
                data = self._aggregate_dn_results(exact_results, normalized_dn)
                if data:
                    logger.info(f"✅ DN {dn_no} found via fallback auto-retry")
                    return {"success": True, "data": data}
        
        if similar_dns:
            logger.info(f"   ├── Similar DNs found: {similar_dns[:5]}")
            return {
                "success": False,
                "error": f"DN {dn_no} not found",
                "similar_dns": similar_dns[:5],
                "message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
            }
        
        logger.warning(f"❌ DN {dn_no} not found - no similar matches")
        return {"success": False, "error": f"DN {dn_no} not found"}
    
    def _aggregate_dn_results(self, results: List[Dict[str, Any]], dn_no: str) -> Optional[Dict[str, Any]]:
        """Aggregate raw DN results into a single dashboard record."""
        if not results:
            return None
        
        data = {
            "dn_no": dn_no,
            "dealer_name": results[0].get('customer_name', 'Unknown'),
            "customer_name": results[0].get('customer_name', 'Unknown'),
            "dealer_code": results[0].get('dealer_code'),
            "customer_code": results[0].get('customer_code'),
            "warehouse": results[0].get('warehouse'),
            "warehouse_code": results[0].get('warehouse_code'),
            "city": results[0].get('ship_to_city'),
            "delivery_location": results[0].get('delivery_location'),
            "sales_manager": results[0].get('sales_manager'),
            "sales_office": results[0].get('sales_office'),
            "division": results[0].get('division'),
            "total_units": sum(r.get('dn_qty', 0) or 0 for r in results),
            "total_revenue": sum(r.get('dn_amount', 0) or 0 for r in results),
            "dn_create_date": min((r.get('dn_create_date') for r in results if r.get('dn_create_date')), default=None),
            "good_issue_date": max((r.get('good_issue_date') for r in results if r.get('good_issue_date')), default=None),
            "pod_date": max((r.get('pod_date') for r in results if r.get('pod_date')), default=None),
            "delivery_status": results[0].get('delivery_status'),
            "pgi_status": results[0].get('pgi_status'),
            "pod_status": results[0].get('pod_status'),
            "pending_flag": results[0].get('pending_flag'),
            "material_count": len(results),
            "source_file": results[0].get('source_file'),
            "upload_batch_id": results[0].get('upload_batch_id'),
            "imported_at": results[0].get('imported_at'),
            "created_at": results[0].get('created_at'),
            "updated_at": results[0].get('updated_at'),
            "model_count": results[0].get('model_count', 0),
            "material_count_distinct": results[0].get('material_count', 0)
        }
        
        delivery_aging = self.calculate_delivery_aging(
            data.get('dn_create_date'),
            data.get('good_issue_date')
        )
        pod_aging = self.calculate_pod_aging(
            data.get('good_issue_date'),
            data.get('pod_date')
        )
        total_cycle = self.calculate_total_cycle(
            data.get('dn_create_date'),
            data.get('pod_date')
        )
        
        data['delivery_aging_days'] = delivery_aging
        data['pod_aging_days'] = pod_aging
        data['total_cycle_days'] = total_cycle
        
        return data
    
    # ==========================================================
    # BLOCK 8: VERIFY DN
    # ==========================================================
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        """Verify if DN exists using multiple matching strategies."""
        logger.info(f"🔍 Verifying DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "exists": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        logger.info(f"   ├── Normalized: '{normalized_dn}'")
        
        query = """
            SELECT COUNT(DISTINCT dn_no) as count 
            FROM delivery_reports 
            WHERE CAST(dn_no AS TEXT) = :dn_no
               OR CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
               OR REPLACE(CAST(dn_no AS TEXT), '-', '') = :dn_no
               OR REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
        """
        results = self._execute_query(query, {"dn_no": normalized_dn})
        exists = results and results[0].get('count', 0) > 0
        
        logger.info(f"✅ DN {dn_no} exists: {exists}")
        return {"success": True, "exists": exists}
    
    # ==========================================================
    # BLOCK 9: TEST DN LOOKUP
    # ==========================================================
    
    def test_dn_lookup(self, dn_no: str) -> Dict[str, Any]:
        """Test DN lookup with full diagnostics."""
        logger.info(f"🔬 Testing DN lookup: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        results = {
            "dn": dn_no,
            "normalized": normalized_dn,
            "exact_count": 0,
            "like_count": 0,
            "regex_count": 0,
            "matching_dns": [],
            "diagnostics": []
        }
        
        query1 = "SELECT COUNT(*) as count FROM delivery_reports WHERE CAST(dn_no AS TEXT) = :dn_no"
        r1 = self._execute_query(query1, {"dn_no": normalized_dn})
        results["exact_count"] = r1[0].get('count', 0) if r1 else 0
        results["diagnostics"].append(f"Exact match: {results['exact_count']}")
        
        query2 = "SELECT COUNT(*) as count FROM delivery_reports WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'"
        r2 = self._execute_query(query2, {"dn_no": normalized_dn})
        results["like_count"] = r2[0].get('count', 0) if r2 else 0
        results["diagnostics"].append(f"LIKE match: {results['like_count']}")
        
        query3 = """
            SELECT COUNT(*) as count 
            FROM delivery_reports 
            WHERE REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
        """
        r3 = self._execute_query(query3, {"dn_no": normalized_dn})
        results["regex_count"] = r3[0].get('count', 0) if r3 else 0
        results["diagnostics"].append(f"REGEXP match: {results['regex_count']}")
        
        query4 = self._build_fallback_dn_query()
        r4 = self._execute_query(query4, {"dn_no": normalized_dn})
        results["matching_dns"] = [str(r.get('dn_no', '')) for r in r4 if r.get('dn_no')]
        
        results["found"] = results["exact_count"] > 0 or results["like_count"] > 0
        results["diagnostics"].append(f"Total matching DNs: {len(results['matching_dns'])}")
        
        logger.info(f"✅ Test DN lookup complete: found={results['found']}")
        return {"success": True, "data": results}
    
    # ==========================================================
    # BLOCK 10: DN DASHBOARD - WITH ENHANCED DATA
    # ==========================================================
        # ==========================================================
    # BLOCK 10: DN DASHBOARD BUILDER (ENHANCED)
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete DN dashboard with enterprise analytics.
        """
        logger.info(f"📊 Building dashboard for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        # ==========================================================
        # STEP 1: QUERY POSTGRESQL (UNCHANGED)
        # ==========================================================
        
        search_result = self.search_dn(dn_no)
        
        if not search_result.get("success"):
            similar_dns = search_result.get("similar_dns", [])
            if similar_dns:
                return {
                    "success": False,
                    "error": f"DN {dn_no} not found. Similar: {', '.join(similar_dns[:3])}"
                }
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        data = search_result.get("data", {})
        
        # ==========================================================
        # STEP 2: GET RAW DATA (UNCHANGED)
        # ==========================================================
        
        raw_dn_create_date = data.get('dn_create_date')
        raw_good_issue_date = data.get('good_issue_date')
        raw_pod_date = data.get('pod_date')
        
        # ==========================================================
        # STEP 3: CALCULATE AGING (UNCHANGED)
        # ==========================================================
        
        delivery_aging = self.calculate_delivery_aging(
            raw_dn_create_date,
            raw_good_issue_date
        )
        pod_aging = self.calculate_pod_aging(
            raw_good_issue_date,
            raw_pod_date
        )
        total_cycle = self.calculate_total_cycle(
            raw_dn_create_date,
            raw_pod_date
        )
        
        # ==========================================================
        # STEP 4: FORMAT DATES (UNCHANGED)
        # ==========================================================
        
        formatted_dn_create = self._format_display_date(raw_dn_create_date)
        formatted_good_issue = self._format_display_date(raw_good_issue_date)
        formatted_pod = self._format_display_date(raw_pod_date)
        
        # ==========================================================
        # STEP 5: GET PRODUCT DETAILS (NEW - CRITICAL)
        # ==========================================================
        
        normalized_dn = self._normalize_dn(dn_no)
        product_query = self._build_product_details_query()
        product_results = self._execute_query(product_query, {"dn_no": normalized_dn})
        
        products = []
        total_units = 0
        total_revenue = 0
        
        for row in product_results:
            model_name = row.get('model_name')
            if model_name:
                qty = int(row.get('quantity', 0) or 0)
                revenue = float(row.get('revenue', 0) or 0)
                division = row.get('division', 'Unknown')
                material_no = row.get('material_number', 'N/A')
                
                products.append({
                    'name': str(model_name),
                    'material_no': str(material_no),
                    'division': str(division),
                    'qty': qty,
                    'revenue': revenue
                })
                total_units += qty
                total_revenue += revenue
        
        # ==========================================================
        # STEP 6: DETERMINE SHIPMENT STAGE FROM DATES (NEW)
        # ==========================================================
        
        pgi_exists = raw_good_issue_date is not None
        pod_exists = raw_pod_date is not None
        
        if pod_exists and pgi_exists:
            calculated_stage = "Delivered"
            calculated_emoji = "✅"
        elif pgi_exists and not pod_exists:
            calculated_stage = "In Transit"
            calculated_emoji = "🚚"
        else:
            calculated_stage = "Pending Dispatch"
            calculated_emoji = "⏳"
        
        # ==========================================================
        # STEP 7: BUILD DASHBOARD (ENHANCED)
        # ==========================================================
        
        # Get material count - use from data or calculate
        material_count = data.get('material_count', 1)
        model_count = len(products)
        
        dashboard = {
            # ==========================================================
            # EXISTING FIELDS (PRESERVED)
            # ==========================================================
            "dn_no": data.get('dn_no'),
            "dealer_name": data.get('dealer_name', 'Unknown'),
            "warehouse": data.get('warehouse', 'Unknown'),
            "city": data.get('city', 'Unknown'),
            "delivery_location": data.get('delivery_location'),
            "sales_manager": data.get('sales_manager'),
            "division": data.get('division'),
            
            "dn_create_date": formatted_dn_create,
            "good_issue_date": formatted_good_issue,
            "pod_date": formatted_pod,
            
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "total_cycle_days": total_cycle,
            "delivery_aging_text": self._format_aging_text(delivery_aging),
            "pod_aging_text": self._format_aging_text(pod_aging),
            "total_cycle_text": self._format_aging_text(total_cycle),
            
            # ==========================================================
            # ENHANCED METRICS (FIXED)
            # ==========================================================
            "total_units": total_units if total_units > 0 else data.get('total_units', 0),
            "total_revenue": total_revenue if total_revenue > 0 else data.get('total_revenue', 0),
            "material_count": material_count,
            "model_count": model_count,
            
            # ==========================================================
            # NEW FIELDS
            # ==========================================================
            "products": products,
            "calculated_stage": calculated_stage,
            "calculated_emoji": calculated_emoji,
            
            # Raw dates for status determination
            "_dn_create_date": raw_dn_create_date,
            "_good_issue_date": raw_good_issue_date,
            "_pod_date": raw_pod_date,
            
            # Additional info
            "dealer_code": data.get('dealer_code'),
            "customer_code": data.get('customer_code'),
            "warehouse_code": data.get('warehouse_code'),
            "sales_office": data.get('sales_office'),
            "source_file": data.get('source_file'),
            "upload_batch_id": data.get('upload_batch_id'),
            "imported_at": data.get('imported_at'),
            "created_at": data.get('created_at'),
            "updated_at": data.get('updated_at'),
            
            # Status fields (for backward compatibility)
            "delivery_status": data.get('delivery_status', 'Unknown'),
            "pgi_status": data.get('pgi_status', 'Unknown'),
            "pod_status": data.get('pod_status', 'Unknown'),
            "pending_flag": data.get('pending_flag', False),
        }
        
        logger.info(f"✅ Dashboard built for DN {dn_no}")
        return {"success": True, "data": dashboard}
    # ==========================================================
    # BLOCK 10.1: HELPER METHODS FOR DASHBOARD BUILDER
    # ==========================================================
    
    def _estimate_delivery_eta(self, distance_km: float) -> str:
        """Estimate delivery ETA based on distance."""
        if distance_km <= 0:
            return "Unknown"
        elif distance_km <= 100:
            return "Same Day"
        elif distance_km <= 250:
            return "1 Day"
        elif distance_km <= 450:
            return "2 Days"
        elif distance_km <= 700:
            return "3 Days"
        else:
            return "4+ Days"
    
    def _calculate_sla(self, actual_days: int, expected_days: int) -> Dict[str, Any]:
        """Calculate SLA performance."""
        if expected_days <= 0:
            return {"status": "Unknown", "on_time": False, "delay_days": 0, "expected_days": expected_days, "actual_days": actual_days}
        
        delay = max(0, actual_days - expected_days) if actual_days > 0 else 0
        on_time = delay == 0
        
        if on_time:
            status = "On Time"
        elif delay <= 1:
            status = "Slightly Delayed"
        elif delay <= 3:
            status = "Delayed"
        else:
            status = "Significantly Delayed"
        
        return {
            "status": status,
            "on_time": on_time,
            "delay_days": delay,
            "expected_days": expected_days,
            "actual_days": actual_days
        }
    
    def _calculate_performance_grade(self, sla: Dict[str, Any], total_cycle: int, expected_days: int) -> str:
        """Calculate performance grade."""
        if not sla.get('on_time', False):
            delay = sla.get('delay_days', 0)
            if delay <= 1:
                return "B+"
            elif delay <= 3:
                return "B-"
            elif delay <= 7:
                return "C+"
            elif delay <= 14:
                return "C-"
            else:
                return "D"
        
        if total_cycle <= expected_days:
            return "A"
        elif total_cycle <= expected_days + 1:
            return "A-"
        else:
            return "B+"
    
    def _calculate_health_score(self, has_pgi: bool, has_pod: bool, has_products: bool, has_distance: bool, sla_status: str) -> int:
        """Calculate overall health score (0-100)."""
        score = 0
        
        # Data completeness (40 points)
        if has_pgi:
            score += 15
        if has_pod:
            score += 15
        if has_products:
            score += 10
        
        # Data quality (30 points)
        if has_distance:
            score += 15
        if has_pgi and has_pod:
            score += 15
        
        # Performance (30 points)
        if sla_status == "On Time":
            score += 30
        elif sla_status == "Slightly Delayed":
            score += 20
        elif sla_status == "Delayed":
            score += 10
        elif sla_status == "Significantly Delayed":
            score += 5
        
        return min(score, 100)
    
    def _generate_recommendations(self, **kwargs) -> List[str]:
        """Generate business recommendations from dashboard data."""
        recommendations = []
        
        # Check PGI status
        if not kwargs.get('has_pgi', False):
            recommendations.append("🚚 PGI Pending - Warehouse should complete PGI immediately.")
        
        # Check POD status
        if kwargs.get('has_pgi', False) and not kwargs.get('has_pod', False):
            recommendations.append("📋 POD Pending - Follow up with transporter for POD confirmation.")
        
        # Check delivery aging
        delivery_aging = kwargs.get('delivery_aging', 0)
        if delivery_aging > 3 and not kwargs.get('has_pgi', False):
            recommendations.append(f"⏰ Dispatch Delay - Shipment has been waiting for dispatch for {delivery_aging} days.")
        
        # Check POD aging
        pod_aging = kwargs.get('pod_aging', 0)
        if pod_aging > 5 and kwargs.get('has_pgi', False) and not kwargs.get('has_pod', False):
            recommendations.append(f"⏰ Transit Delay - Shipment has been in transit for {pod_aging} days.")
        
        # Check SLA
        sla_status = kwargs.get('sla_status', 'Unknown')
        if sla_status == "Delayed" or sla_status == "Significantly Delayed":
            recommendations.append("⚠️ SLA Breach - Delivery time exceeded expected SLA.")
        
        # Check distance
        distance_km = kwargs.get('distance_km', 0)
        if distance_km > 500 and not kwargs.get('has_pod', False):
            recommendations.append("🔄 Long Distance Route - Shipment has long distance transit. Monitor closely.")
        
        # Check products
        if not kwargs.get('has_products', False):
            recommendations.append("📦 Product details not available - Please verify shipment contents.")
        
        # Performance recommendation
        if kwargs.get('has_pod', True) and kwargs.get('has_pgi', True):
            total_cycle = kwargs.get('total_cycle', 0)
            if total_cycle > 14:
                recommendations.append(f"📊 Delivery completed in {total_cycle} days. Review transporter performance.")
        
        # If no recommendations, add a positive one
        if not recommendations:
            recommendations.append("✅ Shipment is on track. No action required.")
        
        return recommendations[:5]  # Limit to 5 recommendations    
  # ==========================================================
    # BLOCK 11: DIAGNOSTIC METHODS
    # ==========================================================
    
    def diagnose_dn(self, dn_no: str) -> Dict[str, Any]:
        """Diagnose DN issues."""
        logger.info(f"🔬 Diagnosing DN: '{dn_no}'")
        
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
        
        exact_query = """
            SELECT COUNT(DISTINCT dn_no) as count 
            FROM delivery_reports 
            WHERE REGEXP_REPLACE(
                CAST(dn_no AS TEXT),
                '[^0-9]',
                '',
                'g'
            ) = :dn_no
        """
        exact_results = self._execute_query(exact_query, {"dn_no": normalized_dn})
        exact_count = exact_results[0].get('count', 0) if exact_results else 0
        result["exact_match_count"] = exact_count
        result["exists"] = exact_count > 0
        result["diagnostic"].append(f"Exact match (normalized): {exact_count} found")
        
        partial_query = self._build_fallback_dn_query()
        partial_results = self._execute_query(partial_query, {"dn_no": normalized_dn})
        similar_dns = [str(r.get('dn_no', '')) for r in partial_results if r.get('dn_no')]
        result["partial_match_count"] = len(similar_dns)
        result["similar_dns"] = similar_dns[:10]
        result["diagnostic"].append(f"Partial matches: {len(similar_dns)} found")
        
        if similar_dns:
            result["diagnostic"].append(f"Similar DNs: {', '.join(similar_dns[:5])}")
        
        raw_query = self._build_raw_dn_query()
        raw_results = self._execute_query(raw_query, {"dn_no": dn_no})
        raw_count = len(raw_results)
        result["diagnostic"].append(f"Raw match (without normalization): {raw_count} found")
        
        logger.info(f"✅ Diagnosis complete for {dn_no}: exists={result['exists']}, partial={result['partial_match_count']}")
        return {"success": True, "data": result}
    
    def check_dn_raw(self, dn_no: str) -> Dict[str, Any]:
        """Check raw DN existence without any normalization."""
        logger.info(f"🔍 Checking raw DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        query = self._build_raw_dn_query()
        results = self._execute_query(query, {"dn_no": dn_no})
        
        similar_dns = [str(r.get('dn_no', '')) for r in results if r.get('dn_no')]
        
        return {
            "success": True,
            "dn": dn_no,
            "found": len(similar_dns) > 0,
            "similar_dns": similar_dns[:10],
            "count": len(similar_dns)
        }
    
    def debug_aging_calculation(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        """Debug aging calculations with native PostgreSQL dates."""
        logger.info("🔍 Running debug_aging_calculation...")
        
        # Validate dates
        dn_valid = self._validate_postgresql_date(dn_create_date, "debug_dn_create")
        gi_valid = self._validate_postgresql_date(good_issue_date, "debug_pgi")
        pod_valid = self._validate_postgresql_date(pod_date, "debug_pod")
        
        # Calculate aging using native dates
        delivery_aging = self.calculate_delivery_aging(dn_create_date, good_issue_date)
        pod_aging = self.calculate_pod_aging(good_issue_date, pod_date)
        total_cycle = self.calculate_total_cycle(dn_create_date, pod_date)
        
        result = {
            "input_dates": {
                "dn_create_date": self._format_display_date(dn_create_date),
                "pgi_date": self._format_display_date(good_issue_date),
                "pod_date": self._format_display_date(pod_date)
            },
            "validation": {
                "dn_create": dn_valid,
                "pgi": gi_valid,
                "pod": pod_valid
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
        
        logger.info("=" * 70)
        logger.info("🔍 DEBUG AGING CALCULATION (Native PostgreSQL Dates)")
        logger.info("=" * 70)
        logger.info("")
        logger.info("📅 PostgreSQL Dates (Native):")
        logger.info(f"  ├── DN Create: {result['input_dates']['dn_create_date']} (valid: {dn_valid['valid']})")
        logger.info(f"  ├── PGI:       {result['input_dates']['pgi_date']} (valid: {gi_valid['valid']})")
        logger.info(f"  └── POD:       {result['input_dates']['pod_date']} (valid: {pod_valid['valid']})")
        logger.info("")
        logger.info("🧮 Aging Calculations (Native Date Difference):")
        logger.info(f"  ├── Delivery Aging: {result['calculations']['delivery_aging_days']} days → {result['formatted']['delivery_aging_text']}")
        logger.info(f"  ├── POD Aging:      {result['calculations']['pod_aging_days']} days → {result['formatted']['pod_aging_text']}")
        logger.info(f"  └── Total Cycle:    {result['calculations']['total_cycle_days']} days → {result['formatted']['total_cycle_text']}")
        logger.info("")
        logger.info("=" * 70)
        
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
            
            logger.info(f"📊 Total pending DNs: {total_pending}")
            
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
            
            logger.info(f"📊 Total pending PGI: {total_pending}")
            
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
            
            logger.info(f"📊 Total pending POD: {total_pending}")
            
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
    # BLOCK 13: WHATSAPP RESPONSE FORMATTER (FIXED - INTELLIGENT STATUS)
    # ==========================================================
        # ==========================================================
    # BLOCK 13: WHATSAPP RESPONSE FORMATTER (ENHANCED)
    # ==========================================================
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """
        Format DN dashboard for WhatsApp response.
        """
        data = dashboard_data.get('data', {})
        
        lines = []
        
        # ==========================================================
        # HEADER
        # ==========================================================
        
        lines.append("📦 *DN: {}*".format(data.get('dn_no', 'N/A')))
        lines.append("")
        
        # ==========================================================
        # DEALER & WAREHOUSE
        # ==========================================================
        
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
        
        # Additional info
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
        
        # ==========================================================
        # METRICS (FIXED - FROM BLOCK 10)
        # ==========================================================
        
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
        
        # ==========================================================
        # DATES
        # ==========================================================
        
        lines.append("*📅 Dates:*")
        lines.append("DN Create: {}".format(data.get('dn_create_date', 'N/A')))
        lines.append("PGI: {}".format(data.get('good_issue_date', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_date', 'N/A')))
        lines.append("")
        
        # ==========================================================
        # AGING
        # ==========================================================
        
        lines.append("*⏳ Aging:*")
        lines.append("Delivery: {}".format(data.get('delivery_aging_text', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_aging_text', 'N/A')))
        lines.append("Total Cycle: {}".format(data.get('total_cycle_text', 'N/A')))
        lines.append("")
        
        # ==========================================================
        # STATUS - INTELLIGENT FROM DATES (FIXED)
        # ==========================================================
        
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
        
        # ==========================================================
        # PRODUCT DETAILS (NEW)
        # ==========================================================
        
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
    """
    Regression tests for date calculations.
    
    Tests:
    1. Your data: 2026-05-05, 2026-05-07, 2026-05-25 → 2, 18, 20
    2. 2026-05-23, 2026-05-24, 2026-05-25 → 1, 1, 2
    3. 2026-06-05, 2026-06-05, 2026-07-05 → 0, 30, 30
    4. 2026-04-05, 2026-05-05, 2026-08-05 → 30, 92, 122
    5. 2026-01-31, 2026-02-01 → 1
    6. 2026-12-31, 2027-01-01 → 1
    7. Leap Year: 2024-02-28, 2024-02-29 → 1
    8. NULL PGI → 0
    9. NULL POD → 0
    """
    logger.info("🧪 Running regression tests...")
    
    from datetime import date as date_type
    
    test_results = []
    all_passed = True
    
    # Test 1: Your data
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
    
    # Test 3
    tc3_dn_create = date_type(2026, 6, 5)
    tc3_pgi = date_type(2026, 6, 5)
    tc3_pod = date_type(2026, 7, 5)
    
    tc3_delivery = self.calculate_delivery_aging(tc3_dn_create, tc3_pgi)
    tc3_pod_aging = self.calculate_pod_aging(tc3_pgi, tc3_pod)
    tc3_total = self.calculate_total_cycle(tc3_dn_create, tc3_pod)
    
    tc3_passed = (tc3_delivery == 0 and tc3_pod_aging == 30 and tc3_total == 30)
    if not tc3_passed:
        all_passed = False
    
    test_results.append({
        "name": "Test 3: 2026-06-05, 2026-06-05, 2026-07-05",
        "expected": {"delivery": 0, "pod": 30, "total": 30},
        "actual": {"delivery": tc3_delivery, "pod": tc3_pod_aging, "total": tc3_total},
        "passed": tc3_passed
    })
    
    # Test 4
    tc4_dn_create = date_type(2026, 4, 5)
    tc4_pgi = date_type(2026, 5, 5)
    tc4_pod = date_type(2026, 8, 5)
    
    tc4_delivery = self.calculate_delivery_aging(tc4_dn_create, tc4_pgi)
    tc4_pod_aging = self.calculate_pod_aging(tc4_pgi, tc4_pod)
    tc4_total = self.calculate_total_cycle(tc4_dn_create, tc4_pod)
    
    tc4_passed = (tc4_delivery == 30 and tc4_pod_aging == 92 and tc4_total == 122)
    if not tc4_passed:
        all_passed = False
    
    test_results.append({
        "name": "Test 4: 2026-04-05, 2026-05-05, 2026-08-05",
        "expected": {"delivery": 30, "pod": 92, "total": 122},
        "actual": {"delivery": tc4_delivery, "pod": tc4_pod_aging, "total": tc4_total},
        "passed": tc4_passed
    })
    
    # Test 5: Month boundary
    tc5_dn_create = date_type(2026, 1, 31)
    tc5_pgi = date_type(2026, 2, 1)
    
    tc5_delivery = self.calculate_delivery_aging(tc5_dn_create, tc5_pgi)
    tc5_passed = (tc5_delivery == 1)
    if not tc5_passed:
        all_passed = False
    
    test_results.append({
        "name": "Test 5: 2026-01-31 → 2026-02-01",
        "expected": {"delivery": 1},
        "actual": {"delivery": tc5_delivery},
        "passed": tc5_passed
    })
    
    # Test 6: Year boundary
    tc6_dn_create = date_type(2026, 12, 31)
    tc6_pgi = date_type(2027, 1, 1)
    
    tc6_delivery = self.calculate_delivery_aging(tc6_dn_create, tc6_pgi)
    tc6_passed = (tc6_delivery == 1)
    if not tc6_passed:
        all_passed = False
    
    test_results.append({
        "name": "Test 6: 2026-12-31 → 2027-01-01",
        "expected": {"delivery": 1},
        "actual": {"delivery": tc6_delivery},
        "passed": tc6_passed
    })
    
    # Test 7: Leap Year
    tc7_dn_create = date_type(2024, 2, 28)
    tc7_pgi = date_type(2024, 2, 29)
    
    tc7_delivery = self.calculate_delivery_aging(tc7_dn_create, tc7_pgi)
    tc7_passed = (tc7_delivery == 1)
    if not tc7_passed:
        all_passed = False
    
    test_results.append({
        "name": "Test 7: 2024-02-28 → 2024-02-29 (Leap Year)",
        "expected": {"delivery": 1},
        "actual": {"delivery": tc7_delivery},
        "passed": tc7_passed
    })
    
    # Test 8: NULL PGI
    tc8_dn_create = date_type(2026, 5, 5)
    tc8_delivery = self.calculate_delivery_aging(tc8_dn_create, None)
    tc8_passed = (tc8_delivery >= 0)
    
    test_results.append({
        "name": "Test 8: NULL PGI",
        "expected": {"delivery": ">= 0"},
        "actual": {"delivery": tc8_delivery},
        "passed": tc8_passed
    })
    
    # Test 9: NULL POD
    tc9_pgi = date_type(2026, 5, 7)
    tc9_pod_aging = self.calculate_pod_aging(tc9_pgi, None)
    tc9_passed = (tc9_pod_aging >= 0)
    
    test_results.append({
        "name": "Test 9: NULL POD",
        "expected": {"pod": ">= 0"},
        "actual": {"pod": tc9_pod_aging},
        "passed": tc9_passed
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
    
    # Log results
    logger.info("=" * 70)
    logger.info("🧪 REGRESSION TEST RESULTS")
    logger.info("=" * 70)
    logger.info("")
    
    for i, test in enumerate(result["tests"], 1):
        status = "✅ PASSED" if test["passed"] else "❌ FAILED"
        logger.info(f"{status} - {test['name']}")
        if "expected" in test and "actual" in test:
            logger.info(f"   Expected: {test['expected']}")
            logger.info(f"   Actual:   {test['actual']}")
        logger.info("")
    
    logger.info(f"Overall Result: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
    logger.info("=" * 70)
    
    return result


# ==========================================================
# BLOCK 15: THREAD-SAFE SINGLETON
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
                    logger.info("🔧 Creating DNAnalysisService singleton...")
                    _dn_analytics_service = DNAnalysisService()
                    logger.info("✅ DNAnalysisService singleton initialized")
                except Exception as e:
                    logger.exception(f"❌ DNAnalysisService initialization failed: {e}")
                    raise
    
    return _dn_analytics_service


# ==========================================================
# BLOCK 16: EXPORTS
# ==========================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service'
]


# ==========================================================
# BLOCK 17: MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v11.0 - ENTERPRISE SAFE UPGRADE")
logger.info("=" * 70)
logger.info("")
logger.info("   SERVICE DETAILS:")
logger.info("   ✅ Service Name: dn_analysis")
logger.info("   ✅ Version: 11.0 (Safe Upgrade)")
logger.info("   ✅ Status: READY")
logger.info("   ✅ Source: PostgreSQL (delivery_reports)")
logger.info("   ✅ Compatible: ai_provider_service.py v5.0")
logger.info("")
logger.info("   DATE POLICY:")
logger.info("   ✅ PostgreSQL DATE values are used AS-IS")
logger.info("   ✅ No month/day swapping")
logger.info("   ✅ Native datetime arithmetic")
logger.info("   ✅ Full raw PostgreSQL verification")
logger.info("   ✅ Database consistency checks")
logger.info("")
logger.info("   SAFE UPGRADE ADDITIONS:")
logger.info("   ✅ Preserved all existing DN extraction logic")
logger.info("   ✅ Fixed metrics (units, revenue, materials, models)")
logger.info("   ✅ Added product details")
logger.info("   ✅ Added dealer/warehouse codes")
logger.info("   ✅ Added distance calculation (non-breaking)")
logger.info("   ✅ Added system information")
logger.info("   ✅ 100% backward compatible")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)

# ✅ Run regression tests on startup
try:
    service = get_dn_analytics_service()
    test_result = service.test_date_calculation()
    if test_result.get("all_passed"):
        logger.info("✅ Regression Tests: ALL PASSED")
    else:
        logger.warning("⚠️ Regression Tests: SOME FAILED")
except Exception as e:
    logger.error(f"❌ Regression Tests failed: {e}")
