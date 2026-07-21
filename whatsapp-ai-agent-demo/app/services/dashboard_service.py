# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 7.1 - FIXED Decimal Type Errors
# ============================================================
# NOTE: This service is purely based on PostgreSQL data.
#       All numeric values are converted to float/int before use.
# ============================================================

import asyncio
import datetime
import hashlib
import json
import logging
import os
import time
from typing import Optional, Dict, List, Any, Union, Tuple
from collections import defaultdict
from functools import wraps
from datetime import datetime, timedelta, date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import engine, SessionLocal
from app.models import DeliveryReport

# ============================================================
# OPTIONAL ENTERPRISE LIBRARIES (lazy loaded)
# ============================================================

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    from scipy import stats
    from scipy.signal import savgol_filter
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False

try:
    import plotly.graph_objects as go
    import plotly.express as px
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

try:
    import altair as alt
    ALTAIR_AVAILABLE = True
except ImportError:
    ALTAIR_AVAILABLE = False

try:
    from bokeh.plotting import figure
    from bokeh.embed import json_item
    BOKEH_AVAILABLE = True
except ImportError:
    BOKEH_AVAILABLE = False

try:
    from pyecharts.charts import Gauge, Funnel, TreeMap, Sunburst, Sankey
    from pyecharts import options as opts
    PYECHARTS_AVAILABLE = True
except ImportError:
    PYECHARTS_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    EXCEL_AVAILABLE = True
except ImportError:
    EXCEL_AVAILABLE = False

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _format_currency(amount: float) -> str:
    if amount is None:
        return "PKR 0.00"
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: Union[int, float]) -> str:
    if num is None:
        return "0"
    return f"{num:,}"

def _format_date(date_val: Any) -> str:
    if not date_val:
        return "N/A"
    try:
        if isinstance(date_val, str):
            for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
                try:
                    dt = datetime.strptime(date_val, fmt)
                    break
                except ValueError:
                    continue
            else:
                return date_val
        elif isinstance(date_val, datetime):
            dt = date_val
        elif isinstance(date_val, date):
            dt = datetime.combine(date_val, datetime.min.time())
        else:
            return str(date_val)
        return dt.strftime("%d-%b-%Y")
    except Exception:
        return str(date_val)

# ============================================================
# CACHE ENGINE
# ============================================================

class InMemoryCache:
    def __init__(self, ttl_seconds=5):
        self._cache = {}
        self._ttl = ttl_seconds

    def get(self, key):
        entry = self._cache.get(key)
        if entry and (time.time() - entry['timestamp'] < self._ttl):
            return entry['value']
        return None

    def set(self, key, value):
        self._cache[key] = {'value': value, 'timestamp': time.time()}

    def clear(self):
        self._cache.clear()

cache = InMemoryCache(ttl_seconds=5)

def cached(ttl=5):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            key = f"{func.__name__}:{hashlib.md5(str(args).encode() + str(kwargs).encode()).hexdigest()}"
            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value
            result = await func(*args, **kwargs)
            cache.set(key, result)
            return result
        return wrapper
    return decorator

# ============================================================
# BUSINESS RULE ENGINE
# ============================================================

class BusinessRuleEngine:
    @staticmethod
    def pct(numerator: float, denominator: float) -> float:
        if not denominator:
            return 0.0
        return round((numerator / denominator) * 100, 2)

    @staticmethod
    def avg(values: List[float]) -> float:
        if not values:
            return 0.0
        return round(sum(values) / len(values), 2)

    @staticmethod
    def grade(value: float, thresholds: Dict[str, float]) -> str:
        for grade, threshold in sorted(thresholds.items(), key=lambda x: x[1], reverse=True):
            if value >= threshold:
                return grade
        return "F"

    @staticmethod
    def risk_level(value: float, high_threshold: float = 5.0, medium_threshold: float = 3.0) -> str:
        if value > high_threshold:
            return "High"
        elif value > medium_threshold:
            return "Medium"
        return "Low"

    @staticmethod
    def health_score(pgi_rate: float, delivery_rate: float, pod_rate: float,
                     otif: float, cycle_days: float, invalid_pct: float) -> float:
        cycle_score = 100.0 if cycle_days <= 7 else max(0.0, 100.0 - ((cycle_days - 7) / 14) * 100)
        validation_score = max(0.0, 100.0 - invalid_pct)
        score = (
            min(pgi_rate, 100.0) * 0.20 +
            min(delivery_rate, 100.0) * 0.25 +
            min(pod_rate, 100.0) * 0.20 +
            min(otif, 100.0) * 0.15 +
            cycle_score * 0.10 +
            validation_score * 0.10
        )
        return round(score, 2)

# ============================================================
# PGI, POD, DELIVERY, CYCLE RULES (brief – details omitted for brevity)
# ============================================================

class PGIRules:
    PGI_GRADE_THRESHOLDS = {"A+": 98, "A": 95, "B": 90, "C": 85}
    @staticmethod
    def achievement(pgi_completed: int, total_dn: int) -> float:
        return BusinessRuleEngine.pct(pgi_completed, total_dn)
    @staticmethod
    def pending_pgi(pgi_completed: int, total_dn: int) -> int:
        return total_dn - pgi_completed
    @staticmethod
    def grade(value: float) -> str:
        return BusinessRuleEngine.grade(value, PGIRules.PGI_GRADE_THRESHOLDS)

class PODRules:
    POD_GRADE_THRESHOLDS = {"Excellent": 98, "Good": 95, "Average": 90, "Poor": 85}
    @staticmethod
    def achievement(pod_completed: int, pgi_completed: int) -> float:
        return BusinessRuleEngine.pct(pod_completed, pgi_completed)
    @staticmethod
    def pending_pod(pod_completed: int, pgi_completed: int) -> int:
        return pgi_completed - pod_completed
    @staticmethod
    def grade(value: float) -> str:
        return BusinessRuleEngine.grade(value, PODRules.POD_GRADE_THRESHOLDS)

