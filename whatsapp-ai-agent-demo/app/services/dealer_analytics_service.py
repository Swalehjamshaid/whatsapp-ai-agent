# ============================================================
# FILE: app/services/dealer_search_service.py
# VERSION: 1.0 - ENTERPRISE DEALER SEARCH ENGINE
# ============================================================

"""
File: app/services/dealer_search_service.py
Version: 1.0 - ENTERPRISE DEALER SEARCH ENGINE

================================================================================
PURPOSE
================================================================================

This file is responsible ONLY for Dealer Search.

Its ONLY responsibility is:

        Detect Dealer
              ↓
      Load Dealer Profile
              ↓
    Calculate Dealer KPIs
              ↓
   Build Dealer Dashboard
              ↓
      Return Professional Response

NO AI used for Dealer Detection.
PostgreSQL is the ONLY source of truth.

================================================================================
SEARCH PRIORITY
================================================================================

1. customer_name
2. dealer_code
3. customer_code

MATCHING PRIORITY:
1. Exact Match (100%)
2. Ignore Case
3. Ignore Spaces
4. Ignore Symbols
5. Partial Match
6. Word Match
7. Alias Match
8. Dealer Code
9. Customer Code
10. RapidFuzz (70% threshold)

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple, Union

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: RAPIDFUZZ FOR FUZZY MATCHING
# ============================================================

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("⚠️ RapidFuzz not available - fuzzy matching disabled")

# ============================================================
# BLOCK 2: DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, desc, asc, and_, text
    from sqlalchemy.orm import Session
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
    logger.info("✅ Dealer search database imports successful")
except ImportError as e:
    DB_AVAILABLE = False
    logger.error(f"❌ Dealer search database import error: {e}")

# ============================================================
# BLOCK 3: CONFIGURATION
# ============================================================

DEALER_CACHE_TTL = int(os.getenv("DEALER_CACHE_TTL", "600"))
FUZZY_MATCH_THRESHOLD = int(os.getenv("FUZZY_MATCH_THRESHOLD", "70"))
DEALER_SESSION_TIMEOUT = int(os.getenv("DEALER_SESSION_TIMEOUT", "1800"))

# ============================================================
# BLOCK 4: DATA CLASSES
# ============================================================

@dataclass
class DealerMatch:
    """Dealer match result"""
    dealer_name: str
    dealer_code: str
    customer_code: str
    score: float
    match_type: str  # exact, case_insensitive, space_insensitive, symbol_insensitive, partial, word, alias, dealer_code, customer_code, fuzzy
    confidence: float
    
    def is_confident(self) -> bool:
        """Check if match is confident enough"""
        return self.confidence >= 0.70
    
    def is_exact(self) -> bool:
        """Check if match is exact"""
        return self.match_type == "exact"

@dataclass
class DealerProfile:
    """Complete dealer profile"""
    dealer_name: str = ""
    dealer_code: str = ""
    customer_code: str = ""
    sales_office: str = ""
    sales_manager: str = ""
    division: str = ""
    primary_warehouse: str = ""
    primary_warehouse_code: str = ""
    primary_city: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    
    # Financial
    total_revenue: float = 0.0
    avg_revenue_per_dn: float = 0.0
    total_units: int = 0
    avg_units_per_dn: float = 0.0
    
    # Operations
    total_dn: int = 0
    pending_dn: int = 0
    pending_pgi: int = 0
    pending_pod: int = 0
    delivered_dn: int = 0
    
    # Delivery
    delivery_pct: float = 0.0
    pgi_pct: float = 0.0
    pod_pct: float = 0.0
    avg_delivery_days: float = 0.0
    avg_pod_days: float = 0.0
    
    # Products
    product_count: int = 0
    model_count: int = 0
    material_count: int = 0
    top_product: str = ""
    top_model: str = ""
    top_material: str = ""
    top_division: str = ""
    
    # Warehouses
    warehouses_used: List[str] = field(default_factory=list)
    warehouse_count: int = 0
    
    # Cities
    cities_served: List[str] = field(default_factory=list)
    city_count: int = 0
    
    # Distance
    distance_km: float = 0.0
    travel_time_minutes: int = 0
    transport_zone: str = ""
    
    # Rankings
    revenue_rank: int = 0
    unit_rank: int = 0
    dn_rank: int = 0
    delivery_rank: int = 0
    overall_rank: int = 0
    
    # Scores
    business_score: float = 0.0
    risk_score: float = 0.0
    revenue_score: float = 0.0
    delivery_score: float = 0.0
    growth_score: float = 0.0
    
    # Timeline
    first_order: str = ""
    last_order: str = ""
    latest_dn: str = ""
    latest_pod_date: str = ""
    latest_activity: str = ""
    
    # Insights & Recommendations
    insights: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

@dataclass
class DealerSearchResult:
    """Complete dealer search result"""
    success: bool
    matches: List[DealerMatch] = field(default_factory=list)
    selected_dealer: Optional[DealerMatch] = None
    profile: Optional[DealerProfile] = None
    message: str = ""
    dashboard: str = ""
    
    def has_multiple_matches(self) -> bool:
        """Check if there are multiple matches"""
        return len(self.matches) > 1
    
    def has_single_confident_match(self) -> bool:
        """Check if there's a single confident match"""
        confident_matches = [m for m in self.matches if m.is_confident()]
        return len(confident_matches) == 1
    
    def get_confident_matches(self) -> List[DealerMatch]:
        """Get all confident matches"""
        return [m for m in self.matches if m.is_confident()]

