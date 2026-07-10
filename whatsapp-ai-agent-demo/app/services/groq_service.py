#!/usr/bin/env python3
# ============================================================
# FILE: app/services/groq_service.py
# VERSION: 10.0 - GROQ POWERS EVERY ANSWER (TEMPLATE STYLE)
# PURPOSE: Answer any logistics question using Groq for intent
#          and formatting, with PostgreSQL for accurate data.
#          Outputs WhatsApp‑style responses with headers and AI insights.
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

VERSION = "10.0"

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

def _format_percent(ratio: float) -> str:
    return f"{ratio:.1f}%"

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

# ============================================================
# GROQ INTENT PARSER (FIRST GROQ CALL)
# ============================================================

class GroqIntentParser:
    def __init__(self):
        # Typo mapping
        self.typo_map = {
            "hight": "highest",
            "higest": "highest",
            "sale": "sales",
            "delear": "dealer",
            "warehous": "warehouse",
            "produt": "product",
            "cities": "city",
        }
        # Phrases that indicate a specific intent/entity
        self.show_phrases = {
            "dealer performance": {"intent": "dashboard", "entity_type": "dealer"},
            "dn status": {"intent": "details", "entity_type": "dn"},
            "warehouse kpi": {"intent": "dashboard", "entity_type": "warehouse"},
            "pgi": {"intent": "aggregate", "metric": "pgi_percent"},
            "pod": {"intent": "aggregate", "metric": "pod_percent"},
            "revenue": {"intent": "aggregate", "metric": "revenue"},
            "units sold": {"intent": "aggregate", "metric": "units"},
            "delivery delay": {"intent": "aggregate", "metric": "delivery_days"},
            "transporter": {"intent": "list", "entity_type": "transporter"},  # not in schema, map to something
            "sales office": {"intent": "list", "entity_type": "sales_office"},
            "city performance": {"intent": "dashboard", "entity_type": "city"},
            "product model": {"intent": "dashboard", "entity_type": "model"},
            "pending dn": {"intent": "ranking", "entity_type": "dn", "metric": "pending"},
            "distance": {"intent": "aggregate", "metric": "distance"},  # not in schema
            "delivery target": {"intent": "aggregate", "metric": "delivery_target"},  # not in schema
            "ai insights": {"intent": "summary"},
            "stock": {"intent": "aggregate", "metric": "units"},
            "warehouse utilization": {"intent": "dashboard", "entity_type": "warehouse"},
            "dealer ranking": {"intent": "ranking", "entity_type": "dealer", "metric": "revenue"},
            "monthly trend": {"intent": "trend", "grouping": "month", "metric": "revenue"},
        }

    def _normalize(self, query: str) -> str:
        q = query.lower()
        for typo, correct in self.typo_map.items():
            q = q.replace(typo, correct)
        return q

    def parse(self, query: str) -> QueryIntent:
        normalized = self._normalize(query)
        if GROQ_AVAILABLE and GROQ_CLIENT:
            return self._parse_with_groq(normalized)
        return self._parse_with_fallback(normalized)

    def _parse_with_groq(self, query: str) -> QueryIntent:
        prompt = f"""
You are a Logistics AI assistant. Extract structured information from the user's question.

Return ONLY valid JSON with these fields:
- intent: one of ['ranking', 'aggregate', 'comparison', 'trend', 'list', 'summary', 'details', 'dashboard']
- entity_type: one of ['dealer', 'city', 'warehouse', 'division', 'model', 'sales_office', 'sales_manager', 'dn'] or null
- entity_value: the specific name mentioned, or null
- metric: one of ['revenue', 'units', 'dns', 'pending', 'delivery_days', 'pgi_percent', 'pod_percent'] or null
- filters: object with keys like division, city, warehouse, dealer, model, etc.
- time_period: one of ['today', 'this_week', 'this_month', 'last_month', 'last_3_months', 'last_6_months', 'year_to_date'] or null
- grouping: one of ['month', 'week', 'day', 'division', 'city', 'dealer', 'warehouse'] or null
- sort_by: metric to sort by (e.g., 'revenue')
- sort_order: 'ASC' or 'DESC'
- limit: integer (default 10)
- comparison_entities: list of two entities if comparing, else null
- extra_columns: list of additional aggregate columns
- fields: for details, list of column names to select

Examples:
- "Show me the dealer performance" → intent='dashboard', entity_type='dealer'
- "Show me the revenue" → intent='aggregate', metric='revenue'
- "Show me the pgi" → intent='aggregate', metric='pgi_percent'
- "Top 5 dealers by revenue in Lahore" → intent='ranking', entity_type='dealer', metric='revenue', filters={{'city':'Lahore'}}, limit=5

Question: "{query}"

Return valid JSON only.
"""
        try:
            response = GROQ_CLIENT.chat.completions.create(
                model="llama3-70b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
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
                extra_columns=data.get("extra_columns"),
                fields=data.get("fields"),
            )
        except Exception as e:
            logger.error(f"Groq intent parse error: {e}")
            return self._parse_with_fallback(query)

    def _parse_with_fallback(self, query: str) -> QueryIntent:
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
        extra_columns = None
        fields = None

        # 1. Detect "show me the X" phrases
        for phrase, mapping in self.show_phrases.items():
            if phrase in q:
                intent = mapping.get("intent", "summary")
                entity_type = mapping.get("entity_type")
                metric = mapping.get("metric")
                grouping = mapping.get("grouping")
                if "entity_type" in mapping and not entity_value:
                    # Try to extract specific value after "for" or "of"
                    m = re.search(r"for\s+([\w\s\-]+)", q)
                    if m:
                        entity_value = m.group(1).strip()
                    else:
                        # If not, maybe the whole query is just the phrase, leave null
                        pass
                break

        # 2. Detect ranking (top/highest/best/worst/lowest/bottom)
        if intent == "summary" and re.search(r"top|highest|best|worst|lowest|bottom", q):
            intent = "ranking"
            m = re.search(r"top\s*(\d+)", q)
            if m:
                limit = int(m.group(1))
            # Determine entity type
            if "dealer" in q or "customer" in q:
                entity_type = "dealer"
            elif "city" in q:
                entity_type = "city"
            elif "warehouse" in q:
                entity_type = "warehouse"
            elif "division" in q or "product" in q:
                entity_type = "division"
            elif "model" in q:
                entity_type = "model"
            elif "sales office" in q:
                entity_type = "sales_office"
            elif "sales manager" in q:
                entity_type = "sales_manager"
            elif "dn" in q:
                entity_type = "dn"
            else:
                entity_type = "division"
            # Metric
            if "units" in q:
                metric = "units"
            elif "dns" in q:
                metric = "dns"
            elif "pending" in q:
                metric = "pending"
            else:
                metric = "revenue"
            sort_by = metric
            # Sort order
            if "highest" in q or "top" in q or "best" in q:
                sort_order = "DESC"
            elif "lowest" in q or "worst" in q or "bottom" in q:
                sort_order = "ASC"
            # Extract filters (city)
            m = re.search(r"in\s+([\w\s\-]+)", q)
            if m:
                filters["city"] = m.group(1).strip()

        # 3. Detect comparison
        if intent == "summary" and re.search(r"compare|vs|versus", q):
            intent = "comparison"
            m = re.search(r"compare\s+(.+?)\s+(?:and|vs|versus)\s+(.+)", q)
            if m:
                comparison_entities = [m.group(1).strip(), m.group(2).strip()]
                # Determine entity type
                if "warehouse" in q:
                    entity_type = "warehouse"
                elif "city" in q:
                    entity_type = "city"
                elif "dealer" in q:
                    entity_type = "dealer"
                else:
                    entity_type = "division"
                metric = "revenue"

        # 4. Detect trend
        if intent == "summary" and ("trend" in q or "monthly" in q or "weekly" in q or "daily" in q):
            intent = "trend"
            if "monthly" in q:
                grouping = "month"
            elif "weekly" in q:
                grouping = "week"
            elif "daily" in q:
                grouping = "day"
            if not metric:
                metric = "revenue"

        # 5. Detect list
        if intent == "summary" and "list" in q:
            intent = "list"
            if "dealer" in q:
                entity_type = "dealer"
            elif "city" in q:
                entity_type = "city"
            elif "warehouse" in q:
                entity_type = "warehouse"
            elif "division" in q or "product" in q:
                entity_type = "division"
            else:
                entity_type = "city"

        # 6. Detect details (DN)
        if intent == "summary" and ("dn" in q and ("details" in q or "show" in q)):
            intent = "details"
            fields = ["dn_no", "customer_name", "customer_model", "warehouse", "ship_to_city",
                      "dn_qty", "dn_amount", "pgi_date", "pod_date", "pending_flag"]
            m = re.search(r"dn\s*[#:]?\s*([A-Za-z0-9\-]+)", q)
            if m:
                filters["dn_no"] = m.group(1)

        # 7. If still summary, treat as dashboard if entity_type is detected
        if intent == "summary" and entity_type and entity_value:
            intent = "dashboard"

        # Set default metric if still not set
        if not metric:
            metric = "revenue"

        # Extract time period
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

        logger.info(f"Fallback parsed: intent={intent}, entity_type={entity_type}, metric={metric}, filters={filters}")
        return QueryIntent(
            intent=intent,
            entity_type=entity_type,
            entity_value=entity_value,
            metric=metric,
            filters=filters,
            time_period=time_period,
            grouping=grouping,
            sort_by=sort_by if sort_by else metric,
            sort_order=sort_order,
            limit=limit,
            comparison_entities=comparison_entities,
            extra_columns=extra_columns,
            fields=fields,
        )