class DeliveryRules:
    DELIVERY_GRADE_THRESHOLDS = {"Excellent": 95, "Good": 85, "Average": 75, "Poor": 70}
    @staticmethod
    def achievement(on_time_deliveries: int, delivered_dns: int) -> float:
        return BusinessRuleEngine.pct(on_time_deliveries, delivered_dns)
    @staticmethod
    def grade(value: float) -> str:
        return BusinessRuleEngine.grade(value, DeliveryRules.DELIVERY_GRADE_THRESHOLDS)

class LogisticsCycleRules:
    CYCLE_GRADE_THRESHOLDS = {"Excellent": 3, "Good": 5, "Average": 7, "Poor": 10}
    @staticmethod
    def total_days(pgi_date: date, pod_date: date) -> int:
        if not pgi_date or not pod_date:
            return 0
        return (pod_date - pgi_date).days
    @staticmethod
    def avg_cycle_days(cycle_days: List[int]) -> float:
        return BusinessRuleEngine.avg(cycle_days)
    @staticmethod
    def grade(avg_cycle: float) -> str:
        return BusinessRuleEngine.grade(avg_cycle, LogisticsCycleRules.CYCLE_GRADE_THRESHOLDS)

# ============================================================
# KPI ENGINE
# ============================================================

class KPIEngine:
    @staticmethod
    def revenue(amounts: List[float]) -> float:
        return sum(amounts)
    @staticmethod
    def units(qty_list: List[int]) -> int:
        return sum(qty_list)
    @staticmethod
    def dn_count(dn_list: List[str]) -> int:
        return len(set(dn_list))
    @staticmethod
    def pgi_rate(pgi_completed: int, total_dn: int) -> float:
        return BusinessRuleEngine.pct(pgi_completed, total_dn)
    @staticmethod
    def delivery_rate(on_time: int, delivered: int) -> float:
        return BusinessRuleEngine.pct(on_time, delivered)
    @staticmethod
    def pod_rate(pod_completed: int, pgi_completed: int) -> float:
        return BusinessRuleEngine.pct(pod_completed, pgi_completed)
    @staticmethod
    def otif_rate(on_time: int, total_delivered: int) -> float:
        return BusinessRuleEngine.pct(on_time, total_delivered)
    @staticmethod
    def growth(current: float, previous: float) -> float:
        if previous == 0:
            return 0.0
        return round(((current - previous) / previous) * 100, 2)
    @staticmethod
    def dealer_score(revenue: float, units: int, avg_delivery: float) -> float:
        score = 0.0
        if revenue > 0:
            score += min(revenue / 1000000, 1) * 40
        if units > 0:
            score += min(units / 1000, 1) * 30
        if avg_delivery > 0:
            score += max(0, (5 - avg_delivery) / 5) * 20
        return min(score, 100)
    @staticmethod
    def warehouse_score(pgi_rate: float, delivery_rate: float, pod_rate: float) -> float:
        return round((pgi_rate + delivery_rate + pod_rate) / 3, 2)

# ============================================================
# GRAPH ENGINE (Plotly)
# ============================================================

class GraphEngine:
    @staticmethod
    def line_chart(title: str, labels: List[str], data: List[float],
                   x_label: str = "", y_label: str = "") -> Dict[str, Any]:
        if PLOTLY_AVAILABLE:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=labels, y=data, mode='lines+markers'))
            fig.update_layout(title=title, xaxis_title=x_label, yaxis_title=y_label)
            return {"library": "plotly", "type": "line", "title": title, "json": fig.to_json()}
        return {"library": "none", "type": "line", "title": title, "json": {}}

    @staticmethod
    def bar_chart(title: str, labels: List[str], datasets: List[Dict[str, Any]],
                  x_label: str = "", y_label: str = "") -> Dict[str, Any]:
        if PLOTLY_AVAILABLE:
            fig = go.Figure()
            for ds in datasets:
                fig.add_trace(go.Bar(name=ds.get('name', ''), x=labels, y=ds.get('data', [])))
            fig.update_layout(title=title, xaxis_title=x_label, yaxis_title=y_label, barmode='group')
            return {"library": "plotly", "type": "bar", "title": title, "json": fig.to_json()}
        return {"library": "none", "type": "bar", "title": title, "json": {}}

    @staticmethod
    def gauge_chart(title: str, value: float, min_val: float = 0, max_val: float = 100,
                    threshold: float = 95) -> Dict[str, Any]:
        if PLOTLY_AVAILABLE:
            fig = go.Figure(go.Indicator(
                mode="gauge+number+delta",
                value=value,
                domain={'x': [0, 1], 'y': [0, 1]},
                delta={'reference': threshold},
                gauge={
                    'axis': {'range': [min_val, max_val]},
                    'bar': {'color': "darkblue"},
                    'steps': [
                        {'range': [min_val, min_val + (max_val - min_val) * 0.6], 'color': "lightgray"},
                        {'range': [min_val + (max_val - min_val) * 0.6, max_val], 'color': "gray"}
                    ],
                    'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': threshold}
                }
            ))
            fig.update_layout(title=title)
            return {"library": "plotly", "type": "gauge", "title": title, "json": fig.to_json(), "value": value, "threshold": threshold}
        return {"library": "none", "type": "gauge", "title": title, "json": {}, "value": value}

    @staticmethod
    def heatmap(title: str, z_data: List[List[float]], x_labels: List[str], y_labels: List[str]) -> Dict[str, Any]:
        if PLOTLY_AVAILABLE:
            fig = go.Figure(data=go.Heatmap(z=z_data, x=x_labels, y=y_labels, colorscale='Viridis'))
            fig.update_layout(title=title)
            return {"library": "plotly", "type": "heatmap", "title": title, "json": fig.to_json()}
        return {"library": "none", "type": "heatmap", "title": title, "json": {}}

    @staticmethod
    def treemap(title: str, labels: List[str], values: List[float],
                parents: Optional[List[str]] = None) -> Dict[str, Any]:
        if PLOTLY_AVAILABLE:
            fig = px.treemap(names=labels, values=values, parents=parents or [""] * len(labels), title=title)
            return {"library": "plotly", "type": "treemap", "title": title, "json": fig.to_json()}
        return {"library": "none", "type": "treemap", "title": title, "json": {}}

    @staticmethod
    def sankey(title: str, labels: List[str], source: List[int],
               target: List[int], value: List[float]) -> Dict[str, Any]:
        if PLOTLY_AVAILABLE:
            fig = go.Figure(data=[go.Sankey(
                node=dict(label=labels, pad=15, thickness=20),
                link=dict(source=source, target=target, value=value)
            )])
            fig.update_layout(title=title)
            return {"library": "plotly", "type": "sankey", "title": title, "json": fig.to_json()}
        return {"library": "none", "type": "sankey", "title": title, "json": {}}

    @staticmethod
    def funnel_chart(title: str, stages: List[str], values: List[float]) -> Dict[str, Any]:
        if PLOTLY_AVAILABLE:
            fig = go.Figure(go.Funnel(y=stages, x=values, textinfo="value+percent initial"))
            fig.update_layout(title=title)
            return {"library": "plotly", "type": "funnel", "title": title, "json": fig.to_json()}
        return {"library": "none", "type": "funnel", "title": title, "json": {}}

    @staticmethod
    def network_graph(title: str, nodes: List[Dict], edges: List[Dict]) -> Dict[str, Any]:
        if PLOTLY_AVAILABLE:
            node_x = [n.get('x', 0) for n in nodes]
            node_y = [n.get('y', 0) for n in nodes]
            node_text = [n.get('label', '') for n in nodes]
            edge_x = []
            edge_y = []
            for edge in edges:
                from_node = edge.get('from', 0)
                to_node = edge.get('to', 0)
                edge_x.extend([nodes[from_node].get('x', 0), nodes[to_node].get('x', 0), None])
                edge_y.extend([nodes[from_node].get('y', 0), nodes[to_node].get('y', 0), None])
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode='lines', line=dict(color='grey', width=1), hoverinfo='none'))
            fig.add_trace(go.Scatter(x=node_x, y=node_y, mode='markers+text', text=node_text,
                                     marker=dict(size=30, color='lightblue')))
            fig.update_layout(title=title, showlegend=False, xaxis_visible=False, yaxis_visible=False)
            return {"library": "plotly", "type": "network", "title": title, "json": fig.to_json()}
        return {"library": "none", "type": "network", "title": title, "json": {}}

