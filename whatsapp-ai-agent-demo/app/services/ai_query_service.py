# ==========================================================
# FILE: app/services/ai_query_service.py
# ==========================================================
# PURPOSE: Natural Language Intelligence Engine
#          Converts Human Questions → Structured Query Plans
#
# WHAT THIS FILE DOES:
# ✅ Understand User Question
# ✅ Extract Meaning
# ✅ Extract Business Intent
# ✅ Extract Entities
# ✅ Extract Metrics
# ✅ Extract Date Ranges
# ✅ Extract Filters
# ✅ Extract Ranking Requirements
# ✅ Extract Comparison Requirements
# ✅ Create Query Plan
#
# WHAT THIS FILE NEVER DOES:
# ✗ SQL Queries
# ✗ Database Access
# ✗ KPI Calculations
# ✗ Revenue Calculations
# ✗ POD/PGI Calculations
# ✗ WhatsApp Sending
# ✗ Response Formatting
# ✗ Dashboard Formatting
# ✗ Data Aggregation
# ✗ Trend Calculations
# ==========================================================

import re
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from loguru import logger

# Optional GROQ for complex queries
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

from app.config import config


# ==========================================================
# QUERY PLAN DATA CLASS
# ==========================================================

@dataclass
class QueryPlan:
    """The main output of this service - contract for entire application"""
    
    # Core intent
    intent: str = "unknown"  # dealer_dashboard, warehouse_dashboard, ranking, comparison, etc.
    
    # Entity information
    entity_type: Optional[str] = None  # dealer, warehouse, city, product, division, sales_manager, dn
    entity_value: Optional[str] = None  # "Dubai Electronics", "Sargodha", etc.
    
    # Metrics
    metric: Optional[str] = None  # revenue, units, dn_count, pod_aging, delivery_aging, etc.
    dimension: Optional[str] = None  # dealer, warehouse, city, product, division
    
    # Time filters
    date_range: Optional[Dict[str, str]] = None  # {"start_date": "2026-06-01", "end_date": "2026-06-30"}
    
    # Additional filters
    filters: Dict[str, Any] = field(default_factory=dict)  # {"city": "lahore", "status": "pending"}
    
    # Ranking
    ranking_type: Optional[str] = None  # top, bottom, best, worst
    limit: Optional[int] = None  # 5, 10, 20
    sort_order: Optional[str] = None  # asc, desc
    sort_by: Optional[str] = None  # revenue, units, aging
    
    # Comparison
    comparison_entities: Optional[Dict[str, str]] = None  # {"left": "lahore", "right": "karachi"}
    
    # Dashboard type
    dashboard_type: Optional[str] = None  # dealer, warehouse, city, product, division, executive
    
    # Control tower
    control_tower_type: Optional[str] = None  # critical_deliveries, critical_pod, worst_dealer, worst_warehouse
    
    # Trend
    trend_period: Optional[str] = None  # daily, weekly, monthly, quarterly, yearly
    trend_metric: Optional[str] = None  # revenue, units, pod, pgi
    
    # Root cause
    root_cause_target: Optional[str] = None  # "lahore", "pod_aging", etc.
    
    # Confidence & routing
    confidence_score: float = 0.0
    requires_groq: bool = False
    requires_kpi: bool = False
    requires_analytics: bool = False
    requires_control_tower: bool = False
    requires_trend_analysis: bool = False
    requires_root_cause: bool = False
    
    # Raw original message
    original_message: str = ""
    normalized_message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert QueryPlan to dictionary for serialization"""
        return {
            "intent": self.intent,
            "entity_type": self.entity_type,
            "entity_value": self.entity_value,
            "metric": self.metric,
            "dimension": self.dimension,
            "date_range": self.date_range,
            "filters": self.filters,
            "ranking_type": self.ranking_type,
            "limit": self.limit,
            "sort_order": self.sort_order,
            "sort_by": self.sort_by,
            "comparison_entities": self.comparison_entities,
            "dashboard_type": self.dashboard_type,
            "control_tower_type": self.control_tower_type,
            "trend_period": self.trend_period,
            "trend_metric": self.trend_metric,
            "root_cause_target": self.root_cause_target,
            "confidence_score": self.confidence_score,
            "requires_groq": self.requires_groq,
            "requires_kpi": self.requires_kpi,
            "requires_analytics": self.requires_analytics,
            "requires_control_tower": self.requires_control_tower,
            "requires_trend_analysis": self.requires_trend_analysis,
            "requires_root_cause": self.requires_root_cause
        }


# ==========================================================
# INTENT TYPES
# ==========================================================

class IntentType:
    DEALER_DASHBOARD = "dealer_dashboard"
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    CITY_DASHBOARD = "city_dashboard"
    PRODUCT_DASHBOARD = "product_dashboard"
    DIVISION_DASHBOARD = "division_dashboard"
    SALES_MANAGER_DASHBOARD = "sales_manager_dashboard"
    DN_LOOKUP = "dn_lookup"
    DN_STATUS = "dn_status"
    POD_ANALYSIS = "pod_analysis"
    PGI_ANALYSIS = "pgi_analysis"
    DELIVERY_ANALYSIS = "delivery_analysis"
    KPI_REPORT = "kpi_report"
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    CONTROL_TOWER = "control_tower"
    RANKING = "ranking"
    COMPARISON = "comparison"
    TREND = "trend"
    ROOT_CAUSE = "root_cause"
    HELP = "help"
    UNKNOWN = "unknown"


# ==========================================================
# METRIC TYPES
# ==========================================================

class MetricType:
    REVENUE = "revenue"
    UNITS = "units"
    DN_COUNT = "dn_count"
    POD_COUNT = "pod_count"
    PGI_COUNT = "pgi_count"
    PENDING_POD = "pending_pod"
    PENDING_DELIVERY = "pending_delivery"
    DELIVERY_AGING = "delivery_aging"
    POD_AGING = "pod_aging"
    FULL_CYCLE = "full_cycle"
    POD_RATE = "pod_rate"
    PGI_RATE = "pgi_rate"
    DELIVERY_RATE = "delivery_rate"


# ==========================================================
# ENTITY PATTERNS
# ==========================================================

class EntityPatterns:
    """Common entity names for detection"""
    
    WAREHOUSES = {
        'rawalpindi', 'lahore', 'karachi', 'islamabad', 'multan',
        'faisalabad', 'gujranwala', 'sargodha', 'attock', 'sialkot'
    }
    
    DIVISIONS = {
        'refrigerator': 'REF', 'fridge': 'REF', 'freezer': 'FRZ',
        'tv': 'TV', 'television': 'TV',
        'cooking': 'COOK', 'oven': 'COOK', 'microwave': 'COOK',
        'commercial ac': 'CAC', 'ac': 'CAC', 'air conditioner': 'CAC',
        'water systems': 'WS', 'water': 'WS'
    }
    
    CITIES = {
        'lahore', 'karachi', 'islamabad', 'rawalpindi', 'multan',
        'faisalabad', 'gujranwala', 'sialkot', 'attock', 'sargodha'
    }


# ==========================================================
# AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    """
    Natural Language Intelligence Engine
    Converts human questions into structured query plans
    """
    
    def __init__(self):
        """Initialize the AI Query Service"""
        self.groq_client = None
        self._init_groq()
        logger.info("AI Query Service initialized")
    
    def _init_groq(self):
        """Initialize GROQ client for complex queries"""
        if GROQ_AVAILABLE and config.GROQ_API_KEY:
            try:
                self.groq_client = Groq(api_key=config.GROQ_API_KEY)
                logger.info("GROQ client initialized for AI Query Service")
            except Exception as e:
                logger.error(f"GROQ initialization failed: {e}")
    
    # ==========================================================
    # 1. PROCESS QUERY - Master Entry Point
    # ==========================================================
    
    async def process_query(self, user_message: str) -> QueryPlan:
        """
        Master entry point for query understanding
        
        Input: "Top 5 dealers by pending POD aging in Lahore this month"
        Output: QueryPlan object
        
        Responsibilities:
        - Normalize Text
        - Detect Intent
        - Extract Entities
        - Extract Metrics
        - Extract Dates
        - Extract Filters
        - Extract Ranking
        - Build Query Plan
        - Return Query Plan
        """
        logger.info(f"Processing query: {user_message[:100]}")
        
        # Step 1: Normalize query
        normalized = self.normalize_query(user_message)
        
        # Step 2: Detect intent
        intent = self.detect_intent(normalized)
        
        # Step 3: Extract entities
        entities = self.extract_entities(normalized, user_message)
        
        # Step 4: Extract metrics
        metric = self.extract_metrics(normalized)
        
        # Step 5: Extract date range
        date_range = self.extract_date_range(normalized)
        
        # Step 6: Extract filters
        filters = self.extract_filters(normalized, entities)
        
        # Step 7: Extract ranking
        ranking = self.extract_ranking(normalized)
        
        # Step 8: Extract comparison
        comparison = self.extract_comparison(normalized)
        
        # Step 9: Detect dashboard type
        dashboard_type = self.detect_dashboard_type(intent, entities)
        
        # Step 10: Build query plan
        query_plan = self.build_query_plan(
            intent=intent,
            entities=entities,
            metric=metric,
            date_range=date_range,
            filters=filters,
            ranking=ranking,
            comparison=comparison,
            dashboard_type=dashboard_type,
            normalized=normalized,
            original=user_message
        )
        
        # Step 11: Calculate confidence score
        query_plan.confidence_score = self.calculate_confidence_score(query_plan)
        
        # Step 12: Validate query plan
        is_valid = self.validate_query_plan(query_plan)
        
        if not is_valid:
            logger.warning(f"Query plan validation failed: {query_plan}")
            query_plan.confidence_score = 0.3
        
        # Step 13: Determine if GROQ is needed
        if query_plan.confidence_score < 0.6 or query_plan.intent == IntentType.UNKNOWN:
            query_plan.requires_groq = True
        
        logger.info(f"Query plan created: intent={query_plan.intent}, confidence={query_plan.confidence_score}")
        
        return query_plan
    
    # ==========================================================
    # 2. NORMALIZE QUERY
    # ==========================================================
    
    def normalize_query(self, message: str) -> str:
        """
        Clean input text
        
        Input: "Top 5 DEALERS By Revenue in Lahore!!!"
        Output: "top 5 dealers by revenue in lahore"
        
        Tasks:
        - Lowercase
        - Trim Spaces
        - Remove Extra Spaces
        - Remove Noise Characters
        - Fix Common Typos
        """
        if not message:
            return ""
        
        # Lowercase
        normalized = message.lower()
        
        # Remove extra spaces
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Remove noise characters (keep letters, numbers, spaces, basic punctuation)
        normalized = re.sub(r'[^\w\s\-&]', '', normalized)
        
        # Trim
        normalized = normalized.strip()
        
        # Fix common typos
        typo_fixes = {
            'warehoue': 'warehouse',
            'warehous': 'warehouse',
            'delears': 'dealers',
            'delear': 'dealer',
            'revinue': 'revenue',
            'revenuee': 'revenue',
            'untis': 'units',
            'unitss': 'units',
            'dns': 'dns',
            'dnn': 'dn',
            'pgi': 'pgi',
            'pod': 'pod',
        }
        
        for typo, fix in typo_fixes.items():
            normalized = normalized.replace(typo, fix)
        
        return normalized
    
    # ==========================================================
    # 3. DETECT INTENT
    # ==========================================================
    
    def detect_intent(self, normalized: str) -> str:
        """
        Understand what user wants
        
        Supported Intents:
        - DEALER_DASHBOARD
        - WAREHOUSE_DASHBOARD
        - CITY_DASHBOARD
        - PRODUCT_DASHBOARD
        - DIVISION_DASHBOARD
        - SALES_MANAGER_DASHBOARD
        - DN_LOOKUP
        - KPI_REPORT
        - EXECUTIVE_DASHBOARD
        - CONTROL_TOWER
        - RANKING
        - COMPARISON
        - TREND
        - ROOT_CAUSE
        - HELP
        """
        
        # Help intent
        if any(word in normalized for word in ['help', 'menu', 'commands', 'what can you do']):
            return IntentType.HELP
        
        # Control Tower
        if any(phrase in normalized for phrase in [
            'critical deliveries', 'critical pod', 'worst dealer', 'worst warehouse',
            'control tower', 'alerts', 'stuck', 'delayed'
        ]):
            return IntentType.CONTROL_TOWER
        
        # Root Cause
        if normalized.startswith('why') and any(word in normalized for word in ['delay', 'aging', 'underperforming']):
            return IntentType.ROOT_CAUSE
        
        # Trend
        if any(word in normalized for word in ['trend', 'trends']) or any(
            phrase in normalized for phrase in ['revenue trend', 'pod trend', 'delivery trend']
        ):
            return IntentType.TREND
        
        # Comparison
        if any(word in normalized for word in ['compare', 'vs', 'versus']) or ' vs ' in normalized:
            return IntentType.COMPARISON
        
        # Ranking
        if any(word in normalized for word in ['top', 'bottom', 'best', 'worst', 'highest', 'lowest']):
            return IntentType.RANKING
        
        # DN Lookup
        dn_match = re.search(r'\b(\d{8,12})\b', normalized)
        if dn_match:
            if 'status' in normalized:
                return IntentType.DN_STATUS
            return IntentType.DN_LOOKUP
        
        # Executive Dashboard
        if any(phrase in normalized for phrase in [
            'executive dashboard', 'ceo dashboard', 'business summary',
            'company kpi', 'overall performance'
        ]):
            return IntentType.EXECUTIVE_DASHBOARD
        
        # KPI Report
        if any(word in normalized for word in ['kpi', 'metrics', 'performance', 'dashboard']):
            return IntentType.KPI_REPORT
        
        # Division Dashboard
        for division in EntityPatterns.DIVISIONS.keys():
            if division in normalized:
                return IntentType.DIVISION_DASHBOARD
        
        # Warehouse Dashboard
        for warehouse in EntityPatterns.WAREHOUSES:
            if warehouse in normalized and ('warehouse' in normalized or len(normalized.split()) <= 3):
                return IntentType.WAREHOUSE_DASHBOARD
        
        # City Dashboard
        for city in EntityPatterns.CITIES:
            if city in normalized and ('city' in normalized or 'in' in normalized):
                return IntentType.CITY_DASHBOARD
        
        # Product Dashboard
        product_match = re.search(r'([A-Z0-9-]{5,20})', normalized.upper())
        if product_match:
            return IntentType.PRODUCT_DASHBOARD
        
        # Dealer Dashboard (default if short message)
        if len(normalized.split()) <= 5:
            return IntentType.DEALER_DASHBOARD
        
        return IntentType.UNKNOWN
    
    # ==========================================================
    # 4. EXTRACT ENTITIES
    # ==========================================================
    
    def extract_entities(self, normalized: str, original: str) -> Dict[str, Any]:
        """
        Identify business objects
        
        Entity Types:
        - Dealer (dealer_name, customer_name)
        - Warehouse (warehouse_name)
        - City (city)
        - Product (product_code, material_no)
        - Division
        - Sales Manager
        - DN (dn_number)
        """
        entities = {}
        
        # Extract DN
        dn_match = re.search(r'\b(\d{8,12})\b', normalized)
        if dn_match:
            entities['dn_number'] = dn_match.group(1)
            return entities
        
        # Extract Warehouse
        for warehouse in EntityPatterns.WAREHOUSES:
            if warehouse in normalized:
                entities['warehouse'] = warehouse.title()
                entities['warehouse_name'] = warehouse.title()
                break
        
        # Extract City
        for city in EntityPatterns.CITIES:
            if city in normalized:
                entities['city'] = city.title()
                entities['city_name'] = city.title()
                break
        
        # Extract Division
        for division_name, division_code in EntityPatterns.DIVISIONS.items():
            if division_name in normalized:
                entities['division'] = division_code
                entities['division_name'] = division_name.title()
                break
        
        # Extract Product
        product_match = re.search(r'([A-Z0-9-]{5,20})', normalized.upper())
        if product_match:
            entities['product_code'] = product_match.group(1)
            entities['product'] = product_match.group(1)
        
        # Extract Sales Manager
        manager_match = re.search(r'(?:sales manager|manager|sm)\s+([a-z\s]{2,30})', normalized)
        if manager_match:
            entities['sales_manager'] = manager_match.group(1).strip().title()
        
        # Extract Dealer (with & symbol handling)
        if '&' in original:
            dealer_match = re.search(r'([A-Za-z\s&]+(?:&[A-Za-z\s]+)+)', original)
            if dealer_match:
                entities['dealer'] = dealer_match.group(1).strip()
                entities['dealer_name'] = dealer_match.group(1).strip()
        elif len(normalized.split()) <= 5 and not entities:
            # Short message without other entities - likely a dealer name
            entities['dealer'] = original.strip()
            entities['dealer_name'] = original.strip()
        
        return entities
    
    # ==========================================================
    # 5. EXTRACT METRICS
    # ==========================================================
    
    def extract_metrics(self, normalized: str) -> Optional[str]:
        """
        Determine what KPI is requested
        
        Supported Metrics:
        - REVENUE, UNITS, DN_COUNT
        - POD_COUNT, PGI_COUNT
        - PENDING_POD, PENDING_DELIVERY
        - DELIVERY_AGING, POD_AGING, FULL_CYCLE
        - POD_RATE, PGI_RATE, DELIVERY_RATE
        """
        
        metric_map = {
            # Revenue metrics
            MetricType.REVENUE: ['revenue', 'sales', 'amount', 'value'],
            
            # Volume metrics
            MetricType.UNITS: ['units', 'quantity', 'qty', 'pieces'],
            MetricType.DN_COUNT: ['dns', 'delivery notes', 'orders', 'deliveries'],
            
            # Count metrics
            MetricType.POD_COUNT: ['pod count', 'pod done', 'pod completed'],
            MetricType.PGI_COUNT: ['pgi count', 'pgi done', 'pgi completed'],
            
            # Pending metrics
            MetricType.PENDING_POD: ['pending pod', 'pod pending', 'pod not done'],
            MetricType.PENDING_DELIVERY: ['pending delivery', 'delivery pending', 'not delivered'],
            
            # Aging metrics
            MetricType.DELIVERY_AGING: ['delivery aging', 'pgi aging', 'delivery delay'],
            MetricType.POD_AGING: ['pod aging', 'pod delay', 'pod latency'],
            MetricType.FULL_CYCLE: ['full cycle', 'total cycle', 'end to end'],
            
            # Rate metrics
            MetricType.POD_RATE: ['pod rate', 'pod percentage', 'pod %'],
            MetricType.PGI_RATE: ['pgi rate', 'pgi percentage', 'pgi %'],
            MetricType.DELIVERY_RATE: ['delivery rate', 'delivery percentage', 'delivery %'],
        }
        
        for metric, keywords in metric_map.items():
            if any(keyword in normalized for keyword in keywords):
                return metric
        
        return None
    
    # ==========================================================
    # 6. EXTRACT DATE RANGE
    # ==========================================================
    
    def extract_date_range(self, normalized: str) -> Optional[Dict[str, str]]:
        """
        Convert human dates into SQL dates
        
        Must Understand:
        today, yesterday, this_week, last_week, this_month, last_month,
        this_quarter, last_quarter, this_year, last_year,
        last_7_days, last_15_days, last_30_days,
        Q1, Q2, Q3, Q4, May 2026, June 2026
        """
        today = date.today()
        
        # Today
        if 'today' in normalized:
            return {
                'start_date': today.isoformat(),
                'end_date': today.isoformat()
            }
        
        # Yesterday
        if 'yesterday' in normalized:
            yesterday = today - timedelta(days=1)
            return {
                'start_date': yesterday.isoformat(),
                'end_date': yesterday.isoformat()
            }
        
        # Last 7 days
        if 'last 7 days' in normalized or 'last 7 days' in normalized:
            start = today - timedelta(days=7)
            return {
                'start_date': start.isoformat(),
                'end_date': today.isoformat()
            }
        
        # Last 15 days
        if 'last 15 days' in normalized:
            start = today - timedelta(days=15)
            return {
                'start_date': start.isoformat(),
                'end_date': today.isoformat()
            }
        
        # Last 30 days
        if 'last 30 days' in normalized or 'last month' in normalized:
            start = today - timedelta(days=30)
            return {
                'start_date': start.isoformat(),
                'end_date': today.isoformat()
            }
        
        # This week
        if 'this week' in normalized:
            start = today - timedelta(days=today.weekday())
            return {
                'start_date': start.isoformat(),
                'end_date': today.isoformat()
            }
        
        # Last week
        if 'last week' in normalized:
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
            return {
                'start_date': start.isoformat(),
                'end_date': end.isoformat()
            }
        
        # This month
        if 'this month' in normalized:
            start = today.replace(day=1)
            return {
                'start_date': start.isoformat(),
                'end_date': today.isoformat()
            }
        
        # Last month
        if 'last month' in normalized:
            first_of_this_month = today.replace(day=1)
            end = first_of_this_month - timedelta(days=1)
            start = end.replace(day=1)
            return {
                'start_date': start.isoformat(),
                'end_date': end.isoformat()
            }
        
        # Quarter detection
        quarter_match = re.search(r'q([1-4])', normalized)
        if quarter_match:
            quarter = int(quarter_match.group(1))
            year = today.year
            quarter_starts = {1: (year, 1, 1), 2: (year, 4, 1), 3: (year, 7, 1), 4: (year, 10, 1)}
            quarter_ends = {1: (year, 3, 31), 2: (year, 6, 30), 3: (year, 9, 30), 4: (year, 12, 31)}
            start = date(*quarter_starts[quarter])
            end = date(*quarter_ends[quarter])
            return {
                'start_date': start.isoformat(),
                'end_date': end.isoformat()
            }
        
        # Specific month/year
        month_match = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})', normalized)
        if month_match:
            month_name = month_match.group(1)
            year = int(month_match.group(2))
            month_num = {
                'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
                'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
            }[month_name]
            start = date(year, month_num, 1)
            if month_num == 12:
                end = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(year, month_num + 1, 1) - timedelta(days=1)
            return {
                'start_date': start.isoformat(),
                'end_date': end.isoformat()
            }
        
        # This year
        if 'this year' in normalized or 'ytd' in normalized:
            start = date(today.year, 1, 1)
            return {
                'start_date': start.isoformat(),
                'end_date': today.isoformat()
            }
        
        return None
    
    # ==========================================================
    # 7. EXTRACT RANKING
    # ==========================================================
    
    def extract_ranking(self, normalized: str) -> Dict[str, Any]:
        """
        Identify ranking requests
        
        Keywords: top, bottom, best, worst, highest, lowest
        """
        ranking = {}
        
        # Detect ranking type
        if any(word in normalized for word in ['top', 'best', 'highest']):
            ranking['ranking_type'] = 'top'
        elif any(word in normalized for word in ['bottom', 'worst', 'lowest']):
            ranking['ranking_type'] = 'bottom'
        
        # Extract limit
        limit_match = re.search(r'(?:top|bottom|best|worst)\s+(\d+)', normalized)
        if limit_match:
            ranking['limit'] = int(limit_match.group(1))
        else:
            ranking['limit'] = 10  # Default
        
        # Extract sort order
        if 'desc' in normalized or 'highest' in normalized or 'top' in normalized:
            ranking['sort_order'] = 'desc'
        elif 'asc' in normalized or 'lowest' in normalized or 'bottom' in normalized:
            ranking['sort_order'] = 'asc'
        else:
            ranking['sort_order'] = 'desc'
        
        # Extract sort by metric
        if 'revenue' in normalized or 'sales' in normalized:
            ranking['sort_by'] = 'revenue'
        elif 'units' in normalized or 'quantity' in normalized:
            ranking['sort_by'] = 'units'
        elif 'aging' in normalized:
            ranking['sort_by'] = 'aging'
        elif 'pod' in normalized:
            ranking['sort_by'] = 'pod_aging'
        
        return ranking
    
    # ==========================================================
    # 8. EXTRACT COMPARISON
    # ==========================================================
    
    def extract_comparison(self, normalized: str) -> Optional[Dict[str, str]]:
        """
        Identify comparison requests
        
        Examples: "Compare Lahore vs Karachi", "Compare Refrigerator vs TV"
        """
        # Pattern: compare X vs Y
        pattern1 = r'compare\s+([a-z\s]+?)\s+vs\s+([a-z\s]+?)(?:$|\.|\s+for|\s+in)'
        match1 = re.search(pattern1, normalized)
        if match1:
            return {
                'left': match1.group(1).strip(),
                'right': match1.group(2).strip()
            }
        
        # Pattern: X vs Y
        pattern2 = r'([a-z\s]+?)\s+vs\s+([a-z\s]+?)(?:$|\.|\s+for|\s+in)'
        match2 = re.search(pattern2, normalized)
        if match2:
            return {
                'left': match2.group(1).strip(),
                'right': match2.group(2).strip()
            }
        
        return None
    
    # ==========================================================
    # 9. EXTRACT FILTERS
    # ==========================================================
    
    def extract_filters(self, normalized: str, entities: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract business filters
        
        Example: "Top 5 dealers by revenue in Lahore this month"
        Output: {"city": "lahore", "date": "this_month"}
        """
        filters = {}
        
        # Add entities as filters
        if entities.get('city'):
            filters['city'] = entities['city']
        
        if entities.get('warehouse'):
            filters['warehouse'] = entities['warehouse']
        
        if entities.get('division'):
            filters['division'] = entities['division']
        
        # Status filter
        if 'pending' in normalized:
            filters['status'] = 'pending'
        elif 'delivered' in normalized:
            filters['status'] = 'delivered'
        
        # Location filter (in X city)
        location_match = re.search(r'in\s+([a-z]+)', normalized)
        if location_match and location_match.group(1) in EntityPatterns.CITIES:
            filters['city'] = location_match.group(1).title()
        
        return filters
    
    # ==========================================================
    # 10. DETECT DASHBOARD TYPE
    # ==========================================================
    
    def detect_dashboard_type(self, intent: str, entities: Dict[str, Any]) -> Optional[str]:
        """
        Identify dashboard request
        
        Possible Dashboards:
        - dealer_dashboard
        - warehouse_dashboard
        - city_dashboard
        - product_dashboard
        - division_dashboard
        - sales_manager_dashboard
        - executive_dashboard
        """
        
        if intent == IntentType.DEALER_DASHBOARD:
            return "dealer_dashboard"
        elif intent == IntentType.WAREHOUSE_DASHBOARD:
            return "warehouse_dashboard"
        elif intent == IntentType.CITY_DASHBOARD:
            return "city_dashboard"
        elif intent == IntentType.PRODUCT_DASHBOARD:
            return "product_dashboard"
        elif intent == IntentType.DIVISION_DASHBOARD:
            return "division_dashboard"
        elif intent == IntentType.SALES_MANAGER_DASHBOARD:
            return "sales_manager_dashboard"
        elif intent == IntentType.EXECUTIVE_DASHBOARD:
            return "executive_dashboard"
        elif intent == IntentType.KPI_REPORT:
            return "kpi_dashboard"
        
        # Infer from entities
        if entities.get('dealer'):
            return "dealer_dashboard"
        if entities.get('warehouse'):
            return "warehouse_dashboard"
        if entities.get('city'):
            return "city_dashboard"
        if entities.get('division'):
            return "division_dashboard"
        
        return None
    
    # ==========================================================
    # 11. BUILD QUERY PLAN
    # ==========================================================
    
    def build_query_plan(
        self,
        intent: str,
        entities: Dict[str, Any],
        metric: Optional[str],
        date_range: Optional[Dict[str, str]],
        filters: Dict[str, Any],
        ranking: Dict[str, Any],
        comparison: Optional[Dict[str, str]],
        dashboard_type: Optional[str],
        normalized: str,
        original: str
    ) -> QueryPlan:
        """
        Build the complete QueryPlan object
        
        Combines all extracted information into a single plan
        """
        
        # Determine entity type and value
        entity_type = None
        entity_value = None
        
        if entities.get('dealer'):
            entity_type = 'dealer'
            entity_value = entities['dealer']
        elif entities.get('warehouse'):
            entity_type = 'warehouse'
            entity_value = entities['warehouse']
        elif entities.get('city'):
            entity_type = 'city'
            entity_value = entities['city']
        elif entities.get('division'):
            entity_type = 'division'
            entity_value = entities['division']
        elif entities.get('product_code'):
            entity_type = 'product'
            entity_value = entities['product_code']
        elif entities.get('dn_number'):
            entity_type = 'dn'
            entity_value = entities['dn_number']
        
        # Determine dimension
        dimension = None
        if intent == IntentType.RANKING:
            if 'dealer' in normalized or 'dealer' in filters:
                dimension = 'dealer'
            elif 'warehouse' in normalized:
                dimension = 'warehouse'
            elif 'city' in normalized:
                dimension = 'city'
            elif 'product' in normalized:
                dimension = 'product'
        
        # Set routing flags
        requires_kpi = intent in [
            IntentType.KPI_REPORT, IntentType.EXECUTIVE_DASHBOARD,
            IntentType.POD_ANALYSIS, IntentType.PGI_ANALYSIS, IntentType.DELIVERY_ANALYSIS
        ]
        
        requires_analytics = intent in [
            IntentType.RANKING, IntentType.COMPARISON, IntentType.TREND, IntentType.CONTROL_TOWER
        ]
        
        requires_control_tower = intent == IntentType.CONTROL_TOWER
        requires_trend_analysis = intent == IntentType.TREND
        requires_root_cause = intent == IntentType.ROOT_CAUSE
        
        return QueryPlan(
            intent=intent,
            entity_type=entity_type,
            entity_value=entity_value,
            metric=metric,
            dimension=dimension,
            date_range=date_range,
            filters=filters,
            ranking_type=ranking.get('ranking_type'),
            limit=ranking.get('limit'),
            sort_order=ranking.get('sort_order'),
            sort_by=ranking.get('sort_by'),
            comparison_entities=comparison,
            dashboard_type=dashboard_type,
            control_tower_type=self._detect_control_tower_type(normalized),
            trend_period=self._detect_trend_period(normalized),
            trend_metric=metric if metric in ['revenue', 'units', 'pod', 'pgi'] else None,
            root_cause_target=self._detect_root_cause_target(normalized),
            original_message=original,
            normalized_message=normalized,
            requires_kpi=requires_kpi,
            requires_analytics=requires_analytics,
            requires_control_tower=requires_control_tower,
            requires_trend_analysis=requires_trend_analysis,
            requires_root_cause=requires_root_cause
        )
    
    # ==========================================================
    # 12. DETECT CONTROL TOWER TYPE
    # ==========================================================
    
    def _detect_control_tower_type(self, normalized: str) -> Optional[str]:
        """Detect specific control tower query type"""
        if 'critical deliveries' in normalized or 'stuck deliveries' in normalized:
            return 'critical_deliveries'
        if 'critical pod' in normalized or 'pending pod' in normalized:
            return 'critical_pod'
        if 'worst dealer' in normalized:
            return 'worst_dealer'
        if 'worst warehouse' in normalized:
            return 'worst_warehouse'
        return 'general'
    
    # ==========================================================
    # 13. DETECT TREND PERIOD
    # ==========================================================
    
    def _detect_trend_period(self, normalized: str) -> Optional[str]:
        """Detect trend period (daily, weekly, monthly, quarterly, yearly)"""
        if 'daily' in normalized or 'day by day' in normalized:
            return 'daily'
        if 'weekly' in normalized or 'week by week' in normalized:
            return 'weekly'
        if 'monthly' in normalized or 'month over month' in normalized:
            return 'monthly'
        if 'quarterly' in normalized or 'quarter over quarter' in normalized:
            return 'quarterly'
        if 'yearly' in normalized or 'year over year' in normalized:
            return 'yearly'
        return 'monthly'  # Default
    
    # ==========================================================
    # 14. DETECT ROOT CAUSE TARGET
    # ==========================================================
    
    def _detect_root_cause_target(self, normalized: str) -> Optional[str]:
        """Detect what the root cause analysis is targeting"""
        if 'delivery' in normalized and 'delay' in normalized:
            return 'delivery_delays'
        if 'pod' in normalized and 'aging' in normalized:
            return 'pod_aging'
        if 'lahore' in normalized:
            return 'lahore'
        if 'dealer' in normalized:
            return 'dealer_performance'
        return None
    
    # ==========================================================
    # 15. GROQ QUERY PLANNER
    # ==========================================================
    
    async def groq_query_planner(self, user_message: str) -> Optional[Dict[str, Any]]:
        """
        Use GROQ for complex questions
        
        Example: "Top 5 dealers by pending POD aging in Lahore this month"
        Output: {"dimension": "dealer", "metric": "pending_pod_aging", "location": "lahore", "limit": 5}
        """
        if not self.groq_client:
            logger.warning("GROQ client not available for query planning")
            return None
        
        try:
            system_prompt = """You are a query planning assistant. Convert user questions into structured JSON.
            
            Output format:
            {
                "intent": "ranking|dashboard|comparison|trend|control_tower",
                "dimension": "dealer|warehouse|city|product|division",
                "metric": "revenue|units|dn_count|pod_aging|delivery_aging|pending_pod",
                "filters": {"city": "lahore", "warehouse": "sargodha"},
                "limit": 10,
                "sort": "desc|asc",
                "date_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
            }
            
            Only output JSON, no explanations."""
            
            response = self.groq_client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=500,
                temperature=0.1
            )
            
            result = response.choices[0].message.content
            # Parse JSON from response
            import json
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            
            return None
            
        except Exception as e:
            logger.error(f"GROQ query planning failed: {e}")
            return None
    
    # ==========================================================
    # 16. VALIDATE QUERY PLAN
    # ==========================================================
    
    def validate_query_plan(self, query_plan: QueryPlan) -> bool:
        """
        Verify plan is executable
        
        Checks:
        - Entity Exists
        - Metric Supported
        - Date Valid
        - Intent Valid
        - Filters Valid
        """
        
        # Check intent is valid
        valid_intents = [
            IntentType.DEALER_DASHBOARD, IntentType.WAREHOUSE_DASHBOARD,
            IntentType.CITY_DASHBOARD, IntentType.PRODUCT_DASHBOARD,
            IntentType.DIVISION_DASHBOARD, IntentType.SALES_MANAGER_DASHBOARD,
            IntentType.DN_LOOKUP, IntentType.DN_STATUS,
            IntentType.POD_ANALYSIS, IntentType.PGI_ANALYSIS, IntentType.DELIVERY_ANALYSIS,
            IntentType.KPI_REPORT, IntentType.EXECUTIVE_DASHBOARD,
            IntentType.CONTROL_TOWER, IntentType.RANKING, IntentType.COMPARISON,
            IntentType.TREND, IntentType.ROOT_CAUSE, IntentType.HELP
        ]
        
        if query_plan.intent not in valid_intents:
            logger.warning(f"Invalid intent: {query_plan.intent}")
            return False
        
        # For ranking, limit must be set
        if query_plan.intent == IntentType.RANKING and not query_plan.limit:
            query_plan.limit = 10  # Set default
        
        # For dashboard, entity must exist
        if query_plan.intent in [IntentType.DEALER_DASHBOARD, IntentType.WAREHOUSE_DASHBOARD,
                                  IntentType.CITY_DASHBOARD, IntentType.PRODUCT_DASHBOARD]:
            if not query_plan.entity_value and not query_plan.dashboard_type:
                logger.warning(f"Dashboard without entity: {query_plan}")
                return False
        
        return True
    
    # ==========================================================
    # 17. CALCULATE CONFIDENCE SCORE
    # ==========================================================
    
    def calculate_confidence_score(self, query_plan: QueryPlan) -> float:
        """
        Measure parsing confidence
        
        Output: 0.00 -> 1.00
        High confidence (0.8+): execute directly
        Low confidence (0.5-0.8): execute with validation
        Very low (0-0.5): send to GROQ
        """
        score = 0.0
        
        # Intent confidence (30%)
        if query_plan.intent != IntentType.UNKNOWN:
            score += 0.3
        
        # Entity confidence (25%)
        if query_plan.entity_type and query_plan.entity_value:
            score += 0.25
        elif query_plan.entity_type or query_plan.entity_value:
            score += 0.15
        
        # Metric confidence (20%)
        if query_plan.metric:
            score += 0.2
        
        # Filters confidence (15%)
        if query_plan.filters:
            score += 0.15
        
        # Date range confidence (10%)
        if query_plan.date_range:
            score += 0.1
        
        # Ranking has its own boost
        if query_plan.ranking_type and query_plan.limit:
            score = min(score + 0.15, 1.0)
        
        return round(score, 2)


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_ai_query_service = None

def get_ai_query_service() -> AIQueryService:
    """Get singleton instance of AIQueryService"""
    global _ai_query_service
    if _ai_query_service is None:
        _ai_query_service = AIQueryService()
    return _ai_query_service


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("AI Query Service - Natural Language Intelligence Engine")
logger.info("=" * 60)
logger.info("")
logger.info("   RESPONSIBILITIES:")
logger.info("   ✅ Natural Language Understanding")
logger.info("   ✅ Intent Detection")
logger.info("   ✅ Entity Extraction")
logger.info("   ✅ Metric Extraction")
logger.info("   ✅ Date Intelligence")
logger.info("   ✅ Ranking Detection")
logger.info("   ✅ Comparison Detection")
logger.info("   ✅ Query Planning")
logger.info("")
logger.info("   WHAT IT NEVER DOES:")
logger.info("   ✗ SQL Queries")
logger.info("   ✗ KPI Calculations")
logger.info("   ✗ WhatsApp Sending")
logger.info("   ✗ Dashboard Formatting")
logger.info("")
logger.info("   STATUS: ✅ READY")
logger.info("=" * 60)
