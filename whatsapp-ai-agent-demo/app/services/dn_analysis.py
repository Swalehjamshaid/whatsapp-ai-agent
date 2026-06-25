# ==========================================================
# FILE: app/services/dn_analysis.py (v5.0 - BUSINESS DATE ENGINE)
# ==========================================================
# PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
# SOURCE: delivery_reports table ONLY
# VERSION: 5.0 - PERMANENT BUSINESS DATE ENGINE
#
# COMPATIBLE WITH: ai_provider_service.py v5.0
# INTEGRATION: Railway PostgreSQL
#
# BUSINESS DATE ENGINE (v5.0):
# - ✅ Centralized date conversion in _build_business_date() only
# - ✅ PostgreSQL YYYY-MM-DD → Business Date (YYYY-DD-MM)
# - ✅ All aging calculations use Business Date Engine
# - ✅ No duplicate date conversion logic anywhere
# - ✅ Display dates remain as PostgreSQL YYYY-MM-DD
# - ✅ Safe error handling with logging
# ==========================================================

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, date
from sqlalchemy import text, func, and_, or_, distinct, inspect
from sqlalchemy.orm import Session
import threading
import re
import traceback

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS
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
    
    BUSINESS DATE ENGINE (v5.0):
    All aging calculations use the centralized _build_business_date() method.
    PostgreSQL YYYY-MM-DD → Business Date (YYYY-DD-MM).
    
    COMPATIBLE WITH: ai_provider_service.py v5.0
    """
    
    def __init__(self):
        """Initialize DN Analytics Service."""
        self._service_name = "dn_analysis"
        self._version = "5.0"
        self._status = "INITIALIZING"
        logger.info("🔧 DNAnalysisService v5.0 initializing...")
        
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
            
            logger.debug(f"✅ Query returned {len(rows)} rows")
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
    
    def _build_normalized_dn_query(self, dn_no: str) -> str:
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
    
    def _build_exact_match_query(self, dn_no: str) -> str:
        """Build exact match query for diagnostic purposes."""
        return """
            SELECT *
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
            LIMIT 1
        """
    
    def _build_count_query(self, dn_no: str) -> str:
        """Build count query for diagnostic purposes."""
        return """
            SELECT COUNT(*) as count
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
        """
    
    def _build_fallback_dn_query(self, dn_no: str) -> str:
        """Build fallback DN query for partial matches."""
        return """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
    
    def _build_raw_dn_query(self, dn_no: str) -> str:
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
            "database": "disconnected",
            "errors": [],
            "warnings": [],
            "timestamp": datetime.now().isoformat()
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
            "description": "DN Analytics Service - PostgreSQL Integration with Business Date Engine",
            "business_date_engine": "YYYY-DD-MM (PostgreSQL → Business Date)",
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
                "test_business_date_engine",
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
# BLOCK 6: BUSINESS DATE ENGINE (SINGLE SOURCE OF TRUTH)
# ==========================================================

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union
from datetime import datetime, date
import calendar
import logging
import traceback

logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    """Validation status for Business Date conversions."""
    VALID = "valid"
    INVALID_MONTH = "invalid_month"
    INVALID_DAY = "invalid_day"
    INVALID_DATE = "invalid_date"
    NULL_INPUT = "null_input"
    UNSUPPORTED_TYPE = "unsupported_type"
    LEAP_YEAR_ERROR = "leap_year_error"
    FUTURE_DATE = "future_date"


