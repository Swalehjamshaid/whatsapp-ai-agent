"""
Enterprise orchestration entry point for WhatsApp AI requests.

The module coordinates intent detection, business-service dispatch, optional AI
enhancement, and response validation.  It intentionally contains no SQL,
analytics, KPI calculation, dashboard construction, or domain business rules.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any, Final, Protocol

import orjson
from cachetools import TTLCache
from dependency_injector import containers, providers
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)


class OrchestrationError(RuntimeError):
    """Base class for safe orchestration failures."""


class ConfigurationError(OrchestrationError):
    """Configuration or dependency resolution failure."""
    pass


class ServiceUnavailableError(OrchestrationError):
    """Service dependency cannot be loaded or is misconfigured."""
    pass


class MethodNotFoundError(OrchestrationError):
    """Requested method does not exist on the resolved service."""
    pass


class DatabaseConnectionError(OrchestrationError):
    """Compatibility exception for domain services that wrap DB failures."""
    pass


class RoutingError(OrchestrationError):
    """Request could not be routed to a valid service method."""
    pass


class GroqError(OrchestrationError):
    """AI enhancement service error."""
    pass


class IntentDetectionError(OrchestrationError):
    """Intent engine failed to produce a valid routing decision."""
    pass


class ServiceRequest(BaseModel):
    """Validated request context passed through the orchestration pipeline."""

    model_config = ConfigDict(str_strip_whitespace=True)

    request_id: str
    message: str = Field(min_length=1, max_length=16_000)
    sender: str | None = Field(default=None, max_length=255)
    intent: str | None = None
    entity: Any = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("message")
    @classmethod
    def reject_control_only_input(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Message cannot be empty")
        return normalized


# Preserve imports used by integrations built against the preceding name.
RequestInput = ServiceRequest


class ServiceResponse(BaseModel):
    """Canonical boundary shared by all orchestrated services."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    success: bool
    data: Any = Field(default_factory=dict)
    whatsapp_message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    request_id: str = ""
    processing_time: float = Field(default=0.0, ge=0.0)


class RoutingDecisionView(BaseModel):
    """Read-only validated view; the intent engine's object is never mutated."""

    model_config = ConfigDict(extra="allow", frozen=True)

    intent: str
    service_key: str | None = None
    method: str | None = None
    entity: Any = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_ai: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RouteTarget:
    provider_name: str
    method: str


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    message: str
    sender: str | None
    started_at: float


class ProviderResolver(Protocol):
    def __call__(self, name: str) -> Any: ...


# ============================================================
# ROUTING TABLE - ALL BUSINESS INTENTS
# ============================================================

ROUTES: Final[dict[str, RouteTarget]] = {
    # DN Service Routes
    "dn_lookup": RouteTarget("dn_service", "get_dn_dashboard"),
    "dn_search": RouteTarget("dn_service", "get_dn_dashboard"),
    "dn_dashboard": RouteTarget("dn_service", "get_dn_dashboard"),
    "dn_status": RouteTarget("dn_service", "get_dn_status"),
    "dn_history": RouteTarget("dn_service", "get_dn_history"),
    "search_dns": RouteTarget("dn_service", "search_dns"),
    "dn_summary": RouteTarget("dn_service", "get_dn_summary"),
    "pending_dn": RouteTarget("dn_service", "get_pending_dns"),
    "pending_dns": RouteTarget("dn_service", "get_pending_dns"),
    "pending_pgi": RouteTarget("dn_service", "get_pending_pgi"),
    "pending_pod": RouteTarget("dn_service", "get_pending_pod"),
    "recent_dns": RouteTarget("dn_service", "get_recent_dns"),
    "oldest_pending": RouteTarget("dn_service", "get_oldest_pending"),
    "delivery_timeline": RouteTarget("dn_service", "get_delivery_timeline"),
    "transit_analysis": RouteTarget("dn_service", "get_transit_analysis"),
    
    # Dealer Service Routes
    "dealer_dashboard": RouteTarget("dealer_service", "get_dealer_dashboard"),
    "dealer_revenue": RouteTarget("dealer_service", "get_dealer_dashboard"),
    "dealer_pending": RouteTarget("dealer_service", "get_dealer_dashboard"),
    "dealer_comparison": RouteTarget("dealer_service", "compare_dealers"),
    "top_dealers": RouteTarget("dealer_service", "get_top_dealers"),
    "dealer_ranking": RouteTarget("dealer_service", "get_top_dealers"),
    
    # Warehouse Service Routes
    "warehouse_dashboard": RouteTarget("warehouse_service", "get_warehouse_dashboard"),
    
    # City Service Routes
    "city_dashboard": RouteTarget("city_service", "get_city_dashboard"),
    
    # Product Service Routes
    "product_dashboard": RouteTarget("product_service", "get_product_dashboard"),
    
    # KPI Service Routes
    "national_kpi": RouteTarget("kpi_service", "get_national_kpi_dashboard"),
    "national_kpi_dashboard": RouteTarget("kpi_service", "get_national_kpi_dashboard"),
    
    # General AI Fallback
    "general_ai": RouteTarget("groq_service", "process_query"),
    "greeting": RouteTarget("groq_service", "process_query"),
    "help": RouteTarget("groq_service", "process_query"),
    "unknown": RouteTarget("groq_service", "process_query"),
}


# ============================================================
# SERVICE SYMBOL RESOLUTION
# ============================================================

_SYMBOLS: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "dn_service": ("app.services.dn_analysis", ("DNAnalysisService", "DNService")),
    "dealer_service": (
        "app.services.dealer_analytics_service",
        ("DealerAnalyticsService", "DealerService"),
    ),
    "warehouse_service": ("app.services.warehouse_service", ("WarehouseService",)),
    "city_service": ("app.services.city_service", ("CityService",)),
    "product_service": ("app.services.product_service", ("ProductService",)),
    "kpi_service": (
        "app.services.kpi_service",
        ("KPIService", "KpiService", "NationalKPIService"),
    ),
    "groq_service": ("app.services.groq_service", ("GroqService",)),
    "intent_engine": (
        "app.services.ai_provider_service_intents",
        ("IntentDetectionEngine", "IntentEngine"),
    ),
}


# ============================================================
# FALLBACK INTENT ENGINE - UPDATED WITH DEALER DETECTION
# ============================================================

