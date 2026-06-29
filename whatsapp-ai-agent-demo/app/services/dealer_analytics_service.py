"""Enterprise dealer intelligence built only from DeliveryReport/PostgreSQL."""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from cachetools import TTLCache
from rapidfuzz import fuzz, process
from sqlalchemy import and_, case, distinct, func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

try:
    import openrouteservice
except ImportError:  # optional at runtime
    openrouteservice = None  # type: ignore[assignment]

try:
    from geopy.distance import great_circle
except ImportError:  # small built-in fallback remains available
    great_circle = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
ORS_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY") or os.getenv("ORS_API_KEY")
CACHE_TTL = max(300, int(os.getenv("DEALER_ANALYTICS_CACHE_TTL", "21600")))


def _text(value: Any, default: str = "Unknown") -> str:
    result = str(value).strip() if value is not None else ""
    return result or default


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0


def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return _text(value, "N/A")


def _status_complete(column: Any) -> Any:
    return func.lower(func.coalesce(column, "")).in_(("completed", "complete", "delivered", "done", "yes"))


@dataclass
class DistanceAnalytics:
    warehouse: str
    dealer_city: str
    distance_km: Optional[float] = None
    estimated_driving_minutes: Optional[int] = None
    estimated_driving_time: str = "Unknown"
    estimated_delivery_time: str = "Unknown"
    source: str = "unavailable"


@dataclass
class DealerDashboard:
    dealer_name: str
    dealer_code: str
    customer_code: str
    city: str
    warehouse: str
    warehouse_code: str
    sales_office: str
    sales_manager: str
    division: str
    total_dn: int
    completed_dn: int
    pending_dn: int
    total_units: int
    total_revenue: float
    average_revenue_per_dn: float
    average_units_per_dn: float
    first_delivery_date: str
    latest_delivery_date: str
    average_delivery_days: float
    average_pod_days: float
    average_total_cycle_time: float
    delivery_success_pct: float
    pgi_success_pct: float
    pod_success_pct: float
    pending_pct: float
    distance: DistanceAnalytics
    delivery_location: str = "Unknown"
    revenue_rank: Optional[int] = None
    delivery_rank: Optional[int] = None
    busiest_month: str = "Unknown"
    strongest_product_category: str = "Unknown"
    weakest_product_category: str = "Unknown"
    revenue_growth_pct: Optional[float] = None
    insights: list[str] = field(default_factory=list)

    # Extended delivery and pending KPIs. Defaults preserve every existing
    # DealerDashboard constructor used by the service and external callers.
    delivered_units: int = 0
    pending_units: int = 0
    delivered_revenue: float = 0.0
    pending_revenue: float = 0.0
    pgi_pending_dn: int = 0
    pod_pending_dn: int = 0
    delivery_pending_dn: int = 0

    # DN timeline and exception analytics.
    oldest_pending_dn: str = "N/A"
    oldest_pending_days: int = 0
    newest_dn: str = "N/A"
    highest_revenue_dn: str = "N/A"
    lowest_revenue_dn: str = "N/A"
    highest_unit_dn: str = "N/A"
    lowest_unit_dn: str = "N/A"
    average_revenue_per_unit: float = 0.0

    # Warehouse and product contribution analytics.
    warehouse_utilization: float = 0.0
    delivery_coverage: float = 0.0
    top_product: str = "Unknown"
    top_model: str = "Unknown"
    top_material: str = "Unknown"

    # Month-over-month performance analytics.
    current_month_revenue: float = 0.0
    previous_month_revenue: float = 0.0
    monthly_growth: float = 0.0
    current_month_dn: int = 0
    previous_month_dn: int = 0
    current_month_units: int = 0
    previous_month_units: int = 0
    best_month: str = "Unknown"
    worst_month: str = "Unknown"

    # Pending severity and national rankings.
    pending_average_days: float = 0.0
    critical_pending: int = 0
    overdue_pending: int = 0
    national_rank: Optional[int] = None
    unit_rank: Optional[int] = None
    dn_rank: Optional[int] = None
    pod_rank: Optional[int] = None
    pending_rank: Optional[int] = None
    regional_rank: Optional[int] = None
    fastest_delivery_days: float = 0.0
    slowest_delivery_days: float = 0.0
    latest_pgi_date: str = "N/A"
    latest_pod_date: str = "N/A"
    same_day_deliveries: int = 0
    next_day_deliveries: int = 0
    top_division: str = "Unknown"
    recommendations: list[str] = field(default_factory=list)
    business_score: float = 0.0
    overall_status: str = "Needs Attention"
    executive_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_whatsapp_message(self) -> str:
        distance = "Unknown" if self.distance.distance_km is None else f"{self.distance.distance_km:,.1f} KM"
        insights = "\n".join(f"\u2022 {item}" for item in self.insights[:10]) or "\u2022 No significant exception detected."
        recommendations = "\n".join(f"\u2022 {item}" for item in self.recommendations[:5]) or "\u2022 Continue monitoring delivery performance."
        return "\n".join(
            (
                "\U0001f3e2 Dealer Dashboard",
                "\u2501" * 18,
                "\U0001f464 Dealer Information",
                f"Dealer Name: {self.dealer_name}",
                f"Dealer Code: {self.dealer_code}",
                f"Customer Code: {self.customer_code}",
                f"City: {self.city}",
                f"Delivery Location: {self.delivery_location}",
                f"Sales Office: {self.sales_office}",
                f"Sales Manager: {self.sales_manager}",
                f"Division: {self.division}",
                "",
                "\U0001f3ed Warehouse",
                f"Warehouse: {self.warehouse} ({self.warehouse_code})",
                f"Distance: {distance}",
                f"Driving Time: {self.distance.estimated_driving_time}",
                f"Estimated Delivery: {self.distance.estimated_delivery_time}",
                "",
                "\U0001f4ca Business Summary",
                f"Revenue: PKR {self.total_revenue:,.2f}",
                f"Average Revenue/DN: PKR {self.average_revenue_per_dn:,.2f}",
                f"Units: {self.total_units:,}",
                f"Average Units/DN: {self.average_units_per_dn:,.2f}",
                f"Total DNs: {self.total_dn:,}",
                f"Completed DNs: {self.completed_dn:,}",
                f"Pending DNs: {self.pending_dn:,}",
                "",
                "\U0001f69a Delivery Performance",
                f"Delivery Success: {self.delivery_success_pct:.2f}%",
                f"PGI Success: {self.pgi_success_pct:.2f}%",
                f"POD Success: {self.pod_success_pct:.2f}%",
                f"Pending: {self.pending_pct:.2f}%",
                f"Average Delivery: {self.average_delivery_days:.2f} Days",
                f"Average POD: {self.average_pod_days:.2f} Days",
                f"Average Total Cycle: {self.average_total_cycle_time:.2f} Days",
                f"Fastest / Slowest: {self.fastest_delivery_days:.0f} / {self.slowest_delivery_days:.0f} Days",
                "",
                "\U0001f4c5 Date Summary",
                f"First DN: {self.first_delivery_date}",
                f"Latest DN: {self.latest_delivery_date}",
                f"Latest PGI: {self.latest_pgi_date}",
                f"Latest POD: {self.latest_pod_date}",
                "",
                "\U0001f4e6 Product Performance",
                f"Top Product: {self.top_product}",
                f"Top Model: {self.top_model}",
                f"Top Material: {self.top_material}",
                f"Strongest Category: {self.strongest_product_category}",
                f"Weakest Category: {self.weakest_product_category}",
                "",
                "\U0001f3c6 Dealer Ranking",
                f"Revenue Rank: {self.revenue_rank or 'N/A'}",
                f"Delivery Rank: {self.delivery_rank or 'N/A'}",
                f"Regional Rank: {self.regional_rank or 'N/A'}",
                f"National Rank: {self.national_rank or 'N/A'}",
                "",
                "\u26a0 Pending Dashboard",
                f"Pending Revenue: PKR {self.pending_revenue:,.2f}",
                f"Pending Units: {self.pending_units:,}",
                f"Pending DNs: {self.pending_dn:,}",
                f"Average Pending: {self.pending_average_days:.1f} Days",
                f"Oldest Pending: {self.oldest_pending_dn} ({self.oldest_pending_days} Days)",
                "",
                "\U0001f7e2 Business Health",
                f"Overall Score: {self.business_score:.1f}/100",
                f"Business Status: {self.overall_status}",
                "",
                "\U0001f4a1 Key Insights",
                insights,
                "",
                "\U0001f4cc Recommendations",
                recommendations,
                "",
                "\U0001f4dd Executive Summary",
                self.executive_summary or "Performance is stable; continue monitoring pending deliveries and POD closure.",
            )
        )

    def __str__(self) -> str:
        return self.to_whatsapp_message()


