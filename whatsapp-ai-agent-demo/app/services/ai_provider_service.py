"""
Enterprise orchestration entry point for WhatsApp AI requests.

This module coordinates the complete request pipeline:
1. Message Normalization
2. Intent Detection
3. Entity Extraction
4. Routing Decision
5. Business Service Dispatch
6. Response Formatting

It contains NO business logic, NO SQL, NO analytics calculations.
Only orchestration and routing.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import re
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass, field
from datetime import datetime, timezone, timedelta
from functools import partial
from typing import Any, Final, Protocol, Optional, Dict, List, Tuple

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

# ============================================================
# BLOCK 1: INTENT DETECTION ENGINE IMPORTS
# ============================================================

from app.services.ai_bootstrap_service import get_ai_bootstrap_service

# Import intent detection modules
from app.services.intent_engine.routing_models import (
    IntentType,
    ServiceType,
    RoutingDecision,
    ExtractedEntity,
    NormalizedMessage,
    INTENT_TO_SERVICE,
    INTENT_TO_METHOD,
)
from app.services.intent_engine.message_normalizer import MessageNormalizer
from app.services.intent_engine.entity_extractor import EntityExtractor
from app.services.intent_engine.intent_classifier import IntentClassifier
from app.services.intent_engine.routing_engine import RoutingEngine

# ============================================================
# BLOCK 2: OPTIONAL LIBRARIES WITH SAFE FALLBACKS
# ============================================================

try:
    import numpy as np
except ImportError:
    np = None

try:
    from rapidfuzz import fuzz, process
except ImportError:
    fuzz = None
    process = None

try:
    import spacy
except ImportError:
    spacy = None

try:
    from textblob import TextBlob
except ImportError:
    TextBlob = None

try:
    from nltk.corpus import stopwords
    import nltk
except ImportError:
    stopwords = None
    nltk = None

try:
    from expiringdict import ExpiringDict
except ImportError:
    ExpiringDict = None

try:
    import dateparser
except ImportError:
    dateparser = None

try:
    from lingua import LanguageDetectorBuilder
    from lingua import Language
except ImportError:
    LanguageDetectorBuilder = None
    Language = None

try:
    import ftfy
except ImportError:
    ftfy = None

try:
    from unidecode import unidecode
except ImportError:
    unidecode = None

try:
    import regex
except ImportError:
    regex = re

try:
    import ahocorasick
except ImportError:
    ahocorasick = None

try:
    from symspellpy import SymSpell, Verbosity
except ImportError:
    SymSpell = None
    Verbosity = None

try:
    from semantic_router import Route, Router, RouteLayer
    from semantic_router.encoders import HuggingFaceEncoder
except ImportError:
    Route = None
    Router = None
    RouteLayer = None
    HuggingFaceEncoder = None

try:
    from flashrank import Ranker
except ImportError:
    Ranker = None


# ============================================================
# BLOCK 3: CUSTOM EXCEPTIONS
# ============================================================

class OrchestrationError(RuntimeError):
    """Base class for safe orchestration failures."""
    def __init__(self, message: str, request_id: str = "", **kwargs):
        self.request_id = request_id
        self.diagnostics = kwargs
        super().__init__(message)


class ConfigurationError(OrchestrationError):
    pass


class ServiceUnavailableError(OrchestrationError):
    pass


class MethodNotFoundError(OrchestrationError):
    pass


class DatabaseConnectionError(OrchestrationError):
    pass


class RoutingError(OrchestrationError):
    pass


class GroqError(OrchestrationError):
    pass


class IntentDetectionError(OrchestrationError):
    pass


class ConversationStateError(OrchestrationError):
    pass


# ============================================================
# BLOCK 4: PYDANTIC MODELS
# ============================================================

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


@dataclass(frozen=True, slots=True)
class RouteTarget:
    provider_name: str
    method: str


class ProviderResolver(Protocol):
    def __call__(self, name: str) -> Any: ...


# ============================================================
# BLOCK 5: CONVERSATION STATE MODELS
# ============================================================

class MenuOption:
    def __init__(self, number: str, label: str, intent: str, service: str = None, method: str = None):
        self.number = number
        self.label = label
        self.intent = intent
        self.service = service
        self.method = method


class ConversationState:
    """User conversation state for menu navigation"""
    
    def __init__(self, sender: str):
        self.sender: str = sender
        self.current_menu: str = "main"
        self.previous_menu: str = ""
        self.selected_intent: str = ""
        self.selected_entity: str = ""
        self.selected_dealer: str = ""
        self.selected_city: str = ""
        self.selected_warehouse: str = ""
        self.waiting_for_input: bool = False
        self.expected_input_type: str = ""
        self.last_message: str = ""
        self.last_response: str = ""
        self.last_intent: str = ""
        self.last_entity: str = ""
        self.context: dict[str, Any] = field(default_factory=dict)
        self.updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
        self.history: list[dict[str, Any]] = field(default_factory=list)
        self.back_stack: list[str] = field(default_factory=list)
        self.current_flow: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "sender": self.sender,
            "current_menu": self.current_menu,
            "previous_menu": self.previous_menu,
            "selected_intent": self.selected_intent,
            "selected_entity": self.selected_entity,
            "selected_dealer": self.selected_dealer,
            "selected_city": self.selected_city,
            "selected_warehouse": self.selected_warehouse,
            "waiting_for_input": self.waiting_for_input,
            "expected_input_type": self.expected_input_type,
            "last_message": self.last_message,
            "last_response": self.last_response,
            "last_intent": self.last_intent,
            "last_entity": self.last_entity,
            "context": self.context,
            "updated_at": self.updated_at.isoformat(),
            "history": self.history[-10:],
            "back_stack": self.back_stack[-5:],
            "current_flow": self.current_flow,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationState":
        state = cls(data["sender"])
        state.current_menu = data.get("current_menu", "main")
        state.previous_menu = data.get("previous_menu", "")
        state.selected_intent = data.get("selected_intent", "")
        state.selected_entity = data.get("selected_entity", "")
        state.selected_dealer = data.get("selected_dealer", "")
        state.selected_city = data.get("selected_city", "")
        state.selected_warehouse = data.get("selected_warehouse", "")
        state.waiting_for_input = data.get("waiting_for_input", False)
        state.expected_input_type = data.get("expected_input_type", "")
        state.last_message = data.get("last_message", "")
        state.last_response = data.get("last_response", "")
        state.last_intent = data.get("last_intent", "")
        state.last_entity = data.get("last_entity", "")
        state.context = data.get("context", {})
        if data.get("updated_at"):
            try:
                state.updated_at = datetime.fromisoformat(data["updated_at"])
            except:
                state.updated_at = datetime.now(timezone.utc)
        state.history = data.get("history", [])
        state.back_stack = data.get("back_stack", [])
        state.current_flow = data.get("current_flow", "")
        return state
    
    def push_back(self, menu: str) -> None:
        self.back_stack.append(menu)
        if len(self.back_stack) > 5:
            self.back_stack = self.back_stack[-5:]
    
    def pop_back(self) -> Optional[str]:
        if self.back_stack:
            return self.back_stack.pop()
        return None


# ============================================================
# BLOCK 6: MENU DEFINITIONS
# ============================================================

class MenuService:
    """Guided menu service for WhatsApp"""
    
    MAIN_MENU = {
        "title": "👋 Welcome to HPK Logistics AI Assistant",
        "subtitle": "Please select an option by replying with the number:",
        "options": [
            {"number": "1️⃣", "label": "DN Services", "intent": "dn_menu", "service": None, "method": None},
            {"number": "2️⃣", "label": "Dealer Analytics", "intent": "dealer_menu", "service": None, "method": None},
            {"number": "3️⃣", "label": "Warehouse Analytics", "intent": "warehouse_menu", "service": None, "method": None},
            {"number": "4️⃣", "label": "City Analytics", "intent": "city_menu", "service": None, "method": None},
            {"number": "5️⃣", "label": "Product Analytics", "intent": "product_menu", "service": None, "method": None},
            {"number": "6️⃣", "label": "National KPI Dashboard", "intent": "national_kpi", "service": "kpi_service", "method": "get_national_kpi_dashboard"},
            {"number": "7️⃣", "label": "Pending Deliveries", "intent": "pending_dns", "service": "dn_service", "method": "get_pending_dns"},
            {"number": "8️⃣", "label": "Reports & Rankings", "intent": "reports_menu", "service": None, "method": None},
            {"number": "9️⃣", "label": "AI Assistant", "intent": "general_ai", "service": "groq_service", "method": "process_query"},
            {"number": "0️⃣", "label": "Help", "intent": "help", "service": "groq_service", "method": "process_query"},
        ],
        "footer": "\n💡 Tip: You can also type natural questions like 'Show dealer Taj Electronics'"
    }
    
    DEALER_MENU = {
        "title": "📊 Dealer Analytics",
        "subtitle": "Please choose:",
        "options": [
            {"number": "1️⃣", "label": "Dealer Dashboard", "intent": "dealer_dashboard", "service": "dealer_service", "method": "get_dealer_dashboard"},
            {"number": "2️⃣", "label": "Dealer Revenue", "intent": "dealer_revenue", "service": "dealer_service", "method": "get_dealer_dashboard"},
            {"number": "3️⃣", "label": "Dealer Units", "intent": "dealer_units", "service": "dealer_service", "method": "get_dealer_dashboard"},
            {"number": "4️⃣", "label": "Dealer Pending DN", "intent": "dealer_pending", "service": "dealer_service", "method": "get_dealer_dashboard"},
            {"number": "5️⃣", "label": "Dealer PGI", "intent": "dealer_pgi", "service": "dealer_service", "method": "get_dealer_dashboard"},
            {"number": "6️⃣", "label": "Dealer POD", "intent": "dealer_pod", "service": "dealer_service", "method": "get_dealer_dashboard"},
            {"number": "7️⃣", "label": "Dealer Performance", "intent": "dealer_performance", "service": "dealer_service", "method": "get_dealer_dashboard"},
            {"number": "8️⃣", "label": "Dealer Ranking", "intent": "top_dealers", "service": "dealer_service", "method": "get_top_dealers"},
            {"number": "9️⃣", "label": "Compare Dealers", "intent": "dealer_comparison", "service": "dealer_service", "method": "compare_dealers"},
            {"number": "0️⃣", "label": "🔙 Back", "intent": "main_menu", "service": None, "method": None},
        ],
        "footer": "\n📝 Please enter the dealer name after selecting an option."
    }
    
    CITY_MENU = {
        "title": "🏙️ City Analytics",
        "subtitle": "Please choose:",
        "options": [
            {"number": "1️⃣", "label": "City Dashboard", "intent": "city_dashboard", "service": "city_service", "method": "get_city_dashboard"},
            {"number": "2️⃣", "label": "City Revenue", "intent": "city_revenue", "service": "city_service", "method": "get_city_dashboard"},
            {"number": "3️⃣", "label": "City Pending", "intent": "city_pending", "service": "city_service", "method": "get_city_dashboard"},
            {"number": "4️⃣", "label": "City Ranking", "intent": "top_cities", "service": "city_service", "method": "get_top_cities"},
            {"number": "5️⃣", "label": "Compare Cities", "intent": "city_comparison", "service": "city_service", "method": "compare_cities"},
            {"number": "0️⃣", "label": "🔙 Back", "intent": "main_menu", "service": None, "method": None},
        ],
        "footer": "\n📝 Please enter the city name after selecting an option."
    }
    
    DN_MENU = {
        "title": "📦 DN Services",
        "subtitle": "Please choose:",
        "options": [
            {"number": "1️⃣", "label": "DN Dashboard", "intent": "dn_lookup", "service": "dn_service", "method": "get_dn_dashboard"},
            {"number": "2️⃣", "label": "DN Status", "intent": "dn_status", "service": "dn_service", "method": "get_dn_status"},
            {"number": "3️⃣", "label": "DN History", "intent": "dn_history", "service": "dn_service", "method": "get_dn_history"},
            {"number": "4️⃣", "label": "DN Timeline", "intent": "delivery_timeline", "service": "dn_service", "method": "get_delivery_timeline"},
            {"number": "5️⃣", "label": "Search DN", "intent": "search_dns", "service": "dn_service", "method": "search_dns"},
            {"number": "6️⃣", "label": "Pending DNs", "intent": "pending_dns", "service": "dn_service", "method": "get_pending_dns"},
            {"number": "7️⃣", "label": "Recent DNs", "intent": "recent_dns", "service": "dn_service", "method": "get_recent_dns"},
            {"number": "0️⃣", "label": "🔙 Back", "intent": "main_menu", "service": None, "method": None},
        ],
        "footer": "\n📝 Please enter the DN number after selecting an option."
    }
    
    REPORTS_MENU = {
        "title": "📊 Reports & Rankings",
        "subtitle": "Please choose:",
        "options": [
            {"number": "1️⃣", "label": "Top Dealers", "intent": "top_dealers", "service": "dealer_service", "method": "get_top_dealers"},
            {"number": "2️⃣", "label": "Top Cities", "intent": "top_cities", "service": "city_service", "method": "get_top_cities"},
            {"number": "3️⃣", "label": "DN Summary", "intent": "dn_summary", "service": "dn_service", "method": "get_dn_summary"},
            {"number": "4️⃣", "label": "National KPI", "intent": "national_kpi", "service": "kpi_service", "method": "get_national_kpi_dashboard"},
            {"number": "5️⃣", "label": "Transit Analysis", "intent": "transit_analysis", "service": "dn_service", "method": "get_transit_analysis"},
            {"number": "0️⃣", "label": "🔙 Back", "intent": "main_menu", "service": None, "method": None},
        ],
        "footer": ""
    }
    
    @classmethod
    def get_menu(cls, menu_name: str) -> dict:
        menus = {
            "main": cls.MAIN_MENU,
            "dealer": cls.DEALER_MENU,
            "city": cls.CITY_MENU,
            "dn": cls.DN_MENU,
            "reports": cls.REPORTS_MENU,
        }
        return menus.get(menu_name, cls.MAIN_MENU)
    
    @classmethod
    def format_menu(cls, menu: dict) -> str:
        lines = [menu["title"], "", menu["subtitle"], ""]
        for option in menu["options"]:
            lines.append(f"{option['number']} {option['label']}")
        if menu.get("footer"):
            lines.extend(["", menu["footer"]])
        return "\n".join(lines)
    
    @classmethod
    def get_option_by_number(cls, menu: dict, number: str) -> Optional[dict]:
        for option in menu["options"]:
            if option["number"] == number or option["number"].replace("️⃣", "") == number:
                return option
        return None


# ============================================================
# BLOCK 7: CONVERSATION MANAGER
# ============================================================

class ConversationManager:
    """Manages conversation state with expiring cache"""
    
    def __init__(self, ttl_seconds: int = 1800):
        self._ttl = ttl_seconds
        if ExpiringDict:
            self._cache = ExpiringDict(max_len=10000, max_age_seconds=ttl_seconds)
        else:
            self._cache = TTLCache(maxsize=10000, ttl=ttl_seconds)
        self._lock = asyncio.Lock()
    
    async def get_state(self, sender: str) -> Optional[ConversationState]:
        async with self._lock:
            data = self._cache.get(sender)
            if data:
                return ConversationState.from_dict(data)
            return None
    
    async def set_state(self, state: ConversationState) -> None:
        async with self._lock:
            self._cache[state.sender] = state.to_dict()
    
    async def clear_state(self, sender: str) -> None:
        async with self._lock:
            if sender in self._cache:
                del self._cache[sender]
    
    async def update_state(self, sender: str, **kwargs) -> Optional[ConversationState]:
        state = await self.get_state(sender)
        if not state:
            state = ConversationState(sender)
        
        for key, value in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, value)
        
        state.updated_at = datetime.now(timezone.utc)
        await self.set_state(state)
        return state
    
    async def add_history(self, sender: str, message: str, response: str) -> None:
        state = await self.get_state(sender)
        if not state:
            state = ConversationState(sender)
        
        state.history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "response": response[:200],
        })
        if len(state.history) > 20:
            state.history = state.history[-20:]
        
        await self.set_state(state)


# ============================================================
# BLOCK 8: ROUTING TABLE (Backward Compatible)
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
    "dealer_pgi": RouteTarget("dealer_service", "get_dealer_dashboard"),
    "dealer_pod": RouteTarget("dealer_service", "get_dealer_dashboard"),
    "dealer_units": RouteTarget("dealer_service", "get_dealer_dashboard"),
    "dealer_performance": RouteTarget("dealer_service", "get_dealer_dashboard"),
    "dealer_comparison": RouteTarget("dealer_service", "compare_dealers"),
    "top_dealers": RouteTarget("dealer_service", "get_top_dealers"),
    "dealer_ranking": RouteTarget("dealer_service", "get_top_dealers"),
    
    # City Service Routes
    "city_dashboard": RouteTarget("city_service", "get_city_dashboard"),
    "city_revenue": RouteTarget("city_service", "get_city_dashboard"),
    "city_pending": RouteTarget("city_service", "get_city_dashboard"),
    "top_cities": RouteTarget("city_service", "get_top_cities"),
    "city_comparison": RouteTarget("city_service", "compare_cities"),
    
    # Warehouse Service Routes
    "warehouse_dashboard": RouteTarget("warehouse_service", "get_warehouse_dashboard"),
    
    # Product Service Routes
    "product_dashboard": RouteTarget("product_service", "get_product_dashboard"),
    
    # KPI Service Routes
    "national_kpi": RouteTarget("kpi_service", "get_national_kpi_dashboard"),
    "national_kpi_dashboard": RouteTarget("kpi_service", "get_national_kpi_dashboard"),
    
    # Menu Routes (handled internally)
    "main_menu": RouteTarget("menu_service", "show_main_menu"),
    "dealer_menu": RouteTarget("menu_service", "show_dealer_menu"),
    "city_menu": RouteTarget("menu_service", "show_city_menu"),
    "dn_menu": RouteTarget("menu_service", "show_dn_menu"),
    "reports_menu": RouteTarget("menu_service", "show_reports_menu"),
    
    # General AI Fallback
    "general_ai": RouteTarget("groq_service", "process_query"),
    "greeting": RouteTarget("groq_service", "process_query"),
    "help": RouteTarget("groq_service", "process_query"),
    "unknown": RouteTarget("groq_service", "process_query"),
}


# ============================================================
# BLOCK 9: SERVICE SYMBOL RESOLUTION
# ============================================================

_SYMBOLS: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "dn_service": ("app.services.dn_analysis", ("DNAnalysisService", "DNService")),
    "dealer_service": (
        "app.services.dealer_analytics_service",
        ("DealerAnalyticsService", "DealerService"),
    ),
    "warehouse_service": ("app.services.warehouse_service", ("WarehouseService",)),
    "city_service": ("app.services.city_service", ("CityAnalyticsService", "CityService")),
    "product_service": ("app.services.product_service", ("ProductService",)),
    "kpi_service": (
        "app.services.kpi_service",
        ("KPIService", "KpiService", "NationalKPIService"),
    ),
    "groq_service": ("app.services.groq_service", ("GroqService",)),
    "menu_service": ("app.services.ai_provider_service", ("MenuService",)),
    "intent_engine": (
        "app.services.intent_engine.intent_engine",
        ("IntentEngine",),
    ),
}


# ============================================================
# BLOCK 10: COMPONENT LOADER
# ============================================================

def _load_component(key: str) -> Any:
    """Load one configured singleton lazily with comprehensive error handling."""
    logger.info(f"Attempting to load component: {key}")
    
    try:
        module_name, candidates = _SYMBOLS[key]
    except KeyError as exc:
        raise ConfigurationError(f"Unknown component: {key}") from exc
    
    # Special handling for menu_service
    if key == "menu_service":
        return MenuService()
    
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        logger.error(f"Cannot import module {module_name}: {exc}")
        if key == "groq_service":
            logger.warning("Groq service unavailable - continuing without AI enhancement")
            return None
        raise ConfigurationError(f"Cannot import {module_name}") from exc
    
    # Function-oriented service modules are already fully configured
    routed_methods = {target.method for target in ROUTES.values() if target.provider_name == key}
    
    if routed_methods and any(callable(getattr(module, method, None)) for method in routed_methods):
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
    
    return module


# ============================================================
# BLOCK 11: DEPENDENCY CONTAINER
# ============================================================

class ApplicationContainer(containers.DeclarativeContainer):
    config = providers.Configuration()
    intent_engine = providers.ThreadSafeSingleton(_load_component, "intent_engine")
    dn_service = providers.ThreadSafeSingleton(_load_component, "dn_service")
    dealer_service = providers.ThreadSafeSingleton(_load_component, "dealer_service")
    warehouse_service = providers.ThreadSafeSingleton(_load_component, "warehouse_service")
    city_service = providers.ThreadSafeSingleton(_load_component, "city_service")
    product_service = providers.ThreadSafeSingleton(_load_component, "product_service")
    kpi_service = providers.ThreadSafeSingleton(_load_component, "kpi_service")
    groq_service = providers.ThreadSafeSingleton(_load_component, "groq_service")
    menu_service = providers.ThreadSafeSingleton(_load_component, "menu_service")


# ============================================================
# BLOCK 12: UTILITY FUNCTIONS
# ============================================================

def _object_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    
    attributes: dict[str, Any] = {}
    for name in ("intent", "service_key", "service", "method", "entity", "confidence", "requires_ai", "needs_groq", "reason", "parameters", "params", "arguments"):
        if hasattr(value, name):
            attributes[name] = getattr(value, name)
    return attributes


async def _call(callable_object: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(callable_object):
        return await callable_object(*args, **kwargs)
    result = await asyncio.to_thread(partial(callable_object, *args, **kwargs))
    if inspect.isawaitable(result):
        return await result
    return result


# ============================================================
# BLOCK 13: SERVICE ROUTER
# ============================================================

class ServiceRouter:
    def __init__(self, resolver: ProviderResolver, routes: Mapping[str, RouteTarget] = ROUTES, *, timeout_seconds: float = 20.0, retry_attempts: int = 3):
        self._resolver = resolver
        self._routes = dict(routes)
        self._timeout = timeout_seconds
        self._attempts = retry_attempts
        self._method_cache: TTLCache[tuple[str, str], Callable[..., Any]] = TTLCache(256, 300)

    def target_for(self, intent: str, service_key: str = None) -> RouteTarget:
        intent_key = intent.strip().casefold()
        configured = self._routes.get(intent_key)
        if configured is None and service_key:
            configured = self._routes.get(service_key.strip().casefold())
        
        if configured is None:
            return RouteTarget("groq_service", "process_query")
        
        return configured

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
            raise MethodNotFoundError(f"Method '{target.method}' is unavailable on '{target.provider_name}'")
        
        self._method_cache[key] = method
        return method

    async def execute(self, decision: RoutingDecision, request_id: str) -> ServiceResponse:
        target = self.target_for(decision.intent.value if hasattr(decision.intent, 'value') else decision.intent)
        method = self._resolve_method(target)
        
        # Build arguments from entity
        args = []
        kwargs = {}
        
        if decision.entity:
            # Special handling for DN service
            if target.provider_name == "dn_service":
                if "dn" in decision.entity or "dn_number" in decision.entity:
                    dn_value = decision.entity.get("dn") or decision.entity.get("dn_number")
                    if dn_value:
                        args = [dn_value]
            # Special handling for Dealer service
            elif target.provider_name == "dealer_service":
                if "dealer_name" in decision.entity:
                    args = [decision.entity["dealer_name"]]
                elif "dealer_code" in decision.entity:
                    args = [decision.entity["dealer_code"]]
            # Special handling for City service
            elif target.provider_name == "city_service":
                if "city" in decision.entity or "city_name" in decision.entity:
                    city_value = decision.entity.get("city") or decision.entity.get("city_name")
                    if city_value:
                        args = [city_value]
        
        transient = (TimeoutError, ConnectionError, DatabaseConnectionError)
        try:
            async for attempt in AsyncRetrying(stop=stop_after_attempt(self._attempts), wait=wait_exponential_jitter(initial=0.25, max=2.0), retry=retry_if_exception_type(transient), reraise=True):
                with attempt:
                    raw_response = await asyncio.wait_for(_call(method, *args, **kwargs), timeout=self._timeout)
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
# BLOCK 14: AI PROVIDER ORCHESTRATOR
# ============================================================

class AIProviderOrchestrator:
    """Single orchestration use case invoked by the webhook layer."""

    _INTENT_METHODS: Final[tuple[str, ...]] = ("get_routing_decision", "detect_intent", "route", "analyze", "classify")
    _GROQ_METHODS: Final[tuple[str, ...]] = ("enhance_response", "generate_response", "process_structured_data", "process_query")
    _MENU_METHODS: Final[tuple[str, ...]] = ("show_main_menu", "show_dealer_menu", "show_city_menu", "show_dn_menu", "show_reports_menu", "handle_menu_selection")

    def __init__(self, container: ApplicationContainer, *, request_timeout_seconds: float = 30.0, cache_ttl: int = 300, conversation_ttl: int = 1800):
        self.container = container
        self.request_timeout = request_timeout_seconds
        self.cache_ttl = cache_ttl
        
        self.intent_cache: TTLCache[str, RoutingDecision] = TTLCache(2_048, cache_ttl)
        self.metadata_cache: TTLCache[str, Any] = TTLCache(128, cache_ttl)
        self.router = ServiceRouter(self._resolve_provider)
        
        self.conversation_manager = ConversationManager(ttl_seconds=conversation_ttl)
        self.registry = self
        
        # Initialize Intent Detection Engine
        self._intent_engine = None
        self._bootstrap = get_ai_bootstrap_service()
        self._nlp_detector = None
        
        self._init_intent_engine()
        
        self._groq_available = True
        try:
            self._resolve_provider("groq_service")
        except Exception:
            self._groq_available = False
            logger.warning("Groq service is unavailable - AI enhancement disabled")
        
        self._startup_validation()
        
        logger.info("✅ AIProviderOrchestrator initialized with Intent Detection Engine")

    def _init_intent_engine(self):
        """Initialize the Intent Detection Engine"""
        try:
            from app.services.intent_engine.intent_engine import IntentEngine
            self._intent_engine = IntentEngine()
            logger.info("✅ IntentEngine initialized successfully")
        except ImportError as e:
            logger.warning(f"⚠️ IntentEngine not available: {e}. Using fallback detection.")
        except Exception as e:
            logger.warning(f"⚠️ IntentEngine initialization failed: {e}. Using fallback detection.")

    def _startup_validation(self) -> None:
        logger.info("🔍 Running startup validation...")
        statuses = {}
        services = ["intent_engine", "dn_service", "dealer_service", "warehouse_service", "city_service", "product_service", "kpi_service", "groq_service", "menu_service"]
        
        for name in services:
            try:
                service = self._resolve_provider(name)
                if service is not None:
                    statuses[name] = "✅ PASS"
                else:
                    statuses[name] = "⚠️ PASS (None returned)"
            except Exception as e:
                statuses[name] = f"❌ FAIL: {type(e).__name__}: {str(e)[:50]}"
                logger.error(f"Startup validation failed for {name}: {e}")
        
        logger.info("📊 Startup Validation Results:")
        for name, status in statuses.items():
            logger.info(f"  {name}: {status}")

    def _resolve_provider(self, name: str) -> Any:
        provider = getattr(self.container, name, None)
        if provider is None or not callable(provider):
            if name == "menu_service":
                return MenuService()
            raise ConfigurationError(f"Dependency provider '{name}' is not registered")
        return provider()

    @staticmethod
    def _intent_cache_key(message: str, sender: str | None) -> str:
        canonical = orjson.dumps({"message": message.casefold(), "sender": sender or ""})
        return canonical.hex()

    def _find_callable(self, service: Any, candidates: tuple[str, ...], label: str) -> Callable[..., Any] | None:
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
    def _format_error_diagnostics(exc: Exception, request_id: str, stage: str = "unknown") -> str:
        root = AIProviderOrchestrator._root_cause(exc)
        trace = traceback.format_exc()
        return (
            f"🔴 ERROR - Request: {request_id} | Stage: {stage}\n"
            f"  Exception: {type(exc).__name__}\n"
            f"  Message: {str(exc)}\n"
            f"  Root Cause: {type(root).__name__}: {str(root)}\n"
            f"  File: {exc.__traceback__.tb_frame.f_code.co_filename if exc.__traceback__ else 'unknown'}\n"
            f"  Line: {exc.__traceback__.tb_lineno if exc.__traceback__ else 'unknown'}\n"
            f"  Trace: {trace[:500]}..."
        )

    @staticmethod
    def _raw_response_fallback(data: Any) -> str:
        try:
            rendered = orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS, default=str).decode("utf-8")
        except (TypeError, ValueError, orjson.JSONEncodeError):
            rendered = str(data)
        return rendered[:4_000]

    async def _detect_intent_with_engine(self, message: str, sender: str | None) -> RoutingDecision:
        """Use Intent Engine to detect intent"""
        if self._intent_engine:
            try:
                start_time = time.perf_counter()
                decision = await asyncio.to_thread(self._intent_engine.process, message)
                decision.processing_time_ms = (time.perf_counter() - start_time) * 1000
                return decision
            except Exception as e:
                logger.warning(f"IntentEngine failed: {e}. Using fallback.")
        
        # Fallback: Return unknown intent
        from app.services.intent_engine.routing_models import IntentType, ServiceType, RoutingDecision
        return RoutingDecision(
            intent=IntentType.UNKNOWN,
            confidence=0.3,
            service=ServiceType.GROQ_SERVICE,
            method="process_query",
            entity={"message": message},
            requires_ai=True,
            reason="Fallback intent detection",
            original_message=message,
            normalized_message=message,
        )

    async def _detect_intent(self, message: str, sender: str | None) -> tuple[RoutingDecision, bool]:
        """Detect intent using Intent Engine with caching"""
        key = self._intent_cache_key(message, sender)
        if key in self.intent_cache:
            logger.debug(f"Intent cache hit for message: {message[:50]}...")
            return self.intent_cache[key], True
        
        # Check conversation state
        decision = None
        if sender:
            state = await self.conversation_manager.get_state(sender)
            if state and state.waiting_for_input:
                if state.expected_input_type in ["dealer_name", "city_name", "warehouse_name", "dn_number"]:
                    # Map expected input to intent
                    intent_map = {
                        "dealer_name": IntentType.DEALER_DASHBOARD,
                        "city_name": IntentType.CITY_DASHBOARD,
                        "warehouse_name": IntentType.WAREHOUSE_DASHBOARD,
                        "dn_number": IntentType.DN_DASHBOARD,
                    }
                    intent = intent_map.get(state.expected_input_type, IntentType.UNKNOWN)
                    decision = RoutingDecision(
                        intent=intent,
                        confidence=1.0,
                        service=INTENT_TO_SERVICE.get(intent, ServiceType.GROQ_SERVICE),
                        method=INTENT_TO_METHOD.get(intent, "process_query"),
                        entity={state.expected_input_type: message.strip()},
                        requires_ai=False,
                        reason=f"Menu input: {state.expected_input_type}",
                        original_message=message,
                        normalized_message=message,
                    )
                    state.waiting_for_input = False
                    state.expected_input_type = ""
                    await self.conversation_manager.set_state(state)
        
        # Use Intent Engine
        if decision is None:
            decision = await self._detect_intent_with_engine(message, sender)
        
        self.intent_cache[key] = decision
        return decision, False

    def _format_menu_response(self, menu_name: str) -> str:
        menu = MenuService.get_menu(menu_name)
        return MenuService.format_menu(menu)

    async def _handle_menu_selection(self, selection: str, sender: str) -> Optional[str]:
        state = await self.conversation_manager.get_state(sender)
        if not state:
            state = ConversationState(sender)
        
        current_menu = MenuService.get_menu(state.current_menu)
        option = MenuService.get_option_by_number(current_menu, selection)
        
        if not option:
            return "❌ Invalid option. Please select a valid number from the menu."
        
        intent = option.get("intent")
        
        if intent in ["main_menu", "dealer_menu", "city_menu", "dn_menu", "reports_menu"]:
            menu = MenuService.get_menu(intent.replace("_menu", ""))
            state.current_menu = intent.replace("_menu", "")
            state.previous_menu = state.current_menu
            state.waiting_for_input = False
            await self.conversation_manager.set_state(state)
            return MenuService.format_menu(menu)
        
        elif intent in ["dealer_dashboard", "dealer_revenue", "dealer_pending", "dealer_pgi", "dealer_pod", "dealer_units", "dealer_performance"]:
            state.selected_intent = intent
            state.waiting_for_input = True
            state.expected_input_type = "dealer_name"
            await self.conversation_manager.set_state(state)
            return "📝 Please enter the Dealer Name.\n\nExample:\nCommercial Electronics Abbottabad\nNew Central Electronics\nSuper Trading"
        
        elif intent in ["city_dashboard", "city_revenue", "city_pending"]:
            state.selected_intent = intent
            state.waiting_for_input = True
            state.expected_input_type = "city_name"
            await self.conversation_manager.set_state(state)
            return "📝 Please enter the City Name.\n\nExample:\nAbbottabad\nLahore\nKarachi"
        
        elif intent in ["dn_lookup", "dn_status", "dn_history", "delivery_timeline"]:
            state.selected_intent = intent
            state.waiting_for_input = True
            state.expected_input_type = "dn_number"
            await self.conversation_manager.set_state(state)
            return "📝 Please enter the DN Number.\n\nExample:\n6243699315\n6243700741"
        
        elif intent == "dealer_comparison":
            state.selected_intent = intent
            state.waiting_for_input = True
            state.expected_input_type = "dealer_names"
            await self.conversation_manager.set_state(state)
            return "📝 Please enter two dealer names to compare.\n\nFormat:\nDealer A vs Dealer B\n\nExample:\nTaj Electronics vs Umar Electronics"
        
        else:
            state.selected_intent = intent
            state.waiting_for_input = False
            await self.conversation_manager.set_state(state)
            return None

    async def _ask_for_entity(self, entity_type: str) -> str:
        prompts = {
            "dealer_name": "📝 Please enter the Dealer Name.\n\nExample:\nCommercial Electronics Abbottabad\nNew Central Electronics\nSuper Trading",
            "city_name": "📝 Please enter the City Name.\n\nExample:\nAbbottabad\nLahore\nKarachi",
            "warehouse_name": "📝 Please enter the Warehouse Name.\n\nExample:\nRawalpindi\nLahore\nKarachi",
            "dn_number": "📝 Please enter the DN Number.\n\nExample:\n6243699315\n6243700741",
            "dealer_names": "📝 Please enter two dealer names to compare.\n\nFormat:\nDealer A vs Dealer B\n\nExample:\nTaj Electronics vs Umar Electronics",
        }
        return prompts.get(entity_type, "📝 Please enter the requested information.")

    async def _enhance(self, decision: RoutingDecision, business_response: ServiceResponse, message: str, request_id: str) -> ServiceResponse:
        if not self._groq_available or not decision.requires_ai:
            logger.debug("Groq service unavailable or AI not required - skipping AI enhancement")
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
                "intent": decision.intent.value if hasattr(decision.intent, 'value') else str(decision.intent),
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
            logger.error(self._format_error_diagnostics(exc, request_id, "groq_enhancement"))
            logger.warning(f"Groq enhancement failed: {str(root)[:100]}")
            return business_response.model_copy(update={
                "metadata": business_response.metadata | {"ai_enhanced": False, "groq_error_type": type(exc).__name__, "groq_error": str(exc)}
            })

    async def process(self, message: str, sender: str | None = None, **context: Any) -> str:
        request_id = str(context.get("request_id") or uuid.uuid4())
        started = time.perf_counter()
        bound = logger.bind(request_id=request_id, sender=sender)
        stage = "request_validation"
        decision: Optional[RoutingDecision] = None
        target: Optional[RouteTarget] = None
        
        try:
            bound.info("Request received original_message={!r}", message)
            request = ServiceRequest(request_id=request_id, message=message, sender=sender, metadata=dict(context))
            bound.info("Request normalized normalized_message={!r}", request.message)
            
            stage = "intent_detection"
            decision, cache_hit = await self._detect_intent(request.message, request.sender)
            
            # Check if we need to ask for entity
            if decision.intent.value in ["ask_for_dealer", "ask_for_city", "ask_for_dn"]:
                response = await self._ask_for_entity(decision.entity.get("type", "unknown"))
                return response
            
            if decision.intent == IntentType.MENU:
                response = self._format_menu_response("main")
                return response
            
            stage = "routing"
            target = self.router.target_for(
                decision.intent.value if hasattr(decision.intent, 'value') else str(decision.intent),
                decision.service.value if hasattr(decision.service, 'value') else str(decision.service)
            )
            
            bound = bound.bind(
                intent=decision.intent.value if hasattr(decision.intent, 'value') else str(decision.intent),
                confidence=decision.confidence,
                service=target.provider_name,
                method=target.method,
            )
            bound.info("Routing decision entity={!r} reason={!r} cache_hit={}", decision.entity, decision.reason, cache_hit)
            
            # Menu service handling
            if target.provider_name == "menu_service":
                if target.method == "show_main_menu":
                    response = self._format_menu_response("main")
                    return response
                elif target.method == "show_dealer_menu":
                    response = self._format_menu_response("dealer")
                    return response
                elif target.method == "show_city_menu":
                    response = self._format_menu_response("city")
                    return response
                elif target.method == "show_dn_menu":
                    response = self._format_menu_response("dn")
                    return response
                elif target.method == "show_reports_menu":
                    response = self._format_menu_response("reports")
                    return response
            
            # Handle menu selection
            if decision.intent == IntentType.UNKNOWN and sender:
                menu_response = await self._handle_menu_selection(message, sender)
                if menu_response:
                    return menu_response
            
            stage = "business_service_execution"
            service_started = time.perf_counter()
            business_response = await asyncio.wait_for(
                self.router.execute(decision, request_id),
                timeout=self.request_timeout
            )
            service_ms = (time.perf_counter() - service_started) * 1000
            
            groq_ms = 0.0
            if decision.requires_ai and target.provider_name != "groq_service":
                stage = "groq_enhancement"
                groq_started = time.perf_counter()
                business_response = await self._enhance(decision, business_response, request.message, request_id)
                groq_ms = (time.perf_counter() - groq_started) * 1000
            
            stage = "response_formatting"
            elapsed = (time.perf_counter() - started) * 1000
            business_response = business_response.model_copy(update={"processing_time": elapsed})
            
            bound.info("Request completed success={} service_time_ms={:.2f} groq_time_ms={:.2f} total_time_ms={:.2f}", 
                      business_response.success, service_ms, groq_ms, elapsed)
            
            # ============================================================
            # BUSINESS ERROR HANDLING (NO AI FALLBACK)
            # ============================================================
            
            if business_response.whatsapp_message:
                if sender:
                    await self.conversation_manager.add_history(sender, message, business_response.whatsapp_message)
                return business_response.whatsapp_message
            if business_response.success:
                response = self._raw_response_fallback(business_response.data)
                if sender:
                    await self.conversation_manager.add_history(sender, message, response)
                return response
            
            error = business_response.error.strip()
            
            # DN Not Found
            if target.provider_name == "dn_service":
                dn_match = re.search(r"(?<!\d)(\d{6,20})(?!\d)", request.message)
                dn_no = dn_match.group(1) if dn_match else "unknown"
                if any(word in error.lower() for word in ["not found", "no rows", "does not exist", "no record"]):
                    return f"DN {dn_no} was not found in PostgreSQL."
            
            # Dealer Not Found
            if target.provider_name == "dealer_service":
                dealer_name = decision.entity.get("dealer_name") if decision.entity else "unknown"
                if any(word in error.lower() for word in ["not found", "no rows", "does not exist", "no record", "dealer"]):
                    return f"Dealer '{dealer_name}' was not found in PostgreSQL."
            
            # City Not Found
            if target.provider_name == "city_service":
                city_name = decision.entity.get("city") if decision.entity else "unknown"
                if any(word in error.lower() for word in ["not found", "no rows", "does not exist", "no record", "city"]):
                    return f"City '{city_name}' was not found in PostgreSQL."
            
            # Database Errors
            if any(word in error.lower() for word in ["database", "connection", "sql", "timeout", "postgres"]):
                return "Database is currently unavailable. Please try again later."
            
            logger.error(f"Service execution error: {error} | Request ID: {request_id}")
            return f"{error} Reference ID: {request_id}"
            
        except ValidationError as exc:
            bound.error(f"Validation error: {exc}")
            return "Please send a valid, non-empty request."
            
        except RoutingError as exc:
            bound.error(f"Routing error: {exc}")
            return f"{exc}. Reference ID: {request_id}"
            
        except MethodNotFoundError as exc:
            bound.error(f"Method not found: {exc}")
            service_name = target.provider_name if target else "Selected service"
            return f"{service_name} does not support the requested operation. Reference ID: {request_id}"
            
        except (ServiceUnavailableError, ConfigurationError, ImportError) as exc:
            bound.error(f"Service unavailable: {exc}")
            service_name = target.provider_name if target else "Requested service"
            return f"{service_name} is unavailable. Reference ID: {request_id}"
            
        except (TimeoutError, asyncio.TimeoutError) as exc:
            bound.error(f"Timeout: {exc}")
            return f"The request timed out. Reference ID: {request_id}"
            
        except (DatabaseConnectionError, SQLAlchemyError, ConnectionError) as exc:
            root = self._root_cause(exc)
            bound.error(f"Database error: {root}")
            return f"Database is currently unavailable. Reference ID: {request_id}"
            
        except Exception as exc:
            root = self._root_cause(exc)
            diagnostics = self._format_error_diagnostics(exc, request_id, stage)
            logger.critical(diagnostics)
            return f"Unexpected internal error. Reference ID: {request_id}"

    async def process_whatsapp_query(self, message: str, sender: str | None = None, **context: Any) -> str:
        if sender is None:
            sender = context.pop("sender_id", None) or context.pop("phone_number", None)
        return await self.process(message, sender, **context)

    async def process_query(self, message: str, sender: str | None = None, **context: Any) -> str:
        return await self.process_whatsapp_query(message, sender, **context)

    async def enhance_response(self, response: Any, message: str = "", **context: Any) -> str:
        request_id = str(context.get("request_id") or uuid.uuid4())
        business_response = ServiceRouter.validate_response(response, request_id)
        
        from app.services.intent_engine.routing_models import IntentType, ServiceType, RoutingDecision
        decision = RoutingDecision(
            intent=IntentType.GENERAL_AI,
            confidence=1.0,
            service=ServiceType.GROQ_SERVICE,
            method="process_query",
            entity=context.get("entity", {}),
            requires_ai=True,
            reason="Explicit response enhancement",
            original_message=message,
            normalized_message=message,
        )
        
        try:
            enhanced = await asyncio.wait_for(self._enhance(decision, business_response, message, request_id), timeout=self.request_timeout)
            return enhanced.whatsapp_message or business_response.whatsapp_message or self._raw_response_fallback(business_response.data)
        except Exception as exc:
            logger.error(self._format_error_diagnostics(exc, request_id, "enhance_response"))
            return business_response.whatsapp_message or self._raw_response_fallback(business_response.data)

    def get_registry_status(self, *, refresh: bool = False) -> dict[str, Any]:
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
                        missing = ["intent methods not found"]
                    else:
                        missing = []
                else:
                    missing = sorted(method for method in methods if not callable(getattr(service, method, None)))
                
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
                statuses[provider_name] = {"available": False, "methods": sorted(methods), "reason": f"{type(exc).__name__}: {exc}"}
        
        result = {
            "healthy": all(status["available"] for status in statuses.values()),
            "services": statuses,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "cache_ttl_seconds": 300,
        }
        self.metadata_cache[cache_key] = result
        return result

    def get_service_registry_status(self) -> dict[str, Any]:
        report = self.get_registry_status()
        services = report["services"]
        ready = sum(1 for status in services.values() if status.get("available"))
        total = len(services)
        return report | {"ready": ready, "in_development": total - ready, "total": total, "readiness_score": (ready / total * 100.0) if total else 0.0}

    @staticmethod
    def _provider_key(service_key: str) -> str:
        aliases = {"dn": "dn_service", "dealer": "dealer_service", "warehouse": "warehouse_service", "city": "city_service", "product": "product_service", "kpi": "kpi_service", "groq": "groq_service", "intent": "intent_engine"}
        return aliases.get(service_key, service_key)

    def get_service_status(self, service_key: str) -> dict[str, Any]:
        provider_key = self._provider_key(service_key)
        status = self.get_registry_status()["services"].get(provider_key)
        if status is None:
            return {"ready": False, "status": "NOT_REGISTERED", "service": service_key}
        return status | {"ready": bool(status.get("available")), "status": "READY" if status.get("available") else "UNAVAILABLE", "service": service_key}

    def get_service_instance(self, service_key: str) -> Any | None:
        provider_key = self._provider_key(service_key)
        try:
            return self._resolve_provider(provider_key)
        except (ConfigurationError, ImportError, TypeError):
            logger.exception("Unable to resolve registry service {}", service_key)
            return None

    def refresh_status(self) -> dict[str, Any]:
        self.metadata_cache.clear()
        self.router._method_cache.clear()
        return self.get_registry_status(refresh=True)

    async def health_check(self) -> dict[str, Any]:
        if "health" in self.metadata_cache:
            return self.metadata_cache["health"]
        
        checks: dict[str, dict[str, Any]] = {}
        for name in ("intent_engine", "dn_service", "dealer_service", "warehouse_service", "city_service", "product_service", "kpi_service", "groq_service"):
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
        
        try:
            bootstrap = get_ai_bootstrap_service()
            checks["bootstrap"] = {"healthy": True, "details": bootstrap.health()}
        except Exception as e:
            checks["bootstrap"] = {"healthy": False, "error": str(e)}
        
        checks["conversation_manager"] = {"healthy": True, "details": {"cache_size": len(self.conversation_manager._cache)}}
        checks["router"] = {"healthy": True, "details": {"routes": len(ROUTES), "cache_size": len(self.router._method_cache)}}
        
        result = {"healthy": all(item["healthy"] for item in checks.values()), "services": checks, "checked_at": datetime.now(timezone.utc).isoformat()}
        self.metadata_cache["health"] = result
        return result


# ============================================================
# BLOCK 15: SINGLETON INSTANCES
# ============================================================

container = ApplicationContainer()
orchestrator = AIProviderOrchestrator(container)
WhatsAppProviderService = AIProviderOrchestrator


# ============================================================
# BLOCK 16: MODULE-LEVEL FUNCTIONS - BACKWARD COMPATIBLE
# ============================================================

async def process_whatsapp_query(message: str, sender: str | None = None, **context: Any) -> str:
    return await orchestrator.process_whatsapp_query(message, sender, **context)


async def process_query(message: str, sender: str | None = None, **context: Any) -> str:
    return await process_whatsapp_query(message, sender, **context)


async def enhance_response(response: Any, message: str = "", **context: Any) -> str:
    return await orchestrator.enhance_response(response, message, **context)


async def health_check() -> dict[str, Any]:
    return await orchestrator.health_check()


def get_whatsapp_provider_service() -> AIProviderOrchestrator:
    return orchestrator


def get_service_registry_status() -> dict[str, Any]:
    return orchestrator.get_registry_status()


def validate_all_services() -> dict[str, Any]:
    return orchestrator.get_registry_status(refresh=True)


def refresh_service_status() -> dict[str, Any]:
    return orchestrator.refresh_status()


def get_system_health() -> dict[str, Any]:
    registry = orchestrator.get_registry_status()
    return {
        "healthy": registry["healthy"],
        "status": "healthy" if registry["healthy"] else "unhealthy",
        "reason": "" if registry["healthy"] else "One or more services failed validation",
        "services": registry["services"],
        "checked_at": registry["checked_at"],
    }


__all__ = [
    "AIProviderOrchestrator",
    "ApplicationContainer",
    "ConfigurationError",
    "DatabaseConnectionError",
    "MethodNotFoundError",
    "ROUTES",
    "RouteTarget",
    "RequestInput",
    "ServiceRequest",
    "ServiceResponse",
    "ServiceRouter",
    "ServiceUnavailableError",
    "WhatsAppProviderService",
    "ConversationManager",
    "ConversationState",
    "MenuService",
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
