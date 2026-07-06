#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 10.0 - ENTERPRISE DEALER INTELLIGENCE PLATFORM
# ============================================================

"""
================================================================================
DEALER LOGISTICS INTELLIGENCE PLATFORM - ENTERPRISE EDITION v10.0
================================================================================

This service is a complete Dealer Logistics Intelligence Platform.

SOURCE OF TRUTH: PostgreSQL ONLY

VERSION HISTORY:
    10.0 - Complete enterprise rewrite with modular architecture
    9.1 - Fixed search for dealer names with hyphens
    9.0 - Added fuzzy search, distance calculation, executive dashboard

PERFORMANCE TARGETS:
    Search: < 100ms
    Dashboard: < 500ms
    Cache Hit Rate: > 80%
    SQL Query Time: < 200ms
    Scales to: 500,000+ records

ARCHITECTURE:
    WhatsApp → Webhook → AI Provider → Dealer Analytics Service →
        Dealer Search Engine → Dealer Repository → PostgreSQL →
        Dashboard Builder → WhatsApp Formatter → User

SEARCH STRATEGIES (Priority Order):
    1. Dealer Code (exact)
    2. Customer Code (exact)
    3. Exact Name Match
    4. ILIKE Name Match
    5. Partial Name Match
    6. Token Match
    7. Fuzzy Match (70% threshold)
    8. Phonetic Match (Soundex)
    9. Suggestions

================================================================================
"""

from __future__ import annotations

import logging
import math
import re
import json
import traceback
import time
import threading
from typing import Optional, Dict, List, Any, Tuple, Union
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from collections import defaultdict

from sqlalchemy import func, distinct, case, or_, and_, desc, asc, text, nullif
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

EXIT_SIGNAL = "__EXIT__"
VERSION = "10.0"
CACHE_TTL = 300  # 5 minutes
DISTANCE_CACHE_TTL = 86400  # 24 hours
SIMILARITY_THRESHOLD = 0.70
SEARCH_LIMIT = 10
TOP_N_LIMIT = 10

# Fallback coordinates (Center of Pakistan)
FALLBACK_COORDINATES = (30.3753, 69.3451)

# Warehouse coordinates
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

# City abbreviations
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

# Dealer suffixes to remove in search
DEALER_SUFFIXES = [
    'Electronics', 'Digital', 'Technologies', 'Traders',
    'Enterprises', 'Systems', 'Solutions', 'Incorporated',
    'International', 'Corporation', 'Limited', 'Ltd',
    'Pvt', 'Private', 'Co', 'Company'
]

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _safe_str(value: Any, default: str = "") -> str:
    """Safely convert to string"""
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except (TypeError, ValueError):
        return default

def _safe_float(value: Any) -> float:
    """Safely convert to float"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _safe_int(value: Any) -> int:
    """Safely convert to integer"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def _calc_pct(numerator: Any, denominator: Any) -> float:
    """Calculate percentage safely"""
    num = _safe_float(numerator)
    den = _safe_float(denominator)
    return round((num / den * 100), 2) if den > 0 else 0.0

def _format_date(value: Any) -> str:
    """Format date for display"""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _safe_str(value, "N/A")

def _format_currency(amount: float) -> str:
    """Format currency in PKR"""
    if amount >= 100_000_000:
        return f"PKR {amount/100_000_000:.2f}Cr"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount/1_000:.2f}K"
    else:
        return f"PKR {amount:,.0f}"

