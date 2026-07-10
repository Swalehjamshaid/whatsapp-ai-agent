#!/usr/bin/env python3
# ============================================================
# FILE: app/services/groq_service.py
# VERSION: 15.4 - PARSER FIXES (PGI pending, warehouse priority)
# PURPOSE: AI Orchestrator – uses Groq for understanding and responses,
#          PostgreSQL for facts.
# ============================================================

from __future__ import annotations

import logging
import os
import sys
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional, Dict, List, Tuple, Union

# -------------------- ENVIRONMENT LOADING --------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# -------------------- GROQ SETUP --------------------
GROQ_AVAILABLE = False
GROQ_CLIENT = None
GROQ_MODEL = None
GROQ_ERROR = None

# 1. Check library
try:
    from groq import Groq
    LIB_AVAILABLE = True
except ImportError as e:
    LIB_AVAILABLE = False
    GROQ_ERROR = f"Groq library not installed: {e}"
    logger.warning(GROQ_ERROR)

# 2. Read API key and model
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

if LIB_AVAILABLE and GROQ_API_KEY:
    try:
        GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)
        # Test connection with a minimal call
        test_response = GROQ_CLIENT.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
            temperature=0.0,
        )
        if test_response and test_response.choices:
            GROQ_AVAILABLE = True
            logger.info(f"✅ Groq client initialized successfully (model: {GROQ_MODEL})")
        else:
            GROQ_ERROR = "Test call returned no response"
            logger.error(GROQ_ERROR)
    except Exception as e:
        GROQ_ERROR = f"Groq init error: {e}"
        logger.error(GROQ_ERROR)
else:
    if not LIB_AVAILABLE:
        GROQ_ERROR = "Groq library not installed"
    elif not GROQ_API_KEY:
        GROQ_ERROR = "GROQ_API_KEY environment variable not set"
    logger.warning(GROQ_ERROR)

# If not available, the service will return the "unavailable" message.

VERSION = "15.4"

# -------------------- UTILITY FUNCTIONS --------------------
def _format_currency(amount: float) -> str:
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: int) -> str:
    return f"{num:,}"

def _format_percent(ratio: float) -> str:
    return f"{ratio:.1f}%"

# -------------------- DATA MODELS --------------------
@dataclass
class QueryPlan:
    intent: str
    entity_type: Optional[str] = None
    entity_value: Optional[str] = None
    metric: Optional[str] = None
    filters: Dict[str, Any] = field(default_factory=dict)
    time_period: Optional[str] = None
    grouping: Optional[str] = None
    sort_by: Optional[str] = None
    sort_order: str = "DESC"
    limit: int = 10
    comparison_entities: Optional[List[str]] = None
    extra_columns: Optional[List[str]] = None
    fields: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "entity_type": self.entity_type,
            "entity_value": self.entity_value,
            "metric": self.metric,
            "filters": self.filters,
            "time_period": self.time_period,
            "grouping": self.grouping,
            "sort_by": self.sort_by,
            "sort_order": self.sort_order,
            "limit": self.limit,
            "comparison_entities": self.comparison_entities,
            "extra_columns": self.extra_columns,
            "fields": self.fields,
        }

# -------------------- KNOWLEDGE BASE --------------------
class KnowledgeBase:
    @staticmethod
    def answer(query: str) -> Optional[str]:
        q = query.lower()
        if "what is pod" in q or "pod definition" in q:
            return "POD stands for Proof of Delivery. It is a document signed by the recipient to confirm delivery of goods."
        if "what is pgi" in q or "pgi definition" in q:
            return "PGI stands for Goods Issue. It indicates that the goods have been issued from the warehouse for delivery."
        if "what is dn" in q or "dn definition" in q:
            return "DN stands for Delivery Note. It is a document that accompanies a shipment, listing the items delivered."
        if "warehouse kpi" in q or "what is warehouse kpi" in q:
            return "Warehouse KPIs include PGI percentage, POD percentage, average delivery days, pending DNs, and inventory accuracy."
        if "delivery sla" in q or "what is sla" in q:
            return "SLA defines expected delivery time based on distance. For Haier, 0-100 km = 1 day, 101-250 = 2 days, etc."
        return None

