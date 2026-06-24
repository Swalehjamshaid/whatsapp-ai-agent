# ==========================================================
# FILE: app/services/dn_analysis.py (v1.1 - DIAGNOSTIC VERSION)
# ==========================================================

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, date
import threading
import sys
import os

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: DIAGNOSTIC IMPORTS
# ==========================================================

logger.info("=" * 70)
logger.info("🔍 DNAnalysisService - DIAGNOSTIC MODE")
logger.info("=" * 70)

# Check Python path
logger.info(f"📁 Python Path: {sys.path}")

# Try importing with detailed error
try:
    from app.database import SessionLocal
    logger.info("✅ app.database imported successfully")
except ImportError as e:
    logger.error(f"❌ app.database import failed: {e}")
    SessionLocal = None

try:
    from app.models import DeliveryReport
    logger.info("✅ app.models imported successfully")
except ImportError as e:
    logger.error(f"❌ app.models import failed: {e}")
    DeliveryReport = None

try:
    from sqlalchemy import text, func, and_, or_
    from sqlalchemy.orm import Session
    from sqlalchemy import inspect
    logger.info("✅ SQLAlchemy imported successfully")
except ImportError as e:
    logger.error(f"❌ SQLAlchemy import failed: {e}")

logger.info("=" * 70)


# ==========================================================
# BLOCK 2: DNAnalysisService CLASS
# ==========================================================