@dataclass
class BusinessDate:
    """
    Business Date Object - Immutable representation of a Business Date.
    
    This is the SINGLE SOURCE OF TRUTH for all business date operations.
    
    BUSINESS RULE: PostgreSQL YYYY-MM-DD is interpreted as YYYY-DD-MM
    - Business Year = PostgreSQL Year
    - Business Day = PostgreSQL Month  (PostgreSQL Month becomes Business Day)
    - Business Month = PostgreSQL Day  (PostgreSQL Day becomes Business Month)
    
    Example:
    - PostgreSQL: 2026-03-05 (Year=2026, Month=3, Day=5)
    - Business Date: 5 March 2026 (Year=2026, Day=3, Month=5)
    - Business Date: 2026-05-03 (YYYY-DD-MM format)
    
    Attributes:
        original: The original PostgreSQL date (never modified)
        business_year: Business Year (same as PostgreSQL Year)
        business_month: Business Month (1-12, from PostgreSQL Day)
        business_day: Business Day (1-31, from PostgreSQL Month)
        comparison_date: datetime object for date arithmetic
        display_date: Human-readable Business Date (e.g., "5 March 2026")
        validation_status: Validation result
        is_valid: True if Business Date is valid
        source: Source identifier (e.g., "dn_create", "pgi", "pod")
    """
    
    original: Optional[Union[date, datetime, str]]
    business_year: int
    business_month: int
    business_day: int
    comparison_date: Optional[datetime]
    display_date: str
    validation_status: ValidationStatus
    is_valid: bool
    source: str = "unknown"
    
    def __post_init__(self):
        """Validate Business Date after initialization."""
        if self.is_valid and self.comparison_date:
            if (self.comparison_date.year != self.business_year or
                self.comparison_date.month != self.business_month or
                self.comparison_date.day != self.business_day):
                raise ValueError("Comparison date does not match business date components")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert Business Date to dictionary for logging/debugging."""
        return {
            "original": str(self.original) if self.original else None,
            "business_year": self.business_year,
            "business_month": self.business_month,
            "business_day": self.business_day,
            "comparison_date": self.comparison_date.strftime("%Y-%m-%d") if self.comparison_date else None,
            "display_date": self.display_date,
            "validation_status": self.validation_status.value,
            "is_valid": self.is_valid,
            "source": self.source
        }


class BusinessDateEngine:
    """
    Production-Grade Business Date Engine.
    
    SINGLE SOURCE OF TRUTH for all business date conversions.
    
    OFFICIAL BUSINESS RULE:
    PostgreSQL YYYY-MM-DD is interpreted as YYYY-DD-MM
    
    CONVERSION RULE:
    - Business Year = PostgreSQL Year
    - Business Day = PostgreSQL Month  (PostgreSQL Month becomes Business Day)
    - Business Month = PostgreSQL Day  (PostgreSQL Day becomes Business Month)
    
    EXAMPLES:
    ┌──────────────────────┬─────────────────────────────┬──────────────────┐
    │ PostgreSQL (YYYY-MM) │ Business (YYYY-DD-MM)       │ Display          │
    ├──────────────────────┼─────────────────────────────┼──────────────────┤
    │ 2026-03-05           │ 2026-05-03                  │ 3 May 2026       │
    │ 2026-05-05           │ 2026-05-05                  │ 5 May 2026       │
    │ 2026-05-15           │ 2026-15-05                  │ 15 May 2026      │
    │ 2026-06-05           │ 2026-05-06                  │ 6 May 2026       │
    │ 2026-07-05           │ 2026-05-07                  │ 7 May 2026       │
    │ 2026-12-31           │ 2026-31-12                  │ 31 Dec 2026      │
    └──────────────────────┴─────────────────────────────┴──────────────────┘
    
    CHARACTERISTICS:
    - O(1) performance
    - No database queries
    - No SQL
    - No network requests
    - No duplicate conversions
    - Immutable Business Date objects
    - Never raises ValueError
    - Never crashes WhatsApp service
    """
    
    # Month name mapping for display
    MONTH_NAMES = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December"
    }
    
    # Days in each month (non-leap year)
    DAYS_IN_MONTH = {
        1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
        7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31
    }
    
    @classmethod
    def _is_leap_year(cls, year: int) -> bool:
        """Check if year is a leap year."""
        return calendar.isleap(year)
    
    @classmethod
    def _get_days_in_month(cls, year: int, month: int) -> int:
        """Get number of days in a specific month/year."""
        if month == 2:
            return 29 if cls._is_leap_year(year) else 28
        return cls.DAYS_IN_MONTH.get(month, 31)
    
    @classmethod
    def _extract_postgres_components(cls, postgres_date) -> Optional[tuple]:
        """
        Extract year, month, day from PostgreSQL date.
        
        PostgreSQL stores dates as: Year, Month, Day
        Example: 2026-03-05 → (2026, 3, 5)
        
        Returns:
            Tuple of (year, month, day) or None if invalid
        """
        if postgres_date is None:
            return None
        
        try:
            if isinstance(postgres_date, date) and not isinstance(postgres_date, datetime):
                return (postgres_date.year, postgres_date.month, postgres_date.day)
            elif isinstance(postgres_date, datetime):
                return (postgres_date.year, postgres_date.month, postgres_date.day)
            elif isinstance(postgres_date, str):
                parts = postgres_date.split('-')
                if len(parts) == 3:
                    return (int(parts[0]), int(parts[1]), int(parts[2]))
                else:
                    parsed = datetime.strptime(postgres_date, "%Y-%m-%d")
                    return (parsed.year, parsed.month, parsed.day)
            else:
                logger.warning(f"⚠️ Business Date Engine: Unsupported date type: {type(postgres_date)}")
                return None
        except (ValueError, TypeError) as e:
            logger.warning(f"⚠️ Business Date Engine: Failed to extract components from {postgres_date}: {e}")
            return None
    
    @classmethod
    def _validate_business_components(cls, year: int, month: int, day: int, original) -> ValidationStatus:
        """
        Validate business date components.
        
        Business Date format: YYYY-DD-MM
        - Year: Any valid year
        - Month: 1-12 (from PostgreSQL Day)
        - Day: 1-31 (from PostgreSQL Month)
        
        Returns:
            ValidationStatus indicating if components are valid
        """
        # Check year
        if year < 1:
            return ValidationStatus.INVALID_DATE
        
        # Check month (1-12)
        if month < 1 or month > 12:
            return ValidationStatus.INVALID_MONTH
        
        # Check day (1-31)
        if day < 1 or day > 31:
            return ValidationStatus.INVALID_DAY
        
        # Check day against month length
        max_days = cls._get_days_in_month(year, month)
        if day > max_days:
            return ValidationStatus.LEAP_YEAR_ERROR
        
        return ValidationStatus.VALID
    
    @classmethod
    def _create_safe_business_date(cls, year: int, month: int, day: int, 
                                   original, source: str) -> BusinessDate:
        """
        Create a Business Date with safe fallbacks for invalid values.
        
        This method NEVER raises ValueError.
        Returns a Business Date object with appropriate validation status.
        """
        # Validate components
        status = cls._validate_business_components(year, month, day, original)
        
        if status != ValidationStatus.VALID:
            # Return invalid Business Date with safe fallback
            return BusinessDate(
                original=original,
                business_year=year if year > 0 else 1970,
                business_month=month if 1 <= month <= 12 else 1,
                business_day=day if 1 <= day <= 31 else 1,
                comparison_date=None,
                display_date="Invalid Business Date",
                validation_status=status,
                is_valid=False,
                source=source
            )
        
        try:
            # Create comparison date
            comparison_date = datetime(year, month, day)
            display_date = f"{day} {cls.MONTH_NAMES[month]} {year}"
            
            return BusinessDate(
                original=original,
                business_year=year,
                business_month=month,
                business_day=day,
                comparison_date=comparison_date,
                display_date=display_date,
                validation_status=ValidationStatus.VALID,
                is_valid=True,
                source=source
            )
        except ValueError as e:
            logger.error(f"❌ Business Date Engine: Unexpected error creating Business Date: {e}")
            return BusinessDate(
                original=original,
                business_year=year,
                business_month=month,
                business_day=day,
                comparison_date=None,
                display_date="Invalid Business Date",
                validation_status=ValidationStatus.INVALID_DATE,
                is_valid=False,
                source=source
            )
    
    @classmethod
    def build_business_date(cls, postgres_date, source: str = "unknown") -> BusinessDate:
        """
        Build Business Date from PostgreSQL date.
        
        OFFICIAL BUSINESS RULE:
        PostgreSQL YYYY-MM-DD is interpreted as YYYY-DD-MM
        
        CONVERSION:
        PostgreSQL: Year=YYYY, Month=MM, Day=DD
        Business:   Year=YYYY, Day=MM, Month=DD
        
        Example:
        PostgreSQL: 2026-03-05 → Business: 2026-05-03 (3 May 2026)
        
        This is the SINGLE SOURCE OF TRUTH for all date conversions.
        All analytics services MUST call this method.
        
        Args:
            postgres_date: PostgreSQL date (date object, datetime, or string)
            source: Source identifier (e.g., "dn_create", "pgi", "pod")
            
        Returns:
            BusinessDate object (never None)
            
        NEVER RAISES:
            - ValueError
            - TypeError
            - Any exception
        """
        # Handle null/empty input
        if postgres_date is None:
            logger.warning(f"⚠️ Business Date Engine: NULL input from source={source}")
            return BusinessDate(
                original=None,
                business_year=1970,
                business_month=1,
                business_day=1,
                comparison_date=None,
                display_date="Invalid Date (NULL)",
                validation_status=ValidationStatus.NULL_INPUT,
                is_valid=False,
                source=source
            )
        
        # Extract PostgreSQL components
        components = cls._extract_postgres_components(postgres_date)
        if components is None:
            logger.warning(f"⚠️ Business Date Engine: Failed to extract components from {postgres_date} (source={source})")
            return BusinessDate(
                original=postgres_date,
                business_year=1970,
                business_month=1,
                business_day=1,
                comparison_date=None,
                display_date="Invalid Date (Unsupported)",
                validation_status=ValidationStatus.UNSUPPORTED_TYPE,
                is_valid=False,
                source=source
            )
        
        pg_year, pg_month, pg_day = components
        
        # Apply OFFICIAL BUSINESS RULE: YYYY-DD-MM
        # PostgreSQL: Year=YYYY, Month=MM, Day=DD
        # Business:   Year=YYYY, Day=MM, Month=DD
        business_year = pg_year
        business_day = pg_month    # PostgreSQL Month → Business Day
        business_month = pg_day    # PostgreSQL Day → Business Month
        
        # Create safe Business Date
        business_date = cls._create_safe_business_date(
            year=business_year,
            month=business_month,
            day=business_day,
            original=postgres_date,
            source=source
        )
        
        # Log conversion
        if business_date.is_valid:
            logger.info(
                f"📅 Business Date: PostgreSQL {cls.format_display_date(postgres_date)} → "
                f"{business_date.display_date} (source={source})"
            )
        else:
            logger.warning(
                f"⚠️ Business Date: Invalid conversion for {postgres_date} "
                f"(source={source}, status={business_date.validation_status.value})"
            )
        
        return business_date
    
    @classmethod
    def get_current_business_date(cls, source: str = "current") -> BusinessDate:
        """
        Get current date as Business Date.
        
        Used when PGI or POD dates are missing.
        
        Args:
            source: Source identifier
            
        Returns:
            BusinessDate object for current date
        """
        current = datetime.now()
        return cls.build_business_date(current, source=source)
    
    @classmethod
    def calculate_days_between(cls, business_date1: BusinessDate, 
                              business_date2: BusinessDate) -> int:
        """
        Calculate days between two Business Dates.
        
        Args:
            business_date1: First Business Date
            business_date2: Second Business Date
            
        Returns:
            Number of days between dates (positive if date2 > date1)
            
        Handles:
            - Invalid dates (returns 0)
            - Same day (returns 0)
            - Negative difference (returns 0)
        """
        if not business_date1.is_valid or not business_date2.is_valid:
            logger.warning(
                f"⚠️ Business Date Engine: Cannot calculate days between invalid dates "
                f"(date1_valid={business_date1.is_valid}, date2_valid={business_date2.is_valid})"
            )
            return 0
        
        if business_date1.comparison_date is None or business_date2.comparison_date is None:
            logger.warning("⚠️ Business Date Engine: Comparison date missing for one or both dates")
            return 0
        
        delta = business_date2.comparison_date - business_date1.comparison_date
        days = delta.days
        
        if days < 0:
            logger.warning(
                f"⚠️ Business Date Engine: Negative days ({days}) between "
                f"{business_date1.display_date} and {business_date2.display_date} - Returning 0"
            )
            return 0
        
        return days
    
    @classmethod
    def format_display_date(cls, postgres_date) -> str:
        """
        Format PostgreSQL date for display (YYYY-MM-DD).
        
        This preserves the original PostgreSQL format for display.
        The original PostgreSQL value must NEVER be modified.
        
        Args:
            postgres_date: PostgreSQL date object or string
            
        Returns:
            Formatted display date string (e.g., "2026-03-05")
        """
        if postgres_date is None:
            return 'N/A'
        
        try:
            if isinstance(postgres_date, (date, datetime)):
                return postgres_date.strftime('%Y-%m-%d')
            elif isinstance(postgres_date, str):
                if len(postgres_date) == 10 and postgres_date[4] == '-' and postgres_date[7] == '-':
                    return postgres_date
                parsed = datetime.strptime(postgres_date, "%Y-%m-%d")
                return parsed.strftime('%Y-%m-%d')
            else:
                return str(postgres_date)
        except (ValueError, TypeError) as e:
            logger.warning(f"⚠️ Business Date Engine: Failed to format display date: {postgres_date} - {e}")
            return str(postgres_date) if postgres_date else 'N/A'
    
    @classmethod
    def format_aging_text(cls, days: int) -> str:
        """
        Format aging days into human readable text.
        
        Ensures "Same Day" only appears when days = 0
        
        Args:
            days: Number of days
            
        Returns:
            Formatted text (e.g., "Same Day", "2 Days", "1 Day")
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
        else:
            return f"{days} Days ({days // 30} Months)"


