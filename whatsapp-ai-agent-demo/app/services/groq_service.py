#!/usr/bin/env python3
# ============================================================
# FILE: app/services/groq_service.py
# VERSION: 7.0 - DYNAMIC QUERY ENGINE (500+ QUESTIONS)
# PURPOSE: Answer any logistics question using LLM + dynamic SQL.
#          Supports 500+ question types via metadata-driven parsing.
# ============================================================

from __future__ import annotations

import logging
import os
import re
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional, Dict, List, Tuple, Union

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)

VERSION = "7.0"

# ============================================================
# GROQ SETUP
# ============================================================

GROQ_AVAILABLE = False
GROQ_CLIENT = None

try:
    from groq import Groq
    GROQ_AVAILABLE = True
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    if GROQ_API_KEY:
        GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)
        logger.info("✅ Groq client initialized")
    else:
        logger.warning("⚠️ GROQ_API_KEY not set – using fallback mode")
except ImportError:
    logger.warning("⚠️ Groq library not installed – using fallback mode")

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        return str(value).strip() or default
    except (TypeError, ValueError):
        return default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def _format_currency(amount: float) -> str:
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: int) -> str:
    return f"{num:,}"

def _format_date(dt: Any) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%d-%b-%Y")
    if isinstance(dt, str):
        try:
            return datetime.strptime(dt, "%Y-%m-%d").strftime("%d-%b-%Y")
        except:
            return dt
    return str(dt) if dt else "N/A"

# ============================================================
# QUERY INTENT DATA MODEL
# ============================================================

@dataclass
class QueryIntent:
    """Structured representation of the user's question."""
    intent: str  # 'ranking', 'aggregate', 'comparison', 'trend', 'list', 'summary'
    entity_type: Optional[str] = None  # 'dealer', 'model', 'division', 'city', 'warehouse', 'sales_office', 'sales_manager', 'dn'
    entity_value: Optional[str] = None
    metric: Optional[str] = None  # 'revenue', 'units', 'dns', 'pending', 'delivery_days', 'pod_days', 'pgi_days'
    filters: Dict[str, Any] = field(default_factory=dict)  # e.g., {'division': 'Washing Machine', 'city': 'Lahore'}
    time_period: Optional[str] = None  # 'today', 'this_week', 'this_month', 'last_month', 'last_3_months', 'last_6_months', 'year_to_date'
    grouping: Optional[str] = None  # 'month', 'week', 'day', 'division', 'city', 'dealer', 'warehouse'
    sort_by: Optional[str] = None  # 'revenue', 'units', 'pending' etc.
    sort_order: str = "DESC"
    limit: int = 10
    comparison_entities: Optional[List[str]] = None  # for comparison intent

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
        }

# ============================================================
# PARSER: GROQ + FALLBACK
# ============================================================

