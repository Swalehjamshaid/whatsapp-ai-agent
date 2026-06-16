# ==========================================================
# FILE: app/schemas/schema_service.py (v5.0 - PRODUCTION)
# ==========================================================
# PURPOSE: Schema Metadata Service - Single Source of Truth
# ARCHITECTURE: Loads dealers, cities, warehouses from database
# COMPATIBILITY: 100% backward compatible with AIQueryService
# ==========================================================

from typing import Dict, List, Optional, Tuple, Set, Any
from threading import Lock
import logging
import re
from datetime import datetime

# ==========================================================
# LOGGING SETUP
# ==========================================================

logger = logging.getLogger(__name__)


# ==========================================================
# DN PATTERN (8-12 digits - Compatible with AIQueryService)
# ==========================================================

DN_PATTERN = re.compile(r'\b(\d{8,12})\b')


# ==========================================================
# INTENT KEYWORDS (PRESERVED - Must match AIQueryService)
# ==========================================================

INTENT_KEYWORDS: Dict[str, List[Tuple[str, int]]] = {
    # Existing intents (DO NOT REMOVE - AIQueryService depends on them)
    "pending_pgi": [("pending pgi", 10), ("pgi pending", 10), ("open pgi", 8)],
    "pending_pod": [("pending pod", 10), ("pod pending", 10), ("open pod", 8)],
    "pgi_aging": [("pgi aging", 10), ("aging pgi", 10), ("pgi delay", 8)],
    "pod_aging": [("pod aging", 10), ("aging pod", 10), ("pod delay", 8)],
    "top_dealers": [("top dealer", 10), ("best dealer", 9), ("top performing", 8)],
    "bottom_dealers": [("bottom dealer", 10), ("worst dealer", 9), ("poor performing", 8)],
    "executive_insight": [("executive insight", 10), ("key issue", 8), ("bottleneck", 8)],
    "root_cause": [("root cause", 10), ("why", 8), ("reason", 8)],
    "general_ai": [("hello", 5), ("hi", 5), ("hey", 5), ("how are you", 6)],
    
    # New intents (can be added safely)
    "dn_lookup": [("dn", 10), ("delivery note", 10), ("track dn", 10)],
    "dealer_dashboard": [("dealer", 9), ("show dealer", 10), ("dealer dashboard", 9)],
    "dealer_revenue": [("revenue", 8), ("sales", 8), ("total revenue", 9)],
    "dealer_units": [("units", 8), ("quantity", 8), ("total units", 9)],
    "dealer_performance": [("performance", 8), ("kpi", 8), ("dealer performance", 9)],
    "dealer_aging": [("aging", 8), ("delay", 8), ("pending", 7)],
    "warehouse_dashboard": [("warehouse", 9), ("show warehouse", 10), ("warehouse summary", 9)],
    "warehouse_performance": [("warehouse performance", 9), ("warehouse kpi", 9)],
    "top_warehouses": [("top warehouse", 10), ("best warehouse", 9)],
    "help": [("help", 10), ("menu", 8), ("commands", 8)],
    "control_tower": [("control tower", 10), ("critical", 8), ("urgent", 8)],
    "trend": [("trend", 8), ("month over month", 9)],
    "comparison": [("compare", 8), ("vs", 7), ("versus", 7)],
}

# ==========================================================
# METRIC KEYWORDS (PRESERVED)
# ==========================================================

METRIC_KEYWORDS: Dict[str, List[str]] = {
    "revenue": ["revenue", "sales", "amount", "total revenue", "total sales"],
    "units": ["units", "quantity", "qty", "total units"],
    "dn_count": ["dns", "delivery notes", "orders", "total dns"],
    "pending_pod": ["pending pod", "pod pending", "pod not done"],
    "pending_delivery": ["pending delivery", "delivery pending", "pending pgi"],
    "delivery_aging": ["delivery aging", "pgi aging", "delivery delay"],
    "pod_aging": ["pod aging", "pod delay", "pod latency"],
    "pod_rate": ["pod rate", "pod percentage", "pod completion"],
    "pgi_rate": ["pgi rate", "pgi percentage", "pgi completion"],
    "delivery_rate": ["delivery rate", "delivery percentage", "delivery completion"],
}

# ==========================================================
# LOGISTICS KEYWORDS (Reject List)
# ==========================================================

