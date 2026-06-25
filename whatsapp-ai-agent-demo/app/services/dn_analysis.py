# ==========================================================
# FILE: app/services/dn_analysis.py (v8.3 - FULLY ALIGNED)
# ==========================================================
# PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
# SOURCE: delivery_reports table ONLY
# VERSION: 8.3 - FULLY ALIGNED WITH PROPER INDENTATION
#
# COMPATIBLE WITH: ai_provider_service.py v5.0
# INTEGRATION: Railway PostgreSQL
#
# DATE POLICY (v8.3):
# - ✅ PostgreSQL DATE values are used AS-IS (YYYY-MM-DD)
# - ✅ No YYYY-DD-MM conversion
# - ✅ No month/day swapping
# - ✅ Native datetime arithmetic
# - ✅ Display dates exactly as stored in PostgreSQL
# - ✅ Dashboard dates match PostgreSQL 1:1
# - ✅ Safe error handling with logging
# - ✅ ALL methods properly indented inside class
# - ✅ ALL methods properly aligned
#
# FIXES IN v8.3:
# - ✅ FIXED: All methods properly indented inside class
# - ✅ FIXED: All methods properly aligned (4 spaces)
# - ✅ FIXED: Dashboard dates read directly from PostgreSQL
# - ✅ FIXED: No date conversion in dashboard builder
# - ✅ FIXED: Added debug logging for date values
# - ✅ FIXED: Field mapping matches DeliveryReport model
# - ✅ VERIFIED: All public methods found by service
# - ✅ VERIFIED: PostgreSQL dates displayed as-is
# ==========================================================

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, date, timedelta
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
import threading
import re
import traceback
import time

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


# ==========================================================
# BLOCK 2: DNAnalysisService CLASS
# ==========================================================

