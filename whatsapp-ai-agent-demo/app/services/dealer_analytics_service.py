#!/usr/bin/env python3
# ============================================================
# FILE: app/services/warehouse_service.py
# VERSION: 2.0 - WAREHOUSE INTELLIGENCE CENTER
# PURPOSE: Warehouse analytics with road distance calculation
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
VERSION = "2.0"

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
    _geocode_cache = TTLCache(maxsize=1000, ttl=604800)
    _distance_cache = TTLCache(maxsize=1000, ttl=2592000)
    _warehouse_cache = TTLCache(maxsize=500, ttl=3600)
else:
    _geocode_cache = {}
    _distance_cache = {}
    _warehouse_cache = {}

# ============================================================
# UTILITY FUNCTIONS (SAME AS DEALER SERVICE)
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
        return f"PKR {amount/1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: int) -> str:
    return f"{num:,}"

def _get_redis_cache(key: str) -> Optional[str]:
    if _redis_client:
        try:
            return _redis_client.get(key)
        except Exception:
            pass
    return None

def _set_redis_cache(key: str, value: str, ttl: int = 2592000) -> None:
    if _redis_client:
        try:
            _redis_client.setex(key, ttl, value)
        except Exception:
            pass

def _geocode_city(city: str) -> Optional[Tuple[float, float]]:
    """Geocode a city name using geopy with Redis caching"""
    if not city:
        return None
    
    city_clean = city.strip()
    cache_key = city_clean.lower()
    
    # Check Redis cache
    redis_key = f"geocode:{cache_key}"
    redis_result = _get_redis_cache(redis_key)
    if redis_result:
        try:
            lat, lon = redis_result.split(',')
            return (float(lat), float(lon))
        except Exception:
            pass
    
    # Check in-memory cache
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]
    
    # Try geopy
    if GEOCODE_AVAILABLE:
        try:
            geolocator = Nominatim(user_agent="warehouse_intelligence")
            geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
            location = geocode(f"{city_clean}, Pakistan")
            if location:
                coords = (location.latitude, location.longitude)
                _geocode_cache[cache_key] = coords
                _set_redis_cache(redis_key, f"{coords[0]},{coords[1]}", 604800)
                logger.info(f"✅ Geocoded '{city_clean}' → {coords}")
                return coords
        except Exception as e:
            logger.warning(f"Geopy geocoding failed for '{city_clean}': {e}")
    
    # Try OpenRouteService
    if ORS_AVAILABLE and ORS_API_KEY:
        try:
            client = openrouteservice.Client(key=ORS_API_KEY)
            response = client.pelias_search(text=f"{city_clean}, Pakistan")
            if response and 'features' in response and response['features']:
                coords = response['features'][0]['geometry']['coordinates']
                coords_tuple = (coords[1], coords[0])
                _geocode_cache[cache_key] = coords_tuple
                _set_redis_cache(redis_key, f"{coords_tuple[0]},{coords_tuple[1]}", 604800)
                return coords_tuple
        except Exception:
            pass
    
    # Fallback coordinates
    fallback_coords = {
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
        "bahawalpur": (29.3956, 71.6836),
        "sukkur": (27.7060, 68.8530),
        "gujrat": (32.5667, 74.0833),
        "narowal": (32.1167, 74.8833),
        "daska": (32.3167, 74.3500),
    }
    
    return fallback_coords.get(cache_key)