LOGISTICS_KEYWORDS: Set[str] = {
    'pending', 'delivered', 'in_transit', 'dispatched', 'shipped', 'received',
    'pgi', 'pod', 'aging', 'delivery', 'revenue', 'units', 'performance',
    'critical', 'alert', 'urgent', 'priority', 'control', 'tower',
    'help', 'menu', 'status', 'what', 'how', 'why', 'when', 'where',
    'who', 'which', 'can', 'could', 'would', 'should', 'is', 'are',
    'show', 'display', 'get', 'tell', 'view', 'list', 'fetch',
    'warehouse', 'summary', 'report', 'kpi', 'dashboard', 'insight',
    'issue', 'problem', 'bottleneck', 'root', 'cause', 'reason',
    'transit', 'delivered', 'rate', 'completion', 'dn', 'order',
    'compare', 'versus', 'vs', 'between', 'against',
    'today', 'yesterday', 'week', 'month', 'year', 'last', 'this',
    'current', 'day', 'week', 'month', 'year', 'all',
    'top', 'bottom', 'best', 'worst', 'highest', 'lowest', 'average',
    'total', 'all', 'some', 'most', 'least', 'more', 'less',
    'dealer', 'customer', 'warehouse', 'city', 'stock', 'inventory',
}

# ==========================================================
# BUSINESS RULES (STATIC)
# ==========================================================

BUSINESS_RULES: Dict[str, Any] = {
    "delivery_aging": {
        "rule": "IF PGI EXISTS THEN delivery_aging = PGI_Date - DN_Creation_Date ELSE delivery_aging = Today - DN_Creation_Date",
        "thresholds": {"critical": 30, "high": 15, "medium": 7, "low": 3}
    },
    "pod_aging": {
        "rule": "IF POD EXISTS THEN pod_aging = POD_Date - PGI_Date ELSE pod_aging = Today - PGI_Date",
        "thresholds": {"critical": 30, "high": 15, "medium": 7, "low": 3}
    },
    "sla": {
        "delivery_aging_target": 7,
        "pod_aging_target": 7,
        "delivery_rate_target": 90,
        "pod_rate_target": 90
    }
}

# ==========================================================
# STATUS DEFINITIONS (STATIC)
# ==========================================================

STATUS_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "dn_status": {
        "delivered": "✅ Delivered - POD Received",
        "in_transit": "🚚 In Transit - PGI Done, POD Pending",
        "pending_pgi": "⏳ Pending PGI - Not Yet Dispatched",
        "unknown": "❓ Status Unknown",
    },
    "risk_status": {
        "critical": "🔴 CRITICAL - Immediate Attention Required",
        "high": "🟠 HIGH - Action Required",
        "medium": "🟡 MEDIUM - Monitor Closely",
        "low": "🟢 LOW - Normal Operations",
    }
}


# ==========================================================
# REPOSITORY (Embedded - No external dependency)
# ==========================================================

class DeliveryRepository:
    """Embedded repository for Delivery Report database operations."""
    
    def __init__(self, db_session=None):
        self._session = db_session
    
    def _get_session(self):
        """Get database session."""
        if self._session is None:
            try:
                from app.database import get_db
                self._session = next(get_db())
            except ImportError:
                logger.error("❌ Cannot import database session")
                raise RuntimeError("Database session not available")
        return self._session
    
    def get_distinct_customers(self) -> List[Dict[str, Any]]:
        """
        Get all unique customer/dealer names from delivery reports.
        Maps to: sold_to_party_name
        
        Returns:
            List of dicts with customer_name
        """
        try:
            session = self._get_session()
            
            try:
                from app.models.delivery_report import DeliveryReport
            except ImportError:
                logger.error("❌ Cannot import DeliveryReport model")
                return []
            
            results = session.query(
                DeliveryReport.sold_to_party_name.label('customer_name')
            ).filter(
                DeliveryReport.sold_to_party_name.isnot(None)
            ).filter(
                DeliveryReport.sold_to_party_name != ''
            ).distinct().order_by(
                DeliveryReport.sold_to_party_name
            ).all()
            
            dealers = [{"customer_name": r[0]} for r in results if r[0]]
            logger.info(f"✅ Loaded {len(dealers)} distinct customers")
            return dealers
            
        except Exception as e:
            logger.error(f"❌ Failed to load distinct customers: {e}")
            return []
    
    def get_distinct_cities(self) -> List[Dict[str, Any]]:
        """
        Get all unique ship-to cities from delivery reports.
        Maps to: ship_to_city
        
        Returns:
            List of dicts with city
        """
        try:
            session = self._get_session()
            
            try:
                from app.models.delivery_report import DeliveryReport
            except ImportError:
                logger.error("❌ Cannot import DeliveryReport model")
                return []
            
            results = session.query(
                DeliveryReport.ship_to_city.label('city')
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).filter(
                DeliveryReport.ship_to_city != ''
            ).distinct().order_by(
                DeliveryReport.ship_to_city
            ).all()
            
            cities = [{"city": r[0]} for r in results if r[0]]
            logger.info(f"✅ Loaded {len(cities)} distinct cities")
            return cities
            
        except Exception as e:
            logger.error(f"❌ Failed to load distinct cities: {e}")
            return []
    
    def get_distinct_warehouses(self) -> List[Dict[str, Any]]:
        """
        Get all unique warehouses from delivery reports.
        Maps to: warehouse
        
        Returns:
            List of dicts with warehouse
        """
        try:
            session = self._get_session()
            
            try:
                from app.models.delivery_report import DeliveryReport
            except ImportError:
                logger.error("❌ Cannot import DeliveryReport model")
                return []
            
            results = session.query(
                DeliveryReport.warehouse.label('warehouse')
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).filter(
                DeliveryReport.warehouse != ''
            ).distinct().order_by(
                DeliveryReport.warehouse
            ).all()
            
            warehouses = [{"warehouse": r[0]} for r in results if r[0]]
            logger.info(f"✅ Loaded {len(warehouses)} distinct warehouses")
            return warehouses
            
        except Exception as e:
            logger.error(f"❌ Failed to load distinct warehouses: {e}")
            return []


