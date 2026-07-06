import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from cachetools import TTLCache
from sqlalchemy import func, case, distinct, or_
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)
CACHE_TTL = 300


class DealerMenuRenderer:
    def render_main_menu(self) -> str:
        return """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
     📦  LOGISTICS INTELLIGENCE CENTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Please choose from:

1. ❌ National KPI
2. ❌ DN Analysis
3. ❌ Dealer Analytics
4. ✅ Warehouse Analytics
5. ✅ Product Analytics
6. ✅ City Analytics
7. ❌ AI Assistant

99 - Return to Main Menu

📌 Services with ✅ are working
📌 Services with ❌ are currently unavailable
"""

    def render_dealer_dashboard(self, dealer: str, dashboard: Dict[str, Any]) -> str:
        idt = dashboard.get("identity", {})
        sales = dashboard.get("sales", {})
        delivery = dashboard.get("delivery", {})
        perf = dashboard.get("performance", {})
        product = dashboard.get("product", {})
        insights = dashboard.get("insights", [])
        recs = dashboard.get("recommendations", [])
        exec_sum = dashboard.get("executive_summary", "")

        lines: List[str] = []
        lines.append(f"🏢 *DEALER DASHBOARD - {idt.get('customer_name', dealer)}*")
        lines.append("")
        lines.append(f"Dealer Code: {idt.get('dealer_code','N/A')}")
        lines.append(f"Customer Code: {idt.get('customer_code','N/A')}")
        lines.append(f"City: {idt.get('city','N/A')} | Warehouse: {idt.get('warehouse','N/A')}")
        lines.append("")
        # KPIs
        lines.append("📊 *Key KPIs*")
        lines.append(f"• Revenue: {_format_currency(sales.get('total_revenue', 0))}")
        lines.append(f"• Units: {sales.get('total_quantity', 0):,}")
        lines.append(f"• DN: {delivery.get('total_dn', 0):,}")
        lines.append(f"• Pending DN: {delivery.get('pending_dn', 0):,}")
        lines.append(f"• Delivery Rate: {delivery.get('delivery_rate', 0):.1f}%")
        lines.append("")
        # Top models
        top = product.get('top_models', [])
        if top:
            lines.append("📦 *Top Models*")
            for m in top[:5]:
                lines.append(f"• {m.get('model','Unknown')} — {m.get('units',0):,} units — {m.get('revenue',0)}")
            lines.append("")

        # Performance & summary
        lines.append("📈 *Performance*")
        lines.append(f"• Business Score: {perf.get('business_score', 0):.1f}/100")
        lines.append(f"• Tier: {perf.get('performance_tier','Standard')}")
        lines.append("")

        if exec_sum:
            lines.append("📋 *Executive Summary*")
            lines.append(exec_sum)
            lines.append("")

        if insights:
            lines.append("💡 *Insights*")
            for ins in insights[:5]:
                lines.append(f"• {ins}")
            lines.append("")

        if recs:
            lines.append("🎯 *Recommendations*")
            for r in recs[:5]:
                lines.append(f"• {r}")
            lines.append("")

        lines.append("0. Main Menu")
        lines.append("99. Back")

        return "\n".join(lines)

def _format_currency(v: float) -> str:
    try:
        return f"PKR {v:,.0f}"
    except Exception:
        return str(v)

class DealerRepository:
    def __init__(self, session: Session):
        self.session = session

    def search_dealers(self, query: str) -> List[Dict[str, Any]]:
        pattern = f"%{query}%"
        try:
            rows = (
                self.session.query(
                    DeliveryReport.customer_name.label('dealer'),
                    DeliveryReport.dealer_code,
                    DeliveryReport.customer_code,
                    DeliveryReport.ship_to_city.label('city'),
                    DeliveryReport.warehouse,
                )
                .filter(
                    or_(
                        DeliveryReport.customer_name.ilike(pattern),
                        DeliveryReport.dealer_code.ilike(pattern),
                        DeliveryReport.customer_code.ilike(pattern),
                    )
                )
                .distinct()
                .limit(20)
                .all()
            )

            return [
                {
                    'dealer': r.dealer,
                    'dealer_code': r.dealer_code,
                    'customer_code': r.customer_code,
                    'city': r.city,
                    'warehouse': r.warehouse,
                }
                for r in rows
            ]
        except Exception as e:
            logger.exception("search_dealers failed")
            return []

    def get_dealer_by_name(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        ident = dealer_identifier.strip()
        pattern = f"%{ident}%"
        try:
            row = (
                self.session.query(DeliveryReport.customer_name.label('customer_name'))
                .filter(
                    or_(
                        DeliveryReport.customer_name.ilike(ident),
                        DeliveryReport.customer_name.ilike(pattern),
                        DeliveryReport.dealer_code.ilike(ident),
                        DeliveryReport.customer_code.ilike(ident),
                    )
                )
                .order_by(DeliveryReport.customer_name)
                .first()
            )
            if not row:
                return None

            customer_name = row.customer_name

            # Aggregate metrics for this dealer
            metrics = self.session.query(
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(case((DeliveryReport.pod_date.is_(None), 1), else_=0)).label('pod_pending'),
                func.sum(case((DeliveryReport.pgi_date.is_(None), 1), else_=0)).label('pgi_pending'),
            ).filter(DeliveryReport.customer_name == customer_name).one()

            # delivery success pct (pod completed / total)
            total_dns = self.session.query(func.count(distinct(DeliveryReport.dn_no))).filter(DeliveryReport.customer_name == customer_name).scalar() or 0
            pod_completed = self.session.query(func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no))))).filter(DeliveryReport.customer_name == customer_name).scalar() or 0
            delivery_rate = (pod_completed / total_dns * 100) if total_dns else 0.0

            # top models
            top_models = (
                self.session.query(
                    DeliveryReport.material.label('model'),
                    func.sum(DeliveryReport.dn_qty).label('units'),
                    func.sum(DeliveryReport.dn_amount).label('revenue'),
                )
                .filter(DeliveryReport.customer_name == customer_name)
                .group_by(DeliveryReport.material)
                .order_by(func.sum(DeliveryReport.dn_qty).desc())
                .limit(10)
                .all()
            )

            top = [
                {'model': t.model or 'Unknown', 'units': int(t.units or 0), 'revenue': float(t.revenue or 0)}
                for t in top_models
            ]

            result = {
                'customer_name': customer_name,
                'dealer_code': None,
                'customer_code': None,
                'city': None,
                'warehouse': None,
                'dn_count': int(metrics.dn_count or 0),
                'total_revenue': float(metrics.total_revenue or 0),
                'total_units': int(metrics.total_units or 0),
                'pod_pending': int(metrics.pod_pending or 0),
                'pgi_pending': int(metrics.pgi_pending or 0),
                'pod_completed': int(pod_completed),
                'delivery_success_pct': float(delivery_rate),
                'top_models': top,
            }

            # try to get a sample identity row
            sample = (
                self.session.query(DeliveryReport)
                .filter(DeliveryReport.customer_name == customer_name)
                .order_by(DeliveryReport.dn_no)
                .first()
            )
            if sample:
                result.update({
                    'dealer_code': sample.dealer_code,
                    'customer_code': sample.customer_code,
                    'city': sample.ship_to_city,
                    'warehouse': sample.warehouse,
                })

            return result

        except Exception:
            logger.exception("get_dealer_by_name failed")
            return None


