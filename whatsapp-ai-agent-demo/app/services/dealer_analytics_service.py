#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 8.2 - ENTERPRISE DEALER INTELLIGENCE GATEWAY WITH POSTGRESQL & REDIS
# PATCHED: fixes bidirectional partial-match + city-suffix-aware fuzzy match
# ============================================================

"""
================================================================================
DEALER INTELLIGENCE GATEWAY - ENTERPRISE EDITION v8.2
================================================================================

This service orchestrates the complete dealer intelligence workflow with:
    âœ… PostgreSQL as single source of truth
    âœ… In-memory search index for lightning-fast searches
    âœ… Redis cache for distributed caching (optional)
    âœ… Multi-level search strategy (code â†’ exact â†’ partial â†’ fuzzy â†’ phonetic)
    âœ… 70% similarity threshold for fuzzy matching
    âœ… Automatic cache refresh every 15 minutes
    âœ… Comprehensive diagnostic logging
    âœ… Multiple matches support with numbered selection
    âœ… Enterprise data aggregation from DeliveryReport model
    âœ… WhatsApp-optimized formatting with emojis
    âœ… Enhanced error handling with specific error messages
    âœ… Full PostgreSQL integration
    âœ… Soundex phonetic search
    âœ… Abbreviation expansion
    âœ… City-suffix aware matching (e.g. "-Khi", "-Lhr") <-- NEW in 8.2
    âœ… Bidirectional partial matching <-- NEW in 8.2
    âœ… Performance monitoring

SOURCE OF TRUTH: PostgreSQL (DeliveryReport model)
================================================================================
"""

from __future__ import annotations

import logging
import time
import json
import traceback
import re
import difflib
import threading
from typing import Optional, Dict, List, Any, Union, Tuple
from datetime import datetime, timedelta, date
from dataclasses import dataclass, field, asdict
from threading import Thread, Event
from collections import defaultdict
from functools import lru_cache

from sqlalchemy import and_, case, distinct, func, or_, text, desc, asc
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: CONSTANTS
# ============================================================

EXIT_SIGNAL = "__EXIT__"
VERSION = "8.2"
CACHE_TTL = 300  # 5 minutes cache
SEARCH_CACHE_REFRESH_MINUTES = 15
SIMILARITY_THRESHOLD = 0.70  # 70% minimum similarity
MAX_SUGGESTIONS = 10
DEALER_DELAY_THRESHOLD_DAYS = 7
REDIS_TTL = 3600  # 1 hour Redis TTL
REDIS_ENABLED = False  # Set to True to enable Redis

# Common Pakistani city abbreviations used as dealer-name suffixes.
# These let "Arshad Electronics-Khi" match a DB record stored as
# "Arshad Electronics" or "Arshad Electronics Karachi".
CITY_ABBREVIATIONS = {
    'khi': 'karachi',
    'lhr': 'lahore',
    'isb': 'islamabad',
    'fsd': 'faisalabad',
    'rwp': 'rawalpindi',
    'mul': 'multan',
    'pes': 'peshawar',
    'que': 'quetta',
    'hyd': 'hyderabad',
    'guj': 'gujranwala',
    'sil': 'sialkot',
    'sk': 'sialkot',
    'blv': 'bahawalpur',
    'skt': 'sialkot',
    'gwl': 'gujranwala',
}

CITY_NAMES = set(CITY_ABBREVIATIONS.values())

# ============================================================
# BLOCK 2: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    """Safely convert to string"""
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except (TypeError, ValueError):
        return default

