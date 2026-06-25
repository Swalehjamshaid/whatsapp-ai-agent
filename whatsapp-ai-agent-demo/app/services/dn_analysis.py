# ==========================================================
# FILE: app/services/dn_analysis.py (v6.1 - FIXED)
# ==========================================================
# PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
# SOURCE: delivery_reports table ONLY
# VERSION: 6.1 - HEALTH CHECK FIXED
#
# COMPATIBLE WITH: ai_provider_service.py v5.0
# INTEGRATION: Railway PostgreSQL
# ==========================================================

import logging
from typing import Dict, List, Optional, Any, Union, Tuple
from datetime import datetime, date
from dataclasses import dataclass, field
from enum import Enum
import threading
import re
import traceback
import calendar
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: DATABASE SETUP
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
# BLOCK 2: ENUMS & DATACLASSES
# ==========================================================

class ValidationStatus(Enum):
    """Validation status for Business Date conversions."""
    VALID = "valid"
    INVALID_MONTH = "invalid_month"
    INVALID_DAY = "invalid_day"
    INVALID_DATE = "invalid_date"
    NULL_INPUT = "null_input"
    UNSUPPORTED_TYPE = "unsupported_type"
    LEAP_YEAR_ERROR = "leap_year_error"


@dataclass
class BusinessDate:
    """
    Business Date Object - Immutable representation.
    
    OFFICIAL BUSINESS RULE: PostgreSQL YYYY-MM-DD is interpreted as YYYY-DD-MM
    - PostgreSQL: Year=YYYY, Month=MM, Day=DD
    - Business:   Year=YYYY, Day=MM, Month=DD
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
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "original": str(self.original) if self.original else None,
            "business_year": self.business_year,
            "business_month": self.business_month,
            "business_day": self.business_day,
            "comparison_date": self.comparison_date.strftime("%Y-%m-%d") if self.comparison_date else None,
            "display_date": self.display_date,
            "validation_status": self.validation_status.value,
            "is_valid": self.is_valid,
            "source": self.source,
            "error_message": self.error_message
        }


# ==========================================================
# BLOCK 3: BUSINESS DATE ENGINE
# ==========================================================

class BusinessDateEngine:
    """
    SINGLE SOURCE OF TRUTH for all business date conversions.
    
    OFFICIAL BUSINESS RULE: PostgreSQL YYYY-MM-DD → Business Date YYYY-DD-MM
    - Business Year = PostgreSQL Year
    - Business Day = PostgreSQL Month
    - Business Month = PostgreSQL Day
    """
    
    MONTH_NAMES = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December"
    }
    
    DAYS_IN_MONTH = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                     7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
    
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
    def _extract_postgres_components(cls, postgres_date) -> Optional[Tuple[int, int, int]]:
        """Extract year, month, day from PostgreSQL date."""
        if postgres_date is None:
            return None
        
        try:
            if isinstance(postgres_date, date):
                return (postgres_date.year, postgres_date.month, postgres_date.day)
            elif isinstance(postgres_date, datetime):
                return (postgres_date.year, postgres_date.month, postgres_date.day)
            elif isinstance(postgres_date, str):
                parts = postgres_date.split('-')
                if len(parts) == 3:
                    return (int(parts[0]), int(parts[1]), int(parts[2]))
                parsed = datetime.strptime(postgres_date, "%Y-%m-%d")
                return (parsed.year, parsed.month, parsed.day)
            return None
        except (ValueError, TypeError) as e:
            logger.warning(f"⚠️ Failed to extract components from {postgres_date}: {e}")
            return None
    
    @classmethod
    def _validate_business_components(cls, year: int, month: int, day: int) -> ValidationStatus:
        """Validate business date components."""
        if year < 1:
            return ValidationStatus.INVALID_DATE
        if month < 1 or month > 12:
            return ValidationStatus.INVALID_MONTH
        if day < 1 or day > 31:
            return ValidationStatus.INVALID_DAY
        max_days = cls._get_days_in_month(year, month)
        if day > max_days:
            return ValidationStatus.LEAP_YEAR_ERROR
        return ValidationStatus.VALID
    
    @classmethod
    def _create_safe_business_date(cls, year: int, month: int, day: int, 
                                   original, source: str) -> BusinessDate:
        """Create Business Date with safe fallbacks for invalid values."""
        status = cls._validate_business_components(year, month, day)
        
        if status != ValidationStatus.VALID:
            error_messages = {
                ValidationStatus.INVALID_MONTH: f"Invalid month: {month} (must be 1-12)",
                ValidationStatus.INVALID_DAY: f"Invalid day: {day} (must be 1-31)",
                ValidationStatus.INVALID_DATE: f"Invalid date: {year}-{month:02d}-{day:02d}",
                ValidationStatus.LEAP_YEAR_ERROR: f"Invalid day: {day} for month {month} in year {year}",
                ValidationStatus.NULL_INPUT: "Null input received",
                ValidationStatus.UNSUPPORTED_TYPE: "Unsupported date type"
            }
            
            return BusinessDate(
                original=original,
                business_year=year if year > 0 else 1970,
                business_month=month if 1 <= month <= 12 else 1,
                business_day=day if 1 <= day <= 31 else 1,
                comparison_date=None,
                display_date="Invalid Business Date",
                validation_status=status,
                is_valid=False,
                source=source,
                error_message=error_messages.get(status, "Unknown validation error")
            )
        
        try:
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
                source=source,
                error_message=None
            )
        except ValueError as e:
            logger.error(f"❌ Business Date Engine: Unexpected error: {e}")
            return BusinessDate(
                original=original,
                business_year=year,
                business_month=month,
                business_day=day,
                comparison_date=None,
                display_date="Invalid Business Date",
                validation_status=ValidationStatus.INVALID_DATE,
                is_valid=False,
                source=source,
                error_message=str(e)
            )
    
    @classmethod
    def build_business_date(cls, postgres_date, source: str = "unknown") -> BusinessDate:
        """
        Build Business Date from PostgreSQL date.
        
        OFFICIAL BUSINESS RULE: PostgreSQL YYYY-MM-DD → Business Date YYYY-DD-MM
        PostgreSQL: Year=YYYY, Month=MM, Day=DD
        Business:   Year=YYYY, Day=MM, Month=DD
        """
        if postgres_date is None:
            return BusinessDate(
                original=None,
                business_year=1970,
                business_month=1,
                business_day=1,
                comparison_date=None,
                display_date="Invalid Date (NULL)",
                validation_status=ValidationStatus.NULL_INPUT,
                is_valid=False,
                source=source,
                error_message="Null input received"
            )
        
        components = cls._extract_postgres_components(postgres_date)
        if components is None:
            return BusinessDate(
                original=postgres_date,
                business_year=1970,
                business_month=1,
                business_day=1,
                comparison_date=None,
                display_date="Invalid Date (Unsupported)",
                validation_status=ValidationStatus.UNSUPPORTED_TYPE,
                is_valid=False,
                source=source,
                error_message=f"Unsupported type: {type(postgres_date)}"
            )
        
        pg_year, pg_month, pg_day = components
        
        # Apply OFFICIAL BUSINESS RULE: YYYY-DD-MM
        business_year = pg_year
        business_day = pg_month    # PostgreSQL Month → Business Day
        business_month = pg_day    # PostgreSQL Day → Business Month
        
        business_date = cls._create_safe_business_date(
            year=business_year,
            month=business_month,
            day=business_day,
            original=postgres_date,
            source=source
        )
        
        if business_date.is_valid:
            logger.info(
                f"📅 Business Date: PostgreSQL {cls.format_display_date(postgres_date)} → "
                f"{business_date.display_date} (source={source})"
            )
        else:
            logger.warning(
                f"⚠️ Business Date: Invalid conversion for {postgres_date} "
                f"(source={source}, error={business_date.error_message})"
            )
        
        return business_date
    
    @classmethod
    def get_current_business_date(cls, source: str = "current") -> BusinessDate:
        """Get current date as Business Date."""
        current = datetime.now()
        return cls.build_business_date(current, source=source)
    
    @classmethod
    def calculate_days_between(cls, date1: BusinessDate, date2: BusinessDate) -> int:
        """Calculate days between two Business Dates."""
        if not date1.is_valid or not date2.is_valid:
            return 0
        if date1.comparison_date is None or date2.comparison_date is None:
            return 0
        
        delta = date2.comparison_date - date1.comparison_date
        days = delta.days
        return max(0, days)
    
    @classmethod
    def format_display_date(cls, postgres_date) -> str:
        """Format PostgreSQL date for display (YYYY-MM-DD)."""
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
            return str(postgres_date)
        except (ValueError, TypeError):
            return str(postgres_date) if postgres_date else 'N/A'
    
    @classmethod
    def format_aging_text(cls, days: int) -> str:
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
        else:
            return f"{days} Days ({days // 30} Months)"


# ==========================================================
# BLOCK 4: DNAnalysisService CLASS
# ==========================================================

class DNAnalysisService:
    """
    DN Analytics Service - Direct PostgreSQL Connection.
    
    Enterprise-grade service with modular architecture.
    All aging calculations use BusinessDateEngine.
    """
    
    def __init__(self):
        """Initialize DN Analytics Service."""
        self._service_name = "dn_analysis"
        self._version = "6.1"
        self._status = "INITIALIZING"
        logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
        
        # Run health check
        health = self.health_check()
        if health.get("healthy", False):
            self._status = "READY"
            logger.info("✅ DNAnalysisService is READY")
        else:
            self._status = "ERROR"
            logger.error(f"❌ DNAnalysisService initialization FAILED: {health.get('errors', [])}")
    
    # ==========================================================
    # BLOCK 5: DATABASE METHODS
    # ==========================================================
    
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
        """Execute parameterized SQL query."""
        session = None
        try:
            session = self._get_session()
            if not session:
                return []
            
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            return rows
            
        except Exception as e:
            logger.error(f"❌ SQL Execution Failed: {e}")
            logger.error(f"   Query: {query[:200]}")
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return []
        finally:
            if session:
                session.close()
    
    # ==========================================================
    # BLOCK 6: DN NORMALIZATION
    # ==========================================================
    
    def _normalize_dn(self, dn_no: str) -> str:
        """Normalize DN number - removes non-numeric characters."""
        if not dn_no:
            return ""
        normalized = re.sub(r'[^0-9]', '', dn_no.strip())
        return normalized
    
    def _is_valid_dn_format(self, dn_no: str) -> bool:
        """Validate DN format."""
        normalized = self._normalize_dn(dn_no)
        return len(normalized) >= 8 and len(normalized) <= 12
    
    # ==========================================================
    # BLOCK 7: DN SEARCH ENGINE
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """
        Search for DN with multiple matching strategies.
        
        Strategies:
        1. Exact match
        2. LIKE match
        3. Regex match (normalized)
        4. Partial match fallback
        """
        logger.info(f"🔍 Searching for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized = self._normalize_dn(dn_no)
        if not self._is_valid_dn_format(normalized):
            return {"success": False, "error": f"Invalid DN format: {dn_no}"}
        
        # Build query with multiple match strategies
        query = """
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
        
        results = self._execute_query(query, {"dn_no": normalized})
        
        if results:
            logger.info(f"✅ DN {dn_no} found with {results[0].get('material_count', 1)} materials")
            return {"success": True, "data": results[0]}
        
        # Fallback: partial match search
        fallback_query = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
        fallback_results = self._execute_query(fallback_query, {"dn_no": normalized})
        similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
        
        if similar_dns:
            return {
                "success": False,
                "error": f"DN {dn_no} not found",
                "similar_dns": similar_dns[:5]
            }
        
        return {"success": False, "error": f"DN {dn_no} not found"}
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        """Verify if DN exists."""
        result = self.search_dn(dn_no)
        return {
            "success": True,
            "exists": result.get("success", False),
            "dn": dn_no
        }
    
    # ==========================================================
    # BLOCK 8: AGING CALCULATIONS
    # ==========================================================
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        """Calculate delivery aging using Business Date Engine."""
        try:
            business_dn = BusinessDateEngine.build_business_date(dn_create_date, source="dn_create")
            if not business_dn.is_valid:
                logger.warning(f"⚠️ Delivery Aging: DN Create invalid - Returning 0")
                return 0
            
            if good_issue_date is None:
                business_pgi = BusinessDateEngine.get_current_business_date(source="pgi_missing")
            else:
                business_pgi = BusinessDateEngine.build_business_date(good_issue_date, source="pgi")
                if not business_pgi.is_valid:
                    logger.warning(f"⚠️ Delivery Aging: PGI invalid - Returning 0")
                    return 0
            
            days = BusinessDateEngine.calculate_days_between(business_dn, business_pgi)
            
            logger.info(
                f"✅ Delivery Aging: DN Create: {BusinessDateEngine.format_display_date(dn_create_date)} "
                f"({business_dn.display_date}) → PGI: {BusinessDateEngine.format_display_date(good_issue_date)} "
                f"({business_pgi.display_date}) = {days} days"
            )
            return days
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate delivery aging: {e}")
            return 0
    
    def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
        """Calculate POD aging using Business Date Engine."""
        try:
            if good_issue_date is None:
                return 0
            
            business_pgi = BusinessDateEngine.build_business_date(good_issue_date, source="pgi")
            if not business_pgi.is_valid:
                logger.warning(f"⚠️ POD Aging: PGI invalid - Returning 0")
                return 0
            
            if pod_date is None:
                business_pod = BusinessDateEngine.get_current_business_date(source="pod_missing")
            else:
                business_pod = BusinessDateEngine.build_business_date(pod_date, source="pod")
                if not business_pod.is_valid:
                    logger.warning(f"⚠️ POD Aging: POD invalid - Returning 0")
                    return 0
            
            days = BusinessDateEngine.calculate_days_between(business_pgi, business_pod)
            
            logger.info(
                f"✅ POD Aging: PGI: {BusinessDateEngine.format_display_date(good_issue_date)} "
                f"({business_pgi.display_date}) → POD: {BusinessDateEngine.format_display_date(pod_date)} "
                f"({business_pod.display_date}) = {days} days"
            )
            return days
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate POD aging: {e}")
            return 0
    
    def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
        """Calculate total cycle using Business Date Engine."""
        try:
            business_dn = BusinessDateEngine.build_business_date(dn_create_date, source="dn_create")
            if not business_dn.is_valid:
                logger.warning(f"⚠️ Total Cycle: DN Create invalid - Returning 0")
                return 0
            
            if pod_date is None:
                business_pod = BusinessDateEngine.get_current_business_date(source="pod_missing")
            else:
                business_pod = BusinessDateEngine.build_business_date(pod_date, source="pod")
                if not business_pod.is_valid:
                    logger.warning(f"⚠️ Total Cycle: POD invalid - Returning 0")
                    return 0
            
            days = BusinessDateEngine.calculate_days_between(business_dn, business_pod)
            
            logger.info(
                f"✅ Total Cycle: DN Create: {BusinessDateEngine.format_display_date(dn_create_date)} "
                f"({business_dn.display_date}) → POD: {BusinessDateEngine.format_display_date(pod_date)} "
                f"({business_pod.display_date}) = {days} days"
            )
            return days
            
        except Exception as e:
            logger.error(f"❌ Failed to calculate total cycle: {e}")
            return 0
    
    # ==========================================================
    # BLOCK 9: DASHBOARD BUILDER
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Get complete DN dashboard."""
        logger.info(f"📊 Getting dashboard for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        search_result = self.search_dn(dn_no)
        if not search_result.get("success"):
            similar = search_result.get("similar_dns", [])
            if similar:
                return {
                    "success": False,
                    "error": f"DN {dn_no} not found. Similar: {', '.join(similar[:3])}"
                }
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        data = search_result["data"]
        
        # Calculate aging
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
        
        # Build dashboard
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
            "dn_create_date": BusinessDateEngine.format_display_date(data.get('dn_create_date')),
            "good_issue_date": BusinessDateEngine.format_display_date(data.get('good_issue_date')),
            "pod_date": BusinessDateEngine.format_display_date(data.get('pod_date')),
            "delivery_status": data.get('delivery_status', 'Unknown'),
            "pgi_status": data.get('pgi_status', 'Unknown'),
            "pod_status": data.get('pod_status', 'Unknown'),
            "pending_flag": data.get('pending_flag', False),
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "total_cycle_days": total_cycle,
            "delivery_aging_text": BusinessDateEngine.format_aging_text(delivery_aging),
            "pod_aging_text": BusinessDateEngine.format_aging_text(pod_aging),
            "total_cycle_text": BusinessDateEngine.format_aging_text(total_cycle)
        }
        
        # Status emojis
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
        
        # PGI status
        pgi_status = dashboard.get('pgi_status', '')
        dashboard['pgi_status_text'] = '✅ Completed' if pgi_status == 'Completed' else '⏳ Pending'
        
        # POD status
        pod_status = dashboard.get('pod_status', '')
        dashboard['pod_status_text'] = 'Done' if pod_status in ['Completed', 'Received', 'Done'] else '⏳ Pending'
        
        # Pending flag
        pending = dashboard.get('pending_flag', False)
        dashboard['pending_flag_text'] = '⚠️ Yes' if pending else '🟢 No'
        
        logger.info(f"✅ Dashboard returned for DN {dn_no}")
        return {"success": True, "data": dashboard}
    
    # ==========================================================
    # BLOCK 10: WHATSAPP FORMATTER
    # ==========================================================
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """Format DN dashboard for WhatsApp response."""
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
        
        # Optional fields
        if data.get('delivery_location'):
            lines.append("*Delivery Location:*")
            lines.append("{}".format(data.get('delivery_location')))
            lines.append("")
        
        if data.get('sales_manager'):
            lines.append("*Sales Manager:*")
            lines.append("{}".format(data.get('sales_manager')))
            lines.append("")
        
        if data.get('division'):
            lines.append("*Division:*")
            lines.append("{}".format(data.get('division')))
            lines.append("")
        
        # Metrics
        lines.append("*📊 Metrics:*")
        lines.append("Units: {}".format(data.get('total_units', 0)))
        revenue = data.get('total_revenue', 0)
        lines.append("Revenue: PKR {:,}".format(revenue) if revenue else "Revenue: PKR 0")
        lines.append("")
        lines.append("Materials: {}".format(data.get('material_count', 1)))
        lines.append("")
        
        # Dates - Display as YYYY-MM-DD
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
        lines.append("POD: {}".format(data.get('pod_status_text', 'Unknown')))
        lines.append("Pending: {}".format(data.get('pending_flag_text', 'Unknown')))
        
        return "\n".join(lines)
    
    # ==========================================================
    # BLOCK 11: PENDING METHODS
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
            
            if total_pending == 0:
                return {"success": True, "data": [], "total": 0, "message": "No pending DNs found"}
            
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
            
            results = self._execute_query(pending_query, {"limit": limit, "offset": offset})
            
            formatted = []
            for row in results:
                delivery_aging = self.calculate_delivery_aging(
                    row.get('dn_create_date'),
                    row.get('good_issue_date')
                )
                
                pending_flag = row.get('pending_flag')
                pending_flag_text = '⚠️ Yes' if pending_flag in [True, 'true', 'True', 1] else '🟢 No'
                
                formatted.append({
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": BusinessDateEngine.format_display_date(row.get('dn_create_date')),
                    "good_issue_date": BusinessDateEngine.format_display_date(row.get('good_issue_date')),
                    "pod_date": BusinessDateEngine.format_display_date(row.get('pod_date')),
                    "delivery_status": row.get('delivery_status') or "Pending",
                    "pgi_status": row.get('pgi_status') or "Pending",
                    "pod_status": row.get('pod_status') or "Unknown",
                    "pending_flag_text": pending_flag_text,
                    "delivery_aging_days": delivery_aging,
                    "delivery_aging_text": BusinessDateEngine.format_aging_text(delivery_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                })
            
            return {
                "success": True,
                "data": formatted,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted)
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
            
            if total_pending == 0:
                return {"success": True, "data": [], "total": 0, "message": "No pending PGI found"}
            
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
            
            results = self._execute_query(pending_query, {"limit": limit, "offset": offset})
            
            formatted = []
            for row in results:
                delivery_aging = self.calculate_delivery_aging(
                    row.get('dn_create_date'),
                    row.get('good_issue_date')
                )
                
                pending_flag = row.get('pending_flag')
                pending_flag_text = '⚠️ Yes' if pending_flag in [True, 'true', 'True', 1] else '🟢 No'
                
                formatted.append({
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": BusinessDateEngine.format_display_date(row.get('dn_create_date')),
                    "good_issue_date": BusinessDateEngine.format_display_date(row.get('good_issue_date')),
                    "pod_date": BusinessDateEngine.format_display_date(row.get('pod_date')),
                    "delivery_status": row.get('delivery_status') or "Pending",
                    "pgi_status": row.get('pgi_status') or "Pending",
                    "pod_status": row.get('pod_status') or "Unknown",
                    "pending_flag_text": pending_flag_text,
                    "delivery_aging_days": delivery_aging,
                    "delivery_aging_text": BusinessDateEngine.format_aging_text(delivery_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                })
            
            return {
                "success": True,
                "data": formatted,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted)
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
            
            if total_pending == 0:
                return {"success": True, "data": [], "total": 0, "message": "No pending POD found"}
            
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
            
            results = self._execute_query(pending_query, {"limit": limit, "offset": offset})
            
            formatted = []
            for row in results:
                pod_aging = self.calculate_pod_aging(
                    row.get('good_issue_date'),
                    row.get('pod_date')
                )
                
                pending_flag = row.get('pending_flag')
                pending_flag_text = '⚠️ Yes' if pending_flag in [True, 'true', 'True', 1] else '🟢 No'
                
                formatted.append({
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": BusinessDateEngine.format_display_date(row.get('dn_create_date')),
                    "good_issue_date": BusinessDateEngine.format_display_date(row.get('good_issue_date')),
                    "pod_date": BusinessDateEngine.format_display_date(row.get('pod_date')),
                    "delivery_status": row.get('delivery_status') or "In Transit",
                    "pgi_status": row.get('pgi_status') or "Completed",
                    "pod_status": row.get('pod_status') or "Pending",
                    "pending_flag_text": pending_flag_text,
                    "pod_aging_days": pod_aging,
                    "pod_aging_text": BusinessDateEngine.format_aging_text(pod_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                })
            
            return {
                "success": True,
                "data": formatted,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted)
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending POD: {e}")
            return {"success": False, "error": str(e)}
    
    # ==========================================================
    # BLOCK 12: HEALTH CHECK - FIXED TO RETURN DICT
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """
        Validate service readiness.
        
        Returns:
            Dict with health status (compatible with ai_provider_service.py)
        """
        logger.info("🔍 Running health check...")
        session = None
        
        result = {
            "healthy": False,
            "service": self._service_name,
            "version": self._version,
            "database": "disconnected",
            "errors": [],
            "warnings": [],
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            # Check SessionLocal
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                return result
            
            # Test connection
            session = SessionLocal()
            session.execute(text("SELECT 1"))
            result["database"] = "connected"
            
            # Check table exists
            inspector = inspect(session.bind)
            tables = inspector.get_table_names()
            if "delivery_reports" not in tables:
                result["errors"].append("Table 'delivery_reports' does not exist")
                return result
            
            # Check required columns
            required_columns = [
                "dn_no", "customer_name", "dealer_code", "customer_code",
                "warehouse", "warehouse_code", "ship_to_city", "delivery_location",
                "dn_qty", "dn_amount", "dn_create_date", "good_issue_date",
                "pod_date", "delivery_status", "pgi_status", "pod_status",
                "pending_flag"
            ]
            columns_info = inspector.get_columns("delivery_reports")
            columns = [col["name"] for col in columns_info]
            
            missing = [col for col in required_columns if col not in columns]
            if missing:
                result["warnings"].append(f"Missing columns: {missing}")
            
            # Test query
            session.execute(text("SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports LIMIT 1"))
            
            result["healthy"] = True
            logger.info("✅ Health check PASSED - Service is READY")
            
        except Exception as e:
            result["errors"].append(f"Health check failed: {str(e)}")
            logger.error(f"❌ Health check failed: {e}")
        finally:
            if session:
                session.close()
        
        return result
    
    def get_service_metadata(self) -> Dict[str, Any]:
        """Get service metadata for ai_provider_service.py."""
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
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod",
                "calculate_delivery_aging",
                "calculate_pod_aging",
                "calculate_total_cycle",
                "format_dn_dashboard"
            ]
        }
    
    def validation_query(self) -> Dict[str, Any]:
        """Used by ai_provider_service.py for validation."""
        try:
            session = self._get_session()
            if not session:
                return {"success": False, "records": 0, "error": "Session not available"}
            
            query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports"
            result = session.execute(text(query))
            row = result.fetchone()
            session.close()
            
            if row:
                return {"success": True, "records": row[0] or 0}
            return {"success": False, "records": 0, "error": "Query returned no results"}
            
        except Exception as e:
            logger.error(f"❌ Validation query failed: {e}")
            return {"success": False, "records": 0, "error": str(e)}
    
    # ==========================================================
    # BLOCK 13: DIAGNOSTIC METHODS
    # ==========================================================
    
    def diagnose_dn(self, dn_no: str) -> Dict[str, Any]:
        """Diagnose DN issues."""
        logger.info(f"🔬 Diagnosing DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized = self._normalize_dn(dn_no)
        
        result = {
            "dn": dn_no,
            "normalized": normalized,
            "exact_match_count": 0,
            "partial_match_count": 0,
            "similar_dns": [],
            "exists": False,
            "diagnostic": []
        }
        
        # Exact match
        exact_query = """
            SELECT COUNT(DISTINCT dn_no) as count 
            FROM delivery_reports 
            WHERE REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
        """
        exact_results = self._execute_query(exact_query, {"dn_no": normalized})
        exact_count = exact_results[0].get('count', 0) if exact_results else 0
        result["exact_match_count"] = exact_count
        result["exists"] = exact_count > 0
        result["diagnostic"].append(f"Exact match (normalized): {exact_count} found")
        
        # Partial match
        partial_query = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 20
        """
        partial_results = self._execute_query(partial_query, {"dn_no": normalized})
        similar_dns = [str(r.get('dn_no', '')) for r in partial_results if r.get('dn_no')]
        result["partial_match_count"] = len(similar_dns)
        result["similar_dns"] = similar_dns[:10]
        result["diagnostic"].append(f"Partial matches: {len(similar_dns)} found")
        
        if similar_dns:
            result["diagnostic"].append(f"Similar DNs: {', '.join(similar_dns[:5])}")
        
        return {"success": True, "data": result}
    
    def check_dn_raw(self, dn_no: str) -> Dict[str, Any]:
        """Check raw DN existence without normalization."""
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
    
    def test_dn_lookup(self, dn_no: str) -> Dict[str, Any]:
        """Test DN lookup with full diagnostics."""
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized = self._normalize_dn(dn_no)
        results = {
            "dn": dn_no,
            "normalized": normalized,
            "exact_count": 0,
            "like_count": 0,
            "regex_count": 0,
            "matching_dns": [],
            "diagnostics": []
        }
        
        # Exact match
        query1 = "SELECT COUNT(*) as count FROM delivery_reports WHERE CAST(dn_no AS TEXT) = :dn_no"
        r1 = self._execute_query(query1, {"dn_no": normalized})
        results["exact_count"] = r1[0].get('count', 0) if r1 else 0
        results["diagnostics"].append(f"Exact match: {results['exact_count']}")
        
        # LIKE match
        query2 = "SELECT COUNT(*) as count FROM delivery_reports WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'"
        r2 = self._execute_query(query2, {"dn_no": normalized})
        results["like_count"] = r2[0].get('count', 0) if r2 else 0
        results["diagnostics"].append(f"LIKE match: {results['like_count']}")
        
        # Regex match
        query3 = """
            SELECT COUNT(*) as count 
            FROM delivery_reports 
            WHERE REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
        """
        r3 = self._execute_query(query3, {"dn_no": normalized})
        results["regex_count"] = r3[0].get('count', 0) if r3 else 0
        results["diagnostics"].append(f"REGEXP match: {results['regex_count']}")
        
        # Get matching DNs
        query4 = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
        r4 = self._execute_query(query4, {"dn_no": normalized})
        results["matching_dns"] = [str(r.get('dn_no', '')) for r in r4 if r.get('dn_no')]
        
        results["found"] = results["exact_count"] > 0 or results["like_count"] > 0
        results["diagnostics"].append(f"Total matching DNs: {len(results['matching_dns'])}")
        
        return {"success": True, "data": results}
    
    def debug_aging_calculation(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        """Debug aging calculations with Business Date Engine."""
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
        
        # Log debug info
        logger.info("=" * 70)
        logger.info("🔍 DEBUG AGING CALCULATION (Business Date Engine)")
        logger.info("=" * 70)
        logger.info(f"📅 PostgreSQL Dates: DN Create={result['input_dates']['dn_create_date']}, "
                    f"PGI={result['input_dates']['pgi_date']}, POD={result['input_dates']['pod_date']}")
        logger.info(f"🔄 Business Dates: DN Create={result['business_dates']['dn_create_date']}, "
                    f"PGI={result['business_dates']['pgi_date']}, POD={result['business_dates']['pod_date']}")
        logger.info(f"🧮 Aging: Delivery={result['formatted']['delivery_aging_text']}, "
                    f"POD={result['formatted']['pod_aging_text']}, "
                    f"Total Cycle={result['formatted']['total_cycle_text']}")
        logger.info("=" * 70)
        
        return result
    
    # ==========================================================
    # BLOCK 14: TEST METHODS
    # ==========================================================
    
    def test_business_date_engine(self) -> Dict[str, Any]:
        """Test Business Date Engine with official mappings."""
        logger.info("🧪 Running Business Date Engine tests...")
        
        from datetime import date as date_type
        
        test_results = []
        all_passed = True
        
        # Official mapping tests
        mapping_tests = [
            ("2026-03-05", date_type(2026, 3, 5), "3 May 2026"),
            ("2026-05-05", date_type(2026, 5, 5), "5 May 2026"),
            ("2026-05-15", date_type(2026, 5, 15), "15 May 2026"),
            ("2026-06-05", date_type(2026, 6, 5), "6 May 2026"),
            ("2026-07-05", date_type(2026, 7, 5), "7 May 2026"),
            ("2026-12-31", date_type(2026, 12, 31), "31 December 2026"),
        ]
        
        for name, pg_date, expected in mapping_tests:
            business_date = BusinessDateEngine.build_business_date(pg_date, source="test")
            passed = business_date.is_valid and business_date.display_date == expected
            if not passed:
                all_passed = False
            test_results.append({
                "name": name,
                "postgresql": str(pg_date),
                "business_date": business_date.display_date,
                "expected": expected,
                "passed": passed
            })
        
        # Aging tests
        aging_tests = [
            {
                "name": "Aging Test 1",
                "dn_create": date_type(2026, 3, 5),
                "pgi": date_type(2026, 5, 5),
                "pod": date_type(2026, 5, 15),
                "expected": (2, 10, 12)
            },
            {
                "name": "Aging Test 2",
                "dn_create": date_type(2026, 6, 5),
                "pgi": date_type(2026, 6, 5),
                "pod": date_type(2026, 7, 5),
                "expected": (0, 1, 1)
            },
            {
                "name": "Aging Test 3",
                "dn_create": date_type(2026, 12, 31),
                "pgi": date_type(2026, 12, 31),
                "pod": date_type(2026, 12, 31),
                "expected": (0, 0, 0)
            }
        ]
        
        for test in aging_tests:
            delivery = self.calculate_delivery_aging(test["dn_create"], test["pgi"])
            pod = self.calculate_pod_aging(test["pgi"], test["pod"])
            total = self.calculate_total_cycle(test["dn_create"], test["pod"])
            
            expected_delivery, expected_pod, expected_total = test["expected"]
            passed = (delivery == expected_delivery and pod == expected_pod and total == expected_total)
            
            if not passed:
                all_passed = False
            
            test_results.append({
                "name": test["name"],
                "delivery_aging": delivery,
                "pod_aging": pod,
                "total_cycle": total,
                "expected": test["expected"],
                "passed": passed
            })
        
        return {
            "test_name": "Business Date Engine Validation",
            "business_rule": "YYYY-DD-MM (PostgreSQL YYYY-MM-DD → Business YYYY-DD-MM)",
            "tests": test_results,
            "all_passed": all_passed,
            "total_tests": len(test_results),
            "passed_tests": sum(1 for t in test_results if t.get("passed", False)),
            "timestamp": datetime.now().isoformat()
        }


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
    'get_dn_analytics_service',
    'BusinessDateEngine',
    'BusinessDate',
    'ValidationStatus'
]