# ==========================================================
# SCHEMA SERVICE - PRODUCTION
# ==========================================================

class SchemaService:
    """
    Central metadata repository with database loading.
    Single source of truth for dealers, cities, warehouses.
    
    Thread-safe singleton with double-checked locking.
    """
    
    def __init__(self):
        """Initialize SchemaService with database metadata."""
        
        # ==========================================================
        # STATIC METADATA (Business Logic - Not from DB)
        # ==========================================================
        
        self.intents = INTENT_KEYWORDS
        self.metrics = METRIC_KEYWORDS
        self.logistics_keywords = LOGISTICS_KEYWORDS
        self.rules = BUSINESS_RULES
        self.statuses = STATUS_DEFINITIONS
        
        # ==========================================================
        # DYNAMIC METADATA (Loaded from Database)
        # ==========================================================
        
        self.dealers: Dict[str, str] = {}
        self.cities: Dict[str, str] = {}
        self.warehouses: Dict[str, str] = {}
        
        # ==========================================================
        # SEARCH INDEXES (O(1) Lookups)
        # ==========================================================
        
        self._dealer_search_index: Dict[str, str] = {}
        self._city_search_index: Dict[str, str] = {}
        self._warehouse_search_index: Dict[str, str] = {}
        
        # ==========================================================
        # METADATA STATE
        # ==========================================================
        
        self._last_refresh: Optional[datetime] = None
        self._health_score: int = 0
        self._initialized: bool = False
        self._load_error: Optional[str] = None
        self._db_connected: bool = False
        self._lock = Lock()
        
        # Load metadata on startup
        self.refresh_metadata()
    
    # ==========================================================
    # REFRESH METADATA (Public)
    # ==========================================================
    
    def refresh_metadata(self) -> Dict[str, Any]:
        """
        Reload all metadata from database.
        
        Returns:
            Dict with load statistics
        """
        with self._lock:
            logger.info("🔄 Refreshing metadata from database...")
            start_time = datetime.now()
            
            try:
                # Use embedded repository
                repo = DeliveryRepository()
                
                # Load dealers
                dealers_data = repo.get_distinct_customers()
                dealer_names = [d['customer_name'] for d in dealers_data if d.get('customer_name')]
                self.dealers = self._build_dealer_map(dealer_names)
                self._dealer_search_index = self._build_search_index(self.dealers)
                logger.info(f"  ✅ Loaded {len(self.dealers)} dealers")
                
                # Load cities
                cities_data = repo.get_distinct_cities()
                city_names = [c['city'] for c in cities_data if c.get('city')]
                self.cities = self._build_city_map(city_names)
                self._city_search_index = self._build_search_index(self.cities)
                logger.info(f"  ✅ Loaded {len(self.cities)} cities")
                
                # Load warehouses
                warehouses_data = repo.get_distinct_warehouses()
                warehouse_names = [w['warehouse'] for w in warehouses_data if w.get('warehouse')]
                self.warehouses = self._build_warehouse_map(warehouse_names)
                self._warehouse_search_index = self._build_search_index(self.warehouses)
                logger.info(f"  ✅ Loaded {len(self.warehouses)} warehouses")
                
                # Set state
                self._last_refresh = datetime.now()
                self._db_connected = True
                
                # Validate
                self._validate_or_raise()
                
                # Calculate health score
                self._health_score = self._calculate_health_score()
                self._initialized = True
                self._load_error = None
                
                duration = (datetime.now() - start_time).total_seconds()
                
                # Log summary
                logger.info(f"✅ Metadata refresh complete in {duration:.2f}s")
                logger.info(f"   Dealers: {len(self.dealers)}")
                logger.info(f"   Cities: {len(self.cities)}")
                logger.info(f"   Warehouses: {len(self.warehouses)}")
                logger.info(f"   Health Score: {self._health_score}/100")
                
                # Log warnings
                self._log_warnings()
                
                return {
                    "status": "success",
                    "dealers": len(self.dealers),
                    "cities": len(self.cities),
                    "warehouses": len(self.warehouses),
                    "health_score": self._health_score,
                    "initialized": self._initialized,
                    "duration_seconds": round(duration, 2),
                    "last_refresh": self._last_refresh.isoformat()
                }
                
            except Exception as e:
                self._initialized = False
                self._load_error = str(e)
                self._db_connected = False
                logger.error(f"❌ Failed to refresh metadata: {e}")
                
                # IMPORTANT: DO NOT populate fake data
                # Keep empty dictionaries - no mock dealers
                
                return {
                    "status": "failed",
                    "error": str(e),
                    "dealers": len(self.dealers),
                    "cities": len(self.cities),
                    "warehouses": len(self.warehouses),
                    "initialized": False
                }
    
    # ==========================================================
    # VALIDATION
    # ==========================================================
    
    def _validate_or_raise(self):
        """
        Validate metadata and raise if critical data missing.
        
        Raises:
            RuntimeError: If no dealers, cities, or warehouses loaded
        """
        if len(self.dealers) == 0:
            raise RuntimeError(
                "No dealers loaded from database. "
                "Check sold_to_party_name column in delivery_report table."
            )
        
        if len(self.cities) == 0:
            raise RuntimeError(
                "No cities loaded from database. "
                "Check ship_to_city column in delivery_report table."
            )
        
        if len(self.warehouses) == 0:
            raise RuntimeError(
                "No warehouses loaded from database. "
                "Check warehouse column in delivery_report table."
            )
        
        if len(self.dealers) < 10:
            logger.warning(f"⚠️ Only {len(self.dealers)} dealers loaded - expected at least 10")
    
    def _log_warnings(self):
        """Log warnings if metadata counts are suspicious."""
        if len(self.dealers) < 10:
            logger.warning(f"⚠️ Dealer count low: {len(self.dealers)} - check data import")
        
        if len(self.cities) < 5:
            logger.warning(f"⚠️ City count low: {len(self.cities)} - check data import")
        
        if len(self.warehouses) < 3:
            logger.warning(f"⚠️ Warehouse count low: {len(self.warehouses)} - check data import")
    
    def _calculate_health_score(self) -> int:
        """Calculate health score (0-100)."""
        score = 100
        
        # Dealer score
        if len(self.dealers) == 0:
            score -= 40
        elif len(self.dealers) < 10:
            score -= 20
        elif len(self.dealers) < 50:
            score -= 10
        
        # City score
        if len(self.cities) == 0:
            score -= 30
        elif len(self.cities) < 5:
            score -= 15
        
        # Warehouse score
        if len(self.warehouses) == 0:
            score -= 30
        elif len(self.warehouses) < 3:
            score -= 15
        
        return max(0, min(100, score))
    
    # ==========================================================
    # BUILD FUNCTIONS
    # ==========================================================
    
    def _build_search_index(self, data: Dict[str, str]) -> Dict[str, str]:
        """Build search index for O(1) lookups."""
        index = {}
        for alias, full_name in data.items():
            normalized = alias.lower().strip()
            index[normalized] = full_name
            
            # Remove common prefixes for additional matches
            prefixes = ["dealer ", "customer ", "warehouse "]
            for prefix in prefixes:
                if normalized.startswith(prefix):
                    without_prefix = normalized[len(prefix):]
                    if without_prefix:
                        index[without_prefix] = full_name
        
        return index
    
    def _build_dealer_map(self, dealer_names: List[str]) -> Dict[str, str]:
        """
        Build dealer lookup map with multiple aliases.
        
        Args:
            dealer_names: List of full dealer names
            
        Returns:
            Dict mapping aliases to full names
        """
        dealer_map = {}
        
        for name in dealer_names:
            if not name or not name.strip():
                continue
            
            name = name.strip()
            name_lower = name.lower()
            
            # Store full name
            dealer_map[name_lower] = name
            
            # Generate aliases
            words = name.split()
            aliases = set()
            
            # Single words
            for word in words:
                if len(word) >= 2:
                    aliases.add(word.lower())
            
            # Two-word combinations
            for i in range(len(words) - 1):
                two_words = f"{words[i]} {words[i+1]}"
                if len(two_words) >= 3:
                    aliases.add(two_words.lower())
            
            # Three-word combinations
            for i in range(len(words) - 2):
                three_words = f"{words[i]} {words[i+1]} {words[i+2]}"
                if len(three_words) >= 3:
                    aliases.add(three_words.lower())
            
            # Remove common prefixes
            prefixes = ["dealer ", "customer ", "m/s ", "ms ", "m/s. ", "ms. "]
            for prefix in prefixes:
                if name_lower.startswith(prefix):
                    without_prefix = name_lower[len(prefix):]
                    if without_prefix:
                        aliases.add(without_prefix)
                        for word in without_prefix.split():
                            if len(word) >= 2:
                                aliases.add(word)
            
            # Add abbreviations
            if len(words) >= 2:
                abbr = ''.join(word[0].upper() for word in words if word)
                if len(abbr) >= 2:
                    aliases.add(abbr.lower())
            
            # Add all aliases
            for alias in aliases:
                if alias and len(alias) >= 2:
                    dealer_map[alias] = name
        
        return dealer_map
    
    def _build_city_map(self, city_names: List[str]) -> Dict[str, str]:
        """Build city lookup map with aliases."""
        city_map = {}
        
        for name in city_names:
            if not name or not name.strip():
                continue
            
            name = name.strip()
            name_lower = name.lower()
            
            # Store full name
            city_map[name_lower] = name
            
            # Store first 3 letters as abbreviation
            if len(name) >= 3:
                city_map[name[:3].lower()] = name
            
            # Common city abbreviations
            common_abbr = {
                "lahore": "lhr",
                "karachi": "khi",
                "islamabad": "isb",
                "rawalpindi": "rwp",
                "multan": "mux",
                "faisalabad": "fsd",
                "peshawar": "pwr",
                "quetta": "qta",
                "hyderabad": "hyd",
                "gujranwala": "guj",
                "sialkot": "skt",
                "bahawalpur": "bwp"
            }
            
            for full, abbr in common_abbr.items():
                if full in name_lower:
                    city_map[abbr] = name
                    break
        
        return city_map
    
    def _build_warehouse_map(self, warehouse_names: List[str]) -> Dict[str, str]:
        """Build warehouse lookup map with aliases."""
        warehouse_map = {}
        
        for name in warehouse_names:
            if not name or not name.strip():
                continue
            
            name = name.strip()
            name_lower = name.lower()
            
            # Store full name
            warehouse_map[name_lower] = name
            
            # Remove "warehouse" suffix
            if name_lower.endswith(" warehouse"):
                without_suffix = name_lower[:-10].strip()
                if without_suffix:
                    warehouse_map[without_suffix] = name
                    # First word of without_suffix
                    first_word = without_suffix.split()[0]
                    if first_word:
                        warehouse_map[first_word] = name
            
            # First word
            words = name.split()
            if words:
                warehouse_map[words[0].lower()] = name
            
            # First two words
            if len(words) >= 2:
                two_words = f"{words[0]} {words[1]}"
                warehouse_map[two_words.lower()] = name
            
            # Common abbreviations
            common_abbr = {
                "lahore": "lhr",
                "karachi": "khi",
                "islamabad": "isb",
                "rawalpindi": "rwp",
                "multan": "mux",
                "faisalabad": "fsd",
                "peshawar": "pwr",
                "quetta": "qta",
            }
            
            for full, abbr in common_abbr.items():
                if full in name_lower:
                    warehouse_map[abbr] = name
                    break
        
        return warehouse_map
    
    # ==========================================================
    # INTENT DETECTION
    # ==========================================================
    
    def detect_intent(self, text: str) -> Tuple[Optional[str], float]:
        """
        Detect intent from text using priority-based scoring.
        
        Args:
            text: Input text to analyze
            
        Returns:
            Tuple of (intent_name, confidence_score)
        """
        if not text:
            return None, 0.0
        
        text = text.lower().strip()
        scores = {}
        
        for intent, keywords in self.intents.items():
            total_score = 0
            matched_keywords = 0
            
            for keyword, priority in keywords:
                if keyword in text:
                    total_score += priority
                    matched_keywords += 1
            
            if matched_keywords > 0:
                confidence = min(0.95, 0.5 + (total_score / 20))
                scores[intent] = confidence
        
        if scores:
            best_intent = max(scores, key=scores.get)
            confidence = scores[best_intent]
            logger.debug(f"Intent detected: {best_intent} (confidence: {confidence:.2f})")
            return best_intent, confidence
        
        return None, 0.0
    
    # ==========================================================
    # METRIC DETECTION
    # ==========================================================
    
    def detect_metric(self, text: str) -> Optional[str]:
        """
        Detect metric from text.
        
        Args:
            text: Input text to analyze
            
        Returns:
            Metric name or None
        """
        if not text:
            return None
        
        text = text.lower().strip()
        
        for metric, keywords in self.metrics.items():
            for keyword in keywords:
                if keyword in text:
                    logger.debug(f"Metric detected: {metric} (matched: '{keyword}')")
                    return metric
        
        return None
    
    # ==========================================================
    # DEALER RESOLUTION (Priority Order)
    # ==========================================================
    
    def resolve_dealer(self, text: str) -> Optional[str]:
        """
        Resolve dealer from text using priority-based matching.
        
        Resolution order:
        1. Exact Match
        2. Indexed Match (O(1))
        3. Word Boundary Match
        4. Partial Fuzzy Match
        5. Token Similarity
        
        Args:
            text: Dealer name or alias
            
        Returns:
            Full dealer name or None
        """
        if not text:
            return None
        
        text = text.lower().strip()
        
        # Remove common prefixes
        prefixes = ["dealer ", "customer ", "show ", "display ", "get "]
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        
        if not text:
            return None
        
        # STEP 1: Exact Match
        for alias, dealer in self.dealers.items():
            if alias == text:
                logger.debug(f"Dealer resolved (exact): {dealer}")
                return dealer
        
        # STEP 2: Indexed Match (O(1))
        if text in self._dealer_search_index:
            result = self._dealer_search_index[text]
            logger.debug(f"Dealer resolved (index): {result}")
            return result
        
        # STEP 3: Word Boundary Match
        words = text.split()
        for word in words:
            if len(word) >= 2:
                pattern = re.compile(rf'\b{re.escape(word)}\b')
                for alias, dealer in self.dealers.items():
                    if pattern.search(alias):
                        logger.debug(f"Dealer resolved (word boundary): {dealer} from '{text}'")
                        return dealer
        
        # STEP 4: Partial Fuzzy Match
        for alias, dealer in self.dealers.items():
            if alias in text or text in alias:
                logger.debug(f"Dealer resolved (fuzzy): {dealer} from '{text}'")
                return dealer
        
        # STEP 5: Token Similarity (single word matches)
        if len(text.split()) == 1:
            word = text
            for alias, dealer in self.dealers.items():
                if word in alias.split() or any(word in w for w in alias.split()):
                    logger.debug(f"Dealer resolved (token): {dealer} from '{text}'")
                    return dealer
        
        return None
    
    # ==========================================================
    # CITY RESOLUTION
    # ==========================================================
    
    def resolve_city(self, text: str) -> Optional[str]:
        """
        Resolve city from text using priority-based matching.
        
        Args:
            text: City name or alias
            
        Returns:
            Full city name or None
        """
        if not text:
            return None
        
        text = text.lower().strip()
        
        # STEP 1: Exact Match
        for alias, city in self.cities.items():
            if alias == text:
                return city
        
        # STEP 2: Indexed Match
        if text in self._city_search_index:
            return self._city_search_index[text]
        
        # STEP 3: Word Boundary Match
        words = text.split()
        for word in words:
            if len(word) >= 2:
                pattern = re.compile(rf'\b{re.escape(word)}\b')
                for alias, city in self.cities.items():
                    if pattern.search(alias):
                        logger.debug(f"City resolved (word boundary): {city} from '{text}'")
                        return city
        
        # STEP 4: Partial Fuzzy Match
        for alias, city in self.cities.items():
            if alias in text or text in alias:
                logger.debug(f"City resolved (fuzzy): {city} from '{text}'")
                return city
        
        return None
    
    # ==========================================================
    # WAREHOUSE RESOLUTION
    # ==========================================================
    
    def resolve_warehouse(self, text: str) -> Optional[str]:
        """
        Resolve warehouse from text using priority-based matching.
        
        Args:
            text: Warehouse name or alias
            
        Returns:
            Full warehouse name or None
        """
        if not text:
            return None
        
        text = text.lower().strip()
        
        # STEP 1: Exact Match
        for alias, warehouse in self.warehouses.items():
            if alias == text:
                return warehouse
        
        # STEP 2: Indexed Match
        if text in self._warehouse_search_index:
            return self._warehouse_search_index[text]
        
        # STEP 3: Word Boundary Match
        words = text.split()
        for word in words:
            if len(word) >= 2:
                pattern = re.compile(rf'\b{re.escape(word)}\b')
                for alias, warehouse in self.warehouses.items():
                    if pattern.search(alias):
                        logger.debug(f"Warehouse resolved (word boundary): {warehouse} from '{text}'")
                        return warehouse
        
        # STEP 4: Partial Fuzzy Match
        for alias, warehouse in self.warehouses.items():
            if alias in text or text in alias:
                logger.debug(f"Warehouse resolved (fuzzy): {warehouse} from '{text}'")
                return warehouse
        
        return None
    
    # ==========================================================
    # LOGISTICS KEYWORD CHECK
    # ==========================================================
    
    def is_logistics_keyword(self, text: str) -> bool:
        """
        Check if text contains logistics keywords.
        
        Args:
            text: Input text to check
            
        Returns:
            True if text contains logistics keywords
        """
        if not text:
            return False
        
        text = text.lower().strip()
        
        for keyword in self.logistics_keywords:
            if keyword in text:
                return True
        
        return False
    
    # ==========================================================
    # DN HELPERS
    # ==========================================================
    
    def is_dn_number(self, text: str) -> bool:
        """
        Check if text is a valid DN number (8-12 digits).
        
        Args:
            text: Input text to check
            
        Returns:
            True if text matches DN pattern
        """
        if not text:
            return False
        return bool(DN_PATTERN.match(text.strip()))
    
    def extract_dn_number(self, text: str) -> Optional[str]:
        """
        Extract DN number from text.
        
        Args:
            text: Input text
            
        Returns:
            DN number or None
        """
        if not text:
            return None
        match = DN_PATTERN.search(text)
        return match.group(1) if match else None
    
    # ==========================================================
    # RISK STATUS HELPERS
    # ==========================================================
    
    def get_risk_status(self, score: float) -> str:
        """
        Get risk status based on score.
        
        Args:
            score: Numeric score (0-100)
            
        Returns:
            Risk status string
        """
        if score < 50:
            return "critical"
        elif score < 70:
            return "high"
        elif score < 85:
            return "medium"
        return "low"
    
    def get_risk_emoji(self, status: str) -> str:
        """
        Get emoji for risk status.
        
        Args:
            status: Risk status
            
        Returns:
            Emoji string
        """
        emojis = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        return emojis.get(status, "⚪")
    
    def get_dn_status(self, status_key: str) -> str:
        """
        Get delivery note status description.
        
        Args:
            status_key: Status key
            
        Returns:
            Status description
        """
        return self.statuses.get("dn_status", {}).get(status_key, "❓ Unknown")
    
    def get_rule(self, rule_name: str) -> Optional[Any]:
        """
        Get business rule by name.
        
        Args:
            rule_name: Rule name
            
        Returns:
            Business rule configuration
        """
        return self.rules.get(rule_name)
    
    # ==========================================================
    # VALIDATE METADATA
    # ==========================================================
    
    def validate_metadata(self) -> Dict[str, Any]:
        """
        Validate metadata integrity.
        
        Returns:
            Validation report with counts and warnings
        """
        warnings = []
        
        if len(self.dealers) == 0:
            warnings.append("No dealers loaded from database")
        
        if len(self.cities) == 0:
            warnings.append("No cities loaded from database")
        
        if len(self.warehouses) == 0:
            warnings.append("No warehouses loaded from database")
        
        if len(self.dealers) < 10:
            warnings.append(f"Low dealer count: {len(self.dealers)}")
        
        return {
            "counts": {
                "dealers": len(self.dealers),
                "cities": len(self.cities),
                "warehouses": len(self.warehouses),
                "intents": len(self.intents),
                "metrics": len(self.metrics),
            },
            "warnings": warnings,
            "initialized": self._initialized,
            "health_score": self._health_score,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None
        }
    
    # ==========================================================
    # HEALTH REPORT
    # ==========================================================
    
    def get_health_report(self) -> Dict[str, Any]:
        """
        Get comprehensive health report.
        
        Returns:
            Health report with counts, score, and status
        """
        return {
            "dealers": len(self.dealers),
            "cities": len(self.cities),
            "warehouses": len(self.warehouses),
            "health_score": self._health_score,
            "initialized": self._initialized,
            "database_connected": self._db_connected,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "load_error": self._load_error,
            "status": "healthy" if self._health_score >= 70 else "warning" if self._health_score >= 50 else "critical"
        }
    
    # ==========================================================
    # DIAGNOSTIC REPORT
    # ==========================================================
    
    def get_diagnostic_report(self) -> Dict[str, Any]:
        """
        Get diagnostic report for /debug/schema endpoint.
        
        Returns:
            Diagnostic report with all metadata
        """
        return {
            "dealers": len(self.dealers),
            "cities": len(self.cities),
            "warehouses": len(self.warehouses),
            "health_score": self._health_score,
            "initialized": self._initialized,
            "search_index_sizes": {
                "dealers": len(self._dealer_search_index),
                "cities": len(self._city_search_index),
                "warehouses": len(self._warehouse_search_index)
            },
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "timestamp": datetime.now().isoformat()
        }