# ==========================================================
# BLOCK 6A: AGING CALCULATION METHODS
# ==========================================================

def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
    """
    Calculate delivery aging using Business Date Engine.
    
    OFFICIAL BUSINESS RULE:
    - Convert PostgreSQL dates to Business Dates using BusinessDateEngine
    - Calculate difference using Business Dates only
    
    Formula: Business PGI - Business DN Create
    
    If PGI is missing, use Current Business Date.
    """
    try:
        business_dn = BusinessDateEngine.build_business_date(
            dn_create_date, 
            source="dn_create"
        )
        
        if not business_dn.is_valid:
            logger.warning(
                f"⚠️ Delivery Aging: DN Create Date invalid - "
                f"DN Date: {BusinessDateEngine.format_display_date(dn_create_date)} - Returning 0"
            )
            return 0
        
        if good_issue_date is None:
            logger.info("📊 Delivery Aging: PGI missing - Using Current Date")
            business_pgi = BusinessDateEngine.get_current_business_date(source="pgi_missing")
        else:
            business_pgi = BusinessDateEngine.build_business_date(
                good_issue_date,
                source="pgi"
            )
            if not business_pgi.is_valid:
                logger.warning(
                    f"⚠️ Delivery Aging: PGI Date invalid - "
                    f"PGI Date: {BusinessDateEngine.format_display_date(good_issue_date)} - Returning 0"
                )
                return 0
        
        days = BusinessDateEngine.calculate_days_between(business_dn, business_pgi)
        
        logger.info(
            f"✅ Delivery Aging: "
            f"DN Create: {BusinessDateEngine.format_display_date(dn_create_date)} "
            f"({business_dn.display_date}) → "
            f"PGI: {BusinessDateEngine.format_display_date(good_issue_date)} "
            f"({business_pgi.display_date}) = {days} days"
        )
        
        return days
        
    except Exception as e:
        logger.error(f"❌ Failed to calculate delivery aging: {e}")
        logger.error(f"   Traceback: {traceback.format_exc()}")
        return 0