class FallbackIntentEngine:
    """
    Simple regex-based intent detection when the primary intent engine fails.
    This ensures the orchestrator never crashes due to intent engine issues.
    Updated to detect dealer names naturally without requiring "dealer" keyword.
    """
    
    _DN_PATTERN = re.compile(r'(?<!\d)(\d{6,20})(?!\d)')
    _DEALER_PATTERNS = re.compile(
        r'(?:dealer|dealers?)\s+(?:for\s+)?([a-zA-Z0-9\s_\-]+)',
        re.IGNORECASE
    )
    
    # Dealer name detection without requiring "dealer" keyword
    _DEALER_NAME_PATTERN = re.compile(
        r'^(?:umar|taj|haroon|commercial|national|mian|mgc|arco|shah|haji|sons|electronics|distributors|traders|foods|group|pvt|ltd|wah|abbottabad|haripur|gilget|rawalpindi)\s*[\w\s]+|^[\w\s]+(?:electronics|distributors|traders|foods|group|pvt|ltd|sons|brothers)',
        re.IGNORECASE
    )
    
    # Common dealer names for exact matching
    _COMMON_DEALERS = frozenset({
        "umar electronics wah",
        "umar electronics",
        "taj electronics",
        "haroon electronics", 
        "commercial electronics",
        "national foods",
        "mian group chakwal",
        "mian group",
        "arco electronics",
        "shah electronics",
        "haji sharaf ud din & sons",
        "haji sharaf",
        "haji sharaf ud din",
    })
    
    # Dealer indicators - words that suggest a message is about a dealer
    _DEALER_INDICATORS = frozenset({
        "electronics", "traders", "distributors", "foods", 
        "group", "pvt", "ltd", "sons", "brothers", "enterprises"
    })
    
    _WAREHOUSE_PATTERNS = re.compile(
        r'(?:warehouse|wh)\s+(?:for\s+)?([a-zA-Z0-9\s_\-]+)',
        re.IGNORECASE
    )
    _CITY_PATTERNS = re.compile(
        r'(?:city)\s+(?:of\s+)?([a-zA-Z\s]+)',
        re.IGNORECASE
    )
    _PENDING_KEYWORDS = re.compile(
        r'(?:pending|not\s+delivered|overdue|late)',
        re.IGNORECASE
    )
    _SUMMARY_KEYWORDS = re.compile(
        r'(?:summary|overview|total|statistics|stats)',
        re.IGNORECASE
    )
    _RECENT_KEYWORDS = re.compile(
        r'(?:recent|latest|newest|today)',
        re.IGNORECASE
    )
    _TOP_KEYWORDS = re.compile(
        r'(?:top|best|highest|leading)',
        re.IGNORECASE
    )
    _GREETING_PATTERNS = re.compile(
        r'^(?:hi|hello|hey|good morning|good afternoon|good evening|hola|namaste|salam|howdy)',
        re.IGNORECASE
    )
    _HELP_PATTERNS = re.compile(
        r'(?:help|assist|support|how\s+to|what\s+is|explain)',
        re.IGNORECASE
    )
    
    @classmethod
    def detect(cls, message: str) -> dict[str, Any]:
        """Detect intent using regex patterns with enhanced dealer detection."""
        message_lower = message.lower().strip()
        message_original = message.strip()
        
        # Check for DN number first
        dn_match = cls._DN_PATTERN.search(message)
        if dn_match:
            return {
                "intent": "dn_lookup",
                "service_key": "dn_service",
                "method": "get_dn_dashboard",
                "entity": dn_match.group(1),
                "confidence": 1.0,
                "requires_ai": False,
                "reason": "DN number detected in message",
            }
        
        # Check for exact dealer name match
        msg_lower = message_lower
        for dealer in cls._COMMON_DEALERS:
            if dealer in msg_lower:
                return {
                    "intent": "dealer_dashboard",
                    "service_key": "dealer_service",
                    "method": "get_dealer_dashboard",
                    "entity": message_original,
                    "confidence": 0.95,
                    "requires_ai": False,
                    "reason": f"Exact dealer match: {dealer}",
                }
        
        # Check if message looks like a dealer name (natural language)
        if cls._DEALER_NAME_PATTERN.search(message_lower):
            # Additional check: if message is short (2-5 words) and doesn't contain query keywords
            word_count = len(message_lower.split())
            if word_count <= 6:
                # Check if it's a query about something else
                query_keywords = ['pending', 'summary', 'recent', 'top', 'compare', 'vs', 'versus', 'revenue', 'units', 'dn', 'delivery']
                if not any(keyword in message_lower for keyword in query_keywords):
                    return {
                        "intent": "dealer_dashboard",
                        "service_key": "dealer_service",
                        "method": "get_dealer_dashboard",
                        "entity": message_original,
                        "confidence": 0.85,
                        "requires_ai": False,
                        "reason": "Dealer name detected (natural language)",
                    }
        
        # Check if message contains dealer indicators
        words = message_lower.split()
        for word in words:
            if word in cls._DEALER_INDICATORS:
                # Check if the message is likely a dealer name
                if len(words) <= 5:
                    return {
                        "intent": "dealer_dashboard",
                        "service_key": "dealer_service",
                        "method": "get_dealer_dashboard",
                        "entity": message_original,
                        "confidence": 0.75,
                        "requires_ai": False,
                        "reason": "Dealer indicator detected",
                    }
        
        # Check for greeting
        if cls._GREETING_PATTERNS.match(message_lower):
            return {
                "intent": "greeting",
                "service_key": "groq_service",
                "method": "process_query",
                "entity": message,
                "confidence": 1.0,
                "requires_ai": True,
                "reason": "Greeting detected",
            }
        
        # Check for help
        if cls._HELP_PATTERNS.search(message_lower):
            return {
                "intent": "help",
                "service_key": "groq_service",
                "method": "process_query",
                "entity": message,
                "confidence": 0.9,
                "requires_ai": True,
                "reason": "Help request detected",
            }
        
        # Check for summary
        if cls._SUMMARY_KEYWORDS.search(message_lower):
            return {
                "intent": "dn_summary",
                "service_key": "dn_service",
                "method": "get_dn_summary",
                "entity": None,
                "confidence": 0.8,
                "requires_ai": False,
                "reason": "Summary request detected",
            }
        
        # Check for pending
        if cls._PENDING_KEYWORDS.search(message_lower):
            if "pod" in message_lower:
                return {
                    "intent": "pending_pod",
                    "service_key": "dn_service",
                    "method": "get_pending_pod",
                    "entity": None,
                    "confidence": 0.85,
                    "requires_ai": False,
                    "reason": "Pending POD detected",
                }
            elif "pgi" in message_lower:
                return {
                    "intent": "pending_pgi",
                    "service_key": "dn_service",
                    "method": "get_pending_pgi",
                    "entity": None,
                    "confidence": 0.85,
                    "requires_ai": False,
                    "reason": "Pending PGI detected",
                }
            else:
                return {
                    "intent": "pending_dns",
                    "service_key": "dn_service",
                    "method": "get_pending_dns",
                    "entity": None,
                    "confidence": 0.85,
                    "requires_ai": False,
                    "reason": "Pending DNs detected",
                }
        
        # Check for recent
        if cls._RECENT_KEYWORDS.search(message_lower):
            return {
                "intent": "recent_dns",
                "service_key": "dn_service",
                "method": "get_recent_dns",
                "entity": None,
                "confidence": 0.7,
                "requires_ai": False,
                "reason": "Recent DNs requested",
            }
        
        # Check for dealer with "dealer" keyword
        dealer_match = cls._DEALER_PATTERNS.search(message)
        if dealer_match:
            return {
                "intent": "dealer_dashboard",
                "service_key": "dealer_service",
                "method": "get_dealer_dashboard",
                "entity": dealer_match.group(1).strip(),
                "confidence": 0.8,
                "requires_ai": False,
                "reason": "Dealer name detected with keyword",
            }
        
        # Check for top dealers
        if cls._TOP_KEYWORDS.search(message_lower) and "dealer" in message_lower:
            return {
                "intent": "top_dealers",
                "service_key": "dealer_service",
                "method": "get_top_dealers",
                "entity": None,
                "confidence": 0.75,
                "requires_ai": False,
                "reason": "Top dealers requested",
            }
        
        # Check for warehouse
        warehouse_match = cls._WAREHOUSE_PATTERNS.search(message)
        if warehouse_match:
            return {
                "intent": "warehouse_dashboard",
                "service_key": "warehouse_service",
                "method": "get_warehouse_dashboard",
                "entity": warehouse_match.group(1).strip(),
                "confidence": 0.75,
                "requires_ai": False,
                "reason": "Warehouse name detected",
            }
        
        # Check for city
        city_match = cls._CITY_PATTERNS.search(message)
        if city_match:
            return {
                "intent": "city_dashboard",
                "service_key": "city_service",
                "method": "get_city_dashboard",
                "entity": city_match.group(1).strip(),
                "confidence": 0.75,
                "requires_ai": False,
                "reason": "City name detected",
            }
        
        # Default to general AI
        return {
            "intent": "general_ai",
            "service_key": "groq_service",
            "method": "process_query",
            "entity": message,
            "confidence": 0.5,
            "requires_ai": True,
            "reason": "No specific pattern matched - using general AI",
        }


