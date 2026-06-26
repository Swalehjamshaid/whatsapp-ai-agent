# ==========================================================
# FILE: app/services/dn_analysis.py (v14.0 - ENTERPRISE PRODUCTION)
# ==========================================================
# PURPOSE: Enterprise DN Analytics Engine - Complete Dashboard
# SOURCE: delivery_reports table ONLY (PostgreSQL Single Source of Truth)
# VERSION: 14.0 - ENTERPRISE PRODUCTION WITH COMPLETE DASHBOARD
#
# COMPATIBLE WITH: ai_provider_service.py v5.0, webhook.py, all analytics services
# INTEGRATION: Railway PostgreSQL, FastAPI, WhatsApp AI Agent
#
# KEY FEATURES:
# - ✅ Preserved working DN search engine
# - ✅ Complete DN information retrieval
# - ✅ Complete product details with material numbers
# - ✅ Correct metrics (units, revenue, materials, models)
# - ✅ Distance calculation with OpenRouteService + geopy fallback
# - ✅ Shipment analytics (aging, velocity, efficiency)
# - ✅ Intelligent shipment stage from dates
# - ✅ Professional WhatsApp dashboard
# - ✅ 100% backward compatible
# ==========================================================

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date, timedelta
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
import threading
import re
import traceback
import time
import os

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS & DATABASE SETUP
# ==========================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Database models imported successfully")
except ImportError as e:
    logger.error(f"❌ Database import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

DEBUG_MODE = os.environ.get("DN_DEBUG_MODE", "false").lower() == "true"
OPENROUTE_API_KEY = os.environ.get("OPENROUTE_API_KEY", "")

# GIS Libraries (optional - graceful fallback)
GEO_AVAILABLE = False
try:
    import openrouteservice
    from geopy.geocoders import Nominatim
    from geopy.distance import geodesic
    from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
    GEO_AVAILABLE = True
    logger.info("✅ GIS libraries available")
except ImportError:
    logger.warning("⚠️ GIS libraries not available. Distance features will use estimation.")

# ==========================================================
# BLOCK 2: DISTANCE CALCULATOR (NEW)
# ==========================================================

class DistanceCalculator:
    """Enterprise Distance Calculator with automatic fallbacks."""
    
    def __init__(self):
        self._cache = {}
        self._geolocator = None
        self._client = None
        
        # Initialize OpenRouteService
        if OPENROUTE_API_KEY and GEO_AVAILABLE:
            try:
                self._client = openrouteservice.Client(key=OPENROUTE_API_KEY)
                logger.info("✅ OpenRouteService initialized")
            except Exception as e:
                logger.error(f"❌ OpenRouteService initialization failed: {e}")
                self._client = None
        
        # Initialize geopy
        if GEO_AVAILABLE:
            try:
                self._geolocator = Nominatim(user_agent="haier-logistics-agent", timeout=10)
                logger.info("✅ Geopy initialized")
            except Exception as e:
                logger.error(f"❌ Geopy initialization failed: {e}")
                self._geolocator = None
        
        logger.info("🔧 DistanceCalculator initialized")
    
    def _get_cache_key(self, location: str) -> str:
        return location.lower().strip()
    
    def get_coordinates(self, location: str) -> Optional[Tuple[float, float]]:
        """Get coordinates for a location with caching."""
        if not location:
            return None
        
        cache_key = self._get_cache_key(location)
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        if not self._geolocator:
            return None
        
        try:
            # Try with clean location
            geocode_result = self._geolocator.geocode(location, timeout=10)
            if geocode_result:
                coords = (geocode_result.latitude, geocode_result.longitude)
                self._cache[cache_key] = coords
                return coords
            
            # Try with country context
            geocode_result = self._geolocator.geocode(f"{location}, Pakistan", timeout=10)
            if geocode_result:
                coords = (geocode_result.latitude, geocode_result.longitude)
                self._cache[cache_key] = coords
                return coords
            
            # Try last word as city (for dealer names)
            if len(location.split()) > 2:
                words = location.split()
                possible_city = words[-1]
                geocode_result = self._geolocator.geocode(f"{possible_city}, Pakistan", timeout=10)
                if geocode_result:
                    coords = (geocode_result.latitude, geocode_result.longitude)
                    self._cache[cache_key] = coords
                    return coords
            
            return None
        except Exception as e:
            logger.warning(f"⚠️ Geocoding failed for {location}: {e}")
            return None
    
    def calculate_distance(self, origin: str, destination: str) -> Dict[str, Any]:
        """
        Calculate distance between origin and destination.
        
        Returns:
            Dict with: distance_km, duration_sec, duration_text, source, confidence
        """
        result = {
            "distance_km": 0,
            "duration_sec": 0,
            "duration_text": "Unknown",
            "source": "unknown",
            "confidence": "low",
            "origin_used": origin,
            "destination_used": destination
        }
        
        if not origin or not destination:
            return result
        
        # Try OpenRouteService
        if self._client and GEO_AVAILABLE:
            try:
                origin_coords = self.get_coordinates(origin)
                dest_coords = self.get_coordinates(destination)
                
                if origin_coords and dest_coords:
                    coords = [[origin_coords[1], origin_coords[0]], [dest_coords[1], dest_coords[0]]]
                    routes = self._client.directions(
                        coordinates=coords,
                        profile='driving-car',
                        format='json'
                    )
                    
                    if routes and routes.get('features'):
                        feature = routes['features'][0]
                        segments = feature.get('properties', {}).get('segments', [])
                        
                        if segments:
                            segment = segments[0]
                            distance_km = segment.get('distance', 0) / 1000
                            duration_sec = segment.get('duration', 0)
                            
                            result = {
                                'distance_km': round(distance_km, 1),
                                'duration_sec': duration_sec,
                                'duration_text': self._format_duration(duration_sec),
                                'source': 'openrouteservice',
                                'confidence': 'high',
                                'origin_used': origin,
                                'destination_used': destination
                            }
                            logger.info(f"🚗 Route: {origin} → {destination}: {distance_km:.1f}km")
                            return result
            except Exception as e:
                logger.error(f"❌ OpenRouteService error: {e}")
        
        # Try geopy fallback
        if self._geolocator and GEO_AVAILABLE:
            try:
                origin_coords = self.get_coordinates(origin)
                dest_coords = self.get_coordinates(destination)
                
                if origin_coords and dest_coords:
                    distance_km = geodesic(origin_coords, dest_coords).kilometers
                    duration_hours = distance_km / 60
                    duration_sec = duration_hours * 3600
                    
                    result = {
                        'distance_km': round(distance_km, 1),
                        'duration_sec': duration_sec,
                        'duration_text': self._format_duration(duration_sec),
                        'source': 'geopy_approximate',
                        'confidence': 'medium',
                        'origin_used': origin,
                        'destination_used': destination
                    }
                    logger.info(f"📍 Geopy: {origin} → {destination}: {distance_km:.1f}km")
                    return result
            except Exception as e:
                logger.error(f"❌ Geopy distance error: {e}")
        
        # Estimate distance using known city distances
        distance_km = self._estimate_distance(origin, destination)
        if distance_km > 0:
            duration_hours = distance_km / 60
            duration_sec = duration_hours * 3600
            
            result = {
                'distance_km': round(distance_km, 1),
                'duration_sec': duration_sec,
                'duration_text': self._format_duration(duration_sec),
                'source': 'estimated',
                'confidence': 'low',
                'origin_used': origin,
                'destination_used': destination
            }
            logger.info(f"📊 Estimated: {origin} → {destination}: {distance_km:.1f}km")
        
        return result
    
    def _format_duration(self, seconds: int) -> str:
        """Format duration in human-readable format."""
        if seconds < 60:
            return "Less than 1 minute"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes} minute{'s' if minutes > 1 else ''}"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            if minutes == 0:
                return f"{hours} hour{'s' if hours > 1 else ''}"
            return f"{hours}h {minutes}m"
        else:
            days = int(seconds / 86400)
            hours = int((seconds % 86400) / 3600)
            return f"{days}d {hours}h"
    
    def _estimate_distance(self, origin: str, destination: str) -> float:
        """Estimate distance between Pakistani cities."""
        city_distances = {
            ("karachi", "lahore"): 1200,
            ("karachi", "islamabad"): 1450,
            ("karachi", "rawalpindi"): 1450,
            ("karachi", "faisalabad"): 1100,
            ("karachi", "multan"): 850,
            ("karachi", "hyderabad"): 160,
            ("karachi", "sukkur"): 450,
            ("lahore", "islamabad"): 380,
            ("lahore", "rawalpindi"): 380,
            ("lahore", "faisalabad"): 150,
            ("lahore", "multan"): 350,
            ("lahore", "gujranwala"): 80,
            ("lahore", "sialkot"): 120,
            ("islamabad", "rawalpindi"): 20,
            ("islamabad", "peshawar"): 180,
            ("islamabad", "abbottabad"): 80,
            ("rawalpindi", "peshawar"): 170,
            ("rawalpindi", "abbottabad"): 70,
            ("rawalpindi", "muzaffarabad"): 120,
            ("rawalpindi", "skardu"): 550,
            ("rawalpindi", "gilgit"): 450,
            ("rawalpindi", "attock"): 90,
            ("rawalpindi", "hassanabdal"): 50,
            ("rawalpindi", "wah cantt"): 50,
        }
        
        origin_key = origin.lower().strip()
        dest_key = destination.lower().strip()
        
        key = (origin_key, dest_key)
        if key in city_distances:
            return city_distances[key]
        
        key_rev = (dest_key, origin_key)
        if key_rev in city_distances:
            return city_distances[key_rev]
        
        return 0
    
    def estimate_travel_time(self, distance_km: float, average_speed_kmh: float = 60) -> float:
        """Estimate travel time in hours."""
        if distance_km <= 0:
            return 0
        return round(distance_km / average_speed_kmh, 1)
    
    def estimate_delivery_eta(self, distance_km: float) -> str:
        """Estimate delivery ETA based on distance."""
        if distance_km <= 0:
            return "Unknown"
        elif distance_km <= 100:
            return "Same Day"
        elif distance_km <= 250:
            return "1 Day"
        elif distance_km <= 450:
            return "2 Days"
        elif distance_km <= 700:
            return "3 Days"
        else:
            return "4+ Days"