@dataclass
class DealerComparison:
    dealers: list[DealerDashboard]
    revenue_leader: str
    units_leader: str
    dn_leader: str
    delivery_leader: str
    summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DealerRanking:
    sort_by: str
    order: str
    dealers: list[DealerDashboard]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DealerSearchResult:
    original_message: str
    extracted_dealer: str
    normalized_dealer: str
    dealer_found: Optional[str] = None
    dealer_code: Optional[str] = None
    customer_code: Optional[str] = None
    alias_used: Optional[str] = None
    rapidfuzz_score: Optional[float] = None
    semantic_score: Optional[float] = None
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False
    cache_used: bool = False
    exception: Optional[str] = None


class CityCoordinateService:
    """Cached coordinates; distances are calculated, never hardcoded."""

    COORDINATES: dict[str, tuple[float, float]] = {
        "abbottabad": (34.1688, 73.2215), "attock": (33.7667, 72.3667),
        "bahawalpur": (29.3956, 71.6836), "bannu": (32.9861, 70.6042),
        "dera ghazi khan": (30.0489, 70.6455), "dera ismail khan": (31.8315, 70.9017),
        "faisalabad": (31.4504, 73.1350), "gilgit": (35.9208, 74.3144),
        "gujranwala": (32.1877, 74.1945), "gujrat": (32.5731, 74.1005),
        "haripur": (33.9946, 72.9106), "hyderabad": (25.3960, 68.3578),
        "islamabad": (33.6844, 73.0479), "jacobabad": (28.2819, 68.4382),
        "jhelum": (32.9405, 73.7276), "karachi": (24.8607, 67.0011),
        "kasur": (31.1187, 74.4508), "kohat": (33.5834, 71.4332),
        "lahore": (31.5204, 74.3587), "larkana": (27.5570, 68.2028),
        "mardan": (34.1989, 72.0231), "mansehra": (34.3302, 73.1968),
        "mirpur": (33.1484, 73.7517), "multan": (30.1575, 71.5249),
        "muzaffarabad": (34.3700, 73.4711), "nawabshah": (26.2442, 68.4100),
        "okara": (30.8138, 73.4534), "peshawar": (34.0151, 71.5249),
        "quetta": (30.1798, 66.9750), "rahim yar khan": (28.4212, 70.2989),
        "rawalpindi": (33.5651, 73.0169), "sahiwal": (30.6682, 73.1114),
        "sargodha": (32.0836, 72.6711), "sheikhupura": (31.7167, 73.9850),
        "sialkot": (32.4945, 74.5229), "skardu": (35.2971, 75.6333),
        "sukkur": (27.7244, 68.8228), "swat": (35.2227, 72.4258),
        "wah cantt": (33.7715, 72.7511), "taxila": (33.7463, 72.8397),
    }

    def __init__(self) -> None:
        self._names = tuple(self.COORDINATES)

    @staticmethod
    def normalize(city: Any) -> str:
        value = _text(city, "").lower().replace("city", "").strip(" ,.-")
        aliases = {"rwp": "rawalpindi", "isb": "islamabad", "lhr": "lahore", "khi": "karachi", "fsd": "faisalabad", "hyd": "hyderabad", "ryk": "rahim yar khan", "dik": "dera ismail khan"}
        return aliases.get(value, value)

    def get(self, city: Any) -> Optional[tuple[float, float]]:
        normalized = self.normalize(city)
        if normalized in self.COORDINATES:
            return self.COORDINATES[normalized]
        match = process.extractOne(normalized, self._names, scorer=fuzz.WRatio, score_cutoff=82)
        return self.COORDINATES[match[0]] if match else None


