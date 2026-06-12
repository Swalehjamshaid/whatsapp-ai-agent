# ==========================================================
# FILE: app/services/logistics_query_service.py (v9.3 - FIXED DN SEARCH)
# ==========================================================
# PURPOSE: Single source of truth for operational logistics tracking
#
# IMPROVEMENTS v9.3:
# - ✅ FIXED DN SEARCH - Now properly finds integer DNs
# - ✅ Added multiple search strategies (int, string, with .0, contains)
# - ✅ Improved integer detection for PostgreSQL integer columns
# - ✅ Added fallback search patterns for maximum compatibility
# - ✅ Better logging for DN search debugging
# - ✅ All v9.2 features preserved
# - ✅ Enhanced debug logging for DN search verification
# ==========================================================

from typing import Dict, Any, Optional, List, Tuple, Set
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, cast, String, Integer, Float, text, inspect
from cachetools import TTLCache
from loguru import logger
import time
import difflib
import re

from app.models import DeliveryReport


# ==========================================================
# ERROR CLASSES
# ==========================================================

class UserError(Exception):
    """User-friendly error - shown to WhatsApp user"""
    pass

class SystemError(Exception):
    """System error - logged but not shown to user"""
    pass

class DatabaseError(Exception):
    """Database connection/query error"""
    pass

class ModelAlignmentError(Exception):
    """Model doesn't align with database schema"""
    pass


# ==========================================================
# LOGISTICS QUERY SERVICE
# ==========================================================