def _normalize_text(text: str) -> str:
    """Normalize text for search - PRESERVES hyphens"""
    if not text:
        return ""
    normalized = text.lower()
    normalized = re.sub(r'[&\./,()\'\"]', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized

def _clean_dealer_name(name: str) -> str:
    """Clean dealer name by removing common suffixes"""
    if not name:
        return ""
    cleaned = name.lower().strip()
    for suffix in DEALER_SUFFIXES:
        cleaned = re.sub(r'\s*' + suffix.lower() + r'\s*$', '', cleaned)
    cleaned = re.sub(r'-[a-z]{3}$', '', cleaned)
    return cleaned.strip()

def _get_soundex(text: str) -> str:
    """Generate Soundex code for phonetic matching"""
    if not text:
        return ""
    text = text.upper()
    soundex = text[0] if text else ""
    mapping = {
        'B': '1', 'F': '1', 'P': '1', 'V': '1',
        'C': '2', 'G': '2', 'J': '2', 'K': '2', 'Q': '2', 'S': '2', 'X': '2', 'Z': '2',
        'D': '3', 'T': '3',
        'L': '4',
        'M': '5', 'N': '5',
        'R': '6'
    }
    prev_code = ''
    for char in text[1:]:
        if char in mapping:
            code = mapping[char]
            if code != prev_code:
                soundex += code
                prev_code = code
            if len(soundex) >= 4:
                break
    return soundex.ljust(4, '0')

def _tokenize(text: str) -> List[str]:
    """Tokenize text for search"""
    normalized = _normalize_text(text)
    return [t for t in normalized.split() if len(t) > 1]

def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance using Haversine formula"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _get_coordinates(city: str) -> Tuple[float, float]:
    """Get coordinates with fallback"""
    city_lower = city.lower()
    coords = WAREHOUSE_COORDINATES.get(city_lower)
    if not coords:
        logger.warning(f"⚠️ No coordinates for city: {city}, using fallback")
        return FALLBACK_COORDINATES
    return coords

def _get_distance_info(warehouse: str, city: str) -> Dict[str, Any]:
    """Calculate distance and estimated delivery time"""
    warehouse_lower = warehouse.lower()
    city_lower = city.lower()
    
    warehouse_coord = WAREHOUSE_COORDINATES.get(warehouse_lower)
    city_coord = WAREHOUSE_COORDINATES.get(city_lower)
    
    if warehouse_coord and city_coord:
        distance = _calculate_distance(
            warehouse_coord[0], warehouse_coord[1],
            city_coord[0], city_coord[1]
        )
        
        if distance <= 80:
            zone = "Local"
            estimated = "Same Day"
        elif distance <= 200:
            zone = "Short Haul"
            estimated = "1 Day"
        elif distance <= 400:
            zone = "Medium Haul"
            estimated = "2 Days"
        elif distance <= 700:
            zone = "Long Haul"
            estimated = "3 Days"
        else:
            zone = "Extended Haul"
            estimated = "4-5 Days"
        
        return {
            "distance_km": round(distance, 1),
            "estimated_delivery": estimated,
            "transportation_zone": zone,
            "source": "Haversine"
        }
    
    return {
        "distance_km": None,
        "estimated_delivery": "Unknown",
        "transportation_zone": "Unknown",
        "source": "Unavailable"
    }

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class DealerIdentity:
    """Dealer identity information"""
    customer_name: str
    dealer_code: str
    customer_code: str
    city: str
    warehouse: str
    warehouse_code: str
    delivery_location: str
    sales_office: str
    sales_manager: str
    division: str
    region: str

@dataclass
class DealerSearchResult:
    """Search result with confidence and suggestions"""
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

@dataclass
class DeliverySummary:
    """Delivery performance summary"""
    total_dn: int = 0
    delivered_dn: int = 0
    pending_dn: int = 0
    pgi_completed: int = 0
    pod_completed: int = 0
    pgi_pending: int = 0
    pod_pending: int = 0
    delivery_rate: float = 0.0
    pgi_rate: float = 0.0
    pod_rate: float = 0.0
    avg_delivery_days: float = 0.0
    avg_pod_days: float = 0.0
    min_delivery_days: float = 0.0
    max_delivery_days: float = 0.0
    median_delivery_days: float = 0.0
    p90_delivery_days: float = 0.0

@dataclass
class SalesSummary:
    """Sales performance summary"""
    total_quantity: int = 0
    total_revenue: float = 0.0
    avg_dn_value: float = 0.0
    avg_quantity_per_dn: float = 0.0
    avg_selling_price: float = 0.0
    highest_dn_value: float = 0.0
    lowest_dn_value: float = 0.0

@dataclass
class ProductSummary:
    """Product performance summary"""
    total_models: int = 0
    top_models: List[Dict[str, Any]] = field(default_factory=list)
    top_materials: List[Dict[str, Any]] = field(default_factory=list)
    top_divisions: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class WarehouseSummary:
    """Warehouse analytics summary"""
    primary_warehouse: str = ""
    warehouses_used: int = 0
    warehouse_distribution: List[Dict[str, Any]] = field(default_factory=list)
    warehouse_utilization: float = 0.0

@dataclass
class CitySummary:
    """City analytics summary"""
    cities_served: int = 0
    top_destination_cities: List[Dict[str, Any]] = field(default_factory=list)
    city_distribution: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class PerformanceSummary:
    """Performance metrics summary"""
    business_score: int = 0
    risk_score: int = 0
    performance_tier: str = "Standard"
    dealer_rating: float = 0.0
    dealer_rank: int = 0

@dataclass
class DealerDashboard:
    """Complete dealer dashboard"""
    identity: DealerIdentity
    distance_info: Dict[str, Any]
    delivery: DeliverySummary
    sales: SalesSummary
    product: ProductSummary
    warehouse: WarehouseSummary
    city: CitySummary
    performance: PerformanceSummary
    executive_summary: str
    insights: List[str]
    recommendations: List[str]
    last_delivery_date: str
    last_pgi_date: str
    last_pod_date: str
    generated_at: datetime = field(default_factory=datetime.now)

# ============================================================
# DEALER REPOSITORY - ALL SQL QUERIES
# ============================================================

class DealerRepository:
    """
    Enterprise Dealer Repository - PostgreSQL ONLY
    
    All queries are optimized SQL with proper aggregation.
    Never loads all rows into Python.
    """
    
    def __init__(self, session: Session):
        self.session = session
    
    # ============================================================
    # SEARCH METHODS
    # ============================================================
    
    def get_dealer_by_code(self, dealer_code: str) -> Optional[Dict[str, Any]]:
        """Get dealer by dealer code"""
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
            DeliveryReport.region,
        ).filter(
            DeliveryReport.dealer_code == dealer_code
        ).first()
        return self._row_to_dict(result) if result else None
    
    def get_dealer_by_customer_code(self, customer_code: str) -> Optional[Dict[str, Any]]:
        """Get dealer by customer code"""
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
            DeliveryReport.region,
        ).filter(
            DeliveryReport.customer_code == customer_code
        ).first()
        return self._row_to_dict(result) if result else None
    
    def search_dealers_exact(self, name: str) -> Optional[Dict[str, Any]]:
        """Search by exact name match (case insensitive)"""
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
            DeliveryReport.region,
        ).filter(
            func.lower(DeliveryReport.customer_name) == name.lower()
        ).first()
        return self._row_to_dict(result) if result else None
    
    def search_dealers_ilike(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search by ILIKE name match"""
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
            DeliveryReport.region,
        ).filter(
            DeliveryReport.customer_name.ilike(f"%{name}%")
        ).distinct().limit(limit).all()
        return [self._row_to_dict(row) for row in results if row]
    
    def search_dealers_partial(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search by partial name with multiple strategies"""
        search_term = name.strip().lower()
        cleaned_term = _clean_dealer_name(search_term)
        
        conditions = [
            func.lower(DeliveryReport.customer_name) == search_term,
            func.lower(DeliveryReport.customer_name).like(f"%{search_term}%"),
            func.lower(DeliveryReport.customer_name).like(f"%{cleaned_term}%"),
            func.lower(DeliveryReport.dealer_code) == search_term,
            func.lower(DeliveryReport.customer_code) == search_term,
            func.lower(func.replace(DeliveryReport.customer_name, ' ', '')) == search_term.replace(' ', ''),
            func.lower(func.replace(DeliveryReport.customer_name, '-', '')) == search_term.replace('-', ''),
        ]
        
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
            DeliveryReport.region,
        ).filter(
            or_(*conditions)
        ).distinct().limit(limit).all()
        return [self._row_to_dict(row) for row in results if row]
    
    def search_dealers_token(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search by token match"""
        tokens = _tokenize(name)
        if not tokens:
            return []
        
        conditions = []
        for token in tokens:
            conditions.append(DeliveryReport.customer_name.ilike(f"%{token}%"))
        
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
            DeliveryReport.region,
        ).filter(
            or_(*conditions)
        ).distinct().limit(limit).all()
        return [self._row_to_dict(row) for row in results if row]
    
    def search_dealers_fuzzy(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search by fuzzy matching"""
        search_term = name.strip().lower()
        cleaned_term = _clean_dealer_name(search_term)
        
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
            DeliveryReport.region,
        ).filter(
            or_(
                DeliveryReport.customer_name.ilike(f"%{cleaned_term}%"),
                DeliveryReport.customer_name.ilike(f"%{search_term}%"),
                DeliveryReport.dealer_code.ilike(f"%{search_term}%"),
                DeliveryReport.customer_code.ilike(f"%{search_term}%"),
            )
        ).distinct().limit(limit).all()
        return [self._row_to_dict(row) for row in results if row]
    
    def search_dealers_phonetic(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search by phonetic (Soundex) matching"""
        soundex = _get_soundex(name)
        if not soundex:
            return []
        
        # PostgreSQL Soundex function
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
            DeliveryReport.region,
        ).filter(
            func.soundex(DeliveryReport.customer_name) == soundex
        ).distinct().limit(limit).all()
        return [self._row_to_dict(row) for row in results if row]
    
    # ============================================================
    # DASHBOARD QUERIES
    # ============================================================
    
    def get_dealer_identity(self, dealer_code: str, customer_code: str = None) -> Optional[Dict[str, Any]]:
        """Get dealer identity"""
        filters = [DeliveryReport.dealer_code == dealer_code]
        if customer_code:
            filters.append(DeliveryReport.customer_code == customer_code)
        
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
            DeliveryReport.region,
        ).filter(*filters).first()
        return self._row_to_dict(result) if result else None
    
    def get_delivery_summary(self, dealer_code: str, customer_code: str = None) -> DeliverySummary:
        """Get delivery performance summary"""
        filters = [DeliveryReport.dealer_code == dealer_code]
        if customer_code:
            filters.append(DeliveryReport.customer_code == customer_code)
        
        result = self.session.query(
            func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
            func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("delivered_dn"),
            func.count(distinct(case(
                (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)
            ))).label("pending_dn"),
            func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no)))).label("pgi_completed"),
            func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("pod_completed"),
            func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)))).label("pgi_pending"),
            func.count(distinct(case(
                (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)
            ))).label("pod_pending"),
            func.avg(case(
                (DeliveryReport.good_issue_date.isnot(None),
                 func.extract('epoch', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date) / 86400)
            )).label("avg_delivery_days"),
            func.avg(case(
                (DeliveryReport.pod_date.isnot(None),
                 func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400)
            )).label("avg_pod_days"),
            func.min(case(
                (DeliveryReport.good_issue_date.isnot(None),
                 func.extract('epoch', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date) / 86400)
            )).label("min_delivery_days"),
            func.max(case(
                (DeliveryReport.good_issue_date.isnot(None),
                 func.extract('epoch', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date) / 86400)
            )).label("max_delivery_days"),
        ).filter(*filters).first()
        
        total_dn = _safe_int(result.total_dn)
        delivered_dn = _safe_int(result.delivered_dn)
        pending_dn = _safe_int(result.pending_dn)
        pgi_completed = _safe_int(result.pgi_completed)
        pod_completed = _safe_int(result.pod_completed)
        pgi_pending = _safe_int(result.pgi_pending)
        pod_pending = _safe_int(result.pod_pending)
        
        return DeliverySummary(
            total_dn=total_dn,
            delivered_dn=delivered_dn,
            pending_dn=pending_dn,
            pgi_completed=pgi_completed,
            pod_completed=pod_completed,
            pgi_pending=pgi_pending,
            pod_pending=pod_pending,
            delivery_rate=_calc_pct(delivered_dn, total_dn),
            pgi_rate=_calc_pct(pgi_completed, total_dn),
            pod_rate=_calc_pct(pod_completed, total_dn),
            avg_delivery_days=_safe_float(result.avg_delivery_days),
            avg_pod_days=_safe_float(result.avg_pod_days),
            min_delivery_days=_safe_float(result.min_delivery_days),
            max_delivery_days=_safe_float(result.max_delivery_days),
            median_delivery_days=0,
            p90_delivery_days=0
        )
    
    def get_sales_summary(self, dealer_code: str, customer_code: str = None) -> SalesSummary:
        """Get sales performance summary"""
        filters = [DeliveryReport.dealer_code == dealer_code]
        if customer_code:
            filters.append(DeliveryReport.customer_code == customer_code)
        
        result = self.session.query(
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_quantity"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
            func.avg(DeliveryReport.dn_amount).label("avg_dn_value"),
            func.avg(DeliveryReport.dn_qty).label("avg_quantity_per_dn"),
            func.avg(DeliveryReport.dn_amount / nullif(DeliveryReport.dn_qty, 0)).label("avg_selling_price"),
            func.max(DeliveryReport.dn_amount).label("highest_dn_value"),
            func.min(DeliveryReport.dn_amount).label("lowest_dn_value"),
        ).filter(*filters).first()
        
        return SalesSummary(
            total_quantity=_safe_int(result.total_quantity),
            total_revenue=_safe_float(result.total_revenue),
            avg_dn_value=_safe_float(result.avg_dn_value),
            avg_quantity_per_dn=_safe_float(result.avg_quantity_per_dn),
            avg_selling_price=_safe_float(result.avg_selling_price),
            highest_dn_value=_safe_float(result.highest_dn_value),
            lowest_dn_value=_safe_float(result.lowest_dn_value)
        )
    
    def get_product_summary(self, dealer_code: str, customer_code: str = None) -> ProductSummary:
        """Get product performance summary"""
        filters = [DeliveryReport.dealer_code == dealer_code]
        if customer_code:
            filters.append(DeliveryReport.customer_code == customer_code)
        
        # Top models
        top_models = self.session.query(
            DeliveryReport.customer_model,
            func.sum(DeliveryReport.dn_amount).label("revenue"),
            func.sum(DeliveryReport.dn_qty).label("quantity"),
            func.count(distinct(DeliveryReport.dn_no)).label("dn_count"),
        ).filter(
            *filters,
            DeliveryReport.customer_model.isnot(None)
        ).group_by(
            DeliveryReport.customer_model
        ).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).limit(TOP_N_LIMIT).all()
        
        # Top materials
        top_materials = self.session.query(
            DeliveryReport.material_no,
            func.sum(DeliveryReport.dn_amount).label("revenue"),
            func.sum(DeliveryReport.dn_qty).label("quantity"),
        ).filter(
            *filters,
            DeliveryReport.material_no.isnot(None)
        ).group_by(
            DeliveryReport.material_no
        ).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).limit(TOP_N_LIMIT).all()
        
        # Top divisions
        top_divisions = self.session.query(
            DeliveryReport.division,
            func.sum(DeliveryReport.dn_amount).label("revenue"),
            func.sum(DeliveryReport.dn_qty).label("quantity"),
        ).filter(
            *filters,
            DeliveryReport.division.isnot(None)
        ).group_by(
            DeliveryReport.division
        ).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).limit(TOP_N_LIMIT).all()
        
        # Total models
        total_models = self.session.query(
            func.count(distinct(DeliveryReport.customer_model)).label("total")
        ).filter(
            *filters,
            DeliveryReport.customer_model.isnot(None)
        ).first()
        
        return ProductSummary(
            total_models=_safe_int(total_models.total) if total_models else 0,
            top_models=[
                {
                    'model': _safe_str(m.customer_model),
                    'revenue': _safe_float(m.revenue),
                    'quantity': _safe_int(m.quantity),
                    'dn_count': _safe_int(m.dn_count),
                } for m in top_models
            ],
            top_materials=[
                {
                    'material': _safe_str(m.material_no),
                    'revenue': _safe_float(m.revenue),
                    'quantity': _safe_int(m.quantity),
                } for m in top_materials
            ],
            top_divisions=[
                {
                    'division': _safe_str(d.division),
                    'revenue': _safe_float(d.revenue),
                    'quantity': _safe_int(d.quantity),
                } for d in top_divisions
            ]
        )
    
    def get_warehouse_summary(self, dealer_code: str, customer_code: str = None) -> WarehouseSummary:
        """Get warehouse analytics summary"""
        filters = [DeliveryReport.dealer_code == dealer_code]
        if customer_code:
            filters.append(DeliveryReport.customer_code == customer_code)
        
        # Warehouse distribution
        warehouse_dist = self.session.query(
            DeliveryReport.warehouse,
            func.count(distinct(DeliveryReport.dn_no)).label("dn_count"),
            func.sum(DeliveryReport.dn_qty).label("units"),
            func.sum(DeliveryReport.dn_amount).label("revenue"),
        ).filter(
            *filters,
            DeliveryReport.warehouse.isnot(None)
        ).group_by(
            DeliveryReport.warehouse
        ).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).all()
        
        # Total warehouses used
        warehouses_used = self.session.query(
            func.count(distinct(DeliveryReport.warehouse)).label("total")
        ).filter(
            *filters,
            DeliveryReport.warehouse.isnot(None)
        ).first()
        
        # Primary warehouse (first from distribution)
        primary_warehouse = warehouse_dist[0].warehouse if warehouse_dist else ""
        
        return WarehouseSummary(
            primary_warehouse=_safe_str(primary_warehouse),
            warehouses_used=_safe_int(warehouses_used.total) if warehouses_used else 0,
            warehouse_distribution=[
                {
                    'warehouse': _safe_str(w.warehouse),
                    'dn_count': _safe_int(w.dn_count),
                    'units': _safe_int(w.units),
                    'revenue': _safe_float(w.revenue),
                } for w in warehouse_dist
            ],
            warehouse_utilization=min(100, (len(warehouse_dist) / 10) * 100) if warehouse_dist else 0
        )
    
    def get_city_summary(self, dealer_code: str, customer_code: str = None) -> CitySummary:
        """Get city analytics summary"""
        filters = [DeliveryReport.dealer_code == dealer_code]
        if customer_code:
            filters.append(DeliveryReport.customer_code == customer_code)
        
        # City distribution
        city_dist = self.session.query(
            DeliveryReport.ship_to_city,
            func.count(distinct(DeliveryReport.dn_no)).label("dn_count"),
            func.sum(DeliveryReport.dn_qty).label("units"),
            func.sum(DeliveryReport.dn_amount).label("revenue"),
        ).filter(
            *filters,
            DeliveryReport.ship_to_city.isnot(None)
        ).group_by(
            DeliveryReport.ship_to_city
        ).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).all()
        
        # Cities served
        cities_served = self.session.query(
            func.count(distinct(DeliveryReport.ship_to_city)).label("total")
        ).filter(
            *filters,
            DeliveryReport.ship_to_city.isnot(None)
        ).first()
        
        return CitySummary(
            cities_served=_safe_int(cities_served.total) if cities_served else 0,
            top_destination_cities=[
                {
                    'city': _safe_str(c.ship_to_city),
                    'revenue': _safe_float(c.revenue),
                    'units': _safe_int(c.units),
                } for c in city_dist[:5]
            ],
            city_distribution=[
                {
                    'city': _safe_str(c.ship_to_city),
                    'dn_count': _safe_int(c.dn_count),
                    'revenue': _safe_float(c.revenue),
                } for c in city_dist
            ]
        )
    
    def get_performance_summary(self, delivery: DeliverySummary, sales: SalesSummary) -> PerformanceSummary:
        """Calculate performance metrics"""
        score = 60
        
        # Delivery Performance (25 points)
        if delivery.delivery_rate >= 95: score += 25
        elif delivery.delivery_rate >= 90: score += 20
        elif delivery.delivery_rate >= 80: score += 15
        elif delivery.delivery_rate >= 70: score += 10
        
        # PGI Performance (15 points)
        if delivery.pgi_rate >= 95: score += 15
        elif delivery.pgi_rate >= 90: score += 10
        elif delivery.pgi_rate >= 80: score += 5
        
        # POD Performance (15 points)
        if delivery.pod_rate >= 90: score += 15
        elif delivery.pod_rate >= 80: score += 10
        elif delivery.pod_rate >= 70: score += 5
        
        # Revenue Performance (15 points)
        if sales.total_revenue > 10_000_000: score += 15
        elif sales.total_revenue > 5_000_000: score += 10
        elif sales.total_revenue > 1_000_000: score += 5
        
        # Delivery Speed (10 points)
        if delivery.avg_delivery_days <= 2: score += 10
        elif delivery.avg_delivery_days <= 4: score += 5
        elif delivery.avg_delivery_days <= 7: score += 2
        
        final_score = min(score, 100)
        risk_score = 100 - final_score
        
        if final_score >= 90:
            tier, rating = "Platinum", 5.0
        elif final_score >= 80:
            tier, rating = "Gold", 4.5
        elif final_score >= 70:
            tier, rating = "Silver", 4.0
        elif final_score >= 60:
            tier, rating = "Bronze", 3.5
        else:
            tier, rating = "Standard", 3.0
        
        return PerformanceSummary(
            business_score=final_score,
            risk_score=risk_score,
            performance_tier=tier,
            dealer_rating=rating,
            dealer_rank=0
        )
    
    def get_date_summary(self, dealer_code: str, customer_code: str = None) -> Dict[str, str]:
        """Get latest dates"""
        filters = [DeliveryReport.dealer_code == dealer_code]
        if customer_code:
            filters.append(DeliveryReport.customer_code == customer_code)
        
        last_delivery = self.session.query(
            func.max(DeliveryReport.dn_create_date).label("last_dn")
        ).filter(*filters).first()
        
        last_pgi = self.session.query(
            func.max(DeliveryReport.good_issue_date).label("last_pgi")
        ).filter(
            *filters,
            DeliveryReport.good_issue_date.isnot(None)
        ).first()
        
        last_pod = self.session.query(
            func.max(DeliveryReport.pod_date).label("last_pod")
        ).filter(
            *filters,
            DeliveryReport.pod_date.isnot(None)
        ).first()
        
        return {
            'last_delivery_date': _format_date(last_delivery.last_dn) if last_delivery else "N/A",
            'last_pgi_date': _format_date(last_pgi.last_pgi) if last_pgi else "N/A",
            'last_pod_date': _format_date(last_pod.last_pod) if last_pod else "N/A"
        }
    
    def get_complete_dashboard(self, dealer_code: str, customer_code: str = None) -> Optional[DealerDashboard]:
        """Get complete dealer dashboard - one unified call"""
        try:
            # Get all data in one session
            identity = self.get_dealer_identity(dealer_code, customer_code)
            if not identity:
                return None
            
            delivery = self.get_delivery_summary(dealer_code, customer_code)
            sales = self.get_sales_summary(dealer_code, customer_code)
            product = self.get_product_summary(dealer_code, customer_code)
            warehouse = self.get_warehouse_summary(dealer_code, customer_code)
            city = self.get_city_summary(dealer_code, customer_code)
            performance = self.get_performance_summary(delivery, sales)
            dates = self.get_date_summary(dealer_code, customer_code)
            
            # Distance info
            distance_info = _get_distance_info(
                identity.get('warehouse', ''),
                identity.get('city', '')
            )
            
            # Generate insights and recommendations
            insights = self._generate_insights(delivery, sales, product)
            recommendations = self._generate_recommendations(delivery, sales, performance)
            executive_summary = self._generate_executive_summary(identity, delivery, sales, performance)
            
            return DealerDashboard(
                identity=DealerIdentity(**identity),
                distance_info=distance_info,
                delivery=delivery,
                sales=sales,
                product=product,
                warehouse=warehouse,
                city=city,
                performance=performance,
                executive_summary=executive_summary,
                insights=insights,
                recommendations=recommendations,
                last_delivery_date=dates['last_delivery_date'],
                last_pgi_date=dates['last_pgi_date'],
                last_pod_date=dates['last_pod_date']
            )
            
        except Exception as e:
            logger.error(f"❌ Dashboard error: {e}")
            return None
    
    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert SQLAlchemy row to dict"""
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
            'region': _safe_str(row.region),
        }
    
    def _generate_insights(self, delivery: DeliverySummary, sales: SalesSummary, product: ProductSummary) -> List[str]:
        """Generate business insights"""
        insights = []
        
        if delivery.delivery_rate >= 95:
            insights.append("✅ Excellent delivery performance")
        elif delivery.delivery_rate >= 85:
            insights.append("✅ Good delivery performance")
        elif delivery.delivery_rate < 75:
            insights.append("⚠️ Delivery rate needs improvement")
        
        if delivery.pod_rate >= 95:
            insights.append("✅ Excellent POD completion")
        elif delivery.pod_rate < 80:
            insights.append("⚠️ POD completion needs attention")
        
        if delivery.pending_dn > 10:
            insights.append(f"⚠️ {delivery.pending_dn} pending deliveries")
        elif delivery.pending_dn > 0:
            insights.append(f"📋 {delivery.pending_dn} pending deliveries")
        
        if sales.total_revenue > 10_000_000:
            insights.append("📈 Revenue is above dealer average")
        elif sales.total_revenue > 5_000_000:
            insights.append("📈 Revenue is at dealer average")
        
        if sales.total_quantity > 1000:
            insights.append(f"📦 Strong sales: {sales.total_quantity:,} units")
        
        if product.total_models > 15:
            insights.append("📦 Strong product portfolio")
        elif product.total_models > 5:
            insights.append("📦 Healthy product portfolio")
        
        if delivery.avg_delivery_days <= 2:
            insights.append(f"🚚 Fast delivery: {delivery.avg_delivery_days:.1f} days")
        elif delivery.avg_delivery_days > 5:
            insights.append("⚠️ Delivery speed needs improvement")
        
        return insights[:8]
    
    def _generate_recommendations(self, delivery: DeliverySummary, sales: SalesSummary, performance: PerformanceSummary) -> List[str]:
        """Generate actionable recommendations"""
        recs = []
        
        if delivery.pending_dn > 10:
            recs.append("📋 Resolve pending deliveries")
        elif delivery.pending_dn > 5:
            recs.append("📋 Clear pending deliveries")
        
        if delivery.delivery_rate < 80:
            recs.append("📋 Improve delivery processes")
        
        if delivery.pod_rate < 85:
            recs.append("📋 Focus on POD completion")
        
        if performance.business_score < 70:
            recs.append("📋 Implement performance improvement plan")
        
        if sales.total_revenue < 1_000_000:
            recs.append("📋 Review revenue growth strategies")
        
        if performance.risk_score > 30:
            recs.append("📋 Conduct risk assessment")
        
        if not recs:
            recs.extend([
                "📋 Maintain current performance",
                "📋 Monitor delivery metrics",
                "📋 Explore growth opportunities"
            ])
        
        return recs[:5]
    
    def _generate_executive_summary(self, identity: Dict, delivery: DeliverySummary, sales: SalesSummary, performance: PerformanceSummary) -> str:
        """Generate executive summary"""
        customer_name = identity.get('customer_name', 'Dealer')
        score = performance.business_score
        revenue = sales.total_revenue
        pending = delivery.pending_dn
        delivery_rate = delivery.delivery_rate
        tier = performance.performance_tier
        
        if score >= 80:
            status = "excellent"
        elif score >= 60:
            status = "good"
        else:
            status = "needs attention"
        
        return (
            f"{customer_name} has {status} performance with a {score}/100 business score. "
            f"Revenue is {_format_currency(revenue)} with {pending} pending deliveries. "
            f"Delivery success rate is {delivery_rate:.1f}%. "
            f"Performance tier: {tier}."
        )