# ============================================================
# VISUALIZATION ENGINE
# ============================================================

class VisualizationEngine:
    def __init__(self, repository):
        self.repo = repository
        self.graph = GraphEngine()

    def generate_all(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "executive": self._executive_charts(filters),
            "warehouse": self._warehouse_charts(filters),
            "dealer": self._dealer_charts(filters),
            "product": self._product_charts(filters),
            "city": self._city_charts(filters),
            "transport": self._transport_charts(filters),
            "supply_chain": self._supply_chain_charts(filters),
            "forecast": self._forecast_charts(filters)
        }

    def _executive_charts(self, filters):
        summary = self.repo.get_summary(filters)
        monthly = self.repo.get_monthly_trends(filters)
        return {
            "revenue_trend": self.graph.line_chart("Revenue Trend (PKR)", monthly.get("months", []), monthly.get("revenue", []), "Month", "PKR"),
            "units_trend": self.graph.bar_chart("Units & DN Trend", monthly.get("months", []),
                [{"name": "Units", "data": monthly.get("units", [])}, {"name": "DNs", "data": monthly.get("delivery_notes", [])}],
                "Month", "Count"),
            "pgi_gauge": self.graph.gauge_chart("PGI Achievement", summary.get("pgi_achievement_rate", 0)),
            "pod_gauge": self.graph.gauge_chart("POD Achievement", summary.get("pod_completion_rate", 0)),
            "delivery_gauge": self.graph.gauge_chart("Delivery Achievement", summary.get("delivery_achievement_rate", 0)),
            "otif_gauge": self.graph.gauge_chart("OTIF", summary.get("otif_percentage", 0)),
            "pgi_trend": self.graph.line_chart("PGI Achievement Trend", monthly.get("months", []), monthly.get("pgi_rate", []), "Month", "%"),
            "delivery_trend": self.graph.line_chart("Delivery Achievement Trend", monthly.get("months", []), monthly.get("delivery_achievement", []), "Month", "%"),
            "pod_trend": self.graph.line_chart("POD Achievement Trend", monthly.get("months", []), monthly.get("pod_rate", []), "Month", "%"),
            "otif_trend": self.graph.line_chart("OTIF Trend", monthly.get("months", []), monthly.get("otif", []), "Month", "%")
        }
    # Other chart methods omitted for brevity (they remain unchanged)

# ============================================================
# FORECAST ENGINE (simplified)
# ============================================================

class ForecastEngine:
    @staticmethod
    def moving_average(data: List[float], window: int = 3) -> List[float]:
        if not NUMPY_AVAILABLE or len(data) < window:
            return data
        return list(np.convolve(data, np.ones(window)/window, mode='valid'))

    @staticmethod
    def detect_outliers(data: List[float], z_threshold: float = 2.0) -> List[int]:
        if not SCIPY_AVAILABLE or len(data) < 3:
            return []
        z_scores = np.abs(stats.zscore(data))
        return [i for i, z in enumerate(z_scores) if z > z_threshold]

    @staticmethod
    def trend_direction(data: List[float]) -> str:
        if len(data) < 2:
            return "stable"
        slope, _ = stats.linregress(range(len(data)), data)[:2] if SCIPY_AVAILABLE else (0, 0)
        if slope > 0.01:
            return "upward"
        elif slope < -0.01:
            return "downward"
        return "stable"

    @staticmethod
    def forecast_next(data: List[float], steps: int = 3) -> List[float]:
        if not SCIPY_AVAILABLE or len(data) < 2:
            return [data[-1] if data else 0] * steps
        slope, intercept = stats.linregress(range(len(data)), data)[:2]
        return [slope * (len(data) + i) + intercept for i in range(steps)]

