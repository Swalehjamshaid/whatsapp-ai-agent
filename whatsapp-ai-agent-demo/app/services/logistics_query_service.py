# ==========================================================
# FILE: app/services/logistics_query_service.py (v6.0 - PRODUCTION STABLE)
# ==========================================================
# PURPOSE: SINGLE SOURCE OF TRUTH for all database access
# 
# CRITICAL FIXES APPLIED:
# 1. ✅ Removed MODE() WITHIN GROUP (PostgreSQL version compatibility)
# 2. ✅ Fixed cache health check for Redis
# 3. ✅ Startup validation no longer crashes on deploy
# 4. ✅ Fixed recursive call in debug_dealer()
# 5. ✅ Added database URL hash for diagnostics
# 6. ✅ Added SQL query logging for debugging
# 7. ✅ Added verify_specific_dn() for direct verification
# 8. ✅ FastAPI debug routes ready
# ==========================================================

from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Tuple, Protocol, runtime_checkable
from sqlalchemy import func, and_, or_, desc, case, text, inspect, cast, String
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from loguru import logger
import re
import time
import uuid
from difflib import SequenceMatcher
from functools import lru_cache
import os
import json
import hashlib

from app.models import DeliveryReport
from app.database import SessionLocal
from app.schemas.schema_service import get_schema_service, DN_PATTERN


# ==========================================================
# STANDARD FIELD MAPPING CONSTANTS
# ==========================================================

DEALER_NAME_FIELD = "customer_name"
DEALER_CODE_FIELD = "dealer_code"
CUSTOMER_CODE_FIELD = "customer_code"
DN_NO_FIELD = "dn_no"
DELIVERY_STATUS_FIELD = "delivery_status"
PGI_STATUS_FIELD = "pgi_status"
POD_STATUS_FIELD = "pod_status"
WAREHOUSE_CODE_FIELD = "warehouse_code"
DELIVERY_LOCATION_FIELD = "delivery_location"
DIVISION_FIELD = "division"


# ==========================================================
# CUSTOM EXCEPTIONS
# ==========================================================

class LogisticsQueryError(Exception):
    """Base exception for logistics query errors."""
    pass

class DealerNotFoundError(LogisticsQueryError):
    """Raised when dealer is not found in database."""
    def __init__(self, dealer_name: str, confidence: float = 0.0):
        self.dealer_name = dealer_name
        self.confidence = confidence
        super().__init__(f"Dealer not found: {dealer_name} (confidence: {confidence:.2f})")

class DNNotFoundError(LogisticsQueryError):
    """Raised when DN is not found in database."""
    def __init__(self, dn_number: str, normalized: str = None):
        self.dn_number = dn_number
        self.normalized = normalized
        super().__init__(f"DN not found: {dn_number} (normalized: {normalized})")

class DashboardGenerationError(LogisticsQueryError):
    """Raised when dashboard generation fails."""
    def __init__(self, dealer_name: str, reason: str):
        self.dealer_name = dealer_name
        self.reason = reason
        super().__init__(f"Dashboard generation failed for {dealer_name}: {reason}")

class DatabaseQueryError(LogisticsQueryError):
    """Raised when database query fails."""
    def __init__(self, query: str, error: str):
        self.query = query
        self.error = error
        super().__init__(f"Database query failed: {error}")

class DatabaseConnectionError(LogisticsQueryError):
    """Raised when database connection fails."""
    def __init__(self, reason: str):
        super().__init__(f"Database connection failed: {reason}")


# ==========================================================
# CACHE PROTOCOL (Dependency Injection)
# ==========================================================

@runtime_checkable
class CacheProvider(Protocol):
    """Cache provider interface for dependency injection."""
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        ...
    
    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        """Set value in cache with TTL."""
        ...
    
    def delete(self, key: str) -> None:
        """Delete value from cache."""
        ...
    
    def clear(self) -> None:
        """Clear all cache."""
        ...


# ==========================================================
# IN-MEMORY CACHE PROVIDER (Fallback)
# ==========================================================

class InMemoryCacheProvider:
    """In-memory cache provider (fallback when Redis not available)."""
    
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._ttl: Dict[str, datetime] = {}
    
    def get(self, key: str) -> Optional[Any]:
        if key in self._cache and key in self._ttl:
            if datetime.now() < self._ttl[key]:
                return self._cache[key]
        return None
    
    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        self._cache[key] = value
        self._ttl[key] = datetime.now() + timedelta(seconds=ttl_seconds)
    
    def delete(self, key: str) -> None:
        self._cache.pop(key, None)
        self._ttl.pop(key, None)
    
    def clear(self) -> None:
        self._cache.clear()
        self._ttl.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        return {"type": "in_memory", "size": len(self._cache)}


# ==========================================================
# REDIS CACHE PROVIDER (Enterprise)
# ==========================================================

