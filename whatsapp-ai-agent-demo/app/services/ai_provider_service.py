"""
File: app/services/ai_provider_service.py
Version: 20.0 - ENTERPRISE AI ORCHESTRATOR
Complete rewrite with Enterprise Service Registry, Intent Detection,
Entity Extraction, Parameter Mapping, Response Normalization, and Logging.

Architecture:
- PostgreSQL is the ONLY source of truth
- Deterministic routing before AI
- Enterprise entity extraction with compiled regex
- Alias-based parameter mapping
- Comprehensive service registry
- Multi-format response normalization
- WhatsApp validation and formatting
- Enterprise-grade logging
- Startup health validation
- Cache management
- AI fallback only when deterministic routing fails

Copyright (c) 2024 HPK Logistics
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Any, Dict, List, Optional, Callable, Awaitable, Union, Tuple, Set

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# =====================================================================================================================
# CONSTANTS
# =====================================================================================================================

WHATSAPP_MAX_MESSAGE_LENGTH = 4096
CACHE_TTL_SECONDS = 300
MAX_CACHE_SIZE = 1000
MENU_NUMBER_PATTERN = re.compile(r'^\s*([0-9])(?:[.)])\s*$')
DN_PATTERN = re.compile(r'(?<!\d)(\d{8,12})(?!\d)')
DN_PATTERN_WITH_SPACES = re.compile(r'(?<!\d)(\d{4}[\s-]*\d{4}[\s-]*\d{0,4})(?!\d)')

# =====================================================================================================================
# ENUMS
# =====================================================================================================================

class Intent(Enum):
    """Supported intents"""
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
    WAREHOUSE_REVENUE = "warehouse_revenue"
    PRODUCT_DASHBOARD = "product_dashboard"
    TOP_PRODUCTS = "top_products"
    NATIONAL_KPI = "national_kpi"
    NATIONAL_REVENUE = "national_revenue"
    PENDING_DNS = "pending_dns"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    TOP_PERFORMERS = "top_performers"
    HELP = "help"
    GENERAL_AI = "general_ai"
    UNKNOWN = "unknown"


class ServiceStatus(Enum):
    """Service health status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


# =====================================================================================================================
# DATA CLASSES
# =====================================================================================================================

@dataclass
class ServiceDefinition:
    """Complete service registry definition"""
    menu_number: str
    menu_name: str
    intent: Intent
    service_key: str
    service_file: str
    service_class: str
    method: str
    compatible_methods: List[str] = field(default_factory=list)
    required_entities: List[str] = field(default_factory=list)
    parameter_mapping: Dict[str, str] = field(default_factory=dict)
    alias_mapping: Dict[str, List[str]] = field(default_factory=dict)
    keywords: List[str] = field(default_factory=list)
    requires_ai: bool = False
    description: str = ""
    example_queries: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    health_status: ServiceStatus = ServiceStatus.UNKNOWN
    service_instance: Optional[Any] = None
    method_cache: Dict[str, Callable] = field(default_factory=dict)
    signature_cache: Dict[str, inspect.Signature] = field(default_factory=dict)


@dataclass
class EntityExtraction:
    """Complete entity extraction with all supported types"""
    dn_number: Optional[str] = None
    dealer_name: Optional[str] = None
    dealer_code: Optional[str] = None
    customer_name: Optional[str] = None
    customer_code: Optional[str] = None
    warehouse: Optional[str] = None
    warehouse_code: Optional[str] = None
    city: Optional[str] = None
    product: Optional[str] = None
    material_number: Optional[str] = None
    sales_office: Optional[str] = None
    sales_manager: Optional[str] = None
    division: Optional[str] = None
    pgi_status: Optional[str] = None
    pod_status: Optional[str] = None
    pending_status: Optional[str] = None
    revenue: Optional[float] = None
    units: Optional[int] = None
    date: Optional[str] = None
    date_range: Optional[Tuple[str, str]] = None
    kpi_type: Optional[str] = None
    month: Optional[str] = None
    year: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}
    
    def is_empty(self) -> bool:
        return not any(v is not None for v in self.__dict__.values())


@dataclass
class RoutingDecision:
    """Complete routing decision"""
    intent: Intent
    confidence: float
    service_definition: ServiceDefinition
    entity: EntityExtraction
    method: str
    mapped_parameters: Dict[str, Any]
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    menu_option: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "service_key": self.service_definition.service_key,
            "method": self.method,
            "entity": self.entity.to_dict(),
            "mapped_parameters": self.mapped_parameters,
            "requires_ai": self.requires_ai,
            "reason": self.reason,
            "original_message": self.original_message[:100],
            "menu_option": self.menu_option,
        }


@dataclass
class RequestContext:
    """Complete request context for enterprise logging"""
    request_id: str
    sender: Optional[str]
    message: str
    normalized_message: str
    intent: Optional[Intent] = None
    confidence: float = 0.0
    entities: Optional[EntityExtraction] = None
    mapped_parameters: Dict[str, Any] = field(default_factory=dict)
    service_key: Optional[str] = None
    method: Optional[str] = None
    database_time_ms: float = 0.0
    formatting_time_ms: float = 0.0
    total_time_ms: float = 0.0
    ai_used: bool = False
    response_size: int = 0
    success: bool = False
    error: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    conversation_state: str = "new"
    response_type: str = "text"
    meta_send_result: Optional[Dict[str, Any]] = None
    
    def elapsed_ms(self) -> float:
        return (time.time() - self.start_time) * 1000
    
    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "sender": self.sender,
            "message": self.message[:100],
            "normalized": self.normalized_message[:100],
            "conversation_state": self.conversation_state,
            "intent": self.intent.value if self.intent else None,
            "confidence": round(self.confidence, 2),
            "entities": self.entities.to_dict() if self.entities else {},
            "mapped_parameters": self.mapped_parameters,
            "service": self.service_key,
            "method": self.method,
            "db_time_ms": round(self.database_time_ms, 2),
            "formatting_ms": round(self.formatting_time_ms, 2),
            "total_ms": round(self.total_time_ms, 2),
            "ai_used": self.ai_used,
            "response_size": self.response_size,
            "response_type": self.response_type,
            "success": self.success,
            "error": self.error[:100] if self.error else None,
            "meta_send_result": self.meta_send_result,
        }


# =====================================================================================================================
# SEMANTIC ROUTER - OPTIONAL DEPENDENCY
# =====================================================================================================================

SEMANTIC_ROUTER_AVAILABLE = False
SEMANTIC_ROUTER_IMPORT_ERROR: Optional[Exception] = None
Route = None
SemanticRouter = None
HuggingFaceEncoder = None

try:
    from semantic_router import Route as _Route
    try:
        from semantic_router import SemanticRouter as _SemanticRouter
    except ImportError:
        try:
            from semantic_router import Router as _SemanticRouter
        except ImportError:
            from semantic_router.layer import RouteLayer as _SemanticRouter
    from semantic_router.encoders import HuggingFaceEncoder as _HuggingFaceEncoder

    Route = _Route
    SemanticRouter = _SemanticRouter
    HuggingFaceEncoder = _HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
except Exception as exc:
    SEMANTIC_ROUTER_IMPORT_ERROR = exc
    logger.warning("Semantic Router unavailable: %s", exc)

# =====================================================================================================================
# RAPIDFUZZ - OPTIONAL DEPENDENCY
# =====================================================================================================================

RAPIDFUZZ_AVAILABLE = False
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    logger.warning("RapidFuzz unavailable; fuzzy matching disabled")

# =====================================================================================================================
# SPACY - OPTIONAL DEPENDENCY
# =====================================================================================================================

SPACY_AVAILABLE = False
try:
    import spacy
    SPACY_AVAILABLE = True
    nlp = spacy.load("en_core_web_sm")
except ImportError:
    logger.warning("spaCy unavailable; NLP entity extraction disabled")

# =====================================================================================================================
# SERVICE IMPORTS WITH HEALTH CHECK
# =====================================================================================================================

SERVICE_IMPORT_STATUS: Dict[str, bool] = {}
SERVICE_IMPORT_ERRORS: Dict[str, str] = {}

def _import_service_instance(service_name: str, import_path: str, class_name: str, factory_method: Optional[str] = None) -> Optional[Any]:
    """Import and instantiate a service with health check"""
    try:
        module = __import__(import_path, fromlist=[class_name])
        if factory_method:
            factory = getattr(module, factory_method)
            instance = factory()
        else:
            service_class = getattr(module, class_name)
            instance = service_class()
        SERVICE_IMPORT_STATUS[service_name] = True
        logger.info(f"✅ {service_name} initialized successfully")
        return instance
    except Exception as exc:
        SERVICE_IMPORT_STATUS[service_name] = False
        SERVICE_IMPORT_ERRORS[service_name] = str(exc)
        logger.error(f"❌ Failed to initialize {service_name}: {exc}")
        return None