class QueryParser:
    """Parse natural language into QueryIntent using Groq (or regex)."""

    # Mapping of common phrases to intent/metric/entity
    METRIC_MAP = {
        "revenue": "revenue",
        "sales": "revenue",
        "income": "revenue",
        "units": "units",
        "quantity": "units",
        "dns": "dns",
        "delivery notes": "dns",
        "pending": "pending",
        "delivery days": "delivery_days",
        "transit days": "delivery_days",
        "pod days": "pod_days",
        "pgi days": "pgi_days",
    }

    ENTITY_MAP = {
        "dealer": "dealer",
        "customer": "dealer",
        "distributor": "dealer",
        "model": "model",
        "product": "model",
        "division": "division",
        "city": "city",
        "warehouse": "warehouse",
        "sales office": "sales_office",
        "sales manager": "sales_manager",
        "dn": "dn",
        "delivery note": "dn",
    }

    INTENT_MAP = {
        "top": "ranking",
        "best": "ranking",
        "highest": "ranking",
        "worst": "ranking",
        "lowest": "ranking",
        "bottom": "ranking",
        "compare": "comparison",
        "vs": "comparison",
        "trend": "trend",
        "monthly": "trend",
        "weekly": "trend",
        "daily": "trend",
        "list": "list",
        "show": "list",
        "total": "aggregate",
        "overall": "aggregate",
        "summary": "summary",
        "overview": "summary",
    }

    def __init__(self):
        self.fallback_patterns = self._build_fallback_patterns()

    def _build_fallback_patterns(self) -> Dict[str, re.Pattern]:
        """Fallback regex patterns for when Groq is unavailable."""
        return {
            "ranking": re.compile(
                r"(?:top|best|highest|worst|lowest|bottom)\s*(\d+)?\s*(?:dealer|model|division|city|warehouse|sales office|sales manager|product)?",
                re.I
            ),
            "comparison": re.compile(
                r"compare\s+(?:the\s+)?([\w\s\-]+?)\s+(?:and|vs|versus)\s+([\w\s\-]+)",
                re.I
            ),
            "aggregate": re.compile(
                r"(?:total|overall)\s*(?:revenue|units|dns|pending)",
                re.I
            ),
            "trend": re.compile(
                r"(?:trend|monthly|weekly|daily)\s*(?:revenue|units|dns|pending)",
                re.I
            ),
        }

    def parse(self, query: str) -> QueryIntent:
        """Parse query using Groq (preferred) or fallback regex."""
        if GROQ_AVAILABLE and GROQ_CLIENT:
            return self._parse_with_groq(query)
        return self._parse_with_fallback(query)

    def _parse_with_groq(self, query: str) -> QueryIntent:
        """Use Groq to extract intent, entities, metrics, filters."""
        prompt = f"""
You are a logistics data assistant. Extract the following from the user's question and return ONLY valid JSON.

Fields:
- intent: one of ['ranking', 'aggregate', 'comparison', 'trend', 'list', 'summary']
- entity_type: one of ['dealer', 'model', 'division', 'city', 'warehouse', 'sales_office', 'sales_manager', 'dn'] or null
- entity_value: the specific name/value of the entity, or null
- metric: one of ['revenue', 'units', 'dns', 'pending', 'delivery_days', 'pod_days', 'pgi_days'] or null
- filters: object with keys like division, city, warehouse, dealer, model, etc. (values are strings)
- time_period: one of ['today', 'this_week', 'this_month', 'last_month', 'last_3_months', 'last_6_months', 'year_to_date'] or null
- grouping: one of ['month', 'week', 'day', 'division', 'city', 'dealer', 'warehouse'] or null
- sort_by: metric to sort by, e.g., 'revenue', 'units', 'pending'
- sort_order: 'ASC' or 'DESC'
- limit: integer (default 10)
- comparison_entities: list of two strings if intent is 'comparison', else null

Question: "{query}"

Return valid JSON only.
"""
        try:
            response = GROQ_CLIENT.chat.completions.create(
                model="llama3-70b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=400,
                response_format={"type": "json_object"}
            )
            data = json.loads(response.choices[0].message.content)
            return QueryIntent(
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
            )
        except Exception as e:
            logger.error(f"Groq parse error: {e}")
            return self._parse_with_fallback(query)

    def _parse_with_fallback(self, query: str) -> QueryIntent:
        """Fallback regex-based parsing."""
        q = query.lower()
        intent = "summary"
        entity_type = None
        entity_value = None
        metric = None
        filters = {}
        time_period = None
        grouping = None
        sort_by = None
        sort_order = "DESC"
        limit = 10
        comparison_entities = None

        # Detect intent
        if re.search(r"(?:top|best|highest|worst|lowest|bottom)", q):
            intent = "ranking"
            # try to extract limit
            m = re.search(r"top\s*(\d+)", q)
            if m:
                limit = int(m.group(1))
            # try to extract entity
            for ent in ["dealer", "model", "division", "city", "warehouse", "sales office", "sales manager"]:
                if ent in q:
                    entity_type = ent.replace(" ", "_")
                    # try to extract value after 'of' or 'for'
                    m = re.search(r"(?:of|for)\s+(.+?)(?:\s+in|\s+with|\s+and|$)", q)
                    if m:
                        entity_value = m.group(1).strip()
                    break
        elif re.search(r"compare|vs|versus", q):
            intent = "comparison"
            m = re.search(r"compare\s+(.+?)\s+(?:and|vs|versus)\s+(.+)", q)
            if m:
                comparison_entities = [m.group(1).strip(), m.group(2).strip()]
        elif re.search(r"(?:total|overall)\s*(?:revenue|units|dns|pending)", q):
            intent = "aggregate"
            for met in ["revenue", "units", "dns", "pending"]:
                if met in q:
                    metric = met
                    break
        elif re.search(r"(?:trend|monthly|weekly|daily)", q):
            intent = "trend"
            for met in ["revenue", "units", "dns", "pending"]:
                if met in q:
                    metric = met
                    break
            if "monthly" in q:
                grouping = "month"
            elif "weekly" in q:
                grouping = "week"
            elif "daily" in q:
                grouping = "day"

        # Extract entity and metric from specific phrases
        # e.g., "revenue of Washing Machine" -> metric=revenue, entity_type=division, entity_value=Washing Machine
        for met in ["revenue", "units", "dns", "pending"]:
            if met in q:
                metric = met
                # try to find entity after "of" or "for"
                m = re.search(r"(?:of|for)\s+(.+?)(?:\s+in|\s+with|$)", q)
                if m:
                    entity_value = m.group(1).strip()
                    # determine entity type by context
                    for ent in ["division", "model", "city", "dealer", "warehouse"]:
                        if ent in q:
                            entity_type = ent
                            break
                    if not entity_type:
                        entity_type = "division"  # default assumption
                break

        # Extract filters like "in Lahore", "for dealer ABC"
        m = re.search(r"in\s+([\w\s\-]+)(?:\s+with|\s+and|$)", q)
        if m:
            city = m.group(1).strip()
            if "city" not in filters:
                filters["city"] = city

        m = re.search(r"for\s+(?:dealer|customer)\s+([\w\s\-]+)", q)
        if m:
            filters["dealer"] = m.group(1).strip()

        # Time periods
        if "today" in q:
            time_period = "today"
        elif "this week" in q:
            time_period = "this_week"
        elif "this month" in q:
            time_period = "this_month"
        elif "last month" in q:
            time_period = "last_month"
        elif "last 3 months" in q or "last three months" in q:
            time_period = "last_3_months"
        elif "last 6 months" in q or "last six months" in q:
            time_period = "last_6_months"
        elif "year to date" in q or "ytd" in q:
            time_period = "year_to_date"

        # Sort order
        if "highest" in q or "top" in q or "best" in q:
            sort_order = "DESC"
        elif "lowest" in q or "worst" in q or "bottom" in q:
            sort_order = "ASC"

        return QueryIntent(
            intent=intent,
            entity_type=entity_type,
            entity_value=entity_value,
            metric=metric,
            filters=filters,
            time_period=time_period,
            grouping=grouping,
            sort_by=metric if metric else "revenue",
            sort_order=sort_order,
            limit=limit,
            comparison_entities=comparison_entities,
        )