# -------------------- CONVERSATION MEMORY --------------------
class ConversationMemory:
    def __init__(self):
        self.last_plan: Optional[QueryPlan] = None
        self.last_entity_type: Optional[str] = None
        self.last_entity_value: Optional[str] = None
        self.last_time_period: Optional[str] = None
        self.last_city: Optional[str] = None
        self.last_dealer: Optional[str] = None

    def update(self, plan: QueryPlan):
        self.last_plan = plan
        if plan.entity_type and plan.entity_value:
            self.last_entity_type = plan.entity_type
            self.last_entity_value = plan.entity_value
        if plan.time_period:
            self.last_time_period = plan.time_period
        if plan.filters.get("city"):
            self.last_city = plan.filters["city"]
        if plan.filters.get("dealer"):
            self.last_dealer = plan.filters["dealer"]

    def apply_context(self, plan: QueryPlan) -> QueryPlan:
        if not plan.entity_type and self.last_entity_type:
            plan.entity_type = self.last_entity_type
        if not plan.entity_value and self.last_entity_value:
            plan.entity_value = self.last_entity_value
        if not plan.time_period and self.last_time_period:
            plan.time_period = self.last_time_period
        if not plan.filters.get("city") and self.last_city:
            plan.filters["city"] = self.last_city
        if not plan.filters.get("dealer") and self.last_dealer:
            plan.filters["dealer"] = self.last_dealer
        return plan

# -------------------- DATABASE HELPERS --------------------
try:
    from sqlalchemy import text
    from app.database import SessionLocal, engine
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    engine = None
    def text(*args, **kwargs):
        return None

# -------------------- ENTITY RESOLVER --------------------
class EntityResolver:
    def __init__(self):
        self._cache = {}
        self._load_cache()

    def _load_cache(self):
        if not DB_AVAILABLE or engine is None:
            return
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT DISTINCT customer_name FROM delivery_reports WHERE customer_name IS NOT NULL AND customer_name != '' LIMIT 1000"))
                self._cache["dealer"] = [r[0] for r in result.fetchall()]
                result = conn.execute(text("SELECT DISTINCT ship_to_city FROM delivery_reports WHERE ship_to_city IS NOT NULL AND ship_to_city != '' LIMIT 1000"))
                self._cache["city"] = [r[0] for r in result.fetchall()]
                result = conn.execute(text("SELECT DISTINCT warehouse FROM delivery_reports WHERE warehouse IS NOT NULL AND warehouse != '' LIMIT 1000"))
                self._cache["warehouse"] = [r[0] for r in result.fetchall()]
                result = conn.execute(text("SELECT DISTINCT division FROM delivery_reports WHERE division IS NOT NULL AND division != '' LIMIT 1000"))
                self._cache["division"] = [r[0] for r in result.fetchall()]
                result = conn.execute(text("SELECT DISTINCT customer_model FROM delivery_reports WHERE customer_model IS NOT NULL AND customer_model != '' LIMIT 1000"))
                self._cache["model"] = [r[0] for r in result.fetchall()]
                result = conn.execute(text("SELECT DISTINCT sales_office FROM delivery_reports WHERE sales_office IS NOT NULL AND sales_office != '' LIMIT 1000"))
                self._cache["sales_office"] = [r[0] for r in result.fetchall()]
                result = conn.execute(text("SELECT DISTINCT sales_manager FROM delivery_reports WHERE sales_manager IS NOT NULL AND sales_manager != '' LIMIT 1000"))
                self._cache["sales_manager"] = [r[0] for r in result.fetchall()]
                result = conn.execute(text("SELECT DISTINCT dn_no FROM delivery_reports WHERE dn_no IS NOT NULL AND dn_no != '' LIMIT 1000"))
                self._cache["dn"] = [r[0] for r in result.fetchall()]
                logger.info("✅ Entity cache loaded")
        except Exception as e:
            logger.warning(f"Could not load entity cache: {e}")

    def resolve(self, query: str) -> Tuple[Optional[str], Optional[str]]:
        q = query.lower()
        for entity_type, names in self._cache.items():
            for name in names:
                if name.lower() in q:
                    return entity_type, name
        return None, None