# DN Analysis
DN_ANALYSIS_SERVICE = None
DN_ANALYSIS_AVAILABLE = False
try:
    DN_ANALYSIS_SERVICE = _import_service_instance(
        "dn_analysis",
        "app.services.dn_analysis",
        "DNAnalysisService"
    )
    DN_ANALYSIS_AVAILABLE = DN_ANALYSIS_SERVICE is not None
except Exception as exc:
    logger.error(f"❌ DN Analysis Service import failed: {exc}")

# Dealer Analytics
DEALER_ANALYTICS_SERVICE = None
DEALER_ANALYTICS_AVAILABLE = False
try:
    DEALER_ANALYTICS_SERVICE = _import_service_instance(
        "dealer_analytics",
        "app.services.dealer_analytics_service",
        "DealerAnalyticsService",
        "get_dealer_analytics_service"
    )
    DEALER_ANALYTICS_AVAILABLE = DEALER_ANALYTICS_SERVICE is not None
except Exception as exc:
    logger.error(f"❌ Dealer Analytics Service import failed: {exc}")

# City Analytics
CITY_ANALYTICS_SERVICE = None
CITY_ANALYTICS_AVAILABLE = False
try:
    CITY_ANALYTICS_SERVICE = _import_service_instance(
        "city_analytics",
        "app.services.city_service",
        "CityAnalyticsService",
        "get_city_analytics_service"
    )
    CITY_ANALYTICS_AVAILABLE = CITY_ANALYTICS_SERVICE is not None
except Exception as exc:
    logger.error(f"❌ City Analytics Service import failed: {exc}")

# Warehouse Service
WAREHOUSE_SERVICE = None
WAREHOUSE_AVAILABLE = False
try:
    # Try warehouse_service first, fallback to dn_analysis for compatibility
    WAREHOUSE_SERVICE = _import_service_instance(
        "warehouse",
        "app.services.warehouse_service",
        "WarehouseAnalyticsService"
    )
    if WAREHOUSE_SERVICE is None:
        WAREHOUSE_SERVICE = DN_ANALYSIS_SERVICE
    WAREHOUSE_AVAILABLE = WAREHOUSE_SERVICE is not None
except Exception as exc:
    logger.warning(f"⚠️ Warehouse Service import failed: {exc}")
    WAREHOUSE_SERVICE = DN_ANALYSIS_SERVICE
    WAREHOUSE_AVAILABLE = WAREHOUSE_SERVICE is not None

# Product Service
PRODUCT_SERVICE = None
PRODUCT_AVAILABLE = False
try:
    PRODUCT_SERVICE = _import_service_instance(
        "product",
        "app.services.product_service",
        "ProductService"
    )
    PRODUCT_AVAILABLE = PRODUCT_SERVICE is not None
except Exception as exc:
    logger.error(f"❌ Product Service import failed: {exc}")

# National KPI
NATIONAL_KPI_SERVICE = None
NATIONAL_KPI_AVAILABLE = False
try:
    NATIONAL_KPI_SERVICE = _import_service_instance(
        "national_kpi",
        "app.services.national_kpi_service",
        "NationalKPIService"
    )
    NATIONAL_KPI_AVAILABLE = NATIONAL_KPI_SERVICE is not None
except Exception as exc:
    logger.error(f"❌ National KPI Service import failed: {exc}")

# Groq Service
GROQ_SERVICE = None
GROQ_AVAILABLE = False
try:
    GROQ_SERVICE = _import_service_instance(
        "groq",
        "app.services.groq_service",
        "GroqService"
    )
    GROQ_AVAILABLE = GROQ_SERVICE is not None
except Exception as exc:
    logger.error(f"❌ Groq Service import failed: {exc}")


# =====================================================================================================================
# MAIN MENU
# =====================================================================================================================

def get_main_menu() -> str:
    """Return the main menu"""
    return """🤖 *HPK Logistics AI Assistant*

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

*Reply with menu number.*"""


def get_help_menu() -> str:
    """Return the help menu"""
    return """🤖 *HPK Logistics AI Assistant - Help*

*How to use:*
• Type a *menu number* (0-9) to navigate
• Type *menu* to see all options
• Type *help* to see this message

*Quick Commands:*
• *DN number* - Track a delivery note
• *City name* - View city dashboard
• *Dealer name* - View dealer dashboard
• *pending* - View pending deliveries

*Available Services:*
0️⃣ Main Menu
1️⃣ DN Delivery
2️⃣ Dealer Analytics
3️⃣ City Analytics
4️⃣ Warehouse Dashboard
5️⃣ Product Analytics
6️⃣ National KPI
7️⃣ Pending DN
8️⃣ Top Performers
9️⃣ AI Query

Type *menu* to see the full menu with descriptions."""


# =====================================================================================================================
# ENTERPRISE ENTITY EXTRACTION ENGINE
# =====================================================================================================================

