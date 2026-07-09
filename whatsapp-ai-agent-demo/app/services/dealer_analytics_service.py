#!/usr/bin/env python3
# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 12.11 - ROAD DISTANCE WITH CACHING
# ============================================================

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional, Dict, List, Tuple
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

ORS_API_KEY = os.getenv("ORS_API_KEY", "")
ORS_PROFILE = os.getenv("ORS_PROFILE", "driving-car")
VERSION = "12.11"

# Try to import geocoding libraries
try:
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    GEOCODE_AVAILABLE = True
    logger.info("✅ Geopy imported successfully")
except ImportError:
    GEOCODE_AVAILABLE = False
    logger.warning("⚠️ Geopy not available")

try:
    import openrouteservice
    ORS_AVAILABLE = True
    logger.info("✅ OpenRouteService imported successfully")
except ImportError:
    ORS_AVAILABLE = False
    logger.warning("⚠️ OpenRouteService not available")

# Try to import caching libraries
try:
    from cachetools import TTLCache
    CACHETOOLS_AVAILABLE = True
    logger.info("✅ Cachetools imported successfully")
except ImportError:
    CACHETOOLS_AVAILABLE = False
    logger.warning("⚠️ Cachetools not available")

try:
    import redis
    REDIS_AVAILABLE = True
    logger.info("✅ Redis imported successfully")
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("⚠️ Redis not available")

# ============================================================
# CACHE CONFIGURATION
# ============================================================

# Redis connection
_redis_client = None
if REDIS_AVAILABLE:
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = redis.from_url(redis_url, decode_responses=True)
        _redis_client.ping()
        logger.info("✅ Redis connected successfully")
    except Exception as e:
        logger.warning(f"⚠️ Redis connection failed: {e}")
        _redis_client = None

# In-memory caches with TTL
if CACHETOOLS_AVAILABLE:
    # Cache for geocoding results (7 days TTL)
    _geocode_cache = TTLCache(maxsize=1000, ttl=604800)
    # Cache for road distance calculations (30 days TTL)
    _distance_cache = TTLCache(maxsize=1000, ttl=2592000)
else:
    # Fallback to simple dict caches
    _geocode_cache = {}
    _distance_cache = {}

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        return str(value).strip() or default
    except (TypeError, ValueError):
        return default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 1) if bottom else 0.0

def _format_currency(amount: float) -> str:
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: int) -> str:
    return f"{num:,}"

def _get_redis_cache(key: str) -> Optional[str]:
    """Get from Redis cache"""
    if _redis_client:
        try:
            return _redis_client.get(key)
        except Exception as e:
            logger.warning(f"Redis get failed: {e}")
    return None

def _set_redis_cache(key: str, value: str, ttl: int = 2592000) -> None:
    """Set Redis cache with TTL (default 30 days for distances)"""
    if _redis_client:
        try:
            _redis_client.setex(key, ttl, value)
        except Exception as e:
            logger.warning(f"Redis set failed: {e}")

def _geocode_city(city: str) -> Optional[Tuple[float, float]]:
    """Geocode a city name to get coordinates using geopy with Redis caching"""
    if not city:
        return None
    
    city_clean = city.strip()
    cache_key = city_clean.lower()
    
    # Check Redis cache first
    redis_key = f"geocode:{cache_key}"
    redis_result = _get_redis_cache(redis_key)
    if redis_result:
        try:
            lat, lon = redis_result.split(',')
            coords = (float(lat), float(lon))
            logger.info(f"✅ Redis cache hit for '{city_clean}'")
            return coords
        except Exception as e:
            logger.warning(f"Redis cache parse failed: {e}")
    
    # Check in-memory cache
    if cache_key in _geocode_cache:
        logger.info(f"✅ Memory cache hit for '{city_clean}'")
        return _geocode_cache[cache_key]
    
    # Try geopy first
    if GEOCODE_AVAILABLE:
        try:
            geolocator = Nominatim(user_agent="dealer_intelligence")
            geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
            
            # Try with "Pakistan" to improve accuracy
            location = geocode(f"{city_clean}, Pakistan")
            if location:
                coords = (location.latitude, location.longitude)
                # Store in caches
                _geocode_cache[cache_key] = coords
                _set_redis_cache(redis_key, f"{coords[0]},{coords[1]}", 604800)
                logger.info(f"✅ Geocoded '{city_clean}' → {coords}")
                return coords
        except Exception as e:
            logger.warning(f"Geopy geocoding failed for '{city_clean}': {e}")
    
    # Try OpenRouteService geocoding
    if ORS_AVAILABLE and ORS_API_KEY:
        try:
            client = openrouteservice.Client(key=ORS_API_KEY)
            response = client.pelias_search(text=f"{city_clean}, Pakistan")
            if response and 'features' in response and response['features']:
                coords = response['features'][0]['geometry']['coordinates']
                # ORS returns [lng, lat], we need [lat, lng]
                coords_tuple = (coords[1], coords[0])
                # Store in caches
                _geocode_cache[cache_key] = coords_tuple
                _set_redis_cache(redis_key, f"{coords_tuple[0]},{coords_tuple[1]}", 604800)
                logger.info(f"✅ ORS geocoded '{city_clean}' → {coords_tuple}")
                return coords_tuple
        except Exception as e:
            logger.warning(f"ORS geocoding failed for '{city_clean}': {e}")
    
    # Fallback: Try to find in hardcoded city list
    fallback_coords = _get_fallback_coordinates(city_clean)
    if fallback_coords:
        _geocode_cache[cache_key] = fallback_coords
        _set_redis_cache(redis_key, f"{fallback_coords[0]},{fallback_coords[1]}", 604800)
        return fallback_coords
    
    logger.warning(f"❌ Could not geocode '{city_clean}'")
    return None