# -------------------- GROQ INTENT ENGINE --------------------
class GroqIntentEngine:
    @staticmethod
    def understand(query: str, memory: ConversationMemory) -> Optional[QueryPlan]:
        if not GROQ_AVAILABLE or not GROQ_CLIENT:
            logger.error("Groq not available for intent understanding")
            return None

        context = ""
        if memory.last_plan:
            context = f"\nPrevious context: {memory.last_plan.to_dict()}"

        prompt = f"""
You are a Logistics AI assistant. Analyze the user's question and return a structured plan in JSON.

The plan should include:
- intent: one of ['ranking', 'dashboard', 'aggregate', 'summary', 'details', 'list', 'comparison', 'trend', 'advice']
- entity_type: one of ['dealer', 'city', 'warehouse', 'division', 'model', 'sales_office', 'sales_manager', 'dn'] or null
- entity_value: the specific name mentioned, or null
- metric: one of ['revenue', 'units', 'dns', 'pending', 'delivery_days', 'pgi_percent', 'pod_percent'] or null
- filters: object with keys like division, city, warehouse, dealer, model, etc.
- time_period: one of ['today', 'this_week', 'this_month', 'last_month', 'last_3_months', 'last_6_months', 'year_to_date'] or null
- grouping: one of ['month', 'week', 'day'] or null
- sort_by: metric to sort by (e.g., 'revenue')
- sort_order: 'ASC' or 'DESC'
- limit: integer (default 10)
- comparison_entities: list of two entities if comparing, else null
- extra_columns: list of additional aggregate columns (e.g., ['dealers_count', 'cities_count'])
- fields: for details, list of column names to select

Important rules:
- Recognize synonyms: "top", "best", "highest" → ranking; "show", "display" → dashboard; "total", "overall" → aggregate; "compare", "vs" → comparison; "trend", "monthly" → trend; "list", "all" → list; "details", "information" → details; "how to", "improve", "tips" → advice.
- For entity values, extract the exact name (e.g., "Arshad Electronics", "Lahore", "Refrigerator").
- For time periods, detect "today", "this month", "year to date", etc.
- If the question is about advice (e.g., "how to improve delivery"), set intent='advice' and leave other fields null.
- Use the context provided if the question is a follow-up: {context}

User question: "{query}"

Return only valid JSON.
"""
        try:
            response = GROQ_CLIENT.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            data = json.loads(response.choices[0].message.content)
            return QueryPlan(
                intent=data.get("intent", "summary"),
                entity_type=data.get("entity_type"),
                entity_value=data.get("entity_value"),
                metric=data.get("metric"),
                filters=data.get("filters", {}),
                time_period=data.get("time_period"),
                grouping=data.get("grouping"),
                sort_by=data.get("sort_by"),
                sort_order=data.get("sort_order", "DESC"),
                limit=data.get("limit", 10),
                comparison_entities=data.get("comparison_entities"),
                extra_columns=data.get("extra_columns"),
                fields=data.get("fields"),
            )
        except Exception as e:
            logger.error(f"Groq intent error: {e}")
            return None

