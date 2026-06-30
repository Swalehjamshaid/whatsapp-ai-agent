"""Enterprise orchestration entry point for WhatsApp AI requests.

The module coordinates intent detection, business-service dispatch, optional AI
enhancement, and response validation.  It intentionally contains no SQL,
analytics, KPI calculation, dashboard construction, or domain business rules.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from functools import partial
from typing import Any, Final, Protocol

import orjson
from cachetools import TTLCache
from dependency_injector import containers, providers
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)


class OrchestrationError(RuntimeError):
    """Base class for safe orchestration failures."""


class ConfigurationError(OrchestrationError):
    pass


class ServiceUnavailableError(OrchestrationError):
    pass


class MethodNotFoundError(OrchestrationError):
    pass


class DatabaseConnectionError(OrchestrationError):
    """Compatibility exception for domain services that wrap DB failures."""


class RequestInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=16_000)
    sender: str | None = Field(default=None, max_length=255)

    @field_validator("message")
    @classmethod
    def reject_control_only_input(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Message cannot be empty")
        return normalized


class ServiceResponse(BaseModel):
    """Canonical boundary shared by all orchestrated services."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    success: bool
    data: Any = Field(default_factory=dict)
    whatsapp_message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    request_id: str = ""


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


ROUTES: Final[dict[str, RouteTarget]] = {
    "dn_lookup": RouteTarget("dn_service", "get_dn_dashboard"),
    "dn_dashboard": RouteTarget("dn_service", "get_dn_dashboard"),
    "pending_dn": RouteTarget("dn_service", "get_pending_dns"),
    "pending_dns": RouteTarget("dn_service", "get_pending_dns"),
    "pending_pgi": RouteTarget("dn_service", "get_pending_pgi"),
    "pending_pod": RouteTarget("dn_service", "get_pending_pod"),
    "recent_dns": RouteTarget("dn_service", "get_recent_dns"),
    "oldest_pending": RouteTarget("dn_service", "get_oldest_pending"),
    "delivery_timeline": RouteTarget("dn_service", "get_delivery_timeline"),
    "transit_analysis": RouteTarget("dn_service", "get_transit_analysis"),
    "dealer_dashboard": RouteTarget("dealer_service", "get_dealer_dashboard"),
    "dealer_comparison": RouteTarget("dealer_service", "compare_dealers"),
    "top_dealers": RouteTarget("dealer_service", "get_top_dealers"),
    "warehouse_dashboard": RouteTarget("warehouse_service", "get_warehouse_dashboard"),
    "city_dashboard": RouteTarget("city_service", "get_city_dashboard"),
    "product_dashboard": RouteTarget("product_service", "get_product_dashboard"),
    "general_ai": RouteTarget("groq_service", "process_query"),
}


_SYMBOLS: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "dn_service": ("app.services.dn_analysis", ("DNAnalysisService", "DNService")),
    "dealer_service": ("app.services.dealer_service", ("DealerService",)),
    "warehouse_service": ("app.services.warehouse_service", ("WarehouseService",)),
    "city_service": ("app.services.city_service", ("CityService",)),
    "product_service": ("app.services.product_service", ("ProductService",)),
    "groq_service": ("app.services.groq_service", ("GroqService",)),
    "intent_engine": (
        "app.services.ai_provider_service_intents",
        ("IntentDetectionEngine", "IntentEngine"),
    ),
}


def _load_component(key: str) -> Any:
    """Load one configured singleton lazily, preserving fast module import."""
    try:
        module_name, candidates = _SYMBOLS[key]
    except KeyError as exc:
        raise ConfigurationError(f"Unknown component: {key}") from exc
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigurationError(f"Cannot import {module_name}") from exc
    for symbol in candidates:
        component = getattr(module, symbol, None)
        if component is not None:
            try:
                return component()
            except TypeError as exc:
                raise ConfigurationError(
                    f"{module_name}.{symbol} requires dependencies; override its container provider"
                ) from exc
    # Function-oriented modules are valid service implementations.
    if key != "intent_engine":
        return module
    raise ConfigurationError(f"No supported component found in {module_name}")


