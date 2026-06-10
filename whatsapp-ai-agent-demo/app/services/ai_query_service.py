# ==========================================================
# FILE: app/services/ai_query_service.py (IMPROVED v35.0)
# ==========================================================
# PURPOSE: PURE ROUTER ONLY - Single Brain for Query Routing
#
# IMPROVEMENTS v35.0:
# - Added 15+ new intents (Dealer, Warehouse, City, Branch, Region, Aging)
# - Enhanced intent detection with keyword groups
# - Improved entity extraction (warehouse, city, division, sales_manager, etc.)
# - Centralized ROUTE_MAP for cleaner routing
# - Service validation at startup
# - Query metrics tracking
# - Context awareness for follow-up questions
# - Query classification (Operational/Analytical/Executive/AI)
# ==========================================================

import re
import time
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from loguru import logger


# ==========================================================
# INTENT TYPES (Expanded)
# ==========================================================

class Intent(str, Enum):
    # DN Operations
    DN_LOOKUP = "dn_lookup"
    DN_TIMELINE = "dn_timeline"
    DN_PRODUCTS = "dn_products"
    DN_AGING = "dn_aging"
    
    # POD Operations
    PENDING_POD = "pending_pod"
    POD_AGING = "pod_aging"
    POD_PERFORMANCE = "pod_performance"
    
    # PGI Operations
    PENDING_PGI = "pending_pgi"
    PGI_AGING = "pgi_aging"
    
    # Delivery Operations
    PENDING_DELIVERIES = "pending_deliveries"
    DELIVERY_AGING = "delivery_aging"
    DELIVERY_PERFORMANCE = "delivery_performance"
    
    # Dealer Operations
    DEALER_PERFORMANCE = "dealer_performance"
    DEALER_LOOKUP = "dealer_lookup"
    TOP_DEALERS = "top_dealers"
    
    # Warehouse Operations
    WAREHOUSE_STATUS = "warehouse_status"
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    TOP_WAREHOUSES = "top_warehouses"
    
    # City/Region Operations
    CITY_STATUS = "city_status"
    CITY_PERFORMANCE = "city_performance"
    REGION_PERFORMANCE = "region_performance"
    BRANCH_PERFORMANCE = "branch_performance"
    
    # Customer/Division Operations
    CUSTOMER_LOOKUP = "customer_lookup"
    DIVISION_ANALYSIS = "division_analysis"
    SALES_MANAGER_ANALYSIS = "sales_manager_analysis"
    MATERIAL_ANALYSIS = "material_analysis"
    
    # Product Operations
    TOP_PRODUCTS = "top_products"
    PRODUCT_PERFORMANCE = "product_performance"
    
    # KPI Operations
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    EXECUTIVE_KPI = "executive_kpi"
    NETWORK_HEALTH = "network_health"
    CRITICAL_DELAYS = "critical_delays"
    CONTROL_TOWER = "control_tower"
    
    # General
    HELP = "help"
    GREETING = "greeting"
    GENERAL = "general"
    AI_QUERY = "ai_query"


# ==========================================================
# QUERY CLASSIFICATION
# ==========================================================

class QueryClass(str, Enum):
    OPERATIONAL = "operational"   # DN lookup, status checks
    ANALYTICAL = "analytical"     # Trends, rankings, comparisons
    EXECUTIVE = "executive"       # KPIs, dashboards, health
    AI = "ai"                     # AI-generated insights


# ==========================================================
# ENTITY EXTRACTION (Enhanced)
# ==========================================================

@dataclass
class ExtractedEntities:
    # Core entities
    dn_number: Optional[str] = None
    dealer: Optional[str] = None
    dealer_code: Optional[str] = None
    customer: Optional[str] = None
    customer_code: Optional[str] = None
    warehouse: Optional[str] = None
    warehouse_code: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    division: Optional[str] = None
    sales_manager: Optional[str] = None
    material_no: Optional[str] = None
    product: Optional[str] = None
    
    # Time entities
    days: Optional[int] = None
    limit: Optional[int] = 10
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    
    # Context (for follow-up questions)
    last_intent: Optional[str] = None
    last_dn: Optional[str] = None
    last_dealer: Optional[str] = None
    last_city: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}
    
    def has_any(self) -> bool:
        return any([self.dn_number, self.dealer, self.warehouse, 
                   self.city, self.region, self.product])