class DNAnalysisService:
    """
    DN Analytics Service - Direct PostgreSQL Connection.
    """
    
    def __init__(self):
        """Initialize DN Analytics Service."""
        self._service_name = "dn_analysis"
        self._version = "1.1"
        self._status = "INITIALIZING"
        
        logger.info("=" * 70)
        logger.info("🔧 DNAnalysisService Initializing...")
        logger.info("=" * 70)
        
        # Check database availability
        if SessionLocal:
            logger.info("✅ SessionLocal is available")
        else:
            logger.error("❌ SessionLocal is NOT available")
        
        if DeliveryReport:
            logger.info("✅ DeliveryReport model is available")
        else:
            logger.error("❌ DeliveryReport model is NOT available")
        
        # Test connection immediately
        test_result = self._test_connection()
        if test_result:
            self._status = "READY"
            logger.info("✅ Database connection test PASSED")
        else:
            self._status = "ERROR"
            logger.error("❌ Database connection test FAILED")
        
        logger.info("=" * 70)
        logger.info(f"Service Status: {self._status}")
        logger.info("=" * 70)
        
        logger.info("✅ DNAnalysisService initialized")
    
    def _test_connection(self) -> bool:
        """Test database connection."""
        try:
            if not SessionLocal:
                logger.error("❌ SessionLocal is None")
                return False
            
            session = SessionLocal()
            result = session.execute(text("SELECT 1"))
            session.close()
            
            logger.info("✅ Database connection test: SUCCESS")
            return True
        except Exception as e:
            logger.error(f"❌ Database connection test FAILED: {e}")
            return False
    
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
        session = self._get_session()
        if not session:
            logger.error("❌ No session available")
            return []
        
        try:
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            session.close()
            logger.info(f"✅ Query executed: {len(rows)} rows returned")
            return rows
        except Exception as e:
            logger.error(f"❌ Query execution failed: {e}")
            session.close()
            return []
    
    # ==========================================================
    # MANDATORY METHODS FOR AI_PROVIDER_SERVICE
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Validate service readiness."""
        logger.info("🔍 Running health check...")
        
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
                session.close()
                return result
            
            # Check 3: Check table exists
            try:
                inspector = inspect(session.bind)
                tables = inspector.get_table_names()
                if "delivery_reports" not in tables:
                    result["errors"].append("Table 'delivery_reports' does not exist")
                    logger.error("❌ Table 'delivery_reports' not found")
                    session.close()
                    return result
                logger.info("✅ Table 'delivery_reports' exists")
            except Exception as e:
                result["errors"].append(f"Table check failed: {str(e)}")
                logger.error(f"❌ Table check failed: {e}")
                session.close()
                return result
            
            # Check 4: Check required columns
            try:
                required_columns = [
                    "dn_no", "customer_name", "warehouse", "ship_to_city",
                    "dn_qty", "dn_amount", "dn_create_date"
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
                session.close()
                return result
            
            # Check 5: Test query execution
            try:
                test_query = "SELECT COUNT(*) as count FROM delivery_reports LIMIT 1"
                session.execute(text(test_query))
                logger.info("✅ Test query executed successfully")
            except Exception as e:
                result["errors"].append(f"Test query failed: {str(e)}")
                logger.error(f"❌ Test query failed: {e}")
                session.close()
                return result
            
            session.close()
            
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
    
    def validation_query(self) -> Dict[str, Any]:
        """Used by ai_provider_service.py for validation."""
        logger.info("🔍 Running validation query...")
        
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
            
            count = session.query(func.count(DeliveryReport.dn_no)).scalar() or 0
            session.close()
            
            result["success"] = True
            result["records"] = count
            
            logger.info(f"✅ Validation query successful: {count} records")
            return result
            
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"❌ Validation query failed: {e}")
            return result
    
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
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod"
            ]
        }
    
    # ==========================================================
    # DN METHODS
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """Search for a specific DN."""
        logger.info(f"🔍 Searching for DN: {dn_no}")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        query = """
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
                COUNT(*) AS material_count
            FROM delivery_reports
            WHERE dn_no = :dn_no
            GROUP BY dn_no
        """
        
        results = self._execute_query(query, {"dn_no": dn_no})
        
        if not results:
            logger.warning(f"❌ DN {dn_no} not found")
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        logger.info(f"✅ DN {dn_no} found with {results[0].get('material_count', 1)} materials")
        return {"success": True, "data": results[0]}
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        """Verify if DN exists."""
        logger.info(f"🔍 Verifying DN: {dn_no}")
        
        if not dn_no:
            return {"success": False, "exists": False, "error": "DN number required"}
        
        query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports WHERE dn_no = :dn_no"
        results = self._execute_query(query, {"dn_no": dn_no})
        exists = results and results[0].get('count', 0) > 0
        
        logger.info(f"✅ DN {dn_no} exists: {exists}")
        return {"success": True, "exists": exists}
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Get complete DN dashboard."""
        logger.info(f"📊 Getting dashboard for DN: {dn_no}")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        query = """
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(dealer_code) AS dealer_code,
                MAX(customer_code) AS customer_code,
                MAX(warehouse) AS warehouse,
                MAX(ship_to_city) AS city,
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
            WHERE dn_no = :dn_no
            GROUP BY dn_no
        """
        
        results = self._execute_query(query, {"dn_no": dn_no})
        
        if not results:
            logger.warning(f"❌ DN {dn_no} not found")
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        data = results[0]
        
        # Format dates
        for date_field in ['dn_create_date', 'good_issue_date', 'pod_date']:
            if data.get(date_field):
                if isinstance(data[date_field], (datetime, date)):
                    data[date_field] = data[date_field].strftime("%Y-%m-%d")
        
        logger.info(f"✅ Dashboard returned for DN {dn_no}")
        return {"success": True, "data": data}
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get pending DNs."""
        logger.info(f"🔍 Getting pending DNs (limit: {limit}, offset: {offset})")
        
        try:
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR pgi_status = 'Pending'
                   OR pending_flag = 'Y'
                   OR delivery_status = 'Pending'
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            logger.info(f"📊 Total pending DNs: {total_pending}")
            
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
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR pgi_status = 'Pending'
                   OR pending_flag = 'Y'
                   OR delivery_status = 'Pending'
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(pending_query, {"limit": limit, "offset": offset})
            
            logger.info(f"✅ Returned {len(results)} pending DNs")
            
            return {
                "success": True,
                "data": results,
                "total": total_pending,
                "limit": limit,
                "offset": offset
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending DNs: {e}")
            return {"success": False, "error": str(e)}
    
    def get_pending_pgi(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get pending PGI."""
        logger.info(f"🔍 Getting pending PGI (limit: {limit}, offset: {offset})")
        
        try:
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR pgi_status = 'Pending'
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            logger.info(f"📊 Total pending PGI: {total_pending}")
            
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
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR pgi_status = 'Pending'
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(pending_query, {"limit": limit, "offset": offset})
            
            logger.info(f"✅ Returned {len(results)} pending PGI")
            
            return {
                "success": True,
                "data": results,
                "total": total_pending,
                "limit": limit,
                "offset": offset
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending PGI: {e}")
            return {"success": False, "error": str(e)}
    
    def get_pending_pod(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get pending POD."""
        logger.info(f"🔍 Getting pending POD (limit: {limit}, offset: {offset})")
        
        try:
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NOT NULL
                  AND (pod_date IS NULL OR pod_status = 'Pending')
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            logger.info(f"📊 Total pending POD: {total_pending}")
            
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
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE good_issue_date IS NOT NULL
                  AND (pod_date IS NULL OR pod_status = 'Pending')
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(pending_query, {"limit": limit, "offset": offset})
            
            logger.info(f"✅ Returned {len(results)} pending POD")
            
            return {
                "success": True,
                "data": results,
                "total": total_pending,
                "limit": limit,
                "offset": offset
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending POD: {e}")
            return {"success": False, "error": str(e)}


# ==========================================================
# THREAD-SAFE SINGLETON
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
# EXPORTS
# ==========================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service'
]


# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v1.1 - DIAGNOSTIC MODE")
logger.info("=" * 70)
logger.info("")
logger.info("   STATUS:")
logger.info("   ✅ Service loaded successfully")
logger.info("   ✅ All imports completed")
logger.info("")
logger.info("   NEXT STEPS:")
logger.info("   1. Check logs above for any errors")
logger.info("   2. Verify PostgreSQL connection")
logger.info("   3. Check if table 'delivery_reports' exists")
logger.info("   4. Verify ai_provider_service.py can find this service")
logger.info("")
logger.info("=" * 70)