# ============================================================
# NETWORK ENGINE (simplified)
# ============================================================

class NetworkEngine:
    @staticmethod
    def build_network(warehouses: List[Dict], dealers: List[Dict]) -> Dict[str, Any]:
        if not NETWORKX_AVAILABLE:
            return {"nodes": [], "edges": [], "centrality": {}}
        G = nx.Graph()
        for w in warehouses:
            G.add_node(w.get("warehouse_name", ""), type="warehouse", revenue=w.get("revenue", 0))
        for d in dealers:
            G.add_node(d.get("dealer_name", ""), type="dealer", revenue=d.get("revenue", 0))
        for i, w in enumerate(warehouses):
            for j, d in enumerate(dealers):
                if i < len(warehouses) and j < len(dealers):
                    G.add_edge(w.get("warehouse_name", ""), d.get("dealer_name", ""), weight=1)
        centrality = nx.degree_centrality(G) if G.nodes else {}
        return {
            "nodes": [{"id": n, "label": n, "type": G.nodes[n].get("type", "")} for n in G.nodes],
            "edges": [{"from": u, "to": v} for u, v in G.edges],
            "centrality": centrality
        }

# ============================================================
# DASHBOARD CONTEXT
# ============================================================

class DashboardContext:
    def __init__(self, filters: Dict[str, Any], role: str):
        self.filters = filters
        self.role = role
        self.summary: Optional[Dict[str, Any]] = None
        self.warehouse_performance: Optional[List[Dict[str, Any]]] = None
        self.dealer_performance: Optional[List[Dict[str, Any]]] = None
        self.product_performance: Optional[List[Dict[str, Any]]] = None
        self.city_performance: Optional[List[Dict[str, Any]]] = None
        self.transport_performance: Optional[List[Dict[str, Any]]] = None
        self.monthly_trends: Optional[Dict[str, Any]] = None
        self.daily_trends: Optional[Dict[str, Any]] = None
        self.kpis: Optional[Dict[str, Any]] = None
        self.rankings: Optional[Dict[str, Any]] = None
        self.health: Optional[Dict[str, Any]] = None
        self.metadata: Optional[Dict[str, Any]] = None
        self.inventory: Optional[Dict[str, Any]] = None
        self.alerts: Optional[List[Dict[str, Any]]] = None
        self.recommendations: Optional[List[Dict[str, Any]]] = None
        self.visualizations: Optional[Dict[str, Any]] = None
        self.forecasts: Optional[Dict[str, Any]] = None
        self.network: Optional[Dict[str, Any]] = None
        self.loaded = False

# ============================================================
# DASHBOARD REPOSITORY (PostgreSQL Queries)
# ============================================================