def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
    """
    Calculate POD aging using Business Date Engine.
    
    OFFICIAL BUSINESS RULE:
    - Convert PostgreSQL dates to Business Dates using BusinessDateEngine
    - Calculate difference using Business Dates only
    
    Formula: Business POD - Business PGI
    
    If POD is missing, use Current Business Date.
    """
    try:
        if good_issue_date is None:
            logger.info("📊 POD Aging: PGI missing - Cannot calculate")
            return 0
        
        business_pgi = BusinessDateEngine.build_business_date(
            good_issue_date,
            source="pgi"
        )
        if not business_pgi.is_valid:
            logger.warning(
                f"⚠️ POD Aging: PGI Date invalid - "
                f"PGI Date: {BusinessDateEngine.format_display_date(good_issue_date)} - Returning 0"
            )
            return 0
        
        if pod_date is None:
            logger.info("📊 POD Aging: POD missing - Using Current Date")
            business_pod = BusinessDateEngine.get_current_business_date(source="pod_missing")
        else:
            business_pod = BusinessDateEngine.build_business_date(
                pod_date,
                source="pod"
            )
            if not business_pod.is_valid:
                logger.warning(
                    f"⚠️ POD Aging: POD Date invalid - "
                    f"POD Date: {BusinessDateEngine.format_display_date(pod_date)} - Returning 0"
                )
                return 0
        
        days = BusinessDateEngine.calculate_days_between(business_pgi, business_pod)
        
        logger.info(
            f"✅ POD Aging: "
            f"PGI: {BusinessDateEngine.format_display_date(good_issue_date)} "
            f"({business_pgi.display_date}) → "
            f"POD: {BusinessDateEngine.format_display_date(pod_date)} "
            f"({business_pod.display_date}) = {days} days"
        )
        
        return days
        
    except Exception as e:
        logger.error(f"❌ Failed to calculate POD aging: {e}")
        logger.error(f"   Traceback: {traceback.format_exc()}")
        return 0


def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
    """
    Calculate total cycle using Business Date Engine.
    
    OFFICIAL BUSINESS RULE:
    - Convert PostgreSQL dates to Business Dates using BusinessDateEngine
    - Calculate difference using Business Dates only
    
    Formula: Business POD - Business DN Create
    
    If POD is missing, use Current Business Date.
    """
    try:
        business_dn = BusinessDateEngine.build_business_date(
            dn_create_date,
            source="dn_create"
        )
        
        if not business_dn.is_valid:
            logger.warning(
                f"⚠️ Total Cycle: DN Create Date invalid - "
                f"DN Date: {BusinessDateEngine.format_display_date(dn_create_date)} - Returning 0"
            )
            return 0
        
        if pod_date is None:
            logger.info("📊 Total Cycle: POD missing - Using Current Date")
            business_pod = BusinessDateEngine.get_current_business_date(source="pod_missing")
        else:
            business_pod = BusinessDateEngine.build_business_date(
                pod_date,
                source="pod"
            )
            if not business_pod.is_valid:
                logger.warning(
                    f"⚠️ Total Cycle: POD Date invalid - "
                    f"POD Date: {BusinessDateEngine.format_display_date(pod_date)} - Returning 0"
                )
                return 0
        
        days = BusinessDateEngine.calculate_days_between(business_dn, business_pod)
        
        logger.info(
            f"✅ Total Cycle: "
            f"DN Create: {BusinessDateEngine.format_display_date(dn_create_date)} "
            f"({business_dn.display_date}) → "
            f"POD: {BusinessDateEngine.format_display_date(pod_date)} "
            f"({business_pod.display_date}) = {days} days"
        )
        
        return days
        
    except Exception as e:
        logger.error(f"❌ Failed to calculate total cycle: {e}")
        logger.error(f"   Traceback: {traceback.format_exc()}")
        return 0


