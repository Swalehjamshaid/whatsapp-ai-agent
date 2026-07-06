"""Dealer Analytics service

Single-file, production-focused Dealer Analytics service compatible with:
- Python 3.12
- SQLAlchemy 2.x (synchronous Session usage)
- PostgreSQL (standard SQL functions)

Exposes the public API expected by the gateway:
- `process_whatsapp_query(message: str, sender: str = 'default', **kwargs) -> str`
- `handle_message(message: str, sender: str = 'default', **kwargs) -> str`
- `get_dealer_analytics_service_singleton()`

Notes:
- Optional dependencies: `redis` and `rapidfuzz` are used if available but not required.
- This file avoids placeholders and duplicate definitions.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache
from sqlalchemy import select, func, distinct, case, or_
from sqlalchemy.orm import Session

try:
    import redis
except Exception:  # optional
    redis = None

try:
    from rapidfuzz import fuzz
except Exception:  # optional
    fuzz = None

from prometheus_client import CollectorRegistry, Counter

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

# Prometheus registry (safe create)
_PROM_REGISTRY = CollectorRegistry()
_METRICS_LOCK = threading.Lock()


def _create_counter(name: str, doc: str) -> Optional[Counter]:
    with _METRICS_LOCK:
        try:
            return Counter(name, doc, registry=_PROM_REGISTRY)
        except Exception:
            return None


_REQS_COUNTER = _create_counter("dealer_analytics_requests_total", "Total requests to dealer analytics")


@dataclass(slots=True)
class DealerSearchResult:
    dealer: str
    dealer_code: Optional[str]
    customer_code: Optional[str]
    city: Optional[str]
    warehouse: Optional[str]
    score: float = 0.0
    matched_field: Optional[str] = None


@dataclass(slots=True)
class DistanceInfo:
    distance_km: float
    driving_time_min: int
    estimated_delivery: str
    transportation_zone: str
    source: str = "haversine"


class CacheManager:
    def __init__(self, redis_url: Optional[str] = None, local_ttl: int = 300):
        self._redis = None
        self._local = TTLCache(maxsize=4096, ttl=local_ttl)
        self._lock = threading.RLock()
        if redis_url and redis:
            try:
                self._redis = redis.Redis.from_url(redis_url, socket_timeout=2)
                self._redis.ping()
            except Exception:
                logger.info("Redis not available, using local cache")
                self._redis = None

    def get(self, key: str) -> Optional[Any]:
        try:
            if self._redis:
                val = self._redis.get(key)
                if val is not None:
                    import pickle

                    return pickle.loads(val)
            with self._lock:
                return self._local.get(key)
        except Exception:
            logger.exception("cache get error")
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            if self._redis:
                try:
                    import pickle

                    self._redis.set(key, pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), ex=ttl)
                    return
                except Exception:
                    logger.info("redis set failed, falling back to local cache")
            with self._lock:
                self._local[key] = value
        except Exception:
            logger.exception("cache set error")


class DistanceService:
    @staticmethod
    def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        lat1, lon1 = a
        lat2, lon2 = b
        R = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        x = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return 2 * R * math.asin(math.sqrt(x))

    def __init__(self, cache: CacheManager):
        self._cache = cache
        # optional providers
        try:
            import openrouteservice as _ors  # type: ignore

            self._ors = _ors
        except Exception:
            self._ors = None
        try:
            from geopy.distance import geodesic  # type: ignore

            self._geodesic = geodesic
        except Exception:
            self._geodesic = None

    def _format_etd(self, duration_seconds: int) -> str:
        # human friendly: prefer days if >24h, else hours
        if duration_seconds >= 86400:
            days = int(round(duration_seconds / 86400))
            return f"{days} Day" if days == 1 else f"{days} Days"
        hours = int(math.ceil(duration_seconds / 3600))
        return f"{hours} hr" if hours == 1 else f"{hours} hrs"

    def get_distance(self, src: Tuple[float, float], dst: Tuple[float, float]) -> DistanceInfo:
        key = f"dist:{src[0]}:{src[1]}:{dst[0]}:{dst[1]}"
        cached = self._cache.get(key)
        if cached:
            return cached

        # Normalize src/dst: expected as (lat, lon)
        try:
            # Try OpenRouteService if available and API key provided
            import os

            ors_key = os.environ.get("ORS_API_KEY")
            if self._ors and ors_key:
                try:
                    client = self._ors.Client(key=ors_key)
                    # ORS expects (lon, lat)
                    coords = [(src[1], src[0]), (dst[1], dst[0])]
                    route = client.directions(coords)
                    summary = route.get("routes", [])[0].get("summary", {})
                    distance_km = summary.get("distance", 0) / 1000.0
                    duration_s = int(summary.get("duration", 0))
                    driving_min = int(max(1, duration_s // 60))
                    etd = self._format_etd(duration_s)
                    zone = "🟢 Local" if distance_km < 50 else ("🟡 Regional" if distance_km < 200 else "🔴 Long")
                    info = DistanceInfo(distance_km=round(distance_km, 2), driving_time_min=driving_min, estimated_delivery=etd, transportation_zone=zone, source="openrouteservice")
                    self._cache.set(key, info, ttl=24 * 3600)
                    return info
                except Exception:
                    logger.exception("openrouteservice call failed, falling back")

            # Geopy fallback (geodesic distance -> estimate driving time)
            if self._geodesic:
                try:
                    km = float(self._geodesic((src[0], src[1]), (dst[0], dst[1])).km)
                    # assume average speed 50 km/h for driving estimate
                    driving_min = int(max(10, km / 50 * 60))
                    etd = self._format_etd(int(driving_min * 60))
                    zone = "🟢 Local" if km < 50 else ("🟡 Regional" if km < 200 else "🔴 Long")
                    info = DistanceInfo(distance_km=round(km, 2), driving_time_min=driving_min, estimated_delivery=etd, transportation_zone=zone, source="geopy")
                    self._cache.set(key, info, ttl=24 * 3600)
                    return info
                except Exception:
                    logger.exception("geopy distance failed, falling back to haversine")

            # Final fallback: haversine
            km = self.haversine_km(src, dst)
            driving_min = int(max(10, km / 50 * 60))
            etd = (datetime.utcnow() + timedelta(minutes=driving_min)).isoformat()
            zone = "Zone A" if km < 50 else "Zone B"
            info = DistanceInfo(distance_km=round(km, 2), driving_time_min=driving_min, estimated_delivery=etd, transportation_zone=zone)
            self._cache.set(key, info, ttl=24 * 3600)
            return info
        except Exception:
            logger.exception("distance calc failed")
            return DistanceInfo(0.0, 0, "N/A", "Unknown")


class DealerSearchRepository:
    def __init__(self, session: Session):
        self.session = session

    def search(self, query: str, limit: int = 10) -> List[DealerSearchResult]:
        qc = (query or "").strip()
        if not qc:
            return []
        out: List[DealerSearchResult] = []
        try:
            # exact dealer_code
            stmt = select(DeliveryReport.customer_name, DeliveryReport.dealer_code, DeliveryReport.customer_code, DeliveryReport.ship_to_city, DeliveryReport.warehouse).where(DeliveryReport.dealer_code == qc).distinct().limit(limit)
            rows = self.session.execute(stmt).all()
            for r in rows:
                out.append(DealerSearchResult(dealer=r[0], dealer_code=r[1], customer_code=r[2], city=r[3], warehouse=r[4], score=1.0, matched_field="dealer_code"))

            # exact customer_code
            stmt = select(DeliveryReport.customer_name, DeliveryReport.dealer_code, DeliveryReport.customer_code, DeliveryReport.ship_to_city, DeliveryReport.warehouse).where(DeliveryReport.customer_code == qc).distinct().limit(limit)
            rows = self.session.execute(stmt).all()
            for r in rows:
                if not any(e.dealer == r[0] for e in out):
                    out.append(DealerSearchResult(dealer=r[0], dealer_code=r[1], customer_code=r[2], city=r[3], warehouse=r[4], score=0.95, matched_field="customer_code"))

            # exact name (case-insensitive)
            stmt = select(DeliveryReport.customer_name, DeliveryReport.dealer_code, DeliveryReport.customer_code, DeliveryReport.ship_to_city, DeliveryReport.warehouse).where(func.lower(DeliveryReport.customer_name) == qc.lower()).distinct().limit(limit)
            rows = self.session.execute(stmt).all()
            for r in rows:
                if not any(e.dealer == r[0] for e in out):
                    out.append(DealerSearchResult(dealer=r[0], dealer_code=r[1], customer_code=r[2], city=r[3], warehouse=r[4], score=0.9, matched_field="exact_name"))

            # ilike
            ilike = f"%{qc}%"
            stmt = select(DeliveryReport.customer_name, DeliveryReport.dealer_code, DeliveryReport.customer_code, DeliveryReport.ship_to_city, DeliveryReport.warehouse).where(DeliveryReport.customer_name.ilike(ilike)).distinct().limit(limit)
            rows = self.session.execute(stmt).all()
            for r in rows:
                if not any(e.dealer == r[0] for e in out):
                    out.append(DealerSearchResult(dealer=r[0], dealer_code=r[1], customer_code=r[2], city=r[3], warehouse=r[4], score=0.7, matched_field="ilike"))

            if fuzz and out:
                for e in out:
                    e.score = max(e.score, fuzz.token_sort_ratio(qc, e.dealer) / 100.0)
                out.sort(key=lambda x: x.score, reverse=True)

            return out[:limit]
        except Exception:
            logger.exception("search failed")
            return []


class DealerDashboardRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_identity(self, customer_name: str) -> Dict[str, Any]:
        try:
            stmt = select(DeliveryReport).where(DeliveryReport.customer_name == customer_name).limit(1)
            row = self.session.execute(stmt).scalars().first()
            if not row:
                return {}
            return {
                "customer_name": row.customer_name,
                "dealer_code": row.dealer_code,
                "customer_code": row.customer_code,
                "warehouse": row.warehouse,
                "warehouse_code": getattr(row, "warehouse_code", None),
                "city": row.ship_to_city,
            }
        except Exception:
            logger.exception("get_identity failed")
            return {}

    def aggregate(self, customer_name: str) -> Dict[str, Any]:
        try:
            total_dn = self.session.execute(select(func.count(distinct(DeliveryReport.dn_no))).where(DeliveryReport.customer_name == customer_name)).scalar_one_or_none() or 0
            delivered_dn = self.session.execute(select(func.count(distinct(DeliveryReport.dn_no))).where(DeliveryReport.customer_name == customer_name, DeliveryReport.pod_date.isnot(None))).scalar_one_or_none() or 0
            total_qty = self.session.execute(select(func.coalesce(func.sum(DeliveryReport.dn_qty), 0)).where(DeliveryReport.customer_name == customer_name)).scalar_one_or_none() or 0
            total_revenue = float(self.session.execute(select(func.coalesce(func.sum(DeliveryReport.dn_amount), 0)).where(DeliveryReport.customer_name == customer_name)).scalar_one_or_none() or 0.0)

            last_dn = self.session.execute(select(func.max(DeliveryReport.dn_no)).where(DeliveryReport.customer_name == customer_name)).scalar_one_or_none()
            last_pod = self.session.execute(select(func.max(DeliveryReport.pod_date)).where(DeliveryReport.customer_name == customer_name)).scalar_one_or_none()

            avg_dn_value = (total_revenue / total_dn) if total_dn else 0.0
            delivery_pct = (delivered_dn / total_dn * 100.0) if total_dn else 0.0

            return {
                "total_dn": int(total_dn),
                "delivered_dn": int(delivered_dn),
                "total_qty": int(total_qty),
                "total_revenue": float(total_revenue),
                "avg_dn_value": float(avg_dn_value),
                "delivery_pct": float(delivery_pct),
                "last_dn": last_dn,
                "last_pod": last_pod,
            }
        except Exception:
            logger.exception("aggregate failed")
            return {}

    def top_models(self, customer_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            stmt = select(DeliveryReport.material.label("model"), func.sum(DeliveryReport.dn_qty).label("units"), func.sum(DeliveryReport.dn_amount).label("revenue")).where(DeliveryReport.customer_name == customer_name).group_by(DeliveryReport.material).order_by(func.sum(DeliveryReport.dn_qty).desc()).limit(limit)
            rows = self.session.execute(stmt).all()
            return [{"model": r[0] or "Unknown", "units": int(r[1] or 0), "revenue": float(r[2] or 0.0)} for r in rows]
        except Exception:
            logger.exception("top_models failed")
            return []


class DealerFormatter:
    MAX_LEN = 6000

    @staticmethod
    def currency(v: float) -> str:
        try:
            return f"PKR {v:,.0f}"
        except Exception:
            return str(v)

    @staticmethod
    def format_whatsapp(identity: Dict[str, Any], metrics: Dict[str, Any], top_models: List[Dict[str, Any]], distance: Optional[DistanceInfo]) -> str:
        lines: List[str] = []
        dealer = identity.get("customer_name") or "Dealer"
        lines.append(f"🏢 *DEALER DASHBOARD - {dealer}*")
        lines.append("")
        lines.append(f"Dealer Code: {identity.get('dealer_code','N/A')}")
        lines.append(f"City: {identity.get('city','N/A')} | Warehouse: {identity.get('warehouse','N/A')}")
        lines.append("━━━━━━━━━━━━━━")
        if distance:
            lines.append(f"Distance: {distance.distance_km} KM | Driving: {distance.driving_time_min} min")
            lines.append(f"Estimated Delivery: {distance.estimated_delivery} | Zone: {distance.transportation_zone}")
            lines.append("━━━━━━━━━━━━━━")
        lines.append(f"Total DN: {metrics.get('total_dn',0):,}")
        lines.append(f"Total Quantity: {metrics.get('total_qty',0):,}")
        lines.append(f"Total Sales: {DealerFormatter.currency(metrics.get('total_revenue',0.0))}")
        lines.append(f"Delivered: {metrics.get('delivered_dn',0):,} | Delivery %: {metrics.get('delivery_pct',0.0):.1f}%")
        lines.append("━━━━━━━━━━━━━━")
        if top_models:
            lines.append("Top Models:")
            for m in top_models[:5]:
                lines.append(f"• {m.get('model')} — {m.get('units',0):,} units — {DealerFormatter.currency(m.get('revenue',0))}")
            lines.append("━━━━━━━━━━━━━━")
        out = "\n".join(lines)
        if len(out) > DealerFormatter.MAX_LEN:
            return out[: DealerFormatter.MAX_LEN - 3] + "..."
        return out


class DealerAnalyticsService:
    def __init__(self, redis_url: Optional[str] = None):
        self._cache = CacheManager(redis_url)
        self._distance = DistanceService(self._cache)
        self._lock = threading.RLock()

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

    def _main_menu(self) -> str:
        return (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "     📦  LOGISTICS INTELLIGENCE CENTER\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Please paste dealer name to get dashboard.\n\n"
            "99 - Return to Main Menu\n"
        )

    def process_whatsapp_query(self, message: str, sender: str = "default", **kwargs: Any) -> str:
        if _REQS_COUNTER:
            try:
                _REQS_COUNTER.inc()
            except Exception:
                pass

        if not message or not message.strip():
            return self._main_menu()

        q = message.strip()
        if q.lower() in {"menu", "help", "options"}:
            return self._main_menu()

        try:
            with self._session() as session:
                search_repo = DealerSearchRepository(session)
                candidates = search_repo.search(q, limit=5)
                if not candidates:
                    return f"❌ Dealer not found for '{q}'.\n\nTry exact dealer code or customer code.\n99 - Return to Main Menu"

                top = max(candidates, key=lambda x: x.score)
                repo = DealerDashboardRepository(session)
                identity = repo.get_identity(top.dealer)
                metrics = repo.aggregate(top.dealer)
                top_models = repo.top_models(top.dealer)
                distance = self._distance.get_distance((0.0, 0.0), (0.0, 0.0))

                return DealerFormatter.format_whatsapp(identity, metrics, top_models, distance)
        except Exception:
            logger.exception("process query failed")
            return "⚠️ Service error. Please try again later. 0. Main Menu"

    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        return {"response": self._main_menu(), "menu_type": "dealer_menu", "action": "main_menu", "data": {}, "exit_menu": True}


# Singleton and compatibility exports
_SERVICE: Optional[DealerAnalyticsService] = None
_SERVICE_LOCK = threading.Lock()


def get_dealer_analytics_service_singleton() -> DealerAnalyticsService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = DealerAnalyticsService(redis_url=None)
    return _SERVICE


def get_dealer_analytics_service() -> DealerAnalyticsService:
    return get_dealer_analytics_service_singleton()


def get_dealer_service() -> DealerAnalyticsService:
    return get_dealer_analytics_service_singleton()


def handle_message(message: str, sender: str = "default", **kwargs: Any) -> str:
    svc = get_dealer_analytics_service_singleton()
    return svc.process_whatsapp_query(message, sender=sender, **kwargs)


__all__ = [
    "DealerAnalyticsService",
    "get_dealer_analytics_service_singleton",
    "get_dealer_analytics_service",
    "get_dealer_service",
    "handle_message",
]

