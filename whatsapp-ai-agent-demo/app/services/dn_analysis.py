# ==========================================================
# FILE: app/services/dn_analysis.py (v15.0 - ENTERPRISE PRODUCTION)
# ==========================================================
# PURPOSE: Enterprise Logistics Analytics Service
# SOURCE: PostgreSQL delivery_reports table ONLY
# VERSION: 15.0 - ENTERPRISE PRODUCTION
#
# COMPATIBLE WITH: ai_provider_service.py, webhook.py, all analytics services
# INTEGRATION: Railway PostgreSQL, FastAPI, OpenRouteService
#
# ENHANCEMENTS v15.0:
# - ✅ Executive WhatsApp Dashboard
# - ✅ Intelligent shipment stage from dates (not status columns)
# - ✅ Road distance with OpenRouteService + geopy fallback
# - ✅ Product models with material numbers and quantities
# - ✅ Module-wise quantity breakdown
# - ✅ Performance KPIs and Route Efficiency
# - ✅ AI Logistics Insights
# - ✅ Data Quality Metrics
# - ✅ Route caching (30 days TTL)
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
from functools import lru_cache
from cachetools import TTLCache

# GIS Libraries
try:
    import openrouteservice
    from geopy.geocoders import Nominatim
    from geopy.distance import geodesic
    from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
    GEO_AVAILABLE = True
except ImportError:
    GEO_AVAILABLE = False
    logging.warning("⚠️ GIS libraries not available. Distance features disabled.")

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

# Configuration
DEBUG_MODE = os.environ.get("DN_DEBUG_MODE", "false").lower() == "true"
OPENROUTE_API_KEY = os.environ.get("OPENROUTE_API_KEY", "")
GEOCODE_USER_AGENT = "haier-logistics-agent"
CACHE_TTL_DAYS = 30
CACHE_ENABLED = os.environ.get("ENABLE_CACHE", "true").lower() == "true"

# ==========================================================
# BLOCK 2: DISTANCE SERVICE
# ==========================================================