class ApplicationContainer(containers.DeclarativeContainer):
    """Dependency-injector registry; applications may override any provider."""

    config = providers.Configuration()
    intent_engine = providers.Singleton(_load_component, "intent_engine")
    dn_service = providers.Singleton(_load_component, "dn_service")
    dealer_service = providers.Singleton(_load_component, "dealer_service")
    warehouse_service = providers.Singleton(_load_component, "warehouse_service")
    city_service = providers.Singleton(_load_component, "city_service")
    product_service = providers.Singleton(_load_component, "product_service")
    groq_service = providers.Singleton(_load_component, "groq_service")


def _object_mapping(value: Any) -> dict[str, Any]:
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
        configured = self._routes.get(decision.intent)
        if configured is None and decision.service_key:
            configured = self._routes.get(decision.service_key)
        if configured is None:
            raise ServiceUnavailableError(f"No route configured for intent '{decision.intent}'")
        # Explicit intent-engine method selection is allowed only on the selected service.
        return RouteTarget(configured.provider_name, decision.method or configured.method)

    def _resolve_method(self, target: RouteTarget) -> Callable[..., Any]:
        key = (target.provider_name, target.method)
        if key in self._method_cache:
            return self._method_cache[key]
        try:
            service = self._resolver(target.provider_name)
        except (ConfigurationError, ImportError) as exc:
            raise ServiceUnavailableError(f"Service '{target.provider_name}' is unavailable") from exc
        method = getattr(service, target.method, None)
        if not callable(method):
            raise MethodNotFoundError(
                f"Method '{target.method}' is unavailable on '{target.provider_name}'"
            )
        self._method_cache[key] = method
        return method

    @staticmethod
    def _arguments(method: Callable[..., Any], decision: Any, message: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
        raw = _object_mapping(decision)
        supplied = raw.get("parameters") or raw.get("params") or raw.get("arguments")
        if isinstance(supplied, Mapping):
            return (), dict(supplied)
        entity = raw.get("entity")
        signature = inspect.signature(method)
        required = [
            parameter for parameter in signature.parameters.values()
            if parameter.name != "self"
            and parameter.default is inspect.Parameter.empty
            and parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if not required:
            return (), {}
        value = entity if entity not in (None, "", {}) else message
        if isinstance(value, Mapping):
            return (), dict(value)
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
        args, kwargs = self._arguments(method, decision_object, message)
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

    def _resolve_provider(self, name: str) -> Any:
        provider = getattr(self.container, name, None)
        if provider is None or not callable(provider):
            raise ConfigurationError(f"Dependency provider '{name}' is not registered")
        return provider()

    @staticmethod
    def _intent_cache_key(message: str, sender: str | None) -> str:
        canonical = orjson.dumps({"message": message.casefold(), "sender": sender or ""})
        return canonical.hex()

    def _find_callable(self, service: Any, candidates: tuple[str, ...], label: str) -> Callable[..., Any]:
        for name in candidates:
            method = getattr(service, name, None)
            if callable(method):
                return method
        if callable(service):
            return service
        raise MethodNotFoundError(f"{label} exposes none of: {', '.join(candidates)}")

    async def _detect_intent(self, message: str, sender: str | None) -> tuple[Any, bool]:
        key = self._intent_cache_key(message, sender)
        if key in self.intent_cache:
            return self.intent_cache[key], True
        engine = self._resolve_provider("intent_engine")
        method = self._find_callable(engine, self._INTENT_METHODS, "Intent engine")
        kwargs: dict[str, Any] = {}
        parameters = inspect.signature(method).parameters
        if "sender" in parameters:
            kwargs["sender"] = sender
        elif "user_id" in parameters:
            kwargs["user_id"] = sender
        decision = await asyncio.wait_for(_call(method, message, **kwargs), timeout=10.0)
        _decision_view(decision)  # Validate before caching; do not mutate the decision.
        self.intent_cache[key] = decision
        return decision, False

    async def _enhance(
        self,
        decision: RoutingDecisionView,
        business_response: ServiceResponse,
        message: str,
        request_id: str,
    ) -> ServiceResponse:
        groq = self._resolve_provider("groq_service")
        method = self._find_callable(groq, self._GROQ_METHODS, "Groq service")
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
        # Preserve authoritative business data; AI may enhance presentation only.
        return business_response.model_copy(update={
            "whatsapp_message": validated.whatsapp_message or business_response.whatsapp_message,
            "metadata": business_response.metadata | {"ai_enhanced": True},
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
        try:
            request = RequestInput(message=message, sender=sender)
            decision_object, cache_hit = await self._detect_intent(request.message, request.sender)
            decision = _decision_view(decision_object)
            target = self.router.target_for(decision)
            bound = bound.bind(
                intent=decision.intent,
                confidence=decision.confidence,
                service=target.provider_name,
                method=target.method,
            )
            bound.info("Routing request cache_hit={}", cache_hit)
            business_response = await asyncio.wait_for(
                self.router.execute(
                    decision_object, decision, request.message, request_id
                ),
                timeout=self.request_timeout,
            )
            if decision.requires_ai and target.provider_name != "groq_service":
                business_response = await asyncio.wait_for(
                    self._enhance(
                        decision, business_response, request.message, request_id
                    ),
                    timeout=self.request_timeout,
                )
            elapsed = (time.perf_counter() - started) * 1000
            bound.info(
                "Request completed success={} response_time_ms={:.2f}",
                business_response.success,
                elapsed,
            )
            if business_response.whatsapp_message:
                return business_response.whatsapp_message
            if business_response.success:
                return "Your request was processed successfully."
            return business_response.error or "I could not process that request right now."
        except ValidationError:
            bound.exception("Request or response validation failed")
            return "Please send a valid, non-empty request."
        except MethodNotFoundError:
            bound.exception("Configured service method was not found")
            return "That service operation is temporarily unavailable."
        except (ServiceUnavailableError, ConfigurationError, ImportError):
            bound.exception("A required service is unavailable")
            return "The requested service is temporarily unavailable. Please try again shortly."
        except (TimeoutError, asyncio.TimeoutError):
            bound.exception("Request timed out")
            return "The request took too long to complete. Please try again."
        except DatabaseConnectionError:
            bound.exception("A business service reported a database connection failure")
            return "Business data is temporarily unavailable. Please try again shortly."
        except (ConnectionError, OSError, RuntimeError, TypeError, ValueError):
            bound.exception("Unexpected orchestration dependency failure")
            return "I could not process that request right now. Please try again shortly."

    async def health_check(self) -> dict[str, Any]:
        """Resolve and probe every dependency without running business logic."""
        if "health" in self.metadata_cache:
            return self.metadata_cache["health"]
        checks: dict[str, dict[str, Any]] = {}
        for name in (
            "intent_engine", "dn_service", "dealer_service", "warehouse_service",
            "city_service", "product_service", "groq_service",
        ):
            try:
                service = self._resolve_provider(name)
                health = getattr(service, "health_check", None)
                result = await asyncio.wait_for(_call(health), 5.0) if callable(health) else {"resolved": True}
                checks[name] = {"healthy": True, "details": result}
            except (ConfigurationError, ImportError, TimeoutError, asyncio.TimeoutError) as exc:
                logger.exception("Startup health check failed for {}", name)
                checks[name] = {"healthy": False, "error": type(exc).__name__}
        result = {"healthy": all(item["healthy"] for item in checks.values()), "services": checks}
        self.metadata_cache["health"] = result
        return result


container = ApplicationContainer()
orchestrator = AIProviderOrchestrator(container)


async def process_whatsapp_query(
    message: str,
    sender: str | None = None,
    **context: Any,
) -> str:
    """Primary backward-compatible webhook entry point."""
    return await orchestrator.process(message, sender, **context)


async def process_query(
    message: str,
    sender: str | None = None,
    **context: Any,
) -> str:
    """Compatibility alias used by older webhook implementations."""
    return await process_whatsapp_query(message, sender, **context)


async def health_check() -> dict[str, Any]:
    return await orchestrator.health_check()


__all__ = [
    "AIProviderOrchestrator",
    "ApplicationContainer",
    "ConfigurationError",
    "DatabaseConnectionError",
    "MethodNotFoundError",
    "ROUTES",
    "RouteTarget",
    "ServiceResponse",
    "ServiceRouter",
    "ServiceUnavailableError",
    "container",
    "health_check",
    "orchestrator",
    "process_query",
    "process_whatsapp_query",
]