class EntityExtractionEngine:
    """Enterprise Entity Extraction Engine with compiled regex patterns"""
    
    def __init__(self):
        self._patterns = self._compile_patterns()
        self._city_names = self._load_city_names()
        self._dealer_suffixes = self._load_dealer_suffixes()
        self._cache = TTLCache(maxsize=1000, ttl=300)
        self._compiled_regex = True
    
    def _compile_patterns(self) -> Dict[str, re.Pattern]:
        """Compile all regex patterns once at startup"""
        return {
            # DN Patterns
            "dn": re.compile(r'(?<!\d)(\d{8,12})(?!\d)'),
            "dn_spaced": re.compile(r'(?<!\d)(\d{4}[\s-]*\d{4}[\s-]*\d{0,4})(?!\d)'),
            "dn_alt": re.compile(r'(?:dn|delivery note|delivery|order|tracking)\s*[#:]\s*(\d{8,12})', re.IGNORECASE),
            
            # Dealer Patterns
            "dealer": re.compile(
                r'(?:dealer|show|get|view)\s+([\w&.\'\- ]{2,}?(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation|store|shop)(?:[\w&.\'\- ]*)?)',
                re.IGNORECASE
            ),
            "dealer_name": re.compile(
                r'([\w&.\'\- ]{2,}?(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation|store|shop)(?:[\w&.\'\- ]*)?)',
                re.IGNORECASE
            ),
            "dealer_code": re.compile(r'(?:dealer code|code|id|dc)\s*[#:]\s*([A-Z0-9]{3,})', re.IGNORECASE),
            
            # Customer Patterns
            "customer_name": re.compile(r'(?:customer|cust)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "customer_code": re.compile(r'(?:customer code|cc|sold to)\s*[#:]\s*([A-Z0-9]{3,})', re.IGNORECASE),
            "sold_to_party": re.compile(r'(?:sold to party|sold-to|stp)\s*[#:]\s*([A-Z0-9]{3,})', re.IGNORECASE),
            
            # Warehouse Patterns
            "warehouse": re.compile(r'(?:warehouse|depot|wh|storage)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "warehouse_code": re.compile(r'(?:warehouse code|wh code|whc)\s*[#:]\s*([A-Z0-9]{3})', re.IGNORECASE),
            
            # City Patterns
            "city": re.compile(r'\b(' + '|'.join(CITY_NAMES) + r')\b', re.IGNORECASE),
            "city_alt": re.compile(r'(?:city|town|location)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            
            # Product Patterns
            "product": re.compile(r'(?:product|prod|item|sku)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "material": re.compile(r'(?:material|mat|mtl)\s*[#:]\s*([A-Z0-9]{6,})', re.IGNORECASE),
            "model": re.compile(r'(?:model|mod)\s+([A-Z0-9\-]{3,})', re.IGNORECASE),
            
            # Office Patterns
            "sales_office": re.compile(r'(?:sales office|office|so)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "sales_manager": re.compile(r'(?:sales manager|manager|sm)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "division": re.compile(r'(?:division|div)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            
            # Status Patterns
            "pgi_status": re.compile(r'(?:pgi status|goods issue)\s+(pending|completed|done|yes|no)', re.IGNORECASE),
            "pod_status": re.compile(r'(?:pod status|proof of delivery)\s+(pending|completed|done|yes|no)', re.IGNORECASE),
            "pending_status": re.compile(r'(?:pending|delay|overdue|missed)\s+(dn|delivery|order)', re.IGNORECASE),
            
            # Metric Patterns
            "revenue": re.compile(r'(?:revenue|rev|amount)\s*[#:]\s*([\d,]+\.?[\d]*)', re.IGNORECASE),
            "units": re.compile(r'(?:units|qty|quantity)\s*[#:]\s*(\d+)', re.IGNORECASE),
            
            # Date Patterns
            "date": re.compile(r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})'),
            "date_range": re.compile(
                r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})\s*(?:to|until|through|and)\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})',
                re.IGNORECASE
            ),
            "month": re.compile(r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})', re.IGNORECASE),
            "year": re.compile(r'\b(20\d{2})\b'),
            
            # KPI Patterns
            "kpi": re.compile(r'(?:kpi|key performance indicator)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
        }
    
    def _load_city_names(self) -> Set[str]:
        """Load city names for detection"""
        return {
            "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
            "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
            "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
            "dera ghazi khan", "gwadar", "rahim yar khan", "chakwal", "mansehra",
        }
    
    def _load_dealer_suffixes(self) -> Set[str]:
        """Load dealer suffixes for detection"""
        return {
            "electronics", "traders", "distributors", "foods", "group", "pvt", "ltd",
            "sons", "brothers", "enterprises", "company", "corporation", "store", "shop",
            "centre", "center", "solutions", "services"
        }
    
    def extract(self, message: str) -> EntityExtraction:
        """Extract all entities from message with caching"""
        cache_key = message.lower().strip()
        cached = self._cache.get(cache_key)
        if cached:
            return cached
        
        entities = EntityExtraction()
        message_lower = message.lower()
        
        # 1. Extract DN (multiple patterns)
        for pattern_name in ["dn", "dn_alt"]:
            if pattern_name in self._patterns:
                match = self._patterns[pattern_name].search(message)
                if match:
                    entities.dn_number = match.group(1)
                    break
        
        if not entities.dn_number:
            match = self._patterns["dn_spaced"].search(message)
            if match:
                candidate = re.sub(r"[\s-]", "", match.group(1))
                if 8 <= len(candidate) <= 12:
                    entities.dn_number = candidate
        
        # 2. Extract Dealer (multiple patterns)
        for pattern_name in ["dealer", "dealer_name"]:
            if pattern_name in self._patterns:
                match = self._patterns[pattern_name].search(message)
                if match:
                    dealer_name = match.group(1).strip()
                    if len(dealer_name) > 2:
                        entities.dealer_name = dealer_name
                        break
        
        # 3. Extract Dealer Code
        match = self._patterns["dealer_code"].search(message)
        if match:
            entities.dealer_code = match.group(1)
        
        # 4. Extract Customer
        match = self._patterns["customer_name"].search(message)
        if match:
            entities.customer_name = match.group(1).strip()
        
        match = self._patterns["customer_code"].search(message)
        if match:
            entities.customer_code = match.group(1)
        
        match = self._patterns["sold_to_party"].search(message)
        if match:
            entities.customer_code = match.group(1)
        
        # 5. Extract Warehouse
        for pattern_name in ["warehouse", "warehouse_code"]:
            if pattern_name in self._patterns:
                match = self._patterns[pattern_name].search(message)
                if match:
                    if pattern_name == "warehouse":
                        entities.warehouse = match.group(1).strip()
                    else:
                        entities.warehouse_code = match.group(1)
                    break
        
        # 6. Extract City
        match = self._patterns["city"].search(message)
        if match:
            entities.city = match.group(1).capitalize()
        else:
            match = self._patterns["city_alt"].search(message)
            if match:
                entities.city = match.group(1).capitalize()
        
        # 7. Extract Product
        match = self._patterns["product"].search(message)
        if match:
            entities.product = match.group(1).strip()
        
        # 8. Extract Material
        match = self._patterns["material"].search(message)
        if match:
            entities.material_number = match.group(1)
        
        # 9. Extract Model
        if not entities.product:
            match = self._patterns["model"].search(message)
            if match:
                entities.product = match.group(1)
        
        # 10. Extract Sales Office
        match = self._patterns["sales_office"].search(message)
        if match:
            entities.sales_office = match.group(1).strip()
        
        # 11. Extract Sales Manager
        match = self._patterns["sales_manager"].search(message)
        if match:
            entities.sales_manager = match.group(1).strip()
        
        # 12. Extract Division
        match = self._patterns["division"].search(message)
        if match:
            entities.division = match.group(1).strip()
        
        # 13. Extract Status
        match = self._patterns["pgi_status"].search(message)
        if match:
            entities.pgi_status = match.group(1).lower()
        
        match = self._patterns["pod_status"].search(message)
        if match:
            entities.pod_status = match.group(1).lower()
        
        match = self._patterns["pending_status"].search(message)
        if match:
            entities.pending_status = match.group(1).lower()
        
        # 14. Extract Revenue
        match = self._patterns["revenue"].search(message)
        if match:
            try:
                entities.revenue = float(match.group(1).replace(',', ''))
            except ValueError:
                pass
        
        # 15. Extract Units
        match = self._patterns["units"].search(message)
        if match:
            try:
                entities.units = int(match.group(1))
            except ValueError:
                pass
        
        # 16. Extract Date
        match = self._patterns["date"].search(message)
        if match:
            entities.date = match.group(1)
        
        # 17. Extract Date Range
        match = self._patterns["date_range"].search(message)
        if match:
            entities.date_range = (match.group(1), match.group(2))
        
        # 18. Extract Month/Year
        match = self._patterns["month"].search(message)
        if match:
            entities.month = match.group(1).capitalize()
            entities.year = match.group(2)
        
        if not entities.year:
            match = self._patterns["year"].search(message)
            if match:
                entities.year = match.group(1)
        
        # 19. Extract KPI
        match = self._patterns["kpi"].search(message)
        if match:
            entities.kpi_type = match.group(1).strip()
        
        # 20. Fuzzy matching using RapidFuzz (if available)
        if RAPIDFUZZ_AVAILABLE and not entities.city and not entities.dealer_name:
            self._apply_fuzzy_matching(message, entities)
        
        # 21. SpaCy NLP (if available)
        if SPACY_AVAILABLE:
            self._apply_spacy_extraction(message, entities)
        
        self._cache[cache_key] = entities
        return entities
    
    def _apply_fuzzy_matching(self, message: str, entities: EntityExtraction) -> None:
        """Apply fuzzy matching for city and dealer detection"""
        try:
            # Fuzzy match city
            city_matches = process.extract(message.lower(), self._city_names, scorer=fuzz.WRatio, limit=1)
            if city_matches and city_matches[0][1] >= 85:
                entities.city = city_matches[0][0].capitalize()
            
            # Fuzzy match dealer suffixes
            if not entities.dealer_name:
                for suffix in self._dealer_suffixes:
                    if suffix in message.lower() and len(message) > 10:
                        # Try to extract dealer name
                        words = message.split()
                        for i, word in enumerate(words):
                            if suffix in word.lower() and i > 0:
                                entities.dealer_name = " ".join(words[:i+1])
                                break
                        if entities.dealer_name:
                            break
        except Exception:
            pass
    
    def _apply_spacy_extraction(self, message: str, entities: EntityExtraction) -> None:
        """Apply spaCy NLP for entity extraction"""
        try:
            doc = nlp(message)
            
            # Extract locations
            if not entities.city:
                for ent in doc.ents:
                    if ent.label_ == "GPE" and ent.text.lower() in self._city_names:
                        entities.city = ent.text.capitalize()
                        break
            
            # Extract organizations
            if not entities.dealer_name:
                for ent in doc.ents:
                    if ent.label_ == "ORG" and len(ent.text) > 3:
                        entities.dealer_name = ent.text
                        break
        except Exception:
            pass


CITY_NAMES = (
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
    "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
    "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
    "dera ghazi khan", "gwadar", "rahim yar khan", "chakwal"
)


# =====================================================================================================================
# ENTERPRISE INTENT DETECTION ENGINE
# =====================================================================================================================

class IntentDetectionEngine:
    """Enterprise Intent Detection with deterministic pipeline"""
    
    def __init__(self, registry: ServiceRegistry):
        self.registry = registry
        self.entity_extractor = EntityExtractionEngine()
        self._router = None
        self._router_initialized = False
        self._router_lock = threading.Lock()
        self._menu_triggers = {
            "menu", "main menu", "options", "start", "back", "home", "help",
            "0", "hello", "hi", "hey", "salam", "assalam o alaikum"
        }
        
        # Keyword-based intents
        self._keyword_intents = {
            "dn": Intent.DN_LOOKUP,
            "delivery note": Intent.DN_LOOKUP,
            "track": Intent.DN_LOOKUP,
            "check delivery": Intent.DN_LOOKUP,
            "dealer": Intent.DEALER_DASHBOARD,
            "distributor": Intent.DEALER_DASHBOARD,
            "partner": Intent.DEALER_DASHBOARD,
            "city": Intent.CITY_DASHBOARD,
            "town": Intent.CITY_DASHBOARD,
            "warehouse": Intent.WAREHOUSE_DASHBOARD,
            "depot": Intent.WAREHOUSE_DASHBOARD,
            "product": Intent.PRODUCT_DASHBOARD,
            "material": Intent.PRODUCT_DASHBOARD,
            "item": Intent.PRODUCT_DASHBOARD,
            "pending": Intent.PENDING_DNS,
            "delay": Intent.PENDING_DNS,
            "overdue": Intent.PENDING_DNS,
            "top": Intent.TOP_PERFORMERS,
            "best": Intent.TOP_PERFORMERS,
            "leaderboard": Intent.TOP_PERFORMERS,
            "performers": Intent.TOP_PERFORMERS,
            "national": Intent.NATIONAL_KPI,
            "kpi": Intent.NATIONAL_KPI,
            "overall": Intent.NATIONAL_KPI,
            "company": Intent.NATIONAL_KPI,
        }
        
        # Rule-based intent patterns
        self._intent_rules = [
            (re.compile(r'\b(?:pending\s+pod|proof of delivery pending)\b', re.IGNORECASE), Intent.PENDING_POD),
            (re.compile(r'\b(?:pending\s+pgi|goods issue pending)\b', re.IGNORECASE), Intent.PENDING_PGI),
            (re.compile(r'\b(?:top|best)\s+performers?\b|\bleaderboard\b', re.IGNORECASE), Intent.TOP_PERFORMERS),
            (re.compile(r'\b(?:national kpi|overall performance|executive dashboard)\b', re.IGNORECASE), Intent.NATIONAL_KPI),
            (re.compile(r'\b(?:dealer|distributor)\s+(?:revenue|sales)\b', re.IGNORECASE), Intent.DEALER_REVENUE),
            (re.compile(r'\bcit(?:y|ies)\s+(?:revenue|sales)\b', re.IGNORECASE), Intent.CITY_REVENUE),
            (re.compile(r'\b(?:dn|delivery)\s+history\b', re.IGNORECASE), Intent.DN_HISTORY),
            (re.compile(r'\b(?:top|best)\s+products?\b', re.IGNORECASE), Intent.TOP_PRODUCTS),
            (re.compile(r'\bwhat\s+is\s+(?:the\s+)?issue\b', re.IGNORECASE), Intent.GENERAL_AI),
            (re.compile(r'\bhelp\b|\bhow to\b|\bguide\b', re.IGNORECASE), Intent.HELP),
        ]
    
    def _initialize_semantic_router(self) -> None:
        """Lazy initialize semantic router"""
        if self._router_initialized:
            return
        
        with self._router_lock:
            if self._router_initialized:
                return
            
            self._router_initialized = True
            
            if not SEMANTIC_ROUTER_AVAILABLE:
                return
            
            try:
                routes = []
                for intent, definition in self.registry._definitions.items():
                    if definition.example_queries:
                        routes.append(Route(
                            name=intent.value,
                            utterances=definition.example_queries + [
                                f"{definition.menu_name.lower()} dashboard",
                                f"show {definition.menu_name.lower()}",
                            ]
                        ))
                
                if routes:
                    encoder = HuggingFaceEncoder()
                    try:
                        self._router = SemanticRouter(encoder=encoder, routes=routes, auto_sync="local")
                    except TypeError:
                        self._router = SemanticRouter(encoder=encoder, routes=routes)
                    logger.info(f"✅ Semantic Router initialized with {len(routes)} routes")
            except Exception as exc:
                logger.warning(f"⚠️ Semantic Router initialization failed: {exc}")
                self._router = None
    
    def _semantic_intent(self, message: str) -> Tuple[Optional[str], float]:
        """Get intent from semantic router"""
        self._initialize_semantic_router()
        if self._router is None:
            return None, 0.0
        
        try:
            result = self._router(message) if callable(self._router) else self._router.route(message)
            if result is None:
                return None, 0.0
            return getattr(result, "name", None), float(getattr(result, "score", 1.0) or 0.0)
        except Exception:
            logger.exception("Semantic routing failed")
            return None, 0.0
    
    def _keyword_intent(self, message: str) -> Optional[Intent]:
        """Detect intent based on keywords"""
        message_lower = message.lower()
        
        for keyword, intent in self._keyword_intents.items():
            if keyword in message_lower:
                return intent
        
        if "pending" in message_lower and ("dn" in message_lower or "delivery" in message_lower):
            return Intent.PENDING_DNS
        if "top" in message_lower and "dealer" in message_lower:
            return Intent.DEALER_DASHBOARD
        if "top" in message_lower and "city" in message_lower:
            return Intent.CITY_DASHBOARD
        
        return None
    
    def detect_intent(self, message: str, entities: EntityExtraction) -> Tuple[Intent, float]:
        """Detect intent using deterministic pipeline"""
        message_lower = message.lower().strip()
        
        # 1. Check for menu
        if message_lower in self._menu_triggers:
            return Intent.MENU, 1.0
        
        # 2. Check for DN
        if entities.dn_number:
            if re.match(r'^\s*\d{8,12}\s*$', message) or f"dn {entities.dn_number}" in message_lower:
                return Intent.DN_LOOKUP, 0.95
            return Intent.DN_LOOKUP, 0.85
        
        # 3. Check for menu number
        if MENU_NUMBER_PATTERN.match(message):
            return Intent.MENU, 1.0
        
        # 4. Check keyword-based intents
        keyword_intent = self._keyword_intent(message)
        if keyword_intent:
            return keyword_intent, 0.85
        
        # 5. Check rule-based intents
        for pattern, intent in self._intent_rules:
            if pattern.search(message):
                return intent, 0.9
        
        # 6. Check entity-based intents
        if entities.dealer_name or entities.dealer_code:
            if "revenue" in message_lower or "sales" in message_lower:
                return Intent.DEALER_REVENUE, 0.8
            if "pending" in message_lower:
                return Intent.DEALER_PENDING, 0.8
            return Intent.DEALER_DASHBOARD, 0.8
        
        if entities.city:
            if "top" in message_lower or "best" in message_lower:
                return Intent.TOP_PERFORMERS, 0.75
            if "revenue" in message_lower or "sales" in message_lower:
                return Intent.CITY_REVENUE, 0.75
            if "pending" in message_lower:
                return Intent.CITY_PENDING, 0.75
            return Intent.CITY_DASHBOARD, 0.8
        
        if entities.warehouse or entities.warehouse_code:
            if "revenue" in message_lower:
                return Intent.WAREHOUSE_REVENUE, 0.75
            if "pending" in message_lower:
                return Intent.WAREHOUSE_PENDING, 0.75
            return Intent.WAREHOUSE_DASHBOARD, 0.8
        
        if entities.product or entities.material_number:
            if "top" in message_lower:
                return Intent.TOP_PRODUCTS, 0.75
            return Intent.PRODUCT_DASHBOARD, 0.8
        
        # 7. Semantic Router
        semantic_intent, confidence = self._semantic_intent(message)
        if semantic_intent and confidence >= 0.4:
            try:
                return Intent(semantic_intent), confidence
            except ValueError:
                pass
        
        # 8. Fallback to general AI
        return Intent.GENERAL_AI, 0.3


# =====================================================================================================================
# ENTERPRISE PARAMETER MAPPING ENGINE
# =====================================================================================================================

class ParameterMapper:
    """Enterprise Parameter Mapping with comprehensive alias support"""
    
    ALIAS_MAPPING = {
        "dn_number": ["dn", "dn_number", "dn_no", "delivery_note", "delivery", "order", "tracking"],
        "dealer_name": ["dealer", "dealer_name", "customer_name", "sold_to_party", "stp", "customer", "distributor", "partner", "retailer"],
        "dealer_code": ["dealer_code", "code", "id", "dc", "customer_code"],
        "warehouse": ["warehouse", "warehouse_name", "wh", "depot", "storage"],
        "warehouse_code": ["warehouse_code", "wh_code", "whc"],
        "city": ["city", "city_name", "town", "location", "ship_to_city"],
        "product": ["product", "product_name", "item", "sku", "model"],
        "material_number": ["material", "material_number", "mat", "mtl", "part"],
        "sales_office": ["sales_office", "office", "so"],
        "sales_manager": ["sales_manager", "manager", "sm"],
        "division": ["division", "div"],
        "pgi_status": ["pgi_status", "goods_issue", "gi"],
        "pod_status": ["pod_status", "proof_of_delivery", "pod"],
        "pending_status": ["pending_status", "pending", "delay", "overdue"],
        "revenue": ["revenue", "rev", "amount", "total"],
        "units": ["units", "qty", "quantity", "volume"],
        "date": ["date", "created", "delivery_date"],
        "date_range": ["date_range", "range", "between"],
        "month": ["month", "period"],
        "year": ["year"],
        "kpi_type": ["kpi", "kpi_type", "metric"],
    }
    
    def __init__(self):
        self._reverse_mapping = self._build_reverse_mapping()
    
    def _build_reverse_mapping(self) -> Dict[str, str]:
        """Build reverse alias mapping"""
        mapping = {}
        for canonical, aliases in self.ALIAS_MAPPING.items():
            for alias in aliases:
                mapping[alias] = canonical
        return mapping
    
    def map_parameters(self, method: Callable, entities: EntityExtraction) -> Dict[str, Any]:
        """Map entity values to method parameters"""
        sig = inspect.signature(method)
        params = {}
        entity_dict = entities.to_dict()
        
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            
            if param.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
                continue
            
            # Direct match
            if param_name in entity_dict:
                params[param_name] = entity_dict[param_name]
                continue
            
            # Alias mapping
            param_lower = param_name.lower()
            if param_lower in self._reverse_mapping:
                canonical = self._reverse_mapping[param_lower]
                if canonical in entity_dict:
                    params[param_name] = entity_dict[canonical]
                    continue
            
            # Try all aliases
            for canonical, aliases in self.ALIAS_MAPPING.items():
                if param_lower in aliases or param_lower == canonical:
                    if canonical in entity_dict:
                        params[param_name] = entity_dict[canonical]
                        break
                    for alias in aliases:
                        if alias in entity_dict:
                            params[param_name] = entity_dict[alias]
                            break
                    if param_name in params:
                        break
            
            # Required parameter with no default
            if param_name not in params and param.default == inspect.Parameter.empty:
                logger.warning(f"⚠️ Required parameter '{param_name}' not found in entities")
        
        return params


# =====================================================================================================================
# ENTERPRISE RESPONSE NORMALIZER
# =====================================================================================================================

class ResponseNormalizer:
    """Enterprise Response Normalizer with priority-based extraction"""
    
    def __init__(self):
        self._formatters = [
            self._extract_whatsapp_message,
            self._extract_formatted_response,
            self._extract_message,
            self._extract_response,
            self._extract_data_to_whatsapp_message,
            self._extract_data_str,
            self._fallback_formatter,
        ]
    
    def normalize(self, result: Any) -> str:
        """Normalize any service return type to WhatsApp-safe string"""
        if result is None:
            return "No response from service. Please try again."
        
        # Handle Pydantic errors gracefully
        if hasattr(result, '__class__') and 'ValidationError' in str(result.__class__):
            return "⚠️ Invalid input format. Please provide the correct data format."
        
        for formatter in self._formatters:
            try:
                formatted = formatter(result)
                if formatted and isinstance(formatted, str) and formatted.strip():
                    return self._clean_response(formatted)
            except Exception:
                continue
        
        return str(result) if result else "No response from service. Please try again."
    
    def _extract_whatsapp_message(self, result: Any) -> Optional[str]:
        """Extract whatsapp_message field"""
        if isinstance(result, dict) and "whatsapp_message" in result:
            msg = result["whatsapp_message"]
            if isinstance(msg, str):
                return msg
            elif isinstance(msg, dict):
                return str(msg) if msg else None
        return None
    
    def _extract_formatted_response(self, result: Any) -> Optional[str]:
        """Extract formatted_response field"""
        if isinstance(result, dict) and "formatted_response" in result:
            return str(result["formatted_response"])
        return None
    
    def _extract_message(self, result: Any) -> Optional[str]:
        """Extract message field"""
        if isinstance(result, dict) and "message" in result:
            return str(result["message"])
        return None
    
    def _extract_response(self, result: Any) -> Optional[str]:
        """Extract response field"""
        if isinstance(result, dict) and "response" in result:
            return str(result["response"])
        return None
    
    def _extract_data_to_whatsapp_message(self, result: Any) -> Optional[str]:
        """Extract data and call to_whatsapp_message"""
        if isinstance(result, dict):
            data = result.get("data")
            if data and hasattr(data, "to_whatsapp_message"):
                try:
                    return data.to_whatsapp_message()
                except Exception:
                    pass
            if data and hasattr(data, "__str__"):
                return str(data)
        return None
    
    def _extract_data_str(self, result: Any) -> Optional[str]:
        """Extract data and convert to string"""
        if isinstance(result, dict):
            data = result.get("data")
            if data and hasattr(data, "__str__"):
                return str(data)
        return None
    
    def _fallback_formatter(self, result: Any) -> Optional[str]:
        """Fallback formatter"""
        if hasattr(result, "__str__"):
            return str(result)
        return None
    
    def _clean_response(self, response: str) -> str:
        """Clean and validate response for WhatsApp"""
        if not response:
            return "No response from service."
        
        response = re.sub(r'\s+', ' ', response).strip()
        
        if len(response) > WHATSAPP_MAX_MESSAGE_LENGTH:
            response = response[:WHATSAPP_MAX_MESSAGE_LENGTH - 100] + "\n\n... (message truncated)"
        
        return response


# =====================================================================================================================
# ENTERPRISE WHATSAPP VALIDATOR
# =====================================================================================================================

class WhatsAppValidator:
    """Enterprise WhatsApp message validator"""
    
    MAX_MESSAGE_LENGTH = 4096
    
    @classmethod
    def validate(cls, message: str) -> bool:
        """Validate message for WhatsApp"""
        if message is None:
            return False
        if not isinstance(message, str):
            return False
        if not message.strip():
            return False
        if len(message) > cls.MAX_MESSAGE_LENGTH:
            logger.warning(f"Message exceeds {cls.MAX_MESSAGE_LENGTH} chars: {len(message)}")
            return False
        return True
    
    @classmethod
    def prepare(cls, message: str) -> str:
        """Prepare message for WhatsApp"""
        if not message:
            return "No response from service."
        
        # Remove unsupported markdown
        message = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', message)
        message = re.sub(r'\_\_([^_]+)\_\_', r'_\1_', message)
        
        cleaned = re.sub(r'\s+', ' ', message).strip()
        
        if len(cleaned) > cls.MAX_MESSAGE_LENGTH:
            cleaned = cleaned[:cls.MAX_MESSAGE_LENGTH - 100] + "\n\n... (message truncated)"
        
        return cleaned


# =====================================================================================================================
# ENTERPRISE LOGGING
# =====================================================================================================================

class EnterpriseLogger:
    """Enterprise logging with structured output"""
    
    @classmethod
    def log_request(cls, context: RequestContext) -> None:
        """Log complete request context"""
        log_data = context.to_log_dict()
        
        status_icon = "✅" if context.success else "❌"
        log_message = (
            f"[{context.request_id}] {status_icon} "
            f"Intent: {log_data['intent']} "
            f"Service: {log_data['service']}.{log_data['method']} "
            f"Total: {log_data['total_ms']}ms "
            f"DB: {log_data['db_time_ms']}ms "
            f"Size: {log_data['response_size']} chars "
            f"State: {log_data['conversation_state']}"
        )
        
        if context.success:
            logger.info(log_message, extra={"context": log_data})
        else:
            logger.error(log_message, extra={"context": log_data})
    
    @classmethod
    def log_startup(cls, status: Dict[str, Any]) -> None:
        """Log startup status"""
        logger.info("=" * 80)
        logger.info("🚀 AIProviderService Startup")
        logger.info(f"  Status: {'✅ Healthy' if status['healthy'] else '⚠️ Degraded'}")
        logger.info(f"  Services: {status['available_services']}/{status['total_services']}")
        logger.info(f"  Errors: {len(status['errors'])}")
        logger.info("=" * 80)
        
        if status['errors']:
            logger.warning("⚠️ Startup Errors:")
            for error in status['errors']:
                logger.warning(f"  - {error}")


# =====================================================================================================================
# ENTERPRISE SERVICE REGISTRY
# =====================================================================================================================

class ServiceRegistry:
    """Enterprise Service Registry with complete service definitions"""
    
    def __init__(self):
        self._definitions: Dict[Intent, ServiceDefinition] = {}
        self._method_cache: Dict[str, Callable] = {}
        self._signature_cache: Dict[str, inspect.Signature] = {}
        self._lock = threading.RLock()
        self._initialize_registry()
    
    def _initialize_registry(self) -> None:
        """Initialize complete service registry"""
        self._definitions = {
            Intent.MENU: ServiceDefinition(
                menu_number="0",
                menu_name="Main Menu",
                intent=Intent.MENU,
                service_key="menu_service",
                service_file="app/services/ai_provider_service.py",
                service_class="AIProviderService",
                method="show_main_menu",
                compatible_methods=[],
                required_entities=[],
                parameter_mapping={},
                alias_mapping={},
                keywords=["menu", "main", "home", "start"],
                requires_ai=False,
                description="Show main menu",
                example_queries=["menu", "help", "0"]
            ),
            
            Intent.DN_LOOKUP: ServiceDefinition(
                menu_number="1",
                menu_name="DN Lookup",
                intent=Intent.DN_LOOKUP,
                service_key="dn_analysis",
                service_file="app/services/dn_analysis.py",
                service_class="DNAnalysisService",
                method="get_dn_dashboard",
                compatible_methods=["get_dn_details", "get_dn_status", "get_dn_info"],
                required_entities=["dn_number"],
                parameter_mapping={"dn_no": "dn_number", "dn": "dn_number"},
                alias_mapping={"dn": ["dn", "dn_number", "dn_no", "delivery_note"]},
                keywords=["dn", "delivery", "tracking", "order"],
                requires_ai=False,
                description="Look up delivery note details",
                example_queries=["Track DN 6243698820", "6243698820", "DN 6243698820 status"]
            ),
            
            Intent.DN_DASHBOARD: ServiceDefinition(
                menu_number="1",
                menu_name="DN Dashboard",
                intent=Intent.DN_DASHBOARD,
                service_key="dn_analysis",
                service_file="app/services/dn_analysis.py",
                service_class="DNAnalysisService",
                method="get_dn_dashboard",
                compatible_methods=["get_dn_details", "get_dn_status"],
                required_entities=["dn_number"],
                parameter_mapping={"dn_no": "dn_number", "dn": "dn_number"},
                alias_mapping={"dn": ["dn", "dn_number", "dn_no", "delivery_note"]},
                keywords=["dn", "dashboard", "stats", "status"],
                requires_ai=False,
                description="View DN analytics dashboard",
                example_queries=["Show DN dashboard", "DN 6243698820 stats"]
            ),
            
            Intent.DN_HISTORY: ServiceDefinition(
                menu_number="1",
                menu_name="DN History",
                intent=Intent.DN_HISTORY,
                service_key="dn_analysis",
                service_file="app/services/dn_analysis.py",
                service_class="DNAnalysisService",
                method="get_dn_history",
                compatible_methods=["get_dn_history"],
                required_entities=["dn_number"],
                parameter_mapping={"dn_no": "dn_number", "dn": "dn_number"},
                alias_mapping={"dn": ["dn", "dn_number", "dn_no", "delivery_note"]},
                keywords=["history", "past", "previous"],
                requires_ai=False,
                description="View DN history timeline",
                example_queries=["DN 6243698820 history", "Delivery history for 6243698820"]
            ),
            
            Intent.DEALER_DASHBOARD: ServiceDefinition(
                menu_number="2",
                menu_name="Dealer Dashboard",
                intent=Intent.DEALER_DASHBOARD,
                service_key="dealer_analytics",
                service_file="app/services/dealer_analytics_service.py",
                service_class="DealerAnalyticsService",
                method="get_dealer_dashboard",
                compatible_methods=["get_dealer_dashboard", "get_dealer_profile"],
                required_entities=["dealer_name"],
                parameter_mapping={"dealer_name": "dealer_name", "dealer": "dealer_name"},
                alias_mapping={"dealer": ["dealer", "dealer_name", "customer_name", "sold_to_party"]},
                keywords=["dealer", "distributor", "partner", "retailer"],
                requires_ai=False,
                description="View dealer analytics dashboard",
                example_queries=["Show dealer Taj Electronics", "Dealer Taj Electronics dashboard"]
            ),
            
            Intent.CITY_DASHBOARD: ServiceDefinition(
                menu_number="3",
                menu_name="City Dashboard",
                intent=Intent.CITY_DASHBOARD,
                service_key="city_analytics",
                service_file="app/services/city_service.py",
                service_class="CityAnalyticsService",
                method="get_city_dashboard",
                compatible_methods=["get_city_dashboard", "get_city_profile"],
                required_entities=["city"],
                parameter_mapping={"city_name": "city", "city": "city"},
                alias_mapping={"city": ["city", "city_name", "town", "location"]},
                keywords=["city", "town", "location", "urban"],
                requires_ai=False,
                description="View city analytics dashboard",
                example_queries=["Show Lahore dashboard", "Lahore", "Karachi city stats"]
            ),
            
            Intent.WAREHOUSE_DASHBOARD: ServiceDefinition(
                menu_number="4",
                menu_name="Warehouse Dashboard",
                intent=Intent.WAREHOUSE_DASHBOARD,
                service_key="warehouse",
                service_file="app/services/warehouse_service.py",
                service_class="WarehouseAnalyticsService",
                method="get_warehouse_dashboard",
                compatible_methods=["get_warehouse_dashboard", "get_warehouse_stats"],
                required_entities=["warehouse"],
                parameter_mapping={"warehouse": "warehouse", "warehouse_code": "warehouse"},
                alias_mapping={"warehouse": ["warehouse", "warehouse_name", "wh", "depot"]},
                keywords=["warehouse", "depot", "storage", "facility"],
                requires_ai=False,
                description="View warehouse analytics dashboard",
                example_queries=["Warehouse dashboard", "LHE warehouse stats"]
            ),
            
            Intent.PRODUCT_DASHBOARD: ServiceDefinition(
                menu_number="5",
                menu_name="Product Dashboard",
                intent=Intent.PRODUCT_DASHBOARD,
                service_key="product",
                service_file="app/services/product_service.py",
                service_class="ProductService",
                method="get_product_dashboard",
                compatible_methods=["get_product_dashboard"],
                required_entities=["product"],
                parameter_mapping={"product": "product", "material": "product"},
                alias_mapping={"product": ["product", "product_name", "item", "sku", "model"]},
                keywords=["product", "material", "item", "sku"],
                requires_ai=False,
                description="View product analytics dashboard",
                example_queries=["Product dashboard", "HMW-20MPS stats"]
            ),
            
            Intent.NATIONAL_KPI: ServiceDefinition(
                menu_number="6",
                menu_name="National KPI",
                intent=Intent.NATIONAL_KPI,
                service_key="national_kpi",
                service_file="app/services/national_kpi_service.py",
                service_class="NationalKPIService",
                method="get_national_kpi_dashboard",
                compatible_methods=["get_national_kpi", "get_kpi", "get_dashboard"],
                required_entities=[],
                parameter_mapping={},
                alias_mapping={},
                keywords=["national", "kpi", "overall", "company"],
                requires_ai=False,
                description="View national KPI dashboard",
                example_queries=["National KPI", "Company performance", "Overall KPIs"]
            ),
            
            Intent.PENDING_DNS: ServiceDefinition(
                menu_number="7",
                menu_name="Pending DNs",
                intent=Intent.PENDING_DNS,
                service_key="dn_analysis",
                service_file="app/services/dn_analysis.py",
                service_class="DNAnalysisService",
                method="get_pending_dns",
                compatible_methods=["get_pending_dns", "get_delayed_dns"],
                required_entities=[],
                parameter_mapping={},
                alias_mapping={},
                keywords=["pending", "delay", "overdue", "missed"],
                requires_ai=False,
                description="View pending delivery notes",
                example_queries=["Pending DNs", "Pending deliveries", "Undelivered DNs"]
            ),
            
            Intent.TOP_PERFORMERS: ServiceDefinition(
                menu_number="8",
                menu_name="Top Performers",
                intent=Intent.TOP_PERFORMERS,
                service_key="dn_analysis",
                service_file="app/services/dn_analysis.py",
                service_class="DNAnalysisService",
                method="get_top_performers",
                compatible_methods=["get_top_performers", "get_performers"],
                required_entities=[],
                parameter_mapping={},
                alias_mapping={},
                keywords=["top", "best", "performers", "leaderboard"],
                requires_ai=False,
                description="View top performers ranking",
                example_queries=["Top performers", "Best dealers", "Performance ranking"]
            ),
            
            Intent.GENERAL_AI: ServiceDefinition(
                menu_number="9",
                menu_name="AI Query",
                intent=Intent.GENERAL_AI,
                service_key="groq",
                service_file="app/services/groq_service.py",
                service_class="GroqService",
                method="process_query",
                compatible_methods=["process_query", "ask_ai", "get_ai_response"],
                required_entities=[],
                parameter_mapping={"message": "message"},
                alias_mapping={},
                keywords=["ai", "ask", "query", "analyze"],
                requires_ai=True,
                description="General AI assistant",
                example_queries=["What's the issue", "Explain this", "Help me understand"]
            ),
            
            Intent.HELP: ServiceDefinition(
                menu_number="0",
                menu_name="Help",
                intent=Intent.HELP,
                service_key="help_service",
                service_file="app/services/ai_provider_service.py",
                service_class="AIProviderService",
                method="show_help",
                compatible_methods=[],
                required_entities=[],
                parameter_mapping={},
                alias_mapping={},
                keywords=["help", "guide", "how to", "instructions"],
                requires_ai=False,
                description="Get help and guidance",
                example_queries=["help", "how to", "guide"]
            ),
        }
        
        # Attach service instances
        for intent, definition in self._definitions.items():
            self._attach_service_instance(definition)
    
    def _attach_service_instance(self, definition: ServiceDefinition) -> None:
        """Attach service instance to definition"""
        if definition.service_key in ["menu_service", "help_service"]:
            definition.service_instance = self
            definition.health_status = ServiceStatus.HEALTHY
        elif definition.service_key == "dn_analysis":
            definition.service_instance = DN_ANALYSIS_SERVICE
            definition.health_status = ServiceStatus.HEALTHY if DN_ANALYSIS_AVAILABLE else ServiceStatus.UNHEALTHY
        elif definition.service_key == "dealer_analytics":
            definition.service_instance = DEALER_ANALYTICS_SERVICE
            definition.health_status = ServiceStatus.HEALTHY if DEALER_ANALYTICS_AVAILABLE else ServiceStatus.UNHEALTHY
        elif definition.service_key == "city_analytics":
            definition.service_instance = CITY_ANALYTICS_SERVICE
            definition.health_status = ServiceStatus.HEALTHY if CITY_ANALYTICS_AVAILABLE else ServiceStatus.UNHEALTHY
        elif definition.service_key == "warehouse":
            definition.service_instance = WAREHOUSE_SERVICE
            definition.health_status = ServiceStatus.HEALTHY if WAREHOUSE_AVAILABLE else ServiceStatus.UNHEALTHY
        elif definition.service_key == "product":
            definition.service_instance = PRODUCT_SERVICE
            definition.health_status = ServiceStatus.HEALTHY if PRODUCT_AVAILABLE else ServiceStatus.UNHEALTHY
        elif definition.service_key == "national_kpi":
            definition.service_instance = NATIONAL_KPI_SERVICE
            definition.health_status = ServiceStatus.HEALTHY if NATIONAL_KPI_AVAILABLE else ServiceStatus.UNHEALTHY
        elif definition.service_key == "groq":
            definition.service_instance = GROQ_SERVICE
            definition.health_status = ServiceStatus.HEALTHY if GROQ_AVAILABLE else ServiceStatus.UNHEALTHY
    
    def get_definition(self, intent: Intent) -> Optional[ServiceDefinition]:
        """Get service definition by intent"""
        return self._definitions.get(intent)
    
    def get_definition_by_menu(self, menu_number: str) -> Optional[ServiceDefinition]:
        """Get service definition by menu number"""
        for definition in self._definitions.values():
            if definition.menu_number == menu_number:
                return definition
        return None
    
    def get_method(self, definition: ServiceDefinition, method_name: str) -> Optional[Callable]:
        """Get method from service instance with signature validation"""
        if not definition.service_instance:
            logger.error(f"❌ Service instance is None for {definition.service_key}")
            return None
        
        cache_key = f"{definition.service_key}_{method_name}"
        
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]
        
        if hasattr(definition.service_instance, method_name):
            method = getattr(definition.service_instance, method_name)
            if callable(method):
                self._method_cache[cache_key] = method
                self._signature_cache[cache_key] = inspect.signature(method)
                return method
        
        # Try compatible methods
        for compatible_method in definition.compatible_methods:
            if hasattr(definition.service_instance, compatible_method):
                method = getattr(definition.service_instance, compatible_method)
                if callable(method):
                    self._method_cache[cache_key] = method
                    self._signature_cache[cache_key] = inspect.signature(method)
                    return method
        
        return None
    
    def get_signature(self, cache_key: str) -> Optional[inspect.Signature]:
        """Get method signature from cache"""
        return self._signature_cache.get(cache_key)
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get health status of all services"""
        status = {}
        for intent, definition in self._definitions.items():
            status[intent.value] = {
                "status": definition.health_status.value,
                "available": definition.service_instance is not None,
                "methods": [definition.method] + definition.compatible_methods,
            }
        return status
    
    def health_check(self) -> Dict[str, Any]:
        """Perform comprehensive health check"""
        results = {
            "overall": "healthy",
            "services": {},
            "errors": [],
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        for intent, definition in self._definitions.items():
            service_status = {
                "status": definition.health_status.value,
                "instance_exists": definition.service_instance is not None,
                "method_available": bool(self.get_method(definition, definition.method)),
                "compatible_methods": len(definition.compatible_methods),
            }
            
            if definition.service_instance is None:
                results["errors"].append(f"Service {definition.service_key} instance missing")
                results["overall"] = "degraded"
            elif not self.get_method(definition, definition.method):
                results["errors"].append(f"Service {definition.service_key} method {definition.method} missing")
                results["overall"] = "degraded"
            
            results["services"][definition.service_key] = service_status
        
        results["imports"] = SERVICE_IMPORT_STATUS
        
        return results


# =====================================================================================================================
# ENTERPRISE CACHE MANAGER
# =====================================================================================================================

class CacheManager:
    """Enterprise cache manager with TTL support"""
    
    def __init__(self, default_ttl: int = CACHE_TTL_SECONDS):
        self.default_ttl = default_ttl
        self._cache = TTLCache(maxsize=MAX_CACHE_SIZE, ttl=default_ttl)
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[Any]:
        """Get cached value"""
        with self._lock:
            return self._cache.get(key)
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set cached value with optional TTL"""
        with self._lock:
            if ttl:
                # Create a new cache with the TTL for this value
                temp_cache = TTLCache(maxsize=1, ttl=ttl)
                temp_cache[key] = value
                self._cache[key] = value
            else:
                self._cache[key] = value
    
    def invalidate(self, key: str) -> None:
        """Invalidate specific cache entry"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
    
    def clear(self) -> None:
        """Clear all cache"""
        with self._lock:
            self._cache.clear()


# =====================================================================================================================
# MAIN AI PROVIDER SERVICE
# =====================================================================================================================

class AIProviderService:
    """Enterprise AI Orchestration Service"""
    
    _instance: Optional["AIProviderService"] = None
    _instance_lock = threading.Lock()
    
    def __new__(cls) -> "AIProviderService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        
        self._request_count = 0
        self._error_count = 0
        self._initialized = False
        
        # Initialize components
        self.registry = ServiceRegistry()
        self.intent_detector = IntentDetectionEngine(self.registry)
        self.parameter_mapper = ParameterMapper()
        self.response_normalizer = ResponseNormalizer()
        self.validator = WhatsAppValidator()
        self.cache_manager = CacheManager()
        
        # Perform startup health check
        self._perform_health_check()
        
        self._initialized = True
        self._log_startup_status()
    
    def _perform_health_check(self) -> None:
        """Perform comprehensive startup health check"""
        logger.info("=" * 80)
        logger.info("🔍 Performing Startup Health Check...")
        
        health = self.registry.health_check()
        
        # Log service status
        logger.info("📊 Service Status:")
        for service_key, status in health["services"].items():
            status_icon = "✅" if status["instance_exists"] else "❌"
            method_icon = "✅" if status["method_available"] else "❌"
            logger.info(f"  {status_icon} {service_key}: Instance={status['instance_exists']}, Method={method_icon}")
        
        # Log import status
        logger.info("📦 Import Status:")
        for service_name, available in SERVICE_IMPORT_STATUS.items():
            status_icon = "✅" if available else "❌"
            logger.info(f"  {status_icon} {service_name}")
        
        # Log errors
        if health["errors"]:
            logger.warning("⚠️ Health Check Errors:")
            for error in health["errors"]:
                logger.warning(f"  - {error}")
        
        self._health_status = health
        logger.info("=" * 80)
    
    def _log_startup_status(self) -> None:
        """Log startup status"""
        available = sum(1 for s in SERVICE_IMPORT_STATUS.values() if s)
        total = len(SERVICE_IMPORT_STATUS)
        
        status = {
            "healthy": self._health_status["overall"] == "healthy",
            "available_services": available,
            "total_services": total,
            "errors": self._health_status["errors"],
        }
        
        EnterpriseLogger.log_startup(status)
    
    def show_main_menu(self) -> str:
        """Show main menu"""
        return get_main_menu()
    
    def show_help(self) -> str:
        """Show help menu"""
        return get_help_menu()
    
    def _get_mapped_parameters(self, method: Callable, entities: EntityExtraction) -> Dict[str, Any]:
        """Get mapped parameters using enterprise parameter mapper"""
        return self.parameter_mapper.map_parameters(method, entities)
    
    async def _execute_service(self, method: Callable, params: Dict[str, Any]) -> Any:
        """Execute service method with proper async/sync handling"""
        try:
            if inspect.iscoroutinefunction(method):
                return await method(**params)
            else:
                return method(**params)
        except Exception as exc:
            logger.error(f"Service execution failed: {exc}")
            logger.error(traceback.format_exc())
            raise
    
    def _extract_whatsapp_message_safe(self, result: Any) -> str:
        """Safely extract WhatsApp message with Pydantic error handling"""
        try:
            return self.response_normalizer.normalize(result)
        except Exception as e:
            logger.error(f"Response normalization failed: {e}")
            if "string_type" in str(e) or "ValidationError" in str(e):
                return "⚠️ Invalid response format from service. Please try again."
            return f"⚠️ Response formatting error: {str(e)[:100]}"
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Process WhatsApp message and return response"""
        sender = sender or sender_id
        context = RequestContext(
            request_id=str(uuid.uuid4())[:8],
            sender=sender,
            message=message,
            normalized_message=' '.join(message.split()).strip() if message else "",
            start_time=time.time(),
            conversation_state="new"
        )
        
        self._request_count += 1
        
        if not message or not message.strip():
            return get_main_menu()
        
        try:
            # Step 1: Extract entities
            logger.info(f"[{context.request_id}] 📨 Processing request #{self._request_count}")
            logger.info(f"[{context.request_id}] Sender: {sender or 'unknown'}")
            logger.info(f"[{context.request_id}] Message: {message[:100]}")
            
            entities = self.intent_detector.entity_extractor.extract(message)
            context.entities = entities
            
            entity_dict = entities.to_dict()
            logger.info(f"[{context.request_id}] 🔍 Extracted entities: {entity_dict}")
            
            # Step 2: Detect intent
            intent, confidence = self.intent_detector.detect_intent(message, entities)
            context.intent = intent
            context.confidence = confidence
            
            logger.info(f"[{context.request_id}] 🎯 Intent: {intent.value} (confidence: {confidence:.2f})")
            
            # Step 3: Get service definition
            definition = self.registry.get_definition(intent)
            if not definition:
                logger.error(f"[{context.request_id}] ❌ No service definition for intent: {intent}")
                return f"⚠️ Service not found for intent: {intent.value}\n\nType *menu* to see options."
            
            context.service_key = definition.service_key
            context.method = definition.method
            context.ai_used = definition.requires_ai
            
            logger.info(f"[{context.request_id}] 📦 Service: {definition.service_key} -> {definition.method}")
            
            # Step 4: Get method
            method = self.registry.get_method(definition, definition.method)
            if not method:
                logger.error(f"[{context.request_id}] ❌ Method {definition.method} not found")
                return f"⚠️ Service method {definition.method} is temporarily unavailable.\n\nType *menu* to see options."
            
            # Step 5: Map parameters
            mapped_params = self._get_mapped_parameters(method, entities)
            context.mapped_parameters = mapped_params
            
            logger.info(f"[{context.request_id}] 📝 Mapped parameters: {mapped_params}")
            
            # Step 6: Check cache
            cache_key = f"{definition.service_key}_{definition.method}_{str(mapped_params)}"
            cached_result = self.cache_manager.get(cache_key)
            if cached_result:
                logger.info(f"[{context.request_id}] 📦 Cache hit for: {cache_key}")
                return cached_result
            
            # Step 7: Execute service
            try:
                start_time = time.time()
                result = await self._execute_service(method, mapped_params)
                context.database_time_ms = (time.time() - start_time) * 1000
                
                logger.info(f"[{context.request_id}] ⏱️ Execution time: {context.database_time_ms:.2f}ms")
                logger.info(f"[{context.request_id}] 📤 Result type: {type(result)}")
                
                # Step 8: Normalize response
                normalize_start = time.time()
                normalized_response = self._extract_whatsapp_message_safe(result)
                context.formatting_time_ms = (time.time() - normalize_start) * 1000
                
                logger.info(f"[{context.request_id}] 📝 Normalized response preview: {normalized_response[:200]}...")
                
                # Step 9: Validate for WhatsApp
                if not self.validator.validate(normalized_response):
                    logger.error(f"[{context.request_id}] ❌ Invalid response")
                    return "⚠️ Service returned an invalid response. Please try again."
                
                final_response = self.validator.prepare(normalized_response)
                context.response_size = len(final_response)
                context.response_type = "text"
                context.success = True
                context.total_time_ms = context.elapsed_ms()
                context.conversation_state = "completed"
                
                # Step 10: Cache response
                self.cache_manager.set(cache_key, final_response)
                
                # Step 11: Log success
                EnterpriseLogger.log_request(context)
                
                return final_response
                
            except Exception as exc:
                context.error = str(exc)
                context.success = False
                context.total_time_ms = context.elapsed_ms()
                self._error_count += 1
                
                logger.error(f"[{context.request_id}] ❌ Method execution failed: {exc}")
                logger.error(traceback.format_exc())
                EnterpriseLogger.log_request(context)
                
                # Try compatible method if available
                for compatible_method in definition.compatible_methods:
                    if compatible_method != definition.method:
                        try:
                            logger.info(f"[{context.request_id}] 🔄 Trying compatible method: {compatible_method}")
                            method = self.registry.get_method(definition, compatible_method)
                            if method:
                                mapped_params = self._get_mapped_parameters(method, entities)
                                result = await self._execute_service(method, mapped_params)
                                normalized_response = self._extract_whatsapp_message_safe(result)
                                if self.validator.validate(normalized_response):
                                    final_response = self.validator.prepare(normalized_response)
                                    context.success = True
                                    return final_response
                        except Exception as compat_exc:
                            logger.warning(f"[{context.request_id}] Compatible method {compatible_method} also failed: {compat_exc}")
                
                return f"⚠️ Service error: {str(exc)[:100]}\n\nPlease try again or type 'menu'."
            
        except Exception as exc:
            context.error = str(exc)
            context.success = False
            context.total_time_ms = context.elapsed_ms()
            self._error_count += 1
            
            logger.error(f"[{context.request_id}] ❌ Processing failed: {exc}")
            logger.error(traceback.format_exc())
            EnterpriseLogger.log_request(context)
            
            if context.normalized_message in {"menu", "main menu", "help", "start", "0"}:
                return get_main_menu()
            
            return "⚠️ Service is temporarily unavailable. Reply *menu* to try again."
    
    def get_status(self) -> Dict[str, Any]:
        """Get service status"""
        return {
            "initialized": self._initialized,
            "healthy": self._health_status["overall"] == "healthy",
            "request_count": self._request_count,
            "error_count": self._error_count,
            "error_rate": self._error_count / max(1, self._request_count),
            "cache_size": len(self.cache_manager._cache),
            "health": self._health_status,
        }


# =====================================================================================================================
# SINGLETON INSTANCE
# =====================================================================================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()


def get_ai_provider_service() -> AIProviderService:
    """Get singleton instance of AIProviderService"""
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                try:
                    _ai_service = AIProviderService()
                except Exception as exc:
                    logger.error(f"❌ Failed to create AIProviderService: {exc}")
                    logger.error(traceback.format_exc())
                    # Create minimal instance
                    _ai_service = AIProviderService.__new__(AIProviderService)
                    _ai_service._initialized = False
                    _ai_service.registry = ServiceRegistry()
                    _ai_service.intent_detector = IntentDetectionEngine(_ai_service.registry)
                    _ai_service.parameter_mapper = ParameterMapper()
                    _ai_service.response_normalizer = ResponseNormalizer()
                    _ai_service.validator = WhatsAppValidator()
                    _ai_service.cache_manager = CacheManager()
                    _ai_service._request_count = 0
                    _ai_service._error_count = 0
                    _ai_service._health_status = {"overall": "degraded", "errors": [str(exc)]}
                    logger.warning("⚠️ AIProviderService running in minimal mode")
    return _ai_service


def get_whatsapp_provider_service() -> AIProviderService:
    """Backward-compatible factory for webhook"""
    return get_ai_provider_service()


# =====================================================================================================================
# MODULE-LEVEL FUNCTION
# =====================================================================================================================

async def process_whatsapp_query(
    message: str,
    sender: Optional[str] = None,
    sender_id: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Module-level function for backward compatibility"""
    try:
        service = get_ai_provider_service()
        return await service.process_whatsapp_query(
            message=message,
            sender=sender,
            sender_id=sender_id,
            **kwargs,
        )
    except Exception as exc:
        logger.error(f"Unexpected failure: {exc}")
        logger.error(traceback.format_exc())
        if message and message.strip().lower() in {"menu", "main menu", "help", "start", "0"}:
            return get_main_menu()
        return "⚠️ Service is temporarily unavailable. Reply *menu* to try again."


# =====================================================================================================================
# EXPORTS
# =====================================================================================================================

__all__ = [
    "process_whatsapp_query",
    "get_main_menu",
    "get_ai_provider_service",
    "get_whatsapp_provider_service",
    "AIProviderService",
    "ServiceRegistry",
    "Intent",
    "ServiceStatus",
    "RoutingDecision",
    "EntityExtraction",
    "RequestContext",
]