# ============================================================
# COMPONENT LOADER WITH FALLBACK
# ============================================================

def _load_component(key: str) -> Any:
    """
    Load one configured singleton lazily with comprehensive error handling.
    If the primary component fails, attempts to load a fallback.
    """
    logger.info(f"Attempting to load component: {key}")
    
    try:
        module_name, candidates = _SYMBOLS[key]
    except KeyError as exc:
        raise ConfigurationError(f"Unknown component: {key}") from exc
    
    # Special handling for intent_engine - always provide fallback
    if key == "intent_engine":
        try:
            module = importlib.import_module(module_name)
            for symbol in candidates:
                component = getattr(module, symbol, None)
                if component is not None:
                    try:
                        return component()
                    except TypeError as exc:
                        logger.warning(f"Intent engine {symbol} constructor failed: {exc}")
                        continue
        except (ImportError, AttributeError, TypeError) as exc:
            logger.error(f"Failed to load primary intent engine from {module_name}: {exc}")
            logger.info("Using fallback intent engine")
            return FallbackIntentEngine()
    
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        logger.error(f"Cannot import module {module_name}: {exc}")
        # For groq_service, we might continue without it
        if key == "groq_service":
            logger.warning("Groq service unavailable - continuing without AI enhancement")
            return None
        raise ConfigurationError(f"Cannot import {module_name}") from exc
    
    # Function-oriented service modules are already fully configured
    routed_methods = {
        target.method
        for target in ROUTES.values()
        if target.provider_name == key
    }
    
    if routed_methods and any(
        callable(getattr(module, method, None))
        for method in routed_methods
    ):
        return module
    
    # Try to instantiate a class-based component
    for symbol in candidates:
        component = getattr(module, symbol, None)
        if component is not None:
            try:
                return component()
            except TypeError as exc:
                logger.error(f"{module_name}.{symbol} requires dependencies: {exc}")
                continue
    
    # Function-oriented modules are valid service implementations
    if key != "intent_engine":
        return module
    
    # Final fallback
    logger.warning(f"No supported component found in {module_name} - using fallback")
    if key == "intent_engine":
        return FallbackIntentEngine()
    
    raise ConfigurationError(f"No supported component found in {module_name}")


# ============================================================
# DEPENDENCY CONTAINER
# ============================================================