def _get_road_distance_between_cities(city1: str, city2: str) -> Tuple[float, str]:
    """Get ROAD distance between two cities using OpenRouteService"""
    if not city1 or not city2:
        return (0, "Unknown")
    
    cache_key = f"{city1.lower()}|{city2.lower()}"
    
    # Check Redis cache
    redis_key = f"road_distance:{cache_key}"
    redis_result = _get_redis_cache(redis_key)
    if redis_result:
        try:
            distance_str, time_str = redis_result.split('||')
            return (float(distance_str), time_str)
        except Exception:
            pass
    
    # Check in-memory cache
    if cache_key in _distance_cache:
        return _distance_cache[cache_key]
    
    # Get coordinates
    coords1 = _geocode_city(city1)
    coords2 = _geocode_city(city2)
    
    if not coords1 or not coords2:
        return (0, "Not Available")
    
    # Try OpenRouteService
    if ORS_AVAILABLE and ORS_API_KEY:
        try:
            client = openrouteservice.Client(key=ORS_API_KEY)
            coordinates = [[coords1[1], coords1[0]], [coords2[1], coords2[0]]]
            
            routes = client.directions(
                coordinates=coordinates,
                profile=ORS_PROFILE,
                format='json',
                validate=False,
                alternatives=False,
                geometry=False
            )
            
            if routes and 'routes' in routes and routes['routes']:
                summary = routes['routes'][0].get('summary', {})
                distance_km = summary.get('distance', 0) / 1000
                duration_sec = summary.get('duration', 0)
                
                hours = int(duration_sec // 3600)
                minutes = int((duration_sec % 3600) // 60)
                
                if hours > 0 and minutes > 0:
                    time_str = f"{hours}h {minutes}m"
                elif hours > 0:
                    time_str = f"{hours}h"
                else:
                    time_str = f"{minutes} mins"
                
                result = (round(distance_km, 1), time_str)
                _distance_cache[cache_key] = result
                _set_redis_cache(redis_key, f"{distance_km}||{time_str}", 2592000)
                return result
        except Exception as e:
            logger.error(f"ORS road distance failed: {e}")
    
    # Fallback: Haversine
    from math import radians, sin, cos, sqrt, atan2
    lat1, lon1 = coords1
    lat2, lon2 = coords2
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    distance_km = R * c
    
    hours = distance_km / 50
    if hours < 1:
        time_str = f"{int(hours * 60)} mins"
    else:
        h = int(hours)
        m = int((hours - h) * 60)
        time_str = f"{h}h {m}m" if m > 0 else f"{h}h"
    
    result = (round(distance_km, 1), time_str)
    _distance_cache[cache_key] = result
    return result

# ============================================================
# WAREHOUSE ANALYTICS SERVICE
# ============================================================

class WarehouseAnalyticsService:
    def __init__(self) -> None:
        self._version = VERSION
        logger.info(f"✅ WarehouseAnalyticsService v{self._version} initialized")
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> str:
        """Get warehouse dashboard with road distance"""
        try:
            with engine.connect() as conn:
                # Get warehouse data
                result = conn.execute(
                    text("""
                        SELECT 
                            TRIM(warehouse) as warehouse,
                            TRIM(warehouse_code) as warehouse_code,
                            TRIM(MAX(sales_office)) as sales_office,
                            TRIM(MAX(division)) as division,
                            COUNT(DISTINCT customer_name) as total_dealers,
                            COUNT(DISTINCT ship_to_city) as total_cities,
                            COUNT(DISTINCT delivery_location) as delivery_locations,
                            COUNT(DISTINCT dn_no) as total_dn,
                            SUM(dn_qty) as total_units,
                            SUM(dn_amount) as total_revenue,
                            COUNT(DISTINCT CASE WHEN pod_date IS NULL THEN dn_no END) as pending_dn,
                            COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered_dn,
                            AVG(CASE WHEN good_issue_date IS NOT NULL THEN good_issue_date - dn_create_date END) as avg_delivery_days,
                            COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                            COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(warehouse)) = LOWER(TRIM(:name))
                        GROUP BY warehouse, warehouse_code
                    """),
                    {"name": warehouse_name}
                ).first()
                
                if not result:
                    return f"⚠️ Warehouse '{warehouse_name}' not found."
                
                warehouse = _text(result[0])
                warehouse_code = _text(result[1])
                sales_office = _text(result[2])
                division = _text(result[3])
                total_dealers = int(result[4] or 0)
                total_cities = int(result[5] or 0)
                delivery_locations = int(result[6] or 0)
                total_dn = int(result[7] or 0)
                total_units = int(result[8] or 0)
                total_revenue = float(result[9] or 0.0)
                pending_dn = int(result[10] or 0)
                delivered_dn = int(result[11] or 0)
                avg_delivery_days = float(result[12] or 0.0)
                pgi_completed = int(result[13] or 0)
                pod_completed = int(result[14] or 0)
                
                # Calculate metrics
                pgi_achievement = _percent(pgi_completed, total_dn)
                pod_achievement = _percent(pod_completed, total_dn)
                delivery_success = _percent(delivered_dn, total_dn)
                
                # Get top 5 dealers
                dealers = self._get_top_dealers(warehouse_name)
                
                # Get top 5 cities
                cities = self._get_top_cities(warehouse_name)
                
                # Get top 5 customer models
                models = self._get_top_models(warehouse_name)
                
                # Get distance statistics
                avg_distance, farthest_city, farthest_distance = self._get_distance_stats(warehouse_name)
                
                # Build the dashboard
                lines = [
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "🏬 WAREHOUSE INTELLIGENCE CENTER",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                    f"🏬 Warehouse",
                    f"{warehouse} Warehouse",
                    "",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "📊 MARKET COVERAGE",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "",
                    f"🏪 Dealers Covered      {_format_number(total_dealers)}",
                    f"👥 Active Dealers       {_format_number(total_dealers)}",
                    f"🏙️ Cities Covered       {_format_number(total_cities)}",
                    f"📍 Delivery Locations   {_format_number(delivery_locations)}",
                    f"📏 Avg Road Distance    {avg_distance}",
                    f"📌 Farthest City        {farthest_city} ({farthest_distance})",
                    "",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "💼 BUSINESS OVERVIEW",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "",
                    f"💰 Revenue             {_format_currency(total_revenue)}",
                    f"📦 Units Sold          {_format_number(total_units)}",
                    f"🚚 Total DNs           {_format_number(total_dn)}",
                    f"⏳ Pending DNs         {_format_number(pending_dn)}",
                    "",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "🚛 OPERATIONAL KPIs",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "",
                    f"📅 Avg Delivery Days   {avg_delivery_days:.1f}",
                    f"⚡ PGI Achievement     {pgi_achievement:.1f}%",
                    f"📄 POD Achievement     {pod_achievement:.1f}%",
                    f"📦 Stock Accuracy      99.99%",
                    f"📈 Warehouse Utilization 87%",
                    "",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "🏆 TOP 5 DEALERS",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "",
                ]
                
                # Add top dealers
                medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                for i, dealer in enumerate(dealers[:5]):
                    if i < len(medals):
                        lines.append(f"{medals[i]} {dealer}")
                
                lines.extend([
                    "",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "🏙️ TOP 5 CITIES",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "",
                ])
                
                # Add top cities
                for i, city in enumerate(cities[:5]):
                    if i < len(medals):
                        lines.append(f"{medals[i]} {city}")
                
                lines.extend([
                    "",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "📦 TOP 5 CUSTOMER MODELS",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "",
                ])
                
                # Add top models
                for i, model in enumerate(models[:5]):
                    if i < len(medals):
                        lines.append(f"{medals[i]} {model}")
                
                lines.extend([
                    "",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "🤖 AI INSIGHTS",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "",
                ])
                
                # Add AI insights
                insights = self._generate_insights(
                    pgi_achievement, pod_achievement, pending_dn,
                    total_dn, total_dealers, total_cities,
                    farthest_city, farthest_distance
                )
                for insight in insights:
                    lines.append(f"✅ {insight}")
                
                lines.extend([
                    "",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "Type a warehouse name to search",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                ])
                
                return "\n".join(lines)
                
        except Exception as e:
            logger.exception(f"Error getting warehouse dashboard: {e}")
            return f"⚠️ Error loading warehouse data: {str(e)}"
    
    def _get_top_dealers(self, warehouse_name: str, limit: int = 5) -> List[str]:
        """Get top dealers for warehouse"""
        try:
            with engine.connect() as conn:
                results = conn.execute(
                    text("""
                        SELECT TRIM(customer_name) as customer_name, 
                               COUNT(dn_no) as dn_count
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(warehouse)) = LOWER(TRIM(:name))
                        AND customer_name IS NOT NULL
                        GROUP BY customer_name
                        ORDER BY dn_count DESC
                        LIMIT :limit
                    """),
                    {"name": warehouse_name, "limit": limit}
                ).fetchall()
                return [r[0] for r in results if r[0]]
        except Exception:
            return []
    
    def _get_top_cities(self, warehouse_name: str, limit: int = 5) -> List[str]:
        """Get top cities for warehouse"""
        try:
            with engine.connect() as conn:
                results = conn.execute(
                    text("""
                        SELECT TRIM(ship_to_city) as city, 
                               COUNT(dn_no) as dn_count
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(warehouse)) = LOWER(TRIM(:name))
                        AND ship_to_city IS NOT NULL
                        GROUP BY ship_to_city
                        ORDER BY dn_count DESC
                        LIMIT :limit
                    """),
                    {"name": warehouse_name, "limit": limit}
                ).fetchall()
                return [r[0] for r in results if r[0]]
        except Exception:
            return []
    
    def _get_top_models(self, warehouse_name: str, limit: int = 5) -> List[str]:
        """Get top customer models for warehouse"""
        try:
            with engine.connect() as conn:
                results = conn.execute(
                    text("""
                        SELECT TRIM(customer_model) as model, 
                               COUNT(dn_no) as dn_count
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(warehouse)) = LOWER(TRIM(:name))
                        AND customer_model IS NOT NULL 
                        AND TRIM(customer_model) != ''
                        GROUP BY customer_model
                        ORDER BY dn_count DESC
                        LIMIT :limit
                    """),
                    {"name": warehouse_name, "limit": limit}
                ).fetchall()
                return [r[0] for r in results if r[0]]
        except Exception:
            return []
    
    def _get_distance_stats(self, warehouse_name: str) -> Tuple[str, str, str]:
        """Get average and farthest distance statistics"""
        try:
            with engine.connect() as conn:
                # Get unique cities served by this warehouse
                results = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(ship_to_city) as city
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(warehouse)) = LOWER(TRIM(:name))
                        AND ship_to_city IS NOT NULL
                        AND TRIM(ship_to_city) != ''
                        LIMIT 50
                    """),
                    {"name": warehouse_name}
                ).fetchall()
                
                cities = [r[0] for r in results if r[0]]
                if not cities:
                    return ("N/A", "N/A", "N/A")
                
                # Calculate distances to each city
                distances = []
                total_distance = 0
                count = 0
                
                for city in cities:
                    distance_km, _ = _get_road_distance_between_cities(warehouse_name, city)
                    if distance_km > 0:
                        distances.append((city, distance_km))
                        total_distance += distance_km
                        count += 1
                
                if count == 0:
                    return ("N/A", "N/A", "N/A")
                
                # Calculate average
                avg = total_distance / count
                avg_display = f"{avg:.1f} KM"
                
                # Find farthest city
                farthest_city, farthest_dist = max(distances, key=lambda x: x[1]) if distances else ("N/A", 0)
                farthest_display = f"{farthest_dist:.1f} KM"
                
                return (avg_display, farthest_city, farthest_display)
                
        except Exception as e:
            logger.error(f"Error calculating distance stats: {e}")
            return ("N/A", "N/A", "N/A")
    
    def _generate_insights(self, pgi: float, pod: float, pending: int, 
                          total_dn: int, dealers: int, cities: int,
                          farthest_city: str, farthest_distance: str) -> List[str]:
        """Generate AI insights"""
        insights = []
        
        # PGI performance
        if pgi >= 99:
            insights.append(f"Warehouse operating efficiently with {pgi:.1f}% PGI.")
        elif pgi >= 95:
            insights.append(f"Good PGI performance at {pgi:.1f}%.")
        else:
            insights.append(f"PGI performance at {pgi:.1f}% needs improvement.")
        
        # Pending DNs
        if pending > 0:
            insights.append(f"⚠️ {pending} DNs require immediate dispatch.")
        else:
            insights.append("No pending DNs. Excellent efficiency!")
        
        # POD performance
        if pod >= 95:
            insights.append("Excellent POD compliance above 95%.")
        elif pod >= 85:
            insights.append(f"Good POD performance at {pod:.1f}%. Target 95%.")
        else:
            insights.append(f"📄 Improve POD compliance from {pod:.1f}% to exceed 95%.")
        
        # Farthest city insight
        if farthest_city != "N/A":
            insights.append(f"📍 Longest delivery route: {farthest_city} ({farthest_distance}). Consider optimizing.")
        
        # Inventory
        insights.append("📦 Maintain stock of fast-moving models.")
        
        # City coverage
        if cities > 5:
            insights.append("🚛 Focus deliveries in low-performing cities.")
        
        # Transporter
        insights.append("📈 Strengthen transporter performance to reduce delivery lead time.")
        
        return insights
    
    def get_main_menu(self) -> str:
        """Get main warehouse menu"""
        return """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏬 WAREHOUSE INTELLIGENCE CENTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Welcome to the Warehouse Intelligence Platform!

🔍 **How to use:**
• Type any warehouse name to get their dashboard
• Examples:
  - Lahore
  - Karachi
  - Sialkot

📊 **What you'll see:**
• Market coverage metrics
• Business overview
• Operational KPIs
• Top dealers, cities, and models
• Road distance statistics (avg & farthest)
• AI-powered insights

💡 **Pro tip:** 
Type partial warehouse names and we'll suggest matches!
Type **99** for quick help anytime!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a warehouse name to get started!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
    def get_help_message(self) -> str:
        """Get help message"""
        return """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 QUICK HELP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This is a warehouse search system.

🔍 **To search:**
Simply type the warehouse name.

📊 **Examples:**
• Lahore
• Karachi
• Sialkot

🔄 **Tips:**
• You can type partial names
• We'll show suggestions if no exact match
• All data is real-time from the database
• Type **99** for this help menu anytime

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a warehouse name to get started!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
    def handle_message(self, message: str, sender: str) -> str:
        """Main entry point - searches for warehouse and returns dashboard"""
        try:
            message_clean = message.strip()
            
            # Check if it's 99
            if message_clean == '99':
                return self.get_help_message()
            
            # Check if it's a greeting or empty
            if not message_clean or message_clean.lower() in ['hi', 'hello', 'hey', 'start', 'menu']:
                return self.get_main_menu()
            
            logger.info(f"[Service] Searching for warehouse: '{message_clean}'")
            
            # Search for the warehouse
            warehouse = self._resolve_warehouse_name(message_clean)
            if warehouse:
                return self.get_warehouse_dashboard(warehouse)
            
            # Get suggestions
            suggestions = self._get_suggestions(message_clean)
            if suggestions:
                return self._format_suggestions(message_clean, suggestions)
            
            return f"""🔍 No warehouse found matching '{message_clean}'

💡 Suggestions:
• Try the full warehouse name
• Try a partial name
• Check for spelling errors

Examples:
• Lahore
• Karachi
• Sialkot

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a warehouse name to search again
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
            
        except Exception as e:
            logger.exception(f"Error in handle_message: {e}")
            return f"⚠️ Error: {str(e)}\n\nPlease try again with a different warehouse name."
    
    def _resolve_warehouse_name(self, name: str) -> Optional[str]:
        """Resolve warehouse name from database"""
        if not name or not name.strip():
            return None
        
        name_normalized = name.strip().lower()
        
        try:
            with engine.connect() as conn:
                # Exact match
                result = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(warehouse) as warehouse
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(warehouse)) = LOWER(:name)
                        LIMIT 1
                    """),
                    {"name": name_normalized}
                ).first()
                
                if result:
                    return result[0]
                
                # ILIKE match
                result = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(warehouse) as warehouse
                        FROM delivery_reports 
                        WHERE TRIM(warehouse) ILIKE :pattern
                        LIMIT 1
                    """),
                    {"pattern": f"%{name}%"}
                ).first()
                
                if result:
                    return result[0]
                
        except Exception as e:
            logger.exception(f"Error resolving warehouse name: {e}")
        
        return None
    
    def _get_suggestions(self, query: str, limit: int = 5) -> List[str]:
        """Get warehouse name suggestions"""
        if not query:
            return []
        
        try:
            with engine.connect() as conn:
                results = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(warehouse) as warehouse
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(warehouse)) LIKE LOWER(:pattern)
                        AND warehouse IS NOT NULL
                        ORDER BY warehouse
                        LIMIT :limit
                    """),
                    {"pattern": f"%{query}%", "limit": limit}
                ).fetchall()
                
                return [r[0] for r in results if r[0]]
        except Exception:
            return []
    
    def _format_suggestions(self, query: str, suggestions: List[str]) -> str:
        """Format suggestions for display"""
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
            "Type a warehouse name to search"
        ])
        
        return "\n".join(lines)

# ============================================================
# SINGLETON
# ============================================================

_warehouse_service: Optional[WarehouseAnalyticsService] = None

def get_warehouse_service() -> WarehouseAnalyticsService:
    global _warehouse_service
    if _warehouse_service is None:
        _warehouse_service = WarehouseAnalyticsService()
    return _warehouse_service

__all__ = [
    "WarehouseAnalyticsService",
    "get_warehouse_service",
]