def _get_fallback_coordinates(city: str) -> Optional[Tuple[float, float]]:
    """Get fallback coordinates from hardcoded list"""
    city_lower = city.lower()
    
    # Extended city coordinates for Pakistan
    fallback_coords = {
        # Major cities
        "karachi": (24.8607, 67.0011),
        "lahore": (31.5204, 74.3587),
        "rawalpindi": (33.5651, 73.0169),
        "islamabad": (33.6844, 73.0479),
        "multan": (30.1575, 71.5249),
        "peshawar": (34.0151, 71.5249),
        "quetta": (30.1798, 66.9750),
        "hyderabad": (25.3960, 68.3578),
        "faisalabad": (31.4504, 73.1350),
        "sialkot": (32.4945, 74.5229),
        "gujranwala": (32.1617, 74.1883),
        "hafizabad": (32.0667, 73.6833),
        "ajk": (34.3700, 73.4711),
        "azad kashmir": (34.3700, 73.4711),
        "muzaffarabad": (34.3700, 73.4711),
        "bagh": (33.9833, 73.7667),
        "sahiwal": (30.6667, 73.1000),
        "okara": (30.8167, 73.4500),
        "sheikhupura": (31.7167, 73.9833),
        "gujrat": (32.5667, 74.0833),
        "jhelum": (32.9333, 73.7333),
        "sargodha": (32.0833, 72.6667),
        "bahawalpur": (29.3956, 71.6836),
        "sukkur": (27.7060, 68.8530),
        "dg khan": (30.0430, 70.6402),
        "abbottabad": (34.1490, 73.2210),
        "gwadar": (25.1260, 62.3250),
        "gilgit": (35.9208, 74.3144),
        "narowal": (32.1167, 74.8833),
        "chakwal": (32.9333, 72.8667),
        "mandi bahauddin": (32.5833, 73.4833),
        "jehlum": (32.9333, 73.7333),
        "kasur": (31.1167, 74.4500),
    }
    
    return fallback_coords.get(city_lower)

