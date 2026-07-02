# =====================================================================================================================
# DEEPSEEK ENTERPRISE SOFTWARE ARCHITECT PROMPT
# PROJECT: HPK WhatsApp AI Logistics Platform
# TARGET FILE: app/services/ai_provider_service.py
# VERSION: Enterprise AI Orchestrator v20.0
# =====================================================================================================================

"""
Enterprise AI Orchestrator for HPK WhatsApp Logistics Platform
Complete request routing, intent detection, and service orchestration
"""

import re
import asyncio
import inspect
import uuid
import time
import json
import os
from typing import Dict, Any, Optional, List, Tuple, Union, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from contextlib import asynccontextmanager

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# =====================================================================================================================
# CONSTANTS & CONFIGURATION
# =====================================================================================================================

class Intent(Enum):
    """Supported intents for the orchestrator"""
    MENU = "menu"
    DN_LOOKUP = "dn_lookup"
    DN_DASHBOARD = "dn_dashboard"
    DN_HISTORY = "dn_history"
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_REVENUE = "dealer_revenue"
    DEALER_PENDING = "dealer_pending"
    CITY_DASHBOARD = "city_dashboard"
    CITY_REVENUE = "city_revenue"
    CITY_PENDING = "city_pending"
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    WAREHOUSE_PENDING = "warehouse_pending"
    WAREHOUSE_REVENUE = "warehouse_revenue"
    PRODUCT_DASHBOARD = "product_dashboard"
    TOP_PRODUCTS = "top_products"
    NATIONAL_KPI = "national_kpi"
    NATIONAL_REVENUE = "national_revenue"
    NATIONAL_UNITS = "national_units"
    PENDING_DNS = "pending_dns"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    TOP_PERFORMERS = "top_performers"
    TOP_DEALERS = "top_dealers"
    TOP_CITIES = "top_cities"
    HELP = "help"
    GENERAL_AI = "general_ai"
    UNKNOWN = "unknown"

class ServiceStatus(Enum):
    """Service health status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

@dataclass
class ServiceRegistryEntry:
    """Service registry entry"""
    menu_number: str
    menu_name: str
    intent: Intent
    service_file: str
    service_class: str
    service_instance: Optional[Any] = None
    preferred_method: str = ""
    compatible_methods: List[str] = field(default_factory=list)
    supported_entities: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    description: str = ""
    requires_ai: bool = False
    example_queries: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    health_status: ServiceStatus = ServiceStatus.UNKNOWN

@dataclass
class EntityExtraction:
    """Extracted entities from user message"""
    dn_number: Optional[str] = None
    dealer_name: Optional[str] = None
    dealer_code: Optional[str] = None
    customer_name: Optional[str] = None
    customer_code: Optional[str] = None
    warehouse: Optional[str] = None
    warehouse_code: Optional[str] = None
    city: Optional[str] = None
    division: Optional[str] = None
    sales_office: Optional[str] = None
    sales_manager: Optional[str] = None
    material_number: Optional[str] = None
    material_code: Optional[str] = None
    product: Optional[str] = None
    revenue: Optional[float] = None
    units: Optional[int] = None
    pending: Optional[int] = None
    pgi: Optional[int] = None
    pod: Optional[int] = None
    date: Optional[datetime] = None
    date_range: Optional[Tuple[datetime, datetime]] = None
    top: Optional[int] = None
    bottom: Optional[int] = None
    ranking: Optional[str] = None
    comparison: Optional[str] = None
    growth: Optional[float] = None
    trend: Optional[str] = None
    query_type: Optional[str] = None
    search_term: Optional[str] = None

# =====================================================================================================================
# MAIN MENU
# =====================================================================================================================

MAIN_MENU = """🤖 HPK Logistics AI Assistant

0️⃣ Main Menu
1️⃣ DN Delivery Menu
2️⃣ Dealer Analytics Menu
3️⃣ City Analytics Menu
4️⃣ Warehouse Dashboard Menu
5️⃣ Product Analytics Menu
6️⃣ National KPI Menu
7️⃣ Pending DN Menu
8️⃣ Top Performers Menu
9️⃣ AI Query Menu