class EntityExtractor:
    # Patterns
    DN_PATTERN = re.compile(r'\b(\d{8,15})\b')
    DAYS_PATTERN = re.compile(r'(\d+)\s+days?', re.IGNORECASE)
    LIMIT_PATTERN = re.compile(r'(?:top|limit)\s+(\d+)', re.IGNORECASE)
    
    # Code patterns
    DEALER_CODE_PATTERN = re.compile(r'dealer[-_]?code[:\s]*([A-Z0-9]+)', re.IGNORECASE)
    CUSTOMER_CODE_PATTERN = re.compile(r'customer[-_]?code[:\s]*([A-Z0-9]+)', re.IGNORECASE)
    WAREHOUSE_CODE_PATTERN = re.compile(r'warehouse[-_]?code[:\s]*([A-Z0-9]+)', re.IGNORECASE)
    MATERIAL_PATTERN = re.compile(r'material[-_]?no[:\s]*([A-Z0-9-]+)', re.IGNORECASE)
    
    # Known entities for extraction
    CITIES = ['karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad', 
              'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot']
    
    @classmethod
    def extract(cls, question: str, context: Dict = None) -> ExtractedEntities:
        """Extract entities from question with context awareness"""
        question_lower = question.lower().strip()
        entities = ExtractedEntities()
        
        # Restore context if available
        if context:
            entities.last_intent = context.get("last_intent")
            entities.last_dn = context.get("last_dn")
            entities.last_dealer = context.get("last_dealer")
            entities.last_city = context.get("last_city")
        
        # Extract DN
        dn_match = cls.DN_PATTERN.search(question)
        if dn_match:
            entities.dn_number = dn_match.group(1)
        elif entities.last_dn and not entities.dn_number:
            # Use context from previous query
            entities.dn_number = entities.last_dn
        
        # Extract days
        days_match = cls.DAYS_PATTERN.search(question_lower)
        if days_match:
            entities.days = int(days_match.group(1))
        
        # Extract limit
        limit_match = cls.LIMIT_PATTERN.search(question_lower)
        if limit_match:
            entities.limit = min(int(limit_match.group(1)), 50)
        
        # Extract codes
        code_match = cls.DEALER_CODE_PATTERN.search(question)
        if code_match:
            entities.dealer_code = code_match.group(1)
        
        code_match = cls.CUSTOMER_CODE_PATTERN.search(question)
        if code_match:
            entities.customer_code = code_match.group(1)
        
        code_match = cls.WAREHOUSE_CODE_PATTERN.search(question)
        if code_match:
            entities.warehouse_code = code_match.group(1)
        
        code_match = cls.MATERIAL_PATTERN.search(question)
        if code_match:
            entities.material_no = code_match.group(1)
        
        # Extract city
        for city in cls.CITIES:
            if city in question_lower:
                entities.city = city.capitalize()
                break
        else:
            if entities.last_city:
                entities.city = entities.last_city
        
        # Extract warehouse
        warehouse_match = re.search(r'warehouse\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance|status)', question_lower)
        if warehouse_match:
            entities.warehouse = warehouse_match.group(1).strip()
        
        # Extract dealer (multiple patterns)
        dealer_patterns = [
            r'dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance|dashboard|details|risk)',
            r'show\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
            r'for\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)'
        ]
        
        for pattern in dealer_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entities.dealer = match.group(1).strip()
                break
        else:
            if entities.last_dealer and not entities.dealer:
                entities.dealer = entities.last_dealer
        
        # Extract division
        division_match = re.search(r'division\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)', question_lower)
        if division_match:
            entities.division = division_match.group(1).strip()
        
        # Extract sales manager
        manager_match = re.search(r'(?:sales manager|manager)\s+([A-Za-z\s]+?)(?:\s+$|\.|\,)', question_lower)
        if manager_match:
            entities.sales_manager = manager_match.group(1).strip()
        
        # Extract product
        product_match = re.search(r'product\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance)', question_lower)
        if product_match:
            entities.product = product_match.group(1).strip()
        
        return entities


# ==========================================================
# INTENT DETECTION (Enhanced with Keyword Groups)
# ==========================================================

