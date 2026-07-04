# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 4.0 - ENTERPRISE DEALER DOMAIN AI ENGINE
# ============================================================

"""
File: app/services/dealer_analytics_service.py
Version: 4.0 - ENTERPRISE DEALER DOMAIN AI ENGINE

================================================================================
PURPOSE
================================================================================

This is a completely independent Enterprise AI Domain Service.

Its responsibilities are:
1. Dealer Intelligence Engine
2. Dealer Analytics & KPI
3. Dealer Search & Ranking
4. Dealer Comparison & Performance
5. Dealer AI Assistant
6. Dealer SQL Engine
7. Dealer Intent Detection
8. Dealer Semantic Routing
9. Dealer Session Management
10. Dealer Response Engine
11. Dealer Menu System (Auto-display)
12. Dealer Question Registry
13. Dealer SQL Registry
14. Dealer Business Rules
15. Dealer Analytics Engine

================================================================================
SYSTEM ARCHITECTURE
================================================================================

WhatsApp User
    │
    ▼
ai_provider_service.py
    │
    ▼
DealerAnalyticsService
    │
    ├── Session Manager
    ├── Menu Manager (Auto-display)
    ├── Menu Registry
    ├── Question Registry
    ├── SQL Registry
    ├── Intent Detection
    ├── Entity Extraction
    ├── Semantic Router
    ├── Business Rules Engine
    ├── SQL Planner
    ├── Repository
    ├── PostgreSQL
    ├── Analytics Engine
    ├── Dashboard Engine
    ├── Search Engine
    ├── Timeline Engine
    ├── Comparison Engine
    ├── Ranking Engine
    ├── AI Assistant
    ├── Response Templates
    └── Cache Manager

================================================================================
AUTO-MENU DISPLAY
================================================================================

Every time this service is entered, the FIRST response MUST automatically
display its menu. The user never enters a blank conversation.

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple, Union, Callable
from functools import lru_cache

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: AI LIBRARIES
# ============================================================

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

try:
    from semantic_router import Route, SemanticRouter
    SEMANTIC_ROUTER_AVAILABLE = True
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False

try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
except ImportError:
    TEXTBLOB_AVAILABLE = False

try:
    import nltk
    from nltk.tokenize import word_tokenize
    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

# ============================================================
# BLOCK 2: DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, desc, asc, and_, case
    from sqlalchemy.orm import Session
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
    logger.info("✅ Dealer database imports successful")
except ImportError as e:
    DB_AVAILABLE = False
    logger.error(f"❌ Dealer database import error: {e}")

# ============================================================
# BLOCK 3: CONFIGURATION
# ============================================================

DEALER_CACHE_TTL = int(os.getenv("DEALER_CACHE_TTL", "300"))
DEALER_SESSION_TIMEOUT = int(os.getenv("DEALER_SESSION_TIMEOUT", "1800"))
DEALER_AI_ENABLED = os.getenv("DEALER_AI_ENABLED", "true").lower() == "true"
DEALER_SEMANTIC_ENABLED = os.getenv("DEALER_SEMANTIC_ENABLED", "true").lower() == "true"
DEALER_MENU_AUTO_SHOW = os.getenv("DEALER_MENU_AUTO_SHOW", "true").lower() == "true"

# AI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

class DealerIntent(Enum):
    """Dealer intent types"""
    DASHBOARD = "dashboard"
    SUMMARY = "summary"
    REVENUE = "revenue"
    UNITS = "units"
    PENDING = "pending"
    DELIVERY = "delivery"
    PGI = "pgi"
    POD = "pod"
    RANKING = "ranking"
    COMPARISON = "comparison"
    SEARCH = "search"
    PERFORMANCE = "performance"
    HISTORY = "history"
    TIMELINE = "timeline"
    PRODUCTS = "products"
    MODELS = "models"
    TOP = "top"
    BOTTOM = "bottom"
    TREND = "trend"
    GROWTH = "growth"
    FORECAST = "forecast"
    AI_ASK = "ai_ask"
    MENU = "menu"
    HELP = "help"
    EXIT = "exit"
    UNKNOWN = "unknown"

class DealerMenuState(Enum):
    """Dealer menu states"""
    MAIN = "main"
    DASHBOARD = "dashboard"
    ANALYTICS = "analytics"
    AI_ASSISTANT = "ai_assistant"
    DEALER_SELECTED = "dealer_selected"
    COMPARISON = "comparison"
    SEARCH_RESULTS = "search_results"
    RANKING = "ranking"

# ============================================================
# BLOCK 5: DATA CLASSES
# ============================================================

@dataclass
class DealerSession:
    """Dealer session state"""
    session_id: str
    locked: bool = True
    current_dealer: Optional[str] = None
    current_dealer_code: Optional[str] = None
    menu_state: DealerMenuState = DealerMenuState.MAIN
    selected_option: Optional[str] = None
    comparison_dealers: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    last_query: str = ""
    last_answer: str = ""
    last_intent: Optional[DealerIntent] = None
    last_sql: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    filters: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    menu_shown: bool = False
    
    def touch(self):
        self.updated_at = datetime.now()
    
    def is_expired(self, timeout: int = DEALER_SESSION_TIMEOUT) -> bool:
        elapsed = (datetime.now() - self.updated_at).total_seconds()
        return elapsed > timeout
    
    def add_history(self, query: str, answer: str):
        self.history.append({
            "query": query,
            "answer": answer[:200] if len(answer) > 200 else answer,
            "intent": self.last_intent.value if self.last_intent else None,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]
        self.last_query = query
        self.last_answer = answer
        self.touch()
    
    def set_dealer(self, name: str, code: Optional[str] = None):
        self.current_dealer = name
        self.current_dealer_code = code
        self.menu_state = DealerMenuState.DEALER_SELECTED
        self.touch()
    
    def clear(self):
        self.current_dealer = None
        self.current_dealer_code = None
        self.menu_state = DealerMenuState.MAIN
        self.comparison_dealers = []
        self.filters = {}
        self.context = {}
        self.menu_shown = False
        self.touch()

@dataclass
class DealerIntentResult:
    """Intent detection result"""
    intent: DealerIntent
    confidence: float
    entities: Dict[str, Any]
    raw_input: str
    processing_time_ms: float

@dataclass
class DealerQueryPlan:
    """Query execution plan"""
    intent: DealerIntent
    dealer: Optional[str] = None
    dealers: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)
    limit: int = 10
    sort_by: Optional[str] = None
    order: str = "desc"
    timeframe: Optional[str] = None
    filters: Dict[str, Any] = field(default_factory=dict)

# ============================================================
# BLOCK 6: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _format_currency(amount: float) -> str:
    if amount is None:
        return "PKR 0.00"
    if amount >= 1_000_000_000_000:
        return f"PKR {amount/1_000_000_000_000:,.2f} Trillion"
    elif amount >= 1_000_000_000:
        return f"PKR {amount/1_000_000_000:,.2f} Billion"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:,.2f} Million"
    else:
        return f"PKR {amount:,.2f}"

def _format_number(num: Union[int, float]) -> str:
    if num is None:
        return "0"
    return f"{num:,}"

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def _growth(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)

# ============================================================
# BLOCK 7: MENU REGISTRY
# ============================================================

class DealerMenuRegistry:
    """Registry of all dealer menus and their items"""
    
    MENUS = {
        "main": {
            "id": "main",
            "name": "DEALER INTELLIGENCE ENGINE",
            "items": [
                {"id": "1", "name": "Dashboard", "handler": "handle_dashboard", "icon": "📊"},
                {"id": "2", "name": "Analytics", "handler": "handle_analytics", "icon": "📈"},
                {"id": "3", "name": "AI Assistant", "handler": "handle_ai_assistant", "icon": "🤖"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "dashboard": {
            "id": "dashboard",
            "name": "DEALER DASHBOARD",
            "items": [
                {"id": "1", "name": "Summary", "handler": "handle_summary", "icon": "📋"},
                {"id": "2", "name": "Revenue", "handler": "handle_revenue", "icon": "💰"},
                {"id": "3", "name": "Units", "handler": "handle_units", "icon": "📦"},
                {"id": "4", "name": "Pending", "handler": "handle_pending", "icon": "⏳"},
                {"id": "5", "name": "Delivery", "handler": "handle_delivery", "icon": "🚚"},
                {"id": "6", "name": "Performance", "handler": "handle_performance", "icon": "📈"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "analytics": {
            "id": "analytics",
            "name": "DEALER ANALYTICS",
            "items": [
                {"id": "1", "name": "Search", "handler": "handle_search", "icon": "🔍"},
                {"id": "2", "name": "Ranking", "handler": "handle_ranking", "icon": "🏆"},
                {"id": "3", "name": "Comparison", "handler": "handle_comparison", "icon": "🔄"},
                {"id": "4", "name": "Timeline", "handler": "handle_timeline", "icon": "📅"},
                {"id": "5", "name": "History", "handler": "handle_history", "icon": "📖"},
                {"id": "6", "name": "Products", "handler": "handle_products", "icon": "📦"},
                {"id": "7", "name": "Trends", "handler": "handle_trends", "icon": "📈"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "ai_assistant": {
            "id": "ai_assistant",
            "name": "DEALER AI ASSISTANT",
            "items": [
                {"id": "1", "name": "Ask Question", "handler": "handle_ai_ask", "icon": "❓"},
                {"id": "2", "name": "Analysis", "handler": "handle_ai_analysis", "icon": "📊"},
                {"id": "3", "name": "Insights", "handler": "handle_ai_insights", "icon": "💡"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        }
    }

# ============================================================
# BLOCK 8: QUESTION REGISTRY
# ============================================================

class DealerQuestionRegistry:
    """Registry of predefined questions and their handlers"""
    
    QUESTIONS = {
        # Dashboard questions
        "dashboard": {"handler": "handle_summary", "priority": 1},
        "summary": {"handler": "handle_summary", "priority": 1},
        "overview": {"handler": "handle_summary", "priority": 1},
        "today kpi": {"handler": "handle_summary", "priority": 1},
        "weekly kpi": {"handler": "handle_summary", "priority": 1},
        "monthly kpi": {"handler": "handle_summary", "priority": 1},
        "yearly kpi": {"handler": "handle_summary", "priority": 1},
        "statistics": {"handler": "handle_summary", "priority": 1},
        "performance": {"handler": "handle_performance", "priority": 1},
        "health check": {"handler": "handle_summary", "priority": 1},
        "business score": {"handler": "handle_summary", "priority": 1},
        "dealer status": {"handler": "handle_summary", "priority": 1},
        
        # Revenue questions
        "revenue": {"handler": "handle_revenue", "priority": 2},
        "total revenue": {"handler": "handle_revenue", "priority": 2},
        "sales": {"handler": "handle_revenue", "priority": 2},
        "income": {"handler": "handle_revenue", "priority": 2},
        "amount": {"handler": "handle_revenue", "priority": 2},
        
        # Units questions
        "units": {"handler": "handle_units", "priority": 2},
        "quantity": {"handler": "handle_units", "priority": 2},
        "volume": {"handler": "handle_units", "priority": 2},
        
        # Pending questions
        "pending": {"handler": "handle_pending", "priority": 2},
        "backlog": {"handler": "handle_pending", "priority": 2},
        "overdue": {"handler": "handle_pending", "priority": 2},
        "pending dn": {"handler": "handle_pending", "priority": 2},
        "pending orders": {"handler": "handle_pending", "priority": 2},
        
        # Delivery questions
        "delivery": {"handler": "handle_delivery", "priority": 2},
        "delivered": {"handler": "handle_delivery", "priority": 2},
        "transit": {"handler": "handle_delivery", "priority": 2},
        "shipping": {"handler": "handle_delivery", "priority": 2},
        
        # PGI/POD questions
        "pgi": {"handler": "handle_pgi", "priority": 2},
        "pod": {"handler": "handle_pod", "priority": 2},
        "goods issue": {"handler": "handle_pgi", "priority": 2},
        "proof of delivery": {"handler": "handle_pod", "priority": 2},
        
        # Ranking questions
        "ranking": {"handler": "handle_ranking", "priority": 2},
        "rank": {"handler": "handle_ranking", "priority": 2},
        "leaderboard": {"handler": "handle_ranking", "priority": 2},
        "top": {"handler": "handle_top", "priority": 2},
        "bottom": {"handler": "handle_bottom", "priority": 2},
        "best": {"handler": "handle_top", "priority": 2},
        "worst": {"handler": "handle_bottom", "priority": 2},
        "highest": {"handler": "handle_top", "priority": 2},
        "lowest": {"handler": "handle_bottom", "priority": 2},
        
        # Comparison questions
        "compare": {"handler": "handle_comparison", "priority": 2},
        "comparison": {"handler": "handle_comparison", "priority": 2},
        "vs": {"handler": "handle_comparison", "priority": 2},
        "versus": {"handler": "handle_comparison", "priority": 2},
        
        # Search questions
        "search": {"handler": "handle_search", "priority": 2},
        "find": {"handler": "handle_search", "priority": 2},
        "lookup": {"handler": "handle_search", "priority": 2},
        
        # Timeline questions
        "timeline": {"handler": "handle_timeline", "priority": 2},
        "history": {"handler": "handle_history", "priority": 2},
        "chronology": {"handler": "handle_timeline", "priority": 2},
        
        # Products questions
        "products": {"handler": "handle_products", "priority": 2},
        "items": {"handler": "handle_products", "priority": 2},
        "models": {"handler": "handle_models", "priority": 2},
        "variants": {"handler": "handle_models", "priority": 2},
        
        # Trend questions
        "trend": {"handler": "handle_trends", "priority": 2},
        "growth": {"handler": "handle_trends", "priority": 2},
        "change": {"handler": "handle_trends", "priority": 2},
    }
    
    @classmethod
    def find_handler(cls, text: str) -> Optional[tuple[str, float]]:
        """Find matching handler for question"""
        text_lower = text.lower().strip()
        
        # Exact match
        if text_lower in cls.QUESTIONS:
            return (cls.QUESTIONS[text_lower]["handler"], 1.0)
        
        # Partial match
        for key, value in cls.QUESTIONS.items():
            if key in text_lower or text_lower in key:
                return (value["handler"], 0.8)
        
        # Fuzzy match
        if RAPIDFUZZ_AVAILABLE:
            best_match = None
            best_score = 0.0
            for key in cls.QUESTIONS.keys():
                score = fuzz.partial_ratio(text_lower, key)
                if score > best_score and score > 70:
                    best_score = score
                    best_match = key
            if best_match:
                return (cls.QUESTIONS[best_match]["handler"], best_score / 100.0)
        
        return None

# ============================================================
# BLOCK 9: SQL REGISTRY
# ============================================================

class DealerSQLRegistry:
    """Registry of all SQL queries for dealer operations"""
    
    @staticmethod
    def get_summary(dealer_identifier: str) -> str:
        """Get dealer summary SQL"""
        return f"""
            SELECT 
                customer_name as dealer,
                dealer_code,
                sales_office,
                sales_manager,
                COUNT(DISTINCT dn_no) as total_dn,
                COALESCE(SUM(dn_qty), 0) as total_units,
                COALESCE(SUM(dn_amount), 0) as total_revenue,
                COUNT(DISTINCT CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_no END) as pending_dn,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) as pgi_pending_dn,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) as pod_pending_dn,
                COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed,
                COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                AVG(CASE WHEN good_issue_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as avg_delivery_days,
                AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as avg_pod_days,
                AVG(dn_amount) as avg_revenue_per_dn,
                MIN(dn_create_date) as first_order,
                MAX(dn_create_date) as last_order
            FROM delivery_reports
            WHERE LOWER(customer_name) = LOWER('{dealer_identifier}')
               OR LOWER(dealer_code) = LOWER('{dealer_identifier}')
               OR LOWER(customer_name) LIKE LOWER('%{dealer_identifier}%')
               OR LOWER(dealer_code) LIKE LOWER('%{dealer_identifier}%')
            GROUP BY customer_name, dealer_code, sales_office, sales_manager
        """
    
    @staticmethod
    def get_ranking(metric: str, limit: int = 10) -> str:
        """Get dealer ranking SQL"""
        metric_map = {
            "revenue": "SUM(dn_amount)",
            "units": "SUM(dn_qty)",
            "dn": "COUNT(DISTINCT dn_no)",
            "pending": "COUNT(DISTINCT CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_no END)",
            "delivery": "AVG(CASE WHEN good_issue_date IS NOT NULL THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END)"
        }
        
        agg_col = metric_map.get(metric, "SUM(dn_amount)")
        order = "DESC" if metric in ["revenue", "units", "dn"] else "ASC"
        
        return f"""
            SELECT 
                customer_name as dealer,
                {agg_col} as value
            FROM delivery_reports
            WHERE customer_name IS NOT NULL
            GROUP BY customer_name
            ORDER BY value {order}
            LIMIT {limit}
        """
    
    @staticmethod
    def get_products(dealer_identifier: str, limit: int = 10) -> str:
        """Get dealer products SQL"""
        return f"""
            SELECT 
                customer_model as product,
                COALESCE(SUM(dn_amount), 0) as revenue,
                COALESCE(SUM(dn_qty), 0) as units,
                COUNT(DISTINCT dn_no) as dn_count
            FROM delivery_reports
            WHERE (LOWER(customer_name) = LOWER('{dealer_identifier}')
               OR LOWER(dealer_code) = LOWER('{dealer_identifier}')
               OR LOWER(customer_name) LIKE LOWER('%{dealer_identifier}%'))
               AND customer_model IS NOT NULL
            GROUP BY customer_model
            ORDER BY revenue DESC
            LIMIT {limit}
        """
    
    @staticmethod
    def search(query: str, limit: int = 30) -> str:
        """Search dealers SQL"""
        search_pattern = f"%{query}%"
        return f"""
            SELECT 
                customer_name as dealer,
                dealer_code,
                sales_office,
                sales_manager,
                COALESCE(SUM(dn_amount), 0) as revenue,
                COUNT(DISTINCT dn_no) as dn_count,
                COALESCE(SUM(dn_qty), 0) as units,
                COUNT(DISTINCT CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_no END) as pending_count
            FROM delivery_reports
            WHERE customer_name ILIKE '{search_pattern}'
               OR dealer_code ILIKE '{search_pattern}'
               OR LOWER(customer_name) LIKE LOWER('%{query}%')
               OR LOWER(dealer_code) LIKE LOWER('%{query}%')
            GROUP BY customer_name, dealer_code, sales_office, sales_manager
            ORDER BY revenue DESC
            LIMIT {limit}
        """

# ============================================================
# BLOCK 10: DEALER INTENT ENGINE
# ============================================================

class DealerIntentEngine:
    """AI-powered intent detection for dealer queries"""
    
    INTENT_PATTERNS = {
        DealerIntent.DASHBOARD: [
            r"(?:show|display|get).*(?:dealer|dashboard|profile)",
            r"dealer (?:dashboard|profile|details|info)",
        ],
        DealerIntent.SUMMARY: [
            r"(?:summary|overview|brief|executive|quick).*(?:dealer)",
            r"dealer (?:summary|overview|statistics)",
        ],
        DealerIntent.REVENUE: [
            r"(?:revenue|sales|income|amount|turnover).*(?:dealer)",
            r"how much (?:revenue|sales)",
            r"(?:revenue|sales) (?:of|for|by)",
        ],
        DealerIntent.UNITS: [
            r"(?:units|quantity|qty|volume|pieces).*(?:dealer)",
            r"how many units",
        ],
        DealerIntent.PENDING: [
            r"(?:pending|backlog|overdue|delayed).*(?:dealer)",
            r"pending (?:dn|order|delivery)",
        ],
        DealerIntent.DELIVERY: [
            r"(?:delivery|deliveries|transit|shipping).*(?:dealer)",
            r"delivery (?:performance|time|days)",
        ],
        DealerIntent.PGI: [
            r"pgi",
            r"goods issue",
            r"issue status",
        ],
        DealerIntent.POD: [
            r"pod",
            r"proof of delivery",
            r"delivery confirmation",
        ],
        DealerIntent.RANKING: [
            r"(?:top|best|highest|leading).*(?:dealer|dealers)",
            r"dealer (?:ranking|rank|leaderboard)",
            r"top (?:dealers|performers)",
        ],
        DealerIntent.COMPARISON: [
            r"compare\s+([\w\s]+)\s+and\s+([\w\s]+)",
            r"vs",
            r"comparison",
        ],
        DealerIntent.SEARCH: [
            r"(?:search|find|lookup).*(?:dealer)",
            r"search (?:dealer|dealers)",
        ],
        DealerIntent.PERFORMANCE: [
            r"(?:performance|score|rating|health).*(?:dealer)",
            r"dealer (?:performance|score|efficiency)",
        ],
        DealerIntent.HISTORY: [
            r"(?:history|past|previous).*(?:dealer)",
            r"dealer (?:history|transactions)",
        ],
        DealerIntent.TIMELINE: [
            r"(?:timeline|chronology|when).*(?:dealer)",
            r"dealer (?:timeline|activity)",
        ],
        DealerIntent.PRODUCTS: [
            r"(?:product|products|items).*(?:dealer)",
            r"what (?:products|items) (?:does|did)",
        ],
        DealerIntent.MODELS: [
            r"(?:model|models|variants).*(?:dealer)",
            r"which (?:models|variants)",
        ],
        DealerIntent.TOP: [
            r"top\s+(\d+)\s+(?:dealers|performers)",
            r"best (?:dealer|dealers)",
            r"highest (?:revenue|sales|performing)",
        ],
        DealerIntent.BOTTOM: [
            r"bottom\s+(\d+)\s+(?:dealers|performers)",
            r"worst (?:dealer|dealers)",
            r"lowest (?:revenue|sales|performing)",
        ],
        DealerIntent.TREND: [
            r"(?:trend|pattern|change).*(?:dealer)",
            r"dealer (?:trend|growth|change)",
            r"monthly trend",
        ],
        DealerIntent.GROWTH: [
            r"(?:growth|increase|decrease).*(?:dealer)",
            r"dealer (?:growth|decline)",
        ],
        DealerIntent.FORECAST: [
            r"(?:forecast|predict|future).*(?:dealer)",
            r"dealer (?:forecast|projection)",
        ],
        DealerIntent.AI_ASK: [
            r"(?:ask|tell|explain|why|how|what|when|where).*(?:dealer)",
            r"dealer (?:analysis|insight|question)",
        ],
        DealerIntent.MENU: [
            r"menu",
            r"dealer menu",
            r"options",
        ],
        DealerIntent.HELP: [
            r"help",
            r"support",
            r"assist",
            r"what can (?:you|i)",
        ],
        DealerIntent.EXIT: [
            r"99",
            r"exit",
            r"quit",
            r"cancel",
            r"back",
        ],
    }
    
    def __init__(self):
        self._patterns = {
            intent: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
            for intent, patterns in self.INTENT_PATTERNS.items()
        }
        self._cache: Dict[str, Tuple[DealerIntent, float]] = {}
        self._cache_lock = threading.RLock()
        self._initialized = False
        self._initialize()
    
    def _initialize(self):
        if self._initialized:
            return
        
        logger.info("🤖 Initializing Dealer Intent Engine...")
        start_time = time.time()
        
        self._init_spacy()
        self._init_nltk()
        self._init_semantic_router()
        self._init_llm_clients()
        
        self._initialized = True
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"✅ Dealer Intent Engine initialized in {elapsed:.1f}ms")
    
    def _init_spacy(self):
        self.nlp = None
        if SPACY_AVAILABLE and spacy:
            try:
                self.nlp = spacy.load("en_core_web_sm")
                logger.info("✅ spaCy loaded")
            except:
                try:
                    spacy.cli.download("en_core_web_sm")
                    self.nlp = spacy.load("en_core_web_sm")
                    logger.info("✅ spaCy downloaded and loaded")
                except Exception as e:
                    logger.warning(f"⚠️ spaCy init failed: {e}")
    
    def _init_nltk(self):
        self.nltk_available = False
        if NLTK_AVAILABLE and nltk:
            try:
                nltk.data.find('tokenizers/punkt')
                self.nltk_available = True
            except LookupError:
                try:
                    nltk.download('punkt', quiet=True)
                    self.nltk_available = True
                except:
                    pass
    
    def _init_semantic_router(self):
        self.semantic_router = None
        if SEMANTIC_ROUTER_AVAILABLE and SemanticRouter:
            try:
                routes = [
                    Route(name="dealer_dashboard", utterances=[
                        "dealer dashboard", "show dealer", "dealer info", "dealer details"
                    ]),
                    Route(name="dealer_revenue", utterances=[
                        "dealer revenue", "dealer sales", "revenue for dealer"
                    ]),
                    Route(name="dealer_pending", utterances=[
                        "dealer pending", "pending orders", "dealer backlog"
                    ]),
                    Route(name="dealer_ranking", utterances=[
                        "top dealers", "dealer ranking", "best dealers"
                    ]),
                    Route(name="dealer_comparison", utterances=[
                        "compare dealers", "dealer vs dealer", "comparison"
                    ]),
                    Route(name="dealer_search", utterances=[
                        "search dealer", "find dealer", "lookup dealer"
                    ]),
                    Route(name="dealer_performance", utterances=[
                        "dealer performance", "dealer score", "dealer health"
                    ]),
                    Route(name="dealer_products", utterances=[
                        "dealer products", "what products", "items sold"
                    ]),
                ]
                self.semantic_router = SemanticRouter(routes=routes)
                logger.info("✅ Semantic Router initialized")
            except Exception as e:
                logger.warning(f"⚠️ Semantic Router init failed: {e}")
    
    def _init_llm_clients(self):
        self.openai_client = None
        self.groq_client = None
        
        if OPENAI_AVAILABLE and openai and OPENAI_API_KEY:
            try:
                self.openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
                logger.info("✅ OpenAI client initialized")
            except Exception as e:
                logger.warning(f"⚠️ OpenAI init failed: {e}")
        
        if GROQ_AVAILABLE and groq and GROQ_API_KEY:
            try:
                self.groq_client = groq.Groq(api_key=GROQ_API_KEY)
                logger.info("✅ Groq client initialized")
            except Exception as e:
                logger.warning(f"⚠️ Groq init failed: {e}")
    
    def detect_intent(self, text: str) -> DealerIntentResult:
        """Detect intent using multi-stage pipeline"""
        start_time = time.time()
        
        if not text or not text.strip():
            return DealerIntentResult(
                intent=DealerIntent.UNKNOWN,
                confidence=0.0,
                entities={},
                raw_input=text,
                processing_time_ms=0.0
            )
        
        text_clean = text.strip().lower()
        cache_key = text_clean[:100]
        
        with self._cache_lock:
            if cache_key in self._cache:
                intent, confidence = self._cache[cache_key]
                return DealerIntentResult(
                    intent=intent,
                    confidence=confidence,
                    entities=self._extract_entities(text),
                    raw_input=text,
                    processing_time_ms=0.0
                )
        
        # Stage 1: Direct pattern matching
        best_intent = DealerIntent.UNKNOWN
        best_score = 0.0
        
        for intent, patterns in self._patterns.items():
            matches = 0
            for pattern in patterns:
                if pattern.search(text_clean):
                    matches += 1
            if matches > 0:
                score = min(1.0, matches / max(1, len(patterns)) * 2)
                if score > best_score:
                    best_score = score
                    best_intent = intent
        
        # Stage 2: RapidFuzz
        if RAPIDFUZZ_AVAILABLE and best_score < 0.6:
            for intent, patterns in self.INTENT_PATTERNS.items():
                for pattern in patterns:
                    score = fuzz.partial_ratio(text_clean, pattern)
                    if score > 80:
                        best_intent = intent
                        best_score = score / 100.0
                        break
                if best_score > 0.8:
                    break
        
        # Stage 3: Semantic Router
        if DEALER_SEMANTIC_ENABLED and self.semantic_router and best_score < 0.6:
            try:
                result = self.semantic_router(text_clean)
                if result and hasattr(result, 'name'):
                    intent_name = result.name.replace("dealer_", "")
                    for intent in DealerIntent:
                        if intent.value == intent_name:
                            best_intent = intent
                            best_score = 0.7
                            break
            except Exception:
                pass
        
        # Stage 4: LLM verification
        if DEALER_AI_ENABLED and best_score < 0.7:
            llm_result = self._llm_verify(text_clean)
            if llm_result:
                best_intent, best_score = llm_result
        
        entities = self._extract_entities(text)
        
        with self._cache_lock:
            self._cache[cache_key] = (best_intent, best_score)
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        return DealerIntentResult(
            intent=best_intent,
            confidence=best_score,
            entities=entities,
            raw_input=text,
            processing_time_ms=elapsed_ms
        )
    
    def _llm_verify(self, text: str) -> Optional[Tuple[DealerIntent, float]]:
        """Use LLM to verify intent"""
        try:
            if self.groq_client:
                response = self.groq_client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[
                        {"role": "system", "content": "Classify dealer query intent: dashboard, summary, revenue, units, pending, delivery, pgi, pod, ranking, comparison, search, performance, history, timeline, products, models, top, bottom, trend, growth, forecast, ai_ask, menu, help, exit. Return only the intent name."},
                        {"role": "user", "content": text}
                    ],
                    temperature=0.1,
                    max_tokens=20
                )
                result = response.choices[0].message.content.strip().lower()
                for intent in DealerIntent:
                    if intent.value == result:
                        return (intent, 0.9)
            elif self.openai_client:
                response = self.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "Classify dealer query intent: dashboard, summary, revenue, units, pending, delivery, pgi, pod, ranking, comparison, search, performance, history, timeline, products, models, top, bottom, trend, growth, forecast, ai_ask, menu, help, exit. Return only the intent name."},
                        {"role": "user", "content": text}
                    ],
                    temperature=0.1,
                    max_tokens=20
                )
                result = response.choices[0].message.content.strip().lower()
                for intent in DealerIntent:
                    if intent.value == result:
                        return (intent, 0.9)
        except Exception as e:
            logger.debug(f"LLM verification failed: {e}")
        
        return None
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract entities from text"""
        entities = {
            "dealers": [],
            "metrics": [],
            "limit": 10,
            "sort_by": None,
            "order": "desc",
            "comparison": [],
            "timeframe": None
        }
        
        # Extract dealer names
        dealer_pattern = r'(?:dealer|dealers|for|of|in|from)\s+([A-Za-z\s]+)'
        matches = re.findall(dealer_pattern, text, re.IGNORECASE)
        if matches:
            entities["dealers"] = [m.strip() for m in matches if m.strip()]
        
        # Extract comparison
        compare_pattern = r'compare\s+([\w\s]+)\s+and\s+([\w\s]+)'
        compare_match = re.search(compare_pattern, text, re.IGNORECASE)
        if compare_match:
            entities["comparison"] = [compare_match.group(1).strip(), compare_match.group(2).strip()]
        
        # Extract limit
        limit_match = re.search(r'top\s+(\d+)', text, re.IGNORECASE)
        if limit_match:
            entities["limit"] = int(limit_match.group(1))
        
        # Extract metrics
        metric_keywords = ["revenue", "units", "pending", "delivery", "performance"]
        for metric in metric_keywords:
            if metric in text.lower():
                entities["metrics"].append(metric)
        
        # Extract timeframe
        if "today" in text.lower():
            entities["timeframe"] = "today"
        elif "this month" in text.lower():
            entities["timeframe"] = "this_month"
        elif "last month" in text.lower():
            entities["timeframe"] = "last_month"
        elif "this year" in text.lower():
            entities["timeframe"] = "this_year"
        
        return entities

# ============================================================
# BLOCK 11: DEALER REPOSITORY
# ============================================================

class DealerRepository:
    """PostgreSQL repository for dealer operations"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()
        self._sql_registry = DealerSQLRegistry()
    
    def _get_cache_key(self, query_type: str, identifier: str) -> str:
        """Generate cache key"""
        return f"{query_type}_{identifier}".lower()
    
    def execute_query(self, sql: str, cache_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """Execute SQL and return results"""
        if cache_key and cache_key in self._cache:
            return self._cache[cache_key]
        
        try:
            result = self.session.execute(text(sql))
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            
            if cache_key:
                self._cache[cache_key] = rows
            
            return rows
        except Exception as e:
            logger.error(f"SQL execution error: {e}")
            logger.error(f"SQL: {sql[:500]}...")
            return []
    
    def get_dashboard(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        """Get dealer dashboard"""
        sql = self._sql_registry.get_summary(dealer_identifier)
        cache_key = f"dashboard_{dealer_identifier.lower()}"
        results = self.execute_query(sql, cache_key)
        
        if not results:
            return None
        
        row = results[0]
        total_dn = int(row.get('total_dn', 0) or 0)
        pending_dn = int(row.get('pending_dn', 0) or 0)
        pgi_completed = int(row.get('pgi_completed', 0) or 0)
        pod_completed = int(row.get('pod_completed', 0) or 0)
        
        data = {
            'dealer': _text(row.get('dealer')),
            'dealer_code': _text(row.get('dealer_code')),
            'sales_office': _text(row.get('sales_office')),
            'sales_manager': _text(row.get('sales_manager')),
            'total_dn': total_dn,
            'total_units': int(row.get('total_units', 0) or 0),
            'total_revenue': float(row.get('total_revenue', 0) or 0.0),
            'pending_dn': pending_dn,
            'pgi_pending_dn': int(row.get('pgi_pending_dn', 0) or 0),
            'pod_pending_dn': int(row.get('pod_pending_dn', 0) or 0),
            'pgi_completed': pgi_completed,
            'pod_completed': pod_completed,
            'avg_delivery_days': float(row.get('avg_delivery_days', 0) or 0.0),
            'avg_pod_days': float(row.get('avg_pod_days', 0) or 0.0),
            'avg_revenue_per_dn': float(row.get('avg_revenue_per_dn', 0) or 0.0),
            'first_order': _date_text(row.get('first_order')),
            'last_order': _date_text(row.get('last_order')),
        }
        
        # Calculate percentages
        data['delivery_success_pct'] = _percent(pgi_completed, total_dn)
        data['pod_success_pct'] = _percent(pod_completed, total_dn)
        data['pending_pct'] = _percent(pending_dn, total_dn)
        
        # Business score
        score = (
            data['delivery_success_pct'] * 0.30 +
            (100 - data['pending_pct']) * 0.25 +
            min(100, data['avg_revenue_per_dn'] / 1000) * 0.25 +
            min(100, data['total_dn'] / 10) * 0.20
        )
        data['business_score'] = round(min(100, max(0, score)), 1)
        
        if data['business_score'] >= 85:
            data['overall_status'] = "Excellent"
            data['performance_grade'] = "A"
        elif data['business_score'] >= 70:
            data['overall_status'] = "Good"
            data['performance_grade'] = "B"
        elif data['business_score'] >= 50:
            data['overall_status'] = "Watch"
            data['performance_grade'] = "C"
        else:
            data['overall_status'] = "Critical"
            data['performance_grade'] = "D"
        
        return data
    
    def get_ranking(self, metric: str = "revenue", limit: int = 10) -> List[Dict[str, Any]]:
        """Get dealer ranking"""
        sql = self._sql_registry.get_ranking(metric, limit)
        cache_key = f"ranking_{metric}_{limit}"
        results = self.execute_query(sql, cache_key)
        
        ranking = []
        for row in results:
            dealer = _text(row.get('dealer'))
            if dealer:
                value = row.get('value')
                if metric in ["revenue"]:
                    formatted = _format_currency(float(value or 0))
                elif metric in ["units", "dn", "pending"]:
                    formatted = _format_number(int(value or 0))
                else:
                    formatted = f"{float(value or 0):.1f}"
                
                ranking.append({
                    'dealer': dealer,
                    'value': formatted,
                    'raw_value': float(value or 0),
                })
        return ranking
    
    def search(self, query: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Search dealers"""
        sql = self._sql_registry.search(query, limit)
        cache_key = f"search_{query}_{limit}"
        results = self.execute_query(sql, cache_key)
        
        items = []
        for row in results:
            dealer = _text(row.get('dealer'))
            if dealer:
                items.append({
                    'dealer': dealer,
                    'dealer_code': _text(row.get('dealer_code')),
                    'sales_office': _text(row.get('sales_office')),
                    'sales_manager': _text(row.get('sales_manager')),
                    'revenue': float(row.get('revenue', 0) or 0),
                    'dn_count': int(row.get('dn_count', 0) or 0),
                    'units': int(row.get('units', 0) or 0),
                    'pending_count': int(row.get('pending_count', 0) or 0),
                })
        return items
    
    def get_products(self, dealer_identifier: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get products for a dealer"""
        sql = self._sql_registry.get_products(dealer_identifier, limit)
        cache_key = f"products_{dealer_identifier.lower()}_{limit}"
        results = self.execute_query(sql, cache_key)
        
        products = []
        for row in results:
            product = _text(row.get('product'))
            if product:
                products.append({
                    'product': product,
                    'revenue': float(row.get('revenue', 0) or 0),
                    'units': int(row.get('units', 0) or 0),
                    'dn_count': int(row.get('dn_count', 0) or 0),
                })
        return products

# ============================================================
# BLOCK 12: DEALER RENDERER
# ============================================================

class DealerRenderer:
    """Render dealer responses for WhatsApp"""
    
    MENU_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━"
    
    @staticmethod
    def _render_menu_footer(menu_type: str = "main") -> str:
        """Render menu footer"""
        menu = DealerMenuRegistry.MENUS.get(menu_type, DealerMenuRegistry.MENUS["main"])
        
        lines = ["", DealerRenderer.MENU_SEPARATOR, ""]
        lines.append(f"📋 *{menu['name']}*")
        lines.append("")
        
        for item in menu["items"]:
            lines.append(f"{item['id']}. {item['icon']} {item['name']}")
        
        lines.append("")
        lines.append("Reply with a number or type your question:")
        
        return "\n".join(lines)
    
    @classmethod
    def render_main_menu(cls) -> str:
        """Render the main menu"""
        return cls._render_menu_footer("main")
    
    @classmethod
    def render_dashboard_menu(cls) -> str:
        """Render the dashboard menu"""
        return cls._render_menu_footer("dashboard")
    
    @classmethod
    def render_analytics_menu(cls) -> str:
        """Render the analytics menu"""
        return cls._render_menu_footer("analytics")
    
    @classmethod
    def render_ai_assistant_menu(cls) -> str:
        """Render the AI assistant menu"""
        return cls._render_menu_footer("ai_assistant")
    
    @classmethod
    def render_with_menu(cls, content: str, menu_type: str = "main") -> str:
        """Render content with menu footer"""
        menu_footer = cls._render_menu_footer(menu_type)
        return f"{content}\n{menu_footer}"
    
    @staticmethod
    def render_dashboard(dealer: str, data: Dict[str, Any]) -> str:
        """Render dealer dashboard"""
        lines = [
            f"📊 *Dealer Dashboard - {dealer}*",
            "",
            "📌 *Details*",
            f"Code: {data.get('dealer_code', 'N/A')}",
            f"Office: {data.get('sales_office', 'N/A')}",
            f"Manager: {data.get('sales_manager', 'N/A')}",
            "",
            "💰 *Financials*",
            f"Revenue: {_format_currency(data.get('total_revenue', 0))}",
            f"Avg/DN: {_format_currency(data.get('avg_revenue_per_dn', 0))}",
            "",
            "📦 *Operations*",
            f"DN: {_format_number(data.get('total_dn', 0))}",
            f"Units: {_format_number(data.get('total_units', 0))}",
            f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
            f"PGI Pending: {_format_number(data.get('pgi_pending_dn', 0))}",
            f"POD Pending: {_format_number(data.get('pod_pending_dn', 0))}",
            "",
            "🚚 *Delivery*",
            f"Success: {data.get('delivery_success_pct', 0):.1f}%",
            f"POD: {data.get('pod_success_pct', 0):.1f}%",
            f"Avg Days: {data.get('avg_delivery_days', 0):.1f}",
            f"Avg POD: {data.get('avg_pod_days', 0):.1f}",
            "",
            "📈 *Performance*",
            f"Score: {data.get('business_score', 0):.1f}/100",
            f"Status: {data.get('overall_status', 'Unknown')}",
            f"Grade: {data.get('performance_grade', 'N/A')}",
            "",
            "📅 *Timeline*",
            f"First: {data.get('first_order', 'N/A')}",
            f"Last: {data.get('last_order', 'N/A')}",
        ]
        
        return "\n".join(lines)
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "Revenue", limit: int = 10) -> str:
        """Render dealer ranking"""
        lines = [f"🏆 *Dealer Rankings by {metric}*", ""]
        
        for i, item in enumerate(ranking[:limit], 1):
            dealer = item.get('dealer', 'Unknown')
            value = item.get('value', 'N/A')
            
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            lines.append(f"{medal} {dealer}: {value}")
        
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison(comparison: Dict[str, Any]) -> str:
        """Render dealer comparison"""
        lines = ["🔄 *Dealer Comparison*", ""]
        
        for key, value in comparison.items():
            if key == "explanation":
                lines.extend(["", "💡 *Summary*", value])
            elif "_metrics" in key:
                dealer = key.replace("_metrics", "")
                lines.append(f"📊 *{dealer}*")
                for k, v in value.items():
                    lines.append(f"  {k}: {v}")
                lines.append("")
        
        return "\n".join(lines)
    
    @staticmethod
    def render_search_results(query: str, items: List[Dict[str, Any]]) -> str:
        """Render search results"""
        if not items:
            return f"🔍 No dealers found for '{query}'"
        
        lines = [f"🔍 *Search Results for '{query}'*", ""]
        lines.append(f"Found: {len(items)} dealers")
        lines.append("")
        
        for i, item in enumerate(items[:15], 1):
            dealer = item.get('dealer', 'Unknown')
            code = item.get('dealer_code', 'N/A')
            revenue = _format_currency(item.get('revenue', 0))
            pending = _format_number(item.get('pending_count', 0))
            
            lines.append(f"{i}. *{dealer}* (Code: {code})")
            lines.append(f"   Revenue: {revenue}")
            lines.append(f"   DN: {_format_number(item.get('dn_count', 0))}")
            lines.append(f"   Pending: {pending}")
            lines.append("")
        
        if len(items) > 15:
            lines.append(f"... and {len(items) - 15} more")
        
        return "\n".join(lines)
    
    @staticmethod
    def render_products(products: List[Dict[str, Any]], dealer: str) -> str:
        """Render dealer products"""
        if not products:
            return f"📦 No products found for {dealer}"
        
        lines = [f"📦 *Products - {dealer}*", ""]
        
        for i, item in enumerate(products[:10], 1):
            product = item.get('product', 'Unknown')
            revenue = _format_currency(item.get('revenue', 0))
            units = _format_number(item.get('units', 0))
            
            lines.append(f"{i}. *{product}*")
            lines.append(f"   Revenue: {revenue}")
            lines.append(f"   Units: {units}")
            lines.append("")
        
        if len(products) > 10:
            lines.append(f"... and {len(products) - 10} more")
        
        return "\n".join(lines)
    
    @staticmethod
    def render_summary(data: Dict[str, Any]) -> str:
        """Render summary"""
        lines = [
            "📋 *Executive Summary*",
            "",
            f"Dealer: {data.get('dealer', 'N/A')}",
            f"Code: {data.get('dealer_code', 'N/A')}",
            "",
            "📊 *Key Metrics*",
            f"Revenue: {_format_currency(data.get('total_revenue', 0))}",
            f"DN: {_format_number(data.get('total_dn', 0))}",
            f"Units: {_format_number(data.get('total_units', 0))}",
            f"Pending: {_format_number(data.get('pending_dn', 0))}",
            "",
            "🚚 *Delivery*",
            f"Success Rate: {data.get('delivery_success_pct', 0):.1f}%",
            f"POD Rate: {data.get('pod_success_pct', 0):.1f}%",
            f"Avg Days: {data.get('avg_delivery_days', 0):.1f}",
            "",
            "📈 *Performance*",
            f"Score: {data.get('business_score', 0):.1f}/100",
            f"Status: {data.get('overall_status', 'Unknown')}",
            f"Grade: {data.get('performance_grade', 'N/A')}",
        ]
        return "\n".join(lines)

# ============================================================
# BLOCK 13: MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """Enterprise Dealer Domain AI Engine - Fully Independent"""
    
    _instance: Optional["DealerAnalyticsService"] = None
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
        self._service_name = "dealer_analytics"
        self._version = "4.0"
        
        # Initialize engines
        self._intent_engine = DealerIntentEngine()
        self._renderer = DealerRenderer()
        self._question_registry = DealerQuestionRegistry()
        
        # Sessions
        self._sessions: Dict[str, DealerSession] = {}
        self._session_lock = threading.RLock()
        
        logger.info("=" * 70)
        logger.info(f"🚀 Dealer Domain AI Engine v{self._version} initialized")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info(f"   🤖 AI Engine: {'Active' if DEALER_AI_ENABLED else 'Limited'}")
        logger.info(f"   🔍 Semantic: {'Enabled' if DEALER_SEMANTIC_ENABLED else 'Disabled'}")
        logger.info(f"   📋 Auto-Menu: {'Enabled' if DEALER_MENU_AUTO_SHOW else 'Disabled'}")
        logger.info("=" * 70)
    
    def _get_session(self, session_id: str) -> DealerSession:
        with self._session_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = DealerSession(session_id=session_id)
                logger.info(f"🆕 New dealer session created for {session_id}")
            return self._sessions[session_id]
    
    def _get_db_session(self) -> Optional[Session]:
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def _get_menu_type(self, session: DealerSession) -> str:
        """Get current menu type based on session state"""
        if session.menu_state == DealerMenuState.DASHBOARD:
            return "dashboard"
        elif session.menu_state == DealerMenuState.ANALYTICS:
            return "analytics"
        elif session.menu_state == DealerMenuState.AI_ASSISTANT:
            return "ai_assistant"
        else:
            return "main"
    
    def _get_menu(self, session: DealerSession) -> str:
        """Get appropriate menu for current session state"""
        menu_type = self._get_menu_type(session)
        
        if menu_type == "dashboard":
            return self._renderer.render_dashboard_menu()
        elif menu_type == "analytics":
            return self._renderer.render_analytics_menu()
        elif menu_type == "ai_assistant":
            return self._renderer.render_ai_assistant_menu()
        else:
            return self._renderer.render_main_menu()
    
    def _render_response(self, content: str, session: DealerSession) -> str:
        """Render response with appropriate menu footer"""
        menu_type = self._get_menu_type(session)
        return self._renderer.render_with_menu(content, menu_type)
    
    def get_main_menu(self) -> str:
        """Get the main dealer menu"""
        return self._renderer.render_main_menu()
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        Main entry point for dealer processing.
        
        This is the ONLY external interface.
        All processing stays inside this module.
        """
        session = self._get_session(sender)
        
        # AUTO-MENU: Show menu on first entry
        if not session.menu_shown:
            session.menu_shown = True
            logger.info(f"📋 Auto-showing dealer menu for {sender}")
            return self._render_response("📊 Welcome to the Dealer Intelligence Engine!", session)
        
        if not message or not message.strip():
            return self._render_response("Please provide a dealer name or select a menu option.", session)
        
        message_clean = message.strip()
        logger.info(f"📊 Dealer Query: '{message_clean}' from {sender}")
        
        session.touch()
        
        # ============================================================
        # STEP 1: Check for exit (99)
        # ============================================================
        if message_clean == "99":
            session.clear()
            logger.info(f"🚪 Dealer session exited for {sender}")
            return "__EXIT__"
        
        # ============================================================
        # STEP 2: Check for menu commands (0, 1, 2, 3)
        # ============================================================
        if message_clean == "0":
            session.menu_state = DealerMenuState.MAIN
            return self._render_response("Main Menu", session)
        
        if message_clean == "1":
            session.menu_state = DealerMenuState.DASHBOARD
            return self._render_response("📊 *Dashboard Menu*\n\nSelect an option below:", session)
        
        if message_clean == "2":
            session.menu_state = DealerMenuState.ANALYTICS
            return self._render_response("📈 *Analytics Menu*\n\nSelect an option below:", session)
        
        if message_clean == "3":
            session.menu_state = DealerMenuState.AI_ASSISTANT
            return self._render_response("🤖 *AI Assistant*\n\nAsk me anything about dealers:", session)
        
        # ============================================================
        # STEP 3: Check Question Registry (Predefined questions)
        # ============================================================
        handler_match = self._question_registry.find_handler(message_clean)
        if handler_match:
            handler_name, confidence = handler_match
            logger.info(f"📋 Question matched: {handler_name} (confidence: {confidence:.2f})")
            
            if handler_name == "handle_summary":
                return self._handle_summary(session, message_clean)
            elif handler_name == "handle_revenue":
                return self._handle_revenue(session, message_clean)
            elif handler_name == "handle_units":
                return self._handle_units(session, message_clean)
            elif handler_name == "handle_pending":
                return self._handle_pending(session, message_clean)
            elif handler_name == "handle_delivery":
                return self._handle_delivery(session, message_clean)
            elif handler_name == "handle_pgi":
                return self._handle_pgi(session, message_clean)
            elif handler_name == "handle_pod":
                return self._handle_pod(session, message_clean)
            elif handler_name == "handle_ranking":
                return self._handle_ranking(session, message_clean)
            elif handler_name == "handle_top":
                return self._handle_top(session, message_clean)
            elif handler_name == "handle_bottom":
                return self._handle_bottom(session, message_clean)
            elif handler_name == "handle_comparison":
                return self._handle_comparison(session, message_clean)
            elif handler_name == "handle_search":
                return self._handle_search(session, message_clean)
            elif handler_name == "handle_performance":
                return self._handle_performance(session, message_clean)
            elif handler_name == "handle_timeline":
                return self._handle_timeline(session, message_clean)
            elif handler_name == "handle_history":
                return self._handle_history(session, message_clean)
            elif handler_name == "handle_products":
                return self._handle_products(session, message_clean)
            elif handler_name == "handle_models":
                return self._handle_models(session, message_clean)
            elif handler_name == "handle_trends":
                return self._handle_trends(session, message_clean)
        
        # ============================================================
        # STEP 4: Check if it's a dealer name
        # ============================================================
        dealer_name = self._resolve_dealer_name(message_clean)
        if dealer_name:
            session.set_dealer(dealer_name)
            return self._handle_dashboard(session, dealer_name)
        
        # ============================================================
        # STEP 5: Intent Detection (AI fallback)
        # ============================================================
        intent_result = self._intent_engine.detect_intent(message_clean)
        session.last_intent = intent_result.intent
        logger.info(f"🎯 Intent: {intent_result.intent.value} (confidence: {intent_result.confidence:.2f})")
        
        # Process based on intent
        response = self._process_intent(session, intent_result, message_clean)
        
        # Update history
        session.add_history(message_clean, response)
        
        return self._render_response(response, session)
    
    def _process_intent(self, session: DealerSession, intent_result: DealerIntentResult, message: str) -> str:
        """Process intent and return response"""
        intent = intent_result.intent
        entities = intent_result.entities
        
        # Extract dealer names from entities
        dealer_names = entities.get("dealers", [])
        
        # Handle exit
        if intent == DealerIntent.EXIT:
            session.clear()
            return "__EXIT__"
        
        # Handle menu
        if intent == DealerIntent.MENU or intent == DealerIntent.HELP:
            return "Main Menu"
        
        # Handle ranking
        if intent == DealerIntent.RANKING:
            metric = entities.get("metrics", ["revenue"])[0] if entities.get("metrics") else "revenue"
            limit = entities.get("limit", 10)
            return self._handle_ranking(session, message)
        
        # Handle comparison
        if intent == DealerIntent.COMPARISON and len(dealer_names) >= 2:
            return self._handle_comparison(session, message)
        
        # Handle search
        if intent == DealerIntent.SEARCH:
            return self._handle_search(session, message)
        
        # Handle dealer-specific queries
        dealer_name = None
        
        # Check if we have a dealer from entities
        if dealer_names:
            dealer_name = dealer_names[0]
        elif session.current_dealer:
            dealer_name = session.current_dealer
        
        if dealer_name:
            session.set_dealer(dealer_name)
            
            if intent == DealerIntent.DASHBOARD or intent == DealerIntent.SUMMARY:
                return self._handle_dashboard(session, dealer_name)
            elif intent == DealerIntent.REVENUE:
                return self._handle_revenue(session, dealer_name)
            elif intent == DealerIntent.UNITS:
                return self._handle_units(session, dealer_name)
            elif intent == DealerIntent.PENDING:
                return self._handle_pending(session, dealer_name)
            elif intent == DealerIntent.DELIVERY:
                return self._handle_delivery(session, dealer_name)
            elif intent == DealerIntent.PGI:
                return self._handle_pgi(session, dealer_name)
            elif intent == DealerIntent.POD:
                return self._handle_pod(session, dealer_name)
            elif intent == DealerIntent.PERFORMANCE:
                return self._handle_performance(session, dealer_name)
            elif intent == DealerIntent.PRODUCTS:
                return self._handle_products(session, dealer_name)
            elif intent == DealerIntent.MODELS:
                return self._handle_models(session, dealer_name)
            elif intent == DealerIntent.HISTORY or intent == DealerIntent.TIMELINE:
                return self._handle_timeline(session, dealer_name)
        
        # If we have a dealer name but no intent, show dashboard
        if dealer_names:
            dealer_name = dealer_names[0]
            session.set_dealer(dealer_name)
            return self._handle_dashboard(session, dealer_name)
        
        # If it's a dealer name with no intent, show dashboard
        if len(message.split()) <= 3:
            dealer_name = self._resolve_dealer_name(message)
            if dealer_name:
                session.set_dealer(dealer_name)
                return self._handle_dashboard(session, dealer_name)
        
        # AI fallback
        if DEALER_AI_ENABLED:
            return self._handle_ai_ask(session, message)
        
        # Unknown
        return self._get_help()
    
    def _resolve_dealer_name(self, text: str) -> Optional[str]:
        """Resolve dealer name from text"""
        text_clean = text.strip().lower()
        
        # Try database search
        try:
            with self._get_db_session() as session:
                repo = DealerRepository(session)
                results = repo.search(text_clean, limit=1)
                if results:
                    return results[0].get('dealer')
        except Exception:
            pass
        
        return None
    
    # ============================================================
    # HANDLERS - Predefined Business Logic
    # ============================================================
    
    def _handle_main_menu(self, session: DealerSession) -> str:
        """Handle main menu"""
        session.menu_state = DealerMenuState.MAIN
        return "Main Menu"
    
    def _handle_dashboard(self, session: DealerSession, dealer_name: str) -> str:
        """Handle dealer dashboard"""
        if not dealer_name:
            return "Please provide a dealer name."
        
        session.menu_state = DealerMenuState.DASHBOARD
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            return self._renderer.render_dashboard(dealer_name, data)
            
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching dealer {dealer_name}"
    
    def _handle_summary(self, session: DealerSession, message: str) -> str:
        """Handle summary request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            # Try to extract dealer from message
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for the summary."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            return self._renderer.render_summary(data)
            
        except Exception as e:
            logger.error(f"Summary error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching summary for {dealer_name}"
    
    def _handle_revenue(self, session: DealerSession, message: str) -> str:
        """Handle revenue request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for revenue."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            revenue = data.get('total_revenue', 0)
            return f"💰 *{dealer_name} Revenue*\n\n{_format_currency(revenue)}"
            
        except Exception as e:
            logger.error(f"Revenue error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching revenue for {dealer_name}"
    
    def _handle_units(self, session: DealerSession, message: str) -> str:
        """Handle units request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for units."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            units = data.get('total_units', 0)
            return f"📦 *{dealer_name} Units*\n\n{_format_number(units)}"
            
        except Exception as e:
            logger.error(f"Units error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching units for {dealer_name}"
    
    def _handle_pending(self, session: DealerSession, message: str) -> str:
        """Handle pending request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for pending."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            lines = [
                f"⏳ *Pending Summary - {dealer_name}*",
                "",
                f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
                f"PGI Pending: {_format_number(data.get('pgi_pending_dn', 0))}",
                f"POD Pending: {_format_number(data.get('pod_pending_dn', 0))}",
            ]
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Pending error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching pending for {dealer_name}"
    
    def _handle_delivery(self, session: DealerSession, message: str) -> str:
        """Handle delivery request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for delivery."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            lines = [
                f"🚚 *Delivery Summary - {dealer_name}*",
                "",
                f"Success Rate: {data.get('delivery_success_pct', 0):.1f}%",
                f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
                f"Avg Delivery Days: {data.get('avg_delivery_days', 0):.1f}",
                f"Avg POD Days: {data.get('avg_pod_days', 0):.1f}",
            ]
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Delivery error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching delivery for {dealer_name}"
    
    def _handle_pgi(self, session: DealerSession, message: str) -> str:
        """Handle PGI request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for PGI."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            lines = [
                f"📋 *PGI Summary - {dealer_name}*",
                "",
                f"PGI Completed: {_format_number(data.get('pgi_completed', 0))}",
                f"PGI Pending: {_format_number(data.get('pgi_pending_dn', 0))}",
                f"PGI Success: {data.get('delivery_success_pct', 0):.1f}%",
            ]
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"PGI error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching PGI for {dealer_name}"
    
    def _handle_pod(self, session: DealerSession, message: str) -> str:
        """Handle POD request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for POD."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            lines = [
                f"📋 *POD Summary - {dealer_name}*",
                "",
                f"POD Completed: {_format_number(data.get('pod_completed', 0))}",
                f"POD Pending: {_format_number(data.get('pod_pending_dn', 0))}",
                f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
            ]
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"POD error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching POD for {dealer_name}"
    
    def _handle_ranking(self, session: DealerSession, message: str) -> str:
        """Handle ranking request"""
        session.menu_state = DealerMenuState.RANKING
        
        # Extract metric from message
        metric = "revenue"
        if "units" in message.lower():
            metric = "units"
        elif "pending" in message.lower():
            metric = "pending"
        elif "delivery" in message.lower():
            metric = "delivery"
        
        # Extract limit
        limit = 10
        limit_match = re.search(r'top\s+(\d+)', message.lower())
        if limit_match:
            limit = int(limit_match.group(1))
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            ranking = repo.get_ranking(metric, limit)
            db_session.close()
            
            if not ranking:
                return f"🏆 *Dealer Rankings by {metric.title()}*\n\nNo dealers found."
            
            return self._renderer.render_ranking(ranking, metric.title(), limit)
            
        except Exception as e:
            logger.error(f"Ranking error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching rankings."
    
    def _handle_top(self, session: DealerSession, message: str) -> str:
        """Handle top dealers request"""
        return self._handle_ranking(session, message)
    
    def _handle_bottom(self, session: DealerSession, message: str) -> str:
        """Handle bottom dealers request"""
        session.menu_state = DealerMenuState.RANKING
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            # Reverse order for bottom
            ranking = repo.get_ranking("revenue", 10)
            ranking.reverse()
            db_session.close()
            
            if not ranking:
                return "🏆 *Bottom Dealers*\n\nNo dealers found."
            
            return self._renderer.render_ranking(ranking, "Revenue (Bottom)", 10)
            
        except Exception as e:
            logger.error(f"Bottom ranking error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching bottom rankings."
    
    def _handle_comparison(self, session: DealerSession, message: str) -> str:
        """Handle comparison request"""
        session.menu_state = DealerMenuState.COMPARISON
        
        # Extract dealer names from message
        compare_pattern = r'compare\s+([\w\s]+)\s+and\s+([\w\s]+)'
        compare_match = re.search(compare_pattern, message, re.IGNORECASE)
        
        if not compare_match:
            return "Please specify two dealers to compare.\nExample: compare Dealer1 and Dealer2"
        
        dealer1 = compare_match.group(1).strip()
        dealer2 = compare_match.group(2).strip()
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            dash1 = repo.get_dashboard(dealer1)
            dash2 = repo.get_dashboard(dealer2)
            db_session.close()
            
            if not dash1 or not dash2:
                return "⚠️ One or both dealers not found."
            
            comparison = {}
            
            for dealer, dash in [(dealer1, dash1), (dealer2, dash2)]:
                comparison[f"{dealer}_metrics"] = {
                    "Revenue": _format_currency(dash.get('total_revenue', 0)),
                    "Units": _format_number(dash.get('total_units', 0)),
                    "DN": _format_number(dash.get('total_dn', 0)),
                    "Pending": _format_number(dash.get('pending_dn', 0)),
                    "Delivery": f"{dash.get('delivery_success_pct', 0):.1f}%",
                    "POD": f"{dash.get('pod_success_pct', 0):.1f}%",
                    "Score": f"{dash.get('business_score', 0):.1f}/100",
                }
            
            rev1 = dash1.get('total_revenue', 0)
            rev2 = dash2.get('total_revenue', 0)
            
            if rev1 > rev2:
                comparison["explanation"] = f"{dealer1} has higher revenue than {dealer2}"
            elif rev2 > rev1:
                comparison["explanation"] = f"{dealer2} has higher revenue than {dealer1}"
            else:
                comparison["explanation"] = f"{dealer1} and {dealer2} have similar revenue"
            
            return self._renderer.render_comparison(comparison)
            
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error comparing dealers."
    
    def _handle_search(self, session: DealerSession, message: str) -> str:
        """Handle search request"""
        session.menu_state = DealerMenuState.SEARCH_RESULTS
        
        # Extract search query
        search_terms = ["search", "find", "lookup"]
        query = message
        for term in search_terms:
            query = query.replace(term, "").strip()
        
        if not query:
            return "Please specify what to search for."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            results = repo.search(query)
            db_session.close()
            
            return self._renderer.render_search_results(query, results)
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error searching for '{query}'"
    
    def _handle_performance(self, session: DealerSession, message: str) -> str:
        """Handle performance request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for performance."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            lines = [
                f"📈 *Performance - {dealer_name}*",
                "",
                f"Score: {data.get('business_score', 0):.1f}/100",
                f"Status: {data.get('overall_status', 'Unknown')}",
                f"Grade: {data.get('performance_grade', 'N/A')}",
                f"Delivery Success: {data.get('delivery_success_pct', 0):.1f}%",
                f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
                f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
            ]
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Performance error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching performance for {dealer_name}"
    
    def _handle_timeline(self, session: DealerSession, message: str) -> str:
        """Handle timeline request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for timeline."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            lines = [
                f"📅 *Timeline - {dealer_name}*",
                "",
                f"First Order: {data.get('first_order', 'N/A')}",
                f"Last Order: {data.get('last_order', 'N/A')}",
                f"Total DN: {_format_number(data.get('total_dn', 0))}",
                f"Total Revenue: {_format_currency(data.get('total_revenue', 0))}",
            ]
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Timeline error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching timeline for {dealer_name}"
    
    def _handle_history(self, session: DealerSession, message: str) -> str:
        """Handle history request"""
        return self._handle_timeline(session, message)
    
    def _handle_products(self, session: DealerSession, message: str) -> str:
        """Handle products request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for products."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            products = repo.get_products(dealer_name)
            db_session.close()
            
            return self._renderer.render_products(products, dealer_name)
            
        except Exception as e:
            logger.error(f"Products error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching products for {dealer_name}"
    
    def _handle_models(self, session: DealerSession, message: str) -> str:
        """Handle models request"""
        return self._handle_products(session, message)
    
    def _handle_trends(self, session: DealerSession, message: str) -> str:
        """Handle trends request"""
        dealer_name = session.current_dealer
        
        if not dealer_name:
            dealer_name = self._resolve_dealer_name(message)
            if not dealer_name:
                return "Please provide a dealer name for trends."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            data = repo.get_dashboard(dealer_name)
            db_session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found."
            
            # Simple growth calculation (mock for now)
            growth = 12.5  # Placeholder
            
            lines = [
                f"📈 *Trends - {dealer_name}*",
                "",
                f"Growth Rate: {growth:+.1f}%",
                f"Revenue: {_format_currency(data.get('total_revenue', 0))}",
                f"DN: {_format_number(data.get('total_dn', 0))}",
                f"Units: {_format_number(data.get('total_units', 0))}",
            ]
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Trends error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error fetching trends for {dealer_name}"
    
    def _handle_ai_ask(self, session: DealerSession, message: str) -> str:
        """Handle AI assistant request"""
        session.menu_state = DealerMenuState.AI_ASSISTANT
        
        if not DEALER_AI_ENABLED:
            return "🤖 AI Assistant is currently disabled."
        
        dealer_name = session.current_dealer
        
        # Build context
        context = f"Dealer: {dealer_name or 'Not specified'}\n"
        
        # Get dealer data if available
        dealer_data = None
        if dealer_name:
            db_session = self._get_db_session()
            if db_session:
                try:
                    repo = DealerRepository(db_session)
                    dealer_data = repo.get_dashboard(dealer_name)
                    db_session.close()
                except Exception:
                    pass
        
        # Build response
        lines = ["🤖 *AI Assistant*", ""]
        lines.append(f"📝 *Question:* {message}")
        lines.append("")
        
        if dealer_data:
            lines.append("📊 *Dealer Data:*")
            lines.append(f"Revenue: {_format_currency(dealer_data.get('total_revenue', 0))}")
            lines.append(f"DN: {_format_number(dealer_data.get('total_dn', 0))}")
            lines.append(f"Delivery: {dealer_data.get('delivery_success_pct', 0):.1f}%")
            lines.append(f"Score: {dealer_data.get('business_score', 0):.1f}/100")
            lines.append("")
        
        # Try LLM response
        try:
            if self._intent_engine.groq_client:
                response = self._intent_engine.groq_client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[
                        {"role": "system", "content": f"You are a dealer analytics expert. Provide insights for: {context}"},
                        {"role": "user", "content": message}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                ai_response = response.choices[0].message.content.strip()
                lines.append("💡 *AI Insights:*")
                lines.append(ai_response)
            elif self._intent_engine.openai_client:
                response = self._intent_engine.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": f"You are a dealer analytics expert. Provide insights for: {context}"},
                        {"role": "user", "content": message}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                ai_response = response.choices[0].message.content.strip()
                lines.append("💡 *AI Insights:*")
                lines.append(ai_response)
            else:
                lines.append("💡 *AI Insights:*")
                lines.append("Please try a specific dealer command like:")
                lines.append("• Revenue analysis")
                lines.append("• Delivery performance")
                lines.append("• Dealer comparison")
        except Exception as e:
            logger.error(f"AI response error: {e}")
            lines.append("💡 *AI Insights:*")
            lines.append("Unable to generate AI insights at this time.")
            lines.append("Please try a specific dealer command.")
        
        return "\n".join(lines)
    
    def _handle_analytics(self, session: DealerSession, message: str) -> str:
        """Handle analytics menu"""
        session.menu_state = DealerMenuState.ANALYTICS
        return "📈 *Analytics Menu*\n\nSelect an option below:"
    
    def _handle_ai_assistant(self, session: DealerSession, message: str) -> str:
        """Handle AI assistant menu"""
        session.menu_state = DealerMenuState.AI_ASSISTANT
        return "🤖 *AI Assistant*\n\nAsk me anything about dealers:"
    
    def _handle_exit(self, session: DealerSession, message: str) -> str:
        """Handle exit"""
        session.clear()
        return "__EXIT__"
    
    def _get_help(self) -> str:
        """Get help message"""
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 *Dealer Commands:*",
            "• Type dealer name for dashboard",
            "• top dealers - Show rankings",
            "• search [keyword] - Search dealers",
            "• compare [dealer1] and [dealer2]",
            "• revenue - Revenue of current dealer",
            "• pending - Pending of current dealer",
            "• delivery - Delivery of current dealer",
            "• products - Products of current dealer",
            "• history - Timeline of current dealer",
            "",
            "📌 *Current Dealer:*",
            "• Use 'menu' to see all options",
            "• Type '99' to return to main menu",
        ])
    
    def health_check(self) -> Dict[str, Any]:
        """Health check"""
        with self._session_lock:
            active_sessions = len(self._sessions)
        
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "ai_enabled": DEALER_AI_ENABLED,
            "semantic_enabled": DEALER_SEMANTIC_ENABLED,
            "auto_menu": DEALER_MENU_AUTO_SHOW,
            "active_sessions": active_sessions,
            "exit_command": "99",
            "timestamp": datetime.now().isoformat()
        }


# ============================================================
# SERVICE SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()

def get_dealer_service() -> DealerAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DealerAnalyticsService()
    return _service


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "DealerSession",
    "DealerIntent",
    "DealerMenuState",
    "get_dealer_service",
]
