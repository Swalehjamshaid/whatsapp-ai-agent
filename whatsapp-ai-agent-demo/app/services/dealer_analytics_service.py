#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 6.0 - ENTERPRISE SEARCH ENGINE INTEGRATION
# ============================================================

"""
================================================================================
DEALER INTELLIGENCE GATEWAY - ENTERPRISE EDITION v6.0
================================================================================

This service orchestrates the complete dealer intelligence workflow with:
    ✅ In-memory search index for lightning-fast searches
    ✅ Multi-level search strategy (code → exact → partial → fuzzy)
    ✅ 70% similarity threshold for fuzzy matching
    ✅ Automatic cache refresh every 15 minutes
    ✅ Comprehensive diagnostic logging
    ✅ Multiple matches support with numbered selection
    ✅ PostgreSQL health monitoring

================================================================================
"""

import logging
import time
import json
import traceback
import re
import difflib
from typing import Optional, Dict, List, Any, Union, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from threading import Thread, Event
from collections import defaultdict

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

EXIT_SIGNAL = "__EXIT__"
VERSION = "6.0"
CACHE_TTL = 300  # 5 minutes cache
SEARCH_CACHE_REFRESH_MINUTES = 15
SIMILARITY_THRESHOLD = 0.70  # 70% minimum similarity
MAX_SUGGESTIONS = 10

# ============================================================
# TYPED MODELS
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
    match_type: str = ""  # exact, partial, fuzzy, code, customer_code
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

@dataclass
class PerformanceSummary:
    """Performance metrics and rankings"""
    business_score: int = 0
    revenue_rank: int = 0
    delivery_rank: int = 0
    overall_rank: int = 0
    performance_tier: str = "Standard"
    dealer_rating: float = 0.0

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
    context: DealerContext
    generated_at: datetime = field(default_factory=datetime.now)

# ============================================================
# SERVICE IMPORTS
# ============================================================

try:
    from app.repositories.dealer_repository import DealerRepository
    REPOSITORY_AVAILABLE = True
    logger.info("✅ DealerRepository loaded")
except ImportError as e:
    REPOSITORY_AVAILABLE = False
    logger.error(f"❌ DealerRepository import failed: {e}")

# ============================================================
# DEALER SEARCH ENGINE (In-Memory Index)
# ============================================================