class IntentDetector:
    # Keyword groups for better matching
    KEYWORD_GROUPS = {
        # DN Operations
        Intent.DN_TIMELINE: ['timeline', 'journey', 'history', 'track', 'progress', 'status history'],
        Intent.DN_PRODUCTS: ['products', 'items', 'materials', 'what products', 'what items'],
        Intent.DN_AGING: ['dn aging', 'how old', 'dn age', 'delivery note age'],
        
        # POD Operations
        Intent.PENDING_POD: ['pending pod', 'pod pending', 'missing pod', 'pod not received', 'pending proof'],
        Intent.POD_AGING: ['pod aging', 'pod older than', 'old pod', 'pod delay'],
        Intent.POD_PERFORMANCE: ['pod performance', 'pod rate', 'pod compliance', 'pod score'],
        
        # PGI Operations
        Intent.PENDING_PGI: ['pending pgi', 'pgi pending', 'pending dispatch', 'not dispatched'],
        Intent.PGI_AGING: ['pgi aging', 'pgi older than', 'dispatch delay', 'pgi backlog'],
        
        # Delivery Operations
        Intent.PENDING_DELIVERIES: ['pending delivery', 'delivery pending', 'undelivered'],
        Intent.DELIVERY_AGING: ['delivery aging', 'delivery older than', 'delayed delivery'],
        Intent.DELIVERY_PERFORMANCE: ['delivery performance', 'on time delivery', 'delivery rate'],
        
        # Dealer Operations
        Intent.DEALER_PERFORMANCE: ['dealer performance', 'dealer metrics', 'dealer score', 'how is dealer'],
        Intent.DEALER_LOOKUP: ['dealer details', 'dealer info', 'who is dealer', 'dealer information'],
        Intent.TOP_DEALERS: ['top dealer', 'best dealer', 'dealer ranking', 'top performing', 'leading dealer'],
        
        # Warehouse Operations
        Intent.WAREHOUSE_STATUS: ['warehouse status', 'warehouse stock', 'warehouse capacity'],
        Intent.WAREHOUSE_PERFORMANCE: ['warehouse performance', 'warehouse efficiency', 'warehouse metrics'],
        Intent.TOP_WAREHOUSES: ['top warehouse', 'best warehouse', 'warehouse ranking'],
        
        # City/Region Operations
        Intent.CITY_STATUS: ['city status', 'city performance', 'city metrics'],
        Intent.CITY_PERFORMANCE: ['city performance', 'city ranking', 'city comparison'],
        Intent.REGION_PERFORMANCE: ['region performance', 'regional performance', 'region score'],
        Intent.BRANCH_PERFORMANCE: ['branch performance', 'branch score', 'branch ranking'],
        
        # Customer/Division Operations
        Intent.CUSTOMER_LOOKUP: ['customer details', 'customer info', 'customer performance'],
        Intent.DIVISION_ANALYSIS: ['division performance', 'division analysis', 'division report'],
        Intent.SALES_MANAGER_ANALYSIS: ['sales manager', 'manager performance', 'manager report'],
        Intent.MATERIAL_ANALYSIS: ['material performance', 'material analysis', 'material report'],
        
        # Product Operations
        Intent.TOP_PRODUCTS: ['top products', 'best products', 'product ranking', 'top selling'],
        Intent.PRODUCT_PERFORMANCE: ['product performance', 'product sales', 'product metrics'],
        
        # KPI Operations
        Intent.EXECUTIVE_DASHBOARD: ['executive dashboard', 'ceo dashboard', 'leadership', 'board view'],
        Intent.EXECUTIVE_KPI: ['kpi', 'key performance', 'metrics', 'performance metrics'],
        Intent.NETWORK_HEALTH: ['network health', 'system health', 'service status', 'health check'],
        Intent.CRITICAL_DELAYS: ['critical delay', 'urgent delay', 'high risk delay', 'critical dn'],
        Intent.CONTROL_TOWER: ['control tower', 'command center', 'all alerts', 'mission control'],
    }
    
    @classmethod
    def classify_query(cls, question: str) -> QueryClass:
        """Classify query type for better routing"""
        question_lower = question.lower()
        
        # Executive queries
        executive_keywords = ['kpi', 'dashboard', 'executive', 'ceo', 'board', 'health', 'control tower']
        if any(kw in question_lower for kw in executive_keywords):
            return QueryClass.EXECUTIVE
        
        # Analytical queries
        analytical_keywords = ['trend', 'ranking', 'top', 'best', 'comparison', 'analysis', 'performance']
        if any(kw in question_lower for kw in analytical_keywords):
            return QueryClass.ANALYTICAL
        
        # AI queries
        ai_keywords = ['why', 'root cause', 'recommend', 'suggest', 'how to improve', 'what if']
        if any(kw in question_lower for kw in ai_keywords):
            return QueryClass.AI
        
        return QueryClass.OPERATIONAL
    
    @classmethod
    def detect(cls, question: str, entities: ExtractedEntities) -> Tuple[Intent, QueryClass]:
        """Detect intent from question and entities"""
        question_lower = question.lower().strip()
        
        # Help
        if question_lower in ['help', 'menu', 'commands']:
            return Intent.HELP, QueryClass.OPERATIONAL
        
        # Greeting
        if question_lower in ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']:
            return Intent.GREETING, QueryClass.OPERATIONAL
        
        # DN number present (highest priority)
        if entities.dn_number:
            if 'timeline' in question_lower or 'history' in question_lower:
                return Intent.DN_TIMELINE, QueryClass.OPERATIONAL
            elif 'product' in question_lower or 'item' in question_lower:
                return Intent.DN_PRODUCTS, QueryClass.OPERATIONAL
            elif 'aging' in question_lower or 'old' in question_lower:
                return Intent.DN_AGING, QueryClass.ANALYTICAL
            else:
                return Intent.DN_LOOKUP, QueryClass.OPERATIONAL
        
        # Dealer present
        if entities.dealer or entities.dealer_code:
            if 'performance' in question_lower or 'metrics' in question_lower:
                return Intent.DEALER_PERFORMANCE, QueryClass.ANALYTICAL
            elif 'details' in question_lower or 'info' in question_lower:
                return Intent.DEALER_LOOKUP, QueryClass.OPERATIONAL
            else:
                return Intent.DEALER_PERFORMANCE, QueryClass.ANALYTICAL
        
        # Warehouse present
        if entities.warehouse or entities.warehouse_code:
            if 'performance' in question_lower:
                return Intent.WAREHOUSE_PERFORMANCE, QueryClass.ANALYTICAL
            else:
                return Intent.WAREHOUSE_STATUS, QueryClass.OPERATIONAL
        
        # City present
        if entities.city:
            if 'performance' in question_lower or 'ranking' in question_lower:
                return Intent.CITY_PERFORMANCE, QueryClass.ANALYTICAL
            else:
                return Intent.CITY_STATUS, QueryClass.OPERATIONAL
        
        # Division present
        if entities.division:
            return Intent.DIVISION_ANALYSIS, QueryClass.ANALYTICAL
        
        # Sales manager present
        if entities.sales_manager:
            return Intent.SALES_MANAGER_ANALYSIS, QueryClass.ANALYTICAL
        
        # Material present
        if entities.material_no:
            return Intent.MATERIAL_ANALYSIS, QueryClass.ANALYTICAL
        
        # Product present
        if entities.product:
            return Intent.PRODUCT_PERFORMANCE, QueryClass.ANALYTICAL
        
        # Keyword-based detection
        for intent, keywords in cls.KEYWORD_GROUPS.items():
            for keyword in keywords:
                if keyword in question_lower:
                    query_class = cls.classify_query(question)
                    return intent, query_class
        
        # Default
        query_class = cls.classify_query(question)
        if query_class == QueryClass.AI:
            return Intent.AI_QUERY, query_class
        
        return Intent.GENERAL, query_class


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    @staticmethod
    def format_success(data: Any, summary: str = None, metadata: Dict = None) -> Dict:
        return {
            "success": True, 
            "data": data, 
            "summary": summary or "",
            "metadata": metadata or {}
        }
    
    @staticmethod
    def format_error(message: str, code: str = "unknown") -> Dict:
        return {"success": False, "data": {}, "summary": message, "error_code": code}
    
    @staticmethod
    def format_help() -> str:
        return """
🤖 *AI LOGISTICS ASSISTANT - HELP* v35.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• Send any 10+ digit number
• `DN timeline` - Track journey
• `DN aging` - Check delay

📋 *Pending Items*
• `Pending POD` - Missing proofs
• `Pending PGI` - Pending dispatches
• `Pending deliveries` - Undelivered

🏪 *Dealer Analytics*
• `Top dealers` - Rankings
• `Dealer ABC performance` - Specific dealer
• `Dealer details` - Information

🏭 *Warehouse Analytics*
• `Top warehouses` - Rankings
• `Warehouse status` - Current state

🌍 *Region & Branch*
• `Region performance` - Regional metrics
• `Branch performance` - Branch scores
• `City performance` - City metrics

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Critical delays` - Urgent issues
• `Control tower` - All alerts

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    @staticmethod
    def format_greeting() -> str:
        hour = datetime.now().hour
        if hour < 12:
            greeting = "Good morning"
        elif hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"
        
        return f"""
{greeting}! 👋

