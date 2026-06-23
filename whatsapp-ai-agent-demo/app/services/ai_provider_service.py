# ==========================================================
# FILE: app/services/ai_provider_service.py (v31.0 - ENTERPRISE)
# PURPOSE: PRODUCTION-GRADE AI ORCHESTRATION WITH POSTGRESQL
# VERSION: 31.0 - Enterprise Refactoring - No Dummy Data
# ==========================================================

import time
import uuid
import re
import os
import requests
from typing import Optional, Callable, Any, Dict, List, Tuple
from dataclasses import dataclass, field
from cachetools import TTLCache, LRUCache
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, String, and_, or_, text



# ==========================================================
# BLOCK 1: POSTGRESQL IMPORTS - THE SOURCE OF TRUTH
# ==========================================================

from app.models import DeliveryReport
from app.database import SessionLocal, check_database_connection
from sqlalchemy import func, cast, String, and_, or_, text, distinct

# ==========================================================
# BLOCK 1.5: SERVICE IMPORTS - LAZY LOADING
# ==========================================================

# Analytics Service - Import at module level
try:
    from app.services.analytics_service import get_analytics_service, AnalyticsResponse
    logger.info("✅ Analytics service imported successfully")
except ImportError as e:
    logger.error(f"❌ Analytics service import failed: {e}")
    get_analytics_service = None
    AnalyticsResponse = None

# Distance Service - Import at module level
try:
    from app.services.distance_service import DistanceService, get_distance_service
    logger.info("✅ Distance service imported successfully")
except ImportError as e:
    logger.error(f"❌ Distance service import failed: {e}")
    DistanceService = None
    get_distance_service = None

# Dealer Analytics - LAZY LOADING (imported only when needed)
# This prevents circular imports
DealerAnalyticsService = None
format_dealer_360_dashboard = None

def _get_dealer_analytics_service():
    """Lazy load DealerAnalyticsService to prevent circular imports."""
    global DealerAnalyticsService, format_dealer_360_dashboard
    
    if DealerAnalyticsService is None:
        try:
            from app.services.dealer_analytics_service import DealerAnalyticsService as _DealerAnalyticsService
            from app.services.dealer_analytics_service import format_dealer_360_dashboard as _format_dealer_360_dashboard
            DealerAnalyticsService = _DealerAnalyticsService
            format_dealer_360_dashboard = _format_dealer_360_dashboard
            logger.info("✅ Dealer Analytics service loaded lazily")
        except ImportError as e:
            logger.error(f"❌ Dealer Analytics service import failed: {e}")
            DealerAnalyticsService = None
            format_dealer_360_dashboard = None
    
    return DealerAnalyticsService, format_dealer_360_dashboard


# ==========================================================
# BLOCK 2: ANALYTICS SERVICE LOADER (PRODUCTION GRADE v13.0)
# ==========================================================

import threading
from datetime import datetime
import traceback as tb

# ==========================================================
# SECTION 2.1: GLOBALS & LOCKS
# ==========================================================

_analytics_service_instance = None
_analytics_service_lock = threading.Lock()
_analytics_health_status = {
    "initialized": False,
    "database_connected": False,
    "total_records": 0,
    "total_dns": 0,
    "total_dealers": 0,
    "total_warehouses": 0,
    "total_cities": 0,
    "last_check": None,
    "errors": []
}

# ==========================================================
# SECTION 2.2: RESPONSE CLASS
# ==========================================================

def _create_response_class():
    """Create response class for analytics service."""
    class AnalyticsResponse:
        def __init__(self, data=None, success=True, error=None, error_id=None):
            self.data = data or {}
            self.success = success
            self.error = error
            self.error_id = error_id or str(uuid.uuid4())[:8]
            self.timestamp = datetime.now().isoformat()
    return AnalyticsResponse

# ==========================================================
# SECTION 2.3: POSTGRESQL HEALTH ENGINE
# ==========================================================
# ==========================================================
# BLOCK 2.3: POSTGRESQL HEALTH ENGINE (HOTFIX v14.0)
# ==========================================================

def _validate_postgresql_health() -> Dict[str, Any]:
    """
    Comprehensive PostgreSQL health validation.
    
    CRITICAL: Only fail on critical issues.
    Optional columns generate warnings, NOT errors.
    
    Critical Issues (FAIL):
    - Database connection failed
    - Table 'delivery_reports' missing
    - Total records = 0
    - dn_no column missing/has no data
    - customer_name column missing/has no data
    - warehouse column missing/has no data
    - ship_to_city column missing/has no data
    
    Optional Issues (WARNINGS):
    - dealer_code empty
    - customer_code empty
    - warehouse_code empty
    - sales_manager empty
    - sales_office empty
    - division empty
    - delivery_status empty
    - pgi_status empty
    - pod_status empty
    - pending_flag empty
    
    Statistics errors (WARNINGS):
    - COUNT DISTINCT queries should not fail health
    """
    global _analytics_health_status
    
    result = {
        "status": "unknown",
        "connected": False,
        "table_exists": False,
        "record_count": 0,
        "critical_columns": {},
        "optional_columns": {},
        "statistics": {},
        "errors": [],
        "warnings": [],
        "database_version": None,
        "timestamp": datetime.now().isoformat()
    }
    
    try:
        db = SessionLocal()
        
        # Get database version (optional)
        try:
            version = db.execute(text("SELECT version()")).scalar()
            result["database_version"] = version.split()[1] if version else "Unknown"
            logger.info(f"📌 PostgreSQL Version: {result['database_version']}")
        except Exception as e:
            result["warnings"].append(f"Could not get database version: {str(e)}")
        
        # ==========================================================
        # CRITICAL: Check table exists
        # ==========================================================
        try:
            total_records = db.query(DeliveryReport).count()
            result["record_count"] = total_records
            result["table_exists"] = True
            result["connected"] = True
            logger.info(f"✅ Table 'delivery_reports' exists with {total_records} records")
        except Exception as e:
            result["errors"].append(f"Table validation failed: {str(e)}")
            result["status"] = "critical"
            db.close()
            _analytics_health_status["errors"] = result["errors"]
            return result
        
        # ==========================================================
        # CRITICAL: Check if any data exists
        # ==========================================================
        if total_records == 0:
            result["errors"].append("No records found in delivery_reports table")
            result["status"] = "critical"
            db.close()
            _analytics_health_status["errors"] = result["errors"]
            return result
        
        # ==========================================================
        # CRITICAL COLUMNS - Service FAILS if these are missing
        # ==========================================================
        critical_columns = [
            "dn_no", "customer_name", "warehouse", "ship_to_city"
        ]
        
        for col in critical_columns:
            try:
                # Check if column has any non-null data
                count = db.query(DeliveryReport).filter(
                    getattr(DeliveryReport, col).isnot(None)
                ).count()
                
                result["critical_columns"][col] = {
                    "exists": True,
                    "non_null_count": count,
                    "has_data": count > 0
                }
                
                if count > 0:
                    logger.info(f"✅ Critical column '{col}' validated ({count} non-null values)")
                else:
                    result["errors"].append(f"Critical column '{col}' has all NULL values")
                    logger.error(f"❌ Critical column '{col}' has all NULL values")
                    
            except Exception as e:
                result["critical_columns"][col] = {
                    "exists": False,
                    "non_null_count": 0,
                    "has_data": False
                }
                result["errors"].append(f"Critical column '{col}' missing: {str(e)}")
                logger.error(f"❌ Critical column '{col}' missing: {str(e)}")
        
        # ==========================================================
        # OPTIONAL COLUMNS - WARNINGS ONLY, NOT ERRORS (FIXED)
        # ==========================================================
        optional_columns = [
            "dealer_code", "customer_code", "warehouse_code",
            "customer_model", "material_no", "sales_office",
            "sales_manager", "division", "delivery_status",
            "pgi_status", "pod_status", "pending_flag"
        ]
        
        for col in optional_columns:
            try:
                count = db.query(DeliveryReport).filter(
                    getattr(DeliveryReport, col).isnot(None)
                ).count()
                
                result["optional_columns"][col] = {
                    "exists": True,
                    "non_null_count": count,
                    "has_data": count > 0
                }
                
                if count == 0:
                    # WARNING only - NOT an error
                    result["warnings"].append(f"Optional column '{col}' has all NULL values")
                    logger.warning(f"⚠️ Optional column '{col}' has all NULL values")
                    
            except Exception as e:
                result["optional_columns"][col] = {
                    "exists": False,
                    "non_null_count": 0,
                    "has_data": False
                }
                # WARNING only - NOT an error
                result["warnings"].append(f"Optional column '{col}' missing: {str(e)}")
                logger.warning(f"⚠️ Optional column '{col}' missing: {str(e)}")
        
        # ==========================================================
        # STATISTICS - WARNINGS ONLY, NEVER FAIL HEALTH (FIXED)
        # ==========================================================
        try:
            # Try to get statistics - if this fails, it's a WARNING, not an error
            stats = {
                "total_dns": 0,
                "total_dealers": 0,
                "total_warehouses": 0,
                "total_cities": 0,
                "total_products": 0
            }
            
            try:
                stats["total_dns"] = db.query(func.count(distinct(DeliveryReport.dn_no))).scalar() or 0
                logger.info(f"📦 Total DNs: {stats['total_dns']}")
            except Exception as e:
                logger.warning(f"⚠️ Could not count DNs: {e}")
                result["warnings"].append(f"DN count failed: {str(e)}")
            
            try:
                stats["total_dealers"] = db.query(func.count(distinct(DeliveryReport.customer_name))).scalar() or 0
                logger.info(f"🏪 Total Dealers: {stats['total_dealers']}")
            except Exception as e:
                logger.warning(f"⚠️ Could not count dealers: {e}")
                result["warnings"].append(f"Dealer count failed: {str(e)}")
            
            try:
                stats["total_warehouses"] = db.query(func.count(distinct(DeliveryReport.warehouse))).scalar() or 0
                logger.info(f"🏭 Total Warehouses: {stats['total_warehouses']}")
            except Exception as e:
                logger.warning(f"⚠️ Could not count warehouses: {e}")
                result["warnings"].append(f"Warehouse count failed: {str(e)}")
            
            try:
                stats["total_cities"] = db.query(func.count(distinct(DeliveryReport.ship_to_city))).scalar() or 0
                logger.info(f"🏙️ Total Cities: {stats['total_cities']}")
            except Exception as e:
                logger.warning(f"⚠️ Could not count cities: {e}")
                result["warnings"].append(f"City count failed: {str(e)}")
            
            try:
                stats["total_products"] = db.query(func.count(distinct(DeliveryReport.customer_model))).scalar() or 0
                logger.info(f"📦 Total Products: {stats['total_products']}")
            except Exception as e:
                logger.warning(f"⚠️ Could not count products: {e}")
                result["warnings"].append(f"Product count failed: {str(e)}")
            
            result["statistics"] = stats
            
        except Exception as e:
            result["warnings"].append(f"Statistics query failed: {str(e)}")
            logger.warning(f"⚠️ Statistics query failed: {str(e)}")
        
        db.close()
        
        # ==========================================================
        # DETERMINE STATUS - Only CRITICAL on actual failures
        # ==========================================================
        critical_errors = len([e for e in result["errors"] if "critical" in e.lower()])
        critical_missing = len([c for c in result["critical_columns"].values() if not c.get("exists", False)])
        has_critical = any([c.get("has_data", False) == False for c in result["critical_columns"].values()])
        
        if not result["connected"]:
            result["status"] = "critical"
        elif not result["table_exists"]:
            result["status"] = "critical"
        elif result["record_count"] == 0:
            result["status"] = "critical"
        elif critical_errors > 0 or critical_missing > 0:
            result["status"] = "critical"
        elif has_critical:
            result["status"] = "critical"
        elif len(result["warnings"]) > 5:
            result["status"] = "degraded"
        else:
            result["status"] = "healthy"
        
        # ==========================================================
        # UPDATE GLOBAL STATUS
        # ==========================================================
        _analytics_health_status.update({
            "initialized": result["status"] != "critical",
            "database_connected": result["connected"],
            "status": result["status"],
            "total_records": result["record_count"],
            "total_dns": result["statistics"].get("total_dns", 0),
            "total_dealers": result["statistics"].get("total_dealers", 0),
            "total_warehouses": result["statistics"].get("total_warehouses", 0),
            "total_cities": result["statistics"].get("total_cities", 0),
            "last_check": datetime.now().isoformat(),
            "errors": result["errors"],
            "warnings": result["warnings"]
        })
        
        logger.info(f"✅ Database Health Status: {result['status'].upper()}")
        
        if result["warnings"]:
            logger.warning(f"⚠️ {len(result['warnings'])} warnings detected")
        
        return result
        
    except Exception as e:
        error_msg = f"Database connection failed: {str(e)}"
        result["errors"].append(error_msg)
        result["status"] = "critical"
        logger.error(f"❌ {error_msg}")
        
        _analytics_health_status["errors"] = result["errors"]
        _analytics_health_status["status"] = "critical"
        
        return result