Reply with menu number."""

# =====================================================================================================================
# LOGGING UTILITIES
# =====================================================================================================================

class LoggingContext:
    """Context manager for structured logging"""
    
    def __init__(self, request_id: str, whatsapp_number: Optional[str] = None):
        self.request_id = request_id
        self.whatsapp_number = whatsapp_number
        self.context = {
            "request_id": request_id,
            "whatsapp_number": whatsapp_number,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def info(self, message: str, **kwargs):
        """Log info with context"""
        log_data = {**self.context, **kwargs}
        logger.info(f"[{self.request_id}] {message}", extra=log_data)
    
    def error(self, message: str, **kwargs):
        """Log error with context"""
        log_data = {**self.context, **kwargs}
        logger.error(f"[{self.request_id}] {message}", extra=log_data)
    
    def debug(self, message: str, **kwargs):
        """Log debug with context"""
        log_data = {**self.context, **kwargs}
        logger.debug(f"[{self.request_id}] {message}", extra=log_data)
    
    def warning(self, message: str, **kwargs):
        """Log warning with context"""
        log_data = {**self.context, **kwargs}
        logger.warning(f"[{self.request_id}] {message}", extra=log_data)

# =====================================================================================================================
# COMPILED REGEX PATTERNS
# =====================================================================================================================

class RegexPatterns:
    """Compiled regex patterns for entity extraction"""
    DN_NUMBER = re.compile(r'\b(\d{10})\b')
    DEALER_NAME = re.compile(r'(?:dealer|show|get|view)\s+(.+?)(?:\s+in|\s+city|$)', re.IGNORECASE)
    DEALER_CODE = re.compile(r'(?:code|id)[\s:]+([A-Z0-9]{3,})', re.IGNORECASE)
    WAREHOUSE = re.compile(r'(?:warehouse|wh)[\s:]+([A-Za-z\s]+)', re.IGNORECASE)
    WAREHOUSE_CODE = re.compile(r'(?:warehouse code|wh code)[\s:]+([A-Z0-9]{3})', re.IGNORECASE)
    CITY = re.compile(r'\b(lahore|karachi|islamabad|rawalpindi|faisalabad|multan|hyderabad|peshawar|quetta|gujranwala|sialkot)\b', re.IGNORECASE)
    DIVISION = re.compile(r'(?:division|div)[\s:]+([A-Za-z\s]+)', re.IGNORECASE)
    SALES_OFFICE = re.compile(r'(?:sales office|office)[\s:]+([A-Za-z\s]+)', re.IGNORECASE)
    SALES_MANAGER = re.compile(r'(?:sales manager|manager)[\s:]+([A-Za-z\s]+)', re.IGNORECASE)
    MATERIAL_NUMBER = re.compile(r'(?:material|mat)[\s:]+([A-Z0-9]{6,})', re.IGNORECASE)
    MATERIAL_CODE = re.compile(r'(?:material code|mat code)[\s:]+([A-Z0-9]{3,})', re.IGNORECASE)
    PRODUCT = re.compile(r'(?:product|prod)[\s:]+([A-Za-z0-9\s-]+)', re.IGNORECASE)
    REVENUE = re.compile(r'(?:revenue|rev)[\s:]+([\d,]+\.?[\d]*)', re.IGNORECASE)
    UNITS = re.compile(r'(?:units|qty)[\s:]+(\d+)', re.IGNORECASE)
    PENDING = re.compile(r'(?:pending|pend)[\s:]+(\d+)', re.IGNORECASE)
    PGI = re.compile(r'(?:pgi)[\s:]+(\d+)', re.IGNORECASE)
    POD = re.compile(r'(?:pod)[\s:]+(\d+)', re.IGNORECASE)
    DATE = re.compile(r'(\d{4}-\d{2}-\d{2})')
    DATE_RANGE = re.compile(r'(\d{4}-\d{2}-\d{2})\s*(?:to|until|through)\s*(\d{4}-\d{2}-\d{2})', re.IGNORECASE)
    TOP = re.compile(r'(?:top|best)\s+(\d+)', re.IGNORECASE)
    BOTTOM = re.compile(r'(?:bottom|worst)\s+(\d+)', re.IGNORECASE)
    RANKING = re.compile(r'(?:rank|ranking)[\s:]+([A-Za-z\s]+)', re.IGNORECASE)
    COMPARISON = re.compile(r'(?:compare|comparison)[\s:]+([A-Za-z\s]+)', re.IGNORECASE)
    GROWTH = re.compile(r'(?:growth)[\s:]+([\d.]+)%', re.IGNORECASE)
    TREND = re.compile(r'(?:trend)[\s:]+([A-Za-z\s]+)', re.IGNORECASE)
    QUERY_TYPE = re.compile(r'(?:what|how|why|when|where|who)[\s:]+(.+)', re.IGNORECASE)
    SEARCH_TERM = re.compile(r'(?:search|find|look for)[\s:]+(.+)', re.IGNORECASE)

    @classmethod
    def get_all_patterns(cls) -> Dict[str, re.Pattern]:
        """Get all compiled patterns"""
        return {name: getattr(cls, name) for name in dir(cls) 
                if not name.startswith('_') and isinstance(getattr(cls, name), re.Pattern)}

# =====================================================================================================================
# INTENT DETECTION ENGINE
# =====================================================================================================================

class IntentDetectionEngine:
    """Deterministic intent detection engine"""
    
    def __init__(self):
        self.intent_patterns = self._build_intent_patterns()
        self.menu_trigger = re.compile(r'^(menu|0|main menu|help|home|start|back|hello|hi|hey)$', re.IGNORECASE)
        self.number_pattern = re.compile(r'^\d+$')
    
    def _build_intent_patterns(self) -> Dict[Intent, List[re.Pattern]]:
        """Build intent pattern mappings"""
        patterns = {
            Intent.MENU: [
                re.compile(r'^(menu|main menu|0)$', re.IGNORECASE),
                re.compile(r'^(help|home|start|back)$', re.IGNORECASE),
                re.compile(r'^(hello|hi|hey)$', re.IGNORECASE)
            ],
            Intent.DN_LOOKUP: [
                re.compile(r'\b(\d{10})\b'),
                re.compile(r'(?:track|check|lookup|find|get)\s+(?:dn|delivery|order)\s*[#:]?\s*(\d{10})', re.IGNORECASE)
            ],
            Intent.DN_DASHBOARD: [
                re.compile(r'(?:dn|delivery).*(?:dashboard|stats|status|summary|analytics)', re.IGNORECASE),
                re.compile(r'(?:show|get|view)\s+(?:dn|delivery).*(?:dashboard|stats)', re.IGNORECASE)
            ],
            Intent.DN_HISTORY: [
                re.compile(r'(?:dn|delivery).*(?:history|past|previous|old|recent)', re.IGNORECASE)
            ],
            Intent.DEALER_DASHBOARD: [
                re.compile(r'(?:dealer|distributor).*(?:dashboard|stats|status|summary|analytics)', re.IGNORECASE),
                re.compile(r'(?:show|get|view)\s+(?:dealer|distributor).*(?:dashboard|stats)', re.IGNORECASE)
            ],
            Intent.DEALER_REVENUE: [
                re.compile(r'(?:dealer|distributor).*(?:revenue|sales|income|earnings|performance)', re.IGNORECASE)
            ],
            Intent.DEALER_PENDING: [
                re.compile(r'(?:dealer|distributor).*(?:pending|delay|overdue|missed|outstanding)', re.IGNORECASE)
            ],
            Intent.CITY_DASHBOARD: [
                re.compile(r'(?:city|town).*(?:dashboard|stats|status|summary|analytics)', re.IGNORECASE),
                re.compile(r'(?:show|get|view)\s+(?:city|town).*(?:dashboard|stats)', re.IGNORECASE)
            ],
            Intent.CITY_REVENUE: [
                re.compile(r'(?:city|town).*(?:revenue|sales|income|earnings|performance)', re.IGNORECASE)
            ],
            Intent.CITY_PENDING: [
                re.compile(r'(?:city|town).*(?:pending|delay|overdue|missed|outstanding)', re.IGNORECASE)
            ],
            Intent.WAREHOUSE_DASHBOARD: [
                re.compile(r'(?:warehouse|wh).*(?:dashboard|stats|status|summary|analytics)', re.IGNORECASE),
                re.compile(r'(?:show|get|view)\s+(?:warehouse|wh).*(?:dashboard|stats)', re.IGNORECASE)
            ],
            Intent.WAREHOUSE_PENDING: [
                re.compile(r'(?:warehouse|wh).*(?:pending|delay|overdue|missed|outstanding)', re.IGNORECASE)
            ],
            Intent.WAREHOUSE_REVENUE: [
                re.compile(r'(?:warehouse|wh).*(?:revenue|sales|income|earnings|performance)', re.IGNORECASE)
            ],
            Intent.PRODUCT_DASHBOARD: [
                re.compile(r'(?:product|prod|material|item).*(?:dashboard|stats|status|summary|analytics)', re.IGNORECASE),
                re.compile(r'(?:show|get|view)\s+(?:product|prod|material).*(?:dashboard|stats)', re.IGNORECASE)
            ],
            Intent.TOP_PRODUCTS: [
                re.compile(r'(?:top|best|highest).*(?:products|items|materials|sku)', re.IGNORECASE),
                re.compile(r'(?:product|item).*(?:top|best|rank)', re.IGNORECASE)
            ],
            Intent.NATIONAL_KPI: [
                re.compile(r'(?:national|overall|company|enterprise).*(?:kpi|metric|performance|dashboard)', re.IGNORECASE)
            ],
            Intent.NATIONAL_REVENUE: [
                re.compile(r'(?:national|overall|company).*(?:revenue|sales|income|earnings|total)', re.IGNORECASE)
            ],
            Intent.NATIONAL_UNITS: [
                re.compile(r'(?:national|overall|company).*(?:units|qty|volume|quantity)', re.IGNORECASE)
            ],
            Intent.PENDING_DNS: [
                re.compile(r'(?:pending|delay|overdue|missed|outstanding).*(?:dn|delivery|order|shipment)', re.IGNORECASE)
            ],
            Intent.PENDING_PGI: [
                re.compile(r'(?:pending|delay|overdue).*pgi', re.IGNORECASE)
            ],
            Intent.PENDING_POD: [
                re.compile(r'(?:pending|delay|overdue).*pod', re.IGNORECASE)
            ],
            Intent.TOP_PERFORMERS: [
                re.compile(r'(?:top|best|highest).*(?:performers|performance|achievers)', re.IGNORECASE)
            ],
            Intent.TOP_DEALERS: [
                re.compile(r'(?:top|best|highest).*(?:dealers|distributors|partners)', re.IGNORECASE)
            ],
            Intent.TOP_CITIES: [
                re.compile(r'(?:top|best|highest).*(?:cities|towns|locations)', re.IGNORECASE)
            ],
            Intent.HELP: [
                re.compile(r'^(help|support|assist|guide|how to)$', re.IGNORECASE)
            ]
        }
        return patterns
    
    def detect_intent(self, message: str) -> Tuple[Intent, float]:
        """Detect intent from message with confidence score"""
        message_lower = message.lower().strip()
        
        # Check for menu first
        if self.menu_trigger.match(message_lower):
            return Intent.MENU, 1.0
        
        # Check if it's a pure number (DN lookup)
        if self.number_pattern.match(message_lower):
            return Intent.DN_LOOKUP, 0.95
        
        # Check all intent patterns
        for intent, patterns in self.intent_patterns.items():
            for pattern in patterns:
                if pattern.search(message_lower):
                    return intent, 0.95
        
        # Check for specific patterns with lower confidence
        if re.search(r'\b\d{10}\b', message):
            return Intent.DN_LOOKUP, 0.9
        
        if re.search(r'(?:dealer|distributor|partner)', message_lower):
            return Intent.DEALER_DASHBOARD, 0.7
        
        if re.search(r'(?:city|town|location)', message_lower):
            return Intent.CITY_DASHBOARD, 0.7
        
        if re.search(r'(?:warehouse|wh|storage)', message_lower):
            return Intent.WAREHOUSE_DASHBOARD, 0.7
        
        if re.search(r'(?:product|material|item|sku)', message_lower):
            return Intent.PRODUCT_DASHBOARD, 0.7
        
        # Check for comparison words
        if re.search(r'(?:compare|versus|vs|against)', message_lower):
            return Intent.GENERAL_AI, 0.6
        
        # Default to general AI
        return Intent.GENERAL_AI, 0.3

# =====================================================================================================================
# ENTITY EXTRACTION ENGINE
# =====================================================================================================================

class EntityExtractionEngine:
    """Deterministic entity extraction engine"""
    
    def __init__(self):
        self.patterns = RegexPatterns.get_all_patterns()
        self._compile_all_patterns()
    
    def _compile_all_patterns(self):
        """Ensure all patterns are compiled"""
        self.dn_pattern = RegexPatterns.DN_NUMBER
        self.dealer_pattern = RegexPatterns.DEALER_NAME
        self.dealer_code_pattern = RegexPatterns.DEALER_CODE
        self.warehouse_pattern = RegexPatterns.WAREHOUSE
        self.warehouse_code_pattern = RegexPatterns.WAREHOUSE_CODE
        self.city_pattern = RegexPatterns.CITY
        self.division_pattern = RegexPatterns.DIVISION
        self.sales_office_pattern = RegexPatterns.SALES_OFFICE
        self.sales_manager_pattern = RegexPatterns.SALES_MANAGER
        self.material_number_pattern = RegexPatterns.MATERIAL_NUMBER
        self.material_code_pattern = RegexPatterns.MATERIAL_CODE
        self.product_pattern = RegexPatterns.PRODUCT
        self.revenue_pattern = RegexPatterns.REVENUE
        self.units_pattern = RegexPatterns.UNITS
        self.pending_pattern = RegexPatterns.PENDING
        self.pgi_pattern = RegexPatterns.PGI
        self.pod_pattern = RegexPatterns.POD
        self.date_pattern = RegexPatterns.DATE
        self.date_range_pattern = RegexPatterns.DATE_RANGE
        self.top_pattern = RegexPatterns.TOP
        self.bottom_pattern = RegexPatterns.BOTTOM
        self.ranking_pattern = RegexPatterns.RANKING
        self.comparison_pattern = RegexPatterns.COMPARISON
        self.growth_pattern = RegexPatterns.GROWTH
        self.trend_pattern = RegexPatterns.TREND
        self.query_type_pattern = RegexPatterns.QUERY_TYPE
        self.search_term_pattern = RegexPatterns.SEARCH_TERM
    
    def extract_entities(self, message: str) -> EntityExtraction:
        """Extract all entities from message"""
        entities = EntityExtraction()
        
        # Extract DN
        dn_match = self.dn_pattern.search(message)
        if dn_match:
            entities.dn_number = dn_match.group(1)
        
        # Extract Dealer
        dealer_match = self.dealer_pattern.search(message)
        if dealer_match:
            entities.dealer_name = dealer_match.group(1).strip()
        
        dealer_code_match = self.dealer_code_pattern.search(message)
        if dealer_code_match:
            entities.dealer_code = dealer_code_match.group(1)
        
        # Extract Warehouse
        warehouse_match = self.warehouse_pattern.search(message)
        if warehouse_match:
            entities.warehouse = warehouse_match.group(1).strip()
        
        warehouse_code_match = self.warehouse_code_pattern.search(message)
        if warehouse_code_match:
            entities.warehouse_code = warehouse_code_match.group(1)
        
        # Extract City
        city_match = self.city_pattern.search(message)
        if city_match:
            entities.city = city_match.group(1).capitalize()
        
        # Extract Division
        division_match = self.division_pattern.search(message)
        if division_match:
            entities.division = division_match.group(1).strip()
        
        # Extract Sales Office
        sales_office_match = self.sales_office_pattern.search(message)
        if sales_office_match:
            entities.sales_office = sales_office_match.group(1).strip()
        
        # Extract Sales Manager
        sales_manager_match = self.sales_manager_pattern.search(message)
        if sales_manager_match:
            entities.sales_manager = sales_manager_match.group(1).strip()
        
        # Extract Material
        material_number_match = self.material_number_pattern.search(message)
        if material_number_match:
            entities.material_number = material_number_match.group(1)
        
        material_code_match = self.material_code_pattern.search(message)
        if material_code_match:
            entities.material_code = material_code_match.group(1)
        
        # Extract Product
        product_match = self.product_pattern.search(message)
        if product_match:
            entities.product = product_match.group(1).strip()
        
        # Extract Revenue
        revenue_match = self.revenue_pattern.search(message)
        if revenue_match:
            try:
                entities.revenue = float(revenue_match.group(1).replace(',', ''))
            except ValueError:
                pass
        
        # Extract Units
        units_match = self.units_pattern.search(message)
        if units_match:
            try:
                entities.units = int(units_match.group(1))
            except ValueError:
                pass
        
        # Extract Pending
        pending_match = self.pending_pattern.search(message)
        if pending_match:
            try:
                entities.pending = int(pending_match.group(1))
            except ValueError:
                pass
        
        # Extract PGI
        pgi_match = self.pgi_pattern.search(message)
        if pgi_match:
            try:
                entities.pgi = int(pgi_match.group(1))
            except ValueError:
                pass
        
        # Extract POD
        pod_match = self.pod_pattern.search(message)
        if pod_match:
            try:
                entities.pod = int(pod_match.group(1))
            except ValueError:
                pass
        
        # Extract Date
        date_match = self.date_pattern.search(message)
        if date_match:
            try:
                entities.date = datetime.strptime(date_match.group(1), '%Y-%m-%d')
            except ValueError:
                pass
        
        # Extract Date Range
        date_range_match = self.date_range_pattern.search(message)
        if date_range_match:
            try:
                start = datetime.strptime(date_range_match.group(1), '%Y-%m-%d')
                end = datetime.strptime(date_range_match.group(2), '%Y-%m-%d')
                entities.date_range = (start, end)
            except ValueError:
                pass
        
        # Extract Top
        top_match = self.top_pattern.search(message)
        if top_match:
            try:
                entities.top = int(top_match.group(1))
            except ValueError:
                pass
        
        # Extract Bottom
        bottom_match = self.bottom_pattern.search(message)
        if bottom_match:
            try:
                entities.bottom = int(bottom_match.group(1))
            except ValueError:
                pass
        
        # Extract Ranking
        ranking_match = self.ranking_pattern.search(message)
        if ranking_match:
            entities.ranking = ranking_match.group(1).strip()
        
        # Extract Comparison
        comparison_match = self.comparison_pattern.search(message)
        if comparison_match:
            entities.comparison = comparison_match.group(1).strip()
        
        # Extract Growth
        growth_match = self.growth_pattern.search(message)
        if growth_match:
            try:
                entities.growth = float(growth_match.group(1))
            except ValueError:
                pass
        
        # Extract Trend
        trend_match = self.trend_pattern.search(message)
        if trend_match:
            entities.trend = trend_match.group(1).strip()
        
        # Extract Query Type
        query_type_match = self.query_type_pattern.search(message)
        if query_type_match:
            entities.query_type = query_type_match.group(1).strip()
        
        # Extract Search Term
        search_term_match = self.search_term_pattern.search(message)
        if search_term_match:
            entities.search_term = search_term_match.group(1).strip()
        
        return entities

# =====================================================================================================================
# SERVICE REGISTRY
# =====================================================================================================================

class ServiceRegistry:
    """Centralized service registry with health monitoring"""
    
    def __init__(self):
        self._services: Dict[Intent, ServiceRegistryEntry] = {}
        self._method_cache: Dict[str, Callable] = {}
        self._instance_cache: Dict[str, Any] = {}
        self._initialize_registry()
    
    def _initialize_registry(self):
        """Initialize service registry with all services"""
        self._services = {
            Intent.DN_LOOKUP: ServiceRegistryEntry(
                menu_number="1",
                menu_name="DN Lookup",
                intent=Intent.DN_LOOKUP,
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_dn_details",
                compatible_methods=["get_dn_details", "get_dn_status", "get_dn_info", "track_dn"],
                supported_entities=["dn_number"],
                keywords=["track", "check", "lookup", "find", "get", "search"],
                description="Look up delivery note details",
                example_queries=["Track DN 6243698820", "Check delivery 6243698749"]
            ),
            Intent.DN_DASHBOARD: ServiceRegistryEntry(
                menu_number="1",
                menu_name="DN Dashboard",
                intent=Intent.DN_DASHBOARD,
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_dn_dashboard",
                compatible_methods=["get_dn_dashboard", "get_dashboard", "get_summary", "get_analytics"],
                supported_entities=["dn_number", "date_range"],
                keywords=["dashboard", "stats", "status", "summary", "analytics"],
                description="View DN analytics dashboard",
                example_queries=["Show DN dashboard", "DN stats"]
            ),
            Intent.DN_HISTORY: ServiceRegistryEntry(
                menu_number="1",
                menu_name="DN History",
                intent=Intent.DN_HISTORY,
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_dn_history",
                compatible_methods=["get_dn_history", "get_history", "get_previous_dns"],
                supported_entities=["dn_number", "date_range"],
                keywords=["history", "past", "previous", "old", "recent"],
                description="View DN history",
                example_queries=["DN history", "Previous deliveries"]
            ),
            Intent.DEALER_DASHBOARD: ServiceRegistryEntry(
                menu_number="2",
                menu_name="Dealer Dashboard",
                intent=Intent.DEALER_DASHBOARD,
                service_file="app.services.dealer_analytics_service",
                service_class="DealerAnalyticsService",
                preferred_method="get_dealer_dashboard",
                compatible_methods=["get_dealer_dashboard", "get_dashboard", "get_dealer_analytics", "get_analytics"],
                supported_entities=["dealer_name", "dealer_code"],
                keywords=["dealer", "distributor", "partner", "retailer"],
                description="View dealer analytics dashboard",
                example_queries=["Show dealer Taj Electronics", "Dealer dashboard"]
            ),
            Intent.CITY_DASHBOARD: ServiceRegistryEntry(
                menu_number="3",
                menu_name="City Dashboard",
                intent=Intent.CITY_DASHBOARD,
                service_file="app.services.city_service",
                service_class="CityService",
                preferred_method="get_city_dashboard",
                compatible_methods=["get_city_dashboard", "get_dashboard", "get_city_analytics"],
                supported_entities=["city"],
                keywords=["city", "town", "urban", "municipal", "location"],
                description="View city analytics dashboard",
                example_queries=["Show Lahore dashboard", "Karachi city stats"]
            ),
            Intent.WAREHOUSE_DASHBOARD: ServiceRegistryEntry(
                menu_number="4",
                menu_name="Warehouse Dashboard",
                intent=Intent.WAREHOUSE_DASHBOARD,
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_warehouse_dashboard",
                compatible_methods=["get_warehouse_dashboard", "get_dashboard", "get_warehouse_analytics"],
                supported_entities=["warehouse", "warehouse_code"],
                keywords=["warehouse", "wh", "storage", "facility", "distribution"],
                description="View warehouse analytics dashboard",
                example_queries=["Warehouse dashboard", "LHE warehouse stats"]
            ),
            Intent.PRODUCT_DASHBOARD: ServiceRegistryEntry(
                menu_number="5",
                menu_name="Product Dashboard",
                intent=Intent.PRODUCT_DASHBOARD,
                service_file="app.services.product_service",
                service_class="ProductService",
                preferred_method="get_product_dashboard",
                compatible_methods=["get_product_dashboard", "get_dashboard", "get_product_analytics"],
                supported_entities=["product", "material_number", "material_code"],
                keywords=["product", "material", "item", "sku", "inventory"],
                description="View product analytics dashboard",
                example_queries=["Product dashboard", "HMW-20MPS stats"]
            ),
            Intent.NATIONAL_KPI: ServiceRegistryEntry(
                menu_number="6",
                menu_name="National KPI",
                intent=Intent.NATIONAL_KPI,
                service_file="app.services.national_kpi_service",
                service_class="NationalKPIService",
                preferred_method="get_national_kpi_dashboard",
                compatible_methods=["get_national_kpi_dashboard", "get_national_kpi", "get_kpi", "get_dashboard"],
                supported_entities=["date_range"],
                keywords=["national", "overall", "company", "enterprise", "corporate"],
                description="View national KPI dashboard",
                example_queries=["National KPI", "Company performance"]
            ),
            Intent.PENDING_DNS: ServiceRegistryEntry(
                menu_number="7",
                menu_name="Pending DNs",
                intent=Intent.PENDING_DNS,
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_pending_dns",
                compatible_methods=["get_pending_dns", "get_pending", "get_delayed_dns", "get_outstanding"],
                supported_entities=["city", "warehouse", "dealer"],
                keywords=["pending", "delay", "overdue", "missed", "outstanding"],
                description="View pending DN list",
                example_queries=["Pending DNs", "Delayed deliveries"]
            ),
            Intent.TOP_PERFORMERS: ServiceRegistryEntry(
                menu_number="8",
                menu_name="Top Performers",
                intent=Intent.TOP_PERFORMERS,
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_top_performers",
                compatible_methods=["get_top_performers", "get_top", "get_performers", "get_ranking"],
                supported_entities=["top", "bottom", "ranking"],
                keywords=["top", "best", "performers", "ranking", "achievers"],
                description="View top performers",
                example_queries=["Top performers", "Best dealers"]
            ),
            Intent.GENERAL_AI: ServiceRegistryEntry(
                menu_number="9",
                menu_name="AI Query",
                intent=Intent.GENERAL_AI,
                service_file="app.services.groq_service",
                service_class="GroqService",
                preferred_method="process_query",
                compatible_methods=["process_query", "ask_ai", "get_ai_response", "query_ai"],
                supported_entities=[],
                keywords=["ai", "ask", "query", "analyze", "explain"],
                description="General AI assistance",
                requires_ai=True,
                example_queries=["What's the issue", "Explain this"]
            ),
            Intent.MENU: ServiceRegistryEntry(
                menu_number="0",
                menu_name="Main Menu",
                intent=Intent.MENU,
                service_file="",
                service_class="",
                preferred_method="show_menu",
                compatible_methods=[],
                supported_entities=[],
                keywords=["menu", "main", "home", "start"],
                description="Show main menu",
                example_queries=["menu", "help"]
            )
        }
    
    def get_service(self, intent: Intent) -> Optional[ServiceRegistryEntry]:
        """Get service entry for intent"""
        return self._services.get(intent)
    
    def get_service_instance(self, entry: ServiceRegistryEntry) -> Any:
        """Get or create service instance"""
        if not entry.service_file or not entry.service_class:
            return None
            
        cache_key = f"{entry.service_file}_{entry.service_class}"
        
        if cache_key in self._instance_cache:
            return self._instance_cache[cache_key]
        
        try:
            module = __import__(entry.service_file, fromlist=[entry.service_class])
            service_class = getattr(module, entry.service_class)
            instance = service_class()
            
            # Cache the instance
            self._instance_cache[cache_key] = instance
            entry.health_status = ServiceStatus.HEALTHY
            return instance
            
        except (ImportError, AttributeError) as e:
            logger.error(f"Failed to load service {entry.service_file}: {e}")
            entry.health_status = ServiceStatus.UNHEALTHY
            return None
    
    def get_method(self, instance: Any, method_name: str) -> Optional[Callable]:
        """Get method from instance with fallback to compatible methods"""
        if instance is None:
            return None
            
        cache_key = f"{id(instance)}_{method_name}"
        
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]
        
        # Check if method exists
        if hasattr(instance, method_name):
            method = getattr(instance, method_name)
            if callable(method):
                self._method_cache[cache_key] = method
                return method
        
        # Try compatible methods
        entry = None
        for intent, entry_obj in self._services.items():
            if entry_obj.service_instance == instance:
                entry = entry_obj
                break
        
        if entry:
            for compatible_method in entry.compatible_methods:
                if hasattr(instance, compatible_method):
                    method = getattr(instance, compatible_method)
                    if callable(method):
                        self._method_cache[cache_key] = method
                        return method
        
        return None
    
    def update_health_status(self, intent: Intent, status: ServiceStatus):
        """Update service health status"""
        entry = self._services.get(intent)
        if entry:
            entry.health_status = status

# =====================================================================================================================
# ROUTING ENGINE
# =====================================================================================================================

class RoutingEngine:
    """Deterministic routing engine with fallback chain"""
    
    def __init__(self, registry: ServiceRegistry):
        self.registry = registry
        self.intent_engine = IntentDetectionEngine()
        self.entity_engine = EntityExtractionEngine()
        self.semantic_router = None  # Optional semantic router
    
    async def route_request(
        self, 
        message: str, 
        whatsapp_number: Optional[str] = None,
        db_session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Route request through the complete pipeline"""
        start_time = time.time()
        request_id = str(uuid.uuid4())[:8]
        
        # Initialize logging context
        log_context = LoggingContext(request_id, whatsapp_number)
        
        try:
            # Normalize message
            normalized = self._normalize_message(message)
            
            # Log incoming
            log_context.info(
                "Received WhatsApp message",
                message=message,
                normalized=normalized,
                whatsapp_number=whatsapp_number
            )
            
            # Step 1: Check for menu
            if self._is_menu_request(normalized):
                return await self._handle_menu(request_id, start_time, log_context)
            
            # Step 2: Detect intent
            intent, confidence = self.intent_engine.detect_intent(normalized)
            log_context.info(
                "Intent detected",
                intent=intent.value,
                confidence=confidence
            )
            
            # Step 3: Extract entities
            entities = self.entity_engine.extract_entities(normalized)
            extracted = self._filter_empty_entities(entities)
            log_context.info(
                "Entities extracted",
                entities=extracted
            )
            
            # Step 4: Get service entry
            service_entry = self.registry.get_service(intent)
            if not service_entry:
                log_context.warning(
                    "Service not found for intent",
                    intent=intent.value
                )
                return self._create_error_response(
                    request_id, 
                    "Service not found for intent", 
                    start_time,
                    log_context
                )
            
            # Step 5: Get service instance
            service_instance = self.registry.get_service_instance(service_entry)
            if not service_instance:
                log_context.error(
                    "Service instance unavailable",
                    service_file=service_entry.service_file,
                    service_class=service_entry.service_class
                )
                return self._create_error_response(
                    request_id, 
                    f"Service {service_entry.service_file} unavailable", 
                    start_time,
                    log_context
                )
            
            # Step 6: Get method
            method = self.registry.get_method(service_instance, service_entry.preferred_method)
            if not method:
                log_context.error(
                    "Method unavailable",
                    method=service_entry.preferred_method,
                    compatible_methods=service_entry.compatible_methods
                )
                return self._create_error_response(
                    request_id, 
                    f"Method {service_entry.preferred_method} unavailable", 
                    start_time,
                    log_context
                )
            
            # Step 7: Execute service
            log_context.info(
                "Executing service",
                service=service_entry.service_file,
                method=service_entry.preferred_method,
                requires_ai=service_entry.requires_ai
            )
            
            result = await self._execute_method(
                method, 
                entities, 
                db_session,
                request_id,
                log_context
            )
            
            # Step 8: Format response
            response = self._format_response(result, intent, entities)
            
            # Step 9: Log success
            elapsed = time.time() - start_time
            log_context.info(
                "Request processed successfully",
                execution_time=elapsed,
                response_length=len(response),
                intent=intent.value
            )
            
            return {
                "success": True,
                "request_id": request_id,
                "response": response,
                "intent": intent.value,
                "entities": extracted,
                "execution_time": elapsed,
                "service": service_entry.service_file,
                "method": service_entry.preferred_method
            }
            
        except Exception as e:
            log_context.error(
                "Error routing request",
                error=str(e),
                error_type=type(e).__name__
            )
            elapsed = time.time() - start_time
            return self._create_error_response(request_id, str(e), elapsed, log_context)
    
    def _normalize_message(self, message: str) -> str:
        """Normalize incoming message"""
        # Remove extra whitespace
        normalized = ' '.join(message.split())
        # Lowercase for matching
        return normalized.lower()
    
    def _is_menu_request(self, message: str) -> bool:
        """Check if message is a menu request"""
        menu_triggers = ['menu', 'main menu', 'help', 'home', 'start', 'back', 'hello', 'hi', 'hey', '0']
        return message.strip() in menu_triggers
    
    async def _handle_menu(
        self, 
        request_id: str, 
        start_time: float,
        log_context: LoggingContext
    ) -> Dict[str, Any]:
        """Handle menu request"""
        elapsed = time.time() - start_time
        log_context.info("Showing main menu")
        
        return {
            "success": True,
            "request_id": request_id,
            "response": MAIN_MENU,
            "intent": Intent.MENU.value,
            "entities": {},
            "execution_time": elapsed,
            "service": "ai_provider_service",
            "method": "show_menu"
        }
    
    async def _execute_method(
        self, 
        method: Callable, 
        entities: EntityExtraction,
        db_session: Optional[AsyncSession],
        request_id: str,
        log_context: LoggingContext
    ) -> Any:
        """Execute method with proper async/sync handling"""
        # Prepare arguments
        kwargs = self._prepare_arguments(entities, db_session)
        
        try:
            # Check if method is async
            if inspect.iscoroutinefunction(method):
                return await method(**kwargs)
            else:
                # Run sync method in thread pool
                return await asyncio.to_thread(method, **kwargs)
        except Exception as e:
            log_context.error(
                "Method execution failed",
                error=str(e),
                error_type=type(e).__name__,
                method_name=method.__name__
            )
            raise
    
    def _prepare_arguments(self, entities: EntityExtraction, db_session: Optional[AsyncSession]) -> Dict[str, Any]:
        """Prepare arguments for service method"""
        kwargs = {}
        
        # Add entities as kwargs
        for key, value in entities.__dict__.items():
            if value is not None:
                kwargs[key] = value
        
        # Add db session if available
        if db_session:
            kwargs['db_session'] = db_session
        
        return kwargs
    
    def _format_response(self, result: Any, intent: Intent, entities: EntityExtraction) -> str:
        """Format service response for WhatsApp"""
        if isinstance(result, str):
            return result
        
        if isinstance(result, dict):
            # Check for formatted response
            if 'formatted' in result:
                return result['formatted']
            
            # Check for message
            if 'message' in result:
                return result['message']
            
            # Check for data
            if 'data' in result:
                return self._dict_to_string(result['data'])
            
            # Convert dict to readable format
            return self._dict_to_string(result)
        
        if isinstance(result, list):
            return self._list_to_string(result)
        
        return str(result)
    
    def _dict_to_string(self, data: Dict[str, Any]) -> str:
        """Convert dict to readable string"""
        lines = []
        for key, value in data.items():
            if key.startswith('_'):
                continue
            if isinstance(value, dict):
                lines.append(f"{key}:")
                for sub_key, sub_value in value.items():
                    lines.append(f"  {sub_key}: {sub_value}")
            elif value is not None:
                lines.append(f"{key}: {value}")
        return '\n'.join(lines) if lines else str(data)
    
    def _list_to_string(self, data: List[Any]) -> str:
        """Convert list to readable string"""
        lines = []
        for idx, item in enumerate(data, 1):
            if isinstance(item, dict):
                lines.append(f"{idx}. {self._dict_to_string(item)}")
            else:
                lines.append(f"{idx}. {item}")
        return '\n'.join(lines) if lines else str(data)
    
    def _filter_empty_entities(self, entities: EntityExtraction) -> Dict[str, Any]:
        """Filter out None values from entities"""
        return {k: v for k, v in entities.__dict__.items() if v is not None}
    
    def _create_error_response(
        self, 
        request_id: str, 
        error: str, 
        start_time: float,
        log_context: Optional[LoggingContext] = None
    ) -> Dict[str, Any]:
        """Create error response"""
        elapsed = time.time() - start_time
        
        if log_context:
            log_context.error(
                "Creating error response",
                error=error,
                execution_time=elapsed
            )
        
        return {
            "success": False,
            "request_id": request_id,
            "response": f"⚠️ Service error: {error}\n\nPlease try again or type 'menu' for options.",
            "intent": Intent.UNKNOWN.value,
            "entities": {},
            "execution_time": elapsed,
            "error": error
        }

