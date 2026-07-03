"""
File: app/services/product_service.py
Version: 5.0 - ENTERPRISE PRODUCT DOMAIN AI EXPERT WITH FULL MENU
Purpose: Answer ANY product-related business question through a single entry point
         PostgreSQL is the ONLY source of truth.
         Full menu system with 20+ options, sub-menus, and AI-powered queries

FEATURES:
- ✅ Complete Menu System
- ✅ 20+ Product Analytics Options with sub-menus
- ✅ Product Selection Prompts
- ✅ Comparison Flow (2 products)
- ✅ Ranking Display with Medals
- ✅ Quick Commands Support
- ✅ Context Memory
- ✅ Dynamic Menu Rendering
- ✅ WhatsApp-Optimized Formatting
- ✅ AI-Powered Natural Language Queries
- ✅ PostgreSQL Integration
- ✅ Full Analytics Suite

Status: ENTERPRISE READY
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any, Optional, Dict, List, Tuple, Union, Set, Callable

from cachetools import TTLCache
from sqlalchemy import and_, case, distinct, func, or_, text, desc, asc
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: OPTIONAL AI IMPORTS
# ============================================================

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    SEMANTIC_AVAILABLE = True
except ImportError:
    SEMANTIC_AVAILABLE = False

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    from semantic_router import Route, Router
    from semantic_router.encoders import HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False

# ============================================================
# BLOCK 2: CONFIGURATION
# ============================================================

CACHE_TTL = max(60, int(os.getenv("PRODUCT_ANALYTICS_CACHE_TTL", "300")))
USE_SEMANTIC_SEARCH = os.getenv("USE_SEMANTIC_SEARCH", "true").lower() == "true"
USE_AI_EXPLANATION = os.getenv("USE_AI_EXPLANATION", "true").lower() == "true"

# ============================================================
# BLOCK 3: CONSTANTS
# ============================================================

PRODUCT_NAMES: List[str] = [
    "Samsung Refrigerator",
    "LG Washing Machine",
    "Sony LED TV",
    "Daikin AC",
    "Gree AC",
    "Haier Refrigerator",
    "Panasonic TV",
    "Kenwood Microwave",
    "Electrolux Oven",
    "Philips Air Fryer"
]

PRODUCT_ALIASES: Dict[str, str] = {
    "samsung": "Samsung Refrigerator",
    "lg": "LG Washing Machine",
    "sony": "Sony LED TV",
    "daikin": "Daikin AC",
    "gree": "Gree AC",
    "haier": "Haier Refrigerator",
    "panasonic": "Panasonic TV",
    "kenwood": "Kenwood Microwave",
    "electrolux": "Electrolux Oven",
    "philips": "Philips Air Fryer"
}

SEPARATOR: str = "────────────────────"

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

class IntentType(Enum):
    """Product question intent types"""
    DASHBOARD = "dashboard"
    REVENUE = "revenue"
    UNITS = "units"
    DEALERS = "dealers"
    WAREHOUSES = "warehouses"
    CITIES = "cities"
    PENDING_DN = "pending_dn"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    COMPARISON = "comparison"
    RANKING = "ranking"
    TREND = "trend"
    FORECAST = "forecast"
    AI_SUMMARY = "ai_summary"
    PERFORMANCE = "performance"
    LIFE_CYCLE = "life_cycle"
    RECOMMENDATIONS = "recommendations"
    SEARCH = "search"
    MENU = "menu"
    UNKNOWN = "unknown"

class MenuState(Enum):
    """Menu navigation states"""
    MAIN = "main"
    PRODUCT_SELECTION = "product_selection"
    COMPARISON_SELECTION = "comparison_selection"
    EXECUTING = "executing"

class ResponseFormat(Enum):
    """Response format types"""
    COMPACT = "compact"
    STANDARD = "standard"
    EXECUTIVE = "executive"
    DETAILED = "detailed"
    KPI_ONLY = "kpi_only"
    JSON = "json"
    COMPARISON = "comparison"
    RANKING = "ranking"
    METRIC = "metric"

# ============================================================
# BLOCK 5: DATACLASSES
# ============================================================

@dataclass
class ProductContext:
    """Session context for product queries"""
    current_product: Optional[str] = None
    current_material_no: Optional[str] = None
    last_question: Optional[str] = None
    last_intent: Optional[IntentType] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    session_start: datetime = field(default_factory=datetime.now)
    menu_state: MenuState = MenuState.MAIN
    selected_option: Optional[str] = None
    comparison_products: List[str] = field(default_factory=list)
    awaiting_product: bool = False
    awaiting_comparison: bool = False
    
    def set_product(self, product: str) -> None:
        self.current_product = product
    
    def get_product(self) -> Optional[str]:
        return self.current_product
    
    def clear(self) -> None:
        self.current_product = None
        self.current_material_no = None
        self.last_question = None
        self.last_intent = None
        self.conversation_history = []
        self.menu_state = MenuState.MAIN
        self.selected_option = None
        self.comparison_products = []
        self.awaiting_product = False
        self.awaiting_comparison = False

@dataclass
class QueryPlan:
    """Query execution plan"""
    intent: IntentType
    product: Optional[str] = None
    products: List[str] = field(default_factory=list)
    material_no: Optional[str] = None
    metrics: List[str] = field(default_factory=list)
    timeframe: Optional[str] = None
    limit: int = 10
    sort_by: Optional[str] = None
    order: str = "desc"
    format: str = "standard"
    confidence: float = 1.0
    requires_ai: bool = False

@dataclass
class ProductAnswer:
    """Complete answer with metadata"""
    question: str
    intent: IntentType
    plan: QueryPlan
    dashboard: Optional[Dict[str, Any]] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    recommendations: List[str] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    formatted_response: str = ""
    confidence: float = 1.0
    execution_time_ms: float = 0.0
    source: str = "PostgreSQL"
    ai_enhanced: bool = False
    context_used: bool = False

# ============================================================
# BLOCK 6: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except (TypeError, ValueError):
        return default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _days(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "days"):
        return round(float(value.days), 2)
    return round(_number(value), 2)

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def _growth(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)

def _flag(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "pending"}

def _format_date(value: Any) -> str:
    if not value:
        return "N/A"
    if isinstance(value, datetime):
        return value.strftime("%d-%b-%Y")
    if isinstance(value, date):
        return value.strftime("%d-%b-%Y")
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%d-%b-%Y")
        except (ValueError, TypeError):
            return str(value)[:10]
    return str(value)

# ============================================================
# BLOCK 7: MENU SYSTEM
# ============================================================

class ProductMenuRenderer:
    """Render product analytics menus in WhatsApp format"""
    
    @staticmethod
    def render_main_menu() -> str:
        """Render main product menu"""
        return "\n".join([
            "📦 *PRODUCT ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Product Dashboard",
            "2. Product Revenue",
            "3. Product Units",
            "4. Product Dealers",
            "5. Product Warehouses",
            "6. Product Cities",
            "7. Pending DN",
            "8. Pending PGI",
            "9. Pending POD",
            "10. Product Comparison",
            "11. Product Ranking",
            "12. Monthly Trend",
            "13. Executive Summary",
            "14. AI Insights",
            "15. Recommendations",
            "16. Product Life Cycle",
            "17. Product Performance",
            "18. Smart Search",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type product name for dashboard",
            "• Compare [Product1] and [Product2]",
            "• Top products by revenue",
            "• Revenue of [Product]",
            "",
            "Reply with a number or product name:"
        ])
    
    @staticmethod
    def render_product_selection(prompt: str = "Enter product name:") -> str:
        """Render product selection prompt"""
        return "\n".join([
            "🔍 *Product Selection*",
            "",
            prompt,
            "",
            "💡 *Examples:*",
            "Samsung Refrigerator",
            "LG Washing Machine",
            "Daikin AC",
            "HSU-18HFC",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_comparison_selection() -> str:
        """Render comparison product selection"""
        return "\n".join([
            "🔄 *Compare Products*",
            "",
            "Enter first product name:",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_product_dashboard(product_name: str, data: Dict[str, Any]) -> str:
        """Render product dashboard"""
        lines = [
            f"📦 *Product Dashboard - {product_name}*",
            "",
            "📌 *Product Details*",
            f"Material No: {data.get('material_no', 'N/A')}",
            f"Division: {data.get('division', 'N/A')}",
            "",
            "💰 *Financials*",
            f"Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Avg Price: PKR {data.get('avg_price', 0):,.2f}",
            f"Avg Revenue/DN: PKR {data.get('avg_revenue_per_dn', 0):,.2f}",
            "",
            "📊 *Performance*",
            f"Units Sold: {data.get('total_units', 0):,}",
            f"DN Count: {data.get('dn_count', 0):,}",
            f"Dealers: {data.get('dealer_count', 0):,}",
            f"Cities: {data.get('city_count', 0):,}",
            f"Warehouses: {data.get('warehouse_count', 0):,}",
            "",
            "📈 *Metrics*",
            f"Business Score: {data.get('business_score', 0):.1f}/100",
            f"Performance Grade: {data.get('performance_grade', 'N/A')}",
            f"Growth Rate: {data.get('growth_rate', 0):+.1f}%",
            "",
            "📅 *Timeline*",
            f"First Sale: {data.get('first_sale', 'N/A')}",
            f"Last Sale: {data.get('last_sale', 'N/A')}",
            "",
            "🏆 *Top Performers*",
            f"Top Dealer: {data.get('top_dealer', 'N/A')}",
            f"Top Warehouse: {data.get('top_warehouse', 'N/A')}",
            f"Top City: {data.get('top_city', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main",
            "",
            "📌 *Try:* 'Revenue of [product]' or 'Top dealers for [product]'"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "revenue", limit: int = 10) -> str:
        """Render product rankings"""
        lines = [
            f"🏆 *Product Rankings by {metric.title()}*",
            "",
        ]
        
        for i, item in enumerate(ranking[:limit], 1):
            product = item.get('product', 'Unknown')
            value = item.get('value', 'N/A')
            
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            lines.append(f"{medal} {product}: {value}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison_result(product1: str, product2: str, metrics: Dict[str, Any]) -> str:
        """Render comparison result"""
        lines = [
            f"🔄 *Comparison: {product1} vs {product2}*",
            "",
            "───────────────────",
            "",
        ]
        
        metrics1 = metrics.get(f"{product1}_metrics", {})
        metrics2 = metrics.get(f"{product2}_metrics", {})
        
        all_keys = set(metrics1.keys()) | set(metrics2.keys())
        
        for key in sorted(all_keys):
            v1 = metrics1.get(key, "N/A")
            v2 = metrics2.get(key, "N/A")
            
            if isinstance(v1, str) and isinstance(v2, str):
                try:
                    num1 = float(re.sub(r'[^\d.]', '', v1))
                    num2 = float(re.sub(r'[^\d.]', '', v2))
                    if key.lower() in ['pending', 'pending dn', 'delivery days']:
                        winner = "✅" if num1 < num2 else "❌" if num1 > num2 else "➖"
                    else:
                        winner = "✅" if num1 > num2 else "❌" if num1 < num2 else "➖"
                    lines.append(f"{key}: {v1} vs {v2} {winner}")
                except:
                    lines.append(f"{key}: {v1} vs {v2}")
            else:
                lines.append(f"{key}: {v1} vs {v2}")
        
        lines.extend([
            "",
            "───────────────────",
            "",
            "💡 *Summary*",
            metrics.get('explanation', 'Comparison complete.'),
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_pending_list(title: str, products: List[Dict[str, Any]]) -> str:
        """Render pending product list"""
        if not products:
            return f"📋 *{title}*\n\nNo pending items found."
        
        lines = [f"📋 *{title}*", ""]
        for i, item in enumerate(products[:10], 1):
            product = item.get('product_name', 'N/A')
            pending = item.get('pending_count', 0)
            lines.append(f"{i}. {product}: {pending} pending")
        
        if len(products) > 10:
            lines.append(f"... and {len(products) - 10} more")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_executive_summary(product_name: str, data: Dict[str, Any]) -> str:
        """Render executive summary"""
        revenue = data.get('total_revenue', 0)
        units = data.get('total_units', 0)
        dn = data.get('dn_count', 0)
        dealers = data.get('dealer_count', 0)
        cities = data.get('city_count', 0)
        warehouses = data.get('warehouse_count', 0)
        growth = data.get('growth_rate', 0)
        score = data.get('business_score', 0)
        recommendations = data.get('recommendations', [])[:3]
        
        lines = [
            f"📋 *Executive Summary - {product_name}*",
            "",
            f"💰 Revenue: PKR {revenue:,.2f}",
            f"📦 Units: {units:,}",
            f"📄 DN: {dn:,}",
            f"🏪 Dealers: {dealers:,}",
            f"🏙️ Cities: {cities:,}",
            f"🏭 Warehouses: {warehouses:,}",
            f"📈 Growth: {growth:+.1f}%",
            f"⭐ Score: {score:.1f}/100",
            "",
            "🎯 *Recommendations*",
        ]
        
        for rec in recommendations:
            lines.append(f"• {rec}")
        
        if not recommendations:
            lines.append("• Maintain current performance levels")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)

# ============================================================
# BLOCK 8: INTENT ENGINE
# ============================================================

class IntentEngine:
    """AI-powered intent detection for product questions"""
    
    INTENT_PATTERNS = {
        IntentType.DASHBOARD: [
            r"(?:show|display|get).*(?:product|dashboard)",
            r"product (?:dashboard|profile|details)",
            r"show me (?:product|dashboard)",
            r"(?:product|model|material) (?:info|information)",
        ],
        IntentType.REVENUE: [
            r"(?:revenue|sales|income).*(?:product)",
            r"product (?:revenue|sales)",
            r"how much (?:revenue|sales).*(?:product)",
            r"revenue of (?:product)",
        ],
        IntentType.UNITS: [
            r"(?:units|quantity|volume).*(?:product)",
            r"product (?:units|quantity)",
            r"how many units",
            r"units sold",
        ],
        IntentType.DEALERS: [
            r"(?:dealer|dealers).*(?:product)",
            r"which dealers sell",
            r"dealer (?:distribution|analysis)",
            r"top dealers for",
        ],
        IntentType.WAREHOUSES: [
            r"(?:warehouse|warehouses).*(?:product)",
            r"which warehouse",
            r"warehouse (?:distribution|analysis)",
            r"stock movement",
        ],
        IntentType.CITIES: [
            r"(?:city|cities).*(?:product)",
            r"which cities",
            r"city (?:distribution|analysis)",
            r"top city for",
        ],
        IntentType.PENDING_DN: [
            r"(?:pending|outstanding|backlog).*(?:dn|delivery).*(?:product)",
            r"product pending (?:dn|orders)",
            r"pending deliveries",
        ],
        IntentType.PENDING_PGI: [
            r"(?:pending pgi|pgi pending).*(?:product)",
            r"product pending pgi",
        ],
        IntentType.PENDING_POD: [
            r"(?:pending pod|pod pending).*(?:product)",
            r"product pending pod",
        ],
        IntentType.COMPARISON: [
            r"compare\s+([\w\s]+)\s+and\s+([\w\s]+)",
            r"vs",
            r"comparison",
        ],
        IntentType.RANKING: [
            r"(?:top|best|highest).*(?:product|products)",
            r"product (?:ranking|rank|leaderboard)",
            r"top products",
            r"best product",
            r"worst product",
        ],
        IntentType.TREND: [
            r"(?:trend|pattern|change).*(?:product)",
            r"product (?:trend|growth|change)",
            r"monthly trend",
        ],
        IntentType.FORECAST: [
            r"(?:forecast|predict|future).*(?:product)",
            r"product (?:forecast|projection)",
        ],
        IntentType.AI_SUMMARY: [
            r"(?:summary|overview|explain).*(?:product)",
            r"product (?:summary|overview|explain)",
            r"tell me about product",
        ],
        IntentType.PERFORMANCE: [
            r"(?:performance|score|rating).*(?:product)",
            r"product (?:performance|score|health)",
            r"how is (?:product|performance)",
        ],
        IntentType.LIFE_CYCLE: [
            r"(?:life cycle|timeline|history).*(?:product)",
            r"product (?:age|life)",
            r"first shipment",
            r"last shipment",
        ],
        IntentType.RECOMMENDATIONS: [
            r"(?:recommend|suggest|advice).*(?:product)",
            r"product (?:recommendations|suggestions)",
            r"what (?:should|can) be done",
        ],
        IntentType.SEARCH: [
            r"(?:search|find|lookup).*(?:product|model|material)",
            r"search (?:product|model)",
            r"find product",
        ],
        IntentType.MENU: [
            r"menu",
            r"product menu",
            r"options",
            r"help",
        ],
    }
    
    def __init__(self):
        self._patterns = {
            intent: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
            for intent, patterns in self.INTENT_PATTERNS.items()
        }
        self._cache: TTLCache[str, Tuple[IntentType, float]] = TTLCache(maxsize=1024, ttl=3600)
        self._lock = threading.RLock()
        
        # Semantic router
        self._semantic_router = None
        if SEMANTIC_ROUTER_AVAILABLE:
            try:
                routes = [
                    Route(name="product_dashboard", utterances=[
                        "product dashboard", "show product", "product details"
                    ]),
                    Route(name="product_revenue", utterances=[
                        "product revenue", "product sales", "revenue for product"
                    ]),
                    Route(name="product_units", utterances=[
                        "product units", "units sold", "product quantity"
                    ]),
                    Route(name="product_dealers", utterances=[
                        "product dealers", "dealers selling", "dealer distribution"
                    ]),
                    Route(name="product_warehouses", utterances=[
                        "product warehouses", "warehouse distribution", "stock movement"
                    ]),
                    Route(name="product_cities", utterances=[
                        "product cities", "city distribution", "top city for product"
                    ]),
                    Route(name="product_comparison", utterances=[
                        "compare products", "product vs product", "comparison"
                    ]),
                    Route(name="product_ranking", utterances=[
                        "top products", "product ranking", "best products"
                    ]),
                    Route(name="product_summary", utterances=[
                        "product summary", "product overview", "tell me about product"
                    ]),
                    Route(name="product_performance", utterances=[
                        "product performance", "product score", "product health"
                    ]),
                ]
                self._semantic_router = Router(routes=routes, encoder=HuggingFaceEncoder())
                logger.info("✅ Semantic router initialized for products")
            except Exception as e:
                logger.warning(f"⚠️ Semantic router init failed: {e}")
    
    def detect_intent(self, question: str) -> Tuple[IntentType, float]:
        """Detect intent with confidence score"""
        question_lower = question.lower()
        cache_key = question_lower[:200]
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        
        best_intent = IntentType.UNKNOWN
        best_score = 0.0
        
        # Check for menu commands first
        if question_lower in ["menu", "product menu", "options", "help", "show menu"]:
            return IntentType.MENU, 1.0
        
        # Pattern matching
        for intent, patterns in self._patterns.items():
            matches = 0
            for pattern in patterns:
                if pattern.search(question_lower):
                    matches += 1
            
            if matches > 0:
                score = min(1.0, matches / max(1, len(patterns)) * 2)
                if score > best_score:
                    best_score = score
                    best_intent = intent
        
        # Semantic router fallback
        if best_intent == IntentType.UNKNOWN and self._semantic_router:
            try:
                result = self._semantic_router.route(question_lower)
                if result and hasattr(result, 'name'):
                    intent_name = result.name.replace("product_", "")
                    for intent in IntentType:
                        if intent.value == intent_name:
                            best_intent = intent
                            best_score = 0.7
                            break
            except Exception:
                pass
        
        # Keyword fallback
        if best_intent == IntentType.UNKNOWN:
            keywords = question_lower.split()
            for keyword in keywords:
                if keyword in ["revenue", "sales", "income"]:
                    best_intent = IntentType.REVENUE
                    best_score = 0.5
                    break
                elif keyword in ["units", "quantity"]:
                    best_intent = IntentType.UNITS
                    best_score = 0.5
                    break
                elif keyword in ["pending", "overdue", "backlog"]:
                    best_intent = IntentType.PENDING_DN
                    best_score = 0.5
                    break
                elif keyword in ["compare", "vs", "versus"]:
                    best_intent = IntentType.COMPARISON
                    best_score = 0.6
                    break
                elif keyword in ["top", "best", "ranking"]:
                    best_intent = IntentType.RANKING
                    best_score = 0.5
                    break
                elif keyword in ["dealer", "dealers"]:
                    best_intent = IntentType.DEALERS
                    best_score = 0.5
                    break
                elif keyword in ["warehouse", "warehouses"]:
                    best_intent = IntentType.WAREHOUSES
                    best_score = 0.5
                    break
                elif keyword in ["city", "cities"]:
                    best_intent = IntentType.CITIES
                    best_score = 0.5
                    break
        
        with self._lock:
            self._cache[cache_key] = (best_intent, best_score)
        
        return best_intent, best_score

# ============================================================
# BLOCK 9: ENTITY EXTRACTION ENGINE
# ============================================================

class EntityEngine:
    """Entity extraction for product questions"""
    
    def __init__(self):
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=3600)
        self._lock = threading.RLock()
    
    def extract_entities(self, question: str) -> Dict[str, Any]:
        """Extract entities from question"""
        question_lower = question.lower()
        cache_key = question_lower[:200]
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        entities = {
            "products": [],
            "material_numbers": [],
            "metrics": [],
            "limit": 10,
            "sort_by": None,
            "order": "desc",
            "comparison_products": [],
            "requires_comparison": False,
        }
        
        # Extract product names
        products = self._extract_products(question_lower)
        if products:
            entities["products"] = products
        
        # Extract material numbers
        material_numbers = self._extract_material_numbers(question_lower)
        if material_numbers:
            entities["material_numbers"] = material_numbers
        
        # Extract metrics
        metrics = self._extract_metrics(question_lower)
        if metrics:
            entities["metrics"] = metrics
        
        # Extract limit
        limit = self._extract_limit(question_lower)
        if limit:
            entities["limit"] = limit
        
        # Check for comparison
        if "compare" in question_lower or "vs" in question_lower or "versus" in question_lower:
            entities["requires_comparison"] = True
            if len(entities["products"]) >= 2:
                entities["comparison_products"] = entities["products"][:2]
        
        # Extract sort order
        if "highest" in question_lower or "top" in question_lower:
            entities["order"] = "desc"
        elif "lowest" in question_lower or "bottom" in question_lower:
            entities["order"] = "asc"
        
        # Extract sort by
        for metric in ["revenue", "units", "dn", "delivery", "pending"]:
            if metric in question_lower:
                entities["sort_by"] = metric
                break
        
        with self._lock:
            self._cache[cache_key] = entities.copy()
        
        return entities
    
    def _extract_products(self, text: str) -> List[str]:
        """Extract product names from text"""
        found = []
        
        # Direct matches
        for product in PRODUCT_NAMES:
            if product.lower() in text:
                found.append(product)
        
        # Check aliases
        for alias, product in PRODUCT_ALIASES.items():
            if alias in text:
                if product not in found:
                    found.append(product)
        
        # Check for quoted product names
        match = re.search(r'"([^"]+)"', text)
        if match:
            found.append(match.group(1))
        
        # Check for product codes (like HSU-18HFC)
        match = re.search(r'\b([A-Z]{2,4}-\d{2,3}[A-Z]{2,4})\b', text.upper())
        if match:
            found.append(match.group(1))
        
        return found
    
    def _extract_material_numbers(self, text: str) -> List[str]:
        """Extract material numbers from text"""
        # Material numbers are typically alphanumeric, 5-15 characters
        matches = re.findall(r'\b([A-Z0-9]{5,15})\b', text.upper())
        return matches
    
    def _extract_metrics(self, text: str) -> List[str]:
        """Extract metrics from text"""
        metric_keywords = {
            "revenue": ["revenue", "sales", "income"],
            "units": ["units", "quantity", "volume"],
            "pending": ["pending", "backlog", "overdue"],
            "dealer": ["dealer", "dealers"],
            "warehouse": ["warehouse", "warehouses"],
            "city": ["city", "cities"],
            "performance": ["performance", "score", "rating"],
        }
        
        found = []
        for metric, keywords in metric_keywords.items():
            for keyword in keywords:
                if keyword in text:
                    found.append(metric)
                    break
        
        return found
    
    def _extract_limit(self, text: str) -> Optional[int]:
        """Extract numeric limit from text"""
        patterns = [
            r"top\s+(\d+)",
            r"first\s+(\d+)",
            r"limit\s+(\d+)",
            r"(\d+)\s+(?:products|items)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    pass
        return None

# ============================================================
# BLOCK 10: PRODUCT REPOSITORY
# ============================================================

class ProductRepository:
    """Product data access layer - PostgreSQL only"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=2048, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def get_product_by_name(self, product_identifier: str) -> Optional[Dict[str, Any]]:
        """Get product by name, model, or material number"""
        product_identifier_lower = product_identifier.lower()
        cache_key = f"product_{product_identifier_lower}"
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            # Search by customer_model or material_no
            query = self.session.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label('product_name'),
                DeliveryReport.material_no,
                DeliveryReport.division,
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealer_count'),
                func.count(distinct(DeliveryReport.ship_to_city)).label('city_count'),
                func.count(distinct(DeliveryReport.warehouse)).label('warehouse_count'),
                func.min(DeliveryReport.dn_create_date).label('first_sale'),
                func.max(DeliveryReport.dn_create_date).label('last_sale'),
                func.avg(case(
                    (DeliveryReport.dn_qty > 0, DeliveryReport.dn_amount / DeliveryReport.dn_qty),
                    else_=0
                )).label('avg_price'),
                func.avg(DeliveryReport.dn_amount).label('avg_revenue_per_dn'),
                func.count(distinct(case(
                    (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_dn'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)
                ))).label('pgi_pending_dn'),
                func.count(distinct(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pod_pending_dn'),
                func.count(distinct(case(
                    (DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)
                ))).label('pod_completed'),
                func.avg(case(
                    (DeliveryReport.good_issue_date.isnot(None),
                     DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                )).label('avg_delivery_days'),
            ).filter(
                or_(
                    func.lower(DeliveryReport.customer_model) == product_identifier_lower,
                    func.lower(DeliveryReport.material_no) == product_identifier_lower,
                    func.lower(DeliveryReport.customer_model).ilike(f"%{product_identifier_lower}%"),
                    func.lower(DeliveryReport.material_no).ilike(f"%{product_identifier_lower}%"),
                )
            ).group_by(
                DeliveryReport.customer_model,
                DeliveryReport.material_no,
                DeliveryReport.division
            ).first()
            
            if not query:
                return None
            
            product_data = {
                'product_name': _text(query.product_name),
                'material_no': _text(query.material_no),
                'division': _text(query.division),
                'dn_count': int(query.dn_count or 0),
                'total_units': int(query.total_units or 0),
                'total_revenue': float(query.total_revenue or 0.0),
                'dealer_count': int(query.dealer_count or 0),
                'city_count': int(query.city_count or 0),
                'warehouse_count': int(query.warehouse_count or 0),
                'first_sale': _date_text(query.first_sale),
                'last_sale': _date_text(query.last_sale),
                'avg_price': float(query.avg_price or 0.0),
                'avg_revenue_per_dn': float(query.avg_revenue_per_dn or 0.0),
                'pending_dn': int(query.pending_dn or 0),
                'pgi_pending_dn': int(query.pgi_pending_dn or 0),
                'pod_pending_dn': int(query.pod_pending_dn or 0),
                'pod_completed': int(query.pod_completed or 0),
                'avg_delivery_days': float(query.avg_delivery_days or 0.0),
            }
            
            # Get top performers
            top_dealer = self._get_top_dealer(product_identifier)
            product_data['top_dealer'] = _text(top_dealer) if top_dealer else "N/A"
            
            top_warehouse = self._get_top_warehouse(product_identifier)
            product_data['top_warehouse'] = _text(top_warehouse) if top_warehouse else "N/A"
            
            top_city = self._get_top_city(product_identifier)
            product_data['top_city'] = _text(top_city) if top_city else "N/A"
            
            # Get monthly data for growth
            monthly_data = self._get_monthly_data(product_identifier)
            if monthly_data:
                product_data.update(monthly_data)
            
            # Calculate metrics
            product_data['delivery_success_pct'] = _percent(
                product_data.get('pod_completed', 0),
                product_data.get('dn_count', 0)
            )
            product_data['pending_pct'] = _percent(
                product_data.get('pending_dn', 0),
                product_data.get('dn_count', 0)
            )
            
            # Business score
            score = (
                product_data.get('delivery_success_pct', 0) * 0.25 +
                (100 - product_data.get('pending_pct', 0)) * 0.25 +
                min(100, product_data.get('total_units', 0) / 100) * 0.20 +
                min(100, product_data.get('dealer_count', 0) * 5) * 0.15 +
                min(100, product_data.get('city_count', 0) * 3) * 0.15
            )
            product_data['business_score'] = round(min(100, max(0, score)), 1)
            
            # Performance grade
            if product_data['business_score'] >= 85:
                product_data['performance_grade'] = "A"
                product_data['overall_status'] = "Excellent"
            elif product_data['business_score'] >= 70:
                product_data['performance_grade'] = "B"
                product_data['overall_status'] = "Good"
            elif product_data['business_score'] >= 50:
                product_data['performance_grade'] = "C"
                product_data['overall_status'] = "Watch"
            else:
                product_data['performance_grade'] = "D"
                product_data['overall_status'] = "Critical"
            
            # Generate insights and recommendations
            product_data['insights'] = self._generate_insights(product_data)
            product_data['recommendations'] = self._generate_recommendations(product_data)
            product_data['executive_summary'] = self._generate_executive_summary(product_data)
            
            with self._lock:
                self._cache[cache_key] = product_data.copy()
            
            return product_data
            
        except Exception as e:
            logger.error(f"Failed to get product data for {product_identifier}: {e}")
            return None
    
    def _get_top_dealer(self, product_identifier: str) -> Optional[str]:
        """Get top dealer by revenue for product"""
        result = self.session.query(
            DeliveryReport.customer_name,
            func.sum(DeliveryReport.dn_amount).label('revenue')
        ).filter(
            or_(
                func.lower(DeliveryReport.customer_model) == product_identifier.lower(),
                func.lower(DeliveryReport.material_no) == product_identifier.lower(),
                func.lower(DeliveryReport.customer_model).ilike(f"%{product_identifier.lower()}%"),
                func.lower(DeliveryReport.material_no).ilike(f"%{product_identifier.lower()}%"),
            )
        ).group_by(
            DeliveryReport.customer_name
        ).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).first()
        
        return result.customer_name if result else None
    
    def _get_top_warehouse(self, product_identifier: str) -> Optional[str]:
        """Get top warehouse by revenue for product"""
        result = self.session.query(
            DeliveryReport.warehouse,
            func.sum(DeliveryReport.dn_amount).label('revenue')
        ).filter(
            or_(
                func.lower(DeliveryReport.customer_model) == product_identifier.lower(),
                func.lower(DeliveryReport.material_no) == product_identifier.lower(),
                func.lower(DeliveryReport.customer_model).ilike(f"%{product_identifier.lower()}%"),
                func.lower(DeliveryReport.material_no).ilike(f"%{product_identifier.lower()}%"),
            )
        ).group_by(
            DeliveryReport.warehouse
        ).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).first()
        
        return result.warehouse if result else None
    
    def _get_top_city(self, product_identifier: str) -> Optional[str]:
        """Get top city by revenue for product"""
        result = self.session.query(
            DeliveryReport.ship_to_city,
            func.sum(DeliveryReport.dn_amount).label('revenue')
        ).filter(
            or_(
                func.lower(DeliveryReport.customer_model) == product_identifier.lower(),
                func.lower(DeliveryReport.material_no) == product_identifier.lower(),
                func.lower(DeliveryReport.customer_model).ilike(f"%{product_identifier.lower()}%"),
                func.lower(DeliveryReport.material_no).ilike(f"%{product_identifier.lower()}%"),
            )
        ).group_by(
            DeliveryReport.ship_to_city
        ).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).first()
        
        return result.ship_to_city if result else None
    
    def _get_monthly_data(self, product_identifier: str) -> Dict[str, Any]:
        """Get monthly growth data"""
        try:
            monthly = self.session.query(
                func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label('month'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
            ).filter(
                or_(
                    func.lower(DeliveryReport.customer_model) == product_identifier.lower(),
                    func.lower(DeliveryReport.material_no) == product_identifier.lower(),
                    func.lower(DeliveryReport.customer_model).ilike(f"%{product_identifier.lower()}%"),
                    func.lower(DeliveryReport.material_no).ilike(f"%{product_identifier.lower()}%"),
                ),
                DeliveryReport.dn_create_date.isnot(None)
            ).group_by(
                'month'
            ).order_by(
                'month'
            ).all()
            
            if not monthly or len(monthly) < 2:
                return {}
            
            current = monthly[-1]
            previous = monthly[-2] if len(monthly) >= 2 else None
            
            current_revenue = float(current.revenue or 0)
            previous_revenue = float(previous.revenue or 0) if previous else 0
            
            return {
                'current_month_revenue': current_revenue,
                'previous_month_revenue': previous_revenue,
                'growth_rate': _growth(current_revenue, previous_revenue),
                'monthly_trend': [{
                    'month': row.month,
                    'revenue': float(row.revenue or 0),
                    'units': int(row.units or 0)
                } for row in monthly[-6:]]  # Last 6 months
            }
        except Exception:
            return {}
    
    def _generate_insights(self, data: Dict[str, Any]) -> List[str]:
        """Generate insights from data"""
        insights = []
        
        revenue = data.get('total_revenue', 0)
        growth = data.get('growth_rate', 0)
        pending = data.get('pending_dn', 0)
        score = data.get('business_score', 0)
        dealers = data.get('dealer_count', 0)
        
        if revenue > 0 and growth > 15:
            insights.append(f"Strong revenue growth at {growth:+.1f}%")
        elif revenue > 0 and growth > 5:
            insights.append(f"Steady revenue growth at {growth:+.1f}%")
        elif revenue > 0 and growth < -10:
            insights.append(f"Revenue decline of {growth:+.1f}% needs attention")
        
        if pending == 0:
            insights.append("No pending orders - excellent operational efficiency")
        elif pending < 10:
            insights.append(f"Low pending orders: {pending}")
        else:
            insights.append(f"High pending orders: {pending}. Priority for resolution")
        
        if score >= 85:
            insights.append(f"Excellent business score of {score:.1f}/100")
        elif score >= 70:
            insights.append(f"Good business score of {score:.1f}/100")
        elif score < 50:
            insights.append(f"Critical business score of {score:.1f}/100")
        
        if dealers >= 50:
            insights.append(f"Strong dealer network with {dealers} dealers")
        elif dealers >= 20:
            insights.append(f"Good dealer network with {dealers} dealers")
        elif dealers < 10:
            insights.append(f"Limited dealer network: {dealers} dealers")
        
        if not insights:
            insights.append("Performance is stable. Continue monitoring.")
        
        return insights
    
    def _generate_recommendations(self, data: Dict[str, Any]) -> List[str]:
        """Generate recommendations from data"""
        recommendations = []
        
        pending = data.get('pending_dn', 0)
        delivery = data.get('delivery_success_pct', 0)
        score = data.get('business_score', 0)
        dealers = data.get('dealer_count', 0)
        top_dealer = data.get('top_dealer', '')
        
        if pending > 20:
            recommendations.append(f"Escalate {pending} pending DNs for resolution")
        elif pending > 10:
            recommendations.append("Review pending orders for timely closure")
        
        if delivery < 80:
            recommendations.append("Improve delivery speed and reliability")
        
        if score < 70:
            recommendations.append("Develop action plan to improve business score")
        
        if dealers < 10:
            recommendations.append("Expand dealer network to increase market reach")
        
        if top_dealer:
            recommendations.append(f"Focus on {top_dealer} for premium distribution")
        
        if not recommendations:
            recommendations.append("Maintain current performance levels")
            recommendations.append("Continue monitoring key metrics")
            recommendations.append("Explore new market opportunities")
        
        return recommendations
    
    def _generate_executive_summary(self, data: Dict[str, Any]) -> str:
        """Generate executive summary"""
        product = data.get('product_name', 'Product')
        revenue = data.get('total_revenue', 0)
        units = data.get('total_units', 0)
        pending = data.get('pending_dn', 0)
        score = data.get('business_score', 0)
        growth = data.get('growth_rate', 0)
        status = data.get('overall_status', 'Unknown')
        
        if growth >= 0:
            trend = "growing"
        else:
            trend = "declining"
        
        if score >= 70:
            action = "maintain current controls"
        else:
            action = "prioritize pending DN and POD closure"
        
        return (
            f"{product} is {trend} with a {score:.1f}/100 business score. "
            f"Revenue is PKR {revenue:,.2f} with {units:,} units and {pending} pending DNs. "
            f"Status: {status}. Recommendation: {action}."
        )
    
    def get_top_products_by_revenue(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top products by revenue"""
        try:
            results = self.session.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label('product'),
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(
                or_(
                    DeliveryReport.customer_model.isnot(None),
                    DeliveryReport.material_no.isnot(None)
                )
            ).group_by(
                'product'
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            ranking = []
            for row in results:
                if row.product:
                    ranking.append({
                        'product': _text(row.product),
                        'value': f"PKR {float(row.revenue or 0):,.2f}"
                    })
            return ranking
        except Exception as e:
            logger.error(f"Failed to get top products: {e}")
            return []
    
    def get_top_products_by_units(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top products by units sold"""
        try:
            results = self.session.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label('product'),
                func.sum(DeliveryReport.dn_qty).label('units')
            ).filter(
                or_(
                    DeliveryReport.customer_model.isnot(None),
                    DeliveryReport.material_no.isnot(None)
                )
            ).group_by(
                'product'
            ).order_by(
                func.sum(DeliveryReport.dn_qty).desc()
            ).limit(limit).all()
            
            ranking = []
            for row in results:
                if row.product:
                    ranking.append({
                        'product': _text(row.product),
                        'value': f"{int(row.units or 0):,} units"
                    })
            return ranking
        except Exception as e:
            logger.error(f"Failed to get top products by units: {e}")
            return []
    
    def get_products_by_warehouse(self, warehouse: str) -> List[Dict[str, Any]]:
        """Get products by warehouse"""
        try:
            results = self.session.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label('product'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count')
            ).filter(
                func.lower(DeliveryReport.warehouse) == warehouse.lower()
            ).group_by(
                'product'
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).all()
            
            products = []
            for row in results:
                if row.product:
                    products.append({
                        'product': _text(row.product),
                        'revenue': float(row.revenue or 0),
                        'units': int(row.units or 0),
                        'dn_count': int(row.dn_count or 0)
                    })
            return products
        except Exception as e:
            logger.error(f"Failed to get products by warehouse: {e}")
            return []
    
    def get_products_by_city(self, city: str) -> List[Dict[str, Any]]:
        """Get products by city"""
        try:
            results = self.session.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label('product'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count')
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city.lower()
            ).group_by(
                'product'
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).all()
            
            products = []
            for row in results:
                if row.product:
                    products.append({
                        'product': _text(row.product),
                        'revenue': float(row.revenue or 0),
                        'units': int(row.units or 0),
                        'dn_count': int(row.dn_count or 0)
                    })
            return products
        except Exception as e:
            logger.error(f"Failed to get products by city: {e}")
            return []
    
    def get_product_dealers(self, product_identifier: str) -> List[Dict[str, Any]]:
        """Get dealers selling a product"""
        try:
            results = self.session.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count')
            ).filter(
                or_(
                    func.lower(DeliveryReport.customer_model) == product_identifier.lower(),
                    func.lower(DeliveryReport.material_no) == product_identifier.lower(),
                    func.lower(DeliveryReport.customer_model).ilike(f"%{product_identifier.lower()}%"),
                    func.lower(DeliveryReport.material_no).ilike(f"%{product_identifier.lower()}%"),
                )
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).all()
            
            dealers = []
            for row in results:
                if row.customer_name:
                    dealers.append({
                        'dealer': _text(row.customer_name),
                        'revenue': float(row.revenue or 0),
                        'units': int(row.units or 0),
                        'dn_count': int(row.dn_count or 0)
                    })
            return dealers
        except Exception as e:
            logger.error(f"Failed to get product dealers: {e}")
            return []
    
    def search_products(self, query: str) -> List[Dict[str, Any]]:
        """Search for products"""
        try:
            search_pattern = f"%{query}%"
            results = self.session.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label('product'),
                DeliveryReport.material_no,
                DeliveryReport.division,
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count')
            ).filter(
                or_(
                    DeliveryReport.customer_model.ilike(search_pattern),
                    DeliveryReport.material_no.ilike(search_pattern),
                    func.lower(DeliveryReport.customer_model).ilike(f"%{query.lower()}%"),
                    func.lower(DeliveryReport.material_no).ilike(f"%{query.lower()}%"),
                )
            ).group_by(
                DeliveryReport.customer_model,
                DeliveryReport.material_no,
                DeliveryReport.division
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(20).all()
            
            products = []
            for row in results:
                if row.product:
                    products.append({
                        'product': _text(row.product),
                        'material_no': _text(row.material_no),
                        'division': _text(row.division),
                        'revenue': float(row.revenue or 0),
                        'units': int(row.units or 0),
                        'dn_count': int(row.dn_count or 0)
                    })
            return products
        except Exception as e:
            logger.error(f"Failed to search products: {e}")
            return []

# ============================================================
# BLOCK 11: PRODUCT DASHBOARD BUILDER
# ============================================================

class ProductDashboardBuilder:
    """Build product dashboards from database"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=CACHE_TTL)
        self._lock = threading.RLock()
        self.repository = ProductRepository(session)
    
    def build(self, product_identifier: str) -> Optional[Dict[str, Any]]:
        """Build dashboard for product"""
        cache_key = product_identifier.lower()
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        product_data = self.repository.get_product_by_name(product_identifier)
        
        if product_data:
            with self._lock:
                self._cache[cache_key] = product_data.copy()
        
        return product_data

# ============================================================
# BLOCK 12: RESPONSE FORMATTER
# ============================================================

class ResponseFormatter:
    """Format responses for different output types"""
    
    def __init__(self):
        self._menu_renderer = ProductMenuRenderer()
    
    def format(self, answer: ProductAnswer) -> str:
        """Format answer based on plan format"""
        if answer.plan.format == ResponseFormat.METRIC:
            return self._format_metric(answer)
        elif answer.plan.format == ResponseFormat.COMPACT:
            return self._format_compact(answer)
        elif answer.plan.format == ResponseFormat.EXECUTIVE:
            return self._format_executive(answer)
        elif answer.plan.format == ResponseFormat.DETAILED:
            return self._format_detailed(answer)
        elif answer.plan.format == ResponseFormat.KPI_ONLY:
            return self._format_kpi_only(answer)
        elif answer.plan.format == ResponseFormat.COMPARISON:
            return self._format_comparison(answer)
        elif answer.plan.format == ResponseFormat.RANKING:
            return self._format_ranking(answer)
        else:
            return self._format_standard(answer)
    
    def _format_metric(self, answer: ProductAnswer) -> str:
        """Single metric format"""
        product = answer.plan.product or "Product"
        lines = [f"📊 *{product}*"]
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        if answer.explanation:
            lines.append("")
            lines.append(answer.explanation)
        
        return "\n".join(lines)
    
    def _format_compact(self, answer: ProductAnswer) -> str:
        """Compact format"""
        product = answer.plan.product or "Product"
        lines = [f"📊 {product}"]
        lines.append("")
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_standard(self, answer: ProductAnswer) -> str:
        """Standard format"""
        return self._menu_renderer.render_product_dashboard(
            answer.plan.product or "Product",
            answer.dashboard or {}
        )
    
    def _format_executive(self, answer: ProductAnswer) -> str:
        """Executive summary format"""
        product = answer.plan.product or "Product"
        lines = [
            f"📋 *Executive Summary - {product}*",
            "",
            answer.explanation or "Performance summary not available.",
            "",
            "📊 *Key Metrics:*",
        ]
        
        for metric_name, value in list(answer.metrics.items())[:5]:
            lines.append(f"• {metric_name}: {value}")
        
        if answer.insights:
            lines.append("")
            lines.append("💡 *Key Insights:*")
            for insight in answer.insights[:2]:
                lines.append(f"• {insight}")
        
        if answer.recommendations:
            lines.append("")
            lines.append("🎯 *Recommendations:*")
            for rec in answer.recommendations[:2]:
                lines.append(f"• {rec}")
        
        return "\n".join(lines)
    
    def _format_detailed(self, answer: ProductAnswer) -> str:
        """Detailed format"""
        product = answer.plan.product or "Product"
        lines = [
            f"📊 *Detailed Analysis - {product}*",
            "",
            "📍 *Product Details*",
            "─" * 40,
        ]
        
        if answer.dashboard:
            lines.append(f"Material No: {answer.dashboard.get('material_no', 'N/A')}")
            lines.append(f"Division: {answer.dashboard.get('division', 'N/A')}")
        
        lines.append("")
        lines.append("📈 *Metrics*")
        lines.append("─" * 40)
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        if answer.insights:
            lines.append("")
            lines.append("💡 *Insights*")
            lines.append("─" * 40)
            for insight in answer.insights:
                lines.append(f"• {insight}")
        
        if answer.recommendations:
            lines.append("")
            lines.append("🎯 *Recommendations*")
            lines.append("─" * 40)
            for rec in answer.recommendations:
                lines.append(f"• {rec}")
        
        return "\n".join(lines)
    
    def _format_kpi_only(self, answer: ProductAnswer) -> str:
        """KPI-only format"""
        product = answer.plan.product or "Product"
        lines = [f"📊 *{product} KPIs*:"]
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"  {metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_comparison(self, answer: ProductAnswer) -> str:
        """Comparison format"""
        return self._menu_renderer.render_comparison_result(
            answer.plan.products[0] if answer.plan.products else "",
            answer.plan.products[1] if len(answer.plan.products) > 1 else "",
            answer.metrics
        )
    
    def _format_ranking(self, answer: ProductAnswer) -> str:
        """Ranking format"""
        ranking_data = answer.metrics.get("ranking", [])
        return self._menu_renderer.render_ranking(ranking_data, answer.plan.sort_by or "revenue", answer.plan.limit)

# ============================================================
# BLOCK 13: MAIN PRODUCT ANALYTICS SERVICE WITH MENU
# ============================================================

class ProductAnalyticsService:
    """
    Product Domain AI Expert with Full Menu System
    Single entry point for all product-related business questions
    PostgreSQL is the ONLY source of truth.
    """
    
    def __init__(self) -> None:
        self._service_name = "product_analytics"
        self._version = "5.0.0-menu"
        self._startup_time = datetime.utcnow().isoformat()
        
        # Initialize engines
        self._intent_engine = IntentEngine()
        self._entity_engine = EntityEngine()
        self._menu_renderer = ProductMenuRenderer()
        self._formatter = ResponseFormatter()
        
        # Context memory
        self._contexts: Dict[str, ProductContext] = {}
        self._context_lock = threading.RLock()
        
        # Caches
        self._dashboard_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._answer_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=300)
        
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=4)
        
        logger.info(f"✅ ProductAnalyticsService initialized (v{self._version})")
        logger.info(f"   Menu System: ✅")
        logger.info(f"   Source of Truth: PostgreSQL")
        logger.info(f"   Product Repository: ✅")
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()
    
    def get_main_menu(self) -> str:
        """Get the main product menu"""
        return self._menu_renderer.render_main_menu()
    
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        """
        Process menu input and return response
        
        Returns:
            {
                "response": str,           # WhatsApp message
                "menu_type": str,          # "product_menu"
                "action": str,             # Action performed
                "data": dict,              # Additional data
                "exit_menu": bool          # True if should return to main menu
            }
        """
        context = self._get_context(session_id)
        user_input = user_input.strip()
        
        # Handle main menu navigation
        if user_input == "0":
            return self._handle_main_menu_return(context)
        elif user_input == "99":
            return self._handle_main_menu_return(context)
        
        # Handle menu options based on state
        if context.menu_state == MenuState.MAIN:
            return self._handle_main_menu_option(context, user_input)
        elif context.menu_state == MenuState.PRODUCT_SELECTION:
            return self._handle_product_selection(context, user_input)
        elif context.menu_state == MenuState.COMPARISON_SELECTION:
            return self._handle_comparison_selection(context, user_input)
        
        # Default: treat as quick query
        return self._handle_quick_query(context, user_input)
    
    def _handle_main_menu_return(self, context: ProductContext) -> Dict[str, Any]:
        """Return to main menu"""
        context.menu_state = MenuState.MAIN
        context.selected_option = None
        context.comparison_products = []
        context.awaiting_product = False
        context.awaiting_comparison = False
        
        return {
            "response": self._menu_renderer.render_main_menu(),
            "menu_type": "product_menu",
            "action": "main_menu",
            "data": {},
            "exit_menu": True  # Exit to main AI Logistics menu
        }
    
    def _handle_main_menu_option(self, context: ProductContext, option: str) -> Dict[str, Any]:
        """Handle main menu option selection"""
        
        option_map = {
            "1": ("dashboard", "Enter product name for dashboard:"),
            "2": ("revenue", "Enter product name for revenue:"),
            "3": ("units", "Enter product name for units:"),
            "4": ("dealers", "Enter product name for dealers:"),
            "5": ("warehouses", "Enter product name for warehouses:"),
            "6": ("cities", "Enter product name for cities:"),
            "7": ("pending_dn", "Enter product name for pending DN:"),
            "8": ("pending_pgi", "Enter product name for pending PGI:"),
            "9": ("pending_pod", "Enter product name for pending POD:"),
            "10": ("comparison", None),  # Special handling
            "11": ("ranking", None),  # Special handling
            "12": ("trend", "Enter product name for trend:"),
            "13": ("executive_summary", "Enter product name for summary:"),
            "14": ("ai_insights", "Enter product name for AI insights:"),
            "15": ("recommendations", "Enter product name for recommendations:"),
            "16": ("life_cycle", "Enter product name for life cycle:"),
            "17": ("performance", "Enter product name for performance:"),
            "18": ("search", None),  # Special handling
        }
        
        if option == "10":
            return self._handle_comparison_start(context)
        elif option == "11":
            return self._handle_ranking_request(context)
        elif option == "18":
            return self._handle_search_start(context)
        
        if option not in option_map:
            return self._handle_quick_query(context, option)
        
        action, prompt = option_map[option]
        
        # Check if we already have a selected product
        if context.current_product:
            result = self._execute_product_action(context, action, context.current_product)
            result["exit_menu"] = False
            return result
        
        # Ask for product
        context.menu_state = MenuState.PRODUCT_SELECTION
        context.selected_option = action
        context.awaiting_product = True
        
        return {
            "response": self._menu_renderer.render_product_selection(prompt),
            "menu_type": "product_menu",
            "action": "product_selection",
            "data": {"purpose": action},
            "exit_menu": False
        }
    
    def _handle_product_selection(self, context: ProductContext, product_input: str) -> Dict[str, Any]:
        """Handle product selection response"""
        product_name = self._resolve_product_name(product_input)
        if not product_name:
            return {
                "response": "\n".join([
                    "❌ Product not found.",
                    "",
                    "Please try again or enter a valid product name.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "product_menu",
                "action": "product_selection_error",
                "data": {},
                "exit_menu": False
            }
        
        context.current_product = product_name
        context.menu_state = MenuState.MAIN
        context.awaiting_product = False
        
        action = context.selected_option or "dashboard"
        result = self._execute_product_action(context, action, product_name)
        result["exit_menu"] = False
        return result
    
    def _handle_comparison_selection(self, context: ProductContext, product_input: str) -> Dict[str, Any]:
        """Handle comparison product selection"""
        product_name = self._resolve_product_name(product_input)
        if not product_name:
            return {
                "response": "\n".join([
                    "❌ Product not found.",
                    "",
                    "Please try again or enter a valid product name.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "product_menu",
                "action": "comparison_error",
                "data": {},
                "exit_menu": False
            }
        
        context.comparison_products.append(product_name)
        
        if len(context.comparison_products) == 1:
            return {
                "response": "\n".join([
                    f"✅ First product selected: {product_name}",
                    "",
                    "Enter second product name:",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "product_menu",
                "action": "comparison_second",
                "data": {"first_product": product_name},
                "exit_menu": False
            }
        else:
            product1, product2 = context.comparison_products[0], context.comparison_products[1]
            context.menu_state = MenuState.MAIN
            context.comparison_products = []
            return self._perform_comparison(context, product1, product2)
    
    def _handle_ranking_request(self, context: ProductContext) -> Dict[str, Any]:
        """Handle ranking request"""
        result = self._get_product_ranking(context)
        result["exit_menu"] = False
        return result
    
    def _handle_search_start(self, context: ProductContext) -> Dict[str, Any]:
        """Start search"""
        context.menu_state = MenuState.PRODUCT_SELECTION
        context.selected_option = "search"
        context.awaiting_product = True
        
        return {
            "response": "\n".join([
                "🔍 *Search Products*",
                "",
                "Enter product name, model, or material number:",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "product_menu",
            "action": "search_start",
            "data": {},
            "exit_menu": False
        }
    
    def _handle_comparison_start(self, context: ProductContext) -> Dict[str, Any]:
        """Start comparison process"""
        context.menu_state = MenuState.COMPARISON_SELECTION
        context.comparison_products = []
        return {
            "response": self._menu_renderer.render_comparison_selection(),
            "menu_type": "product_menu",
            "action": "comparison_start",
            "data": {},
            "exit_menu": False
        }
    
    def _handle_quick_query(self, context: ProductContext, query: str) -> Dict[str, Any]:
        """Handle quick query from main menu"""
        # Check if it's a comparison
        if "compare" in query.lower() or "vs" in query.lower():
            import re
            products = re.findall(r'([\w\s]+?)(?:and|vs|versus)([\w\s]+)', query, re.IGNORECASE)
            if products:
                product1 = self._resolve_product_name(products[0][0].strip())
                product2 = self._resolve_product_name(products[0][1].strip())
                if product1 and product2:
                    return self._perform_comparison(context, product1, product2)
        
        # Check if it's a valid product name
        product_name = self._resolve_product_name(query)
        if product_name:
            context.current_product = product_name
            return self._get_product_dashboard(context, product_name)
        
        # Check if it's a ranking query
        if "top" in query.lower() and ("product" in query.lower() or "products" in query.lower()):
            return self._get_product_ranking(context)
        
        # Check for specific metrics
        for metric in ["revenue", "units", "pending", "dealers", "warehouses", "cities"]:
            if metric in query.lower() and " of " in query.lower():
                # Try to extract product name from "metric of product"
                parts = query.lower().split(" of ")
                if len(parts) > 1:
                    product_name = self._resolve_product_name(parts[-1].strip())
                    if product_name:
                        context.current_product = product_name
                        return self._execute_product_action(context, metric, product_name)
        
        # Default response
        return {
            "response": "\n".join([
                "❌ I didn't understand that.",
                "",
                "💡 *Try one of these:*",
                "• 'Samsung Refrigerator' - Show dashboard",
                "• 'Revenue of Samsung Refrigerator'",
                "• 'Pending in LG Washing Machine'",
                "• 'Compare Samsung and LG'",
                "• 'Top products by revenue'",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "product_menu",
            "action": "unknown_query",
            "data": {},
            "exit_menu": False
        }
    
    def _execute_product_action(self, context: ProductContext, action: str, product_name: str) -> Dict[str, Any]:
        """Execute product action based on selected option"""
        action_map = {
            "dashboard": self._get_product_dashboard,
            "revenue": self._get_product_metric,
            "units": self._get_product_metric,
            "dealers": self._get_product_dealers,
            "warehouses": self._get_product_warehouses,
            "cities": self._get_product_cities,
            "pending_dn": self._get_product_pending_dn,
            "pending_pgi": self._get_product_pending_pgi,
            "pending_pod": self._get_product_pending_pod,
            "trend": self._get_product_trend,
            "executive_summary": self._get_product_executive_summary,
            "ai_insights": self._get_product_ai_insights,
            "recommendations": self._get_product_recommendations,
            "life_cycle": self._get_product_life_cycle,
            "performance": self._get_product_performance,
            "search": self._search_products,
        }
        
        handler = action_map.get(action, self._get_product_dashboard)
        
        if action in ["revenue", "units"]:
            return handler(context, product_name, action)
        elif action == "search":
            return handler(context, product_name)
        else:
            return handler(context, product_name)
    
    def _resolve_product_name(self, input_text: str) -> Optional[str]:
        """Resolve product name from input"""
        input_lower = input_text.lower().strip()
        
        # Direct match
        for product in PRODUCT_NAMES:
            if product.lower() == input_lower:
                return product
        
        # Check aliases
        for alias, product in PRODUCT_ALIASES.items():
            if alias in input_lower:
                return product
        
        # Fuzzy match
        if RAPIDFUZZ_AVAILABLE:
            matches = process.extract(input_lower, PRODUCT_NAMES, scorer=fuzz.WRatio, limit=1)
            if matches and matches[0][1] >= 85:
                return matches[0][0]
        
        # Partial match
        for product in PRODUCT_NAMES:
            if len(input_lower) >= 3:
                if input_lower[:3] in product.lower() or product.lower()[:3] in input_lower:
                    return product
        
        # Try database lookup
        try:
            with self._session() as session:
                repository = ProductRepository(session)
                result = repository.search_products(input_text)
                if result:
                    # Return first product
                    dealer_name = result[0].get('product')
                    if dealer_name:
                        return dealer_name
        except Exception:
            pass
        
        return None
    
    def _get_context(self, session_id: str) -> ProductContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = ProductContext()
            return self._contexts[session_id]
    
    # ============================================================
    # PRODUCT OPERATIONS - ALL DATA FROM POSTGRESQL
    # ============================================================
    
    def _get_product_dashboard(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get product dashboard"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\nPlease check the product name and try again.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "dashboard",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_product_dashboard(product_name, dashboard),
                    "menu_type": "product_menu",
                    "action": "dashboard",
                    "data": {"product": product_name, "dashboard": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            return {
                "response": f"⚠️ Service error for {product_name}: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_metric(self, context: ProductContext, product_name: str, metric: str) -> Dict[str, Any]:
        """Get specific product metric"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "metric_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                metric_mapping = {
                    "revenue": ("Revenue", f"PKR {dashboard.get('total_revenue', 0):,.2f}"),
                    "units": ("Units", f"{dashboard.get('total_units', 0):,}"),
                }
                
                label, value = metric_mapping.get(metric, ("Metric", "N/A"))
                
                return {
                    "response": "\n".join([
                        f"📊 *{product_name} - {label}*",
                        "",
                        f"{value}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "product_menu",
                    "action": f"metric_{metric}",
                    "data": {"product": product_name, "metric": metric, "value": value},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_dealers(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get dealers selling a product"""
        try:
            with self._session() as session:
                repository = ProductRepository(session)
                dealers = repository.get_product_dealers(product_name)
                
                if not dealers:
                    return {
                        "response": f"🏪 *Dealers - {product_name}*\n\nNo dealers found.\n\n0. Main Menu\n99. Back",
                        "menu_type": "product_menu",
                        "action": "dealers",
                        "data": {"product": product_name},
                        "exit_menu": False
                    }
                
                lines = [f"🏪 *Dealers - {product_name}*", ""]
                for i, dealer in enumerate(dealers[:10], 1):
                    lines.append(f"{i}. {dealer.get('dealer', 'Unknown')}")
                    lines.append(f"   Revenue: PKR {dealer.get('revenue', 0):,.2f}")
                    lines.append(f"   Units: {dealer.get('units', 0):,}")
                    lines.append("")
                
                if len(dealers) > 10:
                    lines.append(f"... and {len(dealers) - 10} more")
                
                lines.extend(["", "0. Main Menu", "99. Back"])
                
                return {
                    "response": "\n".join(lines),
                    "menu_type": "product_menu",
                    "action": "dealers",
                    "data": {"product": product_name, "dealers": dealers},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_warehouses(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get warehouses shipping a product"""
        try:
            with self._session() as session:
                repository = ProductRepository(session)
                warehouses = repository.get_products_by_warehouse(product_name)
                
                if not warehouses:
                    return {
                        "response": f"🏭 *Warehouses - {product_name}*\n\nNo warehouses found.\n\n0. Main Menu\n99. Back",
                        "menu_type": "product_menu",
                        "action": "warehouses",
                        "data": {"product": product_name},
                        "exit_menu": False
                    }
                
                lines = [f"🏭 *Warehouses - {product_name}*", ""]
                for i, wh in enumerate(warehouses[:10], 1):
                    lines.append(f"{i}. {wh.get('product', 'Unknown')}")
                    lines.append(f"   Revenue: PKR {wh.get('revenue', 0):,.2f}")
                    lines.append(f"   Units: {wh.get('units', 0):,}")
                    lines.append("")
                
                if len(warehouses) > 10:
                    lines.append(f"... and {len(warehouses) - 10} more")
                
                lines.extend(["", "0. Main Menu", "99. Back"])
                
                return {
                    "response": "\n".join(lines),
                    "menu_type": "product_menu",
                    "action": "warehouses",
                    "data": {"product": product_name, "warehouses": warehouses},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_cities(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get cities where product is sold"""
        try:
            with self._session() as session:
                repository = ProductRepository(session)
                cities = repository.get_products_by_city(product_name)
                
                if not cities:
                    return {
                        "response": f"🏙️ *Cities - {product_name}*\n\nNo cities found.\n\n0. Main Menu\n99. Back",
                        "menu_type": "product_menu",
                        "action": "cities",
                        "data": {"product": product_name},
                        "exit_menu": False
                    }
                
                lines = [f"🏙️ *Cities - {product_name}*", ""]
                for i, city in enumerate(cities[:10], 1):
                    lines.append(f"{i}. {city.get('product', 'Unknown')}")
                    lines.append(f"   Revenue: PKR {city.get('revenue', 0):,.2f}")
                    lines.append(f"   Units: {city.get('units', 0):,}")
                    lines.append("")
                
                if len(cities) > 10:
                    lines.append(f"... and {len(cities) - 10} more")
                
                lines.extend(["", "0. Main Menu", "99. Back"])
                
                return {
                    "response": "\n".join(lines),
                    "menu_type": "product_menu",
                    "action": "cities",
                    "data": {"product": product_name, "cities": cities},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_pending_dn(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get product pending DN"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "pending_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"⏳ *Pending DN - {product_name}*",
                        "",
                        f"Pending DN: {dashboard.get('pending_dn', 0):,}",
                        f"PGI Pending: {dashboard.get('pgi_pending_dn', 0):,}",
                        f"POD Pending: {dashboard.get('pod_pending_dn', 0):,}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "product_menu",
                    "action": "pending_dn",
                    "data": {"product": product_name, "pending": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_pending_pgi(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get product pending PGI"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "pgi_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": f"⏳ *Pending PGI - {product_name}*\n\nPending PGI: {dashboard.get('pgi_pending_dn', 0):,}\n\n0. Main Menu\n99. Back",
                    "menu_type": "product_menu",
                    "action": "pending_pgi",
                    "data": {"product": product_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_pending_pod(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get product pending POD"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "pod_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": f"⏳ *Pending POD - {product_name}*\n\nPending POD: {dashboard.get('pod_pending_dn', 0):,}\n\n0. Main Menu\n99. Back",
                    "menu_type": "product_menu",
                    "action": "pending_pod",
                    "data": {"product": product_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_trend(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get product trend"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "trend_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                monthly_trend = dashboard.get('monthly_trend', [])
                lines = [f"📈 *Monthly Trend - {product_name}*", ""]
                
                if monthly_trend:
                    for month in monthly_trend:
                        lines.append(f"{month['month']}:")
                        lines.append(f"  Revenue: PKR {month['revenue']:,.2f}")
                        lines.append(f"  Units: {month['units']:,}")
                        lines.append("")
                else:
                    lines.append("No trend data available.")
                
                lines.append(f"Growth Rate: {dashboard.get('growth_rate', 0):+.1f}%")
                lines.append("")
                lines.append("0. Main Menu")
                lines.append("99. Back")
                
                return {
                    "response": "\n".join(lines),
                    "menu_type": "product_menu",
                    "action": "trend",
                    "data": {"product": product_name, "trend": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_executive_summary(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get product executive summary"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "summary_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_executive_summary(product_name, dashboard),
                    "menu_type": "product_menu",
                    "action": "executive_summary",
                    "data": {"product": product_name, "dashboard": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_ai_insights(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get AI-powered product insights"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "insights_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                insights = dashboard.get('insights', [])
                if not insights:
                    insights = ["No insights available at this time."]
                
                return {
                    "response": "\n".join([
                        f"💡 *AI Insights - {product_name}*",
                        "",
                        "📊 *Key Findings*",
                        "",
                        "\n".join(f"• {insight}" for insight in insights[:5]),
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "product_menu",
                    "action": "ai_insights",
                    "data": {"product": product_name, "insights": insights},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_recommendations(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get product recommendations"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "recommendations_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                recommendations = dashboard.get('recommendations', [])
                if not recommendations:
                    recommendations = ["No recommendations available at this time."]
                
                return {
                    "response": "\n".join([
                        f"🎯 *Recommendations - {product_name}*",
                        "",
                        "\n".join(f"• {rec}" for rec in recommendations[:5]),
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "product_menu",
                    "action": "recommendations",
                    "data": {"product": product_name, "recommendations": recommendations},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_life_cycle(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get product life cycle information"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "life_cycle_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                first_sale = dashboard.get('first_sale', 'N/A')
                last_sale = dashboard.get('last_sale', 'N/A')
                
                # Calculate age
                try:
                    first_date = datetime.strptime(first_sale, "%d-%b-%Y") if first_sale != 'N/A' else None
                    if first_date:
                        age_days = (datetime.now() - first_date).days
                        age_months = age_days // 30
                        age_years = age_days // 365
                    else:
                        age_days = 0
                        age_months = 0
                        age_years = 0
                except:
                    age_days = 0
                    age_months = 0
                    age_years = 0
                
                return {
                    "response": "\n".join([
                        f"📋 *Life Cycle - {product_name}*",
                        "",
                        f"First Shipment: {first_sale}",
                        f"Last Shipment: {last_sale}",
                        f"Age: {age_years} years, {age_months % 12} months, {age_days % 30} days",
                        "",
                        f"Total DN: {dashboard.get('dn_count', 0):,}",
                        f"Total Units: {dashboard.get('total_units', 0):,}",
                        f"Total Revenue: PKR {dashboard.get('total_revenue', 0):,.2f}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "product_menu",
                    "action": "life_cycle",
                    "data": {"product": product_name, "life_cycle": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_performance(self, context: ProductContext, product_name: str) -> Dict[str, Any]:
        """Get product performance"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dashboard = builder.build(product_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Product '{product_name}' not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "performance_error",
                        "data": {"product": product_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📈 *Performance - {product_name}*",
                        "",
                        f"Business Score: {dashboard.get('business_score', 0):.1f}/100",
                        f"Performance Grade: {dashboard.get('performance_grade', 'N/A')}",
                        f"Overall Status: {dashboard.get('overall_status', 'Unknown')}",
                        f"Growth Rate: {dashboard.get('growth_rate', 0):+.1f}%",
                        "",
                        f"Delivery Success: {dashboard.get('delivery_success_pct', 0):.1f}%",
                        f"Pending Rate: {dashboard.get('pending_pct', 0):.1f}%",
                        f"Avg Delivery Days: {dashboard.get('avg_delivery_days', 0):.1f}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "product_menu",
                    "action": "performance",
                    "data": {"product": product_name, "performance": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_product_ranking(self, context: ProductContext) -> Dict[str, Any]:
        """Get product rankings"""
        try:
            with self._session() as session:
                repository = ProductRepository(session)
                ranking = repository.get_top_products_by_revenue(10)
                
                if not ranking:
                    return {
                        "response": "🏆 *Product Rankings*\n\nNo products found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "ranking",
                        "data": {},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_ranking(ranking, "Revenue", 10),
                    "menu_type": "product_menu",
                    "action": "ranking",
                    "data": {"ranking": ranking},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Ranking error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _perform_comparison(self, context: ProductContext, product1: str, product2: str) -> Dict[str, Any]:
        """Perform product comparison"""
        try:
            with self._session() as session:
                builder = ProductDashboardBuilder(session)
                dash1 = builder.build(product1)
                dash2 = builder.build(product2)
                
                if not dash1 or not dash2:
                    return {
                        "response": "⚠️ One or both products not found.\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "comparison_error",
                        "data": {"error": "not_found"},
                        "exit_menu": False
                    }
                
                metrics = {}
                
                metrics[f"{product1}_metrics"] = {
                    "Revenue": f"PKR {dash1.get('total_revenue', 0):,.2f}",
                    "Units": f"{dash1.get('total_units', 0):,}",
                    "DN": f"{dash1.get('dn_count', 0):,}",
                    "Dealers": f"{dash1.get('dealer_count', 0):,}",
                    "Cities": f"{dash1.get('city_count', 0):,}",
                    "Pending": f"{dash1.get('pending_dn', 0):,}",
                    "Business Score": f"{dash1.get('business_score', 0):.1f}/100",
                    "Growth": f"{dash1.get('growth_rate', 0):+.1f}%",
                }
                
                metrics[f"{product2}_metrics"] = {
                    "Revenue": f"PKR {dash2.get('total_revenue', 0):,.2f}",
                    "Units": f"{dash2.get('total_units', 0):,}",
                    "DN": f"{dash2.get('dn_count', 0):,}",
                    "Dealers": f"{dash2.get('dealer_count', 0):,}",
                    "Cities": f"{dash2.get('city_count', 0):,}",
                    "Pending": f"{dash2.get('pending_dn', 0):,}",
                    "Business Score": f"{dash2.get('business_score', 0):.1f}/100",
                    "Growth": f"{dash2.get('growth_rate', 0):+.1f}%",
                }
                
                rev1 = dash1.get('total_revenue', 0)
                rev2 = dash2.get('total_revenue', 0)
                
                if rev1 > rev2:
                    explanation = f"{product1} has higher revenue than {product2}"
                elif rev2 > rev1:
                    explanation = f"{product2} has higher revenue than {product1}"
                else:
                    explanation = f"{product1} and {product2} have similar revenue"
                
                metrics["explanation"] = explanation
                
                return {
                    "response": self._menu_renderer.render_comparison_result(product1, product2, metrics),
                    "menu_type": "product_menu",
                    "action": "comparison",
                    "data": {"product1": product1, "product2": product2, "metrics": metrics},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Comparison error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _search_products(self, context: ProductContext, query: str) -> Dict[str, Any]:
        """Search for products"""
        try:
            with self._session() as session:
                repository = ProductRepository(session)
                results = repository.search_products(query)
                
                if not results:
                    return {
                        "response": f"🔍 No results found for '{query}'\n\n0. Main Menu",
                        "menu_type": "product_menu",
                        "action": "search",
                        "data": {"query": query, "results": []},
                        "exit_menu": False
                    }
                
                lines = [f"🔍 *Search Results for '{query}'*", ""]
                for i, product in enumerate(results[:10], 1):
                    lines.append(f"{i}. {product.get('product', 'Unknown')}")
                    lines.append(f"   Material: {product.get('material_no', 'N/A')}")
                    lines.append(f"   Division: {product.get('division', 'N/A')}")
                    lines.append(f"   Revenue: PKR {product.get('revenue', 0):,.2f}")
                    lines.append(f"   Units: {product.get('units', 0):,}")
                    lines.append("")
                
                if len(results) > 10:
                    lines.append(f"... and {len(results) - 10} more")
                
                lines.extend(["", "0. Main Menu", "99. Back"])
                
                return {
                    "response": "\n".join(lines),
                    "menu_type": "product_menu",
                    "action": "search",
                    "data": {"query": query, "results": results},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Search error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "product_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    # ============================================================
    # LEGACY METHODS - BACKWARD COMPATIBILITY
    # ============================================================
    
    def get_product_dashboard(self, product_name: str = "", **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        if not product_name:
            return {
                "success": False,
                "whatsapp_message": "⚠️ Please provide a product name.",
                "error": "PRODUCT_REQUIRED"
            }
        
        context = ProductContext()
        result = self._get_product_dashboard(context, product_name)
        return {
            "success": True,
            "data": result.get("data", {}).get("dashboard", {}),
            "whatsapp_message": result.get("response", ""),
        }
    
    def get_top_products(self, limit: int = 10, **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        context = ProductContext()
        result = self._get_product_ranking(context)
        return {
            "success": True,
            "data": result.get("data", {}).get("ranking", []),
            "whatsapp_message": result.get("response", ""),
        }
    
    def compare_products(self, products: List[str], **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        if not products or len(products) < 2:
            return {
                "success": False,
                "whatsapp_message": "⚠️ Please provide at least two products.",
                "error": "TWO_PRODUCTS_REQUIRED"
            }
        
        context = ProductContext()
        result = self._perform_comparison(context, products[0], products[1])
        return {
            "success": True,
            "data": result.get("data", {}),
            "whatsapp_message": result.get("response", ""),
        }
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for service"""
        try:
            with self._session() as session:
                rows = session.query(func.count(DeliveryReport.id)).scalar() or 0
                products = session.query(func.count(distinct(DeliveryReport.customer_model))).scalar() or 0
                materials = session.query(func.count(distinct(DeliveryReport.material_no))).scalar() or 0
            
            return {
                "healthy": True,
                "service": self._service_name,
                "version": self._version,
                "database": "connected",
                "records": int(rows),
                "products": int(products),
                "materials": int(materials),
                "timestamp": datetime.utcnow().isoformat(),
                "source": "PostgreSQL",
                "menu_enabled": True,
            }
        except Exception as e:
            return {
                "healthy": False,
                "service": self._service_name,
                "version": self._version,
                "database": "disconnected",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }
    
    def process_whatsapp_query(self, message: str, sender: str = "default", **kwargs: Any) -> str:
        """
        Process WhatsApp query and return formatted response.
        ALWAYS returns a string - never a dict.
        """
        if not message or not message.strip():
            return self.get_main_menu()
        
        # Check if it's a menu navigation command
        if message.strip() in ["menu", "help", "options"]:
            return self.get_main_menu()
        
        # Process as menu input
        result = self.process_menu_input(sender, message.strip())
        
        # Extract response string
        response = result.get("response", self.get_main_menu())
        
        # If exit_menu is True, user wants to go back to main menu
        if result.get("exit_menu", False):
            return response
        
        return response

# ============================================================
# BLOCK 14: SERVICE SINGLETON
# ============================================================

_service: Optional[ProductAnalyticsService] = None
_service_lock = threading.Lock()

def get_product_analytics_service() -> ProductAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = ProductAnalyticsService()
    return _service

def process_product_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    """Process product menu input for WhatsApp integration"""
    service = get_product_analytics_service()
    return service.process_menu_input(session_id, user_input)

def get_product_main_menu() -> str:
    """Get the main product menu for WhatsApp"""
    service = get_product_analytics_service()
    return service.get_main_menu()

# ============================================================
# BLOCK 15: EXPORTS
# ============================================================

__all__ = [
    "ProductAnalyticsService",
    "ProductContext",
    "IntentType",
    "MenuState",
    "ResponseFormat",
    "get_product_analytics_service",
    "process_product_menu",
    "get_product_main_menu",
    "ProductMenuRenderer",
    "get_product_dashboard",
    "get_top_products",
    "compare_products",
    "health_check",
]