# ==========================================================
# SECTION 2.4: ERROR RESPONSE (NO DUMMY DATA)
# ==========================================================

def _create_error_response(error_msg: str, error_type: str = "SERVICE_INITIALIZATION_FAILED"):
    """
    Create structured error response when service cannot be initialized.
    NEVER returns fake/dummy data.
    """
    error_id = str(uuid.uuid4())[:8]
    
    class ErrorResponse:
        def __init__(self):
            self.error = error_msg
            self.error_id = error_id
            self.error_type = error_type
            self.success = False
        
        def _error_result(self, entity=None, entity_type=None):
            return {
                "error": self.error,
                "error_id": self.error_id,
                "error_type": self.error_type,
                "entity": entity,
                "entity_type": entity_type,
                "suggested_action": "Check PostgreSQL connection, verify data exists, and restart service",
                "timestamp": datetime.now().isoformat()
            }
        
        def get_dn_dashboard(self, dn_no):
            return self._error_result(entity=dn_no, entity_type="dn")
        
        def get_dealer_dashboard(self, dealer_name):
            return self._error_result(entity=dealer_name, entity_type="dealer")
        
        def get_warehouse_dashboard(self, warehouse_name):
            return self._error_result(entity=warehouse_name, entity_type="warehouse")
        
        def get_city_dashboard(self, city_name):
            return self._error_result(entity=city_name, entity_type="city")
        
        def get_product_dashboard(self, product_name):
            return self._error_result(entity=product_name, entity_type="product")
        
        def search_dn(self, *args, **kwargs):
            return []
        
        def search_dealer(self, *args, **kwargs):
            return []
        
        def search_warehouse(self, *args, **kwargs):
            return []
        
        def search_city(self, *args, **kwargs):
            return []
        
        def search_product(self, *args, **kwargs):
            return []
        
        def verify_dn_exists(self, *args, **kwargs):
            return False
        
        def verify_dealer_exists(self, *args, **kwargs):
            return False
        
        def get_dealer_360_dashboard(self, dealer_name):
            return self._error_result(entity=dealer_name, entity_type="dealer")
    
    logger.error(f"❌ Returning error response: {error_msg} (ID: {error_id})")
    return ErrorResponse(), None

# ==========================================================
# SECTION 2.5: MAIN ANALYTICS LOADER
# ==========================================================
# ==========================================================
# BLOCK 2.5: MAIN ANALYTICS LOADER (HOTFIX v14.0)
# ==========================================================

def _get_analytics_service():
    """
    Load analytics service with comprehensive validation.
    BLOCK 2.5 - HOTFIX v14.0 - PRODUCTION GRADE
    
    CRITICAL FIX: Health check warnings do NOT block analytics.
    Only critical failures block analytics.
    """
    logger.info("=" * 70)
    logger.info("🔍 ANALYTICS SERVICE LOADER - HOTFIX v14.0")
    logger.info("=" * 70)
    
    # ==========================================================
    # VALIDATION 1: Check AI Analysis Enabled
    # ==========================================================
    try:
        from app.config import config
        ai_enabled = getattr(config, 'AI_ANALYSIS_ENABLED', True)
        logger.info(f"📌 AI_ANALYSIS_ENABLED: {ai_enabled}")
        
        if not ai_enabled:
            error_msg = "AI_ANALYSIS_ENABLED is False"
            logger.error(f"❌ {error_msg}")
            logger.error("   💡 Set AI_ANALYSIS_ENABLED=True in config")
            return _create_error_response(error_msg, "AI_DISABLED")
    except Exception as e:
        logger.error(f"❌ Config validation failed: {e}")
        return _create_error_response(f"Config validation failed: {str(e)}", "CONFIG_ERROR")
    
    # ==========================================================
    # VALIDATION 2: PostgreSQL Health Check
    # ==========================================================
    health = _validate_postgresql_health()
    
    logger.info(f"📌 PostgreSQL Health Check:")
    logger.info(f"   ✅ Connected: {health['connected']}")
    logger.info(f"   ✅ Status: {health['status']}")
    logger.info(f"   📊 Total Records: {health['record_count']}")
    logger.info(f"   📦 Total DNs: {health['statistics'].get('total_dns', 0)}")
    logger.info(f"   🏪 Total Dealers: {health['statistics'].get('total_dealers', 0)}")
    logger.info(f"   🏭 Total Warehouses: {health['statistics'].get('total_warehouses', 0)}")
    logger.info(f"   🏙️ Total Cities: {health['statistics'].get('total_cities', 0)}")
    
    if health['warnings']:
        logger.warning(f"⚠️ {len(health['warnings'])} warnings detected (non-critical)")
        for warning in health['warnings'][:3]:
            logger.warning(f"   • {warning}")
    
    # ==========================================================
    # CRITICAL: Only fail on critical status
    # ==========================================================
    if health['status'] == 'critical':
        error_msg = f"PostgreSQL health check failed (CRITICAL): {', '.join(health['errors'])}"
        logger.error(f"❌ {error_msg}")
        return _create_error_response(error_msg, "DATABASE_UNHEALTHY")
    
    # ==========================================================
    # VALIDATION 3: Import Analytics Service
    # ==========================================================
    if get_analytics_service is None:
        error_msg = "Analytics service not available (import failed)"
        logger.error(f"❌ {error_msg}")
        return _create_error_response(error_msg, "IMPORT_ERROR")
    
    # ==========================================================
    # VALIDATION 4: Get Service Instance
    # ==========================================================
    try:
        service = get_analytics_service()
        
        if service is None:
            error_msg = "Analytics service returned None"
            logger.error(f"❌ {error_msg}")
            return _create_error_response(error_msg, "SERVICE_NONE")
        
        logger.info(f"📊 Service type: {type(service)}")
        logger.info(f"📊 Service class: {service.__class__.__name__}")
        
    except Exception as e:
        error_msg = f"Failed to get analytics service: {str(e)}"
        logger.error(f"❌ {error_msg}")
        import traceback
        logger.error(traceback.format_exc())
        return _create_error_response(error_msg, "SERVICE_ERROR")
    
    # ==========================================================
    # VALIDATION 5: Verify Required Methods
    # ==========================================================
    required_methods = [
        "get_dn_dashboard",
        "get_dealer_dashboard",
        "get_warehouse_dashboard",
        "get_city_dashboard",
        "get_product_dashboard",
        "search_dn",
        "search_dealer",
        "search_warehouse",
        "search_city",
        "search_product",
        "verify_dn_exists",
        "verify_dealer_exists",
    ]
    
    logger.info("🔍 Verifying analytics methods:")
    missing_methods = []
    
    for method in required_methods:
        if hasattr(service, method):
            logger.info(f"   ✅ {method}: AVAILABLE")
        else:
            missing_methods.append(method)
            logger.error(f"   ❌ {method}: MISSING")
    
    if missing_methods:
        error_msg = f"Missing {len(missing_methods)} required methods: {missing_methods}"
        logger.error(f"❌ {error_msg}")
        return _create_error_response(error_msg, "METHODS_MISSING")
    
    logger.info("=" * 70)
    logger.info("✅ Analytics service initialized successfully")
    logger.info("✅ Service is ready to serve REAL PostgreSQL data")
    logger.info("=" * 70)
    
    return service, AnalyticsResponse


# ==========================================================
# BLOCK 3: CONFIGURATION
# ==========================================================

from app.config import config

CACHE_TTL_SECONDS = getattr(config, 'CACHE_TTL', 300)
CONTEXT_TTL_SECONDS = getattr(config, 'CACHE_TTL_SESSION', 1800)
MAX_RESPONSE_LENGTH = 2500
QUERY_TIMEOUT_SECONDS = getattr(config, 'AI_TIMEOUT_SECONDS', 10)
MAX_RETRY_ATTEMPTS = getattr(config, 'AI_MAX_RETRIES', 3)
AI_ANALYSIS_ENABLED = getattr(config, 'AI_ANALYSIS_ENABLED', True)
FUZZY_MATCH_THRESHOLD = float(os.getenv('FUZZY_MATCH_THRESHOLD', '0.3'))
MAX_FUZZY_RESULTS = int(os.getenv('MAX_FUZZY_RESULTS', '1000'))


# ==========================================================
# BLOCK 4: POSTGRESQL RESOLVER (ENHANCED v5.0)
# ==========================================================