class DistanceService:
    def __init__(self, coordinates: CityCoordinateService) -> None:
        self.coordinates = coordinates
        self.cache: TTLCache[str, DistanceAnalytics] = TTLCache(maxsize=4096, ttl=CACHE_TTL)
        self._lock = threading.RLock()
        self._ors = None
        if ORS_API_KEY and openrouteservice:
            try:
                self._ors = openrouteservice.Client(key=ORS_API_KEY, timeout=5)
            except Exception:
                logger.exception("ORS client initialization failed; distance service is degraded")

    @staticmethod
    def delivery_estimate(km: Optional[float]) -> str:
        if km is None:
            return "Unknown"
        if km <= 80:
            return "Same Day"
        if km <= 200:
            return "Next Day"
        if km <= 400:
            return "1-2 Days"
        if km <= 700:
            return "2-3 Days"
        return "3-5 Days"

    @staticmethod
    def driving_time(minutes: Optional[int]) -> str:
        if minutes is None:
            return "Unknown"
        hours, mins = divmod(max(0, minutes), 60)
        return f"{hours} hr {mins} min" if hours and mins else (f"{hours} hr" if hours else f"{mins} min")

    @staticmethod
    def _haversine(origin: tuple[float, float], destination: tuple[float, float]) -> float:
        lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *destination))
        value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        return 6371.0088 * 2 * math.asin(math.sqrt(value))

    def calculate(self, warehouse: Any, dealer_city: Any) -> DistanceAnalytics:
        warehouse_name, city_name = _text(warehouse), _text(dealer_city)
        key = f"{self.coordinates.normalize(warehouse_name)}|{self.coordinates.normalize(city_name)}"
        with self._lock:
            cached = self.cache.get(key)
        if cached:
            return cached
        origin, destination = self.coordinates.get(warehouse_name), self.coordinates.get(city_name)
        if not origin or not destination:
            result = DistanceAnalytics(warehouse_name, city_name)
        else:
            km: Optional[float] = None
            minutes: Optional[int] = None
            source = "great-circle"
            if self._ors:
                try:
                    route = self._ors.directions([(origin[1], origin[0]), (destination[1], destination[0])], profile="driving-car")
                    summary = route["routes"][0]["summary"]
                    km = float(summary["distance"]) / 1000
                    minutes = int(round(float(summary["duration"]) / 60))
                    source = "openrouteservice"
                except Exception:
                    logger.warning("ORS route failed for %s to %s; using great-circle", warehouse_name, city_name, exc_info=True)
            if km is None:
                km = float(great_circle(origin, destination).km) if great_circle else self._haversine(origin, destination)
                # Road distance/time estimates are intentionally conservative.
                km *= 1.20
                minutes = int(round(km / 55 * 60))
            result = DistanceAnalytics(warehouse_name, city_name, round(km, 1), minutes, self.driving_time(minutes), self.delivery_estimate(km), source)
        with self._lock:
            self.cache[key] = result
        return result


