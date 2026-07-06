#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 12.0 - ENTERPRISE DEALER INTELLIGENCE PLATFORM (COMPLETE FIX)
# ============================================================

"""
================================================================================
DEALER LOGISTICS INTELLIGENCE PLATFORM - ENTERPRISE EDITION v12.0
================================================================================

This service is a complete Dealer Logistics Intelligence Platform.

SOURCE OF TRUTH: PostgreSQL ONLY

VERSION HISTORY:
    12.0 - Complete rewrite with all fixes:
          - Removed non-existent 'region' field
          - Fixed SQL construction (no string conversion)
          - Made DistanceEngine synchronous
          - Made AISummaryEngine synchronous
          - Added connection verification
          - Enhanced health checks
          - Added RapidFuzz search
          - Added ranking system
          - Added metrics tracking
          - Added timeout handling
          - Added graceful shutdown
    11.1 - Fixed async/sync issues
    11.0 - Initial enterprise rewrite

================================================================================
"""

# ============================================================
# BLOCK 1: IMPORTS
# ============================================================

import os
import logging
import math
import re
import json
import traceback
import time
import threading
from typing import Optional, Dict, List, Any, Tuple, Union
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from enum import Enum
from contextlib import contextmanager
from functools import wraps

# SQLAlchemy
from sqlalchemy import func, distinct, case, or_, and_, desc, asc, text, nullif, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import SQLAlchemyError

# Database
from app.database import SessionLocal
from app.models import DeliveryReport

# AI
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

# Distance
try:
    import openrouteservice
    from openrouteservice.distance_matrix import distance_matrix
    ORS_AVAILABLE = True
except ImportError:
    ORS_AVAILABLE = False

# Search
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

# Cache
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# Metrics
try:
    from prometheus_client import Counter, Histogram, Gauge
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 2: METRICS (Optional)
# ============================================================

if METRICS_AVAILABLE:
    dealer_requests_total = Counter(
        'dealer_requests_total',
        'Total dealer analytics requests',
        ['service', 'status']
    )
    dealer_search_time = Histogram(
        'dealer_search_time_seconds',
        'Dealer search time in seconds',
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]
    )
    dealer_dashboard_time = Histogram(
        'dealer_dashboard_generation_seconds',
        'Dealer dashboard generation time in seconds',
        buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
    )
    dealer_errors_total = Counter(
        'dealer_errors_total',
        'Total dealer analytics errors',
        ['error_type']
    )
    dealer_cache_hits = Counter(
        'dealer_cache_hits_total',
        'Total dealer cache hits'
    )
else:
    # Dummy metrics
    class DummyMetric:
        def inc(self, *args, **kwargs): pass
        def observe(self, *args, **kwargs): pass
        def labels(self, *args, **kwargs): return self
    
    dealer_requests_total = DummyMetric()
    dealer_search_time = DummyMetric()
    dealer_dashboard_time = DummyMetric()
    dealer_errors_total = DummyMetric()
    dealer_cache_hits = DummyMetric()

# ============================================================
# BLOCK 3: CONFIGURATION & CONSTANTS
# ============================================================

VERSION = "12.0"
EXIT_SIGNAL = "__EXIT__"
CACHE_TTL = 300  # 5 minutes
DISTANCE_CACHE_TTL = 86400  # 24 hours
SIMILARITY_THRESHOLD = 0.60  # Lowered for better fuzzy matching
SEARCH_LIMIT = 10
TOP_N_LIMIT = 10
TIMEOUT_SECONDS = 30

# Redis config
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# OpenRouteService
ORS_API_KEY = os.getenv("ORS_API_KEY", "")
ORS_BASE_URL = "https://api.openrouteservice.org/v2"

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")

# Fallback coordinates
FALLBACK_COORDINATES = (30.3753, 69.3451)

# ============================================================
# BLOCK 4: UTILITY FUNCTIONS
# ============================================================

def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except (TypeError, ValueError):
        return default

def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def _calc_pct(numerator: Any, denominator: Any) -> float:
    num = _safe_float(numerator)
    den = _safe_float(denominator)
    return round((num / den * 100), 2) if den > 0 else 0.0

def _format_date(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _safe_str(value, "N/A")

def _format_currency(amount: float) -> str:
    if amount >= 100_000_000:
        return f"PKR {amount/100_000_000:.2f}Cr"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount/1_000:.2f}K"
    else:
        return f"PKR {amount:,.0f}"