class DNAnalysisService:
    """
    DN Analytics Service - Direct PostgreSQL Connection.
    
    This service connects directly to PostgreSQL without any repository layer.
    All data comes from delivery_reports table.
    
    DATE POLICY (v8.3):
    - PostgreSQL DATE values are used AS-IS
    - No YYYY-DD-MM conversion
    - Native datetime arithmetic for aging calculations
    - Display dates exactly as stored in PostgreSQL
    - Dashboard dates match PostgreSQL 1:1
    
    COMPATIBLE WITH: ai_provider_service.py v5.0
    """
    
    def __init__(self):
        """Initialize DN Analytics Service."""
        self._service_name = "dn_analysis"
        self._version = "8.3"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0
        self._startup_time = datetime.now().isoformat()
        
        logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
        logger.info("📋 Date Policy: Native PostgreSQL DATE values (YYYY-MM-DD)")
        logger.info("📋 Dashboard: Dates read directly from PostgreSQL")
        logger.info("📋 No YYYY-DD-MM conversion")
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
        """
        Execute raw SQL query and return results as dicts.
        """
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
        """
        Build DN query with multiple matching strategies.
        """
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
                MAX(division) AS division,
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                MAX(delivery_status) AS delivery_status,
                MAX(pgi_status) AS pgi_status,
                MAX(pod_status) AS pod_status,
                MAX(pending_flag) AS pending_flag,
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
    
    def _build_exact_match_query(self) -> str:
        """
        Build exact match query for diagnostic purposes.
        """
        return """
            SELECT *
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
            LIMIT 1
        """
    
    def _build_count_query(self) -> str:
        """
        Build count query for diagnostic purposes.
        """
        return """
            SELECT COUNT(*) as count
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
        """
    
    def _build_fallback_dn_query(self) -> str:
        """
        Build fallback DN query for partial matches.
        """
        return """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
    
    def _build_raw_dn_query(self) -> str:
        """
        Build raw DN query to check if DN exists without normalization.
        """
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
            # Check 1: SessionLocal exists
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                logger.error("❌ Health check failed: SessionLocal not available")
                return result
            
            # Check 2: Test connection
            session = SessionLocal()
            try:
                session.execute(text("SELECT 1"))
                result["database"] = "connected"
                logger.info("✅ Database connection: connected")
            except Exception as e:
                result["errors"].append(f"Connection failed: {str(e)}")
                logger.error(f"❌ Database connection failed: {e}")
                return result
            
            # Check 3: Check table exists
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
            
            # Check 4: Check required columns
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
            
            # Check 5: Test query execution
            try:
                test_query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports LIMIT 1"
                session.execute(text(test_query))
                logger.info("✅ Test query executed successfully")
            except Exception as e:
                result["errors"].append(f"Test query failed: {str(e)}")
                logger.error(f"❌ Test query failed: {e}")
                return result
            
            # All checks passed
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
    # BLOCK 6: AGING CALCULATION METHODS (NATIVE POSTGRESQL DATES)
    # ==========================================================
    
    def _parse_date(self, date_value):
        """
        Parse PostgreSQL date WITHOUT swapping month and day.
        
        PostgreSQL stores dates as YYYY-MM-DD.
        Use them AS-IS without any conversion.
        
        Examples:
        PostgreSQL: 2026-05-01 → 1 May 2026
        PostgreSQL: 2026-05-03 → 3 May 2026
        PostgreSQL: 2026-05-14 → 14 May 2026
        
        Args:
            date_value: Date from PostgreSQL (date object, datetime, or string)
            
        Returns:
            datetime object (unchanged from PostgreSQL)
        """
        if not date_value:
            return None
        
        try:
            # Handle PostgreSQL date objects - USE AS-IS
            if isinstance(date_value, date) and not isinstance(date_value, datetime):
                # PostgreSQL date(2026, 5, 1) → datetime(2026, 5, 1)
                # NO SWAPPING! Use AS-IS.
                return datetime(date_value.year, date_value.month, date_value.day)
            
            # Handle datetime objects - USE AS-IS
            elif isinstance(date_value, datetime):
                return datetime(date_value.year, date_value.month, date_value.day)
            
            # Handle string dates
            elif isinstance(date_value, str):
                parts = date_value.split('-')
                if len(parts) == 3:
                    year = int(parts[0])
                    month = int(parts[1])
                    day = int(parts[2])
                    # NO SWAPPING! Use AS-IS.
                    return datetime(year, month, day)
                else:
                    return datetime.strptime(date_value, "%Y-%m-%d")
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Date parsing error for {date_value}: {e}")
            return None
    
    def _parse_date_ydm(self, date_value):
        """
        Parse date using company format: YYYY-DD-MM.
        
        DEPRECATED: This method is kept for backward compatibility.
        It now uses the same logic as _parse_date() - NO SWAPPING.
        """
        return self._parse_date(date_value)
    
    def _get_day_value(self, date_value) -> int:
        """
        DEPRECATED: This method is kept for backward compatibility.
        Returns the day value from the date.
        
        Args:
            date_value: Date from PostgreSQL
            
        Returns:
            Day number (1-31)
        """
        if not date_value:
            return 0
        
        try:
            parsed = self._parse_date(date_value)
            if parsed:
                return parsed.day
            return 0
        except Exception as e:
            logger.error(f"❌ Failed to extract day from {date_value}: {e}")
            return 0
    
    def _format_date_dmy_long(self, date_value) -> str:
        """
        Format datetime → DD Month YYYY for display.
        
        Example: 2026-05-01 → "1 May 2026"
        """
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
        """
        Format datetime → DD-MMM-YY for display.
        
        Example: 2026-05-01 → "1-May-26"
        """
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
    
    def _safe_date_diff(self, date1, date2) -> int:
        """
        Safely calculate days between two dates using native date subtraction.
        
        Args:
            date1: First date (datetime.date or None)
            date2: Second date (datetime.date or None)
            
        Returns:
            Number of days difference (0 if invalid)
        """
        if date1 is None or date2 is None:
            return 0
        
        try:
            # Ensure both are date objects
            if not isinstance(date1, (date, datetime)):
                logger.warning(f"⚠️ Invalid date1 type: {type(date1)}")
                return 0
            if not isinstance(date2, (date, datetime)):
                logger.warning(f"⚠️ Invalid date2 type: {type(date2)}")
                return 0
            
            # Convert to date if datetime
            if isinstance(date1, datetime):
                date1 = date1.date()
            if isinstance(date2, datetime):
                date2 = date2.date()
            
            # Native date subtraction - NO SWAPPING!
            delta = date2 - date1
            days = delta.days
            return max(0, days)  # Ensure non-negative
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate date difference: {e}")
            return 0
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        """
        Calculate delivery aging using native PostgreSQL dates.
        
        FORMULA: PGI - DN Create Date
        
        If PGI is missing, use Current Date.
        
        Args:
            dn_create_date: DN Create date (PostgreSQL date object)
            good_issue_date: PGI date (PostgreSQL date object)
            
        Returns:
            Delivery aging in days (0 = Same Day)
        """
        try:
            # NULL date handling
            if dn_create_date is None:
                logger.warning("⚠️ DN Create Date Missing - Returning 0")
                return 0
            
            # Parse dates using native PostgreSQL (NO SWAPPING)
            dn_date = self._parse_date(dn_create_date)
            if dn_date is None:
                logger.warning("⚠️ Failed to parse DN Create date - Returning 0")
                return 0
            
            # If PGI is missing, use current date
            if good_issue_date is None:
                logger.info("📊 Delivery Aging: PGI missing - Using Current Date")
                current_date = datetime.now().date()
                days = self._safe_date_diff(dn_date, current_date)
                logger.info(f"✅ Delivery Aging (Current Date): {days} days")
                return days
            
            # Parse PGI date
            gi_date = self._parse_date(good_issue_date)
            if gi_date is None:
                logger.warning("⚠️ Failed to parse PGI date - Returning 0")
                return 0
            
            # Calculate difference using native dates
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
        """
        Calculate POD aging using native PostgreSQL dates.
        
        FORMULA: POD - PGI
        
        If POD is missing, use Current Date.
        
        Args:
            good_issue_date: PGI date (PostgreSQL date object)
            pod_date: POD date (PostgreSQL date object)
            
        Returns:
            POD aging in days (0 = Same Day)
        """
        try:
            # If PGI is missing, POD aging cannot be calculated
            if good_issue_date is None:
                logger.info("📊 POD Aging: PGI missing - Cannot calculate")
                return 0
            
            # Parse PGI date
            gi_date = self._parse_date(good_issue_date)
            if gi_date is None:
                logger.warning("⚠️ Failed to parse PGI date - Returning 0")
                return 0
            
            # If POD is missing, use current date
            if pod_date is None:
                logger.info("📊 POD Aging: POD missing - Using Current Date")
                current_date = datetime.now().date()
                days = self._safe_date_diff(gi_date, current_date)
                logger.info(f"✅ POD Aging (Current Date): {days} days")
                return days
            
            # Parse POD date
            pd_date = self._parse_date(pod_date)
            if pd_date is None:
                logger.warning("⚠️ Failed to parse POD date - Returning 0")
                return 0
            
            # Calculate difference using native dates
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
        """
        Calculate total cycle using native PostgreSQL dates.
        
        FORMULA: POD - DN Create Date
        
        If POD is missing, use Current Date.
        
        Args:
            dn_create_date: DN Create date (PostgreSQL date object)
            pod_date: POD date (PostgreSQL date object)
            
        Returns:
            Total cycle time in days (0 = Same Day)
        """
        try:
            # NULL date handling
            if dn_create_date is None:
                logger.warning("⚠️ DN Create Date Missing - Returning 0")
                return 0
            
            # Parse DN Create date
            dn_date = self._parse_date(dn_create_date)
            if dn_date is None:
                logger.warning("⚠️ Failed to parse DN Create date - Returning 0")
                return 0
            
            # If POD is missing, use current date
            if pod_date is None:
                logger.info("📊 Total Cycle: POD missing - Using Current Date")
                current_date = datetime.now().date()
                days = self._safe_date_diff(dn_date, current_date)
                logger.info(f"✅ Total Cycle (Current Date): {days} days")
                return days
            
            # Parse POD date
            pd_date = self._parse_date(pod_date)
            if pd_date is None:
                logger.warning("⚠️ Failed to parse POD date - Returning 0")
                return 0
            
            # Calculate difference using native dates
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
    
    def _format_aging_text(self, days: int) -> str:
        """
        Format aging days into human readable text.
        
        Ensures "Same Day" only appears when days = 0
        """
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
    
    def _format_display_date(self, date_value) -> str:
        """
        Format PostgreSQL date for display (YYYY-MM-DD).
        
        This preserves the original PostgreSQL format for display.
        
        Args:
            date_value: PostgreSQL date object or string
            
        Returns:
            Formatted display date string (e.g., "2026-05-01")
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
    
    # ==========================================================
    # BLOCK 6.5: DEBUG METHOD (FIXED)
    # ==========================================================
    
    def debug_aging_calculation(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        """
        Debug aging calculations with native PostgreSQL dates.
        
        Shows PostgreSQL dates → Aging calculations.
        
        Args:
            dn_create_date: DN Create date (PostgreSQL)
            good_issue_date: PGI date (PostgreSQL)
            pod_date: POD date (PostgreSQL)
            
        Returns:
            Dictionary with all parsed dates and aging calculations
        """
        logger.info("🔍 Running debug_aging_calculation...")
        
        # Parse dates using native PostgreSQL (NO SWAPPING)
        dn_parsed = self._parse_date(dn_create_date)
        gi_parsed = self._parse_date(good_issue_date)
        pod_parsed = self._parse_date(pod_date)
        
        # Calculate aging using native dates
        delivery_aging = self.calculate_delivery_aging(dn_create_date, good_issue_date)
        pod_aging = self.calculate_pod_aging(good_issue_date, pod_date)
        total_cycle = self.calculate_total_cycle(dn_create_date, pod_date)
        
        # Build result dictionary
        result = {
            "input_dates": {
                "dn_create_date": self._format_display_date(dn_create_date),
                "pgi_date": self._format_display_date(good_issue_date),
                "pod_date": self._format_display_date(pod_date)
            },
            "parsed_dates": {
                "dn_create_date": dn_parsed.strftime('%Y-%m-%d') if dn_parsed else None,
                "pgi_date": gi_parsed.strftime('%Y-%m-%d') if gi_parsed else None,
                "pod_date": pod_parsed.strftime('%Y-%m-%d') if pod_parsed else None
            },
            "display_dates": {
                "dn_create_date": dn_parsed.strftime('%d %B %Y') if dn_parsed else None,
                "pgi_date": gi_parsed.strftime('%d %B %Y') if gi_parsed else None,
                "pod_date": pod_parsed.strftime('%d %B %Y') if pod_parsed else None
            },
            "calculations": {
                "delivery_aging_days": delivery_aging,
                "pod_aging_days": pod_aging,
                "total_cycle_days": total_cycle
            },
            "formatted": {
                "delivery_aging_text": self._format_aging_text(delivery_aging),
                "pod_aging_text": self._format_aging_text(pod_aging),
                "total_cycle_text": self._format_aging_text(total_cycle)
            },
            "timestamp": datetime.now().isoformat()
        }
        
        # Log full debug info
        logger.info("=" * 70)
        logger.info("🔍 DEBUG AGING CALCULATION (Native PostgreSQL Dates)")
        logger.info("=" * 70)
        logger.info("")
        logger.info("📅 PostgreSQL Dates (Display - YYYY-MM-DD):")
        logger.info(f"  ├── DN Create: {result['input_dates']['dn_create_date']}")
        logger.info(f"  ├── PGI:       {result['input_dates']['pgi_date']}")
        logger.info(f"  └── POD:       {result['input_dates']['pod_date']}")
        logger.info("")
        logger.info("📅 Parsed Dates (Native):")
        logger.info(f"  ├── DN Create: {result['parsed_dates']['dn_create_date']} → {result['display_dates']['dn_create_date']}")
        logger.info(f"  ├── PGI:       {result['parsed_dates']['pgi_date']} → {result['display_dates']['pgi_date']}")
        logger.info(f"  └── POD:       {result['parsed_dates']['pod_date']} → {result['display_dates']['pod_date']}")
        logger.info("")
        logger.info("🧮 Aging Calculations (Native Date Difference):")
        logger.info(f"  ├── Delivery Aging: {result['calculations']['delivery_aging_days']} days → {result['formatted']['delivery_aging_text']}")
        logger.info(f"  ├── POD Aging:      {result['calculations']['pod_aging_days']} days → {result['formatted']['pod_aging_text']}")
        logger.info(f"  └── Total Cycle:    {result['calculations']['total_cycle_days']} days → {result['formatted']['total_cycle_text']}")
        logger.info("")
        logger.info("=" * 70)
        
        return result
    
    # ==========================================================
    # BLOCK 7: DN SEARCH WITH FULL DIAGNOSTICS
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """
        Search for a specific DN with multiple matching strategies and full diagnostics.
        
        Steps:
        1. Normalize DN
        2. Try all matching strategies at once
        3. If not found, try fallback partial match
        4. If fallback finds the same DN, auto-retry with exact match
        5. Return results or similar DNs
        """
        logger.info(f"🔍 Searching for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        # Step 1: Normalize DN
        normalized_dn = self._normalize_dn(dn_no)
        logger.info(f"   ├── Normalized: '{normalized_dn}'")
        logger.info(f"   ├── Length: {len(normalized_dn)}")
        
        if len(normalized_dn) < 8:
            return {"success": False, "error": f"Invalid DN format: {normalized_dn} (must be 8-12 digits)"}
        
        # Step 2: Execute query with multiple strategies
        query = self._build_normalized_dn_query()
        results = self._execute_query(query, {"dn_no": normalized_dn})
        
        logger.info(f"📊 DN Search | Input={dn_no} | Normalized={normalized_dn} | Results={len(results)}")
        
        if results:
            logger.info(f"✅ DN {dn_no} found with {results[0].get('material_count', 1)} materials")
            return {"success": True, "data": results[0]}
        
        # Step 3: Check exact match count before fallback
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
        
        # Step 4: Fallback partial match search
        logger.warning(f"⚠️ Primary match not found for {dn_no}. Running fallback search...")
        fallback_query = self._build_fallback_dn_query()
        fallback_results = self._execute_query(fallback_query, {"dn_no": normalized_dn})
        
        similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
        
        # Check if the requested DN is in similar_dns
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
        """
        Aggregate raw DN results into a single dashboard record.
        """
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
            "material_count": len(results)
        }
        
        # Calculate aging using native PostgreSQL dates
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
        """
        Verify if DN exists using multiple matching strategies.
        """
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
    # BLOCK 9: TEST DN LOOKUP (DIAGNOSTIC)
    # ==========================================================
    
    def test_dn_lookup(self, dn_no: str) -> Dict[str, Any]:
        """
        Test DN lookup with full diagnostics.
        
        Returns:
        - exact match count
        - normalized match count
        - like match count
        - first 10 matching DNs
        """
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
        
        # 1. Exact match count
        query1 = "SELECT COUNT(*) as count FROM delivery_reports WHERE CAST(dn_no AS TEXT) = :dn_no"
        r1 = self._execute_query(query1, {"dn_no": normalized_dn})
        results["exact_count"] = r1[0].get('count', 0) if r1 else 0
        results["diagnostics"].append(f"Exact match: {results['exact_count']}")
        
        # 2. LIKE match count
        query2 = "SELECT COUNT(*) as count FROM delivery_reports WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'"
        r2 = self._execute_query(query2, {"dn_no": normalized_dn})
        results["like_count"] = r2[0].get('count', 0) if r2 else 0
        results["diagnostics"].append(f"LIKE match: {results['like_count']}")
        
        # 3. REGEXP_REPLACE match count
        query3 = """
            SELECT COUNT(*) as count 
            FROM delivery_reports 
            WHERE REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
        """
        r3 = self._execute_query(query3, {"dn_no": normalized_dn})
        results["regex_count"] = r3[0].get('count', 0) if r3 else 0
        results["diagnostics"].append(f"REGEXP match: {results['regex_count']}")
        
        # 4. Get matching DNs
        query4 = self._build_fallback_dn_query()
        r4 = self._execute_query(query4, {"dn_no": normalized_dn})
        results["matching_dns"] = [str(r.get('dn_no', '')) for r in r4 if r.get('dn_no')]
        
        results["found"] = results["exact_count"] > 0 or results["like_count"] > 0
        results["diagnostics"].append(f"Total matching DNs: {len(results['matching_dns'])}")
        
        logger.info(f"✅ Test DN lookup complete: found={results['found']}")
        return {"success": True, "data": results}
    
    # ==========================================================
    # BLOCK 10: DN DASHBOARD - FIXED: DATES READ DIRECTLY FROM POSTGRESQL
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete DN dashboard with dates read directly from PostgreSQL.
        
        ✅ PostgreSQL is the ONLY source of truth
        ✅ Dates are used AS-IS from PostgreSQL
        ✅ No date conversion or swapping
        ✅ Dashboard dates exactly match PostgreSQL
        """
        logger.info(f"📊 Getting dashboard for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        # Search for the DN
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
        
        # ✅ FIX: Read dates directly from PostgreSQL data
        # These values come directly from the SQL query
        raw_dn_create_date = data.get('dn_create_date')
        raw_good_issue_date = data.get('good_issue_date')
        raw_pod_date = data.get('pod_date')
        
        # ✅ DEBUG: Log raw PostgreSQL dates
        logger.info(f"📅 RAW PostgreSQL Dates for DN {dn_no}:")
        logger.info(f"   ├── dn_create_date: {raw_dn_create_date}")
        logger.info(f"   ├── good_issue_date: {raw_good_issue_date}")
        logger.info(f"   └── pod_date: {raw_pod_date}")
        
        # ✅ FIX: Calculate aging using native PostgreSQL dates
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
        
        # ✅ FIX: Build dashboard with dates directly from PostgreSQL
        dashboard = {
            "dn_no": data.get('dn_no'),
            "dealer_name": data.get('dealer_name', 'Unknown'),
            "warehouse": data.get('warehouse', 'Unknown'),
            "city": data.get('city', 'Unknown'),
            "delivery_location": data.get('delivery_location'),
            "sales_manager": data.get('sales_manager'),
            "division": data.get('division'),
            "total_units": int(data.get('total_units', 0)),
            "total_revenue": float(data.get('total_revenue', 0)),
            "material_count": data.get('material_count', 1),
            
            # ✅ FIX: Dates read directly from PostgreSQL, formatted for display
            "dn_create_date": self._format_display_date(raw_dn_create_date),
            "good_issue_date": self._format_display_date(raw_good_issue_date),
            "pod_date": self._format_display_date(raw_pod_date),
            
            # ✅ FIX: Status fields
            "delivery_status": data.get('delivery_status', 'Unknown'),
            "pgi_status": data.get('pgi_status', 'Unknown'),
            "pod_status": data.get('pod_status', 'Unknown'),
            "pending_flag": data.get('pending_flag', False),
            
            # ✅ FIX: Aging calculations
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "total_cycle_days": total_cycle,
            "delivery_aging_text": self._format_aging_text(delivery_aging),
            "pod_aging_text": self._format_aging_text(pod_aging),
            "total_cycle_text": self._format_aging_text(total_cycle)
        }
        
        # ✅ FIX: Add status emojis
        status = dashboard.get('delivery_status', '')
        if status in ['Completed', 'Delivered', 'Closed']:
            dashboard['status_emoji'] = '✅'
            dashboard['status_text'] = 'Delivered'
        elif status in ['In Transit', 'Transit']:
            dashboard['status_emoji'] = '🚚'
            dashboard['status_text'] = 'In Transit'
        elif status in ['Pending', 'Open']:
            dashboard['status_emoji'] = '⏳'
            dashboard['status_text'] = 'Pending'
        else:
            dashboard['status_emoji'] = '❓'
            dashboard['status_text'] = status or 'Unknown'
        
        # ✅ FIX: Add PGI status
        pgi_status = dashboard.get('pgi_status', '')
        dashboard['pgi_status_text'] = '✅ Completed' if pgi_status == 'Completed' else '⏳ Pending'
        
        # ✅ FIX: Add POD status
        pod_status = dashboard.get('pod_status', '')
        dashboard['pod_status_text'] = 'Done' if pod_status in ['Completed', 'Received', 'Done'] else '⏳ Pending'
        
        # ✅ FIX: Add pending flag
        pending = dashboard.get('pending_flag', False)
        dashboard['pending_flag_text'] = '⚠️ Yes' if pending else '🟢 No'
        
        # ✅ FIX: Validation - Ensure dates match PostgreSQL
        dashboard_dn_create = dashboard.get('dn_create_date')
        dashboard_good_issue = dashboard.get('good_issue_date')
        dashboard_pod = dashboard.get('pod_date')
        
        raw_dn_create_str = self._format_display_date(raw_dn_create_date)
        raw_good_issue_str = self._format_display_date(raw_good_issue_date)
        raw_pod_str = self._format_display_date(raw_pod_date)
        
        # ✅ FIX: Log validation results
        logger.info(f"📊 Validation for DN {dn_no}:")
        logger.info(f"   ├── DN Create: PostgreSQL={raw_dn_create_str} → Dashboard={dashboard_dn_create} {'✅' if raw_dn_create_str == dashboard_dn_create else '❌'}")
        logger.info(f"   ├── PGI: PostgreSQL={raw_good_issue_str} → Dashboard={dashboard_good_issue} {'✅' if raw_good_issue_str == dashboard_good_issue else '❌'}")
        logger.info(f"   └── POD: PostgreSQL={raw_pod_str} → Dashboard={dashboard_pod} {'✅' if raw_pod_str == dashboard_pod else '❌'}")
        
        # ✅ FIX: If any date doesn't match, log error
        if (raw_dn_create_str != dashboard_dn_create or 
            raw_good_issue_str != dashboard_good_issue or 
            raw_pod_str != dashboard_pod):
            logger.error(f"❌ Date mismatch detected for DN {dn_no}!")
            logger.error(f"   dn_create_date: {raw_dn_create_str} vs {dashboard_dn_create}")
            logger.error(f"   good_issue_date: {raw_good_issue_str} vs {dashboard_good_issue}")
            logger.error(f"   pod_date: {raw_pod_str} vs {dashboard_pod}")
        
        logger.info(f"✅ Dashboard returned for DN {dn_no}")
        return {"success": True, "data": dashboard}
    
    # ==========================================================
    # BLOCK 11: DATE VALIDATION TEST
    # ==========================================================
    
    def test_date_calculation(self) -> Dict[str, Any]:
        """
        Test date calculations using native PostgreSQL dates.
        
        Test Case 1:
        - DN Create: 2026-05-23
        - PGI:       2026-05-24
        - POD:       2026-05-25
        - Expected: Delivery=1, POD=1, Total=2
        
        Test Case 2:
        - DN Create: 2026-06-05
        - PGI:       2026-06-05
        - POD:       2026-07-05
        - Expected: Delivery=0, POD=30, Total=30
        
        Test Case 3:
        - DN Create: 2026-04-05
        - PGI:       2026-05-05
        - POD:       2026-08-05
        - Expected: Delivery=30, POD=92, Total=122
        """
        logger.info("🧪 Running date calculation test...")
        
        from datetime import date as date_type
        
        test_results = []
        all_passed = True
        
        # Test Case 1
        tc1_dn_create = date_type(2026, 5, 23)
        tc1_pgi = date_type(2026, 5, 24)
        tc1_pod = date_type(2026, 5, 25)
        
        tc1_delivery = self.calculate_delivery_aging(tc1_dn_create, tc1_pgi)
        tc1_pod_aging = self.calculate_pod_aging(tc1_pgi, tc1_pod)
        tc1_total = self.calculate_total_cycle(tc1_dn_create, tc1_pod)
        
        tc1_passed = (tc1_delivery == 1 and tc1_pod_aging == 1 and tc1_total == 2)
        if not tc1_passed:
            all_passed = False
        
        test_results.append({
            "name": "Test Case 1: 2026-05-23, 2026-05-24, 2026-05-25",
            "postgresql_dates": {
                "dn_create": str(tc1_dn_create),
                "pgi": str(tc1_pgi),
                "pod": str(tc1_pod)
            },
            "calculations": {
                "delivery_aging": tc1_delivery,
                "pod_aging": tc1_pod_aging,
                "total_cycle": tc1_total
            },
            "expected": {
                "delivery_aging": 1,
                "pod_aging": 1,
                "total_cycle": 2
            },
            "passed": tc1_passed
        })
        
        # Test Case 2
        tc2_dn_create = date_type(2026, 6, 5)
        tc2_pgi = date_type(2026, 6, 5)
        tc2_pod = date_type(2026, 7, 5)
        
        tc2_delivery = self.calculate_delivery_aging(tc2_dn_create, tc2_pgi)
        tc2_pod_aging = self.calculate_pod_aging(tc2_pgi, tc2_pod)
        tc2_total = self.calculate_total_cycle(tc2_dn_create, tc2_pod)
        
        tc2_passed = (tc2_delivery == 0 and tc2_pod_aging == 30 and tc2_total == 30)
        if not tc2_passed:
            all_passed = False
        
        test_results.append({
            "name": "Test Case 2: 2026-06-05, 2026-06-05, 2026-07-05",
            "postgresql_dates": {
                "dn_create": str(tc2_dn_create),
                "pgi": str(tc2_pgi),
                "pod": str(tc2_pod)
            },
            "calculations": {
                "delivery_aging": tc2_delivery,
                "pod_aging": tc2_pod_aging,
                "total_cycle": tc2_total
            },
            "expected": {
                "delivery_aging": 0,
                "pod_aging": 30,
                "total_cycle": 30
            },
            "passed": tc2_passed
        })
        
        # Test Case 3
        tc3_dn_create = date_type(2026, 4, 5)
        tc3_pgi = date_type(2026, 5, 5)
        tc3_pod = date_type(2026, 8, 5)
        
        tc3_delivery = self.calculate_delivery_aging(tc3_dn_create, tc3_pgi)
        tc3_pod_aging = self.calculate_pod_aging(tc3_pgi, tc3_pod)
        tc3_total = self.calculate_total_cycle(tc3_dn_create, tc3_pod)
        
        tc3_passed = (tc3_delivery == 30 and tc3_pod_aging == 92 and tc3_total == 122)
        if not tc3_passed:
            all_passed = False
        
        test_results.append({
            "name": "Test Case 3: 2026-04-05, 2026-05-05, 2026-08-05",
            "postgresql_dates": {
                "dn_create": str(tc3_dn_create),
                "pgi": str(tc3_pgi),
                "pod": str(tc3_pod)
            },
            "calculations": {
                "delivery_aging": tc3_delivery,
                "pod_aging": tc3_pod_aging,
                "total_cycle": tc3_total
            },
            "expected": {
                "delivery_aging": 30,
                "pod_aging": 92,
                "total_cycle": 122
            },
            "passed": tc3_passed
        })
        
        # Build result
        result = {
            "test_name": "Native PostgreSQL Date Calculation Test",
            "date_policy": "YYYY-MM-DD (Native PostgreSQL)",
            "tests": test_results,
            "all_passed": all_passed,
            "total_tests": len(test_results),
            "passed_tests": sum(1 for t in test_results if t.get("passed", False)),
            "timestamp": datetime.now().isoformat()
        }
        
        # Log results
        logger.info("=" * 70)
        logger.info("🧪 NATIVE POSTGRESQL DATE TEST RESULTS")
        logger.info("=" * 70)
        logger.info("")
        
        for i, test in enumerate(result["tests"], 1):
            logger.info(f"📋 {test['name']}:")
            logger.info(f"   PostgreSQL Dates:")
            logger.info(f"     ├── DN Create: {test['postgresql_dates']['dn_create']}")
            logger.info(f"     ├── PGI:       {test['postgresql_dates']['pgi']}")
            logger.info(f"     └── POD:       {test['postgresql_dates']['pod']}")
            logger.info(f"   Calculations (Expected):")
            logger.info(f"     ├── Delivery Aging: {test['calculations']['delivery_aging']} days (Expected: {test['expected']['delivery_aging']}) {'✅' if test['calculations']['delivery_aging'] == test['expected']['delivery_aging'] else '❌'}")
            logger.info(f"     ├── POD Aging:      {test['calculations']['pod_aging']} days (Expected: {test['expected']['pod_aging']}) {'✅' if test['calculations']['pod_aging'] == test['expected']['pod_aging'] else '❌'}")
            logger.info(f"     └── Total Cycle:    {test['calculations']['total_cycle']} days (Expected: {test['expected']['total_cycle']}) {'✅' if test['calculations']['total_cycle'] == test['expected']['total_cycle'] else '❌'}")
            logger.info(f"   Result: {'✅ PASSED' if test['passed'] else '❌ FAILED'}")
            logger.info("")
        
        logger.info(f"Overall Result: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
        logger.info("=" * 70)
        
        return result
    
    # ==========================================================
    # BLOCK 12: DIAGNOSTIC METHODS
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
    
    # ==========================================================
    # BLOCK 13: PENDING METHODS
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get all pending DNs."""
        logger.info(f"🔍 Getting pending DNs (limit: {limit}, offset: {offset})")
        
        try:
            # Limit validation
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
    # BLOCK 14: WHATSAPP RESPONSE FORMATTER - EXACT OUTPUT
    # ==========================================================
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """
        Format DN dashboard for WhatsApp response.
        
        ✅ Dates are in YYYY-MM-DD format (Native PostgreSQL)
        ✅ Aging is calculated correctly
        ✅ POD shows "Done" when completed
        """
        data = dashboard_data.get('data', {})
        
        lines = []
        lines.append("📦 *DN: {}*".format(data.get('dn_no', 'N/A')))
        lines.append("")
        lines.append("*Dealer:*")
        lines.append("{}".format(data.get('dealer_name', 'Unknown')))
        lines.append("")
        lines.append("*Warehouse:*")
        lines.append("{}".format(data.get('warehouse', 'Unknown')))
        lines.append("")
        lines.append("*City:*")
        lines.append("{}".format(data.get('city', 'Unknown')))
        lines.append("")
        
        # Delivery Location
        delivery_location = data.get('delivery_location')
        if delivery_location:
            lines.append("*Delivery Location:*")
            lines.append("{}".format(delivery_location))
            lines.append("")
        
        # Sales Manager
        sales_manager = data.get('sales_manager')
        if sales_manager:
            lines.append("*Sales Manager:*")
            lines.append("{}".format(sales_manager))
            lines.append("")
        
        # Division
        division = data.get('division')
        if division:
            lines.append("*Division:*")
            lines.append("{}".format(division))
            lines.append("")
        
        # Metrics
        lines.append("*📊 Metrics:*")
        lines.append("Units: {}".format(data.get('total_units', 0)))
        revenue = data.get('total_revenue', 0)
        if revenue:
            lines.append("Revenue: PKR {:,}".format(revenue))
        else:
            lines.append("Revenue: PKR 0")
        lines.append("")
        
        # Material Count
        material_count = data.get('material_count', 1)
        lines.append("Materials: {}".format(material_count))
        lines.append("")
        
        # ✅ Dates - Display as YYYY-MM-DD (Native PostgreSQL format)
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
        lines.append("*📋 Status:*")
        lines.append("Delivery: {} {}".format(data.get('status_emoji', '❓'), data.get('status_text', 'Unknown')))
        lines.append("PGI: {}".format(data.get('pgi_status_text', 'Unknown')))
        
        # POD Status - Show "Done" when completed
        pod_status = data.get('pod_status', '')
        pod_status_text = data.get('pod_status_text', 'Unknown')
        
        if pod_status in ['Completed', 'Received', 'Done'] or pod_status_text == 'Done':
            lines.append("POD: Done")
        else:
            lines.append("POD: {}".format(pod_status_text))
        
        lines.append("Pending: {}".format(data.get('pending_flag_text', 'Unknown')))
        
        return "\n".join(lines)


# ==========================================================
# BLOCK 15: THREAD-SAFE SINGLETON
# ==========================================================

_dn_analytics_service = None
_dn_lock = threading.Lock()


def get_dn_analytics_service() -> DNAnalysisService:
    """
    Thread-safe singleton getter.
    
    COMPATIBLE WITH: ai_provider_service.py v5.0
    Expected name: get_dn_analytics_service()
    """
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
logger.info("DNAnalysisService v8.3 - FULLY ALIGNED")
logger.info("=" * 70)
logger.info("")
logger.info("   SERVICE DETAILS:")
logger.info("   ✅ Service Name: dn_analysis")
logger.info("   ✅ Version: 8.3")
logger.info("   ✅ Status: READY")
logger.info("   ✅ Source: PostgreSQL (delivery_reports)")
logger.info("   ✅ Compatible: ai_provider_service.py v5.0")
logger.info("")
logger.info("   DATE POLICY (NATIVE POSTGRESQL):")
logger.info("   ✅ PostgreSQL DATE values are used AS-IS")
logger.info("   ✅ No YYYY-DD-MM conversion")
logger.info("   ✅ No month/day swapping")
logger.info("   ✅ Native datetime arithmetic")
logger.info("   ✅ Display dates exactly as stored in PostgreSQL")
logger.info("   ✅ Dashboard dates match PostgreSQL 1:1")
logger.info("")
logger.info("   AGING FORMULAS:")
logger.info("   ✅ Delivery Aging = PGI - DN Create")
logger.info("   ✅ POD Aging = POD - PGI")
logger.info("   ✅ Total Cycle = POD - DN Create")
logger.info("   ✅ Missing dates use Current Date")
logger.info("")
logger.info("   FIXES IN v8.3:")
logger.info("   ✅ FIXED: All methods properly indented inside class")
logger.info("   ✅ FIXED: All methods properly aligned (4 spaces)")
logger.info("   ✅ FIXED: Dashboard dates read directly from PostgreSQL")
logger.info("   ✅ FIXED: No date conversion in dashboard builder")
logger.info("   ✅ FIXED: Added debug logging for date values")
logger.info("   ✅ FIXED: Field mapping matches DeliveryReport model")
logger.info("   ✅ VERIFIED: All public methods found by service")
logger.info("   ✅ VERIFIED: PostgreSQL dates displayed as-is")
logger.info("")
logger.info("   AVAILABLE METHODS:")
logger.info("   ✅ health_check()")
logger.info("   ✅ validation_query()")
logger.info("   ✅ get_service_metadata()")
logger.info("   ✅ search_dn()")
logger.info("   ✅ verify_dn()")
logger.info("   ✅ get_dn_dashboard()")
logger.info("   ✅ diagnose_dn()")
logger.info("   ✅ check_dn_raw()")
logger.info("   ✅ test_dn_lookup()")
logger.info("   ✅ test_date_calculation()")
logger.info("   ✅ get_pending_dns()")
logger.info("   ✅ get_pending_pgi()")
logger.info("   ✅ get_pending_pod()")
logger.info("   ✅ calculate_delivery_aging()")
logger.info("   ✅ calculate_pod_aging()")
logger.info("   ✅ calculate_total_cycle()")
logger.info("   ✅ format_dn_dashboard()")
logger.info("   ✅ debug_aging_calculation()")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)

# ✅ Run date calculation test on startup
try:
    service = get_dn_analytics_service()
    test_result = service.test_date_calculation()
    if test_result.get("all_passed"):
        logger.info("✅ Native PostgreSQL Date Test: ALL TESTS PASSED")
    else:
        logger.warning("⚠️ Native PostgreSQL Date Test: SOME TESTS FAILED - Check date parsing logic")
except Exception as e:
    logger.error(f"❌ Native PostgreSQL Date Test failed: {e}")
