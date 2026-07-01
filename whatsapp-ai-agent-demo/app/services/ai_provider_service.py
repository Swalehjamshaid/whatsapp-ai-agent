"""
Enterprise orchestration entry point for WhatsApp AI requests.

The module coordinates intent detection, business-service dispatch, optional AI
enhancement, and response validation.  It intentionally contains no SQL,
analytics, KPI calculation, dashboard construction, or domain business rules.

Supports:
- Guided Menu Navigation
- Natural Language Queries
- Conversation State Management
- Multi-stage Intent Detection
- Entity Extraction & Resolution
- Bootstrap Integration for Cached Resources
- Comprehensive Failure Diagnostics
- Multi-stage Recovery System
- Enterprise Service Health Monitoring
- Request Tracing & Performance Monitoring
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
# BLOCK 1: DIAGNOSTICS - BOOTSTRAP IMPORT
# ============================================================

from app.services.ai_bootstrap_service import get_ai_bootstrap_service

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
# BLOCK 3: CUSTOM EXCEPTIONS WITH DIAGNOSTICS
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
# BLOCK 4: HEALTH AND DIAGNOSTICS MODELS
# ============================================================

@dataclass
class ServiceHealth:
    """Health status of a service"""
    name: str
    healthy: bool
    version: str = "unknown"
    dependencies: list[str] = field(default_factory=list)
    database_connected: bool = False
    last_error: Optional[str] = None
    response_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RequestTrace:
    """Complete request execution trace"""
    request_id: str
    sender: str | None
    message: str
    stages: list[dict[str, Any]] = field(default_factory=list)
    start_time: float = field(default_factory=time.perf_counter)
    end_time: Optional[float] = None
    total_time_ms: float = 0.0
    final_status: str = "pending"
    error: Optional[str] = None
    
    def add_stage(self, name: str, success: bool, duration_ms: float = 0.0, details: dict[str, Any] = None):
        self.stages.append({
            "name": name,
            "success": success,
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details or {}
        })
    
    def complete(self, success: bool, error: str = None):
        self.end_time = time.perf_counter()
        self.total_time_ms = (self.end_time - self.start_time) * 1000
        self.final_status = "success" if success else "failed"
        self.error = error


# ============================================================
# BLOCK 5: PYDANTIC MODELS
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
    stage: str = "init"
    intent: str = ""
    entity: Any = None
    service: str = ""
    method: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)


class ProviderResolver(Protocol):
    def __call__(self, name: str) -> Any: ...


# ============================================================
# BLOCK 6: CONVERSATION STATE MODELS
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
# BLOCK 7: MENU DEFINITIONS
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
        "footer": "\n0📝 Please enter the dealer name after selecting an option."
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
# BLOCK 8: CONVERSATION MANAGER
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
    
    async def push_back(self, sender: str, menu: str) -> None:
        state = await self.get_state(sender)
        if state:
            state.push_back(menu)
            await self.set_state(state)
    
    async def pop_back(self, sender: str) -> Optional[str]:
        state = await self.get_state(sender)
        if state:
            return state.pop_back()
        return None


# ============================================================
# BLOCK 9: ROUTING TABLE
# ============================================================

ROUTES: Final[dict[str, RouteTarget]] = {
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
    
    "city_dashboard": RouteTarget("city_service", "get_city_dashboard"),
    "city_revenue": RouteTarget("city_service", "get_city_dashboard"),
    "city_pending": RouteTarget("city_service", "get_city_dashboard"),
    "top_cities": RouteTarget("city_service", "get_top_cities"),
    "city_comparison": RouteTarget("city_service", "compare_cities"),
    
    "warehouse_dashboard": RouteTarget("warehouse_service", "get_warehouse_dashboard"),
    "product_dashboard": RouteTarget("product_service", "get_product_dashboard"),
    "national_kpi": RouteTarget("kpi_service", "get_national_kpi_dashboard"),
    "national_kpi_dashboard": RouteTarget("kpi_service", "get_national_kpi_dashboard"),
    
    "main_menu": RouteTarget("menu_service", "show_main_menu"),
    "dealer_menu": RouteTarget("menu_service", "show_dealer_menu"),
    "city_menu": RouteTarget("menu_service", "show_city_menu"),
    "dn_menu": RouteTarget("menu_service", "show_dn_menu"),
    "reports_menu": RouteTarget("menu_service", "show_reports_menu"),
    
    "general_ai": RouteTarget("groq_service", "process_query"),
    "greeting": RouteTarget("groq_service", "process_query"),
    "help": RouteTarget("groq_service", "process_query"),
    "unknown": RouteTarget("groq_service", "process_query"),
}


# ============================================================
# BLOCK 10: SERVICE SYMBOL RESOLUTION (UPDATED & SECURED)
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
        "app.services.ai_provider_service_intents",
        ("IntentDetectionEngine", "IntentEngine"),
    ),
}

def resolve_orchestrated_service(service_key: str, container: Optional[Any] = None) -> Any:
    """
    Safely resolves and retrieves the service instance from the dependency injection 
    container or context symbol table to avoid runtime pipeline starvation failures.
    """
    # 1. Primary Check: Resolve via Dependency Injection container wire properties if active
    if container and hasattr(container, service_key):
        try:
            provider = getattr(container, service_key)
            return provider() if callable(provider) else provider
        except Exception as container_err:
            logger.error(f"Container wire instantiation error for service '{service_key}': {str(container_err)}")
            # Fall through to standard layout resolution if the wired container state fails

    # 2. Secondary Check: Dynamic importlib module resolution path with precise logging
    if service_key not in _SYMBOLS:
        raise MethodNotFoundError(f"Service key '{service_key}' is not mapped inside the orchestration registry.")
        
    module_path, class_names = _SYMBOLS[service_key]
    try:
        module = importlib.import_module(module_path)
        for class_name in class_names:
            if hasattr(module, class_name):
                target_class = getattr(module, class_name)
                return target_class()
        
        raise MethodNotFoundError(f"None of the target classes {class_names} were matched in module '{module_path}'.")
        
    except ImportError as ie:
        logger.critical(f"Critical System Import Failure for underlying domain module '{module_path}': {str(ie)}")
        raise ServiceUnavailableError(f"System service reference error: module '{module_path}' is missing package elements.", target=service_key)
    except Exception as e:
        logger.critical(f"Unexpected compilation failure resolving orchestration token '{service_key}': {str(e)}")
        raise OrchestrationError(f"Failed to cleanly initialize execution symbol: {str(e)}")


# ============================================================
# BLOCK 11: FALLBACK INTENT ENGINE
# ============================================================

class FallbackIntentEngine:
    """Enhanced regex-based intent detection with fallback support."""
    
    _DN_PATTERN = re.compile(r'(?<!\d)(\d{6,20})(?!\d)')
    _DEALER_PATTERNS = re.compile(r'(?:dealer|dealers?)\s+(?:for\s+)?([a-zA-Z0-9\s_\-]+)', re.IGNORECASE)
    _DEALER_NAME_PATTERN = re.compile(
        r'^(?:umar|taj|haroon|commercial|national|mian|mgc|arco|shah|haji|sons|electronics|distributors|traders|foods|group|pvt|ltd|wah|abbottabad|haripur|gilget|rawalpindi)\s*[\w\s]+|^[\w\s]+(?:electronics|distributors|traders|foods|group|pvt|ltd|sons|brothers)',
        re.IGNORECASE
    )
    _COMMON_DEALERS = frozenset({
        "umar electronics wah", "umar electronics", "taj electronics",
        "haroon electronics", "commercial electronics", "national foods",
        "mian group chakwal", "mian group", "arco electronics",
        "shah electronics", "haji sharaf ud din & sons",
        "haji sharaf", "haji sharaf ud din",
    })
    _DEALER_INDICATORS = frozenset({
        "electronics", "traders", "distributors", "foods", 
        "group", "pvt", "ltd", "sons", "brothers", "enterprises"
    })
    _CITY_PATTERNS = re.compile(
        r'(?:city|city\s+of)\s+(?:of\s+)?([a-zA-Z\s]+)|^(?:abbottabad|lahore|karachi|rawalpindi|quetta|multan|peshawar|gilgit|hyderabad|sialkot|gujranwala|islamabad)\b',
        re.IGNORECASE
    )
    _WAREHOUSE_PATTERNS = re.compile(r'(?:warehouse|wh)\s+(?:for\s+)?([a-zA-Z0-9\s_\-]+)', re.IGNORECASE)
    _MENU_KEYWORDS = re.compile(r'(?:menu|options|choices|services|back|main menu|home)', re.IGNORECASE)
    _PENDING_KEYWORDS = re.compile(r'(?:pending|not\s+delivered|overdue|late|outstanding)', re.IGNORECASE)
    _SUMMARY_KEYWORDS = re.compile(r'(?:summary|overview|total|statistics|stats|dashboard)', re.IGNORECASE)
    _RECENT_KEYWORDS = re.compile(r'(?:recent|latest|newest|today|this\s+week)', re.IGNORECASE)
    _TOP_KEYWORDS = re.compile(r'(?:top|best|highest|leading|rank)', re.IGNORECASE)
    _COMPARE_KEYWORDS = re.compile(r'(?:compare|vs|versus|verses|vs\.)', re.IGNORECASE)
    _REVENUE_KEYWORDS = re.compile(r'(?:revenue|sales|earnings|turnover|income)', re.IGNORECASE)
    _DELIVERY_KEYWORDS = re.compile(r'(?:delivery|transit|shipping|dispatch)', re.IGNORECASE)
    _GREETING_PATTERNS = re.compile(
        r'^(?:hi|hello|hey|good morning|good afternoon|good evening|hola|namaste|salam|howdy|assalamualaikum|salamualaikum)',
        re.IGNORECASE
    )
    _HELP_PATTERNS = re.compile(r'(?:help|assist|support|how\s+to|what\s+is|explain|guide)', re.IGNORECASE)
    _BACK_PATTERNS = re.compile(r'^(?:back|return|go back|previous)$', re.IGNORECASE)
    _MENU_NUMBER_PATTERN = re.compile(r'^[0-9️⃣]+$')
    
    @classmethod
    def detect(cls, message: str) -> dict[str, Any]:
        message_lower = message.lower().strip()
        message_original = message.strip()
        
        if cls._BACK_PATTERNS.match(message_lower):
            return {"intent": "main_menu", "service_key": "menu_service", "method": "show_main_menu", "entity": None, "confidence": 1.0, "requires_ai": False, "reason": "Back command detected"}
        
        if message_lower in ["menu", "main menu", "options", "help"]:
            return {"intent": "main_menu", "service_key": "menu_service", "method": "show_main_menu", "entity": None, "confidence": 1.0, "requires_ai": False, "reason": "Menu command detected"}
        
        dn_match = cls._DN_PATTERN.search(message)
        if dn_match:
            return {"intent": "dn_lookup", "service_key": "dn_service", "method": "get_dn_dashboard", "entity": dn_match.group(1), "confidence": 1.0, "requires_ai": False, "reason": "DN number detected"}
        
        if cls._COMPARE_KEYWORDS.search(message_lower):
            entities = re.findall(r'([a-zA-Z\s]+?)(?:\s+vs\s+|\s+versus\s+|\s+vs\.\s+)([a-zA-Z\s]+)', message_lower)
            if entities:
                return {"intent": "dealer_comparison", "service_key": "dealer_service", "method": "compare_dealers", "entity": {"dealer1": entities[0][0].strip(), "dealer2": entities[0][1].strip()}, "confidence": 0.85, "requires_ai": False, "reason": "Comparison detected"}
        
        for dealer in cls._COMMON_DEALERS:
            if dealer in message_lower:
                return {"intent": "dealer_dashboard", "service_key": "dealer_service", "method": "get_dealer_dashboard", "entity": message_original, "confidence": 0.95, "requires_ai": False, "reason": f"Exact dealer match: {dealer}"}
        
        if cls._DEALER_NAME_PATTERN.search(message_lower):
            word_count = len(message_lower.split())
            if word_count <= 6:
                query_keywords = ['pending', 'summary', 'recent', 'top', 'compare', 'vs', 'revenue', 'units', 'dn', 'delivery']
                if not any(keyword in message_lower for keyword in query_keywords):
                    return {"intent": "dealer_dashboard", "service_key": "dealer_service", "method": "get_dealer_dashboard", "entity": message_original, "confidence": 0.85, "requires_ai": False, "reason": "Dealer name detected (natural language)"}
        
        return {"intent": "unknown", "service_key": "groq_service", "method": "process_query", "entity": None, "confidence": 0.2, "requires_ai": True, "reason": "Fallback to LLM"}
