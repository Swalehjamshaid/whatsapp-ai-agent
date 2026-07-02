"""
File: app/services/ai_provider_service.py
Version: 21.1 - DIAGNOSTIC VERSION WITH FULL DEBUG LOGGING
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
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


# =====================================================================================================================
# DATA CLASSES
# =====================================================================================================================

@dataclass
class ServiceRegistryEntry:
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
    intent: Intent
    confidence: float
    service_entry: ServiceRegistryEntry
    entity: EntityExtraction
    method: str
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    menu_option: Optional[str] = None


@dataclass
class RequestContext:
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
# SERVICE IMPORTS WITH FULL DEBUGGING
# =====================================================================================================================

SERVICE_IMPORT_STATUS: Dict[str, bool] = {}
SERVICE_IMPORT_ERRORS: Dict[str, str] = {}

def _import_service_instance(service_name: str, import_path: str, class_name: str, factory_method: Optional[str] = None) -> Optional[Any]:
    """Import and instantiate a service with full debug logging"""
    try:
        logger.info(f"🔍 Attempting to import {service_name} from {import_path}")
        module = __import__(import_path, fromlist=[class_name])
        
        if factory_method:
            logger.info(f"🔍 Using factory method: {factory_method}")
            factory = getattr(module, factory_method)
            instance = factory()
        else:
            logger.info(f"🔍 Instantiating class: {class_name}")
            service_class = getattr(module, class_name)
            instance = service_class()
        
        SERVICE_IMPORT_STATUS[service_name] = True
        logger.info(f"✅ {service_name} imported and instantiated successfully: {instance}")
        return instance
    except Exception as exc:
        SERVICE_IMPORT_STATUS[service_name] = False
        SERVICE_IMPORT_ERRORS[service_name] = str(exc)
        logger.error(f"❌ Failed to initialize {service_name}: {exc}")
        logger.error(traceback.format_exc())
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
    def __init__(self):
        self._entries: Dict[Intent, ServiceRegistryEntry] = {}
        self._method_cache: Dict[str, Callable] = {}
        self._signature_cache: Dict[str, inspect.Signature] = {}
        self._lock = threading.RLock()
        self._initialize_registry()
    
    def _initialize_registry(self) -> None:
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
                example_queries=["Track DN 6243698820", "6243698820"]
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
                example_queries=["Show Lahore dashboard", "Lahore"]
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
                example_queries=["Show dealer Taj Electronics"]
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
                example_queries=["Pending DNs"]
            ),
            
            Intent.GENERAL_AI: ServiceRegistryEntry(
                menu_number="9",
                menu_name="AI Query",
                intent=Intent.GENERAL_AI,
                service_key="groq",
                service_file="app.services.groq_service",
                service_class="GroqService",
                preferred_method="process_query",
                compatible_methods=["process_query", "ask_ai"],
                required_entities=[],
                parameter_mapping={"message": "message"},
                requires_ai=True,
                description="General AI assistant",
                example_queries=["What's the issue"]
            ),
        }
        
        # Attach service instances
        for intent, entry in self._entries.items():
            self._attach_service_instance(entry)
    
    def _attach_service_instance(self, entry: ServiceRegistryEntry) -> None:
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
        return self._entries.get(intent)
    
    def get_entry_by_menu(self, menu_number: str) -> Optional[ServiceRegistryEntry]:
        for entry in self._entries.values():
            if entry.menu_number == menu_number:
                return entry
        return None
    
    def get_method(self, entry: ServiceRegistryEntry, method_name: str) -> Optional[Callable]:
        if not entry.service_instance:
            logger.error(f"❌ Service instance is None for {entry.service_key}")
            return None
        
        cache_key = f"{entry.service_key}_{method_name}"
        
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]
        
        # Check if method exists
        logger.info(f"🔍 Looking for method '{method_name}' on {entry.service_key}")
        
        if hasattr(entry.service_instance, method_name):
            method = getattr(entry.service_instance, method_name)
            if callable(method):
                logger.info(f"✅ Found method '{method_name}' on {entry.service_key}")
                self._method_cache[cache_key] = method
                self._signature_cache[cache_key] = inspect.signature(method)
                return method
            else:
                logger.error(f"❌ '{method_name}' exists but is not callable on {entry.service_key}")
        else:
            logger.error(f"❌ Method '{method_name}' not found on {entry.service_key}")
            # List available methods for debugging
            available = [m for m in dir(entry.service_instance) if not m.startswith('_') and callable(getattr(entry.service_instance, m))]
            logger.info(f"📋 Available methods on {entry.service_key}: {available[:10]}")
        
        # Try compatible methods
        for compatible_method in entry.compatible_methods:
            if hasattr(entry.service_instance, compatible_method):
                method = getattr(entry.service_instance, compatible_method)
                if callable(method):
                    logger.info(f"✅ Using compatible method '{compatible_method}' instead")
                    self._method_cache[cache_key] = method
                    self._signature_cache[cache_key] = inspect.signature(method)
                    return method
        
        logger.error(f"❌ No compatible method found for {method_name} on {entry.service_key}")
        return None


# =====================================================================================================================
# ENTITY EXTRACTION ENGINE
# =====================================================================================================================

class EntityExtractionEngine:
    def __init__(self):
        self._patterns = self._compile_patterns()
        self._city_names = self._load_city_names()
    
    def _compile_patterns(self) -> Dict[str, re.Pattern]:
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
        return {
            "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
            "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
            "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
            "dera ghazi khan", "gwadar", "rahim yar khan"
        }
    
    def extract(self, message: str) -> EntityExtraction:
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
        
        # Extract City
        city_match = self._patterns["city"].search(message)
        if city_match:
            entities.city = city_match.group(1).capitalize()
        
        # Extract Warehouse
        warehouse_match = self._patterns["warehouse"].search(message)
        if warehouse_match:
            entities.warehouse = warehouse_match.group(1).strip()
        
        # Extract Product
        product_match = self._patterns["product"].search(message)
        if product_match:
            entities.product = product_match.group(1).strip()
        
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
    def __init__(self, registry: ServiceRegistry):
        self.registry = registry
        self.entity_extractor = EntityExtractionEngine()
        self._router = None
        self._router_initialized = False
        self._router_lock = threading.Lock()
        self._menu_triggers = {"menu", "main menu", "options", "start", "back", "home", "help", "0"}
    
    def detect_intent(self, message: str, entities: EntityExtraction) -> Tuple[Intent, float]:
        message_lower = message.lower().strip()
        
        if message_lower in self._menu_triggers:
            return Intent.MENU, 1.0
        
        if entities.dn_number:
            return Intent.DN_LOOKUP, 0.95
        
        if entities.dealer_name:
            return Intent.DEALER_DASHBOARD, 0.85
        
        if entities.city:
            return Intent.CITY_DASHBOARD, 0.85
        
        if entities.warehouse:
            return Intent.WAREHOUSE_DASHBOARD, 0.85
        
        if entities.product:
            return Intent.PRODUCT_DASHBOARD, 0.85
        
        return Intent.GENERAL_AI, 0.3


# =====================================================================================================================
# RESPONSE NORMALIZER
# =====================================================================================================================

class ResponseNormalizer:
    def normalize(self, result: Any) -> str:
        if result is None:
            return "No response from service."
        
        # Try different extraction methods
        if isinstance(result, dict):
            # Check for whatsapp_message
            if "whatsapp_message" in result and result["whatsapp_message"]:
                return result["whatsapp_message"]
            
            # Check for message
            if "message" in result and result["message"]:
                return result["message"]
            
            # Check for response
            if "response" in result and result["response"]:
                return result["response"]
            
            # Check for formatted_response
            if "formatted_response" in result and result["formatted_response"]:
                return result["formatted_response"]
            
            # Check data object
            if "data" in result and result["data"]:
                data = result["data"]
                if hasattr(data, "to_whatsapp_message"):
                    return data.to_whatsapp_message()
                elif hasattr(data, "__str__"):
                    return str(data)
                elif isinstance(data, dict):
                    # Try to format the dict nicely
                    lines = []
                    for key, value in data.items():
                        if not key.startswith('_') and value is not None:
                            lines.append(f"{key}: {value}")
                    if lines:
                        return "\n".join(lines)
        
        # Fallback to string
        if hasattr(result, "__str__"):
            return str(result)
        
        return "No response from service."


# =====================================================================================================================
# WHATSAPP VALIDATOR
# =====================================================================================================================

class WhatsAppValidator:
    MAX_MESSAGE_LENGTH = 4096
    
    @classmethod
    def validate(cls, message: str) -> bool:
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
        if not message:
            return "No response from service."
        
        cleaned = re.sub(r'\s+', ' ', message).strip()
        
        if len(cleaned) > cls.MAX_MESSAGE_LENGTH:
            cleaned = cleaned[:cls.MAX_MESSAGE_LENGTH - 100] + "\n\n... (message truncated)"
        
        return cleaned


# =====================================================================================================================
# MAIN AI PROVIDER SERVICE WITH FULL DEBUGGING
# =====================================================================================================================

class AIProviderService:
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
        
        logger.info("=" * 80)
        logger.info("🔧 Initializing AIProviderService...")
        
        self.registry = ServiceRegistry()
        self.intent_detector = IntentDetectionEngine(self.registry)
        self.response_normalizer = ResponseNormalizer()
        self.validator = WhatsAppValidator()
        
        # Log service status
        logger.info("📊 Service Status:")
        for intent, entry in self.registry._entries.items():
            status = "✅" if entry.service_instance else "❌"
            logger.info(f"  {status} {entry.service_key}: {entry.menu_name}")
            if entry.service_instance:
                logger.info(f"     Methods available: {[m for m in dir(entry.service_instance) if not m.startswith('_') and callable(getattr(entry.service_instance, m))][:5]}")
        
        self._initialized = True
        logger.info("✅ AIProviderService initialized")
        logger.info("=" * 80)
    
    def _get_parameters_for_method(self, method: Callable, entities: EntityExtraction) -> Dict[str, Any]:
        """Get parameters with full debugging"""
        sig = inspect.signature(method)
        params = {}
        entity_dict = {k: v for k, v in entities.__dict__.items() if v is not None}
        
        logger.info(f"🔍 Method signature: {sig}")
        logger.info(f"🔍 Available entities: {entity_dict}")
        
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            
            if param.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
                continue
            
            if param_name in entity_dict:
                params[param_name] = entity_dict[param_name]
                logger.info(f"  ✅ Using {param_name}={entity_dict[param_name]}")
            elif param.default != inspect.Parameter.empty:
                logger.info(f"  ℹ️ Using default for {param_name}")
            else:
                logger.warning(f"  ⚠️ Required parameter '{param_name}' not found in entities")
        
        return params
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        sender = sender or sender_id
        request_id = str(uuid.uuid4())[:8]
        
        self._request_count += 1
        
        if not message or not message.strip():
            return get_main_menu()
        
        logger.info("=" * 60)
        logger.info(f"[{request_id}] 📨 Processing request #{self._request_count}")
        logger.info(f"[{request_id}] Sender: {sender or 'unknown'}")
        logger.info(f"[{request_id}] Message: {message[:100]}")
        
        try:
            # Extract entities
            entities = self.intent_detector.entity_extractor.extract(message)
            entity_dict = {k: v for k, v in entities.__dict__.items() if v is not None}
            logger.info(f"[{request_id}] 🔍 Extracted entities: {entity_dict}")
            
            # Make routing decision
            intent, confidence = self.intent_detector.detect_intent(message, entities)
            logger.info(f"[{request_id}] 🎯 Intent: {intent.value} (confidence: {confidence:.2f})")
            
            entry = self.registry.get_entry(intent)
            if not entry:
                logger.error(f"[{request_id}] ❌ No service entry for intent: {intent}")
                return f"⚠️ Service not found for intent: {intent.value}"
            
            logger.info(f"[{request_id}] 📦 Service: {entry.service_key} -> {entry.preferred_method}")
            
            # Get method
            method = self.registry.get_method(entry, entry.preferred_method)
            if not method:
                logger.error(f"[{request_id}] ❌ Method {entry.preferred_method} not found")
                return f"⚠️ Service method {entry.preferred_method} is temporarily unavailable."
            
            # Prepare parameters
            params = self._get_parameters_for_method(method, entities)
            logger.info(f"[{request_id}] 📝 Calling with params: {params}")
            
            # Execute method
            try:
                start_time = time.time()
                
                if inspect.iscoroutinefunction(method):
                    logger.info(f"[{request_id}] 🔄 Calling async method")
                    result = await method(**params)
                else:
                    logger.info(f"[{request_id}] 🔄 Calling sync method")
                    result = method(**params)
                
                execution_time = (time.time() - start_time) * 1000
                logger.info(f"[{request_id}] ⏱️ Execution time: {execution_time:.2f}ms")
                logger.info(f"[{request_id}] 📤 Result type: {type(result)}")
                logger.info(f"[{request_id}] 📤 Result preview: {str(result)[:200]}")
                
                # Normalize response
                normalized = self.response_normalizer.normalize(result)
                logger.info(f"[{request_id}] 📝 Normalized response: {normalized[:200]}")
                
                # Validate
                if not self.validator.validate(normalized):
                    logger.error(f"[{request_id}] ❌ Invalid response")
                    return "⚠️ Service returned an invalid response. Please try again."
                
                # Prepare for WhatsApp
                final_response = self.validator.prepare(normalized)
                
                logger.info(f"[{request_id}] ✅ Response size: {len(final_response)} chars")
                logger.info(f"[{request_id}] ✅ Request completed successfully")
                logger.info("=" * 60)
                
                return final_response
                
            except Exception as exc:
                logger.error(f"[{request_id}] ❌ Method execution failed: {exc}")
                logger.error(traceback.format_exc())
                return f"⚠️ Service error: {str(exc)[:100]}\n\nPlease try again or type 'menu'."
            
        except Exception as exc:
            self._error_count += 1
            logger.error(f"[{request_id}] ❌ Processing failed: {exc}")
            logger.error(traceback.format_exc())
            logger.info("=" * 60)
            
            if message.lower() in {"menu", "main menu", "help", "start", "0"}:
                return get_main_menu()
            
            return "⚠️ Service is temporarily unavailable. Reply *menu* to try again."
    
    def show_main_menu(self) -> str:
        return get_main_menu()


# =====================================================================================================================
# SINGLETON INSTANCE
# =====================================================================================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()


def get_ai_provider_service() -> AIProviderService:
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                try:
                    _ai_service = AIProviderService()
                except Exception as exc:
                    logger.error(f"❌ Failed to create AIProviderService: {exc}")
                    logger.error(traceback.format_exc())
                    _ai_service = AIProviderService.__new__(AIProviderService)
                    _ai_service._initialized = False
                    _ai_service.registry = ServiceRegistry()
                    _ai_service.intent_detector = IntentDetectionEngine(_ai_service.registry)
                    _ai_service.response_normalizer = ResponseNormalizer()
                    _ai_service.validator = WhatsAppValidator()
                    _ai_service._cache = {}
                    _ai_service._cache_ttl = CACHE_TTL_SECONDS
                    _ai_service._request_count = 0
                    _ai_service._error_count = 0
    return _ai_service


def get_whatsapp_provider_service() -> AIProviderService:
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
]