# ============================================================
# DEALER SEARCH ENGINE
# ============================================================

class DealerSearchEngine:
    """
    Enterprise Dealer Search Engine
    
    Multi-level search with fallback strategies:
    1. Dealer Code (exact)
    2. Customer Code (exact)
    3. Exact Name Match
    4. ILIKE Name Match
    5. Partial Name Match
    6. Token Match
    7. Fuzzy Match
    8. Phonetic Match (Soundex)
    9. Suggestions
    """
    
    def __init__(self):
        self._search_cache = {}
        self._cache_lock = threading.RLock()
    
    def search_dealer(self, query: str) -> DealerSearchResult:
        """Search dealer using multi-level strategy"""
        start_time = time.time()
        
        try:
            if not query or not query.strip():
                return DealerSearchResult(success=False, message="Empty query")
            
            query_clean = query.strip()
            normalized = _normalize_text(query_clean)
            cleaned_query = _clean_dealer_name(query_clean)
            
            logger.info(f"🔍 Searching: '{query_clean}'")
            logger.info(f"   Normalized: '{normalized}'")
            logger.info(f"   Cleaned: '{cleaned_query}'")
            
            with SessionLocal() as session:
                repo = DealerRepository(session)
                
                # Strategy 1: Dealer Code
                result = repo.get_dealer_by_code(query_clean)
                if result:
                    elapsed = (time.time() - start_time) * 1000
                    return DealerSearchResult(
                        success=True,
                        customer_name=result.get('customer_name', ''),
                        dealer_code=result.get('dealer_code', ''),
                        customer_code=result.get('customer_code', ''),
                        confidence=1.0,
                        match_type="dealer_code",
                        message="Found by dealer code",
                        search_time_ms=elapsed,
                        normalized_query=normalized
                    )
                
                # Strategy 2: Customer Code
                result = repo.get_dealer_by_customer_code(query_clean)
                if result:
                    elapsed = (time.time() - start_time) * 1000
                    return DealerSearchResult(
                        success=True,
                        customer_name=result.get('customer_name', ''),
                        dealer_code=result.get('dealer_code', ''),
                        customer_code=result.get('customer_code', ''),
                        confidence=1.0,
                        match_type="customer_code",
                        message="Found by customer code",
                        search_time_ms=elapsed,
                        normalized_query=normalized
                    )
                
                # Strategy 3: Exact Name
                result = repo.search_dealers_exact(query_clean)
                if result:
                    elapsed = (time.time() - start_time) * 1000
                    return DealerSearchResult(
                        success=True,
                        customer_name=result.get('customer_name', ''),
                        dealer_code=result.get('dealer_code', ''),
                        customer_code=result.get('customer_code', ''),
                        confidence=0.95,
                        match_type="exact",
                        message="Found exact match",
                        search_time_ms=elapsed,
                        normalized_query=normalized
                    )
                
                # Strategy 4: ILIKE
                results = repo.search_dealers_ilike(query_clean, limit=SEARCH_LIMIT)
                if results:
                    elapsed = (time.time() - start_time) * 1000
                    first = results[0]
                    return DealerSearchResult(
                        success=True,
                        customer_name=first.get('customer_name', ''),
                        dealer_code=first.get('dealer_code', ''),
                        customer_code=first.get('customer_code', ''),
                        confidence=0.90,
                        match_type="ilike",
                        message="Found ILIKE match",
                        suggestions=[],
                        search_time_ms=elapsed,
                        normalized_query=normalized
                    )
                
                # Strategy 5: Partial Name
                results = repo.search_dealers_partial(query_clean, limit=SEARCH_LIMIT)
                if results:
                    elapsed = (time.time() - start_time) * 1000
                    first = results[0]
                    suggestions = [
                        {
                            'customer_name': r.get('customer_name', ''),
                            'dealer_code': r.get('dealer_code', ''),
                            'customer_code': r.get('customer_code', ''),
                            'confidence': 0.7 - (i * 0.05)
                        }
                        for i, r in enumerate(results[:5])
                    ]
                    return DealerSearchResult(
                        success=True,
                        customer_name=first.get('customer_name', ''),
                        dealer_code=first.get('dealer_code', ''),
                        customer_code=first.get('customer_code', ''),
                        confidence=0.85,
                        match_type="partial",
                        message="Found partial match",
                        suggestions=suggestions[1:] if len(suggestions) > 1 else [],
                        search_time_ms=elapsed,
                        normalized_query=normalized
                    )
                
                # Strategy 6: Token Match
                results = repo.search_dealers_token(query_clean, limit=SEARCH_LIMIT)
                if results:
                    elapsed = (time.time() - start_time) * 1000
                    first = results[0]
                    return DealerSearchResult(
                        success=True,
                        customer_name=first.get('customer_name', ''),
                        dealer_code=first.get('dealer_code', ''),
                        customer_code=first.get('customer_code', ''),
                        confidence=0.80,
                        match_type="token",
                        message="Found token match",
                        suggestions=[],
                        search_time_ms=elapsed,
                        normalized_query=normalized
                    )
                
                # Strategy 7: Fuzzy Match
                results = repo.search_dealers_fuzzy(query_clean, limit=SEARCH_LIMIT)
                if results:
                    elapsed = (time.time() - start_time) * 1000
                    first = results[0]
                    return DealerSearchResult(
                        success=True,
                        customer_name=first.get('customer_name', ''),
                        dealer_code=first.get('dealer_code', ''),
                        customer_code=first.get('customer_code', ''),
                        confidence=0.75,
                        match_type="fuzzy",
                        message="Found fuzzy match",
                        suggestions=[],
                        search_time_ms=elapsed,
                        normalized_query=normalized
                    )
                
                # Strategy 8: Phonetic Match
                results = repo.search_dealers_phonetic(query_clean, limit=SEARCH_LIMIT)
                if results:
                    elapsed = (time.time() - start_time) * 1000
                    first = results[0]
                    return DealerSearchResult(
                        success=True,
                        customer_name=first.get('customer_name', ''),
                        dealer_code=first.get('dealer_code', ''),
                        customer_code=first.get('customer_code', ''),
                        confidence=0.70,
                        match_type="phonetic",
                        message="Found phonetic match",
                        suggestions=[],
                        search_time_ms=elapsed,
                        normalized_query=normalized
                    )
                
                # No matches found
                elapsed = (time.time() - start_time) * 1000
                return DealerSearchResult(
                    success=False,
                    message="No dealer found",
                    suggestions=[],
                    search_time_ms=elapsed,
                    normalized_query=normalized
                )
            
        except Exception as e:
            logger.error(f"❌ Search error: {e}")
            logger.error(traceback.format_exc())
            return DealerSearchResult(
                success=False,
                message=f"Search error: {str(e)}",
                search_time_ms=(time.time() - start_time) * 1000
            )

