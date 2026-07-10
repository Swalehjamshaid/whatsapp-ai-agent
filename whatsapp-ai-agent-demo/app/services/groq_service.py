#!/usr/bin/env python3
# ============================================================
# FILE: app/services/groq_service.py
# VERSION: 13.0 - PRODUCTION AI ORCHESTRATOR
# PURPOSE: Full AI pipeline: Groq understands, PostgreSQL provides facts,
#          Groq generates insights and every WhatsApp response.
# ============================================================

from __future__ import annotations

import logging
import os
import re
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional, Dict, List, Tuple, Union
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)

VERSION = "13.0"

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
    intent: str  # e.g., ranking, dashboard, aggregate, comparison, trend, list, details, advice
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
# KNOWLEDGE BASE (No SQL)
# ============================================================

class KnowledgeBase:
    """Answers common logistics definitions without database queries."""
    @staticmethod
    def answer(query: str) -> Optional[str]:
        q = query.lower()
        if "what is pod" in q or "pod definition" in q:
            return "POD stands for Proof of Delivery. It is a document signed by the recipient to confirm delivery of goods. POD is critical for billing and customer satisfaction."
        if "what is pgi" in q or "pgi definition" in q:
            return "PGI stands for Goods Issue. It indicates that the goods have been issued from the warehouse for delivery. PGI is the trigger for inventory reduction and billing."
        if "what is dn" in q or "dn definition" in q:
            return "DN stands for Delivery Note. It is a document that accompanies a shipment, listing the items delivered. It serves as a record of what has been dispatched."
        if "warehouse kpi" in q or "what is warehouse kpi" in q:
            return "Warehouse KPIs include metrics like PGI percentage, POD percentage, average delivery days, pending DNs, and inventory accuracy. These measure warehouse efficiency and service levels."
        if "delivery sla" in q or "what is sla" in q:
            return "SLA (Service Level Agreement) defines the expected delivery time based on distance. For Haier, typical SLA: 0-100 km = 1 day, 101-250 = 2 days, etc."
        # More knowledge entries can be added
        return None

# ============================================================
# CONVERSATION MEMORY (Context)
# ============================================================

class ConversationMemory:
    """Stores context from the current session for follow‑up questions."""
    def __init__(self):
        self.last_intent: Optional[QueryIntent] = None
        self.last_entity_type: Optional[str] = None
        self.last_entity_value: Optional[str] = None
        self.last_time_period: Optional[str] = None
        self.last_city: Optional[str] = None
        self.last_dealer: Optional[str] = None

    def update(self, intent: QueryIntent):
        self.last_intent = intent
        if intent.entity_type and intent.entity_value:
            self.last_entity_type = intent.entity_type
            self.last_entity_value = intent.entity_value
        if intent.time_period:
            self.last_time_period = intent.time_period
        if intent.filters.get("city"):
            self.last_city = intent.filters["city"]
        if intent.filters.get("dealer"):
            self.last_dealer = intent.filters["dealer"]

    def apply(self, intent: QueryIntent) -> QueryIntent:
        """Fill missing fields from context."""
        if not intent.entity_type and self.last_entity_type:
            intent.entity_type = self.last_entity_type
        if not intent.entity_value and self.last_entity_value:
            intent.entity_value = self.last_entity_value
        if not intent.time_period and self.last_time_period:
            intent.time_period = self.last_time_period
        if not intent.filters.get("city") and self.last_city:
            intent.filters["city"] = self.last_city
        if not intent.filters.get("dealer") and self.last_dealer:
            intent.filters["dealer"] = self.last_dealer
        return intent

# ============================================================
# ENTITY RESOLVER (Cache + DB lookup)
# ============================================================