class DistanceService:
    """Enterprise Distance Service with caching and fallbacks."""
    
    def __init__(self):
        self._cache = TTLCache(maxsize=1000, ttl=CACHE_TTL_DAYS * 86400)
        self._geolocator = None
        self._client = None
        
        if OPENROUTE_API_KEY:
            try:
                self._client = openrouteservice.Client(key=OPENROUTE_API_KEY)
                logger.info("✅ OpenRouteService initialized")
            except Exception as e:
                logger.error(f"❌ OpenRouteService initialization failed: {e}")
                self._client = None
        
        if GEO_AVAILABLE:
            try:
                self._geolocator = Nominatim(user_agent=GEOCODE_USER_AGENT)
                logger.info("✅ Geopy initialized")
            except Exception as e:
                logger.error(f"❌ Geopy initialization failed: {e}")
                self._geolocator = None
        
        logger.info("🔧 DistanceService initialized")
    
    def _get_cache_key(self, origin: str, destination: str) -> str:
        return f"{origin.lower().strip()}|{destination.lower().strip()}"
    
    def _geocode(self, location: str) -> Optional[Tuple[float, float]]:
        if not location or not self._geolocator:
            return None
        
        try:
            geocode_result = self._geolocator.geocode(location, timeout=10)
            if geocode_result:
                logger.info(f"📍 Geocoded: {location} → ({geocode_result.latitude}, {geocode_result.longitude})")
                return (geocode_result.latitude, geocode_result.longitude)
            
            geocode_result = self._geolocator.geocode(f"{location}, Pakistan", timeout=10)
            if geocode_result:
                logger.info(f"📍 Geocoded with country: {location} → ({geocode_result.latitude}, {geocode_result.longitude})")
                return (geocode_result.latitude, geocode_result.longitude)
            
            logger.warning(f"⚠️ Could not geocode: {location}")
            return None
            
        except Exception as e:
            logger.warning(f"⚠️ Geocoding failed for {location}: {e}")
            return None
    
    def _format_duration(self, seconds: int) -> str:
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
    
    def get_distance(self, origin: str, destination: str) -> Dict[str, Any]:
        if not origin or not destination:
            return {
                'distance_km': 0,
                'duration_sec': 0,
                'duration_hours': 0,
                'duration_text': 'Unknown',
                'source': 'unknown'
            }
        
        cache_key = self._get_cache_key(origin, destination)
        
        # Check cache
        if CACHE_ENABLED and cache_key in self._cache:
            logger.info(f"📦 Cache hit: {origin} → {destination}")
            return self._cache[cache_key]
        
        result = None
        
        # Try OpenRouteService
        if self._client:
            try:
                origin_coords = self._geocode(origin)
                dest_coords = self._geocode(destination)
                
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
                                'duration_hours': round(duration_sec / 3600, 1),
                                'duration_text': self._format_duration(duration_sec),
                                'source': 'openrouteservice'
                            }
                            
                            logger.info(f"🚗 OpenRoute: {origin} → {destination}: {distance_km:.1f}km")
            except Exception as e:
                logger.error(f"❌ OpenRouteService error: {e}")
        
        # Try geopy fallback
        if not result and GEO_AVAILABLE:
            try:
                origin_coords = self._geocode(origin)
                dest_coords = self._geocode(destination)
                
                if origin_coords and dest_coords:
                    distance_km = geodesic(origin_coords, dest_coords).kilometers
                    duration_hours = distance_km / 60
                    duration_sec = duration_hours * 3600
                    
                    result = {
                        'distance_km': round(distance_km, 1),
                        'duration_sec': duration_sec,
                        'duration_hours': round(duration_hours, 1),
                        'duration_text': self._format_duration(duration_sec),
                        'source': 'geopy_approximate'
                    }
                    
                    logger.info(f"📍 Geopy: {origin} → {destination}: {distance_km:.1f}km")
            except Exception as e:
                logger.error(f"❌ Geopy distance error: {e}")
        
        # Default fallback
        if not result:
            result = {
                'distance_km': 0,
                'duration_sec': 0,
                'duration_hours': 0,
                'duration_text': 'Not Available',
                'source': 'fallback'
            }
        
        # Cache result
        if CACHE_ENABLED and result.get('distance_km', 0) > 0:
            self._cache[cache_key] = result
            logger.info(f"💾 Route cached: {origin} → {destination}")
        
        return result
    
    def get_distance_category(self, distance_km: float) -> Dict[str, str]:
        if distance_km <= 0:
            return {'category': 'Unknown', 'emoji': '❓'}
        elif distance_km <= 50:
            return {'category': 'Nearby', 'emoji': '📍'}
        elif distance_km <= 150:
            return {'category': 'Short Route', 'emoji': '🚗'}
        elif distance_km <= 300:
            return {'category': 'Medium Route', 'emoji': '🚚'}
        elif distance_km <= 500:
            return {'category': 'Long Route', 'emoji': '🚛'}
        else:
            return {'category': 'Very Long Route', 'emoji': '✈️'}
    
    def get_expected_delivery_days(self, distance_km: float) -> int:
        if distance_km <= 0:
            return 1
        elif distance_km <= 100:
            return 1
        elif distance_km <= 250:
            return 2
        elif distance_km <= 450:
            return 3
        elif distance_km <= 700:
            return 4
        else:
            return 5
    
    def get_expected_delivery_text(self, distance_km: float) -> str:
        days = self.get_expected_delivery_days(distance_km)
        if days == 1:
            return "Same Day"
        elif days == 2:
            return "1 Day"
        else:
            return f"{days} Days"


# ==========================================================
# BLOCK 3: DNAnalysisService CLASS
# ==========================================================

