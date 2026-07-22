class GeoService:
    """Safe geolocation service with built-in coordinates and graceful fallbacks."""
    
    # Common city coordinate mapping to prevent external API/library crashes
    BUILTIN_COORDS = {
        "lahore": {"lat": 31.5497, "lng": 74.3436},
        "karachi": {"lat": 24.8607, "lng": 67.0011},
        "islamabad": {"lat": 33.6844, "lng": 73.0479},
        "rawalpindi": {"lat": 33.6007, "lng": 73.0679},
        "faisalabad": {"lat": 31.4504, "lng": 73.1350},
        "multan": {"lat": 30.1575, "lng": 71.5249},
        "peshawar": {"lat": 34.0151, "lng": 71.5249},
        "quetta": {"lat": 30.1798, "lng": 66.9750}
    }

    @classmethod
    def get_city_coordinates(cls, city_name: Optional[str]) -> Dict[str, float]:
        if not city_name:
            return {"lat": 31.5497, "lng": 74.3436} # Default to Lahore
            
        cleaned_city = str(city_name).strip().lower()
        
        # Check built-in dictionary first
        if cleaned_city in cls.BUILTIN_COORDS:
            return cls.BUILTIN_COORDS[cleaned_city]
            
        # Fallback default coordinate if city is unknown
        return {"lat": 31.5497, "lng": 74.3436}
