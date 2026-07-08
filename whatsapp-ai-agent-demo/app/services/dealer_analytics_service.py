#!/usr/bin/env python3
# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 12.11 - IMPROVED DISTANCE CALCULATION
# ============================================================

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from typing import Any, Optional, Dict, List, Tuple

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

# Cache for geocoding results
_GEOCODE_CACHE: Dict[str, Tuple[float, float]] = {}
_DISTANCE_CACHE: Dict[str, Tuple[float, str]] = {}

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

def _geocode_city(city: str) -> Optional[Tuple[float, float]]:
    """Geocode a city name to get coordinates using geopy"""
    if not city:
        return None
    
    city_clean = city.strip()
    cache_key = city_clean.lower()
    
    # Check cache first
    if cache_key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[cache_key]
    
    # Try geopy first
    if GEOCODE_AVAILABLE:
        try:
            geolocator = Nominatim(user_agent="dealer_intelligence")
            geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
            
            # Try with "Pakistan" to improve accuracy
            location = geocode(f"{city_clean}, Pakistan")
            if location:
                coords = (location.latitude, location.longitude)
                _GEOCODE_CACHE[cache_key] = coords
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
                _GEOCODE_CACHE[cache_key] = coords_tuple
                logger.info(f"✅ ORS geocoded '{city_clean}' → {coords_tuple}")
                return coords_tuple
        except Exception as e:
            logger.warning(f"ORS geocoding failed for '{city_clean}': {e}")
    
    # Fallback: Try to find in hardcoded city list
    fallback_coords = _get_fallback_coordinates(city_clean)
    if fallback_coords:
        _GEOCODE_CACHE[cache_key] = fallback_coords
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
        "hafizabad": (32.0667, 73.6833),  # Added Hafizabad
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