class ApplicationContainer(containers.DeclarativeContainer):
    """Dependency-injector registry; applications may override any provider."""

    config = providers.Configuration()
    intent_engine = providers.ThreadSafeSingleton(_load_component, "intent_engine")
    dn_service = providers.ThreadSafeSingleton(_load_component, "dn_service")
    dealer_service = providers.ThreadSafeSingleton(_load_component, "dealer_service")
    warehouse_service = providers.ThreadSafeSingleton(_load_component, "warehouse_service")
    city_service = providers.ThreadSafeSingleton(_load_component, "city_service")
    product_service = providers.ThreadSafeSingleton(_load_component, "product_service")
    kpi_service = providers.ThreadSafeSingleton(_load_component, "kpi_service")
    groq_service = providers.ThreadSafeSingleton(_load_component, "groq_service")


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _object_mapping(value: Any) -> dict[str, Any]:
    """Convert various object types to a dictionary."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    
    attributes: dict[str, Any] = {}
    for name in (
        "intent", "service_key", "service", "method", "entity", "confidence",
        "requires_ai", "needs_groq", "reason", "parameters", "params", "arguments",
    ):
        if hasattr(value, name):
            attributes[name] = getattr(value, name)
    return attributes


def _decision_view(decision: Any) -> RoutingDecisionView:
    """Convert any routing decision to a validated view."""
    raw = _object_mapping(decision)
    if not raw:
        raise ValueError("Intent engine returned an unsupported routing decision")
    
    return RoutingDecisionView.model_validate({
        "intent": raw.get("intent") or raw.get("service_key") or "general_ai",
        "service_key": raw.get("service_key") or raw.get("service"),
        "method": raw.get("method"),
        "entity": raw.get("entity"),
        "confidence": raw.get("confidence") or 0.0,
        "requires_ai": raw.get("requires_ai", raw.get("needs_groq", False)),
        "reason": raw.get("reason") or "",
    })


async def _call(callable_object: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute sync implementations off-loop and await native async ones."""
    if inspect.iscoroutinefunction(callable_object):
        return await callable_object(*args, **kwargs)
    result = await asyncio.to_thread(partial(callable_object, *args, **kwargs))
    if inspect.isawaitable(result):
        return await result
    return result


# ============================================================
# SERVICE ROUTER
# ============================================================

class ServiceRouter:
    """Resolve, execute, and validate services via a data-driven route table."""

    def __init__(
        self,
        resolver: ProviderResolver,
        routes: Mapping[str, RouteTarget] = ROUTES,
        *,
        timeout_seconds: float = 20.0,
        retry_attempts: int = 3,
    ) -> None:
        self._resolver = resolver
        self._routes = dict(routes)
        self._timeout = timeout_seconds
        self._attempts = retry_attempts
        self._method_cache: TTLCache[tuple[str, str], Callable[..., Any]] = TTLCache(256, 300)

    def target_for(self, decision: RoutingDecisionView) -> RouteTarget:
        intent_key = decision.intent.strip().casefold()
        configured = self._routes.get(intent_key)
        if configured is None and decision.service_key:
            configured = self._routes.get(decision.service_key.strip().casefold())
        
        # If still no route, check if it might be a dealer
        if configured is None:
            entity = str(decision.entity or "")
            # Check for dealer indicators
            dealer_indicators = ["electronics", "traders", "distributors", "foods", "group", "pvt", "ltd", "sons", "brothers"]
            if any(indicator in entity.lower() for indicator in dealer_indicators):
                return RouteTarget("dealer_service", "get_dealer_dashboard")
        
        if configured is None:
            raise ServiceUnavailableError(f"No route configured for intent '{decision.intent}'")
        return RouteTarget(configured.provider_name, decision.method or configured.method)

    def _resolve_method(self, target: RouteTarget) -> Callable[..., Any]:
        key = (target.provider_name, target.method)
        if key in self._method_cache:
            return self._method_cache[key]
        
        try:
            service = self._resolver(target.provider_name)
        except (ConfigurationError, ImportError) as exc:
            raise ServiceUnavailableError(f"Service '{target.provider_name}' is unavailable") from exc
        
        if service is None:
            raise ServiceUnavailableError(f"Service '{target.provider_name}' resolved to None")
        
        method = getattr(service, target.method, None)
        if not callable(method):
            raise MethodNotFoundError(
                f"Method '{target.method}' is unavailable on '{target.provider_name}'"
            )
        
        self._method_cache[key] = method
        return method

    @staticmethod
    def _arguments(
        method: Callable[..., Any],
        decision: Any,
        message: str,
        target: RouteTarget,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        """Bind intent output safely to the selected public service signature."""
        raw = _object_mapping(decision)
        supplied = raw.get("parameters") or raw.get("params") or raw.get("arguments")
        entity = raw.get("entity")
        
        signature = inspect.signature(method)
        parameters = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.name != "self"
        ]
        
        if not parameters:
            return (), {}
        
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        named = {
            parameter.name
            for parameter in parameters
            if parameter.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        }
        
        if isinstance(supplied, Mapping):
            kwargs = dict(supplied) if accepts_kwargs else {
                key: value for key, value in supplied.items() if key in named
            }
            if kwargs:
                signature.bind(**kwargs)
                return (), kwargs

        positional = [
            parameter
            for parameter in parameters
            if parameter.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        
        if not positional:
            keyword_only = [
                parameter
                for parameter in parameters
                if parameter.kind is inspect.Parameter.KEYWORD_ONLY
            ]
            if not keyword_only:
                return (), {}
            
            value = entity if entity not in (None, "", {}) else message
            
            # Special handling for DN service
            if target.provider_name == "dn_service":
                match = re.search(r"(?<!\d)(\d{6,20})(?!\d)", str(value))
                if match is None:
                    match = re.search(r"(?<!\d)(\d{6,20})(?!\d)", message)
                if match is None:
                    raise RoutingError("A valid DN number was not found in the request")
                value = match.group(1)
            
            kwargs = {keyword_only[0].name: value}
            signature.bind(**kwargs)
            return (), kwargs

        # Positional binding
        value: Any = None
        if isinstance(entity, Mapping):
            first_name = positional[0].name
            aliases = (
                first_name,
                "dn_no",
                "dn",
                "value",
                "id",
                "name",
                "query",
            )
            value = next(
                (entity[key] for key in aliases if entity.get(key) not in (None, "")),
                None,
            )
        elif entity not in (None, "", {}):
            value = entity

        # DN number extraction
        if target.provider_name == "dn_service":
            candidate = str(value or message)
            match = re.search(r"(?<!\d)(\d{6,20})(?!\d)", candidate)
            if match is None:
                match = re.search(r"(?<!\d)(\d{6,20})(?!\d)", message)
            if match is None:
                raise RoutingError("A valid DN number was not found in the request")
            value = match.group(1)
        elif value in (None, "", {}):
            value = message

        signature.bind(value)
        return (value,), {}

    async def execute(
        self,
        decision_object: Any,
        decision: RoutingDecisionView,
        message: str,
        request_id: str,
    ) -> ServiceResponse:
        target = self.target_for(decision)
        method = self._resolve_method(target)
        args, kwargs = self._arguments(method, decision_object, message, target)
        
        transient = (TimeoutError, ConnectionError, DatabaseConnectionError)
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._attempts),
                wait=wait_exponential_jitter(initial=0.25, max=2.0),
                retry=retry_if_exception_type(transient),
                reraise=True,
            ):
                with attempt:
                    raw_response = await asyncio.wait_for(
                        _call(method, *args, **kwargs), timeout=self._timeout
                    )
            return self.validate_response(raw_response, request_id)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"{target.provider_name}.{target.method} timed out") from exc

    @staticmethod
    def validate_response(response: Any, request_id: str) -> ServiceResponse:
        if isinstance(response, ServiceResponse):
            return response.model_copy(update={"request_id": response.request_id or request_id})
        if isinstance(response, str):
            return ServiceResponse(success=True, whatsapp_message=response, request_id=request_id)
        
        raw = _object_mapping(response)
        if not raw:
            raise ServiceUnavailableError("Service returned an empty or unsupported response")
        
        raw.setdefault("success", not bool(raw.get("error")))
        raw.setdefault("data", {})
        raw.setdefault("whatsapp_message", "")
        raw.setdefault("metadata", {})
        raw.setdefault("error", "")
        raw["request_id"] = raw.get("request_id") or request_id
        return ServiceResponse.model_validate(raw)


