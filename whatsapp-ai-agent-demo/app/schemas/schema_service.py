# ==========================================================
# FILE: app/schemas/schema_service.py (v7.3 - FULL AUTO-DETECTION)
# ==========================================================
# FIXES APPLIED:
# 1. ✅ AUTO-DETECTS column names from PostgreSQL
# 2. ✅ Enhanced dealer alias generation with SequenceMatcher
# 3. ✅ Added resolve_entity() for unified entity resolution
# 4. ✅ Added debug methods (find_dealer_debug, get_sample_dealers)
# 5. ✅ Improved logging with structured output
# 6. ✅ Added confidence scoring for entity resolution
# 7. ✅ ADDED: generate_metadata_report() - Comprehensive metadata report
# 8. ✅ ADDED: get_metadata_stats() - Quick metadata statistics
# 9. ✅ ADDED: find_entity_debug() - Unified entity debug
# 10. ✅ ADDED: get_all_entities() - Get all entities by type
# 11. ✅ ADDED: search_entities() - Search across all entities
# 12. ✅ ADDED: Auto-detection of actual column names from database
# 13. ✅ ADDED: Fallback column name resolution
# 14. ✅ ADDED: Detailed logging of column detection
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
    "dn_lookup": [
        ("dn", 10), ("delivery note", 10), ("track dn", 10),
        ("track delivery", 10), ("check dn", 9), ("dn status", 9)
    ],
    
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
    
    "warehouse_dashboard": [
        ("warehouse", 9), ("show warehouse", 10), ("warehouse summary", 9),
        ("warehouse details", 9), ("warehouse status", 9)
    ],
    "warehouse_performance": [
        ("warehouse performance", 10), ("warehouse kpi", 10),
        ("warehouse metrics", 9), ("warehouse efficiency", 9)
    ],
    
    "city_dashboard": [
        ("city", 9), ("show city", 10), ("city summary", 9),
        ("city details", 9), ("city status", 9)
    ],
    "city_performance": [
        ("city performance", 10), ("city kpi", 10),
        ("city metrics", 9), ("city efficiency", 9)
    ],
    
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
    
    "root_cause": [
        ("root cause", 10), ("what is the key issue", 10), 
        ("why delayed", 10), ("why aging", 10), ("key issue", 9),
        ("how to bring improvement", 10), ("how to improve", 9),
        ("reason", 8), ("cause", 9), ("improvement areas", 10),
        ("bring improvement", 10), ("critical issue", 9)
    ],
    
    "executive_insight": [
        ("executive insight", 10), ("executive summary", 10),
        ("bottleneck", 9), ("critical issues", 9),
        ("top issues", 9), ("urgent matters", 9)
    ],
    "control_tower": [
        ("control tower", 10), ("critical", 8), ("urgent", 8),
        ("priority", 8), ("alert", 7), ("command center", 9)
    ],
    
    "delivery_performance": [
        ("delivery performance", 10), ("delivery kpi", 10),
        ("delivery metrics", 9), ("delivery efficiency", 9),
        ("on time delivery", 9), ("delivery rate", 9)
    ],
    
    "trend": [
        ("trend", 8), ("month over month", 9), ("trends", 9),
        ("over time", 8), ("historical", 8), ("performance trend", 10)
    ],
    "comparison": [
        ("compare", 8), ("vs", 7), ("versus", 7),
        ("comparison", 8), ("between", 7), ("compare dealers", 10),
        ("compare warehouses", 10), ("compare cities", 10)
    ],
    
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
# METRIC KEYWORDS
# ==========================================================

METRIC_KEYWORDS: Dict[str, List[str]] = {
    "revenue": ["revenue", "sales", "amount", "total revenue", "total sales", "sales amount"],
    "units": ["units", "quantity", "qty", "total units", "unit count", "number of units"],
    "dn_count": ["dns", "delivery notes", "orders", "total dns", "order count"],
    "pending_pod": ["pending pod", "pod pending", "pod not done", "open pod"],
    "pending_delivery": ["pending delivery", "delivery pending", "pending pgi", "undelivered"],
    "delivery_aging": ["delivery aging", "pgi aging", "delivery delay", "pgi delay"],
    "pod_aging": ["pod aging", "pod delay", "pod latency", "aging pod"],
    "pod_rate": ["pod rate", "pod percentage", "pod completion", "pod ratio"],
    "pgi_rate": ["pgi rate", "pgi percentage", "pgi completion", "pgi ratio"],
    "delivery_rate": ["delivery rate", "delivery percentage", "delivery completion", "on-time delivery"],
    "success_rate": ["success rate", "success percentage", "completion rate"],
    "failure_rate": ["failure rate", "failure percentage", "error rate"],
    "avg_delivery_time": ["avg delivery time", "average delivery", "mean delivery", "average delivery time"],
    "processing_time": ["processing time", "pgi time", "good issue time"],
    "total_cycle_time": ["cycle time", "total cycle", "total time"],
    "total_deliveries": ["total deliveries", "total dispatched", "total sent"],
    "total_revenue": ["total revenue", "total sales", "overall revenue"],
    "total_units": ["total units", "total quantity", "overall units"],
}