# =====================================================================================================================
# CACHE MANAGER
# =====================================================================================================================

class CacheManager:
    """TTL cache manager for frequently used data"""
    
    def __init__(self, default_ttl: int = 300):
        self.default_ttl = default_ttl
        self._cache: Dict[str, Tuple[Any, float]] = {}
    
    @lru_cache(maxsize=1000)
    def get(self, key: str) -> Optional[Any]:
        """Get cached value"""
        if key in self._cache:
            value, expiry = self._cache[key]
            if time.time() < expiry:
                return value
            else:
                del self._cache[key]
        return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set cached value with TTL"""
        expiry = time.time() + (ttl or self.default_ttl)
        self._cache[key] = (value, expiry)
    
    def invalidate(self, key: str):
        """Invalidate specific cache entry"""
        if key in self._cache:
            del self._cache[key]

# =====================================================================================================================
# MAIN SERVICE ORCHESTRATOR
# =====================================================================================================================

class AIProviderService:
    """
    Enterprise AI Orchestrator for HPK WhatsApp Logistics Platform
    Complete request routing, intent detection, and service orchestration
    """
    
    def __init__(self):
        """Initialize the orchestrator with all components"""
        self.registry = ServiceRegistry()
        self.routing_engine = RoutingEngine(self.registry)
        self.cache_manager = CacheManager()
        
        # Log initialization
        logger.info("AIProviderService initialized successfully")
        logger.info(f"Registered {len(self.registry._services)} services")
    
    async def process_whatsapp_query(
        self, 
        message: str, 
        whatsapp_number: Optional[str] = None,
        db_session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """
        Main entry point for WhatsApp requests
        
        Args:
            message: Incoming WhatsApp message
            whatsapp_number: Sender's WhatsApp number
            db_session: Optional database session
            
        Returns:
            Dict with response data
        """
        return await self.routing_engine.route_request(
            message, 
            whatsapp_number, 
            db_session
        )
    
    async def process_whatsapp_query_sync(
        self, 
        message: str, 
        whatsapp_number: Optional[str] = None,
        db_session: Optional[AsyncSession] = None
    ) -> str:
        """
        Synchronous wrapper for WhatsApp requests
        
        Args:
            message: Incoming WhatsApp message
            whatsapp_number: Sender's WhatsApp number
            db_session: Optional database session
            
        Returns:
            Response string for WhatsApp
        """
        result = await self.process_whatsapp_query(message, whatsapp_number, db_session)
        return result.get("response", "⚠️ Service error. Please try again.")
    
    def get_menu(self) -> str:
        """Get main menu"""
        return MAIN_MENU
    
    @lru_cache(maxsize=128)
    def get_service_status(self) -> Dict[str, str]:
        """Get health status of all services"""
        statuses = {}
        for intent, entry in self.registry._services.items():
            statuses[intent.value] = entry.health_status.value
        return statuses
    
    def invalidate_cache(self, key: Optional[str] = None):
        """Invalidate cache"""
        if key:
            self.cache_manager.invalidate(key)
        else:
            # Clear all cache
            self.cache_manager._cache.clear()
            self.cache_manager.get.cache_clear()
            logger.info("All caches cleared")

# =====================================================================================================================
# COMPATIBILITY WRAPPER
# =====================================================================================================================

# Singleton instance for backward compatibility
_ai_provider_service_instance = None

def get_ai_provider_service() -> AIProviderService:
    """Get or create singleton instance"""
    global _ai_provider_service_instance
    if _ai_provider_service_instance is None:
        _ai_provider_service_instance = AIProviderService()
    return _ai_provider_service_instance

# =====================================================================================================================
# EXPORTS
# =====================================================================================================================

__all__ = [
    'AIProviderService',
    'get_ai_provider_service',
    'Intent',
    'ServiceStatus',
    'ServiceRegistryEntry',
    'EntityExtraction',
    'MAIN_MENU'
]

# =====================================================================================================================
# END OF FILE
# =====================================================================================================================
