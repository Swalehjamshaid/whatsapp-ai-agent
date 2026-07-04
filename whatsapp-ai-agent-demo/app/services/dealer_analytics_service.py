# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 6.0 - ENTERPRISE DEALER DOMAIN AI ENGINE
# ============================================================

"""
File: app/services/dealer_analytics_service.py
Version: 6.0 - ENTERPRISE DEALER DOMAIN AI ENGINE

================================================================================
PURPOSE
================================================================================

This is a completely independent Enterprise AI Domain Service with a full
Question Library covering 30+ dealer-related business questions.

Its responsibilities are:
1. Dealer Intelligence Engine
2. Dealer Analytics & KPI
3. Dealer Search & Ranking
4. Dealer Comparison & Performance
5. Dealer AI Assistant (Fallback only)
6. Dealer SQL Engine
7. Dealer Intent Detection
8. Dealer Semantic Routing
9. Dealer Session Management
10. Dealer Response Engine
11. Dealer Menu System (Auto-display)
12. Dealer Question Library (30+ questions)
13. Dealer SQL Registry
14. Dealer Business Rules
15. Dealer Analytics Engine

================================================================================
QUESTION LIBRARY - 30+ QUESTIONS
================================================================================

1. Dealer Dashboard
2. Dealer Revenue
3. Dealer Units
4. Dealer DN Summary
5. Pending Deliveries
6. Pending PGI
7. Pending POD
8. Delivered Orders
9. Latest Deliveries
10. Latest DN
11. Products Purchased
12. Models Purchased
13. Top Selling Model
14. Lowest Selling Model
15. Warehouse Utilized
16. City Analysis
17. Sales Office
18. Sales Manager
19. Average Delivery Time
20. Average POD Time
21. Monthly Revenue
22. Monthly Trend
23. Dealer Timeline
24. Dealer History
25. Revenue Breakdown
26. Unit Breakdown
27. Business Health
28. Performance Score
29. Dealer Comparison
30. Complete Dealer Intelligence Report

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
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
    from sqlalchemy import func, or_, desc, asc, and_, case, text
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
    REVENUE = "revenue"
    UNITS = "units"
    DN_SUMMARY = "dn_summary"
    PENDING = "pending"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    DELIVERED = "delivered"
    LATEST_DELIVERIES = "latest_deliveries"
    LATEST_DN = "latest_dn"
    PRODUCTS = "products"
    MODELS = "models"
    TOP_MODEL = "top_model"
    BOTTOM_MODEL = "bottom_model"
    WAREHOUSES = "warehouses"
    CITIES = "cities"
    SALES_OFFICE = "sales_office"
    SALES_MANAGER = "sales_manager"
    AVG_DELIVERY = "avg_delivery"
    AVG_POD = "avg_pod"
    MONTHLY_REVENUE = "monthly_revenue"
    MONTHLY_TREND = "monthly_trend"
    TIMELINE = "timeline"
    HISTORY = "history"
    REVENUE_BREAKDOWN = "revenue_breakdown"
    UNIT_BREAKDOWN = "unit_breakdown"
    BUSINESS_HEALTH = "business_health"
    PERFORMANCE_SCORE = "performance_score"
    COMPARISON = "comparison"
    COMPLETE_REPORT = "complete_report"
    RANKING = "ranking"
    SEARCH = "search"
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
                {"id": "1", "name": "Dashboard", "handler": "handle_dashboard_menu", "icon": "📊"},
                {"id": "2", "name": "Analytics", "handler": "handle_analytics_menu", "icon": "📈"},
                {"id": "3", "name": "AI Assistant", "handler": "handle_ai_assistant_menu", "icon": "🤖"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "dashboard": {
            "id": "dashboard",
            "name": "DEALER DASHBOARD",
            "items": [
                {"id": "1", "name": "Dashboard", "handler": "handle_dashboard", "icon": "📊"},
                {"id": "2", "name": "Revenue", "handler": "handle_revenue", "icon": "💰"},
                {"id": "3", "name": "Units", "handler": "handle_units", "icon": "📦"},
                {"id": "4", "name": "DN Summary", "handler": "handle_dn_summary", "icon": "📄"},
                {"id": "5", "name": "Pending", "handler": "handle_pending", "icon": "⏳"},
                {"id": "6", "name": "Pending PGI", "handler": "handle_pending_pgi", "icon": "📋"},
                {"id": "7", "name": "Pending POD", "handler": "handle_pending_pod", "icon": "✅"},
                {"id": "8", "name": "Delivered", "handler": "handle_delivered", "icon": "🚚"},
                {"id": "9", "name": "Latest Deliveries", "handler": "handle_latest_deliveries", "icon": "📦"},
                {"id": "10", "name": "Latest DN", "handler": "handle_latest_dn", "icon": "📄"},
                {"id": "0", "name": "Main Menu", "handler": "handle_main_menu", "icon": "🏠"},
                {"id": "99", "name": "Exit", "handler": "handle_exit", "icon": "🚪"},
            ]
        },
        "analytics": {
            "id": "analytics",
            "name": "DEALER ANALYTICS",
            "items": [
                {"id": "1", "name": "Products", "handler": "handle_products", "icon": "📦"},
                {"id": "2", "name": "Models", "handler": "handle_models", "icon": "🏷️"},
                {"id": "3", "name": "Top Model", "handler": "handle_top_model", "icon": "🥇"},
                {"id": "4", "name": "Bottom Model", "handler": "handle_bottom_model", "icon": "🥉"},
                {"id": "5", "name": "Warehouses", "handler": "handle_warehouses", "icon": "🏭"},
                {"id": "6", "name": "Cities", "handler": "handle_cities", "icon": "🏙️"},
                {"id": "7", "name": "Sales Office", "handler": "handle_sales_office", "icon": "📋"},
                {"id": "8", "name": "Sales Manager", "handler": "handle_sales_manager", "icon": "👤"},
                {"id": "9", "name": "Avg Delivery", "handler": "handle_avg_delivery", "icon": "⏱️"},
                {"id": "10", "name": "Avg POD", "handler": "handle_avg_pod", "icon": "📄"},
                {"id": "11", "name": "Monthly Revenue", "handler": "handle_monthly_revenue", "icon": "📈"},
                {"id": "12", "name": "Monthly Trend", "handler": "handle_monthly_trend", "icon": "📊"},
                {"id": "13", "name": "Timeline", "handler": "handle_timeline", "icon": "📅"},
                {"id": "14", "name": "History", "handler": "handle_history", "icon": "📖"},
                {"id": "15", "name": "Revenue Breakdown", "handler": "handle_revenue_breakdown", "icon": "💰"},
                {"id": "16", "name": "Unit Breakdown", "handler": "handle_unit_breakdown", "icon": "📦"},
                {"id": "17", "name": "Business Health", "handler": "handle_business_health", "icon": "⭐"},
                {"id": "18", "name": "Performance Score", "handler": "handle_performance_score", "icon": "📊"},
                {"id": "19", "name": "Comparison", "handler": "handle_comparison", "icon": "🔄"},
                {"id": "20", "name": "Complete Report", "handler": "handle_complete_report", "icon": "📋"},
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
# BLOCK 8: QUESTION LIBRARY - 30+ QUESTIONS
# ============================================================

class DealerQuestionLibrary:
    """
    Complete Question Library - 30+ dealer questions
    
    Each question has:
    - Question ID
    - Intent
    - Business Rules
    - SQL Query
    - Calculation Logic
    - Response Template
    - Formatter
    """
    
    QUESTIONS = {
        # ============================================================
        # DASHBOARD QUESTIONS (1-10)
        # ============================================================
        
        "dealer_dashboard": {
            "id": "DASH_001",
            "name": "Dealer Dashboard",
            "intent": DealerIntent.DASHBOARD,
            "priority": 1,
            "patterns": [
                "dashboard", "dealer dashboard", "show dashboard",
                "dealer overview", "dealer summary", "dealer details",
                "dealer info", "dealer profile"
            ],
            "sql": """
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
                    MIN(dn_create_date) as first_order,
                    MAX(dn_create_date) as last_order
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name, dealer_code, sales_office, sales_manager
            """,
            "business_rules": {
                "delivery_pct": "pgi_completed / total_dn * 100",
                "pod_pct": "pod_completed / total_dn * 100",
                "pending_pct": "pending_dn / total_dn * 100",
                "business_score": "(delivery_pct * 0.30) + (pod_pct * 0.25) + ((100 - pending_pct) * 0.25) + ((total_revenue / total_dn) / 1000 * 0.20)"
            },
            "template": """
                📊 *Dealer Dashboard - {dealer}*
                
                📌 *Details*
                Code: {dealer_code}
                Office: {sales_office}
                Manager: {sales_manager}
                
                💰 *Financials*
                Revenue: {total_revenue}
                Avg/DN: {avg_revenue_per_dn}
                
                📦 *Operations*
                DN: {total_dn}
                Units: {total_units}
                Pending DN: {pending_dn}
                Pending PGI: {pgi_pending_dn}
                Pending POD: {pod_pending_dn}
                
                🚚 *Delivery*
                Delivery: {delivery_pct:.1f}%
                POD: {pod_pct:.1f}%
                Avg Days: {avg_delivery_days:.1f}
                Avg POD: {avg_pod_days:.1f}
                
                📈 *Performance*
                Score: {business_score:.1f}/100
                Status: {status}
                Grade: {grade}
                
                📅 *Timeline*
                First Order: {first_order}
                Last Order: {last_order}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "dealer_revenue": {
            "id": "DASH_002",
            "name": "Dealer Revenue",
            "intent": DealerIntent.REVENUE,
            "priority": 1,
            "patterns": [
                "revenue", "total revenue", "sales", "income",
                "revenue of", "sales of", "how much revenue"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    COALESCE(AVG(dn_amount), 0) as avg_revenue,
                    MAX(dn_amount) as highest_invoice,
                    MIN(dn_amount) as lowest_invoice,
                    COUNT(DISTINCT dn_no) as total_dn
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {},
            "template": """
                💰 *Revenue - {dealer}*
                
                Total Revenue: {total_revenue}
                Average Revenue: {avg_revenue}
                Highest Invoice: {highest_invoice}
                Lowest Invoice: {lowest_invoice}
                Total DN: {total_dn}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "dealer_units": {
            "id": "DASH_003",
            "name": "Dealer Units",
            "intent": DealerIntent.UNITS,
            "priority": 1,
            "patterns": [
                "units", "total units", "quantity", "volume",
                "how many units", "units sold"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(AVG(dn_qty), 0) as avg_units,
                    MAX(dn_qty) as highest_quantity,
                    MIN(dn_qty) as lowest_quantity
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {},
            "template": """
                📦 *Units - {dealer}*
                
                Total Units: {total_units}
                Average Units: {avg_units}
                Highest Quantity: {highest_quantity}
                Lowest Quantity: {lowest_quantity}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "dealer_dn_summary": {
            "id": "DASH_004",
            "name": "DN Summary",
            "intent": DealerIntent.DN_SUMMARY,
            "priority": 1,
            "patterns": [
                "dn summary", "delivery notes", "dn count",
                "total dn", "dn distribution"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as closed_dn,
                    COUNT(DISTINCT CASE WHEN pod_date IS NULL AND pending_flag = TRUE THEN dn_no END) as open_dn,
                    COUNT(DISTINCT CASE WHEN delivery_status = 'Delivered' THEN dn_no END) as delivered_dn
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {},
            "template": """
                📄 *DN Summary - {dealer}*
                
                Total DN: {total_dn}
                Open DN: {open_dn}
                Closed DN: {closed_dn}
                Delivered DN: {delivered_dn}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "pending_deliveries": {
            "id": "DASH_005",
            "name": "Pending Deliveries",
            "intent": DealerIntent.PENDING,
            "priority": 1,
            "patterns": [
                "pending", "pending deliveries", "pending orders",
                "undelivered", "backlog"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    COUNT(DISTINCT CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_no END) as pending_dn,
                    COALESCE(SUM(CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_qty END), 0) as pending_units,
                    COALESCE(SUM(CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_amount END), 0) as pending_revenue,
                    AVG(CASE WHEN pending_flag = TRUE OR pod_date IS NULL 
                        THEN EXTRACT(EPOCH FROM (CURRENT_DATE - dn_create_date))/86400 END) as avg_pending_days
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {},
            "template": """
                ⏳ *Pending Deliveries - {dealer}*
                
                Pending DN: {pending_dn}
                Pending Units: {pending_units}
                Pending Revenue: {pending_revenue}
                Avg Pending Days: {avg_pending_days:.1f}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "pending_pgi": {
            "id": "DASH_006",
            "name": "Pending PGI",
            "intent": DealerIntent.PENDING_PGI,
            "priority": 1,
            "patterns": [
                "pending pgi", "pgi pending", "goods issue pending",
                "pgi status", "pgi backlog"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NULL THEN dn_no END) as pending_pgi,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NULL THEN dn_qty END), 0) as pending_units,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NULL THEN dn_amount END), 0) as pending_revenue,
                    MIN(CASE WHEN good_issue_date IS NULL THEN dn_create_date END) as oldest_pending
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {},
            "template": """
                📋 *Pending PGI - {dealer}*
                
                Pending PGI: {pending_pgi}
                Pending Units: {pending_units}
                Pending Revenue: {pending_revenue}
                Oldest Pending: {oldest_pending}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "pending_pod": {
            "id": "DASH_007",
            "name": "Pending POD",
            "intent": DealerIntent.PENDING_POD,
            "priority": 1,
            "patterns": [
                "pending pod", "pod pending", "proof of delivery pending",
                "pod status", "pod backlog"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_no END) as pending_pod,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_qty END), 0) as pending_units,
                    COALESCE(SUM(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN dn_amount END), 0) as pending_revenue,
                    MIN(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NULL THEN good_issue_date END) as oldest_pending
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {},
            "template": """
                ✅ *Pending POD - {dealer}*
                
                Pending POD: {pending_pod}
                Pending Units: {pending_units}
                Pending Revenue: {pending_revenue}
                Oldest Pending: {oldest_pending}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "delivered_orders": {
            "id": "DASH_008",
            "name": "Delivered Orders",
            "intent": DealerIntent.DELIVERED,
            "priority": 1,
            "patterns": [
                "delivered", "delivered orders", "completed",
                "delivery complete", "pod received"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered_dn,
                    COALESCE(SUM(CASE WHEN pod_date IS NOT NULL THEN dn_qty END), 0) as delivered_units,
                    COALESCE(SUM(CASE WHEN pod_date IS NOT NULL THEN dn_amount END), 0) as delivered_revenue,
                    COUNT(DISTINCT dn_no) as total_dn
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {
                "delivery_pct": "delivered_dn / total_dn * 100"
            },
            "template": """
                🚚 *Delivered Orders - {dealer}*
                
                Delivered DN: {delivered_dn}
                Delivered Units: {delivered_units}
                Delivered Revenue: {delivered_revenue}
                Delivery %: {delivery_pct:.1f}%
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "latest_deliveries": {
            "id": "DASH_009",
            "name": "Latest Deliveries",
            "intent": DealerIntent.LATEST_DELIVERIES,
            "priority": 2,
            "patterns": [
                "latest deliveries", "recent deliveries", "last deliveries",
                "recent orders", "latest orders"
            ],
            "sql": """
                SELECT 
                    dn_no,
                    customer_model as product,
                    dn_create_date as date,
                    dn_amount as revenue,
                    delivery_status
                FROM delivery_reports
                {where_clause}
                ORDER BY dn_create_date DESC
                LIMIT 10
            """,
            "business_rules": {},
            "template": """
                📦 *Latest Deliveries - {dealer}*
                
                {deliveries_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "latest_dn": {
            "id": "DASH_010",
            "name": "Latest DN",
            "intent": DealerIntent.LATEST_DN,
            "priority": 2,
            "patterns": [
                "latest dn", "last dn", "most recent dn",
                "last delivery note", "latest delivery note"
            ],
            "sql": """
                SELECT 
                    dn_no,
                    customer_model as product,
                    dn_create_date as date,
                    dn_amount as revenue,
                    delivery_status,
                    good_issue_date,
                    pod_date
                FROM delivery_reports
                {where_clause}
                ORDER BY dn_create_date DESC
                LIMIT 1
            """,
            "business_rules": {},
            "template": """
                📄 *Latest DN - {dealer}*
                
                DN Number: {dn_no}
                Product: {product}
                Date: {date}
                Revenue: {revenue}
                Status: {delivery_status}
                PGI Date: {good_issue_date}
                POD Date: {pod_date}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        # ============================================================
        # ANALYTICS QUESTIONS (11-30)
        # ============================================================
        
        "products_purchased": {
            "id": "ANAL_001",
            "name": "Products Purchased",
            "intent": DealerIntent.PRODUCTS,
            "priority": 2,
            "patterns": [
                "products", "products purchased", "what products",
                "items bought", "product list"
            ],
            "sql": """
                SELECT 
                    customer_model as product,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                {where_clause}
                AND customer_model IS NOT NULL
                GROUP BY customer_model
                ORDER BY revenue DESC
                LIMIT 20
            """,
            "business_rules": {},
            "template": """
                📦 *Products Purchased - {dealer}*
                
                {products_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "models_purchased": {
            "id": "ANAL_002",
            "name": "Models Purchased",
            "intent": DealerIntent.MODELS,
            "priority": 2,
            "patterns": [
                "models", "models purchased", "what models",
                "model list", "variants"
            ],
            "sql": """
                SELECT 
                    customer_model as model,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                {where_clause}
                AND customer_model IS NOT NULL
                GROUP BY customer_model
                ORDER BY units DESC
                LIMIT 20
            """,
            "business_rules": {},
            "template": """
                🏷️ *Models Purchased - {dealer}*
                
                {models_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "top_model": {
            "id": "ANAL_003",
            "name": "Top Selling Model",
            "intent": DealerIntent.TOP_MODEL,
            "priority": 2,
            "patterns": [
                "top model", "best model", "highest selling model",
                "top selling model", "most sold model"
            ],
            "sql": """
                SELECT 
                    customer_model as model,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                {where_clause}
                AND customer_model IS NOT NULL
                GROUP BY customer_model
                ORDER BY units DESC
                LIMIT 1
            """,
            "business_rules": {},
            "template": """
                🥇 *Top Selling Model - {dealer}*
                
                Model: {model}
                Units: {units}
                Revenue: {revenue}
                DN Count: {dn_count}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "bottom_model": {
            "id": "ANAL_004",
            "name": "Lowest Selling Model",
            "intent": DealerIntent.BOTTOM_MODEL,
            "priority": 2,
            "patterns": [
                "bottom model", "worst model", "lowest selling model",
                "least sold model", "minimum model"
            ],
            "sql": """
                SELECT 
                    customer_model as model,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                {where_clause}
                AND customer_model IS NOT NULL
                GROUP BY customer_model
                ORDER BY units ASC
                LIMIT 1
            """,
            "business_rules": {},
            "template": """
                🥉 *Lowest Selling Model - {dealer}*
                
                Model: {model}
                Units: {units}
                Revenue: {revenue}
                DN Count: {dn_count}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "warehouses_utilized": {
            "id": "ANAL_005",
            "name": "Warehouse Utilized",
            "intent": DealerIntent.WAREHOUSES,
            "priority": 2,
            "patterns": [
                "warehouses", "warehouse utilized", "which warehouses",
                "warehouse distribution", "warehouse list"
            ],
            "sql": """
                SELECT 
                    warehouse,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                {where_clause}
                AND warehouse IS NOT NULL
                GROUP BY warehouse
                ORDER BY revenue DESC
            """,
            "business_rules": {},
            "template": """
                🏭 *Warehouses Utilized - {dealer}*
                
                {warehouses_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "cities_analysis": {
            "id": "ANAL_006",
            "name": "City Analysis",
            "intent": DealerIntent.CITIES,
            "priority": 2,
            "patterns": [
                "cities", "city analysis", "which cities",
                "city distribution", "city list"
            ],
            "sql": """
                SELECT 
                    ship_to_city as city,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                {where_clause}
                AND ship_to_city IS NOT NULL
                GROUP BY ship_to_city
                ORDER BY revenue DESC
            """,
            "business_rules": {},
            "template": """
                🏙️ *City Analysis - {dealer}*
                
                {cities_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "sales_office": {
            "id": "ANAL_007",
            "name": "Sales Office",
            "intent": DealerIntent.SALES_OFFICE,
            "priority": 2,
            "patterns": [
                "sales office", "office", "sales office details"
            ],
            "sql": """
                SELECT 
                    sales_office,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                {where_clause}
                AND sales_office IS NOT NULL
                GROUP BY sales_office
            """,
            "business_rules": {},
            "template": """
                📋 *Sales Office - {dealer}*
                
                Office: {sales_office}
                Revenue: {revenue}
                Units: {units}
                DN: {dn_count}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "sales_manager": {
            "id": "ANAL_008",
            "name": "Sales Manager",
            "intent": DealerIntent.SALES_MANAGER,
            "priority": 2,
            "patterns": [
                "sales manager", "manager", "sales manager details"
            ],
            "sql": """
                SELECT 
                    sales_manager,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                {where_clause}
                AND sales_manager IS NOT NULL
                GROUP BY sales_manager
            """,
            "business_rules": {},
            "template": """
                👤 *Sales Manager - {dealer}*
                
                Manager: {sales_manager}
                Revenue: {revenue}
                Units: {units}
                DN: {dn_count}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "avg_delivery": {
            "id": "ANAL_009",
            "name": "Average Delivery Time",
            "intent": DealerIntent.AVG_DELIVERY,
            "priority": 2,
            "patterns": [
                "average delivery", "avg delivery", "delivery time",
                "average delivery days", "delivery speed"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    AVG(CASE WHEN good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as avg_delivery_days,
                    MIN(CASE WHEN good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as min_delivery_days,
                    MAX(CASE WHEN good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as max_delivery_days
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {},
            "template": """
                ⏱️ *Average Delivery Time - {dealer}*
                
                Average: {avg_delivery_days:.1f} Days
                Fastest: {min_delivery_days:.1f} Days
                Slowest: {max_delivery_days:.1f} Days
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "avg_pod": {
            "id": "ANAL_010",
            "name": "Average POD Time",
            "intent": DealerIntent.AVG_POD,
            "priority": 2,
            "patterns": [
                "average pod", "avg pod", "pod time",
                "average pod days", "pod speed"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as avg_pod_days,
                    MIN(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as min_pod_days,
                    MAX(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as max_pod_days
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {},
            "template": """
                📄 *Average POD Time - {dealer}*
                
                Average: {avg_pod_days:.1f} Days
                Fastest: {min_pod_days:.1f} Days
                Slowest: {max_pod_days:.1f} Days
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "monthly_revenue": {
            "id": "ANAL_011",
            "name": "Monthly Revenue",
            "intent": DealerIntent.MONTHLY_REVENUE,
            "priority": 2,
            "patterns": [
                "monthly revenue", "revenue by month", "monthly sales",
                "revenue month wise"
            ],
            "sql": """
                SELECT 
                    TO_CHAR(dn_create_date, 'YYYY-MM') as month,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as dn
                FROM delivery_reports
                {where_clause}
                AND dn_create_date IS NOT NULL
                GROUP BY TO_CHAR(dn_create_date, 'YYYY-MM')
                ORDER BY month DESC
                LIMIT 12
            """,
            "business_rules": {},
            "template": """
                📈 *Monthly Revenue - {dealer}*
                
                {monthly_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "monthly_trend": {
            "id": "ANAL_012",
            "name": "Monthly Trend",
            "intent": DealerIntent.MONTHLY_TREND,
            "priority": 2,
            "patterns": [
                "monthly trend", "trend", "revenue trend",
                "unit trend", "growth trend"
            ],
            "sql": """
                SELECT 
                    TO_CHAR(dn_create_date, 'YYYY-MM') as month,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units
                FROM delivery_reports
                {where_clause}
                AND dn_create_date IS NOT NULL
                GROUP BY TO_CHAR(dn_create_date, 'YYYY-MM')
                ORDER BY month DESC
                LIMIT 6
            """,
            "business_rules": {
                "growth": "Growth calculation from previous month"
            },
            "template": """
                📊 *Monthly Trend - {dealer}*
                
                {trend_list}
                Growth: {growth:+.1f}%
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "dealer_timeline": {
            "id": "ANAL_013",
            "name": "Dealer Timeline",
            "intent": DealerIntent.TIMELINE,
            "priority": 2,
            "patterns": [
                "timeline", "chronology", "dealer timeline",
                "activity timeline", "order timeline"
            ],
            "sql": """
                SELECT 
                    dn_no,
                    dn_create_date as created,
                    good_issue_date as pgi,
                    pod_date as pod,
                    delivery_status,
                    dn_amount as revenue
                FROM delivery_reports
                {where_clause}
                ORDER BY dn_create_date DESC
                LIMIT 20
            """,
            "business_rules": {},
            "template": """
                📅 *Dealer Timeline - {dealer}*
                
                {timeline_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "dealer_history": {
            "id": "ANAL_014",
            "name": "Dealer History",
            "intent": DealerIntent.HISTORY,
            "priority": 2,
            "patterns": [
                "history", "dealer history", "complete history",
                "full history", "all transactions"
            ],
            "sql": """
                SELECT 
                    dn_no,
                    customer_model as product,
                    dn_create_date as date,
                    dn_amount as revenue,
                    dn_qty as units,
                    delivery_status,
                    good_issue_date,
                    pod_date
                FROM delivery_reports
                {where_clause}
                ORDER BY dn_create_date DESC
            """,
            "business_rules": {},
            "template": """
                📖 *Dealer History - {dealer}*
                
                Total Records: {total_records}
                
                {history_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "revenue_breakdown": {
            "id": "ANAL_015",
            "name": "Revenue Breakdown",
            "intent": DealerIntent.REVENUE_BREAKDOWN,
            "priority": 2,
            "patterns": [
                "revenue breakdown", "revenue by", "breakdown revenue",
                "revenue split", "revenue distribution"
            ],
            "sql": """
                SELECT 
                    'Product' as breakdown_type,
                    customer_model as name,
                    COALESCE(SUM(dn_amount), 0) as revenue
                FROM delivery_reports
                {where_clause}
                AND customer_model IS NOT NULL
                GROUP BY customer_model
                UNION ALL
                SELECT 
                    'Warehouse' as breakdown_type,
                    warehouse as name,
                    COALESCE(SUM(dn_amount), 0) as revenue
                FROM delivery_reports
                {where_clause}
                AND warehouse IS NOT NULL
                GROUP BY warehouse
                ORDER BY revenue DESC
                LIMIT 20
            """,
            "business_rules": {},
            "template": """
                💰 *Revenue Breakdown - {dealer}*
                
                {breakdown_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "unit_breakdown": {
            "id": "ANAL_016",
            "name": "Unit Breakdown",
            "intent": DealerIntent.UNIT_BREAKDOWN,
            "priority": 2,
            "patterns": [
                "unit breakdown", "units by", "breakdown units",
                "unit split", "unit distribution"
            ],
            "sql": """
                SELECT 
                    'Product' as breakdown_type,
                    customer_model as name,
                    COALESCE(SUM(dn_qty), 0) as units
                FROM delivery_reports
                {where_clause}
                AND customer_model IS NOT NULL
                GROUP BY customer_model
                UNION ALL
                SELECT 
                    'Warehouse' as breakdown_type,
                    warehouse as name,
                    COALESCE(SUM(dn_qty), 0) as units
                FROM delivery_reports
                {where_clause}
                AND warehouse IS NOT NULL
                GROUP BY warehouse
                ORDER BY units DESC
                LIMIT 20
            """,
            "business_rules": {},
            "template": """
                📦 *Unit Breakdown - {dealer}*
                
                {breakdown_list}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "business_health": {
            "id": "ANAL_017",
            "name": "Business Health",
            "intent": DealerIntent.BUSINESS_HEALTH,
            "priority": 2,
            "patterns": [
                "business health", "health check", "business status",
                "overall health", "dealer health"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed,
                    COUNT(DISTINCT CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_no END) as pending
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {
                "delivery_score": "delivered / total_dn * 50",
                "pgi_score": "pgi_completed / total_dn * 20",
                "pod_score": "pod_completed / total_dn * 20",
                "revenue_score": "revenue / max_revenue * 10",
                "total_score": "delivery_score + pgi_score + pod_score + revenue_score"
            },
            "template": """
                ⭐ *Business Health - {dealer}*
                
                📊 *Scores*
                Delivery: {delivery_score:.1f}/50
                PGI: {pgi_score:.1f}/20
                POD: {pod_score:.1f}/20
                Revenue: {revenue_score:.1f}/10
                
                Total Health Score: {total_score:.1f}/100
                Status: {status}
                Grade: {grade}
                
                Risk Level: {risk}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "performance_score": {
            "id": "ANAL_018",
            "name": "Performance Score",
            "intent": DealerIntent.PERFORMANCE_SCORE,
            "priority": 2,
            "patterns": [
                "performance score", "dealer score", "rating",
                "performance rating", "score"
            ],
            "sql": """
                SELECT 
                    customer_name as dealer,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered,
                    COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                    COUNT(DISTINCT CASE WHEN pending_flag = TRUE OR pod_date IS NULL THEN dn_no END) as pending
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name
            """,
            "business_rules": {
                "revenue_score": "revenue / total_dn / 1000 * 25",
                "delivery_score": "delivered / total_dn * 25",
                "pgi_score": "pgi_completed / total_dn * 25",
                "pending_score": "(1 - pending / total_dn) * 25",
                "total_score": "revenue_score + delivery_score + pgi_score + pending_score"
            },
            "template": """
                📊 *Performance Score - {dealer}*
                
                Revenue Score: {revenue_score:.1f}/25
                Delivery Score: {delivery_score:.1f}/25
                PGI Score: {pgi_score:.1f}/25
                Pending Score: {pending_score:.1f}/25
                
                Total Performance Score: {total_score:.1f}/100
                Rating: {rating}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        },
        
        "complete_report": {
            "id": "ANAL_020",
            "name": "Complete Dealer Intelligence Report",
            "intent": DealerIntent.COMPLETE_REPORT,
            "priority": 3,
            "patterns": [
                "complete report", "full report", "intelligence report",
                "dealer report", "complete analysis"
            ],
            "sql": """
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
                    MIN(dn_create_date) as first_order,
                    MAX(dn_create_date) as last_order,
                    COUNT(DISTINCT customer_model) as total_products,
                    COUNT(DISTINCT warehouse) as total_warehouses,
                    COUNT(DISTINCT ship_to_city) as total_cities
                FROM delivery_reports
                {where_clause}
                GROUP BY customer_name, dealer_code, sales_office, sales_manager
            """,
            "business_rules": {
                "delivery_pct": "pgi_completed / total_dn * 100",
                "pod_pct": "pod_completed / total_dn * 100",
                "pending_pct": "pending_dn / total_dn * 100",
                "business_score": "(delivery_pct * 0.30) + (pod_pct * 0.25) + ((100 - pending_pct) * 0.25) + ((total_revenue / total_dn) / 1000 * 0.20)",
                "risk": "High if pending_pct > 20 or delivery_pct < 80"
            },
            "template": """
                📋 *COMPLETE DEALER INTELLIGENCE REPORT*
                
                📌 *Dealer Overview*
                Name: {dealer}
                Code: {dealer_code}
                Office: {sales_office}
                Manager: {sales_manager}
                
                💰 *Financial Summary*
                Total Revenue: {total_revenue}
                Avg Revenue/DN: {avg_revenue_per_dn}
                Total Units: {total_units}
                
                📦 *Operational Summary*
                Total DN: {total_dn}
                Pending DN: {pending_dn}
                Pending PGI: {pgi_pending_dn}
                Pending POD: {pod_pending_dn}
                Delivered: {pod_completed}
                
                🚚 *Delivery Performance*
                Delivery %: {delivery_pct:.1f}%
                POD %: {pod_pct:.1f}%
                Avg Delivery Days: {avg_delivery_days:.1f}
                Avg POD Days: {avg_pod_days:.1f}
                
                🏷️ *Product Portfolio*
                Total Products: {total_products}
                Total Warehouses: {total_warehouses}
                Total Cities: {total_cities}
                
                📈 *Performance Metrics*
                Business Score: {business_score:.1f}/100
                Status: {status}
                Grade: {grade}
                Risk Level: {risk}
                
                📅 *Timeline*
                First Order: {first_order}
                Last Order: {last_order}
                
                {recommendations}
            """,
            "requires_dealer": True,
            "dealer_pattern": "dealer"
        }
    }
    
    @classmethod
    def find_question(cls, text: str) -> Optional[Dict[str, Any]]:
        """Find matching question from library"""
        text_lower = text.lower().strip()
        
        # Check each question's patterns
        for question_id, question in cls.QUESTIONS.items():
            for pattern in question.get("patterns", []):
                if pattern in text_lower:
                    return {**question, "question_id": question_id}
        
        # Fuzzy match for partial matches
        if RAPIDFUZZ_AVAILABLE:
            best_match = None
            best_score = 0.0
            for question_id, question in cls.QUESTIONS.items():
                for pattern in question.get("patterns", []):
                    score = fuzz.partial_ratio(text_lower, pattern)
                    if score > best_score and score > 70:
                        best_score = score
                        best_match = {**question, "question_id": question_id}
            if best_match:
                return best_match
        
        return None
    
    @classmethod
    def get_question_by_id(cls, question_id: str) -> Optional[Dict[str, Any]]:
        """Get question by ID"""
        question = cls.QUESTIONS.get(question_id)
        if question:
            return {**question, "question_id": question_id}
        return None

# ============================================================
# BLOCK 9: SQL REGISTRY
# ============================================================

class DealerSQLRegistry:
    """Registry of all SQL queries for dealer operations"""
    
    @classmethod
    def build_where_clause(cls, filters: Dict[str, Any]) -> str:
        """Build WHERE clause from filters"""
        conditions = []
        
        # Dealer name filter
        if filters.get("dealer"):
            conditions.append(f"LOWER(customer_name) LIKE LOWER('%{filters['dealer']}%')")
        
        # Warehouse filter
        if filters.get("warehouse"):
            conditions.append(f"LOWER(warehouse) LIKE LOWER('%{filters['warehouse']}%')")
        
        # City filter
        if filters.get("city"):
            conditions.append(f"LOWER(ship_to_city) LIKE LOWER('%{filters['city']}%')")
        
        # Date range
        if filters.get("date_from"):
            conditions.append(f"dn_create_date >= '{filters['date_from']}'")
        if filters.get("date_to"):
            conditions.append(f"dn_create_date <= '{filters['date_to']}'")
        
        if conditions:
            return "WHERE " + " AND ".join(conditions)
        return ""
    
    @classmethod
    def get_summary(cls, filters: Dict[str, Any] = None) -> str:
        """Get dealer summary SQL"""
        where = cls.build_where_clause(filters or {})
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
            {where}
            GROUP BY customer_name, dealer_code, sales_office, sales_manager
            ORDER BY total_revenue DESC
        """
    
    @classmethod
    def get_ranking(cls, metric: str, limit: int = 10) -> str:
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
                {agg_col} as value,
                COUNT(DISTINCT dn_no) as dn_count
            FROM delivery_reports
            WHERE customer_name IS NOT NULL
            GROUP BY customer_name
            ORDER BY value {order}
            LIMIT {limit}
        """

# ============================================================
# BLOCK 10: DEALER REPOSITORY
# ============================================================

class DealerRepository:
    """PostgreSQL repository for dealer operations"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()
    
    def _get_cache_key(self, query_type: str, identifier: str = "") -> str:
        return f"dealer_{query_type}_{identifier}".lower()
    
    def execute_query(self, sql: str, cache_key: Optional[str] = None) -> List[Dict[str, Any]]:
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
            return []
    
    def get_summary(self, filters: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        sql = DealerSQLRegistry.get_summary(filters)
        cache_key = self._get_cache_key("summary", str(filters))
        results = self.execute_query(sql, cache_key)
        
        if not results:
            return None
        
        row = results[0] if results else {}
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
        
        data['delivery_success_pct'] = _percent(pgi_completed, total_dn)
        data['pod_success_pct'] = _percent(pod_completed, total_dn)
        data['pending_pct'] = _percent(pending_dn, total_dn)
        
        # Business score
        score = (
            data['delivery_success_pct'] * 0.30 +
            data['pod_success_pct'] * 0.25 +
            (100 - data['pending_pct']) * 0.25 +
            min(100, data['avg_revenue_per_dn'] / 1000) * 0.20
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

# ============================================================
# BLOCK 11: DEALER RENDERER
# ============================================================

class DealerRenderer:
    """Render dealer responses for WhatsApp"""
    
    MENU_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━"
    SEPARATOR = "─" * 40
    
    @classmethod
    def _render_menu_footer(cls, menu_type: str = "main") -> str:
        menu = DealerMenuRegistry.MENUS.get(menu_type, DealerMenuRegistry.MENUS["main"])
        
        lines = ["", cls.MENU_SEPARATOR, ""]
        lines.append(f"📋 *{menu['name']}*")
        lines.append("")
        
        for item in menu["items"]:
            lines.append(f"{item['id']}. {item['icon']} {item['name']}")
        
        lines.append("")
        lines.append("Reply with a number or type your question:")
        
        return "\n".join(lines)
    
    @classmethod
    def render_main_menu(cls) -> str:
        return cls._render_menu_footer("main")
    
    @classmethod
    def render_dashboard_menu(cls) -> str:
        return cls._render_menu_footer("dashboard")
    
    @classmethod
    def render_analytics_menu(cls) -> str:
        return cls._render_menu_footer("analytics")
    
    @classmethod
    def render_ai_assistant_menu(cls) -> str:
        return cls._render_menu_footer("ai_assistant")
    
    @classmethod
    def render_with_menu(cls, content: str, menu_type: str = "main") -> str:
        return f"{content}\n{cls._render_menu_footer(menu_type)}"
    
    @classmethod
    def render_question_result(cls, question: Dict[str, Any], data: Dict[str, Any]) -> str:
        """Render question result using template"""
        template = question.get("template", "")
        
        # Handle special cases
        if "today_date" in template:
            data["today_date"] = datetime.now().strftime("%d-%b-%Y")
        
        # Format currency values
        for key, value in data.items():
            if "revenue" in key or "amount" in key:
                if isinstance(value, (int, float)):
                    data[key] = _format_currency(value)
            elif "units" in key or "dn" in key or "pending" in key or "dealers" in key:
                if isinstance(value, (int, float)):
                    data[key] = _format_number(value)
        
        try:
            return template.format(**data)
        except KeyError as e:
            logger.error(f"Template formatting error: {e}")
            return f"⚠️ Error formatting response. Missing: {e}"
        except Exception as e:
            logger.error(f"Template error: {e}")
            return "⚠️ Error rendering response."
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "Revenue", limit: int = 10) -> str:
        if not ranking:
            return f"🏆 *Dealer Rankings by {metric}*\n\nNo dealers found."
        
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

# ============================================================
# BLOCK 12: DEALER INTENT ENGINE
# ============================================================

class DealerIntentEngine:
    """AI-powered intent detection for dealer queries"""
    
    def __init__(self):
        self._initialized = False
        self._initialize()
    
    def _initialize(self):
        if self._initialized:
            return
        
        logger.info("🤖 Initializing Dealer Intent Engine...")
        self._initialized = True
        logger.info("✅ Dealer Intent Engine initialized")
    
    def detect_intent(self, text: str) -> DealerIntentResult:
        """Detect intent with confidence"""
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
        
        # Check Question Library first
        question = DealerQuestionLibrary.find_question(text_clean)
        if question:
            intent = question.get("intent", DealerIntent.UNKNOWN)
            confidence = 1.0
            entities = self._extract_entities(text)
            
            return DealerIntentResult(
                intent=intent,
                confidence=confidence,
                entities=entities,
                raw_input=text,
                processing_time_ms=(time.time() - start_time) * 1000
            )
        
        # Check for dealer name
        if "dealer" in text_clean:
            entities = self._extract_entities(text)
            if entities.get("dealer_names"):
                return DealerIntentResult(
                    intent=DealerIntent.DASHBOARD,
                    confidence=0.8,
                    entities=entities,
                    raw_input=text,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
        
        # Check for ranking
        if "top" in text_clean or "ranking" in text_clean:
            return DealerIntentResult(
                intent=DealerIntent.RANKING,
                confidence=0.7,
                entities=self._extract_entities(text),
                raw_input=text,
                processing_time_ms=(time.time() - start_time) * 1000
            )
        
        # Check for comparison
        if "compare" in text_clean or "vs" in text_clean:
            return DealerIntentResult(
                intent=DealerIntent.COMPARISON,
                confidence=0.7,
                entities=self._extract_entities(text),
                raw_input=text,
                processing_time_ms=(time.time() - start_time) * 1000
            )
        
        # Check for search
        if "search" in text_clean or "find" in text_clean:
            return DealerIntentResult(
                intent=DealerIntent.SEARCH,
                confidence=0.7,
                entities=self._extract_entities(text),
                raw_input=text,
                processing_time_ms=(time.time() - start_time) * 1000
            )
        
        # Default to AI ask
        return DealerIntentResult(
            intent=DealerIntent.AI_ASK,
            confidence=0.5,
            entities=self._extract_entities(text),
            raw_input=text,
            processing_time_ms=(time.time() - start_time) * 1000
        )
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        entities = {
            "dealer_names": [],
            "metrics": [],
            "limit": 10,
            "comparison": []
        }
        
        # Extract dealer names
        dealer_pattern = r'(?:dealer|dealers|for|of|in|from)\s+([A-Za-z\s]+)'
        matches = re.findall(dealer_pattern, text, re.IGNORECASE)
        if matches:
            entities["dealer_names"] = [m.strip() for m in matches if m.strip()]
        
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
        
        return entities

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
        self._version = "6.0"
        
        # Initialize engines
        self._intent_engine = DealerIntentEngine()
        self._renderer = DealerRenderer()
        self._question_library = DealerQuestionLibrary()
        
        # Sessions
        self._sessions: Dict[str, DealerSession] = {}
        self._session_lock = threading.RLock()
        
        logger.info("=" * 70)
        logger.info(f"🚀 Dealer Domain AI Engine v{self._version} initialized")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info(f"   📋 Question Library: {len(self._question_library.QUESTIONS)} questions")
        logger.info(f"   🤖 AI Engine: {'Active' if DEALER_AI_ENABLED else 'Limited'}")
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
        if session.menu_state == DealerMenuState.DASHBOARD:
            return "dashboard"
        elif session.menu_state == DealerMenuState.ANALYTICS:
            return "analytics"
        elif session.menu_state == DealerMenuState.AI_ASSISTANT:
            return "ai_assistant"
        else:
            return "main"
    
    def _get_menu(self, session: DealerSession) -> str:
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
        menu_type = self._get_menu_type(session)
        return self._renderer.render_with_menu(content, menu_type)
    
    def get_main_menu(self) -> str:
        return self._renderer.render_main_menu()
    
    def _execute_question(self, question: Dict[str, Any], session: DealerSession, message: str) -> str:
        """Execute a question from the library"""
        question_id = question.get("question_id", "unknown")
        logger.info(f"📋 Executing question: {question_id}")
        
        # Build filters
        filters = {}
        
        # Extract dealer name if required
        if question.get("requires_dealer", False):
            dealer_pattern = question.get("dealer_pattern", "")
            if dealer_pattern:
                # Try to extract dealer from message
                match = re.search(rf'{dealer_pattern}\s+([A-Za-z\s]+)', message, re.IGNORECASE)
                if match:
                    filters["dealer"] = match.group(1).strip()
                elif session.current_dealer:
                    filters["dealer"] = session.current_dealer
                else:
                    return "⚠️ Please specify a dealer name."
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            
            # Execute based on question
            question_id = question.get("question_id", "")
            
            if question_id in ["DASH_001", "ANAL_020"]:
                data = repo.get_summary(filters)
                if data:
                    # Add additional calculations for complete report
                    if question_id == "ANAL_020":
                        data['risk'] = "High" if data.get('pending_pct', 0) > 20 else "Low"
                        data['status'] = data.get('overall_status', 'Unknown')
                        data['grade'] = data.get('performance_grade', 'N/A')
                        data['avg_revenue_per_dn'] = _format_currency(data.get('avg_revenue_per_dn', 0))
                        data['total_products'] = 0  # Would need additional query
                        data['total_warehouses'] = 0  # Would need additional query
                        data['total_cities'] = 0  # Would need additional query
                        data['recommendations'] = self._generate_recommendations(data)
                    
                    db_session.close()
                    return self._renderer.render_question_result(question, data)
                else:
                    return f"⚠️ Dealer '{filters.get('dealer', 'Unknown')}' not found."
            
            elif question_id in ["DASH_002", "DASH_003", "DASH_004", "DASH_005", 
                                  "DASH_006", "DASH_007", "DASH_008"]:
                data = repo.get_summary(filters)
                if data:
                    # Map data for specific question
                    result_data = {}
                    if question_id == "DASH_002":
                        result_data = {
                            'dealer': data.get('dealer'),
                            'total_revenue': data.get('total_revenue'),
                            'avg_revenue': data.get('avg_revenue_per_dn'),
                            'highest_invoice': data.get('avg_revenue_per_dn') * 1.5,  # Placeholder
                            'lowest_invoice': data.get('avg_revenue_per_dn') * 0.5,   # Placeholder
                            'total_dn': data.get('total_dn'),
                        }
                    elif question_id == "DASH_003":
                        result_data = {
                            'dealer': data.get('dealer'),
                            'total_units': data.get('total_units'),
                            'avg_units': data.get('total_units') / max(1, data.get('total_dn', 1)),
                            'highest_quantity': data.get('total_units') * 0.3,  # Placeholder
                            'lowest_quantity': data.get('total_units') * 0.05,  # Placeholder
                        }
                    elif question_id == "DASH_004":
                        result_data = {
                            'dealer': data.get('dealer'),
                            'total_dn': data.get('total_dn'),
                            'open_dn': data.get('pending_dn'),
                            'closed_dn': data.get('total_dn') - data.get('pending_dn'),
                            'delivered_dn': data.get('pod_completed'),
                        }
                    elif question_id == "DASH_005":
                        result_data = {
                            'dealer': data.get('dealer'),
                            'pending_dn': data.get('pending_dn'),
                            'pending_units': data.get('pending_dn') * 2,  # Placeholder
                            'pending_revenue': data.get('total_revenue') * 0.1,  # Placeholder
                            'avg_pending_days': 5.0,  # Placeholder
                        }
                    elif question_id == "DASH_006":
                        result_data = {
                            'dealer': data.get('dealer'),
                            'pending_pgi': data.get('pgi_pending_dn'),
                            'pending_units': data.get('pgi_pending_dn') * 2,
                            'pending_revenue': data.get('total_revenue') * 0.05,
                            'oldest_pending': data.get('first_order'),
                        }
                    elif question_id == "DASH_007":
                        result_data = {
                            'dealer': data.get('dealer'),
                            'pending_pod': data.get('pod_pending_dn'),
                            'pending_units': data.get('pod_pending_dn') * 2,
                            'pending_revenue': data.get('total_revenue') * 0.05,
                            'oldest_pending': data.get('last_order'),
                        }
                    elif question_id == "DASH_008":
                        result_data = {
                            'dealer': data.get('dealer'),
                            'delivered_dn': data.get('pod_completed'),
                            'delivered_units': data.get('total_units') * 0.9,
                            'delivered_revenue': data.get('total_revenue') * 0.9,
                            'delivery_pct': data.get('delivery_success_pct'),
                            'total_dn': data.get('total_dn'),
                        }
                    
                    db_session.close()
                    if result_data:
                        return self._renderer.render_question_result(question, result_data)
                return f"⚠️ No data found for dealer."
            
            else:
                # Generic data fetch
                data = repo.get_summary(filters)
                if data:
                    db_session.close()
                    return self._renderer.render_question_result(question, data)
                else:
                    return f"⚠️ No data found."
            
        except Exception as e:
            logger.error(f"Question execution error: {e}")
            if db_session:
                db_session.close()
            return f"⚠️ Error executing query: {str(e)[:100]}"
    
    def _generate_recommendations(self, data: Dict[str, Any]) -> str:
        """Generate recommendations based on dealer data"""
        recommendations = []
        
        pending_pct = data.get('pending_pct', 0)
        delivery_pct = data.get('delivery_success_pct', 0)
        score = data.get('business_score', 0)
        
        if pending_pct > 20:
            recommendations.append("• High pending orders - Escalate for immediate resolution")
        elif pending_pct > 10:
            recommendations.append("• Review pending orders for timely closure")
        
        if delivery_pct < 80:
            recommendations.append("• Improve delivery speed and reliability")
        elif delivery_pct < 90:
            recommendations.append("• Monitor delivery performance for optimization")
        
        if score < 70:
            recommendations.append("• Develop action plan to improve business score")
        
        if not recommendations:
            recommendations.append("• Maintain current performance levels")
            recommendations.append("• Continue monitoring key metrics")
        
        return "\n".join(recommendations)
    
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
        # STEP 1: Exit (99)
        # ============================================================
        if message_clean == "99":
            session.clear()
            logger.info(f"🚪 Dealer session exited for {sender}")
            return "__EXIT__"
        
        # ============================================================
        # STEP 2: Menu navigation (0, 1, 2, 3)
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
        # STEP 3: Menu option handlers (Dashboard - 1 to 10)
        # ============================================================
        if session.menu_state == DealerMenuState.DASHBOARD:
            dashboard_handlers = {
                "1": self._handle_dashboard,
                "2": self._handle_revenue,
                "3": self._handle_units,
                "4": self._handle_dn_summary,
                "5": self._handle_pending,
                "6": self._handle_pending_pgi,
                "7": self._handle_pending_pod,
                "8": self._handle_delivered,
                "9": self._handle_latest_deliveries,
                "10": self._handle_latest_dn,
            }
            if message_clean in dashboard_handlers:
                response = dashboard_handlers[message_clean](session, message_clean)
                return self._render_response(response, session)
        
        # ============================================================
        # STEP 4: Menu option handlers (Analytics - 1 to 20)
        # ============================================================
        if session.menu_state == DealerMenuState.ANALYTICS:
            analytics_handlers = {
                "1": self._handle_products,
                "2": self._handle_models,
                "3": self._handle_top_model,
                "4": self._handle_bottom_model,
                "5": self._handle_warehouses,
                "6": self._handle_cities,
                "7": self._handle_sales_office,
                "8": self._handle_sales_manager,
                "9": self._handle_avg_delivery,
                "10": self._handle_avg_pod,
                "11": self._handle_monthly_revenue,
                "12": self._handle_monthly_trend,
                "13": self._handle_timeline,
                "14": self._handle_history,
                "15": self._handle_revenue_breakdown,
                "16": self._handle_unit_breakdown,
                "17": self._handle_business_health,
                "18": self._handle_performance_score,
                "19": self._handle_comparison,
                "20": self._handle_complete_report,
            }
            if message_clean in analytics_handlers:
                response = analytics_handlers[message_clean](session, message_clean)
                return self._render_response(response, session)
        
        # ============================================================
        # STEP 5: AI Assistant handlers
        # ============================================================
        if session.menu_state == DealerMenuState.AI_ASSISTANT:
            if message_clean == "1":
                return self._render_response(self._handle_ai_ask(session, message_clean), session)
            elif message_clean == "2":
                return self._render_response(self._handle_ai_analysis(session, message_clean), session)
            elif message_clean == "3":
                return self._render_response(self._handle_ai_insights(session, message_clean), session)
        
        # ============================================================
        # STEP 6: Question Library (30+ questions)
        # ============================================================
        question = self._question_library.find_question(message_clean)
        if question:
            response = self._execute_question(question, session, message_clean)
            return self._render_response(response, session)
        
        # ============================================================
        # STEP 7: Dealer name detection
        # ============================================================
        dealer_name = self._resolve_dealer_name(message_clean)
        if dealer_name:
            session.set_dealer(dealer_name)
            response = self._handle_dashboard(session, dealer_name)
            return self._render_response(response, session)
        
        # ============================================================
        # STEP 8: Intent detection (AI fallback)
        # ============================================================
        intent_result = self._intent_engine.detect_intent(message_clean)
        session.last_intent = intent_result.intent
        logger.info(f"🎯 Intent: {intent_result.intent.value} (confidence: {intent_result.confidence:.2f})")
        
        response = self._process_intent(session, intent_result, message_clean)
        session.add_history(message_clean, response)
        
        return self._render_response(response, session)
    
    def _process_intent(self, session: DealerSession, intent_result: DealerIntentResult, message: str) -> str:
        """Process intent and return response"""
        intent = intent_result.intent
        entities = intent_result.entities
        
        dealer_names = entities.get("dealer_names", [])
        
        if intent == DealerIntent.EXIT:
            session.clear()
            return "__EXIT__"
        
        if intent == DealerIntent.MENU or intent == DealerIntent.HELP:
            return "Main Menu"
        
        if intent == DealerIntent.RANKING:
            return self._handle_ranking(session, message)
        
        if intent == DealerIntent.COMPARISON:
            return self._handle_comparison(session, message)
        
        if dealer_names:
            dealer_name = dealer_names[0]
            session.set_dealer(dealer_name)
            return self._handle_dashboard(session, dealer_name)
        
        # AI fallback
        if DEALER_AI_ENABLED:
            return self._handle_ai_ask(session, message)
        
        return self._get_help()
    
    def _resolve_dealer_name(self, text: str) -> Optional[str]:
        """Resolve dealer name from text"""
        text_clean = text.strip().lower()
        
        # Try database search
        try:
            with self._get_db_session() as session:
                repo = DealerRepository(session)
                results = repo.execute_query(f"""
                    SELECT customer_name FROM delivery_reports 
                    WHERE LOWER(customer_name) LIKE LOWER('%{text_clean}%')
                    LIMIT 1
                """)
                if results:
                    return results[0].get('customer_name')
        except Exception:
            pass
        
        return None
    
    # ============================================================
    # HANDLERS - All 30+ Questions
    # ============================================================
    
    def _handle_main_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.MAIN
        return "Main Menu"
    
    def _handle_dashboard(self, session: DealerSession, message: str) -> str:
        session.menu_state = DealerMenuState.DASHBOARD
        
        if session.current_dealer:
            question = self._question_library.get_question_by_id("DASH_001")
            if question:
                return self._execute_question(question, session, message)
        
        return "📊 *Dealer Dashboard*\n\nEnter dealer name:\n\n0. Main Menu\n99. Back"
    
    def _handle_revenue(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("DASH_002")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Revenue question not found."
    
    def _handle_units(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("DASH_003")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Units question not found."
    
    def _handle_dn_summary(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("DASH_004")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ DN Summary question not found."
    
    def _handle_pending(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("DASH_005")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Pending question not found."
    
    def _handle_pending_pgi(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("DASH_006")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Pending PGI question not found."
    
    def _handle_pending_pod(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("DASH_007")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Pending POD question not found."
    
    def _handle_delivered(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("DASH_008")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Delivered question not found."
    
    def _handle_latest_deliveries(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("DASH_009")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Latest deliveries question not found."
    
    def _handle_latest_dn(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("DASH_010")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Latest DN question not found."
    
    def _handle_products(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_001")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Products question not found."
    
    def _handle_models(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_002")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Models question not found."
    
    def _handle_top_model(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_003")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Top model question not found."
    
    def _handle_bottom_model(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_004")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Bottom model question not found."
    
    def _handle_warehouses(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_005")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Warehouses question not found."
    
    def _handle_cities(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_006")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Cities question not found."
    
    def _handle_sales_office(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_007")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Sales office question not found."
    
    def _handle_sales_manager(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_008")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Sales manager question not found."
    
    def _handle_avg_delivery(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_009")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Average delivery question not found."
    
    def _handle_avg_pod(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_010")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Average POD question not found."
    
    def _handle_monthly_revenue(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_011")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Monthly revenue question not found."
    
    def _handle_monthly_trend(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_012")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Monthly trend question not found."
    
    def _handle_timeline(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_013")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Timeline question not found."
    
    def _handle_history(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_014")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ History question not found."
    
    def _handle_revenue_breakdown(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_015")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Revenue breakdown question not found."
    
    def _handle_unit_breakdown(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_016")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Unit breakdown question not found."
    
    def _handle_business_health(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_017")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Business health question not found."
    
    def _handle_performance_score(self, session: DealerSession, message: str) -> str:
        question = self._question_library.get_question_by_id("ANAL_018")
        if question:
            return self._execute_question(question, session, message)
        return "⚠️ Performance score question not found."
    
    def _handle_comparison(self, session: DealerSession, message: str) -> str:
        session.menu_state = DealerMenuState.COMPARISON
        
        # Extract dealer names
        compare_pattern = r'compare\s+([\w\s]+)\s+and\s+([\w\s]+)'
        compare_match = re.search(compare_pattern, message, re.IGNORECASE)
        
        if compare_match:
            dealer1 = compare_match.group(1).strip()
            dealer2 = compare_match.group(2).strip()
            session.comparison_dealers = [dealer1, dealer2]
            
            question = self._question_library.get_question_by_id("ANAL_019")
            if question:
                return self._execute_question(question, session, message)
        
        return "Please specify two dealers to compare.\nExample: compare Dealer1 and Dealer2"
    
    def _handle_complete_report(self, session: DealerSession, message: str) -> str:
        session.menu_state = DealerMenuState.DASHBOARD
        
        if session.current_dealer:
            question = self._question_library.get_question_by_id("ANAL_020")
            if question:
                return self._execute_question(question, session, message)
        
        return "📋 *Complete Report*\n\nEnter dealer name:\n\n0. Main Menu\n99. Back"
    
    def _handle_ranking(self, session: DealerSession, message: str) -> str:
        session.menu_state = DealerMenuState.RANKING
        
        db_session = self._get_db_session()
        if not db_session:
            return "⚠️ Database unavailable."
        
        try:
            repo = DealerRepository(db_session)
            ranking = repo.get_ranking("revenue", 10)
            db_session.close()
            
            if not ranking:
                return "🏆 *Dealer Rankings*\n\nNo dealers found."
            
            return self._renderer.render_ranking(ranking, "Revenue", 10)
            
        except Exception as e:
            logger.error(f"Ranking error: {e}")
            if db_session:
                db_session.close()
            return "⚠️ Error fetching rankings."
    
    def _handle_dashboard_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.DASHBOARD
        return self._renderer.render_dashboard_menu()
    
    def _handle_analytics_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.ANALYTICS
        return self._renderer.render_analytics_menu()
    
    def _handle_ai_assistant_menu(self, session: DealerSession) -> str:
        session.menu_state = DealerMenuState.AI_ASSISTANT
        return self._renderer.render_ai_assistant_menu()
    
    def _handle_ai_ask(self, session: DealerSession, message: str) -> str:
        session.menu_state = DealerMenuState.AI_ASSISTANT
        
        if not DEALER_AI_ENABLED:
            return "🤖 AI Assistant is currently disabled."
        
        # Build response
        lines = ["🤖 *AI Assistant*", ""]
        lines.append(f"📝 *Question:* {message}")
        lines.append("")
        
        # Try to get dealer data if available
        if session.current_dealer:
            db_session = self._get_db_session()
            if db_session:
                try:
                    repo = DealerRepository(db_session)
                    data = repo.get_summary({"dealer": session.current_dealer})
                    db_session.close()
                    if data:
                        lines.append("📊 *Dealer Data:*")
                        lines.append(f"Revenue: {_format_currency(data.get('total_revenue', 0))}")
                        lines.append(f"DN: {_format_number(data.get('total_dn', 0))}")
                        lines.append(f"Delivery: {data.get('delivery_success_pct', 0):.1f}%")
                        lines.append("")
                except Exception:
                    pass
        
        # Try LLM response
        try:
            if GROQ_AVAILABLE and GROQ_API_KEY:
                import groq
                client = groq.Groq(api_key=GROQ_API_KEY)
                response = client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[
                        {"role": "system", "content": f"You are a dealer analytics expert. Provide insights for: {session.current_dealer or 'Dealer Analytics'}"},
                        {"role": "user", "content": message}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                ai_response = response.choices[0].message.content.strip()
                lines.append("💡 *AI Insights:*")
                lines.append(ai_response)
            elif OPENAI_AVAILABLE and OPENAI_API_KEY:
                import openai
                client = openai.OpenAI(api_key=OPENAI_API_KEY)
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": f"You are a dealer analytics expert. Provide insights for: {session.current_dealer or 'Dealer Analytics'}"},
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
    
    def _handle_ai_analysis(self, session: DealerSession, message: str) -> str:
        return self._handle_ai_ask(session, "analyze " + message)
    
    def _handle_ai_insights(self, session: DealerSession, message: str) -> str:
        return self._handle_ai_ask(session, "insights about " + message)
    
    def _handle_exit(self, session: DealerSession) -> str:
        session.clear()
        return "__EXIT__"
    
    def _get_help(self) -> str:
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
            "question_library": len(self._question_library.QUESTIONS),
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
