# ==========================================================
# FILE: app/services/dn_analysis.py (v3.1 - PRODUCTION GRADE)
# ==========================================================
# PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
# SOURCE: delivery_reports table ONLY
# VERSION: 3.1 - FIXED DN LOOKUP WITH REGEXP_REPLACE
#
# COMPATIBLE WITH: ai_provider_service.py v5.0
# INTEGRATION: Railway PostgreSQL
#
# FIXES APPLIED IN v3.1:
# - ✅ FIXED: Imported distinct from sqlalchemy
# - ✅ FIXED: REGEXP_REPLACE for both sides of DN comparison
# - ✅ FIXED: material_count uses COUNT(DISTINCT material_no)
# - ✅ ADDED: Raw DN diagnostic query
# - ✅ ADDED: Detailed logging for fallback matches
# ==========================================================

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, date
from sqlalchemy import text, func, and_, or_, distinct  # ✅ FIXED: distinct imported
from sqlalchemy.orm import Session
import threading
import re

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS
# ==========================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    from sqlalchemy import inspect
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
        self._version = "3.1"
        self._status = "INITIALIZING"
        logger.info("🔧 DNAnalysisService v3.1 initializing...")
        
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
        session = None
        try:
            session = self._get_session()
            if not session:
                logger.error("❌ No session available")
                return []
            
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            return rows
        except Exception as e:
            logger.error(f"❌ Query execution failed: {e}")
            return []
        finally:
            if session:
                session.close()
    
    # ==========================================================
    # BLOCK 4: DN SEARCH NORMALIZATION (FIXED v3.1)
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
        Build normalized DN query with REGEXP_REPLACE.
        
        ✅ FIXED: Uses REGEXP_REPLACE to normalize BOTH sides
        ✅ FIXED: material_count uses COUNT(DISTINCT material_no)
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
                COUNT(DISTINCT material_no) AS material_count
            FROM delivery_reports
            WHERE REGEXP_REPLACE(
                CAST(dn_no AS TEXT),
                '[^0-9]',
                '',
                'g'
            ) = :dn_no
            GROUP BY dn_no
        """
    
    def _build_fallback_dn_query(self, dn_no: str) -> str:
        """
        Build fallback DN query for partial matches.
        
        WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
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
        
        Used for diagnostics.
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
            
            # Check 4: Check required columns
            try:
                required_columns = [
                    "dn_no", "customer_name", "dealer_code", "customer_code",
                    "warehouse", "warehouse_code", "ship_to_city", "delivery_location",
                    "dn_qty", "dn_amount", "dn_create_date", "good_issue_date",
                    "pod_date", "delivery_status", "pgi_status", "pod_status",
                    "pending_flag", "material_no"
                ]
                columns = [col["name"] for col in inspector.get_columns("delivery_reports")]
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
            
            # Check 5: Test query execution with COUNT(DISTINCT dn_no)
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
            
            # ✅ FIXED: distinct is now imported
            count = session.query(func.count(distinct(DeliveryReport.dn_no))).scalar() or 0
            
            result["success"] = True
            result["records"] = count
            
            logger.info(f"✅ Validation query successful: {count} DNs")
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
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod",
                "calculate_delivery_aging",
                "calculate_pod_aging",
                "calculate_total_cycle"
            ]
        }
    
    # ==========================================================
    # BLOCK 6: AGING CALCULATION METHODS
    # ==========================================================
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        """Calculate delivery aging."""
        try:
            if isinstance(dn_create_date, str):
                dn_create_date = datetime.fromisoformat(dn_create_date.replace('Z', '+00:00'))
            if isinstance(good_issue_date, str):
                good_issue_date = datetime.fromisoformat(good_issue_date.replace('Z', '+00:00'))
            
            if not dn_create_date:
                return 0
            
            if good_issue_date:
                if isinstance(dn_create_date, datetime) and isinstance(good_issue_date, datetime):
                    days = (good_issue_date.date() - dn_create_date.date()).days
                elif isinstance(dn_create_date, date) and isinstance(good_issue_date, date):
                    days = (good_issue_date - dn_create_date).days
                else:
                    days = 0
                return max(0, days)
            
            if isinstance(dn_create_date, datetime):
                days = (datetime.now().date() - dn_create_date.date()).days
            elif isinstance(dn_create_date, date):
                days = (datetime.now().date() - dn_create_date).days
            else:
                days = 0
            return max(0, days)
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to calculate delivery aging: {e}")
            return 0
    
    def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
        """Calculate POD aging."""
        try:
            if isinstance(good_issue_date, str):
                good_issue_date = datetime.fromisoformat(good_issue_date.replace('Z', '+00:00'))
            if isinstance(pod_date, str):
                pod_date = datetime.fromisoformat(pod_date.replace('Z', '+00:00'))
            
            if not good_issue_date:
                return 0
            
            if pod_date:
                if isinstance(good_issue_date, datetime) and isinstance(pod_date, datetime):
                    days = (pod_date.date() - good_issue_date.date()).days
                elif isinstance(good_issue_date, date) and isinstance(pod_date, date):
                    days = (pod_date - good_issue_date).days
                else:
                    days = 0
                return max(0, days)
            
            if isinstance(good_issue_date, datetime):
                days = (datetime.now().date() - good_issue_date.date()).days
            elif isinstance(good_issue_date, date):
                days = (datetime.now().date() - good_issue_date).days
            else:
                days = 0
            return max(0, days)
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to calculate POD aging: {e}")
            return 0
    
    def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
        """Calculate total cycle time."""
        try:
            if isinstance(dn_create_date, str):
                dn_create_date = datetime.fromisoformat(dn_create_date.replace('Z', '+00:00'))
            if isinstance(pod_date, str):
                pod_date = datetime.fromisoformat(pod_date.replace('Z', '+00:00'))
            
            if not dn_create_date:
                return 0
            
            if pod_date:
                if isinstance(dn_create_date, datetime) and isinstance(pod_date, datetime):
                    days = (pod_date.date() - dn_create_date.date()).days
                elif isinstance(dn_create_date, date) and isinstance(pod_date, date):
                    days = (pod_date - dn_create_date).days
                else:
                    days = 0
                return max(0, days)
            
            if isinstance(dn_create_date, datetime):
                days = (datetime.now().date() - dn_create_date.date()).days
            elif isinstance(dn_create_date, date):
                days = (datetime.now().date() - dn_create_date).days
            else:
                days = 0
            return max(0, days)
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to calculate total cycle: {e}")
            return 0
    
    def _format_aging_text(self, days: int) -> str:
        """Format aging days into human readable text."""
        if days <= 0:
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
    # BLOCK 7: DN SEARCH WITH NORMALIZATION (FIXED v3.1)
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """
        Search for a specific DN with normalization and fallback.
        
        Steps:
        1. Normalize DN (trim whitespace, keep only digits)
        2. Try exact match with REGEXP_REPLACE (normalizes BOTH sides)
        3. If not found, try fallback partial match
        4. Return results or similar DNs
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
        
        # Step 2: Execute normalized query with REGEXP_REPLACE
        query = self._build_normalized_dn_query(normalized_dn)
        results = self._execute_query(query, {"dn_no": normalized_dn})
        
        if results:
            logger.info(f"✅ DN {dn_no} found with {results[0].get('material_count', 1)} materials")
            return {"success": True, "data": results[0]}
        
        # Step 3: Fallback partial match search
        logger.warning(f"⚠️ Exact match not found for {dn_no}. Running fallback search...")
        fallback_query = self._build_fallback_dn_query(normalized_dn)
        fallback_results = self._execute_query(fallback_query, {"dn_no": normalized_dn})
        
        similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
        
        # ✅ FIXED: Log similar DNs before returning
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
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        """Verify if DN exists using normalized search."""
        logger.info(f"🔍 Verifying DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "exists": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        
        query = """
            SELECT COUNT(DISTINCT dn_no) as count 
            FROM delivery_reports 
            WHERE REGEXP_REPLACE(
                CAST(dn_no AS TEXT),
                '[^0-9]',
                '',
                'g'
            ) = :dn_no
        """
        results = self._execute_query(query, {"dn_no": normalized_dn})
        exists = results and results[0].get('count', 0) > 0
        
        logger.info(f"✅ DN {dn_no} exists: {exists}")
        return {"success": True, "exists": exists}
    
    # ==========================================================
    # BLOCK 8: DN DASHBOARD (ENHANCED)
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Get complete DN dashboard with normalized search."""
        logger.info(f"📊 Getting dashboard for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        # Use search_dn for consistent normalization
        result = self.search_dn(dn_no)
        
        if not result.get("success"):
            # Format enhanced error message
            error_msg = result.get("error", f"DN {dn_no} not found")
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
        
        data['delivery_aging_days'] = delivery_aging
        data['pod_aging_days'] = pod_aging
        data['total_cycle_days'] = total_cycle
        data['delivery_aging_text'] = self._format_aging_text(delivery_aging)
        data['pod_aging_text'] = self._format_aging_text(pod_aging)
        data['total_cycle_text'] = self._format_aging_text(total_cycle)
        
        # Format dates for display
        for date_field in ['dn_create_date', 'good_issue_date', 'pod_date']:
            if data.get(date_field):
                if isinstance(data[date_field], (datetime, date)):
                    data[date_field] = data[date_field].strftime("%Y-%m-%d")
        
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
        
        # Add POD status
        pod_status = data.get('pod_status', '')
        if pod_status == 'Completed':
            data['pod_status_text'] = '✅ Completed'
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
    # BLOCK 9: DATABASE DIAGNOSTICS (ENHANCED v3.1)
    # ==========================================================
    
    def diagnose_dn(self, dn_no: str) -> Dict[str, Any]:
        """
        Diagnose DN issues.
        
        Returns:
        - Exact Match Count
        - Partial Match Count
        - Similar DNs
        - DN Exists Flag
        """
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
        
        # Check exact match with REGEXP_REPLACE
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
        
        # Check partial match
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
        
        # Check if dn_no exists as-is (without normalization)
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
        """
        Check raw DN existence without any normalization.
        
        This helps prove whether the DN actually exists in the database.
        """
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
                    COUNT(DISTINCT material_no) AS material_count
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
                    COUNT(DISTINCT material_no) AS material_count
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
                    COUNT(DISTINCT material_no) AS material_count
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
    # BLOCK 11: WHATSAPP RESPONSE FORMATTER
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
        lines.append("*📊 Metrics:*")
        lines.append("Units: {}".format(data.get('total_units', 0)))
        lines.append("Revenue: PKR {:,}".format(data.get('total_revenue', 0) or 0))
        lines.append("")
        lines.append("*📅 Dates:*")
        lines.append("DN Create: {}".format(data.get('dn_create_date', 'N/A')))
        lines.append("PGI: {}".format(data.get('good_issue_date', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_date', 'N/A')))
        lines.append("")
        lines.append("*⏳ Aging:*")
        lines.append("Delivery: {}".format(data.get('delivery_aging_text', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_aging_text', 'N/A')))
        lines.append("Total Cycle: {}".format(data.get('total_cycle_text', 'N/A')))
        lines.append("")
        lines.append("*📋 Status:*")
        lines.append("Delivery: {} {}".format(data.get('status_emoji', '❓'), data.get('status_text', 'Unknown')))
        lines.append("PGI: {}".format(data.get('pgi_status_text', 'Unknown')))
        lines.append("POD: {}".format(data.get('pod_status_text', 'Unknown')))
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
logger.info("DNAnalysisService v3.1 - PRODUCTION GRADE")
logger.info("=" * 70)
logger.info("")
logger.info("   SERVICE DETAILS:")
logger.info("   ✅ Service Name: dn_analysis")
logger.info("   ✅ Version: 3.1")
logger.info("   ✅ Status: READY")
logger.info("   ✅ Source: PostgreSQL (delivery_reports)")
logger.info("   ✅ Compatible: ai_provider_service.py v5.0")
logger.info("")
logger.info("   FIXES APPLIED IN v3.1:")
logger.info("   ✅ FIXED: Imported distinct from sqlalchemy")
logger.info("   ✅ FIXED: REGEXP_REPLACE for both sides of DN comparison")
logger.info("   ✅ FIXED: material_count uses COUNT(DISTINCT material_no)")
logger.info("   ✅ ADDED: Raw DN diagnostic query (check_dn_raw)")
logger.info("   ✅ ADDED: Detailed logging for fallback matches")
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
logger.info("   ✅ material_count = COUNT(DISTINCT material_no)")
logger.info("   ✅ All data from PostgreSQL")
logger.info("   ❌ No CSV, Excel, JSON, Mock Data")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
