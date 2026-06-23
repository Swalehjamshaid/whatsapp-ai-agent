# ==========================================================
# FILE: app/services/analytics_service.py (v32.0 - ENTERPRISE)
# PURPOSE: CENTRAL DATA ENGINE - POSTGRESQL ONLY
# VERSION: 32.0 - Complete Enterprise Refactoring
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import uuid
import re
import math
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, case, desc, asc, cast, String, text, distinct
from functools import lru_cache
import json
import hashlib
import threading
from cachetools import TTLCache

# ==========================================================
# BLOCK 1: POSTGRESQL IMPORTS - THE SOURCE OF TRUTH
# ==========================================================

# ==========================================================
# FILE: app/services/analytics_service.py (v32.0 - ENTERPRISE)
# PURPOSE: CENTRAL DATA ENGINE - POSTGRESQL ONLY
# VERSION: 32.0 - Complete Enterprise Refactoring
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import uuid
import re
import math
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, case, desc, asc, cast, String, text, distinct
from functools import lru_cache
import json
import hashlib
import threading
from cachetools import TTLCache

# ==========================================================
# BLOCK 1: POSTGRESQL IMPORTS - THE SOURCE OF TRUTH
# ==========================================================

from app.models import DeliveryReport
from app.database import SessionLocal, check_database_connection
# ==========================================================
# BLOCK 1.1: POSTGRESQL HEALTH ENGINE (ENHANCED v3.0)
# ==========================================================
# ==========================================================
# BLOCK 1.1: POSTGRESQL HEALTH ENGINE (FIXED v4.0)
# ==========================================================

class PostgreSQLHealthEngine:
    """
    Comprehensive PostgreSQL health validation.
    Validates table existence and critical columns only.
    Missing data in optional columns generates warnings, NOT errors.
    """
    
    @staticmethod
    def validate_database() -> Dict[str, Any]:
        """
        Perform complete database health check.
        Returns detailed validation results with severity levels.
        """
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
            
            # Get database version
            try:
                version = db.execute(text("SELECT version()")).scalar()
                result["database_version"] = version.split()[1] if version else "Unknown"
                logger.info(f"📌 PostgreSQL Version: {result['database_version']}")
            except Exception as e:
                result["warnings"].append(f"Could not get database version: {str(e)}")
            
            # Check table exists
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
                return result
            
            if total_records == 0:
                result["warnings"].append("No records found in delivery_reports table")
                result["status"] = "warning"
                db.close()
                return result
            
            # ==========================================================
            # CRITICAL COLUMNS - Service fails if these are missing
            # ==========================================================
            critical_columns = [
                "dn_no", "customer_name", "warehouse", "ship_to_city",
                "dn_qty", "dn_amount"
            ]
            
            for col in critical_columns:
                try:
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
                        result["warnings"].append(f"Critical column '{col}' has all NULL values")
                except Exception as e:
                    result["critical_columns"][col] = {
                        "exists": False,
                        "non_null_count": 0,
                        "has_data": False
                    }
                    result["errors"].append(f"Critical column '{col}' missing: {str(e)}")
                    logger.error(f"❌ Critical column '{col}' missing: {str(e)}")
            
            # ==========================================================
            # OPTIONAL COLUMNS - Warnings only, NOT errors (FIXED)
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
                        result["warnings"].append(f"Optional column '{col}' has all NULL values")
                        logger.warning(f"⚠️ Optional column '{col}' has all NULL values")
                except Exception as e:
                    result["optional_columns"][col] = {
                        "exists": False,
                        "non_null_count": 0,
                        "has_data": False
                    }
                    result["warnings"].append(f"Optional column '{col}' missing: {str(e)}")
                    logger.warning(f"⚠️ Optional column '{col}' missing: {str(e)}")
            
            # ==========================================================
            # STATISTICS - FIXED: Use 'distinct' with correct import
            # ==========================================================
            try:
                result["statistics"] = {
                    "total_records": total_records,
                    "total_dns": db.query(func.count(distinct(DeliveryReport.dn_no))).scalar() or 0,
                    "total_dealers": db.query(func.count(distinct(DeliveryReport.customer_name))).scalar() or 0,
                    "total_warehouses": db.query(func.count(distinct(DeliveryReport.warehouse))).scalar() or 0,
                    "total_cities": db.query(func.count(distinct(DeliveryReport.ship_to_city))).scalar() or 0,
                    "total_products": db.query(func.count(distinct(DeliveryReport.customer_model))).scalar() or 0
                }
                logger.info(f"📊 Statistics: {result['statistics']}")
            except Exception as e:
                result["warnings"].append(f"Statistics query failed: {str(e)}")
                logger.warning(f"⚠️ Statistics query failed: {str(e)}")
            
            db.close()
            
            # ==========================================================
            # DETERMINE STATUS
            # ==========================================================
            critical_errors = len([e for e in result["errors"] if "critical" in e.lower()])
            critical_missing = len([c for c in result["critical_columns"].values() if not c.get("exists", False)])
            
            if not result["connected"] or not result["table_exists"]:
                result["status"] = "critical"
            elif critical_errors > 0 or critical_missing > 0:
                result["status"] = "critical"
            elif result["record_count"] == 0:
                result["status"] = "warning"
            elif len(result["warnings"]) > 5:
                result["status"] = "degraded"
            else:
                result["status"] = "healthy"
            
            logger.info(f"✅ Database Health Status: {result['status'].upper()}")
            
            return result
            
        except Exception as e:
            result["errors"].append(f"Database connection failed: {str(e)}")
            result["status"] = "critical"
            logger.error(f"❌ Database Health Check Failed: {str(e)}")
            return result
    
    @staticmethod
    def get_health_report() -> Dict[str, Any]:
        """Get detailed health report."""
        return PostgreSQLHealthEngine.validate_database()



# ==========================================================
# BLOCK 2: CONSTANTS
# ==========================================================

CACHE_TTL_SECONDS = 300
QUERY_TIMEOUT_SECONDS = 10
MAX_RETRY_ATTEMPTS = 3
MAX_RESPONSE_LENGTH = 2500
SEARCH_LIMIT = 20
TOP_LIMIT = 10

# ==========================================================
# BLOCK 3: RESPONSE CONTRACT
# ==========================================================

class AnalyticsResponse:
    """
    Standard response contract for all analytics endpoints.
    Compatible with ai_provider_service.py validation logic.
    """
    
    def __init__(self, success: bool = True, data: Dict[str, Any] = None, 
                 error: str = None, error_id: str = None, metadata: Dict[str, Any] = None):
        self.success = success
        self.data = data or {}
        self.error = error
        self.error_id = error_id or str(uuid.uuid4())[:8]
        self.metadata = metadata or {}
        self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "error_id": self.error_id,
            "metadata": self.metadata,
            "timestamp": self.timestamp
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())

# ==========================================================
# BLOCK 4: DATE VALIDATION ENGINE
# ==========================================================

class DateValidator:
    """
    PRODUCTION DATE VALIDATION ENGINE.
    
    CRITICAL BUSINESS RULE:
    ALL dates are interpreted as YEAR-DATE-MONTH (YYYY-DD-MM)
    """
    
    @staticmethod
    @lru_cache(maxsize=1024)
    def parse_business_date(raw_value: Any) -> Optional[datetime]:
        """Parse date using intelligent format detection."""
        if raw_value is None:
            return None
        
        if isinstance(raw_value, datetime):
            return raw_value
        
        raw_str = str(raw_value).strip()
        if not raw_str:
            return None
        
        try:
            parts = raw_str.split("-")
            if len(parts) != 3:
                return None
            
            year = int(parts[0])
            pos2 = int(parts[1])
            pos3 = int(parts[2])
            
            if not (1900 <= year <= 2100):
                return None
            
            # Try YYYY-DD-MM first
            if 1 <= pos3 <= 12 and 1 <= pos2 <= 31:
                try:
                    return datetime(year, pos3, pos2)
                except ValueError:
                    pass
            
            # Try YYYY-MM-DD (auto-swap)
            if 1 <= pos2 <= 12 and 1 <= pos3 <= 31:
                try:
                    return datetime(year, pos2, pos3)
                except ValueError:
                    pass
            
            return None
            
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def calculate_aging(
        create_date: Optional[datetime],
        pgi_date: Optional[datetime],
        pod_date: Optional[datetime]
    ) -> Dict[str, Any]:
        """Calculate aging metrics."""
        create_date = DateValidator.parse_business_date(create_date)
        pgi_date = DateValidator.parse_business_date(pgi_date)
        pod_date = DateValidator.parse_business_date(pod_date)
        
        today = datetime.now().date()
        
        result = {
            "delivery_aging": None,
            "pod_aging": None,
            "total_cycle": None,
            "delivery_aging_text": "N/A",
            "pod_aging_text": "N/A",
            "total_cycle_text": "N/A",
            "pgi_completed": False,
            "pod_received": False,
            "delivery_completed": False
        }
        
        # Calculate Delivery Aging
        if create_date and pgi_date:
            delivery_aging = max(0, (pgi_date.date() - create_date.date()).days)
            result["delivery_aging"] = delivery_aging
            result["delivery_aging_text"] = DateValidator._format_aging(delivery_aging)
            result["pgi_completed"] = True
        elif create_date and not pgi_date:
            delivery_aging = max(0, (today - create_date.date()).days)
            result["delivery_aging"] = delivery_aging
            result["delivery_aging_text"] = f"{DateValidator._format_aging(delivery_aging)} (Pending PGI)"
        
        # Calculate POD Aging
        if pgi_date and pod_date:
            pod_aging = max(0, (pod_date.date() - pgi_date.date()).days)
            result["pod_aging"] = pod_aging
            result["pod_aging_text"] = DateValidator._format_aging(pod_aging)
            result["pod_received"] = True
        elif pgi_date and not pod_date:
            pod_aging = max(0, (today - pgi_date.date()).days)
            result["pod_aging"] = pod_aging
            result["pod_aging_text"] = f"{DateValidator._format_aging(pod_aging)} (Pending POD)"
        
        # Calculate Total Cycle
        if create_date and pod_date:
            total_cycle = max(0, (pod_date.date() - create_date.date()).days)
            result["total_cycle"] = total_cycle
            result["total_cycle_text"] = DateValidator._format_aging(total_cycle)
            result["delivery_completed"] = True
        elif create_date and not pod_date:
            if pgi_date:
                result["total_cycle_text"] = "In Progress (POD Pending)"
            else:
                result["total_cycle_text"] = "In Progress (PGI Pending)"
        
        return result
    
    @staticmethod
    def _format_aging(days: int) -> str:
        """Format aging for display."""
        if days is None:
            return "N/A"
        if days == 0:
            return "Same Day"
        elif days == 1:
            return "1 Day"
        else:
            return f"{days} Days"