class EntityResolver:
    """
    Extracts and resolves entity names from the query.
    In production, this would query PostgreSQL to get a list of known entities.
    """
    def __init__(self, session: Optional[Session] = None):
        self.session = session or SessionLocal()
        # Cache entity lists (refresh periodically)
        self._dealer_cache: List[str] = []
        self._city_cache: List[str] = []
        self._warehouse_cache: List[str] = []
        self._division_cache: List[str] = []
        self._model_cache: List[str] = []
        self._sales_office_cache: List[str] = []
        self._sales_manager_cache: List[str] = []
        self._dn_cache: List[str] = []
        self._load_caches()

    def _load_caches(self):
        """Load known entities from database (or use static fallback)."""
        try:
            # For production, replace with actual queries
            # Dealer names
            result = self.session.execute(text("SELECT DISTINCT customer_name FROM delivery_reports WHERE customer_name IS NOT NULL AND customer_name != '' LIMIT 500"))
            self._dealer_cache = [r[0] for r in result.fetchall()]
            # Cities
            result = self.session.execute(text("SELECT DISTINCT ship_to_city FROM delivery_reports WHERE ship_to_city IS NOT NULL AND ship_to_city != '' LIMIT 500"))
            self._city_cache = [r[0] for r in result.fetchall()]
            # Warehouses
            result = self.session.execute(text("SELECT DISTINCT warehouse FROM delivery_reports WHERE warehouse IS NOT NULL AND warehouse != '' LIMIT 500"))
            self._warehouse_cache = [r[0] for r in result.fetchall()]
            # Divisions
            result = self.session.execute(text("SELECT DISTINCT division FROM delivery_reports WHERE division IS NOT NULL AND division != '' LIMIT 500"))
            self._division_cache = [r[0] for r in result.fetchall()]
            # Models
            result = self.session.execute(text("SELECT DISTINCT customer_model FROM delivery_reports WHERE customer_model IS NOT NULL AND customer_model != '' LIMIT 500"))
            self._model_cache = [r[0] for r in result.fetchall()]
            # Sales offices
            result = self.session.execute(text("SELECT DISTINCT sales_office FROM delivery_reports WHERE sales_office IS NOT NULL AND sales_office != '' LIMIT 500"))
            self._sales_office_cache = [r[0] for r in result.fetchall()]
            # Sales managers
            result = self.session.execute(text("SELECT DISTINCT sales_manager FROM delivery_reports WHERE sales_manager IS NOT NULL AND sales_manager != '' LIMIT 500"))
            self._sales_manager_cache = [r[0] for r in result.fetchall()]
            # DN numbers
            result = self.session.execute(text("SELECT DISTINCT dn_no FROM delivery_reports WHERE dn_no IS NOT NULL AND dn_no != '' LIMIT 500"))
            self._dn_cache = [r[0] for r in result.fetchall()]
            logger.info(f"Entity cache loaded: {len(self._dealer_cache)} dealers, {len(self._city_cache)} cities, etc.")
        except Exception as e:
            logger.warning(f"Could not load entity cache from DB: {e}. Using static fallback.")
            # Static fallbacks
            self._dealer_cache = ["Arshad Electronics", "Al-Fatah", "Saudia Electronics", "Karim Traders"]
            self._city_cache = ["Lahore", "Karachi", "Islamabad", "Peshawar", "Rawalpindi", "Faisalabad", "Gujranwala"]
            self._warehouse_cache = ["Lahore", "Karachi", "Islamabad"]
            self._division_cache = ["Refrigerator", "Washing Machine", "Home Air Conditioner", "TV", "Freezer"]
            self._model_cache = ["HWM120-AS MG", "HWM150-AS MG", "RFD-200", "AC-12"]
            self._sales_office_cache = ["North", "South", "Central"]
            self._sales_manager_cache = ["Ali Khan", "Ahmed Raza"]
            self._dn_cache = ["DN12345", "DN67890"]

    def resolve(self, query: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Find entity type and value in the query.
        Returns (entity_type, entity_value) or (None, None).
        """
        q = query.lower()
        # Check each entity type
        for entity_type, cache in [
            ("dealer", self._dealer_cache),
            ("city", self._city_cache),
            ("warehouse", self._warehouse_cache),
            ("division", self._division_cache),
            ("model", self._model_cache),
            ("sales_office", self._sales_office_cache),
            ("sales_manager", self._sales_manager_cache),
            ("dn", self._dn_cache),
        ]:
            for name in cache:
                if name.lower() in q:
                    return entity_type, name
        return None, None

# ============================================================
# GROQ AI ORCHESTRATOR (Core)
# ============================================================

class GroqOrchestrator:
    """
    The main AI brain. Orchestrates intent, entities, SQL planning,
    insights, and final response generation – all using Groq where possible.
    """
    def __init__(self):
        self.memory = ConversationMemory()
        self.entity_resolver = EntityResolver()
        self.knowledge = KnowledgeBase()
        self.sql_builder = SQLBuilder()
        self.business_rules = BusinessRulesEngine()
        self.analytics = AnalyticsEngine()
        self.insight_engine = InsightEngine()
        self.formatter = WhatsAppFormatter()
        self._session = SessionLocal()
        self.FOOTER = "\n\nReply *99* to return to the main menu."

    def process(self, query: str, sender: str = "default") -> str:
        """
        Main entry point for a user question.
        Returns the final WhatsApp response.
        """
        try:
            logger.info(f"Processing: '{query}' from {sender}")

            # 1. Check knowledge base (no SQL needed)
            kb_answer = self.knowledge.answer(query)
            if kb_answer:
                return kb_answer + self.FOOTER

            # 2. Detect if this is a follow-up (context-aware)
            # For simplicity, we'll parse fresh but then apply memory.

            # 3. Primary: Use Groq to understand intent and entities
            intent = self._understand_with_groq(query)

            # 4. Apply conversation memory
            intent = self.memory.apply(intent)

            # 5. If still no entity, try resolver
            if not intent.entity_type or not intent.entity_value:
                entity_type, entity_value = self.entity_resolver.resolve(query)
                if entity_type:
                    intent.entity_type = entity_type
                    intent.entity_value = entity_value

            # 6. If intent is advice, handle directly (no SQL)
            if intent.intent == "advice":
                response = self._generate_advice(query)
                return response + self.FOOTER

            # 7. Build SQL using templates
            sql, params = self.sql_builder.build(intent)
            logger.info(f"SQL: {sql}")

            # 8. Execute query (source of truth)
            results = self._execute_sql(sql, params)
            logger.info(f"Found {len(results)} results")

            # 9. Apply business rules and analytics
            if results:
                results = self.business_rules.enrich(intent, results)
                results = self.analytics.enrich(intent, results)

            # 10. Generate insights using Groq or rules
            insights = self.insight_engine.generate(intent, results, query)

            # 11. Final response – always via Groq (or fallback template)
            response = self._format_response(intent, results, insights, query)

            # 12. Update memory
            self.memory.update(intent)

            return response + self.FOOTER

        except Exception as e:
            logger.exception(f"Error processing query: {e}")
            return "⚠️ An error occurred. Please try again." + self.FOOTER

    def _understand_with_groq(self, query: str) -> QueryIntent:
        """Use Groq to extract intent, entities, filters, etc."""
        if GROQ_AVAILABLE and GROQ_CLIENT:
            prompt = self._build_intent_prompt(query)
            try:
                resp = GROQ_CLIENT.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=500,
                    response_format={"type": "json_object"}
                )
                data = json.loads(resp.choices[0].message.content)
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
                logger.error(f"Groq understanding failed: {e}")
                return self._rule_based_understand(query)
        return self._rule_based_understand(query)

    def _build_intent_prompt(self, query: str) -> str:
        return f"""
You are a Logistics AI assistant. Extract structured information from the user's question.

Return ONLY valid JSON with these fields:
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

Synonyms:
- "best", "top", "highest" → ranking, DESC
- "show", "display" → dashboard
- "total", "overall" → aggregate
- "compare", "vs" → comparison
- "trend", "monthly" → trend
- "list", "all" → list
- "details", "information" → details
- "how to", "improve", "tips" → advice

Question: "{query}"

Return valid JSON only.
"""

    def _rule_based_understand(self, query: str) -> QueryIntent:
        """Fallback rule‑based understanding when Groq is unavailable."""
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

        # Detect advice
        if any(word in q for word in ["how to", "tips", "suggestions", "improve", "optimize"]):
            return QueryIntent(intent="advice")

        # Detect intent from keywords
        if "top" in q or "best" in q or "highest" in q:
            intent = "ranking"
            sort_order = "DESC"
        elif "compare" in q or "vs" in q:
            intent = "comparison"
        elif "trend" in q or "monthly" in q:
            intent = "trend"
        elif "list" in q:
            intent = "list"
        elif "details" in q or "information" in q:
            intent = "details"
        elif "total" in q or "overall" in q:
            intent = "aggregate"
        elif "show" in q or "display" in q:
            intent = "dashboard"

        # Detect entity from synonyms
        entity_map = {
            "dealer": ["dealer", "customer", "party"],
            "warehouse": ["warehouse", "godown"],
            "city": ["city", "town"],
            "division": ["division", "product line", "category"],
            "model": ["model", "product"],
            "sales_office": ["sales office", "office"],
            "sales_manager": ["sales manager", "manager"],
            "dn": ["dn", "delivery note"],
        }
        for ent, synonyms in entity_map.items():
            if any(syn in q for syn in synonyms):
                entity_type = ent
                # Try to extract value after "for", "of", "in"
                m = re.search(r"(?:for|of|in)\s+([\w\s\-]+)", q)
                if m:
                    entity_value = m.group(1).strip()
                break

        # Detect metric
        metric_map = {
            "revenue": ["revenue", "sales", "amount"],
            "units": ["units", "quantity"],
            "dns": ["dns", "delivery notes"],
            "pending": ["pending", "open"],
            "delivery_days": ["delivery days", "transit"],
            "pgi_percent": ["pgi", "pgi%"],
            "pod_percent": ["pod", "pod%"],
        }
        for met, synonyms in metric_map.items():
            if any(syn in q for syn in synonyms):
                metric = met
                break
        if not metric and intent in ["ranking", "aggregate"]:
            metric = "revenue"

        # Time period
        if "today" in q:
            time_period = "today"
        elif "this week" in q:
            time_period = "this_week"
        elif "this month" in q:
            time_period = "this_month"
        elif "last month" in q:
            time_period = "last_month"
        elif "year to date" in q or "ytd" in q:
            time_period = "year_to_date"

        # Limit
        m = re.search(r"top\s*(\d+)", q)
        if m:
            limit = int(m.group(1))

        # Filters
        m = re.search(r"in\s+([\w\s\-]+)", q)
        if m:
            filters["city"] = m.group(1).strip()
        m = re.search(r"for\s+([\w\s\-]+)", q)
        if m and not entity_value:
            filters["dealer"] = m.group(1).strip()

        return QueryIntent(
            intent=intent,
            entity_type=entity_type,
            entity_value=entity_value,
            metric=metric,
            filters=filters,
            time_period=time_period,
            grouping=grouping,
            sort_by=metric if metric else None,
            sort_order=sort_order,
            limit=limit,
            comparison_entities=comparison_entities,
        )

    def _generate_advice(self, query: str) -> str:
        """Use Groq to answer advice questions without SQL."""
        if GROQ_AVAILABLE and GROQ_CLIENT:
            prompt = f"""
The user asked: "{query}"

They are asking for advice on logistics improvement. Based on best practices in supply chain management, provide a helpful, actionable response with bullet points. Keep it concise (max 200 words) and friendly for WhatsApp.

Response:
"""
            try:
                resp = GROQ_CLIENT.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.4,
                    max_tokens=300,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"Advice generation failed: {e}")
        return "Here are some tips to improve delivery: 1. Increase vehicle capacity. 2. Reduce warehouse waiting time. 3. Improve route planning. 4. Automate customer notifications."

    def _execute_sql(self, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
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

    def _format_response(self, intent: QueryIntent, results: List[Dict], insights: str, query: str) -> str:
        """Use Groq to generate the final WhatsApp response from data and insights."""
        if not results:
            # No data – ask Groq to generate a polite "no data" response
            return self._generate_no_data_response(query, intent)

        # Build a concise data summary for Groq
        data_summary = self._build_data_summary(intent, results)

        if GROQ_AVAILABLE and GROQ_CLIENT:
            prompt = f"""
You are a Logistics AI assistant. Format the following data and insights into a clear WhatsApp response.

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
                    model="llama3-70b-8192",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=400,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"Response formatting failed: {e}")
                # Fallback to template
                return self._fallback_format(intent, results, insights)
        else:
            return self._fallback_format(intent, results, insights)

    def _build_data_summary(self, intent: QueryIntent, results: List[Dict]) -> str:
        if intent.intent in ["dashboard", "summary", "aggregate"]:
            row = results[0]
            return ", ".join([f"{k}: {v}" for k, v in row.items()])
        elif intent.intent == "ranking":
            lines = []
            for i, row in enumerate(results[:10], 1):
                name = row.get("entity_name", "Unknown")
                val = row.get("metric_value", 0)
                lines.append(f"{i}. {name}: {val}")
            return "\n".join(lines)
        elif intent.intent == "details":
            lines = []
            for row in results[:5]:
                row_parts = [f"{k}: {v}" for k, v in row.items()]
                lines.append(" | ".join(row_parts))
            return "\n".join(lines)
        else:
            return str(results)

    def _generate_no_data_response(self, query: str, intent: QueryIntent) -> str:
        if GROQ_AVAILABLE and GROQ_CLIENT:
            prompt = f"""
The user asked: "{query}"

No data was found for this query. Write a friendly, helpful response explaining that no matching records were found, and suggest they try a different question or check the spelling of names.

Keep it concise (max 100 words).
"""
            try:
                resp = GROQ_CLIENT.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=150,
                )
                return resp.choices[0].message.content.strip()
            except Exception:
                pass
        return "No data found for your query. Please try a different question."

    def _fallback_format(self, intent: QueryIntent, results: List[Dict], insights: str) -> str:
        """Simple template fallback when Groq formatting fails."""
        lines = []
        if not results:
            return "No data found."

        if intent.intent in ["dashboard", "summary", "aggregate"]:
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
        elif intent.intent == "ranking":
            entity_label = intent.entity_type or "Division"
            metric_label = intent.metric or "Revenue"
            lines.append(f"🏆 TOP {len(results)} {entity_label.upper()} BY {metric_label.upper()}")
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