# ==========================================================
# SINGLETON
# ==========================================================

_schema_service = None
_schema_lock = Lock()


def get_schema_service() -> SchemaService:
    """
    Thread-safe singleton getter for SchemaService.
    
    Returns:
        SchemaService: Singleton instance with database-loaded metadata
    """
    global _schema_service
    
    if _schema_service is None:
        with _schema_lock:
            if _schema_service is None:
                try:
                    _schema_service = SchemaService()
                    logger.info("SchemaService singleton initialized")
                except Exception as e:
                    logger.error(f"❌ SchemaService initialization failed: {e}")
                    raise
    
    return _schema_service


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def refresh_schema_metadata() -> Dict[str, Any]:
    """
    Force refresh of schema metadata from database.
    Call this after Excel import or on demand.
    
    Returns:
        Dict: Refresh results
    """
    service = get_schema_service()
    return service.refresh_metadata()


def get_schema_health() -> Dict[str, Any]:
    """
    Get schema health report.
    Use for /debug/schema/health endpoint.
    
    Returns:
        Dict: Health report
    """
    service = get_schema_service()
    return service.get_health_report()


def get_schema_diagnostics() -> Dict[str, Any]:
    """
    Get schema diagnostics.
    Use for /debug/schema endpoint.
    
    Returns:
        Dict: Diagnostic report
    """
    service = get_schema_service()
    return service.get_diagnostic_report()


