#!/usr/bin/env python3
# ============================================================
# FILE: app/services/groq_service.py
# VERSION: 6.0 - AI-POWERED LOGISTICS ASSISTANT
# PURPOSE: Answer any logistics question using Groq LLM + SQL.
#          Handles 300+ question types via natural language.
#          Integrates with gateway (process_whatsapp_query).
# ============================================================

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional, Dict, List, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)

VERSION = "6.0"

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

# ============================================================
# DATABASE REPOSITORY
# ============================================================

class LogisticsRepository:
    def __init__(self, session: Session):
        self.session = session

    # ----- BASIC AGGREGATES -----
    def get_total_revenue(self) -> float:
        try:
            with engine.connect() as conn:
                return _number(conn.execute(text("SELECT COALESCE(SUM(dn_amount), 0) FROM delivery_reports")).scalar())
        except Exception as e:
            logger.error(f"get_total_revenue error: {e}")
            return 0.0

    def get_total_dns(self) -> int:
        try:
            with engine.connect() as conn:
                return _int(conn.execute(text("SELECT COUNT(DISTINCT dn_no) FROM delivery_reports")).scalar())
        except Exception as e:
            logger.error(f"get_total_dns error: {e}")
            return 0

    def get_total_units(self) -> int:
        try:
            with engine.connect() as conn:
                return _int(conn.execute(text("SELECT COALESCE(SUM(dn_qty), 0) FROM delivery_reports")).scalar())
        except Exception as e:
            logger.error(f"get_total_units error: {e}")
            return 0

    def get_pending_dns(self) -> int:
        try:
            with engine.connect() as conn:
                return _int(conn.execute(text("SELECT COUNT(DISTINCT dn_no) FROM delivery_reports WHERE pending_flag = true")).scalar())
        except Exception as e:
            logger.error(f"get_pending_dns error: {e}")
            return 0

    # ----- BY DIMENSION -----
    def get_revenue_by_division(self, division: str) -> float:
        try:
            with engine.connect() as conn:
                return _number(conn.execute(
                    text("SELECT COALESCE(SUM(dn_amount), 0) FROM delivery_reports WHERE LOWER(division) = LOWER(:div)"),
                    {"div": division}
                ).scalar())
        except Exception as e:
            logger.error(f"get_revenue_by_division error: {e}")
            return 0.0

    def get_units_by_division(self, division: str) -> int:
        try:
            with engine.connect() as conn:
                return _int(conn.execute(
                    text("SELECT COALESCE(SUM(dn_qty), 0) FROM delivery_reports WHERE LOWER(division) = LOWER(:div)"),
                    {"div": division}
                ).scalar())
        except Exception as e:
            logger.error(f"get_units_by_division error: {e}")
            return 0

    def get_pending_by_division(self, division: str) -> int:
        try:
            with engine.connect() as conn:
                return _int(conn.execute(
                    text("SELECT COUNT(DISTINCT dn_no) FROM delivery_reports WHERE LOWER(division) = LOWER(:div) AND pending_flag = true"),
                    {"div": division}
                ).scalar())
        except Exception as e:
            logger.error(f"get_pending_by_division error: {e}")
            return 0

    def get_revenue_by_model(self, model: str) -> float:
        try:
            with engine.connect() as conn:
                return _number(conn.execute(
                    text("SELECT COALESCE(SUM(dn_amount), 0) FROM delivery_reports WHERE LOWER(customer_model) = LOWER(:model)"),
                    {"model": model}
                ).scalar())
        except Exception as e:
            logger.error(f"get_revenue_by_model error: {e}")
            return 0.0

    def get_units_by_model(self, model: str) -> int:
        try:
            with engine.connect() as conn:
                return _int(conn.execute(
                    text("SELECT COALESCE(SUM(dn_qty), 0) FROM delivery_reports WHERE LOWER(customer_model) = LOWER(:model)"),
                    {"model": model}
                ).scalar())
        except Exception as e:
            logger.error(f"get_units_by_model error: {e}")
            return 0

    def get_revenue_by_city(self, city: str) -> float:
        try:
            with engine.connect() as conn:
                return _number(conn.execute(
                    text("SELECT COALESCE(SUM(dn_amount), 0) FROM delivery_reports WHERE LOWER(ship_to_city) = LOWER(:city)"),
                    {"city": city}
                ).scalar())
        except Exception as e:
            logger.error(f"get_revenue_by_city error: {e}")
            return 0.0

    def get_revenue_by_dealer(self, dealer: str) -> float:
        try:
            with engine.connect() as conn:
                return _number(conn.execute(
                    text("SELECT COALESCE(SUM(dn_amount), 0) FROM delivery_reports WHERE LOWER(customer_name) = LOWER(:dealer)"),
                    {"dealer": dealer}
                ).scalar())
        except Exception as e:
            logger.error(f"get_revenue_by_dealer error: {e}")
            return 0.0

    def get_revenue_by_warehouse(self, warehouse: str) -> float:
        try:
            with engine.connect() as conn:
                return _number(conn.execute(
                    text("SELECT COALESCE(SUM(dn_amount), 0) FROM delivery_reports WHERE LOWER(warehouse) = LOWER(:wh)"),
                    {"wh": warehouse}
                ).scalar())
        except Exception as e:
            logger.error(f"get_revenue_by_warehouse error: {e}")
            return 0.0

    # ----- RANKINGS -----
    def get_top_divisions(self, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT division, COALESCE(SUM(dn_amount), 0) AS revenue
                        FROM delivery_reports
                        WHERE division IS NOT NULL AND division != ''
                        GROUP BY division
                        ORDER BY revenue DESC
                        LIMIT :limit
                    """),
                    {"limit": limit}
                ).fetchall()
                return [{"name": r[0], "revenue": _number(r[1])} for r in rows if r[0]]
        except Exception as e:
            logger.error(f"get_top_divisions error: {e}")
            return []

    def get_top_models(self, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT customer_model, COALESCE(SUM(dn_amount), 0) AS revenue
                        FROM delivery_reports
                        WHERE customer_model IS NOT NULL AND customer_model != ''
                        GROUP BY customer_model
                        ORDER BY revenue DESC
                        LIMIT :limit
                    """),
                    {"limit": limit}
                ).fetchall()
                return [{"name": r[0], "revenue": _number(r[1])} for r in rows if r[0]]
        except Exception as e:
            logger.error(f"get_top_models error: {e}")
            return []

    def get_top_dealers(self, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT customer_name, COALESCE(SUM(dn_amount), 0) AS revenue
                        FROM delivery_reports
                        WHERE customer_name IS NOT NULL AND customer_name != ''
                        GROUP BY customer_name
                        ORDER BY revenue DESC
                        LIMIT :limit
                    """),
                    {"limit": limit}
                ).fetchall()
                return [{"name": r[0], "revenue": _number(r[1])} for r in rows if r[0]]
        except Exception as e:
            logger.error(f"get_top_dealers error: {e}")
            return []

    def get_top_cities(self, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT ship_to_city, COALESCE(SUM(dn_amount), 0) AS revenue
                        FROM delivery_reports
                        WHERE ship_to_city IS NOT NULL AND ship_to_city != ''
                        GROUP BY ship_to_city
                        ORDER BY revenue DESC
                        LIMIT :limit
                    """),
                    {"limit": limit}
                ).fetchall()
                return [{"name": r[0], "revenue": _number(r[1])} for r in rows if r[0]]
        except Exception as e:
            logger.error(f"get_top_cities error: {e}")
            return []

    def get_top_warehouses(self, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT warehouse, COALESCE(SUM(dn_amount), 0) AS revenue
                        FROM delivery_reports
                        WHERE warehouse IS NOT NULL AND warehouse != ''
                        GROUP BY warehouse
                        ORDER BY revenue DESC
                        LIMIT :limit
                    """),
                    {"limit": limit}
                ).fetchall()
                return [{"name": r[0], "revenue": _number(r[1])} for r in rows if r[0]]
        except Exception as e:
            logger.error(f"get_top_warehouses error: {e}")
            return []

    # ----- TRENDS -----
    def get_monthly_revenue(self, months: int = 3) -> List[Dict[str, Any]]:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT TO_CHAR(dn_create_date, 'YYYY-MM') AS month,
                               COALESCE(SUM(dn_amount), 0) AS revenue
                        FROM delivery_reports
                        WHERE dn_create_date >= CURRENT_DATE - INTERVAL :months * INTERVAL '1 month'
                        GROUP BY TO_CHAR(dn_create_date, 'YYYY-MM')
                        ORDER BY month
                    """),
                    {"months": months}
                ).fetchall()
                return [{"month": r[0], "revenue": _number(r[1])} for r in rows]
        except Exception as e:
            logger.error(f"get_monthly_revenue error: {e}")
            return []