# ============================================================
# SQL BUILDER (Templates)
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
        else:  # summary
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
# BUSINESS RULES ENGINE
# ============================================================

class BusinessRulesEngine:
    @staticmethod
    def enrich(intent: QueryIntent, results: List[Dict]) -> List[Dict]:
        """Add calculated fields (ratings, risk, etc.) to results."""
        if not results:
            return results

        if intent.intent == "dashboard" and len(results) == 1:
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
            # Target status for delivery days
            if delivery_days <= 3:
                target_status = "On Target"
            elif delivery_days <= 5:
                target_status = "Marginally Off Target"
            else:
                target_status = "Off Target"
            row["delivery_target_status"] = target_status
            results[0] = row

        elif intent.intent == "ranking":
            # Add rank position
            for idx, row in enumerate(results, 1):
                row["rank"] = idx

        return results

# ============================================================
# ANALYTICS ENGINE
# ============================================================

class AnalyticsEngine:
    @staticmethod
    def enrich(intent: QueryIntent, results: List[Dict]) -> List[Dict]:
        """Calculate growth, variance, etc. (requires previous period data)."""
        # For simplicity, this is a stub; in production you would query previous period and compute.
        # We'll just add a mock growth percentage.
        if results and intent.intent == "dashboard":
            row = results[0]
            # Mock growth (in production, query last month's data)
            row["revenue_growth"] = round(((row.get("revenue", 0) * 0.12)), 0)
            row["units_growth"] = round(((row.get("units", 0) * 0.08)), 0)
            results[0] = row
        return results

