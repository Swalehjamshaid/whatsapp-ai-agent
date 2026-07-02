"""
File: app/services/ai_provider_service.py
Version: 16.0 - production WhatsApp logistics orchestrator

Single entry point for the WhatsApp AI agent. Deterministic requests (menu,
menu numbers, DN numbers and obvious entities) never depend on an AI provider.
Semantic Router and Groq are optional enhancements and cannot prevent startup.
"""

from __future__ import annotations

import inspect
import logging
import re
import threading
import time
from datetime import date, datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Semantic Router has changed its public class names across releases. Support
# current and older installations, but never make the whole application fail.
Route = None
SemanticRouter = None
HuggingFaceEncoder = None
SEMANTIC_ROUTER_AVAILABLE = False
SEMANTIC_ROUTER_IMPORT_ERROR: Optional[Exception] = None

try:
    from semantic_router import Route as _Route
    try:
        from semantic_router import SemanticRouter as _SemanticRouter
    except ImportError:  # compatibility with older semantic-router releases
        try:
            from semantic_router import Router as _SemanticRouter
        except ImportError:
            from semantic_router.layer import RouteLayer as _SemanticRouter
    from semantic_router.encoders import HuggingFaceEncoder as _HuggingFaceEncoder

    Route = _Route
    SemanticRouter = _SemanticRouter
    HuggingFaceEncoder = _HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
except Exception as exc:  # optional dependency
    SEMANTIC_ROUTER_IMPORT_ERROR = exc
    logger.warning("Semantic Router unavailable; rules and AI fallback remain active: %s", exc)


@dataclass
class RoutingDecision:
    intent: str
    confidence: float
    service_key: str
    service_file: str
    method: str
    entity: Dict[str, Any]
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    menu_option: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "service_key": self.service_key,
            "service_file": self.service_file,
            "method": self.method,
            "entity": self.entity,
            "requires_ai": self.requires_ai,
            "reason": self.reason,
            "original_message": self.original_message,
            "menu_option": self.menu_option,
        }


# Service imports deliberately degrade independently. One broken analytics
# module must not disable every WhatsApp command.
try:
    from app.services.dn_analysis import DNAnalysisService
except Exception as exc:
    logger.exception("Unable to import DNAnalysisService: %s", exc)

    class DNAnalysisService:  # type: ignore[no-redef]
        async def get_dn_dashboard(self, entities: Dict[str, Any]) -> str:
            return "⚠️ DN service is temporarily unavailable."

        async def get_warehouse_dashboard(self, entities: Dict[str, Any]) -> str:
            return "⚠️ Warehouse service is temporarily unavailable."

        async def get_pending_dns(self, entities: Dict[str, Any]) -> str:
            return "⚠️ Pending DN service is temporarily unavailable."

        async def get_top_performers(self, entities: Dict[str, Any]) -> str:
            return "⚠️ Performance service is temporarily unavailable."


try:
    from app.services.dealer_analytics_service import DealerAnalyticsService
except Exception as exc:
    logger.exception("Unable to import DealerAnalyticsService: %s", exc)

    class DealerAnalyticsService:  # type: ignore[no-redef]
        async def get_dealer_dashboard(self, entities: Dict[str, Any]) -> str:
            return "⚠️ Dealer service is temporarily unavailable."


try:
    from app.services.city_service import CityService
except Exception as exc:
    logger.exception("Unable to import CityService: %s", exc)

    class CityService:  # type: ignore[no-redef]
        async def get_city_dashboard(self, entities: Dict[str, Any]) -> str:
            return "⚠️ City service is temporarily unavailable."


try:
    from app.services.product_service import ProductService
except Exception as exc:
    logger.exception("Unable to import ProductService: %s", exc)

    class ProductService:  # type: ignore[no-redef]
        async def get_product_dashboard(self, entities: Dict[str, Any]) -> str:
            return "⚠️ Product service is temporarily unavailable."


try:
    from app.services.national_kpi_service import NationalKPIService
except Exception as exc:
    logger.exception("Unable to import NationalKPIService: %s", exc)

    class NationalKPIService:  # type: ignore[no-redef]
        async def get_national_kpi(self, entities: Dict[str, Any]) -> str:
            return "⚠️ National KPI service is temporarily unavailable."


