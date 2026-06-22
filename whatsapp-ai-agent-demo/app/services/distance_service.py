# ==========================================================
# FILE: app/services/distance_service.py
# PURPOSE: Distance calculation using geopy - Standalone
# ==========================================================

from geopy.geocoders import Nominatim
from geopy.distance import geodesic, great_circle
from geopy.extra.rate_limiter import RateLimiter
from typing import Optional, Tuple, Dict, Any
from loguru import logger
import time
import os

class DistanceService:
    """
    Distance calculation service using geopy.
    Standalone - no changes needed to other files.
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
            self._initialized = True
            logger.info("✅ DistanceService initialized")
        except Exception as e:
            logger.error(f"❌ DistanceService initialization failed: {e}")
            self._initialized = False
    
    def get_coordinates(self, location: str) -> Optional[Tuple[float, float]]:
        """
        Get latitude and longitude for a location.
        
        Args:
            location: Location name (e.g., "Rawalpindi, Pakistan")
        
        Returns:
            Tuple of (latitude, longitude) or None
        """
        if not location:
            return None
        
        # Check cache first
        cache_key = location.lower().strip()
        if cache_key in self.cache:
            logger.debug(f"📍 Cache hit for: {location}")
            return self.cache[cache_key]
        
        try:
            logger.info(f"🔍 Geocoding: {location}")
            
            # Add country context for better results
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
    
    def calculate_distance(self, origin: str, destination: str) -> Dict[str, Any]:
        """
        Calculate distance between two locations.
        
        Args:
            origin: Origin location (e.g., "Rawalpindi")
            destination: Destination location (e.g., "Attock")
        
        Returns:
            Dict with distance information
        """
        # Check cache
        cache_key = f"{origin.lower().strip()}|{destination.lower().strip()}"
        if cache_key in self.cache:
            logger.debug(f"📏 Cache hit for: {origin} → {destination}")
            result = self.cache[cache_key]
            result['from_cache'] = True
            return result
        
        try:
            logger.info(f"📏 Calculating distance: {origin} → {destination}")
            
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
            
            # Calculate multiple distance types
            geodesic_dist = geodesic(origin_coords, dest_coords).kilometers
            great_circle_dist = great_circle(origin_coords, dest_coords).kilometers
            
            # Rough driving estimate (1.3x straight-line for roads)
            approx_driving = geodesic_dist * 1.3
            avg_speed = 50  # km/h average driving speed
            
            result = {
                "success": True,
                "origin": origin,
                "destination": destination,
                "origin_coords": {"lat": origin_coords[0], "lng": origin_coords[1]},
                "destination_coords": {"lat": dest_coords[0], "lng": dest_coords[1]},
                "distance_km": round(geodesic_dist, 1),
                "distance_miles": round(geodesic_dist * 0.621371, 1),
                "straight_line_km": round(great_circle_dist, 1),
                "approx_driving_km": round(approx_driving, 1),
                "approx_driving_hours": round(approx_driving / avg_speed, 1),
                "approx_driving_minutes": int((approx_driving / avg_speed) * 60),
                "from_cache": False
            }
            
            self.cache[cache_key] = result
            logger.info(f"✅ Distance: {origin} → {destination} = {result['distance_km']} km")
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
        return self.calculate_distance(warehouse, dealer_city)
    
    def get_warehouse_coverage(self, warehouse: str, cities: list) -> Dict[str, Any]:
        """
        Calculate distances from warehouse to multiple cities.
        
        Args:
            warehouse: Warehouse location
            cities: List of city names
        
        Returns:
            Dict with coverage information
        """
        if not warehouse or not cities:
            return {"success": False, "error": "Warehouse and cities required"}
        
        distances = []
        total_distance = 0
        max_distance = 0
        min_distance = float('inf')
        
        for city in cities:
            if not city:
                continue
            dist = self.calculate_warehouse_distance(warehouse, city)
            if dist.get('success'):
                distance_km = dist.get('distance_km', 0)
                distances.append({
                    "city": city,
                    "distance_km": distance_km,
                    "driving_hours": dist.get('approx_driving_hours', 0)
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
    
    def format_distance_text(self, distance_info: Dict[str, Any]) -> str:
        """
        Format distance information for WhatsApp message.
        
        Args:
            distance_info: Result from calculate_distance()
        
        Returns:
            Formatted text for display
        """
        if not distance_info or not distance_info.get('success'):
            return ""
        
        distance_km = distance_info.get('distance_km', 0)
        driving_hours = distance_info.get('approx_driving_hours', 0)
        driving_minutes = distance_info.get('approx_driving_minutes', 0)
        
        lines = []
        lines.append(f"📍 *Distance*")
        lines.append(f"Warehouse → Dealer: {distance_km:.1f} km")
        
        if driving_hours:
            if driving_hours < 1:
                lines.append(f"⏱️ Approx Driving: {driving_minutes} minutes")
            else:
                lines.append(f"⏱️ Approx Driving: {driving_hours:.1f} hours")
        
        return "\n".join(lines)

# ==========================================================
# SINGLETON INSTANCE - Easy import
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
    print("🧪 TESTING DISTANCE SERVICE")
    print("=" * 60)
    
    # Test 1: Rawalpindi → Attock
    print("\n📏 Test 1: Rawalpindi → Attock")
    result = service.calculate_distance("Rawalpindi", "Attock")
    if result.get('success'):
        print(f"   Distance: {result['distance_km']} km")
        print(f"   Driving: {result['approx_driving_hours']} hours")
    else:
        print(f"   ❌ Failed: {result.get('error')}")
    
    # Test 2: Rawalpindi → Wah Cantt
    print("\n📏 Test 2: Rawalpindi → Wah Cantt")
    result = service.calculate_distance("Rawalpindi", "Wah Cantt")
    if result.get('success'):
        print(f"   Distance: {result['distance_km']} km")
        print(f"   Driving: {result['approx_driving_hours']} hours")
    else:
        print(f"   ❌ Failed: {result.get('error')}")
    
    # Test 3: Multiple cities
    print("\n📏 Test 3: Warehouse Coverage")
    coverage = service.get_warehouse_coverage(
        "Rawalpindi",
        ["Attock", "Wah Cantt", "Islamabad", "Lahore"]
    )
    if coverage.get('success'):
        print(f"   Total Cities: {coverage['total_cities']}")
        print(f"   Average Distance: {coverage['average_distance_km']} km")
        print(f"   Farthest: {coverage['max_distance_km']} km")
        print(f"   Closest: {coverage['min_distance_km']} km")
    else:
        print(f"   ❌ Failed: {coverage.get('error')}")
    
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
