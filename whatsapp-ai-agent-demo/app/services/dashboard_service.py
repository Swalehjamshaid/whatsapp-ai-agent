#!/usr/bin/env python3
# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 19.4.3 – FULLY ROBUST, FOLLOWS DN_ANALYSIS PATTERN
# ============================================================

"""
Dashboard Service – Enterprise Logistics Dashboard for Haier Pakistan.
Provides executive KPIs, pipeline, warehouse ranking, alerts, etc.
Always returns valid JSON – even if database is down or table missing.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import traceback
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Union

# ============================================================
# DATABASE IMPORTS
# ============================================================
try:
    from sqlalchemy import create_engine, text, MetaData, Table, exc
    from sqlalchemy.orm import sessionmaker, Session
    from app.database import SessionLocal, engine
    from app.models import DeliveryReport
    DB_AVAILABLE = True
except ImportError as e:
    DB_AVAILABLE = False
    SessionLocal = None
    engine = None

# ============================================================
# LOGGING SETUP
# ============================================================
logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL")
DASHBOARD_CACHE_TTL = int(os.getenv("DASHBOARD_CACHE_TTL", "30"))  # seconds
PERFORMANCE_TARGET = int(os.getenv("DASHBOARD_PERFORMANCE_TARGET", "300"))

# ============================================================
# UTILITY FUNCTIONS
# ============================================================
def _format_number(v: Union[int, float]) -> str:
    if v is None:
        return "0"
    return f"{int(v):,}"

def _format_currency(v: float) -> str:
    if v is None:
        return "PKR 0"
    if v >= 1_000_000_000:
        return f"PKR {v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"PKR {v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"PKR {v:,.0f}"
    return f"PKR {v:,.0f}"

def _format_pct(v: float) -> str:
    if v is None:
        return "0.0%"
    return f"{v:.1f}%"

def _safe_str(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

# ============================================================
# DASHBOARD SERVICE – SINGLETON
# ============================================================
class DashboardService:
    _instance: Optional["DashboardService"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self._version = "19.4.3"
        self._service_name = "dashboard_service"
        self._engine = None
        self._session_local = None
        self._table_exists = False
        self._cache = {}
        self._cache_time = 0

        # Init database
        self._init_database()
        logger.info("=" * 60)
        logger.info(f"🚀 Dashboard Service v{self._version} initialized")
        logger.info(f"   🗄️  Database: {'Connected' if self._engine else 'Unavailable'}")
        logger.info(f"   📋 Table exists: {self._table_exists}")
        logger.info(f"   ⏱️  Cache TTL: {DASHBOARD_CACHE_TTL}s")
        logger.info("=" * 60)

    def _init_database(self):
        """Initialize database connection and check table existence."""
        if not DB_AVAILABLE:
            logger.warning("⚠️ SQLAlchemy not available – database disabled")
            return

        # Try to use the app's existing engine, or create our own
        if engine is not None:
            self._engine = engine
        elif DATABASE_URL:
            try:
                self._engine = create_engine(DATABASE_URL)
            except Exception as e:
                logger.error(f"❌ Failed to create engine: {e}")
                self._engine = None
        else:
            logger.warning("⚠️ No DATABASE_URL provided – database disabled")
            self._engine = None

        if self._engine is None:
            return

        # Check if table exists
        try:
            with self._engine.connect() as conn:
                result = conn.execute(text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'delivery_reports')"
                ))
                self._table_exists = result.scalar()
                if self._table_exists:
                    logger.info("✅ Table 'delivery_reports' exists.")
                else:
                    logger.warning("⚠️ Table 'delivery_reports' does NOT exist.")
        except Exception as e:
            logger.error(f"❌ Table check failed: {e}")
            self._table_exists = False

    def _get_session(self) -> Optional[Session]:
        """Return a new session or None if unavailable."""
        if not self._engine or not self._table_exists:
            return None
        try:
            return sessionmaker(bind=self._engine)()
        except Exception as e:
            logger.error(f"❌ Session creation failed: {e}")
            return None

    def _close_session(self, session: Optional[Session]):
        if session:
            try:
                session.close()
            except Exception:
                pass

    # ---------- Data Fetching ----------
    def _fetch_delivery_data(self) -> List[Dict[str, Any]]:
        """Fetch all records as list of dicts. Returns empty list on failure."""
        session = self._get_session()
        if not session:
            logger.warning("No DB session – returning empty data.")
            return []

        try:
            # We'll fetch all columns – to avoid missing columns, use a raw SQL
            # that selects all columns.
            # Use SQLAlchemy core for safety.
            with self._engine.connect() as conn:
                # Get column names
                col_result = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'delivery_reports'"
                ))
                cols = [row[0] for row in col_result]

                # Build SELECT
                select_cols = ", ".join(cols) if cols else "*"
                query = f"SELECT {select_cols} FROM delivery_reports"
                # If 'deleted' column exists, filter
                if 'deleted' in cols:
                    query += " WHERE deleted = false OR deleted IS NULL"

                rows = conn.execute(text(query)).fetchall()
                result = []
                for row in rows:
                    d = {}
                    for i, col in enumerate(cols):
                        d[col] = row[i]
                    result.append(d)
                logger.info(f"✅ Fetched {len(result)} rows from delivery_reports.")
                return result
        except Exception as e:
            logger.error(f"❌ Fetch error: {traceback.format_exc()}")
            return []
        finally:
            self._close_session(session)

    # ---------- Business Logic Methods ----------
    def _safe_agg(self, data: List[Dict], col: str, default: float = 0.0) -> float:
        """Safely sum a column, handling missing keys and None values."""
        total = 0.0
        for row in data:
            val = row.get(col)
            if val is not None:
                try:
                    total += float(val)
                except (ValueError, TypeError):
                    pass
        return total

    def _safe_count_unique(self, data: List[Dict], col: str) -> int:
        """Count unique non-null values in a column."""
        values = set()
        for row in data:
            val = row.get(col)
            if val is not None:
                values.add(str(val))
        return len(values)

    def _safe_count_notna(self, data: List[Dict], col: str) -> int:
        """Count rows where column is not null."""
        cnt = 0
        for row in data:
            val = row.get(col)
            if val is not None and val != "":
                cnt += 1
        return cnt

    def calculate_kpis(self, data: List[Dict]) -> Dict[str, Any]:
        if not data:
            return {
                "total_dn": {"value": 0},
                "total_units": {"value": 0},
                "total_value": {"value": 0},
                "pgi_achievement": {"value": 0.0},
                "pod_achievement": {"value": 0.0},
                "pending_dn": {"value": 0},
                "pending_units": {"value": 0},
                "health_score": {"value": 0.0},
            }

        total_dn = self._safe_count_unique(data, "dn")
        total_units = self._safe_agg(data, "units")
        total_value = self._safe_agg(data, "value")

        # PGI: count non-null pgi_date
        pgi_count = self._safe_count_notna(data, "pgi_date")
        pgi_achievement = pgi_count / total_dn if total_dn > 0 else 0.0

        # POD
        pod_count = self._safe_count_notna(data, "pod_date")
        pod_achievement = pod_count / total_dn if total_dn > 0 else 0.0

        # Pending: where delivery_date is null
        pending_dn = 0
        pending_units = 0
        for row in data:
            if row.get("delivery_date") in (None, ""):
                pending_dn += 1
                pending_units += float(row.get("units", 0))

        # Health score
        health_score = (pgi_achievement * 0.4 + pod_achievement * 0.4) * 100
        if pending_units > 5000:
            health_score -= 15
        elif pending_units > 1000:
            health_score -= 8
        health_score = max(0, min(100, health_score))

        return {
            "total_dn": {"value": total_dn},
            "total_units": {"value": total_units},
            "total_value": {"value": total_value},
            "pgi_achievement": {"value": pgi_achievement},
            "pod_achievement": {"value": pod_achievement},
            "pending_dn": {"value": pending_dn},
            "pending_units": {"value": pending_units},
            "health_score": {"value": health_score},
        }

    def generate_executive_summary(self, data: List[Dict]) -> str:
        if not data:
            return "No data available. Please import an Excel file."

        kpis = self.calculate_kpis(data)
        total_dn = kpis["total_dn"]["value"]
        total_units = kpis["total_units"]["value"]
        pgi = kpis["pgi_achievement"]["value"] * 100
        pod = kpis["pod_achievement"]["value"] * 100
        pending = kpis["pending_units"]["value"]
        health = kpis["health_score"]["value"]

        summary = (
            f"Today's logistics performance: {total_dn:,} Delivery Notes "
            f"representing {total_units:,.0f} units. PGI achievement is at {pgi:.1f}% "
            f"and POD achievement at {pod:.1f}%. "
            f"There are {pending:,.0f} units pending dispatch. "
            f"The overall logistics health score is {health:.1f}%. "
        )
        if health >= 90:
            summary += "Operations are running excellently."
        elif health >= 75:
            summary += "Performance is solid; monitor pending units closely."
        else:
            summary += "Immediate attention required to improve PGI and POD rates."
        return summary

    def calculate_pipeline(self, data: List[Dict]) -> Dict[str, Dict]:
        if not data:
            return {
                "dn_created": {"dn": 0, "pct": 0},
                "pgi_completed": {"dn": 0, "pct": 0},
                "in_transit": {"dn": 0, "pct": 0},
                "delivered": {"dn": 0, "pct": 0},
                "pod_received": {"dn": 0, "pct": 0},
            }

        # Filter today's data if created_at exists, otherwise use all
        today = datetime.now().date()
        today_data = []
        for row in data:
            created = row.get("created_at")
            if created is not None:
                try:
                    if isinstance(created, datetime):
                        dt = created.date()
                    elif isinstance(created, date):
                        dt = created
                    elif isinstance(created, str):
                        dt = datetime.fromisoformat(created[:10]).date()
                    else:
                        dt = today
                    if dt == today:
                        today_data.append(row)
                except:
                    continue
        if not today_data:
            today_data = data  # fallback to all

        total_dn = self._safe_count_unique(today_data, "dn")
        if total_dn == 0:
            total_dn = 1

        pgi_count = self._safe_count_notna(today_data, "pgi_date")
        # In transit: pgi not null and delivery null
        in_transit = 0
        for row in today_data:
            if row.get("pgi_date") not in (None, "") and row.get("delivery_date") in (None, ""):
                in_transit += 1
        delivered = self._safe_count_notna(today_data, "delivery_date")
        pod_received = self._safe_count_notna(today_data, "pod_date")

        def pct(v):
            return round((v / total_dn) * 100, 1) if total_dn > 0 else 0

        return {
            "dn_created": {"dn": total_dn, "pct": 100},
            "pgi_completed": {"dn": pgi_count, "pct": pct(pgi_count)},
            "in_transit": {"dn": in_transit, "pct": pct(in_transit)},
            "delivered": {"dn": delivered, "pct": pct(delivered)},
            "pod_received": {"dn": pod_received, "pct": pct(pod_received)},
        }

    def calculate_warehouse_performance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        # Group by warehouse
        warehouses = {}
        for row in data:
            wh = row.get("warehouse", "Unknown")
            if wh not in warehouses:
                warehouses[wh] = {
                    "warehouse": wh,
                    "dns": set(),
                    "units": 0,
                    "value": 0,
                    "pgi_count": 0,
                    "pod_count": 0,
                    "delivery_count": 0,
                    "pending_units": 0,
                    "pending_dns": set(),
                    "delivery_days": [],
                    "pod_days": [],
                }
            whd = warehouses[wh]
            dn = row.get("dn")
            if dn is not None:
                whd["dns"].add(str(dn))
            whd["units"] += float(row.get("units", 0))
            whd["value"] += float(row.get("value", 0))

            if row.get("pgi_date") not in (None, ""):
                whd["pgi_count"] += 1
            if row.get("pod_date") not in (None, ""):
                whd["pod_count"] += 1
                # Compute pod days if delivery date exists
                if row.get("delivery_date") not in (None, ""):
                    try:
                        pod_dt = self._parse_date(row.get("pod_date"))
                        del_dt = self._parse_date(row.get("delivery_date"))
                        if pod_dt and del_dt:
                            days = (del_dt - pod_dt).days
                            if days >= 0:
                                whd["pod_days"].append(days)
                    except:
                        pass
            if row.get("delivery_date") not in (None, ""):
                whd["delivery_count"] += 1
                # Compute delivery days (pgi to delivery)
                if row.get("pgi_date") not in (None, ""):
                    try:
                        pgi_dt = self._parse_date(row.get("pgi_date"))
                        del_dt = self._parse_date(row.get("delivery_date"))
                        if pgi_dt and del_dt:
                            days = (del_dt - pgi_dt).days
                            if days >= 0:
                                whd["delivery_days"].append(days)
                    except:
                        pass
            else:
                # pending
                whd["pending_units"] += float(row.get("units", 0))
                if dn is not None:
                    whd["pending_dns"].add(str(dn))

        result = []
        for wh, whd in warehouses.items():
            dns_count = len(whd["dns"])
            pgi_pct = (whd["pgi_count"] / dns_count * 100) if dns_count > 0 else 0
            delivery_pct = (whd["delivery_count"] / dns_count * 100) if dns_count > 0 else 0
            pod_pct = (whd["pod_count"] / dns_count * 100) if dns_count > 0 else 0
            avg_delivery_days = sum(whd["delivery_days"]) / len(whd["delivery_days"]) if whd["delivery_days"] else 0
            avg_pod_days = sum(whd["pod_days"]) / len(whd["pod_days"]) if whd["pod_days"] else 0
            pending_units = whd["pending_units"]
            pending_dns = len(whd["pending_dns"])

            # Performance score
            perf = (pgi_pct * 0.3 + delivery_pct * 0.3 + pod_pct * 0.3)
            if pending_units > 5000:
                perf -= 20
            elif pending_units > 1000:
                perf -= 10
            perf = max(0, min(100, perf))

            # Risk
            if avg_delivery_days > 10:
                risk = "🔴"
            elif avg_delivery_days > 5:
                risk = "🟡"
            else:
                risk = "🟢"

            # Trend (will be computed later across all)
            result.append({
                "warehouse": wh,
                "dns": dns_count,
                "units": whd["units"],
                "value": whd["value"],
                "pgi_pct": pgi_pct,
                "delivery_pct": delivery_pct,
                "pod_pct": pod_pct,
                "avg_delivery_days": avg_delivery_days,
                "avg_pod_days": avg_pod_days,
                "pending_units": pending_units,
                "pending_dns": pending_dns,
                "performance_score": perf,
                "risk": risk,
                "ai_insight": "",
            })

        # Sort by performance descending, assign rank
        result.sort(key=lambda x: x["performance_score"], reverse=True)
        avg_score = sum(r["performance_score"] for r in result) / len(result) if result else 50
        for i, r in enumerate(result):
            r["rank"] = i + 1
            r["trend"] = "↑" if r["performance_score"] > avg_score else ("↓" if r["performance_score"] < avg_score else "▬")
            # AI insight
            if r["pending_units"] > 5000:
                r["ai_insight"] = "High pending units. Immediate action required."
            elif r["avg_delivery_days"] > 5:
                r["ai_insight"] = f"Avg delivery {r['avg_delivery_days']:.1f} days. Optimize routes."
            elif r["pod_pct"] < 80:
                r["ai_insight"] = "Low POD rate. Follow up on proof of delivery."
            else:
                r["ai_insight"] = "Good performance. Maintain standards."

            # Round numeric fields
            r["performance_score"] = round(r["performance_score"], 1)
            r["pgi_pct"] = round(r["pgi_pct"], 1)
            r["delivery_pct"] = round(r["delivery_pct"], 1)
            r["pod_pct"] = round(r["pod_pct"], 1)
            r["avg_delivery_days"] = round(r["avg_delivery_days"], 1)
            r["avg_pod_days"] = round(r["avg_pod_days"], 1)
            r["units"] = int(r["units"])
            r["dns"] = int(r["dns"])
            r["value"] = float(r["value"])
            r["pending_units"] = int(r["pending_units"])
            r["pending_dns"] = int(r["pending_dns"])

        return result

    def _parse_date(self, val: Any) -> Optional[datetime]:
        """Safely parse date from string, datetime, or date object."""
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        if isinstance(val, date):
            return datetime.combine(val, datetime.min.time())
        if isinstance(val, str):
            for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
                try:
                    return datetime.strptime(val, fmt)
                except ValueError:
                    continue
        return None

    def calculate_city_performance(self, data: List[Dict], top_n: int = 5) -> List[Dict]:
        if not data:
            return []

        cities = {}
        for row in data:
            city = row.get("city", "Unknown")
            if city not in cities:
                cities[city] = {
                    "city": city,
                    "delivery_days": [],
                    "pending_units": 0,
                }
            if row.get("delivery_date") not in (None, "") and row.get("pgi_date") not in (None, ""):
                try:
                    pgi = self._parse_date(row.get("pgi_date"))
                    delv = self._parse_date(row.get("delivery_date"))
                    if pgi and delv:
                        days = (delv - pgi).days
                        if days >= 0:
                            cities[city]["delivery_days"].append(days)
                except:
                    pass
            else:
                cities[city]["pending_units"] += float(row.get("units", 0))

        result = []
        for city, cdata in cities.items():
            avg_days = sum(cdata["delivery_days"]) / len(cdata["delivery_days"]) if cdata["delivery_days"] else 0
            status = "Good"
            if avg_days > 10:
                status = "Critical"
            elif avg_days > 5:
                status = "Warning"
            result.append({
                "city": city,
                "avg_delivery_days": round(avg_days, 1),
                "pending_units": int(cdata["pending_units"]),
                "status": status,
            })

        result.sort(key=lambda x: x["avg_delivery_days"], reverse=True)
        return result[:top_n]

    def calculate_pending_analysis(self, data: List[Dict], top_n: int = 5) -> List[Dict]:
        if not data:
            return []

        pending = {}
        for row in data:
            if row.get("delivery_date") in (None, ""):
                wh = row.get("warehouse", "Unknown")
                if wh not in pending:
                    pending[wh] = {"pending_dns": set(), "pending_units": 0}
                pending[wh]["pending_dns"].add(str(row.get("dn")))
                pending[wh]["pending_units"] += float(row.get("units", 0))

        result = []
        for wh, pdata in pending.items():
            result.append({
                "warehouse": wh,
                "pending_dns": len(pdata["pending_dns"]),
                "pending_units": int(pdata["pending_units"]),
            })
        result.sort(key=lambda x: x["pending_units"], reverse=True)
        return result[:top_n]

    def calculate_dealer_performance(self, data: List[Dict], top_n: int = 5) -> List[Dict]:
        if not data:
            return []

        dealers = {}
        for row in data:
            dealer = row.get("dealer", "Unknown")
            if dealer not in dealers:
                dealers[dealer] = {"units": 0, "revenue": 0}
            dealers[dealer]["units"] += float(row.get("units", 0))
            dealers[dealer]["revenue"] += float(row.get("value", 0))

        result = []
        for dealer, vals in dealers.items():
            result.append({
                "dealer": dealer,
                "units": int(vals["units"]),
                "revenue": float(vals["revenue"]),
            })
        result.sort(key=lambda x: x["revenue"], reverse=True)
        return result[:top_n]

    def calculate_product_performance(self, data: List[Dict], top_n: int = 5) -> List[Dict]:
        if not data:
            return []

        products = {}
        for row in data:
            prod = row.get("product", "Unknown")
            if prod not in products:
                products[prod] = {"units": 0, "dns": set()}
            products[prod]["units"] += float(row.get("units", 0))
            products[prod]["dns"].add(str(row.get("dn")))

        result = []
        for prod, vals in products.items():
            result.append({
                "product": prod,
                "units": int(vals["units"]),
                "delivery_notes": len(vals["dns"]),
            })
        result.sort(key=lambda x: x["units"], reverse=True)
        return result[:top_n]

    def calculate_division_performance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        divs = {}
        for row in data:
            div = row.get("division", "Unknown")
            if div not in divs:
                divs[div] = 0.0
            divs[div] += float(row.get("value", 0))

        result = []
        for div, rev in divs.items():
            result.append({
                "division": div,
                "revenue": float(rev),
            })
        result.sort(key=lambda x: x["revenue"], reverse=True)
        return result

    def calculate_delivery_compliance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        # Simulate brackets based on avg delivery days quantiles
        delivery_days = []
        for row in data:
            if row.get("delivery_date") not in (None, "") and row.get("pgi_date") not in (None, ""):
                try:
                    pgi = self._parse_date(row.get("pgi_date"))
                    delv = self._parse_date(row.get("delivery_date"))
                    if pgi and delv:
                        days = (delv - pgi).days
                        if days >= 0:
                            delivery_days.append(days)
                except:
                    pass
        if not delivery_days:
            return [
                {"distance": "0-100", "target_days": 1, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
                {"distance": "100-200", "target_days": 2, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
                {"distance": "200-300", "target_days": 3, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
                {"distance": "300-500", "target_days": 5, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
                {"distance": "500-1000", "target_days": 7, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
            ]

        # Use quantiles
        sorted_days = sorted(delivery_days)
        q = [0.2, 0.4, 0.6, 0.8]
        quantiles = []
        for p in q:
            idx = int(p * len(sorted_days))
            quantiles.append(sorted_days[idx])

        brackets = [
            {"distance": "0-100", "target_days": 1, "min": 0, "max": quantiles[0]},
            {"distance": "100-200", "target_days": 2, "min": quantiles[0], "max": quantiles[1]},
            {"distance": "200-300", "target_days": 3, "min": quantiles[1], "max": quantiles[2]},
            {"distance": "300-500", "target_days": 5, "min": quantiles[2], "max": quantiles[3]},
            {"distance": "500-1000", "target_days": 7, "min": quantiles[3], "max": float('inf')},
        ]

        result = []
        for b in brackets:
            subset = [d for d in delivery_days if b["min"] <= d <= b["max"]]
            if subset:
                actual = sum(subset) / len(subset)
                compliance = min(100, (b["target_days"] / actual) * 100) if actual > 0 else 0
                status = "Within Standard" if compliance >= 80 else "Needs Improvement"
            else:
                actual = 0
                compliance = 0
                status = "No Data"
            result.append({
                "distance": b["distance"],
                "target_days": b["target_days"],
                "actual_days": round(actual, 1),
                "compliance_pct": round(compliance, 1),
                "status": status,
            })
        return result

    def generate_critical_alerts(self, data: List[Dict]) -> List[Dict]:
        alerts = []
        if not data:
            return alerts

        # 1. Pending units per warehouse
        pending_wh = {}
        for row in data:
            if row.get("delivery_date") in (None, ""):
                wh = row.get("warehouse", "Unknown")
                pending_wh[wh] = pending_wh.get(wh, 0) + float(row.get("units", 0))

        for wh, units in pending_wh.items():
            if units > 5000:
                alerts.append({
                    "category": "Pending Units",
                    "source": wh,
                    "message": f"Warehouse {wh} has {units:,.0f} units pending, exceeding critical threshold.",
                    "severity": "CRITICAL"
                })
            elif units > 1000:
                alerts.append({
                    "category": "Pending Units",
                    "source": wh,
                    "message": f"Warehouse {wh} has {units:,.0f} units pending, above warning level.",
                    "severity": "WARNING"
                })

        # 2. City delays
        city_perf = self.calculate_city_performance(data, top_n=10)
        for c in city_perf:
            if c["status"] == "Critical":
                alerts.append({
                    "category": "Delivery Delay",
                    "source": c["city"],
                    "message": f"City {c['city']} has avg delivery {c['avg_delivery_days']:.1f} days, critical.",
                    "severity": "CRITICAL"
                })
            elif c["status"] == "Warning":
                alerts.append({
                    "category": "Delivery Delay",
                    "source": c["city"],
                    "message": f"City {c['city']} has avg delivery {c['avg_delivery_days']:.1f} days, warning.",
                    "severity": "WARNING"
                })

        # 3. Low POD
        wh_perf = self.calculate_warehouse_performance(data)
        for wh in wh_perf:
            if wh["pod_pct"] < 70:
                alerts.append({
                    "category": "Low POD Rate",
                    "source": wh["warehouse"],
                    "message": f"Warehouse {wh['warehouse']} has POD rate {wh['pod_pct']:.1f}%, below 70%.",
                    "severity": "WARNING"
                })

        # 4. Health score
        health = self.calculate_kpis(data)["health_score"]["value"]
        if health < 70:
            alerts.append({
                "category": "Health Score",
                "source": "Overall Logistics",
                "message": f"Health score is {health:.1f}%, below acceptable level.",
                "severity": "CRITICAL"
            })

        alerts.sort(key=lambda x: 0 if x["severity"] == "CRITICAL" else 1)
        return alerts[:10]

    def get_recommendations(self, data: List[Dict]) -> List[str]:
        recs = []
        if not data:
            return ["No data to generate recommendations. Please import an Excel file."]

        # 1. Pending units
        pending_units = self.calculate_kpis(data)["pending_units"]["value"]
        if pending_units > 5000:
            recs.append("Urgently expedite dispatch of pending units at all warehouses to reduce backlog.")

        # 2. POD
        pod_rate = self.calculate_kpis(data)["pod_achievement"]["value"]
        if pod_rate < 0.8:
            recs.append("Implement a POD follow-up campaign with sales managers to improve proof of delivery collection.")

        # 3. City delays
        city_perf = self.calculate_city_performance(data, top_n=3)
        for c in city_perf:
            if c["status"] in ["Critical", "Warning"]:
                recs.append(f"Review logistics routes and capacity for {c['city']} to reduce delivery days.")

        # 4. Warehouse performance
        wh_perf = self.calculate_warehouse_performance(data)
        for wh in wh_perf[:3]:
            if wh["performance_score"] < 70:
                recs.append(f"Conduct an operational review at {wh['warehouse']} to improve performance score.")

        if not recs:
            recs.append("All metrics are within acceptable ranges. Continue monitoring and maintain current performance levels.")
        return recs[:5]

    def calculate_monthly_trend(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        months = {}
        for row in data:
            created = row.get("created_at") or row.get("delivery_date")
            if created is None:
                continue
            try:
                dt = self._parse_date(created)
                if dt:
                    month_key = dt.strftime("%Y-%m")
                    if month_key not in months:
                        months[month_key] = {"dn": set(), "units": 0}
                    months[month_key]["dn"].add(str(row.get("dn")))
                    months[month_key]["units"] += float(row.get("units", 0))
            except:
                continue

        result = []
        for month, vals in sorted(months.items()):
            result.append({
                "month": month,
                "dn_count": len(vals["dn"]),
                "units": int(vals["units"]),
            })
        return result

    # ---------- Main Public Method ----------
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Return full dashboard data as JSON-serializable dict. Always succeeds."""
        start_time = time.time()
        try:
            # Check cache
            if time.time() - self._cache_time < DASHBOARD_CACHE_TTL and self._cache:
                logger.debug("Returning cached dashboard data")
                return self._cache

            # Fetch data
            raw_data = self._fetch_delivery_data()
            if not raw_data:
                logger.warning("No data fetched – returning empty dashboard.")
                result = self._empty_response("No data available. Please import an Excel file.")
            else:
                # Compute everything
                cards = self.calculate_kpis(raw_data)
                summary = self.generate_executive_summary(raw_data)
                pipeline = self.calculate_pipeline(raw_data)
                warehouse_ranking = self.calculate_warehouse_performance(raw_data)
                delayed_cities = self.calculate_city_performance(raw_data)
                pending_warehouses = self.calculate_pending_analysis(raw_data)
                top_dealers = self.calculate_dealer_performance(raw_data)
                top_products = self.calculate_product_performance(raw_data)
                division_perf = self.calculate_division_performance(raw_data)
                compliance = self.calculate_delivery_compliance(raw_data)
                alerts = self.generate_critical_alerts(raw_data)
                recommendations = self.get_recommendations(raw_data)
                monthly_trend = self.calculate_monthly_trend(raw_data)

                result = {
                    "cards": cards,
                    "executive_summary_text": summary,
                    "pipeline_detailed": pipeline,
                    "warehouse_ranking": warehouse_ranking,
                    "top_delayed_cities": delayed_cities,
                    "top_pending_warehouses": pending_warehouses,
                    "top_dealers": top_dealers,
                    "top_products": top_products,
                    "division_performance": division_perf,
                    "delivery_compliance": compliance,
                    "alerts": alerts,
                    "recommendations": recommendations,
                    "monthly_trend": monthly_trend,
                    "metadata": {
                        "record_count": len(raw_data),
                        "version": self._version,
                        "execution_time_ms": round((time.time() - start_time) * 1000, 1),
                    }
                }

            # Update cache
            self._cache = result
            self._cache_time = time.time()
            return result

        except Exception as e:
            logger.error(f"❌ Dashboard generation error: {traceback.format_exc()}")
            return self._empty_response(f"Error generating dashboard: {str(e)}")

    def _empty_response(self, message: str) -> Dict[str, Any]:
        """Return a valid dashboard structure with zeros and the message."""
        return {
            "cards": {
                "total_dn": {"value": 0},
                "total_units": {"value": 0},
                "total_value": {"value": 0},
                "pgi_achievement": {"value": 0.0},
                "pod_achievement": {"value": 0.0},
                "pending_dn": {"value": 0},
                "pending_units": {"value": 0},
                "health_score": {"value": 0.0},
            },
            "executive_summary_text": message,
            "pipeline_detailed": {
                "dn_created": {"dn": 0, "pct": 0},
                "pgi_completed": {"dn": 0, "pct": 0},
                "in_transit": {"dn": 0, "pct": 0},
                "delivered": {"dn": 0, "pct": 0},
                "pod_received": {"dn": 0, "pct": 0},
            },
            "warehouse_ranking": [],
            "top_delayed_cities": [],
            "top_pending_warehouses": [],
            "top_dealers": [],
            "top_products": [],
            "division_performance": [],
            "delivery_compliance": [
                {"distance": "0-100", "target_days": 1, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
                {"distance": "100-200", "target_days": 2, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
                {"distance": "200-300", "target_days": 3, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
                {"distance": "300-500", "target_days": 5, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
                {"distance": "500-1000", "target_days": 7, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
            ],
            "alerts": [],
            "recommendations": [message],
            "monthly_trend": [],
            "metadata": {
                "record_count": 0,
                "version": self._version,
                "execution_time_ms": 0,
            },
        }

    # ---------- Health Check ----------
    def health_check(self) -> Dict[str, Any]:
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy" if self._engine and self._table_exists else "degraded",
            "database": "connected" if self._engine else "disconnected",
            "table_exists": self._table_exists,
            "cache_size": len(self._cache),
            "timestamp": datetime.now().isoformat(),
        }

# ============================================================
# SINGLETON ACCESSOR
# ============================================================
_service: Optional[DashboardService] = None
_service_lock = threading.Lock()

def get_dashboard_service() -> DashboardService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DashboardService()
    return _service

# Alias for compatibility
DashboardServiceInstance = get_dashboard_service

# ============================================================
# FLASK BLUEPRINT (optional)
# ============================================================
try:
    from flask import Blueprint, jsonify, request, current_app

    def create_dashboard_blueprint():
        bp = Blueprint('dashboard', __name__, url_prefix='/dashboard/api')
        service = get_dashboard_service()

        @bp.route('/data', methods=['GET'])
        def get_data():
            try:
                data = service.get_dashboard_data()
                return jsonify(data)
            except Exception as e:
                current_app.logger.error(f"Route error: {traceback.format_exc()}")
                return jsonify({"error": str(e)}), 500

        @bp.route('/upload', methods=['POST'])
        def upload_excel():
            if 'file' not in request.files:
                return jsonify({"error": "No file part"}), 400
            file = request.files['file']
            if file.filename == '':
                return jsonify({"error": "No selected file"}), 400
            # We'll implement upload later – for now return a placeholder
            return jsonify({"status": "success", "message": "Upload endpoint not yet implemented."}), 200

        @bp.route('/health', methods=['GET'])
        def health():
            return jsonify(service.health_check())

        return bp
except ImportError:
    # Flask not installed – skip blueprint
    pass

# ============================================================
# EXPORTS
# ============================================================
__all__ = [
    "DashboardService",
    "get_dashboard_service",
    "create_dashboard_blueprint",
]