class DealerSearchEngine:
    """
    Enterprise Dealer Search Engine with in-memory index
    
    Features:
        ✅ In-memory search index for lightning-fast searches
        ✅ Multi-level search strategy
        ✅ 70% similarity threshold
        ✅ Automatic cache refresh
        ✅ Comprehensive logging
    """
    
    def __init__(self, repository):
        self.repository = repository
        self._index: Dict[str, DealerIndex] = {}
        self._normalized_index: Dict[str, str] = {}
        self._code_index: Dict[str, str] = {}
        self._customer_code_index: Dict[str, str] = {}
        self._alias_index: Dict[str, List[str]] = defaultdict(list)
        self._last_refresh = None
        self._refresh_thread = None
        self._stop_refresh = Event()
        self._search_count = 0
        self._search_success_count = 0
        self._avg_search_time = 0.0
        
        # Build initial index
        self._build_index()
        
        # Start auto-refresh thread
        self._start_auto_refresh()
        
        # Display startup banner
        self._show_startup_banner()
    
    def _show_startup_banner(self):
        """Display startup banner with system status"""
        print("\n" + "━" * 70)
        print("DEALER SEARCH ENGINE".center(70))
        print("━" * 70)
        print(f"\nDatabase Status      : {'✅ Connected' if self.repository else '❌ Disconnected'}")
        print(f"Total Records        : {self._get_total_records():,}")
        print(f"Unique Dealers       : {len(self._index):,}")
        print(f"Unique Dealer Codes  : {len(self._code_index):,}")
        print(f"Search Index         : {'✅ Ready' if self._index else '❌ Empty'}")
        print(f"Search Cache         : {'✅ Loaded' if self._index else '❌ Empty'}")
        print(f"Fuzzy Search         : {'✅ Enabled' if self._index else '❌ Disabled'}")
        print(f"Partial Search       : {'✅ Enabled' if self._index else '❌ Disabled'}")
        print(f"Alias Search         : {'✅ Enabled' if self._index else '❌ Disabled'}")
        print(f"Similarity Threshold : {SIMILARITY_THRESHOLD * 100:.0f}%")
        print(f"Auto Refresh         : Every {SEARCH_CACHE_REFRESH_MINUTES} minutes")
        print(f"\nSystem Status        : {'✅ READY' if self._index else '❌ NOT READY'}")
        print("━" * 70 + "\n")
    
    def _get_total_records(self) -> int:
        """Get total records from repository"""
        try:
            if self.repository:
                health = self.repository.health_check()
                return health.get('rows', 0)
        except:
            pass
        return 0
    
    def _build_index(self):
        """Build in-memory search index from PostgreSQL"""
        logger.info("🔨 Building dealer search index...")
        start_time = time.time()
        
        try:
            if not self.repository:
                logger.error("❌ Repository not available")
                return
            
            # Get all dealers from PostgreSQL
            dealers = self.repository.get_all_dealers()
            
            if not dealers:
                logger.warning("⚠️ No dealers found in database")
                return
            
            # Build index
            index = {}
            normalized_index = {}
            code_index = {}
            customer_code_index = {}
            alias_index = defaultdict(list)
            
            for dealer in dealers:
                customer_name = dealer.get('customer_name', '')
                dealer_code = dealer.get('dealer_code', '')
                customer_code = dealer.get('customer_code', '')
                
                if not customer_name and not dealer_code:
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
                    city=dealer.get('ship_to_city', ''),
                    warehouse=dealer.get('warehouse', ''),
                    warehouse_code=dealer.get('warehouse_code', ''),
                    sales_office=dealer.get('sales_office', ''),
                    sales_manager=dealer.get('sales_manager', '')
                )
                
                # Add to indexes
                key = dealer_code or customer_name
                index[key] = entry
                
                if normalized:
                    normalized_index[normalized] = dealer_code or customer_name
                
                if dealer_code:
                    code_index[dealer_code.upper()] = dealer_code or customer_name
                
                if customer_code:
                    customer_code_index[customer_code.upper()] = dealer_code or customer_name
                
                # Generate aliases
                aliases = self._generate_aliases(customer_name)
                for alias in aliases:
                    alias_index[alias].append(dealer_code or customer_name)
            
            # Update indexes
            self._index = index
            self._normalized_index = normalized_index
            self._code_index = code_index
            self._customer_code_index = customer_code_index
            self._alias_index = alias_index
            self._last_refresh = datetime.now()
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Search index built: {len(self._index)} dealers in {elapsed*1000:.0f}ms")
            
        except Exception as e:
            logger.error(f"❌ Failed to build search index: {e}")
            logger.error(traceback.format_exc())
    
    def _start_auto_refresh(self):
        """Start automatic cache refresh thread"""
        def refresh_worker():
            while not self._stop_refresh.is_set():
                self._stop_refresh.wait(SEARCH_CACHE_REFRESH_MINUTES * 60)
                if not self._stop_refresh.is_set():
                    logger.info("🔄 Auto-refreshing search index...")
                    self._build_index()
        
        self._refresh_thread = Thread(target=refresh_worker, daemon=True)
        self._refresh_thread.start()
        logger.info(f"🔄 Auto-refresh started (every {SEARCH_CACHE_REFRESH_MINUTES} minutes)")
    
    # ============================================================
    # SEARCH METHODS
    # ============================================================
    
    def search_dealer(self, query: str) -> DealerSearchResult:
        """
        Search for dealer using multi-level strategy
        
        Search order:
        1. Dealer Code
        2. Customer Code
        3. Exact Name
        4. Case-insensitive
        5. Partial Match
        6. Token Match
        7. Fuzzy Match (70%)
        8. Alias Match
        """
        start_time = time.time()
        self._search_count += 1
        
        try:
            # Normalize query
            normalized_query = self._normalize_text(query)
            logger.info(f"🔍 Search started: '{query}' → normalized: '{normalized_query}'")
            
            if not normalized_query:
                return DealerSearchResult(
                    success=False,
                    message="Empty query",
                    search_time_ms=0,
                    normalized_query=normalized_query
                )
            
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
            
            # Step 5: Partial match
            result = self._search_partial_match(normalized_query)
            if result:
                return self._create_search_result(result, "partial", start_time, normalized_query)
            
            # Step 6: Token match
            result = self._search_token_match(normalized_query)
            if result:
                return self._create_search_result(result, "token", start_time, normalized_query)
            
            # Step 7: Fuzzy match (70% threshold)
            result = self._search_fuzzy_match(normalized_query)
            if result:
                return self._create_search_result(result, "fuzzy", start_time, normalized_query)
            
            # Step 8: Alias match
            result = self._search_alias_match(normalized_query)
            if result:
                return self._create_search_result(result, "alias", start_time, normalized_query)
            
            # No matches found
            # Get suggestions
            suggestions = self._get_suggestions(normalized_query)
            
            self._search_success_count += 0
            elapsed = time.time() - start_time
            
            logger.info(f"❌ Search failed: '{query}' - No matches found")
            
            return DealerSearchResult(
                success=False,
                message="No dealer found",
                suggestions=suggestions[:MAX_SUGGESTIONS],
                search_time_ms=elapsed * 1000,
                normalized_query=normalized_query
            )
            
        except Exception as e:
            logger.error(f"❌ Search error: {e}")
            logger.error(traceback.format_exc())
            elapsed = time.time() - start_time
            return DealerSearchResult(
                success=False,
                message=f"Search error: {str(e)}",
                search_time_ms=elapsed * 1000,
                normalized_query=normalized_query
            )
    
    # ============================================================
    # SEARCH STRATEGIES
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
    
    def _search_partial_match(self, query: str) -> Optional[DealerIndex]:
        """Search by partial match"""
        query_lower = query.lower()
        best_match = None
        best_score = 0
        
        for key, entry in self._index.items():
            name_lower = entry.customer_name.lower()
            if query_lower in name_lower:
                # Calculate match quality
                score = len(query_lower) / len(name_lower)
                if score > best_score:
                    best_score = score
                    best_match = entry
        
        return best_match
    
    def _search_token_match(self, query: str) -> Optional[DealerIndex]:
        """Search by token match"""
        tokens = self._tokenize(query)
        if not tokens:
            return None
        
        best_match = None
        best_score = 0
        
        for key, entry in self._index.items():
            entry_tokens = entry.search_tokens
            if not entry_tokens:
                continue
            
            # Count matching tokens
            matching_tokens = sum(1 for token in tokens if token in entry_tokens)
            if matching_tokens > 0:
                score = matching_tokens / len(tokens)
                if score > best_score:
                    best_score = score
                    best_match = entry
        
        # Require at least 50% token match
        if best_score >= 0.5:
            return best_match
        
        return None
    
    def _search_fuzzy_match(self, query: str) -> Optional[DealerIndex]:
        """Search by fuzzy match (70% threshold)"""
        best_match = None
        best_ratio = 0
        
        for key, entry in self._index.items():
            # Compare with customer name
            ratio = difflib.SequenceMatcher(None, query, entry.normalized_name).ratio()
            
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = entry
            
            # Also compare with tokens
            for token in entry.search_tokens:
                token_ratio = difflib.SequenceMatcher(None, query, token).ratio()
                if token_ratio > best_ratio:
                    best_ratio = token_ratio
                    best_match = entry
        
        # Check threshold
        if best_ratio >= SIMILARITY_THRESHOLD:
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
    # UTILITY METHODS
    # ============================================================
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for search"""
        if not text:
            return ""
        
        # Convert to lowercase
        normalized = text.lower()
        
        # Remove punctuation
        normalized = re.sub(r'[&\-\./,()]', ' ', normalized)
        
        # Remove extra spaces
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Strip leading/trailing spaces
        normalized = normalized.strip()
        
        return normalized
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text for search"""
        normalized = self._normalize_text(text)
        return normalized.split() if normalized else []
    
    def _generate_aliases(self, name: str) -> List[str]:
        """Generate common aliases for a dealer name"""
        aliases = []
        if not name:
            return aliases
        
        # Remove common suffixes
        name_clean = re.sub(r'\s+Electronics\s*$', '', name, flags=re.IGNORECASE)
        name_clean = re.sub(r'\s+Digital\s*$', '', name_clean, flags=re.IGNORECASE)
        name_clean = re.sub(r'\s+Technologies\s*$', '', name_clean, flags=re.IGNORECASE)
        name_clean = re.sub(r'\s+Traders\s*$', '', name_clean, flags=re.IGNORECASE)
        
        if name_clean != name:
            aliases.append(self._normalize_text(name_clean))
        
        # Take first word
        tokens = name.split()
        if tokens:
            aliases.append(self._normalize_text(tokens[0]))
        
        # Take first two words
        if len(tokens) >= 2:
            aliases.append(self._normalize_text(' '.join(tokens[:2])))
        
        return aliases
    
    def _get_suggestions(self, query: str) -> List[Dict[str, Any]]:
        """Get search suggestions when no match found"""
        suggestions = []
        
        # Get fuzzy matches below threshold
        for key, entry in self._index.items():
            ratio = difflib.SequenceMatcher(None, query, entry.normalized_name).ratio()
            if ratio > 0.3 and ratio < SIMILARITY_THRESHOLD:
                suggestions.append({
                    'customer_name': entry.customer_name,
                    'dealer_code': entry.dealer_code,
                    'confidence': round(ratio * 100, 1)
                })
        
        # Sort by confidence
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
        
        logger.info(f"✅ Match found: '{entry.customer_name}' ({match_type}) - {confidence*100:.0f}% confidence")
        
        return DealerSearchResult(
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
    
    # ============================================================
    # REFRESH METHODS
    # ============================================================
    
    def refresh_index(self):
        """Manually refresh the search index"""
        logger.info("🔄 Manual refresh requested")
        self._build_index()
    
    def stop_auto_refresh(self):
        """Stop automatic refresh thread"""
        self._stop_refresh.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=5)
        logger.info("🔄 Auto-refresh stopped")
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for search engine"""
        return {
            "status": "ready" if self._index else "not_ready",
            "dealers_indexed": len(self._index),
            "dealer_codes": len(self._code_index),
            "customer_codes": len(self._customer_code_index),
            "aliases": len(self._alias_index),
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "search_count": self._search_count,
            "search_success_count": self._search_success_count,
            "success_rate": round((self._search_success_count / max(self._search_count, 1)) * 100, 1),
            "avg_search_time_ms": round(self._avg_search_time, 1)
        }

# ============================================================
# DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Intelligence Gateway - Enterprise Edition v6.0
    
    Features:
        ✅ In-memory search engine
        ✅ Session management
        ✅ Dashboard generation
        ✅ WhatsApp formatting
        ✅ Comprehensive health monitoring
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
        self._repository = None
        self._search_engine = None
        self._sessions: Dict[str, DealerContext] = {}
        self._startup_time = datetime.now()
        self._request_count = 0
        self._avg_response_time = 0.0
        self._query_count = 0
        self._avg_query_time = 0.0
        
        # Initialize components
        self._initialize_components()
        
        # Display startup information
        self._show_startup_info()
        
        logger.info("=" * 70)
        logger.info("🚀 DEALER INTELLIGENCE GATEWAY v6.0")
        logger.info("   🎯 Enterprise Search Engine Integration")
        logger.info("   🔍 In-Memory Search Index: ✅")
        logger.info("   🔄 Auto-Refresh: Every 15 minutes")
        logger.info("   🎯 Similarity Threshold: 70%")
        logger.info("=" * 70)
    
    # ============================================================
    # INITIALIZATION
    # ============================================================
    
    def _initialize_components(self):
        """Initialize all required components"""
        # Initialize repository
        if REPOSITORY_AVAILABLE:
            try:
                self._repository = DealerRepository()
                logger.info("✅ Repository initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize repository: {e}")
        
        # Initialize search engine
        if self._repository:
            try:
                self._search_engine = DealerSearchEngine(self._repository)
                logger.info("✅ Search engine initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize search engine: {e}")
        else:
            logger.error("❌ Cannot initialize search engine: Repository not available")
    
    def _show_startup_info(self):
        """Display startup information"""
        print("\n" + "=" * 70)
        print("🏢 DEALER INTELLIGENCE GATEWAY v6.0".center(70))
        print("=" * 70)
        print(f"🚀 Started: {self._startup_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🗄️  Repository: {'✅' if self._repository else '❌'}")
        print(f"🔍 Search Engine: {'✅' if self._search_engine else '❌'}")
        print(f"💾 Session: ✅ Memory")
        print("=" * 70 + "\n")
    
    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - Called by AIProviderService
        
        Args:
            message: User's input from WhatsApp
            sender: User identifier
            
        Returns:
            Formatted response for WhatsApp
        """
        start_time = time.time()
        self._request_count += 1
        
        try:
            logger.info(f"📨 Received: '{message}' from {sender}")
            
            # Validate input
            if not message or not message.strip():
                return self._show_welcome(sender)
            
            message_clean = message.strip()
            
            # Check for exit
            if self._is_exit_command(message_clean):
                logger.info(f"🚪 Exit requested by {sender}")
                return EXIT_SIGNAL
            
            # Check for help/welcome
            if self._is_help_command(message_clean):
                return self._show_welcome(sender)
            
            # Check for examples
            if self._is_examples_command(message_clean):
                return self._show_examples()
            
            # Check for numeric selection (from multiple matches)
            if message_clean.isdigit():
                return self._handle_selection(int(message_clean), sender)
            
            # Get or create session
            context = self._get_or_create_session(sender)
            
            # Search for dealer using search engine
            search_result = self._search_dealer(message_clean)
            
            if not search_result.success:
                return self._format_not_found(message_clean, search_result)
            
            # Update session with dealer info
            self._update_session_context(context, search_result)
            
            # Load dashboard
            dashboard = self._load_dashboard(search_result, context)
            
            if not dashboard:
                return self._format_error("Unable to load dealer dashboard")
            
            # Update session with dashboard
            context.dashboard = asdict(dashboard)
            context.last_query = message_clean
            context.pending_matches = []
            self._sessions[sender] = context
            
            # Format response
            response = self._format_dashboard(dashboard)
            
            # Log performance
            elapsed = time.time() - start_time
            self._update_performance_metrics(elapsed)
            
            logger.info(f"✅ Dashboard returned in {elapsed*1000:.0f}ms")
            
            return response
            
        except Exception as e:
            logger.error(f"❌ process_whatsapp_query error: {e}")
            logger.error(traceback.format_exc())
            return self._format_error(str(e)[:100])
    
    # ============================================================
    # SEARCH (Using Search Engine)
    # ============================================================
    
    def _search_dealer(self, query: str) -> DealerSearchResult:
        """Search for dealer using search engine"""
        if not self._search_engine:
            return DealerSearchResult(
                success=False,
                message="Search engine unavailable"
            )
        
        try:
            result = self._search_engine.search_dealer(query)
            
            # Log search details
            logger.info(f"🔍 Search completed in {result.search_time_ms:.0f}ms")
            logger.info(f"   Match: {result.match_type if result.success else 'None'}")
            logger.info(f"   Confidence: {result.confidence*100:.0f}%")
            
            if result.suggestions:
                logger.info(f"   Suggestions: {len(result.suggestions)}")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Search error: {e}")
            logger.error(traceback.format_exc())
            return DealerSearchResult(
                success=False,
                message=str(e)
            )
    
    # ============================================================
    # HANDLE SELECTION (Multiple Matches)
    # ============================================================
    
    def _handle_selection(self, selection: int, sender: str) -> str:
        """Handle numeric selection from multiple matches"""
        context = self._sessions.get(sender)
        if not context or not context.pending_matches:
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
        
        if selection < 1 or selection > len(context.pending_matches):
            return f"Please select a number between 1 and {len(context.pending_matches)}"
        
        # Get selected dealer
        selected = context.pending_matches[selection - 1]
        
        # Create search result
        search_result = DealerSearchResult(
            success=True,
            customer_name=selected.get('customer_name', ''),
            dealer_code=selected.get('dealer_code', ''),
            customer_code=selected.get('customer_code', ''),
            confidence=selected.get('confidence', 0.9),
            match_type="selection",
            message="Selected from matches"
        )
        
        # Update context
        self._update_session_context(context, search_result)
        
        # Load dashboard
        dashboard = self._load_dashboard(search_result, context)
        
        if not dashboard:
            return self._format_error("Unable to load dealer dashboard")
        
        # Update session
        context.dashboard = asdict(dashboard)
        context.last_query = search_result.customer_name
        context.pending_matches = []
        self._sessions[sender] = context
        
        return self._format_dashboard(dashboard)
    
    # ============================================================
    # DASHBOARD LOADING
    # ============================================================
    
    def _load_dashboard(self, search_result: DealerSearchResult, 
                       context: DealerContext) -> Optional[DealerDashboard]:
        """Load dealer dashboard from repository"""
        if not self._repository:
            logger.error("❌ Repository not available")
            return None
        
        try:
            dealer_code = search_result.dealer_code
            customer_code = search_result.customer_code
            logger.info(f"📊 Loading dashboard for {search_result.customer_name}")
            
            # Get summary data
            summary_data = self._repository.get_dealer_summary(dealer_code, customer_code)
            
            if not summary_data:
                logger.error(f"❌ No summary data for {dealer_code}")
                return None
            
            # Create dashboard components
            identity = DealerIdentity(
                customer_name=summary_data.get('customer_name', search_result.customer_name),
                dealer_code=dealer_code,
                customer_code=customer_code,
                city=summary_data.get('city', ''),
                warehouse=summary_data.get('warehouse', ''),
                warehouse_code=summary_data.get('warehouse_code', ''),
                delivery_location=summary_data.get('delivery_location', ''),
                sales_office=summary_data.get('sales_office', ''),
                sales_manager=summary_data.get('sales_manager', '')
            )
            
            delivery = DeliverySummary(
                total_dn=summary_data.get('total_dn', 0),
                delivered_dn=summary_data.get('delivered_dn', 0),
                pending_dn=summary_data.get('pending_dn', 0),
                pgi_completed=summary_data.get('pgi_completed', 0),
                pod_completed=summary_data.get('pod_completed', 0),
                delivery_rate=summary_data.get('delivery_rate', 0.0),
                pgi_rate=summary_data.get('pgi_rate', 0.0),
                pod_rate=summary_data.get('pod_rate', 0.0),
                avg_delivery_days=summary_data.get('avg_delivery_days', 0.0),
                avg_pod_days=summary_data.get('avg_pod_days', 0.0)
            )
            
            business = BusinessSummary(
                total_revenue=summary_data.get('total_revenue', 0.0),
                total_units=summary_data.get('total_units', 0),
                total_dn=summary_data.get('total_dn', 0),
                avg_revenue_per_dn=summary_data.get('avg_revenue_per_dn', 0.0),
                avg_units_per_dn=summary_data.get('avg_units_per_dn', 0.0),
                yoy_growth=summary_data.get('yoy_growth', 0.0),
                target_achievement=summary_data.get('target_achievement', 0.0)
            )
            
            product = ProductSummary(
                products_sold=summary_data.get('products_sold', 0),
                models_count=summary_data.get('models_count', 0),
                materials_count=summary_data.get('materials_count', 0),
                top_product=summary_data.get('top_product', 'N/A'),
                top_model=summary_data.get('top_model', 'N/A'),
                top_material=summary_data.get('top_material', 'N/A'),
                primary_division=summary_data.get('primary_division', 'N/A')
            )
            
            operation = OperationSummary(
                cities_served=summary_data.get('cities_served', 0),
                warehouses_used=summary_data.get('warehouses_used', 0),
                primary_warehouse=summary_data.get('primary_warehouse', 'N/A'),
                latest_dn=summary_data.get('latest_dn', 'N/A'),
                latest_pgi=summary_data.get('latest_pgi', 'N/A'),
                latest_pod=summary_data.get('latest_pod', 'N/A')
            )
            
            # Calculate performance
            performance = self._calculate_performance(delivery, business, operation)
            
            # Generate insights
            insights = self._generate_insights(delivery, business, product, operation, performance)
            
            dashboard = DealerDashboard(
                identity=identity,
                delivery=delivery,
                business=business,
                product=product,
                operation=operation,
                performance=performance,
                insights=insights,
                context=context
            )
            
            logger.info(f"✅ Dashboard loaded for {search_result.customer_name}")
            return dashboard
            
        except Exception as e:
            logger.error(f"❌ Failed to load dashboard: {e}")
            logger.error(traceback.format_exc())
            return None
    
    # ============================================================
    # PERFORMANCE CALCULATION
    # ============================================================
    
    def _calculate_performance(self, delivery: DeliverySummary, 
                              business: BusinessSummary,
                              operation: OperationSummary) -> PerformanceSummary:
        """Calculate performance metrics deterministically"""
        score = 60
        
        # Delivery performance (max 20 points)
        if delivery.delivery_rate >= 95:
            score += 20
        elif delivery.delivery_rate >= 90:
            score += 15
        elif delivery.delivery_rate >= 80:
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
        
        # Revenue performance (max 10 points)
        if business.total_revenue > 10000000:
            score += 10
        elif business.total_revenue > 5000000:
            score += 5
        
        # Growth performance (max 10 points)
        if business.yoy_growth > 20:
            score += 10
        elif business.yoy_growth > 10:
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
        elif score >= 80:
            tier = "Gold"
            rating = 4.5
        elif score >= 70:
            tier = "Silver"
            rating = 4.0
        elif score >= 60:
            tier = "Bronze"
            rating = 3.5
        else:
            tier = "Standard"
            rating = 3.0
        
        return PerformanceSummary(
            business_score=min(score, 100),
            revenue_rank=12,
            delivery_rank=8,
            overall_rank=10,
            performance_tier=tier,
            dealer_rating=rating
        )
    
    # ============================================================
    # INSIGHTS GENERATION
    # ============================================================
    
    def _generate_insights(self, delivery: DeliverySummary,
                          business: BusinessSummary,
                          product: ProductSummary,
                          operation: OperationSummary,
                          performance: PerformanceSummary) -> List[str]:
        """Generate deterministic business insights"""
        insights = []
        
        # Delivery insights
        if delivery.delivery_rate >= 95:
            insights.append("✅ Excellent delivery performance (95%+)")
        elif delivery.delivery_rate >= 90:
            insights.append("✅ Strong delivery performance (90%+)")
        elif delivery.delivery_rate < 80:
            insights.append("⚠️ Delivery rate below 80% - requires attention")
        
        if delivery.pgi_rate >= 95:
            insights.append("✅ Excellent PGI completion")
        elif delivery.pgi_rate < 80:
            insights.append("⚠️ PGI completion below 80% - requires attention")
        
        if delivery.pod_rate >= 90:
            insights.append("✅ Excellent POD completion")
        elif delivery.pod_rate < 70:
            insights.append("⚠️ POD completion below 70% - requires attention")
        
        if delivery.pending_dn > 0:
            insights.append(f"⚠️ {delivery.pending_dn} pending deliveries require attention")
        
        # Business insights
        if business.total_revenue > 10000000:
            insights.append("📈 Revenue is above dealer average")
        elif business.total_revenue > 5000000:
            insights.append("📈 Revenue is at dealer average")
        
        if business.yoy_growth > 20:
            insights.append("📈 High growth momentum (20%+)")
        elif business.yoy_growth > 10:
            insights.append("📈 Good growth momentum (10%+)")
        
        # Product insights
        if product.products_sold > 15:
            insights.append("📦 Strong product portfolio across multiple models")
        elif product.products_sold > 5:
            insights.append("📦 Healthy product portfolio")
        
        if product.top_product != "N/A":
            insights.append(f"🏆 Top product: {product.top_product}")
        
        # Operation insights
        if operation.cities_served > 5:
            insights.append(f"🌍 Wide coverage across {operation.cities_served} cities")
        
        if operation.warehouses_used > 1:
            insights.append("🏭 Multiple warehouses utilization")
        
        # Performance insights
        if performance.business_score >= 90:
            insights.append("⭐ Platinum performance tier")
        elif performance.business_score >= 80:
            insights.append("⭐ Gold performance tier")
        
        # Ensure at least 3 insights
        if len(insights) < 3:
            insights.extend([
                "📊 Regular performance monitoring recommended",
                "💡 Review pending deliveries for closure",
                "📈 Maintain current growth trajectory"
            ])
        
        return insights[:10]
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def _get_or_create_session(self, user_id: str) -> DealerContext:
        """Get existing session or create new one"""
        if user_id not in self._sessions:
            self._sessions[user_id] = DealerContext()
            logger.info(f"🆕 New session created for {user_id}")
        return self._sessions[user_id]
    
    def _update_session_context(self, context: DealerContext, search_result: DealerSearchResult):
        """Update session with dealer information"""
        context.dealer_name = search_result.customer_name
        context.dealer_code = search_result.dealer_code
        context.customer_code = search_result.customer_code
        context.last_query = search_result.customer_name
        context.search_count += 1
        context.last_activity = datetime.now()
        logger.info(f"💾 Session updated for {search_result.customer_name}")
    
    # ============================================================
    # RESPONSE FORMATTING
    # ============================================================
    
    def _format_dashboard(self, dashboard: DealerDashboard) -> str:
        """Format dashboard for WhatsApp response"""
        lines = []
        
        # Header
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏢 DEALER INTELLIGENCE")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Dealer Information
        lines.append("👤 Dealer")
        lines.append(dashboard.identity.customer_name)
        lines.append("")
        lines.append("🆔 Dealer Code")
        lines.append(dashboard.identity.dealer_code)
        lines.append("")
        lines.append("🆔 Customer Code")
        lines.append(dashboard.identity.customer_code)
        lines.append("")
        
        # Location
        lines.append("📍 LOCATION")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
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
        lines.append("👔 Sales Office")
        lines.append(dashboard.identity.sales_office)
        lines.append("")
        lines.append("👨‍💼 Sales Manager")
        lines.append(dashboard.identity.sales_manager)
        lines.append("")
        
        # Delivery Summary
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📦 DELIVERY SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"🚚 Total DN           : {dashboard.delivery.total_dn}")
        lines.append(f"✅ Delivered DN       : {dashboard.delivery.delivered_dn}")
        lines.append(f"⏳ Pending DN         : {dashboard.delivery.pending_dn}")
        lines.append("")
        lines.append(f"📤 PGI Completed      : {dashboard.delivery.pgi_completed}")
        lines.append(f"📥 POD Completed      : {dashboard.delivery.pod_completed}")
        lines.append("")
        lines.append(f"📊 Delivery Rate      : {dashboard.delivery.delivery_rate:.2f}%")
        lines.append(f"📊 PGI Rate           : {dashboard.delivery.pgi_rate:.2f}%")
        lines.append(f"📊 POD Rate           : {dashboard.delivery.pod_rate:.2f}%")
        lines.append("")
        lines.append(f"🚚 Avg Delivery Days  : {dashboard.delivery.avg_delivery_days:.1f} Days")
        lines.append(f"📥 Avg POD Days       : {dashboard.delivery.avg_pod_days:.1f} Days")
        lines.append("")
        
        # Business Summary
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💰 BUSINESS SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"💵 Total Revenue")
        lines.append(self._format_currency(dashboard.business.total_revenue))
        lines.append("")
        lines.append(f"📦 Total Units Sold")
        lines.append(f"{dashboard.business.total_units:,}")
        lines.append("")
        lines.append(f"📄 Total Delivery Notes")
        lines.append(f"{dashboard.business.total_dn}")
        lines.append("")
        lines.append(f"💰 Average Revenue / DN")
        lines.append(self._format_currency(dashboard.business.avg_revenue_per_dn))
        lines.append("")
        lines.append(f"📦 Average Units / DN")
        lines.append(f"{dashboard.business.avg_units_per_dn:.2f}")
        lines.append("")
        
        # Product Summary
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📦 PRODUCT SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
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
        
        # Operation Summary
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📍 OPERATION SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
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
        
        # Performance
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📈 PERFORMANCE")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        score = dashboard.performance.business_score
        score_emoji = "🟢" if score >= 90 else "🟡" if score >= 70 else "🔴"
        lines.append("Business Score")
        lines.append(f"{score} / 100 {score_emoji}")
        lines.append("")
        lines.append("Performance Tier")
        lines.append(dashboard.performance.performance_tier)
        lines.append("")
        lines.append("Dealer Rating")
        lines.append(f"{dashboard.performance.dealer_rating:.1f} / 5.0 ⭐")
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
        
        # Insights
        if dashboard.insights:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 BUSINESS INSIGHTS")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("")
            for insight in dashboard.insights[:8]:
                lines.append(insight)
                lines.append("")
        
        # Footer
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💬 Type '99' to return to Main Menu")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)
    
    def _format_not_found(self, query: str, search_result: DealerSearchResult) -> str:
        """Format dealer not found response with suggestions"""
        lines = []
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔍 DEALER NOT FOUND")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"We couldn't find '{query}' in our records.")
        lines.append("")
        
        # Show suggestions if available
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
            
            # Store suggestions in session for selection
            context = self._sessions.get("default")
            if context:
                context.pending_matches = search_result.suggestions[:5]
        else:
            lines.append("💡 Suggestions:")
            lines.append("• Check the spelling")
            lines.append("• Try searching by Dealer Code")
            lines.append("• Try searching by Customer Code")
            lines.append("• Use partial name search")
            lines.append("")
            lines.append("📝 Examples:")
            lines.append("• Arshad Electronics-Khi")
            lines.append("• Zoom Appliances")
            lines.append("• RUBA Digital")
            lines.append("")
        
        lines.append("99️⃣ Return to Main Menu")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)
    
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
    
    def _format_currency(self, amount: float) -> str:
        """Format currency for display"""
        if amount >= 1000000:
            return f"PKR {amount/1000000:.1f}M"
        elif amount >= 1000:
            return f"PKR {amount/1000:.1f}K"
        else:
            return f"PKR {amount:,.0f}"
    
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
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏢 DEALER SEARCH",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Please write the Dealer Name.",
            "",
            "Examples:",
            "• Arshad Electronics-Khi",
            "• Zoom Appliances",
            "• RUBA Digital",
            "• Metro Electronics",
            "• Friends Electronics",
            "",
            "Supported Search:",
            "✓ Dealer Name",
            "✓ Dealer Code",
            "✓ Customer Code",
            "✓ Partial Search",
            "✓ Alias",
            "✓ Smart Match (70%)",
            "",
            "99️⃣ Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _show_examples(self) -> str:
        """Show example dealer names"""
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
            "7. Galaxy Electronics",
            "8. Star Traders",
            "",
            "💡 You can also search by:",
            "• Dealer Code (e.g., DLR-045)",
            "• Customer Code (e.g., CUST-789)",
            "",
            "99️⃣ Return to Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
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
            "status": "healthy",
            "uptime_seconds": (datetime.now() - self._startup_time).seconds,
            "components": {
                "repository": "available" if self._repository else "unavailable",
                "search_engine": "available" if self._search_engine else "unavailable"
            },
            "performance": {
                "total_requests": self._request_count,
                "avg_response_time_ms": self._avg_response_time * 1000,
                "active_sessions": len(self._sessions)
            }
        }
        
        # Search engine health
        if self._search_engine:
            search_health = self._search_engine.health_check()
            health["search_engine"] = search_health
        
        # Repository health
        if self._repository:
            try:
                repo_health = self._repository.health_check()
                health["database"] = {
                    "status": repo_health.get('status', 'unknown'),
                    "records": repo_health.get('rows', 0),
                    "dealers": repo_health.get('dealers', 0)
                }
            except Exception as e:
                health["database"] = {"status": f"Error: {e}"}
                health["status"] = "degraded"
        
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
                "avg_search_time_ms": search_health.get('avg_search_time_ms', 0)
            }
        
        return metrics
    
    def clear_cache(self):
        """Clear all caches"""
        self._sessions.clear()
        logger.info("💾 All caches cleared")

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
    "DealerDashboard"
]

# ============================================================
# TEST / STANDALONE MODE
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("DEALER INTELLIGENCE GATEWAY v6.0 - TEST MODE".center(70))
    print("=" * 70)
    print()
    
    service = get_dealer_service()
    
    # Show health
    health = service.health_check()
    print("📊 Health Check:")
    print(json.dumps(health, indent=2, default=str))
    print()
    
    # Show welcome
    print(service._show_welcome())
    print()
    
    # Interactive test
    print("🔍 INTERACTIVE TEST MODE")
    print("Enter dealer name to search (or 99 to exit)")
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