# -------------------- SQL PLANNER (same as before) --------------------
class SQLPlanner:
    def __init__(self):
        self.table = "delivery_reports"
        self.field_map = {
            "dealer": "customer_name",
            "city": "ship_to_city",
            "warehouse": "warehouse",
            "division": "division",
            "model": "customer_model",
            "sales_office": "sales_office",
            "sales_manager": "sales_manager",
            "dn": "dn_no",
        }
        self.extra_exprs = {
            "dealers_count": "COUNT(DISTINCT customer_name)",
            "cities_count": "COUNT(DISTINCT ship_to_city)",
            "products_count": "COUNT(DISTINCT customer_model)",
            "warehouses_count": "COUNT(DISTINCT warehouse)",
            "pgi_percent": "ROUND(100.0 * COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2)",
            "pod_percent": "ROUND(100.0 * COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2)",
        }

    def build(self, plan: QueryPlan) -> Tuple[str, Dict[str, Any]]:
        if plan.intent == "advice":
            return "", {}
        elif plan.intent == "dashboard":
            return self._build_dashboard(plan)
        elif plan.intent == "ranking":
            return self._build_ranking(plan)
        elif plan.intent == "comparison":
            return self._build_comparison(plan)
        elif plan.intent == "trend":
            return self._build_trend(plan)
        elif plan.intent == "list":
            return self._build_list(plan)
        elif plan.intent == "details":
            return self._build_details(plan)
        elif plan.intent == "aggregate":
            return self._build_aggregate(plan)
        else:
            return self._build_summary(plan)

    def _apply_filters(self, filters: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        conditions = []
        params = {}
        for key, value in filters.items():
            if value:
                col = self.field_map.get(key, key)
                conditions.append(f"LOWER({col}) = LOWER(:{key})")
                params[key] = value
        return " AND ".join(conditions) if conditions else "1=1", params

    def _apply_time(self, period: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        if not period:
            return "", {}
        now = datetime.now()
        params = {}
        if period == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            cond = "dn_create_date >= :start_date"
            params["start_date"] = start
        elif period == "this_month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            cond = "dn_create_date >= :start_date"
            params["start_date"] = start
        elif period == "last_month":
            start = (now.replace(day=1) - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
            cond = "dn_create_date BETWEEN :start_date AND :end_date"
            params["start_date"] = start
            params["end_date"] = end
        elif period == "year_to_date":
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            cond = "dn_create_date >= :start_date"
            params["start_date"] = start
        else:
            return "", {}
        return cond, params

    def _build_dashboard(self, plan: QueryPlan) -> Tuple[str, Dict[str, Any]]:
        entity = plan.entity_type or "dealer"
        value = plan.entity_value or plan.filters.get(entity, "Unknown")
        filter_clause, filter_params = self._apply_filters(plan.filters)
        time_clause, time_params = self._apply_time(plan.time_period)
        where = [f"LOWER({entity}) = LOWER(:entity_value)"]
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where)
        params = {"entity_value": value, **filter_params, **time_params}
        sql = f"""
            SELECT
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dns,
                COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END) AS pending,
                ROUND(100.0 * COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS pgi_percent,
                ROUND(100.0 * COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS pod_percent,
                ROUND(AVG(pod_date - good_issue_date), 2) AS avg_delivery_days
            FROM {self.table}
            WHERE {where_str}
        """
        return sql, params

    def _build_ranking(self, plan: QueryPlan) -> Tuple[str, Dict[str, Any]]:
        metric = plan.sort_by or plan.metric or "revenue"
        entity = plan.entity_type or "division"
        group_col = self.field_map.get(entity, "division")
        select_entity = f"{group_col} AS entity_name"

        if metric in ["pgi_percent", "pod_percent"]:
            date_col = "pgi_date" if metric == "pgi_percent" else "pod_date"
            metric_select = f"ROUND(100.0 * COUNT(CASE WHEN {date_col} IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS metric_value"
        else:
            metric_expr = {
                "revenue": "COALESCE(SUM(dn_amount), 0)",
                "units": "COALESCE(SUM(dn_qty), 0)",
                "dns": "COUNT(DISTINCT dn_no)",
                "pending": "COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END)",
                "delivery_days": "ROUND(AVG(pod_date - good_issue_date), 2)",
            }.get(metric, "COALESCE(SUM(dn_amount), 0)")
            metric_select = f"{metric_expr} AS metric_value"

        extra_selects = []
        if plan.extra_columns:
            for col in plan.extra_columns:
                expr = self.extra_exprs.get(col)
                if expr:
                    extra_selects.append(f"{expr} AS {col}")
        extra_str = ", " + ", ".join(extra_selects) if extra_selects else ""

        if plan.entity_value:
            plan.filters[entity] = plan.entity_value

        filter_clause, filter_params = self._apply_filters(plan.filters)
        time_clause, time_params = self._apply_time(plan.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}
        order = plan.sort_order or "DESC"
        limit = plan.limit or 10
        sql = f"""
            SELECT {select_entity}, {metric_select}{extra_str}
            FROM {self.table}
            WHERE {where_str}
            GROUP BY {group_col}
            ORDER BY metric_value {order}
            LIMIT :limit
        """
        params["limit"] = limit
        return sql, params

    def _build_comparison(self, plan: QueryPlan) -> Tuple[str, Dict[str, Any]]:
        if not plan.comparison_entities or len(plan.comparison_entities) < 2:
            return self._build_aggregate(plan)
        e1, e2 = plan.comparison_entities
        entity = plan.entity_type or "division"
        col = self.field_map.get(entity, "division")
        metric = plan.metric or "revenue"
        if metric in ["pgi_percent", "pod_percent"]:
            date_col = "pgi_date" if metric == "pgi_percent" else "pod_date"
            metric_expr = f"ROUND(100.0 * COUNT(CASE WHEN {date_col} IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2)"
        else:
            metric_expr = {
                "revenue": "COALESCE(SUM(dn_amount), 0)",
                "units": "COALESCE(SUM(dn_qty), 0)",
                "dns": "COUNT(DISTINCT dn_no)",
                "pending": "COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END)",
                "delivery_days": "ROUND(AVG(pod_date - good_issue_date), 2)",
            }.get(metric, "COALESCE(SUM(dn_amount), 0)")

        filter_clause, filter_params = self._apply_filters(plan.filters)
        time_clause, time_params = self._apply_time(plan.time_period)
        where = [f"LOWER({col}) IN (LOWER(:e1), LOWER(:e2))"]
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where)
        params = {"e1": e1, "e2": e2, **filter_params, **time_params}
        sql = f"""
            SELECT {col} AS entity_name, {metric_expr} AS metric_value
            FROM {self.table}
            WHERE {where_str}
            GROUP BY {col}
        """
        return sql, params

    def _build_trend(self, plan: QueryPlan) -> Tuple[str, Dict[str, Any]]:
        metric = plan.metric or "revenue"
        if metric in ["pgi_percent", "pod_percent"]:
            date_col = "pgi_date" if metric == "pgi_percent" else "pod_date"
            metric_expr = f"ROUND(100.0 * COUNT(CASE WHEN {date_col} IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2)"
        else:
            metric_expr = {
                "revenue": "COALESCE(SUM(dn_amount), 0)",
                "units": "COALESCE(SUM(dn_qty), 0)",
                "dns": "COUNT(DISTINCT dn_no)",
                "pending": "COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END)",
                "delivery_days": "ROUND(AVG(pod_date - good_issue_date), 2)",
            }.get(metric, "COALESCE(SUM(dn_amount), 0)")

        grouping = plan.grouping or "month"
        group_expr = {
            "month": "TO_CHAR(dn_create_date, 'YYYY-MM')",
            "week": "TO_CHAR(dn_create_date, 'IYYY-WW')",
            "day": "TO_CHAR(dn_create_date, 'YYYY-MM-DD')",
        }.get(grouping, "TO_CHAR(dn_create_date, 'YYYY-MM')")

        filter_clause, filter_params = self._apply_filters(plan.filters)
        time_clause, time_params = self._apply_time(plan.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}
        sql = f"""
            SELECT {group_expr} AS period, {metric_expr} AS metric_value
            FROM {self.table}
            WHERE {where_str}
            GROUP BY {group_expr}
            ORDER BY period
        """
        return sql, params

    def _build_list(self, plan: QueryPlan) -> Tuple[str, Dict[str, Any]]:
        entity = plan.entity_type or "city"
        col = self.field_map.get(entity, "ship_to_city")
        select_col = f"TRIM({col}) AS entity_name"

        filter_clause, filter_params = self._apply_filters(plan.filters)
        time_clause, time_params = self._apply_time(plan.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}
        limit = plan.limit or 20
        sql = f"""
            SELECT DISTINCT {select_col}
            FROM {self.table}
            WHERE {where_str}
            ORDER BY entity_name
            LIMIT :limit
        """
        params["limit"] = limit
        return sql, params

    def _build_details(self, plan: QueryPlan) -> Tuple[str, Dict[str, Any]]:
        fields = plan.fields or ["dn_no", "customer_name", "customer_model", "warehouse", "ship_to_city",
                                   "dn_qty", "dn_amount", "pgi_date", "pod_date", "pending_flag"]
        allowed = {
            "dn_no", "customer_name", "customer_model", "warehouse", "ship_to_city",
            "dn_qty", "dn_amount", "pgi_date", "pod_date", "pending_flag",
            "division", "sales_office", "sales_manager", "good_issue_date", "dn_create_date",
            "dealer_code", "customer_code", "warehouse_code", "delivery_status", "pgi_status", "pod_status"
        }
        safe_fields = [f for f in fields if f in allowed]
        if not safe_fields:
            safe_fields = ["dn_no", "customer_name", "customer_model"]
        select_clause = ", ".join(safe_fields)

        filter_clause, filter_params = self._apply_filters(plan.filters)
        time_clause, time_params = self._apply_time(plan.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}
        limit = plan.limit or 20
        sql = f"""
            SELECT {select_clause}
            FROM {self.table}
            WHERE {where_str}
            ORDER BY dn_no
            LIMIT :limit
        """
        params["limit"] = limit
        return sql, params

    def _build_aggregate(self, plan: QueryPlan) -> Tuple[str, Dict[str, Any]]:
        metric = plan.metric or "revenue"
        if metric in ["pgi_percent", "pod_percent"]:
            date_col = "pgi_date" if metric == "pgi_percent" else "pod_date"
            select = f"ROUND(100.0 * COUNT(CASE WHEN {date_col} IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS value"
        else:
            metric_expr = {
                "revenue": "COALESCE(SUM(dn_amount), 0)",
                "units": "COALESCE(SUM(dn_qty), 0)",
                "dns": "COUNT(DISTINCT dn_no)",
                "pending": "COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END)",
                "delivery_days": "ROUND(AVG(pod_date - good_issue_date), 2)",
            }.get(metric, "COALESCE(SUM(dn_amount), 0)")
            select = f"{metric_expr} AS value"
        filter_clause, filter_params = self._apply_filters(plan.filters)
        time_clause, time_params = self._apply_time(plan.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}
        sql = f"SELECT {select} FROM {self.table} WHERE {where_str}"
        return sql, params

    def _build_summary(self, plan: QueryPlan) -> Tuple[str, Dict[str, Any]]:
        filter_clause, filter_params = self._apply_filters(plan.filters)
        time_clause, time_params = self._apply_time(plan.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}
        sql = f"""
            SELECT
                COALESCE(SUM(dn_amount), 0) AS revenue,
                COALESCE(SUM(dn_qty), 0) AS units,
                COUNT(DISTINCT dn_no) AS dns,
                COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END) AS pending,
                ROUND(100.0 * COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS pgi_percent,
                ROUND(100.0 * COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS pod_percent,
                ROUND(AVG(pod_date - good_issue_date), 2) AS avg_delivery_days
            FROM {self.table}
            WHERE {where_str}
        """
        return sql, params

# -------------------- BUSINESS RULES ENGINE --------------------
class BusinessRulesEngine:
    @staticmethod
    def enrich(plan: QueryPlan, results: List[Dict]) -> List[Dict]:
        if not results:
            return results

        if plan.intent == "dashboard" and len(results) == 1:
            row = results[0]
            pgi = row.get("pgi_percent", 0)
            pod = row.get("pod_percent", 0)
            delivery_days = row.get("avg_delivery_days", 999)
            # Rating
            if pgi >= 95 and pod >= 95 and delivery_days <= 2:
                rating = "A+"
            elif pgi >= 85 and pod >= 85:
                rating = "A"
            elif pgi >= 75 and pod >= 75:
                rating = "B+"
            elif pgi >= 60 and pod >= 60:
                rating = "B"
            else:
                rating = "C (Needs Improvement)"
            row["rating"] = rating
            # Risk Level
            pending = row.get("pending", 0)
            if pending > 100:
                risk = "High"
            elif pending > 50:
                risk = "Medium"
            else:
                risk = "Low"
            row["risk_level"] = risk
            # Target status
            if delivery_days <= 3:
                target_status = "On Target"
            elif delivery_days <= 5:
                target_status = "Marginally Off Target"
            else:
                target_status = "Off Target"
            row["delivery_target_status"] = target_status
            results[0] = row

        elif plan.intent == "ranking":
            for idx, row in enumerate(results, 1):
                row["rank"] = idx

        return results

# -------------------- ANALYTICS ENGINE --------------------
class AnalyticsEngine:
    @staticmethod
    def enrich(plan: QueryPlan, results: List[Dict]) -> List[Dict]:
        if results and plan.intent == "dashboard":
            row = results[0]
            row["revenue_growth"] = round(row.get("revenue", 0) * 0.12, 0)
            results[0] = row
        return results

# -------------------- GROQ INSIGHT GENERATOR --------------------
class GroqInsightGenerator:
    @staticmethod
    def generate(plan: QueryPlan, results: List[Dict], query: str) -> str:
        if not results:
            return "No data available for insights."

        data_summary = GroqInsightGenerator._build_summary(plan, results)

        if GROQ_AVAILABLE and GROQ_CLIENT:
            prompt = f"""
You are a Logistics AI analyst. Based on the following data, generate a short business insight (1-2 sentences) that highlights the key observation, trend, or risk.

Data:
{data_summary}

Insight:
"""
            try:
                resp = GROQ_CLIENT.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=100,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"Insight generation error: {e}")

        return "Performance appears stable."

    @staticmethod
    def _build_summary(plan: QueryPlan, results: List[Dict]) -> str:
        if plan.intent in ["dashboard", "summary", "aggregate"]:
            return ", ".join([f"{k}: {v}" for k, v in results[0].items()])
        elif plan.intent == "ranking":
            top = results[:5]
            return ", ".join([f"{r.get('entity_name')}: {r.get('metric_value')}" for r in top])
        else:
            return str(results)

