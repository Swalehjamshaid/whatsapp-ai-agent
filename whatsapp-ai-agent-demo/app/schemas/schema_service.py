# ==========================================================
# FILE: app/schemas/schema_service.py (v7.0 - PRODUCTION FIX)
# ==========================================================
# FIXES APPLIED:
# 1. Enhanced dealer alias generation with SequenceMatcher
# 2. Added resolve_entity() for unified entity resolution
# 3. Added debug methods (find_dealer_debug, get_sample_dealers)
# 4. Improved logging with structured output
# 5. Added confidence scoring for entity resolution
# ==========================================================

from typing import Dict, List, Optional, Tuple, Set, Any
from threading import Lock
import logging
import re
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import json

# ==========================================================
# LOGGING SETUP
# ==========================================================

logger = logging.getLogger(__name__)

# ==========================================================
# DN PATTERN (8-12 digits)
# ==========================================================

DN_PATTERN = re.compile(r'\b(\d{8,12})\b')

# ==========================================================
# DELIVERY CALCULATION RULES
# ==========================================================

@dataclass
class DeliveryMetrics:
    """Delivery time calculation rules with validation."""
    
    processing_time_rule: str = "Good Issue Date - DN Create Date"
    delivery_time_rule: str = "POD Date - Good Issue Date"
    total_cycle_time_rule: str = "POD Date - DN Create Date"
    
    max_processing_days: int = 30
    max_delivery_days: int = 30
    max_cycle_days: int = 60
    
    def validate_dates(self, dn_date: Optional[datetime], pgi_date: Optional[datetime], pod_date: Optional[datetime]) -> Dict[str, Any]:
        """
        Validate date consistency and identify data quality issues.
        FIX: Correct sequence validation and no negative durations.
        """
        issues = []
        is_valid = True
        warnings = []
        
        # Check for missing dates
        missing_dates = []
        if dn_date is None:
            missing_dates.append("DN Create Date")
            is_valid = False
        if pgi_date is None:
            missing_dates.append("Good Issue Date")
            is_valid = False
        if pod_date is None:
            missing_dates.append("POD Date")
            is_valid = False
        
        if missing_dates:
            issues.append(f"Missing dates: {', '.join(missing_dates)}")
        
        # Only validate if all dates exist
        if dn_date and pgi_date and pod_date:
            # CRITICAL FIX: Validate sequence correctly
            if pgi_date < dn_date:
                issues.append(
                    f"⚠️ Data Integrity Issue: PGI Date ({pgi_date.strftime('%Y-%m-%d')}) "
                    f"occurs before DN Date ({dn_date.strftime('%Y-%m-%d')})"
                )
                is_valid = False
            
            if pod_date < pgi_date:
                issues.append(
                    f"⚠️ Data Integrity Issue: POD Date ({pod_date.strftime('%Y-%m-%d')}) "
                    f"occurs before PGI Date ({pgi_date.strftime('%Y-%m-%d')})"
                )
                is_valid = False
            
            if pod_date < dn_date:
                issues.append(
                    f"⚠️ Data Integrity Issue: POD Date ({pod_date.strftime('%Y-%m-%d')}) "
                    f"occurs before DN Date ({dn_date.strftime('%Y-%m-%d')})"
                )
                is_valid = False
        
        # Calculate durations ONLY if sequence is valid
        durations = {}
        if is_valid and dn_date and pgi_date and pod_date:
            # FIX: Correct calculations with absolute values to prevent negatives
            processing_days = (pgi_date - dn_date).days
            delivery_days = (pod_date - pgi_date).days
            cycle_days = (pod_date - dn_date).days
            
            # Ensure no negative durations (data integrity check)
            if processing_days < 0:
                issues.append(f"⚠️ Negative processing time: {processing_days} days")
                processing_days = 0
                is_valid = False
            
            if delivery_days < 0:
                issues.append(f"⚠️ Negative delivery time: {delivery_days} days")
                delivery_days = 0
                is_valid = False
            
            if cycle_days < 0:
                issues.append(f"⚠️ Negative cycle time: {cycle_days} days")
                cycle_days = 0
                is_valid = False
            
            durations = {
                'processing_time_days': processing_days,
                'delivery_time_days': delivery_days,
                'total_cycle_days': cycle_days
            }
            
            # Check thresholds
            if processing_days > self.max_processing_days:
                warnings.append(f"Processing time ({processing_days} days) exceeds threshold ({self.max_processing_days} days)")
            
            if delivery_days > self.max_delivery_days:
                warnings.append(f"Delivery time ({delivery_days} days) exceeds threshold ({self.max_delivery_days} days)")
            
            if cycle_days > self.max_cycle_days:
                warnings.append(f"Total cycle time ({cycle_days} days) exceeds threshold ({self.max_cycle_days} days)")
        else:
            durations = {
                'processing_time_days': None,
                'delivery_time_days': None,
                'total_cycle_days': None
            }
        
        # Data quality flags
        data_quality_flags = {
            'missing_dn_date': dn_date is None,
            'missing_pgi_date': pgi_date is None,
            'missing_pod_date': pod_date is None,
            'invalid_date_sequence': not is_valid if (dn_date and pgi_date and pod_date) else False
        }
        
        return {
            'is_valid': is_valid,
            'issues': issues,
            'warnings': warnings,
            'durations': durations,
            'data_quality_flags': data_quality_flags,
            'dn_date': dn_date.isoformat() if dn_date else None,
            'pgi_date': pgi_date.isoformat() if pgi_date else None,
            'pod_date': pod_date.isoformat() if pod_date else None
        }

# ==========================================================
# INTENT KEYWORDS (Restructured for priority routing)
# ==========================================================