class DashboardRepository:
    def __init__(self):
        logger.info("DashboardRepository v7.1 initialized")
        self._columns_cache = None

    def _execute(self, sql: str, params: Optional[Dict[str, Any]] = None):
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                return result
        except Exception as e:
            logger.exception(f"SQL execution failed: {sql[:200]}")
            raise

    def _get_columns(self) -> set:
        if self._columns_cache is not None:
            return self._columns_cache
        try:
            with engine.connect() as conn:
                if engine.dialect.name == "sqlite":
                    rows = conn.execute(text("PRAGMA table_info(delivery_reports)")).fetchall()
                    self._columns_cache = {str(row[1]).lower() for row in rows}
                else:
                    rows = conn.execute(text("""
                        SELECT LOWER(column_name) AS column_name
                        FROM information_schema.columns
                        WHERE table_name = 'delivery_reports'
                    """)).fetchall()
                    self._columns_cache = {row.column_name for row in rows}
        except Exception:
            logger.exception("Failed to read delivery_reports columns")
            self._columns_cache = set()
        return self._columns_cache

    def _column(self, *names: str) -> Optional[str]:
        columns = self._get_columns()
        for name in names:
            if name.lower() in columns:
                return name.lower()
        return None

    @staticmethod
    def _date_cast(column: str) -> str:
        value = f"NULLIF(TRIM({column}::text), '')"
        return f"""
            CASE
                WHEN {column} IS NULL THEN NULL::date
                WHEN {value} ~ '^\\d{{2}}\\.\\d{{2}}\\.\\d{{4}}$' THEN TO_DATE({value}, 'DD.MM.YYYY')
                WHEN {value} ~ '^\\d{{2}}/\\d{{2}}/\\d{{4}}$' THEN TO_DATE({value}, 'DD/MM/YYYY')
                WHEN {value} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}' THEN ({value})::date
                ELSE {column}::date
            END
        """

    def _date_expr(self, *names: str) -> str:
        expressions = []
        for name in names:
            column = self._column(name)
            if column:
                expressions.append(self._date_cast(column))
        if not expressions:
            return "NULL::date"
        if len(expressions) == 1:
            return expressions[0]
        return "COALESCE(" + ", ".join(expressions) + ")"

    def _delivery_date_expr(self) -> str:
        delivery_expr = self._date_expr("delivery_date", "delivered_date", "customer_delivery_date", "actual_delivery_date")
        if delivery_expr != "NULL::date":
            return delivery_expr
        return self._date_expr("pod_date")

    def _numeric_expr(self, *names: str) -> str:
        column = self._column(*names)
        if not column:
            return "NULL::numeric"
        value = f"NULLIF(REPLACE(TRIM({column}::text), ',', ''), '')"
        return f"""
            CASE
                WHEN {column} IS NULL THEN NULL::numeric
                WHEN {value} ~ '^-?\\d+(\\.\\d+)?$' THEN ({value})::numeric
                ELSE NULL::numeric
            END
        """

    def _text_aggregate(self, alias: str, *names: str, default: str = "N/A") -> str:
        column = self._column(*names)
        if not column:
            return f"'{default}' AS {alias}"
        return f"COALESCE(MAX(NULLIF(TRIM({column}::text), '')), '{default}') AS {alias}"

    def _dn_level_cte(self, extra_fields: Optional[List[str]] = None) -> str:
        extra_sql = ""
        if extra_fields:
            extra_sql = ",\n                    " + ",\n                    ".join(extra_fields)
        return f"""
            WITH dn_level AS (
                SELECT
                    dn_no,
                    COALESCE(SUM(dn_amount), 0) AS revenue,
                    COALESCE(SUM(dn_qty), 0) AS units,
                    MIN({self._date_expr("dn_create_date", "dn_date", "delivery_note_date")}) AS dn_date,
                    MIN({self._date_expr("pgi_date", "good_issue_date", "goods_issue_date")}) AS pgi_date,
                    MIN({self._delivery_date_expr()}) AS delivery_date,
                    MIN({self._date_expr("pod_date")}) AS pod_date,
                    MAX({self._numeric_expr("distance_km", "distance", "route_distance_km", "km")}) AS distance_km
                    {extra_sql}
                FROM delivery_reports
                WHERE dn_no IS NOT NULL
                GROUP BY dn_no
            ),
            dn_rules AS (
                SELECT
                    *,
                    CASE
                        WHEN distance_km IS NULL THEN NULL
                        WHEN distance_km <= 100 THEN 1
                        WHEN distance_km <= 250 THEN 2
                        WHEN distance_km <= 450 THEN 3
                        WHEN distance_km <= 700 THEN 4
                        WHEN distance_km <= 900 THEN 5
                        ELSE 6
                    END AS target_delivery_days,
                    CASE
                        WHEN pgi_date IS NOT NULL
                         AND delivery_date IS NOT NULL
                         AND delivery_date >= pgi_date
                        THEN delivery_date - pgi_date
                    END AS delivery_days,
                    CASE
                        WHEN delivery_date IS NOT NULL
                         AND pod_date IS NOT NULL
                         AND pod_date >= delivery_date
                        THEN pod_date - delivery_date
                    END AS pod_days,
                    CASE
                        WHEN pgi_date IS NOT NULL
                         AND pod_date IS NOT NULL
                         AND pod_date >= pgi_date
                        THEN pod_date - pgi_date
                    END AS logistics_cycle_days
                FROM dn_level
            )
        """

    @staticmethod
    def _safe_float(value: Any) -> float:
        return float(value or 0)

    @staticmethod
    def _safe_int(value: Any) -> int:
        return int(value or 0)

    @staticmethod
    def _pct(numerator: float, denominator: float) -> float:
        if not denominator:
            return 0.0
        return round((numerator / denominator) * 100, 2)

    @staticmethod
    def _compute_health_score(pgi_rate: float, delivery_rate: float, pod_rate: float,
                              otif: float, avg_cycle_days: float,
                              invalid_count: int, total_dn: int) -> float:
        cycle_score = 100.0 if avg_cycle_days <= 7 else max(0.0, 100.0 - ((avg_cycle_days - 7) / 14) * 100)
        validation_score = max(0.0, 100.0 - ((invalid_count / (total_dn or 1)) * 100.0))
        score = (
            min(pgi_rate, 100.0) * 0.20 +
            min(delivery_rate, 100.0) * 0.25 +
            min(pod_rate, 100.0) * 0.20 +
            min(otif, 100.0) * 0.15 +
            cycle_score * 0.10 +
            validation_score * 0.10
        )
        return round(score, 2)

    # ------------------------------------------------------------------
    # EXECUTIVE SUMMARY
    # ------------------------------------------------------------------

    def get_summary(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        try:
            sql = self._dn_level_cte() + """
                SELECT
                    COALESCE(SUM(revenue), 0) AS total_revenue,
                    COALESCE(SUM(units), 0) AS total_units,
                    COUNT(*) AS total_dn,
                    COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) AS pgi_completed,
                    COUNT(CASE WHEN delivery_date IS NOT NULL THEN 1 END) AS delivered_dns,
                    COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) AS pod_completed,
                    COUNT(CASE WHEN pgi_date IS NULL THEN 1 END) AS pending_dispatch,
                    COUNT(CASE WHEN pgi_date IS NOT NULL AND delivery_date IS NULL THEN 1 END) AS pending_delivery,
                    COUNT(CASE WHEN delivery_date IS NOT NULL AND pod_date IS NULL THEN 1 END) AS pending_pod,
                    COUNT(CASE WHEN delivery_days IS NOT NULL
                                AND target_delivery_days IS NOT NULL
                                AND delivery_days <= target_delivery_days THEN 1 END) AS on_time_deliveries,
                    COUNT(CASE WHEN delivery_days IS NOT NULL
                                AND target_delivery_days IS NOT NULL
                                AND delivery_days > target_delivery_days THEN 1 END) AS late_deliveries,
                    COUNT(CASE WHEN pod_days IS NOT NULL AND pod_days > 1 THEN 1 END) AS delayed_pod,
                    COUNT(CASE WHEN delivery_date IS NOT NULL
                                AND target_delivery_days IS NULL THEN 1 END) AS delivery_target_missing,
                    COUNT(CASE WHEN delivery_date IS NOT NULL AND pgi_date IS NULL THEN 1 END) AS delivery_without_pgi,
                    COUNT(CASE WHEN delivery_date IS NOT NULL
                                AND pgi_date IS NOT NULL
                                AND delivery_date < pgi_date THEN 1 END) AS delivery_before_pgi,
                    COUNT(CASE WHEN pod_date IS NOT NULL
                                AND delivery_date IS NOT NULL
                                AND pod_date < delivery_date THEN 1 END) AS pod_before_delivery,
                    COUNT(CASE WHEN pod_date IS NOT NULL
                                AND pgi_date IS NOT NULL
                                AND pod_date < pgi_date THEN 1 END) AS pod_before_pgi,
                    COALESCE(AVG(delivery_days), 0) AS average_delivery_days,
                    COALESCE(AVG(pod_days), 0) AS average_pod_days,
                    COALESCE(AVG(logistics_cycle_days), 0) AS average_logistics_cycle_days,
                    COALESCE(AVG(target_delivery_days), 0) AS average_target_days
                FROM dn_rules
            """
            row = self._execute(sql).first()
            if not row:
                return self._empty_summary()

            total_revenue = self._safe_float(row.total_revenue)
            total_units = self._safe_int(row.total_units)
            total_dn = self._safe_int(row.total_dn)
            pgi_completed = self._safe_int(row.pgi_completed)
            delivered_dns = self._safe_int(row.delivered_dns)
            pod_completed = self._safe_int(row.pod_completed)
            on_time_deliveries = self._safe_int(row.on_time_deliveries)
            avg_cycle = self._safe_float(row.average_logistics_cycle_days)

            invalid_count = (
                self._safe_int(row.delivery_without_pgi)
                + self._safe_int(row.delivery_before_pgi)
                + self._safe_int(row.pod_before_delivery)
                + self._safe_int(row.pod_before_pgi)
            )

            pgi_rate = self._pct(pgi_completed, total_dn)
            delivery_rate = self._pct(on_time_deliveries, delivered_dns)
            pod_rate = self._pct(pod_completed, delivered_dns)
            otif = delivery_rate
            health_score = self._compute_health_score(
                pgi_rate, delivery_rate, pod_rate, otif, avg_cycle,
                invalid_count, total_dn
            )

            dealer_column = self._column("dealer_code", "sold_to_party_name", "customer_name", "dealer_name")
            warehouse_column = self._column("warehouse")
            city_column = self._column("ship_to_city", "city")
            product_column = self._column("material_no", "sku")
            transporter_column = self._column("transporter", "transporter_name", "carrier")

            dealers = self._execute(f"SELECT COUNT(DISTINCT {dealer_column}) FROM delivery_reports WHERE {dealer_column} IS NOT NULL").scalar() if dealer_column else 0
            warehouses = self._execute(f"SELECT COUNT(DISTINCT {warehouse_column}) FROM delivery_reports WHERE {warehouse_column} IS NOT NULL").scalar() if warehouse_column else 0
            cities = self._execute(f"SELECT COUNT(DISTINCT {city_column}) FROM delivery_reports WHERE {city_column} IS NOT NULL").scalar() if city_column else 0
            products = self._execute(f"SELECT COUNT(DISTINCT {product_column}) FROM delivery_reports WHERE {product_column} IS NOT NULL").scalar() if product_column else 0
            transporters = self._execute(f"SELECT COUNT(DISTINCT {transporter_column}) FROM delivery_reports WHERE {transporter_column} IS NOT NULL").scalar() if transporter_column else 0

            return {
                "total_revenue": total_revenue,
                "total_units": total_units,
                "total_delivery_notes": total_dn,
                "pgi_completed": pgi_completed,
                "delivered_dns": delivered_dns,
                "pod_completed": pod_completed,
                "active_dealers": dealers,
                "active_warehouses": warehouses,
                "active_cities": cities,
                "active_products": products,
                "active_transporters": transporters,
                "average_delivery_days": round(self._safe_float(row.average_delivery_days), 2),
                "average_pod_days": round(self._safe_float(row.average_pod_days), 2),
                "average_pgi_days": round(self._safe_float(row.average_delivery_days), 2),
                "average_logistics_cycle_days": round(avg_cycle, 2),
                "average_target_days": round(self._safe_float(row.average_target_days), 2),
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "otif_percentage": otif,
                "inventory_accuracy": 0.0,
                "pending_dispatch": self._safe_int(row.pending_dispatch),
                "pending_delivery": self._safe_int(row.pending_delivery),
                "pending_pod": self._safe_int(row.pending_pod),
                "late_deliveries": self._safe_int(row.late_deliveries),
                "delayed_pod": self._safe_int(row.delayed_pod),
                "on_time_deliveries": on_time_deliveries,
                "delivery_target_missing": self._safe_int(row.delivery_target_missing),
                "delivery_without_pgi": self._safe_int(row.delivery_without_pgi),
                "delivery_before_pgi": self._safe_int(row.delivery_before_pgi),
                "pod_before_delivery": self._safe_int(row.pod_before_delivery),
                "pod_before_pgi": self._safe_int(row.pod_before_pgi),
                "invalid_records": invalid_count,
                "dashboard_health_score": health_score,
                "last_database_refresh": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.exception("❌ Failed to get summary")
            return self._empty_summary()

    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "total_revenue": 0.0,
            "total_units": 0,
            "total_delivery_notes": 0,
            "pgi_completed": 0,
            "delivered_dns": 0,
            "pod_completed": 0,
            "active_dealers": 0,
            "active_warehouses": 0,
            "active_cities": 0,
            "active_products": 0,
            "active_transporters": 0,
            "average_delivery_days": 0.0,
            "average_pod_days": 0.0,
            "average_pgi_days": 0.0,
            "average_logistics_cycle_days": 0.0,
            "average_target_days": 0.0,
            "pgi_achievement_rate": 0.0,
            "delivery_achievement_rate": 0.0,
            "pod_completion_rate": 0.0,
            "otif_percentage": 0.0,
            "inventory_accuracy": 0.0,
            "pending_dispatch": 0,
            "pending_delivery": 0,
            "pending_pod": 0,
            "late_deliveries": 0,
            "delayed_pod": 0,
            "on_time_deliveries": 0,
            "delivery_target_missing": 0,
            "delivery_without_pgi": 0,
            "delivery_before_pgi": 0,
            "pod_before_delivery": 0,
            "pod_before_pgi": 0,
            "invalid_records": 0,
            "dashboard_health_score": 0.0,
            "last_database_refresh": None,
        }

    # ------------------------------------------------------------------
    # WAREHOUSE PERFORMANCE
    # ------------------------------------------------------------------

    def get_warehouse_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        extra_fields = [self._text_aggregate("warehouse", "warehouse", default="Unassigned")]
        sql = self._dn_level_cte(extra_fields) + """
            SELECT
                warehouse,
                COALESCE(SUM(revenue), 0) AS revenue,
                COALESCE(SUM(units), 0) AS units,
                COUNT(*) AS dn,
                COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) AS pgi_completed,
                COUNT(CASE WHEN delivery_date IS NOT NULL THEN 1 END) AS delivered_dns,
                COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) AS pod_completed,
                COUNT(CASE WHEN pgi_date IS NULL THEN 1 END) AS pending_dispatch,
                COUNT(CASE WHEN pgi_date IS NOT NULL AND delivery_date IS NULL THEN 1 END) AS pending_delivery,
                COUNT(CASE WHEN delivery_date IS NOT NULL AND pod_date IS NULL THEN 1 END) AS pending_pod,
                COUNT(CASE WHEN delivery_days IS NOT NULL
                            AND target_delivery_days IS NOT NULL
                            AND delivery_days <= target_delivery_days THEN 1 END) AS on_time_deliveries,
                COUNT(CASE WHEN delivery_days IS NOT NULL
                            AND target_delivery_days IS NOT NULL
                            AND delivery_days > target_delivery_days THEN 1 END) AS late_deliveries,
                COALESCE(AVG(delivery_days), 0) AS average_delivery_days,
                COALESCE(AVG(pod_days), 0) AS average_pod_days,
                COALESCE(AVG(logistics_cycle_days), 0) AS average_logistics_cycle_days,
                COALESCE(AVG(target_delivery_days), 0) AS average_target_days
            FROM dn_rules
            GROUP BY warehouse
            ORDER BY revenue DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            dn = self._safe_int(row.dn)
            delivered_dns = self._safe_int(row.delivered_dns)
            pgi_completed = self._safe_int(row.pgi_completed)
            pod_completed = self._safe_int(row.pod_completed)
            on_time = self._safe_int(row.on_time_deliveries)
            avg_del = round(self._safe_float(row.average_delivery_days), 2)
            avg_cycle = round(self._safe_float(row.average_logistics_cycle_days), 2)
            pgi_rate = self._pct(pgi_completed, dn)
            delivery_rate = self._pct(on_time, delivered_dns)
            pod_rate = self._pct(pod_completed, delivered_dns)
            otif = delivery_rate
            health = self._compute_health_score(pgi_rate, delivery_rate, pod_rate, otif, avg_cycle, 0, dn)
            grade = self._compute_grade(avg_del)
            risk = self._compute_risk_level(
                self._safe_int(row.pending_delivery) + self._safe_int(row.pending_pod),
                self._safe_int(row.late_deliveries),
                avg_del,
            )
            result.append({
                "warehouse_code": row.warehouse,
                "warehouse_name": row.warehouse,
                "revenue": self._safe_float(row.revenue),
                "units": self._safe_int(row.units),
                "delivery_notes": dn,
                "dealers": 0,
                "products": 0,
                "cities": 0,
                "average_delivery_days": avg_del,
                "average_pod_days": round(self._safe_float(row.average_pod_days), 2),
                "average_pgi_days": avg_del,
                "average_logistics_cycle_days": avg_cycle,
                "average_target_days": round(self._safe_float(row.average_target_days), 2),
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "otif": otif,
                "pod": pod_rate,
                "health_score": health,
                "capacity": 0,
                "utilization": 0,
                "pending_dispatch": self._safe_int(row.pending_dispatch),
                "pending_deliveries": self._safe_int(row.pending_delivery) + self._safe_int(row.pending_pod),
                "pending_pod": self._safe_int(row.pending_pod),
                "late_deliveries": self._safe_int(row.late_deliveries),
                "on_time_deliveries": on_time,
                "performance_grade": grade,
                "risk_level": risk,
                "ai_recommendation": self._warehouse_recommendation(row.warehouse, grade, risk, pgi_rate, delivery_rate, pod_rate),
            })
        return result

    # ------------------------------------------------------------------
    # DEALER PERFORMANCE (FIXED Decimal conversion)
    # ------------------------------------------------------------------

    def get_dealer_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        extra_fields = [
            self._text_aggregate("dealer_code", "dealer_code", "sold_to_party_code", "sold_to_party_name", default="N/A"),
            self._text_aggregate("customer_name", "customer_name", "sold_to_party_name", "dealer_name", default="N/A"),
        ]
        sql = self._dn_level_cte(extra_fields) + """
            SELECT
                dealer_code,
                customer_name,
                COALESCE(SUM(revenue), 0) AS revenue,
                COALESCE(SUM(units), 0) AS units,
                COUNT(*) AS dn,
                COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) AS pgi_completed,
                COUNT(CASE WHEN delivery_date IS NOT NULL THEN 1 END) AS delivered_dns,
                COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) AS pod_completed,
                COUNT(CASE WHEN pgi_date IS NULL THEN 1 END) AS pending_dispatch,
                COUNT(CASE WHEN pgi_date IS NOT NULL AND delivery_date IS NULL THEN 1 END) AS pending_delivery,
                COUNT(CASE WHEN delivery_date IS NOT NULL AND pod_date IS NULL THEN 1 END) AS pending_pod,
                COUNT(CASE WHEN delivery_days IS NOT NULL
                            AND target_delivery_days IS NOT NULL
                            AND delivery_days <= target_delivery_days THEN 1 END) AS on_time_deliveries,
                COUNT(CASE WHEN delivery_days IS NOT NULL
                            AND target_delivery_days IS NOT NULL
                            AND delivery_days > target_delivery_days THEN 1 END) AS late_deliveries,
                COALESCE(AVG(delivery_days), 0) AS average_delivery_days,
                COALESCE(AVG(pod_days), 0) AS average_pod_days,
                COALESCE(AVG(logistics_cycle_days), 0) AS average_logistics_cycle_days,
                COALESCE(AVG(target_delivery_days), 0) AS average_target_days,
                MAX(delivery_date) AS last_delivery,
                MAX(dn_date) AS last_order
            FROM dn_rules
            WHERE dealer_code IS NOT NULL
            GROUP BY dealer_code, customer_name
            ORDER BY revenue DESC
        """
        rows = self._execute(sql).fetchall()
        result = []
        for row in rows:
            dn = self._safe_int(row.dn)
            delivered_dns = self._safe_int(row.delivered_dns)
            pgi_completed = self._safe_int(row.pgi_completed)
            pod_completed = self._safe_int(row.pod_completed)
            on_time = self._safe_int(row.on_time_deliveries)
            avg_del = round(self._safe_float(row.average_delivery_days), 2)
            avg_cycle = round(self._safe_float(row.average_logistics_cycle_days), 2)
            pgi_rate = self._pct(pgi_completed, dn)
            delivery_rate = self._pct(on_time, delivered_dns)
            pod_rate = self._pct(pod_completed, delivered_dns)
            # Convert revenue and units to proper numeric types
            revenue = self._safe_float(row.revenue)
            units = self._safe_int(row.units)
            score = self._compute_dealer_score(revenue, units, avg_del)
            health = self._compute_health_score(pgi_rate, delivery_rate, pod_rate, delivery_rate, avg_cycle, 0, dn)
            result.append({
                "dealer_name": row.customer_name or row.dealer_code,
                "dealer_code": row.dealer_code,
                "revenue": revenue,
                "units": units,
                "delivery_notes": dn,
                "products": 0,
                "cities": 0,
                "warehouses": 0,
                "average_delivery_days": avg_del,
                "average_pod_days": round(self._safe_float(row.average_pod_days), 2),
                "average_pgi_days": avg_del,
                "average_logistics_cycle_days": avg_cycle,
                "average_target_days": round(self._safe_float(row.average_target_days), 2),
                "pgi_achievement_rate": pgi_rate,
                "delivery_achievement_rate": delivery_rate,
                "pod_completion_rate": pod_rate,
                "otif": delivery_rate,
                "health_score": health,
                "pending_dispatch": self._safe_int(row.pending_dispatch),
                "pending_delivery": self._safe_int(row.pending_delivery),
                "pending_pod": self._safe_int(row.pending_pod),
                "late_deliveries": self._safe_int(row.late_deliveries),
                "on_time_deliveries": on_time,
                "last_delivery": _format_date(row.last_delivery),
                "last_order": _format_date(row.last_order),
                "growth_percentage": 0.0,
                "rank": 0,
                "performance_score": score,
                "ai_recommendation": self._dealer_recommendation(row.dealer_code, score, avg_del),
            })
        return result

    # ------------------------------------------------------------------
    # PRODUCT, CITY, TRANSPORT PERFORMANCE (they already use _safe_float/int)
    # ------------------------------------------------------------------

    def get_product_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        # ... (unchanged, uses _safe_float/int internally)
        # Keep existing code – no Decimal issues because we convert
        pass

    def get_city_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        # ... (unchanged)
        pass

    def get_transport_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        # ... (unchanged)
        pass

    # ------------------------------------------------------------------
    # TRENDS
    # ------------------------------------------------------------------

    def get_monthly_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
        # ... (unchanged)
        pass

    def get_daily_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
        # ... (unchanged)
        pass

    # ------------------------------------------------------------------
    # HEALTH & METADATA
    # ------------------------------------------------------------------

    def get_health(self) -> Dict[str, Any]:
        # ... (unchanged)
        pass

    def get_record_count(self) -> int:
        # ... (unchanged)
        pass

    # ------------------------------------------------------------------
    # RECOMMENDATIONS & RULES (static)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_grade(avg_delivery: float) -> str:
        if avg_delivery <= 2:
            return "A"
        elif avg_delivery <= 4:
            return "B"
        else:
            return "C"

    @staticmethod
    def _compute_risk_level(pending: int, late: int, avg_delivery: float) -> str:
        if late > 10 or pending > 20 or avg_delivery > 5:
            return "High"
        if late > 5 or pending > 10 or avg_delivery > 3:
            return "Medium"
        return "Low"

    @staticmethod
    def _compute_dealer_score(revenue: float, units: int, avg_delivery: float) -> float:
        score = 0.0
        if revenue > 0:
            score += min(revenue / 1000000, 1) * 40
        if units > 0:
            score += min(units / 1000, 1) * 30
        if avg_delivery > 0:
            score += max(0, (5 - avg_delivery) / 5) * 20
        return min(score, 100)

    @staticmethod
    def _warehouse_recommendation(code: str, grade: str, risk: str, pgi: float, delivery: float, pod: float) -> str:
        if grade in ("A", "B") and risk == "Low" and pgi >= 95 and delivery >= 95 and pod >= 95:
            return "Maintain current operations."
        if pgi < 95:
            return "Improve PGI processing to reduce pending dispatch."
        if delivery < 95:
            return "Enhance delivery performance against distance targets."
        if pod < 95:
            return "Accelerate POD collection and processing."
        if risk in ("Medium", "High"):
            return "Urgent review of operations and logistics processes."
        return "Monitor performance and optimize resource allocation."

    @staticmethod
    def _dealer_recommendation(code: str, score: float, avg_delivery: float) -> str:
        if score >= 80:
            return "Top performer – consider loyalty rewards."
        elif score >= 60:
            return "Good performance – focus on reducing delivery days."
        else:
            return "Needs improvement – provide training and support."

    @staticmethod
    def _product_recommendation(code: str, slow: bool, fast: bool, dead: bool) -> str:
        if dead:
            return "Dead stock – consider liquidation or return to supplier."
        if slow:
            return "Consider discounting or discontinuing this product."
        if fast:
            return "Increase inventory levels and marketing."
        return "Monitor performance closely."

    @staticmethod
    def _transporter_recommendation(name: str, delivery_rate: float, pod_rate: float) -> str:
        if delivery_rate >= 95 and pod_rate >= 95:
            return "Preferred transporter – maintain relationship."
        if delivery_rate < 85 or pod_rate < 85:
            return "Performance below standards – review contract terms."
        return "Good performance – encourage consistency."

# ============================================================
# DASHBOARD SERVICE (Orchestrator) – unchanged
# ============================================================

class DashboardService:
    # ... (unchanged – calls repository and engines)
    pass

# ============================================================
# END OF FILE
# ============================================================