# ==========================================================
# BLOCK 5: KPI ENGINE
# ==========================================================

class KPIEngine:
    """Business KPI calculation engine - PostgreSQL only."""
    
    @staticmethod
    def calculate_delivery_rate(delivered_dns: int, total_dns: int) -> float:
        if total_dns == 0:
            return 0.0
        return round((delivered_dns / total_dns) * 100, 1)
    
    @staticmethod
    def calculate_pgi_rate(pgi_completed: int, in_transit: int, total_dns: int) -> float:
        if total_dns == 0:
            return 0.0
        return round(((pgi_completed + in_transit) / total_dns) * 100, 1)
    
    @staticmethod
    def calculate_pod_rate(pod_completed: int, delivered_dns: int) -> float:
        if delivered_dns == 0:
            return 0.0
        return round((pod_completed / delivered_dns) * 100, 1)
    
    @staticmethod
    def calculate_health_score(metrics: Dict[str, float]) -> int:
        delivery_rate = metrics.get("delivery_rate", 0)
        pod_rate = metrics.get("pod_rate", 0)
        avg_aging = metrics.get("avg_aging", 0)
        revenue = metrics.get("revenue", 0)
        
        score = (
            (delivery_rate / 100 * 40) +
            (pod_rate / 100 * 30) +
            ((100 - min(avg_aging / 30 * 100, 100)) / 100 * 20) +
            (min(revenue / 1000000 * 100, 100) / 100 * 10)
        )
        return min(int(score), 100)
    
    @staticmethod
    def calculate_risk_level(delivery_rate: float, pod_rate: float, avg_aging: float) -> Tuple[str, int]:
        delivery_risk = 0 if delivery_rate >= 90 else 50 if delivery_rate >= 70 else 100
        pod_risk = 0 if pod_rate >= 90 else 50 if pod_rate >= 70 else 100
        aging_risk = 0 if avg_aging <= 3 else 50 if avg_aging <= 7 else 100
        
        risk_score = (delivery_risk + pod_risk + aging_risk) // 3
        
        if risk_score <= 25:
            return "Low", risk_score
        elif risk_score <= 50:
            return "Medium", risk_score
        elif risk_score <= 75:
            return "High", risk_score
        else:
            return "Critical", risk_score

# ==========================================================
# BLOCK 6: CACHE ENGINE
# ==========================================================

class CacheEngine:
    """
    Centralized caching for analytics service.
    TTL: 300 seconds (5 minutes)
    """
    
    def __init__(self):
        self.dn_cache = TTLCache(maxsize=2000, ttl=300)
        self.dealer_cache = TTLCache(maxsize=2000, ttl=300)
        self.warehouse_cache = TTLCache(maxsize=2000, ttl=300)
        self.city_cache = TTLCache(maxsize=2000, ttl=300)
        self.product_cache = TTLCache(maxsize=2000, ttl=300)
        self.dashboard_cache = TTLCache(maxsize=1000, ttl=300)
        
        self.stats = {
            "hits": 0,
            "misses": 0,
            "total_queries": 0
        }
    
    def get(self, cache_name: str, key: str) -> Optional[Any]:
        """Get from cache."""
        self.stats["total_queries"] += 1
        cache = getattr(self, cache_name, None)
        if cache and key in cache:
            self.stats["hits"] += 1
            return cache[key]
        self.stats["misses"] += 1
        return None
    
    def set(self, cache_name: str, key: str, value: Any):
        """Set in cache."""
        cache = getattr(self, cache_name, None)
        if cache:
            cache[key] = value
    
    def clear(self, cache_name: str = None):
        """Clear cache."""
        if cache_name:
            cache = getattr(self, cache_name, None)
            if cache:
                cache.clear()
        else:
            for cache in [self.dn_cache, self.dealer_cache, self.warehouse_cache, 
                          self.city_cache, self.product_cache, self.dashboard_cache]:
                cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self.stats["hits"] + self.stats["misses"]
        return {
            "hits": self.stats["hits"],
            "misses": self.stats["misses"],
            "total_queries": self.stats["total_queries"],
            "hit_ratio": round(self.stats["hits"] / total * 100, 1) if total > 0 else 0,
            "cache_sizes": {
                "dn": len(self.dn_cache),
                "dealer": len(self.dealer_cache),
                "warehouse": len(self.warehouse_cache),
                "city": len(self.city_cache),
                "product": len(self.product_cache),
                "dashboard": len(self.dashboard_cache)
            }
        }

# ==========================================================
# BLOCK 7: SEARCH ENGINE (ENHANCED)
# ==========================================================