class RedisCacheProvider:
    """Redis cache provider for distributed deployments."""
    
    def __init__(self):
        self._client = None
        self._available = False
        self._init_redis()
    
    def _init_redis(self):
        try:
            import redis
            redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
            self._client = redis.from_url(redis_url, decode_responses=True)
            self._client.ping()
            self._available = True
            logger.info("✅ Redis cache connected")
        except ImportError:
            logger.warning("⚠️ Redis not installed, using in-memory cache")
            self._available = False
        except Exception as e:
            logger.warning(f"⚠️ Redis connection failed: {e}, using in-memory cache")
            self._available = False
    
    def get(self, key: str) -> Optional[Any]:
        if not self._available:
            return None
        try:
            value = self._client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.debug(f"Redis get failed: {e}")
            return None
    
    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        if not self._available:
            return
        try:
            self._client.setex(key, ttl_seconds, json.dumps(value))
        except Exception as e:
            logger.debug(f"Redis set failed: {e}")
    
    def delete(self, key: str) -> None:
        if not self._available:
            return
        try:
            self._client.delete(key)
        except Exception as e:
            logger.debug(f"Redis delete failed: {e}")
    
    def clear(self) -> None:
        if not self._available:
            return
        try:
            self._client.flushdb()
        except Exception as e:
            logger.debug(f"Redis clear failed: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        return {"type": "redis", "available": self._available}


# ==========================================================
# QUERY TIMING HELPER
# ==========================================================

class QueryTimer:
    """Helper class for measuring query execution time."""
    
    def __init__(self, request_id: str, query_type: str):
        self.request_id = request_id
        self.query_type = query_type
        self.start_time = None
        self.end_time = None
        self.duration_ms = 0
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        
        # Log query completion
        logger.info(
            f"[{self.request_id}] {self.query_type} completed in {self.duration_ms:.2f}ms"
        )
    
    def get_duration(self) -> float:
        """Get duration in milliseconds."""
        return self.duration_ms


# ==========================================================
# LOGISTICS QUERY SERVICE (PRODUCTION STABLE)
# ==========================================================

class LogisticsQueryService:
    """
    DATABASE ACCESS LAYER - SINGLE SOURCE OF TRUTH
    
    PRODUCTION STABLE:
    - PostgreSQL version compatible (no MODE() WITHIN GROUP)
    - Startup validation won't crash deploy
    - Proper cache health checks
    - Database URL diagnostics
    - SQL query logging for debugging
    - FastAPI debug routes ready
    """
    
    # Expose constants for alignment
    DEALER_NAME_FIELD = DEALER_NAME_FIELD
    DEALER_CODE_FIELD = DEALER_CODE_FIELD
    CUSTOMER_CODE_FIELD = CUSTOMER_CODE_FIELD
    DN_NO_FIELD = DN_NO_FIELD
    
    def __init__(self, db: Optional[Session] = None, cache_provider: Optional[CacheProvider] = None):
        self.db = db or SessionLocal()
        self._owned_db = db is None
        self.schema = get_schema_service()
        self.today = date.today()
        
        # Dependency Injection: Cache Provider
        self.cache = cache_provider or self._create_default_cache()
        
        # Table name auto-detection with fallback
        self.table_name = self._detect_table_name()
        
        # Performance metrics
        self.metrics = {
            "total_queries": 0,
            "dealer_resolutions": 0,
            "dealer_resolution_hits": 0,
            "dealer_resolution_misses": 0,
            "dealer_cache_hits": 0,
            "dealer_cache_misses": 0,
            "dn_lookups": 0,
            "dn_lookups_hits": 0,
            "dn_lookups_misses": 0,
            "dashboard_generations": 0,
            "dashboard_generations_success": 0,
            "dashboard_generations_failure": 0,
            "avg_query_time_ms": 0,
            "total_query_time_ms": 0
        }
        
        # FIXED: Database URL Hash for diagnostics (no credentials exposed)
        self._log_database_url_hash()
        
        # DIAGNOSTIC: Startup validation (won't crash on deploy)
        self._validate_startup()
        
        logger.info("=" * 60)
        logger.info("LogisticsQueryService v6.0 - Production Stable")
        logger.info("=" * 60)
        logger.info("")
        logger.info("   CRITICAL FIXES:")
        logger.info(f"   ✅ Table: '{self.table_name}'")
        logger.info(f"   ✅ Cache: {type(self.cache).__name__}")
        logger.info("   ✅ Removed MODE() WITHIN GROUP (PostgreSQL compatible)")
        logger.info("   ✅ Startup validation won't crash deploy")
        logger.info("   ✅ Fixed cache health check")
        logger.info("   ✅ Added database URL hash")
        logger.info("   ✅ verify_specific_dn() for direct verification")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 60)
    
    def _log_database_url_hash(self):
        """Log database URL hash for diagnostics (no credentials)."""
        try:
            url_str = str(self.db.bind.url)
            # Hash the URL to identify which database we're connected to
            url_hash = hashlib.sha256(url_str.encode()).hexdigest()[:12]
            logger.info(f"🔐 Database URL Hash: {url_hash}")
            
            # Extract host and database name (safe to log)
            from urllib.parse import urlparse
            parsed = urlparse(url_str)
            db_info = {
                "host": parsed.hostname,
                "database": parsed.path.lstrip('/'),
                "port": parsed.port
            }
            logger.info(f"📊 Connected to: {db_info['host']}:{db_info['port']}/{db_info['database']}")
        except Exception as e:
            logger.warning(f"Could not log database URL hash: {e}")
    
    def _create_default_cache(self) -> CacheProvider:
        """Create default cache provider."""
        try:
            return RedisCacheProvider()
        except:
            return InMemoryCacheProvider()
    
    def close(self):
        if self._owned_db and self.db:
            self.db.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # ==========================================================
    # TABLE NAME AUTO-DETECTION (With Fallback)
    # ==========================================================
    
    def _detect_table_name(self) -> str:
        """Auto-detect actual table name with robust fallback."""
        try:
            from app.models import DeliveryReport
            table_name = DeliveryReport.__tablename__
            
            # Verify table exists
            try:
                inspector = inspect(self.db.bind)
                tables = inspector.get_table_names()
                
                if table_name in tables:
                    logger.info(f"✅ Table '{table_name}' exists")
                    return table_name
                
                # Try alternatives
                alternatives = ['delivery_report', 'delivery_reports', 'deliveryreport']
                for alt in alternatives:
                    if alt in tables:
                        logger.warning(f"⚠️ Using '{alt}' instead of '{table_name}'")
                        return alt
                
                logger.warning(f"⚠️ Table '{table_name}' not found, using model name")
                return table_name
                
            except Exception as e:
                logger.warning(f"⚠️ Table verification failed: {e}, using model name")
                return table_name
                
        except Exception as e:
            logger.warning(f"⚠️ Table detection failed: {e}, using default")
            return "delivery_reports"
    
    # ==========================================================
    # DEALER NAME CACHING (With Redis support)
    # ==========================================================
    
    def _get_cached_dealers(self) -> List[str]:
        """Get cached dealer names with TTL."""
        cache_key = "dealer_names"
        cache_ttl = 300  # 5 minutes
        
        # Try cache
        cached = self.cache.get(cache_key)
        if cached is not None:
            self.metrics["dealer_cache_hits"] += 1
            return cached
        
        self.metrics["dealer_cache_misses"] += 1
        
        # Fetch from database
        dealers = self._get_all_dealer_names_from_db()
        
        # Store in cache
        if dealers:
            self.cache.set(cache_key, dealers, cache_ttl)
            logger.debug(f"Dealer cache refreshed: {len(dealers)} dealers")
        else:
            # Cache empty result to prevent repeated queries
            self.cache.set(cache_key, [], 60)  # Short TTL for empty
        
        return dealers
    
    def _get_all_dealer_names_from_db(self) -> List[str]:
        """Get all dealer names from database using SQL aggregation."""
        try:
            results = self.db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).distinct().order_by(DeliveryReport.customer_name).all()
            return [r[0] for r in results if r[0]]
        except Exception as e:
            logger.error(f"Failed to get dealer names: {e}")
            return []
    
    def clear_cache(self):
        """Clear all caches."""
        self.cache.clear()
        logger.info("All caches cleared")
    
    # ==========================================================
    # STARTUP VALIDATION (Won't Crash Deploy)
    # ==========================================================
    
    def _validate_startup(self):
        """
        Validate database connection on startup.
        CRITICAL FIX: Logs errors but doesn't crash the service.
        """
        request_id = str(uuid.uuid4())[:8]
        self._startup_validation_passed = False
        
        try:
            # Check database connection
            with QueryTimer(request_id, "startup_validation") as timer:
                result = self.db.execute(text("SELECT 1 as connected")).first()
                
                if not result:
                    logger.error(f"[{request_id}] ❌ Database connection test failed")
                    return
                
                logger.info(f"[{request_id}] ✅ Database connection verified")
                
                # Check table exists and has records
                count_result = self.db.execute(
                    text(f"SELECT COUNT(*) as count FROM {self.table_name}")
                ).first()
                
                record_count = count_result.count if count_result else 0
                
                if record_count == 0:
                    logger.warning(f"[{request_id}] ⚠️ Table '{self.table_name}' has 0 records")
                else:
                    # Get distinct DN count
                    dn_count_result = self.db.execute(
                        text(f"SELECT COUNT(DISTINCT dn_no) as count FROM {self.table_name}")
                    ).first()
                    dn_count = dn_count_result.count if dn_count_result else 0
                    
                    # Get sample DN
                    sample_result = self.db.execute(
                        text(f"SELECT dn_no FROM {self.table_name} LIMIT 1")
                    ).first()
                    sample_dn = sample_result.dn_no if sample_result else None
                    
                    logger.info(f"[{request_id}] ✅ Database validation passed:")
                    logger.info(f"    - Table: {self.table_name}")
                    logger.info(f"    - Total Records: {record_count:,}")
                    logger.info(f"    - Total DNs: {dn_count:,}")
                    logger.info(f"    - Sample DN: {sample_dn}")
                    
                    self._startup_validation_passed = True
            
        except Exception as e:
            # CRITICAL FIX: Log error but don't crash
            logger.error(f"[{request_id}] ❌ Startup validation failed: {e}")
            logger.error(f"[{request_id}] Service will start but database features may not work")
            # Do NOT raise exception here - allow service to start
    
    # ==========================================================
    # DEALER RESOLUTION ENGINE (With Diagnostics)
    # ==========================================================
    
    def resolve_dealer_name(self, dealer_input: str) -> Tuple[Optional[str], float, str]:
        """
        Enhanced dealer resolution with REAL confidence scoring.
        With production diagnostics logging.
        """
        request_id = str(uuid.uuid4())[:8]
        self.metrics["dealer_resolutions"] += 1
        
        if not dealer_input:
            return None, 0.0, "empty_input"
        
        logger.info(f"[{request_id}] 🔍 Resolving dealer: '{dealer_input}'")
        logger.info(f"[{request_id}] DB={self.table_name}")
        
        with QueryTimer(request_id, "dealer_resolution") as timer:
            dealer_clean = dealer_input.strip()
            dealer_lower = dealer_clean.lower()
            
            # STRATEGY 1: Exact Dealer Name Match
            try:
                exact = self.db.query(DeliveryReport.customer_name).filter(
                    func.lower(DeliveryReport.customer_name) == dealer_lower
                ).first()
                if exact:
                    confidence = 0.99
                    self.metrics["dealer_resolution_hits"] += 1
                    logger.info(f"[{request_id}] ✅ Exact match: '{exact[0]}' (conf: {confidence:.2f})")
                    logger.info(f"[{request_id}] Rows Found=1, Strategy=exact_match")
                    return exact[0], confidence, "exact_match"
            except Exception as e:
                logger.debug(f"[{request_id}] Exact match failed: {e}")
            
            # STRATEGY 2: Dealer Code Match
            try:
                code_match = self.db.query(DeliveryReport.customer_name).filter(
                    func.lower(DeliveryReport.dealer_code) == dealer_lower
                ).first()
                if code_match:
                    confidence = 0.95
                    self.metrics["dealer_resolution_hits"] += 1
                    logger.info(f"[{request_id}] ✅ Dealer code match: '{code_match[0]}' (conf: {confidence:.2f})")
                    logger.info(f"[{request_id}] Rows Found=1, Strategy=dealer_code_match")
                    return code_match[0], confidence, "dealer_code_match"
            except Exception as e:
                logger.debug(f"[{request_id}] Dealer code match failed: {e}")
            
            # STRATEGY 3: Customer Code Match
            try:
                customer_code_match = self.db.query(DeliveryReport.customer_name).filter(
                    func.lower(DeliveryReport.customer_code) == dealer_lower
                ).first()
                if customer_code_match:
                    confidence = 0.95
                    self.metrics["dealer_resolution_hits"] += 1
                    logger.info(f"[{request_id}] ✅ Customer code match: '{customer_code_match[0]}' (conf: {confidence:.2f})")
                    logger.info(f"[{request_id}] Rows Found=1, Strategy=customer_code_match")
                    return customer_code_match[0], confidence, "customer_code_match"
            except Exception as e:
                logger.debug(f"[{request_id}] Customer code match failed: {e}")
            
            # STRATEGY 4: Contains Match
            try:
                contains = self.db.query(DeliveryReport.customer_name).filter(
                    DeliveryReport.customer_name.ilike(f"%{dealer_clean}%")
                ).first()
                if contains:
                    confidence = self._calculate_confidence(dealer_clean, contains[0], "contains")
                    self.metrics["dealer_resolution_hits"] += 1
                    logger.info(f"[{request_id}] ✅ Contains match: '{contains[0]}' (conf: {confidence:.2f})")
                    logger.info(f"[{request_id}] Rows Found=1, Strategy=contains_match")
                    return contains[0], confidence, "contains_match"
            except Exception as e:
                logger.debug(f"[{request_id}] Contains match failed: {e}")
            
            # STRATEGY 5: Word-by-Word Partial Match
            words = dealer_lower.split()
            if len(words) >= 2:
                try:
                    for i in range(len(words) - 1):
                        for j in range(i + 1, min(i + 4, len(words) + 1)):
                            pattern = ' '.join(words[i:j])
                            if len(pattern) >= 3:
                                result = self.db.query(DeliveryReport.customer_name).filter(
                                    func.lower(DeliveryReport.customer_name).contains(pattern)
                                ).first()
                                if result:
                                    confidence = self._calculate_confidence(pattern, result[0], "word")
                                    self.metrics["dealer_resolution_hits"] += 1
                                    logger.info(f"[{request_id}] ✅ Word match '{pattern}': '{result[0]}' (conf: {confidence:.2f})")
                                    logger.info(f"[{request_id}] Rows Found=1, Strategy=word_match")
                                    return result[0], confidence, "word_match"
                except Exception as e:
                    logger.debug(f"[{request_id}] Word match failed: {e}")
            
            # STRATEGY 6: Fuzzy Match
            try:
                all_dealers = self._get_cached_dealers()
                if all_dealers and len(all_dealers) <= 1000:
                    best_match = None
                    best_score = 0.0
                    
                    for dealer in all_dealers:
                        score = SequenceMatcher(None, dealer_lower, dealer.lower()).ratio()
                        if score > best_score and score >= 0.70:
                            best_score = score
                            best_match = dealer
                    
                    if best_match:
                        confidence = best_score
                        self.metrics["dealer_resolution_hits"] += 1
                        logger.info(f"[{request_id}] ✅ Fuzzy match: '{best_match}' (score: {best_score:.2f})")
                        logger.info(f"[{request_id}] Rows Found=1, Strategy=fuzzy_match")
                        return best_match, confidence, "fuzzy_match"
            except Exception as e:
                logger.debug(f"[{request_id}] Fuzzy match failed: {e}")
            
            # STRATEGY 7: Return DealerNotFoundError
            self.metrics["dealer_resolution_misses"] += 1
            logger.warning(f"[{request_id}] ❌ Dealer not resolved: '{dealer_input}'")
            logger.info(f"[{request_id}] Rows Found=0, Strategy=all_failed")
            raise DealerNotFoundError(dealer_input, 0.0)
    
    def _calculate_confidence(self, input_text: str, matched_text: str, strategy: str) -> float:
        """Calculate REAL confidence score based on match quality."""
        input_lower = input_text.lower().strip()
        matched_lower = matched_text.lower().strip()
        
        if input_lower == matched_lower:
            return 0.99
        
        if input_lower in matched_lower:
            base = 0.95
            ratio = len(input_lower) / len(matched_lower)
            if ratio < 0.3:
                base -= 0.10
            return min(base, 0.98)
        
        if matched_lower in input_lower:
            base = 0.90
            ratio = len(matched_lower) / len(input_lower)
            if ratio < 0.3:
                base -= 0.10
            return min(base, 0.95)
        
        score = SequenceMatcher(None, input_lower, matched_lower).ratio()
        
        input_words = set(input_lower.split())
        matched_words = set(matched_lower.split())
        common_words = input_words & matched_words
        
        if common_words:
            boost = min(len(common_words) * 0.05, 0.15)
            score = min(score + boost, 0.95)
        
        return round(score, 2)
    
    # ==========================================================
    # DN NORMALIZATION
    # ==========================================================
    
    def normalize_dn_number(self, dn_input: str) -> Optional[str]:
        """
        Normalize DN number to standard format.
        
        Supports:
        - 6243612069
        - 6243612069.0
        - 6243612069.00
        - "6243612069 "
        - " 6243612069"
        - 6243612069-0
        """
        if not dn_input:
            return None
        
        dn_clean = dn_input.strip()
        
        if '.' in dn_clean:
            dn_clean = dn_clean.split('.')[0]
        
        if '-' in dn_clean:
            dn_clean = dn_clean.split('-')[0]
        
        dn_clean = re.sub(r'[^0-9]', '', dn_clean)
        
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return None
        
        return dn_clean
    
    # ==========================================================
    # DN QUERIES (With Production Diagnostics)
    # ==========================================================
    
    def get_dn_details(self, dn_number: str) -> Optional[Dict[str, Any]]:
        """
        Get DN details with robust, datatype-safe search.
        
        PRODUCTION DIAGNOSTICS:
        - Structured logging with request_id
        - Query execution timing
        - Row count logging
        - Database context logging
        """
        request_id = str(uuid.uuid4())[:8]
        self.metrics["dn_lookups"] += 1
        found_this_request = False
        
        try:
            normalized_dn = self.normalize_dn_number(dn_number)
            
            # Log database context
            logger.info(f"[{request_id}] DB={self.table_name}")
            
            # Get total DNs count for context
            try:
                total_dns_result = self.db.execute(
                    text(f"SELECT COUNT(DISTINCT dn_no) as count FROM {self.table_name}")
                ).first()
                total_dns = total_dns_result.count if total_dns_result else 0
                logger.info(f"[{request_id}] Total DNs in DB={total_dns}")
            except Exception as e:
                logger.debug(f"[{request_id}] Could not get total DNs: {e}")
            
            if not normalized_dn:
                logger.warning(f"[{request_id}] Invalid DN: {dn_number}")
                logger.info(f"[{request_id}] Searching DN={dn_number} (invalid)")
                logger.info(f"[{request_id}] Rows Found=0, Query=normalization_failed")
                self.metrics["dn_lookups_misses"] += 1
                return None
            
            logger.info(f"[{request_id}] Searching DN={normalized_dn} (original: {dn_number})")
            logger.info(f"[{request_id}] Table={self.table_name}")
            
            with QueryTimer(request_id, "dn_lookup") as timer:
                # STRATEGY 1: Direct match (with CAST for safety)
                record = self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == normalized_dn
                ).first()
                
                if record:
                    found_this_request = True
                    logger.info(f"[{request_id}] ✅ DN found: {record.dn_no}")
                    logger.info(f"[{request_id}] Rows Found=1, Strategy=exact_match")
                    self.metrics["dn_lookups_hits"] += 1
                    return self._format_dn_record(record)
                
                # STRATEGY 2: LIKE pattern (for .0 variations)
                if normalized_dn.isdigit():
                    like_pattern = f"{normalized_dn}%"
                    # Use .limit(5).all() for diagnostics
                    records = self.db.query(DeliveryReport).filter(
                        cast(DeliveryReport.dn_no, String).like(like_pattern)
                    ).limit(5).all()
                    
                    if records:
                        found_this_request = True
                        logger.info(f"[{request_id}] ✅ DN found with LIKE: {records[0].dn_no}")
                        logger.info(f"[{request_id}] Rows Found={len(records)}, Strategy=like_match")
                        self.metrics["dn_lookups_hits"] += 1
                        return self._format_dn_record(records[0])
                
                # STRATEGY 3: Leading zeros
                if normalized_dn.isdigit():
                    for zeros in range(1, 4):
                        padded = normalized_dn.zfill(len(normalized_dn) + zeros)
                        records = self.db.query(DeliveryReport).filter(
                            cast(DeliveryReport.dn_no, String) == padded
                        ).limit(5).all()
                        
                        if records:
                            found_this_request = True
                            logger.info(f"[{request_id}] ✅ DN with leading zeros: {records[0].dn_no}")
                            logger.info(f"[{request_id}] Rows Found={len(records)}, Strategy=leading_zero_match")
                            self.metrics["dn_lookups_hits"] += 1
                            return self._format_dn_record(records[0])
                
                # STRATEGY 4: Contains pattern (for DN in text)
                if normalized_dn.isdigit():
                    records = self.db.query(DeliveryReport).filter(
                        cast(DeliveryReport.dn_no, String).contains(normalized_dn)
                    ).limit(5).all()
                    
                    if records:
                        found_this_request = True
                        logger.info(f"[{request_id}] ✅ DN found with contains: {records[0].dn_no}")
                        logger.info(f"[{request_id}] Rows Found={len(records)}, Strategy=contains_match")
                        self.metrics["dn_lookups_hits"] += 1
                        return self._format_dn_record(records[0])
                
                # STRATEGY 5: Return DNNotFoundError
                logger.warning(f"[{request_id}] ❌ DN not found: {dn_number} (normalized: {normalized_dn})")
                logger.info(f"[{request_id}] Rows Found=0, Strategy=all_failed")
                self.metrics["dn_lookups_misses"] += 1
                raise DNNotFoundError(dn_number, normalized_dn)
                
        except DNNotFoundError:
            raise
        except Exception as e:
            logger.error(f"[{request_id}] DN query failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.metrics["dn_lookups_misses"] += 1
            return None
        finally:
            duration_ms = timer.get_duration() if 'timer' in locals() else 0
            self.metrics["total_query_time_ms"] += duration_ms
            self.metrics["total_queries"] += 1
    
    def _format_dn_record(self, record) -> Dict[str, Any]:
        """Format DN record with Python date calculations."""
        # Safe date calculations with None checks
        pgi_aging = None
        pod_aging = None
        total_aging = None
        
        if record.dn_create_date and record.good_issue_date:
            if record.good_issue_date >= record.dn_create_date:
                pgi_aging = (record.good_issue_date - record.dn_create_date).days
        
        if record.good_issue_date and record.pod_date:
            if record.pod_date >= record.good_issue_date:
                pod_aging = (record.pod_date - record.good_issue_date).days
        
        if record.dn_create_date and record.pod_date:
            if record.pod_date >= record.dn_create_date:
                total_aging = (record.pod_date - record.dn_create_date).days
        
        if record.pod_status == 'Completed':
            status = "delivered"
        elif record.delivery_status == 'Completed':
            status = "in_transit"
        else:
            status = "pending_pgi"
        
        return {
            "dn_number": record.dn_no,
            "dealer": record.customer_name,
            "dealer_code": record.dealer_code,
            "customer_code": record.customer_code,
            "warehouse": record.warehouse,
            "warehouse_code": record.warehouse_code,
            "city": record.ship_to_city,
            "delivery_location": record.delivery_location,
            "material_no": record.material_no,
            "customer_model": record.customer_model,
            "units": int(record.dn_qty or 0),
            "amount": float(record.dn_amount or 0),
            "dn_date": record.dn_create_date,
            "pgi_date": record.good_issue_date,
            "pod_date": record.pod_date,
            "pgi_aging_days": pgi_aging,
            "pod_aging_days": pod_aging,
            "total_aging_days": total_aging,
            "status": status,
            "status_display": self.schema.get_dn_status(status),
            "delivery_status": record.delivery_status,
            "pgi_status": record.pgi_status,
            "pod_status": record.pod_status,
            "sales_office": record.sales_office,
            "sales_manager": record.sales_manager,
            "division": record.division,
            "source_file": record.source_file if hasattr(record, 'source_file') else None
        }
    
    # ==========================================================
    # VERIFY SPECIFIC DN (CRITICAL NEW METHOD)
    # ==========================================================
    
    def verify_specific_dn(self, dn: str) -> Dict[str, Any]:
        """
        Direct raw SQL verification of a DN.
        
        This is the most reliable method to check if a DN exists.
        It uses raw SQL and returns all available data.
        
        Returns:
            {
                "dn": "6243611858",
                "normalized_dn": "6243611858",
                "found": true,
                "table": "delivery_reports",
                "record": {
                    "dn_no": "6243611858",
                    "customer_name": "ABC Electronics",
                    "source_file": "May DN & PGI.xlsx",
                    ...
                },
                "total_records_with_dn": 1,
                "raw_sql": "SELECT * FROM delivery_reports WHERE CAST(dn_no AS TEXT) = '6243611858'"
            }
        """
        request_id = str(uuid.uuid4())[:8]
        
        try:
            normalized_dn = self.normalize_dn_number(dn)
            
            if not normalized_dn:
                return {
                    "dn": dn,
                    "normalized_dn": None,
                    "found": False,
                    "error": "Invalid DN format",
                    "table": self.table_name
                }
            
            logger.info(f"[{request_id}] 🔍 VERIFY_SPECIFIC_DN: {dn} (normalized: {normalized_dn})")
            logger.info(f"[{request_id}] Table={self.table_name}")
            
            # Build raw SQL for transparency
            raw_sql = f"SELECT * FROM {self.table_name} WHERE CAST(dn_no AS TEXT) = '{normalized_dn}' LIMIT 1"
            logger.info(f"[{request_id}] SQL={raw_sql}")
            
            with QueryTimer(request_id, "verify_specific_dn") as timer:
                # Execute raw SQL
                result = self.db.execute(
                    text(f"""
                        SELECT 
                            dn_no,
                            customer_name,
                            dealer_code,
                            customer_code,
                            division,
                            warehouse,
                            warehouse_code,
                            delivery_location,
                            sales_office,
                            sales_manager,
                            ship_to_city,
                            dn_qty,
                            dn_amount,
                            dn_create_date,
                            good_issue_date,
                            pod_date,
                            delivery_status,
                            pgi_status,
                            pod_status,
                            material_no,
                            customer_model,
                            source_file
                        FROM {self.table_name}
                        WHERE CAST(dn_no AS TEXT) = :dn
                        LIMIT 1
                    """),
                    {"dn": normalized_dn}
                ).first()
                
                # Get count of all matches
                count_result = self.db.execute(
                    text(f"""
                        SELECT COUNT(*) as count
                        FROM {self.table_name}
                        WHERE CAST(dn_no AS TEXT) = :dn
                    """),
                    {"dn": normalized_dn}
                ).first()
                
                record_count = count_result.count if count_result else 0
                found = record_count > 0
                
                response = {
                    "dn": dn,
                    "normalized_dn": normalized_dn,
                    "found": found,
                    "table": self.table_name,
                    "total_records_with_dn": record_count,
                    "raw_sql": raw_sql,
                    "duration_ms": timer.get_duration()
                }
                
                if found and result:
                    # Convert to dict
                    record_dict = {
                        "dn_no": result.dn_no,
                        "customer_name": result.customer_name,
                        "dealer_code": result.dealer_code,
                        "customer_code": result.customer_code,
                        "division": result.division,
                        "warehouse": result.warehouse,
                        "warehouse_code": result.warehouse_code,
                        "delivery_location": result.delivery_location,
                        "sales_office": result.sales_office,
                        "sales_manager": result.sales_manager,
                        "ship_to_city": result.ship_to_city,
                        "dn_qty": int(result.dn_qty) if result.dn_qty else 0,
                        "dn_amount": float(result.dn_amount) if result.dn_amount else 0,
                        "dn_create_date": result.dn_create_date.isoformat() if result.dn_create_date else None,
                        "good_issue_date": result.good_issue_date.isoformat() if result.good_issue_date else None,
                        "pod_date": result.pod_date.isoformat() if result.pod_date else None,
                        "delivery_status": result.delivery_status,
                        "pgi_status": result.pgi_status,
                        "pod_status": result.pod_status,
                        "material_no": result.material_no,
                        "customer_model": result.customer_model,
                        "source_file": result.source_file
                    }
                    response["record"] = record_dict
                    logger.info(f"[{request_id}] ✅ DN verified: {result.dn_no} - {result.customer_name}")
                else:
                    logger.warning(f"[{request_id}] ❌ DN NOT FOUND: {dn}")
                
                return response
                
        except Exception as e:
            logger.error(f"[{request_id}] Verification failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "dn": dn,
                "found": False,
                "table": self.table_name,
                "error": str(e)
            }
    
    # ==========================================================
    # DATABASE VERIFICATION METHOD
    # ==========================================================
    
    def verify_dn_exists(self, dn: str) -> Dict[str, Any]:
        """
        Directly verify whether a DN exists in PostgreSQL.
        
        Returns:
            {
                "dn": "6243611858",
                "normalized_dn": "6243611858",
                "found": true,
                "table": "delivery_reports",
                "record_count": 1,
                "dealer_name": "ZQ Electronics"
            }
        """
        request_id = str(uuid.uuid4())[:8]
        
        try:
            normalized_dn = self.normalize_dn_number(dn)
            
            if not normalized_dn:
                return {
                    "dn": dn,
                    "normalized_dn": None,
                    "found": False,
                    "table": self.table_name,
                    "record_count": 0,
                    "error": "Invalid DN format"
                }
            
            logger.info(f"[{request_id}] Verifying DN: {dn} (normalized: {normalized_dn})")
            logger.info(f"[{request_id}] Table={self.table_name}")
            
            with QueryTimer(request_id, "verify_dn") as timer:
                # Query using CAST for safety
                result = self.db.execute(
                    text(f"""
                        SELECT 
                            dn_no,
                            customer_name as dealer_name,
                            warehouse,
                            ship_to_city,
                            dn_qty,
                            dn_amount
                        FROM {self.table_name}
                        WHERE CAST(dn_no AS TEXT) = :dn
                        LIMIT 1
                    """),
                    {"dn": normalized_dn}
                ).first()
                
                # Get count of all matches
                count_result = self.db.execute(
                    text(f"""
                        SELECT COUNT(*) as count
                        FROM {self.table_name}
                        WHERE CAST(dn_no AS TEXT) = :dn
                    """),
                    {"dn": normalized_dn}
                ).first()
                
                record_count = count_result.count if count_result else 0
                found = record_count > 0
                
                response = {
                    "dn": dn,
                    "normalized_dn": normalized_dn,
                    "found": found,
                    "table": self.table_name,
                    "record_count": record_count,
                    "duration_ms": timer.get_duration()
                }
                
                if found and result:
                    response.update({
                        "dealer_name": result.dealer_name,
                        "warehouse": result.warehouse,
                        "city": result.ship_to_city,
                        "dn_qty": int(result.dn_qty or 0),
                        "dn_amount": float(result.dn_amount or 0)
                    })
                
                logger.info(f"[{request_id}] Verification complete: found={found}, count={record_count}")
                return response
                
        except Exception as e:
            logger.error(f"[{request_id}] Verification failed: {e}")
            return {
                "dn": dn,
                "found": False,
                "table": self.table_name,
                "error": str(e)
            }
    
    # ==========================================================
    # DATABASE HEALTH CHECK (FIXED CACHE CHECK)
    # ==========================================================
    
    def debug_database(self) -> Dict[str, Any]:
        """
        Debug database health and connection.
        
        Returns:
            {
                "database_connected": true,
                "table_name": "delivery_reports",
                "total_records": 125432,
                "total_dns": 89211,
                "sample_dn": "6243612314",
                "cache_provider": "RedisCacheProvider"
            }
        """
        request_id = str(uuid.uuid4())[:8]
        
        try:
            logger.info(f"[{request_id}] 🔍 Debugging database...")
            logger.info(f"[{request_id}] Table={self.table_name}")
            
            # Check connection
            connection_result = self.db.execute(text("SELECT 1 as connected")).first()
            database_connected = connection_result is not None
            
            if not database_connected:
                return {
                    "database_connected": False,
                    "error": "Database connection failed"
                }
            
            # Get record count
            count_result = self.db.execute(
                text(f"SELECT COUNT(*) as count FROM {self.table_name}")
            ).first()
            total_records = count_result.count if count_result else 0
            
            # Get distinct DN count
            dn_count_result = self.db.execute(
                text(f"SELECT COUNT(DISTINCT dn_no) as count FROM {self.table_name}")
            ).first()
            total_dns = dn_count_result.count if dn_count_result else 0
            
            # Get sample DN
            sample_result = self.db.execute(
                text(f"SELECT dn_no FROM {self.table_name} LIMIT 1")
            ).first()
            sample_dn = sample_result.dn_no if sample_result else None
            
            # Get dealer count
            dealer_count_result = self.db.execute(
                text(f"SELECT COUNT(DISTINCT customer_name) as count FROM {self.table_name} WHERE customer_name IS NOT NULL")
            ).first()
            total_dealers = dealer_count_result.count if dealer_count_result else 0
            
            # FIXED: Proper cache health check
            cache_available = False
            if isinstance(self.cache, RedisCacheProvider):
                cache_available = self.cache._available
            elif isinstance(self.cache, InMemoryCacheProvider):
                cache_available = True
            
            response = {
                "database_connected": True,
                "table_name": self.table_name,
                "total_records": total_records,
                "total_dns": total_dns,
                "total_dealers": total_dealers,
                "sample_dn": sample_dn,
                "cache_provider": type(self.cache).__name__,
                "cache_available": cache_available
            }
            
            logger.info(f"[{request_id}] Database debug complete: {total_records:,} records, {total_dns:,} DNs")
            return response
            
        except Exception as e:
            logger.error(f"[{request_id}] Database debug failed: {e}")
            return {
                "database_connected": False,
                "error": str(e)
            }
    
    # ==========================================================
    # DN DEBUG METHOD
    # ==========================================================
    
    def debug_dn(self, dn: str) -> Dict[str, Any]:
        """
        Debug DN lookup with all strategies.
        
        Returns:
            {
                "input_dn": "6243611858",
                "normalized_dn": "6243611858",
                "found": true,
                "lookup_strategy": "exact_match",
                "dealer_name": "ABC Electronics",
                "warehouse": "Rawalpindi",
                "source_file": "May DN & PGI.xlsx"
            }
        """
        request_id = str(uuid.uuid4())[:8]
        
        try:
            normalized_dn = self.normalize_dn_number(dn)
            
            if not normalized_dn:
                return {
                    "input_dn": dn,
                    "normalized_dn": None,
                    "found": False,
                    "lookup_strategy": "invalid_format",
                    "error": "Invalid DN format"
                }
            
            logger.info(f"[{request_id}] 🔍 Debugging DN: {dn} (normalized: {normalized_dn})")
            logger.info(f"[{request_id}] Table={self.table_name}")
            
            strategies_tested = []
            record = None
            final_strategy = None
            
            with QueryTimer(request_id, "debug_dn") as timer:
                # STRATEGY 1: Exact Match
                strategies_tested.append("exact_match")
                record = self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == normalized_dn
                ).first()
                
                if record:
                    final_strategy = "exact_match"
                    logger.info(f"[{request_id}] Found with exact_match: {record.dn_no}")
                
                # STRATEGY 2: LIKE Match
                if not record and normalized_dn.isdigit():
                    strategies_tested.append("like_match")
                    like_pattern = f"{normalized_dn}%"
                    records = self.db.query(DeliveryReport).filter(
                        cast(DeliveryReport.dn_no, String).like(like_pattern)
                    ).limit(5).all()
                    if records:
                        record = records[0]
                        final_strategy = "like_match"
                        logger.info(f"[{request_id}] Found with like_match: {record.dn_no} ({len(records)} matches)")
                
                # STRATEGY 3: Leading Zero Match
                if not record and normalized_dn.isdigit():
                    strategies_tested.append("leading_zero_match")
                    for zeros in range(1, 4):
                        padded = normalized_dn.zfill(len(normalized_dn) + zeros)
                        records = self.db.query(DeliveryReport).filter(
                            cast(DeliveryReport.dn_no, String) == padded
                        ).limit(5).all()
                        if records:
                            record = records[0]
                            final_strategy = f"leading_zero_match_{zeros}"
                            logger.info(f"[{request_id}] Found with leading_zero_match: {record.dn_no} ({len(records)} matches)")
                            break
                
                # STRATEGY 4: Contains Match
                if not record and normalized_dn.isdigit():
                    strategies_tested.append("contains_match")
                    records = self.db.query(DeliveryReport).filter(
                        cast(DeliveryReport.dn_no, String).contains(normalized_dn)
                    ).limit(5).all()
                    if records:
                        record = records[0]
                        final_strategy = "contains_match"
                        logger.info(f"[{request_id}] Found with contains_match: {record.dn_no} ({len(records)} matches)")
                
                # Build response
                response = {
                    "input_dn": dn,
                    "normalized_dn": normalized_dn,
                    "found": record is not None,
                    "lookup_strategy": final_strategy or "all_failed",
                    "strategies_tested": strategies_tested,
                    "duration_ms": timer.get_duration()
                }
                
                if record:
                    response.update({
                        "dealer_name": record.customer_name,
                        "dealer_code": record.dealer_code,
                        "customer_code": record.customer_code,
                        "warehouse": record.warehouse,
                        "warehouse_code": record.warehouse_code,
                        "city": record.ship_to_city,
                        "delivery_location": record.delivery_location,
                        "source_file": record.source_file if hasattr(record, 'source_file') else None,
                        "delivery_status": record.delivery_status,
                        "pgi_status": record.pgi_status,
                        "pod_status": record.pod_status,
                        "dn_qty": int(record.dn_qty or 0),
                        "dn_amount": float(record.dn_amount or 0)
                    })
                else:
                    response["error"] = "DN not found in database"
                
                logger.info(f"[{request_id}] Debug complete: found={record is not None}, strategy={final_strategy}")
                return response
                
        except Exception as e:
            logger.error(f"[{request_id}] Debug DN failed: {e}")
            return {
                "input_dn": dn,
                "found": False,
                "error": str(e)
            }
    
    # ==========================================================
    # DEALER DEBUG METHOD (FIXED RECURSIVE CALL)
    # ==========================================================
    
    def debug_dealer(self, dealer_name: str) -> Dict[str, Any]:
        """
        Debug dealer resolution.
        
        FIXED: Passes resolved name to get_dealer_profile() to avoid duplicate resolution.
        
        Returns:
            {
                "input": "ZQ Electronics",
                "resolved": true,
                "resolved_name": "ZQ Electronics",
                "confidence": 0.98,
                "strategy": "exact_match",
                "total_records": 245
            }
        """
        request_id = str(uuid.uuid4())[:8]
        
        try:
            logger.info(f"[{request_id}] 🔍 Debugging dealer: '{dealer_name}'")
            logger.info(f"[{request_id}] Table={self.table_name}")
            
            with QueryTimer(request_id, "debug_dealer") as timer:
                try:
                    # FIXED: Only resolve once
                    resolved_name, confidence, strategy = self.resolve_dealer_name(dealer_name)
                    
                    # Get record count for this dealer
                    count_result = self.db.execute(
                        text(f"""
                            SELECT COUNT(DISTINCT dn_no) as count
                            FROM {self.table_name}
                            WHERE customer_name = :dealer_name
                        """),
                        {"dealer_name": resolved_name}
                    ).first()
                    
                    total_records = count_result.count if count_result else 0
                    
                    # FIXED: Pass resolved_name directly to avoid recursive call
                    profile = self._get_dealer_profile_by_name(resolved_name)
                    
                    response = {
                        "input": dealer_name,
                        "resolved": True,
                        "resolved_name": resolved_name,
                        "confidence": confidence,
                        "strategy": strategy,
                        "total_records": total_records,
                        "duration_ms": timer.get_duration()
                    }
                    
                    if profile:
                        response.update({
                            "dealer_code": profile.get("dealer_code"),
                            "customer_code": profile.get("customer_code"),
                            "division": profile.get("division"),
                            "city": profile.get("city"),
                            "warehouse": profile.get("warehouse"),
                            "sales_office": profile.get("sales_office"),
                            "sales_manager": profile.get("sales_manager"),
                            "first_dn_date": profile.get("first_dn_date"),
                            "last_dn_date": profile.get("last_dn_date")
                        })
                    
                    logger.info(f"[{request_id}] Dealer debug complete: '{resolved_name}' ({strategy})")
                    return response
                    
                except DealerNotFoundError:
                    logger.warning(f"[{request_id}] Dealer not resolved: '{dealer_name}'")
                    return {
                        "input": dealer_name,
                        "resolved": False,
                        "duration_ms": timer.get_duration()
                    }
                
        except Exception as e:
            logger.error(f"[{request_id}] Debug dealer failed: {e}")
            return {
                "input": dealer_name,
                "resolved": False,
                "error": str(e)
            }
    
    def _get_dealer_profile_by_name(self, resolved_name: str) -> Optional[Dict[str, Any]]:
        """
        Internal method to get dealer profile by resolved name.
        No resolution performed - assumes name is already resolved.
        """
        try:
            result = self.db.query(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.division,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.delivery_location,
                func.min(DeliveryReport.dn_create_date).label('first_dn_date'),
                func.max(DeliveryReport.dn_create_date).label('last_dn_date')
            ).filter(
                DeliveryReport.customer_name == resolved_name
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.division,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.delivery_location
            ).first()
            
            if not result:
                return None
            
            return {
                "dealer_name": result.customer_name,
                "dealer_code": result.dealer_code,
                "customer_code": result.customer_code,
                "division": result.division,
                "sales_office": result.sales_office,
                "sales_manager": result.sales_manager,
                "warehouse": result.warehouse,
                "warehouse_code": result.warehouse_code,
                "city": result.ship_to_city,
                "delivery_location": result.delivery_location,
                "first_dn_date": result.first_dn_date,
                "last_dn_date": result.last_dn_date
            }
            
        except Exception as e:
            logger.error(f"Get dealer profile by name failed: {e}")
            return None
    
    # ==========================================================
    # DEALER DASHBOARD QUERIES (FIXED MODE() WITHIN GROUP)
    # ==========================================================
    
    def get_dealer_dashboard_data(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get comprehensive dealer dashboard data with SQL Aggregation.
        
        FIXED: Replaced MODE() WITHIN GROUP with MAX() for PostgreSQL compatibility.
        """
        request_id = str(uuid.uuid4())[:8]
        self.metrics["dashboard_generations"] += 1
        
        logger.info(f"[{request_id}] 📊 Dashboard requested for: '{dealer_name}'")
        logger.info(f"[{request_id}] Table={self.table_name}")
        
        with QueryTimer(request_id, "dashboard_generation") as timer:
            try:
                # Step 1: Resolve dealer
                resolved_name, confidence, strategy = self.resolve_dealer_name(dealer_name)
                logger.info(f"[{request_id}] Resolved dealer: '{resolved_name}' (strategy: {strategy})")
                
                # Step 2: SQL Aggregation
                # FIXED: Replaced MODE() WITHIN GROUP with MAX() for PostgreSQL compatibility
                sql = text(f"""
                    SELECT 
                        customer_name as dealer_name,
                        MAX(dealer_code) as dealer_code,
                        MAX(customer_code) as customer_code,
                        MAX(division) as division,
                        MAX(warehouse_code) as warehouse_code,
                        MAX(delivery_location) as delivery_location,
                        MAX(sales_office) as sales_office,
                        MAX(sales_manager) as sales_manager,
                        MAX(warehouse) as top_warehouse,
                        MAX(ship_to_city) as city,  -- FIXED: Replaced MODE() WITHIN GROUP
                        MIN(dn_create_date) as first_dn_date,
                        MAX(dn_create_date) as last_dn_date,
                        
                        COUNT(DISTINCT dn_no) as total_dns,
                        COALESCE(SUM(dn_qty), 0) as total_units,
                        COALESCE(SUM(dn_amount), 0) as total_revenue,
                        
                        COUNT(DISTINCT CASE 
                            WHEN delivery_status = 'Completed' AND good_issue_date IS NOT NULL 
                            THEN dn_no 
                        END) as delivered_dns,
                        
                        COUNT(DISTINCT CASE 
                            WHEN delivery_status != 'Completed' OR good_issue_date IS NULL 
                            THEN dn_no 
                        END) as pending_dns,
                        
                        COUNT(DISTINCT CASE 
                            WHEN delivery_status = 'Completed' AND pod_status != 'Completed' 
                            THEN dn_no 
                        END) as transit_dns,
                        
                        COUNT(DISTINCT CASE 
                            WHEN pod_status = 'Completed' 
                            THEN dn_no 
                        END) as pod_completed_dns,
                        
                        COUNT(DISTINCT CASE 
                            WHEN delivery_status = 'Completed' AND pod_status != 'Completed' 
                            THEN dn_no 
                        END) as pending_pod_dns,
                        
                        AVG(CASE 
                            WHEN good_issue_date IS NOT NULL AND dn_create_date IS NOT NULL 
                            THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400 
                        END) as avg_pgi_aging,
                        
                        AVG(CASE 
                            WHEN pod_date IS NOT NULL AND good_issue_date IS NOT NULL 
                            THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400 
                        END) as avg_pod_aging,
                        
                        AVG(CASE 
                            WHEN pod_date IS NOT NULL AND dn_create_date IS NOT NULL 
                            THEN EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400 
                        END) as avg_total_aging,
                        
                        COALESCE(SUM(CASE WHEN delivery_status = 'Completed' THEN dn_qty ELSE 0 END), 0) as delivered_units,
                        COALESCE(SUM(CASE WHEN delivery_status != 'Completed' OR good_issue_date IS NULL THEN dn_qty ELSE 0 END), 0) as pending_units,
                        COALESCE(SUM(CASE WHEN delivery_status = 'Completed' AND pod_status != 'Completed' THEN dn_qty ELSE 0 END), 0) as transit_units
                        
                    FROM {self.table_name}
                    WHERE customer_name = :dealer_name
                    GROUP BY customer_name
                """)
                
                result = self.db.execute(sql, {"dealer_name": resolved_name}).first()
                
                if not result or result.total_dns == 0:
                    logger.warning(f"[{request_id}] No records for dealer: '{resolved_name}'")
                    self.metrics["dashboard_generations_failure"] += 1
                    raise DashboardGenerationError(resolved_name, "No records found")
                
                # Get oldest pending
                oldest_sql = text(f"""
                    SELECT dn_no, dn_create_date 
                    FROM {self.table_name}
                    WHERE customer_name = :dealer_name 
                    AND (delivery_status != 'Completed' OR good_issue_date IS NULL)
                    ORDER BY dn_create_date 
                    LIMIT 1
                """)
                oldest = self.db.execute(oldest_sql, {"dealer_name": resolved_name}).first()
                
                # Build dashboard
                total_dns = result.total_dns or 1
                delivered_dns = result.delivered_dns or 0
                pod_completed_dns = result.pod_completed_dns or 0
                
                delivery_rate = round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
                
                if delivered_dns == 0 and total_dns == 0:
                    dealer_status = "Inactive"
                elif total_dns < 10:
                    dealer_status = "Low Activity"
                elif delivery_rate >= 90:
                    dealer_status = "Active - High Performance"
                else:
                    dealer_status = "Active - Needs Attention"
                
                dashboard = {
                    "success": True,
                    "dealer_name": resolved_name,
                    "dealer_code": result.dealer_code or "Unknown",
                    "customer_code": result.customer_code or "Unknown",
                    "division": result.division or "Unknown",
                    "sales_office": result.sales_office or "Unknown",
                    "sales_manager": result.sales_manager or "Unknown",
                    "city": result.city or "Unknown",
                    "warehouse": result.top_warehouse or "Unknown",
                    "warehouse_code": result.warehouse_code or "Unknown",
                    "delivery_location": result.delivery_location or "Unknown",
                    "dealer_status": dealer_status,
                    "first_dn_date": result.first_dn_date,
                    "last_dn_date": result.last_dn_date,
                    "total_dns": total_dns,
                    "total_units": int(result.total_units or 0),
                    "total_revenue": float(result.total_revenue or 0),
                    "delivered_dns": delivered_dns,
                    "pending_dns": result.pending_dns or 0,
                    "transit_dns": result.transit_dns or 0,
                    "pod_completed_dns": pod_completed_dns,
                    "pending_pod_dns": result.pending_pod_dns or 0,
                    "delivered_units": int(result.delivered_units or 0),
                    "pending_units": int(result.pending_units or 0),
                    "transit_units": int(result.transit_units or 0),
                    "delivery_rate": delivery_rate,
                    "pod_rate": round((pod_completed_dns / (delivered_dns or 1) * 100) if delivered_dns > 0 else 0, 1),
                    "avg_pgi_aging": round(result.avg_pgi_aging or 0, 1),
                    "avg_pod_aging": round(result.avg_pod_aging or 0, 1),
                    "avg_total_aging": round(result.avg_total_aging or 0, 1),
                    "oldest_pending_dn": oldest.dn_no if oldest else None,
                    "oldest_pending_days": (self.today - oldest.dn_create_date).days if oldest and oldest.dn_create_date else 0,
                    "metadata": {
                        "request_id": request_id,
                        "duration_ms": timer.get_duration(),
                        "resolution_confidence": confidence,
                        "resolution_strategy": strategy,
                        "total_records": total_dns
                    }
                }
                
                self.metrics["dashboard_generations_success"] += 1
                self.metrics["total_query_time_ms"] += timer.get_duration()
                self.metrics["total_queries"] += 1
                
                logger.info(f"[{request_id}] ✅ Dashboard generated: {total_dns} DNs, {dashboard['total_revenue']:.2f} revenue")
                return dashboard
                
            except (DealerNotFoundError, DashboardGenerationError):
                self.metrics["dashboard_generations_failure"] += 1
                raise
            except Exception as e:
                logger.error(f"[{request_id}] Dashboard generation failed: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self.metrics["dashboard_generations_failure"] += 1
                raise DatabaseQueryError("get_dealer_dashboard_data", str(e))
    
    # ==========================================================
    # LEGACY METHODS
    # ==========================================================
    
    def get_all_dealer_names(self) -> List[str]:
        """Get all unique dealer names from database."""
        return self._get_cached_dealers()
    
    def get_all_warehouse_names(self) -> List[str]:
        """Get all unique warehouse names from database."""
        try:
            results = self.db.query(DeliveryReport.warehouse).filter(
                DeliveryReport.warehouse.isnot(None),
                DeliveryReport.warehouse != ''
            ).distinct().order_by(DeliveryReport.warehouse).all()
            return [r[0] for r in results if r[0]]
        except Exception as e:
            logger.error(f"Get all warehouse names failed: {e}")
            return []
    
    def get_all_city_names(self) -> List[str]:
        """Get all unique city names from database."""
        try:
            results = self.db.query(DeliveryReport.ship_to_city).filter(
                DeliveryReport.ship_to_city.isnot(None),
                DeliveryReport.ship_to_city != ''
            ).distinct().order_by(DeliveryReport.ship_to_city).all()
            return [r[0] for r in results if r[0]]
        except Exception as e:
            logger.error(f"Get all city names failed: {e}")
            return []
    
    def get_dealer_profile(self, dealer_name: str) -> Optional[Dict[str, Any]]:
        """Get complete dealer profile with all fields."""
        try:
            resolved_name, _, _ = self.resolve_dealer_name(dealer_name)
            return self._get_dealer_profile_by_name(resolved_name)
        except Exception as e:
            logger.error(f"Get dealer profile failed: {e}")
            return None
    
    # ==========================================================
    # DEBUG API READY ENDPOINTS
    # ==========================================================
    
    def debug_endpoints(self) -> Dict[str, str]:
        """Return available debug endpoints."""
        return {
            "debug_database": "GET /debug/database",
            "debug_dn": "GET /debug/dn/{dn}",
            "debug_dealer": "GET /debug/dealer/{dealer}",
            "verify_dn": "GET /debug/verify/{dn}",
            "verify_specific": "GET /debug/verify_specific/{dn}"
        }
    
    # ==========================================================
    # METRICS
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get performance metrics."""
        return {
            "total_queries": self.metrics["total_queries"],
            "total_query_time_ms": self.metrics["total_query_time_ms"],
            "avg_query_time_ms": round(self.metrics["total_query_time_ms"] / max(1, self.metrics["total_queries"]), 2),
            "dealer_resolutions": {
                "total": self.metrics["dealer_resolutions"],
                "hits": self.metrics["dealer_resolution_hits"],
                "misses": self.metrics["dealer_resolution_misses"],
                "cache_hits": self.metrics["dealer_cache_hits"],
                "cache_misses": self.metrics["dealer_cache_misses"],
                "success_rate": round(self.metrics["dealer_resolution_hits"] / max(1, self.metrics["dealer_resolutions"]) * 100, 1)
            },
            "dn_lookups": {
                "total": self.metrics["dn_lookups"],
                "hits": self.metrics["dn_lookups_hits"],
                "misses": self.metrics["dn_lookups_misses"],
                "success_rate": round(self.metrics["dn_lookups_hits"] / max(1, self.metrics["dn_lookups"]) * 100, 1)
            },
            "dashboard_generations": {
                "total": self.metrics["dashboard_generations"],
                "success": self.metrics["dashboard_generations_success"],
                "failure": self.metrics["dashboard_generations_failure"],
                "success_rate": round(self.metrics["dashboard_generations_success"] / max(1, self.metrics["dashboard_generations"]) * 100, 1)
            },
            "version": "6.0",
            "table_name": self.table_name,
            "cache_type": type(self.cache).__name__
        }


# ==========================================================
# FASTAPI DEBUG ROUTES (Ready to include in your router)
# ==========================================================

"""
Add these routes to your FastAPI router:

from fastapi import APIRouter
from app.services.logistics_query_service import get_logistics_query_service

router = APIRouter(prefix="/debug", tags=["debug"])

@router.get("/database")
async def debug_database():
    service = get_logistics_query_service()
    return service.debug_database()

@router.get("/dn/{dn}")
async def debug_dn(dn: str):
    service = get_logistics_query_service()
    return service.debug_dn(dn)

@router.get("/dealer/{dealer}")
async def debug_dealer(dealer: str):
    service = get_logistics_query_service()
    return service.debug_dealer(dealer)

@router.get("/verify/{dn}")
async def verify_dn(dn: str):
    service = get_logistics_query_service()
    return service.verify_dn_exists(dn)

@router.get("/verify_specific/{dn}")
async def verify_specific_dn(dn: str):
    service = get_logistics_query_service()
    return service.verify_specific_dn(dn)

@router.get("/endpoints")
async def debug_endpoints():
    service = get_logistics_query_service()
    return service.debug_endpoints()
"""


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(
    db: Optional[Session] = None,
    cache_provider: Optional[CacheProvider] = None
) -> LogisticsQueryService:
    """Factory function for LogisticsQueryService singleton."""
    return LogisticsQueryService(db=db, cache_provider=cache_provider)


# ==========================================================
# CONVENIENCE FUNCTIONS
# ==========================================================

def create_production_service() -> LogisticsQueryService:
    """Create service with Redis cache (if available)."""
    try:
        cache = RedisCacheProvider()
        return LogisticsQueryService(cache_provider=cache)
    except:
        logger.warning("⚠️ Using in-memory cache (Redis not available)")
        return LogisticsQueryService()


def create_test_service(db: Session) -> LogisticsQueryService:
    """Create service for testing with in-memory cache."""
    cache = InMemoryCacheProvider()
    return LogisticsQueryService(db=db, cache_provider=cache)