# ============================================================
# BLOCK 5: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _format_currency(amount: float) -> str:
    if amount is None:
        return "PKR 0.00"
    if amount >= 1_000_000_000_000:
        return f"PKR {amount/1_000_000_000_000:,.2f} Trillion"
    elif amount >= 1_000_000_000:
        return f"PKR {amount/1_000_000_000:,.2f} Billion"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:,.2f} Million"
    else:
        return f"PKR {amount:,.2f}"

def _format_number(num: Union[int, float]) -> str:
    if num is None:
        return "0"
    return f"{num:,}"

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def _growth(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)

def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _get_transport_zone(distance_km: float) -> str:
    if distance_km <= 50:
        return "Local (≤50 km)"
    elif distance_km <= 150:
        return "Regional (51-150 km)"
    elif distance_km <= 300:
        return "National (151-300 km)"
    elif distance_km <= 500:
        return "Extended (301-500 km)"
    else:
        return "Long Haul (>500 km)"

def _normalize_text(text: str) -> str:
    """Normalize text for matching - remove spaces, symbols, special chars"""
    # Remove special characters and symbols
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    # Remove extra spaces
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def _normalize_no_spaces(text: str) -> str:
    """Remove all spaces from text"""
    return re.sub(r'\s+', '', text)

# ============================================================
# BLOCK 6: DEALER SEARCH ENGINE
# ============================================================

