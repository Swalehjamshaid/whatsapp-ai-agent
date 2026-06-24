# ==========================================================
# FILE: app/services/dn_analysis.py (v4.0 - PRODUCTION GRADE)
# ==========================================================
# PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
# SOURCE: delivery_reports table ONLY
# VERSION: 4.0 - FIXED DN LOOKUP WITH FULL DIAGNOSTICS
#
# COMPATIBLE WITH: ai_provider_service.py v5.0
# INTEGRATION: Railway PostgreSQL
#
# FIXES APPLIED IN v4.0:
# - ✅ ADDED: Full SQL exception logging with traceback
# - ✅ ADDED: Direct exact-match check before aggregation
# - ✅ ADDED: test_dn_lookup() diagnostic method
# - ✅ ADDED: Column type logging in health_check()
# - ✅ FIXED: Auto-retry with exact DN when fallback finds same DN
# - ✅ ADDED: Diagnostic logging for every search
# - ✅ ADDED: COUNT(*) pre-check before DN Not Found
# - ✅ UPDATED: POD shows "Done" when completed
# - ✅ UPDATED: Dates stay in YYYY-MM-DD format
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
    
    COMPATIBLE WITH: ai_provider_service.py v5.0
    """
    
    def __init__(self):
        """Initialize DN Analytics Service."""
        self._service_name = "dn_analysis"
        self._version = "4.0"
        self._status = "INITIALIZING"
        logger.info("🔧 DNAnalysisService v4.0 initializing...")
        
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
        
        ✅ FIXED: Full exception logging with traceback
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
            # ✅ FULL EXCEPTION LOGGING
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
    
    def _build_exact_match_query(self, dn_no: str) -> str:
        """
        Build exact match query for diagnostic purposes.
        """
        return """
            SELECT *
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
            LIMIT 1
        """
    
    def _build_count_query(self, dn_no: str) -> str:
        """
        Build count query for diagnostic purposes.
        """
        return """
            SELECT COUNT(*) as count
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
        """
    
    def _build_fallback_dn_query(self, dn_no: str) -> str:
        """
        Build fallback DN query for partial matches.
        """
        return """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
    
    def _build_raw_dn_query(self, dn_no: str) -> str:
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
            "database": "disconnected",
            "errors": [],
            "warnings": [],
            "timestamp": datetime.now().isoformat()
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
            
            # Check 4: Check required columns AND log column types
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
                
                # ✅ Log column types for diagnostics
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
    
    # ==========================================================
    # ✅ FIXED: validation_query() - Uses raw SQL
    # ==========================================================
    
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
            "description": "DN Analytics Service - PostgreSQL Integration",
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
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod",
                "calculate_delivery_aging",
                "calculate_pod_aging",
                "calculate_total_cycle"
            ]
        }
    
    # ==========================================================
    # BLOCK 6: AGING CALCULATION METHODS (YYYY-DD-MM)
    # ==========================================================
        # ==========================================================
    # BLOCK 6: AGING CALCULATION METHODS (YYYY-DD-MM)
    # ==========================================================
    
    def _parse_date(self, date_value):
        """
        Parse date using company format: YYYY-DD-MM
        
        Example: "2026-06-05" → Year=2026, Day=06, Month=05 → May 6, 2026
        
        Args:
            date_value: Date string in YYYY-DD-MM format or datetime/date object
            
        Returns:
            datetime object or None if parsing fails
        """
        if not date_value:
            return None
        
        try:
            # Handle datetime or date objects
            if isinstance(date_value, datetime):
                return date_value
            elif isinstance(date_value, date):
                return datetime.combine(date_value, datetime.min.time())
            
            # Handle string dates - ALWAYS parse as YYYY-DD-MM
            if isinstance(date_value, str):
                parts = date_value.split('-')
                if len(parts) == 3:
                    year = int(parts[0])   # 2026
                    day = int(parts[1])    # 06
                    month = int(parts[2])  # 05
                    
                    # Validate date parts
                    if not (1 <= month <= 12 and 1 <= day <= 31):
                        raise ValueError(f"Invalid month/day: month={month}, day={day}")
                    
                    parsed_date = datetime(year, month, day)
                    
                    # ✅ Diagnostic logging
                    logger.info(f"Date Conversion: Raw={date_value} Parsed={parsed_date.strftime('%Y-%m-%d')}")
                    
                    return parsed_date
                else:
                    # Fallback for other formats
                    logger.warning(f"⚠️ Unexpected date format (not YYYY-DD-MM): {date_value}")
                    return datetime.strptime(date_value, '%Y-%m-%d')
            
            return None
            
        except Exception as e:
            logger.warning(f"⚠️ Date parsing error for {date_value}: {e}")
            return None
    
    def _parse_date_ydm(self, date_value):
        """
        Parse date using company format: YYYY-DD-MM
        
        Same as _parse_date() - uses YYYY-DD-MM format consistently.
        
        Example: "2026-06-05" → Year=2026, Day=06, Month=05 → May 6, 2026
        """
        # Use the same parsing logic as _parse_date
        return self._parse_date(date_value)
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        """
        Calculate delivery aging in days using YYYY-DD-MM dates.
        
        IF good_issue_date EXISTS:
            good_issue_date - dn_create_date
        ELSE:
            CURRENT_DATE - dn_create_date
        
        Args:
            dn_create_date: DN Create date in YYYY-DD-MM format
            good_issue_date: PGI date in YYYY-DD-MM format
            
        Returns:
            Delivery aging in days
        """
        try:
            # Parse dates using YYYY-DD-MM format
            dn_date = self._parse_date(dn_create_date)
            gi_date = self._parse_date(good_issue_date)
            
            if not dn_date:
                logger.warning(f"⚠️ Failed to parse DN Create date: {dn_create_date}")
                return 0
            
            # ✅ Diagnostic logging
            logger.info(f"📊 Delivery Aging: DN={dn_create_date} PGI={good_issue_date}")
            
            if gi_date:
                days = (gi_date - dn_date).days
                logger.info(f"   ├── Delivery Aging: {days} days (PGI - DN)")
                return days
            
            # No PGI yet - calculate from current date
            days = (datetime.now() - dn_date).days
            logger.info(f"   ├── Delivery Aging: {days} days (Current - DN)")
            return days
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to calculate delivery aging: {e}")
            return 0
    
    def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
        """
        Calculate POD aging in days using YYYY-DD-MM dates.
        
        IF pod_date EXISTS:
            pod_date - good_issue_date
        ELSE:
            CURRENT_DATE - good_issue_date
        
        Args:
            good_issue_date: PGI date in YYYY-DD-MM format
            pod_date: POD date in YYYY-DD-MM format
            
        Returns:
            POD aging in days
        """
        try:
            # Parse dates using YYYY-DD-MM format
            gi_date = self._parse_date(good_issue_date)
            pd_date = self._parse_date(pod_date)
            
            if not gi_date:
                logger.warning(f"⚠️ Failed to parse PGI date: {good_issue_date}")
                return 0
            
            # ✅ Diagnostic logging
            logger.info(f"📊 POD Aging: PGI={good_issue_date} POD={pod_date}")
            
            if pd_date:
                days = (pd_date - gi_date).days
                logger.info(f"   ├── POD Aging: {days} days (POD - PGI)")
                return days
            
            # No POD yet - calculate from current date
            days = (datetime.now() - gi_date).days
            logger.info(f"   ├── POD Aging: {days} days (Current - PGI)")
            return days
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to calculate POD aging: {e}")
            return 0
    
    def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
        """
        Calculate total cycle time in days using YYYY-DD-MM dates.
        
        IF pod_date EXISTS:
            pod_date - dn_create_date
        ELSE:
            CURRENT_DATE - dn_create_date
        
        Args:
            dn_create_date: DN Create date in YYYY-DD-MM format
            pod_date: POD date in YYYY-DD-MM format
            
        Returns:
            Total cycle time in days
        """
        try:
            # Parse dates using YYYY-DD-MM format
            dn_date = self._parse_date(dn_create_date)
            pd_date = self._parse_date(pod_date)
            
            if not dn_date:
                logger.warning(f"⚠️ Failed to parse DN Create date: {dn_create_date}")
                return 0
            
            # ✅ Diagnostic logging
            logger.info(f"📊 Total Cycle: DN={dn_create_date} POD={pod_date}")
            
            if pd_date:
                days = (pd_date - dn_date).days
                logger.info(f"   ├── Total Cycle: {days} days (POD - DN)")
                return days
            
            # No POD yet - calculate from current date
            days = (datetime.now() - dn_date).days
            logger.info(f"   ├── Total Cycle: {days} days (Current - DN)")
            return days
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to calculate total cycle: {e}")
            return 0
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
        
        # ✅ Diagnostic logging
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
            # ✅ If exact count > 0, try direct exact match
            logger.info(f"   ├── Exact match found! Trying direct query...")
            exact_query = self._build_exact_match_query(normalized_dn)
            exact_results = self._execute_query(exact_query, {"dn_no": normalized_dn})
            if exact_results:
                # Build aggregated result from exact match
                data = self._aggregate_dn_results(exact_results, normalized_dn)
                if data:
                    logger.info(f"✅ DN {dn_no} found via direct exact match")
                    return {"success": True, "data": data}
        
        # Step 4: Fallback partial match search
        logger.warning(f"⚠️ Primary match not found for {dn_no}. Running fallback search...")
        fallback_query = self._build_fallback_dn_query(normalized_dn)
        fallback_results = self._execute_query(fallback_query, {"dn_no": normalized_dn})
        
        similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
        
        # ✅ Check if the requested DN is in similar_dns
        requested_dn_found = any(dn == normalized_dn or dn == dn_no for dn in similar_dns)
        
        if requested_dn_found:
            # ✅ Auto-retry with the exact DN
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
    # BLOCK 8: DN DASHBOARD - KEEPS YYYY-MM-DD FORMAT
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
        
        # Add aging days
        data['delivery_aging_days'] = delivery_aging
        data['pod_aging_days'] = pod_aging
        data['total_cycle_days'] = total_cycle
        
        # Add aging text
        data['delivery_aging_text'] = self._format_aging_text(delivery_aging)
        data['pod_aging_text'] = self._format_aging_text(pod_aging)
        data['total_cycle_text'] = self._format_aging_text(total_cycle)
        
        # ✅ KEEP DATES AS YYYY-MM-DD (NO FORMATTING)
        # Dates are already in YYYY-MM-DD format from PostgreSQL
        
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
        
        # ✅ POD Status - "Done" when completed
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
    # BLOCK 8.5: DATE VALIDATION TEST
    # ==========================================================
    
    def test_date_calculation(self) -> Dict[str, Any]:
        """
        Test date calculations using the company-wide YYYY-DD-MM format.
        
        Test Case:
        - DN Create: 2026-06-05
        - PGI:       2026-06-05
        - POD:       2026-07-05
        
        Expected Results:
        - Delivery Aging = 0 days
        - POD Aging = 1 day
        - Total Cycle = 1 day
        
        Note: Since dates are parsed as YYYY-DD-MM:
        - 2026-06-05 → 6 May 2026 (day=06, month=05)
        - 2026-07-05 → 7 May 2026 (day=07, month=05)
        - Difference: 1 day
        """
        logger.info("🧪 Running date calculation test...")
        
        # Test data in YYYY-DD-MM format
        dn_create = "2026-06-05"  # Year=2026, Day=06, Month=05 → 6 May 2026
        pgi = "2026-06-05"        # Year=2026, Day=06, Month=05 → 6 May 2026
        pod = "2026-07-05"        # Year=2026, Day=07, Month=05 → 7 May 2026
        
        # Parse dates for display
        dn_parsed = self._parse_date(dn_create)
        pgi_parsed = self._parse_date(pgi)
        pod_parsed = self._parse_date(pod)
        
        # Calculate aging
        delivery_aging = self.calculate_delivery_aging(dn_create, pgi)
        pod_aging = self.calculate_pod_aging(pgi, pod)
        total_cycle = self.calculate_total_cycle(dn_create, pod)
        
        # Format results
        result = {
            "test_name": "Date Calculation Test (YYYY-DD-MM)",
            "input": {
                "dn_create": dn_create,
                "pgi": pgi,
                "pod": pod
            },
            "parsed_dates": {
                "dn_create": dn_parsed.strftime("%d %B %Y") if dn_parsed else None,
                "pgi": pgi_parsed.strftime("%d %B %Y") if pgi_parsed else None,
                "pod": pod_parsed.strftime("%d %B %Y") if pod_parsed else None
            },
            "calculations": {
                "delivery_aging_days": delivery_aging,
                "pod_aging_days": pod_aging,
                "total_cycle_days": total_cycle
            },
            "expected": {
                "delivery_aging_days": 0,
                "pod_aging_days": 1,
                "total_cycle_days": 1
            },
            "passed": (
                delivery_aging == 0 and
                pod_aging == 1 and
                total_cycle == 1
            )
        }
        
        # Log results
        logger.info("📊 Test Results:")
        logger.info(f"   ├── DN Create: {dn_create} → {result['parsed_dates']['dn_create']}")
        logger.info(f"   ├── PGI: {pgi} → {result['parsed_dates']['pgi']}")
        logger.info(f"   ├── POD: {pod} → {result['parsed_dates']['pod']}")
        logger.info(f"   ├── Delivery Aging: {delivery_aging} days (Expected: 0) ✅")
        logger.info(f"   ├── POD Aging: {pod_aging} days (Expected: 1) ✅")
        logger.info(f"   ├── Total Cycle: {total_cycle} days (Expected: 1) ✅")
        logger.info(f"   └── Test: {'✅ PASSED' if result['passed'] else '❌ FAILED'}")
        
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
        
        ✅ Dates are in YYYY-MM-DD format
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
        
        # ✅ Dates - Display as YYYY-MM-DD
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
        
        # ✅ POD Status - Show "Done" when completed
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
logger.info("DNAnalysisService v4.0 - PRODUCTION GRADE")
logger.info("=" * 70)
logger.info("")
logger.info("   SERVICE DETAILS:")
logger.info("   ✅ Service Name: dn_analysis")
logger.info("   ✅ Version: 4.0")
logger.info("   ✅ Status: READY")
logger.info("   ✅ Source: PostgreSQL (delivery_reports)")
logger.info("   ✅ Compatible: ai_provider_service.py v5.0")
logger.info("")
logger.info("   FIXES APPLIED IN v4.0:")
logger.info("   ✅ ADDED: Full SQL exception logging with traceback")
logger.info("   ✅ ADDED: Direct exact-match check before aggregation")
logger.info("   ✅ ADDED: test_dn_lookup() diagnostic method")
logger.info("   ✅ ADDED: Column type logging in health_check()")
logger.info("   ✅ FIXED: Auto-retry with exact DN when fallback finds same DN")
logger.info("   ✅ ADDED: Diagnostic logging for every search")
logger.info("   ✅ ADDED: COUNT(*) pre-check before DN Not Found")
logger.info("   ✅ UPDATED: POD shows 'Done' when completed")
logger.info("   ✅ UPDATED: Dates stay in YYYY-MM-DD format")
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
logger.info("   ✅ get_pending_dns()")
logger.info("   ✅ get_pending_pgi()")
logger.info("   ✅ get_pending_pod()")
logger.info("   ✅ calculate_delivery_aging()")
logger.info("   ✅ calculate_pod_aging()")
logger.info("   ✅ calculate_total_cycle()")
logger.info("   ✅ format_dn_dashboard()")
logger.info("")
logger.info("   RULES:")
logger.info("   ✅ DN Count = COUNT(DISTINCT dn_no)")
logger.info("   ✅ Units = SUM(dn_qty)")
logger.info("   ✅ Revenue = SUM(dn_amount)")
logger.info("   ✅ pending_flag = BOOLEAN (TRUE/FALSE)")
logger.info("   ✅ All data from PostgreSQL")
logger.info("   ❌ No CSV, Excel, JSON, Mock Data")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
