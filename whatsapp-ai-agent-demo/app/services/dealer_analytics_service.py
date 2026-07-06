# whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
import threading
from datetime import datetime

# Replace these imports with your real app.database / app.models in production
try:
    from app.database import SessionLocal  # type: ignore
except Exception:
    # minimal stub so file is importable during edit
    class SessionLocal:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): pass
        def __call__(self): return self

# --- Helpers -----------------------------------------------------------------
def _format_currency(value: Any) -> str:
    try:
        v = float(value)
        if abs(v) >= 1_000_000:
            return f"PKR {v/1_000_000:.2f} M"
        if abs(v) >= 1_000:
            return f"PKR {v/1_000:.2f} K"
        return f"PKR {v:,.2f}"
    except Exception:
        return str(value or "PKR 0")

# --- Stubs / Lightweight implementations (replace with your real repository) ---
class DealerRepository:
    def __init__(self, session: Any) -> None:
        self.session = session

    def search_dealers(self, query: str) -> List[Dict[str, Any]]:
        # Placeholder: production must query Postgres and return matching dealers
        return []

    def get_dealer_by_name(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        # Placeholder: production must aggregate and return full dealer data
        return None

class DealerDashboardBuilder:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.repository = DealerRepository(session)

    def build(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        data = self.repository.get_dealer_by_name(dealer_identifier)
        if not data:
            return None
        # If repository returns a pre-built dashboard dictionary, pass it through.
        # Otherwise map known fields into the dashboard shape expected by the renderer.
        if "identity" in data:
            return data
        # Minimal mapping fallback:
        return {
            "identity": {
                "customer_name": data.get("customer_name"),
                "dealer_code": data.get("dealer_code"),
                "customer_code": data.get("customer_code"),
                "warehouse": data.get("warehouse"),
                "city": data.get("ship_to_city") or data.get("city"),
                "sales_office": data.get("sales_office"),
                "sales_manager": data.get("sales_manager"),
            },
            "distance": data.get("distance", {}),
            "delivery": {
                "total_dn": data.get("dn_count"),
                "pending_dn": data.get("pending_dn"),
                "pgi_pending": data.get("pgi_pending_dn"),
                "pod_pending": data.get("pod_pending_dn"),
                "delivered_dn": data.get("pod_completed"),
                "delivery_rate": data.get("delivery_success_pct"),
                "avg_delivery_days": data.get("avg_delivery_days"),
            },
            "sales": {
                "total_revenue": data.get("total_revenue"),
                "total_quantity": data.get("total_units") or data.get("total_quantity"),
            },
            "product": {
                "top_models": data.get("top_models", []),
            },
            "performance": {
                "business_score": data.get("business_score", 0),
                "dealer_rating": data.get("dealer_rating", 0),
                "performance_tier": data.get("performance_tier", ""),
            },
            "dates": {
                "last_dn": data.get("last_dn"),
                "last_delivery_date": data.get("last_delivery_date"),
            },
            "insights": data.get("insights", []),
            "executive_summary": data.get("executive_summary", ""),
        }

# --- Renderer ---------------------------------------------------------------
class DealerMenuRenderer:
    def render_dealer_dashboard(self, dealer_name: str, dashboard: Dict[str, Any]) -> str:
        identity = dashboard.get("identity", {}) or {}
        delivery = dashboard.get("delivery", {}) or {}
        sales = dashboard.get("sales", {}) or {}
        distance = dashboard.get("distance", {}) or {}
        product = dashboard.get("product", {}) or {}
        performance = dashboard.get("performance", {}) or {}
        dates = dashboard.get("dates", {}) or {}
        exec_summary = dashboard.get("executive_summary") or dashboard.get("executive_summary", "") or ""

        def fmt_num(n):
            try:
                return f"{int(n):,}"
            except Exception:
                return str(n or "0")

        def fmt_float(n, dec=1):
            try:
                return f"{float(n):.{dec}f}"
            except Exception:
                return str(n or "0")

        def fmt_sales(val):
            try:
                v = float(val)
                if abs(v) >= 1_000_000:
                    return f"PKR {v/1_000_000:.2f} M"
                if abs(v) >= 1_000:
                    return f"PKR {v/1_000:.2f} K"
                return f"PKR {v:,.2f}"
            except Exception:
                return str(val or "PKR 0")

        dealer_display = identity.get("customer_name") or dealer_name or "Dealer"
        dealer_code = identity.get("dealer_code", "N/A")
        customer_code = identity.get("customer_code", "N/A")
        warehouse = identity.get("warehouse", "N/A")
        city = identity.get("city", "N/A")
        sales_office = identity.get("sales_office", "N/A")
        sales_manager = identity.get("sales_manager", "N/A")

        distance_km = distance.get("distance_km", "N/A")
        est_delivery = distance.get("estimated_delivery", "N/A")
        trans_zone = distance.get("transportation_zone", "N/A")

        total_dn = fmt_num(delivery.get("total_dn", 0))
        total_qty = fmt_num(sales.get("total_quantity", delivery.get("total_quantity", delivery.get("total_units", 0))))
        total_sales = fmt_sales(sales.get("total_revenue", 0))
        delivered = fmt_num(delivery.get("delivered_dn", delivery.get("pod_completed", 0)))
        pending = fmt_num(delivery.get("pending_dn", 0))
        pgi_pending = fmt_num(delivery.get("pgi_pending", 0))
        pod_pending = fmt_num(delivery.get("pod_pending", 0))
        delivery_rate = fmt_float(delivery.get("delivery_rate", delivery.get("delivery_success_pct", 0)), 1)
        avg_delivery_days = fmt_float(delivery.get("avg_delivery_days", 0), 1)

        top_models = product.get("top_models", []) or []
        if not top_models:
            top_models = ["N/A"]

        business_score = performance.get("business_score", 0)
        dealer_rating = int(performance.get("dealer_rating", 0)) if performance.get("dealer_rating") is not None else 0
        rating_stars = "⭐" * max(0, min(5, dealer_rating)) if dealer_rating else "N/A"
        perf_text = performance.get("performance_tier") or performance.get("performance", "")
        if not perf_text:
            if business_score >= 85:
                perf_text = "Excellent"
            elif business_score >= 70:
                perf_text = "Good"
            else:
                perf_text = "Standard"

        last_dn = dates.get("last_dn", dates.get("last_delivery_no", "N/A"))
        last_delivery = dates.get("last_delivery_date", dates.get("last_delivery", "N/A"))

        lines = [
            "🏢 DEALER DASHBOARD",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Dealer",
            f"{dealer_display}",
            "",
            "Dealer Code",
            f"{dealer_code}",
            "",
            "Customer Code",
            f"{customer_code}",
            "",
            "Warehouse",
            f"{warehouse}",
            "",
            "Dealer City",
            f"{city}",
            "",
            "Sales Office",
            f"{sales_office}",
            "",
            "Sales Manager",
            f"{sales_manager}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📍 Logistics",
            "",
            "Road Distance",
            f"{distance_km} KM",
            "",
            "Estimated Delivery",
            f"{est_delivery}",
            "",
            "Transportation Zone",
            f"{trans_zone}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📦 Delivery Performance",
            "",
            "Total DN",
            f"{total_dn}",
            "",
            "Total Quantity",
            f"{total_qty} Units",
            "",
            "Total Sales",
            f"{total_sales}",
            "",
            "Delivered",
            f"{delivered}",
            "",
            "Pending",
            f"{pending}",
            "",
            "PGI Pending",
            f"{pgi_pending}",
            "",
            "POD Pending",
            f"{pod_pending}",
            "",
            "Delivery Success",
            f"{delivery_rate}%",
            "",
            "Average Delivery Days",
            f"{avg_delivery_days} Days",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "🏆 Top Selling Models",
            "",
        ]

        for i, m in enumerate(top_models[:10], start=1):
            lines.append(f"{i}. {m}")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📊 Business Performance",
            "",
            "Business Score",
            f"{int(business_score)} / 100",
            "",
            "Dealer Rating",
            f"{rating_stars}",
            "",
            "Performance",
            f"{perf_text}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📅 Latest Activity",
            "",
            "Last DN",
            f"{last_dn}",
            "",
            "Last Delivery",
            f"{last_delivery}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "💡 Executive Summary",
            "",
        ])

        if isinstance(exec_summary, str) and exec_summary.strip():
            for line in exec_summary.splitlines():
                line = line.strip()
                if line:
                    if line.startswith("•") or line.startswith("-"):
                        lines.append(line)
                    else:
                        lines.append(f"• {line}")
        else:
            insights = dashboard.get("insights", []) or []
            if insights:
                for ins in insights[:5]:
                    lines.append(f"• {ins}")
            else:
                lines.append("• Executive summary not available.")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")

        return "\n".join(lines)

