# ============================================================
# FILE: app/services/geo_service.py
# VERSION: 16.3 - ENTERPRISE GEO-INTELLIGENCE MODULE
# ============================================================

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class GeoService:
    """Enterprise Geolocation Service providing robust coordinate mapping and distance estimation."""
    
    # Comprehensive built-in coordinate mapping for standard logistics hubs
    BUILTIN_COORDS = {
        "lahore": {"lat": 31.5497, "lng": 74.3436},
        "karachi": {"lat": 24.8607, "lng": 67.0011},
        "islamabad": {"lat": 33.6844, "lng": 73.0479},
        "rawalpindi": {"lat": 33.6007, "lng": 73.0679},
        "faisalabad": {"lat": 31.4504, "lng": 73.1350},
        "multan": {"lat": 30.1575, "lng": 71.5249},
        "peshawar": {"lat": 34.0151, "lng": 71.5249},
        "quetta": {"lat": 30.1798, "lng": 66.9750},
        "sialkot": {"lat": 32.4945, "lng": 74.5229},
        "gujranwala": {"lat": 32.1877, "lng": 74.1945},
        "hyderabad": {"lat": 25.3960, "lng": 68.3578},
        "bahawalpur": {"lat": 29.3956, "lng": 71.6836},
        "sargodha": {"lat": 32.0836, "lng": 72.6711}
    }

    @classmethod
    def get_city_coordinates(cls, city_name: Optional[str]) -> Dict[str, float]:
        """
        Safely retrieve geographic coordinates for a given city name.
        Returns fallback coordinates (Lahore) if the city is unknown or None.
        """
        default_coords = {"lat": 31.5497, "lng": 74.3436}
        
        if not city_name or not isinstance(city_name, str):
            return default_coords
            
        cleaned_city = city_name.strip().lower()
        
        if cleaned_city in cls.BUILTIN_COORDS:
            return cls.BUILTIN_COORDS[cleaned_city]
            
        logger.debug(f"City '{city_name}' not found in built-in mapping. Falling back to default coordinates.")
        return default_coords

    @classmethod
    def calculate_city_distance(cls, origin_city: str, destination_city: str) -> float:
        """
        Calculate estimated distance between two cities using built-in coordinates.
        """
        import math
        coords1 = cls.get_city_coordinates(origin_city)
        coords2 = cls.get_city_coordinates(destination_city)
        
        lat1, lon1 = coords1["lat"], coords1["lng"]
        lat2, lon2 = coords2["lat"], coords2["lng"]
        
        R = 6371.0  # Earth radius in kilometers
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        
        a = (math.sin(dlat / 2) ** 2 + 
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return round(R * c, 2)