# ============================================================
# INTENT & ENTITY EXTRACTION (Pattern + Groq)
# ============================================================

class QueryParser:
    def __init__(self):
        # Common entity patterns
        self.entity_patterns = {
            "division": re.compile(r"(?:division|category|type)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
            "model": re.compile(r"(?:model|product|sku)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
            "city": re.compile(r"(?:city|location|in)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
            "dealer": re.compile(r"(?:dealer|customer|partner)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
            "warehouse": re.compile(r"(?:warehouse|wh)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
        }
        # Intent patterns (fallback)
        self.intent_patterns = {
            "total_revenue": re.compile(r"(?:total|overall)?\s*revenue", re.I),
            "pending_dns": re.compile(r"pending\s*(?:dn|delivery|order)", re.I),
            "top_divisions": re.compile(r"(?:top|best|highest)\s*division", re.I),
            "top_models": re.compile(r"(?:top|best|highest)\s*(?:model|product)", re.I),
            "top_dealers": re.compile(r"(?:top|best|highest)\s*(?:dealer|customer)", re.I),
            "top_cities": re.compile(r"(?:top|best|highest)\s*cit(?:y|ies)", re.I),
            "top_warehouses": re.compile(r"(?:top|best|highest)\s*warehouse", re.I),
            "revenue_by_entity": re.compile(r"revenue\s*(?:for|of|by)\s*['\"]?([\w\s\-]+)['\"]?", re.I),
            "units_by_entity": re.compile(r"units?\s*(?:for|of|by)\s*['\"]?([\w\s\-]+)['\"]?", re.I),
            "comparison": re.compile(r"compare\s+(?:the\s+)?([\w\s\-]+?)\s+and\s+([\w\s\-]+)", re.I),
            "trend": re.compile(r"(?:trend|monthly|recent)\s*(?:revenue|sales)", re.I),
            "greeting": re.compile(r"^(hi|hello|hey|start|menu|help)$", re.I),
        }

    def parse(self, query: str) -> Dict[str, Any]:
        """Extract intent and entities using pattern matching (fallback)."""
        result = {
            "intent": "unknown",
            "entities": {},
            "raw_query": query,
        }

        # Extract entities
        for key, pattern in self.entity_patterns.items():
            match = pattern.search(query)
            if match:
                result["entities"][key] = match.group(1).strip()

        # Detect intent
        for intent, pattern in self.intent_patterns.items():
            if pattern.search(query):
                result["intent"] = intent
                # For revenue_by_entity and units_by_entity, entities might be already captured
                break

        # Additional logic: if "revenue" and an entity is present, set intent to revenue_by_entity
        if "revenue" in query.lower() and result["entities"]:
            if "division" in result["entities"]:
                result["intent"] = "revenue_by_division"
            elif "model" in result["entities"]:
                result["intent"] = "revenue_by_model"
            elif "city" in result["entities"]:
                result["intent"] = "revenue_by_city"
            elif "dealer" in result["entities"]:
                result["intent"] = "revenue_by_dealer"
            elif "warehouse" in result["entities"]:
                result["intent"] = "revenue_by_warehouse"

        if "units" in query.lower() and result["entities"]:
            if "division" in result["entities"]:
                result["intent"] = "units_by_division"
            elif "model" in result["entities"]:
                result["intent"] = "units_by_model"

        return result

    def parse_with_groq(self, query: str) -> Dict[str, Any]:
        """Use Groq to understand intent and extract entities (more robust)."""
        if not GROQ_AVAILABLE or not GROQ_CLIENT:
            return self.parse(query)

        try:
            prompt = f"""
You are a logistics data assistant. Analyze the user's question and extract the following information:
- intent: one of ['total_revenue', 'pending_dns', 'top_divisions', 'top_models', 'top_dealers', 'top_cities', 'top_warehouses', 'revenue_by_division', 'revenue_by_model', 'revenue_by_city', 'revenue_by_dealer', 'revenue_by_warehouse', 'units_by_division', 'units_by_model', 'comparison', 'trend', 'greeting', 'unknown']
- entities: dict with keys like division, model, city, dealer, warehouse, product1, product2
- If comparison, extract product1 and product2.
Return only valid JSON.

Question: "{query}"
"""
            response = GROQ_CLIENT.chat.completions.create(
                model="llama3-70b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"}
            )
            import json
            result = json.loads(response.choices[0].message.content)
            result["raw_query"] = query
            return result
        except Exception as e:
            logger.error(f"Groq parse error: {e}")
            return self.parse(query)

# ============================================================
# RESPONSE GENERATOR (Pattern + Groq)
# ============================================================

class ResponseGenerator:
    def __init__(self):
        self.repo = LogisticsRepository(SessionLocal())

    def generate(self, parsed: Dict[str, Any]) -> str:
        intent = parsed.get("intent", "unknown")
        entities = parsed.get("entities", {})
        query = parsed.get("raw_query", "")

        # Handle greeting
        if intent == "greeting":
            return self._get_welcome()

        # Handle specific intents
        if intent == "total_revenue":
            return self._total_revenue()
        elif intent == "pending_dns":
            return self._pending_dns()
        elif intent == "top_divisions":
            return self._top_divisions()
        elif intent == "top_models":
            return self._top_models()
        elif intent == "top_dealers":
            return self._top_dealers()
        elif intent == "top_cities":
            return self._top_cities()
        elif intent == "top_warehouses":
            return self._top_warehouses()
        elif intent == "revenue_by_division":
            div = entities.get("division") or self._extract_from_query(query, "division")
            return self._revenue_by_division(div)
        elif intent == "revenue_by_model":
            model = entities.get("model") or self._extract_from_query(query, "model")
            return self._revenue_by_model(model)
        elif intent == "revenue_by_city":
            city = entities.get("city") or self._extract_from_query(query, "city")
            return self._revenue_by_city(city)
        elif intent == "revenue_by_dealer":
            dealer = entities.get("dealer") or self._extract_from_query(query, "dealer")
            return self._revenue_by_dealer(dealer)
        elif intent == "revenue_by_warehouse":
            wh = entities.get("warehouse") or self._extract_from_query(query, "warehouse")
            return self._revenue_by_warehouse(wh)
        elif intent == "units_by_division":
            div = entities.get("division") or self._extract_from_query(query, "division")
            return self._units_by_division(div)
        elif intent == "units_by_model":
            model = entities.get("model") or self._extract_from_query(query, "model")
            return self._units_by_model(model)
        elif intent == "comparison":
            p1 = entities.get("product1") or self._extract_from_query(query, "product1")
            p2 = entities.get("product2") or self._extract_from_query(query, "product2")
            return self._comparison(p1, p2)
        elif intent == "trend":
            return self._trend()
        else:
            # Fallback: try to use Groq to generate a generic answer
            if GROQ_AVAILABLE and GROQ_CLIENT:
                return self._ask_groq(query)
            return self._fallback_response(query)

    # ---- Helper to extract entity from query if not found ----
    def _extract_from_query(self, query: str, entity_type: str) -> Optional[str]:
        # Simple heuristic: look for quoted text or common patterns
        # If entity_type is 'division', look for "division is ..." etc.
        patterns = {
            "division": re.compile(r"(?:division|category|type)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
            "model": re.compile(r"(?:model|product|sku)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
            "city": re.compile(r"(?:city|location|in)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
            "dealer": re.compile(r"(?:dealer|customer|partner)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
            "warehouse": re.compile(r"(?:warehouse|wh)\s*(?:is\s*)?['\"]?([\w\s\-]+)['\"]?", re.I),
            "product1": re.compile(r"compare\s+(?:the\s+)?([\w\s\-]+?)\s+and", re.I),
            "product2": re.compile(r"and\s+([\w\s\-]+)(?:\?|$)", re.I),
        }
        pattern = patterns.get(entity_type)
        if pattern:
            match = pattern.search(query)
            if match:
                return match.group(1).strip()
        return None

    # ---- Specific response methods ----
    def _total_revenue(self) -> str:
        revenue = self.repo.get_total_revenue()
        pending = self.repo.get_pending_dns()
        units = self.repo.get_total_units()
        return "\n".join([
            "📊 *Logistics Overview*",
            "",
            f"💰 Total Revenue: {_format_currency(revenue)}",
            f"📦 Total Units: {_format_number(units)}",
            f"🚚 Total DNs: {_format_number(self.repo.get_total_dns())}",
            f"⏳ Pending DNs: {_format_number(pending)}",
        ])

    def _pending_dns(self) -> str:
        pending = self.repo.get_pending_dns()
        total = self.repo.get_total_dns()
        return f"⏳ Pending DNs: {_format_number(pending)} out of {_format_number(total)} ({round(pending/total*100 if total else 0,1)}%)"

    def _top_divisions(self) -> str:
        items = self.repo.get_top_divisions(5)
        if not items:
            return "No divisions found."
        lines = ["🏆 *Top Divisions by Revenue*", ""]
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item['name']}: {_format_currency(item['revenue'])}")
        return "\n".join(lines)

    def _top_models(self) -> str:
        items = self.repo.get_top_models(5)
        if not items:
            return "No models found."
        lines = ["🏆 *Top Models by Revenue*", ""]
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item['name']}: {_format_currency(item['revenue'])}")
        return "\n".join(lines)

    def _top_dealers(self) -> str:
        items = self.repo.get_top_dealers(5)
        if not items:
            return "No dealers found."
        lines = ["🏆 *Top Dealers by Revenue*", ""]
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item['name']}: {_format_currency(item['revenue'])}")
        return "\n".join(lines)

    def _top_cities(self) -> str:
        items = self.repo.get_top_cities(5)
        if not items:
            return "No cities found."
        lines = ["🏆 *Top Cities by Revenue*", ""]
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item['name']}: {_format_currency(item['revenue'])}")
        return "\n".join(lines)

    def _top_warehouses(self) -> str:
        items = self.repo.get_top_warehouses(5)
        if not items:
            return "No warehouses found."
        lines = ["🏆 *Top Warehouses by Revenue*", ""]
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item['name']}: {_format_currency(item['revenue'])}")
        return "\n".join(lines)

    def _revenue_by_division(self, division: str) -> str:
        if not division:
            return "Please specify a division (e.g., 'Washing Machine')."
        rev = self.repo.get_revenue_by_division(division)
        units = self.repo.get_units_by_division(division)
        pending = self.repo.get_pending_by_division(division)
        return f"📊 *Division: {division}*\n\n💰 Revenue: {_format_currency(rev)}\n📦 Units: {_format_number(units)}\n⏳ Pending DNs: {_format_number(pending)}"

    def _revenue_by_model(self, model: str) -> str:
        if not model:
            return "Please specify a product model (e.g., 'HWM120-AS MG')."
        rev = self.repo.get_revenue_by_model(model)
        units = self.repo.get_units_by_model(model)
        return f"📊 *Model: {model}*\n\n💰 Revenue: {_format_currency(rev)}\n📦 Units: {_format_number(units)}"

    def _revenue_by_city(self, city: str) -> str:
        if not city:
            return "Please specify a city."
        rev = self.repo.get_revenue_by_city(city)
        return f"📊 *City: {city}*\n\n💰 Revenue: {_format_currency(rev)}"

    def _revenue_by_dealer(self, dealer: str) -> str:
        if not dealer:
            return "Please specify a dealer name."
        rev = self.repo.get_revenue_by_dealer(dealer)
        return f"📊 *Dealer: {dealer}*\n\n💰 Revenue: {_format_currency(rev)}"

    def _revenue_by_warehouse(self, warehouse: str) -> str:
        if not warehouse:
            return "Please specify a warehouse."
        rev = self.repo.get_revenue_by_warehouse(warehouse)
        return f"📊 *Warehouse: {warehouse}*\n\n💰 Revenue: {_format_currency(rev)}"

    def _units_by_division(self, division: str) -> str:
        if not division:
            return "Please specify a division."
        units = self.repo.get_units_by_division(division)
        return f"📦 *Units in {division}:* {_format_number(units)}"

    def _units_by_model(self, model: str) -> str:
        if not model:
            return "Please specify a model."
        units = self.repo.get_units_by_model(model)
        return f"📦 *Units for {model}:* {_format_number(units)}"

    def _comparison(self, p1: str, p2: str) -> str:
        if not p1 or not p2:
            return "Please specify two products to compare (e.g., 'compare A and B')."
        rev1 = self.repo.get_revenue_by_model(p1) or self.repo.get_revenue_by_division(p1)
        rev2 = self.repo.get_revenue_by_model(p2) or self.repo.get_revenue_by_division(p2)
        units1 = self.repo.get_units_by_model(p1) or self.repo.get_units_by_division(p1)
        units2 = self.repo.get_units_by_model(p2) or self.repo.get_units_by_division(p2)
        return f"📊 *Comparison: {p1} vs {p2}*\n\n{p1}: Revenue {_format_currency(rev1)}, Units {_format_number(units1)}\n{p2}: Revenue {_format_currency(rev2)}, Units {_format_number(units2)}\n\nDifference: {_format_currency(rev1 - rev2)} revenue, {units1 - units2} units."

    def _trend(self) -> str:
        data = self.repo.get_monthly_revenue(6)
        if not data:
            return "No trend data available."
        lines = ["📈 *Monthly Revenue Trend (last 6 months)*", ""]
        for item in data:
            lines.append(f"{item['month']}: {_format_currency(item['revenue'])}")
        return "\n".join(lines)

    def _get_welcome(self) -> str:
        return "\n".join([
            "🤖 *AI Logistics Assistant*",
            "",
            "I can answer questions about:",
            "• Total revenue, pending DNs, top performers",
            "• Revenue/units by division, model, city, dealer, warehouse",
            "• Comparisons and trends",
            "",
            "Examples:",
            "• What is the total revenue?",
            "• Show pending DNs",
            "• Top 5 divisions by revenue",
            "• Revenue for Washing Machine",
            "• Compare HWM120 and HWM150",
            "• Monthly trend",
            "",
            "Reply *99* to return to menu."
        ])

    def _ask_groq(self, query: str) -> str:
        """Use Groq to generate a response based on available data."""
        try:
            # First, fetch some context (e.g., summary stats)
            total_rev = self.repo.get_total_revenue()
            total_units = self.repo.get_total_units()
            pending = self.repo.get_pending_dns()
            top_divs = self.repo.get_top_divisions(3)
            top_divs_str = ", ".join([f"{d['name']} ({_format_currency(d['revenue'])})" for d in top_divs])
            context = f"""
Total Revenue: {_format_currency(total_rev)}
Total Units: {_format_number(total_units)}
Pending DNs: {_format_number(pending)}
Top Divisions: {top_divs_str}
"""
            prompt = f"""
You are a logistics data assistant. Answer the user's question based on the context below. If the question is not directly answerable, suggest what data might help.
Context:
{context}

User question: {query}

Provide a helpful, concise response (max 150 words).
"""
            response = GROQ_CLIENT.chat.completions.create(
                model="llama3-70b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq generation error: {e}")
            return self._fallback_response(query)

    def _fallback_response(self, query: str) -> str:
        return f"🤔 I didn't understand that question.\n\nYou can ask about revenue, pending DNs, top performers, comparisons, or trends. Type 'help' for examples."

# ============================================================
# MAIN SERVICE
# ============================================================

class GroqService:
    def __init__(self) -> None:
        self._version = VERSION
        self.parser = QueryParser()
        self.generator = ResponseGenerator()
        logger.info(f"✅ GroqService v{self._version} initialized")
        logger.info(f"   Groq: {'✅' if GROQ_AVAILABLE else '❌'}")

    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """Main entry point – called by gateway."""
        try:
            msg = message.strip()
            if not msg:
                return self._get_welcome()

            if msg == "99":
                logger.info("[GroqService] Exit signal")
                return "99"

            # If it's a simple greeting or menu request
            if msg.lower() in ["hi", "hello", "hey", "start", "menu", "help"]:
                return self._get_welcome()

            logger.info(f"[GroqService] Processing: '{msg}' from {sender}")

            # Parse the query (try Groq first, fallback to pattern)
            if GROQ_AVAILABLE and GROQ_CLIENT:
                parsed = self.parser.parse_with_groq(msg)
            else:
                parsed = self.parser.parse(msg)

            # Generate response
            response = self.generator.generate(parsed)

            return response

        except Exception as e:
            logger.exception(f"[GroqService] Error: {e}")
            return "⚠️ An error occurred. Please try again."

    def _get_welcome(self) -> str:
        return self.generator._get_welcome()

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
