#!/usr/bin/env python3
"""
dashboard_service.py - Enterprise Logistics Dashboard Service
Version: 20.0 – Full enterprise implementation
Provides all KPIs, executive summary, pipeline, rankings, compliance,
alerts, recommendations, metadata, and trends.
"""

import os
import logging
import time
import traceback
import threading
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional, Union, Tuple
from collections import defaultdict
import math

# ------------------------------------------------------------
# DATABASE IMPORTS
# ------------------------------------------------------------
try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from app.database import SessionLocal, engine
    from app.models import DeliveryReport
    DB_APP_AVAILABLE = True
except ImportError:
    DB_APP_AVAILABLE = False
    SessionLocal = None
    engine = None

# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# UTILITY FUNCTIONS
# ------------------------------------------------------------
def _safe_str(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

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

def _parse_date(val: Any) -> Optional[datetime]:
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

def _safe_divide(a: float, b: float, default: float = 0.0) -> float:
    return (a / b) if b != 0 else default

# ------------------------------------------------------------
# DASHBOARD SERVICE (Singleton)
# ------------------------------------------------------------
class DashboardService:
    _instance = None
    _lock = threading.Lock()

    # Column mapping from database to expected names
    COLUMN_MAP = {
        'dn_no': 'dn',
        'dn_qty': 'units',
        'dn_amount': 'value',
        'good_issue_date': 'pgi_date',
        'pod_date': 'pod_date',
        'dn_create_date': 'created_at',
        'ship_to_city': 'city',
        'customer_name': 'dealer',
        'customer_model': 'product',
        'sales_office': 'sales_office',
        'sales_manager': 'sales_manager',
        'division': 'division',
        'warehouse': 'warehouse',
        # Add any other mappings
    }

    # Fixed delivery compliance brackets (distance in KM)
    COMPLIANCE_BRACKETS = [
        {"distance": "0-100", "target_days": 1},
        {"distance": "101-250", "target_days": 2},
        {"distance": "251-450", "target_days": 3},
        {"distance": "451-700", "target_days": 4},
        {"distance": "701-900", "target_days": 5},
        {"distance": "901+", "target_days": 6},
    ]

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
        self._version = "20.0"
        self._engine = None
        self._session_maker = None
        self._table_exists = False
        self._cache = {}
        self._cache_time = 0
        self._cache_ttl = int(os.getenv("DASHBOARD_CACHE_TTL", "30"))

        self._init_database()
        logger.info("=" * 60)
        logger.info(f"🚀 Dashboard Service v{self._version} initialized")
        logger.info(f"   🗄️  Database engine: {'OK' if self._engine else 'None'}")
        logger.info(f"   📋 Table exists: {self._table_exists}")
        logger.info("=" * 60)

    def _init_database(self):
        if DB_APP_AVAILABLE and engine is not None:
            self._engine = engine
            self._session_maker = sessionmaker(bind=engine)
            logger.info("✅ Using app's database engine")
        else:
            db_url = os.getenv("DATABASE_URL")
            if db_url:
                try:
                    from sqlalchemy import create_engine
                    self._engine = create_engine(db_url)
                    self._session_maker = sessionmaker(bind=self._engine)
                    logger.info("✅ Created engine from DATABASE_URL")
                except Exception as e:
                    logger.error(f"❌ Failed to create engine: {e}")
                    self._engine = None
            else:
                logger.warning("⚠️ No DATABASE_URL environment variable")

        if self._engine:
            try:
                with self._engine.connect() as conn:
                    result = conn.execute(text(
                        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'delivery_reports')"
                    ))
                    self._table_exists = result.scalar()
                    if self._table_exists:
                        logger.info("✅ Table 'delivery_reports' exists")
                    else:
                        logger.warning("⚠️ Table 'delivery_reports' does NOT exist")
            except Exception as e:
                logger.error(f"❌ Table check failed: {e}")
                self._table_exists = False

    # ---------- Data Fetching with Column Mapping ----------
    def _fetch_data(self) -> List[Dict[str, Any]]:
        if not self._engine or not self._table_exists:
            return []

        try:
            with self._engine.connect() as conn:
                col_result = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'delivery_reports'"
                ))
                cols = [row[0] for row in col_result]
                if not cols:
                    return []

                select_cols = ", ".join(cols)
                query = f"SELECT {select_cols} FROM delivery_reports"
                if 'deleted' in cols:
                    query += " WHERE deleted = false OR deleted IS NULL"

                rows = conn.execute(text(query)).fetchall()
                data = []
                for row in rows:
                    d = {}
                    for i, col in enumerate(cols):
                        d[col] = row[i]
                    data.append(d)

                # Apply column mapping
                mapped_data = []
                for row in data:
                    mapped = {}
                    for old, new in self.COLUMN_MAP.items():
                        if old in row:
                            mapped[new] = row[old]
                    # Keep any columns that already match expected names
                    for expected in ['dn', 'units', 'value', 'pgi_date', 'pod_date',
                                     'warehouse', 'city', 'dealer', 'product', 'division',
                                     'sales_office', 'sales_manager', 'created_at']:
                        if expected in row and expected not in mapped:
                            mapped[expected] = row[expected]
                    mapped_data.append(mapped)
                return mapped_data

        except Exception as e:
            logger.error(f"❌ Fetch error: {traceback.format_exc()}")
            return []

    # ---------- BUSINESS LOGIC FUNCTIONS ----------

    # 1. KPI Cards (enhanced)
    def calculate_kpis(self, data: List[Dict]) -> Dict[str, Any]:
        if not data:
            return {
                "total_dn": {"value": 0},
                "total_units": {"value": 0},
                "total_value": {"value": 0},
                "pgi_achievement": {"value": 0.0},
                "delivery_achievement": {"value": 0.0},
                "pod_achievement": {"value": 0.0},
                "pending_dn": {"value": 0},
                "pending_units": {"value": 0},
                "pending_value": {"value": 0.0},
                "avg_delivery_days": {"value": 0.0},
                "avg_pod_days": {"value": 0.0},
                "health_score": {"value": 0.0},
            }

        # Get distinct DNs and aggregate
        dns = {}
        for row in data:
            dn = row.get("dn")
            if dn:
                dn_str = str(dn)
                if dn_str not in dns:
                    dns[dn_str] = {
                        "units": 0,
                        "value": 0,
                        "pgi_date": None,
                        "pod_date": None,
                        "warehouse": row.get("warehouse"),
                        "city": row.get("city"),
                    }
                dns[dn_str]["units"] += float(row.get("units", 0))
                dns[dn_str]["value"] += float(row.get("value", 0))
                if row.get("pgi_date") not in (None, ""):
                    dns[dn_str]["pgi_date"] = row.get("pgi_date")
                if row.get("pod_date") not in (None, ""):
                    dns[dn_str]["pod_date"] = row.get("pod_date")

        total_dn = len(dns)
        total_units = sum(d["units"] for d in dns.values())
        total_value = sum(d["value"] for d in dns.values())

        # Counts
        pgi_count = sum(1 for d in dns.values() if d["pgi_date"] not in (None, ""))
        pod_count = sum(1 for d in dns.values() if d["pod_date"] not in (None, ""))
        # Delivery = POD (since delivery is considered complete when POD is received)
        delivery_count = pod_count
        pending_dn = total_dn - pod_count
        pending_units = sum(d["units"] for dn, d in dns.items() if d["pod_date"] in (None, ""))
        pending_value = sum(d["value"] for dn, d in dns.items() if d["pod_date"] in (None, ""))

        # Percentages
        pgi_pct = _safe_divide(pgi_count, total_dn) * 100
        delivery_pct = _safe_divide(delivery_count, pgi_count) * 100 if pgi_count > 0 else 0  # % of PGI that got delivered
        pod_pct = _safe_divide(pod_count, total_dn) * 100

        # Average days
        delivery_days_list = []
        pod_days_list = []
        for d in dns.values():
            if d["pgi_date"] not in (None, "") and d["pod_date"] not in (None, ""):
                pgi = _parse_date(d["pgi_date"])
                pod = _parse_date(d["pod_date"])
                if pgi and pod:
                    dd = (pod - pgi).days
                    if dd >= 0:
                        delivery_days_list.append(dd)
                        # POD days = pod - delivery (but delivery is same as pod, so 0)
                        # We'll compute average POD days as (pod - pgi) also, but later we can differentiate if we have separate delivery date
                        pod_days_list.append(dd)
        avg_delivery_days = sum(delivery_days_list) / len(delivery_days_list) if delivery_days_list else 0
        avg_pod_days = sum(pod_days_list) / len(pod_days_list) if pod_days_list else 0

        # Health score: weighted average of PGI, Delivery, POD, penalty for pending
        health = (pgi_pct * 0.3 + delivery_pct * 0.3 + pod_pct * 0.3)
        if pending_units > 5000:
            health -= 15
        elif pending_units > 1000:
            health -= 8
        health = max(0, min(100, health))

        return {
            "total_dn": {"value": total_dn},
            "total_units": {"value": total_units},
            "total_value": {"value": total_value},
            "pgi_achievement": {"value": pgi_pct},
            "delivery_achievement": {"value": delivery_pct},
            "pod_achievement": {"value": pod_pct},
            "pending_dn": {"value": pending_dn},
            "pending_units": {"value": pending_units},
            "pending_value": {"value": pending_value},
            "avg_delivery_days": {"value": avg_delivery_days},
            "avg_pod_days": {"value": avg_pod_days},
            "health_score": {"value": health},
        }

    # 2. Executive Summary (enhanced)
    def generate_executive_summary(self, data: List[Dict]) -> Dict[str, Any]:
        if not data:
            return {
                "overall_health": 0,
                "status": "No Data",
                "summary": "No data available. Please import an Excel file.",
                "risks": [],
                "highlights": [],
                "recommendations": ["Import data to see recommendations."],
            }

        kpis = self.calculate_kpis(data)
        health = kpis["health_score"]["value"]
        pending_units = kpis["pending_units"]["value"]
        total_dn = kpis["total_dn"]["value"]
        total_units = kpis["total_units"]["value"]
        total_value = kpis["total_value"]["value"]
        pgi = kpis["pgi_achievement"]["value"]
        delivery = kpis["delivery_achievement"]["value"]
        pod = kpis["pod_achievement"]["value"]
        avg_delivery_days = kpis["avg_delivery_days"]["value"]
        avg_pod_days = kpis["avg_pod_days"]["value"]

        # Determine status
        if health >= 90:
            status = "Excellent"
        elif health >= 75:
            status = "Good"
        elif health >= 60:
            status = "Fair"
        else:
            status = "Critical"

        # Summary text
        summary = (
            f"Logistics performance: {total_dn:,} Delivery Notes, {total_units:,.0f} units, "
            f"PKR {total_value:,.0f} revenue. PGI: {pgi:.1f}%, Delivery: {delivery:.1f}%, "
            f"POD: {pod:.1f}%. Avg Delivery Days: {avg_delivery_days:.1f}, Avg POD Days: {avg_pod_days:.1f}. "
            f"Pending: {pending_units:,.0f} units. Health Score: {health:.1f}%."
        )

        # Identify risks
        risks = []
        if pending_units > 5000:
            risks.append("High pending units (>5000).")
        if delivery < 80:
            risks.append("Low delivery rate (<80%).")
        if pod < 80:
            risks.append("Low POD rate (<80%).")
        if avg_delivery_days > 7:
            risks.append("High average delivery days (>7).")

        # Highlights (best performers)
        highlights = []
        # Best warehouse by performance
        wh_perf = self.calculate_warehouse_performance(data)
        if wh_perf:
            best_wh = max(wh_perf, key=lambda x: x["performance_score"])
            if best_wh["performance_score"] > 80:
                highlights.append(f"Top warehouse: {best_wh['warehouse']} (Score: {best_wh['performance_score']:.1f}%)")
        # Best dealer
        dealer_perf = self.calculate_dealer_performance(data)
        if dealer_perf:
            best_dealer = dealer_perf[0]
            highlights.append(f"Top dealer: {best_dealer['dealer']} (Revenue: {_format_currency(best_dealer['revenue'])})")

        # Recommendations (dynamic)
        recommendations = self.get_recommendations(data)

        return {
            "overall_health": round(health, 1),
            "status": status,
            "summary": summary,
            "risks": risks,
            "highlights": highlights,
            "recommendations": recommendations,
        }

    # 3. Pipeline (enhanced with more stages, but we'll keep it simple if data missing)
    def calculate_pipeline(self, data: List[Dict]) -> Dict[str, Any]:
        if not data:
            return self._empty_pipeline()

        dns = {}
        for row in data:
            dn = row.get("dn")
            if dn:
                dn_str = str(dn)
                if dn_str not in dns:
                    dns[dn_str] = {
                        "pgi_date": None,
                        "pod_date": None,
                        # Additional stages could be derived if we have columns like 'vehicle_assigned_date', 'loading_date', etc.
                    }
                if row.get("pgi_date") not in (None, ""):
                    dns[dn_str]["pgi_date"] = row.get("pgi_date")
                if row.get("pod_date") not in (None, ""):
                    dns[dn_str]["pod_date"] = row.get("pod_date")

        total = len(dns)
        if total == 0:
            return self._empty_pipeline()

        # Stages
        dn_created = total
        pgi_completed = sum(1 for d in dns.values() if d["pgi_date"] not in (None, ""))
        # In transit = pgi done, pod not done
        in_transit = sum(1 for d in dns.values() if d["pgi_date"] not in (None, "") and d["pod_date"] in (None, ""))
        delivered = sum(1 for d in dns.values() if d["pod_date"] not in (None, ""))
        pod_received = delivered

        # Additional stages (we'll set them as 0 if no data)
        vehicle_assigned = 0
        loading = 0
        gate_out = 0
        arrival = 0
        closed = pod_received

        def pct(v):
            return round((v / total) * 100, 1) if total > 0 else 0

        conversion = round((pod_received / total) * 100, 1) if total > 0 else 0
        pipeline_loss = round(((dn_created - pod_received) / total) * 100, 1) if total > 0 else 0

        return {
            "dn_created": {"dn": dn_created, "pct": 100},
            "pgi_completed": {"dn": pgi_completed, "pct": pct(pgi_completed)},
            "vehicle_assigned": {"dn": vehicle_assigned, "pct": pct(vehicle_assigned)},
            "loading": {"dn": loading, "pct": pct(loading)},
            "gate_out": {"dn": gate_out, "pct": pct(gate_out)},
            "in_transit": {"dn": in_transit, "pct": pct(in_transit)},
            "arrival": {"dn": arrival, "pct": pct(arrival)},
            "delivered": {"dn": delivered, "pct": pct(delivered)},
            "pod_received": {"dn": pod_received, "pct": pct(pod_received)},
            "closed": {"dn": closed, "pct": pct(closed)},
            "conversion": conversion,
            "pipeline_loss": pipeline_loss,
        }

    def _empty_pipeline(self):
        return {
            "dn_created": {"dn": 0, "pct": 0},
            "pgi_completed": {"dn": 0, "pct": 0},
            "vehicle_assigned": {"dn": 0, "pct": 0},
            "loading": {"dn": 0, "pct": 0},
            "gate_out": {"dn": 0, "pct": 0},
            "in_transit": {"dn": 0, "pct": 0},
            "arrival": {"dn": 0, "pct": 0},
            "delivered": {"dn": 0, "pct": 0},
            "pod_received": {"dn": 0, "pct": 0},
            "closed": {"dn": 0, "pct": 0},
            "conversion": 0,
            "pipeline_loss": 0,
        }

    # 4. Warehouse Performance (enhanced)
    def calculate_warehouse_performance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        # Aggregate by warehouse
        wh_data = {}
        for row in data:
            wh = row.get("warehouse", "Unknown")
            if wh not in wh_data:
                wh_data[wh] = {
                    "dns": set(),
                    "units": 0,
                    "value": 0,
                    "pgi_count": 0,
                    "pod_count": 0,
                    "pending_units": 0,
                    "pending_value": 0,
                    "pod_days": [],
                }
            w = wh_data[wh]
            dn = row.get("dn")
            if dn:
                w["dns"].add(str(dn))
            w["units"] += float(row.get("units", 0))
            w["value"] += float(row.get("value", 0))
            if row.get("pgi_date") not in (None, ""):
                w["pgi_count"] += 1
            if row.get("pod_date") not in (None, ""):
                w["pod_count"] += 1
                # Compute pod days (pod - pgi)
                if row.get("pgi_date") not in (None, ""):
                    pgi = _parse_date(row.get("pgi_date"))
                    pod = _parse_date(row.get("pod_date"))
                    if pgi and pod:
                        days = (pod - pgi).days
                        if days >= 0:
                            w["pod_days"].append(days)
            else:
                w["pending_units"] += float(row.get("units", 0))
                w["pending_value"] += float(row.get("value", 0))

        result = []
        for wh, w in wh_data.items():
            dns = len(w["dns"])
            if dns == 0:
                continue
            pgi_pct = _safe_divide(w["pgi_count"], dns) * 100
            pod_pct = _safe_divide(w["pod_count"], dns) * 100
            delivery_pct = pod_pct  # delivery = pod
            avg_pod_days = sum(w["pod_days"]) / len(w["pod_days"]) if w["pod_days"] else 0
            pending_units = w["pending_units"]
            pending_value = w["pending_value"]
            performance_score = (pgi_pct * 0.3 + delivery_pct * 0.3 + pod_pct * 0.3)
            if pending_units > 5000:
                performance_score -= 20
            elif pending_units > 1000:
                performance_score -= 10
            performance_score = max(0, min(100, performance_score))

            # Health = performance_score (or a custom metric)
            health = performance_score

            # Ranking will be assigned later
            result.append({
                "warehouse": wh,
                "dns": dns,
                "units": w["units"],
                "value": w["value"],
                "pgi_pct": round(pgi_pct, 1),
                "delivery_pct": round(delivery_pct, 1),
                "pod_pct": round(pod_pct, 1),
                "avg_delivery_days": round(avg_pod_days, 1),  # same as pod days
                "avg_pod_days": round(avg_pod_days, 1),
                "pending_units": int(pending_units),
                "pending_dns": int(dns - w["pod_count"]),
                "pending_value": pending_value,
                "performance_score": round(performance_score, 1),
                "health_score": round(health, 1),
                "risk": "🔴" if avg_pod_days > 10 or pending_units > 5000 else ("🟡" if avg_pod_days > 5 or pending_units > 1000 else "🟢"),
                "trend": "▬",
                "ai_insight": "",
                "rank": 0,
            })

        # Sort and assign ranks, trends, insights
        result.sort(key=lambda x: x["performance_score"], reverse=True)
        avg_score = sum(r["performance_score"] for r in result) / len(result) if result else 50
        for i, r in enumerate(result):
            r["rank"] = i + 1
            r["trend"] = "↑" if r["performance_score"] > avg_score else ("↓" if r["performance_score"] < avg_score else "▬")
            if r["pending_units"] > 5000:
                r["ai_insight"] = "High pending units. Immediate action required."
            elif r["avg_delivery_days"] > 5:
                r["ai_insight"] = f"Avg delivery {r['avg_delivery_days']:.1f} days. Optimize routes."
            elif r["pod_pct"] < 80:
                r["ai_insight"] = "Low POD rate. Follow up on proof of delivery."
            else:
                r["ai_insight"] = "Good performance. Maintain standards."

        return result

    # 5. City Performance (enhanced)
    def calculate_city_performance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        city_data = {}
        for row in data:
            city = row.get("city", "Unknown")
            if city not in city_data:
                city_data[city] = {
                    "dns": set(),
                    "units": 0,
                    "value": 0,
                    "pgi_count": 0,
                    "pod_count": 0,
                    "pending_units": 0,
                    "pod_days": [],
                    "warehouses": set(),
                    "dealers": set(),
                }
            c = city_data[city]
            dn = row.get("dn")
            if dn:
                c["dns"].add(str(dn))
            c["units"] += float(row.get("units", 0))
            c["value"] += float(row.get("value", 0))
            if row.get("pgi_date") not in (None, ""):
                c["pgi_count"] += 1
            if row.get("pod_date") not in (None, ""):
                c["pod_count"] += 1
                if row.get("pgi_date") not in (None, ""):
                    pgi = _parse_date(row.get("pgi_date"))
                    pod = _parse_date(row.get("pod_date"))
                    if pgi and pod:
                        days = (pod - pgi).days
                        if days >= 0:
                            c["pod_days"].append(days)
            else:
                c["pending_units"] += float(row.get("units", 0))
            if row.get("warehouse"):
                c["warehouses"].add(row.get("warehouse"))
            if row.get("dealer"):
                c["dealers"].add(row.get("dealer"))

        result = []
        for city, c in city_data.items():
            dns = len(c["dns"])
            if dns == 0:
                continue
            pgi_pct = _safe_divide(c["pgi_count"], dns) * 100
            pod_pct = _safe_divide(c["pod_count"], dns) * 100
            delivery_pct = pod_pct
            avg_delivery_days = sum(c["pod_days"]) / len(c["pod_days"]) if c["pod_days"] else 0
            pending_units = c["pending_units"]
            health = (pgi_pct * 0.3 + delivery_pct * 0.3 + pod_pct * 0.3)
            if pending_units > 5000:
                health -= 15
            elif pending_units > 1000:
                health -= 8
            health = max(0, min(100, health))

            status = "Good"
            risk = "🟢"
            if avg_delivery_days > 10 or pending_units > 5000:
                status = "Critical"
                risk = "🔴"
            elif avg_delivery_days > 5 or pending_units > 1000:
                status = "Warning"
                risk = "🟡"

            result.append({
                "city": city,
                "avg_delivery_days": round(avg_delivery_days, 1),
                "pending_units": int(pending_units),
                "status": status,
                "risk": risk,
                "revenue": c["value"],
                "units": c["units"],
                "dns": dns,
                "dealers": len(c["dealers"]),
                "warehouses": len(c["warehouses"]),
                "pgi_pct": round(pgi_pct, 1),
                "delivery_pct": round(delivery_pct, 1),
                "pod_pct": round(pod_pct, 1),
                "health": round(health, 1),
            })

        result.sort(key=lambda x: x["avg_delivery_days"], reverse=True)
        return result

    # 6. Pending Analysis
    def calculate_pending_analysis(self, data: List[Dict], top_n: int = 5) -> List[Dict]:
        if not data:
            return []

        pending = {}
        for row in data:
            if row.get("pod_date") in (None, ""):
                wh = row.get("warehouse", "Unknown")
                if wh not in pending:
                    pending[wh] = {"pending_dns": set(), "pending_units": 0, "pending_value": 0}
                pending[wh]["pending_dns"].add(str(row.get("dn")))
                pending[wh]["pending_units"] += float(row.get("units", 0))
                pending[wh]["pending_value"] += float(row.get("value", 0))

        result = []
        for wh, vals in pending.items():
            result.append({
                "warehouse": wh,
                "pending_dns": len(vals["pending_dns"]),
                "pending_units": int(vals["pending_units"]),
                "pending_value": vals["pending_value"],
            })
        result.sort(key=lambda x: x["pending_units"], reverse=True)
        return result[:top_n]

    # 7. Dealer Performance (enhanced)
    def calculate_dealer_performance(self, data: List[Dict], top_n: int = 10) -> List[Dict]:
        if not data:
            return []

        dealer_data = {}
        for row in data:
            dealer = row.get("dealer", "Unknown")
            if dealer not in dealer_data:
                dealer_data[dealer] = {
                    "dns": set(),
                    "units": 0,
                    "revenue": 0,
                    "pgi_count": 0,
                    "pod_count": 0,
                    "cities": set(),
                    "warehouses": set(),
                    "pod_days": [],
                }
            d = dealer_data[dealer]
            dn = row.get("dn")
            if dn:
                d["dns"].add(str(dn))
            d["units"] += float(row.get("units", 0))
            d["revenue"] += float(row.get("value", 0))
            if row.get("pgi_date") not in (None, ""):
                d["pgi_count"] += 1
            if row.get("pod_date") not in (None, ""):
                d["pod_count"] += 1
                if row.get("pgi_date") not in (None, ""):
                    pgi = _parse_date(row.get("pgi_date"))
                    pod = _parse_date(row.get("pod_date"))
                    if pgi and pod:
                        days = (pod - pgi).days
                        if days >= 0:
                            d["pod_days"].append(days)
            if row.get("city"):
                d["cities"].add(row.get("city"))
            if row.get("warehouse"):
                d["warehouses"].add(row.get("warehouse"))

        result = []
        for dealer, d in dealer_data.items():
            dns = len(d["dns"])
            if dns == 0:
                continue
            pod_pct = _safe_divide(d["pod_count"], dns) * 100
            delivery_pct = pod_pct
            avg_delivery_days = sum(d["pod_days"]) / len(d["pod_days"]) if d["pod_days"] else 0
            avg_pod_days = avg_delivery_days
            pending_units = d["units"] - sum(1 for _ in range(d["pod_count"]))  # approximate
            growth = 0  # Not implemented
            health = (delivery_pct * 0.6 + pod_pct * 0.4)
            if pending_units > 5000:
                health -= 15
            health = max(0, min(100, health))

            result.append({
                "dealer": dealer,
                "revenue": d["revenue"],
                "units": d["units"],
                "dns": dns,
                "cities": len(d["cities"]),
                "warehouses": len(d["warehouses"]),
                "avg_delivery_days": round(avg_delivery_days, 1),
                "avg_pod_days": round(avg_pod_days, 1),
                "pending_units": int(pending_units),
                "pod_pct": round(pod_pct, 1),
                "delivery_pct": round(delivery_pct, 1),
                "health": round(health, 1),
                "growth": 0,
                "rank": 0,
                "ai_insight": "",
            })

        result.sort(key=lambda x: x["revenue"], reverse=True)
        for i, r in enumerate(result[:top_n]):
            r["rank"] = i + 1
            if r["pod_pct"] < 80:
                r["ai_insight"] = "Low POD rate. Follow up."
            elif r["avg_delivery_days"] > 5:
                r["ai_insight"] = f"Long delivery {r['avg_delivery_days']:.1f} days."
            else:
                r["ai_insight"] = "Good performance."
        return result[:top_n]

    # 8. Product Performance (enhanced)
    def calculate_product_performance(self, data: List[Dict], top_n: int = 10) -> List[Dict]:
        if not data:
            return []

        product_data = {}
        for row in data:
            prod = row.get("product", "Unknown")
            if prod not in product_data:
                product_data[prod] = {
                    "dns": set(),
                    "units": 0,
                    "revenue": 0,
                    "pgi_count": 0,
                    "pod_count": 0,
                    "cities": set(),
                    "warehouses": set(),
                    "dealers": set(),
                    "pod_days": [],
                }
            p = product_data[prod]
            dn = row.get("dn")
            if dn:
                p["dns"].add(str(dn))
            p["units"] += float(row.get("units", 0))
            p["revenue"] += float(row.get("value", 0))
            if row.get("pgi_date") not in (None, ""):
                p["pgi_count"] += 1
            if row.get("pod_date") not in (None, ""):
                p["pod_count"] += 1
                if row.get("pgi_date") not in (None, ""):
                    pgi = _parse_date(row.get("pgi_date"))
                    pod = _parse_date(row.get("pod_date"))
                    if pgi and pod:
                        days = (pod - pgi).days
                        if days >= 0:
                            p["pod_days"].append(days)
            if row.get("city"):
                p["cities"].add(row.get("city"))
            if row.get("warehouse"):
                p["warehouses"].add(row.get("warehouse"))
            if row.get("dealer"):
                p["dealers"].add(row.get("dealer"))

        result = []
        total_revenue = sum(p["revenue"] for p in product_data.values())
        for prod, p in product_data.items():
            dns = len(p["dns"])
            if dns == 0:
                continue
            pod_pct = _safe_divide(p["pod_count"], dns) * 100
            delivery_pct = pod_pct
            avg_delivery_days = sum(p["pod_days"]) / len(p["pod_days"]) if p["pod_days"] else 0
            pending_units = p["units"] - sum(1 for _ in range(p["pod_count"]))
            contribution = _safe_divide(p["revenue"], total_revenue) * 100
            health = (delivery_pct * 0.5 + pod_pct * 0.5)
            if pending_units > 5000:
                health -= 15
            health = max(0, min(100, health))

            result.append({
                "product": prod,
                "units": int(p["units"]),
                "revenue": p["revenue"],
                "dns": dns,
                "dealers": len(p["dealers"]),
                "warehouses": len(p["warehouses"]),
                "cities": len(p["cities"]),
                "avg_delivery_days": round(avg_delivery_days, 1),
                "avg_pod_days": round(avg_delivery_days, 1),
                "pending_units": int(pending_units),
                "pod_pct": round(pod_pct, 1),
                "delivery_pct": round(delivery_pct, 1),
                "contribution": round(contribution, 1),
                "health": round(health, 1),
                "rank": 0,
                "ai_insight": "",
            })

        result.sort(key=lambda x: x["revenue"], reverse=True)
        for i, r in enumerate(result[:top_n]):
            r["rank"] = i + 1
            if r["pod_pct"] < 80:
                r["ai_insight"] = "Low POD rate."
            elif r["avg_delivery_days"] > 5:
                r["ai_insight"] = f"Long delivery {r['avg_delivery_days']:.1f} days."
            else:
                r["ai_insight"] = "Good performance."
        return result[:top_n]

    # 9. Division Performance (enhanced)
    def calculate_division_performance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        div_data = {}
        for row in data:
            div = row.get("division", "Unknown")
            if div not in div_data:
                div_data[div] = {
                    "dns": set(),
                    "units": 0,
                    "revenue": 0,
                    "pgi_count": 0,
                    "pod_count": 0,
                    "pending_units": 0,
                }
            d = div_data[div]
            dn = row.get("dn")
            if dn:
                d["dns"].add(str(dn))
            d["units"] += float(row.get("units", 0))
            d["revenue"] += float(row.get("value", 0))
            if row.get("pgi_date") not in (None, ""):
                d["pgi_count"] += 1
            if row.get("pod_date") not in (None, ""):
                d["pod_count"] += 1
            else:
                d["pending_units"] += float(row.get("units", 0))

        result = []
        for div, d in div_data.items():
            dns = len(d["dns"])
            if dns == 0:
                continue
            pgi_pct = _safe_divide(d["pgi_count"], dns) * 100
            pod_pct = _safe_divide(d["pod_count"], dns) * 100
            delivery_pct = pod_pct
            health = (pgi_pct * 0.3 + delivery_pct * 0.3 + pod_pct * 0.3)
            if d["pending_units"] > 5000:
                health -= 15
            health = max(0, min(100, health))

            result.append({
                "division": div,
                "dns": dns,
                "units": d["units"],
                "revenue": d["revenue"],
                "pgi_pct": round(pgi_pct, 1),
                "delivery_pct": round(delivery_pct, 1),
                "pod_pct": round(pod_pct, 1),
                "pending_units": int(d["pending_units"]),
                "health": round(health, 1),
                "rank": 0,
            })

        result.sort(key=lambda x: x["revenue"], reverse=True)
        for i, r in enumerate(result):
            r["rank"] = i + 1
        return result

    # 10. Sales Office Performance
    def calculate_sales_office_performance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        # Check if sales_office column exists and has data
        has_office = any(row.get("sales_office") for row in data)
        if not has_office:
            return []

        office_data = {}
        for row in data:
            office = row.get("sales_office", "Unknown")
            if office not in office_data:
                office_data[office] = {
                    "dns": set(),
                    "units": 0,
                    "revenue": 0,
                    "pgi_count": 0,
                    "pod_count": 0,
                    "pending_units": 0,
                }
            o = office_data[office]
            dn = row.get("dn")
            if dn:
                o["dns"].add(str(dn))
            o["units"] += float(row.get("units", 0))
            o["revenue"] += float(row.get("value", 0))
            if row.get("pgi_date") not in (None, ""):
                o["pgi_count"] += 1
            if row.get("pod_date") not in (None, ""):
                o["pod_count"] += 1
            else:
                o["pending_units"] += float(row.get("units", 0))

        result = []
        for office, o in office_data.items():
            dns = len(o["dns"])
            if dns == 0:
                continue
            pgi_pct = _safe_divide(o["pgi_count"], dns) * 100
            pod_pct = _safe_divide(o["pod_count"], dns) * 100
            delivery_pct = pod_pct
            health = (pgi_pct * 0.3 + delivery_pct * 0.3 + pod_pct * 0.3)
            if o["pending_units"] > 5000:
                health -= 15
            health = max(0, min(100, health))

            result.append({
                "sales_office": office,
                "dns": dns,
                "units": o["units"],
                "revenue": o["revenue"],
                "pgi_pct": round(pgi_pct, 1),
                "delivery_pct": round(delivery_pct, 1),
                "pod_pct": round(pod_pct, 1),
                "pending_units": int(o["pending_units"]),
                "health": round(health, 1),
                "rank": 0,
            })

        result.sort(key=lambda x: x["revenue"], reverse=True)
        for i, r in enumerate(result):
            r["rank"] = i + 1
        return result

    # 11. Sales Manager Performance
    def calculate_sales_manager_performance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        has_manager = any(row.get("sales_manager") for row in data)
        if not has_manager:
            return []

        manager_data = {}
        for row in data:
            mgr = row.get("sales_manager", "Unknown")
            if mgr not in manager_data:
                manager_data[mgr] = {
                    "dns": set(),
                    "units": 0,
                    "revenue": 0,
                    "pgi_count": 0,
                    "pod_count": 0,
                    "pending_units": 0,
                }
            m = manager_data[mgr]
            dn = row.get("dn")
            if dn:
                m["dns"].add(str(dn))
            m["units"] += float(row.get("units", 0))
            m["revenue"] += float(row.get("value", 0))
            if row.get("pgi_date") not in (None, ""):
                m["pgi_count"] += 1
            if row.get("pod_date") not in (None, ""):
                m["pod_count"] += 1
            else:
                m["pending_units"] += float(row.get("units", 0))

        result = []
        for mgr, m in manager_data.items():
            dns = len(m["dns"])
            if dns == 0:
                continue
            pgi_pct = _safe_divide(m["pgi_count"], dns) * 100
            pod_pct = _safe_divide(m["pod_count"], dns) * 100
            delivery_pct = pod_pct
            health = (pgi_pct * 0.3 + delivery_pct * 0.3 + pod_pct * 0.3)
            if m["pending_units"] > 5000:
                health -= 15
            health = max(0, min(100, health))

            result.append({
                "sales_manager": mgr,
                "dns": dns,
                "units": m["units"],
                "revenue": m["revenue"],
                "pgi_pct": round(pgi_pct, 1),
                "delivery_pct": round(delivery_pct, 1),
                "pod_pct": round(pod_pct, 1),
                "pending_units": int(m["pending_units"]),
                "health": round(health, 1),
                "rank": 0,
            })

        result.sort(key=lambda x: x["revenue"], reverse=True)
        for i, r in enumerate(result):
            r["rank"] = i + 1
        return result

    # 12. Delivery Compliance (fixed distance-based SLA)
    def calculate_delivery_compliance(self, data: List[Dict]) -> List[Dict]:
        # Since we don't have actual distance, we'll use pod_days as proxy
        # We'll group by delivery days brackets (quantiles) but with fixed targets
        if not data:
            return self._empty_compliance()

        # Compute pod_days for each DN (if pgi and pod exist)
        pod_days_list = []
        for row in data:
            if row.get("pgi_date") not in (None, "") and row.get("pod_date") not in (None, ""):
                pgi = _parse_date(row.get("pgi_date"))
                pod = _parse_date(row.get("pod_date"))
                if pgi and pod:
                    days = (pod - pgi).days
                    if days >= 0:
                        pod_days_list.append(days)

        if not pod_days_list:
            return self._empty_compliance()

        # Use fixed brackets with target days
        # We'll assign each DN to a bracket based on its actual days.
        # Instead of using quantiles, we'll use fixed thresholds.
        # Since we don't have distance, we'll simulate distance brackets by
        # sorting and grouping into 6 equal-sized groups.
        sorted_days = sorted(pod_days_list)
        n = len(sorted_days)
        group_size = max(1, n // len(self.COMPLIANCE_BRACKETS))
        result = []
        for i, bracket in enumerate(self.COMPLIANCE_BRACKETS):
            start = i * group_size
            end = min((i + 1) * group_size, n)
            if start < n:
                subset = sorted_days[start:end]
                actual_avg = sum(subset) / len(subset)
                target_days = bracket["target_days"]
                compliance = min(100, (target_days / actual_avg) * 100) if actual_avg > 0 else 0
                status = "Within Standard" if compliance >= 80 else "Needs Improvement"
            else:
                actual_avg = 0
                compliance = 0
                status = "No Data"
            result.append({
                "distance": bracket["distance"],
                "target_days": target_days,
                "actual_days": round(actual_avg, 1),
                "compliance_pct": round(compliance, 1),
                "status": status,
            })
        return result

    def _empty_compliance(self) -> List[Dict]:
        return [
            {"distance": b["distance"], "target_days": b["target_days"], "actual_days": 0, "compliance_pct": 0, "status": "No Data"}
            for b in self.COMPLIANCE_BRACKETS
        ]

    # 13. Critical Alerts (enhanced)
    def generate_critical_alerts(self, data: List[Dict]) -> List[Dict]:
        alerts = []
        if not data:
            return alerts

        kpis = self.calculate_kpis(data)
        # Low PGI
        if kpis["pgi_achievement"]["value"] < 80:
            alerts.append({
                "category": "PGI Rate",
                "source": "Overall",
                "message": f"PGI achievement is {kpis['pgi_achievement']['value']:.1f}%, below 80%.",
                "severity": "WARNING"
            })
        # Low Delivery
        if kpis["delivery_achievement"]["value"] < 80:
            alerts.append({
                "category": "Delivery Rate",
                "source": "Overall",
                "message": f"Delivery achievement is {kpis['delivery_achievement']['value']:.1f}%, below 80%.",
                "severity": "WARNING"
            })
        # Low POD
        if kpis["pod_achievement"]["value"] < 80:
            alerts.append({
                "category": "POD Rate",
                "source": "Overall",
                "message": f"POD achievement is {kpis['pod_achievement']['value']:.1f}%, below 80%.",
                "severity": "WARNING"
            })

        # Warehouse below 85%
        wh_perf = self.calculate_warehouse_performance(data)
        for wh in wh_perf:
            if wh["performance_score"] < 85:
                alerts.append({
                    "category": "Warehouse Performance",
                    "source": wh["warehouse"],
                    "message": f"Warehouse {wh['warehouse']} score {wh['performance_score']:.1f}%, below 85%.",
                    "severity": "WARNING"
                })

        # Dealer below 85% (based on health)
        dealer_perf = self.calculate_dealer_performance(data)
        for d in dealer_perf:
            if d["health"] < 85:
                alerts.append({
                    "category": "Dealer Performance",
                    "source": d["dealer"],
                    "message": f"Dealer {d['dealer']} health {d['health']:.1f}%, below 85%.",
                    "severity": "WARNING"
                })

        # City delays
        city_perf = self.calculate_city_performance(data)
        for c in city_perf:
            if c["status"] == "Critical":
                alerts.append({
                    "category": "City Delivery Delay",
                    "source": c["city"],
                    "message": f"City {c['city']} avg delivery {c['avg_delivery_days']:.1f} days (Critical).",
                    "severity": "CRITICAL"
                })
            elif c["status"] == "Warning":
                alerts.append({
                    "category": "City Delivery Delay",
                    "source": c["city"],
                    "message": f"City {c['city']} avg delivery {c['avg_delivery_days']:.1f} days (Warning).",
                    "severity": "WARNING"
                })

        # Pending value
        if kpis["pending_value"]["value"] > 10000000:  # 10M
            alerts.append({
                "category": "Pending Value",
                "source": "Overall",
                "message": f"Pending value is {_format_currency(kpis['pending_value']['value'])}, above 10M.",
                "severity": "CRITICAL"
            })

        # Missing POD/ PGI alerts – we can check counts
        # Already covered above

        alerts.sort(key=lambda x: 0 if x["severity"] == "CRITICAL" else 1)
        return alerts[:10]

    # 14. Recommendations (enhanced)
    def get_recommendations(self, data: List[Dict]) -> List[str]:
        if not data:
            return ["No data to generate recommendations."]

        recs = []
        kpis = self.calculate_kpis(data)
        pending_units = kpis["pending_units"]["value"]
        pending_value = kpis["pending_value"]["value"]
        pgi = kpis["pgi_achievement"]["value"]
        delivery = kpis["delivery_achievement"]["value"]
        pod = kpis["pod_achievement"]["value"]
        avg_delivery_days = kpis["avg_delivery_days"]["value"]

        if pending_units > 5000:
            recs.append("Urgently expedite dispatch of pending units (over 5000).")
        if pending_value > 10000000:
            recs.append("Focus on clearing high-value pending orders (>10M).")
        if pgi < 85:
            recs.append("Improve PGI completion rate: review warehouse processes.")
        if delivery < 85:
            recs.append("Increase delivery rate: optimize routing and carrier performance.")
        if pod < 85:
            recs.append("Implement POD follow-up campaign with sales managers.")
        if avg_delivery_days > 5:
            recs.append(f"Reduce average delivery days ({avg_delivery_days:.1f}) by reviewing logistics routes.")

        # Warehouse-specific
        wh_perf = self.calculate_warehouse_performance(data)
        for wh in wh_perf[:3]:
            if wh["performance_score"] < 70:
                recs.append(f"Conduct operational review at {wh['warehouse']} (score {wh['performance_score']:.1f}%).")

        # Dealer-specific
        dealer_perf = self.calculate_dealer_performance(data)
        for d in dealer_perf[:3]:
            if d["health"] < 70:
                recs.append(f"Engage dealer {d['dealer']} to improve performance.")

        # City-specific
        city_perf = self.calculate_city_performance(data)
        for c in city_perf[:2]:
            if c["status"] in ["Critical", "Warning"]:
                recs.append(f"Review logistics routes for {c['city']} (avg delivery {c['avg_delivery_days']:.1f} days).")

        if not recs:
            recs.append("All metrics are within acceptable ranges. Continue monitoring.")
        return recs[:5]

    # 15. Monthly Trend
    def calculate_monthly_trend(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        months = {}
        for row in data:
            created = row.get("created_at") or row.get("pod_date")
            if created is None:
                continue
            dt = _parse_date(created)
            if dt:
                month_key = dt.strftime("%Y-%m")
                if month_key not in months:
                    months[month_key] = {
                        "dns": set(),
                        "units": 0,
                        "value": 0,
                        "pgi_count": 0,
                        "pod_count": 0,
                    }
                m = months[month_key]
                dn = row.get("dn")
                if dn:
                    m["dns"].add(str(dn))
                m["units"] += float(row.get("units", 0))
                m["value"] += float(row.get("value", 0))
                if row.get("pgi_date") not in (None, ""):
                    m["pgi_count"] += 1
                if row.get("pod_date") not in (None, ""):
                    m["pod_count"] += 1

        result = []
        for month, vals in sorted(months.items()):
            dns = len(vals["dns"])
            result.append({
                "month": month,
                "dn_count": dns,
                "units": int(vals["units"]),
                "revenue": vals["value"],
                "pgi_count": vals["pgi_count"],
                "pod_count": vals["pod_count"],
                "pgi_pct": round(_safe_divide(vals["pgi_count"], dns) * 100, 1) if dns > 0 else 0,
                "pod_pct": round(_safe_divide(vals["pod_count"], dns) * 100, 1) if dns > 0 else 0,
            })
        return result

    # 16. Metadata
    def generate_metadata(self, data: List[Dict], execution_time_ms: float) -> Dict[str, Any]:
        return {
            "version": self._version,
            "record_count": len(data),
            "execution_time_ms": round(execution_time_ms, 1),
            "cache_ttl": self._cache_ttl,
            "database_status": "connected" if self._engine and self._table_exists else "disconnected",
            "data_freshness": datetime.now().isoformat(),
            "upload_info": {
                "last_upload": None,  # Could be fetched from a separate table
                "filename": None,
                "rows_imported": None,
                "rows_rejected": None,
                "duplicates": None,
            }
        }

    # ------------------------------------------------------------
    # MAIN PUBLIC METHOD – async for FastAPI
    # ------------------------------------------------------------
    async def get_dashboard_data(self, filters: Optional[Dict] = None) -> Dict[str, Any]:
        start_time = time.time()
        try:
            if time.time() - self._cache_time < self._cache_ttl and self._cache:
                logger.debug("Returning cached data")
                return self._cache

            raw_data = self._fetch_data()

            # Compute all sections
            cards = self.calculate_kpis(raw_data)
            exec_summary = self.generate_executive_summary(raw_data)
            pipeline = self.calculate_pipeline(raw_data)
            warehouse_ranking = self.calculate_warehouse_performance(raw_data)
            city_performance = self.calculate_city_performance(raw_data)
            pending_analysis = self.calculate_pending_analysis(raw_data)
            dealer_performance = self.calculate_dealer_performance(raw_data)
            product_performance = self.calculate_product_performance(raw_data)
            division_performance = self.calculate_division_performance(raw_data)
            sales_office_performance = self.calculate_sales_office_performance(raw_data)
            sales_manager_performance = self.calculate_sales_manager_performance(raw_data)
            delivery_compliance = self.calculate_delivery_compliance(raw_data)
            alerts = self.generate_critical_alerts(raw_data)
            recommendations = self.get_recommendations(raw_data)
            monthly_trend = self.calculate_monthly_trend(raw_data)
            metadata = self.generate_metadata(raw_data, (time.time() - start_time) * 1000)

            result = {
                "cards": cards,
                "executive_summary": exec_summary,          # New structured summary
                "executive_summary_text": exec_summary["summary"],  # Keep for backwards compatibility
                "pipeline_detailed": pipeline,
                "warehouse_ranking": warehouse_ranking,
                "city_performance": city_performance,
                "pending_analysis": pending_analysis,
                "dealer_performance": dealer_performance,
                "product_performance": product_performance,
                "division_performance": division_performance,
                "sales_office_performance": sales_office_performance,
                "sales_manager_performance": sales_manager_performance,
                "delivery_compliance": delivery_compliance,
                "alerts": alerts,
                "recommendations": recommendations,
                "monthly_trend": monthly_trend,
                "metadata": metadata,
            }

            self._cache = result
            self._cache_time = time.time()
            return result

        except Exception as e:
            logger.error(f"❌ Dashboard generation error: {traceback.format_exc()}")
            return self._empty_response(f"Error: {str(e)}")

    def _empty_response(self, message: str) -> Dict[str, Any]:
        # Return minimal structure with empty data
        return {
            "cards": self.calculate_kpis([]),
            "executive_summary": {
                "overall_health": 0,
                "status": "No Data",
                "summary": message,
                "risks": [],
                "highlights": [],
                "recommendations": [message],
            },
            "executive_summary_text": message,
            "pipeline_detailed": self._empty_pipeline(),
            "warehouse_ranking": [],
            "city_performance": [],
            "pending_analysis": [],
            "dealer_performance": [],
            "product_performance": [],
            "division_performance": [],
            "sales_office_performance": [],
            "sales_manager_performance": [],
            "delivery_compliance": self._empty_compliance(),
            "alerts": [],
            "recommendations": [message],
            "monthly_trend": [],
            "metadata": {
                "version": self._version,
                "record_count": 0,
                "execution_time_ms": 0,
                "cache_ttl": self._cache_ttl,
                "database_status": "disconnected" if not self._engine else "connected",
                "data_freshness": datetime.now().isoformat(),
                "upload_info": {}
            },
        }

    def health_check(self) -> Dict[str, Any]:
        return {
            "service": "dashboard_service",
            "version": self._version,
            "status": "healthy" if self._engine and self._table_exists else "degraded",
            "database": "connected" if self._engine else "disconnected",
            "table_exists": self._table_exists,
            "cache_size": len(self._cache),
            "timestamp": datetime.now().isoformat(),
        }

# ------------------------------------------------------------
# SINGLETON ACCESSOR
# ------------------------------------------------------------
_service = None
_service_lock = threading.Lock()

def get_dashboard_service() -> DashboardService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DashboardService()
    return _service

# ------------------------------------------------------------
# FASTAPI ROUTER (Optional)
# ------------------------------------------------------------
try:
    from fastapi import APIRouter, Query
    router = APIRouter(prefix="/dashboard/api", tags=["dashboard"])

    @router.get("/data")
    async def dashboard_data(
        start_date: Optional[str] = Query(None),
        end_date: Optional[str] = Query(None),
        warehouse: Optional[str] = Query(None),
        dealer: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        city: Optional[str] = Query(None),
        division: Optional[str] = Query(None),
        transporter: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
    ):
        service = get_dashboard_service()
        filters = {k: v for k, v in locals().items() if v is not None and k != "service"}
        return await service.get_dashboard_data(filters)
except ImportError:
    pass

# ------------------------------------------------------------
# MODULE EXPORTS
# ------------------------------------------------------------
__all__ = [
    "DashboardService",
    "get_dashboard_service",
    "router",
]