# ==========================================================
# LOGISTICS KEYWORDS
# ==========================================================

LOGISTICS_KEYWORDS: Set[str] = {
    'pending', 'delivered', 'in_transit', 'dispatched', 'shipped', 'received',
    'pgi', 'pod', 'aging', 'delivery', 'revenue', 'units', 'performance',
    'critical', 'alert', 'urgent', 'priority', 'control', 'tower',
    'help', 'menu', 'status', 'what', 'how', 'why', 'when', 'where',
    'who', 'which', 'can', 'could', 'would', 'should', 'is', 'are',
    'show', 'display', 'get', 'tell', 'view', 'list', 'fetch', 'find',
    'warehouse', 'summary', 'report', 'kpi', 'dashboard', 'insight',
    'issue', 'problem', 'bottleneck', 'root', 'cause', 'reason',
    'dealer', 'customer', 'city', 'stock', 'inventory', 'sales',
    'transit', 'delivered', 'rate', 'completion', 'dn', 'order',
    'compare', 'versus', 'vs', 'between', 'against',
    'today', 'yesterday', 'week', 'month', 'year', 'last', 'this',
    'current', 'day', 'week', 'month', 'year', 'all',
    'top', 'bottom', 'best', 'worst', 'highest', 'lowest', 'average',
    'total', 'all', 'some', 'most', 'least', 'more', 'less', 'much',
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
# AUTO-DETECTION DELIVERY REPOSITORY
# ==========================================================

class DeliveryRepository:
    """
    Embedded repository for Delivery Report database operations.
    ✅ AUTO-DETECTS column names from PostgreSQL.
    """
    
    def __init__(self, db_session=None):
        self._session = db_session
        self._column_cache = {
            'dealer_col': None,
            'city_col': None,
            'warehouse_col': None,
            'dn_col': None,
            'amount_col': None,
            'qty_col': None,
            'dn_date_col': None,
            'pgi_date_col': None,
            'pod_date_col': None
        }
        self._detected_columns = False
    
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
    
    def _detect_columns(self):
        """
        AUTO-DETECT actual column names from PostgreSQL.
        This is the critical fix for deployment issues.
        """
        if self._detected_columns:
            return
        
        try:
            session = self._get_session()
            
            try:
                from app.models.delivery_report import DeliveryReport
            except ImportError:
                logger.error("❌ Cannot import DeliveryReport model")
                return
            
            # Get actual column names from the model
            columns = [col.key for col in DeliveryReport.__table__.columns]
            logger.info(f"🔍 Found columns in database: {columns}")
            
            # ==========================================================
            # DETECT DEALER COLUMN
            # ==========================================================
            dealer_candidates = ['sold_to_party_name', 'customer_name', 'dealer_name', 'party_name', 'customer']
            for col in dealer_candidates:
                if col in columns:
                    self._column_cache['dealer_col'] = col
                    logger.info(f"✅ Found dealer column: '{col}'")
                    break
            
            # ==========================================================
            # DETECT CITY COLUMN
            # ==========================================================
            city_candidates = ['ship_to_city', 'city', 'dealer_city', 'city_name', 'customer_city']
            for col in city_candidates:
                if col in columns:
                    self._column_cache['city_col'] = col
                    logger.info(f"✅ Found city column: '{col}'")
                    break
            
            # ==========================================================
            # DETECT WAREHOUSE COLUMN
            # ==========================================================
            warehouse_candidates = ['warehouse', 'warehouse_name', 'warehouse_location', 'plant']
            for col in warehouse_candidates:
                if col in columns:
                    self._column_cache['warehouse_col'] = col
                    logger.info(f"✅ Found warehouse column: '{col}'")
                    break
            
            # ==========================================================
            # DETECT DN NUMBER COLUMN
            # ==========================================================
            dn_candidates = ['dn_no', 'delivery_note_no', 'dn_number', 'delivery_no', 'doc_no']
            for col in dn_candidates:
                if col in columns:
                    self._column_cache['dn_col'] = col
                    logger.info(f"✅ Found DN column: '{col}'")
                    break
            
            # ==========================================================
            # DETECT AMOUNT COLUMN
            # ==========================================================
            amount_candidates = ['dn_amount', 'amount', 'total_amount', 'value', 'invoice_amount']
            for col in amount_candidates:
                if col in columns:
                    self._column_cache['amount_col'] = col
                    logger.info(f"✅ Found amount column: '{col}'")
                    break
            
            # ==========================================================
            # DETECT QUANTITY COLUMN
            # ==========================================================
            qty_candidates = ['dn_qty', 'quantity', 'qty', 'units', 'total_qty']
            for col in qty_candidates:
                if col in columns:
                    self._column_cache['qty_col'] = col
                    logger.info(f"✅ Found quantity column: '{col}'")
                    break
            
            # ==========================================================
            # DETECT DATE COLUMNS
            # ==========================================================
            dn_date_candidates = ['dn_create_date', 'dn_date', 'created_date', 'order_date']
            for col in dn_date_candidates:
                if col in columns:
                    self._column_cache['dn_date_col'] = col
                    logger.info(f"✅ Found DN date column: '{col}'")
                    break
            
            pgi_date_candidates = ['good_issue_date', 'pgi_date', 'dispatch_date', 'issue_date']
            for col in pgi_date_candidates:
                if col in columns:
                    self._column_cache['pgi_date_col'] = col
                    logger.info(f"✅ Found PGI date column: '{col}'")
                    break
            
            pod_date_candidates = ['pod_date', 'delivery_date', 'received_date', 'confirmation_date']
            for col in pod_date_candidates:
                if col in columns:
                    self._column_cache['pod_date_col'] = col
                    logger.info(f"✅ Found POD date column: '{col}'")
                    break
            
            # Set defaults if not found
            if self._column_cache['dealer_col'] is None:
                self._column_cache['dealer_col'] = 'sold_to_party_name'
                logger.warning("⚠️ Using default dealer column: 'sold_to_party_name'")
            
            if self._column_cache['city_col'] is None:
                self._column_cache['city_col'] = 'ship_to_city'
                logger.warning("⚠️ Using default city column: 'ship_to_city'")
            
            if self._column_cache['warehouse_col'] is None:
                self._column_cache['warehouse_col'] = 'warehouse'
                logger.warning("⚠️ Using default warehouse column: 'warehouse'")
            
            if self._column_cache['dn_col'] is None:
                self._column_cache['dn_col'] = 'dn_no'
                logger.warning("⚠️ Using default DN column: 'dn_no'")
            
            self._detected_columns = True
            
            logger.info("=" * 60)
            logger.info("✅ COLUMN DETECTION COMPLETE:")
            logger.info(f"   Dealer:  {self._column_cache['dealer_col']}")
            logger.info(f"   City:    {self._column_cache['city_col']}")
            logger.info(f"   Warehouse: {self._column_cache['warehouse_col']}")
            logger.info(f"   DN:      {self._column_cache['dn_col']}")
            logger.info(f"   Amount:  {self._column_cache['amount_col']}")
            logger.info(f"   Qty:     {self._column_cache['qty_col']}")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"❌ Column detection failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Set defaults
            self._column_cache['dealer_col'] = 'sold_to_party_name'
            self._column_cache['city_col'] = 'ship_to_city'
            self._column_cache['warehouse_col'] = 'warehouse'
            self._column_cache['dn_col'] = 'dn_no'
            self._detected_columns = True
    
    def get_column(self, column_type: str) -> str:
        """Get the actual column name for a given type."""
        self._detect_columns()
        return self._column_cache.get(f'{column_type}_col', column_type)
    
    def get_distinct_customers(self) -> List[Dict[str, Any]]:
        """Get all unique customer/dealer names from delivery reports."""
        try:
            session = self._get_session()
            self._detect_columns()
            
            try:
                from app.models.delivery_report import DeliveryReport
            except ImportError:
                logger.error("❌ Cannot import DeliveryReport model")
                return []
            
            dealer_col = self._column_cache['dealer_col']
            
            if not hasattr(DeliveryReport, dealer_col):
                logger.error(f"❌ Column '{dealer_col}' not found in DeliveryReport")
                return []
            
            col_attr = getattr(DeliveryReport, dealer_col)
            
            results = session.query(
                col_attr.label('customer_name')
            ).filter(
                col_attr.isnot(None)
            ).filter(
                col_attr != ''
            ).distinct().order_by(
                col_attr
            ).all()
            
            dealers = [{"customer_name": r[0]} for r in results if r[0]]
            logger.info(f"✅ Loaded {len(dealers)} distinct customers from column '{dealer_col}'")
            
            # Log sample if available
            if len(dealers) > 0:
                sample = [d['customer_name'] for d in dealers[:5]]
                logger.info(f"   Sample: {sample}")
            
            return dealers
            
        except Exception as e:
            logger.error(f"❌ Failed to load distinct customers: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def get_distinct_cities(self) -> List[Dict[str, Any]]:
        """Get all unique ship-to cities from delivery reports."""
        try:
            session = self._get_session()
            self._detect_columns()
            
            try:
                from app.models.delivery_report import DeliveryReport
            except ImportError:
                logger.error("❌ Cannot import DeliveryReport model")
                return []
            
            city_col = self._column_cache['city_col']
            
            if not hasattr(DeliveryReport, city_col):
                logger.error(f"❌ Column '{city_col}' not found in DeliveryReport")
                return []
            
            col_attr = getattr(DeliveryReport, city_col)
            
            results = session.query(
                col_attr.label('city')
            ).filter(
                col_attr.isnot(None)
            ).filter(
                col_attr != ''
            ).distinct().order_by(
                col_attr
            ).all()
            
            cities = [{"city": r[0]} for r in results if r[0]]
            logger.info(f"✅ Loaded {len(cities)} distinct cities from column '{city_col}'")
            return cities
            
        except Exception as e:
            logger.error(f"❌ Failed to load distinct cities: {e}")
            return []
    
    def get_distinct_warehouses(self) -> List[Dict[str, Any]]:
        """Get all unique warehouses from delivery reports."""
        try:
            session = self._get_session()
            self._detect_columns()
            
            try:
                from app.models.delivery_report import DeliveryReport
            except ImportError:
                logger.error("❌ Cannot import DeliveryReport model")
                return []
            
            warehouse_col = self._column_cache['warehouse_col']
            
            if not hasattr(DeliveryReport, warehouse_col):
                logger.error(f"❌ Column '{warehouse_col}' not found in DeliveryReport")
                return []
            
            col_attr = getattr(DeliveryReport, warehouse_col)
            
            results = session.query(
                col_attr.label('warehouse')
            ).filter(
                col_attr.isnot(None)
            ).filter(
                col_attr != ''
            ).distinct().order_by(
                col_attr
            ).all()
            
            warehouses = [{"warehouse": r[0]} for r in results if r[0]]
            logger.info(f"✅ Loaded {len(warehouses)} distinct warehouses from column '{warehouse_col}'")
            return warehouses
            
        except Exception as e:
            logger.error(f"❌ Failed to load distinct warehouses: {e}")
            return []

# ==========================================================
# SCHEMA SERVICE - METADATA INTELLIGENCE ENGINE
# ==========================================================

class SchemaService:
    """
    Central Metadata Intelligence Engine for Logistics Analytics.
    ✅ AUTO-DETECTS column names from PostgreSQL.
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
        ✅ Uses AUTO-DETECTED column names.
        
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
                self._dealer_list = list(self.dealers.values())
                logger.info(f"  ✅ Loaded {len(self.dealers)} dealers")
                
                # Log sample dealers for debugging
                if len(self.dealers) > 0:
                    sample = list(self.dealers.values())[:5]
                    logger.info(f"  📋 Sample dealers: {sample}")
                else:
                    logger.warning("  ⚠️ No dealers loaded! Check database connection and column names.")
                
                # Load cities
                cities_data = repo.get_distinct_cities()
                city_names = [c['city'] for c in cities_data if c.get('city')]
                self.cities = self._build_city_map(city_names)
                self._city_search_index = self._build_search_index(self.cities)
                self._city_list = list(self.cities.values())
                logger.info(f"  ✅ Loaded {len(self.cities)} cities")
                
                # Load warehouses
                warehouses_data = repo.get_distinct_warehouses()
                warehouse_names = [w['warehouse'] for w in warehouses_data if w.get('warehouse')]
                self.warehouses = self._build_warehouse_map(warehouse_names)
                self._warehouse_search_index = self._build_search_index(self.warehouses)
                self._warehouse_list = list(self.warehouses.values())
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
                import traceback
                logger.error(traceback.format_exc())
                
                return {
                    "status": "failed",
                    "error": str(e),
                    "dealers": len(self.dealers),
                    "cities": len(self.cities),
                    "warehouses": len(self.warehouses),
                    "initialized": False
                }
    
    # ==========================================================
    # BUILD FUNCTIONS
    # ==========================================================
    
    def _build_search_index(self, data: Dict[str, str]) -> Dict[str, str]:
        """Build search index for O(1) lookups."""
        index = {}
        for alias, full_name in data.items():
            normalized = alias.lower().strip()
            index[normalized] = full_name
            
            prefixes = ["dealer ", "customer ", "warehouse "]
            for prefix in prefixes:
                if normalized.startswith(prefix):
                    without_prefix = normalized[len(prefix):]
                    if without_prefix:
                        index[without_prefix] = full_name
        return index
    
    def _build_dealer_map(self, dealer_names: List[str]) -> Dict[str, str]:
        """Build dealer lookup map with intelligent aliases."""
        dealer_map = {}
        
        for name in dealer_names:
            if not name or not name.strip():
                continue
            
            name = name.strip()
            name_lower = name.lower()
            dealer_map[name_lower] = name
            
            words = name.split()
            aliases = set()
            
            for word in words:
                if len(word) >= 2:
                    aliases.add(word.lower())
                    if len(word) >= 3:
                        aliases.add(word[:3].lower())
                    if len(word) >= 2:
                        aliases.add(word[:2].lower())
            
            for i in range(len(words) - 1):
                if words[i].lower() in ['of', 'the', 'and'] and words[i+1].lower() in ['of', 'the', 'and']:
                    continue
                two_words = f"{words[i]} {words[i+1]}"
                if len(two_words) >= 3:
                    aliases.add(two_words.lower())
                    abbr = f"{words[i][0]}{words[i+1][0]}".lower()
                    if len(abbr) >= 2:
                        aliases.add(abbr)
            
            for i in range(len(words) - 2):
                three_words = f"{words[i]} {words[i+1]} {words[i+2]}"
                if len(three_words) >= 3:
                    aliases.add(three_words.lower())
                    abbr = f"{words[i][0]}{words[i+1][0]}{words[i+2][0]}".lower()
                    if len(abbr) >= 2:
                        aliases.add(abbr)
            
            if len(words) >= 2:
                concat = ''.join(words).lower()
                if len(concat) >= 3:
                    aliases.add(concat)
            
            prefixes = ["dealer ", "customer ", "m/s ", "ms ", "m/s. ", "ms. ", "shop "]
            for prefix in prefixes:
                if name_lower.startswith(prefix):
                    without_prefix = name_lower[len(prefix):]
                    if without_prefix:
                        aliases.add(without_prefix)
                        for word in without_prefix.split():
                            if len(word) >= 2:
                                aliases.add(word)
            
            business_suffixes = ["electronics", "traders", "enterprises", "industries", 
                               "corporation", "company", "group", "trading"]
            for suffix in business_suffixes:
                if name_lower.endswith(suffix):
                    without_suffix = name_lower[:-len(suffix)].strip()
                    if without_suffix:
                        aliases.add(without_suffix)
                        if without_suffix:
                            first_word = without_suffix.split()[0]
                            if first_word:
                                aliases.add(first_word)
            
            if len(words) >= 2:
                last_word = words[-1].lower()
                if len(last_word) >= 2:
                    aliases.add(last_word)
                if len(words) >= 2:
                    last_two = f"{words[-2]} {words[-1]}"
                    if len(last_two) >= 3:
                        aliases.add(last_two.lower())
            
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
            city_map[name_lower] = name
            
            aliases = set()
            
            if len(name) >= 3:
                aliases.add(name[:3].lower())
            if len(name) >= 2:
                aliases.add(name[:2].lower())
            
            common_abbr = {
                "lahore": "lhr", "karachi": "khi", "islamabad": "isb",
                "rawalpindi": "rwp", "multan": "mux", "faisalabad": "fsd",
                "peshawar": "pwr", "quetta": "qta", "hyderabad": "hyd",
                "gujranwala": "guj", "sialkot": "skt", "bahawalpur": "bwp",
                "haripur": "hrp", "pindigheb": "pdg", "abbottabad": "abb",
                "mingora": "mng", "dera": "der", "sahiwal": "shw",
                "okara": "okr", "sheikhupura": "shp"
            }
            
            for full, abbr in common_abbr.items():
                if full in name_lower:
                    aliases.add(abbr)
                    break
            
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
            warehouse_map[name_lower] = name
            
            aliases = set()
            
            if name_lower.endswith(" warehouse"):
                without_suffix = name_lower[:-10].strip()
                if without_suffix:
                    aliases.add(without_suffix)
                    first_word = without_suffix.split()[0]
                    if first_word:
                        aliases.add(first_word)
            
            words = name.split()
            for word in words:
                if len(word) >= 2:
                    aliases.add(word.lower())
            
            if words:
                aliases.add(words[0].lower())
            
            if len(words) >= 2:
                two_words = f"{words[0]} {words[1]}"
                aliases.add(two_words.lower())
            
            common_abbr = {
                "lahore": "lhr", "karachi": "khi", "islamabad": "isb",
                "rawalpindi": "rwp", "multan": "mux", "faisalabad": "fsd",
                "peshawar": "pwr", "quetta": "qta",
            }
            
            for full, abbr in common_abbr.items():
                if full in name_lower:
                    aliases.add(abbr)
                    break
            
            for alias in aliases:
                if alias and len(alias) >= 2:
                    warehouse_map[alias] = name
        
        return warehouse_map
    
    # ==========================================================
    # FUZZY MATCHING
    # ==========================================================
    
    def _fuzzy_match(self, text: str, candidates: List[str], threshold: float = 0.80) -> Tuple[Optional[str], float]:
        """Perform fuzzy matching using SequenceMatcher."""
        if not text or not candidates:
            return None, 0.0
        
        text_lower = text.lower()
        best_match = None
        best_score = 0.0
        
        for candidate in candidates:
            if not candidate:
                continue
            candidate_lower = candidate.lower()
            
            score = SequenceMatcher(None, text_lower, candidate_lower).ratio()
            
            if text_lower in candidate_lower or candidate_lower in text_lower:
                score = min(1.0, score + 0.1)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = candidate
        
        return best_match, best_score
    
    # ==========================================================
    # ENTITY RESOLUTION
    # ==========================================================
    
    def resolve_entity(self, text: str) -> Dict[str, Any]:
        """Unified entity resolution - returns dealer, city, or warehouse."""
        if not text:
            return {"type": "none", "name": None, "confidence": 0.0}
        
        text_clean = text.strip()
        
        dealer_result = self.resolve_dealer(text_clean)
        if dealer_result:
            return {
                "type": "dealer",
                "name": dealer_result,
                "confidence": self._get_dealer_confidence(text_clean, dealer_result)
            }
        
        city_result = self.resolve_city(text_clean)
        if city_result:
            return {
                "type": "city",
                "name": city_result,
                "confidence": 0.90
            }
        
        warehouse_result = self.resolve_warehouse(text_clean)
        if warehouse_result:
            return {
                "type": "warehouse",
                "name": warehouse_result,
                "confidence": 0.90
            }
        
        return {"type": "none", "name": None, "confidence": 0.0}
    
    # ==========================================================
    # DEALER RESOLUTION
    # ==========================================================
    
    def resolve_dealer(self, text: str) -> Optional[str]:
        """Resolve dealer from text using intelligent priority-based matching."""
        if not text:
            return None
        
        text = text.lower().strip()
        
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
        
        # STEP 2: Indexed Match
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
        
        # STEP 5: SequenceMatcher Fuzzy Match
        text_words = set(text.split())
        best_candidates = []
        
        for dealer in self._dealer_list:
            dealer_words = set(dealer.lower().split())
            common_words = text_words & dealer_words
            if len(common_words) >= 1:
                best_candidates.append(dealer)
        
        if not best_candidates:
            best_candidates = self._dealer_list
        
        best_match, confidence = self._fuzzy_match(text, best_candidates, threshold=0.80)
        
        if best_match and confidence >= 0.80:
            logger.debug(f"Dealer resolved (fuzzy sequence): {best_match} (confidence: {confidence:.2f})")
            return best_match
        
        return None
    
    def _get_dealer_confidence(self, input_text: str, resolved_name: str) -> float:
        """Calculate confidence score for dealer resolution."""
        if not input_text or not resolved_name:
            return 0.0
        
        input_lower = input_text.lower().strip()
        resolved_lower = resolved_name.lower().strip()
        
        if input_lower == resolved_lower:
            return 0.99
        
        if input_lower in resolved_lower:
            return 0.95
        
        if resolved_lower in input_lower:
            return 0.90
        
        input_words = set(input_lower.split())
        resolved_words = set(resolved_lower.split())
        common_words = input_words & resolved_words
        
        if common_words:
            if len(common_words) >= 2:
                return 0.85
            else:
                return 0.80
        
        score = SequenceMatcher(None, input_lower, resolved_lower).ratio()
        return min(0.90, score)
    
    def find_dealer_debug(self, name: str) -> Dict[str, Any]:
        """Debug method to find dealer resolution details."""
        result = {
            "input": name,
            "resolved": None,
            "method": "none",
            "confidence": 0.0,
            "all_matches": []
        }
        
        if not name:
            return result
        
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
        text_lower = text.lower().strip()
        for alias, dealer in self.dealers.items():
            if alias == text_lower:
                return dealer
        return None
    
    def _resolve_dealer_index(self, text: str) -> Optional[str]:
        text_lower = text.lower().strip()
        return self._dealer_search_index.get(text_lower)
    
    def _resolve_dealer_word_boundary(self, text: str) -> Optional[str]:
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
        text_lower = text.lower().strip()
        for alias, dealer in self.dealers.items():
            if alias in text_lower or text_lower in alias:
                return dealer
        return None
    
    def _resolve_dealer_sequence(self, text: str) -> Optional[str]:
        text_lower = text.lower().strip()
        best_match, _ = self._fuzzy_match(text_lower, self._dealer_list, threshold=0.80)
        return best_match
    
    def get_sample_dealers(self, limit: int = 10) -> List[Dict[str, str]]:
        dealer_list = list(self.dealers.values())[:limit]
        return [{"name": d} for d in dealer_list]
    
    def get_dealer_count(self) -> int:
        return len(self.dealers)
    
    # ==========================================================
    # CITY RESOLUTION
    # ==========================================================
    
    def resolve_city(self, text: str) -> Optional[str]:
        if not text:
            return None
        
        text = text.lower().strip()
        
        for alias, city in self.cities.items():
            if alias == text:
                return city
        
        if text in self._city_search_index:
            return self._city_search_index[text]
        
        words = text.split()
        for word in words:
            if len(word) >= 2:
                pattern = re.compile(rf'\b{re.escape(word)}\b')
                for alias, city in self.cities.items():
                    if pattern.search(alias):
                        logger.debug(f"City resolved (word boundary): {city} from '{text}'")
                        return city
        
        for alias, city in self.cities.items():
            if alias in text or text in alias:
                logger.debug(f"City resolved (fuzzy): {city} from '{text}'")
                return city
        
        best_match, confidence = self._fuzzy_match(text, self._city_list, threshold=0.80)
        if best_match and confidence >= 0.80:
            logger.debug(f"City resolved (fuzzy sequence): {best_match} (confidence: {confidence:.2f})")
            return best_match
        
        return None
    
    def find_city_debug(self, name: str) -> Dict[str, Any]:
        result = {"input": name, "resolved": None, "method": "none", "confidence": 0.0}
        if not name:
            return result
        resolved = self.resolve_city(name)
        if resolved:
            result["resolved"] = resolved
            result["method"] = "city_resolution"
            result["confidence"] = 0.90
        return result
    
    # ==========================================================
    # WAREHOUSE RESOLUTION
    # ==========================================================
    
    def resolve_warehouse(self, text: str) -> Optional[str]:
        if not text:
            return None
        
        text = text.lower().strip()
        
        for alias, warehouse in self.warehouses.items():
            if alias == text:
                return warehouse
        
        if text in self._warehouse_search_index:
            return self._warehouse_search_index[text]
        
        words = text.split()
        for word in words:
            if len(word) >= 2:
                pattern = re.compile(rf'\b{re.escape(word)}\b')
                for alias, warehouse in self.warehouses.items():
                    if pattern.search(alias):
                        logger.debug(f"Warehouse resolved (word boundary): {warehouse} from '{text}'")
                        return warehouse
        
        for alias, warehouse in self.warehouses.items():
            if alias in text or text in alias:
                logger.debug(f"Warehouse resolved (fuzzy): {warehouse} from '{text}'")
                return warehouse
        
        best_match, confidence = self._fuzzy_match(text, self._warehouse_list, threshold=0.80)
        if best_match and confidence >= 0.80:
            logger.debug(f"Warehouse resolved (fuzzy sequence): {best_match} (confidence: {confidence:.2f})")
            return best_match
        
        return None
    
    def find_warehouse_debug(self, name: str) -> Dict[str, Any]:
        result = {"input": name, "resolved": None, "method": "none", "confidence": 0.0}
        if not name:
            return result
        resolved = self.resolve_warehouse(name)
        if resolved:
            result["resolved"] = resolved
            result["method"] = "warehouse_resolution"
            result["confidence"] = 0.90
        return result
    
    # ==========================================================
    # UNIFIED ENTITY DEBUG
    # ==========================================================
    
    def find_entity_debug(self, name: str) -> Dict[str, Any]:
        result = {
            "input": name,
            "dealer": self.find_dealer_debug(name),
            "city": self.find_city_debug(name),
            "warehouse": self.find_warehouse_debug(name),
            "unified": self.resolve_entity(name)
        }
        return result
    
    def get_all_entities(self, entity_type: str = "all") -> Dict[str, Any]:
        result = {}
        if entity_type in ["dealers", "all"]:
            result["dealers"] = list(self.dealers.values())
        if entity_type in ["cities", "all"]:
            result["cities"] = list(self.cities.values())
        if entity_type in ["warehouses", "all"]:
            result["warehouses"] = list(self.warehouses.values())
        return result
    
    def search_entities(self, query: str) -> Dict[str, Any]:
        query_lower = query.lower().strip()
        result = {
            "query": query,
            "matching_dealers": [],
            "matching_cities": [],
            "matching_warehouses": []
        }
        
        for name in self.dealers.values():
            if query_lower in name.lower():
                result["matching_dealers"].append(name)
        
        for name in self.cities.values():
            if query_lower in name.lower():
                result["matching_cities"].append(name)
        
        for name in self.warehouses.values():
            if query_lower in name.lower():
                result["matching_warehouses"].append(name)
        
        return result
    
    # ==========================================================
    # METADATA REPORTS
    # ==========================================================
    
    def get_metadata_stats(self) -> Dict[str, Any]:
        return {
            "dealers": len(self.dealers),
            "cities": len(self.cities),
            "warehouses": len(self.warehouses),
            "intents": len(self.intents),
            "metrics": len(self.metrics),
            "health_score": self._health_score,
            "initialized": self._initialized,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None
        }
    
    def generate_metadata_report(self) -> Dict[str, Any]:
        return {
            "status": "healthy" if self._health_score >= 70 else "warning" if self._health_score >= 50 else "critical",
            "health_score": self._health_score,
            "initialized": self._initialized,
            "database_connected": self._db_connected,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "load_error": self._load_error,
            "counts": {
                "dealers": len(self.dealers),
                "cities": len(self.cities),
                "warehouses": len(self.warehouses),
                "intents": len(self.intents),
                "metrics": len(self.metrics),
                "logistics_keywords": len(self.logistics_keywords),
                "business_rules": len(self.rules),
                "status_definitions": len(self.statuses)
            },
            "search_index_sizes": {
                "dealers": len(self._dealer_search_index),
                "cities": len(self._city_search_index),
                "warehouses": len(self._warehouse_search_index)
            },
            "stats": self._stats,
            "sample_data": {
                "dealers": list(self.dealers.values())[:5],
                "cities": list(self.cities.values())[:5],
                "warehouses": list(self.warehouses.values())[:5]
            },
            "generated_at": datetime.now().isoformat()
        }
    
    # ==========================================================
    # VALIDATION
    # ==========================================================
    
    def _validate_or_raise(self):
        if len(self.dealers) == 0:
            raise RuntimeError(
                "No dealers loaded from database. "
                "Check column names in delivery_report table."
            )
    
    def _log_warnings(self):
        if len(self.dealers) < 10:
            logger.warning(f"⚠️ Dealer count low: {len(self.dealers)} - check data import")
        if len(self.cities) < 5:
            logger.warning(f"⚠️ City count low: {len(self.cities)} - check data import")
        if len(self.warehouses) < 3:
            logger.warning(f"⚠️ Warehouse count low: {len(self.warehouses)} - check data import")
    
    def _calculate_health_score(self) -> int:
        score = 100
        if len(self.dealers) == 0:
            score -= 40
        elif len(self.dealers) < 10:
            score -= 20
        elif len(self.dealers) < 50:
            score -= 10
        if len(self.cities) == 0:
            score -= 30
        elif len(self.cities) < 5:
            score -= 15
        if len(self.warehouses) == 0:
            score -= 30
        elif len(self.warehouses) < 3:
            score -= 15
        return max(0, min(100, score))
    
    # ==========================================================
    # INTENT & METRIC DETECTION
    # ==========================================================
    
    def detect_intent(self, text: str) -> Tuple[Optional[str], float]:
        if not text:
            return None, 0.0
        
        text = text.lower().strip()
        
        if DN_PATTERN.search(text):
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
            return best_intent, confidence
        
        return None, 0.0
    
    def detect_metric(self, text: str) -> Optional[str]:
        if not text:
            return None
        text = text.lower().strip()
        for metric, keywords in self.metrics.items():
            for keyword in keywords:
                if keyword in text:
                    return metric
        return None
    
    def is_logistics_keyword(self, text: str) -> bool:
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
        if not text:
            return False
        return bool(DN_PATTERN.match(text.strip()))
    
    def extract_dn_number(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = DN_PATTERN.search(text)
        return match.group(1) if match else None
    
    # ==========================================================
    # DELIVERY METRICS
    # ==========================================================
    
    def calculate_delivery_metrics(
        self,
        dn_date: Optional[datetime],
        pgi_date: Optional[datetime],
        pod_date: Optional[datetime]
    ) -> Dict[str, Any]:
        return self.delivery_metrics.validate_dates(dn_date, pgi_date, pod_date)
    
    def get_delivery_metrics_definition(self) -> Dict[str, Any]:
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
        if score < 50:
            return "critical"
        elif score < 70:
            return "high"
        elif score < 85:
            return "medium"
        return "low"
    
    def get_risk_emoji(self, status: str) -> str:
        emojis = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        return emojis.get(status, "⚪")
    
    def get_dn_status(self, status_key: str) -> str:
        return self.statuses.get("dn_status", {}).get(status_key, "❓ Unknown")
    
    def get_rule(self, rule_name: str) -> Optional[Any]:
        return self.rules.get(rule_name)
    
    def get_data_quality_status(self, validation_result: Dict[str, Any]) -> str:
        if not validation_result.get("is_valid", False):
            return "error"
        elif validation_result.get("issues", []):
            return "warning"
        return "valid"
    
    # ==========================================================
    # HEALTH & DIAGNOSTIC REPORTS
    # ==========================================================
    
    def get_health_report(self) -> Dict[str, Any]:
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
    
    def get_diagnostic_report(self) -> Dict[str, Any]:
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
    """Thread-safe singleton getter for SchemaService."""
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
    service = get_schema_service()
    return service.refresh_metadata()


def get_schema_health() -> Dict[str, Any]:
    service = get_schema_service()
    return service.get_health_report()


def get_schema_diagnostics() -> Dict[str, Any]:
    service = get_schema_service()
    return service.get_diagnostic_report()


def generate_metadata_report() -> Dict[str, Any]:
    service = get_schema_service()
    return service.generate_metadata_report()


def get_metadata_stats() -> Dict[str, Any]:
    service = get_schema_service()
    return service.get_metadata_stats()


def is_dn_number(text: str) -> bool:
    service = get_schema_service()
    return service.is_dn_number(text)


def extract_dn_number(text: str) -> Optional[str]:
    service = get_schema_service()
    return service.extract_dn_number(text)


def calculate_delivery_metrics(
    dn_date: Optional[datetime],
    pgi_date: Optional[datetime],
    pod_date: Optional[datetime]
) -> Dict[str, Any]:
    service = get_schema_service()
    return service.calculate_delivery_metrics(dn_date, pgi_date, pod_date)


def resolve_entity(text: str) -> Dict[str, Any]:
    service = get_schema_service()
    return service.resolve_entity(text)


def find_dealer_debug(name: str) -> Dict[str, Any]:
    service = get_schema_service()
    return service.find_dealer_debug(name)


def find_city_debug(name: str) -> Dict[str, Any]:
    service = get_schema_service()
    return service.find_city_debug(name)


def find_warehouse_debug(name: str) -> Dict[str, Any]:
    service = get_schema_service()
    return service.find_warehouse_debug(name)


def find_entity_debug(name: str) -> Dict[str, Any]:
    service = get_schema_service()
    return service.find_entity_debug(name)


def get_all_entities(entity_type: str = "all") -> Dict[str, Any]:
    service = get_schema_service()
    return service.get_all_entities(entity_type)


def search_entities(query: str) -> Dict[str, Any]:
    service = get_schema_service()
    return service.search_entities(query)


def get_sample_dealers(limit: int = 10) -> List[Dict[str, str]]:
    service = get_schema_service()
    return service.get_sample_dealers(limit)


def get_dealer_count() -> int:
    service = get_schema_service()
    return service.get_dealer_count()


# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'SchemaService',
    'DeliveryMetrics',
    'DeliveryRepository',
    'get_schema_service',
    'refresh_schema_metadata',
    'get_schema_health',
    'get_schema_diagnostics',
    'generate_metadata_report',
    'get_metadata_stats',
    'resolve_entity',
    'find_dealer_debug',
    'find_city_debug',
    'find_warehouse_debug',
    'find_entity_debug',
    'get_all_entities',
    'search_entities',
    'get_sample_dealers',
    'get_dealer_count',
    'is_dn_number',
    'extract_dn_number',
    'calculate_delivery_metrics',
    'DN_PATTERN',
    'INTENT_KEYWORDS',
    'METRIC_KEYWORDS',
    'LOGISTICS_KEYWORDS',
    'BUSINESS_RULES',
    'STATUS_DEFINITIONS',
]