I'm your *AI Logistics Assistant v35.0*. I can help you track DNs, check performance, and monitor operations.

Type `Help` to see all commands.
"""


# ==========================================================
# CENTRAL ROUTE MAP
# ==========================================================

class RouteMap:
    """Centralized route mapping for cleaner routing"""
    
    # Logistics routes
    LOGISTICS_ROUTES = {
        Intent.DN_LOOKUP: ("get_complete_dn_intelligence", True),
        Intent.DN_TIMELINE: ("get_dn_timeline", True),
        Intent.DN_PRODUCTS: ("get_dn_products", True),
        Intent.DN_AGING: ("get_complete_dn_intelligence", True),
        Intent.PENDING_POD: ("get_pod_status", False),
        Intent.POD_AGING: ("get_pod_status", False),
        Intent.PENDING_PGI: ("get_pending_pgi", False),
        Intent.PGI_AGING: ("get_pending_pgi", False),
        Intent.PENDING_DELIVERIES: ("get_pending_deliveries", False),
        Intent.WAREHOUSE_STATUS: ("get_warehouse_status", True),
        Intent.REGION_PERFORMANCE: ("get_region_performance", True),
        Intent.CITY_STATUS: ("get_region_performance", True),
    }
    
    # Analytics routes
    ANALYTICS_ROUTES = {
        Intent.TOP_DEALERS: ("get_top_dealers", True),
        Intent.TOP_WAREHOUSES: ("get_top_warehouses", True),
        Intent.TOP_PRODUCTS: ("get_top_products", True),
        Intent.DEALER_PERFORMANCE: ("get_dealer_performance", True),
        Intent.DEALER_LOOKUP: ("get_dealer_performance", True),
        Intent.WAREHOUSE_PERFORMANCE: ("get_warehouse_status", True),
        Intent.CITY_PERFORMANCE: ("get_city_performance", True),
        Intent.BRANCH_PERFORMANCE: ("get_branch_performance", True),
        Intent.DIVISION_ANALYSIS: ("get_division_analysis", True),
        Intent.PRODUCT_PERFORMANCE: ("get_top_products", True),
    }
    
    # KPI routes
    KPI_ROUTES = {
        Intent.EXECUTIVE_DASHBOARD: ("get_executive_dashboard", False),
        Intent.EXECUTIVE_KPI: ("get_executive_dashboard", False),
        Intent.NETWORK_HEALTH: ("get_network_health", False),
        Intent.CRITICAL_DELAYS: ("get_critical_delays", False),
        Intent.CONTROL_TOWER: ("get_control_tower", False),
    }
    
    @classmethod
    def get_route(cls, intent: Intent) -> Tuple[Optional[str], Optional[str], bool]:
        """Get route for intent: (service, method, has_param)"""
        
        if intent in cls.LOGISTICS_ROUTES:
            method, has_param = cls.LOGISTICS_ROUTES[intent]
            return "logistics", method, has_param
        
        if intent in cls.ANALYTICS_ROUTES:
            method, has_param = cls.ANALYTICS_ROUTES[intent]
            return "analytics", method, has_param
        
        if intent in cls.KPI_ROUTES:
            method, has_param = cls.KPI_ROUTES[intent]
            return "kpi", method, has_param
        
        return None, None, False


# ==========================================================
# QUERY METRICS TRACKING
# ==========================================================

class QueryMetrics:
    """Track query metrics for monitoring"""
    
    def __init__(self):
        self.metrics = {
            "total_queries": 0,
            "by_intent": {},
            "by_class": {},
            "avg_response_time_ms": 0,
            "success_rate": 100.0,
            "failures": 0
        }
    
    def record(self, intent: str, query_class: str, processing_time_ms: float, success: bool):
        self.metrics["total_queries"] += 1
        
        # By intent
        if intent not in self.metrics["by_intent"]:
            self.metrics["by_intent"][intent] = 0
        self.metrics["by_intent"][intent] += 1
        
        # By class
        if query_class not in self.metrics["by_class"]:
            self.metrics["by_class"][query_class] = 0
        self.metrics["by_class"][query_class] += 1
        
        # Response time (moving average)
        current_avg = self.metrics["avg_response_time_ms"]
        total = self.metrics["total_queries"]
        self.metrics["avg_response_time_ms"] = ((current_avg * (total - 1)) + processing_time_ms) / total
        
        # Success rate
        if not success:
            self.metrics["failures"] += 1
        self.metrics["success_rate"] = ((self.metrics["total_queries"] - self.metrics["failures"]) / self.metrics["total_queries"]) * 100
    
    def get_metrics(self) -> Dict:
        return {
            **self.metrics,
            "by_intent": dict(sorted(self.metrics["by_intent"].items(), key=lambda x: x[1], reverse=True)[:10])
        }


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    def __init__(self, db: Session):
        self.db = db
        self._logistics_service = None
        self._analytics_service = None
        self._kpi_service = None
        self._ai_provider = None
        self.formatter = ResponseFormatter()
        self.metrics = QueryMetrics()
        self.conversation_context = {}  # user_id -> context
        
        # Validate services on startup
        self._validate_services()
        
        logger.info("✅ AI Query Service v35.0 - Pure Router Mode with Enhanced Features")
    
    def _validate_services(self):
        """Validate all services are available at startup"""
        services_status = {}
        
        # Check logistics service
        try:
            if self.logistics_service:
                # Test a simple method
                services_status["logistics"] = "available"
            else:
                services_status["logistics"] = "unavailable"
        except Exception as e:
            services_status["logistics"] = f"error: {e}"
        
        # Check analytics service
        try:
            if self.analytics_service:
                services_status["analytics"] = "available"
            else:
                services_status["analytics"] = "unavailable"
        except Exception as e:
            services_status["analytics"] = f"error: {e}"
        
        # Check KPI service
        try:
            if self.kpi_service:
                services_status["kpi"] = "available"
            else:
                services_status["kpi"] = "unavailable"
        except Exception as e:
            services_status["kpi"] = f"error: {e}"
        
        # Check AI provider
        try:
            if self.ai_provider:
                services_status["ai"] = "available"
            else:
                services_status["ai"] = "unavailable"
        except Exception as e:
            services_status["ai"] = f"error: {e}"
        
        logger.info(f"Service validation: {services_status}")
        return services_status
    
    def _get_context(self, user_phone: str) -> Dict:
        """Get conversation context for user"""
        if user_phone not in self.conversation_context:
            self.conversation_context[user_phone] = {}
        return self.conversation_context[user_phone]
    
    def _update_context(self, user_phone: str, intent: Intent, entities: ExtractedEntities, response: Dict):
        """Update conversation context"""
        context = self._get_context(user_phone)
        context["last_intent"] = intent.value
        context["last_query_time"] = datetime.now().isoformat()
        
        if entities.dn_number:
            context["last_dn"] = entities.dn_number
        if entities.dealer:
            context["last_dealer"] = entities.dealer
        if entities.city:
            context["last_city"] = entities.city
        
        # Keep only last 10 interactions
        if len(context) > 10:
            keys = list(context.keys())
            for key in keys[:-10]:
                del context[key]
    
    @property
    def logistics_service(self):
        if self._logistics_service is None:
            try:
                from app.services.logistics_query_service import LogisticsQueryService
                self._logistics_service = LogisticsQueryService(self.db)
                logger.debug("LogisticsQueryService loaded")
            except Exception as e:
                logger.error(f"Failed to load LogisticsQueryService: {e}")
        return self._logistics_service
    
    @property
    def analytics_service(self):
        if self._analytics_service is None:
            try:
                from app.services.analytics_service import AnalyticsService
                self._analytics_service = AnalyticsService(self.db)
                logger.debug("AnalyticsService loaded")
            except Exception as e:
                logger.error(f"Failed to load AnalyticsService: {e}")
        return self._analytics_service
    
    @property
    def kpi_service(self):
        if self._kpi_service is None:
            try:
                from app.services.kpi_service import KPIService
                self._kpi_service = KPIService(self.db)
                logger.debug("KPIService loaded")
            except Exception as e:
                logger.error(f"Failed to load KPIService: {e}")
        return self._kpi_service
    
    @property
    def ai_provider(self):
        if self._ai_provider is None:
            try:
                from app.services.ai_provider_service import get_ai_provider
                self._ai_provider = get_ai_provider()
                logger.debug("AI Provider loaded")
            except Exception as e:
                logger.error(f"Failed to load AI Provider: {e}")
        return self._ai_provider
    
    def process_query(self, question: str, user_phone: str = None) -> Dict:
        """Main entry point - Pure routing pipeline."""
        start_time = time.time()
        
        logger.info(f"Processing: {question[:100]}")
        
        # Get conversation context
        context = self._get_context(user_phone) if user_phone else {}
        
        # Extract entities (with context)
        entities = EntityExtractor.extract(question, context)
        logger.debug(f"Entities: {entities.to_dict()}")
        
        # Detect intent and classify
        intent, query_class = IntentDetector.detect(question, entities)
        logger.info(f"Intent: {intent.value}, Class: {query_class.value}")
        
        # Route to service using centralized route map
        result = self._route(intent, entities, question)
        
        # Format response
        whatsapp_message = self._to_whatsapp(result)
        
        # Update context
        if user_phone:
            self._update_context(user_phone, intent, entities, result)
        
        # Record metrics
        elapsed_ms = (time.time() - start_time) * 1000
        self.metrics.record(intent.value, query_class.value, elapsed_ms, result.get("success", True))
        
        logger.info(f"Response generated in {elapsed_ms:.0f}ms")
        
        return {
            "success": result.get("success", True),
            "response": whatsapp_message,
            "intent": intent.value,
            "query_class": query_class.value,
            "entities": entities.to_dict(),
            "processing_time_ms": round(elapsed_ms, 2)
        }
    
    def _route(self, intent: Intent, entities: ExtractedEntities, question: str) -> Dict:
        """Route to appropriate service using centralized route map"""
        
        # Get route from map
        service_name, method, has_param = RouteMap.get_route(intent)
        
        # DN Aging special handling
        if intent == Intent.DN_AGING and entities.dn_number:
            result = self._call_logistics("get_complete_dn_intelligence", entities.dn_number)
            if result.get("success") and result.get("data"):
                data = result["data"]
                aging_days = data.get("aging_days", 0)
                result["summary"] = f"📅 *DN Aging Report*\n━━━━━━━━━━━━━━━━━━━━━\n\nDN: {entities.dn_number}\nAge: {aging_days} days\nStatus: {data.get('pod_status', 'Unknown')}"
            return result
        
        # POD Aging special handling
        if intent == Intent.POD_AGING:
            result = self._call_logistics("get_pod_status", None)
            if result.get("success") and result.get("data"):
                data = result["data"]
                result["summary"] = f"📋 *POD Aging Summary*\n━━━━━━━━━━━━━━━━━━━━━\n\nPending PODs: {data.get('pending_count', 0)}\nAvg Aging: {data.get('avg_aging', 0)} days"
            return result
        
        # Control Tower special handling
        if intent == Intent.CONTROL_TOWER:
            return self._call_control_tower()
        
        # Route based on service
        if service_name == "logistics" and self.logistics_service:
            param = entities.dn_number if has_param and entities.dn_number else (
                entities.city if entities.city else (
                    entities.region if entities.region else None
                )
            )
            if param:
                return self._call_logistics(method, param)
            return self._call_logistics(method)
        
        if service_name == "analytics" and self.analytics_service:
            param = entities.dealer if has_param and entities.dealer else (
                entities.limit if method in ["get_top_dealers", "get_top_warehouses", "get_top_products"] else None
            )
            if param:
                return self._call_analytics(method, param)
            return self._call_analytics(method, entities.limit)
        
        if service_name == "kpi" and self.kpi_service:
            return self._call_kpi(method, 30)
        
        # General Routes
        if intent == Intent.HELP:
            return self.formatter.format_success({}, self.formatter.format_help())
        if intent == Intent.GREETING:
            return self.formatter.format_success({}, self.formatter.format_greeting())
        
        # AI Query
        if intent == Intent.AI_QUERY or query_class == QueryClass.AI:
            return self._call_ai("general", question)
        
        # Default
        return self._call_ai("general", question)
    
    def _call_logistics(self, method: str, *args) -> Dict:
        if not self.logistics_service:
            return self.formatter.format_error("Logistics service unavailable")
        try:
            service_method = getattr(self.logistics_service, method, None)
            if not service_method:
                return self.formatter.format_error(f"Method '{method}' not available")
            result = service_method(*args) if args else service_method()
            if isinstance(result, dict) and result.get("error"):
                return self.formatter.format_error(result["error"])
            return self.formatter.format_success(result)
        except Exception as e:
            logger.error(f"Logistics call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _call_analytics(self, method: str, *args) -> Dict:
        if not self.analytics_service:
            return self.formatter.format_error("Analytics service unavailable")
        try:
            service_method = getattr(self.analytics_service, method, None)
            if not service_method:
                return self.formatter.format_error(f"Method '{method}' not available")
            result = service_method(*args) if args else service_method()
            if isinstance(result, dict) and result.get("error"):
                return self.formatter.format_error(result["error"])
            return self.formatter.format_success(result)
        except Exception as e:
            logger.error(f"Analytics call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _call_kpi(self, method: str, *args) -> Dict:
        if not self.kpi_service:
            return self.formatter.format_error("KPI service unavailable")
        try:
            service_method = getattr(self.kpi_service, method, None)
            if not service_method:
                return self.formatter.format_error(f"Method '{method}' not available")
            result = service_method(*args) if args else service_method()
            if isinstance(result, dict) and result.get("error"):
                return self.formatter.format_error(result["error"])
            return self.formatter.format_success(result)
        except Exception as e:
            logger.error(f"KPI call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _call_control_tower(self) -> Dict:
        alerts = {}
        if self.logistics_service:
            try:
                pod_result = self.logistics_service.get_pod_status()
                if pod_result and not pod_result.get("error"):
                    alerts["pending_pods"] = pod_result
            except Exception as e:
                logger.error(f"Failed to get POD status: {e}")
        if self.kpi_service:
            try:
                risks = self.kpi_service.get_risk_alerts()
                if risks and not risks.get("error"):
                    alerts["risk_alerts"] = risks
            except Exception as e:
                logger.error(f"Failed to get risk alerts: {e}")
        
        pending_count = alerts.get("pending_pods", {}).get("pending_count", 0)
        alert_count = alerts.get("risk_alerts", {}).get("total_alerts", 0)
        
        summary = f"🚨 *CONTROL TOWER*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        summary += f"📋 Pending PODs: {pending_count}\n"
        summary += f"🔔 Active Alerts: {alert_count}\n"
        
        if alert_count > 0:
            summary += f"\n🔴 Immediate attention required!"
        else:
            summary += f"\n✅ All systems operational"
        
        return self.formatter.format_success(alerts, summary)
    
    def _call_ai(self, analysis_type: str, question: str) -> Dict:
        if not self.ai_provider:
            return self.formatter.format_error("AI service unavailable")
        try:
            result = self.ai_provider.chat(question, "guest")
            response_text = result if isinstance(result, str) else str(result)
            return self.formatter.format_success({"insight": response_text}, response_text)
        except Exception as e:
            logger.error(f"AI call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _to_whatsapp(self, response: Dict) -> str:
        if not response.get("success"):
            return f"❌ {response.get('summary', 'Unable to process request')}"
        summary = response.get("summary", "")
        if summary:
            return summary
        return "✅ Request processed successfully"
    
    def health_check(self) -> Dict:
        return {
            "service": "ai_query_service",
            "version": "35.0",
            "mode": "pure_router",
            "status": "healthy",
            "metrics": self.metrics.get_metrics(),
            "services": {
                "logistics": self._logistics_service is not None,
                "analytics": self._analytics_service is not None,
                "kpi": self._kpi_service is not None,
                "ai": self._ai_provider is not None
            }
        }
    
    def get_metrics(self) -> Dict:
        return self.metrics.get_metrics()


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(question: str, db: Session, phone_number: str = None, user_id: str = None) -> str:
    try:
        service = AIQueryService(db)
        result = service.process_query(question, phone_number or user_id)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.exception(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


def health_check(db: Session) -> Dict:
    try:
        service = AIQueryService(db)
        return service.health_check()
    except Exception as e:
        return {"service": "ai_query_service", "status": "unhealthy", "error": str(e), "version": "35.0"}


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("🧠 AI QUERY SERVICE v35.0 - ENHANCED PURE ROUTER MODE")
logger.info("   New Features:")
logger.info("   • 25+ Intent Types (Dealer, Warehouse, City, Branch, Region, Aging)")
logger.info("   • Enhanced Entity Extraction (warehouse, city, division, sales_manager)")
logger.info("   • Centralized Route Map for cleaner routing")
logger.info("   • Service Validation at Startup")
logger.info("   • Query Metrics Tracking")
logger.info("   • Context Awareness for Follow-up Questions")
logger.info("   • Query Classification (Operational/Analytical/Executive/AI)")
logger.info("=" * 70)