# ============================================================
# DYNAMIC SQL BUILDER
# ============================================================

class SQLBuilder:
    """Build SQL queries dynamically from QueryIntent."""

    def __init__(self):
        self.base_table = "delivery_reports"

    def build(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        """Return (sql, params) for the given intent."""
        if intent.intent == "aggregate":
            return self._build_aggregate(intent)
        elif intent.intent == "ranking":
            return self._build_ranking(intent)
        elif intent.intent == "comparison":
            return self._build_comparison(intent)
        elif intent.intent == "trend":
            return self._build_trend(intent)
        elif intent.intent == "list":
            return self._build_list(intent)
        else:  # summary
            return self._build_summary(intent)

    def _apply_filters(self, filters: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Build WHERE clause from filters dict."""
        conditions = []
        params = {}
        for key, value in filters.items():
            if value:
                # Map filter keys to column names
                col_map = {
                    "division": "division",
                    "city": "ship_to_city",
                    "warehouse": "warehouse",
                    "dealer": "customer_name",
                    "model": "customer_model",
                    "sales_office": "sales_office",
                    "sales_manager": "sales_manager",
                }
                col = col_map.get(key, key)
                conditions.append(f"LOWER({col}) = LOWER(:{key})")
                params[key] = value
        return " AND ".join(conditions) if conditions else "1=1", params

    def _apply_time_period(self, time_period: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        """Add time filter based on time_period."""
        if not time_period:
            return "", {}
        now = datetime.now()
        params = {}
        if time_period == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            cond = "dn_create_date >= :start_date"
            params["start_date"] = start
        elif time_period == "this_week":
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            cond = "dn_create_date >= :start_date"
            params["start_date"] = start
        elif time_period == "this_month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            cond = "dn_create_date >= :start_date"
            params["start_date"] = start
        elif time_period == "last_month":
            start = (now.replace(day=1) - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
            cond = "dn_create_date BETWEEN :start_date AND :end_date"
            params["start_date"] = start
            params["end_date"] = end
        elif time_period == "last_3_months":
            start = now - timedelta(days=90)
            cond = "dn_create_date >= :start_date"
            params["start_date"] = start
        elif time_period == "last_6_months":
            start = now - timedelta(days=180)
            cond = "dn_create_date >= :start_date"
            params["start_date"] = start
        elif time_period == "year_to_date":
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            cond = "dn_create_date >= :start_date"
            params["start_date"] = start
        else:
            return "", {}
        return cond, params

    def _build_aggregate(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        """Build aggregate query (e.g., total revenue)."""
        metric = intent.metric or "revenue"
        metric_col_map = {
            "revenue": "COALESCE(SUM(dn_amount), 0) AS value",
            "units": "COALESCE(SUM(dn_qty), 0) AS value",
            "dns": "COUNT(DISTINCT dn_no) AS value",
            "pending": "COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END) AS value",
            "delivery_days": "ROUND(AVG(pod_date - good_issue_date), 2) AS value",
            "pod_days": "ROUND(AVG(pod_date - good_issue_date), 2) AS value",  # alias
            "pgi_days": "ROUND(AVG(good_issue_date - dn_create_date), 2) AS value",
        }
        select = metric_col_map.get(metric, "COALESCE(SUM(dn_amount), 0) AS value")

        # Build WHERE
        filter_clause, filter_params = self._apply_filters(intent.filters)
        time_clause, time_params = self._apply_time_period(intent.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}

        sql = f"""
            SELECT {select}
            FROM {self.base_table}
            WHERE {where_str}
        """
        return sql, params

    def _build_ranking(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        """Build ranking query (top N by metric)."""
        metric = intent.sort_by or intent.metric or "revenue"
        metric_col_map = {
            "revenue": "COALESCE(SUM(dn_amount), 0) AS metric_value",
            "units": "COALESCE(SUM(dn_qty), 0) AS metric_value",
            "dns": "COUNT(DISTINCT dn_no) AS metric_value",
            "pending": "COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END) AS metric_value",
        }
        select_metric = metric_col_map.get(metric, "COALESCE(SUM(dn_amount), 0) AS metric_value")

        # Determine entity to group by
        entity = intent.entity_type or "division"
        entity_col_map = {
            "dealer": "customer_name",
            "model": "customer_model",
            "division": "division",
            "city": "ship_to_city",
            "warehouse": "warehouse",
            "sales_office": "sales_office",
            "sales_manager": "sales_manager",
        }
        group_col = entity_col_map.get(entity, "division")
        select_entity = f"{group_col} AS entity_name"

        # Build WHERE
        filter_clause, filter_params = self._apply_filters(intent.filters)
        time_clause, time_params = self._apply_time_period(intent.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}

        order = intent.sort_order or "DESC"
        limit = intent.limit or 10
        sql = f"""
            SELECT {select_entity}, {select_metric}
            FROM {self.base_table}
            WHERE {where_str}
            GROUP BY {group_col}
            ORDER BY metric_value {order}
            LIMIT :limit
        """
        params["limit"] = limit
        return sql, params

    def _build_comparison(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        """Build comparison query (two entities)."""
        if not intent.comparison_entities or len(intent.comparison_entities) < 2:
            # fallback to aggregate on two filters
            return self._build_aggregate(intent)

        entity1, entity2 = intent.comparison_entities
        # Determine entity type from intent or default to division
        entity = intent.entity_type or "division"
        col_map = {
            "dealer": "customer_name",
            "model": "customer_model",
            "division": "division",
            "city": "ship_to_city",
            "warehouse": "warehouse",
            "sales_office": "sales_office",
            "sales_manager": "sales_manager",
        }
        col = col_map.get(entity, "division")

        metric = intent.metric or "revenue"
        metric_expr = {
            "revenue": "COALESCE(SUM(dn_amount), 0)",
            "units": "COALESCE(SUM(dn_qty), 0)",
            "dns": "COUNT(DISTINCT dn_no)",
            "pending": "COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END)",
        }.get(metric, "COALESCE(SUM(dn_amount), 0)")

        # Build WHERE with two entities
        filter_clause, filter_params = self._apply_filters(intent.filters)
        time_clause, time_params = self._apply_time_period(intent.time_period)
        where = [f"LOWER({col}) IN (LOWER(:e1), LOWER(:e2))"]
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where)
        params = {"e1": entity1, "e2": entity2, **filter_params, **time_params}

        sql = f"""
            SELECT
                {col} AS entity_name,
                {metric_expr} AS metric_value
            FROM {self.base_table}
            WHERE {where_str}
            GROUP BY {col}
        """
        return sql, params

    def _build_trend(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        """Build trend query (time series)."""
        metric = intent.metric or "revenue"
        metric_expr = {
            "revenue": "COALESCE(SUM(dn_amount), 0)",
            "units": "COALESCE(SUM(dn_qty), 0)",
            "dns": "COUNT(DISTINCT dn_no)",
            "pending": "COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END)",
        }.get(metric, "COALESCE(SUM(dn_amount), 0)")

        grouping = intent.grouping or "month"
        group_expr = {
            "month": "TO_CHAR(dn_create_date, 'YYYY-MM')",
            "week": "TO_CHAR(dn_create_date, 'IYYY-WW')",
            "day": "TO_CHAR(dn_create_date, 'YYYY-MM-DD')",
        }.get(grouping, "TO_CHAR(dn_create_date, 'YYYY-MM')")

        # Build WHERE
        filter_clause, filter_params = self._apply_filters(intent.filters)
        time_clause, time_params = self._apply_time_period(intent.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}

        sql = f"""
            SELECT
                {group_expr} AS period,
                {metric_expr} AS metric_value
            FROM {self.base_table}
            WHERE {where_str}
            GROUP BY {group_expr}
            ORDER BY period
        """
        return sql, params

    def _build_list(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        """Build list query (list DNs, dealers, etc.)."""
        # For simplicity, treat as ranking with entity_type = 'dn' or similar
        # We'll return a simple list of entities
        entity = intent.entity_type or "dn"
        col_map = {
            "dealer": "customer_name",
            "model": "customer_model",
            "division": "division",
            "city": "ship_to_city",
            "warehouse": "warehouse",
            "sales_office": "sales_office",
            "sales_manager": "sales_manager",
            "dn": "dn_no",
        }
        col = col_map.get(entity, "dn_no")
        select_col = f"TRIM({col}) AS entity_name"
        group_col = col

        # Build WHERE
        filter_clause, filter_params = self._apply_filters(intent.filters)
        time_clause, time_params = self._apply_time_period(intent.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}

        limit = intent.limit or 20
        sql = f"""
            SELECT DISTINCT {select_col}
            FROM {self.base_table}
            WHERE {where_str}
            ORDER BY entity_name
            LIMIT :limit
        """
        params["limit"] = limit
        return sql, params

    def _build_summary(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        """Build a summary query (multiple aggregates)."""
        # Return a few key metrics
        filter_clause, filter_params = self._apply_filters(intent.filters)
        time_clause, time_params = self._apply_time_period(intent.time_period)
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
                ROUND(AVG(pod_date - good_issue_date), 2) AS avg_delivery_days
            FROM {self.base_table}
            WHERE {where_str}
        """
        return sql, params

# ============================================================
# REPOSITORY (EXECUTES QUERIES)
# ============================================================

class LogisticsRepository:
    def __init__(self, session: Session):
        self.session = session

    def execute_query(self, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts."""
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql), params)
                rows = result.fetchall()
                if not rows:
                    return []
                # Convert to list of dicts
                columns = result.keys()
                return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(f"SQL execution error: {e}")
            return []

# ============================================================
# RESPONSE FORMATTER
# ============================================================

class ResponseFormatter:
    @staticmethod
    def format_results(intent: QueryIntent, results: List[Dict[str, Any]]) -> str:
        """Format query results into WhatsApp-friendly message."""
        if not results:
            return "No data found for your query."

        if intent.intent == "aggregate":
            return ResponseFormatter._format_aggregate(intent, results)
        elif intent.intent == "ranking":
            return ResponseFormatter._format_ranking(intent, results)
        elif intent.intent == "comparison":
            return ResponseFormatter._format_comparison(intent, results)
        elif intent.intent == "trend":
            return ResponseFormatter._format_trend(intent, results)
        elif intent.intent == "list":
            return ResponseFormatter._format_list(intent, results)
        else:  # summary
            return ResponseFormatter._format_summary(intent, results)

    @staticmethod
    def _format_aggregate(intent: QueryIntent, results: List[Dict]) -> str:
        row = results[0]
        metric = intent.metric or "revenue"
        label = {
            "revenue": "💰 Revenue",
            "units": "📦 Units",
            "dns": "🚚 DNs",
            "pending": "⏳ Pending DNs",
            "delivery_days": "📅 Avg Delivery Days",
            "pod_days": "📅 Avg POD Days",
            "pgi_days": "📅 Avg PGI Days",
        }.get(metric, "Value")
        value = row.get("value", "N/A")
        if metric == "revenue":
            value = _format_currency(value)
        elif metric in ["units", "dns", "pending"]:
            value = _format_number(value)
        elif metric in ["delivery_days", "pod_days", "pgi_days"]:
            value = f"{value:.1f} days"
        return f"{label}: {value}"

    @staticmethod
    def _format_ranking(intent: QueryIntent, results: List[Dict]) -> str:
        entity_label = intent.entity_type or "Division"
        metric = intent.sort_by or intent.metric or "revenue"
        metric_label = {
            "revenue": "Revenue",
            "units": "Units",
            "dns": "DNs",
            "pending": "Pending DNs",
        }.get(metric, "Value")

        lines = [f"🏆 *Top {len(results)} {entity_label.capitalize()} by {metric_label}*", ""]
        for i, row in enumerate(results, 1):
            name = row.get("entity_name", "Unknown")
            value = row.get("metric_value", 0)
            if metric == "revenue":
                value = _format_currency(value)
            elif metric in ["units", "dns", "pending"]:
                value = _format_number(value)
            else:
                value = f"{value:.1f}"
            lines.append(f"{i}. {name}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _format_comparison(intent: QueryIntent, results: List[Dict]) -> str:
        if not results or len(results) < 2:
            return "Comparison data not available."
        entity_label = intent.entity_type or "Entity"
        metric = intent.metric or "revenue"
        metric_label = {
            "revenue": "Revenue",
            "units": "Units",
            "dns": "DNs",
            "pending": "Pending DNs",
        }.get(metric, "Value")

        lines = [f"📊 *Comparison ({metric_label})*", ""]
        for row in results:
            name = row.get("entity_name", "Unknown")
            value = row.get("metric_value", 0)
            if metric == "revenue":
                value = _format_currency(value)
            elif metric in ["units", "dns", "pending"]:
                value = _format_number(value)
            else:
                value = f"{value:.1f}"
            lines.append(f"{name}: {value}")
        # Add difference if possible
        if len(results) == 2:
            v1 = _number(results[0].get("metric_value", 0))
            v2 = _number(results[1].get("metric_value", 0))
            diff = v1 - v2
            diff_str = _format_currency(diff) if metric == "revenue" else f"{diff:+,.0f}"
            lines.append(f"\nDifference: {diff_str}")
        return "\n".join(lines)

    @staticmethod
    def _format_trend(intent: Intent, results: List[Dict]) -> str:
        metric = intent.metric or "revenue"
        metric_label = {
            "revenue": "Revenue",
            "units": "Units",
            "dns": "DNs",
            "pending": "Pending DNs",
        }.get(metric, "Value")
        lines = [f"📈 *{metric_label} Trend*", ""]
        for row in results:
            period = row.get("period", "N/A")
            value = row.get("metric_value", 0)
            if metric == "revenue":
                value = _format_currency(value)
            elif metric in ["units", "dns", "pending"]:
                value = _format_number(value)
            else:
                value = f"{value:.1f}"
            lines.append(f"{period}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _format_list(intent: Intent, results: List[Dict]) -> str:
        entity_label = intent.entity_type or "Item"
        lines = [f"📋 *List of {entity_label.capitalize()}*", ""]
        for i, row in enumerate(results, 1):
            name = row.get("entity_name", "Unknown")
            lines.append(f"{i}. {name}")
        return "\n".join(lines)

    @staticmethod
    def _format_summary(intent: Intent, results: List[Dict]) -> str:
        row = results[0]
        lines = [
            "📊 *Summary*",
            "",
            f"💰 Revenue: {_format_currency(row.get('revenue', 0))}",
            f"📦 Units: {_format_number(row.get('units', 0))}",
            f"🚚 DNs: {_format_number(row.get('dns', 0))}",
            f"⏳ Pending DNs: {_format_number(row.get('pending', 0))}",
            f"📅 Avg Delivery Days: {row.get('avg_delivery_days', 0):.1f} days",
        ]
        return "\n".join(lines)

# ============================================================
# MAIN SERVICE
# ============================================================

class GroqService:
    def __init__(self) -> None:
        self._version = VERSION
        self.parser = QueryParser()
        self.sql_builder = SQLBuilder()
        self.repo = LogisticsRepository(SessionLocal())
        self.formatter = ResponseFormatter()
        logger.info(f"✅ GroqService v{self._version} initialized")
        logger.info(f"   Groq: {'✅' if GROQ_AVAILABLE else '❌'}")

    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        try:
            msg = message.strip()
            if not msg:
                return self._get_welcome()

            if msg == "99":
                logger.info("[GroqService] Exit signal")
                return "99"

            if msg.lower() in ["hi", "hello", "hey", "start", "menu", "help"]:
                return self._get_welcome()

            logger.info(f"[GroqService] Processing: '{msg}' from {sender}")

            # Parse query
            intent = self.parser.parse(msg)

            # Build SQL
            sql, params = self.sql_builder.build(intent)

            # Execute
            results = self.repo.execute_query(sql, params)

            # Format response
            response = self.formatter.format_results(intent, results)

            return response

        except Exception as e:
            logger.exception(f"[GroqService] Error: {e}")
            return "⚠️ An error occurred. Please try again."

    def _get_welcome(self) -> str:
        return "\n".join([
            "🤖 *AI Logistics Assistant*",
            "",
            "I can answer questions about:",
            "• Revenue, units, DNs, pending orders",
            "• Rankings (top dealers, models, cities, warehouses)",
            "• Comparisons between any entities",
            "• Trends over time (monthly, weekly, daily)",
            "• Lists of entities",
            "• Summaries with filters",
            "",
            "Examples:",
            "• What is total revenue?",
            "• Top 5 dealers by revenue in Lahore",
            "• Revenue of Washing Machine division",
            "• Compare HWM120 and HWM150",
            "• Monthly trend of revenue",
            "• Pending DNs for dealer ABC",
            "• List all cities",
            "• Summary for last month",
            "",
            "Reply *99* to return to menu."
        ])

# ============================================================
# SINGLETON
# ============================================================

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
            _service_instance = GroqService()
    return _service_instance

__all__ = [
    "GroqService",
    "get_groq_service",
    "VERSION"
]