INTENT_KEYWORDS: Dict[str, List[Tuple[str, int]]] = {
    # ==========================================================
    # DN INTELLIGENCE (HIGHEST PRIORITY)
    # ==========================================================
    
    "dn_lookup": [
        ("dn", 10), ("delivery note", 10), ("track dn", 10),
        ("track delivery", 10), ("check dn", 9), ("dn status", 9)
    ],
    
    # ==========================================================
    # DEALER INTELLIGENCE
    # ==========================================================
    
    "dealer_dashboard": [
        ("dealer", 9), ("show dealer", 10), ("dealer dashboard", 9),
        ("dealer summary", 9), ("dealer details", 9)
    ],
    
    "dealer_revenue": [
        ("revenue", 8), ("sales", 8), ("total revenue", 9),
        ("total sales", 9), ("dealer revenue", 10), ("dealer sales", 10)
    ],
    
    "dealer_units": [
        ("units", 8), ("quantity", 8), ("total units", 9),
        ("dealer units", 10), ("dealer quantity", 10)
    ],
    
    "dealer_performance": [
        ("performance", 8), ("kpi", 8), ("dealer performance", 10),
        ("dealer kpi", 10), ("performance metrics", 9)
    ],
    
    "dealer_aging": [
        ("aging", 8), ("delay", 8), ("pending", 7),
        ("dealer aging", 10), ("dealer delay", 10), ("oldest", 8)
    ],
    
    # ==========================================================
    # WAREHOUSE INTELLIGENCE
    # ==========================================================
    
    "warehouse_dashboard": [
        ("warehouse", 9), ("show warehouse", 10), ("warehouse summary", 9),
        ("warehouse details", 9), ("warehouse status", 9)
    ],
    
    "warehouse_performance": [
        ("warehouse performance", 10), ("warehouse kpi", 10),
        ("warehouse metrics", 9), ("warehouse efficiency", 9)
    ],
    
    # ==========================================================
    # CITY INTELLIGENCE
    # ==========================================================
    
    "city_dashboard": [
        ("city", 9), ("show city", 10), ("city summary", 9),
        ("city details", 9), ("city status", 9)
    ],
    
    "city_performance": [
        ("city performance", 10), ("city kpi", 10),
        ("city metrics", 9), ("city efficiency", 9)
    ],
    
    # ==========================================================
    # PENDING & AGING INTELLIGENCE
    # ==========================================================
    
    "pending_pgi": [
        ("pending pgi", 10), ("pgi pending", 10), ("open pgi", 8),
        ("pgi not done", 9), ("pending good issue", 9)
    ],
    
    "pending_pod": [
        ("pending pod", 10), ("pod pending", 10), ("open pod", 8),
        ("pod not done", 9), ("pending delivery", 9)
    ],
    
    "pgi_aging": [
        ("pgi aging", 10), ("aging pgi", 10), ("pgi delay", 8),
        ("good issue aging", 9), ("pgi overdue", 9)
    ],
    
    "pod_aging": [
        ("pod aging", 10), ("aging pod", 10), ("pod delay", 8),
        ("delivery aging", 9), ("pod overdue", 9)
    ],
    
    # ==========================================================
    # RANKING INTELLIGENCE
    # ==========================================================
    
    "top_dealers": [
        ("top dealer", 10), ("best dealer", 9), ("top performing", 8),
        ("top 10 dealers", 10), ("highest dealer", 9), ("top performers", 9)
    ],
    
    "bottom_dealers": [
        ("bottom dealer", 10), ("worst dealer", 9), ("poor performing", 8),
        ("bottom 10 dealers", 10), ("lowest dealer", 9), ("worst performers", 9)
    ],
    
    "top_warehouses": [
        ("top warehouse", 10), ("best warehouse", 9), ("top performing warehouse", 10)
    ],
    
    "top_cities": [
        ("top city", 10), ("best city", 9), ("top performing city", 10)
    ],
    
    # ==========================================================
    # ROOT CAUSE INTELLIGENCE (HIGH PRIORITY FOR ANALYTICS)
    # ==========================================================
    
    "root_cause": [
        ("root cause", 10), ("what is the key issue", 10), 
        ("why delayed", 10), ("why aging", 10), ("key issue", 9),
        ("how to bring improvement", 10), ("how to improve", 9),
        ("reason", 8), ("cause", 9), ("improvement areas", 10),
        ("bring improvement", 10), ("critical issue", 9)
    ],
    
    # ==========================================================
    # EXECUTIVE INTELLIGENCE
    # ==========================================================
    
    "executive_insight": [
        ("executive insight", 10), ("executive summary", 10),
        ("bottleneck", 9), ("critical issues", 9),
        ("top issues", 9), ("urgent matters", 9)
    ],
    
    "control_tower": [
        ("control tower", 10), ("critical", 8), ("urgent", 8),
        ("priority", 8), ("alert", 7), ("command center", 9)
    ],
    
    # ==========================================================
    # DELIVERY PERFORMANCE INTELLIGENCE
    # ==========================================================
    
    "delivery_performance": [
        ("delivery performance", 10), ("delivery kpi", 10),
        ("delivery metrics", 9), ("delivery efficiency", 9),
        ("on time delivery", 9), ("delivery rate", 9)
    ],
    
    # ==========================================================
    # TREND & COMPARISON INTELLIGENCE
    # ==========================================================
    
    "trend": [
        ("trend", 8), ("month over month", 9), ("trends", 9),
        ("over time", 8), ("historical", 8), ("performance trend", 10)
    ],
    
    "comparison": [
        ("compare", 8), ("vs", 7), ("versus", 7),
        ("comparison", 8), ("between", 7), ("compare dealers", 10),
        ("compare warehouses", 10), ("compare cities", 10)
    ],
    
    # ==========================================================
    # HELP & GENERAL (LOWEST PRIORITY)
    # ==========================================================
    
    "help": [
        ("help", 10), ("menu", 8), ("commands", 8),
        ("what can you do", 10), ("available commands", 10)
    ],
    
    "general_ai": [
        ("hello", 5), ("hi", 5), ("hey", 5), ("how are you", 6),
        ("good morning", 5), ("good evening", 5)
    ],
}