# --- Context object ---------------------------------------------------------
class DealerContext:
    def __init__(self) -> None:
        self.menu_state = "MAIN"
        self.current_dealer: Optional[str] = None

# --- Main Service -----------------------------------------------------------
class DealerAnalyticsService:
    def __init__(self) -> None:
        self._service_name = "dealer_analytics"
        self._version = "1.0"
        self._menu_renderer = DealerMenuRenderer()
        self._lock = threading.RLock()

    @staticmethod
    def _session():
        return SessionLocal()

    def get_main_menu(self) -> str:
        return "🔹 Dealer Analytics - send dealer name to view dashboard."

    def _resolve_dealer_name(self, input_text: str) -> Optional[str]:
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                dealers = repo.search_dealers(input_text)
                if dealers:
                    return dealers[0].get("dealer")
        except Exception:
            pass
        return None

    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        # Minimal fallback to satisfy callers; in production keep your full menu implementation.
        context = DealerContext()
        if user_input.strip() == "0":
            return {"response": self.get_main_menu(), "exit_menu": True}
        # If it's a dealer name, route to dashboard
        dealer_name = self._resolve_dealer_name(user_input)
        if dealer_name:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                if dashboard:
                    return {"response": self._menu_renderer.render_dealer_dashboard(dealer_name, dashboard), "exit_menu": False}
        return {"response": "❌ Not found. Send a valid dealer name.", "exit_menu": False}

    def process_whatsapp_query(self, message: str, sender: str = "default", **kwargs: Any) -> str:
        """
        Process WhatsApp query and return formatted response.
        ALWAYS returns a string - never a dict.
        """
        if not message or not message.strip():
            return self.get_main_menu()

        text = message.strip()

        # Menu navigation
        if text.lower() in ["menu", "help", "options"]:
            return self.get_main_menu()

        # First: treat text as dealer name
        try:
            dealer_name = self._resolve_dealer_name(text)
            if dealer_name:
                with self._session() as session:
                    builder = DealerDashboardBuilder(session)
                    dashboard = builder.build(dealer_name)
                    if dashboard:
                        return self._menu_renderer.render_dealer_dashboard(dealer_name, dashboard)
        except Exception:
            pass

        # Fallback to menu processing
        result = self.process_menu_input(sender, text)
        return result.get("response", self.get_main_menu())

# --- Singleton accessor -----------------------------------------------------
_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()

def get_dealer_analytics_service() -> DealerAnalyticsService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DealerAnalyticsService()
    return _service