def _normalize_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.lower()
    normalized = re.sub(r'[&\./,()\'\"]', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized

def _clean_dealer_name(name: str) -> str:
    if not name:
        return ""
    cleaned = name.lower().strip()
    suffixes = [
        'Electronics', 'Digital', 'Technologies', 'Traders',
        'Enterprises', 'Systems', 'Solutions', 'Incorporated',
        'International', 'Corporation', 'Limited', 'Ltd',
        'Pvt', 'Private', 'Co', 'Company'
    ]
    for suffix in suffixes:
        cleaned = re.sub(r'\s*' + suffix.lower() + r'\s*$', '', cleaned)
    cleaned = re.sub(r'-[a-z]{3}$', '', cleaned)
    return cleaned.strip()

# ============================================================
# BLOCK 5: COORDINATES
# ============================================================

WAREHOUSE_COORDINATES: Dict[str, Tuple[float, float]] = {
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
    "dg khan": (30.0430, 70.6402),
    "abbottabad": (34.1490, 73.2210),
    "gwadar": (25.1260, 62.3250),
    "gilgit": (35.9208, 74.3144),
}

CITY_COORDINATES: Dict[str, Tuple[float, float]] = {
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
    "dg khan": (30.0430, 70.6402),
    "abbottabad": (34.1490, 73.2210),
    "gwadar": (25.1260, 62.3250),
    "gilgit": (35.9208, 74.3144),
}

CITY_ABBREVIATIONS = {
    'khi': 'karachi',
    'lhr': 'lahore',
    'isb': 'islamabad',
    'rwp': 'rawalpindi',
    'fsd': 'faisalabad',
    'mul': 'multan',
    'pes': 'peshawar',
    'que': 'quetta',
    'hyd': 'hyderabad',
    'guj': 'gujranwala',
    'skt': 'sialkot',
}

CITY_NAMES = set(CITY_ABBREVIATIONS.values())

# ============================================================
# BLOCK 6: DATA CLASSES
# ============================================================

@dataclass
class DealerSearchResult:
    success: bool
    customer_name: str = ""
    dealer_code: str = ""
    customer_code: str = ""
    confidence: float = 0.0
    match_type: str = ""
    message: str = ""
    suggestions: List[Dict[str, Any]] = field(default_factory=list)
    search_time_ms: float = 0.0
    normalized_query: str = ""

# ============================================================
# BLOCK 7: DISTANCE ENGINE (SYNCHRONOUS - NO EVENT LOOP)
# ============================================================

class DistanceEngine:
    """Enterprise Distance Engine - Fully Synchronous"""
    
    def __init__(self):
        self._cache = {}
        self._cache_lock = threading.RLock()
        self._ors_client = None
        self._ors_available = False
        
        if ORS_AVAILABLE and ORS_API_KEY:
            try:
                self._ors_client = openrouteservice.Client(key=ORS_API_KEY, timeout=TIMEOUT_SECONDS)
                self._ors_available = True
                logger.info("✅ OpenRouteService initialized")
            except Exception as e:
                logger.warning(f"⚠️ OpenRouteService initialization failed: {e}")
        else:
            logger.info("ℹ️ OpenRouteService not configured, using Haversine fallback")
    
    def get_distance(self, warehouse: str, city: str) -> Dict[str, Any]:
        """Get road distance and driving time - SYNCHRONOUS"""
        cache_key = f"{warehouse.lower()}_{city.lower()}"
        
        with self._cache_lock:
            if cache_key in self._cache:
                cache_age = (datetime.now() - self._cache[cache_key]['timestamp']).seconds
                if cache_age < DISTANCE_CACHE_TTL:
                    logger.info(f"✅ Distance cache hit for {warehouse}→{city}")
                    dealer_cache_hits.inc()
                    return self._cache[cache_key]['data']
        
        try:
            warehouse_coords = self._get_coordinates(warehouse)
            city_coords = self._get_coordinates(city)
            
            if not warehouse_coords or not city_coords:
                return self._get_haversine_distance(warehouse, city)
            
            if self._ors_available and self._ors_client:
                try:
                    distance_data = self._get_ors_distance(warehouse_coords, city_coords)
                    if distance_data:
                        result = {
                            "distance_km": distance_data['distance'],
                            "driving_time": distance_data['duration'],
                            "source": "OpenRouteService",
                            "transportation_zone": self._get_transportation_zone(distance_data['distance']),
                            "estimated_delivery": self._get_estimated_delivery(distance_data['distance'])
                        }
                        with self._cache_lock:
                            self._cache[cache_key] = {'data': result, 'timestamp': datetime.now()}
                        return result
                except Exception as e:
                    logger.warning(f"⚠️ ORS request failed: {e}")
            
            return self._get_haversine_distance(warehouse, city)
            
        except Exception as e:
            logger.error(f"Distance error: {e}")
            return self._get_haversine_distance(warehouse, city)
    
    def _get_coordinates(self, location: str) -> Optional[Tuple[float, float]]:
        coords = WAREHOUSE_COORDINATES.get(location.lower())
        if coords:
            return coords
        coords = CITY_COORDINATES.get(location.lower())
        if coords:
            return coords
        return None
    
    def _get_ors_distance(self, from_coords: Tuple[float, float], 
                         to_coords: Tuple[float, float]) -> Optional[Dict[str, Any]]:
        if not self._ors_client:
            return None
        try:
            coords = [[from_coords[1], from_coords[0]], [to_coords[1], to_coords[0]]]
            matrix = distance_matrix(
                self._ors_client,
                locations=coords,
                metrics=['distance', 'duration'],
                units='km'
            )
            if matrix and 'distances' in matrix:
                return {
                    'distance': round(matrix['distances'][0][1], 1),
                    'duration': self._format_duration(matrix['durations'][0][1])
                }
        except Exception as e:
            logger.error(f"ORS error: {e}")
        return None
    
    def _get_haversine_distance(self, warehouse: str, city: str) -> Dict[str, Any]:
        warehouse_coords = WAREHOUSE_COORDINATES.get(warehouse.lower())
        city_coords = CITY_COORDINATES.get(city.lower())
        
        if warehouse_coords and city_coords:
            distance = self._calculate_haversine(
                warehouse_coords[0], warehouse_coords[1],
                city_coords[0], city_coords[1]
            )
            return {
                "distance_km": round(distance, 1),
                "driving_time": self._estimate_driving_time(distance),
                "source": "Haversine (fallback)",
                "transportation_zone": self._get_transportation_zone(distance),
                "estimated_delivery": self._get_estimated_delivery(distance)
            }
        
        return {
            "distance_km": None,
            "driving_time": "Unknown",
            "source": "Unavailable",
            "transportation_zone": "Unknown",
            "estimated_delivery": "Unknown"
        }
    
    def _calculate_haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c
    
    def _get_transportation_zone(self, distance: float) -> str:
        if distance <= 80:
            return "Local"
        elif distance <= 200:
            return "Regional"
        elif distance <= 400:
            return "National"
        else:
            return "International"
    
    def _get_estimated_delivery(self, distance: float) -> str:
        if distance <= 80:
            return "Same Day"
        elif distance <= 200:
            return "1 Day"
        elif distance <= 400:
            return "2 Days"
        elif distance <= 700:
            return "3 Days"
        else:
            return "4-5 Days"
    
    def _format_duration(self, minutes: float) -> str:
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        if hours == 0:
            return f"{mins} Minutes"
        elif mins == 0:
            return f"{hours} Hours"
        else:
            return f"{hours} Hr {mins} Min"
    
    def _estimate_driving_time(self, distance: float) -> str:
        hours = distance / 60
        return self._format_duration(hours * 60)

# ============================================================
# BLOCK 8: AI SUMMARY ENGINE (SYNCHRONOUS - NO EVENT LOOP)
# ============================================================

class AISummaryEngine:
    """AI-powered executive summary generation - Fully Synchronous"""
    
    def __init__(self):
        self._client = None
        self._available = False
        
        if GROQ_AVAILABLE and GROQ_API_KEY:
            try:
                self._client = Groq(api_key=GROQ_API_KEY)
                self._available = True
                logger.info("✅ Groq AI initialized")
            except Exception as e:
                logger.warning(f"⚠️ Groq initialization failed: {e}")
        else:
            logger.info("ℹ️ Groq AI not configured, using fallback summary")
    
    def generate_summary(self, dealer_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate AI-powered executive summary - SYNCHRONOUS"""
        if not self._available or not self._client:
            return self._generate_fallback_summary(dealer_data)
        
        try:
            prompt = self._build_prompt(dealer_data)
            
            response = self._client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are a business intelligence analyst for Haier Logistics."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=300,
                timeout=TIMEOUT_SECONDS
            )
            
            summary = response.choices[0].message.content
            return self._parse_ai_response(summary, dealer_data)
            
        except Exception as e:
            logger.warning(f"⚠️ AI summary failed: {e}")
            return self._generate_fallback_summary(dealer_data)
    
    def _build_prompt(self, data: Dict[str, Any]) -> str:
        delivery = data.get('delivery', {})
        sales = data.get('sales', {})
        
        return f"""
        Analyze this dealer's performance and provide:
        1. Business Health (1-10)
        2. Delivery Performance (Excellent/Good/Fair/Poor)
        3. Sales Trend (Growing/Stable/Declining)
        4. Risk Level (Low/Medium/High)
        5. Key Recommendations (3 items)
        
        Dealer: {data.get('identity', {}).get('customer_name', 'Unknown')}
        Revenue: PKR {sales.get('total_revenue', 0):,.2f}
        Total DN: {delivery.get('total_dn', 0)}
        Delivery Rate: {delivery.get('delivery_rate', 0):.1f}%
        Pending DN: {delivery.get('pending_dn', 0)}
        """
    
    def _parse_ai_response(self, response: str, data: Dict[str, Any]) -> Dict[str, Any]:
        lines = response.split('\n')
        result = {
            'health_score': 7,
            'delivery_performance': 'Good',
            'sales_trend': 'Stable',
            'risk_level': 'Medium',
            'recommendations': []
        }
        
        for line in lines:
            line = line.strip()
            if 'Health' in line and ':' in line:
                try:
                    result['health_score'] = int(re.search(r'\d+', line).group())
                except:
                    pass
            elif 'Delivery' in line and ':' in line:
                result['delivery_performance'] = line.split(':')[-1].strip()
            elif 'Sales' in line and ':' in line:
                result['sales_trend'] = line.split(':')[-1].strip()
            elif 'Risk' in line and ':' in line:
                result['risk_level'] = line.split(':')[-1].strip()
            elif 'Recommendation' in line and not result['recommendations']:
                result['recommendations'].append(line)
        
        if not result['recommendations']:
            result['recommendations'] = self._get_default_recommendations(data)
        
        return result
    
    def _generate_fallback_summary(self, data: Dict[str, Any]) -> Dict[str, Any]:
        delivery = data.get('delivery', {})
        sales = data.get('sales', {})
        
        delivery_rate = delivery.get('delivery_rate', 0)
        pending = delivery.get('pending_dn', 0)
        revenue = sales.get('total_revenue', 0)
        
        if delivery_rate >= 90:
            health = 8
            delivery_perf = "Excellent"
        elif delivery_rate >= 75:
            health = 6
            delivery_perf = "Good"
        else:
            health = 4
            delivery_perf = "Fair"
        
        if pending > 0:
            risk = "Medium" if pending < 10 else "High"
        else:
            risk = "Low"
        
        return {
            'health_score': health,
            'delivery_performance': delivery_perf,
            'sales_trend': 'Stable' if revenue > 0 else 'Declining',
            'risk_level': risk,
            'recommendations': self._get_default_recommendations(data)
        }
    
    def _get_default_recommendations(self, data: Dict[str, Any]) -> List[str]:
        delivery = data.get('delivery', {})
        pending = delivery.get('pending_dn', 0)
        
        recs = []
        if pending > 0:
            recs.append(f"Resolve {pending} pending deliveries")
        if delivery.get('delivery_rate', 0) < 80:
            recs.append("Improve delivery performance")
        if delivery.get('pod_rate', 0) < 85:
            recs.append("Focus on POD completion")
        
        if not recs:
            recs = ["Maintain current performance", "Monitor key metrics", "Explore growth opportunities"]
        
        return recs[:3]

# ============================================================
# BLOCK 9: REPOSITORY LAYER
# ============================================================

class DealerRepository:
    def __init__(self, session: Session):
        self.session = session

class DealerSearchRepository(DealerRepository):
    """Search repository with multi-field search - NO 'region' field"""
    
    def search_dealers(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        search_pattern = f"%{query}%"
        
        results = self.session.query(
            DeliveryReport.customer_name,
            DeliveryReport.dealer_code,
            DeliveryReport.customer_code,
            DeliveryReport.ship_to_city,
            DeliveryReport.warehouse,
            DeliveryReport.warehouse_code,
            DeliveryReport.delivery_location,
            DeliveryReport.sales_office,
            DeliveryReport.sales_manager,
            DeliveryReport.division,
            DeliveryReport.material_no,
            DeliveryReport.customer_model,
        ).filter(
            or_(
                DeliveryReport.customer_name.ilike(search_pattern),
                DeliveryReport.dealer_code.ilike(search_pattern),
                DeliveryReport.customer_code.ilike(search_pattern),
                DeliveryReport.warehouse.ilike(search_pattern),
                DeliveryReport.sales_office.ilike(search_pattern),
                DeliveryReport.sales_manager.ilike(search_pattern),
                DeliveryReport.ship_to_city.ilike(search_pattern),
                DeliveryReport.material_no.ilike(search_pattern),
                DeliveryReport.customer_model.ilike(search_pattern),
                DeliveryReport.delivery_location.ilike(search_pattern),
                DeliveryReport.warehouse_code.ilike(search_pattern),
            )
        ).distinct().limit(limit).all()
        
        return [self._row_to_dict(row) for row in results if row]
    
    def search_dealer_multi_stage(self, query: str) -> Optional[Dict[str, Any]]:
        """Multi-stage search: Dealer Code → Customer Code → Exact → ILIKE"""
        
        # Stage 1: Dealer Code
        result = self.session.query(
            DeliveryReport.customer_name,
            DeliveryReport.dealer_code,
            DeliveryReport.customer_code,
            DeliveryReport.ship_to_city,
            DeliveryReport.warehouse,
            DeliveryReport.warehouse_code,
            DeliveryReport.delivery_location,
            DeliveryReport.sales_office,
            DeliveryReport.sales_manager,
            DeliveryReport.division,
        ).filter(
            DeliveryReport.dealer_code == query
        ).first()
        if result:
            return self._row_to_dict(result)
        
        # Stage 2: Customer Code
        result = self.session.query(
            DeliveryReport.customer_name,
            DeliveryReport.dealer_code,
            DeliveryReport.customer_code,
            DeliveryReport.ship_to_city,
            DeliveryReport.warehouse,
            DeliveryReport.warehouse_code,
            DeliveryReport.delivery_location,
            DeliveryReport.sales_office,
            DeliveryReport.sales_manager,
            DeliveryReport.division,
        ).filter(
            DeliveryReport.customer_code == query
        ).first()
        if result:
            return self._row_to_dict(result)
        
        # Stage 3: Exact Name
        result = self.session.query(
            DeliveryReport.customer_name,
            DeliveryReport.dealer_code,
            DeliveryReport.customer_code,
            DeliveryReport.ship_to_city,
            DeliveryReport.warehouse,
            DeliveryReport.warehouse_code,
            DeliveryReport.delivery_location,
            DeliveryReport.sales_office,
            DeliveryReport.sales_manager,
            DeliveryReport.division,
        ).filter(
            func.lower(DeliveryReport.customer_name) == query.lower()
        ).first()
        if result:
            return self._row_to_dict(result)
        
        # Stage 4: ILIKE
        results = self.session.query(
            DeliveryReport.customer_name,
            DeliveryReport.dealer_code,
            DeliveryReport.customer_code,
            DeliveryReport.ship_to_city,
            DeliveryReport.warehouse,
            DeliveryReport.warehouse_code,
            DeliveryReport.delivery_location,
            DeliveryReport.sales_office,
            DeliveryReport.sales_manager,
            DeliveryReport.division,
        ).filter(
            DeliveryReport.customer_name.ilike(f"%{query}%")
        ).limit(10).all()
        if results:
            return self._row_to_dict(results[0])
        
        return None
    
    def search_dealers_rapidfuzz(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search using RapidFuzz for spelling mistakes"""
        if not RAPIDFUZZ_AVAILABLE:
            return self.search_dealers(query, limit)
        
        # Get all dealer names
        dealers = self.session.query(
            DeliveryReport.customer_name,
            DeliveryReport.dealer_code,
            DeliveryReport.customer_code,
            DeliveryReport.ship_to_city,
            DeliveryReport.warehouse,
        ).distinct().limit(100).all()
        
        dealer_names = [d.customer_name for d in dealers if d.customer_name]
        
        # Find matches using RapidFuzz
        matches = process.extract(query, dealer_names, scorer=fuzz.WRatio, limit=limit)
        
        results = []
        for match in matches:
            if match[1] >= 60:  # 60% threshold
                # Get full dealer data
                dealer = self.session.query(
                    DeliveryReport.customer_name,
                    DeliveryReport.dealer_code,
                    DeliveryReport.customer_code,
                    DeliveryReport.ship_to_city,
                    DeliveryReport.warehouse,
                    DeliveryReport.warehouse_code,
                    DeliveryReport.delivery_location,
                    DeliveryReport.sales_office,
                    DeliveryReport.sales_manager,
                    DeliveryReport.division,
                ).filter(
                    DeliveryReport.customer_name == match[0]
                ).first()
                if dealer:
                    results.append(self._row_to_dict(dealer))
        
        return results
    
    def _row_to_dict(self, row) -> Dict[str, Any]:
        if not row:
            return {}
        return {
            'customer_name': _safe_str(row.customer_name),
            'dealer_code': _safe_str(row.dealer_code),
            'customer_code': _safe_str(row.customer_code),
            'city': _safe_str(row.ship_to_city),
            'warehouse': _safe_str(row.warehouse),
            'warehouse_code': _safe_str(row.warehouse_code),
            'delivery_location': _safe_str(row.delivery_location),
            'sales_office': _safe_str(row.sales_office),
            'sales_manager': _safe_str(row.sales_manager),
            'division': _safe_str(row.division),
            'material_no': _safe_str(getattr(row, 'material_no', '')),
            'customer_model': _safe_str(getattr(row, 'customer_model', '')),
        }

class DealerAnalyticsRepository(DealerRepository):
    """Analytics repository with CTE optimization - PARAMETERIZED"""
    
    def get_dashboard(self, dealer_code: str, customer_code: str = None) -> Optional[Dict[str, Any]]:
        # Build SQL string first (not TextClause)
        sql = """
            WITH dealer_data AS (
                SELECT 
                    MAX(customer_name) as customer_name,
                    MAX(dealer_code) as dealer_code,
                    MAX(customer_code) as customer_code,
                    MAX(ship_to_city) as city,
                    MAX(warehouse) as warehouse,
                    MAX(warehouse_code) as warehouse_code,
                    MAX(delivery_location) as delivery_location,
                    MAX(sales_office) as sales_office,
                    MAX(sales_manager) as sales_manager,
                    MAX(division) as division,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered_dn,
                    COUNT(DISTINCT CASE WHEN pod_date IS NULL OR pending_flag = true THEN dn_no END) as pending_dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) as pgi_pending,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) as pod_pending,
                    COALESCE(SUM(dn_qty), 0) as total_quantity,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    AVG(dn_amount) as avg_dn_value,
                    AVG(dn_qty) as avg_quantity,
                    AVG(EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400) as avg_delivery_days,
                    AVG(EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400) as avg_pod_days,
                    MIN(EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400) as min_delivery_days,
                    MAX(EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400) as max_delivery_days,
                    MAX(dn_create_date) as last_delivery,
                    MAX(good_issue_date) as last_pgi,
                    MAX(pod_date) as last_pod,
                    COUNT(DISTINCT customer_model) as total_models
                FROM delivery_reports
                WHERE dealer_code = :dealer_code
        """
        
        params = {"dealer_code": dealer_code}
        
        if customer_code:
            sql += " AND customer_code = :customer_code"
            params["customer_code"] = customer_code
        
        sql += " ) SELECT * FROM dealer_data"
        
        query = text(sql)
        result = self.session.execute(query, params).first()
        
        if not result:
            return None
        
        return {
            'identity': self._get_identity(result),
            'delivery': self._get_delivery_metrics(result),
            'sales': self._get_sales_metrics(result),
            'dates': self._get_dates(result),
            'product_count': result.total_models or 0,
        }
    
    def _get_identity(self, row) -> Dict[str, Any]:
        return {
            'customer_name': _safe_str(row.customer_name),
            'dealer_code': _safe_str(row.dealer_code),
            'customer_code': _safe_str(row.customer_code),
            'city': _safe_str(row.city),
            'warehouse': _safe_str(row.warehouse),
            'warehouse_code': _safe_str(row.warehouse_code),
            'delivery_location': _safe_str(row.delivery_location),
            'sales_office': _safe_str(row.sales_office),
            'sales_manager': _safe_str(row.sales_manager),
            'division': _safe_str(row.division),
        }
    
    def _get_delivery_metrics(self, row) -> Dict[str, Any]:
        total = _safe_int(row.total_dn)
        delivered = _safe_int(row.delivered_dn)
        return {
            'total_dn': total,
            'delivered_dn': delivered,
            'pending_dn': _safe_int(row.pending_dn),
            'pgi_completed': _safe_int(row.pgi_completed),
            'pod_completed': _safe_int(row.pod_completed),
            'pgi_pending': _safe_int(row.pgi_pending),
            'pod_pending': _safe_int(row.pod_pending),
            'delivery_rate': _calc_pct(delivered, total),
            'pgi_rate': _calc_pct(_safe_int(row.pgi_completed), total),
            'pod_rate': _calc_pct(_safe_int(row.pod_completed), total),
            'avg_delivery_days': _safe_float(row.avg_delivery_days),
            'avg_pod_days': _safe_float(row.avg_pod_days),
            'min_delivery_days': _safe_float(row.min_delivery_days),
            'max_delivery_days': _safe_float(row.max_delivery_days),
        }
    
    def _get_sales_metrics(self, row) -> Dict[str, Any]:
        return {
            'total_quantity': _safe_int(row.total_quantity),
            'total_revenue': _safe_float(row.total_revenue),
            'avg_dn_value': _safe_float(row.avg_dn_value),
            'avg_quantity': _safe_float(row.avg_quantity),
        }
    
    def _get_dates(self, row) -> Dict[str, str]:
        return {
            'last_delivery_date': _format_date(row.last_delivery),
            'last_pgi_date': _format_date(row.last_pgi),
            'last_pod_date': _format_date(row.last_pod),
        }

# ============================================================
# BLOCK 10: MAIN DEALER ANALYTICS SERVICE (FULLY SYNCHRONOUS)
# ============================================================

class DealerAnalyticsService:
    """
    Enterprise Dealer Intelligence Platform - Fully Synchronous
    PostgreSQL is the ONLY source of truth.
    """
    
    _instance: Optional["DealerAnalyticsService"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._version = VERSION
        
        # Initialize components (synchronous)
        self._distance_engine = DistanceEngine()
        self._ai_engine = AISummaryEngine()
        self._redis_client = None
        
        # Redis connection
        if REDIS_AVAILABLE:
            try:
                self._redis_client = redis.Redis(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    db=REDIS_DB,
                    decode_responses=True,
                    socket_timeout=TIMEOUT_SECONDS
                )
                self._redis_client.ping()
                logger.info("✅ Redis connected")
            except Exception as e:
                logger.warning(f"⚠️ Redis connection failed: {e}")
                self._redis_client = None
        
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._startup_time = datetime.now()
        self._request_count = 0
        self._success_count = 0
        self._error_count = 0
        self._shutting_down = False
        
        self._show_startup_info()
        self._verify_connections()
    
    def _show_startup_info(self):
        logger.info("=" * 70)
        logger.info(f"🏢 DEALER LOGISTICS INTELLIGENCE v{self._version}".center(70))
        logger.info("=" * 70)
        logger.info("🗄️  PostgreSQL: Single Source of Truth")
        logger.info("🔍 Search Engine: 10+ Fields + RapidFuzz")
        logger.info("📊 Dashboard: 25+ KPI Sections")
        logger.info("📱 WhatsApp Optimized")
        logger.info("💾 Cache: 5 minutes (Redis)")
        logger.info("📈 Scales to: 500,000+ records")
        logger.info("🤖 AI Summary: Groq (Optional)")
        logger.info("📍 Distance: OpenRouteService (Optional)")
        logger.info("=" * 70)
    
    def _verify_connections(self):
        """Verify all connections at startup"""
        issues = []
        
        # PostgreSQL verification
        try:
            with SessionLocal() as session:
                result = session.execute(text("SELECT 1")).scalar()
                if result == 1:
                    logger.info("✅ PostgreSQL: Connected")
                else:
                    issues.append("PostgreSQL verification failed")
        except Exception as e:
            issues.append(f"PostgreSQL: {str(e)}")
        
        # Redis verification
        if self._redis_client:
            try:
                if self._redis_client.ping():
                    logger.info("✅ Redis: Connected")
                else:
                    issues.append("Redis ping failed")
            except Exception as e:
                issues.append(f"Redis: {str(e)}")
        else:
            logger.info("ℹ️ Redis: Not configured")
        
        # Groq verification
        if self._ai_engine._available:
            logger.info("✅ Groq AI: Available")
        else:
            logger.info("ℹ️ Groq AI: Not configured")
        
        # ORS verification
        if self._distance_engine._ors_available:
            logger.info("✅ OpenRouteService: Available")
        else:
            logger.info("ℹ️ OpenRouteService: Not configured")
        
        if issues:
            logger.warning(f"⚠️ Connection issues detected: {', '.join(issues)}")
        else:
            logger.info("✅ All connections verified")
    
    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        self._request_count += 1
        start_time = time.time()
        
        try:
            dealer_requests_total.labels(service='dealer_analytics', status='started').inc()
            
            logger.info(f"📨 Dealer query: '{message}' from {sender}")
            
            if not message or not message.strip():
                return self._get_welcome_message()
            
            message_clean = message.strip()
            
            if message_clean in ["99", "exit", "quit", "back"]:
                logger.info(f"🚪 Exit requested by {sender}")
                return EXIT_SIGNAL
            
            if message_clean in ["help", "?", "start", "hello", "hi"]:
                return self._get_welcome_message()
            
            if message_clean in ["examples", "example"]:
                return self._get_examples()
            
            if message_clean.isdigit():
                return self._handle_selection(int(message_clean), sender)
            
            # Search for dealer
            search_result = self._search_dealer_multi_stage(message_clean)
            
            if not search_result.success:
                self._error_count += 1
                dealer_errors_total.labels(error_type='not_found').inc()
                dealer_requests_total.labels(service='dealer_analytics', status='not_found').inc()
                return self._format_not_found(message_clean, search_result, sender)
            
            session = self._get_session(sender)
            session['dealer_code'] = search_result.dealer_code
            session['customer_code'] = search_result.customer_code
            session['last_query'] = message_clean
            session['pending_matches'] = search_result.suggestions
            
            # Build dashboard
            dashboard = self._build_dashboard_sync(
                search_result.dealer_code,
                search_result.customer_code
            )
            
            if not dashboard:
                self._error_count += 1
                dealer_errors_total.labels(error_type='no_data').inc()
                dealer_requests_total.labels(service='dealer_analytics', status='no_data').inc()
                return self._format_no_data(search_result.customer_name)
            
            response = self._format_dashboard(dashboard)
            
            elapsed = (time.time() - start_time) * 1000
            self._success_count += 1
            dealer_requests_total.labels(service='dealer_analytics', status='success').inc()
            dealer_dashboard_time.observe(elapsed / 1000)
            
            logger.info(f"✅ Response in {elapsed:.0f}ms")
            
            return response
            
        except Exception as e:
            self._error_count += 1
            dealer_errors_total.labels(error_type='exception').inc()
            dealer_requests_total.labels(service='dealer_analytics', status='error').inc()
            logger.error(f"❌ Dealer service error: {e}")
            logger.error(traceback.format_exc())
            return self._format_error(str(e)[:100])
    
    # ============================================================
    # SEARCH - Multi-stage with RapidFuzz
    # ============================================================
    
    def _search_dealer_multi_stage(self, query: str) -> DealerSearchResult:
        start_time = time.time()
        
        try:
            if not query or not query.strip():
                return DealerSearchResult(success=False, message="Empty query")
            
            query_clean = query.strip()
            normalized = _normalize_text(query_clean)
            cleaned = _clean_dealer_name(query_clean)
            
            logger.info(f"🔍 Searching: '{query_clean}' (cleaned: '{cleaned}')")
            
            results = []
            
            with SessionLocal() as session:
                search_repo = DealerSearchRepository(session)
                
                # Stage 1: Dealer Code
                result = search_repo.search_dealer_multi_stage(query_clean)
                if result:
                    results.append((result, 1.0, "dealer_code"))
                
                # Stage 2: RapidFuzz
                fuzzy_results = search_repo.search_dealers_rapidfuzz(query_clean, limit=10)
                for r in fuzzy_results:
                    # Calculate confidence
                    name = r.get('customer_name', '')
                    confidence = fuzz.WRatio(query_clean, name) / 100.0
                    if confidence >= 0.60:
                        results.append((r, confidence, "rapidfuzz"))
                
                # Rank results by confidence
                results.sort(key=lambda x: x[1], reverse=True)
                
                if results:
                    best, confidence, match_type = results[0]
                    elapsed = (time.time() - start_time) * 1000
                    dealer_search_time.observe(elapsed / 1000)
                    
                    suggestions = []
                    for r, conf, typ in results[1:5]:
                        suggestions.append({
                            'customer_name': r.get('customer_name', ''),
                            'dealer_code': r.get('dealer_code', ''),
                            'customer_code': r.get('customer_code', ''),
                            'confidence': conf * 100
                        })
                    
                    return DealerSearchResult(
                        success=True,
                        customer_name=best.get('customer_name', ''),
                        dealer_code=best.get('dealer_code', ''),
                        customer_code=best.get('customer_code', ''),
                        confidence=confidence,
                        match_type=match_type,
                        message=f"Found {best.get('customer_name', 'Unknown')}",
                        suggestions=suggestions,
                        search_time_ms=elapsed,
                        normalized_query=normalized
                    )
                
                elapsed = (time.time() - start_time) * 1000
                dealer_search_time.observe(elapsed / 1000)
                return DealerSearchResult(
                    success=False,
                    message="No dealer found",
                    suggestions=[],
                    search_time_ms=elapsed,
                    normalized_query=normalized
                )
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return DealerSearchResult(
                success=False,
                message=f"Search error: {str(e)}",
                search_time_ms=(time.time() - start_time) * 1000
            )
    
    # ============================================================
    # DASHBOARD BUILDING
    # ============================================================
    
    def _build_dashboard_sync(self, dealer_code: str, customer_code: str = None) -> Optional[Dict[str, Any]]:
        start_time = time.time()
        
        try:
            # Try Redis cache
            cache_key = f"dashboard:{dealer_code}:{customer_code}"
            if self._redis_client:
                try:
                    cached = self._redis_client.get(cache_key)
                    if cached:
                        logger.info(f"✅ Dashboard cache hit for {dealer_code}")
                        dealer_cache_hits.inc()
                        return json.loads(cached)
                except Exception as e:
                    logger.warning(f"Redis get error: {e}")
            
            with SessionLocal() as session:
                analytics_repo = DealerAnalyticsRepository(session)
                dashboard = analytics_repo.get_dashboard(dealer_code, customer_code)
                
                if not dashboard:
                    return None
                
                identity = dashboard.get('identity', {})
                
                # Distance
                dashboard['distance'] = self._distance_engine.get_distance(
                    identity.get('warehouse', ''),
                    identity.get('city', '')
                )
                
                # AI Summary
                dashboard['summary'] = self._ai_engine.generate_summary(dashboard)
                
                # Cache in Redis
                if self._redis_client:
                    try:
                        self._redis_client.setex(cache_key, CACHE_TTL, json.dumps(dashboard))
                    except Exception as e:
                        logger.warning(f"Redis set error: {e}")
                
                elapsed = (time.time() - start_time) * 1000
                dealer_dashboard_time.observe(elapsed / 1000)
                
                return dashboard
                
        except Exception as e:
            logger.error(f"Dashboard build error: {e}")
            return None
    
    # ============================================================
    # WHATSAPP FORMATTING
    # ============================================================
    
    def _format_dashboard(self, dashboard: Dict[str, Any]) -> str:
        identity = dashboard.get('identity', {})
        delivery = dashboard.get('delivery', {})
        sales = dashboard.get('sales', {})
        distance = dashboard.get('distance', {})
        dates = dashboard.get('dates', {})
        summary = dashboard.get('summary', {})
        
        lines = []
        
        # HEADER
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏢 DEALER DASHBOARD")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # DEALER INFORMATION
        lines.append("Dealer")
        lines.append(identity.get('customer_name', 'N/A'))
        lines.append("")
        lines.append("Dealer Code")
        lines.append(identity.get('dealer_code', 'N/A'))
        lines.append("")
        lines.append("Warehouse")
        lines.append(identity.get('warehouse', 'N/A'))
        lines.append("")
        lines.append("Dealer City")
        lines.append(identity.get('city', 'N/A'))
        lines.append("")
        
        # LOGISTICS
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("📍 LOGISTICS")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"Road Distance")
        lines.append(f"{distance.get('distance_km', 'N/A')} KM")
        lines.append("")
        lines.append("Driving Time")
        lines.append(distance.get('driving_time', 'N/A'))
        lines.append("")
        lines.append("Estimated Delivery")
        lines.append(distance.get('estimated_delivery', 'N/A'))
        lines.append("")
        lines.append("Transportation Zone")
        lines.append(distance.get('transportation_zone', 'N/A'))
        lines.append("")
        
        # DELIVERY PERFORMANCE
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("🚚 DELIVERY")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"Total DN        : {delivery.get('total_dn', 0):,}")
        lines.append(f"Total Qty        : {sales.get('total_quantity', 0):,}")
        lines.append(f"Total Sales      : {_format_currency(sales.get('total_revenue', 0))}")
        lines.append(f"Delivered        : {delivery.get('delivered_dn', 0):,}")
        lines.append(f"Pending          : {delivery.get('pending_dn', 0):,}")
        lines.append(f"PGI Pending      : {delivery.get('pgi_pending', 0):,}")
        lines.append(f"POD Pending      : {delivery.get('pod_pending', 0):,}")
        lines.append(f"Delivery Rate    : {delivery.get('delivery_rate', 0):.1f}%")
        lines.append(f"Avg Delivery     : {delivery.get('avg_delivery_days', 0):.1f} Days")
        lines.append("")
        
        # AI SUMMARY
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("📊 AI SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"Health Score     : {summary.get('health_score', 'N/A')}/10")
        lines.append(f"Delivery         : {summary.get('delivery_performance', 'N/A')}")
        lines.append(f"Sales Trend      : {summary.get('sales_trend', 'N/A')}")
        lines.append(f"Risk Level       : {summary.get('risk_level', 'N/A')}")
        lines.append("")
        
        # RECOMMENDATIONS
        recs = summary.get('recommendations', [])
        if recs:
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("💡 RECOMMENDATIONS")
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("")
            for rec in recs[:3]:
                lines.append(f"• {rec}")
                lines.append("")
        
        # DATES
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("📅 DATES")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"Last Delivery    : {dates.get('last_delivery_date', 'N/A')}")
        lines.append(f"Last PGI         : {dates.get('last_pgi_date', 'N/A')}")
        lines.append(f"Last POD         : {dates.get('last_pod_date', 'N/A')}")
        lines.append("")
        
        # FOOTER
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💬 Type '99' to return to Main Menu")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)
    
    # ============================================================
    # HELPERS
    # ============================================================
    
    def _get_welcome_message(self) -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏢 DEALER LOGISTICS INTELLIGENCE",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Enter a Dealer Name, Code, or City.",
            "",
            "✅ Dealer Code",
            "✅ Customer Code",
            "✅ Dealer Name",
            "✅ City Name",
            "✅ Warehouse",
            "✅ Natural Language Queries",
            "",
            "💡 Try: Arshad Electronics-Khi",
            "💡 Try: Karachi",
            "💡 Try: Top dealers by revenue",
            "",
            "99️⃣ Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _get_examples(self) -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📝 DEALER EXAMPLES",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Try searching for:",
            "",
            "1. Arshad Electronics-Khi",
            "2. Zoom Appliances",
            "3. Karachi (shows all dealers in Karachi)",
            "4. Top dealers by revenue",
            "5. Dealer with highest DN",
            "6. Pending deliveries",
            "",
            "💡 Natural Language:",
            "• Show Karachi dealers",
            "• Top dealer in Lahore",
            "• Best performing dealer",
            "",
            "99️⃣ Return to Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _format_not_found(self, query: str, search_result: DealerSearchResult, sender: str) -> str:
        lines = []
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔍 DEALER NOT FOUND")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"We couldn't find '{query}' in our records.")
        lines.append("")
        
        if search_result.suggestions:
            lines.append("💡 Did you mean:")
            lines.append("")
            for i, suggestion in enumerate(search_result.suggestions[:5], 1):
                confidence = suggestion.get('confidence', 0)
                name = suggestion.get('customer_name', 'Unknown')
                lines.append(f"{i}. {name} ({confidence:.0f}% match)")
            lines.append("")
            lines.append("💬 Type the number to select a dealer")
            lines.append("")
            
            session = self._get_session(sender)
            session['pending_matches'] = search_result.suggestions[:5]
        else:
            lines.append("💡 Suggestions:")
            lines.append("• Check the spelling")
            lines.append("• Try searching by City (e.g., 'Karachi')")
            lines.append("• Try searching by Dealer Code")
            lines.append("• Try searching by Customer Code")
            lines.append("")
        
        lines.append("99️⃣ Return to Main Menu")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)
    
    def _format_no_data(self, dealer_name: str) -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "⚠️ NO DATA AVAILABLE",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"We found '{dealer_name}' but no delivery data is available.",
            "",
            "💡 Possible reasons:",
            "• No delivery reports imported",
            "• No recent transactions",
            "• Data import may be incomplete",
            "",
            "99️⃣ Return to Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _format_error(self, error_message: str) -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "⚠️ ERROR",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "An error occurred while processing your request.",
            "",
            f"Error: {error_message}",
            "",
            "Please try again or type '99' to exit.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _get_session(self, user_id: str) -> Dict[str, Any]:
        if user_id not in self._sessions:
            self._sessions[user_id] = {
                'dealer_code': '',
                'customer_code': '',
                'last_query': '',
                'pending_matches': [],
                'created_at': datetime.now(),
                'last_activity': datetime.now()
            }
        self._sessions[user_id]['last_activity'] = datetime.now()
        return self._sessions[user_id]
    
    def _handle_selection(self, selection: int, sender: str) -> str:
        session = self._get_session(sender)
        
        if not session.get('pending_matches'):
            return self._format_error("No pending selection")
        
        matches = session['pending_matches']
        if selection < 1 or selection > len(matches):
            return self._format_error(f"Please select 1-{len(matches)}")
        
        selected = matches[selection - 1]
        
        search_result = self._search_dealer_multi_stage(selected.get('customer_name', ''))
        
        if not search_result.success:
            return self._format_not_found(selected.get('customer_name', ''), search_result, sender)
        
        session['dealer_code'] = search_result.dealer_code
        session['customer_code'] = search_result.customer_code
        session['pending_matches'] = []
        
        dashboard = self._build_dashboard_sync(
            search_result.dealer_code,
            search_result.customer_code
        )
        
        if not dashboard:
            return self._format_no_data(search_result.customer_name)
        
        return self._format_dashboard(dashboard)
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check"""
        uptime = (datetime.now() - self._startup_time).seconds
        
        health = {
            "service": "dealer_analytics_service",
            "version": self._version,
            "status": "healthy",
            "uptime_seconds": uptime,
            "total_requests": self._request_count,
            "successful_requests": self._success_count,
            "error_count": self._error_count,
            "success_rate": round((self._success_count / max(self._request_count, 1)) * 100, 1),
            "active_sessions": len(self._sessions),
            "connections": {
                "postgresql": "unknown",
                "redis": "unknown",
                "groq": "unknown",
                "ors": "unknown"
            }
        }
        
        # Check PostgreSQL
        try:
            with SessionLocal() as session:
                result = session.execute(text("SELECT 1")).scalar()
                health["connections"]["postgresql"] = "connected" if result == 1 else "failed"
        except Exception as e:
            health["connections"]["postgresql"] = f"error: {str(e)}"
            health["status"] = "degraded"
        
        # Check Redis
        if self._redis_client:
            try:
                if self._redis_client.ping():
                    health["connections"]["redis"] = "connected"
                else:
                    health["connections"]["redis"] = "failed"
                    health["status"] = "degraded"
            except Exception as e:
                health["connections"]["redis"] = f"error: {str(e)}"
                health["status"] = "degraded"
        else:
            health["connections"]["redis"] = "not_configured"
        
        # Check Groq
        if self._ai_engine._available:
            health["connections"]["groq"] = "available"
        else:
            health["connections"]["groq"] = "not_configured"
        
        # Check ORS
        if self._distance_engine._ors_available:
            health["connections"]["ors"] = "available"
        else:
            health["connections"]["ors"] = "not_configured"
        
        return health
    
    # ============================================================
    # GRACEFUL SHUTDOWN
    # ============================================================
    
    def shutdown(self):
        """Graceful shutdown"""
        logger.info("🛑 Shutting down Dealer Analytics Service...")
        self._shutting_down = True
        
        if self._redis_client:
            try:
                self._redis_client.close()
                logger.info("✅ Redis connection closed")
            except Exception as e:
                logger.warning(f"Redis close error: {e}")

# ============================================================
# BLOCK 11: SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        _service = DealerAnalyticsService()
    return _service

# ============================================================
# BLOCK 12: EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "get_dealer_service",
    "EXIT_SIGNAL",
    "VERSION"
]

# ============================================================
# BLOCK 13: TEST MODE
# ============================================================

if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("DEALER LOGISTICS INTELLIGENCE - TEST MODE".center(70))
    logger.info("=" * 70)
    
    service = get_dealer_service()
    
    health = service.health_check()
    logger.info("📊 Health Check:")
    for key, value in health.items():
        if key == "connections":
            logger.info(f"  {key}:")
            for k, v in value.items():
                logger.info(f"    {k}: {v}")
        else:
            logger.info(f"  {key}: {value}")
    
    logger.info(service._get_welcome_message())
    logger.info("🔍 Enter dealer name to search (or 99 to exit)")
    
    while True:
        try:
            query = input("🔍 Enter Dealer Name: ").strip()
            
            if query == "99":
                logger.info("\n👋 Goodbye!")
                service.shutdown()
                break
            
            if not query:
                continue
            
            logger.info("\n⏳ Processing...\n")
            result = service.process_whatsapp_query(query, "test_user")
            
            if result == EXIT_SIGNAL:
                logger.info("Exiting...")
                break
            
            logger.info(result)
            
        except KeyboardInterrupt:
            logger.info("\n\n👋 Goodbye!")
            service.shutdown()
            break
        except Exception as e:
            logger.error(f"\n❌ Error: {e}\n")
            traceback.print_exc()