class SearchEngine:
    """Universal PostgreSQL Search Engine with confidence ranking."""
    
    def __init__(self, db: Session, cache: CacheEngine = None):
        self.db = db
        self.cache = cache or CacheEngine()
    
    def search_dn(self, query: str, exact: bool = False) -> List[Dict[str, Any]]:
        """Search for DNs in PostgreSQL."""
        if not query or not query.strip():
            return []
        
        # Normalize DN
        query_clean = re.sub(r'[^0-9]', '', str(query).strip())
        if len(query_clean) < 8 or len(query_clean) > 12:
            return []
        
        try:
            if exact:
                results = self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == query_clean
                ).limit(SEARCH_LIMIT).all()
            else:
                results = self.db.query(DeliveryReport).filter(
                    DeliveryReport.dn_no.like(f"%{query_clean}%")
                ).limit(SEARCH_LIMIT).all()
            
            return [{
                "dn_no": r.dn_no,
                "customer_name": r.customer_name,
                "warehouse": r.warehouse,
                "ship_to_city": r.ship_to_city,
                "dn_amount": r.dn_amount,
                "dn_qty": r.dn_qty,
                "delivery_status": r.delivery_status
            } for r in results]
            
        except Exception as e:
            logger.error(f"DN search error: {e}")
            return []
    
    def search_dealer(self, query: str, exact: bool = False) -> List[Dict[str, Any]]:
        """Search for dealers in PostgreSQL with confidence scoring."""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = []
            
            # STRATEGY 1: Exact match (highest confidence)
            if exact:
                results = self.db.query(
                    func.distinct(DeliveryReport.customer_name)
                ).filter(
                    func.lower(DeliveryReport.customer_name) == func.lower(query_clean)
                ).limit(SEARCH_LIMIT).all()
            else:
                # STRATEGY 2: ILIKE match
                results = self.db.query(
                    func.distinct(DeliveryReport.customer_name)
                ).filter(
                    DeliveryReport.customer_name.ilike(f"%{query_clean}%")
                ).limit(SEARCH_LIMIT).all()
            
            if not results and not exact:
                # STRATEGY 3: Token match
                tokens = query_clean.split()
                for token in tokens:
                    if len(token) > 2:
                        token_results = self.db.query(
                            func.distinct(DeliveryReport.customer_name)
                        ).filter(
                            DeliveryReport.customer_name.ilike(f"%{token}%")
                        ).limit(SEARCH_LIMIT).all()
                        if token_results:
                            results = token_results
                            break
            
            return [{"dealer_name": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"Dealer search error: {e}")
            return []
    
    def search_warehouse(self, query: str) -> List[Dict[str, Any]]:
        """Search for warehouses in PostgreSQL."""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = self.db.query(
                func.distinct(DeliveryReport.warehouse)
            ).filter(
                DeliveryReport.warehouse.ilike(f"%{query_clean}%")
            ).filter(
                DeliveryReport.warehouse.isnot(None),
                DeliveryReport.warehouse != ''
            ).limit(SEARCH_LIMIT).all()
            
            return [{"warehouse": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"Warehouse search error: {e}")
            return []
    
    def search_city(self, query: str) -> List[Dict[str, Any]]:
        """Search for cities in PostgreSQL."""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = self.db.query(
                func.distinct(DeliveryReport.ship_to_city)
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{query_clean}%")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None),
                DeliveryReport.ship_to_city != ''
            ).limit(SEARCH_LIMIT).all()
            
            return [{"city": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"City search error: {e}")
            return []
    
    def search_product(self, query: str) -> List[Dict[str, Any]]:
        """Search for products in PostgreSQL."""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = self.db.query(
                func.distinct(DeliveryReport.customer_model)
            ).filter(
                DeliveryReport.customer_model.ilike(f"%{query_clean}%")
            ).filter(
                DeliveryReport.customer_model.isnot(None),
                DeliveryReport.customer_model != ''
            ).limit(SEARCH_LIMIT).all()
            
            if not results:
                results = self.db.query(
                    func.distinct(DeliveryReport.material_no)
                ).filter(
                    DeliveryReport.material_no.ilike(f"%{query_clean}%")
                ).filter(
                    DeliveryReport.material_no.isnot(None),
                    DeliveryReport.material_no != ''
                ).limit(SEARCH_LIMIT).all()
                return [{"product": r[0]} for r in results if r[0]]
            
            return [{"product": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"Product search error: {e}")
            return []
    
    def verify_dn_exists(self, dn_no: str) -> Dict[str, Any]:
        """Verify if a DN exists in PostgreSQL."""
        # Normalize DN
        query_clean = re.sub(r'[^0-9]', '', str(dn_no).strip())
        if len(query_clean) < 8 or len(query_clean) > 12:
            return {"dn": dn_no, "found": False, "error": "Invalid DN format"}
        
        try:
            exists = self.db.query(
                self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == query_clean
                ).exists()
            ).scalar()
            
            return {"dn": dn_no, "found": exists, "normalized": query_clean}
            
        except Exception as e:
            logger.error(f"DN verification error: {e}")
            return {"dn": dn_no, "found": False, "error": str(e)}
    
    def verify_dealer_exists(self, dealer_name: str) -> bool:
        """Verify if a dealer exists in PostgreSQL."""
        if not dealer_name or not dealer_name.strip():
            return False
        
        try:
            exists = self.db.query(
                self.db.query(DeliveryReport).filter(
                    func.lower(DeliveryReport.customer_name) == func.lower(dealer_name.strip())
                ).exists()
            ).scalar()
            return exists
        except Exception as e:
            logger.error(f"Dealer verification error: {e}")
            return False

# ==========================================================
# BLOCK 8: ENTITY RESOLVER (ENHANCED)
# ==========================================================

class EntityResolver:
    """
    Entity resolution engine using PostgreSQL.
    Enhanced with priority ordering and confidence scoring.
    
    RESOLUTION PRIORITY:
    1. DN (8-12 digits)
    2. Warehouse (explicit keywords)
    3. City (explicit keywords)  
    4. Product (product keywords)
    5. Dealer (last priority)
    """
    
    def __init__(self, db: Session, cache: CacheEngine = None):
        self.db = db
        self.cache = cache or CacheEngine()
        
        # Product keywords for detection
        self.PRODUCT_KEYWORDS = [
            'refrigerator', 'fridge', 'freezer', 'ac', 'air conditioner',
            'washing machine', 'washer', 'led', 'tv', 'television',
            'microwave', 'oven', 'water dispenser', 'cooler', 'heater'
        ]
        
        # Warehouse keywords for detection
        self.WAREHOUSE_KEYWORDS = ['warehouse', 'wh', 'depot', 'godown', 'distribution center']
        
        # City keywords for detection
        self.CITY_KEYWORDS = ['city', 'town', 'district', 'region']
    
    # ==========================================================
    # DN NORMALIZATION ENGINE
    # ==========================================================
    
    def _normalize_dn(self, dn_input: str) -> str:
        """
        Normalize DN number for database lookup.
        
        Supports:
        - 6243684514
        - 6243684514.0
        - 6243684514.00
        -  6243684514
        - "6243684514"
        - 6243684514
        """
        if not dn_input:
            return ""
        
        raw = str(dn_input).strip()
        # Remove quotes if present
        raw = raw.strip('"').strip("'")
        # Remove decimal if present
        if '.' in raw:
            raw = raw.split('.')[0]
        # Remove any non-numeric characters
        return re.sub(r'[^0-9]', '', raw)
    
    def resolve_dn(self, dn_input: str) -> Dict[str, Any]:
        """
        Resolve DN number with hardened normalization.
        """
        if not dn_input or not dn_input.strip():
            return {
                "entity": None,
                "entity_type": "dn",
                "found": False,
                "confidence": 0,
                "resolution_method": "none"
            }
        
        normalized = self._normalize_dn(dn_input)
        
        # Validate length (8-12 digits)
        if len(normalized) < 8 or len(normalized) > 12:
            return {
                "entity": None,
                "entity_type": "dn",
                "found": False,
                "confidence": 0,
                "resolution_method": "invalid_length",
                "normalized": normalized
            }
        
        # Check cache
        cache_key = f"dn:{normalized}"
        cached = self.cache.get("dn_cache", cache_key) if self.cache else None
        if cached:
            logger.info(f"✅ DN cache hit: {normalized}")
            return cached
        
        try:
            # Query with cast to handle different data types
            result = self.db.query(DeliveryReport.dn_no).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
            
            if result:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "dn",
                    "found": True,
                    "confidence": 1.0,
                    "resolution_method": "exact_match",
                    "normalized": normalized
                }
                if self.cache:
                    self.cache.set("dn_cache", cache_key, response)
                logger.info(f"✅ DN resolved: {resolved} (confidence: 1.0)")
                return response
            
            # Try partial match for suggestions
            try:
                similar = self.db.query(DeliveryReport.dn_no).filter(
                    DeliveryReport.dn_no.like(f"%{normalized}%")
                ).limit(5).all()
                
                if similar:
                    suggestions = [s[0] for s in similar]
                    response = {
                        "entity": None,
                        "entity_type": "dn",
                        "found": False,
                        "confidence": 0.3,
                        "resolution_method": "partial_match",
                        "normalized": normalized,
                        "suggestions": suggestions[:3]
                    }
                    return response
            except Exception as e:
                logger.warning(f"Similar DN search failed: {e}")
            
            response = {
                "entity": None,
                "entity_type": "dn",
                "found": False,
                "confidence": 0,
                "resolution_method": "not_found",
                "normalized": normalized
            }
            return response
            
        except Exception as e:
            logger.error(f"DN resolution error: {e}")
            return {
                "entity": None,
                "entity_type": "dn",
                "found": False,
                "confidence": 0,
                "resolution_method": "error",
                "error": str(e)
            }
    
    def resolve_dealer(self, dealer_input: str) -> Dict[str, Any]:
        """
        Resolve dealer name with multiple strategies.
        Returns structured result with confidence.
        """
        if not dealer_input or not dealer_input.strip():
            return {
                "entity": None,
                "entity_type": "dealer",
                "found": False,
                "confidence": 0,
                "resolution_method": "none"
            }
        
        dealer_input = dealer_input.strip()
        
        # Check cache
        cache_key = f"dealer:{dealer_input.lower()}"
        cached = self.cache.get("dealer_cache", cache_key) if self.cache else None
        if cached:
            logger.info(f"✅ Dealer cache hit: {dealer_input}")
            return cached
        
        try:
            # STRATEGY 1: Exact match
            result = self.db.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)
            ).first()
            if result:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "dealer",
                    "found": True,
                    "confidence": 1.0,
                    "resolution_method": "exact_match"
                }
                if self.cache:
                    self.cache.set("dealer_cache", cache_key, response)
                logger.info(f"✅ Dealer resolved (exact): {resolved} (confidence: 1.0)")
                return response
            
            # STRATEGY 2: ILIKE match
            result = self.db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_input}%")
            ).first()
            if result:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "dealer",
                    "found": True,
                    "confidence": 0.85,
                    "resolution_method": "ilike_match"
                }
                if self.cache:
                    self.cache.set("dealer_cache", cache_key, response)
                logger.info(f"✅ Dealer resolved (ILIKE): {resolved} (confidence: 0.85)")
                return response
            
            # STRATEGY 3: Token-based matching
            tokens = dealer_input.split()
            for token in tokens:
                if len(token) > 2 and token.lower() not in ['the', 'and', 'for', 'with']:
                    result = self.db.query(DeliveryReport.customer_name).filter(
                        DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        response = {
                            "entity": resolved,
                            "entity_type": "dealer",
                            "found": True,
                            "confidence": 0.75,
                            "resolution_method": f"token_match_{token}",
                            "matched_token": token
                        }
                        if self.cache:
                            self.cache.set("dealer_cache", cache_key, response)
                        logger.info(f"✅ Dealer resolved (token '{token}'): {resolved} (confidence: 0.75)")
                        return response
            
            response = {
                "entity": None,
                "entity_type": "dealer",
                "found": False,
                "confidence": 0,
                "resolution_method": "not_found"
            }
            if self.cache:
                self.cache.set("dealer_cache", cache_key, response)
            return response
            
        except Exception as e:
            logger.error(f"Dealer resolution error: {e}")
            return {
                "entity": None,
                "entity_type": "dealer",
                "found": False,
                "confidence": 0,
                "resolution_method": "error",
                "error": str(e)
            }
    
    def resolve_warehouse(self, warehouse_input: str) -> Dict[str, Any]:
        """
        Resolve warehouse name.
        Never routes to dealer.
        """
        if not warehouse_input or not warehouse_input.strip():
            return {
                "entity": None,
                "entity_type": "warehouse",
                "found": False,
                "confidence": 0,
                "resolution_method": "none"
            }
        
        warehouse_input = warehouse_input.strip()
        
        # Check cache
        cache_key = f"warehouse:{warehouse_input.lower()}"
        cached = self.cache.get("warehouse_cache", cache_key) if self.cache else None
        if cached:
            logger.info(f"✅ Warehouse cache hit: {warehouse_input}")
            return cached
        
        try:
            # Clean the input - remove keywords
            clean_input = warehouse_input
            for kw in ['warehouse', 'wh', 'depot', 'godown']:
                clean_input = re.sub(rf'\b{kw}\b', '', clean_input, flags=re.IGNORECASE)
            clean_input = clean_input.strip()
            
            if not clean_input:
                clean_input = warehouse_input
            
            # Try exact match
            result = self.db.query(DeliveryReport.warehouse).filter(
                func.lower(DeliveryReport.warehouse) == func.lower(clean_input)
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "warehouse",
                    "found": True,
                    "confidence": 1.0,
                    "resolution_method": "exact_match"
                }
                if self.cache:
                    self.cache.set("warehouse_cache", cache_key, response)
                logger.info(f"✅ Warehouse resolved: {resolved} (confidence: 1.0)")
                return response
            
            # Try ILIKE
            result = self.db.query(DeliveryReport.warehouse).filter(
                DeliveryReport.warehouse.ilike(f"%{clean_input}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "warehouse",
                    "found": True,
                    "confidence": 0.85,
                    "resolution_method": "ilike_match"
                }
                if self.cache:
                    self.cache.set("warehouse_cache", cache_key, response)
                logger.info(f"✅ Warehouse resolved (ILIKE): {resolved} (confidence: 0.85)")
                return response
            
            response = {
                "entity": None,
                "entity_type": "warehouse",
                "found": False,
                "confidence": 0,
                "resolution_method": "not_found"
            }
            if self.cache:
                self.cache.set("warehouse_cache", cache_key, response)
            return response
            
        except Exception as e:
            logger.error(f"Warehouse resolution error: {e}")
            return {
                "entity": None,
                "entity_type": "warehouse",
                "found": False,
                "confidence": 0,
                "resolution_method": "error",
                "error": str(e)
            }
    
    def resolve_city(self, city_input: str) -> Dict[str, Any]:
        """
        Resolve city name.
        Never routes to dealer.
        """
        if not city_input or not city_input.strip():
            return {
                "entity": None,
                "entity_type": "city",
                "found": False,
                "confidence": 0,
                "resolution_method": "none"
            }
        
        city_input = city_input.strip()
        
        # Check cache
        cache_key = f"city:{city_input.lower()}"
        cached = self.cache.get("city_cache", cache_key) if self.cache else None
        if cached:
            logger.info(f"✅ City cache hit: {city_input}")
            return cached
        
        try:
            # Clean the input - remove keywords
            clean_input = city_input
            for kw in ['city', 'town', 'district', 'region']:
                clean_input = re.sub(rf'\b{kw}\b', '', clean_input, flags=re.IGNORECASE)
            clean_input = clean_input.strip()
            
            if not clean_input:
                clean_input = city_input
            
            # Try exact match
            result = self.db.query(DeliveryReport.ship_to_city).filter(
                func.lower(DeliveryReport.ship_to_city) == func.lower(clean_input)
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "city",
                    "found": True,
                    "confidence": 1.0,
                    "resolution_method": "exact_match"
                }
                if self.cache:
                    self.cache.set("city_cache", cache_key, response)
                logger.info(f"✅ City resolved: {resolved} (confidence: 1.0)")
                return response
            
            # Try ILIKE
            result = self.db.query(DeliveryReport.ship_to_city).filter(
                DeliveryReport.ship_to_city.ilike(f"%{clean_input}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "city",
                    "found": True,
                    "confidence": 0.85,
                    "resolution_method": "ilike_match"
                }
                if self.cache:
                    self.cache.set("city_cache", cache_key, response)
                logger.info(f"✅ City resolved (ILIKE): {resolved} (confidence: 0.85)")
                return response
            
            response = {
                "entity": None,
                "entity_type": "city",
                "found": False,
                "confidence": 0,
                "resolution_method": "not_found"
            }
            if self.cache:
                self.cache.set("city_cache", cache_key, response)
            return response
            
        except Exception as e:
            logger.error(f"City resolution error: {e}")
            return {
                "entity": None,
                "entity_type": "city",
                "found": False,
                "confidence": 0,
                "resolution_method": "error",
                "error": str(e)
            }
    
    def resolve_product(self, product_input: str) -> Dict[str, Any]:
        """
        Resolve product name.
        Never routes to dealer.
        """
        if not product_input or not product_input.strip():
            return {
                "entity": None,
                "entity_type": "product",
                "found": False,
                "confidence": 0,
                "resolution_method": "none"
            }
        
        product_input = product_input.strip()
        
        # Check cache
        cache_key = f"product:{product_input.lower()}"
        cached = self.cache.get("product_cache", cache_key) if self.cache else None
        if cached:
            logger.info(f"✅ Product cache hit: {product_input}")
            return cached
        
        try:
            # Try customer_model first
            result = self.db.query(DeliveryReport.customer_model).filter(
                func.lower(DeliveryReport.customer_model) == func.lower(product_input)
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "product",
                    "found": True,
                    "confidence": 1.0,
                    "resolution_method": "exact_match_model"
                }
                if self.cache:
                    self.cache.set("product_cache", cache_key, response)
                logger.info(f"✅ Product resolved (model): {resolved} (confidence: 1.0)")
                return response
            
            # Try material_no
            result = self.db.query(DeliveryReport.material_no).filter(
                func.lower(DeliveryReport.material_no) == func.lower(product_input)
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "product",
                    "found": True,
                    "confidence": 1.0,
                    "resolution_method": "exact_match_material"
                }
                if self.cache:
                    self.cache.set("product_cache", cache_key, response)
                logger.info(f"✅ Product resolved (material): {resolved} (confidence: 1.0)")
                return response
            
            # Try ILIKE on customer_model
            result = self.db.query(DeliveryReport.customer_model).filter(
                DeliveryReport.customer_model.ilike(f"%{product_input}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                response = {
                    "entity": resolved,
                    "entity_type": "product",
                    "found": True,
                    "confidence": 0.85,
                    "resolution_method": "ilike_match_model"
                }
                if self.cache:
                    self.cache.set("product_cache", cache_key, response)
                logger.info(f"✅ Product resolved (ILIKE): {resolved} (confidence: 0.85)")
                return response
            
            response = {
                "entity": None,
                "entity_type": "product",
                "found": False,
                "confidence": 0,
                "resolution_method": "not_found"
            }
            if self.cache:
                self.cache.set("product_cache", cache_key, response)
            return response
            
        except Exception as e:
            logger.error(f"Product resolution error: {e}")
            return {
                "entity": None,
                "entity_type": "product",
                "found": False,
                "confidence": 0,
                "resolution_method": "error",
                "error": str(e)
            }
    
    def resolve_entity(self, query: str) -> Dict[str, Any]:
        """
        Universal entity resolver with strict priority ordering.
        
        PRIORITY ORDER:
        1. DN (8-12 digits)
        2. Warehouse (explicit keywords)
        3. City (explicit keywords)
        4. Product (product keywords)
        5. Dealer (last priority)
        """
        if not query or not query.strip():
            return {
                "entity": None,
                "entity_type": "unknown",
                "found": False,
                "confidence": 0,
                "resolution_method": "none"
            }
        
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
        if any(kw in query_clean.lower() for kw in self.WAREHOUSE_KEYWORDS):
            result = self.resolve_warehouse(query_clean)
            if result.get('found'):
                logger.info(f"✅ Entity resolved as Warehouse: {result.get('entity')}")
                return result
        
        # ==========================================================
        # PRIORITY 3: City Resolution
        # ==========================================================
        if any(kw in query_clean.lower() for kw in self.CITY_KEYWORDS):
            result = self.resolve_city(query_clean)
            if result.get('found'):
                logger.info(f"✅ Entity resolved as City: {result.get('entity')}")
                return result
        
        # ==========================================================
        # PRIORITY 4: Product Resolution
        # ==========================================================
        if any(kw in query_clean.lower() for kw in self.PRODUCT_KEYWORDS):
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
        
        logger.warning(f"❌ No entity resolved for: '{query_clean}'")
        return {
            "entity": None,
            "entity_type": "unknown",
            "found": False,
            "confidence": 0,
            "resolution_method": "none"
        }

# ==========================================================
# BLOCK 8.5: DISTANCE SERVICE INTEGRATION
# ==========================================================

class DistanceIntegration:
    """
    Lazy integration with DistanceService.
    Never allows distance service failures to break analytics.
    """
    
    def __init__(self):
        self._distance_service = None
        self._initialized = False
        self._last_error = None
    
    def _get_distance_service(self):
        """Lazy load DistanceService."""
        if self._initialized:
            return self._distance_service
        
        try:
            from app.services.distance_service import get_distance_service
            self._distance_service = get_distance_service()
            self._initialized = True
            logger.info("✅ DistanceService loaded successfully")
        except ImportError as e:
            self._last_error = f"ImportError: {str(e)}"
            logger.warning(f"⚠️ DistanceService import failed: {e}")
            self._initialized = True
        except Exception as e:
            self._last_error = str(e)
            logger.warning(f"⚠️ DistanceService initialization failed: {e}")
            self._initialized = True
        
        return self._distance_service
    
    def calculate_dealer_distance(self, warehouse: str, city: str) -> Dict[str, Any]:
        """
        Calculate distance from warehouse to dealer city.
        Never fails - returns safe default if distance service unavailable.
        """
        if not warehouse or not city:
            return {
                "distance_km": None,
                "travel_time_hours": None,
                "transit_days": None,
                "available": False,
                "error": "Missing warehouse or city"
            }
        
        service = self._get_distance_service()
        if not service:
            return {
                "distance_km": None,
                "travel_time_hours": None,
                "transit_days": None,
                "available": False,
                "error": "Distance service not available"
            }
        
        try:
            result = service.calculate_warehouse_distance(warehouse, city)
            if result and result.get('success'):
                distance_km = result.get('distance_km', 0)
                travel_hours = result.get('approx_driving_hours', 0)
                travel_minutes = result.get('approx_driving_minutes', 0)
                
                # Calculate transit days
                transit_days = round(travel_hours / 8, 1) if travel_hours > 0 else 0
                
                return {
                    "distance_km": round(distance_km, 1),
                    "travel_time_hours": round(travel_hours, 1),
                    "travel_time_minutes": travel_minutes,
                    "transit_days": transit_days,
                    "available": True,
                    "error": None
                }
            else:
                error = result.get('error', 'Unknown error') if result else 'No result'
                return {
                    "distance_km": None,
                    "travel_time_hours": None,
                    "transit_days": None,
                    "available": False,
                    "error": error
                }
        except Exception as e:
            logger.error(f"Distance calculation error: {e}")
            return {
                "distance_km": None,
                "travel_time_hours": None,
                "transit_days": None,
                "available": False,
                "error": str(e)
            }
    
    def get_warehouse_coverage(self, warehouse: str) -> Dict[str, Any]:
        """
        Get warehouse coverage information.
        Never fails - returns safe default if distance service unavailable.
        """
        if not warehouse:
            return {
                "coverage_radius": None,
                "average_distance": None,
                "cities_served": 0,
                "available": False,
                "error": "Missing warehouse"
            }
        
        service = self._get_distance_service()
        if not service:
            return {
                "coverage_radius": None,
                "average_distance": None,
                "cities_served": 0,
                "available": False,
                "error": "Distance service not available"
            }
        
        try:
            coverage = service.get_warehouse_coverage(warehouse)
            if coverage and coverage.get('success'):
                return {
                    "coverage_radius": coverage.get('max_distance_km', 0),
                    "average_distance": coverage.get('average_distance_km', 0),
                    "cities_served": coverage.get('total_cities', 0),
                    "available": True,
                    "error": None
                }
            else:
                return {
                    "coverage_radius": None,
                    "average_distance": None,
                    "cities_served": 0,
                    "available": False,
                    "error": coverage.get('error', 'Unknown error') if coverage else 'No result'
                }
        except Exception as e:
            logger.error(f"Warehouse coverage error: {e}")
            return {
                "coverage_radius": None,
                "average_distance": None,
                "cities_served": 0,
                "available": False,
                "error": str(e)
            }

# ==========================================================
# BLOCK 8.6: DEALER360 INTEGRATION
# ==========================================================

class Dealer360Integration:
    """
    Lazy integration with Dealer360Dashboard.
    Prevents circular imports.
    """
    
    def __init__(self, db: Session, resolver: EntityResolver, search: SearchEngine):
        self.db = db
        self.resolver = resolver
        self.search = search
        self._dealer_360 = None
        self._initialized = False
        self._last_error = None
    
    def _get_dealer_360(self):
        """Lazy load Dealer360Dashboard."""
        if self._initialized:
            return self._dealer_360
        
        try:
            from app.services.dealer_analytics_service import Dealer360Dashboard
            self._dealer_360 = Dealer360Dashboard(self.db, self.resolver, self.search)
            self._initialized = True
            logger.info("✅ Dealer360Dashboard loaded successfully")
        except ImportError as e:
            self._last_error = f"ImportError: {str(e)}"
            logger.warning(f"⚠️ Dealer360Dashboard import failed: {e}")
            self._initialized = True
        except Exception as e:
            self._last_error = str(e)
            logger.warning(f"⚠️ Dealer360Dashboard initialization failed: {e}")
            self._initialized = True
        
        return self._dealer_360
    
    def get_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get Dealer360 dashboard - safe fallback if service unavailable."""
        if not dealer_name or not dealer_name.strip():
            return {"error": "Dealer name is required"}
        
        service = self._get_dealer_360()
        if not service:
            return {
                "error": "Dealer360 dashboard service not available",
                "error_id": str(uuid.uuid4())[:8]
            }
        
        try:
            result = service.get_dashboard(dealer_name)
            if result and "error" not in result:
                result['_dashboard_type'] = '360'
                return result
            else:
                return result or {"error": "No result from Dealer360 service"}
        except Exception as e:
            logger.error(f"Dealer360 dashboard error: {e}")
            return {
                "error": f"Dealer360 dashboard error: {str(e)[:100]}",
                "error_id": str(uuid.uuid4())[:8]
            }

# ==========================================================
# BLOCK 8.7: SERVICE HEALTH API
# ==========================================================

class ServiceHealthAPI:
    """
    Health API for analytics service.
    Callable from ai_provider_service.py.
    """
    
    @staticmethod
    def get_service_status(analytics_service: 'AnalyticsService') -> Dict[str, Any]:
        """Get comprehensive service status."""
        if not analytics_service:
            return {
                "status": "unavailable",
                "healthy": False,
                "message": "Analytics service not initialized"
            }
        
        try:
            # Check database health
            db_health = PostgreSQLHealthEngine.validate_database()
            
            # Check repository
            repo_healthy = hasattr(analytics_service, 'repo') and analytics_service.repo is not None
            
            # Check methods
            methods = [
                "get_dn_dashboard", "get_dealer_dashboard", "get_warehouse_dashboard",
                "get_city_dashboard", "get_product_dashboard", "search_dn",
                "search_dealer", "verify_dn_exists", "verify_dealer_exists"
            ]
            available_methods = [m for m in methods if hasattr(analytics_service, m)]
            
            return {
                "status": "healthy" if db_health['status'] == 'healthy' and repo_healthy else "degraded",
                "healthy": db_health['status'] == 'healthy' and repo_healthy,
                "database": {
                    "connected": db_health['connected'],
                    "status": db_health['status'],
                    "record_count": db_health['record_count']
                },
                "repository": {
                    "available": repo_healthy
                },
                "methods": {
                    "available": available_methods,
                    "total_required": len(methods),
                    "coverage": round(len(available_methods) / len(methods) * 100, 1)
                },
                "metrics": analytics_service.metrics if hasattr(analytics_service, 'metrics') else {},
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "status": "error",
                "healthy": False,
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    @staticmethod
    def is_service_healthy(analytics_service: 'AnalyticsService') -> bool:
        """Quick health check."""
        status = ServiceHealthAPI.get_service_status(analytics_service)
        return status.get('healthy', False)
    
    @staticmethod
    def get_health_report(analytics_service: 'AnalyticsService') -> Dict[str, Any]:
        """Get detailed health report."""
        return ServiceHealthAPI.get_service_status(analytics_service)

# ==========================================================
# BLOCK 9: ANALYTICS REPOSITORY
# ==========================================================

class AnalyticsRepository:
    """PostgreSQL-driven analytics repository."""
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or SessionLocal()
        self._owned_db = db is None
        
        # Initialize components with cache
        self.cache = CacheEngine()
        self.resolver = EntityResolver(self.db, self.cache)
        self.search = SearchEngine(self.db, self.cache)
        self.distance = DistanceIntegration()
        self.dealer360 = Dealer360Integration(self.db, self.resolver, self.search)
        
        logger.info("✅ AnalyticsRepository initialized with cache and integrations")
        
        # Validate required methods
        required_methods = [
            "get_dn_dashboard", "get_dealer_dashboard", "get_warehouse_dashboard",
            "get_city_dashboard", "get_product_dashboard", "get_pgi_dashboard",
            "get_pod_dashboard", "get_delivery_dashboard", "get_executive_dashboard",
            "get_control_tower_dashboard", "get_revenue_dashboard", "get_ranking_dashboard",
            "get_aging_dashboard"
        ]
        
        missing_methods = []
        for method in required_methods:
            if not hasattr(self, method):
                missing_methods.append(method)
                logger.error(f"❌ Missing method: {method}")
        
        if missing_methods:
            logger.error(f"❌ Missing {len(missing_methods)} required methods")
        else:
            logger.info("✅ AnalyticsRepository initialized with all required methods")
    
    def close(self):
        if self._owned_db and self.db:
            self.db.close()

# ==========================================================
# BLOCK 10: DN DASHBOARD
# ==========================================================

    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """
        Complete DN dashboard - PostgreSQL only.
        """
        import time
        start_time = time.time()
        
        try:
            logger.info(f"📄 Processing DN: '{dn_no}'")
            
            # Normalize DN
            normalized = self.resolver._normalize_dn(dn_no)
            
            if len(normalized) < 8 or len(normalized) > 12:
                return {"error": f"Invalid DN format: {dn_no}. Must be 8-12 digits."}
            
            # Check cache
            cache_key = f"dn_dashboard:{normalized}"
            cached = self.cache.get("dashboard_cache", cache_key)
            if cached:
                logger.info(f"✅ DN dashboard cache hit: {normalized}")
                return cached
            
            # Query the record
            try:
                record = self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == normalized
                ).first()
            except Exception as e:
                logger.error(f"❌ Database query failed: {e}")
                return {"error": f"Database error: {str(e)}"}
            
            if not record:
                # Try to find similar DNs
                suggestions = []
                try:
                    similar = self.db.query(DeliveryReport.dn_no).filter(
                        DeliveryReport.dn_no.like(f"%{normalized[-4:]}%")
                    ).limit(5).all()
                    if similar:
                        suggestions = [s[0] for s in similar]
                except Exception as e:
                    logger.warning(f"Could not find similar DNs: {e}")
                
                if suggestions:
                    return {
                        "error": f"DN {dn_no} not found",
                        "suggestions": suggestions
                    }
                
                return {"error": f"DN {dn_no} not found"}
            
            # Calculate aging
            aging_result = DateValidator.calculate_aging(
                record.dn_create_date,
                record.good_issue_date,
                record.pod_date
            )
            
            # Build response
            response = {
                "dn_number": record.dn_no,
                "customer_name": record.customer_name or "",
                "dealer_code": record.dealer_code or "",
                "customer_code": record.customer_code or "",
                "warehouse": record.warehouse or "",
                "warehouse_code": record.warehouse_code or "",
                "ship_to_city": record.ship_to_city or "",
                "sales_office": record.sales_office or "",
                "sales_manager": record.sales_manager or "",
                "division": record.division or "",
                "customer_model": record.customer_model or "",
                "material_no": record.material_no or "",
                "units": int(record.dn_qty) if record.dn_qty else 0,
                "amount": float(record.dn_amount) if record.dn_amount else 0,
                "dn_create_date": record.dn_create_date.isoformat() if record.dn_create_date else None,
                "good_issue_date": record.good_issue_date.isoformat() if record.good_issue_date else None,
                "pod_date": record.pod_date.isoformat() if record.pod_date else None,
                "delivery_status": record.delivery_status or "Unknown",
                "pgi_status": record.pgi_status or "",
                "pod_status": record.pod_status or "",
                "pending_flag": record.pending_flag if record.pending_flag is not None else False,
                "delivery_aging": aging_result.get("delivery_aging"),
                "pod_aging": aging_result.get("pod_aging"),
                "total_cycle": aging_result.get("total_cycle"),
                "delivery_aging_text": aging_result.get("delivery_aging_text", "N/A"),
                "pod_aging_text": aging_result.get("pod_aging_text", "N/A"),
                "total_cycle_text": aging_result.get("total_cycle_text", "N/A"),
                "pgi_completed": aging_result.get("pgi_completed", False),
                "pod_received": aging_result.get("pod_received", False),
                "delivery_completed": aging_result.get("delivery_completed", False)
            }
            
            # Cache response
            self.cache.set("dashboard_cache", cache_key, response)
            
            total_time = time.time() - start_time
            logger.info(f"✅ DN {normalized} dashboard built in {total_time:.3f}s")
            return response
            
        except Exception as e:
            logger.error(f"❌ Get DN dashboard failed: {e}")
            return {"error": f"Failed to load DN: {str(e)[:100]}"}

# ==========================================================
# BLOCK 11: DEALER DASHBOARD
# ==========================================================

    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """
        Complete dealer dashboard - PostgreSQL only.
        """
        import time
        start_time = time.time()
        
        try:
            logger.info(f"🔍 Searching for dealer: '{dealer_name}'")
            
            if not dealer_name or not dealer_name.strip():
                return {"error": "Dealer name is required"}
            
            # Check cache
            cache_key = f"dealer_dashboard:{dealer_name.lower()}"
            cached = self.cache.get("dashboard_cache", cache_key)
            if cached:
                logger.info(f"✅ Dealer dashboard cache hit: {dealer_name}")
                return cached
            
            # Resolve dealer
            resolved_result = self.resolver.resolve_dealer(dealer_name)
            
            if not resolved_result.get('found'):
                similar = self.search.search_dealer(dealer_name, exact=False)
                if similar:
                    suggestions = [s['dealer_name'] for s in similar[:5]]
                    return {
                        "error": f"Dealer '{dealer_name}' not found",
                        "suggestions": suggestions
                    }
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            resolved = resolved_result.get('entity')
            
            # Get dealer profile
            profile_result = self.db.query(
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.division,
                DeliveryReport.warehouse,
                DeliveryReport.ship_to_city,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(resolved)
            ).order_by(
                DeliveryReport.dn_create_date.desc()
            ).first()
            
            # Query metrics
            metrics_result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("transit_dns"),
                func.count(distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns")
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(resolved)
            ).first()
            
            if not metrics_result or metrics_result.total_dns == 0:
                return {"error": f"No data found for dealer '{resolved}'"}
            
            total_dns = metrics_result.total_dns or 0
            delivered_dns = metrics_result.delivered_dns or 0
            transit_dns = metrics_result.transit_dns or 0
            pod_completed = metrics_result.pod_completed_dns or 0
            
            delivery_rate = KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            pgi_rate = KPIEngine.calculate_pgi_rate(delivered_dns, transit_dns, total_dns)
            pod_rate = KPIEngine.calculate_pod_rate(pod_completed, delivered_dns) if delivered_dns > 0 else 0
            
            risk_level, risk_score = KPIEngine.calculate_risk_level(delivery_rate, pod_rate, 0)
            
            response = {
                "dealer_name": resolved,
                "dealer_code": profile_result[0] if profile_result and profile_result[0] else "",
                "customer_code": profile_result[1] if profile_result and profile_result[1] else "",
                "division": profile_result[2] if profile_result and profile_result[2] else "",
                "warehouse": profile_result[3] if profile_result and profile_result[3] else "",
                "city": profile_result[4] if profile_result and profile_result[4] else "",
                "sales_office": profile_result[5] if profile_result and profile_result[5] else "",
                "sales_manager": profile_result[6] if profile_result and profile_result[6] else "",
                "total_dns": total_dns,
                "total_units": int(metrics_result.total_units or 0),
                "total_revenue": float(metrics_result.total_revenue or 0),
                "delivered_dns": delivered_dns,
                "pending_dns": metrics_result.pending_dns or 0,
                "transit_dns": transit_dns,
                "pod_completed_dns": pod_completed,
                "delivery_rate": delivery_rate,
                "pgi_rate": pgi_rate,
                "pod_rate": pod_rate,
                "health_score": KPIEngine.calculate_health_score({
                    "delivery_rate": delivery_rate,
                    "pod_rate": pod_rate,
                    "avg_aging": 0,
                    "revenue": float(metrics_result.total_revenue or 0)
                }),
                "risk_level": risk_level,
                "risk_score": risk_score
            }
            
            # Add distance information
            if response.get('warehouse') and response.get('city'):
                distance_info = self.distance.calculate_dealer_distance(
                    response.get('warehouse'),
                    response.get('city')
                )
                if distance_info and distance_info.get('available'):
                    response['distance_km'] = distance_info.get('distance_km')
                    response['travel_time_hours'] = distance_info.get('travel_time_hours')
                    response['transit_days'] = distance_info.get('transit_days')
            
            # Cache response
            self.cache.set("dashboard_cache", cache_key, response)
            
            total_time = time.time() - start_time
            logger.info(f"✅ Dealer dashboard built for: {resolved} in {total_time:.3f}s")
            return response
            
        except Exception as e:
            logger.error(f"❌ Get dealer dashboard failed: {e}")
            return {"error": f"Failed to load dealer data: {str(e)[:100]}"}

# ==========================================================
# BLOCK 12: WAREHOUSE DASHBOARD
# ==========================================================

    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Complete warehouse dashboard - PostgreSQL only."""
        try:
            logger.info(f"🔍 Searching for warehouse: '{warehouse_name}'")
            
            resolved_result = self.resolver.resolve_warehouse(warehouse_name)
            
            if not resolved_result.get('found'):
                similar = self.search.search_warehouse(warehouse_name)
                if similar:
                    suggestions = [s['warehouse'] for s in similar[:5]]
                    return {
                        "error": f"Warehouse '{warehouse_name}' not found",
                        "suggestions": suggestions
                    }
                return {"error": f"Warehouse '{warehouse_name}' not found"}
            
            resolved = resolved_result.get('entity')
            
            result = self.db.query(
                DeliveryReport.warehouse.label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.ship_to_city)).label("cities_served"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns")
            ).filter(
                DeliveryReport.warehouse == resolved
            ).group_by(DeliveryReport.warehouse).first()
            
            if not result or result.total_dns == 0:
                return {"error": f"No data found for warehouse '{resolved}'"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            
            response = {
                "warehouse": resolved,
                "warehouse_code": result.warehouse_code or "",
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "total_dealers": result.total_dealers or 0,
                "cities_served": result.cities_served or 0,
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            }
            
            # Add coverage information
            coverage = self.distance.get_warehouse_coverage(resolved)
            if coverage and coverage.get('available'):
                response['coverage_radius'] = coverage.get('coverage_radius')
                response['average_distance'] = coverage.get('average_distance')
                response['cities_served'] = coverage.get('cities_served')
            
            return response
            
        except Exception as e:
            logger.error(f"Get warehouse dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 13: CITY DASHBOARD
# ==========================================================

    def get_city_dashboard(self, city_name: str) -> Dict[str, Any]:
        """Complete city dashboard - PostgreSQL only."""
        try:
            logger.info(f"🔍 Searching for city: '{city_name}'")
            
            resolved_result = self.resolver.resolve_city(city_name)
            
            if not resolved_result.get('found'):
                similar = self.search.search_city(city_name)
                if similar:
                    suggestions = [s['city'] for s in similar[:5]]
                    return {
                        "error": f"City '{city_name}' not found",
                        "suggestions": suggestions
                    }
                return {"error": f"City '{city_name}' not found"}
            
            resolved = resolved_result.get('entity')
            
            result = self.db.query(
                DeliveryReport.ship_to_city.label("city"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.warehouse)).label("total_warehouses"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns")
            ).filter(
                DeliveryReport.ship_to_city == resolved
            ).group_by(DeliveryReport.ship_to_city).first()
            
            if not result or result.total_dns == 0:
                return {"error": f"No data found for city '{resolved}'"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            
            return {
                "city_name": resolved,
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "total_dealers": result.total_dealers or 0,
                "total_warehouses": result.total_warehouses or 0,
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            }
            
        except Exception as e:
            logger.error(f"Get city dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 14: PRODUCT DASHBOARD
# ==========================================================

    def get_product_dashboard(self, product_name: str) -> Dict[str, Any]:
        """Complete product dashboard - PostgreSQL only."""
        try:
            logger.info(f"🔍 Searching for product: '{product_name}'")
            
            resolved_result = self.resolver.resolve_product(product_name)
            
            if not resolved_result.get('found'):
                similar = self.search.search_product(product_name)
                if similar:
                    suggestions = [s['product'] for s in similar[:5]]
                    return {
                        "error": f"Product '{product_name}' not found",
                        "suggestions": suggestions
                    }
                return {"error": f"Product '{product_name}' not found"}
            
            resolved = resolved_result.get('entity')
            
            result = self.db.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label("product"),
                func.sum(DeliveryReport.dn_amount).label("revenue"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
                func.count(distinct(DeliveryReport.customer_name)).label("dealers"),
                func.count(distinct(DeliveryReport.ship_to_city)).label("cities"),
                func.count(distinct(DeliveryReport.warehouse)).label("warehouses"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered")
            ).filter(
                or_(
                    DeliveryReport.customer_model == resolved,
                    DeliveryReport.material_no == resolved
                )
            ).group_by(
                DeliveryReport.customer_model,
                DeliveryReport.material_no
            ).first()
            
            if not result or result.dns == 0:
                return {"error": f"No data found for product '{resolved}'"}
            
            total_dns = result.dns or 1
            
            return {
                "product": resolved,
                "revenue": float(result.revenue or 0),
                "units": int(result.units or 0),
                "dns": total_dns,
                "dealers": result.dealers or 0,
                "cities": result.cities or 0,
                "warehouses": result.warehouses or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(result.delivered or 0, total_dns)
            }
            
        except Exception as e:
            logger.error(f"Get product dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 15: PGI DASHBOARD
# ==========================================================

    def get_pgi_dashboard(self) -> Dict[str, Any]:
        """PGI dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no), else_=None))).label("pgi_completed"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pgi_pending"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("in_transit")
            ).first()
            
            total = result.total_dns or 1
            pgi_completed = result.pgi_completed or 0
            in_transit = result.in_transit or 0
            
            return {
                "total_dns": total,
                "pgi_completed": pgi_completed,
                "pgi_pending": result.pgi_pending or 0,
                "in_transit": in_transit,
                "pgi_rate": KPIEngine.calculate_pgi_rate(pgi_completed, in_transit, total)
            }
            
        except Exception as e:
            logger.error(f"Get PGI dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 16: POD DASHBOARD
# ==========================================================

    def get_pod_dashboard(self) -> Dict[str, Any]:
        """POD dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pod_pending"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns")
            ).first()
            
            total = result.total_dns or 1
            pod_completed = result.pod_completed or 0
            delivered_dns = result.delivered_dns or 0
            
            return {
                "total_dns": total,
                "pod_completed": pod_completed,
                "pod_pending": result.pod_pending or 0,
                "delivered_dns": delivered_dns,
                "pod_rate": KPIEngine.calculate_pod_rate(pod_completed, delivered_dns) if delivered_dns > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Get POD dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 17: DELIVERY DASHBOARD
# ==========================================================

    def get_delivery_dashboard(self) -> Dict[str, Any]:
        """Delivery dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("in_transit"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi")
            ).first()
            
            total = result.total_dns or 1
            delivered = result.delivered or 0
            in_transit = result.in_transit or 0
            
            return {
                "total_dns": total,
                "delivered": delivered,
                "in_transit": in_transit,
                "pending_pgi": result.pending_pgi or 0,
                "pending": result.pending or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered, total),
                "pgi_rate": KPIEngine.calculate_pgi_rate(delivered, in_transit, total)
            }
            
        except Exception as e:
            logger.error(f"Get delivery dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 18: EXECUTIVE DASHBOARD
# ==========================================================

    def get_executive_dashboard(self) -> Dict[str, Any]:
        """Executive dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.ship_to_city)).label("total_cities"),
                func.count(distinct(DeliveryReport.warehouse)).label("total_warehouses"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns")
            ).first()
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            
            return {
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "total_dealers": result.total_dealers or 0,
                "total_cities": result.total_cities or 0,
                "total_warehouses": result.total_warehouses or 0,
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            }
            
        except Exception as e:
            logger.error(f"Get executive dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 19: CONTROL TOWER DASHBOARD
# ==========================================================

    def get_control_tower_dashboard(self) -> Dict[str, Any]:
        """Control tower dashboard - PostgreSQL only."""
        try:
            pgi_alerts = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                func.date_part('day', func.now() - DeliveryReport.dn_create_date).label("days_old")
            ).filter(
                DeliveryReport.good_issue_date.is_(None),
                DeliveryReport.dn_create_date.isnot(None),
                func.date_part('day', func.now() - DeliveryReport.dn_create_date) > 7
            ).order_by(desc("days_old")).limit(10).all()
            
            pod_alerts = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                func.date_part('day', func.now() - DeliveryReport.good_issue_date).label("days_old")
            ).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.pod_date.is_(None),
                func.date_part('day', func.now() - DeliveryReport.good_issue_date) > 7
            ).order_by(desc("days_old")).limit(10).all()
            
            alerts = []
            for r in pgi_alerts:
                days = int(r.days_old or 0)
                alerts.append({
                    "type": "Pending PGI",
                    "severity": "high" if days > 15 else "medium",
                    "description": f"DN {r.dn_no} for {r.customer_name} pending PGI for {days} days"
                })
            
            for r in pod_alerts:
                days = int(r.days_old or 0)
                alerts.append({
                    "type": "Pending POD",
                    "severity": "critical" if days > 30 else "high" if days > 15 else "medium",
                    "description": f"DN {r.dn_no} for {r.customer_name} pending POD for {days} days"
                })
            
            return {
                "alerts": alerts[:20],
                "critical_count": sum(1 for a in alerts if a.get("severity") == "critical"),
                "high_count": sum(1 for a in alerts if a.get("severity") == "high"),
                "total_alerts": len(alerts)
            }
            
        except Exception as e:
            logger.error(f"Get control tower dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 20: REVENUE DASHBOARD
# ==========================================================

    def get_revenue_dashboard(self) -> Dict[str, Any]:
        """Revenue dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns")
            ).first()
            
            by_dealer = self.db.query(
                DeliveryReport.customer_name.label("dealer"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(desc("revenue")).limit(10).all()
            
            dealer_revenue = []
            for r in by_dealer:
                dealer_revenue.append({
                    "dealer": r.dealer or "Unknown",
                    "revenue": float(r.revenue or 0)
                })
            
            return {
                "total_revenue": float(result.total_revenue or 0),
                "total_units": int(result.total_units or 0),
                "total_dns": result.total_dns or 0,
                "top_dealers": dealer_revenue
            }
            
        except Exception as e:
            logger.error(f"Get revenue dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 21: RANKING DASHBOARD
# ==========================================================

    def get_ranking_dashboard(self, limit: int = 10) -> Dict[str, Any]:
        """Dealer ranking - PostgreSQL only."""
        try:
            results = self.db.query(
                DeliveryReport.customer_name.label("dealer"),
                func.sum(DeliveryReport.dn_amount).label("revenue"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered")
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(desc("revenue")).limit(limit).all()
            
            ranking = []
            for r in results:
                total_dns = r.dns or 1
                ranking.append({
                    "dealer": r.dealer or "Unknown",
                    "revenue": float(r.revenue or 0),
                    "units": int(r.units or 0),
                    "dns": total_dns,
                    "delivery_rate": KPIEngine.calculate_delivery_rate(r.delivered or 0, total_dns)
                })
            
            return {"ranking": ranking, "total": len(ranking)}
            
        except Exception as e:
            logger.error(f"Get ranking dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 22: AGING DASHBOARD
# ==========================================================

    def get_aging_dashboard(self) -> Dict[str, Any]:
        """Aging dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(case((func.date_part('day', func.now() - DeliveryReport.dn_create_date) <= 7, DeliveryReport.dn_no), else_=None))).label("days_0_7"),
                func.count(distinct(case((and_(func.date_part('day', func.now() - DeliveryReport.dn_create_date) > 7, func.date_part('day', func.now() - DeliveryReport.dn_create_date) <= 14), DeliveryReport.dn_no), else_=None))).label("days_8_14"),
                func.count(distinct(case((and_(func.date_part('day', func.now() - DeliveryReport.dn_create_date) > 14, func.date_part('day', func.now() - DeliveryReport.dn_create_date) <= 30), DeliveryReport.dn_no), else_=None))).label("days_15_30"),
                func.count(distinct(case((func.date_part('day', func.now() - DeliveryReport.dn_create_date) > 30, DeliveryReport.dn_no), else_=None))).label("days_30_plus")
            ).filter(
                DeliveryReport.dn_create_date.isnot(None),
                DeliveryReport.pending_flag == True
            ).first()
            
            return {
                "days_0_7": result.days_0_7 or 0,
                "days_8_14": result.days_8_14 or 0,
                "days_15_30": result.days_15_30 or 0,
                "days_30_plus": result.days_30_plus or 0,
                "total_pending": (result.days_0_7 or 0) + (result.days_8_14 or 0) + (result.days_15_30 or 0) + (result.days_30_plus or 0)
            }
            
        except Exception as e:
            logger.error(f"Get aging dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 23: DEALER 360 DASHBOARD
# ==========================================================

    def get_dealer_360_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get complete 360° dealer dashboard."""
        return self.dealer360.get_dashboard(dealer_name)

# ==========================================================
# BLOCK 24: ANALYTICS SERVICE CLASS
# ==========================================================

class AnalyticsService:
    """
    Main analytics service - PostgreSQL only.
    Enterprise Production Grade v32.0.
    """
    
    def __init__(self, db: Optional[Session] = None):
        self.repo = AnalyticsRepository(db)
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "avg_response_time": 0
        }
        logger.info("✅ AnalyticsService v32.0 initialized - PostgreSQL Only")
    
    def close(self):
        self.repo.close()
    
    # ==========================================================
    # BLOCK 25: SEARCH METHODS
    # ==========================================================
    
    def search_dn(self, query: str, exact: bool = False) -> AnalyticsResponse:
        """Search for DNs - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="DN number is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_dn(query, exact)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"DN {query} not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"DN search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_dealer(self, query: str, exact: bool = False) -> AnalyticsResponse:
        """Search for dealers - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="Dealer name is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_dealer(query, exact)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"Dealer '{query}' not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_warehouse(self, query: str) -> AnalyticsResponse:
        """Search for warehouses - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="Warehouse name is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_warehouse(query)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"Warehouse '{query}' not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Warehouse search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_city(self, query: str) -> AnalyticsResponse:
        """Search for cities - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="City name is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_city(query)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"City '{query}' not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"City search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_product(self, query: str) -> AnalyticsResponse:
        """Search for products - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="Product name is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_product(query)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"Product '{query}' not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Product search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # BLOCK 26: VERIFICATION METHODS
    # ==========================================================
    
    def verify_dn_exists(self, dn_no: str) -> AnalyticsResponse:
        """Verify DN exists - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.verify_dn_exists(dn_no)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"DN verification failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def verify_dealer_exists(self, dealer_name: str) -> AnalyticsResponse:
        """Verify dealer exists - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.verify_dealer_exists(dealer_name)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"exists": result})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer verification failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # BLOCK 27: ENTITY RESOLUTION
    # ==========================================================
    
    def resolve_dealer(self, dealer_name: str) -> Optional[str]:
        return self.repo.resolver.resolve_dealer(dealer_name)
    
    def resolve_warehouse(self, warehouse_name: str) -> Optional[str]:
        return self.repo.resolver.resolve_warehouse(warehouse_name)
    
    def resolve_city(self, city_name: str) -> Optional[str]:
        return self.repo.resolver.resolve_city(city_name)
    
    def resolve_product(self, product_name: str) -> Optional[str]:
        return self.repo.resolver.resolve_product(product_name)
    
    def resolve_dn(self, dn_no: str) -> Optional[str]:
        return self.repo.resolver.resolve_dn(dn_no)
    
    # ==========================================================
    # BLOCK 28: DASHBOARD METHODS
    # ==========================================================
    
    def get_dealer_360_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Get complete 360° dealer dashboard."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Dealer 360 Dashboard request for: {dealer_name}")
            
            if not dealer_name or not str(dealer_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer name is required")
            
            if not hasattr(self.repo, 'get_dealer_360_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer 360 dashboard not available")
            
            result = self.repo.get_dealer_360_dashboard(dealer_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer 360 dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_dn_dashboard(self, dn_no: str) -> AnalyticsResponse:
        """Get DN Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 DN Dashboard request for: {dn_no}")
            
            if not dn_no or not str(dn_no).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="DN number is required")
            
            if not hasattr(self.repo, 'get_dn_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="DN dashboard not available")
            
            result = self.repo.get_dn_dashboard(dn_no.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"DN dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_dealer_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Get Dealer Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Dealer Dashboard request for: {dealer_name}")
            
            if not dealer_name or not str(dealer_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer name is required")
            
            # Try 360 dashboard first
            if hasattr(self.repo, 'get_dealer_360_dashboard'):
                try:
                    result = self.repo.get_dealer_360_dashboard(dealer_name.strip())
                    if "error" not in result:
                        result['_dashboard_type'] = '360'
                        self.metrics["successful_requests"] += 1
                        return AnalyticsResponse(success=True, data=result)
                except Exception as e:
                    logger.warning(f"360 dashboard failed, falling back: {e}")
            
            # Fallback to legacy
            if not hasattr(self.repo, 'get_dealer_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer dashboard not available")
            
            result = self.repo.get_dealer_dashboard(dealer_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> AnalyticsResponse:
        """Get Warehouse Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Warehouse Dashboard request for: {warehouse_name}")
            
            if not warehouse_name or not str(warehouse_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Warehouse name is required")
            
            if not hasattr(self.repo, 'get_warehouse_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Warehouse dashboard not available")
            
            result = self.repo.get_warehouse_dashboard(warehouse_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Warehouse dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_city_dashboard(self, city_name: str) -> AnalyticsResponse:
        """Get City Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 City Dashboard request for: {city_name}")
            
            if not city_name or not str(city_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="City name is required")
            
            if not hasattr(self.repo, 'get_city_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="City dashboard not available")
            
            result = self.repo.get_city_dashboard(city_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"City dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_product_dashboard(self, product_name: str) -> AnalyticsResponse:
        """Get Product Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Product Dashboard request for: {product_name}")
            
            if not product_name or not str(product_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Product name is required")
            
            if not hasattr(self.repo, 'get_product_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Product dashboard not available")
            
            result = self.repo.get_product_dashboard(product_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Product dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_pgi_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_pgi_dashboard'):
                return AnalyticsResponse(success=False, error="PGI dashboard not available")
            result = self.repo.get_pgi_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"PGI dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_pod_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_pod_dashboard'):
                return AnalyticsResponse(success=False, error="POD dashboard not available")
            result = self.repo.get_pod_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"POD dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_delivery_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_delivery_dashboard'):
                return AnalyticsResponse(success=False, error="Delivery dashboard not available")
            result = self.repo.get_delivery_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Delivery dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_executive_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_executive_dashboard'):
                return AnalyticsResponse(success=False, error="Executive dashboard not available")
            result = self.repo.get_executive_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Executive dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_control_tower_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_control_tower_dashboard'):
                return AnalyticsResponse(success=False, error="Control tower not available")
            result = self.repo.get_control_tower_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Control tower error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_revenue_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_revenue_dashboard'):
                return AnalyticsResponse(success=False, error="Revenue dashboard not available")
            result = self.repo.get_revenue_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Revenue dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_ranking_dashboard(self, limit: int = 10) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_ranking_dashboard'):
                return AnalyticsResponse(success=False, error="Ranking dashboard not available")
            result = self.repo.get_ranking_dashboard(limit)
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Ranking dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_aging_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_aging_dashboard'):
                return AnalyticsResponse(success=False, error="Aging dashboard not available")
            result = self.repo.get_aging_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Aging dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))

# ==========================================================
# BLOCK 29: FACTORY FUNCTION (FIXED v9.0)
# ==========================================================

_analytics_service = None
_analytics_initialization_attempts = 0
_MAX_ANALYTICS_INIT_ATTEMPTS = 3

def get_analytics_service(db: Optional[Session] = None) -> AnalyticsService:
    """
    Get or create AnalyticsService singleton.
    ALWAYS returns AnalyticsService, NEVER None.
    """
    global _analytics_service, _analytics_initialization_attempts
    
    logger.info("=" * 60)
    logger.info("🔍 ANALYTICS SERVICE INITIALIZATION")
    logger.info("=" * 60)
    
    # If already initialized, return it
    if _analytics_service is not None:
        logger.info(f"✅ AnalyticsService already initialized")
        return _analytics_service
    
    # Check max attempts
    if _analytics_initialization_attempts >= _MAX_ANALYTICS_INIT_ATTEMPTS:
        logger.error(f"❌ Max attempts ({_MAX_ANALYTICS_INIT_ATTEMPTS}) reached")
        logger.warning("⚠️ Creating emergency instance")
        try:
            _analytics_service = AnalyticsService(db)
            return _analytics_service
        except Exception as e:
            logger.error(f"❌ Emergency creation failed: {e}")
            _analytics_service = AnalyticsService()
            return _analytics_service
    
    _analytics_initialization_attempts += 1
    logger.info(f"📌 Attempt {_analytics_initialization_attempts}/{_MAX_ANALYTICS_INIT_ATTEMPTS}")
    
    # ==========================================================
    # VALIDATION: Test Database Connection
    # ==========================================================
    try:
        from app.database import SessionLocal
        from app.models import DeliveryReport
        from sqlalchemy import func
        
        test_db = SessionLocal()
        total_records = test_db.query(DeliveryReport).count()
        total_dns = test_db.query(func.count(distinct(DeliveryReport.dn_no))).scalar()
        total_dealers = test_db.query(func.count(distinct(DeliveryReport.customer_name))).scalar()
        test_db.close()
        
        logger.info("✅ PostgreSQL Validation: SUCCESS")
        logger.info(f"   📊 Total Records: {total_records}")
        logger.info(f"   📦 Total DNs: {total_dns}")
        logger.info(f"   🏪 Total Dealers: {total_dealers}")
        
        if total_records == 0:
            logger.warning("⚠️ Database has ZERO records")
            logger.warning("   💡 Insert data into delivery_reports table")
            
    except Exception as e:
        logger.error(f"❌ Database validation failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    # ==========================================================
    # Create Analytics Service - NEVER RETURN NONE
    # ==========================================================
    try:
        logger.info("🔄 Creating AnalyticsService...")
        _analytics_service = AnalyticsService(db)
        logger.info(f"✅ AnalyticsService created successfully")
        logger.info(f"📊 Service type: {type(_analytics_service)}")
        logger.info(f"📊 Service class: {_analytics_service.__class__.__name__}")
        
        # ==========================================================
        # Verify Critical Methods
        # ==========================================================
        critical_methods = [
            "get_dealer_360_dashboard",
            "get_dealer_dashboard",
            "get_dn_dashboard",
            "search_dealer",
            "get_warehouse_dashboard",
            "get_city_dashboard",
            "get_product_dashboard"
        ]
        
        logger.info("🔍 Verifying critical methods:")
        for method in critical_methods:
            if hasattr(_analytics_service, method):
                logger.info(f"   ✅ {method}: AVAILABLE")
            else:
                logger.error(f"   ❌ {method}: MISSING")
        
        logger.info("=" * 60)
        logger.info("✅ AnalyticsService initialized successfully")
        logger.info("   Service is ready to serve REAL PostgreSQL data")
        logger.info("=" * 60)
        
        _analytics_initialization_attempts = 0
        return _analytics_service
        
    except Exception as e:
        logger.error(f"❌ Failed to create AnalyticsService: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # ==========================================================
        # CRITICAL FIX: ALWAYS return an instance, never None
        # ==========================================================
        logger.warning("⚠️ Creating emergency AnalyticsService instance...")
        try:
            _analytics_service = AnalyticsService(db)
            logger.info(f"✅ Emergency AnalyticsService created")
            return _analytics_service
        except Exception as e2:
            logger.error(f"❌ Emergency creation failed: {e2}")
            # ABSOLUTE LAST RESORT
            _analytics_service = AnalyticsService()
            logger.info("✅ Absolute last resort AnalyticsService created")
            return _analytics_service

# ==========================================================
# BLOCK 30: SERVICE HEALTH API (NEW)
# ==========================================================

def get_service_status(service: Optional[AnalyticsService] = None) -> Dict[str, Any]:
    """Get comprehensive service status."""
    if service is None:
        service = _analytics_service
    return ServiceHealthAPI.get_service_status(service)

def is_service_healthy(service: Optional[AnalyticsService] = None) -> bool:
    """Quick health check."""
    if service is None:
        service = _analytics_service
    return ServiceHealthAPI.is_service_healthy(service)

def get_health_report(service: Optional[AnalyticsService] = None) -> Dict[str, Any]:
    """Get detailed health report."""
    if service is None:
        service = _analytics_service
    return ServiceHealthAPI.get_health_report(service)

# ==========================================================
# BLOCK 31: EXPORTS
# ==========================================================

__all__ = [
    'AnalyticsService',
    'AnalyticsResponse',
    'AnalyticsRepository',
    'KPIEngine',
    'DateValidator',
    'SearchEngine',
    'EntityResolver',
    'PostgreSQLHealthEngine',
    'CacheEngine',
    'DistanceIntegration',
    'Dealer360Integration',
    'ServiceHealthAPI',
    'validate_postgresql_health',
    'get_analytics_service',
    'get_service_status',
    'is_service_healthy',
    'get_health_report',
]

# ==========================================================
# END OF FILE - v32.0 ENTERPRISE PRODUCTION
# ==========================================================