def is_dn_number(text: str) -> bool:
    """
    Check if text is a valid DN number (8-12 digits).
    
    Args:
        text: Input text
        
    Returns:
        True if text matches DN pattern
    """
    service = get_schema_service()
    return service.is_dn_number(text)


def extract_dn_number(text: str) -> Optional[str]:
    """
    Extract DN number from text.
    
    Args:
        text: Input text
        
    Returns:
        DN number or None
    """
    service = get_schema_service()
    return service.extract_dn_number(text)


# ==========================================================
# FASTAPI DIAGNOSTIC ENDPOINTS (To be added to main.py)
# ==========================================================

"""
Add these endpoints to app/main.py:

@app.get("/debug/schema")
async def schema_diagnostics():
    \"\"\"Get schema diagnostics.\"\"\"
    return get_schema_diagnostics()

@app.get("/debug/schema/health")
async def schema_health():
    \"\"\"Get schema health report.\"\"\"
    return get_schema_health()

@app.post("/debug/schema/refresh")
async def schema_refresh():
    \"\"\"Force refresh schema metadata.\"\"\"
    result = refresh_schema_metadata()
    return result
"""


# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

logger.info("SchemaService v5.0 loaded (Production Ready)")


# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'SchemaService',
    'get_schema_service',
    'refresh_schema_metadata',
    'get_schema_health',
    'get_schema_diagnostics',
    'is_dn_number',
    'extract_dn_number',
    'DN_PATTERN',
    'INTENT_KEYWORDS',
    'METRIC_KEYWORDS',
    'LOGISTICS_KEYWORDS',
    'BUSINESS_RULES',
    'STATUS_DEFINITIONS',
]