# ============================================================
# AI PROVIDER ORCHESTRATOR
# ============================================================

class AIProviderOrchestrator:
    """Single orchestration use case invoked by the webhook layer."""

    _INTENT_METHODS: Final[tuple[str, ...]] = (
        "get_routing_decision", "detect_intent", "route", "analyze", "classify",
    )
    _GROQ_METHODS: Final[tuple[str, ...]] = (
        "enhance_response", "generate_response", "process_structured_data", "process_query",
    )

    def __init__(
        self,
        container: ApplicationContainer,
        *,
        request_timeout_seconds: float = 30.0,
        cache_ttl: int = 300,
    ) -> None:
        self.container = container
        self.request_timeout = request_timeout_seconds
        self.intent_cache: TTLCache[str, Any] = TTLCache(2_048, cache_ttl)
        self.metadata_cache: TTLCache[str, Any] = TTLCache(128, cache_ttl)
        self.router = ServiceRouter(self._resolve_provider)
        # Backward-compatible registry facade expected by webhook.py
        self.registry = self
        
        # Track if groq is available
        self._groq_available = True
        try:
            self._resolve_provider("groq_service")
        except Exception:
            self._groq_available = False
            logger.warning("Groq service is unavailable - AI enhancement disabled")

    def _resolve_provider(self, name: str) -> Any:
        provider = getattr(self.container, name, None)
        if provider is None or not callable(provider):
            raise ConfigurationError(f"Dependency provider '{name}' is not registered")
        return provider()

    @staticmethod
    def _intent_cache_key(message: str, sender: str | None) -> str:
        canonical = orjson.dumps({"message": message.casefold(), "sender": sender or ""})
        return canonical.hex()

    def _find_callable(self, service: Any, candidates: tuple[str, ...], label: str) -> Callable[..., Any] | None:
        """Find a callable method with graceful failure."""
        if service is None:
            logger.warning(f"Service {label} is None")
            return None
        
        for name in candidates:
            method = getattr(service, name, None)
            if callable(method):
                return method
        
        if callable(service):
            return service
        
        logger.warning(f"{label} exposes none of: {', '.join(candidates)}")
        return None

    @staticmethod
    def _root_cause(exc: BaseException) -> BaseException:
        root = exc
        visited: set[int] = set()
        while id(root) not in visited:
            visited.add(id(root))
            next_error = root.__cause__ or root.__context__
            if next_error is None:
                break
            root = next_error
        return root

    @staticmethod
    def _raw_response_fallback(data: Any) -> str:
        """Return readable structured data when a presentation layer is absent."""
        try:
            rendered = orjson.dumps(
                data,
                option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS,
                default=str,
            ).decode("utf-8")
        except (TypeError, ValueError, orjson.JSONEncodeError):
            rendered = str(data)
        return rendered[:4_000]

    async def _detect_intent(self, message: str, sender: str | None) -> tuple[Any, bool]:
        """Detect intent with fallback support and dealer detection."""
        key = self._intent_cache_key(message, sender)
        if key in self.intent_cache:
            logger.debug(f"Intent cache hit for message: {message[:50]}...")
            return self.intent_cache[key], True
        
        try:
            engine = self._resolve_provider("intent_engine")
            method = self._find_callable(engine, self._INTENT_METHODS, "Intent engine")
            
            if method is None:
                logger.warning("Intent engine methods not found - using fallback")
                decision = FallbackIntentEngine.detect(message)
            else:
                kwargs: dict[str, Any] = {}
                parameters = inspect.signature(method).parameters
                if "sender" in parameters:
                    kwargs["sender"] = sender
                elif "user_id" in parameters:
                    kwargs["user_id"] = sender
                
                decision = await asyncio.wait_for(_call(method, message, **kwargs), timeout=10.0)
                
        except (ImportError, ConfigurationError, TimeoutError, asyncio.TimeoutError, AttributeError) as exc:
            logger.error(f"Intent detection failed: {exc} - using fallback")
            decision = FallbackIntentEngine.detect(message)
        except Exception as exc:
            logger.exception(f"Unexpected intent detection error: {exc} - using fallback")
            decision = FallbackIntentEngine.detect(message)
        
        # Validate the decision before caching
        try:
            _decision_view(decision)
        except (ValueError, ValidationError) as exc:
            logger.error(f"Invalid routing decision: {exc} - using fallback")
            decision = FallbackIntentEngine.detect(message)
        
        # If decision is general_ai, check if it might be a dealer
        if decision.get("intent") == "general_ai":
            msg_lower = message.lower()
            dealer_indicators = ["electronics", "traders", "distributors", "foods", "group", "pvt", "ltd", "sons", "brothers"]
            dealer_names = ["umar", "taj", "haroon", "commercial", "national", "mian", "mgc", "arco", "shah", "haji"]
            
            # Check if message contains dealer indicators or common dealer names
            has_indicator = any(indicator in msg_lower for indicator in dealer_indicators)
            has_dealer_name = any(name in msg_lower for name in dealer_names)
            
            # If it has dealer indicators and is short (2-5 words), treat as dealer
            word_count = len(msg_lower.split())
            if (has_indicator or has_dealer_name) and word_count <= 6:
                decision = {
                    "intent": "dealer_dashboard",
                    "service_key": "dealer_service",
                    "method": "get_dealer_dashboard",
                    "entity": message.strip(),
                    "confidence": 0.8,
                    "requires_ai": False,
                    "reason": "Dealer detected (fallback - general_ai override)",
                }
        
        self.intent_cache[key] = decision
        return decision, False

    async def _enhance(
        self,
        decision: RoutingDecisionView,
        business_response: ServiceResponse,
        message: str,
        request_id: str,
    ) -> ServiceResponse:
        """Enhance response with AI if available."""
        if not self._groq_available:
            logger.debug("Groq service unavailable - skipping AI enhancement")
            return business_response
        
        try:
            groq = self._resolve_provider("groq_service")
            if groq is None:
                logger.warning("Groq service resolved to None - skipping AI enhancement")
                return business_response
            
            method = self._find_callable(groq, self._GROQ_METHODS, "Groq service")
            if method is None:
                logger.warning("Groq service methods not found - skipping AI enhancement")
                return business_response
            
            structured = {
                "request_id": request_id,
                "intent": decision.intent,
                "entity": decision.entity,
                "user_message": message,
                "business_result": business_response.model_dump(mode="json"),
            }
            
            parameters = inspect.signature(method).parameters
            if len(parameters) == 1:
                enhanced = await _call(method, structured)
            else:
                enhanced = await _call(method, message, structured)
            
            if isinstance(enhanced, str):
                return business_response.model_copy(update={"whatsapp_message": enhanced})
            
            validated = ServiceRouter.validate_response(enhanced, request_id)
            return business_response.model_copy(update={
                "whatsapp_message": validated.whatsapp_message or business_response.whatsapp_message,
                "metadata": business_response.metadata | {"ai_enhanced": True},
            })
        except Exception as exc:
            root = self._root_cause(exc)
            logger.opt(exception=True).error(
                "Optional Groq enhancement failed; returning business response "
                "exception_type={} exception_message={!r} root_cause_type={} "
                "root_cause={!r}",
                type(exc).__name__,
                str(exc),
                type(root).__name__,
                str(root),
            )
            return business_response.model_copy(update={
                "metadata": business_response.metadata
                | {
                    "ai_enhanced": False,
                    "groq_error_type": type(exc).__name__,
                    "groq_error": str(exc),
                }
            })

    async def process(
        self,
        message: str,
        sender: str | None = None,
        **context: Any,
    ) -> str:
        request_id = str(context.get("request_id") or uuid.uuid4())
        started = time.perf_counter()
        bound = logger.bind(request_id=request_id, sender=sender)
        stage = "request_validation"
        decision: RoutingDecisionView | None = None
        target: RouteTarget | None = None
        
        try:
            bound.info("Request received original_message={!r}", message)
            request = ServiceRequest(
                request_id=request_id,
                message=message,
                sender=sender,
                metadata=dict(context),
            )
            bound.info("Request normalized normalized_message={!r}", request.message)
            
            stage = "intent_detection"
            decision_object, cache_hit = await self._detect_intent(request.message, request.sender)
            decision = _decision_view(decision_object)
            
            stage = "routing"
            target = self.router.target_for(decision)
            bound = bound.bind(
                intent=decision.intent,
                confidence=decision.confidence,
                service=target.provider_name,
                method=target.method,
            )
            bound.info(
                "Routing decision entity={!r} reason={!r} cache_hit={} cache_miss={}",
                decision.entity,
                decision.reason,
                cache_hit,
                not cache_hit,
            )
            
            stage = "business_service_execution"
            service_started = time.perf_counter()
            business_response = await asyncio.wait_for(
                self.router.execute(
                    decision_object, decision, request.message, request_id
                ),
                timeout=self.request_timeout,
            )
            service_ms = (time.perf_counter() - service_started) * 1000
            
            groq_ms = 0.0
            if decision.requires_ai and target.provider_name != "groq_service":
                stage = "groq_enhancement"
                groq_started = time.perf_counter()
                business_response = await self._enhance(
                    decision, business_response, request.message, request_id
                )
                groq_ms = (time.perf_counter() - groq_started) * 1000
            
            stage = "response_formatting"
            elapsed = (time.perf_counter() - started) * 1000
            business_response = business_response.model_copy(
                update={"processing_time": elapsed}
            )
            
            bound.info(
                "Request completed success={} service_time_ms={:.2f} groq_time_ms={:.2f} "
                "total_time_ms={:.2f} response_length={}",
                business_response.success,
                service_ms,
                groq_ms,
                elapsed,
                len(business_response.whatsapp_message),
            )
            
            # Return the response
            if business_response.whatsapp_message:
                return business_response.whatsapp_message
            if business_response.success:
                return self._raw_response_fallback(business_response.data)
            
            error = business_response.error.strip()
            if target.provider_name == "dn_service" and "not found" in error.casefold():
                dn_match = re.search(r"(?<!\d)(\d{6,20})(?!\d)", request.message)
                dn_no = dn_match.group(1) if dn_match else str(decision.entity)
                return f"DN {dn_no} was not found in PostgreSQL."
            
            if any(token in error.casefold() for token in ("database", "connection", "sql", "timeout")):
                return "Database is currently unavailable."
            
            return error or f"Service execution failed. Reference ID: {request_id}"
            
        except ValidationError as exc:
            bound.opt(exception=True).error(
                "Failure stage={} exception_type={} exception_message={!r}",
                stage, type(exc).__name__, str(exc),
            )
            return "Please send a valid, non-empty request."
            
        except RoutingError as exc:
            bound.opt(exception=True).error(
                "Failure stage={} exception_type={} exception_message={!r}",
                stage, type(exc).__name__, str(exc)
            )
            return f"{exc}. Reference ID: {request_id}"
            
        except MethodNotFoundError as exc:
            bound.opt(exception=True).error(
                "Failure stage={} exception_type={} exception_message={!r}",
                stage, type(exc).__name__, str(exc)
            )
            service_name = target.provider_name if target else "Selected service"
            return f"{service_name} does not support the requested operation. Reference ID: {request_id}"
            
        except (ServiceUnavailableError, ConfigurationError, ImportError) as exc:
            bound.opt(exception=True).error(
                "Failure stage={} exception_type={} exception_message={!r}",
                stage, type(exc).__name__, str(exc)
            )
            service_name = target.provider_name if target else "Requested service"
            return f"{service_name} is unavailable. Reference ID: {request_id}"
            
        except (TimeoutError, asyncio.TimeoutError) as exc:
            bound.opt(exception=True).error(
                "Failure stage={} exception_type={} exception_message={!r}",
                stage, type(exc).__name__, str(exc)
            )
            return f"The request timed out. Reference ID: {request_id}"
            
        except (DatabaseConnectionError, SQLAlchemyError, ConnectionError) as exc:
            root = self._root_cause(exc)
            bound.opt(exception=True).error(
                "Failure stage={} database_status=unavailable exception_type={} "
                "exception_message={!r} root_cause_type={} root_cause={!r}",
                stage, type(exc).__name__, str(exc), type(root).__name__, str(root),
            )
            return f"Database is currently unavailable. Reference ID: {request_id}"
            
        except (AttributeError, ValueError, TypeError, KeyError, IndexError, RuntimeError, OSError) as exc:
            root = self._root_cause(exc)
            bound.opt(exception=True).error(
                "Failure stage={} intent={} entity={!r} service={} method={} "
                "exception_type={} exception_message={!r} root_cause_type={} "
                "root_cause={!r} execution_time_ms={:.2f}",
                stage,
                decision.intent if decision else None,
                decision.entity if decision else None,
                target.provider_name if target else None,
                target.method if target else None,
                type(exc).__name__,
                str(exc),
                type(root).__name__,
                str(root),
                (time.perf_counter() - started) * 1000,
            )
            return f"Unexpected internal error. Reference ID: {request_id}"
            
        except Exception as exc:
            root = self._root_cause(exc)
            bound.opt(exception=True).critical(
                "Unhandled failure stage={} intent={} entity={!r} service={} method={} "
                "exception_type={} exception_message={!r} root_cause_type={} "
                "root_cause={!r} execution_time_ms={:.2f}",
                stage,
                decision.intent if decision else None,
                decision.entity if decision else None,
                target.provider_name if target else None,
                target.method if target else None,
                type(exc).__name__,
                str(exc),
                type(root).__name__,
                str(root),
                (time.perf_counter() - started) * 1000,
            )
            return f"Unexpected internal error. Reference ID: {request_id}"

    async def process_whatsapp_query(
        self,
        message: str,
        sender: str | None = None,
        **context: Any,
    ) -> str:
        """Compatibility entry point used on the provider-service instance."""
        if sender is None:
            sender = context.pop("sender_id", None) or context.pop("phone_number", None)
        return await self.process(message, sender, **context)

    async def process_query(
        self,
        message: str,
        sender: str | None = None,
        **context: Any,
    ) -> str:
        """Compatibility alias for callers resolving the singleton directly."""
        return await self.process_whatsapp_query(message, sender, **context)

    async def enhance_response(
        self,
        response: Any,
        message: str = "",
        **context: Any,
    ) -> str:
        """Enhance an existing business response without changing its data."""
        request_id = str(context.get("request_id") or uuid.uuid4())
        business_response = ServiceRouter.validate_response(response, request_id)
        decision = RoutingDecisionView(
            intent=str(context.get("intent") or "general_ai"),
            entity=context.get("entity"),
            confidence=float(context.get("confidence") or 1.0),
            requires_ai=True,
            reason="Explicit response enhancement",
        )
        
        try:
            enhanced = await asyncio.wait_for(
                self._enhance(decision, business_response, message, request_id),
                timeout=self.request_timeout,
            )
            return (
                enhanced.whatsapp_message
                or business_response.whatsapp_message
                or self._raw_response_fallback(business_response.data)
            )
        except Exception as exc:
            root = self._root_cause(exc)
            logger.bind(request_id=request_id).opt(exception=True).error(
                "Response enhancement failed exception_type={} exception_message={!r} "
                "root_cause_type={} root_cause={!r}; returning business response",
                type(exc).__name__,
                str(exc),
                type(root).__name__,
                str(root),
            )
            return (
                business_response.whatsapp_message
                or self._raw_response_fallback(business_response.data)
            )

    def get_registry_status(self, *, refresh: bool = False) -> dict[str, Any]:
        """Validate imports, instances, and routed methods without business calls."""
        cache_key = "service_registry"
        if not refresh and cache_key in self.metadata_cache:
            return self.metadata_cache[cache_key]
        
        routed_methods: dict[str, set[str]] = {}
        for target in ROUTES.values():
            routed_methods.setdefault(target.provider_name, set()).add(target.method)
        routed_methods["intent_engine"] = set()
        
        statuses: dict[str, dict[str, Any]] = {}
        for provider_name, methods in routed_methods.items():
            try:
                service = self._resolve_provider(provider_name)
                
                if provider_name == "intent_engine":
                    method = self._find_callable(service, self._INTENT_METHODS, "Intent engine")
                    if method is None:
                        # Check if it's the fallback engine
                        if isinstance(service, FallbackIntentEngine):
                            missing = []
                        else:
                            missing = ["intent methods not found"]
                    else:
                        missing = []
                else:
                    missing = sorted(
                        method for method in methods
                        if not callable(getattr(service, method, None))
                    )
                
                metadata_method = getattr(service, "get_service_metadata", None)
                metadata = metadata_method() if callable(metadata_method) and not inspect.iscoroutinefunction(metadata_method) else {}
                
                statuses[provider_name] = {
                    "available": not missing,
                    "class": type(service).__name__,
                    "module": getattr(service, "__name__", type(service).__module__),
                    "methods": sorted(methods),
                    "missing_methods": missing,
                    "metadata": metadata if isinstance(metadata, Mapping) else {},
                    "reason": "" if not missing else f"Missing methods: {', '.join(missing)}",
                }
            except (ConfigurationError, ImportError, MethodNotFoundError, TypeError) as exc:
                logger.exception("Service registry validation failed for {}", provider_name)
                statuses[provider_name] = {
                    "available": False,
                    "methods": sorted(methods),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
        
        result = {
            "healthy": all(status["available"] for status in statuses.values()),
            "services": statuses,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "cache_ttl_seconds": 300,
            "fallback_engine_active": isinstance(
                self._resolve_provider("intent_engine"), FallbackIntentEngine
            ) if statuses.get("intent_engine", {}).get("available") else False,
        }
        self.metadata_cache[cache_key] = result
        return result

    def get_service_registry_status(self) -> dict[str, Any]:
        """Compatibility report consumed by the existing webhook startup code."""
        report = self.get_registry_status()
        services = report["services"]
        ready = sum(1 for status in services.values() if status.get("available"))
        total = len(services)
        return report | {
            "ready": ready,
            "in_development": total - ready,
            "total": total,
            "readiness_score": (ready / total * 100.0) if total else 0.0,
        }

    @staticmethod
    def _provider_key(service_key: str) -> str:
        aliases = {
            "dn": "dn_service",
            "dealer": "dealer_service",
            "warehouse": "warehouse_service",
            "city": "city_service",
            "product": "product_service",
            "kpi": "kpi_service",
            "groq": "groq_service",
            "intent": "intent_engine",
        }
        return aliases.get(service_key, service_key)

    def get_service_status(self, service_key: str) -> dict[str, Any]:
        """Compatibility method exposed through ``service.registry``."""
        provider_key = self._provider_key(service_key)
        status = self.get_registry_status()["services"].get(provider_key)
        if status is None:
            return {"ready": False, "status": "NOT_REGISTERED", "service": service_key}
        return status | {
            "ready": bool(status.get("available")),
            "status": "READY" if status.get("available") else "UNAVAILABLE",
            "service": service_key,
        }

    def get_service_instance(self, service_key: str) -> Any | None:
        """Resolve a registered singleton for legacy diagnostics endpoints."""
        provider_key = self._provider_key(service_key)
        try:
            return self._resolve_provider(provider_key)
        except (ConfigurationError, ImportError, TypeError):
            logger.exception("Unable to resolve registry service {}", service_key)
            return None

    def refresh_status(self) -> dict[str, Any]:
        """Clear diagnostic caches and revalidate the entire service registry."""
        self.metadata_cache.clear()
        self.router._method_cache.clear()
        return self.get_registry_status(refresh=True)

    async def health_check(self) -> dict[str, Any]:
        """Resolve and probe every dependency without running business logic."""
        if "health" in self.metadata_cache:
            return self.metadata_cache["health"]
        
        checks: dict[str, dict[str, Any]] = {}
        for name in (
            "intent_engine", "dn_service", "dealer_service", "warehouse_service",
            "city_service", "product_service", "kpi_service", "groq_service",
        ):
            try:
                service = self._resolve_provider(name)
                if service is None:
                    checks[name] = {"healthy": False, "error": "Service resolved to None"}
                    continue
                
                health = getattr(service, "health_check", None)
                if callable(health):
                    result = await asyncio.wait_for(_call(health), 5.0)
                    checks[name] = {"healthy": True, "details": result}
                else:
                    checks[name] = {"healthy": True, "details": {"resolved": True}}
            except (ConfigurationError, ImportError, TimeoutError, asyncio.TimeoutError) as exc:
                logger.exception("Startup health check failed for {}", name)
                checks[name] = {"healthy": False, "error": type(exc).__name__}
        
        result = {
            "healthy": all(item["healthy"] for item in checks.values()),
            "services": checks,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self.metadata_cache["health"] = result
        return result


# ============================================================
# SINGLETON INSTANCES
# ============================================================

container = ApplicationContainer()
orchestrator = AIProviderOrchestrator(container)

# Preserve the historical service class name used by imports and type checks.
WhatsAppProviderService = AIProviderOrchestrator


# ============================================================
# MODULE-LEVEL FUNCTIONS - BACKWARD COMPATIBLE
# ============================================================

async def process_whatsapp_query(
    message: str,
    sender: str | None = None,
    **context: Any,
) -> str:
    """Primary backward-compatible webhook entry point."""
    return await orchestrator.process_whatsapp_query(message, sender, **context)


async def process_query(
    message: str,
    sender: str | None = None,
    **context: Any,
) -> str:
    """Compatibility alias used by older webhook implementations."""
    return await process_whatsapp_query(message, sender, **context)


async def enhance_response(
    response: Any,
    message: str = "",
    **context: Any,
) -> str:
    """Backward-compatible module-level response enhancement entry point."""
    return await orchestrator.enhance_response(response, message, **context)


async def health_check() -> dict[str, Any]:
    return await orchestrator.health_check()


def get_whatsapp_provider_service() -> AIProviderOrchestrator:
    """Return the process-wide, dependency-injected orchestrator singleton."""
    return orchestrator


def get_service_registry_status() -> dict[str, Any]:
    """Return cached service-registry diagnostics."""
    return orchestrator.get_registry_status()


def validate_all_services() -> dict[str, Any]:
    """Validate every registered service and routed method."""
    return orchestrator.get_registry_status(refresh=True)


def refresh_service_status() -> dict[str, Any]:
    """Invalidate diagnostic state and return a fresh registry report."""
    return orchestrator.refresh_status()


def get_system_health() -> dict[str, Any]:
    """Synchronous health snapshot suitable for existing status endpoints."""
    registry = orchestrator.get_registry_status()
    return {
        "healthy": registry["healthy"],
        "status": "healthy" if registry["healthy"] else "unhealthy",
        "reason": "" if registry["healthy"] else "One or more services failed validation",
        "services": registry["services"],
        "checked_at": registry["checked_at"],
        "fallback_engine_active": registry.get("fallback_engine_active", False),
    }


__all__ = [
    "AIProviderOrchestrator",
    "ApplicationContainer",
    "ConfigurationError",
    "DatabaseConnectionError",
    "FallbackIntentEngine",
    "MethodNotFoundError",
    "ROUTES",
    "RouteTarget",
    "RequestInput",
    "ServiceRequest",
    "ServiceResponse",
    "ServiceRouter",
    "ServiceUnavailableError",
    "WhatsAppProviderService",
    "container",
    "enhance_response",
    "get_service_registry_status",
    "get_system_health",
    "get_whatsapp_provider_service",
    "health_check",
    "orchestrator",
    "process_query",
    "process_whatsapp_query",
    "refresh_service_status",
    "validate_all_services",
]