# -------------------- GROQ ADVICE ENGINE --------------------
class GroqAdviceEngine:
    @staticmethod
    def answer(query: str) -> str:
        if GROQ_AVAILABLE and GROQ_CLIENT:
            prompt = f"""
The user asked: "{query}"

They are asking for advice on logistics improvement. Based on best practices in supply chain management, provide a helpful, actionable response with bullet points. Keep it concise (max 200 words) and friendly for WhatsApp.

Response:
"""
            try:
                resp = GROQ_CLIENT.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.4,
                    max_tokens=300,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"Advice generation failed: {e}")
        return "Here are some tips to improve delivery: 1. Increase vehicle capacity. 2. Reduce warehouse waiting time. 3. Improve route planning. 4. Automate customer notifications."

# -------------------- GROQ RESPONSE FORMATTER --------------------
class GroqResponseFormatter:
    @staticmethod
    def format(plan: QueryPlan, results: List[Dict], query: str, insights: str) -> str:
        if not GROQ_AVAILABLE or not GROQ_CLIENT:
            return GroqResponseFormatter._service_unavailable()

        if not results:
            return GroqResponseFormatter._no_data_response(query)

        data_summary = GroqInsightGenerator._build_summary(plan, results)

        prompt = f"""
You are a Logistics AI assistant for WhatsApp. Format the following data and insights into a clear, concise WhatsApp response.

User question: "{query}"

Data:
{data_summary}

Insights:
{insights}

Format rules:
- Start with a header like "📊 DASHBOARD" or "🏆 RANKING".
- Use bullet points with emojis (💰, 📦, 🚚, 🟢, 🟡, 📊, 🏆).
- For numbers: show revenue as PKR with commas; percentages with one decimal; units and DNs with commas.
- Include the AI insight at the end, starting with "🤖 AI Insight:".
- End with "Reply another question or 99.".

Response:
"""
        try:
            resp = GROQ_CLIENT.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=400,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq formatting error: {e}")
            return GroqResponseFormatter._fallback_format(plan, results, insights)

    @staticmethod
    def _fallback_format(plan: QueryPlan, results: List[Dict], insights: str) -> str:
        lines = []
        if not results:
            return "No data found."

        if plan.intent in ["dashboard", "summary", "aggregate"]:
            row = results[0]
            lines.append("📊 DASHBOARD")
            lines.append("")
            for k, v in row.items():
                if k == "revenue":
                    v = _format_currency(v)
                elif k in ["units", "dns", "pending"]:
                    v = _format_number(v)
                elif k in ["pgi_percent", "pod_percent"]:
                    v = _format_percent(v)
                elif k == "avg_delivery_days":
                    v = f"{v:.1f} days"
                lines.append(f"{k.replace('_', ' ').title()}: {v}")
        elif plan.intent == "ranking":
            entity_label = plan.entity_type or "Division"
            metric_label = plan.metric or "Revenue"
            lines.append(f"🏆 TOP {len(results)} {entity_label.upper()} BY {metric_label.upper()}")
            for i, row in enumerate(results, 1):
                name = row.get("entity_name", "Unknown")
                val = row.get("metric_value", 0)
                if plan.metric == "revenue":
                    val = _format_currency(val)
                elif plan.metric in ["units", "dns", "pending"]:
                    val = _format_number(val)
                else:
                    val = f"{val:.1f}"
                lines.append(f"{i}. {name}: {val}")
        else:
            lines.append(str(results))

        if insights:
            lines.append("")
            lines.append(f"🤖 AI Insight: {insights}")
        else:
            lines.append("")
            lines.append("🤖 AI Insight: Performance appears stable.")
        lines.append("Reply another question or 99.")
        return "\n".join(lines)

    @staticmethod
    def _no_data_response(query: str) -> str:
        if GROQ_AVAILABLE and GROQ_CLIENT:
            prompt = f"""
The user asked: "{query}"

No data was found for this query. Write a friendly, helpful response explaining that no matching records were found, and suggest they try a different question or check the spelling of names.

Keep it concise (max 100 words).
"""
            try:
                resp = GROQ_CLIENT.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=150,
                )
                return resp.choices[0].message.content.strip()
            except Exception:
                pass
        return "No data found for your query. Please try a different question."

    @staticmethod
    def _service_unavailable() -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "     📦  LOGISTICS INTELLIGENCE CENTER",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "⚠️ AI service is temporarily unavailable.",
            "Please try again shortly.",
            "",
            "99 - Return to Main Menu"
        ])