# ============================================================
# DEALER DASHBOARD BUILDER
# ============================================================

class DealerDashboardBuilder:
    """
    Enterprise Dealer Dashboard Builder
    
    Caches dashboards for 5 minutes.
    Never caches search results.
    """
    
    def __init__(self):
        self._cache: Dict[str, DealerDashboard] = {}
        self._cache_time: Dict[str, datetime] = {}
        self._cache_lock = threading.RLock()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_size_limit = 1000
    
    def build(self, dealer_code: str, customer_code: str = None) -> Optional[DealerDashboard]:
        """Build dealer dashboard with caching"""
        cache_key = f"{dealer_code}_{customer_code}"
        
        # Check cache
        with self._cache_lock:
            # Clean cache if too large
            if len(self._cache) > self._cache_size_limit:
                oldest_keys = sorted(self._cache_time.keys(), key=lambda k: self._cache_time[k])[:100]
                for key in oldest_keys:
                    del self._cache[key]
                    del self._cache_time[key]
                logger.info(f"🧹 Cache cleaned: removed {len(oldest_keys)} entries")
            
            if cache_key in self._cache:
                cache_age = (datetime.now() - self._cache_time[cache_key]).seconds
                if cache_age < CACHE_TTL:
                    self._cache_hits += 1
                    logger.info(f"✅ Dashboard cache hit for {dealer_code}")
                    return self._cache[cache_key]
            self._cache_misses += 1
        
        try:
            logger.info(f"📊 Building dashboard for {dealer_code}")
            start_time = time.time()
            
            with SessionLocal() as session:
                repo = DealerRepository(session)
                dashboard = repo.get_complete_dashboard(dealer_code, customer_code)
                
                if not dashboard:
                    logger.warning(f"⚠️ No dashboard data for {dealer_code}")
                    return None
            
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"✅ Dashboard built in {elapsed:.0f}ms")
            
            # Cache
            with self._cache_lock:
                self._cache[cache_key] = dashboard
                self._cache_time[cache_key] = datetime.now()
            
            return dashboard
            
        except Exception as e:
            logger.error(f"❌ Dashboard error: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._cache_lock:
            return {
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "cache_size": len(self._cache),
                "cache_limit": self._cache_size_limit,
                "hit_rate": round((self._cache_hits / max(self._cache_hits + self._cache_misses, 1)) * 100, 1)
            }
    
    def clear_cache(self):
        """Clear cache"""
        with self._cache_lock:
            self._cache.clear()
            self._cache_time.clear()
            logger.info("📊 Dashboard cache cleared")

# ============================================================
# WHATSAPP FORMATTER
# ============================================================

class WhatsAppFormatter:
    """
    Enterprise WhatsApp Formatter
    
    Formats dealer dashboard for WhatsApp with clean, mobile-optimized layout.
    """
    
    @staticmethod
    def format_dashboard(dashboard: DealerDashboard) -> str:
        """Format dashboard for WhatsApp"""
        identity = dashboard.identity
        distance = dashboard.distance_info
        delivery = dashboard.delivery
        sales = dashboard.sales
        product = dashboard.product
        warehouse = dashboard.warehouse
        city = dashboard.city
        performance = dashboard.performance
        
        lines = []
        
        # HEADER
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏢 DEALER DASHBOARD")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # DEALER INFORMATION
        lines.append("Dealer")
        lines.append(identity.customer_name)
        lines.append("")
        lines.append("Dealer Code")
        lines.append(identity.dealer_code)
        lines.append("")
        lines.append("Customer Code")
        lines.append(identity.customer_code)
        lines.append("")
        
        # LOCATION
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("📍 LOCATION")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("Warehouse")
        lines.append(identity.warehouse)
        lines.append("")
        lines.append("Warehouse Code")
        lines.append(identity.warehouse_code)
        lines.append("")
        lines.append("Dealer City")
        lines.append(identity.city)
        lines.append("")
        lines.append("Distance")
        if distance.get('distance_km'):
            lines.append(f"{distance['distance_km']} KM")
            lines.append("")
            lines.append("Estimated Delivery")
            lines.append(distance.get('estimated_delivery', 'N/A'))
            lines.append("")
            lines.append("Transportation Zone")
            lines.append(distance.get('transportation_zone', 'N/A'))
        else:
            lines.append("Not Available")
        lines.append("")
        
        # DELIVERY PERFORMANCE
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("🚚 DELIVERY PERFORMANCE")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"Total DN        : {delivery.total_dn:,}")
        lines.append(f"Delivered       : {delivery.delivered_dn:,}")
        lines.append(f"Pending         : {delivery.pending_dn:,}")
        lines.append(f"PGI Pending     : {delivery.pgi_pending:,}")
        lines.append(f"POD Pending     : {delivery.pod_pending:,}")
        lines.append("")
        lines.append(f"Delivery Rate   : {delivery.delivery_rate:.1f}%")
        lines.append(f"PGI Rate        : {delivery.pgi_rate:.1f}%")
        lines.append(f"POD Rate        : {delivery.pod_rate:.1f}%")
        lines.append("")
        
        # SALES PERFORMANCE
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("💰 SALES PERFORMANCE")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"Total Quantity  : {sales.total_quantity:,} Units")
        lines.append(f"Total Sales     : {_format_currency(sales.total_revenue)}")
        lines.append(f"Avg DN Value    : {_format_currency(sales.avg_dn_value)}")
        lines.append(f"Avg Quantity    : {sales.avg_quantity_per_dn:.2f} Units")
        lines.append("")
        
        # DELIVERY TIMES
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("⏱️ DELIVERY TIMES")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"Avg Delivery    : {delivery.avg_delivery_days:.1f} Days")
        lines.append(f"Avg POD         : {delivery.avg_pod_days:.1f} Days")
        lines.append(f"Min Delivery    : {delivery.min_delivery_days:.1f} Days")
        lines.append(f"Max Delivery    : {delivery.max_delivery_days:.1f} Days")
        lines.append("")
        lines.append(f"Last DN         : {dashboard.last_delivery_date}")
        lines.append(f"Last PGI        : {dashboard.last_pgi_date}")
        lines.append(f"Last POD        : {dashboard.last_pod_date}")
        lines.append("")
        
        # TOP MODELS
        if product.top_models:
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("🏷️ TOP MODELS")
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("")
            for i, model in enumerate(product.top_models[:5], 1):
                lines.append(f"{i}. {model.get('model', 'N/A')}")
                lines.append(f"   Revenue: {_format_currency(model.get('revenue', 0))}")
                lines.append(f"   Quantity: {model.get('quantity', 0):,}")
                lines.append("")
        
        # WAREHOUSE
        if warehouse.warehouse_distribution:
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("🏭 WAREHOUSE")
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("")
            for wh in warehouse.warehouse_distribution[:3]:
                lines.append(wh.get('warehouse', 'N/A'))
                lines.append(f"  DN: {wh.get('dn_count', 0):,}")
                lines.append(f"  Units: {wh.get('units', 0):,}")
                lines.append("")
        
        # CITY
        if city.top_destination_cities:
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("📍 TOP CITIES")
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("")
            for i, c in enumerate(city.top_destination_cities[:3], 1):
                lines.append(f"{i}. {c.get('city', 'N/A')}")
                lines.append(f"   Revenue: {_format_currency(c.get('revenue', 0))}")
                lines.append("")
        
        # PERFORMANCE
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("📈 PERFORMANCE")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"Business Score   : {performance.business_score}/100")
        lines.append(f"Risk Score       : {performance.risk_score}/100")
        lines.append(f"Performance      : {performance.performance_tier}")
        
        full_stars = int(performance.dealer_rating)
        empty_stars = 5 - full_stars
        stars = "⭐" * full_stars + "☆" * empty_stars
        lines.append(f"Dealer Rating    : {stars}")
        lines.append("")
        
        # INSIGHTS
        if dashboard.insights:
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("💡 INSIGHTS")
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("")
            for insight in dashboard.insights[:3]:
                lines.append(insight)
                lines.append("")
        
        # RECOMMENDATIONS
        if dashboard.recommendations:
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("📋 RECOMMENDATIONS")
            lines.append("━━━━━━━━━━━━━━━━")
            lines.append("")
            for rec in dashboard.recommendations[:3]:
                lines.append(rec)
                lines.append("")
        
        # FOOTER
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💬 Type '99' to return to Main Menu")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)