class DealerSearchEngine:
    """
    Enterprise Dealer Search Engine
    
    ONLY responsible for:
    1. Detecting dealers
    2. Loading dealer profiles
    3. Calculating KPIs
    4. Building dashboards
    
    NO AI used for detection.
    PostgreSQL is the ONLY source of truth.
    """
    
    _instance: Optional["DealerSearchEngine"] = None
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
        self._service_name = "dealer_search"
        self._version = "1.0"
        
        # Cache for dealer data
        self._dealer_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()
        
        # Load all dealers on startup
        self._load_all_dealers()
        
        logger.info("=" * 80)
        logger.info(f"🚀 Dealer Search Engine v{self._version} initialized")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info(f"   🔍 Fuzzy Match Threshold: {FUZZY_MATCH_THRESHOLD}%")
        logger.info(f"   📚 Dealers Loaded: {len(self._dealer_cache)}")
        logger.info("=" * 80)
    
    def _get_db_session(self) -> Optional[Session]:
        """Get database session"""
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def _load_all_dealers(self):
        """Load all dealers into cache"""
        if not DB_AVAILABLE:
            logger.warning("⚠️ Database not available")
            return
        
        session = self._get_db_session()
        if not session:
            return
        
        try:
            results = session.query(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code
            ).all()
            
            session.close()
            
            for row in results:
                dealer_name = _text(row.customer_name)
                if dealer_name and dealer_name != "N/A":
                    key = dealer_name.lower()
                    self._dealer_cache[key] = {
                        'dealer_name': dealer_name,
                        'dealer_code': _text(row.dealer_code),
                        'customer_code': _text(row.customer_code),
                        'sales_office': _text(row.sales_office),
                        'sales_manager': _text(row.sales_manager),
                        'division': _text(row.division),
                        'primary_city': _text(row.ship_to_city),
                        'primary_warehouse': _text(row.warehouse),
                        'primary_warehouse_code': _text(row.warehouse_code),
                        'dn_count': int(row.dn_count or 0),
                        'total_units': int(row.total_units or 0),
                        'total_revenue': float(row.total_revenue or 0),
                    }
            
            logger.info(f"✅ Loaded {len(self._dealer_cache)} dealers into cache")
            
        except Exception as e:
            logger.error(f"Failed to load dealers: {e}")
            if session:
                session.close()
    
    # ============================================================
    # DEALER DETECTION ENGINE
    # ============================================================
    
    def detect_dealer(self, query: str) -> DealerSearchResult:
        """
        Detect dealer using multi-stage matching
        
        Priority:
        1. Exact Match (customer_name)
        2. Case Insensitive
        3. Space Insensitive
        4. Symbol Insensitive
        5. Partial Match
        6. Word Match
        7. Alias Match
        8. Dealer Code
        9. Customer Code
        10. RapidFuzz (70% threshold)
        """
        if not query or not query.strip():
            return DealerSearchResult(
                success=False,
                message="Please enter a dealer name."
            )
        
        query_clean = query.strip()
        start_time = time.time()
        
        # Stage 1: Exact Match
        result = self._exact_match(query_clean)
        if result:
            logger.info(f"✅ Exact match: {result.dealer_name} (100%)")
            return self._finalize_result(result, query_clean)
        
        # Stage 2: Case Insensitive
        result = self._case_insensitive_match(query_clean)
        if result:
            logger.info(f"✅ Case insensitive match: {result.dealer_name}")
            return self._finalize_result(result, query_clean)
        
        # Stage 3: Space Insensitive
        result = self._space_insensitive_match(query_clean)
        if result:
            logger.info(f"✅ Space insensitive match: {result.dealer_name}")
            return self._finalize_result(result, query_clean)
        
        # Stage 4: Symbol Insensitive
        result = self._symbol_insensitive_match(query_clean)
        if result:
            logger.info(f"✅ Symbol insensitive match: {result.dealer_name}")
            return self._finalize_result(result, query_clean)
        
        # Stage 5: Partial Match
        result = self._partial_match(query_clean)
        if result:
            logger.info(f"✅ Partial match: {result.dealer_name}")
            return self._finalize_result(result, query_clean)
        
        # Stage 6: Word Match
        result = self._word_match(query_clean)
        if result:
            logger.info(f"✅ Word match: {result.dealer_name}")
            return self._finalize_result(result, query_clean)
        
        # Stage 7: Alias Match
        result = self._alias_match(query_clean)
        if result:
            logger.info(f"✅ Alias match: {result.dealer_name}")
            return self._finalize_result(result, query_clean)
        
        # Stage 8: Dealer Code
        result = self._dealer_code_match(query_clean)
        if result:
            logger.info(f"✅ Dealer code match: {result.dealer_name}")
            return self._finalize_result(result, query_clean)
        
        # Stage 9: Customer Code
        result = self._customer_code_match(query_clean)
        if result:
            logger.info(f"✅ Customer code match: {result.dealer_name}")
            return self._finalize_result(result, query_clean)
        
        # Stage 10: Fuzzy Match (RapidFuzz - 70% threshold)
        result = self._fuzzy_match(query_clean)
        if result:
            logger.info(f"✅ Fuzzy match: {result.dealer_name} ({result.confidence:.1f}%)")
            return self._finalize_result(result, query_clean)
        
        # No match found - suggest similar dealers
        suggestions = self._get_suggestions(query_clean)
        
        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(f"⏱️ Dealer detection completed in {elapsed_ms:.2f}ms")
        
        return DealerSearchResult(
            success=False,
            message="Dealer not found.",
            matches=suggestions
        )
    
    # ============================================================
    # MATCHING STAGES
    # ============================================================
    
    def _exact_match(self, query: str) -> Optional[DealerMatch]:
        """Exact match on customer_name"""
        for key, data in self._dealer_cache.items():
            if key == query.lower():
                return DealerMatch(
                    dealer_name=data['dealer_name'],
                    dealer_code=data.get('dealer_code', ''),
                    customer_code=data.get('customer_code', ''),
                    score=100.0,
                    match_type="exact",
                    confidence=1.0
                )
        return None
    
    def _case_insensitive_match(self, query: str) -> Optional[DealerMatch]:
        """Case insensitive match"""
        query_lower = query.lower()
        for key, data in self._dealer_cache.items():
            if key == query_lower:
                return DealerMatch(
                    dealer_name=data['dealer_name'],
                    dealer_code=data.get('dealer_code', ''),
                    customer_code=data.get('customer_code', ''),
                    score=99.0,
                    match_type="case_insensitive",
                    confidence=0.99
                )
        return None
    
    def _space_insensitive_match(self, query: str) -> Optional[DealerMatch]:
        """Space insensitive match"""
        query_no_spaces = _normalize_no_spaces(query.lower())
        for key, data in self._dealer_cache.items():
            key_no_spaces = _normalize_no_spaces(key)
            if key_no_spaces == query_no_spaces:
                return DealerMatch(
                    dealer_name=data['dealer_name'],
                    dealer_code=data.get('dealer_code', ''),
                    customer_code=data.get('customer_code', ''),
                    score=98.0,
                    match_type="space_insensitive",
                    confidence=0.98
                )
        return None
    
    def _symbol_insensitive_match(self, query: str) -> Optional[DealerMatch]:
        """Symbol insensitive match"""
        query_normalized = _normalize_text(query.lower())
        for key, data in self._dealer_cache.items():
            key_normalized = _normalize_text(key)
            if key_normalized == query_normalized:
                return DealerMatch(
                    dealer_name=data['dealer_name'],
                    dealer_code=data.get('dealer_code', ''),
                    customer_code=data.get('customer_code', ''),
                    score=97.0,
                    match_type="symbol_insensitive",
                    confidence=0.97
                )
        return None
    
    def _partial_match(self, query: str) -> Optional[DealerMatch]:
        """Partial match - query is part of dealer name"""
        query_lower = query.lower()
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            if query_lower in key:
                # Calculate confidence based on length ratio
                confidence = len(query) / len(key)
                if confidence > best_score:
                    best_score = confidence
                    best_match = data
        
        if best_match and best_score > 0.5:
            return DealerMatch(
                dealer_name=best_match['dealer_name'],
                dealer_code=best_match.get('dealer_code', ''),
                customer_code=best_match.get('customer_code', ''),
                score=best_score * 100,
                match_type="partial",
                confidence=best_score
            )
        return None
    
    def _word_match(self, query: str) -> Optional[DealerMatch]:
        """Word match - match individual words"""
        query_words = set(query.lower().split())
        if len(query_words) < 2:
            return None
        
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            key_words = set(key.split())
            common_words = query_words & key_words
            if common_words:
                score = len(common_words) / len(query_words)
                if score > best_score:
                    best_score = score
                    best_match = data
        
        if best_match and best_score > 0.5:
            return DealerMatch(
                dealer_name=best_match['dealer_name'],
                dealer_code=best_match.get('dealer_code', ''),
                customer_code=best_match.get('customer_code', ''),
                score=best_score * 100,
                match_type="word",
                confidence=best_score
            )
        return None
    
    def _alias_match(self, query: str) -> Optional[DealerMatch]:
        """Alias match - check if query is part of dealer name"""
        query_lower = query.lower()
        if len(query_lower) < 3:
            return None
        
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            if query_lower in key:
                score = len(query) / len(key)
                if score > best_score:
                    best_score = score
                    best_match = data
        
        if best_match and best_score > 0.4:
            return DealerMatch(
                dealer_name=best_match['dealer_name'],
                dealer_code=best_match.get('dealer_code', ''),
                customer_code=best_match.get('customer_code', ''),
                score=best_score * 100,
                match_type="alias",
                confidence=best_score
            )
        return None
    
    def _dealer_code_match(self, query: str) -> Optional[DealerMatch]:
        """Match by dealer code"""
        query_clean = query.strip().lower()
        for key, data in self._dealer_cache.items():
            dealer_code = data.get('dealer_code', '').lower()
            if dealer_code and dealer_code == query_clean:
                return DealerMatch(
                    dealer_name=data['dealer_name'],
                    dealer_code=data.get('dealer_code', ''),
                    customer_code=data.get('customer_code', ''),
                    score=99.0,
                    match_type="dealer_code",
                    confidence=0.99
                )
        return None
    
    def _customer_code_match(self, query: str) -> Optional[DealerMatch]:
        """Match by customer code"""
        query_clean = query.strip().lower()
        for key, data in self._dealer_cache.items():
            customer_code = data.get('customer_code', '').lower()
            if customer_code and customer_code == query_clean:
                return DealerMatch(
                    dealer_name=data['dealer_name'],
                    dealer_code=data.get('dealer_code', ''),
                    customer_code=data.get('customer_code', ''),
                    score=99.0,
                    match_type="customer_code",
                    confidence=0.99
                )
        return None
    
    def _fuzzy_match(self, query: str) -> Optional[DealerMatch]:
        """Fuzzy match using RapidFuzz (70% threshold)"""
        if not RAPIDFUZZ_AVAILABLE:
            return None
        
        query_lower = query.lower()
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            # Use WRatio for better matching
            score = fuzz.WRatio(query_lower, key)
            if score > best_score and score >= FUZZY_MATCH_THRESHOLD:
                best_score = score
                best_match = data
        
        if best_match:
            return DealerMatch(
                dealer_name=best_match['dealer_name'],
                dealer_code=best_match.get('dealer_code', ''),
                customer_code=best_match.get('customer_code', ''),
                score=best_score,
                match_type="fuzzy",
                confidence=best_score / 100.0
            )
        return None
    
    def _get_suggestions(self, query: str, limit: int = 5) -> List[DealerMatch]:
        """Get dealer suggestions for no match"""
        suggestions = []
        
        if RAPIDFUZZ_AVAILABLE:
            scored = []
            for key, data in self._dealer_cache.items():
                score = fuzz.WRatio(query.lower(), key)
                if score > 50:
                    scored.append((score, data))
            
            scored.sort(key=lambda x: x[0], reverse=True)
            
            for score, data in scored[:limit]:
                suggestions.append(
                    DealerMatch(
                        dealer_name=data['dealer_name'],
                        dealer_code=data.get('dealer_code', ''),
                        customer_code=data.get('customer_code', ''),
                        score=score,
                        match_type="suggestion",
                        confidence=score / 100.0
                    )
                )
        else:
            # Fallback to partial match
            query_lower = query.lower()
            for key, data in self._dealer_cache.items():
                if query_lower in key or key in query_lower:
                    suggestions.append(
                        DealerMatch(
                            dealer_name=data['dealer_name'],
                            dealer_code=data.get('dealer_code', ''),
                            customer_code=data.get('customer_code', ''),
                            score=70.0,
                            match_type="suggestion",
                            confidence=0.70
                        )
                    )
                    if len(suggestions) >= limit:
                        break
        
        return suggestions
    
    def _finalize_result(self, match: DealerMatch, query: str) -> DealerSearchResult:
        """Finalize the search result - load profile and build dashboard"""
        # Load full profile
        profile = self._load_dealer_profile(match.dealer_name)
        
        if not profile:
            return DealerSearchResult(
                success=False,
                message=f"Dealer '{match.dealer_name}' found but profile could not be loaded.",
                matches=[match]
            )
        
        # Build dashboard
        dashboard = self._build_dashboard(profile)
        
        return DealerSearchResult(
            success=True,
            matches=[match],
            selected_dealer=match,
            profile=profile,
            message=f"Dealer found: {match.dealer_name}",
            dashboard=dashboard
        )
    
    # ============================================================
    # DEALER PROFILE LOADER
    # ============================================================
    
    def _load_dealer_profile(self, dealer_name: str) -> Optional[DealerProfile]:
        """Load complete dealer profile from PostgreSQL"""
        session = self._get_db_session()
        if not session:
            return None
        
        try:
            # Get dealer data
            sql = f"""
                SELECT 
                    customer_name,
                    dealer_code,
                    customer_code,
                    sales_office,
                    sales_manager,
                    division,
                    ship_to_city as primary_city,
                    warehouse as primary_warehouse,
                    warehouse_code as primary_warehouse_code,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    COUNT(DISTINCT CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_no END) as pending_dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) as pending_pgi,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) as pending_pod,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered_dn,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed,
                    AVG(CASE WHEN good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as avg_delivery_days,
                    AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as avg_pod_days,
                    MIN(dn_create_date) as first_order,
                    MAX(dn_create_date) as last_order,
                    MAX(CASE WHEN pod_date IS NOT NULL THEN pod_date END) as latest_pod,
                    MAX(GREATEST(dn_create_date, good_issue_date, pod_date)) as latest_activity,
                    COUNT(DISTINCT customer_model) as product_count,
                    COUNT(DISTINCT warehouse) as warehouse_count,
                    COUNT(DISTINCT ship_to_city) as city_count
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                   OR LOWER(dealer_code) = LOWER('{dealer_name}')
                GROUP BY customer_name, dealer_code, customer_code, sales_office, 
                         sales_manager, division, ship_to_city, warehouse, warehouse_code
                ORDER BY total_revenue DESC
                LIMIT 1
            """
            result = session.execute(text(sql))
            row = result.fetchone()
            
            if not row:
                session.close()
                return None
            
            data = dict(zip(row.keys(), row))
            
            profile = DealerProfile()
            
            # Identity
            profile.dealer_name = _text(data.get('customer_name'))
            profile.dealer_code = _text(data.get('dealer_code'))
            profile.customer_code = _text(data.get('customer_code'))
            profile.sales_office = _text(data.get('sales_office'))
            profile.sales_manager = _text(data.get('sales_manager'))
            profile.division = _text(data.get('division'))
            profile.primary_city = _text(data.get('primary_city'))
            profile.primary_warehouse = _text(data.get('primary_warehouse'))
            profile.primary_warehouse_code = _text(data.get('primary_warehouse_code'))
            
            # Financial
            total_dn = int(data.get('total_dn', 0) or 0)
            profile.total_dn = total_dn
            profile.total_units = int(data.get('total_units', 0) or 0)
            profile.total_revenue = float(data.get('total_revenue', 0) or 0)
            profile.avg_revenue_per_dn = profile.total_revenue / max(1, total_dn)
            profile.avg_units_per_dn = profile.total_units / max(1, total_dn)
            
            # Operations
            profile.pending_dn = int(data.get('pending_dn', 0) or 0)
            profile.pending_pgi = int(data.get('pending_pgi', 0) or 0)
            profile.pending_pod = int(data.get('pending_pod', 0) or 0)
            profile.delivered_dn = int(data.get('delivered_dn', 0) or 0)
            
            # Delivery
            profile.delivery_pct = _percent(profile.delivered_dn, total_dn)
            pgi_completed = int(data.get('pgi_completed', 0) or 0)
            pod_completed = int(data.get('pod_completed', 0) or 0)
            profile.pgi_pct = _percent(pgi_completed, total_dn)
            profile.pod_pct = _percent(pod_completed, total_dn)
            profile.avg_delivery_days = float(data.get('avg_delivery_days', 0) or 0)
            profile.avg_pod_days = float(data.get('avg_pod_days', 0) or 0)
            
            # Products
            profile.product_count = int(data.get('product_count', 0) or 0)
            profile.warehouse_count = int(data.get('warehouse_count', 0) or 0)
            profile.city_count = int(data.get('city_count', 0) or 0)
            
            # Timeline
            profile.first_order = _date_text(data.get('first_order'))
            profile.last_order = _date_text(data.get('last_order'))
            profile.latest_pod_date = _date_text(data.get('latest_pod'))
            profile.latest_activity = _date_text(data.get('latest_activity'))
            
            # Get top product
            product_sql = f"""
                SELECT customer_model, COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND customer_model IS NOT NULL
                GROUP BY customer_model
                ORDER BY dn_count DESC
                LIMIT 1
            """
            product_result = session.execute(text(product_sql))
            product_row = product_result.fetchone()
            if product_row:
                profile.top_product = _text(product_row[0])
                profile.top_model = _text(product_row[0])
            
            # Get warehouses used
            wh_sql = f"""
                SELECT DISTINCT warehouse
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND warehouse IS NOT NULL
                ORDER BY warehouse
            """
            wh_result = session.execute(text(wh_sql))
            profile.warehouses_used = [_text(row[0]) for row in wh_result.fetchall()]
            
            # Get cities served
            city_sql = f"""
                SELECT DISTINCT ship_to_city
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER('{dealer_name}')
                AND ship_to_city IS NOT NULL
                ORDER BY ship_to_city
            """
            city_result = session.execute(text(city_sql))
            profile.cities_served = [_text(row[0]) for row in city_result.fetchall()]
            
            session.close()
            
            # Calculate business score
            score = (
                profile.delivery_pct * 0.30 +
                profile.pod_pct * 0.20 +
                (100 - _percent(profile.pending_dn, total_dn)) * 0.20 +
                min(100, profile.total_revenue / 1000000) * 0.15 +
                min(100, profile.warehouse_count * 10) * 0.15
            )
            profile.business_score = round(min(100, max(0, score)), 1)
            profile.risk_score = round(100 - profile.business_score, 1)
            
            # Generate insights
            profile.insights = self._generate_insights(profile)
            profile.recommendations = self._generate_recommendations(profile)
            
            # Calculate rankings
            self._calculate_rankings(profile)
            
            return profile
            
        except Exception as e:
            logger.error(f"Failed to load dealer profile: {e}")
            if session:
                session.close()
            return None
    
    def _calculate_rankings(self, profile: DealerProfile):
        """Calculate dealer rankings"""
        session = self._get_db_session()
        if not session:
            return
        
        try:
            sql = """
                SELECT 
                    customer_name,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COUNT(DISTINCT dn_no) as dn_count,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered
                FROM delivery_reports
                WHERE customer_name IS NOT NULL
                GROUP BY customer_name
            """
            result = session.execute(text(sql))
            rows = result.fetchall()
            session.close()
            
            all_dealers = []
            for row in rows:
                all_dealers.append({
                    'name': _text(row[0]),
                    'revenue': float(row[1] or 0),
                    'dn': int(row[2] or 0),
                    'units': float(row[3] or 0),
                    'delivered': int(row[4] or 0)
                })
            
            if not all_dealers:
                return
            
            dealer_name = profile.dealer_name
            
            # Revenue rank
            sorted_by_revenue = sorted(all_dealers, key=lambda x: x['revenue'], reverse=True)
            for i, d in enumerate(sorted_by_revenue, 1):
                if d['name'] == dealer_name:
                    profile.revenue_rank = i
                    break
            
            # Unit rank
            sorted_by_units = sorted(all_dealers, key=lambda x: x['units'], reverse=True)
            for i, d in enumerate(sorted_by_units, 1):
                if d['name'] == dealer_name:
                    profile.unit_rank = i
                    break
            
            # DN rank
            sorted_by_dn = sorted(all_dealers, key=lambda x: x['dn'], reverse=True)
            for i, d in enumerate(sorted_by_dn, 1):
                if d['name'] == dealer_name:
                    profile.dn_rank = i
                    break
            
            # Delivery rank
            sorted_by_delivery = sorted(all_dealers, key=lambda x: x['delivered'] / max(1, x['dn']), reverse=True)
            for i, d in enumerate(sorted_by_delivery, 1):
                if d['name'] == dealer_name:
                    profile.delivery_rank = i
                    break
            
            # Overall rank
            rank_sum = profile.revenue_rank + profile.unit_rank + profile.dn_rank + profile.delivery_rank
            profile.overall_rank = int(rank_sum / 4)
            
        except Exception as e:
            logger.error(f"Failed to calculate rankings: {e}")
            if session:
                session.close()
    
    def _generate_insights(self, profile: DealerProfile) -> List[str]:
        """Generate insights from profile"""
        insights = []
        
        if profile.total_revenue > 10000000:
            insights.append(f"High revenue performer: {_format_currency(profile.total_revenue)}")
        
        if profile.delivery_pct >= 95:
            insights.append("Excellent delivery performance")
        elif profile.delivery_pct < 80:
            insights.append("Delivery performance needs improvement")
        
        if profile.warehouse_count > 3:
            insights.append(f"Strong warehouse network: {profile.warehouse_count} warehouses")
        
        if profile.product_count > 10:
            insights.append(f"Wide product portfolio: {profile.product_count} products")
        
        if profile.business_score >= 85:
            insights.append("Excellent overall business health")
        elif profile.business_score < 50:
            insights.append("Critical business health - immediate action required")
        
        if not insights:
            insights.append("Performance is stable. Continue monitoring.")
        
        return insights[:5]
    
    def _generate_recommendations(self, profile: DealerProfile) -> List[str]:
        """Generate recommendations from profile"""
        recommendations = []
        
        if profile.delivery_pct < 85:
            recommendations.append("📦 Improve delivery speed and reliability")
        
        if profile.pending_dn > 20:
            recommendations.append(f"⏳ Escalate {profile.pending_dn} pending DNs for resolution")
        
        if profile.product_count < 5:
            recommendations.append("🛒 Expand product portfolio to increase revenue")
        
        if profile.warehouse_count == 1:
            recommendations.append("🏭 Consider diversifying warehouse coverage")
        
        if profile.city_count < 3:
            recommendations.append("🌍 Expand to new cities for growth")
        
        if profile.business_score < 70:
            recommendations.append("📊 Develop action plan to improve business score")
        
        if not recommendations:
            recommendations.append("✅ Maintain current performance levels")
            recommendations.append("📊 Continue monitoring key metrics")
        
        return recommendations[:5]
    
    # ============================================================
    # DASHBOARD BUILDER
    # ============================================================
    
    def _build_dashboard(self, profile: DealerProfile) -> str:
        """Build professional WhatsApp dashboard"""
        if not profile:
            return "⚠️ Dealer profile not available."
        
        lines = []
        
        # Header
        lines.append("=" * 50)
        lines.append(f"🏢 *DEALER DASHBOARD*")
        lines.append("=" * 50)
        lines.append("")
        
        # Identity
        lines.append("📌 *Identity*")
        lines.append(f"Name: {profile.dealer_name}")
        if profile.dealer_code and profile.dealer_code != "N/A":
            lines.append(f"Code: {profile.dealer_code}")
        if profile.customer_code and profile.customer_code != "N/A":
            lines.append(f"Customer Code: {profile.customer_code}")
        if profile.sales_office and profile.sales_office != "N/A":
            lines.append(f"Office: {profile.sales_office}")
        if profile.sales_manager and profile.sales_manager != "N/A":
            lines.append(f"Manager: {profile.sales_manager}")
        if profile.division and profile.division != "N/A":
            lines.append(f"Division: {profile.division}")
        lines.append("")
        
        # Location
        lines.append("📍 *Location*")
        if profile.primary_warehouse and profile.primary_warehouse != "N/A":
            lines.append(f"Warehouse: {profile.primary_warehouse}")
        if profile.primary_warehouse_code and profile.primary_warehouse_code != "N/A":
            lines.append(f"Warehouse Code: {profile.primary_warehouse_code}")
        if profile.primary_city and profile.primary_city != "N/A":
            lines.append(f"City: {profile.primary_city}")
        lines.append("")
        
        # Financial
        lines.append("💰 *Financials*")
        lines.append(f"Revenue: {_format_currency(profile.total_revenue)}")
        lines.append(f"Avg Revenue/DN: {_format_currency(profile.avg_revenue_per_dn)}")
        lines.append(f"Total Units: {_format_number(profile.total_units)}")
        lines.append(f"Avg Units/DN: {profile.avg_units_per_dn:.1f}")
        lines.append("")
        
        # Operations
        lines.append("📦 *Operations*")
        lines.append(f"Total DN: {_format_number(profile.total_dn)}")
        lines.append(f"Pending DN: {_format_number(profile.pending_dn)}")
        lines.append(f"Pending PGI: {_format_number(profile.pending_pgi)}")
        lines.append(f"Pending POD: {_format_number(profile.pending_pod)}")
        lines.append(f"Delivered DN: {_format_number(profile.delivered_dn)}")
        lines.append("")
        
        # Delivery
        lines.append("🚚 *Delivery*")
        lines.append(f"Delivery Success: {profile.delivery_pct:.1f}%")
        lines.append(f"PGI Success: {profile.pgi_pct:.1f}%")
        lines.append(f"POD Success: {profile.pod_pct:.1f}%")
        lines.append(f"Avg Delivery Days: {profile.avg_delivery_days:.1f}")
        lines.append(f"Avg POD Days: {profile.avg_pod_days:.1f}")
        lines.append("")
        
        # Products
        lines.append("🏷️ *Products*")
        lines.append(f"Total Products: {_format_number(profile.product_count)}")
        if profile.top_product and profile.top_product != "N/A":
            lines.append(f"Top Product: {profile.top_product}")
        if profile.top_model and profile.top_model != "N/A":
            lines.append(f"Top Model: {profile.top_model}")
        lines.append("")
        
        # Warehouses
        lines.append("🏭 *Warehouses*")
        lines.append(f"Warehouses: {_format_number(profile.warehouse_count)}")
        if profile.warehouses_used:
            display = profile.warehouses_used[:3]
            lines.append(f"Used: {', '.join(display)}")
            if len(profile.warehouses_used) > 3:
                lines.append(f"... and {len(profile.warehouses_used) - 3} more")
        lines.append("")
        
        # Cities
        lines.append("🏙️ *Cities*")
        lines.append(f"Cities Served: {_format_number(profile.city_count)}")
        if profile.cities_served:
            display = profile.cities_served[:3]
            lines.append(f"Served: {', '.join(display)}")
            if len(profile.cities_served) > 3:
                lines.append(f"... and {len(profile.cities_served) - 3} more")
        lines.append("")
        
        # Distance
        if profile.distance_km > 0:
            lines.append("📍 *Distance*")
            lines.append(f"Distance: {profile.distance_km:.1f} km")
            lines.append(f"Travel Time: {profile.travel_time_minutes} min")
            lines.append(f"Zone: {profile.transport_zone}")
            lines.append("")
        
        # Rankings
        lines.append("🏆 *Rankings*")
        if profile.revenue_rank > 0:
            lines.append(f"Revenue Rank: #{profile.revenue_rank}")
        if profile.unit_rank > 0:
            lines.append(f"Units Rank: #{profile.unit_rank}")
        if profile.dn_rank > 0:
            lines.append(f"DN Rank: #{profile.dn_rank}")
        if profile.delivery_rank > 0:
            lines.append(f"Delivery Rank: #{profile.delivery_rank}")
        if profile.overall_rank > 0:
            lines.append(f"Overall Rank: #{profile.overall_rank}")
        lines.append("")
        
        # Scores
        lines.append("📊 *Scores*")
        lines.append(f"Business Score: {profile.business_score:.1f}/100")
        lines.append(f"Risk Score: {profile.risk_score:.1f}/100")
        lines.append("")
        
        # Timeline
        lines.append("📅 *Timeline*")
        if profile.first_order and profile.first_order != "N/A":
            lines.append(f"First Order: {profile.first_order}")
        if profile.last_order and profile.last_order != "N/A":
            lines.append(f"Last Order: {profile.last_order}")
        if profile.latest_pod_date and profile.latest_pod_date != "N/A":
            lines.append(f"Latest POD: {profile.latest_pod_date}")
        if profile.latest_activity and profile.latest_activity != "N/A":
            lines.append(f"Latest Activity: {profile.latest_activity}")
        lines.append("")
        
        # Insights
        if profile.insights:
            lines.append("💡 *Insights*")
            for insight in profile.insights:
                lines.append(f"• {insight}")
            lines.append("")
        
        # Recommendations
        if profile.recommendations:
            lines.append("🎯 *Recommendations*")
            for rec in profile.recommendations:
                lines.append(f"• {rec}")
            lines.append("")
        
        # Footer
        lines.append("=" * 50)
        lines.append("")
        lines.append("99️⃣ Main Menu")
        
        return "\n".join(lines)
    
    # ============================================================
    # PUBLIC API - MAIN ENTRY POINT
    # ============================================================
    
    def search_dealer(self, query: str) -> DealerSearchResult:
        """
        MAIN ENTRY POINT - Search for a dealer
        
        This is the ONLY external method.
        """
        if not query or not query.strip():
            return DealerSearchResult(
                success=False,
                message="Please enter a dealer name."
            )
        
        return self.detect_dealer(query)
    
    def get_welcome_message(self) -> str:
        """Get welcome message for the service"""
        return "\n".join([
            "=" * 50,
            "🏢 *DEALER SEARCH*",
            "",
            "Please type the Dealer Name.",
            "",
            "📌 *Examples:*",
            "• Zoom Appliances",
            "• Arshad Electronics-Khi",
            "• RUBA Digital",
            "• Metro Electronics",
            "• Friends Electronics",
            "• Al Madina Electronics",
            "",
            "Type the Dealer Name to continue.",
            "",
            "99️⃣ Main Menu",
            "=" * 50
        ])
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for the service"""
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "dealers_in_cache": len(self._dealer_cache),
            "fuzzy_threshold": FUZZY_MATCH_THRESHOLD,
            "rapidfuzz_available": RAPIDFUZZ_AVAILABLE,
            "exit_command": "99",
            "timestamp": datetime.now().isoformat()
        }


# ============================================================
# BLOCK 7: SERVICE SINGLETON
# ============================================================

_service: Optional[DealerSearchEngine] = None
_service_lock = threading.Lock()

def get_dealer_search_engine() -> DealerSearchEngine:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DealerSearchEngine()
    return _service


# ============================================================
# BLOCK 8: MODULE EXPORTS
# ============================================================

__all__ = [
    "DealerSearchEngine",
    "DealerSearchResult",
    "DealerMatch",
    "DealerProfile",
    "get_dealer_search_engine",
]