class PostgreSQLResolver:
    """
    Pure PostgreSQL-based entity resolution with caching.
    Enhanced with strict routing priority.
    """
    
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory or SessionLocal
        self.DeliveryReport = DeliveryReport
        
        # Caches with TTL 300 seconds
        self.dn_cache = TTLCache(maxsize=2000, ttl=300)
        self.dealer_cache = TTLCache(maxsize=2000, ttl=300)
        self.warehouse_cache = TTLCache(maxsize=2000, ttl=300)
        self.city_cache = TTLCache(maxsize=2000, ttl=300)
        self.product_cache = TTLCache(maxsize=2000, ttl=300)
        
        self.fuzzy_threshold = FUZZY_MATCH_THRESHOLD
        self.max_fuzzy_results = MAX_FUZZY_RESULTS
        
        # Stats
        self.stats = {
            "dn_lookups": 0,
            "dealer_lookups": 0,
            "warehouse_lookups": 0,
            "city_lookups": 0,
            "product_lookups": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }
    
    def _get_session(self) -> Optional[Session]:
        try:
            return self.session_factory()
        except Exception as e:
            logger.error(f"Session creation failed: {e}")
            return None
    
    def _update_stats(self, entity_type: str, cache_hit: bool = False):
        """Update resolver statistics."""
        if entity_type == "dn":
            self.stats["dn_lookups"] += 1
        elif entity_type == "dealer":
            self.stats["dealer_lookups"] += 1
        elif entity_type == "warehouse":
            self.stats["warehouse_lookups"] += 1
        elif entity_type == "city":
            self.stats["city_lookups"] += 1
        elif entity_type == "product":
            self.stats["product_lookups"] += 1
        
        if cache_hit:
            self.stats["cache_hits"] += 1
        else:
            self.stats["cache_misses"] += 1
    
    # ==========================================================
    # DN RESOLUTION (HARDENED)
    # ==========================================================
    
    def resolve_dn(self, query: str) -> Dict[str, Any]:
        """
        Resolve DN number with hardened support for multiple data types.
        
        Supports:
        - VARCHAR (string)
        - INTEGER (int)
        - BIGINT (long)
        - NUMERIC (decimal)
        
        Normalizes:
        - 6243684514
        - 6243684514.0
        -  6243684514
        """
        self.stats["dn_lookups"] += 1
        
        if not query:
            return {"entity": None, "entity_type": "dn", "found": False, "error": "Empty query"}
        
        # Normalize DN
        raw = str(query).strip()
        # Remove decimal if present
        if '.' in raw:
            raw = raw.split('.')[0]
        # Remove any non-numeric characters
        normalized = re.sub(r'[^0-9]', '', raw)
        
        # Validate length (8-12 digits)
        if len(normalized) < 8 or len(normalized) > 12:
            return {
                "entity": None,
                "entity_type": "dn",
                "found": False,
                "error": f"Invalid DN format: {query} (must be 8-12 digits)",
                "normalized": normalized
            }
        
        # Check cache
        cache_key = f"dn:{normalized}"
        if cache_key in self.dn_cache:
            self.stats["cache_hits"] += 1
            logger.debug(f"DN cache hit: {normalized}")
            result = self.dn_cache[cache_key]
            result["from_cache"] = True
            return result
        
        self.stats["cache_misses"] += 1
        
        session = self._get_session()
        if not session:
            return {
                "entity": None,
                "entity_type": "dn",
                "found": False,
                "error": "Database session unavailable",
                "normalized": normalized
            }
        
        try:
            # Query with cast to handle different data types
            result = session.query(DeliveryReport.dn_no).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
            session.close()
            
            if result:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "dn",
                    "found": True,
                    "normalized": normalized,
                    "match_type": "exact",
                    "confidence": 1.0,
                    "resolution_strategy": "exact_match"
                }
                self.dn_cache[cache_key] = response
                logger.info(f"✅ DN resolved: {resolved}")
                return response
            
            # Try partial match
            try:
                session = self._get_session()
                results = session.query(DeliveryReport.dn_no).filter(
                    DeliveryReport.dn_no.like(f"%{normalized}%")
                ).limit(5).all()
                session.close()
                
                if results:
                    suggestions = [r[0] for r in results]
                    response = {
                        "entity": None,
                        "entity_type": "dn",
                        "found": False,
                        "normalized": normalized,
                        "suggestions": suggestions[:3],
                        "error": f"DN {normalized} not found. Did you mean one of these?"
                    }
                    self.dn_cache[cache_key] = response
                    return response
            except Exception as e:
                logger.warning(f"Partial DN search failed: {e}")
            
            response = {
                "entity": None,
                "entity_type": "dn",
                "found": False,
                "normalized": normalized,
                "error": f"DN {normalized} not found in database"
            }
            self.dn_cache[cache_key] = response
            return response
            
        except Exception as e:
            logger.error(f"DN resolution error: {e}")
            return {
                "entity": None,
                "entity_type": "dn",
                "found": False,
                "error": f"Database error: {str(e)}"
            }
    
    # ==========================================================
    # DEALER RESOLUTION
    # ==========================================================
    
    def resolve_dealer(self, query: str) -> Dict[str, Any]:
        """Resolve dealer name with multiple strategies."""
        self.stats["dealer_lookups"] += 1
        
        if not query or not query.strip():
            return {"entity": None, "entity_type": "dealer", "found": False, "error": "Empty query"}
        
        query_clean = query.strip()
        cache_key = f"dealer:{query_clean.lower()}"
        
        # Check cache
        if cache_key in self.dealer_cache:
            self.stats["cache_hits"] += 1
            result = self.dealer_cache[cache_key]
            result["from_cache"] = True
            return result
        
        self.stats["cache_misses"] += 1
        
        session = self._get_session()
        if not session:
            return {"entity": None, "entity_type": "dealer", "found": False, "error": "Database session unavailable"}
        
        try:
            # STRATEGY 1: Exact match
            result = session.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(query_clean)
            ).first()
            if result:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "dealer",
                    "found": True,
                    "match_type": "exact",
                    "confidence": 1.0,
                    "resolution_strategy": "exact_match"
                }
                self.dealer_cache[cache_key] = response
                session.close()
                logger.info(f"✅ Dealer resolved (exact): {resolved}")
                return response
            
            # STRATEGY 2: ILIKE match
            result = session.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.ilike(f"%{query_clean}%")
            ).first()
            if result:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "dealer",
                    "found": True,
                    "match_type": "partial",
                    "confidence": 0.85,
                    "resolution_strategy": "ilike_match"
                }
                self.dealer_cache[cache_key] = response
                session.close()
                logger.info(f"✅ Dealer resolved (ILIKE): {resolved}")
                return response
            
            # STRATEGY 3: Token-based matching
            tokens = query_clean.split()
            for token in tokens:
                if len(token) > 2 and token.lower() not in ['the', 'and', 'for', 'with']:
                    result = session.query(DeliveryReport.customer_name).filter(
                        DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        response = {
                            "entity": resolved,
                            "entity_type": "dealer",
                            "found": True,
                            "match_type": "token",
                            "confidence": 0.75,
                            "resolution_strategy": f"token_match_{token}",
                            "matched_token": token
                        }
                        self.dealer_cache[cache_key] = response
                        session.close()
                        logger.info(f"✅ Dealer resolved (token '{token}'): {resolved}")
                        return response
            
            # STRATEGY 4: Suggestions
            try:
                similar = session.query(DeliveryReport.customer_name).filter(
                    DeliveryReport.customer_name.ilike(f"%{query_clean[:3]}%")
                ).limit(5).all()
                session.close()
                
                if similar:
                    suggestions = [s[0] for s in similar if s[0]]
                    response = {
                        "entity": None,
                        "entity_type": "dealer",
                        "found": False,
                        "suggestions": suggestions[:3],
                        "error": f"Dealer '{query_clean}' not found. Did you mean one of these?"
                    }
                    self.dealer_cache[cache_key] = response
                    return response
            except Exception as e:
                logger.warning(f"Similar dealer search failed: {e}")
            
            session.close()
            response = {
                "entity": None,
                "entity_type": "dealer",
                "found": False,
                "error": f"Dealer '{query_clean}' not found in database"
            }
            self.dealer_cache[cache_key] = response
            return response
            
        except Exception as e:
            logger.error(f"Dealer resolution error: {e}")
            return {"entity": None, "entity_type": "dealer", "found": False, "error": str(e)}
    
    # ==========================================================
    # WAREHOUSE RESOLUTION
    # ==========================================================
    
    def resolve_warehouse(self, query: str) -> Dict[str, Any]:
        """Resolve warehouse name."""
        self.stats["warehouse_lookups"] += 1
        
        if not query or not query.strip():
            return {"entity": None, "entity_type": "warehouse", "found": False, "error": "Empty query"}
        
        query_clean = query.strip()
        cache_key = f"warehouse:{query_clean.lower()}"
        
        if cache_key in self.warehouse_cache:
            self.stats["cache_hits"] += 1
            result = self.warehouse_cache[cache_key]
            result["from_cache"] = True
            return result
        
        self.stats["cache_misses"] += 1
        
        session = self._get_session()
        if not session:
            return {"entity": None, "entity_type": "warehouse", "found": False, "error": "Database session unavailable"}
        
        try:
            # Try exact match
            result = session.query(DeliveryReport.warehouse).filter(
                func.lower(DeliveryReport.warehouse) == func.lower(query_clean)
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "warehouse",
                    "found": True,
                    "match_type": "exact",
                    "confidence": 1.0,
                    "resolution_strategy": "exact_match"
                }
                self.warehouse_cache[cache_key] = response
                session.close()
                logger.info(f"✅ Warehouse resolved: {resolved}")
                return response
            
            # Try ILIKE
            result = session.query(DeliveryReport.warehouse).filter(
                DeliveryReport.warehouse.ilike(f"%{query_clean}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "warehouse",
                    "found": True,
                    "match_type": "partial",
                    "confidence": 0.85,
                    "resolution_strategy": "ilike_match"
                }
                self.warehouse_cache[cache_key] = response
                session.close()
                logger.info(f"✅ Warehouse resolved (ILIKE): {resolved}")
                return response
            
            session.close()
            response = {
                "entity": None,
                "entity_type": "warehouse",
                "found": False,
                "error": f"Warehouse '{query_clean}' not found in database"
            }
            self.warehouse_cache[cache_key] = response
            return response
            
        except Exception as e:
            logger.error(f"Warehouse resolution error: {e}")
            return {"entity": None, "entity_type": "warehouse", "found": False, "error": str(e)}
    
    # ==========================================================
    # CITY RESOLUTION
    # ==========================================================
    
    def resolve_city(self, query: str) -> Dict[str, Any]:
        """Resolve city name."""
        self.stats["city_lookups"] += 1
        
        if not query or not query.strip():
            return {"entity": None, "entity_type": "city", "found": False, "error": "Empty query"}
        
        query_clean = query.strip()
        cache_key = f"city:{query_clean.lower()}"
        
        if cache_key in self.city_cache:
            self.stats["cache_hits"] += 1
            result = self.city_cache[cache_key]
            result["from_cache"] = True
            return result
        
        self.stats["cache_misses"] += 1
        
        session = self._get_session()
        if not session:
            return {"entity": None, "entity_type": "city", "found": False, "error": "Database session unavailable"}
        
        try:
            result = session.query(DeliveryReport.ship_to_city).filter(
                func.lower(DeliveryReport.ship_to_city) == func.lower(query_clean)
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "city",
                    "found": True,
                    "match_type": "exact",
                    "confidence": 1.0,
                    "resolution_strategy": "exact_match"
                }
                self.city_cache[cache_key] = response
                session.close()
                logger.info(f"✅ City resolved: {resolved}")
                return response
            
            result = session.query(DeliveryReport.ship_to_city).filter(
                DeliveryReport.ship_to_city.ilike(f"%{query_clean}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "city",
                    "found": True,
                    "match_type": "partial",
                    "confidence": 0.85,
                    "resolution_strategy": "ilike_match"
                }
                self.city_cache[cache_key] = response
                session.close()
                logger.info(f"✅ City resolved (ILIKE): {resolved}")
                return response
            
            session.close()
            response = {
                "entity": None,
                "entity_type": "city",
                "found": False,
                "error": f"City '{query_clean}' not found in database"
            }
            self.city_cache[cache_key] = response
            return response
            
        except Exception as e:
            logger.error(f"City resolution error: {e}")
            return {"entity": None, "entity_type": "city", "found": False, "error": str(e)}
    
    # ==========================================================
    # PRODUCT RESOLUTION
    # ==========================================================
    
    def resolve_product(self, query: str) -> Dict[str, Any]:
        """Resolve product name."""
        self.stats["product_lookups"] += 1
        
        if not query or not query.strip():
            return {"entity": None, "entity_type": "product", "found": False, "error": "Empty query"}
        
        query_clean = query.strip()
        cache_key = f"product:{query_clean.lower()}"
        
        if cache_key in self.product_cache:
            self.stats["cache_hits"] += 1
            result = self.product_cache[cache_key]
            result["from_cache"] = True
            return result
        
        self.stats["cache_misses"] += 1
        
        session = self._get_session()
        if not session:
            return {"entity": None, "entity_type": "product", "found": False, "error": "Database session unavailable"}
        
        try:
            # Try customer_model first
            result = session.query(DeliveryReport.customer_model).filter(
                func.lower(DeliveryReport.customer_model) == func.lower(query_clean)
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "product",
                    "found": True,
                    "match_type": "exact",
                    "confidence": 1.0,
                    "resolution_strategy": "exact_match_model"
                }
                self.product_cache[cache_key] = response
                session.close()
                logger.info(f"✅ Product resolved (model): {resolved}")
                return response
            
            # Try material_no
            result = session.query(DeliveryReport.material_no).filter(
                func.lower(DeliveryReport.material_no) == func.lower(query_clean)
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "product",
                    "found": True,
                    "match_type": "exact",
                    "confidence": 1.0,
                    "resolution_strategy": "exact_match_material"
                }
                self.product_cache[cache_key] = response
                session.close()
                logger.info(f"✅ Product resolved (material): {resolved}")
                return response
            
            # Try ILIKE on customer_model
            result = session.query(DeliveryReport.customer_model).filter(
                DeliveryReport.customer_model.ilike(f"%{query_clean}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "product",
                    "found": True,
                    "match_type": "partial",
                    "confidence": 0.85,
                    "resolution_strategy": "ilike_match_model"
                }
                self.product_cache[cache_key] = response
                session.close()
                logger.info(f"✅ Product resolved (ILIKE model): {resolved}")
                return response
            
            session.close()
            response = {
                "entity": None,
                "entity_type": "product",
                "found": False,
                "error": f"Product '{query_clean}' not found in database"
            }
            self.product_cache[cache_key] = response
            return response
            
        except Exception as e:
            logger.error(f"Product resolution error: {e}")
            return {"entity": None, "entity_type": "product", "found": False, "error": str(e)}
    
    # ==========================================================
    # UNIVERSAL ENTITY RESOLVER
    # ==========================================================
    
    def resolve_entity(self, query: str) -> Dict[str, Any]:
        """
        Universal entity resolver with strict priority ordering.
        
        Priority Order:
        1. DN (8-12 digits)
        2. Warehouse
        3. City
        4. Product
        5. Dealer (last priority)
        """
        if not query or not query.strip():
            return {"entity": None, "entity_type": "unknown", "found": False}
        
        query_clean = query.strip()
        logger.info(f"🔍 Universal entity resolution for: '{query_clean}'")
        
        # ==========================================================
        # PRIORITY 1: DN Resolution
        # ==========================================================
        dn_match = re.search(r'\b(\d{8,12})\b', query_clean)
        if dn_match:
            result = self.resolve_dn(dn_match.group(1))
            if result.get('found'):
                logger.info(f"✅ Entity resolved as DN: {result.get('entity')}")
                return result
        
        # ==========================================================
        # PRIORITY 2: Warehouse Resolution
        # ==========================================================
        warehouse_keywords = ['warehouse', 'wh', 'depot', 'godown', 'distribution center']
        if any(kw in query_clean.lower() for kw in warehouse_keywords):
            result = self.resolve_warehouse(query_clean)
            if result.get('found'):
                logger.info(f"✅ Entity resolved as Warehouse: {result.get('entity')}")
                return result
        
        # ==========================================================
        # PRIORITY 3: City Resolution
        # ==========================================================
        city_keywords = ['city', 'town', 'district', 'region']
        if any(kw in query_clean.lower() for kw in city_keywords):
            result = self.resolve_city(query_clean)
            if result.get('found'):
                logger.info(f"✅ Entity resolved as City: {result.get('entity')}")
                return result
        
        # ==========================================================
        # PRIORITY 4: Product Resolution
        # ==========================================================
        product_keywords = [
            'refrigerator', 'fridge', 'freezer', 'ac', 'air conditioner',
            'washing machine', 'washer', 'led', 'tv', 'television',
            'microwave', 'oven', 'water dispenser', 'cooler', 'heater'
        ]
        if any(kw in query_clean.lower() for kw in product_keywords):
            result = self.resolve_product(query_clean)
            if result.get('found'):
                logger.info(f"✅ Entity resolved as Product: {result.get('entity')}")
                return result
        
        # ==========================================================
        # PRIORITY 5: Dealer Resolution (LAST)
        # ==========================================================
        result = self.resolve_dealer(query_clean)
        if result.get('found'):
            logger.info(f"✅ Entity resolved as Dealer: {result.get('entity')}")
            return result
        
        # ==========================================================
        # NO MATCH FOUND
        # ==========================================================
        logger.warning(f"❌ No entity resolved for: '{query_clean}'")
        return {
            "entity": None,
            "entity_type": "unknown",
            "found": False,
            "error": f"No entity found for '{query_clean}'"
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get resolver statistics."""
        return {
            "dn_lookups": self.stats["dn_lookups"],
            "dealer_lookups": self.stats["dealer_lookups"],
            "warehouse_lookups": self.stats["warehouse_lookups"],
            "city_lookups": self.stats["city_lookups"],
            "product_lookups": self.stats["product_lookups"],
            "cache_hits": self.stats["cache_hits"],
            "cache_misses": self.stats["cache_misses"],
            "cache_hit_ratio": round(
                self.stats["cache_hits"] / (self.stats["cache_hits"] + self.stats["cache_misses"]) * 100,
                1
            ) if (self.stats["cache_hits"] + self.stats["cache_misses"]) > 0 else 0
        }


# ==========================================================
# BLOCK 5: CONVERSATION CONTEXT
# ==========================================================

@dataclass
class ConversationContext:
    phone_number: str
    last_intent: Optional[str] = None
    last_entity: Optional[str] = None
    last_dealer: Optional[str] = None
    last_warehouse: Optional[str] = None
    last_city: Optional[str] = None
    last_dn: Optional[str] = None
    last_product: Optional[str] = None
    last_distance: Optional[Dict] = None
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    confidence: float = 0.0
    is_valid: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_city": self.last_city,
            "last_dn": self.last_dn,
            "last_product": self.last_product,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number,
            "message_count": self.message_count
        }


# ==========================================================
# BLOCK 6: INTENT CLASSIFIER (ENHANCED v5.0)
# ==========================================================

@dataclass
class IntentResult:
    intent: str
    confidence: float
    entity_type: Optional[str] = None
    entity_value: Optional[str] = None
    raw_query: str = ""
    normalized_query: str = ""

class IntentClassifier:
    """
    Enhanced intent classifier with strict priority-based routing.
    
    PRIORITY ORDER:
    1. DN Number (8-12 digits)
    2. Warehouse (explicit keywords)
    3. City (explicit keywords)
    4. Product (product keywords)
    5. Dealer (last priority)
    6. Distance Query
    7. General AI Query
    
    NEVER allows: Warehouse→Dealer, City→Dealer, Product→Dealer
    """
    
    # ==========================================================
    # KEYWORD DEFINITIONS
    # ==========================================================
    
    PRODUCT_KEYWORDS = {
        'refrigerator': 0.95, 'fridge': 0.95, 'freezer': 0.95,
        'deep freezer': 0.95, 'ac': 0.95, 'air conditioner': 0.95,
        'washing machine': 0.95, 'washer': 0.90, 'led': 0.90,
        'tv': 0.90, 'television': 0.90, 'microwave': 0.95,
        'oven': 0.90, 'water dispenser': 0.95, 'cooler': 0.90,
        'heater': 0.90, 'generator': 0.85
    }
    
    WAREHOUSE_KEYWORDS = {
        'warehouse': 0.95, 'wh': 0.90, 'depot': 0.90,
        'distribution center': 0.90, 'godown': 0.85
    }
    
    CITY_KEYWORDS = {
        'city': 0.95, 'town': 0.85, 'district': 0.85,
        'region': 0.80, 'area': 0.70
    }
    
    DEALER_INDICATORS = {
        'electronics': 0.70, 'trading': 0.60,
        'enterprise': 0.60, 'corporation': 0.60,
        'industries': 0.60, 'traders': 0.60,
        'house': 0.50, 'store': 0.50, 'mart': 0.50,
        'company': 0.50
    }
    
    DISTANCE_KEYWORDS = {
        'distance': 0.95, 'from': 0.90, 'to': 0.90,
        'between': 0.90, 'drive': 0.85, 'driving': 0.85,
        'travel': 0.85, 'km': 0.80, 'miles': 0.80
    }
    
    # ==========================================================
    # PATTERNS
    # ==========================================================
    
    DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
    WAREHOUSE_PATTERN = re.compile(r'(?:warehouse|wh|depot|godown)\s+([A-Za-z0-9\s\-&]+)', re.IGNORECASE)
    CITY_PATTERN = re.compile(r'(?:city|town|district|region)\s+([A-Za-z\s\-]+)', re.IGNORECASE)
    DISTANCE_PATTERN = re.compile(r'(?:distance|from|to|between)\s+([A-Za-z\s\-]+)\s+(?:to|and|from)\s+([A-Za-z\s\-]+)', re.IGNORECASE)
    
    # ==========================================================
    # INITIALIZATION
    # ==========================================================
    
    def __init__(self, resolver: PostgreSQLResolver):
        self.resolver = resolver
        self._cache = TTLCache(maxsize=1000, ttl=300)
        self._stats = {
            "total_classifications": 0,
            "intent_counts": {},
            "confidence_sum": 0
        }
    
    # ==========================================================
    # MAIN CLASSIFY METHOD
    # ==========================================================
    
    def classify(self, query: str, context: Optional[ConversationContext] = None) -> IntentResult:
        """Classify intent with strict priority-based routing."""
        if not query or not query.strip():
            return IntentResult(intent="help", confidence=1.0, raw_query=query)
        
        query_clean = query.strip()
        query_lower = query_clean.lower()
        cache_key = f"intent:{query_lower}"
        
        # Check cache
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if cached.confidence > 0.5:
                self._stats["total_classifications"] += 1
                return cached
        
        # Classify with priority
        result = self._classify_with_priority(query_clean, query_lower, context)
        
        # Cache result
        self._cache[cache_key] = result
        
        # Update stats
        self._stats["total_classifications"] += 1
        self._stats["intent_counts"][result.intent] = self._stats["intent_counts"].get(result.intent, 0) + 1
        self._stats["confidence_sum"] += result.confidence
        
        return result
    
    # ==========================================================
    # PRIORITY CLASSIFICATION
    # ==========================================================
    
    def _classify_with_priority(self, query: str, query_lower: str, context: Optional[ConversationContext]) -> IntentResult:
        """
        Classify intent with strict priority order.
        
        PRIORITY 1: DN Number
        PRIORITY 2: Warehouse
        PRIORITY 3: City
        PRIORITY 4: Product
        PRIORITY 5: Dealer (LAST)
        PRIORITY 6: Distance Query
        PRIORITY 7: Context Fallback
        """
        
        # ==========================================================
        # PRIORITY 1: DN SEARCH
        # ==========================================================
        dn_match = self.DN_PATTERN.search(query)
        if dn_match:
            dn_number = dn_match.group(1)
            result = self.resolver.resolve_dn(dn_number)
            if result.get('found'):
                return IntentResult(
                    intent="dn_dashboard",
                    confidence=1.0,
                    entity_type="dn",
                    entity_value=result.get('entity'),
                    raw_query=query,
                    normalized_query=dn_number
                )
            # Still route to DN dashboard with the number they provided
            return IntentResult(
                intent="dn_dashboard",
                confidence=0.9,
                entity_type="dn",
                entity_value=dn_number,
                raw_query=query,
                normalized_query=dn_number
            )
        
        # ==========================================================
        # PRIORITY 2: WAREHOUSE
        # ==========================================================
        for keyword, confidence in self.WAREHOUSE_KEYWORDS.items():
            if keyword in query_lower:
                warehouse_match = self.WAREHOUSE_PATTERN.search(query)
                if warehouse_match:
                    warehouse_name = warehouse_match.group(1).strip()
                    if len(warehouse_name) > 1:
                        result = self.resolver.resolve_warehouse(warehouse_name)
                        if result.get('found'):
                            return IntentResult(
                                intent="warehouse_dashboard",
                                confidence=confidence,
                                entity_type="warehouse",
                                entity_value=result.get('entity'),
                                raw_query=query,
                                normalized_query=warehouse_name
                            )
                        else:
                            # Still route to warehouse dashboard
                            return IntentResult(
                                intent="warehouse_dashboard",
                                confidence=confidence * 0.8,
                                entity_type="warehouse",
                                entity_value=warehouse_name,
                                raw_query=query,
                                normalized_query=warehouse_name
                            )
        
        # ==========================================================
        # PRIORITY 3: CITY
        # ==========================================================
        for keyword, confidence in self.CITY_KEYWORDS.items():
            if keyword in query_lower:
                city_match = self.CITY_PATTERN.search(query)
                if city_match:
                    city_name = city_match.group(1).strip()
                    if len(city_name) > 1:
                        result = self.resolver.resolve_city(city_name)
                        if result.get('found'):
                            return IntentResult(
                                intent="city_dashboard",
                                confidence=confidence,
                                entity_type="city",
                                entity_value=result.get('entity'),
                                raw_query=query,
                                normalized_query=city_name
                            )
                        else:
                            return IntentResult(
                                intent="city_dashboard",
                                confidence=confidence * 0.8,
                                entity_type="city",
                                entity_value=city_name,
                                raw_query=query,
                                normalized_query=city_name
                            )
        
        # ==========================================================
        # PRIORITY 4: PRODUCT
        # ==========================================================
        best_product_match = None
        best_product_score = 0
        
        for keyword, score in self.PRODUCT_KEYWORDS.items():
            if keyword in query_lower:
                if score > best_product_score:
                    best_product_score = score
                    best_product_match = keyword
        
        if best_product_match:
            result = self.resolver.resolve_product(best_product_match)
            if result.get('found'):
                return IntentResult(
                    intent="product_dashboard",
                    confidence=best_product_score,
                    entity_type="product",
                    entity_value=result.get('entity'),
                    raw_query=query,
                    normalized_query=best_product_match
                )
            else:
                return IntentResult(
                    intent="product_dashboard",
                    confidence=best_product_score * 0.8,
                    entity_type="product",
                    entity_value=best_product_match,
                    raw_query=query,
                    normalized_query=best_product_match
                )
        
        # ==========================================================
        # PRIORITY 5: DEALER (LAST)
        # ==========================================================
        # Check for dealer indicators
        dealer_score = 0
        for keyword, score in self.DEALER_INDICATORS.items():
            if keyword in query_lower:
                dealer_score = max(dealer_score, score)
        
        if dealer_score > 0.3:
            result = self.resolver.resolve_dealer(query)
            if result.get('found'):
                return IntentResult(
                    intent="dealer_dashboard",
                    confidence=dealer_score,
                    entity_type="dealer",
                    entity_value=result.get('entity'),
                    raw_query=query,
                    normalized_query=query
                )
        
        # Try standalone entity detection with correct priority
        if len(query) > 2 and not any(c.isdigit() for c in query):
            # Try warehouse first
            warehouse_result = self.resolver.resolve_warehouse(query)
            if warehouse_result.get('found'):
                return IntentResult(
                    intent="warehouse_dashboard",
                    confidence=0.7,
                    entity_type="warehouse",
                    entity_value=warehouse_result.get('entity'),
                    raw_query=query,
                    normalized_query=query
                )
            
            # Try city
            city_result = self.resolver.resolve_city(query)
            if city_result.get('found'):
                return IntentResult(
                    intent="city_dashboard",
                    confidence=0.7,
                    entity_type="city",
                    entity_value=city_result.get('entity'),
                    raw_query=query,
                    normalized_query=query
                )
            
            # Try product
            product_result = self.resolver.resolve_product(query)
            if product_result.get('found'):
                return IntentResult(
                    intent="product_dashboard",
                    confidence=0.7,
                    entity_type="product",
                    entity_value=product_result.get('entity'),
                    raw_query=query,
                    normalized_query=query
                )
            
            # Finally dealer (LAST)
            dealer_result = self.resolver.resolve_dealer(query)
            if dealer_result.get('found'):
                return IntentResult(
                    intent="dealer_dashboard",
                    confidence=0.6,
                    entity_type="dealer",
                    entity_value=dealer_result.get('entity'),
                    raw_query=query,
                    normalized_query=query
                )
        
        # ==========================================================
        # PRIORITY 6: DISTANCE QUERY
        # ==========================================================
        distance_match = self.DISTANCE_PATTERN.search(query)
        if distance_match:
            origin = distance_match.group(1).strip()
            destination = distance_match.group(2).strip()
            if origin and destination:
                return IntentResult(
                    intent="distance_query",
                    confidence=0.85,
                    entity_type="distance",
                    entity_value=f"{origin} to {destination}",
                    raw_query=query,
                    normalized_query=f"{origin}|{destination}"
                )
        
        # ==========================================================
        # PRIORITY 7: CONTEXT FALLBACK
        # ==========================================================
        if context and context.last_intent and context.last_entity:
            return IntentResult(
                intent=context.last_intent,
                confidence=0.5,
                entity_type="context",
                entity_value=context.last_entity,
                raw_query=query,
                normalized_query=query
            )
        
        # ==========================================================
        # DEFAULT: HELP
        # ==========================================================
        return IntentResult(
            intent="help",
            confidence=0.5,
            raw_query=query,
            normalized_query=query
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get classifier statistics."""
        return {
            "total_classifications": self._stats["total_classifications"],
            "intent_counts": self._stats["intent_counts"],
            "average_confidence": round(
                self._stats["confidence_sum"] / self._stats["total_classifications"],
                2
            ) if self._stats["total_classifications"] > 0 else 0
        }


# ==========================================================
# BLOCK 7: MAIN AI ORCHESTRATOR (FULLY INTEGRATED)
# ==========================================================

class AIOrchestrator:
    """
    Complete AI Orchestrator with all services integrated.
    Enterprise Production Grade.
    """
    
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory or SessionLocal
        
        # Initialize all services
        self._analytics = None
        self._analytics_response = None
        self._resolver = None
        self._classifier = None
        self._distance_service = None
        self._dealer_analytics = None
        
        # Caches
        self.response_cache = TTLCache(maxsize=2000, ttl=CACHE_TTL_SECONDS)
        self.failure_cache = TTLCache(maxsize=400, ttl=60)
        self.fast_cache = LRUCache(maxsize=1000)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        self._current_request_id: Optional[str] = None
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "intent_detection": {},
            "entity_resolution": {},
            "errors": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "avg_response_time": 0
        }
        
        logger.info("=" * 70)
        logger.info("AI Router v31.0 - ENTERPRISE PRODUCTION")
        logger.info("=" * 70)
        
        # Initialize all services
        self._init_services()
        
        logger.info("=" * 70)
        logger.info("✅ AI Router v31.0 initialized successfully")
        logger.info("✅ All services integrated and ready")
        logger.info("=" * 70)
    
    def _init_services(self):
        """Initialize all services with error handling."""
        # 1. Analytics Service
        try:
            self._init_analytics()
        except Exception as e:
            logger.error(f"❌ Analytics init failed: {e}")
            self._analytics = None
        
        # 2. Resolver
        try:
            self._resolver = PostgreSQLResolver(self.session_factory)
            logger.info("✅ Resolver initialized")
        except Exception as e:
            logger.error(f"❌ Resolver init failed: {e}")
            self._resolver = None
        
        # 3. Classifier
        try:
            self._classifier = IntentClassifier(self.resolver)
            logger.info("✅ Classifier initialized")
        except Exception as e:
            logger.error(f"❌ Classifier init failed: {e}")
            self._classifier = None
        
        # 4. Distance Service (lazy)
        try:
            if DistanceService:
                self._distance_service = DistanceService(self.session_factory)
                logger.info("✅ Distance service initialized")
            else:
                logger.warning("⚠️ Distance service not available")
        except Exception as e:
            logger.error(f"❌ Distance service init failed: {e}")
            self._distance_service = None
        
        # 5. Dealer Analytics (lazy loaded)
        try:
            DealerAnalyticsService, _ = _get_dealer_analytics_service()
            if DealerAnalyticsService:
                self._dealer_analytics = DealerAnalyticsService(self.session_factory, self.resolver, None)
                logger.info("✅ Dealer Analytics service initialized")
            else:
                logger.warning("⚠️ Dealer Analytics service not available")
        except Exception as e:
            logger.error(f"❌ Dealer Analytics service init failed: {e}")
            self._dealer_analytics = None
    
    def _init_analytics(self):
        """Initialize analytics service with retry."""
        for attempt in range(3):
            try:
                logger.info(f"🔄 Attempt {attempt + 1}/3 to initialize analytics...")
                service, response_class = _get_analytics_service()
                self._analytics = service
                self._analytics_response = response_class
                
                if self._analytics is not None:
                    logger.info(f"✅ Analytics service initialized on attempt {attempt + 1}")
                    return
                else:
                    logger.warning(f"⚠️ Analytics service None on attempt {attempt + 1}")
                    time.sleep(1)
            except Exception as e:
                logger.error(f"❌ Attempt {attempt + 1} failed: {e}")
                time.sleep(1)
        
        logger.error("❌ All attempts to initialize analytics failed!")
        # Do NOT create fallback - return structured error
        self._analytics = None
    
    @property
    def analytics(self):
        """Get analytics service with lazy reload."""
        if self._analytics is None:
            logger.warning("⚠️ Analytics service is None - attempting to reload...")
            try:
                service, response_class = _get_analytics_service()
                self._analytics = service
                self._analytics_response = response_class
                if self._analytics is None:
                    logger.error("❌ Analytics service still None after reload")
            except Exception as e:
                logger.error(f"❌ Reload failed: {e}")
        return self._analytics
    
    @property
    def resolver(self):
        if self._resolver is None:
            self._resolver = PostgreSQLResolver(self.session_factory)
        return self._resolver
    
    @property
    def classifier(self):
        if self._classifier is None:
            self._classifier = IntentClassifier(self.resolver)
        return self._classifier
    
    @property
    def distance_service(self):
        if self._distance_service is None and DistanceService:
            try:
                self._distance_service = DistanceService(self.session_factory)
                logger.info("✅ Distance service initialized (lazy)")
            except Exception as e:
                logger.error(f"Distance service init failed: {e}")
                self._distance_service = None
        return self._distance_service
    
    @property
    def dealer_analytics(self):
        if self._dealer_analytics is None:
            try:
                DealerAnalyticsService, _ = _get_dealer_analytics_service()
                if DealerAnalyticsService:
                    self._dealer_analytics = DealerAnalyticsService(self.session_factory, self.resolver, None)
                    logger.info("✅ Dealer Analytics service initialized (lazy)")
                else:
                    logger.warning("⚠️ Dealer Analytics service not available")
            except Exception as e:
                logger.error(f"Dealer Analytics init failed: {e}")
                self._dealer_analytics = None
        return self._dealer_analytics
    
    def _load_context(self, phone_number: Optional[str]) -> Optional[ConversationContext]:
        """Load conversation context."""
        if not phone_number:
            return None
        
        if phone_number not in self.conversation_cache:
            self.conversation_cache[phone_number] = ConversationContext(phone_number=phone_number)
        
        context = self.conversation_cache[phone_number]
        if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
            context = ConversationContext(phone_number=phone_number)
            self.conversation_cache[phone_number] = context
        
        return context
    
    def _update_context(self, phone_number: Optional[str], intent: str, entity_type: str, entity: str):
        """Update conversation context."""
        if not phone_number or not entity:
            return
        
        context = self._load_context(phone_number)
        if not context:
            return
        
        context.last_intent = intent
        context.last_entity = entity
        context.message_count += 1
        context.last_updated = time.time()
        context.confidence = 0.9
        
        if entity_type == "dealer":
            context.last_dealer = entity
        elif entity_type == "warehouse":
            context.last_warehouse = entity
        elif entity_type == "city":
            context.last_city = entity
        elif entity_type == "dn":
            context.last_dn = entity
        elif entity_type == "product":
            context.last_product = entity
        
        self.conversation_cache[phone_number] = context
    
    def _validate_response(self, response, service_name: str, req_id: str) -> Tuple[bool, str, Optional[Dict]]:
        """Validate response from any service."""
        if response is None:
            return False, "No response received", None
        
        if isinstance(response, dict):
            if "error" in response:
                return False, response.get("error", "Unknown error"), response
            if not response:
                return False, "Empty response", None
            return True, "", response
        
        if hasattr(response, 'success'):
            if not response.success:
                error = getattr(response, 'error', 'Unknown error')
                return False, error, getattr(response, 'data', {})
            data = getattr(response, 'data', {})
            return True, "", data
        
        if isinstance(response, list):
            return True, "", {"results": response}
        
        return True, "", {"data": response}
    
    def _create_error_response(self, error_msg: str, error_type: str, entity: str = None, entity_type: str = None) -> str:
        """Create structured error response."""
        error_id = str(uuid.uuid4())[:8]
        
        response = (
            f"❌ *{error_type.replace('_', ' ').title()}*\n\n"
            f"{error_msg}\n\n"
        )
        
        if entity:
            response += f"🔍 Entity: {entity}\n"
        if entity_type:
            response += f"📊 Type: {entity_type}\n"
        
        response += (
            f"🆔 Tracking ID: {error_id}\n\n"
            f"💡 *Suggested Action:*\n"
            f"Please check the input and try again. If the issue persists, contact support."
        )
        
        return response
    
    def _truncate_response(self, response: str) -> str:
        """Truncate response if too long."""
        if len(response) > MAX_RESPONSE_LENGTH:
            return response[:MAX_RESPONSE_LENGTH - 20] + "\n\n... (truncated)"
        return response


# ==========================================================
# BLOCK 8: MAIN ENTRY POINT
# ==========================================================

    def process_whatsapp_query(
        self,
        question: str,
        session_factory: Optional[Callable[[], Session]] = None,
        phone_number: Optional[str] = None,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> str:
        """
        Process WhatsApp query with full service integration.
        Enterprise Production Grade.
        """
        start_time = time.time()
        
        if not getattr(config, 'AI_ANALYSIS_ENABLED', True):
            return "⚠️ AI service is currently disabled. Please contact support."
        
        req_id = request_id or str(uuid.uuid4())[:8]
        self.metrics["total_requests"] += 1
        
        logger.info(f"[{req_id}] 📥 Processing: '{question[:100]}'")
        
        if session_factory:
            self.session_factory = session_factory
        
        if not question or len(question.strip()) < 2:
            return "Please provide a valid question. Type 'help' for menu."
        
        try:
            context = self._load_context(phone_number)
            
            # Check if analytics is available
            if self.analytics is None:
                error_msg = "Analytics service is not available. Please check the database connection."
                logger.error(f"[{req_id}] ❌ {error_msg}")
                return self._create_error_response(
                    error_msg,
                    "SERVICE_UNAVAILABLE",
                    question,
                    "unknown"
                )
            
            # Classify intent
            intent_result = self.classifier.classify(question.strip(), context)
            
            logger.info(f"[{req_id}] 🎯 Intent: {intent_result.intent}")
            logger.info(f"[{req_id}] 📊 Entity: {intent_result.entity_value}")
            
            if intent_result.intent == "help":
                return self._get_help_message()
            
            # Route to appropriate dashboard
            result = self._route_to_dashboard(
                intent_result.intent,
                intent_result.entity_value,
                intent_result.entity_type,
                context,
                req_id
            )
            
            if result:
                self._update_context(
                    phone_number,
                    intent_result.intent,
                    intent_result.entity_type or "unknown",
                    intent_result.entity_value or "unknown"
                )
                elapsed = time.time() - start_time
                self.metrics["avg_response_time"] = (
                    (self.metrics["avg_response_time"] * (self.metrics["successful_requests"]) + elapsed) /
                    (self.metrics["successful_requests"] + 1)
                )
                self.metrics["successful_requests"] += 1
                logger.info(f"[{req_id}] ✅ Completed in {elapsed:.3f}s")
                return result
            
            return self._get_help_message()
            
        except Exception as e:
            self.metrics["errors"] += 1
            self.metrics["failed_requests"] += 1
            logger.exception(f"[{req_id}] ❌ ERROR: {e}")
            return self._create_error_response(
                f"An unexpected error occurred: {str(e)[:100]}",
                "PROCESSING_ERROR",
                question,
                "unknown"
            )


# ==========================================================
# BLOCK 9: ROUTING ENGINE WITH FULL INTEGRATION
# ==========================================================

    def _route_to_dashboard(self, intent: str, entity: Optional[str], 
                            entity_type: Optional[str], 
                            context: Optional[ConversationContext], 
                            req_id: str) -> Optional[str]:
        """Route to appropriate dashboard with full service integration."""
        if not self.analytics:
            return self._create_error_response(
                "Analytics service is not available",
                "SERVICE_UNAVAILABLE",
                entity,
                entity_type
            )
        
        ROUTE_MAP = {
            "dn_dashboard": self._route_dn_dashboard,
            "dealer_dashboard": self._route_dealer_dashboard,
            "warehouse_dashboard": self._route_warehouse_dashboard,
            "city_dashboard": self._route_city_dashboard,
            "product_dashboard": self._route_product_dashboard,
            "distance_query": self._route_distance_query,
        }
        
        try:
            handler = ROUTE_MAP.get(intent)
            if handler:
                return handler(entity, context, req_id)
            return None
        except Exception as e:
            logger.error(f"[{req_id}] Routing error: {e}")
            return self._create_error_response(
                f"Routing error: {str(e)[:100]}",
                "ROUTING_ERROR",
                entity,
                entity_type
            )


# ==========================================================
# BLOCK 10: ROUTE HANDLERS WITH SERVICE INTEGRATION
# ==========================================================

    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """
        Handle DN dashboard with complete validation and error handling.
        """
        import time
        start_time = time.time()
        
        logger.info(f"[{req_id}] 📄 DN Dashboard route called")
        
        dn_number = entity or (context.last_dn if context else None)
        
        if not dn_number:
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number.\n\n*Example:* 6243675570"
        
        # Clean DN - remove non-numeric characters
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        
        # Validate DN format (8-12 digits)
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return self._create_error_response(
                f"Invalid DN number: '{dn_number}'. DN numbers must be 8-12 digits.",
                "INVALID_DN",
                dn_number,
                "dn"
            )
        
        logger.info(f"[{req_id}] 🔍 Looking up DN: {dn_clean}")
        
        # ==========================================================
        # STEP 1: Verify Analytics Service
        # ==========================================================
        if self.analytics is None:
            return self._create_error_response(
                "Analytics service is not available",
                "SERVICE_UNAVAILABLE",
                dn_clean,
                "dn"
            )
        
        if not hasattr(self.analytics, 'get_dn_dashboard'):
            return self._create_error_response(
                "DN dashboard method not available",
                "METHOD_UNAVAILABLE",
                dn_clean,
                "dn"
            )
        
        # ==========================================================
        # STEP 2: Get DN Dashboard
        # ==========================================================
        try:
            logger.info(f"[{req_id}] 📊 Calling analytics.get_dn_dashboard('{dn_clean}')")
            response = self.analytics.get_dn_dashboard(dn_clean)
            logger.info(f"[{req_id}] 📊 Response type: {type(response)}")
            
            # ==========================================================
            # STEP 3: Validate Response
            # ==========================================================
            is_valid, error_msg, data = self._validate_response(response, "DN Dashboard", req_id)
            
            if not is_valid:
                logger.error(f"[{req_id}] ❌ Validation failed: {error_msg}")
                return self._create_error_response(
                    error_msg,
                    "DN_NOT_FOUND",
                    dn_clean,
                    "dn"
                )
            
            # ==========================================================
            # STEP 4: Format and Return
            # ==========================================================
            logger.info(f"[{req_id}] ✅ Valid data received, formatting...")
            
            result = self._format_dn_dashboard(data, dn_clean)
            
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ DN dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ DN dashboard error: {e}")
            return self._create_error_response(
                f"Error retrieving DN: {str(e)[:100]}",
                "DN_ERROR",
                dn_clean,
                "dn"
            )

    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle dealer dashboard with full service integration."""
        import time
        start_time = time.time()
        
        dealer_name = entity or (context.last_dealer if context else None)
        
        if not dealer_name:
            return "🏪 *DEALER DASHBOARD*\n\nPlease specify a dealer name."
        
        original_name = dealer_name
        
        if self.analytics is None:
            return self._create_error_response(
                "Analytics service is not available",
                "SERVICE_UNAVAILABLE",
                dealer_name,
                "dealer"
            )
        
        logger.info(f"[{req_id}] 🔍 Searching for dealer: '{dealer_name}'")
        
        try:
            # ==========================================================
            # STEP 1: Get Dealer 360 Dashboard
            # ==========================================================
            data = None
            
            if self.dealer_analytics:
                try:
                    logger.info(f"[{req_id}] 📊 Getting 360 dashboard for: {dealer_name}")
                    response = self.dealer_analytics.get_dashboard(dealer_name)
                    
                    if isinstance(response, dict) and not response.get('error'):
                        data = response
                        logger.info(f"[{req_id}] ✅ 360 dashboard retrieved")
                    elif hasattr(response, 'success') and response.success:
                        data = response.data if hasattr(response, 'data') else {}
                        logger.info(f"[{req_id}] ✅ 360 dashboard retrieved from response")
                except Exception as e:
                    logger.warning(f"[{req_id}] ⚠️ 360 dashboard failed: {e}")
            
            # ==========================================================
            # STEP 2: Add Distance Information
            # ==========================================================
            if data and self.distance_service:
                try:
                    profile = data.get('profile', {})
                    warehouse = profile.get('warehouse')
                    city = profile.get('city')
                    if warehouse and city and warehouse != 'Not Set' and city != 'Not Set':
                        distance_info = self.distance_service.calculate_warehouse_distance(warehouse, city)
                        if distance_info and distance_info.get('success'):
                            data['distance_km'] = distance_info.get('distance_km')
                            data['approx_driving_minutes'] = distance_info.get('approx_driving_minutes')
                            data['approx_driving_hours'] = distance_info.get('approx_driving_hours')
                            data['distance_type'] = distance_info.get('distance_type', 'unknown')
                            logger.info(f"[{req_id}] ✅ Distance: {distance_info.get('distance_km')} km")
                except Exception as e:
                    logger.warning(f"[{req_id}] ⚠️ Distance calculation failed: {e}")
            
            # ==========================================================
            # STEP 3: Fallback to Legacy Analytics
            # ==========================================================
            if not data:
                try:
                    logger.info(f"[{req_id}] 📊 Using legacy analytics for: {dealer_name}")
                    response = self.analytics.get_dealer_dashboard(dealer_name)
                    if hasattr(response, 'success') and response.success:
                        data = response.data if hasattr(response, 'data') else {}
                    elif isinstance(response, dict) and not response.get('error'):
                        data = response
                except Exception as e:
                    logger.warning(f"[{req_id}] ⚠️ Legacy dashboard failed: {e}")
            
            # ==========================================================
            # STEP 4: Validate and Format
            # ==========================================================
            if not data or (isinstance(data, dict) and data.get('error')):
                error = data.get('error', 'No data') if isinstance(data, dict) else 'No data'
                return self._create_error_response(
                    f"Dealer '{original_name}' not found: {error}",
                    "DEALER_NOT_FOUND",
                    original_name,
                    "dealer"
                )
            
            # Use 360 formatter if available
            if data.get('_dashboard_type') == '360' or 'profile' in data:
                try:
                    _, format_func = _get_dealer_analytics_service()
                    if format_func:
                        result = format_func(data)
                        elapsed = time.time() - start_time
                        logger.info(f"[{req_id}] ✅ 360 dashboard returned in {elapsed:.3f}s")
                        return result
                except Exception as e:
                    logger.warning(f"[{req_id}] ⚠️ 360 formatter failed: {e}")
            
            result = self._format_dealer_dashboard(data, dealer_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Dealer dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Dealer dashboard error: {e}")
            return self._create_error_response(
                f"Error retrieving dealer data: {str(e)[:100]}",
                "DEALER_ERROR",
                original_name,
                "dealer"
            )

    def _route_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle warehouse dashboard with coverage integration."""
        import time
        start_time = time.time()
        
        warehouse_name = entity or (context.last_warehouse if context else None)
        
        if not warehouse_name:
            return "🏭 *WAREHOUSE DASHBOARD*\n\nPlease specify a warehouse name."
        
        if self.analytics is None:
            return self._create_error_response(
                "Analytics service is not available",
                "SERVICE_UNAVAILABLE",
                warehouse_name,
                "warehouse"
            )
        
        try:
            if not hasattr(self.analytics, 'get_warehouse_dashboard'):
                return self._create_error_response(
                    "Warehouse dashboard method not available",
                    "METHOD_UNAVAILABLE",
                    warehouse_name,
                    "warehouse"
                )
            
            response = self.analytics.get_warehouse_dashboard(warehouse_name)
            is_valid, error_msg, data = self._validate_response(response, "Warehouse Dashboard", req_id)
            
            if not is_valid:
                return self._create_error_response(
                    error_msg,
                    "WAREHOUSE_NOT_FOUND",
                    warehouse_name,
                    "warehouse"
                )
            
            # Add distance coverage information
            if data and self.distance_service:
                try:
                    coverage = self.distance_service.get_warehouse_coverage(warehouse_name)
                    if coverage and coverage.get('success'):
                        data['avg_distance_km'] = coverage.get('average_distance_km')
                        data['max_distance_km'] = coverage.get('max_distance_km')
                        data['min_distance_km'] = coverage.get('min_distance_km')
                        data['distance_info'] = coverage.get('cities', [])
                        logger.info(f"[{req_id}] ✅ Coverage info added")
                except Exception as e:
                    logger.warning(f"[{req_id}] ⚠️ Coverage info failed: {e}")
            
            result = self._format_warehouse_dashboard(data, warehouse_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Warehouse dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Warehouse dashboard error: {e}")
            return self._create_error_response(
                f"Error retrieving warehouse data: {str(e)[:100]}",
                "WAREHOUSE_ERROR",
                warehouse_name,
                "warehouse"
            )

    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle city dashboard."""
        import time
        start_time = time.time()
        
        city_name = entity or (context.last_city if context else None)
        
        if not city_name:
            return "🏙️ *CITY DASHBOARD*\n\nPlease specify a city name."
        
        if self.analytics is None:
            return self._create_error_response(
                "Analytics service is not available",
                "SERVICE_UNAVAILABLE",
                city_name,
                "city"
            )
        
        try:
            if not hasattr(self.analytics, 'get_city_dashboard'):
                return self._create_error_response(
                    "City dashboard method not available",
                    "METHOD_UNAVAILABLE",
                    city_name,
                    "city"
                )
            
            response = self.analytics.get_city_dashboard(city_name)
            is_valid, error_msg, data = self._validate_response(response, "City Dashboard", req_id)
            
            if not is_valid:
                return self._create_error_response(
                    error_msg,
                    "CITY_NOT_FOUND",
                    city_name,
                    "city"
                )
            
            result = self._format_city_dashboard(data, city_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ City dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ City dashboard error: {e}")
            return self._create_error_response(
                f"Error retrieving city data: {str(e)[:100]}",
                "CITY_ERROR",
                city_name,
                "city"
            )

    def _route_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle product dashboard."""
        import time
        start_time = time.time()
        
        product_name = entity or (context.last_product if context else None)
        
        if not product_name:
            return "📦 *PRODUCT DASHBOARD*\n\nPlease specify a product."
        
        if self.analytics is None:
            return self._create_error_response(
                "Analytics service is not available",
                "SERVICE_UNAVAILABLE",
                product_name,
                "product"
            )
        
        try:
            if not hasattr(self.analytics, 'get_product_dashboard'):
                return self._create_error_response(
                    "Product dashboard method not available",
                    "METHOD_UNAVAILABLE",
                    product_name,
                    "product"
                )
            
            response = self.analytics.get_product_dashboard(product_name)
            is_valid, error_msg, data = self._validate_response(response, "Product Dashboard", req_id)
            
            if not is_valid:
                return self._create_error_response(
                    error_msg,
                    "PRODUCT_NOT_FOUND",
                    product_name,
                    "product"
                )
            
            result = self._format_product_dashboard(data, product_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Product dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Product dashboard error: {e}")
            return self._create_error_response(
                f"Error retrieving product data: {str(e)[:100]}",
                "PRODUCT_ERROR",
                product_name,
                "product"
            )

    def _route_distance_query(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle distance query."""
        import time
        start_time = time.time()
        
        if not entity:
            return "📍 *DISTANCE QUERY*\n\nPlease specify origin and destination.\n\n*Example:* Distance Lahore to Karachi"
        
        try:
            # Parse origin and destination
            parts = entity.split(' to ')
            if len(parts) != 2:
                parts = entity.split(' from ')
            if len(parts) != 2:
                parts = entity.split(' between ')
            
            if len(parts) != 2:
                return "📍 *DISTANCE QUERY*\n\nPlease specify origin and destination.\n\n*Example:* Distance Lahore to Karachi"
            
            origin = parts[0].strip()
            destination = parts[1].strip()
            
            if not self.distance_service:
                return "📍 *DISTANCE QUERY*\n\nDistance service is not available. Please try again later."
            
            logger.info(f"[{req_id}] 📍 Calculating distance: {origin} → {destination}")
            
            result = self.distance_service.calculate_distance(origin, destination)
            
            if not result.get('success'):
                return f"📍 *DISTANCE QUERY*\n\nUnable to calculate distance between '{origin}' and '{destination}'.\n\n{result.get('error', 'Unknown error')}"
            
            distance_km = result.get('distance_km', 0)
            distance_miles = result.get('distance_miles', 0)
            driving_hours = result.get('approx_driving_hours', 0)
            driving_minutes = result.get('approx_driving_minutes', 0)
            distance_type = result.get('distance_type', 'unknown')
            
            lines = [
                "📍 *DISTANCE CALCULATION*",
                "",
                f"🛫 From: {origin}",
                f"🛬 To: {destination}",
                "",
                f"📏 Distance: {distance_km:.1f} km ({distance_miles:.1f} miles)",
                "",
            ]
            
            if distance_type == "road":
                lines.append("🚗 *Road Distance* (accurate)")
            else:
                lines.append("✈️ *Air Distance* (approximate)")
            
            if driving_hours:
                if driving_hours < 1:
                    lines.append(f"⏱️ Approx Driving: {driving_minutes} minutes")
                else:
                    hours = int(driving_hours)
                    minutes = int((driving_hours - hours) * 60)
                    if minutes > 0:
                        lines.append(f"⏱️ Approx Driving: {hours}h {minutes}m")
                    else:
                        lines.append(f"⏱️ Approx Driving: {hours}h")
            
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Distance query returned in {elapsed:.3f}s")
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Distance query error: {e}")
            return self._create_error_response(
                f"Error calculating distance: {str(e)[:100]}",
                "DISTANCE_ERROR",
                entity,
                "distance"
            )


# ==========================================================
# BLOCK 11: FORMATTERS WITH DISTANCE SUPPORT
# ==========================================================

    def _format_dn_dashboard(self, data: Dict, dn_number: str) -> str:
        """Format DN dashboard - production grade."""
        try:
            if not data:
                return self._create_error_response(
                    f"No data available for DN {dn_number}",
                    "DN_NOT_FOUND",
                    dn_number,
                    "dn"
                )
            
            def safe_get(key, default=""):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            # Get all fields with proper defaults
            customer_name = safe_get('customer_name', 'Unknown Dealer')
            dealer_code = safe_get('dealer_code', '')
            customer_code = safe_get('customer_code', '')
            warehouse = safe_get('warehouse', 'Unknown Warehouse')
            city = safe_get('ship_to_city', 'Unknown City')
            sales_office = safe_get('sales_office', '')
            sales_manager = safe_get('sales_manager', '')
            division = safe_get('division', '')
            customer_model = safe_get('customer_model', '')
            material_no = safe_get('material_no', '')
            
            units = safe_get('units', 0)
            amount = safe_get('amount', 0)
            status = safe_get('delivery_status', 'Unknown')
            pgi_status = safe_get('pgi_status', '')
            pod_status = safe_get('pod_status', '')
            
            create_date = safe_get('dn_create_date', '')
            pgi_date = safe_get('good_issue_date', '')
            pod_date = safe_get('pod_date', '')
            
            delivery_aging = safe_get('delivery_aging_text', '')
            pod_aging = safe_get('pod_aging_text', '')
            total_cycle = safe_get('total_cycle_text', '')
            
            pending_flag = data.get('pending_flag', False)
            pending_text = "🔴 Yes" if pending_flag else "🟢 No"
            
            status_emoji = "✅" if status in ['Completed', 'Delivered', 'Closed'] else "⏳"
            
            lines = [
                "📄 *DN TRACKING*",
                "",
                f"DN No: {safe_get('dn_number', dn_number)}",
                f"Dealer: {customer_name}",
            ]
            
            if dealer_code:
                lines.append(f"Dealer Code: {dealer_code}")
            if customer_code:
                lines.append(f"Customer Code: {customer_code}")
            
            lines.append(f"Warehouse: {warehouse}")
            lines.append(f"City: {city}")
            
            if sales_office:
                lines.append(f"Sales Office: {sales_office}")
            if sales_manager:
                lines.append(f"Sales Manager: {sales_manager}")
            if division:
                lines.append(f"Division: {division}")
            
            lines.extend([
                "",
                "📦 *Products*",
                f"Model: {customer_model if customer_model else 'N/A'}",
                f"Material: {material_no if material_no else 'N/A'}",
                "",
                "📊 *Metrics*",
                f"Units: {units}",
            ])
            
            if amount and amount != 0:
                lines.append(f"Revenue: PKR {amount:,.0f}")
            else:
                lines.append("Revenue: PKR 0")
            
            lines.extend([
                "",
                "📅 *Dates*",
                f"Create: {create_date if create_date else 'N/A'}",
                f"PGI: {pgi_date if pgi_date else 'N/A'}",
                f"POD: {pod_date if pod_date else 'N/A'}",
                "",
                "⏳ *Aging*",
                f"Delivery Aging: {delivery_aging if delivery_aging else 'N/A'}",
                f"POD Aging: {pod_aging if pod_aging else 'N/A'}",
                f"Total Cycle: {total_cycle if total_cycle else 'N/A'}",
                "",
                "📋 *Status*",
                f"Delivery: {status} {status_emoji}",
                f"PGI: {pgi_status if pgi_status else 'N/A'}",
                f"POD: {pod_status if pod_status else 'N/A'}",
                f"Pending: {pending_text}"
            ])
            
            issues = data.get('issues', [])
            if issues and isinstance(issues, list):
                lines.append("")
                lines.append("⚠️ *Data Issues*")
                for issue in issues[:3]:
                    lines.append(f"   {issue}")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"DN format error for {dn_number}: {e}")
            return self._create_error_response(
                f"Error formatting DN details: {str(e)[:100]}",
                "FORMAT_ERROR",
                dn_number,
                "dn"
            )

    def _format_dealer_dashboard(self, data: Dict, dealer_name: str) -> str:
        """Format dealer dashboard with distance information."""
        try:
            if not data:
                return self._create_error_response(
                    f"No data available for dealer {dealer_name}",
                    "DEALER_NOT_FOUND",
                    dealer_name,
                    "dealer"
                )
            
            def safe_get(key, default=""):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            revenue = data.get('total_revenue', 0)
            delivery_rate = safe_get('delivery_rate', 0)
            total_dns = safe_get('total_dns', 0)
            total_units = safe_get('total_units', 0)
            delivered = safe_get('delivered_dns', 0)
            pending = safe_get('pending_dns', 0)
            transit = safe_get('transit_dns', 0)
            
            lines = [
                "🏢 *DEALER DASHBOARD*",
                "",
                f"Dealer: {safe_get('dealer_name', dealer_name)}",
                f"Dealer Code: {safe_get('dealer_code', 'Not Set')}",
                f"Customer Code: {safe_get('customer_code', 'Not Set')}",
                f"Division: {safe_get('division', 'Not Set')}",
                f"Warehouse: {safe_get('warehouse', 'Not Set')}",
                f"City: {safe_get('city', 'Not Set')}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {total_dns}",
                f"Total Units: {total_units}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                "",
                "📦 *Delivery Status*",
                f"Delivered: {delivered}",
                f"In Transit: {transit}",
                f"Pending: {pending}",
                f"Delivery Rate: {delivery_rate}%",
            ]
            
            # Add distance information if available
            distance_km = data.get('distance_km')
            if distance_km:
                lines.append("")
                lines.append("📍 *Distance*")
                lines.append(f"Warehouse → Dealer: {distance_km:.1f} km")
                
                approx_minutes = data.get('approx_driving_minutes')
                if approx_minutes:
                    if approx_minutes < 60:
                        lines.append(f"⏱️ Approx Driving: {approx_minutes} minutes")
                    else:
                        hours = int(approx_minutes // 60)
                        minutes = int(approx_minutes % 60)
                        lines.append(f"⏱️ Approx Driving: {hours}h {minutes}m")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Dealer format error: {e}")
            return self._create_error_response(
                f"Error formatting dealer data: {str(e)[:100]}",
                "FORMAT_ERROR",
                dealer_name,
                "dealer"
            )

    def _format_warehouse_dashboard(self, data: Dict, warehouse_name: str) -> str:
        """Format warehouse dashboard with coverage information."""
        try:
            if not data:
                return self._create_error_response(
                    f"No data available for warehouse {warehouse_name}",
                    "WAREHOUSE_NOT_FOUND",
                    warehouse_name,
                    "warehouse"
                )
            
            def safe_get(key, default=""):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            revenue = data.get('total_revenue', 0)
            
            lines = [
                "🏭 *WAREHOUSE DASHBOARD*",
                "",
                f"Warehouse: {safe_get('warehouse', warehouse_name)}",
                f"Warehouse Code: {safe_get('warehouse_code', '')}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {safe_get('total_dns', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                f"Total Dealers: {safe_get('total_dealers', 0)}",
                f"Cities Served: {safe_get('cities_served', 0)}",
                "",
                "📦 *Delivery Status*",
                f"Delivered: {safe_get('delivered_dns', 0)} ({safe_get('delivery_rate', 0)}%)",
                f"Pending: {safe_get('pending_dns', 0)}",
                f"Pending POD: {safe_get('pending_pod_dns', 0)}",
            ]
            
            # Add distance coverage
            avg_distance = data.get('avg_distance_km')
            if avg_distance:
                lines.append("")
                lines.append("📍 *Distance Coverage*")
                lines.append(f"Average Distance: {avg_distance:.1f} km")
                
                max_distance = data.get('max_distance_km')
                if max_distance:
                    lines.append(f"Farthest City: {max_distance:.1f} km")
                
                min_distance = data.get('min_distance_km')
                if min_distance:
                    lines.append(f"Closest City: {min_distance:.1f} km")
                
                distance_info = data.get('distance_info', [])
                if distance_info:
                    lines.append("")
                    lines.append("📌 *Top Cities by Distance*")
                    for item in distance_info[:5]:
                        city = item.get('city', 'Unknown')
                        dist = item.get('distance_km', 0)
                        lines.append(f"• {city}: {dist:.1f} km")
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Warehouse format error: {e}")
            return self._create_error_response(
                f"Error formatting warehouse data: {str(e)[:100]}",
                "FORMAT_ERROR",
                warehouse_name,
                "warehouse"
            )

    def _format_city_dashboard(self, data: Dict, city_name: str) -> str:
        """Format city dashboard."""
        try:
            if not data:
                return self._create_error_response(
                    f"No data available for city {city_name}",
                    "CITY_NOT_FOUND",
                    city_name,
                    "city"
                )
            
            def safe_get(key, default=""):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            revenue = data.get('total_revenue', 0)
            
            lines = [
                "🏙️ *CITY DASHBOARD*",
                "",
                f"City: {safe_get('city_name', city_name)}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {safe_get('total_dns', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                f"Total Dealers: {safe_get('total_dealers', 0)}",
                f"Total Warehouses: {safe_get('total_warehouses', 0)}",
                "",
                f"📦 Delivery Rate: {safe_get('delivery_rate', 0)}%"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"City format error: {e}")
            return self._create_error_response(
                f"Error formatting city data: {str(e)[:100]}",
                "FORMAT_ERROR",
                city_name,
                "city"
            )

    def _format_product_dashboard(self, data: Dict, product_name: str) -> str:
        """Format product dashboard."""
        try:
            if not data:
                return self._create_error_response(
                    f"No data available for product {product_name}",
                    "PRODUCT_NOT_FOUND",
                    product_name,
                    "product"
                )
            
            def safe_get(key, default=""):
                val = data.get(key, default)
                return default if val is None or val == "" else val
            
            revenue = data.get('revenue', 0)
            
            lines = [
                "📦 *PRODUCT DASHBOARD*",
                "",
                f"Product: {safe_get('product', product_name)}",
                "",
                "📊 *Metrics*",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                f"Total Units: {safe_get('units', 0)}",
                f"Total DNs: {safe_get('dns', 0)}",
                "",
                "📍 *Distribution*",
                f"Dealers: {safe_get('dealers', 0)}",
                f"Cities: {safe_get('cities', 0)}",
                f"Warehouses: {safe_get('warehouses', 0)}",
                "",
                f"📦 Delivery Rate: {safe_get('delivery_rate', 0)}%"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Product format error: {e}")
            return self._create_error_response(
                f"Error formatting product data: {str(e)[:100]}",
                "FORMAT_ERROR",
                product_name,
                "product"
            )

    def _get_help_message(self) -> str:
        """Get help message."""
        return """🏠 *HAIER LOGISTICS AI*

📋 *Available Dashboards:*

1️⃣ 🏪 Dealer Dashboard
2️⃣ 🏭 Warehouse Dashboard
3️⃣ 🏙️ City Dashboard
4️⃣ 📦 Product Dashboard
5️⃣ 📄 DN Dashboard
6️⃣ 📋 PGI Dashboard
7️⃣ ✅ POD Dashboard
8️⃣ 🚚 Delivery Dashboard
9️⃣ 👔 Executive Dashboard
🔟 🚨 Control Tower
1️⃣1️⃣ 📍 Distance Calculator

🔍 *Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "Pakistan Electronics")
• Product name (e.g., "Refrigerator")
• City name (e.g., "Lahore City")
• Warehouse name (e.g., "Rawalpindi warehouse")
• Distance Lahore to Karachi
• "Help" for menu

*Ask me anything about logistics!* 🤖"""


# ==========================================================
# BLOCK 12: SINGLETON & WRAPPER FUNCTIONS
# ==========================================================

_orchestrator = None
_initialization_attempts = 0
_MAX_INIT_ATTEMPTS = 3

def get_orchestrator(session_factory: Optional[Callable[[], Session]] = None) -> Optional[AIOrchestrator]:
    """Get or create AI Orchestrator singleton."""
    global _orchestrator, _initialization_attempts
    
    if _orchestrator is not None:
        return _orchestrator
    
    if _initialization_attempts >= _MAX_INIT_ATTEMPTS:
        logger.error(f"❌ Max initialization attempts ({_MAX_INIT_ATTEMPTS}) reached")
        return None
    
    _initialization_attempts += 1
    logger.info(f"🔄 Initializing AI Orchestrator (attempt {_initialization_attempts}/{_MAX_INIT_ATTEMPTS})...")
    
    try:
        _orchestrator = AIOrchestrator(session_factory=session_factory)
        logger.info("✅ AI Orchestrator v31.0 initialized successfully")
        _initialization_attempts = 0
        return _orchestrator
    except Exception as e:
        logger.error(f"❌ Failed to initialize AI Orchestrator: {e}")
        import traceback
        logger.error(traceback.format_exc())
        _orchestrator = None
        return None


def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    """
    Process WhatsApp query with all services integrated.
    Enterprise Production Grade.
    """
    global _orchestrator
    
    if not question or not question.strip():
        return "Please provide a valid question. Type 'help' for menu."
    
    orchestrator = get_orchestrator(session_factory)
    
    if orchestrator is None:
        return "⚠️ AI service is currently unavailable. Please try again later."
    
    try:
        return orchestrator.process_whatsapp_query(
            question=question,
            session_factory=session_factory,
            phone_number=phone_number,
            user_id=user_id,
            request_id=request_id
        )
    except Exception as e:
        logger.error(f"❌ Error processing query: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"⚠️ Error processing your request. Please try again later."


def reset_orchestrator() -> None:
    """Reset the orchestrator singleton."""
    global _orchestrator, _initialization_attempts
    _orchestrator = None
    _initialization_attempts = 0
    logger.info("🔄 Orchestrator reset successfully")


def get_orchestrator_status() -> Dict[str, Any]:
    """Get current orchestrator status for diagnostics."""
    global _orchestrator, _initialization_attempts
    
    if _orchestrator is None:
        return {
            "orchestrator_initialized": False,
            "initialization_attempts": _initialization_attempts,
            "max_attempts": _MAX_INIT_ATTEMPTS,
            "analytics_available": False,
            "distance_available": False,
            "dealer_analytics_available": False,
            "conversation_count": 0,
            "metrics": {}
        }
    
    return {
        "orchestrator_initialized": True,
        "initialization_attempts": _initialization_attempts,
        "max_attempts": _MAX_INIT_ATTEMPTS,
        "analytics_available": hasattr(_orchestrator, 'analytics') and _orchestrator.analytics is not None,
        "distance_available": hasattr(_orchestrator, 'distance_service') and _orchestrator.distance_service is not None,
        "dealer_analytics_available": hasattr(_orchestrator, 'dealer_analytics') and _orchestrator.dealer_analytics is not None,
        "conversation_count": len(_orchestrator.conversation_cache) if _orchestrator else 0,
        "resolver_stats": _orchestrator.resolver.get_stats() if _orchestrator and hasattr(_orchestrator, 'resolver') else {},
        "classifier_stats": _orchestrator.classifier.get_stats() if _orchestrator and hasattr(_orchestrator, 'classifier') else {},
        "metrics": _orchestrator.metrics if _orchestrator else {}
    }


# ==========================================================
# BLOCK 13: EXPORTS
# ==========================================================

__all__ = [
    'AIOrchestrator',
    'PostgreSQLResolver',
    'ConversationContext',
    'IntentClassifier',
    'IntentResult',
    'get_orchestrator',
    'process_whatsapp_query',
    'reset_orchestrator',
    'get_orchestrator_status',
]

# ==========================================================
# END OF FILE - v31.0 ENTERPRISE PRODUCTION
# ==========================================================