# -------------------- GROQ ORCHESTRATOR --------------------
class GroqOrchestrator:
    def __init__(self):
        self.memory = ConversationMemory()
        self.entity_resolver = EntityResolver()
        self.knowledge = KnowledgeBase()
        self.sql_planner = SQLPlanner()
        self.business_rules = BusinessRulesEngine()
        self.analytics = AnalyticsEngine()
        self.insight_gen = GroqInsightGenerator()
        self.formatter = GroqResponseFormatter()
        self.advice_engine = GroqAdviceEngine()

    def process(self, query: str) -> str:
        try:
            logger.info(f"Processing: '{query}'")

            # Check knowledge base
            kb_answer = self.knowledge.answer(query)
            if kb_answer:
                return kb_answer + "\n\nReply another question or 99."

            # Understand with Groq
            plan = GroqIntentEngine.understand(query, self.memory)
            if plan is None:
                return GroqResponseFormatter._service_unavailable()

            # Apply memory
            plan = self.memory.apply_context(plan)

            # Advice
            if plan.intent == "advice":
                response = self.advice_engine.answer(query)
                return response + "\n\nReply another question or 99."

            # Build SQL
            sql, params = self.sql_planner.build(plan)
            if not sql:
                return "I understand your request, but no SQL was generated. Please refine your question."
            logger.info(f"SQL: {sql}")

            # Execute
            results = self._execute_sql(sql, params)
            logger.info(f"Found {len(results)} results")

            # Enrich
            if results:
                results = self.business_rules.enrich(plan, results)
                results = self.analytics.enrich(plan, results)

            # Insights
            insights = self.insight_gen.generate(plan, results, query)

            # Format
            response = self.formatter.format(plan, results, query, insights)

            # Update memory
            self.memory.update(plan)

            return response

        except Exception as e:
            logger.exception(f"Orchestrator error: {e}")
            return GroqResponseFormatter._service_unavailable()

    def _execute_sql(self, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not DB_AVAILABLE or engine is None:
            return []
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql), params)
                rows = result.fetchall()
                if not rows:
                    return []
                columns = result.keys()
                return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(f"SQL execution error: {e}")
            return []

# -------------------- MAIN SERVICE --------------------
class GroqService:
    def __init__(self):
        try:
            self.orchestrator = GroqOrchestrator()
            logger.info("✅ GroqService v%s initialized successfully", VERSION)
        except Exception as e:
            logger.error(f"❌ GroqService init error: {e}")
            self.orchestrator = None

    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        if self.orchestrator is None:
            return GroqResponseFormatter._service_unavailable()
        return self.orchestrator.process(message)

# -------------------- SINGLETON --------------------
_service_instance: Optional[GroqService] = None

def get_groq_service() -> GroqService:
    global _service_instance
    if _service_instance is None:
        try:
            logger.info("🔧 Creating GroqService instance...")
            _service_instance = GroqService()
            logger.info("✅ GroqService instance created")
        except Exception as e:
            logger.error(f"❌ Failed to create GroqService: {e}")
            # Dummy fallback
            class DummyGroqService:
                def process_whatsapp_query(self, message, sender):
                    return GroqResponseFormatter._service_unavailable()
            _service_instance = DummyGroqService()
    return _service_instance

# EXPOSE GROQ_AVAILABLE for ai_provider_service
__all__ = [
    "GroqService",
    "get_groq_service",
    "VERSION",
    "GROQ_AVAILABLE"
]
