"""
File: app/services/ai_provider_service.py
Version: 20.0 - ENTERPRISE ORCHESTRATION LAYER
Complete WhatsApp AI service orchestrator with enterprise-grade routing,
signature-aware invocation, response normalization, and comprehensive error handling.

Architecture Principles:
- PostgreSQL is the ONLY source of truth
- Deterministic routing before AI
- Service registry with method validation
- Signature-aware parameter passing
- Unified response normalization
- Comprehensive logging and health checks
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

logger = logging.getLogger(__name__)

# =====================================================================================================================
# CONSTANTS
# =====================================================================================================================

WHATSAPP_MAX_MESSAGE_LENGTH = 4096
CACHE_TTL_SECONDS = 300
MAX_CACHE_SIZE = 1000
DN_PATTERN = re.compile(r'(?<!\d)(\d{8,12})(?!\d)')
DN_PATTERN_WITH_SPACES = re.compile(r'(?<!\d)(\d{4}[\s-]*\d{4}[\s-]*\d{0,4})(?!\d)')
MENU_NUMBER_PATTERN = re.compile(r'^\s*([0-9])(?:[.)])\s*$')

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
class ServiceRegistryEntry:
    """Service registry entry with method validation"""
    menu_number: str
    menu_name: str
    intent: Intent
    service_key: str
    service_file: str
    service_class: str
    preferred_method: str
    compatible_methods: List[str] = field(default_factory=list)
    required_entities: List[str] = field(default_factory=list)
    parameter_mapping: Dict[str, str] = field(default_factory=dict)
    expected_response_format: str = "dict"
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
    """Extracted entities from user message"""
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
    date_range: Optional[Tuple[str, str]] = None


@dataclass
class RoutingDecision:
    """Complete routing decision with all context"""
    intent: Intent
    confidence: float
    service_entry: ServiceRegistryEntry
    entity: EntityExtraction
    method: str
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    menu_option: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "service_key": self.service_entry.service_key,
            "method": self.method,
            "entity": {k: v for k, v in self.entity.__dict__.items() if v is not None},
            "requires_ai": self.requires_ai,
            "reason": self.reason,
            "original_message": self.original_message[:100],
            "menu_option": self.menu_option,
        }


@dataclass
class RequestContext:
    """Complete request context for logging"""
    request_id: str
    sender: Optional[str]
    message: str
    normalized_message: str
    intent: Optional[Intent] = None
    confidence: float = 0.0
    entities: Optional[EntityExtraction] = None
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
    
    def elapsed_ms(self) -> float:
        return (time.time() - self.start_time) * 1000
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "sender": self.sender,
            "message": self.message[:100],
            "normalized": self.normalized_message[:100],
            "intent": self.intent.value if self.intent else None,
            "confidence": self.confidence,
            "entities": {k: v for k, v in self.entities.__dict__.items() if v is not None} if self.entities else {},
            "service": self.service_key,
            "method": self.method,
            "db_time_ms": round(self.database_time_ms, 2),
            "formatting_ms": round(self.formatting_time_ms, 2),
            "total_ms": round(self.total_time_ms, 2),
            "ai_used": self.ai_used,
            "response_size": self.response_size,
            "success": self.success,
            "error": self.error[:100] if self.error else None,
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
# SERVICE IMPORTS
# =====================================================================================================================

SERVICE_IMPORT_STATUS: Dict[str, bool] = {}
SERVICE_IMPORT_ERRORS: Dict[str, str] = {}

def _import_service(service_name: str, import_path: str, class_name: str) -> Optional[Any]:
    """Import a service with proper error handling"""
    try:
        module = __import__(import_path, fromlist=[class_name])
        service_class = getattr(module, class_name)
        SERVICE_IMPORT_STATUS[service_name] = True
        return service_class
    except Exception as exc:
        SERVICE_IMPORT_STATUS[service_name] = False
        SERVICE_IMPORT_ERRORS[service_name] = str(exc)
        logger.warning(f"⚠️ Failed to import {service_name}: {exc}")
        return None

def _import_service_instance(service_name: str, import_path: str, class_name: str, factory_method: Optional[str] = None) -> Optional[Any]:
    """Import and instantiate a service with proper error handling"""
    try:
        module = __import__(import_path, fromlist=[class_name])
        if factory_method:
            factory = getattr(module, factory_method)
            instance = factory()
        else:
            service_class = getattr(module, class_name)
            instance = service_class()
        SERVICE_IMPORT_STATUS[service_name] = True
        return instance
    except Exception as exc:
        SERVICE_IMPORT_STATUS[service_name] = False
        SERVICE_IMPORT_ERRORS[service_name] = str(exc)
        logger.warning(f"⚠️ Failed to initialize {service_name}: {exc}")
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


# =====================================================================================================================
# SERVICE REGISTRY
# =====================================================================================================================

class ServiceRegistry:
    """Enterprise service registry with method validation and signature detection"""
    
    def __init__(self):
        self._entries: Dict[Intent, ServiceRegistryEntry] = {}
        self._method_cache: Dict[str, Callable] = {}
        self._signature_cache: Dict[str, inspect.Signature] = {}
        self._lock = threading.RLock()
        self._initialize_registry()
    
    def _initialize_registry(self) -> None:
        """Initialize the service registry with all services"""
        self._entries = {
            Intent.MENU: ServiceRegistryEntry(
                menu_number="0",
                menu_name="Main Menu",
                intent=Intent.MENU,
                service_key="menu_service",
                service_file="ai_provider_service.py",
                service_class="AIProviderService",
                preferred_method="show_main_menu",
                compatible_methods=[],
                required_entities=[],
                parameter_mapping={},
                requires_ai=False,
                description="Show main menu",
                example_queries=["menu", "help", "0"]
            ),
            
            Intent.DN_LOOKUP: ServiceRegistryEntry(
                menu_number="1",
                menu_name="DN Lookup",
                intent=Intent.DN_LOOKUP,
                service_key="dn_analysis",
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_dn_dashboard",
                compatible_methods=["get_dn_details", "get_dn_status", "get_dn_info"],
                required_entities=["dn_number"],
                parameter_mapping={"dn_number": "dn_no", "dn": "dn_no"},
                requires_ai=False,
                description="Look up delivery note details",
                example_queries=["Track DN 6243698820", "6243698820", "DN 6243698820 status"]
            ),
            
            Intent.DN_DASHBOARD: ServiceRegistryEntry(
                menu_number="1",
                menu_name="DN Dashboard",
                intent=Intent.DN_DASHBOARD,
                service_key="dn_analysis",
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_dn_dashboard",
                compatible_methods=["get_dn_details", "get_dn_status"],
                required_entities=["dn_number"],
                parameter_mapping={"dn_number": "dn_no", "dn": "dn_no"},
                requires_ai=False,
                description="View DN analytics dashboard",
                example_queries=["Show DN dashboard", "DN 6243698820 stats"]
            ),
            
            Intent.DN_HISTORY: ServiceRegistryEntry(
                menu_number="1",
                menu_name="DN History",
                intent=Intent.DN_HISTORY,
                service_key="dn_analysis",
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_dn_history",
                compatible_methods=["get_dn_history"],
                required_entities=["dn_number"],
                parameter_mapping={"dn_number": "dn_no", "dn": "dn_no"},
                requires_ai=False,
                description="View DN history timeline",
                example_queries=["DN 6243698820 history", "Delivery history for 6243698820"]
            ),
            
            Intent.DEALER_DASHBOARD: ServiceRegistryEntry(
                menu_number="2",
                menu_name="Dealer Dashboard",
                intent=Intent.DEALER_DASHBOARD,
                service_key="dealer_analytics",
                service_file="app.services.dealer_analytics_service",
                service_class="DealerAnalyticsService",
                preferred_method="get_dealer_dashboard",
                compatible_methods=["get_dealer_dashboard", "get_dealer_profile"],
                required_entities=["dealer_name"],
                parameter_mapping={"dealer_name": "dealer_name", "dealer": "dealer_name"},
                requires_ai=False,
                description="View dealer analytics dashboard",
                example_queries=["Show dealer Taj Electronics", "Dealer Taj Electronics dashboard"]
            ),
            
            Intent.CITY_DASHBOARD: ServiceRegistryEntry(
                menu_number="3",
                menu_name="City Dashboard",
                intent=Intent.CITY_DASHBOARD,
                service_key="city_analytics",
                service_file="app.services.city_service",
                service_class="CityAnalyticsService",
                preferred_method="get_city_dashboard",
                compatible_methods=["get_city_dashboard", "get_city_profile"],
                required_entities=["city"],
                parameter_mapping={"city": "city_name", "city_name": "city_name"},
                requires_ai=False,
                description="View city analytics dashboard",
                example_queries=["Show Lahore dashboard", "Karachi city stats", "Lahore"]
            ),
            
            Intent.WAREHOUSE_DASHBOARD: ServiceRegistryEntry(
                menu_number="4",
                menu_name="Warehouse Dashboard",
                intent=Intent.WAREHOUSE_DASHBOARD,
                service_key="dn_analysis",
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_warehouse_dashboard",
                compatible_methods=["get_warehouse_dashboard"],
                required_entities=["warehouse"],
                parameter_mapping={"warehouse": "warehouse", "warehouse_code": "warehouse"},
                requires_ai=False,
                description="View warehouse analytics dashboard",
                example_queries=["Warehouse dashboard", "LHE warehouse stats"]
            ),
            
            Intent.PRODUCT_DASHBOARD: ServiceRegistryEntry(
                menu_number="5",
                menu_name="Product Dashboard",
                intent=Intent.PRODUCT_DASHBOARD,
                service_key="product",
                service_file="app.services.product_service",
                service_class="ProductService",
                preferred_method="get_product_dashboard",
                compatible_methods=["get_product_dashboard"],
                required_entities=["product"],
                parameter_mapping={"product": "product", "material": "product"},
                requires_ai=False,
                description="View product analytics dashboard",
                example_queries=["Product dashboard", "HMW-20MPS stats"]
            ),
            
            Intent.NATIONAL_KPI: ServiceRegistryEntry(
                menu_number="6",
                menu_name="National KPI",
                intent=Intent.NATIONAL_KPI,
                service_key="national_kpi",
                service_file="app.services.national_kpi_service",
                service_class="NationalKPIService",
                preferred_method="get_national_kpi",
                compatible_methods=["get_national_kpi_dashboard", "get_kpi", "get_dashboard"],
                required_entities=[],
                parameter_mapping={},
                requires_ai=False,
                description="View national KPI dashboard",
                example_queries=["National KPI", "Company performance", "Overall KPIs"]
            ),
            
            Intent.PENDING_DNS: ServiceRegistryEntry(
                menu_number="7",
                menu_name="Pending DNs",
                intent=Intent.PENDING_DNS,
                service_key="dn_analysis",
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_pending_dns",
                compatible_methods=["get_pending_dns"],
                required_entities=[],
                parameter_mapping={},
                requires_ai=False,
                description="View pending delivery notes",
                example_queries=["Pending DNs", "Pending deliveries", "Undelivered DNs"]
            ),
            
            Intent.TOP_PERFORMERS: ServiceRegistryEntry(
                menu_number="8",
                menu_name="Top Performers",
                intent=Intent.TOP_PERFORMERS,
                service_key="dn_analysis",
                service_file="app.services.dn_analysis",
                service_class="DNAnalysisService",
                preferred_method="get_top_performers",
                compatible_methods=["get_top_performers"],
                required_entities=[],
                parameter_mapping={},
                requires_ai=False,
                description="View top performers ranking",
                example_queries=["Top performers", "Best dealers", "Performance ranking"]
            ),
            
            Intent.GENERAL_AI: ServiceRegistryEntry(
                menu_number="9",
                menu_name="AI Query",
                intent=Intent.GENERAL_AI,
                service_key="groq",
                service_file="app.services.groq_service",
                service_class="GroqService",
                preferred_method="process_query",
                compatible_methods=["process_query", "ask_ai", "get_ai_response"],
                required_entities=[],
                parameter_mapping={"message": "message"},
                requires_ai=True,
                description="General AI assistant",
                example_queries=["What's the issue", "Explain this"]
            ),
        }
        
        # Attach service instances
        for intent, entry in self._entries.items():
            self._attach_service_instance(entry)
    
    def _attach_service_instance(self, entry: ServiceRegistryEntry) -> None:
        """Attach service instance to registry entry"""
        if entry.service_key == "menu_service":
            entry.service_instance = self
            entry.health_status = ServiceStatus.HEALTHY
        elif entry.service_key == "dn_analysis":
            entry.service_instance = DN_ANALYSIS_SERVICE
            entry.health_status = ServiceStatus.HEALTHY if DN_ANALYSIS_AVAILABLE else ServiceStatus.UNHEALTHY
        elif entry.service_key == "dealer_analytics":
            entry.service_instance = DEALER_ANALYTICS_SERVICE
            entry.health_status = ServiceStatus.HEALTHY if DEALER_ANALYTICS_AVAILABLE else ServiceStatus.UNHEALTHY
        elif entry.service_key == "city_analytics":
            entry.service_instance = CITY_ANALYTICS_SERVICE
            entry.health_status = ServiceStatus.HEALTHY if CITY_ANALYTICS_AVAILABLE else ServiceStatus.UNHEALTHY
        elif entry.service_key == "product":
            entry.service_instance = PRODUCT_SERVICE
            entry.health_status = ServiceStatus.HEALTHY if PRODUCT_AVAILABLE else ServiceStatus.UNHEALTHY
        elif entry.service_key == "national_kpi":
            entry.service_instance = NATIONAL_KPI_SERVICE
            entry.health_status = ServiceStatus.HEALTHY if NATIONAL_KPI_AVAILABLE else ServiceStatus.UNHEALTHY
        elif entry.service_key == "groq":
            entry.service_instance = GROQ_SERVICE
            entry.health_status = ServiceStatus.HEALTHY if GROQ_AVAILABLE else ServiceStatus.UNHEALTHY
    
    def get_entry(self, intent: Intent) -> Optional[ServiceRegistryEntry]:
        """Get registry entry by intent"""
        return self._entries.get(intent)
    
    def get_entry_by_menu(self, menu_number: str) -> Optional[ServiceRegistryEntry]:
        """Get registry entry by menu number"""
        for entry in self._entries.values():
            if entry.menu_number == menu_number:
                return entry
        return None
    
    def get_method(self, entry: ServiceRegistryEntry, method_name: str) -> Optional[Callable]:
        """Get method from service instance with signature validation"""
        if not entry.service_instance:
            return None
        
        cache_key = f"{entry.service_key}_{method_name}"
        
        # Check cache
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]
        
        # Try preferred method
        if hasattr(entry.service_instance, method_name):
            method = getattr(entry.service_instance, method_name)
            if callable(method):
                self._method_cache[cache_key] = method
                self._signature_cache[cache_key] = inspect.signature(method)
                return method
        
        # Try compatible methods
        for compatible_method in entry.compatible_methods:
            if hasattr(entry.service_instance, compatible_method):
                method = getattr(entry.service_instance, compatible_method)
                if callable(method):
                    self._method_cache[cache_key] = method
                    self._signature_cache[cache_key] = inspect.signature(method)
                    return method
        
        return None
    
    def get_signature(self, cache_key: str) -> Optional[inspect.Signature]:
        """Get method signature from cache"""
        return self._signature_cache.get(cache_key)
    
    def validate_method_signature(self, method: Callable, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and filter parameters based on method signature"""
        sig = inspect.signature(method)
        valid_params = {}
        
        for param_name, param in sig.parameters.items():
            if param_name in parameters:
                valid_params[param_name] = parameters[param_name]
            elif param.default == inspect.Parameter.empty and param.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
                # Required parameter with no default and no value provided
                logger.warning(f"Missing required parameter: {param_name}")
        
        return valid_params
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get health status of all services"""
        status = {}
        for intent, entry in self._entries.items():
            status[intent.value] = {
                "status": entry.health_status.value,
                "available": entry.service_instance is not None,
                "methods": [entry.preferred_method] + entry.compatible_methods,
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
        
        for intent, entry in self._entries.items():
            service_status = {
                "status": entry.health_status.value,
                "instance_exists": entry.service_instance is not None,
                "method_available": bool(self.get_method(entry, entry.preferred_method)),
                "compatible_methods": len(entry.compatible_methods),
            }
            
            if entry.service_instance is None:
                results["errors"].append(f"Service {entry.service_key} instance missing")
                results["overall"] = "degraded"
            elif not self.get_method(entry, entry.preferred_method):
                results["errors"].append(f"Service {entry.service_key} method {entry.preferred_method} missing")
                results["overall"] = "degraded"
            
            results["services"][entry.service_key] = service_status
        
        return results


# =====================================================================================================================
# ENTITY EXTRACTION ENGINE
# =====================================================================================================================

class EntityExtractionEngine:
    """Entity extraction with compiled regex patterns"""
    
    def __init__(self):
        self._patterns = self._compile_patterns()
        self._city_names = self._load_city_names()
    
    def _compile_patterns(self) -> Dict[str, re.Pattern]:
        """Compile regex patterns once at startup"""
        return {
            "dn": DN_PATTERN,
            "dn_spaced": DN_PATTERN_WITH_SPACES,
            "dealer": re.compile(r'(?:dealer|show|get)\s+([\w&.\'\- ]{2,}?(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation)(?:[\w&.\'\- ]*)?)', re.IGNORECASE),
            "dealer_name": re.compile(r'([\w&.\'\- ]{2,}?(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation)(?:[\w&.\'\- ]*)?)'),
            "warehouse": re.compile(r'(?:warehouse|depot|\bwh\b)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "warehouse_code": re.compile(r'(?:warehouse code|wh code)\s+([A-Z0-9]{3})', re.IGNORECASE),
            "product": re.compile(r'(?:product|model|material|item)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "material": re.compile(r'(?:material|mat)\s+([A-Z0-9]{6,})', re.IGNORECASE),
            "sales_office": re.compile(r'(?:sales office|office)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "sales_manager": re.compile(r'(?:sales manager|manager)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "division": re.compile(r'(?:division|div)\s+([\w&.\'\- ]{2,})', re.IGNORECASE),
            "city": re.compile(r'\b(' + '|'.join(CITY_NAMES) + r')\b', re.IGNORECASE),
            "dealer_code": re.compile(r'(?:code|id)\s+([A-Z0-9]{3,})', re.IGNORECASE),
            "customer_code": re.compile(r'(?:customer code|cust code)\s+([A-Z0-9]{3,})', re.IGNORECASE),
        }
    
    def _load_city_names(self) -> Set[str]:
        """Load city names for detection"""
        return {
            "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
            "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
            "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
            "dera ghazi khan", "gwadar", "rahim yar khan"
        }
    
    def extract(self, message: str) -> EntityExtraction:
        """Extract all entities from message"""
        entities = EntityExtraction()
        message_lower = message.lower()
        
        # Extract DN
        dn_match = self._patterns["dn"].search(message)
        if dn_match:
            entities.dn_number = dn_match.group(1)
        else:
            dn_match = self._patterns["dn_spaced"].search(message)
            if dn_match:
                candidate = re.sub(r"[\s-]", "", dn_match.group(1))
                if 8 <= len(candidate) <= 12:
                    entities.dn_number = candidate
        
        # Extract Dealer
        dealer_match = self._patterns["dealer"].search(message)
        if dealer_match:
            entities.dealer_name = dealer_match.group(1).strip()
        else:
            dealer_match = self._patterns["dealer_name"].search(message)
            if dealer_match:
                entities.dealer_name = dealer_match.group(1).strip()
        
        # Extract Dealer Code
        dealer_code_match = self._patterns["dealer_code"].search(message)
        if dealer_code_match:
            entities.dealer_code = dealer_code_match.group(1)
        
        # Extract Customer Code
        customer_code_match = self._patterns["customer_code"].search(message)
        if customer_code_match:
            entities.customer_code = customer_code_match.group(1)
        
        # Extract Warehouse
        warehouse_match = self._patterns["warehouse"].search(message)
        if warehouse_match:
            entities.warehouse = warehouse_match.group(1).strip()
        
        warehouse_code_match = self._patterns["warehouse_code"].search(message)
        if warehouse_code_match:
            entities.warehouse_code = warehouse_code_match.group(1)
        
        # Extract City
        city_match = self._patterns["city"].search(message)
        if city_match:
            entities.city = city_match.group(1).capitalize()
        
        # Extract Product
        product_match = self._patterns["product"].search(message)
        if product_match:
            entities.product = product_match.group(1).strip()
        
        # Extract Material
        material_match = self._patterns["material"].search(message)
        if material_match:
            entities.material_number = material_match.group(1)
        
        # Extract Sales Office
        sales_office_match = self._patterns["sales_office"].search(message)
        if sales_office_match:
            entities.sales_office = sales_office_match.group(1).strip()
        
        # Extract Sales Manager
        sales_manager_match = self._patterns["sales_manager"].search(message)
        if sales_manager_match:
            entities.sales_manager = sales_manager_match.group(1).strip()
        
        # Extract Division
        division_match = self._patterns["division"].search(message)
        if division_match:
            entities.division = division_match.group(1).strip()
        
        return entities


CITY_NAMES = (
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
    "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
    "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
    "dera ghazi khan", "gwadar", "rahim yar khan"
)


# =====================================================================================================================
# INTENT DETECTION ENGINE
# =====================================================================================================================

class IntentDetectionEngine:
    """Deterministic intent detection with rule-based and semantic routing"""
    
    def __init__(self, registry: ServiceRegistry):
        self.registry = registry
        self.entity_extractor = EntityExtractionEngine()
        self._router = None
        self._router_initialized = False
        self._router_lock = threading.Lock()
        self._menu_triggers = {
            "menu", "main menu", "options", "start", "back", "home", "help",
            "0", "hello", "hi", "hey", "salam"
        }
        
        # Rule-based intent patterns
        self._intent_rules = [
            (re.compile(r'\b(?:pending\s+pod|proof of delivery pending)\b', re.IGNORECASE), Intent.PENDING_POD),
            (re.compile(r'\b(?:pending\s+pgi|goods issue pending)\b', re.IGNORECASE), Intent.PENDING_PGI),
            (re.compile(r'\b(?:pending\s+dn|pending deliveries|undelivered)\b', re.IGNORECASE), Intent.PENDING_DNS),
            (re.compile(r'\b(?:top|best)\s+performers?\b|\bleaderboard\b', re.IGNORECASE), Intent.TOP_PERFORMERS),
            (re.compile(r'\b(?:dn|delivery note)\s+(?:service|services|dashboard|status|details?)\b', re.IGNORECASE), Intent.DN_DASHBOARD),
            (re.compile(r'\bdealer\s+(?:service|services|dashboard|analytics|performance)\b', re.IGNORECASE), Intent.DEALER_DASHBOARD),
            (re.compile(r'\bcit(?:y|ies)\s+(?:service|services|dashboard|analytics|performance)\b', re.IGNORECASE), Intent.CITY_DASHBOARD),
            (re.compile(r'\bwarehouse\s+(?:service|services|dashboard|analytics|performance)\b', re.IGNORECASE), Intent.WAREHOUSE_DASHBOARD),
            (re.compile(r'\bproduct\s+(?:service|services|dashboard|analytics|performance)\b', re.IGNORECASE), Intent.PRODUCT_DASHBOARD),
            (re.compile(r'\b(?:national kpi|overall performance|executive dashboard)\b', re.IGNORECASE), Intent.NATIONAL_KPI),
            (re.compile(r'\b(?:dealer|distributor)\s+(?:revenue|sales)\b', re.IGNORECASE), Intent.DEALER_REVENUE),
            (re.compile(r'\bcit(?:y|ies)\s+(?:revenue|sales)\b', re.IGNORECASE), Intent.CITY_REVENUE),
            (re.compile(r'\b(?:dn|delivery)\s+history\b', re.IGNORECASE), Intent.DN_HISTORY),
            (re.compile(r'\b(?:top|best)\s+products?\b', re.IGNORECASE), Intent.TOP_PRODUCTS),
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
                # Build routes from registry
                routes = []
                for intent, entry in self.registry._entries.items():
                    if entry.example_queries:
                        routes.append(Route(
                            name=intent.value,
                            utterances=entry.example_queries + [
                                f"{entry.menu_name.lower()} dashboard",
                                f"show {entry.menu_name.lower()}",
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
    
    def detect_intent(self, message: str, entities: EntityExtraction) -> Tuple[Intent, float]:
        """Detect intent using deterministic pipeline"""
        message_lower = message.lower().strip()
        
        # 1. Check for menu
        if message_lower in self._menu_triggers:
            return Intent.MENU, 1.0
        
        # 2. Check for menu number
        if MENU_NUMBER_PATTERN.match(message):
            return Intent.MENU, 1.0
        
        # 3. Check for DN
        if entities.dn_number:
            return Intent.DN_LOOKUP, 0.95
        
        # 4. Check rule-based intents
        for pattern, intent in self._intent_rules:
            if pattern.search(message):
                return intent, 0.9
        
        # 5. Check entity-based intents
        if entities.dealer_name or entities.dealer_code:
            return Intent.DEALER_DASHBOARD, 0.85
        
        if entities.city:
            return Intent.CITY_DASHBOARD, 0.85
        
        if entities.warehouse or entities.warehouse_code:
            return Intent.WAREHOUSE_DASHBOARD, 0.85
        
        if entities.product or entities.material_number:
            return Intent.PRODUCT_DASHBOARD, 0.85
        
        # 6. Semantic Router
        semantic_intent, confidence = self._semantic_intent(message)
        if semantic_intent and confidence >= 0.3:
            try:
                return Intent(semantic_intent), confidence
            except ValueError:
                pass
        
        # 7. Default to AI
        return Intent.GENERAL_AI, 0.3
    
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


# =====================================================================================================================
# RESPONSE NORMALIZER
# =====================================================================================================================

class ResponseNormalizer:
    """Unified response normalizer for all service return types"""
    
    def __init__(self):
        self._formatters: List[Callable] = [
            self._extract_whatsapp_message,
            self._extract_formatted_response,
            self._extract_message,
            self._extract_response,
            self._extract_data_to_whatsapp_message,
            self._extract_str_data,
            self._fallback_formatter,
        ]
    
    def normalize(self, result: Any) -> str:
        """Normalize any service return type to WhatsApp-safe string"""
        if result is None:
            return "No response from service. Please try again."
        
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
            return result["whatsapp_message"]
        return None
    
    def _extract_formatted_response(self, result: Any) -> Optional[str]:
        """Extract formatted_response field"""
        if isinstance(result, dict) and "formatted_response" in result:
            return result["formatted_response"]
        return None
    
    def _extract_message(self, result: Any) -> Optional[str]:
        """Extract message field"""
        if isinstance(result, dict) and "message" in result:
            return result["message"]
        return None
    
    def _extract_response(self, result: Any) -> Optional[str]:
        """Extract response field"""
        if isinstance(result, dict) and "response" in result:
            return result["response"]
        return None
    
    def _extract_data_to_whatsapp_message(self, result: Any) -> Optional[str]:
        """Extract data and call to_whatsapp_message"""
        if isinstance(result, dict):
            data = result.get("data")
            if data and hasattr(data, "to_whatsapp_message"):
                return data.to_whatsapp_message()
        return None
    
    def _extract_str_data(self, result: Any) -> Optional[str]:
        """Extract data and convert to string"""
        if isinstance(result, dict):
            data = result.get("data")
            if data and hasattr(data, "__str__"):
                return str(data)
        return None
    
    def _fallback_formatter(self, result: Any) -> Optional[str]:
        """Fallback formatter for any type"""
        if hasattr(result, "__str__"):
            return str(result)
        return None
    
    def _clean_response(self, response: str) -> str:
        """Clean and validate response for WhatsApp"""
        if not response:
            return "No response from service."
        
        # Remove excessive whitespace
        response = re.sub(r'\s+', ' ', response).strip()
        
        # Ensure it's within WhatsApp limits
        if len(response) > WHATSAPP_MAX_MESSAGE_LENGTH:
            # Split into multiple messages if needed
            response = response[:WHATSAPP_MAX_MESSAGE_LENGTH - 100] + "\n\n... (message truncated)"
        
        return response


# =====================================================================================================================
# WHATSAPP VALIDATOR
# =====================================================================================================================

class WhatsAppValidator:
    """WhatsApp message validator with size limits and formatting"""
    
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
        
        # Clean the message
        cleaned = re.sub(r'\s+', ' ', message).strip()
        
        # Truncate if needed
        if len(cleaned) > cls.MAX_MESSAGE_LENGTH:
            cleaned = cleaned[:cls.MAX_MESSAGE_LENGTH - 100] + "\n\n... (message truncated)"
        
        return cleaned


# =====================================================================================================================
# MAIN AI PROVIDER SERVICE
# =====================================================================================================================

class AIProviderService:
    """Enterprise AI orchestration service"""
    
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
        self._cache: Dict[str, Tuple[float, RoutingDecision]] = {}
        self._cache_ttl = CACHE_TTL_SECONDS
        self._initialized = False
        self._initialization_errors: List[str] = []
        
        # Initialize components
        self.registry = ServiceRegistry()
        self.intent_detector = IntentDetectionEngine(self.registry)
        self.response_normalizer = ResponseNormalizer()
        self.validator = WhatsAppValidator()
        
        # Perform startup validation
        self._validate_startup()
        
        self._initialized = True
        self._log_status()
    
    def _validate_startup(self) -> None:
        """Perform comprehensive startup validation"""
        logger.info("=" * 80)
        logger.info("🔍 Starting AI Provider Service Validation...")
        
        # 1. Validate service imports
        logger.info("📦 Validating service imports...")
        for service_name, available in SERVICE_IMPORT_STATUS.items():
            status = "✅" if available else "❌"
            logger.info(f"  {status} {service_name}: {'Available' if available else 'Unavailable'}")
            if not available and service_name in SERVICE_IMPORT_ERRORS:
                self._initialization_errors.append(f"{service_name}: {SERVICE_IMPORT_ERRORS[service_name]}")
        
        # 2. Validate service instances
        logger.info("🔧 Validating service instances...")
        health = self.registry.health_check()
        for service_key, status in health["services"].items():
            status_icon = "✅" if status["instance_exists"] else "❌"
            method_icon = "✅" if status["method_available"] else "❌"
            logger.info(f"  {status_icon} {service_key}: Instance={status['instance_exists']}, Method={method_icon}")
            if not status["instance_exists"]:
                self._initialization_errors.append(f"{service_key}: Instance not available")
            if not status["method_available"]:
                self._initialization_errors.append(f"{service_key}: Method not available")
        
        # 3. Validate Semantic Router
        logger.info(f"🧠 Semantic Router: {'✅' if SEMANTIC_ROUTER_AVAILABLE else '❌'}")
        if not SEMANTIC_ROUTER_AVAILABLE and SEMANTIC_ROUTER_IMPORT_ERROR:
            self._initialization_errors.append(f"Semantic Router: {SEMANTIC_ROUTER_IMPORT_ERROR}")
        
        # 4. Validate Groq Service
        logger.info(f"🤖 Groq Service: {'✅' if GROQ_AVAILABLE else '❌'}")
        if not GROQ_AVAILABLE:
            self._initialization_errors.append("Groq Service: Not available")
        
        # 5. Summarize
        if self._initialization_errors:
            logger.warning(f"⚠️ Validation completed with {len(self._initialization_errors)} issues:")
            for error in self._initialization_errors:
                logger.warning(f"  - {error}")
            logger.warning("Service will run in degraded mode")
        else:
            logger.info("✅ Validation completed successfully - All services available")
        
        logger.info("=" * 80)
    
    def _log_status(self) -> None:
        """Log service initialization status"""
        logger.info("=" * 80)
        logger.info("🤖 AIProviderService Initialized Successfully")
        logger.info(f"  Status: {'✅ Healthy' if not self._initialization_errors else '⚠️ Degraded'}")
        logger.info(f"  Errors: {len(self._initialization_errors)}")
        logger.info(f"  Cache TTL: {self._cache_ttl}s")
        logger.info("=" * 80)
    
    def health_check(self) -> Dict[str, Any]:
        """Get comprehensive health check"""
        return {
            "initialized": self._initialized,
            "healthy": len(self._initialization_errors) == 0,
            "errors": self._initialization_errors,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "cache_size": len(self._cache),
            "services": self.registry.get_service_status(),
            "semantic_router": SEMANTIC_ROUTER_AVAILABLE,
            "groq": GROQ_AVAILABLE,
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    def _normalize_message(self, message: str) -> str:
        """Normalize incoming message"""
        return ' '.join(message.split()).strip()
    
    def _is_menu_request(self, message: str) -> bool:
        """Check if message is a menu request"""
        return message.lower() in {"menu", "main menu", "options", "start", "back", "home", "help", "0"}
    
    def _get_decision_from_entities(self, entities: EntityExtraction) -> Optional[RoutingDecision]:
        """Get routing decision based on entities"""
        # Dealer
        if entities.dealer_name or entities.dealer_code:
            entry = self.registry.get_entry(Intent.DEALER_DASHBOARD)
            if entry and entry.service_instance:
                return RoutingDecision(
                    intent=Intent.DEALER_DASHBOARD,
                    confidence=0.85,
                    service_entry=entry,
                    entity=entities,
                    method=entry.preferred_method,
                    requires_ai=False,
                    reason="Dealer entity detected"
                )
        
        # City
        if entities.city:
            entry = self.registry.get_entry(Intent.CITY_DASHBOARD)
            if entry and entry.service_instance:
                return RoutingDecision(
                    intent=Intent.CITY_DASHBOARD,
                    confidence=0.85,
                    service_entry=entry,
                    entity=entities,
                    method=entry.preferred_method,
                    requires_ai=False,
                    reason="City entity detected"
                )
        
        # Warehouse
        if entities.warehouse or entities.warehouse_code:
            entry = self.registry.get_entry(Intent.WAREHOUSE_DASHBOARD)
            if entry and entry.service_instance:
                return RoutingDecision(
                    intent=Intent.WAREHOUSE_DASHBOARD,
                    confidence=0.85,
                    service_entry=entry,
                    entity=entities,
                    method=entry.preferred_method,
                    requires_ai=False,
                    reason="Warehouse entity detected"
                )
        
        # Product
        if entities.product or entities.material_number:
            entry = self.registry.get_entry(Intent.PRODUCT_DASHBOARD)
            if entry and entry.service_instance:
                return RoutingDecision(
                    intent=Intent.PRODUCT_DASHBOARD,
                    confidence=0.85,
                    service_entry=entry,
                    entity=entities,
                    method=entry.preferred_method,
                    requires_ai=False,
                    reason="Product entity detected"
                )
        
        return None
    
    def _get_parameters_for_method(self, method: Callable, entities: EntityExtraction) -> Dict[str, Any]:
        """Get parameters for method based on signature"""
        sig = inspect.signature(method)
        params = {}
        
        # Get entity mapping
        entry = None
        for e in self.registry._entries.values():
            if e.service_instance and hasattr(e.service_instance, method.__name__):
                entry = e
                break
        
        # Build parameter mapping
        entity_dict = {k: v for k, v in entities.__dict__.items() if v is not None}
        
        for param_name, param in sig.parameters.items():
            # Skip self and cls
            if param_name in ("self", "cls"):
                continue
            
            # Check direct match
            if param_name in entity_dict:
                params[param_name] = entity_dict[param_name]
                continue
            
            # Check mapping
            if entry and param_name in entry.parameter_mapping:
                mapped_key = entry.parameter_mapping[param_name]
                if mapped_key in entity_dict:
                    params[param_name] = entity_dict[mapped_key]
                    continue
            
            # Check if it's a required parameter with no default
            if param.default == inspect.Parameter.empty:
                logger.warning(f"Required parameter '{param_name}' not found in entities")
        
        return params
    
    def _make_routing_decision(self, message: str, entities: EntityExtraction) -> RoutingDecision:
        """Make routing decision using deterministic pipeline"""
        normalized = self._normalize_message(message)
        cache_key = normalized.lower()
        
        # Check cache
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]
        
        # 1. Check for menu
        if self._is_menu_request(normalized):
            entry = self.registry.get_entry(Intent.MENU)
            decision = RoutingDecision(
                intent=Intent.MENU,
                confidence=1.0,
                service_entry=entry,
                entity=entities,
                method="show_main_menu",
                requires_ai=False,
                reason="Menu request detected",
                original_message=message,
                menu_option="0"
            )
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision
        
        # 2. Check for DN
        if entities.dn_number:
            entry = self.registry.get_entry(Intent.DN_LOOKUP)
            if entry and entry.service_instance:
                decision = RoutingDecision(
                    intent=Intent.DN_LOOKUP,
                    confidence=0.95,
                    service_entry=entry,
                    entity=entities,
                    method=entry.preferred_method,
                    requires_ai=False,
                    reason="DN number detected",
                    original_message=message,
                    menu_option=entry.menu_number
                )
                self._cache[cache_key] = (time.monotonic(), decision)
                return decision
        
        # 3. Check for menu number
        menu_match = MENU_NUMBER_PATTERN.match(normalized)
        if menu_match:
            menu_number = menu_match.group(1)
            entry = self.registry.get_entry_by_menu(menu_number)
            if entry:
                decision = RoutingDecision(
                    intent=entry.intent,
                    confidence=1.0,
                    service_entry=entry,
                    entity=entities,
                    method=entry.preferred_method,
                    requires_ai=entry.requires_ai,
                    reason=f"Menu number {menu_number} selected",
                    original_message=message,
                    menu_option=menu_number
                )
                self._cache[cache_key] = (time.monotonic(), decision)
                return decision
        
        # 4. Check entity-based routing
        entity_decision = self._get_decision_from_entities(entities)
        if entity_decision:
            self._cache[cache_key] = (time.monotonic(), entity_decision)
            return entity_decision
        
        # 5. Detect intent
        intent, confidence = self.intent_detector.detect_intent(message, entities)
        entry = self.registry.get_entry(intent)
        
        if entry and entry.service_instance and confidence >= 0.3:
            decision = RoutingDecision(
                intent=intent,
                confidence=confidence,
                service_entry=entry,
                entity=entities,
                method=entry.preferred_method,
                requires_ai=entry.requires_ai,
                reason=f"Intent detected: {intent.value}",
                original_message=message,
                menu_option=entry.menu_number
            )
            self._cache[cache_key] = (time.monotonic(), decision)
            return decision
        
        # 6. Fallback to AI
        ai_entry = self.registry.get_entry(Intent.GENERAL_AI)
        decision = RoutingDecision(
            intent=Intent.GENERAL_AI,
            confidence=0.3,
            service_entry=ai_entry,
            entity=entities,
            method=ai_entry.preferred_method,
            requires_ai=True,
            reason="Fallback to AI",
            original_message=message,
            menu_option=ai_entry.menu_number
        )
        self._cache[cache_key] = (time.monotonic(), decision)
        return decision
    
    def _execute_service(self, decision: RoutingDecision, context: RequestContext) -> str:
        """Execute service with signature-aware invocation"""
        entry = decision.service_entry
        
        # Menu service
        if entry.service_key == "menu_service":
            return get_main_menu()
        
        # Check service availability
        if not entry.service_instance:
            context.error = f"Service {entry.service_key} unavailable"
            return f"⚠️ {entry.menu_name} service is temporarily unavailable. Please try again later."
        
        # Get method
        method = self.registry.get_method(entry, decision.method)
        if not method:
            context.error = f"Method {decision.method} not found"
            return f"⚠️ Service method {decision.method} is temporarily unavailable. Please try again later."
        
        # Prepare parameters
        params = self._get_parameters_for_method(method, decision.entity)
        
        try:
            start_time = time.time()
            
            # Check if method is async
            if inspect.iscoroutinefunction(method):
                # Run async method
                result = asyncio.run(method(**params))
            else:
                # Run sync method
                result = method(**params)
            
            context.database_time_ms = (time.time() - start_time) * 1000
            
            # Normalize response
            normalize_start = time.time()
            normalized_response = self.response_normalizer.normalize(result)
            context.formatting_time_ms = (time.time() - normalize_start) * 1000
            
            # Validate response
            if not self.validator.validate(normalized_response):
                context.error = "Invalid response format"
                return "⚠️ Service returned an invalid response. Please try again."
            
            context.success = True
            context.response_size = len(normalized_response)
            
            return normalized_response
            
        except Exception as exc:
            context.error = str(exc)
            context.success = False
            logger.error(f"Service execution failed: {exc}")
            logger.error(traceback.format_exc())
            
            # Try compatible method if available
            for compatible in entry.compatible_methods:
                if compatible != decision.method:
                    try:
                        method = self.registry.get_method(entry, compatible)
                        if method:
                            params = self._get_parameters_for_method(method, decision.entity)
                            if inspect.iscoroutinefunction(method):
                                result = asyncio.run(method(**params))
                            else:
                                result = method(**params)
                            normalized_response = self.response_normalizer.normalize(result)
                            if self.validator.validate(normalized_response):
                                context.success = True
                                return normalized_response
                    except Exception:
                        continue
            
            return f"⚠️ Service error: {str(exc)[:100]}\n\nPlease try again or type 'menu' for options."
    
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
            normalized_message=self._normalize_message(message),
            start_time=time.time()
        )
        
        self._request_count += 1
        
        if not message or not message.strip():
            return get_main_menu()
        
        try:
            logger.info(f"[{context.request_id}] Processing request #{self._request_count} from {sender or 'unknown'}")
            
            # Extract entities
            entities = self.intent_detector.entity_extractor.extract(message)
            context.entities = entities
            
            # Make routing decision
            decision = self._make_routing_decision(message, entities)
            context.intent = decision.intent
            context.confidence = decision.confidence
            context.service_key = decision.service_entry.service_key
            context.method = decision.method
            context.ai_used = decision.requires_ai
            
            logger.info(f"[{context.request_id}] Route: {decision.intent.value} -> {decision.service_entry.service_key}.{decision.method} ({decision.reason})")
            
            # Execute service
            response = self._execute_service(decision, context)
            
            # Prepare for WhatsApp
            final_response = self.validator.prepare(response)
            context.total_time_ms = context.elapsed_ms()
            
            # Log success
            logger.info(
                f"[{context.request_id}] ✅ Response sent successfully "
                f"(total: {context.total_time_ms:.2f}ms, "
                f"db: {context.database_time_ms:.2f}ms, "
                f"format: {context.formatting_time_ms:.2f}ms)"
            )
            logger.debug(f"[{context.request_id}] Response size: {len(final_response)} chars")
            
            return final_response
            
        except Exception as exc:
            context.error = str(exc)
            context.success = False
            context.total_time_ms = context.elapsed_ms()
            self._error_count += 1
            
            logger.error(f"[{context.request_id}] ❌ Processing failed: {exc}")
            logger.error(traceback.format_exc())
            
            # Return menu for menu requests
            if context.normalized_message in {"menu", "main menu", "help", "start", "0"}:
                return get_main_menu()
            
            return "⚠️ Service is temporarily unavailable. Reply *menu* to try again."
    
    def show_main_menu(self) -> str:
        """Show main menu"""
        return get_main_menu()
    
    def get_status(self) -> Dict[str, Any]:
        """Get service status"""
        return {
            "initialized": self._initialized,
            "healthy": len(self._initialization_errors) == 0,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "error_rate": self._error_count / max(1, self._request_count),
            "cache_size": len(self._cache),
            "errors": self._initialization_errors,
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
                    # Create minimal instance that can at least show menu
                    _ai_service = AIProviderService.__new__(AIProviderService)
                    _ai_service._initialized = False
                    _ai_service._initialization_errors = [str(exc)]
                    _ai_service._request_count = 0
                    _ai_service._error_count = 0
                    _ai_service._cache = {}
                    _ai_service._cache_ttl = CACHE_TTL_SECONDS
                    _ai_service.registry = ServiceRegistry()
                    _ai_service.intent_detector = IntentDetectionEngine(_ai_service.registry)
                    _ai_service.response_normalizer = ResponseNormalizer()
                    _ai_service.validator = WhatsAppValidator()
                    logger.warning("⚠️ AIProviderService running in minimal mode - only menu will work")
    return _ai_service


def get_whatsapp_provider_service() -> AIProviderService:
    """Backward-compatible factory for webhook"""
    return get_ai_provider_service()


# =====================================================================================================================
# MODULE-LEVEL FUNCTION - BACKWARD COMPATIBLE
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
        logger.error(f"Unexpected failure in process_whatsapp_query: {exc}")
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
]