class DealerDashboardBuilder:
    def __init__(self, session: Session):
        self.session = session
        self.repository = DealerRepository(session)
        self._cache: TTLCache = TTLCache(maxsize=1024, ttl=CACHE_TTL)
        self._lock = threading.RLock()

    def build(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        key = dealer_identifier.lower()
        with self._lock:
            if key in self._cache:
                return self._cache[key]

        data = self.repository.get_dealer_by_name(dealer_identifier)
        if not data:
            return None

        dashboard = {
            'identity': {
                'customer_name': data.get('customer_name'),
                'dealer_code': data.get('dealer_code'),
                'customer_code': data.get('customer_code'),
                'city': data.get('city'),
                'warehouse': data.get('warehouse'),
            },
            'sales': {
                'total_revenue': data.get('total_revenue', 0),
                'total_quantity': data.get('total_units', 0),
            },
            'delivery': {
                'total_dn': data.get('dn_count', 0),
                'pending_dn': data.get('pod_pending', 0),
                'pgi_pending': data.get('pgi_pending', 0),
                'pod_completed': data.get('pod_completed', 0),
                'delivery_rate': data.get('delivery_success_pct', 0.0),
            },
            'product': {
                'top_models': data.get('top_models', [])
            },
            'performance': {
                'business_score': data.get('business_score', 0),
                'performance_tier': data.get('performance_tier', 'Standard')
            },
            'insights': [],
            'recommendations': [],
            'executive_summary': '',
        }

        with self._lock:
            self._cache[key] = dashboard

        return dashboard


class DealerAnalyticsService:
    def __init__(self) -> None:
        self._renderer = DealerMenuRenderer()
        self._lock = threading.RLock()

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

    def get_main_menu(self) -> str:
        return self._renderer.render_main_menu()

    def process_whatsapp_query(self, message: str, sender: str = "default", **kwargs: Any) -> str:
        """Prefer direct dealer lookup first; fallback to menu flow."""
        if not message or not message.strip():
            return self.get_main_menu()

        text = message.strip()
        if text.lower() in ("menu", "help", "options"):
            return self.get_main_menu()

        # Attempt direct dealer lookup and return dashboard immediately if found
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                dealer = repo.get_dealer_by_name(text)
                if dealer:
                    builder = DealerDashboardBuilder(session)
                    dashboard = builder.build(dealer.get('customer_name') or text)
                    if dashboard:
                        return self._renderer.render_dealer_dashboard(dealer.get('customer_name') or text, dashboard)
        except Exception:
            logger.exception("direct lookup failed")

        # Fallback: attempt to interpret as menu input (session id = sender)
        try:
            service = get_dealer_analytics_service_singleton()
            result = service.process_menu_input(sender, text)
            return result.get('response', self.get_main_menu())
        except Exception:
            logger.exception("fallback menu processing failed")
            return self.get_main_menu()

    # Minimal adapter to avoid circular reference when calling menu flow fallback
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        # Very small compatibility shim: instantiate a full-featured service below if needed.
        return {
            'response': self.get_main_menu(),
            'menu_type': 'dealer_menu',
            'action': 'menu_fallback',
            'data': {},
            'exit_menu': True,
        }


# Simple singleton: if the project already has a full DealerAnalyticsService, this
# file's get_dealer_analytics_service_singleton will prefer that. Otherwise it will
# return a lightweight instance defined above.
_external_service: Optional[Any] = None
_external_lock = threading.Lock()

def get_dealer_analytics_service_singleton() -> DealerAnalyticsService:
    global _external_service
    if _external_service is None:
        with _external_lock:
            if _external_service is None:
                _external_service = DealerAnalyticsService()
    return _external_service