try:
    from app.services.groq_service import GroqService
except Exception as exc:
    logger.exception("Unable to import GroqService: %s", exc)

    class GroqService:  # type: ignore[no-redef]
        async def process_query(self, message: str, entities: Dict[str, Any]) -> str:
            return get_main_menu()


MENU_OPTIONS: Dict[str, Dict[str, Any]] = {
    "0": {"name": "Main Menu", "service_key": "menu_service", "service_file": "ai_provider_service.py", "method": "show_main_menu", "requires_ai": False},
    "1": {"name": "DN Delivery", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_dn_dashboard", "requires_ai": False},
    "2": {"name": "Dealer Analytics", "service_key": "dealer_analytics", "service_file": "dealer_analytics_service.py", "method": "get_dealer_dashboard", "requires_ai": False},
    "3": {"name": "City Analytics", "service_key": "city_service", "service_file": "city_service.py", "method": "get_city_dashboard", "requires_ai": False},
    "4": {"name": "Warehouse Dashboard", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_warehouse_dashboard", "requires_ai": False},
    "5": {"name": "Product Analytics", "service_key": "product_service", "service_file": "product_service.py", "method": "get_product_dashboard", "requires_ai": False},
    "6": {"name": "National KPI", "service_key": "national_kpi_service", "service_file": "national_kpi_service.py", "method": "get_national_kpi", "requires_ai": False},
    "7": {"name": "Pending DN", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_pending_dns", "requires_ai": False},
    "8": {"name": "Top Performers", "service_key": "dn_analysis", "service_file": "dn_analysis.py", "method": "get_top_performers", "requires_ai": False},
    "9": {"name": "AI Query", "service_key": "groq_service", "service_file": "groq_service.py", "method": "process_query", "requires_ai": True},
}

METHOD_CANDIDATES: Dict[str, tuple[str, ...]] = {
    "get_dn_dashboard": ("get_dn_dashboard", "get_delivery_dashboard", "get_dashboard", "analyze_dn"),
    "get_dealer_dashboard": ("get_dealer_dashboard", "get_dealer_analytics", "get_dashboard"),
    "get_city_dashboard": ("get_city_dashboard", "get_city_analytics", "get_dashboard"),
    "get_warehouse_dashboard": ("get_warehouse_dashboard", "get_warehouse_analytics", "get_dashboard"),
    "get_product_dashboard": ("get_product_dashboard", "get_product_analytics", "get_dashboard"),
    "get_national_kpi": ("get_national_kpi_dashboard", "get_national_kpi", "get_kpi_dashboard", "get_dashboard"),
    "get_pending_dns": ("get_pending_dns", "get_pending_dn", "get_pending_deliveries"),
    "get_top_performers": ("get_top_performers", "get_performance_leaders", "get_leaderboard"),
    "process_query": ("process_query", "process_whatsapp_query", "generate_response", "ask"),
}

INTENT_TO_MENU = {
    "dn_lookup": "1", "dn_status": "1", "dn_history": "1", "dn_summary": "1",
    "dealer_dashboard": "2", "dealer_revenue": "2", "dealer_pending": "2", "top_dealers": "2", "dealer_comparison": "2",
    "city_dashboard": "3", "city_revenue": "3", "city_pending": "3", "top_cities": "3", "city_comparison": "3",
    "warehouse_dashboard": "4", "warehouse_revenue": "4", "warehouse_pending": "4", "top_warehouses": "4",
    "product_dashboard": "5", "top_products": "5",
    "national_kpi": "6", "national_revenue": "6", "national_units": "6",
    "pending_dns": "7", "pending_pgi": "7", "pending_pod": "7",
    "top_performers": "8", "help": "0", "menu": "0", "greeting": "0",
}

ROUTE_UTTERANCES: Dict[str, List[str]] = {
    "dn_lookup": ["show dn", "track dn", "delivery note", "dn status", "check delivery note"],
    "dn_history": ["dn history", "delivery history", "dn timeline", "tracking history"],
    "dn_summary": ["dn summary", "total dns", "delivery summary", "dn statistics"],
    "pending_dns": ["pending dns", "pending deliveries", "undelivered dns", "delivery backlog"],
    "pending_pgi": ["pending pgi", "goods issue pending", "pgi not done"],
    "pending_pod": ["pending pod", "proof of delivery pending", "pod missing"],
    "dealer_dashboard": ["dealer dashboard", "dealer performance", "show dealer", "dealer details"],
    "dealer_revenue": ["dealer revenue", "dealer sales", "dealer earnings"],
    "dealer_pending": ["dealer pending", "dealer pending orders", "dealer pending dns"],
    "top_dealers": ["top dealers", "best dealers", "dealer ranking"],
    "dealer_comparison": ["compare dealers", "dealer comparison", "dealer versus dealer"],
    "city_dashboard": ["city dashboard", "city performance", "show city", "city analytics"],
    "city_revenue": ["city revenue", "city sales", "revenue by city"],
    "city_pending": ["city pending", "pending deliveries by city"],
    "top_cities": ["top cities", "best cities", "city ranking"],
    "city_comparison": ["compare cities", "city comparison", "city versus city"],
    "warehouse_dashboard": ["warehouse dashboard", "warehouse performance", "show warehouse"],
    "warehouse_revenue": ["warehouse revenue", "warehouse sales"],
    "warehouse_pending": ["warehouse pending", "pending by warehouse"],
    "top_warehouses": ["top warehouses", "best warehouses", "warehouse ranking"],
    "product_dashboard": ["product dashboard", "product performance", "show product"],
    "top_products": ["top products", "best products", "top selling products"],
    "national_kpi": ["national kpi", "overall performance", "executive dashboard"],
    "national_revenue": ["national revenue", "total revenue", "overall sales"],
    "national_units": ["national units", "total units", "overall quantity"],
    "top_performers": ["top performers", "leaderboard", "best performers"],
    "greeting": ["hello", "hi", "salam", "good morning", "good evening"],
    "help": ["help", "how does this work", "what can you do", "instructions"],
    "menu": ["menu", "main menu", "options", "services", "show menu"],
}

CITY_NAMES = (
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
    "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
    "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
    "dera ghazi khan",
)


def get_main_menu() -> str:
    return (
        "🤖 *HPK Logistics AI Assistant*\n\n"
        "Please select an option:\n\n"
        "0️⃣ Main Menu\n"
        "1️⃣ DN Delivery Menu\n"
        "2️⃣ Dealer Analytics Menu\n"
        "3️⃣ City Analytics Menu\n"
        "4️⃣ Warehouse Dashboard Menu\n"
        "5️⃣ Product Analytics Menu\n"
        "6️⃣ National KPI Menu\n"
        "7️⃣ Pending DN Menu\n"
        "8️⃣ Top Performers Menu\n"
        "9️⃣ AI Query Menu\n\n"
        "Reply with the menu number."
    )


def get_invalid_selection_message() -> str:
    return "Invalid selection. Please choose a number from 0 to 9.\n\n" + get_main_menu()


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _scalar_service_argument(decision: RoutingDecision) -> Optional[str]:
    """Return the scalar value expected by the existing analytics services."""
    keys_by_service = {
        "dn_analysis": ("dn", "dn_number", "id"),
        "dealer_analytics": ("dealer", "dealer_name"),
        "city_service": ("city", "city_name"),
        "product_service": ("product",),
    }
    for key in keys_by_service.get(decision.service_key, ()):
        value = decision.entity.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _resolve_compatible_method(service: Any, preferred: str) -> tuple[Optional[Any], Optional[str]]:
    """Find the preferred method or a documented backward-compatible alias."""
    for name in METHOD_CANDIDATES.get(preferred, (preferred,)):
        candidate = getattr(service, name, None)
        if callable(candidate):
            return candidate, name
    return None, None


def _whatsapp_text(value: Any) -> str:
    """Convert service output to readable WhatsApp text without exposing JSON."""
    if value is None:
        return "⚠️ No information was returned for this request."
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump()
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    if isinstance(value, dict):
        lines: List[str] = []
        for key, item in value.items():
            if item is None:
                continue
            label = str(key).replace("_", " ").strip().title()
            rendered = _whatsapp_text(item)
            if "\n" in rendered:
                lines.append(f"*{label}*\n{rendered}")
            else:
                lines.append(f"*{label}:* {rendered}")
        return "\n".join(lines) or "⚠️ No information was returned for this request."
    if isinstance(value, (list, tuple, set)):
        items = [_whatsapp_text(item) for item in value]
        return "\n".join(f"• {item}" for item in items if item)
    return str(value).strip()


async def _invoke_compatible(method: Any, candidates: List[tuple[Any, ...]]) -> Any:
    """Invoke sync/async methods while adapting known legacy call contracts."""
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        signature = None

    last_contract_error: Optional[Exception] = None
    for arguments in candidates:
        if signature is not None:
            try:
                signature.bind(*arguments)
            except TypeError:
                continue
        try:
            return await _resolve(method(*arguments))
        except Exception as exc:
            is_contract_error = isinstance(exc, TypeError) or exc.__class__.__name__ == "ValidationError"
            if not is_contract_error:
                raise
            last_contract_error = exc
    if last_contract_error is not None:
        raise last_contract_error
    raise TypeError(f"No compatible call signature found for {method!r}")


class _ServiceRegistryAdapter:
    """Compatibility layer for webhook v28.2 diagnostic endpoints."""

    def __init__(self, owner: "AIProviderService") -> None:
        self._owner = owner

    def _services(self) -> Dict[str, Any]:
        return {
            "dn": self._owner.dn_service,
            "dn_analysis": self._owner.dn_service,
            "dealer": self._owner.dealer_service,
            "dealer_analytics": self._owner.dealer_service,
            "city": self._owner.city_service,
            "city_service": self._owner.city_service,
            "product": self._owner.product_service,
            "product_service": self._owner.product_service,
            "national_kpi": self._owner.national_kpi_service,
            "national_kpi_service": self._owner.national_kpi_service,
            "groq": self._owner.groq_service,
            "groq_service": self._owner.groq_service,
        }

    def get_service_instance(self, name: str) -> Optional[Any]:
        return self._services().get((name or "").casefold())

    def get_service_status(self, name: str) -> Dict[str, Any]:
        service = self.get_service_instance(name)
        return {
            "name": name,
            "ready": service is not None,
            "status": "READY" if service is not None else "UNAVAILABLE",
        }


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

        # Only mark initialized after all mandatory state exists.
        self.dn_service = DNAnalysisService()
        self.dealer_service = DealerAnalyticsService()
        self.city_service = CityService()
        self.product_service = ProductService()
        self.national_kpi_service = NationalKPIService()
        self.groq_service = GroqService()
        self._router: Any = None
        self._router_init_attempted = False
        self._router_lock = threading.Lock()
        self._cache: Dict[str, tuple[float, RoutingDecision]] = {}
        self._cache_ttl = 300.0
        self.registry = _ServiceRegistryAdapter(self)
        self._initialized = True
        logger.info("AIProviderService initialized; semantic router will load lazily")

    def get_service_registry_status(self) -> Dict[str, Any]:
        """Legacy health API expected by app/routes/webhook.py v28.2."""
        unique_services = (
            self.dn_service,
            self.dealer_service,
            self.city_service,
            self.product_service,
            self.national_kpi_service,
            self.groq_service,
        )
        ready = sum(service is not None for service in unique_services)
        total = len(unique_services)
        return {
            "ready": ready,
            "in_development": total - ready,
            "total": total,
            "readiness_score": (ready / total * 100.0) if total else 0.0,
            "semantic_router": "ready" if self._router is not None else "lazy",
        }

    def _ensure_semantic_router(self) -> None:
        if self._router is not None or self._router_init_attempted:
            return
        with self._router_lock:
            if self._router is not None or self._router_init_attempted:
                return
            self._router_init_attempted = True
            if not SEMANTIC_ROUTER_AVAILABLE:
                logger.warning("Semantic routing disabled: %s", SEMANTIC_ROUTER_IMPORT_ERROR)
                return
            try:
                encoder = HuggingFaceEncoder()
                routes = [Route(name=name, utterances=utterances) for name, utterances in ROUTE_UTTERANCES.items()]
                try:
                    self._router = SemanticRouter(encoder=encoder, routes=routes, auto_sync="local")
                except TypeError:
                    self._router = SemanticRouter(encoder=encoder, routes=routes)
                logger.info("Semantic Router initialized with %d routes", len(routes))
            except Exception:
                self._router = None
                logger.exception("Semantic Router initialization failed; deterministic routing remains available")

    @staticmethod
    def _extract_dn(text: str) -> Optional[str]:
        compact = text.strip()
        match = re.search(r"(?<!\d)(\d{8,12})(?!\d)", compact)
        if match:
            return match.group(1)
        match = re.search(r"(?<!\d)(\d{4}[\s-]*\d{4}[\s-]*\d{0,4})(?!\d)", compact)
        if match:
            candidate = re.sub(r"[\s-]", "", match.group(1))
            return candidate if 8 <= len(candidate) <= 12 else None
        return None

    @staticmethod
    def _menu_number(text: str) -> Optional[str]:
        match = re.fullmatch(r"\s*([0-9])(?:[.)])?\s*", text)
        return match.group(1) if match else None

    @staticmethod
    def _extract_entities(text: str) -> Dict[str, Any]:
        entities: Dict[str, Any] = {}
        dn = AIProviderService._extract_dn(text)
        if dn:
            entities.update({"dn": dn, "dn_number": dn, "id": dn})

        lowered = text.casefold()
        for city in CITY_NAMES:
            if re.search(rf"\b{re.escape(city)}\b", lowered):
                entities.update({"city": city.title(), "city_name": city.title()})
                break

        dealer = re.search(
            r"([\w&.'\- ]{2,}?(?:electronics|traders|distributors|foods|group|pvt|ltd|sons|brothers|enterprises|company|corporation)(?:[\w&.'\- ]*)?)",
            text,
            re.IGNORECASE,
        )
        if dealer:
            name = dealer.group(1).strip()
            entities.update({"dealer": name, "dealer_name": name})

        warehouse = re.search(r"(?:warehouse|depot|\bwh\b)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if warehouse:
            entities["warehouse"] = warehouse.group(1).strip()

        product = re.search(r"(?:product|model|material|item)\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if product:
            entities["product"] = product.group(1).strip()

        division = re.search(r"\bdivision\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if division:
            entities["division"] = division.group(1).strip()

        sales_office = re.search(r"\bsales\s+office\s+([\w&.'\- ]{2,})", text, re.IGNORECASE)
        if sales_office:
            entities["sales_office"] = sales_office.group(1).strip()

        material = re.search(r"\bmaterial(?:\s+(?:number|code))?\s+([\w.\-]{2,})", text, re.IGNORECASE)
        if material:
            entities["material"] = material.group(1).strip()

        iso_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
        slash_dates = re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", text)
        dates = iso_dates + slash_dates
        if dates:
            entities["date"] = dates[0]
            if len(dates) > 1:
                entities["date_range"] = {"from": dates[0], "to": dates[1]}

        flags = {
            "pgi": r"\bpgi\b|\bpost goods issue\b",
            "pod": r"\bpod\b|\bproof of delivery\b",
            "pending": r"\bpending\b|\boutstanding\b|\boverdue\b",
            "top": r"\btop\b|\bbest\b|\bhighest\b",
            "bottom": r"\bbottom\b|\bworst\b|\blowest\b",
            "revenue": r"\brevenue\b|\bsales\b|\bearnings\b",
            "units": r"\bunits?\b|\bquantity\b|\bqty\b",
        }
        for key, pattern in flags.items():
            if re.search(pattern, lowered):
                entities[key] = True
        return entities

    @staticmethod
    def _decision_for_menu(menu_option: str, message: str, entities: Optional[Dict[str, Any]] = None, intent: Optional[str] = None, confidence: float = 1.0, reason: str = "") -> RoutingDecision:
        config = MENU_OPTIONS[menu_option]
        return RoutingDecision(
            intent=intent or config["name"].lower().replace(" ", "_"),
            confidence=confidence,
            service_key=config["service_key"],
            service_file=config["service_file"],
            method=config["method"],
            entity=entities or {},
            requires_ai=config["requires_ai"],
            reason=reason,
            original_message=message,
            menu_option=menu_option,
        )

    def _semantic_intent(self, message: str) -> tuple[Optional[str], float]:
        self._ensure_semantic_router()
        if self._router is None:
            return None, 0.0
        try:
            result = self._router(message) if callable(self._router) else self._router.route(message)
            if result is None:
                return None, 0.0
            return getattr(result, "name", None), float(getattr(result, "score", 1.0) or 0.0)
        except Exception:
            logger.exception("Semantic routing failed for message")
            return None, 0.0

    @staticmethod
    def _rule_intent(message: str) -> Optional[str]:
        """Cheap, dependable routing for common commands when embeddings are down."""
        text = message.casefold()
        rules = (
            (r"\b(?:pending\s+pod|proof of delivery pending)\b", "pending_pod"),
            (r"\b(?:pending\s+pgi|goods issue pending)\b", "pending_pgi"),
            (r"\b(?:pending\s+dn|pending deliveries)\b", "pending_dns"),
            (r"\b(?:top|best)\s+performers?\b|\bleaderboard\b", "top_performers"),
            (r"\b(?:top|best)\s+dealers?\b|\bdealer ranking\b", "top_dealers"),
            (r"\b(?:dn|delivery note)\b", "dn_lookup"),
            (r"\bdealer\b", "dealer_dashboard"),
            (r"\bwarehouse\b|\bdepot\b", "warehouse_dashboard"),
            (r"\bcity\b|\bcities\b", "city_dashboard"),
            (r"\bproduct\b|\bmaterial\b", "product_dashboard"),
            (r"\b(?:national kpi|overall performance|executive dashboard)\b", "national_kpi"),
            (r"\b(?:total|national|overall)\s+revenue\b", "national_revenue"),
            (r"\b(?:total|national|overall)\s+units?\b", "national_units"),
        )
        for pattern, intent in rules:
            if re.search(pattern, text):
                return intent
        return None

    def _make_routing_decision(self, message: str) -> RoutingDecision:
        normalized = message.strip()
        cache_key = normalized.casefold()
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]

        if not normalized:
            decision = self._decision_for_menu("0", message, reason="Empty message")
        elif (dn := self._extract_dn(normalized)):
            entities = {"dn": dn, "dn_number": dn, "id": dn}
            decision = self._decision_for_menu("1", message, entities, "dn_lookup", reason="DN number detected")
        elif normalized.casefold() in {
            "menu", "main menu", "options", "start", "back", "home", "help",
            "hi", "hello", "show menu", "display menu",
        }:
            decision = self._decision_for_menu("0", message, reason="Menu keyword detected")
        elif (number := self._menu_number(normalized)) is not None:
            decision = self._decision_for_menu(number, message, reason="Menu number selected")
        else:
            entities = self._extract_entities(normalized)
            # Explicit command words take priority (for example, "Warehouse
            # Rawalpindi" must not become a city route merely because
            # Rawalpindi is also a recognized city).
            rule_intent = self._rule_intent(normalized)
            rule_menu = INTENT_TO_MENU.get(rule_intent or "")
            if rule_menu:
                decision = self._decision_for_menu(
                    rule_menu, message, entities, rule_intent, 1.0,
                    "Deterministic rule matched",
                )
            elif "dealer" in entities:
                decision = self._decision_for_menu("2", message, entities, "dealer_dashboard", reason="Dealer entity detected")
            elif "city" in entities:
                decision = self._decision_for_menu("3", message, entities, "city_dashboard", reason="City entity detected")
            elif "warehouse" in entities:
                decision = self._decision_for_menu("4", message, entities, "warehouse_dashboard", reason="Warehouse entity detected")
            elif "product" in entities:
                decision = self._decision_for_menu("5", message, entities, "product_dashboard", reason="Product entity detected")
            else:
                intent, confidence = self._semantic_intent(normalized)
                menu_option = INTENT_TO_MENU.get(intent or "")
                if menu_option and confidence >= 0.30:
                    decision = self._decision_for_menu(menu_option, message, entities, intent, confidence, "Semantic route matched")
                else:
                    decision = self._decision_for_menu("9", message, entities or {"message": message}, "general_ai", max(confidence, 0.30), "AI fallback")

        self._cache[cache_key] = (time.monotonic(), decision)
        if len(self._cache) > 1000:
            self._cache.clear()
        return decision

    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **_: Any,
    ) -> str:
        # ``sender_id`` is retained for compatibility with webhook v28.2.
        sender = sender or sender_id
        started_at = time.perf_counter()
        if not message or not message.strip():
            logger.info("Empty message routed to menu in %.2fms", (time.perf_counter() - started_at) * 1000)
            return get_main_menu()

        logger.info("Processing WhatsApp message from %s", sender or "unknown")
        decision = self._make_routing_decision(message)
        logger.info("Route: %s -> %s.%s (%s)", decision.intent, decision.service_file, decision.method, decision.reason)

        if decision.service_key == "menu_service":
            logger.info("Menu response completed in %.2fms", (time.perf_counter() - started_at) * 1000)
            return get_main_menu()

        services = {
            "dn_analysis": self.dn_service,
            "dealer_analytics": self.dealer_service,
            "city_service": self.city_service,
            "product_service": self.product_service,
            "national_kpi_service": self.national_kpi_service,
            "groq_service": self.groq_service,
        }
        service = services.get(decision.service_key)
        if service is None:
            logger.error("Unknown service key: %s", decision.service_key)
            return get_invalid_selection_message()

        try:
            method, selected_method = _resolve_compatible_method(service, decision.method)
            if method is None:
                aliases = ", ".join(METHOD_CANDIDATES.get(decision.method, (decision.method,)))
                logger.error(
                    "No compatible method on %s; checked: %s",
                    decision.service_key,
                    aliases,
                )
                return f"⚠️ {MENU_OPTIONS[decision.menu_option or '0']['name']} is not configured correctly."
            logger.info(
                "Executing service=%s method=%s entities=%s",
                decision.service_key,
                selected_method,
                decision.entity,
            )
            if decision.service_key == "groq_service":
                result = await _invoke_compatible(
                    method,
                    [(message, decision.entity), (message,)],
                )
            else:
                # Existing analytics services validate their primary argument as
                # a string (for example DNAnalysisService expects "6243701122"),
                # whereas some newer implementations accept an entity dict.
                # Prefer the scalar contract when an entity is present.
                scalar_argument = _scalar_service_argument(decision)
                if scalar_argument is not None:
                    candidates = [(scalar_argument,), (decision.entity,), ()]
                else:
                    # Most existing dashboard services validate a query string;
                    # newer services accept the extracted entity mapping or no args.
                    candidates = [("",), (decision.entity,), ()]
                result = await _invoke_compatible(method, candidates)
            response_text = _whatsapp_text(result)
            logger.info(
                "Request completed intent=%s service=%s method=%s ai_fallback=%s elapsed_ms=%.2f",
                decision.intent,
                decision.service_key,
                selected_method,
                decision.service_key == "groq_service",
                (time.perf_counter() - started_at) * 1000,
            )
            return response_text or "⚠️ No response was returned. Please try again."
        except Exception:
            logger.exception("Service call failed: %s.%s", decision.service_key, decision.method)
            if decision.service_key == "groq_service":
                return "⚠️ AI service is temporarily unavailable. Reply *menu* to use logistics services."
            return f"⚠️ {MENU_OPTIONS[decision.menu_option or '0']['name']} is temporarily unavailable. Please try again."


_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()


def get_ai_provider_service() -> AIProviderService:
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                _ai_service = AIProviderService()
    return _ai_service


def get_whatsapp_provider_service() -> AIProviderService:
    """Backward-compatible factory used by webhook v28.2 and older code."""
    return get_ai_provider_service()


async def process_whatsapp_query(
    message: str,
    sender: Optional[str] = None,
    sender_id: Optional[str] = None,
    **kwargs: Any,
) -> str:
    try:
        return await get_ai_provider_service().process_whatsapp_query(
            message=message,
            sender=sender,
            sender_id=sender_id,
            **kwargs,
        )
    except Exception:
        logger.exception("Unexpected AI provider failure")
        # Keep WhatsApp responsive even for an unforeseen initialization bug.
        if message and message.strip().casefold() in {"menu", "main menu", "help", "start", "0"}:
            return get_main_menu()
        return "⚠️ Service is temporarily unavailable. Reply *menu* to try again."


__all__ = [
    "process_whatsapp_query",
    "get_main_menu",
    "get_ai_provider_service",
    "get_whatsapp_provider_service",
    "RoutingDecision",
    "MENU_OPTIONS",
    "INTENT_TO_MENU",
]