# ==========================================================
# METRIC KEYWORDS (Enhanced for better detection)
# ==========================================================

METRIC_KEYWORDS: Dict[str, List[str]] = {
    # Revenue metrics
    "revenue": ["revenue", "sales", "amount", "total revenue", "total sales", "sales amount"],
    
    # Unit metrics
    "units": ["units", "quantity", "qty", "total units", "unit count", "number of units"],
    
    # DN metrics
    "dn_count": ["dns", "delivery notes", "orders", "total dns", "order count"],
    
    # Pending metrics
    "pending_pod": ["pending pod", "pod pending", "pod not done", "open pod"],
    "pending_delivery": ["pending delivery", "delivery pending", "pending pgi", "undelivered"],
    
    # Aging metrics
    "delivery_aging": ["delivery aging", "pgi aging", "delivery delay", "pgi delay"],
    "pod_aging": ["pod aging", "pod delay", "pod latency", "aging pod"],
    
    # Rate metrics
    "pod_rate": ["pod rate", "pod percentage", "pod completion", "pod ratio"],
    "pgi_rate": ["pgi rate", "pgi percentage", "pgi completion", "pgi ratio"],
    "delivery_rate": ["delivery rate", "delivery percentage", "delivery completion", "on-time delivery"],
    
    # Performance metrics
    "success_rate": ["success rate", "success percentage", "completion rate"],
    "failure_rate": ["failure rate", "failure percentage", "error rate"],
    
    # Time metrics
    "avg_delivery_time": ["avg delivery time", "average delivery", "mean delivery", "average delivery time"],
    "processing_time": ["processing time", "pgi time", "good issue time"],
    "total_cycle_time": ["cycle time", "total cycle", "total time"],
    
    # Summary metrics
    "total_deliveries": ["total deliveries", "total dispatched", "total sent"],
    "total_revenue": ["total revenue", "total sales", "overall revenue"],
    "total_units": ["total units", "total quantity", "overall units"],
}

# ==========================================================
# LOGISTICS KEYWORDS (Reject List)
# ==========================================================

LOGISTICS_KEYWORDS: Set[str] = {
    # Status words
    'pending', 'delivered', 'in_transit', 'dispatched', 'shipped', 'received',
    
    # Process words
    'pgi', 'pod', 'aging', 'delivery', 'revenue', 'units', 'performance',
    
    # Alert words
    'critical', 'alert', 'urgent', 'priority', 'control', 'tower',
    
    # Query words
    'help', 'menu', 'status', 'what', 'how', 'why', 'when', 'where',
    'who', 'which', 'can', 'could', 'would', 'should', 'is', 'are',
    
    # Action words
    'show', 'display', 'get', 'tell', 'view', 'list', 'fetch', 'find',
    
    # Business words
    'warehouse', 'summary', 'report', 'kpi', 'dashboard', 'insight',
    'issue', 'problem', 'bottleneck', 'root', 'cause', 'reason',
    'dealer', 'customer', 'city', 'stock', 'inventory', 'sales',
    
    # Transit words
    'transit', 'delivered', 'rate', 'completion', 'dn', 'order',
    
    # Comparison words
    'compare', 'versus', 'vs', 'between', 'against',
    
    # Time words
    'today', 'yesterday', 'week', 'month', 'year', 'last', 'this',
    'current', 'day', 'week', 'month', 'year', 'all',
    
    # Quantifiers
    'top', 'bottom', 'best', 'worst', 'highest', 'lowest', 'average',
    'total', 'all', 'some', 'most', 'least', 'more', 'less', 'much',
    
    # Numbers
    'first', 'second', 'third', 'fourth', 'fifth', 'tenth',
    'one', 'two', 'three', 'four', 'five', 'ten',
}

# ==========================================================
# BUSINESS RULES
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
    "processing_time": {
        "rule": "Processing Time = Good Issue Date - DN Create Date",
        "thresholds": {"critical": 7, "high": 5, "medium": 3, "low": 1}
    },
    "delivery_time": {
        "rule": "Delivery Time = POD Date - Good Issue Date",
        "thresholds": {"critical": 7, "high": 5, "medium": 3, "low": 1}
    },
    "total_cycle_time": {
        "rule": "Total Cycle Time = POD Date - DN Create Date",
        "thresholds": {"critical": 14, "high": 10, "medium": 7, "low": 3}
    },
    "sla": {
        "delivery_aging_target": 7,
        "pod_aging_target": 7,
        "delivery_rate_target": 90,
        "pod_rate_target": 90,
        "processing_time_target": 3,
        "delivery_time_target": 3,
        "total_cycle_time_target": 7
    }
}

# ==========================================================
# STATUS DEFINITIONS
# ==========================================================

STATUS_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "dn_status": {
        "delivered": "✅ Delivered - POD Received",
        "in_transit": "🚚 In Transit - PGI Done, POD Pending",
        "pending_pgi": "⏳ Pending PGI - Not Yet Dispatched",
        "pending_pod": "📦 Pending POD - Dispatched, Awaiting Confirmation",
        "unknown": "❓ Status Unknown",
    },
    "risk_status": {
        "critical": "🔴 CRITICAL - Immediate Attention Required",
        "high": "🟠 HIGH - Action Required",
        "medium": "🟡 MEDIUM - Monitor Closely",
        "low": "🟢 LOW - Normal Operations",
    },
    "data_quality": {
        "valid": "✅ All dates valid and in correct order",
        "warning": "⚠️ Data quality issues detected - check details",
        "error": "❌ Critical data quality issues - requires investigation"
    }
}