# ============================================================
# INSIGHT ENGINE
# ============================================================

class InsightEngine:
    def generate(self, intent: QueryIntent, results: List[Dict], query: str) -> str:
        """Generate business insights using Groq or rules."""
        if not results:
            return "No data available for insights."

        # Build a compact summary
        data_summary = self._build_summary(intent, results)

        if GROQ_AVAILABLE and GROQ_CLIENT:
            prompt = f"""
You are a Logistics AI analyst. Based on the following data, generate a short business insight (1-2 sentences) that highlights the key observation, trend, or risk.

Data:
{data_summary}

Insight:
"""
            try:
                resp = GROQ_CLIENT.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=100,
                )
                return resp.choices[0].message.content.strip()
            except Exception:
                pass

        # Fallback rule-based insights
        return self._rule_insight(intent, results)

    def _build_summary(self, intent: QueryIntent, results: List[Dict]) -> str:
        if intent.intent in ["dashboard", "summary", "aggregate"]:
            return ", ".join([f"{k}: {v}" for k, v in results[0].items()])
        elif intent.intent == "ranking":
            top = results[:5]
            return ", ".join([f"{r.get('entity_name')}: {r.get('metric_value')}" for r in top])
        else:
            return str(results)

    def _rule_insight(self, intent: QueryIntent, results: List[Dict]) -> str:
        if not results:
            return "No data to analyze."

        if intent.intent == "dashboard":
            row = results[0]
            revenue = row.get("revenue", 0)
            rating = row.get("rating", "N/A")
            risk = row.get("risk_level", "Unknown")
            if revenue == 0:
                return "No revenue recorded for this period."
            return f"Revenue is {_format_currency(revenue)}. Rating: {rating}. Risk: {risk}."
        elif intent.intent == "ranking":
            if len(results) > 0:
                top = results[0]
                return f"Top performer: {top.get('entity_name')} with {top.get('metric_value')}."
            return "Ranking data available."
        else:
            return "Performance appears stable."

# ============================================================
# WHATSAPP FORMATTER (now integrated into Orchestrator)
# ============================================================

# The WhatsApp formatting is handled inside GroqOrchestrator._format_response.
# The fallback is in _fallback_format.

# ============================================================
# MAIN SERVICE (Backward-compatible entry point)
# ============================================================

class GroqService:
    """Main service class (backward-compatible)."""
    def __init__(self):
        self.orchestrator = GroqOrchestrator()

    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        return self.orchestrator.process(message, sender)

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