class LogisticsQueryService:
    # MANDATORY columns - service cannot work without these
    MANDATORY_COLUMNS = [
        "dn_no",
        "customer_name",
        "dn_create_date",
    ]
    
    # OPTIONAL columns - service works but with limited features
    OPTIONAL_COLUMNS = [
        "customer_code",
        "warehouse",
        "warehouse_code",
        "good_issue_date",
        "pod_date",
        "dn_qty",
        "dn_amount",
        "material_no",
        "customer_model",
        "division",
        "ship_to_city",
        "dealer_code",
        "sales_manager"
    ]
    
    def __init__(self, db: Session):
        self.db = db
        self.table_name = DeliveryReport.__tablename__
        
        # Count cache to reduce table scans
        self._cached_total_count = None
        self._count_cache_time = None
        self._count_cache_ttl = 60  # Cache for 60 seconds
        
        # Performance caching
        self.dealer_cache = TTLCache(maxsize=200, ttl=600)   # 10 minutes
        self.warehouse_cache = TTLCache(maxsize=100, ttl=600)
        self.region_cache = TTLCache(maxsize=50, ttl=600)
        
        # Startup validation
        self._validate_startup()
        
        # Model-database alignment validation (MANDATORY only)
        self._validate_model_alignment()
        
        logger.info("=" * 70)
        logger.info("📦 LOGISTICS QUERY SERVICE v9.3 - FIXED DN SEARCH")
        logger.info(f"   Table: {self.table_name}")
        logger.info(f"   Cache: Dealer={self.dealer_cache.maxsize}, Warehouse={self.warehouse_cache.maxsize}")
        logger.info("=" * 70)
    
    # ==========================================================
    # PHASE 2: DATABASE STARTUP VALIDATION
    # ==========================================================
    
    def _validate_startup(self):
        """Validate database table exists at startup"""
        try:
            inspector = inspect(self.db.bind)
            if not inspector.has_table(self.table_name):
                raise DatabaseError(f"Table '{self.table_name}' not found in database")
            
            # Check if table has data
            count = self._get_total_count()
            logger.info(f"   ✅ Database table '{self.table_name}' found with {count} records")
            
        except Exception as e:
            logger.error(f"   ❌ Database validation failed: {e}")
            raise DatabaseError(f"Database validation failed: {e}")
    
    # ==========================================================
    # PRIORITY 2: COUNT CACHING
    # ==========================================================
    
    def _get_total_count(self) -> int:
        """Get total record count with caching"""
        now = time.time()
        
        if (self._cached_total_count is not None and 
            self._count_cache_time is not None and 
            now - self._count_cache_time < self._count_cache_ttl):
            return self._cached_total_count
        
        try:
            self._cached_total_count = self.db.query(DeliveryReport).count()
            self._count_cache_time = now
            return self._cached_total_count
        except Exception as e:
            logger.error(f"Failed to get count: {e}")
            return 0
    
    def _invalidate_count_cache(self):
        """Invalidate count cache when data might have changed"""
        self._cached_total_count = None
        self._count_cache_time = None
    
    # ==========================================================
    # MODEL-DATABASE ALIGNMENT VALIDATION
    # ==========================================================
    
    def _validate_model_alignment(self):
        """Validate that mandatory columns exist in the database table"""
        try:
            inspector = inspect(self.db.bind)
            actual_columns = {col['name'] for col in inspector.get_columns(self.table_name)}
            
            # Check mandatory columns
            missing_mandatory = []
            for required_col in self.MANDATORY_COLUMNS:
                if required_col not in actual_columns:
                    missing_mandatory.append(required_col)
            
            if missing_mandatory:
                logger.error(f"   ❌ Missing MANDATORY columns: {missing_mandatory}")
                logger.info(f"   Available columns in DB: {sorted(actual_columns)}")
                raise ModelAlignmentError(
                    f"Database table '{self.table_name}' missing mandatory columns: {missing_mandatory}"
                )
            
            # Check optional columns (log warnings only)
            missing_optional = []
            for optional_col in self.OPTIONAL_COLUMNS:
                if optional_col not in actual_columns:
                    missing_optional.append(optional_col)
            
            if missing_optional:
                logger.warning(f"   ⚠️ Missing OPTIONAL columns: {missing_optional}")
                logger.warning(f"   Service will continue with limited functionality")
            
            logger.info(f"   ✅ Model-database alignment verified: {len(self.MANDATORY_COLUMNS)} mandatory columns match")
            
            # Log sample data for verification
            sample = self.db.query(DeliveryReport).first()
            if sample:
                logger.info(f"   📝 Sample DN: {getattr(sample, 'dn_no', 'N/A')}")
                logger.info(f"   📝 Sample Dealer: {getattr(sample, 'customer_name', 'N/A')}")
                warehouse_val = getattr(sample, 'warehouse', None)
                logger.info(f"   📝 Sample Warehouse: {warehouse_val or 'N/A'}")
            
        except ModelAlignmentError:
            raise
        except Exception as e:
            logger.error(f"   ⚠️ Model alignment check warning: {e}")
    
    # ==========================================================
    # RECORD VALIDATION
    # ==========================================================
    
    def _validate_record(self, record: DeliveryReport) -> Tuple[bool, str]:
        """Validate record has minimum required data"""
        if not record:
            return False, "Empty record"
        
        dn_no = getattr(record, 'dn_no', None)
        if not dn_no:
            return False, "Missing DN number"
        
        customer_name = getattr(record, 'customer_name', None)
        if not customer_name:
            return False, f"DN {dn_no} missing customer name"
        
        dn_date = getattr(record, 'dn_create_date', None)
        if not dn_date:
            return False, f"DN {dn_no} missing creation date"
        
        return True, "Valid"
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _validate_session(self) -> bool:
        if not self.db:
            logger.error("Database session is None")
            return False
        return True
    
    # ==========================================================
    # UNIVERSAL DN NORMALIZATION
    # ==========================================================
    
    def normalize_dn(self, dn) -> str:
        """Universal DN normalization - handles all formats"""
        if dn is None:
            return ""
        dn_str = str(dn).strip()
        # Remove .0 suffix
        if dn_str.endswith('.0'):
            dn_str = dn_str[:-2]
        # Remove non-numeric characters
        dn_str = re.sub(r'[^0-9]', '', dn_str)
        return dn_str
    
    # ==========================================================
    # SMART DN NOT FOUND
    # ==========================================================
    
    def _find_closest_dns(self, searched_dn: str, limit: int = 5) -> List[str]:
        """Find closest matching DNs for user feedback"""
        try:
            all_dns = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.dn_no.isnot(None)
            ).distinct().limit(100).all()
            
            dn_strings = [str(dn[0]) for dn in all_dns if dn[0]]
            
            closest = difflib.get_close_matches(searched_dn, dn_strings, n=limit, cutoff=0.6)
            return closest
        except Exception as e:
            logger.warning(f"Could not find closest DNs: {e}")
            return []
    
    # ==========================================================
    # BUSINESS RULE ENGINE
    # ==========================================================
    
    def calculate_delivery_days(self, pgi_date, dn_date) -> int:
        """Business Rule: Delivery Days = PGI Date - DN Date"""
        if pgi_date and dn_date:
            return max(0, (pgi_date - dn_date).days)
        return 0
    
    def calculate_pod_days(self, pod_date, pgi_date) -> int:
        """Business Rule: POD Days = POD Date - PGI Date"""
        if pod_date and pgi_date:
            return max(0, (pod_date - pgi_date).days)
        return 0
    
    def calculate_pending_delivery_days(self, dn_date) -> int:
        """Business Rule: Pending Delivery Days = Today - DN Date"""
        if dn_date:
            return max(0, (date.today() - dn_date).days)
        return 0
    
    def calculate_pending_pod_days(self, pgi_date) -> int:
        """Business Rule: Pending POD Days = Today - PGI Date"""
        if pgi_date:
            return max(0, (date.today() - pgi_date).days)
        return 0
    
    def calculate_priority(self, days: int) -> str:
        if days > 14:
            return "CRITICAL"
        elif days > 7:
            return "HIGH"
        elif days > 3:
            return "MEDIUM"
        else:
            return "LOW"
    
    # ==========================================================
    # DYNAMIC STATUS CALCULATION
    # ==========================================================
    
    def calculate_dn_status(self, pgi_date, pod_date) -> Dict[str, str]:
        """Calculate status dynamically from dates"""
        if pgi_date and pod_date:
            return {"status": "Delivered", "emoji": "✅", "description": "Full delivery completed"}
        elif pgi_date and not pod_date:
            return {"status": "POD Pending", "emoji": "⏳", "description": "Dispatched, awaiting proof of delivery"}
        else:
            return {"status": "Delivery Pending", "emoji": "🟡", "description": "Not yet dispatched"}
    
    def calculate_pod_status(self, pod_date) -> str:
        """Calculate POD status dynamically"""
        if pod_date:
            return "RECEIVED"
        return "PENDING"
    
    def calculate_delivery_status(self, good_issue_date, pod_date) -> str:
        """Calculate delivery status dynamically"""
        if good_issue_date and pod_date:
            return "DELIVERED"
        elif good_issue_date:
            return "IN_TRANSIT"
        return "PENDING"
    
    # ==========================================================
    # DN AGGREGATION ENGINE
    # ==========================================================
    
    def aggregate_dn_records(self, records: List[DeliveryReport]) -> Dict[str, Any]:
        """Aggregate multiple records of same DN into one. 1 DN = Multiple Products, counted ONCE"""
        if not records:
            return {}
        
        valid_records = []
        for r in records:
            is_valid, msg = self._validate_record(r)
            if is_valid:
                valid_records.append(r)
            else:
                logger.warning(f"Skipping invalid record: {msg}")
        
        if not valid_records:
            logger.error("No valid records found in aggregation")
            return {}
        
        first = valid_records[0]
        
        dn_number = self.normalize_dn(getattr(first, 'dn_no', None))
        customer_name = getattr(first, 'customer_name', 'N/A')
        customer_code = getattr(first, 'customer_code', None) or getattr(first, 'dealer_code', None) or 'N/A'
        division = getattr(first, 'division', 'N/A')
        warehouse = getattr(first, 'warehouse', 'N/A')
        warehouse_code = getattr(first, 'warehouse_code', None) or warehouse
        city = getattr(first, 'ship_to_city', 'N/A')
        dn_create_date = getattr(first, 'dn_create_date', None)
        good_issue_date = getattr(first, 'good_issue_date', None)
        pod_date = getattr(first, 'pod_date', None)
        
        unique_models = set()
        products = []
        product_codes = set()
        total_quantity = 0
        total_amount = 0.0
        
        for r in valid_records:
            dn_qty = getattr(r, 'dn_qty', 0)
            dn_amount = getattr(r, 'dn_amount', 0.0)
            material_no = getattr(r, 'material_no', None)
            customer_model = getattr(r, 'customer_model', None)
            
            total_quantity += int(dn_qty or 0)
            total_amount += float(dn_amount or 0)
            
            if material_no:
                model_name = customer_model or material_no
                unique_models.add(model_name)
                
                if material_no not in product_codes:
                    product_codes.add(material_no)
                    products.append({
                        "material_no": material_no,
                        "customer_model": model_name,
                        "quantity": int(dn_qty or 0),
                        "amount": float(dn_amount or 0)
                    })
                else:
                    for p in products:
                        if p["material_no"] == material_no:
                            p["quantity"] += int(dn_qty or 0)
                            p["amount"] += float(dn_amount or 0)
                            break
        
        delivery_days = self.calculate_delivery_days(good_issue_date, dn_create_date)
        pod_days = self.calculate_pod_days(pod_date, good_issue_date)
        status_info = self.calculate_dn_status(good_issue_date, pod_date)
        
        return {
            "dn_no": dn_number,
            "customer_name": customer_name,
            "customer_code": customer_code,
            "division": division,
            "warehouse": warehouse,
            "warehouse_code": warehouse_code,
            "city": city,
            "dn_date": dn_create_date,
            "dn_date_str": dn_create_date.strftime("%Y-%m-%d") if dn_create_date else "N/A",
            "pgi_date": good_issue_date,
            "pgi_date_str": good_issue_date.strftime("%Y-%m-%d") if good_issue_date else "Not Dispatched",
            "pod_date": pod_date,
            "pod_date_str": pod_date.strftime("%Y-%m-%d") if pod_date else "Not Received",
            "delivery_days": delivery_days,
            "pod_days": pod_days,
            "status": status_info["status"],
            "status_emoji": status_info["emoji"],
            "status_description": status_info["description"],
            "total_models": len(unique_models),
            "models_list": sorted(list(unique_models)),
            "total_quantity": total_quantity,
            "total_amount": total_amount,
            "products": products
        }
    
    # ==========================================================
    # MULTI-STAGE DN SEARCH - IMPROVED FOR INTEGER SEARCH
    # ==========================================================
    
    def _search_dn(self, dn_number: str) -> List[DeliveryReport]:
        """
        Multi-stage DN search with improved integer detection.
        CRITICAL FIX: Properly handles PostgreSQL integer columns.
        """
        start_time = time.time()
        normalized = self.normalize_dn(dn_number)
        
        total_records = self._get_total_count()
        logger.info(f"🔍 DN Search Input: '{dn_number}' -> normalized: '{normalized}'")
        logger.info(f"   Table: {self.table_name}, Total records: {total_records}")
        
        # Try to convert to integer if possible (CRITICAL for PostgreSQL integer columns)
        int_val = None
        try:
            int_val = int(normalized)
            logger.info(f"   ✓ Successfully converted to integer: {int_val} (type: {type(int_val).__name__})")
        except ValueError:
            logger.warning(f"   ✗ Cannot convert '{normalized}' to integer - will use string search only")
        
        # Build search conditions - INTEGER FIRST (since your data is stored as integers)
        search_conditions = []
        
        # Priority 1: Integer match (YOUR DATA TYPE - PostgreSQL integer column)
        if int_val is not None:
            logger.info(f"   Strategy 1: Integer equality search for {int_val}")
            search_conditions.append(DeliveryReport.dn_no == int_val)
        else:
            logger.info(f"   Strategy 1: Skipped (not an integer)")
        
        # Priority 2: String match
        logger.info(f"   Strategy 2: String exact search for '{normalized}'")
        search_conditions.append(cast(DeliveryReport.dn_no, String) == normalized)
        
        # Priority 3: With .0 suffix
        logger.info(f"   Strategy 3: String with .0 suffix for '{normalized}.0'")
        search_conditions.append(cast(DeliveryReport.dn_no, String) == f"{normalized}.0")
        
        # Execute combined search
        logger.info(f"   Executing combined search with {len(search_conditions)} conditions...")
        results = self.db.query(DeliveryReport).filter(or_(*search_conditions)).all()
        
        # Log detailed results
        logger.info(f"   📊 Combined search returned {len(results)} records")
        
        if results:
            # Verify first result's DN type and value
            first_dn = results[0].dn_no
            logger.info(f"   ✅ First result DN: '{first_dn}' (type: {type(first_dn).__name__})")
            
            # Check if integer comparison would have worked
            if int_val is not None and first_dn == int_val:
                logger.info(f"   ✓ Match verified: {first_dn} == {int_val} (integer match)")
            elif str(first_dn) == normalized:
                logger.info(f"   ✓ Match verified: {first_dn} == {normalized} (string match)")
            else:
                logger.info(f"   ✓ Match verified via contains or other strategy")
            
            elapsed = time.time() - start_time
            logger.info(f"✅ DN found: {dn_number} ({len(results)} records, {elapsed:.3f}s)")
            return results
        
        # If still not found, try contains as last resort
        logger.info(f"   Strategy 4: Contains search for '%{normalized}%' (last resort)")
        contains_results = self.db.query(DeliveryReport).filter(
            cast(DeliveryReport.dn_no, String).like(f"%{normalized}%")
        ).all()
        
        logger.info(f"   📊 Contains search returned {len(contains_results)} records")
        
        if contains_results:
            first_dn = contains_results[0].dn_no
            logger.info(f"   ✅ First result DN: '{first_dn}' (type: {type(first_dn).__name__})")
            elapsed = time.time() - start_time
            logger.info(f"✅ DN found via contains: {dn_number} ({len(contains_results)} records, {elapsed:.3f}s)")
            return contains_results
        
        elapsed = time.time() - start_time
        logger.info(f"❌ DN {dn_number} not found after all search stages ({elapsed:.3f}s)")
        
        # Additional debug: Check what DNs exist in the table
        sample_dns = self.db.query(DeliveryReport.dn_no).filter(
            DeliveryReport.dn_no.isnot(None)
        ).limit(5).all()
        if sample_dns:
            sample_str = ', '.join([f"'{dn[0]}' (type: {type(dn[0]).__name__})" for dn in sample_dns])
            logger.info(f"   Sample DNs in table: {sample_str}")
        
        return []
    
    # ==========================================================
    # DEBUG DN VERIFICATION TOOL
    # ==========================================================
    
    def verify_dn_column_type(self) -> Dict[str, Any]:
        """Verify the actual data type of dn_no column in the database"""
        try:
            inspector = inspect(self.db.bind)
            columns = inspector.get_columns(self.table_name)
            
            dn_column = None
            for col in columns:
                if col['name'] == 'dn_no':
                    dn_column = col
                    break
            
            if dn_column:
                col_type = str(dn_column['type'])
                is_integer = 'INT' in col_type.upper() or 'INTEGER' in col_type.upper()
                
                # Get sample values to verify
                samples = self.db.query(DeliveryReport.dn_no).filter(
                    DeliveryReport.dn_no.isnot(None)
                ).limit(5).all()
                
                sample_values = []
                for s in samples:
                    val = s[0]
                    sample_values.append({
                        "value": val,
                        "type": type(val).__name__,
                        "is_int_instance": isinstance(val, int)
                    })
                
                return {
                    "column_name": "dn_no",
                    "database_type": col_type,
                    "is_integer_column": is_integer,
                    "python_type_from_db": sample_values[0]["type"] if sample_values else "unknown",
                    "sample_values": sample_values,
                    "recommended_search": "integer comparison" if is_integer else "string comparison"
                }
            else:
                return {"error": "dn_no column not found"}
        except Exception as e:
            return {"error": str(e)}
    
    def test_dn_search_methods(self, dn_number: str) -> Dict[str, Any]:
        """Test each search method individually to see which works"""
        start_time = time.time()
        normalized = self.normalize_dn(dn_number)
        
        results = {
            "dn_searched": dn_number,
            "normalized": normalized,
            "tests": {}
        }
        
        # Test 1: Direct integer comparison (if possible)
        try:
            int_val = int(normalized)
            int_results = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == int_val
            ).all()
            results["tests"]["integer_comparison"] = {
                "used_value": int_val,
                "found": len(int_results) > 0,
                "count": len(int_results),
                "sample_dn": int_results[0].dn_no if int_results else None
            }
            logger.info(f"   Test 1 (integer={int_val}): {len(int_results)} results")
        except ValueError:
            results["tests"]["integer_comparison"] = {
                "error": f"Cannot convert '{normalized}' to integer"
            }
            logger.warning(f"   Test 1 skipped: not an integer")
        
        # Test 2: String exact match
        str_results = self.db.query(DeliveryReport).filter(
            cast(DeliveryReport.dn_no, String) == normalized
        ).all()
        results["tests"]["string_exact"] = {
            "found": len(str_results) > 0,
            "count": len(str_results),
            "sample_dn": str_results[0].dn_no if str_results else None
        }
        logger.info(f"   Test 2 (string='{normalized}'): {len(str_results)} results")
        
        # Test 3: Contains
        contains_results = self.db.query(DeliveryReport).filter(
            cast(DeliveryReport.dn_no, String).like(f"%{normalized}%")
        ).all()
        results["tests"]["contains"] = {
            "found": len(contains_results) > 0,
            "count": len(contains_results),
            "sample_dn": contains_results[0].dn_no if contains_results else None
        }
        logger.info(f"   Test 3 (contains='%{normalized}%'): {len(contains_results)} results")
        
        # Get column info
        try:
            inspector = inspect(self.db.bind)
            columns = inspector.get_columns(self.table_name)
            for col in columns:
                if col['name'] == 'dn_no':
                    results["column_info"] = {
                        "type": str(col['type']),
                        "nullable": col['nullable'],
                        "python_type": type(self.db.query(DeliveryReport.dn_no).first()[0]).__name__ if self.db.query(DeliveryReport.dn_no).first() else "unknown"
                    }
                    break
        except Exception as e:
            results["column_info"] = {"error": str(e)}
        
        elapsed = time.time() - start_time
        results["test_time_ms"] = round(elapsed * 1000, 2)
        
        return results
    
    def debug_dn_search(self, dn_number: str) -> Dict[str, Any]:
        """Comprehensive DN search debug tool"""
        start_time = time.time()
        
        normalized = self.normalize_dn(dn_number)
        total_records = self._get_total_count()
        
        # Try integer conversion
        try:
            int_val = int(normalized)
        except ValueError:
            int_val = None
        
        # Test each search method individually
        results = {
            "searched_dn": dn_number,
            "normalized": normalized,
            "as_integer": int_val,
            "total_records": total_records,
            "methods": {}
        }
        
        # Test integer match
        if int_val is not None:
            int_match = self.db.query(DeliveryReport).filter(DeliveryReport.dn_no == int_val).all()
            results["methods"]["integer_match"] = {
                "found": len(int_match) > 0,
                "count": len(int_match)
            }
            if int_match:
                results["sample"] = [{"dn_no": r.dn_no, "customer": r.customer_name} for r in int_match[:3]]
        
        # Test string match
        string_match = self.db.query(DeliveryReport).filter(cast(DeliveryReport.dn_no, String) == normalized).all()
        results["methods"]["string_match"] = {
            "found": len(string_match) > 0,
            "count": len(string_match)
        }
        
        # Test with .0
        dot_match = self.db.query(DeliveryReport).filter(cast(DeliveryReport.dn_no, String) == f"{normalized}.0").all()
        results["methods"]["with_dot"] = {
            "found": len(dot_match) > 0,
            "count": len(dot_match)
        }
        
        # Get sample DNs
        sample_dns = self.db.query(DeliveryReport.dn_no).filter(
            DeliveryReport.dn_no.isnot(None)
        ).distinct().limit(10).all()
        results["sample_dns"] = [str(dn[0]) for dn in sample_dns if dn[0]]
        
        results["found"] = any(
            m.get("found", False) for m in results["methods"].values()
        )
        
        elapsed = time.time() - start_time
        results["debug_time_ms"] = round(elapsed * 1000, 2)
        
        return results
    
    # ==========================================================
    # MANDATORY DN RESPONSE STRUCTURE
    # ==========================================================
    
    def build_standard_dn_response(self, aggregated: Dict[str, Any]) -> Dict[str, Any]:
        """Build standard DN response with all mandatory fields"""
        return {
            "dn_no": aggregated.get("dn_no", "N/A"),
            "date": aggregated.get("dn_date_str", "N/A"),
            "dealer_name": aggregated.get("customer_name", "N/A"),
            "dealer_code": aggregated.get("customer_code", "N/A"),
            "sales_office": aggregated.get("division", "N/A"),
            "warehouse": aggregated.get("warehouse", "N/A"),
            "warehouse_code": aggregated.get("warehouse_code", "N/A"),
            "city": aggregated.get("city", "N/A"),
            "status": aggregated.get("status", "Unknown"),
            "status_emoji": aggregated.get("status_emoji", "❓"),
            "status_description": aggregated.get("status_description", ""),
            "pgi_date": aggregated.get("pgi_date_str", "Not Dispatched"),
            "pod_date": aggregated.get("pod_date_str", "Not Received"),
            "delivery_days": aggregated.get("delivery_days", 0),
            "pod_days": aggregated.get("pod_days", 0),
            "total_models": aggregated.get("total_models", 0),
            "models_list": aggregated.get("models_list", []),
            "total_quantity": aggregated.get("total_quantity", 0),
            "total_amount": aggregated.get("total_amount", 0.0),
            "products": aggregated.get("products", [])
        }
    
    # ==========================================================
    # MAIN DN QUERY ENTRY POINT
    # ==========================================================
    
    def get_complete_dn_intelligence(self, dn_number: str) -> Dict[str, Any]:
        """Get complete DN intelligence - MAIN ENTRY POINT"""
        start_time = time.time()
        logger.info(f"🔍 DN Query: {dn_number}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable", is_user_error=True)
        
        try:
            records = self._search_dn(dn_number)
            
            if not records:
                closest = self._find_closest_dns(self.normalize_dn(dn_number))
                error_msg = f"DN {dn_number} not found in database."
                if closest:
                    error_msg += f" Did you mean: {', '.join(closest[:3])}?"
                else:
                    sample_dns = self._get_sample_dns(5)
                    if sample_dns:
                        error_msg += f" Available DNs: {', '.join(sample_dns)}"
                
                elapsed = time.time() - start_time
                logger.info(f"❌ DN Query failed: {dn_number} (Not found, {elapsed:.3f}s)")
                return self._format_error(error_msg, is_user_error=True)
            
            aggregated = self.aggregate_dn_records(records)
            if not aggregated:
                error_msg = f"DN {dn_number} found but has invalid data structure"
                logger.error(error_msg)
                return self._format_error(error_msg, is_user_error=True)
            
            response = self.build_standard_dn_response(aggregated)
            priority = self.calculate_priority(response.get("delivery_days", 0))
            response["priority"] = priority
            
            summary = f"DN {response['dn_no']} is {response['status']}. {response['total_models']} models, {response['total_quantity']} units."
            
            elapsed = time.time() - start_time
            logger.info(f"✅ DN Query success: {dn_number} ({len(records)} records, {elapsed:.3f}s)")
            
            return self._format_success(response, summary)
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ DN query error: {e} ({elapsed:.3f}s)")
            return self._format_error("Unable to process DN query", is_user_error=False)
    
    # ==========================================================
    # MULTI-FIELD DEALER SEARCH (with CAST fix)
    # ==========================================================
    
    def _search_dealer(self, search_term: str) -> List[DeliveryReport]:
        """Search dealer by name, dealer code, or customer code (with proper casting)"""
        search_pattern = f"%{search_term}%"
        
        conditions = [
            DeliveryReport.customer_name.ilike(search_pattern)
        ]
        
        if hasattr(DeliveryReport, 'customer_code'):
            conditions.append(
                cast(DeliveryReport.customer_code, String).ilike(search_pattern)
            )
        
        if hasattr(DeliveryReport, 'dealer_code'):
            conditions.append(
                cast(DeliveryReport.dealer_code, String).ilike(search_pattern)
            )
        
        results = self.db.query(DeliveryReport).filter(or_(*conditions)).all()
        
        if results:
            logger.info(f"✅ Dealer found: '{search_term}' -> {len(results)} records")
        else:
            logger.info(f"❌ Dealer not found: '{search_term}'")
        
        return results
    
    # ==========================================================
    # DEALER OPERATIONAL INTELLIGENCE
    # ==========================================================
    
    def get_dealer_all_dns(self, dealer_name: str) -> Dict[str, Any]:
        """Get all DNs for a dealer with complete details"""
        start_time = time.time()
        cache_key = f"dealer_dns_{dealer_name.lower()}"
        
        cache_hit = cache_key in self.dealer_cache
        if cache_hit:
            logger.info(f"📦 Cache HIT for dealer: {dealer_name}")
            return self.dealer_cache[cache_key]
        
        logger.info(f"📦 Cache MISS for dealer: {dealer_name}")
        
        try:
            records = self._search_dealer(dealer_name)
            
            if not records:
                elapsed = time.time() - start_time
                logger.info(f"❌ Dealer query failed: {dealer_name} (Not found, {elapsed:.3f}s)")
                return self._format_error(f"Dealer '{dealer_name}' not found", is_user_error=True)
            
            dn_groups = {}
            for r in records:
                dn_no = self.normalize_dn(getattr(r, 'dn_no', None))
                if dn_no:
                    if dn_no not in dn_groups:
                        dn_groups[dn_no] = []
                    dn_groups[dn_no].append(r)
            
            dns = []
            for dn_no, group in dn_groups.items():
                aggregated = self.aggregate_dn_records(group)
                if aggregated:
                    dns.append({
                        "dn_no": dn_no,
                        "date": aggregated.get("dn_date_str", "N/A"),
                        "pgi_date": aggregated.get("pgi_date_str", "N/A"),
                        "pod_date": aggregated.get("pod_date_str", "N/A"),
                        "warehouse": aggregated.get("warehouse", "N/A"),
                        "status": aggregated.get("status", "Unknown"),
                        "total_models": aggregated.get("total_models", 0),
                        "total_quantity": aggregated.get("total_quantity", 0)
                    })
            
            dns.sort(key=lambda x: x.get("date", ""), reverse=True)
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Dealer query success: {dealer_name} -> {len(dn_groups)} DNs, {len(records)} records ({elapsed:.3f}s)")
            
            result = self._format_success(
                {"dealer_name": dealer_name, "total_dns": len(dns), "dns": dns},
                f"Dealer {dealer_name} has {len(dns)} DNs"
            )
            
            self.dealer_cache[cache_key] = result
            return result
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ Dealer DNs error: {e} ({elapsed:.3f}s)")
            return self._format_error("Unable to retrieve dealer DNs", is_user_error=False)
    
    def get_dealer_operational_summary(self, dealer_name: str) -> Dict[str, Any]:
        """Get operational summary for a dealer"""
        start_time = time.time()
        logger.info(f"📊 Dealer Operational Summary: {dealer_name}")
        
        try:
            records = self._search_dealer(dealer_name)
            
            if not records:
                elapsed = time.time() - start_time
                logger.info(f"❌ Dealer summary failed: {dealer_name} (Not found, {elapsed:.3f}s)")
                return self._format_error(f"Dealer '{dealer_name}' not found", is_user_error=True)
            
            pending_delivery = []
            pending_pod = []
            delayed = []
            
            unique_dn_pending_delivery = set()
            unique_dn_pending_pod = set()
            unique_dn_delayed = set()
            
            for r in records:
                dn_no = self.normalize_dn(getattr(r, 'dn_no', None))
                if not dn_no:
                    continue
                    
                good_issue_date = getattr(r, 'good_issue_date', None)
                pod_date = getattr(r, 'pod_date', None)
                dn_create_date = getattr(r, 'dn_create_date', None)
                
                if not good_issue_date:
                    if dn_no not in unique_dn_pending_delivery:
                        days = self.calculate_pending_delivery_days(dn_create_date)
                        pending_delivery.append({"dn_no": dn_no, "days": days})
                        unique_dn_pending_delivery.add(dn_no)
                elif good_issue_date and not pod_date:
                    if dn_no not in unique_dn_pending_pod:
                        days = self.calculate_pending_pod_days(good_issue_date)
                        pending_pod.append({"dn_no": dn_no, "days": days})
                        unique_dn_pending_pod.add(dn_no)
                
                delivery_days = self.calculate_delivery_days(good_issue_date, dn_create_date)
                if delivery_days > 7:
                    if dn_no not in unique_dn_delayed:
                        delayed.append({"dn_no": dn_no, "days": delivery_days})
                        unique_dn_delayed.add(dn_no)
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Dealer summary success: {dealer_name} -> Pending Delivery: {len(pending_delivery)}, Pending POD: {len(pending_pod)}, Delayed: {len(delayed)} ({elapsed:.3f}s)")
            
            return self._format_success(
                {
                    "dealer_name": dealer_name,
                    "pending_deliveries": len(pending_delivery),
                    "pending_pod": len(pending_pod),
                    "delayed_shipments": len(delayed),
                    "pending_delivery_list": pending_delivery[:10],
                    "pending_pod_list": pending_pod[:10],
                    "delayed_list": delayed[:10]
                },
                f"Dealer {dealer_name}: {len(pending_delivery)} pending deliveries, {len(pending_pod)} pending POD"
            )
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ Dealer operational summary error: {e} ({elapsed:.3f}s)")
            return self._format_error("Unable to retrieve dealer operations", is_user_error=False)
    
    # ==========================================================
    # WAREHOUSE INTELLIGENCE
    # ==========================================================
    
    def get_warehouse_status(self, warehouse_name: str = None) -> Dict[str, Any]:
        """Get warehouse status with priority buckets using DN counts"""
        start_time = time.time()
        cache_key = f"warehouse_{warehouse_name or 'all'}"
        
        cache_hit = cache_key in self.warehouse_cache
        if cache_hit:
            logger.info(f"📦 Cache HIT for warehouse: {warehouse_name or 'all'}")
            return self.warehouse_cache[cache_key]
        
        logger.info(f"📦 Cache MISS for warehouse: {warehouse_name or 'all'}")
        
        try:
            query = self.db.query(DeliveryReport)
            if warehouse_name:
                query = query.filter(DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"))
            
            records = query.all()
            
            if not records:
                elapsed = time.time() - start_time
                logger.info(f"❌ Warehouse query failed: {warehouse_name} (Not found, {elapsed:.3f}s)")
                return self._format_error(f"Warehouse '{warehouse_name}' not found" if warehouse_name else "No warehouse data", is_user_error=True)
            
            warehouses = {}
            for r in records:
                wh = getattr(r, 'warehouse', 'Unknown') or 'Unknown'
                dn_no = self.normalize_dn(getattr(r, 'dn_no', None))
                
                if wh not in warehouses:
                    warehouses[wh] = {
                        "dn_set": set(),
                        "total_quantity": 0,
                        "pending_dispatch_set": set(),
                        "pending_pod_set": set(),
                        "critical_delays_set": set(),
                        "high_priority_set": set()
                    }
                
                if dn_no:
                    warehouses[wh]["dn_set"].add(dn_no)
                warehouses[wh]["total_quantity"] += int(getattr(r, 'dn_qty', 0) or 0)
                
                good_issue_date = getattr(r, 'good_issue_date', None)
                pod_date = getattr(r, 'pod_date', None)
                dn_create_date = getattr(r, 'dn_create_date', None)
                
                if not good_issue_date and dn_no:
                    warehouses[wh]["pending_dispatch_set"].add(dn_no)
                elif good_issue_date and not pod_date and dn_no:
                    warehouses[wh]["pending_pod_set"].add(dn_no)
                
                days = self.calculate_delivery_days(good_issue_date, dn_create_date)
                if days > 14 and dn_no:
                    warehouses[wh]["critical_delays_set"].add(dn_no)
                elif days > 7 and dn_no:
                    warehouses[wh]["high_priority_set"].add(dn_no)
            
            result_list = []
            for wh, data in warehouses.items():
                result_list.append({
                    "warehouse": wh,
                    "dn_count": len(data["dn_set"]),
                    "total_quantity": data["total_quantity"],
                    "pending_dispatch": len(data["pending_dispatch_set"]),
                    "pending_pod": len(data["pending_pod_set"]),
                    "critical_delays": len(data["critical_delays_set"]),
                    "high_priority": len(data["high_priority_set"])
                })
            
            result_list.sort(key=lambda x: x["dn_count"], reverse=True)
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Warehouse query success: {warehouse_name or 'all'} -> {len(result_list)} warehouses ({elapsed:.3f}s)")
            
            result = self._format_success(
                {"warehouses": result_list, "total_warehouses": len(result_list)},
                f"Found {len(result_list)} warehouses"
            )
            
            self.warehouse_cache[cache_key] = result
            return result
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ Warehouse status error: {e} ({elapsed:.3f}s)")
            return self._format_error("Unable to retrieve warehouse status", is_user_error=False)
    
    # ==========================================================
    # REGION INTELLIGENCE
    # ==========================================================
    
    def get_region_performance(self, region: str = None, region_type: str = "warehouse") -> Dict[str, Any]:
        """Get region performance metrics"""
        start_time = time.time()
        cache_key = f"region_{region_type}_{region or 'all'}"
        
        cache_hit = cache_key in self.region_cache
        if cache_hit:
            logger.info(f"📦 Cache HIT for region: {region_type}={region or 'all'}")
            return self.region_cache[cache_key]
        
        logger.info(f"📦 Cache MISS for region: {region_type}={region or 'all'}")
        
        try:
            query = self.db.query(DeliveryReport)
            
            region_field_map = {
                "warehouse": DeliveryReport.warehouse,
                "sales_office": DeliveryReport.division,
                "city": DeliveryReport.ship_to_city,
                "division": DeliveryReport.division
            }
            
            region_field = region_field_map.get(region_type, DeliveryReport.warehouse)
            
            if region and hasattr(region_field, 'ilike'):
                query = query.filter(region_field.ilike(f"%{region}%"))
            elif region:
                query = query.filter(region_field == region)
            
            records = query.all()
            
            if not records:
                elapsed = time.time() - start_time
                logger.info(f"⚠️ Region query: {region_type}={region or 'all'} -> No data ({elapsed:.3f}s)")
                return self._format_success(
                    {"region": region or "All", "region_type": region_type, "total_dns": 0},
                    f"No data for {region_type} {region}" if region else "No data available"
                )
            
            unique_dns = set()
            unique_dealers = set()
            unique_warehouses = set()
            total_quantity = 0
            pending_pod_dns = set()
            delivered_dns = set()
            
            for r in records:
                dn_no = self.normalize_dn(getattr(r, 'dn_no', None))
                if dn_no:
                    unique_dns.add(dn_no)
                
                customer_name = getattr(r, 'customer_name', None)
                if customer_name:
                    unique_dealers.add(customer_name)
                
                warehouse = getattr(r, 'warehouse', None)
                if warehouse:
                    unique_warehouses.add(warehouse)
                
                total_quantity += int(getattr(r, 'dn_qty', 0) or 0)
                
                good_issue_date = getattr(r, 'good_issue_date', None)
                pod_date = getattr(r, 'pod_date', None)
                
                if dn_no:
                    if good_issue_date and not pod_date:
                        pending_pod_dns.add(dn_no)
                    if pod_date:
                        delivered_dns.add(dn_no)
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Region query success: {region_type}={region or 'all'} -> {len(unique_dns)} DNs, {len(unique_dealers)} dealers ({elapsed:.3f}s)")
            
            result = self._format_success(
                {
                    "region": region or "All",
                    "region_type": region_type,
                    "total_dns": len(unique_dns),
                    "total_quantity": total_quantity,
                    "unique_dealers": len(unique_dealers),
                    "unique_warehouses": len(unique_warehouses),
                    "pending_pod": len(pending_pod_dns),
                    "delivered": len(delivered_dns),
                    "completion_rate": round((len(delivered_dns) / max(1, len(unique_dns))) * 100, 1)
                },
                f"Region ({region_type}): {len(unique_dns)} DNs, {len(unique_dealers)} dealers"
            )
            
            self.region_cache[cache_key] = result
            return result
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ Region performance error: {e} ({elapsed:.3f}s)")
            return self._format_error("Unable to retrieve region performance", is_user_error=False)
    
    # ==========================================================
    # POD & DELIVERY DASHBOARDS
    # ==========================================================
    
    def get_pod_status(self, region: str = None) -> Dict[str, Any]:
        """Get POD status with priority buckets"""
        start_time = time.time()
        logger.info(f"📋 POD Status Query: region={region or 'all'}")
        
        try:
            query = self.db.query(DeliveryReport)
            
            if region:
                if hasattr(DeliveryReport, 'division'):
                    query = query.filter(DeliveryReport.division == region)
                elif hasattr(DeliveryReport, 'warehouse'):
                    query = query.filter(DeliveryReport.warehouse.ilike(f"%{region}%"))
            
            records = query.all()
            
            pending_records = []
            for r in records:
                good_issue_date = getattr(r, 'good_issue_date', None)
                pod_date = getattr(r, 'pod_date', None)
                if good_issue_date and not pod_date:
                    pending_records.append(r)
            
            critical_dns = set()
            high_dns = set()
            medium_dns = set()
            low_dns = set()
            pending_list_dict = {}
            
            for r in pending_records:
                dn_no = self.normalize_dn(getattr(r, 'dn_no', None))
                if not dn_no:
                    continue
                
                good_issue_date = getattr(r, 'good_issue_date', None)
                days = self.calculate_pending_pod_days(good_issue_date)
                priority = self.calculate_priority(days)
                
                if priority == "CRITICAL":
                    critical_dns.add(dn_no)
                elif priority == "HIGH":
                    high_dns.add(dn_no)
                elif priority == "MEDIUM":
                    medium_dns.add(dn_no)
                else:
                    low_dns.add(dn_no)
                
                if dn_no not in pending_list_dict:
                    pending_list_dict[dn_no] = {
                        "dn_no": dn_no,
                        "dealer": getattr(r, 'customer_name', 'N/A'),
                        "pending_days": days,
                        "priority": priority
                    }
            
            pending_list = sorted(pending_list_dict.values(), key=lambda x: x["pending_days"], reverse=True)
            
            elapsed = time.time() - start_time
            logger.info(f"✅ POD Status success: {len(pending_list)} pending DNs ({len(critical_dns)} critical, {elapsed:.3f}s)")
            
            return self._format_success(
                {
                    "total_pending": len(pending_list),
                    "critical": len(critical_dns),
                    "high": len(high_dns),
                    "medium": len(medium_dns),
                    "low": len(low_dns),
                    "pending_list": pending_list[:20]
                },
                f"{len(pending_list)} PODs pending ({len(critical_dns)} critical)"
            )
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ POD status error: {e} ({elapsed:.3f}s)")
            return self._format_error("Unable to retrieve POD status", is_user_error=False)
    
    def get_pending_deliveries(self, days: int = None) -> Dict[str, Any]:
        """Get pending deliveries with priority buckets"""
        start_time = time.time()
        logger.info(f"🚚 Pending Deliveries Query: days={days or 'all'}")
        
        try:
            query = self.db.query(DeliveryReport)
            records = query.all()
            
            pending_records = []
            for r in records:
                good_issue_date = getattr(r, 'good_issue_date', None)
                if not good_issue_date:
                    dn_create_date = getattr(r, 'dn_create_date', None)
                    if days and dn_create_date:
                        cutoff = date.today() - timedelta(days=days)
                        if dn_create_date <= cutoff:
                            pending_records.append(r)
                    else:
                        pending_records.append(r)
            
            critical_dns = set()
            high_dns = set()
            medium_dns = set()
            low_dns = set()
            pending_list_dict = {}
            
            for r in pending_records:
                dn_no = self.normalize_dn(getattr(r, 'dn_no', None))
                if not dn_no:
                    continue
                
                dn_create_date = getattr(r, 'dn_create_date', None)
                days_pending = self.calculate_pending_delivery_days(dn_create_date)
                priority = self.calculate_priority(days_pending)
                
                if priority == "CRITICAL":
                    critical_dns.add(dn_no)
                elif priority == "HIGH":
                    high_dns.add(dn_no)
                elif priority == "MEDIUM":
                    medium_dns.add(dn_no)
                else:
                    low_dns.add(dn_no)
                
                if dn_no not in pending_list_dict:
                    pending_list_dict[dn_no] = {
                        "dn_no": dn_no,
                        "dealer": getattr(r, 'customer_name', 'N/A'),
                        "pending_days": days_pending,
                        "priority": priority
                    }
            
            pending_list = sorted(pending_list_dict.values(), key=lambda x: x["pending_days"], reverse=True)
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Pending Deliveries success: {len(pending_list)} pending DNs ({len(critical_dns)} critical, {elapsed:.3f}s)")
            
            return self._format_success(
                {
                    "total_pending": len(pending_list),
                    "critical": len(critical_dns),
                    "high": len(high_dns),
                    "medium": len(medium_dns),
                    "low": len(low_dns),
                    "pending_list": pending_list[:20]
                },
                f"{len(pending_list)} deliveries pending ({len(critical_dns)} critical)"
            )
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ Pending deliveries error: {e} ({elapsed:.3f}s)")
            return self._format_error("Unable to retrieve pending deliveries", is_user_error=False)
    
    # ==========================================================
    # SAMPLE DNS HELPER
    # ==========================================================
    
    def _get_sample_dns(self, limit: int = 5) -> List[str]:
        """Get sample DNs from database for error messages"""
        try:
            results = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.dn_no.isnot(None)
            ).distinct().limit(limit).all()
            return [str(r[0]) for r in results if r[0]]
        except:
            return []
    
    # ==========================================================
    # PROPER ERROR HANDLING
    # ==========================================================
    
    def _format_success(self, data: Any, summary: str) -> Dict[str, Any]:
        return {
            "success": True,
            "data": data,
            "_summary": summary
        }
    
    def _format_error(self, error: str, is_user_error: bool = True) -> Dict[str, Any]:
        """Format error - user errors shown, system errors hidden"""
        if is_user_error:
            return {
                "success": False,
                "data": {},
                "_summary": f"❌ {error}",
                "error": error
            }
        else:
            logger.error(f"System error: {error}")
            return {
                "success": False,
                "data": {},
                "_summary": "❌ Unable to process request. Please try again later.",
                "error": "System error"
            }
    
    # ==========================================================
    # EXPANDED HEALTH CHECK
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check for monitoring with KPIs"""
        start_time = time.time()
        
        db_connected = False
        table_exists = False
        row_count = 0
        distinct_dns = 0
        distinct_dealers = 0
        distinct_warehouses = 0
        
        try:
            self.db.execute(text("SELECT 1"))
            db_connected = True
            
            inspector = inspect(self.db.bind)
            table_exists = inspector.has_table(self.table_name)
            
            if table_exists:
                row_count = self._get_total_count()
                
                distinct_dns = self.db.query(func.count(func.distinct(DeliveryReport.dn_no))).scalar() or 0
                distinct_dealers = self.db.query(func.count(func.distinct(DeliveryReport.customer_name))).scalar() or 0
                
                if hasattr(DeliveryReport, 'warehouse'):
                    distinct_warehouses = self.db.query(func.count(func.distinct(DeliveryReport.warehouse))).scalar() or 0
                
        except Exception as e:
            logger.error(f"Health check failed: {e}")
        
        elapsed = time.time() - start_time
        
        return {
            "service": "logistics",
            "version": "9.3",
            "status": "healthy" if db_connected and table_exists else "unhealthy",
            "database_connected": db_connected,
            "table_exists": table_exists,
            "table_name": self.table_name,
            "row_count": row_count,
            "distinct_dns": distinct_dns,
            "distinct_dealers": distinct_dealers,
            "distinct_warehouses": distinct_warehouses,
            "cache_sizes": {
                "dealer_cache": len(self.dealer_cache),
                "warehouse_cache": len(self.warehouse_cache),
                "region_cache": len(self.region_cache)
            },
            "health_check_time_ms": round(elapsed * 1000, 2),
            "features": {
                "dn_aggregation": True,
                "business_rules_applied": True,
                "priority_buckets": True,
                "multi_stage_search": True,
                "model_alignment_validation": True,
                "schema_adaptive_fields": True,
                "smart_dn_not_found": True,
                "dynamic_status_calculation": True,
                "integer_dn_search": True
            }
        }
    
    # ==========================================================
    # COMPATIBILITY METHODS (Aliases) - All preserved
    # ==========================================================
    
    def get_dn_timeline(self, dn_number: str) -> Dict[str, Any]:
        result = self.get_complete_dn_intelligence(dn_number)
        if result.get("success"):
            dn_data = result.get("data", {})
            timeline = [
                {"status": "DN Created", "date": dn_data.get("date", "N/A")},
                {"status": "PGI Date", "date": dn_data.get("pgi_date", "N/A")},
                {"status": "POD Date", "date": dn_data.get("pod_date", "N/A")}
            ]
            return self._format_success(timeline, f"Timeline for DN {dn_number}")
        return result
    
    def get_dn_products(self, dn_number: str) -> Dict[str, Any]:
        result = self.get_complete_dn_intelligence(dn_number)
        if result.get("success"):
            products = result.get("data", {}).get("products", [])
            return self._format_success(products, f"Products in DN {dn_number}")
        return result
    
    def get_top_dealers(self, limit: int = 10) -> Dict[str, Any]:
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(func.distinct(DeliveryReport.dn_no)).label('dn_count')
            ).group_by(DeliveryReport.customer_name).order_by(
                func.count(func.distinct(DeliveryReport.dn_no)).desc()
            ).limit(limit).all()
            
            dealers = [{"name": r[0] or "Unknown", "dn_count": r[1]} for r in results if r[0]]
            return self._format_success(dealers, f"Top {len(dealers)} dealers")
        except Exception as e:
            return self._format_error("Unable to retrieve top dealers", is_user_error=False)
    
    def get_top_warehouses(self, limit: int = 10) -> Dict[str, Any]:
        try:
            if not hasattr(DeliveryReport, 'warehouse'):
                return self._format_error("Warehouse field not available", is_user_error=True)
            
            results = self.db.query(
                DeliveryReport.warehouse,
                func.count(func.distinct(DeliveryReport.dn_no)).label('dn_count')
            ).group_by(DeliveryReport.warehouse).order_by(
                func.count(func.distinct(DeliveryReport.dn_no)).desc()
            ).limit(limit).all()
            
            warehouses = [{"name": r[0] or "Unknown", "dn_count": r[1]} for r in results if r[0]]
            return self._format_success(warehouses, f"Top {len(warehouses)} warehouses")
        except Exception as e:
            return self._format_error("Unable to retrieve top warehouses", is_user_error=False)
    
    def get_top_products(self, limit: int = 10) -> Dict[str, Any]:
        try:
            results = self.db.query(
                DeliveryReport.material_no,
                DeliveryReport.customer_model,
                func.sum(DeliveryReport.dn_qty).label('total_qty')
            ).group_by(DeliveryReport.material_no, DeliveryReport.customer_model).order_by(
                func.sum(DeliveryReport.dn_qty).desc()
            ).limit(limit).all()
            
            products = [{"code": r[0] or "N/A", "name": r[1] or "N/A", "total_quantity": r[2] or 0} for r in results]
            return self._format_success(products, f"Top {len(products)} products")
        except Exception as e:
            return self._format_error("Unable to retrieve top products", is_user_error=False)
    
    def get_warehouse_performance(self, warehouse_name: str) -> Dict[str, Any]:
        return self.get_warehouse_status(warehouse_name)
    
    def get_region_information(self, region: str, region_type: str = "warehouse") -> Dict[str, Any]:
        return self.get_region_performance(region, region_type)
    
    def get_dealer_performance(self, dealer_name: str) -> Dict[str, Any]:
        return self.get_dealer_all_dns(dealer_name)
    
    def get_dealer_details(self, dealer_name: str) -> Dict[str, Any]:
        return self.get_dealer_all_dns(dealer_name)
    
    def get_pod_aging_report(self) -> Dict[str, Any]:
        return self.get_pod_status()
    
    def get_delivery_aging_report(self) -> Dict[str, Any]:
        return self.get_pending_deliveries()
    
    def get_pending_pgi(self, days: int = None) -> Dict[str, Any]:
        try:
            query = self.db.query(DeliveryReport)
            records = query.all()
            
            pending_count = 0
            for r in records:
                good_issue_date = getattr(r, 'good_issue_date', None)
                if not good_issue_date:
                    if days:
                        dn_create_date = getattr(r, 'dn_create_date', None)
                        if dn_create_date:
                            cutoff = date.today() - timedelta(days=days)
                            if dn_create_date <= cutoff:
                                pending_count += 1
                    else:
                        pending_count += 1
            
            return self._format_success(
                {"pending_count": pending_count, "pending_pgi": []},
                f"{pending_count} PGI pending"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve pending PGI", is_user_error=False)
    
    def get_pgi_aging_report(self) -> Dict[str, Any]:
        return self.get_pending_pgi()
    
    def get_pod_performance(self) -> Dict[str, Any]:
        try:
            records = self.db.query(DeliveryReport).all()
            
            total = len(records)
            completed = 0
            for r in records:
                pod_date = getattr(r, 'pod_date', None)
                if pod_date:
                    completed += 1
            
            rate = round((completed / max(1, total)) * 100, 1)
            return self._format_success(
                {"total": total, "completed": completed, "compliance_rate": rate, "target": 95},
                f"POD Compliance: {rate}% ({completed}/{total})"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve POD performance", is_user_error=False)
    
    def get_delivery_performance(self) -> Dict[str, Any]:
        try:
            records = self.db.query(DeliveryReport).all()
            
            total = len(records)
            completed = 0
            for r in records:
                good_issue_date = getattr(r, 'good_issue_date', None)
                pod_date = getattr(r, 'pod_date', None)
                if good_issue_date and pod_date:
                    completed += 1
            
            rate = round((completed / max(1, total)) * 100, 1)
            return self._format_success(
                {"total": total, "completed": completed, "on_time_rate": rate, "target": 95},
                f"Delivery Performance: {rate}% ({completed}/{total})"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve delivery performance", is_user_error=False)
    
    def get_pending_items(self, region: str = None) -> Dict[str, Any]:
        try:
            records = self.db.query(DeliveryReport).all()
            
            pending_count = 0
            for r in records:
                good_issue_date = getattr(r, 'good_issue_date', None)
                pod_date = getattr(r, 'pod_date', None)
                
                if not good_issue_date or (good_issue_date and not pod_date):
                    pending_count += 1
            
            return self._format_success(
                {"total_pending": pending_count},
                f"Total pending: {pending_count}"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve pending items", is_user_error=False)
    
    def get_dn_aging_report(self, dn_number: str) -> Dict[str, Any]:
        return self.get_complete_dn_intelligence(dn_number)
    
    def validate_dn(self, dn_number: str) -> bool:
        result = self.get_complete_dn_intelligence(dn_number)
        return result.get("success", False)
    
    def get_all_dns_list(self, limit: int = 20) -> Dict[str, Any]:
        try:
            results = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.dn_no.isnot(None)
            ).distinct().limit(limit).all()
            dns = [str(r[0]) for r in results if r[0]]
            return self._format_success(
                {"dns": dns, "total": len(dns)},
                f"Found {len(dns)} DNs"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve DNs", is_user_error=False)
    
    def debug_show_all_dns(self, limit: int = 30) -> Dict[str, Any]:
        return self.get_all_dns_list(limit)
    
    def debug_check_dn_exists(self, dn_number: str) -> Dict[str, Any]:
        result = self.get_complete_dn_intelligence(dn_number)
        return {
            "success": True,
            "data": {
                "dn_searched": dn_number,
                "found": result.get("success", False),
                "match_count": 1 if result.get("success") else 0
            },
            "_summary": f"DN {dn_number} {'FOUND' if result.get('success') else 'NOT FOUND'} in database"
        }
    
    def show_all_dns(self, limit: int = 20) -> Dict[str, Any]:
        return self.get_all_dns_list(limit)
    
    # ==========================================================
    # BUSINESS REQUIREMENT: Complete DN Detail for WhatsApp
    # ==========================================================
    
    def get_complete_dn_detail(self, dn_number: str) -> Dict[str, Any]:
        """Get complete DN detail formatted exactly for WhatsApp display"""
        result = self.get_complete_dn_intelligence(dn_number)
        
        if not result.get("success"):
            return result
        
        data = result.get("data", {})
        
        whatsapp_format = {
            "success": True,
            "data": {
                "dn_number": data.get("dn_no"),
                "dealer_name": data.get("dealer_name"),
                "sales_office": data.get("sales_office"),
                "warehouse": data.get("warehouse"),
                "dn_date": data.get("date"),
                "pgi_date": data.get("pgi_date"),
                "pod_date": data.get("pod_date"),
                "delivery_days": data.get("delivery_days"),
                "pod_days": data.get("pod_days"),
                "status": data.get("status"),
                "status_emoji": data.get("status_emoji"),
                "total_models": data.get("total_models"),
                "models_list": data.get("models_list"),
                "total_quantity": data.get("total_quantity"),
                "priority": data.get("priority")
            },
            "_summary": result.get("_summary")
        }
        
        return whatsapp_format


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(db: Session) -> LogisticsQueryService:
    return LogisticsQueryService(db)


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 70)
logger.info("📦 LOGISTICS QUERY SERVICE v9.3 - FIXED DN SEARCH")
logger.info("")
logger.info("   CRITICAL FIXES v9.3:")
logger.info("   ✅ IMPROVED DN SEARCH - Integer detection first")
logger.info("   ✅ Multiple search strategies (int, string, .0, contains)")
logger.info("   ✅ Better logging for DN search debugging")
logger.info("   ✅ Enhanced debug tools (verify_dn_column_type, test_dn_search_methods)")
logger.info("   ✅ Removed sold_to_party reference (not in models)")
logger.info("")
logger.info("   SEARCH STRATEGIES (in order):")
logger.info("   1. Integer match (for PostgreSQL integer columns)")
logger.info("   2. String exact match")
logger.info("   3. With .0 suffix")
logger.info("   4. Contains (last resort)")
logger.info("")
logger.info("   DEBUG TOOLS:")
logger.info("   - verify_dn_column_type() - Check actual DN column data type")
logger.info("   - test_dn_search_methods(dn) - Test each search strategy")
logger.info("   - debug_dn_search(dn) - Comprehensive debug info")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