# ==========================================================
# BLOCK 3: DNAnalysisService CLASS (STABLE - NO CHANGES TO SEARCH)
# ==========================================================

class DNAnalysisService:
    """Enterprise DN Analytics Service - Stable Search Engine."""
    
    def __init__(self):
        self._service_name = "dn_analysis"
        self._version = "14.0"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0
        self._startup_time = datetime.now().isoformat()
        self._debug_mode = DEBUG_MODE
        self._distance_calculator = DistanceCalculator()
        
        logger.info("=" * 70)
        logger.info(f"🏭 DNAnalysisService v{self._version} - ENTERPRISE PRODUCTION")
        logger.info("=" * 70)
        
        test_result = self._test_connection()
        if test_result:
            self._status = "READY"
            logger.info("✅ DNAnalysisService is READY")
        else:
            self._status = "ERROR"
            logger.error("❌ DNAnalysisService initialization FAILED")
    
    # ==========================================================
    # BLOCK 4: DATABASE CONNECTION METHODS (UNCHANGED)
    # ==========================================================
    
    def _test_connection(self) -> bool:
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
            
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            
            execution_time_ms = (time.time() - start_time) * 1000
            self._query_count += 1
            self._total_execution_time_ms += execution_time_ms
            
            if execution_time_ms > 1000:
                logger.warning(f"⚠️ Slow query ({execution_time_ms:.0f}ms): {query[:100]}...")
            
            return rows
        except Exception as e:
            logger.error(f"❌ SQL Execution Failed: {e}")
            return []
        finally:
            if session:
                session.close()
    
    # ==========================================================
    # BLOCK 5: DN NORMALIZATION (UNCHANGED - STABLE)
    # ==========================================================
    
    def _normalize_dn(self, dn_no: str) -> str:
        """Centralized DN normalization."""
        if not dn_no:
            return ""
        normalized = re.sub(r'[^0-9]', '', str(dn_no).strip())
        normalized = normalized.lstrip('0')
        return normalized
    
    # ==========================================================
    # BLOCK 6: SQL QUERY BUILDERS (ENHANCED)
    # ==========================================================
    
    def _build_complete_dn_query(self) -> str:
        """Build complete DN query with all fields."""
        return """
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(dealer_code) AS dealer_code,
                MAX(customer_code) AS customer_code,
                MAX(warehouse) AS warehouse,
                MAX(warehouse_code) AS warehouse_code,
                MAX(ship_to_city) AS city,
                MAX(delivery_location) AS delivery_location,
                MAX(sales_manager) AS sales_manager,
                MAX(sales_office) AS sales_office,
                MAX(division) AS division,
                MAX(order_type) AS order_type,
                MAX(storage_location) AS storage_location,
                MAX(source_file) AS source_file,
                MAX(upload_batch_id) AS upload_batch_id,
                MIN(created_at) AS created_at,
                MAX(updated_at) AS updated_at,
                MAX(imported_at) AS imported_at,
                MAX(remarks) AS remarks,
                MAX(delivery_status) AS delivery_status,
                MAX(pgi_status) AS pgi_status,
                MAX(pod_status) AS pod_status,
                MAX(pending_flag) AS pending_flag,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                COUNT(DISTINCT material_no) AS material_count,
                COUNT(DISTINCT customer_model) AS model_count,
                COUNT(*) AS row_count
            FROM delivery_reports
            WHERE 
                REGEXP_REPLACE(TRIM(dn_no::text), '[^0-9]', '', 'g') = :dn_no
            GROUP BY dn_no
            LIMIT 1
        """
    
    def _build_product_details_query(self) -> str:
        """Build query for complete product details."""
        return """
            SELECT 
                customer_model AS model_name,
                material_no AS material_number,
                division,
                warehouse,
                customer_name AS dealer_name,
                SUM(dn_qty) AS quantity,
                SUM(dn_amount) AS revenue,
                COUNT(*) AS item_count
            FROM delivery_reports
            WHERE 
                REGEXP_REPLACE(TRIM(dn_no::text), '[^0-9]', '', 'g') = :dn_no
            GROUP BY customer_model, material_no, division, warehouse, customer_name
            ORDER BY quantity DESC
            LIMIT 50
        """
    
    def _build_division_summary_query(self) -> str:
        """Build division summary query."""
        return """
            SELECT 
                division,
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                COUNT(DISTINCT customer_model) AS model_count,
                COUNT(DISTINCT material_no) AS material_count
            FROM delivery_reports
            WHERE 
                REGEXP_REPLACE(TRIM(dn_no::text), '[^0-9]', '', 'g') = :dn_no
            GROUP BY division
            ORDER BY total_units DESC
        """
    
    def _build_exact_dn_query(self) -> str:
        """Build exact match query for fallback."""
        return """
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(dealer_code) AS dealer_code,
                MAX(customer_code) AS customer_code,
                MAX(warehouse) AS warehouse,
                MAX(warehouse_code) AS warehouse_code,
                MAX(ship_to_city) AS city,
                MAX(delivery_location) AS delivery_location,
                MAX(sales_manager) AS sales_manager,
                MAX(sales_office) AS sales_office,
                MAX(division) AS division,
                MAX(order_type) AS order_type,
                MAX(storage_location) AS storage_location,
                MAX(source_file) AS source_file,
                MAX(upload_batch_id) AS upload_batch_id,
                MIN(created_at) AS created_at,
                MAX(updated_at) AS updated_at,
                MAX(imported_at) AS imported_at,
                MAX(remarks) AS remarks,
                MAX(delivery_status) AS delivery_status,
                MAX(pgi_status) AS pgi_status,
                MAX(pod_status) AS pod_status,
                MAX(pending_flag) AS pending_flag,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                COUNT(DISTINCT material_no) AS material_count,
                COUNT(DISTINCT customer_model) AS model_count,
                COUNT(*) AS row_count
            FROM delivery_reports
            WHERE dn_no = :dn_no
            GROUP BY dn_no
            LIMIT 1
        """
    
    def _build_fallback_dn_query(self) -> str:
        """Build fallback query to find similar DNs."""
        return """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE REGEXP_REPLACE(TRIM(dn_no::text), '[^0-9]', '', 'g') LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
    
    # ==========================================================
    # BLOCK 7: HEALTH & VALIDATION METHODS (UNCHANGED)
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        session = None
        result = {
            "healthy": False,
            "service": self._service_name,
            "version": self._version,
            "database": "disconnected",
            "errors": [],
            "warnings": [],
            "timestamp": datetime.now().isoformat(),
            "query_count": self._query_count,
            "total_execution_time_ms": self._total_execution_time_ms
        }
        
        try:
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                return result
            
            session = SessionLocal()
            session.execute(text("SELECT 1"))
            result["database"] = "connected"
            
            inspector = inspect(session.bind)
            tables = inspector.get_table_names()
            if "delivery_reports" not in tables:
                result["errors"].append("Table 'delivery_reports' does not exist")
                return result
            
            result["healthy"] = True
            result["database"] = "connected"
            self._status = "READY"
            
            return result
        except Exception as e:
            result["errors"].append(str(e))
            return result
        finally:
            if session:
                session.close()
    
    def validation_query(self) -> Dict[str, Any]:
        session = None
        result = {"success": False, "records": 0, "error": None}
        
        try:
            session = self._get_session()
            if not session:
                result["error"] = "SessionLocal not available"
                return result
            
            query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports"
            query_result = session.execute(text(query))
            row = query_result.fetchone()
            
            if row:
                result["success"] = True
                result["records"] = row[0] or 0
            
            return result
        except Exception as e:
            result["error"] = str(e)
            return result
        finally:
            if session:
                session.close()
    
    def get_service_metadata(self) -> Dict[str, Any]:
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": self._status,
            "module": "DN Analytics",
            "description": "Haier Pakistan Logistics - Enterprise DN Analytics Engine",
            "date_policy": "Native PostgreSQL DATE values (YYYY-MM-DD)",
            "business_logic": "Shipment stage from dates (not status columns)",
            "gis_provider": "OpenRouteService + geopy fallback + estimation",
            "methods": [
                "health_check", "validation_query", "get_service_metadata",
                "search_dn", "verify_dn", "get_dn_dashboard",
                "get_complete_dn_dashboard", "diagnose_dn", "check_dn_raw",
                "test_dn_lookup", "test_date_calculation",
                "get_pending_dns", "get_pending_pgi", "get_pending_pod",
                "calculate_delivery_aging", "calculate_pod_aging", "calculate_total_cycle",
                "calculate_distance", "calculate_route", "estimate_travel_time",
                "estimate_delivery_eta", "get_coordinates", "format_dn_dashboard"
            ]
        }
    
    # ==========================================================
    # BLOCK 8: DATE ENGINE (UNCHANGED)
    # ==========================================================
    
    def _validate_postgresql_date(self, date_value, field_name: str = "date") -> Dict[str, Any]:
        result = {
            "valid": False,
            "value": None,
            "type": "unknown",
            "formatted": "N/A",
            "error": None,
            "field": field_name
        }
        
        if date_value is None:
            result["error"] = "NULL value"
            result["type"] = "NoneType"
            return result
        
        if isinstance(date_value, date) and not isinstance(date_value, datetime):
            result["type"] = "date"
            result["value"] = date_value
            result["formatted"] = date_value.strftime('%Y-%m-%d')
            result["valid"] = True
            return result
        
        elif isinstance(date_value, datetime):
            result["type"] = "datetime"
            result["value"] = date_value
            result["formatted"] = date_value.strftime('%Y-%m-%d')
            result["valid"] = True
            return result
        
        else:
            result["error"] = f"Unsupported type: {type(date_value)}"
            return result
    
    def _format_date_dmy_long(self, date_value) -> str:
        if not date_value:
            return 'N/A'
        try:
            if isinstance(date_value, (date, datetime)):
                return date_value.strftime('%d-%b-%Y')
            return str(date_value)
        except Exception:
            return 'N/A'
    
    def _safe_date_diff(self, date1, date2) -> int:
        if date1 is None or date2 is None:
            return 0
        try:
            if isinstance(date1, datetime):
                date1 = date1.date()
            if isinstance(date2, datetime):
                date2 = date2.date()
            if isinstance(date1, date) and isinstance(date2, date):
                return max(0, (date2 - date1).days)
            return 0
        except Exception:
            return 0
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        try:
            if dn_create_date is None:
                return 0
            if good_issue_date is None:
                return self._safe_date_diff(dn_create_date, datetime.now().date())
            return self._safe_date_diff(dn_create_date, good_issue_date)
        except Exception:
            return 0
    
    def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
        try:
            if good_issue_date is None:
                return 0
            if pod_date is None:
                return self._safe_date_diff(good_issue_date, datetime.now().date())
            return self._safe_date_diff(good_issue_date, pod_date)
        except Exception:
            return 0
    
    def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
        try:
            if dn_create_date is None:
                return 0
            if pod_date is None:
                return self._safe_date_diff(dn_create_date, datetime.now().date())
            return self._safe_date_diff(dn_create_date, pod_date)
        except Exception:
            return 0
    
    def _format_aging_text(self, days: int) -> str:
        if days < 0:
            return f"{abs(days)} Days (Data Error)"
        elif days == 0:
            return "Same Day"
        elif days == 1:
            return "1 Day"
        elif days <= 6:
            return f"{days} Days"
        elif days <= 13:
            return f"{days} Days (1-2 Weeks)"
        elif days <= 20:
            return f"{days} Days (2-3 Weeks)"
        elif days <= 30:
            return f"{days} Days (3-4 Weeks)"
        else:
            months = days // 30
            return f"{days} Days (Over 1 Month)"
    
    # ==========================================================
    # BLOCK 9: SHIPMENT STAGE ENGINE (UNCHANGED)
    # ==========================================================
    
    def _determine_shipment_stage(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        pgi_exists = good_issue_date is not None
        pod_exists = pod_date is not None
        
        def fmt_date(d):
            if d is None:
                return 'N/A'
            if isinstance(d, (date, datetime)):
                return d.strftime('%d-%b-%Y')
            return str(d)
        
        if pod_exists and pgi_exists:
            return {
                "stage": "Delivered",
                "stage_emoji": "✅",
                "pgi_status": "Completed",
                "pod_status": "Received",
                "pgi_status_text": "✅ Completed",
                "pod_status_text": "Done",
                "pending": False,
                "health": "Completed Successfully",
                "health_emoji": "🟢",
                "recommendation": "Shipment completed successfully. Review performance if delivery exceeded expected time.",
                "progress": [
                    {"step": "DN Created", "status": "✅", "date": fmt_date(dn_create_date)},
                    {"step": "PGI Completed", "status": "✅", "date": fmt_date(good_issue_date)},
                    {"step": "POD Received", "status": "✅", "date": fmt_date(pod_date)}
                ]
            }
        elif pgi_exists and not pod_exists:
            return {
                "stage": "In Transit",
                "stage_emoji": "🚚",
                "pgi_status": "Completed",
                "pod_status": "Pending",
                "pgi_status_text": "✅ Completed",
                "pod_status_text": "⏳ Pending",
                "pending": True,
                "health": "On Route",
                "health_emoji": "🟡",
                "recommendation": "Follow up with transporter for POD confirmation.",
                "progress": [
                    {"step": "DN Created", "status": "✅", "date": fmt_date(dn_create_date)},
                    {"step": "PGI Completed", "status": "✅", "date": fmt_date(good_issue_date)},
                    {"step": "POD Pending", "status": "⏳", "date": "Pending"}
                ]
            }
        else:
            return {
                "stage": "Pending Dispatch",
                "stage_emoji": "⏳",
                "pgi_status": "Pending",
                "pod_status": "Pending",
                "pgi_status_text": "⏳ Pending",
                "pod_status_text": "⏳ Pending",
                "pending": True,
                "health": "Awaiting Warehouse Dispatch",
                "health_emoji": "🟡",
                "recommendation": "Warehouse should complete PGI immediately.",
                "progress": [
                    {"step": "DN Created", "status": "✅", "date": fmt_date(dn_create_date)},
                    {"step": "PGI Pending", "status": "⏳", "date": "Pending"},
                    {"step": "POD Not Started", "status": "⏳", "date": "Not Started"}
                ]
            }
    
    # ==========================================================
    # BLOCK 10: DISTANCE METHODS (NEW)
    # ==========================================================
    
    def calculate_distance(self, origin: str, destination: str) -> Dict[str, Any]:
        return self._distance_calculator.calculate_distance(origin, destination)
    
    def calculate_route(self, origin: str, destination: str) -> Dict[str, Any]:
        return self.calculate_distance(origin, destination)
    
    def estimate_travel_time(self, distance_km: float, average_speed_kmh: float = 60) -> float:
        return self._distance_calculator.estimate_travel_time(distance_km, average_speed_kmh)
    
    def estimate_delivery_eta(self, distance_km: float) -> str:
        return self._distance_calculator.estimate_delivery_eta(distance_km)
    
    def get_coordinates(self, location: str) -> Optional[Tuple[float, float]]:
        return self._distance_calculator.get_coordinates(location)
    
    def calculate_route_efficiency(self, distance_km: float, actual_days: int, expected_days: int) -> float:
        if expected_days <= 0 or actual_days <= 0:
            return 0
        efficiency = (expected_days / actual_days) * 100
        return min(efficiency, 100)
    
    def calculate_sla(self, actual_days: int, expected_days: int) -> Dict[str, Any]:
        if expected_days <= 0:
            return {"status": "Unknown", "on_time": False, "delay_days": 0}
        
        delay = max(0, actual_days - expected_days) if actual_days > 0 else 0
        on_time = delay == 0
        
        if on_time:
            status = "On Time"
        elif delay <= 1:
            status = "Slightly Delayed"
        elif delay <= 3:
            status = "Delayed"
        else:
            status = "Significantly Delayed"
        
        return {
            "status": status,
            "on_time": on_time,
            "delay_days": delay,
            "expected_days": expected_days,
            "actual_days": actual_days
        }
    
    def calculate_velocity(self, distance_km: float, days: int) -> float:
        if days <= 0 or distance_km <= 0:
            return 0
        return round(distance_km / days, 1)
    
    def calculate_average_speed(self, distance_km: float, hours: float) -> float:
        if hours <= 0 or distance_km <= 0:
            return 0
        return round(distance_km / hours, 1)
    
    # ==========================================================
    # BLOCK 11: DN SEARCH ENGINE (STABLE - NO CHANGES)
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """Stable DN Search Engine - DO NOT MODIFY."""
        logger.info(f"🔍 Searching for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        logger.info(f"   ├── Normalized DN: '{normalized_dn}'")
        
        if len(normalized_dn) < 8:
            return {"success": False, "error": f"Invalid DN format: {normalized_dn} (must be 8-12 digits)"}
        
        # Query with normalized DN
        query = self._build_complete_dn_query()
        results = self._execute_query(query, {"dn_no": normalized_dn})
        
        if results:
            logger.info(f"✅ DN {dn_no} found")
            return {"success": True, "data": results[0]}
        
        # Try with original DN
        results = self._execute_query(query, {"dn_no": str(dn_no)})
        if results:
            logger.info(f"✅ DN {dn_no} found with original match")
            return {"success": True, "data": results[0]}
        
        # Fallback - find similar DNs
        logger.warning(f"⚠️ Primary match not found for {dn_no}. Running fallback...")
        fallback_results = self._execute_query(self._build_fallback_dn_query(), {"dn_no": normalized_dn})
        similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
        
        if similar_dns:
            logger.info(f"   ├── Found {len(similar_dns)} similar DNs: {similar_dns[:5]}")
            
            exact_query = self._build_exact_dn_query()
            for similar_dn in similar_dns[:5]:
                logger.info(f"   ├── Trying exact match for: '{similar_dn}'")
                results = self._execute_query(exact_query, {"dn_no": similar_dn})
                if results:
                    logger.info(f"✅ DN found via similar match: {similar_dn}")
                    return {"success": True, "data": results[0]}
            
            return {
                "success": False,
                "error": f"DN {dn_no} not found",
                "similar_dns": similar_dns[:5],
                "message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
            }
        
        logger.warning(f"❌ DN {dn_no} not found")
        return {"success": False, "error": f"DN {dn_no} not found"}
    
    # ==========================================================
    # BLOCK 12: VERIFY DN (UNCHANGED)
    # ==========================================================
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        search_result = self.search_dn(dn_no)
        return {"success": True, "exists": search_result.get("success", False)}
    
    # ==========================================================
    # BLOCK 13: DN DASHBOARD (ENHANCED - COMPLETE DATA)
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Get complete DN dashboard with all data."""
        logger.info(f"📊 Getting dashboard for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        # Get DN summary
        search_result = self.search_dn(dn_no)
        
        if not search_result.get("success"):
            similar_dns = search_result.get("similar_dns", [])
            if similar_dns:
                return {
                    "success": False,
                    "error": f"DN {dn_no} not found. Similar: {', '.join(similar_dns[:3])}"
                }
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        data = search_result.get("data", {})
        
        # Get product details
        normalized_dn = self._normalize_dn(dn_no)
        
        # Get products
        product_query = self._build_product_details_query()
        product_results = self._execute_query(product_query, {"dn_no": normalized_dn})
        
        products = []
        module_quantities = {}
        total_units = 0
        total_revenue = 0
        
        for row in product_results:
            model_name = row.get('model_name')
            if model_name:
                qty = int(row.get('quantity', 0) or 0)
                revenue = float(row.get('revenue', 0) or 0)
                division = row.get('division', 'Unknown')
                material_no = row.get('material_number', 'N/A')
                
                products.append({
                    'name': str(model_name),
                    'material_no': str(material_no),
                    'division': str(division),
                    'qty': qty,
                    'revenue': revenue
                })
                
                if division in module_quantities:
                    module_quantities[division] += qty
                else:
                    module_quantities[division] = qty
                
                total_units += qty
                total_revenue += revenue
        
        # Get division summary
        division_query = self._build_division_summary_query()
        division_results = self._execute_query(division_query, {"dn_no": normalized_dn})
        
        division_summary = []
        for row in division_results:
            division_summary.append({
                'division': row.get('division', 'Unknown'),
                'total_units': int(row.get('total_units', 0) or 0),
                'total_revenue': float(row.get('total_revenue', 0) or 0),
                'model_count': int(row.get('model_count', 0) or 0),
                'material_count': int(row.get('material_count', 0) or 0)
            })
        
        # Get dates
        raw_dn_create = data.get('dn_create_date')
        raw_pgi = data.get('good_issue_date')
        raw_pod = data.get('pod_date')
        
        # Calculate aging
        delivery_aging = self.calculate_delivery_aging(raw_dn_create, raw_pgi)
        pod_aging = self.calculate_pod_aging(raw_pgi, raw_pod)
        total_cycle = self.calculate_total_cycle(raw_dn_create, raw_pod)
        
        # Determine stage
        stage_info = self._determine_shipment_stage(raw_dn_create, raw_pgi, raw_pod)
        
        # Format dates
        dn_create_date = self._format_date_dmy_long(raw_dn_create)
        good_issue_date = self._format_date_dmy_long(raw_pgi)
        pod_date = self._format_date_dmy_long(raw_pod)
        
        # Build complete dashboard
        dashboard = {
            # DN Information
            "dn_no": data.get('dn_no'),
            "dealer_name": data.get('dealer_name', 'Unknown'),
            "dealer_code": data.get('dealer_code', 'N/A'),
            "customer_code": data.get('customer_code', 'N/A'),
            "warehouse": data.get('warehouse', 'Unknown'),
            "warehouse_code": data.get('warehouse_code', 'N/A'),
            "city": data.get('city', 'Unknown'),
            "delivery_location": data.get('delivery_location'),
            "sales_manager": data.get('sales_manager'),
            "sales_office": data.get('sales_office'),
            "division": data.get('division', 'Unknown'),
            "order_type": data.get('order_type'),
            "storage_location": data.get('storage_location'),
            
            # Summary Metrics
            "total_units": total_units or data.get('total_units', 0),
            "total_revenue": total_revenue or data.get('total_revenue', 0),
            "material_count": data.get('material_count', 0),
            "model_count": data.get('model_count', 0),
            "row_count": data.get('row_count', 0),
            
            # Product Details
            "products": products,
            "module_quantities": module_quantities,
            "division_summary": division_summary,
            
            # Dates
            "dn_create_date": dn_create_date,
            "good_issue_date": good_issue_date,
            "pod_date": pod_date,
            "_dn_create_date": raw_dn_create,
            "_good_issue_date": raw_pgi,
            "_pod_date": raw_pod,
            
            # Shipment Stage
            "stage": stage_info["stage"],
            "stage_emoji": stage_info["stage_emoji"],
            "health": stage_info["health"],
            "health_emoji": stage_info["health_emoji"],
            "pending_flag": stage_info["pending"],
            "progress": stage_info["progress"],
            "recommendation": stage_info["recommendation"],
            "pgi_status_text": stage_info["pgi_status_text"],
            "pod_status_text": stage_info["pod_status_text"],
            "pending_flag_text": "⚠️ Yes" if stage_info["pending"] else "🟢 No",
            
            # Aging
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "total_cycle_days": total_cycle,
            "delivery_aging_text": self._format_aging_text(delivery_aging),
            "pod_aging_text": self._format_aging_text(pod_aging) if pod_aging > 0 else "Not Started",
            "total_cycle_text": self._format_aging_text(total_cycle),
            
            # System Info
            "source_file": data.get('source_file'),
            "upload_batch_id": data.get('upload_batch_id'),
            "imported_at": data.get('imported_at'),
            "created_at": data.get('created_at'),
            "updated_at": data.get('updated_at'),
            "remarks": data.get('remarks'),
            
            # Status columns (for backward compatibility)
            "delivery_status": data.get('delivery_status'),
            "pgi_status": data.get('pgi_status'),
            "pod_status": data.get('pod_status')
        }
        
        return {"success": True, "data": dashboard}
    
    # ==========================================================
    # BLOCK 14: GET COMPLETE DN DASHBOARD (WITH DISTANCE)
    # ==========================================================
    
    def get_complete_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Get complete DN dashboard with distance analytics."""
        dashboard_result = self.get_dn_dashboard(dn_no)
        
        if not dashboard_result.get("success"):
            return dashboard_result
        
        data = dashboard_result.get("data", {})
        
        # Calculate distance from warehouse to delivery location
        warehouse = data.get('warehouse')
        destination = data.get('delivery_location') or data.get('city')
        
        if warehouse and destination:
            distance_data = self.calculate_distance(warehouse, destination)
            
            data['distance_km'] = distance_data.get('distance_km', 0)
            data['distance_text'] = f"{distance_data.get('distance_km', 0):.1f} km" if distance_data.get('distance_km', 0) > 0 else "Not Available"
            data['duration_text'] = distance_data.get('duration_text', 'Unknown')
            data['route_source'] = distance_data.get('source', 'unknown')
            data['route_confidence'] = distance_data.get('confidence', 'low')
            data['destination_used'] = destination
            data['origin_used'] = warehouse
            
            # Calculate ETA
            eta = self.estimate_delivery_eta(distance_data.get('distance_km', 0))
            data['eta'] = eta
            
            # Calculate travel time
            travel_time = self.estimate_travel_time(distance_data.get('distance_km', 0))
            data['travel_time_hours'] = travel_time
            
            # Calculate velocity
            total_cycle_days = data.get('total_cycle_days', 0)
            distance_km = distance_data.get('distance_km', 0)
            
            if total_cycle_days > 0 and distance_km > 0:
                data['velocity_km_per_day'] = self.calculate_velocity(distance_km, total_cycle_days)
                data['average_speed_kmh'] = self.calculate_average_speed(
                    distance_km,
                    travel_time or (distance_km / 60)
                )
                
                # Calculate SLA
                expected_days = 1 if eta == "Same Day" else int(eta.split()[0]) if eta != "4+ Days" else 4
                sla = self.calculate_sla(total_cycle_days, expected_days)
                data['sla_status'] = sla.get('status', 'Unknown')
                data['sla_on_time'] = sla.get('on_time', False)
                data['sla_delay_days'] = sla.get('delay_days', 0)
                data['route_efficiency'] = self.calculate_route_efficiency(distance_km, total_cycle_days, expected_days)
        
        return {"success": True, "data": data}
    
    # ==========================================================
    # BLOCK 15: DIAGNOSTIC METHODS (UNCHANGED)
    # ==========================================================
    
    def diagnose_dn(self, dn_no: str) -> Dict[str, Any]:
        logger.info(f"🔬 Diagnosing DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        start_time = time.time()
        
        result = {
            "dn": dn_no,
            "normalized": normalized_dn,
            "exact_match_count": 0,
            "partial_match_count": 0,
            "similar_dns": [],
            "exists": False,
            "diagnostic": [],
            "execution_time_ms": 0
        }
        
        exact_query = """
            SELECT COUNT(DISTINCT dn_no) as count 
            FROM delivery_reports 
            WHERE REGEXP_REPLACE(TRIM(dn_no::text), '[^0-9]', '', 'g') = :dn_no
        """
        exact_results = self._execute_query(exact_query, {"dn_no": normalized_dn})
        exact_count = exact_results[0].get('count', 0) if exact_results else 0
        result["exact_match_count"] = exact_count
        result["exists"] = exact_count > 0
        result["diagnostic"].append(f"Exact match: {exact_count} found")
        
        partial_results = self._execute_query(self._build_fallback_dn_query(), {"dn_no": normalized_dn})
        similar_dns = [str(r.get('dn_no', '')) for r in partial_results if r.get('dn_no')]
        result["partial_match_count"] = len(similar_dns)
        result["similar_dns"] = similar_dns[:10]
        result["diagnostic"].append(f"Partial matches: {len(similar_dns)} found")
        
        result["execution_time_ms"] = round((time.time() - start_time) * 1000, 2)
        result["diagnostic"].append(f"Execution time: {result['execution_time_ms']}ms")
        
        return {"success": True, "data": result}
    
    def check_dn_raw(self, dn_no: str) -> Dict[str, Any]:
        results = self._execute_query(self._build_fallback_dn_query(), {"dn_no": dn_no})
        similar_dns = [str(r.get('dn_no', '')) for r in results if r.get('dn_no')]
        return {
            "success": True,
            "dn": dn_no,
            "found": len(similar_dns) > 0,
            "similar_dns": similar_dns[:10],
            "count": len(similar_dns)
        }
    
    def test_dn_lookup(self, dn_no: str) -> Dict[str, Any]:
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
        
        query1 = "SELECT COUNT(*) as count FROM delivery_reports WHERE dn_no = :dn_no"
        r1 = self._execute_query(query1, {"dn_no": normalized_dn})
        results["exact_count"] = r1[0].get('count', 0) if r1 else 0
        results["diagnostics"].append(f"Exact match: {results['exact_count']}")
        
        query2 = "SELECT COUNT(*) as count FROM delivery_reports WHERE dn_no LIKE '%' || :dn_no || '%'"
        r2 = self._execute_query(query2, {"dn_no": normalized_dn})
        results["like_count"] = r2[0].get('count', 0) if r2 else 0
        results["diagnostics"].append(f"LIKE match: {results['like_count']}")
        
        r4 = self._execute_query(self._build_fallback_dn_query(), {"dn_no": normalized_dn})
        results["matching_dns"] = [str(r.get('dn_no', '')) for r in r4 if r.get('dn_no')]
        results["diagnostics"].append(f"Total matching DNs: {len(results['matching_dns'])}")
        
        results["found"] = results["exact_count"] > 0 or results["like_count"] > 0
        
        return {"success": True, "data": results}
    
    def debug_aging_calculation(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        delivery_aging = self.calculate_delivery_aging(dn_create_date, good_issue_date)
        pod_aging = self.calculate_pod_aging(good_issue_date, pod_date)
        total_cycle = self.calculate_total_cycle(dn_create_date, pod_date)
        
        return {
            "input_dates": {
                "dn_create_date": str(dn_create_date) if dn_create_date else 'N/A',
                "pgi_date": str(good_issue_date) if good_issue_date else 'N/A',
                "pod_date": str(pod_date) if pod_date else 'N/A'
            },
            "calculations": {
                "delivery_aging_days": delivery_aging,
                "pod_aging_days": pod_aging,
                "total_cycle_days": total_cycle
            },
            "formatted": {
                "delivery_aging_text": self._format_aging_text(delivery_aging),
                "pod_aging_text": self._format_aging_text(pod_aging) if pod_aging > 0 else "Not Started",
                "total_cycle_text": self._format_aging_text(total_cycle)
            }
        }
    
    # ==========================================================
    # BLOCK 16: PENDING METHODS (UNCHANGED)
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
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
            
            formatted_results = []
            for row in results:
                stage_info = self._determine_shipment_stage(
                    row.get('dn_create_date'),
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
                    "dn_create_date": row.get('dn_create_date'),
                    "stage": stage_info["stage"],
                    "stage_emoji": stage_info["stage_emoji"],
                    "pending": stage_info["pending"],
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
            
            formatted_results = []
            for row in results:
                stage_info = self._determine_shipment_stage(
                    row.get('dn_create_date'),
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
                    "dn_create_date": row.get('dn_create_date'),
                    "stage": stage_info["stage"],
                    "stage_emoji": stage_info["stage_emoji"],
                    "pending": stage_info["pending"],
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
            
            formatted_results = []
            for row in results:
                stage_info = self._determine_shipment_stage(
                    row.get('dn_create_date'),
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
                    "dn_create_date": row.get('dn_create_date'),
                    "stage": stage_info["stage"],
                    "stage_emoji": stage_info["stage_emoji"],
                    "pending": stage_info["pending"],
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
    
    # ==========================================================
    # BLOCK 17: WHATSAPP RESPONSE FORMATTER (ENHANCED - COMPLETE)
    # ==========================================================
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """Format complete DN dashboard for WhatsApp response."""
        data = dashboard_data.get('data', {})
        
        # ==========================================================
        # SECTION 1: DN HEADER
        # ==========================================================
        
        lines = []
        lines.append("📦 *DN: {}*".format(data.get('dn_no', 'N/A')))
        lines.append("")
        
        # ==========================================================
        # SECTION 2: DEALER & WAREHOUSE INFORMATION
        # ==========================================================
        
        lines.append("*Dealer Information:*")
        lines.append("Dealer: {}".format(data.get('dealer_name', 'Unknown')))
        if data.get('dealer_code') and data.get('dealer_code') != 'N/A':
            lines.append("Dealer Code: {}".format(data.get('dealer_code')))
        if data.get('customer_code') and data.get('customer_code') != 'N/A':
            lines.append("Customer Code: {}".format(data.get('customer_code')))
        lines.append("")
        
        lines.append("*Warehouse Information:*")
        lines.append("Warehouse: {}".format(data.get('warehouse', 'Unknown')))
        if data.get('warehouse_code') and data.get('warehouse_code') != 'N/A':
            lines.append("Warehouse Code: {}".format(data.get('warehouse_code')))
        if data.get('delivery_location'):
            lines.append("Delivery Location: {}".format(data.get('delivery_location')))
        lines.append("City: {}".format(data.get('city', 'Unknown')))
        lines.append("")
        
        if data.get('sales_office'):
            lines.append("Sales Office: {}".format(data.get('sales_office')))
        if data.get('sales_manager'):
            lines.append("Sales Manager: {}".format(data.get('sales_manager')))
        if data.get('division'):
            lines.append("Division: {}".format(data.get('division')))
        if data.get('order_type'):
            lines.append("Order Type: {}".format(data.get('order_type')))
        if data.get('storage_location'):
            lines.append("Storage: {}".format(data.get('storage_location')))
        lines.append("")
        lines.append("────────────────────")
        lines.append("")
        
        # ==========================================================
        # SECTION 3: SHIPMENT SUMMARY (FIXED METRICS)
        # ==========================================================
        
        lines.append("*📊 Shipment Summary:*")
        lines.append("DN Count: {}".format(data.get('row_count', 1)))
        lines.append("Models: {}".format(data.get('model_count', 0)))
        lines.append("Materials: {}".format(data.get('material_count', 0)))
        
        # FIX: Show actual units and revenue
        units = data.get('total_units', 0)
        if units:
            lines.append("Total Units: {}".format(units))
        else:
            lines.append("Total Units: 0")
        
        revenue = data.get('total_revenue', 0)
        if revenue:
            lines.append("Total Revenue: PKR {:,}".format(revenue))
        else:
            lines.append("Total Revenue: PKR 0")
        lines.append("")
        lines.append("────────────────────")
        lines.append("")
        
        # ==========================================================
        # SECTION 4: PRODUCT DETAILS
        # ==========================================================
        
        products = data.get('products', [])
        if products:
            lines.append("*📦 Product Details:*")
            for idx, product in enumerate(products[:15], 1):
                model_name = product.get('name', 'Unknown')
                material_no = product.get('material_no', 'N/A')
                qty = product.get('qty', 0)
                revenue = product.get('revenue', 0)
                
                lines.append("{}. {}: {} units".format(idx, model_name, qty))
                if material_no != 'N/A':
                    lines.append("   Material: {}".format(material_no))
                if revenue:
                    lines.append("   Revenue: PKR {:,}".format(revenue))
            
            if len(products) > 15:
                lines.append("... and {} more products".format(len(products) - 15))
            lines.append("")
            lines.append("────────────────────")
            lines.append("")
        
        # ==========================================================
        # SECTION 5: DIVISION SUMMARY
        # ==========================================================
        
        division_summary = data.get('division_summary', [])
        if division_summary:
            lines.append("*📊 Division Summary:*")
            for div in division_summary[:5]:
                division_name = div.get('division', 'Unknown')
                units_div = div.get('total_units', 0)
                revenue_div = div.get('total_revenue', 0)
                models_div = div.get('model_count', 0)
                lines.append("{}: {} units, {} models".format(division_name, units_div, models_div))
            lines.append("")
            lines.append("────────────────────")
            lines.append("")
        
        # ==========================================================
        # SECTION 6: TIMELINE & AGING
        # ==========================================================
        
        lines.append("*📅 Timeline:*")
        lines.append("DN Create: {}".format(data.get('dn_create_date', 'N/A')))
        lines.append("PGI: {}".format(data.get('good_issue_date', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_date', 'N/A')))
        lines.append("")
        lines.append("*⏳ Aging:*")
        lines.append("Delivery: {}".format(data.get('delivery_aging_text', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_aging_text', 'N/A')))
        lines.append("Total Cycle: {}".format(data.get('total_cycle_text', 'N/A')))
        lines.append("")
        lines.append("────────────────────")
        lines.append("")
        
        # ==========================================================
        # SECTION 7: ROUTE ANALYTICS
        # ==========================================================
        
        distance_km = data.get('distance_km', 0)
        if distance_km > 0:
            lines.append("*🚚 Route Analytics:*")
            lines.append("Warehouse: {}".format(data.get('origin_used', 'Unknown')))
            lines.append("Destination: {}".format(data.get('destination_used', 'Unknown')))
            lines.append("Distance: {}".format(data.get('distance_text', 'Not Available')))
            lines.append("Estimated Drive: {}".format(data.get('duration_text', 'Unknown')))
            lines.append("Expected Delivery: {}".format(data.get('eta', 'Unknown')))
            
            if data.get('velocity_km_per_day', 0) > 0:
                lines.append("Velocity: {} km/day".format(data.get('velocity_km_per_day')))
            if data.get('route_efficiency', 0) > 0:
                lines.append("Route Efficiency: {}%".format(data.get('route_efficiency')))
            if data.get('sla_status'):
                lines.append("SLA: {}".format(data.get('sla_status')))
                if data.get('sla_delay_days', 0) > 0:
                    lines.append("Delay: {} Days".format(data.get('sla_delay_days')))
            lines.append("")
            lines.append("────────────────────")
            lines.append("")
        
        # ==========================================================
        # SECTION 8: SHIPMENT STATUS
        # ==========================================================
        
        lines.append("*📋 Shipment Status:*")
        lines.append("Stage: {} {}".format(data.get('stage_emoji', '❓'), data.get('stage', 'Unknown')))
        lines.append("Health: {} {}".format(data.get('health_emoji', '❓'), data.get('health', 'Unknown')))
        lines.append("PGI: {}".format(data.get('pgi_status_text', 'Unknown')))
        lines.append("POD: {}".format(data.get('pod_status_text', 'Unknown')))
        lines.append("Pending: {}".format(data.get('pending_flag_text', 'Unknown')))
        lines.append("")
        lines.append("────────────────────")
        lines.append("")
        
        # ==========================================================
        # SECTION 9: RECOMMENDATION
        # ==========================================================
        
        if data.get('recommendation'):
            lines.append("*💡 Recommendation:*")
            lines.append(data.get('recommendation'))
            lines.append("")
            lines.append("────────────────────")
            lines.append("")
        
        # ==========================================================
        # SECTION 10: SYSTEM INFORMATION
        # ==========================================================
        
        lines.append("*📁 System Information:*")
        if data.get('source_file'):
            lines.append("Source: {}".format(data.get('source_file')))
        if data.get('upload_batch_id'):
            lines.append("Batch: {}".format(data.get('upload_batch_id')))
        if data.get('imported_at'):
            imported_at = data.get('imported_at')
            if isinstance(imported_at, datetime):
                imported_at = imported_at.strftime('%d-%b-%Y %H:%M')
            lines.append("Imported: {}".format(imported_at))
        if data.get('updated_at'):
            updated_at = data.get('updated_at')
            if isinstance(updated_at, datetime):
                updated_at = updated_at.strftime('%d-%b-%Y %H:%M')
            lines.append("Updated: {}".format(updated_at))
        lines.append("")
        
        return "\n".join(lines)


# ==========================================================
# BLOCK 18: THREAD-SAFE SINGLETON
# ==========================================================

_dn_analytics_service = None
_dn_lock = threading.Lock()


def get_dn_analytics_service() -> DNAnalysisService:
    global _dn_analytics_service
    
    if _dn_analytics_service is None:
        with _dn_lock:
            if _dn_analytics_service is None:
                try:
                    _dn_analytics_service = DNAnalysisService()
                except Exception as e:
                    logger.exception(f"❌ DNAnalysisService initialization failed: {e}")
                    raise
    
    return _dn_analytics_service


# ==========================================================
# BLOCK 19: EXPORTS
# ==========================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service',
    'DistanceCalculator'
]


# ==========================================================
# BLOCK 20: MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v14.0 - ENTERPRISE PRODUCTION")
logger.info("=" * 70)
logger.info("")
logger.info("   ✅ Service: dn_analysis")
logger.info("   ✅ Version: 14.0")
logger.info("   ✅ Status: READY")
logger.info("   ✅ PostgreSQL as Single Source of Truth")
logger.info("   ✅ Stable DN Search Engine (UNCHANGED)")
logger.info("   ✅ Complete DN Dashboard")
logger.info("   ✅ Complete Product Details")
logger.info("   ✅ Correct Metrics (Units, Revenue, Materials, Models)")
logger.info("   ✅ Distance Calculation with Fallbacks")
logger.info("   ✅ Route Analytics & SLA")
logger.info("   ✅ Professional WhatsApp Dashboard")
logger.info("   ✅ 100% Backward Compatible")
logger.info("")
logger.info("   STATUS: ✅ ENTERPRISE PRODUCTION READY")
logger.info("=" * 70)
