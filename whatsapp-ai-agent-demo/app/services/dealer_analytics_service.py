#!/usr/bin/env python3
# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 13.0 - ROBUST & RELIABLE
# PURPOSE: Search dealers by name, return dashboard with KPIs.
#          Gracefully handles missing optional dependencies.
# ============================================================

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Optional, Dict, List, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

VERSION = "13.0"

# ============================================================
# OPTIONAL DEPENDENCIES – ALL GRACEFULLY HANDLED
# ============================================================

GEOCODE_AVAILABLE = False
ORS_AVAILABLE = False
CACHETOOLS_AVAILABLE = False
REDIS_AVAILABLE = False

try:
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    GEOCODE_AVAILABLE = True
    logger.info("✅ Geopy imported successfully")
except ImportError:
    logger.warning("⚠️ Geopy not available")

try:
    import openrouteservice
    ORS_AVAILABLE = True
    logger.info("✅ OpenRouteService imported successfully")
except ImportError:
    logger.warning("⚠️ OpenRouteService not available")

try:
    from cachetools import TTLCache
    CACHETOOLS_AVAILABLE = True
    logger.info("✅ Cachetools imported successfully")
except ImportError:
    logger.warning("⚠️ Cachetools not available")

try:
    import redis
    REDIS_AVAILABLE = True
    logger.info("✅ Redis imported successfully")
except ImportError:
    logger.warning("⚠️ Redis not available")

# ============================================================
# CACHE SETUP (fallback if not available)
# ============================================================

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

if CACHETOOLS_AVAILABLE:
    _geocode_cache = TTLCache(maxsize=1000, ttl=604800)
    _distance_cache = TTLCache(maxsize=1000, ttl=2592000)
else:
    _geocode_cache = {}
    _distance_cache = {}

# ============================================================
# UTILITY FUNCTIONS (same as before, with fallbacks)
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

# ============================================================
# GEOCODING (with fallbacks – never crashes)
# ============================================================

def _geocode_city(city: str) -> Optional[Tuple[float, float]]:
    """Geocode a city – returns None if unavailable."""
    if not city or not (GEOCODE_AVAILABLE or ORS_AVAILABLE):
        return None
    city_clean = city.strip()
    cache_key = city_clean.lower()
    redis_key = f"geocode:{cache_key}"

    # Check caches
    redis_result = _get_redis_cache(redis_key)
    if redis_result:
        try:
            lat, lon = redis_result.split(',')
            return (float(lat), float(lon))
        except Exception:
            pass
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    coords = None
    # Try geopy
    if GEOCODE_AVAILABLE:
        try:
            geolocator = Nominatim(user_agent="dealer_intelligence")
            geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
            location = geocode(f"{city_clean}, Pakistan")
            if location:
                coords = (location.latitude, location.longitude)
        except Exception as e:
            logger.warning(f"Geopy failed for '{city_clean}': {e}")

    # Try ORS if geopy failed
    if not coords and ORS_AVAILABLE:
        try:
            client = openrouteservice.Client(key=os.getenv("ORS_API_KEY", ""))
            response = client.pelias_search(text=f"{city_clean}, Pakistan")
            if response and 'features' in response and response['features']:
                lng, lat = response['features'][0]['geometry']['coordinates']
                coords = (lat, lng)
        except Exception as e:
            logger.warning(f"ORS failed for '{city_clean}': {e}")

    if coords:
        _geocode_cache[cache_key] = coords
        _set_redis_cache(redis_key, f"{coords[0]},{coords[1]}", 604800)
        return coords

    # Hardcoded fallback
    fallback = {
        "karachi": (24.8607, 67.0011),
        "lahore": (31.5204, 74.3587),
        "rawalpindi": (33.5651, 73.0169),
        "islamabad": (33.6844, 73.0479),
        "multan": (30.1575, 71.5249),
        "peshawar": (34.0151, 71.5249),
        "quetta": (30.1798, 66.9750),
        "hyderabad": (25.3960, 68.3578),
        "faisalabad": (31.4504, 73.1350),
        "gujrat": (32.5667, 74.0833),
    }
    return fallback.get(city_clean.lower())