# ============================================================
# QUERY PLANNER & SQL BUILDER (unchanged from v9.1)
# ============================================================

class SQLBuilder:
    def __init__(self):
        self.table = "delivery_reports"
        self.field_map = {
            "dn_no": "dn_no",
            "customer_name": "customer_name",
            "dealer_code": "dealer_code",
            "customer_code": "customer_code",
            "customer_model": "customer_model",
            "division": "division",
            "warehouse": "warehouse",
            "warehouse_code": "warehouse_code",
            "ship_to_city": "ship_to_city",
            "sales_office": "sales_office",
            "sales_manager": "sales_manager",
            "delivery_status": "delivery_status",
            "pgi_status": "pgi_status",
            "pod_status": "pod_status",
            "pending_flag": "pending_flag",
            "dn_amount": "dn_amount",
            "dn_qty": "dn_qty",
            "dn_create_date": "dn_create_date",
            "good_issue_date": "good_issue_date",
            "pod_date": "pod_date",
        }
        self.extra_exprs = {
            "dealers_count": "COUNT(DISTINCT customer_name)",
            "cities_count": "COUNT(DISTINCT ship_to_city)",
            "products_count": "COUNT(DISTINCT customer_model)",
            "warehouses_count": "COUNT(DISTINCT warehouse)",
            "pgi_percent": "ROUND(100.0 * COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2)",
            "pod_percent": "ROUND(100.0 * COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2)",
            "dns_count": "COUNT(DISTINCT dn_no)",
            "avg_delivery_days": "ROUND(AVG(pod_date - good_issue_date), 2)",
        }

    def build(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        if intent.intent == "dashboard":
            return self._build_dashboard(intent)
        elif intent.intent == "ranking":
            return self._build_ranking(intent)
        elif intent.intent == "comparison":
            return self._build_comparison(intent)
        elif intent.intent == "trend":
            return self._build_trend(intent)
        elif intent.intent == "list":
            return self._build_list(intent)
        elif intent.intent == "details":
            return self._build_details(intent)
        elif intent.intent == "aggregate":
            return self._build_aggregate(intent)
        else:
            return self._build_summary(intent)

    def _apply_filters(self, filters: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        conditions = []
        params = {}
        for key, value in filters.items():
            if value:
                col = self.field_map.get(key, key)
                conditions.append(f"LOWER({col}) = LOWER(:{key})")
                params[key] = value
        return " AND ".join(conditions) if conditions else "1=1", params

    def _apply_time_period(self, time_period: Optional[str]) -> Tuple[str, Dict[str, Any]]:
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

    def _build_dashboard(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        entity_type = intent.entity_type or "dealer"
        entity_value = intent.entity_value
        if not entity_value:
            entity_value = intent.filters.get(entity_type)
        if not entity_value:
            entity_value = "Unknown"

        filter_clause, filter_params = self._apply_filters(intent.filters)
        time_clause, time_params = self._apply_time_period(intent.time_period)
        where = [f"LOWER({entity_type}) = LOWER(:entity_value)"]
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where)
        params = {"entity_value": entity_value, **filter_params, **time_params}
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

    def _build_ranking(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        metric = intent.sort_by or intent.metric or "revenue"
        entity = intent.entity_type or "division"
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
        if intent.extra_columns:
            for col in intent.extra_columns:
                expr = self.extra_exprs.get(col)
                if expr:
                    extra_selects.append(f"{expr} AS {col}")
        extra_str = ", " + ", ".join(extra_selects) if extra_selects else ""

        if intent.entity_value:
            intent.filters[entity] = intent.entity_value
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
            SELECT {select_entity}, {metric_select}{extra_str}
            FROM {self.table}
            WHERE {where_str}
            GROUP BY {group_col}
            ORDER BY metric_value {order}
            LIMIT :limit
        """
        params["limit"] = limit
        return sql, params

    def _build_comparison(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        if not intent.comparison_entities or len(intent.comparison_entities) < 2:
            return self._build_aggregate(intent)
        entity1, entity2 = intent.comparison_entities
        entity = intent.entity_type or "division"
        col = self.field_map.get(entity, "division")
        metric = intent.metric or "revenue"
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
            SELECT {col} AS entity_name, {metric_expr} AS metric_value
            FROM {self.table}
            WHERE {where_str}
            GROUP BY {col}
        """
        return sql, params

    def _build_trend(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        metric = intent.metric or "revenue"
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

        grouping = intent.grouping or "month"
        group_expr = {
            "month": "TO_CHAR(dn_create_date, 'YYYY-MM')",
            "week": "TO_CHAR(dn_create_date, 'IYYY-WW')",
            "day": "TO_CHAR(dn_create_date, 'YYYY-MM-DD')",
        }.get(grouping, "TO_CHAR(dn_create_date, 'YYYY-MM')")

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
            SELECT {group_expr} AS period, {metric_expr} AS metric_value
            FROM {self.table}
            WHERE {where_str}
            GROUP BY {group_expr}
            ORDER BY period
        """
        return sql, params

    def _build_list(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        entity = intent.entity_type or "city"
        col = self.field_map.get(entity, "ship_to_city")
        select_col = f"TRIM({col}) AS entity_name"

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
            FROM {self.table}
            WHERE {where_str}
            ORDER BY entity_name
            LIMIT :limit
        """
        params["limit"] = limit
        return sql, params

    def _build_details(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        fields = intent.fields or ["dn_no", "customer_name", "customer_model", "warehouse", "ship_to_city",
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
            SELECT {select_clause}
            FROM {self.table}
            WHERE {where_str}
            ORDER BY dn_no
            LIMIT :limit
        """
        params["limit"] = limit
        return sql, params

    def _build_aggregate(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
        metric = intent.metric or "revenue"
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
        filter_clause, filter_params = self._apply_filters(intent.filters)
        time_clause, time_params = self._apply_time_period(intent.time_period)
        where = []
        if filter_clause and filter_clause != "1=1":
            where.append(filter_clause)
        if time_clause:
            where.append(time_clause)
        where_str = " AND ".join(where) if where else "1=1"
        params = {**filter_params, **time_params}
        sql = f"SELECT {select} FROM {self.table} WHERE {where_str}"
        return sql, params

    def _build_summary(self, intent: QueryIntent) -> Tuple[str, Dict[str, Any]]:
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
                ROUND(100.0 * COUNT(CASE WHEN pgi_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS pgi_percent,
                ROUND(100.0 * COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS pod_percent,
                ROUND(AVG(pod_date - good_issue_date), 2) AS avg_delivery_days
            FROM {self.table}
            WHERE {where_str}
        """
        return sql, params

# ============================================================
# REPOSITORY
# ============================================================

class LogisticsRepository:
    def __init__(self, session: Session):
        self.session = session

    def execute_query(self, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
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

# ============================================================
# BUSINESS RULES ENGINE (keep as is)
# ============================================================

class BusinessRulesEngine:
    @staticmethod
    def enrich_dashboard(data: Dict[str, Any]) -> Dict[str, Any]:
        pgi = data.get("pgi_percent", 0)
        if pgi >= 95:
            data["rating"] = "Excellent"
        elif pgi >= 80:
            data["rating"] = "Good"
        elif pgi >= 60:
            data["rating"] = "Average"
        else:
            data["rating"] = "Needs Improvement"
        data["delivery_target_met"] = data.get("avg_delivery_days", 999) <= 3.0
        return data

    @staticmethod
    def enrich_ranking(results: List[Dict]) -> List[Dict]:
        return results

# ============================================================
# GROQ RESPONSE FORMATTER (SECOND GROQ CALL) – TEMPLATE STYLE
# ============================================================

class GroqResponseFormatter:
    @staticmethod
    def format(intent: QueryIntent, results: List[Dict], query: str) -> Optional[str]:
        if not GROQ_AVAILABLE or not GROQ_CLIENT:
            logger.warning("Groq not available, skipping response formatting")
            return None

        # Prepare data summary
        if not results:
            data_summary = "No data found."
        else:
            # Build a compact representation
            if intent.intent in ["summary", "aggregate", "dashboard"]:
                row = results[0]
                data_summary = ", ".join([f"{k}: {v}" for k, v in row.items()])
            elif intent.intent == "ranking":
                top = results[:10]
                lines = []
                for r in top:
                    name = r.get("entity_name", "Unknown")
                    val = r.get("metric_value", 0)
                    extra = ""
                    if intent.extra_columns:
                        extra = " (" + ", ".join([f"{c}: {r.get(c, 'N/A')}" for c in intent.extra_columns if c in r]) + ")"
                    lines.append(f"{name}: {val}{extra}")
                data_summary = "\n".join(lines)
            elif intent.intent == "details":
                lines = []
                for r in results[:10]:
                    row_parts = [f"{k}: {v}" for k, v in r.items()]
                    lines.append(" | ".join(row_parts))
                data_summary = "\n".join(lines)
            else:
                data_summary = "\n".join([str(r) for r in results[:10]])

        # Determine header based on intent
        header_map = {
            "dashboard": "📊 DASHBOARD",
            "ranking": "🏆 RANKING",
            "comparison": "⚖️ COMPARISON",
            "trend": "📈 TREND",
            "summary": "📊 SUMMARY",
            "aggregate": "📊 AGGREGATE",
            "details": "📋 DETAILS",
            "list": "📋 LIST",
        }
        header = header_map.get(intent.intent, "📊 RESULT")

        prompt = f"""
You are a helpful Logistics AI assistant for WhatsApp. Format the following query results into a clear, concise, and friendly response that follows the template style.

The response should start with a line of dashes, then the header (like "📊 NATIONAL KPI" or "👤 DEALER DASHBOARD"), then a blank line, then bullet points for each key metric, then a blank line, then an AI insight line (start with "🤖 AI Insight" or "🤖 AI:"), and finally "Reply another question or 99.".

Use appropriate emojis for each metric:
- 💰 for Revenue
- 📦 for Units
- 🚚 for DNs
- 🟢 for Delivered
- 🟡 for Pending
- 📊 for percentages
- 📅 for dates
- 🏆 for top rankings

Format numbers as currency with "PKR" if it's revenue, and add commas for thousands.

AI Insight should be a short, meaningful observation about the data (e.g., "Performance is stable." or "Revenue increased 5% this month.").

User question: "{query}"

Data:
{data_summary}

Now produce the final WhatsApp response.
"""
        try:
            resp = GROQ_CLIENT.chat.completions.create(
                model="llama3-70b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=400,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq response formatting error: {e}")
            return None

# ============================================================
# FALLBACK TEMPLATE FORMATTER (when Groq fails)
# ============================================================

class TemplateFormatter:
    @staticmethod
    def format(intent: QueryIntent, results: List[Dict]) -> str:
        if not results:
            return "No data found for your query."

        # Determine header
        header_map = {
            "dashboard": "📊 DASHBOARD",
            "ranking": "🏆 RANKING",
            "comparison": "⚖️ COMPARISON",
            "trend": "📈 TREND",
            "summary": "📊 SUMMARY",
            "aggregate": "📊 AGGREGATE",
            "details": "📋 DETAILS",
            "list": "📋 LIST",
        }
        header = header_map.get(intent.intent, "📊 RESULT")

        # Build response lines
        lines = [f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━", header, ""]
        if intent.intent in ["summary", "dashboard", "aggregate"]:
            row = results[0]
            # Show all relevant fields
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
        elif intent.intent == "ranking":
            entity_label = intent.entity_type or "Division"
            metric_label = intent.metric or "Revenue"
            lines.append(f"Top {len(results)} {entity_label.capitalize()} by {metric_label.capitalize()}:")
            for i, row in enumerate(results, 1):
                name = row.get("entity_name", "Unknown")
                val = row.get("metric_value", 0)
                if intent.metric == "revenue":
                    val = _format_currency(val)
                elif intent.metric in ["units", "dns", "pending"]:
                    val = _format_number(val)
                else:
                    val = f"{val:.1f}"
                lines.append(f"{i}. {name}: {val}")
        elif intent.intent == "details":
            lines.append("DN Details:")
            for row in results:
                parts = [f"{k}: {v}" for k, v in row.items()]
                lines.append(" | ".join(parts))
        else:
            lines.append(str(results))

        lines.append("")
        lines.append("🤖 AI Insight: Based on the data, performance appears stable.")
        lines.append("Reply another question or 99.")
        return "\n".join(lines)

# ============================================================
# MAIN SERVICE
# ============================================================

class GroqService:
    def __init__(self) -> None:
        self._version = VERSION
        self.intent_parser = GroqIntentParser()
        self.sql_builder = SQLBuilder()
        self.repo = LogisticsRepository(SessionLocal())
        self.business_rules = BusinessRulesEngine()
        self.groq_formatter = GroqResponseFormatter()
        self.template_formatter = TemplateFormatter()
        self.FOOTER = "\n\nReply *99* to return to the main menu."
        logger.info(f"✅ GroqService v{self._version} initialized")
        logger.info(f"   Groq: {'✅' if GROQ_AVAILABLE else '❌'}")

    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        try:
            msg = message.strip()
            if not msg:
                return self._get_welcome() + self.FOOTER

            if msg.isdigit() and msg != "99":
                return self._get_welcome() + self.FOOTER

            if msg == "99":
                logger.info("[GroqService] Exit signal")
                return "99"

            if msg.lower() in ["hi", "hello", "hey", "start", "menu", "help"]:
                return self._get_welcome() + self.FOOTER

            logger.info(f"[GroqService] Processing: '{msg}' from {sender}")

            # Step 1: Groq intent extraction
            intent = self.intent_parser.parse(msg)
            logger.info(f"Parsed intent: {intent.to_dict()}")

            # Step 2: Build SQL
            sql, params = self.sql_builder.build(intent)
            logger.info(f"SQL: {sql}")

            # Step 3: Execute query
            results = self.repo.execute_query(sql, params)
            logger.info(f"Found {len(results)} results")

            # Step 4: Apply business rules
            if intent.intent == "dashboard" and results:
                results[0] = self.business_rules.enrich_dashboard(results[0])
            elif intent.intent == "ranking" and results:
                results = self.business_rules.enrich_ranking(results)

            # Step 5: Format response – always try Groq first
            formatted = self.groq_formatter.format(intent, results, msg)
            if formatted:
                response = formatted
            else:
                # Fallback to template
                response = self.template_formatter.format(intent, results)

            return response + self.FOOTER

        except Exception as e:
            logger.exception(f"[GroqService] Error: {e}")
            return "⚠️ An error occurred. Please try again." + self.FOOTER

    def _get_welcome(self) -> str:
        return "\n".join([
            "🤖 *AI Logistics Assistant*",
            "",
            "I can answer questions about your delivery data. Try asking:",
            "",
            "• What is total revenue?",
            "• Top 5 dealers by revenue in Lahore",
            "• Show Refrigerator revenue in Gujranwala",
            "• Arshad Electronics (dealer dashboard)",
            "• Which warehouse has the highest revenue this month?",
            "• Compare Lahore and Karachi warehouses",
            "• Monthly trend of revenue",
            "• Pending DNs for dealer ABC",
            "• List all cities",
            "• Show DN details for DN12345",
            "• Which DN has the highest quantity?",
            "• Show me the dealer performance",
            "• Show me the revenue",
            "• Show me the pgi",
            "",
            "Reply *99* to return to this menu."
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