# ==========================================================
# BLOCK 17: MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v6.1 - ENTERPRISE EDITION (FIXED)")
logger.info("=" * 70)
logger.info("")
logger.info("   SERVICE DETAILS:")
logger.info("   ✅ Service Name: dn_analysis")
logger.info("   ✅ Version: 6.1")
logger.info("   ✅ Source: PostgreSQL (delivery_reports)")
logger.info("   ✅ Compatible: ai_provider_service.py v5.0")
logger.info("")
logger.info("   BUSINESS DATE ENGINE:")
logger.info("   ✅ PostgreSQL YYYY-MM-DD → Business Date YYYY-DD-MM")
logger.info("   ✅ All aging calculations use BusinessDateEngine")
logger.info("   ✅ Display dates remain as PostgreSQL YYYY-MM-DD")
logger.info("")
logger.info("   AVAILABLE METHODS:")
logger.info("   ✅ health_check() - Returns Dict (compatible)")
logger.info("   ✅ validation_query()")
logger.info("   ✅ get_service_metadata()")
logger.info("   ✅ search_dn()")
logger.info("   ✅ verify_dn()")
logger.info("   ✅ get_dn_dashboard()")
logger.info("   ✅ get_pending_dns()")
logger.info("   ✅ get_pending_pgi()")
logger.info("   ✅ get_pending_pod()")
logger.info("   ✅ calculate_delivery_aging()")
logger.info("   ✅ calculate_pod_aging()")
logger.info("   ✅ calculate_total_cycle()")
logger.info("   ✅ format_dn_dashboard()")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)

# Run test on startup
try:
    service = get_dn_analytics_service()
    test_result = service.test_business_date_engine()
    if test_result.get("all_passed"):
        logger.info("✅ Business Date Engine: ALL TESTS PASSED")
    else:
        logger.warning("⚠️ Business Date Engine: SOME TESTS FAILED")
except Exception as e:
    logger.error(f"❌ Business Date Engine test failed: {e}")