# ==========================================================
# EMBEDDED REPOSITORY
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
        """Get all unique customer/dealer names from delivery reports."""
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
        """Get all unique ship-to cities from delivery reports."""
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
        """Get all unique warehouses from delivery reports."""
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
# SCHEMA SERVICE - METADATA INTELLIGENCE ENGINE (FIXED)
# ==========================================================

class SchemaService:
    """
    Central Metadata Intelligence Engine for Logistics Analytics.
    FIXED: Enhanced dealer resolution with SequenceMatcher and debug capabilities.
    """
    
    def __init__(self):
        """Initialize SchemaService with database metadata."""
        
        # ==========================================================
        # STATIC METADATA
        # ==========================================================
        
        self.intents = INTENT_KEYWORDS
        self.metrics = METRIC_KEYWORDS
        self.logistics_keywords = LOGISTICS_KEYWORDS
        self.rules = BUSINESS_RULES
        self.statuses = STATUS_DEFINITIONS
        self.delivery_metrics = DeliveryMetrics()
        
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
        self._stats = {
            "total_dealers_loaded": 0,
            "total_cities_loaded": 0,
            "total_warehouses_loaded": 0,
            "refresh_count": 0,
            "last_refresh_duration_ms": 0
        }
        
        # ==========================================================
        # SEQUENCE MATCHER CACHE
        # ==========================================================
        
        self._dealer_list: List[str] = []
        self._city_list: List[str] = []
        self._warehouse_list: List[str] = []
        
        # Load metadata on startup
        self.refresh_metadata()
    
    # ==========================================================
    # REFRESH METADATA
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
                repo = DeliveryRepository()
                
                # Load dealers
                dealers_data = repo.get_distinct_customers()
                dealer_names = [d['customer_name'] for d in dealers_data if d.get('customer_name')]
                self.dealers = self._build_dealer_map(dealer_names)
                self._dealer_search_index = self._build_search_index(self.dealers)
                self._dealer_list = list(self.dealers.values())  # For SequenceMatcher
                logger.info(f"  ✅ Loaded {len(self.dealers)} dealers")
                
                # Load cities
                cities_data = repo.get_distinct_cities()
                city_names = [c['city'] for c in cities_data if c.get('city')]
                self.cities = self._build_city_map(city_names)
                self._city_search_index = self._build_search_index(self.cities)
                self._city_list = list(self.cities.values())  # For SequenceMatcher
                logger.info(f"  ✅ Loaded {len(self.cities)} cities")
                
                # Load warehouses
                warehouses_data = repo.get_distinct_warehouses()
                warehouse_names = [w['warehouse'] for w in warehouses_data if w.get('warehouse')]
                self.warehouses = self._build_warehouse_map(warehouse_names)
                self._warehouse_search_index = self._build_search_index(self.warehouses)
                self._warehouse_list = list(self.warehouses.values())  # For SequenceMatcher
                logger.info(f"  ✅ Loaded {len(self.warehouses)} warehouses")
                
                # Set state
                self._last_refresh = datetime.now()
                self._db_connected = True
                self._stats["refresh_count"] += 1
                self._stats["total_dealers_loaded"] = len(self.dealers)
                self._stats["total_cities_loaded"] = len(self.cities)
                self._stats["total_warehouses_loaded"] = len(self.warehouses)
                
                # Validate
                self._validate_or_raise()
                
                # Calculate health score
                self._health_score = self._calculate_health_score()
                self._initialized = True
                self._load_error = None
                
                duration = (datetime.now() - start_time).total_seconds()
                self._stats["last_refresh_duration_ms"] = round(duration * 1000, 2)
                
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
                
                return {
                    "status": "failed",
                    "error": str(e),
                    "dealers": len(self.dealers),
                    "cities": len(self.cities),
                    "warehouses": len(self.warehouses),
                    "initialized": False
                }
    
    # ==========================================================
    # BUILD FUNCTIONS (ENHANCED)
    # ==========================================================
    
    def _build_search_index(self, data: Dict[str, str]) -> Dict[str, str]:
        """Build search index for O(1) lookups."""
        index = {}
        for alias, full_name in data.items():
            normalized = alias.lower().strip()
            index[normalized] = full_name
            
            # Remove common prefixes
            prefixes = ["dealer ", "customer ", "warehouse "]
            for prefix in prefixes:
                if normalized.startswith(prefix):
                    without_prefix = normalized[len(prefix):]
                    if without_prefix:
                        index[without_prefix] = full_name
        
        return index
    
    def _build_dealer_map(self, dealer_names: List[str]) -> Dict[str, str]:
        """
        Build dealer lookup map with intelligent aliases.
        FIXED: Enhanced alias generation for better recognition.
        
        Examples:
            "Mian Group of Chakwal Wah" → mian, group, chakwal, wah, mian group, 
                                          mian group of chakwal wah, etc.
            "Dubai Electronics" → dubai, electronics, dubai electronics
        """
        dealer_map = {}
        
        for name in dealer_names:
            if not name or not name.strip():
                continue
            
            name = name.strip()
            name_lower = name.lower()
            
            # Store full name
            dealer_map[name_lower] = name
            
            # Generate intelligent aliases
            words = name.split()
            aliases = set()
            
            # FIXED: Include all meaningful word combinations
            # Single words (every word is a potential alias)
            for word in words:
                if len(word) >= 2:
                    aliases.add(word.lower())
                    # Handle abbreviations
                    if len(word) >= 3:
                        aliases.add(word[:3].lower())
                    if len(word) >= 2:
                        aliases.add(word[:2].lower())
            
            # Two-word combinations (including "of" and other prepositions)
            for i in range(len(words) - 1):
                # Skip if both words are prepositions
                if words[i].lower() in ['of', 'the', 'and'] and words[i+1].lower() in ['of', 'the', 'and']:
                    continue
                two_words = f"{words[i]} {words[i+1]}"
                if len(two_words) >= 3:
                    aliases.add(two_words.lower())
                    # Abbreviation of two words
                    abbr = f"{words[i][0]}{words[i+1][0]}".lower()
                    if len(abbr) >= 2:
                        aliases.add(abbr)
            
            # Three-word combinations
            for i in range(len(words) - 2):
                three_words = f"{words[i]} {words[i+1]} {words[i+2]}"
                if len(three_words) >= 3:
                    aliases.add(three_words.lower())
                    # Abbreviation of three words
                    abbr = f"{words[i][0]}{words[i+1][0]}{words[i+2][0]}".lower()
                    if len(abbr) >= 2:
                        aliases.add(abbr)
            
            # All words concatenated (for multi-word dealers)
            if len(words) >= 2:
                concat = ''.join(words).lower()
                if len(concat) >= 3:
                    aliases.add(concat)
            
            # Remove common prefixes
            prefixes = ["dealer ", "customer ", "m/s ", "ms ", "m/s. ", "ms. ", "shop "]
            for prefix in prefixes:
                if name_lower.startswith(prefix):
                    without_prefix = name_lower[len(prefix):]
                    if without_prefix:
                        aliases.add(without_prefix)
                        # Also add words from without_prefix
                        for word in without_prefix.split():
                            if len(word) >= 2:
                                aliases.add(word)
            
            # Handle business suffixes
            business_suffixes = ["electronics", "traders", "enterprises", "industries", 
                               "corporation", "company", "group", "trading"]
            for suffix in business_suffixes:
                if name_lower.endswith(suffix):
                    without_suffix = name_lower[:-len(suffix)].strip()
                    if without_suffix:
                        aliases.add(without_suffix)
                        # Add first word of without_suffix
                        if without_suffix:
                            first_word = without_suffix.split()[0]
                            if first_word:
                                aliases.add(first_word)
            
            # FIXED: Special handling for "Mian Group of Chakwal Wah" style names
            # Extract location names (last word or last few words)
            if len(words) >= 2:
                # Last word is often a location
                last_word = words[-1].lower()
                if len(last_word) >= 2:
                    aliases.add(last_word)
                
                # Second last word + last word
                if len(words) >= 2:
                    last_two = f"{words[-2]} {words[-1]}"
                    if len(last_two) >= 3:
                        aliases.add(last_two.lower())
            
            # Add all aliases
            for alias in aliases:
                if alias and len(alias) >= 2:
                    dealer_map[alias] = name
        
        logger.debug(f"Generated {len(dealer_map)} aliases for {len(dealer_names)} dealers")
        return dealer_map
    
    def _build_city_map(self, city_names: List[str]) -> Dict[str, str]:
        """Build city lookup map with intelligent aliases."""
        city_map = {}
        
        for name in city_names:
            if not name or not name.strip():
                continue
            
            name = name.strip()
            name_lower = name.lower()
            
            # Store full name
            city_map[name_lower] = name
            
            # Generate aliases
            aliases = set()
            
            # First 3 letters
            if len(name) >= 3:
                aliases.add(name[:3].lower())
            
            # First 2 letters
            if len(name) >= 2:
                aliases.add(name[:2].lower())
            
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
                "bahawalpur": "bwp",
                "haripur": "hrp",
                "pindigheb": "pdg",
                "abbottabad": "abb",
                "mingora": "mng",
                "dera": "der",
                "sahiwal": "shw",
                "okara": "okr",
                "sheikhupura": "shp"
            }
            
            for full, abbr in common_abbr.items():
                if full in name_lower:
                    aliases.add(abbr)
                    break
            
            # Add all aliases
            for alias in aliases:
                if alias and len(alias) >= 2:
                    city_map[alias] = name
        
        return city_map
    
    def _build_warehouse_map(self, warehouse_names: List[str]) -> Dict[str, str]:
        """Build warehouse lookup map with intelligent aliases."""
        warehouse_map = {}
        
        for name in warehouse_names:
            if not name or not name.strip():
                continue
            
            name = name.strip()
            name_lower = name.lower()
            
            # Store full name
            warehouse_map[name_lower] = name
            
            # Generate aliases
            aliases = set()
            
            # Remove "warehouse" suffix
            if name_lower.endswith(" warehouse"):
                without_suffix = name_lower[:-10].strip()
                if without_suffix:
                    aliases.add(without_suffix)
                    # First word of without_suffix
                    first_word = without_suffix.split()[0]
                    if first_word:
                        aliases.add(first_word)
            
            # All words as aliases
            words = name.split()
            for word in words:
                if len(word) >= 2:
                    aliases.add(word.lower())
            
            # First word
            if words:
                aliases.add(words[0].lower())
            
            # First two words
            if len(words) >= 2:
                two_words = f"{words[0]} {words[1]}"
                aliases.add(two_words.lower())
            
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
                    aliases.add(abbr)
                    break
            
            # Add all aliases
            for alias in aliases:
                if alias and len(alias) >= 2:
                    warehouse_map[alias] = name
        
        return warehouse_map
    
    # ==========================================================
    # FUZZY MATCHING WITH SEQUENCE MATCHER
    # ==========================================================
    
    def _fuzzy_match(self, text: str, candidates: List[str], threshold: float = 0.80) -> Tuple[Optional[str], float]:
        """
        Perform fuzzy matching using SequenceMatcher.
        
        Args:
            text: Text to match
            candidates: List of candidate strings
            threshold: Minimum similarity score (0.0 - 1.0)
            
        Returns:
            Tuple of (best_match, confidence_score)
        """
        if not text or not candidates:
            return None, 0.0
        
        text_lower = text.lower()
        best_match = None
        best_score = 0.0
        
        for candidate in candidates:
            if not candidate:
                continue
            candidate_lower = candidate.lower()
            
            # Calculate similarity
            score = SequenceMatcher(None, text_lower, candidate_lower).ratio()
            
            # Boost score if text is contained in candidate or vice versa
            if text_lower in candidate_lower or candidate_lower in text_lower:
                score = min(1.0, score + 0.1)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = candidate
        
        return best_match, best_score
    
    # ==========================================================
    # ENTITY RESOLUTION (UNIFIED)
    # ==========================================================
    
    def resolve_entity(self, text: str) -> Dict[str, Any]:
        """
        Unified entity resolution - returns dealer, city, or warehouse.
        FIXED: Added confidence scoring and detailed resolution method.
        
        Args:
            text: Input text to resolve
            
        Returns:
            Dict with entity resolution result
        """
        if not text:
            return {"type": "none", "name": None, "confidence": 0.0}
        
        # Clean input
        text_clean = text.strip()
        
        # Try dealer resolution first (most common)
        dealer_result = self.resolve_dealer(text_clean)
        if dealer_result:
            return {
                "type": "dealer",
                "name": dealer_result,
                "confidence": self._get_dealer_confidence(text_clean, dealer_result)
            }
        
        # Try city resolution
        city_result = self.resolve_city(text_clean)
        if city_result:
            return {
                "type": "city",
                "name": city_result,
                "confidence": 0.90
            }
        
        # Try warehouse resolution
        warehouse_result = self.resolve_warehouse(text_clean)
        if warehouse_result:
            return {
                "type": "warehouse",
                "name": warehouse_result,
                "confidence": 0.90
            }
        
        return {"type": "none", "name": None, "confidence": 0.0}
    
    # ==========================================================
    # DEALER RESOLUTION (ENHANCED)
    # ==========================================================
    
    def resolve_dealer(self, text: str) -> Optional[str]:
        """
        Resolve dealer from text using intelligent priority-based matching.
        FIXED: Added SequenceMatcher fuzzy matching.
        
        Resolution order:
        1. Exact Match
        2. Indexed Match (O(1))
        3. Word Boundary Match
        4. Partial Fuzzy Match
        5. SequenceMatcher Fuzzy Match (NEW)
        
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
        
        # STEP 5: SequenceMatcher Fuzzy Match (NEW)
        # Filter candidates with similar word count
        text_words = set(text.split())
        best_candidates = []
        
        for dealer in self._dealer_list:
            dealer_words = set(dealer.lower().split())
            common_words = text_words & dealer_words
            if len(common_words) >= 1:  # At least one common word
                best_candidates.append(dealer)
        
        # If no candidates with common words, try all
        if not best_candidates:
            best_candidates = self._dealer_list
        
        # Fuzzy match
        best_match, confidence = self._fuzzy_match(text, best_candidates, threshold=0.80)
        
        if best_match and confidence >= 0.80:
            logger.debug(f"Dealer resolved (fuzzy sequence): {best_match} (confidence: {confidence:.2f})")
            return best_match
        
        return None
    
    def _get_dealer_confidence(self, input_text: str, resolved_name: str) -> float:
        """
        Calculate confidence score for dealer resolution.
        
        Args:
            input_text: Original input text
            resolved_name: Resolved dealer name
            
        Returns:
            Confidence score (0.0 - 1.0)
        """
        if not input_text or not resolved_name:
            return 0.0
        
        input_lower = input_text.lower().strip()
        resolved_lower = resolved_name.lower().strip()
        
        # Exact match
        if input_lower == resolved_lower:
            return 0.99
        
        # Input is contained in resolved
        if input_lower in resolved_lower:
            return 0.95
        
        # Resolved is contained in input
        if resolved_lower in input_lower:
            return 0.90
        
        # Partial match
        input_words = set(input_lower.split())
        resolved_words = set(resolved_lower.split())
        common_words = input_words & resolved_words
        
        if common_words:
            # At least one common word
            if len(common_words) >= 2:
                return 0.85
            else:
                return 0.80
        
        # SequenceMatcher fallback
        score = SequenceMatcher(None, input_lower, resolved_lower).ratio()
        return min(0.90, score)
    
    def find_dealer_debug(self, name: str) -> Dict[str, Any]:
        """
        Debug method to find dealer resolution details.
        
        Args:
            name: Dealer name to look up
            
        Returns:
            Debug info with resolution details
        """
        result = {
            "input": name,
            "resolved": None,
            "method": "none",
            "confidence": 0.0,
            "all_matches": []
        }
        
        if not name:
            return result
        
        # Try all resolution methods
        methods = [
            ("exact", self._resolve_dealer_exact),
            ("index", self._resolve_dealer_index),
            ("word_boundary", self._resolve_dealer_word_boundary),
            ("fuzzy", self._resolve_dealer_fuzzy),
            ("sequence", self._resolve_dealer_sequence)
        ]
        
        for method_name, method_func in methods:
            resolved = method_func(name)
            if resolved:
                result["resolved"] = resolved
                result["method"] = method_name
                result["confidence"] = self._get_dealer_confidence(name, resolved)
                break
        
        # Get all possible matches
        for dealer in self._dealer_list:
            if dealer.lower() != name.lower():
                score = SequenceMatcher(None, name.lower(), dealer.lower()).ratio()
                if score >= 0.70:
                    result["all_matches"].append({
                        "name": dealer,
                        "similarity": round(score, 3)
                    })
        
        result["all_matches"] = sorted(result["all_matches"], key=lambda x: x["similarity"], reverse=True)[:5]
        
        return result
    
    def _resolve_dealer_exact(self, text: str) -> Optional[str]:
        """Exact match resolution."""
        text_lower = text.lower().strip()
        for alias, dealer in self.dealers.items():
            if alias == text_lower:
                return dealer
        return None
    
    def _resolve_dealer_index(self, text: str) -> Optional[str]:
        """Indexed match resolution."""
        text_lower = text.lower().strip()
        return self._dealer_search_index.get(text_lower)
    
    def _resolve_dealer_word_boundary(self, text: str) -> Optional[str]:
        """Word boundary match resolution."""
        text_lower = text.lower().strip()
        words = text_lower.split()
        for word in words:
            if len(word) >= 2:
                pattern = re.compile(rf'\b{re.escape(word)}\b')
                for alias, dealer in self.dealers.items():
                    if pattern.search(alias):
                        return dealer
        return None
    
    def _resolve_dealer_fuzzy(self, text: str) -> Optional[str]:
        """Partial fuzzy match resolution."""
        text_lower = text.lower().strip()
        for alias, dealer in self.dealers.items():
            if alias in text_lower or text_lower in alias:
                return dealer
        return None
    
    def _resolve_dealer_sequence(self, text: str) -> Optional[str]:
        """SequenceMatcher fuzzy match resolution."""
        text_lower = text.lower().strip()
        best_match, _ = self._fuzzy_match(text_lower, self._dealer_list, threshold=0.80)
        return best_match
    
    def get_sample_dealers(self, limit: int = 10) -> List[Dict[str, str]]:
        """
        Get sample dealers for debugging.
        
        Args:
            limit: Number of dealers to return
            
        Returns:
            List of dealer names
        """
        dealer_list = list(self.dealers.values())[:limit]
        return [{"name": d} for d in dealer_list]
    
    def get_dealer_count(self) -> int:
        """
        Get total number of dealers loaded.
        
        Returns:
            Number of dealers
        """
        return len(self.dealers)
    
    # ==========================================================
    # CITY RESOLUTION (ENHANCED)
    # ==========================================================
    
    def resolve_city(self, text: str) -> Optional[str]:
        """
        Resolve city from text using intelligent priority-based matching.
        FIXED: Added SequenceMatcher fuzzy matching.
        
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
        
        # STEP 5: SequenceMatcher Fuzzy Match (NEW)
        best_match, confidence = self._fuzzy_match(text, self._city_list, threshold=0.80)
        if best_match and confidence >= 0.80:
            logger.debug(f"City resolved (fuzzy sequence): {best_match} (confidence: {confidence:.2f})")
            return best_match
        
        return None
    
    def find_city_debug(self, name: str) -> Dict[str, Any]:
        """
        Debug method to find city resolution details.
        
        Args:
            name: City name to look up
            
        Returns:
            Debug info with resolution details
        """
        result = {
            "input": name,
            "resolved": None,
            "method": "none",
            "confidence": 0.0
        }
        
        if not name:
            return result
        
        resolved = self.resolve_city(name)
        if resolved:
            result["resolved"] = resolved
            result["method"] = "city_resolution"
            result["confidence"] = 0.90
        
        return result
    
    # ==========================================================
    # WAREHOUSE RESOLUTION (ENHANCED)
    # ==========================================================
    
    def resolve_warehouse(self, text: str) -> Optional[str]:
        """
        Resolve warehouse from text using intelligent priority-based matching.
        FIXED: Added SequenceMatcher fuzzy matching.
        
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
        
        # STEP 5: SequenceMatcher Fuzzy Match (NEW)
        best_match, confidence = self._fuzzy_match(text, self._warehouse_list, threshold=0.80)
        if best_match and confidence >= 0.80:
            logger.debug(f"Warehouse resolved (fuzzy sequence): {best_match} (confidence: {confidence:.2f})")
            return best_match
        
        return None
    
    def find_warehouse_debug(self, name: str) -> Dict[str, Any]:
        """
        Debug method to find warehouse resolution details.
        
        Args:
            name: Warehouse name to look up
            
        Returns:
            Debug info with resolution details
        """
        result = {
            "input": name,
            "resolved": None,
            "method": "none",
            "confidence": 0.0
        }
        
        if not name:
            return result
        
        resolved = self.resolve_warehouse(name)
        if resolved:
            result["resolved"] = resolved
            result["method"] = "warehouse_resolution"
            result["confidence"] = 0.90
        
        return result
    
    # ==========================================================
    # VALIDATION
    # ==========================================================
    
    def _validate_or_raise(self):
        """Validate metadata and raise if critical data missing."""
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
    # INTENT DETECTION
    # ==========================================================
    
    def detect_intent(self, text: str) -> Tuple[Optional[str], float]:
        """
        Detect intent from text using priority-based scoring.
        FIXED: Better handling of entity-only queries.
        
        Args:
            text: Input text to analyze
            
        Returns:
            Tuple of (intent_name, confidence_score)
        """
        if not text:
            return None, 0.0
        
        text = text.lower().strip()
        
        # Check for DN first
        if DN_PATTERN.search(text):
            logger.debug(f"Intent detected: dn_lookup (DN number found)")
            return "dn_lookup", 0.95
        
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
    # DELIVERY METRICS CALCULATION
    # ==========================================================
    
    def calculate_delivery_metrics(
        self,
        dn_date: Optional[datetime],
        pgi_date: Optional[datetime],
        pod_date: Optional[datetime]
    ) -> Dict[str, Any]:
        """
        Calculate delivery metrics with data quality validation.
        FIXED: Correct sequence validation.
        
        Args:
            dn_date: DN Create Date
            pgi_date: Good Issue Date
            pod_date: POD Date
            
        Returns:
            Dict with validation results and calculated metrics
        """
        return self.delivery_metrics.validate_dates(dn_date, pgi_date, pod_date)
    
    def get_delivery_metrics_definition(self) -> Dict[str, Any]:
        """
        Get delivery metrics calculation rules.
        
        Returns:
            Dict with rule definitions
        """
        return {
            "processing_time": {
                "rule": self.delivery_metrics.processing_time_rule,
                "target_days": self.rules["sla"]["processing_time_target"],
                "thresholds": self.rules["processing_time"]["thresholds"]
            },
            "delivery_time": {
                "rule": self.delivery_metrics.delivery_time_rule,
                "target_days": self.rules["sla"]["delivery_time_target"],
                "thresholds": self.rules["delivery_time"]["thresholds"]
            },
            "total_cycle_time": {
                "rule": self.delivery_metrics.total_cycle_time_rule,
                "target_days": self.rules["sla"]["total_cycle_time_target"],
                "thresholds": self.rules["total_cycle_time"]["thresholds"]
            }
        }
    
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
    
    def get_data_quality_status(self, validation_result: Dict[str, Any]) -> str:
        """
        Get data quality status from validation result.
        
        Args:
            validation_result: Result from calculate_delivery_metrics
            
        Returns:
            Data quality status string
        """
        if not validation_result.get("is_valid", False):
            return "error"
        elif validation_result.get("issues", []):
            return "warning"
        return "valid"
    
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
            "intents": len(self.intents),
            "metrics": len(self.metrics),
            "health_score": self._health_score,
            "initialized": self._initialized,
            "database_connected": self._db_connected,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "load_error": self._load_error,
            "stats": self._stats,
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
            "intents": len(self.intents),
            "metrics": len(self.metrics),
            "health_score": self._health_score,
            "initialized": self._initialized,
            "search_index_sizes": {
                "dealers": len(self._dealer_search_index),
                "cities": len(self._city_search_index),
                "warehouses": len(self._warehouse_search_index)
            },
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "stats": self._stats,
            "timestamp": datetime.now().isoformat()
        }
    
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
                    logger.info("✅ SchemaService singleton initialized")
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


def calculate_delivery_metrics(
    dn_date: Optional[datetime],
    pgi_date: Optional[datetime],
    pod_date: Optional[datetime]
) -> Dict[str, Any]:
    """
    Calculate delivery metrics with data quality validation.
    
    Args:
        dn_date: DN Create Date
        pgi_date: Good Issue Date
        pod_date: POD Date
        
    Returns:
        Dict with validation results and calculated metrics
    """
    service = get_schema_service()
    return service.calculate_delivery_metrics(dn_date, pgi_date, pod_date)


def resolve_entity(text: str) -> Dict[str, Any]:
    """
    Unified entity resolution.
    
    Args:
        text: Input text to resolve
        
    Returns:
        Dict with entity resolution result
    """
    service = get_schema_service()
    return service.resolve_entity(text)


def find_dealer_debug(name: str) -> Dict[str, Any]:
    """
    Debug method to find dealer resolution details.
    
    Args:
        name: Dealer name to look up
        
    Returns:
        Debug info with resolution details
    """
    service = get_schema_service()
    return service.find_dealer_debug(name)


def find_city_debug(name: str) -> Dict[str, Any]:
    """
    Debug method to find city resolution details.
    
    Args:
        name: City name to look up
        
    Returns:
        Debug info with resolution details
    """
    service = get_schema_service()
    return service.find_city_debug(name)


def find_warehouse_debug(name: str) -> Dict[str, Any]:
    """
    Debug method to find warehouse resolution details.
    
    Args:
        name: Warehouse name to look up
        
    Returns:
        Debug info with resolution details
    """
    service = get_schema_service()
    return service.find_warehouse_debug(name)


def get_sample_dealers(limit: int = 10) -> List[Dict[str, str]]:
    """
    Get sample dealers for debugging.
    
    Args:
        limit: Number of dealers to return
        
    Returns:
        List of dealer names
    """
    service = get_schema_service()
    return service.get_sample_dealers(limit)


def get_dealer_count() -> int:
    """
    Get total number of dealers loaded.
    
    Returns:
        Number of dealers
    """
    service = get_schema_service()
    return service.get_dealer_count()


# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    # Main classes
    'SchemaService',
    'DeliveryMetrics',
    'DeliveryRepository',
    
    # Singleton helpers
    'get_schema_service',
    'refresh_schema_metadata',
    'get_schema_health',
    'get_schema_diagnostics',
    
    # Entity resolution
    'resolve_entity',
    'find_dealer_debug',
    'find_city_debug',
    'find_warehouse_debug',
    'get_sample_dealers',
    'get_dealer_count',
    
    # DN helpers
    'is_dn_number',
    'extract_dn_number',
    
    # Delivery metrics
    'calculate_delivery_metrics',
    
    # Constants
    'DN_PATTERN',
    'INTENT_KEYWORDS',
    'METRIC_KEYWORDS',
    'LOGISTICS_KEYWORDS',
    'BUSINESS_RULES',
    'STATUS_DEFINITIONS',
]