def _get_road_distance_between_cities(city1: str, city2: str) -> Tuple[float, str]:
    """
    Get ROAD distance between two cities using OpenRouteService
    This uses actual road networks, not straight-line distance
    """
    
    if not city1 or not city2:
        return (0, "Unknown")
    
    cache_key = f"{city1.lower()}|{city2.lower()}"
    
    # Check Redis cache first
    redis_key = f"road_distance:{cache_key}"
    redis_result = _get_redis_cache(redis_key)
    if redis_result:
        try:
            distance_str, time_str = redis_result.split('||')
            result = (float(distance_str), time_str)
            logger.info(f"✅ Redis cache hit for road distance: {city1} → {city2}")
            return result
        except Exception as e:
            logger.warning(f"Redis cache parse failed: {e}")
    
    # Check in-memory cache
    if cache_key in _distance_cache:
        logger.info(f"✅ Memory cache hit for road distance: {city1} → {city2}")
        return _distance_cache[cache_key]
    
    # Get coordinates for both cities
    coords1 = _geocode_city(city1)
    coords2 = _geocode_city(city2)
    
    if not coords1 or not coords2:
        logger.warning(f"Could not get coordinates for {city1} or {city2}")
        return (0, "Not Available")
    
    # Try OpenRouteService for ROAD distance (this is the primary method)
    if ORS_AVAILABLE and ORS_API_KEY:
        try:
            client = openrouteservice.Client(key=ORS_API_KEY)
            
            # ORS expects [lng, lat]
            coordinates = [
                [coords1[1], coords1[0]],
                [coords2[1], coords2[0]]
            ]
            
            logger.info(f"Calculating ROAD distance from {city1} to {city2} using ORS...")
            
            routes = client.directions(
                coordinates=coordinates,
                profile=ORS_PROFILE,  # 'driving-car' for road routes
                format='json',
                validate=False,
                alternatives=False,
                geometry=False  # Don't return geometry to save bandwidth
            )
            
            if routes and 'routes' in routes and routes['routes']:
                summary = routes['routes'][0].get('summary', {})
                distance_km = summary.get('distance', 0) / 1000
                duration_sec = summary.get('duration', 0)
                
                # Format duration
                hours = int(duration_sec // 3600)
                minutes = int((duration_sec % 3600) // 60)
                
                if hours > 0 and minutes > 0:
                    time_str = f"{hours}h {minutes}m"
                elif hours > 0:
                    time_str = f"{hours}h"
                else:
                    time_str = f"{minutes} mins"
                
                result = (round(distance_km, 1), time_str)
                
                # Store in caches
                _distance_cache[cache_key] = result
                _set_redis_cache(redis_key, f"{distance_km}||{time_str}", 2592000)
                
                logger.info(f"✅ ROAD distance (ORS): {city1} → {city2}: {distance_km:.1f} KM, {time_str}")
                return result
                
        except Exception as e:
            logger.error(f"ORS road distance calculation failed: {e}")
    
    # Fallback: If ORS fails, use Haversine as last resort
    logger.warning(f"ORS failed, using Haversine fallback for {city1} → {city2}")
    distance_km = _haversine_distance(coords1, coords2)
    
    # Estimate travel time (assuming avg speed 50 km/h for highways)
    hours = distance_km / 50
    if hours < 1:
        minutes = int(hours * 60)
        time_str = f"{minutes} mins"
    else:
        h = int(hours)
        m = int((hours - h) * 60)
        time_str = f"{h}h {m}m" if m > 0 else f"{h}h"
    
    result = (round(distance_km, 1), time_str)
    _distance_cache[cache_key] = result
    logger.info(f"⚠️ Fallback (Haversine) distance: {city1} → {city2}: {distance_km:.1f} KM, {time_str}")
    return result

def _haversine_distance(coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
    """Calculate straight-line distance using Haversine formula (fallback only)"""
    from math import radians, sin, cos, sqrt, atan2
    
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    
    R = 6371  # Earth's radius in km
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    return R * c

def _get_dealer_rating(delivery_rate: float, revenue: float, pending_dn: int = 0, dn_count: int = 0) -> str:
    """Calculate dealer rating based on performance"""
    score = 0
    
    # Delivery performance (40% weight)
    if delivery_rate >= 95:
        score += 40
    elif delivery_rate >= 85:
        score += 30
    elif delivery_rate >= 70:
        score += 20
    else:
        score += 10
    
    # Revenue performance (30% weight)
    if revenue >= 100_000_000:
        score += 30
    elif revenue >= 50_000_000:
        score += 22
    elif revenue >= 10_000_000:
        score += 15
    elif revenue >= 1_000_000:
        score += 10
    else:
        score += 5
    
    # Volume performance (20% weight based on DN count)
    if dn_count > 100:
        score += 20
    elif dn_count > 50:
        score += 15
    elif dn_count > 20:
        score += 10
    elif dn_count > 10:
        score += 8
    else:
        score += 5
    
    # Pending DNs penalty (10% weight)
    if pending_dn == 0:
        score += 10
    elif pending_dn <= 2:
        score += 8
    elif pending_dn <= 5:
        score += 5
    elif pending_dn <= 10:
        score += 3
    else:
        score += 0
    
    if score >= 85:
        return "A+"
    elif score >= 75:
        return "A"
    elif score >= 65:
        return "B+"
    elif score >= 55:
        return "B"
    elif score >= 45:
        return "C+"
    else:
        return "C"

# ============================================================
# DEALER REPOSITORY
# ============================================================

class DealerRepository:
    def __init__(self, session: Session):
        self.session = session
    
    def get_dealer_by_name(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        """Get dealer data using direct PostgreSQL connection."""
        from app.database import engine
        
        if not dealer_identifier:
            logger.warning("[Repository] Empty dealer identifier provided")
            return None
        
        dealer_clean = dealer_identifier.strip()
        dealer_normalized = " ".join(dealer_clean.lower().split())
        
        logger.info(f"[Repository] Searching for: '{dealer_normalized}'")
        
        try:
            with engine.connect() as conn:
                # Query to get dealer data
                result = conn.execute(
                    text("""
                        SELECT 
                            TRIM(customer_name) as customer_name,
                            TRIM(dealer_code) as dealer_code,
                            TRIM(customer_code) as customer_code,
                            TRIM(MAX(ship_to_city)) as ship_to_city,
                            TRIM(MAX(warehouse)) as warehouse,
                            TRIM(MAX(sales_office)) as sales_office,
                            TRIM(MAX(sales_manager)) as sales_manager,
                            TRIM(MAX(division)) as division,
                            COUNT(DISTINCT dn_no) as dn_count,
                            SUM(dn_qty) as total_units,
                            SUM(dn_amount) as total_revenue,
                            MIN(dn_create_date) as first_sale,
                            MAX(dn_create_date) as last_sale,
                            AVG(dn_amount) as avg_dn_value,
                            COUNT(DISTINCT CASE WHEN pod_date IS NULL THEN dn_no END) as pending_dn,
                            COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) as pgi_pending_dn,
                            COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) as pod_pending_dn,
                            COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed,
                            COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                            AVG(CASE WHEN good_issue_date IS NOT NULL THEN good_issue_date - dn_create_date END) as avg_delivery_days,
                            AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN pod_date - good_issue_date END) as avg_pod_days
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(customer_name)) = LOWER(TRIM(:name))
                        GROUP BY customer_name, dealer_code, customer_code
                    """),
                    {"name": dealer_normalized}
                ).first()
                
                if result:
                    logger.info(f"[Repository] ✅ Found dealer: {result[0]}")
                    return self._build_dealer_data(result)
                
                logger.warning(f"[Repository] ❌ No data found for: '{dealer_identifier}'")
                return None
                
        except Exception as e:
            logger.exception(f"[Repository] ❌ Failed to get dealer: {dealer_identifier}")
            return None
    
    def _build_dealer_data(self, row) -> Dict[str, Any]:
        """Build dealer data dictionary from query result"""
        dn_count = int(row[8] or 0)
        total_revenue = float(row[10] or 0.0)
        total_units = int(row[9] or 0)
        pending_dn = int(row[14] or 0)
        
        data = {
            'customer_name': _text(row[0]),
            'dealer_code': _text(row[1]),
            'customer_code': _text(row[2]),
            'city': _text(row[3]),
            'warehouse': _text(row[4]),
            'sales_office': _text(row[5]),
            'sales_manager': _text(row[6]),
            'division': _text(row[7]),
            'dn_count': dn_count,
            'total_units': total_units,
            'total_revenue': total_revenue,
            'first_sale': _text(row[11]),
            'last_sale': _text(row[12]),
            'avg_dn_value': float(row[13] or 0.0),
            'pending_dn': pending_dn,
            'pgi_pending_dn': int(row[15] or 0),
            'pod_pending_dn': int(row[16] or 0),
            'pod_completed': int(row[17] or 0),
            'pgi_completed': int(row[18] or 0),
            'avg_delivery_days': float(row[19] or 0.0),
            'avg_pod_days': float(row[20] or 0.0),
        }
        
        # Calculate rates
        data['delivery_rate'] = _percent(data.get('pod_completed', 0), dn_count)
        data['pod_achievement'] = _percent(data.get('pod_completed', 0), dn_count)
        data['pgi_achievement'] = _percent(data.get('pgi_completed', 0), dn_count)
        data['pending_pct'] = _percent(data.get('pending_dn', 0), dn_count)
        
        # Get ROAD distance using improved geocoding
        city = data.get('city', '')
        warehouse = data.get('warehouse', '')
        
        logger.info(f"[Repository] Calculating ROAD distance: Warehouse='{warehouse}', City='{city}'")
        
        if city and warehouse and city != 'Unknown' and warehouse != 'Unknown':
            try:
                # Use ROAD distance calculation
                distance_km, time_str = _get_road_distance_between_cities(warehouse, city)
                data['distance_km'] = distance_km
                data['distance_time'] = time_str
                logger.info(f"[Repository] ROAD distance calculated: {distance_km} KM, {time_str}")
            except Exception as e:
                logger.error(f"[Repository] Error calculating road distance: {e}")
                data['distance_km'] = None
                data['distance_time'] = "Not Available"
        else:
            logger.warning(f"[Repository] Cannot calculate road distance: city='{city}', warehouse='{warehouse}'")
            data['distance_km'] = None
            data['distance_time'] = "Not Available"
        
        # Get rating with more factors
        data['rating'] = _get_dealer_rating(
            data['delivery_rate'], 
            total_revenue, 
            pending_dn,
            dn_count
        )
        
        return data

# ============================================================
# MAIN SERVICE
# ============================================================

class DealerAnalyticsService:
    def __init__(self) -> None:
        self._version = VERSION
        logger.info(f"✅ DealerAnalyticsService v{self._version} initialized")
        logger.info(f"   Geopy: {'✅' if GEOCODE_AVAILABLE else '❌'}")
        logger.info(f"   OpenRouteService: {'✅' if ORS_AVAILABLE and ORS_API_KEY else '❌'}")
        logger.info(f"   Redis: {'✅' if _redis_client else '❌'}")
        logger.info(f"   Cachetools: {'✅' if CACHETOOLS_AVAILABLE else '❌'}")
        
        if ORS_AVAILABLE and ORS_API_KEY:
            logger.info("   Using OpenRouteService for ROAD distance calculations")
        elif GEOCODE_AVAILABLE:
            logger.info("   Using Geopy for geocoding with fallback")
        else:
            logger.info("   Using fallback distance calculation")
    
    def handle_message(self, message: str, sender: str) -> str:
        """Main entry point - searches for dealer and returns dashboard"""
        try:
            message_clean = message.strip()
            
            # Check if it's a numeric command (1-9 or 99)
            if message_clean in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '99']:
                logger.info("[Service] Numeric input detected, showing help")
                return self._get_help_message()
            
            # Check if it's a greeting or empty
            if not message_clean or message_clean.lower() in ['hi', 'hello', 'hey', 'start']:
                return self._get_welcome_message()
            
            logger.info("[Service] Searching for: '%s' from %s", message_clean, sender)
            
            # Search for the dealer
            result = self._search_dealer(message_clean)
            return result
            
        except Exception as e:
            logger.exception("[Service] Error in handle_message")
            return f"⚠️ Error: {str(e)}\n\nPlease try again with a different dealer name."
    
    def _get_welcome_message(self) -> str:
        """Get welcome message"""
        ors_status = "✅ Active" if ORS_AVAILABLE and ORS_API_KEY else "⚠️ Fallback Mode"
        geopy_status = "✅ Active" if GEOCODE_AVAILABLE else "❌"
        redis_status = "✅ Connected" if _redis_client else "❌ Not Connected"
        
        return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏢 HAIER DEALER INTELLIGENCE CENTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Welcome to the Dealer Intelligence Platform!

🔍 **How to use:**
• Type any dealer name to get their dashboard
• Examples:
  - Arshad Electronics-Khi
  - Mega Digital
  - Japan Electronics

📊 **What you'll see:**
• Dealer profile and location
• Sales performance metrics
• Delivery statistics
• AI-powered insights
• ROAD distance from warehouse

🗺️ **Distance Services:**
• OpenRouteService (Road): {ors_status}
• Geopy (Geocoding): {geopy_status}
• Redis Cache: {redis_status}

💡 **Pro tip:** 
Type partial names and we'll suggest matches!
Type **99** for quick help anytime!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a dealer name to get started!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
    def _get_help_message(self) -> str:
        """Get help message for numeric commands"""
        return """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 QUICK HELP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This is a dealer search system.

🔍 **To search:**
Simply type the dealer name.

📊 **Examples:**
• Arshad Electronics-Khi
• Mega Digital
• Japan Electronics

🔄 **Tips:**
• You can type partial names
• We'll show suggestions if no exact match
• All data is real-time from the database
• Type **99** for this help menu anytime

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a dealer name to get started!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
    def _search_dealer(self, dealer_name: str) -> str:
        """Search for dealer and return dashboard or suggestions"""
        
        # Try to find the dealer
        dealer = self._resolve_dealer_name(dealer_name)
        if dealer:
            return self._show_dashboard(dealer)
        
        # Get suggestions
        suggestions = self._get_suggestions(dealer_name)
        if suggestions:
            return self._format_suggestions(dealer_name, suggestions)
        
        # No results
        return f"""🔍 No dealer found matching '{dealer_name}'

💡 Suggestions:
• Try the full dealer name
• Try a partial name
• Check for spelling errors

Examples:
• Arshad Electronics-Khi
• Mega Digital
• Japan Electronics

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a dealer name to search again
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
    def _resolve_dealer_name(self, name: str) -> Optional[str]:
        """Resolve dealer name using direct database connection."""
        from app.database import engine
        
        if not name or not name.strip():
            return None
        
        name_normalized = self._normalize_name(name)
        logger.info("[Service] Searching for: '%s'", name_normalized)
        
        try:
            with engine.connect() as conn:
                # Exact match
                result = conn.execute(
                    text("""
                        SELECT TRIM(customer_name) as customer_name
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(customer_name)) = LOWER(TRIM(:name))
                        LIMIT 1
                    """),
                    {"name": name_normalized}
                ).first()
                
                if result:
                    logger.info("[Service] ✅ Found: '%s'", result[0])
                    return result[0]
                
                # ILIKE match
                result = conn.execute(
                    text("""
                        SELECT TRIM(customer_name) as customer_name
                        FROM delivery_reports 
                        WHERE TRIM(customer_name) ILIKE TRIM(:name)
                        LIMIT 1
                    """),
                    {"name": f"%{name}%"}
                ).first()
                
                if result:
                    logger.info("[Service] ✅ Found (ILIKE): '%s'", result[0])
                    return result[0]
                
                logger.info("[Service] ❌ No match found for: '%s'", name_normalized)
                
        except Exception as e:
            logger.exception("[Service] Error resolving dealer name: %s", name_normalized)
        
        return None
    
    def _get_suggestions(self, query: str, limit: int = 5) -> List[str]:
        """Get dealer name suggestions based on query."""
        if not query:
            return []
        
        query_normalized = self._normalize_name(query)
        
        try:
            with engine.connect() as conn:
                results = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(customer_name) as customer_name
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(customer_name)) LIKE LOWER(TRIM(:pattern))
                        ORDER BY customer_name
                        LIMIT :limit
                    """),
                    {"pattern": f"%{query_normalized}%", "limit": limit}
                ).fetchall()
                
                suggestions = [r[0] for r in results if r[0]]
                logger.info("[Service] Found %d suggestions for: '%s'", len(suggestions), query)
                return suggestions
                
        except Exception as e:
            logger.exception("[Service] Error getting suggestions for: %s", query)
            return []
    
    def _show_dashboard(self, dealer_name: str) -> str:
        """Show dealer dashboard"""
        logger.info("[Service] Dashboard for: '%s'", dealer_name)
        
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                data = repo.get_dealer_by_name(dealer_name)
                
                if data:
                    logger.info("[Service] ✅ Dashboard data found for: '%s'", dealer_name)
                    return self._render_dashboard(data)
                else:
                    logger.warning("[Service] ❌ No data for: '%s'", dealer_name)
                    return f"⚠️ No data found for: {dealer_name}"
                    
        except Exception as e:
            logger.exception("[Service] Error building dashboard for: %s", dealer_name)
            return f"⚠️ Error loading dashboard: {str(e)}"
    
    def _normalize_name(self, name: str) -> str:
        """Normalize dealer name consistently."""
        if not name:
            return ""
        return " ".join(name.strip().lower().split())
    
    def _format_suggestions(self, query: str, suggestions: List[str]) -> str:
        """Format suggestions for display"""
        if not suggestions:
            return f"🔍 No dealers found matching '{query}'"
        
        lines = [
            f"🔍 No exact match for '{query}'",
            "",
            "💡 Did you mean:",
            ""
        ]
        
        for i, s in enumerate(suggestions[:5], 1):
            lines.append(f"{i}. {s}")
        
        lines.extend([
            "",
            f"Type the exact name or try: {query}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Type a dealer name to search"
        ])
        
        return "\n".join(lines)
    
    def _render_dashboard(self, data: Dict[str, Any]) -> str:
        """Render the dashboard in the exact format requested"""
        
        # Extract data
        customer_name = data.get('customer_name', 'Unknown')
        dealer_code = data.get('dealer_code', 'N/A')
        city = data.get('city', 'N/A')
        warehouse = data.get('warehouse', 'N/A')
        distance_km = data.get('distance_km')
        distance_time = data.get('distance_time', 'Not Available')
        division = data.get('division', 'N/A')
        
        revenue = data.get('total_revenue', 0)
        dn_count = data.get('dn_count', 0)
        total_units = data.get('total_units', 0)
        delivered = data.get('pod_completed', 0)
        pending_dn = data.get('pending_dn', 0)
        avg_delivery_days = data.get('avg_delivery_days', 0)
        pod_achievement = data.get('pod_achievement', 0)
        pgi_achievement = data.get('pgi_achievement', 0)
        rating = data.get('rating', 'C')
        
        # Get top customer models from database
        top_models = self._get_top_models(customer_name)
        
        # Get best month
        best_month = self._get_highest_sales_month(customer_name)
        
        # Get revenue trend
        revenue_trend = self._get_revenue_trend(customer_name)
        
        # Clean dealer name - remove phone numbers and C/O
        clean_name = re.sub(r'0[0-9]{2,4}[-.\s]?[0-9]{7,8}', '', customer_name)
        clean_name = re.sub(r'C/O\s*', '', clean_name, flags=re.IGNORECASE)
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        
        # Format road distance
        if distance_km is not None and distance_km > 0:
            distance_display = f"{distance_km} KM ({distance_time})"
        else:
            distance_display = "Not Available"
        
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏢 HAIER DEALER INTELLIGENCE CENTER",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"👤 {clean_name}",
            f"📍 {city}",
            f"🏬 Dispatch WH : {warehouse}",
            f"📏 Road Distance : {distance_display}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "💼 BUSINESS SUMMARY",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"💰 Revenue            {_format_currency(revenue)}",
            f"📦 Units Sold         {_format_number(total_units)}",
            f"🚚 Delivery Notes     {_format_number(dn_count)}",
            f"🏷️ Division           {division}",
            f"⭐ Dealer Rating       {rating}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "🏆 TOP CUSTOMER MODELS",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        
        # Add top customer models with clean formatting
        if top_models:
            for i, (model, count) in enumerate(top_models):
                # Clean up model name - remove extra spaces
                model_display = model.strip() if model else "N/A"
                
                # Format count with proper pluralization
                count_display = f"{count} Unit{'s' if count > 1 else ''}"
                
                # Calculate spacing for alignment
                model_len = len(model_display)
                if model_len <= 10:
                    padding = " " * 18
                elif model_len <= 15:
                    padding = " " * 15
                elif model_len <= 20:
                    padding = " " * 12
                else:
                    padding = " " * 8
                
                if i == 0:
                    lines.append(f"🥇 {model_display}{padding}{count_display}")
                elif i == 1:
                    lines.append(f"🥈 {model_display}{padding}{count_display}")
                elif i == 2:
                    lines.append(f"🥉 {model_display}{padding}{count_display}")
                else:
                    lines.append(f"•  {model_display}{padding}{count_display}")
        else:
            lines.append("   No models found")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "🚛 DELIVERY PERFORMANCE",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"✅ Delivered DNs      {_format_number(delivered)} ({pod_achievement:.1f}%)",
            f"⏳ Pending DNs        {_format_number(pending_dn)}",
            f"📅 Avg Delivery       {avg_delivery_days:.1f} Days",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "📊 SERVICE KPIs",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"⚡ PGI Achievement    {pgi_achievement:.1f}%",
            f"📄 POD Achievement    {pod_achievement:.1f}%",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "📈 SALES INSIGHTS",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📅 Best Month         {best_month}",
            f"🏬 Primary Warehouse  {warehouse}",
            f"📦 Best Seller        {top_models[0][0] if top_models else 'N/A'}",
            f"📊 Revenue Trend      {revenue_trend}",
            f"🚛 Delivery Status    {self._get_delivery_performance(pod_achievement)}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "🤖 AI RECOMMENDATIONS",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ])
        
        # Add AI recommendations
        recommendations = self._get_ai_recommendations(data, top_models)
        for rec in recommendations:
            lines.append(f"✅ {rec}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Type a dealer name to search",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        
        return "\n".join(lines)
    
    def _get_top_models(self, customer_name: str, limit: int = 7) -> List[Tuple[str, int]]:
        """Get top customer models for the dealer from PostgreSQL"""
        try:
            with engine.connect() as conn:
                results = conn.execute(
                    text("""
                        SELECT customer_model, COUNT(dn_no) as count
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(customer_name)) = LOWER(TRIM(:name))
                        AND customer_model IS NOT NULL 
                        AND TRIM(customer_model) != ''
                        GROUP BY customer_model
                        ORDER BY count DESC
                        LIMIT :limit
                    """),
                    {"name": customer_name, "limit": limit}
                ).fetchall()
                return [(r[0], int(r[1])) for r in results if r[0]]
        except Exception as e:
            logger.error(f"Error getting top models: {e}")
            return []
    
    def _get_highest_sales_month(self, customer_name: str) -> str:
        """Get highest sales month"""
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT TO_CHAR(dn_create_date, 'Month') as month, 
                               SUM(dn_amount) as revenue
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(customer_name)) = LOWER(TRIM(:name))
                        GROUP BY TO_CHAR(dn_create_date, 'Month'), 
                                 EXTRACT(MONTH FROM dn_create_date)
                        ORDER BY revenue DESC
                        LIMIT 1
                    """),
                    {"name": customer_name}
                ).first()
                return result[0].strip() if result else "N/A"
        except Exception as e:
            logger.error(f"Error getting best month: {e}")
            return "N/A"
    
    def _get_revenue_trend(self, customer_name: str) -> str:
        """Get revenue trend (growth or decline)"""
        try:
            with engine.connect() as conn:
                # Get last two months revenue
                result = conn.execute(
                    text("""
                        SELECT 
                            SUM(dn_amount) as revenue
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(customer_name)) = LOWER(TRIM(:name))
                        AND dn_create_date >= CURRENT_DATE - INTERVAL '3 months'
                        GROUP BY EXTRACT(MONTH FROM dn_create_date)
                        ORDER BY EXTRACT(MONTH FROM dn_create_date) DESC
                        LIMIT 2
                    """),
                    {"name": customer_name}
                ).fetchall()
                
                if len(result) >= 2:
                    current = float(result[0][0] or 0)
                    previous = float(result[1][0] or 0)
                    if previous > 0:
                        growth = ((current - previous) / previous) * 100
                        if growth > 10:
                            return "High Growth ↑"
                        elif growth > 0:
                            return "Growing ↑"
                        elif growth > -10:
                            return "Stable →"
                        else:
                            return "Declining ↓"
                return "Stable →"
        except Exception as e:
            logger.error(f"Error getting revenue trend: {e}")
            return "Stable →"
    
    def _get_delivery_performance(self, delivery_rate: float) -> str:
        """Get delivery performance rating"""
        if delivery_rate >= 95:
            return "Excellent"
        elif delivery_rate >= 85:
            return "Good"
        elif delivery_rate >= 70:
            return "Average"
        else:
            return "Needs Improvement"
    
    def _get_ai_recommendations(self, data: Dict[str, Any], top_models: List[Tuple[str, int]]) -> List[str]:
        """Generate AI recommendations"""
        recommendations = []
        
        # Best selling model recommendation
        if top_models:
            model = top_models[0][0]
            recommendations.append(f"Maintain stock of {model}.")
        
        # Pending deliveries
        pending = data.get('pending_dn', 0)
        if pending > 0:
            recommendations.append(f"🚚 Prioritize dispatch of {pending} pending DNs.")
        else:
            recommendations.append("All deliveries completed. Excellent efficiency!")
        
        # POD compliance
        pod_achievement = data.get('pod_achievement', 0)
        if pod_achievement < 90:
            recommendations.append("📄 Improve POD compliance through timely document submission.")
        
        # Rating improvement
        rating = data.get('rating', 'C')
        if rating in ['C+', 'C']:
            recommendations.append("⚡ Improve PGI & POD to achieve an 'A' dealer rating.")
        
        # Diversification suggestion
        if len(top_models) > 1:
            recommendations.append("📈 Increase focus on AC and Refrigerator models to diversify sales.")
        
        # Warehouse recommendation
        warehouse = data.get('warehouse', '')
        if warehouse and warehouse != 'Unknown':
            recommendations.append(f"🎯 Continue dispatches from {warehouse} for faster deliveries.")
        
        return recommendations[:6]  # Limit to 6 recommendations
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()

# ============================================================
# SINGLETON & EXPORTS
# ============================================================

_dealer_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    global _dealer_service
    try:
        if _dealer_service is None:
            logger.info("🔧 Creating DealerAnalyticsService instance...")
            _dealer_service = DealerAnalyticsService()
            logger.info("✅ DealerAnalyticsService instance created successfully")
        return _dealer_service
    except Exception as e:
        logger.error(f"❌ Failed to create DealerAnalyticsService: {e}")
        import traceback
        logger.error(traceback.format_exc())
        _dealer_service = DealerAnalyticsService()
        return _dealer_service

__all__ = [
    "DealerAnalyticsService",
    "get_dealer_service",
    "VERSION"
]
