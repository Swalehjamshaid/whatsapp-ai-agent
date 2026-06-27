# ======================================================================================================
# FILE: app/services/dn_analysis.py
# VERSION: v13.1 - FIXED INITIALIZATION
# ======================================================================================================
# PURPOSE: DN Analytics Service - Direct PostgreSQL Integration
# ======================================================================================================

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
import threading
import re
import traceback
import time
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

# ======================================================================================================
# BLOCK 1: IMPORTS & DATABASE SETUP
# ======================================================================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Database models imported successfully")
except ImportError as e:
    logger.error(f"❌ Database import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

DEBUG_MODE = os.environ.get("DN_DEBUG_MODE", "false").lower() == "true"
PRODUCTION_MODE = os.environ.get("DN_PRODUCTION_MODE", "true").lower() == "true"

# Lazy load GIS libraries
GEO_AVAILABLE = False
OPENROUTE_API_KEY = os.environ.get("OPENROUTE_API_KEY", "")


def _lazy_load_gis():
    """Lazy load GIS libraries only when needed."""
    global GEO_AVAILABLE
    if GEO_AVAILABLE:
        return True
    
    try:
        import openrouteservice
        from geopy.geocoders import Nominatim
        from geopy.distance import geodesic
        GEO_AVAILABLE = True
        logger.info("✅ GIS libraries loaded successfully")
        return True
    except ImportError:
        logger.warning("⚠️ GIS libraries not available. Distance features will use estimation.")
        return False


# ======================================================================================================
# BLOCK 2: DNAnalysisService CLASS
# ======================================================================================================

class DNAnalysisService:
    """
    DN Analytics Service - Direct PostgreSQL Connection.
    
    v13.1 - FIXED INITIALIZATION:
    - ✅ PostgreSQL is the ONLY source of truth
    - ✅ Proper error handling during initialization
    - ✅ All public methods available
    - ✅ 100% backward compatible
    """
    
    # Class-level method registry for AI provider service
    _PUBLIC_METHODS = [
        "health_check",
        "validation_query",
        "get_service_metadata",
        "search_dn",
        "verify_dn",
        "get_dn_dashboard",
        "get_pending_dns",
        "get_pending_pgi",
        "get_pending_pod",
        "format_dn_dashboard",
        "diagnose_dn",
        "check_dn_raw",
        "test_dn_lookup",
        "test_date_calculation",
        "calculate_delivery_aging",
        "calculate_pod_aging",
        "calculate_total_cycle"
    ]
    
    def __init__(self):
        """Initialize DN Analytics Service with error handling."""
        self._service_name = "dn_analysis"
        self._version = "13.1"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0
        self._startup_time = datetime.now().isoformat()
        self._debug_mode = DEBUG_MODE
        self._production_mode = PRODUCTION_MODE
        self._schema_validated = False
        self._distance_calculator = None
        self._initialized = False
        
        logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
        
        try:
            # Register methods for AI provider
            self._register_methods()
            
            # Test connection
            test_result = self._test_connection()
            if test_result:
                self._status = "READY"
                self._initialized = True
                logger.info("✅ DNAnalysisService is READY")
            else:
                self._status = "ERROR"
                logger.error("❌ DNAnalysisService initialization FAILED")
                
        except Exception as e:
            self._status = "ERROR"
            logger.error(f"❌ DNAnalysisService initialization error: {e}")
            logger.error(traceback.format_exc())
    
    def _register_methods(self):
        """Register all public methods for AI provider service detection."""
        logger.info(f"📋 Registering {len(self._PUBLIC_METHODS)} public methods...")
        for method in self._PUBLIC_METHODS:
            if hasattr(self, method):
                logger.debug(f"   ✅ Method registered: {method}")
            else:
                logger.warning(f"   ⚠️ Method not found: {method}")
    
    def get_available_methods(self) -> List[str]:
        """Return list of available methods for AI provider service."""
        return self._PUBLIC_METHODS.copy()
    
    def is_ready(self) -> bool:
        """Check if service is ready."""
        return self._initialized and self._status == "READY"
    
    # ======================================================================================================
    # BLOCK 3: DATABASE CONNECTION METHODS
    # ======================================================================================================
    
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
        start_time = time.time()
        session = None
        try:
            session = self._get_session()
            if not session:
                logger.error("❌ No session available")
                return []
            
            if self._debug_mode:
                logger.debug(f"📝 Executing SQL: {query[:200]}...")
                logger.debug(f"📝 Parameters: {params}")
            
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            
            execution_time_ms = (time.time() - start_time) * 1000
            self._query_count += 1
            self._total_execution_time_ms += execution_time_ms
            
            if self._debug_mode:
                logger.debug(f"✅ Query returned {len(rows)} rows in {execution_time_ms:.2f}ms")
            return rows
            
        except Exception as e:
            logger.error(f"❌ SQL Execution Failed!")
            logger.error(f"   Query: {query[:500]}")
            if params:
                logger.error(f"   Parameters: {params}")
            logger.error(f"   Error: {str(e)}")
            if self._debug_mode:
                logger.error(f"   Traceback:\n{traceback.format_exc()}")
            return []
        finally:
            if session:
                session.close()
    
    # ======================================================================================================
    # BLOCK 4: DN VALIDATION & NORMALIZATION
    # ======================================================================================================
    
    def _normalize_dn(self, dn_no: str) -> str:
        """Normalize DN number for search."""
        if not dn_no:
            return ""
        
        # Trim spaces
        normalized = dn_no.strip()
        
        # Remove non-numeric characters
        normalized = re.sub(r'[^0-9]', '', normalized)
        
        if self._debug_mode:
            logger.debug(f"🔍 DN Normalization: '{dn_no}' → '{normalized}'")
        
        return normalized
    
    def _validate_dn(self, dn_no: str) -> Tuple[bool, str, str]:
        """Validate DN number."""
        if not dn_no:
            return False, "", "DN number required"
        
        normalized = self._normalize_dn(dn_no)
        
        if not normalized:
            return False, "", "DN must contain numeric characters"
        
        if len(normalized) < 8:
            return False, normalized, f"DN must be at least 8 digits (got {len(normalized)})"
        
        if len(normalized) > 12:
            return False, normalized, f"DN cannot exceed 12 digits (got {len(normalized)})"
        
        return True, normalized, None
    
    # ======================================================================================================
    # BLOCK 5: SQL ENGINE - SEARCH
    # ======================================================================================================
    
    def _build_search_query(self) -> str:
        """Build optimized search query - returns ALL rows for a DN."""
        return """
            SELECT 
                id,
                dn_no,
                dn_work,
                order_type,
                division,
                customer_code,
                dealer_code,
                customer_name,
                customer_model,
                material_no,
                storage_location,
                sales_office,
                sales_manager,
                ship_to_city,
                warehouse,
                warehouse_code,
                delivery_location,
                dn_qty,
                dn_amount,
                dn_create_date,
                good_issue_date,
                pod_date,
                remarks,
                delivery_status,
                pgi_status,
                pod_status,
                pending_flag,
                source_file,
                upload_batch_id,
                imported_at,
                created_at,
                updated_at
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
            ORDER BY customer_model ASC, id ASC
        """
    
    def _build_similar_dn_query(self) -> str:
        """Build query to find similar DNs for error messaging."""
        return """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
    
    # ======================================================================================================
    # BLOCK 6: DATE ENGINE
    # ======================================================================================================
    
    def _format_display_date(self, date_value) -> str:
        """Format date for display (YYYY-MM-DD)."""
        if date_value is None:
            return 'N/A'
        
        try:
            if isinstance(date_value, (date, datetime)):
                return date_value.strftime('%Y-%m-%d')
            elif isinstance(date_value, str):
                if len(date_value) == 10 and date_value[4] == '-' and date_value[7] == '-':
                    return date_value
                parsed = datetime.strptime(date_value, "%Y-%m-%d")
                return parsed.strftime('%Y-%m-%d')
            else:
                return str(date_value)
        except (ValueError, TypeError):
            return str(date_value) if date_value else 'N/A'
    
    def _parse_date(self, date_value):
        """Parse date without conversion."""
        if not date_value:
            return None
        
        if isinstance(date_value, (date, datetime)):
            return date_value
        
        if isinstance(date_value, str):
            try:
                return datetime.strptime(date_value, "%Y-%m-%d")
            except ValueError:
                pass
        
        return None
    
    def _safe_date_diff(self, date1, date2) -> int:
        """Safely calculate days between two dates."""
        if date1 is None or date2 is None:
            return 0
        
        try:
            if not isinstance(date1, (date, datetime)):
                return 0
            if not isinstance(date2, (date, datetime)):
                return 0
            
            if isinstance(date1, datetime):
                date1 = date1.date()
            if isinstance(date2, datetime):
                date2 = date2.date()
            
            delta = date2 - date1
            days = delta.days
            return max(0, days)
            
        except Exception as e:
            if self._debug_mode:
                logger.error(f"❌ Failed to calculate date difference: {e}")
            return 0
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        """Calculate delivery aging from PostgreSQL dates."""
        try:
            if dn_create_date is None:
                return 0
            
            dn_date = self._parse_date(dn_create_date)
            if dn_date is None:
                return 0
            
            if good_issue_date is None:
                current_date = datetime.now().date()
                return self._safe_date_diff(dn_date, current_date)
            
            gi_date = self._parse_date(good_issue_date)
            if gi_date is None:
                return 0
            
            return self._safe_date_diff(dn_date, gi_date)
            
        except Exception as e:
            if self._debug_mode:
                logger.error(f"❌ Failed to calculate delivery aging: {e}")
            return 0
    
    def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
        """Calculate POD aging from PostgreSQL dates."""
        try:
            if good_issue_date is None:
                return 0
            
            gi_date = self._parse_date(good_issue_date)
            if gi_date is None:
                return 0
            
            if pod_date is None:
                current_date = datetime.now().date()
                return self._safe_date_diff(gi_date, current_date)
            
            pd_date = self._parse_date(pod_date)
            if pd_date is None:
                return 0
            
            return self._safe_date_diff(gi_date, pd_date)
            
        except Exception as e:
            if self._debug_mode:
                logger.error(f"❌ Failed to calculate POD aging: {e}")
            return 0
    
    def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
        """Calculate total cycle from PostgreSQL dates."""
        try:
            if dn_create_date is None:
                return 0
            
            dn_date = self._parse_date(dn_create_date)
            if dn_date is None:
                return 0
            
            if pod_date is None:
                current_date = datetime.now().date()
                return self._safe_date_diff(dn_date, current_date)
            
            pd_date = self._parse_date(pod_date)
            if pd_date is None:
                return 0
            
            return self._safe_date_diff(dn_date, pd_date)
            
        except Exception as e:
            if self._debug_mode:
                logger.error(f"❌ Failed to calculate total cycle: {e}")
            return 0
    
    def _format_aging_text(self, days: int) -> str:
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
        elif days < 365:
            return f"{days} Days ({days // 30} Months)"
        else:
            years = days // 365
            months = (days % 365) // 30
            if months > 0:
                return f"{days} Days ({years} Year{'s' if years > 1 else ''}, {months} Month{'s' if months > 1 else ''})"
            return f"{days} Days ({years} Year{'s' if years > 1 else ''})"
    
    # ======================================================================================================
    # BLOCK 7: SEARCH ENGINE - SINGLE SOURCE OF TRUTH
    # ======================================================================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """SEARCH ENGINE - Returns ALL rows belonging to a DN."""
        start_time = time.time()
        logger.info(f"🔍 SEARCH ENGINE: '{dn_no}'")
        
        # STEP 1: Validate DN
        is_valid, normalized_dn, error_msg = self._validate_dn(dn_no)
        if not is_valid:
            logger.warning(f"❌ Invalid DN: {error_msg}")
            return {"success": False, "error": error_msg}
        
        logger.info(f"   ├── Normalized: '{normalized_dn}'")
        
        # STEP 2: Exact PostgreSQL Search
        query = self._build_search_query()
        results = self._execute_query(query, {"dn_no": normalized_dn})
        
        if results:
            logger.info(f"   ├── Found {len(results)} rows for DN")
            
            # STEP 3: Aggregate ALL rows
            aggregated_data = self._aggregate_dn_rows(results, normalized_dn)
            
            # STEP 4: Build complete dashboard
            dashboard = self._build_dashboard_from_aggregated_data(aggregated_data, normalized_dn)
            
            execution_time = (time.time() - start_time) * 1000
            logger.info(f"   ├── Aggregated {len(results)} rows into dashboard")
            logger.info(f"   ├── Materials: {dashboard.get('material_count', 0)}")
            logger.info(f"   ├── Models: {dashboard.get('model_count', 0)}")
            logger.info(f"   ├── Units: {dashboard.get('total_units', 0)}")
            logger.info(f"   ├── Revenue: PKR {dashboard.get('total_revenue', 0):,.0f}")
            logger.info(f"   ├── Status: {dashboard.get('calculated_stage', 'Unknown')}")
            logger.info(f"✅ Search completed in {execution_time:.2f}ms")
            
            return {"success": True, "data": dashboard}
        
        # STEP 5: Similar DNs for error message
        similar_query = self._build_similar_dn_query()
        similar_results = self._execute_query(similar_query, {"dn_no": normalized_dn})
        similar_dns = [str(r.get('dn_no', '')) for r in similar_results if r.get('dn_no')]
        
        if similar_dns:
            logger.info(f"   ├── Similar DNs: {similar_dns[:5]}")
            return {
                "success": False,
                "error": f"DN {dn_no} not found",
                "similar_dns": similar_dns[:5],
                "message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
            }
        
        execution_time = (time.time() - start_time) * 1000
        logger.warning(f"❌ DN {dn_no} not found in {execution_time:.2f}ms")
        return {"success": False, "error": f"DN {dn_no} not found"}
    
    def _aggregate_dn_rows(self, rows: List[Dict[str, Any]], dn_no: str) -> Dict[str, Any]:
        """Aggregate ALL rows belonging to a DN."""
        if not rows:
            return {}
        
        first_row = rows[0]
        
        # Collect unique values
        unique_models = set()
        unique_materials = set()
        products = []
        total_units = 0
        total_revenue = 0
        
        # Get date values
        dn_create_dates = []
        good_issue_dates = []
        pod_dates = []
        
        for row in rows:
            # Collect models
            model = row.get('customer_model')
            if model:
                unique_models.add(model)
            
            # Collect materials
            material = row.get('material_no')
            if material:
                unique_materials.add(material)
            
            # Collect products
            if model:
                qty = int(row.get('dn_qty', 0) or 0)
                revenue = float(row.get('dn_amount', 0) or 0)
                total_units += qty
                total_revenue += revenue
                
                products.append({
                    'model': str(model),
                    'material_no': str(row.get('material_no', 'N/A')),
                    'division': str(row.get('division', 'Unknown')),
                    'quantity': qty,
                    'revenue': revenue,
                    'warehouse': str(row.get('warehouse', 'Unknown')),
                    'city': str(row.get('ship_to_city', 'Unknown')),
                })
            
            # Collect dates
            if row.get('dn_create_date'):
                dn_create_dates.append(row.get('dn_create_date'))
            if row.get('good_issue_date'):
                good_issue_dates.append(row.get('good_issue_date'))
            if row.get('pod_date'):
                pod_dates.append(row.get('pod_date'))
        
        # Determine min/max dates
        dn_create_date = min(dn_create_dates) if dn_create_dates else None
        good_issue_date = max(good_issue_dates) if good_issue_dates else None
        pod_date = max(pod_dates) if pod_dates else None
        
        # Sort products by model
        products.sort(key=lambda x: x.get('model', ''))
        
        return {
            "dn_no": first_row.get('dn_no', dn_no),
            "customer_name": first_row.get('customer_name'),
            "dealer_name": first_row.get('customer_name'),
            "dealer_code": first_row.get('dealer_code'),
            "customer_code": first_row.get('customer_code'),
            "warehouse": first_row.get('warehouse'),
            "warehouse_code": first_row.get('warehouse_code'),
            "city": first_row.get('ship_to_city'),
            "delivery_location": first_row.get('delivery_location'),
            "sales_manager": first_row.get('sales_manager'),
            "sales_office": first_row.get('sales_office'),
            "division": first_row.get('division'),
            "total_units": total_units,
            "total_revenue": total_revenue,
            "material_count": len(unique_materials),
            "model_count": len(unique_models),
            "row_count": len(rows),
            "dn_create_date": dn_create_date,
            "good_issue_date": good_issue_date,
            "pod_date": pod_date,
            "products": products,
            "source_file": first_row.get('source_file'),
            "upload_batch_id": first_row.get('upload_batch_id'),
            "created_at": first_row.get('created_at'),
            "updated_at": first_row.get('updated_at'),
        }
    
    def _build_dashboard_from_aggregated_data(self, aggregated_data: Dict[str, Any], dn_no: str) -> Dict[str, Any]:
        """Build complete dashboard from aggregated data."""
        if not aggregated_data:
            return {}
        
        # Extract raw dates
        raw_dn_create_date = aggregated_data.get('dn_create_date')
        raw_good_issue_date = aggregated_data.get('good_issue_date')
        raw_pod_date = aggregated_data.get('pod_date')
        
        # Calculate aging
        delivery_aging = self.calculate_delivery_aging(raw_dn_create_date, raw_good_issue_date)
        pod_aging = self.calculate_pod_aging(raw_good_issue_date, raw_pod_date)
        total_cycle = self.calculate_total_cycle(raw_dn_create_date, raw_pod_date)
        
        # Format dates
        formatted_dn_create = self._format_display_date(raw_dn_create_date)
        formatted_good_issue = self._format_display_date(raw_good_issue_date)
        formatted_pod = self._format_display_date(raw_pod_date)
        
        # Determine status from dates
        pgi_exists = raw_good_issue_date is not None
        pod_exists = raw_pod_date is not None
        
        if pod_exists and pgi_exists:
            calculated_stage = "Delivered"
            calculated_emoji = "✅"
            pgi_status = "Completed"
            pod_status = "Completed"
            pending_flag = False
            pending_flag_text = "🟢 No"
        elif pgi_exists and not pod_exists:
            calculated_stage = "In Transit"
            calculated_emoji = "🚚"
            pgi_status = "Completed"
            pod_status = "Pending"
            pending_flag = True
            pending_flag_text = "⚠️ Yes"
        else:
            calculated_stage = "Pending Dispatch"
            calculated_emoji = "⏳"
            pgi_status = "Pending"
            pod_status = "Pending"
            pending_flag = True
            pending_flag_text = "⚠️ Yes"
        
        # Build products list
        products = []
        for product in aggregated_data.get('products', []):
            products.append({
                'name': product.get('model', 'Unknown'),
                'material_no': product.get('material_no', 'N/A'),
                'division': product.get('division', 'Unknown'),
                'qty': product.get('quantity', 0),
                'revenue': product.get('revenue', 0),
                'warehouse': product.get('warehouse', 'Unknown'),
                'city': product.get('city', 'Unknown'),
            })
        
        # Build complete dashboard
        return {
            "dn_no": aggregated_data.get('dn_no', dn_no),
            "dealer_name": aggregated_data.get('dealer_name', 'Unknown'),
            "dealer_code": aggregated_data.get('dealer_code'),
            "customer_name": aggregated_data.get('customer_name', 'Unknown'),
            "customer_code": aggregated_data.get('customer_code'),
            "warehouse": aggregated_data.get('warehouse', 'Unknown'),
            "warehouse_code": aggregated_data.get('warehouse_code'),
            "city": aggregated_data.get('city', 'Unknown'),
            "delivery_location": aggregated_data.get('delivery_location'),
            "sales_manager": aggregated_data.get('sales_manager'),
            "sales_office": aggregated_data.get('sales_office'),
            "division": aggregated_data.get('division'),
            "total_units": aggregated_data.get('total_units', 0),
            "total_revenue": aggregated_data.get('total_revenue', 0),
            "material_count": aggregated_data.get('material_count', 0),
            "model_count": aggregated_data.get('model_count', 0),
            "row_count": aggregated_data.get('row_count', 0),
            "dn_create_date": formatted_dn_create,
            "good_issue_date": formatted_good_issue,
            "pod_date": formatted_pod,
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "total_cycle_days": total_cycle,
            "delivery_aging_text": self._format_aging_text(delivery_aging),
            "pod_aging_text": self._format_aging_text(pod_aging),
            "total_cycle_text": self._format_aging_text(total_cycle),
            "calculated_stage": calculated_stage,
            "calculated_emoji": calculated_emoji,
            "delivery_status": calculated_stage,
            "pgi_status": pgi_status,
            "pod_status": pod_status,
            "pending_flag": pending_flag,
            "pending_flag_text": pending_flag_text,
            "products": products,
            "source_file": aggregated_data.get('source_file'),
            "upload_batch_id": aggregated_data.get('upload_batch_id'),
            "created_at": aggregated_data.get('created_at'),
            "updated_at": aggregated_data.get('updated_at'),
        }
    
    # ======================================================================================================
    # BLOCK 8: VERIFY DN
    # ======================================================================================================
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        """Verify if DN exists using search engine."""
        logger.info(f"🔍 Verifying DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "exists": False, "error": "DN number required"}
        
        search_result = self.search_dn(dn_no)
        exists = search_result.get("success", False)
        
        logger.info(f"✅ DN {dn_no} exists: {exists}")
        return {"success": True, "exists": exists}
    
    # ======================================================================================================
    # BLOCK 9: GET DN DASHBOARD
    # ======================================================================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Get complete DN dashboard."""
        logger.info(f"📊 Building dashboard for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        search_result = self.search_dn(dn_no)
        
        if not search_result.get("success"):
            similar_dns = search_result.get("similar_dns", [])
            if similar_dns:
                return {
                    "success": False,
                    "error": f"DN {dn_no} not found. Similar: {', '.join(similar_dns[:3])}"
                }
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        dashboard = search_result.get("data", {})
        logger.info(f"✅ Dashboard built for DN {dn_no}")
        return {"success": True, "data": dashboard}
    
    # ======================================================================================================
    # BLOCK 10: HEALTH CHECK
    # ======================================================================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Validate service readiness."""
        logger.info("🔍 Running health check...")
        
        result = {
            "healthy": False,
            "service": self._service_name,
            "version": self._version,
            "database": "disconnected",
            "errors": [],
            "warnings": [],
            "timestamp": datetime.now().isoformat(),
            "query_count": self._query_count,
            "total_execution_time_ms": self._total_execution_time_ms,
            "available_methods": self.get_available_methods(),
            "initialized": self._initialized,
            "status": self._status
        }
        
        try:
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                logger.error("❌ Health check failed: SessionLocal not available")
                return result
            
            # Test connection
            session = None
            try:
                session = SessionLocal()
                session.execute(text("SELECT 1"))
                result["database"] = "connected"
                logger.info("✅ Database connection: connected")
            except Exception as e:
                result["errors"].append(f"Connection failed: {str(e)}")
                logger.error(f"❌ Database connection failed: {e}")
                return result
            finally:
                if session:
                    session.close()
            
            # Test query
            session = None
            try:
                session = self._get_session()
                if session:
                    test_query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports LIMIT 1"
                    session.execute(text(test_query))
                    logger.info("✅ Test query executed successfully")
            except Exception as e:
                result["errors"].append(f"Test query failed: {str(e)}")
                logger.error(f"❌ Test query failed: {e}")
                return result
            finally:
                if session:
                    session.close()
            
            result["healthy"] = True
            self._status = "READY"
            self._initialized = True
            
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
        
        session = None
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
            "description": "DN Analytics Service - Single Source of Truth",
            "date_policy": "Native PostgreSQL DATE values (YYYY-MM-DD)",
            "search_policy": "Index-first, fallback-second",
            "aggregation_policy": "ALL rows per DN",
            "debug_mode": self._debug_mode,
            "initialized": self._initialized,
            "available_methods": self.get_available_methods(),
            "methods": self._PUBLIC_METHODS
        }
    
    # ======================================================================================================
    # BLOCK 11: PENDING QUERIES
    # ======================================================================================================
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get all pending DNs."""
        logger.info(f"🔍 Getting pending DNs (limit: {limit}, offset: {offset})")
        
        try:
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
                
                pending_flag = row.get('pending_flag')
                pending_flag_text = '⚠️ Yes' if pending_flag else '🟢 No'
                
                formatted_results.append({
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": self._format_display_date(row.get('dn_create_date')),
                    "good_issue_date": self._format_display_date(row.get('good_issue_date')),
                    "pod_date": self._format_display_date(row.get('pod_date')),
                    "delivery_status": row.get('delivery_status') or "Pending",
                    "pending_flag": pending_flag,
                    "pending_flag_text": pending_flag_text,
                    "delivery_aging_days": delivery_aging,
                    "delivery_aging_text": self._format_aging_text(delivery_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                })
            
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
                formatted_results.append({
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": self._format_display_date(row.get('dn_create_date')),
                    "good_issue_date": self._format_display_date(row.get('good_issue_date')),
                    "pod_date": self._format_display_date(row.get('pod_date')),
                    "delivery_status": row.get('delivery_status') or "Pending",
                    "pending_flag": row.get('pending_flag'),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                })
            
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
                
                formatted_results.append({
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": self._format_display_date(row.get('dn_create_date')),
                    "good_issue_date": self._format_display_date(row.get('good_issue_date')),
                    "pod_date": self._format_display_date(row.get('pod_date')),
                    "delivery_status": "In Transit",
                    "pending_flag": row.get('pending_flag'),
                    "pod_aging_days": pod_aging,
                    "pod_aging_text": self._format_aging_text(pod_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                })
            
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
    
    # ======================================================================================================
    # BLOCK 12: FORMAT DN DASHBOARD
    # ======================================================================================================
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """Format DN dashboard for WhatsApp response."""
        data = dashboard_data.get('data', {})
        
        lines = []
        
        # Header
        lines.append("📦 *DN: {}*".format(data.get('dn_no', 'N/A')))
        lines.append("")
        
        # Dealer
        dealer_name = data.get('dealer_name', 'Unknown')
        if dealer_name:
            lines.append("*Dealer:*")
            lines.append("{}".format(dealer_name))
            lines.append("")
        
        # Warehouse
        warehouse = data.get('warehouse', 'Unknown')
        if warehouse:
            lines.append("*Warehouse:*")
            lines.append("{}".format(warehouse))
            lines.append("")
        
        # City
        city = data.get('city', 'Unknown')
        if city:
            lines.append("*City:*")
            lines.append("{}".format(city))
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
        
        # Dealer Code
        dealer_code = data.get('dealer_code')
        if dealer_code:
            lines.append("*Dealer Code:*")
            lines.append("{}".format(dealer_code))
            lines.append("")
        
        # Warehouse Code
        warehouse_code = data.get('warehouse_code')
        if warehouse_code:
            lines.append("*Warehouse Code:*")
            lines.append("{}".format(warehouse_code))
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
        lines.append("Materials: {}".format(data.get('material_count', 1)))
        model_count = data.get('model_count', 0)
        if model_count > 0:
            lines.append("Models: {}".format(model_count))
        lines.append("")
        
        # Dates
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
        calculated_stage = data.get('calculated_stage', 'Unknown')
        calculated_emoji = data.get('calculated_emoji', '❓')
        pgi_status = data.get('pgi_status', 'Unknown')
        pod_status = data.get('pod_status', 'Unknown')
        pending_flag_text = data.get('pending_flag_text', 'Unknown')
        
        lines.append("*📋 Status:*")
        lines.append("Delivery: {} {}".format(calculated_emoji, calculated_stage))
        lines.append("PGI: {}".format(pgi_status))
        lines.append("POD: {}".format(pod_status))
        lines.append("Pending: {}".format(pending_flag_text))
        lines.append("")
        
        # Products
        products = data.get('products', [])
        if products:
            lines.append("*📦 Product Details:*")
            for idx, product in enumerate(products[:10], 1):
                model_name = product.get('name', 'Unknown')
                material_no = product.get('material_no', 'N/A')
                qty = product.get('qty', 0)
                
                lines.append("{}. {}: {} units".format(idx, model_name, qty))
                if material_no != 'N/A':
                    lines.append("   Material: {}".format(material_no))
            
            if len(products) > 10:
                remaining = len(products) - 10
                total_units_remaining = sum(p.get('qty', 0) for p in products[10:])
                lines.append("... and {} more models ({} units)".format(remaining, total_units_remaining))
            lines.append("")
        
        return "\n".join(lines)
    
    # ======================================================================================================
    # BLOCK 13: DIAGNOSTIC METHODS
    # ======================================================================================================
    
    def diagnose_dn(self, dn_no: str) -> Dict[str, Any]:
        """Diagnose DN issues."""
        logger.info(f"🔬 Diagnosing DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        is_valid, normalized_dn, error_msg = self._validate_dn(dn_no)
        if not is_valid:
            return {"success": False, "error": error_msg}
        
        result = {
            "dn": dn_no,
            "normalized": normalized_dn,
            "exact_match_count": 0,
            "similar_dns": [],
            "exists": False,
            "diagnostic": []
        }
        
        # Exact match
        exact_query = "SELECT COUNT(*) as count FROM delivery_reports WHERE CAST(dn_no AS TEXT) = :dn_no"
        exact_results = self._execute_query(exact_query, {"dn_no": normalized_dn})
        exact_count = exact_results[0].get('count', 0) if exact_results else 0
        result["exact_match_count"] = exact_count
        result["exists"] = exact_count > 0
        result["diagnostic"].append(f"Exact match: {exact_count} found")
        
        # Similar DNs
        similar_query = self._build_similar_dn_query()
        similar_results = self._execute_query(similar_query, {"dn_no": normalized_dn})
        similar_dns = [str(r.get('dn_no', '')) for r in similar_results if r.get('dn_no')]
        result["similar_dns"] = similar_dns[:10]
        result["diagnostic"].append(f"Similar DNs: {len(similar_dns)} found")
        
        logger.info(f"✅ Diagnosis complete for {dn_no}")
        return {"success": True, "data": result}
    
    def check_dn_raw(self, dn_no: str) -> Dict[str, Any]:
        """Check raw DN existence without any normalization."""
        logger.info(f"🔍 Checking raw DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        query = self._build_similar_dn_query()
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
        logger.info(f"🔬 Testing DN lookup: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        is_valid, normalized_dn, error_msg = self._validate_dn(dn_no)
        if not is_valid:
            return {"success": False, "error": error_msg}
        
        results = {
            "dn": dn_no,
            "normalized": normalized_dn,
            "exact_count": 0,
            "matching_dns": [],
            "diagnostics": []
        }
        
        # Exact match count
        query1 = "SELECT COUNT(*) as count FROM delivery_reports WHERE CAST(dn_no AS TEXT) = :dn_no"
        r1 = self._execute_query(query1, {"dn_no": normalized_dn})
        results["exact_count"] = r1[0].get('count', 0) if r1 else 0
        results["diagnostics"].append(f"Exact match: {results['exact_count']}")
        
        # Matching DNs
        query2 = self._build_similar_dn_query()
        r2 = self._execute_query(query2, {"dn_no": normalized_dn})
        results["matching_dns"] = [str(r.get('dn_no', '')) for r in r2 if r.get('dn_no')]
        
        results["found"] = results["exact_count"] > 0
        results["diagnostics"].append(f"Total matching DNs: {len(results['matching_dns'])}")
        
        logger.info(f"✅ Test DN lookup complete: found={results['found']}")
        return {"success": True, "data": results}
    
    def test_date_calculation(self) -> Dict[str, Any]:
        """Regression tests for date calculations."""
        logger.info("🧪 Running regression tests...")
        
        from datetime import date as date_type
        
        test_results = []
        all_passed = True
        
        # Test 1
        tc1_dn_create = date_type(2026, 5, 5)
        tc1_pgi = date_type(2026, 5, 7)
        tc1_pod = date_type(2026, 5, 25)
        
        tc1_delivery = self.calculate_delivery_aging(tc1_dn_create, tc1_pgi)
        tc1_pod_aging = self.calculate_pod_aging(tc1_pgi, tc1_pod)
        tc1_total = self.calculate_total_cycle(tc1_dn_create, tc1_pod)
        
        tc1_passed = (tc1_delivery == 2 and tc1_pod_aging == 18 and tc1_total == 20)
        if not tc1_passed:
            all_passed = False
        
        test_results.append({
            "name": "Test 1: 2026-05-05, 2026-05-07, 2026-05-25",
            "expected": {"delivery": 2, "pod": 18, "total": 20},
            "actual": {"delivery": tc1_delivery, "pod": tc1_pod_aging, "total": tc1_total},
            "passed": tc1_passed
        })
        
        # Test 2
        tc2_dn_create = date_type(2026, 5, 23)
        tc2_pgi = date_type(2026, 5, 24)
        tc2_pod = date_type(2026, 5, 25)
        
        tc2_delivery = self.calculate_delivery_aging(tc2_dn_create, tc2_pgi)
        tc2_pod_aging = self.calculate_pod_aging(tc2_pgi, tc2_pod)
        tc2_total = self.calculate_total_cycle(tc2_dn_create, tc2_pod)
        
        tc2_passed = (tc2_delivery == 1 and tc2_pod_aging == 1 and tc2_total == 2)
        if not tc2_passed:
            all_passed = False
        
        test_results.append({
            "name": "Test 2: 2026-05-23, 2026-05-24, 2026-05-25",
            "expected": {"delivery": 1, "pod": 1, "total": 2},
            "actual": {"delivery": tc2_delivery, "pod": tc2_pod_aging, "total": tc2_total},
            "passed": tc2_passed
        })
        
        # Build result
        result = {
            "test_name": "Regression Tests - Native PostgreSQL Dates",
            "date_policy": "YYYY-MM-DD (Native PostgreSQL)",
            "tests": test_results,
            "all_passed": all_passed,
            "total_tests": len(test_results),
            "passed_tests": sum(1 for t in test_results if t.get("passed", False)),
            "timestamp": datetime.now().isoformat()
        }
        
        # Log results
        logger.info("=" * 70)
        logger.info("🧪 REGRESSION TEST RESULTS")
        logger.info("=" * 70)
        
        for i, test in enumerate(result["tests"], 1):
            status = "✅ PASSED" if test["passed"] else "❌ FAILED"
            logger.info(f"{status} - {test['name']}")
            if "expected" in test and "actual" in test:
                logger.info(f"   Expected: {test['expected']}")
                logger.info(f"   Actual:   {test['actual']}")
        
        logger.info(f"Overall Result: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
        logger.info("=" * 70)
        
        return result


# ======================================================================================================
# BLOCK 14: THREAD-SAFE SINGLETON
# ======================================================================================================

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


# ======================================================================================================
# BLOCK 15: EXPORTS
# ======================================================================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service'
]


# ======================================================================================================
# BLOCK 16: MODULE INITIALIZATION
# ======================================================================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v13.1 - FIXED INITIALIZATION")
logger.info("=" * 70)
logger.info("")
logger.info("   SERVICE DETAILS:")
logger.info("   ✅ Service Name: dn_analysis")
logger.info("   ✅ Version: 13.1")
logger.info("   ✅ Source: PostgreSQL (delivery_reports)")
logger.info("   ✅ Compatible: ai_provider_service.py v5.0")
logger.info("")
logger.info("   PUBLIC METHODS:")
logger.info("   ✅ health_check()")
logger.info("   ✅ validation_query()")
logger.info("   ✅ get_service_metadata()")
logger.info("   ✅ search_dn()")
logger.info("   ✅ verify_dn()")
logger.info("   ✅ get_dn_dashboard()")
logger.info("   ✅ get_pending_dns()")
logger.info("   ✅ get_pending_pgi()")
logger.info("   ✅ get_pending_pod()")
logger.info("   ✅ format_dn_dashboard()")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)

# Initialize service
try:
    service = get_dn_analytics_service()
    if service.is_ready():
        logger.info("✅ DN Analytics Service initialized successfully")
    else:
        logger.warning("⚠️ DN Analytics Service initialized with errors")
except Exception as e:
    logger.error(f"❌ DN Analytics Service initialization failed: {e}")
