#!/usr/bin/env python3
"""
dashboard_service.py - Enterprise Logistics Dashboard Service
Version: 19.9 – Fixed pipeline (all data), added conversion
Full integration with dashboard.html v19.6
"""

import os
import logging
import time
import traceback
import threading
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Union
import json

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
        # Add others as needed
    }

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
        self._version = "19.9"
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
        """Try to set up database connection."""
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

                mapped_data = []
                for row in data:
                    mapped = {}
                    for old, new in self.COLUMN_MAP.items():
                        if old in row:
                            mapped[new] = row[old]
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

    # ---------- Business Logic ----------
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

        total_dn = len({str(row.get("dn")) for row in data if row.get("dn")})
        total_units = sum(float(row.get("units", 0)) for row in data)
        total_value = sum(float(row.get("value", 0)) for row in data)

        pgi_count = sum(1 for row in data if row.get("pgi_date") not in (None, ""))
        pod_count = sum(1 for row in data if row.get("pod_date") not in (None, ""))

        pgi_achievement = pgi_count / total_dn if total_dn > 0 else 0.0
        pod_achievement = pod_count / total_dn if total_dn > 0 else 0.0

        pending_dn = sum(1 for row in data if row.get("pod_date") in (None, ""))
        pending_units = sum(float(row.get("units", 0)) for row in data if row.get("pod_date") in (None, ""))

        health = (pgi_achievement * 0.4 + pod_achievement * 0.4) * 100
        if pending_units > 5000:
            health -= 15
        elif pending_units > 1000:
            health -= 8
        health = max(0, min(100, health))

        return {
            "total_dn": {"value": total_dn},
            "total_units": {"value": total_units},
            "total_value": {"value": total_value},
            "pgi_achievement": {"value": pgi_achievement},
            "pod_achievement": {"value": pod_achievement},
            "pending_dn": {"value": pending_dn},
            "pending_units": {"value": pending_units},
            "health_score": {"value": health},
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

    def calculate_pipeline(self, data: List[Dict]) -> Dict[str, Any]:
        """
        Pipeline using all data (not just today).
        Returns stages with counts and percentages, plus overall conversion.
        """
        if not data:
            return {
                "dn_created": {"dn": 0, "pct": 0},
                "pgi_completed": {"dn": 0, "pct": 0},
                "in_transit": {"dn": 0, "pct": 0},
                "delivered": {"dn": 0, "pct": 0},
                "pod_received": {"dn": 0, "pct": 0},
                "conversion": 0,
            }

        # Get unique DNs
        all_dns = {str(row.get("dn")) for row in data if row.get("dn")}
        dn_created = len(all_dns)
        if dn_created == 0:
            dn_created = 1

        # Stage counts
        pgi_completed = sum(1 for row in data if row.get("pgi_date") not in (None, ""))
        # In transit: pgi done, pod not done
        in_transit = sum(1 for row in data if row.get("pgi_date") not in (None, "") and row.get("pod_date") in (None, ""))
        delivered = sum(1 for row in data if row.get("pod_date") not in (None, ""))
        pod_received = delivered

        def pct(v):
            return round((v / dn_created) * 100, 1) if dn_created > 0 else 0

        conversion = round((pod_received / dn_created) * 100, 1) if dn_created > 0 else 0

        return {
            "dn_created": {"dn": dn_created, "pct": 100},
            "pgi_completed": {"dn": pgi_completed, "pct": pct(pgi_completed)},
            "in_transit": {"dn": in_transit, "pct": pct(in_transit)},
            "delivered": {"dn": delivered, "pct": pct(delivered)},
            "pod_received": {"dn": pod_received, "pct": pct(pod_received)},
            "conversion": conversion,
        }

    # ------------------------------------------------------------
    # Other calculation methods (unchanged, but using mapped columns)
    # ------------------------------------------------------------
    def calculate_warehouse_performance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return []

        wh_data = {}
        for row in data:
            wh = row.get("warehouse", "Unknown")
            if wh not in wh_data:
                wh_data[wh] = {
                    "warehouse": wh,
                    "dns": set(),
                    "units": 0,
                    "value": 0,
                    "pgi_count": 0,
                    "pod_count": 0,
                    "pending_units": 0,
                    "pending_dns": set(),
                    "pod_days": [],
                }
            w = wh_data[wh]
            dn = row.get("dn")
            if dn is not None:
                w["dns"].add(str(dn))
            w["units"] += float(row.get("units", 0))
            w["value"] += float(row.get("value", 0))
            if row.get("pgi_date") not in (None, ""):
                w["pgi_count"] += 1
            if row.get("pod_date") not in (None, ""):
                w["pod_count"] += 1
                if row.get("pgi_date") not in (None, ""):
                    pgi = _parse_date(row.get("pgi_date"))
                    pod = _parse_date(row.get("pod_date"))
                    if pgi and pod:
                        days = (pod - pgi).days
                        if days >= 0:
                            w["pod_days"].append(days)
            else:
                w["pending_units"] += float(row.get("units", 0))
                if dn is not None:
                    w["pending_dns"].add(str(dn))

        result = []
        for wh, w in wh_data.items():
            dns = len(w["dns"])
            pgi_pct = (w["pgi_count"] / dns * 100) if dns > 0 else 0
            pod_pct = (w["pod_count"] / dns * 100) if dns > 0 else 0
            avg_pod_days = sum(w["pod_days"]) / len(w["pod_days"]) if w["pod_days"] else 0
            pending_units = w["pending_units"]
            pending_dns = len(w["pending_dns"])

            delivery_pct = pod_pct
            perf = (pgi_pct * 0.3 + delivery_pct * 0.3 + pod_pct * 0.3)
            if pending_units > 5000:
                perf -= 20
            elif pending_units > 1000:
                perf -= 10
            perf = max(0, min(100, perf))

            risk = "🔴" if avg_pod_days > 10 else ("🟡" if avg_pod_days > 5 else "🟢")
            result.append({
                "warehouse": wh,
                "dns": dns,
                "units": w["units"],
                "value": w["value"],
                "pgi_pct": round(pgi_pct, 1),
                "delivery_pct": round(delivery_pct, 1),
                "pod_pct": round(pod_pct, 1),
                "avg_delivery_days": round(avg_pod_days, 1),
                "avg_pod_days": round(avg_pod_days, 1),
                "pending_units": int(pending_units),
                "pending_dns": int(pending_dns),
                "performance_score": round(perf, 1),
                "risk": risk,
                "trend": "▬",
                "ai_insight": "",
            })

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

    def calculate_city_performance(self, data: List[Dict], top_n: int = 5) -> List[Dict]:
        if not data:
            return []

        city_data = {}
        for row in data:
            city = row.get("city", "Unknown")
            if city not in city_data:
                city_data[city] = {"pod_days": [], "pending_units": 0}
            if row.get("pod_date") not in (None, "") and row.get("pgi_date") not in (None, ""):
                pgi = _parse_date(row.get("pgi_date"))
                pod = _parse_date(row.get("pod_date"))
                if pgi and pod:
                    days = (pod - pgi).days
                    if days >= 0:
                        city_data[city]["pod_days"].append(days)
            else:
                city_data[city]["pending_units"] += float(row.get("units", 0))

        result = []
        for city, cd in city_data.items():
            avg = sum(cd["pod_days"]) / len(cd["pod_days"]) if cd["pod_days"] else 0
            status = "Good"
            if avg > 10:
                status = "Critical"
            elif avg > 5:
                status = "Warning"
            result.append({
                "city": city,
                "avg_delivery_days": round(avg, 1),
                "pending_units": int(cd["pending_units"]),
                "status": status,
            })
        result.sort(key=lambda x: x["avg_delivery_days"], reverse=True)
        return result[:top_n]

    def calculate_pending_analysis(self, data: List[Dict], top_n: int = 5) -> List[Dict]:
        if not data:
            return []

        pending = {}
        for row in data:
            if row.get("pod_date") in (None, ""):
                wh = row.get("warehouse", "Unknown")
                if wh not in pending:
                    pending[wh] = {"pending_dns": set(), "pending_units": 0}
                pending[wh]["pending_dns"].add(str(row.get("dn")))
                pending[wh]["pending_units"] += float(row.get("units", 0))

        result = []
        for wh, vals in pending.items():
            result.append({
                "warehouse": wh,
                "pending_dns": len(vals["pending_dns"]),
                "pending_units": int(vals["pending_units"]),
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
            divs[div] = divs.get(div, 0) + float(row.get("value", 0))

        result = []
        for div, rev in divs.items():
            result.append({"division": div, "revenue": float(rev)})
        result.sort(key=lambda x: x["revenue"], reverse=True)
        return result

    def calculate_delivery_compliance(self, data: List[Dict]) -> List[Dict]:
        if not data:
            return self._empty_compliance()

        delivery_days = []
        for row in data:
            if row.get("pod_date") not in (None, "") and row.get("pgi_date") not in (None, ""):
                pgi = _parse_date(row.get("pgi_date"))
                pod = _parse_date(row.get("pod_date"))
                if pgi and pod:
                    days = (pod - pgi).days
                    if days >= 0:
                        delivery_days.append(days)

        if not delivery_days:
            return self._empty_compliance()

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

    def _empty_compliance(self) -> List[Dict]:
        return [
            {"distance": "0-100", "target_days": 1, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
            {"distance": "100-200", "target_days": 2, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
            {"distance": "200-300", "target_days": 3, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
            {"distance": "300-500", "target_days": 5, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
            {"distance": "500-1000", "target_days": 7, "actual_days": 0, "compliance_pct": 0, "status": "No Data"},
        ]

    def generate_critical_alerts(self, data: List[Dict]) -> List[Dict]:
        alerts = []
        if not data:
            return alerts

        pending_wh = {}
        for row in data:
            if row.get("pod_date") in (None, ""):
                wh = row.get("warehouse", "Unknown")
                pending_wh[wh] = pending_wh.get(wh, 0) + float(row.get("units", 0))
        for wh, units in pending_wh.items():
            if units > 5000:
                alerts.append({
                    "category": "Pending Units",
                    "source": wh,
                    "message": f"Warehouse {wh} has {units:,.0f} units pending, critical.",
                    "severity": "CRITICAL"
                })
            elif units > 1000:
                alerts.append({
                    "category": "Pending Units",
                    "source": wh,
                    "message": f"Warehouse {wh} has {units:,.0f} units pending, warning.",
                    "severity": "WARNING"
                })

        city_perf = self.calculate_city_performance(data, top_n=10)
        for c in city_perf:
            if c["status"] == "Critical":
                alerts.append({
                    "category": "Delivery Delay",
                    "source": c["city"],
                    "message": f"City {c['city']} avg delivery {c['avg_delivery_days']:.1f} days, critical.",
                    "severity": "CRITICAL"
                })
            elif c["status"] == "Warning":
                alerts.append({
                    "category": "Delivery Delay",
                    "source": c["city"],
                    "message": f"City {c['city']} avg delivery {c['avg_delivery_days']:.1f} days, warning.",
                    "severity": "WARNING"
                })

        wh_perf = self.calculate_warehouse_performance(data)
        for wh in wh_perf:
            if wh["pod_pct"] < 70:
                alerts.append({
                    "category": "Low POD Rate",
                    "source": wh["warehouse"],
                    "message": f"Warehouse {wh['warehouse']} POD rate {wh['pod_pct']:.1f}%, below 70%.",
                    "severity": "WARNING"
                })

        health = self.calculate_kpis(data)["health_score"]["value"]
        if health < 70:
            alerts.append({
                "category": "Health Score",
                "source": "Overall Logistics",
                "message": f"Health score {health:.1f}%, below acceptable.",
                "severity": "CRITICAL"
            })

        alerts.sort(key=lambda x: 0 if x["severity"] == "CRITICAL" else 1)
        return alerts[:10]

    def get_recommendations(self, data: List[Dict]) -> List[str]:
        if not data:
            return ["No data to generate recommendations."]

        recs = []
        pending_units = self.calculate_kpis(data)["pending_units"]["value"]
        if pending_units > 5000:
            recs.append("Urgently expedite dispatch of pending units.")

        pod_rate = self.calculate_kpis(data)["pod_achievement"]["value"]
        if pod_rate < 0.8:
            recs.append("Implement POD follow-up campaign.")

        city_perf = self.calculate_city_performance(data, top_n=3)
        for c in city_perf:
            if c["status"] in ["Critical", "Warning"]:
                recs.append(f"Review logistics routes for {c['city']}.")

        wh_perf = self.calculate_warehouse_performance(data)
        for wh in wh_perf[:3]:
            if wh["performance_score"] < 70:
                recs.append(f"Conduct operational review at {wh['warehouse']}.")

        if not recs:
            recs.append("All metrics are within acceptable ranges.")
        return recs[:5]

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
                    months[month_key] = {"dns": set(), "units": 0}
                months[month_key]["dns"].add(str(row.get("dn")))
                months[month_key]["units"] += float(row.get("units", 0))

        result = []
        for month, vals in sorted(months.items()):
            result.append({
                "month": month,
                "dn_count": len(vals["dns"]),
                "units": int(vals["units"]),
            })
        return result

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
            if not raw_data:
                result = self._empty_response("No data available. Please import an Excel file.")
            else:
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

            self._cache = result
            self._cache_time = time.time()
            return result

        except Exception as e:
            logger.error(f"❌ Dashboard generation error: {traceback.format_exc()}")
            return self._empty_response(f"Error: {str(e)}")

    def _empty_response(self, message: str) -> Dict[str, Any]:
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
                "conversion": 0,
            },
            "warehouse_ranking": [],
            "top_delayed_cities": [],
            "top_pending_warehouses": [],
            "top_dealers": [],
            "top_products": [],
            "division_performance": [],
            "delivery_compliance": self._empty_compliance(),
            "alerts": [],
            "recommendations": [message],
            "monthly_trend": [],
            "metadata": {
                "record_count": 0,
                "version": self._version,
                "execution_time_ms": 0,
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