def _haversine_distance(coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
    from math import radians, sin, cos, sqrt, atan2
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

def _get_road_distance_between_cities(city1: str, city2: str) -> Tuple[float, str]:
    """Return (distance_km, time_str) – fallback to Haversine if ORS fails."""
    if not city1 or not city2:
        return (0, "Unknown")
    cache_key = f"{city1.lower()}|{city2.lower()}"
    redis_key = f"road_distance:{cache_key}"

    # Check caches
    redis_result = _get_redis_cache(redis_key)
    if redis_result:
        try:
            d, t = redis_result.split('||')
            return (float(d), t)
        except Exception:
            pass
    if cache_key in _distance_cache:
        return _distance_cache[cache_key]

    coords1 = _geocode_city(city1)
    coords2 = _geocode_city(city2)
    if not coords1 or not coords2:
        return (0, "Not Available")

    if ORS_AVAILABLE:
        try:
            client = openrouteservice.Client(key=os.getenv("ORS_API_KEY", ""))
            routes = client.directions(
                coordinates=[[coords1[1], coords1[0]], [coords2[1], coords2[0]]],
                profile=os.getenv("ORS_PROFILE", "driving-car"),
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
            logger.warning(f"ORS distance failed: {e}")

    # Fallback to Haversine
    distance_km = _haversine_distance(coords1, coords2)
    hours = distance_km / 50
    if hours < 1:
        time_str = f"{int(hours*60)} mins"
    else:
        h = int(hours)
        m = int((hours - h) * 60)
        time_str = f"{h}h {m}m" if m > 0 else f"{h}h"
    result = (round(distance_km, 1), time_str)
    _distance_cache[cache_key] = result
    return result

def _get_dealer_rating(delivery_rate: float, revenue: float, pending_dn: int = 0, dn_count: int = 0) -> str:
    score = 0
    if delivery_rate >= 95:
        score += 40
    elif delivery_rate >= 85:
        score += 30
    elif delivery_rate >= 70:
        score += 20
    else:
        score += 10
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
# REPOSITORY – RAW SQL
# ============================================================

class DealerRepository:
    def __init__(self, session: Session):
        self.session = session

    def resolve_dealer(self, dealer_input: str) -> Optional[str]:
        """Exact or ILIKE match on customer_name."""
        if not dealer_input or not dealer_input.strip():
            return None
        dealer_clean = dealer_input.strip()
        logger.info(f"[Repository] Resolving dealer: '{dealer_clean}'")
        try:
            with engine.connect() as conn:
                # Exact match
                result = conn.execute(
                    text("""
                        SELECT TRIM(customer_name)
                        FROM delivery_reports
                        WHERE LOWER(TRIM(customer_name)) = LOWER(TRIM(:name))
                        LIMIT 1
                    """),
                    {"name": dealer_clean}
                ).first()
                if result:
                    logger.info(f"[Repository] Exact match: {result[0]}")
                    return result[0]
                # ILIKE match
                result = conn.execute(
                    text("""
                        SELECT TRIM(customer_name)
                        FROM delivery_reports
                        WHERE TRIM(customer_name) ILIKE TRIM(:pattern)
                        LIMIT 1
                    """),
                    {"pattern": f"%{dealer_clean}%"}
                ).first()
                if result:
                    logger.info(f"[Repository] ILIKE match: {result[0]}")
                    return result[0]
                logger.info(f"[Repository] No match for '{dealer_clean}'")
                return None
        except Exception as e:
            logger.error(f"Error resolving dealer: {e}")
            return None

    def get_dealer_data(self, dealer_name: str) -> Optional[Dict[str, Any]]:
        """Fetch aggregated data for a given dealer name."""
        dealer_clean = dealer_name.strip()
        if not dealer_clean:
            return None
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT
                            TRIM(MAX(customer_name)) AS customer_name,
                            TRIM(MAX(dealer_code)) AS dealer_code,
                            TRIM(MAX(customer_code)) AS customer_code,
                            TRIM(MAX(ship_to_city)) AS ship_to_city,
                            TRIM(MAX(warehouse)) AS warehouse,
                            TRIM(MAX(sales_office)) AS sales_office,
                            TRIM(MAX(sales_manager)) AS sales_manager,
                            TRIM(MAX(division)) AS division,
                            COUNT(DISTINCT dn_no) AS dn_count,
                            SUM(dn_qty) AS total_units,
                            SUM(dn_amount) AS total_revenue,
                            MIN(dn_create_date) AS first_sale,
                            MAX(dn_create_date) AS last_sale,
                            AVG(dn_amount) AS avg_dn_value,
                            COUNT(DISTINCT CASE WHEN pod_date IS NULL THEN dn_no END) AS pending_dn,
                            COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) AS pgi_pending_dn,
                            COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) AS pod_pending_dn,
                            COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) AS pod_completed,
                            COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) AS pgi_completed,
                            AVG(CASE WHEN good_issue_date IS NOT NULL THEN good_issue_date - dn_create_date END) AS avg_delivery_days,
                            AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL THEN pod_date - good_issue_date END) AS avg_pod_days
                        FROM delivery_reports
                        WHERE TRIM(customer_name) = TRIM(:name)
                        GROUP BY customer_name, dealer_code, customer_code
                    """),
                    {"name": dealer_clean}
                ).first()
                if not row or row.total_revenue is None:
                    return None
                data = {
                    'customer_name': _text(row[0]),
                    'dealer_code': _text(row[1]),
                    'customer_code': _text(row[2]),
                    'city': _text(row[3]),
                    'warehouse': _text(row[4]),
                    'sales_office': _text(row[5]),
                    'sales_manager': _text(row[6]),
                    'division': _text(row[7]),
                    'dn_count': int(row[8] or 0),
                    'total_units': int(row[9] or 0),
                    'total_revenue': float(row[10] or 0.0),
                    'first_sale': _text(row[11]),
                    'last_sale': _text(row[12]),
                    'avg_dn_value': float(row[13] or 0.0),
                    'pending_dn': int(row[14] or 0),
                    'pgi_pending_dn': int(row[15] or 0),
                    'pod_pending_dn': int(row[16] or 0),
                    'pod_completed': int(row[17] or 0),
                    'pgi_completed': int(row[18] or 0),
                    'avg_delivery_days': float(row[19] or 0.0),
                    'avg_pod_days': float(row[20] or 0.0),
                }
                data['delivery_rate'] = _percent(data['pod_completed'], data['dn_count'])
                data['pod_achievement'] = _percent(data['pod_completed'], data['dn_count'])
                data['pgi_achievement'] = _percent(data['pgi_completed'], data['dn_count'])
                data['pending_pct'] = _percent(data['pending_dn'], data['dn_count'])

                # Distance
                city = data['city']
                warehouse = data['warehouse']
                if city and warehouse and city != 'Unknown' and warehouse != 'Unknown':
                    try:
                        dist, time = _get_road_distance_between_cities(warehouse, city)
                        data['distance_km'] = dist
                        data['distance_time'] = time
                    except Exception:
                        data['distance_km'] = None
                        data['distance_time'] = "Not Available"
                else:
                    data['distance_km'] = None
                    data['distance_time'] = "Not Available"

                data['rating'] = _get_dealer_rating(
                    data['delivery_rate'],
                    data['total_revenue'],
                    data['pending_dn'],
                    data['dn_count']
                )
                return data
        except Exception as e:
            logger.error(f"Error getting dealer data: {e}")
            return None

# ============================================================
# MAIN SERVICE
# ============================================================

class DealerAnalyticsService:
    def __init__(self) -> None:
        self._version = VERSION
        logger.info(f"✅ DealerAnalyticsService v{self._version} initialized")
        logger.info(f"   Geopy: {'✅' if GEOCODE_AVAILABLE else '❌'}")
        logger.info(f"   OpenRouteService: {'✅' if ORS_AVAILABLE else '❌'}")
        logger.info(f"   Redis: {'✅' if _redis_client else '❌'}")
        logger.info(f"   Cachetools: {'✅' if CACHETOOLS_AVAILABLE else '❌'}")

    def handle_message(self, message: str, sender: str) -> str:
        try:
            msg = message.strip()
            if msg == "99":
                logger.info("[Service] Exit command")
                return "99"
            if msg in ['1','2','3','4','5','6','7','8','9','0']:
                return self._get_help_message()
            if not msg or msg.lower() in ['hi','hello','hey','start']:
                return self._get_welcome_message()
            logger.info(f"[Service] Searching for: '{msg}'")
            result = self._search_dealer(msg)
            return result
        except Exception as e:
            logger.exception("[Service] Error in handle_message")
            return f"⚠️ Error: {str(e)}\n\nPlease try again."

    def _get_welcome_message(self) -> str:
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
• Distance from warehouse

🗺️ **Services:**
• Geocoding: {'✅' if GEOCODE_AVAILABLE else '⚠️ Fallback'}
• Route: {'✅' if ORS_AVAILABLE else '⚠️ Fallback'}
• Cache: {'✅' if _redis_client else '⚠️ Memory'}

💡 Type **99** to return to main menu.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a dealer name to get started!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    def _get_help_message(self) -> str:
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
• Type **99** to return to the Main Menu

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a dealer name to get started!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    def _search_dealer(self, query: str) -> str:
        repo = DealerRepository(self._session())
        dealer_name = repo.resolve_dealer(query)
        if dealer_name:
            data = repo.get_dealer_data(dealer_name)
            if data:
                return self._render_dashboard(data)
        suggestions = self._get_suggestions(query)
        if suggestions:
            return self._format_suggestions(query, suggestions)
        return f"🔍 No dealer found matching '{query}'\n\n💡 Try a partial name or check spelling."

    def _get_suggestions(self, query: str, limit: int = 5) -> List[str]:
        if not query:
            return []
        try:
            with engine.connect() as conn:
                results = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(customer_name)
                        FROM delivery_reports
                        WHERE TRIM(customer_name) ILIKE TRIM(:pattern)
                        ORDER BY customer_name
                        LIMIT :limit
                    """),
                    {"pattern": f"%{query}%", "limit": limit}
                ).fetchall()
                return [r[0] for r in results if r[0]]
        except Exception as e:
            logger.error(f"Error getting suggestions: {e}")
            return []

    def _format_suggestions(self, query: str, suggestions: List[str]) -> str:
        lines = [f"🔍 No exact match for '{query}'", "", "💡 Did you mean:", ""]
        for i, s in enumerate(suggestions[:5], 1):
            lines.append(f"{i}. {s}")
        lines.extend(["", f"Type the exact name or try: {query}", "", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "Type a dealer name to search"])
        return "\n".join(lines)

    def _render_dashboard(self, data: Dict[str, Any]) -> str:
        customer_name = data.get('customer_name', 'Unknown')
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

        # Clean name
        clean_name = re.sub(r'0[0-9]{2,4}[-.\s]?[0-9]{7,8}', '', customer_name)
        clean_name = re.sub(r'C/O\s*', '', clean_name, flags=re.IGNORECASE)
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()

        distance_display = f"{distance_km} KM ({distance_time})" if distance_km and distance_km > 0 else "Not Available"

        # Top models (simplified)
        top_models = self._get_top_models(customer_name)
        best_month = self._get_highest_sales_month(customer_name)
        revenue_trend = self._get_revenue_trend(customer_name)

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
        if top_models:
            for i, (model, count) in enumerate(top_models):
                model_display = model.strip() if model else "N/A"
                count_display = f"{count} Unit{'s' if count > 1 else ''}"
                padding = " " * (20 - len(model_display)) if len(model_display) < 20 else " "
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
        recommendations = self._get_ai_recommendations(data, top_models)
        for rec in recommendations:
            lines.append(f"✅ {rec}")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "99 to Return to Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Type a dealer name to search",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        return "\n".join(lines)

    def _get_top_models(self, dealer_name: str, limit: int = 7) -> List[Tuple[str, int]]:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT customer_model, COUNT(dn_no) AS count
                        FROM delivery_reports
                        WHERE TRIM(customer_name) = TRIM(:name)
                        AND customer_model IS NOT NULL AND TRIM(customer_model) != ''
                        GROUP BY customer_model
                        ORDER BY count DESC
                        LIMIT :limit
                    """),
                    {"name": dealer_name, "limit": limit}
                ).fetchall()
                return [(r[0], int(r[1])) for r in rows if r[0]]
        except Exception as e:
            logger.error(f"Error getting top models: {e}")
            return []

    def _get_highest_sales_month(self, dealer_name: str) -> str:
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT TO_CHAR(dn_create_date, 'Month') AS month
                        FROM delivery_reports
                        WHERE TRIM(customer_name) = TRIM(:name)
                        GROUP BY TO_CHAR(dn_create_date, 'Month'), EXTRACT(MONTH FROM dn_create_date)
                        ORDER BY SUM(dn_amount) DESC
                        LIMIT 1
                    """),
                    {"name": dealer_name}
                ).first()
                return row[0].strip() if row else "N/A"
        except Exception:
            return "N/A"

    def _get_revenue_trend(self, dealer_name: str) -> str:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT SUM(dn_amount) AS revenue
                        FROM delivery_reports
                        WHERE TRIM(customer_name) = TRIM(:name)
                        AND dn_create_date >= CURRENT_DATE - INTERVAL '3 months'
                        GROUP BY EXTRACT(MONTH FROM dn_create_date)
                        ORDER BY EXTRACT(MONTH FROM dn_create_date) DESC
                        LIMIT 2
                    """),
                    {"name": dealer_name}
                ).fetchall()
                if len(rows) >= 2:
                    current = float(rows[0][0] or 0)
                    previous = float(rows[1][0] or 0)
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
        except Exception:
            return "Stable →"

    def _get_delivery_performance(self, rate: float) -> str:
        if rate >= 95:
            return "Excellent"
        elif rate >= 85:
            return "Good"
        elif rate >= 70:
            return "Average"
        else:
            return "Needs Improvement"

    def _get_ai_recommendations(self, data: Dict[str, Any], top_models: List[Tuple[str, int]]) -> List[str]:
        recs = []
        if top_models:
            recs.append(f"Maintain stock of {top_models[0][0]}.")
        pending = data.get('pending_dn', 0)
        if pending > 0:
            recs.append(f"🚚 Prioritize dispatch of {pending} pending DNs.")
        else:
            recs.append("All deliveries completed. Excellent efficiency!")
        if data.get('pod_achievement', 0) < 90:
            recs.append("📄 Improve POD compliance through timely document submission.")
        if data.get('rating', 'C') in ['C+', 'C']:
            recs.append("⚡ Improve PGI & POD to achieve an 'A' dealer rating.")
        if len(top_models) > 1:
            recs.append("📈 Increase focus on AC and Refrigerator models to diversify sales.")
        warehouse = data.get('warehouse', '')
        if warehouse and warehouse != 'Unknown':
            recs.append(f"🎯 Continue dispatches from {warehouse} for faster deliveries.")
        return recs[:6]

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

# ============================================================
# SINGLETON
# ============================================================

_dealer_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    global _dealer_service
    if _dealer_service is None:
        try:
            logger.info("🔧 Creating DealerAnalyticsService instance...")
            _dealer_service = DealerAnalyticsService()
            logger.info("✅ DealerAnalyticsService instance created successfully")
        except Exception as e:
            logger.error(f"❌ Failed to create DealerAnalyticsService: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Return a minimal working instance even if init fails
            _dealer_service = DealerAnalyticsService()
    return _dealer_service

__all__ = [
    "DealerAnalyticsService",
    "get_dealer_service",
    "VERSION"
]
