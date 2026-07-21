# ============================================================
# FILE: app/services/geo_service.py
# VERSION: 1.0 - ENTERPRISE GEOSPATIAL INTELLIGENCE PLATFORM
# ============================================================

import logging
import os
from typing import Optional, Tuple
from geopy.distance import geodesic
import openrouteservice
from openrouteservice import exceptions

logger = logging.getLogger(__name__)

class GeoService:
    def __init__(self):
        self.ORS_API_KEY = os.getenv("ORS_API_KEY", "")
        self.client = None
        if self.ORS_API_KEY:
            try:
                self.client = openrouteservice.Client(key=self.ORS_API_KEY)
                logger.info("🗺️ OpenRouteService client initialized successfully")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize OpenRouteService client: {e}")
        else:
            logger.info("ℹ️ ORS_API_KEY not found. Falling back to Geopy geodesic distance calculations.")

    def calculate_distance_km(
        self, 
        origin_coords: Tuple[float, float], 
        destination_coords: Tuple[float, float], 
        mode: str = 'driving-car'
    ) -> float:
        """
        Calculates road distance (KM) using OpenRouteService if available,
        otherwise falls back to Geopy straight-line geodesic distance.
        origin_coords / destination_coords format: (latitude, longitude)
        """
        lat1, lon1 = origin_coords
        lat2, lon2 = destination_coords

        # Try OpenRouteService road routing first (ORS expects [lon, lat])
        if self.client:
            try:
                coordinates = [[lon1, lat1], [lon2, lat2]]
                routes = self.client.directions(
                    coordinates=coordinates,
                    profile=mode,
                    format='json',
                    units='km'
                )
                if routes and 'routes' in routes and len(routes['routes']) > 0:
                    distance = routes['routes'][0]['summary']['distance']
                    return round(distance, 2)
            except Exception as e:
                logger.warning(f"⚠️ ORS routing failed, falling back to Geopy: {e}")

        # Fallback to Geopy (Great-circle / geodesic distance)
        try:
            dist = geodesic((lat1, lon1), (lat2, lon2)).kilometers
            # Apply a standard road-tortuosity factor multiplier (~1.25x for road vs air)
            road_adjusted_dist = dist * 1.25
            return round(road_adjusted_dist, 2)
        except Exception as e:
            logger.error(f"❌ Geopy distance calculation failed: {e}")
            return 0.0

    @staticmethod
    def get_standard_pgi_days(distance_km: float) -> int:
        """Determines Standard PGI target days based on distance tier."""
        if distance_km <= 100:
            return 1
        elif distance_km <= 250:
            return 1
        elif distance_km <= 450:
            return 2
        elif distance_km <= 700:
            return 3
        elif distance_km <= 900:
            return 4
        else:
            return 5

    @staticmethod
    def get_standard_pod_days(distance_km: float) -> int:
        """Determines Standard POD target days based on distance tier."""
        if distance_km <= 100:
            return 1
        elif distance_km <= 250:
            return 2
        elif distance_km <= 450:
            return 3
        elif distance_km <= 700:
            return 4
        elif distance_km <= 900:
            return 5
        else:
            return 6  # Covers extreme outstations and special regional clauses