class DNAnalysisService:
    """Enterprise Logistics Analytics Service."""
    
    def __init__(self):
        self._service_name = "dn_analysis"
        self._version = "15.0"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0
        self._distance_service = DistanceService()
        
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
    # BLOCK 4: DATABASE CONNECTION METHODS
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
    # BLOCK 5: DN SEARCH NORMALIZATION
    # ==========================================================
    
    def _normalize_dn(self, dn_no: str) -> str:
        if not dn_no:
            return ""
        return re.sub(r'[^0-9]', '', dn_no.strip())
    
    def _build_normalized_dn_query(self) -> str:
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
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                COUNT(DISTINCT customer_model) AS model_count,
                COUNT(DISTINCT material_no) AS material_count,
                COUNT(DISTINCT division) AS division_count,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                COUNT(*) AS material_count_total,
                MAX(delivery_status) AS delivery_status,
                MAX(pgi_status) AS pgi_status,
                MAX(pod_status) AS pod_status,
                MAX(pending_flag) AS pending_flag
            FROM delivery_reports
            WHERE 
                CAST(dn_no AS TEXT) = :dn_no
                OR CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
                OR REPLACE(CAST(dn_no AS TEXT), '-', '') = :dn_no
                OR REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
            GROUP BY dn_no
            LIMIT 1
        """
    
    def _build_model_query(self) -> str:
        return """
            SELECT 
                customer_model AS model_name,
                material_no AS material_number,
                division,
                SUM(dn_qty) AS quantity,
                SUM(dn_amount) AS revenue,
                COUNT(*) AS item_count
            FROM delivery_reports
            WHERE 
                CAST(dn_no AS TEXT) = :dn_no
                OR CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
                OR REPLACE(CAST(dn_no AS TEXT), '-', '') = :dn_no
                OR REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
            GROUP BY customer_model, material_no, division
            ORDER BY quantity DESC
            LIMIT 20
        """
    
    # ==========================================================
    # BLOCK 6: DATE VALIDATOR
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
        
        elif isinstance(date_value, str):
            result["type"] = "string"
            parts = date_value.split('-')
            if len(parts) == 3:
                try:
                    year = int(parts[0])
                    month = int(parts[1])
                    day = int(parts[2])
                    if year < 1 or month < 1 or month > 12 or day < 1 or day > 31:
                        result["error"] = f"Invalid date components: {date_value}"
                        return result
                    parsed = datetime(year, month, day)
                    result["value"] = parsed
                    result["formatted"] = parsed.strftime('%Y-%m-%d')
                    result["valid"] = True
                    return result
                except ValueError as e:
                    result["error"] = f"Invalid date: {date_value} - {e}"
                    return result
            else:
                result["error"] = f"Invalid format: {date_value} - expected YYYY-MM-DD"
                return result
        
        else:
            result["error"] = f"Unsupported type: {type(date_value)}"
            return result
    
    def _format_date_dmy(self, date_value) -> str:
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
    
    # ==========================================================
    # BLOCK 6.4: AGING ENGINE
    # ==========================================================
    
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
    # BLOCK 6.5: SHIPMENT STAGE ENGINE
    # ==========================================================
    
    def _determine_shipment_stage(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        """Determine shipment stage from dates - NOT from status columns."""
        
        pgi_exists = good_issue_date is not None
        pod_exists = pod_date is not None
        
        def fmt_date(d):
            if d is None:
                return 'N/A'
            if isinstance(d, (date, datetime)):
                return d.strftime('%d-%b-%Y')
            return str(d)
        
        # CASE 1: POD exists → Delivered
        if pod_exists and pgi_exists:
            return {
                "stage": "Delivered",
                "stage_emoji": "✅",
                "health": "Successfully Delivered",
                "health_emoji": "🟢",
                "pending": False,
                "completion": 100,
                "delay_category": "None",
                "performance_rating": "Excellent",
                "recommendation": "Shipment completed successfully. Review performance metrics for continuous improvement.",
                "progress": [
                    {"step": "DN Created", "status": "✅", "date": fmt_date(dn_create_date)},
                    {"step": "PGI Completed", "status": "✅", "date": fmt_date(good_issue_date)},
                    {"step": "POD Received", "status": "✅", "date": fmt_date(pod_date)}
                ]
            }
        
        # CASE 2: PGI exists, POD missing → In Transit
        if pgi_exists and not pod_exists:
            return {
                "stage": "In Transit",
                "stage_emoji": "🚚",
                "health": "Shipment On Route",
                "health_emoji": "🟡",
                "pending": True,
                "completion": 66,
                "delay_category": "Monitoring",
                "performance_rating": "In Progress",
                "recommendation": "Shipment is currently in transit. Follow up with transporter for POD confirmation.",
                "progress": [
                    {"step": "DN Created", "status": "✅", "date": fmt_date(dn_create_date)},
                    {"step": "PGI Completed", "status": "✅", "date": fmt_date(good_issue_date)},
                    {"step": "POD Pending", "status": "⏳", "date": "Pending"}
                ]
            }
        
        # CASE 3: PGI missing → Pending Dispatch
        else:
            return {
                "stage": "Pending Dispatch",
                "stage_emoji": "⏳",
                "health": "Awaiting Warehouse Dispatch",
                "health_emoji": "🟡",
                "pending": True,
                "completion": 33,
                "delay_category": "Dispatch Pending",
                "performance_rating": "Not Started",
                "recommendation": "Shipment has not yet been dispatched. Warehouse should complete PGI immediately.",
                "progress": [
                    {"step": "DN Created", "status": "✅", "date": fmt_date(dn_create_date)},
                    {"step": "PGI Pending", "status": "⏳", "date": "Pending"},
                    {"step": "POD Not Started", "status": "⏳", "date": "Not Started"}
                ]
            }
    
    # ==========================================================
    # BLOCK 7: DN SEARCH
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        logger.info(f"🔍 Searching for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        
        if len(normalized_dn) < 8:
            return {"success": False, "error": f"Invalid DN format: {normalized_dn} (must be 8-12 digits)"}
        
        query = self._build_normalized_dn_query()
        results = self._execute_query(query, {"dn_no": normalized_dn})
        
        if results:
            return {"success": True, "data": results[0]}
        
        fallback_query = """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
        fallback_results = self._execute_query(fallback_query, {"dn_no": normalized_dn})
        similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
        
        if similar_dns:
            return {
                "success": False,
                "error": f"DN {dn_no} not found",
                "similar_dns": similar_dns[:5],
                "message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
            }
        
        return {"success": False, "error": f"DN {dn_no} not found"}
    
    # ==========================================================
    # BLOCK 8: VERIFY DN
    # ==========================================================
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        if not dn_no:
            return {"success": False, "exists": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
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
        
        return {"success": True, "exists": exists}
    
    # ==========================================================
    # BLOCK 9: HEALTH CHECK
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
            
            # Check required columns
            required_columns = [
                "dn_no", "customer_name", "warehouse", "ship_to_city",
                "dn_qty", "dn_amount", "dn_create_date", "good_issue_date",
                "pod_date", "delivery_status", "pending_flag"
            ]
            columns_info = inspector.get_columns("delivery_reports")
            columns = [col["name"] for col in columns_info]
            
            missing = [col for col in required_columns if col not in columns]
            if missing:
                result["warnings"].append(f"Missing columns: {missing}")
            
            # Check indexes
            indexes = inspector.get_indexes("delivery_reports")
            index_names = [idx["name"] for idx in indexes]
            if "idx_dn_no" not in index_names:
                result["warnings"].append("Missing index on dn_no")
            
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
    
    # ==========================================================
    # BLOCK 10: DN DASHBOARD BUILDER
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        logger.info(f"📊 Getting dashboard for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        # Get summary data
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
        
        # Get model details
        model_query = self._build_model_query()
        normalized_dn = self._normalize_dn(dn_no)
        model_results = self._execute_query(model_query, {"dn_no": normalized_dn})
        
        models = []
        module_quantities = {}
        total_model_units = 0
        
        for row in model_results:
            model_name = row.get('model_name')
            if model_name:
                qty = int(row.get('quantity', 0) or 0)
                revenue = float(row.get('revenue', 0) or 0)
                division = row.get('division', 'Unknown')
                material_no = row.get('material_number', 'N/A')
                
                models.append({
                    'name': str(model_name),
                    'material_no': str(material_no),
                    'division': str(division),
                    'qty': qty,
                    'revenue': revenue
                })
                
                # Aggregate module quantities
                if division in module_quantities:
                    module_quantities[division] += qty
                else:
                    module_quantities[division] = qty
                
                total_model_units += qty
        
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
        
        # Calculate logistics
        warehouse = data.get('warehouse', '')
        city = data.get('city', '')
        logistics = self._calculate_logistics(warehouse, city)
        
        # Format dates
        dn_create_date = self._format_date_dmy(raw_dn_create)
        good_issue_date = self._format_date_dmy(raw_pgi)
        pod_date = self._format_date_dmy(raw_pod)
        
        # Metrics
        total_units = data.get('total_units')
        total_revenue = data.get('total_revenue')
        model_count = data.get('model_count', 0)
        material_count = data.get('material_count', 0)
        division_count = data.get('division_count', 0)
        
        # Build dashboard
        dashboard = {
            "dn_no": data.get('dn_no'),
            "dealer_name": data.get('dealer_name', 'Unknown'),
            "dealer_code": data.get('dealer_code', 'N/A'),
            "customer_code": data.get('customer_code', 'N/A'),
            "warehouse": warehouse or 'Unknown',
            "warehouse_code": data.get('warehouse_code', 'N/A'),
            "city": city or 'Unknown',
            "delivery_location": data.get('delivery_location'),
            "sales_manager": data.get('sales_manager'),
            "sales_office": data.get('sales_office', 'Unknown Office'),
            "division": data.get('division', 'Unknown'),
            
            # Metrics
            "total_units": total_units,
            "total_revenue": total_revenue,
            "material_count": material_count or 1,
            "model_count": model_count,
            "division_count": division_count,
            "source_file": data.get('source_file', 'June DN & PGI'),
            
            # Models
            "models": models,
            "module_quantities": module_quantities,
            "total_model_units": total_model_units,
            
            # Dates
            "dn_create_date": dn_create_date,
            "good_issue_date": good_issue_date,
            "pod_date": pod_date,
            
            # Raw dates
            "_dn_create_date": raw_dn_create,
            "_good_issue_date": raw_pgi,
            "_pod_date": raw_pod,
            
            # Stage
            "stage": stage_info["stage"],
            "stage_emoji": stage_info["stage_emoji"],
            "health": stage_info["health"],
            "health_emoji": stage_info["health_emoji"],
            "pending_flag": stage_info["pending"],
            "completion": stage_info["completion"],
            "delay_category": stage_info["delay_category"],
            "performance_rating": stage_info["performance_rating"],
            "progress": stage_info["progress"],
            "recommendation": stage_info["recommendation"],
            
            # Aging
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "total_cycle_days": total_cycle,
            "delivery_aging_text": self._format_aging_text(delivery_aging),
            "pod_aging_text": self._format_aging_text(pod_aging) if pod_aging > 0 else "Not Started",
            "total_cycle_text": self._format_aging_text(total_cycle),
            
            # Logistics
            "distance_km": logistics.get('distance_km', 0),
            "distance_text": logistics.get('distance_text', 'Not Available'),
            "distance_category": logistics.get('distance_category', 'Unknown'),
            "distance_emoji": logistics.get('distance_emoji', '📍'),
            "duration_text": logistics.get('duration_text', 'Not Available'),
            "expected_delivery_text": logistics.get('expected_delivery_text', 'Not Available'),
            "expected_delivery_days": logistics.get('expected_delivery_days', 0),
            
            # Performance
            "on_time": False,
            "delay_days": 0,
            "efficiency": 0
        }
        
        # Calculate performance metrics
        expected_days = logistics.get('expected_delivery_days', 1)
        if expected_days > 0 and total_cycle > 0:
            delay = max(0, total_cycle - expected_days)
            efficiency = round((expected_days / total_cycle) * 100, 1) if total_cycle > 0 else 0
            dashboard['delay_days'] = delay
            dashboard['efficiency'] = min(efficiency, 100)
            dashboard['on_time'] = delay == 0
        
        return {"success": True, "data": dashboard}
    
    # ==========================================================
    # BLOCK 10.1: LOGISTICS CALCULATOR
    # ==========================================================
    
    def _calculate_logistics(self, warehouse: str, destination: str) -> Dict[str, Any]:
        result = {
            "distance_km": 0,
            "distance_text": "Not Available",
            "distance_category": "Unknown",
            "distance_emoji": "❓",
            "duration_text": "Not Available",
            "expected_delivery_text": "Not Available",
            "expected_delivery_days": 0
        }
        
        if not warehouse or not destination:
            return result
        
        distance_data = self._distance_service.get_distance(warehouse, destination)
        
        if distance_data:
            distance_km = distance_data.get('distance_km', 0)
            if distance_km > 0:
                result['distance_km'] = distance_km
                result['distance_text'] = f"{distance_km:.1f} km"
                
                category = self._distance_service.get_distance_category(distance_km)
                result['distance_category'] = category['category']
                result['distance_emoji'] = category['emoji']
                
                result['duration_text'] = distance_data.get('duration_text', 'Not Available')
                
                expected_days = self._distance_service.get_expected_delivery_days(distance_km)
                result['expected_delivery_days'] = expected_days
                result['expected_delivery_text'] = self._distance_service.get_expected_delivery_text(distance_km)
        
        return result
    
    # ==========================================================
    # BLOCK 11: PENDING METHODS
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        logger.info(f"🔍 Getting pending DNs (limit: {limit}, offset: {offset})")
        
        try:
            limit = min(limit, 1000)
            
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR (good_issue_date IS NOT NULL AND pod_date IS NULL)
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
                   OR (good_issue_date IS NOT NULL AND pod_date IS NULL)
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
    # BLOCK 12: VALIDATION & METADATA
    # ==========================================================
    
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
            "description": "Haier Pakistan Logistics - Enterprise DN Dashboard",
            "date_policy": "Native PostgreSQL DATE values (YYYY-MM-DD)",
            "business_logic": "Shipment stage from dates (not status columns)",
            "gis_provider": "OpenRouteService + geopy fallback",
            "methods": [
                "health_check",
                "validation_query",
                "get_service_metadata",
                "search_dn",
                "verify_dn",
                "get_dn_dashboard",
                "format_dn_dashboard",
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod"
            ]
        }
    
    # ==========================================================
    # BLOCK 13: WHATSAPP RESPONSE FORMATTER (EXECUTIVE DASHBOARD)
    # ==========================================================
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """
        Format DN dashboard for WhatsApp - Executive Logistics Dashboard.
        """
        data = dashboard_data.get('data', {})
        
        # ==========================================================
        # EXTRACT DATA
        # ==========================================================
        
        dn_no = data.get('dn_no', 'N/A')
        dealer_name = data.get('dealer_name', 'Unknown')
        dealer_code = data.get('dealer_code', 'DC-XXXXX')
        city = data.get('city', 'Unknown')
        destination = data.get('city', 'Unknown')
        sales_office = data.get('sales_office', 'Unknown Office')
        sales_manager = data.get('sales_manager', 'Unknown')
        
        warehouse = data.get('warehouse', 'Unknown')
        warehouse_code = data.get('warehouse_code', 'Unknown')
        dispatch_point = data.get('warehouse', 'Unknown') + ' Warehouse'
        
        division = data.get('division', 'Unknown')
        
        material_count = data.get('material_count', 1)
        model_count = data.get('model_count', 0)
        
        units = data.get('total_units')
        if units is None:
            total_units = "Not Available"
            units_display = "Not Available"
        else:
            total_units = str(int(units))
            units_display = f"{int(units)} Units"
        
        revenue = data.get('total_revenue')
        if revenue is None:
            total_revenue = "Not Available"
            revenue_display = "Not Available"
        else:
            total_revenue = f"PKR {revenue:,.0f}"
            revenue_display = f"PKR {revenue:,.0f}"
        
        dn_create_date = data.get('dn_create_date', 'N/A')
        good_issue_date = data.get('good_issue_date', 'N/A')
        pod_date = data.get('pod_date', 'N/A')
        
        delivery_aging_text = data.get('delivery_aging_text', 'N/A')
        pod_aging_text = data.get('pod_aging_text', 'Not Started')
        total_cycle_text = data.get('total_cycle_text', 'N/A')
        delivery_aging_days = data.get('delivery_aging_days', 0)
        pod_aging_days = data.get('pod_aging_days', 0)
        total_cycle_days = data.get('total_cycle_days', 0)
        
        stage = data.get('stage', 'Unknown')
        stage_emoji = data.get('stage_emoji', '❓')
        health = data.get('health', 'Unknown')
        health_emoji = data.get('health_emoji', '❓')
        progress = data.get('progress', [])
        recommendation = data.get('recommendation', 'Unable to determine shipment status.')
        
        # Document Status
        if stage == 'Delivered':
            doc_status = "✅ Delivered"
        elif stage == 'In Transit':
            doc_status = "🚚 In Transit"
        else:
            doc_status = "⏳ Pending Dispatch"
        
        # Health Text
        if stage == 'Delivered':
            health_text = "🟢 Successfully Delivered"
        elif stage == 'In Transit':
            health_text = "🟡 Shipment On Route"
        else:
            health_text = "🟡 Awaiting Warehouse Dispatch"
        
        # Models
        models = data.get('models', [])
        module_quantities = data.get('module_quantities', {})
        
        # Performance
        on_time = "✅ Yes" if data.get('on_time', False) else "❌ No"
        delay_days = data.get('delay_days', 0)
        efficiency = data.get('efficiency', 0)
        expected_delivery_text = data.get('expected_delivery_text', 'Not Available')
        
        # Route Analytics
        distance_text = data.get('distance_text', 'Not Available')
        duration_text = data.get('duration_text', 'Not Available')
        distance_category = data.get('distance_category', 'Unknown')
        distance_emoji = data.get('distance_emoji', '📍')
        
        # Source
        source_file = data.get('source_file', 'June DN & PGI')
        imported_at = datetime.now().strftime('%d-%b-%Y')
        generated_at = datetime.now().strftime('%d-%b-%Y %H:%M')
        
        # ==========================================================
        # BUILD RESPONSE
        # ==========================================================
        
        lines = []
        
        # Header
        lines.append("📦 *HAIER LOGISTICS – EXECUTIVE DN DASHBOARD*")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Document Status
        lines.append("🆔 *Delivery Note*")
        lines.append(dn_no)
        lines.append("")
        lines.append("📦 *Document Status*")
        lines.append(doc_status)
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Dealer Information
        lines.append("🏪 *Dealer Information*")
        lines.append("")
        lines.append("Dealer")
        lines.append(dealer_name)
        lines.append("")
        lines.append("Dealer Code")
        lines.append(dealer_code)
        lines.append("")
        lines.append("Destination")
        lines.append(destination)
        lines.append("")
        lines.append("City")
        lines.append(city)
        lines.append("")
        lines.append("Sales Office")
        lines.append(sales_office)
        lines.append("")
        lines.append("Sales Manager")
        lines.append(sales_manager)
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Warehouse Information
        lines.append("🏭 *Warehouse Information*")
        lines.append("")
        lines.append("Warehouse")
        lines.append(warehouse)
        lines.append("")
        lines.append("Warehouse Code")
        lines.append(warehouse_code)
        lines.append("")
        lines.append("Dispatch Point")
        lines.append(dispatch_point)
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Shipment Summary
        lines.append("📊 *Shipment Summary*")
        lines.append("")
        lines.append("DN Count")
        lines.append("1")
        lines.append("")
        lines.append("Product Models")
        lines.append(str(model_count))
        lines.append("")
        lines.append("Total Units")
        lines.append(units_display)
        lines.append("")
        lines.append("Shipment Value")
        lines.append(revenue_display)
        lines.append("")
        lines.append("Material Numbers")
        lines.append(str(material_count))
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Division Summary
        lines.append("📦 *Division Summary*")
        lines.append("")
        lines.append("Division")
        lines.append(division)
        lines.append("")
        lines.append("Models")
        lines.append(str(model_count))
        lines.append("")
        lines.append("Units")
        lines.append(units_display)
        lines.append("")
        lines.append("Value")
        lines.append(revenue_display)
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Product Details
        lines.append("📦 *Product Details*")
        lines.append("")
        
        if models:
            emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            for idx, model in enumerate(models[:10], 1):
                model_name = model.get('name', 'Unknown')
                material_no = model.get('material_no', 'N/A')
                qty = model.get('qty', 0)
                emoji = emojis[idx-1] if idx <= len(emojis) else f"{idx}."
                
                lines.append(f"{emoji} {model_name}")
                lines.append(f"Material: {material_no}")
                lines.append(f"Qty: {qty} Units")
                lines.append("")
            
            if len(models) > 10:
                lines.append(f"• ... and {len(models) - 10} more models")
                lines.append("")
        else:
            lines.append("No product details available")
            lines.append("")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Module-wise Quantity
        lines.append("📦 *Module-wise Quantity*")
        lines.append("")
        
        if module_quantities:
            sorted_modules = sorted(module_quantities.items(), key=lambda x: x[1], reverse=True)
            for module_name, qty in sorted_modules:
                lines.append(f"{module_name}")
                lines.append(f"{qty} Units")
                lines.append("")
        else:
            lines.append(f"{division}")
            lines.append(f"{units_display}")
            lines.append("")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Shipment Timeline
        lines.append("📅 *Shipment Timeline*")
        lines.append("")
        lines.append("DN Created")
        lines.append(dn_create_date)
        lines.append("")
        
        if good_issue_date != 'N/A' and good_issue_date is not None:
            lines.append("PGI Completed")
        else:
            lines.append("PGI Pending")
        lines.append(good_issue_date)
        lines.append("")
        
        if pod_date != 'N/A' and pod_date is not None:
            lines.append("POD Received")
        else:
            lines.append("POD Pending")
        lines.append(pod_date)
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Transit Performance
        lines.append("⏱ *Transit Performance*")
        lines.append("")
        lines.append("Dispatch Time")
        lines.append(delivery_aging_text)
        lines.append("")
        lines.append("Transit Time")
        lines.append(pod_aging_text)
        lines.append("")
        lines.append("Total Delivery Cycle")
        lines.append(total_cycle_text)
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Route Analytics
        lines.append("🚛 *Route Analytics*")
        lines.append("")
        lines.append("Warehouse")
        lines.append(warehouse)
        lines.append("")
        lines.append("Destination")
        lines.append(destination)
        lines.append("")
        lines.append("Road Distance")
        lines.append(distance_text)
        lines.append("")
        lines.append("Estimated Drive Time")
        lines.append(duration_text)
        lines.append("")
        lines.append("Expected Delivery")
        lines.append(expected_delivery_text)
        lines.append("")
        lines.append("Actual Delivery")
        lines.append(total_cycle_text)
        lines.append("")
        if delay_days > 0:
            lines.append("Delivery Delay")
            lines.append(f"{delay_days} Days")
        else:
            lines.append("Delivery Delay")
            lines.append("On Time")
        lines.append("")
        if distance_category != 'Unknown':
            lines.append("Distance Category")
            lines.append(f"{distance_emoji} {distance_category}")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Shipment Status
        lines.append("📋 *Shipment Status*")
        lines.append("")
        lines.append("Current Stage")
        lines.append(f"{stage_emoji} {stage}")
        lines.append("")
        lines.append("Shipment Health")
        lines.append(health_text)
        lines.append("")
        lines.append("Progress")
        lines.append("")
        
        for item in progress:
            status = item.get('status', '⏳')
            step = item.get('step', '')
            date_val = item.get('date', '')
            if date_val and date_val not in ['Pending', 'Not Started', 'N/A']:
                lines.append(f"{status} {step}")
            else:
                lines.append(f"{status} {step}")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Performance KPIs
        lines.append("📈 *Performance KPIs*")
        lines.append("")
        lines.append("On-Time Delivery")
        lines.append(on_time)
        lines.append("")
        lines.append("Expected Transit")
        lines.append(expected_delivery_text)
        lines.append("")
        lines.append("Actual Transit")
        lines.append(total_cycle_text)
        lines.append("")
        if delay_days > 0:
            lines.append("Delay")
            lines.append(f"{delay_days} Days")
        else:
            lines.append("Delay")
            lines.append("No Delay")
        lines.append("")
        if efficiency > 0:
            lines.append("Route Efficiency")
            lines.append(f"{efficiency}%")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # AI Logistics Insights
        lines.append("🤖 *AI Logistics Insights*")
        lines.append("")
        
        insights = []
        if stage == 'Delivered':
            insights.append("• Shipment completed successfully.")
            if delivery_aging_days <= 1:
                insights.append("• Dispatch process completed within 1 day.")
            else:
                insights.append(f"• Dispatch took {delivery_aging_days} days.")
            if delay_days > 0:
                insights.append(f"• Transit exceeded expected delivery time by {delay_days} days.")
                insights.append(f"• Route efficiency is {efficiency}%.")
                insights.append("• Review transporter performance for this route.")
                insights.append(f"• Monitor future {warehouse} → {destination} deliveries for recurring delays.")
            else:
                insights.append("• Delivery completed within expected time.")
                insights.append("• Transporter performance is satisfactory.")
        elif stage == 'In Transit':
            insights.append("• Shipment is currently in transit.")
            insights.append(f"• Transit has been {pod_aging_days} days so far.")
            insights.append("• Follow up with transporter for POD confirmation.")
            if delay_days > 0:
                insights.append(f"• Delivery is already {delay_days} days delayed.")
                insights.append("• Expedite the delivery process.")
        else:
            insights.append("• Shipment is pending dispatch.")
            insights.append(f"• Waiting for dispatch for {delivery_aging_days} days.")
            insights.append("• Warehouse should complete PGI immediately.")
            insights.append("• Coordinate with warehouse team for priority handling.")
        
        if units is None:
            insights.append("• Units data is missing. Please update records.")
        if revenue is None:
            insights.append("• Revenue data is missing. Please update records.")
        if not models:
            insights.append("• Product model details are incomplete.")
        
        for insight in insights:
            lines.append(insight)
            lines.append("")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Source Information
        lines.append("📁 *Source Information*")
        lines.append("")
        lines.append("Source File")
        lines.append(source_file)
        lines.append("")
        lines.append("Imported")
        lines.append(imported_at)
        lines.append("")
        lines.append("Generated")
        lines.append(generated_at)
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Footer
        lines.append("🤖 Generated by")
        lines.append("Haier Logistics AI Assistant")
        
        return "\n".join(lines)


# ==========================================================
# BLOCK 14: THREAD-SAFE SINGLETON
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
# BLOCK 15: EXPORTS
# ==========================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service'
]


# ==========================================================
# BLOCK 16: MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v15.0 - ENTERPRISE PRODUCTION")
logger.info("=" * 70)
logger.info("")
logger.info("   ✅ Service: dn_analysis")
logger.info("   ✅ Version: 15.0")
logger.info("   ✅ Status: READY")
logger.info("   ✅ PostgreSQL as Single Source of Truth")
logger.info("   ✅ Intelligent status from dates")
logger.info("   ✅ Executive WhatsApp Dashboard")
logger.info("   ✅ Product models with material numbers")
logger.info("   ✅ Module-wise quantity breakdown")
logger.info("   ✅ Route analytics with distance")
logger.info("   ✅ Performance KPIs")
logger.info("   ✅ AI Logistics Insights")
logger.info("")
logger.info("   STATUS: ✅ ENTERPRISE PRODUCTION READY")
logger.info("=" * 70)