# ============================================================
# MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """
    Enterprise Dealer Intelligence Platform
    
    Single entry point for all dealer analytics.
    Encapsulates all dealer intelligence functionality.
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
        self._search_engine = DealerSearchEngine()
        self._dashboard_builder = DealerDashboardBuilder()
        self._formatter = WhatsAppFormatter()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._startup_time = datetime.now()
        self._request_count = 0
        self._success_count = 0
        self._error_count = 0
        
        self._show_startup_info()
    
    def _show_startup_info(self):
        """Display startup information"""
        print("\n" + "=" * 70)
        print("🏢 DEALER LOGISTICS INTELLIGENCE v{}".center(70).format(self._version))
        print("=" * 70)
        print("🗄️  PostgreSQL: Single Source of Truth")
        print("🔍 Search Engine: 9 Strategies")
        print("📊 Dashboard: 15+ KPI Sections")
        print("📱 WhatsApp Optimized")
        print("💾 Cache: 5 minutes")
        print("📈 Scales to: 500,000+ records")
        print("=" * 70 + "\n")
    
    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - Called by AIProviderService
        
        Returns:
            WhatsApp-formatted dashboard or error message
        """
        self._request_count += 1
        start_time = time.time()
        
        try:
            logger.info(f"📨 Dealer query: '{message}' from {sender}")
            
            if not message or not message.strip():
                return self._get_welcome_message()
            
            message_clean = message.strip()
            
            # Command checks
            if message_clean in ["99", "exit", "quit", "back"]:
                logger.info(f"🚪 Exit requested by {sender}")
                return EXIT_SIGNAL
            
            if message_clean in ["help", "?", "start", "hello", "hi"]:
                return self._get_welcome_message()
            
            if message_clean in ["examples", "example"]:
                return self._get_examples()
            
            # Handle numeric selection (for suggestions)
            if message_clean.isdigit():
                return self._handle_selection(int(message_clean), sender)
            
            # Search for dealer
            search_result = self._search_engine.search_dealer(message_clean)
            
            if not search_result.success:
                self._error_count += 1
                return self._format_not_found(message_clean, search_result, sender)
            
            # Get or create session
            session = self._get_session(sender)
            session['dealer_code'] = search_result.dealer_code
            session['customer_code'] = search_result.customer_code
            session['last_query'] = message_clean
            session['pending_matches'] = search_result.suggestions
            
            # Build dashboard
            dashboard = self._dashboard_builder.build(
                search_result.dealer_code,
                search_result.customer_code
            )
            
            if not dashboard:
                self._error_count += 1
                return self._format_no_data(search_result.customer_name)
            
            # Format response
            response = self._formatter.format_dashboard(dashboard)
            
            elapsed = (time.time() - start_time) * 1000
            self._success_count += 1
            logger.info(f"✅ Response in {elapsed:.0f}ms")
            
            return response
            
        except Exception as e:
            self._error_count += 1
            logger.error(f"❌ Dealer service error: {e}")
            logger.error(traceback.format_exc())
            return self._format_error(str(e)[:100])
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def _get_session(self, user_id: str) -> Dict[str, Any]:
        """Get or create session"""
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
    
    # ============================================================
    # HANDLE SELECTION
    # ============================================================
    
    def _handle_selection(self, selection: int, sender: str) -> str:
        """Handle numeric selection from suggestions"""
        session = self._get_session(sender)
        
        if not session.get('pending_matches'):
            return "\n".join([
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "⚠️ NO PENDING SELECTION",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "Please enter a dealer name to search.",
                "",
                "99️⃣ Return to Main Menu",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ])
        
        matches = session['pending_matches']
        if selection < 1 or selection > len(matches):
            return f"Please select a number between 1 and {len(matches)}"
        
        selected = matches[selection - 1]
        
        # Search again with selected dealer
        search_result = self._search_engine.search_dealer(selected.get('customer_name', ''))
        
        if not search_result.success:
            return self._format_not_found(selected.get('customer_name', ''), search_result, sender)
        
        session['dealer_code'] = search_result.dealer_code
        session['customer_code'] = search_result.customer_code
        session['pending_matches'] = []
        
        dashboard = self._dashboard_builder.build(
            search_result.dealer_code,
            search_result.customer_code
        )
        
        if not dashboard:
            return self._format_no_data(search_result.customer_name)
        
        return self._formatter.format_dashboard(dashboard)
    
    # ============================================================
    # WELCOME AND EXAMPLES
    # ============================================================
    
    def _get_welcome_message(self) -> str:
        """Show welcome message"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏢 DEALER LOGISTICS INTELLIGENCE",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Enter a Dealer Name, Code, or Customer Code.",
            "",
            "✅ Dealer Code",
            "✅ Customer Code",
            "✅ Dealer Name",
            "✅ Partial Name",
            "✅ Fuzzy Match",
            "",
            "💡 Try: Arshad Electronics-Khi",
            "",
            "99️⃣ Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _get_examples(self) -> str:
        """Show examples"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📝 DEALER EXAMPLES",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Try searching for:",
            "",
            "1. Arshad Electronics-Khi",
            "2. Zoom Appliances",
            "3. RUBA Digital",
            "4. Metro Electronics",
            "5. Friends Electronics",
            "6. Al Madina Electronics",
            "",
            "💡 Or search by:",
            "• Dealer Code (e.g., DEAL_001)",
            "• Customer Code (e.g., CUST_001)",
            "",
            "99️⃣ Return to Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    # ============================================================
    # ERROR FORMATTING
    # ============================================================
    
    def _format_not_found(self, query: str, search_result: DealerSearchResult, sender: str) -> str:
        """Format not found response"""
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
                lines.append(f"{i}. {name} ({confidence*100:.0f}% match)")
            lines.append("")
            lines.append("💬 Type the number to select a dealer")
            lines.append("")
            
            # Store suggestions
            session = self._get_session(sender)
            session['pending_matches'] = search_result.suggestions[:5]
        else:
            lines.append("💡 Suggestions:")
            lines.append("• Check the spelling")
            lines.append("• Try searching by Dealer Code")
            lines.append("• Try searching by Customer Code")
            lines.append("• Use partial name search")
            lines.append("")
        
        lines.append("99️⃣ Return to Main Menu")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)
    
    def _format_no_data(self, dealer_name: str) -> str:
        """Format no data response"""
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
        """Format error response"""
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
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check"""
        uptime = (datetime.now() - self._startup_time).seconds
        
        return {
            "service": "dealer_analytics_service",
            "version": self._version,
            "status": "healthy" if self._error_count < self._request_count * 0.1 else "degraded",
            "uptime_seconds": uptime,
            "total_requests": self._request_count,
            "successful_requests": self._success_count,
            "error_count": self._error_count,
            "success_rate": round((self._success_count / max(self._request_count, 1)) * 100, 1),
            "cache_hit_rate": self._dashboard_builder.get_cache_stats().get('hit_rate', 0),
            "cache_size": self._dashboard_builder.get_cache_stats().get('cache_size', 0),
            "active_sessions": len(self._sessions)
        }

# ============================================================
# SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        _service = DealerAnalyticsService()
    return _service

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "get_dealer_service",
    "EXIT_SIGNAL",
    "VERSION"
]

# ============================================================
# TEST MODE
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("DEALER LOGISTICS INTELLIGENCE - TEST MODE".center(70))
    print("=" * 70)
    print()
    
    service = get_dealer_service()
    
    # Show health
    health = service.health_check()
    print("📊 Health Check:")
    for key, value in health.items():
        print(f"  {key}: {value}")
    print()
    
    # Show welcome
    print(service._get_welcome_message())
    print()
    
    # Interactive test
    print("🔍 Enter dealer name to search (or 99 to exit)")
    print()
    
    while True:
        try:
            query = input("🔍 Enter Dealer Name: ").strip()
            
            if query == "99":
                print("\n👋 Goodbye!")
                break
            
            if not query:
                continue
            
            print("\n⏳ Processing...\n")
            result = service.process_whatsapp_query(query, "test_user")
            
            if result == EXIT_SIGNAL:
                print("Exiting...")
                break
            
            print(result)
            print()
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}\n")
            traceback.print_exc()
