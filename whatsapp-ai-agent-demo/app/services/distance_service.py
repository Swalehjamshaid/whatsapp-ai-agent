# ==========================================================
# FILE: app/services/distance_service.py
# PURPOSE: Distance calculation using geopy + OpenRouteService
# VERSION: 3.0 - WITH ROAD DISTANCE
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

class DistanceService:
    """
    Distance calculation service with ROAD DISTANCE support.
    
    Features:
    - Road distance using OpenRouteService API (FREE)
    - Fallback to air distance if API fails
    - City name normalization
    - Batch processing
    - Caching for performance
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
            # OPENROUTESERVICE API - For road distance
            # ==========================================================
            # Get free API key from: https://openrouteservice.org/
            self.ors_api_key = os.getenv("OPENROUTE_API_KEY", "")
            self.ors_base_url = "https://api.openrouteservice.org/v2/directions/driving-car"
            
            # ==========================================================
            # CITY NAME MAPPING - Fixes spelling issues
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
            }
            
            # ==========================================================
            # FALLBACK COORDINATES - If geocoding fails
            # ==========================================================
            self.fallback_coords = {
                "gilgit": (35.9189, 74.3123),
                "rawalpindi": (33.5651, 73.0169),
                "islamabad": (33.6844, 73.0479),
                "lahore": (31.5204, 74.3587),
                "karachi": (24.8607, 67.0011),
                "attock": (33.8886, 72.6641),
                "wah cantt": (33.7700, 72.7500),
                "jand": (33.7800, 72.0200),
                "kamra cantt": (33.7500, 73.0000),
                "peshawar": (34.0151, 71.5249),
                "quetta": (30.1798, 66.9750),
                "multan": (30.1575, 71.5249),
                "faisalabad": (31.4504, 73.1350),
                "sialkot": (32.4945, 74.5229),
                "gujranwala": (32.1627, 74.1883),
            }
            
            self._initialized = True
            logger.info("✅ DistanceService initialized")
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
    
    def _normalize_city(self, city: str) -> str:
        """Normalize city names for better geocoding."""
        if not city:
            return city
        
        city_lower = city.lower().strip()
        normalized = self.city_mapping.get(city_lower, city)
        
        if normalized != city:
            logger.info(f"🔍 City name normalized: '{city}' → '{normalized}'")
        
        return normalized
    
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
    
    def get_road_distance(self, origin: str, destination: str) -> Optional[Dict[str, Any]]:
        """
        Get road distance using OpenRouteService API.
        
        Args:
            origin: Origin location
            destination: Destination location
        
        Returns:
            Dict with distance, duration, or None if fails
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
                    
                    distance_km = summary.get('distance', 0) / 1000  # meters to km
                    duration_min = summary.get('duration', 0) / 60   # seconds to minutes
                    
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
    
    def calculate_distance(self, origin: str, destination: str) -> Dict[str, Any]:
        """
        Calculate distance between two locations (ROAD DISTANCE PREFERRED).
        
        Args:
            origin: Origin location (e.g., "Rawalpindi")
            destination: Destination location (e.g., "Attock")
        
        Returns:
            Dict with distance information
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
            approx_driving = geodesic_dist * 1.3  # Rough estimate
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
    
    def get_warehouse_coverage(self, warehouse: str, cities: list) -> Dict[str, Any]:
        """Calculate distances from warehouse to multiple cities."""
        if not warehouse or not cities:
            return {"success": False, "error": "Warehouse and cities required"}
        
        warehouse = self._normalize_city(warehouse)
        
        distances = []
        total_distance = 0
        max_distance = 0
        min_distance = float('inf')
        
        for city in cities:
            if not city:
                continue
            city = self._normalize_city(city)
            dist = self.calculate_warehouse_distance(warehouse, city)
            if dist.get('success'):
                distance_km = dist.get('distance_km', 0)
                distances.append({
                    "city": city,
                    "distance_km": distance_km,
                    "driving_hours": dist.get('approx_driving_hours', 0),
                    "distance_type": dist.get('distance_type', 'unknown')
                })
                total_distance += distance_km
                max_distance = max(max_distance, distance_km)
                min_distance = min(min_distance, distance_km)
        
        return {
            "success": True,
            "warehouse": warehouse,
            "total_cities": len(distances),
            "total_distance_km": round(total_distance, 1),
            "average_distance_km": round(total_distance / len(distances), 1) if distances else 0,
            "max_distance_km": round(max_distance, 1),
            "min_distance_km": round(min_distance, 1) if min_distance != float('inf') else 0,
            "distances": distances
        }
    
    def calculate_distances_for_dealers(self, warehouse: str, dealers: List[Dict]) -> List[Dict]:
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
            else:
                result['distance_km'] = None
                result['distance_error'] = dist.get('error', 'Unknown error')
            
            results.append(result)
        
        return results
    
    def get_nearby_dealers(self, warehouse: str, dealers: List[Dict], max_distance: float = 100) -> List[Dict]:
        """Get dealers within a certain distance from a warehouse."""
        results = self.calculate_distances_for_dealers(warehouse, dealers)
        nearby = [d for d in results if d.get('distance_km') and d['distance_km'] <= max_distance]
        return sorted(nearby, key=lambda x: x.get('distance_km', float('inf')))
    
    def get_farthest_dealers(self, warehouse: str, dealers: List[Dict], limit: int = 10) -> List[Dict]:
        """Get farthest dealers from a warehouse."""
        results = self.calculate_distances_for_dealers(warehouse, dealers)
        valid = [d for d in results if d.get('distance_km')]
        return sorted(valid, key=lambda x: x.get('distance_km', 0), reverse=True)[:limit]
    
    def format_distance_text(self, distance_info: Dict[str, Any]) -> str:
        """Format distance information for WhatsApp message."""
        if not distance_info or not distance_info.get('success'):
            return ""
        
        distance_km = distance_info.get('distance_km', 0)
        driving_hours = distance_info.get('approx_driving_hours', 0)
        driving_minutes = distance_info.get('approx_driving_minutes', 0)
        distance_type = distance_info.get('distance_type', 'unknown')
        source = distance_info.get('source', '')
        
        lines = []
        lines.append(f"📍 *Distance*")
        lines.append(f"Warehouse → Dealer: {distance_km:.1f} km")
        
        # Show road vs air indicator
        if distance_type == "road":
            lines.append(f"   🚗 Road distance (accurate)")
        else:
            lines.append(f"   ✈️ Approximate (air distance)")
        
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
        
        return "\n".join(lines)


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_distance_service = None

def get_distance_service() -> DistanceService:
    """Get the singleton DistanceService instance."""
    global _distance_service
    if _distance_service is None:
        _distance_service = DistanceService()
    return _distance_service


# ==========================================================
# QUICK TEST FUNCTION
# ==========================================================

def test_distance():
    """Test the distance service."""
    service = get_distance_service()
    
    print("=" * 60)
    print("🧪 TESTING DISTANCE SERVICE (ROAD DISTANCE)")
    print("=" * 60)
    
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
    
    # Test 2: Rawalpindi → Gilgit
    print("\n📏 Test 2: Rawalpindi → Gilgit")
    result = service.calculate_distance("Rawalpindi", "Gilgit")
    if result.get('success'):
        print(f"   ✅ Distance: {result['distance_km']} km")
        print(f"   ✅ Type: {result.get('distance_type', 'unknown')}")
        print(f"   ✅ Source: {result.get('source', 'unknown')}")
        print(f"   ✅ Driving: {result.get('approx_driving_hours', 0)} hours")
    else:
        print(f"   ❌ Failed: {result.get('error')}")
    
    # Test 3: Rawalpindi → Lahore
    print("\n📏 Test 3: Rawalpindi → Lahore")
    result = service.calculate_distance("Rawalpindi", "Lahore")
    if result.get('success'):
        print(f"   ✅ Distance: {result['distance_km']} km")
        print(f"   ✅ Type: {result.get('distance_type', 'unknown')}")
        print(f"   ✅ Source: {result.get('source', 'unknown')}")
        print(f"   ✅ Driving: {result.get('approx_driving_hours', 0)} hours")
    else:
        print(f"   ❌ Failed: {result.get('error')}")
    
    print("\n" + "=" * 60)
    print("✅ Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    test_distance()


# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'DistanceService',
    'get_distance_service',
    'test_distance'
]

# ==========================================================
# END OF FILE
# ==========================================================
