# ==========================================================
# FILE: app/services/distance_service.py (v4.0 - FULLY INTEGRATED)
# PURPOSE: Distance calculation with PostgreSQL + OpenRouteService
# VERSION: 4.0 - FULLY INTEGRATED WITH ALL SERVICES
# ==========================================================

from geopy.geocoders import Nominatim
from geopy.distance import geodesic, great_circle
from geopy.extra.rate_limiter import RateLimiter
from typing import Optional, Tuple, Dict, Any, List
from loguru import logger
import time
import os
import re
import requests
import json
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

# ==========================================================
# BLOCK 1: POSTGRESQL IMPORTS - FOR DIRECT DB ACCESS
# ==========================================================

try:
    from app.models import DeliveryReport
    from app.database import SessionLocal
    POSTGRES_AVAILABLE = True
    logger.info("✅ PostgreSQL available for distance service")
except ImportError as e:
    logger.warning(f"⚠️ PostgreSQL not available: {e}")
    POSTGRES_AVAILABLE = False
    DeliveryReport = None
    SessionLocal = None

# ==========================================================
# BLOCK 2: DISTANCE SERVICE CLASS (ENHANCED)
# ==========================================================

class DistanceService:
    """
    Distance calculation service with PostgreSQL integration.
    
    Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                    DistanceService                         │
    │  ┌──────────────────────────────────────────────────────┐  │
    │  │  PostgreSQL Integration                             │  │
    │  │  - get_dealer_cities()                             │  │
    │  │  - get_warehouse_cities()                          │  │
    │  │  - get_warehouse_dealers()                         │  │
    │  └──────────────────────────────────────────────────────┘  │
    │  ┌──────────────────────────────────────────────────────┐  │
    │  │  OpenRouteService API (Road Distance)               │  │
    │  │  - get_road_distance()                             │  │
    │  │  - calculate_warehouse_distance()                  │  │
    │  └──────────────────────────────────────────────────────┘  │
    │  ┌──────────────────────────────────────────────────────┐  │
    │  │  Geocoding (Nominatim)                              │  │
    │  │  - get_coordinates()                               │  │
    │  │  - _normalize_city()                               │  │
    │  └──────────────────────────────────────────────────────┘  │
    │  ┌──────────────────────────────────────────────────────┐  │
    │  │  Batch Processing                                   │  │
    │  │  - get_warehouse_coverage()                        │  │
    │  │  - calculate_dealer_distances()                    │  │
    │  └──────────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
    PostgreSQL          OpenRouteService    Geocoding API
    delivery_reports    (Road Distance)     (Coordinates)
    """
    
    _instance = None
    
    def __new__(cls):
        """Singleton pattern - only one instance needed."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        try:
            # ==========================================================
            # BLOCK 2.1: Geocoding Setup
            # ==========================================================
            self.geolocator = Nominatim(
                user_agent="whatsapp_ai_agent",
                timeout=10
            )
            self.geocode = RateLimiter(
                self.geolocator.geocode, 
                min_delay_seconds=1
            )
            self.cache = {}
            
            # ==========================================================
            # BLOCK 2.2: OpenRouteService API - Road Distance
            # ==========================================================
            self.ors_api_key = os.getenv("OPENROUTE_API_KEY", "")
            self.ors_base_url = "https://api.openrouteservice.org/v2/directions/driving-car"
            
            # ==========================================================
            # BLOCK 2.3: City Name Mapping
            # ==========================================================
            self.city_mapping = {
                # Gilgit region
                "gilget": "Gilgit",
                "gilgit": "Gilgit",
                "gliget": "Gilgit",
                "gulgit": "Gilgit",
                
                # Major cities
                "islamabad": "Islamabad",
                "lahore": "Lahore",
                "karachi": "Karachi",
                "rawalpindi": "Rawalpindi",
                "attock": "Attock",
                "wah cantt": "Wah Cantt",
                "wah": "Wah Cantt",
                "jand": "Jand",
                "kamra cantt": "Kamra Cantt",
                "kamra": "Kamra Cantt",
                "sukkur": "Sukkur",
                "hyderabad": "Hyderabad",
                "multan": "Multan",
                "faisalabad": "Faisalabad",
                "gujranwala": "Gujranwala",
                "sialkot": "Sialkot",
                "peshawar": "Peshawar",
                "quetta": "Quetta",
                "sahiwal": "Sahiwal",
                "gujrat": "Gujrat",
                "sheikhupura": "Sheikhupura",
                "jhelum": "Jhelum",
                "mianwali": "Mianwali",
                "bhalwal": "Bhalwal",
                "rawalakot": "Rawalakot",
                "bagh": "Bagh",
                "muzaffarabad": "Muzaffarabad",
                "chakwal": "Chakwal",
                "mandi bahauddin": "Mandi Bahauddin",
                "sargodha": "Sargodha",
                "bhakkar": "Bhakkar",
                "layyah": "Layyah",
                "muzaffargarh": "Muzaffargarh",
                "dera ghazi khan": "Dera Ghazi Khan",
                "khanewal": "Khanewal",
                "vehari": "Vehari",
                "pakpattan": "Pakpattan",
                "okara": "Okara",
                "kasur": "Kasur",
                "nankana sahib": "Nankana Sahib",
                "hafizabad": "Hafizabad",
                "mandi": "Mandi",
                "sambrial": "Sambrial",
                "wazirabad": "Wazirabad",
                
                # Warehouse mappings
                "wh-khi-01": "Karachi",
                "wh khi 01": "Karachi",
                "whkhi01": "Karachi",
                "khi": "Karachi",
                "wh-lhe-01": "Lahore",
                "wh-lhe-02": "Lahore",
                "wh-isb-01": "Islamabad",
                "wh-rwp-01": "Rawalpindi",
                "wh-pew-01": "Peshawar",
                
                # Dealer name mappings
                "chn": "Karachi",
                "marhaba": "Karachi",
                "marhaba electronics": "Karachi",
                "marhaba electronics chn": "Karachi",
            }
            
            # ==========================================================
            # BLOCK 2.4: Fallback Coordinates
            # ==========================================================
            self.fallback_coords = {
                # Major cities
                "gilgit": (35.9189, 74.3123),
                "rawalpindi": (33.5651, 73.0169),
                "islamabad": (33.6844, 73.0479),
                "lahore": (31.5204, 74.3587),
                "karachi": (24.8607, 67.0011),
                "hyderabad": (25.3925, 68.3737),
                "multan": (30.1575, 71.5249),
                "faisalabad": (31.4504, 73.1350),
                "peshawar": (34.0151, 71.5249),
                "quetta": (30.1798, 66.9750),
                "sialkot": (32.4945, 74.5229),
                "gujranwala": (32.1627, 74.1883),
                "attock": (33.8886, 72.6641),
                "wah cantt": (33.7700, 72.7500),
                "jand": (33.7800, 72.0200),
                "kamra cantt": (33.7500, 73.0000),
                "sukkur": (27.7051, 68.8578),
                "sahiwal": (30.6659, 73.1089),
                "gujrat": (32.5736, 74.0750),
                "sheikhupura": (31.7131, 73.9783),
                "jhelum": (32.9345, 73.7310),
                "mianwali": (32.5769, 71.5253),
                "sargodha": (32.0836, 72.6711),
                "dera ghazi khan": (30.0500, 70.6333),
                "okara": (30.8081, 73.4454),
                "kasur": (31.1189, 74.4500),
                
                # Warehouse coordinates
                "wh-khi-01": (24.8607, 67.0011),
                "wh khi 01": (24.8607, 67.0011),
                "whkhi01": (24.8607, 67.0011),
                "khi": (24.8607, 67.0011),
                "chn": (24.8607, 67.0011),
                "marhaba": (24.8607, 67.0011),
                "marhaba electronics": (24.8607, 67.0011),
                "marhaba electronics chn": (24.8607, 67.0011),
            }
            
            self._initialized = True
            logger.info("✅ DistanceService v4.0 initialized")
            logger.info(f"📋 Loaded {len(self.city_mapping)} city name mappings")
            logger.info(f"📋 Loaded {len(self.fallback_coords)} fallback coordinates")
            
            if self.ors_api_key:
                logger.info("✅ OpenRouteService API key configured - ROAD DISTANCE AVAILABLE")
            else:
                logger.warning("⚠️ No OpenRouteService API key - using air distance only")
                logger.warning("   Get free API key: https://openrouteservice.org/")
                
        except Exception as e:
            logger.error(f"❌ DistanceService initialization failed: {e}")
            self._initialized = False


# ==========================================================
# BLOCK 3: NORMALIZE CITY METHOD
# ==========================================================

    def _normalize_city(self, city: str) -> str:
        """Normalize city names for better geocoding."""
        if not city:
            return city
        
        logger.debug(f"🔍 Normalizing: '{city}'")
        
        city_lower = city.lower().strip()
        normalized = self.city_mapping.get(city_lower, city)
        
        # Handle warehouse codes
        if city_lower.startswith('wh-') or city_lower.startswith('wh '):
            parts = city_lower.replace('wh-', '').replace('wh ', '').split('-')
            if parts:
                city_code = parts[0].upper()
                if city_code in ['KHI', 'LHE', 'ISB', 'RWP', 'PEW']:
                    mapping = {
                        'KHI': 'Karachi',
                        'LHE': 'Lahore', 
                        'ISB': 'Islamabad',
                        'RWP': 'Rawalpindi',
                        'PEW': 'Peshawar'
                    }
                    normalized = mapping.get(city_code, normalized)
                    logger.info(f"🔍 Warehouse code '{city_code}' → '{normalized}'")
        
        if normalized != city:
            logger.info(f"🔍 City name normalized: '{city}' → '{normalized}'")
        
        return normalized


# ==========================================================
# BLOCK 4: GET COORDINATES METHOD
# ==========================================================

    def get_coordinates(self, location: str) -> Optional[Tuple[float, float]]:
        """Get latitude and longitude for a location."""
        if not location:
            return None
        
        location = self._normalize_city(location)
        
        # Check cache
        cache_key = location.lower().strip()
        if cache_key in self.cache:
            logger.debug(f"📍 Cache hit for: {location}")
            return self.cache[cache_key]
        
        # Check fallback coordinates
        if cache_key in self.fallback_coords:
            coords = self.fallback_coords[cache_key]
            logger.info(f"📍 Using fallback coordinates for: {location} → ({coords[0]:.4f}, {coords[1]:.4f})")
            self.cache[cache_key] = coords
            return coords
        
        try:
            logger.info(f"🔍 Geocoding: {location}")
            
            search_location = location
            if "pakistan" not in location.lower() and len(location.split()) < 3:
                search_location = f"{location}, Pakistan"
            
            location_data = self.geocode(search_location)
            
            if location_data:
                lat = location_data.latitude
                lng = location_data.longitude
                logger.info(f"✅ Found: {location} → ({lat:.4f}, {lng:.4f})")
                self.cache[cache_key] = (lat, lng)
                return (lat, lng)
            else:
                logger.warning(f"❌ Could not geocode: {location}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Geocoding error for {location}: {e}")
            return None


# ==========================================================
# BLOCK 5: GET ROAD DISTANCE METHOD
# ==========================================================

    def get_road_distance(self, origin: str, destination: str) -> Optional[Dict[str, Any]]:
        """
        Get road distance using OpenRouteService API.
        
        Returns:
            Dict with distance_km, duration_min, duration_hours
        """
        if not self.ors_api_key:
            logger.warning("⚠️ No OpenRouteService API key - cannot get road distance")
            return None
        
        try:
            # Get coordinates
            origin_coords = self.get_coordinates(origin)
            dest_coords = self.get_coordinates(destination)
            
            if not origin_coords or not dest_coords:
                return None
            
            # OpenRouteService expects: longitude,latitude
            origin_point = f"{origin_coords[1]},{origin_coords[0]}"
            dest_point = f"{dest_coords[1]},{dest_coords[0]}"
            
            url = f"{self.ors_base_url}?api_key={self.ors_api_key}&start={origin_point}&end={dest_point}"
            
            logger.info(f"🚗 Getting road distance: {origin} → {destination}")
            
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if 'features' in data and len(data['features']) > 0:
                    properties = data['features'][0]['properties']
                    summary = properties.get('summary', {})
                    
                    distance_km = summary.get('distance', 0) / 1000
                    duration_min = summary.get('duration', 0) / 60
                    
                    return {
                        "success": True,
                        "distance_km": round(distance_km, 1),
                        "duration_min": round(duration_min, 0),
                        "duration_hours": round(duration_min / 60, 1),
                        "source": "OpenRouteService"
                    }
            else:
                logger.warning(f"⚠️ OpenRouteService API error: {response.status_code}")
                
        except requests.Timeout:
            logger.warning("⚠️ OpenRouteService request timed out")
        except Exception as e:
            logger.error(f"❌ OpenRouteService error: {e}")
        
        return None


# ==========================================================
# BLOCK 6: CALCULATE DISTANCE (MAIN METHOD)
# ==========================================================

    def calculate_distance(self, origin: str, destination: str) -> Dict[str, Any]:
        """
        Calculate distance between two locations (ROAD DISTANCE PREFERRED).
        
        Returns:
            Dict with success, distance_km, distance_miles, driving_time
        """
        # Normalize city names
        origin = self._normalize_city(origin)
        destination = self._normalize_city(destination)
        
        # Check cache
        cache_key = f"{origin.lower().strip()}|{destination.lower().strip()}"
        if cache_key in self.cache:
            logger.debug(f"📏 Cache hit for: {origin} → {destination}")
            result = self.cache[cache_key]
            result['from_cache'] = True
            return result
        
        try:
            logger.info(f"📏 Calculating distance: {origin} → {destination}")
            
            # ==========================================================
            # STEP 1: Try OpenRouteService for road distance
            # ==========================================================
            road_dist = self.get_road_distance(origin, destination)
            
            if road_dist and road_dist.get('success'):
                result = {
                    "success": True,
                    "origin": origin,
                    "destination": destination,
                    "distance_km": road_dist['distance_km'],
                    "distance_miles": round(road_dist['distance_km'] * 0.621371, 1),
                    "approx_driving_minutes": int(road_dist['duration_min']),
                    "approx_driving_hours": road_dist['duration_hours'],
                    "distance_type": "road",
                    "source": "OpenRouteService"
                }
                self.cache[cache_key] = result
                logger.info(f"✅ Road distance: {origin} → {destination} = {result['distance_km']} km")
                return result
            
            # ==========================================================
            # STEP 2: Fallback to air distance
            # ==========================================================
            logger.info(f"📏 Using air distance fallback: {origin} → {destination}")
            
            origin_coords = self.get_coordinates(origin)
            dest_coords = self.get_coordinates(destination)
            
            if not origin_coords or not dest_coords:
                result = {
                    "success": False,
                    "error": "Could not geocode locations",
                    "origin": origin,
                    "destination": destination
                }
                self.cache[cache_key] = result
                return result
            
            geodesic_dist = geodesic(origin_coords, dest_coords).kilometers
            approx_driving = geodesic_dist * 1.3
            avg_speed = 50
            
            result = {
                "success": True,
                "origin": origin,
                "destination": destination,
                "origin_coords": {"lat": origin_coords[0], "lng": origin_coords[1]},
                "destination_coords": {"lat": dest_coords[0], "lng": dest_coords[1]},
                "distance_km": round(geodesic_dist, 1),
                "distance_miles": round(geodesic_dist * 0.621371, 1),
                "approx_driving_km": round(approx_driving, 1),
                "approx_driving_hours": round(approx_driving / avg_speed, 1),
                "approx_driving_minutes": int((approx_driving / avg_speed) * 60),
                "distance_type": "air",
                "source": "Haversine (fallback)"
            }
            
            self.cache[cache_key] = result
            logger.info(f"✅ Air distance (fallback): {origin} → {destination} = {result['distance_km']} km")
            return result
            
        except Exception as e:
            logger.error(f"❌ Distance calculation error: {e}")
            result = {
                "success": False,
                "error": str(e),
                "origin": origin,
                "destination": destination
            }
            self.cache[cache_key] = result
            return result


# ==========================================================
# BLOCK 7: WAREHOUSE DISTANCE (ENHANCED)
# ==========================================================

    def calculate_warehouse_distance(self, warehouse: str, dealer_city: str) -> Dict[str, Any]:
        """Calculate distance from warehouse to dealer city."""
        if not warehouse or not dealer_city:
            return {
                "success": False,
                "error": "Warehouse and dealer city required"
            }
        
        warehouse = self._normalize_city(warehouse)
        dealer_city = self._normalize_city(dealer_city)
        
        return self.calculate_distance(warehouse, dealer_city)


# ==========================================================
# BLOCK 8: POSTGRESQL INTEGRATION - GET DEALER CITIES
# ==========================================================

    def get_dealer_cities(self, dealer_name: str = None) -> List[Dict[str, Any]]:
        """
        Get unique dealer cities from PostgreSQL.
        
        Args:
            dealer_name: Optional - get cities for specific dealer
        
        Returns:
            List of dicts with city, count, dealers
        """
        if not POSTGRES_AVAILABLE or not SessionLocal:
            logger.warning("⚠️ PostgreSQL not available - cannot get dealer cities")
            return []
        
        try:
            db = SessionLocal()
            
            if dealer_name:
                # Get cities for specific dealer
                results = db.query(
                    DeliveryReport.ship_to_city,
                    func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                    func.sum(DeliveryReport.dn_amount).label('total_revenue')
                ).filter(
                    func.lower(DeliveryReport.customer_name) == func.lower(dealer_name),
                    DeliveryReport.ship_to_city.isnot(None),
                    DeliveryReport.ship_to_city != ''
                ).group_by(
                    DeliveryReport.ship_to_city
                ).order_by(
                    desc('total_revenue')
                ).all()
            else:
                # Get all cities with statistics
                results = db.query(
                    DeliveryReport.ship_to_city,
                    func.count(distinct(DeliveryReport.customer_name)).label('dealer_count'),
                    func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                    func.sum(DeliveryReport.dn_amount).label('total_revenue')
                ).filter(
                    DeliveryReport.ship_to_city.isnot(None),
                    DeliveryReport.ship_to_city != ''
                ).group_by(
                    DeliveryReport.ship_to_city
                ).order_by(
                    desc('dealer_count')
                ).all()
            
            db.close()
            
            cities = []
            for r in results:
                city = {
                    "city": r[0],
                    "dealer_count": r[1] if len(r) > 1 else 0,
                    "dn_count": r[2] if len(r) > 2 else 0,
                    "total_revenue": float(r[3] or 0) if len(r) > 3 else 0
                }
                cities.append(city)
            
            logger.info(f"✅ Found {len(cities)} dealer cities from PostgreSQL")
            return cities
            
        except Exception as e:
            logger.error(f"❌ PostgreSQL dealer cities error: {e}")
            return []


# ==========================================================
# BLOCK 9: POSTGRESQL INTEGRATION - GET WAREHOUSE CITIES
# ==========================================================

    def get_warehouse_cities(self, warehouse: str = None) -> List[Dict[str, Any]]:
        """
        Get unique warehouse cities from PostgreSQL.
        
        Args:
            warehouse: Optional - get cities for specific warehouse
        
        Returns:
            List of dicts with warehouse, city, dn_count
        """
        if not POSTGRES_AVAILABLE or not SessionLocal:
            logger.warning("⚠️ PostgreSQL not available - cannot get warehouse cities")
            return []
        
        try:
            db = SessionLocal()
            
            if warehouse:
                # Get cities for specific warehouse
                results = db.query(
                    DeliveryReport.ship_to_city,
                    func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                    func.sum(DeliveryReport.dn_amount).label('total_revenue')
                ).filter(
                    func.lower(DeliveryReport.warehouse) == func.lower(warehouse),
                    DeliveryReport.ship_to_city.isnot(None),
                    DeliveryReport.ship_to_city != ''
                ).group_by(
                    DeliveryReport.ship_to_city
                ).order_by(
                    desc('dn_count')
                ).all()
            else:
                # Get all warehouse-city pairs
                results = db.query(
                    DeliveryReport.warehouse,
                    DeliveryReport.ship_to_city,
                    func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                    func.sum(DeliveryReport.dn_amount).label('total_revenue')
                ).filter(
                    DeliveryReport.warehouse.isnot(None),
                    DeliveryReport.warehouse != '',
                    DeliveryReport.ship_to_city.isnot(None),
                    DeliveryReport.ship_to_city != ''
                ).group_by(
                    DeliveryReport.warehouse,
                    DeliveryReport.ship_to_city
                ).order_by(
                    desc('dn_count')
                ).all()
            
            db.close()
            
            cities = []
            for r in results:
                city = {
                    "warehouse": r[0],
                    "city": r[1],
                    "dn_count": r[2] if len(r) > 2 else 0,
                    "total_revenue": float(r[3] or 0) if len(r) > 3 else 0
                }
                cities.append(city)
            
            logger.info(f"✅ Found {len(cities)} warehouse-city pairs from PostgreSQL")
            return cities
            
        except Exception as e:
            logger.error(f"❌ PostgreSQL warehouse cities error: {e}")
            return []


# ==========================================================
# BLOCK 10: WAREHOUSE COVERAGE (ENHANCED WITH POSTGRESQL)
# ==========================================================

    def get_warehouse_coverage(self, warehouse: str) -> Dict[str, Any]:
        """
        Get complete warehouse coverage from PostgreSQL.
        
        This method:
        1. Queries PostgreSQL for all cities served by warehouse
        2. Calculates distances to each city
        3. Returns coverage statistics
        
        Returns:
            Dict with coverage statistics and city distances
        """
        if not warehouse:
            return {
                "success": False,
                "error": "Warehouse required"
            }
        
        try:
            warehouse = self._normalize_city(warehouse)
            logger.info(f"📍 Getting coverage for warehouse: {warehouse}")
            
            # ==========================================================
            # STEP 1: Get cities from PostgreSQL
            # ==========================================================
            city_data = self.get_warehouse_cities(warehouse)
            
            if not city_data:
                return {
                    "success": False,
                    "error": f"No cities found for warehouse: {warehouse}",
                    "warehouse": warehouse
                }
            
            # ==========================================================
            # STEP 2: Calculate distances to each city
            # ==========================================================
            distances = []
            total_distance = 0
            max_distance = 0
            min_distance = float('inf')
            
            for city_info in city_data:
                city = city_info.get('city')
                if not city:
                    continue
                
                # Calculate distance
                dist = self.calculate_warehouse_distance(warehouse, city)
                
                if dist.get('success'):
                    distance_km = dist.get('distance_km', 0)
                    distances.append({
                        "city": city,
                        "distance_km": distance_km,
                        "driving_hours": dist.get('approx_driving_hours', 0),
                        "driving_minutes": dist.get('approx_driving_minutes', 0),
                        "distance_type": dist.get('distance_type', 'unknown'),
                        "dn_count": city_info.get('dn_count', 0),
                        "total_revenue": city_info.get('total_revenue', 0)
                    })
                    total_distance += distance_km
                    max_distance = max(max_distance, distance_km)
                    min_distance = min(min_distance, distance_km)
            
            if not distances:
                return {
                    "success": False,
                    "error": "Could not calculate distances for any city",
                    "warehouse": warehouse
                }
            
            # ==========================================================
            # STEP 3: Build coverage response
            # ==========================================================
            avg_distance = total_distance / len(distances)
            
            # Sort by distance
            distances_sorted = sorted(distances, key=lambda x: x.get('distance_km', 0))
            
            result = {
                "success": True,
                "warehouse": warehouse,
                "total_cities": len(distances),
                "total_distance_km": round(total_distance, 1),
                "average_distance_km": round(avg_distance, 1),
                "max_distance_km": round(max_distance, 1),
                "min_distance_km": round(min_distance, 1) if min_distance != float('inf') else 0,
                "cities": distances_sorted,
                "summary": {
                    "closest_city": distances_sorted[0]['city'] if distances_sorted else 'N/A',
                    "closest_distance": distances_sorted[0]['distance_km'] if distances_sorted else 0,
                    "farthest_city": distances_sorted[-1]['city'] if distances_sorted else 'N/A',
                    "farthest_distance": distances_sorted[-1]['distance_km'] if distances_sorted else 0
                }
            }
            
            logger.info(f"✅ Coverage for {warehouse}: {len(distances)} cities, avg {avg_distance:.1f} km")
            return result
            
        except Exception as e:
            logger.error(f"❌ Warehouse coverage error: {e}")
            return {
                "success": False,
                "error": str(e),
                "warehouse": warehouse
            }


# ==========================================================
# BLOCK 11: POSTGRESQL INTEGRATION - GET WAREHOUSE DEALERS
# ==========================================================

    def get_warehouse_dealers(self, warehouse: str) -> List[Dict[str, Any]]:
        """
        Get all dealers served by a warehouse from PostgreSQL.
        
        Returns:
            List of dealers with city and distance
        """
        if not warehouse or not POSTGRES_AVAILABLE or not SessionLocal:
            return []
        
        try:
            db = SessionLocal()
            warehouse = self._normalize_city(warehouse)
            
            results = db.query(
                DeliveryReport.customer_name,
                DeliveryReport.ship_to_city,
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue')
            ).filter(
                func.lower(DeliveryReport.warehouse) == func.lower(warehouse),
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != '',
                DeliveryReport.ship_to_city.isnot(None),
                DeliveryReport.ship_to_city != ''
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.ship_to_city
            ).order_by(
                desc('total_revenue')
            ).all()
            
            db.close()
            
            dealers = []
            for r in results:
                dealer_city = r[1]
                distance = self.calculate_warehouse_distance(warehouse, dealer_city)
                
                dealers.append({
                    "name": r[0],
                    "city": dealer_city,
                    "dn_count": r[2] or 0,
                    "total_revenue": float(r[3] or 0),
                    "distance_km": distance.get('distance_km') if distance.get('success') else None,
                    "driving_hours": distance.get('approx_driving_hours') if distance.get('success') else None
                })
            
            logger.info(f"✅ Found {len(dealers)} dealers for warehouse: {warehouse}")
            return dealers
            
        except Exception as e:
            logger.error(f"❌ Warehouse dealers error: {e}")
            return []


# ==========================================================
# BLOCK 12: BATCH DISTANCE CALCULATION
# ==========================================================

    def calculate_dealer_distances(self, warehouse: str, dealers: List[Dict]) -> List[Dict]:
        """Calculate distances for multiple dealers from a warehouse."""
        if not warehouse or not dealers:
            return []
        
        warehouse = self._normalize_city(warehouse)
        results = []
        
        for dealer in dealers:
            dealer_name = dealer.get('name', 'Unknown')
            dealer_city = dealer.get('city', '')
            
            if not dealer_city:
                results.append({
                    **dealer,
                    'distance_km': None,
                    'distance_error': 'No city specified'
                })
                continue
            
            dist = self.calculate_warehouse_distance(warehouse, dealer_city)
            
            result = {**dealer}
            if dist.get('success'):
                result['distance_km'] = dist.get('distance_km')
                result['distance_miles'] = dist.get('distance_miles')
                result['approx_driving_hours'] = dist.get('approx_driving_hours')
                result['approx_driving_minutes'] = dist.get('approx_driving_minutes')
                result['distance_type'] = dist.get('distance_type', 'unknown')
                result['source'] = dist.get('source', 'unknown')
            else:
                result['distance_km'] = None
                result['distance_error'] = dist.get('error', 'Unknown error')
            
            results.append(result)
        
        return results


# ==========================================================
# BLOCK 13: GET NEARBY DEALERS
# ==========================================================

    def get_nearby_dealers(self, warehouse: str, dealers: List[Dict], max_distance: float = 100) -> List[Dict]:
        """Get dealers within a certain distance from a warehouse."""
        results = self.calculate_dealer_distances(warehouse, dealers)
        nearby = [d for d in results if d.get('distance_km') and d['distance_km'] <= max_distance]
        return sorted(nearby, key=lambda x: x.get('distance_km', float('inf')))


# ==========================================================
# BLOCK 14: GET FARTHEST DEALERS
# ==========================================================

    def get_farthest_dealers(self, warehouse: str, dealers: List[Dict], limit: int = 10) -> List[Dict]:
        """Get farthest dealers from a warehouse."""
        results = self.calculate_dealer_distances(warehouse, dealers)
        valid = [d for d in results if d.get('distance_km')]
        return sorted(valid, key=lambda x: x.get('distance_km', 0), reverse=True)[:limit]


# ==========================================================
# BLOCK 15: FORMAT DISTANCE TEXT
# ==========================================================

    def format_distance_text(self, distance_info: Dict[str, Any]) -> str:
        """Format distance information for WhatsApp message."""
        if not distance_info or not distance_info.get('success'):
            return ""
        
        distance_km = distance_info.get('distance_km', 0)
        driving_hours = distance_info.get('approx_driving_hours', 0)
        driving_minutes = distance_info.get('approx_driving_minutes', 0)
        distance_type = distance_info.get('distance_type', 'unknown')
        
        lines = []
        lines.append(f"📍 *Distance*")
        lines.append(f"Warehouse → Dealer: {distance_km:.1f} km")
        
        if distance_type == "road":
            lines.append(f"   🚗 Road distance (accurate)")
        else:
            lines.append(f"   ✈️ Approximate (air distance)")
        
        if driving_hours:
            if driving_hours < 1:
                if driving_minutes:
                    lines.append(f"⏱️ Approx Driving: {driving_minutes} minutes")
                else:
                    lines.append(f"⏱️ Approx Driving: < 1 hour")
            else:
                hours = int(driving_hours)
                minutes = int((driving_hours - hours) * 60)
                if minutes > 0:
                    lines.append(f"⏱️ Approx Driving: {hours}h {minutes}m")
                else:
                    lines.append(f"⏱️ Approx Driving: {hours}h")
        
        return "\n".join(lines)


# ==========================================================
# BLOCK 16: COVERAGE FORMATTER
# ==========================================================

    def format_coverage_text(self, coverage: Dict[str, Any]) -> str:
        """Format warehouse coverage for WhatsApp message."""
        if not coverage or not coverage.get('success'):
            return "📍 *Warehouse Coverage*\n\nNo coverage data available"
        
        warehouse = coverage.get('warehouse', 'Unknown')
        total_cities = coverage.get('total_cities', 0)
        avg_distance = coverage.get('average_distance_km', 0)
        max_distance = coverage.get('max_distance_km', 0)
        min_distance = coverage.get('min_distance_km', 0)
        summary = coverage.get('summary', {})
        cities = coverage.get('cities', [])
        
        lines = []
        lines.append(f"🏭 *WAREHOUSE COVERAGE*")
        lines.append(f"Warehouse: {warehouse}")
        lines.append("")
        lines.append(f"📊 *Coverage Stats*")
        lines.append(f"Total Cities: {total_cities}")
        lines.append(f"Average Distance: {avg_distance:.1f} km")
        lines.append(f"Closest City: {min_distance:.1f} km")
        lines.append(f"Farthest City: {max_distance:.1f} km")
        lines.append("")
        
        if summary:
            lines.append("📌 *Summary*")
            lines.append(f"Closest: {summary.get('closest_city', 'N/A')} ({summary.get('closest_distance', 0):.1f} km)")
            lines.append(f"Farthest: {summary.get('farthest_city', 'N/A')} ({summary.get('farthest_distance', 0):.1f} km)")
            lines.append("")
        
        if cities:
            lines.append("📍 *Cities by Distance*")
            for city in cities[:10]:
                city_name = city.get('city', 'Unknown')
                dist = city.get('distance_km', 0)
                dn_count = city.get('dn_count', 0)
                lines.append(f"• {city_name}: {dist:.1f} km ({dn_count} DNs)")
        
        if len(cities) > 10:
            lines.append(f"... and {len(cities) - 10} more cities")
        
        return "\n".join(lines)


# ==========================================================
# BLOCK 17: SINGLETON INSTANCE
# ==========================================================

_distance_service = None

def get_distance_service() -> DistanceService:
    """Get the singleton DistanceService instance."""
    global _distance_service
    if _distance_service is None:
        _distance_service = DistanceService()
    return _distance_service


# ==========================================================
# BLOCK 18: TEST FUNCTION
# ==========================================================

def test_distance():
    """Test the distance service with PostgreSQL integration."""
    service = get_distance_service()
    
    print("=" * 70)
    print("🧪 TESTING DISTANCE SERVICE v4.0 (WITH POSTGRESQL)")
    print("=" * 70)
    
    # Test 1: Rawalpindi → Attock
    print("\n📏 Test 1: Rawalpindi → Attock")
    result = service.calculate_distance("Rawalpindi", "Attock")
    if result.get('success'):
        print(f"   ✅ Distance: {result['distance_km']} km")
        print(f"   ✅ Type: {result.get('distance_type', 'unknown')}")
        print(f"   ✅ Source: {result.get('source', 'unknown')}")
        print(f"   ✅ Driving: {result.get('approx_driving_hours', 0)} hours")
    else:
        print(f"   ❌ Failed: {result.get('error')}")
    
    # Test 2: PostgreSQL - Get Dealer Cities
    print("\n📏 Test 2: PostgreSQL - Get Dealer Cities")
    cities = service.get_dealer_cities()
    print(f"   ✅ Found {len(cities)} dealer cities")
    for city in cities[:5]:
        print(f"   • {city['city']}: {city.get('dealer_count', 0)} dealers, {city.get('dn_count', 0)} DNs")
    
    # Test 3: Warehouse Coverage
    print("\n📏 Test 3: Warehouse Coverage")
    coverage = service.get_warehouse_coverage("Lahore")
    if coverage.get('success'):
        print(f"   ✅ Warehouse: {coverage['warehouse']}")
        print(f"   ✅ Cities: {coverage['total_cities']}")
        print(f"   ✅ Avg Distance: {coverage['average_distance_km']:.1f} km")
    else:
        print(f"   ❌ Failed: {coverage.get('error')}")
    
    print("\n" + "=" * 70)
    print("✅ Test Complete")
    print("=" * 70)


# ==========================================================
# BLOCK 19: EXPORTS
# ==========================================================

__all__ = [
    'DistanceService',
    'get_distance_service',
    'test_distance'
]

# ==========================================================
# END OF FILE - v4.0 FULLY INTEGRATED
# ==========================================================