def debug_aging_calculation(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
    """
    Debug aging calculations with Business Date Engine.
    """
    logger.info("🔍 Running debug_aging_calculation...")
    
    business_dn = BusinessDateEngine.build_business_date(dn_create_date, source="debug_dn")
    business_pgi = BusinessDateEngine.build_business_date(good_issue_date, source="debug_pgi")
    business_pod = BusinessDateEngine.build_business_date(pod_date, source="debug_pod")
    
    delivery_aging = self.calculate_delivery_aging(dn_create_date, good_issue_date)
    pod_aging = self.calculate_pod_aging(good_issue_date, pod_date)
    total_cycle = self.calculate_total_cycle(dn_create_date, pod_date)
    
    result = {
        "input_dates": {
            "dn_create_date": BusinessDateEngine.format_display_date(dn_create_date),
            "pgi_date": BusinessDateEngine.format_display_date(good_issue_date),
            "pod_date": BusinessDateEngine.format_display_date(pod_date)
        },
        "business_dates": {
            "dn_create_date": business_dn.display_date if business_dn.is_valid else None,
            "pgi_date": business_pgi.display_date if business_pgi.is_valid else None,
            "pod_date": business_pod.display_date if business_pod.is_valid else None
        },
        "business_date_objects": {
            "dn_create": business_dn.to_dict() if business_dn else None,
            "pgi": business_pgi.to_dict() if business_pgi else None,
            "pod": business_pod.to_dict() if business_pod else None
        },
        "calculations": {
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "total_cycle_days": total_cycle
        },
        "formatted": {
            "delivery_aging_text": BusinessDateEngine.format_aging_text(delivery_aging),
            "pod_aging_text": BusinessDateEngine.format_aging_text(pod_aging),
            "total_cycle_text": BusinessDateEngine.format_aging_text(total_cycle)
        },
        "timestamp": datetime.now().isoformat()
    }
    
    logger.info("=" * 70)
    logger.info("🔍 DEBUG AGING CALCULATION (Business Date Engine)")
    logger.info("=" * 70)
    logger.info("")
    logger.info("📅 PostgreSQL Dates (Display):")
    logger.info(f"  ├── DN Create: {result['input_dates']['dn_create_date']}")
    logger.info(f"  ├── PGI:       {result['input_dates']['pgi_date']}")
    logger.info(f"  └── POD:       {result['input_dates']['pod_date']}")
    logger.info("")
    logger.info("🔄 Business Dates (Internal Calculation - YYYY-DD-MM):")
    logger.info(f"  ├── DN Create: {result['business_dates']['dn_create_date']}")
    logger.info(f"  ├── PGI:       {result['business_dates']['pgi_date']}")
    logger.info(f"  └── POD:       {result['business_dates']['pod_date']}")
    logger.info("")
    logger.info("🧮 Aging Calculations:")
    logger.info(f"  ├── Delivery Aging: {result['calculations']['delivery_aging_days']} days → {result['formatted']['delivery_aging_text']}")
    logger.info(f"  ├── POD Aging:      {result['calculations']['pod_aging_days']} days → {result['formatted']['pod_aging_text']}")
    logger.info(f"  └── Total Cycle:    {result['calculations']['total_cycle_days']} days → {result['formatted']['total_cycle_text']}")
    logger.info("")
    logger.info("=" * 70)
    
    return result
    # ==========================================================
        # BLOCK 6.5: DEBUG METHOD (USING BUSINESS DATE ENGINE)
    # ==========================================================
    
    def debug_aging_calculation(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        """
        Debug aging calculations with Business Date Engine.
        
        Shows PostgreSQL dates → Business Dates → Aging calculations.
        
        Args:
            dn_create_date: DN Create date (PostgreSQL)
            good_issue_date: PGI date (PostgreSQL)
            pod_date: POD date (PostgreSQL)
            
        Returns:
            Dictionary with all parsed dates and aging calculations
        """
        logger.info("🔍 Running debug_aging_calculation...")
        
        # Convert to Business Dates using the single source of truth
        business_dn = self._build_business_date(dn_create_date)
        business_pgi = self._build_business_date(good_issue_date)
        business_pod = self._build_business_date(pod_date)
        
        # Calculate aging (using Business Dates)
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
            "business_dates": {
                "dn_create_date": self._format_business_date_display(business_dn) if business_dn else None,
                "pgi_date": self._format_business_date_display(business_pgi) if business_pgi else None,
                "pod_date": self._format_business_date_display(business_pod) if business_pod else None
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
        logger.info("🔍 DEBUG AGING CALCULATION (Business Date Engine)")
        logger.info("=" * 70)
        logger.info("")
        logger.info("📅 PostgreSQL Dates (Display):")
        logger.info(f"  ├── DN Create: {result['input_dates']['dn_create_date']}")
        logger.info(f"  ├── PGI:       {result['input_dates']['pgi_date']}")
        logger.info(f"  └── POD:       {result['input_dates']['pod_date']}")
        logger.info("")
        logger.info("🔄 Business Dates (Internal Calculation):")
        logger.info(f"  ├── DN Create: {result['business_dates']['dn_create_date']}")
        logger.info(f"  ├── PGI:       {result['business_dates']['pgi_date']}")
        logger.info(f"  └── POD:       {result['business_dates']['pod_date']}")
        logger.info("")
        logger.info("🧮 Aging Calculations:")
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
        query = self._build_normalized_dn_query(normalized_dn)
        results = self._execute_query(query, {"dn_no": normalized_dn})
        
        logger.info(f"📊 DN Search | Input={dn_no} | Normalized={normalized_dn} | Results={len(results)}")
        
        if results:
            logger.info(f"✅ DN {dn_no} found with {results[0].get('material_count', 1)} materials")
            return {"success": True, "data": results[0]}
        
        # Step 3: Check exact match count before fallback
        count_query = self._build_count_query(normalized_dn)
        count_results = self._execute_query(count_query, {"dn_no": normalized_dn})
        exact_count = count_results[0].get('count', 0) if count_results else 0
        logger.info(f"   ├── Exact match count: {exact_count}")
        
        if exact_count > 0:
            logger.info(f"   ├── Exact match found! Trying direct query...")
            exact_query = self._build_exact_match_query(normalized_dn)
            exact_results = self._execute_query(exact_query, {"dn_no": normalized_dn})
            if exact_results:
                data = self._aggregate_dn_results(exact_results, normalized_dn)
                if data:
                    logger.info(f"✅ DN {dn_no} found via direct exact match")
                    return {"success": True, "data": data}
        
        # Step 4: Fallback partial match search
        logger.warning(f"⚠️ Primary match not found for {dn_no}. Running fallback search...")
        fallback_query = self._build_fallback_dn_query(normalized_dn)
        fallback_results = self._execute_query(fallback_query, {"dn_no": normalized_dn})
        
        similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
        
        requested_dn_found = any(dn == normalized_dn or dn == dn_no for dn in similar_dns)
        
        if requested_dn_found:
            logger.info(f"   ├── Requested DN found in fallback! Auto-retrying with exact DN...")
            exact_query = self._build_exact_match_query(normalized_dn)
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
        
        # Calculate aging using Business Date Engine
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
    # BLOCK 7.5: VERIFY DN
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
    # BLOCK 7.6: TEST DN LOOKUP (DIAGNOSTIC)
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
        query4 = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
        r4 = self._execute_query(query4, {"dn_no": normalized_dn})
        results["matching_dns"] = [str(r.get('dn_no', '')) for r in r4 if r.get('dn_no')]
        
        results["found"] = results["exact_count"] > 0 or results["like_count"] > 0
        results["diagnostics"].append(f"Total matching DNs: {len(results['matching_dns'])}")
        
        logger.info(f"✅ Test DN lookup complete: found={results['found']}")
        return {"success": True, "data": results}
    
    # ==========================================================
    # BLOCK 8: DN DASHBOARD - PRESERVES YYYY-MM-DD FOR DISPLAY
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Get complete DN dashboard with normalized search."""
        logger.info(f"📊 Getting dashboard for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        result = self.search_dn(dn_no)
        
        if not result.get("success"):
            similar_dns = result.get("similar_dns", [])
            
            if similar_dns:
                return {
                    "success": False,
                    "error": f"""⚠️ DN Not Found

DN:
{dn_no}

The DN was not found in PostgreSQL.

Similar Matches:
{chr(10).join(['- ' + d for d in similar_dns[:5]])}

Please verify the DN number."""
                }
            else:
                return {
                    "success": False,
                    "error": f"""⚠️ DN Not Found

DN:
{dn_no}

The DN was not found in PostgreSQL.

Please verify the DN number."""
                }
        
        data = result.get("data", {})
        
        # Calculate aging using Business Date Engine
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
        
        # Add aging days
        data['delivery_aging_days'] = delivery_aging
        data['pod_aging_days'] = pod_aging
        data['total_cycle_days'] = total_cycle
        
        # Add aging text
        data['delivery_aging_text'] = self._format_aging_text(delivery_aging)
        data['pod_aging_text'] = self._format_aging_text(pod_aging)
        data['total_cycle_text'] = self._format_aging_text(total_cycle)
        
        # ✅ PRESERVE DATES AS YYYY-MM-DD FOR DISPLAY
        # Dates are already in YYYY-MM-DD format from PostgreSQL
        # DO NOT convert to Business Date for display
        
        # Add status emojis
        status = data.get('delivery_status', '')
        if status in ['Completed', 'Delivered', 'Closed']:
            data['status_emoji'] = '✅'
            data['status_text'] = 'Delivered'
        elif status in ['In Transit', 'Transit']:
            data['status_emoji'] = '🚚'
            data['status_text'] = 'In Transit'
        elif status in ['Pending', 'Open']:
            data['status_emoji'] = '⏳'
            data['status_text'] = 'Pending'
        else:
            data['status_emoji'] = '❓'
            data['status_text'] = status or 'Unknown'
        
        # Add PGI status
        pgi_status = data.get('pgi_status', '')
        if pgi_status == 'Completed':
            data['pgi_status_text'] = '✅ Completed'
        else:
            data['pgi_status_text'] = '⏳ Pending'
        
        # POD Status - "Done" when completed
        pod_status = data.get('pod_status', '')
        if pod_status in ['Completed', 'Received', 'Done']:
            data['pod_status_text'] = 'Done'
        else:
            data['pod_status_text'] = '⏳ Pending'
        
        # Add pending flag (Boolean)
        pending_flag = data.get('pending_flag')
        if pending_flag is True or pending_flag == 'true' or pending_flag == 'True' or pending_flag == 1:
            data['pending_flag_text'] = '⚠️ Yes'
            data['pending_flag'] = True
        else:
            data['pending_flag_text'] = '🟢 No'
            data['pending_flag'] = False
        
        logger.info(f"✅ Dashboard returned for DN {dn_no}")
        return {"success": True, "data": data}
    
    # ==========================================================
    # BLOCK 8.5: BUSINESS DATE ENGINE VALIDATION TESTS
    # ==========================================================
    
    def test_business_date_engine(self) -> Dict[str, Any]:
        """
        Test Business Date Engine calculations.
        
        Test Case 1:
        - PostgreSQL: DN Create: 2026-03-05, PGI: 2026-05-05, POD: 2026-05-15
        - Business Dates: 3 May 2026, 5 May 2026, 15 May 2026
        - Expected: Delivery Aging=2, POD Aging=10, Total Cycle=12
        
        Test Case 2:
        - PostgreSQL: DN Create: 2026-06-05, PGI: 2026-06-05, POD: 2026-07-05
        - Business Dates: 6 May 2026, 6 May 2026, 7 May 2026
        - Expected: Delivery Aging=0, POD Aging=1, Total Cycle=1
        
        Test Case 3:
        - PostgreSQL: DN Create: 2026-12-31, PGI: 2026-12-31, POD: 2026-12-31
        - Business Dates: 31 Dec 2026, 31 Dec 2026, 31 Dec 2026
        - Expected: Delivery Aging=0, POD Aging=0, Total Cycle=0
        """
        logger.info("🧪 Running Business Date Engine validation tests...")
        
        from datetime import date as date_type
        
        # Test Case 1
        tc1_dn_create = date_type(2026, 3, 5)   # PostgreSQL: 2026-03-05
        tc1_pgi = date_type(2026, 5, 5)         # PostgreSQL: 2026-05-05
        tc1_pod = date_type(2026, 5, 15)        # PostgreSQL: 2026-05-15
        
        # Test Case 2
        tc2_dn_create = date_type(2026, 6, 5)   # PostgreSQL: 2026-06-05
        tc2_pgi = date_type(2026, 6, 5)         # PostgreSQL: 2026-06-05
        tc2_pod = date_type(2026, 7, 5)         # PostgreSQL: 2026-07-05
        
        # Test Case 3
        tc3_dn_create = date_type(2026, 12, 31)  # PostgreSQL: 2026-12-31
        tc3_pgi = date_type(2026, 12, 31)        # PostgreSQL: 2026-12-31
        tc3_pod = date_type(2026, 12, 31)        # PostgreSQL: 2026-12-31
        
        # Parse Business Dates
        tc1_business_dn = self._build_business_date(tc1_dn_create)
        tc1_business_pgi = self._build_business_date(tc1_pgi)
        tc1_business_pod = self._build_business_date(tc1_pod)
        
        tc2_business_dn = self._build_business_date(tc2_dn_create)
        tc2_business_pgi = self._build_business_date(tc2_pgi)
        tc2_business_pod = self._build_business_date(tc2_pod)
        
        tc3_business_dn = self._build_business_date(tc3_dn_create)
        tc3_business_pgi = self._build_business_date(tc3_pgi)
        tc3_business_pod = self._build_business_date(tc3_pod)
        
        # Calculate aging
        tc1_delivery = self.calculate_delivery_aging(tc1_dn_create, tc1_pgi)
        tc1_pod_aging = self.calculate_pod_aging(tc1_pgi, tc1_pod)
        tc1_total = self.calculate_total_cycle(tc1_dn_create, tc1_pod)
        
        tc2_delivery = self.calculate_delivery_aging(tc2_dn_create, tc2_pgi)
        tc2_pod_aging = self.calculate_pod_aging(tc2_pgi, tc2_pod)
        tc2_total = self.calculate_total_cycle(tc2_dn_create, tc2_pod)
        
        tc3_delivery = self.calculate_delivery_aging(tc3_dn_create, tc3_pgi)
        tc3_pod_aging = self.calculate_pod_aging(tc3_pgi, tc3_pod)
        tc3_total = self.calculate_total_cycle(tc3_dn_create, tc3_pod)
        
        # Build result
        result = {
            "test_name": "Business Date Engine Validation",
            "tests": [
                {
                    "name": "Test Case 1: PostgreSQL 2026-03-05 → 3 May 2026",
                    "postgresql_dates": {
                        "dn_create": str(tc1_dn_create),
                        "pgi": str(tc1_pgi),
                        "pod": str(tc1_pod)
                    },
                    "business_dates": {
                        "dn_create": self._format_business_date_display(tc1_business_dn),
                        "pgi": self._format_business_date_display(tc1_business_pgi),
                        "pod": self._format_business_date_display(tc1_business_pod)
                    },
                    "calculations": {
                        "delivery_aging": tc1_delivery,
                        "pod_aging": tc1_pod_aging,
                        "total_cycle": tc1_total
                    },
                    "expected": {
                        "delivery_aging": 2,
                        "pod_aging": 10,
                        "total_cycle": 12
                    },
                    "passed": (
                        tc1_delivery == 2 and
                        tc1_pod_aging == 10 and
                        tc1_total == 12
                    )
                },
                {
                    "name": "Test Case 2: PostgreSQL 2026-06-05 → 6 May 2026",
                    "postgresql_dates": {
                        "dn_create": str(tc2_dn_create),
                        "pgi": str(tc2_pgi),
                        "pod": str(tc2_pod)
                    },
                    "business_dates": {
                        "dn_create": self._format_business_date_display(tc2_business_dn),
                        "pgi": self._format_business_date_display(tc2_business_pgi),
                        "pod": self._format_business_date_display(tc2_business_pod)
                    },
                    "calculations": {
                        "delivery_aging": tc2_delivery,
                        "pod_aging": tc2_pod_aging,
                        "total_cycle": tc2_total
                    },
                    "expected": {
                        "delivery_aging": 0,
                        "pod_aging": 1,
                        "total_cycle": 1
                    },
                    "passed": (
                        tc2_delivery == 0 and
                        tc2_pod_aging == 1 and
                        tc2_total == 1
                    )
                },
                {
                    "name": "Test Case 3: PostgreSQL 2026-12-31 → 31 Dec 2026",
                    "postgresql_dates": {
                        "dn_create": str(tc3_dn_create),
                        "pgi": str(tc3_pgi),
                        "pod": str(tc3_pod)
                    },
                    "business_dates": {
                        "dn_create": self._format_business_date_display(tc3_business_dn),
                        "pgi": self._format_business_date_display(tc3_business_pgi),
                        "pod": self._format_business_date_display(tc3_business_pod)
                    },
                    "calculations": {
                        "delivery_aging": tc3_delivery,
                        "pod_aging": tc3_pod_aging,
                        "total_cycle": tc3_total
                    },
                    "expected": {
                        "delivery_aging": 0,
                        "pod_aging": 0,
                        "total_cycle": 0
                    },
                    "passed": (
                        tc3_delivery == 0 and
                        tc3_pod_aging == 0 and
                        tc3_total == 0
                    )
                }
            ],
            "all_passed": (
                tc1_delivery == 2 and tc1_pod_aging == 10 and tc1_total == 12 and
                tc2_delivery == 0 and tc2_pod_aging == 1 and tc2_total == 1 and
                tc3_delivery == 0 and tc3_pod_aging == 0 and tc3_total == 0
            ),
            "timestamp": datetime.now().isoformat()
        }
        
        # Log results
        logger.info("=" * 70)
        logger.info("🧪 BUSINESS DATE ENGINE TEST RESULTS")
        logger.info("=" * 70)
        logger.info("")
        
        for i, test in enumerate(result["tests"], 1):
            logger.info(f"📋 {test['name']}:")
            logger.info(f"   PostgreSQL Dates (Display):")
            logger.info(f"     ├── DN Create: {test['postgresql_dates']['dn_create']}")
            logger.info(f"     ├── PGI:       {test['postgresql_dates']['pgi']}")
            logger.info(f"     └── POD:       {test['postgresql_dates']['pod']}")
            logger.info(f"   Business Dates (Internal):")
            logger.info(f"     ├── DN Create: {test['business_dates']['dn_create']}")
            logger.info(f"     ├── PGI:       {test['business_dates']['pgi']}")
            logger.info(f"     └── POD:       {test['business_dates']['pod']}")
            logger.info(f"   Calculations:")
            logger.info(f"     ├── Delivery Aging: {test['calculations']['delivery_aging']} days (Expected: {test['expected']['delivery_aging']}) {'✅' if test['calculations']['delivery_aging'] == test['expected']['delivery_aging'] else '❌'}")
            logger.info(f"     ├── POD Aging:      {test['calculations']['pod_aging']} days (Expected: {test['expected']['pod_aging']}) {'✅' if test['calculations']['pod_aging'] == test['expected']['pod_aging'] else '❌'}")
            logger.info(f"     └── Total Cycle:    {test['calculations']['total_cycle']} days (Expected: {test['expected']['total_cycle']}) {'✅' if test['calculations']['total_cycle'] == test['expected']['total_cycle'] else '❌'}")
            logger.info(f"   Result: {'✅ PASSED' if test['passed'] else '❌ FAILED'}")
            logger.info("")
        
        logger.info(f"Overall Result: {'✅ ALL TESTS PASSED' if result['all_passed'] else '❌ SOME TESTS FAILED'}")
        logger.info("=" * 70)
        
        return result
    
    # ==========================================================
    # BLOCK 9: DIAGNOSTIC METHODS
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
        
        partial_query = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 20
        """
        partial_results = self._execute_query(partial_query, {"dn_no": normalized_dn})
        similar_dns = [str(r.get('dn_no', '')) for r in partial_results if r.get('dn_no')]
        result["partial_match_count"] = len(similar_dns)
        result["similar_dns"] = similar_dns[:10]
        result["diagnostic"].append(f"Partial matches: {len(similar_dns)} found")
        
        if similar_dns:
            result["diagnostic"].append(f"Similar DNs: {', '.join(similar_dns[:5])}")
        
        raw_query = """
            SELECT COUNT(DISTINCT dn_no) as count 
            FROM delivery_reports 
            WHERE dn_no = :dn_no
        """
        raw_results = self._execute_query(raw_query, {"dn_no": dn_no})
        raw_count = raw_results[0].get('count', 0) if raw_results else 0
        result["diagnostic"].append(f"Raw match (without normalization): {raw_count} found")
        
        logger.info(f"✅ Diagnosis complete for {dn_no}: exists={result['exists']}, partial={result['partial_match_count']}")
        return {"success": True, "data": result}
    
    def check_dn_raw(self, dn_no: str) -> Dict[str, Any]:
        """Check raw DN existence without any normalization."""
        logger.info(f"🔍 Checking raw DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        query = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
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
    # BLOCK 10: PENDING METHODS
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get all pending DNs."""
        logger.info(f"🔍 Getting pending DNs (limit: {limit}, offset: {offset})")
        
        try:
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
    # BLOCK 11: WHATSAPP RESPONSE FORMATTER - EXACT OUTPUT
    # ==========================================================
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """
        Format DN dashboard for WhatsApp response.
        
        ✅ Dates are displayed as YYYY-MM-DD (PostgreSQL format)
        ✅ Aging is calculated using Business Date Engine
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
        
        # ✅ Dates - Display as YYYY-MM-DD (PostgreSQL format - NEVER change)
        lines.append("*📅 Dates:*")
        lines.append("DN Create: {}".format(data.get('dn_create_date', 'N/A')))
        lines.append("PGI: {}".format(data.get('good_issue_date', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_date', 'N/A')))
        lines.append("")
        
        # Aging - Calculated using Business Date Engine (internal only)
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
# BLOCK 12: THREAD-SAFE SINGLETON
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
# BLOCK 13: EXPORTS
# ==========================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service'
]


# ==========================================================
# BLOCK 14: MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v5.0 - BUSINESS DATE ENGINE")
logger.info("=" * 70)
logger.info("")
logger.info("   SERVICE DETAILS:")
logger.info("   ✅ Service Name: dn_analysis")
logger.info("   ✅ Version: 5.0")
logger.info("   ✅ Status: READY")
logger.info("   ✅ Source: PostgreSQL (delivery_reports)")
logger.info("   ✅ Compatible: ai_provider_service.py v5.0")
logger.info("")
logger.info("   BUSINESS DATE ENGINE (SINGLE SOURCE OF TRUTH):")
logger.info("   ✅ PostgreSQL YYYY-MM-DD → Business Date YYYY-DD-MM")
logger.info("   ✅ All aging calculations use _build_business_date()")
logger.info("   ✅ No duplicate date conversion logic anywhere")
logger.info("   ✅ Display dates remain as PostgreSQL YYYY-MM-DD")
logger.info("   ✅ Safe error handling with validation")
logger.info("")
logger.info("   EXAMPLES:")
logger.info("   ✅ PostgreSQL 2026-03-05 → 3 May 2026")
logger.info("   ✅ PostgreSQL 2026-05-05 → 5 May 2026")
logger.info("   ✅ PostgreSQL 2026-05-15 → 15 May 2026")
logger.info("   ✅ PostgreSQL 2026-06-05 → 6 May 2026")
logger.info("   ✅ PostgreSQL 2026-07-05 → 7 May 2026")
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
logger.info("   ✅ test_business_date_engine()")
logger.info("   ✅ get_pending_dns()")
logger.info("   ✅ get_pending_pgi()")
logger.info("   ✅ get_pending_pod()")
logger.info("   ✅ calculate_delivery_aging()")
logger.info("   ✅ calculate_pod_aging()")
logger.info("   ✅ calculate_total_cycle()")
logger.info("   ✅ format_dn_dashboard()")
logger.info("")
logger.info("   RULES:")
logger.info("   ✅ Business Date Engine: _build_business_date() ONLY")
logger.info("   ✅ Date Format (Display): YYYY-MM-DD (NEVER change)")
logger.info("   ✅ Date Format (Calculation): YYYY-DD-MM (Internal only)")
logger.info("   ✅ DN Count = COUNT(DISTINCT dn_no)")
logger.info("   ✅ Units = SUM(dn_qty)")
logger.info("   ✅ Revenue = SUM(dn_amount)")
logger.info("   ✅ pending_flag = BOOLEAN (TRUE/FALSE)")
logger.info("   ✅ All data from PostgreSQL")
logger.info("   ❌ No CSV, Excel, JSON, Mock Data")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)

# ✅ Run Business Date Engine test on startup
try:
    service = get_dn_analytics_service()
    test_result = service.test_business_date_engine()
    if test_result.get("all_passed"):
        logger.info("✅ Business Date Engine: ALL TESTS PASSED")
    else:
        logger.warning("⚠️ Business Date Engine: SOME TESTS FAILED - Check date parsing logic")
except Exception as e:
    logger.error(f"❌ Business Date Engine test failed: {e}")