class DealerAnalyticsService:
    """Enterprise dealer analytics with stable, routing-compatible methods."""

    SORT_ALIASES = {
        "revenue": "total_revenue", "units": "total_units", "dn": "total_dn", "dn_count": "total_dn",
        "average_delivery": "average_delivery_days", "fastest_delivery": "average_delivery_days",
        "highest_pod": "pod_success_pct", "lowest_pending": "pending_pct", "best_revenue_growth": "revenue_growth_pct",
        "highest_pending": "pending_pct", "lowest_revenue": "total_revenue", "lowest_units": "total_units",
        "slowest_delivery": "average_delivery_days", "poor_pod": "pod_success_pct",
    }
    STOP_PHRASES = (
        "tell me about", "dealer dashboard", "dealer profile", "dealer performance",
        "dealer statistics", "dealer revenue", "dealer distance", "dealer pending",
        "dealer status", "dealer pod", "dealer pgi", "show", "display", "dealer",
        "profile", "statistics", "performance", "status", "revenue", "distance",
        "pending", "dashboard", "about", "of", "the", "company", "private",
        "limited", "pvt", "ltd",
    )
    DEALER_ALIASES = {
        "mian": "Mian Group Chakwal",
        "mgc": "Mian Group Chakwal",
        "mian chakwal": "Mian Group Chakwal",
        "mian wah": "Mian Group Chakwal",
        "mian chakwal wah": "Mian Group Chakwal",
        "mian group chakwal wah": "Mian Group Chakwal",
        "taj": "Taj Electronics",
        "taj haripur": "Taj Electronics Haripur",
    }

    def __init__(self) -> None:
        self._service_name = "dealer_analytics"
        self._version = "4.1.0"
        self._startup_time = datetime.utcnow().isoformat()
        self._initialization_errors: list[str] = []
        try:
            self._coordinates = CityCoordinateService()
        except Exception as error:
            logger.exception("Coordinate service initialization failed")
            self._initialization_errors.append(str(error))
            self._coordinates = CityCoordinateService.__new__(CityCoordinateService)
            self._coordinates._names = tuple()
            self._coordinates.COORDINATES = {}
        try:
            self._distance = DistanceService(self._coordinates)
        except Exception as error:
            logger.exception("Distance service initialization failed")
            self._initialization_errors.append(str(error))
            self._distance = None
        self._dealer_cache: TTLCache[str, DealerSearchResult] = TTLCache(maxsize=2048, ttl=CACHE_TTL)
        self._candidate_cache: TTLCache[str, list[dict[str, str]]] = TTLCache(maxsize=1, ttl=900)
        self._extended_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=2048, ttl=900)
        self._dashboard_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=2048, ttl=300)
        self._ranking_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=64, ttl=300)
        self._search_lock = threading.RLock()
        self._last_diagnostic: dict[str, Any] = {}

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

    @staticmethod
    def _dealer_filter(identifier: str) -> Any:
        token = identifier.strip()
        return or_(
            func.lower(func.trim(DeliveryReport.customer_name)) == token.lower(),
            DeliveryReport.dealer_code == token,
            DeliveryReport.customer_code == token,
        )

    @classmethod
    def _normalize_dealer_text(cls, value: Any) -> str:
        text_value = unicodedata.normalize("NFKD", _text(value, "").lower())
        text_value = re.sub(r"[^a-z0-9\s]", " ", text_value)
        text_value = re.sub(r"\s+", " ", text_value).strip()
        for phrase in sorted(cls.STOP_PHRASES, key=len, reverse=True):
            text_value = re.sub(rf"\b{re.escape(phrase)}\b", " ", text_value)
        return re.sub(r"\s+", " ", text_value).strip()

    def _dealer_candidates(self, session: Session) -> tuple[list[dict[str, str]], bool]:
        with self._search_lock:
            cached = self._candidate_cache.get("all")
        if cached is not None:
            return cached, True
        started = time.perf_counter()
        rows = session.query(
            DeliveryReport.customer_name,
            DeliveryReport.dealer_code,
            DeliveryReport.customer_code,
        ).filter(DeliveryReport.customer_name.isnot(None)).distinct().all()
        candidates = [
            {
                "name": _text(row.customer_name),
                "dealer_code": _text(row.dealer_code, ""),
                "customer_code": _text(row.customer_code, ""),
                "normalized": self._normalize_dealer_text(row.customer_name),
            }
            for row in rows if _text(row.customer_name, "")
        ]
        with self._search_lock:
            self._candidate_cache["all"] = candidates
        logger.info("Dealer candidate query returned %s rows in %.2fms", len(candidates), (time.perf_counter() - started) * 1000)
        return candidates, False

    def _resolve_dealer(self, session: Session, message: str) -> DealerSearchResult:
        started = time.perf_counter()
        original = _text(message, "")
        normalized = self._normalize_dealer_text(original)
        alias = self.DEALER_ALIASES.get(normalized)
        search_text = alias or normalized
        cache_key = search_text.lower()
        with self._search_lock:
            cached = self._dealer_cache.get(cache_key)
        if cached:
            result = DealerSearchResult(**asdict(cached))
            result.original_message, result.cache_used = original, True
            return result
        result = DealerSearchResult(original, search_text, normalized, alias_used=alias)
        try:
            candidates, cache_used = self._dealer_candidates(session)
            result.cache_used = cache_used
            token = original.strip()
            code_matches = [item for item in candidates if token in {item["dealer_code"], item["customer_code"]}]
            if code_matches:
                best = code_matches[0]
                result.dealer_found, result.dealer_code, result.customer_code = best["name"], best["dealer_code"], best["customer_code"]
            else:
                exact = [item for item in candidates if item["normalized"] == self._normalize_dealer_text(search_text)]
                contains = [item for item in candidates if search_text and (search_text in item["normalized"] or item["normalized"] in search_text)]
                pool = exact or contains
                if len(pool) == 1:
                    best = pool[0]
                    result.dealer_found, result.dealer_code, result.customer_code = best["name"], best["dealer_code"], best["customer_code"]
                    result.rapidfuzz_score = 100.0 if exact else round(float(fuzz.WRatio(search_text, best["normalized"])), 2)
                else:
                    choices = {index: item["normalized"] for index, item in enumerate(candidates)}
                    matches = process.extract(search_text, choices, scorer=fuzz.WRatio, limit=5)
                    scored = [(candidates[index], float(score)) for _, score, index in matches]
                    result.suggestions = [{"dealer_name": item["name"], "similarity": round(score, 2), "dealer_code": item["dealer_code"]} for item, score in scored]
                    if scored:
                        result.rapidfuzz_score = round(scored[0][1], 2)
                    confident = [entry for entry in scored if entry[1] >= 85]
                    if len(confident) == 1 or (len(confident) > 1 and confident[0][1] - confident[1][1] >= 5):
                        best = confident[0][0]
                        result.dealer_found, result.dealer_code, result.customer_code = best["name"], best["dealer_code"], best["customer_code"]
                    elif confident:
                        result.ambiguous = True
            with self._search_lock:
                self._dealer_cache[cache_key] = result
        except Exception as error:
            result.exception = str(error)
            logger.exception("Dealer resolution failed for %s", original)
        self._last_diagnostic = {**asdict(result), "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)}
        logger.info("Dealer search original=%r normalized=%r alias=%r score=%r selected=%r", original, normalized, alias, result.rapidfuzz_score, result.dealer_found)
        return result

    @staticmethod
    def _suggestion_response(search: DealerSearchResult) -> dict[str, Any]:
        suggestions = search.suggestions[:5]
        if search.ambiguous:
            lines = ["Multiple Dealers Found", ""]
            for index, item in enumerate(suggestions, 1):
                lines.extend((str(index), item["dealer_name"], f'{item["similarity"]:.0f}%', ""))
            lines.append("Reply with dealer number.")
            code = "MULTIPLE_DEALERS_FOUND"
        else:
            lines = ["Did you mean", ""]
            for item in suggestions:
                lines.extend((item["dealer_name"], f'{item["similarity"]:.0f}%', ""))
            code = "DEALER_SUGGESTIONS"
        message = "\n".join(lines).strip()
        return {"success": False, "error_code": code, "message": message, "response": message, "formatted_response": message, "whatsapp_message": message, "suggestions": suggestions, "search": search}

    @staticmethod
    def _dealer_key(row: Any) -> str:
        return _text(row.dealer_code, _text(row.customer_code, _text(row.dealer_name)))

    def _aggregate_query(self, session: Session, dealer: Optional[str] = None) -> list[Any]:
        completed = or_(DeliveryReport.pending_flag.is_(False), _status_complete(DeliveryReport.delivery_status), DeliveryReport.pod_date.isnot(None))
        pending = or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None))
        pgi_pending = DeliveryReport.good_issue_date.is_(None)
        pod_pending = and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None))
        query = session.query(
            func.coalesce(DeliveryReport.customer_name, "Unknown").label("dealer_name"),
            func.coalesce(DeliveryReport.dealer_code, "Unknown").label("dealer_code"),
            func.coalesce(DeliveryReport.customer_code, "Unknown").label("customer_code"),
            func.max(DeliveryReport.ship_to_city).label("city"), func.max(DeliveryReport.delivery_location).label("delivery_location"), func.max(DeliveryReport.warehouse).label("warehouse"),
            func.max(DeliveryReport.warehouse_code).label("warehouse_code"), func.max(DeliveryReport.sales_office).label("sales_office"),
            func.max(DeliveryReport.sales_manager).label("sales_manager"), func.max(DeliveryReport.division).label("division"),
            func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
            func.count(distinct(case((completed, DeliveryReport.dn_no)))).label("completed_dn"),
            func.count(distinct(case((pending, DeliveryReport.dn_no)))).label("pending_dn"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
            func.coalesce(func.sum(case((completed, DeliveryReport.dn_qty), else_=0)), 0).label("delivered_units"),
            func.coalesce(func.sum(case((pending, DeliveryReport.dn_qty), else_=0)), 0).label("pending_units"),
            func.coalesce(func.sum(case((completed, DeliveryReport.dn_amount), else_=0.0)), 0.0).label("delivered_revenue"),
            func.coalesce(func.sum(case((pending, DeliveryReport.dn_amount), else_=0.0)), 0.0).label("pending_revenue"),
            func.count(distinct(case((pgi_pending, DeliveryReport.dn_no)))).label("pgi_pending_dn"),
            func.count(distinct(case((pod_pending, DeliveryReport.dn_no)))).label("pod_pending_dn"),
            func.count(distinct(case((pending, DeliveryReport.dn_no)))).label("delivery_pending_dn"),
            func.min(case((pending, DeliveryReport.dn_create_date))).label("oldest_pending_date"),
            func.avg(case((pending, func.current_date() - DeliveryReport.dn_create_date))).label("pending_average_days"),
            func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
            func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
            func.max(DeliveryReport.good_issue_date).label("latest_pgi_date"),
            func.max(DeliveryReport.pod_date).label("latest_pod_date"),
            func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
            func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
            func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
            func.min(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("fastest_delivery"),
            func.max(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("slowest_delivery"),
            func.count(distinct(case((DeliveryReport.good_issue_date - DeliveryReport.dn_create_date == 0, DeliveryReport.dn_no)))).label("same_day_deliveries"),
            func.count(distinct(case((DeliveryReport.good_issue_date - DeliveryReport.dn_create_date == 1, DeliveryReport.dn_no)))).label("next_day_deliveries"),
            func.count(distinct(case((_status_complete(DeliveryReport.delivery_status), DeliveryReport.dn_no)))).label("delivery_success"),
            func.count(distinct(case((or_(_status_complete(DeliveryReport.pgi_status), DeliveryReport.good_issue_date.isnot(None)), DeliveryReport.dn_no)))).label("pgi_success"),
            func.count(distinct(case((or_(_status_complete(DeliveryReport.pod_status), DeliveryReport.pod_date.isnot(None)), DeliveryReport.dn_no)))).label("pod_success"),
        ).filter(DeliveryReport.customer_name.isnot(None))
        if dealer:
            query = query.filter(self._dealer_filter(dealer))
        return query.group_by(DeliveryReport.customer_name, DeliveryReport.dealer_code, DeliveryReport.customer_code).all()

    @staticmethod
    def _days(value: Any) -> float:
        if value is None:
            return 0.0
        if hasattr(value, "days"):
            return round(float(value.days), 2)
        return round(_number(value), 2)

    def _row_to_dashboard(self, row: Any, include_distance: bool = True) -> DealerDashboard:
        total = int(row.total_dn or 0)
        dashboard = DealerDashboard(
            dealer_name=_text(row.dealer_name), dealer_code=_text(row.dealer_code), customer_code=_text(row.customer_code),
            city=_text(row.city), delivery_location=_text(getattr(row, "delivery_location", None)), warehouse=_text(row.warehouse), warehouse_code=_text(row.warehouse_code),
            sales_office=_text(row.sales_office), sales_manager=_text(row.sales_manager), division=_text(row.division),
            total_dn=total, completed_dn=int(row.completed_dn or 0), pending_dn=int(row.pending_dn or 0),
            total_units=int(row.total_units or 0), total_revenue=round(_number(row.total_revenue), 2),
            average_revenue_per_dn=round(_number(row.total_revenue) / total, 2) if total else 0,
            average_units_per_dn=round(_number(row.total_units) / total, 2) if total else 0,
            first_delivery_date=_date_text(row.first_delivery_date), latest_delivery_date=_date_text(row.latest_delivery_date),
            average_delivery_days=self._days(row.avg_delivery), average_pod_days=self._days(row.avg_pod),
            average_total_cycle_time=self._days(row.avg_cycle), delivery_success_pct=_percent(row.delivery_success, total),
            pgi_success_pct=_percent(row.pgi_success, total), pod_success_pct=_percent(row.pod_success, total),
            pending_pct=_percent(row.pending_dn, total),
            distance=(self._safe_distance(row.warehouse, row.city) if include_distance else DistanceAnalytics(_text(row.warehouse), _text(row.city))),
            delivered_units=int(getattr(row, "delivered_units", 0) or 0),
            pending_units=int(getattr(row, "pending_units", 0) or 0),
            delivered_revenue=round(_number(getattr(row, "delivered_revenue", 0)), 2),
            pending_revenue=round(_number(getattr(row, "pending_revenue", 0)), 2),
            pgi_pending_dn=int(getattr(row, "pgi_pending_dn", 0) or 0),
            pod_pending_dn=int(getattr(row, "pod_pending_dn", 0) or 0),
            delivery_pending_dn=int(getattr(row, "delivery_pending_dn", 0) or 0),
            oldest_pending_days=max(0, (date.today() - row.oldest_pending_date).days) if getattr(row, "oldest_pending_date", None) else 0,
            pending_average_days=self._days(getattr(row, "pending_average_days", 0)),
            average_revenue_per_unit=round(_number(row.total_revenue) / _number(row.total_units), 2) if _number(row.total_units) else 0.0,
            delivery_coverage=_percent(row.completed_dn, total),
            latest_pgi_date=_date_text(getattr(row, "latest_pgi_date", None)),
            latest_pod_date=_date_text(getattr(row, "latest_pod_date", None)),
            fastest_delivery_days=self._days(getattr(row, "fastest_delivery", 0)),
            slowest_delivery_days=self._days(getattr(row, "slowest_delivery", 0)),
            same_day_deliveries=int(getattr(row, "same_day_deliveries", 0) or 0),
            next_day_deliveries=int(getattr(row, "next_day_deliveries", 0) or 0),
        )
        dashboard.insights = self._basic_insights(dashboard)
        return dashboard

    def _safe_distance(self, warehouse: Any, city: Any) -> DistanceAnalytics:
        try:
            if self._distance is not None:
                return self._distance.calculate(warehouse, city)
        except Exception:
            logger.exception("Distance calculation failed for warehouse=%r city=%r", warehouse, city)
        return DistanceAnalytics(_text(warehouse), _text(city))

    def _apply_extended_analytics(self, session: Session, item: DealerDashboard) -> None:
        """Populate DN, month, product, warehouse and national-rank analytics."""
        identity = item.dealer_code if item.dealer_code != "Unknown" else item.customer_code
        identity = identity if identity != "Unknown" else item.dealer_name
        cache_key = str(identity).lower()
        cached = self._extended_cache.get(cache_key)
        if cached:
            for key, value in cached.items():
                setattr(item, key, value)
            self._apply_business_health(item)
            item.insights, item.recommendations = self._business_insights(item)
            return

        condition = self._dealer_filter(str(identity))
        values: dict[str, Any] = {}
        dn_rows = session.query(
            DeliveryReport.dn_no.label("dn"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.min(DeliveryReport.dn_create_date).label("created"),
            func.max(DeliveryReport.good_issue_date).label("issued"),
            func.max(DeliveryReport.pod_date).label("pod"),
            func.max(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label("pending"),
        ).filter(condition).group_by(DeliveryReport.dn_no).all()

        if dn_rows:
            by_revenue = sorted(dn_rows, key=lambda row: _number(row.revenue))
            by_units = sorted(dn_rows, key=lambda row: _number(row.units))
            by_date = sorted(dn_rows, key=lambda row: row.created or date.min)
            pending_rows = [row for row in dn_rows if int(row.pending or 0)]
            delivery_days = [(row.issued - row.created).days for row in dn_rows if row.created and row.issued and row.issued >= row.created]
            values.update({
                "highest_revenue_dn": _text(by_revenue[-1].dn, "N/A"),
                "lowest_revenue_dn": _text(by_revenue[0].dn, "N/A"),
                "highest_unit_dn": _text(by_units[-1].dn, "N/A"),
                "lowest_unit_dn": _text(by_units[0].dn, "N/A"),
                "newest_dn": _text(by_date[-1].dn, "N/A"),
                "fastest_delivery_days": float(min(delivery_days)) if delivery_days else 0.0,
                "slowest_delivery_days": float(max(delivery_days)) if delivery_days else 0.0,
            })
            if pending_rows:
                oldest = min(pending_rows, key=lambda row: row.created or date.max)
                ages = [max(0, (date.today() - row.created).days) for row in pending_rows if row.created]
                values.update({
                    "oldest_pending_dn": _text(oldest.dn, "N/A"),
                    "oldest_pending_days": max(ages) if ages else 0,
                    "pending_average_days": round(sum(ages) / len(ages), 2) if ages else 0.0,
                    "critical_pending": sum(1 for age in ages if age > 7),
                    "overdue_pending": sum(1 for age in ages if age > 14),
                })

        monthly = session.query(
            func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("month"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.count(distinct(DeliveryReport.dn_no)).label("dns"),
        ).filter(condition, DeliveryReport.dn_create_date.isnot(None)).group_by("month").all()
        if monthly:
            month_map = {row.month: row for row in monthly}
            current = date.today().strftime("%Y-%m")
            previous_date = date.today().replace(day=1)
            previous_date = (previous_date.replace(year=previous_date.year - 1, month=12) if previous_date.month == 1 else previous_date.replace(month=previous_date.month - 1))
            previous = previous_date.strftime("%Y-%m")
            current_row, previous_row = month_map.get(current), month_map.get(previous)
            current_revenue = _number(current_row.revenue) if current_row else 0.0
            previous_revenue = _number(previous_row.revenue) if previous_row else 0.0
            growth = ((current_revenue - previous_revenue) * 100 / previous_revenue) if previous_revenue else (100.0 if current_revenue else 0.0)
            best = max(monthly, key=lambda row: _number(row.revenue))
            worst = min(monthly, key=lambda row: _number(row.revenue))
            values.update({
                "current_month_revenue": round(current_revenue, 2), "previous_month_revenue": round(previous_revenue, 2),
                "monthly_growth": round(growth, 2), "current_month_units": int(current_row.units or 0) if current_row else 0,
                "previous_month_units": int(previous_row.units or 0) if previous_row else 0,
                "current_month_dn": int(current_row.dns or 0) if current_row else 0,
                "previous_month_dn": int(previous_row.dns or 0) if previous_row else 0,
                "best_month": _text(best.month), "worst_month": _text(worst.month), "busiest_month": _text(best.month),
                "revenue_growth_pct": round(growth, 2),
            })

        def top_value(column: Any) -> str:
            row = session.query(column.label("value"), func.sum(DeliveryReport.dn_amount).label("revenue")).filter(condition, column.isnot(None)).group_by(column).order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
            return _text(row.value) if row else "Unknown"

        division_rows = session.query(
            DeliveryReport.division.label("value"),
            func.sum(DeliveryReport.dn_amount).label("revenue"),
        ).filter(condition, DeliveryReport.division.isnot(None)).group_by(DeliveryReport.division).order_by(func.sum(DeliveryReport.dn_amount).desc()).all()
        values["top_division"] = _text(division_rows[0].value) if division_rows else "Unknown"
        values["top_product"] = top_value(DeliveryReport.customer_model)
        values["top_model"] = values["top_product"]
        values["top_material"] = top_value(DeliveryReport.material_no)
        values["strongest_product_category"] = values["top_division"]
        values["weakest_product_category"] = _text(division_rows[-1].value) if division_rows else "Unknown"

        warehouse_units = session.query(func.coalesce(func.sum(DeliveryReport.dn_qty), 0)).filter(DeliveryReport.warehouse == item.warehouse).scalar() or 0
        values["warehouse_utilization"] = _percent(item.total_units, warehouse_units)

        ranking_rows = session.query(
            DeliveryReport.customer_name.label("name"), DeliveryReport.dealer_code.label("code"),
            func.max(DeliveryReport.ship_to_city).label("city"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("delivery"),
            func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("pod"),
            func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending"),
        ).filter(DeliveryReport.customer_name.isnot(None)).group_by(DeliveryReport.customer_name, DeliveryReport.dealer_code).all()
        target = next((row for row in ranking_rows if _text(row.code, "") == item.dealer_code or _text(row.name, "") == item.dealer_name), None)
        if target:
            def rank_for(key: Any, reverse: bool = True) -> int:
                ordered = sorted(ranking_rows, key=key, reverse=reverse)
                return next((index for index, row in enumerate(ordered, 1) if row is target), len(ordered))
            values["revenue_rank"] = rank_for(lambda row: _number(row.revenue))
            values["unit_rank"] = rank_for(lambda row: _number(row.units))
            values["dn_rank"] = rank_for(lambda row: int(row.dns or 0))
            values["delivery_rank"] = rank_for(lambda row: self._days(row.delivery) if row.delivery is not None else float("inf"), False)
            values["pod_rank"] = rank_for(lambda row: _percent(row.pod, row.dns))
            values["pending_rank"] = rank_for(lambda row: _percent(row.pending, row.dns), False)
            composite = sorted(ranking_rows, key=lambda row: (_number(row.revenue), _percent(row.pod, row.dns)), reverse=True)
            values["national_rank"] = next((index for index, row in enumerate(composite, 1) if row is target), len(composite))
            regional = [row for row in ranking_rows if _text(row.city, "").lower() == item.city.lower()]
            regional.sort(key=lambda row: _number(row.revenue), reverse=True)
            values["regional_rank"] = next((index for index, row in enumerate(regional, 1) if row is target), len(regional) or 1)

        for key, value in values.items():
            setattr(item, key, value)
        self._apply_business_health(item)
        self._extended_cache[cache_key] = values
        item.insights, item.recommendations = self._business_insights(item)

    @staticmethod
    def _apply_business_health(item: DealerDashboard) -> None:
        score = (
            item.delivery_success_pct * 0.35
            + item.pgi_success_pct * 0.20
            + item.pod_success_pct * 0.25
            + max(0.0, 100.0 - item.pending_pct) * 0.20
        )
        item.business_score = round(max(0.0, min(100.0, score)), 1)
        item.overall_status = "Excellent" if score >= 90 else ("Good" if score >= 75 else ("Watch" if score >= 60 else "Needs Attention"))
        trend = "growing" if item.monthly_growth >= 0 else "declining"
        action = "maintain current controls" if item.overall_status in {"Excellent", "Good"} else "prioritize pending DN and POD closure"
        item.executive_summary = (
            f"{item.dealer_name} is {trend} with a {item.business_score:.1f}/100 business score. "
            f"Delivery success is {item.delivery_success_pct:.1f}% and {item.pending_dn} DNs remain pending; {action}."
        )

    @staticmethod
    def _business_insights(item: DealerDashboard) -> tuple[list[str], list[str]]:
        trend = "increasing" if item.monthly_growth >= 0 else "decreasing"
        insights = [
            f"Dealer revenue is {trend} ({item.monthly_growth:+.1f}% month over month).",
            f"Dealer has {item.pending_dn:,} pending DNs and {item.pending_units:,} pending units.",
            f"Pending revenue is PKR {item.pending_revenue:,.2f}.",
            f"Delivery success is {item.delivery_success_pct:.1f}% with average delivery of {item.average_delivery_days:.1f} days.",
            f"POD completion is {item.pod_success_pct:.1f}% and PGI completion is {item.pgi_success_pct:.1f}%.",
            f"Warehouse {item.warehouse} serves this dealer and contributes {item.warehouse_utilization:.1f}% of its unit throughput.",
            f"{item.top_model} is the leading model; top material is {item.top_material}.",
            f"Best revenue month is {item.best_month}; national rank is {item.national_rank or 'N/A'}.",
        ]
        if item.oldest_pending_days:
            insights.append(f"Oldest pending DN {item.oldest_pending_dn} is {item.oldest_pending_days} days old.")
        recommendations = []
        if item.overdue_pending:
            recommendations.append(f"Escalate {item.overdue_pending} DNs pending for more than 14 days.")
        if item.pod_success_pct < 90:
            recommendations.append("Prioritize POD collection and closure.")
        if item.pgi_pending_dn:
            recommendations.append(f"Review {item.pgi_pending_dn} DNs awaiting PGI.")
        if not recommendations:
            recommendations.append("Maintain the current delivery and POD control process.")
        return insights, recommendations

    @staticmethod
    def _basic_insights(item: DealerDashboard) -> list[str]:
        insights = []
        if item.delivery_success_pct >= 95:
            insights.append("Dealer has excellent delivery performance.")
        if item.pending_pct >= 25:
            insights.append("Dealer has high pending deliveries requiring attention.")
        if item.pod_success_pct < 80:
            insights.append("Dealer has low POD completion.")
        if item.average_delivery_days and item.average_delivery_days <= 2:
            insights.append("Dealer receives deliveries quickly.")
        if item.distance.distance_km is not None:
            insights.append(f"Dealer is {item.distance.distance_km:,.1f} KM from the primary warehouse.")
        return insights

    def _enrich_profile(self, session: Session, item: DealerDashboard) -> None:
        condition = self._dealer_filter(item.dealer_code if item.dealer_code != "Unknown" else item.dealer_name)
        month = session.query(func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("period"), func.sum(DeliveryReport.dn_amount).label("revenue")).filter(condition, DeliveryReport.dn_create_date.isnot(None)).group_by("period").order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
        products = session.query(DeliveryReport.division.label("category"), func.sum(DeliveryReport.dn_amount).label("revenue")).filter(condition, DeliveryReport.division.isnot(None)).group_by(DeliveryReport.division).order_by(func.sum(DeliveryReport.dn_amount).desc()).all()
        item.busiest_month = _text(month.period) if month else "Unknown"
        if products:
            item.strongest_product_category = _text(products[0].category)
            item.weakest_product_category = _text(products[-1].category)
            item.insights.append(f"Strongest product category is {item.strongest_product_category}.")
        if item.busiest_month != "Unknown":
            item.insights.append(f"Dealer's busiest month is {item.busiest_month}.")

    def get_dealer_dashboard(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        identifier = dealer_name or kwargs.get("dealer") or kwargs.get("dealer_code") or kwargs.get("customer_code") or ""
        if not identifier:
            return {"success": False, "error_code": "DEALER_REQUIRED", "message": "Please provide a dealer name or code."}
        try:
            with self._session() as session:
                search = self._resolve_dealer(session, str(identifier))
                if search.exception:
                    return {"success": False, "error_code": "SEARCH_ERROR", "message": "Dealer search is temporarily unavailable.", "error": search.exception}
                if not search.dealer_found:
                    return self._suggestion_response(search)
                resolved_identity = search.dealer_code or search.customer_code or search.dealer_found
                dashboard_key = str(resolved_identity).lower()
                cached_dashboard = self._dashboard_cache.get(dashboard_key)
                if cached_dashboard:
                    return cached_dashboard
                rows = self._aggregate_query(session, resolved_identity)
                if not rows:
                    return self._suggestion_response(search)
                data = self._row_to_dashboard(rows[0])
                try:
                    self._apply_extended_analytics(session, data)
                except Exception:
                    logger.exception("Extended dealer analytics failed; returning core dashboard")
                    data.insights, data.recommendations = self._business_insights(data)
                try:
                    formatted = data.to_whatsapp_message()
                except Exception:
                    logger.exception("Dealer WhatsApp formatting failed")
                    formatted = f"Dealer Dashboard\nDealer: {data.dealer_name}\nRevenue: {data.total_revenue:,.2f}\nUnits: {data.total_units:,}\nDN: {data.total_dn:,}"
                response = {"success": True, "data": data, "dashboard": data, "search": search, "whatsapp_message": formatted, "formatted_response": formatted, "message": formatted, "response": formatted}
                self._dashboard_cache[dashboard_key] = response
                return response
        except Exception as error:
            logger.exception("Dealer dashboard query failed")
            return {"success": False, "error_code": "DATABASE_UNAVAILABLE", "message": "Dealer database is currently unavailable.", "error": str(error)}

    def diagnose_dealer_search(self, message: str = "", **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._session() as session:
                result = self._resolve_dealer(session, message or kwargs.get("dealer_name") or kwargs.get("dealer") or "")
                rows = len(self._aggregate_query(session, result.dealer_code or result.customer_code or result.dealer_found)) if result.dealer_found else 0
            output = asdict(result)
            output.update({"rows_returned": rows, "distance_calculated": False, "distance_source": "Unknown", "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)})
            return {"success": result.exception is None, "diagnostic": output}
        except Exception as error:
            logger.exception("Dealer diagnostics failed")
            return {"success": False, "diagnostic": {"original_message": message, "any_exception": str(error), "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)}}

    def get_dealer_profile(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        try:
            result = self.get_dealer_dashboard(dealer_name, **kwargs)
            if not result.get("success"):
                return result
            with self._session() as session:
                self._enrich_profile(session, result["data"])
            result["profile"] = result["data"]
            result["whatsapp_message"] = result["data"].to_whatsapp_message()
            result["message"] = result["whatsapp_message"]
            result["response"] = result["whatsapp_message"]
            return result
        except Exception as error:
            logger.exception("Dealer profile failed")
            return {"success": False, "error_code": "PROFILE_ERROR", "message": "Dealer profile is temporarily unavailable.", "error": str(error)}

    def compare_dealers(self, dealer_names: Any = None, dealer_two: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
        try:
            values = dealer_names or kwargs.get("dealers") or kwargs.get("dealer1") or []
            if isinstance(values, str):
                values = [values]
            values = list(values)
            second = dealer_two or kwargs.get("dealer2")
            if second:
                values.append(second)
            values = list(dict.fromkeys(str(value) for value in values if value))
            if len(values) < 2:
                return {"success": False, "error_code": "TWO_DEALERS_REQUIRED", "message": "Please provide at least two dealers."}
            dashboards = []
            for value in values[:10]:
                result = self.get_dealer_dashboard(value)
                if result.get("success"):
                    dashboards.append(result["data"])
            if len(dashboards) < 2:
                return {"success": False, "error_code": "DEALERS_NOT_FOUND", "message": "At least two matching dealers are required."}
            comparison = DealerComparison(
                dashboards, max(dashboards, key=lambda x: x.total_revenue).dealer_name,
                max(dashboards, key=lambda x: x.total_units).dealer_name, max(dashboards, key=lambda x: x.total_dn).dealer_name,
                min(dashboards, key=lambda x: x.average_delivery_days or float("inf")).dealer_name,
                [f"{max(dashboards, key=lambda x: x.total_revenue).dealer_name} leads revenue.", f"{min(dashboards, key=lambda x: x.pending_pct).dealer_name} has the lowest pending rate."],
            )
            return {"success": True, "data": comparison, "comparison": comparison}
        except Exception as error:
            logger.exception("Dealer comparison failed")
            return {"success": False, "error_code": "COMPARISON_ERROR", "message": "Dealer comparison is temporarily unavailable.", "error": str(error)}

    def _rank(self, sort_by: str, limit: int, bottom: bool) -> dict[str, Any]:
        try:
            cache_key = f"{sort_by.lower()}|{int(limit)}|{int(bottom)}"
            cached = self._ranking_cache.get(cache_key)
            if cached:
                return cached
            with self._session() as session:
                items = [self._row_to_dashboard(row, include_distance=False) for row in self._aggregate_query(session)]
            key_name = self.SORT_ALIASES.get(sort_by.lower().replace(" ", "_"), "total_revenue")
            naturally_low = key_name in {"average_delivery_days", "pending_pct", "total_revenue", "total_units", "pod_success_pct"}
            reverse = (not bottom and not (key_name in {"average_delivery_days", "pending_pct"})) or (bottom and key_name in {"average_delivery_days", "pending_pct"})
            items.sort(key=lambda value: getattr(value, key_name, 0) if getattr(value, key_name, None) is not None else 0, reverse=reverse)
            ranking = DealerRanking(sort_by, "bottom" if bottom else "top", items[: max(1, min(int(limit), 100))])
            response = {"success": True, "data": ranking, "dealers": ranking.dealers, "count": len(ranking.dealers)}
            self._ranking_cache[cache_key] = response
            return response
        except (SQLAlchemyError, ValueError) as error:
            logger.exception("Dealer ranking failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is currently unavailable.", "error": str(error)}

    def get_top_dealers(self, limit: int = 10, sort_by: str = "revenue", **kwargs: Any) -> dict[str, Any]:
        try:
            return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), False)
        except Exception as error:
            logger.exception("Top dealer request failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is temporarily unavailable.", "error": str(error)}

    def get_bottom_dealers(self, limit: int = 10, sort_by: str = "highest_pending", **kwargs: Any) -> dict[str, Any]:
        try:
            return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), True)
        except Exception as error:
            logger.exception("Bottom dealer request failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is temporarily unavailable.", "error": str(error)}

    def health_check(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._session() as session:
                rows = session.query(func.count(DeliveryReport.id)).scalar() or 0
            return {"healthy": True, "service": self._service_name, "version": self._version, "database": "connected", "records": int(rows), "latency_ms": round((time.perf_counter() - started) * 1000, 2), "timestamp": datetime.utcnow().isoformat()}
        except Exception as error:
            logger.exception("Dealer analytics health check failed")
            return {"healthy": False, "service": self._service_name, "version": self._version, "database": "disconnected", "error": str(error), "timestamp": datetime.utcnow().isoformat()}

    def validation_query(self) -> dict[str, Any]:
        try:
            with self._session() as session:
                records = session.query(func.count(distinct(func.coalesce(DeliveryReport.dealer_code, DeliveryReport.customer_code, DeliveryReport.customer_name)))).scalar() or 0
            return {"success": True, "records": int(records), "error": None}
        except Exception as error:
            return {"success": False, "records": 0, "error": str(error)}

    def get_service_metadata(self) -> dict[str, Any]:
        return {"service_name": self._service_name, "version": self._version, "status": "DEGRADED" if self._initialization_errors else "READY", "source": "PostgreSQL DeliveryReport", "distance_provider": "OpenRouteService" if self._distance and self._distance._ors else "geopy great-circle", "startup_time": self._startup_time, "initialization_errors": self._initialization_errors}


_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()


def get_dealer_analytics_service() -> DealerAnalyticsService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                try:
                    _service = DealerAnalyticsService()
                except Exception:
                    logger.exception("DealerAnalyticsService initialization failed")
                    _service = DealerAnalyticsService.__new__(DealerAnalyticsService)
                    _service._service_name = "dealer_analytics"
                    _service._version = "4.1.0-degraded"
                    _service._startup_time = datetime.utcnow().isoformat()
                    _service._initialization_errors = ["Service initialized in emergency degraded mode"]
                    _service._coordinates = CityCoordinateService()
                    _service._distance = None
                    _service._dealer_cache = TTLCache(maxsize=2048, ttl=CACHE_TTL)
                    _service._candidate_cache = TTLCache(maxsize=1, ttl=900)
                    _service._extended_cache = TTLCache(maxsize=2048, ttl=900)
                    _service._dashboard_cache = TTLCache(maxsize=2048, ttl=300)
                    _service._ranking_cache = TTLCache(maxsize=64, ttl=300)
                    _service._search_lock = threading.RLock()
                    _service._last_diagnostic = {}
    return _service


__all__ = ["DealerAnalyticsService", "DealerDashboard", "DealerComparison", "DealerRanking", "DealerSearchResult", "DistanceAnalytics", "CityCoordinateService", "get_dealer_analytics_service"]