def _get_distance_between_cities(city1: str, city2: str) -> Tuple[float, str]:
    """Get distance between two cities using OpenRouteService"""
    
    if not city1 or not city2:
        return (0, "Unknown")
    
    cache_key = f"{city1.lower()}|{city2.lower()}"
    if cache_key in _DISTANCE_CACHE:
        return _DISTANCE_CACHE[cache_key]
    
    # Get coordinates for both cities
    coords1 = _geocode_city(city1)
    coords2 = _geocode_city(city2)
    
    if not coords1 or not coords2:
        logger.warning(f"Could not get coordinates for {city1} or {city2}")
        return (0, "Not Available")
    
    # Try OpenRouteService for route distance
    if ORS_AVAILABLE and ORS_API_KEY:
        try:
            client = openrouteservice.Client(key=ORS_API_KEY)
            
            # ORS expects [lng, lat]
            coordinates = [
                [coords1[1], coords1[0]],
                [coords2[1], coords2[0]]
            ]
            
            routes = client.directions(
                coordinates=coordinates,
                profile=ORS_PROFILE,
                format='json',
                validate=False
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
                _DISTANCE_CACHE[cache_key] = result
                logger.info(f"✅ ORS distance: {city1} → {city2}: {distance_km:.1f} KM, {time_str}")
                return result
                
        except Exception as e:
            logger.error(f"ORS distance calculation failed: {e}")
    
    # Fallback: Calculate straight-line distance using Haversine
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
    _DISTANCE_CACHE[cache_key] = result
    logger.info(f"⚠️ Fallback distance: {city1} → {city2}: {distance_km:.1f} KM, {time_str}")
    return result

def _haversine_distance(coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
    """Calculate straight-line distance using Haversine formula"""
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
        
        # Get distance using improved geocoding
        city = data.get('city', '')
        warehouse = data.get('warehouse', '')
        
        logger.info(f"[Repository] Calculating distance: Warehouse='{warehouse}', City='{city}'")
        
        if city and warehouse and city != 'Unknown' and warehouse != 'Unknown':
            try:
                # Use improved distance calculation
                distance_km, time_str = _get_distance_between_cities(warehouse, city)
                data['distance_km'] = distance_km
                data['distance_time'] = time_str
                logger.info(f"[Repository] Distance calculated: {distance_km} KM, {time_str}")
            except Exception as e:
                logger.error(f"[Repository] Error calculating distance: {e}")
                data['distance_km'] = None
                data['distance_time'] = "Not Available"
        else:
            logger.warning(f"[Repository] Cannot calculate distance: city='{city}', warehouse='{warehouse}'")
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
        
        if ORS_AVAILABLE and ORS_API_KEY:
            logger.info("   Using OpenRouteService for distance calculations")
        elif GEOCODE_AVAILABLE:
            logger.info("   Using Geopy for geocoding with fallback")
        else:
            logger.info("   Using fallback distance calculation")
    
    def handle_message(self, message: str, sender: str) -> str:
        """Main entry point - searches for dealer and returns dashboard"""
        try:
            message_clean = message.strip()
            
            # Check if it's a numeric command (1-9)
            if message_clean.isdigit() and len(message_clean) == 1:
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
        
        return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏢 DEALER INTELLIGENCE CENTER
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
• Distance from warehouse

🗺️ **Distance Services:**
• OpenRouteService: {ors_status}
• Geopy (Geocoding): {geopy_status}

💡 **Pro tip:** 
Type partial names and we'll suggest matches!

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
        
        # Format distance
        if distance_km is not None and distance_km > 0:
            distance_str = f"{distance_km} KM (Estimated {distance_time})"
        else:
            distance_str = "Not Available"
        
        # Clean dealer name - remove phone numbers and C/O
        clean_name = re.sub(r'0[0-9]{2,4}[-.\s]?[0-9]{7,8}', '', customer_name)
        clean_name = re.sub(r'C/O\s*', '', clean_name, flags=re.IGNORECASE)
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏢 DEALER INTELLIGENCE CENTER",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"👤 Dealer",
            f"{clean_name}",
            "",
            f"🆔 Dealer Code",
            f"{dealer_code}",
            "",
            f"📍 City",
            f"{city}",
            "",
            f"🏬 Warehouse",
            f"{warehouse}",
            "",
            f"📏 Distance",
            f"{distance_str}",
            "",
            f"📦 Division",
            f"{division}",
            "",
            f"💰 Total Revenue",
            f"{_format_currency(revenue)}",
            "",
            f"📦 Total DNs",
            f"{_format_number(dn_count)}",
            "",
            f"📦 Total Units",
            f"{_format_number(total_units)}",
            "",
            f"🚚 Delivered",
            f"{_format_number(delivered)} ({pod_achievement:.1f}%)",
            "",
            f"⏳ Pending DNs",
            f"{_format_number(pending_dn)}",
            "",
            f"📅 Avg Delivery Time",
            f"{avg_delivery_days:.1f} Days",
            "",
            f"📄 POD Achievement",
            f"{pod_achievement:.1f}%",
            "",
            f"⚡ PGI Achievement",
            f"{pgi_achievement:.1f}%",
            "",
            f"⭐ Dealer Rating",
            f"{rating}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "📈 BUSINESS INSIGHTS",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"• Best Selling Model : {self._get_best_selling_model(customer_name)}",
            f"• Highest Sales Month : {self._get_highest_sales_month(customer_name)}",
            f"• Revenue Growth : {self._get_revenue_growth(customer_name)}",
            f"• Delivery Performance : {self._get_delivery_performance(pod_achievement)}",
            f"• Primary Warehouse : {warehouse}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "🤖 AI RECOMMENDATIONS",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        
        # Add AI recommendations
        recommendations = self._get_ai_recommendations(data)
        for rec in recommendations:
            lines.append(f"✅ {rec}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Type a dealer name to search",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        
        return "\n".join(lines)
    
    def _get_best_selling_model(self, customer_name: str) -> str:
        """Get best selling model for the dealer"""
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT material_no, COUNT(dn_no) as count
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(customer_name)) = LOWER(TRIM(:name))
                        GROUP BY material_no
                        ORDER BY count DESC
                        LIMIT 1
                    """),
                    {"name": customer_name}
                ).first()
                return result[0] if result else "N/A"
        except Exception:
            return "N/A"
    
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
        except Exception:
            return "N/A"
    
    def _get_revenue_growth(self, customer_name: str) -> str:
        """Calculate revenue growth"""
        try:
            with engine.connect() as conn:
                # Get last two months revenue
                result = conn.execute(
                    text("""
                        SELECT 
                            EXTRACT(MONTH FROM dn_create_date) as month,
                            SUM(dn_amount) as revenue
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(customer_name)) = LOWER(TRIM(:name))
                        AND dn_create_date >= CURRENT_DATE - INTERVAL '3 months'
                        GROUP BY EXTRACT(MONTH FROM dn_create_date)
                        ORDER BY month DESC
                        LIMIT 2
                    """),
                    {"name": customer_name}
                ).fetchall()
                
                if len(result) >= 2:
                    current = float(result[0][1] or 0)
                    previous = float(result[1][1] or 0)
                    if previous > 0:
                        growth = ((current - previous) / previous) * 100
                        arrow = "↑" if growth > 0 else "↓"
                        return f"{arrow} {abs(growth):.1f}%"
                return "0.0%"
        except Exception:
            return "0.0%"
    
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
    
    def _get_ai_recommendations(self, data: Dict[str, Any]) -> List[str]:
        """Generate AI recommendations"""
        recommendations = []
        
        # Best selling model recommendation
        model = self._get_best_selling_model(data.get('customer_name', ''))
        if model != 'N/A':
            recommendations.append(f"Maintain inventory of {model}.")
        
        # Pending deliveries
        pending = data.get('pending_dn', 0)
        if pending > 0:
            recommendations.append(f"Expedite {pending} pending deliveries.")
        else:
            recommendations.append("All deliveries completed. Excellent efficiency!")
        
        # Performance based recommendations
        delivery_rate = data.get('delivery_rate', 0)
        if delivery_rate >= 95:
            recommendations.append("Dealer qualifies for Premium Service.")
        elif delivery_rate >= 85:
            recommendations.append("Dealer qualifies for Priority Service.")
        
        # Warehouse recommendation
        warehouse = data.get('warehouse', '')
        city = data.get('city', '')
        if warehouse and city and warehouse != 'Unknown' and city != 'Unknown':
            recommendations.append(f"Current warehouse is the nearest dispatch point.")
        
        # Rating based recommendations
        rating = data.get('rating', 'C')
        if rating in ['A+', 'A']:
            recommendations.append("Dealer is eligible for exclusive offers.")
        elif rating in ['C+', 'C']:
            recommendations.append("Consider providing additional support to improve performance.")
        
        return recommendations[:5]  # Limit to 5 recommendations
    
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