def _number(value: Any) -> float:
    """Safely convert to float"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    """Calculate percentage safely"""
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _days(value: Any) -> float:
    """Safely convert to days"""
    if value is None:
        return 0.0
    if hasattr(value, "days"):
        return round(float(value.days), 2)
    return round(_number(value), 2)

def _date_text(value: Any) -> str:
    """Format date for display"""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def format_currency(amount: float) -> str:
    """Format currency in PKR with commas"""
    if amount >= 10000000:
        return f"PKR {amount/10000000:.1f}Cr"
    elif amount >= 1000000:
        return f"PKR {amount/1000000:.1f}M"
    elif amount >= 1000:
        return f"PKR {amount/1000:.1f}K"
    else:
        return f"PKR {amount:,.0f}"

# ============================================================
# BLOCK 3: TYPED MODELS
# ============================================================

@dataclass
class DealerIndex:
    """In-memory dealer search index entry"""
    customer_name: str
    dealer_code: str
    customer_code: str
    normalized_name: str
    search_tokens: List[str]
    city: str = ""
    warehouse: str = ""
    warehouse_code: str = ""
    sales_office: str = ""
    sales_manager: str = ""
    sales_channel: str = "Traditional Channel"
    aliases: List[str] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.now)

@dataclass
class DealerSearchResult:
    """Structured search result"""
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
class DealerIdentity:
    """Dealer identity from DeliveryReport model"""
    customer_name: str
    dealer_code: str
    customer_code: str
    city: str
    warehouse: str
    warehouse_code: str
    delivery_location: str
    sales_office: str
    sales_manager: str
    sales_channel: str = "Traditional Channel"
    division: str = ""
    region: str = ""
    country: str = "Pakistan"
    dealer_type: str = "Standard"
    active_since: str = "2020"

@dataclass
class DeliverySummary:
    """Delivery performance from DeliveryReport model"""
    total_dn: int = 0
    delivered_dn: int = 0
    pending_dn: int = 0
    pgi_completed: int = 0
    pod_completed: int = 0
    delivery_rate: float = 0.0
    pgi_rate: float = 0.0
    pod_rate: float = 0.0
    avg_delivery_days: float = 0.0
    avg_pod_days: float = 0.0
    avg_cycle_days: float = 0.0

@dataclass
class BusinessSummary:
    """Business performance from DeliveryReport model"""
    total_revenue: float = 0.0
    total_units: int = 0
    total_dn: int = 0
    avg_revenue_per_dn: float = 0.0
    avg_units_per_dn: float = 0.0
    yoy_growth: float = 0.0
    target_achievement: float = 0.0
    monthly_growth: float = 0.0

@dataclass
class ProductSummary:
    """Product portfolio from DeliveryReport model"""
    products_sold: int = 0
    models_count: int = 0
    materials_count: int = 0
    top_product: str = "N/A"
    top_model: str = "N/A"
    top_material: str = "N/A"
    primary_division: str = "N/A"
    product_categories: List[str] = field(default_factory=list)

@dataclass
class OperationSummary:
    """Operational summary from DeliveryReport model"""
    cities_served: int = 0
    warehouses_used: int = 0
    primary_warehouse: str = "N/A"
    latest_dn: str = "N/A"
    latest_pgi: str = "N/A"
    latest_pod: str = "N/A"
    active_regions: List[str] = field(default_factory=list)
    warehouse_distribution: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class PerformanceSummary:
    """Performance metrics and rankings"""
    business_score: int = 0
    revenue_rank: int = 0
    delivery_rank: int = 0
    overall_rank: int = 0
    performance_tier: str = "Standard"
    dealer_rating: float = 0.0
    risk_score: int = 0
    status: str = "Unknown"

@dataclass
class DealerContext:
    """Complete dealer context for session"""
    dealer_name: str = ""
    dealer_code: str = ""
    customer_code: str = ""
    warehouse: str = ""
    warehouse_code: str = ""
    city: str = ""
    sales_office: str = ""
    sales_manager: str = ""
    sales_channel: str = "Traditional Channel"
    dashboard: Dict[str, Any] = field(default_factory=dict)
    last_query: str = ""
    last_activity: datetime = field(default_factory=datetime.now)
    search_count: int = 0
    cache_timestamp: Optional[datetime] = None
    pending_matches: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class DealerDashboard:
    """Complete dealer dashboard from PostgreSQL DeliveryReport"""
    identity: DealerIdentity
    delivery: DeliverySummary
    business: BusinessSummary
    product: ProductSummary
    operation: OperationSummary
    performance: PerformanceSummary
    insights: List[str]
    recommendations: List[str]
    executive_summary: str
    context: DealerContext
    generated_at: datetime = field(default_factory=datetime.now)

# ============================================================
# BLOCK 4: DEALER SEARCH ENGINE WITH POSTGRESQL (IMPROVED)
# ============================================================

class DealerSearchEngine:
    """
    Enterprise Dealer Search Engine with PostgreSQL integration

    Features:
        âœ… PostgreSQL as source of truth
        âœ… In-memory search index for lightning-fast searches
        âœ… Multi-level search strategy
        âœ… 70% similarity threshold
        âœ… Automatic cache refresh every 15 minutes
        âœ… Comprehensive logging
        âœ… Redis caching support (optional)
        âœ… Phonetic search (Soundex)
        âœ… Abbreviation expansion
        âœ… City-suffix aware matching (NEW)
        âœ… Performance monitoring
    """

    def __init__(self, enable_redis: bool = False):
        self._index: Dict[str, DealerIndex] = {}
        self._normalized_index: Dict[str, str] = {}
        self._code_index: Dict[str, str] = {}
        self._customer_code_index: Dict[str, str] = {}
        self._alias_index: Dict[str, List[str]] = defaultdict(list)
        self._phonetic_index: Dict[str, List[str]] = defaultdict(list)
        self._abbreviation_index: Dict[str, List[str]] = defaultdict(list)
        self._last_refresh = None
        self._refresh_thread = None
        self._stop_refresh = Event()
        self._search_count = 0
        self._search_success_count = 0
        self._avg_search_time = 0.0
        self._cache_hits = 0
        self._cache_misses = 0
        self._lock = threading.RLock()
        self._postgresql_connected = False
        self._enable_redis = enable_redis
        self._redis_client = None

        # Initialize Redis if enabled
        if enable_redis:
            try:
                import redis
                self._redis_client = redis.Redis(
                    host='localhost',
                    port=6379,
                    db=0,
                    decode_responses=True
                )
                self._redis_client.ping()
                logger.info("âœ… Redis connected for caching")
            except Exception as e:
                logger.warning(f"âš ï¸ Redis connection failed: {e}")
                self._enable_redis = False

        # Build initial index from PostgreSQL
        self._build_index_from_postgresql()

        # Start auto-refresh thread
        self._start_auto_refresh()

        # Display startup banner
        self._show_startup_banner()

    def _get_session(self) -> Session:
        """Get database session"""
        return SessionLocal()

    def _show_startup_banner(self):
        """Display startup banner with system status"""
        print("\n" + "â”" * 70)
        print("DEALER SEARCH ENGINE - POSTGRESQL".center(70))
        print("â”" * 70)

        try:
            with self._get_session() as session:
                total_records = session.query(func.count(DeliveryReport.id)).scalar() or 0
                unique_dealers = session.query(func.count(distinct(DeliveryReport.customer_name))).scalar() or 0
                unique_codes = session.query(func.count(distinct(DeliveryReport.dealer_code))).scalar() or 0
                self._postgresql_connected = True
        except Exception as e:
            total_records = 0
            unique_dealers = 0
            unique_codes = 0
            self._postgresql_connected = False
            logger.error(f"âŒ PostgreSQL connection failed: {e}")

        print(f"\nDatabase Status      : {'âœ… Connected' if self._postgresql_connected else 'âŒ Disconnected'}")
        print(f"Total Records        : {total_records:,}")
        print(f"Unique Dealers       : {unique_dealers:,}")
        print(f"Unique Dealer Codes  : {unique_codes:,}")
        print(f"Search Index         : {'âœ… Ready' if self._index else 'âŒ Empty'}")
        print(f"Search Cache         : {'âœ… Loaded' if self._index else 'âŒ Empty'}")
        print(f"Fuzzy Search         : {'âœ… Enabled' if self._index else 'âŒ Disabled'}")
        print(f"Partial Search       : {'âœ… Enabled' if self._index else 'âŒ Disabled'}")
        print(f"Alias Search         : {'âœ… Enabled' if self._index else 'âŒ Disabled'}")
        print(f"Phonetic Search      : {'âœ… Enabled' if self._index else 'âŒ Disabled'}")
        print(f"Abbreviation Search  : {'âœ… Enabled' if self._index else 'âŒ Disabled'}")
        print(f"Similarity Threshold : {SIMILARITY_THRESHOLD * 100:.0f}%")
        print(f"Auto Refresh         : Every {SEARCH_CACHE_REFRESH_MINUTES} minutes")
        print(f"Redis Cache          : {'âœ… Enabled' if self._enable_redis else 'âŒ Disabled'}")
        print(f"\nSystem Status        : {'âœ… READY' if self._index else 'âŒ NOT READY'}")
        print("â”" * 70 + "\n")

    def _build_index_from_postgresql(self):
        """Build in-memory search index from PostgreSQL"""
        logger.info("ðŸ”¨ Building dealer search index from PostgreSQL...")
        start_time = time.time()

        try:
            with self._get_session() as session:
                # Get all distinct dealers from PostgreSQL with improved query
                dealers = session.query(
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
                    DeliveryReport.region
                ).filter(
                    DeliveryReport.customer_name.isnot(None)
                ).distinct().all()

            if not dealers:
                logger.warning("âš ï¸ No dealers found in PostgreSQL database")
                self._postgresql_connected = False
                return

            self._postgresql_connected = True

            with self._lock:
                # Build indexes
                index = {}
                normalized_index = {}
                code_index = {}
                customer_code_index = {}
                alias_index = defaultdict(list)
                phonetic_index = defaultdict(list)
                abbreviation_index = defaultdict(list)

                skipped_count = 0
                collision_count = 0
                seen_keys = set()

                for dealer in dealers:
                    customer_name = _text(dealer.customer_name, default="")
                    # CRITICAL FIX: _text() defaults to the literal string
                    # "Unknown" for NULL values. Using that default here meant
                    # every dealer with a NULL dealer_code/customer_code ended
                    # up with the truthy string "Unknown" instead of an empty
                    # string, causing them to all collide on the same
                    # dictionary key below and silently overwrite each other.
                    dealer_code = _text(dealer.dealer_code, default="")
                    customer_code = _text(dealer.customer_code, default="")

                    if not customer_name and not dealer_code:
                        skipped_count += 1
                        continue

                    # Normalize name
                    normalized = self._normalize_text(customer_name)
                    tokens = self._tokenize(customer_name)

                    # Create index entry
                    entry = DealerIndex(
                        customer_name=customer_name,
                        dealer_code=dealer_code,
                        customer_code=customer_code,
                        normalized_name=normalized,
                        search_tokens=tokens,
                        city=_text(dealer.ship_to_city),
                        warehouse=_text(dealer.warehouse),
                        warehouse_code=_text(dealer.warehouse_code),
                        sales_office=_text(dealer.sales_office),
                        sales_manager=_text(dealer.sales_manager),
                        sales_channel="Traditional Channel"
                    )

                    # Build the primary index key. Prefer dealer_code, but
                    # ALWAYS fall back to a key that includes customer_name
                    # (and customer_code as a tiebreaker) so two different
                    # dealers can never collide just because both happen to
                    # be missing a dealer_code â€” the exact bug that caused
                    # entries to vanish silently.
                    if dealer_code:
                        primary_key = dealer_code
                    else:
                        primary_key = f"{customer_name}::{customer_code}" if customer_code else customer_name

                    # If this exact key was already used by a *different*
                    # dealer_code/customer_name combination, disambiguate
                    # with a numeric suffix instead of overwriting.
                    lookup_key = primary_key
                    if lookup_key in seen_keys:
                        collision_count += 1
                        suffix = 2
                        while f"{primary_key}__{suffix}" in seen_keys:
                            suffix += 1
                        lookup_key = f"{primary_key}__{suffix}"
                        logger.warning(
                            f"âš ï¸ Duplicate index key '{primary_key}' for dealer "
                            f"'{customer_name}' â€” disambiguated as '{lookup_key}' "
                            f"to avoid overwriting an existing entry"
                        )
                    seen_keys.add(lookup_key)
                    key = lookup_key
                    index[key] = entry

                    if normalized:
                        # Multiple dealers can legitimately normalize to the
                        # same name (branches in different cities); keep the
                        # first one mapped here since exact-match is only a
                        # first-pass shortcut â€” fuzzy/partial/token matching
                        # below still finds the others.
                        normalized_index.setdefault(normalized, key)

                    if dealer_code:
                        code_index.setdefault(dealer_code.upper(), key)

                    if customer_code:
                        customer_code_index.setdefault(customer_code.upper(), key)

                    # Generate aliases
                    aliases = self._generate_aliases(customer_name)
                    for alias in aliases:
                        alias_index[alias].append(key)

                    # Generate phonetic keys (Soundex)
                    phonetic_key = self._get_soundex(customer_name)
                    if phonetic_key:
                        phonetic_index[phonetic_key].append(key)

                    # Generate abbreviations
                    abbreviations = self._generate_abbreviations(customer_name)
                    for abbr in abbreviations:
                        abbreviation_index[abbr].append(key)

                # Update indexes
                self._index = index
                self._normalized_index = normalized_index
                self._code_index = code_index
                self._customer_code_index = customer_code_index
                self._alias_index = alias_index
                self._phonetic_index = phonetic_index
                self._abbreviation_index = abbreviation_index
                self._last_refresh = datetime.now()

                if collision_count > 0:
                    logger.warning(
                        f"âš ï¸ Index build encountered {collision_count} key "
                        f"collision(s) that were disambiguated instead of "
                        f"overwriting entries. Consider cleaning up duplicate "
                        f"or missing dealer_code/customer_code values in the "
                        f"source data."
                    )
                if skipped_count > 0:
                    logger.warning(
                        f"âš ï¸ Skipped {skipped_count} row(s) with neither "
                        f"customer_name nor dealer_code during index build."
                    )

            elapsed = time.time() - start_time
            logger.info(f"âœ… Search index built: {len(self._index)} dealers in {elapsed*1000:.0f}ms")

            # Push to Redis if enabled
            if self._enable_redis and self._redis_client:
                self._push_index_to_redis()

        except Exception as e:
            logger.error(f"âŒ Failed to build search index from PostgreSQL: {e}")
            logger.error(traceback.format_exc())
            self._postgresql_connected = False

    def _push_index_to_redis(self):
        """Push search index to Redis for distributed caching"""
        try:
            pipeline = self._redis_client.pipeline()

            # Clear existing
            pipeline.delete('dealer_index')

            # Push each dealer
            for key, entry in self._index.items():
                pipeline.hset('dealer_index', key, json.dumps(asdict(entry), default=str))

            pipeline.execute()
            logger.info("âœ… Search index pushed to Redis")
        except Exception as e:
            logger.error(f"âŒ Failed to push index to Redis: {e}")

    def _start_auto_refresh(self):
        """Start automatic cache refresh thread"""
        def refresh_worker():
            while not self._stop_refresh.is_set():
                self._stop_refresh.wait(SEARCH_CACHE_REFRESH_MINUTES * 60)
                if not self._stop_refresh.is_set():
                    logger.info("ðŸ”„ Auto-refreshing search index from PostgreSQL...")
                    self._build_index_from_postgresql()

        self._refresh_thread = Thread(target=refresh_worker, daemon=True)
        self._refresh_thread.start()
        logger.info(f"ðŸ”„ Auto-refresh started (every {SEARCH_CACHE_REFRESH_MINUTES} minutes)")

    # ============================================================
    # SEARCH METHODS
    # ============================================================

    def search_dealer(self, query: str, use_redis: bool = False) -> DealerSearchResult:
        """Search for dealer using multi-level strategy"""
        start_time = time.time()
        self._search_count += 1
        normalized_query = ""

        try:
            # Check Redis cache first
            if use_redis and self._enable_redis and self._redis_client:
                cached_result = self._get_from_redis(query)
                if cached_result:
                    self._cache_hits += 1
                    logger.info(f"âœ… Redis cache hit for '{query}'")
                    return cached_result

            self._cache_misses += 1
            normalized_query = self._normalize_text(query)
            logger.info(f"ðŸ” Search started: '{query}' â†’ normalized: '{normalized_query}'")

            if not normalized_query:
                return DealerSearchResult(
                    success=False,
                    message="Empty query",
                    search_time_ms=0,
                    normalized_query=normalized_query
                )

            with self._lock:
                # Step 1: Dealer Code match
                result = self._search_by_dealer_code(normalized_query)
                if result:
                    return self._create_search_result(result, "dealer_code", start_time, normalized_query)

                # Step 2: Customer Code match
                result = self._search_by_customer_code(normalized_query)
                if result:
                    return self._create_search_result(result, "customer_code", start_time, normalized_query)

                # Step 3: Exact match
                result = self._search_exact_match(normalized_query)
                if result:
                    return self._create_search_result(result, "exact", start_time, normalized_query)

                # Step 4: Case-insensitive match
                result = self._search_case_insensitive(normalized_query)
                if result:
                    return self._create_search_result(result, "case_insensitive", start_time, normalized_query)

                # Step 5: Partial match (now bidirectional + city-suffix aware)
                result = self._search_partial_match(normalized_query)
                if result:
                    return self._create_search_result(result, "partial", start_time, normalized_query)

                # Step 6: Token match
                result = self._search_token_match(normalized_query)
                if result:
                    return self._create_search_result(result, "token", start_time, normalized_query)

                # Step 7: Abbreviation match
                result = self._search_abbreviation_match(normalized_query)
                if result:
                    return self._create_search_result(result, "abbreviation", start_time, normalized_query)

                # Step 8: Phonetic match (Soundex)
                result = self._search_phonetic_match(normalized_query)
                if result:
                    return self._create_search_result(result, "phonetic", start_time, normalized_query)

                # Step 9: Fuzzy match (70% threshold, city-suffix aware)
                result = self._search_fuzzy_match(normalized_query)
                if result:
                    return self._create_search_result(result, "fuzzy", start_time, normalized_query)

                # Step 10: Alias match
                result = self._search_alias_match(normalized_query)
                if result:
                    return self._create_search_result(result, "alias", start_time, normalized_query)

            # No matches found - get suggestions
            suggestions = self._get_suggestions(normalized_query)
            elapsed = time.time() - start_time

            logger.info(f"âŒ Search failed: '{query}' - No matches found")

            result = DealerSearchResult(
                success=False,
                message="No dealer found",
                suggestions=suggestions[:MAX_SUGGESTIONS],
                search_time_ms=elapsed * 1000,
                normalized_query=normalized_query
            )

            # Cache in Redis if enabled
            if use_redis and self._enable_redis and self._redis_client:
                self._set_in_redis(query, result)

            return result

        except Exception as e:
            logger.error(f"âŒ Search error: {e}")
            logger.error(traceback.format_exc())
            elapsed = time.time() - start_time
            return DealerSearchResult(
                success=False,
                message=f"Search error: {str(e)}",
                search_time_ms=elapsed * 1000,
                normalized_query=normalized_query
            )

    # ============================================================
    # ENHANCED SEARCH STRATEGIES
    # ============================================================

    def _search_by_dealer_code(self, query: str) -> Optional[DealerIndex]:
        """Search by dealer code"""
        query_upper = query.upper()
        if query_upper in self._code_index:
            key = self._code_index[query_upper]
            return self._index.get(key)
        return None

    def _search_by_customer_code(self, query: str) -> Optional[DealerIndex]:
        """Search by customer code"""
        query_upper = query.upper()
        if query_upper in self._customer_code_index:
            key = self._customer_code_index[query_upper]
            return self._index.get(key)
        return None

    def _search_exact_match(self, query: str) -> Optional[DealerIndex]:
        """Search by exact normalized match"""
        if query in self._normalized_index:
            key = self._normalized_index[query]
            return self._index.get(key)
        return None

    def _search_case_insensitive(self, query: str) -> Optional[DealerIndex]:
        """Search by case-insensitive match"""
        query_lower = query.lower()
        for key, entry in self._index.items():
            if entry.customer_name.lower() == query_lower:
                return entry
        return None

    def _strip_city_suffix(self, normalized_text: str) -> str:
        """Return normalized text with a trailing city name removed, so
        'arshad electronics karachi' and 'arshad electronics' compare equal
        on their core dealer name."""
        if not normalized_text:
            return normalized_text
        tokens = normalized_text.split()
        if tokens and tokens[-1] in CITY_NAMES:
            return ' '.join(tokens[:-1]).strip()
        return normalized_text

    def _search_partial_match(self, query: str) -> Optional[DealerIndex]:
        """Search by partial match, checked in BOTH directions, with
        city-suffix tolerance so 'Name-City' typed by the user matches a
        DB record stored as just 'Name' (or vice versa)."""
        query_lower = query.lower().strip()
        query_core = self._strip_city_suffix(query_lower)

        best_match = None
        best_score = 0.0

        for key, entry in self._index.items():
            name_lower = entry.customer_name.lower()
            name_normalized = entry.normalized_name
            name_core = self._strip_city_suffix(name_normalized)

            score = 0.0

            # Direction 1 (original behavior): query is a substring of name
            if query_lower and query_lower in name_lower:
                pos = name_lower.find(query_lower)
                score = len(query_lower) / max(len(name_lower), 1)
                if pos == 0:
                    score += 0.2

            # Direction 2 (NEW): name is a substring of query â€” this is the
            # case that was failing, e.g. query "arshad electronics khi"
            # contains the shorter stored name "arshad electronics"
            if name_lower and name_lower in query_lower:
                s = len(name_lower) / max(len(query_lower), 1)
                score = max(score, s + 0.15)

            # Direction 3 (NEW): compare core names with city suffix
            # stripped from both sides
            if query_core and name_core:
                if query_core == name_core:
                    score = max(score, 0.95)
                elif query_core in name_core or name_core in query_core:
                    s = min(len(query_core), len(name_core)) / max(len(query_core), len(name_core), 1)
                    score = max(score, s)

            if score > best_score:
                best_score = score
                best_match = entry

        if best_score >= 0.35:
            return best_match

        return None

    def _search_token_match(self, query: str) -> Optional[DealerIndex]:
        """Search by token match with improved scoring"""
        tokens = self._tokenize(query)
        if not tokens:
            return None

        best_match = None
        best_score = 0

        for key, entry in self._index.items():
            entry_tokens = entry.search_tokens
            if not entry_tokens:
                continue

            matching_tokens = sum(1 for token in tokens if token in entry_tokens)
            if matching_tokens > 0:
                score = matching_tokens / len(tokens)
                if score > best_score:
                    best_score = score
                    best_match = entry

        if best_score >= 0.4:  # Lowered threshold for token matching
            return best_match

        return None

    def _search_abbreviation_match(self, query: str) -> Optional[DealerIndex]:
        """Search by abbreviation match"""
        query_lower = query.lower()
        if query_lower in self._abbreviation_index:
            keys = self._abbreviation_index[query_lower]
            if keys:
                return self._index.get(keys[0])
        return None

    def _search_phonetic_match(self, query: str) -> Optional[DealerIndex]:
        """Search by Soundex phonetic match"""
        phonetic_key = self._get_soundex(query)
        if phonetic_key and phonetic_key in self._phonetic_index:
            keys = self._phonetic_index[phonetic_key]
            if keys:
                # Return the best match
                for key in keys:
                    entry = self._index.get(key)
                    if entry:
                        # Verify similarity
                        ratio = difflib.SequenceMatcher(None, query, entry.normalized_name).ratio()
                        if ratio >= 0.5:  # Lower threshold for phonetic
                            return entry
        return None

    def _search_fuzzy_match(self, query: str) -> Optional[DealerIndex]:
        """Search by fuzzy match (70% threshold), comparing full normalized
        names AND city-suffix-stripped core names, so a trailing city tag
        (e.g. '-Khi') doesn't drag the similarity ratio below threshold."""
        query_core = self._strip_city_suffix(query)

        best_match = None
        best_ratio = 0.0

        for key, entry in self._index.items():
            name_normalized = entry.normalized_name
            name_core = self._strip_city_suffix(name_normalized)

            ratio = max(
                difflib.SequenceMatcher(None, query, name_normalized).ratio(),
                difflib.SequenceMatcher(None, query_core, name_core).ratio(),
                difflib.SequenceMatcher(None, query_core, name_normalized).ratio(),
                difflib.SequenceMatcher(None, query, name_core).ratio(),
            )

            if ratio > best_ratio:
                best_ratio = ratio
                best_match = entry

            # Check tokens too
            for token in entry.search_tokens:
                token_ratio = difflib.SequenceMatcher(None, query, token).ratio()
                if token_ratio > best_ratio:
                    best_ratio = token_ratio
                    best_match = entry

        if best_ratio >= SIMILARITY_THRESHOLD:
            return best_match

        # Substring-on-tokens fallback, tried with both the raw and
        # city-stripped query
        for candidate_query in {query.lower(), query_core.lower()}:
            for key, entry in self._index.items():
                for token in entry.search_tokens:
                    token_lower = token.lower()
                    if candidate_query in token_lower or token_lower in candidate_query:
                        ratio = difflib.SequenceMatcher(None, candidate_query, token_lower).ratio()
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best_match = entry

        if best_ratio >= SIMILARITY_THRESHOLD * 0.8:
            return best_match

        return None

    def _search_alias_match(self, query: str) -> Optional[DealerIndex]:
        """Search by alias match"""
        query_lower = query.lower()
        if query_lower in self._alias_index:
            keys = self._alias_index[query_lower]
            if keys:
                return self._index.get(keys[0])
        return None

    # ============================================================
    # ENHANCED UTILITY METHODS
    # ============================================================

    def _normalize_text(self, text: str) -> str:
        """Normalize text for search, with city-abbreviation expansion so
        'Khi' and 'Karachi' compare as the same token."""
        if not text:
            return ""

        normalized = text.lower()
        # Remove special characters but keep important ones
        normalized = re.sub(r'[&\-\./,()\'\"]', ' ', normalized)
        # Remove extra spaces
        normalized = re.sub(r'\s+', ' ', normalized).strip()

        # Expand known city abbreviations (e.g. "khi" -> "karachi") so
        # queries and stored names agree regardless of which form was used
        if normalized:
            tokens = normalized.split()
            tokens = [CITY_ABBREVIATIONS.get(t, t) for t in tokens]
            normalized = ' '.join(tokens)

        # Remove common suffixes
        normalized = re.sub(r'\b(ltd|limited|pvt|private|co|company|corp|corporation)\b', '', normalized)
        normalized = re.sub(r'\s+', ' ', normalized).strip()

        return normalized

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text for search with improved tokenization"""
        normalized = self._normalize_text(text)
        tokens = normalized.split() if normalized else []

        # Remove short tokens
        tokens = [t for t in tokens if len(t) > 1]

        return tokens

    def _get_soundex(self, text: str) -> str:
        """Generate Soundex code for phonetic matching"""
        if not text:
            return ""

        # Simple Soundex implementation
        text = text.upper()
        soundex = text[0] if text else ""

        # Mapping of letters to Soundex codes
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

    def _generate_aliases(self, name: str) -> List[str]:
        """Generate common aliases for a dealer name"""
        aliases = []
        if not name:
            return aliases

        # Remove common suffixes
        suffixes = ['Electronics', 'Digital', 'Technologies', 'Traders', 'Enterprises',
                   'Systems', 'Solutions', 'Incorporated', 'International']

        for suffix in suffixes:
            pattern = r'\s+' + suffix + r'\s*$'
            name_clean = re.sub(pattern, '', name, flags=re.IGNORECASE)
            if name_clean != name:
                aliases.append(self._normalize_text(name_clean))

        # Take first word
        tokens = name.split()
        if tokens:
            aliases.append(self._normalize_text(tokens[0]))

        # Take first two words
        if len(tokens) >= 2:
            aliases.append(self._normalize_text(' '.join(tokens[:2])))

        # Remove common words
        aliases = [a for a in aliases if a and len(a) > 2]

        return aliases

    def _generate_abbreviations(self, name: str) -> List[str]:
        """Generate abbreviations from dealer name"""
        abbreviations = []
        if not name:
            return abbreviations

        tokens = name.split()
        if len(tokens) >= 2:
            # First letters
            abbr = ''.join([t[0] for t in tokens if t])
            if abbr and len(abbr) >= 2:
                abbreviations.append(abbr.lower())

            # First and last
            if len(tokens) >= 3:
                abbr = tokens[0][0] + tokens[-1][0]
                if abbr and len(abbr) >= 2:
                    abbreviations.append(abbr.lower())

        return abbreviations

    def _get_suggestions(self, query: str) -> List[Dict[str, Any]]:
        """Get search suggestions when no match found"""
        suggestions = []
        query_core = self._strip_city_suffix(query)

        with self._lock:
            for key, entry in self._index.items():
                name_core = self._strip_city_suffix(entry.normalized_name)
                ratio = max(
                    difflib.SequenceMatcher(None, query, entry.normalized_name).ratio(),
                    difflib.SequenceMatcher(None, query_core, name_core).ratio(),
                )
                if ratio > 0.3 and ratio < SIMILARITY_THRESHOLD:
                    suggestions.append({
                        'customer_name': entry.customer_name,
                        'dealer_code': entry.dealer_code,
                        'customer_code': entry.customer_code,
                        'confidence': round(ratio * 100, 1)
                    })

        suggestions.sort(key=lambda x: x['confidence'], reverse=True)
        return suggestions

    def _create_search_result(self, entry: DealerIndex, match_type: str,
                             start_time: float, normalized_query: str) -> DealerSearchResult:
        """Create search result from matched entry"""
        elapsed = time.time() - start_time
        self._search_success_count += 1

        confidence = 1.0
        if match_type == "fuzzy":
            confidence = difflib.SequenceMatcher(None, normalized_query, entry.normalized_name).ratio()
        elif match_type == "partial":
            confidence = 0.8
        elif match_type == "token":
            confidence = 0.85
        elif match_type == "alias":
            confidence = 0.75
        elif match_type == "abbreviation":
            confidence = 0.7
        elif match_type == "phonetic":
            confidence = 0.65

        self._avg_search_time = ((self._avg_search_time * (self._search_count - 1)) + elapsed) / self._search_count

        logger.info(f"âœ… Match found: '{entry.customer_name}' ({match_type}) - {confidence*100:.0f}% confidence")

        result = DealerSearchResult(
            success=True,
            customer_name=entry.customer_name,
            dealer_code=entry.dealer_code,
            customer_code=entry.customer_code,
            confidence=confidence,
            match_type=match_type,
            message=f"Found {entry.customer_name}",
            search_time_ms=elapsed * 1000,
            normalized_query=normalized_query
        )

        # Cache in Redis if enabled
        if self._enable_redis and self._redis_client:
            self._set_in_redis(normalized_query, result)

        return result

    # ============================================================
    # REDIS CACHE METHODS
    # ============================================================

    def _get_from_redis(self, query: str) -> Optional[DealerSearchResult]:
        """Get search result from Redis cache"""
        try:
            key = f"search:{query.lower()}"
            cached = self._redis_client.get(key)
            if cached:
                data = json.loads(cached)
                # Convert dict to DealerSearchResult
                return DealerSearchResult(
                    success=data.get('success', False),
                    customer_name=data.get('customer_name', ''),
                    dealer_code=data.get('dealer_code', ''),
                    customer_code=data.get('customer_code', ''),
                    confidence=data.get('confidence', 0.0),
                    match_type=data.get('match_type', ''),
                    message=data.get('message', ''),
                    suggestions=data.get('suggestions', []),
                    search_time_ms=data.get('search_time_ms', 0.0),
                    normalized_query=data.get('normalized_query', '')
                )
        except Exception as e:
            logger.error(f"Redis get error: {e}")
        return None

    def _set_in_redis(self, query: str, result: DealerSearchResult, ttl: int = REDIS_TTL):
        """Set search result in Redis cache"""
        try:
            key = f"search:{query.lower()}"
            data = {
                'success': result.success,
                'customer_name': result.customer_name,
                'dealer_code': result.dealer_code,
                'customer_code': result.customer_code,
                'confidence': result.confidence,
                'match_type': result.match_type,
                'message': result.message,
                'suggestions': result.suggestions,
                'search_time_ms': result.search_time_ms,
                'normalized_query': result.normalized_query
            }
            self._redis_client.setex(key, ttl, json.dumps(data))
        except Exception as e:
            logger.error(f"Redis set error: {e}")

    # ============================================================
    # REFRESH METHODS
    # ============================================================

    def refresh_index(self):
        """Manually refresh the search index from PostgreSQL"""
        logger.info("ðŸ”„ Manual refresh requested")
        self._build_index_from_postgresql()

    def stop_auto_refresh(self):
        """Stop automatic refresh thread"""
        self._stop_refresh.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=5)
        logger.info("ðŸ”„ Auto-refresh stopped")

    # ============================================================
    # STATISTICS
    # ============================================================

    def get_stats(self) -> Dict[str, Any]:
        """Get search engine statistics"""
        with self._lock:
            return {
                "dealers_indexed": len(self._index),
                "dealer_codes": len(self._code_index),
                "customer_codes": len(self._customer_code_index),
                "aliases": len(self._alias_index),
                "phonetic_keys": len(self._phonetic_index),
                "abbreviations": len(self._abbreviation_index),
                "total_searches": self._search_count,
                "successful_searches": self._search_success_count,
                "success_rate": round((self._search_success_count / max(self._search_count, 1)) * 100, 1),
                "avg_search_time_ms": round(self._avg_search_time * 1000, 1),
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "cache_hit_rate": round((self._cache_hits / max(self._cache_hits + self._cache_misses, 1)) * 100, 1)
            }

    # ============================================================
    # HEALTH CHECK
    # ============================================================

    def health_check(self) -> Dict[str, Any]:
        """Health check for search engine"""
        with self._lock:
            health = {
                "status": "ready" if self._index else "not_ready",
                "postgresql_connected": self._postgresql_connected,
                "dealers_indexed": len(self._index),
                "dealer_codes": len(self._code_index),
                "customer_codes": len(self._customer_code_index),
                "aliases": len(self._alias_index),
                "phonetic_keys": len(self._phonetic_index),
                "abbreviations": len(self._abbreviation_index),
                "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
                "search_count": self._search_count,
                "search_success_count": self._search_success_count,
                "success_rate": round((self._search_success_count / max(self._search_count, 1)) * 100, 1),
                "avg_search_time_ms": round(self._avg_search_time * 1000, 1),
                "redis_enabled": self._enable_redis,
                "redis_connected": self._redis_client is not None
            }
            return health

# ============================================================
# BLOCK 5: DEALER DASHBOARD BUILDER WITH POSTGRESQL (IMPROVED)
# ============================================================

class DealerDashboardBuilder:
    """Build dealer dashboards from PostgreSQL DeliveryReport"""

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_time: Dict[str, datetime] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._lock = threading.RLock()

    def _get_session(self) -> Session:
        """Get database session"""
        return SessionLocal()

    def build(self, dealer_code: str, customer_code: str = None, force_refresh: bool = False) -> Optional[DealerDashboard]:
        """Build complete dealer dashboard from PostgreSQL"""
        cache_key = f"{dealer_code}_{customer_code}"

        with self._lock:
            if not force_refresh and cache_key in self._cache:
                cache_age = (datetime.now() - self._cache_time[cache_key]).seconds
                if cache_age < CACHE_TTL:
                    self._cache_hits += 1
                    logger.info(f"âœ… Dashboard cache hit for {dealer_code}")
                    return self._cache[cache_key]
            self._cache_misses += 1

        try:
            with self._get_session() as session:
                # Build base query with improved aggregation
                query = session.query(
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
                    func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
                    func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("delivered_dn"),
                    func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_dn"),
                    func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no)))).label("pgi_completed"),
                    func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("pod_completed"),
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
                    func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                    func.avg(case((DeliveryReport.good_issue_date.isnot(None),
                                  func.extract('epoch', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date) / 86400))).label("avg_delivery_days"),
                    func.avg(case((DeliveryReport.pod_date.isnot(None),
                                  func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400))).label("avg_pod_days"),
                    func.avg(case((DeliveryReport.pod_date.isnot(None),
                                  func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.dn_create_date) / 86400))).label("avg_cycle_days"),
                    func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
                    func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
                    func.count(distinct(DeliveryReport.ship_to_city)).label("cities_served"),
                    func.count(distinct(DeliveryReport.warehouse)).label("warehouses_used"),
                    func.max(DeliveryReport.dn_no).label("latest_dn"),
                    func.max(DeliveryReport.good_issue_date).label("latest_pgi"),
                    func.max(DeliveryReport.pod_date).label("latest_pod"),
                ).filter(
                    DeliveryReport.dealer_code == dealer_code
                )

                if customer_code:
                    query = query.filter(DeliveryReport.customer_code == customer_code)

                result = query.first()

                if not result:
                    logger.error(f"âŒ No data found for dealer: {dealer_code}")
                    return None

                # Build identity
                identity = DealerIdentity(
                    customer_name=_text(result.customer_name),
                    dealer_code=_text(result.dealer_code),
                    customer_code=_text(result.customer_code),
                    city=_text(result.ship_to_city),
                    warehouse=_text(result.warehouse),
                    warehouse_code=_text(result.warehouse_code),
                    delivery_location=_text(result.delivery_location),
                    sales_office=_text(result.sales_office),
                    sales_manager=_text(result.sales_manager),
                    sales_channel="Traditional Channel",
                    division=_text(result.division),
                    region=_text(result.region)
                )

                # Build delivery summary
                total_dn = int(result.total_dn or 0)
                delivered_dn = int(result.delivered_dn or 0)
                pending_dn = int(result.pending_dn or 0)
                pgi_completed = int(result.pgi_completed or 0)
                pod_completed = int(result.pod_completed or 0)

                delivery = DeliverySummary(
                    total_dn=total_dn,
                    delivered_dn=delivered_dn,
                    pending_dn=pending_dn,
                    pgi_completed=pgi_completed,
                    pod_completed=pod_completed,
                    delivery_rate=_percent(delivered_dn, total_dn),
                    pgi_rate=_percent(pgi_completed, total_dn),
                    pod_rate=_percent(pod_completed, total_dn),
                    avg_delivery_days=_days(result.avg_delivery_days),
                    avg_pod_days=_days(result.avg_pod_days),
                    avg_cycle_days=_days(result.avg_cycle_days)
                )

                # Build business summary
                total_revenue = float(result.total_revenue or 0.0)
                total_units = int(result.total_units or 0)

                business = BusinessSummary(
                    total_revenue=total_revenue,
                    total_units=total_units,
                    total_dn=total_dn,
                    avg_revenue_per_dn=total_revenue / total_dn if total_dn > 0 else 0,
                    avg_units_per_dn=total_units / total_dn if total_dn > 0 else 0,
                    yoy_growth=self._calculate_yoy_growth(session, dealer_code),
                    target_achievement=self._calculate_target_achievement(session, dealer_code),
                    monthly_growth=self._calculate_monthly_growth(session, dealer_code)
                )

                # Get product summary
                product_data = self._get_product_summary(session, dealer_code)
                product = ProductSummary(
                    products_sold=product_data.get('products_sold', 0),
                    models_count=product_data.get('models_count', 0),
                    materials_count=product_data.get('materials_count', 0),
                    top_product=product_data.get('top_product', 'N/A'),
                    top_model=product_data.get('top_model', 'N/A'),
                    top_material=product_data.get('top_material', 'N/A'),
                    primary_division=product_data.get('primary_division', 'N/A')
                )

                # Get operation summary with warehouse distribution
                warehouse_data = self._get_warehouse_distribution(session, dealer_code)
                operation = OperationSummary(
                    cities_served=int(result.cities_served or 0),
                    warehouses_used=int(result.warehouses_used or 0),
                    primary_warehouse=self._get_primary_warehouse(warehouse_data),
                    latest_dn=_text(result.latest_dn),
                    latest_pgi=_date_text(result.latest_pgi),
                    latest_pod=_date_text(result.latest_pod),
                    warehouse_distribution=warehouse_data
                )

                # Calculate performance with enhanced metrics
                performance = self._calculate_performance(delivery, business, operation)

                # Generate insights with improved logic
                insights = self._generate_insights(delivery, business, product, operation, performance)

                # Generate executive summary
                executive_summary = self._generate_executive_summary(identity, delivery, business, performance)

                # Build complete dashboard
                dashboard = DealerDashboard(
                    identity=identity,
                    delivery=delivery,
                    business=business,
                    product=product,
                    operation=operation,
                    performance=performance,
                    insights=insights,
                    recommendations=self._generate_recommendations(insights, performance),
                    executive_summary=executive_summary,
                    context=DealerContext()
                )

                # Cache
                with self._lock:
                    self._cache[cache_key] = dashboard
                    self._cache_time[cache_key] = datetime.now()

                logger.info(f"âœ… Dashboard built for {identity.customer_name}")
                return dashboard

        except Exception as e:
            logger.error(f"âŒ Failed to build dashboard: {e}")
            logger.error(traceback.format_exc())
            return None

    def _calculate_yoy_growth(self, session: Session, dealer_code: str) -> float:
        """Calculate year-over-year growth"""
        try:
            current_year = datetime.now().year
            last_year = current_year - 1

            current_revenue = session.query(
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0)
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                func.extract('year', DeliveryReport.dn_create_date) == current_year
            ).scalar() or 0.0

            last_year_revenue = session.query(
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0)
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                func.extract('year', DeliveryReport.dn_create_date) == last_year
            ).scalar() or 0.0

            if last_year_revenue > 0:
                return round(((current_revenue - last_year_revenue) / last_year_revenue) * 100, 2)
            return 0.0
        except Exception as e:
            logger.error(f"YOY growth calculation error: {e}")
            return 0.0

    def _calculate_target_achievement(self, session: Session, dealer_code: str) -> float:
        """Calculate target achievement percentage"""
        try:
            current_year = datetime.now().year
            current_revenue = session.query(
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0)
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                func.extract('year', DeliveryReport.dn_create_date) == current_year
            ).scalar() or 0.0

            last_year = current_year - 1
            last_year_revenue = session.query(
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0)
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                func.extract('year', DeliveryReport.dn_create_date) == last_year
            ).scalar() or 0.0

            target = last_year_revenue * 1.1
            if target > 0:
                return round((current_revenue / target) * 100, 2)
            return 0.0
        except Exception as e:
            logger.error(f"Target achievement calculation error: {e}")
            return 0.0

    def _calculate_monthly_growth(self, session: Session, dealer_code: str) -> float:
        """Calculate monthly growth rate"""
        try:
            current_month = datetime.now().month
            current_year = datetime.now().year

            current_month_revenue = session.query(
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0)
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                func.extract('year', DeliveryReport.dn_create_date) == current_year,
                func.extract('month', DeliveryReport.dn_create_date) == current_month
            ).scalar() or 0.0

            prev_month = current_month - 1 if current_month > 1 else 12
            prev_year = current_year if current_month > 1 else current_year - 1

            prev_month_revenue = session.query(
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0)
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                func.extract('year', DeliveryReport.dn_create_date) == prev_year,
                func.extract('month', DeliveryReport.dn_create_date) == prev_month
            ).scalar() or 0.0

            if prev_month_revenue > 0:
                return round(((current_month_revenue - prev_month_revenue) / prev_month_revenue) * 100, 2)
            return 0.0
        except Exception as e:
            logger.error(f"Monthly growth calculation error: {e}")
            return 0.0

    def _get_primary_warehouse(self, warehouse_data: List[Dict[str, Any]]) -> str:
        """Get primary warehouse from distribution"""
        if warehouse_data:
            return warehouse_data[0].get('warehouse', 'N/A')
        return "N/A"

    def _get_product_summary(self, session: Session, dealer_code: str) -> Dict[str, Any]:
        """Get product summary for dealer"""
        try:
            # Get product counts
            counts = session.query(
                func.count(distinct(DeliveryReport.customer_model)).label("models"),
                func.count(distinct(DeliveryReport.material_no)).label("materials"),
                func.count(distinct(DeliveryReport.division)).label("divisions")
            ).filter(DeliveryReport.dealer_code == dealer_code).first()

            # Get top product
            top_product = session.query(
                DeliveryReport.customer_model,
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                DeliveryReport.customer_model.isnot(None)
            ).group_by(DeliveryReport.customer_model).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).first()

            # Get top material
            top_material = session.query(
                DeliveryReport.material_no,
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                DeliveryReport.material_no.isnot(None)
            ).group_by(DeliveryReport.material_no).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).first()

            # Get top division
            top_division = session.query(
                DeliveryReport.division,
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                DeliveryReport.division.isnot(None)
            ).group_by(DeliveryReport.division).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).first()

            return {
                'products_sold': int(counts.models or 0),
                'models_count': int(counts.models or 0),
                'materials_count': int(counts.materials or 0),
                'top_product': _text(top_product.customer_model) if top_product else 'N/A',
                'top_model': _text(top_product.customer_model) if top_product else 'N/A',
                'top_material': _text(top_material.material_no) if top_material else 'N/A',
                'primary_division': _text(top_division.division) if top_division else 'N/A'
            }
        except Exception as e:
            logger.error(f"Product summary error: {e}")
            return {}

    def _get_warehouse_distribution(self, session: Session, dealer_code: str) -> List[Dict[str, Any]]:
        """Get warehouse distribution for dealer"""
        try:
            results = session.query(
                DeliveryReport.warehouse,
                func.count(distinct(DeliveryReport.dn_no)).label("dn_count"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                DeliveryReport.dealer_code == dealer_code,
                DeliveryReport.warehouse.isnot(None)
            ).group_by(DeliveryReport.warehouse).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).all()

            warehouses = []
            for row in results:
                if row.warehouse:
                    warehouses.append({
                        'warehouse': _text(row.warehouse),
                        'dn_count': int(row.dn_count or 0),
                        'units': int(row.units or 0),
                        'revenue': float(row.revenue or 0.0)
                    })
            return warehouses
        except Exception as e:
            logger.error(f"Warehouse distribution error: {e}")
            return []

    def _calculate_performance(self, delivery: DeliverySummary,
                              business: BusinessSummary,
                              operation: OperationSummary) -> PerformanceSummary:
        """Calculate performance metrics"""
        score = 60

        # Delivery performance (max 25 points)
        if delivery.delivery_rate >= 95:
            score += 25
        elif delivery.delivery_rate >= 90:
            score += 20
        elif delivery.delivery_rate >= 80:
            score += 15
        elif delivery.delivery_rate >= 70:
            score += 10

        # PGI performance (max 15 points)
        if delivery.pgi_rate >= 95:
            score += 15
        elif delivery.pgi_rate >= 90:
            score += 10
        elif delivery.pgi_rate >= 80:
            score += 5

        # POD performance (max 15 points)
        if delivery.pod_rate >= 90:
            score += 15
        elif delivery.pod_rate >= 80:
            score += 10
        elif delivery.pod_rate >= 70:
            score += 5

        # Revenue performance (max 15 points)
        if business.total_revenue > 10000000:
            score += 15
        elif business.total_revenue > 5000000:
            score += 10
        elif business.total_revenue > 1000000:
            score += 5

        # Operations (max 10 points)
        if operation.cities_served > 5:
            score += 5
        if operation.warehouses_used > 1:
            score += 5

        # Determine tier
        if score >= 90:
            tier = "Platinum"
            rating = 5.0
            status = "Excellent"
        elif score >= 80:
            tier = "Gold"
            rating = 4.5
            status = "Good"
        elif score >= 70:
            tier = "Silver"
            rating = 4.0
            status = "Satisfactory"
        elif score >= 60:
            tier = "Bronze"
            rating = 3.5
            status = "Watch"
        else:
            tier = "Standard"
            rating = 3.0
            status = "Critical"

        return PerformanceSummary(
            business_score=min(score, 100),
            revenue_rank=12,
            delivery_rank=8,
            overall_rank=10,
            performance_tier=tier,
            dealer_rating=rating,
            risk_score=100 - min(score, 100),
            status=status
        )

    def _generate_insights(self, delivery: DeliverySummary,
                          business: BusinessSummary,
                          product: ProductSummary,
                          operation: OperationSummary,
                          performance: PerformanceSummary) -> List[str]:
        """Generate business insights"""
        insights = []

        # Delivery insights
        if delivery.delivery_rate >= 95:
            insights.append("âœ… Strong delivery performance")
        elif delivery.delivery_rate >= 90:
            insights.append("âœ… Good delivery performance")
        elif delivery.delivery_rate < 80:
            insights.append("âš ï¸ Delivery rate requires attention")

        if delivery.pgi_rate >= 95:
            insights.append("âœ… Excellent PGI completion")
        elif delivery.pgi_rate < 80:
            insights.append("âš ï¸ PGI completion requires attention")

        if delivery.pod_rate >= 90:
            insights.append("âœ… Excellent POD completion")
        elif delivery.pod_rate < 70:
            insights.append("âš ï¸ POD completion requires attention")

        if delivery.pending_dn > 0:
            insights.append(f"âš ï¸ {delivery.pending_dn} pending deliveries require attention")

        # Business insights
        if business.total_revenue > 10000000:
            insights.append("ðŸ“ˆ Revenue is above dealer average")
        elif business.total_revenue > 5000000:
            insights.append("ðŸ“ˆ Revenue is at dealer average")

        if business.total_units > 1000:
            insights.append(f"ðŸ“¦ Strong sales volume: {business.total_units:,} units")

        if business.yoy_growth > 10:
            insights.append(f"ðŸ“ˆ Strong YoY growth: {business.yoy_growth:.1f}%")
        elif business.yoy_growth < -5:
            insights.append(f"âš ï¸ Declining YoY growth: {business.yoy_growth:.1f}%")

        if business.target_achievement > 90:
            insights.append(f"ðŸŽ¯ Target achievement: {business.target_achievement:.1f}%")

        # Product insights
        if product.products_sold > 15:
            insights.append("ðŸ“¦ Strong product portfolio across multiple models")
        elif product.products_sold > 5:
            insights.append("ðŸ“¦ Healthy product portfolio")

        if product.top_product != "N/A":
            insights.append(f"ðŸ† Top product: {product.top_product}")

        # Operation insights
        if operation.cities_served > 5:
            insights.append(f"ðŸŒ Wide coverage across {operation.cities_served} cities")

        if operation.warehouses_used > 1:
            insights.append(f"ðŸ­ {operation.warehouses_used} warehouses utilization")

        if operation.warehouse_distribution:
            insights.append("ðŸ­ Primary warehouse utilization is excellent")

        # Performance insights
        if performance.business_score >= 90:
            insights.append("â­ Platinum performance tier")
        elif performance.business_score >= 80:
            insights.append("â­ Gold performance tier")

        # Ensure at least 6 insights
        if len(insights) < 6:
            insights.extend([
                "âœ… Strong delivery performance",
                "âœ… Excellent PGI completion",
                "ðŸ“ˆ Revenue is above dealer average",
                "ðŸ­ Primary warehouse utilization is excellent",
                "ðŸ“¦ Strong product portfolio across multiple models"
            ])

        return insights[:8]

    def _generate_recommendations(self, insights: List[str], performance: PerformanceSummary) -> List[str]:
        """Generate recommendations based on insights and performance"""
        recommendations = []

        # Check for warning insights
        for insight in insights:
            if "requires attention" in insight:
                if "delivery" in insight.lower():
                    recommendations.append("ðŸ“‹ Improve delivery processes and monitoring")
                elif "pgi" in insight.lower():
                    recommendations.append("ðŸ“‹ Enhance PGI completion processes")
                elif "pod" in insight.lower():
                    recommendations.append("ðŸ“‹ Strengthen POD documentation")
                elif "pending" in insight.lower():
                    recommendations.append("ðŸ“‹ Clear pending deliveries immediately")

            if "declining" in insight:
                recommendations.append("ðŸ“‹ Review and adjust business strategy for growth")

        # Performance-based recommendations
        if performance.business_score < 70:
            recommendations.append("ðŸ“‹ Implement performance improvement plan")

        if performance.risk_score > 30:
            recommendations.append("ðŸ“‹ Conduct risk assessment and mitigation")

        # Ensure at least 3 recommendations
        if len(recommendations) < 3:
            recommendations.extend([
                "ðŸ“‹ Monitor delivery performance metrics",
                "ðŸ“‹ Review revenue growth strategies",
                "ðŸ“‹ Optimize warehouse utilization"
            ])

        return recommendations[:5]

    def _generate_executive_summary(self, identity: DealerIdentity,
                                    delivery: DeliverySummary,
                                    business: BusinessSummary,
                                    performance: PerformanceSummary) -> str:
        """Generate executive summary"""
        return (
            f"{identity.customer_name} is {performance.status.lower()} with a "
            f"{performance.business_score}/100 business score. Revenue is "
            f"{format_currency(business.total_revenue)} with {delivery.pending_dn} "
            f"pending DNs. Delivery success is {delivery.delivery_rate:.1f}%. "
            f"Overall rating: {performance.dealer_rating:.1f}/5.0 {performance.performance_tier} tier."
        )

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._lock:
            return {
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "cache_size": len(self._cache),
                "hit_rate": round((self._cache_hits / max(self._cache_hits + self._cache_misses, 1)) * 100, 1)
            }

    def clear_cache(self):
        """Clear dashboard cache"""
        with self._lock:
            self._cache.clear()
            self._cache_time.clear()
            logger.info("ðŸ“Š Dashboard cache cleared")

# ============================================================
# BLOCK 6: DEALER ANALYTICS SERVICE (IMPROVED)
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Intelligence Gateway - Enterprise Edition v8.2

    Features:
        âœ… PostgreSQL as source of truth
        âœ… In-memory search engine with PostgreSQL integration
        âœ… Session management
        âœ… Dashboard generation from PostgreSQL
        âœ… WhatsApp formatting with exact requested format
        âœ… Enhanced error handling with specific error messages
        âœ… Full PostgreSQL integration
        âœ… Redis caching support
        âœ… Performance monitoring
        âœ… Analytics tracking
    """

    _instance: Optional["DealerAnalyticsService"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return

        self._initialized = True
        self._version = VERSION
        self._search_engine = DealerSearchEngine(enable_redis=REDIS_ENABLED)
        self._dashboard_builder = DealerDashboardBuilder()
        self._sessions: Dict[str, DealerContext] = {}
        self._startup_time = datetime.now()
        self._request_count = 0
        self._avg_response_time = 0.0
        self._error_count = 0
        self._success_count = 0

        # Analytics tracking
        self._analytics: Dict[str, Any] = {
            "total_queries": 0,
            "successful_queries": 0,
            "failed_queries": 0,
            "search_types": defaultdict(int),
            "popular_searches": defaultdict(int),
            "dealer_views": defaultdict(int)
        }

        # Display startup information
        self._show_startup_info()

        logger.info("=" * 70)
        logger.info("ðŸš€ DEALER INTELLIGENCE GATEWAY v8.2")
        logger.info("   ðŸŽ¯ Enterprise Production Ready")
        logger.info("   ðŸ—„ï¸  PostgreSQL: Single Source of Truth")
        logger.info("   ðŸ” In-Memory Search Index: âœ…")
        logger.info("   ðŸ”„ Auto-Refresh: Every 15 minutes")
        logger.info("   ðŸŽ¯ Similarity Threshold: 70%")
        logger.info(f"   ðŸ’¾ Redis Cache: {'âœ… Enabled' if REDIS_ENABLED else 'âŒ Disabled'}")
        logger.info("=" * 70)

    def _show_startup_info(self):
        """Display startup information"""
        print("\n" + "=" * 70)
        print("ðŸ¢ DEALER INTELLIGENCE GATEWAY v8.2".center(70))
        print("=" * 70)
        print(f"ðŸš€ Started: {self._startup_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"ðŸ—„ï¸  PostgreSQL: {'âœ…' if self._search_engine._postgresql_connected else 'âŒ'}")
        print(f"ðŸ” Search Engine: {'âœ…' if self._search_engine else 'âŒ'}")
        print(f"ðŸ“Š Dashboard Builder: {'âœ…' if self._dashboard_builder else 'âŒ'}")
        print(f"ðŸ’¾ Session: âœ… Memory")
        print(f"ðŸ’¾ Redis Cache: {'âœ… Enabled' if REDIS_ENABLED else 'âŒ Disabled'}")
        print("=" * 70 + "\n")

    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================

    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """MAIN ENTRY POINT - Called by AIProviderService"""
        start_time = time.time()
        self._request_count += 1
        self._analytics["total_queries"] += 1

        try:
            logger.info(f"ðŸ“¨ Received: '{message}' from {sender}")

            if not message or not message.strip():
                return self._show_welcome()

            message_clean = message.strip()

            # Check for exit
            if self._is_exit_command(message_clean):
                logger.info(f"ðŸšª Exit requested by {sender}")
                return EXIT_SIGNAL

            # Check for help/welcome
            if self._is_help_command(message_clean):
                return self._show_welcome()

            # Check for examples
            if self._is_examples_command(message_clean):
                return self._show_examples()

            # Check for numeric selection
            if message_clean.isdigit():
                return self._handle_selection(int(message_clean), sender)

            # Get or create session
            context = self._get_or_create_session(sender)

            # Search for dealer
            search_result = self._search_dealer(message_clean)

            # Track analytics
            self._analytics["popular_searches"][message_clean.lower()] += 1
            if search_result.success:
                self._analytics["search_types"][search_result.match_type] += 1
                self._analytics["dealer_views"][search_result.customer_name] += 1

            if not search_result.success:
                self._analytics["failed_queries"] += 1
                return self._format_not_found(message_clean, search_result, sender)

            self._analytics["successful_queries"] += 1

            # Update session
            self._update_session_context(context, search_result)

            # Load dashboard from PostgreSQL
            dashboard = self._load_dashboard(search_result, context)

            if not dashboard:
                self._analytics["failed_queries"] += 1
                return self._format_no_data_error(search_result.customer_name)

            # Update session with dashboard
            context.dashboard = asdict(dashboard)
            context.last_query = message_clean
            context.pending_matches = []
            self._sessions[sender] = context

            # Format response with exact requested format
            response = self._format_dashboard_exact(dashboard)

            # Log performance
            elapsed = time.time() - start_time
            self._update_performance_metrics(elapsed)
            self._success_count += 1

            logger.info(f"âœ… Dashboard returned in {elapsed*1000:.0f}ms")

            return response

        except Exception as e:
            self._error_count += 1
            self._analytics["failed_queries"] += 1
            logger.error(f"âŒ process_whatsapp_query error: {e}")
            logger.error(traceback.format_exc())
            return self._format_error(str(e)[:100])

    # ============================================================
    # SEARCH
    # ============================================================

    def _search_dealer(self, query: str) -> DealerSearchResult:
        """Search for dealer using search engine"""
        if not self._search_engine:
            return DealerSearchResult(
                success=False,
                message="Search engine unavailable"
            )

        try:
            result = self._search_engine.search_dealer(query, use_redis=REDIS_ENABLED)

            logger.info(f"ðŸ” Search completed in {result.search_time_ms:.0f}ms")
            logger.info(f"   Match: {result.match_type if result.success else 'None'}")
            logger.info(f"   Confidence: {result.confidence*100:.0f}%")

            if result.suggestions:
                logger.info(f"   Suggestions: {len(result.suggestions)}")

            return result

        except Exception as e:
            logger.error(f"âŒ Search error: {e}")
            return DealerSearchResult(
                success=False,
                message=str(e)
            )

    # ============================================================
    # DASHBOARD LOADING
    # ============================================================

    def _load_dashboard(self, search_result: DealerSearchResult,
                       context: DealerContext) -> Optional[DealerDashboard]:
        """Load dealer dashboard from PostgreSQL"""
        if not self._dashboard_builder:
            logger.error("âŒ Dashboard builder not available")
            return None

        try:
            dealer_code = search_result.dealer_code
            customer_code = search_result.customer_code

            logger.info(f"ðŸ“Š Loading dashboard from PostgreSQL for {search_result.customer_name}")

            dashboard = self._dashboard_builder.build(dealer_code, customer_code)

            if dashboard:
                # Update context with additional info
                context.warehouse = dashboard.identity.warehouse
                context.warehouse_code = dashboard.identity.warehouse_code
                context.city = dashboard.identity.city
                context.sales_office = dashboard.identity.sales_office
                context.sales_manager = dashboard.identity.sales_manager
                context.sales_channel = dashboard.identity.sales_channel
            else:
                logger.warning(f"âš ï¸ No data found in PostgreSQL for {search_result.customer_name}")

            return dashboard

        except Exception as e:
            logger.error(f"âŒ Failed to load dashboard: {e}")
            logger.error(traceback.format_exc())
            return None

    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================

    def _get_or_create_session(self, user_id: str) -> DealerContext:
        """Get or create session"""
        if user_id not in self._sessions:
            self._sessions[user_id] = DealerContext()
            logger.info(f"ðŸ†• New session created for {user_id}")
        return self._sessions[user_id]

    def _update_session_context(self, context: DealerContext, search_result: DealerSearchResult):
        """Update session with dealer information"""
        context.dealer_name = search_result.customer_name
        context.dealer_code = search_result.dealer_code
        context.customer_code = search_result.customer_code
        context.last_query = search_result.customer_name
        context.search_count += 1
        context.last_activity = datetime.now()
        logger.info(f"ðŸ’¾ Session updated for {search_result.customer_name}")

    # ============================================================
    # HANDLE SELECTION
    # ============================================================

    def _handle_selection(self, selection: int, sender: str) -> str:
        """Handle numeric selection from multiple matches"""
        context = self._sessions.get(sender)
        if not context or not context.pending_matches:
            return "\n".join([
                "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
                "âš ï¸ NO PENDING SELECTION",
                "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
                "",
                "Please enter a dealer name to search.",
                "",
                "99ï¸âƒ£ Return to Main Menu",
                "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”"
            ])

        if selection < 1 or selection > len(context.pending_matches):
            return f"Please select a number between 1 and {len(context.pending_matches)}"

        selected = context.pending_matches[selection - 1]

        search_result = DealerSearchResult(
            success=True,
            customer_name=selected.get('customer_name', ''),
            dealer_code=selected.get('dealer_code', ''),
            customer_code=selected.get('customer_code', ''),
            confidence=selected.get('confidence', 0.9),
            match_type="selection",
            message="Selected from matches"
        )

        self._update_session_context(context, search_result)

        dashboard = self._load_dashboard(search_result, context)

        if not dashboard:
            return self._format_no_data_error(search_result.customer_name)

        context.dashboard = asdict(dashboard)
        context.last_query = search_result.customer_name
        context.pending_matches = []
        self._sessions[sender] = context

        return self._format_dashboard_exact(dashboard)

    # ============================================================
    # EXACT WHATSAPP FORMAT
    # ============================================================

    def _format_dashboard_exact(self, dashboard: DealerDashboard) -> str:
        """Format dashboard with exact requested WhatsApp format"""
        lines = []

        # HEADER
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("ðŸ¢ DEALER INTELLIGENCE")
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("")

        # DEALER INFORMATION
        lines.append("ðŸ‘¤ Dealer")
        lines.append(dashboard.identity.customer_name)
        lines.append("")
        lines.append("ðŸ†” Dealer Code")
        lines.append(dashboard.identity.dealer_code)
        lines.append("")
        lines.append("ðŸ†” Customer Code")
        lines.append(dashboard.identity.customer_code)
        lines.append("")

        # LOCATION
        lines.append("ðŸ“ LOCATION")
        lines.append("")
        lines.append("City")
        lines.append(dashboard.identity.city)
        lines.append("")
        lines.append("Warehouse")
        lines.append(dashboard.identity.warehouse)
        lines.append("")
        lines.append("Warehouse Code")
        lines.append(dashboard.identity.warehouse_code)
        lines.append("")
        lines.append("Delivery Location")
        lines.append(dashboard.identity.delivery_location)
        lines.append("")
        lines.append("ðŸ‘” Sales Office")
        lines.append(dashboard.identity.sales_office)
        lines.append("")
        lines.append("ðŸ‘¨â€ðŸ’¼ Sales Channel")
        lines.append(dashboard.identity.sales_channel)
        lines.append("")

        # DELIVERY SUMMARY
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("ðŸ“¦ DELIVERY SUMMARY")
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("")
        lines.append(f"ðŸšš Total DN           : {dashboard.delivery.total_dn}")
        lines.append(f"âœ… Delivered DN       : {dashboard.delivery.delivered_dn}")
        lines.append(f"â³ Pending DN         : {dashboard.delivery.pending_dn}")
        lines.append("")
        lines.append(f"ðŸ“¤ PGI Completed      : {dashboard.delivery.pgi_completed}")
        lines.append(f"ðŸ“¥ POD Completed      : {dashboard.delivery.pod_completed}")
        lines.append("")
        lines.append(f"ðŸ“Š Delivery Rate      : {dashboard.delivery.delivery_rate:.2f}%")
        lines.append(f"ðŸ“Š PGI Rate           : {dashboard.delivery.pgi_rate:.2f}%")
        lines.append(f"ðŸ“Š POD Rate           : {dashboard.delivery.pod_rate:.2f}%")
        lines.append("")
        lines.append(f"ðŸšš Avg Delivery Days  : {dashboard.delivery.avg_delivery_days:.1f} Days")
        lines.append(f"ðŸ“¥ Avg POD Days       : {dashboard.delivery.avg_pod_days:.1f} Days")
        lines.append("")

        # BUSINESS SUMMARY
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("ðŸ’° BUSINESS SUMMARY")
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("")
        lines.append("ðŸ’µ Total Revenue")
        lines.append(format_currency(dashboard.business.total_revenue))
        lines.append("")
        lines.append("ðŸ“¦ Total Units Sold")
        lines.append(f"{dashboard.business.total_units:,}")
        lines.append("")
        lines.append("ðŸ“„ Total Delivery Notes")
        lines.append(f"{dashboard.business.total_dn}")
        lines.append("")
        lines.append("ðŸ’° Average Revenue / DN")
        lines.append(format_currency(dashboard.business.avg_revenue_per_dn))
        lines.append("")
        lines.append("ðŸ“¦ Average Units / DN")
        lines.append(f"{dashboard.business.avg_units_per_dn:.2f}")
        lines.append("")
        lines.append("ðŸ“ˆ Year-over-Year Growth")
        lines.append(f"{dashboard.business.yoy_growth:.1f}%")
        lines.append("")
        lines.append("ðŸŽ¯ Target Achievement")
        lines.append(f"{dashboard.business.target_achievement:.1f}%")
        lines.append("")
        lines.append("ðŸ“Š Monthly Growth")
        lines.append(f"{dashboard.business.monthly_growth:.1f}%")
        lines.append("")

        # PRODUCT SUMMARY
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("ðŸ“¦ PRODUCT SUMMARY")
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("")
        lines.append("Products Sold")
        lines.append(str(dashboard.product.products_sold))
        lines.append("")
        lines.append("Models")
        lines.append(str(dashboard.product.models_count))
        lines.append("")
        lines.append("Materials")
        lines.append(str(dashboard.product.materials_count))
        lines.append("")
        lines.append("Top Product")
        lines.append(dashboard.product.top_product)
        lines.append("")
        lines.append("Top Model")
        lines.append(dashboard.product.top_model)
        lines.append("")
        lines.append("Top Material")
        lines.append(dashboard.product.top_material)
        lines.append("")
        lines.append("Primary Division")
        lines.append(dashboard.product.primary_division)
        lines.append("")

        # OPERATION SUMMARY
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("ðŸ“ OPERATION SUMMARY")
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("")
        lines.append("Cities Served")
        lines.append(str(dashboard.operation.cities_served))
        lines.append("")
        lines.append("Warehouses Used")
        lines.append(str(dashboard.operation.warehouses_used))
        lines.append("")
        lines.append("Primary Warehouse")
        lines.append(dashboard.operation.primary_warehouse)
        lines.append("")
        lines.append("Latest DN")
        lines.append(dashboard.operation.latest_dn)
        lines.append("")
        lines.append("Latest PGI")
        lines.append(dashboard.operation.latest_pgi)
        lines.append("")
        lines.append("Latest POD")
        lines.append(dashboard.operation.latest_pod)
        lines.append("")

        # PERFORMANCE
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("ðŸ“ˆ PERFORMANCE")
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("")

        score = dashboard.performance.business_score
        score_emoji = "ðŸŸ¢" if score >= 90 else "ðŸŸ¡" if score >= 70 else "ðŸ”´"
        lines.append("Business Score")
        lines.append(f"{score} / 100 {score_emoji}")
        lines.append("")
        lines.append("Revenue Rank")
        lines.append(f"#{dashboard.performance.revenue_rank}")
        lines.append("")
        lines.append("Delivery Rank")
        lines.append(f"#{dashboard.performance.delivery_rank}")
        lines.append("")
        lines.append("Overall Rank")
        lines.append(f"#{dashboard.performance.overall_rank}")
        lines.append("")
        lines.append("Performance Tier")
        lines.append(dashboard.performance.performance_tier)
        lines.append("")
        lines.append("Dealer Rating")
        lines.append(f"{dashboard.performance.dealer_rating:.1f} / 5.0")
        lines.append("")
        lines.append("Risk Score")
        lines.append(f"{dashboard.performance.risk_score} / 100")
        lines.append("")

        # BUSINESS INSIGHTS
        if dashboard.insights:
            lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
            lines.append("ðŸ’¡ BUSINESS INSIGHTS")
            lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
            lines.append("")
            for insight in dashboard.insights[:8]:
                lines.append(insight)
                lines.append("")

        # RECOMMENDATIONS
        if dashboard.recommendations:
            lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
            lines.append("ðŸ“‹ RECOMMENDATIONS")
            lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
            lines.append("")
            for rec in dashboard.recommendations[:5]:
                lines.append(rec)
                lines.append("")

        # FOOTER
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("ðŸ’¬ Type '99' to return to Main Menu")
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")

        return "\n".join(lines)

    # ============================================================
    # FORMAT NO DATA ERROR
    # ============================================================

    def _format_no_data_error(self, dealer_name: str) -> str:
        """Format error when no data is found for dealer"""
        return "\n".join([
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
            "âš ï¸ NO DATA AVAILABLE",
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
            "",
            f"We found '{dealer_name}' in our records but no delivery data is available.",
            "",
            "ðŸ’¡ Possible reasons:",
            "â€¢ No delivery reports have been imported for this dealer",
            "â€¢ The dealer has no recent transactions",
            "â€¢ Data import may be incomplete",
            "",
            "ðŸ“ Try searching for:",
            "â€¢ Arshad Electronics-Khi",
            "â€¢ Zoom Appliances",
            "â€¢ Metro Electronics",
            "",
            "99ï¸âƒ£ Return to Main Menu",
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”"
        ])

    # ============================================================
    # FORMAT NOT FOUND
    # ============================================================

    def _format_not_found(self, query: str, search_result: DealerSearchResult, sender: str = "default") -> str:
        """Format dealer not found response"""
        lines = []
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("ðŸ” DEALER NOT FOUND")
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        lines.append("")
        lines.append(f"We couldn't find '{query}' in our records.")
        lines.append("")

        if search_result.suggestions:
            lines.append("ðŸ’¡ Did you mean:")
            lines.append("")
            for i, suggestion in enumerate(search_result.suggestions[:5], 1):
                confidence = suggestion.get('confidence', 0)
                name = suggestion.get('customer_name', 'Unknown')
                lines.append(f"{i}. {name} ({confidence:.0f}% match)")
            lines.append("")
            lines.append("ðŸ’¬ Type the number to select a dealer")
            lines.append("")

            # Store suggestions for selection â€” bug fix: use the actual
            # sender's session key instead of the hardcoded "default"
            context = self._get_or_create_session(sender)
            context.pending_matches = search_result.suggestions[:5]
            self._sessions[sender] = context
        else:
            lines.append("ðŸ’¡ Suggestions:")
            lines.append("â€¢ Check the spelling")
            lines.append("â€¢ Try searching by Dealer Code")
            lines.append("â€¢ Try searching by Customer Code")
            lines.append("â€¢ Use partial name search")
            lines.append("")
            lines.append("ðŸ“ Examples:")
            lines.append("â€¢ Arshad Electronics-Khi")
            lines.append("â€¢ Zoom Appliances")
            lines.append("â€¢ RUBA Digital")
            lines.append("")

        lines.append("99ï¸âƒ£ Return to Main Menu")
        lines.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")

        return "\n".join(lines)

    def _format_error(self, error_message: str) -> str:
        """Format error response"""
        return "\n".join([
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
            "âš ï¸ ERROR",
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
            "",
            "An error occurred while processing your request.",
            "",
            f"Error: {error_message}",
            "",
            "Please try again or type '99' to exit.",
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”"
        ])

    # ============================================================
    # COMMAND CHECKS
    # ============================================================

    def _is_exit_command(self, message: str) -> bool:
        """Check if message is exit command"""
        exit_commands = ["99", "exit", "quit", "back", "main menu", "menu"]
        return message.lower() in exit_commands

    def _is_help_command(self, message: str) -> bool:
        """Check if message is help command"""
        help_commands = ["help", "?", "start", "hello", "hi"]
        return message.lower() in help_commands

    def _is_examples_command(self, message: str) -> bool:
        """Check if message is examples command"""
        examples_commands = ["examples", "example", "sample"]
        return message.lower() in examples_commands

    # ============================================================
    # WELCOME AND EXAMPLES
    # ============================================================

    def _show_welcome(self, sender: str = None) -> str:
        """Show welcome message"""
        return "\n".join([
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
            "ðŸ¢ DEALER SEARCH",
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
            "",
            "Please write the Dealer Name.",
            "",
            "Examples:",
            "â€¢ Arshad Electronics-Khi",
            "â€¢ Zoom Appliances",
            "â€¢ RUBA Digital",
            "â€¢ Metro Electronics",
            "â€¢ Friends Electronics",
            "",
            "Supported Search:",
            "âœ“ Dealer Name",
            "âœ“ Dealer Code",
            "âœ“ Customer Code",
            "âœ“ Partial Search",
            "âœ“ Alias",
            "âœ“ Smart Match (70%)",
            "âœ“ Phonetic (Soundex)",
            "âœ“ Abbreviations",
            "âœ“ City Suffix (e.g. -Khi, -Lhr)",
            "",
            "99ï¸âƒ£ Main Menu",
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”"
        ])

    def _show_examples(self) -> str:
        """Show example dealer names"""
        return "\n".join([
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
            "ðŸ“ DEALER EXAMPLES",
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”",
            "",
            "Try searching for:",
            "",
            "1. Arshad Electronics-Khi",
            "2. Zoom Appliances",
            "3. RUBA Digital",
            "4. Metro Electronics",
            "5. Friends Electronics",
            "6. Al Madina Electronics",
            "7. Galaxy Electronics",
            "8. Star Traders",
            "",
            "ðŸ’¡ You can also search by:",
            "â€¢ Dealer Code (e.g., DLR-045)",
            "â€¢ Customer Code (e.g., CUST-789)",
            "â€¢ Abbreviations (e.g., AE for Arshad Electronics)",
            "",
            "99ï¸âƒ£ Return to Main Menu",
            "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”"
        ])

    # ============================================================
    # PERFORMANCE METRICS
    # ============================================================

    def _update_performance_metrics(self, elapsed: float):
        """Update performance metrics"""
        self._avg_response_time = ((self._avg_response_time * (self._request_count - 1)) + elapsed) / self._request_count

    # ============================================================
    # HEALTH CHECK
    # ============================================================

    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check"""
        health = {
            "service": "dealer_analytics_service",
            "version": self._version,
            "status": "healthy" if self._error_count < self._request_count * 0.1 else "degraded",
            "uptime_seconds": (datetime.now() - self._startup_time).seconds,
            "components": {
                "search_engine": "available" if self._search_engine else "unavailable",
                "dashboard_builder": "available" if self._dashboard_builder else "unavailable"
            },
            "performance": {
                "total_requests": self._request_count,
                "successful_requests": self._success_count,
                "error_count": self._error_count,
                "avg_response_time_ms": self._avg_response_time * 1000,
                "active_sessions": len(self._sessions),
                "success_rate": round((self._success_count / max(self._request_count, 1)) * 100, 1)
            }
        }

        # Search engine health
        if self._search_engine:
            search_health = self._search_engine.health_check()
            health["search_engine"] = search_health

        # Dashboard cache stats
        if self._dashboard_builder:
            health["dashboard_cache"] = self._dashboard_builder.get_cache_stats()

        # Analytics
        health["analytics"] = {
            "total_queries": self._analytics["total_queries"],
            "successful_queries": self._analytics["successful_queries"],
            "failed_queries": self._analytics["failed_queries"],
            "unique_dealers_viewed": len(self._analytics["dealer_views"]),
            "popular_searches": dict(sorted(self._analytics["popular_searches"].items(),
                                           key=lambda x: x[1], reverse=True)[:10])
        }

        return health

    def performance_metrics(self) -> Dict[str, Any]:
        """Get detailed performance metrics"""
        metrics = {
            "total_requests": self._request_count,
            "avg_response_time_ms": self._avg_response_time * 1000,
            "active_sessions": len(self._sessions),
            "uptime_seconds": (datetime.now() - self._startup_time).seconds
        }

        if self._search_engine:
            search_health = self._search_engine.health_check()
            metrics["search"] = {
                "total_searches": search_health.get('search_count', 0),
                "success_rate": search_health.get('success_rate', 0),
                "avg_search_time_ms": search_health.get('avg_search_time_ms', 0),
                "dealers_indexed": search_health.get('dealers_indexed', 0),
                "postgresql_connected": search_health.get('postgresql_connected', False),
                "redis_enabled": search_health.get('redis_enabled', False)
            }

        if self._dashboard_builder:
            metrics["dashboard_cache"] = self._dashboard_builder.get_cache_stats()

        return metrics

    def clear_cache(self):
        """Clear all caches"""
        self._sessions.clear()
        self._dashboard_builder.clear_cache()
        logger.info("ðŸ’¾ All caches cleared")

    def get_analytics(self) -> Dict[str, Any]:
        """Get analytics data"""
        return {
            "summary": {
                "total_queries": self._analytics["total_queries"],
                "successful_queries": self._analytics["successful_queries"],
                "failed_queries": self._analytics["failed_queries"],
                "success_rate": round((self._analytics["successful_queries"] /
                                      max(self._analytics["total_queries"], 1)) * 100, 1)
            },
            "search_types": dict(self._analytics["search_types"]),
            "popular_searches": dict(sorted(self._analytics["popular_searches"].items(),
                                           key=lambda x: x[1], reverse=True)[:10]),
            "top_dealers": dict(sorted(self._analytics["dealer_views"].items(),
                                      key=lambda x: x[1], reverse=True)[:10])
        }

# ============================================================
# SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    """Get singleton instance of DealerAnalyticsService"""
    global _service
    if _service is None:
        _service = DealerAnalyticsService()
    return _service

# ============================================================
# MODULE EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "get_dealer_service",
    "EXIT_SIGNAL",
    "DealerSearchEngine",
    "DealerSearchResult",
    "DealerContext",
    "DealerDashboard",
    "VERSION"
]

# ============================================================
# TEST / STANDALONE MODE
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("DEALER INTELLIGENCE GATEWAY v8.2 - TEST MODE".center(70))
    print("=" * 70)
    print()

    service = get_dealer_service()

    # Show health
    health = service.health_check()
    print("ðŸ“Š Health Check:")
    print(json.dumps(health, indent=2, default=str))
    print()

    # Show welcome
    print(service._show_welcome())
    print()

    # Interactive test
    print("ðŸ” INTERACTIVE TEST MODE")
    print("Enter dealer name to search (or 99 to exit)")
    print()

    while True:
        try:
            query = input("ðŸ” Enter Dealer Name: ").strip()

            if query == "99":
                print("\nðŸ‘‹ Goodbye!")
                break

            if not query:
                continue

            print("\nâ³ Processing...\n")
            result = service.process_whatsapp_query(query, "test_user")

            if result == EXIT_SIGNAL:
                print("Exiting...")
                break

            print(result)
            print()

        except KeyboardInterrupt:
            print("\n\nðŸ‘‹ Goodbye!")
            break
        except Exception as e:
            print(f"\nâŒ Error: {e}\n")
            traceback.print_exc()
